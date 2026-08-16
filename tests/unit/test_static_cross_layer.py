"""Static layer: cross-layer detector tests (DevPlan 163 W-C C2).

# GREP_SUMMARY: test-static cross-layer dotted-imports python3-m modules-entrypoints deploy-bootstrap R5 B11 direction-allowlist
# STRUCTURE: ▶ probe modules→entrypoints dotted (synthetic) → RED | ▶ probe modules→internal
#            dotted (R5 B11-оригинал) → RED | ▶ probe python3 -m в modules sh (R5) → RED
#            → ▶ probe deploy→bootstrap → RED | ▶ allowlist postgres-hook scope → PASS → ⎋
"""
# region MODULE_CONTRACT
## @purpose  R5-пары детектора cross_layer (DevPlan 163 W-C C2): позитивный тест на
##           синтетическое нарушение (modules→entrypoints dotted-импорт), R5-негативы на
##           ОРИГИНАЛЬНЫЕ входы B11 (dotted py import + python3 -m из modules, U-09),
##           deploy→bootstrap запрет (W5/G3), PASS-контроль direction-allowlist
##           (modules→internal под scope postgres/hooks/).
## @scope    Native imports; probe-файлы под tmp_path/core/<layer>/ (Zero Hardcode Rule);
##           allowlist scope-префикс core/modules/postgres/hooks/ проверяется на tmp-дереве.
## @invariants
##   - modules→entrypoints dotted → RED (entrypoints ∉ {lib, templates})
##   - modules→internal dotted → RED вне allowlist-scope (оригинальный вход B11)
##   - python3 -m core.internal.* в modules sh → RED (B11, U-09)
##   - core/internal/deploy/* → core.internal.bootstrap.* → RED (W5/G3)
##   - modules→internal под scope core/modules/postgres/hooks/ → allowlisted (S7)
## @rationale R5 anti-survivorship: старый гейт был слеп к dotted/python3 -m паттернам
##            (U-09) — детектор обязан их ловить; allowlist-контроль — против ложных RED
##            на легитимном postgres-hook (D1 by design).
## @changes 2026-08-13 | DevPlan 163 W-C C2 — Created
# endregion MODULE_CONTRACT

from __future__ import annotations

import logging

from core.internal.static.cross_layer import detect
from tests._conftest.ldd import ldd_trajectory

logger = logging.getLogger(__name__)


# 🧪 TRAP[TEST] · POSITIVE · synthetic modules→entrypoints dotted import → RED
# · Scenario: `from core.entrypoints.deploy import ...` в modules-фикстуре — направление
# ·   modules→entrypoints запрещено (_IMPORT_RULES modules = {lib, templates})
# · Last fail: N/A (синтетический вариант, отсутствует в исходных негативах)
# · Remove if: cross-layer gate superseded
@ldd_trajectory
def test_cross_layer_modules_to_entrypoints_detected(caplog, tmp_path) -> None:
    """Synthetic positive: dotted import modules→entrypoints детектируется."""
    probe_dir = tmp_path / "core" / "modules" / "_probe_ep_tmp"
    probe_dir.mkdir(parents=True)
    probe = probe_dir / "test_negative.py"
    probe.write_text("from core.entrypoints.deploy import old_deploy\n", encoding="utf-8")
    findings = detect(tmp_path)
    hits = [f for f in findings if "_probe_ep_tmp" in f.file]
    assert hits, "R5 FAIL: modules→entrypoints dotted import not detected"
    assert "[modules→entrypoints]" in hits[0].message
    logger.info("[IMP:9][test_cross_layer] modules→entrypoints RED: %s", hits[0])


# 🧪 TRAP[TEST] · NEGATIVE (R5) · исходный вход B11 dotted py import из modules → RED
# · Scenario: `from core.internal.shared.telegram_notifier import send_telegram` в modules —
# ·   точный вход U-09 (старый гейт — 36 passed при 4 реальных py-нарушениях, слепота к dotted)
# · Last fail: old gate — слепота к dotted-импортам (DevPlan 116 B11 T1, U-09)
# · Remove if: cross-layer gate superseded
@ldd_trajectory
def test_cross_layer_negative_dotted_py_in_modules(caplog, tmp_path) -> None:
    """R5 negative: оригинальный вход B11 — dotted py import в modules → RED."""
    probe_dir = tmp_path / "core" / "modules" / "_b11_negative_py_tmp"
    probe_dir.mkdir(parents=True)
    probe = probe_dir / "test_negative.py"
    probe.write_text("from core.internal.shared.telegram_notifier import send_telegram\n", encoding="utf-8")
    findings = detect(tmp_path)
    hits = [f for f in findings if "_b11_negative_py_tmp" in f.file]
    assert hits, "R5 FAIL: dotted py import (B11 original input) not detected"
    assert "[modules→internal]" in hits[0].message
    logger.info("[IMP:9][test_cross_layer] R5 dotted py RED: %s", hits[0])


# 🧪 TRAP[TEST] · NEGATIVE (R5) · исходный вход B11 python3 -m из modules sh → RED
# · Scenario: `python3 -m core.internal.shared.node_yaml` в sh-фикстуре modules —
# ·   точный вход U-09 (disk-monitor/postgres-hook жили незамеченными)
# · Last fail: old gate — слепота к python3 -m (DevPlan 116 B11 T1)
# · Remove if: cross-layer gate superseded
@ldd_trajectory
def test_cross_layer_negative_python3_m_in_modules(caplog, tmp_path) -> None:
    """R5 negative: оригинальный вход B11 — python3 -m core.internal.* в modules sh → RED."""
    probe_dir = tmp_path / "core" / "modules" / "_b11_negative_sh_tmp"
    probe_dir.mkdir(parents=True)
    probe = probe_dir / "test_negative.sh"
    probe.write_text(
        "#!/usr/bin/env bash\n"
        'db_name="$(python3 -m core.internal.shared.node_yaml '
        '--file "${ai_yaml}" --get needs.database)"\n',
        encoding="utf-8",
    )
    findings = detect(tmp_path)
    hits = [f for f in findings if "_b11_negative_sh_tmp" in f.file]
    assert hits, "R5 FAIL: python3 -m dotted (B11 original input) not detected"
    assert "[modules→internal]" in hits[0].message
    logger.info("[IMP:9][test_cross_layer] R5 python3 -m RED: %s", hits[0])


# 🧪 TRAP[TEST] · POSITIVE · deploy→bootstrap dotted import → RED (W5/G3)
# · Scenario: `import core.internal.bootstrap.state_machine` в core/internal/deploy/ —
# ·   направление deploy→bootstrap запрещено (DevPlan 119 G3, core/AGENTS.md)
# · Last fail: N/A (контракт W5 — изоляция фаз bootstrap-конвейера)
# · Remove if: запрет deploy→bootstrap отменяется
@ldd_trajectory
def test_cross_layer_deploy_to_bootstrap_detected(caplog, tmp_path) -> None:
    """Positive: dotted import deploy→bootstrap детектируется (W5/G3)."""
    probe_dir = tmp_path / "core" / "internal" / "deploy"
    probe_dir.mkdir(parents=True)
    probe = probe_dir / "_probe.py"
    probe.write_text("import core.internal.bootstrap.state_machine\n", encoding="utf-8")
    findings = detect(tmp_path)
    hits = [f for f in findings if "internal/deploy/_probe" in f.file]
    assert hits, "R5 FAIL: deploy→bootstrap dotted import not detected"
    assert "[deploy→bootstrap]" in hits[0].message
    logger.info("[IMP:9][test_cross_layer] deploy→bootstrap RED: %s", hits[0])


# 🧪 TRAP[TEST] · CONTROL · modules→internal под scope postgres/hooks/ → allowlisted
# · Scenario: probe core/modules/postgres/hooks/on_project_deploy.py c
# ·   `from core.internal.shared.node_yaml import ...` — направление modules→internal
# ·   под scope-префиксом allowlist (S7, D1 by design) → PASS
# · Last fail: N/A (control — allowlist не должен давать ложных RED)
# · Remove if: cross-layer gate superseded
@ldd_trajectory
def test_cross_layer_allowlist_scope_postgres_hook(caplog, tmp_path) -> None:
    """PASS-контроль: modules→internal под scope core/modules/postgres/hooks/ не RED."""
    probe_dir = tmp_path / "core" / "modules" / "postgres" / "hooks"
    probe_dir.mkdir(parents=True)
    probe = probe_dir / "on_project_deploy.py"
    probe.write_text("from core.internal.shared.node_yaml import resolve\n", encoding="utf-8")
    findings = detect(tmp_path)
    hits = [f for f in findings if "postgres/hooks" in f.file]
    assert not hits, f"PASS-control FAIL: allowlisted postgres-hook flagged: {hits}"
    logger.info("[IMP:9][test_cross_layer] postgres-hook allowlisted (direction modules→internal, scope hooks/)")
