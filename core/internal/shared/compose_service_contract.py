#!/usr/bin/env python3
# GREP_SUMMARY: compose-service-contract, service-network-coverage, env-var-unresolved, db-consumed-not-declared, L1, static-analyzer, shared, K1, K3, provides, environment-scan, interpolation
# STRUCTURE: ▶ analyze_service_contracts(compose, env_keys, secret_names, needs_database, provides) → ○ iter env/args refs (${VAR} + $VAR, $$-escape) → ◇ coverage (PLATFORM_<SVC>_ ref ∩ networks ≠ ∅) → ◇ env-resolution (ref ∈ env_keys ∪ secret_names) → ◇ db-needs (PLATFORM_POSTGRES_* ⇔ needs.database) → ⊕ violations tuple → ⎋
# region MODULE_CONTRACT
## @purpose  ЕДИНСТВЕННЫЙ статический анализатор service-контрактов compose проекта (Plan 019
##           TASK-4, dual-mechanism ban §1.10): три L1-правила — service-network-coverage
##           (потребляемый платформенный сервис достижим: networks(сервиса) ∩
##           provides.networks(SoT) ≠ ∅), env-var-unresolved (каждый ${VAR} без дефолта резолвится
##           из .env.platform ∪ secret-definitions), db-consumed-not-declared (потребление
##           PLATFORM_POSTGRES_* требует needs.database в ai-platform.yaml). Один механизм для
##           двух рубежей: K3 verify_contracts (deploy, Plan 019 TASK-4) и K1 project-check
##           (push, Plan 019 TASK-5) — идентичный вердикт на одном compose (dual-mechanism = drift).
## @scope    core/internal/shared/ — потребители: core/internal/deploy/verify_contracts.py (K3),
##           core/internal/practices/check_project.py (K1, TASK-5). НЕ импортирует
##           bootstrap/deploy/* (shared — слой вниз, инвариант shared/AGENTS.md п.5).
##           Библиотека: exit-коды не нужны, main() нет, side-effects при импорте нет.
## @invariants
##   1. Правило (a) service-network-coverage: для каждой ${PLATFORM_<SVC>_*} ссылки в
##      environment/build.args сервиса: networks(сервиса) ∩ provides[SVC].networks ≠ ∅
##      (иначе L1 violation). networks сервиса — list[str] ИЛИ dict (ключи = имена).
##   2. Правило (b) env-var-unresolved: каждый ${VAR} БЕЗ дефолта (${VAR}, ${VAR:?err},
##      ${VAR?err}, $VAR) резолвится из env_keys ∪ secret_names; ${VAR:-def}/${VAR-def}
##      (дефолт) — пропуск; $$ — escape, пропуск. Сканируются environment (list "K=V" ИЛИ
##      dict — обе части) и build.args (list/dict); не-строки str() перед сканом.
##   3. Правило (c) db-consumed-not-declared: потребление ${PLATFORM_POSTGRES_DSN} (или bare
##      $PLATFORM_POSTGRES_DSN) при needs_database=False → violation; обратное направление
##      (declared без потребления) НЕ нарушает.
##   4. Resilience: compose не dict / services не dict / сервис не dict → пропуск
##      (verify_contracts сам флагает parse-fail). analyze_service_contracts НИКОГДА не кидает
##      на данных; load_provides кидает FileNotFoundError при отсутствии SoT (fail-fast).
##   5. load_env_keys: отсутствующий файл → frozenset() (проект может не иметь env).
##   6. Семантика сети — ПЕРЕСЕЧЕНИЕ, НЕ список обязательных сетей (см. @rationale).
##   7. Модуль не имеет side-effects при импорте (только logging.getLogger(__name__)).
## @rationale Q: почему контракт сетей = пересечение networks(svc) ∩ provides.networks(SoT), а не
##            список «обязательных сетей»? A: статический анализ не знает DNS-рантайм; SoT
##            platform-infra.yaml (provides-секция, DR-M4) уже канонизирован parity-гейтом (⊆, REF-0017);
##            пересечение — минимальная проверяемая форма того же контракта, без второго списка
##            сетей (knowledge dedup).
##            Q: почему один shared-анализатор, а не два чека? A: dual-mechanism = drift-ускоритель
##            (§1.10): K3 (verify_contracts) и K1 (project-check) обязаны давать идентичный вердикт
##            на одном compose — иначе push-рубеж и deploy-рубеж разойдутся; единственный механизм
##            в core/internal/shared/ (критерии размещения — shared/AGENTS.md).
## @changes  2026-08-31 · Plan 019 TASK-4 — создан (инцидент пилотов asi-group: proxy-net only +
##           ${DATABASE_URL} → pgbouncer/litellm недостижимы; K3-гейт был слеп к классу — F5)
# endregion MODULE_CONTRACT

from __future__ import annotations

import logging
import re
from collections.abc import Iterator, Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import cast

import yaml

from core.internal.shared.compose_profiles import resolve_infra_path

logger = logging.getLogger(__name__)

# ── Rule-id (contract_id K3/K1 — единственный источник имён) ──────────────────
RULE_SERVICE_NETWORK_COVERAGE: str = "service-network-coverage"
RULE_ENV_VAR_UNRESOLVED: str = "env-var-unresolved"
RULE_DB_CONSUMED_NOT_DECLARED: str = "db-consumed-not-declared"

# ── Compose interpolation regex (Plan 019 TASK-4) ──────────────────────────────
# ${NAME}, ${NAME:-def}, ${NAME-def}, ${NAME:?err}, ${NAME?err} — дефолт-присутствие
# определяется по группе оператора (:- или - = дефолт; :?/? = error-форма, требует резолва).
# ⚠️ TRAP[BUG] · 2026-08-31 · P1 · Plan 019 TASK-4: DevPlan-спека regex имела НЕ-захватывающую
# · группу оператора `(?::?[-?][^}]*)?` → m.group(2) кидал IndexError на КАЖДОМ вызове
# · (первый прогон гейта 4/4 failed)
# · Symptom: IndexError: no such group (group 2) в _extract_refs
# · Root: `(?:...)` — non-capturing; спека расходилась с использованием m.group(2)
# · Fix: `((:?[-?])[^}]*)?` — та же семантика матчинга, группа 2 захватывает оператор+хвост
# · Prevention: R5-negative тест test_gate_network_coverage_blocks_db_without_shared_db_net
# ·   исполняет _extract_refs на ${PLATFORM_LITELLM_URL:-...} — регрессия regex = RED
_BRACE_RE = re.compile(r"\$\{([A-Za-z_][A-Za-z0-9_]*)((:?[-?])[^}]*)?\}")
# bare $NAME — только вне brace-форм (после удаления brace-матчей, чтобы $VAR внутри
# ${..:-$VAR}-дефолта не ловился как самостоятельная ссылка)
_BARE_RE = re.compile(r"\$(?!\{)([A-Za-z_][A-Za-z0-9_]*)")
# $$ — compose-escape литерального $: `$${VAR}`/`$$VAR` НЕ интерполируются
_ESCAPE_RE = re.compile(r"\$\$")
# Потребляемый env-префикс postgres (правило c): любой PLATFORM_POSTGRES_* (DSN/URL/...)
_POSTGRES_PREFIX: str = "PLATFORM_POSTGRES_"


# region FUNC_ServiceContractViolation
## @purpose  Frozen-нарушение service-контракта: rule (одно из RULE_* констант), service (имя
##           сервиса compose), message (человекочитаемое описание). Потребитель (K3/K1) мапит в
##           свой формат findings (contract_id = rule).
## @io       ⇥ rule/service/message → ⎋ ServiceContractViolation
## @complexity O(1)
@dataclass(frozen=True)
class ServiceContractViolation:
    """Single service-contract violation (rule + service + message)."""

    rule: str
    service: str
    message: str


# endregion FUNC_ServiceContractViolation


# region FUNC_ServiceContractInput
## @purpose  Frozen-вход анализатора: parsed compose, env-ключи .env.platform, имена
##           secret-definitions.yaml, needs.database-флаг, provides (SoT platform-infra.yaml).
##           Явный бандл — оба потребителя (K3/K1) собирают входы одинаково.
## @io       ⇥ compose/env_keys/secret_names/needs_database/provides → ⎋ ServiceContractInput
## @complexity O(1)
@dataclass(frozen=True)
class ServiceContractInput:
    """Analyzer input bundle (compose + env keys + secret names + needs + provides SoT)."""

    compose: dict[str, object]
    env_keys: frozenset[str]
    secret_names: frozenset[str]
    needs_database: bool
    provides: dict[str, object]


# endregion FUNC_ServiceContractInput


# region FUNC_analyze_service_contracts
## @purpose  Прогон трёх L1-правил по compose проекта → кортеж нарушений. Единственная точка
##           вердиктов для K3/K1 (dual-mechanism ban §1.10). НИКОГДА не кидает на данных:
##           compose/services/сервис не dict → пропуск (parse-fail флагает verify_contracts сам).
## @io       ⇥ inp: ServiceContractInput → ⎋ tuple[ServiceContractViolation, ...]
## @complexity O(S * R * P) где S = сервисы, R = env-ссылки, P = provides-записи
## @invariants
##   - env-var-unresolved: только ссылки БЕЗ дефолта (${VAR:-def}/${VAR-def}/$$ — пропуск)
##   - service-network-coverage: только ${PLATFORM_<SVC>_*} ссылки; пустое пересечение → violation
##   - db-consumed-not-declared: PLATFORM_POSTGRES_* потребляется ∧ needs_database=False
def analyze_service_contracts(inp: ServiceContractInput) -> tuple[ServiceContractViolation, ...]:
    """Analyze compose service contracts → tuple of violations (never raises on data)."""
    compose = inp.compose
    if not isinstance(compose, dict):  # pyright: ignore[reportUnnecessaryIsInstance] — resilience-граница (спец: никогда не кидать на данных)
        return ()
    services = compose.get("services")
    if not isinstance(services, dict):
        return ()
    services = cast(dict[str, object], services)
    resolvable = inp.env_keys | inp.secret_names
    violations: list[ServiceContractViolation] = []
    counts: dict[str, int] = {"coverage": 0, "unresolved": 0, "db": 0}
    services_scanned = 0

    for svc_name, svc in services.items():
        if not isinstance(svc, dict):
            continue
        services_scanned += 1
        svc_typed = cast(dict[str, object], svc)
        svc_networks = _service_networks(svc_typed)
        for text in _iter_scan_strings(svc_typed):
            for name, has_default in _extract_refs(text):
                if not has_default and name not in resolvable:
                    counts["unresolved"] += 1
                    violations.append(
                        ServiceContractViolation(
                            RULE_ENV_VAR_UNRESOLVED,
                            svc_name,
                            f"env reference '${{{name}}}' без дефолта не резолвится из .env.platform ∪ "
                            "secret-definitions — интерполяция compose даст пустую строку "
                            "(инцидент пилотов asi-group: ${{DATABASE_URL}} → '', план 019 F3)",
                        )
                    )
                for ps_name, ps_cfg in inp.provides.items():
                    prefix = f"PLATFORM_{str(ps_name).upper()}_"
                    if not name.startswith(prefix):
                        continue
                    provided_nets = _provides_networks(ps_cfg)
                    if svc_networks.isdisjoint(provided_nets):
                        counts["coverage"] += 1
                        violations.append(
                            ServiceContractViolation(
                                RULE_SERVICE_NETWORK_COVERAGE,
                                svc_name,
                                f"consumes '{name}' (платформенный сервис '{ps_name}') но networks "
                                f"{sorted(svc_networks) or ['(none)']} ∩ provides.networks "
                                f"{sorted(provided_nets)} = ∅ — сервис недостижим "
                                "(SoT platform-infra#provides, DR-M4)",
                            )
                        )
                if name.startswith(_POSTGRES_PREFIX) and not inp.needs_database:
                    counts["db"] += 1
                    violations.append(
                        ServiceContractViolation(
                            RULE_DB_CONSUMED_NOT_DECLARED,
                            svc_name,
                            f"consumes '{name}' (postgres) но needs.database не объявлен в "
                            "ai-platform.yaml — роль/БД не провижинятся хук-ом postgres "
                            "(класс ***-DSN roadmap, план 019 F6)",
                        )
                    )

    logger.info(
        "[IMP:8][compose_service_contract][analyze] services=%d provides=%d resolvable-keys=%d",
        services_scanned,
        len(inp.provides),
        len(resolvable),
    )
    logger.info(
        "[IMP:9][compose_service_contract][verdict] coverage=%d env-unresolved=%d db-needs=%d total=%d",
        counts["coverage"],
        counts["unresolved"],
        counts["db"],
        len(violations),
    )
    return tuple(violations)


# endregion FUNC_analyze_service_contracts


# region FUNC__extract_refs
## @purpose  Извлечь интерполяционные ссылки из строки: (name, has_default) для ${...} форм и
##           bare $NAME. Порядок: (1) $$-escape удаляется (литеральный $ не интерполируется),
##           (2) brace-матчи ${NAME[op...]} → (name, has_default=op ∈ {:-,-}), (3) brace-матчи
##           удаляются из строки, (4) bare-скан по остатку (вне brace-форм — $VAR внутри
##           ${..:-$VAR}-дефолта не ловится как самостоятельная ссылка).
## @io       ⇥ text: str → ⎋ list[tuple[str, bool]] (name, has_default)
## @complexity O(T) где T = len(text)
def _extract_refs(text: str) -> list[tuple[str, bool]]:
    """Extract (name, has_default) interpolation references from a compose string."""
    refs: list[tuple[str, bool]] = []
    unescaped = _ESCAPE_RE.sub("", text)
    for m in _BRACE_RE.finditer(unescaped):
        op = m.group(2) or ""
        has_default = op.startswith((":-", "-"))
        refs.append((m.group(1), has_default))
    remainder = _BRACE_RE.sub("", unescaped)
    refs.extend((m.group(1), False) for m in _BARE_RE.finditer(remainder))
    return refs


# endregion FUNC__extract_refs


# region FUNC__iter_scan_strings
## @purpose  Собрать строки для интерполяционного скана сервиса: environment (dict-форма:
##           "K=V" — обе части; list-форма "K=V" целиком) и build.args (dict/list — то же).
##           Не-строковые значения (int/bool) — str() перед сканом (compose конвертирует).
## @io       ⇥ svc: dict → ⎋ Iterator[str]
## @complexity O(E) где E = число env/args записей
def _iter_scan_strings(svc: dict[str, object]) -> Iterator[str]:
    """Yield interpolation-scan strings from service environment/build.args (both forms)."""
    env = svc.get("environment")
    if isinstance(env, dict):
        for key, value in cast(dict[str, object], env).items():
            yield f"{key}={value}" if value is not None else str(key)
    elif isinstance(env, list):
        for item in cast(list[object], env):
            yield str(item)
    build = svc.get("build")
    if isinstance(build, dict):
        args = cast(dict[str, object], build).get("args")
        if isinstance(args, dict):
            for key, value in cast(dict[str, object], args).items():
                yield f"{key}={value}" if value is not None else str(key)
        elif isinstance(args, list):
            for item in cast(list[object], args):
                yield str(item)


# endregion FUNC__iter_scan_strings


# region FUNC__service_networks
## @purpose  Имена сетей сервиса: list[str] ИЛИ dict (ключи = имена сетей, compose long-form
##           с aliases). Неожиданная форма → пустое множество (fail-closed на правило coverage:
##           пустое пересечение = violation при наличии PLATFORM_* потребления).
## @io       ⇥ svc: dict → ⎋ set[str]
## @complexity O(N) где N = сети сервиса
def _service_networks(svc: dict[str, object]) -> set[str]:
    """Return service network names (list-form or dict-keys form)."""
    networks = svc.get("networks")
    if networks is None:
        return set()
    if isinstance(networks, dict):
        return {str(k) for k in cast(dict[str, object], networks)}
    if isinstance(networks, list):
        return {str(x) for x in cast(list[object], networks)}
    return set()


# endregion FUNC__service_networks


# region FUNC__provides_networks
## @purpose  Имена сетей платформенного сервиса из provides-записи (SoT platform-infra.yaml,
##           provides-секция).
##           Не-dict запись / не-list networks → пустое множество (consume-ссылка остаётся
##           непокрытой → coverage violation — fail-closed).
## @io       ⇥ ps_cfg: Any → ⎋ set[str]
## @complexity O(N) где N = сети provides
def _provides_networks(ps_cfg: object) -> set[str]:
    """Return provided service network names (platform-infra.yaml provides-секция)."""
    if not isinstance(ps_cfg, dict):
        return set()
    networks = cast(dict[str, object], ps_cfg).get("networks")
    if isinstance(networks, list):
        return {str(x) for x in cast(list[object], networks)}
    return set()


# endregion FUNC__provides_networks


# region FUNC_load_env_keys
## @purpose  Парсер .env-файла → frozenset имён ключей (KEY=..., комментарии/пустые skip,
##           split по первому =, strip). Отсутствующий файл → frozenset() — проект может не
##           иметь .env.platform (env-интерполяция пуста — честно, не silent-fail).
## @io       ⇥ path: Path | str → ⎋ frozenset[str]
## @complexity O(L) где L = строки файла
def load_env_keys(path: Path | str) -> frozenset[str]:
    """Parse .env-style file → frozenset of key names (missing/unreadable file → empty set)."""
    env_path = Path(path)
    if not env_path.is_file():
        logger.info("[IMP:7][compose_service_contract][env] file not found: %s — empty key set", env_path)
        return frozenset()
    try:
        text = env_path.read_text(encoding="utf-8")
    except OSError as exc:
        logger.warning("[IMP:7][compose_service_contract][env] read failed: %s (%s) — empty key set", env_path, exc)
        return frozenset()
    keys: set[str] = set()
    for line_raw in text.splitlines():
        line = line_raw.strip()
        if not line or line.startswith("#"):
            continue
        if "=" in line:
            key = line.split("=", 1)[0].strip()
            if key:
                keys.add(key)
    logger.info("[IMP:8][compose_service_contract][env] %d key(s) from %s", len(keys), env_path)
    return frozenset(keys)


# endregion FUNC_load_env_keys


# region FUNC_load_provides
## @purpose  Резолв SoT provides (platform-infra.yaml, provides-секция) через compose_profiles.
##           resolve_infra_path (РЕЮС path-логики — НЕ дублировать) → yaml.safe_load →
##           provides-секция. Файл не найден → FileNotFoundError (fail-fast: SoT всегда
##           доставляется с core/, DR-M4); 'provides' не dict → {} + громкий error-лог.
## @io       ⇥ env: Mapping | None (None = os.environ) → ⎋ dict[str, object] (provides-секция)
##           ⚡ FileNotFoundError — platform-infra.yaml отсутствует (resolve_infra_path → None)
## @complexity O(1) — single YAML load
def load_provides(env: Mapping[str, str] | None = None) -> dict[str, object]:
    """Load platform-infra.yaml provides-секция (SoT networks) — FileNotFoundError if SoT missing."""
    infra_path = resolve_infra_path(env)
    if infra_path is None:
        msg = (
            "[IMP:10][compose_service_contract] core/platform-infra.yaml not found — SoT provides "
            "обязателен (DR-M4); run `make generate-platform-env`"
        )
        raise FileNotFoundError(msg)
    with Path(infra_path).open(encoding="utf-8") as f:
        data = cast(dict[str, object] | None, yaml.safe_load(f))
    provides = (data or {}).get("provides", {})
    if not isinstance(provides, dict):
        logger.error(
            "[IMP:9][compose_service_contract][provides] 'provides' не dict в %s — empty (fail-open)",
            infra_path,
        )
        return {}
    provides_typed = cast(dict[str, object], provides)
    logger.info(
        "[IMP:9][compose_service_contract][provides] loaded %d service(s) from %s",
        len(provides_typed),
        infra_path,
    )
    return provides_typed


# endregion FUNC_load_provides
