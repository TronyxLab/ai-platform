#!/usr/bin/env python3
# GREP_SUMMARY: issue-cert, acme.sh, letsencrypt, tls, dns-01, webnames, dnsapi, wildcard-cert, idempotent, cert-expiry, project-certs, http-01, standalone, shred, inject, retry, DI, runner, node-yaml
# STRUCTURE: ▶ ┌environ (NODE_YAML/PLATFORM_*)┐ → ○ resolve_domain_config (NodeYaml | env fallback) → ◇ main LE-valid? SKIP
#           → ◇ validate env → issue_tls_cert (dns/http/auto + retry) → ◇ http/auto → platform.domain individual
#           → ⊕ cert_check_expiry (30d) → ○ issue_project_certs (subdomain-skip) → ⎋ exit 0|1
# region MODULE_CONTRACT
## @purpose  SSL/TLS certificate issuance via acme.sh DNS-01/HTTP-01 (DevPlan 164 W3.5-1 S8).
##           Strangler-декомпозиция issue-cert.sh (708 LOC shell → тестируемый Python-модуль):
##           acme.sh CLI-вызовы остаются subprocess-вызовами модуля; API-key shred-протокол,
##           DNS-01/HTTP-01 ветвление, retry, node.yaml-резолв и project-certs — Python.
##           Вызывается ТОЛЬКО cert_orchestrator.py как subprocess `python3 -m core.internal.bootstrap.issue_cert`
##           (env-контракт PLATFORM_DOMAIN/PLATFORM_EMAIL/PLATFORM_ACME_DNS_PLUGIN/PLATFORM_PROJECT_DOMAINS/
##           ACME_CHALLENGE_MODE/WEBNAMES_API_KEY/NODE_YAML) — НЕ автономный CLI.
## @scope    Executor-модуль в цепочке φ7 certificates / φ12 deploy_update (через cert_orchestrator).
##           Вся оркестрация (domain iteration, S3 cache, cron, upload) — в cert_orchestrator.py.
##           Реестр DNS-провайдеров (provider_registry) управляет env — модуль читает готовый плагин.
## @invariants
##   - Идемпотентность: /etc/letsencrypt/live/$domain/fullchain.pem от Let's Encrypt → SKIP (exit 0)
##     (issuer-проверка через shared/ssl_certs.cert_is_le_issuer — единый SoT, P0 mkcert-TRAP)
##   - DNS-01 primary (wildcard), HTTP-01 fallback via ACME_CHALLENGE_MODE=auto; http — HTTP-01 only
##   - HTTP-01 standalone требует свободный порт 80 (вызывается ДО docker compose up); без wildcard
##   - webnames: инъекция API-ключа в dnsapi-скрипт + shred из ВСЕХ on-disk-локаций сразу после acme.sh
##     (tmpfs /tmp + ${ACME_HOME}/dnsapi/dns_webnames.sh); ключ НИКОГДА не попадает в логи/вывод
##   - Retry: acme.sh --issue провал → повторная попытка (max_attempts=2, re-inject для webnames)
##   - generic DNS-01: креды env-переменными acme.sh (CF_Token, DP_Id, REGRU_API_Username...);
##     regru — env-passthrough + account.conf (TRAP 2026-08-12, renewal через cron)
##   - NODE_YAML резолв через NodeYaml фасад (E12/D18): node.yaml приоритетнее env (прежний канон)
##   - LETSENCRYPT_DIR env override (тесты); ACME_HOME override (тесты/иной путь)
##   - Логи — в stderr (logging.basicConfig stream=sys.stderr), LDD [IMP:1-10], stdout пуст
## @rationale Языковая политика (root AGENTS.md): BUSINESS_LOGIC shell >100 LOC → Python.
##            W3-7 аудит: issue-cert.sh 708 LOC (keep-Rev 2027-02) — Rev снят решением S8
##            (DevPlan 164 W3.5-1): acme.sh стабилен >6 мес, порт даёт тестируемость
##            (DNS-01 vs HTTP-01, shred-протокол, retry) и убирает 708 LOC shell.
##            Существующие Python-модули переиспользованы: ssl_certs (--is-le/--check-expiry → прямые
##            вызовы cert_is_le_issuer/cert_check_expiry), node_yaml (--domain-config → get_domain_config).
## @changes  2026-08-14 | DevPlan 164 W3.5-1 — создан (полная декомпозиция issue-cert.sh, S8)
## @changes  2026-08-15 | DevPlan 170 W6-D2 — run() 128 LOC/CC20 → 5 этапных функций
##                      (_resolve_inputs/_ensure_dns01_challenge/_run_acme/_verify_result/_finalize);
##                      webnames inject+shred → webnames_protocol.py (inject_webnames/shred_secrets,
##                      re-export inject_webnames_key/_shred_paths для тест-контракта)
## ⚠️ TRAP[DECISION] · 2026-07-23 · D1 — DNS-01 primary, HTTP-01 graceful degradation
## · Rejected: HTTP-01 only (no wildcard certs)
## · Reason: DNS-01 preferred (wildcard), HTTP-01 fallback when DNS-01 unavailable
## · Rev: when webnames.ru API recovers → revert to DNS-01 only
## ⚠️ TRAP[DECISION] · 2026-08-12 · HI · regru-креды — env-passthrough + account.conf (DevPlan 154 W1)
## · Rejected: inject+shred (webnames-паттерн) для regru
## · Reason: acme.sh сохраняет env-креды в account.conf (mutable) — они НУЖНЫ для автоматического
## ·   renew (cron daily). inject+shred сломал бы renewal (креды уничтожены). Компромисс:
## ·   /opt/acme.sh/account.conf root:600 на изолированной корпоративной ноде.
## · Rev: появление токен-API reg.ru → перевести на inject+shred (реестр: mode=inject).
# endregion MODULE_CONTRACT

from __future__ import annotations

import logging
import os
import shutil
import sys
import tempfile
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from pathlib import Path

# DevPlan 170 W6-D2: webnames API-key инъекция + shred-протокол консолидированы в
# webnames_protocol.py; имена inject_webnames_key/_shred_paths — алиасы re-export
# (контракт test_issue_cert.py, приватные имена сохранены).
from core.internal.bootstrap.webnames_protocol import (
    DNSAPI_PLUGIN_NAME,
    WEBNAMES_EXT_SCRIPT,
)
from core.internal.bootstrap.webnames_protocol import (
    inject_webnames as inject_webnames_key,
)
from core.internal.bootstrap.webnames_protocol import (
    shred_secrets as _shred_paths,
)
from core.internal.shared.contracts import EXIT_GENERIC, EXIT_OK
from core.internal.shared.env_facts import EnvironmentFacts, default_env_facts
from core.internal.shared.node_yaml import NodeYaml
from core.internal.shared.ssl_certs import cert_check_expiry, cert_is_le_issuer
from core.internal.shared.subprocess_io import CommandRunner, default_command_runner

logger = logging.getLogger(__name__)

# ── Канонические константы (совпадают с прежними литералами issue-cert.sh) ──
DEFAULT_ACME_HOME: str = "/opt/acme.sh"
DEFAULT_LETSENCRYPT_DIR: str = "/etc/letsencrypt"
ACME_SERVER: str = "letsencrypt"
KEY_LENGTH: str = "ec-256"
EXPIRY_THRESHOLD_DAYS: int = 30
ISSUE_MAX_ATTEMPTS: int = 2  # retry на acme.sh failure (W3.5-1 тест-спека)
PORT80_SS_TIMEOUT: int = 10
PORT80_NETSTAT_TIMEOUT: int = 10
ACME_CMD_TIMEOUT: int = 300
INSTALL_CERT_TIMEOUT: int = 60
# DNSAPI_PLUGIN_NAME/WEBNAMES_EXT_SCRIPT — из webnames_protocol.py (W6-D2)

# Порог проверки expiry в секундах (30 дней — канон ssl_certs.DEFAULT_EXPIRY_THRESHOLD)
EXPIRY_THRESHOLD_SECONDS: int = EXPIRY_THRESHOLD_DAYS * 86400


# region DATACLASSES


@dataclass
class DomainConfig:
    """Доменный конфиг из node.yaml / env (E12: platform_domain, email, acme_dns_plugin, project_domains).

    ## @purpose — Результат резолва node.yaml (NodeYaml.get_domain_config) с env-fallback.
    ## @io — ⇥ поля → ⎋ frozen-подобный dataclass
    ## @complexity — O(1)
    """

    platform_domain: str = ""
    email: str = ""
    acme_dns_plugin: str = ""
    project_domains: list[str] = field(default_factory=list)


@dataclass
class IssueContext:
    """DI-контекст выпуска сертификатов (runner/facts/environ + канонные пути).

    ## @purpose — Единая точка инъекции I/O-каналов и путей (W-H DevPlan 163 паттерн):
    ##            тесты передают fake runner/facts и tmp-пути; prod — дефолты.
    ## @io — ⇥ runner/facts/environ; acme_home/letsencrypt_dir/tmp_dir/max_attempts → ⎋ контекст
    ## @complexity — O(1)
    ## @invariants
    ##   - runner=None → default_command_runner() (канон run_subprocess, C10/B4)
    ##   - facts=None → default_env_facts()
    ##   - environ — Mapping (os.environ в prod; тесты — dict)
    ##   - tmp_dir пуст → tempfile.gettempdir() (mktemp канон /tmp/dns_webnames.XXXXXX)
    """

    runner: CommandRunner = field(default_factory=default_command_runner)
    facts: EnvironmentFacts = field(default_factory=default_env_facts)
    environ: Mapping[str, str] = field(default_factory=lambda: os.environ)
    acme_home: str = DEFAULT_ACME_HOME
    letsencrypt_dir: str = DEFAULT_LETSENCRYPT_DIR
    tmp_dir: str = ""
    max_attempts: int = ISSUE_MAX_ATTEMPTS


# endregion DATACLASSES


# region FUNC__log_step
def _log_step(step: str, status: str, msg: str) -> None:
    """log_step-эквивалент: [IMP:8][issue-cert][<step>] <STATUS>: <msg> (logging.sh канон).

    ## @purpose  Байт-совместимый вывод шагов с прежним shell log_step (logging.sh).
    ## @io — ⇥ step, status, msg → ⎋ stderr via logger
    ## @complexity — O(1)
    """
    logger.info("[IMP:8][issue-cert][%s] %s: %s", step, status, msg)


# endregion FUNC__log_step


# region FUNC_resolve_domain_config
## @purpose  Резолв доменного конфига: NodeYaml (NODE_YAML env) → env fallback (E12/D18 канон).
## @io       ⇥ node_yaml: str (путь; пусто → environ NODE_YAML), environ: Mapping → ⎋ DomainConfig
## @complexity — O(P) — P = размер node.yaml
## @invariants
##   - NODE_YAML задан и файл существует → NodeYaml.get_domain_config() (flat schema, D18)
##   - node.yaml значение приоритетнее env (прежний shell: `${yaml:-${env:-}}`)
##   - Ошибка чтения/парсинга → WARN + env fallback (graceful, прежний log_warn канон)
##   - project_domains — список из NodeYaml или split по пробелам env PLATFORM_PROJECT_DOMAINS
def resolve_domain_config(node_yaml: str, environ: Mapping[str, str]) -> DomainConfig:
    """Read domain config from node.yaml (NodeYaml facade) with env fallback (E12/D18)."""
    cfg = DomainConfig(
        platform_domain=environ.get("PLATFORM_DOMAIN", ""),
        email=environ.get("PLATFORM_EMAIL", ""),
        acme_dns_plugin=environ.get("PLATFORM_ACME_DNS_PLUGIN", ""),
        project_domains=_split_domains(environ.get("PLATFORM_PROJECT_DOMAINS", "")),
    )
    path = node_yaml or environ.get("NODE_YAML", "")
    if not path or not Path(path).is_file():
        if path:
            _log_step("main", "WARN", "Failed to parse NODE_YAML via NodeYaml — falling back to env vars")
        return cfg

    try:
        yaml_cfg = NodeYaml(path).get_domain_config()
    # ruff: ignore[BLE001] — best-effort: произвольный node.yaml (graceful, прежний канон)
    except Exception as e:  # noqa: EXC — best-effort: NodeYaml парсинг произвольного node.yaml (graceful, прежний канон)
        _log_step("main", "WARN", "Failed to parse NODE_YAML via NodeYaml — falling back to env vars")
        logger.info("[IMP:8][issue-cert][main] node.yaml parse error: %s", e)
        return cfg

    # node.yaml value takes priority, then existing env (прежний shell E12 канон)
    cfg.platform_domain = yaml_cfg.platform_domain or cfg.platform_domain
    cfg.email = yaml_cfg.email or cfg.email
    cfg.acme_dns_plugin = yaml_cfg.acme_dns_plugin or cfg.acme_dns_plugin
    if yaml_cfg.project_domains:
        cfg.project_domains = yaml_cfg.project_domains
    logger.info("[IMP:8][issue-cert][main] Domain config resolved (NODE_YAML=%s)", path)
    return cfg


# endregion FUNC_resolve_domain_config


# region FUNC__split_domains
def _split_domains(raw: str) -> list[str]:
    """Разбить пробельную строку доменов (PLATFORM_PROJECT_DOMAINS) на список.

    ▶ ┌raw┐ → ○ split → ○ filter пустых → ⎋ list[str]
    """
    return [d for d in raw.split() if d]


# endregion FUNC__split_domains


# region FUNC_is_subdomain
## @purpose  Проверить, что domain — поддомен parent (используется issue_project_certs).
## @io       ⇥ domain: str, parent: str → ⎋ bool
## @complexity — O(1)
## @invariants
##   - Пустой domain/parent → False; точное совпадение (domain == parent) → False
##   - Суффикс-матчинг: domain == *.parent (app.tronyx.ru → tronyx.ru)
def is_subdomain(domain: str, parent: str) -> bool:
    """True if domain is a subdomain of parent (suffix match, exact match excluded)."""
    if not domain or not parent:
        return False
    return domain != parent and domain.endswith("." + parent)


# endregion FUNC_is_subdomain


# region FUNC__cert_path
def _cert_path(ctx: IssueContext, domain: str) -> str:
    """Полный путь fullchain.pem домена (LETSENCRYPT_DIR/live/$domain/fullchain.pem)."""
    return str(Path(ctx.letsencrypt_dir) / "live" / domain / "fullchain.pem")


# endregion FUNC__cert_path


# region FUNC_issue_tls_cert
## @purpose  Публичная обёртка выпуска TLS-сертификата с guard-логикой (экс-issue_tls_cert shell).
## @io       ⇥ domain, email, dns_plugin, wildcard: bool, ctx: IssueContext → ⎋ bool
## @complexity — O(T) — T = acme.sh время (с retry)
## @invariants
##   - Пустой domain → SKIP True (не ошибка — main обрабатывает)
##   - Идемпотентность: cert_is_le_issuer(fullchain.pem) → SKIP True (P0 mkcert-TRAP, D1)
##   - challenge_mode=http ИЛИ dns_plugin=http01 → HTTP-01 standalone (без DNS-01); wildcard → IMP:9 WARN
##   - DNS-01 требует dns_plugin (иначе FAIL); webnames требует WEBNAMES_API_KEY (иначе FAIL)
##   - challenge_mode=auto: DNS-01 провал → HTTP-01 fallback (IMP:9 WARN о потере wildcard)
def issue_tls_cert(
    domain: str,
    email: str,
    dns_plugin: str,
    wildcard: bool,
    ctx: IssueContext,
    *,
    challenge_mode: str = "dns",
    cert_is_le_issuer_fn: Callable[[str], bool] | None = None,
) -> bool:
    """Issue TLS cert via acme.sh (dns/http/auto per ACME_CHALLENGE_MODE). True = issued or skipped."""
    if not domain:
        _log_step("acme.sh", "SKIP", "No domain specified — skipping TLS certificate")
        return True

    # [IMP:9][issue-cert][acme.sh] Idempotency: do NOT re-issue existing valid LE certificate
    # ⚠️ TRAP[BUG] · 2026-07-22 · P0 · Was: -f check only → mkcert certs passed as valid
    # · Fix (DevPlan 119 D1): ssl_certs.cert_is_le_issuer (единый SoT) — вердикт тот же (rejects non-LE)
    # cert_is_le_issuer_fn (DevPlan 167 D3): fake issuer-проверка для тестов; None → канон.
    # 🧐 TRAP[DI-SEAM] · 2026-08-14 · — · cert_is_le_issuer_fn на issue_tls_cert (issuer-check seam)
    # · Rejected: прямой вызов shared/ssl_certs.cert_is_le_issuer (openssl-субпроцесс)
    # · Reason: seam = тестируемость реального issuer-решения (SKIP vs re-issue) без openssl
    # ·   и без глобального патча module-атрибута; поведение по умолчанию (None → канон) неизменно
    # · Rev: появление общего ssl-certs-объекта (issuer + expiry) → единый DI-контекст
    le_issuer = cert_is_le_issuer if cert_is_le_issuer_fn is None else cert_is_le_issuer_fn
    cert_path = _cert_path(ctx, domain)
    if le_issuer(cert_path):
        _log_step("acme.sh", "SKIP", f"Valid LE certificate already exists: {cert_path}")
        logger.info("[IMP:9][issue-cert][acme.sh] Idempotent skip — valid LE cert on disk: %s", cert_path)
        return True
    if ctx.facts.path_isfile(cert_path):
        _log_step("acme.sh", "WARN", "Certificate exists but NOT from Let's Encrypt (mkcert/self-signed?) — re-issuing")

    # ── HTTP-01 only mode: bypass DNS-01 entirely (registry-driven http01 provider, DevPlan 154 W1) ──
    if challenge_mode == "http" or dns_plugin == "http01":
        _log_step("acme.sh", "INFO", "ACME_CHALLENGE_MODE=http — using HTTP-01 standalone (no DNS-01)")
        if wildcard:
            logger.info(
                "[IMP:9][issue-cert][acme.sh] WARN: wildcard=true with HTTP-01 — LE requires DNS-01 for wildcard. "
                "Issuing individual domain cert."
            )
        return _issue_http01_cert(domain, email, ctx)

    # ── DNS-01 or AUTO mode: DNS plugin required ──
    if not dns_plugin:
        _log_step("tls", "FAIL", "TLS certificate requires DNS plugin (set PLATFORM_ACME_DNS_PLUGIN in env)")
        return False

    if dns_plugin == "webnames" and not ctx.environ.get("WEBNAMES_API_KEY", ""):
        _log_step("tls", "FAIL", "WEBNAMES_API_KEY not set — required for wildcard TLS via acme.sh DNS-01")
        return False

    _log_step(
        "acme.sh", "START", f"Issuing TLS certificate for {domain} (email: {email}) via acme.sh DNS-01 ({dns_plugin})"
    )

    ok = _issue_acme_cert(domain, email, dns_plugin, wildcard, ctx)

    # ── AUTO mode: fallback to HTTP-01 on DNS-01 failure ──
    if not ok and challenge_mode == "auto":
        logger.info(
            "[IMP:9][issue-cert][acme.sh] DNS-01 failed for %s — falling back to HTTP-01 (no wildcard cert)", domain
        )
        if wildcard:
            logger.info(
                "[IMP:9][issue-cert][acme.sh] HTTP-01 does NOT support wildcard — "
                "issuing individual domain cert for %s instead of *.%s",
                domain,
                domain,
            )
        return _issue_http01_cert(domain, email, ctx)

    return ok


# endregion FUNC_issue_tls_cert


# region FUNC__issue_acme_cert
## @purpose  Issue Let's Encrypt cert via acme.sh DNS-01 challenge (webnames inject+shred | generic).
## @io       ⇥ domain, email, dns_plugin, wildcard, ctx → ⎋ bool
## @complexity — O(A × T) — A = max_attempts, T = acme.sh время
## @invariants
##   - webnames: ключ инъектируется в dnsapi-скрипт (tmp + ${ACME_HOME}/dnsapi/) и shred'ится
##     из ВСЕХ on-disk-локаций сразу после acme.sh (включая провал — ключ не остаётся на диске)
##   - Retry: acme.sh --issue провал → повторная попытка (re-inject для webnames)
##   - Плагин — короткое имя (dns_webnames) из dnsapi/, НЕ полный путь (P2 acme.sh basename TRAP)
##   - Установка cert (--install-cert) в LETSENCRYPT_DIR/live/$domain/ с --reloadcmd (nginx + s3_ssl_cache)
## ⚠️ TRAP[BUG] · 2026-07-03 · P2 · acme.sh basename bug — PID in temp dir path
## · acme.sh распознаёт DNS-плагины ТОЛЬКО по короткому имени (dns_webnames) из директории
## · dnsapi/, а НЕ по полному пути. При передаче полного пути acme.sh молча игнорирует флаг
## · и падает в HTTP-01 (не поддерживает wildcard). Фикс: копия с инъекцией в dnsapi/ +
## · короткое имя. Prevention: всегда копировать плагин в dnsapi/ с фиксированным именем.
## 💼 TRAP[BUSINESS] · 2026-06-11 · HI · API key cleaned from disk after use — security requirement
## · Risk: plaintext API key on persistent disk is a security vulnerability
## · Mitigation: key written to tmpfs (/tmp), used for acme.sh, then shredded immediately
def _issue_acme_cert(
    domain: str,
    email: str,
    dns_plugin: str,
    wildcard: bool,
    ctx: IssueContext,
) -> bool:
    """Issue a DNS-01 cert via acme.sh (webnames inject+shred / generic env-creds)."""
    acme_sh = str(Path(ctx.acme_home) / "acme.sh")
    if not (ctx.facts.path_isfile(acme_sh) and os.access(acme_sh, os.X_OK)):
        _log_step("acme", "FAIL", f"acme.sh not found at {acme_sh}")
        return False

    _log_step("acme", "START", f"Issuing TLS certificate via acme.sh ({dns_plugin}) for {domain} (email: {email})")

    if dns_plugin == "webnames":
        return _issue_acme_webnames(domain, email, wildcard, ctx)
    return _issue_acme_generic(domain, email, dns_plugin, wildcard, ctx)


# endregion FUNC__issue_acme_cert


# region FUNC__issue_acme_webnames
## @purpose  webnames-ветка DNS-01: инъекция API-ключа + shred-протокол (TRAP 2026-06-11).
## @io       ⇥ domain, email, wildcard, ctx → ⎋ bool
## @complexity — O(A × T) — A = max_attempts, T = acme.sh время
## @invariants
##   - Ключ НЕ логируется и не попадает в stdout/stderr модуля (только в файлы-жертвы, которые shred)
##   - Инъекция: API_KEY= строка заменяется (sed-канон); tmp-файл chmod +x; копия в ${ACME_HOME}/dnsapi/
##   - После последней попытки: shred -u tmp + dnsapi/dns_webnames.sh (rm -f fallback) — всегда
##   - retry: acme.sh --issue rc!=0 → повторная инъекция + повтор (max_attempts)
## ⚠️ TRAP[BUG] · 2026-07-23 · P0 · FALSE DIAGNOSIS: zone_manager_unavailable ≠ DNS-01 broken
## · Symptom: webnames.ru API returns {"result":"ERROR","details":"zone_manager_unavailable"} for
## ·   domains_list. Reality: TXT add/delete WORK (add: OK, delete: OK). Wildcard *.tronyx.ru
## ·   issued via LE staging 2026-07-23. Root of prior failure: LE rate-limit (50/domain/week).
## · Prevention: НЕ отключать DNS-01 по domains_list ошибке — проверять add/delete.
def _issue_acme_webnames(domain: str, email: str, wildcard: bool, ctx: IssueContext) -> bool:
    """webnames DNS-01: inject API key into dnsapi script + shred after acme.sh (retry-совместимо)."""
    webnames_script = str(Path(ctx.acme_home) / "dnsapi_ext" / WEBNAMES_EXT_SCRIPT)
    if not ctx.facts.path_isfile(webnames_script):
        _log_step(
            "acme", "FAIL", f"dns_webnames.sh not found at {webnames_script} — ensure regtime-ltd/dnsapi is cloned"
        )
        return False

    api_key = ctx.environ.get("WEBNAMES_API_KEY", "")
    # Validate webnames API key format — must include leading asterisk
    if api_key and not api_key.startswith("*"):
        _log_step(
            "acme", "WARN", "WEBNAMES_API_KEY missing leading '*' — webnames.ru API may return zone_manager_unavailable"
        )
    if not api_key:
        _log_step("acme", "FAIL", "WEBNAMES_API_KEY not set in secrets — cannot authenticate to webnames.ru API")
        return False

    tmp_dir = ctx.tmp_dir or tempfile.gettempdir()
    fd, tmp_name = tempfile.mkstemp(prefix="dns_webnames.", dir=tmp_dir)
    os.close(fd)  # mkstemp открывает fd — закрываем (запись ниже через write_text)
    dnsapi_tmp = Path(tmp_name)
    # Inject API key into temp file (webnames script has API_KEY hardcoded) — sed-канон
    original = Path(webnames_script).read_text(encoding="utf-8")
    dnsapi_tmp.write_text(inject_webnames_key(original, api_key), encoding="utf-8")
    dnsapi_tmp.chmod(0o755)

    dnsapi_dest = Path(ctx.acme_home) / "dnsapi" / WEBNAMES_EXT_SCRIPT
    dnsapi_dest.parent.mkdir(parents=True, exist_ok=True)  # acme.sh dnsapi/ существует в проде; защита для тестов
    shutil.copy2(dnsapi_tmp, dnsapi_dest)
    dnsapi_dest.chmod(0o755)

    domain_args = [domain]
    if wildcard:
        domain_args.append(f"*.{domain}")

    last_rc = 1
    try:
        for attempt in range(1, ctx.max_attempts + 1):
            result = ctx.runner.run(
                [
                    str(Path(ctx.acme_home) / "acme.sh"),
                    "--issue",
                    "--home",
                    ctx.acme_home,
                    "--dns",
                    DNSAPI_PLUGIN_NAME,
                    "--server",
                    ACME_SERVER,
                    "--email",
                    email,
                    *[arg for d in domain_args for arg in ("-d", d)],
                    "--keylength",
                    KEY_LENGTH,
                ],
                timeout=ACME_CMD_TIMEOUT,
                check=False,
            )
            last_rc = result.returncode
            if last_rc == 0:
                break
            _log_step("acme", "WARN", f"acme.sh --issue exited with {last_rc} (attempt {attempt}/{ctx.max_attempts})")
    finally:
        # Wipe API key from all on-disk locations immediately after acme.sh completes (shred protocol)
        _shred_paths([dnsapi_tmp, dnsapi_dest], ctx.runner)

    if last_rc != 0:
        _log_step("acme", "FAIL", f"acme.sh --issue exited with {last_rc}")
        return False

    return _install_cert_files(domain, ctx)


# endregion FUNC__issue_acme_webnames


# region FUNC__issue_acme_generic
## @purpose  generic DNS-01 ветка: стандартный плагин acme.sh (CF_Token, DP_Id, REGRU... env creds).
## @io       ⇥ domain, email, dns_plugin, wildcard, ctx → ⎋ bool
## @complexity — O(A × T)
## @invariants
##   - Креды env-переменными (acme.sh конвенция); НЕ inject+shred (regru TRAP — renew нужен)
##   - retry: --issue провал → повтор (max_attempts)
def _issue_acme_generic(domain: str, email: str, dns_plugin: str, wildcard: bool, ctx: IssueContext) -> bool:
    """Generic DNS-01: acme.sh convention env creds (CF_Token, DP_Id, REGRU_API_*...)."""
    domain_args = [domain]
    if wildcard:
        domain_args.append(f"*.{domain}")

    last_rc = 1
    for attempt in range(1, ctx.max_attempts + 1):
        result = ctx.runner.run(
            [
                str(Path(ctx.acme_home) / "acme.sh"),
                "--issue",
                "--home",
                ctx.acme_home,
                "--dns",
                f"dns_{dns_plugin}",
                "--server",
                ACME_SERVER,
                "--email",
                email,
                *[arg for d in domain_args for arg in ("-d", d)],
                "--keylength",
                KEY_LENGTH,
            ],
            timeout=ACME_CMD_TIMEOUT,
            check=False,
        )
        last_rc = result.returncode
        if last_rc == 0:
            break
        _log_step(
            "acme",
            "WARN",
            f"acme.sh --issue (generic dns_{dns_plugin}) failed with {last_rc} (attempt {attempt}/{ctx.max_attempts})",
        )

    if last_rc != 0:
        _log_step("acme", "FAIL", f"acme.sh --issue (generic dns_{dns_plugin}) failed")
        return False

    return _install_cert_files(domain, ctx)


# endregion FUNC__issue_acme_generic


# region FUNC__install_cert_files
## @purpose  Установить сертификат в LETSENCRYPT_DIR/live/$domain/ (--install-cert) + reloadcmd S3-upload.
## @io       ⇥ domain, ctx → ⎋ bool
## @complexity — O(1) + install-cert subprocess
## @invariants
##   - --key-file/--fullchain-file в канонный cert_dir (mkdir -p канон)
##   - --reloadcmd: nginx reload + s3_ssl_cache.py sync (S3 backup после каждого renew, 052 §4.5)
##   - Провал install-cert → FAIL False (сертификат выпущен, но не установлен — nginx сломается)
def _install_cert_files(domain: str, ctx: IssueContext) -> bool:
    """Install issued cert into LETSENCRYPT_DIR/live/DOMAIN (--install-cert + reloadcmd S3 sync)."""
    cert_dir = Path(ctx.letsencrypt_dir) / "live" / domain
    cert_dir.mkdir(parents=True, exist_ok=True)

    core_dir = Path(__file__).resolve().parent
    # S3 sync: acme.sh runs reloadcmd right after cert install — cert saved locally FIRST
    # (nginx reload + best-effort s3_ssl_cache upload, WARN on fail, 052 §4.5)
    result = ctx.runner.run(
        [
            str(Path(ctx.acme_home) / "acme.sh"),
            "--install-cert",
            "-d",
            domain,
            "--home",
            ctx.acme_home,
            "--key-file",
            str(cert_dir / "privkey.pem"),
            "--fullchain-file",
            str(cert_dir / "fullchain.pem"),
            "--reloadcmd",
            (
                f"systemctl reload nginx && if [ -f '{core_dir}/s3_ssl_cache.py' ]; "
                f"then python3 '{core_dir}/s3_ssl_cache.py' upload '{domain}'; fi"
            ),
        ],
        timeout=INSTALL_CERT_TIMEOUT,
        check=False,
    )
    if result.returncode != 0:
        _log_step("acme", "FAIL", f"acme.sh cert installation exited with {result.returncode}")
        return False
    _log_step("acme", "DONE", f"TLS certificate installed via acme.sh: {cert_dir / 'fullchain.pem'}")
    logger.info(
        "[IMP:9][issue-cert][acme] Certificate installed: %s (reloadcmd: nginx + s3_ssl_cache upload)", cert_dir
    )
    return True


# endregion FUNC__install_cert_files


# region FUNC__port80_in_use
## @purpose  Проверить занятость порта 80 (ss -tlnp | netstat fallback) — HTTP-01 standalone guard.
## @io       ⇥ runner: CommandRunner → ⎋ bool (True = порт занят)
## @complexity — O(1) subprocess × 1-2
## @invariants
##   - ss отсутствует (rc 127) → netstat fallback; оба отсутствуют → False (порт свободен)
##   - ':80 ' в выводе (ss/netstat) → True (занят)
def _port80_in_use(runner: CommandRunner) -> bool:
    """True if port 80 is in use (ss -tlnp, netstat fallback) — HTTP-01 standalone needs it free."""
    for cmd, timeout in ((["ss", "-tlnp"], PORT80_SS_TIMEOUT), (["netstat", "-tlnp"], PORT80_NETSTAT_TIMEOUT)):
        result = runner.run(cmd, timeout=timeout, check=False)
        if result.returncode == 0 and ":80 " in (result.stdout or ""):
            return True
    return False


# endregion FUNC__port80_in_use


# region FUNC__issue_http01_cert
## @purpose  HTTP-01 standalone ветка (fallback): свободный порт 80 + --standalone issue + install.
## @io       ⇥ domain, email, ctx → ⎋ bool
## @complexity — O(A × T) — A = max_attempts, T = acme.sh время
## @invariants
##   - Порт 80 занят → FAIL (stop nginx first) — прежний ss/netstat канон
##   - Без wildcard (LE требует DNS-01 для wildcard) — только -d domain
##   - Retry на --issue провал (max_attempts)
def _issue_http01_cert(domain: str, email: str, ctx: IssueContext) -> bool:
    """Issue a Let's Encrypt cert via acme.sh HTTP-01 standalone (port 80 free, no wildcard)."""
    acme_sh = str(Path(ctx.acme_home) / "acme.sh")
    if not (ctx.facts.path_isfile(acme_sh) and os.access(acme_sh, os.X_OK)):
        _log_step("acme-http", "FAIL", f"acme.sh not found at {acme_sh}")
        return False

    _log_step("acme-http", "START", f"Issuing TLS certificate via HTTP-01 (standalone) for {domain}")

    # Check if port 80 is available (ss/netstat) — HTTP-01 standalone needs it free
    if _port80_in_use(ctx.runner):
        _log_step("acme-http", "FAIL", "Port 80 is in use — cannot use HTTP-01 standalone mode. Stop nginx first.")
        return False

    last_rc = 1
    for attempt in range(1, ctx.max_attempts + 1):
        result = ctx.runner.run(
            [
                str(Path(ctx.acme_home) / "acme.sh"),
                "--issue",
                "--home",
                ctx.acme_home,
                "--standalone",
                "--server",
                ACME_SERVER,
                "--email",
                email,
                "-d",
                domain,
                "--keylength",
                KEY_LENGTH,
            ],
            timeout=ACME_CMD_TIMEOUT,
            check=False,
        )
        last_rc = result.returncode
        if last_rc == 0:
            break
        _log_step(
            "acme-http",
            "WARN",
            f"acme.sh --issue --standalone exited with {last_rc} (attempt {attempt}/{ctx.max_attempts})",
        )

    if last_rc != 0:
        _log_step("acme-http", "FAIL", f"acme.sh --issue --standalone exited with {last_rc}")
        return False

    _log_step("acme-http", "DONE", "HTTP-01 certificate issued — installing")
    return _install_cert_files(domain, ctx)


# endregion FUNC__issue_http01_cert


# region FUNC_issue_project_certs
## @purpose  Выпуск сертификатов независимых проектных доменов (PLATFORM_PROJECT_DOMAINS).
## @io       ⇥ platform_domain, email, dns_plugin, project_domains: list[str], ctx → ⎋ tuple[int, int] (issued, skipped)
## @complexity — O(D × T) — D = проектных доменов
## @invariants
##   - Пропускает поддомены platform_domain (покрыты wildcard'ом)
##   - Пустой dns_plugin → SKIP (не ошибка)
##   - Non-fatal: провал одного домена не останавливает остальные (WARN)
##   - Идемпотентность: issue_tls_cert сам SKIP'ает существующие LE-сертификаты
def issue_project_certs(
    platform_domain: str,
    email: str,
    dns_plugin: str,
    project_domains: list[str],
    ctx: IssueContext,
    *,
    challenge_mode: str = "dns",
    cert_is_le_issuer_fn: Callable[[str], bool] | None = None,
) -> tuple[int, int]:
    """Issue wildcard certs for independent project domains. Returns (issued, skipped)."""
    if not project_domains:
        _log_step("project-certs", "SKIP", "No PLATFORM_PROJECT_DOMAINS — nothing to issue")
        return 0, 0
    if not dns_plugin:
        _log_step("project-certs", "SKIP", "No DNS plugin configured — cannot issue project certs")
        return 0, 0

    _log_step("project-certs", "START", f"Processing project domains: {' '.join(project_domains)}")

    issued = 0
    skipped = 0
    for domain in project_domains:
        if not domain:
            continue
        if platform_domain and is_subdomain(domain, platform_domain):
            _log_step("project-certs", "SKIP", f"{domain} — subdomain of {platform_domain}, covered by wildcard")
            skipped += 1
            continue
        _log_step("project-certs", "INFO", f"Issuing wildcard cert for: {domain}")
        if issue_tls_cert(
            domain,
            email,
            dns_plugin,
            wildcard=True,
            ctx=ctx,
            challenge_mode=challenge_mode,
            cert_is_le_issuer_fn=cert_is_le_issuer_fn,
        ):
            issued += 1
        else:
            _log_step("project-certs", "WARN", f"Failed to issue cert for {domain} — continuing")

    _log_step("project-certs", "DONE", f"Project certs: issued={issued} skipped={skipped}")
    logger.info("[IMP:9][issue-cert][project-certs] issued=%d skipped=%d", issued, skipped)
    return issued, skipped


# endregion FUNC_issue_project_certs


# region FUNC_run
## @purpose  Executor cert issuance logic (экс-тело main() shell) — env-контракт Python-оркестратора.
##           DevPlan 170 W6-D2: run() 128 LOC/CC20 декомпозирован на 5 этапных функций
##           (_resolve_inputs → _ensure_dns01_challenge → _run_acme → _verify_result → _finalize);
##           run() — тонкий оркестратор ≤50 LOC, поведение 1:1 (exit-коды, renew/issue, dns01/webroot).
## @io       ⇥ environ: Mapping | None; runner/facts DI → ⎋ int (0 = ok, 1 = generic error)
## @complexity — O(D × T) — D = доменов, T = acme.sh время
## @invariants
##   - sys.exit НЕ вызывается — run() возвращает int (канон core/AGENTS.md)
##   - NODE_YAML → resolve_domain_config (node.yaml приоритетнее env) — в _resolve_inputs
##   - Идемпотентность главного сертификата: LE-валидный → SKIP main, project certs продолжаются
##     (⚠️ TRAP[BUG] 2026-07-17 P1 — early exit блокировал project domains: exit → return канон)
##   - Валидация env: PLATFORM_DOMAIN/PLATFORM_EMAIL обязательны; dns_plugin — только не-http;
##     webnames key — только dns/auto — в _ensure_dns01_challenge
##   - http/auto → индивидуальный сертификат platform.DOMAIN (нет wildcard) — в _run_acme
##   - Проверка expiry >30 дней (ssl_certs.cert_check_expiry) — WARN, не ошибка — в _verify_result
##   - project certs — ВСЕГДА обрабатываются, НЕ блокируются idempotency main (P1 TRAP) — в _finalize
def run(
    environ: Mapping[str, str] | None = None,
    *,
    runner: CommandRunner | None = None,
    facts: EnvironmentFacts | None = None,
    cert_is_le_issuer_fn: Callable[[str], bool] | None = None,
) -> int:
    """Execute cert issuance from env (NODE_YAML/PLATFORM_*). Exit 0 = success, 1 = generic error.

    ▶ ┌environ/runner/facts DI┐ → ○ _resolve_inputs → ◇ _ensure_dns01_challenge (ok?)
      → ◇ main_cert_exists? │ → _run_acme → _verify_result → ○ _finalize (project certs + DONE) → ⎋ 0 | ⎋ 1

    cert_is_le_issuer_fn (DevPlan 167 D3): fake issuer-проверка для тестов; None → канон
    (shared/ssl_certs.cert_is_le_issuer). Пробрасывается в issue_tls_cert/issue_project_certs —
    единый issuer-вердикт на всём пути run().

    ## @changes 2026-08-15 | DevPlan 170 W6-D2 — run() → тонкий оркестратор (этапы в 5 функциях)
    """
    env: Mapping[str, str] = os.environ if environ is None else environ
    r = runner if runner is not None else default_command_runner()
    facts_obj = facts if facts is not None else default_env_facts()

    ctx, cfg, challenge_mode = _resolve_inputs(env, r, facts_obj)
    domain, email = cfg.platform_domain, cfg.email
    dns_plugin, project_domains = cfg.acme_dns_plugin, cfg.project_domains
    le_issuer = cert_is_le_issuer if cert_is_le_issuer_fn is None else cert_is_le_issuer_fn

    main_cert_exists, ok = _ensure_dns01_challenge(ctx, env, cfg, challenge_mode, le_issuer)
    if not ok:
        return EXIT_GENERIC

    if not main_cert_exists:
        if not _run_acme(domain, email, dns_plugin, challenge_mode, ctx, le_issuer, cert_is_le_issuer_fn):
            return EXIT_GENERIC
        _verify_result(_cert_path(ctx, domain))

    _finalize(domain, email, dns_plugin, project_domains, ctx, challenge_mode, cert_is_le_issuer_fn)
    return EXIT_OK


# endregion FUNC_run


# region FUNC__resolve_inputs
## @purpose  Этап 1 (W6-D2): собрать DI-контекст (IssueContext с env-override путей) +
##           резолв доменного конфига (NodeYaml приоритетнее env, E12/D18) + challenge_mode.
## @io       ⇥ env: Mapping, runner: CommandRunner, facts: EnvironmentFacts → ⎋ (ctx, cfg, challenge_mode)
## @complexity — O(P) — P = размер node.yaml
## @invariants
##   - ACME_HOME/LETSENCRYPT_DIR env override канон (shell: ${ACME_HOME:-/opt/acme.sh}, тесты)
##   - NODE_YAML → resolve_domain_config (node.yaml приоритетнее env)
def _resolve_inputs(
    env: Mapping[str, str],
    runner: CommandRunner,
    facts: EnvironmentFacts,
) -> tuple[IssueContext, DomainConfig, str]:
    """Собрать входные данные run(): IssueContext (DI) + DomainConfig + challenge_mode."""
    ctx = IssueContext(
        runner=runner,
        facts=facts,
        environ=env,
        acme_home=env.get("ACME_HOME", DEFAULT_ACME_HOME),  # env override канон (shell: ${ACME_HOME:-/opt/acme.sh})
        letsencrypt_dir=env.get("LETSENCRYPT_DIR", DEFAULT_LETSENCRYPT_DIR),  # env override (тесты/test_nginx_acme)
    )
    # ── S7: Parse NODE_YAML via NodeYaml (E12/D18: replaces shell grep|cut re-parsing) ──
    cfg = resolve_domain_config(env.get("NODE_YAML", ""), env)
    challenge_mode = env.get("ACME_CHALLENGE_MODE", "dns")
    return ctx, cfg, challenge_mode


# endregion FUNC__resolve_inputs


# region FUNC__ensure_dns01_challenge
## @purpose  Этап 2 (W6-D2): идемпотентность main-сертификата (LE-валидный → SKIP) + валидация
##           обязательного env (domain/email/dns_plugin/webnames key). Возвращает флаг main_cert_exists
##           (этап 3 пропускается) и ok (False → run exit 1, fail-fast канон).
## @io       ⇥ ctx, env, cfg, challenge_mode, le_issuer → ⎋ tuple[bool, bool] — (main_cert_exists, ok)
## @complexity — O(1)
## @invariants
##   - LE-валидный main cert → (True, True): project certs продолжаются (P1 TRAP — без early exit)
##   - Не-LE сертификат на диске → WARN re-issue (не SKIP) — mkcert P0 guard
##   - PLATFORM_DOMAIN/PLATFORM_EMAIL пустые → FAIL (False, False)
##   - challenge_mode != http: dns_plugin обязателен; webnames требует WEBNAMES_API_KEY
##   - challenge_mode == http: INFO (DNS plugin не требуется)
## ⚠️ TRAP[BUG] · 2026-07-17 · P1 · Early exit blocked project domains
## · Symptom: project domains skipped on subsequent node-update runs because early
## ·   exit 0 on main cert exists check happened BEFORE _issue_project_certs
## · Root: idempotency check used exit 0 which terminated the entire process
## · Fix: boolean flag to skip main cert issuance but continue to project domains
## · Prevention: always process project domains independently of main cert status
def _ensure_dns01_challenge(
    ctx: IssueContext,
    env: Mapping[str, str],
    cfg: DomainConfig,
    challenge_mode: str,
    le_issuer: Callable[[str], bool],
) -> tuple[bool, bool]:
    """Идемпотентность main + env-валидация → (main_cert_exists, ok)."""
    domain = cfg.platform_domain
    email = cfg.email
    dns_plugin = cfg.acme_dns_plugin
    cert_path = _cert_path(ctx, domain)

    # ── Idempotency: skip main cert if already exists AND is from Let's Encrypt ──
    main_cert_exists = False
    if domain and le_issuer(cert_path):
        _log_step("main", "SKIP", f"Valid LE certificate already exists: {cert_path} (idempotent)")
        logger.info(
            "[IMP:9][issue-cert][main] BUSINESS INVARIANT: main cert exists — skip main, continue project domains"
        )
        main_cert_exists = True
    elif domain and ctx.facts.path_isfile(cert_path):
        _log_step("main", "WARN", "Certificate exists but NOT from Let's Encrypt (mkcert/self-signed?) — re-issuing")

    if main_cert_exists:
        return True, True

    # ── Validate required environment ───────────────────────────────
    if not domain:
        _log_step("main", "FAIL", "PLATFORM_DOMAIN not set — cannot provision SSL certificate")
        return False, False
    if not email:
        _log_step("main", "FAIL", "PLATFORM_EMAIL not set — required for Let's Encrypt registration")
        return False, False

    # DNS plugin guard: only required for dns/auto modes, not for http-only mode
    if challenge_mode != "http":
        if not dns_plugin:
            _log_step("main", "FAIL", "PLATFORM_ACME_DNS_PLUGIN not set — required for DNS-01 challenge")
            return False, False
        if dns_plugin == "webnames" and not env.get("WEBNAMES_API_KEY", ""):
            _log_step("main", "FAIL", "WEBNAMES_API_KEY not set — required for webnames DNS-01 TLS")
            return False, False
    else:
        _log_step("main", "INFO", "ACME_CHALLENGE_MODE=http — DNS plugin not required, using HTTP-01 standalone")

    return False, True


# endregion FUNC__ensure_dns01_challenge


# region FUNC__run_acme
## @purpose  Этап 3 (W6-D2): выпуск main-сертификата (wildcard) + Step 1b — индивидуальный
##           сертификат platform.DOMAIN при HTTP-01 fallback (http/auto, нет wildcard).
## @io       ⇥ domain, email, dns_plugin, challenge_mode, ctx, le_issuer, cert_is_le_issuer_fn
##           → ⎋ bool (True = ok; False → run exit 1)
## @complexity — O(T) — T = acme.sh время (с retry)
## @invariants
##   - Провал issue_tls_cert main → False (fail-fast, exit 1 канон)
##   - platform.DOMAIN провал → WARN + continue (не блокирует)
##   - challenge_mode http/auto: individual cert только если нет LE-валидного (le_issuer)
def _run_acme(
    domain: str,
    email: str,
    dns_plugin: str,
    challenge_mode: str,
    ctx: IssueContext,
    le_issuer: Callable[[str], bool],
    cert_is_le_issuer_fn: Callable[[str], bool] | None,
) -> bool:
    """Step 1 + 1b: main wildcard cert + individual platform.DOMAIN (HTTP-01 fallback)."""
    # ── Step 1: Issue TLS certificate ─────────────────────
    _log_step("main", "START", f"SSL provisioning for {domain} via acme.sh ({dns_plugin or 'http-01'})")
    if not issue_tls_cert(
        domain,
        email,
        dns_plugin,
        wildcard=True,
        ctx=ctx,
        challenge_mode=challenge_mode,
        cert_is_le_issuer_fn=cert_is_le_issuer_fn,
    ):
        _log_step("main", "FAIL", f"TLS certificate issuance failed for {domain}")
        return False

    # ── Step 1b: Issue individual subdomain certs when HTTP-01 fallback in use ──
    # [IMP:9][issue-cert][main] When ACME_CHALLENGE_MODE is auto or http, the main cert
    # is individual (not wildcard). Known subdomains need their own individual certs.
    if challenge_mode in {"http", "auto"}:
        subdomain_path = _cert_path(ctx, f"platform.{domain}")
        if domain and not le_issuer(subdomain_path):
            _log_step("main", "INFO", f"Issuing individual cert for platform.{domain} (HTTP-01 fallback — no wildcard)")
            if not issue_tls_cert(
                f"platform.{domain}",
                email,
                dns_plugin,
                wildcard=False,
                ctx=ctx,
                challenge_mode=challenge_mode,
                cert_is_le_issuer_fn=cert_is_le_issuer_fn,
            ):
                _log_step("main", "WARN", f"Failed to issue individual cert for platform.{domain} — continuing")

    return True


# endregion FUNC__run_acme


# region FUNC__verify_result
## @purpose  Этап 4 (W6-D2): проверка expiry выпущенного main-сертификата >30 дней (WARN, не ошибка).
## @io       ⇥ cert_path: str → ⎋ None
## @complexity — O(1) — cert_check_expiry (openssl)
## @invariants
##   - Порог EXPIRY_THRESHOLD_SECONDS (30 дней — канон ssl_certs.DEFAULT_EXPIRY_THRESHOLD)
##   - Негативный результат — WARN (renew soon), НЕ ошибка (exit остаётся 0)
## @rationale DevPlan 119 D1: _acme_verify_cert → shared/ssl_certs.cert_check_expiry (единый SoT)
def _verify_result(cert_path: str) -> None:
    """Step 3: verify certificate expiry >30 days (BUSINESS INVARIANT, WARN on violation)."""
    # [IMP:9][issue-cert][main] BUSINESS INVARIANT: cert must be valid >30 days
    if not cert_check_expiry(cert_path, EXPIRY_THRESHOLD_SECONDS):
        _log_step("main", "WARN", "Certificate expires within 30 days — renew soon")


# endregion FUNC__verify_result


# region FUNC__finalize
## @purpose  Этап 5 (W6-D2): выпуск сертификатов независимых проектных доменов (Step 4) + DONE.
##           ВСЕГДА выполняется (не блокируется idempotency main — P1 TRAP).
## @io       ⇥ domain, email, dns_plugin, project_domains, ctx, challenge_mode, cert_is_le_issuer_fn → ⎋ None
## @complexity — O(D × T) — D = проектных доменов
## @invariants
##   - issue_project_certs: subdomain-skip, non-fatal на провал домена, идемпотентность
##   - Финальный DONE + IMP:9 лог (LDD-телеметрия завершения)
def _finalize(
    domain: str,
    email: str,
    dns_plugin: str,
    project_domains: list[str],
    ctx: IssueContext,
    challenge_mode: str,
    cert_is_le_issuer_fn: Callable[[str], bool] | None,
) -> None:
    """Step 4: project domain certs + DONE (ALWAYS processed — P1 TRAP guard)."""
    # [IMP:9][issue-cert][main] BUSINESS INVARIANT: independent project domains get
    # single-domain certs. Skips subdomains of PLATFORM_DOMAIN (covered by wildcard).
    # ALWAYS processed — NOT blocked by main cert idempotency check (see TRAP[BUG] above).
    issue_project_certs(
        domain,
        email,
        dns_plugin,
        project_domains,
        ctx,
        challenge_mode=challenge_mode,
        cert_is_le_issuer_fn=cert_is_le_issuer_fn,
    )

    _log_step("main", "DONE", f"SSL provisioning complete for {domain}")
    logger.info("[IMP:9][issue-cert][main] SSL provisioning complete: %s", domain or "(no domain)")


# endregion FUNC__finalize


# region FUNC_main
def main(_argv: list[str] | None = None) -> int:
    """CLI: `python3 -m core.internal.bootstrap.issue_cert` (subprocess-канал cert_orchestrator).

    ▶ ┌argv┐ → ○ logging stderr → ○ run(environ) → ⎋ exit 0|1

    ## @purpose  Composition root: env-контракт (PLATFORM_*/NODE_YAML/ACME_CHALLENGE_MODE/
    ##            WEBNAMES_API_KEY) читается из os.environ; аргументы CLI не принимаются
    ##            (НЕ автономный entrypoint — только subprocess-вызов оркестратора).
    ## @io — ⇥ argv: list[str] | None (игнорируется) → ⎋ int
    ## @complexity — O(D × T)
    ## @invariants
    ##   - sys.exit НЕ вызывается — main() возвращает int (канон core/AGENTS.md)
    ##   - Логи в stderr (stdout пуст — фасад/оркестратор читают stderr)
    """
    logging.basicConfig(level=logging.INFO, format="%(message)s", stream=sys.stderr)
    return run()


# endregion FUNC_main


if __name__ == "__main__":
    sys.exit(main())
