"""Endpoint/model context-window probing helpers.

OpenAI-compatible `/models` endpoints are not standardized for context
metadata.  This module centralizes the provider-specific best-effort probing
so runtime budgeting, settings UI, and tests all share one contract.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Iterable

import httpx
from loguru import logger

# The app needs room for tool/context packets.  Unknown endpoints should not
# collapse to 8k unless the endpoint explicitly reports a smaller window.
MIN_USER_CONTEXT_LIMIT = 10_000
FALLBACK_CONTEXT_LIMIT = 16_384
# When an endpoint does not report a real context window, the fallback is only
# a safe default, not a hard maximum.  The UI/runtime may honor an explicit user
# cap up to this manual ceiling while clearly marking it as unverified.
MAX_MANUAL_CONTEXT_LIMIT = 1_048_576


# Provider/model metadata that some OpenAI-compatible endpoints do not expose
# through /v1/models. Keep this narrow and sourced from provider/model docs; it
# is used only when live endpoint probing fails to report a real value.
_KNOWN_PROVIDER_CONTEXT_LIMITS: dict[tuple[str, str], int] = {
    ("nvidia_nim", "openai/gpt-oss-120b"): 128_000,
    ("nvidia_nim", "openai/gpt-oss-20b"): 128_000,
}


def known_provider_context_limit(provider_id: str | None, model_id: str | None) -> int | None:
    """Return a curated provider/model context limit when endpoint metadata is absent.

    NVIDIA NIM's /v1/models endpoint has been observed not to consistently
    expose context metadata.  This small map prevents known high-context models
    from collapsing to the conservative 16k fallback while keeping unknown
    models on the normal probe/fallback path.
    """
    provider = str(provider_id or "").strip().lower()
    model = str(model_id or "").strip().lower()
    if not provider or not model:
        return None
    candidates = [model]
    if "/" not in model:
        candidates.append(f"openai/{model}")
    for candidate in candidates:
        value = _KNOWN_PROVIDER_CONTEXT_LIMITS.get((provider, candidate))
        if value:
            return int(value)
    return None


@dataclass(frozen=True)
class ContextLimitProbeResult:
    """Result of probing a provider/model context limit."""

    provider: str
    model_id: str
    usable_context_tokens: int
    source: str
    loaded_context_tokens: int | None = None
    max_context_tokens: int | None = None
    endpoint_reported: bool = False


def strip_openai_compat_suffix(base_url: str) -> str:
    """Return endpoint root for native provider APIs.

    Examples:
        http://localhost:1234/v1 -> http://localhost:1234
    """
    return (base_url or "").rstrip("/").removesuffix("/v1")


def pick_number(*values: Any) -> int | None:
    """Return the first positive integer-like value."""
    for value in values:
        if value is None or isinstance(value, bool):
            continue
        try:
            number = int(float(value))
        except (TypeError, ValueError):
            continue
        if number > 0:
            return number
    return None


def model_matches(model: dict[str, Any], model_id: str) -> bool:
    """Return whether a provider model record refers to model_id."""
    wanted = str(model_id or "").strip()
    if not wanted:
        return False
    values = [
        model.get("id"), model.get("key"), model.get("name"),
        model.get("display_name"), model.get("model"),
    ]
    for value in values:
        text = str(value or "").strip()
        if text and (text == wanted or text.endswith(f"/{wanted}") or wanted.endswith(f"/{text}")):
            return True
    for instance in _as_list(model.get("loaded_instances")):
        if not isinstance(instance, dict):
            continue
        iid = str(instance.get("id") or instance.get("model") or "").strip()
        if iid and (iid == wanted or iid.endswith(f"/{wanted}") or wanted.endswith(f"/{iid}")):
            return True
    return False


def extract_context_limit(model_data: dict[str, Any]) -> int | None:
    """Extract context-window metadata from common provider response shapes.

    Supports OpenRouter-like, LM Studio-like, Ollama-like, NVIDIA/local-server
    style extensions.  ``max_tokens`` is deliberately last because some APIs use
    it for output tokens rather than the full context window.
    """
    if not isinstance(model_data, dict):
        return None
    direct = pick_number(
        model_data.get("context_length"),
        model_data.get("context_window"),
        model_data.get("max_context_length"),
        model_data.get("max_context_tokens"),
        model_data.get("input_token_limit"),
        model_data.get("max_input_tokens"),
        model_data.get("n_ctx"),
        model_data.get("num_ctx"),
        model_data.get("model_max_length"),
        model_data.get("max_model_len"),
        model_data.get("max_seq_len"),
        model_data.get("max_sequence_length"),
        model_data.get("sequence_length"),
    )
    if direct:
        return direct

    for key in ("architecture", "top_provider", "metadata", "limits", "config", "parameters"):
        nested = model_data.get(key)
        if isinstance(nested, dict):
            nested_limit = extract_context_limit(nested)
            if nested_limit:
                return nested_limit

    # LM Studio native loaded instance runtime context.  Prefer actual loaded
    # config elsewhere when choosing between loaded/max, but include it here for
    # generic parsing too.
    for instance in _as_list(model_data.get("loaded_instances")):
        if not isinstance(instance, dict):
            continue
        config = instance.get("config") if isinstance(instance.get("config"), dict) else instance
        loaded = extract_context_limit(config)
        if loaded:
            return loaded

    # Last provider-supplied fallback.
    return pick_number(model_data.get("max_tokens"))


def extract_loaded_context_limit(model_data: dict[str, Any]) -> int | None:
    """Extract runtime loaded context from LM Studio-style model records."""
    for instance in _as_list(model_data.get("loaded_instances")):
        if not isinstance(instance, dict):
            continue
        config = instance.get("config") if isinstance(instance.get("config"), dict) else instance
        loaded = extract_context_limit(config)
        if loaded:
            return loaded
    return None


async def probe_endpoint_context_limit(
    *,
    base_url: str | None,
    model_id: str,
    api_key: str | None = None,
    provider_id: str | None = None,
    fallback_tokens: int = FALLBACK_CONTEXT_LIMIT,
    timeout: float = 10.0,
) -> ContextLimitProbeResult:
    """Best-effort context-window probe for a provider/model.

    Probe order:
    1. LM Studio native `/api/v1/models` derived from the OpenAI-compatible base.
    2. Generic OpenAI-compatible `/models` with provider-specific metadata keys.
    3. Fallback configured in this module.
    """
    headers = {"Content-Type": "application/json"}
    if api_key:
        headers["Authorization"] = f"Bearer {api_key}"
    base = (base_url or "").rstrip("/")

    # Native LM Studio endpoint.  Safe to try for local/custom providers; 404 or
    # connection failure simply falls through to generic probing.
    if base:
        native_root = strip_openai_compat_suffix(base)
        native_url = f"{native_root}/api/v1/models"
        try:
            result = await _probe_models_url(
                url=native_url,
                headers=headers,
                model_id=model_id,
                provider="lmstudio-native",
                timeout=timeout,
                prefer_loaded=True,
            )
            if result:
                return result
        except Exception as exc:
            logger.debug(f"Context probe skipped native endpoint {native_url}: {exc}")

        compat_url = f"{base}/models" if not base.endswith("/models") else base
        try:
            result = await _probe_models_url(
                url=compat_url,
                headers=headers,
                model_id=model_id,
                provider="openai-compatible",
                timeout=timeout,
                prefer_loaded=False,
            )
            if result:
                return result
        except Exception as exc:
            logger.debug(f"Context probe skipped compatible endpoint {compat_url}: {exc}")

    known_limit = known_provider_context_limit(provider_id, model_id)
    if known_limit:
        logger.info(
            "Context probe using built-in provider metadata: provider={} model={} -> {} tokens",
            provider_id or "unknown", model_id, known_limit,
        )
        return ContextLimitProbeResult(
            provider=provider_id or "known_provider_metadata",
            model_id=model_id,
            usable_context_tokens=max(MIN_USER_CONTEXT_LIMIT, int(known_limit)),
            max_context_tokens=int(known_limit),
            source="built-in provider/model metadata",
            endpoint_reported=True,
        )

    return ContextLimitProbeResult(
        provider=provider_id or "fallback",
        model_id=model_id,
        usable_context_tokens=max(MIN_USER_CONTEXT_LIMIT, int(fallback_tokens or FALLBACK_CONTEXT_LIMIT)),
        source="configured fallback",
        endpoint_reported=False,
    )


async def _probe_models_url(
    *,
    url: str,
    headers: dict[str, str],
    model_id: str,
    provider: str,
    timeout: float,
    prefer_loaded: bool,
) -> ContextLimitProbeResult | None:
    async with httpx.AsyncClient(timeout=timeout) as client:
        response = await client.get(url, headers=headers)
        if not response.is_success:
            return None
        payload = response.json()
    models = list(iter_model_records(payload))
    if not models:
        return None
    model = next((m for m in models if model_matches(m, model_id)), None)
    if model is None:
        return None
    loaded = extract_loaded_context_limit(model)
    maximum = extract_context_limit(model)
    usable = loaded if prefer_loaded and loaded else maximum or loaded
    if not usable:
        return None
    return ContextLimitProbeResult(
        provider=provider,
        model_id=model_id,
        loaded_context_tokens=loaded,
        max_context_tokens=maximum,
        usable_context_tokens=int(usable),
        source=url,
        endpoint_reported=True,
    )


def iter_model_records(payload: Any) -> Iterable[dict[str, Any]]:
    """Yield model records from common list response envelopes."""
    if isinstance(payload, list):
        for item in payload:
            if isinstance(item, dict):
                yield item
        return
    if not isinstance(payload, dict):
        return
    for key in ("data", "models"):
        records = payload.get(key)
        if isinstance(records, list):
            for item in records:
                if isinstance(item, dict):
                    yield item


def _as_list(value: Any) -> list[Any]:
    if isinstance(value, list):
        return value
    return []
