"""Dead-code detector — reachability analysis for shell scripts (DevPlan 163 W-C).

# GREP_SUMMARY: static dead-code reachability shell call-graph entrypoint-manifest source exec glob shebang
# STRUCTURE: ▶ _parse_manifest_edges + _scan_makefile_refs + _scan_precommit_refs → ⊕ seeds
#            → ○ BFS via _find_source_calls → ⟦reachable_set⟧
#            → glob core/**/*.sh → ∖ exceptions → ◇ entrypoints|internal without caller?
#            → ⎋ DEAD_CODE Finding
"""
# region MODULE_CONTRACT
## @purpose  Детектор мёртвых shell-скриптов (AST/структурный аналог
##           tests/gates/test_gate_dead_code.py, TASK-5G3): строит call-graph из
##           entrypoint-manifest.yaml (delegates_to), Makefile и .pre-commit-config.yaml
##           (seeds) + source/./exec/bash-вызовов внутри достижимых .sh (BFS). Скрипт
##           под core/entrypoints/ или core/internal/ без живого caller'а → Finding
##           с правилом "dead-code" (blocking).
## @scope    Структурный анализ файловой системы — без Docker, сети, runtime-зависимостей.
##           Сканирует core/**/*.sh (shebang-файлы), Makefile, core/entrypoint-manifest.yaml,
##           .pre-commit-config.yaml. Пути — относительно корня сканирования (root).
## @invariants
##   - entrypoint-manifest.yaml — канонический источник рёбер Makefile→entrypoint
##   - Makefile и .pre-commit-config.yaml — дополнительные источники seeds (.sh-ссылок)
##   - source/./exec/bash внутри достижимых .sh — внутренние рёбра call-graph
##   - Документированные исключения (lib/, modules/, bootstrap/systemd/,
##     internal/healthcheck/, templates/, *healthcheck.sh, *install.sh,
##     hermes-agent build/context, generate-catalog.sh, bootstrap shell-фасады
##     state_machine) — НЕ считаются мёртвыми
##   - .sh без shebang — вне скоупа (данные, не скрипты)
##   - `changed` (--changed): глобальный анализ — прогон только если хотя бы один
##     релевантный источник (core/**/*.sh, Makefile, манифест, pre-commit) в changed
## @rationale Мёртвые скрипты накапливают путаницу при аудитах/рефакторинге. Прямое
##            замещение pytest-гейта (DevPlan 163 M1, §4.3): детектор повторяет
##            семантику гейта на корпусе дефектов, удаление гейта — фаза 2.
## @changes 2026-08-13 | DevPlan 163 W-C C1 — Created (порт test_gate_dead_code.py)
# endregion MODULE_CONTRACT

from __future__ import annotations

import logging
import os
import re
from collections import deque
from pathlib import Path

import yaml

from core.internal.static.finding import Finding

logger = logging.getLogger(__name__)

# ── Regex-паттерны (порт из test_gate_dead_code.py, TASK-5G3) ──────────────────
# source/./exec вызовы: source "path", . "path", exec "path". $ намеренно в классе
# символов — ${SCRIPT_DIR}/${PLATFORM_ROOT}/${CORE_DIR} переменные.
_SOURCE_EXEC_RE: re.Pattern[str] = re.compile(r'(?:^|\s)(?:source|\.|exec)\s+["\']?([^"\';\s&|()<>`]+)["\']?')
# bash "path.sh" прямое исполнение
_BASH_EXEC_RE: re.Pattern[str] = re.compile(r'bash\s+["\']?([^"\';\s&|()<>`]+\.sh)["\']?')
# /opt/platform/... абсолютные пути в строках/присваиваниях
_PLATFORM_PATH_RE: re.Pattern[str] = re.compile(r'["\'](/opt/platform/[^"\';\s]+\.sh)["\']')
# Любая .sh-ссылка в тексте (Makefile, pre-commit)
_SH_PATH_RE: re.Pattern[str] = re.compile(r"(?:^|\s|[→])([a-zA-Z0-9_./-]+\.sh)")
# core/entrypoints|internal пути даже с переменным префиксом
_CORE_SH_PATH_RE: re.Pattern[str] = re.compile(r"(core/(?:entrypoints|internal)/[\w./-]+\.sh)")
# Переменная-подобный dotsource: source "${VAR}/path/to/script.sh"
_VAR_SOURCE_RE: re.Pattern[str] = re.compile(r'\$\{[A-Z_]+}/[^"\';\s]+\.sh')

# Документированные исключения (не обязаны иметь caller'ов, TASK-5G3)
_EXCEPTION_PREFIXES: tuple[str, ...] = (
    "core/lib/",  # библиотеки — source по конвенции
    "core/modules/",  # модульные скрипты — динамическая итерация
    "core/bootstrap/systemd/",  # systemd unit скрипты
    "core/internal/healthcheck/",  # cron-triggered healthcheck (docker-healthcheck.sh)
    "core/templates/",  # шаблоны — не исполняются
)
_EXCEPTION_SUFFIXES: tuple[str, ...] = (
    "healthcheck.sh",  # итерируется healthcheck entrypoint через glob
    "install.sh",  # вызывается из module Makefile
)
_EXCEPTION_PATHS: tuple[str, ...] = (
    # hermes-agent build/context — вызываются из Dockerfile, не из shell
    "core/modules/hermes-agent/build/scripts/",
    "core/modules/hermes-agent/context/scripts/",
    # generate-catalog.sh — переменная-путь (${CORE_DIR}/...) из node-lifecycle.sh
    "core/internal/catalog/generate-catalog.sh",
    # W4 state-machine — скрипты вызываются из Python subprocess (не source/exec)
    "core/internal/bootstrap/deploy-modules.sh",
    "core/internal/bootstrap/install-acme.sh",
    "core/internal/bootstrap/install-docker.sh",
    "core/internal/bootstrap/install-tor-proxy.sh",
    "core/internal/bootstrap/setup-node.sh",
)


# region FUNC_is_exception
def is_exception(rel_path: str) -> bool:
    """Проверить, что относительный путь скрипта — документированное исключение.

    ## @purpose  Исключить библиотеки, модульные healthcheck/install, systemd и
    ##           шаблоны из dead-code проверки.
    ## @io       ⇥ rel_path: str (repo-relative) → ⎋ bool
    ## @complexity  O(P + S + E) — префиксы, суффиксы, точные пути
    """
    for prefix in _EXCEPTION_PREFIXES:
        if rel_path.startswith(prefix):
            return True
    for suffix in _EXCEPTION_SUFFIXES:
        if rel_path.endswith(suffix):
            return True
    return any(rel_path.startswith(path) for path in _EXCEPTION_PATHS)


# endregion FUNC_is_exception


# region FUNC_resolve_source_path
def _resolve_source_path(raw_path: str, script_abs_dir: str, script_rel: str, root: Path) -> str | None:
    """Разрешить путь из source/./exec-вызова в абсолютный путь внутри проекта.

    ## @purpose  Обработать ${SCRIPT_DIR}/${CORE_DIR}/${PLATFORM_ROOT} переменные и
    ##           обычные относительные пути.
    ## @io       ⇥ raw_path: str, script_abs_dir: str, script_rel: str, root: Path
    ##           ⎋ str | None — абсолютный путь, или None если неразрешим
    ## @complexity  O(1)
    ## @invariants
    ##   - ${SCRIPT_DIR} → script_abs_dir; ${CORE_DIR} → root/core
    ##   - ${PLATFORM_ROOT} контекстно: entrypoints → parent(entrypoints/)=core/
    ##   - Неразрешимые переменные → None (пропуск)
    ##   - Абсолютные пути вне root → None
    """
    path = raw_path.strip().strip('"').strip("'")
    if not path:
        return None

    if "${" in path:
        pl_platform_root = (
            Path(script_abs_dir).parent.as_posix() if script_rel.startswith("core/entrypoints/") else root.as_posix()
        )
        known_vars: dict[str, str] = {
            "${SCRIPT_DIR}": script_abs_dir,
            "${PLATFORM_ROOT}": pl_platform_root,
            "${CORE_DIR}": (root / "core").as_posix(),
        }
        resolved_any = False
        for var, replacement in known_vars.items():
            if var in path:
                path = path.replace(var, replacement)
                resolved_any = True
        if not resolved_any:
            return None

    candidate = Path(path)
    if not candidate.is_absolute():
        candidate = Path(script_abs_dir) / candidate
    path = os.path.normpath(candidate.as_posix())

    if not path.startswith(str(root)):
        # Production-префикс PLATFORM_ROOT — через env-fallback канон (gate no_hardcoded_local_paths
        # allowlist: os.environ.get("PLATFORM_ROOT", DEFAULT_PLATFORM_ROOT); DEFAULT_PLATFORM_ROOT —
        # shared/deploy_paths, literal см. там). Семантика: резолв production-путей
        # в repo-относительные для call-graph (как в гейте-оригинале test_gate_dead_code.py).
        prod_prefix = os.environ.get("PLATFORM_ROOT", "/opt/platform/")
        if path.startswith(prod_prefix):
            path = os.path.normpath((root / path[len(prod_prefix) :]).as_posix())
        else:
            return None

    return path if Path(path).is_file() else None


# endregion FUNC_resolve_source_path


# region FUNC_find_source_calls
def _find_source_calls(script_abs: str, script_rel: str, root: Path) -> list[str]:
    """Извлечь все source/./exec/bash/var-source ссылки на скрипты из .sh файла.

    ## @purpose  Прочитать .sh и собрать разрешимые пути вызываемых скриптов
    ##           (4 паттерна: source/exec, bash, /opt/platform, ${VAR}/path).
    ## @io       ⇥ script_abs: str, script_rel: str, root: Path → ⎋ list[str]
    ## @complexity  O(L) — строки файла
    ## @invariants  Возвращает только пути, существующие внутри root; нечитаемые
    ##              файлы и неразрешимые переменные тихо пропускаются
    """
    if not Path(script_abs).is_file():
        return []
    script_dir = Path(script_abs).parent
    results: set[str] = set()
    try:
        with Path(script_abs).open(encoding="utf-8", errors="replace") as fh:
            content = fh.read()
    except OSError:
        logger.warning("[IMP:7][_find_source_calls] Cannot read: %s", script_abs)
        return []

    for regex in (_SOURCE_EXEC_RE, _BASH_EXEC_RE, _PLATFORM_PATH_RE):
        for match in regex.finditer(content):
            resolved = _resolve_source_path(match.group(1), script_dir.as_posix(), script_rel, root)
            if resolved:
                results.add(resolved)
    for match in _VAR_SOURCE_RE.finditer(content):
        resolved = _resolve_source_path(match.group(0), script_dir.as_posix(), script_rel, root)
        if resolved:
            results.add(resolved)

    return sorted(results)


# endregion FUNC_find_source_calls


# region FUNC_extract_sh_paths_from_text
def _extract_sh_paths_from_text(text: str) -> set[str]:
    """Извлечь .sh пути из произвольного текста (Makefile, pre-commit, delegates_to).

    ## @purpose  Парсить ссылки вида core/entrypoints/bootstrap.sh, отбрасывая
    ##           голые имена файлов и не-project пути.
    ## @io       ⇥ text: str → ⎋ set[str] относительных путей
    ## @complexity  O(N) — строки текста
    """
    paths: set[str] = set()
    for match in _SH_PATH_RE.finditer(text):
        candidate = match.group(1).strip().strip("'\".")
        if not candidate or "/" not in candidate or not candidate.endswith(".sh"):
            continue
        if candidate.startswith(("core/", "/")):
            paths.add(candidate)
    return paths


# endregion FUNC_extract_sh_paths_from_text


# region FUNC_extract_sh_path_from_segment
def _extract_sh_path_from_segment(segment: str) -> str | None:
    """Извлечь .sh путь из сегмента delegates_to, отбрасывая аргументы.

    ## @purpose  Обрабатывает "build.sh build-platform" → "build.sh".
    ## @io       ⇥ segment: str → ⎋ str | None
    ## @complexity  O(T) — токены
    """
    for token in segment.split():
        cleaned = token.strip("'\"")
        if cleaned.endswith(".sh"):
            return cleaned
    return None


# endregion FUNC_extract_sh_path_from_segment


# region FUNC_parse_manifest_edges
def _parse_manifest_edges(root: Path) -> dict[str, set[str]]:
    """Построить рёбра call-graph из delegates_to entrypoint-manifest.yaml.

    ## @purpose  Для каждого entry: разбить delegates_to по →, извлечь .sh пути
    ##           из сегментов, построить цепочку caller→callee.
    ## @io       ⇥ root: Path → ⎋ dict[str, set[str]] {caller: {callee, ...}}
    ## @complexity  O(E * P) — записи манифеста × путей
    """
    edges: dict[str, set[str]] = {}
    manifest = root / "core" / "entrypoint-manifest.yaml"
    if not manifest.is_file():
        logger.warning("[IMP:7][_parse_manifest_edges] Manifest not found: %s", manifest)
        return edges
    try:
        with Path(manifest).open(encoding="utf-8") as fh:
            data = yaml.safe_load(fh)  # pyright: ignore[reportAny] W11-G4: pyyaml без stubs → Any; isinstance-гейт ниже
    except (OSError, yaml.YAMLError):
        logger.warning("[IMP:7][_parse_manifest_edges] Cannot parse manifest: %s", manifest)
        return edges

    skip_groups = ("allowed_verbs",)  # forbidden-тройка упразднена DevPlan 171 W3.3
    for group_name, entries in data.items() if isinstance(data, dict) else ():
        if group_name in skip_groups or not isinstance(entries, list):
            continue
        for entry in entries:
            if not isinstance(entry, dict):
                continue
            delegates_to = entry.get("delegates_to", "")
            if not isinstance(delegates_to, str) or not delegates_to.strip():
                continue
            sh_paths = [
                extracted
                for seg in (s.strip() for s in delegates_to.split("→"))
                if (extracted := _extract_sh_path_from_segment(seg)) is not None
            ]
            if not sh_paths:
                continue
            for i in range(len(sh_paths) - 1):
                edges.setdefault(sh_paths[i], set()).add(sh_paths[i + 1])
            if len(sh_paths) == 1:
                edges.setdefault(sh_paths[0], set())

    logger.info("[IMP:8][_parse_manifest_edges] Extracted %d caller(s) from manifest", len(edges))
    return edges


# endregion FUNC_parse_manifest_edges


# region FUNC_scan_file_refs
def _scan_file_refs(root: Path, rel_file: str, *, add_core_pattern: bool) -> set[str]:
    """Собрать .sh ссылки из текстового файла (Makefile / .pre-commit-config.yaml).

    ## @purpose  Поймать скрипты, вызываемые вне manifest-рёбер. Строго core/ пути.
    ## @io       ⇥ root: Path, rel_file: str, add_core_pattern: bool (keyword-only)
    ##           ⎋ set[str]
    ## @complexity  O(L) — строки файла
    """
    path = root / rel_file
    if not path.is_file():
        return set()
    try:
        content = path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return set()
    raw_paths = _extract_sh_paths_from_text(content)
    if add_core_pattern:
        raw_paths.update(m.group(1) for m in _CORE_SH_PATH_RE.finditer(content))
    return {p for p in raw_paths if p.startswith("core/") and p.endswith(".sh")}


# endregion FUNC_scan_file_refs


# region FUNC_find_all_shell_scripts
def _find_all_shell_scripts(root: Path) -> set[str]:
    """Найти все shebang .sh скрипты под core/.

    ## @purpose  Дерево core/**/*.sh с первой строкой #! — кандидаты на dead-code.
    ## @io       ⇥ root: Path → ⎋ set[str] repo-relative путей
    ## @complexity  O(N) — файлы в дереве
    """
    scripts: set[str] = set()
    core_dir = root / "core"
    if not core_dir.is_dir():
        return scripts
    for entry in core_dir.rglob("*.sh"):
        if not entry.is_file() or "__pycache__" in entry.parts:
            continue
        try:
            with entry.open(encoding="utf-8", errors="replace") as fh:
                first_line = fh.readline(128)
        except OSError:
            continue
        if first_line.startswith("#!"):
            scripts.add(entry.relative_to(root).as_posix())
    logger.info("[IMP:8][_find_all_shell_scripts] Found %d shebang .sh file(s) under core/", len(scripts))
    return scripts


# endregion FUNC_find_all_shell_scripts


# region FUNC_build_call_graph
def _build_call_graph(root: Path) -> tuple[set[str], set[str]]:
    """Построить call-graph и вычислить множество достижимых скриптов.

    ## @purpose  Seeds (манифест + Makefile + pre-commit) → BFS через
    ##           source/./exec/bash-вызовы → reachable-множество.
    ## @io       ⇥ root: Path → ⎋ (seeds: set[str], reachable: set[str])
    ## @complexity  O(V + E + V * L) — скрипты, рёбра, средние строки
    """
    logger.info("[IMP:8][_build_call_graph] Building call graph from manifest, Makefile, pre-commit...")
    manifest_edges = _parse_manifest_edges(root)

    seeds: set[str] = set()
    for caller_rel, callees in manifest_edges.items():
        seeds.add(caller_rel)
        seeds.update(callees)
    seeds.update(_scan_file_refs(root, "Makefile", add_core_pattern=True))
    seeds.update(_scan_file_refs(root, ".pre-commit-config.yaml", add_core_pattern=False))

    graph: dict[str, set[str]] = {s: set() for s in seeds}
    for caller, callees in manifest_edges.items():
        graph.setdefault(caller, set()).update(callees)
        for c in callees:
            graph.setdefault(c, set())

    visited: set[str] = set()
    queue: deque[str] = deque(seeds)
    while queue:
        script_rel = queue.popleft()
        if script_rel in visited:
            continue
        visited.add(script_rel)
        script_abs = root / script_rel
        if not script_abs.is_file():
            continue
        for callee_abs in _find_source_calls(script_abs.as_posix(), script_rel, root):
            callee_rel = Path(callee_abs).relative_to(root).as_posix()
            graph.setdefault(script_rel, set()).add(callee_rel)
            graph.setdefault(callee_rel, set())
            if callee_rel not in visited:
                queue.append(callee_rel)

    reachable: set[str] = set()
    bfs_queue: deque[str] = deque(seeds)
    bfs_visited: set[str] = set()
    while bfs_queue:
        node = bfs_queue.popleft()
        if node in bfs_visited:
            continue
        bfs_visited.add(node)
        if (root / node).is_file():
            reachable.add(node)
        bfs_queue.extend(callee for callee in graph.get(node, set()) if callee not in bfs_visited)

    logger.info(
        "[IMP:9][_build_call_graph] Seeds=%d, Graph nodes=%d, Reachable=%d", len(seeds), len(graph), len(reachable)
    )
    return seeds, reachable


# endregion FUNC_build_call_graph


# region FUNC_detect
def detect(root: Path, changed: set[str] | None = None) -> list[Finding]:
    """Найти мёртвые shebang-скрипты под core/entrypoints/ и core/internal/.

    # ▶ _build_call_graph ─◇ script in reachable|seeds|exception? ─→ skip
    #                         └→ ⊕ DEAD_CODE Finding → ⎋ sorted list

    ## @purpose  Главный вход детектора (registry): все shebang .sh в core/entrypoints/
    ##           и core/internal/ обязаны иметь живой caller (манифест / Makefile /
    ##           pre-commit / source-цепочка).
    ## @io       ⇥ root: Path (корень сканирования), changed: set[str] | None
    ##           ⎋ list[Finding] — rule="dead-code"
    ## @complexity  Делегирует _build_call_graph + _find_all_shell_scripts
    ## @invariants  Исключения (_EXCEPTION_*) никогда не репортуются; несуществующие
    ##              файлы не репортуются (граф фильтрует); --changed: прогон только
    ##              если затронут релевантный источник (скрипт/манифест/Makefile)
    """
    if changed is not None:
        relevant = changed & _RELEVANT_GLOBS(root)
        if not relevant:
            logger.info("[IMP:8][dead_code][changed] No changed relevant source — skipping global analysis")
            return []

    seeds, reachable = _build_call_graph(root)
    all_scripts = _find_all_shell_scripts(root)
    findings: list[Finding] = []

    for script_rel in sorted(all_scripts):
        if is_exception(script_rel):
            continue
        if script_rel in reachable or script_rel in seeds:
            continue
        if not script_rel.startswith(("core/entrypoints/", "core/internal/")):
            continue
        findings.append(
            Finding(
                rule="dead-code",
                file=script_rel,
                line=1,
                message="DEAD_CODE: no live caller found (entrypoint-manifest / Makefile / pre-commit / source-chain)",
            )
        )
        logger.warning("[IMP:9][dead_code][RED] DEAD_CODE: %s", script_rel)

    logger.info("[IMP:9][dead_code] All shebang scripts=%d, Dead findings=%d", len(all_scripts), len(findings))
    if not findings:
        logger.info("[IMP:9][dead_code] PASS: all entrypoints/internal scripts are reachable")
    return findings


# endregion FUNC_detect


# region FUNC_RELEVANT_GLOBS
def _RELEVANT_GLOBS(root: Path) -> set[str]:
    """Собрать релевантные пути для --changed-фильтра глобального анализа.

    ## @purpose  Множество путей, изменение которых может повлиять на достижимость:
    ##           все core/**/*.sh + источники seeds. Только существующие файлы.
    ## @io       ⇥ root: Path → ⎋ set[str]
    ## @complexity  O(N) — файлы в core/**
    """
    relevant: set[str] = {
        p for p in ("Makefile", "core/entrypoint-manifest.yaml", ".pre-commit-config.yaml") if (root / p).is_file()
    }
    core_dir = root / "core"
    if core_dir.is_dir():
        relevant.update(p.relative_to(root).as_posix() for p in core_dir.rglob("*.sh") if p.is_file())
    return relevant


# endregion FUNC_RELEVANT_GLOBS
