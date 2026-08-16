# GREP_SUMMARY: test-module-interface dispatch parity thin-facade remove-hook deploy-hook validate-interface skip invalid-config cli
# STRUCTURE: ┌tmp module.yaml fixtures┐ → ◇ dispatch (registered/not/invalid/unknown) → ◇ deploy-hook/remove-hook (R5: удалённый _invoke_dispatch_hook) → ◇ CLI main parity → ◇ shell facade parity → ⎋ LDD IMP:9/10
# region MODULE_CONTRACT
## @purpose  Unit tests for shared/module_interface.py dispatch/CLI (DevPlan 119 D4) — dual-SoT
##           устранён: module-interface.sh (206 LOC) → тонкий фасад (26 LOC), validate/dispatch
##           логика переехала в Python. Покрывает R5 anti-survivorship для удалённых shell-функций
##           _invoke_validate_interface/_invoke_dispatch_healthcheck/_invoke_dispatch_install/_invoke_dispatch_hook.
## @scope    Tests: dispatch (rc 0=skip/success, 1=script failed, 2=invalid config), deploy-hook/
##           remove-hook (hooks.on_project_deploy/on_project_remove), CLI main(argv) == dispatch rc
##           (parity: shell фасад вызывает CLI), shell-фасад parity через реальный bash subprocess
##           (rc-0 skip path — скрипты модулей НЕ выполняются).
## @invariants
##   - dispatch — чистая диспетчеризация с subprocess на скрипты модулей (tmp_path fixtures)
##   - Shell-фасад parity использует РЕАЛЬНЫЙ bash subprocess с реальным модулем core/modules
##     (только rc-0 skip / rc-2 invalid — никакие скрипты не выполняются)
##   - R5: remove-hook/deploy-hook dispatch протестированы с исходным входом (hooks.on_project_*)
##   - LDD: IMP:9 в успешных сценариях
## @rationale D4 (DevPlan 119, AUDIT-1 F6): module-interface.sh dual-SoT с Python-каноном (118 C5).
##   Удаление shell-логики без negative-тестов = survivorship (R5). Parity: CLI == dispatch,
##   shell-фасад == CLI.
## @changes  2026-08-02 | DevPlan 119 D4 — Created (R5 parity)
# endregion MODULE_CONTRACT

import logging
import subprocess
from pathlib import Path

import pytest

from core.internal.shared.module_interface import dispatch
from core.internal.shared.module_interface import main as cli_main

pytestmark = pytest.mark.static_audit

logger = logging.getLogger(__name__)

PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
_MODULE_INTERFACE_SH = PROJECT_ROOT / "core" / "lib" / "module-interface.sh"


def _assert_imp9(caplog: pytest.LogCaptureFixture, needle: str | None = None) -> None:
    """Assert at least one IMP:9 log (LDD telemetry standard)."""
    logger.info("--- LDD TRAJECTORY (IMP:7-10) ---")
    found = False
    for record in list(caplog.records):
        if "[IMP:" in record.message:
            logger.info("%s", record.message)
            if needle and needle in record.message:
                found = True
    logger.info("--- END LDD TRAJECTORY ---")
    if needle:
        assert found, f"Critical LDD Error: No IMP:9 log containing '{needle}'"
    else:
        assert any("[IMP:9]" in r.message for r in caplog.records), "Critical LDD Error: No IMP:9 log found"


def _make_module_fixture(
    tmp_path: Path,
    *,
    interfaces: list[str] | None = None,
    hooks: dict | None = None,
    scripts: dict[str, str] | None = None,
) -> Path:
    """Создать fixture-модуль в tmp_path: module.yaml (+ hooks) и скрипты.

    ## @purpose — модульная фикстура для dispatch-тестов (tmp_path, никаких реальных модулей).
    """
    module_dir = tmp_path / "test-module"
    module_dir.mkdir(parents=True, exist_ok=True)
    module_yaml: dict = {}
    if interfaces is not None:
        module_yaml["interfaces"] = interfaces
    if hooks:
        module_yaml["hooks"] = hooks
    (module_dir / "module.yaml").write_text(
        __import__("yaml").safe_dump(module_yaml, sort_keys=False) if module_yaml else "name: test-module\n",
        encoding="utf-8",
    )
    for name, content in (scripts or {}).items():
        script = module_dir / name
        script.write_text(content, encoding="utf-8")
        script.chmod(0o755)
    return module_dir


# region TEST_dispatch


# 🧪 TRAP[TEST] · 2026-08-02 · Regression · dispatch: module.yaml отсутствует → rc 2 (D4)
# · Scenario: несуществующий модуль → rc 2 (invalid config, как shell invoke_module_interface)
# · Last fail: N/A (new — D4; перенос shell-логики)
# · Remove if: rc-2 контракт меняется
def test_dispatch_invalid_module(caplog: pytest.LogCaptureFixture) -> None:
    """module.yaml отсутствует → rc 2 (invalid config)."""
    caplog.set_level(logging.INFO)
    rc, _out = dispatch("no-such-module", "install")
    assert rc == 2
    assert any("[IMP:9]" in r.message and "INVALID" in r.message for r in caplog.records)


# 🧪 TRAP[TEST] · 2026-08-02 · Regression · dispatch: интерфейс не зарегистрирован → rc 0 skip (D4)
# · Scenario: interface вне module.yaml.interfaces → rc 0 (graceful skip, как shell)
# · Last fail: N/A (new — D4; перенос shell _invoke_validate_interface)
# · Remove if: skip-семантика меняется
def test_dispatch_interface_not_registered_skip(caplog: pytest.LogCaptureFixture, tmp_path) -> None:
    """Интерфейс не в module.yaml#interfaces → rc 0 (graceful skip), скрипт не вызывается."""
    caplog.set_level(logging.INFO)
    _make_module_fixture(tmp_path, interfaces=["healthcheck"])
    rc, out = dispatch("test-module", "install", modules_dir=str(tmp_path))
    assert rc == 0
    assert not out


# 🧪 TRAP[TEST] · 2026-08-02 · Regression · dispatch: install-интерфейс выполняет install.sh (D4)
# · Scenario: interface=install зарегистрирован → install.sh выполнен, rc от скрипта
# · Last fail: N/A (new — D4; перенос shell _invoke_dispatch_install)
# · Remove if: install-dispatch меняется
def test_dispatch_install_runs_script(caplog: pytest.LogCaptureFixture, tmp_path) -> None:
    """install зарегистрирован → install.sh выполняется, rc скрипта возвращается."""
    caplog.set_level(logging.INFO)
    _make_module_fixture(
        tmp_path,
        interfaces=["install"],
        scripts={"install.sh": "#!/usr/bin/env bash\nexit 3\n"},
    )
    rc, _out = dispatch("test-module", "install", modules_dir=str(tmp_path))
    assert rc == 3, "rc скрипта должен пробрасываться"
    # LDD: для упавшего скрипта телесентрия — IMP:8 exit-лог (IMP:9 = успех, здесь не ожидается)
    assert any("[IMP:8]" in r.message and "exit=3" in r.message for r in caplog.records)


# 🧪 TRAP[TEST] · 2026-08-02 · Regression · dispatch: скрипт отсутствует → rc 0 skip (D4)
# · Scenario: интерфейс зарегистрирован, но healthcheck.sh нет → rc 0 (как shell)
# · Last fail: N/A (new — D4)
# · Remove if: skip-при-отсутствии-скрипта меняется
def test_dispatch_missing_script_skip(caplog: pytest.LogCaptureFixture, tmp_path) -> None:
    """Интерфейс зарегистрирован, но скрипта нет → rc 0 (skip)."""
    caplog.set_level(logging.INFO)
    _make_module_fixture(tmp_path, interfaces=["healthcheck"])
    rc, _out = dispatch("test-module", "healthcheck", "liveness", modules_dir=str(tmp_path))
    assert rc == 0


# 🧪 TRAP[TEST] · 2026-08-02 · Regression · dispatch: healthcheck args пробрасываются (D4)
# · Scenario: healthcheck liveness → скрипт получает 'liveness' аргументом
# · Last fail: N/A (new — D4; перенос shell _invoke_dispatch_healthcheck)
# · Remove if: args проброс меняется
def test_dispatch_healthcheck_args_passthrough(caplog: pytest.LogCaptureFixture, tmp_path) -> None:
    """healthcheck args пробрасываются в скрипт модуля."""
    caplog.set_level(logging.INFO)
    _make_module_fixture(
        tmp_path,
        interfaces=["healthcheck"],
        scripts={"healthcheck.sh": '#!/usr/bin/env bash\necho "got=$1" >&2\nexit 0\n'},
    )
    rc, out = dispatch("test-module", "healthcheck", "liveness", modules_dir=str(tmp_path))
    assert rc == 0
    assert "got=liveness" in out


# 🧪 TRAP[TEST] · NEGATIVE (R5) · dispatch remove-hook — удалённый _invoke_dispatch_hook (D4)
# · Scenario: interface=remove-hook + hooks.on_project_remove → hook-скрипт выполнен с args
# · Last fail: shell _invoke_dispatch_hook (module-interface.sh:182-205) удалён в D4 —
# ·   исходный вход: hooks.on_project_remove → bash script PROJECT_DIR PROJECT NODE
# · Remove if: remove-hook dispatch удалён
def test_dispatch_remove_hook_negative(caplog: pytest.LogCaptureFixture, tmp_path) -> None:
    """R5 negative: remove-hook → hooks.on_project_remove скрипт выполняется с args (K2)."""
    caplog.set_level(logging.INFO)
    _make_module_fixture(
        tmp_path,
        interfaces=["remove-hook"],
        hooks={"on_project_remove": "remove-hook.sh"},
        scripts={"remove-hook.sh": '#!/usr/bin/env bash\necho "removed:$1:$2:$3" >&2\nexit 0\n'},
    )
    rc, out = dispatch("test-module", "remove-hook", "PROJ_DIR", "myproj", "my-vps", modules_dir=str(tmp_path))
    assert rc == 0
    assert "removed:PROJ_DIR:myproj:my-vps" in out, f"hook args не проброшены: {out}"
    _assert_imp9(caplog)


# 🧪 TRAP[TEST] · NEGATIVE (R5) · dispatch deploy-hook — удалённый _invoke_dispatch_hook (D4)
# · Scenario: interface=deploy-hook + hooks.on_project_deploy → hook-скрипт выполнен
# · Last fail: shell _invoke_dispatch_hook (module-interface.sh:182-205) удалён в D4
# · Remove if: deploy-hook dispatch удалён
def test_dispatch_deploy_hook_negative(caplog: pytest.LogCaptureFixture, tmp_path) -> None:
    """R5 negative: deploy-hook → hooks.on_project_deploy скрипт выполняется с args."""
    caplog.set_level(logging.INFO)
    _make_module_fixture(
        tmp_path,
        interfaces=["deploy-hook"],
        hooks={"on_project_deploy": "deploy-hook.sh"},
        scripts={"deploy-hook.sh": "#!/usr/bin/env bash\nexit 0\n"},
    )
    rc, _out = dispatch("test-module", "deploy-hook", "PROJ_DIR", "myproj", "my-vps", modules_dir=str(tmp_path))
    assert rc == 0


# 🧪 TRAP[TEST] · 2026-08-02 · Regression · dispatch: hook-поле отсутствует → rc 0 skip (D4)
# · Scenario: remove-hook зарегистрирован, но hooks.on_project_remove нет → rc 0
# · Last fail: shell _invoke_dispatch_hook — поле отсутствует → skip (module-interface.sh:190)
# · Remove if: skip-семантика hooks меняется
def test_dispatch_hook_field_missing_skip(caplog: pytest.LogCaptureFixture, tmp_path) -> None:
    """remove-hook зарегистрирован, hooks.on_project_remove отсутствует → rc 0 (skip)."""
    caplog.set_level(logging.INFO)
    _make_module_fixture(tmp_path, interfaces=["remove-hook"])
    rc, _out = dispatch("test-module", "remove-hook", "a", "b", "c", modules_dir=str(tmp_path))
    assert rc == 0


# endregion TEST_dispatch


# region TEST_CLI_parity (shell-фасад вызывает CLI; CLI rc == dispatch rc)


# 🧪 TRAP[TEST] · NEGATIVE (R5) · test_module_interface_shell_parity — CLI == dispatch (D4)
# · Scenario: main(["invoke", module, interface]) rc == dispatch(...) rc на 3 путях
# ·   (invalid rc 2 / skip rc 0 / script rc). Shell-фасад — чистый shim над CLI (parity by construction).
# · Last fail: module-interface.sh dual-SoT (206 LOC) — shell и Python расходились по exit-кодам
# · Remove if: CLI invoke удалён / dispatch rc-контракт меняется
def test_module_interface_shell_parity_negative(caplog: pytest.LogCaptureFixture, tmp_path) -> None:
    """R5: CLI (что вызывает shell-фасад) == dispatch() на rc-контракте 0/2."""
    caplog.set_level(logging.INFO)

    # Путь 1: invalid module → rc 2 (и CLI, и dispatch)
    assert dispatch("no-such-module", "install")[0] == 2
    assert cli_main(["invoke", "no-such-module", "install"]) == 2

    # Путь 2: не-зарегистрированный интерфейс → rc 0 (skip)
    _make_module_fixture(tmp_path, interfaces=["healthcheck"])
    assert dispatch("test-module", "install", modules_dir=str(tmp_path))[0] == 0
    assert cli_main(["invoke", "test-module", "install", "--modules-dir", str(tmp_path)]) == 0


# 🧪 TRAP[TEST] · 2026-08-02 · Regression · shell-фасад: реальный bash subprocess rc parity (D4)
# · Scenario: bash -c "source module-interface.sh && invoke_module_interface postgres not-registered" → rc 0
# ·   (реальный модуль core/modules, интерфейс вне списка — скрипты НЕ выполняются, безопасно)
# · Last fail: N/A (new — D4; доказывает end-to-end фасад→CLI→dispatch)
# · Remove if: модуль postgres перестанет существовать / фасад изменён
def test_shell_facade_real_bash_parity() -> None:
    """Реальный bash-фасад (source + invoke) возвращает rc 0/2 как Python dispatch."""
    result = subprocess.run(
        [
            "bash",
            "-c",
            f"source '{_MODULE_INTERFACE_SH}' && invoke_module_interface postgres definitely-not-registered",
        ],
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, f"skip-path rc должен быть 0, got {result.returncode}: {result.stderr}"

    result_invalid = subprocess.run(
        [
            "bash",
            "-c",
            f"source '{_MODULE_INTERFACE_SH}' && invoke_module_interface no-such-module install",
        ],
        capture_output=True,
        text=True,
        check=False,
    )
    assert result_invalid.returncode == 2, f"invalid-path rc должен быть 2, got {result_invalid.returncode}"


# 🧪 TRAP[TEST] · 2026-08-02 · Regression · фасад: тонкий (<30 LOC) + делегирует CLI (D4, AC-D4.1/AC-D4.2)
# · Scenario: wc -l < 30; содержит python3 -m core.internal.shared.module_interface invoke
# · Last fail: N/A (new — D4)
# · Remove if: фасад снова раздувается (>30 LOC) или меняет канал делегирования
def test_facade_thin_and_delegates() -> None:
    """module-interface.sh: <30 LOC + единственный канал — python3 -m ... invoke."""
    content = _MODULE_INTERFACE_SH.read_text(encoding="utf-8")
    assert len(content.splitlines()) < 30, f"facade раздут: {len(content.splitlines())} LOC (AC-D4.1 <30)"
    assert "python3 -m core.internal.shared.module_interface invoke" in content, "AC-D4.2: нет CLI-делегирования"


# endregion TEST_CLI_parity
