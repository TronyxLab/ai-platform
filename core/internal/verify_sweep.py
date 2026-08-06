#!/usr/bin/env python3
# GREP_SUMMARY: verify-sweep, e2e-verify, sweep, endpoints, http-check, tls-check, openssl-s-client, wildcard-san, expected-codes, by-design, json-report, exit-contract, devplan-136
# STRUCTURE: ▶ collect_endpoints ┌node, mode┐ → ◇ local (node.yaml projects + overlays/nginx server_names) | ◇ remote (ssh cat conf.d)
#            → ○ check_http ┌ep┐ → curl --resolve → classify by-design (200/301/302/401/403/404/444 pass, 5xx fail)
#            → ○ check_tls ┌ep┐ → openssl s_client → chain ⊕ SAN wildcard match ⊕ expiry (WARN<14d / FAIL expired)
#            → ⊕ verdict table (+ --json) → ⎋ exit 0 (all ok) | 1 (FAIL) | 2 (config error)
# region MODULE_CONTRACT
## @purpose  Endpoint sweep verification for `make e2e-verify` (DevPlan 136 W5, T5.1-T5.6).
##           Collects all HTTP(S) endpoints of a node (local: node.yaml projects + rendered
##           vhost server_names; remote: SSH-read nginx conf.d), then sweeps each endpoint
##           with check_http (curl, expected_codes by-design classification) and check_tls
##           (openssl s_client — chain depth, wildcard SAN matching, expiry WARN<14d / FAIL).
##           Accepts the deployment as a SCRIPT: exit 0 when every endpoint verdict is
##           green — «всё 200 + зелёные» is a command, not a manual table (DevPlan 136 §4.2).
## @scope    New module per DevPlan 136 §2.2/§3 data flow. Consumed by:
##           - makefiles/ci.mk e2e-verify target (T5.2) → `python3 -m core.internal.verify_sweep sweep`
##           - core/check-suite.yaml record id: e2e-verify (T5.3, diagnostic-only)
##           - W6.7 (server-агент): `make e2e-verify NODE=test-e2e` — таблица + exit 0
##           Unit-tested by tests/unit/test_verify_sweep.py (T5.5).
## @invariants
##   I1: Exit contract 0/1/2: 0 = all endpoints OK; 1 = ≥1 FAIL verdict (HTTP/TLS/connectivity,
##       SSH unavailable in remote mode — R4: FAIL, не skip); 2 = config error (node.yaml
##       not found, node.host unresolvable). sys.exit — только в main().
##   I2: expected_codes classification by-design: 200 OK; 301/302 redirect by-design;
##       401/403 auth by-design; 404/444 deny by-design; 502/504 upstream FAIL; любые
##       другие non-200 (500/503/...) → FAIL. Expected_codes не «все коды OK» — у FAIL
##       кодов нет легального by-design статуса (DevPlan 136 §5.5, риск «ложные FAIL»).
##   I3: check_tls работает через openssl s_client к {host}:443 c SNI {fqdn}; leaf PEM
##       извлекается из вывода, записывается во временный файл; expiry — через
##       shared/ssl_certs.cert_check_expiry (реюз T5.6) + локальный _cert_days_left
##       для различения WARN (<14d) / FAIL (expired); SAN — локальный wildcard-матчинг.
##   I4: HTTP-проверка использует `curl --resolve {fqdn}:443:{host}` — DNS закреплён за
##       IP ноды (nginx server_name матчится по Host), не зависит от публичного DNS.
##   I5: R4-семантика: node.yaml отсутствует / node.host не резолвится → exit 2 (config);
##       SSH недоступен (remote collect) / соединение к endpoint падает → FAIL verdict (exit 1).
##       Skip-путей нет.
##   I6: 0 endpoints (голая test-e2e нода) → exit 0 с IMP:9 «0 endpoints — nothing to sweep»
##       (паритет domain_verifier «no expose:true domains → PASS», DevPlan 136 AC W5).
##   I7: main() -> int (канон core/AGENTS.md); business-функции НЕ вызывают sys.exit;
##       исключения → PlatformError-паттерн, никогда bare except.
## @rationale Q: Почему новый модуль, а не расширение domain_verifier.py?
##            A: domain_verifier/verify-domains.sh — post-deploy HTTPS-проверка «всё == 200»
##            (один проект или все expose:true; 0 by-design кодов). e2e-verify — sweep с
##            expected_codes-классификацией (302/401/404 не FAIL — иначе «ложные FAIL» на
##            живой ноде, DevPlan 136 §9 риск) + TLS-проверка (chain/SAN/expiry), которой в
##            domain_verifier НЕТ (curl не отдаёт SAN/notAfter). Расширение domain_verifier
##            сломало бы его CI-семантику «non-200 = warn» (DevPlan 125 T1) и смешало бы
##            два вердикта. Отдельный sweep-модуль — единственный путь без регрессии.
##            Q: Почему DRY-реюз TLS (T5.6) не полный?
##            A: verify-domains.sh — тонкий shell-фасад (46 LOC) без TLS-логики: его Python-
##            делегат domain_verifier.py делает только curl-HTTP. Единственная переиспользуемая
##            TLS-логика — shared/ssl_certs.py (локальные файлы, -checkend bool, CN-only
##            match). e2e-verify требует сетевой fetch (openssl s_client — нового), SAN-парсинг
##            (нет в ssl_certs) и дату истечения (для WARN<14d vs FAIL — нет в -checkend).
##            Реюз: cert_check_expiry + cert_is_le_issuer на извлечённом PEM; новый:
##            s_client-fetch, _extract_leaf_cert, san_matches_domain, _cert_days_left.
## @changes  2026-08-05 | DevPlan 136 W5 — Created (T5.1)
## @usecases
##   - Оператор/агент: `make e2e-verify NODE=tronyx-vps` — таблица endpoint→HTTP→TLS→вердикт,
##     exit 0 = приёмка «всё зелёное» скриптом (W6.7).
##   - QA/диагностика: `make e2e-verify NODE=<n> JSON=1` — machine-readable отчёт.
##   - Bare test-node (W6): 0 endpoints → exit 0 (истинная приёмка голой ноды).
# endregion MODULE_CONTRACT

from __future__ import annotations

import argparse
import json
import logging
import os
import re
import subprocess
import sys
import tempfile
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

from core.internal.shared.exceptions import (
    ConfigNotFoundError,
    ConfigParseError,
    ConfigValidationError,
    PlatformError,
)
from core.internal.shared.node_yaml import NodeYaml
from core.internal.shared.node_yaml.projects import ProjectEntry
from core.internal.shared.timeouts import SSH_CONNECT_TIMEOUT

# Единый SSH-раннер (DevPlan 139 W3 T4): verbatim-копия удалена, канон — vps_readiness.
# Сигнатура (host, user, cmd, timeout, ssh_lib_path=None) -> tuple[int, str] идентична;
# timeout-семантика (Python-level = bash timeout + 5s) сохранена без изменений.
# Гейт ssh_opts_sole_path остаётся зелёным (ssh.sh фасад не тронут).
from core.internal.shared.vps_readiness import default_ssh_runner

logger = logging.getLogger(__name__)

# ── Constants (единственный источник литералов домена e2e-verify) ──────────
CURL_TIMEOUT_DEFAULT: int = 10
"""## @invariant HTTP-таймаут curl --max-time (сек)."""

OPENSSL_TIMEOUT_DEFAULT: int = 10
"""## @invariant Таймаут openssl s_client / x509 subprocess (сек)."""

EXPIRY_WARN_DAYS: int = 14
"""## @invariant Порог WARN: <14 дней до истечения сертификата — WARN (не FAIL)."""

SSL_PORT: int = 443
"""## @invariant Порт TLS-проверки (nginx 443 ssl)."""

DEFAULT_SSH_USER: str = "ci-deploy"
"""## @invariant SSH-пользователь remote-collect (паритет vps_readiness.SSH_USER)."""

REMOTE_NGINX_CONF_DIR: str = "/etc/nginx/conf.d/overlay"
"""## @invariant Remote-директория nginx vhost conf.d (include /etc/nginx/conf.d/overlay/*.conf, nginx_harness.py:123)."""

_NODE_HOST_MAP_ENV = "NODE_HOST_MAP"

# Коды HTTP по дизайну (I2). FAIL-коды (502/504/5xx) легального by-design статуса не имеют.
_BY_DESIGN_OK_CODES: frozenset[int] = frozenset({200, 301, 302, 401, 403, 404, 444})
_BY_DESIGN_FAIL_CODES: frozenset[int] = frozenset({502, 504})
"""## @invariant by-design классификация: OK-коды — 200/301/302/401/403/404/444; FAIL — 502/504; иное → FAIL."""

_CERT_BLOCK_RE = re.compile(r"-----BEGIN CERTIFICATE-----.*?-----END CERTIFICATE-----", re.DOTALL)
_SERVER_NAME_RE = re.compile(r"\bserver_name\s+(.+?)\s*;", re.MULTILINE)


# region DATA_CLASSES


@dataclass
class Endpoint:
    """Один endpoint для sweep-верификации.

    ## @purpose — Целевая точка проверки: fqdn + IP ноды (host) + источник коллекции.
    ## @io — ⇥ источник (node.yaml project | vhost conf | remote nginx) → ⎋ Endpoint
    ## @invariants
    ##   - fqdn — lowercase FQDN (server_name / project.domain)
    ##   - host — IP ноды (node.host), для --resolve и s_client -connect
    ##   - expected — опциональный per-endpoint allowlist кодов (None → by-design классификация)
    """

    name: str
    fqdn: str
    host: str
    source: str = "node-yaml"
    expected: list[int] | None = None


@dataclass
class HttpResult:
    """Результат HTTP-проверки endpoint.

    ## @purpose — code + by-design вердикт (pass/warn/fail) для агрегации.
    ## @invariants — code None при connection error (вердикт fail); ok = вердикт != fail.
    """

    fqdn: str
    code: int | None = None
    verdict: str = "fail"
    error: str | None = None

    @property
    def ok(self) -> bool:
        """Вердикт не FAIL (pass | warn)."""
        return self.verdict != "fail"


@dataclass
class TlsResult:
    """Результат TLS-проверки endpoint (openssl s_client + x509).

    ## @purpose — chain depth, SAN-матчинг, expiry (WARN<14d / FAIL expired), issuer.
    ## @invariants — chain_depth 0 при отсутствии сертификата (вердикт fail);
    ##   days_left None при непарсируемой дате; verdict ∈ {ok, warn, fail}.
    """

    fqdn: str
    chain_depth: int = 0
    san_ok: bool | None = None
    days_left: int | None = None
    verdict: str = "fail"
    error: str | None = None
    issuer: str | None = None

    @property
    def ok(self) -> bool:
        """Вердикт не FAIL (ok | warn)."""
        return self.verdict != "fail"


@dataclass
class SweepReport:
    """Агрегированный отчёт sweep-прогона.

    ## @purpose — все вердикты + финальный exit-код (0/1) и JSON-сериализация.
    ## @invariants — exit_code 0 iff нет FAIL verdict'ов и нет collection-ошибок.
    """

    node: str
    mode: str
    http: list[HttpResult] = field(default_factory=list)
    tls: list[TlsResult] = field(default_factory=list)
    collect_errors: list[str] = field(default_factory=list)
    endpoints: int = 0

    def to_dict(self) -> dict:
        """JSON-совместимый dict отчёта (данные → json.dumps, I7)."""
        return {
            "node": self.node,
            "mode": self.mode,
            "endpoints": self.endpoints,
            "http": [{"fqdn": r.fqdn, "code": r.code, "verdict": r.verdict, "error": r.error} for r in self.http],
            "tls": [
                {
                    "fqdn": r.fqdn,
                    "chain_depth": r.chain_depth,
                    "san_ok": r.san_ok,
                    "days_left": r.days_left,
                    "verdict": r.verdict,
                    "error": r.error,
                }
                for r in self.tls
            ],
            "collect_errors": self.collect_errors,
        }

    @property
    def exit_code(self) -> int:
        """Финальный exit: 0 = все зелёные, 1 = ≥1 FAIL / collection error."""
        if self.collect_errors:
            return 1
        for r in (*self.http, *self.tls):
            if not r.ok:
                return 1
        return 0


# endregion DATA_CLASSES


# region EXCEPTIONS


class EndpointCollectionError(PlatformError):
    """Хард-ошибка сбора endpoints (R4: ssh-недоступен → FAIL; конфиг-ошибки → exit 2).

    ## @purpose — Единый сигнал сбоя коллекции: main() решает exit 1 (FAIL) vs exit 2 (config).
    ## @io — ⇥ message + exit_code (1 = operational FAIL, 2 = config error) → ⎋ исключение
    """

    def __init__(self, message: str, exit_code: int = 1) -> None:
        super().__init__(message)
        self.exit_code = exit_code


# endregion EXCEPTIONS


# region PURE_PARSERS


# region FUNC_parse_nginx_server_names
def parse_nginx_server_names(conf_text: str) -> list[str]:
    """Извлечь server_name FQDN из nginx conf текста (80/443 server блоки).

    ▶ ┌conf_text┐ → ○ re server_name (…;) → ⊕ split + strip ';' → ○ lowercase + dedup → ⎋ list[str]

    ## @purpose — Pure-парсер server_name директив nginx (vhost .conf и remote conf.d cat).
    ##            Используется локальной (overlays/nginx/*.conf) и remote (ssh cat) коллекцией.
    ## @io — ⇥ conf_text: str — содержимое одного/нескольких nginx conf файлов
    ##       → ⎋ list[str] — lowercase уникальные FQDN (пустой при отсутствии server_name)
    ## @complexity — O(L) где L = строки конфига
    ## @invariants
    ##   - server_name может содержать несколько имён через пробел — все извлекаются
    ##   - Терминальная ';' обрезается; регистр нормализуется в lowercase
    ##   - Пустые значения / подчёркнутые виртуальные имена (_) игнорируются
    ##   - Duplicate FQDN → уникализируются (set)
    ##   - Невалидный текст → пустой список (не raise — graceful)
    """
    names: list[str] = []
    for match in _SERVER_NAME_RE.finditer(conf_text):
        raw = match.group(1).strip()
        for token in raw.split():
            token = token.strip().rstrip(";")
            if not token or token == "_":
                continue
            token = token.lower()
            if token not in names:
                names.append(token)
    logger.info("[IMP:9][parse_nginx_server_names] Parsed %d server_name(s)", len(names))
    return names


# endregion FUNC_parse_nginx_server_names


# region FUNC_san_matches_domain
def san_matches_domain(fqdn: str, san: str) -> bool:
    """Проверить, покрывает ли SAN (subjectAltName) домен — точное или wildcard-совпадение.

    ▶ ┌fqdn + san┐ → ◇ exact (case-insens) → ◇ '*.domain' wildcard (ровно один уровень)
      → ⊕ True | ⎋ False

    ## @purpose — Wildcard SAN-матчинг для check_tls (DevPlan 136 T5.1: «wildcard SAN-матчинг»).
    ##            Отличается от shared/ssl_certs.cert_subject_matches_domain (CN-only, подстрока):
    ##            SAN-домен сравнивается полностью, '*.example.com' покрывает ТОЛЬКО
    ##            <label>.example.com, НЕ глубже и НЕ сам apex.
    ## @io — ⇥ fqdn: str — проверяемый FQDN (lowercase на входе вызывающего)
    ##       ⇥ san: str — SAN из сертификата (может содержать 'DNS:' префикс — срезается)
    ##       → ⎋ bool — True если SAN покрывает fqdn
    ## @complexity — O(N) где N = len(fqdn)
    ## @invariants
    ##   - Точное совпадение (example.com == example.com) → True
    ##   - Wildcard '*.example.com' матчит ровно один уровень: api.example.com ✅,
    ##     example.com ❌, deep.api.example.com ❌ (одноуровневая семантика wildcard)
    ##   - 'DNS:' префикс SAN срезается (openssl x509 -ext subjectAltName формат)
    ##   - Регистро-независимо (lowercase с обеих сторон)
    ##   - '*' не в первом сегменте (foo.*.com) → False (невалидный wildcard)
    ##   - Пустой fqdn/san → False (fail-fast, никогда True)
    """
    fqdn_l = fqdn.strip().lower()
    san_l = san.strip().lower()
    if san_l.startswith("dns:"):
        san_l = san_l[4:].strip()

    if not fqdn_l or not san_l:
        logger.info("[IMP:8][san_matches_domain] Empty fqdn or san — False")
        return False

    if fqdn_l == san_l:
        logger.info("[IMP:9][san_matches_domain] Exact SAN match: %s", san_l)
        return True

    if san_l.startswith("*."):
        wildcard_suffix = san_l[1:]  # ".example.com"
        if fqdn_l.endswith(wildcard_suffix):
            prefix = fqdn_l[: -len(wildcard_suffix)]
            if prefix and "." not in prefix:
                logger.info("[IMP:9][san_matches_domain] Wildcard SAN %s matches %s", san_l, fqdn_l)
                return True

    logger.info("[IMP:8][san_matches_domain] SAN %s does NOT match %s", san_l, fqdn_l)
    return False


# endregion FUNC_san_matches_domain


# region FUNC_classify_http_code
def classify_http_code(code: int, expected: list[int] | None = None) -> str:
    """By-design классификация HTTP-кода endpoint (expected_codes, I2).

    ▶ ┌code, expected┐ → ◇ expected задан? → code ∈ expected → 'pass' | 'fail'
      → ◇ 200/301/302/401/403/404/444 → 'pass' → ◇ 502/504 → 'fail' → ⎋ 'fail' (иное)

    ## @purpose — expected_codes классификация по дизайну: 404/444 deny и 401/403 auth НЕ
    ##            считаются FAIL на живом endpoint (иначе ложные FAIL, DevPlan 136 §9).
    ##            per-endpoint expected allowlist переопределяет by-design набор.
    ## @io — ⇥ code: int — HTTP-код ответа; expected: list[int] | None — per-endpoint allowlist
    ##       → ⎋ str — 'pass' | 'fail' (warn не используется HTTP-классификацией)
    ## @complexity — O(1)
    ## @invariants
    ##   - expected задан: code ∈ expected → 'pass', иначе 'fail' (allowlist строгий)
    ##   - expected=None: by-design OK = {200,301,302,401,403,404,444}; FAIL = {502,504} + всё иное
    ##   - Ни один FAIL-код (502/504) не имеет легального by-design статуса — always fail
    ##   - Вердикт 'fail' → exit 1 (единственная fail-ветка HTTP)
    """
    if expected is not None:
        verdict = "pass" if code in expected else "fail"
        logger.info("[IMP:9][classify_http_code] code=%d expected=%r → %s", code, expected, verdict)
        return verdict

    if code in _BY_DESIGN_OK_CODES:
        logger.info("[IMP:9][classify_http_code] code=%d by-design OK → pass", code)
        return "pass"
    if code in _BY_DESIGN_FAIL_CODES:
        logger.info("[IMP:9][classify_http_code] code=%d by-design FAIL → fail", code)
        return "fail"

    logger.info("[IMP:9][classify_http_code] code=%d unexpected → fail", code)
    return "fail"


# endregion FUNC_classify_http_code


# region FUNC_expiry_verdict
def expiry_verdict(days_left: int | None) -> str:
    """Expiry-вердикт по дням до истечения (WARN<14d / FAIL при истечении).

    ▶ ┌days_left┐ → ◇ None → 'fail' (непарсируемая дата — fail-verbose) → ◇ <0 → 'fail'
      → ◇ <14 → 'warn' → ⎋ 'ok'

    ## @purpose — Пороговая логика WARN/FAIL для check_tls expiry (I3): WARN при <14 дней,
    ##            FAIL при истечении (days_left < 0). None (непарсируемая дата) — FAIL
    ##            (fail-verbose, никогда молчаливый pass).
    ## @io — ⇥ days_left: int | None — дней до notAfter → ⎋ str ('ok'|'warn'|'fail')
    ## @complexity — O(1)
    ## @invariants
    ##   - days_left < 0 → 'fail' (сертификат истёк)
    ##   - 0 <= days_left < 14 → 'warn' (порог WARN, константа EXPIRY_WARN_DAYS)
    ##   - days_left >= 14 → 'ok'
    ##   - None → 'fail' (нельзя подтвердить — FAIL, не skip; R4-дух)
    """
    if days_left is None:
        logger.info("[IMP:9][expiry_verdict] No parseable expiry date → fail")
        return "fail"
    if days_left < 0:
        logger.info("[IMP:9][expiry_verdict] %d days left (<0) → fail (expired)", days_left)
        return "fail"
    if days_left < EXPIRY_WARN_DAYS:
        logger.info("[IMP:9][expiry_verdict] %d days left (<%d) → warn", days_left, EXPIRY_WARN_DAYS)
        return "warn"
    logger.info("[IMP:9][expiry_verdict] %d days left (>=%d) → ok", days_left, EXPIRY_WARN_DAYS)
    return "ok"


# endregion FUNC_expiry_verdict


# endregion PURE_PARSERS


# region COLLECT_ENDPOINTS


# region FUNC_collect_endpoints
def collect_endpoints(
    node: str,
    *,
    mode: str = "remote",
    node_configs_dir: str | None = None,
    platform_root: str | None = None,
    ssh_runner: Callable[[str, str, str, int], tuple[int, str]] | None = None,
    remote_conf_dir: str = REMOTE_NGINX_CONF_DIR,
    ssh_user: str = DEFAULT_SSH_USER,
) -> list[Endpoint]:
    """Собрать endpoints ноды: local (node.yaml + overlays/nginx) или remote (ssh nginx conf.d).

    ▶ ┌node, mode┐ → ◇ resolve node.yaml (3-path) → ◇ node.host
      → ◇ mode=local? read_node_yaml_projects + parse overlays/nginx/*.conf
      → ◇ mode=remote? ssh cat {remote_conf_dir}/*.conf → parse server_names
      → ⊕ Endpoint(name, fqdn, host) dedup by fqdn → ⎋ list[Endpoint]

    ## @purpose — Источники endpoints по дизайну (DevPlan 136 §2.2): local — node.yaml
    ##            projects (NodeYaml.get_project_entries — единственный парсер node.yaml,
    ##            инвариант 13) + server_names из рендеренных vhost .conf; remote — SSH
    ##            чтение nginx conf.d (conf-парсинг, R4: ssh-недоступен → EndpointCollectionError
    ##            exit 1, не skip). host (IP ноды) резолвится: NODE_HOST_MAP env → node.host.
    ## @io — ⇥ node: str; mode: 'local'|'remote'; node_configs_dir: str | None (локальный
    ##         поиск node.yaml + overlays); platform_root: str | None; ssh_runner DI;
    ##         remote_conf_dir: str; ssh_user: str
    ##       → ⎋ list[Endpoint] (dedup by fqdn; пустой на голой ноде)
    ## @complexity — O(P + F) где P = проекты node.yaml, F = conf-файлы (+ SSH round-trip в remote)
    ## @raises — EndpointCollectionError: node.yaml не найден (exit 2), node.host пуст (exit 2),
    ##           SSH недоступен (remote, exit 1 — R4 FAIL)
    ## @invariants
    ##   - node.yaml резолвится через NodeYaml.resolve (3-path канон)
    ##   - Проекты парсятся ТОЛЬКО NodeYaml.get_project_entries (не yaml.safe_load)
    ##   - Endpoint.host = node.host; NODE_HOST_MAP (JSON env) имеет приоритет (паритет vps_readiness)
    ##   - Local: vhost .conf server_names добавляются как дополнительные endpoints (дрейф-детект:
    ##     задеплоенные vhost'ы, которых нет в node.yaml, всё равно проверяются)
    ##   - Remote: `cat {remote_conf_dir}/*.conf` через ssh_runner (пустой вывод = 0 endpoints,
    ##     НЕ ошибка — голый тест-node)
    ##   - Dedup по fqdn: первый источник выигрывает (node.yaml проект важнее vhost-conf)
    """
    logger.info("[IMP:7][collect_endpoints] node=%s mode=%s", node, mode)

    # ── Step 1: resolve node.yaml (3-path канон) ────────────────────
    try:
        node_yaml_path = _resolve_node_yaml_path(node, platform_root)
    except ConfigNotFoundError as exc:
        raise EndpointCollectionError(str(exc), exit_code=2) from exc

    # ── Step 2: resolve node.host (NODE_HOST_MAP env → node.host) ───
    host = _resolve_node_host(node, node_yaml_path)
    if not host:
        raise EndpointCollectionError(
            f"node.host not resolvable for node {node!r} (NODE_HOST_MAP env or node.yaml#node.host)",
            exit_code=2,
        )
    logger.info("[IMP:8][collect_endpoints] Resolved host=%s for node=%s", host, node)

    endpoints: list[Endpoint] = []

    if mode == "local":
        endpoints.extend(_collect_local(node_yaml_path, node_configs_dir, node, host))
    elif mode == "remote":
        endpoints.extend(_collect_remote(node, host, ssh_runner, remote_conf_dir, ssh_user))
    else:
        raise EndpointCollectionError(f"Unknown mode {mode!r} — expected 'local' | 'remote'", exit_code=2)

    # ── Step 3: dedup by fqdn (первый источник выигрывает) ──────────
    seen: set[str] = set()
    unique: list[Endpoint] = []
    for ep in endpoints:
        if ep.fqdn in seen:
            logger.info("[IMP:7][collect_endpoints] Dedup fqdn=%s (keep first source=%s)", ep.fqdn, ep.source)
            continue
        seen.add(ep.fqdn)
        unique.append(ep)

    logger.info("[IMP:9][collect_endpoints] Collected %d unique endpoint(s) mode=%s", len(unique), mode)
    return unique


# endregion FUNC_collect_endpoints


# region FUNC__resolve_node_yaml_path
def _resolve_node_yaml_path(node: str, platform_root: str | None) -> str:
    """3-path резолв node.yaml через NodeYaml.resolve (канон).

    ▶ ┌node, platform_root┐ → NodeYaml.resolve (env PLATFORM_ROOT/HOME) → ⎋ path
    ## @purpose — Единая точка резолва node.yaml (делегирует NodeYaml.resolve, инвариант
    ##            «единая точка чтения node.yaml»). platform_root → config_dir (hermetic DI).
    ## @io — ⇥ node: str; platform_root: str | None → ⎋ str (абсолютный путь)
    ## @raises — ConfigNotFoundError (не найдён ни в одном из 3 путей)
    ## @complexity — O(P + N) — 3-path probe + YAML parse
    """
    resolved = NodeYaml.resolve(node_name=node, config_dir=platform_root)
    path = str(resolved._path)
    logger.info("[IMP:9][_resolve_node_yaml_path] Resolved node.yaml: %s", path)
    return path


# endregion FUNC__resolve_node_yaml_path


# region FUNC__resolve_node_host
def _resolve_node_host(node: str, node_yaml_path: str) -> str:
    """Резолв IP ноды: NODE_HOST_MAP (JSON env) приоритетнее node.yaml#node.host.

    ▶ ┌node, node_yaml_path┐ → ◇ NODE_HOST_MAP JSON → host | ◇ NodeYaml.get(node.host) → ⎋ str

    ## @purpose — Единый резолв host для checks и SSH (паритет vps_readiness._resolve_node_host:
    ##            NODE_HOST_MAP env — JSON node→host; fallback — node.yaml#node.host).
    ## @io — ⇥ node: str; node_yaml_path: str → ⎋ str (host или '')
    ## @complexity — O(1) + YAML parse
    ## @invariants
    ##   - NODE_HOST_MAP валиден JSON + node в нём → env выигрывает
    ##   - Иначе → NodeYaml.get("node.host", default="")
    ##   - Пусто в обоих → '' (вызывающий решает exit 2)
    """
    raw_map = os.environ.get(_NODE_HOST_MAP_ENV)
    if raw_map:
        try:
            node_host_map = json.loads(raw_map)
            if isinstance(node_host_map, dict) and node_host_map.get(node):
                host = str(node_host_map[node])
                logger.info("[IMP:9][_resolve_node_host] NODE_HOST_MAP: %s → %s", node, host)
                return host
        except json.JSONDecodeError:
            logger.warning("[IMP:7][_resolve_node_host] NODE_HOST_MAP is not valid JSON — falling back to node.yaml")

    try:
        ny = NodeYaml(node_yaml_path)
        host = str(ny.get("node.host", default="") or "")
    except (ConfigNotFoundError, ConfigParseError) as exc:
        logger.warning("[IMP:7][_resolve_node_host] node.yaml unreadable: %s", exc)
        return ""
    if host:
        logger.info("[IMP:9][_resolve_node_host] node.yaml#node.host: %s", host)
    return host


# endregion FUNC__resolve_node_host


# region FUNC__collect_local
def _collect_local(node_yaml_path: str, node_configs_dir: str | None, node: str, host: str) -> list[Endpoint]:
    """Локальная коллекция: node.yaml projects (domain) + overlays/nginx server_names.

    ▶ ┌node_yaml_path, node_configs_dir┐ → ○ NodeYaml.get_project_entries → ⊕ projects-with-domain
      → ○ glob overlays/nginx/*.conf → ⊕ parse_nginx_server_names → ⎋ list[Endpoint]

    ## @purpose — Источник №1 (node.yaml projects с domain) + источник №2 (рендеренные
    ##            vhost .conf server_names — дрейф-детект задеплоенных доменов).
    ## @io — ⇥ node_yaml_path: str; node_configs_dir: str | None; node: str; host: str
    ##       → ⎋ list[Endpoint]
    ## @complexity — O(P + F) где P = проекты, F = conf-файлы
    ## @invariants
    ##   - Проект без domain → пропускается (не endpoint — не HTTP-рутируется)
    ##   - overlays_dir = <node_configs_dir>/<node>/overlays/nginx (если node_configs_dir задан);
    ##     отсутствие директории → 0 дополнительных endpoints (не ошибка)
    ##   - Парсинг conf-файлов — parse_nginx_server_names (pure)
    ##   - source: 'node-yaml' | 'vhost-conf' (для дрейф-аудита)
    """
    endpoints: list[Endpoint] = []

    try:
        ny = NodeYaml(node_yaml_path)
        entries: list[ProjectEntry] = ny.get_project_entries()
    except (ConfigNotFoundError, ConfigParseError, ConfigValidationError) as exc:
        logger.warning("[IMP:7][_collect_local] node.yaml projects unreadable: %s", exc)
        entries = []

    for entry in entries:
        fqdn = entry.domain.strip().lower()
        if not fqdn:
            logger.info("[IMP:7][_collect_local] Project %s has no domain — skip", entry.name)
            continue
        endpoints.append(Endpoint(name=entry.name, fqdn=fqdn, host=host, source="node-yaml"))
    logger.info("[IMP:8][_collect_local] %d endpoint(s) from node.yaml projects", len(endpoints))

    if node_configs_dir:
        overlays_dir = Path(node_configs_dir) / node / "overlays" / "nginx"
        if overlays_dir.is_dir():
            for conf_file in sorted(overlays_dir.glob("*.conf")):
                try:
                    conf_text = conf_file.read_text(encoding="utf-8")
                except OSError as exc:
                    logger.warning("[IMP:7][_collect_local] Unreadable vhost conf %s: %s", conf_file, exc)
                    continue
                endpoints.extend(
                    Endpoint(name=conf_file.stem, fqdn=fqdn, host=host, source="vhost-conf")
                    for fqdn in parse_nginx_server_names(conf_text)
                )
            logger.info("[IMP:8][_collect_local] Scanned overlays/nginx: %s", overlays_dir)
        else:
            logger.info("[IMP:7][_collect_local] No overlay dir %s — vhost-conf source empty", overlays_dir)

    logger.info("[IMP:9][_collect_local] %d local endpoint(s)", len(endpoints))
    return endpoints


# endregion FUNC__collect_local


# region FUNC__collect_remote
def _collect_remote(
    node: str,
    host: str,
    ssh_runner: Callable[[str, str, str, int], tuple[int, str]] | None,
    remote_conf_dir: str,
    ssh_user: str,
) -> list[Endpoint]:
    """Remote-коллекция: SSH чтение nginx conf.d → parse server_names (conf-парсинг).

    ▶ ┌node, host┐ → ⚡ ssh cat {remote_conf_dir}/*.conf → ◇ rc!=0 → EndpointCollectionError(exit 1, R4)
      → ○ parse_nginx_server_names → ⊕ Endpoint(name=file, fqdn, host, source='remote-nginx') → ⎋ list

    ## @purpose — Источник remote (DevPlan 136 T5.1: «remote — через SSH чтение nginx conf.d»):
    ##            фактические задеплоенные server_names на ноде (истинное состояние).
    ##            R4: ssh-недоступен → EndpointCollectionError(exit_code=1) — FAIL, не skip.
    ## @io — ⇥ node: str; host: str; ssh_runner DI (None → default_ssh_runner);
    ##         remote_conf_dir: str; ssh_user: str → ⎋ list[Endpoint]
    ## @complexity — O(1) SSH round-trip + O(L) парсинг
    ## @raises — EndpointCollectionError(exit_code=1): ssh rc != 0 / timeout / bash missing
    ## @invariants
    ##   - Команда: `cat {remote_conf_dir}/*.conf` (shell glob на remote-стороне)
    ##   - Пустой stdout (нет conf.d файлов) → 0 endpoints (голый test-node, НЕ ошибка)
    ##   - ssh_runner DI (host, user, cmd, timeout) -> (rc, stdout) — паттерн vps_readiness
    ##   - Таймаут SSH: SSH_CONNECT_TIMEOUT (shared/timeouts канон)
    """
    if ssh_runner is None:
        ssh_runner = default_ssh_runner

    cmd = f"cat {remote_conf_dir}/*.conf 2>/dev/null"
    logger.info("[IMP:7][_collect_remote] SSH %s@%s: %s", ssh_user, host, cmd)
    rc, stdout = ssh_runner(host, ssh_user, cmd, SSH_CONNECT_TIMEOUT)
    if rc != 0:
        raise EndpointCollectionError(
            f"SSH unavailable for remote collect: {ssh_user}@{host} rc={rc} (R4: FAIL, not skip)",
            exit_code=1,
        )

    names = parse_nginx_server_names(stdout)
    endpoints = [
        Endpoint(name=f"remote-nginx-{i}", fqdn=fqdn, host=host, source="remote-nginx") for i, fqdn in enumerate(names)
    ]
    logger.info("[IMP:9][_collect_remote] %d remote endpoint(s) via SSH conf.d read", len(endpoints))
    return endpoints


# endregion FUNC__collect_remote


# endregion COLLECT_ENDPOINTS


# region CHECKS


# region FUNC_check_http
def check_http(
    ep: Endpoint,
    *,
    timeout: int = CURL_TIMEOUT_DEFAULT,
    curl_runner: Callable[[list[str], int], subprocess.CompletedProcess] | None = None,
) -> HttpResult:
    """HTTP-проверка endpoint: curl --resolve {fqdn}:443:{host} → by-design классификация.

    ▶ ┌ep┐ → ⚡ curl -sS -o /dev/null -w '%{http_code}' --max-time t --resolve f:443:host https://f/
      → ◇ rc/TimeoutExpired → fail | ◇ code → classify_http_code → ⎋ HttpResult

    ## @purpose — Проверка доступности endpoint по HTTPS с DNS-закреплением за IP ноды (I4).
    ##            Вердикт — by-design классификация (I2): 404/444 deny, 401/403 auth,
    ##            301/302 redirect — pass; 502/504/5xx — fail; connection error — fail (R4).
    ## @io — ⇥ ep: Endpoint; timeout: int; curl_runner DI (None → subprocess.run)
    ##       → ⎋ HttpResult (code, verdict, error)
    ## @complexity — O(1) — один subprocess curl
    ## @invariants
    ##   - `--resolve {ep.fqdn}:443:{ep.host}` — DNS pinned (не зависит от публичного DNS)
    ##   - Выход из stdout: %{http_code}; connection error → verdict fail (не skip)
    ##   - subprocess.TimeoutExpired → fail c ошибкой (fail-verbose)
    ##   - per-endpoint ep.expected allowlist → classify_http_code(code, ep.expected)
    """
    url = f"https://{ep.fqdn}/"
    cmd = [
        "curl",
        "-sS",
        "-o",
        "/dev/null",
        "-w",
        "%{http_code}",
        "--max-time",
        str(timeout),
        "--resolve",
        f"{ep.fqdn}:{SSL_PORT}:{ep.host}",
        url,
    ]
    logger.info("[IMP:7][check_http] curl %s (resolve → %s:%d)", url, ep.host, SSL_PORT)

    runner = curl_runner or _default_curl_runner
    try:
        result = runner(cmd, timeout + 5)
    except subprocess.TimeoutExpired:
        logger.info("[IMP:10][check_http] Timeout for %s", ep.fqdn)
        return HttpResult(fqdn=ep.fqdn, code=None, verdict="fail", error=f"Connection timed out (>{timeout}s)")

    if result.returncode != 0:
        logger.info("[IMP:9][check_http] Connection failed for %s (curl exit %d)", ep.fqdn, result.returncode)
        return HttpResult(
            fqdn=ep.fqdn,
            code=None,
            verdict="fail",
            error=f"Connection failed (curl exit {result.returncode})",
        )

    try:
        code = int(result.stdout.strip())
    except ValueError:
        logger.info("[IMP:10][check_http] Unparseable HTTP code for %s: %r", ep.fqdn, result.stdout)
        return HttpResult(fqdn=ep.fqdn, code=None, verdict="fail", error="Unparseable HTTP code")

    verdict = classify_http_code(code, ep.expected)
    logger.info("[IMP:9][check_http] %s — HTTP %s verdict=%s", ep.fqdn, code, verdict)
    return HttpResult(fqdn=ep.fqdn, code=code, verdict=verdict)


# endregion FUNC_check_http


# region FUNC_check_tls
def check_tls(
    ep: Endpoint,
    *,
    timeout: int = OPENSSL_TIMEOUT_DEFAULT,
    s_client_runner: Callable[[list[str], int], subprocess.CompletedProcess] | None = None,
) -> TlsResult:
    """TLS-проверка endpoint: openssl s_client → chain + SAN wildcard + expiry (I3).

    ▶ ┌ep┐ → ⚡ openssl s_client -connect host:443 -servername fqdn -showcerts
      → ◇ rc!=0 → fail → ○ _extract_leaf_cert → ⊕ tmp PEM
      → ◇ SAN parse (openssl x509 -ext subjectAltName) → san_matches_domain
      → ◇ _cert_days_left + ssl_certs.cert_check_expiry (реюз T5.6) → expiry_verdict
      → ⊕ chain_depth + issuer (cert_is_le_issuer) → ⎋ TlsResult

    ## @purpose — TLS-стек проверки (DevPlan 136 T5.1): chain (число сертификатов в выводе
    ##            s_client), wildcard SAN-матчинг fqdn против subjectAltName, expiry
    ##            WARN<14d / FAIL при истечении. Реюз shared/ssl_certs (T5.6) на извлечённом
    ##            leaf PEM: cert_check_expiry (порог 14 дней) + cert_is_le_issuer (инфо-WARN).
    ## @io — ⇥ ep: Endpoint; timeout: int; s_client_runner DI (None → subprocess.run)
    ##       → ⎋ TlsResult (chain_depth, san_ok, days_left, verdict, issuer, error)
    ## @complexity — O(1) — 1-3 openssl subprocess (s_client + x509 SAN + x509 enddate/checkend)
    ## @invariants
    ##   - `openssl s_client -connect {host}:{443} -servername {fqdn} -showcerts </dev/null`
    ##   - chain_depth = число CERTIFICATE-блоков в s_client выводе (0 → fail)
    ##   - SAN берётся из leaf (первого) PEM через `openssl x509 -noout -ext subjectAltName`
    ##   - expiry: _cert_days_left (openssl x509 -enddate) → expiry_verdict;
    ##     cross-check ssl_certs.cert_check_expiry(PEM, 14*86400) — реюз shared/ssl_certs (T5.6)
    ##   - issuer: ssl_certs.cert_is_le_issuer — не FAIL-ветка, а WARN-инфо (self-signed dev)
    ##   - Любая openssl ошибка/timeout → verdict fail (fail-verbose, никогда молчаливый pass)
    """
    cmd = [
        "openssl",
        "s_client",
        "-connect",
        f"{ep.host}:{SSL_PORT}",
        "-servername",
        ep.fqdn,
        "-showcerts",
    ]
    logger.info("[IMP:7][check_tls] openssl s_client %s:%d SNI=%s", ep.host, SSL_PORT, ep.fqdn)

    runner = s_client_runner or _default_subprocess_runner
    try:
        result = runner(cmd, timeout)
    except subprocess.TimeoutExpired:
        logger.info("[IMP:10][check_tls] openssl s_client timeout for %s", ep.fqdn)
        return TlsResult(fqdn=ep.fqdn, verdict="fail", error=f"openssl s_client timed out (>{timeout}s)")
    except FileNotFoundError as exc:
        logger.info("[IMP:10][check_tls] openssl not found: %s", exc)
        return TlsResult(fqdn=ep.fqdn, verdict="fail", error=f"openssl not found: {exc}")

    if result.returncode != 0:
        logger.info("[IMP:9][check_tls] TLS handshake failed for %s (openssl exit %d)", ep.fqdn, result.returncode)
        return TlsResult(
            fqdn=ep.fqdn,
            verdict="fail",
            error=f"TLS handshake failed (openssl exit {result.returncode})",
        )

    output = result.stdout or ""
    chain_depth = len(_CERT_BLOCK_RE.findall(output))
    if chain_depth == 0:
        logger.info("[IMP:10][check_tls] No certificate chain for %s", ep.fqdn)
        return TlsResult(fqdn=ep.fqdn, chain_depth=0, verdict="fail", error="No certificate chain in s_client output")

    leaf_pem = _extract_leaf_cert(output)
    if leaf_pem is None:
        logger.info("[IMP:10][check_tls] Leaf cert extraction failed for %s", ep.fqdn)
        return TlsResult(fqdn=ep.fqdn, chain_depth=chain_depth, verdict="fail", error="Leaf cert extraction failed")

    # ── SAN wildcard matching ────────────────────────────────────────
    san_ok: bool | None = None
    with tempfile.NamedTemporaryFile("w", suffix=".pem", delete=False) as tmp:
        tmp.write(leaf_pem)
        tmp_path = tmp.name
    try:
        san_ok = _cert_san_matches(tmp_path, ep.fqdn, timeout)

        # ── Expiry: _cert_days_left + shared/ssl_certs реюз (T5.6) ──
        days_left = _cert_days_left(tmp_path, timeout)
        expiry_v = expiry_verdict(days_left)

        # Реюз shared/ssl_certs (T5.6): cert_check_expiry — кросс-проверка порога 14 дней
        from core.internal.shared.ssl_certs import cert_check_expiry

        cross_ok = cert_check_expiry(tmp_path, EXPIRY_WARN_DAYS * 86400, timeout=timeout)

        # ── Issuer (информационный WARN, не FAIL-ветка) ─────────────
        from core.internal.shared.ssl_certs import cert_is_le_issuer

        is_le = cert_is_le_issuer(tmp_path, timeout=timeout)

        verdict = expiry_v if expiry_v == "fail" else ("fail" if san_ok is False else expiry_v)
        if san_ok is False:
            logger.info("[IMP:9][check_tls] %s — SAN mismatch → fail", ep.fqdn)
        logger.info(
            "[IMP:9][check_tls] %s — chain=%d san_ok=%s days_left=%s cross_check=%s le=%s verdict=%s",
            ep.fqdn,
            chain_depth,
            san_ok,
            days_left,
            cross_ok,
            is_le,
            verdict,
        )
        return TlsResult(
            fqdn=ep.fqdn,
            chain_depth=chain_depth,
            san_ok=san_ok,
            days_left=days_left,
            verdict=verdict,
            issuer="Let's Encrypt" if is_le else ("unknown" if is_le is False else None),
        )
    finally:
        try:
            os.unlink(tmp_path)
        except OSError:
            logger.warning("[IMP:7][check_tls] Temp PEM cleanup failed: %s", tmp_path)


# endregion FUNC_check_tls


# region FUNC__extract_leaf_cert
def _extract_leaf_cert(s_client_output: str) -> str | None:
    """Извлечь первый (leaf) PEM-сертификат из вывода openssl s_client -showcerts.

    ▶ ┌s_client_output┐ → ○ re BEGIN/END CERTIFICATE → ⊕ first block → ⎋ str | None

    ## @purpose — Первый CERTIFICATE-блок в s_client выводе = leaf (серверный) сертификат;
    ##            остальные — цепочка (intermediates/root). SAN/expiry проверяются на leaf.
    ## @io — ⇥ s_client_output: str → ⎋ str | None (leaf PEM с маркерами, None если блоков нет)
    ## @complexity — O(L) где L = длина вывода
    ## @invariants
    ##   - Возвращает ПЕРВЫЙ блок (порядок s_client: leaf → chain)
    ##   - Сохраняет BEGIN/END маркеры (необходимо для openssl x509 -in)
    ##   - Нет блоков → None (fail-verbose)
    """
    match = _CERT_BLOCK_RE.search(s_client_output)
    if not match:
        return None
    logger.info("[IMP:8][_extract_leaf_cert] Extracted leaf PEM (%d chars)", len(match.group(0)))
    return match.group(0)


# endregion FUNC__extract_leaf_cert


# region FUNC__cert_san_matches
def _cert_san_matches(cert_path: str, fqdn: str, timeout: int) -> bool | None:
    """Проверить SAN сертификата на покрытие fqdn (openssl x509 -ext subjectAltName).

    ▶ ┌cert_path, fqdn┐ → ⚡ openssl x509 -in C -noout -ext subjectAltName → ◇ rc!=0 → None
      → ○ parse 'DNS:' entries → ⊕ any san_matches_domain → ⎋ bool | None

    ## @purpose — SAN-матчинг leaf-сертификата (I3). openssl x509 -ext subjectAltName выводит
    ##            'DNS:example.com, DNS:*.example.com' — парсится и матчится per-SAN.
    ## @io — ⇥ cert_path: str; fqdn: str; timeout: int → ⎋ bool | None (None = непарсируемо)
    ## @complexity — O(1) subprocess + O(S) где S = число SAN
    ## @invariants
    ##   - subprocess ошибка/timeout → None (fail-verbose → вызывающий решает fail)
    ##   - Ни один SAN не матчит → False
    ##   - DNS:-префикс срезается в san_matches_domain
    """
    try:
        result = subprocess.run(
            ["openssl", "x509", "-in", cert_path, "-noout", "-ext", "subjectAltName"],
            capture_output=True,
            text=True,
            timeout=timeout,
        )
    except (subprocess.TimeoutExpired, FileNotFoundError, OSError) as exc:
        logger.warning("[IMP:7][_cert_san_matches] openssl SAN check failed: %s", exc)
        return None
    if result.returncode != 0:
        logger.warning("[IMP:7][_cert_san_matches] openssl SAN exit %d", result.returncode)
        return None

    sans: list[str] = []
    for line in result.stdout.splitlines():
        line = line.strip()
        if not line.startswith("DNS:"):
            continue
        sans.extend(token.strip() for token in line.split(",") if token.strip().startswith("DNS:"))
    if not sans:
        logger.info("[IMP:8][_cert_san_matches] No DNS SANs in cert for %s", fqdn)
        return False

    matched = any(san_matches_domain(fqdn, san) for san in sans)
    logger.info("[IMP:9][_cert_san_matches] fqdn=%s SANs=%r → %s", fqdn, sans, matched)
    return matched


# endregion FUNC__cert_san_matches


# region FUNC__cert_days_left
def _cert_days_left(cert_path: str, timeout: int) -> int | None:
    """Дней до истечения сертификата (openssl x509 -enddate → datetime).

    ▶ ┌cert_path┐ → ⚡ openssl x509 -in C -enddate -noout → parse 'notAfter=...' → ⊕ (notAfter - now).days → ⎋ int | None

    ## @purpose — Дней до notAfter для различения WARN (<14д) / FAIL (expired). Дата парсится
    ##            из `openssl x509 -enddate` ('notAfter=Aug  5 12:00:00 2026 GMT'). Отличие от
    ##            shared/ssl_certs.cert_check_expiry (-checkend bool): нужен сам день — для
    ##            пороговой классификации (T5.6 @rationale).
    ## @io — ⇥ cert_path: str; timeout: int → ⎋ int | None (None = непарсируемо)
    ## @complexity — O(1) subprocess
    ## @invariants
    ##   - openssl ошибка/непарсируемая дата → None (fail-verbose)
    ##   - Округление: (notAfter - now).days — floor; notAfter через минуту → 0 (warn)
    ##   - Формат 'notAfter=' + strftime('%b %e %H:%M:%S %Y %Z') (openssl -enddate)
    """
    try:
        result = subprocess.run(
            ["openssl", "x509", "-in", cert_path, "-enddate", "-noout"],
            capture_output=True,
            text=True,
            timeout=timeout,
        )
    except (subprocess.TimeoutExpired, FileNotFoundError, OSError) as exc:
        logger.warning("[IMP:7][_cert_days_left] openssl enddate failed: %s", exc)
        return None
    if result.returncode != 0:
        logger.warning("[IMP:7][_cert_days_left] openssl enddate exit %d", result.returncode)
        return None

    marker = "notAfter="
    for line in result.stdout.splitlines():
        line = line.strip()
        if not line.startswith(marker):
            continue
        try:
            not_after = datetime.strptime(line[len(marker) :].strip(), "%b %e %H:%M:%S %Y %Z").replace(
                tzinfo=timezone.utc
            )
        except ValueError as exc:
            logger.warning("[IMP:7][_cert_days_left] Unparseable notAfter %r: %s", line, exc)
            return None
        days = (not_after - datetime.now(timezone.utc)).days
        logger.info("[IMP:9][_cert_days_left] notAfter=%s days_left=%d", line[len(marker) :].strip(), days)
        return days
    logger.warning("[IMP:7][_cert_days_left] no notAfter line in enddate output")
    return None


# endregion FUNC__cert_days_left


# endregion CHECKS


# region RUNNERS


# region FUNC__default_curl_runner
def _default_curl_runner(cmd: list[str], timeout: int) -> subprocess.CompletedProcess:
    """Дефолтный curl-раннер (subprocess.run) — DI-точка для unit-тестов.

    ▶ ┌cmd, timeout┐ → ⚡ subprocess.run(capture_output=True, text=True) → ⎋ CompletedProcess
    ## @purpose — Единственная точка subprocess для check_http (тесты инжектят curl_runner).
    ## @io — ⇥ cmd: list[str]; timeout: int → ⎋ subprocess.CompletedProcess
    ## @complexity — O(1)
    """
    return subprocess.run(cmd, capture_output=True, text=True, timeout=timeout, check=False)


# endregion FUNC__default_curl_runner


# region FUNC__default_subprocess_runner
def _default_subprocess_runner(cmd: list[str], timeout: int) -> subprocess.CompletedProcess:
    """Дефолтный openssl-раннер (subprocess.run) — DI-точка для unit-тестов.

    ▶ ┌cmd, timeout┐ → ⚡ subprocess.run(capture_output=True, text=True) → ⎋ CompletedProcess
    ## @purpose — Единая точка subprocess для openssl s_client (тесты инжектят s_client_runner).
    ## @io — ⇥ cmd: list[str]; timeout: int → ⎋ subprocess.CompletedProcess
    ## @complexity — O(1)
    """
    return subprocess.run(cmd, capture_output=True, text=True, timeout=timeout, check=False)


# endregion FUNC__default_subprocess_runner


# region FUNC_ssh_runner
# ⚠️ TRAP[DECISION] · 2026-08-05 · — · SSH-раннер дедуплицирован (DevPlan 139 W3 T4)
# · Rejected: держать verbatim-копию default_ssh_runner в verify_sweep (дрейф: 2 копии,
# ·   timeout-семантика может разойтись; AC W3b: rg def default_ssh_runner → 1 def)
# · Reason: канон — core.internal.shared.vps_readiness.default_ssh_runner (DevPlan 105);
# ·   сигнатура (host, user, cmd, timeout, ssh_lib_path=None) идентична, Python-level
# ·   timeout = bash timeout + 5s сохранён (сверено до импорта). DI-точка ssh_runner
# ·   в _collect_remote не менялась — name разрешается в импортированный канон.
# · Rev: если потребуется иная timeout-семантика для e2e-verify — параметризовать канон,
# ·   не копировать.
# endregion FUNC_ssh_runner


# endregion RUNNERS


# region REPORT


# region FUNC__render_text_report
def _render_text_report(report: SweepReport) -> str:
    """Текстовый отчёт-таблица (endpoint → HTTP → TLS → вердикт).

    ▶ ┌report┐ → ⊕ строки таблицы → ∑ verdict → ⎋ str

    ## @purpose — Человекочитаемая таблица sweep (AC W5: «таблица = вывод команды»).
    ## @io — ⇥ report: SweepReport → ⎋ str (многострочная таблица)
    ## @complexity — O(E) где E = endpoints
    ## @invariants
    ##   - Одна строка на endpoint: fqdn | HTTP code | TLS verdict | итог
    ##   - Collection errors печатаются отдельным блоком (R4: FAIL-причина видима)
    ##   - Итоговая строка с exit-семантикой
    """
    lines: list[str] = []
    lines.append("")
    lines.append("┌─ e2e-verify sweep ──────────────────────────────────────────┐")
    lines.append(f"│ node={report.node}  mode={report.mode}  endpoints={report.endpoints}")
    lines.append("├────────────────────────────────────────────────────────────┤")
    for r in report.http:
        http_v = f"HTTP {r.code}" if r.code is not None else f"ERR {r.error}"
        tls_row = next((t for t in report.tls if t.fqdn == r.fqdn), None)
        tls_v = (
            "TLS fail"
            if tls_row and not tls_row.ok
            else ("TLS warn" if tls_row and tls_row.verdict == "warn" else "TLS ok")
        )
        overall = "OK" if r.ok and (tls_row is None or tls_row.ok) else "FAIL"
        lines.append(f"│ {r.fqdn:<32} {http_v:<18} {tls_v:<10} {overall}")
    lines.extend(f"│ COLLECT FAIL: {err}" for err in report.collect_errors)
    lines.append("└────────────────────────────────────────────────────────────┘")
    lines.append("")
    if report.exit_code == 0:
        lines.append(f"✅ e2e-verify PASS — {report.endpoints} endpoint(s) all green")
    else:
        lines.append(f"❌ e2e-verify FAIL — {report.endpoints} endpoint(s), review table above")
    return "\n".join(lines)


# endregion FUNC__render_text_report


# endregion REPORT


# region CLI


# region FUNC__build_parser
def _build_parser() -> argparse.ArgumentParser:
    """CLI-парсер verify_sweep (sweep subcommand).

    ▶ ┌None┐ → ⊕ argparse.ArgumentParser → ⎋ parser
    ## @purpose — CLI: `python3 -m core.internal.verify_sweep sweep --node N [--mode ...] [--json] ...`
    ## @io — ⇥ None → ⎋ argparse.ArgumentParser
    ## @complexity — O(1)
    """
    parser = argparse.ArgumentParser(description="Endpoint sweep verification (DevPlan 136 W5 e2e-verify)")
    sub = parser.add_subparsers(dest="command", required=True)

    p_sweep = sub.add_parser("sweep", help="Sweep-verify all endpoints of a node")
    p_sweep.add_argument("--node", required=True, help="Node name (resolved via node.yaml / NODE_HOST_MAP)")
    p_sweep.add_argument(
        "--mode",
        choices=("local", "remote"),
        default="remote",
        help="Endpoint collection source: local (node.yaml + overlays/nginx) | remote (ssh nginx conf.d, default)",
    )
    p_sweep.add_argument("--json", action="store_true", help="Print machine-readable JSON report to stdout")
    p_sweep.add_argument("--node-configs-dir", default=None, help="Path to node-configs/ (local mode overlay scan)")
    p_sweep.add_argument("--platform-root", default=None, help="Platform root for node.yaml 3-path search")
    p_sweep.add_argument("--timeout", type=int, default=CURL_TIMEOUT_DEFAULT, help="HTTP/TLS timeout in seconds")
    p_sweep.add_argument(
        "--ssh-user", default=DEFAULT_SSH_USER, help="SSH user for remote collect (default: ci-deploy)"
    )
    p_sweep.add_argument(
        "--nginx-conf-dir",
        default=REMOTE_NGINX_CONF_DIR,
        help="Remote nginx conf.d dir for remote collect (default: /etc/nginx/conf.d/overlay)",
    )
    return parser


# endregion FUNC__build_parser


# region FUNC_main
def main(argv: list[str] | None = None) -> int:
    """CLI entry: sweep endpoint collection + HTTP/TLS checks → отчёт → exit 0/1/2.

    ▶ ┌argv┐ → ◇ parse → ◇ collect_endpoints (EndpointCollectionError → exit 1|2)
      → ○ check_http/check_tls per endpoint ([IMP:9] на каждый вердикт)
      → ⊕ render (json|text) → ⎋ exit 0|1|2

    ## @purpose — Оркестрация полного sweep (DevPlan 136 T5.1): collection → checks →
    ##            отчёт. Каждый вердикт логируется [IMP:9] (LDD телеметрия для QA);
    ##            --json печатает machine-readable отчёт в stdout, логи — в stderr.
    ## @io — ⇥ argv: list[str] | None → ⎋ int (0 all ok | 1 ≥1 FAIL | 2 config error)
    ## @complexity — O(E * (H + T)) где E = endpoints, H = HTTP check, T = TLS check
    ## @invariants
    ##   - main() НЕ вызывает sys.exit — возвращает int (канон core/AGENTS.md)
    ##   - EndpointCollectionError exit_code=2 (config) / 1 (operational FAIL) пробрасывается как есть
    ##   - [IMP:9] на каждый HTTP и TLS вердикт (анти-иллюзия: траектория видна QA)
    ##   - --json → отчёт в stdout (json.dumps), текстовый отчёт в stderr
    ##   - 0 endpoints → exit 0 (голая test-e2e нода, I6)
    ##   - Логи в stderr через logging (stdout зарезервирован под отчёт/JSON)
    """
    logging.basicConfig(level=logging.INFO, format="%(message)s", stream=sys.stderr)

    parser = _build_parser()
    args = parser.parse_args(argv)
    if args.command != "sweep":
        parser.error(f"Unknown command: {args.command}")
        return 2

    logger.info("[IMP:7][main] e2e-verify sweep start: node=%s mode=%s json=%s", args.node, args.mode, args.json)

    # ── Collection ───────────────────────────────────────────────────
    try:
        endpoints = collect_endpoints(
            args.node,
            mode=args.mode,
            node_configs_dir=args.node_configs_dir,
            platform_root=args.platform_root,
            remote_conf_dir=args.nginx_conf_dir,
            ssh_user=args.ssh_user,
        )
    except EndpointCollectionError as exc:
        logger.error("[IMP:10][main] Endpoint collection failed (exit=%d): %s", exc.exit_code, exc)
        print(f"e2e-verify FAIL — endpoint collection error: {exc}", file=sys.stderr)
        return exc.exit_code

    report = SweepReport(node=args.node, mode=args.mode, endpoints=len(endpoints))

    # ── Checks (per endpoint: HTTP + TLS, [IMP:9] verdict) ───────────
    for ep in endpoints:
        http_result = check_http(ep, timeout=args.timeout)
        tls_result = check_tls(ep, timeout=args.timeout)
        report.http.append(http_result)
        report.tls.append(tls_result)
        logger.info(
            "[IMP:9][main] verdict %s: http=%s tls=%s (exit-so-far=%d)",
            ep.fqdn,
            http_result.verdict,
            tls_result.verdict,
            0 if (http_result.ok and tls_result.ok) else 1,
        )

    # ── Report ───────────────────────────────────────────────────────
    if args.json:
        print(json.dumps(report.to_dict(), ensure_ascii=False, indent=2))
    else:
        print(_render_text_report(report), file=sys.stderr)

    if report.exit_code == 0:
        logger.info("[IMP:9][main] e2e-verify PASS — %d endpoint(s) all green", report.endpoints)
    else:
        logger.info("[IMP:9][main] e2e-verify FAIL — %d endpoint(s), review table", report.endpoints)
    return report.exit_code


# endregion FUNC_main


# endregion CLI


if __name__ == "__main__":
    sys.exit(main())
