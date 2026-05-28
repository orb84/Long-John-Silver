"""
Plan executor for LJS.

Executes structured AgentPlan steps deterministically using
ToolCallExecutor. Respects step dependencies and stops on
failure, returning typed PlanExecutionResult.
"""

from typing import Any, Optional
import json as _json
import re

from loguru import logger

from src.ai.tool_executor import ToolCallExecutor
from src.search.web.url_utils import is_http_url, normalize_search_result_url
from src.core.models import PlanStep, AgentPlan, PlanExecutionStep, PlanExecutionResult


class PlanExecutor:
    """Executes structured plans deterministically.

    Runs AgentPlan steps in order, respecting depends_on for
    ordering constraints. Stops when a required step fails.
    """

    def __init__(
        self,
        tool_executor: ToolCallExecutor,
        allowed_tool_names: set[str],
    ) -> None:
        """Initialize the plan executor.

        Args:
            tool_executor: Executor for individual tool calls.
            allowed_tool_names: Set of tool names allowed for this intent.
        """
        self._tool_executor = tool_executor
        self._allowed_tool_names = allowed_tool_names

    async def execute(self, plan: AgentPlan) -> PlanExecutionResult:
        """Execute all plan steps in order respecting dependencies.

        Args:
            plan: The structured plan with steps to execute.

        Returns:
            PlanExecutionResult with per-step results.
        """
        executed: dict[str, PlanExecutionStep] = {}
        step_results: list[PlanExecutionStep] = []

        for step in plan.steps:
            dep_check = self._check_dependencies(step, executed)
            if dep_check is not None:
                step_results.append(dep_check)
                return PlanExecutionResult(
                    plan=plan, steps=step_results, all_successful=False,
                )

            step_result = await self._execute_single_step(step, executed)
            executed[step.id] = step_result
            step_results.append(step_result)

            if not step_result.success:
                logger.error(
                    f"Plan step '{step.id}' ({step.tool_name}) failed: "
                    f"{step_result.error}"
                )
                return PlanExecutionResult(
                    plan=plan, steps=step_results, all_successful=False,
                )

        return PlanExecutionResult(
            plan=plan, steps=step_results, all_successful=True,
        )

    def _check_dependencies(
        self,
        step: PlanStep,
        executed: dict[str, PlanExecutionStep],
    ) -> Optional[PlanExecutionStep]:
        """Check that all dependencies for a step have been met.

        Args:
            step: The step whose dependencies to check.
            executed: Already-executed steps keyed by ID.

        Returns:
            A failed PlanExecutionStep if a dependency is missing or
            failed, or None if all dependencies pass.
        """
        for dep_id in step.depends_on:
            if dep_id not in executed:
                return PlanExecutionStep(
                    step=step,
                    success=False,
                    error=f"Dependency '{dep_id}' has not been executed",
                )
            if not executed[dep_id].success:
                return PlanExecutionStep(
                    step=step,
                    success=False,
                    error=f"Dependency '{dep_id}' failed",
                )
        return None

    def _resolve_dynamic_arguments(
        self,
        resolved_args: dict[str, Any],
        step: PlanStep,
        executed: dict[str, PlanExecutionStep],
    ) -> str | None:
        """Replace supported planner placeholders using dependency outputs.

        Supported placeholders:
        - ${step_id.path.to.field}
        - {step_id.path.to.field} for older/local models that omit the dollar.
        - ${step_id.latest_season} as an alias for common metadata fields.
        - ``<URL from the first search result of step search_id>`` style
          planner prose, resolved from the previous web_search payload.
        - SELECTED_MAGNET-style legacy placeholders, resolved to the first
          candidate magnet from a dependency search result.

        The executor is the last safety boundary before real tools run.  If a
        value still looks like an unresolved planner placeholder after these
        passes, fail the step with a clear error instead of sending nonsense
        such as ``<URL from ...>`` to network or download tools.
        """
        for key, value in list(resolved_args.items()):
            if not isinstance(value, str):
                continue
            token = value.strip()
            placeholder = re.fullmatch(r"\$?\{([A-Za-z0-9_-]+)\.([^}]+)\}", token)
            if placeholder:
                dep_id, path = placeholder.group(1), placeholder.group(2)
                if dep_id not in executed:
                    return f"Could not resolve placeholder {token}: dependency '{dep_id}' has not run."
                dep_payload = self._dependency_payload(executed[dep_id])
                found = self._extract_placeholder_path(dep_payload, path)
                if found is None:
                    return f"Could not resolve placeholder {token} from dependency '{dep_id}'."
                resolved_args[key] = self._coerce_placeholder_value(found)
                logger.info("Resolved plan placeholder {} for argument '{}' -> {}", token, key, resolved_args[key])
                continue

            url_placeholder = self._resolve_url_placeholder(token, step, executed)
            if url_placeholder is not None:
                if not url_placeholder:
                    return f"Could not resolve URL placeholder {value!r} from dependencies."
                logger.info("Resolved URL placeholder {} for argument '{}' -> {}", token, key, url_placeholder)
                resolved_args[key] = url_placeholder
                continue

            if "SELECTED_MAGNET" in token.upper() or ("MAGNET" in token.upper() and (token.startswith("<") or token.startswith("{"))):
                found_magnet = self._extract_first_candidate_magnet(step, executed)
                if found_magnet:
                    logger.info(f"Resolved placeholder '{value}' for parameter '{key}' to magnet link: {found_magnet[:60]}...")
                    resolved_args[key] = found_magnet
                else:
                    return f"Could not resolve placeholder {value!r} from dependencies."
                continue

            if self._looks_like_unresolved_placeholder(token):
                return f"Unresolved planner placeholder for argument '{key}': {value!r}."

        validation_error = self._validate_resolved_arguments(step, resolved_args)
        if validation_error:
            return validation_error
        return None

    def _resolve_url_placeholder(
        self,
        token: str,
        step: PlanStep,
        executed: dict[str, PlanExecutionStep],
    ) -> str | None:
        """Resolve common natural-language URL placeholders from web_search.

        Local/open models sometimes produce prose placeholders such as
        ``<URL from the first search result of step search_event>`` instead of
        the formal ``${search_event.results.0.url}`` syntax.  Treat those as
        resolvable planner references, not as URLs.
        """
        lower = token.lower()
        if "url" not in lower or "result" not in lower:
            return None
        if not (token.startswith("<") or "from" in lower or "search" in lower):
            return None

        dep_ids: list[str] = []
        match = re.search(r"\bstep\s+([A-Za-z0-9_-]+)", token, re.IGNORECASE)
        if match:
            dep_ids.append(match.group(1))
        dep_ids.extend(dep_id for dep_id in step.depends_on if dep_id not in dep_ids)
        if not dep_ids:
            dep_ids.extend(executed.keys())

        for dep_id in dep_ids:
            dep_step = executed.get(dep_id)
            if not dep_step:
                continue
            found = self._extract_first_url(self._dependency_payload(dep_step))
            if found:
                return found
        return ""

    def _extract_first_candidate_magnet(
        self,
        step: PlanStep,
        executed: dict[str, PlanExecutionStep],
    ) -> str | None:
        """Return the first magnet link from dependency search candidates."""
        for dep_id in step.depends_on:
            dep_step = executed.get(dep_id)
            if not dep_step:
                continue
            payload = self._dependency_payload(dep_step)
            if isinstance(payload, dict):
                candidates = payload.get("candidates")
                if isinstance(candidates, list) and candidates:
                    first = candidates[0]
                    if isinstance(first, dict) and first.get("magnet"):
                        return str(first["magnet"])
        return None

    def _extract_first_url(self, payload: Any) -> str | None:
        """Find the first normalized HTTP(S) URL in common tool-result payload shapes."""
        if isinstance(payload, dict):
            for key in ("url", "href"):
                value = normalize_search_result_url(payload.get(key))
                if value:
                    return value
            for key in ("results", "hits", "links", "items"):
                values = payload.get(key)
                if isinstance(values, list):
                    for item in values:
                        found = self._extract_first_url(item)
                        if found:
                            return found
            for key in ("result", "best", "data"):
                found = self._extract_first_url(payload.get(key))
                if found:
                    return found
        elif isinstance(payload, list):
            for item in payload:
                found = self._extract_first_url(item)
                if found:
                    return found
        return None

    @staticmethod
    def _is_http_url(value: Any) -> bool:
        return is_http_url(value)

    @staticmethod
    def _looks_like_unresolved_placeholder(token: str) -> bool:
        lower = token.lower()
        if re.fullmatch(r"\$?\{[^}]+\}", token):
            return True
        if token.startswith("<") and token.endswith(">"):
            return True
        placeholder_words = ("from the first", "from previous", "from step", "selected_", "replace with")
        return any(word in lower for word in placeholder_words)

    def _validate_resolved_arguments(self, step: PlanStep, resolved_args: dict[str, Any]) -> str | None:
        """Reject unresolved/invalid arguments before a real tool sees them."""
        if step.tool_name in {"read_web_page", "browser_open", "browser_extract"}:
            url = resolved_args.get("url")
            normalized = normalize_search_result_url(url)
            if normalized:
                if normalized != url:
                    logger.info(
                        "Normalized URL argument for tool '{}' from {} -> {}",
                        step.tool_name,
                        url,
                        normalized,
                    )
                    resolved_args["url"] = normalized
                return None
            return (
                f"Tool '{step.tool_name}' requires a resolved http(s) URL, "
                f"but got {url!r}."
            )
        return None

    @staticmethod
    def _dependency_payload(dep_step: PlanExecutionStep) -> Any:
        """Decode a tool result message into a Python payload when possible."""
        result = dep_step.result or {}
        if isinstance(result, dict):
            content = result.get("content", result)
        else:
            content = result
        if isinstance(content, str) and content:
            try:
                return _json.loads(content)
            except Exception:
                return content
        return content

    def _extract_placeholder_path(self, payload: Any, path: str) -> Any:
        """Extract a dotted placeholder path, including download aliases.

        Planners occasionally reference a friendly top-level field such as
        ``${search.candidate_ids}`` even though ``search_media_torrents`` keeps
        queueable batch IDs under ``batch_recommendation.candidate_ids``.
        Resolve those aliases here so deterministic plans do not fail before
        the app has a chance to queue the recommended batch.
        """
        normalized = (path or "").strip()
        if normalized in {"latest_season", "latest_season_number", "current_season"}:
            return self._extract_latest_season(payload)
        if normalized in {
            "seasons", "result.seasons", "results.seasons", "best.seasons",
            "seasons.length", "result.seasons.length", "results.seasons.length",
            "best.seasons.length", "best.number_of_seasons",
        }:
            return self._extract_latest_season(payload)
        # Smaller/local planners often invent container prefixes such as
        # `${lookup.results.latest_season}` even when metadata_lookup exposes a
        # top-level `answer_hints.latest_season` or a list of season objects. Do
        # not add one-off patches for each prefix; any requested "latest/current
        # season" concept resolves through the metadata contract extractor.
        if normalized.endswith((".latest_season", ".latest_season_number", ".current_season")):
            return self._extract_latest_season(payload)
        if normalized.endswith(".seasons.length"):
            return self._extract_latest_season(payload)

        direct = self._extract_dotted_path(payload, normalized)
        if direct is not None:
            return direct

        episode_alias = self._extract_episode_fact_alias(payload, normalized)
        if episode_alias is not None:
            return episode_alias

        if normalized in {"candidate_ids", "results[*].candidate_id", "candidates[*].candidate_id", "results.candidate_ids"}:
            return self._extract_candidate_ids_alias(payload)
        if normalized == "results_total_size_gb":
            return self._extract_total_size_gb_alias(payload)
        if normalized == "queue_download_arguments":
            return self._extract_dotted_path(payload, "batch_recommendation.queue_download_arguments")
        if normalized == "result_set_id":
            return (
                self._extract_dotted_path(payload, "result_set_id")
                or self._extract_dotted_path(payload, "batch_recommendation.result_set_id")
            )
        return None

    @staticmethod
    def _extract_dotted_path(payload: Any, path: str) -> Any:
        """Return a dotted-path value from dict/list payloads."""
        current = payload
        for part in (path or "").split("."):
            if isinstance(current, dict):
                current = current.get(part)
            elif isinstance(current, list) and part == "length":
                current = len(current)
            elif isinstance(current, list) and part.isdigit():
                idx = int(part)
                current = current[idx] if 0 <= idx < len(current) else None
            else:
                return None
            if current is None:
                return None
        return current

    def _extract_episode_fact_alias(self, payload: Any, path: str) -> Any:
        """Resolve common planner aliases for requested episode facts.

        Metadata lookup returns provider results under ``results`` and compact
        answer data under ``answer_hints.requested_episode``.  Smaller/local
        planners sometimes invent paths like ``results.episode.air_date`` even
        though ``results`` is a list.  Treat those as aliases to the requested
        episode rather than surfacing a raw placeholder error to the user.
        """
        normalized = (path or "").strip()
        if ".episode." not in f".{normalized}." and not normalized.startswith(("episode.", "requested_episode.")):
            return None

        alias_prefixes = (
            "results.episode.",
            "result.episode.",
            "best.episode.",
            "episode.",
            "requested_episode.",
        )
        field = None
        for prefix in alias_prefixes:
            if normalized.startswith(prefix):
                field = normalized[len(prefix):]
                break
        if not field:
            return None

        candidate_paths = (
            f"answer_hints.requested_episode.{field}",
            f"requested_episode.{field}",
            f"episode.{field}",
        )
        for candidate in candidate_paths:
            value = self._extract_dotted_path(payload, candidate)
            if value is not None:
                return value
        return None

    def _extract_candidate_ids_alias(self, payload: Any) -> list[str] | None:
        """Resolve planner wildcard candidate-id placeholders safely.

        For multi-unit search results, prefer the category-built batch
        recommendation. For pack searches, a wildcard over ``results`` means the
        top pack candidate, not every candidate in the cache.
        """
        batch_ids = self._extract_dotted_path(payload, "batch_recommendation.candidate_ids")
        if isinstance(batch_ids, list) and batch_ids:
            return [str(value) for value in batch_ids if value]

        candidates = self._extract_dotted_path(payload, "candidates")
        if not isinstance(candidates, list):
            candidates = self._extract_dotted_path(payload, "candidate_picker")
        if not isinstance(candidates, list):
            return None

        scope = str(self._extract_dotted_path(payload, "search_scope") or "").lower()
        if scope in {"bundle_preferred", "bundle_only", "season_pack_preferred", "season_pack_only"}:
            for candidate in candidates:
                if isinstance(candidate, dict) and candidate.get("is_bundle") and (candidate.get("candidate_id") or candidate.get("id")):
                    return [str(candidate.get("candidate_id") or candidate.get("id"))]
        ids = [str((candidate or {}).get("candidate_id") or (candidate or {}).get("id")) for candidate in candidates if isinstance(candidate, dict) and ((candidate or {}).get("candidate_id") or (candidate or {}).get("id"))]
        return ids or None

    def _extract_total_size_gb_alias(self, payload: Any) -> float | None:
        """Resolve common storage-preflight size aliases from search results."""
        direct = self._extract_dotted_path(payload, "results_total_size_gb")
        if direct is None:
            direct = self._extract_dotted_path(payload, "search_summary.results_total_size_gb")
        try:
            if direct is not None:
                return float(direct)
        except (TypeError, ValueError):
            pass
        total_bytes = self._extract_dotted_path(payload, "estimated_total_size_bytes")
        if total_bytes is None:
            total_bytes = self._extract_dotted_path(payload, "search_summary.estimated_total_size_bytes")
        try:
            return round(float(total_bytes or 0) / (1024 ** 3), 3)
        except (TypeError, ValueError):
            return None

    def _extract_latest_season(self, payload: Any) -> int | None:
        """Find the newest known season in common metadata_lookup payload shapes."""
        direct_paths = (
            "latest_season",
            "best.latest_season",
            "best.number_of_seasons",
            "best.seasons",
            "answer_hints.latest_season",
            "answer_hints.seasons",
        )
        for direct in direct_paths:
            # Use the raw dotted extractor here rather than _extract_placeholder_path;
            # several planner aliases (for example best.number_of_seasons)
            # intentionally call back into _extract_latest_season.
            value = self._extract_dotted_path(payload, direct) if "." in direct else (payload.get(direct) if isinstance(payload, dict) else None)
            coerced = self._maybe_int(value)
            if coerced is not None:
                return coerced

        seasons: list[int] = []

        def collect_season_values(value: Any) -> None:
            """Collect explicit season-like integers without treating ids or years as seasons."""
            iv = self._maybe_int(value)
            if iv is not None:
                seasons.append(iv)
            elif isinstance(value, list):
                for child in value:
                    collect_season_values(child)
            elif isinstance(value, dict):
                # TMDB season objects commonly expose either season_number or
                # just number; do not collect unrelated years or ids.
                for candidate_key in ("season_number", "season", "number"):
                    iv2 = self._maybe_int(value.get(candidate_key))
                    if iv2 is not None:
                        seasons.append(iv2)

        def visit(value: Any) -> None:
            """Walk common metadata containers looking only for season-related keys."""
            if isinstance(value, dict):
                for key, child in value.items():
                    if key in {"season", "season_number", "number_of_seasons", "seasons", "season_numbers"}:
                        collect_season_values(child)
                    else:
                        visit(child)
            elif isinstance(value, list):
                for child in value:
                    visit(child)

        visit(payload)
        return max(seasons) if seasons else None

    @staticmethod
    def _maybe_int(value: Any) -> int | None:
        if isinstance(value, bool):
            return None
        if isinstance(value, int):
            return value
        if isinstance(value, float) and value.is_integer():
            return int(value)
        if isinstance(value, str) and value.strip().isdigit():
            return int(value.strip())
        return None

    def _coerce_placeholder_value(self, value: Any) -> Any:
        """Keep numbers numeric so JSON schema validation receives ints."""
        iv = self._maybe_int(value)
        return iv if iv is not None else value

    def _is_soft_metadata_failure(self, tool_name: str, error: str) -> bool:
        """Return True for metadata misses that should allow fallback research.

        A metadata lookup returning no provider/no result is useful context, not
        a terminal plan failure.  The assistant still has web_search/read_web_page
        available for SEARCH/CHAT turns and should be allowed to continue.
        """
        if tool_name != "metadata_lookup":
            return False
        lower = (error or "").lower()
        soft_markers = (
            "no metadata service",
            "no metadata results",
            "not found",
            "unavailable",
            "could not",
        )
        return any(marker in lower for marker in soft_markers)

    async def _execute_single_step(
        self, step: PlanStep, executed: dict[str, PlanExecutionStep],
    ) -> PlanExecutionStep:
        """Execute a single plan step via ToolCallExecutor.

        Args:
            step: The plan step to execute.
            executed: The executed steps dict containing preceding results.

        Returns:
            PlanExecutionStep with result or error.
        """
        # Resolve dynamic arguments from preceding dependencies.  Planners may
        # produce placeholders such as ${lookup_show.latest_season}; never pass
        # these raw strings into typed tools where they become ValueErrors.
        resolved_args = dict(step.arguments)
        placeholder_error = self._resolve_dynamic_arguments(resolved_args, step, executed)
        if placeholder_error:
            return PlanExecutionStep(step=step, success=False, error=placeholder_error)

        logger.info(
            f"Executing plan step '{step.id}' -> Calling tool '{step.tool_name}' "
            f"with arguments: {resolved_args}"
        )
        try:
            result_message, result_summary = (
                await self._tool_executor.execute_tool_call(
                    name=step.tool_name,
                    arguments_raw=resolved_args,
                    tool_call_id=f"plan_{step.id}",
                    allowed_tool_names=self._allowed_tool_names,
                )
            )
            # Check for error payloads embedded in tool results.
            # Tool calls may return {"error": "..."} without raising.
            result_content = result_message.get("content", "")
            if isinstance(result_content, str) and result_content:
                try:
                     parsed = _json.loads(result_content)
                     if isinstance(parsed, dict) and parsed.get("error"):
                         error_msg = parsed["error"]
                         if self._is_soft_metadata_failure(step.tool_name, str(error_msg)):
                             logger.info(
                                 "Plan step '{}' ({}) returned a soft metadata miss: {}. "
                                 "Leaving the result in context so the agent can fall back to web/library tools.",
                                 step.id,
                                 step.tool_name,
                                 error_msg,
                             )
                             return PlanExecutionStep(
                                 step=step,
                                 success=True,
                                 result=result_message,
                                 summary=f"{step.tool_name}: soft miss - {error_msg}",
                             )
                         logger.error(
                             f"Plan step '{step.id}' ({step.tool_name}) "
                             f"returned error: {error_msg}"
                         )
                         return PlanExecutionStep(
                             step=step,
                             success=False,
                             result=result_message,
                             error=str(error_msg),
                         )
                     if isinstance(parsed, dict) and parsed.get("ok") is False:
                         error_msg = parsed.get("error") or f"{step.tool_name} returned ok=false"
                         if self._is_soft_metadata_failure(step.tool_name, str(error_msg)):
                             logger.info(
                                 "Plan step '{}' ({}) returned ok=false as a soft metadata miss: {}. "
                                 "Continuing to the agent loop for fallback research.",
                                 step.id,
                                 step.tool_name,
                                 error_msg,
                             )
                             return PlanExecutionStep(
                                 step=step,
                                 success=True,
                                 result=result_message,
                                 summary=f"{step.tool_name}: soft miss - {error_msg}",
                             )
                         logger.error(
                             f"Plan step '{step.id}' ({step.tool_name}) returned unsuccessful result: {error_msg}"
                         )
                         return PlanExecutionStep(
                             step=step,
                             success=False,
                             result=result_message,
                             error=str(error_msg),
                         )
                     if step.tool_name == "queue_download":
                         verified = (
                             isinstance(parsed, dict)
                             and parsed.get("status") == "queued"
                             and bool(parsed.get("download_id"))
                         )
                         if not verified:
                             error_msg = (
                                 "queue_download did not return a verified "
                                 "queued status with a download_id"
                             )
                             logger.error(
                                 f"Plan step '{step.id}' ({step.tool_name}) "
                                 f"failed verification: {parsed}"
                             )
                             return PlanExecutionStep(
                                 step=step,
                                 success=False,
                                 result=result_message,
                                 error=error_msg,
                             )
                except (_json.JSONDecodeError, TypeError):
                     pass

            return PlanExecutionStep(
                step=step,
                success=True,
                result=result_message,
                summary=result_summary[:300] if len(result_summary) > 300
                else result_summary,
            )
        except Exception as e:
            logger.error(f"Plan step '{step.id}' execution error: {e}")
            return PlanExecutionStep(
                step=step,
                success=False,
                error=str(e),
            )
