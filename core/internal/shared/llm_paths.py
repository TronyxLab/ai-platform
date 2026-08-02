#!/usr/bin/env python3
# GREP_SUMMARY: llm-paths, litellm-config, path-resolver, shared, litellm-config-yml, template, SoT
# STRUCTURE: ▶ ┌core_dir┐ → ○ litellm_config_path → modules/litellm/config/litellm-config.yml → ⎋ Path
#            → ▶ ┌core_dir┐ → ○ litellm_template_path → modules/litellm/config/litellm-config.yml.j2 → ⎋ Path
# region MODULE_CONTRACT
## @purpose  Единый источник пути litellm-config.yml (DevPlan 118 C6) — дедупликация 5 копий
##           вывода + 1 шаблона: context_deployer (LITELLM_CONFIG_PATH), deploy_orchestrator
##           (_render_litellm_config), llm_provision, phases, config_renderer (template .j2).
##           Правка пути применяется в ОДНОМ месте (AC-C6).
## @scope    Импортируется context_deployer.py, deploy_orchestrator.py, llm_provision.py,
##           phases.py, config_renderer.py (≥4 потребителя — критерий shared/). Чистые
##           резолверы без состояния: core_dir → Path.
## @invariants
##   1. litellm_config_path(core_dir) = <core_dir>/modules/litellm/config/litellm-config.yml
##   2. litellm_template_path(core_dir) = <core_dir>/modules/litellm/config/litellm-config.yml.j2
##   3. Пути immutable-композиция Path — никогда не строка-конкатенация
##   4. Модуль не импортирует bootstrap/deploy/* (слой shared — только вниз)
## @rationale C6 (DevPlan 118): минимум 4 копии пути litellm-config.yml + 1 шаблон — правка
##            пути (например, переезд litellm модуля) требовала 5 правок с риском расхождения.
##            Единый резолвер в shared/ устраняет источник дрейфа (AC-C6).
## @changes  2026-08-02 | DevPlan 118 C6 — Created (единый путь litellm-config.yml)
# endregion MODULE_CONTRACT

from __future__ import annotations

from pathlib import Path


# region FUNC_litellm_config_path
## @purpose  Резолвер пути вывода litellm-config.yml относительно core_dir (DevPlan 118 C6).
## @io       ⇥ core_dir: str | Path — корень core/ (VPS: /opt/platform/core) → ⎋ Path
## @complexity O(1)
## @invariants
##   - Результат всегда \p core_dir + modules/litellm/config/litellm-config.yml
def litellm_config_path(core_dir: str | Path) -> Path:
    """Resolve the litellm-config.yml output path under core_dir (C6)."""
    return Path(core_dir) / "modules" / "litellm" / "config" / "litellm-config.yml"


# endregion FUNC_litellm_config_path


# region FUNC_litellm_template_path
## @purpose  Резолвер пути Jinja2-шаблона litellm-config.yml.j2 относительно core_dir (C6).
## @io       ⇥ core_dir: str | Path — корень core/ (repo: \p repo_root/core) → ⎋ Path
## @complexity O(1)
## @invariants
##   - Результат всегда \p core_dir + modules/litellm/config/litellm-config.yml.j2
def litellm_template_path(core_dir: str | Path) -> Path:
    """Resolve the litellm-config.yml.j2 template path under core_dir (C6)."""
    return Path(core_dir) / "modules" / "litellm" / "config" / "litellm-config.yml.j2"


# endregion FUNC_litellm_template_path
