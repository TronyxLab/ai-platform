# GREP_SUMMARY: chaos-audit log-audit-manifest marker sources docker journald loki alerts http state export verdict TTR
# STRUCTURE: ▶ LogMarker(dataclass) → ◇ LogAuditManifest (add/check/export) → ◇ source checkers (docker/journald/loki/alerts/http/state) → ◇ record_verdict → ⎋
# region MODULE_CONTRACT
## @purpose  DevPlan 126 W1 (chaos-resilience): Log Audit Manifest — проверка, что инцидент
##           (инъекция отказа) оставил полный реконструируемый след в логах. Каждый тест
##           T1-T11 собирает manifest обязательных/опциональных маркеров по источникам:
##           docker logs, journalctl, Loki API, Grafana alertmanager API, HTTP-коды сайтов,
##           docker health state. Экспорт логов-артефактов в .ai/plans/126-chaos-resilience/files/.
## @scope    Consumed by tests/e2e/test_chaos_resilience.py. NOT in regular gate (chaos marker).
## @invariants
##   - marker.source ∈ {docker, journald, loki, alerts, http, state}
##   - expected: required (fail, если не найден) | optional (предупреждение, PARTIAL)
##   - Критерий «инцидент без следа» (DevPlan §4): required-маркер не найден ни в одном
##     источнике → FAIL даже при успешном восстановлении; маркер есть, но первопричина не
##     читается → PARTIAL; контейнерный маркер есть в docker logs, но НЕТ в Loki → PARTIAL.
##   - Все SSH-команды идут через NodeSSHClient (lib/ssh.sh parity, timeout 124).
##   - Grafana auth: GF_SECURITY_ADMIN_USER/PASSWORD из /run/platform/secrets.env на хосте.
##   - Вердикты пишутся в files/T<N>/verdict.json + files/results.json (агрегат).
## @rationale DevPlan 126 §4: единый механизм проверки логов для всех 11 тестов — без
##           дублирования SSH/curl-логики в каждом тесте.
## @changes 2026-08-03 | DevPlan 126 W1 — Created
# endregion MODULE_CONTRACT

from __future__ import annotations

import contextlib
import json
import logging
import time
from dataclasses import dataclass
from pathlib import Path

from tests._conftest.node import NodeSSHClient

logger = logging.getLogger(__name__)

# ── Канонические константы ──
_GRAFANA_ALERTS_API = "http://127.0.0.1:3000/api/alertmanager/grafana/api/v2/alerts"
_LOKI_API = "http://127.0.0.1:3100/loki/api/v1/query_range"
_SECRETS_ENV = "/run/platform/secrets.env"

_MARKER_SOURCES = ("docker", "journald", "loki", "alerts", "http", "state", "auditfile")

# Сайты платформы (T1/T2/T3/T9/T11 — проверка живости стека снаружи)
SITE_URLS = [
    "https://www.tronyx.ru/",
    "https://sexydancerostov.ru/",
    "https://botanika.tronyx.ru/",
    "https://platform.tronyx.ru/",
]
_SITE_OK_CODES = (200, 201, 301, 302, 307, 308, 401, 403)


def _http_code_ok(code: str) -> bool:
    """HTTP-код из curl (str) считается «сайт жив» (2xx/3xx/401/403)."""
    try:
        return int(code) in _SITE_OK_CODES
    except ValueError:
        return False


# Эталонный список контейнеров (docker ps baseline, W1) — заполняется из файла
_BASELINE_CONTAINERS = [
    "backup-cron",
    "botanika",
    "cadvisor",
    "clickhouse",
    "dance-site",
    "grafana",
    "hermes-agent",
    "langfuse",
    "langfuse-redis",
    "litellm",
    "loki",
    "minio",
    "nginx",
    "nginx-prometheus-exporter",
    "node-exporter",
    "pgbouncer",
    "postgres",
    "postgres-exporter",
    "prometheus",
    "promtail",
    "redis",
    "redis-exporter",
    "status-page",
    "tronyx-site",
]


# region DATACLASS_LogMarker
@dataclass
class LogMarker:
    """Один маркер Log Audit Manifest.

    ## @purpose — Декларативное описание ожидаемого следа инцидента в одном источнике.
    ## @io — ⇥ source/regex/window_min/expected/container/label → ⎋ LogMarker
    ## @complexity — O(1)
    ## @invariants
    ##   - source ∈ _MARKER_SOURCES
    ##   - expected: "required" | "optional"
    ##   - container обязателен для source=docker/loki
    """

    source: str
    regex: str
    label: str = ""
    window_min: int = 15
    expected: str = "required"  # required | optional
    container: str | None = None
    negate: bool = False  # True: found = regex ОТСУТСТВУЕТ в окне (маркер отсутствия ошибок)
    path: str | None = None  # для source=auditfile — путь к файлу (default /var/log/platform/audit.jsonl)

    def __post_init__(self) -> None:
        if self.source not in _MARKER_SOURCES:
            raise ValueError(f"unknown marker source: {self.source}")
        if self.expected not in ("required", "optional"):
            raise ValueError(f"unknown expected value: {self.expected}")
        if self.source in ("docker", "loki") and not self.container:
            raise ValueError(f"marker source {self.source} requires container")
        if not self.label:
            self.label = f"{self.source}:{self.regex[:40]}"


# endregion DATACLASS_LogMarker


# region DATACLASS_MarkerResult
@dataclass
class MarkerResult:
    """Результат проверки одного маркера.

    ## @purpose — Machine-readable результат для вердикта теста: найден ли маркер,
    ##             в каком источнике, какой командой, детали совпадения.
    ## @io — ⇥ marker/found/detail → ⎋ MarkerResult
    ## @complexity — O(1)
    """

    marker: LogMarker
    found: bool
    detail: str = ""
    loki_duplicate: bool | None = None  # контейнерный маркер продублирован в Loki?


# endregion DATACLASS_MarkerResult


# region CLASS_LogAuditManifest
class LogAuditManifest:
    """Log Audit Manifest — проверка следов инцидента по источникам + экспорт логов.

    ▶ ┌markers┐ → ○ add_marker() → ⚡ check_all(ssh, incident_start) → ⎋ list[MarkerResult]
    ▶ ┌containers┐ → ⚡ export_logs(ssh, incident_start, out_dir) → ⎋ files/T<N>/…

    ## @purpose — Единая точка проверки «инцидент оставил след» для chaos-тестов.
    ## @io — ⇥ ssh: NodeSSHClient → ⎋ per-method результаты
    ## @complexity — O(M) SSH round-trips (M = число маркеров)
    ## @invariants
    ##   - check_all возвращает результат по КАЖДОМУ маркеру (никогда не бросает)
    ##   - Для docker-маркеров дополнительно проверяется дубликат в Loki (конвейер)
    ##   - Время окна: from = incident_start − 1 мин, to = incident_start + max(window_min, ttr+2 мин)
    ##   - Export: journalctl + docker logs затронутых контейнеров + Loki-выборка → out_dir
    ## @rationale DevPlan 126 §4 — критерий «инцидент без следа» реализован как набор
    ##           маркеров с expected=required; PARTIAL-случаи (нет Loki-дубликата) отделены.
    """

    def __init__(self, name: str) -> None:
        self.name = name
        self.markers: list[LogMarker] = []

    def add(
        self,
        source: str,
        regex: str,
        *,
        label: str = "",
        window_min: int = 15,
        expected: str = "required",
        container: str | None = None,
        negate: bool = False,
        path: str | None = None,
    ) -> LogAuditManifest:
        """Добавить маркер в manifest (fluent API)."""
        self.markers.append(LogMarker(source, regex, label, window_min, expected, container, negate, path))
        return self

    # ── helpers ──────────────────────────────────────────────────────────────
    @staticmethod
    def _count_from_ssh(res) -> int:
        """Извлечь последнее целое из stdout SSH-результата (grep -c). 0 при ошибке."""
        count = 0
        with contextlib.suppress(ValueError, IndexError):
            count = int(res.stdout.strip().splitlines()[-1])
        return count

    @staticmethod
    def _window(incident_start: int, window_min: int, ttr_s: int) -> tuple[int, int]:
        """Вернуть (from_epoch, to_epoch) окна поиска маркера по epoch-секундам хоста.

        docker logs принимает epoch-секунды; journalctl — '@<epoch>' (systemd.time(7)).
        """
        from_ts = incident_start - 60  # margin 1 мин до инъекции
        to_ts = incident_start + max(window_min * 60, ttr_s + 120)
        return from_ts, to_ts

    @staticmethod
    def _host_date(ssh: NodeSSHClient, epoch: int, fmt: str = "%Y-%m-%dT%H:%M:%SZ") -> str:
        res = ssh.ssh_read(f"date -d @{epoch} +'{fmt}'", timeout=20)
        return res.stdout.strip()

    # ── checkers ─────────────────────────────────────────────────────────────
    def _check_docker(self, ssh: NodeSSHClient, marker: LogMarker, from_ts: int, to_ts: int) -> MarkerResult:
        cmd = f"docker logs --since {from_ts} --until {to_ts} {marker.container} 2>&1 | grep -cE '{marker.regex}'"
        res = ssh.ssh_read(cmd, timeout=90)
        count = self._count_from_ssh(res)
        found = (count == 0) if marker.negate else (count > 0)
        return MarkerResult(marker, found=found, detail=f"count={count}")

    def _check_journald(self, ssh: NodeSSHClient, marker: LogMarker, from_ts: int, to_ts: int) -> MarkerResult:
        cmd = f"journalctl --since '@{from_ts}' --until '@{to_ts}' --no-pager 2>/dev/null | grep -cE '{marker.regex}'"
        res = ssh.ssh_read(cmd, timeout=90)
        count = self._count_from_ssh(res)
        found = (count == 0) if marker.negate else (count > 0)
        return MarkerResult(marker, found=found, detail=f"count={count}")

    def _check_auditfile(self, ssh: NodeSSHClient, marker: LogMarker) -> MarkerResult:
        path = marker.path or "/var/log/platform/audit.jsonl"
        if marker.container:
            # файл внутри контейнера (например backup-cron: /var/log/platform/backup/postgres.log)
            cmd = f"docker exec {marker.container} grep -cE '{marker.regex}' {path} 2>/dev/null || true"
        else:
            cmd = f"grep -cE '{marker.regex}' {path} 2>/dev/null || true"
        res = ssh.ssh_read(cmd, timeout=30)
        count = self._count_from_ssh(res)
        found = (count == 0) if marker.negate else (count > 0)
        return MarkerResult(marker, found=found, detail=f"count={count} (file={path})")

    def _check_loki(self, ssh: NodeSSHClient, marker: LogMarker, incident_start: int, ttr_s: int) -> MarkerResult:
        from_ns = (incident_start - 60) * 1_000_000_000
        to_ns = (incident_start + max(marker.window_min * 60, ttr_s + 120)) * 1_000_000_000
        query = f'{{container="{marker.container}"}} |~ "{marker.regex}"'
        cmd = (
            f"curl -s -G '{_LOKI_API}' "
            f"--data-urlencode 'query={query}' "
            f"--data-urlencode 'start={from_ns}' "
            f"--data-urlencode 'end={to_ns}' "
            f"--data-urlencode 'limit=5'"
        )
        res = ssh.ssh_read(cmd, timeout=90)
        found = False
        detail = "loki unreachable/empty"
        try:
            data = json.loads(res.stdout)
            results = ((data.get("data") or {}).get("result")) or []
            found = len(results) > 0
            detail = f"streams={len(results)}"
        except json.JSONDecodeError:
            detail = f"non-JSON response: {res.stdout[:120]!r}"
        return MarkerResult(marker, found=found, detail=detail)

    def _check_alerts(self, ssh: NodeSSHClient, marker: LogMarker, incident_start: int, ttr_s: int) -> MarkerResult:
        # окно не передаётся в API — alertmanager v2 отдаёт активные алерты; проверка
        # по startsAt внутри окна выполняется ниже (время проверки — момент вызова).
        cmd = (
            f"set -a; source {_SECRETS_ENV} 2>/dev/null; set +a; "
            f'curl -s -u "$GF_SECURITY_ADMIN_USER:$GF_SECURITY_ADMIN_PASSWORD" '
            f"'{_GRAFANA_ALERTS_API}'"
        )
        res = ssh.ssh_read(cmd, timeout=60)
        found = False
        detail = "no alerts / API error"
        try:
            alerts = json.loads(res.stdout)
            if isinstance(alerts, list):
                import re

                found_alerts = []
                for alert in alerts:
                    blob = json.dumps(alert)
                    if re.search(marker.regex, blob):
                        starts_at = alert.get("startsAt") or ""
                        found_alerts.append(starts_at)
                found = len(found_alerts) > 0
                detail = f"matched={found_alerts}" if found_alerts else f"alerts_total={len(alerts)}"
        except json.JSONDecodeError:
            detail = f"non-JSON: {res.stdout[:120]!r}"
        return MarkerResult(marker, found=found, detail=detail)

    def _check_http(self, ssh: NodeSSHClient, marker: LogMarker) -> MarkerResult:
        url = marker.regex  # для source=http regex поле = URL
        res = ssh.ssh_read(f"curl -s -L --noproxy '*' -o /dev/null -w '%{{http_code}}' -m 15 '{url}'", timeout=30)
        code = res.stdout.strip()
        return MarkerResult(marker, found=_http_code_ok(code), detail=f"{url} → {code}")

    def _check_state(self, ssh: NodeSSHClient, marker: LogMarker) -> MarkerResult:
        container = marker.container or marker.regex
        res = ssh.ssh_read(
            f"docker inspect --format '{{{{.State.Status}}}}/{{{{.State.Health.Status}}}}' {container} 2>/dev/null",
            timeout=30,
        )
        status = res.stdout.strip()
        parts = status.split("/")
        running = bool(parts and parts[0] == "running")
        health = parts[1] if len(parts) > 1 else ""
        ok = running and (health in ("", "healthy"))
        return MarkerResult(marker, found=ok, detail=f"status={status}")

    # ── public API ───────────────────────────────────────────────────────────
    def check_all(self, ssh: NodeSSHClient, incident_start: int, ttr_s: int) -> list[MarkerResult]:
        """Проверить ВСЕ маркеры manifest'а. Никогда не бросает — возвращает результаты.

        ▶ ┌markers┐ → ○ per-marker dispatch → ○ Loki-дубликат для docker-маркеров → ⎋ list[MarkerResult]
        """
        results: list[MarkerResult] = []
        for marker in self.markers:
            try:
                if marker.source == "docker":
                    from_ts, to_ts = self._window(incident_start, marker.window_min, ttr_s)
                    result = self._check_docker(ssh, marker, from_ts, to_ts)
                    if result.found and not marker.negate:
                        loki_res = self._check_loki(ssh, marker, incident_start, ttr_s)
                        result.loki_duplicate = loki_res.found
                        if not loki_res.found:
                            logger.warning(
                                "[IMP:7][manifest][%s] docker-маркер '%s' НЕ продублирован в Loki (дыра конвейера)",
                                self.name,
                                marker.label,
                            )
                elif marker.source == "journald":
                    from_ts, to_ts = self._window(incident_start, marker.window_min, ttr_s)
                    result = self._check_journald(ssh, marker, from_ts, to_ts)
                elif marker.source == "auditfile":
                    result = self._check_auditfile(ssh, marker)
                elif marker.source == "loki":
                    result = self._check_loki(ssh, marker, incident_start, ttr_s)
                elif marker.source == "alerts":
                    result = self._check_alerts(ssh, marker, incident_start, ttr_s)
                elif marker.source == "http":
                    result = self._check_http(ssh, marker)
                elif marker.source == "state":
                    result = self._check_state(ssh, marker)
                else:  # pragma: no cover — guarded by __post_init__
                    result = MarkerResult(marker, found=False, detail="unknown source")
            except Exception as exc:  # проверка маркера НЕ должна ронять тест
                logger.warning("[IMP:7][manifest][%s] marker '%s' check error: %s", self.name, marker.label, exc)
                result = MarkerResult(marker, found=False, detail=f"check error: {exc}")
            logger.info(
                "[IMP:9][manifest][%s] marker '%s' → found=%s (%s)",
                self.name,
                marker.label,
                result.found,
                result.detail,
            )
            results.append(result)
        return results

    def export_logs(self, ssh: NodeSSHClient, incident_start: int, out_dir: Path, containers: list[str]) -> Path:
        """Экспортировать логи инцидента в out_dir (journald + docker logs + alerts).

        ▶ ┌out_dir┐ → ⚡ journalctl --since → ⚡ per-container docker logs → ⚡ alerts.json → ⎋ out_dir
        """
        out_dir.mkdir(parents=True, exist_ok=True)
        from_iso = self._host_date(ssh, incident_start - 120)

        journal = ssh.ssh_read(f"journalctl --since '{from_iso}' --no-pager 2>/dev/null | tail -n 4000", timeout=120)
        (out_dir / "journal.log").write_text(journal.stdout, errors="replace")

        for container in containers:
            res = ssh.ssh_read(f"docker logs --since '{from_iso}' {container} 2>&1 | tail -n 3000", timeout=120)
            (out_dir / f"{container}.log").write_text(res.stdout, errors="replace")

        alerts = ssh.ssh_read(
            f"set -a; source {_SECRETS_ENV} 2>/dev/null; set +a; "
            f"curl -s -u \"$GF_SECURITY_ADMIN_USER:$GF_SECURITY_ADMIN_PASSWORD\" '{_GRAFANA_ALERTS_API}'",
            timeout=60,
        )
        (out_dir / "alerts.json").write_text(alerts.stdout, errors="replace")

        loki = ssh.ssh_read(
            f"curl -s -G '{_LOKI_API}' --data-urlencode 'query={{job=~\"docker|varlogs\"}}' "
            f"--data-urlencode 'start={(incident_start - 120) * 1_000_000_000}' "
            f"--data-urlencode 'end={(incident_start + 3600) * 1_000_000_000}' "
            f"--data-urlencode 'limit=500'",
            timeout=120,
        )
        (out_dir / "loki.json").write_text(loki.stdout, errors="replace")
        return out_dir


# endregion CLASS_LogAuditManifest


# region FUNC_audit_helpers
def record_verdict(
    test_id: str, out_dir: Path, verdict: str, ttr_s: int, results: list[MarkerResult], incident_start: int
) -> None:
    """Записать вердикт теста в files/T<N>/verdict.json + обновить files/results.json.

    ▶ ┌verdict/ttr/results┐ → ○ verdict.json → ○ results.json (агрегат) → ⎋ None
    """
    out_dir.mkdir(parents=True, exist_ok=True)
    verdict_data = {
        "test": test_id,
        "verdict": verdict,
        "ttr_s": ttr_s,
        "incident_start_epoch": incident_start,
        "checked_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "markers": [
            {
                "label": r.marker.label,
                "source": r.marker.source,
                "regex": r.marker.regex,
                "expected": r.marker.expected,
                "found": r.found,
                "loki_duplicate": r.loki_duplicate,
                "detail": r.detail,
            }
            for r in results
        ],
    }
    (out_dir / "verdict.json").write_text(json.dumps(verdict_data, indent=2, ensure_ascii=False))
    results_path = out_dir.parent / "results.json"
    aggregate = {}
    if results_path.exists():
        try:
            aggregate = json.loads(results_path.read_text())
        except json.JSONDecodeError:
            aggregate = {}
    aggregate[test_id] = {"verdict": verdict, "ttr_s": ttr_s}
    results_path.write_text(json.dumps(aggregate, indent=2, ensure_ascii=False))
    logger.info("[IMP:9][verdict][%s] verdict=%s ttr=%ss → %s", test_id, verdict, ttr_s, out_dir / "verdict.json")


def compute_verdict(results: list[MarkerResult]) -> tuple[str, list[str]]:
    """Вычислить вердикт по результатам маркеров: SUCCESS/PARTIAL/FAIL + причины.

    ▶ ┌results┐ → ○ required-missing → ○ optional-missing → ○ loki-gap → ⎋ (verdict, reasons)

    ## @purpose — Критерий «инцидент без следа» (DevPlan §4): FAIL при отсутствии
    ##            required-маркера; PARTIAL при optional-промахе или дыре Loki-конвейера.
    """
    reasons: list[str] = []
    for r in results:
        if r.marker.expected == "required" and not r.found:
            reasons.append(f"required marker NOT FOUND: {r.marker.label} ({r.detail})")
        elif r.marker.expected == "optional" and not r.found:
            reasons.append(f"optional marker missing: {r.marker.label} ({r.detail})")
        elif r.marker.source == "docker" and r.found and r.loki_duplicate is False:
            reasons.append(f"Loki pipeline gap: {r.marker.label} (found in docker logs, absent in Loki)")
    if any(r.marker.expected == "required" and not r.found for r in results):
        return "FAIL", reasons
    if reasons:
        return "PARTIAL", reasons
    return "SUCCESS", reasons


def host_epoch_seconds(ssh: NodeSSHClient) -> int:
    """Текущее epoch-время хоста (для окон маркеров)."""
    res = ssh.ssh_read("date +%s", timeout=20)
    return int(res.stdout.strip())


def sites_status(ssh: NodeSSHClient, bypass_dns: bool = False) -> dict[str, str]:
    """Проверить HTTP-коды всех сайтов платформы (снаружи-через-хост).

    bypass_dns=True: curl --resolve domain:443:127.0.0.1 — DNS-независимый probe
    (для T2: хостовая резолюция отключена инъекцией, nginx при этом жив).
    """
    status: dict[str, str] = {}
    for url in SITE_URLS:
        from urllib.parse import urlparse

        parsed = urlparse(url)
        host = parsed.netloc
        resolve_flag = f"--resolve {host}:443:127.0.0.1" if bypass_dns else ""
        res = ssh.ssh_read(
            f"curl -s -L --noproxy '*' {resolve_flag} -o /dev/null -w '%{{http_code}}' -m 15 '{url}'",
            timeout=30,
        )
        status[url] = res.stdout.strip()
    return status


def wait_sites_up(
    ssh: NodeSSHClient, timeout_s: int, interval_s: float = 5.0, bypass_dns: bool = False
) -> tuple[bool, dict[str, str]]:
    """Ждать, пока все сайты снова отвечают. Вернуть (ok, последний статус)."""
    deadline = time.monotonic() + timeout_s
    last_status: dict[str, str] = {}
    while time.monotonic() < deadline:
        last_status = sites_status(ssh, bypass_dns=bypass_dns)
        if last_status and all(_http_code_ok(code) for code in last_status.values()):
            return True, last_status
        time.sleep(interval_s)
    return False, last_status


def wait_all_containers(
    ssh: NodeSSHClient, timeout_s: int, interval_s: float = 5.0, containers: list[str] | None = None
) -> tuple[bool, list[str], dict[str, str]]:
    """Ждать, пока все контейнеры эталона снова running (+healthy где есть healthcheck).

    ▶ ┌containers┐ → ○ poll docker ps/inspect → ⎋ (ok, missing, status_map)

    ## @purpose — Единый recovery-предикат для T1/T6/T7/T11: «стек снова здоров».
    ## @invariants
    ##   - Контейнер healthy-статуса: running/healthy (или running без healthcheck)
    ##   - Отсутствующий контейнер = still missing; unhealthy = NOT ready
    """
    wanted = containers or _BASELINE_CONTAINERS
    deadline = time.monotonic() + timeout_s
    last_missing: list[str] = []
    last_status: dict[str, str] = {}
    while time.monotonic() < deadline:
        status_res = ssh.ssh_read("docker ps -a --format '{{.Names}}\t{{.Status}}\t{{.State}}'", timeout=30)
        status_map: dict[str, str] = {}
        for line in status_res.stdout.strip().splitlines():
            parts = line.split("\t")
            if len(parts) >= 2:
                status_map[parts[0]] = parts[1]
        missing = [c for c in wanted if c not in status_map]
        not_ready = [
            c
            for c, st in status_map.items()
            if c in wanted and not (st.startswith("Up") and ("healthy" in st or "(healthy)" in st or ")" not in st))
        ]
        # (unhealthy)/(health: starting)/Exited/Restarting → не готов; "Up N" без скобок → готов
        last_missing = missing + not_ready
        last_status = status_map
        if not last_missing:
            return True, [], status_map
        time.sleep(interval_s)
    return False, last_missing, last_status


# endregion FUNC_audit_helpers
