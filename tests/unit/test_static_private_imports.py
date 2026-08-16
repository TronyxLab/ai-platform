"""Static layer: private-imports detector tests (DevPlan 163 W-C C3).

# GREP_SUMMARY: test-static private-imports underscore-import private-api ast import-map R5 U-07 SRP
# STRUCTURE: ▶ synthetic from X import _name → RED | ▶ synthetic X._attr на импортированный модуль
#            → RED | ▶ R5-оригинал U-07 (from core.x import _helper) → RED | ▶ control: stdlib
#            os._exit + from X import name as _alias → PASS → ⎋
"""
# region MODULE_CONTRACT
## @purpose  R5-пары детектора private_imports (DevPlan 163 W-C C3): позитивные тесты на
##           синтетические нарушения ((a) from X import _name, (b) X._attr на импортированный
##           модуль), R5-негатив на ОРИГИНАЛЬНЫЙ вход гейта (U-07: приватный импорт в core/),
##           PASS-контроль (stdlib os._exit — легитимный приватный stdlib API; приватный
##           алиас ПУБЛИЧНОЙ сущности — легитимен).
## @scope    Native imports; probe-файлы в tmp_path (для деревьев без core/ детектор
##           сканирует root.rglob("*.py")).
## @invariants
##   - from X import _name (без alias) → RED
##   - X._attr на импортированный не-stdlib модуль → RED
##   - stdlib модули (os._exit) → PASS; from X import name as _alias → PASS
##   - Allowlist пуст
## @rationale R5 anti-survivorship (U-07, B9 T6.1): приватные имена остаются внутри
##            home-модуля; публичные API — через публичные имена + __init__.py-экспорт.
## @changes 2026-08-13 | DevPlan 163 W-C C3 — Created
# endregion MODULE_CONTRACT

from __future__ import annotations

import logging

from core.internal.static.private_imports import detect
from tests._conftest.ldd import ldd_trajectory

logger = logging.getLogger(__name__)


# 🧪 TRAP[TEST] · POSITIVE · synthetic from X import _name → RED
# · Scenario: probe `from core.helper import _internal` — from-import приватного имени
# ·   без публичного алиаса (класс (a) гейта B9 T6.1) → RED
# · Last fail: N/A (синтетический вариант)
# · Remove if: private-import гейт отменяется
@ldd_trajectory
def test_private_imports_from_import_private_name(caplog, tmp_path) -> None:
    """Synthetic positive: from X import _name детектируется."""
    probe = tmp_path / "_probe_from.py"
    probe.write_text("from core.helper import _internal\n", encoding="utf-8")
    findings = detect(tmp_path)
    hits = [f for f in findings if "_probe_from" in f.file]
    assert hits, "R5 FAIL: from X import _name not detected"
    assert "from-import" in hits[0].message
    logger.info("[IMP:9][test_private_imports] from-import _name RED: %s", hits[0])


# 🧪 TRAP[TEST] · POSITIVE · synthetic X._attr на импортированный модуль → RED
# · Scenario: probe `import core.helper as ch\nch._internal()` — attribute-доступ
# ·   к приватному имени импортированного модуля (класс (b) гейта B9 T6.1) → RED
# · Last fail: N/A (синтетический вариант)
# · Remove if: private-import гейт отменяется
@ldd_trajectory
def test_private_imports_attribute_access_on_imported_module(caplog, tmp_path) -> None:
    """Synthetic positive: X._attr на импортированный модуль детектируется."""
    probe = tmp_path / "_probe_attr.py"
    probe.write_text("import core.helper as ch\nch._internal()\n", encoding="utf-8")
    findings = detect(tmp_path)
    hits = [f for f in findings if "_probe_attr" in f.file]
    assert hits, "R5 FAIL: X._attr on imported module not detected"
    assert "attribute-доступ" in hits[0].message
    logger.info("[IMP:9][test_private_imports] attribute-access RED: %s", hits[0])


# 🧪 TRAP[TEST] · NEGATIVE (R5) · оригинал U-07: приватный межмодульный импорт в core/ → RED
# · Scenario: probe core/internal/probe.py с `from core.shared._secrets import _parse` —
# ·   точный класс U-07 (приватный межмодульный импорт, SRP-граница B9 T6.1)
# · Last fail: DevPlan 116 B9 T6.1 — приватные имена использовались между модулями
# · Remove if: private-import гейт отменяется
@ldd_trajectory
def test_private_imports_negative_original_u07_input(caplog, tmp_path) -> None:
    """R5 negative: оригинальный вход U-07 — приватный межмодульный импорт в core/."""
    probe_dir = tmp_path / "core" / "internal"
    probe_dir.mkdir(parents=True)
    probe = probe_dir / "probe.py"
    probe.write_text("from core.shared._secrets import _parse\n", encoding="utf-8")
    findings = detect(tmp_path)
    hits = [f for f in findings if "probe.py" in f.file]
    assert hits, "R5 FAIL: private cross-module import (U-07 original class) not detected"
    logger.info("[IMP:9][test_private_imports] R5 U-07 private import RED: %s", hits[0])


# 🧪 TRAP[TEST] · CONTROL · stdlib os._exit + приватный алиас публичной сущности → PASS
# · Scenario: probe с `os._exit(0)` (stdlib-приватный API) и `from x import name as _alias`
# ·   (публичная сущность, приватный алиас) → 0 RED (исключения гейта B9 T6.1)
# · Last fail: N/A (control — легитимные stdlib/alias-паттерны)
# · Remove if: private-import гейт отменяется
@ldd_trajectory
def test_private_imports_stdlib_and_public_alias_not_flagged(caplog, tmp_path) -> None:
    """PASS-контроль: stdlib os._exit + приватный алиас публичной сущности не RED."""
    probe = tmp_path / "_probe_ok.py"
    probe.write_text(
        "import os\nfrom core.public import name as _alias\nos._exit(0)\n",
        encoding="utf-8",
    )
    findings = detect(tmp_path)
    hits = [f for f in findings if "_probe_ok" in f.file]
    assert not hits, f"PASS-control FAIL: stdlib/alias patterns flagged: {hits}"
    logger.info("[IMP:9][test_private_imports] stdlib os._exit + public alias not flagged")
