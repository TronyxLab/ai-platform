#!/usr/bin/env python3
# GREP_SUMMARY: test-vhost-configurator configure-vhost update-yaml-for-vhost configure-vhost-via-subprocess resolve-node-configs-dir skip-no-domain fallback D4
# STRUCTURE: fixtures(tmp_path project factory) → ◇ configure_vhost ┌empty domain → SKIP (False) без мутаций┐ → ◇ primary vhost_renderer (real call, D-I1 закрыт) → ◇ fallback subprocess (add-vhost.sh) → ◇ update_yaml_for_vhost (needs.domain + expose:true) → ◇ resolve_node_configs_dir (walk-up | PROJECTS_ROOT env) → ⎋ LDD IMP:9
# region MODULE_CONTRACT
## @purpose  Unit tests for scaffold/vhost_configurator.py (DevPlan 139 W4.1 — закрытие blind spot
##            vhost_configurator, 222 LOC). Рендер НЕ перетестируется — unit/test_vhost_renderer.py
##            (1123 LOC) покрыт (§7.3 НЕ трогать): здесь только configure_vhost/update_yaml_for_vhost/
##            configure_vhost_via_subprocess/resolve_node_configs_dir.
## @scope    configure_vhost (пустой domain → SKIP без мутаций; primary renderer path; fallback
##           subprocess при недоступности renderer), update_yaml_for_vhost (needs.domain + expose:true),
##           configure_vhost_via_subprocess (add-vhost.sh найден/не найден, rc 0/non-zero,
##           node-configs dir отсутствует), resolve_node_configs_dir (walk-up + PROJECTS_ROOT fallback).
## @invariants
##   - Без domain → SKIP (False) без мутаций (update_yaml_for_vhost НЕ вызывается)
##   - D4: try vhost_renderer (Python API) → fallback add-vhost.sh subprocess
##   - update_yaml_for_vhost: needs.domain + expose:true перед генерацией vhost
##   - resolve_node_configs_dir: projects/org/node-configs → PROJECTS_ROOT env fallback
##   - tmp_path-изоляция (xdist-безопасность), 0 subprocess реальной ноды (мок-шелы)
##   - Test Honesty R1-R5: negative-тесты (пустой domain, add-vhost.sh отсутствует, rc!=0,
##     node-configs отсутствует, renderer возвращает False) — 0 pass-тестов
##   - LDD: каждый тест — IMP:9-траектория (ldd_trajectory)
## @rationale W4 (139): 222 LOC production без тестов — критичный adopt-path (step 8). Поведенческие
##            контракты из MODULE_CONTRACT vhost_configurator переносятся в исполняемые проверки.
## @changes  2026-08-05 | Created (DevPlan 139 W4.1)
##            2026-08-11 | DevPlan 145 W3 D-I1 — configure_vhost_for_project реализован
##                       в vhost_renderer.py; primary-path тест переведён с mock на реальный вызов
# endregion MODULE_CONTRACT

import logging
import subprocess
import sys
from pathlib import Path
from unittest import mock

from core.internal.scaffold import vhost_configurator as vc
from tests._conftest.ldd import ldd_trajectory

logger = logging.getLogger(__name__)

# Реальный путь add-vhost.sh (используется configure_vhost_via_subprocess при найденном скрипте)
_ADD_VHOST_SCRIPT = Path(__file__).resolve().parent.parent.parent / "core" / "internal" / "scaffold" / "add-vhost.sh"


# region FUNC__make_project
## @purpose  Создать tmp project с ai-platform.yaml (needs-секция) — стандартная фикстура.
## @io       ⇥ tmp_path, needs: dict → ⎋ project_dir: Path
## @complexity O(1)
def _make_project(tmp_path: Path, needs: dict | None = None) -> Path:
    """Create a tmp project dir with ai-platform.yaml for update/configure tests."""
    project_dir = tmp_path / "proj"
    project_dir.mkdir(parents=True, exist_ok=True)
    import yaml

    data = {"name": "test-app", "type": "backend", "needs": needs or {"database": "postgres"}}
    with open(project_dir / "ai-platform.yaml", "w") as f:
        yaml.dump(data, f, default_flow_style=False, sort_keys=False)
    return project_dir


# endregion FUNC__make_project


# ═══════════════════════════════════════════════════════════════════════════
# configure_vhost — пустой domain → SKIP без мутаций
# ═══════════════════════════════════════════════════════════════════════════


# region FUNC_test_configure_vhost_empty_domain_skips_no_mutation
## @purpose  Пустой domain → SKIP (False) БЕЗ мутаций: update_yaml_for_vhost НЕ вызывается,
##            subprocess не запускается, yaml-файл не трогается.
# 🧪 TRAP[TEST] · configure_vhost_empty_domain_skips · NEGATIVE · Regression: пустой domain запускает vhost
# · Scenario: configure_vhost(domain="") → False; update_yaml_for_vhost mocked → assert NOT called;
# ·   yaml-файл не существует/не создаётся (мутаций нет)
# · Last fail: N/A (новый тест W4.1; до теста поведение SKIP не было зафиксировано)
# · Remove if: контракт «без domain → skip без мутаций» меняется
@ldd_trajectory
def test_configure_vhost_empty_domain_skips_no_mutation(tmp_path, monkeypatch, caplog) -> None:
    """Пустой domain → SKIP (False), update_yaml_for_vhost НЕ вызывается, файлов нет."""
    project_dir = tmp_path / "proj"
    project_dir.mkdir()
    yaml_file = project_dir / "ai-platform.yaml"

    update_mock = mock.MagicMock()
    monkeypatch.setattr(vc, "update_yaml_for_vhost", update_mock)

    result = vc.configure_vhost(project_dir, domain="", org="org", yaml_file=yaml_file)

    assert result is False, "Пустой domain обязан вернуть False (SKIP)"
    update_mock.assert_not_called(), "SKIP не должен мутировать ai-platform.yaml"
    assert not yaml_file.exists(), "SKIP не должен создавать файлы (no mutation)"
    logger.info("[IMP:9][test] configure_vhost: пустой domain → SKIP (False) без мутаций ✓")


# endregion FUNC_test_configure_vhost_empty_domain_skips_no_mutation


# ═══════════════════════════════════════════════════════════════════════════
# configure_vhost — primary vhost_renderer path (DI-инъекция)
# ═══════════════════════════════════════════════════════════════════════════


# region FUNC_test_configure_vhost_renderer_primary_path
## @purpose  D4 primary (D-I1 закрыт): vhost_renderer.configure_vhost_for_project реализован →
##            вызывается renderer, subprocess fallback НЕ запускается, vhost-файл создан, результат True.
# 🧪 TRAP[TEST] · configure_vhost_renderer_primary_path · Contract (D4) · Regression: renderer path не исполняется
# · Scenario: node_configs_dir задан; configure_vhost → real configure_vhost_for_project → render_vhost;
# ·   vhost-файл создан в overlays/nginx/{domain}.conf; subprocess НЕ вызван; result=True
# · Last fail: N/A (после D-I1 primary-path реальный; ранее mock-инъекция)
# · Remove if: renderer-путь удаляется из configure_vhost (тогда тест удалить с ним)
@ldd_trajectory
def test_configure_vhost_renderer_primary_path(tmp_path, monkeypatch, caplog) -> None:
    """Renderer доступен (D-I1) → configure_vhost_for_project вызывается, vhost создан, subprocess не запускается."""
    # D-I1 (DevPlan 145 W3): configure_vhost_for_project реализован — реальный вызов, не mock
    project_dir = tmp_path / "proj"
    project_dir.mkdir(parents=True, exist_ok=True)
    import yaml

    # ai-platform.yaml с expose:true + target_node (нужно для load_vhost_config)
    with open(project_dir / "ai-platform.yaml", "w") as f:
        yaml.dump(
            {
                "name": "test-app",
                "type": "backend",
                "target_node": "test-node",
                "needs": {"domain": "example.com", "expose": True},
            },
            f,
            default_flow_style=False,
            sort_keys=False,
        )

    yaml_file = project_dir / "ai-platform.yaml"
    node_configs_dir = tmp_path / "node-configs"

    subprocess_mock = mock.MagicMock()
    monkeypatch.setattr(vc.subprocess, "run", subprocess_mock)

    result = vc.configure_vhost(
        project_dir, domain="example.com", org="org", yaml_file=yaml_file, node_configs_dir=node_configs_dir
    )

    assert result is True, "Renderer-успех обязан вернуть True"
    subprocess_mock.assert_not_called(), "Renderer path не должен запускать subprocess fallback"
    # vhost-файл создан в overlays/nginx/test-node/
    vhost_file = node_configs_dir / "test-node" / "overlays" / "nginx" / "example.com.conf"
    assert vhost_file.exists(), f"Ожидался vhost-файл: {vhost_file}"
    assert "GENERATED" in vhost_file.read_text(), "vhost-файл должен содержать GENERATED-маркер"
    logger.info("[IMP:9][test] configure_vhost: primary renderer path → True, vhost создан ✓")


# endregion FUNC_test_configure_vhost_renderer_primary_path


# region FUNC_test_configure_vhost_renderer_false_triggers_subprocess
## @purpose  Renderer возвращает False (нет target_node → load_vhost_config=None → configure skip) →
##            fallback на subprocess add-vhost.sh (D4) → True при rc=0.
# 🧪 TRAP[TEST] · configure_vhost_renderer_false_fallback · Contract (D4) · Regression: False-рендера не ведёт к fallback
# · Scenario: ai-platform.yaml без target_node → configure_vhost_for_project returns False →
# ·   configure_vhost_via_subprocess вызывается; subprocess rc=0 → True
# · Last fail: N/A (после D-I1: реальный renderer, False через отсутствующий target_node)
# · Remove if: renderer-False контракт меняется (False перестаёт означать «пробуй fallback»)
@ldd_trajectory
def test_configure_vhost_renderer_false_triggers_subprocess(tmp_path, monkeypatch, caplog) -> None:
    """Renderer → False (no target_node) → subprocess fallback (add-vhost.sh), rc=0 → True."""
    project_dir = tmp_path / "proj"
    project_dir.mkdir(parents=True, exist_ok=True)
    import yaml

    # ai-platform.yaml БЕЗ target_node → load_vhost_config вернёт None → configure_vhost_for_project False
    with open(project_dir / "ai-platform.yaml", "w") as f:
        yaml.dump(
            {"name": "test-app", "type": "backend", "needs": {"database": "postgres"}},
            f,
            default_flow_style=False,
            sort_keys=False,
        )
    yaml_file = project_dir / "ai-platform.yaml"
    node_configs_dir = tmp_path / "node-configs"
    node_configs_dir.mkdir()

    subprocess_mock = mock.MagicMock(
        return_value=subprocess.CompletedProcess(["bash", str(_ADD_VHOST_SCRIPT)], 0, stdout="", stderr="")
    )
    monkeypatch.setattr(vc.subprocess, "run", subprocess_mock)

    result = vc.configure_vhost(
        project_dir, domain="example.com", org="org", yaml_file=yaml_file, node_configs_dir=node_configs_dir
    )

    assert result is True, "Fallback rc=0 обязан вернуть True"
    subprocess_mock.assert_called_once(), "Renderer False → subprocess fallback запущен"
    logger.info("[IMP:9][test] configure_vhost: renderer False (no target_node) → subprocess fallback → True ✓")


# endregion FUNC_test_configure_vhost_renderer_false_triggers_subprocess


# ═══════════════════════════════════════════════════════════════════════════
# configure_vhost — fallback subprocess (renderer недоступен — реальный путь в дереве)
# ═══════════════════════════════════════════════════════════════════════════


# region FUNC_test_configure_vhost_fallback_subprocess_when_renderer_unavailable
## @purpose  vhost_renderer НЕДОСТУПЕН (sys.modules=None → ImportError — тест форсирует
##            недоступность renderer-модуля) → fallback configure_vhost_via_subprocess → bash add-vhost.sh с
##            --project-dir/--node-configs-dir → rc=0 → True. update_yaml_for_vhost отработал ДО fallback.
# 🧪 TRAP[TEST] · configure_vhost_fallback_subprocess_unavailable_renderer · Regression (реальный путь) · Fallback не работает
# · Scenario: sys.modules["...vhost_renderer"]=None → ImportError → subprocess add-vhost.sh rc=0 → True;
# ·   assert bash args (--project-dir/--node-configs-dir); yaml обновлён (needs.domain+expose)
# · Last fail: N/A (новый тест W4.1; реальный путь исполняется сегодня — мёртвый renderer path)
# · Remove if: renderer становится доступным (configure_vhost_for_project появляется) — поведение сменится на primary
@ldd_trajectory
def test_configure_vhost_fallback_subprocess_when_renderer_unavailable(tmp_path, monkeypatch, caplog) -> None:
    """Renderer ImportError → fallback: bash add-vhost.sh с корректными аргументами → True."""
    project_dir = _make_project(tmp_path)
    yaml_file = project_dir / "ai-platform.yaml"
    node_configs_dir = tmp_path / "node-configs"
    node_configs_dir.mkdir()

    # Принудительный ImportError для vhost_renderer (None в sys.modules → import halt)
    monkeypatch.setitem(sys.modules, "core.internal.scaffold.vhost_renderer", None)

    subprocess_mock = mock.MagicMock(
        return_value=subprocess.CompletedProcess(["bash", str(_ADD_VHOST_SCRIPT)], 0, stdout="", stderr="")
    )
    monkeypatch.setattr(vc.subprocess, "run", subprocess_mock)

    result = vc.configure_vhost(
        project_dir, domain="example.com", org="org", yaml_file=yaml_file, node_configs_dir=node_configs_dir
    )

    assert result is True, "subprocess fallback rc=0 обязан вернуть True"
    subprocess_mock.assert_called_once()
    args = subprocess_mock.call_args.args[0]
    assert args[0] == "bash", f"Первый аргумент — bash, got {args}"
    assert str(args[1]).endswith("add-vhost.sh"), f"Второй аргумент — add-vhost.sh, got {args[1]}"
    assert "--project-dir" in args and str(project_dir) in args
    assert "--node-configs-dir" in args and str(node_configs_dir) in args

    # update_yaml_for_vhost отработал ДО fallback: needs.domain + expose:true записаны
    import yaml

    with open(yaml_file) as f:
        written = yaml.safe_load(f)
    assert written["needs"]["domain"] == "example.com"
    assert written["needs"]["expose"] is True
    logger.info(
        "[IMP:9][test] configure_vhost: renderer ImportError → subprocess fallback (bash add-vhost.sh) → True ✓"
    )


# endregion FUNC_test_configure_vhost_fallback_subprocess_when_renderer_unavailable


# region FUNC_test_configure_vhost_subprocess_nonzero_returns_false
## @purpose  Fallback subprocess вернул ненулевой rc → False + лог stderr (IMP:8).
# 🧪 TRAP[TEST] · configure_vhost_subprocess_nonzero · NEGATIVE (R5) · Regression: ненулевой rc add-vhost.sh трактуется как успех
# · Scenario: subprocess rc=1 + stderr "boom" → False; stderr залогирован; возврат не raise
# · Last fail: N/A (новый negative-тест W4.1)
# · Remove if: семантика rc add-vhost.sh меняется (rc!=0 перестаёт быть ошибкой)
@ldd_trajectory
def test_configure_vhost_subprocess_nonzero_returns_false(tmp_path, monkeypatch, caplog) -> None:
    """Fallback rc!=0 → False (проверять вручную), stderr залогирован."""
    project_dir = _make_project(tmp_path)
    yaml_file = project_dir / "ai-platform.yaml"
    node_configs_dir = tmp_path / "node-configs"
    node_configs_dir.mkdir()

    monkeypatch.setitem(sys.modules, "core.internal.scaffold.vhost_renderer", None)
    subprocess_mock = mock.MagicMock(
        return_value=subprocess.CompletedProcess(
            ["bash", str(_ADD_VHOST_SCRIPT)], 1, stdout="", stderr="add-vhost boom (test)"
        )
    )
    monkeypatch.setattr(vc.subprocess, "run", subprocess_mock)

    result = vc.configure_vhost(
        project_dir, domain="example.com", org="org", yaml_file=yaml_file, node_configs_dir=node_configs_dir
    )

    assert result is False, "rc!=0 обязан вернуть False"
    stderr_logs = [r.message for r in caplog.records if "add-vhost.sh stderr" in r.message]
    assert stderr_logs, "Ожидался IMP:8 лог stderr add-vhost.sh"
    logger.info("[IMP:9][test] configure_vhost: subprocess rc=1 → False, stderr залогирован ✓")


# endregion FUNC_test_configure_vhost_subprocess_nonzero_returns_false


# region FUNC_test_configure_vhost_subprocess_node_configs_missing
## @purpose  Fallback: node-configs dir отсутствует (resolve → None) → False (IMP:8 manual hint),
##            subprocess НЕ запускается.
# 🧪 TRAP[TEST] · configure_vhost_node_configs_missing · NEGATIVE · Regression: отсутствие node-configs не блокируется
# · Scenario: add-vhost.sh есть, node_configs_dir=None → resolve mocked → None → False;
# ·   subprocess НЕ вызван; лог "node-configs dir not found"
# · Last fail: N/A (новый negative-тест W4.1)
# · Remove if: контракт «нет node-configs → manual hint + False» меняется
@ldd_trajectory
def test_configure_vhost_subprocess_node_configs_missing(tmp_path, monkeypatch, caplog) -> None:
    """node-configs dir отсутствует → False, subprocess не запускается, manual hint."""
    project_dir = _make_project(tmp_path)
    yaml_file = project_dir / "ai-platform.yaml"

    monkeypatch.setitem(sys.modules, "core.internal.scaffold.vhost_renderer", None)
    monkeypatch.setattr(vc, "resolve_node_configs_dir", lambda project_dir, org: None)
    subprocess_mock = mock.MagicMock()
    monkeypatch.setattr(vc.subprocess, "run", subprocess_mock)

    result = vc.configure_vhost(
        project_dir, domain="example.com", org="org", yaml_file=yaml_file, node_configs_dir=None
    )

    assert result is False, "Нет node-configs → False (manual hint)"
    subprocess_mock.assert_not_called(), "Без node-configs subprocess не запускается"
    hints = [r.message for r in caplog.records if "create vhost manually" in r.message]
    assert hints, "Ожидался manual-хинт (create vhost manually in overlays/nginx/)"
    logger.info("[IMP:9][test] configure_vhost: node-configs отсутствует → False + manual hint ✓")


# endregion FUNC_test_configure_vhost_subprocess_node_configs_missing


# ═══════════════════════════════════════════════════════════════════════════
# update_yaml_for_vhost
# ═══════════════════════════════════════════════════════════════════════════


# region FUNC_test_update_yaml_for_vhost_sets_domain_and_expose
## @purpose  update_yaml_for_vhost: needs.domain + needs.expose=True записываются ДО генерации vhost;
##            прочие needs-ключи сохраняются.
# 🧪 TRAP[TEST] · update_yaml_for_vhost_sets_domain_expose · Contract (D4) · Regression: yaml не обновляется перед vhost
# · Scenario: yaml c needs.database → update → needs.domain="example.com", needs.expose is True,
# ·   database сохранён; IMP:7 лог "ai-platform.yaml updated"
# · Last fail: N/A (новый тест W4.1)
# · Remove if: контракт «needs.domain + expose:true перед генерацией» меняется
@ldd_trajectory
def test_update_yaml_for_vhost_sets_domain_and_expose(tmp_path, caplog) -> None:
    """update_yaml_for_vhost записывает needs.domain + expose:true, сохраняя прочие ключи."""
    project_dir = _make_project(tmp_path, needs={"database": "postgres", "llm": False})
    yaml_file = project_dir / "ai-platform.yaml"

    vc.update_yaml_for_vhost(yaml_file, "example.com", log_prefix="adopt")

    import yaml

    with open(yaml_file) as f:
        written = yaml.safe_load(f)
    assert written["needs"]["domain"] == "example.com", "needs.domain должен быть установлен"
    assert written["needs"]["expose"] is True, "needs.expose должен быть True"
    assert written["needs"]["database"] == "postgres", "Существующие needs-ключи сохраняются"
    updated_logs = [r.message for r in caplog.records if "ai-platform.yaml updated" in r.message]
    assert updated_logs, "Ожидался IMP:7 лог обновления yaml"
    logger.info(
        "[IMP:9][test] update_yaml_for_vhost: needs.domain=%s, expose=true записаны, database сохранён ✓", "example.com"
    )


# endregion FUNC_test_update_yaml_for_vhost_sets_domain_and_expose


# region FUNC_test_update_yaml_for_vhost_missing_file_noop
## @purpose  yaml_file отсутствует → no-op (return), без исключений и без создания файла.
# 🧪 TRAP[TEST] · update_yaml_for_vhost_missing_file · NEGATIVE · Regression: отсутствующий yaml роняет vhost
# · Scenario: yaml_file не существует → update → return None без raise; файл не создаётся
# · Last fail: N/A (новый negative-тест W4.1)
# · Remove if: контракт «missing yaml → no-op» меняется
@ldd_trajectory
def test_update_yaml_for_vhost_missing_file_noop(tmp_path, caplog) -> None:
    """yaml_file отсутствует → no-op (без исключений, без создания файла)."""
    missing = tmp_path / "nope" / "ai-platform.yaml"

    vc.update_yaml_for_vhost(missing, "example.com", log_prefix="adopt")

    assert not missing.exists(), "Файл не должен создаваться при отсутствии"
    logger.info("[IMP:9][test] update_yaml_for_vhost: отсутствующий yaml → no-op без мутаций ✓")


# endregion FUNC_test_update_yaml_for_vhost_missing_file_noop


# ═══════════════════════════════════════════════════════════════════════════
# resolve_node_configs_dir
# ═══════════════════════════════════════════════════════════════════════════


# region FUNC_test_resolve_node_configs_dir_walk_up
## @purpose  Walk-up: project_dir = `<projects_root>/<org>/<proj>`, parent.name == org → `<projects_root>/node-configs`.
# 🧪 TRAP[TEST] · resolve_node_configs_dir_walk_up · Contract (D4) · Regression: walk-up резолв сломан
# · Scenario: tmp/org/proj + tmp/node-configs (is_dir) → resolve(proj, org) == tmp/node-configs
# · Last fail: N/A (новый тест W4.1)
# · Remove if: логика resolve_node_configs_dir меняется (walk-up удаляется)
@ldd_trajectory
def test_resolve_node_configs_dir_walk_up(tmp_path, monkeypatch, caplog) -> None:
    """Walk-up: parent.name == org → projects_root/node-configs."""
    monkeypatch.delenv("PROJECTS_ROOT", raising=False)
    org_dir = tmp_path / "org"
    proj_dir = org_dir / "proj"
    proj_dir.mkdir(parents=True)
    node_configs = tmp_path / "node-configs"
    node_configs.mkdir()

    resolved = vc.resolve_node_configs_dir(proj_dir, org="org")

    assert resolved == node_configs, f"Walk-up резолв: ожидался {node_configs}, got {resolved}"
    logger.info("[IMP:9][test] resolve_node_configs_dir: walk-up → %s ✓", resolved)


# endregion FUNC_test_resolve_node_configs_dir_walk_up


# region FUNC_test_resolve_node_configs_dir_env_fallback
## @purpose  PROJECTS_ROOT env fallback: parent.name != org → `PROJECTS_ROOT/org/node-configs` (is_dir).
# 🧪 TRAP[TEST] · resolve_node_configs_dir_env_fallback · Contract (D4) · Regression: PROJECTS_ROOT fallback сломан
# · Scenario: project вне org-структуры; PROJECTS_ROOT=tmp; tmp/org/node-configs создан → resolve вернёт его
# · Last fail: N/A (новый тест W4.1)
# · Remove if: PROJECTS_ROOT fallback удаляется из resolve_node_configs_dir
@ldd_trajectory
def test_resolve_node_configs_dir_env_fallback(tmp_path, monkeypatch, caplog) -> None:
    """PROJECTS_ROOT env → `PROJECTS_ROOT/org/node-configs`."""
    proj_dir = tmp_path / "elsewhere" / "proj"
    proj_dir.mkdir(parents=True)
    projects_root = tmp_path / "projects-root"
    (projects_root / "org" / "node-configs").mkdir(parents=True)

    monkeypatch.setenv("PROJECTS_ROOT", str(projects_root))

    resolved = vc.resolve_node_configs_dir(proj_dir, org="org")

    assert resolved == projects_root / "org" / "node-configs", f"Env fallback: got {resolved}"
    logger.info("[IMP:9][test] resolve_node_configs_dir: PROJECTS_ROOT fallback → %s ✓", resolved)


# endregion FUNC_test_resolve_node_configs_dir_env_fallback


# region FUNC_test_resolve_node_configs_dir_none
## @purpose  Ни walk-up, ни env → None (graceful).
# 🧪 TRAP[TEST] · resolve_node_configs_dir_none · NEGATIVE · Regression: отсутствие node-configs → crash
# · Scenario: proj вне org-структуры, PROJECTS_ROOT не задан, нет node-configs → None (без raise)
# · Last fail: N/A (новый negative-тест W4.1)
# · Remove if: resolve_node_configs_dir начинает бросать исключения
@ldd_trajectory
def test_resolve_node_configs_dir_none(tmp_path, monkeypatch, caplog) -> None:
    """Нет walk-up и нет PROJECTS_ROOT → None (graceful)."""
    monkeypatch.delenv("PROJECTS_ROOT", raising=False)
    proj_dir = tmp_path / "elsewhere" / "proj"
    proj_dir.mkdir(parents=True)

    resolved = vc.resolve_node_configs_dir(proj_dir, org="org")

    assert resolved is None, f"Ожидался None, got {resolved}"
    logger.info("[IMP:9][test] resolve_node_configs_dir: ни walk-up, ни env → None ✓")


# endregion FUNC_test_resolve_node_configs_dir_none
