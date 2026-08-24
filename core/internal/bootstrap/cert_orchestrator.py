#!/usr/bin/env python3
# GREP_SUMMARY: cert-orchestrator, bulk-restore, s3-cache, acme-issue, ssl, letsencrypt, idempotent, graceful-degradation, secrets_env_parser, DI, runner, facts
# STRUCTURE: ▶ ┌domains list┐ → ○ for each domain: s3 check → s3 download → (miss?) issue_cert → ⊕ CertResult → ⎋
# region MODULE_CONTRACT
## @purpose  Certificate orchestrator: bulk-restore SSL certs from S3 cache first,
##           then issue missing ones via acme.sh (issue_cert module).
##           Restore-first strategy minimizes acme.sh API calls and bootstrap latency.
## @scope    Called from state_machine.py deploy_context step (18.2 + 18.3).
##           Orchestrates the S3 SSL cache (check/download/upload) and issue_cert module.
## @invariants
##   1. Restore-first: try S3 cache before acme.sh issue
##   2. Idempotent: valid certs (>30 days) are skipped
##   3. Non-fatal: failure of one domain does NOT block others
##   4. Cache: successful issue → upload to S3 for future restores (handled by issue_cert.py --reloadcmd)
##   5. Graceful: S3 unavailable → fall back to acme.sh only
##   6. All subprocess calls have 120s timeout (s3) / 300s timeout (issue)
##   7. E1 (160): runner/facts/validity_path/cert_validity_fn/s3_cache/environ DI-параметры
##      (None = реальные вызовы/пути; поведение/exit-коды/идемпотентность НЕ изменены)
##   8. REF-0008 (SEC-0026): orchestrate_certs entry — fail-fast fqdn-валидация каждого домена
##      ДО side-effects; REF-0008 (BUG-0606): self-signed НЕ перезаписывает существующий
##      LE-сертификат; генерация self-signed алертится в Telegram (event=cert.self_signed)
## @rationale StatusReport 045: acme.sh DNS-01 issue is slow (60-120s per domain) and
##           can fail if DNS propagation is incomplete. S3 cache (bulk-restore) allows
##           instant cert restoration for previously-bootstrapped nodes, reducing
##           bootstrap time from minutes to seconds for cert phase.
## @changes  2026-07-22 | DevPlan 047 Phase 3 — Created cert orchestrator
## @changes  2026-07-23 | DevPlan 058 — ACME_CHALLENGE_MODE env var passthrough, DomainCertResult.challenge field
## @changes  2026-07-30 | DevPlan 086 — Migrated _source_secrets_env() from bash subprocess to shared secrets_env_parser.parse()
## @changes  2026-08-13 | DevPlan 160 E1 — +runner/facts/validity_path/cert_validity_fn/s3_cache/environ DI
## @changes  2026-08-15 | DevPlan 170 W6-D3 — orchestrate_certs (92 LOC/CC17) → 3 шага
##            (_collect_required_certs/_issue_or_reuse/_finalize_orchestration); _source_secrets_env →
##            secrets_env_apply.apply_secrets_env (изоляция мутации env, allowlist-контракт);
##            ручной yaml.safe_load node.yaml → NodeYaml-фасад (119 H1); _plw_body__source_secrets_env удалён
## @changes  2026-08-24 | REF-0008 (meta-refactoring В2) — fail-fast fqdn-gate на entry
##            orchestrate_certs (SEC-0026); _generate_self_signed: LE-preserve guard (BUG-0606)
##            + TG-alert cert.self_signed (FAIL-0300: fallback молчал ~76 дней)
# endregion MODULE_CONTRACT

from __future__ import annotations

import logging
import os
import pathlib
import subprocess
from collections.abc import Callable, Mapping
from dataclasses import asdict, dataclass, field
from typing import TYPE_CHECKING, Protocol, TypedDict, cast

from core.internal.bootstrap.secrets_env_apply import apply_secrets_env
from core.internal.config import platform_config
from core.internal.shared.deploy_paths import letsencrypt_live  # C7: единый резолвер /etc/letsencrypt/live
from core.internal.shared.env_facts import EnvironmentFacts, default_env_facts
from core.internal.shared.exceptions import (
    ConfigNotFoundError,
    ConfigParseError,
    ConfigValidationError,
    PlatformFatalError,
)
from core.internal.shared.node_yaml import NodeYaml  # 119 H1: фасад чтения node.yaml (замена yaml.safe_load)

# REF-0008: TG-alert source=self_signed (fallback молчал ~76 дней — FAIL-0300 leg)
from core.internal.shared.notifications import Notification, notify_event
from core.internal.shared.ssl_certs import (
    DEFAULT_OPENSSL_TIMEOUT,  # B5: канон openssl-таймаута (литерал 30 удалён)
    cert_get_subject,  # FL15 (DevPlan 125 T5): SAN/subject-разбор для wildcard-покрытия
    cert_is_le_issuer,  # REF-0008 (BUG-0606): self-signed не перезаписывает LE-сертификат
    cert_is_valid,  # C9: единая комбинация «cert валиден» (DevPlan 118 C9); _is_cert_valid удалён
    cert_subject_matches_domain,  # FL15 (DevPlan 125 T5): CN-матчинг direct/wildcard
    validate_cert_domain_fqdn,  # REF-0008 (SEC-0026): fail-fast fqdn на entry orchestrate_certs
)
from core.internal.shared.subprocess_io import CommandRunner

logger = logging.getLogger(__name__)

# ── Direct import of s3_ssl_cache (DevPlan 052 Phase 1) ──
# S3 cache — прямой Python-вызов (без subprocess).
# Eliminates subshell credential propagation bug — S3_* env vars are read
# directly by s3_ssl_cache functions from os.environ (no subshell).
# ⚠️ TRAP[BUG] 2026-08-03 · top-level `import s3_ssl_cache` ломался на VPS
# · Symptom: прод-бустрап φ7 — «s3_ssl_cache module not available» при работающем
#   boto3 (s3_ssl_cache.py сам использует ТОЛЬКО dotted core.internal импорты).
# · Root: top-level import требует bootstrap-директорию в sys.path; cli.py (VPS)
#   запускается без неё → ImportError → s3_ssl_cache=None → S3 cache выключен.
# · Fix: канонический dotted-импорт from core.internal.bootstrap import s3_ssl_cache.
try:
    from core.internal.bootstrap import s3_ssl_cache
except ImportError:
    s3_ssl_cache = None  # type: ignore[assignment]
    logger.warning("[IMP:7][cert_orchestrator] s3_ssl_cache module not available — S3 operations disabled")

# ── Provider registry (DevPlan 154 W1, вариант C): модульный реестр DNS-провайдеров ──
# Тот же graceful-паттерн, что у s3_ssl_cache: реестр недоступен (старый core на VPS) →
# поведение (префиксный фильтр WEBNAMES/S3_/PLATFORM_ + issue-cert.sh сам читает NODE_YAML).
# Типы CertProviderRegistry/ProviderConfig используются ТОЛЬКО в аннотациях (from __future__ import
# annotations → строки, runtime не вычисляются) — импортируются под TYPE_CHECKING, чтобы аннотации
# оставались валидными типами (reportInvalidTypeForm), а runtime graceful-fallback — только load_registry.
if TYPE_CHECKING:
    from core.internal.bootstrap.provider_registry import (
        CertProviderRegistry,
        ProviderConfig,
    )

try:
    from core.internal.bootstrap.provider_registry import load_registry
except ImportError:
    load_registry = None  # type: ignore[assignment]
    logger.warning("[IMP:7][cert_orchestrator] provider_registry not available — cert provider resolution")

# ── Префиксы для _source_secrets_env (когда реестр недоступен) ──
# 170 W6-D3: фильтрация делегирована в secrets_env_apply.apply_secrets_env(allowlist, prefixes)
_FALLBACK_SECRET_PREFIXES = ("WEBNAMES", "S3_", "PLATFORM_")

# ── Constants ──────────────────────────────────────────────────────────────
# W1-A1 (план 170): ISSUE_TIMEOUT=300 (дубль SoT) → BUILD_TIMEOUT (300) — длительная операция
# issue_cert (docker build + acme) использует каноническое 300s окно.
from core.internal.shared.timeouts import BUILD_TIMEOUT

# S3_TIMEOUT=120 — уникальное значение S3-домена (cache-операции cert_orchestrator), НЕ в
# SoT-наборе {10,15,30,60,120,180,300,600} канонизированном для docker/ssh (120 отсутствует —
# единственный 120 в этом домене). Остаётся модульной константой (TRAP ниже).
# 🧐 TRAP[DECISION] · 2026-08-14 · — · S3_TIMEOUT=120 — уникальное значение S3-домена
# · Rejected: канонизация в shared/timeouts · Reason: S3-операции вне docker/ssh/healthcheck
# ·   скоупа timeouts.py (гейт timeout_literals сканирует только docker/ssh-домен; cert_orchestrator
# ·   в allowlist гейта как HTTP/S3-домен); единственный потребитель — этот модуль
# · Rev: если появится второй S3-таймаут 120 — канонизировать в shared/timeouts
S3_TIMEOUT = 120  # seconds for S3 cache operations
ISSUE_TIMEOUT = BUILD_TIMEOUT  # seconds for issue_cert module (канон 300, W1-A1)
CERT_VALIDITY_PATH = str(letsencrypt_live())  # C7: единый резолвер shared/deploy_paths


# region PROTOCOL_S3Cache
class S3Cache(Protocol):
    """DI-протокол S3-кэша сертификатов (модуль s3_ssl_cache / тест-фейки).

    ## @purpose — Тип DI-параметра s3_cache вместо Any (W11-G3): структурный контракт
    ##            check/download/upload, реализуется s3_ssl_cache-модулем и fake-объектами тестов.
    ## @complexity — O(1) — декларация протокола
    """

    def check_cert(self, domain: str, s3_bucket: str) -> bool: ...

    def download_cert(self, domain: str, cert_dir: str, acme_home: str, s3_bucket: str) -> bool: ...

    def upload_cert(self, domain: str, cert_dir: str, acme_home: str, s3_bucket: str) -> bool: ...


# endregion PROTOCOL_S3Cache


# region TYPEDEF_OrchestrateDI
class _OrchestrateDI(TypedDict):
    """DI-пакет для **-spread в _issue_or_reuse (W11-G3: замена dict[str, Any]).

    ## @complexity — O(1)
    """

    runner: CommandRunner | None
    facts: EnvironmentFacts | None
    validity_path: str | None
    cert_validity_fn: Callable[[str], bool] | None
    s3_cache: S3Cache | None
    environ: Mapping[str, str]


# endregion TYPEDEF_OrchestrateDI


# region DATACLASSES


@dataclass
class DomainCertResult:
    """Result of cert orchestration for a single domain.

    ## @purpose — Track per-domain cert status, source (S3 or acme), and errors.
    ## @io — ⇥ constructor params → ⎋ serializable result
    ## @complexity — O(1)
    """

    domain: str
    status: str = "pending"  # restored | issued | skipped | failed
    source: str = ""  # s3 | acme | skip | none
    challenge: str = ""  # dns | http — which challenge type was used for issuance
    error: str | None = None

    def to_dict(self) -> dict[str, str]:
        """Serialize to JSON-compatible dict."""
        d = asdict(self)  # typeshed: dict[str, Any]
        if self.error is None:
            d.pop("error", None)
        return cast("dict[str, str]", d)  # W11-G3: asdict → Any; error=None уже исключён выше


@dataclass
class CertResult:
    """Aggregated result of cert orchestration across all domains.

    ## @purpose — Collect per-domain results and summary counts.
    ## @io — ⇥ domains list → ⎋ serializable result with per-domain breakdown
    ## @complexity — O(N) where N = number of domains
    """

    domains: dict[str, DomainCertResult] = field(default_factory=dict)
    restored: int = 0
    issued: int = 0
    skipped: int = 0
    failed: int = 0

    def add(self, result: DomainCertResult) -> None:
        """Add a per-domain result and increment summary counter."""
        self.domains[result.domain] = result
        if result.status == "restored":
            self.restored += 1
        elif result.status == "issued":
            self.issued += 1
        elif result.status == "skipped":
            self.skipped += 1
        elif result.status == "failed":
            self.failed += 1

    def to_dict(self) -> dict[str, object]:
        """Serialize to JSON-compatible dict."""
        return {
            "domains": {k: v.to_dict() for k, v in self.domains.items()},
            "summary": {
                "restored": self.restored,
                "issued": self.issued,
                "skipped": self.skipped,
                "failed": self.failed,
            },
        }


# endregion DATACLASSES


# region ORCHESTRATION


# region FUNC_orchestrate_certs
## @purpose — Orchestrate cert restoration + issuance for a list of domains.
##            Restore-first: try S3 cache, then fall back to acme.sh issue.
## @io — ⇥ domains: list[str], issue_cert_script: str, secrets_env: str,
##       migrate_cron: bool, node_yaml: str, runner: CommandRunner | None,
##       facts: EnvironmentFacts | None, validity_path: str | None,
##       cert_validity_fn: Callable | None, s3_cache: Any | None, environ: Mapping | None
##       → ⎋ CertResult
## @complexity — O(D * T) where D = domains, T = timeout per operation
## @invariants
##   - Each domain is processed independently (non-fatal on failure)
##   - Valid certs (>30 days, checked via S3 cache check) are skipped
##   - S3 restore failure → fall back to issue_cert
##   - All subprocess calls have timeout
## @changes 2026-08-13 | E1 (160): +runner/facts/validity_path/cert_validity_fn/s3_cache/environ
##            (тесты без monkeypatch os/subprocess/CERT_VALIDITY_PATH/cert_is_valid/s3_ssl_cache)
## @changes 2026-08-24 | REF-0008 (SEC-0026): fail-fast validate_cert_domain_fqdn на entry —
##            невалидный needs.domain (`../`-traversal/RCE) отклоняется ДО любых S3/issue
##            side-effects (ConfigValidationError, exit 4)
def orchestrate_certs(
    domains: list[str],
    issue_cert_script: str,
    secrets_env: str = "",
    migrate_cron: bool = False,
    node_yaml: str = "",
    *,
    runner: CommandRunner | None = None,
    facts: EnvironmentFacts | None = None,
    validity_path: str | None = None,
    cert_validity_fn: Callable[[str], bool] | None = None,
    s3_cache: S3Cache | None = None,
    environ: Mapping[str, str] | None = None,
) -> CertResult:
    """Restore from S3 first, issue missing (thin: collect → provider-ctx+secrets → issue/reuse → finalize)."""
    env: Mapping[str, str] = os.environ if environ is None else environ
    domains = _collect_required_certs(domains, env)
    if not domains:
        return CertResult()

    # ── REF-0008 fail-fast entry gate (SEC-0026): fqdn каждого домена ДО любых side-effects ──
    # Домен попадает в пути live/<domain>/, S3-keys и shell-строки reloadcmd под root —
    # `../`-домен = path traversal/RCE. Валидация на входе (sink'и дальше по конвейеру).
    for domain in domains:
        validate_cert_domain_fqdn(domain)
    logger.info("[IMP:9][cert_orchestrator] All %d domain(s) passed FQDN validation", len(domains))

    logger.info("[IMP:8][cert_orchestrator] Orchestrating certs for %d domains", len(domains))
    # ── Provider registry (154 W1): грузится один раз; per-domain резолв — в _issue_or_reuse ──
    registry, node_plugin, plugins_map = _load_provider_context(node_yaml, facts=facts, environ=env)
    # Source secrets.env if provided (для кредов DNS-провайдеров)
    if secrets_env and (facts or default_env_facts()).path_isfile(secrets_env):
        logger.info("[IMP:8][cert_orchestrator] Sourcing secrets.env: %s", secrets_env)
        _source_secrets_env(secrets_env, registry)

    # DI-проброс (E1/160) → _issue_or_reuse → _process_single_domain
    di: _OrchestrateDI = {
        "runner": runner,
        "facts": facts,
        "validity_path": validity_path,
        "cert_validity_fn": cert_validity_fn,
        "s3_cache": s3_cache,
        "environ": env,
    }
    result = _issue_or_reuse(domains, issue_cert_script, registry, node_plugin, plugins_map, **di)
    _finalize_orchestration(result, migrate_cron=migrate_cron)
    return result


# endregion FUNC_orchestrate_certs


# region FUNC_collect_required_certs
## @purpose  Собрать список доменов для оркестрации: PLATFORM_DOMAIN env fallback (T0.3, 048.P3).
##            Возвращает пустой список → orchestrate_certs завершается no-op (семантика 1:1).
## @io — ⇥ domains: list[str], env: Mapping[str, str] → ⎋ list[str]
## @complexity — O(1)
## @invariants
##   - Пустой domains + непустой PLATFORM_DOMAIN → [PLATFORM_DOMAIN]
##   - Пустой domains + пустой PLATFORM_DOMAIN → [] (skip-семантика сохраняется)
## @changes 2026-08-15 | 170 W6-D3 — выделен из orchestrate_certs (92 LOC/CC17 → 3 шага)
def _collect_required_certs(domains: list[str], env: Mapping[str, str]) -> list[str]:
    """Собрать домены: PLATFORM_DOMAIN env fallback + отсев пустого списка."""
    if not domains:
        pd = env.get("PLATFORM_DOMAIN", "").strip()
        if pd:
            domains = [pd]
            logger.info("[IMP:7][cert_orchestrator] Using PLATFORM_DOMAIN from env: %s", pd)
    if not domains:
        logger.info("[IMP:7][cert_orchestrator] No domains to orchestrate — skipping")
    return domains


# endregion FUNC_collect_required_certs


# region FUNC_issue_or_reuse
## @purpose  Цикл обработки доменов: per-domain provider-резолв (154 W1) + _process_single_domain
##            (restore-first → issue → self-signed). Skip-семантика 1:1: пустой домен пропускается,
##            неизвестный провайдер → fail-fast per-domain (TRAP 154), остальные продолжаются.
## @io — ⇥ domains: list[str], issue_cert_script: str, registry: CertProviderRegistry | None,
##       node_plugin: str, plugins_map: dict[str, str] | None,
##       runner/facts/validity_path/cert_validity_fn/s3_cache/environ (DI) → ⎋ CertResult
## @complexity — O(D * T) where D = domains, T = timeout per operation
## @invariants
##   - Каждый домен обрабатывается независимо (non-fatal on failure)
##   - Provider resolve: конфиг не задан → provider=None (issue сам читает NODE_YAML)
##   - Неизвестное имя провайдера → DomainCertResult(status="failed") — не тихий fallback (TRAP 154)
## @changes 2026-08-15 | 170 W6-D3 — цикл вынесен из orchestrate_certs (шаг 2 из 3)
def _issue_or_reuse(
    domains: list[str],
    issue_cert_script: str,
    registry: CertProviderRegistry | None,
    node_plugin: str,
    plugins_map: dict[str, str] | None,
    *,
    runner: CommandRunner | None = None,
    facts: EnvironmentFacts | None = None,
    validity_path: str | None = None,
    cert_validity_fn: Callable[[str], bool] | None = None,
    s3_cache: S3Cache | None = None,
    environ: Mapping[str, str] | None = None,
) -> CertResult:
    """Обработать каждый домен: restore-first → issue → self-signed (skip-семантика)."""
    env: Mapping[str, str] = os.environ if environ is None else environ
    result = CertResult()

    for domain in domains:
        if not domain:
            continue
        provider = None
        # ── Резолв провайдера (DevPlan 154 W1) ──
        # Конфиг НЕ задан (нет acme_dns_plugin/acme_dns_plugins в node.yaml) → фолбэк-путь:
        # provider=None → issue-cert.sh сам читает NODE_YAML (обратная совместимость, AC 154 W1).
        # Конфиг задан, но имя неизвестно → fail-fast per-domain (TRAP 154: не тихий fallback).
        if registry is not None and (node_plugin or plugins_map):
            try:
                provider = registry.resolve_provider(domain, node_plugin, plugins_map)
            except ConfigValidationError as e:
                # Fail-fast per-domain: неизвестный провайдер — НЕ тихий generic-fallback
                # (TRAP 154 W1); домен помечается failed, остальные продолжаются.
                logger.warning("[IMP:8][cert_orchestrator] %s — provider resolve failed: %s", domain, e)
                result.add(DomainCertResult(domain=domain, status="failed", source="none", error=str(e)))
                continue
        domain_result = _process_single_domain(
            domain,
            issue_cert_script,
            provider,
            registry,
            runner=runner,
            facts=facts,
            validity_path=validity_path,
            cert_validity_fn=cert_validity_fn,
            s3_cache=s3_cache,
            environ=env,
        )
        if domain_result is not None:
            result.add(domain_result)

    return result


# endregion FUNC_issue_or_reuse


# region FUNC_finalize_orchestration
## @purpose  Финализация оркестрации: установка/миграция cron (после любых обработанных доменов)
##            + summary-лог (семантика 1:1). Возвращает None — side-effect на cron + лог.
## @io — ⇥ result: CertResult, migrate_cron: bool → ⎋ None
## @complexity — O(1)
## @invariants
##   - cron ставится только если restored/issued/skipped > 0
##   - migrate_cron_if_needed вызывается только при migrate_cron=True (bootstrap init)
##   - summary-лог всегда (IMP:9) — LDD-контракт orchestrate_certs
## @changes 2026-08-15 | 170 W6-D3 — хвост orchestrate_certs вынесен в шаг 3 из 3
def _finalize_orchestration(result: CertResult, *, migrate_cron: bool) -> None:
    """Установить cron после обработки доменов + залогировать итог (skip-семантика 1:1)."""
    if result.restored > 0 or result.issued > 0 or result.skipped > 0:
        _install_cron()
        # ── Migrate old cron entries if requested (bootstrap init) ──
        if migrate_cron:
            migrate_cron_if_needed()

    logger.info(
        "[IMP:9][cert_orchestrator] Done: restored=%d issued=%d skipped=%d failed=%d",
        result.restored,
        result.issued,
        result.skipped,
        result.failed,
    )


# endregion FUNC_finalize_orchestration


# region FUNC_process_single_domain
## @purpose — Process a single domain: check validity, restore from S3, or issue via acme.sh.
##            Calls _upload_to_s3() on skip (disk present) and after successful issue.
## @io — ⇥ domain: str, issue_cert_script: str,
##       provider: ProviderConfig|None, registry: CertProviderRegistry|None,
##       runner/facts/validity_path/cert_validity_fn/s3_cache/environ (DI) → ⎋ DomainCertResult
## @complexity — O(T) where T = timeout per operation
## @invariants
##   - Step 1: Check if valid cert already exists on disk (skip + upload to S3)
##   - Step 2: Try S3 restore (check + download via direct import)
##   - Step 3: Fall back to issue_cert if S3 miss/unavailable (provider-driven env)
##   - After successful issue, upload to S3
##   - Non-fatal: any failure returns DomainCertResult(status="failed")
## @changes 2026-08-13 | E1 (160): +DI threading (facts/validity_path/cert_validity_fn/s3_cache/runner/environ)
def _process_single_domain(
    domain: str,
    issue_cert_script: str,
    provider: ProviderConfig | None = None,
    registry: CertProviderRegistry | None = None,
    *,
    runner: CommandRunner | None = None,
    facts: EnvironmentFacts | None = None,
    validity_path: str | None = None,
    cert_validity_fn: Callable[[str], bool] | None = None,
    s3_cache: S3Cache | None = None,
    environ: Mapping[str, str] | None = None,
    upload_fn: Callable[..., bool] | None = None,
) -> DomainCertResult:
    """Process a single domain through restore → issue pipeline.

    DI (W-H DevPlan 163): upload_fn=None → _upload_to_s3 (канон); тесты передают
    call-recording fake (0 патчей внутреннего хелпера).
    """
    logger.info("[IMP:8][cert_orchestrator] Processing domain: %s", domain)

    vpath = validity_path or CERT_VALIDITY_PATH
    env: Mapping[str, str] = os.environ if environ is None else environ
    facts_obj = facts or default_env_facts()
    is_valid_fn = cert_validity_fn if cert_validity_fn is not None else cert_is_valid

    # ── Step 1: Check if cert already valid on disk ──
    cert_path = os.path.join(vpath, domain, "fullchain.pem")
    if facts_obj.path_isfile(cert_path) and is_valid_fn(cert_path):  # C9: единая комбинация shared/ssl_certs
        logger.info("[IMP:9][cert_orchestrator] %s — valid cert on disk, uploading to S3", domain)
        (upload_fn if upload_fn is not None else _upload_to_s3)(
            domain, validity_path=vpath, s3_cache=s3_cache, environ=env
        )  # Always sync to S3 (052 §4.5)
        return DomainCertResult(domain=domain, status="skipped", source="disk_synced")

    # ── Step 2: Try S3 restore via direct import (no subprocess) ──
    cache = s3_ssl_cache if s3_cache is None else s3_cache
    if cache is not None:
        s3_result = _try_s3_restore(domain, validity_path=vpath, s3_cache=cache, environ=env, facts=facts_obj)
        if s3_result.status == "restored":
            return s3_result
        logger.info("[IMP:7][cert_orchestrator] %s — S3 miss/unavailable, falling back to issue", domain)
    else:
        logger.warning("[IMP:7][cert_orchestrator] s3_ssl_cache module not loaded — S3 restore unavailable")

    # ── Step 3: Fall back to issue_cert ──
    if facts_obj.path_isfile(issue_cert_script):
        result = _issue_cert(domain, issue_cert_script, provider, registry, runner=runner, environ=env)
        if result.status == "issued":
            (upload_fn if upload_fn is not None else _upload_to_s3)(
                domain, validity_path=vpath, s3_cache=s3_cache, environ=env
            )  # Upload after issue (052 §4.5)
            # ── FL15 (DevPlan 125 T5): покрытие домена после issue ──
            # issue-cert.sh SKIP'ает поддомены уже выпущенного wildcard'а с rc=0 →
            # «issued successfully» без сертификата live/<domain>/ → ложный alarm «Missing cert».
            # Проверяем реальное покрытие (direct | wildcard родителя); только отсутствие
            # покрытия → WARN (не alarm): INFO «covered by wildcard» — НЕ alarm (FL15).
            _log_post_issue_coverage(domain, validity_path=vpath, facts=facts_obj)
            return result
        # issue failed — fall through to self-signed
        logger.warning("[IMP:8][cert_orchestrator] %s — issue_cert failed, trying self-signed fallback", domain)
    else:
        logger.warning("[IMP:8][cert_orchestrator] %s — no issue_cert script, trying self-signed fallback", domain)

    # ── Step 4: Self-signed as last resort (DevPlan 053 F6) ──
    # Both S3 restore and acme.sh issue failed — generate self-signed
    # to prevent nginx crash-loop. Monitoring should alert on self_signed source.
    logger.warning(
        "[IMP:8][cert_orchestrator] %s — all issuance methods failed, generating self-signed fallback", domain
    )
    return _generate_self_signed(domain, validity_path=vpath, runner=runner)


# endregion FUNC_process_single_domain


# region FUNC_try_s3_restore
## @purpose — Try to restore a cert from S3 via s3_ssl_cache (direct import, no subprocess).
##            S3 cache — прямой Python-вызов (без subprocess).
##            Eliminates subshell credential propagation bug.
## @io — ⇥ domain: str, validity_path: str | None, s3_cache: Any | None,
##       environ: Mapping | None, facts → ⎋ DomainCertResult
## @complexity — O(T) where T = S3 round-trip time
## @invariants
##   - Step 1: s3_ssl_cache.check_cert(domain, s3_bucket) → bool
##   - Step 2: s3_ssl_cache.download_cert(domain, ...) → bool
##   - Returns status="restored" on success, status="pending" on miss
## @changes 2026-08-13 | E1 (160): +validity_path/s3_cache/environ/facts DI
# region FUNC__plw_body__try_s3_restore
## @purpose  Тело try-блока (PLW0717 extraction из _try_s3_restore) — семантика except не меняется.
## @io       ⇥ cache, domain, facts_obj, s3_bucket, vpath → ⎋ результат try-тела
## @complexity O(1) — извлечение управляющего потока
def _plw_body__try_s3_restore(
    cache: S3Cache,
    domain: str,
    facts_obj: EnvironmentFacts,
    s3_bucket: str,
    vpath: str,
) -> DomainCertResult:
    if not cache.check_cert(domain, s3_bucket):
        logger.info("[IMP:7][cert_orchestrator] %s — S3 cache miss", domain)
        return DomainCertResult(domain=domain, status="pending", source="s3")
    cert_dir = vpath
    acme_home = "/opt/acme.sh"
    if not cache.download_cert(domain, cert_dir, acme_home, s3_bucket):
        logger.warning("[IMP:7][cert_orchestrator] %s — S3 download failed", domain)
        return DomainCertResult(
            domain=domain,
            status="pending",
            source="s3",
            error="download failed",
        )
    cert_path = os.path.join(vpath, domain, "fullchain.pem")
    if facts_obj.path_isfile(cert_path):
        logger.info("[IMP:9][cert_orchestrator] %s — cert restored from S3", domain)
        return DomainCertResult(domain=domain, status="restored", source="s3")
    logger.warning("[IMP:7][cert_orchestrator] %s — S3 download OK but cert not on disk", domain)
    return DomainCertResult(
        domain=domain,
        status="pending",
        source="s3",
        error="download succeeded but cert file missing",
    )


# endregion FUNC__plw_body__try_s3_restore


def _try_s3_restore(
    domain: str,
    *,
    validity_path: str | None = None,
    s3_cache: S3Cache | None = None,
    environ: Mapping[str, str] | None = None,
    facts: EnvironmentFacts | None = None,
) -> DomainCertResult:
    """Try S3 check + download via s3_ssl_cache (direct import, no subprocess).

    ## @rationale DevPlan 052 Phase 1: Replace subprocess.run calls with direct
    ##            s3_ssl_cache function calls. S3_* env vars are read directly
    ##            from os.environ by s3_ssl_cache — no subshell credential loss.
    """
    cache = s3_ssl_cache if s3_cache is None else s3_cache
    env: Mapping[str, str] = os.environ if environ is None else environ
    if cache is None:
        logger.warning("[IMP:7][cert_orchestrator] s3_ssl_cache module not available — S3 restore disabled")
        return DomainCertResult(domain=domain, status="pending", source="s3")

    s3_bucket = env.get("S3_BUCKET", platform_config.default_s3_bucket_sentinel())
    if not s3_bucket:
        logger.warning("[IMP:7][cert_orchestrator] S3_BUCKET not set — S3 restore unavailable")
        return DomainCertResult(domain=domain, status="pending", source="s3")

    vpath = validity_path or CERT_VALIDITY_PATH
    facts_obj = facts or default_env_facts()
    try:
        # Step 1: Check S3 cache via direct import
        return _plw_body__try_s3_restore(cache, domain, facts_obj, s3_bucket, vpath)
    except (ConfigNotFoundError, ConfigParseError, PlatformFatalError, OSError) as e:
        logger.warning("[IMP:7][cert_orchestrator] %s — S3 operation failed: %s", domain, e)
        return DomainCertResult(domain=domain, status="pending", source="s3", error=f"{type(e).__name__}: {e}")


# endregion FUNC_try_s3_restore


# region FUNC_upload_to_s3
## @purpose — Upload cert files to S3 via s3_ssl_cache (direct import, no subprocess).
##            Called on skip (cert on disk) and after successful acme.sh issue.
##            Non-fatal: returns False on failure, never raises.
## @io — ⇥ domain: str, validity_path: str | None, s3_cache: Any | None,
##       environ: Mapping | None → ⎋ bool (True = upload succeeded)
## @complexity — O(N) where N = files to upload (~4)
## @invariants
##   - Returns False if S3_BUCKET not set (S3 not configured)
##   - Non-fatal: failure logs WARN, returns False
##   - Uses s3_ssl_cache.upload_cert() directly (same process, no subshell)
## @rationale DevPlan 052 §4.4: Guaranteed S3 upload on every cert path
##           (skip, restore, issue) prevents cert loss for platform domain.
## @changes 2026-08-13 | E1 (160): +validity_path/s3_cache/environ DI
def _upload_to_s3(
    domain: str,
    *,
    validity_path: str | None = None,
    s3_cache: S3Cache | None = None,
    environ: Mapping[str, str] | None = None,
) -> bool:
    """Upload cert to S3 via s3_ssl_cache (direct import)."""
    cache = s3_ssl_cache if s3_cache is None else s3_cache
    env: Mapping[str, str] = os.environ if environ is None else environ
    # ⚠️ TRAP[BUG] 2026-08-03 · NoneType.upload_cert (прод-бустрап φ7)
    # · Symptom: 'SSL provision failed (non-fatal): 'NoneType' object has no attribute
    #   'upload_cert'' — после успешного issue cert (bootstrap tronyx-vps run3).
    # · Root: guard s3_ssl_cache is None был только в try_s3_restore, НЕ в _upload_to_s3.
    # · Fix: ранний return False при недоступном s3_ssl_cache (S3 опциональна).
    if cache is None:
        logger.warning("[IMP:7][cert_orchestrator] %s — S3 upload skipped (module unavailable)", domain)
        return False
    s3_bucket = env.get("S3_BUCKET", platform_config.default_s3_bucket_sentinel())
    if not s3_bucket:
        return False
    try:
        return cache.upload_cert(domain, validity_path or CERT_VALIDITY_PATH, "/opt/acme.sh", s3_bucket)
    except (ConfigNotFoundError, ConfigParseError, PlatformFatalError, OSError) as e:
        logger.warning("[IMP:7][cert_orchestrator] %s — S3 upload failed: %s", domain, e)
        return False


# endregion FUNC_upload_to_s3


# region FUNC_issue_cert
## @purpose — Issue a cert via issue_cert module (acme.sh DNS-01/HTTP-01, DevPlan 164 W3.5-1).
##            Non-fatal: failure logs WARN and returns failed result.
## @io — ⇥ domain: str, issue_cert_script: str (путь .sh (fallback/тесты) | .py (модуль)),
##       provider: ProviderConfig|None, registry: CertProviderRegistry|None,
##       runner: CommandRunner | None, environ: Mapping | None → ⎋ DomainCertResult
## @complexity — O(T) where T = ISSUE_TIMEOUT
## @invariants
##   - Sets PLATFORM_DOMAIN env var for the domain being issued
##   - provider задан → PLATFORM_ACME_DNS_PLUGIN={plugin}, ACME_CHALLENGE_MODE (registry-driven,
##     http01-провайдер принудительно http), env-креды — строгий allowlist (provider_env)
##   - provider=None (fallback) → env НЕ задаётся: issue_cert сам читает NODE_YAML (обратная совместимость)
##   - Диспетч команды (W3.5-1): issue_cert_script заканчивается .sh → ["bash", script] (fallback/тесты);
##     иначе → ["python3", "-m", "core.internal.bootstrap.issue_cert"] + PYTHONPATH repo-root
##   - issue_cert handles idempotency internally (skips if cert exists)
##   - Non-fatal: failure returns status="failed"
## ⚠️ TRAP[DECISION] · 2026-08-14 · — · issue-cert.sh → issue_cert.py (DevPlan 164 W3.5-1 S8)
## · Rejected: оставить shell executor (708 LOC keep-Rev 2027-02)
## · Reason: Rev снят решением S8 — acme.sh стабилен >6 мес; Python-порт даёт тестируемость
## ·   (DNS-01 vs HTTP-01, shred-протокол, retry) и удаляет 708 LOC shell. Фасад не нужен —
## ·   cert_orchestrator вызывает модуль напрямую (subprocess python3 -m).
## · Rev: если issue_cert.py начнёт дублировать оркестрацию cert_orchestrator — пересмотреть границу.
## @changes 2026-08-13 | E1 (160): +runner/environ DI (subprocess → runner; env через параметр)
## @changes 2026-08-14 | W3.5-1 (164): bash issue-cert.sh → python3 -m core.internal.bootstrap.issue_cert
# region FUNC__plw_body__issue_cert
## @purpose  Тело try-блока (PLW0717 extraction из _issue_cert) — семантика except не меняется.
## @io       ⇥ challenge_mode, domain, issue_cert_script, issue_env, runner → ⎋ результат try-тела
## @complexity O(1) — извлечение управляющего потока
def _plw_body__issue_cert(
    challenge_mode: str,
    domain: str,
    issue_cert_script: str,
    issue_env: dict[str, str],
    runner: CommandRunner | None,
) -> DomainCertResult:
    # ── W3.5-1 (164): диспетч команды — bash для .sh (тесты/старые ноды),
    # ── python3 -m для нового модуля (канон: python3 -m core.internal.bootstrap.issue_cert).
    # ── PYTHONPATH repo-root обязателен (модуль импортирует core.internal.* — канон issue-cert.sh:43).
    if issue_cert_script.endswith(".sh"):
        cmd = ["bash", issue_cert_script]
    else:
        cmd = ["python3", "-m", "core.internal.bootstrap.issue_cert"]
        root = str(pathlib.Path(__file__).resolve().parents[3])
        issue_env = dict(issue_env)
        existing_pp = issue_env.get("PYTHONPATH", "")
        issue_env["PYTHONPATH"] = f"{root}:{existing_pp}" if existing_pp else root
    if runner is None:
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=ISSUE_TIMEOUT,
            env=issue_env,
            check=False,
        )
    else:
        # 🧐 TRAP[DI-SEAM] · 2026-08-14 · — · env пробрасывается в runner-канал issue_cert
        # · Rejected: прямой вызов subprocess.run (тест не мог бы наблюдать allowlist-env)
        # · Reason: runner-ветка теряла issue_env (креды провайдера не доходили до issue_cert
        # ·   при runner-канале — дыра allowlist-контракта 154 W1); env= — единственный способ
        # ·   тестировать env-контракт через fake runner (0 monkeypatch)
        # · Rev: если CommandRunner-протокол получит официальный env-параметр — синхронизировать
        result = cast(
            "subprocess.CompletedProcess[str]",
            runner.run(cmd, timeout=ISSUE_TIMEOUT, check=False, env=issue_env),  # pyright: ignore[reportCallIssue] — env-канал DI-сима (TRAP[DI-SEAM] выше): CommandRunner-протокол без env-параметра, тест-фейки его принимают
        )  # W11-G3: ignored reportCallIssue → Unknown; каст к CompletedProcess[str] (subprocess.run-ветка)
    if result.returncode == 0:
        logger.info("[IMP:9][cert_orchestrator] %s — cert issued successfully (challenge=%s)", domain, challenge_mode)
        return DomainCertResult(
            domain=domain,
            status="issued",
            source="acme",
            challenge=challenge_mode,
        )
    logger.warning(
        "[IMP:7][cert_orchestrator] %s — issue_cert failed (exit=%d): %s",
        domain,
        result.returncode,
        result.stderr.strip()[:200] if result.stderr else "unknown",
    )
    return DomainCertResult(
        domain=domain,
        status="failed",
        source="acme",
        challenge=challenge_mode,
        error=result.stderr.strip()[:200] if result.stderr else f"exit={result.returncode}",
    )


# endregion FUNC__plw_body__issue_cert


def _issue_cert(
    domain: str,
    issue_cert_script: str,
    provider: ProviderConfig | None = None,
    registry: CertProviderRegistry | None = None,
    *,
    runner: CommandRunner | None = None,
    environ: Mapping[str, str] | None = None,
) -> DomainCertResult:
    """Issue cert via issue_cert module. Returns issued or failed result."""
    env: Mapping[str, str] = os.environ if environ is None else environ
    challenge_mode = env.get("ACME_CHALLENGE_MODE", "dns")
    logger.info("[IMP:9][cert_orchestrator] %s — issuing via acme.sh (challenge=%s)", domain, challenge_mode)
    # Set env for issue_cert (it reads PLATFORM_DOMAIN + ACME_CHALLENGE_MODE)
    issue_env = dict(env)
    issue_env["PLATFORM_DOMAIN"] = domain
    # ── Provider-driven env (DevPlan 154 W1): реестр — единственный источник плагина ──
    # provider=None (fallback/реестр недоступен) → НЕ задаём PLATFORM_ACME_DNS_PLUGIN —
    # issue-cert.sh сам прочитает NODE_YAML (прежний путь, обратная совместимость).
    if provider is not None and registry is not None:
        plugin_name = provider.plugin or provider.name
        challenge = registry.challenge_mode(provider, challenge_mode)
        issue_env["PLATFORM_ACME_DNS_PLUGIN"] = plugin_name
        issue_env["ACME_CHALLENGE_MODE"] = challenge
        # Allowlist-контракт (инвариант 3 provider_registry): креды ДРУГИХ провайдеров
        # не уходят в env issue-cert.sh (os.environ.copy() содержит ВСЕ sourced-переменные,
        # включая WEBNAMES_API_KEY при issue через regru). S3_/PLATFORM_ сохраняются —
        # reloadcmd (s3_ssl_cache upload) и env_shared потребители от них зависят.
        for cred_name in registry.all_cred_names():
            if cred_name not in provider.creds:
                issue_env.pop(cred_name, None)
        # Allowlist-креды провайдера (инвариант 3 provider_registry): ТОЛЬКО provider.creds
        issue_env.update(registry.provider_env(provider, dict(env)))
        logger.info(
            "[IMP:9][cert_orchestrator] %s — provider=%s plugin=%s challenge=%s (registry-driven)",
            domain,
            provider.name,
            plugin_name,
            challenge,
        )
    else:
        issue_env["ACME_CHALLENGE_MODE"] = challenge_mode
        logger.info("[IMP:8][cert_orchestrator] %s — issue path (no registry) — issue-cert.sh reads NODE_YAML", domain)
    try:
        return _plw_body__issue_cert(challenge_mode, domain, issue_cert_script, issue_env, runner)
    except subprocess.TimeoutExpired:
        logger.warning("[IMP:7][cert_orchestrator] %s — issue_cert timed out", domain)
        return DomainCertResult(
            domain=domain, status="failed", source="acme", challenge=challenge_mode, error="timeout"
        )
    except FileNotFoundError as e:
        logger.warning("[IMP:7][cert_orchestrator] %s — issue_cert error: %s", domain, e)
        return DomainCertResult(
            domain=domain, status="failed", source="acme", challenge=challenge_mode, error=f"{type(e).__name__}: {e}"
        )


# endregion FUNC_issue_cert


# region FUNC_generate_self_signed
## @purpose — Generate self-signed certificate as last-resort fallback (F6).
##            Called when BOTH S3 restore and acme.sh issue fail (e.g., DNS API down,
##            no credentials). Self-signed cert allows nginx to start (avoids crash-loop),
##            but browsers will show security warning. Valid 90 days.
##            REF-0008: (a) существующий LE-сертификат НЕ перезаписывается (BUG-0606 —
##            self-signed fallback затирал валидный LE-серт при ложном «invalid»);
##            (b) генерация алертится в Telegram event=cert.self_signed (ранее ~76 дней тишины).
## @io — ⇥ domain: str, validity_path: str | None, runner: CommandRunner | None,
##       environ: Mapping | None (TG-alert env), send_fn (DI транспорта),
##       cert_is_le_fn: Callable[[str], bool] | None (DI issuer-guard; None → канон)
##       → ⎋ DomainCertResult
## @complexity — O(1) + openssl subprocess
## @invariants
##   - Generates 2048-bit RSA key + self-signed x509 cert valid 90 days
##   - LE cert на диске → generation ОТКЛОНЯЕТСЯ (failed/self_signed, файл не тронут)
##   - Non-fatal: returns failed result on error
##   - Logs WARN on success (must be replaced with real cert) + TG alert (non-blocking)
##   - Sets proper file permissions (key=0600, cert=0644)
## @changes 2026-08-13 | E1 (160): +validity_path/runner DI
## @changes 2026-08-24 | REF-0008: +LE-preserve guard (BUG-0606) + TG-alert + environ/send_fn DI
# region FUNC__plw_body__generate_self_signed
## @purpose  Тело try-блока (PLW0717 extraction из _generate_self_signed) — семантика except не меняется.
## @io       ⇥ cert_path, domain, key_path, runner → ⎋ результат try-тела
## @complexity O(1) — извлечение управляющего потока
def _plw_body__generate_self_signed(
    cert_path: str,
    domain: str,
    key_path: str,
    runner: CommandRunner | None,
) -> DomainCertResult:
    if runner is None:
        subprocess.run(
            ["openssl", "genrsa", "-out", key_path, "2048"],
            capture_output=True,
            timeout=DEFAULT_OPENSSL_TIMEOUT,
            check=True,
        )
    else:
        runner.run(
            ["openssl", "genrsa", "-out", key_path, "2048"],
            timeout=DEFAULT_OPENSSL_TIMEOUT,
            check=True,
        )
    os.chmod(key_path, 0o600)
    if runner is None:
        subprocess.run(
            [
                "openssl",
                "req",
                "-new",
                "-x509",
                "-key",
                key_path,
                "-out",
                cert_path,
                "-days",
                "90",
                "-subj",
                f"/CN={domain}",
            ],
            capture_output=True,
            timeout=DEFAULT_OPENSSL_TIMEOUT,
            check=True,
        )
    else:
        runner.run(
            [
                "openssl",
                "req",
                "-new",
                "-x509",
                "-key",
                key_path,
                "-out",
                cert_path,
                "-days",
                "90",
                "-subj",
                f"/CN={domain}",
            ],
            timeout=DEFAULT_OPENSSL_TIMEOUT,
            check=True,
        )
    os.chmod(cert_path, 0o644)
    logger.warning(
        "[IMP:7][cert_orchestrator] %s — SELF-SIGNED cert generated (browsers will warn). "
        "Fix: ensure DNS-01 credentials in secrets.env or wait for acme.sh retry.",
        domain,
    )
    return DomainCertResult(domain=domain, status="issued", source="self_signed")


# endregion FUNC__plw_body__generate_self_signed


def _generate_self_signed(
    domain: str,
    *,
    validity_path: str | None = None,
    runner: CommandRunner | None = None,
    environ: Mapping[str, str] | None = None,
    send_fn: Callable[..., bool] | None = None,
    cert_is_le_fn: Callable[[str], bool] | None = None,
) -> DomainCertResult:
    """Generate self-signed certificate as last-resort fallback (LE-preserve + TG-alert, REF-0008).

    ## @purpose — Disaster recovery: keep nginx running when cert issuance fails.
    ## @rationale F6: self-signed cert allows nginx to start (avoids crash-loop),
    ##            but monitoring should alert on self_signed source.
    """
    vpath = validity_path or CERT_VALIDITY_PATH
    cert_dir = os.path.join(vpath, domain)
    os.makedirs(cert_dir, exist_ok=True)

    key_path = os.path.join(cert_dir, "privkey.pem")
    cert_path = os.path.join(cert_dir, "fullchain.pem")

    # ── REF-0008 / BUG-0606: self-signed НЕ перезаписывает существующий LE-сертификат ──
    # Ложный «invalid» вердикт (например, transient openssl failure upstream) не должен
    # затирать валидный LE-серт self-signed'ом — восстановление LE-состояния дороже.
    le_issuer_fn = cert_is_le_fn if cert_is_le_fn is not None else cert_is_le_issuer
    if pathlib.Path(cert_path).is_file() and le_issuer_fn(cert_path):
        logger.warning(
            "[IMP:7][cert_orchestrator] %s — REFUSING self-signed overwrite: valid LE certificate exists "
            "(BUG-0606 guard); fix issuance instead",
            domain,
        )
        return DomainCertResult(
            domain=domain,
            status="failed",
            source="self_signed",
            error="existing LE certificate preserved — refusing self-signed overwrite",
        )

    try:
        # Generate RSA private key (B5: канон DEFAULT_OPENSSL_TIMEOUT — литерал 30 удалён)
        result = _plw_body__generate_self_signed(cert_path, domain, key_path, runner)
    except (OSError, FileNotFoundError, subprocess.CalledProcessError) as e:
        logger.warning("[IMP:7][cert_orchestrator] %s — self-signed generation failed: %s", domain, e)
        return DomainCertResult(domain=domain, status="failed", source="none", error=f"{type(e).__name__}: {e}")
    else:
        if result.status == "issued" and result.source == "self_signed":
            _notify_self_signed(domain, environ=environ, send_fn=send_fn)
        return result


# endregion FUNC_generate_self_signed


# region FUNC_notify_self_signed
## @purpose  TG-alert при генерации self-signed fallback (REF-0008, FAIL-0300 leg): единственный
##           сигнал «все методы выпуска провалились» — ранее fallback был полностью беззвучен
##           (~76 дней тишины). Non-blocking: alert никогда не ломает bootstrap φ7.
## @io       ⇥ domain: str, environ: Mapping | None (env для resolve_chat_id),
##              send_fn: Callable | None (DI транспорта; None → канон telegram_notifier) → ⎋ None
## @complexity O(1) + 1 HTTP POST (30s timeout, в notify_event)
## @invariants
##   - severity=warning (не critical: сервис UP на self-signed, но требует замены)
##   - event=cert.self_signed — throttle/dedup ключ notify_event (fingerprint=message)
##   - Никогда не raise: исключение → IMP:7 WARN (bootstrap продолжается)
def _notify_self_signed(
    domain: str,
    *,
    environ: Mapping[str, str] | None = None,
    send_fn: Callable[..., bool] | None = None,
) -> None:
    """Send Telegram alert for self-signed fallback generation (non-blocking, REF-0008)."""
    try:
        notify_event(
            Notification(
                severity="warning",
                context="cert",
                event="cert.self_signed",
                message=f"Self-signed certificate generated for {domain} — browsers will warn until real cert issued",
                details=["S3 restore and acme.sh issue both failed for this domain"],
                action=(
                    "Fix DNS-provider credentials in secrets.env or ACME failure, then re-run make converge NODE=<node>"
                ),
            ),
            env=environ,
            send_fn=send_fn,
        )
        logger.info("[IMP:8][cert_orchestrator] %s — self_signed TG alert dispatched", domain)
    # ruff: ignore[blind-except] — alert non-blocking: bootstrap φ7 важнее доставки уведомления
    except Exception as e:  # noqa: EXC — DEPLOY_BEST_EFFORT: доставка алерта никогда не блокирует bootstrap
        logger.warning("[IMP:7][cert_orchestrator] %s — self_signed TG alert failed (non-fatal): %s", domain, e)


# endregion FUNC_notify_self_signed


# region FUNC_install_cron
## @purpose — Lazy facade for cron_installer.install_acme_cron (DevPlan 117 G T58.4).
## @io — ⇥ acme_home: str → ⎋ bool (True = cron installed or already present)
## @complexity — O(1) + delegate
## @invariants
##   - Includes --renew-hook to upload certs to S3 after each renewal
##   - Non-fatal: failure logs WARN, returns False
##   - Idempotent: no-op if cron entry already has s3_ssl_cache reference
def _install_cron(acme_home: str = "/opt/acme.sh") -> bool:
    """Install acme.sh --install-cronjob + --renew-hook with S3 upload."""
    from core.internal.bootstrap.cron_installer import install_acme_cron as _impl

    return _impl(acme_home)


# endregion FUNC_install_cron


# region FUNC_migrate_cron_if_needed
## @purpose — Lazy facade for cron_installer.migrate_acme_cron_if_needed (DevPlan 117 G T58.4).
## @io — ⇥ acme_home: str → ⎋ bool (True = migration succeeded or was not needed)
## @complexity — O(1) + delegate
## @invariants
##   - Idempotent: if cron already has s3_ssl_cache reference, skips
##   - Non-fatal: failure logs WARN, returns False
##   - Non-fatal: no crontab → returns True (nothing to migrate)
##   - Runs on bootstrap init (step_18_deploy_context) and update
## @rationale DRIFT-C4: old nginx/install.sh _acme_install_cron() installed
##            cron WITHOUT --renew-hook for S3 upload.
def migrate_cron_if_needed(acme_home: str = "/opt/acme.sh") -> bool:
    """Check crontab for old acme.sh entry (no S3 sync) → replace with new one."""
    from core.internal.bootstrap.cron_installer import migrate_acme_cron_if_needed as _impl

    return _impl(acme_home)


# endregion FUNC_migrate_cron_if_needed


# endregion ORCHESTRATION


# region HELPERS


# region FUNC_load_provider_context
## @purpose  Загрузить реестр провайдеров + acme-конфиг node.yaml (DevPlan 154 W1).
##            Возвращает (registry|None, node_plugin, plugins_map) — фолбэк при
##            недоступном реестре/битом node.yaml (обратная совместимость, WARN).
## @io       ⇥ node_yaml: str (путь; пусто → env NODE_YAML), facts: EnvironmentFacts | None,
##           environ: Mapping | None → ⎋ tuple[CertProviderRegistry|None, str, dict|None]
## @complexity — O(P + N) — P = провайдеры реестра, N = размер node.yaml
## @invariants
##   - load_registry() единожды; ConfigValidationError реестра → WARN + (не raise)
##   - node.yaml читается NodeYaml-фасадом (119 H1, семантика yaml.safe_load); отсутствие
##     acme-полей → пустые значения (get с default)
##   - plugins_map нормализуется до dict[str, str] (нестроковые значения отбрасываются)
##   - NODE_YAML env — fallback, если параметр пуст (канон issue-cert.sh)
## @changes 2026-08-13 | E1 (160): +facts/environ DI (os.path.isfile/os.environ через параметры)
## @changes 2026-08-15 | 170 W6-D3: yaml.safe_load → NodeYaml-фасад (119 H1)
def _load_provider_context(
    node_yaml: str,
    *,
    facts: EnvironmentFacts | None = None,
    environ: Mapping[str, str] | None = None,
) -> tuple[CertProviderRegistry | None, str, dict[str, str] | None]:
    """Load provider registry + node.yaml acme config. Fallback-safe (never raises)."""
    registry: CertProviderRegistry | None = None
    node_plugin = ""
    plugins_map: dict[str, str] | None = None

    if load_registry is None:
        return None, node_plugin, plugins_map

    try:
        registry = load_registry()
    except (ConfigValidationError, OSError) as e:
        logger.warning("[IMP:7][cert_orchestrator] Provider registry unavailable (%s) — single-plugin path", e)
        return None, node_plugin, plugins_map

    # ── node.yaml acme-конфиг: acme_dns_plugin (single) + acme_dns_plugins (per-domain) ──
    # 170 W6-D3: ручной yaml.safe_load → NodeYaml-фасад (119 H1): та же семантика чтения
    # (utf-8 + safe_load + dict-root), исключения конвертируются: FileNotFoundError →
    # ConfigNotFoundError, YAMLError → ConfigParseError (ловится ниже, WARN-путь сохраняется).
    env: Mapping[str, str] = os.environ if environ is None else environ
    node_path = node_yaml or env.get("NODE_YAML", "")
    if node_path and (facts or default_env_facts()).path_isfile(node_path):
        # ruff: ignore[PLW0717] — try вложен в условный блок внутри функции — после-try чтение локалей неанализируемо
        try:
            node = NodeYaml(node_path)
            node_plugin = str(node.get("acme_dns_plugin", default="") or "")
            # default="" (НЕ None): NodeYaml.get трактует default=None как «raise on missing»
            raw_map = cast(
                "object", node.get("acme_dns_plugins", default="") or {}
            )  # W11-G1 cross-file: node_yaml.get (G1 overload) → str | dict
            if isinstance(raw_map, dict):
                plugins_map = {
                    str(k): str(v)
                    for k, v in cast("dict[object, object]", raw_map).items()
                    if isinstance(v, (str, int))
                }  # W11-G1 cross-file: dict-сужение → каст границы
            logger.info(
                "[IMP:8][cert_orchestrator] node acme config: plugin=%r plugins_map=%s", node_plugin, plugins_map
            )
        except (ConfigNotFoundError, ConfigParseError, OSError) as e:
            logger.warning("[IMP:7][cert_orchestrator] node.yaml read failed (%s) — path", e)
    else:
        logger.info("[IMP:7][cert_orchestrator] No node.yaml (%r) — node_plugin from env only", node_path)

    return registry, node_plugin, plugins_map


# endregion FUNC_load_provider_context


# region FUNC_log_post_issue_coverage
## @purpose  Проверить покрытие домена после issue-cert.sh (FL15, DevPlan 125 T5):
##            direct-сертификат live/{domain}/ ИЛИ wildcard родителя (*.tronyx.ru покрывает
##            botanika.tronyx.ru). issue-cert.sh SKIP'ает поддомены wildcard'а с rc=0 —
##            прежняя проверка только rc давала ложный alarm «Missing cert».
## @io       ⇥ domain: str, validity_path: str | None, facts: EnvironmentFacts | None
##           → ⎋ str («direct» | «wildcard:parent» | «none»)
## @complexity — O(ancestors) — до 2 openssl subject-проверок
## @invariants
##   - direct: live/{domain}/fullchain.pem с subject, покрывающим domain (exact CN)
##   - wildcard: live/{parent}/fullchain.pem с CN = *.parent (cert_subject_matches_domain)
##   - INFO «covered by wildcard» — НЕ alarm; только реальное отсутствие покрытия → WARN (FL15)
##   - Non-fatal: openssl ошибки → «none» (WARN-путь, никогда не raise)
## @changes 2026-08-13 | E1 (160): +validity_path/facts DI (os.path.isfile → facts.path_isfile)
def _log_post_issue_coverage(
    domain: str,
    *,
    validity_path: str | None = None,
    facts: EnvironmentFacts | None = None,
) -> str:
    """Проверить покрытие домена (direct или wildcard родителя) и залогировать вердикт (FL15)."""
    vpath = validity_path or CERT_VALIDITY_PATH
    facts_obj = facts or default_env_facts()
    # 1. Direct: сертификат самого домена
    direct = os.path.join(vpath, domain, "fullchain.pem")
    if facts_obj.path_isfile(direct):
        subject = cert_get_subject(direct)
        if subject and cert_subject_matches_domain(subject, domain):
            logger.info(
                "[IMP:9][cert_orchestrator] %s — covered by direct cert (live/%s/fullchain.pem)", domain, domain
            )
            return "direct"

    # 2. Wildcard: *.parent покрывает поддомен (только для subdomains — parent != domain)
    # ⚠️ TRAP[BUG] · 2026-08-06 · P1 · Ночная сессия 141 — B12: прямой серт родителя ≠ wildcard
    # · Symptom: botanika/roadmap.tronyx.ru «covered by wildcard *.tronyx.ru» при ОТСУТСТВИИ
    # ·   сертификатов (nginx emerg: cannot load certificate) — direct-серт tronyx.ru (CN=tronyx.ru)
    # ·   проходил проверку cert_subject_matches_domain(subject, parent) как «wildcard».
    # · Root: проверка не требовала WILDCARD-формы CN (*.parent) — direct-серт родителя матчился.
    # · Fix: wildcard-ветка проверяет subject против '*.parent' (только настоящий wildcard).
    # · Rev: если wildcard-серты перестанут использоваться — ветку можно удалить.
    labels = domain.split(".")
    for i in range(1, len(labels) - 1):
        parent = ".".join(labels[i:])
        wildcard_path = os.path.join(vpath, parent, "fullchain.pem")
        if not facts_obj.path_isfile(wildcard_path):
            continue
        subject = cert_get_subject(wildcard_path)
        if subject and cert_subject_matches_domain(subject, f"*.{parent}"):
            logger.info(
                "[IMP:9][cert_orchestrator] %s — covered by wildcard %s (issue-cert SKIP поддомена), НЕ alarm (FL15)",
                domain,
                f"*.{parent}",
            )
            return f"wildcard:{parent}"

    logger.warning(
        "[IMP:7][cert_orchestrator] %s — NO cert coverage after issue (ни direct, ни wildcard родителя) — "
        "возможен «Missing cert» alarm; проверьте каталог сертификатов %s",
        domain,
        vpath,
    )
    return "none"


# endregion FUNC_log_post_issue_coverage


# region FUNC_source_secrets_env
## @purpose  Source secrets.env file to load DNS-provider credentials into environment.
##            Тонкая обёртка-делегат (170 W6-D3): вычисляет allowlist из реестра провайдеров
##            и делегирует запись в secrets_env_apply.apply_secrets_env (канонический канал
##            086/170 — изоляция мутации env). Затем purges proxy-переменные (прокси ломает
##            S3/acme после source) и WARN-ит WEBNAMES_API_KEY без '*'-префикса.
## @io — ⇥ secrets_env_path: str, registry: CertProviderRegistry|None → ⎋ None (side-effect: env vars set)
## @complexity — O(N) где N = записей secrets.env
## @invariants
##   - Non-fatal: if source fails (FileNotFoundError), logs WARN
##   - Only exports env vars, does not modify the file
##   - Фильтр (DevPlan 154 W1): точные имена кредов реестра (allowlist) + префиксы
##     (WEBNAMES/S3_/PLATFORM_) при недоступном реестре; WEBNAMES_API_KEY всегда fallback-safe
## @changes 2026-08-15 | 170 W6-D3: parse+запись → apply_secrets_env (секреты_env_apply.py);
##            _plw_body__source_secrets_env удалён (try-тело стало тривиальным вызовом)
def _source_secrets_env(secrets_env_path: str, registry: CertProviderRegistry | None = None) -> None:
    """Source secrets.env to load WEBNAMES_API_KEY and other secrets (allowlist-канал)."""
    # Allowlist-вычисление вне try — FileNotFoundError бросает ТОЛЬКО apply_secrets_env (parse);
    # registry.all_cred_names() — set-операции без I/O (семантика except не меняется).
    if registry is not None:
        target_names = set(registry.all_cred_names()) | {"WEBNAMES_API_KEY"}
        target_prefixes = ("S3_", "PLATFORM_")
    else:
        target_names = {"WEBNAMES_API_KEY"}
        target_prefixes = _FALLBACK_SECRET_PREFIXES
    try:
        apply_secrets_env(secrets_env_path, target_names, target_prefixes)
        _purge_proxy_env()
        _warn_webnames_key()
    except FileNotFoundError:
        logger.warning("[IMP:7][cert_orchestrator] Secrets file not found (non-fatal): %s", secrets_env_path)


# endregion FUNC_source_secrets_env


# region FUNC_purge_proxy_env
## @purpose  Удалить proxy-переменные из os.environ после source secrets.env — прокси ломает
##            S3/acme-вызовы (s3_ssl_cache / issue_cert используют прямые соединения).
## @io — ⇥ → ⎋ None (side-effect: pop proxy-переменных)
## @complexity — O(P) — P = 6 имён (константный набор)
## @invariants
##   - pop с default=None — отсутствующие переменные не ошибка
##   - Набор фиксирован (HTTPS/HTTP/NO_PROXY в верхнем и нижнем регистре)
## @changes 2026-08-15 | 170 W6-D3 — вынесен из _source_secrets_env (декомпозиция обёртки)
_PROXY_ENV_VARS = ("HTTPS_PROXY", "HTTP_PROXY", "https_proxy", "http_proxy", "NO_PROXY", "no_proxy")


def _purge_proxy_env() -> None:
    """Удалить proxy-переменные окружения (прокси не должен перехватывать S3/acme)."""
    for proxy_var in _PROXY_ENV_VARS:
        os.environ.pop(proxy_var, None)


# endregion FUNC_purge_proxy_env


# region FUNC_warn_webnames_key
## @purpose  WARN если WEBNAMES_API_KEY не начинается с '*' — webnames.ru API возвращает
##            zone_manager_unavailable для domains_list без '*'-префикса (add/delete TXT работают).
## @io — ⇥ → ⎋ None (side-effect: возможно WARN-лог)
## @complexity — O(1)
## @invariants
##   - Пустой ключ → без WARN (не настроен)
##   - Ключ со '*'-префиксом → без WARN (канон webnames control panel)
## @changes 2026-08-15 | 170 W6-D3 — вынесен из _source_secrets_env (декомпозиция обёртки)
def _warn_webnames_key() -> None:
    """WARN о WEBNAMES_API_KEY без ведущего '*' (webnames.ru API может вернуть zone_manager_unavailable)."""
    webnames_key = os.environ.get("WEBNAMES_API_KEY", "")
    if webnames_key and not webnames_key.startswith("*"):
        logger.warning(
            "[IMP:9][cert_orchestrator] WEBNAMES_API_KEY missing leading '*' — "
            "webnames.ru API may return zone_manager_unavailable for domains_list "
            "(listing only, add/delete TXT records still work). "
            "The key shown in webnames control panel includes the asterisk prefix."
        )


# endregion FUNC_warn_webnames_key


# endregion HELPERS
