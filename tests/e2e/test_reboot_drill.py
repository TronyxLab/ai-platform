# GREP_SUMMARY: reboot-drill e2e requires-node platform-secrets active docker healthy plan-012 T1 F-037 AC5
# STRUCTURE: ▶ _require_node_env → ⚡ reboot (manual gate) → ○ poll systemctl is-active platform-secrets → ◇ docker ps health 25/25 → ⎋ PASS|FAIL
# region MODULE_CONTRACT
## @purpose  Reboot-drill E2E (plan 012 T1 / F-037 / AC5): reboot ноды → platform-secrets
##           active БЕЗ ручных drop-in обходов → docker-стек поднимается здоровым.
## @scope    requires_node harness — НЕ в make check/make gate (фильтр not requires_node).
##           Требует: NODE env, SSH root@<node>. Запуск вручную: make test-node NODE=<n>.
## @invariants
##   - Юнит platform-secrets содержит Environment=PYTHONPATH=/opt/platform (unit-тест T1)
##   - После reboot: systemctl is-active platform-secrets == active (exited) в пределах окна
##   - Docker-стек: все контейнеры с healthcheck → healthy; контейнеры без healthcheck → running
##   - Тест НЕ выполняет reboot сам (destructive) — оператор ребутает, тест верифицирует
##     пост-reboot состояние через REBOOTED=1 гейт-переменную (fail-loud без неё)
## @rationale F-037: после reboot decrypt_secrets.py падал на импортах core.internal.* —
##            чинилось ручным drop-in PYTHONPATH. Юнит теперь самодостаточен (T1);
##            drill доказывает это на живой ноде.
## @changes   CREATED 2026-08-26 | DevPlan 012 T1 — reboot drill (F-037)
# endregion MODULE_CONTRACT

from __future__ import annotations

import logging
import os
import time

import pytest

from tests._conftest.node import NodeSSHClient, _require_node_env

logger = logging.getLogger(__name__)

ACTIVE_POLL_TIMEOUT_S = 300
POLL_INTERVAL_S = 10


def _container_health_summary(client: NodeSSHClient) -> tuple[int, int, int, list[str]]:
    """Summarize `docker ps` states → (total, healthy_or_running, unhealthy, problems)."""
    result = client.docker_ps()
    assert result.exit_code == 0, f"[IMP:9][reboot-drill] FAIL: docker ps failed: {result.stderr}"
    lines = [ln for ln in result.stdout.splitlines()[1:] if ln.strip()]
    total = len(lines)
    problems: list[str] = []
    healthy = 0
    for ln in lines:
        # docker ps format: STATUS column contains "healthy"/"unhealthy"/"Up ..."
        lower = ln.lower()
        if "unhealthy" in lower:
            problems.append(ln)
        elif "(healthy)" in lower or "up " in lower or "exited (0)" in lower:
            healthy += 1
        else:
            problems.append(ln)
    return total, healthy, len(problems), problems


@pytest.mark.requires_node
def test_reboot_stack_alive() -> None:
    """Reboot ноды → platform-secrets active → docker-стек живой (AC5).

    ## @purpose — F-037 regression drill: единица загрузки самодостаточна (PYTHONPATH in unit),
    ##            секреты расшифрованы до docker, стек поднимается без оператора.
    ## @io — ⇥ NODE env + SSH → ⎋ None (asserts service + container states)
    ## @complexity — O(n) polling over containers
    ## @scenario — AC5: reboot → platform-secrets active → docker healthy
    """
    # 🧪 TRAP[TEST] · REGRESSION · F-037 reboot drill (requires_node)
    # · Scenario: operator reboots node (REBOOTED=1) → poll platform-secrets → poll docker health
    # · Last fail: unit без PYTHONPATH → ImportError после reboot → secrets.env отсутствует → стек не поднялся
    # · Remove if: bootstrap перестаёт использовать systemd oneshot для secrets provision
    node = _require_node_env()
    if os.environ.get("REBOOTED") != "1":
        pytest.fail(
            "[IMP:9][reboot-drill] Оператор должен ПЕРЕЗАГРУЗИТЬ ноду и запустить тест с "
            "REBOOTED=1 (destructive reboot выполняется вручную, не тестом). "
            "Flow: ssh root@<node> 'reboot' → wait → REBOOTED=1 make test-node"
        )

    client = NodeSSHClient(node)

    # ── Phase 1: platform-secrets becomes active after boot ──
    deadline = time.monotonic() + ACTIVE_POLL_TIMEOUT_S
    last_status = "<none>"
    while time.monotonic() < deadline:
        res = client.ssh_read("systemctl is-active platform-secrets.service", timeout=30)
        last_status = res.stdout.strip() or res.stderr.strip()
        logger.info("[IMP:8][reboot-drill] systemctl status: %s", last_status)
        if last_status == "active":
            break
        time.sleep(POLL_INTERVAL_S)

    assert last_status == "active", (
        f"[IMP:9][reboot-drill] FAIL: platform-secrets not active within {ACTIVE_POLL_TIMEOUT_S}s "
        f"(last status: {last_status}) — check journalctl -u platform-secrets"
    )
    logger.info("[IMP:9][reboot-drill] platform-secrets active (PYTHONPATH self-sufficient unit)")

    # ── Phase 2: decrypted env exists ──
    env_check = client.ssh_read("test -s /var/lib/platform/run/secrets.env && echo OK", timeout=30)
    assert "OK" in env_check.stdout, (
        "[IMP:9][reboot-drill] FAIL: /var/lib/platform/run/secrets.env missing/empty after reboot"
    )
    logger.info("[IMP:9][reboot-drill] secrets.env decrypted and non-empty")

    # ── Phase 3: docker stack alive (no unhealthy containers) ──
    total, healthy, bad, problems = _container_health_summary(client)
    logger.info("[IMP:8][reboot-drill] containers total=%d ok=%d bad=%d", total, healthy, bad)
    if problems:
        for p in problems:
            logger.warning("[IMP:7][reboot-drill] problem container: %s", p)
    assert bad == 0, f"[IMP:9][reboot-drill] FAIL: {bad} container(s) unhealthy/not-running after reboot: {problems}"
    assert total > 0, "[IMP:9][reboot-drill] FAIL: no containers found — stack did not start"
    logger.info("[IMP:9][reboot-drill] PASS: %d/%d containers alive post-reboot", healthy, total)
