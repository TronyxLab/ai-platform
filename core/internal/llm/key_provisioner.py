#!/usr/bin/env python3
# GREP_SUMMARY: key_provisioner, idempotent, virtual-keys, LiteLLM, provision_all, CLI, persist, profile-rules
# STRUCTURE: ▶ parse_args() → ◇ provision_all(master_key, base_url, policy_path) →
#            ◇ load policy.yaml → ◇ discover consumers (projects + platform) → ○ for each consumer:
#            ┌─ ◇ resolve profile (explicit → rule → default) → ┌─ ◇ get profile config → ⊕ apply overrides →
#            ├─ ◇ get_key_by_metadata(project=<name>) → ◇ exists? ─┬─ models match? → ⚡ skip
#            │                                                  ├─ models differ? → ⚡ update_key
#            │                                                  └─ not exists? → ⚡ generate_key
#            ├─ ◇ persist_project_key(name, key) → ⊕ keys[name] = key
#            └─ ⚡ return keys dict
#            ⎋ persist — print summary → exit_code
# region MODULE_CONTRACT
## @purpose  Idempotent virtual key provisioner for LiteLLM. Discovers LLM consumers
##           (projects + platform services), resolves profiles, and creates/updates/skips
##           LiteLLM virtual keys. Keys are persisted to a JSON store for later use by
##           env-sync (Wave 6: SOPS integration).
## @scope    DevPlan 049 Phase 4 — Key Provisioner. Called from provision-llm.sh.
##           Idempotency: repeated calls produce identical key sets per project.
## @invariants
##   - Idempotent: same key on repeated calls if config unchanged
##   - Profile resolution order: explicit (project.llm.profile) → rule match → default
##   - Empty overrides are safe (no-op merge)
##   - persist_project_key is a stub — SOPS integration planned for Wave 6
## @rationale Python-first: all business logic in Python, shell is a thin facade.
##            Idempotency prevents duplicate key creation during retries.
## @changes — 2026-07-24 | Created (DevPlan 049 Phase 4)
##           2026-08-01 | DevPlan 117 D24 — discover_projects shim → shared/project_registry.discover_llm_projects
##                      (реальная детекция ai-platform.yaml llm.enabled: true; TRAP[DECISION] снят)
##           2026-08-14 | DevPlan 170 W1-A3 — _DEFAULT_BASE_URL порт из shared/platform_ports
##           2026-08-24 | REF-0007 — persist_project_key: atomic_write_json(mode=0600) от создания
##                      (plain open("w")+chmod-после удалён — нет world-readable окна)
# endregion MODULE_CONTRACT

import argparse
import json
import logging
import os
import pathlib
import sys
import tempfile
from copy import deepcopy
from typing import TypedDict, cast

# ⚠️ TRAP[BUG] · 2026-08-05 · HI · Standalone-инвокация key_provisioner.py без PYTHONPATH → ModuleNotFoundError
# · Symptom: `env -i python3 key_provisioner.py --help` падал на `from core.internal.llm.admin_client...`
# ·   (provision-llm.sh экспортирует PYTHONPATH — но прямой вызов модуля без него ломался;
# ·   латентный класс A, DevPlan 136 W2 T2.10).
# · Root: _PROJECT_ROOT определялся ПОСЛЕ core.* импортов и без sys.path.insert — self-bootstrap отсутствовал.
# · Fix: self-bootstrap корня репо (канон config_renderer.py:44-45) ДО core.* импортов.
# ·   Файл: core/internal/llm/key_provisioner.py → корень = 4 уровня parent.
# · Prevention: core.*-модули не полагаются на внешний PYTHONPATH — self-bootstrap в источнике.
# · DevPlan 136 W2 T2.10: тест env -i python3 key_provisioner.py --help → exit 0.
_PROJECT_ROOT = pathlib.Path(__file__).resolve().parent.parent.parent.parent
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

from core.internal.llm.admin_client import KeyInfo, LiteLLMAdminClient
from core.internal.llm.policy_schema import LLMPolicy

# REF-0007 (11-DevPlan Волна 1): канонический atomic writer — plaintext JSON-хранилище
# LLM-ключей пишется mode=0600 ОТ СОЗДАНИЯ (нет окна world-readable в tmpdir)
from core.internal.shared.atomic_writer import atomic_write_json
from core.internal.shared.exceptions import PlatformError

# DevPlan 170 W1-A3: порт из единого реестра shared/platform_ports (литерал 4000 удалён)
from core.internal.shared.platform_ports import PLATFORM_PORT_LITELLM

logger = logging.getLogger(__name__)

# ── Constants ────────────────────────────────────────────────────────────────

_DEFAULT_BASE_URL: str = f"http://litellm:{PLATFORM_PORT_LITELLM}"
_KEY_PREVIEW_LEN: int = 16  # сколько символов ключа показывать в логах (маскировка)
_BUDGET_EPSILON: float = 0.001  # допустимое расхождение daily-бюджета (float-сравнение)
_DEFAULT_POLICY_REL_PATH = pathlib.Path("core") / "internal" / "llm" / "policy.yaml"

# ── Project root resolution ──────────────────────────────────────────────────
# _PROJECT_ROOT определён выше (self-bootstrap, W2 T2.10) — см. шапку модуля.


# region DATA_Consumer
class Consumer(TypedDict, total=False):
    """Дескриптор LLM-потребителя (проект или platform-сервис) — граница JSON/YAML.

    ## @purpose  Единица обнаружения ключей: name + опциональный llm-конфиг
    ##            (enabled, profile, overrides). Источники: project_registry
    ##            (ai-platform.yaml) и get_platform_consumers().
    """

    name: str
    llm: dict[str, object]


# endregion DATA_Consumer


# region DATA_ProfileConfig
class ProfileConfig(TypedDict):
    """Эффективная конфигурация профиля (базовая + overrides) для /key/generate|update.

    ## @purpose  Единый носитель параметров ключа: models/budget/rpm_limit/metadata —
    ##            строится get_profile_config + apply_overrides, потребляется
    ##            key_config_matches и admin_client.
    """

    models: list[str]
    budget: dict[str, float]
    rpm_limit: int
    metadata: dict[str, str]


# endregion DATA_ProfileConfig


# region CONSUMER_DISCOVERY


def discover_projects() -> list[Consumer]:
    """Discover LLM-enabled projects from ai-platform.yaml files.

    ## @purpose  Scan project directories for ai-platform.yaml with llm section.
    ##           Returns a list of project descriptors with name and llm config.
    ##           Делегирует в shared/project_registry.discover_llm_projects (DevPlan 117 D24) —
    ##           реальная детекция вместо хардкод-шима.
    ## @io
    ##   - ⎋ list[dict] — each dict has 'name' (str) and 'llm' (dict with 'enabled', etc.)
    ## @complexity O(P * Y) где P = проекты в node.yaml, Y = parse ai-platform.yaml
    ## @invariants
    ##   - Каждый entry имеет минимум 'name' и 'llm.enabled'
    ##   - Проекты с llm.enabled: false пропускаются (фильтр в project_registry)
    ## @rationale TRAP[DECISION] 2026-07-24 (shim) снят: реальная детекция через
    ##            shared/project_registry.discover_llm_projects — фильтр по ai-platform.yaml
    ##            llm.enabled: true (DevPlan 117 D24, рев-условие выполнено).
    """
    # DevPlan 117 D24: единая детекция LLM-проектов в shared/project_registry (без хардкода).
    from core.internal.shared.project_registry import discover_llm_projects

    projects = discover_llm_projects()
    logger.log(
        logging.INFO,
        "[IMP:8][discover_projects] Delegated to project_registry.discover_llm_projects — %d LLM-enabled project(s)",
        len(projects),
    )
    # W11: list[dict[str, object]] → list[Consumer] (dict → TypedDict — cast через object)
    return [cast("Consumer", cast(object, p)) for p in projects]


def get_platform_consumers() -> list[Consumer]:
    """Return hardcoded platform service consumers that need LLM keys.

    ## @purpose  Platform services (like hermes-agent) are not projects but
    ##           still need virtual keys. They are defined here as pseudo-projects.
    ## @io  ⎋ list[dict] — each with 'name' (str) — no llm dict, profile resolved via rules
    ## @complexity O(1)
    ## @invariants
    ##   - Platform consumers always have llm.enabled = true
    ##   - Their profile is resolved via auto_provision.profile_rules
    ##   - They have no overrides — only what the rule dictates
    """
    return [
        cast("Consumer", cast(object, {"name": "hermes-agent", "llm": cast("dict[str, object]", {"enabled": True})})),
    ]


# endregion CONSUMER_DISCOVERY


# region PROFILE_RESOLUTION


def resolve_profile(
    consumer: Consumer,
    policy: LLMPolicy,
) -> str:
    """Resolve the profile name for a consumer.

    ## @purpose  Priority order:
    ##   1. Explicit profile from consumer llm.llm.profile
    ##   2. Matching rule from policy.auto_provision.profile_rules
    ##   3. Default from policy.auto_provision.default_profile
    ## @io
    ##   - consumer: dict — consumer descriptor with 'name' and optional 'llm.profile'
    ##   - policy: LLMPolicy — loaded policy with profile_rules
    ##   - ⎋ str — resolved profile name
    ## @complexity O(R) where R = number of profile_rules
    ## @invariants
    ##   - Rule matching: first rule where rule.match matches consumer name wins
    ##   - default_profile is guaranteed to exist (validated by LLMPolicy.from_yaml)
    """
    consumer_name = consumer.get("name", "unknown")

    # 1. Explicit profile
    llm_config = consumer.get("llm", {})
    if isinstance(llm_config, dict):
        explicit_profile = llm_config.get("profile")
        if explicit_profile:
            logger.log(
                logging.INFO,
                "[IMP:8][resolve_profile] Explicit profile for '%s': %s",
                consumer_name,
                explicit_profile,
            )
            return cast("str", explicit_profile)  # W11: YAML-граница (object) → str (profile — строка SoT)

    # 2. Rule match
    for rule in policy.auto_provision.profile_rules:
        match_criteria = rule.match
        if isinstance(match_criteria, dict) and match_criteria.get("name") == consumer_name:
            logger.log(
                logging.INFO,
                "[IMP:8][resolve_profile] Rule match for '%s': profile=%s",
                consumer_name,
                rule.profile,
            )
            return rule.profile

    # 3. Default
    logger.log(
        logging.INFO,
        "[IMP:8][resolve_profile] Default profile for '%s': %s",
        consumer_name,
        policy.auto_provision.default_profile,
    )
    return policy.auto_provision.default_profile


def get_profile_config(
    profile_name: str,
    policy: LLMPolicy,
) -> ProfileConfig:
    """Get the effective configuration from a profile.

    ## @purpose  Extract models, budget, rpm, and metadata from a named profile.
    ## @io
    ##   - profile_name: str — profile name
    ##   - policy: LLMPolicy — loaded policy
    ##   - ⎋ dict — config with keys: models, budget (daily, monthly), rpm_limit, metadata
    ## @complexity O(1)
    """
    profile = policy.profiles[profile_name]
    budget = profile.budget
    config: ProfileConfig = {
        "models": list(profile.models),
        "budget": {
            "daily": budget.daily if budget.daily is not None else 0.0,
        },
        "rpm_limit": profile.rpm_limit,
        "metadata": dict(profile.metadata) if profile.metadata else {},
    }
    if budget.monthly is not None:
        config["budget"]["monthly"] = budget.monthly

    logger.log(
        logging.INFO,
        "[IMP:8][get_profile_config] Profile '%s': models=%s, budget=%s, rpm=%d",
        profile_name,
        config["models"],
        config["budget"],
        config["rpm_limit"],
    )
    return config


def apply_overrides(
    base_config: ProfileConfig,
    overrides: dict[str, object] | None,
) -> ProfileConfig:
    """Apply project-level overrides on top of the base profile config.

    ## @purpose  Deep-merge overrides into profile config. Overrides can include:
    ##           models, budget (daily, monthly), rpm_limit.
    ## @io
    ##   - base_config: dict — profile base config
    ##   - overrides: dict | None — project-specific overrides
    ##   - ⎋ dict — merged config
    ## @complexity O(1) — shallow merge with nested budget override
    ## @invariants
    ##   - overrides.models replaces base_config.models (not append)
    ##   - overrides.budget.daily replaces base_config.budget.daily
    ##   - overrides.rpm_limit replaces base_config.rpm_limit
    ##   - None overrides → no-op (returns deep copy of base)
    """
    if not overrides:
        logger.log(
            logging.DEBUG,
            "[IMP:7][apply_overrides] No overrides — returning base config as-is",
        )
        return deepcopy(base_config)

    merged = deepcopy(base_config)

    if "models" in overrides and overrides["models"] is not None:
        # W11: YAML-граница (object) → list[str] (модели — строки из SoT/overrides)
        merged["models"] = cast("list[str]", overrides["models"])
        logger.log(
            logging.DEBUG,
            "[IMP:7][apply_overrides] Override models: %s",
            merged["models"],
        )

    if "budget" in overrides and isinstance(overrides["budget"], dict):
        budget_ovr = cast("dict[str, object]", overrides["budget"])
        for key in ("daily", "monthly"):
            if key in budget_ovr and budget_ovr[key] is not None:
                merged.setdefault("budget", {})[key] = cast("float", budget_ovr[key])
                logger.log(
                    logging.DEBUG,
                    "[IMP:7][apply_overrides] Override budget.%s: %s",
                    key,
                    merged["budget"][key],
                )

    if "rpm_limit" in overrides and overrides["rpm_limit"] is not None:
        merged["rpm_limit"] = cast("int", overrides["rpm_limit"])
        logger.log(
            logging.DEBUG,
            "[IMP:7][apply_overrides] Override rpm_limit: %s",
            merged["rpm_limit"],
        )

    logger.log(
        logging.INFO,
        "[IMP:8][apply_overrides] Merged config: models=%s, budget=%s, rpm=%d",
        merged["models"],
        merged["budget"],
        merged["rpm_limit"],
    )
    return merged


# endregion PROFILE_RESOLUTION


# region KEY_PERSISTENCE


def get_default_persist_path() -> pathlib.Path:
    """Return the default path for the project keys JSON file.

    ## @purpose  Keys are persisted to PLATFORM_STATE_DIR or temp dir.
    ##           SOPS integration planned for Wave 6.
    ## @complexity O(1)
    """
    return pathlib.Path(os.environ.get("PLATFORM_STATE_DIR", tempfile.gettempdir())) / "litellm-project-keys.json"


def persist_project_key(
    project_name: str,
    key: str,
    persist_path: pathlib.Path | None = None,
) -> None:
    """Persist a generated virtual key to a JSON store.

    ## @purpose  Write key to a JSON file at persist_path. Creates the file
    ##           if it doesn't exist, otherwise merges with existing entries.
    ##           This is a stub — real SOPS integration planned for Wave 6.
    ## @io
    ##   - project_name: str — consumer name
    ##   - key: str — virtual key token
    ##   - persist_path: Path | None — path to JSON store (default: /var/tmp/...)
    ## @complexity O(1) — single file read/write
    ## @invariants
    ##   - File is valid JSON (dict of project_name → key)
    ##   - If file doesn't exist, it is created
    ##   - If project already exists in store, it is overwritten
    ## @rationale Stub: actual SOPS encryption will be added in Wave 6.
    ##            For now, plain JSON is sufficient for testing and integration.
    """
    if persist_path is None:
        persist_path = get_default_persist_path()

    # Load existing store
    store: dict[str, str] = {}
    if persist_path.exists():
        try:
            with pathlib.Path(persist_path).open(encoding="utf-8") as f:
                store = cast("dict[str, str]", json.load(f))  # W11: json → Any → dict[str, str]
        except (json.JSONDecodeError, OSError) as e:
            logger.log(
                logging.WARNING,
                "[IMP:6][persist_project_key] Failed to read existing store, overwriting: %s",
                e,
            )
            store = {}

    # Update store
    store[project_name] = key
    logger.log(
        logging.CRITICAL,
        "[IMP:9][persist_project_key] Key persisted: project=%s, key=%s..., path=%s",
        project_name,
        key[:_KEY_PREVIEW_LEN] if len(key) > _KEY_PREVIEW_LEN else key,
        persist_path,
    )

    # REF-0007 (11-DevPlan Волна 1): канонический atomic_write_json(mode=0600) вместо
    # plain open("w") + chmod-после — temp создаётся 0600 (mkstemp-семантика), chmod до
    # replace: нет окна с world-readable plaintext-ключами в tmpdir; crash → cleanup temp.
    atomic_write_json(persist_path, cast("dict[str, object]", store), mode=0o600)

    logger.log(
        logging.INFO,
        "[IMP:8][persist_project_key] Store updated: %d entries at %s",
        len(store),
        persist_path,
    )


# endregion KEY_PERSISTENCE


# region KEY_MATCHING


def key_config_matches(
    key_info: KeyInfo,
    config: ProfileConfig,
) -> bool:
    """Check if an existing key's config matches the desired config.

    ## @purpose  Compare models, budget, and rpm_limit between existing key
    ##           info and desired config. Used for idempotency: skip if matching.
    ## @io
    ##   - key_info: dict — response from /key/info (LiteLLM key object)
    ##   - config: dict — desired config with models, budget, rpm_limit
    ##   - ⎋ bool — True if key matches desired config (idempotent skip)
    ## @complexity O(M) where M = number of models
    ## @invariants
    ##   - Models comparison is set-based (order-independent)
    ##   - Budget comparison is approximate (floats, within 0.001 tolerance)
    ##   - RPM limit must match exactly
    """
    # Compare models (order-independent)
    existing_models = set(key_info.get("models", []) or [])
    desired_models = set(config.get("models", []) or [])
    if existing_models != desired_models:
        logger.log(
            logging.DEBUG,
            "[IMP:7][key_config_matches] Models differ: existing=%s, desired=%s",
            existing_models,
            desired_models,
        )
        return False

    # Compare budget (approximate float comparison)
    existing_budget = key_info.get("max_budget", 0.0) or 0.0
    desired_budget = config.get("budget", {}).get("daily", 0.0) or 0.0
    if abs(existing_budget - desired_budget) > _BUDGET_EPSILON:
        logger.log(
            logging.DEBUG,
            "[IMP:7][key_config_matches] Budget differs: existing=%.4f, desired=%.4f",
            existing_budget,
            desired_budget,
        )
        return False

    # Compare RPM limit
    existing_rpm = key_info.get("rpm_limit", 0) or 0
    desired_rpm = config.get("rpm_limit", 0) or 0
    if existing_rpm != desired_rpm:
        logger.log(
            logging.DEBUG,
            "[IMP:7][key_config_matches] RPM limit differs: existing=%d, desired=%d",
            existing_rpm,
            desired_rpm,
        )
        return False

    logger.log(
        logging.CRITICAL,
        "[IMP:9][key_config_matches] Config MATCHES — idempotent skip eligible",
    )
    return True


# endregion KEY_MATCHING


# region PROVISION_CORE


def provision_all(
    master_key: str,
    base_url: str = _DEFAULT_BASE_URL,
    policy_path: pathlib.Path | None = None,
    persist_path: pathlib.Path | None = None,
) -> dict[str, str]:
    """Provision virtual keys for all LLM consumers.

    ## @purpose  Main provisioning pipeline:
    ##   1. Load policy from policy.yaml
    ##   2. Create LiteLLMAdminClient
    ##   3. Discover consumers (projects + platform services)
    ##   4. For each consumer: resolve profile, check existing key, create/update/skip
    ##   5. Persist keys to JSON store
    ##   6. Return {consumer_name: api_key}
    ## @io
    ##   - master_key: str — LITELLM_MASTER_KEY for Admin API auth
    ##   - base_url: str — LiteLLM base URL
    ##   - policy_path: Path | None — path to policy.yaml (default: project default)
    ##   - persist_path: Path | None — path to key store JSON
    ##   - ⎋ dict[str, str] — {consumer_name: api_key} for all provisioned projects
    ## @complexity O(C * (R + M)) where C = consumers, R = rules, M = models comparison
    ## @invariants
    ##   - IDEMPOTENT: repeated calls with same config produce identical keys
    ##   - Consumer without 'llm' or with llm.enabled=false → skipped
    ##   - Profile always resolves to a valid, existing profile
    ##   - Every generated key is persisted via persist_project_key()
    """
    logger.log(
        logging.CRITICAL,
        "[IMP:9][provision_all] Starting key provisioning — base_url=%s",
        base_url,
    )

    # Step 1: Resolve policy path
    if policy_path is None:
        policy_path = _PROJECT_ROOT / _DEFAULT_POLICY_REL_PATH
    logger.log(
        logging.INFO,
        "[IMP:7][provision_all] Policy path: %s",
        policy_path,
    )

    # Step 2: Load policy
    policy = LLMPolicy.from_yaml(str(policy_path))
    logger.log(
        logging.CRITICAL,
        "[IMP:9][provision_all] Policy loaded: %d profiles, %d aliases",
        len(policy.profiles),
        len(policy.aliases),
    )

    # Step 3: Create admin client
    client = LiteLLMAdminClient(base_url=base_url, master_key=master_key)

    # Step 4: Discover consumers
    projects = discover_projects()
    platform_consumers = get_platform_consumers()
    all_consumers = projects + platform_consumers
    logger.log(
        logging.CRITICAL,
        "[IMP:9][provision_all] Discovered %d consumers (%d projects + %d platform)",
        len(all_consumers),
        len(projects),
        len(platform_consumers),
    )

    # Step 5: Provision keys
    provisioned_keys: dict[str, str] = {}

    for consumer in all_consumers:
        consumer_name = consumer.get("name", "unknown")

        # Skip disabled
        llm_config = consumer.get("llm", {})
        if not isinstance(llm_config, dict) or not llm_config.get("enabled", False):
            logger.log(
                logging.INFO,
                "[IMP:8][provision_all] SKIP '%s': llm not enabled",
                consumer_name,
            )
            continue

        # Resolve profile
        profile_name = resolve_profile(consumer, policy)
        logger.log(
            logging.INFO,
            "[IMP:8][provision_all] Consumer '%s' → profile '%s'",
            consumer_name,
            profile_name,
        )

        # Get profile config + apply overrides
        base_config = get_profile_config(profile_name, policy)
        llm_overrides = llm_config.get("overrides") if isinstance(llm_config, dict) else None
        # W11: YAML-граница (object) → dict[str, object] (isinstance-сужение + cast)
        overrides: dict[str, object] | None = (
            cast("dict[str, object]", llm_overrides) if isinstance(llm_overrides, dict) else None
        )
        effective_config = apply_overrides(base_config, overrides)

        # Build metadata for the key
        key_metadata: dict[str, str] = {
            "project": consumer_name,
        }
        profile_metadata = effective_config.get("metadata", {})
        if isinstance(profile_metadata, dict):
            key_metadata.update({k: v for k, v in profile_metadata.items() if isinstance(v, str)})

        # Check existing key
        existing_key = client.get_key_by_metadata(project=consumer_name)

        if existing_key and isinstance(existing_key, dict):
            existing_token = existing_key.get("key", "")
            if key_config_matches(existing_key, effective_config):
                # Idempotent: key exists with matching config → skip
                logger.log(
                    logging.CRITICAL,
                    "[IMP:9][provision_all] IDEMPOTENT SKIP '%s': key exists with matching config",
                    consumer_name,
                )
                provisioned_keys[consumer_name] = existing_token
                persist_project_key(consumer_name, existing_token, persist_path)
                continue
            # Key exists but config differs → update
            logger.log(
                logging.INFO,
                "[IMP:8][provision_all] UPDATE '%s': key exists with different config",
                consumer_name,
            )
            try:
                client.update_key(
                    key=existing_token,
                    models=effective_config.get("models"),
                    max_budget=effective_config.get("budget", {}).get("daily"),
                    rpm_limit=effective_config.get("rpm_limit"),
                    metadata=key_metadata,
                )
                logger.log(
                    logging.CRITICAL,
                    "[IMP:9][provision_all] KEY UPDATED '%s': %s...",
                    consumer_name,
                    existing_token[:_KEY_PREVIEW_LEN] if len(existing_token) > _KEY_PREVIEW_LEN else existing_token,
                )
                provisioned_keys[consumer_name] = existing_token
                persist_project_key(consumer_name, existing_token, persist_path)
                continue
            except (OSError, ConnectionError, TimeoutError) as e:
                logger.log(
                    logging.WARNING,
                    "[IMP:8][provision_all] Update failed for '%s': %s — falling through to generate",
                    consumer_name,
                    e,
                )

        # Key does not exist → generate
        logger.log(
            logging.INFO,
            "[IMP:8][provision_all] GENERATE '%s': no existing key found",
            consumer_name,
        )
        try:
            gen_result = client.generate_key(
                models=effective_config.get("models", []),
                metadata=key_metadata,
                max_budget=effective_config.get("budget", {}).get("daily", 0.0),
                budget_duration="1d",
                rpm_limit=effective_config.get("rpm_limit", 10),
            )
            new_key = gen_result.get("key", "")
            logger.log(
                logging.CRITICAL,
                "[IMP:9][provision_all] KEY GENERATED '%s': %s...",
                consumer_name,
                new_key[:_KEY_PREVIEW_LEN] if len(new_key) > _KEY_PREVIEW_LEN else new_key,
            )
            provisioned_keys[consumer_name] = new_key
            persist_project_key(consumer_name, new_key, persist_path)
        except (OSError, ConnectionError, TimeoutError) as e:
            logger.log(
                logging.WARNING,
                "[IMP:8][provision_all] Generate failed for '%s': %s",
                consumer_name,
                e,
            )

    # Summary
    total_skipped = len(all_consumers) - len(provisioned_keys)
    logger.log(
        logging.CRITICAL,
        "[IMP:9][provision_all] Provisioning complete: %d keys provisioned, %d skipped",
        len(provisioned_keys),
        total_skipped,
    )

    return provisioned_keys


# endregion PROVISION_CORE


# region CLI


class CliArgs(argparse.Namespace):
    """Типизированный namespace CLI (W11): ТОЛЬКО аннотации без значений.

    ## @purpose  Значения НЕ задаются class-атрибутами — hasattr(namespace, dest)
    ##            перебивает parser-дефолты; поля заполняет parse_args(namespace=CliArgs()).
    """

    master_key: str | None  # pyright: ignore[reportUninitializedInstanceVariable] — W11 argparse fills (без class-value дефолтов)
    base_url: str  # pyright: ignore[reportUninitializedInstanceVariable] — W11 argparse fills (без class-value дефолтов)
    policy: str | None  # pyright: ignore[reportUninitializedInstanceVariable] — W11 argparse fills (без class-value дефолтов)
    persist: str | None  # pyright: ignore[reportUninitializedInstanceVariable] — W11 argparse fills (без class-value дефолтов)


def _parse_args(argv: list[str] | None = None) -> CliArgs:
    """Parse CLI arguments for key provisioner.

    ## @purpose  Argument parser with env var fallback for master key.
    ## @complexity O(1)
    """
    parser = argparse.ArgumentParser(
        description="Provision LiteLLM virtual keys for all LLM consumers",
    )
    parser.add_argument(
        "--master-key",
        type=str,
        default=None,
        help="LITELLM_MASTER_KEY (default: $LITELLM_MASTER_KEY env var)",
    )
    parser.add_argument(
        "--base-url",
        type=str,
        default=_DEFAULT_BASE_URL,
        help=f"LiteLLM base URL (default: {_DEFAULT_BASE_URL})",
    )
    parser.add_argument(
        "--policy",
        type=str,
        default=None,
        help="Path to policy.yaml (default: core/internal/llm/policy.yaml)",
    )
    parser.add_argument(
        "--persist",
        type=str,
        default=None,
        help="Path to persist keys JSON (default: /var/tmp/litellm-project-keys.json)",
    )
    return parser.parse_args(argv, namespace=CliArgs())


# region FUNC__plw_body_main
## @purpose  Тело try-блока (PLW0717 extraction из main) — семантика except не меняется.
## @io       ⇥ args, master_key, persist_path, policy_path → ⎋ результат try-тела
## @complexity O(1) — извлечение управляющего потока
def _plw_body_main(
    args: CliArgs,
    master_key: str,
    persist_path: pathlib.Path | None,
    policy_path: pathlib.Path,
) -> None:
    keys = provision_all(
        master_key=master_key,
        base_url=args.base_url,
        policy_path=policy_path,
        persist_path=persist_path,
    )
    print(f"\n{'=' * 50}")
    print(f"LLM Key Provisioning Complete: {len(keys)} keys")
    print(f"{'=' * 50}")
    for consumer_name, api_key in sorted(keys.items()):
        masked = api_key[:_KEY_PREVIEW_LEN] + "..." if len(api_key) > _KEY_PREVIEW_LEN else api_key
        print(f"  {consumer_name}: {masked}")
    print(f"{'=' * 50}\n")
    logger.log(
        logging.CRITICAL,
        "[IMP:9][main] Provisioning completed successfully: %d keys",
        len(keys),
    )


# endregion FUNC__plw_body_main


def main(argv: list[str] | None = None) -> int:
    """CLI entry point for key_provisioner.py.

    ## @purpose  Parse args, provision keys, print summary, return exit code.
    ## @io
    ##   - argv: list[str] | None — CLI arguments (default: sys.argv[1:])
    ##   - ⎋ int — exit code: 0 success, 1 on error
    ## @complexity O(provision_all)
    """
    args = _parse_args(argv)

    # Resolve master key: CLI arg → env var
    master_key = args.master_key or os.environ.get("LITELLM_MASTER_KEY", "")
    if not master_key:
        logger.log(
            logging.CRITICAL,
            "[IMP:10][main] LITELLM_MASTER_KEY not provided — use --master-key or set env var",
        )
        print("ERROR: LITELLM_MASTER_KEY is required (--master-key or LITELLM_MASTER_KEY env var)", file=sys.stderr)
        return 1

    # Resolve policy path
    policy_path = pathlib.Path(args.policy) if args.policy else (_PROJECT_ROOT / _DEFAULT_POLICY_REL_PATH)

    # Resolve persist path
    persist_path = pathlib.Path(args.persist) if args.persist else None

    logger.log(
        logging.INFO,
        "[IMP:7][main] Key Provisioner started: base_url=%s, policy=%s",
        args.base_url,
        policy_path,
    )

    try:
        _plw_body_main(args, master_key, persist_path, policy_path)

    except PlatformError as e:
        logger.log(
            logging.CRITICAL,
            "[IMP:10][main] Provisioning failed with exit=%d: %s",
            e.exit_code,
            e,
        )
        return e.exit_code
    # ruff: ignore[BLE001] — top-level CLI handler for unknown exceptions
    except Exception as e:  # noqa: EXC — top-level CLI handler for unknown exceptions
        logger.log(
            logging.CRITICAL,
            "[IMP:10][main] Provisioning failed: %s: %s",
            type(e).__name__,
            e,
        )
        return 1
    else:
        return 0


if __name__ == "__main__":
    sys.exit(main())

# endregion CLI
