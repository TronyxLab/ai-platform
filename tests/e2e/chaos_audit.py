# GREP_SUMMARY: chaos-drills lean-helpers await-condition probe-sites-local wait-containers-healthy container-pid injection-proof capture-evidence verdict-json node-yaml-resolver fast-tier night-tier
# STRUCTURE: ▶ load_node_yaml/resolve_site_urls → ◇ await_condition (единый poll) → ◇ probe_sites_local (--resolve 127.0.0.1, 1 SSH round-trip) → ◇ wait_sites_up / wait_containers_healthy → ◇ container_pid + assert_injection_landed (proof до recovery) → ⊕ capture_evidence (tail 200 + verdict.json) → ⎋
# region MODULE_CONTRACT
## @purpose  DevPlan 013 (resilience-drills rework): lean-хелперы для 9 fast + 3 night drills.
##           Заменяют LogAuditManifest-церемониал (multi-source log-forensics/Loki/alerts/export)
##           на поведенческие примитивы: proof-of-injection, degradation-window probe,
##           recovery-await с TTR-бюджетом, точечный evidence (1 контейнер × tail 200 + verdict.json).
## @scope    Consumed by tests/e2e/test_chaos_resilience.py. NOT in regular gate (chaos marker).
## @invariants
##   - Инвариант потока drill'а: НИ один recovery-wait не начинается без доказанной инъекции —
##     assert_injection_landed единственный канал доказательства (наследие TRAP[BUG] VR 142 §6)
##   - Канон healthcheck-критерия: running AND (healthy|""|none) = здоров; unhealthy/starting/
##     exited/restarting → ждать (TRAP[DECISION] root AGENTS.md; семантика старого wait_all_containers)
##   - SITE_URLS резолвятся из node-configs/<NODE>/node.yaml (projects[].expose+domain);
##     отсутствие файла/проектов — явный FAIL (R4), НЕ hardcode
##   - Все SSH-команды через NodeSSHClient (lib/ssh.sh parity, timeout→124)
##   - Локальный site-probe (--resolve host:443:127.0.0.1): DNS-независимый, бьёт в локальный
##     nginx (ingress+vhost+TLS), <1s на URL (DevPlan 013 §7 rationale)
## @rationale Q: почему удалён log-forensics? A: цель владельца — отказоустойчивость и
##           самовосстановление; behavioral assertions (proof/degradation/ttr) проверяют то же
##           свойство за секунды. Экспорт логов оставлен точечно (affected container tail 200).
## @changes 2026-08-26 | DevPlan 013 W1 TASK-1 — rewrite из chaos_audit.py (LogAuditManifest era)
# endregion MODULE_CONTRACT

from __future__ import annotations

import json
import logging
import time
from collections.abc import Callable
from pathlib import Path
from urllib.parse import urlparse

import pytest

from tests._conftest.node import NodeSSHClient
from tests.helpers.gate_helpers import repo_root

logger = logging.getLogger(__name__)

_SITE_OK_CODES = (200, 201, 301, 302, 307, 308, 401, 403)


# region FUNC_load_node_yaml
def load_node_yaml(node: str) -> dict:
    """Прочитать node-configs/<NODE>/node.yaml тем же резолвером, что использует conftest.

    ## @purpose — Единая точка резолва фактуры ноды для drills (projects/modules). Отсутствие
    ##            файла — явный FAIL (R4: environmental absence = config error), не skip.
    ## @io — ⇥ node: str → ⎋ dict | pytest.fail
    ## @complexity — O(1) — single YAML read
    """
    path = repo_root() / "node-configs" / node / "node.yaml"
    if not path.is_file():
        pytest.fail(
            f"node-configs/{node}/node.yaml not found at {path}. Chaos drills resolve sites/baseline "
            "from the node config (R4: missing config = FAIL, не hardcode). "
            "Синлайньте org-репозиторий node-configs в корень ворктри.",
            pytrace=False,
        )
    import yaml

    with Path(path).open(encoding="utf-8") as f:
        return yaml.safe_load(f) or {}


# endregion FUNC_load_node_yaml


# region FUNC_resolve_site_urls
def resolve_site_urls(node_yaml: dict) -> list[str]:
    """SITE_URLS из node.yaml: projects[] с expose:true и domain → https://<domain>/.

    ## @purpose — Список сайтов платформы из фактуры ноды (никаких hardcoded доменов).
    ## @io — ⇥ node_yaml: dict → ⎋ list[str] | pytest.fail (пусто → R4)
    ## @complexity — O(P) — P проектов
    """
    urls = [f"https://{p['domain']}/" for p in node_yaml.get("projects") or [] if p.get("expose") and p.get("domain")]
    if not urls:
        pytest.fail(
            "No exposed projects with domain in node.yaml — cannot probe sites (R4: config error).",
            pytrace=False,
        )
    logger.info("[IMP:8][helpers][sites] resolved %d site(s): %s", len(urls), urls)
    return urls


# endregion FUNC_resolve_site_urls


# region FUNC_await_condition
def await_condition(fn: Callable[[], bool], timeout_s: int, interval_s: float = 2.0) -> tuple[bool, bool]:
    """Единый poll-примитив: крутить fn() до True или таймаута. → (ok, timed_out).

    ## @purpose — Общий цикл ожидания для recovery-предикатов и state-poll'ов.
    ## @io — ⇥ fn, timeout_s, interval_s → ⎋ (ok, timed_out)
    ## @complexity — O(timeout_s / interval_s) вызовов fn
    """
    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        try:
            if fn():
                return True, False
        except Exception as exc:  # ruff: ignore[BLE001] — транзиентная SSH/парсинг-ошибка → retry, не крах
            logger.info("[IMP:7][helpers][await] transient poll error (retry): %s", exc)
        time.sleep(interval_s)
    return False, True


# endregion FUNC_await_condition


# region FUNC_probe_sites_local
def probe_sites_local(ssh: NodeSSHClient, urls: list[str]) -> dict[str, str]:
    """HTTP-коды сайтов через локальный nginx (curl --resolve host:443:127.0.0.1).

    ▶ ┌urls┐ → ⚡ ОДИН SSH round-trip (batched for-loop) → ⎋ {url: code}

    ## @purpose — DNS-независимый ingress-пробо: проверяется vhost+TLS на локальном nginx,
    ##            без сетевой дисперсии внешних проб (TTR-замер детерминирован, DevPlan 013 §7).
    ## @io — ⇥ ssh, urls → ⎋ {url: http_code_str}
    ## @complexity — O(1) SSH round-trip × O(U) curl внутри
    """
    hosts = [urlparse(u).netloc for u in urls]
    cmd = "; ".join(
        f"echo {h} $(curl -s --noproxy '*' --resolve '{h}:443:127.0.0.1' -o /dev/null -w '%{{http_code}}' -m 10 'https://{h}/')"
        for h in hosts
    )
    res = ssh.ssh_read(cmd, timeout=60)
    by_host = {}
    for line in res.stdout.strip().splitlines():
        parts = line.split()
        if len(parts) == 2:
            by_host[parts[0]] = parts[1]
    return {u: by_host.get(urlparse(u).netloc, "000") for u in urls}


def sites_ok(codes: dict[str, str]) -> bool:
    """Критерий «сайты живы»: все коды ∈ _SITE_OK_CODES (2xx/3xx/401/403)."""
    return bool(codes) and all(c.isdigit() and int(c) in _SITE_OK_CODES for c in codes.values())


# endregion FUNC_probe_sites_local


# region FUNC_wait_sites_up
def wait_sites_up(
    ssh: NodeSSHClient, urls: list[str], timeout_s: int, interval_s: float = 5.0
) -> tuple[bool, dict[str, str]]:
    """Ждать, пока ВСЕ сайты снова отвечают (поверх probe_sites_local). → (ok, last_codes)."""
    deadline = time.monotonic() + timeout_s
    last: dict[str, str] = {}
    while time.monotonic() < deadline:
        last = probe_sites_local(ssh, urls)
        if sites_ok(last):
            return True, last
        time.sleep(interval_s)
    return False, last


# endregion FUNC_wait_sites_up


# region FUNC_snapshot_running_containers
def snapshot_running_containers(ssh: NodeSSHClient) -> list[str]:
    """Baseline-контейнеры = живой снапшот `docker ps` (running-only) ДО инъекции.

    ## @purpose — Per-node baseline без static-списков: healthy snapshot из Data Flow drill'а.
    ##            Rejected: modules[]→containers маппинг из node.yaml — имена контейнеров не 1:1
    ##            с модулями (monitoring→prometheus/grafana/cadvisor); live-снапшот = runtime-правда.
    ## @io — ⇥ ssh → ⎋ list[str] (имена)
    ## @complexity — O(1) SSH round-trip
    """
    res = ssh.ssh_read("docker ps --format '{{.Names}}'", timeout=30)
    names = [ln.strip() for ln in res.stdout.splitlines() if ln.strip()]
    logger.info("[IMP:8][helpers][baseline] %d running container(s)", len(names))
    assert names, "baseline snapshot empty — docker stack is down (precondition violated)"
    return names


# endregion FUNC_snapshot_running_containers


# region FUNC_wait_containers_healthy
def wait_containers_healthy(
    ssh: NodeSSHClient, timeout_s: int, containers: list[str], interval_s: float = 5.0
) -> tuple[bool, list[str]]:
    """Ждать канонического здоровья набора: running AND (healthy|""|none).

    ▶ ┌containers┐ → ○ poll docker ps → ◇ Up ∧ (healthy ∨ нет health-скобок)? → ⎋ (ok, not_ready)

    ## @purpose — Единый recovery-предикат drills (семантика старого wait_all_containers,
    ##            канон healthcheck-критерия root AGENTS.md).
    ## @io — ⇥ ssh, timeout_s, containers → ⎋ (ok, not_ready_names)
    ## @complexity — O(timeout_s/interval_s) SSH round-trips
    """
    wanted = set(containers)

    def _fetch_status() -> dict[str, str]:
        res = ssh.ssh_read("docker ps --format '{{.Names}}\\t{{.Status}}'", timeout=30)
        status_map: dict[str, str] = {}
        for line in res.stdout.strip().splitlines():
            parts = line.split("\t")
            if len(parts) >= 2:
                status_map[parts[0]] = parts[1]
        return status_map

    def _all_ready() -> bool:
        status_map = _fetch_status()
        for c in containers:
            st = status_map.get(c)
            if st is None or not (st.startswith("Up") and ("healthy" in st or "(healthy)" in st or ")" not in st)):
                return False
        return True

    ok, _ = await_condition(_all_ready, timeout_s, interval_s)
    if ok:
        return True, []
    # финальный срез для диагностики: кто именно не готов
    status_map = _fetch_status()
    not_ready = sorted(c for c in wanted if c not in set(status_map))
    not_ready += sorted(c for c in containers if c in status_map and not (status_map[c].startswith("Up")))
    return False, not_ready


# endregion FUNC_wait_containers_healthy


# region FUNC_container_pid
def container_pid(ssh: NodeSSHClient, name: str) -> int:
    """Host-PID main-процесса контейнера (docker inspect .State.Pid) с guard >0.

    ## @purpose — kill-инъекция по host-pid (docker exec kill -9 1 НЕ доставляется namespace-init;
    ##            docker kill НЕ триггерит restart policy). Guard: pid 0/пустой = «убил не ту группу»
    ##            (TRAP[BUG] VR 142 §6) → AssertionError.
    ## @io — ⇥ ssh, name → ⎋ int (>0) | AssertionError
    ## @complexity — O(1)
    """
    raw = ssh.ssh_read(f"docker inspect --format '{{{{.State.Pid}}}}' {name}", timeout=30).stdout.strip()
    assert raw.isdigit() and int(raw) > 0, f"cannot resolve host pid of {name}: {raw!r}"
    return int(raw)


# endregion FUNC_container_pid


# region FUNC_assert_injection_landed
def assert_injection_landed(
    predicate: Callable[[], str | None],
    timeout_s: int,
    description: str,
    interval_s: float = 2.0,
) -> str:
    """Proof-of-injection: поллить predicate до evidence или AssertionError.

    ▶ ┌predicate┐ → ○ poll ≤timeout_s → ◇ evidence? → ⊕ IMP:9 log → ⎋ evidence | ⚡ AssertionError

    ## @purpose — ИНВАРИАНТ потока drill'а: ни один recovery-wait не начинается без
    ##            доказательства, что инъекция приземлилась (state exited/unhealthy/blocked/
    ##            kernel-marker). predicate возвращает evidence-строку или None.
    ## @io — ⇥ predicate, timeout_s, description → ⎋ evidence: str | AssertionError
    ## @complexity — O(timeout_s / interval_s) вызовов predicate
    """
    deadline = time.monotonic() + timeout_s
    last: str | None = None
    while time.monotonic() < deadline:
        try:
            last = predicate()
        except Exception as exc:  # ruff: ignore[BLE001] — транзиентная ошибка poll'а → retry
            last = None
            logger.info("[IMP:7][helpers][proof] transient error polling %s: %s", description, exc)
        if last is not None:
            logger.info("[IMP:9][helpers][proof] injection landed: %s (%s)", description, last)
            return last
        time.sleep(interval_s)
    msg = f"injection did NOT land within {timeout_s}s: {description} (last evidence={last!r})"
    raise AssertionError(msg)


# endregion FUNC_assert_injection_landed


# region FUNC_capture_evidence
def capture_evidence(
    ssh: NodeSSHClient,
    out_dir: Path,
    container: str | None,
    *,
    test_id: str,
    verdict: str,
    ttr_s: int,
    injection_proof: str,
    extra: dict | None = None,
) -> Path:
    """Точечный evidence: affected container × docker logs tail 200 + verdict.json.

    ▶ ┌facts┐ → ⚡ docker logs tail 200 → ⊕ verdict.json (injection_proof обязателен, AC2) → ⎋ out_dir

    ## @purpose — Посмертный разбор флаков без multi-source церемониала (AC2: каждый verdict.json
    ##            несёт injection_proof).
    ## @io — ⇥ ssh, out_dir, container, test_id/verdict/ttr/injection_proof → ⎋ out_dir
    ## @complexity — O(1)-O(2) SSH round-trips
    """
    out_dir.mkdir(parents=True, exist_ok=True)
    if container:
        logs = ssh.ssh_read(f"docker logs --tail 200 {container} 2>&1", timeout=90)
        (out_dir / f"{container}.log").write_text(logs.stdout, errors="replace", encoding="utf-8")
    payload = {
        "test": test_id,
        "verdict": verdict,
        "ttr_s": ttr_s,
        "injection_proof": injection_proof,
        "checked_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        **(extra or {}),
    }
    (out_dir / "verdict.json").write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")
    logger.info(
        "[IMP:9][helpers][verdict] %s verdict=%s ttr=%ss proof=%r → %s",
        test_id,
        verdict,
        ttr_s,
        injection_proof[:60],
        out_dir,
    )
    return out_dir


# endregion FUNC_capture_evidence


# region FUNC_host_epoch_seconds
def host_epoch_seconds(ssh: NodeSSHClient) -> int:
    """Текущее epoch-время хоста."""
    return int(ssh.ssh_read("date +%s", timeout=20).stdout.strip())


# endregion FUNC_host_epoch_seconds
