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
from pathlib import Path

import pytest

from core.internal.deploy.orchestrator_cli import _deliver, build_parser

logger = logging.getLogger(__name__)

_REPO_ROOT = Path(__file__).resolve().parent.parent.parent
_DEPLOY_MK = _REPO_ROOT / "makefiles" / "deploy.mk"


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
