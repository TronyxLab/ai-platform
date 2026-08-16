# GREP_SUMMARY: verify-sweep, models, endpoint, http-result, tls-result, sweep-report, exit-contract, endpoint-collection-error, ssl-port
# STRUCTURE: ┌dataclasses (Endpoint, HttpResult, TlsResult, SweepReport)┐ → ⊕ EndpointCollectionError → ⎋ константа SSL_PORT
# region MODULE_CONTRACT
## @purpose  Модели данных пакета verify_sweep (DevPlan 136 W5 T5.1-T5.6): цель проверки
##           (Endpoint), результаты HTTP/TLS-проверок (HttpResult/TlsResult), агрегированный
##           отчёт sweep (SweepReport с JSON-сериализацией и exit-контрактом 0/1) и
##           единственное исключение коллекции (EndpointCollectionError).
## @scope    Только типы данных + протокольная константа SSL_PORT. Бизнес-логика —
##           в collection.py / http_check.py / tls_check.py / report.py; CLI — в __init__.py.
## @invariants
##   - Exit-контракт SweepReport: 0 = все endpoints OK (нет FAIL-вердиктов, нет collection-ошибок);
##     1 = ≥1 FAIL verdict или collection error (I1 монолита)
##   - HttpResult.ok = verdict != "fail" (pass | warn); TlsResult.ok = verdict != "fail" (ok | warn)
##   - EndpointCollectionError несёт exit_code (1 = operational FAIL / R4, 2 = config error) —
##     решает main(), НЕ само исключение
## @rationale Декомпозиция монолита verify_sweep.py (1284 LOC) → пакет (план 170 W7-E1,
##            research-A §7). Модели выделены отдельно: dataclasses самодостаточны,
##            не тянут subprocess/SSH-зависимости — чистый доменный слой без импорт-графа.
## @changes  2026-08-15 | План 170 W7-E1 — выделено из verify_sweep.py (чистый move, поведение 1:1)
## @usecases
##   - collection.py собирает list[Endpoint]; http_check/tls_check возвращают HttpResult/TlsResult
##   - report.py и main() агрегируют всё в SweepReport → to_dict() → JSON (--json)
# endregion MODULE_CONTRACT

from __future__ import annotations

from dataclasses import dataclass, field

from core.internal.shared.exceptions import PlatformError

SSL_PORT: int = 443
"""## @invariant Порт TLS-проверки (nginx 443 ssl) — используется curl --resolve (http_check)
и openssl s_client -connect (tls_check). Единственный литерал порта в пакете."""


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

    def to_dict(self) -> dict[str, object]:
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


class EndpointCollectionError(PlatformError):
    """Хард-ошибка сбора endpoints (R4: ssh-недоступен → FAIL; конфиг-ошибки → exit 2).

    ## @purpose — Единый сигнал сбоя коллекции: main() решает exit 1 (FAIL) vs exit 2 (config).
    ## @io — ⇥ message + exit_code (1 = operational FAIL, 2 = config error) → ⎋ исключение
    """

    def __init__(self, message: str, exit_code: int = 1) -> None:
        super().__init__(message)
        self.exit_code = exit_code
