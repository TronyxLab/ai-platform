# GREP_SUMMARY: test-make-contract gate make-contract .PHONY-recipe dry-run backup-restore-matrix restart-soft nginx-parity restart-consistency restart-hard
# STRUCTURE: ▶ модули(docker) → ◇ .PHONY-рецепты (0 пустых) → ◇ make -n dry-run (exit 0) → ◇ backup/restore матрица D1 → ◇ restart soft → ◇ restart-consistency (root/module.mk/manifest/platform-secrets) → ◇ nginx dev-config dup-детекция → ⎋ verdict
# region MODULE_CONTRACT
## @purpose  Gate-тест make-контракта модулей (DevPlan 116 B7 T9, U-25): 0 пустых .PHONY,
##           dry-run всех таргетов, матрица backup/restore (D1), restart = soft (stop start),
##           restart-консистентность (F7: root Makefile, module.mk restart-hard, manifest,
##           platform-secrets systemd-exclusion), nginx dev-config не дублирует config/ (U-46).
## @scope    Все 13 docker-модулей (platform-secrets — systemd, вне скоупа) + nginx compose dry-run
## @invariants
##   - Каждый таргет из .PHONY имеет рецепт (0 пустых .PHONY — U-25 не возвращается)
##   - make -n <target> exit 0 (dry-run без реального docker)
##   - backup/restore объявлены РОВНО у postgres, backup-cron, hermes-agent (матрица D1)
##   - restart рецепт содержит stop start (soft, не down) — AGENTS.md:167-контракт
##   - F7 (118, D13): restart-проверки консолидированы из test_restart_consistency.py
##     (удалён, 257 LOC, был invisible — без @pytest.mark.gate); все тесты активированы маркером
##   - nginx: 0 файлов dev-config с содержимым, идентичным config/ (dup-детекция U-46)
##   - docker compose -f docker-compose.base.yml -f docker-compose.dev.yml config --quiet → exit 0
## @rationale Бриф: «для каждого модуля make -n restore/restart/backup не падает» — адаптирован
##   под D1 (stateless — таргеты отсутствуют, «No rule to make target» — ожидаемое поведение).
## @changes 2026-08-01 | Created (DevPlan 116 B7 T9)
## @changes 2026-08-02 | F7 (DevPlan 118 D13): restart-consistency консолидирован из
##   test_restart_consistency.py (удалён, не регистрируется в manifest — G1 пересечение)
# endregion MODULE_CONTRACT

import logging
import re
import shutil
import subprocess
from pathlib import Path

import pytest
from _conftest.ldd import _print_ldd_trajectory, ldd_trajectory

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
def test_no_empty_phony_targets(caplog, tmp_path) -> None:
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
        combined_path = tmp_path / f"{mod}.combined.mk"
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
# 📝 TRAP[DEBT] · 2026-08-01 · MED · FIXED 2026-08-02 (DevPlan 119 C): flaky unlink под xdist —
# ·   .combined.mk.tmp писался в фиксированный путь модуля и unlink() падал с FileNotFoundError
# ·   при параллельном прогоне. Фикс (как и предписывал TRAP): tmp_path-фикстура вместо
# ·   core/modules/<mod>/.combined.mk.tmp — 3 функции мигрированы (no_empty_phony, dry_run, restart).
@pytest.mark.gate
def test_make_n_dry_run_all_targets(caplog, tmp_path) -> None:
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
        combined_path = tmp_path / f"{mod}.combined.mk"
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
def test_restart_soft_semantics(caplog, tmp_path) -> None:
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
        combined_path = tmp_path / f"{mod}.combined.mk"
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


# ══════════════════════════════════════════════════════════════════════════════
# Restart-консистентность (консолидировано из test_restart_consistency.py — DevPlan 118 F7)
# ══════════════════════════════════════════════════════════════════════════════
# F7 (D13): test_restart_consistency.py (257 LOC, НЕ зарегистрирован — invisible) удалён;
# проверки перенесены сюда (зарегистрированный gate-файл). Все тесты получили
# @pytest.mark.gate — в исходном файле маркера не было (потому и не исполнялись).


# region FUNC_extract_make_target
## @purpose  Extract body of a make target from Makefile content (preserving indentation)
## @io
##   @input  content: str — full Makefile content
##   @input  target: str — target name with colon, e.g. "restart:"
##   @output str|None — extracted target body, or None if not found
## @complexity O(n) linear scan
def extract_make_target(content: str, target: str) -> str | None:
    """Extract the body of a make target from Makefile content."""
    lines = content.split("\n")
    in_target = False
    target_lines = []
    for line in lines:
        if line.strip().startswith(target):
            in_target = True
            target_lines.append(line)
        elif in_target:
            # Lines starting with tab or 4 spaces are part of the target
            if line.startswith(("\t", "    ", "\t@", "    @")) or line.strip() == "":
                target_lines.append(line)
            else:
                break
    return "\n".join(target_lines) if target_lines else None


# endregion FUNC_extract_make_target


# region FUNC_test_root_makefile_restart_is_soft
## @purpose  Verify root Makefile restart target uses soft restart (stop + start)
## @io
##   @input  None (reads Makefile from repo_root())
##   @output None (asserts)
## @complexity O(n) on Makefile
# 🧪 TRAP[TEST] · F7 (DevPlan 118) · Regression: root Makefile restart должно быть soft (D4→A)
# · Scenario: root Makefile restart target
# · Last fail: N/A (D4→A converged semantics; тест был invisible в test_restart_consistency.py)
# · Remove if: restart semantics changes to require hard restart
@pytest.mark.gate
@ldd_trajectory
def test_root_makefile_restart_is_soft(caplog) -> None:
    """Root Makefile restart target must use 'stop && start', not 'down && up -d'."""
    caplog.set_level(logging.INFO)
    makefile = repo_root() / "Makefile"
    content = makefile.read_text()
    # restart target is now in makefiles/modules.mk after include-split
    modules_mk = repo_root() / "makefiles" / "modules.mk"
    if modules_mk.is_file():
        content += "\n" + modules_mk.read_text()

    # Find the restart target section
    restart_section = extract_make_target(content, "restart:")
    assert restart_section is not None, "restart target not found in Makefile/makefiles/modules.mk"
    print(f"  Root Makefile restart section:\n{restart_section[:300]}")

    # Must contain stop && start for soft restart
    assert "stop" in restart_section and "start" in restart_section, (
        f"Root Makefile restart should use 'stop && start', found: {restart_section[:200]}"
    )
    assert "down" not in restart_section and "up -d" not in restart_section, (
        f"Root Makefile restart should NOT use 'down && up -d', found: {restart_section[:200]}"
    )
    logger.info("[IMP:9][gate][restart] Root Makefile uses soft restart (stop && start) ✓")

    # Must NOT use hard restart (down + up -d)
    assert not bool(re.search(r"docker\s+compose\s+down\b", restart_section, re.IGNORECASE)), (
        "Root Makefile restart should NOT use 'docker compose down' command"
    )
    logger.info("[IMP:9][gate][restart] Root Makefile: no hard restart command found ✓")


# endregion FUNC_test_root_makefile_restart_is_soft


# region FUNC_test_module_mk_restart_hard_exists
## @purpose  Verify module.mk has restart-hard target (hard restart with --force-recreate);
##           regular restart is inherited soft from Makefile.common
## @io
##   @input  None (reads module.mk from repo_root()/core/templates/)
##   @output None (asserts)
## @complexity O(n) on module.mk
# 🧪 TRAP[TEST] · F7 (DevPlan 118) · Regression: module template restart-hard target removed
# · Scenario: core/templates/module.mk restart-hard target
# · Last fail: N/A (D4→A renamed hard restart to restart-hard; тест был invisible)
# · Remove if: module template restart-hard semantics changes
@pytest.mark.gate
@ldd_trajectory
def test_module_mk_restart_hard_exists(caplog) -> None:
    """Module template must have restart-hard target with --force-recreate."""
    caplog.set_level(logging.INFO)
    module_mk = repo_root() / "core" / "templates" / "module.mk"
    content = module_mk.read_text()

    # Regular restart should NOT be overridden in module.mk (inherited soft from Makefile.common)
    restart_section = extract_make_target(content, "restart:")
    assert restart_section is None, "module.mk should NOT override restart (inherited soft from Makefile.common)"
    logger.info("[IMP:9][gate][restart] module.mk: restart NOT overridden (soft from Makefile.common) ✓")

    # restart-hard must exist with --force-recreate
    restart_hard_section = extract_make_target(content, "restart-hard:")
    assert restart_hard_section is not None, (
        "restart-hard target not found in module.mk. Expected hard restart with --force-recreate."
    )
    assert "--force-recreate" in restart_hard_section, (
        f"module.mk restart-hard should use '--force-recreate', found: {restart_hard_section[:200]}"
    )
    assert "down" in restart_hard_section and "up -d" in restart_hard_section, (
        f"module.mk restart-hard should use 'down && up -d', found: {restart_hard_section[:200]}"
    )
    logger.info("[IMP:9][gate][restart] module.mk: restart-hard found with --force-recreate ✓")


# endregion FUNC_test_module_mk_restart_hard_exists


# region FUNC_test_no_soft_restart_in_docker_makefiles
## @purpose  Verify no Docker-service Makefile uses 'docker compose restart' (soft)
## @io
##   @input  None (scans core/modules/*/Makefile)
##   @output None (asserts)
## @complexity O(n*m) where n=makefiles, m=lines per file
# 🧪 TRAP[TEST] · F7 (DevPlan 118) · Regression: soft restart in module Makefiles
# · Scenario: all core/modules/*/Makefile restart targets
# · Last fail: N/A (new test; был invisible в test_restart_consistency.py)
# · Remove if: any module legitimately needs soft restart
@pytest.mark.gate
@ldd_trajectory
def test_no_soft_restart_in_docker_makefiles(caplog) -> None:
    """No Docker-service Makefile should use 'docker compose restart' (soft)."""
    caplog.set_level(logging.INFO)
    makefiles = list(repo_root().glob("core/modules/*/Makefile"))
    # Exclude platform-secrets (uses systemd, not Docker)
    makefiles = [m for m in makefiles if "platform-secrets" not in str(m)]

    violations = []
    for mf in makefiles:
        content = mf.read_text()
        # Look for soft restart pattern in the restart target
        if "restart:" in content:
            restart_sec = extract_make_target(content, "restart:")
            if restart_sec:
                # Parse COMPOSE_CMD for command
                has_soft = bool(re.search(r"\$\{?COMPOSE_CMD\}?\s+restart\b", restart_sec))
                has_soft = has_soft or bool(re.search(r"docker\s+compose\s+restart\b", restart_sec))
                if has_soft:
                    violations.append(str(mf.relative_to(repo_root())))
                    print(f"  SOFT restart in {mf.relative_to(repo_root())}: {restart_sec[:150]}")

    checked = len(makefiles)
    logger.info("[IMP:9][gate][restart] Checked %d Docker module Makefiles for soft restart", checked)
    assert len(violations) == 0, f"Found {len(violations)} Makefiles using soft restart: {violations}"
    logger.info("[IMP:9][gate][restart] All %d module Makefiles use hard restart ✓", checked)


# endregion FUNC_test_no_soft_restart_in_docker_makefiles


# region FUNC_test_manifest_restart_is_soft
## @purpose  Verify entrypoint-manifest.yaml describes restart as soft restart (stop + start)
## @io
##   @input  None (reads entrypoint-manifest.yaml)
##   @output None (asserts)
## @complexity O(1) YAML parse + string search
# 🧪 TRAP[TEST] · F7 (DevPlan 118) · Regression: manifest restart mechanism drift
# · Scenario: entrypoint-manifest.yaml lifecycle.restart delegates_to
# · Last fail: N/A (D4→A converged to soft; тест был invisible)
# · Remove if: manifest restart mechanism intentionally reverts to hard
@pytest.mark.gate
@ldd_trajectory
def test_manifest_restart_is_soft(caplog) -> None:
    """entrypoint-manifest.yaml must describe restart as soft restart (stop + start)."""
    caplog.set_level(logging.INFO)
    import yaml

    manifest_path = repo_root() / "core" / "entrypoint-manifest.yaml"
    content = manifest_path.read_text()
    manifest = yaml.safe_load(content)
    lifecycle = manifest.get("lifecycle", [])
    restart_entry = None
    for entry in lifecycle:
        if entry.get("make_target") == "restart":
            restart_entry = entry
            break

    assert restart_entry is not None, "restart entry not found in lifecycle"
    delegates_to = restart_entry.get("delegates_to", "")
    description = restart_entry.get("description", "").lower()
    assert "stop &&" in delegates_to and "start" in delegates_to, (
        f"restart delegates_to should contain 'stop && start', got: {delegates_to}"
    )
    assert "soft" in description, f"restart description should mention 'soft', got: {restart_entry.get('description')}"
    # Must NOT reference 'up -d' for soft restart
    assert "up -d" not in delegates_to, (
        f"restart delegates_to should NOT contain 'up -d' (that's hard), got: {delegates_to}"
    )

    logger.info("[IMP:9][gate][restart] Manifest restart mechanism: soft restart verified ✓")


# endregion FUNC_test_manifest_restart_is_soft


# region FUNC_test_platform_secrets_excluded
## @purpose  platform-secrets uses systemd restart, not Docker — verify it's NOT flagged as Docker soft restart
## @io
##   @input  None (reads platform-secrets/Makefile)
##   @output None (asserts)
## @complexity O(n) on Makefile
# 🧪 TRAP[TEST] · F7 (DevPlan 118) · Regression: false positive on platform-secrets
# · Scenario: platform-secrets/Makefile restart (systemd via module-system.mk include, not Docker)
# · Last fail: N/A (new test; был invisible в test_restart_consistency.py)
# · Remove if: platform-secrets migrates to Docker compose restart
@pytest.mark.gate
@ldd_trajectory
def test_platform_secrets_excluded(caplog) -> None:
    """platform-secrets Makefile includes module-system.mk (systemd restart) — should NOT be flagged as Docker."""
    caplog.set_level(logging.INFO)
    ps_makefile = repo_root() / "core" / "modules" / "platform-secrets" / "Makefile"
    if ps_makefile.exists():
        content = ps_makefile.read_text()
        # Verify file includes module-system.mk (provides systemd restart via template)
        assert "include ../../templates/module-system.mk" in content, (
            "platform-secrets Makefile should include module-system.mk"
        )
        logger.info("[IMP:9][gate][restart] platform-secrets Makefile exists, verifying no Docker restart")
        # Load the included template to verify restart target is provided
        template_path = ps_makefile.parent.parent.parent / "templates" / "module-system.mk"
        if template_path.exists():
            template_content = template_path.read_text()
            assert "restart:" in template_content, "module-system.mk should define restart target"
        # It should NOT reference docker compose restart anywhere
        has_docker = bool(re.search(r"docker\s+compose\s+restart\b", content))
        assert not has_docker, "platform-secrets should NOT use docker compose restart"
        logger.info("[IMP:9][gate][restart] platform-secrets excluded from Docker restart check ✓")
    else:
        logger.info("[IMP:9][gate][restart] platform-secrets Makefile not found (skipped) ✓")


# endregion FUNC_test_platform_secrets_excluded
