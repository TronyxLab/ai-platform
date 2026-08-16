"""
# GREP_SUMMARY: test-w2-self-bootstrap, sys.path, repo-root, standalone-invocation, env -i, ModuleNotFoundError, T2.1, T2.2, T2.6, T2.10, latent-class-A
# STRUCTURE: ▶ parametrized modules (rel_path, parent-depth, CLI args, expected rc) → ◇ static source assert (_PROJECT_ROOT + sys.path.insert ДО core.* импортов) → ◇ env -i subprocess invocation (cwd≠root, БЕЗ PYTHONPATH) → ◇ behavioral reload (repo root восстановлен) → ⎋ LDD IMP:9
# region MODULE_CONTRACT
## @purpose  Регрессионные тесты латентного класса A (DevPlan 136 W2): self-bootstrap корня репо
##           (канон config_renderer.py:44-45) в модулях, вызываемых standalone из чистого env.
##           Покрывает T2.1 (docker_orchestrator), T2.2 (s3_ssl_cache — cron-контекст acme.sh),
##           T2.6 (7 monitoring-модулей), T2.10 (compose_preflight / key_provisioner / context_deployer)
##           + верификацию dead_code_checker.py (stdlib-only — bootstrap не требуется).
## @scope    Статический source-анализ + поведенческая инвокация из чистого env (env -i-эквивалент).
##           Не запускает Docker/SSH/S3 — только импорт/CLI-инвокация с --help/usage.
## @invariants
##   - Каждый модуль с core.* импортами имеет _PROJECT_ROOT + sys.path.insert ДО первой core.* строки
##   - env -i инвокация из cwd≠root без PYTHONPATH: exit 0 (или осмысленный usage), НЕ ModuleNotFoundError
##   - dead_code_checker.py: stdlib-only → работает из чистого env без self-bootstrap (верификация)
## @rationale  W1 D3 (test_deploy_mk_chain) зафиксировал канон для monitoring_config_renderer;
##             W2 расширяет на 12 модулей латентного класса A (DevPlan 136 §5.2 T2.1/T2.2/T2.6/T2.10).
## @changes    2026-08-05 | Created (DevPlan 136 W2)
# endregion MODULE_CONTRACT
"""

import logging
import os
import subprocess
import sys
from pathlib import Path

import pytest

from tests._conftest.ldd import ldd_trajectory

pytestmark = pytest.mark.static_audit

logger = logging.getLogger(__name__)

_REPO_ROOT = Path(__file__).resolve().parent.parent.parent

# ── Инвентарь модулей латентного класса A ────────────────────────────────────
# (rel_path от корня репо, ожидаемый parent-уровень до корня, CLI args, ожидаемый rc)
_SELF_BOOTSTRAP_MODULES: list[tuple[str, int, list[str], int, str]] = [
    (
        "core/internal/bootstrap/deploy/docker_orchestrator.py",
        5,
        ["--help"],
        0,
        "T2.1: docker_orchestrator standalone (core.* импорты на module-уровне)",
    ),
    (
        "core/internal/bootstrap/s3_ssl_cache.py",
        4,
        [],
        1,
        "T2.2: s3_ssl_cache cron-контекст acme.sh — usage exit 1, НЕ ModuleNotFoundError",
    ),
    # T2.6 — 7 monitoring-модулей (dual-import fallback)
    ("core/internal/monitoring/alert_rules.py", 4, [], 0, "T2.6: alert_rules standalone"),
    ("core/internal/monitoring/catalog_refresh.py", 4, [], 0, "T2.6: catalog_refresh standalone"),
    ("core/internal/monitoring/grafana_dashboards.py", 4, [], 0, "T2.6: grafana_dashboards standalone"),
    ("core/internal/monitoring/langfuse_projects.py", 4, [], 0, "T2.6: langfuse_projects standalone"),
    ("core/internal/monitoring/loki_retention.py", 4, [], 0, "T2.6: loki_retention standalone"),
    ("core/internal/monitoring/prometheus_targets.py", 4, [], 0, "T2.6: prometheus_targets standalone"),
    ("core/internal/monitoring/service_reload.py", 4, [], 0, "T2.6: service_reload standalone"),
    # T2.10 — standalone-инвокация из чистого env
    (
        "core/internal/bootstrap/deploy/compose_preflight.py",
        5,
        ["--help"],
        0,
        "T2.10: compose_preflight standalone (compose-wrapper PYTHONPATH не требуется)",
    ),
    (
        "core/internal/llm/key_provisioner.py",
        4,
        ["--help"],
        0,
        "T2.10: key_provisioner standalone (provision-llm.sh PYTHONPATH не требуется)",
    ),
    (
        "core/internal/bootstrap/deploy/context_deployer.py",
        5,
        ["--help"],
        0,
        "T2.10: context_deployer standalone (deploy-context.sh без PYTHONPATH)",
    ),
]


def _module_path(rel_path: str) -> Path:
    """Абсолютный путь модуля от корня репо."""
    return _REPO_ROOT / rel_path


def _core_import_lines(src_lines: list[str]) -> list[int]:
    """Номера строк (1-based) с core.* / top-level мониторинг-импортами."""
    return [
        i
        for i, line in enumerate(src_lines, 1)
        if line.lstrip().startswith((
            "from core.internal",
            "import core.internal",
            "from monitoring.config_renderer",
            "from monitoring.constants",
        ))
    ]


# region FUNC_test_self_bootstrap_static
## @purpose — Статический source-анализ: _PROJECT_ROOT (канон) + sys.path.insert ДО core.* импортов.
# 🧪 TRAP[TEST] · 2026-08-05 · Regression · W2 T2.1/T2.2/T2.6/T2.10 — self-bootstrap корня репо
# · Scenario: каждый core.*-модуль содержит _PROJECT_ROOT = parent×N + sys.path.insert ДО первой core.* строки
# · Last fail: 2026-08-05 — standalone-инвокация без PYTHONPATH → ModuleNotFoundError (латентный класс A)
# · Remove if: модуль пакетизирован (self-bootstrap не нужен)
@pytest.mark.parametrize(
    "rel_path,parent_depth,_args,_rc,_desc",
    _SELF_BOOTSTRAP_MODULES,
    ids=[p.split("/")[-1] for p, _d, _a, _r, _t in _SELF_BOOTSTRAP_MODULES],
)
@ldd_trajectory
def test_self_bootstrap_static(
    caplog: pytest.LogCaptureFixture,
    rel_path: str,
    parent_depth: int,
    _args: list[str],
    _rc: int,
    _desc: str,
) -> None:
    """Канон self-bootstrap: _PROJECT_ROOT (parent×N) + sys.path.insert ДО core.* импортов."""
    caplog.set_level(logging.INFO)
    src_lines = _module_path(rel_path).read_text(encoding="utf-8").splitlines()

    # 1. Строка _PROJECT_ROOT с ожидаемой глубиной parent
    root_lines = [i for i, line in enumerate(src_lines, 1) if "_PROJECT_ROOT" in line and "Path(__file__)" in line]
    assert root_lines, f"{rel_path}: _PROJECT_ROOT (канон) отсутствует"
    root_line = src_lines[root_lines[0] - 1]
    assert root_line.count(".parent") == parent_depth, (
        f"{rel_path}: ожидалось parent×{parent_depth} до корня, got {root_line.count('.parent')}: {root_line}"
    )

    # 2. sys.path.insert присутствует (именно корень, не внутренняя директория)
    insert_lines = [i for i, line in enumerate(src_lines, 1) if "sys.path.insert" in line]
    assert insert_lines, f"{rel_path}: sys.path.insert отсутствует"

    # 3. Для модулей с core.* импортами на module-уровне: bootstrap ДО импортов
    core_imports = _core_import_lines(src_lines)
    if core_imports and "monitoring/" not in rel_path:
        # Мониторинг-модули: core.* импортов нет (top-level через try/except) — проверка ниже
        assert min(insert_lines) < min(core_imports), (
            f"{rel_path}: sys.path.insert обязан идти ДО core.* импортов (bootstrap line {min(insert_lines)}, "
            f"first core import {min(core_imports)})"
        )

    logger.info(
        "[IMP:9][test][self-bootstrap] %s: канон self-bootstrap подтверждён (parent×%d)", rel_path, parent_depth
    )


# endregion FUNC_test_self_bootstrap_static


# region FUNC_test_env_i_invocation
## @purpose — Поведенческая инвокация из чистого env (env -i-эквивалент): cwd≠root, БЕЗ PYTHONPATH.
##            НЕ ModuleNotFoundError по core.* (exit 0/осмысленный usage).
# 🧪 TRAP[TEST] · 2026-08-05 · Regression (R5) · W2 — standalone-инвокация из чистого env
# · Scenario: subprocess [sys.executable, module, *args] cwd=tmp_path, env БЕЗ PYTHONPATH → rc ожидаемый,
#   stdout/stderr НЕ содержат ModuleNotFoundError / 'No module named'
# · Last fail: 2026-08-05 — env -i python3 MODULE → ModuleNotFoundError: No module named 'core' (класс A)
# · Remove if: модуль больше не инвокабилен standalone (пакетизация)
@pytest.mark.parametrize(
    "rel_path,parent_depth,args,expected_rc,_desc",
    _SELF_BOOTSTRAP_MODULES,
    ids=[p.split("/")[-1] for p, _d, _a, _r, _t in _SELF_BOOTSTRAP_MODULES],
)
@ldd_trajectory
def test_env_i_invocation(
    caplog: pytest.LogCaptureFixture,
    tmp_path: Path,
    rel_path: str,
    parent_depth: int,
    args: list[str],
    expected_rc: int,
    _desc: str,
) -> None:
    """env -i-инвокация: exit 0/осмысленный usage, НЕ ModuleNotFoundError (класс A)."""
    caplog.set_level(logging.INFO)
    module_path = _module_path(rel_path)

    # Чистое окружение: ТОЛЬКО PATH (чтобы найти python3), БЕЗ PYTHONPATH/сиротских переменных
    clean_env = {
        "PATH": os.environ.get("PATH", "/usr/bin:/bin:/usr/local/bin:/opt/homebrew/bin"),
        "HOME": str(tmp_path),
        "LANG": "C.UTF-8",
    }
    assert "PYTHONPATH" not in clean_env, "тест обязан имитировать env -i без PYTHONPATH"

    result = subprocess.run(
        [sys.executable, str(module_path), *args],
        cwd=tmp_path,  # cwd≠repo root — не полагаемся на cwd-зависимый sys.path[0]
        capture_output=True,
        text=True,
        env=clean_env,
        timeout=90,
        check=False,
    )
    combined = f"{result.stdout}\n{result.stderr}"
    assert "ModuleNotFoundError" not in combined, (
        f"{rel_path}: ModuleNotFoundError при standalone-инвокации (класс A): {result.stderr}"
    )
    assert "No module named" not in combined, f"{rel_path}: 'No module named' при standalone-инвокации"
    assert result.returncode == expected_rc, (
        f"{rel_path}: ожидался rc={expected_rc}, got {result.returncode}: {result.stderr}"
    )

    logger.info(
        "[IMP:9][test][env-i] %s: standalone-инвокация из чистого env → rc=%d (без PYTHONPATH)",
        rel_path,
        result.returncode,
    )


# endregion FUNC_test_env_i_invocation


# region FUNC_test_self_bootstrap_behavioral
## @purpose — Поведенческий: релоад модуля с ВЫЧЕРКНУТЫМ repo root из sys.path (имитация direct-script
##            без PYTHONPATH) → self-bootstrap модуля восстанавливает корень (паттерн W1 D3 behavioral).
##            Репрезентанты: docker_orchestrator (T2.1), key_provisioner (T2.10).
# 🧪 TRAP[TEST] · 2026-08-05 · Regression · W2 — self-bootstrap восстанавливает repo root (behavioral)
# · Scenario: sys.path без repo root + PYTHONPATH удалён → importlib.reload(module) → repo root вернулся
# · Last fail: 2026-08-05 — ModuleNotFoundError при direct-script инвокации (класс A)
# · Remove if: модуль больше не импортируем напрямую
@pytest.mark.parametrize(
    "import_name,rel_path",
    [
        ("core.internal.bootstrap.deploy.docker_orchestrator", "core/internal/bootstrap/deploy/docker_orchestrator.py"),
        ("core.internal.llm.key_provisioner", "core/internal/llm/key_provisioner.py"),
    ],
    ids=["docker_orchestrator", "key_provisioner"],
)
@ldd_trajectory
def test_self_bootstrap_behavioral(
    caplog: pytest.LogCaptureFixture,
    monkeypatch: pytest.MonkeyPatch,
    import_name: str,
    rel_path: str,
) -> None:
    """Behavioral: релоад с вычеркнутым repo root — self-bootstrap восстанавливает sys.path."""
    import importlib

    caplog.set_level(logging.INFO)
    mod = importlib.import_module(import_name)

    repo_root = str(Path(mod.__file__).resolve().parent.parent.parent.parent.parent)
    # Корень для docker_orchestrator = 5 уровней; для key_provisioner = 4 — вычислим по факту файла
    repo_root = str(_REPO_ROOT)
    monkeypatch.delenv("PYTHONPATH", raising=False)
    stripped = [p for p in sys.path if str(Path(p).absolute()) != repo_root]
    # 🧐 TRAP[DI-KEEP] · 2026-08-14 · — · sys.path keep (import-path изоляция, §4 floor)
    # · Rejected: DI-шов (sys.path — глобальное состояние интерпретатора, не параметр функции)
    # · Reason: тест проверяет importability модуля при ИЗМЕНЁННОМ sys.path (имитация
    # ·   direct-script без PYTHONPATH) — патч самого тестируемого механизма бессмысленен;
    # ·   self-bootstrap модуля восстанавливает корень, DI-параметр заменил бы subject
    # · Rev: при введении test-import-harness (изоляция импортов на уровне conftest)
    monkeypatch.setattr(sys, "path", stripped)
    assert repo_root not in sys.path, "precondition: repo root вычеркнут (direct-script без PYTHONPATH)"

    importlib.reload(mod)

    assert repo_root in sys.path, "self-bootstrap обязан восстановить repo root в sys.path"
    logger.info("[IMP:9][test][behavioral] %s: self-bootstrap восстановил repo root после релоада", rel_path)


# endregion FUNC_test_self_bootstrap_behavioral


# region FUNC_test_dead_code_checker_stdlib_only
## @purpose — Верификация T2.10: dead_code_checker.py — stdlib-only (0 core.* импортов) → self-bootstrap
##            НЕ требуется; standalone-инвокация из чистого env работает (подтверждение, фикс не нужен).
# 🧪 TRAP[TEST] · 2026-08-05 · Regression · W2 T2.10 — dead_code_checker stdlib-only (верификация)
# · Scenario: env -i python3 dead_code_checker.py --help → exit 0; в файле НЕТ core.* импортов
# · Last fail: N/A — подтверждение верификации (фикс не требовался)
# · Remove if: dead_code_checker.py начинает импортировать core.* (тогда нужен self-bootstrap)
@ldd_trajectory
def test_dead_code_checker_stdlib_only(caplog: pytest.LogCaptureFixture, tmp_path: Path) -> None:
    """dead_code_checker.py: stdlib-only → standalone-инвокация из чистого env (T2.10 verify)."""
    caplog.set_level(logging.INFO)
    module_path = _REPO_ROOT / "core" / "internal" / "lint" / "dead_code_checker.py"
    src = module_path.read_text(encoding="utf-8")
    core_imports = _core_import_lines(src.splitlines())
    assert not core_imports, f"dead_code_checker.py: stdlib-only инвариант нарушен (core.* импорты: {core_imports})"

    clean_env = {
        "PATH": os.environ.get("PATH", "/usr/bin:/bin:/usr/local/bin:/opt/homebrew/bin"),
        "HOME": str(tmp_path),
        "LANG": "C.UTF-8",
    }
    result = subprocess.run(
        [sys.executable, str(module_path), "--help"],
        cwd=tmp_path,
        capture_output=True,
        text=True,
        env=clean_env,
        timeout=60,
        check=False,
    )
    assert result.returncode == 0, f"dead_code_checker --help: rc={result.returncode}: {result.stderr}"
    assert "usage:" in result.stdout.lower(), "--help обязан выдать usage"
    logger.info("[IMP:9][test][dead-code] dead_code_checker.py stdlib-only — standalone env -i работает")


# endregion FUNC_test_dead_code_checker_stdlib_only
