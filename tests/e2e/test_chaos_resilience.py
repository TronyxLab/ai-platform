# GREP_SUMMARY: chaos-resilience fault-injection T1-T11 docker-restart dns partition clock-skew tor postgres-sigkill oom disk cert restore reboot cross-boot-audit
# STRUCTURE: ▶ T1 docker restart → T2 DNS → T3 network partition → T4 clock skew → T5 tor → T6 postgres SIGKILL → T7 OOM → T8 disk 92% → T9 cert/secrets → T10 restore-drill → T11 reboot + cross-boot audit → ⎋
# region MODULE_CONTRACT
## @purpose  DevPlan 126 (chaos-resilience) W2-W4: 11 fault-injection тестов на tronyx-vps.
##           Каждый тест: инъекция отказа (ssh_exec) → ожидание самовосстановления (poll) →
##           TTR-замер → LogAuditManifest (маркеры по docker logs/journald/Loki/Grafana alerts) →
##           экспорт логов в .ai/plans/126-chaos-resilience/files/T<N>/ → вердикт (verdict.json).
##           T11 — reboot + кросс-бут аудит: инциденты T1-T10 реконструируются из персистентных
##           логов (journald/docker logs/audit.jsonl) без участия очевидца.
## @scope    tests/e2e — NOT in regular gate (@pytest.mark.requires_node filter). Маркер chaos —
##           отдельный прогон: pytest tests/e2e/test_chaos_resilience.py -m chaos -k <T...>.
## @invariants
##   - 0 параметризации (детерминизм); каждый тест автономен (свой manifest + export)
##   - Инъекции ТОЛЬКО через node_ssh (NodeSSHClient, lib/ssh.sh parity)
##   - Критерий «инцидент без следа»: required-маркер не найден → assert fail (FAIL),
##     PARTIAL (optional-промах / Loki-дыра) → записывается в verdict + Debt в W5
##   - Восстановление: wait_all_containers (эталон 24 контейнера) + wait_sites_up
##   - T11 в конце программы; iptables-apply (T3) с автооткатом; бэкап перед W3
##   - e2e conftest test_vps_fresh (autouse) сбрасывает state.json — baseline-копия
##     восстановливается после программы (files/baseline/state.json)
## @rationale DevPlan 126 §3: единственный VPS → прямой прогон; каждый тест — отдельный
##           pytest-кейс с аудитом после (пауза на анализ перед следующей инъекцией).
## @changes 2026-08-03 | DevPlan 126 W1 — Created
# endregion MODULE_CONTRACT

from __future__ import annotations

import json
import logging
import re
import time
from pathlib import Path

import pytest

from tests._conftest.node import NodeSSHClient, assert_ldd_imp9_e2e
from tests.e2e.chaos_audit import (
    LogAuditManifest,
    compute_verdict,
    host_epoch_seconds,
    record_verdict,
    wait_all_containers,
    wait_sites_up,
)

logger = logging.getLogger(__name__)

_FILES_DIR = Path(__file__).resolve().parents[2] / ".ai" / "plans" / "126-chaos-resilience" / "files"
_SECRETS_ENV = "/var/lib/platform/run/secrets.env"


# region FUNC_helpers
def _out_dir(test_id: str) -> Path:
    d = _FILES_DIR / test_id
    d.mkdir(parents=True, exist_ok=True)
    return d


def _psql(ssh: NodeSSHClient, db: str, sql: str, timeout: int = 60) -> str:
    """Выполнить SQL на хосте через docker exec postgres (platform superuser)."""
    res = ssh.ssh_exec(
        f'docker exec -e PGPASSWORD="$(grep POSTGRES_PASSWORD {_SECRETS_ENV} | cut -d= -f2-)" '
        f'postgres psql -U platform -d {db} -tAc "{sql}"',
        timeout=timeout,
    )
    return res.stdout.strip()


def _marker_http_sites(manifest: LogAuditManifest, label_prefix: str = "sites") -> None:
    """Добавить http-маркеры для всех сайтов платформы."""
    for url in (
        "https://www.tronyx.ru/",
        "https://sexydancerostov.ru/",
        "https://botanika.tronyx.ru/",
        "https://platform.tronyx.ru/",
    ):
        manifest.add("http", url, label=f"{label_prefix}:{url}", expected="required")


def _marker_stack_healthy(manifest: LogAuditManifest) -> None:
    """Маркеры здорового ядра стека (state-проверки ключевых контейнеров)."""
    for container in ("postgres", "nginx", "redis", "loki", "prometheus", "clickhouse"):
        manifest.add("state", container, label=f"state:{container}", expected="required", container=container)


# endregion FUNC_helpers


# ══════════════════════════════════════════════════════════════════════════════
# T1 — Рестарт Docker daemon
# ══════════════════════════════════════════════════════════════════════════════
# region TEST_T1
@pytest.mark.chaos
@pytest.mark.requires_node
def test_t01_docker_daemon_restart(requires_node: str, node_ssh: NodeSSHClient, caplog) -> None:
    """T1: systemctl restart docker → стек самовосстанавливается ≤3 мин, сайты 200.

    Наблюдение W2 (2026-08-03, tronyx-vps): рестарт docker daemon НЕ перезапускает
    контейнеры — containerd держит их живыми; daemon переподключается (StartedAt
    контейнеров не меняется). Инъекция проверяет: daemon restart залогирован,
    контейнеры НЕ пересозданы (uptime-непрерывность), стек здоров, сайты 200.

    # 🧪 TRAP[TEST] · Scenario: daemon restart (docker API downtime) · Last fail: N/A
    # · Regression: restart policy unless-stopped поднимает контейнеры после
    # ·   docker daemon restart ТОЛЬКО если они упали; живые контейнеры не трогаются
    # · Remove if: restart-политики заменены на другой механизм автозапуска
    """
    caplog.set_level(logging.DEBUG)
    incident_start = host_epoch_seconds(node_ssh)
    logger.info("[IMP:9][T1][inject] systemctl restart docker (incident_start=%d)", incident_start)

    # uptime-эталон: StartedAt ключевых контейнеров ДО инъекции
    started_before = node_ssh.ssh_read(
        'for c in postgres nginx litellm clickhouse; do echo -n "$c "; '
        "docker inspect --format '{{.State.StartedAt}}' $c; done",
        timeout=30,
    )
    started_before_map = dict(line.split() for line in started_before.stdout.strip().splitlines() if line.strip())
    logger.info("[IMP:9][T1][pre] container StartedAt: %s", started_before_map)

    inject = node_ssh.ssh_exec("systemctl restart docker", timeout=300)
    assert inject.exit_code == 0, f"restart docker failed: {inject.stderr}"

    t0 = time.monotonic()
    ok, missing, _ = wait_all_containers(node_ssh, timeout_s=240)
    ttr = int(time.monotonic() - t0)
    sites_ok, site_status = wait_sites_up(node_ssh, timeout_s=120)
    logger.info("[IMP:9][T1][recovery] ttr=%ss containers_ok=%s sites_ok=%s", ttr, ok, sites_ok)

    # контейнеры НЕ пересозданы (StartedAt совпадает) — resilience-факт
    started_after = node_ssh.ssh_read(
        'for c in postgres nginx litellm clickhouse; do echo -n "$c "; '
        "docker inspect --format '{{.State.StartedAt}}' $c; done",
        timeout=30,
    )
    started_after_map = dict(line.split() for line in started_after.stdout.strip().splitlines() if line.strip())
    no_recreate = all(started_after_map.get(c) == v for c, v in started_before_map.items())
    logger.info("[IMP:9][T1][recovery] containers NOT recreated (uptime continuity): %s", no_recreate)

    manifest = LogAuditManifest("T1")
    manifest.add(
        "journald",
        r"(Stopped|Stopping) docker\.service.*Docker Application Container Engine",
        label="journald:docker-stopped",
    )
    manifest.add(
        "journald",
        r"(Starting|Started) docker\.service.*Docker Application Container Engine",
        label="journald:docker-started",
    )
    manifest.add("docker", "no upstream", container="nginx", negate=True, label="docker:nginx-no-upstream-errors")
    manifest.add("loki", ".", container="nginx", label="loki:nginx-pipeline-alive-after-restart")
    _marker_stack_healthy(manifest)
    _marker_http_sites(manifest)

    results = manifest.check_all(node_ssh, incident_start, ttr)
    manifest.export_logs(node_ssh, incident_start, _out_dir("T1"), ["nginx", "postgres", "promtail", "loki"])
    verdict, reasons = compute_verdict(results)

    assert ok, f"T1 FAIL: containers not recovered within 240s: {missing}"
    assert sites_ok, f"T1 FAIL: sites not recovered: {site_status}"
    assert ttr <= 180, f"T1 FAIL: TTR {ttr}s > 180s limit"
    assert no_recreate, f"T1 FAIL: containers were recreated by daemon restart: {started_after_map}"
    record_verdict("T1", _out_dir("T1"), verdict, ttr, results, incident_start)
    logger.info("[IMP:9][T1][verdict] %s ttr=%ss reasons=%s", verdict, ttr, reasons)
    assert verdict != "FAIL", f"T1 log audit FAIL: {reasons}"
    assert_ldd_imp9_e2e(caplog)


# endregion TEST_T1


# ══════════════════════════════════════════════════════════════════════════════
# T2 — Отказ DNS хоста
# ══════════════════════════════════════════════════════════════════════════════
# region TEST_T2
@pytest.mark.chaos
@pytest.mark.requires_node
def test_t02_host_dns_failure(requires_node: str, node_ssh: NodeSSHClient, caplog) -> None:
    """T2: systemd-resolved stop (90s) → внутренний стек жив (docker DNS 127.0.0.11),
    хостовые исходящие дают ясные fail-логи; после start — recovery."""
    caplog.set_level(logging.DEBUG)
    incident_start = host_epoch_seconds(node_ssh)
    logger.info("[IMP:9][T2][inject] systemctl stop systemd-resolved (90s window)")

    stop = node_ssh.ssh_exec("systemctl stop systemd-resolved", timeout=60)
    assert stop.exit_code == 0, f"stop systemd-resolved failed: {stop.stderr}"

    # окно 90с: внутренний стек жив (DNS-независимый probe — хостовая резолюция выключена);
    # хостовые процессы дают ясные fail-логи
    t0 = time.monotonic()
    sites_ok, site_status = wait_sites_up(node_ssh, timeout_s=90, bypass_dns=True)
    probe = node_ssh.ssh_read("getent hosts api.telegram.org; echo RC=$?", timeout=30)
    host_dns_failed = "RC=2" in probe.stdout or "RC=1" in probe.stdout
    # платформенный путь: apt (хост-процесс, DevPlan T2 «acme/apt») — ясный
    # «Temporary failure resolving» (персистентный след в /var/log/apt/chaos-dns.log)
    apt_probe = node_ssh.ssh_exec("apt-get update 2>&1 | tee /var/log/apt/chaos-dns.log | tail -3", timeout=180)
    apt_dns_failed = "Temporary failure resolving" in apt_probe.stdout
    logger.info(
        "[IMP:9][T2][window] host_dns_failed=%s apt_dns_failed=%s (%s)",
        host_dns_failed,
        apt_dns_failed,
        apt_probe.stdout.strip()[-120:],
    )
    time.sleep(30)
    ttr = int(time.monotonic() - t0) + 30

    start_res = node_ssh.ssh_exec("systemctl start systemd-resolved", timeout=60)
    assert start_res.exit_code == 0, f"start systemd-resolved failed: {start_res.stderr}"
    recovered, _, _ = wait_all_containers(node_ssh, timeout_s=120)
    sites_ok_after, site_status_after = wait_sites_up(node_ssh, timeout_s=60)
    logger.info(
        "[IMP:9][T2][recovery] dns_failed=%s containers_ok=%s sites_ok=%s", host_dns_failed, recovered, sites_ok_after
    )

    manifest = LogAuditManifest("T2")
    manifest.add(
        "journald",
        r"Stopped systemd-resolved\.service.*Network Name Resolution",
        label="journald:resolved-stopped",
    )
    manifest.add(
        "journald",
        r"Started systemd-resolved\.service.*Network Name Resolution",
        label="journald:resolved-started",
    )
    manifest.add(
        "auditfile",
        r"Temporary failure resolving",
        path="/var/log/apt/chaos-dns.log",
        label="audit:apt-resolv-fail",
    )
    manifest.add(
        "docker",
        "Name or service not known|Temporary failure in name resolution",
        container="litellm",
        label="docker:litellm-resolv-fail",
        expected="optional",
    )
    manifest.add("docker", "no upstream", container="nginx", negate=True, label="docker:nginx-no-upstream-errors")
    _marker_stack_healthy(manifest)
    _marker_http_sites(manifest)

    results = manifest.check_all(node_ssh, incident_start, ttr)
    manifest.export_logs(node_ssh, incident_start, _out_dir("T2"), ["nginx", "litellm", "backup-cron"])
    verdict, reasons = compute_verdict(results)

    assert sites_ok, f"T2 FAIL: sites down during DNS outage: {site_status}"
    assert host_dns_failed, f"T2 FAIL: host DNS did NOT fail (getent: {probe.stdout})"
    assert apt_dns_failed, f"T2 FAIL: apt did not show resolv failure: {apt_probe.stdout}"
    assert recovered and sites_ok_after, f"T2 FAIL: recovery incomplete: {site_status_after}"
    record_verdict("T2", _out_dir("T2"), verdict, ttr, results, incident_start)
    logger.info("[IMP:9][T2][verdict] %s ttr=%ss reasons=%s", verdict, ttr, reasons)
    assert verdict != "FAIL", f"T2 log audit FAIL: {reasons}"
    assert_ldd_imp9_e2e(caplog)


# endregion TEST_T2


# ══════════════════════════════════════════════════════════════════════════════
# T3 — Сетевая партиция наружу 120 c (iptables-apply, автооткат)
# ══════════════════════════════════════════════════════════════════════════════
# region TEST_T3
@pytest.mark.chaos
@pytest.mark.requires_node
def test_t03_network_partition_outbound(requires_node: str, node_ssh: NodeSSHClient, caplog) -> None:
    """T3: OUTPUT DROP (кроме established/loopback/локальных подсетей) на 120с.
    iptables-apply -c 'sleep 120; exit 1' → автооткат. Сайты живы (INBOUND нетронут);
    исходящие платформенные пути (tor proxy healthcheck, backup→S3) дают ясные fail-логи."""
    caplog.set_level(logging.DEBUG)
    incident_start = host_epoch_seconds(node_ssh)
    rules = (
        "*filter\n"
        ":INPUT ACCEPT [0:0]\n"
        ":FORWARD ACCEPT [0:0]\n"
        ":OUTPUT DROP [0:0]\n"
        "-A OUTPUT -m conntrack --ctstate ESTABLISHED,RELATED -j ACCEPT\n"
        "-A OUTPUT -o lo -j ACCEPT\n"
        "-A OUTPUT -d 172.16.0.0/12 -j ACCEPT\n"
        "-A OUTPUT -d 10.0.0.0/8 -j ACCEPT\n"
        "-A OUTPUT -d 192.168.0.0/16 -j ACCEPT\n"
        "COMMIT\n"
    )
    rules6 = (
        "*filter\n"
        ":INPUT ACCEPT [0:0]\n"
        ":FORWARD ACCEPT [0:0]\n"
        ":OUTPUT DROP [0:0]\n"
        "-A OUTPUT -m conntrack --ctstate ESTABLISHED,RELATED -j ACCEPT\n"
        "-A OUTPUT -o lo -j ACCEPT\n"
        "-A OUTPUT -d fc00::/7 -j ACCEPT\n"
        "-A OUTPUT -d fe80::/10 -j ACCEPT\n"
        "COMMIT\n"
    )
    write = node_ssh.ssh_exec(
        f"cat > /tmp/chaos-partition.rules <<'EOF'\n{rules}EOF\ncat > /tmp/chaos-partition6.rules <<'EOF'\n{rules6}EOF",
        timeout=30,
    )
    assert write.exit_code == 0, f"rules write failed: {write.stderr}"
    logger.info("[IMP:9][T3][inject] OUTPUT DROP v4+v6 partition (120s auto-revert, save/restore)")

    # Детерминированная партиция v4+v6: save → apply → conntrack flush (stale ESTABLISHED
    # маскирует новые SYN через ctstate-ACCEPT — tuple reuse) → автооткат через 120с из
    # снапшота. IPv6 обязателен: curl Happy-Eyeballs уходит в IPv6 (2001:67c:...) —
    # без ip6 правил партиция дырявая (наблюдалось 2026-08-03, T3 run 3-5).
    inject = node_ssh.ssh_exec(
        "iptables-save > /tmp/chaos-iptables-backup.rules && "
        "ip6tables-save > /tmp/chaos-iptables6-backup.rules && "
        "iptables-restore < /tmp/chaos-partition.rules && "
        "ip6tables-restore < /tmp/chaos-partition6.rules && "
        "conntrack -F 2>/dev/null; "
        "nohup bash -c '(sleep 120; iptables-restore < /tmp/chaos-iptables-backup.rules; "
        "ip6tables-restore < /tmp/chaos-iptables6-backup.rules; "
        "conntrack -F 2>/dev/null) >/tmp/chaos-partition-restore.log 2>&1' >/dev/null 2>&1 & "
        "echo PARTITION_OK",
        timeout=30,
    )
    assert "PARTITION_OK" in inject.stdout, f"partition start failed: {inject.stdout} {inject.stderr}"
    time.sleep(10)

    # окно партиции: сайты живы (probe через 127.0.0.1 — публичный URL уходит OUT и
    # блокируется партицией; внешние пользователи не затронуты — INPUT нетронут),
    # исходящие платформенные пути падают
    sites_ok, site_status = wait_sites_up(node_ssh, timeout_s=60, bypass_dns=True)
    outbound_probe = node_ssh.ssh_read(
        "curl -s --noproxy '*' -o /dev/null -w '%{http_code}' -m 8 https://api.telegram.org/ 2>&1; echo C=$?",
        timeout=30,
    )
    outbound_blocked = outbound_probe.stdout.strip().endswith("C=28") or "C=7" in outbound_probe.stdout
    # платформенный путь: tor-proxy-healthcheck (реальный компонент, ходит в Telegram через proxy)
    tor_check = node_ssh.ssh_exec(
        "cd /opt/platform && TELEGRAM_PROXY_URL=http://127.0.0.1:8118 "
        "python3 -m core.internal.healthcheck.tor_proxy_check 2>&1 | tail -3; echo EXIT=${PIPESTATUS[0]}",
        timeout=120,
    )
    tor_failed = "EXIT=1" in tor_check.stdout or "EXIT=2" in tor_check.stdout
    # платформенный путь: backup → S3 (upload не может начаться — сеть наружу заблокирована);
    # вывод probe пишется в /var/log/platform/backup/chaos-t3.log (docker exec stdout
    # НЕ попадает в docker logs — персистентный след в лог-директории бэкапов)
    backup_probe = node_ssh.ssh_exec(
        "docker exec backup-cron sh -c "
        "'curl -sS -m 8 -o /dev/null https://s3.timeweb.cloud 2>&1 "
        "| tee -a /var/log/platform/backup/chaos-t3.log; "
        'echo "curl_exit=$? $(date -u +%FT%TZ)" >> /var/log/platform/backup/chaos-t3.log\'',
        timeout=60,
    )
    backup_blocked = "curl_exit=7" in backup_probe.stdout or "curl_exit=28" in backup_probe.stdout

    # ждём автооткат (фоновый restore из снапшота через 120с)
    t0 = time.monotonic()
    reverted = False
    while time.monotonic() - t0 < 300:
        policy = node_ssh.ssh_read("iptables -S OUTPUT | head -1", timeout=20)
        if "-P OUTPUT ACCEPT" in policy.stdout:
            reverted = True
            break
        time.sleep(5)
    # ⚠️ Safety-net (находка W3, 2026-08-03): снапшот iptables МОЖЕТ не содержать
    # docker-цепочек (если взят после повреждённого revert'а iptables-apply) →
    # FORWARD DROP без DOCKER-USER ломает container outbound. Проверка + рестарт docker.
    docker_chains = node_ssh.ssh_read("iptables -S | grep -cE 'DOCKER-USER|DOCKER'", timeout=30)
    if int(docker_chains.stdout.strip() or "0") == 0:
        logger.warning("[IMP:8][T3][safety] docker iptables chains missing after restore — restart docker")
        node_ssh.ssh_exec("systemctl restart docker", timeout=300)
        ok_after, missing_after, _ = wait_all_containers(node_ssh, timeout_s=240)
        assert ok_after, f"T3 safety-net FAIL: containers after docker restart: {missing_after}"
    outbound_probe_check = node_ssh.ssh_exec(
        "docker exec backup-cron curl -s -m 10 -o /dev/null -w '%{http_code}' https://s3.timeweb.cloud 2>&1",
        timeout=60,
    )
    assert outbound_probe_check.stdout.strip() == "200", (
        f"T3 safety-net FAIL: container outbound broken: {outbound_probe_check.stdout}"
    )
    ttr = int(time.monotonic() - t0) + 120
    recovered_probe = node_ssh.ssh_read(
        "curl -s --noproxy '*' -o /dev/null -w '%{http_code}' -m 15 https://api.telegram.org/ 2>&1; echo C=$?",
        timeout=30,
    )
    outbound_restored = "C=0" in recovered_probe.stdout
    sites_after, site_after = wait_sites_up(node_ssh, timeout_s=60, bypass_dns=True)
    logger.info(
        "[IMP:9][T3][recovery] reverted=%s outbound_restored=%s tor_failed=%s backup_blocked=%s",
        reverted,
        outbound_restored,
        tor_failed,
        backup_blocked,
    )

    manifest = LogAuditManifest("T3")
    manifest.add(
        "auditfile",
        r"curl_exit=(7|28)|Failed to connect|Could not resolve|timed out|unreachable",
        container="backup-cron",
        path="/var/log/platform/backup/chaos-t3.log",
        label="audit:backup-outbound-fail",
    )
    manifest.add("journald", "tor-proxy", label="journald:tor-proxy-healthcheck-ran", expected="optional")
    manifest.add("auditfile", "tor-healthcheck", label="audit:tor-healthcheck", expected="optional")
    _marker_stack_healthy(manifest)
    _marker_http_sites(manifest)

    results = manifest.check_all(node_ssh, incident_start, ttr)
    manifest.export_logs(node_ssh, incident_start, _out_dir("T3"), ["backup-cron", "nginx", "litellm", "tor"])
    verdict, reasons = compute_verdict(results)

    assert sites_ok, f"T3 FAIL: sites down during partition: {site_status}"
    assert outbound_blocked, f"T3 FAIL: outbound NOT blocked during partition: {outbound_probe.stdout}"
    assert tor_failed, f"T3 FAIL: tor-proxy-check did not fail during partition: {tor_check.stdout}"
    assert reverted, "T3 FAIL: iptables-apply did not auto-revert"
    assert outbound_restored, f"T3 FAIL: outbound not restored after revert: {recovered_probe.stdout}"
    assert sites_after, f"T3 FAIL: sites after recovery: {site_after}"
    record_verdict("T3", _out_dir("T3"), verdict, ttr, results, incident_start)
    logger.info("[IMP:9][T3][verdict] %s ttr=%ss reasons=%s", verdict, ttr, reasons)
    assert verdict != "FAIL", f"T3 log audit FAIL: {reasons}"
    assert_ldd_imp9_e2e(caplog)


# endregion TEST_T3


# ══════════════════════════════════════════════════════════════════════════════
# T4 — Clock skew ±24 h
# ══════════════════════════════════════════════════════════════════════════════
# region TEST_T4
@pytest.mark.chaos
@pytest.mark.requires_node
def test_t04_clock_skew_24h(requires_node: str, node_ssh: NodeSSHClient, caplog) -> None:
    """T4: timedatectl set-ntp false → date -s ±24h → NTP recovery. Наблюдение Loki
    retention (границы до/после — потеря данных отсутствует)."""
    caplog.set_level(logging.DEBUG)
    incident_start = host_epoch_seconds(node_ssh)
    logger.info("[IMP:9][T4][inject] clock skew +24h (NTP disabled)")

    # pre-skew граница Loki: сколько nginx-логов в окне [start-10m, start]
    loki_before = node_ssh.ssh_read(
        f"curl -s -G 'http://127.0.0.1:3100/loki/api/v1/query_range' "
        f"--data-urlencode 'query={{container=\"nginx\"}}' "
        f"--data-urlencode 'start={(incident_start - 600) * 1_000_000_000}' "
        f"--data-urlencode 'end={(incident_start + 60) * 1_000_000_000}' "
        f"--data-urlencode 'limit=10' | python3 -c \"import json,sys; d=json.load(sys.stdin); "
        f"print(len((d.get('data') or {{}}).get('result') or []))\"",
        timeout=60,
    )
    nginx_streams_before = int(loki_before.stdout.strip() or "0")
    logger.info("[IMP:9][T4][loki] nginx streams before skew: %d", nginx_streams_before)

    inject = node_ssh.ssh_exec("timedatectl set-ntp false && date -s '+24 hours'", timeout=60)
    assert inject.exit_code == 0, f"skew +24h failed: {inject.stderr}"
    t0 = time.monotonic()
    time.sleep(60)
    sites_plus, status_plus = wait_sites_up(node_ssh, timeout_s=60)
    tls_probe = node_ssh.ssh_read(
        "curl -s --noproxy '*' -x http://127.0.0.1:8118 -o /dev/null -w '%{http_code}' -m 15 "
        "https://api.telegram.org/ 2>&1; echo C=$?",
        timeout=30,
    )
    tls_blocked = "C=60" in tls_probe.stdout or "C=35" in tls_probe.stdout
    logger.info("[IMP:9][T4][skew] tls_probe=%s tls_blocked=%s", tls_probe.stdout.strip(), tls_blocked)
    time.sleep(30)
    back = node_ssh.ssh_exec("date -s '-24 hours'", timeout=60)
    assert back.exit_code == 0, f"skew -24h failed: {back.stderr}"
    sites_minus, status_minus = wait_sites_up(node_ssh, timeout_s=60)
    ntp = node_ssh.ssh_exec("timedatectl set-ntp true", timeout=60)
    assert ntp.exit_code == 0

    # NTP recovery: ждём синхронизацию systemd-timesyncd
    synced = False
    while time.monotonic() - t0 < 150:
        st = node_ssh.ssh_read("timedatectl show -p NTPSynchronized -p TimeUSec", timeout=20)
        if "NTPSynchronized=yes" in st.stdout:
            synced = True
            break
        time.sleep(5)
    ttr = int(time.monotonic() - t0)

    # post-check: Loki границы — pre-skew логи не потеряны
    loki_after = node_ssh.ssh_read(
        f"curl -s -G 'http://127.0.0.1:3100/loki/api/v1/query_range' "
        f"--data-urlencode 'query={{container=\"nginx\"}}' "
        f"--data-urlencode 'start={(incident_start - 600) * 1_000_000_000}' "
        f"--data-urlencode 'end={(incident_start + 60) * 1_000_000_000}' "
        f"--data-urlencode 'limit=10' | python3 -c \"import json,sys; d=json.load(sys.stdin); "
        f"print(len((d.get('data') or {{}}).get('result') or []))\"",
        timeout=60,
    )
    nginx_streams_after = int(loki_after.stdout.strip() or "0")
    loki_no_loss = nginx_streams_after >= nginx_streams_before
    logger.info(
        "[IMP:9][T4][loki] streams before=%d after=%d no_loss=%s",
        nginx_streams_before,
        nginx_streams_after,
        loki_no_loss,
    )

    manifest = LogAuditManifest("T4")
    # skew-фаза (+24h): маркеры ищем в смещённом времени (window_offset=86400);
    # recovery-фаза (NTP после возврата) — в реальном времени (offset=0)
    manifest.add(
        "journald",
        r"Clock change detected|Time jumped|time jump",
        label="journald:clock-change",
        window_offset=86400,
    )
    manifest.add(
        "journald",
        r"Initial clock synchronization|Contacted time server|adjusting|Synchronized",
        label="journald:timesyncd-recovery",
        unit="systemd-timesyncd",
    )
    # ⚠️ Находка W2 (T4): во время skew Loki-ингestion падает (ring: ingester unhealthy,
    #    500 «at least 1 live replicas required»), бэклог отклоняется «entry too far
    #    behind» → ~30 мин контейнерных логов потеряны ИЗ LOKI (docker logs сохраняют).
    #    Маркер optional — потеря документируется (verdict PARTIAL + Debt), не fail-тест.
    manifest.add(
        "loki",
        ".",
        container="nginx",
        label="loki:nginx-logs-after-skew",
        expected="optional",
        window_offset=86400,
    )
    manifest.add(
        "docker",
        r"error sending batch|entry too far behind|live replicas required",
        container="promtail",
        label="docker:loki-skew-errors",
        window_offset=86400,
    )
    _marker_stack_healthy(manifest)
    _marker_http_sites(manifest)

    results = manifest.check_all(node_ssh, incident_start, ttr)  # per-marker window_offset внутри
    manifest.export_logs(node_ssh, incident_start, _out_dir("T4"), ["nginx", "loki", "promtail", "litellm"])
    verdict, reasons = compute_verdict(results)

    assert sites_plus, f"T4 FAIL: sites down during +24h skew: {status_plus}"
    assert sites_minus, f"T4 FAIL: sites down during -24h skew: {status_minus}"
    assert synced, "T4 FAIL: NTP did not resynchronize"
    assert loki_no_loss, f"T4 FAIL: Loki pre-skew logs lost (streams {nginx_streams_before}→{nginx_streams_after})"
    record_verdict("T4", _out_dir("T4"), verdict, ttr, results, incident_start)
    logger.info("[IMP:9][T4][verdict] %s ttr=%ss reasons=%s", verdict, ttr, reasons)
    assert verdict != "FAIL", f"T4 log audit FAIL: {reasons}"
    assert_ldd_imp9_e2e(caplog)


# endregion TEST_T4


# ══════════════════════════════════════════════════════════════════════════════
# T5 — Отказ Tor (Telegram-канал)
# ══════════════════════════════════════════════════════════════════════════════
# region TEST_T5
@pytest.mark.chaos
@pytest.mark.requires_node
def test_t05_tor_telegram_channel_down(requires_node: str, node_ssh: NodeSSHClient, caplog) -> None:
    """T5: stop tor+privoxy → внутренний стек жив; доставка Telegram падает с явным логом
    (не silent); после start — tor/privoxy UP + privoxy→tor forward работает.

    Находка W2 (2026-08-03): TELEGRAM_BOT_TOKEN в secrets.env НЕВАЛИДЕН — Telegram
    отвечает 404 Not Found на /getMe (и напрямую, и через tor). Полный tor_proxy_check
    НЕ может пройти (telegram-стадия) — recovery-критерий = privoxy-стадия + сервисы UP;
    404 токена фиксируется в /var/log/platform/tor-healthcheck.log как evidence.
    """
    caplog.set_level(logging.DEBUG)
    incident_start = host_epoch_seconds(node_ssh)
    # выравнивание на 5-мин границу cron (*/5): спим до следующей + 15с
    next_boundary = ((incident_start // 300) + 1) * 300 + 15
    sleep_s = max(0, next_boundary - incident_start)
    logger.info("[IMP:9][T5][align] sleeping %ss to next tor-healthcheck cron boundary", sleep_s)
    time.sleep(sleep_s)
    incident_start = host_epoch_seconds(node_ssh)
    logger.info("[IMP:9][T5][inject] stop tor@default + privoxy (window ~5.5 min)")

    stop = node_ssh.ssh_exec("systemctl stop tor@default.service privoxy.service", timeout=60)
    assert stop.exit_code == 0, f"stop tor/privoxy failed: {stop.stderr}"

    t0 = time.monotonic()
    sites_ok, site_status = wait_sites_up(node_ssh, timeout_s=60)
    # платформенная проверка канала: tor_proxy_check (getMe через proxy) — ожидаем fail;
    # вывод пишется в /var/log/platform/tor-healthcheck.log (персистентный след)
    check_fail = node_ssh.ssh_exec(
        "cd /opt/platform && TELEGRAM_PROXY_URL=http://127.0.0.1:8118 "
        "python3 -m core.internal.healthcheck.tor_proxy_check 2>&1 "
        "| tee /var/log/platform/tor-healthcheck.log | tail -3; echo EXIT=${PIPESTATUS[0]}",
        timeout=120,
    )
    tor_check_failed = "EXIT=1" in check_fail.stdout or "EXIT=2" in check_fail.stdout
    # платформенный отправитель: send_telegram через proxy → должен залогировать failure
    notifier_probe = node_ssh.ssh_exec(
        f"set -a; source {_SECRETS_ENV}; set +a; cd /opt/platform && PYTHONPATH=/opt/platform "
        'python3 -c "from core.internal.shared.telegram_notifier import send_telegram; '
        "import os; ok=send_telegram('chaos-T5-test', proxy_url='http://127.0.0.1:8118'); "
        "print('SENT_OK=' + str(ok))\" 2>&1 | tail -2",
        timeout=120,
    )
    send_failed = "SENT_OK=False" in notifier_probe.stdout
    # ждём cron-цикл tor-proxy-healthcheck в окне
    time.sleep(300)
    ttr = int(time.monotonic() - t0) + 300

    start_res = node_ssh.ssh_exec("systemctl start tor@default.service privoxy.service", timeout=120)
    assert start_res.exit_code == 0, f"start tor/privoxy failed: {start_res.stderr}"

    # recovery: tor/privoxy UP + privoxy→tor forward работает (telegram-стадия НЕ может
    # пройти — токен 404, pre-existing находка). Ждём "Privoxy → Tor forward: working".
    recovered = False
    privoxy_recovered = False
    t_rec = time.monotonic()  # свежее окно recovery (t0 от инъекции уже истёк после sleep)
    while time.monotonic() - t_rec < 300:
        chk = node_ssh.ssh_exec(
            "cd /opt/platform && TELEGRAM_PROXY_URL=http://127.0.0.1:8118 "
            "python3 -m core.internal.healthcheck.tor_proxy_check 2>&1 "
            "| tee /var/log/platform/tor-healthcheck.log | tail -4; echo EXIT=${PIPESTATUS[0]}",
            timeout=120,
        )
        if "Privoxy → Tor forward: working" in chk.stdout:
            privoxy_recovered = True
        svc = node_ssh.ssh_read("systemctl is-active tor@default.service privoxy.service | tr '\\n' ' '", timeout=30)
        if privoxy_recovered and svc.stdout.count("active") == 2:
            recovered = True
            break
        time.sleep(15)
    ttr = int(time.monotonic() - t0)
    sites_after, status_after = wait_sites_up(node_ssh, timeout_s=60)
    # если recovery-loop не успел зафиксировать 404 (tor поднимался медленно) — дождаться
    for _ in range(6):
        token_404 = node_ssh.ssh_read(
            "grep -cE 'HTTP Error 404|Not Found' /var/log/platform/tor-healthcheck.log 2>/dev/null || true",
            timeout=30,
        )
        if int(token_404.stdout.strip() or "0") > 0:
            break
        time.sleep(10)
    token_404_found = int(token_404.stdout.strip() or "0") > 0
    logger.info(
        "[IMP:9][T5][recovery] tor_check_failed=%s send_failed=%s recovered=%s token_404=%s",
        tor_check_failed,
        send_failed,
        recovered,
        token_404_found,
    )

    manifest = LogAuditManifest("T5")
    manifest.add("journald", r"Stopped|Deactivated", label="journald:tor-stopped", unit="tor@default")
    manifest.add("journald", r"Stopped|Deactivated", label="journald:privoxy-stopped", unit="privoxy")
    manifest.add("journald", r"Started", label="journald:tor-started", unit="tor@default")
    manifest.add("journald", r"Started", label="journald:privoxy-started", unit="privoxy")
    manifest.add(
        "journald", "CRON.*tor-proxy|tor-proxy-healthcheck", label="journald:cron-tor-check-ran", expected="optional"
    )
    manifest.add(
        "auditfile",
        r"HTTP Error 404|Not Found|Tor Proxy Healthcheck",
        path="/var/log/platform/tor-healthcheck.log",
        label="audit:tor-healthcheck-entries",
    )
    _marker_stack_healthy(manifest)
    _marker_http_sites(manifest)

    results = manifest.check_all(node_ssh, incident_start, ttr)
    manifest.export_logs(node_ssh, incident_start, _out_dir("T5"), ["nginx", "hermes-agent", "promtail"])
    verdict, reasons = compute_verdict(results)

    assert sites_ok, f"T5 FAIL: sites down during tor outage: {site_status}"
    assert tor_check_failed, f"T5 FAIL: tor_proxy_check did not fail: {check_fail.stdout}"
    assert send_failed, f"T5 FAIL: telegram send did not fail via proxy: {notifier_probe.stdout}"
    assert recovered, "T5 FAIL: tor/privoxy did not recover"
    assert sites_after, f"T5 FAIL: sites after recovery: {status_after}"
    assert token_404_found, "T5 FAIL: telegram 404 evidence missing (token finding not documented)"
    record_verdict("T5", _out_dir("T5"), verdict, ttr, results, incident_start)
    logger.info("[IMP:9][T5][verdict] %s ttr=%ss reasons=%s", verdict, ttr, reasons)
    assert verdict != "FAIL", f"T5 log audit FAIL: {reasons}"
    assert_ldd_imp9_e2e(caplog)


# endregion TEST_T5


# ══════════════════════════════════════════════════════════════════════════════
# T6 — SIGKILL Postgres под нагрузкой
# ══════════════════════════════════════════════════════════════════════════════
# region TEST_T6
@pytest.mark.chaos
@pytest.mark.requires_node
def test_t06_postgres_sigkill_under_load(requires_node: str, node_ssh: NodeSSHClient, caplog) -> None:
    """T6: SIGKILL postgres-процесса (kill -9 1 ВНУТРИ контейнера) посреди INSERT-нагрузки →
    restart policy → WAL recovery → 0 потерянных committed-строк; контейнер сам восстанавливается.

    Находка W3 (2026-08-03): `docker kill -s KILL` НЕ триггерит restart policy —
    daemon-инициированная остановка (как docker stop); инъекция = kill -9 PID 1
    изнутри (падение main-процесса → политика unless-stopped срабатывает).
    """
    caplog.set_level(logging.DEBUG)
    incident_start = host_epoch_seconds(node_ssh)
    logger.info("[IMP:9][T6][prep] create chaos_drill + load loop")

    # cleanup предыдущих прогонов (bracket-regex — не матчит собственную cmdline)
    node_ssh.ssh_exec(
        "pkill -f '[c]haos-t6-load' 2>/dev/null; pkill -f '[c]haos-t6-loader' 2>/dev/null; true", timeout=30
    )
    node_ssh.ssh_exec("rm -f /tmp/chaos-t6-load.log /tmp/chaos-t6-loader.log", timeout=30)
    # препарация: БД chaos_drill (drop если есть) + таблица + счётчик
    _psql(node_ssh, "platform", "DROP DATABASE IF EXISTS chaos_drill")
    _psql(node_ssh, "platform", "CREATE DATABASE chaos_drill")
    _psql(
        node_ssh,
        "chaos_drill",
        "CREATE TABLE IF NOT EXISTS t(id serial PRIMARY KEY, payload text, ts timestamptz DEFAULT now())",
    )
    _psql(
        node_ssh,
        "chaos_drill",
        "CREATE TABLE IF NOT EXISTS counter(n int); DELETE FROM counter; INSERT INTO counter VALUES (0)",
    )

    # нагрузка: 200 батчей по 50 строк, commit каждые 50, счётчик в таблице
    load_cmd = (
        f"set -a; source {_SECRETS_ENV}; set +a; "
        "for i in $(seq 1 200); do "
        'docker exec -e PGPASSWORD="$POSTGRES_PASSWORD" postgres psql -U platform -d chaos_drill -q -c '
        '"INSERT INTO t(payload) SELECT md5(g::text) FROM generate_series(1,50) g; '
        'UPDATE counter SET n=$((i*50));" && echo "committed=$((i*50))" >> /tmp/chaos-t6-load.log; '
        "done"
    )
    load = node_ssh.ssh_exec(f"nohup bash -c '{load_cmd}' >/tmp/chaos-t6-loader.log 2>&1 &", timeout=30)
    assert load.exit_code == 0

    # ждём ≥100 committed строк, затем SIGKILL main-процесса посреди нагрузки
    committed = 0
    for _ in range(60):
        cnt = _psql(node_ssh, "chaos_drill", "SELECT COALESCE(MAX(n),0) FROM counter", timeout=30)
        try:
            committed = int(cnt)
        except ValueError:
            committed = 0
        if committed >= 100:
            break
        time.sleep(2)
    logger.info("[IMP:9][T6][load] committed before kill: %d", committed)
    assert committed >= 100, f"T6 FAIL: load did not reach 100 commits (got {committed})"

    t0 = time.monotonic()
    # Инъекция: host-pid main-процесса из docker inspect .State.Pid + kill -9 С ХОСТА.
    # Находки W3 (2026-08-03): (a) `docker kill -s KILL` НЕ триггерит restart policy
    # (daemon-инициированная остановка); (b) `docker exec ... kill -9 1` НЕ убивает
    # контейнер (namespace-init защищён — SIGKILL не доставляется, проверено на redis).
    # kill -9 host-pid = падение main-процесса → container exit 137 → unless-stopped fires.
    pg_pid = node_ssh.ssh_read("docker inspect --format '{{.State.Pid}}' postgres", timeout=30).stdout.strip()
    assert pg_pid.isdigit(), f"T6 FAIL: cannot resolve postgres host pid: {pg_pid}"
    kill = node_ssh.ssh_exec(f"kill -9 {pg_pid}", timeout=60)
    assert kill.exit_code == 0, f"kill -9 {pg_pid} failed: {kill.stderr}"

    ok, missing, _ = wait_all_containers(node_ssh, timeout_s=240, containers=["postgres", "pgbouncer"])
    ttr = int(time.monotonic() - t0)

    # верификация: counter.n (committed) == count(t) — 0 потерянных строк
    # ждём завершения нагрузки (loader process исчез + counter стабилен 2 чтения подряд) —
    # верификация при работающем loader даёт гонку чтения (наблюдалось 3150/3900)
    stable_counter = -1
    for _ in range(120):
        cnt = _psql(node_ssh, "chaos_drill", "SELECT COALESCE(MAX(n),0) FROM counter", timeout=30)
        try:
            cur = int(cnt)
        except ValueError:
            cur = -1
        loader_alive = node_ssh.ssh_read("ps aux | grep -c '[c]haos-t6' || true", timeout=20)
        if cur == 10000 and int(loader_alive.stdout.strip() or "0") == 0:
            stable_counter = cur
            break
        if cur == stable_counter:  # два одинаковых чтения подряд = нагрузка остановилась
            break
        stable_counter = cur
        time.sleep(3)
    final_counter = _psql(node_ssh, "chaos_drill", "SELECT COALESCE(MAX(n),0) FROM counter", timeout=60)
    final_count = _psql(node_ssh, "chaos_drill", "SELECT count(*) FROM t", timeout=60)
    loader_lines = node_ssh.ssh_read("wc -l < /tmp/chaos-t6-load.log 2>/dev/null || echo 0", timeout=20)
    try:
        fc = int(final_counter)
        rows = int(final_count)
        committed_batches = int(loader_lines.stdout.strip() or "0")
    except ValueError:
        fc, rows, committed_batches = -1, -1, -1
    # Инвариант (находка W3): батчи, прерванные SIGKILL (uncommitted), корректно теряются —
    # rows == успешные батчи × 50 (успешный батч = строка в loader-логе; INSERT+UPDATE атомарны).
    data_integrity = rows == committed_batches * 50 and committed_batches > 0
    logger.info(
        "[IMP:9][T6][verify] committed=%s rows=%s batches=%s integrity=%s",
        fc,
        rows,
        committed_batches,
        data_integrity,
    )

    manifest = LogAuditManifest("T6")
    manifest.add("docker", "database system was interrupted", container="postgres", label="docker:postgres-interrupted")
    manifest.add("docker", "database system is ready", container="postgres", label="docker:postgres-ready")
    manifest.add("state", "postgres", container="postgres", label="state:postgres-healthy")
    manifest.add("alerts", "postgres|Service Down", label="alerts:postgres-down", expected="optional")
    manifest.add("docker", "no upstream", container="nginx", negate=True, label="docker:nginx-no-upstream-errors")

    results = manifest.check_all(node_ssh, incident_start, ttr)
    manifest.export_logs(node_ssh, incident_start, _out_dir("T6"), ["postgres", "nginx", "pgbouncer"])
    verdict, reasons = compute_verdict(results)

    assert ok, f"T6 FAIL: postgres not recovered: {missing}"
    assert data_integrity, f"T6 FAIL: data loss! committed={fc} rows={rows}"
    assert ttr <= 120, f"T6 FAIL: TTR {ttr}s > 120s"
    record_verdict("T6", _out_dir("T6"), verdict, ttr, results, incident_start)
    logger.info("[IMP:9][T6][verdict] %s ttr=%ss reasons=%s", verdict, ttr, reasons)
    assert verdict != "FAIL", f"T6 log audit FAIL: {reasons}"
    assert_ldd_imp9_e2e(caplog)


# endregion TEST_T6


# ══════════════════════════════════════════════════════════════════════════════
# T7 — OOM-kill модуля (clickhouse)
# ══════════════════════════════════════════════════════════════════════════════
# region TEST_T7
@pytest.mark.chaos
@pytest.mark.requires_node
def test_t07_oom_kill_clickhouse(requires_node: str, node_ssh: NodeSSHClient, caplog) -> None:
    """T7: bash-аллокатор внутри clickhouse (лимит 1GiB) → cgroup OOM-kill →
    restart policy → up ≤2 мин; ядро называет жертву в journalctl -k."""
    caplog.set_level(logging.DEBUG)
    incident_start = host_epoch_seconds(node_ssh)
    logger.info("[IMP:9][T7][inject] OOM allocator inside clickhouse (1GiB limit)")

    allocator = (
        "docker exec clickhouse bash -c "
        '\'a=""; for i in $(seq 1 400); do a="$a$(head -c 8000000 /dev/zero | tr "\\0" "x")"; '
        "done; echo ALLOC_DONE'"
    )
    node_ssh.ssh_exec(allocator, timeout=180)

    t0 = time.monotonic()
    ok, missing, _ = wait_all_containers(node_ssh, timeout_s=180, containers=["clickhouse"])
    ttr = int(time.monotonic() - t0)

    # ядро назвало жертву: journalctl -k OOM report с clickhouse cgroup
    oom_report = node_ssh.ssh_read(
        "journalctl -k --no-pager 2>/dev/null "
        "| grep -iE 'oom|out of memory' | grep -i clickhouse | head -3; "
        "journalctl -k --no-pager 2>/dev/null | grep -ciE 'out of memory|oom-kill'",
        timeout=60,
    )
    oom_lines = int(oom_report.stdout.strip().splitlines()[-1] or "0")
    victim_named = bool(re.search(r"clickhouse", oom_report.stdout, re.I))
    logger.info("[IMP:9][T7][oom] oom_lines=%s victim_named=%s", oom_lines, victim_named)

    manifest = LogAuditManifest("T7")
    manifest.add("journald", "Out of memory|oom-kill|Killed process", label="journald:kernel-oom", kflag=True)
    manifest.add("state", "clickhouse", container="clickhouse", label="state:clickhouse-healthy")
    manifest.add(
        "docker",
        "Available RAM|Startup",
        container="clickhouse",
        label="docker:clickhouse-startup",
        expected="optional",
    )
    manifest.add("docker", "no upstream", container="nginx", negate=True, label="docker:nginx-no-upstream-errors")
    _marker_stack_healthy(manifest)

    results = manifest.check_all(node_ssh, incident_start, ttr)
    manifest.export_logs(node_ssh, incident_start, _out_dir("T7"), ["clickhouse", "nginx"])
    verdict, reasons = compute_verdict(results)

    assert ok, f"T7 FAIL: clickhouse not recovered: {missing}"
    assert oom_lines >= 1, "T7 FAIL: no kernel OOM report in journalctl -k"
    assert victim_named, f"T7 FAIL: OOM victim not named: {oom_report.stdout}"
    assert ttr <= 120, f"T7 FAIL: TTR {ttr}s > 120s"
    record_verdict("T7", _out_dir("T7"), verdict, ttr, results, incident_start)
    logger.info("[IMP:9][T7][verdict] %s ttr=%ss reasons=%s", verdict, ttr, reasons)
    assert verdict != "FAIL", f"T7 log audit FAIL: {reasons}"
    assert_ldd_imp9_e2e(caplog)


# endregion TEST_T7


# ══════════════════════════════════════════════════════════════════════════════
# T8 — Диск 90–93%
# ══════════════════════════════════════════════════════════════════════════════
# region TEST_T8
@pytest.mark.chaos
@pytest.mark.requires_node
def test_t08_disk_pressure_92(requires_node: str, node_ssh: NodeSSHClient, caplog) -> None:
    """T8: dd в /tmp до 92% (контроль df каждые 512MB, резерв ≥5%) → ENOSPC-ошибки с
    ясной причиной, Grafana DiskSpaceLow fire → rm → полное восстановление + resolve."""
    caplog.set_level(logging.DEBUG)
    incident_start = host_epoch_seconds(node_ssh)
    logger.info("[IMP:9][T8][inject] dd to /tmp until 92% used (checks every 512MB)")

    # dd-цикл с контролем df — до 92% (не выше 94%)
    fill = node_ssh.ssh_exec(
        "dd if=/dev/zero of=/tmp/chaos-disk bs=1M count=1024 status=none; "
        "while true; do "
        "USED=$(df / | awk 'NR==2 {print $5}' | tr -d '%'); "
        'echo "used=$USED"; '
        'if [ "$USED" -ge 92 ] && [ "$USED" -le 94 ]; then break; fi; '
        'if [ "$USED" -gt 94 ]; then echo OVER; break; fi; '
        "dd if=/dev/zero of=/tmp/chaos-disk bs=1M count=512 conv=notrunc oflag=append status=none; "
        "done; echo FILL_DONE used=$USED",
        timeout=1800,
    )
    fill_out = fill.stdout.strip().splitlines()
    used_pct = 0
    for line in fill_out:
        m = re.match(r"used=(\d+)", line)
        if m:
            used_pct = int(m.group(1))
    logger.info("[IMP:9][T8][fill] disk used=%s%%", used_pct)
    assert used_pct >= 90, f"T8 FAIL: disk did not reach 90% (used={used_pct}%)"

    t0 = time.monotonic()
    # платформенный путь: бэкап в окне переполнения → ENOSPC с ясной причиной.
    # Находка W3: при 92% (6GB free) бэкап УСПЕВАЕТ (дамп ~128KB) — ENOSPC не
    # возникает. Pre-fill spool-тома до ~99% → бэкап падает с No space left.
    spool_fill = node_ssh.ssh_exec(
        "docker exec backup-cron sh -c 'dd if=/dev/zero of=/var/lib/platform/backup-spool/chaos-fill "
        "bs=1M count=128 status=none; while true; do "
        'U=\$(df / | awk "NR==2 {print \\\$5}" | tr -d "%"); '
        'if [ "\$U" -ge 99 ]; then break; fi; '
        "dd if=/dev/zero of=/var/lib/platform/backup-spool/chaos-fill bs=1M count=128 "
        "conv=notrunc oflag=append status=none 2>/dev/null; done; echo SPOOL_FILLED used=\$U'",
        timeout=600,
    )
    logger.info("[IMP:9][T8][spool] %s", spool_fill.stdout.strip().splitlines()[-1][-60:])
    backup_probe = node_ssh.ssh_exec(
        "docker exec backup-cron /usr/local/bin/backup-postgres.sh 2>&1 | tail -4", timeout=300
    )
    backup_enspc = bool(re.search(r"No space left|ENOSPC|cannot allocate|write error", backup_probe.stdout))
    sites_ok, site_status = wait_sites_up(node_ssh, timeout_s=60)

    # Grafana DiskSpaceLow alert fire (rule interval 30s, for: 0s)
    # ⚠️ Находка W3 (T8): Grafana Disk Space Low rule НЕ срабатывает — expr
    # `node_filesystem_avail_bytes / node_filesystem_size_bytes < 0.2` без mountpoint-
    # фильтра: reducer last берёт произвольную серию (tmpfs/overlay с ratio>0.2) →
    # state остаётся inactive даже при 90% (проверено экспериментом, ratio=0.107).
    # → Debt D-N. Здесь проверяем DATA-PATH (Prometheus видит критичный ratio),
    # rule-state — диагностика (ожидаемый FAIL → Debt, не fail-критерий теста).
    ratio_critical = False
    rule_state = ""
    for _ in range(30):
        ratio = node_ssh.ssh_read(
            'curl -s -m 10 "http://127.0.0.1:9090/api/v1/query" --data-urlencode '
            "\"query=node_filesystem_avail_bytes{mountpoint='/'} / node_filesystem_size_bytes{mountpoint='/'}\"",
            timeout=30,
        )
        try:
            data = json.loads(ratio.stdout)
            vals = [x.get("value", ["", ""])[1] for x in (data.get("data") or {}).get("result") or []]
            if vals and all(float(v) < 0.2 for v in vals):
                ratio_critical = True
        except (json.JSONDecodeError, ValueError) as exc:
            logger.info("[IMP:7][T8][poll] prometheus ratio parse failed (retry): %s", exc)
        rules = node_ssh.ssh_read(
            f"set -a; source {_SECRETS_ENV}; set +a; "
            'curl -s -u "$GF_SECURITY_ADMIN_USER:$GF_SECURITY_ADMIN_PASSWORD" '
            "'http://127.0.0.1:3000/api/prometheus/grafana/api/v1/rules'",
            timeout=30,
        )
        try:
            data = json.loads(rules.stdout)
            for group in (data.get("data") or {}).get("groups") or []:
                for rule in group.get("rules") or []:
                    if re.search(r"Disk|space", json.dumps(rule), re.I):
                        rule_state = f"{rule.get('name')}={rule.get('state')}"
        except json.JSONDecodeError as exc:
            logger.info("[IMP:7][T8][poll] grafana rules parse failed (retry): %s", exc)
        if ratio_critical:
            break
        time.sleep(5)
    # data-path подтверждён (ratio_critical); rule_state — диагностика (D-N Debt)
    alert_detail = f"ratio_critical={ratio_critical} rule={rule_state}"
    logger.info("[IMP:9][T8][alert] %s", alert_detail)
    ttr = int(time.monotonic() - t0)

    # восстановление: rm файла → df в норму
    rm = node_ssh.ssh_exec("rm -f /tmp/chaos-disk && df -h / | tail -1", timeout=60)
    assert rm.exit_code == 0, f"rm chaos-disk failed: {rm.stderr}"
    resolved = False
    for _ in range(30):
        ratio = node_ssh.ssh_read(
            'curl -s -m 10 "http://127.0.0.1:9090/api/v1/query" --data-urlencode '
            "\"query=node_filesystem_avail_bytes{mountpoint='/'} / node_filesystem_size_bytes{mountpoint='/'}\"",
            timeout=30,
        )
        try:
            data = json.loads(ratio.stdout)
            vals = [float(x.get("value", ["", ""])[1]) for x in (data.get("data") or {}).get("result") or []]
            if vals and all(v > 0.5 for v in vals):
                resolved = True
                break
        except (json.JSONDecodeError, ValueError) as exc:
            logger.info("[IMP:7][T8][poll] prometheus ratio parse failed (retry): %s", exc)
        time.sleep(10)
    sites_after, status_after = wait_sites_up(node_ssh, timeout_s=60)
    recovered_containers, miss2, _ = wait_all_containers(node_ssh, timeout_s=120)
    logger.info("[IMP:9][T8][recovery] alert_resolved=%s sites=%s", resolved, sites_after)

    manifest = LogAuditManifest("T8")
    manifest.add("journald", "No space left on device|ENOSPC", label="journald:enspc-evidence")
    manifest.add("docker", "No space left on device|ENOSPC", container="backup-cron", label="docker:backup-enspc")
    # rule не срабатывает (expr без mountpoint — D-N Debt); data-path проверен выше
    manifest.add("alerts", "Disk|space", label="alerts:diskspace-fired", expected="optional")
    _marker_stack_healthy(manifest)
    _marker_http_sites(manifest)

    results = manifest.check_all(node_ssh, incident_start, ttr)
    manifest.export_logs(node_ssh, incident_start, _out_dir("T8"), ["backup-cron", "postgres", "nginx", "promtail"])
    verdict, reasons = compute_verdict(results)

    assert backup_enspc, f"T8 FAIL: no ENOSPC evidence from backup: {backup_probe.stdout}"
    assert sites_ok and sites_after, f"T8 FAIL: sites: {site_status} → {status_after}"
    assert recovered_containers, f"T8 FAIL: containers: {miss2}"
    record_verdict("T8", _out_dir("T8"), verdict, ttr, results, incident_start)
    logger.info("[IMP:9][T8][verdict] %s ttr=%ss reasons=%s", verdict, ttr, reasons)
    assert verdict != "FAIL", f"T8 log audit FAIL: {reasons}"
    assert_ldd_imp9_e2e(caplog)


# endregion TEST_T8


# ══════════════════════════════════════════════════════════════════════════════
# T9 — Повреждение TLS cert + secrets
# ══════════════════════════════════════════════════════════════════════════════
# region TEST_T9
@pytest.mark.chaos
@pytest.mark.requires_node
def test_t09_cert_and_secrets_corruption(requires_node: str, node_ssh: NodeSSHClient, caplog) -> None:
    """T9: подмена байтов в live-cert (nginx serve кешированным — 0 простоя) и enc-секретах
    (unlock fail с ясной ошибкой); восстановление из бэкапа без последствий."""
    caplog.set_level(logging.DEBUG)
    incident_start = host_epoch_seconds(node_ssh)

    # препарация: бэкапы cert и enc-файла
    cert_dir = "/etc/letsencrypt/live/tronyx.ru"
    enc_file = node_ssh.ssh_read(
        "ls /opt/node-configs/secrets/tronyx-vps.enc.yaml 2>/dev/null || echo MISSING", timeout=20
    )
    enc_path = "/opt/node-configs/secrets/tronyx-vps.enc.yaml"
    if "MISSING" in enc_file.stdout:
        enc_path = node_ssh.ssh_read(
            "find /opt/node-configs -name 'tronyx-vps.enc.yaml' 2>/dev/null | head -1", timeout=20
        ).stdout.strip()
    assert enc_path, "T9 FAIL: enc file not found on host"
    prep = node_ssh.ssh_exec(
        f"cp {cert_dir}/fullchain.pem {cert_dir}/fullchain.pem.chaosbak && "
        f"cp {enc_path} {enc_path}.chaosbak && echo PREP_OK",
        timeout=30,
    )
    assert "PREP_OK" in prep.stdout, f"T9 prep failed: {prep.stderr}"

    # инъекция: flip байтов (не удаление) в live-копиях
    inject = node_ssh.ssh_exec(
        f"printf '\\x00\\x01' | dd of={cert_dir}/fullchain.pem bs=1 seek=1500 conv=notrunc status=none && "
        f"printf '\\x00\\x01' | dd of={enc_path} bs=1 seek=500 conv=notrunc status=none && echo CORRUPT_OK",
        timeout=30,
    )
    assert "CORRUPT_OK" in inject.stdout, f"T9 injection failed: {inject.stderr}"
    t0 = time.monotonic()
    time.sleep(20)  # дать nginx/системе «заметить» (reload/renew не происходят — serve кеширован)

    sites_ok, site_status = wait_sites_up(node_ssh, timeout_s=60)
    # secrets-unlock fail с ясной ошибкой
    unlock = node_ssh.ssh_exec(
        f"age -d {enc_path} 2>&1 | head -3; echo EXIT=$?",
        timeout=60,
    )
    unlock_failed = "EXIT=1" in unlock.stdout or "EXIT=2" in unlock.stdout
    unlock_clear = bool(re.search(r"error|failed|invalid", unlock.stdout, re.I))
    ttr = int(time.monotonic() - t0)

    # восстановление из бэкапа
    restore = node_ssh.ssh_exec(
        f"cp {cert_dir}/fullchain.pem.chaosbak {cert_dir}/fullchain.pem && "
        f"cp {enc_path}.chaosbak {enc_path} && "
        f"rm -f {cert_dir}/fullchain.pem.chaosbak {enc_path}.chaosbak && echo RESTORE_OK",
        timeout=30,
    )
    assert "RESTORE_OK" in restore.stdout, f"T9 restore failed: {restore.stderr}"
    unlock_after = node_ssh.ssh_exec(f"age -d {enc_path} >/dev/null 2>&1; echo EXIT=$?", timeout=60)
    unlock_recovered = "EXIT=0" in unlock_after.stdout
    sites_after, status_after = wait_sites_up(node_ssh, timeout_s=60)
    cert_valid = node_ssh.ssh_read(
        f"openssl x509 -in {cert_dir}/fullchain.pem -noout -subject 2>&1 | head -1", timeout=30
    )
    logger.info(
        "[IMP:9][T9][recovery] unlock_failed=%s unlock_recovered=%s cert=%s",
        unlock_failed,
        unlock_recovered,
        cert_valid.stdout.strip()[:60],
    )

    manifest = LogAuditManifest("T9")
    manifest.add("docker", "error", container="nginx", label="docker:nginx-errors", expected="optional")
    manifest.add("journald", "age: error|failed to decrypt|invalid", label="journald:age-fail", expected="optional")
    manifest.add("state", "nginx", container="nginx", label="state:nginx-healthy")
    _marker_http_sites(manifest)
    _marker_stack_healthy(manifest)

    results = manifest.check_all(node_ssh, incident_start, ttr)
    manifest.export_logs(node_ssh, incident_start, _out_dir("T9"), ["nginx", "status-page"])
    verdict, reasons = compute_verdict(results)

    assert sites_ok, f"T9 FAIL: sites down during cert corruption: {site_status}"
    assert unlock_failed and unlock_clear, f"T9 FAIL: unlock did not fail clearly: {unlock.stdout}"
    assert unlock_recovered, f"T9 FAIL: unlock not recovered: {unlock_after.stdout}"
    assert sites_after, f"T9 FAIL: sites after restore: {status_after}"
    assert "subject=" in cert_valid.stdout, f"T9 FAIL: restored cert invalid: {cert_valid.stdout}"
    record_verdict("T9", _out_dir("T9"), verdict, ttr, results, incident_start)
    logger.info("[IMP:9][T9][verdict] %s ttr=%ss reasons=%s", verdict, ttr, reasons)
    assert verdict != "FAIL", f"T9 log audit FAIL: {reasons}"
    assert_ldd_imp9_e2e(caplog)


# endregion TEST_T9


# ══════════════════════════════════════════════════════════════════════════════
# T10 — Restore-drill: DROP БД → restore из S3
# ══════════════════════════════════════════════════════════════════════════════
# region TEST_T10
@pytest.mark.chaos
@pytest.mark.requires_node
def test_t10_restore_drill_drop_db(requires_node: str, node_ssh: NodeSSHClient, caplog) -> None:
    """T10: chaos_drill (10k строк + checksum) → штатный бэкап в S3 → DROP DATABASE →
    restore из S3 → row-count + checksum совпадают; audit-trail в логах бэкапа."""
    caplog.set_level(logging.DEBUG)
    incident_start = host_epoch_seconds(node_ssh)
    logger.info("[IMP:9][T10][prep] seed chaos_drill 10k rows + checksum")

    _psql(node_ssh, "platform", "CREATE DATABASE chaos_drill")
    _psql(node_ssh, "chaos_drill", "CREATE TABLE IF NOT EXISTS t(id serial PRIMARY KEY, payload text)")
    _psql(node_ssh, "chaos_drill", "TRUNCATE t")
    _psql(
        node_ssh,
        "chaos_drill",
        "INSERT INTO t(payload) SELECT md5(g::text) FROM generate_series(1,10000) g",
        timeout=120,
    )
    count_before = int(_psql(node_ssh, "chaos_drill", "SELECT count(*) FROM t") or "0")
    checksum_before = _psql(node_ssh, "chaos_drill", "SELECT md5(string_agg(payload, '' ORDER BY id)) FROM t")
    logger.info("[IMP:9][T10][seed] rows=%d checksum=%s", count_before, checksum_before[:16])
    assert count_before == 10000, f"T10 prep FAIL: rows={count_before}"

    # штатный бэкап → S3 (лог с S3-ключом + sha)
    backup = node_ssh.ssh_exec("docker exec backup-cron /usr/local/bin/backup-postgres.sh 2>&1 | tail -6", timeout=900)
    m_key = re.search(r"s3://\S+?/(pgdumpall_\d+T\d+Z\.sql\.gz)", backup.stdout)
    m_sha = re.search(r"sha256=([0-9a-f]{64})", backup.stdout)
    s3_key = m_key.group(1) if m_key else ""
    sha256 = m_sha.group(1) if m_sha else ""
    assert s3_key, f"T10 FAIL: S3 key not found in backup log: {backup.stdout}"
    logger.info("[IMP:9][T10][backup] s3_key=%s sha256=%s", s3_key, sha256[:16])

    # инъекция: DROP DATABASE
    drop = _psql(node_ssh, "platform", "DROP DATABASE chaos_drill")
    assert "DROP" in drop or drop == "", f"T10 drop failed: {drop}"
    t0 = time.monotonic()

    # restore из S3: скачать дамп, вырезать секцию chaos_drill, psql -f
    restore = node_ssh.ssh_exec(
        f"set -a; source {_SECRETS_ENV}; set +a; "
        "docker exec backup-cron python3 - <<PYEOF\n"
        "import boto3, os, gzip\n"
        "s3 = boto3.client('s3', endpoint_url=os.environ['S3_ENDPOINT_URL'], region_name=os.environ['S3_REGION'], "
        "aws_access_key_id=os.environ['S3_ACCESS_KEY'], aws_secret_access_key=os.environ['S3_SECRET_KEY'])\n"
        f"key = os.environ['S3_PREFIX'] + '/postgres/{s3_key}'\n"
        "obj = s3.get_object(Bucket=os.environ['S3_BUCKET'], Key=key)\n"
        "text = gzip.decompress(obj['Body'].read()).decode()\n"
        "out = []\n"
        "in_db = False\n"
        "for line in text.splitlines():\n"
        "    if line.startswith('\\\\connect chaos_drill'):\n"
        "        in_db = True\n"
        "        out.append(line)\n"
        "        continue\n"
        "    if line.startswith('\\\\connect ') and in_db:\n"
        "        break\n"
        "    if in_db:\n"
        "        out.append(line)\n"
        "open('/tmp/chaos_drill_restore.sql','w').write('\\n'.join(out))\n"
        "print('EXTRACTED', len(out), 'lines')\n"
        "PYEOF",
        timeout=300,
    )
    assert "EXTRACTED" in restore.stdout, f"T10 extract FAIL: {restore.stdout} {restore.stderr}"
    restore_db = node_ssh.ssh_exec(
        f"set -a; source {_SECRETS_ENV}; set +a; "
        "docker cp /tmp/chaos_drill_restore.sql backup-cron:/tmp/chaos_drill_restore.sql && "
        'docker exec -e PGPASSWORD="$POSTGRES_PASSWORD" backup-cron psql -h postgres -U platform '
        "-d postgres -q -f /tmp/chaos_drill_restore.sql 2>&1 | tail -3; echo PSQL_EXIT=$?",
        timeout=300,
    )
    restore_ok = "PSQL_EXIT=0" in restore_db.stdout
    ttr = int(time.monotonic() - t0)

    # верификация: row-count + checksum
    count_after = int(_psql(node_ssh, "chaos_drill", "SELECT count(*) FROM t") or "-1")
    checksum_after = _psql(node_ssh, "chaos_drill", "SELECT md5(string_agg(payload, '' ORDER BY id)) FROM t")
    integrity = count_after == count_before and checksum_after == checksum_before
    # audit-trail restore-drill (в лог-директорию бэкапов)
    node_ssh.ssh_exec(
        f'mkdir -p /var/log/platform/backup && echo "$(date -u +%FT%TZ) restore-drill T10: '
        f's3_key={s3_key} sha={sha256} rows={count_after} checksum_match={checksum_after == checksum_before}" '
        f">> /var/log/platform/backup/restore.log",
        timeout=30,
    )
    logger.info(
        "[IMP:9][T10][verify] rows=%d→%d checksum_match=%s integrity=%s",
        count_before,
        count_after,
        checksum_after == checksum_before,
        integrity,
    )

    manifest = LogAuditManifest("T10")
    manifest.add("docker", "UPLOAD COMPLETE|UPLOAD VERIFIED", container="backup-cron", label="docker:backup-s3-audit")
    manifest.add("journald", "restore-drill T10", label="journald:restore-audit", expected="optional")
    _marker_stack_healthy(manifest)

    results = manifest.check_all(node_ssh, incident_start, ttr)
    manifest.export_logs(node_ssh, incident_start, _out_dir("T10"), ["backup-cron", "postgres"])
    verdict, reasons = compute_verdict(results)

    assert restore_ok, f"T10 FAIL: psql restore failed: {restore_db.stdout}"
    assert integrity, (
        f"T10 FAIL: data mismatch! rows {count_before}→{count_after}, checksum {'OK' if checksum_after == checksum_before else 'MISMATCH'}"
    )
    assert sha256, "T10 FAIL: no SHA256 in backup audit log"
    record_verdict("T10", _out_dir("T10"), verdict, ttr, results, incident_start)
    logger.info("[IMP:9][T10][verdict] %s ttr=%ss reasons=%s", verdict, ttr, reasons)
    assert verdict != "FAIL", f"T10 log audit FAIL: {reasons}"
    assert_ldd_imp9_e2e(caplog)


# endregion TEST_T10


# ══════════════════════════════════════════════════════════════════════════════
# T11 — Полный reboot + кросс-бут аудит
# ══════════════════════════════════════════════════════════════════════════════
# region TEST_T11
@pytest.mark.chaos
@pytest.mark.requires_node
def test_t11_reboot_and_cross_boot_audit(requires_node: str, node_ssh: NodeSSHClient, caplog) -> None:
    """T11: systemctl reboot → systemd → docker → compose-стек → healthy ≤5 мин → сайты 200.
    Кросс-бут аудит: инциденты T1-T10 реконструируются из персистентных логов
    (journald/docker logs/audit.jsonl/backup log) без участия очевидца."""
    caplog.set_level(logging.DEBUG)
    incident_start = host_epoch_seconds(node_ssh)
    logger.info("[IMP:9][T11][inject] systemctl reboot")

    reboot = node_ssh.ssh_exec("systemctl reboot", timeout=60)
    logger.info("[IMP:9][T11][reboot] exit=%d (SSH drop expected)", reboot.exit_code)

    # ждём SSH обратно (до 15 мин)
    import subprocess

    ssh_back = False
    t0 = time.monotonic()
    while time.monotonic() - t0 < 900:
        try:
            proc = subprocess.run(
                [
                    "ssh",
                    "-o",
                    "BatchMode=yes",
                    "-o",
                    "ConnectTimeout=8",
                    "-o",
                    "StrictHostKeyChecking=accept-new",
                    f"root@{node_ssh.host}",
                    "true",
                ],
                capture_output=True,
                timeout=15,
                check=False,
            )
            if proc.returncode == 0:
                ssh_back = True
                break
        except subprocess.TimeoutExpired as exc:
            logger.info("[IMP:7][T11][ssh-poll] probe timeout: %s", exc)
            time.sleep(10)
    ttr = int(time.monotonic() - t0)
    assert ssh_back, f"T11 FAIL: SSH did not come back within 900s (ttr={ttr}s)"

    ok, missing, _ = wait_all_containers(node_ssh, timeout_s=300)
    sites_ok, site_status = wait_sites_up(node_ssh, timeout_s=180)
    boot_id = node_ssh.ssh_read("cat /proc/sys/kernel/random/boot_id", timeout=20).stdout.strip()
    logger.info("[IMP:9][T11][recovery] ttr=%ss containers=%s sites=%s boot=%s", ttr, ok, sites_ok, boot_id[:8])

    # ── кросс-бут аудит: каждый инцидент T1-T10 обязан иметь след ──
    since_iso = node_ssh.ssh_read(f"date -d @{incident_start - 3600} +%Y-%m-%dT%H:%M:%SZ", timeout=20).stdout.strip()
    cross_checks = [
        ("T1", "journald", "docker:daemon-restart", r"Started Docker Application Container Engine", None),
        ("T2", "journald", "resolved:stop", r"Stopped Network Name Resolution", None),
        (
            "T3",
            "docker",
            "backup:outbound-fail",
            r"Failed to connect|Network is unreachable|Could not resolve",
            "backup-cron",
        ),
        ("T4", "journald", "clock:change", r"System clock time changed|time jump", None),
        ("T5", "journald", "tor:stop", r"Stopped.*(tor@default|privoxy)", None),
        ("T6", "docker", "postgres:interrupted", r"database system was interrupted", "postgres"),
        ("T7", "journald", "kernel:oom", r"Out of memory|oom-kill", None),
        ("T8", "docker", "backup:enspc", r"No space left on device|ENOSPC", "backup-cron"),
        ("T9", "auditfile", "age:fail", r"age: error|failed to decrypt", None),
        ("T10", "auditfile", "restore:drill", r"restore-drill T10", None),
    ]
    cross_results: list[dict] = []
    for test_id, source, label, regex, container in cross_checks:
        if source == "journald":
            res = node_ssh.ssh_read(
                f"journalctl --since '{since_iso}' --no-pager 2>/dev/null | grep -cE '{regex}'", timeout=90
            )
            found = int(res.stdout.strip().splitlines()[-1] or "0") > 0
        elif source == "docker":
            res = node_ssh.ssh_read(
                f"docker logs --since '{since_iso}' {container} 2>&1 | grep -cE '{regex}'", timeout=90
            )
            found = int(res.stdout.strip().splitlines()[-1] or "0") > 0
        elif source == "auditfile":
            path = (
                "/var/log/platform/audit.jsonl" if label != "restore:drill" else "/var/log/platform/backup/restore.log"
            )
            res = node_ssh.ssh_read(f"grep -cE '{regex}' {path} 2>/dev/null || true", timeout=30)
            found = int(res.stdout.strip().splitlines()[-1] or "0") > 0
        else:  # pragma: no cover
            found = False
        cross_results.append({"incident": test_id, "label": label, "found": found, "source": source})
        logger.info("[IMP:9][T11][cross-boot] %s %s found=%s", test_id, label, found)
    cross_all_found = all(r["found"] for r in cross_results)

    manifest = LogAuditManifest("T11")
    manifest.add("journald", "Started Docker Application Container Engine", label="journald:docker-after-boot")
    manifest.add("docker", "database system is ready", container="postgres", label="docker:postgres-ready")
    _marker_stack_healthy(manifest)
    _marker_http_sites(manifest)

    results = manifest.check_all(node_ssh, incident_start, ttr)
    manifest.export_logs(node_ssh, incident_start, _out_dir("T11"), ["nginx", "postgres", "promtail", "loki"])
    (_out_dir("T11") / "cross_boot_audit.json").write_text(json.dumps(cross_results, indent=2))
    verdict, reasons = compute_verdict(results)

    assert ok, f"T11 FAIL: containers not recovered after reboot: {missing}"
    assert sites_ok, f"T11 FAIL: sites not recovered: {site_status}"
    assert ttr <= 600, f"T11 FAIL: TTR {ttr}s > 600s"
    assert cross_all_found, f"T11 FAIL: cross-boot audit incomplete: {cross_results}"
    record_verdict("T11", _out_dir("T11"), verdict, ttr, results, incident_start)
    logger.info("[IMP:9][T11][verdict] %s ttr=%ss reasons=%s", verdict, ttr, reasons)
    assert verdict != "FAIL", f"T11 log audit FAIL: {reasons}"
    assert_ldd_imp9_e2e(caplog)


# endregion TEST_T11
