# GREP_SUMMARY: test-make-contract gate make-contract .PHONY-recipe dry-run backup-restore-matrix restart-soft nginx-parity
# STRUCTURE: ▶ модули(docker) → ◇ .PHONY-рецепты (0 пустых) → ◇ make -n dry-run (exit 0) → ◇ backup/restore матрица D1 → ◇ restart soft → ◇ nginx dev-config dup-детекция → ⎋ verdict
# region MODULE_CONTRACT
## @purpose  Gate-тест make-контракта модулей (DevPlan 116 B7 T9, U-25): 0 пустых .PHONY,
##           dry-run всех таргетов, матрица backup/restore (D1), restart = soft (stop start),
##           nginx dev-config не дублирует config/ (U-46).
## @scope    Все 13 docker-модулей (platform-secrets — systemd, вне скоупа) + nginx compose dry-run
## @invariants
##   - Каждый таргет из .PHONY имеет рецепт (0 пустых .PHONY — U-25 не возвращается)
##   - make -n <target> exit 0 (dry-run без реального docker)
##   - backup/restore объявлены РОВНО у postgres, backup-cron, hermes-agent (матрица D1)
##   - restart рецепт содержит stop start (soft, не down) — AGENTS.md:167-контракт
##   - nginx: 0 файлов dev-config с содержимым, идентичным config/ (dup-детекция U-46)
##   - docker compose -f docker-compose.base.yml -f docker-compose.dev.yml config --quiet → exit 0
## @rationale Бриф: «для каждого модуля make -n restore/restart/backup не падает» — адаптирован
##   под D1 (stateless — таргеты отсутствуют, «No rule to make target» — ожидаемое поведение).
## @changes 2026-08-01 | Created (DevPlan 116 B7 T9)
# endregion MODULE_CONTRACT

import logging
import re
import shutil
import subprocess
from pathlib import Path

import pytest
from _conftest.ldd import _print_ldd_trajectory

from tests.helpers.gate_helpers import repo_root

logger = logging.getLogger(__name__)

# 13 docker-модулей (platform-secrets — module-system.mk, вне скоупа)
_DOCKER_MODULES = [
    "postgres",
    "backup-cron",
    "hermes-agent",
    "clickhouse",
    "minio",
    "redis",
    "nginx",
    "status-page",
    "infra-metrics",
    "litellm",
    "langfuse",
    "logging",
    "monitoring",
]

# Матрица D1: stateful-модули, объявляющие backup/restore
_STATEFUL_MODULES = {"postgres", "backup-cron", "hermes-agent"}


def _parse_phony_targets(makefile: Path) -> list[str]:
    """Извлечь имена таргетов из .PHONY: строки Makefile (включая includes).

    ## @io  ⇥ makefile → ⎋ list[str] имён таргетов из .PHONY
    ## @complexity O(L) — L строк файла
    ## @invariants
    ##   - Условные .PHONY (в ifeq-блоках module.mk) парсятся КАК ОБЪЯВЛЕННЫЕ — фактическую
    ##     доступность таргета проверяет сам make (dry-run), а не статический парсер.
    """
    targets: list[str] = []
    text = makefile.read_text()
    for match in re.finditer(r"^\.PHONY\s*:\s*(.+)$", text, re.MULTILINE):
        targets.extend(match.group(1).split())
    return sorted(set(targets))


def _target_has_recipe(makefile: Path, target: str) -> bool:
    """Проверить, что у таргета есть тело: prerequisites ИЛИ tab-рецепт (0 пустых .PHONY).

    ## @io  ⇥ makefile, target → ⎋ bool — есть ли тело (restart: stop start → True)
    ## @complexity O(L)
    ## @invariants
    ##   - `restart: stop start` — prerequisites (тело есть, это НЕ пустой таргет)
    ##   - Пустой таргет = ни prerequisites, ни tab-рецепта
    """
    text = makefile.read_text()
    lines = text.split("\n")
    in_target = False
    for line in lines:
        stripped = line.strip()
        if re.match(rf"^{re.escape(target)}\s*:", stripped) and not stripped.startswith("."):
            in_target = True
            # Строка объявления с prerequisites (что-то после ':') — тело есть
            prereq = stripped.split(":", 1)[1].strip()
            if prereq:
                return True
            continue
        if in_target:
            if line.startswith("\t") and line.strip():
                return True
            if stripped == "":
                continue
            if line.startswith(("\t", " ")):
                continue
            if re.match(r"^[a-zA-Z_-]+:", stripped):
                return False  # следующий таргет — тела не было
    return False


def _extract_target_recipe(makefile: Path, target: str) -> str:
    """Извлечь тело таргета: строка объявления (включая prerequisites) + tab-рецепт.

    ## @io  ⇥ makefile, target → ⎋ str — строка объявления + рецепт ("" если нет)
    ## @complexity O(L)
    ## @invariants
    ##   - restart: stop start — prerequisites (не tab-рецепт); строка объявления обязана
    ##     попасть в результат (иначе soft-семантика не проверяема)
    """
    text = makefile.read_text()
    lines = text.split("\n")
    in_target = False
    recipe: list[str] = []
    for line in lines:
        stripped = line.strip()
        if re.match(rf"^{re.escape(target)}\s*:", stripped) and not stripped.startswith("."):
            in_target = True
            recipe = [stripped]  # строка объявления с prerequisites
            continue
        if in_target:
            if line.startswith("\t"):
                recipe.append(line.strip())
            else:
                break
    return "\n".join(recipe)


# 🧪 TRAP[TEST] · make_contract_no_empty_phony · Gate · Regression: пустой .PHONY-таргет (U-25)
# · Scenario: каждый таргет из .PHONY всех 13 docker-модулей имеет рецепт
# · Last fail: U-25 (11/13 модулей: .PHONY restore без рецепта = тихий no-op)
# · Remove if: make-контракт переезжает с .PHONY на другой механизм
@pytest.mark.gate
def test_no_empty_phony_targets(caplog) -> None:
    """0 пустых .PHONY — каждый объявленный таргет имеет рецепт (U-25)."""
    caplog.set_level(logging.INFO)
    root = repo_root()
    violations: list[str] = []

    for mod in _DOCKER_MODULES:
        makefile = root / "core" / "modules" / mod / "Makefile"
        if not makefile.is_file():
            continue
        # Включаем module.mk + Makefile.common в анализ рецептов (перекрытия через include)
        combined = makefile.read_text()
        module_mk = root / "core" / "templates" / "module.mk"
        common = root / "core" / "Makefile.common"
        combined += "\n" + module_mk.read_text() + "\n" + common.read_text()
        combined_path = root / "core" / "modules" / mod / ".combined.mk.tmp"
        combined_path.write_text(combined)
        try:
            phony = _parse_phony_targets(combined_path)
            violations.extend(
                f"{mod}: .PHONY target '{target}' has no recipe"
                for target in phony
                if not _target_has_recipe(combined_path, target)
            )
        finally:
            combined_path.unlink()

    print(f"[IMP:8][no_empty_phony] Checked {len(_DOCKER_MODULES)} modules, {len(violations)} violations")
    assert not violations, "Empty .PHONY targets found (U-25 regression):\n" + "\n".join(violations)
    logger.info("[IMP:9][no_empty_phony] 0 empty .PHONY targets — PASS")

    found = _print_ldd_trajectory(caplog, "test_no_empty_phony_targets")

    assert found, "No IMP:9 log found — LDD violation"

    print("[IMP:9][no_empty_phony] 0 empty .PHONY targets — PASS")


# 🧪 TRAP[TEST] · make_contract_dry_run · Gate · Regression: make -n падает на модулях
# · Scenario: make -n <target> для всех .PHONY-таргетов всех 13 docker-модулей → exit 0
# · Last fail: U-25 (restore dry-run был тихим no-op)
# · Remove if: make-контракт модулей меняется кардинально
# 📝 TRAP[DEBT] · 2026-08-01 · MED · flaky unlink под xdist: .combined.mk.tmp пишется в фиксированный
# ·   путь модуля и unlink() в finally падает с FileNotFoundError при параллельном прогоне (observed
# ·   during DevPlan 116 B3 T10 gate run; passes in isolation)
# · Suspected: xdist worker/процессный race — фиксированный путь в дереве модуля разделяется между
# ·   параллельными прогонами одного теста (перезапись/удаление между write_text и unlink)
# · Impact: периодический красный gate без изменения кода — ложный фейл волны
# · When: deferred, out of scope B3 — фикс: tmp_path-фикстура вместо core/modules/<mod>/.combined.mk.tmp
@pytest.mark.gate
def test_make_n_dry_run_all_targets(caplog) -> None:
    """make -n для всех .PHONY-таргетов всех docker-модулей — exit 0 (без реального docker)."""
    caplog.set_level(logging.INFO)
    root = repo_root()
    failed: list[str] = []

    for mod in _DOCKER_MODULES:
        makefile = root / "core" / "modules" / mod / "Makefile"
        if not makefile.is_file():
            continue
        combined = makefile.read_text()
        combined += "\n" + (root / "core" / "templates" / "module.mk").read_text()
        combined += "\n" + (root / "core" / "Makefile.common").read_text()
        combined_path = root / "core" / "modules" / mod / ".combined.mk.tmp"
        combined_path.write_text(combined)
        try:
            phony = _parse_phony_targets(combined_path)
            for target in phony:
                # Условные backup/restore у stateless-модулей: парсер видит их в ifeq-блоке,
                # но make не объявляет (BACKUP_MODE=none) → «No rule to make target» = ожидаемо
                # (DevPlan T1 критерий). Dry-run проверяем только фактически доступные таргеты:
                # make -n backup на stateless → exit 2 — это НЕ баг, это контракт D1.
                if target in ("backup", "restore") and mod not in _STATEFUL_MODULES:
                    continue
                result = subprocess.run(
                    ["make", "-n", target],
                    capture_output=True,
                    text=True,
                    cwd=str(root / "core" / "modules" / mod),
                    timeout=15,
                )
                if result.returncode != 0:
                    failed.append(f"{mod}: make -n {target} → exit {result.returncode}: {result.stderr[:150]}")
        finally:
            combined_path.unlink()

    print(f"[IMP:8][dry_run] make -n dry-run across {len(_DOCKER_MODULES)} modules")
    assert not failed, "make -n failed:\n" + "\n".join(failed)
    logger.info("[IMP:9][dry_run] All module make -n dry-runs exit 0 — PASS")

    found = _print_ldd_trajectory(caplog, "test_make_n_dry_run_all_targets")

    assert found, "No IMP:9 log found — LDD violation"

    print("[IMP:9][dry_run] All module make -n dry-runs exit 0 — PASS")


# 🧪 TRAP[TEST] · make_contract_backup_restore_matrix · Gate · Regression: backup/restore матрица D1
# · Scenario: backup/restore объявлены РОВНО у postgres, backup-cron, hermes-agent
# · Last fail: U-25/U-61 (11 модулей имели битый backup/restore из шаблона)
# · Remove if: матрица stateful-модулей меняется (требует DevPlan)
@pytest.mark.gate
def test_backup_restore_matrix_d1(caplog) -> None:
    """backup/restore объявлены ровно у stateful-модулей (D1)."""
    caplog.set_level(logging.INFO)
    root = repo_root()

    # Фактическая доступность таргета проверяется через make -n (dry-run exit code):
    # stateful → exit 0, stateless → exit 2 («No rule to make target» — контракт D1).
    # Статический парсер НЕ годится: module.mk содержит условные ifeq-блоки, где
    # backup/restore объявлены литерально — парсер не умеет вычислять make-условия.
    declared: set[str] = set()
    for mod in _DOCKER_MODULES:
        makefile = root / "core" / "modules" / mod / "Makefile"
        if not makefile.is_file():
            continue
        backup_ok = subprocess.run(
            ["make", "-n", "backup"],
            capture_output=True,
            text=True,
            cwd=str(makefile.parent),
            timeout=15,
        ).returncode
        restore_ok = subprocess.run(
            ["make", "-n", "restore"],
            capture_output=True,
            text=True,
            cwd=str(makefile.parent),
            timeout=15,
        ).returncode
        if backup_ok == 0 or restore_ok == 0:
            declared.add(mod)

    print(f"[IMP:8][backup_matrix] Modules declaring backup/restore: {sorted(declared)}")
    assert declared == _STATEFUL_MODULES, (
        f"Backup/restore matrix drift (D1): declared={sorted(declared)}, expected={sorted(_STATEFUL_MODULES)}"
    )
    logger.info("[IMP:9][backup_matrix] backup/restore ровно у stateful-модулей (D1) — PASS")
    found = _print_ldd_trajectory(caplog, "test_backup_restore_matrix_d1")
    assert found, "No IMP:9 log found — LDD violation"
    print("[IMP:9][backup_matrix] backup/restore ровно у stateful-модулей (D1) — PASS")


# 🧪 TRAP[TEST] · make_contract_restart_soft · Gate · Regression: restart = recreate (не soft)
# · Scenario: restart рецепт содержит stop start (soft), НЕ down
# · Last fail: U-25 (restart: stop start = recreate из-за stop=down)
# · Remove if: семантика restart меняется (требует DevPlan)
@pytest.mark.gate
def test_restart_soft_semantics(caplog) -> None:
    """restart = soft (stop start), не down && up — AGENTS.md:167-контракт."""
    caplog.set_level(logging.INFO)
    root = repo_root()
    violations: list[str] = []

    for mod in _DOCKER_MODULES:
        makefile = root / "core" / "modules" / mod / "Makefile"
        if not makefile.is_file():
            continue
        combined = makefile.read_text()
        combined += "\n" + (root / "core" / "templates" / "module.mk").read_text()
        combined += "\n" + (root / "core" / "Makefile.common").read_text()
        combined_path = root / "core" / "modules" / mod / ".combined.mk.tmp"
        combined_path.write_text(combined)
        try:
            recipe = _extract_target_recipe(combined_path, "restart")
            if not recipe:
                violations.append(f"{mod}: restart target missing")
                continue
            if "stop" not in recipe or "start" not in recipe:
                violations.append(f"{mod}: restart recipe does not contain 'stop start': {recipe[:80]}")
            if "down" in recipe and "up" in recipe:
                violations.append(f"{mod}: restart recipe uses down+up (hard) — должен быть soft: {recipe[:80]}")
        finally:
            combined_path.unlink()

    print(f"[IMP:8][restart_soft] Checked {len(_DOCKER_MODULES)} modules for soft restart")
    assert not violations, "Restart semantics drift (U-25):\n" + "\n".join(violations)
    logger.info("[IMP:9][restart_soft] Все модули: restart = stop start (soft) — PASS")

    found = _print_ldd_trajectory(caplog, "test_restart_soft_semantics")

    assert found, "No IMP:9 log found — LDD violation"

    print("[IMP:9][restart_soft] Все модули: restart = stop start (soft) — PASS")


# 🧪 TRAP[TEST] · nginx_dev_config_no_duplicates · Gate · Regression: dev-config дублирует config/ (U-46)
# · Scenario: 0 файлов dev-config с содержимым, идентичным config/
# · Last fail: U-46 (10 файлов dev-config были полными дублями config/)
# · Remove if: dev-режим nginx переезжает на другой механизм
@pytest.mark.gate
def test_nginx_dev_config_no_duplicates(caplog) -> None:
    """0 файлов dev-config идентичны config/ (dup-детекция U-46)."""
    caplog.set_level(logging.INFO)
    root = repo_root()
    config_dir = root / "core" / "modules" / "nginx" / "config"
    dev_config_dir = root / "core" / "modules" / "nginx" / "dev-config"

    duplicates: list[str] = []
    if dev_config_dir.is_dir():
        for dev_file in sorted(dev_config_dir.iterdir()):
            if not dev_file.is_file():
                continue
            config_file = config_dir / dev_file.name
            if config_file.is_file() and dev_file.read_bytes() == config_file.read_bytes():
                duplicates.append(dev_file.name)

    print(
        f"[IMP:8][nginx_parity] dev-config files: {len(list(dev_config_dir.glob('*')) if dev_config_dir.is_dir() else [])}, duplicates: {duplicates}"
    )
    assert not duplicates, (
        f"dev-config файлы идентичны config/ (U-46): {duplicates}. "
        f"Dev-режим = docker-compose.dev.yml override, дубли запрещены."
    )
    logger.info("[IMP:9][nginx_parity] 0 дублей dev-config/config — PASS")

    found = _print_ldd_trajectory(caplog, "test_nginx_dev_config_no_duplicates")

    assert found, "No IMP:9 log found — LDD violation"

    print("[IMP:9][nginx_parity] 0 дублей dev-config/config — PASS")


# 🧪 TRAP[TEST] · nginx_dev_compose_valid · Gate · Regression: docker-compose.dev.yml невалиден
# · Scenario: docker compose -f base.yml -f dev.yml config --quiet → exit 0
# · Last fail: N/A (новый dev.yml, DevPlan 116 D3)
# · Remove if: dev-оверрайд удаляется
@pytest.mark.gate
def test_nginx_dev_compose_valid(caplog) -> None:
    """docker compose base+dev config валиден (dry-run, T9 step 3)."""
    caplog.set_level(logging.INFO)
    nginx_dir = repo_root() / "core" / "modules" / "nginx"
    base = nginx_dir / "docker-compose.base.yml"
    dev = nginx_dir / "docker-compose.dev.yml"

    if not shutil.which("docker"):
        pytest.skip("docker not available — compose dry-run skipped (infra unavailability)")
    if not dev.is_file():
        pytest.fail("docker-compose.dev.yml not found (D3, DevPlan 116 B7 T5)")

    result = subprocess.run(
        ["docker", "compose", "--profile", "nginx", "-f", str(base), "-f", str(dev), "config", "--quiet"],
        capture_output=True,
        text=True,
        timeout=60,
    )
    print(f"[IMP:8][nginx_compose] docker compose config exit={result.returncode}")
    assert result.returncode == 0, f"docker compose base+dev config FAILED: {result.stderr[:500]}"
    logger.info("[IMP:9][nginx_compose] docker compose base+dev config valid — PASS")

    found = _print_ldd_trajectory(caplog, "test_nginx_dev_compose_valid")

    assert found, "No IMP:9 log found — LDD violation"

    print("[IMP:9][nginx_compose] docker compose base+dev config valid — PASS")
