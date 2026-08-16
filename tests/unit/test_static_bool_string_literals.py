"""Static layer: bool-string-literals detector tests (DevPlan 163 W-C C2).

# GREP_SUMMARY: test-static bool-string-literals strict-comparison lower-normalization R5 T6 probe
# STRUCTURE: ▶ probe `!= "False"` (synthetic) → RED | ▶ probe `== "true"` (R5 T6-оригинал) → RED
#            → ▶ normalized probes (inline .lower() + entry-normalized Name) → PASS → ⎋
"""
# region MODULE_CONTRACT
## @purpose  R5-пары детектора bool_string_literals (DevPlan 163 W-C C2): позитивный
##           тест на синтетическое нарушение (`!= "False"` — вариант, отсутствующий в
##           исходном гейте), негативный R5-тест на ОРИГИНАЛЬНЫЙ вход T6-гейта
##           (`if enabled == "true":`), PASS-контроль нормализованных сравнений.
## @scope    Native imports (core.internal.static.bool_string_literals.detect); probe-файлы
##           в tmp_path (Zero Hardcode Rule); рабочее дерево не загрязняется.
## @invariants
##   - `!= "False"` без .lower() → RED (synthetic, NotEq-вариант)
##   - `== "true"` без .lower() → RED (оригинальный вход T6, R5 anti-survivorship)
##   - normalized: (x or "").lower() == "true" и Name-присваивание .lower() на входе → PASS
## @rationale R5 (Test Honesty, .kilo/rules/testing.md): детектор обязан ловить исходный
##            вход, сломавший гейт (node-lifecycle.sh TRAP 2026-08-03); контроль —
##            против ложных RED на легитимных нормализациях (dataflow T6).
## @changes 2026-08-13 | DevPlan 163 W-C C2 — Created
# endregion MODULE_CONTRACT

from __future__ import annotations

import logging

from core.internal.static.bool_string_literals import detect
from tests._conftest.ldd import ldd_trajectory

logger = logging.getLogger(__name__)


# 🧪 TRAP[TEST] · POSITIVE · synthetic `!= "False"` без нормализации детектится
# · Scenario: probe `if enabled != "False":` — NotEq + "False" литерал, левая часть Name
# ·   без .lower() и без предшествующего нормализующего присваивания → RED
# · Last fail: N/A (синтетический вариант, отсутствует в исходном гейте)
# · Remove if: гейт булевой нормализации отменяется
@ldd_trajectory
def test_bool_string_not_eq_false_detected(caplog, tmp_path) -> None:
    """Synthetic positive: `enabled != "False"` (NotEq-вариант) детектируется."""
    probe = tmp_path / "_probe_ne.py"
    probe.write_text(
        'def deploy(enabled):\n    if enabled != "False":\n        return "deploy"\n    return "skip"\n',
        encoding="utf-8",
    )
    findings = detect(tmp_path)
    hits = [f for f in findings if "_probe_ne" in f.file]
    assert hits, 'R5 FAIL: synthetic `!= "False"` comparison not detected'
    assert hits[0].rule == "bool-string-literals"
    logger.info("[IMP:9][test_bool_string] synthetic NotEq detected: %s", hits[0])


# 🧪 TRAP[TEST] · NEGATIVE (R5) · исходный вход T6 `enabled == "true"` детектится
# · Scenario: probe `if enabled == "true":` — точный вход, поймавший bug T6
# ·   (node_yaml CLI возвращал Python-bool "True", строгие сравнения ломались)
# · Last fail: deploy_orchestrator.py:309 enabled == "true" без видимой нормализации (T6)
# · Remove if: гейт булевой нормализации отменяется
@ldd_trajectory
def test_bool_string_negative_original_t6_input(caplog, tmp_path) -> None:
    """R5 negative: оригинальный вход T6 `if enabled == "true":` детектируется."""
    probe = tmp_path / "_probe_r5.py"
    probe.write_text(
        'def deploy(enabled):\n    if enabled == "true":\n        return "deploy"\n    return "skip"\n',
        encoding="utf-8",
    )
    findings = detect(tmp_path)
    hits = [f for f in findings if "_probe_r5" in f.file]
    assert hits, 'R5 FAIL: original T6 input `enabled == "true"` not detected'
    logger.info("[IMP:9][test_bool_string] R5 original T6 input detected: %s", hits[0])


# 🧪 TRAP[TEST] · CONTROL · нормализованные сравнения НЕ детектятся (dataflow T6)
# · Scenario: inline .lower() (`os.environ.get(...).lower() == "true"`) + Name,
# ·   нормализованный присваиванием .lower() на входе функции → 0 offenders
# · Last fail: N/A (control — против ложных RED на легитимных нормализациях)
# · Remove if: гейт булевой нормализации отменяется
@ldd_trajectory
def test_bool_string_normalized_not_flagged(caplog, tmp_path) -> None:
    """PASS-контроль: `.lower()`-нормализованные сравнения (inline + вход функции) не RED."""
    probe = tmp_path / "_probe_ok.py"
    probe.write_text(
        "import os\n"
        "\n"
        "def cleanup(tor_enabled):\n"
        '    tor_enabled = (tor_enabled or "").strip().lower()\n'
        '    if tor_enabled != "true":\n'
        '        return "strip"\n'
        '    return "keep"\n'
        "\n"
        "def check():\n"
        '    if os.environ.get("AUTO_RECONCILE", "false").lower() == "true":\n'
        "        return 1\n"
        "    return 0\n",
        encoding="utf-8",
    )
    findings = detect(tmp_path)
    hits = [f for f in findings if "_probe_ok" in f.file]
    assert not hits, f"PASS-control FAIL: normalized comparisons flagged: {hits}"
    logger.info("[IMP:9][test_bool_string] normalized comparisons (inline + entry-Normalized) not flagged")


# 🧪 TRAP[TEST] · POSITIVE · семантический allowlist устойчив к сдвигу строк (DevPlan 171 W3.8)
# · Scenario: allowlisted функция _parse_modules — сравнение в ней не RED даже при
# ·   сдвиге номера строки (ключ — (file, function), не rel:lineno)
# · Last fail: N/A (new — W3.8 замена per-line allowlist на per-function)
# · Remove if: allowlist-механизм детектора отменяется
@ldd_trajectory
def test_bool_string_allowlist_survives_line_shift(caplog, tmp_path) -> None:
    """Семантический allowlist: (file, function)-ключ не зависит от номера строки."""
    core_dir = tmp_path / "core"
    target = core_dir / "internal" / "bootstrap" / "deploy"
    target.mkdir(parents=True)
    probe = target / "deploy_orchestrator.py"
    # Перенос строки: нарушение на lineno 3 вместо 317 — per-line allowlist бы пропустил
    # только 317, семантический — любую строку внутри _parse_modules.
    probe.write_text(
        "def _parse_modules(node_yaml, _modules_dir, modules_filter):\n"
        "    raw = []\n"
        '    if raw == "true":\n'  # lineno 3 — сдвиг относительно канона
        "        return raw\n",
        encoding="utf-8",
    )
    findings = detect(tmp_path)
    hits = [f for f in findings if "deploy_orchestrator" in f.file]
    assert not hits, f"W3.8 FAIL: semantic allowlist did not cover shifted line: {hits}"
    logger.info("[IMP:9][test_bool_string] semantic allowlist covers shifted line (function key)")
