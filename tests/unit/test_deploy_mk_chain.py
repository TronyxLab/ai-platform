#!/usr/bin/env python3
# GREP_SUMMARY: test-deploy-mk-chain, deploy.mk, deploy-project, deliver, --host, skip-verify, --scp, D3, NODE-resolve
# STRUCTURE: ▶ 3 scenarios ┌deploy.mk рецепт (deliver+--host, 0 мёртвых флагов) + deliver CLI JSON + negative no-host┐ → ○ caplog LDD IMP:9 → ⊕ TRAP[TEST] → ⎋
# region MODULE_CONTRACT
## @purpose  Unit tests for DevPlan 116 B1 T5 (D3) — make deploy-project цепочка: рецепт deploy.mk
##           использует `deliver` + `--host` (НЕ --skip-verify/--scp); deliver CLI пробрасывает
##           JSON-результат VPS в stdout и exit по нему; NODE без host-резолва → fail-fast.
## @scope    Tests: парсинг makefiles/deploy.mk (статический рецепт-анализ) + orchestrator_cli
##           deliver (_deliver) с monkeypatched ForcedCommandChannel.
## @invariants
##   - No Docker, no SSH, no subprocess (deliver-канал мокается)
##   - LDD: IMP:9 лог на deliver start
##   - R5 anti-survivorship: --skip-verify/--scp отсутствуют в рецепте
## @rationale  DevPlan 116 B1 T5 criteria: make deploy-project не содержит мёртвых флагов;
##             host резолвится через extract_node_host; deliver не выполняет локальный compose.
## @changes    2026-08-01 | Created (DevPlan 116 B1 T5)
# endregion MODULE_CONTRACT

import json
import logging
import os
import sys
from pathlib import Path

import pytest

from core.internal.deploy.orchestrator_cli import _deliver, build_parser

logger = logging.getLogger(__name__)

_REPO_ROOT = Path(__file__).resolve().parent.parent.parent
_DEPLOY_MK = _REPO_ROOT / "makefiles" / "deploy.mk"
_MODULES_MK = _REPO_ROOT / "makefiles" / "modules.mk"
_MANIFEST_MK = _REPO_ROOT / "makefiles" / "manifest.mk"
_COMPOSE_WRAPPER = _REPO_ROOT / "core" / "entrypoints" / "compose-wrapper.sh"
_MONITORING_RENDERER = _REPO_ROOT / "core" / "internal" / "monitoring_config_renderer.py"


def _recipe_from_mk(mk_path: Path, target: str) -> str:
    """Извлечь рецепт таргета из makefile-файла (от 'target:' до следующего неотступленного '## ').

    ## @purpose — Статический парсинг make-рецепта для guard-assert'ов (паттерн _deploy_project_recipe).
    ## @io — ⇥ mk_path: Path, target: str → ⎋ str (рецепт целиком)
    ## @complexity — O(L) где L = строк файла
    """
    content = mk_path.read_text()
    lines = content.splitlines()
    start = next(i for i, line in enumerate(lines) if line.strip() == f"{target}:")
    end = len(lines)
    for i in range(start + 1, len(lines)):
        stripped = lines[i].strip()
        if stripped.startswith("## ") and not lines[i].startswith("\t"):
            end = i
            break
    return "\n".join(lines[start:end])


def _assert_imp9_logged(caplog: pytest.LogCaptureFixture) -> None:
    """Print IMP:7-10 trajectory and assert at least one IMP:9 log present."""
    found_imp9 = False
    print("--- LDD TRAJECTORY (IMP:7-10) ---")
    for record in caplog.records:
        if "[IMP:" in record.message:
            imp_level = int(record.message.split("[IMP:")[1].split("]")[0])
            if imp_level >= 7:
                print(record.message)
            if imp_level >= 9:
                found_imp9 = True
    print("--- END LDD TRAJECTORY ---")
    assert found_imp9, "Critical LDD Error: No IMP:9 business logic log found"


def _deploy_project_recipe() -> str:
    """Извлечь рецепт таргета deploy-project из makefiles/deploy.mk.

    ## @purpose — Статический парсинг: срез от 'deploy-project:' до следующего
    ##            неотступленного '## ' комментария (начало следующего таргета).
    """
    content = _DEPLOY_MK.read_text()
    lines = content.splitlines()
    start = next(i for i, line in enumerate(lines) if line.strip() == "deploy-project:")
    end = len(lines)
    for i in range(start + 1, len(lines)):
        stripped = lines[i].strip()
        if stripped.startswith("## ") and not lines[i].startswith("\t"):
            end = i
            break
    return "\n".join(lines[start:end])


# region FUNC_test_deploy_mk_recipe_uses_deliver
## @purpose — Рецепт deploy-project использует `deliver` + `--host`; НЕ содержит --skip-verify/--scp (D3).
# 🧪 TRAP[TEST] · DevPlan 116 B1 T5 · D3 negative: мёртвые флаги удалены
# · Regression: --skip-verify/--scp возвращаются в deploy.mk (мёртвый код, D3)
# · Scenario: рецепт содержит 'deliver' и '--host'; не содержит '--skip-verify', '--scp', 'SKIP_VERIFY'
# · Last fail: legacy — deploy.mk:58,72-78 передавал --skip-verify (аргумента нет в CLI → fail)
# · Remove if: deploy-project снова получает флаги канала
def test_deploy_mk_recipe_uses_deliver() -> None:
    """Рецепт deploy-project: deliver + --host, 0 мёртвых флагов (D3)."""
    recipe = _deploy_project_recipe()

    assert "deliver" in recipe, "Рецепт должен использовать CLI subcommand deliver (T5)"
    assert "--host" in recipe, "Рецепт должен передавать --host (NODE→host резолв)"
    assert "--skip-verify" not in recipe, "--skip-verify удалён (D3)"
    assert "--scp" not in recipe, "--scp удалён (T5 — единый канал ForcedCommandChannel)"
    assert "SKIP_VERIFY" not in recipe, "SKIP_VERIFY переменная удалена (D3)"
    assert "extract_node_host" in recipe, "Рецепт должен резолвить host через extract_node_host"
    logger.info("[IMP:9][test][deploy_mk] Рецепт deploy-project чист: deliver + --host, 0 мёртвых флагов")


# endregion FUNC_test_deploy_mk_recipe_uses_deliver


# region FUNC_test_deliver_cli_forwards_vps_json
## @purpose — deliver CLI: monkeypatched ForcedCommandChannel._retry_deliver возвращает JSON-результат
##            VPS (DEPLOYED) → stdout пробрасывает JSON, exit 0.
# 🧪 TRAP[TEST] · DevPlan 116 B1 T5 · deliver JSON passthrough
# · Regression: deliver теряет JSON-результат VPS (exit не отражает статус receive)
# · Scenario: mock-канал stdout='{"status":"DEPLOYED",...}' → _deliver печатает JSON, rc 0
# · Last fail: N/A (new test)
# · Remove if: deliver CLI меняется
def test_deliver_cli_forwards_vps_json(monkeypatch, tmp_path, caplog: pytest.LogCaptureFixture, capsys) -> None:
    """deliver пробрасывает JSON VPS в stdout и exit по status (DEPLOYED → 0)."""
    caplog.set_level(logging.INFO)

    proj_dir = tmp_path / "myproj"
    proj_dir.mkdir()
    (proj_dir / "docker-compose.yml").write_text("services:\n  web:\n    image: nginx\n")
    (proj_dir / "ai-platform.yaml").write_text("name: myproj\n")

    vps_json = json.dumps(
        {"status": "DEPLOYED", "project": "myproj", "version": "abc123", "channel": "local", "error_info": None}
    )

    class _FakeChannel:
        def _retry_deliver(self, payload):
            from core.internal.deploy.channels import DeliveryResult

            return DeliveryResult(success=True, stdout=vps_json, exit_code=0)

    monkeypatch.setattr("core.internal.deploy.orchestrator_cli.ForcedCommandChannel", lambda: _FakeChannel())

    parser = build_parser()
    args = parser.parse_args(
        ["deliver", "--project", "myproj", "--version", "abc123", "--host", "1.2.3.4", "--project-dir", str(proj_dir)]
    )

    rc = _deliver(args)

    out = capsys.readouterr().out
    _assert_imp9_logged(caplog)
    payload = json.loads(out.strip())
    assert rc == 0
    assert payload["status"] == "DEPLOYED"
    assert payload["version"] == "abc123"
    assert payload["project"] == "myproj"


# endregion FUNC_test_deliver_cli_forwards_vps_json


# region FUNC_test_deliver_cli_vps_failed_exit_1
## @purpose — deliver: VPS receive FAILED → exit 1 (честный exit по статусу receive).
# 🧪 TRAP[TEST] · DevPlan 116 B1 T5 · deliver exit по VPS-статусу
# · Regression: deliver exit 0 несмотря на FAILED на VPS
# · Scenario: mock-канал stdout='{"status":"FAILED",...}' → _deliver rc 1
# · Last fail: N/A (new test)
# · Remove if: deliver CLI меняется
def test_deliver_cli_vps_failed_exit_1(monkeypatch, tmp_path, capsys) -> None:
    """deliver: VPS FAILED → exit 1."""
    proj_dir = tmp_path / "myproj"
    proj_dir.mkdir()
    (proj_dir / "docker-compose.yml").write_text("services:\n  web:\n    image: nginx\n")
    (proj_dir / "ai-platform.yaml").write_text("name: myproj\n")

    vps_json = json.dumps({"status": "FAILED", "project": "myproj", "error_info": "compose failed"})

    class _FakeChannel:
        def _retry_deliver(self, payload):
            from core.internal.deploy.channels import DeliveryResult

            return DeliveryResult(success=True, stdout=vps_json, exit_code=0)

    monkeypatch.setattr("core.internal.deploy.orchestrator_cli.ForcedCommandChannel", lambda: _FakeChannel())

    parser = build_parser()
    args = parser.parse_args(["deliver", "--project", "myproj", "--host", "1.2.3.4", "--project-dir", str(proj_dir)])

    rc = _deliver(args)

    out = capsys.readouterr().out
    payload = json.loads(out.strip())
    assert rc == 1, "VPS FAILED → deliver exit 1"
    assert payload["status"] == "FAILED"


# endregion FUNC_test_deliver_cli_vps_failed_exit_1


# region FUNC_test_deliver_cli_requires_host
## @purpose — deliver без --host → fail-fast (exit 1) — NODE→host резолв обязан в make-слое.
# 🧪 TRAP[TEST] · DevPlan 116 B1 T5 · negative: deliver требует host
# · Regression: deliver шлёт на пустой host (SCPChannel FAILED — «requires 'host'»)
# · Scenario: _deliver с --host "" → rc 1, JSON ERROR
# · Last fail: legacy — deploy-project не передавал host → SCPChannel всегда FAILED
# · Remove if: deliver CLI меняется
def test_deliver_cli_requires_host(tmp_path, capsys) -> None:
    """deliver без --host → exit 1 (fail-fast, NODE→host в make-слое)."""
    parser = build_parser()
    args = parser.parse_args(["deliver", "--project", "myproj", "--project-dir", str(tmp_path)])

    rc = _deliver(args)

    out = capsys.readouterr().out
    payload = json.loads(out.strip())
    assert rc == 1
    assert payload["status"] == "ERROR"
    assert "--host" in payload["error"]


# endregion FUNC_test_deliver_cli_requires_host


# ═══════════════════════════════════════════════════════════════════
# region Tests: D1/D2/D3 — up-safe / compose-wrapper / render-monitoring (DevPlan 136 W1 T1.10)
# ═══════════════════════════════════════════════════════════════════


# region FUNC_test_up_safe_empty_modules_passthrough_profiles
## @purpose — D2 (7a7537e): up-safe с ПУСТЫМ MODULES → COMPOSE_PROFILES из .env пробрасывается
##            (else-ветка БЕЗ COMPOSE_PROFILES= — compose читает .env). Раньше пустой MODULES
##            переопределял COMPOSE_PROFILES пустым → «no service selected».
# 🧪 TRAP[TEST] · 2026-08-05 · Regression · D2 — up-safe с пустым MODULES (7a7537e)
# · Scenario: рецепт up-safe содержит if [ -n "$(MODULES)" ]; else-ветка вызывает compose-wrapper БЕЗ COMPOSE_PROFILES
# · Last fail: 2026-08-04 — COMPOSE_PROFILES="$(MODULES)" безусловно → пусто → «no service selected»
# · Remove if: up-safe меняет профильную логику
def test_up_safe_empty_modules_passthrough_profiles() -> None:
    """D2: up-safe с пустым MODULES → COMPOSE_PROFILES из .env (else-ветка без принудительного пустого)."""
    recipe = _recipe_from_mk(_MODULES_MK, "up-safe")

    assert 'if [ -n "$(MODULES)" ]' in recipe, "D2: guard по непустому MODULES обязан присутствовать"
    assert recipe.count("COMPOSE_PROFILES=") == 1, (
        "D2: COMPOSE_PROFILES= ровно один раз (внутри if-ветки), иначе пустой MODULES ломает профили"
    )
    # else-ветка: compose-wrapper.sh up -d БЕЗ COMPOSE_PROFILES (passthrough .env)
    else_branch = recipe.split("else")[1].split("fi")[0]
    assert "COMPOSE_PROFILES" not in else_branch, "D2: else-ветка (пустой MODULES) НЕ должна форсить COMPOSE_PROFILES"
    assert "compose-wrapper.sh up -d" in else_branch, "D2: else-ветка обязана вызывать compose-wrapper"

    # R5 negative: старый безусловный паттерн (строка @COMPOSE_PROFILES=...) отсутствует
    assert "\n\t@COMPOSE_PROFILES=" not in recipe, "D2 negative: безусловный COMPOSE_PROFILES (старый паттерн) удалён"
    logger.info("[IMP:9][test][d2] up-safe: пустой MODULES → passthrough .env профилей")


# endregion FUNC_test_up_safe_empty_modules_passthrough_profiles


# region FUNC_test_compose_wrapper_exports_pythonpath
## @purpose — D1 (7a7537e): compose-wrapper.sh экспортирует PYTHONPATH с repo root — compose_preflight.py
##            (core.internal.* импорты) обязан работать при standalone-запуске из любого cwd.
# 🧪 TRAP[TEST] · 2026-08-05 · Regression · D1 — compose-wrapper PYTHONPATH export (7a7537e)
# · Scenario: compose-wrapper.sh содержит export PYTHONPATH=REPO_ROOT (SCRIPT_DIR/../..)
# · Last fail: 2026-08-04 — compose_preflight.py без PYTHONPATH → core.* импорт падал (make up-safe fail)
# · Remove if: compose_preflight.py перестаёт импортировать core.internal (пакетизация)
def test_compose_wrapper_exports_pythonpath() -> None:
    """D1: compose-wrapper.sh export PYTHONPATH с корнем репо (standalone compose_preflight)."""
    content = _COMPOSE_WRAPPER.read_text()

    assert "export PYTHONPATH=" in content, "D1: export PYTHONPATH обязателен в compose-wrapper.sh"
    assert "${SCRIPT_DIR}/../.." in content, "D1: PYTHONPATH обязан указывать на корень репо (2 уровня вверх)"
    assert "compose_preflight.py" in content, "compose-wrapper обязан делегировать compose_preflight.py"
    logger.info("[IMP:9][test][d1] compose-wrapper.sh экспортирует PYTHONPATH (repo root)")


# endregion FUNC_test_compose_wrapper_exports_pythonpath


# region FUNC_test_render_monitoring_self_bootstrap_source
## @purpose — D3 (5fe5802): render-monitoring — make-рецепт БЕЗ PYTHONPATH (direct-script), модуль
##            обязан сам загрузить repo root в sys.path ДО core.internal импортов.
# 🧪 TRAP[TEST] · 2026-08-05 · Regression · D3 — render-monitoring self-bootstrap (5fe5802)
# · Scenario: рецепт render-monitoring без PYTHONPATH + monitoring_config_renderer.py с
# ·   _PROJECT_ROOT (3 уровня) + sys.path.insert ДО core.internal импортов
# · Last fail: 2026-08-04 — render-monitoring ModuleNotFoundError (sys.path fallback неполный)
# · Remove if: render-monitoring получает PYTHONPATH в рецепте (тогда инвертировать)
def test_render_monitoring_self_bootstrap_source() -> None:
    """D3: render-monitoring direct-script без PYTHONPATH — self-bootstrap в source модуля."""
    recipe = _recipe_from_mk(_MANIFEST_MK, "render-monitoring")

    assert "PYTHONPATH" not in recipe, "D3: рецепт не должен полагаться на внешний PYTHONPATH (self-bootstrap)"
    assert "python3 core/internal/monitoring_config_renderer.py" in recipe, "direct-script invocация ожидалась"

    src = _MONITORING_RENDERER.read_text()
    assert "_PROJECT_ROOT = str(Path(__file__).resolve().parent.parent.parent)" in src, (
        "D3: self-bootstrap = корень репо (3 уровня parent)"
    )
    lines = src.splitlines()
    bootstrap_line = next(i for i, line in enumerate(lines, 1) if "sys.path.insert" in line)
    import_lines = [
        i
        for i, line in enumerate(lines, 1)
        if line.lstrip().startswith(("from core.internal", "import core.internal")) and i > bootstrap_line
    ]
    assert import_lines and bootstrap_line < min(import_lines), (
        "D3: sys.path.insert обязан идти ДО core.internal импортов (direct-script)"
    )
    logger.info("[IMP:9][test][d3] render-monitoring: self-bootstrap присутствует, рецепт без PYTHONPATH")


# endregion FUNC_test_render_monitoring_self_bootstrap_source


# region FUNC_test_render_monitoring_self_bootstrap_behavioral
## @purpose — D3 behavioral: релоад модуля с ВЫЧЕРКНУТЫМ repo root из sys.path (имитация direct-script
##            без PYTHONPATH) → self-bootstrap модуля восстанавливает корень.
# 🧪 TRAP[TEST] · 2026-08-05 · Regression · D3 behavioral — self-bootstrap восстанавливает repo root
# · Scenario: sys.path без repo root + PYTHONPATH удалён → importlib.reload(module) → repo root вернулся
# · Last fail: 2026-08-04 — ModuleNotFoundError при direct-script invocации render-monitoring
# · Remove if: monitoring_config_renderer.py перестаёт быть direct-script
def test_render_monitoring_self_bootstrap_behavioral(monkeypatch: pytest.MonkeyPatch) -> None:
    """D3 behavioral: релоад с вычеркнутым repo root — self-bootstrap восстанавливает sys.path."""
    import importlib

    import core.internal.monitoring_config_renderer as mcr

    repo_root = str(Path(mcr.__file__).resolve().parent.parent.parent)
    monkeypatch.delenv("PYTHONPATH", raising=False)
    stripped = [p for p in sys.path if os.path.abspath(p) != repo_root]
    monkeypatch.setattr(sys, "path", stripped)
    assert repo_root not in sys.path, "precondition: repo root вычеркнут (direct-script без PYTHONPATH)"

    importlib.reload(mcr)

    assert repo_root in sys.path, "D3: self-bootstrap обязан восстановить repo root в sys.path"
    logger.info("[IMP:9][test][d3-behavioral] self-bootstrap восстановил repo root после релоада")


# endregion FUNC_test_render_monitoring_self_bootstrap_behavioral

# endregion Tests: D1/D2/D3 — up-safe / compose-wrapper / render-monitoring (DevPlan 136 W1 T1.10)
