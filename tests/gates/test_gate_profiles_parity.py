# GREP_SUMMARY: gate profiles-parity COMPOSE_PROFILES SoT platform-infra allowlist no-hardcopy
# STRUCTURE: ▶ ┌SoT: platform-infra env_defaults.COMPOSE_PROFILES┐ → ◇ (a) == discovered modules → ◇ (b) == platform-env/.env.example → ◇ (c) == make _get_all_profiles → ◇ (d) no copies outside allowlist → ⎋ pass|fail
# region MODULE_CONTRACT
## @purpose  Parity gate (DevPlan 116 T9, U-02): COMPOSE_PROFILES имеет РОВНО один SoT —
##           core/platform-infra.yaml env_defaults. Проверяет 4 паритета: (a) SoT ==
##           обнаруженные docker-модули, (b) generated == SoT, (c) make _get_all_profiles
##           == SoT, (d) полная строка отсутствует во всех tracked-файлах кроме allowlist
##           {core/platform-infra.yaml, platform-env.yaml, .env.example}.
## @scope    Read-only gate — не модифицирует файлы. Может поднять make _get_all_profiles.
## @invariants
##   - SoT = platform-infra.yaml env_defaults.COMPOSE_PROFILES (13-item, comma-separated)
##   - (a) SoT set == set(parent names of discover_docker_modules(core/modules))
##   - (b) platform-env.yaml env_defaults.COMPOSE_PROFILES == SoT; .env.example COMPOSE_PROFILES= == SoT
##   - (c) `make _get_all_profiles` stdout == SoT
##   - (d) полная SoT-строка найдена только в allowlist-файлах (rg по tracked-файлам)
##   - Test marked @pytest.mark.gate — runs in `make gate MODE=fast`
## @rationale Устраняет 8 ручных копий COMPOSE_PROFILES (U-02). Гейт с allowlist:
##            хардкод разрешён только в SoT и generated-файлах (решение 01-Brief §1).
## @changes 2026-07-31 | Created (DevPlan 116 T9)
# endregion MODULE_CONTRACT

import logging
import pathlib
import subprocess

import pytest
import yaml

from tests.conftest import ldd_trajectory
from tests.helpers.gate_helpers import repo_root

logger = logging.getLogger(__name__)

ROOT = repo_root()
PLATFORM_INFRA = ROOT / "core" / "platform-infra.yaml"
PLATFORM_ENV = ROOT / "platform-env.yaml"
ENV_EXAMPLE = ROOT / ".env.example"

# allowlist: файлы, где полная COMPOSE_PROFILES строка ДОПУСТИМА (SoT + generated)
_ALLOWLIST = {
    "core/platform-infra.yaml",
    "platform-env.yaml",
    ".env.example",
}


def _load_yaml(path) -> dict:
    with pathlib.Path(path).open(encoding="utf-8") as f:
        return yaml.safe_load(f)


def _sot_profiles() -> str:
    """SoT: core/platform-infra.yaml env_defaults.COMPOSE_PROFILES."""
    infra = _load_yaml(PLATFORM_INFRA)
    profiles = (infra.get("env_defaults") or {}).get("COMPOSE_PROFILES")
    if not profiles:
        pytest.fail("platform-infra.yaml env_defaults.COMPOSE_PROFILES missing (SoT)")
    return str(profiles)


def _discovered_module_names() -> set[str]:
    """Docker module names via canonical predicate (DevPlan 116 T7)."""
    from core.internal.scripts.module_discovery import discover_docker_modules

    compose_files = discover_docker_modules(ROOT / "core" / "modules")
    return {p.parent.name for p in compose_files}


# ── (a) SoT == discovered docker modules ──────────────────────────────────────


@pytest.mark.gate
@ldd_trajectory
def test_sot_matches_discovered_modules(caplog) -> None:
    """COMPOSE_PROFILES SoT must equal the discovered docker module set (13==13)."""
    sot_set = {s.strip() for s in _sot_profiles().split(",") if s.strip()}
    discovered = _discovered_module_names()

    missing_in_discovered = sot_set - discovered
    extra_in_discovered = discovered - sot_set

    logger.info(
        "[IMP:8][profiles_parity][a] SoT=%d modules, discovered=%d modules",
        len(sot_set),
        len(discovered),
    )
    if missing_in_discovered or extra_in_discovered:
        logger.error(
            "[IMP:10][profiles_parity][a] SoT/discovered mismatch: missing=%s extra=%s",
            sorted(missing_in_discovered),
            sorted(extra_in_discovered),
        )
        pytest.fail(
            "COMPOSE_PROFILES SoT diverges from discovered docker modules:\n"
            f"  in SoT, not discovered: {sorted(missing_in_discovered)}\n"
            f"  discovered, not in SoT: {sorted(extra_in_discovered)}\n"
            "Update core/platform-infra.yaml env_defaults.COMPOSE_PROFILES (SoT)."
        )

    logger.info("[IMP:9][profiles_parity][a] PASS: SoT == discovered (%d modules)", len(sot_set))


# ── (b) generated parity: platform-env.yaml + .env.example == SoT ─────────────


@pytest.mark.gate
@ldd_trajectory
def test_generated_files_match_sot(caplog) -> None:
    """platform-env.yaml and .env.example COMPOSE_PROFILES must equal the SoT."""
    sot = _sot_profiles()

    # platform-env.yaml env_defaults
    platform_env = _load_yaml(PLATFORM_ENV)
    pe_profiles = str((platform_env.get("env_defaults") or {}).get("COMPOSE_PROFILES", ""))
    if pe_profiles != sot:
        logger.error("[IMP:10][profiles_parity][b] platform-env.yaml != SoT")
        pytest.fail(
            f"platform-env.yaml COMPOSE_PROFILES={pe_profiles!r} != SoT {sot!r} — run `make generate-platform-env`"
        )

    # .env.example COMPOSE_PROFILES=
    env_line = None
    with pathlib.Path(ENV_EXAMPLE).open(encoding="utf-8") as f:
        for line in f:
            if line.startswith("COMPOSE_PROFILES="):
                env_line = line.split("=", 1)[1].strip()
                break
    if env_line != sot:
        logger.error("[IMP:10][profiles_parity][b] .env.example != SoT")
        pytest.fail(f".env.example COMPOSE_PROFILES={env_line!r} != SoT {sot!r} — run `make generate-env-example`")

    logger.info("[IMP:9][profiles_parity][b] PASS: platform-env.yaml + .env.example == SoT")


# ── (c) make _get_all_profiles == SoT ─────────────────────────────────────────


@pytest.mark.gate
@ldd_trajectory
def test_make_get_all_profiles_matches_sot(caplog) -> None:
    """`make _get_all_profiles` output must equal the SoT (runtime-чтение через yaml_query)."""
    sot = _sot_profiles()

    result = subprocess.run(
        ["make", "_get_all_profiles"],
        capture_output=True,
        text=True,
        timeout=30,
        cwd=str(ROOT),
        check=False,
    )
    if result.returncode != 0:
        pytest.fail(f"make _get_all_profiles failed (exit {result.returncode}): {result.stderr.strip()}")

    lines = [line for line in result.stdout.strip().splitlines() if not line.startswith("make[")]
    actual = "".join(lines).strip()
    if actual != sot:
        logger.error("[IMP:10][profiles_parity][c] make _get_all_profiles != SoT")
        pytest.fail(f"make _get_all_profiles={actual!r} != SoT {sot!r} — helpers.mk must read SoT via yaml_query.py")

    logger.info("[IMP:9][profiles_parity][c] PASS: make _get_all_profiles == SoT")


# ── (d) no hardcoded copies outside allowlist ─────────────────────────────────


@pytest.mark.gate
@ldd_trajectory
def test_no_hardcoded_profiles_outside_allowlist(caplog) -> None:
    """Full COMPOSE_PROFILES string must appear only in the allowlist (SoT + generated)."""
    sot = _sot_profiles()

    tracked = subprocess.run(
        ["git", "ls-files"],
        capture_output=True,
        text=True,
        timeout=30,
        cwd=str(ROOT),
        check=False,
    )
    if tracked.returncode != 0:
        pytest.fail("git ls-files failed")
    tracked_files = [f for f in tracked.stdout.strip().splitlines() if f]

    violations: list[str] = []
    for rel in tracked_files:
        if rel in _ALLOWLIST:
            continue
        path = ROOT / rel
        if not path.is_file():
            continue
        try:
            content = path.read_text(errors="replace")
        except OSError:
            continue
        if sot in content:
            violations.append(rel)

    if violations:
        logger.error("[IMP:10][profiles_parity][d] Hardcoded COMPOSE_PROFILES copies: %s", violations)
        pytest.fail(
            "COMPOSE_PROFILES hardcoded outside allowlist "
            "{platform-infra.yaml, platform-env.yaml, .env.example}:\n"
            + "\n".join(f"  - {v}" for v in violations)
            + "\n\nReplace with runtime-read via yaml_query.py (DevPlan 116 T2)."
        )

    logger.info("[IMP:9][profiles_parity][d] PASS: no hardcoded COMPOSE_PROFILES outside allowlist")
