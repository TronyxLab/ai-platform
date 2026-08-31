# GREP_SUMMARY: enabled-modules, node-yaml, get-modules, module-aware, secrets-fail-loud, consumers, node-resolution
# STRUCTURE: ▶ resolve_node_yaml_path [NODE_NAME → NODE_CONFIGS_DIR → node_configs_remote → CWD/node-configs] → ◇ NodeYaml.get_modules() (list|dict) → ⊕ enabled-filter → ⎋ set[str] | None (None = standalone → legacy)
# region MODULE_CONTRACT
## @purpose  Единый резолвер enabled-модулей ноды из node.yaml для module-aware проверок.
##           Потребитель-прецедент: fail-loud валидация секретов — секрет tier=required ∧
##           source=sops требуется ТОЛЬКО если хотя бы один его consumer-модуль enabled в
##           node.yaml (минимальный контекст без postgres/minio/hermes/monitoring НЕ обязан
##           нести их секреты). Возвращает None когда node.yaml недоступен (standalone без
##           NODE / файл не найден) — вызывающий сохраняет ЛЕГАСИ-поведение (глобальная
##           проверка всех required∧sops), что совместимо с существующими unit-тестами.
## @scope    core/internal/shared/enabled_modules.py. Потребители: core/internal/secrets/
##           decrypt_secrets.py (apply_ci_default_injection), core/internal/bootstrap/lifecycle/
##           helpers/secrets.py (verify_required_sops_secrets). Резолв node.yaml — тот же канон,
##           что resolve_enc_path/_resolve_dev_secrets_path (NODE_CONFIGS_DIR → /opt/node-configs
##           → репо node-configs/ на dev-машине).
## @invariants
##   - NODE_NAME пуст / node.yaml не найден → None (никакого файлового I/O при пустом имени)
##   - Возвращается МНОЖЕСТВО enabled-модулей (имена), никогда None при наличии node.yaml
##   - enabled-семантика как у деплоя: значение истинно при bool True / строке "true"/"1"/"yes";
##     отсутствующий enabled → True (объявленный модуль деплоится; консистентно с
##     secrets_validator.parse_modules_from_node_yaml default-enabled)
##   - dict-формат node.yaml modules {name: {enabled, config_overlay}} поддержан (fallback)
##   - Модуль с пустым name пропускается (не влияет на requirement)
## @rationale  Два независимых fail-loud-валидатора (decrypt + lifecycle postcondition) обязаны
##             видеть ОДНО множество enabled-модулей — дублирование резолва разъехалось бы
##             (один блокирует, второй нет). Единый shared-резолвер — DRY без layering-нарушений
##             (shared не импортирует bootstrap/deploy).
## @changes  2026-08-31 | Created — module-aware secrets fail-loud (launch-validation asi-team-vps)
# endregion MODULE_CONTRACT

from __future__ import annotations

import logging
import os
import pathlib
from collections.abc import Mapping
from typing import cast

from core.internal.shared.deploy_paths import node_configs_remote
from core.internal.shared.exceptions import ConfigValidationError

logger = logging.getLogger(__name__)

_TRUE_STRINGS = frozenset({"true", "1", "yes", "on"})


# region FUNC__is_enabled
## @purpose  Нормализация значения enabled (bool | str) в bool. Отсутствующее значение →
##           True (деплой-семантика: объявленный модуль деплоится, консистентно с
##           secrets_validator.parse_modules_from_node_yaml default-enabled="true").
## @io       ⇥ value: object (bool | str | None) → ⎋ bool
## @complexity O(1)
def _is_enabled(value: object) -> bool:
    """Normalize an `enabled` field value (bool/str) to bool; absent → True (deploy semantics)."""
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        return value.strip().lower() in _TRUE_STRINGS
    # Числа/прочее: True только для явного true-значения; числовой 1/0 редок в YAML-модулях
    if isinstance(value, int):
        return value == 1
    return True


# endregion FUNC__is_enabled


# region FUNC_resolve_node_yaml_path
## @purpose  Резолв пути node.yaml ноды: env NODE_CONFIGS_DIR → канон node_configs_remote()
##           (/opt/node-configs на VPS) → dev-repo fallback (CWD/node-configs на машине
##           оператора). Тот же порядок канонов, что decrypt_secrets._resolve_dev_secrets_path.
## @io       ⇥ node_name: str, env: Mapping | None (None = os.environ) → ⎋ pathlib.Path | None
## @complexity O(1) — 1-3 is_file probe
## @invariants
##   - Пустое node_name → None без I/O
##   - Только существующий файл принимается; первый совпавший кандидат выигрывает
def resolve_node_yaml_path(*, node_name: str, env: Mapping[str, str] | None = None) -> pathlib.Path | None:
    """Resolve `<configs_dir>/<node_name>/node.yaml` (env → remote → dev-repo), or None."""
    name = (node_name or "").strip()
    if not name:
        logger.info("[IMP:7][enabled_modules] node_name empty — no node.yaml resolution (standalone)")
        return None

    source: Mapping[str, str] = os.environ if env is None else env
    candidates: list[pathlib.Path] = []
    configs_dir = (source.get("NODE_CONFIGS_DIR", "") or "").strip()
    if configs_dir:
        candidates.append(pathlib.Path(configs_dir) / name / "node.yaml")
    # Канон VPS: /opt/node-configs/<node>/node.yaml (core-deliver доставляет node-configs на ноду)
    candidates.append(node_configs_remote(env=source) / name / "node.yaml")
    # Dev-repo fallback (машина оператора): ./node-configs/<node>/node.yaml
    candidates.append(pathlib.Path.cwd() / "node-configs" / name / "node.yaml")

    seen: set[str] = set()
    for candidate in candidates:
        key = str(candidate)
        if key in seen:
            continue
        seen.add(key)
        if candidate.is_file():
            logger.info("[IMP:8][enabled_modules] node.yaml for node=%s → %s", name, candidate)
            return candidate
    logger.info("[IMP:7][enabled_modules] node.yaml for node=%s not found in %d candidate(s)", name, len(candidates))
    return None


# endregion FUNC_resolve_node_yaml_path


# region FUNC_enabled_modules_from_node_yaml
## @purpose  Извлечь множество enabled-модулей из node.yaml через канон NodeYaml.get_modules()
##           (list-формат; dict-формат {name: {enabled}} — fallback на сырой get).
## @io       ⇥ path: Path → ⎋ set[str] (пусто при отсутствии секции modules)
## @complexity O(M), M = число записей modules
## @invariants
##   - NodeYaml.get_modules() — SoT чтения node.yaml (DevPlan 117 D20)
##   - ConfigValidationError (не-list) → dict-формат fallback через get("modules")
def enabled_modules_from_node_yaml(path: pathlib.Path) -> set[str]:
    """Return the set of enabled module names declared in node.yaml."""
    from core.internal.shared.node_yaml import NodeYaml

    node = NodeYaml(str(path))
    try:
        raw_modules: object = node.get_modules()
    except ConfigValidationError:
        # dict-формат {name: {enabled, config_overlay}} NodeYaml не поддерживает (list-only) —
        # fallback на сырой get (тот же паттерн, что secrets_validator.parse_modules_from_node_yaml)
        raw_modules = node.get("modules", default=cast(dict[str, object], {}))

    enabled: set[str] = set()
    modules = cast(object, raw_modules)
    if isinstance(modules, list):
        # Типизированная граница NodeYaml.get_modules() (DevPlan 117 D20) — list[dict[str, object]],
        # тот же паттерн cast-без-isinstance, что secrets_validator.parse_modules_from_node_yaml
        for m in cast(list[dict[str, object]], modules):
            name = str(m.get("name", "")).strip()
            if not name:
                continue
            if _is_enabled(m.get("enabled")):
                enabled.add(name)
    elif isinstance(modules, dict):
        for name, value in cast(dict[str, object], modules).items():
            enabled_value: object = (
                cast(dict[str, object], value).get("enabled", True) if isinstance(value, dict) else value
            )
            if _is_enabled(enabled_value):
                enabled.add(str(name))

    logger.info(
        "[IMP:8][enabled_modules] %d enabled module(s) in %s: %s", len(enabled), path, ", ".join(sorted(enabled))
    )
    return enabled


# endregion FUNC_enabled_modules_from_node_yaml


# region FUNC_resolve_enabled_modules
## @purpose  Единая точка входа: enabled-модули ноды по имени ноды и env. None → node.yaml
##           недоступен → вызывающий сохраняет легаси-поведение (глобальная проверка).
## @io       ⇥ node_name: str, env: Mapping | None (None = os.environ) → ⎋ set[str] | None
## @complexity O(1) резолв + O(M) чтение
## @invariants
##   - None ТОЛЬКО при недоступности node.yaml (standalone без NODE) — НЕ пустое множество
##   - Никогда не raise при отсутствии файла (файл может отсутствовать легитимно)
def resolve_enabled_modules(*, node_name: str = "", env: Mapping[str, str] | None = None) -> set[str] | None:
    """Resolve enabled modules from node.yaml; None when node.yaml unavailable (legacy global)."""
    path = resolve_node_yaml_path(node_name=node_name, env=env)
    if path is None:
        logger.info(
            "[IMP:7][enabled_modules] node.yaml unavailable for node=%r — legacy global validation (no module filter)",
            (node_name or "").strip(),
        )
        return None
    return enabled_modules_from_node_yaml(path)


# endregion FUNC_resolve_enabled_modules
