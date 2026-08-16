# GREP_SUMMARY: gate env-example-drift no_proxy-superset postgres-password-unified s3-endpoint-removed env-example-fresh platform-domain-default env-parity env-sync template-subset
# STRUCTURE: ◇ test_env_example_fresh → ◇ test_no_proxy_superset → ◇ test_s3_endpoint_removed → ◇ test_platform_domain_default → ◇ test_no_inline_python3_in_scaffold → ◇ (W2 T2.3 merge) env-parity + prometheus-dirs + sync (well-formed/mirror/order/noproxy) + template-subset

# region MODULE_CONTRACT
## @purpose  Gate test: validate .env.example consistency with SoT (platform-infra.yaml + secret-definitions.yaml).
##           Implements DRIFT-E1, E2, E6, E7 closure verification. S3_ENDPOINT elimination audit.
##           Validation: gen-env-platform.sh deleted (DevPlan 090), gen_env_platform.py canonical.
##           W2 T2.3 (DevPlan 160): консолидация env-семейства — поглотил live-сценарии
##           test_env_contract.py (env_defaults↔.env.example parity + PROMETHEUS_*_DIR canonical),
##           test_gate_env_example_sync.py (well-formed/mirror/order/NO_PROXY-subset) и
##           test_gate_env_example_template.py (template PLATFORM_* ⊆ provides).
##           POSTGRES_PASSWORD-unified и NEXTAUTH_SECRET перенесены в test_gate_env_defaults_consistency.py.
## @scope    Production code (core/, .env.example, .env, templates/). Test files excluded from S3_ENDPOINT audit.
## @invariants
##   - .env.example is byte-identical to sync_env_defaults.py generated output
##   - .env.example NO_PROXY is superset of platform-infra.yaml no_proxy_internal
##   - env_defaults (platform-env.yaml) ↔ .env.example key/value parity (exact count)
##   - PROMETHEUS_TARGETS_DIR/RULES_DIR — canonical /opt/platform/ paths, registered in volumes
##   - .env.example без дублирующихся ключей; .env ↔ .env.example key/order mirror; NO_PROXY ⊆
##   - Template .env.example PLATFORM_* ∈ provides (gen_env_platform contract)
##   - S3_ENDPOINT (without _URL) does NOT exist in production code
##   - PLATFORM_DOMAIN default is ai-platform.local in sync_env_defaults.py (env .example SoT)
##   - gen-env-platform.sh deleted — gen_env_platform.py is the canonical source
## @rationale Gate-enforced drift prevention: catches any manual edits to .env.example or
##            regressions in S3_ENDPOINT removal. gen-env-platform.sh deleted per DevPlan 090.
## @changes  2026-07-26 | Created per DevPlan 082 TASK-8
##           2026-08-12 | DevPlan 160 W2 T2.3 — MERGE env-семейства (env_contract/sync/template)
# endregion MODULE_CONTRACT

import logging
import os
import pathlib
import re
import subprocess
import sys

import pytest
import yaml

from tests.conftest import ldd_trajectory
from tests.helpers.gate_helpers import repo_root

logger = logging.getLogger(__name__)

ROOT = repo_root()
ENV_EXAMPLE = ROOT / ".env.example"
PLATFORM_ENV = ROOT / "platform-env.yaml"
SECRET_DEFS = ROOT / "core" / "secret-definitions.yaml"
PLATFORM_INFRA = ROOT / "core" / "platform-infra.yaml"
GEN_ENV_PLATFORM_SH = ROOT / "core" / "internal" / "scaffold" / "gen-env-platform.sh"
SYNC_SCRIPT = ROOT / "core" / "internal" / "scripts" / "sync_env_defaults.py"
ENV_DEFAULTS_GENERATED = ROOT / "tests" / "helpers" / "env_defaults_generated.py"
HERMES_AGENT_ENV_EXAMPLE = ROOT / "core" / "modules" / "hermes-agent" / ".env.example"

# ── W2 T2.3: sync-домен (перенесено из test_gate_env_example_sync.py) ──────────────
VAR_RE = re.compile(r"^(PLATFORM_[A-Z_]+)=")


def _parse_keys_in_order(env_path: os.PathLike) -> list[str]:
    """Parse key names in order of appearance, ignoring comments and blanks."""
    keys: list[str] = []
    with pathlib.Path(env_path).open(encoding="utf-8") as f:
        for line_raw in f:
            line = line_raw.strip()
            if not line or line.startswith("#"):
                continue
            if "=" in line:
                key = line.split("=", 1)[0].strip()
                keys.append(key)
    return keys


def _parse_env_value(env_path: os.PathLike, key: str) -> str | None:
    """Extract value of a key from a .env-style file. Returns None if not found."""
    if not pathlib.Path(env_path).is_file():
        return None
    with pathlib.Path(env_path).open(encoding="utf-8") as f:
        for line_raw in f:
            line = line_raw.strip()
            if not line or line.startswith("#"):
                continue
            if "=" in line:
                k, v = line.split("=", 1)
                if k.strip() == key:
                    return v.strip()
    return None


# ── W2 T2.3: template-домен (перенесено из test_gate_env_example_template.py) ──────
def _provided_vars(env_data: dict) -> set[str]:
    """Ожидаемое множество PLATFORM_* имён по контракту gen_env_platform (provides + networks + core)."""
    names: set[str] = set()
    for svc, svc_data in (env_data.get("provides") or {}).items():
        svc_upper = str(svc).upper()
        if svc_data.get("host"):
            names.add(f"PLATFORM_{svc_upper}_HOST")
        if svc_data.get("port"):
            names.add(f"PLATFORM_{svc_upper}_PORT")
        if svc_data.get("dsn_template"):
            names.add(f"PLATFORM_{svc_upper}_DSN")
        if svc_data.get("url_template"):
            names.add(f"PLATFORM_{svc_upper}_URL")
    for net in env_data.get("networks") or []:
        net_name = net.get("name") if isinstance(net, dict) else net
        if net_name:
            names.add(f"PLATFORM_{str(net_name).upper().replace('-', '_')}")
    names.update({"PLATFORM_DOMAIN", "PLATFORM_PROVIDES", "PLATFORM_NO_PROXY"})
    return names


@pytest.mark.gate
@ldd_trajectory
def test_env_example_fresh(caplog):
    """.env.example is byte-identical to sync_env_defaults.py --check output."""
    result = subprocess.run(
        [
            sys.executable,
            str(SYNC_SCRIPT),
            "--platform-env",
            str(PLATFORM_ENV),
            "--secret-defs",
            str(SECRET_DEFS),
            "--output",
            str(ENV_EXAMPLE),
            "--check",
        ],
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
    )
    if result.returncode == 2:
        logger.error("[IMP:10][gate] .env.example diverges from SoT:\n%s", result.stderr[:2000])
        pytest.fail(".env.example is stale — run: make generate-env-example")
    elif result.returncode != 0:
        logger.error("[IMP:10][gate] sync_env_defaults.py failed: %s", result.stderr[:1000])
        pytest.fail(f"sync_env_defaults.py exited {result.returncode}")
    logger.info("[IMP:9][gate] PASS: .env.example is fresh (byte-identical to generated output)")


@pytest.mark.gate
@ldd_trajectory
def test_no_proxy_superset(caplog):
    """.env.example NO_PROXY must be a superset of platform-infra.yaml no_proxy_internal."""
    with pathlib.Path(PLATFORM_INFRA).open(encoding="utf-8") as f:
        infra = yaml.safe_load(f)
    no_proxy_internal = infra.get("proxy", {}).get("no_proxy_internal", "")
    so_t_entries = {e.strip() for e in no_proxy_internal.split(",") if e.strip()}

    env_noproxy = ""
    with pathlib.Path(ENV_EXAMPLE).open(encoding="utf-8") as f:
        for line in f:
            if line.startswith("NO_PROXY="):
                env_noproxy = line.split("=", 1)[1].strip().strip('"')
                break
    env_entries = {e.strip() for e in env_noproxy.split(",") if e.strip()}

    missing = so_t_entries - env_entries
    if missing:
        logger.error("[IMP:10][gate] .env.example NO_PROXY missing entries: %s", sorted(missing))
        logger.error("[IMP:10][gate] SoT (platform-infra): %s", sorted(so_t_entries))
        logger.error("[IMP:10][gate] .env.example: %s", sorted(env_entries))
        pytest.fail(f".env.example NO_PROXY missing SoT entries: {sorted(missing)}")

    logger.info(
        "[IMP:9][gate] PASS: .env.example NO_PROXY superset (SoT=%d, env=%d)", len(so_t_entries), len(env_entries)
    )


def _scan_text_for_s3_endpoint(search_path: object, content: str, patterns_to_check: list[str]) -> list[str]:
    """Найти S3_ENDPOINT (не _URL) в тексте файла (PLR1702-хелпер).

    ## @io — ⇥ search_path, content, patterns → ⎋ list[str] violations
    ## @complexity — O(N*P) где N = длина текста, P = паттерны
    """
    violations: list[str] = []
    for pat in patterns_to_check:
        matches = re.finditer(pat, content)
        for m in matches:
            ctx_start = max(0, m.start() - 10)
            ctx_end = min(len(content), m.end() + 30)
            violations.append(f"{search_path}: ...{content[ctx_start:ctx_end]}...")
    return violations


@pytest.mark.gate
@ldd_trajectory
def test_s3_endpoint_removed(caplog):
    """S3_ENDPOINT (without _URL) must NOT exist in production code."""
    # Search production code: Python, shell, compose, .env files
    search_dirs = [
        ROOT / "core",
        ROOT / ".env",
        ROOT / ".env.example",
    ]

    # Build grep command — search for S3_ENDPOINT but NOT S3_ENDPOINT_URL
    violations: list[str] = []
    patterns_to_check = [
        r"S3_ENDPOINT[^_]",  # catches S3_ENDPOINT=, S3_ENDPOINT}, S3_ENDPOINT", etc.
    ]

    for search_path in search_dirs:
        if not search_path.exists():
            continue
        if search_path.is_file():
            violations.extend(_scan_text_for_s3_endpoint(search_path, search_path.read_text(), patterns_to_check))
            continue
        # Directory — search files
        for ext in ("*.py", "*.yml", "*.yaml", "*.sh", "*.env"):
            for fpath in search_path.rglob(ext):
                # Skip test files
                if "tests/" in str(fpath) or "/test_" in str(fpath):
                    continue
                # Skip __pycache__
                if "__pycache__" in str(fpath):
                    continue
                try:
                    content = fpath.read_text()
                    violations.extend(_scan_text_for_s3_endpoint(fpath, content, patterns_to_check))
                except OSError:
                    logger.debug("[IMP:7][env-drift] Skipping unreadable file: %s", fpath)

    if violations:
        logger.error("[IMP:10][gate] S3_ENDPOINT (without _URL) found in production code:")
        for v in violations:
            logger.error("  %s", v)
        pytest.fail(
            f"S3_ENDPOINT found in {len(violations)} location(s) — remove S3_ENDPOINT alias, keep only S3_ENDPOINT_URL"
        )

    logger.info("[IMP:9][gate] PASS: S3_ENDPOINT removed from production code (zero references)")


@pytest.mark.gate
@ldd_trajectory
def test_platform_domain_default(caplog):
    """PLATFORM_DOMAIN SoT = platform-infra.yaml env_defaults (DevPlan 116 T3, U-16/D4)."""
    # SoT: platform-infra.yaml env_defaults.PLATFORM_DOMAIN = ai-platform.local (одно определение)
    with pathlib.Path(PLATFORM_INFRA).open(encoding="utf-8") as f:
        infra = yaml.safe_load(f)
    pd_sot = (infra.get("env_defaults") or {}).get("PLATFORM_DOMAIN")
    assert pd_sot == "ai-platform.local", (
        f"platform-infra.yaml env_defaults.PLATFORM_DOMAIN must be ai-platform.local, got {pd_sot!r}"
    )

    # Генератор НЕ содержит hardcoded fallback-значения (fail-fast через get_val_required)
    with pathlib.Path(SYNC_SCRIPT).open(encoding="utf-8") as f:
        content = f.read()
    assert 'get_val("PLATFORM_DOMAIN", "ai-platform.local")' not in content, (
        "sync_env_defaults.py must NOT contain hardcoded PLATFORM_DOMAIN fallback — "
        "use get_val_required (DevPlan 116 T3, invariant 7)"
    )
    assert "get_val_required" in content, "sync_env_defaults.py must use get_val_required for SoT keys"

    # Also verify it's NOT in env_defaults_generated.py (production-only key)
    with pathlib.Path(ENV_DEFAULTS_GENERATED).open(encoding="utf-8") as f:
        gen = f.read()
    assert "PLATFORM_DOMAIN" not in gen, (
        "PLATFORM_DOMAIN must NOT be in env_defaults_generated.py — "
        "it's a production-only key set during deployment, not a test helper default"
    )
    logger.info("[IMP:9][gate] PASS: PLATFORM_DOMAIN SoT = platform-infra.yaml env_defaults (ai-platform.local)")


@pytest.mark.gate
@ldd_trajectory
def test_no_inline_python3_in_scaffold(caplog):
    """gen-env-platform.sh deleted (DevPlan 090) — gen_env_platform.py is the canonical source."""
    # Verify gen-env-platform.sh is gone
    assert not GEN_ENV_PLATFORM_SH.exists(), (
        "gen-env-platform.sh must be deleted — business logic migrated to gen_env_platform.py"
    )

    # Verify gen_env_platform.py exists and is a proper Python module
    gen_env_py = GEN_ENV_PLATFORM_SH.with_suffix(".py").with_name("gen_env_platform.py")
    assert gen_env_py.is_file(), f"gen_env_platform.py not found at {gen_env_py}"

    content = gen_env_py.read_text()
    # Must be native Python (no inline shell)
    assert "python3" not in content or "#!/usr/bin/env python3" in content, (
        "gen_env_platform.py must be Python, not shell with inline python3"
    )

    logger.info(
        "[IMP:9][gate] PASS: gen-env-platform.sh removed, gen_env_platform.py exists (%d lines, %d bytes)",
        len(content.splitlines()),
        len(content),
    )


# ══════════════════════════════════════════════════════════════════════════════
# W2 T2.3 — MERGED from test_env_contract.py (env_defaults ↔ .env.example parity)
# ══════════════════════════════════════════════════════════════════════════════

# Count-константа УДАЛЕНА (v1.0.1, research-E6 A2): хрупкий count-assert «Expected N, got M»
# гонял ручную синхронизацию числа при КАЖДОМ легитимном добавлении env_defaults
# (прецеденты: 12→13, 86, 89, 90, 94, 95→96 TELEGRAM_CHAT_ID). Заменён двусторонней
# key-parity: missing_keys (env_defaults→example) + extra_keys (example→env_defaults) —
# строже, без ручной константы.

# Canonical Prometheus directory paths
PROMETHEUS_TARGETS_DIR_CANONICAL: str = "/opt/platform/prometheus-targets"
PROMETHEUS_RULES_DIR_CANONICAL: str = "/opt/platform/prometheus-rules"


@pytest.mark.gate
@ldd_trajectory
def test_env_example_matches_platform_env_defaults(caplog):
    """MERGED (W2 T2.3): env_defaults (platform-env.yaml) ↔ .env.example key/value parity + count."""
    import dotenv

    assert pathlib.Path(PLATFORM_ENV).is_file(), f"[IMP:9] platform-env.yaml not found at {PLATFORM_ENV}"
    assert pathlib.Path(ENV_EXAMPLE).is_file(), f"[IMP:9] .env.example not found at {ENV_EXAMPLE}"

    with pathlib.Path(PLATFORM_ENV).open(encoding="utf-8") as f:
        data = yaml.safe_load(f)
    assert data is not None, "[IMP:9] platform-env.yaml is empty or invalid"
    assert "env_defaults" in data, "[IMP:9] platform-env.yaml missing env_defaults section"

    env_defaults: dict = data["env_defaults"]
    env_example = dotenv.dotenv_values(ENV_EXAMPLE)

    # v1.0.1 (research-E6 A2): count-assert заменён двусторонней key-parity —
    # missing_keys (SoT→example) + extra_keys (example→SoT, старые/мёртвые ключи).
    extra_keys = [k for k in env_example if k not in env_defaults]

    mismatches: list[str] = []
    missing_keys: list[str] = []
    for key, expected_value in env_defaults.items():
        if key not in env_example:
            missing_keys.append(key)
            continue
        actual_value = env_example[key]
        actual_value_str = actual_value if actual_value is not None else ""
        # AWS-алиасы (${S3_ACCESS_KEY} литералы, DevPlan 116 T3 U-17): сравниваем с РЕЗОЛВНУТЫМ значением референса
        alias_m = re.fullmatch(r"\$\{(\w+)\}", str(expected_value))
        resolved_value = expected_value
        if alias_m and alias_m.group(1) in env_defaults:
            resolved_value = env_defaults[alias_m.group(1)]
        if actual_value_str != str(resolved_value):
            mismatches.append(f"{key}: .env.example='{actual_value_str}' ≠ env_defaults='{resolved_value}'")

    error_parts = []
    if missing_keys:
        error_parts.append(f"Missing in .env.example ({len(missing_keys)}): {', '.join(missing_keys)}")
    if extra_keys:
        error_parts.append(
            f"Extra in .env.example, not in env_defaults SoT ({len(extra_keys)}): {', '.join(extra_keys)}"
        )
    if mismatches:
        error_parts.append(f"Value mismatches ({len(mismatches)}):\n" + "\n".join(mismatches))
    assert not error_parts, "Parity check failed:\n" + "\n".join(error_parts)
    logger.info(
        "[IMP:9][gate] PASS: env_defaults↔.env.example двусторонняя key/value-parity (%d ключей)",
        len(env_defaults),
    )


@pytest.mark.gate
@ldd_trajectory
def test_prometheus_dirs_canonical(caplog):
    """MERGED (W2 T2.3): PROMETHEUS_*_DIR — canonical /opt/platform/ paths, registered in volumes."""
    import dotenv

    with pathlib.Path(PLATFORM_ENV).open(encoding="utf-8") as f:
        data = yaml.safe_load(f)
    env_example = dotenv.dotenv_values(ENV_EXAMPLE)

    targets_dir = env_example.get("PROMETHEUS_TARGETS_DIR")
    assert targets_dir is not None, "[IMP:9] PROMETHEUS_TARGETS_DIR missing from .env.example"
    assert targets_dir == PROMETHEUS_TARGETS_DIR_CANONICAL, (
        f"[IMP:9] PROMETHEUS_TARGETS_DIR='{targets_dir}' ≠ canonical '{PROMETHEUS_TARGETS_DIR_CANONICAL}'"
    )

    rules_dir = env_example.get("PROMETHEUS_RULES_DIR")
    assert rules_dir is not None, "[IMP:9] PROMETHEUS_RULES_DIR missing from .env.example"
    assert rules_dir == PROMETHEUS_RULES_DIR_CANONICAL, (
        f"[IMP:9] PROMETHEUS_RULES_DIR='{rules_dir}' ≠ canonical '{PROMETHEUS_RULES_DIR_CANONICAL}'"
    )

    volumes = data.get("volumes", [])
    volume_paths = [v["path"] for v in volumes if isinstance(v, dict) and "path" in v]
    assert PROMETHEUS_TARGETS_DIR_CANONICAL in volume_paths, (
        f"[IMP:9] Volume '{PROMETHEUS_TARGETS_DIR_CANONICAL}' not registered in platform-env.yaml volumes"
    )
    assert PROMETHEUS_RULES_DIR_CANONICAL in volume_paths, (
        f"[IMP:9] Volume '{PROMETHEUS_RULES_DIR_CANONICAL}' not registered in platform-env.yaml volumes"
    )
    logger.info(
        "[IMP:9][gate] PASS: PROMETHEUS_*_DIR canonical (%s, %s) и зарегистрированы в volumes",
        PROMETHEUS_TARGETS_DIR_CANONICAL,
        PROMETHEUS_RULES_DIR_CANONICAL,
    )


# ══════════════════════════════════════════════════════════════════════════════
# W2 T2.3 — MERGED from test_gate_env_example_sync.py (.env ↔ .env.example mirror)
# ══════════════════════════════════════════════════════════════════════════════


@pytest.mark.gate
@ldd_trajectory
def test_env_example_well_formed(caplog):
    """.env.example must have no duplicate keys (merged W2 T2.3)."""
    assert pathlib.Path(ENV_EXAMPLE).is_file(), f".env.example not found at {ENV_EXAMPLE}"

    keys = _parse_keys_in_order(ENV_EXAMPLE)
    key_set = set(keys)

    violations = []
    if len(keys) != len(key_set):
        seen = {}
        for k in keys:
            seen[k] = seen.get(k, 0) + 1
        dups = {k: v for k, v in seen.items() if v > 1}
        violations.append(f"Duplicate keys: {dups}")
        for k, v in dups.items():
            logger.error("[IMP:10][gate] DUP: key '%s' appears %d times in .env.example", k, v)

    assert not violations, "GATE_ENV_EXAMPLE_SYNC: .env.example malformed:\n  " + "\n  ".join(violations)
    logger.info("[IMP:9][gate] PASS: .env.example well-formed (%d unique keys)", len(keys))


@pytest.mark.gate
@ldd_trajectory
def test_env_mirrors_example_keys(caplog):
    """If .env exists, all keys must appear in both files (bidirectional mirror, merged W2 T2.3)."""
    env_path = ROOT / ".env"
    if not env_path.is_file():
        logger.info("[IMP:7][gate] .env not found — skipping key mirror check (CI mode)")
        pytest.skip(".env not available (CI)")

    example_keys = set(_parse_keys_in_order(ENV_EXAMPLE))
    env_keys = set(_parse_keys_in_order(env_path))

    violations = []
    missing_from_env = example_keys - env_keys
    if missing_from_env:
        violations.append(
            f"Keys in .env.example but missing from .env ({len(missing_from_env)}): "
            + ", ".join(sorted(missing_from_env))
        )
    missing_from_example = env_keys - example_keys
    if missing_from_example:
        violations.append(
            f"Keys in .env but missing from .env.example ({len(missing_from_example)}): "
            + ", ".join(sorted(missing_from_example))
        )

    assert not violations, (
        f"GATE_ENV_SYNC: {len(violations)} key drift(s) between .env and .env.example:\n  " + "\n  ".join(violations)
    )
    logger.info("[IMP:9][gate] PASS: .env ↔ .env.example keys match bidirectionally (%d keys)", len(example_keys))


@pytest.mark.gate
@ldd_trajectory
def test_env_example_key_order_matches_dotenv(caplog):
    """If .env exists, key order must be identical (structural mirror, merged W2 T2.3)."""
    env_path = ROOT / ".env"
    if not env_path.is_file():
        logger.info("[IMP:7][gate] .env not found — skipping order check (CI mode)")
        pytest.skip(".env not available (CI)")

    example_keys = _parse_keys_in_order(ENV_EXAMPLE)
    env_keys = _parse_keys_in_order(env_path)

    violations = []
    max_len = min(len(example_keys), len(env_keys))
    for i in range(max_len):
        if example_keys[i] != env_keys[i]:
            violations.append(f"Position {i}: .env.example has '{example_keys[i]}', .env has '{env_keys[i]}'")
            logger.error("[IMP:10][gate] ORDER: pos %d: example='%s' env='%s'", i, example_keys[i], env_keys[i])
    if len(example_keys) != len(env_keys):
        violations.append(f"Key count mismatch: .env.example={len(example_keys)}, .env={len(env_keys)}")

    assert not violations, f"GATE_ENV_ORDER_SYNC: {len(violations)} order mismatch(es):\n" + "\n  ".join(violations)
    logger.info("[IMP:9][gate] PASS: .env ↔ .env.example key order match (%d keys)", len(example_keys))


@pytest.mark.gate
@ldd_trajectory
def test_hermes_agent_env_example_well_formed(caplog):
    """hermes-agent/.env.example must have no duplicate keys (if exists, merged W2 T2.3)."""
    if not HERMES_AGENT_ENV_EXAMPLE.is_file():
        pytest.skip("hermes-agent/.env.example not found")

    keys = _parse_keys_in_order(HERMES_AGENT_ENV_EXAMPLE)
    key_set = set(keys)

    violations = []
    if len(keys) != len(key_set):
        seen = {}
        for k in keys:
            seen[k] = seen.get(k, 0) + 1
        dups = {k: v for k, v in seen.items() if v > 1}
        violations.append(f"Duplicate keys in hermes-agent/.env.example: {dups}")

    assert not violations, "GATE_HERMES_AGENT_ENV_EXAMPLE: malformed:\n  " + "\n  ".join(violations)
    logger.info("[IMP:9][gate] PASS: hermes-agent/.env.example well-formed (%d unique keys)", len(keys))


@pytest.mark.gate
@ldd_trajectory
def test_env_noproxy_subset(caplog):
    """.env.example NO_PROXY — канонический список. .env NO_PROXY ⊆ .env.example NO_PROXY (merged W2 T2.3)."""
    env_path = ROOT / ".env"
    if not env_path.is_file():
        logger.info("[IMP:7][gate] .env not found — skipping NO_PROXY subset check (CI mode)")
        pytest.skip(".env not available (CI)")

    env_noproxy = _parse_env_value(env_path, "NO_PROXY")
    example_noproxy = _parse_env_value(ENV_EXAMPLE, "NO_PROXY")

    if env_noproxy is None or example_noproxy is None:
        logger.info("[IMP:7][gate] NO_PROXY not set in one or both files — skipping")
        return

    env_hosts = {h.strip() for h in env_noproxy.split(",") if h.strip()}
    example_hosts = {h.strip() for h in example_noproxy.split(",") if h.strip()}

    violations = []
    extra_hosts = env_hosts - example_hosts
    if extra_hosts:
        violations.append(
            f"NO_PROXY in .env contains hosts not in .env.example ({len(extra_hosts)}): "
            + ", ".join(sorted(extra_hosts))
        )

    assert not violations, (
        f"GATE_ENV_NOPROXY_SUBSET: {len(violations)} NO_PROXY host(s) are external:\n  "
        + "\n  ".join(violations)
        + f"\n.env.example NO_PROXY: {', '.join(sorted(example_hosts))}"
    )
    logger.info("[IMP:9][gate] PASS: .env NO_PROXY ⊆ .env.example NO_PROXY (%d hosts)", len(env_hosts))


# ══════════════════════════════════════════════════════════════════════════════
# W2 T2.3 — MERGED from test_gate_env_example_template.py (template PLATFORM_* ⊆ provides)
# ══════════════════════════════════════════════════════════════════════════════


@pytest.mark.gate
@ldd_trajectory
def test_env_example_subset_of_provides(caplog) -> None:
    """Каждая PLATFORM_* в .env.example шаблонов должна быть в платформенном окружении (merged W2 T2.3)."""
    with pathlib.Path(PLATFORM_ENV).open(encoding="utf-8") as f:
        env_data = yaml.safe_load(f)
    provided = _provided_vars(env_data)
    logger.info("[IMP:8][env_example] Provided PLATFORM_* names: %d", len(provided))

    violations: list[str] = []
    checked_templates = 0

    for template_name in ("template-backend", "template-frontend"):
        example_file = ROOT / "templates" / template_name / ".env.example"
        if not example_file.is_file():
            logger.info("[IMP:7][env_example] %s: .env.example отсутствует — skip", template_name)
            continue
        checked_templates += 1
        for line_no, line in enumerate(example_file.read_text().splitlines(), 1):
            m = VAR_RE.match(line.strip())
            if not m:
                continue
            var = m.group(1)
            if var not in provided:
                violations.append(f"{template_name}:{line_no}: {var}")

    assert checked_templates >= 2, f"Ожидались .env.example в обоих шаблонах, проверено: {checked_templates}"

    if violations:
        pytest.fail(
            ".env.example содержит переменные, отсутствующие в платформенном окружении "
            "(platform-env.yaml#provides/networks, контракт gen_env_platform):\n"
            + "\n".join(f"  - {v}" for v in violations)
            + "\n\nДобавьте переменную в platform-env.yaml или удалите из .env.example."
        )

    logger.info("[IMP:9][env_example] PASS: все PLATFORM_* в .env.example ∈ provided (%d vars)", len(provided))
