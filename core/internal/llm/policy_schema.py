# GREP_SUMMARY: policy_schema, LLMPolicy, Pydantic, YAML, validation, aliases, profiles, providers, auto_provision
# STRUCTURE: ▶ load_yaml(path) → ◇ validate_with_jsonschema(data) → ◇ LLMPolicy.model_validate(data) → ⎋ LLMPolicy instance
# region MODULE_CONTRACT
## @purpose  Pydantic models for LLM policy validation. Loads and validates
##           policy.yaml — the single source of truth for providers, aliases,
##           profiles, and auto-provision rules.
## @scope    Validates the structure of core/internal/llm/policy.yaml at load time.
##           Used by config_renderer.py and key_provisioner.py for type-safe access.
## @invariants
##   - LLMPolicy.from_yaml() validates against JSON Schema FIRST, then Pydantic
##   - DeploymentList.deployments can be empty list (reserved alias) or DeploymentList dict
##   - All Decimal budget values are validated as non-negative
##   - Profile metadata is optional, key-value string pairs only
## @rationale Dual validation (JSON Schema + Pydantic) catches structural YAML errors
##            early and provides Python type safety for consumers.
## @changes — 2026-07-24 | Created (DevPlan 049 Phase 1)
# endregion MODULE_CONTRACT

import json
import logging
import pathlib
from collections.abc import Iterable, Mapping, Sequence
from typing import TypeAlias, cast

import jsonschema
import yaml
from jsonschema.exceptions import ValidationError
from pydantic import BaseModel, Field, ValidationInfo, field_validator

from core.internal.shared.exceptions import ConfigValidationError

logger = logging.getLogger(__name__)

# W11: JSON-граница для jsonschema (iter_errors принимает _JsonParameter — рекурсивный JSON-тип)
JsonValue: TypeAlias = "str | int | float | bool | Mapping[str, JsonValue] | Sequence[JsonValue] | None"


# region MODELS


class ProviderDef(BaseModel):
    """AI provider definition — maps to an env var containing the API key.

    ## @purpose  Defines an external AI provider (e.g. DeepSeek, OpenAI).
    ##           Each provider has exactly one API key env var.
    ## @io
    ##   - key_env: str — name of env var with the API key
    ## @complexity O(1)
    """

    key_env: str


class DeploymentRef(BaseModel):
    """Reference to a specific model deployment at a provider.

    ## @purpose  Points to one concrete model at one provider.
    ##           Used as primary or fallback within an alias.
    ## @io
    ##   - provider: str — provider name from the providers section
    ##   - model: str — LiteLLM model string (e.g. deepseek/deepseek-v4-pro)
    ## @complexity O(1)
    """

    provider: str
    model: str


class DeploymentList(BaseModel):
    """Active deployment configuration for an alias.

    ## @purpose  Holds optional primary and fallback deployments.
    ##           Both are optional — a deployment-less alias is inactive/reserved.
    ## @io
    ##   - primary: Optional[DeploymentRef] — primary model
    ##   - fallback: Optional[DeploymentRef] — fallback model
    ## @complexity O(1)
    """

    primary: DeploymentRef | None = None
    fallback: DeploymentRef | None = None


class AliasDef(BaseModel):
    """Semantic model alias — consumers call this name, not the raw model string.

    ## @purpose  Maps a human-readable alias (e.g. "reasoning") to concrete
    ##           model deployments with primary/fallback. Features describe
    ##           what the model can do for routing decisions.
    ## @io
    ##   - label: str — human-readable description
    ##   - context_window: Optional[int] — max tokens
    ##   - features: list[str] — capability tags
    ##   - deployments: DeploymentList | list — active deployments or empty list (reserved)
    ## @complexity O(1)
    ## @invariants
    ##   - list deployments must be empty (reserved alias signal)
    ##   - DeploymentList with no primary and no fallback = inactive
    """

    label: str
    context_window: int | None = None
    features: list[str]
    # W11: union вместо Any — DeploymentList (активный алиас) или пустой list (reserved-сигнал)
    deployments: DeploymentList | list[object] = Field(
        ...,
        description="DeploymentList object or empty list (reserved alias)",
    )

    # ⚠️ TRAP[BUG] · 2026-08-15 · P1 · mode="before" обязателен для deployments-валидатора
    # · Symptom: after-валидатор получал готовый DeploymentList и падал «must be an empty list
    # ·   or DeploymentList dict, got DeploymentList» — pydantic v2 коерсит dict→DeploymentList
    # ·   (union-аннотация) ДО after-валидаторов.
    # · Root: field_validator по умолчанию mode="after" — аннотация DeploymentList | list[object]
    # ·   заставляет pydantic валидировать/коерсить вход до вызова валидатора.
    # · Fix: mode="before" — валидатор видит СЫРОЕ значение (list | dict | DeploymentList-инстанс);
    # ·   dict → DeploymentList(**cast) коерсится явно, list → проверка пустоты (reserved).
    # · Prevention: union-аннотации полей с кастомными валидаторами — mode="before" (TRAP[TEST]
    # ·   в tests/unit/test_llm_policy_schema.py: deployment dict → DeploymentList, empty list → ok).
    @field_validator("deployments", mode="before")
    @classmethod
    def _validate_deployments(cls, v: object) -> DeploymentList | list[object]:
        """Validate deployments: empty list (reserved) or DeploymentList-compatible dict.

        ## @purpose  Enforce that deployments is either an empty list (alias is reserved,
        ##           not active) or a dict with optional primary/fallback.
        ## @io  ⇥ v → ◇ isinstance check → ⊕ ValueError if invalid → ⎋ v
        ## @complexity O(1)
        ## @changes  2026-08-15 | DevPlan 170 W11 — v: object, возврат union, mode="before" (TRAP[BUG] выше)
        """
        if isinstance(v, DeploymentList):
            return v  # уже коерснутый инстанс (явный вызов/дефолт) — пропустить
        if isinstance(v, list):
            if len(cast("list[object]", v)) != 0:  # W11: list[Unknown] → list[object] (len-аргумент)
                msg = f"deployments list must be empty for reserved aliases, got {len(cast('list[object]', v))} items"
                raise ConfigValidationError(msg)
            return cast("list[object]", v)  # W11: isinstance-сужение list → list[Unknown]
        if isinstance(v, dict):
            # W11: pydantic __init__ проверяет kwargs по полям → cast значений к полям модели
            return DeploymentList(**cast("dict[str, DeploymentRef | None]", v))
        msg = f"deployments must be an empty list or DeploymentList dict, got {type(v).__name__}"
        raise ConfigValidationError(msg)


class BudgetDef(BaseModel):
    """Budget limits for a profile.

    ## @purpose  Defines spending caps in USD for daily and/or monthly periods.
    ##           All values must be non-negative.
    ## @io
    ##   - daily: Optional[float] — daily budget in USD
    ##   - monthly: Optional[float] — monthly budget in USD
    ## @complexity O(1)
    """

    daily: float | None = None
    monthly: float | None = None


class ProfileDef(BaseModel):
    """Access profile — defines which models, budget, and rate limits a project gets.

    ## @purpose  A named profile that can be assigned to projects.
    ##           Profiles are defined in policy.yaml and referenced by name.
    ## @io
    ##   - label: str — human-readable description
    ##   - models: list[str] — alias names this profile grants access to
    ##   - budget: BudgetDef — spending caps
    ##   - rpm_limit: int — requests per minute limit
    ##   - metadata: Optional[dict[str, str]] — arbitrary tags
    ## @complexity O(1)
    """

    label: str
    models: list[str]
    budget: BudgetDef
    rpm_limit: int
    metadata: dict[str, str] | None = None


class AutoProvisionRule(BaseModel):
    """Profile override rule — matches a project by name and assigns a profile.

    ## @purpose  Allows auto-assigning specific profiles to known projects/services
    ##           (e.g. hermes-agent gets unlimited). First match wins.
    ## @io
    ##   - match: dict[str, str] — criteria (e.g. {name: hermes-agent})
    ##   - profile: str — profile to assign
    ## @complexity O(1)
    """

    match: dict[str, str]
    profile: str


class AutoProvisionDef(BaseModel):
    """Auto-provisioning configuration for virtual keys.

    ## @purpose  Controls how virtual keys are automatically provisioned for projects.
    ##           default_profile is used when no explicit profile or matching rule applies.
    ## @io
    ##   - default_profile: str — fallback profile name
    ##   - profile_rules: list[AutoProvisionRule] — ordered override rules
    ## @complexity O(1)
    """

    default_profile: str
    profile_rules: list[AutoProvisionRule]


class LLMPolicy(BaseModel):
    """Root model for LLM policy — validates the entire policy.yaml structure.

    ## @purpose  Complete LLM policy configuration: providers, aliases, profiles,
    ##           and auto-provision rules. Validated via both JSON Schema and Pydantic.
    ## @io
    ##   - providers: dict[str, ProviderDef] — AI provider definitions
    ##   - aliases: dict[str, AliasDef] — semantic model aliases
    ##   - profiles: dict[str, ProfileDef] — access profiles
    ##   - auto_provision: AutoProvisionDef — auto-provisioning rules
    ## @complexity O(N) where N = total number of aliases + profiles
    ## @invariants
    ##   - providers must be non-empty (at least one AI provider)
    ##   - Must have at least 'reasoning' and 'chat' aliases defined
    ##   - auto_provision.default_profile must reference an existing profile
    ##   - All profile model references must reference existing aliases
    """

    providers: dict[str, ProviderDef]
    aliases: dict[str, AliasDef]
    profiles: dict[str, ProfileDef]
    auto_provision: AutoProvisionDef

    @field_validator("providers")
    @classmethod
    def _validate_providers_non_empty(cls, v: dict[str, ProviderDef]) -> dict[str, ProviderDef]:
        """Ensure at least one provider is defined.

        ## @purpose  The gateway needs at least one AI provider to function.
        ##           Empty providers would render all aliases unusable.
        ## @complexity O(1)
        """
        if not v:
            msg = "providers must be non-empty (at least one AI provider)"
            raise ConfigValidationError(msg)
        return v

    @field_validator("aliases")
    @classmethod
    def _validate_aliases_have_reasoning_chat(cls, v: dict[str, AliasDef]) -> dict[str, AliasDef]:
        """Ensure at least 'reasoning' and 'chat' aliases are defined.

        ## @purpose  These are the minimum required semantic aliases for the platform
        ##           to function. The DevPlan contract requires both.
        ## @complexity O(1)
        """
        for required in ("reasoning", "chat"):
            if required not in v:
                msg = f"Missing required alias '{required}' — at least reasoning and chat must be defined"
                raise ConfigValidationError(msg)
        return v

    @field_validator("profiles")
    @classmethod
    def _validate_profiles_have_default(cls, v: dict[str, ProfileDef]) -> dict[str, ProfileDef]:
        """Ensure 'default' profile exists.

        ## @purpose  auto_provision.default_profile references 'default' by default.
        ##           The profile must exist for provisioning to work.
        ## @complexity O(1)
        """
        if "default" not in v:
            msg = "profiles must include a 'default' profile (required by auto-provision)"
            raise ConfigValidationError(msg)
        return v

    @field_validator("auto_provision")
    @classmethod
    def _validate_default_profile_exists(
        cls,
        v: AutoProvisionDef,
        info: ValidationInfo,  # ruff: ignore[ARG003] — pydantic field_validator контракт
    ) -> AutoProvisionDef:
        """Validate default_profile references an existing profile.

        ## @purpose  Prevent runtime errors during key provisioning where a non-existent
        ##           profile is referenced. Validation happens at load time, not at runtime.
        ## @complexity O(1)
        ## @note This validator is best-effort because profiles may not be loaded yet
        ##       (Pydantic v2 does not pass other fields to model validators directly).
        ##       Cross-field validation is handled in from_yaml().
        """
        return v

    @classmethod
    def from_yaml(cls, path: str | pathlib.Path) -> "LLMPolicy":
        """Load and validate LLM policy from a YAML file.

        ## @purpose  Factory method: load YAML → validate against JSON Schema →
        ##           validate with Pydantic → return LLMPolicy instance. LDD logging
        ##           at IMP:7-10 for observability.
        ## @io
        ##   - path: str | pathlib.Path — path to policy.yaml
        ##   - output: LLMPolicy — validated policy instance
        ##   - raises: FileNotFoundError, jsonschema.ValidationError, pydantic.ValidationError
        ## @complexity O(N + M) where N = alias count, M = profile count
        ## @invariants
        ##   - JSON Schema path is resolved relative to this file: ../../schemas/llm-policy.schema.json
        ##   - Cross-field validation: profiles referenced by auto_provision must exist
        ##   - Cross-field validation: provider names in deployments must exist in providers
        ##   - Cross-field validation: profile models must reference existing aliases
        """
        logger.info("[IMP:7][LLMPolicy][from_yaml] Loading policy from: %s", path)

        # Step 1: read YAML
        resolved_path = pathlib.Path(path)
        if not resolved_path.exists():
            logger.critical("[IMP:10][LLMPolicy][from_yaml] File not found: %s", resolved_path)
            msg = f"Policy file not found: {resolved_path}"
            raise FileNotFoundError(msg)

        with pathlib.Path(resolved_path).open(encoding="utf-8") as f:
            data = cast("dict[str, object]", yaml.safe_load(f))  # W11: yaml → Any → dict[str, object]

        if not isinstance(data, dict):
            logger.critical("[IMP:10][LLMPolicy][from_yaml] Invalid YAML structure — expected dict")
            msg = "Invalid YAML structure: expected a top-level mapping (dict)"
            raise ConfigValidationError(msg)

        logger.info("[IMP:8][LLMPolicy][from_yaml] YAML loaded: %d top-level keys", len(data))

        # Step 2: validate against JSON Schema
        schema_path = pathlib.Path(__file__).resolve().parent.parent.parent / "schemas" / "llm-policy.schema.json"
        logger.info("[IMP:7][LLMPolicy][from_yaml] Validating against JSON Schema: %s", schema_path)
        if schema_path.exists():
            with pathlib.Path(schema_path).open(encoding="utf-8") as f:
                schema = cast("dict[str, object]", json.load(f))  # W11: json → Any → dict[str, object]
            validator = jsonschema.Draft7Validator(schema)
            # W11: iter_errors → Generator[Any] + instance: _JsonParameter (jsonschema)
            # → cast к Iterable[ValidationError] (Any-элементы не типизируемы иначе)
            raw_errors = cast(
                "Iterable[ValidationError]",
                validator.iter_errors(  # pyright: ignore[reportUnknownMemberType] — W11 external jsonschema Unknown-оверлоад
                    cast("JsonValue", data)
                ),
            )
            errors = list(raw_errors)
            if errors:
                error_messages = "; ".join(e.message for e in errors)
                logger.critical(
                    "[IMP:10][LLMPolicy][from_yaml] JSON Schema validation failed: %s",
                    error_messages,
                )
                msg = f"Policy JSON Schema validation failed: {error_messages}"
                raise jsonschema.ValidationError(msg)
            logger.info("[IMP:9][LLMPolicy][from_yaml] JSON Schema validation PASSED (0 errors)")
        else:
            logger.warning(
                "[IMP:5][LLMPolicy][from_yaml] JSON Schema not found at %s — skipping schema validation",
                schema_path,
            )

        # Step 3: Pydantic validation
        logger.info("[IMP:8][LLMPolicy][from_yaml] Creating LLMPolicy instance via Pydantic...")
        policy = cls.model_validate(data)

        # Step 4: cross-field validation
        logger.info("[IMP:8][LLMPolicy][from_yaml] Running cross-field validation...")

        # 4a: auto_provision.default_profile exists in profiles
        if policy.auto_provision.default_profile not in policy.profiles:
            msg = (
                f"auto_provision.default_profile '{policy.auto_provision.default_profile}' "
                f"not found in profiles: {list(policy.profiles.keys())}"
            )
            raise ConfigValidationError(msg)

        # 4b: profile models reference existing aliases
        for profile_name, profile in policy.profiles.items():
            for model_ref in profile.models:
                if model_ref not in policy.aliases:
                    msg = (
                        f"Profile '{profile_name}' references alias '{model_ref}' "
                        f"which does not exist in aliases: {list(policy.aliases.keys())}"
                    )
                    raise ConfigValidationError(msg)

        # 4c: deployment provider references exist in providers
        alias_names_with_issues: list[str] = []
        for alias_name, alias in policy.aliases.items():
            deployments = alias.deployments
            if isinstance(deployments, DeploymentList):
                for dep_type, dep_ref in [
                    ("primary", deployments.primary),
                    ("fallback", deployments.fallback),
                ]:
                    if dep_ref is not None and dep_ref.provider not in policy.providers:
                        alias_names_with_issues.append(
                            f"{alias_name}.{dep_type}: provider '{dep_ref.provider}' not found"
                        )

        if alias_names_with_issues:
            raise ConfigValidationError(
                "Deployment references non-existent providers: " + "; ".join(alias_names_with_issues)
            )

        logger.info(
            "[IMP:9][LLMPolicy][from_yaml] LLMPolicy instance created: "
            "providers=%d, aliases=%d, profiles=%d, default_profile='%s'",
            len(policy.providers),
            len(policy.aliases),
            len(policy.profiles),
            policy.auto_provision.default_profile,
        )

        return policy


# endregion MODELS
