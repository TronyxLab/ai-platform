#!/usr/bin/env python3
# GREP_SUMMARY: docker-smoke-parity, xdist, timeout, pre-cleanup, ownership, check-suite, workflow, conftest, REF-0111, hang-class
# STRUCTURE: ▶ load check-suite.yaml + platform-test.yml + compose.py/timeouts.py →
#            ⊕ P1 owner-fields · P2 executor-channel · P3 compose-timeout-parity ·
#            P4 pre-cleanup-present · P5 budget-relations → ⎋ FAIL при дрейфе любого слоя
# region MODULE_CONTRACT
## @purpose  Parity-gate исполнения docker-smoke (REF-0111): параметры исполнения docker-тестов
##           владеет core/check-suite.yaml (schema v1: xdist/timeout/cmd --timeout), остальные
##           слои ПОТРЕБЛЯЮТ. Дрейф трёх слоёв = 900s-hang класс (инцидент 2026-08-17,
##           ≥10 коммитов починки; каждый регресс блокирует верификацию hotfix'ов).
## @scope    Три слоя: core/check-suite.yaml (владелец), .github/workflows/platform-test.yml,
##           tests/_conftest/compose.py (+ shared/timeouts.py как источник констант).
## @invariants
##   - P1: smoke в манифесте — xdist:false, timeout > per-test (--timeout в cmd), junit-отчёт
##   - P2: ci-docker шаг workflow вызывает `make gate MODE=ci-docker` (параметры резолвит
##     executor из манифеста); прямой pytest с собственными параметрами в шаге = drift
##   - P3: PLATFORM_COMPOSE_TIMEOUT workflow == shared/timeouts.COMPOSE_UP_TIMEOUT
##     (однократный health-wait вместо retry-спирали)
##   - P4: pre-cleanup (docker system prune -af) присутствует ДО ci-docker шага
##   - P5: бюджетные отношения: setup-deadline conftest (540s) < smoke timeout;
##     per-test timeout < suite timeout; compose-default fallback < workflow override
## ⚠️ TRAP[DECISION] · 2026-08-25 · — · pre_cleanup НЕ переносится в check-suite.yaml
# · Rejected: новое поле pre_cleanup: в записи smoke (карточка REF-0111 предлагала xdist:/
# ·   timeout_s:/pre_cleanup:) · Reason: freeze P3 п.11 — schema v1 манифеста не расширяется;
# ·   xdist и timeout уже являются полями v1 (ownership фактически у манифеста), а pre-cleanup —
# ·   операция ОКРУЖЕНИЯ CI-раннера (docker system prune до стека), не параметр сьюта; гейт
# ·   закрепляет его наличие и позицию в workflow до пересмотра freeze.
# · Rev: schema v2 (несовместимое изменение, invalidate fingerprint) или вынос pre-cleanup
# ·   в отдельный make-шаг — тогда поле легально появится в манифесте.
## @changes 2026-08-25 | REF-0111 (DevPlan 11 Волна 3) — Created
# endregion MODULE_CONTRACT

import logging
import re

import pytest

from tests.conftest import ldd_trajectory
from tests.helpers.gate_helpers import load_yaml, repo_root

logger = logging.getLogger(__name__)

_ROOT = repo_root()
_MANIFEST_PATH = _ROOT / "core" / "check-suite.yaml"
_PLATFORM_TEST = _ROOT / ".github" / "workflows" / "platform-test.yml"
_COMPOSE_PY = _ROOT / "tests" / "_conftest" / "compose.py"


def _smoke_entry() -> dict:
    """Запись smoke из check-suite.yaml."""
    manifest = load_yaml(_MANIFEST_PATH)
    entry = next((c for c in manifest.get("checks", []) if c.get("id") == "smoke"), None)
    assert entry is not None, "[IMP:10][smoke-parity] запись smoke исчезла из манифеста"
    return entry


def _workflow_env_int(workflow_text: str, var: str) -> int:
    """Извлечь int-значение env-var из workflow (формат `VAR: N`)."""
    match = re.search(rf"^\s*{var}:\s*(\d+)\s*$", workflow_text, re.MULTILINE)
    assert match is not None, f"[IMP:10][smoke-parity] {var} не найден в platform-test.yml"
    return int(match.group(1))


# region TEST_docker_smoke_ownership
@pytest.mark.gate
@ldd_trajectory
# 🧪 TRAP[TEST] · 2026-08-25 · REGRESSION · REF-0111: дрейф 3 слоёв = hang-класс инцидентов
# · Scenario: кто-то возвращает xdist:true для smoke / меняет таймаут одного слоя /
#   удаляет pre-cleanup → конкурентные воркеры на одном стеке → 900s килл без диагностики
# · Last fail: инцидент 2026-08-17 (CI smoke 900s-hang, apply_xdist вставлял -n auto)
# · Remove if: A-40 extraction (test-infra редизайн, P2) меняет архитектуру исполнения
def test_docker_smoke_execution_contract_parity(caplog) -> None:
    """Параметры docker-smoke синхронны во всех трёх слоях (owner: check-suite.yaml)."""
    caplog.set_level(logging.INFO)

    # ── P1: владелец — check-suite.yaml ──────────────────────────────────────
    smoke = _smoke_entry()
    assert smoke.get("xdist") is False, (
        f"[IMP:10][smoke-parity] smoke.xdist={smoke.get('xdist')!r}: docker-сьют обязан быть "
        "single-process (TRAP compose.py 2026-08-05; инцидент 900s-hang)"
    )
    suite_timeout = int(smoke.get("timeout", 0))
    assert suite_timeout >= 600, f"[IMP:10][smoke-parity] smoke.timeout={suite_timeout} < 600s бюджет полного стека"
    cmd = smoke.get("cmd", "")
    per_test = re.search(r"--timeout=(\d+)", cmd)
    assert per_test is not None, "[IMP:10][smoke-parity] в cmd smoke отсутствует per-test --timeout"
    assert int(per_test.group(1)) < suite_timeout, (
        f"[IMP:10][smoke-parity] per-test timeout ({per_test.group(1)}s) ≥ suite timeout ({suite_timeout}s): "
        "висящий тест должен падать БЫСТРО с именем, а не тихо убиваться бюджетом сьюта"
    )
    assert smoke.get("junit") == "tests/report-smoke.xml", "smoke.junit сместился — каналы отчётности разорваны"

    # ── P2: workflow потребляет через executor ───────────────────────────────
    wf_text = _PLATFORM_TEST.read_text(errors="replace")
    body = "\n".join(ln for ln in wf_text.splitlines() if not ln.strip().startswith("#"))
    assert "make gate MODE=ci-docker" in body, (
        "[IMP:10][smoke-parity] ci-docker шаг workflow не вызывает make gate MODE=ci-docker — "
        "параметры перестали резолвиться из манифеста (drift-канал REF-0111)"
    )

    # ── P3: compose-timeout parity с SoT ────────────────────────────────────
    from core.internal.shared.timeouts import COMPOSE_UP_TIMEOUT

    wf_compose_timeout = _workflow_env_int(wf_text, "PLATFORM_COMPOSE_TIMEOUT")
    assert wf_compose_timeout == COMPOSE_UP_TIMEOUT, (
        f"[IMP:10][smoke-parity] PLATFORM_COMPOSE_TIMEOUT={wf_compose_timeout} ≠ "
        f"timeouts.COMPOSE_UP_TIMEOUT={COMPOSE_UP_TIMEOUT} (однократный health-wait, без retry-спирали)"
    )

    # ── P4: pre-cleanup присутствует (см. TRAP: вне schema v1, pinned здесь) ──
    prune_pos = body.find("docker system prune -af")
    gate_pos = body.find("make gate MODE=ci-docker")
    assert prune_pos != -1, "[IMP:10][smoke-parity] pre-cleanup (docker system prune -af) удалён из workflow"
    assert 0 <= prune_pos < gate_pos, "[IMP:10][smoke-parity] pre-cleanup должен идти ДО ci-docker gate"

    # ── P5: бюджетные отношения conftest ↔ манифест ─────────────────────────
    compose_text = _COMPOSE_PY.read_text(errors="replace")
    deadline_match = re.search(r"_SMOKE_SETUP_DEADLINE_SECONDS\s*=\s*(\d+)", compose_text)
    assert deadline_match is not None, "_SMOKE_SETUP_DEADLINE_SECONDS исчез из compose.py"
    setup_deadline = int(deadline_match.group(1))
    assert setup_deadline < suite_timeout, (
        f"[IMP:10][smoke-parity] setup-deadline ({setup_deadline}s) ≥ smoke timeout ({suite_timeout}s): "
        "setup обязан укладываться в бюджет с запасом на тесты"
    )
    logger.info(
        "[IMP:9][smoke-parity] PASS: xdist=false, suite=%ds, per-test=%ss, compose-timeout=%d==SoT, "
        "pre-cleanup до gate, setup-deadline=%ds<%ds",
        suite_timeout,
        int(per_test.group(1)),
        wf_compose_timeout,
        setup_deadline,
        suite_timeout,
    )


# endregion TEST_docker_smoke_ownership


# region TEST_conftest_single_process_contract
@pytest.mark.gate
@ldd_trajectory
def test_conftest_documents_single_process(caplog) -> None:
    """Слой conftest держит контракт single-process docker-сьюитов (TRAP 2026-08-05)."""
    caplog.set_level(logging.INFO)
    compose_text = _COMPOSE_PY.read_text(errors="replace")
    assert "single-process" in compose_text, (
        "[IMP:10][smoke-parity] TRAP single-process исчез из compose.py — семантика одного стека "
        "на машину потеряла документацию при следующем рефакторинге"
    )
    logger.info("[IMP:9][smoke-parity] PASS: conftest документирует single-process контракт")


# endregion TEST_conftest_single_process_contract
