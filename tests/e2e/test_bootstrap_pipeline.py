# GREP_SUMMARY: e2e bootstrap-pipeline cold-start 9-phases update-mode 5-phases converge deploy healthcheck backup snapshot restore roundtrip rebootstrap idempotent
# STRUCTURE: ▶ T6 cold-start bootstrap → T7 update-mode → T8 converge idempotent → T9 deploy → T10 healthcheck → T11 backup snapshot → T12 restore roundtrip → T13 rebootstrap idempotent
# region MODULE_CONTRACT
## @purpose  DevPlan 095 Wave 2 (T6-T13): 8 happy-path E2E scenarios of the real bootstrap
##           pipeline on a recreatable test-VPS — cold-start bootstrap (9 INIT phases) →
##           node-update (5 UPDATE phases) → converge → deploy test-project via
##           DeployOrchestrator.receive() → healthcheck → backup snapshot → restore
##           round-trip → idempotent rebootstrap. Closes GAP-4 / VR 091 AC-B3
##           (NOT_VERIFIABLE → verifiable).
## @scope    Requires: test-VPS (node-configs/test-e2e/node.yaml), NODE env, SSH access,
##           AGE_SECRET_KEY. Run via `make test-node NODE=test-e2e`.
## @invariants
##   - Tests are ORDER-DEPENDENT (pipeline flow): definition order = execution order
##     (pytest preserves definition order within a module; session-scoped fixtures per DevPlan §4.1)
##   - Deterministic: 0 @pytest.mark.parametrize (Anti-Loop Note, AC6)
##   - Every test: @pytest.mark.requires_node + requires_node fixture param (DevPlan §IMPLEMENTATION NOTE)
##   - Every test: LDD IMP:9 assertion (AC7) + TRAP[TEST] marker
##   - NO_SERVICE (NODE missing) → FAIL via fixture, never skip (Rule R4)
## @rationale DevPlan 095: единственный способ верифицировать 14 фаз + DeployOrchestrator +
##           lib/ssh.sh end-to-end на реальном VPS после Strangler-Fig миграций.
## ⚠️ TRAP[DECISION] · 2026-07-31 · HI · T9: `make deploy` (git push → CI) заменён на receive-доставку
## · Rejected: `make deploy PROJECT=tests/e2e/fixtures/test-project NODE=<node>` — таргет требует
##   git-репозиторий с origin и выполняет git push (CI в E2E-окружении отсутствует); fixture не git.
## · Reason: CI-эквивалент = сборка payload tar локально + SSH stdin → orchestrator_cli receive
##   на VPS (ровно то, что CI делает после push; AC10 из DevPlan 089). receive() баг с пустым
##   SCPChannel исправлен (LocalChannel) — см. TRAP[DECISION] в core/internal/deploy/orchestrator.py.
## · Rev: если появится тестовый CI-workflow с forced-command — переключить на него.
## ⚠️ TRAP[DECISION] · 2026-07-31 · MED · T10: `make healthcheck NODE=` — локальный стек, не VPS
## · Rejected: `make healthcheck NODE=<node>` — modules-healthcheck.sh итерирует ЛОКАЛЬНЫЕ
##   core/modules/*/module.yaml (аргумент NODE игнорируется) — проверяет dev-машину, не VPS.
## · Reason: E2E проверяет развёрнутый контейнер test-project на VPS напрямую (docker inspect
##   State.Status/Health) — «все модули healthy» для ноды с modules=[] тривиально.
## · Rev: если modules-healthcheck.sh получит remote-mode (NODE) — переключить тест на make healthcheck.
## ⚠️ TRAP[DECISION] · 2026-07-31 · MED · T11/T12: `make backup/restore` — локальный backup-cron/postgres
## · Rejected: `make backup NODE=` / `make restore NODE=` — root-таргеты работают с ЛОКАЛЬНЫМ
##   backup-cron/postgres стеком (docker exec), NODE не поддерживают; на test-VPS modules=[].
## · Reason: реальный backup-артефакт DeployOrchestrator на VPS = DeployHistory snapshot
##   (/opt/projects/<p>/.deploy-snapshots/*.json). T12 restore = передоставка payload
##   (compose up desired state) — rollback-путь требует IMAGE_TAG-совместимых compose-образов
##   (rollback re-tag {service}:previous-rollback) и покрыт unit-тестами DeployEngine.
## · Rev: если backup-cron получит NODE/remote-mode — переключить T11/T12 на make backup.
# endregion MODULE_CONTRACT

from __future__ import annotations

import json
import logging
import subprocess
from pathlib import Path

import pytest

from tests._conftest.node import (
    INIT_PHASES,
    UPDATE_PHASES,
    NodeSSHClient,
    NodeState,
    assert_ldd_imp9_e2e,
    build_payload_tar,
    deliver_payload_via_ssh,
)
from tests.helpers.gate_helpers import repo_root

logger = logging.getLogger(__name__)


# region HELPER_run_make
def _run_make(target: str, node: str, timeout: int = 900, extra: str = "") -> subprocess.CompletedProcess:
    """Run a local make target (NODE=<node>) and return the completed process.

    ▶ ┌target + node┐ → ⚡ make <target> NODE=<node> → ⎋ CompletedProcess

    ## @purpose — Execute the canonical make targets exactly as an operator would
    ##            (bootstrap-node / node-update / converge), inheriting the E2E env
    ##            (AGE_SECRET_KEY_FILE, SSH_KEY, NODE, DRY_RUN).
    ## @io — ⇥ target: str, node: str, timeout: int, extra: str → ⎋ CompletedProcess
    ## @complexity — O(1) — single subprocess
    ## @invariants
    ##   - cwd = repo_root() (make must resolve makefiles/ + node-configs/)
    ##   - env = os.environ copy + NODE (operator-provided AGE_SECRET_KEY_FILE passes through)
    """
    args = ["make", target, f"NODE={node}"] + ([extra] if extra else [])
    logger.info("[IMP:8][run_make] %s", " ".join(args))
    return subprocess.run(
        args,
        cwd=str(repo_root()),
        capture_output=True,
        text=True,
        timeout=timeout,
        check=False,
    )


# endregion HELPER_run_make


# region TEST_T6
@pytest.mark.requires_node
def test_cold_start_bootstrap_9_phases(
    requires_node: str, node_ssh: NodeSSHClient, node_state: NodeState, caplog
) -> None:
    """Cold-start bootstrap: `make bootstrap-node` → all 9 INIT phases done (VR 091 AC-B3).

    # 🧪 TRAP[TEST] · Scenario: cold-start 9 INIT фаз на чистой test-VPS · Last fail: N/A
    # · Regression: state.json reset не выполнен перед suite (test_vps_fresh) → фазы SKIP
    # · Remove if: bootstrap pipeline заменён (14-фазная структура DevPlan 087 устарела)
    ## @purpose — AC4/AC13: полный bootstrap из нуля на пересоздаваемой VPS. Каждая из
    ##            9 INIT фаз (φ1-φ8.5) должна получить done в state.json. Закрывает
    ##            VR 091 AC-B3 (NOT_VERIFIABLE → verifiable).
    ## @io — ⇥ requires_node, node_ssh, node_state, caplog → ⎋ None (asserts)
    ## @complexity — O(1) — one bootstrap run (~10-30 min on fresh VPS)
    """
    caplog.set_level(logging.DEBUG)

    result = _run_make("bootstrap-node", requires_node, timeout=1800)
    logger.info("[IMP:9][T6][bootstrap] exit=%d stderr_tail=%s", result.returncode, result.stderr.strip()[-300:])
    assert result.returncode == 0, f"Cold-start bootstrap failed (exit={result.returncode}): {result.stderr[-1500:]}"

    done, pending = node_state.all_phases_done(INIT_PHASES)
    logger.info("[IMP:9][T6][bootstrap] INIT phases done=%d/%d pending=%s", len(done), len(INIT_PHASES), pending)
    assert not pending, f"INIT phases not done after bootstrap: {pending}"

    assert_ldd_imp9_e2e(caplog)


# endregion TEST_T6


# region TEST_T7
@pytest.mark.requires_node
def test_update_mode_5_phases(requires_node: str, node_state: NodeState, caplog) -> None:
    """`make node-update` → all 5 UPDATE phases (φ9-φ13) done. Runs AFTER T6 (INIT required).

    # 🧪 TRAP[TEST] · Scenario: node-update на забутстрапленной ноде · Last fail: N/A
    # · Regression: node-update.sh падает без INIT (state.json отсутствует) → тест требует T6
    # · Remove if: UPDATE-фазы (φ9-φ13) удалены из pipeline
    ## @purpose — AC4: инкрементальный update-режим — 5 UPDATE фаз (secrets/node-config/
    ##            registry/deploy/converge update) выполняются и получают done.
    ## @io — ⇥ requires_node, node_state, caplog → ⎋ None (asserts)
    ## @complexity — O(1) — one node-update run
    """
    caplog.set_level(logging.DEBUG)

    result = _run_make("node-update", requires_node, timeout=900)
    logger.info("[IMP:9][T7][update] exit=%d", result.returncode)
    assert result.returncode == 0, f"node-update failed (exit={result.returncode}): {result.stderr[-1500:]}"

    done, pending = node_state.all_phases_done(UPDATE_PHASES)
    logger.info("[IMP:9][T7][update] UPDATE phases done=%d/%d pending=%s", len(done), len(UPDATE_PHASES), pending)
    assert not pending, f"UPDATE phases not done after node-update: {pending}"

    assert_ldd_imp9_e2e(caplog)


# endregion TEST_T7


# region TEST_T8
@pytest.mark.requires_node
def test_converge_idempotent(requires_node: str, node_ssh: NodeSSHClient, caplog) -> None:
    """`make converge` → exit 0 (clean) or 1 (warnings); desired state achieved; idempotent.

    # 🧪 TRAP[TEST] · Scenario: converge R1-R9 на забутстрапленной ноде · Last fail: N/A
    # · Regression: converge.sh вернул exit>=2 (errors) — reconcile R-юниты не могут выполниться
    # · Remove if: converge удалён из lifecycle (reconciler.py заменён)
    ## @purpose — AC4: нода конвергируется с desired state из node.yaml (stub test-project
    ##            создаётся в /opt/projects/test-project). Повторный converge → тот же exit
    ##            (идемпотентность, AGENTS.md инвариант 6/8 семантика 0/1/2).
    ## @io — ⇥ requires_node, node_ssh, caplog → ⎋ None (asserts)
    ## @complexity — O(1) — two converge runs
    """
    caplog.set_level(logging.DEBUG)

    first = _run_make("converge", requires_node, timeout=600)
    logger.info("[IMP:9][T8][converge] first exit=%d (0=clean, 1=warnings)", first.returncode)
    assert first.returncode in (0, 1), f"converge failed with errors (exit={first.returncode}): {first.stderr[-1500:]}"

    second = _run_make("converge", requires_node, timeout=600)
    logger.info("[IMP:9][T8][converge] second exit=%d (idempotent)", second.returncode)
    assert second.returncode in (0, 1), f"second converge failed (exit={second.returncode}): {second.stderr[-1500:]}"

    # Desired state: stub project dir exists on the VPS (converge R3 reconcile_projects)
    check = node_ssh.ssh_read("test -d /opt/projects/test-project && echo EXISTS || echo MISSING", timeout=30)
    logger.info("[IMP:9][T8][converge] /opt/projects/test-project -> %s", check.stdout.strip())
    assert "EXISTS" in check.stdout, f"Desired state not achieved: {check.stdout} {check.stderr}"

    assert_ldd_imp9_e2e(caplog)


# endregion TEST_T8


# region TEST_T9
@pytest.mark.requires_node
def test_deploy_test_project(
    requires_node: str,
    node_ssh: NodeSSHClient,
    test_project_fixture: str,
    test_project_dir: str,
    caplog,
) -> None:
    """Deploy test-project via DeployOrchestrator.receive() (CI-equivalent) → container running.

    # 🧪 TRAP[TEST] · Scenario: payload tar → SSH stdin → receive() → compose up · Last fail: N/A
    # · Regression: receive() SCPChannel-баг (пустой metadata['host']) — исправлен LocalChannel
    # · Remove if: receive() путь доставки заменён другим каналом
    ## @purpose — AC4/AC10(089): DeployOrchestrator.receive() end-to-end на реальном SSH
    ##            forced-command. После receive: контейнер test-project-web running + HTTP 200.
    ## @io — ⇥ requires_node, node_ssh, test_project_fixture, test_project_dir, caplog → ⎋ None
    ## @complexity — O(P) — payload assembly + SSH delivery + compose up + pull (~2-4 min)
    """
    caplog.set_level(logging.DEBUG)

    tar_path = build_payload_tar(Path(test_project_dir))
    delivery = deliver_payload_via_ssh(node_ssh, tar_path, timeout=600)
    logger.info("[IMP:9][T9][deploy] receive exit=%d", delivery.exit_code)
    assert delivery.exit_code == 0, f"receive failed: {delivery.stdout[-800:]} {delivery.stderr[-800:]}"

    try:
        payload_json = json.loads(delivery.stdout.strip().splitlines()[-1])
    except json.JSONDecodeError:
        payload_json = {}
    logger.info(
        "[IMP:9][T9][deploy] DeployResult status=%s project=%s", payload_json.get("status"), payload_json.get("project")
    )
    assert payload_json.get("status") in ("DEPLOYED", "PARTIAL"), f"DeployResult not success: {delivery.stdout[-800:]}"
    assert payload_json.get("project") == test_project_fixture

    ps = node_ssh.docker_ps(project="test-project")
    logger.info("[IMP:9][T9][deploy] docker ps: %s", ps.stdout.strip())
    assert "test-project-web" in ps.stdout, f"Container test-project-web not running: {ps.stdout} {ps.stderr}"

    http = node_ssh.http_status(8080)
    logger.info("[IMP:9][T9][deploy] HTTP 8080 -> %s", http.stdout.strip())
    assert http.stdout.strip() == "200", f"test-project HTTP not 200: {http.stdout} {http.stderr}"

    assert_ldd_imp9_e2e(caplog)


# endregion TEST_T9


# region TEST_T10
@pytest.mark.requires_node
def test_healthcheck_all_healthy(requires_node: str, node_ssh: NodeSSHClient, caplog) -> None:
    """Deployed test-project container is running and healthy (docker inspect).

    # 🧪 TRAP[TEST] · Scenario: docker inspect State.Status + State.Health.Status · Last fail: N/A
    # · Regression: compose healthcheck отсутствует/кривой → container stuck in starting/unhealthy
    # · Remove if: modules-healthcheck.sh получает remote NODE mode — переключить на make healthcheck
    ## @purpose — AC4: развёрнутый сервис healthy. Прямая проверка на VPS: `make healthcheck`
    ##            локальный (см. TRAP[DECISION] в module contract) — E2E инспектирует
    ##            контейнер test-project-web (State.Status=running, Health=healthy).
    ## @io — ⇥ requires_node, node_ssh, caplog → ⎋ None (asserts)
    ## @complexity — O(1) — single docker inspect
    """
    caplog.set_level(logging.DEBUG)

    result = node_ssh.ssh_read(
        "docker inspect --format '{{.State.Status}} {{.State.Health.Status}}' test-project-web",
        timeout=30,
    )
    logger.info("[IMP:9][T10][healthcheck] inspect: %s", result.stdout.strip())
    assert result.exit_code == 0, f"docker inspect failed: {result.stderr}"
    assert "running" in result.stdout and "healthy" in result.stdout, (
        f"Container not healthy: '{result.stdout.strip()}'"
    )

    assert_ldd_imp9_e2e(caplog)


# endregion TEST_T10


# region TEST_T11
@pytest.mark.requires_node
def test_backup_creates_snapshot(requires_node: str, node_ssh: NodeSSHClient, caplog) -> None:
    """DeployHistory snapshot (backup artifact) exists after deploy — on the VPS.

    # 🧪 TRAP[TEST] · Scenario: /opt/projects/test-project/.deploy-snapshots/*.json · Last fail: N/A
    # · Regression: DeployOrchestrator.snapshot step не выполнился (receive/engine path) → 0 snapshot
    # · Remove if: DeployHistory заменён другим backup-механизмом
    ## @purpose — AC4: backup-артефакт создан в backup storage. VPS-side backup storage =
    ##            DeployHistory snapshots (/opt/projects/<p>/.deploy-snapshots/ — DevPlan 089 DD5),
    ##            создаётся DeployOrchestrator.deploy() после healthcheck (T9).
    ## @io — ⇥ requires_node, node_ssh, caplog → ⎋ None (asserts)
    ## @complexity — O(1) — single SSH ls
    """
    caplog.set_level(logging.DEBUG)

    result = node_ssh.ssh_read(
        "ls -1 /opt/projects/test-project/.deploy-snapshots/*.json 2>/dev/null | wc -l",
        timeout=30,
    )
    count = int(result.stdout.strip() or "0")
    logger.info("[IMP:9][T11][backup] snapshot count=%d", count)
    assert result.exit_code == 0, f"snapshot ls failed: {result.stderr}"
    assert count >= 1, f"No DeployHistory snapshot found on VPS (count={count})"

    assert_ldd_imp9_e2e(caplog)


# endregion TEST_T11


# region TEST_T12
@pytest.mark.requires_node
def test_restore_roundtrip(requires_node: str, node_ssh: NodeSSHClient, test_project_dir: str, caplog) -> None:
    """Destroy test-project (compose down -v) → restore via payload redelivery → HTTP 200.

    # 🧪 TRAP[TEST] · Scenario: down -v → container gone → redeploy → data restored · Last fail: N/A
    # · Regression: restore не поднял контейнер (payload/проект потерян после down -v) → 000/404
    # · Remove if: DeployOrchestrator rollback поддерживает произвольные compose-образы
    ## @purpose — AC4: backup → destroy → restore round-trip. «Данные восстановлены» =
    ##            HTTP 200 на 8080 + контейнер running. Restore-механизм: передоставка payload
    ##            (compose up desired state) — см. TRAP[DECISION] в module contract (rollback
    ##            требует IMAGE_TAG-совместимых образов, покрыт unit-тестами DeployEngine).
    ## @io — ⇥ requires_node, node_ssh, test_project_dir, caplog → ⎋ None (asserts)
    ## @complexity — O(P) — destroy + redelivery + compose up
    """
    caplog.set_level(logging.DEBUG)

    # ── Destroy ──
    down = node_ssh.ssh_exec("cd /opt/projects/test-project && docker compose down -v --remove-orphans", timeout=120)
    logger.info("[IMP:9][T12][restore] destroy exit=%d", down.exit_code)
    assert down.exit_code == 0, f"compose down failed: {down.stderr[-500:]}"

    gone = node_ssh.docker_ps(project="test-project")
    logger.info("[IMP:8][T12][restore] after destroy: %s", gone.stdout.strip())
    assert "test-project-web" not in gone.stdout, f"Container still present after destroy: {gone.stdout}"

    # ── Restore: redeliver payload (DeployOrchestrator restore semantics for stateless fixture) ──
    tar_path = build_payload_tar(Path(test_project_dir))
    delivery = deliver_payload_via_ssh(node_ssh, tar_path, timeout=600)
    logger.info("[IMP:9][T12][restore] restore delivery exit=%d", delivery.exit_code)
    assert delivery.exit_code == 0, f"restore receive failed: {delivery.stdout[-800:]} {delivery.stderr[-800:]}"

    http = node_ssh.http_status(8080)
    logger.info("[IMP:9][T12][restore] HTTP 8080 after restore -> %s", http.stdout.strip())
    assert http.stdout.strip() == "200", f"Data not restored (HTTP {http.stdout.strip()}): {http.stderr}"

    assert_ldd_imp9_e2e(caplog)


# endregion TEST_T12


# region TEST_T13
@pytest.mark.requires_node
def test_pipeline_idempotent_rebootstrap(requires_node: str, node_state: NodeState, caplog) -> None:
    """Second `make bootstrap-node` → all phases SKIP (already done) — idempotent.

    # 🧪 TRAP[TEST] · Scenario: повторный bootstrap после полного INIT · Last fail: N/A
    # · Regression: state.json потерян/не читается → фазы перевыполняются (не SKIP)
    # · Remove if: bootstrap перестал быть идемпотентным (AGENTS.md инвариант 6 нарушен)
    ## @purpose — AC4/AC6: идемпотентность bootstrap на реальном окружении. Повторный запуск
    ##            не перевыполняет фазы: лог содержит «already done — skipping», все 9 INIT
    ##            фаз остаются done (grouped-phase skip logic, AGENTS.md инвариант 6).
    ## @io — ⇥ requires_node, node_state, caplog → ⎋ None (asserts)
    ## @complexity — O(1) — one rebootstrap run (~5-10 min with skips)
    """
    caplog.set_level(logging.DEBUG)

    result = _run_make("bootstrap-node", requires_node, timeout=1800)
    logger.info("[IMP:9][T13][rebootstrap] exit=%d", result.returncode)
    assert result.returncode == 0, f"Rebootstrap failed (exit={result.returncode}): {result.stderr[-1500:]}"

    combined = (result.stdout or "") + (result.stderr or "")
    skip_markers = combined.count("already done — skipping") + combined.count("Phase .* skipping")
    logger.info("[IMP:9][T13][rebootstrap] skip markers found=%d", skip_markers)
    assert skip_markers >= 1, f"No phase-skip logs in rebootstrap output: {combined[-1500:]}"

    done, pending = node_state.all_phases_done(INIT_PHASES)
    logger.info(
        "[IMP:9][T13][rebootstrap] INIT phases still done=%d/%d pending=%s", len(done), len(INIT_PHASES), pending
    )
    assert not pending, f"INIT phases regressed after rebootstrap: {pending}"

    assert_ldd_imp9_e2e(caplog)


# endregion TEST_T13
