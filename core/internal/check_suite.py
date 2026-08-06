#!/usr/bin/env python3
# GREP_SUMMARY: check-suite, executor, manifest, diagnostic, gate-portal, check-diff, fingerprint, cache, list, xdist, junit-merge, allow-no-tests, non-blocking
# STRUCTURE: ▶ load_manifest → ◇ validate_manifest → ○ run ┌diagnostic: fix→fingerprint→cache?→static∥+pytest→report→cache! | gate: steps→fail-fast/accumulate→junit-merge | diff: changed→pre-commit/ruff/pytest┐ → ⊕ list → ⎋ exit code
# region MODULE_CONTRACT
## @purpose  Единый executor набора проверок из SoT-манифеста core/check-suite.yaml (DevPlan 120 §3.2).
##           Три режима: (1) diagnostic — `make check` (экс-preflight): fix-фаза → fingerprint-кэш →
##           static-чеки в потоках + pytest-чеки последовательно с xdist → единый отчёт;
##           (2) gate — `make gate MODE=fast|full|ci-docker`: канонический арбитр, порядок шагов из
##           манифеста (паритет ci.mk), fail-fast (fast) / accumulate + junit-merge (full/ci-docker),
##           БЕЗ кэша; (3) diff — `make check-diff`: узкий diff-таргет (pre-commit --files + ruff по
##           diff + pytest изменённых test-файлов), без кэша.
## @scope    core/internal/check_suite.py — stdlib-only, Python 3.10+. PEP 420 namespace package.
##           Запуск: python -m core.internal.check_suite run|list|fingerprint.
##           Потребители: makefiles/repair.mk (check/check-diff), makefiles/ci.mk (gate),
##           core/internal/preflight.py (тонкий deprecated-фасад).
## @invariants
##   - Манифест — единственный источник состава проверок; НИКАКИХ hardcoded списков в executor'е
##   - Diagnostic: fix-фаза (fix-gate pre-step + tier=fix чеков) ПОСЛЕДОВАТЕЛЬНО до fingerprint;
##     fingerprint вычисляется ПОСЛЕ fix-фазы (мутация файлов автоправкой)
##   - Кэш применяется ТОЛЬКО к diagnostic; gate/diff — без кэша; replay только при байт-идентичном
##     дереве И зелёном последнем прогоне; CHECK_CACHE=0/--no-cache — без чтения и записи
##   - pytest-чеки диагностики строго последовательно (1 pytest с -n auto за раз — решение b);
##     static-чеки параллельно в потоках (workers)
##   - xdist: прямые pytest-команды получают -n auto (если spec.xdist и xdist доступен);
##     `make test MARKER=...`/test_runner-команды — xdist внутри test_runner (Wave 1); TEST_NO_XDIST=1 отключает
##   - gate: allow_no_tests (exit 5 → PASS), non_blocking (провал не роняет gate), PYTEST_NO_ESCALATION=1
##     на pytest-шагах, PROJECT → -k для project_filter-чеков (только прямые pytest-команды — паритет ci.mk),
##     удаление tests/report*.xml перед прогоном, junit-merge через tests/merge_junit.py (full/ci-docker)
##   - Выход: 0 = зелёный, 1 = провалы, 2 = ошибка конфигурации/использования
## @rationale DevPlan 120 (решения пользователя 2026-08-02): B — SoT-манифест + xdist везде +
##            глобальный fingerprint-кэш (per-check watch-scope кэш брифа отклонён); b — pytest-чеки
##            последовательно, static параллельно (без переподписки 36+ воркеров на 12 ядер);
##            a — check-diff = честный diff-таргет без кэша. Инвариант «preflight НЕ заменяет gate»
##            переформулирован системно: два executor'а одного манифеста — диагностический
##            акселератор, канонический арбитр; дрейф невозможен конструктивно.
## @changes 2026-08-02 | Created (DevPlan 120 Wave 1-4)
## @changes 2026-08-03 | DevPlan 124 T2c (A2+): _docker_suite_lock — процессный flock
##            tests/.docker-suite.lock (зеркало test_runner, ЕДИНЫЙ lock-файл машины);
##            _run_cmd(docker_lock=True) оборачивает docker-чеки (gates-docker,
##            predeploy-docker, spec.docker: true) — межсессионная сериализация (F4)
# endregion MODULE_CONTRACT

from __future__ import annotations

# region IMPORTS
import argparse
import concurrent.futures
import contextlib
import hashlib
import json
import logging
import os
import re
import shlex
import signal
import subprocess
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path

# endregion IMPORTS

# region CONSTANTS

logger = logging.getLogger(__name__)

# Root of the ai-platform project (3 levels up from core/internal/check_suite.py)
_PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent

_MANIFEST_PATH = _PROJECT_ROOT / "core" / "check-suite.yaml"

_DEFAULT_MAX_WORKERS = 6

_VALID_TIERS = ("fix", "static", "pytest")
_VALID_GATE_MODES = ("fast", "full", "ci-docker")

# Checks that are диагностика-only (diagnostic: false) — заданы в манифесте явно,
# здесь — только константы валидации для consistency-гейта (не список проверок)
_DIAGNOSTIC_FALSE_DEFAULT_IDS = ("lint", "check-file-lines", "smoke", "component", "predeploy-docker")

_CACHE_FILENAME = "check-cache.json"

# Исполняемые, резолвящиеся в .venv при наличии (команды манифеста пишутся нейтрально:
# pytest/pre-commit/ruff/python3 — на машине разработчика живут в .venv)
_VENV_RESOLVABLE = ("pytest", "pre-commit", "ruff", "python3")

# Пути/базлайны, исключаемые из fingerprint-дерева (вдобавок к gitignore)
_FINGERPRINT_EXCLUDE_PARTS = (".venv", "__pycache__", ".pytest_cache", "node_modules", ".git")
# report*.xml + .test_counter.json + flock-локи (.test_counter.json.lock — артефакты тест-прогонов)
_FINGERPRINT_EXCLUDE_RE = re.compile(r"(^|/)(tests/report[^/]*\.xml|\.test_counter\.json(\.lock)?)$")

# ⚠️ TRAP[DECISION] · 2026-08-02 · — · fix-gate — built-in pre-step диагностики, НЕ запись манифеста
# · Rejected: добавить fix-gate как tier=fix запись в check-suite.yaml
# · Reason: DevPlan 120 §3.1 манифест содержит только pre-commit в tier=fix; fix-gate (мутирующая
#   автофаза) — преемник Phase 1 прежнего preflight.py, выполняется ДО чеков манифеста и ДО
#   fingerprint (иначе автоправка ломала бы replay). Состав проверок (диагностика) — манифест;
#   автофикс-фаза — оркестрационная преамбула executor'а, не «проверка».
# · Rev: если появятся дополнительные fix-фазы (>1) — перенести их в манифест как tier=fix записи.
_FIX_GATE_PRE_STEP = "make fix-gate"

# ⚠️ TRAP[DECISION] · 2026-08-02 · — · fingerprint — чистый Python вместо `xargs -0 sha256sum`
# · Rejected: git ls-files -c -o --exclude-standard -z | xargs -0 sha256sum (DevPlan §3.4)
# · Reason: sha256sum отсутствует на macOS по умолчанию (только shasum -a 256); xargs-пайплайн
#   нестабилен между GNU/BSD coreutils. Эквивалент: один subprocess git ls-files + hashlib в Python
#   (тот же байт-набор дерева, тот же fingerprint-контракт).
# · Rev: если дерево вырастет >100k файлов и хеширование станет бутылочным горлышком → xargs -P.
_FINGERPRINT_EXTRA_FILES = ("core/check-suite.yaml", ".pre-commit-config.yaml", "pyproject.toml")

# endregion CONSTANTS


# region DATA_MODELS


@dataclass
class CheckSpec:
    """Нормализованная запись манифеста (одна проверка набора)."""

    id: str
    tier: str
    timeout: int
    gate_modes: list[str] = field(default_factory=list)
    diagnostic: bool = True
    xdist: bool = True
    sequential: bool = False
    allow_no_tests: bool = False
    non_blocking: bool = False
    junit: str | None = None
    project_filter: bool = False
    docker: bool = False
    cmd: str | None = None
    cmds: dict[str, str] | None = None

    # region FUNC_CheckSpec_RESOLVE_COMMAND
    # ⚠️ TRAP[BUG] · 2026-08-02 · P1 · Диагностический прогон МОЛЧА пропускал чеки с только cmds
    # · Symptom: `make check` отчитывался GREEN за 21s, но gates/static_audit/predeploy НЕ исполнялись
    # ·   (7/11 чеков; static_audit 3106 тестов отсутствовал в прогоне — ложный зелёный)
    # · Root: resolve_command(gate_mode=None) возвращал self.cmd=None для чеков с только cmds
    # ·   (gates/gates-docker/static_audit/predeploy) — diagnostic-ветка «если cmd None — пропустить»
    # · Fix: diagnostic-контекст резолвит канонический fast-вариант (cmds["fast"]) — паритет
    # ·   прежнего preflight Phase 3 (fast-выражения); run_diagnostic не может молча пропустить
    # ·   чек с резолвленной командой (assert-страховка отсутствует, но fast-фолбэк закрывает класс)
    # · Prevention: unit-тест test_resolve_command_diagnostic_falls_back_to_fast (регресс-гард)
    ## @purpose  Выбор команды чека: cmds[gate_mode] приоритетнее cmd (per-mode выражения РАЗНЫЕ:
    ##           gates fast/full, static_audit fast/full, predeploy fast/full — паритет ci.mk).
    ##           Диагностический контекст (gate_mode=None): канонический fast-вариант
    ##           (прежний preflight Phase 3 исполнял fast-выражения: gates без docker,
    ##           static_audit через test_runner, predeploy без docker) — cmd ИЛИ cmds["fast"].
    ## @io       ⇥ gate_mode: str | None (fast|full|ci-docker) → ⎋ str | None (команда или None)
    ## @complexity O(1)
    def resolve_command(self, gate_mode: str | None) -> str | None:
        """Resolve the command: cmds[gate_mode] wins; diagnostic falls back to cmds['fast']."""
        if gate_mode and self.cmds and gate_mode in self.cmds:
            return self.cmds[gate_mode]
        if self.cmd:
            return self.cmd
        if self.cmds:
            # Диагностика (gate_mode=None): fast-вариант — канонический diagnostic-набор.
            # Без этого чеки с ТОЛЬКО cmds (gates/static_audit/predeploy) МОЛЧА пропускались бы.
            return self.cmds.get("fast") or next(iter(self.cmds.values()), None)
        return None

    # endregion FUNC_CheckSpec_RESOLVE_COMMAND


@dataclass
class CheckOutcome:
    """Результат исполнения одного чека."""

    name: str
    exit_code: int
    stdout: str = ""
    stderr: str = ""
    duration_ms: float = 0.0
    auto_fixed: bool = False
    passed_no_tests: bool = False
    blocked: bool = False  # non_blocking провал (gate не роняет)

    @property
    def passed(self) -> bool:
        return self.exit_code == 0

    def error_summary(self, max_lines: int = 20) -> str:
        """Извлечь ключевые строки ошибок для отчёта (формат прежнего preflight)."""
        combined = (self.stderr + "\n" + self.stdout).strip()
        if not combined:
            return "(no output)"
        lines = combined.split("\n")
        key_lines: list[str] = []
        for line in lines:
            stripped = line.strip()
            if not stripped:
                continue
            lower = stripped.lower()
            if any(kw in lower for kw in ("fail", "error", "warning:", "could not", "unable", "ref", "undefined")):
                key_lines.append(stripped)
        if not key_lines:
            non_empty = [ln.strip() for ln in lines if ln.strip()]
            key_lines = non_empty[-max_lines:]
        return "\n".join(key_lines[:max_lines])


# endregion DATA_MODELS


# region MANIFEST_LOAD


# region FUNC_load_manifest
## @purpose  Загрузка SoT-манифеста core/check-suite.yaml (или явного пути для тестов).
## @io       ⇥ root: Path — корень проекта → ⎋ dict (распарсенный YAML)
## @complexity O(1) — чтение одного файла
def load_manifest(root: Path | None = None) -> dict:
    """Load the check-suite SoT manifest from disk."""
    root = root or _PROJECT_ROOT
    manifest_path = root / "core" / "check-suite.yaml"
    if not manifest_path.is_file():
        raise FileNotFoundError(f"check-suite manifest not found: {manifest_path}")
    import yaml

    try:
        with open(manifest_path) as f:
            data = yaml.safe_load(f)
    except yaml.YAMLError as exc:
        # T3.6 (DevPlan 116 B4): бизнес-ошибки → иерархия PlatformError (exit 3), НЕ bare ValueError
        from core.internal.shared.exceptions import ConfigParseError

        raise ConfigParseError(f"check-suite manifest YAML parse error: {exc}") from exc
    if not isinstance(data, dict):
        from core.internal.shared.exceptions import ConfigParseError

        raise ConfigParseError(f"check-suite manifest must be a mapping: {manifest_path}")
    logger.info("[IMP:7][load_manifest][io] Loaded manifest from %s", manifest_path)
    return data


# endregion FUNC_load_manifest


# region FUNC_validate_manifest
## @purpose  Структурная валидация манифеста (схема v1): id-формат/уникальность,
##           tier, timeout, gate_modes, cmd|cmds-покрытие, junit-уникальность ПО РЕЖИМАМ.
##           Возвращает список ошибок (пустой = валидно) — consistency-гейт и executor
##           используют одну и ту же функцию (fail-fast до запуска).
## @io       ⇥ manifest: dict → ⎋ list[str] ошибок (пустой = валидно)
## @complexity O(C) где C = число чеков
## @invariants
##   - id: ^[a-z0-9]+([-_][a-z0-9]+)*$ (kebab ИЛИ snake — static_audit каноничен, DevPlan §3.1)
##     и уникален
##   - tier ∈ {fix, static, pytest}; timeout > 0
##   - gate_modes ⊆ {fast, full, ci-docker} (отсутствие = диагностика-only)
##   - Для каждого gate-режима из gate_modes команда резолвится (cmd ИЛИ cmds[mode])
##   - junit-пути уникальны В ПРЕДЕЛАХ КАЖДОГО gate-режима (predeploy и predeploy-docker
##     делят tests/report-predeploy.xml намеренно — режимы fast/full vs ci-docker не пересекаются)
def validate_manifest(manifest: dict) -> list[str]:
    """Validate manifest schema v1; returns list of errors (empty = valid)."""
    errors: list[str] = []
    checks = manifest.get("checks", [])
    if not isinstance(checks, list) or not checks:
        return ["manifest.checks must be a non-empty list"]

    seen_ids: set[str] = set()
    # junit-пути по каждому gate-режиму (раздельные режимы могут делить путь — predeploy/predeploy-docker)
    junit_by_mode: dict[str, dict[str, str]] = {m: {} for m in _VALID_GATE_MODES}
    for i, c in enumerate(checks):
        prefix = f"checks[{i}]"
        if not isinstance(c, dict):
            errors.append(f"{prefix}: must be a mapping")
            continue
        cid = c.get("id", "")
        if not re.fullmatch(r"[a-z0-9]+([-_][a-z0-9]+)*", cid):
            errors.append(f"{prefix}: id={cid!r} не kebab/snake-case")
        if cid in seen_ids:
            errors.append(f"{prefix}: duplicate id={cid!r}")
        seen_ids.add(cid)

        tier = c.get("tier")
        if tier not in _VALID_TIERS:
            errors.append(f"{prefix} ({cid}): tier={tier!r} ∉ {_VALID_TIERS}")
        timeout = c.get("timeout", 0)
        if not isinstance(timeout, int) or timeout <= 0:
            errors.append(f"{prefix} ({cid}): timeout={timeout!r} must be int > 0")

        gate_modes = c.get("gate_modes", [])
        if not isinstance(gate_modes, list) or not set(gate_modes).issubset(set(_VALID_GATE_MODES)):
            errors.append(f"{prefix} ({cid}): gate_modes={gate_modes!r} ⊄ {_VALID_GATE_MODES}")

        cmd = c.get("cmd")
        cmds = c.get("cmds")
        if cmd is None and cmds is None:
            errors.append(f"{prefix} ({cid}): neither cmd nor cmds present")
        errors.extend(
            f"{prefix} ({cid}): команда для gate-режима {mode!r} не резолвится (cmd|cmds)"
            for mode in gate_modes
            if cmd is None and (not isinstance(cmds, dict) or mode not in cmds)
        )

        junit = c.get("junit")
        if junit:
            for mode in gate_modes:
                if junit in junit_by_mode[mode]:
                    errors.append(
                        f"{prefix} ({cid}): duplicate junit path {junit!r} в режиме {mode!r} "
                        f"(уже у {junit_by_mode[mode][junit]!r})"
                    )
                junit_by_mode[mode][junit] = cid

    logger.info("[IMP:8][validate_manifest][check] %d check(s), %d error(s)", len(checks), len(errors))
    return errors


# endregion FUNC_validate_manifest


# region FUNC_parse_checks
## @purpose  Манифест → список CheckSpec с дефолтами схемы v1: diagnostic=True (tier
##           fix/static/pytest), xdist=True (tier pytest; явные false — в манифесте).
## @io       ⇥ manifest: dict → ⎋ list[CheckSpec] (порядок манифеста = канонический порядок gate)
## @complexity O(C)
def parse_checks(manifest: dict) -> list[CheckSpec]:
    """Parse manifest checks into CheckSpec dataclasses with schema defaults."""
    specs: list[CheckSpec] = []
    for c in manifest.get("checks", []):
        if not isinstance(c, dict):
            continue
        tier = c.get("tier", "static")
        specs.append(
            CheckSpec(
                id=c.get("id", ""),
                tier=tier,
                timeout=c.get("timeout", 60),
                gate_modes=list(c.get("gate_modes", [])),
                diagnostic=c.get("diagnostic", True),
                xdist=c.get("xdist", tier == "pytest"),
                sequential=c.get("sequential", False),
                allow_no_tests=c.get("allow_no_tests", False),
                non_blocking=c.get("non_blocking", False),
                junit=c.get("junit"),
                project_filter=c.get("project_filter", False),
                docker=c.get("docker", False),
                cmd=c.get("cmd"),
                cmds=c.get("cmds"),
            )
        )
    return specs


# endregion FUNC_parse_checks


# region FUNC_list_checks
## @purpose  Фильтрация чеков: gate_mode=None → диагностический набор (diagnostic=True);
##           gate_mode=fast|full|ci-docker → чек с данным режимом (порядок манифеста).
## @io       ⇥ manifest: dict, gate_mode: str | None → ⎋ list[CheckSpec]
## @complexity O(C)
def list_checks(manifest: dict, gate_mode: str | None = None) -> list[CheckSpec]:
    """Return checks: diagnostic set (gate_mode=None) or checks for a gate mode."""
    specs = parse_checks(manifest)
    if gate_mode is None:
        return [s for s in specs if s.diagnostic]
    return [s for s in specs if gate_mode in s.gate_modes]


# endregion FUNC_list_checks

# endregion MANIFEST_LOAD


# region COMMAND_EXEC


# region FUNC_resolve_command
## @purpose  Резолв исполняемых в .venv (pytest/pre-commit/ruff/python3) — команды манифеста
##           нейтральны, на машине разработчика исполняемые живут в .venv (Makefile использует
##           $(PYTHON) = .venv/bin/python). System python3 может не иметь pytest-зависимостей.
## @io       ⇥ tokens: list[str] (shlex-разбор команды), root: Path → list[str] (токены с резолвом)
## @complexity O(1)
def _resolve_command_tokens(tokens: list[str], root: Path) -> list[str]:
    """Resolve venv executables (pytest/pre-commit/ruff/python3) when present."""
    if not tokens:
        return tokens
    if tokens[0] in _VENV_RESOLVABLE:
        venv_bin = root / ".venv" / "bin" / tokens[0]
        if venv_bin.is_file():
            return [str(venv_bin), *tokens[1:]]
    return tokens


# endregion FUNC_resolve_command


# region FUNC_has_xdist
## @purpose  Проверка доступности pytest-xdist для venv-интерпретатора (дубль из прежнего
##           preflight.py — DevPlan 120 §3.3: «перенос в shared или локальный дубль»).
## @io       ⇥ python_path: str → bool
## @complexity O(1) — subprocess python -c "import xdist"
def _has_xdist(python_path: str) -> bool:
    """Best-effort availability check for pytest-xdist."""
    try:
        result = subprocess.run(
            [python_path, "-c", "import xdist"],
            capture_output=True,
            timeout=5,
        )
        return result.returncode == 0
    except Exception:  # noqa: EXC — best-effort check, any failure = unavailable
        return False


# endregion FUNC_has_xdist


# region FUNC_apply_xdist
## @purpose  Применение -n auto к ПРЯМЫМ pytest-командам (первый токен pytest) при
##           spec.xdist и доступности xdist; TEST_NO_XDIST=1 отключает. `make test MARKER=...`
##           и test_runner-команды НЕ трогаются (xdist внутри test_runner, Wave 1).
## @io       ⇥ cmd_str: str, spec: CheckSpec, root: Path → str (модифицированная команда)
## @complexity O(T) где T = токены
## @rationale DevPlan §3.1 xdist: true на gates/contract/static_audit/predeploy/smoke/component;
##            §3.3 — test_runner получает -n auto; добавление -n к `make ...` сломало бы make.
def _apply_xdist(cmd_str: str, spec: CheckSpec, root: Path) -> str:
    """Insert `-n auto` after `pytest` for direct pytest commands when xdist enabled."""
    tokens = shlex.split(cmd_str)
    if not tokens or tokens[0] != "pytest":
        return cmd_str
    if not spec.xdist:
        return cmd_str
    if os.environ.get("TEST_NO_XDIST") == "1":
        return cmd_str
    venv_python = root / ".venv" / "bin" / "python"
    python_path = str(venv_python) if venv_python.is_file() else sys.executable
    if not _has_xdist(python_path):
        return cmd_str
    # -n auto ПЕРЕД -m (DevPlan §3.3); pytest допускает любую позицию, но конвенция — после pytest
    tokens[1:1] = ["-n", "auto"]
    logger.info("[IMP:8][apply_xdist][resolve] %s → -n auto добавлен", spec.id)
    # ⚠️ shlex.join (НЕ " ".join): исходные кавычки -m-выражения ("gate and not requires_docker")
    # уже сняты shlex.split — повторная склейка без кавычек ломала выражение → exit 5 (0 тестов)
    return shlex.join(tokens)


# endregion FUNC_apply_xdist


# region FUNC_apply_project_filter
## @purpose  PROJECT=\<name\> → -k \<name\> для project_filter-чеков (predeploy). Паритет ci.mk:
##           -k применялся ТОЛЬКО к прямой pytest-команде fast-predeploy, не к make test.
## @io       ⇥ cmd_str: str, project: str | None → str
## @complexity O(T)
def _apply_project_filter(cmd_str: str, project: str | None) -> str:
    """Append `-k <project>` for direct pytest commands when project_filter is set."""
    if not project:
        return cmd_str
    tokens = shlex.split(cmd_str)
    if tokens and tokens[0] == "pytest":
        return f"{cmd_str} -k {shlex.quote(project)}"
    return cmd_str


# endregion FUNC_apply_project_filter


# region FUNC_docker_suite_lock
## @purpose  Процессный advisory flock на tests/.docker-suite.lock (DevPlan 124 T2c) —
##           зеркало test_runner._docker_suite_lock: ЕДИНЫЙ lock-файл для ВСЕХ
##           docker-pytest-процессов на машине (test_runner и check_suite). Два агента,
##           одновременно гоняющих docker-чеки, НЕ пересекаются по compose-стеку (F4).
##           Реализация fcntl.flock (прецедент counter.py, DevPlan 120 §3.3) вместо
##           flock-CLI (отсутствует на macOS; stdlib-only инвариант).
## @io       ⇥ root: Path → contextmanager (lock удерживается внутри with)
## @complexity O(1)
# ⚠️ TRAP[DECISION] · 2026-08-03 · — · docker-лок check_suite: fcntl-зеркало test_runner
# · Rejected: shell-префикс `flock tests/.docker-suite.lock` к команде чека (текст DevPlan
# ·   124 T2c) — flock-CLI отсутствует на macOS (`which flock` → not found, 2026-08-03);
# ·   prefix-подход не удержал бы лок при timeout-киле subprocess (flock-ребёнок остался бы)
# · Reason: in-process fcntl.flock вокруг subprocess.run держит лок ровно на время исполнения
# ·   команды и безусловно освобождается в finally/при завершении процесса; единый lock-файл
# ·   tests/.docker-suite.lock общий с test_runner (T2c: «Единый lock-файл для всех процессов»)
# · Rev: при появлении shell-потребителя лока — вынести в shared-модуль с CLI.
@contextlib.contextmanager
def _docker_suite_lock(root: Path):
    """Context manager holding the process-level docker-suite flock (mirror of test_runner)."""
    import fcntl  # lazy — POSIX-only (darwin/linux)

    lock_path = root / "tests" / ".docker-suite.lock"
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    logger.info("[IMP:8][docker_lock][acquire] flock held: %s", lock_path)
    with open(lock_path, "a+") as fh:
        fcntl.flock(fh.fileno(), fcntl.LOCK_EX)
        try:
            yield
        finally:
            fcntl.flock(fh.fileno(), fcntl.LOCK_UN)
            logger.info("[IMP:8][docker_lock][release] flock released: %s", lock_path)


# endregion FUNC_docker_suite_lock


# region FUNC_run_cmd
## @purpose  Исполнение команды чека: subprocess с таймаутом, cwd=root, env; timeout → exit 124;
##           FileNotFoundError → exit 127. docker_lock=True → команда оборачивается в
##           _docker_suite_lock (docker-чеки сериализуются межсессионно, DevPlan 124 T2c).
##           НЕ бросает исключений — caller собирает результат.
## @io       ⇥ cmd_str: str, timeout: int, env: dict, root: Path,
##             docker_lock: bool (spec.docker: true) → CheckOutcome
## @complexity O(1) + время subprocess
def _run_cmd(
    cmd_str: str,
    timeout: int,
    env: dict[str, str],
    root: Path,
    docker_lock: bool = False,
) -> CheckOutcome:
    """Run a single check command; never raises on check failure."""
    tokens = _resolve_command_tokens(shlex.split(cmd_str), root)
    start = time.monotonic()
    try:
        with _docker_suite_lock(root) if docker_lock else contextlib.nullcontext():
            # ⚠️ TRAP[BUG] · 2026-08-06 · P1 · Ночная сессия 141 — таймаут-килл оставлял орфанов
            # · Symptom: static_audit >300s → subprocess.run(timeout) убивал ТОЛЬКО pytest-родителя;
            # ·   xdist-воркеры/дети осиротевали и ПРОДОЛЖАЛИ мутировать tests/ (junitxml, __pycache__)
            # ·   → последующий doxygen-check парсил дерево во время мутаций → 46 «unexpanded alias»
            # ·   (flex-баг 1.17.0) → gate/check флакали; орфан жил часами.
            # · Fix: start_new_session + killpg при TimeoutExpired (весь process-group).
            result = subprocess.run(
                tokens,
                capture_output=True,
                text=True,
                timeout=timeout,
                cwd=str(root),
                env=env,
                start_new_session=True,
            )
        duration = (time.monotonic() - start) * 1000
        logger.info(
            "[IMP:8][run_cmd][exec] %s → exit=%d (%.1fs)",
            " ".join(tokens)[:120],
            result.returncode,
            duration / 1000,
        )
        return CheckOutcome(
            name=tokens[0],
            exit_code=result.returncode,
            stdout=result.stdout,
            stderr=result.stderr,
            duration_ms=duration,
        )
    except subprocess.TimeoutExpired as exc:
        duration = (time.monotonic() - start) * 1000
        # killpg: убить ВЕСЬ process-group (pytest-родитель + xdist-воркеры) — иначе орфаны
        # продолжают мутировать дерево и ломают doxygen-check (TRAP выше)
        try:
            os.killpg(os.getpgid(exc.pid), signal.SIGKILL)
            logger.warning(
                "[IMP:7][run_cmd][timeout] %s TIMEOUT after %ds — killed process group %d",
                cmd_str[:80],
                timeout,
                exc.pid,
            )
        except (ProcessLookupError, PermissionError):
            pass  # процесс уже мёртв
        return CheckOutcome(
            name=tokens[0] if tokens else "?", exit_code=124, stderr=f"Timeout after {timeout}s", duration_ms=duration
        )
    except FileNotFoundError:
        duration = (time.monotonic() - start) * 1000
        logger.error("[IMP:9][run_cmd][missing] Command not found: %s", tokens[0] if tokens else "?")
        return CheckOutcome(
            name=tokens[0] if tokens else "?",
            exit_code=127,
            stderr=f"Command not found: {tokens[0] if tokens else '?'}",
            duration_ms=duration,
        )


# endregion FUNC_run_cmd

# endregion COMMAND_EXEC


# region FINGERPRINT_CACHE


# region FUNC_tree_files
## @purpose  Список файлов дерева: git ls-files -c -o --exclude-standard (tracked + untracked,
##           gitignore уважается) + явные исключения (report*.xml, .test_counter.json, venv-пути).
## @io       ⇥ root: Path → ⎋ list[str] | None (None = git недоступен)
## @complexity O(N) где N = файлы
def _tree_files(root: Path) -> list[str] | None:
    """List tree files (tracked + untracked non-ignored) via one git subprocess."""
    try:
        result = subprocess.run(
            ["git", "ls-files", "-c", "-o", "--exclude-standard", "-z"],
            capture_output=True,
            cwd=str(root),
            timeout=60,
        )
        if result.returncode != 0:
            return None
    except (FileNotFoundError, subprocess.TimeoutExpired, OSError):
        return None
    files: list[str] = []
    for raw in result.stdout.decode("utf-8", errors="replace").split("\0"):
        if not raw:
            continue
        if any(part in _FINGERPRINT_EXCLUDE_PARTS for part in raw.split("/")):
            continue
        if _FINGERPRINT_EXCLUDE_RE.search(raw):
            continue
        files.append(raw)
    return files


# endregion FUNC_tree_files


# region FUNC_compute_fingerprint
## @purpose  Fingerprint целого дерева (DevPlan §3.4): sha256(манифест + .pre-commit-config.yaml +
##           pyproject.toml + содержимое ВСЕХ файлов дерева). Байт-идентичное дерево → тот же
##           fingerprint; любая правка/untracked-файл → miss. None если git недоступен (кэш off).
## @io       ⇥ root: Path → ⎋ str | None
## @complexity O(N * S) где N = файлы, S = средний размер
## @rationale Чистый Python вместо xargs sha256sum (TRAP[DECISION] выше): один git subprocess
##            + hashlib; формат-контракт DevPlan сохранён.
def compute_fingerprint(root: Path) -> str | None:
    """Compute the whole-tree fingerprint; None when git is unavailable."""
    files = _tree_files(root)
    if files is None:
        logger.warning("[IMP:7][fingerprint][skip] git недоступен — fingerprint-кэш отключён")
        return None

    hasher = hashlib.sha256()
    for rel in _FINGERPRINT_EXTRA_FILES:
        p = root / rel
        if p.is_file():
            hasher.update(rel.encode("utf-8"))
            hasher.update(b"\0")
            hasher.update(p.read_bytes())
    for rel in sorted(files):
        p = root / rel
        try:
            data = p.read_bytes()
        except OSError:
            continue
        hasher.update(rel.encode("utf-8"))
        hasher.update(b"\0")
        hasher.update(data)
    fp = hasher.hexdigest()
    logger.info("[IMP:8][fingerprint][compute] %d файлов → %s", len(files), fp[:16])
    return fp


# endregion FUNC_compute_fingerprint


# region FUNC_cache_path
## @purpose  Путь кэша: $(git rev-parse --git-dir)/check-cache.json (не коммитится).
## @io       ⇥ root: Path → ⎋ Path | None (None = git недоступен)
## @complexity O(1) — один git subprocess
def _cache_path(root: Path) -> Path | None:
    """Resolve cache file inside the git dir (not committed)."""
    try:
        result = subprocess.run(
            ["git", "rev-parse", "--git-dir"],
            capture_output=True,
            text=True,
            cwd=str(root),
            timeout=15,
        )
        if result.returncode != 0:
            return None
        gitdir = result.stdout.strip()
        gitdir_path = Path(gitdir)
        if not gitdir_path.is_absolute():
            gitdir_path = root / gitdir_path
        return gitdir_path / _CACHE_FILENAME
    except (FileNotFoundError, subprocess.TimeoutExpired, OSError):
        return None


# endregion FUNC_cache_path


# region FUNC_load_cache
## @purpose  Чтение кэш-JSON (fingerprint/status/report); битый/отсутствующий → None.
## @io       ⇥ path: Path → ⎋ dict | None
## @complexity O(1)
def _load_cache(path: Path | None) -> dict | None:
    """Read cache JSON; malformed/missing → None."""
    if path is None or not path.is_file():
        return None
    try:
        with open(path) as f:
            data = json.load(f)
        return data if isinstance(data, dict) else None
    except (json.JSONDecodeError, OSError):
        return None


# endregion FUNC_load_cache


# region FUNC_save_cache
## @purpose  Запись кэш-JSON (атомарно: tmp + os.replace — конкурентные executor'ы не портят файл).
## @io       ⇥ path: Path | None, data: dict → None
## @complexity O(1)
def _save_cache(path: Path | None, data: dict) -> None:
    """Write cache JSON atomically (tmp + os.replace)."""
    if path is None:
        return
    try:
        tmp = path.with_suffix(".json.tmp")
        with open(tmp, "w") as f:
            json.dump(data, f)
            f.write("\n")
        os.replace(tmp, path)
    except OSError as exc:
        logger.warning("[IMP:7][cache][write] cache write failed: %s", exc)


# endregion FUNC_save_cache

# endregion FINGERPRINT_CACHE


# region REPORTING


# region FUNC_format_report
## @purpose  Единый отчёт (формат прежнего preflight): статус, длительность, счётчики,
##           per-check PASS/FAIL/FIXED, секция провалов с error_summary, NEXT-подсказка.
##           json_output → машиночитаемый dict. Возвращает (str, dict).
## @io       ⇥ outcomes: list[CheckOutcome], duration_ms: float, json_output: bool,
##             replayed: bool → (str, dict)
## @complexity O(R) где R = результаты
def _format_report(
    outcomes: list[CheckOutcome],
    duration_ms: float,
    json_output: bool = False,
    replayed: bool = False,
) -> tuple[str, dict]:
    """Build the unified check report (human or JSON)."""
    passed = sum(1 for r in outcomes if r.passed or r.passed_no_tests)
    auto_fixed = sum(1 for r in outcomes if r.auto_fixed)
    # blocked (non_blocking) провалы НЕ роняют статус — gate остаётся зелёным (паритет ci.mk `|| true`)
    failed = sum(1 for r in outcomes if not r.passed and not r.passed_no_tests and not r.blocked)
    status = "green" if failed == 0 else "failed"

    checks_payload = [
        {
            "name": r.name,
            "exit_code": r.exit_code,
            "passed": r.passed,
            "auto_fixed": r.auto_fixed,
            "no_tests": r.passed_no_tests,
            "blocked": r.blocked,
            "duration_ms": round(r.duration_ms),
            "error_summary": r.error_summary() if (not r.passed and not r.passed_no_tests) else "",
        }
        for r in outcomes
    ]
    report_dict = {
        "status": status,
        "total_checks": len(outcomes),
        "passed": passed,
        "auto_fixed": auto_fixed,
        "failed": failed,
        "checks": checks_payload,
        "duration_ms": duration_ms,
        "replayed": replayed,
    }

    if json_output:
        return (json.dumps(report_dict, indent=2), report_dict)

    lines: list[str] = []
    sep = "=" * 64
    subsep = "-" * 64
    lines.append(f"\n{sep}")
    lines.append(f"  CHECK REPORT: {status.upper()}" + (" (replayed from cache)" if replayed else ""))
    lines.append(f"{sep}")
    lines.append(
        f"  Duration: {duration_ms / 1000:.1f}s  |  "
        f"Checks: {len(outcomes)} total  |  "
        f"{passed} passed  |  "
        f"{auto_fixed} auto-fixed  |  "
        f"{failed} failed"
    )
    lines.append("")

    for r in outcomes:
        if r.passed_no_tests:
            marker, icon = "PASS", "OK"
        elif r.auto_fixed:
            marker, icon = "FIXED", "FX"
        elif r.passed:
            marker, icon = "PASS", "OK"
        elif r.blocked:
            marker, icon = "BLOCKED", "!!"
        else:
            marker, icon = "FAIL", "!!"
        lines.append(f"  [{icon}] {r.name}: {marker} ({r.duration_ms / 1000:.1f}s)")

    failed_checks = [r for r in outcomes if not r.passed and not r.passed_no_tests and not r.blocked]
    if failed_checks:
        lines.append(f"\n{subsep}")
        lines.append(f"  FAILED CHECKS ({len(failed_checks)}):")
        lines.append(f"{subsep}")
        for r in failed_checks:
            lines.append(f"\n  ### {r.name} (exit {r.exit_code})")
            summary = r.error_summary(max_lines=15)
            lines.extend(f"      {line}" for line in summary.split("\n"))

    lines.append(f"\n{subsep}")
    if status == "green":
        lines.append("  RESULT: All checks PASS.")
        lines.append("  NEXT:   make gate MODE=fast  (single verification)")
    else:
        lines.append(f"  RESULT: {failed} check(s) failed.")
        lines.append("  NEXT:   Fix ALL errors above, then:")
        lines.append("          make gate MODE=fast  (single verification)")
    lines.append(f"{sep}\n")
    return ("\n".join(lines), report_dict)


# endregion FUNC_format_report

# endregion REPORTING


# region RUN_DIAGNOSTIC


# region FUNC_run_fix_phase
## @purpose  Диагностическая fix-фаза (экс-preflight Phase 1+2): fix-gate pre-step + tier=fix
##           чеки манифеста ПОСЛЕДОВАТЕЛЬНО. pre-commit имеет retry-once (автоправка гигиены).
##           fix-gate провал → остальные фазы не запускаются (среда не чиста).
## @io       ⇥ manifest: dict, root: Path, env: dict → (list[CheckOutcome], bool)
##           (результаты, fix_ok)
## @complexity O(F) где F = fix-чеки
def _run_fix_phase(manifest: dict, root: Path, env: dict[str, str]) -> tuple[list[CheckOutcome], bool]:
    """Run the sequential auto-fix phase: fix-gate pre-step + tier=fix manifest checks."""
    results: list[CheckOutcome] = []
    print("[IMP:7][check] Fix phase: make fix-gate (auto-fix)...", file=sys.stderr)
    fix_gate = _run_cmd(_FIX_GATE_PRE_STEP, 120, env, root)
    results.append(fix_gate)
    if not fix_gate.passed:
        print(f"[IMP:9][check] fix-gate FAILED (exit {fix_gate.exit_code})", file=sys.stderr)
        return results, False

    for spec in list_checks(manifest, gate_mode=None):
        if spec.tier != "fix":
            continue
        cmd_str = spec.resolve_command(gate_mode=None)
        if not cmd_str:
            continue
        print(f"[IMP:7][check] Fix phase: {spec.id} (tier=fix)...", file=sys.stderr)
        r = _run_cmd(cmd_str, spec.timeout, env, root)
        if not r.passed:
            # Retry-once: pre-commit автоправляет гигиену (trailing-whitespace, end-of-file-fixer)
            print(f"[IMP:8][check] {spec.id} had issues — re-running to apply auto-fixes...", file=sys.stderr)
            r2 = _run_cmd(cmd_str, spec.timeout, env, root)
            if r2.passed:
                r2.auto_fixed = True
                r = r2
        results.append(r)
    return results, True


# endregion FUNC_run_fix_phase


# region FUNC_run_diagnostic
## @purpose  Диагностический executor (`make check`): fix-фаза → fingerprint → кэш (replay
##           зелёного прогона) → static-чеки в потоках + pytest-чеки последовательно → отчёт
##           → запись кэша. Кэш только здесь; gate/diff — без кэша.
## @io       ⇥ root: Path, no_fix: bool, json_output: bool, workers: int, no_cache: bool,
##             verbose: bool → int (0 зелёный, 1 провалы)
## @complexity O(C * t) где C = чеки, t = время исполнения
## @invariants
##   - fingerprint ПОСЛЕ fix-фазы (мутация автоправкой не ломает replay)
##   - Replay только при fingerprint-совпадении И status=green; упавший прогон никогда не реплеится
##   - pytest-чеки строго последовательно (решение b); static-чеки параллельно
##   - --no-cache / CHECK_CACHE=0 → без чтения и записи кэша
def run_diagnostic(
    root: Path,
    no_fix: bool = False,
    json_output: bool = False,
    workers: int = _DEFAULT_MAX_WORKERS,
    no_cache: bool = False,
    verbose: bool = False,
) -> int:
    """Diagnostic executor: fix phase → fingerprint cache → parallel static + sequential pytest."""
    start = time.monotonic()
    manifest = load_manifest(root)
    errors = validate_manifest(manifest)
    if errors:
        print(f"[IMP:10][check] Manifest invalid ({len(errors)} error(s)):\n" + "\n".join(f"  - {e}" for e in errors))
        return 2

    env = os.environ.copy()
    env.setdefault("PYTEST_NO_ESCALATION", "1")

    outcomes: list[CheckOutcome] = []
    # ── Fix phase (sequential, мутирует файлы) ──
    if not no_fix:
        fix_results, fix_ok = _run_fix_phase(manifest, root, env)
        outcomes.extend(fix_results)
        if not fix_ok:
            total_ms = (time.monotonic() - start) * 1000
            report_str, _ = _format_report(outcomes, total_ms, json_output=json_output)
            print(report_str)
            return 1

    # ── Fingerprint ПОСЛЕ fix-фазы (DevPlan §3.4 п.3) ──
    cache_disabled = no_cache or os.environ.get("CHECK_CACHE") == "0"
    fp = None if cache_disabled else compute_fingerprint(root)
    cache_file = None if cache_disabled else _cache_path(root)

    if fp is not None and cache_file is not None:
        cached = _load_cache(cache_file)
        if cached and cached.get("fingerprint") == fp and cached.get("status") == "green":
            print("[IMP:7][check] Fingerprint совпал — replay зелёного прогона (кэш)", file=sys.stderr)
            if json_output and isinstance(cached.get("checks"), list):
                print(
                    json.dumps(
                        {
                            "status": "green",
                            "replayed": True,
                            **{
                                k: cached[k]
                                for k in ("total_checks", "passed", "auto_fixed", "failed", "duration_ms")
                                if k in cached
                            },
                            "checks": cached["checks"],
                        },
                        indent=2,
                    )
                )
            else:
                print(cached.get("report", "(кэш без отчёта)"))
            return 0

    diagnostic_checks = list_checks(manifest, gate_mode=None)
    static_checks = [s for s in diagnostic_checks if s.tier == "static"]
    pytest_checks = [s for s in diagnostic_checks if s.tier == "pytest"]

    # ── static: параллельно в потоках; pytest: строго последовательно (решение b) ──
    # ⚠️ TRAP[BUG] · 2026-08-06 · P1 · Ночная сессия 141 — doxygen-check флакал в параллели
    # · Symptom: make check/gate периодически падали «46 doxygen warning(s)» (unexpanded alias),
    # ·   standalone doxygen = 0 warning'ов. Поймано в check: doxygen в ThreadPoolExecutor
    # ·   параллельно с static_audit (pytest, 300s) — pytest мутирует tests/ (__pycache__,
    # ·   report-файлы) пока doxygen парсит → lexer doxygen 1.17.0 (flex push-back overflow,
    # ·   TRAP Doxyfile:53) → «Found unexpanded alias» в последующих docstring'ах.
    # · Fix: sequential:true чеки (doxygen-check) исполняются ПОСЛЕ параллельной static-фазы,
    # ·   до pytest-фазы. Плюс unique log в ci.mk (коллизия /tmp/doxygen-check.log при двух gate).
    # · Rev: если doxygen обновится (flex-fix) — sequential можно снять.
    static_parallel = [s for s in static_checks if not s.sequential and s.resolve_command(None)]
    static_sequential = [s for s in static_checks if s.sequential and s.resolve_command(None)]
    static_results: list[CheckOutcome] = []
    with concurrent.futures.ThreadPoolExecutor(max_workers=workers) as executor:
        futures = {
            executor.submit(_run_cmd, s.resolve_command(None) or "", s.timeout, env, root): s.id
            for s in static_parallel
        }
        for future in concurrent.futures.as_completed(futures):
            cid = futures[future]
            try:
                static_results.append(future.result())
            except Exception as exc:  # noqa: EXC — best-effort thread-pool wrapper, must not crash
                static_results.append(CheckOutcome(name=cid, exit_code=1, stderr=f"Internal error: {exc}"))
    for spec in static_sequential:
        print(f"[IMP:7][check] {spec.id} (sequential, после параллельной static-фазы)...", file=sys.stderr)
        static_results.append(_run_cmd(spec.resolve_command(None) or "", spec.timeout, env, root))
    outcomes.extend(static_results)

    for spec in pytest_checks:
        cmd_str = spec.resolve_command(None)
        if not cmd_str:
            continue
        cmd_str = _apply_xdist(cmd_str, spec, root)
        print(f"[IMP:7][check] pytest: {spec.id} (sequential, xdist={spec.xdist})...", file=sys.stderr)
        # DevPlan 124 T2c: docker-чеки (spec.docker: true — gates-docker/predeploy-docker)
        # — под процессным локом (межсессионная сериализация docker-стека, F4)
        r = _run_cmd(cmd_str, spec.timeout, env, root, docker_lock=spec.docker)
        if spec.allow_no_tests and r.exit_code == 5:
            r.passed_no_tests = True
            print(f"[IMP:8][check] {spec.id}: 0 тестов (rc=5) → PASS (allow_no_tests)", file=sys.stderr)
        outcomes.append(r)

    total_ms = (time.monotonic() - start) * 1000
    report_str, report_dict = _format_report(outcomes, total_ms, json_output=json_output)

    if verbose and not json_output:
        for r in outcomes:
            if not r.passed and not r.passed_no_tests:
                report_str += f"\n\n=== FULL OUTPUT: {r.name} ===\n"
                report_str += r.stdout + "\n" + r.stderr

    print(report_str)

    # ── Запись кэша (status failed тоже пишется — упавший прогон не реплеится) ──
    if fp is not None and cache_file is not None:
        _save_cache(
            cache_file,
            {
                "fingerprint": fp,
                "status": report_dict["status"],
                "duration_ms": total_ms,
                "report": report_str,
                "checks": report_dict["checks"],
            },
        )
        logger.info("[IMP:8][check][cache] cache written (status=%s)", report_dict["status"])

    return 0 if report_dict["status"] == "green" else 1


# endregion FUNC_run_diagnostic

# endregion RUN_DIAGNOSTIC


# region RUN_GATE


# region FUNC_cleanup_reports
## @purpose  Удаление старых tests/report*.xml перед прогоном gate (паритет ci.mk: rm -f).
## @io       ⇥ root: Path → None
## @complexity O(R) где R = report-файлы
def _cleanup_reports(root: Path) -> None:
    """Remove stale JUnit reports before a gate run (parity with ci.mk)."""
    reports_dir = root / "tests"
    if reports_dir.is_dir():
        for p in reports_dir.glob("report*.xml"):
            with contextlib.suppress(OSError):
                p.unlink()


# endregion FUNC_cleanup_reports


# region FUNC_merge_junit
## @purpose  junit-merge через tests/merge_junit.py (DevPlan §3.6: reuse существующего
##           механизма, НЕ новая агрегация): существующие junit-файлы чеков → tests/report.xml.
##           Missing-файлы merge_junit пропускает сам; отсутствие всех → warn без fail.
## @io       ⇥ root: Path, junit_paths: list[str] (в каноническом порядке) → None
## @complexity O(M * T) где M = файлы, T = тесткейсы
def _merge_junit(root: Path, junit_paths: list[str]) -> None:
    """Merge existing JUnit reports via tests/merge_junit.py (reuse, DevPlan §3.6)."""
    existing = [str(root / p) for p in junit_paths if (root / p).is_file()]
    if not existing:
        logger.warning("[IMP:7][gate][merge] Нет JUnit-отчётов для merge — пропуск")
        return
    # merge_junit.py — инфраструктура платформы: ищем в tests/ корня прогона, фолбэк — tests/ платформы
    merge_script = root / "tests" / "merge_junit.py"
    if not merge_script.is_file():
        merge_script = _PROJECT_ROOT / "tests" / "merge_junit.py"
    out = root / "tests" / "report.xml"
    proc = subprocess.run(
        [sys.executable, str(merge_script), *existing, "-o", str(out)],
        capture_output=True,
        text=True,
        timeout=120,
        cwd=str(root),
    )
    if proc.returncode != 0:
        print(
            f"[IMP:9][gate][merge] JUnit merge FAILED (exit {proc.returncode}): {proc.stderr[-500:]}", file=sys.stderr
        )


# endregion FUNC_merge_junit


# region FUNC_run_gate
## @purpose  Канонический gate-executor (`make gate MODE=fast|full|ci-docker`): шаги из
##           манифеста по gate_modes в каноническом порядке; fast — fail-fast, full/ci-docker —
##           accumulate + junit-merge; БЕЗ кэша (арбитр всегда честный прогон).
## @io       ⇥ root: Path, gate_mode: str, project: str | None, skip_precommit: bool → int
## @complexity O(C * t) где C = шаги, t = время исполнения
## @invariants
##   - Порядок шагов = порядок манифеста (паритет ci.mk — golden-тест consistency-гейта)
##   - allow_no_tests (rc=5) → PASS; non_blocking → провал не роняет gate и не стопит fast
##   - SKIP_PRECOMMIT → pre-commit шаг пропускается (паритет ci.mk SKIP_PRECOMMIT=1)
##   - PROJECT → -k только для прямых pytest-команд project_filter-чеков
##   - fail-fast: первый НЕ-non_blocking провал → exit 1 (последующие шаги не выполняются)
##   - accumulate: все шаги выполняются; exit 1 при любом провале
##   - junit-merge: full → contract/static_audit/predeploy/smoke/component; ci-docker →
##     predeploy-docker/smoke/component (порядок и состав паритетны ci.mk)
def run_gate(
    root: Path,
    gate_mode: str,
    project: str | None = None,
    skip_precommit: bool = False,
) -> int:
    """Canonical gate executor: manifest-ordered steps, fail-fast/accumulate, no cache."""
    if gate_mode not in _VALID_GATE_MODES:
        print(
            f"[IMP:10][gate] ERROR: Unknown MODE={gate_mode!r}. Valid values: {', '.join(_VALID_GATE_MODES)}",
            file=sys.stderr,
        )
        return 2

    start = time.monotonic()
    manifest = load_manifest(root)
    errors = validate_manifest(manifest)
    if errors:
        print(f"[IMP:10][gate] Manifest invalid ({len(errors)} error(s)):\n" + "\n".join(f"  - {e}" for e in errors))
        return 2

    steps = list_checks(manifest, gate_mode=gate_mode)
    _cleanup_reports(root)
    env = os.environ.copy()
    env.setdefault("PYTEST_NO_ESCALATION", "1")

    print(
        f"[IMP:7][gate] MODE={gate_mode} — {len(steps)} шагов из core/check-suite.yaml (без кэша)...", file=sys.stderr
    )
    outcomes: list[CheckOutcome] = []
    gate_failed = False
    for i, spec in enumerate(steps, 1):
        if spec.id == "pre-commit" and skip_precommit:
            print(f"[IMP:7][gate] Step {i}/{len(steps)}: pre-commit SKIPPED (SKIP_PRECOMMIT=1)", file=sys.stderr)
            continue
        cmd_str = spec.resolve_command(gate_mode)
        if not cmd_str:
            print(f"[IMP:9][gate] Step {i}/{len(steps)}: {spec.id} — команда не найдена (пропуск)", file=sys.stderr)
            continue
        cmd_str = _apply_xdist(cmd_str, spec, root)
        cmd_str = _apply_project_filter(cmd_str, project) if spec.project_filter else cmd_str
        print(f"[IMP:7][gate] Step {i}/{len(steps)}: {spec.id}...", file=sys.stderr)
        # DevPlan 124 T2c: docker-чеки (spec.docker: true) — под процессным локом (F4)
        r = _run_cmd(cmd_str, spec.timeout, env, root, docker_lock=spec.docker)
        # DevPlan 124 (решение пользователя 2026-08-03): pre-commit-шаг — retry-once при
        # «files were modified by this hook». Механизм флейка: pre-commit сверяет git-статус
        # до/после КАЖДОГО хука; параллельная сессия (`git add -A` + commit в том же worktree,
        # прецедент 2026-08-03 — RC-сессия коммитила во время gate-прогонов) меняет индекс во
        # время исполнения хука → ложный «files were modified» (2/3 gate-фейлов; standalone —
        # 0 фейлов). Retry-once отличает транзиент (повтор проходит) от реальной модификации
        # хуком (повтор тоже падает — gate честно RED).
        if spec.id == "pre-commit" and not r.passed and "files were modified by this hook" in (r.stdout or ""):
            print(
                "[IMP:8][gate] pre-commit: 'files were modified' — транзиентная гонка с параллельной "
                "git-операцией, retry-once (DevPlan 124)",
                file=sys.stderr,
            )
            r = _run_cmd(cmd_str, spec.timeout, env, root, docker_lock=spec.docker)
        if spec.allow_no_tests and r.exit_code == 5:
            r.passed_no_tests = True
            print(f"[IMP:8][gate] {spec.id}: 0 тестов (rc=5) → PASS (allow_no_tests)", file=sys.stderr)
        if not r.passed and not r.passed_no_tests:
            if spec.non_blocking:
                r.blocked = True
                print(f"[IMP:8][gate] {spec.id}: провал НЕ блокирует gate (non_blocking)", file=sys.stderr)
            else:
                gate_failed = True
                print(f"[IMP:9][gate] FAIL: {spec.id} (exit {r.exit_code})", file=sys.stderr)
                # ⚠️ TRAP[BUG] 2026-08-03 · stdout pytest вытеснялся скипами из stderr
                # · Symptom: gate-fast CI «gates exit 1» без деталей — FAILED-строки pytest
                # ·   не видны (conftest automatic_skip_gate логирует 16 скипов в stderr;
                # ·   прежний выбор (r.stderr or r.stdout) показывал только хвост скипов).
                # · Fix: приоритет stdout (pytest short summary с FAILED), stderr — fallback.
                print(((r.stdout or r.stderr) or "")[-3000:], file=sys.stderr)
        outcomes.append(r)
        if gate_failed and gate_mode == "fast":
            break  # fail-fast: первый блокирующий провал стопит fast-режим

    if gate_mode in ("full", "ci-docker"):
        junit_paths = [s.junit for s in steps if s.junit]
        _merge_junit(root, junit_paths)

    total_ms = (time.monotonic() - start) * 1000
    report_str, report_dict = _format_report(outcomes, total_ms)
    print(report_str)

    if report_dict["status"] == "green":
        print(f"[IMP:9][gate] Gate: ALL PASS (MODE={gate_mode})", file=sys.stderr)
        return 0
    print(f"[IMP:9][gate] Gate: FAILURES DETECTED (MODE={gate_mode}) — см. FAIL-секции выше", file=sys.stderr)
    return 1


# endregion FUNC_run_gate

# endregion RUN_GATE


# region RUN_DIFF


# region FUNC_diff_files
## @purpose  Файлы diff-скоупа: git diff --name-only HEAD (tracked) + git ls-files -o
##           --exclude-standard (untracked). None = git недоступен.
## @io       ⇥ root: Path → ⎋ list[str] | None
## @complexity O(N)
def _diff_files(root: Path) -> list[str] | None:
    """Collect changed files: tracked (vs HEAD) + untracked non-ignored."""
    changed: list[str] = []
    try:
        r1 = subprocess.run(
            ["git", "diff", "--name-only", "HEAD"],
            capture_output=True,
            text=True,
            cwd=str(root),
            timeout=30,
        )
        if r1.returncode != 0:
            return None
        changed.extend(line for line in r1.stdout.splitlines() if line.strip())
        r2 = subprocess.run(
            ["git", "ls-files", "-o", "--exclude-standard"],
            capture_output=True,
            text=True,
            cwd=str(root),
            timeout=30,
        )
        if r2.returncode != 0:
            return None
        changed.extend(line for line in r2.stdout.splitlines() if line.strip())
    except (FileNotFoundError, subprocess.TimeoutExpired, OSError):
        return None
    return sorted(set(changed))


# endregion FUNC_diff_files


# region FUNC_build_diff_steps
## @purpose  Diff-скоуп (DevPlan §3.5): (1) pre-commit run --files \<изменённые\> — ВСЕГДА при
##           diff; (2) ruff check \<изменённые .py\>; (3) pytest \<изменённые test-файлы\>
##           (tests/**/test_*.py). Без кэша. Пустой diff → [] (exit 0 «nothing to diff»).
## @io       ⇥ root: Path, changed: list[str] → list[tuple[str, str, int]] (name, cmd, timeout)
## @complexity O(N)
## @invariants
##   - pre-commit --files заменяет --all-files (9.9s → ~2s на узком diff)
##   - ruff только по изменённым .py; pytest только по tests/**/test_*.py
##   - Нет изменений → пустой список → exit 0
def _build_diff_steps(root: Path, changed: list[str]) -> list[tuple[str, str, int]]:
    """Build the narrow diff-step list (pre-commit --files + ruff diff + pytest diff)."""
    if not changed:
        return []
    steps: list[tuple[str, str, int]] = []
    files_arg = " ".join(shlex.quote(f) for f in changed)
    steps.append(("pre-commit (diff)", f"pre-commit run --files {files_arg}", 120))
    py_files = [f for f in changed if f.endswith(".py")]
    if py_files:
        py_arg = " ".join(shlex.quote(f) for f in py_files)
        steps.append(("ruff check (diff)", f"ruff check {py_arg}", 60))
    test_files = [f for f in changed if re.match(r"^tests/.*test_.*\.py$", f)]
    if test_files:
        test_arg = " ".join(shlex.quote(f) for f in test_files)
        steps.append(("pytest (diff)", f"pytest {test_arg} -q --tb=short", 300))
    return steps


# endregion FUNC_build_diff_steps


# region FUNC_run_diff
## @purpose  check-diff executor: diff-файлы → узкие шаги → последовательный прогон → отчёт.
##           Пустой diff → exit 0. Без кэша (узкий честный таргет, DevPlan §3.5).
## @io       ⇥ root: Path → int
## @complexity O(N + t)
def run_diff(root: Path) -> int:
    """Diff-scope executor: pre-commit --files + ruff diff + pytest changed test files."""
    start = time.monotonic()
    changed = _diff_files(root)
    if changed is None:
        print("[IMP:9][check-diff] git недоступен — diff-скоуп не определим", file=sys.stderr)
        return 1
    if not changed:
        print("[IMP:7][check-diff] Nothing to diff — exit 0", file=sys.stderr)
        return 0

    print(f"[IMP:7][check-diff] {len(changed)} изменённых файлов", file=sys.stderr)
    env = os.environ.copy()
    env.setdefault("PYTEST_NO_ESCALATION", "1")
    outcomes: list[CheckOutcome] = []
    for name, cmd_str, timeout in _build_diff_steps(root, changed):
        print(f"[IMP:7][check-diff] {name}...", file=sys.stderr)
        r = _run_cmd(cmd_str, timeout, env, root)
        outcomes.append(r)

    total_ms = (time.monotonic() - start) * 1000
    report_str, report_dict = _format_report(outcomes, total_ms)
    print(report_str)
    return 0 if report_dict["status"] == "green" else 1


# endregion FUNC_run_diff

# endregion RUN_DIFF


# region CLI


# region FUNC_cmd_list
## @purpose  `list [--gate-mode fast|full]`: печать id чеков в каноническом порядке.
##           Используется consistency-гейтом для golden-паритета шагов gate.
## @io       ⇥ args: Namespace, root: Path → int
## @complexity O(C)
def _cmd_list(args: argparse.Namespace, root: Path) -> int:
    """List check ids (diagnostic set or a gate mode) in canonical manifest order."""
    manifest = load_manifest(root)
    gate_mode = getattr(args, "gate_mode", None)
    specs = list_checks(manifest, gate_mode=gate_mode)
    for s in specs:
        print(s.id)
    logger.info("[IMP:9][list][result] %d check(s) для gate_mode=%s", len(specs), gate_mode)
    return 0


# endregion FUNC_cmd_list


# region FUNC_cmd_fingerprint
## @purpose  `fingerprint`: вывод fingerprint дерева (диагностика кэша).
## @io       ⇥ args, root → int
## @complexity O(N * S)
def _cmd_fingerprint(args: argparse.Namespace, root: Path) -> int:
    """Print the tree fingerprint (cache diagnostics)."""
    fp = compute_fingerprint(root)
    if fp is None:
        print("fingerprint: unavailable (git недоступен)", file=sys.stderr)
        return 1
    print(fp)
    return 0


# endregion FUNC_cmd_fingerprint


# region FUNC_cmd_run
## @purpose  `run` dispatch: --mode diagnostic | diff | --gate-mode fast|full|ci-docker.
##           --gate-mode несовместим с --mode (взаимоисключение).
## @io       ⇥ args: Namespace, root: Path → int
## @complexity O(1) + режим
def _cmd_run(args: argparse.Namespace, root: Path) -> int:
    """Dispatch run subcommand to diagnostic/diff/gate executors."""
    if args.gate_mode:
        if args.mode:
            print("[IMP:10][run] --gate-mode несовместим с --mode", file=sys.stderr)
            return 2
        return run_gate(root, args.gate_mode, project=args.project, skip_precommit=args.skip_precommit)
    if args.mode == "diff":
        return run_diff(root)
    return run_diagnostic(
        root,
        no_fix=args.no_fix,
        json_output=args.json,
        workers=args.workers,
        no_cache=args.no_cache,
        verbose=args.verbose,
    )


# endregion FUNC_cmd_run


# region FUNC_main
## @purpose  CLI: run/list/fingerprint + флаги diagnostic (--no-fix/--json/--workers/--no-cache/
##           --verbose), gate (--gate-mode/--project/--skip-precommit), diff (--mode diff).
## @io       ⇥ argv: list[str] | None → int (exit code)
## @complexity O(1)
def main(argv: list[str] | None = None) -> int:
    """CLI entrypoint for the check-suite executor."""
    parser = argparse.ArgumentParser(
        prog="check_suite",
        description="Единый executor набора проверок из core/check-suite.yaml (DevPlan 120).",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    run_p = sub.add_parser("run", help="Запуск executor'а: diagnostic (по умолчанию), diff или gate.")
    run_p.add_argument(
        "--mode", choices=("diagnostic", "diff"), default=None, help="Режим диагностики (по умолчанию diagnostic)"
    )
    run_p.add_argument(
        "--gate-mode", choices=_VALID_GATE_MODES, default=None, help="Канонический gate-режим (без кэша)"
    )
    run_p.add_argument("--no-fix", action="store_true", help="Пропустить fix-фазу (fix-gate + tier=fix)")
    run_p.add_argument("--json", action="store_true", help="Машиночитаемый JSON-отчёт")
    run_p.add_argument("--workers", type=int, default=_DEFAULT_MAX_WORKERS, help="Воркеры static-параллелизма")
    run_p.add_argument("--no-cache", action="store_true", help="Без чтения/записи fingerprint-кэша (CHECK_CACHE=0)")
    run_p.add_argument("--verbose", "-v", action="store_true", help="Полный stdout/stderr упавших чеков")
    run_p.add_argument("--project", default=None, help=r"PROJECT=\<name\> → -k для project_filter-чеков")
    run_p.add_argument("--skip-precommit", action="store_true", help="SKIP_PRECOMMIT=1 — пропустить pre-commit шаг")

    list_p = sub.add_parser("list", help="Список id чеков (диагностический набор или gate-режим)")
    list_p.add_argument("--gate-mode", choices=_VALID_GATE_MODES, default=None, help="Фильтр по gate-режиму")

    sub.add_parser("fingerprint", help="Вычислить fingerprint дерева (диагностика кэша)")

    args = parser.parse_args(argv)
    root = _PROJECT_ROOT

    if args.command == "run":
        return _cmd_run(args, root)
    if args.command == "list":
        return _cmd_list(args, root)
    return _cmd_fingerprint(args, root)


# endregion FUNC_main

# endregion CLI


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(message)s", stream=sys.stderr)
    sys.exit(main())
