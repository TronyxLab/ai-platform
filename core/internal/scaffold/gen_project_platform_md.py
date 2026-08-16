#!/usr/bin/env python3
# GREP_SUMMARY: gen_project_platform_md, AI-PLATFORM.md, generator, provides, enabled-modules, node-yaml, GENERATED-markers, per-node, DSN
# STRUCTURE: ▶ generate(project_dir→load ai-platform.yaml + node.yaml + platform-env.yaml) → ⊕ render_static → ⊕ render_generated (enabled-modules/services/networks/needs) → ◇ markers (replace-section|create|skip) → ⚡ atomic_write_text → ⎋ CLI main
# region MODULE_CONTRACT
## @purpose  Generate the project file AI-PLATFORM.md — hybrid: static canonical reference
##           + per-node GENERATED section (enabled modules, provides services with DSN/URL,
##           networks, needs-status) + Practices GENERATED section (уровень/зрелость/
##           [PRACTICES:PROPOSE], DevPlan 137 W1 §2.1C). Pattern: gen_env_platform.py.
## @scope    Library generate()/write_project_platform_md() + CLI main(). Consumers:
##           scaffold_helpers.gen_project_platform_md (new-project/adopt-project),
##           Makefile project-sync-env (CLI), converge R3 (if-missing, direct import).
## @invariants
##   - Static part is a stable template; GENERATED section is delimited by
##     <!-- GENERATED:START:platform_md --> / <!-- GENERATED:END:platform_md -->
##   - Re-generation replaces ONLY the section between markers (manual static edits preserved)
##   - Existing file WITHOUT markers → NOT overwritten unless force=True (idempotent)
##   - Missing node.yaml / platform-env.yaml → section with explicit warning (graceful, no crash)
##   - ${NAME}/${DOMAIN} substitution in DSN/URL templates (same semantics as gen_env_platform)
##   - Atomic write via shared/atomic_writer (single canonical writer, DevPlan 119 E5)
##   - Library functions never call sys.exit() — raise; sys.exit only in main()
## @rationale DevPlan 133 D2/D3: «полный снапшот устаревает, только статика не per-node» →
##            гибрид с GENERATED-маркерами (канонический паттерн generated-секций, инвариант 11).
##            Канон документа — секция «Контракт окружения проекта» root AGENTS.md (D1); файл проекта — указатель.
## @changes  2026-08-03 · DevPlan 133 W1 — создан
## @changes  2026-08-16 · Аудит релиза 1.0.0 — _default_node_yaml_path: walk-up fallback без
##                      PROJECTS_BASE (<project_dir>/../node-configs/<node>/node.yaml, dev-layout);
##                      PROJECTS_BASE остаётся первичным (VPS-путь)
## @links    CALLS: shared/atomic_writer, shared/node_yaml, shared/project_yaml, gen_env_platform (substitution semantics)
##           CALLED_BY: scaffold_helpers.py, converge/projects.py (R3), Makefile project-sync-env (CLI)
# endregion MODULE_CONTRACT

from __future__ import annotations

import argparse
import logging
import os
import sys
from pathlib import Path

import yaml

logger = logging.getLogger(__name__)

# ── sys.path bootstrap: standalone CLI invocation (python3 script.py) ──
# Project root добавляется вручную (core/internal/scaffold/ → parents[3] = repo root),
# чтобы core.internal.shared.* резолвился без PYTHONPATH (паттерн on_project_deploy.py).
_PROJECT_ROOT = str(Path(__file__).resolve().parents[3])
if _PROJECT_ROOT not in sys.path:
    sys.path.insert(0, _PROJECT_ROOT)

from typing import ClassVar, cast

from core.internal.shared.atomic_writer import atomic_write_text
from core.internal.shared.exceptions import PlatformError
from core.internal.shared.node_yaml import NodeYaml
from core.internal.shared.project_yaml import get_name, get_needs

__all__ = [
    "GENERATED_END",
    "GENERATED_START",
    "PRACTICES_END",
    "PRACTICES_START",
    "generate",
    "main",
    "render_generated",
    "render_practices_section",
    "render_static",
    "write_project_platform_md",
]

# ── GENERATED-section markers (canonical generated-секции паттерн, инвариант 11) ──
GENERATED_START = "<!-- GENERATED:START:platform_md -->"
GENERATED_END = "<!-- GENERATED:END:platform_md -->"

# ── Practices-секция (DevPlan 137 W1 §2.1C): уровень/зрелость/[PRACTICES:PROPOSE] ──
PRACTICES_START = "<!-- GENERATED:START:practices_md -->"
PRACTICES_END = "<!-- GENERATED:END:practices_md -->"

# ── Repo root (для default-резолва platform-env.yaml в CLI) ──
_REPO_ROOT = Path(__file__).resolve().parents[3]


# ═══════════════════════════════════════════════════════════════════
# region FUNC_render_static
## @purpose  Render the STATIC part of AI-PLATFORM.md (stable template — not per-node).
##           Ссылки на канон: URL GitHub (org/ai-platform) + локальный путь.
## @param project_name  Project name
## @param org           GitHub org / context (для URL канона)
## @return  Static markdown (без GENERATED-секции — добавляется в generate())
## @complexity O(1) — f-string template
def render_static(project_name: str, org: str = "") -> str:
    """Render the static part of AI-PLATFORM.md (stable template)."""
    org_part = f"{org}/" if org else ""
    local_path = "../../ai-platform/AGENTS.md"
    url = f"https://github.com/{org_part}ai-platform/blob/main/AGENTS.md#контракт-окружения-проекта"
    static = f"""# AI-PLATFORM.md — {project_name}

Этот файл — контракт между проектом и платформой ai-platform. Полное описание окружения,
границ и команд — в каноническом документе платформы:

- **URL:** {url}
- **Локальный путь:** `{local_path}`

## DO NOT

1. НЕ поднимай собственные postgres/redis/прокси/TLS — это сервисы платформы.
2. НЕ публикуй порты в docker-compose — ingress и TLS делает nginx-модуль платформы.
3. НЕ редактируй `.env.platform` вручную — GENERATED, устарел → `make sync-env`.
4. НЕ храни секреты/токены в файлах проекта; пароль роли БД — только в `.platform-db.env` на ноде.
5. НЕ меняй GENERATED-секцию ниже вручную — перезапишется при `make sync-env`.

## Команды

- `make sync-env` — (пере)генерировать `.env.platform` и GENERATED-секцию этого файла
- `make status` — live-статус проекта на целевой ноде
- `make help` — все доступные команды

Деплой = `git push` (main → production). Не изобретай скрипты — только make-таргеты
(реестр: `ai-platform/core/entrypoint-manifest.yaml`).

## Окружение ноды (GENERATED)

{GENERATED_START}
{GENERATED_END}

## Practices  {PRACTICES_START}
{PRACTICES_END}

## Приоритет инструкций

При конфликте: `AGENTS.md` проекта → этот файл → секция «Контракт окружения проекта» root AGENTS.md (канон)
→ `ai-platform/AGENTS.md` (root) → `ai-platform/core/AGENTS.md`.
"""
    logger.info("[IMP:7][gen_platform_md][static] Static part rendered for %s (org=%s)", project_name, org)
    return static


# endregion FUNC_render_static


# ═══════════════════════════════════════════════════════════════════
# region FUNC_render_generated
## @purpose  Render the GENERATED (per-node) section content: node/context/domain,
##           enabled modules (node.yaml modules with enabled == "true"),
##           provides services (platform-env.yaml, ${NAME}/${DOMAIN} substitution),
##           networks, needs-status of the project (ai-platform.yaml).
##           Graceful degradation: missing files → warning lines, no crash.
## @param project_name     Project name (${NAME} substitution)
## @param project_dir       Project directory (for ai-platform.yaml needs-status)
## @param node_yaml_path   Path to node.yaml (optional — graceful if missing)
## @param platform_env_path  Path to platform-env.yaml (optional — graceful if missing)
## @param domain           Node domain fallback (${DOMAIN} substitution; from node.yaml if absent)
## @return  Section content WITHOUT markers (caller wraps with GENERATED_START/END)
## @complexity O(M + S) — M = modules, S = provides services
def render_generated(
    project_name: str,
    node_yaml_path: str = "",
    platform_env_path: str = "",
    domain: str = "",
    project_dir: Path | None = None,
) -> str:
    """Render the per-node GENERATED section content (enabled modules, services, networks, needs)."""
    lines: list[str] = []

    # ── node.yaml: node/context/domain + enabled modules ──
    node_name = ""
    context = ""
    modules: list[str] = []
    if node_yaml_path and Path(node_yaml_path).is_file():
        # ruff: ignore[PLW0717] — try вложен в условный блок внутри функции — после-try чтение локалей неанализируемо
        try:
            node = NodeYaml(node_yaml_path)
            node_name = str(node.get("node.name", default="") or "")
            ctx = node.get_context()
            context = str(ctx) if ctx else ""
            if not domain:
                domain = str(node.get("domain", default="") or "")
            modules = [
                str(m.get("name", ""))
                for m in node.get_modules()
                # W11-G5: runtime guard против malformed module entries (get_modules typed, YAML произволен)
                if isinstance(m, dict)  # pyright: ignore[reportUnnecessaryIsInstance] — W11-G5: runtime guard против malformed module entries
                and str(m.get("enabled", "")).lower() == "true"
                and m.get("name")
            ]
            logger.info(
                "[IMP:9][gen_platform_md][generated] node.yaml loaded: node=%s context=%s enabled_modules=%d",
                node_name or "<none>",
                context or "<none>",
                len(modules),
            )
        except (OSError, PlatformError) as exc:  # noqa: EXC — graceful: любой parse-сбой → warning-секция (best-effort)
            logger.warning("[IMP:7][gen_platform_md][generated] node.yaml parse skipped: %s", exc)
            modules = []
    else:
        logger.warning(
            "[IMP:7][gen_platform_md][generated] node.yaml not found: %s — module list unavailable", node_yaml_path
        )

    lines.append(f"**Node:** {node_name or '<unknown>'}  ")
    lines.append(f"**Context (org):** {context or '<unknown>'}  ")
    lines.append(f"**Domain:** {domain or '<not set>'}  ")
    lines.append("")
    lines.append("**Enabled modules:** " + (", ".join(sorted(modules)) if modules else "_node.yaml not found_") + "  ")

    # ── platform-env.yaml: provides services + networks ──
    services: list[tuple[str, str, str, str]] = []  # (name, host, port, dsn_or_url)
    networks: set[str] = set()
    if platform_env_path and Path(platform_env_path).is_file():
        # ruff: ignore[PLW0717] — try вложен в условный блок внутри функции — после-try чтение локалей неанализируемо
        try:
            with Path(platform_env_path).open(encoding="utf-8") as f:
                # W11: yaml.safe_load returns Any → cast to payload boundary
                loaded = cast(dict[str, object] | None, yaml.safe_load(f))
            data: dict[str, object] = loaded if loaded is not None else {}
            # W11: provides section is dict[str, object] → cast nested mapping boundary
            provides = cast(dict[str, object], data.get("provides", {}))
            for svc_name in sorted(provides.keys()):
                # W11: dict[str, object].get → object → raw guard + cast
                svc_raw: object = provides[svc_name] or {}
                if not isinstance(svc_raw, dict):
                    continue
                svc = cast(dict[str, object], svc_raw)
                host = str(svc.get("host", "") or "")
                port = str(svc.get("port", "") or "")
                dsn_tmpl = str(svc.get("dsn_template", "") or "")
                url_tmpl = str(svc.get("url_template", "") or "")
                val = dsn_tmpl or url_tmpl
                if project_name:
                    val = val.replace("${NAME}", project_name)
                if domain:
                    val = val.replace("${DOMAIN}", domain)
                services.append((svc_name, host, port, val))
                for net in cast(list[object], svc.get("networks", []) or []):
                    if net:
                        networks.add(str(net))
            logger.info(
                "[IMP:9][gen_platform_md][generated] platform-env.yaml loaded: %d provides, %d networks",
                len(services),
                len(networks),
            )
        except (OSError, yaml.YAMLError) as exc:
            logger.warning("[IMP:7][gen_platform_md][generated] platform-env.yaml parse skipped: %s", exc)
            services = []
            networks = set()
    else:
        logger.warning(
            "[IMP:7][gen_platform_md][generated] platform-env.yaml not found: %s — services unavailable",
            platform_env_path,
        )

    lines.append("")
    lines.append("**Platform services:**")
    if services:
        lines.append("")
        lines.append("| Service | Host | Port | DSN / URL |")
        lines.append("|---------|------|------|-----------|")
        for name, host, port, val in services:
            port_cell = port if port else "-"
            val_cell = f"`{val}`" if val else "-"
            lines.append(f"| {name} | {host} | {port_cell} | {val_cell} |")
    else:
        lines.append("")
        lines.append("_platform-env.yaml not found — сервисы недоступны (⚠️ проверь доставку core)_")
    lines.append("")
    lines.append("**Networks:** " + (", ".join(sorted(networks)) if networks else "—") + "  ")

    # ── ai-platform.yaml: needs-статус проекта ──
    needs = _read_project_needs(project_dir if project_dir is not None else Path())
    if needs:
        rendered = ", ".join(f"{k}: {v}" for k, v in sorted(needs.items()))
        lines.append("")
        lines.append(f"**needs:** {rendered}  ")

    lines.append("")
    logger.info("[IMP:9][gen_platform_md][generated] Section rendered (%d lines)", len(lines))
    return "\n".join(lines)


# endregion FUNC_render_generated


# ═══════════════════════════════════════════════════════════════════
# region FUNC_read_project_needs
## @purpose  Read needs-статус проекта из ai-platform.yaml (canonical project_yaml reader).
## @param project_dir  Project directory (Path)
## @return  dict[str, Any] — needs-секция ({} если отсутствует/не dict)
## @complexity O(1)
def _read_project_needs(project_dir: Path) -> dict[str, object]:
    """Read needs section from project's ai-platform.yaml ({} if absent)."""
    from core.internal.shared.project_yaml import load_project_yaml

    return get_needs(load_project_yaml(project_dir))


# endregion FUNC_read_project_needs


# ═══════════════════════════════════════════════════════════════════
# region FUNC_render_practices_section
## @purpose  Render Practices-секцию AI-PLATFORM.md (DevPlan 137 §2.1C): уровень практик,
##           состояние эскалатора (baseline|proposed|active-full) + maturity-reason,
##           generator-инфо (версия канона + hash lock) и [PRACTICES:PROPOSE] варнинг-блок
##           для proposed (агент видит предложение до начала работы).
## @param project_dir  Project directory (ai-platform.yaml quality.level + practices.lock)
## @return  Section content WITHOUT markers (caller wraps with PRACTICES_START/END)
## @complexity O(F) где F = файлы проекта (maturity walk)
## @invariants
##   - Level display: proposed → "full (auto-proposed)"; active-full → "full (active)";
##     baseline → level_setting как есть (auto/baseline/full)
##   - State display: proposed добавляет "(age=41d, files=87)" reason
##   - Варнинг-блок — только при decision.warning (proposed); автопромоута НЕТ
##   - Graceful: нет ai-platform.yaml/lock → "not configured / not synced" (не crash)
def render_practices_section(project_dir: str) -> str:
    """Render Practices section content (level, state, generator, PROPOSE warning)."""
    from core.internal.practices.escalator import evaluate
    from core.internal.practices.generators import read_lock
    from core.internal.practices.manifest import load_manifest
    from core.internal.practices.maturity import compute_maturity

    proj = Path(project_dir)
    data = _load_raw_project_yaml(proj)
    quality: object = data.get("quality") or {}
    # W11: quality section is dict[str, object] → cast nested boundary
    level_setting = str(cast(dict[str, object], quality).get("level", "auto") or "auto")

    maturity = compute_maturity(proj)
    lock = read_lock(proj)
    try:
        decision = evaluate(maturity, level_setting, lock)
    except ValueError:
        decision = None

    manifest = load_manifest()
    version = manifest.version

    if decision is None:
        level_display = f"{level_setting} (invalid)"
        state_display = "unknown"
    elif decision.state_name == "proposed":
        level_display = "full (auto-proposed)"
        state_display = f"proposed ({decision.reason})"
    elif decision.state_name == "active-full":
        level_display = "full (active)"
        state_display = "active-full"
    else:
        level_display = level_setting
        state_display = "baseline"

    hash_part = lock.generator_hash if lock is not None else "not synced"
    lines: list[str] = [
        f"- **Level:** {level_display}  ",
        f"- **State:** {state_display}  ",
        f"- **Generator:** practices v{version}, hash {hash_part}  ",
    ]
    if decision is not None and decision.warning:
        lines.extend([
            "",
            f"> {decision.warning.splitlines()[0]}",
            "> >>> RECOMMEND: `make project-set-practices full` (или `make project-sync-practices` для обновления канона)",
            "> Деплой НЕ блокируется (proposed = non-blocking). active-full включается ТОЛЬКО по согласию",
            "> (`make project-set-practices full`) — автопромоута нет (решение пользователя 2026-08-05).",
        ])
    lines.append("")
    logger.info(
        "[IMP:9][gen_platform_md][practices] Section rendered (state=%s level=%s)", state_display, level_display
    )
    return "\n".join(lines)


# endregion FUNC_render_practices_section


# ═══════════════════════════════════════════════════════════════════
# region FUNC_load_raw_project_yaml
## @purpose  Прочитать ai-platform.yaml проекта (raw dict) для quality.level.
## @param project_dir  Project directory
## @return  dict ({} если отсутствует/не dict)
## @complexity O(1)
def _load_raw_project_yaml(project_dir: Path) -> dict[str, object]:
    """Load project ai-platform.yaml dict ({} if missing/unparseable)."""
    from core.internal.shared.project_yaml import load_project_yaml

    return load_project_yaml(project_dir)


# endregion FUNC_load_raw_project_yaml


# ═══════════════════════════════════════════════════════════════════
# region FUNC_generate
## @purpose  Generate the FULL AI-PLATFORM.md content (static + GENERATED section).
## @param project_dir        Project directory (for ai-platform.yaml needs + display)
## @param node_name          Node name (used only for logs; node.yaml is authoritative)
## @param node_yaml_path     Path to node.yaml (optional)
## @param platform_env_path  Path to platform-env.yaml (optional)
## @param project_yaml_path  Path to ai-platform.yaml (optional; default project_dir/ai-platform.yaml)
## @param domain             Node domain fallback (${DOMAIN} substitution)
## @return  Full markdown content (str)
## @complexity O(M + S)
## @invariants
##   - GENERATED section wrapped in canonical markers (single occurrence)
##   - Missing inputs → warning lines inside the section (never crash)
def generate(
    project_dir: str,
    node_name: str = "",
    node_yaml_path: str = "",
    platform_env_path: str = "",
    project_yaml_path: str = "",
    domain: str = "",
) -> str:
    """Generate full AI-PLATFORM.md content (static part + GENERATED section)."""
    proj_path = Path(project_dir)
    name = _project_name(proj_path, project_yaml_path)
    if not node_yaml_path:
        node_yaml_path = _default_node_yaml_path(proj_path, node_name)
    if not platform_env_path:
        platform_env_path = str(_REPO_ROOT / "platform-env.yaml")

    logger.info(
        "[IMP:8][gen_platform_md][generate] Generating AI-PLATFORM.md for %s (node_yaml=%s, platform_env=%s)",
        name,
        node_yaml_path or "<none>",
        platform_env_path or "<none>",
    )

    org = _resolve_org(node_yaml_path, proj_path)
    static = render_static(name, org)
    section = render_generated(name, node_yaml_path, platform_env_path, domain=domain, project_dir=proj_path)
    practices_section = render_practices_section(str(proj_path))

    # Wrap sections with markers (canonical single pairs)
    full = static.replace(
        f"{GENERATED_START}\n{GENERATED_END}",
        f"{GENERATED_START}\n{section}\n{GENERATED_END}",
        1,
    )
    full = full.replace(
        f"{PRACTICES_START}\n{PRACTICES_END}",
        f"{PRACTICES_START}\n{practices_section}\n{PRACTICES_END}",
        1,
    )
    logger.info("[IMP:9][gen_platform_md][generate] AI-PLATFORM.md rendered (%d chars)", len(full))
    return full


# endregion FUNC_generate


# ═══════════════════════════════════════════════════════════════════
# region FUNC_write_project_platform_md
## @purpose  Idempotent atomic write of AI-PLATFORM.md into project_dir: missing file → create
##           full content; existing WITH markers → replace ONLY the section; existing WITHOUT
##           markers and not force → skip.
## @param project_dir        Project directory (target: project_dir/AI-PLATFORM.md)
## @param node_name          Node name (logs; node.yaml is authoritative)
## @param node_yaml_path     Path to node.yaml (optional)
## @param platform_env_path  Path to platform-env.yaml (optional)
## @param project_yaml_path  Path to ai-platform.yaml (optional)
## @param force              Overwrite existing file without markers
## @param domain             Node domain fallback
## @return  "created" | "updated" | "exists" | "skipped"
## @complexity O(M + S + N) — render + atomic write
## @invariants
##   - Atomic write via shared/atomic_writer (single canonical writer)
##   - Never produces duplicate GENERATED sections (replace-section semantics)
def write_project_platform_md(
    project_dir: str,
    node_name: str = "",
    node_yaml_path: str = "",
    platform_env_path: str = "",
    project_yaml_path: str = "",
    force: bool = False,
    domain: str = "",
) -> str:
    """Write AI-PLATFORM.md atomically with replace-section idempotency."""
    target = Path(project_dir) / "AI-PLATFORM.md"
    full = generate(
        project_dir,
        node_name=node_name,
        node_yaml_path=node_yaml_path,
        platform_env_path=platform_env_path,
        project_yaml_path=project_yaml_path,
        domain=domain,
    )

    if target.is_file():
        existing = target.read_text(encoding="utf-8")
        if GENERATED_START in existing and GENERATED_END in existing:
            # Replace section only (preserve hand-edited static part) — identical marker pairs
            new_content = _replace_section(existing, full, GENERATED_START, GENERATED_END)
            if PRACTICES_START in existing and PRACTICES_END in existing:
                new_content = _replace_section(new_content, full, PRACTICES_START, PRACTICES_END)
            else:
                # Practices-маркеров ещё нет (файл создан до 137) → добавить секцию из статики
                new_content = _append_practices_section(new_content, full)
            atomic_write_text(target, new_content, mode=0o644)
            logger.info("[IMP:9][gen_platform_md][write] Section updated: %s (static part preserved)", target)
            return "updated"
        if not force:
            logger.info("[IMP:6][gen_platform_md][write] SKIP: %s exists without markers (use --force)", target)
            return "exists"
        logger.info("[IMP:7][gen_platform_md][write] Force mode: overwriting %s", target)

    atomic_write_text(target, full, mode=0o644)
    logger.info("[IMP:9][gen_platform_md][write] Written: %s", target)
    return "created"


# endregion FUNC_write_project_platform_md


# ═══════════════════════════════════════════════════════════════════
# region FUNC_replace_section
## @purpose  Replace content between start/end markers in `existing` with the block from `full`
##           (preserving static part before/after). Идентичная семантика прежней
##           replace-section для platform_md — расширена на обе пары маркеров (137).
## @io       ⇥ existing, full, start, end → ⎋ str
## @complexity O(N)
def _replace_section(existing: str, full: str, start: str, end: str) -> str:
    """Swap marker-delimited block in existing with block from full (static preserved)."""
    head = existing.split(start, 1)[0]
    tail = existing.split(end, 1)[1]
    section_part = full.split(start, 1)[1].split(end, 1)[0]
    return head + start + section_part + end + tail


# endregion FUNC_replace_section


# ═══════════════════════════════════════════════════════════════════
# region FUNC_append_practices_section
## @purpose  Добавить Practices-секцию (маркеры + контент из full) в конец существующего файла,
##           если маркеров ещё нет (файл создан до DevPlan 137). Идемпотентно.
## @io       ⇥ existing, full → ⎋ str
## @complexity O(N)
def _append_practices_section(existing: str, full: str) -> str:
    """Append Practices GENERATED section to existing file (markers absent case)."""
    if "## Practices" not in full or PRACTICES_END not in full:
        return existing
    block = full.split("## Practices", 1)[1]
    block = "## Practices" + block.split(PRACTICES_END, 1)[0] + PRACTICES_END
    return existing.rstrip("\n") + "\n\n" + block + "\n"


# endregion FUNC_append_practices_section


# ═══════════════════════════════════════════════════════════════════
# region FUNC_resolve_helpers
## @purpose  Path/name/org resolution helpers (defaults for CLI + scaffold wrapper).
## @complexity O(1) each


def _project_name(proj_path: Path, project_yaml_path: str = "") -> str:
    """Project name: ai-platform.yaml name → project → dir basename."""
    yaml_path = project_yaml_path or str(proj_path / "ai-platform.yaml")
    if Path(yaml_path).is_file():
        from core.internal.shared.project_yaml import load_project_yaml

        n = get_name(load_project_yaml(proj_path))
        if n:
            return n
    return proj_path.name


def _default_node_yaml_path(proj_path: Path, node_name: str = "") -> str:
    """Default node.yaml resolution — два канона (PROJECTS_BASE первичен):

    1. PROJECTS_BASE задан (VPS/CI): PROJECTS_BASE/org/node-configs/node/node.yaml
       (org = basename(parent(project_dir)) — путь-производная).
    2. PROJECTS_BASE НЕ задан (dev-машина, ~/projects layout): walk-up от project_dir:
       <project_dir>/../node-configs/<node>/node.yaml
       (~/projects/<context>/<project>/ + ~/projects/<context>/node-configs/<node>/node.yaml).
    3. Оба кандидата не найдены → "" (graceful degradation — как раньше).
    node берётся из ai-platform.yaml target_node (→ node_name аргумент → PLATFORM_DEFAULT_NODE).
    """
    # ── node: ai-platform.yaml target_node → аргумент → env fallback ──
    node = node_name or ""
    if not node:
        from core.internal.shared.project_yaml import get_target_node, load_project_yaml

        node = get_target_node(load_project_yaml(proj_path))
    if not node:
        node = os.environ.get("PLATFORM_DEFAULT_NODE", "tronyx-vps")

    projects_root = os.environ.get("PROJECTS_BASE", "")
    if projects_root:
        # VPS/CI-канон первичен: PROJECTS_BASE/org/node-configs/node/node.yaml
        if not proj_path.parent.is_dir():
            return ""
        candidate = Path(projects_root) / proj_path.parent.name / "node-configs" / node / "node.yaml"
        if candidate.is_file():
            logger.info("[IMP:8][gen_platform_md][node_yaml] PROJECTS_BASE-resolved: %s", candidate)
            return str(candidate)
        logger.info("[IMP:7][gen_platform_md][node_yaml] node.yaml not found at %s (PROJECTS_BASE path)", candidate)
        return ""

    # ── Dev walk-up без PROJECTS_BASE: <project_dir>/../node-configs/<node>/node.yaml ──
    candidate = proj_path.parent / "node-configs" / node / "node.yaml"
    if candidate.is_file():
        logger.info("[IMP:8][gen_platform_md][node_yaml] walk-up resolved: %s", candidate)
        return str(candidate)
    logger.info("[IMP:7][gen_platform_md][node_yaml] node.yaml not found at %s (walk-up) — graceful section", candidate)
    return ""


def _resolve_org(node_yaml_path: str, proj_path: Path) -> str:
    """Org for the canonical URL: node.yaml repos.core → context → path-derived org."""
    if node_yaml_path and Path(node_yaml_path).is_file():
        # ruff: ignore[PLW0717] — try вложен в условный блок внутри функции — после-try чтение локалей неанализируемо
        try:
            node = NodeYaml(node_yaml_path)
            core_repo = str(node.get("repos.core", default="") or "")
            if core_repo:
                org = core_repo.split("github.com/", 1)[-1].split("/", 1)[0]
                if org:
                    return org
            ctx = node.get_context()
            if ctx:
                return str(ctx)
        except (OSError, PlatformError) as exc:  # noqa: EXC — graceful fallback chain (best-effort)
            logger.info("[IMP:6][gen_platform_md][org] node.yaml org resolution skipped: %s", exc)
    return proj_path.parent.name if proj_path.parent.is_dir() else ""


# endregion FUNC_resolve_helpers


# ═══════════════════════════════════════════════════════════════════
# region FUNC_main
class _GenPlatformMdArgs(argparse.Namespace):
    """Typed argparse namespace (W11: Namespace attribute access is Any).

    ClassVar-аннотации БЕЗ значений (только типы) — значения ломают hasattr/parser-дефолты.
    """

    project_dir: ClassVar[str]
    node_yaml: ClassVar[str]
    platform_env: ClassVar[str]
    project_yaml: ClassVar[str]
    force: ClassVar[bool]
    domain: ClassVar[str]


## @purpose  CLI entry point: generate/write AI-PLATFORM.md for a project directory.
## @io       stdin: --project-dir [--node-yaml --platform-env --project-yaml --force --domain]
##           stdout: result status; stderr: LDD logs
## @exitcode 0  Success (created/updated/skipped/exists)
## @exitcode 1  Invalid args (missing --project-dir)
def main(argv: list[str] | None = None) -> int:
    """CLI entry point for gen_project_platform_md.py."""
    logging.basicConfig(
        # W11: getattr(logging, str) → Any; level must be int for basicConfig
        level=cast(int, getattr(logging, os.environ.get("LOG_LEVEL", "INFO"))),
        format="[%(levelname)s][gen_platform_md] %(message)s",
        stream=sys.stderr,
    )

    parser = argparse.ArgumentParser(description="Generate AI-PLATFORM.md (platform contract) for a project")
    parser.add_argument("--project-dir", required=True, type=str, help="Project directory (target: AI-PLATFORM.md)")
    parser.add_argument("--node-yaml", type=str, default="", help="Path to node.yaml (default: auto-resolve)")
    parser.add_argument("--platform-env", type=str, default="", help="Path to platform-env.yaml (default: repo root)")
    parser.add_argument("--project-yaml", type=str, default="", help="Path to ai-platform.yaml (default: project dir)")
    parser.add_argument("--force", action="store_true", default=False, help="Overwrite existing file without markers")
    parser.add_argument("--domain", type=str, default="", help="Node domain fallback for ${DOMAIN} substitution")
    args = parser.parse_args(argv, namespace=_GenPlatformMdArgs())

    project_dir = args.project_dir
    if not project_dir:
        print("FAIL-FAST: --project-dir is required", file=sys.stderr)
        return 1

    try:
        status = write_project_platform_md(
            project_dir,
            node_name="",
            node_yaml_path=args.node_yaml,
            platform_env_path=args.platform_env,
            project_yaml_path=args.project_yaml,
            force=args.force,
            domain=args.domain,
        )
    except OSError as exc:
        print(f"FAIL-FAST: write failed for {project_dir}: {exc}", file=sys.stderr)
        return 1

    print(f"AI-PLATFORM.md: {status} ({project_dir})")
    logger.info("[IMP:9][gen_platform_md][main] CLI done — %s", status)
    return 0


# endregion FUNC_main


if __name__ == "__main__":
    sys.exit(main())
