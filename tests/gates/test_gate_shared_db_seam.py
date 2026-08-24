# GREP_SUMMARY: gate shared-db-seam postgres deploy-hook module-interface post-deploy-chain registration TEST-18 REF-0002 structural
# STRUCTURE: ▶ module.yaml registration → ⊕ wrapper delegating to python impl → ⊕ post_deploy_chain invokes deploy-hook via module_interface → ⊕ hook entry smoke (missing args rc=0) → ⎋ verdict
# region MODULE_CONTRACT
## @purpose  TEST-18 port (REF-0002, 11-DevPlan Волна 1): shared-db seam структурный гейт —
##           цепочка «post-deploy chain → module_interface.invoke(postgres, deploy-hook) →
##           on_project_deploy.py» существует и согласована БЕЗ реального postgres.
## @scope    Structural checks: (1) module.yaml postgres регистрирует interfaces:deploy-hook +
##           hooks.on_project_deploy; (2) wrapper .sh делегирует python-реализации;
##           (3) post_deploy_chain содержит invoke deploy-hook через module_interface;
##           (4) main() хука возвращает 0 при missing args (smoke канала вызова).
##           Полный e2e с реальным postgres — tests/e2e/test_shared_db_access.py
##           (local_stack marker, вне CI по дизайну).
## @invariants
##   - R5-negative: отсутствие регистрации/делегирования/invocation = RED (каждый детектор
##     бьёт в конкретный разрыв seam'а)
##   - 0 monkeypatch subprocess; хук вызывается нативно (main(argv=[]))
##   - @pytest.mark.gate + manifest trinity
## @rationale REF-0002: хук обязан выполняться на каждом деплое needs.database проекта.
##            Тихий skip dispatch (interfaces без deploy-hook) или потеря wrapper'а ломает
##            канал молча — гейт фиксирует все три звена цепочки структурно.
## @changes 2026-08-24 | Created (REF-0002 W1, TEST-18)
# endregion MODULE_CONTRACT

from __future__ import annotations

import logging
import stat

import pytest
import yaml

from tests.helpers.gate_helpers import repo_root

logger = logging.getLogger(__name__)

_ROOT = repo_root()
_MODULE_DIR = _ROOT / "core" / "modules" / "postgres"
_HOOK_PY = _MODULE_DIR / "hooks" / "on_project_deploy.py"
_WRAPPER_SH = _MODULE_DIR / "hooks" / "on_project_deploy.sh"
_CHAIN_PY = _ROOT / "core" / "internal" / "deploy" / "hooks" / "post_deploy_chain.py"

# ═══════════════════════════════════════════════════════════════════
# region Tests: shared-db seam (REF-0002 / TEST-18)
# ═══════════════════════════════════════════════════════════════════


def _load_module_yaml() -> dict:
    with (_MODULE_DIR / "module.yaml").open(encoding="utf-8") as f:
        return yaml.safe_load(f) or {}


# 🧪 TRAP[TEST] · Gate · REGRESSION · REF-0002 deploy-hook регистрация
# · Scenario: module.yaml postgres содержит interfaces:[...deploy-hook] И hooks.on_project_deploy
# ·   (dispatch делает тихий rc=0-skip без пары — ловушка, закрывавшая канал целиком)
# · Last fail: N/A (регистрация добавлена REF-0002 В0; гейт защищает от регрессии)
# · Remove if: deploy-hook механизм заменён другим контрактом (синхронно с module_interface)
@pytest.mark.gate
def test_postgres_registers_deploy_hook_pair(caplog) -> None:
    """module.yaml: interfaces содержит deploy-hook И hooks.on_project_deploy зарегистрирован."""
    caplog.set_level(logging.INFO)
    data = _load_module_yaml()

    interfaces = data.get("interfaces") or []
    assert "deploy-hook" in interfaces, (
        f"postgres module.yaml#interfaces обязан содержать 'deploy-hook' (без него dispatch тихо skip): {interfaces}"
    )
    hooks = (data.get("hooks") or {}).get("on_project_deploy", "")
    assert hooks, "postgres module.yaml#hooks.on_project_deploy отсутствует — канал не зарегистрирован"
    assert "on_project_deploy.sh" in str(hooks), f"хук обязан ссылаться на wrapper .sh: {hooks}"

    logger.critical("[IMP:9][gate] postgres deploy-hook pair registered (interfaces + hooks)")


# 🧪 TRAP[TEST] · Gate · NEGATIVE-cover · wrapper delegation + executable bit
# · Scenario: wrapper существует, +x, и делегирует python-реализации (exec python3 …py)
# · Last fail: N/A
# · Remove if: dispatch начнёт вызывать .py напрямую (wrapper упразднён каноном)
@pytest.mark.gate
def test_hook_wrapper_delegates_to_python_impl(caplog) -> None:
    """Wrapper .sh существует, executable, и содержит exec-делегацию в on_project_deploy.py."""
    caplog.set_level(logging.INFO)
    assert _WRAPPER_SH.is_file(), f"отсутствует {_WRAPPER_SH} — dispatch вызывает bash-скрипт"
    mode = _WRAPPER_SH.stat().st_mode
    assert mode & stat.S_IXUSR, "wrapper обязан быть executable (+x)"

    wrapper_text = _WRAPPER_SH.read_text(encoding="utf-8")
    assert "python3" in wrapper_text and "on_project_deploy.py" in wrapper_text, (
        "wrapper обязан делегировать python-реализации (thin-facade контракт)"
    )
    assert _HOOK_PY.is_file(), f"отсутствует python-реализация {_HOOK_PY}"

    logger.critical("[IMP:9][gate] wrapper delegates to python impl, executable bit set")


# 🧪 TRAP[TEST] · Gate · NEGATIVE-cover · chain invocation seam
# · Scenario: post_deploy_chain вызывает module deploy-hooks через module_interface
# ·   invoke c 'deploy-hook' (registry-driven) — разрыв вызова = хук никогда не исполняется
# · Last fail: N/A
# · Remove if: chain переключена на иной механизм вызова модульных хуков
@pytest.mark.gate
def test_post_deploy_chain_invokes_deploy_hooks_via_module_interface(caplog) -> None:
    """post_deploy_chain.py содержит invocation deploy-hook через module_interface."""
    caplog.set_level(logging.INFO)
    chain_text = _CHAIN_PY.read_text(encoding="utf-8")

    assert "deploy-hook" in chain_text, (
        "post_deploy_chain не вызывает 'deploy-hook' — хук postgres никогда не исполнится"
    )
    assert "module_interface" in chain_text, (
        "вызов deploy-hook обязан идти через shared/module_interface (cross-layer контракт)"
    )

    logger.critical("[IMP:9][gate] chain→module_interface→deploy-hook seam present")


# 🧪 TRAP[TEST] · Gate · Smoke · hook entry contract
# · Scenario: main(argv=[]) → rc=0 (missing args — skip, не crash) — контракт entrypoint'а,
# ·   который dispatch вызывает на КАЖДОМ деплое
# · Last fail: N/A
# · Remove if: сигнатура main()/аргументы хука меняются
@pytest.mark.gate
def test_hook_entry_missing_args_returns_zero(caplog) -> None:
    """main() без аргументов → rc=0 (skip), stderr-лог, никакого traceback."""
    caplog.set_level(logging.INFO)
    from core.modules.postgres.hooks import on_project_deploy

    rc = on_project_deploy.main(argv=[], env={"POSTGRES_PASSWORD": "ci-placeholder"})
    assert rc == 0, "missing args обязаны давать rc=0 (skip-семантика хука)"

    logger.critical("[IMP:9][gate] hook entry missing-args smoke: rc=0")


# endregion Tests: shared-db seam
