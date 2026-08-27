# GREP_SUMMARY: resilience-drills crash-injection degraded-dependency watchdog-heals oom-kernel-kill disk-pressure fallocate tor-channel fails-loud reboot zero-restart-loops outbound-partition docker-daemon-restart fast-tier night-tier devplan-013
# STRUCTURE: ▶ FAST (chaos and not night): F1 postgres SIGKILL+WAL → F2 redis kill → F3 litellm kill → F4/F5 degraded stop redis/litellm → F6 watchdog heals unhealthy (ручной вызов, пороги 1/0) → F7 OOM clickhouse (kernel kill) → F8 disk pressure (fallocate ≥92%) → F9 tor fails loud ‖ NIGHT (-m night): N1 reboot ΔRestartCount==0 → N2 outbound partition 45s auto-revert → N3 docker daemon restart (uptime continuity) → ⎋
# region MODULE_CONTRACT
## @purpose  DevPlan 013 (resilience-drills rework): 12 resilience drills вместо 12 долгих
##           chaos-тестов T1-T12 (~1770 LOC, часы рантайма → ≤25 мин суммарно). Каждый drill
##           доказывает 4 факта (AC2): (a) инъекция приземлилась (state-poll/probe ДО recovery),
##           (b) деградация соответствует дизайну модуля, (c) самовосстановление в бюджет TTR,
##           (d) нода чиста после теста (try/finally восстановление).
## @scope    tests/e2e — NOT в make check/gate (фильтр requires_node). Два тира:
##           fast `-m "chaos and not night"` (9 drills, ≤6 мин каждый) и night `-m night`
##           (3 drills: reboot/outbound-partition/docker-daemon-restart, отдельное окно).
## @invariants
##   - Поток drill'а (DevPlan 013 §4): precondition (healthy snapshot) → inject → proof-of-
##     injection (assert_injection_landed, НИКОГДА не пропускается) → degradation window →
##     recovery trigger → await(recovery-predicate, TTR-budget) → ∑ assert: proof ∧ degradation
##     ∧ ttr ∧ clean → capture_evidence → PASS
##   - Любой сброс состояния (health-cmd, iptables, файлы, БД) — в try/finally даже при assert-fail
##   - 0 параметризации (детерминизм); инъекции ТОЛЬКО через node_ssh (NodeSSHClient parity)
##   - Сайты/контейнеры резолвятся из node-configs/<NODE>/node.yaml + live-snapshot (не hardcode;
##     отсутствие → FAIL R4)
##   - Экзотика удалена (AC3): DNS-resolver stop, time-skew ±24h, TLS/secrets corruption,
##     кросс-бут аудит T1-T10, restore-drill (Debt Intake → отдельный план после фикса ранбука)
## @rationale Q: два тира? A: reboot/partition/daemon-restart — реальные сценарии с ценой
##           времени; тир-граница по маркеру сохраняет один файл/один запуск pytest.
##           Q: watchdog вручную, не cron? A: тестируемое свойство — «watchdog ЛЕЧИТ unhealthy»,
##           а не расписание cron (законтрактовано CRON_WATCHDOG_LINE + CI-gate); ручной вызов
##           той же команды с env-порогами 1/0 мин — та же кодовая ветка, −20 минут.
## @changes 2026-08-26 | DevPlan 013 W2 TASK-3 — rewrite из test_chaos_resilience.py (T1-T12 era)
## @modulemap
##   F1-F9 [fast] — crash/degraded/watchdog/OOM/disk/tor drills (marker chaos)
##   N1-N3 [night] — reboot/partition/daemon-restart (markers chaos+night)
##   _restart_count_map [W:1] — W3-2 порт: RestartCount снапшот для Δ==0 верификации
# endregion MODULE_CONTRACT

from __future__ import annotations

import json
import logging
import re
import subprocess
import time
from pathlib import Path

import pytest

from tests._conftest.node import NodeSSHClient, assert_ldd_imp9_e2e
from tests.e2e.chaos_audit import (
    assert_injection_landed,
    await_condition,
    capture_evidence,
    container_pid,
    load_node_yaml,
    probe_sites_local,
    resolve_site_urls,
    sites_ok,
    snapshot_running_containers,
    wait_containers_healthy,
    wait_sites_up,
)

logger = logging.getLogger(__name__)

_FILES_DIR = Path("/tmp") / f"chaos-{time.strftime('%Y%m%d-%H%M%S')}"
_SECRETS_ENV = "/var/lib/platform/run/secrets.env"
_WATCHDOG_STATE_FILE = "/var/lib/platform/run/watchdog-state.json"
_PLATFORM_CORE = "/opt/platform/core"


def _out_dir(test_id: str) -> Path:
    d = _FILES_DIR / test_id
    d.mkdir(parents=True, exist_ok=True)
    return d


def _site_urls(node: str) -> list[str]:
    """SITE_URLS из node-configs/<NODE>/node.yaml (R4-FAIL при отсутствии — не hardcode)."""
    return resolve_site_urls(load_node_yaml(node))


def _psql(ssh: NodeSSHClient, db: str, sql: str, timeout: int = 60) -> str:
    """Выполнить SQL на хосте через docker exec postgres (platform superuser)."""
    res = ssh.ssh_exec(
        f'docker exec -e PGPASSWORD="$(grep POSTGRES_PASSWORD {_SECRETS_ENV} | cut -d= -f2-)" '
        f'postgres psql -U platform -d {db} -tAc "{sql}"',
        timeout=timeout,
    )
    return res.stdout.strip()


def _restart_count_map(ssh: NodeSSHClient) -> dict[str, int]:
    """Снапшот RestartCount всех контейнеров (W3-2 порт): дельта до/после boot обязана быть 0.

    ## @purpose — RestartLoop-верификация reboot'а: RestartCount персистентен
    ##            (/var/lib/docker/containers), сравнивается ДЕЛЬТА до/после.
    ## @io — ⇥ ssh → ⎋ dict[name, count]; нераспарсимая строка → WARN, пропуск.
    """
    res = ssh.ssh_read(
        "docker ps -aq | while read cid; do "
        "name=$(docker inspect --format '{{.Name}}' \"$cid\" | sed 's|^/||'); "
        "rc=$(docker inspect --format '{{.RestartCount}}' \"$cid\"); "
        'echo "$name $rc"; done',
        timeout=90,
    )
    counts: dict[str, int] = {}
    for line in res.stdout.strip().splitlines():
        parts = line.split()
        if len(parts) >= 2 and parts[0]:
            try:
                counts[parts[0]] = int(parts[1])
            except ValueError:
                logger.warning("[IMP:7][N1][restart-count] unparsable line: %r", line)
    return counts


# ════════════════════════════════════════ FAST TIER ══════════════════════════════════════
# region TEST_F1_postgres_crash
@pytest.mark.chaos
@pytest.mark.requires_node
def test_crash_postgres_data_integrity(requires_node: str, node_ssh: NodeSSHClient, caplog) -> None:
    """F1: SIGKILL host-pid postgres под INSERT-нагрузкой → unless-stopped → WAL recovery →
    rows == committed_batches×50 (0 потерянных committed строк); TTR ≤120s.

    # 🧪 TRAP[TEST] · Scenario: crash-consistency postgres под нагрузкой · Last fail: VR 142 §6 (T6 RED — kill без proof)
    # · Regression: restart policy unless-stopped + WAL durability: батч = INSERT 50 строк +
    # ·   UPDATE counter, атомарен; прерванные SIGKILL батчи теряются ЦЕЛИКОМ (корректно),
    # ·   committed == rows. Инъекция kill -9 host-pid (docker exec kill -9 1 НЕ доставляется
    # ·   namespace-init; docker kill НЕ триггерит policy).
    # · Remove if: postgres уходит на другой runtime без restart-policy/WAL семантики
    """
    caplog.set_level(logging.DEBUG)
    ssh = node_ssh
    t_start = time.monotonic()
    logger.info("[IMP:9][F1][prep] chaos_drill DB + counter-table + loader (40 батчей × 50 строк)")

    # cleanup прошлых прогонов (bracket-regex не матчит собственную cmdline)
    ssh.ssh_exec("pkill -f '[c]haos-f1-loader' 2>/dev/null; true", timeout=30)
    ssh.ssh_exec("rm -f /tmp/chaos-f1-load.log", timeout=30)
    _psql(ssh, "platform", "DROP DATABASE IF EXISTS chaos_drill WITH (FORCE)")
    _psql(ssh, "platform", "CREATE DATABASE chaos_drill")
    _psql(ssh, "chaos_drill", "CREATE TABLE t(id serial PRIMARY KEY, payload text)")
    _psql(ssh, "chaos_drill", "CREATE TABLE counter(n int); INSERT INTO counter VALUES (0)")

    load_cmd = (
        f"set -a; source {_SECRETS_ENV}; set +a; "
        "for i in $(seq 1 40); do "
        'docker exec -e PGPASSWORD="$POSTGRES_PASSWORD" postgres psql -U platform -d chaos_drill -q -c '
        '"INSERT INTO t(payload) SELECT md5(g::text) FROM generate_series(1,50) g; '
        'UPDATE counter SET n=$((i*50));" >/dev/null && echo "committed_batch" >> /tmp/chaos-f1-load.log; '
        "done"
    )
    load = ssh.ssh_exec(f"nohup bash -c '{load_cmd}' >/tmp/chaos-f1-loader.log 2>&1 &", timeout=30)
    assert load.exit_code == 0, f"loader spawn failed: {load.stderr}"

    def _committed_rows() -> int:
        raw = _psql(ssh, "chaos_drill", "SELECT COALESCE(MAX(n),0) FROM counter", timeout=30)
        return int(raw) if raw.isdigit() else 0

    landed, _ = await_condition(lambda: _committed_rows() >= 100, timeout_s=60, interval_s=2.0)
    assert landed, f"F1 FAIL: load did not reach 100 committed rows (got {_committed_rows()})"
    logger.info("[IMP:9][F1][inject] SIGKILL host-pid postgres (committed=%d rows)", _committed_rows())

    rc_before_res = ssh.ssh_read("docker inspect --format '{{.RestartCount}}' postgres", timeout=30)
    rc_before = int(rc_before_res.stdout.strip() or "0")
    pid = container_pid(ssh, "postgres")
    kill = ssh.ssh_exec(f"kill -9 {pid}", timeout=30)
    assert kill.exit_code == 0, f"kill -9 {pid} failed: {kill.stderr}"

    proof = assert_injection_landed(
        lambda: (
            state
            if (
                state := ssh.ssh_read("docker inspect --format '{{.State.Status}}' postgres", timeout=20).stdout.strip()
            )
            == "exited"
            else None
        ),
        timeout_s=30,
        description="postgres container state == exited",
        interval_s=1.0,
    )

    ok, missing = wait_containers_healthy(ssh, timeout_s=120, containers=["postgres", "pgbouncer"])
    ttr = int(time.monotonic() - t_start)

    # integrity: ждём завершения loader'а (uncommitted батчи корректно потеряны целиком)
    await_condition(
        lambda: ssh.ssh_read("pgrep -fc '[c]haos-f1-loader' || true", timeout=20).stdout.strip() in {"", "0"},
        timeout_s=120,
        interval_s=3.0,
    )
    rows_raw = _psql(ssh, "chaos_drill", "SELECT count(*) FROM t", timeout=60)
    batches_raw = ssh.ssh_read("wc -l < /tmp/chaos-f1-load.log 2>/dev/null || echo 0", timeout=20).stdout.strip()
    try:
        rows, batches = int(rows_raw), int(batches_raw)
    except ValueError:
        rows, batches = -1, -1
    integrity = batches > 0 and rows == batches * 50
    logger.info("[IMP:9][F1][verify] rows=%d committed_batches=%d integrity=%s", rows, batches, integrity)

    rc_after = int(
        ssh.ssh_read("docker inspect --format '{{.RestartCount}}' postgres", timeout=30).stdout.strip() or "0"
    )
    verdict_extra = {"rows": rows, "committed_batches": batches, "restart_count": f"{rc_before}->{rc_after}"}
    try:
        assert ok, f"F1 FAIL: postgres not recovered within 120s: {missing}"
        assert rc_after > rc_before, f"F1 FAIL: RestartCount unchanged ({rc_before}→{rc_after}) — policy не сработал"
        assert integrity, f"F1 FAIL: DATA LOSS! rows={rows} != committed_batches({batches})×50"
        assert ttr <= 120, f"F1 FAIL: TTR {ttr}s > 120s budget"
        capture_evidence(
            ssh,
            _out_dir("F1"),
            "postgres",
            test_id="F1",
            verdict="PASS",
            ttr_s=ttr,
            injection_proof=proof,
            extra=verdict_extra,
        )
    except AssertionError:
        capture_evidence(
            ssh,
            _out_dir("F1"),
            "postgres",
            test_id="F1",
            verdict="FAIL",
            ttr_s=ttr,
            injection_proof=proof,
            extra=verdict_extra,
        )
        raise
    finally:
        ssh.ssh_exec("pkill -f '[c]haos-f1-loader' 2>/dev/null; true", timeout=30)
        _psql(ssh, "platform", "DROP DATABASE IF EXISTS chaos_drill WITH (FORCE)")
        ssh.ssh_exec("rm -f /tmp/chaos-f1-load.log /tmp/chaos-f1-loader.log", timeout=30)
    assert_ldd_imp9_e2e(caplog)


# endregion TEST_F1_postgres_crash


# region TEST_F2_F3_redis_litellm_kill
@pytest.mark.chaos
@pytest.mark.requires_node
def test_crash_redis_restart_policy(requires_node: str, node_ssh: NodeSSHClient, caplog) -> None:
    """F2: kill -9 host-pid redis → exited-proof → healthy ≤90s; сайты живы в окне смерти.

    # 🧪 TRAP[TEST] · Scenario: cache-crash self-heal (restart policy) · Last fail: N/A
    # · Regression: redis = cache-only модуль; смерть кэша НЕ валит сайты (degradation канон);
    # ·   restart: always поднимает контейнер ≤90s.
    # · Remove if: redis перестаёт быть cache-only или policy меняется на внешний supervisor
    """
    caplog.set_level(logging.DEBUG)
    facts = _crash_and_heal(requires_node, node_ssh, container="redis", heal_budget_s=90, test_id="F2")
    try:
        assert facts["healthy"], f"F2 FAIL: redis not healthy within 90s: {facts['missing']}"
        assert facts["delta"] > 0, f"F2 FAIL: RestartCount unchanged ({facts['delta']}), policy не сработал"
        assert sites_ok(facts["codes_during"]), f"F2 FAIL: sites down during crash window: {facts['codes_during']}"
        assert sites_ok(facts["codes_after"]), f"F2 FAIL: sites down after recovery: {facts['codes_after']}"
        capture_evidence(
            node_ssh,
            _out_dir("F2"),
            "redis",
            test_id="F2",
            verdict="PASS",
            ttr_s=facts["ttr_s"],
            injection_proof=facts["proof"],
        )
    except AssertionError:
        capture_evidence(
            node_ssh,
            _out_dir("F2"),
            "redis",
            test_id="F2",
            verdict="FAIL",
            ttr_s=facts["ttr_s"],
            injection_proof=facts["proof"],
        )
        raise
    assert_ldd_imp9_e2e(caplog)


@pytest.mark.chaos
@pytest.mark.requires_node
def test_crash_litellm_restart_policy(requires_node: str, node_ssh: NodeSSHClient, caplog) -> None:
    """F3: kill -9 host-pid litellm → exited-proof → healthy ≤120s; сайты живы в окне смерти.

    # 🧪 TRAP[TEST] · Scenario: LLM-proxy crash self-heal · Last fail: N/A
    # · Regression: litellm недоступность НЕ валит сайты (проекты не зависят синхронно от LLM);
    # ·   restart policy поднимает контейнер ≤120s.
    # · Remove if: litellm станет синхронной зависимостью ingress-пути сайтов
    """
    caplog.set_level(logging.DEBUG)
    facts = _crash_and_heal(requires_node, node_ssh, container="litellm", heal_budget_s=120, test_id="F3")
    try:
        assert facts["healthy"], f"F3 FAIL: litellm not healthy within 120s: {facts['missing']}"
        assert facts["delta"] > 0, f"F3 FAIL: RestartCount unchanged ({facts['delta']}), policy не сработал"
        assert sites_ok(facts["codes_during"]), f"F3 FAIL: sites down during crash window: {facts['codes_during']}"
        assert sites_ok(facts["codes_after"]), f"F3 FAIL: sites down after recovery: {facts['codes_after']}"
        capture_evidence(
            node_ssh,
            _out_dir("F3"),
            "litellm",
            test_id="F3",
            verdict="PASS",
            ttr_s=facts["ttr_s"],
            injection_proof=facts["proof"],
        )
    except AssertionError:
        capture_evidence(
            node_ssh,
            _out_dir("F3"),
            "litellm",
            test_id="F3",
            verdict="FAIL",
            ttr_s=facts["ttr_s"],
            injection_proof=facts["proof"],
        )
        raise
    assert_ldd_imp9_e2e(caplog)


def _crash_and_heal(node: str, ssh: NodeSSHClient, *, container: str, heal_budget_s: int, test_id: str) -> dict:
    """Механика kill→proof→window→healthy БЕЗ вердикт-ассертов (R1): возвращает факты,
    вердикт (AC2: proof ∧ degradation ∧ ttr ∧ clean) ассертит тело теста."""
    urls = _site_urls(node)
    t0 = time.monotonic()
    rc_before = int(
        ssh.ssh_read(f"docker inspect --format '{{{{.RestartCount}}}}' {container}", timeout=30).stdout.strip() or "0"
    )
    pid = container_pid(ssh, container)
    logger.info("[IMP:9][%s][inject] kill -9 host-pid=%s %s", test_id, pid, container)
    kill = ssh.ssh_exec(f"kill -9 {pid}", timeout=30)
    assert kill.exit_code == 0, f"kill -9 {pid} failed: {kill.stderr}"

    # F-20 (017): на healthy самохилящейся ноде docker перезапускает контейнер быстрее
    # первого опроса — статус-окно exited/restarting может не попасть в сэмпл.
    # Доказательство инъекции = статус ИЛИ прирост .RestartCount (свойство убитого процесса,
    # см TRAP[BUG] VR142 §6). Инвариант «proof до recovery-wait» сохранён.
    def _injection_evidence() -> str | None:
        st = ssh.ssh_read(f"docker inspect --format '{{{{.State.Status}}}}' {container}", timeout=20).stdout.strip()
        if st in {"exited", "restarting"}:
            return f"state={st}"
        rc_now = int(
            ssh.ssh_read(f"docker inspect --format '{{{{.RestartCount}}}}' {container}", timeout=20).stdout.strip()
            or "0"
        )
        return f"restart_count_delta={rc_now - rc_before}" if rc_now > rc_before else None

    proof = assert_injection_landed(
        _injection_evidence,
        timeout_s=30,
        description=f"{container} state or RestartCount delta",
        interval_s=1.0,
    )
    codes_during = probe_sites_local(ssh, urls)
    logger.info("[IMP:9][%s][window] sites alive during death-window: %s", test_id, sites_ok(codes_during))

    ok, missing = wait_containers_healthy(ssh, timeout_s=heal_budget_s, containers=[container])
    rc_after = int(
        ssh.ssh_read(f"docker inspect --format '{{{{.RestartCount}}}}' {container}", timeout=30).stdout.strip() or "0"
    )
    codes_after = probe_sites_local(ssh, urls)
    return {
        "healthy": ok,
        "missing": missing,
        "delta": rc_after - rc_before,
        "codes_during": codes_during,
        "codes_after": codes_after,
        "ttr_s": int(time.monotonic() - t0),
        "proof": proof,
    }


# endregion TEST_F2_F3_redis_litellm_kill


# region TEST_F4_F5_degraded_stop
@pytest.mark.chaos
@pytest.mark.requires_node
def test_degraded_redis_sites_alive(requires_node: str, node_ssh: NodeSSHClient, caplog) -> None:
    """F4: docker stop redis на 45s → сайты 200 ВСЁ окно (graceful degradation) → start →
    healthy ≤90s. Stop (≠ kill) — управляемая деградация без рестарт-цикла.

    # 🧪 TRAP[TEST] · Scenario: graceful degradation при остановленной зависимости · Last fail: N/A
    # · Regression: проекты не падают синхронно от отказа кэша — канон модуля cache;
    # ·   docker start восстанавливает без recreate.
    # · Remove if: какой-то проект станет жёстко зависимым от redis на request-path
    """
    caplog.set_level(logging.DEBUG)
    facts = _stop_window_and_heal(
        requires_node, node_ssh, container="redis", window_s=45, heal_budget_s=90, test_id="F4"
    )
    try:
        assert facts["window_ok"], "F4 FAIL: sites down during stopped window"
        assert facts["healthy"], f"F4 FAIL: redis not healthy after start: {facts['missing']}"
        assert sites_ok(facts["codes_after"]), f"F4 FAIL: sites after recovery: {facts['codes_after']}"
        capture_evidence(
            node_ssh,
            _out_dir("F4"),
            "redis",
            test_id="F4",
            verdict="PASS",
            ttr_s=facts["ttr_s"],
            injection_proof=facts["proof"],
        )
    except AssertionError:
        capture_evidence(
            node_ssh,
            _out_dir("F4"),
            "redis",
            test_id="F4",
            verdict="FAIL",
            ttr_s=facts["ttr_s"],
            injection_proof=facts["proof"],
        )
        raise
    assert_ldd_imp9_e2e(caplog)


@pytest.mark.chaos
@pytest.mark.requires_node
def test_degraded_litellm_sites_alive(requires_node: str, node_ssh: NodeSSHClient, caplog) -> None:
    """F5: docker stop litellm на 30s → сайты 200 всё окно → start → healthy ≤120s.

    # 🧪 TRAP[TEST] · Scenario: graceful degradation LLM-proxy · Last fail: N/A
    # · Regression: ingress-путь сайтов не зависит синхронно от litellm; start восстанавливает.
    # · Remove if: litellm войдёт в синхронный request-path сайтов
    """
    caplog.set_level(logging.DEBUG)
    facts = _stop_window_and_heal(
        requires_node, node_ssh, container="litellm", window_s=30, heal_budget_s=120, test_id="F5"
    )
    try:
        assert facts["window_ok"], "F5 FAIL: sites down during stopped window"
        assert facts["healthy"], f"F5 FAIL: litellm not healthy after start: {facts['missing']}"
        assert sites_ok(facts["codes_after"]), f"F5 FAIL: sites after recovery: {facts['codes_after']}"
        capture_evidence(
            node_ssh,
            _out_dir("F5"),
            "litellm",
            test_id="F5",
            verdict="PASS",
            ttr_s=facts["ttr_s"],
            injection_proof=facts["proof"],
        )
    except AssertionError:
        capture_evidence(
            node_ssh,
            _out_dir("F5"),
            "litellm",
            test_id="F5",
            verdict="FAIL",
            ttr_s=facts["ttr_s"],
            injection_proof=facts["proof"],
        )
        raise
    assert_ldd_imp9_e2e(caplog)


def _stop_window_and_heal(
    node: str, ssh: NodeSSHClient, *, container: str, window_s: int, heal_budget_s: int, test_id: str
) -> dict:
    """Механика stop→window(sites polls)→start→healthy БЕЗ вердикт-ассертов (R1):
    docker start — в finally-семантике; возвращает факты для вердикта теста."""
    urls = _site_urls(node)
    t0 = time.monotonic()
    logger.info("[IMP:9][%s][inject] docker stop %s (%ds window)", test_id, container, window_s)
    stop = ssh.ssh_exec(f"docker stop {container}", timeout=60)
    assert stop.exit_code == 0, f"docker stop {container} failed: {stop.stderr}"
    try:
        proof = assert_injection_landed(
            lambda: (
                st
                if (
                    st := ssh.ssh_read(
                        f"docker inspect --format '{{{{.State.Status}}}}' {container}", timeout=20
                    ).stdout.strip()
                )
                == "exited"
                else None
            ),
            timeout_s=20,
            description=f"{container} stopped (exited)",
            interval_s=1.0,
        )
        window_codes: list[dict[str, str]] = []
        deadline = time.monotonic() + window_s
        while time.monotonic() < deadline:
            codes = probe_sites_local(ssh, urls)
            window_codes.append(codes)
            logger.info("[IMP:9][%s][window] sites=%s codes=%s", test_id, sites_ok(codes), codes)
            time.sleep(min(10.0, max(1.0, deadline - time.monotonic())))
    finally:
        start = ssh.ssh_exec(f"docker start {container}", timeout=60)
        assert start.exit_code == 0, f"docker start {container} failed: {start.stderr}"

    ok, missing = wait_containers_healthy(ssh, timeout_s=heal_budget_s, containers=[container])
    return {
        "window_ok": all(sites_ok(c) for c in window_codes),
        "healthy": ok,
        "missing": missing,
        "codes_after": probe_sites_local(ssh, urls),
        "ttr_s": int(time.monotonic() - t0),
        "proof": proof,
    }


# endregion TEST_F4_F5_degraded_stop


# region TEST_F6_watchdog_heals
def _watchdog_invoke_cmd() -> str:
    """Ручной вызов той же команды, что в /etc/cron.d/platform-watchdog (flock+timeout+путь),
    с env-порогами 1/0 мин (та же кодовая ветка decide_actions, −20 минут ожидания cron)."""
    return (
        "env WATCHDOG_UNHEALTHY_MIN=1 WATCHDOG_COOLDOWN_MIN=0 "
        "/usr/bin/flock -n /run/lock/platform-watchdog.lock "
        f"/usr/bin/timeout 50 python3 {_PLATFORM_CORE}/internal/healthcheck/watchdog.py"
    )


@pytest.mark.chaos
@pytest.mark.requires_node
def test_watchdog_heals_unhealthy(requires_node: str, node_ssh: NodeSSHClient, caplog) -> None:
    """F6: сломать healthcheck redis (CMD-SHELL false + interval 5s) → unhealthy → ручной запуск
    watchdog.py (пороги WATCHDOG_UNHEALTHY_MIN=1/COOLDOWN=0) → RestartCount+1 + запись в
    state-file → вернуть канонический healthcheck → healthy ≤60s.

    # 🧪 TRAP[TEST] · Scenario: watchdog лечит unhealthy-but-alive (выше restart policy) · Last fail: VR 142 §6 (T12 через реальный cron ≥15 мин — удалён церемониал)
    # · Regression: watchdog (DevPlan 132 W1) рестартует контейнеры, пережившие restart policy
    # ·   в unhealthy; stamp-after-success (REF-0014); state персистентен (142 W2).
    # ·   Ручной вызов = та же run_watchdog()-ветка, что cron (расписание закрыто CI-gate
    # ·   test_gate_watchdog_clean_env + CRON_WATCHDOG_LINE).
    # · Remove if: watchdog заменён другим механизмом auto-heal
    """
    caplog.set_level(logging.DEBUG)
    ssh = node_ssh
    container = "redis"
    urls = _site_urls(requires_node)
    t0 = time.monotonic()

    # ── prep: жив + healthcheck есть; сохранить канонический Healthcheck; сбросить бухгалтерию ──
    pre = ssh.ssh_read(
        f"docker inspect --format '{{{{.State.Status}}}}/{{{{.State.Health.Status}}}}/{{{{.RestartCount}}}}' {container}",
        timeout=30,
    )
    pre_parts = pre.stdout.strip().split("/")
    assert pre_parts[0] == "running", f"F6 FAIL: {container} not running: {pre.stdout}"
    hc_json = ssh.ssh_read(
        f"docker inspect --format '{{{{json .Config.Healthcheck}}}}' {container}", timeout=30
    ).stdout.strip()
    assert '"Test"' in hc_json, f"F6 FAIL: no healthcheck on {container}: {hc_json!r}"
    clean = ssh.ssh_exec(
        "python3 - <<'PYEOF'\n"
        "import json, os\n"
        f"p = {_WATCHDOG_STATE_FILE!r}\n"
        "state = json.load(open(p)) if os.path.exists(p) else {}\n"
        'for section in ("unhealthy_since", "last_restart"):\n'
        "    state.setdefault(section, {}).pop('redis', None)\n"
        "json.dump(state, open(p, 'w'), indent=2)\n"
        "print('STATE_CLEANED')\n"
        "PYEOF",
        timeout=30,
    )
    assert "STATE_CLEANED" in clean.stdout, f"F6 prep FAIL: watchdog state clean: {clean.stderr}"
    logger.info("[IMP:9][F6][prep] canonical healthcheck saved, watchdog state cleaned for redis")

    restored = False

    def _restore_healthcheck() -> None:
        nonlocal restored
        if restored:
            return
        hc = json.loads(hc_json)
        flags = [f"--health-cmd '{json.dumps(hc['Test'])}'"]
        if hc.get("Interval"):
            flags.append(f"--health-interval {int(hc['Interval']) // 1_000_000_000}s")
        if hc.get("Timeout"):
            flags.append(f"--health-timeout {int(hc['Timeout']) // 1_000_000_000}s")
        if hc.get("Retries"):
            flags.append(f"--health-retries {hc['Retries']}")
        res = ssh.ssh_exec(f"docker update {' '.join(flags)} {container}", timeout=60)
        restored = True
        logger.info("[IMP:8][F6][restore] canonical healthcheck back (rc=%d)", res.exit_code)

    try:
        # ── inject: сломанный health-cmd + частый interval → быстрый unhealthy ──
        inject = ssh.ssh_exec(
            f"docker update --health-cmd 'CMD-SHELL false' --health-interval 5s {container} && echo INJECT_OK",
            timeout=60,
        )
        assert "INJECT_OK" in inject.stdout, f"F6 inject FAIL: {inject.stderr}"
        proof = assert_injection_landed(
            lambda: (
                h
                if (
                    h := ssh.ssh_read(
                        f"docker inspect --format '{{{{.State.Health.Status}}}}' {container}", timeout=20
                    ).stdout.strip()
                )
                == "unhealthy"
                else None
            ),
            timeout_s=120,
            description="redis health == unhealthy (broken health-cmd)",
            interval_s=5.0,
        )
        codes_during = probe_sites_local(ssh, urls)
        logger.info("[IMP:9][F6][window] unhealthy-alive: sites alive=%s (cache-only канон)", sites_ok(codes_during))

        # ── recovery trigger: ручные проходы watchdog — та же команда, что в cron.d
        #    (flock+timeout+путь; python3-префикс вместо shebang-exec — устойчив к exec-bit).
        #    pass1 записывает unhealthy_since; при unhealthy ≥1 мин следующий проход рестартует.
        #    Детекция — RestartCount (неопровержимое доказательство docker restart). ──
        def _watchdog_restarted() -> str | None:
            rc_raw = ssh.ssh_read(
                f"docker inspect --format '{{{{.RestartCount}}}}' {container}", timeout=20
            ).stdout.strip()
            if int(rc_raw or "0") > int(pre_parts[2] or "0"):
                return f"RestartCount {pre_parts[2]}→{rc_raw}"
            ssh.ssh_exec(_watchdog_invoke_cmd(), timeout=60)
            return None

        restarted_proof = assert_injection_landed(
            _watchdog_restarted, timeout_s=300, description="watchdog restarts redis", interval_s=15.0
        )
        _restore_healthcheck()
        ttr_to_restart = int(time.monotonic() - t0)
        logger.info(
            "[IMP:9][F6][recovery] watchdog restarted (%s), ttr_to_restart=%ss", restarted_proof, ttr_to_restart
        )

        healthy, _ = await_condition(
            lambda: (
                ssh.ssh_read(
                    f"docker inspect --format '{{{{.State.Health.Status}}}}' {container}", timeout=20
                ).stdout.strip()
                == "healthy"
            ),
            timeout_s=60,
            interval_s=5.0,
        )
        state_has_redis = (
            int(
                (
                    ssh.ssh_read(
                        f"grep -c '\"redis\"' {_WATCHDOG_STATE_FILE} 2>/dev/null || echo 0", timeout=20
                    ).stdout.strip()
                    or "0"
                ).splitlines()[-1]
            )
            > 0
        )
        ttr = int(time.monotonic() - t0)
        codes_after = probe_sites_local(ssh, urls)
        try:
            assert restarted_proof, "F6 FAIL: watchdog did not restart redis within 300s"
            assert state_has_redis, "F6 FAIL: watchdog state-file missing redis entry"
            assert healthy, "F6 FAIL: redis not healthy ≤60s after canonical healthcheck restore"
            assert sites_ok(codes_during) and sites_ok(codes_after), (
                f"F6 FAIL: sites during={codes_during} after={codes_after}"
            )
            capture_evidence(
                ssh,
                _out_dir("F6"),
                container,
                test_id="F6",
                verdict="PASS",
                ttr_s=ttr,
                injection_proof=proof,
                extra={"ttr_to_restart_s": ttr_to_restart},
            )
        except AssertionError:
            capture_evidence(
                ssh, _out_dir("F6"), container, test_id="F6", verdict="FAIL", ttr_s=ttr, injection_proof=proof
            )
            raise
    finally:
        _restore_healthcheck()
    assert_ldd_imp9_e2e(caplog)


# endregion TEST_F6_watchdog_heals


# region TEST_F7_oom_clickhouse
@pytest.mark.chaos
@pytest.mark.requires_node
def test_oom_clickhouse_kernel_kill(requires_node: str, node_ssh: NodeSSHClient, caplog) -> None:
    """F7: memory-bomb внутри clickhouse cgroup (лимит 1GiB) → kernel OOM-kill жертвы по
    cgroup-id → restart policy → up ≤120s.

    # 🧪 TRAP[TEST] · Scenario: kernel-initiated kill (OOM) self-heal · Last fail: VR 142 §6 (T7 RED: victim искали по comm, не по cgroup-id)
    # · Regression: cgroup OOM убивает аллокатор (memcg-жертва); ядро называет жертву в
    # ·   journalctl -k по cgroup scope (docker-<id>.scope|docker/<id>) — comm=bash это
    # ·   процесс-жертва, не сервис; restart policy поднимает контейнер ≤120s.
    # · Remove if: memory-лимиты clickhouse сняты (OOM станет невозможным)
    """
    caplog.set_level(logging.DEBUG)
    ssh = node_ssh
    t0 = time.monotonic()
    ch_id = ssh.ssh_read("docker inspect --format '{{.Id}}' clickhouse", timeout=30).stdout.strip()
    ch_short = ch_id[:12]
    logger.info("[IMP:9][F7][inject] memory-bomb in clickhouse cgroup (id=%s…)", ch_short)

    allocator = (
        "docker exec clickhouse bash -c "
        '\'a=""; for i in $(seq 1 400); do a="$a$(head -c 8000000 /dev/zero | tr "\\0" "x")"; '
        "done; echo ALLOC_DONE'"
    )
    ssh.ssh_exec(allocator, timeout=180)

    kernel_oom_pattern = rf"docker-{re.escape(ch_short)}\.scope|docker/{re.escape(ch_id)}|clickhouse"

    def _oom_victim_named() -> str | None:
        res = ssh.ssh_read(
            "journalctl -k --no-pager 2>/dev/null | grep -iE 'out of memory|oom-kill|killed process' "
            f"| grep -iE '{kernel_oom_pattern}' | tail -1",
            timeout=60,
        )
        return res.stdout.strip() or None

    proof = assert_injection_landed(
        _oom_victim_named, timeout_s=90, description="kernel OOM report names cgroup victim"
    )
    ok, missing = wait_containers_healthy(ssh, timeout_s=120, containers=["clickhouse"])
    ttr = int(time.monotonic() - t0)
    try:
        assert ok, f"F7 FAIL: clickhouse not recovered within 120s: {missing}"
        assert ttr <= 120, f"F7 FAIL: TTR {ttr}s > 120s"
        capture_evidence(
            ssh, _out_dir("F7"), "clickhouse", test_id="F7", verdict="PASS", ttr_s=ttr, injection_proof=proof[:160]
        )
    except AssertionError:
        capture_evidence(
            ssh, _out_dir("F7"), "clickhouse", test_id="F7", verdict="FAIL", ttr_s=ttr, injection_proof=proof[:160]
        )
        raise
    assert_ldd_imp9_e2e(caplog)


# endregion TEST_F7_oom_clickhouse


# region TEST_F8_disk_pressure
_PROM_RATIO_CMD = (
    'curl -s -m 10 "http://127.0.0.1:9090/api/v1/query" --data-urlencode '
    "\"query=node_filesystem_avail_bytes{mountpoint='/'} / node_filesystem_size_bytes{mountpoint='/'}\""
)


def _prom_ratio(ssh: NodeSSHClient) -> float | None:
    """Prometheus ratio avail/size для mountpoint=/ (None при ошибке парсинга — retry снаружи)."""
    res = ssh.ssh_read(_PROM_RATIO_CMD, timeout=30)
    try:
        data = json.loads(res.stdout)
        vals = [float(x.get("value", ["", ""])[1]) for x in (data.get("data") or {}).get("result") or []]
        return min(vals) if vals else None
    except (json.JSONDecodeError, ValueError, TypeError):
        return None


@pytest.mark.chaos
@pytest.mark.requires_node
def test_disk_pressure_alert_and_recovery(requires_node: str, node_ssh: NodeSSHClient, caplog) -> None:
    """F8: fallocate /tmp до ≥92% (cap 94%, секунды вместо dd-минут) → Prometheus ratio<0.2 →
    rm → ratio>0.5; сайты живы всё окно. Alert-rule-state НЕ проверяется (Debt D-N: expr без
    mountpoint-фильтра — вне скоупа metrics-модуля).

    # 🧪 TRAP[TEST] · Scenario: disk-pressure data-path (monitoring видит критичный ratio) · Last fail: VR 142 §6 (T8 RED: spool-fill хрупкий канал; dd-минуты удалены)
    # · Regression: monitoring data-path — node_filesystem_* метрики отражают заполнение;
    # ·   fallocate резервирует блоки мгновенно (space reservation) — инъекция «места нет»
    # ·   идентична ENOSPC-поведению ФС без dd-цикла.
    # · Remove if: monitoring перейдёт на другой источник disk-метрик
    """
    caplog.set_level(logging.DEBUG)
    ssh = node_ssh
    urls = _site_urls(requires_node)
    t0 = time.monotonic()
    fill_path = "/tmp/chaos-f8.fill"

    # расчёт объёма из df: цель used≥92%, cap 94%
    calc = ssh.ssh_exec(
        "TOTAL=$(df -B1 --output=size / | sed -n '2p' | tr -d ' '); "
        "AVAIL=$(df -B1 --output=avail / | sed -n '2p' | tr -d ' '); "
        "CUR=$((TOTAL - AVAIL)); "
        "NEED=$((TOTAL * 93 / 100 - CUR)); "
        "CAP=$((TOTAL * 94 / 100 - CUR)); "
        '[ "$NEED" -gt "$CAP" ] && NEED=$CAP; '
        f"fallocate -l $NEED {fill_path} && echo FALLOC_OK bytes=$NEED",
        timeout=60,
    )
    m = re.search(r"FALLOC_OK bytes=(\d+)", calc.stdout)
    assert m, f"F8 FAIL: fallocate did not run: {calc.stdout} {calc.stderr}"
    logger.info("[IMP:9][F8][inject] fallocate %d bytes (%.1f GiB) → /tmp", int(m.group(1)), int(m.group(1)) / 2**30)
    try:

        def _used_pct() -> str | None:
            out = ssh.ssh_read('df -P / | awk \'NR==2 {gsub("%","",$5); print $5}\'', timeout=20).stdout.strip()
            return f"used={out}%" if out.isdigit() and int(out) >= 92 else None

        proof = assert_injection_landed(_used_pct, timeout_s=30, description="df used% >= 92")

        def _ratio_below_threshold() -> str | None:
            r = _prom_ratio(ssh)
            return f"ratio={r}" if r is not None and r < 0.2 else None

        critical_proof = assert_injection_landed(
            _ratio_below_threshold, timeout_s=150, description="Prometheus ratio < 0.2", interval_s=10.0
        )
        codes_during = probe_sites_local(ssh, urls)
        logger.info("[IMP:9][F8][window] %s sites_alive=%s (%s)", critical_proof, sites_ok(codes_during), proof)
    finally:
        rm = ssh.ssh_exec(f"rm -f {fill_path}", timeout=60)
        assert rm.exit_code == 0, f"F8 cleanup FAIL: rm fill: {rm.stderr}"

    def _ratio_above_recovery() -> str | None:
        r = _prom_ratio(ssh)
        return f"ratio={r}" if r is not None and r > 0.5 else None

    recovery_proof = assert_injection_landed(
        _ratio_above_recovery, timeout_s=240, description="Prometheus ratio > 0.5 after rm", interval_s=10.0
    )
    ttr = int(time.monotonic() - t0)
    codes_after = probe_sites_local(ssh, urls)
    try:
        assert sites_ok(codes_during) and sites_ok(codes_after), (
            f"F8 FAIL: sites during={codes_during} after={codes_after}"
        )
        capture_evidence(
            ssh,
            _out_dir("F8"),
            None,
            test_id="F8",
            verdict="PASS",
            ttr_s=ttr,
            injection_proof=f"df {proof}; {critical_proof}",
            extra={"prom_recovered": recovery_proof},
        )
    except AssertionError:
        capture_evidence(
            ssh,
            _out_dir("F8"),
            None,
            test_id="F8",
            verdict="FAIL",
            ttr_s=ttr,
            injection_proof=f"df {proof}; {critical_proof}",
        )
        raise
    assert_ldd_imp9_e2e(caplog)


# endregion TEST_F8_disk_pressure


# region TEST_F9_tor_fails_loud
@pytest.mark.chaos
@pytest.mark.requires_node
def test_tor_channel_fails_loud(requires_node: str, node_ssh: NodeSSHClient, caplog) -> None:
    """F9: stop tor@default+privoxy → send_telegram=False с ТРАНСПОРТНОЙ ошибкой (fail-loud,
    не silent) → start → «Privoxy → Tor forward: working» + сервисы active ≤180s.
    БЕЗ telegram-stage токена: recovery-критерий = privoxy-стадия (токен 404 — pre-existing
    Debt Intake, канал доставки не зависит от валидности токена).

    # 🧪 TRAP[TEST] · Scenario: telegram-канал отказывает ГРОМКО (не silent) · Last fail: T5-era: cron-alignment sleep'ы (+10 мин) и токен-зависимость удалены (DevPlan 013 §8 Debt Intake)
    # · Regression: notify-канал нода→Telegram ходит через privoxy→tor; обрыв транспорта даёт
    # ·   DELIVERY FAILED с URLError (соединение с proxy), НЕ HTTP 404 (ответ API сквозь канал).
    # · Remove if: notify-канал перестанет использовать tor/privoxy транспорт
    """
    caplog.set_level(logging.DEBUG)
    ssh = node_ssh
    urls = _site_urls(requires_node)
    t0 = time.monotonic()
    logger.info("[IMP:9][F9][inject] stop tor@default + privoxy")

    stop = ssh.ssh_exec("systemctl stop tor@default.service privoxy.service", timeout=60)
    assert stop.exit_code == 0, f"stop tor/privoxy failed: {stop.stderr}"

    def _services_down() -> str | None:
        out = ssh.ssh_read(
            "systemctl is-active tor@default.service privoxy.service | tr '\\n' ' '", timeout=30
        ).stdout.strip()
        # NB: "inactive" содержит подстроку "active" — считать через split()
        return out if out.split().count("inactive") == 2 else None

    proof = assert_injection_landed(_services_down, timeout_s=30, description="tor+privoxy inactive")
    codes_during = probe_sites_local(ssh, urls)

    # send_telegram напрямую: ожидаем False + транспортную (не API-) ошибку в логе доставки
    notifier = ssh.ssh_exec(
        f"set -a; source {_SECRETS_ENV}; set +a; cd /opt/platform && PYTHONPATH=/opt/platform "
        "python3 - <<'PY' 2>&1\n"
        "import logging\n"
        "logging.basicConfig(level=logging.INFO, format='%(message)s')\n"
        "from core.internal.shared.notifications import send_telegram\n"
        "ok = send_telegram('chaos-F9 transport probe', proxy_url='http://127.0.0.1:8118')\n"
        "print('SENT_OK=' + str(ok))\n"
        "PY",
        timeout=120,
    )
    sent_failed = "SENT_OK=False" in notifier.stdout
    # транспортный маркер: DELIVERY FAILED НЕ от API-ответа (HTTP NNN), а от канала (URLError/proxy)
    transport_error = bool(re.search(r"DELIVERY FAILED: (?!Telegram API returned HTTP)\S+", notifier.stdout))
    logger.info(
        "[IMP:9][F9][window] sent_failed=%s transport_error=%s out=%s",
        sent_failed,
        transport_error,
        notifier.stdout.strip().splitlines()[-1:] or "<empty>",
    )

    start = ssh.ssh_exec("systemctl start tor@default.service privoxy.service", timeout=120)
    assert start.exit_code == 0, f"start tor/privoxy failed: {start.stderr}"

    def _channel_recovered() -> str | None:
        chk = ssh.ssh_exec(
            "cd /opt/platform && TELEGRAM_PROXY_URL=http://127.0.0.1:8118 "
            "python3 -m core.internal.healthcheck.tor_proxy_check 2>&1 | tail -4; echo EXIT=${PIPESTATUS[0]}",
            timeout=120,
        )
        svc = ssh.ssh_read("systemctl is-active tor@default.service privoxy.service | tr '\\n' ' '", timeout=30)
        if "Privoxy → Tor forward: working" in chk.stdout and svc.stdout.split() == ["active", "active"]:
            return chk.stdout.strip().splitlines()[0][:80]
        return None

    recovery_proof = assert_injection_landed(
        _channel_recovered, timeout_s=180, description="privoxy→tor forward working + services active", interval_s=15.0
    )
    ttr = int(time.monotonic() - t0)
    codes_after = probe_sites_local(ssh, urls)
    try:
        assert sent_failed and transport_error, (
            f"F9 FAIL: send did NOT fail loud through broken channel "
            f"(failed={sent_failed}, transport={transport_error}): {notifier.stdout[-300:]}"
        )
        assert sites_ok(codes_during) and sites_ok(codes_after), (
            f"F9 FAIL: sites during={codes_during} after={codes_after} — tor не должен влиять на ingress"
        )
        capture_evidence(
            ssh,
            _out_dir("F9"),
            None,
            test_id="F9",
            verdict="PASS",
            ttr_s=ttr,
            injection_proof=proof,
            extra={"recovery": recovery_proof},
        )
    except AssertionError:
        capture_evidence(ssh, _out_dir("F9"), None, test_id="F9", verdict="FAIL", ttr_s=ttr, injection_proof=proof)
        raise
    assert_ldd_imp9_e2e(caplog)


# endregion TEST_F9_tor_fails_loud


# ══════════════════════════════════════ NIGHT TIER ══════════════════════════════════════
# region TEST_N1_reboot
@pytest.mark.chaos
@pytest.mark.night
@pytest.mark.requires_node
def test_reboot_self_start_zero_loops(requires_node: str, node_ssh: NodeSSHClient, caplog) -> None:
    """N1: systemctl reboot → SSH ≤900s → стек ≤300s → сайты → ΔRestartCount==0 ∀ контейнеров.

    # 🧪 TRAP[TEST] · Scenario: boot-самостарт без restart-loops (W3-2 порт, P0 F-037 класс) · Last fail: W3-2 (162): стартовая гонка litellm раньше postgres давала delta>0
    # · Regression: после reboot порядок старта идёт через Docker restart policy + systemd
    # ·   (не orchestrator) — любой delta RestartCount > 0 во время boot = стартовая гонка
    # ·   (кандидат на healthcheck-wait в entrypoint). Кросс-бут аудит T1-T10 удалён (AC3).
    # · Remove if: порядок старта переведён на orchestrator/topo-sort
    """
    caplog.set_level(logging.DEBUG)
    ssh = node_ssh
    urls = _site_urls(requires_node)
    baseline = snapshot_running_containers(ssh)
    restart_before = _restart_count_map(ssh)
    logger.info("[IMP:9][N1][pre] baseline=%d containers, restart counts: %s", len(baseline), restart_before)

    t0 = time.monotonic()
    reboot = ssh.ssh_exec("systemctl reboot", timeout=60)
    logger.info("[IMP:9][N1][inject] systemctl reboot exit=%d (SSH drop expected)", reboot.exit_code)

    ssh_back = False
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
                    f"root@{ssh.host}",
                    "true",
                ],
                capture_output=True,
                timeout=15,
                check=False,
            )
            if proc.returncode == 0:
                ssh_back = True
                break
        except subprocess.TimeoutExpired:
            time.sleep(10)
        time.sleep(10)
    proof = f"boot_id={ssh.ssh_read('cat /proc/sys/kernel/random/boot_id', timeout=30).stdout.strip()[:8]}"
    assert ssh_back, "N1 FAIL: SSH did not come back within 900s"

    ok, missing = wait_containers_healthy(ssh, timeout_s=300, containers=baseline)
    sites_ok_flag, codes = wait_sites_up(ssh, urls, timeout_s=180)
    ttr = int(time.monotonic() - t0)

    restart_after = _restart_count_map(ssh)
    deltas = {name: restart_after.get(name, 0) - before for name, before in restart_before.items()}
    loops = {name: d for name, d in deltas.items() if d > 0}
    logger.info("[IMP:9][N1][recovery] ttr=%ss containers=%s sites=%s deltas_nonzero=%s", ttr, ok, sites_ok_flag, loops)
    try:
        assert ok, f"N1 FAIL: stack not healthy ≤300s after boot: {missing}"
        assert sites_ok_flag, f"N1 FAIL: sites after boot: {codes}"
        assert not loops, f"N1 FAIL: restart-loops after reboot (W3-2): {loops}"
        capture_evidence(
            ssh,
            _out_dir("N1"),
            None,
            test_id="N1",
            verdict="PASS",
            ttr_s=ttr,
            injection_proof=f"reboot issued, {proof}",
            extra={"restart_deltas_nonzero": loops},
        )
    except AssertionError:
        capture_evidence(
            ssh, _out_dir("N1"), None, test_id="N1", verdict="FAIL", ttr_s=ttr, injection_proof="reboot issued"
        )
        raise
    assert_ldd_imp9_e2e(caplog)


# endregion TEST_N1_reboot


# region TEST_N2_outbound_partition
_PARTITION_RULES_V4 = (
    "*filter\n:INPUT ACCEPT [0:0]\n:FORWARD ACCEPT [0:0]\n:OUTPUT DROP [0:0]\n"
    "-A OUTPUT -m conntrack --ctstate ESTABLISHED,RELATED -j ACCEPT\n"
    "-A OUTPUT -o lo -j ACCEPT\n-A OUTPUT -d 172.16.0.0/12 -j ACCEPT\n"
    "-A OUTPUT -d 10.0.0.0/8 -j ACCEPT\n-A OUTPUT -d 192.168.0.0/16 -j ACCEPT\nCOMMIT\n"
)
_PARTITION_RULES_V6 = (
    "*filter\n:INPUT ACCEPT [0:0]\n:FORWARD ACCEPT [0:0]\n:OUTPUT DROP [0:0]\n"
    "-A OUTPUT -m conntrack --ctstate ESTABLISHED,RELATED -j ACCEPT\n"
    "-A OUTPUT -o lo -j ACCEPT\n-A OUTPUT -d fc00::/7 -j ACCEPT\n"
    "-A OUTPUT -d fe80::/10 -j ACCEPT\nCOMMIT\n"
)


@pytest.mark.chaos
@pytest.mark.night
@pytest.mark.requires_node
def test_outbound_partition_inbound_alive(requires_node: str, node_ssh: NodeSSHClient, caplog) -> None:
    """N2: OUTPUT DROP v4+v6 на 45s (nohup auto-revert из iptables-save снапшота) → inbound
    жив (локальный probe), исходящие curl exit 7/28 → revert → outbound restored; safety-net
    docker-цепочек сохранён.

    # 🧪 TRAP[TEST] · Scenario: egress-partition не трогает inbound (ingress изоляция ufw) · Last fail: T3-era run 3-5: без v6 правил Happy-Eyeballs делал партицию дырявой — v4+v6 обязательны
    # · Regression: INPUT/ufw не зависит от OUTPUT; conntrack flush обязателен (stale
    # ·   ESTABLISHED маскирует новые SYN через ctstate-ACCEPT); auto-revert из снапшота;
    # ·   снапшот МОЖЕТ не содержать docker-цепочек → safety-net проверяет DOCKER-USER.
    # · Remove if: egress-политика переедет на другой механизм (nftables-only и т.п.)
    """
    caplog.set_level(logging.DEBUG)
    ssh = node_ssh
    urls = _site_urls(requires_node)
    t0 = time.monotonic()
    write = ssh.ssh_exec(
        f"cat > /tmp/chaos-n2.rules <<'EOF'\n{_PARTITION_RULES_V4}EOF\n"
        f"cat > /tmp/chaos-n26.rules <<'EOF'\n{_PARTITION_RULES_V6}EOF",
        timeout=30,
    )
    assert write.exit_code == 0, f"rules write failed: {write.stderr}"
    inject = ssh.ssh_exec(
        "iptables-save > /tmp/chaos-n2-backup.rules && ip6tables-save > /tmp/chaos-n26-backup.rules && "
        "iptables-restore < /tmp/chaos-n2.rules && ip6tables-restore < /tmp/chaos-n26.rules && "
        "conntrack -F 2>/dev/null; "
        "nohup bash -c '(sleep 45; iptables-restore < /tmp/chaos-n2-backup.rules; "
        "ip6tables-restore < /tmp/chaos-n26-backup.rules; conntrack -F 2>/dev/null) "
        ">/tmp/chaos-n2-restore.log 2>&1' >/dev/null 2>&1 & "
        "echo PARTITION_OK",
        timeout=30,
    )
    assert "PARTITION_OK" in inject.stdout, f"partition start failed: {inject.stdout} {inject.stderr}"
    time.sleep(8)
    logger.info("[IMP:9][N2][inject] OUTPUT DROP v4+v6 (45s auto-revert armed)")

    try:
        codes_inbound = probe_sites_local(ssh, urls)
        proof_inbound = f"inbound_codes={'OK' if sites_ok(codes_inbound) else codes_inbound}"
        outbound_probe = ssh.ssh_read(
            "curl -s --noproxy '*' -o /dev/null -w '%{http_code}' -m 8 https://api.telegram.org/ 2>&1; echo C=$?",
            timeout=30,
        )
        outbound_blocked = outbound_probe.stdout.strip().endswith("C=28") or "C=7" in outbound_probe.stdout
        logger.info(
            "[IMP:9][N2][window] inbound_alive=%s outbound_blocked=%s (%s)",
            sites_ok(codes_inbound),
            outbound_blocked,
            outbound_probe.stdout.strip(),
        )
    finally:
        reverted, _ = await_condition(
            lambda: "-P OUTPUT ACCEPT" in ssh.ssh_read("iptables -S OUTPUT | head -1", timeout=20).stdout,
            timeout_s=300,
            interval_s=5.0,
        )

    # safety-net (находка W3 2026-08-03): снапшот может не содержать docker-цепочек →
    # FORWARD DROP без DOCKER-USER ломает container outbound — детект + restart docker
    docker_chains = ssh.ssh_read("iptables -S | grep -cE 'DOCKER-USER|DOCKER' || true", timeout=30)
    if int(docker_chains.stdout.strip() or "0") == 0:
        logger.warning("[IMP:8][N2][safety-net] docker iptables chains missing — restart docker")
        ssh.ssh_exec("systemctl restart docker", timeout=300)
        stack_ok, miss = wait_containers_healthy(ssh, timeout_s=240, containers=snapshot_running_containers(ssh))
        assert stack_ok, f"N2 safety-net FAIL: stack after docker restart: {miss}"

    def _outbound_restored() -> str | None:
        res = ssh.ssh_read(
            "curl -s --noproxy '*' -o /dev/null -w '%{http_code}' -m 15 https://api.telegram.org/ 2>&1; echo C=$?",
            timeout=30,
        )
        return "restored" if "C=0" in res.stdout else None

    restore_proof = assert_injection_landed(
        _outbound_restored, timeout_s=90, description="outbound connectivity restored"
    )
    ttr = int(time.monotonic() - t0)
    codes_after = probe_sites_local(ssh, urls)
    try:
        assert sites_ok(codes_inbound), f"N2 FAIL: inbound DOWN during partition: {codes_inbound}"
        assert outbound_blocked, f"N2 FAIL: outbound NOT blocked during partition: {outbound_probe.stdout}"
        assert reverted, "N2 FAIL: auto-revert did not fire within 300s"
        assert sites_ok(codes_after), f"N2 FAIL: sites after revert: {codes_after}"
        capture_evidence(
            ssh,
            _out_dir("N2"),
            None,
            test_id="N2",
            verdict="PASS",
            ttr_s=ttr,
            injection_proof=proof_inbound + "; outbound blocked (C=7/28)",
            extra={"restore": restore_proof},
        )
    except AssertionError:
        capture_evidence(
            ssh, _out_dir("N2"), None, test_id="N2", verdict="FAIL", ttr_s=ttr, injection_proof="partition issued"
        )
        raise
    assert_ldd_imp9_e2e(caplog)


# endregion TEST_N2_outbound_partition


# region TEST_N3_daemon_restart
@pytest.mark.chaos
@pytest.mark.night
@pytest.mark.requires_node
def test_docker_daemon_restart_containers_kept(requires_node: str, node_ssh: NodeSSHClient, caplog) -> None:
    """N3: systemctl restart docker → StartedAt ключевых контейнеров НЕПРЕРЫВЕН (containerd
    держит живые контейнеры, daemon переподключается) → стек healthy ≤240s → сайты 200.

    # 🧪 TRAP[TEST] · Scenario: daemon-restart uptime continuity (наблюдение W2 2026-08-03) · Last fail: N/A
    # · Regression: restart policy unless-stopped поднимает УПАВШИЕ контейнеры после daemon
    # ·   restart; ЖИВЫЕ не пересоздаются (StartedAt неизменен) — resilience-факт containerd.
    # · Remove if: docker-runtime перестанет переживать daemon restart с сохранением контейнеров
    """
    caplog.set_level(logging.DEBUG)
    ssh = node_ssh
    urls = _site_urls(requires_node)
    baseline = snapshot_running_containers(ssh)

    def _started_at_map() -> dict[str, str]:
        res = ssh.ssh_read(
            'for c in postgres nginx litellm clickhouse; do echo -n "$c "; '
            "docker inspect --format '{{.State.StartedAt}}' $c; done",
            timeout=30,
        )
        return dict(line.split() for line in res.stdout.strip().splitlines() if line.strip())

    started_before = _started_at_map()
    t0 = time.monotonic()
    logger.info("[IMP:9][N3][inject] systemctl restart docker")
    inject = ssh.ssh_exec("systemctl restart docker", timeout=300)
    assert inject.exit_code == 0, f"restart docker failed: {inject.stderr}"

    ok, missing = wait_containers_healthy(ssh, timeout_s=240, containers=baseline)
    started_after = _started_at_map()
    no_recreate = all(started_after.get(c) == v for c, v in started_before.items())
    sites_flag, codes = wait_sites_up(ssh, urls, timeout_s=120)
    ttr = int(time.monotonic() - t0)
    logger.info("[IMP:9][N3][recovery] ttr=%ss containers=%s no_recreate=%s sites=%s", ttr, ok, no_recreate, sites_flag)
    try:
        assert ok, f"N3 FAIL: stack not healthy within 240s: {missing}"
        assert no_recreate, f"N3 FAIL: containers recreated by daemon restart: {started_after}"
        assert sites_flag, f"N3 FAIL: sites after daemon restart: {codes}"
        assert ttr <= 240, f"N3 FAIL: TTR {ttr}s > 240s"
        capture_evidence(
            ssh,
            _out_dir("N3"),
            "nginx",
            test_id="N3",
            verdict="PASS",
            ttr_s=ttr,
            injection_proof=f"daemon restarted; StartedAt continuity={no_recreate}",
        )
    except AssertionError:
        capture_evidence(
            ssh,
            _out_dir("N3"),
            "nginx",
            test_id="N3",
            verdict="FAIL",
            ttr_s=ttr,
            injection_proof="daemon restart issued",
        )
        raise
    assert_ldd_imp9_e2e(caplog)


# endregion TEST_N3_daemon_restart
