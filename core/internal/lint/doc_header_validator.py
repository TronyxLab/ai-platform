#!/usr/bin/env python3
# GREP_SUMMARY: doc-headers, module-contract, structure, regions, yaml-purpose, namelint, make-target, manifest, staged
# STRUCTURE: ▶ validate_files(staged) → ◇ ext filter (py|sh|md|yaml|yml) → ◇ GREP_SUMMARY presence+keywords(staged) →
#            ◇ STRUCTURE → ◇ MODULE_CONTRACT (non-md) → ◇ regions balance → ◇ YAML @purpose → ◇ .md sh-refs (backtick) → ⎋ errors
#            ▶ namelint: yaml.safe_load(manifest) + .PHONY (Makefile + makefiles/*.mk) → ◇ allowed/lifecycle/exceptions → ⎋ errors
# region MODULE_CONTRACT
## @purpose  Валидация документ-хедеров staged-файлов (AC2 DevPlan 106) + namelint (make-target names vs manifest).
##           Strangler-порт check-doc-headers.sh (236 LOC) + lint.sh check_namelint() (awk → yaml.safe_load).
## @scope    Импортируемый API: validate_file, validate_files, validate_make_target_names.
##           CLI: python3 -m ... doc-headers <files...> | namelint.
## @invariants
##   - doc-headers: ext py|sh|md|yaml|yml (из последней точки, shell ${file##*.}); skip .venv/node_modules/__pycache__
##   - presence-проверки — первые 10 строк (порт head -10); keywords staged: БЕЗ strip HTML и БЕЗ skip flags
##   - namelint: системные исключения из секции name_linter манифеста (НЕ hardcoded case-паттерны)
##   - namespace_collision_names (name_linter) реализован в tests/gates/test_gate_manifest_integrity.py
##     (NAMESPACE_COLLISION_NAMES ← manifest; test_module_targets_use_canonical_names) — namelint здесь
##     НЕ дублирует (DRY, DevPlan 128 W3); манифест = код
##   - Без аргументов doc-headers → pass (exit 0)
## @rationale awk-парсинг YAML — источник хрупкости (P3); yaml.safe_load устраняет класс ошибок.
##            namelint размещён здесь (не в третьем модуле) по Brief §План (строка 28) — Rev: >150 LOC → вынести.
## @changes 2026-07-31 | Created (DevPlan 106 Strangler-Fig)
# endregion MODULE_CONTRACT

import argparse
import logging
import re
import sys
from pathlib import Path
from typing import cast

import yaml

from core.internal.lint.grepsummary_validator import (
    extract_keywords,
    extract_sh_refs,
    validate_keywords_present,
)

logger = logging.getLogger("doc_header_validator")

# ⚠️ TRAP[DECISION] · 2026-08-15 · — · Категорийное правило вместо name_linter.system_exceptions
# · (DevPlan 171 W3.6): перечень имён {help, venv, pre-commit-install, pre-commit-run,
# ·   _get_all_profiles} заменён на КАТЕГОРИИ: стандартные служебные таргеты make
# ·   (help/venv — документированный набор), префиксы (test-/gate-/pre-commit- из манифеста
# ·   name_linter.system_prefixes) и `_`-префиксные имена (автоматически). Новый служебный
# ·   таргет не требует правки валидатора — достаточно попасть в категорию.
# · Rev: если категории перестанут покрывать новые классы служебных таргетов — расширить
# ·   категорийное правило (не возвращать перечень).

# Стандартные служебные таргеты make (категорийное правило, DevPlan 171 W3.6).
# help-all — системное исключение (План 175 W1.3): полный реестр глаголов, пара к help.
STANDARD_MAKE_SERVICE_TARGETS: frozenset[str] = frozenset({"help", "help-all", "venv"})

# ⚠️ TRAP[DECISION] · 2026-07-31 · — · namelint размещён в doc_header_validator.py
# · Rejected: третий модуль name_validator.py
# · Reason: Brief §План (строка 28) явно перечисляет namelint в doc_header_validator.py;
# ·   File Manifest Brief содержит ровно 2 Python-модуля — третий модуль создал бы drift
# ·   между DevPlan и Brief.
# · Rev: при росте namelint-логики >150 LOC — вынести в отдельный модуль с обновлением манифеста.

_REGION_OPEN_RE = re.compile(r"^[ \t]*# region", re.MULTILINE)
_REGION_CLOSE_RE = re.compile(r"^[ \t]*# endregion", re.MULTILINE)
_VALID_EXTS = {"py", "sh", "md", "yaml", "yml"}
_SKIP_PARTS = {".venv", "node_modules", "__pycache__"}
_PHONY_RE = re.compile(r"^\.PHONY:\s*(.+)$")


# region FUNC__read_text
_MAX_DEPTH: int = 5  # максимальная глубина relative_to(internal).parts при поиске


def _read_text(file: Path) -> str | None:
    """Read file as UTF-8 with replace-errors; None on OSError (binary/unreadable → skip)."""
    try:
        return file.read_text(encoding="utf-8", errors="replace")
    except OSError as e:
        logger.warning("[IMP:7][_read_text][warn] cannot read %s: %s", file, e)
        return None


# endregion FUNC__read_text


# region FUNC__default_repo_root
def _default_repo_root() -> Path:
    """Resolve repo root: core/internal/lint/xx.py → parents[3] (zero hardcoded paths)."""
    return Path(__file__).resolve().parents[3]


# endregion FUNC__default_repo_root


# region FUNC_check_regions_balanced
def check_regions_balanced(file: Path) -> list[str]:
    """Count # region vs # endregion lines; imbalance → error.

    ▶ ┌file┐ → ○ count ^[ \t]*# region → ○ count ^[ \t]*# endregion → ◇ equal? → ⎋ errors

    ## @purpose — Порт check-doc-headers.sh check_regions_balanced() (строки 30-40).
    ## @io — ⇥ file: Path → ⎋ list[str] — [FAIL] при opens != closes
    ## @complexity — O(L) — два regex-прохода по строкам
    ## @invariants — считаются ВСЕ # region, включая FUNC_-регионы (grep -c семантика)
    ## @rationale — region/endregion — парные маркеры модуля и функций; разбаланс = потерянный блок кода.
    """
    text = _read_text(file)
    if text is None:
        return []
    opens = len(_REGION_OPEN_RE.findall(text))
    closes = len(_REGION_CLOSE_RE.findall(text))
    if opens != closes:
        msg = f"[FAIL] {file}: #region count ({opens}) != #endregion count ({closes})"
        logger.info("[IMP:9][check_regions_balanced][fail] %s", msg)
        return [msg]
    logger.info("[IMP:9][check_regions_balanced][pass] %s: %d region(s) balanced", file, opens)
    return []


# endregion FUNC_check_regions_balanced


# region FUNC_check_grep_summary_presence
def check_grep_summary_presence(file: Path) -> list[str]:
    """GREP_SUMMARY presence in first 10 lines + staged keyword validation.

    ▶ ┌file┐ → ○ first 10 lines → ◇ '# GREP_SUMMARY:' → ○ extract_keywords(staged) → ○ validate_keywords_present → ⎋ errors

    ## @purpose — Порт check-doc-headers.sh check_grep_summary() (строки 43-77): presence + keywords.
    ## @io — ⇥ file: Path → ⎋ list[str] — [FAIL] presence/keyword ошибки
    ## @complexity — O(K * F) — K keywords, F размер файла
    ## @invariants — presence: '# GREP_SUMMARY:' (substring) в первых 10 строках; keywords из первой
    ##               matching-строки, staged-парсинг (без strip/flags)
    ## @rationale — staged-парсинг keywords отличается от scan-режима (без strip, без skip) — §2.3.
    """
    text = _read_text(file)
    if text is None:
        return []
    first_10 = text.splitlines()[:10]
    if not any("# GREP_SUMMARY:" in line for line in first_10):
        msg = f"[FAIL] {file}: Missing '# GREP_SUMMARY:' in first 10 lines"
        logger.info("[IMP:9][check_grep_summary_presence][fail] %s", msg)
        return [msg]
    summary_line = next(line for line in first_10 if "# GREP_SUMMARY:" in line)
    return validate_keywords_present(file, extract_keywords(summary_line, mode="staged"))


# endregion FUNC_check_grep_summary_presence


# region FUNC_check_structure
def check_structure(file: Path) -> list[str]:
    """'# STRUCTURE:' presence in first 10 lines.

    ▶ ┌file┐ → ○ first 10 lines → ◇ '# STRUCTURE:' present? → ⎋ errors

    ## @purpose — Порт check-doc-headers.sh check_structure() (строки 94-103).
    ## @io — ⇥ file: Path → ⎋ list[str] — [FAIL] при отсутствии
    ## @complexity — O(L) — проверка первых 10 строк
    ## @invariants — '# STRUCTURE:' (substring) в первых 10 строках
    ## @rationale — STRUCTURE — обязательный элемент семантического markup (grep-навигация агентов).
    """
    text = _read_text(file)
    if text is None:
        return []
    first_10 = text.splitlines()[:10]
    if not any("# STRUCTURE:" in line for line in first_10):
        msg = f"[FAIL] {file}: Missing '# STRUCTURE:' in first 10 lines"
        logger.info("[IMP:9][check_structure][fail] %s", msg)
        return [msg]
    logger.info("[IMP:9][check_structure][pass] %s", file)
    return []


# endregion FUNC_check_structure


# region FUNC_check_module_contract
def check_module_contract(file: Path) -> list[str]:
    """'# region MODULE_CONTRACT' + '# endregion MODULE_CONTRACT' presence.

    ▶ ┌file┐ → ◇ region present? → ◇ endregion present? → ⊕ errors → ⎋ list[str]

    ## @purpose — Порт check-doc-headers.sh check_module_contract() (строки 80-91).
    ## @io — ⇥ file: Path → ⎋ list[str] — [FAIL] отсутствия region/endregion
    ## @complexity — O(L) — два substring-поиска
    ## @invariants — обе стороны региона обязательны (модульный контракт — замкнутый блок)
    ## @rationale — MODULE_CONTRACT — единственный источник бизнес-контекста модуля (zero-context survival).
    """
    text = _read_text(file)
    if text is None:
        return []
    errors: list[str] = []
    if "# region MODULE_CONTRACT" not in text:
        msg = f"[FAIL] {file}: Missing '# region MODULE_CONTRACT'"
        logger.info("[IMP:9][check_module_contract][fail] %s", msg)
        errors.append(msg)
    if "# endregion MODULE_CONTRACT" not in text:
        msg = f"[FAIL] {file}: Missing '# endregion MODULE_CONTRACT'"
        logger.info("[IMP:9][check_module_contract][fail] %s", msg)
        errors.append(msg)
    if not errors:
        logger.info("[IMP:9][check_module_contract][pass] %s", file)
    return errors


# endregion FUNC_check_module_contract


# region FUNC_check_yaml_purpose
def check_yaml_purpose(file: Path) -> list[str]:
    """'## @purpose' presence (yaml/yml only).

    ▶ ┌file┐ → ◇ '## @purpose' present? → ⎋ errors

    ## @purpose — Порт check-doc-headers.sh check_yaml_purpose() (строки 106-113).
    ## @io — ⇥ file: Path → ⎋ list[str] — [FAIL] при отсутствии
    ## @complexity — O(L) — substring-поиск
    ## @invariants — применяется только к .yaml/.yml (вызывается из validate_file по ext)
    ## @rationale — YAML-артефакты (манифесты, module.yaml) обязаны декларировать @purpose.
    """
    text = _read_text(file)
    if text is None:
        return []
    if "## @purpose" not in text:
        msg = f"[FAIL] {file}: Missing '## @purpose' tag (required for YAML files)"
        logger.info("[IMP:9][check_yaml_purpose][fail] %s", msg)
        return [msg]
    logger.info("[IMP:9][check_yaml_purpose][pass] %s", file)
    return []


# endregion FUNC_check_yaml_purpose


# region FUNC_check_md_sh_refs
def check_md_sh_refs(file: Path, repo_root: Path | None = None) -> list[str]:
    """Backtick .sh refs in .md: resolve via known dirs + lib/-strip + find core/internal maxdepth 5.

    ▶ ┌file, repo_root┐ → ○ extract_sh_refs(backtick_only=True) → ○ skip абсолютных /* → ○ resolve dirs → ○ lib/ strip → ○ find internal → ⎋ errors

    ## @purpose — Порт check-doc-headers.sh check_md_sh_refs() (строки 121-171).
    ## @io — ⇥ file: Path; ⇥ repo_root: Path | None (default: проект из parents[3]) → ⎋ list[str]
    ## @complexity — O(R * F) — R ссылок, F файлов в core/internal (find maxdepth 5)
    ## @invariants — skip абсолютных /*; resolve: root/ref, root/core/{entrypoints,lib,bootstrap}/ref;
    ##               lib/<name> → core/lib/<name> (TRAP[BUG-FIX]); find core/internal/ maxdepth 5 (basename, без '/')
    ## @rationale — git-модель check-doc-headers.sh: ссылки в .md обязаны разрешаться локально;
    ##               skip-list отклонён (TRAP[DECISION]) — фикс документации лучше.
    """
    # 🧐 TRAP[DECISION] · 2026-07-11 · — · deleted script refs в .md — НЕ skip-list
    # · Rejected: add skip-list for deleted scripts
    # · Reason: skip-list маскирует документационный drift. Fix: (a) убрать backticks у удалённых
    # ·   скриптов в .md, или (b) починить резолюцию. Оба варианта лучше skip-list.
    # · Rev: если backtick .sh ref в .md не резолвится — чинить док или резолвер.
    # 🧐 TRAP[BUG-FIX] · 2026-07-21 · Handle `lib/<name>.sh` references in prose
    # · Reason: AGENTS.md ссылается на libs как `lib/ssh.sh`. Валидатор пробовал core/lib/lib/ssh.sh
    # ·   (неверно). Strip ведущего 'lib/' префикса и повторная проверка против core/lib/ — по FS.
    root = repo_root or _default_repo_root()
    text = _read_text(file)
    if text is None:
        return []
    refs = extract_sh_refs(text, backtick_only=True)
    if not refs:
        return []
    errors: list[str] = []
    for ref in refs:
        # Skip absolute paths — validated on target machine, not locally
        if ref.startswith("/"):
            continue
        if _ref_exists_in_dirs(root, ref):
            continue
        if ref.startswith("lib/") and (root / "core/lib" / ref[4:]).is_file():
            continue
        if "/" not in ref and _find_in_internal(root, ref):
            continue
        msg = f"[FAIL] {file}: Referenced script '{ref}' not found"
        logger.info("[IMP:9][check_md_sh_refs][fail] %s", msg)
        errors.append(msg)
    if not errors:
        logger.info("[IMP:9][check_md_sh_refs][pass] %s: %d ref(s) resolve", file, len(refs))
    return errors


# endregion FUNC_check_md_sh_refs


# region FUNC__ref_exists_in_dirs
def _ref_exists_in_dirs(root: Path, ref: str) -> bool:
    """Check ref against root + known script dirs (порт -f проверок check_md_sh_refs:143)."""
    for candidate in (
        root / ref,
        root / "core/entrypoints" / ref,
        root / "core/lib" / ref,
        root / "core/bootstrap" / ref,
    ):
        if candidate.is_file():
            return True
    return False


# endregion FUNC__ref_exists_in_dirs


# region FUNC__find_in_internal
def _find_in_internal(root: Path, ref: str) -> bool:
    """Recursive basename search in core/internal/ maxdepth 5 (порт find | grep -q, check-doc-headers.sh:162)."""
    internal = root / "core" / "internal"
    if not internal.is_dir():
        return False
    return any(path.is_file() and len(path.relative_to(internal).parts) <= _MAX_DEPTH for path in internal.rglob(ref))


# endregion FUNC__find_in_internal


# region FUNC__should_skip
def _should_skip(file: Path) -> bool:
    """Skip .venv/node_modules/__pycache__ paths.

    ▶ ┌file┐ → ○ split path components → ◇ intersect _SKIP_PARTS → ⎋ bool

    ## @purpose — Порт case-фильтра check-doc-headers.sh:185-187 (`.venv/*|node_modules/*|__pycache__/*`).
    ## @io — ⇥ file: Path → ⎋ bool (True = skip)
    ## @complexity — O(P) — P компонентов пути
    ## @invariants — компонентный матч (любая директория в пути) — суперсет префиксного case shell;
    ##               покрывает и относительные (pre-commit) и абсолютные (тесты) пути
    ## @rationale — префиксный case shell не ловит вложенные .venv; компонентный матч семантически
    ##               корректен и строже (не валидирует мусорные пути).
    """
    parts = set(str(file).replace("\\", "/").split("/"))
    return bool(parts & _SKIP_PARTS)


# endregion FUNC__should_skip


# region FUNC__file_passes_filter
def _file_passes_filter(file: Path) -> bool:
    """Ext + skip фильтр (порт case-веток check-doc-headers.sh:179-187)."""
    name = file.name
    if "." not in name:
        return False
    return name.rsplit(".", 1)[1] in _VALID_EXTS and not _should_skip(file)


# endregion FUNC__file_passes_filter


# region FUNC_validate_file
def validate_file(file: Path, repo_root: Path | None = None) -> list[str]:
    """Apply ext filter + skip rules + all doc-header checks for one file.

    ▶ ┌file┐ → ◇ passes filter? → ○ [CHECK] → ○ 6 проверок (по ext) → ⎋ errors

    ## @purpose — Порт check-doc-headers.sh main-цикла (строки 174-226) для одного файла.
    ## @io — ⇥ file: Path; ⇥ repo_root: Path | None → ⎋ list[str] — [FAIL] ошибки ([] при skip или всё ок)
    ## @complexity — O(K * F + R) — K keywords, R .sh refs
    ## @invariants — ext из последней точки (shell ${file##*.}, case-sensitive); skip-части по компонентам
    ## @rationale — фильтрация перенесена в Python для testability; [CHECK]-строка на stdout (формат shell).
    """
    if not _file_passes_filter(file):
        return []
    print(f"[CHECK] {file}")
    errors: list[str] = []
    errors.extend(check_grep_summary_presence(file))
    errors.extend(check_structure(file))
    if file.name.rsplit(".", 1)[1] != "md":
        errors.extend(check_module_contract(file))
    errors.extend(check_regions_balanced(file))
    if file.name.rsplit(".", 1)[1] in {"yaml", "yml"}:
        errors.extend(check_yaml_purpose(file))
    if file.name.rsplit(".", 1)[1] == "md":
        errors.extend(check_md_sh_refs(file, repo_root))
    return errors


# endregion FUNC_validate_file


# region FUNC_validate_files
def validate_files(files: list[str], repo_root: Path | None = None) -> tuple[list[str], int]:
    """Iterate files, aggregate errors; no files → pass.

    ▶ ┌files┐ → ○ loop validate_file (по фильтру) → ⊕ errors → ⎋ (errors, checked_count)

    ## @purpose — Порт check-doc-headers.sh main-цикла; без аргументов → pass (exit 0).
    ## @io — ⇥ files: list[str]; ⇥ repo_root: Path | None → ⎋ (list[str], int) — (ошибки, число проверенных файлов)
    ## @complexity — O(N * K) — N файлов, K проверок на файл
    ## @invariants — без аргументов → ([], 0); каждый файл ровно один [CHECK]
    ## @rationale — агрегация ВСЕХ ошибок (shell: «Reports all errors across files», не first-error).
    """
    errors: list[str] = []
    checked = 0
    for f in files:
        path = Path(f)
        if not _file_passes_filter(path):
            continue
        checked += 1
        errors.extend(validate_file(path, repo_root))
    logger.info("[IMP:9][validate_files][result] %d file(s) checked, %d error(s)", checked, len(errors))
    return errors, checked


# endregion FUNC_validate_files


# region FUNC__parse_phony
def _parse_phony(makefile: Path) -> set[str]:
    r"""Parse .PHONY: lines from a Makefile → set of target names.

    ▶ ┌makefile┐ → ○ read lines → ○ match ^\.PHONY:\s*(.+) → ⊕ targets → ⎋ set[str]

    ## @purpose — Порт grep '^.PHONY:' | sed 's/^.PHONY: *//' | tr ' ' '\n' (lint.sh:119-126).
    ## @io — ⇥ makefile: Path → ⎋ set[str] — имена таргетов (пустой set при отсутствии/ошибке чтения)
    ## @complexity — O(L) — L строк Makefile
    ## @invariants — литеральный '.PHONY:' (shell grep '^.PHONY:' с точкой-anychar — не воспроизводится);
    ##               пустые .PHONY-строки игнорируются (sed '/^$/d' семантика)
    ## @rationale — root Makefile и makefiles/*.mk сканируются оба (W4-E4 include-split) — lint.sh:114-126.
    """
    try:
        lines = makefile.read_text(encoding="utf-8").splitlines()
    except OSError:
        return set()
    targets: set[str] = set()
    for line in lines:
        m = _PHONY_RE.match(line)
        if m:
            targets.update(m.group(1).split())
    return targets


# endregion FUNC__parse_phony


# region FUNC_validate_make_target_names
def validate_make_target_names(repo_root: Path) -> list[str]:
    """Validate .PHONY make targets against manifest allowed/lifecycle + system exceptions.

    ▶ ┌repo_root┐ → ○ manifest exists → ○ yaml.safe_load → ○ .PHONY (Makefile + makefiles/*.mk) →
    ◇ allowed → ◇ lifecycle → ◇ prefixes → ◇ literals → ⊕ errors → ⎋ list[str]

    ## @purpose — Порт lint.sh check_namelint() (строки 86-169): awk-парсинг заменён yaml.safe_load (P3).
    ## @io — ⇥ repo_root: Path → ⎋ list[str] — [FAIL] ошибки (manifest/Makefile отсутствуют → FAIL, lint.sh:90-100)
    ## @complexity — O(T) — T make-таргетов
    ## @invariants — порядок проверок: allowed → lifecycle → system_prefixes → категории
    ##               (STANDARD_MAKE_SERVICE_TARGETS + `_`-префикс — DevPlan 171 W3.6);
    ##               namespace_collision_names проверяется в tests/gates/test_gate_manifest_integrity.py
    ##               (не дублируется здесь — DRY, DevPlan 128 W3; манифест = код)
    ## @rationale — категорийное правило вместо перечня имён (W3.6): новый служебный таргет
    ##               без правки валидатора; system_prefixes из манифеста — Source of Truth (G3).
    """
    manifest = repo_root / "core" / "entrypoint-manifest.yaml"
    if not manifest.is_file():
        msg = f"[FAIL] Manifest not found: {manifest}"
        logger.info("[IMP:9][validate_make_target_names][fail] %s", msg)
        return [msg]
    makefile = repo_root / "Makefile"
    if not makefile.is_file():
        msg = f"[FAIL] Root Makefile not found: {makefile}"
        logger.info("[IMP:9][validate_make_target_names][fail] %s", msg)
        return [msg]
    try:
        with Path(manifest).open(encoding="utf-8") as f:
            data = yaml.safe_load(f)  # pyright: ignore[reportAny] W11-G4: pyyaml без stubs → Any; isinstance-гейт ниже
    except (yaml.YAMLError, OSError) as e:
        msg = f"[FAIL] Cannot parse manifest {manifest}: {e}"
        logger.info("[IMP:9][validate_make_target_names][fail] %s", msg)
        return [msg]
    if not isinstance(data, dict):
        msg = f"[FAIL] Manifest {manifest} has no top-level mapping"
        logger.info("[IMP:9][validate_make_target_names][fail] %s", msg)
        return [msg]
    allowed = set(data.get("allowed_verbs") or [])
    lifecycle = set(data.get("module_lifecycle") or [])
    name_linter = data.get("name_linter") or {}
    system_prefixes = tuple(name_linter.get("system_prefixes") or [])
    # Проверка коллизий имён реализована в tests/gates/test_gate_manifest_integrity.py
    # (NAMESPACE_COLLISION_NAMES ← manifest name_linter; test_module_targets_use_canonical_names —
    #   модульные Makefile не используют голые deploy/build). Здесь НЕ дублируется (DRY).
    # Манифест отражает код (гейт читает name_linter.namespace_collision_names) — «манифест = код».
    targets: set[str] = set()
    targets |= _parse_phony(makefile)
    mk_dir = repo_root / "makefiles"
    if mk_dir.is_dir():
        for mk in sorted(mk_dir.glob("*.mk")):
            targets |= _parse_phony(mk)
    if not targets:
        msg = "[FAIL] No .PHONY targets found in root Makefile or makefiles/*.mk"
        logger.info("[IMP:9][validate_make_target_names][fail] %s", msg)
        return [msg]
    logger.info("[IMP:7][validate_make_target_names][start] %d target(s) loaded", len(targets))
    errors: list[str] = []
    for target in sorted(targets):
        if (
            target in allowed
            or target in lifecycle
            or target.startswith(system_prefixes)
            or target in STANDARD_MAKE_SERVICE_TARGETS
            or target.startswith("_")
        ):
            continue
        msg = f"[FAIL] Target '{target}' is not in allowed_verbs and not a system exception"
        logger.info("[IMP:9][validate_make_target_names][fail] %s", msg)
        errors.append(msg)
    if not errors:
        logger.info("[IMP:9][validate_make_target_names][pass] %d target(s) validated", len(targets))
    return errors


# endregion FUNC_validate_make_target_names


# region FUNC_build_parser
def build_parser() -> argparse.ArgumentParser:
    """CLI parser: doc-headers <files...> | namelint."""
    parser = argparse.ArgumentParser(description="Doc-header validation + namelint (DevPlan 106)")
    subparsers = parser.add_subparsers(dest="command", required=True)
    dh = subparsers.add_parser("doc-headers", help="Validate doc headers of staged files")
    dh.add_argument("files", nargs="*", help="Files to validate (pre-commit staged paths)")
    subparsers.add_parser("namelint", help="Validate make target names against manifest")
    return parser


# endregion FUNC_build_parser


# region FUNC_main
def main() -> int:
    """CLI entry: doc-headers/namelint → exit 0/1; [FAIL]/[PASS] на stdout, IMP-логи на stderr."""
    logging.basicConfig(level=logging.INFO, format="%(message)s", stream=sys.stderr)
    args = build_parser().parse_args()
    repo_root = _default_repo_root()
    command = cast(str, args.command)  # W11-G4: argparse Namespace → Any
    if command == "doc-headers":
        # W11-G4/фикс-восстановление: args.files читается ТОЛЬКО внутри ветки doc-headers —
        # у namelint-субпарсера атрибута files нет (иначе AttributeError)
        files = cast(list[str], args.files)
        errors, checked = validate_files(files, repo_root)
        if errors:
            for err in errors:
                print(err)
            print("[FAIL] check-doc-headers: One or more files failed documentation header validation")
            logger.info("[IMP:9][doc-headers][main] Validation FAILED — %d error(s)", len(errors))
            return 1
        print(f"[PASS] check-doc-headers: All staged files passed documentation header validation ({checked} files)")
        logger.info("[IMP:9][doc-headers][main] Validation PASS — all headers OK")
        return 0
    errors = validate_make_target_names(repo_root)
    if errors:
        for err in errors:
            print(err)
        print(f"[lint.sh] FAILED — {len(errors)} error(s) found")
        logger.info("[IMP:9][namelint][main] FAILED — %d error(s)", len(errors))
        return 1
    print("[lint.sh] PASS — all make targets validated against manifest")
    logger.info("[IMP:9][namelint][main] PASS")
    return 0


# endregion FUNC_main

if __name__ == "__main__":
    sys.exit(main())
