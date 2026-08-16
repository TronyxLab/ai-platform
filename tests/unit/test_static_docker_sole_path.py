"""Static layer: docker-sole-path detector tests (DevPlan 163 W-C C2).

# GREP_SUMMARY: test-static docker-sole-path subprocess docker-compose docker-ops ps inspect exec shell R5 U-13
# STRUCTURE: ▶ subprocess docker compose вне shared (synthetic) → RED | ▶ subprocess docker ps
#            вне docker_ops (synthetic) → RED | ▶ shell `docker compose` вне фасадов (R5 D70) → RED
#            → ▶ control: docker compose в shared/docker_compose.py → PASS → ⎋
"""
# region MODULE_CONTRACT
## @purpose  R5-пары детектора docker_sole_path (DevPlan 163 W-C C2): позитивные тесты на
##           синтетические нарушения (docker compose и docker ps вне sole-path фасадов),
##           R5-негатив на shell/make-класс D70 (прямой `docker compose` в entrypoints-фасаде
##           вне allowlist), PASS-контроль (вызовы в разрешённых фасадах не RED).
## @scope    Native imports; probe-файлы в tmp_path с layout core/internal/... и
##           core/entrypoints/... (Zero Hardcode Rule).
## @invariants
##   - subprocess docker compose вне core/internal/shared/docker_compose.py → RED (U-13)
##   - subprocess docker ps вне docker_ops.py/watchdog.py → RED (128 W1)
##   - `docker compose` в shell/make вне allowlist → RED (D70)
##   - docker compose в shared/docker_compose.py → PASS
## @rationale R5 anti-survivorship: U-13 — каждая волна добавляла копию docker compose
##            up/pull; D70 — слепая зона shell/make точек. Детектор ловит возврат копий.
## @changes 2026-08-13 | DevPlan 163 W-C C2 — Created
# endregion MODULE_CONTRACT

from __future__ import annotations

import logging

from core.internal.static.docker_sole_path import detect
from tests._conftest.ldd import ldd_trajectory

logger = logging.getLogger(__name__)


# 🧪 TRAP[TEST] · POSITIVE · synthetic subprocess docker compose вне shared → RED
# · Scenario: core/internal/bootstrap/_probe.py c `subprocess.run(["docker", "compose", "up"])`
# ·   — копия compose-вызова вне единственного фасада shared/docker_compose.py (U-13)
# · Last fail: N/A (синтетический вариант)
# · Remove if: docker-sole-path гейт отменяется
@ldd_trajectory
def test_docker_sole_path_compose_outside_shared_detected(caplog, tmp_path) -> None:
    """Synthetic positive: docker compose subprocess вне shared/docker_compose.py → RED."""
    probe = tmp_path / "core" / "internal" / "bootstrap" / "_probe_compose.py"
    probe.parent.mkdir(parents=True)
    probe.write_text(
        'import subprocess\nsubprocess.run(["docker", "compose", "up", "-d"], check=True)\n',
        encoding="utf-8",
    )
    findings = detect(tmp_path)
    hits = [f for f in findings if "_probe_compose" in f.file]
    assert hits, "R5 FAIL: docker compose subprocess outside shared not detected"
    assert "docker compose" in hits[0].message
    logger.info("[IMP:9][test_docker_sole_path] compose outside shared RED: %s", hits[0])


# 🧪 TRAP[TEST] · POSITIVE · synthetic docker ps вне docker_ops → RED (128 W1)
# · Scenario: core/internal/_probe_ops.py c `subprocess.run(["docker", "ps", "-a"])` —
# ·   копия ops-вызова вне shared/docker_ops.py (drift-акселератор 128 W1)
# · Last fail: N/A (синтетический вариант)
# · Remove if: docker-sole-path гейт отменяется
@ldd_trajectory
def test_docker_sole_path_ops_outside_shared_detected(caplog, tmp_path) -> None:
    """Synthetic positive: docker ps subprocess вне shared/docker_ops.py → RED."""
    probe = tmp_path / "core" / "internal" / "_probe_ops.py"
    probe.parent.mkdir(parents=True)
    probe.write_text(
        'import subprocess\nsubprocess.run(["docker", "ps", "-a"], check=True)\n',
        encoding="utf-8",
    )
    findings = detect(tmp_path)
    hits = [f for f in findings if "_probe_ops" in f.file]
    assert hits, "R5 FAIL: docker ps subprocess outside docker_ops not detected"
    logger.info("[IMP:9][test_docker_sole_path] docker ps outside docker_ops RED: %s", hits[0])


# 🧪 TRAP[TEST] · NEGATIVE (R5) · прямой `docker compose` в shell вне фасадов → RED (D70)
# · Scenario: core/entrypoints/_probe_direct.sh c `docker compose up -d` — класс дефекта
# ·   D70 (модульные Makefile/entrypoints вызывали compose напрямую, минуя фасады)
# · Last fail: D70-класс — compose вызовы в module.mk/entrypoints вне канонических фасадов
# · Remove if: docker-sole-path гейт отменяется
@ldd_trajectory
def test_docker_sole_path_negative_shell_direct_compose(caplog, tmp_path) -> None:
    """R5 negative: прямой `docker compose` в shell вне allowlist → RED (D70)."""
    probe = tmp_path / "core" / "entrypoints" / "_probe_direct.sh"
    probe.parent.mkdir(parents=True)
    probe.write_text("#!/usr/bin/env bash\ndocker compose up -d\n", encoding="utf-8")
    findings = detect(tmp_path)
    hits = [f for f in findings if "_probe_direct" in f.file]
    assert hits, "R5 FAIL: direct `docker compose` in shell (D70) not detected"
    logger.info("[IMP:9][test_docker_sole_path] R5 direct shell compose RED: %s", hits[0])


# 🧪 TRAP[TEST] · CONTROL · docker compose в shared/docker_compose.py → PASS
# · Scenario: probe в shared/docker_compose.py c docker compose subprocess — разрешённый фасад → 0 RED
# · Last fail: N/A (control — легитимный фасад не должен быть RED)
# · Remove if: docker-sole-path гейт отменяется
@ldd_trajectory
def test_docker_sole_path_allowed_facades_not_flagged(caplog, tmp_path) -> None:
    """PASS-контроль: вызовы в разрешённом фасаде (shared/docker_compose.py) не RED."""
    shared = tmp_path / "core" / "internal" / "shared"
    shared.mkdir(parents=True)
    (shared / "docker_compose.py").write_text(
        'import subprocess\nsubprocess.run(["docker", "compose", "ps"], check=True)\n',
        encoding="utf-8",
    )
    findings = detect(tmp_path)
    hits = [f for f in findings if f.file in {"core/internal/shared/docker_compose.py"}]
    assert not hits, f"PASS-control FAIL: allowed facade flagged: {hits}"
    logger.info("[IMP:9][test_docker_sole_path] allowed facade (shared) not flagged")
