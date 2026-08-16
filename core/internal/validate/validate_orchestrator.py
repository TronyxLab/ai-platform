#!/usr/bin/env python3
# GREP_SUMMARY: validate-orchestrator, yaml, json-schema, ajv, python-jsonschema, discovery, schema-routing, lint-routing, error-aggregation, validate.sh-migration, pre-commit, exit1
# STRUCTURE: ▶ main(args) → ◇ --check-fqdn|--check-ports (subprocess conflict_checks) → ⊕ targets=args|discover_targets(os.walk) → ◇ for file: ┌resolve_schema(basename)┐ → ◇ skip|ai-platform-extension|validate_file → ◇ ajv|python subprocess → ∑ ERRORS → ⎋ exit 0|1
# region MODULE_CONTRACT
## @purpose  Python-порт оркестрации validate.sh (DevPlan 107). Все «тяжёлые» операции уже
##           делегированы в Python-CLI: jsonschema_validate.py (093 W1), conflict_checks.py
##           (Strangler 2026-07-31), node_yaml.py (--json-output). Этот модуль — последний
##           слой: auto-discovery файлов, schema-routing (node/module/ai-platform), выбор
##           валидатора (ajv|python), lint-routing, агрегация ошибок, exit-code.
## @scope    Вызывается entrypoint'ом core/entrypoints/validate.sh напрямую (двух-хоповый
##           фасад validate.sh → internal/validate/validate.sh схлопнут, DevPlan 173 W1.2) →
##           этот модуль → subprocess Python-CLI (jsonschema_validate/conflict_checks/node_yaml).
##           НЕ менять путь jsonschema_validate.py (core.internal.scripts — DevPlan 093 закрепил, D6).
## @invariants
##   - stderr-формат байт-идентичен log_imp() из core/lib/logging.sh с prefix=validate:
##     `[IMP:N][validate][block] msg`
##   - exit 0 только если ВСЕ файлы прошли ВСЕ схемы; exit 1 = ≥1 ошибка валидации
##   - schema-routing 1:1 по фактическому case validate.sh: node.yaml|yml → node.schema.json,
##     module.yaml|yml → module.schema.json, ai-platform.yaml|yml → ai-platform.schema.json;
##     всё остальное (включая llm/policy.yaml) → skip non-declaration (D1: llm-policy.schema.json
##     НЕ подключается — вне текущего контракта)
##   - .yml для ai-platform → REJECT (AD-2, 00 §5), НО ветка НЕ прерывается — schema-валидация
##     выполняется (D5)
##   - --lint = обычный arg: фильтруется `--*` в цикле → discovery НЕ запускается (D2 — lint-режима
##     в validate.sh нет, --lint = no-op pass exit 0)
##   - Авто-discovery: os.walk + sorted — КОРРЕКТНЫЕ пути без trailing \n (D3-фикс; shell
##     find|sort -z на BSD сливал весь вывод в один NUL-record → ложные «File not found»)
##   - Субпроцессы вызываются с cwd=REPO_ROOT (namespace-пакеты без __init__.py резолвятся
##     через cwd/PYTHONPATH — устойчивость к произвольной точке запуска, DD3)
## @rationale 251 LOC — последний «толстый» internal-скрипт области validate; миграция
##   оркестрации в Python завершает Strangler-декомпозицию (AGENTS.md §Языковая политика).
##   Байт-идентичный stderr сохраняет контракты make validate / make lint / CI-logs.
## ⚠️ TRAP[DECISION] · 2026-07-01 · — · Single manifest format (ai-platform.yaml only)
## ·   validate.sh ONLY validates ai-platform.yaml — project.yaml/declaration.yaml
## ·   are NOT supported (AD-2).
## ·   If a file uses .yml instead of .yaml, it's rejected with an explicit error.
## ·   Rejected: support both project.yaml and ai-platform.yaml — would fragment the config
## ·   Rejected: auto-migrate old format — risk of silent data loss in edge cases
## ⚠️ TRAP[DECISION] · 2026-07-01 · — · Port conflict check via --check-ports
## ·   validate.sh has a --check-ports mode that scans all ai-platform.yaml files in
## ·   PROJECTS_BASE for host_port uniqueness. deploy blocked if conflict found.
## ·   Rationale: separate validation step allows CI/CD to check before deploy.
## ·   PlatformError → raise (receive-канал dispatch также проверяет — defense-in-depth).
## ·   Rejected: rely only on per-deploy check — CI can fail faster (before pull)
## ⚠️ TRAP[DECISION] · 2026-07-31 · MED · Оркестратор делегирует Python-CLI через subprocess, не native import
## ·   Rejected: native import validate_yaml_against_schema/check_fqdn_conflict (риск: репликация
## ·   exit-code и stderr-контрактов CLI, дрейф от golden-тестов 093)
## ·   Reason: subprocess = та же граница, что у shell-фасада; stderr и exit-code сохраняются
## ·   дословно; CLI уже протестированы.
## ·   Rev: если orchestrator начнёт нуждаться в бизнес-логике валидации (не оркестрации) →
## ·   выделить в shared-модуль с собственными тестами.
## @changes
##   LAST_CHANGE: 2026-07-31 | Created (DevPlan 107 T2 — validate.sh orchestrator migration)
# endregion MODULE_CONTRACT

from __future__ import annotations

import importlib.util
import logging
import os
import subprocess
import sys
import tempfile
from collections.abc import Callable, Mapping, Sequence
from pathlib import Path

# ── sys.path bootstrap for direct-script invocation (validate.sh → python3 validate_orchestrator.py) ──
# os.path.dirname/abspath — STR-возврат (sys.path требует строки); pathlib-цепочка нечитаема
# (PTH100/PTH120 — per-file-ignore)
_PLATFORM_ROOT = os.path.join(
    os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
)
if _PLATFORM_ROOT not in sys.path:
    sys.path.insert(0, _PLATFORM_ROOT)

from core.internal.shared.env_facts import EnvironmentFacts, default_env_facts
from core.internal.shared.exceptions import PlatformError, PlatformFatalError

logger = logging.getLogger(__name__)

# ── Repo-relative anchors (DD3) ─────────────────────────────────────────────
# validate_orchestrator.py → parents[0]=validate, [1]=internal, [2]=core, [3]=repo root
REPO_ROOT = Path(__file__).resolve().parents[3]
SCHEMAS_DIR = Path(__file__).resolve().parents[2] / "schemas"

# Schema routing map — 1:1 по case из validate.sh L223-238 (D1: ровно 3 схемы)
_SCHEMA_ROUTING: dict[str, str] = {
    "node.yaml": "node.schema.json",
    "node.yml": "node.schema.json",
    "module.yaml": "module.schema.json",
    "module.yml": "module.schema.json",
    "ai-platform.yaml": "ai-platform.schema.json",
    "ai-platform.yml": "ai-platform.schema.json",
}


# region FUNC_emit
_FQDN_ARGS_MIN: int = 2  # args: <command> <project-dir>

# Известные no-op флаги (D2): `make lint` = validate.sh --lint → no-op pass exit 0
# (миграция legacy validate.sh --lint; discovery пропущен, targets=["--lint"] → skip в цикле).
# ⚠️ TRAP[BUG] · 2026-08-15 · P2 · Молчаливый skip неизвестных --флагов маскировал опечатки
# · Symptom: `validate --liint` тихо проходил с exit 0 (аргумент-флаг скипался как не-файл) —
# ·   оператор не видел, что проверка не запускалась.
# · Root: цикл валидации скипал ЛЮБОЙ аргумент со startswith("--"), не различая
# ·   известные no-op (--lint) и опечатки/неизвестные флаги.
# · Fix: явный отказ на неизвестный --флаг (parser.error-семантика exit 2);
# ·   --lint остаётся в allowlist как документированный no-op (D2 make lint).
# · Prevention: новый --флаг добавляется в _KNOWN_NOOP_FLAGS ЯВНО (fail-fast на опечатку).
_KNOWN_NOOP_FLAGS: frozenset[str] = frozenset({"--lint"})


def emit(imp: int, block: str, msg: str) -> None:
    """Эмитировать LDD-строку в stderr (байт-идентично log_imp) + logger.info для caplog.

    ▶ ┌(imp, block, msg)┐ → ⊕ f"[IMP:{imp}][validate][{block}] {msg}" → ⎋ stderr + logger.info

    ## @purpose — Единственный эмиттер сообщений: байт-идентичный формат
    ##             `[IMP:N][validate][block] msg` (log_imp из core/lib/logging.sh с prefix=validate).
    ## @io — ⇥ imp: int (1-10) · block: str · msg: str → ⎋ None (stderr + logger.info)
    ## @complexity — O(1)
    ## @invariants
    ##   - stderr-строка БЕЗ trailing-пробелов, один \n в конце (print default)
    ##   - logger.info дублирует строку — LDD-телеграфия под caplog (тесты)
    """
    line = f"[IMP:{imp}][validate][{block}] {msg}"
    print(line, file=sys.stderr)
    logger.info(line)


# endregion FUNC_emit


# region FUNC_detect_validator
def detect_validator(
    facts: EnvironmentFacts | None = None,
    find_spec_fn: Callable[[str], object] | None = None,
) -> str:
    """Определить доступный schema-валидатор: ajv (приоритет) → python-jsonschema → exit 1.

    ▶ ◇ which('ajv')? → "ajv" · ◇ find_spec('jsonschema')? → "python" · ✗ → emit ERROR + exit 1

    ## @purpose — Selection validator'а с приоритетом ajv > python (байт-идентично detect_validator() из validate.sh).
    ## @io — ⇥ facts: EnvironmentFacts | None (None = реальные системные; which ajv через DI),
    ##          find_spec_fn: Callable | None (DI, W-H DevPlan 163 — find_spec-канал;
    ##              None = importlib.util.find_spec) → ⎋ str ("ajv" | "python")
    ## @complexity — O(1)
    ## @invariants
    ##   - facts.which('ajv') непустой → "ajv" (не проверяем jsonschema)
    ##   - find_spec('jsonschema') не-None → "python"
    ##   - Ни один не доступен → PlatformFatalError (T3.6: business sys.exit → raise)
    ## @changes 2026-08-13 | DevPlan 160 W4b — +facts (which ajv через DI, убирает monkeypatch shutil)
    ## @changes 2026-08-13 | DevPlan 163 W-H — +find_spec_fn (0 патчей importlib.util в тестах)
    """
    facts = facts or default_env_facts()
    find_spec_impl = importlib.util.find_spec if find_spec_fn is None else find_spec_fn
    if facts.which("ajv"):
        return "ajv"
    try:
        spec = find_spec_impl("jsonschema")
    except (ModuleNotFoundError, ValueError):
        # jsonschema может отсутствовать в окружении или имя некорректно — тот же путь, что и None
        spec = None
    if spec is not None:
        return "python"
    # T3.6 (DevPlan 116 B4): business sys.exit → raise PlatformFatalError (нет валидатора — ручное действие)
    msg = "No validator found. Install: npm install -g ajv-cli ajv-formats  OR  pip3 install jsonschema pyyaml"
    raise PlatformFatalError(msg)


# endregion FUNC_detect_validator


# region FUNC_discover_targets
def discover_targets(root: Path) -> list[Path]:
    """Авто-обнаружение *.yaml/*.yml в root (core/internal), детерминированно, без NUL-артефактов.

    ▶ ┌root┐ → ○ os.walk → ○ filter *.yaml+*.yml → ⊕ sorted(key=str) → ⎋ list[Path]

    ## @purpose — Python-порт auto-discovery из validate.sh (find|sort -z|read -d '') с ИСПРАВЛЕНИЕМ D3.
    ## @io — ⇥ root: Path (корень поиска) → ⎋ list[Path] — нормализованные абсолютные пути, sorted
    ## @complexity — O(F) где F = число файлов в дереве
    ## @invariants
    ##   - Возвращает только *.yaml и *.yml (регистр точный — как `-name "*.yaml"` в find)
    ##   - Сортировка по строковому представлению пути — детерминированный порядок (эквивалент sort -z)
    ##   - Пути БЕЗ trailing \n и БЕЗ ../-компонентов (в отличие от shell-варианта)
    # ⚠️ TRAP[DECISION] · 2026-07-31 · MED · Auto-discovery: os.walk+sorted вместо репликации find|sort -z|read -d ''
    # · Rejected: репликация shell-pipeline 1:1 (риск: консервация trailing-\n corruption → ложные
    # ·   "File not found" для declaration-файлов)
    # · Reason: D3 — BSD sort -z сливает весь find-вывод (с \n-разделителями) в один NUL-record;
    # ·   `read -r -d ''` получает ВЕСЬ blob как один target с trailing \n → `[[ -f ]]` ложно fails.
    # ·   os.walk даёт корректные пути + детерминированный порядок. На текущем дереве (policy.yaml,
    # ·   non-declaration) вывод байт-идентичен по контенту skip-строки (нормализованный путь).
    # · Rev: если consumer полагается на ложный "File not found" для auto-discovered declaration-файлов
    # ·   → пересмотреть.
    """
    targets: list[Path] = []
    for dirpath, _dirnames, filenames in os.walk(root):
        targets.extend(Path(dirpath) / fn for fn in filenames if fn.endswith((".yaml", ".yml")))
    return sorted(targets, key=str)


# endregion FUNC_discover_targets


# region FUNC_resolve_schema
def resolve_schema(basename: str) -> str | None:
    """Роутинг имени файла → schema basename (1:1 по case validate.sh L223-238).

    ▶ ┌basename┐ → ◇ _SCHEMA_ROUTING.get(basename) → ⎋ str | None (None = non-declaration → skip)

    ## @purpose — Schema resolution: node/module/ai-platform → соответствующая схема; прочее → None (skip).
    ## @io — ⇥ basename: str → ⎋ str | None (schema basename, напр. "node.schema.json")
    ## @complexity — O(1)
    ## @invariants
    ##   - ТОЛЬКО 3 routing-пары (D1) — llm-policy.schema.json НЕ подключён (вне контракта)
    ##   - .yaml И .yml варианты обоих расширений маршрутизируются
    """
    return _SCHEMA_ROUTING.get(basename)


# endregion FUNC_resolve_schema


# region FUNC_check_project_extension
def check_project_extension(path: Path) -> bool:
    """Reject ai-platform.yml (AD-2: platform требует .yaml); НЕ прерывает выполнение ветки (D5).

    ▶ ┌path┐ → ◇ basename == "ai-platform.yml"? → emit REJECT + ⎋ False · иначе ⎋ True

    ## @purpose — Extension-проверка ai-platform: .yml → FAIL + ошибка агрегируется (D5: rc игнорируется).
    ## @io — ⇥ path: Path → ⎋ bool (False = .yml extension violation)
    ## @complexity — O(1)
    ## @invariants
    ##   - Только ТОЧНЫЙ basename "ai-platform.yml" — прочие .yml (docker-compose.yml и т.п.) не трогаются
    ##   - Возврат False НЕ прерывает main: schema-валидация выполняется (D5, байт-идентично validate.sh L230-234)
    """
    if Path(path).name == "ai-platform.yml":
        emit(
            9,
            "extension",
            f"FAIL: REJECT: '{path}' uses .yml extension — platform requires .yaml for ai-platform declarations",
        )
        return False
    return True


# endregion FUNC_check_project_extension


# region FUNC_validate_with_ajv
def validate_with_ajv(
    yaml_file: Path, schema_file: Path, run_cmd: Callable[..., subprocess.CompletedProcess[str]] | None = None
) -> bool:
    """Валидация через ajv-cli: YAML→JSON (node_yaml --json-output) → ajv validate.

    ▶ ┌(yaml_file, schema_file)┐ → ○ subprocess node_yaml --json-output → ○ tmp json → ○ ajv validate → ◇ rc → ⎋ bool

    ## @purpose — ajv-путь валидации (байт-идентично validate_with_ajv() из validate.sh L62-85).
    ## @io — ⇥ yaml_file: Path · schema_file: Path → ⎋ bool (True = valid)
    ## @complexity — O(S*I) доминирует ajv (subprocess)
    ## @invariants
    ##   - node_yaml fail → [IMP:9][validate][ajv] FAIL: Failed to parse YAML: <file>
    ##   - ajv fail → [IMP:9][validate][ajv] FAIL: <file>: <output> (однострочный формат)
    ##   - Успех → [IMP:7][validate][ajv] OK: <file>
    ##   - temp-файл удаляется в finally (mktemp + trap RETURN эквивалент)
    """
    tmp_json_path: Path | None = None
    runner = subprocess.run if run_cmd is None else run_cmd
    try:
        with tempfile.NamedTemporaryFile(
            encoding="utf-8", prefix="platform-validate-", suffix=".json", mode="w", delete=False
        ) as tmp:
            tmp_json_path = Path(tmp.name)

        proc = runner(
            [
                sys.executable,
                "-m",
                "core.internal.shared.node_yaml",
                "--file",
                str(yaml_file),
                "--json-output",
            ],
            capture_output=True,
            text=True,
            cwd=str(REPO_ROOT),
            check=False,
        )
        if proc.returncode != 0:
            emit(9, "ajv", f"FAIL: Failed to parse YAML: {yaml_file}")
            return False
        tmp_json_path.write_text(proc.stdout)

        proc2 = runner(
            [
                "ajv",
                "validate",
                "-s",
                str(schema_file),
                "-d",
                str(tmp_json_path),
                "--errors=text",
                "--all-errors",
            ],
            capture_output=True,
            text=True,
            cwd=str(REPO_ROOT),
            check=False,
        )
        if proc2.returncode != 0:
            emit(9, "ajv", f"FAIL: {yaml_file}: {proc2.stderr}")
            return False
        emit(7, "ajv", f"OK: {yaml_file}")
        return True
    finally:
        if tmp_json_path is not None:
            try:
                tmp_json_path.unlink(missing_ok=True)
            except OSError:
                # mktemp-trap эквивалент: cleanup best-effort, не маскирует основной результат
                logger.info("[IMP:4][validate][ajv] tmp cleanup skipped: %s", tmp_json_path)


# endregion FUNC_validate_with_ajv


# region FUNC_validate_with_python
def validate_with_python(
    yaml_file: Path, schema_file: Path, run_cmd: Callable[..., subprocess.CompletedProcess[str]] | None = None
) -> bool:
    """Валидация через jsonschema_validate.py (093 W1) — subprocess, cwd=REPO_ROOT (DD3).

    ▶ ┌(yaml_file, schema_file)┐ → ○ subprocess jsonschema_validate → ◇ rc → ⎋ bool

    ## @purpose — python-jsonschema путь (байт-идентично validate_with_python() из validate.sh L92-105).
    ##            DevPlan 116 B6 T5.4: jsonschema_validate теперь — wrapper над
    ##            core.internal.shared.schema_validator (единый вход соблюдён); этот вызов —
    ##            wrapper над wrapper'ом, не трогаем (orchestrator не является парсером).
    ## @io — ⇥ yaml_file: Path · schema_file: Path → ⎋ bool (True = valid)
    ## @complexity — O(S*I) доминирует jsonschema (subprocess)
    ## @invariants
    ##   - rc != 0 → [IMP:9][validate][python] FAIL: <file>:\n<output> (МНОГОСТРОЧНЫЙ формат —
    ##     literal \n между "file:" и error lines, L99-100 validate.sh)
    ##   - Успех → [IMP:7][validate][python] OK: <file>
    ##   - Путь модуля core.internal.scripts.jsonschema_validate НЕ меняется (D6)
    """
    runner = subprocess.run if run_cmd is None else run_cmd
    proc = runner(
        [
            sys.executable,
            "-m",
            "core.internal.scripts.jsonschema_validate",
            "--yaml-file",
            str(yaml_file),
            "--schema-file",
            str(schema_file),
        ],
        capture_output=True,
        text=True,
        cwd=str(REPO_ROOT),
        check=False,
    )
    if proc.returncode != 0:
        emit(9, "python", f"FAIL: {yaml_file}:\n{proc.stderr}")
        return False
    emit(7, "python", f"OK: {yaml_file}")
    return True


# endregion FUNC_validate_with_python


# region FUNC_validate_file
def validate_file(
    yaml_file: Path,
    schema_file: Path,
    validator: str,
    run_cmd: Callable[..., subprocess.CompletedProcess[str]] | None = None,
) -> bool:
    """Проверить существование файлов → dispatching по validator (ajv|python).

    ▶ ┌(yaml_file, schema_file, validator)┐ → ◇ -f yaml? → ◇ -f schema? → ⊕ emit Validating → ◇ validator → ⎋ bool

    ## @purpose — Центральная точка валидации одного файла (байт-идентично validate_file() из validate.sh L124-144).
    ## @io — ⇥ yaml_file: Path · schema_file: Path · validator: str ("ajv"|"python"),
    ##          run_cmd: Callable | None (DI, W-H — subprocess-канал; None = subprocess.run) → ⎋ bool
    ## @complexity — O(1) + делегирование валидатору
    ## @invariants
    ##   - Отсутствующий yaml → [IMP:9][validate][file] FAIL: File not found: <path>
    ##   - Отсутствующая schema → [IMP:9][validate][schema] FAIL: Schema not found: <path>
    ##   - Оба существуют → [IMP:6][validate][validate] Validating: <file> against <schema_basename>
    """
    if not Path(yaml_file).is_file():
        emit(9, "file", f"FAIL: File not found: {yaml_file}")
        return False
    if not Path(schema_file).is_file():
        emit(9, "schema", f"FAIL: Schema not found: {schema_file}")
        return False

    emit(6, "validate", f"Validating: {yaml_file} against {Path(schema_file).name}")

    if validator == "ajv":
        return validate_with_ajv(yaml_file, schema_file, run_cmd=run_cmd)
    return validate_with_python(yaml_file, schema_file, run_cmd=run_cmd)


# endregion FUNC_validate_file


# region FUNC_check_fqdn_conflict
def check_fqdn_conflict(
    project_dir: str, run_cmd: Callable[..., subprocess.CompletedProcess[str]] | None = None
) -> int:
    """Делегировать FQDN-uniqueness проверку в conflict_checks.py (Strangler 2026-07-31).

    ▶ ┌project_dir┐ → ○ subprocess conflict_checks check-fqdn → ⎋ child rc (exit passthrough)

    ## @purpose — --check-fqdn ветка: E1 (06 §5.4), stderr дочернего CLI passthrough.
    ## @io — ⇥ project_dir: str → ⎋ int (rc дочернего процесса: 0=ok, 1=conflict)
    ## @complexity — O(P) доминирует conflict_checks
    ## @invariants — cwd=REPO_ROOT; stderr не перехватывается (passthrough в родительский stderr)
    """
    runner = subprocess.run if run_cmd is None else run_cmd
    proc = runner(
        [
            sys.executable,
            "-m",
            "core.internal.validate.conflict_checks",
            "check-fqdn",
            project_dir,
        ],
        cwd=str(REPO_ROOT),
        check=False,
    )
    return proc.returncode


# endregion FUNC_check_fqdn_conflict


# region FUNC_check_port_conflict
def check_port_conflict(base: str, run_cmd: Callable[..., subprocess.CompletedProcess[str]] | None = None) -> int:
    """Делегировать host_port-uniqueness проверку в conflict_checks.py.

    ▶ ┌base┐ → ○ subprocess conflict_checks check-ports → ⎋ child rc

    ## @purpose — --check-ports ветка: E2 host_port uniqueness, passthrough.
    ## @io — ⇥ base: str (projects base; может быть "" → CLI сам резолвит default) → ⎋ int
    ## @complexity — O(P) доминирует conflict_checks
    ## @invariants — cwd=REPO_ROOT; пустая base передаётся как есть (байт-идентично L183-194)
    """
    runner = subprocess.run if run_cmd is None else run_cmd
    proc = runner(
        [
            sys.executable,
            "-m",
            "core.internal.validate.conflict_checks",
            "check-ports",
            base,
        ],
        cwd=str(REPO_ROOT),
        check=False,
    )
    return proc.returncode


# endregion FUNC_check_port_conflict


# region FUNC_main
def main(
    argv: list[str] | None = None,
    *,
    validator: str | None = None,
    discover_fn: Callable[[Path], list[Path]] | None = None,
    run_cmd: Callable[..., subprocess.CompletedProcess[str]] | None = None,
    env: Mapping[str, str] | None = None,
) -> int:
    """CLI entry: спец-флаги → targets → discovery-if-empty → цикл валидации → агрегация.

    ▶ ┌argv┐ → ◇ --check-fqdn? → ⎋ rc · ◇ --check-ports? → ⎋ rc · ⊕ detect_validator → ○ targets=args|discover → ◇ targets empty → OK · ○ for file: ◇ --* skip · ◇ resolve_schema? → ◇ ai-platform: extension+migration → ○ validate_file → ∑ errors → ◇ errors>0 → FAIL exit 1 · ⎋ OK exit 0

    ## @purpose — Полная оркестрация (байт-идентично main() из validate.sh L172-248):
    ##             спец-флаги --check-fqdn/--check-ports → иначе валидация target-файлов.
    ## @io — ⇥ argv: list[str] | None (None = sys.argv[1:]),
    ##       validator: str | None (W5 T5.3 DI — override detect_validator(); None = авто-детект),
    ##       discover_fn: Callable | None (DI, W-H DevPlan 163 — discover_targets; None = канон),
    ##       run_cmd: Callable | None (DI — subprocess-канал validate/conflict_checks; None = subprocess.run)
    ##       → ⎋ int exit code
    ## @complexity — O(F * S * I) где F = число файлов
    ## @invariants
    ##   - --check-fqdn без аргумента → [IMP:10][validate][fqdn] ERROR: --check-fqdn requires a
    ##     project directory argument + exit 1 (L175-177)
    ##   - --check-ports: arg → PROJECTS_BASE env → core/projects (если существует) → "" (L183-194)
    ##   - targets пусто ПОСЛЕ discovery → [IMP:6][validate][main] No YAML files found to validate + exit 0
    ##   - args непустые → discovery ПРОПУЩЕН (D2: make lint = validate --lint → 0 файлов → OK exit 0)
    ##   - ERRORS > 0 → [IMP:9][validate][result] FAIL: N validation error(s) found + exit 1
    ##   - ERRORS == 0 → [IMP:8][validate][result] OK: All files valid + exit 0
    ##   - W5 T5.3: validator= параметром — убирает 4 monkeypatch detect_validator в тестах;
    ##     поведение без параметра не меняется (авто-детект через detect_validator(facts=None))
    """
    args = list(sys.argv[1:] if argv is None else argv)
    source: Mapping[str, str] = os.environ if env is None else env
    discover_impl = discover_targets if discover_fn is None else discover_fn
    runner = subprocess.run if run_cmd is None else run_cmd

    # ── Спец-флаги (DD4: обрабатываются в orchestrator'е, фасад — только exec) ──
    if args and args[0] == "--check-fqdn":
        if len(args) < _FQDN_ARGS_MIN or not args[1]:
            emit(10, "fqdn", "ERROR: --check-fqdn requires a project directory argument")
            return 1
        return check_fqdn_conflict(args[1], run_cmd=runner)

    if args and args[0] == "--check-ports":
        base = args[1] if len(args) > 1 and args[1] else ""
        if not base:
            base = source.get("PROJECTS_BASE", "")
            if not base:
                projects_dir = REPO_ROOT / "core" / "projects"
                if projects_dir.is_dir():
                    base = str(projects_dir)
        return check_port_conflict(base, run_cmd=runner)

    # ── Основной поток валидации ──
    emit(6, "start", "Detecting schema validator")
    try:
        # W5 T5.3: validator= override (тесты) → иначе авто-детект
        chosen_validator = validator if validator else detect_validator()
    except PlatformError as e:
        logger.critical("[IMP:10][main] Unhandled platform error (exit=%d): %s", e.exit_code, e)
        print(f"[FATAL] {e}", file=sys.stderr)
        return e.exit_code
    emit(6, "start", f"Using validator: {chosen_validator}")

    # Files to validate: from args or auto-discover (D2: непустые args → discovery пропущен)
    # Sequence (ковариантно): targets принимает и list[str] (args), и list[Path] (discover_targets)
    targets: Sequence[Path | str] = list(args)
    if not targets:
        targets = discover_impl(Path(__file__).resolve().parent.parent)

    if not targets:
        emit(6, "main", "No YAML files found to validate")
        return 0

    errors = 0
    for file in targets:
        # Skip flag arguments (e.g. --lint) that are not file paths (D2).
        # Только документированные no-op флаги (make lint); неизвестный --флаг —
        # явный отказ exit 2 (TRAP[BUG] 2026-08-15: опечатки не должны тихо скипаться).
        if str(file).startswith("--"):
            if str(file) in _KNOWN_NOOP_FLAGS:
                continue
            emit(10, "main", f"ERROR: unknown flag: {file} (known no-op: {sorted(_KNOWN_NOOP_FLAGS)})")
            return 2

        basename = Path(file).name
        schema_name = resolve_schema(basename)

        if schema_name is None:
            emit(6, "skip", f"Skipping non-declaration file: {file}")
            continue

        schema_file = SCHEMAS_DIR / schema_name

        # ai-platform ветка: extension-check НЕ short-circuit'ит (D5) + migration INFO
        if basename in {"ai-platform.yaml", "ai-platform.yml"}:
            if not check_project_extension(Path(file)):
                errors += 1
            emit(6, "migration", f"INFO: '{file}' — единый формат манифеста (AD-2)")

        if not validate_file(Path(file), schema_file, chosen_validator, run_cmd=runner):
            errors += 1

    if errors > 0:
        emit(9, "result", f"FAIL: {errors} validation error(s) found")
        return 1

    emit(8, "result", "OK: All files valid")
    return 0


# endregion FUNC_main


if __name__ == "__main__":
    sys.exit(main())
