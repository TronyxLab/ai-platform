# GREP_SUMMARY: multi-bootstrap, idempotency, requires-node, harness, test-VPS, bootstrap-node, 3x-run, SKIP-phases, NODE_PREBOOTSTRAPPED, AGE_SECRET_KEY_FILE, T12.11, multi-run
# STRUCTURE: ▶ resolve host (node-configs/<NODE>/node.yaml) → ○ 3× _run_bootstrap(make bootstrap-node → /tmp/multiboot_<ts>/run<N>.log) → ◇ assert exit=0 + SKIP markers + state done → ⊕ durations → ⎋ summary report
# region MODULE_CONTRACT
## @purpose  Multi-run harness (DevPlan 136 W12 T12.11): 3× bootstrap на test-VPS — проверка
##           ИДЕМПОТЕНТНОСТИ реального `make bootstrap-node` (фазы SKIP на повторных прогонах,
##           state.json не регрессирует). Каждый прогон 10-30 мин (холодный) / 5-10 мин (с SKIP) —
##           stdout в /tmp/multiboot_$(date +%s)/run<N>.log для чтения при таймауте.
## @scope    Integration harness, маркер requires_node (НЕ в make check / make gate — фильтр
##           `not requires_node`). Требует: NODE env, SSH root@test-VPS, AGE_SECRET_KEY_FILE.
## @invariants
##   - Ровно 3 прогона `make bootstrap-node NODE=<node>` — никакого reset state между ними
##     (идемпотентность на УЖЕ забутстрапленной ноде, NODE_PREBOOTSTRAPPED=1)
##   - Каждый прогон: exit 0 + >= 1 SKIP-маркер фазы + state.json фазы не регрессировали
##   - Логи прогонов: /tmp/multiboot_<ts>/run<N>.log (stdout+stderr) — читаемы при таймауте
##   - Таймаут прогона 1800s (subprocess) — никогда не висит; при таймауте читать лог
##   - AGE_SECRET_KEY_FILE: default ~/.config/sops/age/keys.txt (operator), override через env
## @rationale DevPlan 136 W12 T12.11: multi-run harness — эмпирическая проверка идемпотентности
##            (инвариант 6 AGENTS.md) на живой ноде; результаты → отчёт/Debt. Нода НЕ
##            пересоздаётся — повторные прогоны на забутстрапленной ноде = SKIP-семантика.
## @changes 2026-08-05 | DevPlan 136 W12 T12.11 — создан
# endregion MODULE_CONTRACT

from __future__ import annotations

import logging
import os
import subprocess
import time
from dataclasses import dataclass, field
from pathlib import Path

import pytest
import yaml

from tests._conftest.node import NodeSSHClient, NodeState, _require_node_env
from tests.helpers.gate_helpers import repo_root

logger = logging.getLogger(__name__)

N_RUNS = 3
RUN_TIMEOUT = 1800  # 30 мин на прогон (холодный bootstrap; с SKIP — 5-10 мин)
_DEFAULT_AGE_KEY = Path.home() / ".config" / "sops" / "age" / "keys.txt"


# region CLASS_BootstrapRunResult
@dataclass
class BootstrapRunResult:
    """Результат одного bootstrap-прогона."""

    run_idx: int
    returncode: int
    duration_s: float
    log_path: str
    skip_markers: int = 0
    timed_out: bool = field(default=False)


# endregion CLASS_BootstrapRunResult


# region HELPER_resolve_host
def _resolve_host(node: str) -> str:
    """Resolve test-VPS host from node-configs/<NODE>/node.yaml (R4-fail если нет)."""
    node_yaml = repo_root() / "node-configs" / node / "node.yaml"
    if not node_yaml.is_file():
        pytest.fail(f"node-configs/{node}/node.yaml not found at {node_yaml}", pytrace=False)
    with Path(node_yaml).open(encoding="utf-8") as f:
        data = yaml.safe_load(f) or {}
    host = (data.get("node") or {}).get("host", "")
    if not host:
        pytest.fail(f"node.host missing in {node_yaml}", pytrace=False)
    return host


# endregion HELPER_resolve_host


# region HELPER_run_bootstrap
def _run_bootstrap(node: str, run_dir: Path, run_idx: int) -> BootstrapRunResult:
    """Один прогон `make bootstrap-node NODE=<node>` — stdout в /tmp/multiboot_<ts>/run<N>.log.

    ▶ ┌node + run_dir┐ → ⚡ make bootstrap-node (env AGE+NODE_PREBOOTSTRAPPED) → ◇ timeout? →
       ⊕ stdout/stderr в лог → ⎋ BootstrapRunResult

    ## @purpose — Эмпирический прогон канонической операции (как оператор): make bootstrap-node
    ##            наследует E2E env (AGE_SECRET_KEY_FILE, NODE_PREBOOTSTRAPPED). Лог — на диск,
    ##            чтобы при таймауте (1800s) субагент мог прочитать фазы/ошибки без перезапуска.
    ## @io       ⇥ node, run_dir, run_idx → ⎋ BootstrapRunResult
    ## @complexity O(1) — один долгий subprocess
    ## @invariants
    ##   - cwd = repo_root() (make должен резолвить makefiles/ + node-configs/)
    ##   - env: os.environ + NODE_PREBOOTSTRAPPED=1 + AGE_SECRET_KEY_FILE (default operator key)
    ##   - timeout → timed_out=True, returncode=124-семантика (не крах)
    """
    log_path = run_dir / f"run{run_idx}.log"
    age_key = os.environ.get("AGE_SECRET_KEY_FILE", str(_DEFAULT_AGE_KEY))
    env = dict(os.environ)
    env["NODE_PREBOOTSTRAPPED"] = "1"
    env["AGE_SECRET_KEY_FILE"] = age_key
    if not Path(age_key).is_file():
        pytest.fail(
            f"AGE_SECRET_KEY_FILE {age_key} not found — bootstrap φ4 (secrets) требует ключ "
            "(T12.11 harness pre-flight)",
            pytrace=False,
        )

    args = ["make", "bootstrap-node", f"NODE={node}"]
    logger.info("[IMP:8][multi_bootstrap][run%d] %s (log=%s)", run_idx, " ".join(args), log_path)
    start = time.monotonic()
    try:
        with Path(log_path).open("w", encoding="utf-8") as f:
            proc = subprocess.run(
                args,
                cwd=str(repo_root()),
                env=env,
                stdout=f,
                stderr=subprocess.STDOUT,
                timeout=RUN_TIMEOUT,
                check=False,
            )
    except subprocess.TimeoutExpired:
        # Лог уже на диске — читаем его позже; таймаут = graceful failure, не крах
        return BootstrapRunResult(
            run_idx=run_idx, returncode=124, duration_s=time.monotonic() - start, log_path=str(log_path), timed_out=True
        )
    duration = time.monotonic() - start
    # Подсчёт SKIP-маркеров фаз (идемпотентность: повторные фазы → SKIP)
    log_text = log_path.read_text(errors="replace", encoding="utf-8")
    skip_markers = max(
        log_text.count("SKIP"),
        log_text.lower().count("skipping"),
        log_text.count("already done"),
    )
    logger.info(
        "[IMP:9][multi_bootstrap][run%d] exit=%d duration=%.0fs skip_markers=%d",
        run_idx,
        proc.returncode,
        duration,
        skip_markers,
    )
    return BootstrapRunResult(
        run_idx=run_idx,
        returncode=proc.returncode,
        duration_s=duration,
        log_path=str(log_path),
        skip_markers=skip_markers,
    )


# endregion HELPER_run_bootstrap


# region TEST_multi_bootstrap_idempotency
@pytest.mark.requires_node
def test_3x_bootstrap_idempotent(caplog, tmp_path: Path) -> None:
    """3× `make bootstrap-node` на забутстрапленной test-VPS — все прогоны идемпотентны (фазы SKIP).

    # 🧪 TRAP[TEST] · Scenario: 3× bootstrap на живой ноде (NODE_PREBOOTSTRAPPED=1)
    # · Regression: AGENTS.md инвариант 6 («make bootstrap-node — строго идемпотентный»)
    # · Last fail: N/A (новый harness T12.11)
    # · Remove if: bootstrap заменён на non-идемпотентную модель (противоречит инварианту 6)
    ## @purpose — T12.11 (multi-run): эмпирическая идемпотентность реального bootstrap.
    ##            Каждый прогон: exit 0 + >= 1 SKIP-маркер + INIT-фазы state.json не регрессировали.
    ## @io — ⇥ caplog, tmp_path → ⎋ None (asserts)
    ## @complexity O(N_RUNS × RUN_TIMEOUT) — ~15-30 мин суммарно
    """
    caplog.set_level(logging.DEBUG)
    node = _require_node_env()  # R4-fail при отсутствии NODE (не skip)
    host = _resolve_host(node)
    ssh = NodeSSHClient(host)
    state = NodeState(ssh)

    # Логи прогонов — в /tmp/multiboot_<ts>/ (читаемы субагентом при таймауте; tmp_path-копия в отчёте)
    run_dir = Path(f"/tmp/multiboot_{int(time.time())}")
    run_dir.mkdir(parents=True, exist_ok=True)

    results: list[BootstrapRunResult] = []
    for i in range(1, N_RUNS + 1):
        result = _run_bootstrap(node, run_dir, i)
        results.append(result)
        assert not result.timed_out, (
            f"run{i} TIMEOUT after {RUN_TIMEOUT}s — см. лог {result.log_path} (читать файл, не перезапускать)"
        )
        assert result.returncode == 0, f"run{i} bootstrap failed (exit={result.returncode}) — лог {result.log_path}"
        # Идемпотентность: на повторном прогоне фазы SKIP (не re-run)
        if i > 1:
            assert result.skip_markers >= 1, (
                f"run{i}: НЕТ SKIP-маркеров фаз — bootstrap НЕ идемпотентен (лог {result.log_path})"
            )

    # State не регрессировал: INIT-фазы всё ещё done
    init_phases = list(state.phases("init").keys())
    pending = state.all_phases_done(init_phases)[1]  # только pending важен для идемпотентности
    pending = [p for p in pending if p]
    assert not pending, f"INIT-фазы регрессировали после 3× bootstrap: {pending}"

    # Отчёт
    report = {
        "runs": [
            {
                "run": r.run_idx,
                "exit": r.returncode,
                "duration_s": round(r.duration_s, 1),
                "skip_markers": r.skip_markers,
                "log": r.log_path,
            }
            for r in results
        ],
        "total_duration_s": round(sum(r.duration_s for r in results), 1),
    }
    report_path = tmp_path / "multiboot_report.json"
    report_path.write_text(__import__("json").dumps(report, indent=2), encoding="utf-8")
    logger.critical(
        "[IMP:9][multi_bootstrap] 3× bootstrap IDEMPOTENT — durations_s=%s total=%.0fs",
        [r.duration_s for r in results],
        report["total_duration_s"],
    )
    for r in results:
        logger.critical(
            "[IMP:9][multi_bootstrap] run%d: exit=%d duration=%.0fs skip=%d log=%s",
            r.run_idx,
            r.returncode,
            r.duration_s,
            r.skip_markers,
            r.log_path,
        )
    logger.info("--- LDD TRAJECTORY (IMP:7-10) ---")
    for record in list(caplog.records):
        if "[IMP:" in record.message:
            logger.info("%s", record.message)
    logger.info("--- END LDD TRAJECTORY ---")


# endregion TEST_multi_bootstrap_idempotency
