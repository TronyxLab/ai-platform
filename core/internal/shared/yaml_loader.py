#!/usr/bin/env python3
# GREP_SUMMARY: yaml_loader, platform-env, secret-definitions, SoT-YAML, typed-reader, PlatformEnv, env_defaults, secrets, dedup
# STRUCTURE: ▶ load_platform_env → ◇ yaml.safe_load → ⊕ networks/volumes/env_defaults/profiles → ⟦PlatformEnv⟧ → ▶ load_secret_definitions → ◇ missing? → ⎋ [] → ⊕ secrets list → ⟦list[dict]⟧
# region MODULE_CONTRACT
## @purpose  Типизированные читатели SoT-YAML (platform-env.yaml, secret-definitions.yaml) —
##           единый слой YAML-парсинга вместо 3 локальных дублей (DevPlan 177 W3.5).
## @scope    core/internal/shared/ — потребители: provisioner.py (типизированный PlatformEnv),
##           scripts/sync_env_defaults.py (env_defaults + secret-проекция),
##           scripts/generate_secrets_manifest.py (raw secrets-список).
##           НЕ затрагивает node.yaml (NodeYaml-фасад) и ai-platform.yaml (project_yaml) —
##           отдельные SoT-читатели своих доменов.
## @invariants
##   1. load_platform_env → PlatformEnv: FileNotFoundError при отсутствии файла,
##      yaml.YAMLError при битом YAML (fail-fast, совместимо с provisioner.main).
##   2. env_defaults нормализуются к str (None → "") — единая семантика обоих потребителей
##      (provisioner пишет KEY=VALUE в GITHUB_ENV; sync_env_defaults рендерит строки).
##   3. load_secret_definitions → list[dict[str, object]]: отсутствие файла → [] + warning
##      (НЕ raise — семантика generate_secrets_manifest), 'secrets' не список → [] + error.
##   4. Входные SoT-файлы НЕ мутируются (read-only).
##   5. Типы NetworkConfig/VolumeConfig/PlatformEnv перенесены из provisioner.py
##      (shared — ниже по зависимостям; provisioner re-export'ит их для обратной совместимости).
## @rationale DevPlan 177 W3.5 — дедупликация чтения SoT-YAML: provisioner (типизированный),
##            sync_env_defaults (dict-формы), generate_secrets_manifest (raw-список) парсили
##            одни и те же файлы с расходящейся семантикой (missing-file raise vs []).
##            Единый слой устраняет дублирование YAML-кода и дрейф edge-case'ов.
##            Критерий shared-инвентаря (минимум 2 потребителя) выполнен: 3 потребителя.
## @changes  2026-08-16 | DevPlan 177 W3.5 — создан; типы PlatformEnv/NetworkConfig/VolumeConfig
##                      и load_platform_env перенесены из provisioner.py; load_secret_definitions
##                      консолидирован из generate_secrets_manifest.py
# endregion MODULE_CONTRACT

from __future__ import annotations

import logging
from dataclasses import dataclass
from pathlib import Path
from typing import cast

import yaml

logger = logging.getLogger(__name__)


# ── Typed model (перенесено из provisioner.py, DevPlan 177 W3.5) ──


# region DATACLASS_NetworkConfig
@dataclass
class NetworkConfig:
    """Single Docker network definition from platform-env.yaml."""

    name: str
    driver: str = "bridge"
    internal: bool = False


# endregion DATACLASS_NetworkConfig


# region DATACLASS_VolumeConfig
@dataclass
class VolumeConfig:
    """Single volume directory definition from platform-env.yaml.

    ## @purpose — Host directory (bind-mount source) + optional владелец.
    ## @invariants
    ##   - owner: "uid:gid" или "" — применяется chown при создании/несовпадении
    ##   - Postgres wal-archive требует владельца postgres (999:999) — TRAP[BUG] 2026-08-03
    """

    path: str
    owner: str = ""


# endregion DATACLASS_VolumeConfig


# region DATACLASS_PlatformEnv
@dataclass
class PlatformEnv:
    """Parsed platform-env.yaml structure."""

    networks: list[NetworkConfig]
    volumes: list[VolumeConfig]
    env_defaults: dict[str, str]
    profiles: list[str]


# endregion DATACLASS_PlatformEnv


# region FUNC_load_platform_env
def load_platform_env(yaml_path: Path) -> PlatformEnv:
    """Parse platform-env.yaml into typed PlatformEnv.

    ## @purpose  Единый типизированный читатель platform-env.yaml (SoT). Извлекает
    ##            4 секции: networks, volumes, env_defaults, profiles. Missing-секции
    ##            трактуются как пустые (list/dict). env_defaults нормализуются к str
    ##            (None → "") — общая семантика provisioner и sync_env_defaults.
    ## @io        ⇥ yaml_path: Path → ⎋ PlatformEnv
    ##            ⚡ raise FileNotFoundError (нет файла) / yaml.YAMLError (битый YAML)
    ## @complexity O(N) где N = размер YAML
    ## @invariants
    ##   - FileNotFoundError(yaml_path) если файл отсутствует (fail-fast, provisioner.main)
    ##   - env_defaults: str(v) при не-None (int → "9000"), None → "" (без "None"-мусора)
    ##   - networks/volumes: non-dict записи пропускаются (isinstance-фильтр)
    ##   - LDD block name: [yaml_loader]
    """
    if not yaml_path.is_file():
        raise FileNotFoundError(yaml_path)

    logger.info("[IMP:7][yaml_loader] Reading platform-env.yaml from %s", yaml_path)

    with Path(yaml_path).open(encoding="utf-8") as f:
        # W11: yaml.safe_load returns Any → cast to payload boundary
        loaded = cast(dict[str, object] | None, yaml.safe_load(f))
    data: dict[str, object] = loaded if loaded is not None else {}

    networks_raw: object = data.get("networks") or []
    networks = [
        NetworkConfig(
            name=cast(str, cast(dict[str, object], n).get("name")),
            driver=cast(str, cast(dict[str, object], n).get("driver", "bridge")),
            internal=bool(cast(dict[str, object], n).get("internal", False)),
        )
        for n in cast(list[object], networks_raw)
        if isinstance(n, dict)
    ]

    volumes_raw: object = data.get("volumes") or []
    volumes = [
        VolumeConfig(
            path=cast(str, cast(dict[str, object], v).get("path")),
            owner=str(cast(dict[str, object], v).get("owner", "") or "").strip(),
        )
        for v in cast(list[object], volumes_raw)
        if isinstance(v, dict)
    ]

    # W11: dict()/list() copy semantics preserved (matching original provisioner);
    # env_defaults: единая str-нормализация (None → "") для обоих потребителей (W3.5)
    env_defaults_raw: object = data.get("env_defaults") or {}
    if isinstance(env_defaults_raw, dict):
        # W11: isinstance-narrowed dict — ключи уже str (reportUnnecessaryCast на k не нужен)
        env_defaults: dict[str, str] = {
            k: str(v) if v is not None else "" for k, v in cast(dict[str, object], env_defaults_raw).items()
        }
    else:
        env_defaults = {}
    profiles_raw: object = data.get("profiles") or []
    profiles = cast(list[str], list(cast(list[object], profiles_raw)))

    result = PlatformEnv(
        networks=networks,
        volumes=volumes,
        env_defaults=env_defaults,
        profiles=profiles,
    )

    logger.info(
        "[IMP:8][yaml_loader] Parsed: %d networks, %d volumes, %d env vars, %d profiles",
        len(result.networks),
        len(result.volumes),
        len(result.env_defaults),
        len(result.profiles),
    )
    logger.info("[IMP:9][yaml_loader] Loaded %d env_defaults", len(result.env_defaults))

    return result


# endregion FUNC_load_platform_env


# region FUNC_load_secret_definitions
def load_secret_definitions(path: Path | str) -> list[dict[str, object]]:
    """Load raw secret definitions from secret-definitions.yaml.

    ## @purpose  Единый читатель secret-definitions.yaml (SoT). Возвращает сырой список
    ##            secret-записей с ПОЛНЫМ сохранением полей (потребитель-проекция решает,
    ##            какие поля извлекать). Семантика missing-файла унаследована от
    ##            generate_secrets_manifest: [] + warning, НЕ raise (main() pre-flight
    ##            проверяет существование файла до вызова).
    ## @io        ⇥ path: Path | str → ⎋ list[dict[str, object]] (пусто при отсутствии/не-list)
    ## @complexity O(N) где N = число секретов в файле
    ## @invariants
    ##   - str path нормализуется в Path
    ##   - Отсутствующий файл → [] + [IMP:8] warning
    ##   - 'secrets' не список → [] + [IMP:9] error
    ##   - Non-dict записи в secrets пропускаются
    ##   - LDD block name: [yaml_loader]
    """
    if isinstance(path, str):
        path = Path(path)

    logger.info("[IMP:7][yaml_loader] Loading secret definitions from %s", path)

    if not path.is_file():
        logger.warning("[IMP:8][yaml_loader] Secret definitions file not found: %s — returning empty", path)
        return []

    with Path(path).open(encoding="utf-8") as f:
        # W11: yaml.safe_load returns Any → cast to secret-defs boundary
        data = cast(dict[str, object] | None, yaml.safe_load(f))

    secrets: object = data.get("secrets", []) if data else []
    if not isinstance(secrets, list):
        logger.error("[IMP:9][yaml_loader] 'secrets' key is not a list in %s", path)
        return []

    # W11: list[Unknown] after isinstance → cast to object list → typed list of opaque entries
    typed_secrets: list[dict[str, object]] = [
        cast(dict[str, object], s) for s in cast(list[object], secrets) if isinstance(s, dict)
    ]

    logger.info(
        "[IMP:9][yaml_loader] Loaded %d secret definitions from %s",
        len(typed_secrets),
        path,
    )
    return typed_secrets


# endregion FUNC_load_secret_definitions
