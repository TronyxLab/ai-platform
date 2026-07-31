# GREP_SUMMARY: gate domain-parity PLATFORM_DOMAIN test.local admin@test.local single-source env-chain
# STRUCTURE: ▶ ┌SoT: platform-infra env_defaults.PLATFORM_DOMAIN┐ → ◇ (a) defined exactly once + in generated → ◇ (b) 0 × test.local/admin@test.local in prod dirs → ◇ (c) env_defaults_generated has no PLATFORM_DOMAIN → ⎋ pass|fail
# region MODULE_CONTRACT
## @purpose  Parity gate (DevPlan 116 T3/T9, U-16/U-17): PLATFORM_DOMAIN имеет РОВНО одно
##           определение — core/platform-infra.yaml env_defaults. Устраняет test.local
##           артефакты из производственной цепочки env (env-цепочка без test.local).
## @scope    Read-only gate. Сканирует {core/, Makefile, makefiles/, .github/,
##           platform-env.yaml, .env.example, templates/} на test.local / admin@test.local.
##           tests/ исключены — фикстуры test_add_vhost.py используют test.local как данные.
## @invariants
##   - (a) PLATFORM_DOMAIN определён ровно один раз в SoT (platform-infra env_defaults)
##   - (a) PLATFORM_DOMAIN присутствует в generated: platform-env.yaml + .env.example
##   - (b) 0 вхождений test.local / admin@test.local в прод-директориях
##   - (c) env_defaults_generated.py не содержит PLATFORM_DOMAIN (production-only key)
##   - Test marked @pytest.mark.gate — runs in `make gate MODE=fast`
## @rationale U-16: PLATFORM_DOMAIN ×4 копии + test.local + PLATFORM_MASTER_EMAIL=admin@test.local.
##            Единый SoT делает домен-дрейф структурно невозможным (allowlist-гейт).
## @changes 2026-07-31 | Created (DevPlan 116 T9)
# endregion MODULE_CONTRACT

import logging
import re

import pytest
import yaml

from tests.conftest import ldd_trajectory
from tests.helpers.gate_helpers import repo_root

logger = logging.getLogger(__name__)

ROOT = repo_root()
PLATFORM_INFRA = ROOT / "core" / "platform-infra.yaml"
PLATFORM_ENV = ROOT / "platform-env.yaml"
ENV_EXAMPLE = ROOT / ".env.example"
ENV_DEFAULTS_GENERATED = ROOT / "tests" / "helpers" / "env_defaults_generated.py"

# Директории/файлы прод-цепочки — test.local/admin@test.local ЗАПРЕЩЕНЫ (tests/ исключены)
_SCAN_PATHS = [
    "core",
    "Makefile",
    "makefiles",
    ".github",
    "platform-env.yaml",
    ".env.example",
    "templates",
]

_FORBIDDEN = ["test.local", "admin@test.local"]


# ── (a) single SoT definition + presence in generated ────────────────────────


@pytest.mark.gate
@ldd_trajectory
def test_platform_domain_single_sot(caplog) -> None:
    """PLATFORM_DOMAIN must be defined exactly once in SoT and present in generated files."""
    with open(PLATFORM_INFRA) as f:
        infra = yaml.safe_load(f)
    env_defaults = infra.get("env_defaults") or {}

    # Exactly one definition in SoT
    occurrences = [k for k in env_defaults if k == "PLATFORM_DOMAIN"]
    if len(occurrences) != 1:
        logger.error("[IMP:10][domain_parity][a] PLATFORM_DOMAIN occurrences in SoT: %d", len(occurrences))
        pytest.fail(
            "PLATFORM_DOMAIN must be defined exactly ONCE in platform-infra.yaml env_defaults "
            f"(found {len(occurrences)} occurrences)"
        )
    pd_sot = env_defaults["PLATFORM_DOMAIN"]
    if pd_sot != "ai-platform.local":
        logger.error("[IMP:10][domain_parity][a] SoT PLATFORM_DOMAIN=%r", pd_sot)
        pytest.fail(f"platform-infra.yaml env_defaults.PLATFORM_DOMAIN must be ai-platform.local, got {pd_sot!r}")

    # Present in generated platform-env.yaml
    with open(PLATFORM_ENV) as f:
        pe = yaml.safe_load(f)
    pe_pd = (pe.get("env_defaults") or {}).get("PLATFORM_DOMAIN")
    if pe_pd != pd_sot:
        logger.error("[IMP:10][domain_parity][a] platform-env.yaml PLATFORM_DOMAIN=%r", pe_pd)
        pytest.fail(
            f"platform-env.yaml env_defaults.PLATFORM_DOMAIN={pe_pd!r} != SoT {pd_sot!r} — "
            "run `make generate-platform-env`"
        )

    # Present in generated .env.example
    env_line = None
    with open(ENV_EXAMPLE) as f:
        for line in f:
            if line.startswith("PLATFORM_DOMAIN="):
                env_line = line.split("=", 1)[1].strip()
                break
    if env_line != pd_sot:
        logger.error("[IMP:10][domain_parity][a] .env.example PLATFORM_DOMAIN=%r", env_line)
        pytest.fail(f".env.example PLATFORM_DOMAIN={env_line!r} != SoT {pd_sot!r} — run `make sync-env-defaults`")

    logger.info("[IMP:9][domain_parity][a] PASS: single SoT definition + generated parity (%s)", pd_sot)


# ── (b) zero test.local / admin@test.local in production chain ────────────────


@pytest.mark.gate
@ldd_trajectory
def test_no_test_local_in_production_chain(caplog) -> None:
    """test.local / admin@test.local must NOT appear in production env chain (U-16/U-17)."""
    violations: list[str] = []

    def scan_file(path, rel: str) -> None:
        try:
            content = path.read_text(errors="replace")
        except OSError:
            return
        for forbidden in _FORBIDDEN:
            if forbidden in content:
                for i, line in enumerate(content.splitlines(), 1):
                    if forbidden in line:
                        violations.append(f"{rel}:{i}: {forbidden} → {line.strip()[:100]}")

    for rel in _SCAN_PATHS:
        path = ROOT / rel
        if not path.exists():
            continue
        if path.is_file():
            scan_file(path, rel)
            continue
        for p in sorted(path.rglob("*")):
            if not p.is_file():
                continue
            parts = p.relative_to(ROOT).parts
            if any(seg.startswith(".") or seg == "__pycache__" for seg in parts):
                continue
            # Бинарные файлы (pyc, images) не сканируем
            if b"\x00" in p.read_bytes()[:2048]:
                continue
            scan_file(p, p.relative_to(ROOT).as_posix())

    if violations:
        logger.error("[IMP:10][domain_parity][b] test.local artifacts in production chain:")
        for v in violations:
            logger.error("  %s", v)
        pytest.fail(
            f"test.local / admin@test.local found in {len(violations)} production location(s):\n"
            + "\n".join(f"  - {v}" for v in violations)
            + "\n\nSoT: core/platform-infra.yaml env_defaults.PLATFORM_DOMAIN=ai-platform.local "
            "(DevPlan 116 T3)."
        )

    logger.info("[IMP:9][domain_parity][b] PASS: zero test.local/admin@test.local in production chain")


# ── (c) env_defaults_generated has no PLATFORM_DOMAIN ─────────────────────────


@pytest.mark.gate
@ldd_trajectory
def test_env_defaults_generated_has_no_domain(caplog) -> None:
    """env_defaults_generated.py must NOT contain PLATFORM_DOMAIN (production-only key)."""
    content = ENV_DEFAULTS_GENERATED.read_text()
    if "PLATFORM_DOMAIN" in content:
        logger.error("[IMP:10][domain_parity][c] PLATFORM_DOMAIN leaked into env_defaults_generated.py")
        pytest.fail(
            "PLATFORM_DOMAIN must NOT be in env_defaults_generated.py — "
            "it's a production-only key (not a secret ci_default), set during deployment"
        )

    logger.info("[IMP:9][domain_parity][c] PASS: env_defaults_generated.py has no PLATFORM_DOMAIN")
