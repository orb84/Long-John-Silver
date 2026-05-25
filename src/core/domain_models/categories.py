"""Category contracts, manifests, security settings, and agent context models."""

from __future__ import annotations

from datetime import datetime
from enum import Enum
import re
from typing import Any, Literal, Optional

from pydantic import BaseModel, Field, field_validator, model_serializer, model_validator

from src.core.domain_models.enums import Intent

# --- Category Contract Models ---


class CategoryRouterBrief(BaseModel):
    """Compact category description used before full prompt construction.

    Router briefs must stay short. They are shown to category routing logic
    and sometimes to lightweight LLM calls, so they should identify the
    category without carrying the full category prompt.
    """

    category_id: str
    display_name: str
    description: str
    keywords: list[str] = Field(default_factory=list)
    item_types: list[str] = Field(default_factory=list)


class CategoryPromptExample(BaseModel):
    """Small category-specific few-shot example for LLM guidance."""

    user: str
    expected_intent: str
    expected_behavior: str
    tool_plan: list[str] = Field(default_factory=list)


class CategoryLlmProfile(BaseModel):
    """LLM-oriented description and rules owned by a category.

    The profile helps the assistant understand what a category is, how to
    talk about it, which ambiguities are common, and how to use tools safely.
    It refines global assistant behavior but never overrides global safety,
    confirmation, privacy, or destructive-action policy.
    """

    category_id: str
    short_description: str
    user_facing_description: str
    router_description: str = ""
    domain_vocabulary: list[str] = Field(default_factory=list)
    item_types: list[str] = Field(default_factory=list)
    identifiers: list[str] = Field(default_factory=list)
    common_user_requests: list[str] = Field(default_factory=list)
    ambiguity_rules: list[str] = Field(default_factory=list)
    search_rules: list[str] = Field(default_factory=list)
    download_rules: list[str] = Field(default_factory=list)
    organization_rules: list[str] = Field(default_factory=list)
    safety_rules: list[str] = Field(default_factory=list)
    tool_usage_notes: list[str] = Field(default_factory=list)
    examples: list[CategoryPromptExample] = Field(default_factory=list)

    def router_brief(self, display_name: str) -> CategoryRouterBrief:
        """Build the compact category-router brief for this profile."""
        return CategoryRouterBrief(
            category_id=self.category_id,
            display_name=display_name,
            description=self.router_description or self.short_description,
            keywords=self.domain_vocabulary,
            item_types=self.item_types,
        )

    def format_for_prompt(self, intent: str) -> str:
        """Format this category profile as compact LLM prompt guidance."""
        parts = [
            f"CATEGORY PROFILE: {self.category_id}",
            self.user_facing_description,
            "These category rules refine behavior only; they do not override global safety or tool policy.",
        ]
        if self.domain_vocabulary:
            parts.append("Vocabulary: " + ", ".join(self.domain_vocabulary[:16]))
        if self.item_types:
            parts.append("Item types: " + ", ".join(self.item_types))
        if self.identifiers:
            parts.append("Identifiers: " + ", ".join(self.identifiers))
        if self.ambiguity_rules:
            parts.append("Ambiguity rules:\n" + "\n".join(f"- {rule}" for rule in self.ambiguity_rules))
        if intent.lower() in {"search", "chat"} and self.search_rules:
            parts.append("Search/research rules:\n" + "\n".join(f"- {rule}" for rule in self.search_rules))
        if intent.lower() == "download" and self.download_rules:
            parts.append("Download rules:\n" + "\n".join(f"- {rule}" for rule in self.download_rules))
        if self.tool_usage_notes:
            parts.append("Tool usage notes:\n" + "\n".join(f"- {note}" for note in self.tool_usage_notes))
        if self.examples:
            formatted_examples = []
            for example in self.examples[:3]:
                formatted_examples.append(
                    f"User: {example.user}\nExpected: {example.expected_behavior}\nTools: {', '.join(example.tool_plan)}"
                )
            parts.append("Examples:\n" + "\n\n".join(formatted_examples))
        return "\n\n".join(parts)



class CategoryProperty(BaseModel):
    """A configurable custom parameter owned by a media category."""

    name: str
    value_type: str
    description: str
    default_value: Any
    value: Any = None

    @property
    def current_value(self) -> Any:
        """Return the configured value or the default when unset."""
        return self.value if self.value is not None else self.default_value

    @model_serializer(mode="wrap")
    def _serialize_with_current_value(self, handler) -> dict[str, Any]:
        """Expose current_value for manifest-driven settings UIs."""
        data = handler(self)
        data["current_value"] = self.current_value
        return data


class CategoryUiSection(BaseModel):
    """One UI section rendered for category dashboards or item details."""

    id: str
    title: str
    component: str
    description: str = ""
    capabilities_required: list[str] = Field(default_factory=list)


class CategoryActionDeclaration(BaseModel):
    """Action declaration shared by UI actions and LLM tools."""

    name: str
    label: str
    description: str
    parameters: dict[str, Any] = Field(default_factory=lambda: {"type": "object", "properties": {}, "required": []})
    ui_visible: bool = True
    llm_visible: bool = True
    requires_confirmation: bool = False
    destructive: bool = False
    tool_name: str | None = None
    risk_level: Literal["read", "write", "destructive"] = "read"
    operation: str = ""
    capabilities_required: list[str] = Field(default_factory=list)
    confirmation_prompt: str | None = None
    result_component: str | None = None

    @property
    def exposed_tool_name(self) -> str:
        """Return the tool name exposed to the LLM registry/policy."""
        return self.tool_name or self.name


class CategoryWorkflowDeclaration(BaseModel):
    """Workflow declaration shared by category runtime and LLM tool policy."""

    name: str
    description: str
    parameters: dict[str, Any] = Field(default_factory=lambda: {"type": "object", "properties": {}, "required": []})
    intent: Intent
    risk_level: Literal["read", "write", "destructive"] = "read"
    requires_confirmation: bool = False
    tool_name: str | None = None


class ChangedEntity(BaseModel):
    """Entity touched by an action receipt."""

    entity_type: str
    entity_id: str
    display_name: str = ""
    change: str = ""


class ActionReceipt(BaseModel):
    """User- and LLM-facing receipt from a category or app action."""

    action_id: str = ""
    category_id: str = ""
    action_name: str = ""
    status: Literal["success", "failed", "needs_confirmation", "partial"] = "success"
    user_message: str = ""
    technical_message: str | None = None
    changed_entities: list[ChangedEntity] = Field(default_factory=list)
    next_actions: list[CategoryActionDeclaration] = Field(default_factory=list)
    data: dict[str, Any] = Field(default_factory=dict)






class SafePathDecision(BaseModel):
    """Decision returned by filesystem path security checks."""

    ok: bool
    requested_path: str
    resolved_path: str = ""
    allowed_roots: list[str] = Field(default_factory=list)
    category_id: str | None = None
    purpose: str = "generic"
    reason: str | None = None


class SafeFileOperation(BaseModel):
    """Description of a filesystem mutation after path-policy validation."""

    operation: str
    category_id: str | None = None
    source_path: str | None = None
    target_path: str | None = None
    trash_path: str | None = None
    dry_run: bool = False
    destructive: bool = False
    allowed: bool = False
    reason: str | None = None


class SecurityConfirmationRequest(BaseModel):
    """Two-phase confirmation request for risky or destructive actions."""

    token: str
    action_name: str
    category_id: str | None = None
    risk_level: Literal["read", "write", "destructive"] = "write"
    affected_paths: list[str] = Field(default_factory=list)
    blocked_paths: list[str] = Field(default_factory=list)
    payload_hash: str
    expires_at: datetime
    user_message: str = ""


class SecurityAuditEvent(BaseModel):
    """Append-only audit event for security-relevant operations."""

    event_id: str
    timestamp: datetime = Field(default_factory=datetime.utcnow)
    actor: str = "system"
    source: str = "system"
    action_name: str
    category_id: str | None = None
    item_id: str | None = None
    unit_key: str | None = None
    operation: str = ""
    risk_level: Literal["read", "write", "destructive"] = "read"
    status: str = "pending"
    paths: list[str] = Field(default_factory=list)
    reason: str | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)


class SecurityConfig(BaseModel):
    """Filesystem and command-execution safety settings."""

    enabled: bool = True
    require_confirmation_for_destructive_actions: bool = True
    use_trash_for_deletes: bool = True
    trash_folder_name: str = ".ljs-trash"
    max_files_per_destructive_action: int = 100
    max_gb_per_destructive_action: float = 50.0
    allow_package_install_actions: bool = True
    audit_log_path: str = "./data/security_audit.jsonl"


class StorageConfig(BaseModel):
    """Disk-space monitoring and preflight policy settings."""

    enabled: bool = True
    include_in_llm_context: bool = True
    warning_free_percent: float = 15.0
    critical_free_percent: float = 5.0
    warning_free_gb: float = 50.0
    critical_free_gb: float = 10.0
    minimum_free_after_download_gb: float = 5.0
    context_max_volumes: int = 5
    ui_refresh_seconds: int = 60

class CategorySpecProperty(BaseModel):
    """Declarative property definition for generated categories."""

    name: str
    value_type: str = "string"
    description: str = ""
    default_value: Any = ""
    required: bool = False


class CategorySpecUnit(BaseModel):
    """Declarative unit definition for generated categories."""

    name: str
    unit_type: str = "unit"
    key_template: str = "{index}"
    description: str = ""


class CategorySpec(BaseModel):
    """Validated declarative specification for an LLM-generated category.

    The spec is deliberately narrower than arbitrary Python code. A scaffold
    service can render files from it, tests can validate it, and humans can
    review the preview before installing the category.
    """

    category_id: str
    class_name: str = ""
    display_name: str
    description: str
    default_folder: str = ""
    media_kind: str = "media"
    capabilities: list[str] = Field(default_factory=list)
    metadata_providers: list[str] = Field(default_factory=list)
    discovery_sources: list[dict[str, Any]] = Field(default_factory=list)
    provider_research: list[dict[str, Any]] = Field(default_factory=list)
    download_profile_research: list[dict[str, Any]] = Field(default_factory=list)
    design_notes: str = ""
    download_profile: dict[str, Any] = Field(default_factory=dict)
    item_types: list[str] = Field(default_factory=list)
    identifiers: list[str] = Field(default_factory=lambda: ["title", "library_path"])
    lifecycle_fields: list[str] = Field(default_factory=list)
    suggestion_types: list[str] = Field(default_factory=list)
    invalidation_triggers: list[str] = Field(default_factory=list)
    default_check_interval_days: int = 90
    properties: list[CategorySpecProperty | CategoryProperty] = Field(default_factory=list)
    units: list[CategorySpecUnit] = Field(default_factory=list)
    taste_dimensions: dict[str, float] = Field(default_factory=dict)
    examples: list[CategoryPromptExample] = Field(default_factory=list)

    @field_validator("category_id")
    @classmethod
    def _validate_category_id(cls, value: str) -> str:
        """Validate category IDs before generating filenames or tool names."""
        cleaned = value.strip().lower()
        if not re.match(r"^[a-z][a-z0-9_]*$", cleaned):
            raise ValueError("category_id must be lowercase snake_case and start with a letter")
        return cleaned

    @model_validator(mode="after")
    def _derive_defaults(self) -> "CategorySpec":
        """Derive safe class/folder defaults from the category identifier."""
        if not self.default_folder:
            self.default_folder = self.category_id
        if not self.class_name:
            self.class_name = "".join(part.capitalize() for part in self.category_id.split("_")) + "Category"
        return self

    @field_validator("class_name")
    @classmethod
    def _validate_class_name(cls, value: str) -> str:
        """Validate generated Python class names when explicitly provided."""
        cleaned = value.strip()
        if cleaned and not re.match(r"^[A-Z][A-Za-z0-9]*Category$", cleaned):
            raise ValueError("class_name must be PascalCase and end with Category")
        return cleaned

    @field_validator("capabilities", "metadata_providers", "item_types", "identifiers", "lifecycle_fields", "suggestion_types", "invalidation_triggers")
    @classmethod
    def _dedupe_tokens(cls, values: list[str]) -> list[str]:
        """Normalize repeated token lists while preserving author intent order."""
        seen: set[str] = set()
        result: list[str] = []
        for value in values:
            token = str(value).strip()
            if token and token not in seen:
                seen.add(token)
                result.append(token)
        return result

    @field_validator("default_check_interval_days")
    @classmethod
    def _validate_default_check_interval(cls, value: int) -> int:
        """Keep generated lifecycle cadence in a safe bounded range."""
        return max(1, min(int(value or 90), 365))

    @field_validator("taste_dimensions")
    @classmethod
    def _validate_taste_dimensions(cls, values: dict[str, float]) -> dict[str, float]:
        """Validate category-owned taste dimension weights.

        Weights are cautious multipliers over evidence, not direct preference
        scores. Keep them within a bounded range so generated categories cannot
        dominate the taste profile with one metadata field.
        """
        cleaned: dict[str, float] = {}
        for key, value in (values or {}).items():
            token = str(key).strip()
            if not re.match(r"^[a-z][a-z0-9_]*$", token):
                raise ValueError("taste dimension keys must be lowercase snake_case")
            weight = float(value)
            if weight < 0 or weight > 1:
                raise ValueError("taste dimension weights must be between 0 and 1")
            cleaned[token] = weight
        return cleaned


class CategoryScaffoldPreview(BaseModel):
    """Files rendered from a category spec for human review before installation."""

    category_id: str
    files: dict[str, str] = Field(default_factory=dict)
    warnings: list[str] = Field(default_factory=list)


class CategorySetupRequirement(BaseModel):
    """One setup requirement advertised by a category manifest.

    Requirements explain what the category needs, whether it is mandatory,
    how the current configuration satisfies it, and which setting key or
    action can resolve it. They are intentionally descriptive so the setup
    wizard can educate users without hard-coding TV or movie assumptions.
    """

    id: str
    label: str
    description: str
    required: bool = False
    configured: bool = False
    setting_key: str | None = None
    action: str | None = None
    help_url: str | None = None
    severity: Literal["info", "recommended", "warning", "required"] = "info"
    why_it_matters: str = ""
    secret: bool = False
    validation_action: str | None = None
    docs_url: str | None = None

class CategoryManifest(BaseModel):
    """Complete category contract for backend, UI, and assistant runtime."""

    category_id: str
    display_name: str
    description: str
    default_folder: str = ""
    icon: str | None = None
    media_kind: str = "media"
    capabilities: list[str] = Field(default_factory=list)
    metadata_providers: list[str] = Field(default_factory=list)
    discovery_sources: list[dict[str, Any]] = Field(default_factory=list)
    """Declarative category-owned enrichment/discovery services for setup, UI, and LLM context."""
    properties: list[dict[str, Any]] = Field(default_factory=list)
    ui_sections: list[CategoryUiSection] = Field(default_factory=list)
    actions: list[CategoryActionDeclaration] = Field(default_factory=list)
    workflows: list[CategoryWorkflowDeclaration] = Field(default_factory=list)
    setup_requirements: list[CategorySetupRequirement] = Field(default_factory=list)
    tool_names: list[str] = Field(default_factory=list)
    supported_operations: list[str] = Field(default_factory=list)
    router_brief: CategoryRouterBrief | None = None
    llm_summary: str = ""
    examples: list[str] = Field(default_factory=list)


class CategoryResolution(BaseModel):
    """Result of resolving a user request to a media category."""

    category_id: str | None = None
    confidence: float = 0.0
    ambiguous_categories: list[str] = Field(default_factory=list)
    reason: str = ""


class AgentBudget(BaseModel):
    """Per-run budget for LLM and tool use."""

    max_llm_calls: int = 2
    max_tool_calls: int = 6
    max_searches: int = 3
    max_browser_pages: int = 5


class AgentRunContext(BaseModel):
    """Structured context for one assistant run.

    This context is built before the LLM is called and is used by prompt
    construction, tool policy, action confirmation, and receipt generation.
    """

    user_message: str
    intent: Intent
    category_id: str | None = None
    category_resolution: CategoryResolution | None = None
    target_item_id: str | None = None
    target_item_key: str | None = None
    target_unit: dict[str, Any] = Field(default_factory=dict)
    operation: str | None = None
    action_name: str | None = None
    confirmation_token: str | None = None
    constraints: dict[str, Any] = Field(default_factory=dict)
    budget: AgentBudget = Field(default_factory=AgentBudget)
    allowed_tool_names: list[str] = Field(default_factory=list)
    prompt_sections: list[str] = Field(default_factory=list)
    risk_level: Literal["read", "write", "destructive"] = "read"
    requires_confirmation: bool = False

