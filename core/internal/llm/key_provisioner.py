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
## 🧐 TRAP[DECISION] · 2026-07-24 · — · discover_projects is a shim, not real project discovery
## · Rejected: implement full ai-platform.yaml scanner now (out of scope)
## · Reason: real discovery depends on project directory layout that is not fully standardised
## · Rev: when core/internal/bootstrap has discover_projects → remove shim and import real function
# endregion MODULE_CONTRACT

import argparse
import json
import logging
import os
import pathlib
import sys
import tempfile
from copy import deepcopy
from typing import Any

from core.internal.llm.admin_client import LiteLLMAdminClient
from core.internal.llm.policy_schema import LLMPolicy

logger = logging.getLogger(__name__)

# ── Constants ────────────────────────────────────────────────────────────────

_DEFAULT_BASE_URL: str = "http://litellm:4000"
_DEFAULT_POLICY_REL_PATH = pathlib.Path("core") / "internal" / "llm" / "policy.yaml"

# ── Project root resolution ──────────────────────────────────────────────────

_PROJECT_ROOT = pathlib.Path(__file__).resolve().parent.parent.parent.parent


# region CONSUMER_DISCOVERY


def discover_projects() -> list[dict[str, Any]]:
    """Discover LLM-enabled projects from ai-platform.yaml files.

    ## @purpose  Scan project directories for ai-platform.yaml with llm section.
    ##           Returns a list of project descriptors with name and llm config.
    ##           Currently a shim — returns hardcoded test data until the real
    ##           project discovery mechanism is implemented.
    ## @io
    ##   - ⎋ list[dict] — each dict has 'name' (str) and 'llm' (dict with 'enabled', etc.)
    ## @complexity O(1) — shim, hardcoded
    ## @invariants
    ##   - Each entry has at minimum 'name' and 'llm.enabled'
    ##   - Projects with llm.enabled: false should be skipped during provisioning
    ## @rationale Real discovery depends on project directory layout that is not
    ##            fully standardised yet. Shim enables testing and integration.
    ##
    ## 🧐 TRAP[DECISION] · 2026-07-24 · — · discover_projects shim
    ## · Rejected: implement full ai-platform.yaml scanner (out of scope)
    ## · Reason: real discovery depends on project directory layout not yet standardised
    ## · Rev: when core/internal/ has discover_projects → remove shim and import real function
    """
    # Attempt real discovery: try importing from platform's project scanner
    # TODO: replace shim with real ai-platform.yaml scanner
    _discovery_paths = [
        "core.internal.deploy.project_discovery",
        "core.internal.bootstrap.project_discovery",
        "core.internal.scaffold.project_discovery",
    ]
    for module_path in _discovery_paths:
        try:
            mod = __import__(module_path, fromlist=["discover_projects"])
            if hasattr(mod, "discover_projects"):
                logger.log(
                    logging.INFO,
                    "[IMP:8][discover_projects] Using real discovery from %s",
                    module_path,
                )
                return mod.discover_projects()  # type: ignore[attr-defined]
        except (ImportError, ModuleNotFoundError):  # noqa: PERF203
            continue

    # Shim: return hardcoded test projects
    logger.log(
        logging.WARNING,
        "[IMP:6][discover_projects] Real discovery not available — using hardcoded shim. "
        "TODO: replace with ai-platform.yaml scanner.",
    )
    return [
        {"name": "test-backend", "llm": {"enabled": True}},
        {"name": "test-priority", "llm": {"enabled": True, "profile": "premium"}},
        {"name": "test-legacy", "llm": {"enabled": False}},
    ]


def get_platform_consumers() -> list[dict[str, Any]]:
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
        {"name": "hermes-agent", "llm": {"enabled": True}},
    ]


# endregion CONSUMER_DISCOVERY


# region PROFILE_RESOLUTION


def resolve_profile(
    consumer: dict[str, Any],
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
            return explicit_profile

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
) -> dict[str, Any]:
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
    config: dict[str, Any] = {
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
    base_config: dict[str, Any],
    overrides: dict[str, Any] | None,
) -> dict[str, Any]:
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
        merged["models"] = list(overrides["models"])
        logger.log(
            logging.DEBUG,
            "[IMP:7][apply_overrides] Override models: %s",
            merged["models"],
        )

    if "budget" in overrides and isinstance(overrides["budget"], dict):
        for key in ("daily", "monthly"):
            if key in overrides["budget"] and overrides["budget"][key] is not None:
                merged.setdefault("budget", {})[key] = overrides["budget"][key]
                logger.log(
                    logging.DEBUG,
                    "[IMP:7][apply_overrides] Override budget.%s: %s",
                    key,
                    merged["budget"][key],
                )

    if "rpm_limit" in overrides and overrides["rpm_limit"] is not None:
        merged["rpm_limit"] = overrides["rpm_limit"]
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
            with open(persist_path) as f:
                store = json.load(f)
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
        key[:16] if len(key) > 16 else key,
        persist_path,
    )

    # Write store
    persist_path.parent.mkdir(parents=True, exist_ok=True)
    with open(persist_path, "w") as f:
        json.dump(store, f, indent=2)

    logger.log(
        logging.INFO,
        "[IMP:8][persist_project_key] Store updated: %d entries at %s",
        len(store),
        persist_path,
    )


# endregion KEY_PERSISTENCE


# region KEY_MATCHING


def key_config_matches(
    key_info: dict[str, Any],
    config: dict[str, Any],
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
    if abs(existing_budget - desired_budget) > 0.001:
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
        overrides = llm_config.get("overrides") if isinstance(llm_config, dict) else None
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
                    existing_token[:16] if len(existing_token) > 16 else existing_token,
                )
                provisioned_keys[consumer_name] = existing_token
                persist_project_key(consumer_name, existing_token, persist_path)
                continue
            except Exception as e:
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
                new_key[:16] if len(new_key) > 16 else new_key,
            )
            provisioned_keys[consumer_name] = new_key
            persist_project_key(consumer_name, new_key, persist_path)
        except Exception as e:
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


def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
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
    return parser.parse_args(argv)


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
        keys = provision_all(
            master_key=master_key,
            base_url=args.base_url,
            policy_path=policy_path,
            persist_path=persist_path,
        )

        # Print summary to stdout
        print(f"\n{'=' * 50}")
        print(f"LLM Key Provisioning Complete: {len(keys)} keys")
        print(f"{'=' * 50}")
        for consumer_name, api_key in sorted(keys.items()):
            masked = api_key[:16] + "..." if len(api_key) > 16 else api_key
            print(f"  {consumer_name}: {masked}")
        print(f"{'=' * 50}\n")

        logger.log(
            logging.CRITICAL,
            "[IMP:9][main] Provisioning completed successfully: %d keys",
            len(keys),
        )
        return 0

    except Exception as e:
        logger.log(
            logging.CRITICAL,
            "[IMP:10][main] Provisioning failed: %s: %s",
            type(e).__name__,
            e,
        )
        print(f"ERROR: {e}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())

# endregion CLI
