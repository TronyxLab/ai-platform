#!/usr/bin/env python3
# GREP_SUMMARY: key_provisioner, idempotent, virtual-keys, LiteLLM, provision_all, CLI, persist, profile-rules, atomic-store, resolve-base-url, loopback-fallback
# STRUCTURE: ▶ resolve_base_url(explicit→env→DNS-check→loopback) → parse_args() →
#            ◇ provision_all(master_key, base_url, policy_path) →
#            ◇ load policy.yaml → ◇ discover consumers (projects + platform) →
#            ◇ list_keys() ONCE (пагинация внутри) → ⊕ all_keys cache → ○ for each consumer:
#            ┌─ ◇ resolve profile (explicit → rule → default) → ┌─ ◇ get profile config → ⊕ apply overrides →
#            ├─ ◇ find_key_by_metadata(all_keys, project=name) → ◇ exists? ─┬─ models match? → ⚡ skip
#            │                                                          ├─ models differ? → ⚡ update_key (fail → WARN+failed)
#            │                                                          └─ not exists? → ⚡ generate_key (fail → WARN+failed)
#            ├─ ◇ persist_project_key(name, key) [FileLock(store.lock) + atomic_write_json] → ⊕ keys[name] = key
#            └─ ⚡ failed>0 → ⚡ PlatformError
#            ⎋ summary (created/updated/idempotent/skipped_disabled/failed ≠ mixed-skip) → exit_code
# region MODULE_CONTRACT
## @purpose  Idempotent virtual key provisioner for LiteLLM. Discovers LLM consumers
##           (projects + platform services), resolves profiles, and creates/updates/skips
##           LiteLLM virtual keys. Keys are persisted to a JSON store for later use by
##           env-sync (Wave 6: SOPS integration).
## @scope    DevPlan 049 Phase 4 — Key Provisioner. Called from provision-llm.sh.
##           Idempotency: repeated calls produce identical key sets per project.
## @invariants
##   - Idempotent: same key on repeated calls if config unchanged
##   - Profile resolution order: explicit (project.llm.profile) → rule match → default
##   - Empty overrides are safe (no-op merge)
##   - Store write: FileLock(<store>.lock) + atomic_write_json(mode=0600); corrupt store
##     → PlatformError (fail-loud, НИКОГДА overwrite-all — REF-0104)
##   - Key-list скачивается ONCE за прогон; lookup — локальный filter (PERF-081)
##   - Transport-failure при lookup/update/generate → WARN + failed++, НЕ «no key»
##     (REF-0104: иначе transient-сбой порождает дубликаты budget-bearing ключей);
##     failed>0 → PlatformError (exit≠0), честный summary отличает failure от skipped
## @rationale Python-first: all business logic in Python, shell is a thin facade.
##            Idempotency prevents duplicate key creation during retries.
## @changes — 2026-07-24 | Created (DevPlan 049 Phase 4)
##           2026-08-01 | DevPlan 117 D24 — discover_projects shim → shared/project_registry.discover_llm_projects
##                      (реальная детекция ai-platform.yaml llm.enabled: true; TRAP[DECISION] снят)
##           2026-08-14 | DevPlan 170 W1-A3 — _DEFAULT_BASE_URL порт из shared/platform_ports
##           2026-08-24 | REF-0007 — persist_project_key: atomic_write_json(mode=0600) от создания
##                      (plain open("w")+chmod-после удалён — нет world-readable окна)
##           2026-08-25 | REF-0104 — corrupt-store fail-fast + FileLock; list_keys ONCE;
##                      transport-error ≠ no-key; честный фазовый summary (failed ≠ skipped)
##           2026-08-27 | F-10 (P1) — resolve_base_url: explicit → $LLM_BASE_URL/$LITELLM_BASE_URL →
##                      default с DNS-check → loopback fallback (docker DNS "litellm" не резолвится
##                      из host-процессов ноды/dev — φ11 llm_provision падал ConnectError)
# endregion MODULE_CONTRACT

import argparse
import json
import logging
import os
import pathlib
import socket
import sys
import tempfile
from collections.abc import Mapping
from copy import deepcopy
from typing import TypedDict, cast

# ⚠️ TRAP[BUG] · 2026-08-05 · HI · Standalone-инвокация key_provisioner.py без PYTHONPATH → ModuleNotFoundError
# · Symptom: `env -i python3 key_provisioner.py --help` падал на `from core.internal.llm.admin_client...`
# ·   (provision-llm.sh экспортирует PYTHONPATH — но прямой вызов модуля без него ломался;
# ·   латентный класс A, DevPlan 136 W2 T2.10).
# · Root: _PROJECT_ROOT определялся ПОСЛЕ core.* импортов и без sys.path.insert — self-bootstrap отсутствовал.
# · Fix: self-bootstrap корня репо (канон config_renderer.py:44-45) ДО core.* импортов.
# ·   Файл: core/internal/llm/key_provisioner.py → корень = 4 уровня parent.
# · Prevention: core.*-модули не полагаются на внешний PYTHONPATH — self-bootstrap в источнике.
# · DevPlan 136 W2 T2.10: тест env -i python3 key_provisioner.py --help → exit 0.
_PROJECT_ROOT = pathlib.Path(__file__).resolve().parent.parent.parent.parent
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

from core.internal.llm.admin_client import (
    KeyInfo,
    LiteLLMAdminClient,
    LiteLLMTransportError,
    find_key_by_metadata,
)
from core.internal.llm.policy_schema import LLMPolicy

# REF-0007 (11-DevPlan Волна 1): канонический atomic writer — plaintext JSON-хранилище
# LLM-ключей пишется mode=0600 ОТ СОЗДАНИЯ (нет окна world-readable в tmpdir)
from core.internal.shared.atomic_writer import atomic_write_json

# plan 012 T12 (F-020): PLATFORM_STATE_DIR через канонический резолвер deploy_paths
# (литерал tempfile.gettempdir() удалён из дефолта стора; dev-fallback — WARN+tmp)
from core.internal.shared.deploy_paths import bootstrap_state_dir, secrets_env_file
from core.internal.shared.exceptions import PlatformError

# REF-0104 (11-DevPlan Волна 3): FileLock сериализует read-modify-write стора между
# конкурентными provision-прогонами (bootstrap φ8/φ12 гонка с ручным make provision-llm)
from core.internal.shared.file_lock import FileLock

# DevPlan 170 W1-A3: порт из единого реестра shared/platform_ports (литерал 4000 удалён)
from core.internal.shared.platform_ports import PLATFORM_PORT_LITELLM

logger = logging.getLogger(__name__)

# ── Constants ────────────────────────────────────────────────────────────────

# F-10 (P1): docker DNS-имя по умолчанию — резолвится ТОЛЬКО внутри compose-сети.
# Из host-процессов ноды/dev (φ11 registry_update llm_provision, deploy-context,
# provision-llm.sh subprocess) "litellm" → gaierror → ConnectError. Резолв — resolve_base_url().
_DEFAULT_BASE_URL: str = f"http://litellm:{PLATFORM_PORT_LITELLM}"
# F-10: loopback-фасад для host-run (make provision-llm использует тот же explicit URL).
# Порт — из SoT shared/platform_ports (НЕ литерал 4000 — гейт порт-parity).
_LOOPBACK_BASE_URL: str = f"http://127.0.0.1:{PLATFORM_PORT_LITELLM}"
_KEY_PREVIEW_LEN: int = 16  # сколько символов ключа показывать в логах (маскировка)
_BUDGET_EPSILON: float = 0.001  # допустимое расхождение daily-бюджета (float-сравнение)
_DEFAULT_POLICY_REL_PATH = pathlib.Path("core") / "internal" / "llm" / "policy.yaml"
_LOCK_TIMEOUT_SECONDS: float = 30.0  # ожидание store.lock при конкурентном provision

# DevPlan 16 T1.D (P0-5): зарезервированные ключи метаданных — профильная конфигурация НЕ
# может затереть их в key_metadata (иначе lookup find_key_by_metadata(project=…) ломается)
_RESERVED_METADATA_KEYS: frozenset[str] = frozenset({"project"})


# region BASE_URL_RESOLUTION
# region FUNC__extract_host
## @purpose  Извлечение hostname из base_url (scheme- и path-агностично). Единая точка
##           парсинга для resolve_base_url (DNS-check) и _ensure_local_proxy_neutral (NO_PROXY).
## @io       ⇥ url: str → ⎋ str (hostname без порта)
## @complexity O(1)
def _extract_host(url: str) -> str:
    """Extract hostname from a base URL (scheme/path-agnostic)."""
    hostport = url.split("://", 1)[-1].split("/", 1)[0] if "://" in url else url.split(":", 1)[0]
    return hostport.split(":", 1)[0]


# endregion FUNC__extract_host


# region FUNC_resolve_base_url
## @purpose  F-10 (P1): честный резолв base_url для provisioning. Приоритет:
##           explicit (CLI --base-url / программный вызов — контракт сохраняется) →
##           env LLM_BASE_URL/LITELLM_BASE_URL (непустой) → _DEFAULT_BASE_URL
##           ("http://litellm:PORT") с DNS-check: docker-hostname резолвится (compose-сеть)
##           → дефолт; иначе (host-run ноды/dev: gaierror) → loopback
##           "http://127.0.0.1:PORT" + [IMP:8] лог fallback.
##           Фон: φ11 registry_update llm_provision и deploy-context запускают
##           provision-llm.sh БЕЗ --base-url → argparse-дефолт упирался в docker DNS-имя,
##           не резолвимое из host-процессов → ConnectError «Temporary failure in name
##           resolution» → fail-soft done_with_warnings, virtual keys НЕ провижинились.
## @io       ⇥ explicit: str | None → ⎋ str (резолвнутый base_url, всегда пригоден к connect)
## @complexity O(1) + DNS-lookup (getaddrinfo без таймаута — localhost fail-fast, OK)
## @invariants
##   - explicit непустой → возвращается БЕЗ DNS-запроса (getaddrinfo не трогается)
##   - env читается как «задан, если непуст» (пустая строка = отсутствует)
##   - getaddrinfo вызывается ТОЛЬКО для хоста _DEFAULT_BASE_URL; gaierror → loopback
## @rationale Q: почему не менять _DEFAULT_BASE_URL на loopback всегда? A: внутри compose-сети
##            (bootstrap φ8, container-run) корректный адрес — docker DNS "litellm"; loopback
##            сломал бы контейнерный путь. DNS-check честно различает два окружения.
def resolve_base_url(explicit: str | None) -> str:
    """Resolve the effective LiteLLM base URL for provisioning (F-10)."""
    # 1. Explicit — CLI/программный контракт приоритетен (make provision-llm: 127.0.0.1)
    if explicit:
        logger.log(
            logging.INFO,
            "[IMP:8][resolve_base_url] Using explicit base_url: %s",
            explicit,
        )
        return explicit

    # 2. Env override (непустой): LLM_BASE_URL → LITELLM_BASE_URL
    for env_name in ("LLM_BASE_URL", "LITELLM_BASE_URL"):
        env_url = str(os.environ.get(env_name) or "")
        if env_url:
            logger.log(
                logging.INFO,
                "[IMP:8][resolve_base_url] Using $%s base_url: %s",
                env_name,
                env_url,
            )
            return env_url

    # 3. Default "http://litellm:PORT" — резолвится ТОЛЬКО внутри compose-сети.
    host = _extract_host(_DEFAULT_BASE_URL)
    try:
        _ = socket.getaddrinfo(host, None)  # DNS-liveness probe (F-10)
    except OSError:
        logger.log(
            logging.INFO,
            "[IMP:8][resolve_base_url] docker-hostname %r unresolvable — falling back to loopback %s",
            host,
            _LOOPBACK_BASE_URL,
        )
        return _LOOPBACK_BASE_URL
    logger.log(
        logging.INFO,
        "[IMP:8][resolve_base_url] docker-hostname %r resolvable — keeping default %s",
        host,
        _DEFAULT_BASE_URL,
    )
    return _DEFAULT_BASE_URL


# endregion FUNC_resolve_base_url
# endregion BASE_URL_RESOLUTION


# ── Project root resolution ──────────────────────────────────────────────────
# _PROJECT_ROOT определён выше (self-bootstrap, W2 T2.10) — см. шапку модуля.


# region DATA_Consumer
class Consumer(TypedDict, total=False):
    """Дескриптор LLM-потребителя (проект или platform-сервис) — граница JSON/YAML.

    ## @purpose  Единица обнаружения ключей: name + опциональный llm-конфиг
    ##            (enabled, profile, overrides). Источники: project_registry
    ##            (ai-platform.yaml) и get_platform_consumers().
    """

    name: str
    llm: dict[str, object]


# endregion DATA_Consumer


# region DATA_ProfileConfig
class ProfileConfig(TypedDict):
    """Эффективная конфигурация профиля (базовая + overrides) для /key/generate|update.

    ## @purpose  Единый носитель параметров ключа: models/budget/rpm_limit/metadata —
    ##            строится get_profile_config + apply_overrides, потребляется
    ##            key_config_matches и admin_client.
    """

    models: list[str]
    budget: dict[str, float]
    rpm_limit: int
    metadata: dict[str, str]


# endregion DATA_ProfileConfig


# region CONSUMER_DISCOVERY


def discover_projects() -> list[Consumer]:
    """Discover LLM-enabled projects from ai-platform.yaml files.

    ## @purpose  Scan project directories for ai-platform.yaml with llm section.
    ##           Returns a list of project descriptors with name and llm config.
    ##           Делегирует в shared/project_registry.discover_llm_projects (DevPlan 117 D24) —
    ##           реальная детекция вместо хардкод-шима.
    ## @io
    ##   - ⎋ list[dict] — each dict has 'name' (str) and 'llm' (dict with 'enabled', etc.)
    ## @complexity O(P * Y) где P = проекты в node.yaml, Y = parse ai-platform.yaml
    ## @invariants
    ##   - Каждый entry имеет минимум 'name' и 'llm.enabled'
    ##   - Проекты с llm.enabled: false пропускаются (фильтр в project_registry)
    ## @rationale TRAP[DECISION] 2026-07-24 (shim) снят: реальная детекция через
    ##            shared/project_registry.discover_llm_projects — фильтр по ai-platform.yaml
    ##            llm.enabled: true (DevPlan 117 D24, рев-условие выполнено).
    """
    # DevPlan 117 D24: единая детекция LLM-проектов в shared/project_registry (без хардкода).
    from core.internal.shared.project_registry import discover_llm_projects

    projects = discover_llm_projects()
    logger.log(
        logging.INFO,
        "[IMP:8][discover_projects] Delegated to project_registry.discover_llm_projects — %d LLM-enabled project(s)",
        len(projects),
    )
    # W11: list[dict[str, object]] → list[Consumer] (dict → TypedDict — cast через object)
    return [cast("Consumer", cast(object, p)) for p in projects]


def get_platform_consumers() -> list[Consumer]:
    """Return hardcoded platform service consumers that need LLM keys.

    ## @purpose  Platform services (like hermes-agent) are not projects but
    ##           still need virtual keys. They are defined here as pseudo-projects.
    ## @io  ⎋ list[dict] — each with 'name' (str) — no llm dict, profile resolved via rules
    ## @complexity O(1)
    ## @invariants
    ##   - Platform consumers always have llm.enabled = true
    ##   - Their profile is resolved via auto_provision.profile_rules
    ##   - They have no overrides — only what the rule dictates
    """
    return [
        cast("Consumer", cast(object, {"name": "hermes-agent", "llm": cast("dict[str, object]", {"enabled": True})})),
    ]


# endregion CONSUMER_DISCOVERY


# region PROFILE_RESOLUTION


def resolve_profile(
    consumer: Consumer,
    policy: LLMPolicy,
) -> str:
    """Resolve the profile name for a consumer.

    ## @purpose  Priority order:
    ##   1. Explicit profile from consumer llm.llm.profile
    ##   2. Matching rule from policy.auto_provision.profile_rules
    ##   3. Default from policy.auto_provision.default_profile
    ## @io
    ##   - consumer: dict — consumer descriptor with 'name' and optional 'llm.profile'
    ##   - policy: LLMPolicy — loaded policy with profile_rules
    ##   - ⎋ str — resolved profile name
    ## @complexity O(R) where R = number of profile_rules
    ## @invariants
    ##   - Rule matching: first rule where rule.match matches consumer name wins
    ##   - default_profile is guaranteed to exist (validated by LLMPolicy.from_yaml)
    """
    consumer_name = consumer.get("name", "unknown")

    # 1. Explicit profile
    llm_config = consumer.get("llm", {})
    if isinstance(llm_config, dict):
        explicit_profile = llm_config.get("profile")
        if explicit_profile:
            logger.log(
                logging.INFO,
                "[IMP:8][resolve_profile] Explicit profile for '%s': %s",
                consumer_name,
                explicit_profile,
            )
            return cast("str", explicit_profile)  # W11: YAML-граница (object) → str (profile — строка SoT)

    # 2. Rule match
    for rule in policy.auto_provision.profile_rules:
        match_criteria = rule.match
        if isinstance(match_criteria, dict) and match_criteria.get("name") == consumer_name:
            logger.log(
                logging.INFO,
                "[IMP:8][resolve_profile] Rule match for '%s': profile=%s",
                consumer_name,
                rule.profile,
            )
            return rule.profile

    # 3. Default
    logger.log(
        logging.INFO,
        "[IMP:8][resolve_profile] Default profile for '%s': %s",
        consumer_name,
        policy.auto_provision.default_profile,
    )
    return policy.auto_provision.default_profile


def get_profile_config(
    profile_name: str,
    policy: LLMPolicy,
) -> ProfileConfig:
    """Get the effective configuration from a profile.

    ## @purpose  Extract models, budget, rpm, and metadata from a named profile.
    ## @io
    ##   - profile_name: str — profile name
    ##   - policy: LLMPolicy — loaded policy
    ##   - ⎋ dict — config with keys: models, budget (daily, monthly), rpm_limit, metadata
    ## @complexity O(1)
    """
    profile = policy.profiles[profile_name]
    budget = profile.budget
    config: ProfileConfig = {
        "models": list(profile.models),
        "budget": {
            "daily": budget.daily if budget.daily is not None else 0.0,
        },
        "rpm_limit": profile.rpm_limit,
        "metadata": dict(profile.metadata) if profile.metadata else {},
    }
    if budget.monthly is not None:
        config["budget"]["monthly"] = budget.monthly

    logger.log(
        logging.INFO,
        "[IMP:8][get_profile_config] Profile '%s': models=%s, budget=%s, rpm=%d",
        profile_name,
        config["models"],
        config["budget"],
        config["rpm_limit"],
    )
    return config


def apply_overrides(
    base_config: ProfileConfig,
    overrides: dict[str, object] | None,
) -> ProfileConfig:
    """Apply project-level overrides on top of the base profile config.

    ## @purpose  Deep-merge overrides into profile config. Overrides can include:
    ##           models, budget (daily, monthly), rpm_limit.
    ## @io
    ##   - base_config: dict — profile base config
    ##   - overrides: dict | None — project-specific overrides
    ##   - ⎋ dict — merged config
    ## @complexity O(1) — shallow merge with nested budget override
    ## @invariants
    ##   - overrides.models replaces base_config.models (not append)
    ##   - overrides.budget.daily replaces base_config.budget.daily
    ##   - overrides.rpm_limit replaces base_config.rpm_limit
    ##   - None overrides → no-op (returns deep copy of base)
    """
    if not overrides:
        logger.log(
            logging.DEBUG,
            "[IMP:7][apply_overrides] No overrides — returning base config as-is",
        )
        return deepcopy(base_config)

    merged = deepcopy(base_config)

    if "models" in overrides and overrides["models"] is not None:
        # W11: YAML-граница (object) → list[str] (модели — строки из SoT/overrides)
        merged["models"] = cast("list[str]", overrides["models"])
        logger.log(
            logging.DEBUG,
            "[IMP:7][apply_overrides] Override models: %s",
            merged["models"],
        )

    if "budget" in overrides and isinstance(overrides["budget"], dict):
        budget_ovr = cast("dict[str, object]", overrides["budget"])
        for key in ("daily", "monthly"):
            if key in budget_ovr and budget_ovr[key] is not None:
                merged.setdefault("budget", {})[key] = cast("float", budget_ovr[key])
                logger.log(
                    logging.DEBUG,
                    "[IMP:7][apply_overrides] Override budget.%s: %s",
                    key,
                    merged["budget"][key],
                )

    if "rpm_limit" in overrides and overrides["rpm_limit"] is not None:
        merged["rpm_limit"] = cast("int", overrides["rpm_limit"])
        logger.log(
            logging.DEBUG,
            "[IMP:7][apply_overrides] Override rpm_limit: %s",
            merged["rpm_limit"],
        )

    logger.log(
        logging.INFO,
        "[IMP:8][apply_overrides] Merged config: models=%s, budget=%s, rpm=%d",
        merged["models"],
        merged["budget"],
        merged["rpm_limit"],
    )
    return merged


# endregion PROFILE_RESOLUTION


# region KEY_PERSISTENCE


def get_default_persist_path() -> pathlib.Path:
    """Return the default path for the project keys JSON file.

    ## @purpose  Keys are persisted to PLATFORM_STATE_DIR (canonical: /var/lib/platform/.bootstrap)
    ##           or temp dir on dev (dir unwritable — WARN + fallback).
    ##           SOPS integration planned for Wave 6.
    ## @complexity O(1)
    ## @invariants
    ##   - plan 012 T12 (F-022): PLATFORM_STATE_DIR через deploy_paths.bootstrap_state_dir()
    ##     (литерал tempfile.gettempdir() как дефолт удалён — гейт run_paths_sole)
    ##   - dev-машина без PLATFORM_STATE_DIR: каноническая дира root-owned → mkdir падает →
    ##     WARN + tempdir (проверяемый OSError, не молчаливый перехват)
    """
    state_dir = bootstrap_state_dir()
    try:
        state_dir.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        logger.warning(
            "[IMP:7][get_default_persist_path] Canonical state dir %s not writable (%s) — dev fallback to tempdir",
            state_dir,
            exc,
        )
        state_dir = pathlib.Path(tempfile.gettempdir())
    return state_dir / "litellm-project-keys.json"


def _store_lock_path(persist_path: pathlib.Path) -> pathlib.Path:
    """Return the lock-file path guarding read-modify-write of the key store.

    ## @purpose  REF-0104: <store>.lock рядом со стором — сериализация
    ##           read-modify-write между конкурентными provision-прогонами.
    ## @complexity O(1)
    """
    return pathlib.Path(str(persist_path) + ".lock")


# region FUNC_load_key_store
## @purpose  Загрузка key-store с fail-fast на corruption (REF-0104 / DATA-902).
## @io       ⇥ persist_path → ⎋ dict[str, str] │ ⚡ PlatformError
## @complexity O(K)
## @invariants
##   - Отсутствующий файл → {} (легитимный первый запуск)
##   - Невалидный JSON / не-dict / не-str значения → PlatformError (НИКОГДА silent {})
def _load_key_store(persist_path: pathlib.Path) -> dict[str, str]:
    """Load the JSON key store; corrupt/unreadable store fails LOUD.

    ## @purpose  ⚠️ TRAP[BUG] · 2026-08-25 · P0 · Truncated store silently wiped ALL keys
    ##           · Symptom: OOM/crash во время open("w") обрезал store; следующий reader
    ##             глотал JSONDecodeError в {} и ЗАЛИВАЛ одно-ключевой store поверх всех —
    ##             потеря ВСЕХ virtual keys = mass 401 до ручного re-provision (DATA-902).
    ##           · Root: except (JSONDecodeError, OSError): store = {} — corruption
    ##             неотличима от «файла нет», overwrite-all был дефолтом.
    ##           · Fix: corruption → PlatformError; восстановление — только осознанное
    ##             удаление файла оператором (или restore), никогда молча из provisioner'а.
    ##           · Prevention: fail-fast контракт _load_key_store + FileLock от гонок +
    ##             atomic_write_json от truncate-окон; corruption-chain unit-тест существует:
    ##             tests/unit/test_llm_key_provisioner.py::test_corruption_chain_fail_loud
    ##             (QA C4/G2, DevPlan 14 T1.3).
    ## @io
    ##   - ⎋ dict[str, str] — project_name → virtual key token
    ##   - ⚡ PlatformError — файл есть, но невалиден (truncated/non-dict/non-str)
    ## @complexity O(K)
    """
    if not persist_path.exists():
        logger.log(
            logging.INFO,
            "[IMP:7][_load_key_store] Store does not exist yet: %s",
            persist_path,
        )
        return {}
    try:
        with persist_path.open(encoding="utf-8") as f:
            data = cast("object", json.load(f))
    except json.JSONDecodeError as e:
        msg = (
            f"LLM key store is CORRUPT (invalid JSON): {persist_path} ({e}). "
            f"Refusing to overwrite existing keys — repair or delete the file deliberately."
        )
        raise PlatformError(msg) from e
    except OSError as e:
        msg = f"LLM key store is unreadable: {persist_path} ({e})"
        raise PlatformError(msg) from e

    if not isinstance(data, dict):
        msg = f"LLM key store has unexpected shape (expected object, got {type(data).__name__}): {persist_path}"
        raise PlatformError(msg)

    store: dict[str, str] = {}
    for k, v in cast("dict[object, object]", data).items():
        if not isinstance(k, str) or not isinstance(v, str):
            msg = f"LLM key store has non-string entry ({k!r}: …): {persist_path}"
            raise PlatformError(msg)
        store[k] = v

    logger.log(
        logging.INFO,
        "[IMP:7][_load_key_store] Loaded %d entr(y/ies) from %s",
        len(store),
        persist_path,
    )
    return store


# endregion FUNC_load_key_store


def persist_project_key(
    project_name: str,
    key: str,
    persist_path: pathlib.Path | None = None,
) -> None:
    """Persist a generated virtual key to a JSON store.

    ## @purpose  Write key to a JSON file at persist_path. Creates the file
    ##           if it doesn't exist, otherwise merges with existing entries.
    ##           REF-0104: read-modify-write под FileLock(<store>.lock); corrupt
    ##           store → PlatformError (fail-loud, НИКОГДА overwrite-all).
    ##           SOPS integration planned for Wave 6.
    ## @io
    ##   - project_name: str — consumer name
    ##   - key: str — virtual key token
    ##   - persist_path: Path | None — path to JSON store (default: PLATFORM_STATE_DIR)
    ##   - ⚡ PlatformError — corrupt/unreadable store
    ##   - ⚡ FileLockError — lock не взят за _LOCK_TIMEOUT_SECONDS (fail-closed)
    ## @complexity O(K) — single file read/write
    ## @invariants
    ##   - File is valid JSON (dict of project_name → key)
    ##   - If file doesn't exist, it is created (mode 0600 via atomic_write_json)
    ##   - If project already exists in store, it is overwritten
    ##   - Конкурентные писатели сериализованы; corrupt-стор блокирует запись
    ## @rationale Ключи — единственная копия credentials проектов в сторе: потеря
    ##            необратима (нет backup-домена), поэтому запись честнее доступности.
    """
    if persist_path is None:
        persist_path = get_default_persist_path()

    with FileLock(_store_lock_path(persist_path), timeout=_LOCK_TIMEOUT_SECONDS):
        # Fail-fast load: corrupt store → PlatformError, НИКОГДА overwrite-all (REF-0104)
        store = _load_key_store(persist_path)

        store[project_name] = key
        logger.log(
            logging.CRITICAL,
            "[IMP:9][persist_project_key] Key persisted: project=%s, key=%s..., path=%s",
            project_name,
            key[:_KEY_PREVIEW_LEN] if len(key) > _KEY_PREVIEW_LEN else key,
            persist_path,
        )

        # REF-0007 (11-DevPlan Волна 1): канонический atomic_write_json(mode=0600) вместо
        # plain open("w") + chmod-после — temp создаётся 0600 (mkstemp-семантика), chmod до
        # replace: нет окна с world-readable plaintext-ключами в tmpdir; crash → cleanup temp.
        atomic_write_json(persist_path, cast("dict[str, object]", store), mode=0o600)

        logger.log(
            logging.INFO,
            "[IMP:8][persist_project_key] Store updated: %d entries at %s",
            len(store),
            persist_path,
        )


# endregion KEY_PERSISTENCE


# region KEY_MATCHING


def key_config_matches(
    key_info: KeyInfo,
    config: ProfileConfig,
) -> bool:
    """Check if an existing key's config matches the desired config.

    ## @purpose  Compare models, budget, and rpm_limit between existing key
    ##           info and desired config. Used for idempotency: skip if matching.
    ## @io
    ##   - key_info: dict — response from /key/info (LiteLLM key object)
    ##   - config: dict — desired config with models, budget, rpm_limit
    ##   - ⎋ bool — True if key matches desired config (idempotent skip)
    ## @complexity O(M) where M = number of models
    ## @invariants
    ##   - Models comparison is set-based (order-independent)
    ##   - Budget comparison is approximate (floats, within 0.001 tolerance)
    ##   - RPM limit must match exactly
    """
    # Compare models (order-independent)
    existing_models = set(key_info.get("models", []) or [])
    desired_models = set(config.get("models", []) or [])
    if existing_models != desired_models:
        logger.log(
            logging.DEBUG,
            "[IMP:7][key_config_matches] Models differ: existing=%s, desired=%s",
            existing_models,
            desired_models,
        )
        return False

    # Compare budget (approximate float comparison)
    existing_budget = key_info.get("max_budget", 0.0) or 0.0
    desired_budget = config.get("budget", {}).get("daily", 0.0) or 0.0
    if abs(existing_budget - desired_budget) > _BUDGET_EPSILON:
        logger.log(
            logging.DEBUG,
            "[IMP:7][key_config_matches] Budget differs: existing=%.4f, desired=%.4f",
            existing_budget,
            desired_budget,
        )
        return False

    # Compare RPM limit
    existing_rpm = key_info.get("rpm_limit", 0) or 0
    desired_rpm = config.get("rpm_limit", 0) or 0
    if existing_rpm != desired_rpm:
        logger.log(
            logging.DEBUG,
            "[IMP:7][key_config_matches] RPM limit differs: existing=%d, desired=%d",
            existing_rpm,
            desired_rpm,
        )
        return False

    logger.log(
        logging.CRITICAL,
        "[IMP:9][key_config_matches] Config MATCHES — idempotent skip eligible",
    )
    return True


# endregion KEY_MATCHING


# region PROVISION_CORE


def provision_all(
    master_key: str,
    base_url: str | None = None,
    policy_path: pathlib.Path | None = None,
    persist_path: pathlib.Path | None = None,
) -> dict[str, str]:
    """Provision virtual keys for all LLM consumers.

    ## @purpose  Main provisioning pipeline:
    ##   1. Load policy from policy.yaml
    ##   2. Create LiteLLMAdminClient
    ##   3. Discover consumers (projects + platform services)
    ##   4. For each consumer: resolve profile, check existing key, create/update/skip
    ##   5. Persist keys to JSON store
    ##   6. Return {consumer_name: api_key}
    ## @io
    ##   - master_key: str — LITELLM_MASTER_KEY for Admin API auth
    ##   - base_url: str | None — explicit LiteLLM base URL (None → resolve_base_url:
    ##     $LLM_BASE_URL/$LITELLM_BASE_URL → _DEFAULT_BASE_URL с DNS-check → loopback; F-10)
    ##   - policy_path: Path | None — path to policy.yaml (default: project default)
    ##   - persist_path: Path | None — path to key store JSON
    ##   - ⎋ dict[str, str] — {consumer_name: api_key} for all provisioned projects
    ## @complexity O(C * (R + M)) where C = consumers, R = rules, M = models comparison
    ## @invariants
    ##   - IDEMPOTENT: repeated calls with same config produce identical keys
    ##   - Consumer without 'llm' or with llm.enabled=false → skipped
    ##   - Profile always resolves to a valid, existing profile
    ##   - Every generated key is persisted via persist_project_key()
    ##   - DevPlan 16 T1.D: metadata['project']==consumer_name ВСЕГДА (merge-guard reserved);
    ##     весь проход find→update/generate→persist под store.lock (конкурентные дубли
    ##     структурно невозможны); любой терминальный failed → PlatformError (φ11 не done);
    ##     пустой токен листинга = not-found (стор пустым значением НЕ перезаписывается)
    """
    # F-10: None → resolve_base_url (explicit → env → default с DNS-check → loopback).
    # Явный str проходит as-is (CLI --base-url контракт, make provision-llm).
    resolved_base_url = resolve_base_url(base_url)
    logger.log(
        logging.CRITICAL,
        "[IMP:9][provision_all] Starting key provisioning — base_url=%s",
        resolved_base_url,
    )

    # Step 1: Resolve policy path
    if policy_path is None:
        policy_path = _PROJECT_ROOT / _DEFAULT_POLICY_REL_PATH
    logger.log(
        logging.INFO,
        "[IMP:7][provision_all] Policy path: %s",
        policy_path,
    )

    # Step 2: Load policy
    policy = LLMPolicy.from_yaml(str(policy_path))
    logger.log(
        logging.CRITICAL,
        "[IMP:9][provision_all] Policy loaded: %d profiles, %d aliases",
        len(policy.profiles),
        len(policy.aliases),
    )

    # Step 3: Create admin client
    client = LiteLLMAdminClient(base_url=resolved_base_url, master_key=master_key)

    # Step 4: Discover consumers
    projects = discover_projects()
    platform_consumers = get_platform_consumers()
    all_consumers = projects + platform_consumers
    logger.log(
        logging.CRITICAL,
        "[IMP:9][provision_all] Discovered %d consumers (%d projects + %d platform)",
        len(all_consumers),
        len(projects),
        len(platform_consumers),
    )

    # Step 5: Provision keys
    provisioned_keys: dict[str, str] = {}
    # QA C4 (DevPlan 14 T1.3): failed-consumer учёт; DevPlan 16 T1.D (P0-5): пост-цикл
    # PlatformError — φ11 обязан видеть провал фазы llm-keys (partial-словарь не «успех»).
    failed_consumers: list[str] = []
    # DevPlan 16 T1.D (P1-6): lock-scope — fetch-once + весь потребительский проход
    # (find → update/generate → persist) под store.lock; конкурентный второй прогон ждёт/
    # падает по таймауту лока → дубли структурно невозможны. FileLock реентрантен:
    # вложенные persist_project_key берут тот же путь без deadlock.
    # QA C4 (PERF-081): fetch-once — ОДИН list_keys() за прогон, lookup'ы — фильтр
    # поверх скачанного списка; после update/generate запись обновляется локально.

    if persist_path is None:
        persist_path = get_default_persist_path()

    with FileLock(_store_lock_path(persist_path), timeout=_LOCK_TIMEOUT_SECONDS):
        # plan 012 T12 (F-020/F-021, AC-c): list_keys — транспортный call-site ВНЕ per-consumer
        # try. LiteLLMTransportError здесь НЕ может быть «no key» (REF-0104) — listing-сбой
        # означает: мы не знаем существующие ключи → любой generate создал бы дубликаты.
        # Семантика: все enabled-потребители counted failed → PlatformError в summary.
        known_keys: list[KeyInfo]
        try:
            known_keys = client.list_keys()
        except LiteLLMTransportError as exc:
            listing_failed = [
                str(c.get("name", "unknown")) for c in all_consumers if (c.get("llm") or {}).get("enabled", False)
            ]
            failed_consumers.extend(listing_failed)
            logger.log(
                logging.WARNING,
                "[IMP:8][provision_all] list_keys TRANSPORT FAILURE: %s — %d enabled consumer(s) "
                "counted failed (generate NOT attempted — duplicate-key guard REF-0104)",
                exc,
                len(listing_failed),
            )
            logger.log(
                logging.CRITICAL,
                "[IMP:9][provision_all] Provisioning complete: %d keys provisioned, %d skipped, %d failed: %s",
                0,
                len(all_consumers) - len(listing_failed),
                len(failed_consumers),
                failed_consumers or "none",
            )
            listing_msg = (
                f"LLM Admin API listing failed (transport): {exc} — "
                "phase NOT done; generate suppressed to avoid duplicate keys"
            )
            raise PlatformError(listing_msg) from exc
        logger.log(
            logging.INFO,
            "[IMP:8][provision_all] Fetch-once index built: %d existing key(s) downloaded in 1 list_keys() call",
            len(known_keys),
        )

        for consumer in all_consumers:
            consumer_name = consumer.get("name", "unknown")

            # Skip disabled
            llm_config = consumer.get("llm", {})
            if not isinstance(llm_config, dict) or not llm_config.get("enabled", False):
                logger.log(
                    logging.INFO,
                    "[IMP:8][provision_all] SKIP '%s': llm not enabled",
                    consumer_name,
                )
                continue

            # Resolve profile
            profile_name = resolve_profile(consumer, policy)
            logger.log(
                logging.INFO,
                "[IMP:8][provision_all] Consumer '%s' → profile '%s'",
                consumer_name,
                profile_name,
            )

            # Get profile config + apply overrides
            base_config = get_profile_config(profile_name, policy)
            llm_overrides = llm_config.get("overrides") if isinstance(llm_config, dict) else None
            # W11: YAML-граница (object) → dict[str, object] (isinstance-сужение + cast)
            overrides: dict[str, object] | None = (
                cast("dict[str, object]", llm_overrides) if isinstance(llm_overrides, dict) else None
            )
            effective_config = apply_overrides(base_config, overrides)

            # Build metadata for the key
            key_metadata: dict[str, str] = {
                "project": consumer_name,
            }
            profile_metadata = effective_config.get("metadata", {})
            if isinstance(profile_metadata, dict):
                # DevPlan 16 T1.D (P0-5): merge-guard — профильные метаданные НЕ затирают
                # зарезервированный "project" (иначе lookup по project никогда не матчит
                # → GENERATE бюджетных дублей на каждом прогоне). Инвариант:
                # key_metadata["project"] == consumer_name всегда.
                for meta_key, meta_value in profile_metadata.items():
                    if not isinstance(meta_value, str):
                        continue
                    if meta_key in _RESERVED_METADATA_KEYS:
                        logger.log(
                            logging.WARNING,
                            "[IMP:8][provision_all] Reserved metadata key '%s' from profile "
                            "ignored for '%s' (invariant: metadata['project']==consumer)",
                            meta_key,
                            consumer_name,
                        )
                        continue
                    key_metadata[meta_key] = meta_value

            # Check existing key (QA C4 fetch-once: фильтр поверх одного list_keys(),
            # НИКАКИХ per-consumer пагинаций)
            existing_key = find_key_by_metadata(known_keys, project=consumer_name)

            existing_token = ""
            if existing_key and isinstance(existing_key, dict):
                existing_token = str(existing_key.get("key") or "")
                if not existing_token:
                    # DevPlan 16 T1.D (P1-8): пустой токен листинга = запись not-found;
                    # НЕ персистится поверх рабочего ключа стора
                    logger.log(
                        logging.WARNING,
                        "[IMP:8][provision_all] Listing for '%s' returned EMPTY token — "
                        "treating as not-found (store untouched by empty value)",
                        consumer_name,
                    )
                    existing_key = None

            if existing_key and isinstance(existing_key, dict):
                if key_config_matches(existing_key, effective_config):
                    # Idempotent: key exists with matching config → skip
                    logger.log(
                        logging.CRITICAL,
                        "[IMP:9][provision_all] IDEMPOTENT SKIP '%s': key exists with matching config",
                        consumer_name,
                    )
                    provisioned_keys[consumer_name] = existing_token
                    persist_project_key(consumer_name, existing_token, persist_path)
                    continue
                # Key exists but config differs → update
                logger.log(
                    logging.INFO,
                    "[IMP:8][provision_all] UPDATE '%s': key exists with different config",
                    consumer_name,
                )
                update_ok = False
                try:
                    client.update_key(
                        key=existing_token,
                        models=effective_config.get("models"),
                        max_budget=effective_config.get("budget", {}).get("daily"),
                        rpm_limit=effective_config.get("rpm_limit"),
                        metadata=key_metadata,
                    )
                    update_ok = True
                except (OSError, ConnectionError, TimeoutError, LiteLLMTransportError) as e:
                    # QA C4 (DevPlan 14 T1.3): fall-through-to-generate УДАЛЁН. Неудачный update
                    # оставляет живой ключ со СТАРОЙ конфигурацией; generate создал бы ВТОРОЙ
                    # budget-bearing ключ с тем же metadata (мина массовых дублей DATA-класса).
                    logger.log(
                        logging.WARNING,
                        "[IMP:8][provision_all] UPDATE FAILED '%s': %s — ключ сохранён со старой "
                        "конфигурацией; generate НЕ выполняется (запрет дублей ключей)",
                        consumer_name,
                        e,
                    )
                    failed_consumers.append(consumer_name)
                if not update_ok:
                    continue
                logger.log(
                    logging.CRITICAL,
                    "[IMP:9][provision_all] KEY UPDATED '%s': %s...",
                    consumer_name,
                    existing_token[:_KEY_PREVIEW_LEN] if len(existing_token) > _KEY_PREVIEW_LEN else existing_token,
                )
                provisioned_keys[consumer_name] = existing_token
                persist_project_key(consumer_name, existing_token, persist_path)
                # QA C4: локальное обновление индекса — консистентность в рамках прогона
                if isinstance(existing_key, dict):
                    budget_cfg = effective_config.get("budget")
                    budget_daily = cast("float", budget_cfg.get("daily", 0.0)) if isinstance(budget_cfg, dict) else 0.0
                    existing_key.update(
                        key=existing_token,
                        models=cast("list[str]", effective_config.get("models") or []),
                        max_budget=budget_daily,
                        rpm_limit=cast("int", effective_config.get("rpm_limit", 10)),
                        metadata=cast("dict[str, object]", key_metadata),
                    )
                continue

            # Key does not exist → generate
            logger.log(
                logging.INFO,
                "[IMP:8][provision_all] GENERATE '%s': no existing key found",
                consumer_name,
            )
            gen_ok = False
            gen_result: dict[str, object] = {}
            try:
                gen_raw: object = cast(
                    "object",
                    client.generate_key(
                        models=effective_config.get("models", []),
                        metadata=key_metadata,
                        max_budget=effective_config.get("budget", {}).get("daily", 0.0),
                        budget_duration="1d",
                        rpm_limit=effective_config.get("rpm_limit", 10),
                    ),
                )
                gen_result = cast("dict[str, object]", gen_raw)
                gen_ok = True
            except (OSError, ConnectionError, TimeoutError, LiteLLMTransportError) as e:
                logger.log(
                    logging.WARNING,
                    "[IMP:8][provision_all] GENERATE FAILED '%s': %s — фаза продолжает следующих потребителей",
                    consumer_name,
                    e,
                )
                failed_consumers.append(consumer_name)
            if not gen_ok:
                continue
            new_key = cast("str", gen_result.get("key", ""))
            if not new_key:
                # DevPlan 16 T1.D (P1-8): пустой токен ГЕНЕРАЦИИ = терминальный провал
                logger.log(
                    logging.WARNING,
                    "[IMP:8][provision_all] Generate returned EMPTY token for '%s' — counted as failed",
                    consumer_name,
                )
                failed_consumers.append(consumer_name)
                continue
            logger.log(
                logging.CRITICAL,
                "[IMP:9][provision_all] KEY GENERATED '%s': %s...",
                consumer_name,
                new_key[:_KEY_PREVIEW_LEN] if len(new_key) > _KEY_PREVIEW_LEN else new_key,
            )
            provisioned_keys[consumer_name] = new_key
            persist_project_key(consumer_name, new_key, persist_path)
            # QA C4: локальное пополнение индекса — консистентность в рамках прогона
            known_keys.append(cast("KeyInfo", cast("object", gen_result)))

    # Summary
    total_skipped = len(all_consumers) - len(provisioned_keys)
    logger.log(
        logging.CRITICAL,
        "[IMP:9][provision_all] Provisioning complete: %d keys provisioned, %d skipped, %d failed: %s",
        len(provisioned_keys),
        total_skipped,
        len(failed_consumers),
        failed_consumers or "none",
    )

    # DevPlan 16 T1.D (P0-5): честный failed — исключение вместо WARN-only сводки;
    # φ11 не фиксирует llm-keys done при проваленных ключах (инвариант exit-контракта).
    if failed_consumers:
        msg = (
            f"LLM key provisioning FAILED for {len(failed_consumers)} consumer(s): "
            f"{sorted(failed_consumers)} — фаза llm-keys НЕ завершена (partial-словарь не успех)"
        )
        raise PlatformError(msg)

    return provisioned_keys


# endregion PROVISION_CORE


# region FUNC__resolve_master_key
## @purpose  Резолв LITELLM_MASTER_KEY: CLI-флаг → env → secrets.env ноды (plan 012 T12 / F-020).
##           Файловый fallback закрывает deploy-context цепочку: provision-llm.sh вызывается
##           subprocess'ом из llm_provision.py (env-less) — ключ приходит из secrets.env,
##           а не из env-дерева вызова.
## @io       ⇥ cli_value: str | None, env: Mapping | None (DI) → ⎋ str (пустая при отсутствии)
## @complexity O(S) — чтение secrets.env при непустом env-фолбэке (только если CLI/env пусты)
## @invariants
##   - Приоритет: CLI > env > secrets_env_file() > "" (main печатает ошибку на "")
##   - secrets.env читается через канонический парсер shared/secrets_env_parser
##   - Пустая строка = источник отсутствует (не ошибка на этом уровне)
def _resolve_master_key(
    cli_value: str | None,
    env: Mapping[str, str] | None = None,
) -> str:
    source = os.environ if env is None else env
    if cli_value:
        logger.log(logging.INFO, "[IMP:8][_resolve_master_key] Using CLI --master-key")
        return cli_value
    env_key = str(source.get("LITELLM_MASTER_KEY") or "")
    if env_key:
        logger.log(logging.INFO, "[IMP:8][_resolve_master_key] Using LITELLM_MASTER_KEY env")
        return env_key
    secrets_path = secrets_env_file(source)
    if secrets_path.is_file():
        from core.internal.shared.secrets_env_parser import parse as parse_secrets_env

        secrets = parse_secrets_env(str(secrets_path), strict=False)
        file_key = str(secrets.get("LITELLM_MASTER_KEY") or "")
        if file_key:
            logger.log(
                logging.INFO,
                "[IMP:9][_resolve_master_key] LITELLM_MASTER_KEY resolved from %s (F-020)",
                secrets_path,
            )
            return file_key
        logger.log(
            logging.INFO,
            "[IMP:7][_resolve_master_key] %s exists but has no LITELLM_MASTER_KEY",
            secrets_path,
        )
    return ""


# endregion FUNC__resolve_master_key


# region FUNC__ensure_local_proxy_neutral
## @purpose  Host-run proxy-нейтральность (plan 012 T12 / F-022): когда base_url указывает
##           на локальный фасад (127.0.0.1/localhost/litellm), HTTP(S)_PROXY не должен
##           перехватывать loopback-трафик (httpx trust_env читает env-proxy и ломает
##           connect к 127.0.0.1:4000). Метод: setdefault NO_PROXY для локальных хостов;
##           явный CLI --no-proxy приоритетен.
## @io       ⇥ base_url: str, no_proxy: str | None (DI) → ⎋ None (мутирует os.environ)
## @complexity O(1)
## @invariants
##   - Применяется ТОЛЬКО когда host base_url — loopback/litellm (host-run на ноде/dev)
##   - NO_PROXY добавляется setdefault-семантикой (существующий не затирается)
def _ensure_local_proxy_neutral(base_url: str, no_proxy: str | None = None) -> None:
    local_hosts = {"127.0.0.1", "localhost", "::1", "litellm"}
    host = _extract_host(base_url)
    if host not in local_hosts:
        return
    current = no_proxy if no_proxy is not None else os.environ.get("NO_PROXY", "")
    merged = ",".join(part for part in (current, "127.0.0.1,localhost,::1,litellm") if part)
    os.environ["NO_PROXY"] = merged
    logger.log(
        logging.INFO,
        "[IMP:8][_ensure_local_proxy_neutral] Local facade %s — NO_PROXY=%s (F-022)",
        host,
        merged,
    )


# endregion FUNC__ensure_local_proxy_neutral


# region CLI


class CliArgs(argparse.Namespace):
    """Типизированный namespace CLI (W11): ТОЛЬКО аннотации без значений.

    ## @purpose  Значения НЕ задаются class-атрибутами — hasattr(namespace, dest)
    ##            перебивает parser-дефолты; поля заполняет parse_args(namespace=CliArgs()).
    """

    master_key: str | None  # pyright: ignore[reportUninitializedInstanceVariable] — W11 argparse fills (без class-value дефолтов)
    base_url: str | None  # pyright: ignore[reportUninitializedInstanceVariable] — F-10: default=None, резолв в main()
    policy: str | None  # pyright: ignore[reportUninitializedInstanceVariable] — W11 argparse fills (без class-value дефолтов)
    persist: str | None  # pyright: ignore[reportUninitializedInstanceVariable] — W11 argparse fills (без class-value дефолтов)


def _parse_args(argv: list[str] | None = None) -> CliArgs:
    """Parse CLI arguments for key provisioner.

    ## @purpose  Argument parser with env var fallback for master key.
    ## @complexity O(1)
    """
    parser = argparse.ArgumentParser(
        description="Provision LiteLLM virtual keys for all LLM consumers",
    )
    parser.add_argument(
        "--master-key",
        type=str,
        default=None,
        help="LITELLM_MASTER_KEY (default: $LITELLM_MASTER_KEY env var)",
    )
    parser.add_argument(
        "--base-url",
        type=str,
        # F-10: default=None — резолв в main() через resolve_base_url (explicit → env →
        # default с DNS-check → loopback). Прежний argparse-default (env/_DEFAULT_BASE_URL)
        # лишал resolver возможности различить «явно задано» и «дефолт».
        default=None,
        help=(
            "LiteLLM base URL (default: $LLM_BASE_URL/$LITELLM_BASE_URL → "
            f"{_DEFAULT_BASE_URL} с loopback-fallback, F-10)"
        ),
    )
    parser.add_argument(
        "--policy",
        type=str,
        default=None,
        help="Path to policy.yaml (default: core/internal/llm/policy.yaml)",
    )
    parser.add_argument(
        "--persist",
        type=str,
        default=None,
        help="Path to persist keys JSON (default: /var/tmp/litellm-project-keys.json)",
    )
    return parser.parse_args(argv, namespace=CliArgs())


# region FUNC__plw_body_main
## @purpose  Тело try-блока (PLW0717 extraction из main) — семантика except не меняется.
## @io       ⇥ args, master_key, persist_path, policy_path → ⎋ результат try-тела
## @complexity O(1) — извлечение управляющего потока
def _plw_body_main(
    args: CliArgs,
    master_key: str,
    persist_path: pathlib.Path | None,
    policy_path: pathlib.Path,
) -> None:
    keys = provision_all(
        master_key=master_key,
        base_url=args.base_url,
        policy_path=policy_path,
        persist_path=persist_path,
    )
    print(f"\n{'=' * 50}")
    print(f"LLM Key Provisioning Complete: {len(keys)} keys")
    print(f"{'=' * 50}")
    for consumer_name, api_key in sorted(keys.items()):
        masked = api_key[:_KEY_PREVIEW_LEN] + "..." if len(api_key) > _KEY_PREVIEW_LEN else api_key
        print(f"  {consumer_name}: {masked}")
    print(f"{'=' * 50}\n")
    logger.log(
        logging.CRITICAL,
        "[IMP:9][main] Provisioning completed successfully: %d keys",
        len(keys),
    )


# endregion FUNC__plw_body_main


def main(argv: list[str] | None = None) -> int:
    """CLI entry point for key_provisioner.py.

    ## @purpose  Parse args, provision keys, print summary, return exit code.
    ## @io
    ##   - argv: list[str] | None — CLI arguments (default: sys.argv[1:])
    ##   - ⎋ int — exit code: 0 success, 1 on error
    ## @complexity O(provision_all)
    """
    args = _parse_args(argv)

    # F-10 (P1): резолв base_url ДО использования — explicit --base-url сохраняется,
    # иначе env → default с DNS-check → loopback (φ11/deploy-context subprocess без --base-url).
    args.base_url = resolve_base_url(args.base_url)

    # Resolve master key: CLI arg → env → secrets.env (plan 012 T12 F-020)
    master_key = _resolve_master_key(args.master_key)
    if not master_key:
        logger.log(
            logging.CRITICAL,
            "[IMP:10][main] LITELLM_MASTER_KEY not provided — use --master-key, set env var, or add it to secrets.env",
        )
        print(
            "ERROR: LITELLM_MASTER_KEY is required (--master-key | LITELLM_MASTER_KEY env | secrets.env)",
            file=sys.stderr,
        )
        return 1

    # Resolve policy path
    policy_path = pathlib.Path(args.policy) if args.policy else (_PROJECT_ROOT / _DEFAULT_POLICY_REL_PATH)

    # Resolve persist path
    persist_path = pathlib.Path(args.persist) if args.persist else None

    # plan 012 T12 (F-022): host-run provision нейтрален к proxy для локальных фасадов
    _ensure_local_proxy_neutral(args.base_url)

    logger.log(
        logging.INFO,
        "[IMP:7][main] Key Provisioner started: base_url=%s, policy=%s",
        args.base_url,
        policy_path,
    )

    try:
        _plw_body_main(args, master_key, persist_path, policy_path)

    except PlatformError as e:
        logger.log(
            logging.CRITICAL,
            "[IMP:10][main] Provisioning failed with exit=%d: %s",
            e.exit_code,
            e,
        )
        return e.exit_code
    # ruff: ignore[BLE001] — top-level CLI handler for unknown exceptions
    except Exception as e:  # noqa: EXC001 — top-level CLI handler for unknown exceptions
        logger.log(
            logging.CRITICAL,
            "[IMP:10][main] Provisioning failed: %s: %s",
            type(e).__name__,
            e,
        )
        return 1
    else:
        return 0


if __name__ == "__main__":
    sys.exit(main())

# endregion CLI
