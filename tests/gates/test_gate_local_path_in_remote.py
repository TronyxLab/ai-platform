#!/usr/bin/env python3
# GREP_SUMMARY: gate local-path-remote passthrough build-ssh-cmd remote-args FL6 forbidden-path-vars AGE_SECRET_KEY_FILE PLATFORM_ROOT NODE_YAML line-scan
# STRUCTURE: ▶ scan makefiles/*.mk + core/entrypoints/*.sh + core/internal/bootstrap/*.sh → ○ line-scan: construct(PASSTHROUGH_ARGS+=/passthrough_args/execute_remote_/build_*_ssh_cmd) ∧ forbidden-path-var($VAR/${VAR}/--age-secret-key-file) → ⊕ violations → ⛔ RED (allowlist пуст) → ⎋ R5 negatives (probe в tmp_path)
# region MODULE_CONTRACT
## @purpose  Gate FL6 (DevPlan 123 T9): локальные пути НИКОГДА не уходят в remote-аргументы
##           (passthrough / build_*_ssh_cmd / execute_remote_*). Строка, содержащая ОДНОВРЕМЕННО
##           (а) passthrough-конструкцию и (б) переменную-путь из запрещённого списка в форме
##           $VAR / ${VAR} или флаг --age-secret-key-file → RED (allowlist пуст).
## @scope    Line-scan по образцу test_gate_timeout_literals.py (workflow-скан): makefiles/*.mk,
##           core/entrypoints/*.sh, core/internal/bootstrap/*.sh (build-ssh-cmd.sh, remote-cmd.sh,
##           node-lifecycle.sh, bootstrap.sh, node-update.sh, converge.sh).
##           ⚠️ НЕ дублирует test_gate_no_hardcoded_local_paths.py — тот про hardcoded пути-литералы
##           в Python-коде (/Users/..., /opt/platform/); этот — про ФОРВАРД ПЕРЕМЕННЫХ в remote-аргументы
##           (passthrough/build_ssh_cmd) — другой скоуп.
## @invariants
##   - RED: одна строка = (а) passthrough-конструкция И (б) $AGE_SECRET_KEY_FILE/$PLATFORM_ROOT/
##     $NODE_CONFIGS_DIR/$PROJECTS_BASE/$NODE_YAML (в форме $VAR/${VAR}) или флаг --age-secret-key-file
##   - allowlist ПУСТ — легитимных кейсов форварда локальных путей в remote НЕТ (DevPlan 123 T9)
##   - НЕ RED: локальное чтение ключа (node-update.sh:52, bootstrap.sh:50 — флаг без конструкции
##     на строке), export PLATFORM_ROOT на remote-стороне (build-ssh-cmd.sh:45 — имя переменной БЕЗ $),
##     --node-yaml с remote-путём (remote_node_yaml=/opt/node-configs/...), make → локальный entrypoint
##     (bootstrap.mk:30/54 — флаг без конструкции на строке), NODE_YAML_PATH (производная переменная —
##     вне списка DevPlan, граница регекса исключает суффикс _PATH)
## @rationale FL6 (false-lead 6, RC 121): класс «локальный путь уходит в remote passthrough» закрыт
##            точечно TRAP[BUG] 2026-08-03 (bootstrap.sh:48-50 — --age-secret-key-file уходил в remote
##            passthrough: локальный путь читался НА VPS). Гейт фиксирует класс навсегда: форвард
##            пути в remote-команду — RED, allowlist пуст (легитимных кейсов нет).
## @changes 2026-08-03 | DevPlan 123 T9 — Created (FL6)
# endregion MODULE_CONTRACT

import logging
import re
from pathlib import Path

import pytest

from tests.conftest import ldd_trajectory
from tests.helpers.gate_helpers import repo_root

logger = logging.getLogger(__name__)

ROOT = repo_root()

# ── (а) passthrough-конструкции (DevPlan 123 T9) ──────────────────────────────
# execute_remote_ покрывает execute_remote_update/execute_remote_converge/execute_remote_reconcile;
# build_ssh_cmd — также substring build_update_ssh_cmd/build_converge_ssh_cmd.
_PASSTHROUGH_CONSTRUCT = re.compile(
    r"PASSTHROUGH_ARGS\+=|passthrough_args|execute_remote_|"
    r"build_update_ssh_cmd|build_converge_ssh_cmd|build_ssh_cmd"
)

# ── (б) запрещённые переменные-пути (локальные значения) + флаг-ловушка ────────
_FORBIDDEN_PATH_VARS = ("AGE_SECRET_KEY_FILE", "PLATFORM_ROOT", "NODE_CONFIGS_DIR", "PROJECTS_BASE", "NODE_YAML")
# Форма $VAR / ${VAR}: после имени — } (для ${VAR}) или НЕ-идентификатор (для $VAR/;..."$/конец строки).
# Граница исключает производные имена (NODE_YAML_PATH не матчится NODE_YAML — суффикс _ в [A-Za-z0-9_]).
_FORBIDDEN_VAR_REF = re.compile(
    r"\$(?:\{)?(AGE_SECRET_KEY_FILE|PLATFORM_ROOT|NODE_CONFIGS_DIR|PROJECTS_BASE|NODE_YAML)"
    r"(?:\}|[^A-Za-z0-9_]|$)"
)
_FORBIDDEN_FLAG = "--age-secret-key-file"

# ⚠️ allowlist ПУСТ (DevPlan 123 T9) — легитимных кейсов форварда локальных путей в remote НЕТ.
_ALLOWLISTED_LINES: set[str] = set()

# Scan-scope: относительные glob'ы от repo root (line-scan, образец timeout_literals workflow-скана)
_SCAN_GLOBS = (
    "makefiles/*.mk",
    "core/entrypoints/*.sh",
    "core/internal/bootstrap/*.sh",
)


# region FUNC__line_has_forbidden_path
## @purpose  Проверить, что строка содержит запрещённую переменную-путь ($VAR/${VAR}) или флаг --age-secret-key-file
## @io  input: line → output: bool
## @complexity O(L) — линейный поиск по строке
def _line_has_forbidden_path(line: str) -> bool:
    if _FORBIDDEN_FLAG in line:
        return True
    return _FORBIDDEN_VAR_REF.search(line) is not None
# endregion FUNC__line_has_forbidden_path


# region FUNC__scan_violations
## @purpose  Line-scan: passthrough-конструкция ∧ запрещённая переменная-путь на ОДНОЙ строке → RED
## @io  input: root (None = repo scan-scope; tmp_path = R5-пробы, Zero Hardcode Rule)
##       → output: list[(rel_path, lineno, line)]
## @complexity O(F * L) — линейный скан строк по файлам скоупа
def _scan_violations(root: Path | None = None) -> list[tuple[str, int, str]]:
    violations: list[tuple[str, int, str]] = []
    base = ROOT if root is None else root
    if root is None:
        files: list[Path] = []
        for glob_pat in _SCAN_GLOBS:
            files.extend(sorted(base.glob(glob_pat)))
    else:
        # R5-пробы во tmp_path: сканируем все файлы временного корня (Zero Hardcode Rule,
        # устраняет xdist-race с позитивным сканером — паттерн DevPlan 119 H)
        files = sorted(base.rglob("*"))

    for path in files:
        if not path.is_file():
            continue
        rel = path.relative_to(ROOT).as_posix() if root is None else path.relative_to(base).as_posix()
        try:
            lines = path.read_text(errors="replace").splitlines()
        except OSError:
            logger.warning("[IMP:7][scan] Cannot read %s", rel)
            continue
        for i, line in enumerate(lines, 1):
            if not _PASSTHROUGH_CONSTRUCT.search(line):
                continue
            if not _line_has_forbidden_path(line):
                continue
            if line.strip() in _ALLOWLISTED_LINES:
                continue
            violations.append((rel, i, line.strip()))
            logger.warning("[IMP:7][scan][local-path-remote] %s:%d — %s", rel, i, line.strip())
    return violations
# endregion FUNC__scan_violations


# region FUNC_test_no_local_path_in_remote_passthrough
@pytest.mark.gate
@ldd_trajectory
# 🧪 TRAP[TEST] · 2026-08-03 · REGRESSION · FL6 — локальные пути не уходят в remote-аргументы (DevPlan 123 T9)
# · Scenario: строка passthrough/build_*_ssh_cmd/execute_remote_* содержит $AGE_SECRET_KEY_FILE/PLATFORM_ROOT/
# ·   NODE_CONFIGS_DIR/PROJECTS_BASE/NODE_YAML или флаг --age-secret-key-file → RED
# · Last fail: RC 121 — --age-secret-key-file уходил в remote passthrough (bootstrap.sh:48-50,
# ·   локальный путь читался НА VPS; false-lead 6)
# · Remove if: passthrough/build_*_ssh_cmd каналы удаляются (тогда и гейт не нужен)
def test_no_local_path_in_remote_passthrough(caplog) -> None:
    """FL6: ни одна строка passthrough/build_*_ssh_cmd/execute_remote_* не форвардит локальный путь в remote."""
    caplog.set_level(logging.INFO)
    violations = _scan_violations()

    if violations:
        for rel, lineno, line in violations:
            logger.error("[IMP:10][local-path-remote] %s:%d %s", rel, lineno, line)
        pytest.fail(
            f"Локальные пути в remote-аргументах ({len(violations)}):\n"
            + "\n".join(f"  - {rel}:{lineno} {line}" for rel, lineno, line in violations)
            + "\n\nПравило (DevPlan 123 T9, FL6): remote-команды НИКОГДА не получают локальные пути. "
            "Ключ/секрет читай ЛОКАЛЬНО, в remote передавай КОНТЕНТ (--age-secret-key / AGE_SECRET_KEY env), "
            "не путь. Allowlist пуст — легитимных кейсов нет."
        )

    logger.info("[IMP:9][local-path-remote] PASS: 0 локальных путей в remote-аргументах (passthrough/build_ssh_cmd)")
# endregion FUNC_test_no_local_path_in_remote_passthrough


# region FUNC_test_r5_negative_passthrough_path_var_detected
@pytest.mark.gate
@ldd_trajectory
# 🧪 TRAP[TEST] · 2026-08-03 · NEGATIVE (R5) · PASSTHROUGH_ARGS+=("${AGE_SECRET_KEY_FILE}") детектится (FL6)
# · Scenario: probe-файл (tmp_path, Zero Hardcode Rule) с PASSTHROUGH_ARGS+=("${AGE_SECRET_KEY_FILE}")
# ·   → сканер ловит (конструкция PASSTHROUGH_ARGS+= ∧ переменная AGE_SECRET_KEY_FILE)
# · Last fail: RC 121 false-lead 6 — AGE_SECRET_KEY_FILE как аргумент (не ключ-контент) в passthrough
# · Remove if: гейт локальный-путь→remote отменяется
def test_r5_negative_passthrough_path_var_detected(caplog, tmp_path) -> None:
    """R5 negative: PASSTHROUGH_ARGS+=("${AGE_SECRET_KEY_FILE}") (исходный вход FL6) детектится."""
    import textwrap

    probe = tmp_path / "_gate_probe_pass.sh"
    probe.write_text(
        textwrap.dedent(
            """\
            #!/usr/bin/env bash
            PASSTHROUGH_ARGS+=("${AGE_SECRET_KEY_FILE}")
            """
        )
    )
    try:
        hits = [v for v in _scan_violations(root=tmp_path) if "_gate_probe_pass" in v[0]]
        assert hits, 'R5 FAIL: PASSTHROUGH_ARGS+=("${AGE_SECRET_KEY_FILE}") не обнаружен (исходный вход FL6)'
        logger.info("[IMP:9][local-path-remote][R5][passthrough] PASS: probe %s:%d %s", *hits[0])
    finally:
        probe.unlink(missing_ok=True)
# endregion FUNC_test_r5_negative_passthrough_path_var_detected


# region FUNC_test_r5_negative_remote_flag_local_path_detected
@pytest.mark.gate
@ldd_trajectory
# 🧪 TRAP[TEST] · 2026-08-03 · NEGATIVE (R5) · execute_remote_converge --age-secret-key-file "${PLATFORM_ROOT}/x" детектится (FL6)
# · Scenario: probe-файл (tmp_path) с execute_remote_converge ... --age-secret-key-file "${PLATFORM_ROOT}/x"
# ·   → сканер ловит (конструкция execute_remote_ ∧ флаг --age-secret-key-file + ${PLATFORM_ROOT})
# · Last fail: RC 121 false-lead 6 — путь AGE-ключа как remote-флаг (ловушка node-lifecycle.sh:28,
# ·   удалена DevPlan 123 T9)
# · Remove if: гейт локальный-путь→remote отменяется
def test_r5_negative_remote_flag_local_path_detected(caplog, tmp_path) -> None:
    """R5 negative: execute_remote_* с --age-secret-key-file "${PLATFORM_ROOT}/x" детектится."""
    import textwrap

    probe = tmp_path / "_gate_probe_remote_flag.sh"
    probe.write_text(
        textwrap.dedent(
            """\
            #!/usr/bin/env bash
            execute_remote_converge "node1" --age-secret-key-file "${PLATFORM_ROOT}/age-key.txt"
            """
        )
    )
    try:
        hits = [v for v in _scan_violations(root=tmp_path) if "_gate_probe_remote_flag" in v[0]]
        assert hits, 'R5 FAIL: execute_remote_converge --age-secret-key-file "${PLATFORM_ROOT}/x" не обнаружен'
        logger.info("[IMP:9][local-path-remote][R5][remote-flag] PASS: probe %s:%d %s", *hits[0])
    finally:
        probe.unlink(missing_ok=True)
# endregion FUNC_test_r5_negative_remote_flag_local_path_detected
