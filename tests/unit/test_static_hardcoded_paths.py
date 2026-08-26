"""Static layer: hardcoded-paths detector tests (DevPlan 163 W-C C3).

# GREP_SUMMARY: test-static hardcoded-paths user-homepath platform-root cross-platform R5 P0 server-path
# STRUCTURE: ▶ synthetic /Users/tronyx/... в probe → RED | ▶ R5-оригинал P0 (test_component_hermes.py:66)
#            + UF9 (/opt/platform в core) → RED | ▶ control: автодетект __file__/env-fallback → PASS → ⎋
"""
# region MODULE_CONTRACT
## @purpose  R5-пары детектора hardcoded_paths (DevPlan 163 W-C C3): позитивный тест на
##           синтетическое нарушение (хардкод /Users/<user>/ в probe), R5-негативы на
##           ОРИГИНАЛЬНЫЕ входы гейта (P0 2026-07-23: "/Users/tronyx/projects/ai-platform" в
##           tests; UF9: /opt/platform в core/), PASS-контроль (автодетект __file__/env-fallback).
## @scope    Native imports; probe-файлы в tmp_path (детектор сканирует tests/+core/, для
##           probe-деревьев — рекурсивный скан всех *.py с home-паттерном).
## @invariants
##   - /Users/<user>/... или /home/<user>/... в tests/ + core/ → RED
##   - /opt/platform/... в core/ → RED (server-паттерн, только core/)
##   - os.path.dirname(__file__)-автодетект / env-fallback → файл пропускается
## @rationale R5 anti-survivorship (P0): хардкод "/Users/tronyx/projects/ai-platform" сломал
##            hermes-тесты на CI (macOS vs Linux). Детектор обязан ловить точный вход.
## @changes 2026-08-13 | DevPlan 163 W-C C3 — Created
# endregion MODULE_CONTRACT

from __future__ import annotations

import logging

from core.internal.static.hardcoded_paths import detect
from tests._conftest.ldd import ldd_trajectory

logger = logging.getLogger(__name__)


# 🧪 TRAP[TEST] · POSITIVE · synthetic /Users/<user>/ хардкод → RED
# · Scenario: probe-файл с `"/Users/tronyx/projects/ai-platform"` (литерал-строка) → RED
# · Last fail: N/A (синтетический вариант)
# · Remove if: кросс-платформенный хардкод-гейт отменяется
@ldd_trajectory
def test_hardcoded_paths_user_home_detected(caplog, tmp_path) -> None:
    """Synthetic positive: хардкод /Users/<user>/ пути детектируется."""
    probe = tmp_path / "_probe_home.py"
    probe.write_text(
        'BASE = "/Users/tronyx/projects/ai-platform"\n',
        encoding="utf-8",
    )
    findings = detect(tmp_path)
    hits = [f for f in findings if "_probe_home" in f.file]
    assert hits, "R5 FAIL: hardcoded /Users/ path not detected"
    assert "hardcoded" in hits[0].message
    logger.info("[IMP:9][test_hardcoded_paths] synthetic /Users/ RED: %s", hits[0])


# 🧪 TRAP[TEST] · NEGATIVE (R5) · оригинал P0: /Users/tronyx/... в tests/ → RED
# · Scenario: probe в tests/ с `"/Users/tronyx/projects/ai-platform"` — точный вход
# ·   P0 2026-07-23 (test_component_hermes.py:66 сломал hermes-тесты на CI)
# · Last fail: 2026-07-23 P0 — test_component_hermes.py:66 хардкод "/Users/tronyx/..."
# · Remove if: кросс-платформенный хардкод-гейт отменяется
@ldd_trajectory
def test_hardcoded_paths_negative_p0_original_input(caplog, tmp_path) -> None:
    """R5 negative: оригинальный вход P0 — /Users/tronyx/projects/ai-platform в tests/."""
    probe_dir = tmp_path / "tests"
    probe_dir.mkdir()
    probe = probe_dir / "test_component_hermes.py"
    probe.write_text(
        'PROJECT_ROOT = "/Users/tronyx/projects/ai-platform"\n',
        encoding="utf-8",
    )
    findings = detect(tmp_path)
    hits = [f for f in findings if "test_component_hermes" in f.file]
    assert hits, "R5 FAIL: P0 original input (/Users/tronyx/...) not detected"
    logger.info("[IMP:9][test_hardcoded_paths] R5 P0 input RED: %s", hits[0])


# 🧪 TRAP[TEST] · NEGATIVE (R5) · UF9: /opt/platform/ в core/ → RED
# · Scenario: probe в core/ с `BASE = "/opt/platform/core"` — точный класс UF9
# ·   (compose_preflight.py:45 хардкод /opt/platform — P2 coverage gap 2026-07-23)
# · Last fail: 2026-07-23 UF9 — /opt/platform хардкод в core/ не ловился сканом tests/
# · Remove if: server-path гейт отменяется
@ldd_trajectory
def test_hardcoded_paths_negative_server_path_in_core(caplog, tmp_path) -> None:
    """R5 negative: /opt/platform/ в core/ (UF9 класс) детектируется."""
    probe_dir = tmp_path / "core"
    probe_dir.mkdir()
    probe = probe_dir / "compose_preflight.py"
    probe.write_text(
        'BASE = "/opt/platform/core"\n',
        encoding="utf-8",
    )
    findings = detect(tmp_path)
    hits = [f for f in findings if "compose_preflight" in f.file]
    assert hits, "R5 FAIL: UF9 /opt/platform in core/ not detected"
    logger.info("[IMP:9][test_hardcoded_paths] R5 UF9 server-path RED: %s", hits[0])


# 🧪 TRAP[TEST] · CONTROL · автодетект __file__ / env-fallback → PASS
# · Scenario: probe с os.path.dirname(__file__)-автодетектом и os.environ.get(..., "/opt/...")
# ·   fallback → файл пропускается (легитимный паттерн, allowlist контента)
# · Last fail: N/A (control — легитимный автодетект не должен быть RED)
# · Remove if: автодетект-паттерн заменяется иным механизмом
@ldd_trajectory
def test_hardcoded_paths_autodetect_not_flagged(caplog, tmp_path) -> None:
    """PASS-контроль: os.path.dirname(__file__)-автодетект/env-fallback не RED."""
    probe = tmp_path / "_probe_autodetect.py"
    probe.write_text(
        "import os\n"
        'BASE = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))\n'
        'SERVER = os.environ.get("PLATFORM_ROOT", "/opt/platform")\n',
        encoding="utf-8",
    )
    findings = detect(tmp_path)
    hits = [f for f in findings if "_probe_autodetect" in f.file]
    assert not hits, f"PASS-control FAIL: autodetect/env-fallback flagged: {hits}"
    logger.info("[IMP:9][test_hardcoded_paths] autodetect/env-fallback not flagged")


# 🧪 TRAP[TEST] · 2026-08-26 · P3 · R5-негатив: CI-path исключён, user-path ловится (AI-0036)
# · Regression: lookahead стоял после ПЕРВОГО компонента (`/home/[\w.-]+/(?!runner/work/)`)
#   — исключение /home/runner/work/ не работало, ложные срабатывания на CI
# · Scenario: '/home/runner/work/repo/repo/…' НЕ матчится; '/home/user/projects/app/src/main.py'
#   матчится (прямой unit на regex-компилят hardcoded_paths)
# · Last fail: DevPlan 17 верификация @64c2090 (аудит AI-0036)
# · Remove if: regex заменяется на конфигурируемый allowlist путей
def test_ci_path_excluded_negative() -> None:
    """R5: runner/work не матчится, обычный /home/user/ матчится (T7.6)."""
    from core.internal.static.hardcoded_paths import _HARDCODED_HOME_PATH

    ci_hit = _HARDCODED_HOME_PATH.search('"/home/runner/work/repo/repo/src/main.py"')
    assert not ci_hit, f"CI-path обязан быть исключён: {ci_hit}"

    user_hit = _HARDCODED_HOME_PATH.search('"/home/user/projects/app/src/main.py"')
    assert user_hit, "обычный /home/user/... обязан матчиться"
    logger.critical("[IMP:9][test_hardcoded_paths] CI excluded, user flagged — OK (AI-0036)")
