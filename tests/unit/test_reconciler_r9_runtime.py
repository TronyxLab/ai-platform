"""
# GREP_SUMMARY: test-reconciler, r9-runtime, reconcile-runtime, docker-inspect, compose-up, self-heal, cooldown
# STRUCTURE: ▶ tmp_path + monkeypatch + mock subprocess → ◇ R9 reconcile_runtime_state 3× (running/exited/cooldown) → ⊕ compose-up verify → ⊕ cooldown verify → ⎋ LDD trajectory
# region MODULE_CONTRACT
## @purpose  Unit tests for R9 reconcile_runtime_state in reconciler.py — docker container runtime state reconciliation
## @scope    Tests docker container state inspection and self-heal via docker compose up -d, with cooldown tracking
## @invariants
##   - All docker-dependent tests mock subprocess.run to avoid real docker calls
##   - File operations use tmp_path exclusively
##   - Each test validates IMP:9 business logic log presence via LDD trajectory
##   - Self-heal uses `docker compose up -d`, NOT `docker restart`
## @rationale Direct function testing with mock subprocess.run for docker inspect/compose commands
# endregion MODULE_CONTRACT
"""

import json
import logging
import subprocess
import sys
from pathlib import Path
from unittest.mock import patch

import pytest

from tests._conftest.ldd import ldd_trajectory

logger = logging.getLogger(__name__)

# ── Import the module under test ──
_MODULE_DIR = Path(__file__).resolve().parent.parent.parent / "core" / "internal" / "bootstrap" / "converge"
sys.path.insert(0, str(_MODULE_DIR))
import reconciler

from core.internal.bootstrap.converge import infra

# ═══════════════════════════════════════════════════════════════════
# region Fixtures
# ═══════════════════════════════════════════════════════════════════


@pytest.fixture
def reset_state():
    """Reset reconciler module state before each test."""
    infra.reset_state()
    infra.node_name = "test-node"
    infra.core_dir = str(Path(__file__).resolve().parent.parent.parent / "core")


@pytest.fixture
def node_yaml_with_modules(tmp_path):
    """Create a node.yaml with docker modules."""
    yaml_content = """
context: test-context
modules:
  - name: nginx
    enabled: true
  - name: postgres
    enabled: true
  - name: redis
    enabled: true
projects: []
"""
    yaml_path = tmp_path / "node.yaml"
    yaml_path.write_text(yaml_content)
    return str(yaml_path)


@pytest.fixture
def mock_modules_dir(tmp_path):
    """Create mock module directories with docker-compose.yml."""
    modules_base = tmp_path / "modules"
    for mod in ("nginx", "postgres", "redis"):
        mod_dir = modules_base / mod
        mod_dir.mkdir(parents=True)
        compose = mod_dir / "docker-compose.yml"
        compose.write_text(f"version: '3'\nservices:\n  {mod}:\n    image: {mod}:latest\n")
    return str(modules_base)


# endregion Fixtures


# ═══════════════════════════════════════════════════════════════════
# R9 — reconcile_runtime_state
# ═══════════════════════════════════════════════════════════════════


# region FUNC_test_reconcile_runtime_running
## 🧪 TRAP[TEST] · R9 running · Scenario: all containers running → status=converged
## · Regression: R9 convergence check — running containers = converged
## · Last fail: never
## · Remove if: reconcile_runtime_state running check logic changes
@pytest.mark.usefixtures("reset_state")
@ldd_trajectory
def test_reconcile_runtime_running(tmp_path, caplog, node_yaml_with_modules, mock_modules_dir):
    """R9: All containers running → status=converged."""
    caplog.set_level(logging.INFO)
    logger.info("[IMP:9][test] R9 running — all containers in running state")

    # Set up cooldown file
    cooldown_file = tmp_path / ".converge_cooldown.json"

    compose_up_calls = []

    def mock_run(cmd, *args, **kwargs):
        cmd_str = " ".join(cmd) if isinstance(cmd, list) else str(cmd)
        if "docker info" in cmd_str:
            return subprocess.CompletedProcess(args=cmd, returncode=0, stdout="", stderr="")
        # docker ps --filter name=<module>_<service> --format {.Names} → return container name
        if "docker ps" in cmd_str and "--filter" in cmd_str:
            return subprocess.CompletedProcess(args=cmd, returncode=0, stdout="nginx\npostgres\nredis\n", stderr="")
        # docker inspect → State.Status=running
        if "docker inspect" in cmd_str and "State.Status" in cmd_str:
            return subprocess.CompletedProcess(args=cmd, returncode=0, stdout="running", stderr="")
        # docker compose up -d → track
        if "compose" in cmd_str and "up" in cmd_str and "-d" in cmd_str:
            compose_up_calls.append(cmd)
            return subprocess.CompletedProcess(args=cmd, returncode=0, stdout="", stderr="")
        return subprocess.CompletedProcess(args=cmd, returncode=0, stdout="", stderr="")

    with patch.object(subprocess, "run", side_effect=mock_run):
        entry = reconciler.reconcile_runtime_state(
            node_yaml_path=node_yaml_with_modules,
            modules_dir=mock_modules_dir,
            dry_run=False,
            report_only=False,
            cooldown_file=str(cooldown_file),
        )

    assert entry["unit"] == "R9"
    assert entry["status"] == "converged"
    assert len(compose_up_calls) == 0, "docker compose up -d should NOT be called for running containers"
    logger.info("[IMP:9][test] R9 running verified: no self-heal invoked")


# endregion FUNC_test_reconcile_runtime_running


# region FUNC_test_reconcile_runtime_exited
## 🧪 TRAP[TEST] · R9 exited → self-heal · Scenario: container exited → self-heal via `docker compose up -d`
## · Regression: R9 self-heal — exited containers trigger compose up -d, NOT docker restart
## · Last fail: never
## · Remove if: reconcile_runtime_state self-heal logic changes
@pytest.mark.usefixtures("reset_state")
@ldd_trajectory
def test_reconcile_runtime_exited(tmp_path, caplog, node_yaml_with_modules, mock_modules_dir):
    """R9: Container exited → self-heal via `docker compose up -d`, NOT `docker restart`."""
    caplog.set_level(logging.INFO)
    logger.info("[IMP:9][test] R9 exited — self-heal via docker compose up -d")

    cooldown_file = tmp_path / ".converge_cooldown.json"

    compose_up_calls = []
    docker_restart_calls = []

    def mock_run(cmd, *args, **kwargs):
        cmd_str = " ".join(cmd) if isinstance(cmd, list) else str(cmd)
        if "docker info" in cmd_str:
            return subprocess.CompletedProcess(args=cmd, returncode=0, stdout="", stderr="")
        if "docker ps" in cmd_str and "--filter" in cmd_str:
            return subprocess.CompletedProcess(args=cmd, returncode=0, stdout="postgres\n", stderr="")
        # Docker inspect → return "exited" (non-running)
        if "docker inspect" in cmd_str and "State.Status" in cmd_str:
            return subprocess.CompletedProcess(args=cmd, returncode=0, stdout="exited", stderr="")
        # docker compose up -d → track
        if "compose" in cmd_str and "up" in cmd_str and "-d" in cmd_str:
            compose_up_calls.append(cmd)
            return subprocess.CompletedProcess(args=cmd, returncode=0, stdout="", stderr="")
        # docker restart → track (should NOT happen)
        if "docker restart" in cmd_str:
            docker_restart_calls.append(cmd)
            return subprocess.CompletedProcess(args=cmd, returncode=0, stdout="", stderr="")
        return subprocess.CompletedProcess(args=cmd, returncode=0, stdout="", stderr="")

    with patch.object(subprocess, "run", side_effect=mock_run):
        entry = reconciler.reconcile_runtime_state(
            node_yaml_path=node_yaml_with_modules,
            modules_dir=mock_modules_dir,
            dry_run=False,
            report_only=False,
            cooldown_file=str(cooldown_file),
        )

    assert entry["unit"] == "R9"
    assert entry["status"] == "mutated"
    assert len(compose_up_calls) > 0, "docker compose up -d should have been called for exited container"
    assert len(docker_restart_calls) == 0, "docker restart should NOT be used — must use compose up -d"
    logger.info("[IMP:9][test] R9 exited verified: self-heal via compose up -d, not docker restart")


# endregion FUNC_test_reconcile_runtime_exited


# region FUNC_test_reconcile_runtime_exited_oneshot_skipped
## 🧪 TRAP[TEST] · 142 B28a · R9 oneshot-guard · Scenario: exited + RestartPolicy=no (init/createbuckets
## · Regression: exited oneshot (platform-minio-createbuckets-1) триггерил self-heal через compose up -d
## ·   БЕЗ env-секретов → «MINIO_ROOT_USER is not set» → heal fail → converge exit 2 на КАЖДОМ прогоне.
## · Last fail: 2026-08-07 (bootstrap 142, converge rc=2, R9 errors=1)
## · Remove if: oneshot-контейнеры будут иметь отличный признак (label/state)
def test_reconcile_runtime_exited_oneshot_skipped(tmp_path, caplog, node_yaml_with_modules, mock_modules_dir):
    """R9: exited + RestartPolicy=no (oneshot) → skip self-heal (не ошибка, не compose up)."""
    caplog.set_level(logging.INFO)
    logger.info("[IMP:9][test] R9 exited-oneshot — skip self-heal (142 B28a)")

    cooldown_file = tmp_path / ".converge_cooldown.json"

    compose_up_calls = []

    def mock_run(cmd, *args, **kwargs):
        cmd_str = " ".join(cmd) if isinstance(cmd, list) else str(cmd)
        if "docker info" in cmd_str:
            return subprocess.CompletedProcess(args=cmd, returncode=0, stdout="", stderr="")
        if "docker ps" in cmd_str and "--filter" in cmd_str:
            return subprocess.CompletedProcess(args=cmd, returncode=0, stdout="postgres\n", stderr="")
        # State.Status → exited; RestartPolicy.Name → "no" (oneshot)
        if "docker inspect" in cmd_str and "RestartPolicy.Name" in cmd_str:
            return subprocess.CompletedProcess(args=cmd, returncode=0, stdout="no", stderr="")
        if "docker inspect" in cmd_str and "State.Status" in cmd_str:
            return subprocess.CompletedProcess(args=cmd, returncode=0, stdout="exited", stderr="")
        if "compose" in cmd_str and "up" in cmd_str and "-d" in cmd_str:
            compose_up_calls.append(cmd)
            return subprocess.CompletedProcess(args=cmd, returncode=0, stdout="", stderr="")
        return subprocess.CompletedProcess(args=cmd, returncode=0, stdout="", stderr="")

    with patch.object(subprocess, "run", side_effect=mock_run):
        entry = reconciler.reconcile_runtime_state(
            node_yaml_path=node_yaml_with_modules,
            modules_dir=mock_modules_dir,
            dry_run=False,
            report_only=False,
            cooldown_file=str(cooldown_file),
        )

    assert entry["unit"] == "R9"
    assert entry["status"] == "converged", f"oneshot exited не должен требовать heal: {entry}"
    assert len(compose_up_calls) == 0, "compose up -d НЕ должен вызываться для exited-oneshot"
    assert "oneshot" in caplog.text or "skip self-heal" in caplog.text
    logger.info("[IMP:9][test] R9 exited-oneshot verified: skip self-heal, no compose up")


# endregion FUNC_test_reconcile_runtime_exited_oneshot_skipped


# region FUNC_test_reconcile_runtime_cooldown
## 🧪 TRAP[TEST] · R9 cooldown · Scenario: same container self-healed recently → skip (cooldown)
## · Regression: R9 cooldown — skip self-heal if same container was healed in last 3 converge runs
## · Last fail: never
## · Remove if: reconcile_runtime_state cooldown logic changes
@pytest.mark.usefixtures("reset_state")
@ldd_trajectory
def test_reconcile_runtime_cooldown(tmp_path, caplog, node_yaml_with_modules, mock_modules_dir):
    """R9: Container self-healed recently → cooldown skip, no compose up -d."""
    caplog.set_level(logging.INFO)
    logger.info("[IMP:9][test] R9 cooldown — previously self-healed container skipped")

    # Set up cooldown file WITH a recent cooldown entry for "postgres"
    cooldown_file = tmp_path / ".converge_cooldown.json"
    # Write cooldown state: postgres healed 1 run ago (within cooldown window of 3)
    cooldown_data = {"containers": {"postgres": {"last_healed_run": 5}}}
    cooldown_file.write_text(json.dumps(cooldown_data))

    compose_up_calls = []

    # Use a counter to simulate converge run tracking
    # Actually, run 4 - run 5 = -1 which is < 3. So cooldown triggers. Let me fix: set current to 6.
    # Current run: 6, last heal: 5, diff = 1 < 3 → cooldown skip

    def mock_run(cmd, *args, **kwargs):
        cmd_str = " ".join(cmd) if isinstance(cmd, list) else str(cmd)
        if "docker info" in cmd_str:
            return subprocess.CompletedProcess(args=cmd, returncode=0, stdout="", stderr="")
        if "docker ps" in cmd_str and "--filter" in cmd_str:
            return subprocess.CompletedProcess(args=cmd, returncode=0, stdout="postgres\nnginx\n", stderr="")
        # nginx is running → ok, postgres is exited
        if "docker inspect" in cmd_str and "State.Status" in cmd_str:
            # nginx running, postgres exited
            return subprocess.CompletedProcess(args=cmd, returncode=0, stdout="exited", stderr="")
        if "compose" in cmd_str and "up" in cmd_str and "-d" in cmd_str:
            compose_up_calls.append(cmd)
            return subprocess.CompletedProcess(args=cmd, returncode=0, stdout="", stderr="")
        return subprocess.CompletedProcess(args=cmd, returncode=0, stdout="", stderr="")

    with patch.object(subprocess, "run", side_effect=mock_run):
        entry = reconciler.reconcile_runtime_state(
            node_yaml_path=node_yaml_with_modules,
            modules_dir=mock_modules_dir,
            dry_run=False,
            report_only=False,
            cooldown_file=str(cooldown_file),
        )

    assert entry["unit"] == "R9"
    # The cooldown should cause status to be "converged" or "warn", not "mutated"
    assert entry["status"] != "mutated", "Cooldown should prevent self-heal (no mutation)"
    assert len(compose_up_calls) == 0, "docker compose up -d should NOT be called during cooldown"
    logger.info("[IMP:9][test] R9 cooldown verified: self-heal skipped within cooldown window")


# endregion FUNC_test_reconcile_runtime_cooldown


# ═══════════════════════════════════════════════════════════════════
# REF-0014 — label-детекция проекта + канонический compose-argv (build_compose_args)
# ═══════════════════════════════════════════════════════════════════


# region REF0014_R9_LABEL_AND_ARGV


def _single_module_env(tmp_path, mod: str = "postgres"):
    """node.yaml с одним docker-модулем + modules dir с его compose-файлом (точный argv-ассерт)."""
    yaml_path = tmp_path / "node.yaml"
    yaml_path.write_text(
        f"context: test-context\nmodules:\n  - name: {mod}\n    enabled: true\nprojects: []\n",
        encoding="utf-8",
    )
    mod_dir = tmp_path / "modules" / mod
    mod_dir.mkdir(parents=True)
    compose = mod_dir / "docker-compose.yml"
    compose.write_text(f"version: '3'\nservices:\n  {mod}:\n    image: {mod}:latest\n", encoding="utf-8")
    return str(yaml_path), str(tmp_path / "modules"), compose


def _make_mock_run(compose_up_calls: list | None = None, ps_cmds: list | None = None):
    """mock subprocess.run: info ok; ps → контейнер проекта; inspect → exited/unless-stopped; up captured."""

    def mock_run(cmd, *args, **kwargs):
        cmd_str = " ".join(cmd) if isinstance(cmd, list) else str(cmd)
        if "docker info" in cmd_str:
            return subprocess.CompletedProcess(args=cmd, returncode=0, stdout="", stderr="")
        if "docker ps" in cmd_str:
            if ps_cmds is not None:
                ps_cmds.append(list(cmd))
            return subprocess.CompletedProcess(args=cmd, returncode=0, stdout="pg-main\n", stderr="")
        if "docker inspect" in cmd_str and "RestartPolicy.Name" in cmd_str:
            return subprocess.CompletedProcess(args=cmd, returncode=0, stdout="unless-stopped", stderr="")
        if "docker inspect" in cmd_str and "State.Status" in cmd_str:
            return subprocess.CompletedProcess(args=cmd, returncode=0, stdout="exited", stderr="")
        if "compose" in cmd_str and "up" in cmd_str and "-d" in cmd_str:
            if compose_up_calls is not None:
                compose_up_calls.append(list(cmd))
            return subprocess.CompletedProcess(args=cmd, returncode=0, stdout="", stderr="")
        return subprocess.CompletedProcess(args=cmd, returncode=0, stdout="", stderr="")

    return mock_run


# 🧪 TRAP[TEST] · REF-0014 · Regression · Scenario: детекция контейнеров модуля по
# ·   label=com.docker.compose.project=<module>, substring-фильтров name= НЕТ
# · Last fail: 2026-08-24 (BUG-0701) — name=monitoring → 0 рядов; name=redis матчил langfuse-redis
# · Remove if: механизм детекции проекта изменится
@pytest.mark.usefixtures("reset_state")
@ldd_trajectory
def test_r9_detects_module_by_compose_project_label(tmp_path, caplog, node_yaml_with_modules, mock_modules_dir):
    """REF-0014: docker ps --filter label=com.docker.compose.project=<module> для каждого модуля."""
    caplog.set_level(logging.INFO)
    logger.info("[IMP:9][test] R9 label-detection — compose project label вместо substring name=")

    cooldown_file = tmp_path / ".converge_cooldown.json"
    ps_cmds: list[list[str]] = []
    compose_up_calls: list[list[str]] = []

    with patch.object(subprocess, "run", side_effect=_make_mock_run(compose_up_calls, ps_cmds)):
        entry = reconciler.reconcile_runtime_state(
            node_yaml_path=node_yaml_with_modules,
            modules_dir=mock_modules_dir,
            dry_run=False,
            report_only=False,
            cooldown_file=str(cooldown_file),
        )

    assert entry["unit"] == "R9"
    assert entry["status"] == "mutated", "exited-контейнеры должны быть детектированы и вылечены"
    assert ps_cmds, "docker ps обязан вызываться для каждого docker-модуля"
    queried: set[str] = set()
    for cmd in ps_cmds:
        filters = [cmd[i + 1] for i, f in enumerate(cmd) if f == "--filter"]
        assert filters, f"docker ps без --filter: {cmd}"
        for flt in filters:
            assert flt.startswith("label=com.docker.compose.project="), (
                f"REF-0014 FAIL: substring/name-детекция вернулась: {flt}"
            )
            queried.add(flt.removeprefix("label=com.docker.compose.project="))
    assert {"nginx", "postgres", "redis"} <= queried, f"не все модули опрошены по label: {queried}"
    assert compose_up_calls, "детекция должна работать end-to-end (heal выполнен)"
    logger.info("[IMP:9][test] R9 label-detection verified: %d ps-вызовов, heal выполнен", len(ps_cmds))


# 🧪 TRAP[TEST] · REF-0014 · Regression · Scenario: R9 argv через канонический build_compose_args —
# ·   root-compose ПЕРВЫМ и ЕДИНСТВЕННЫМ -f, затем secrets env-file, platform .env, --profile
# · Last fail: 2026-08-24 (BUG-0701) — ручной argv ['-f', base.yml] без env/profile ломал каждый
# ·   docker-модуль (undefined volume / missing ${VAR:?}, 3 режима отказа живьём)
# · Remove if: R9 перестанет строить argv через bootstrap/deploy/compose_args.build_compose_args
@pytest.mark.usefixtures("reset_state")
@ldd_trajectory
def test_r9_compose_argv_root_first_env_profile(tmp_path, caplog, monkeypatch):
    """REF-0014: точный порядок argv — [-f root, --env-file secrets, --env-file platform.env, --profile]."""
    caplog.set_level(logging.INFO)
    logger.info("[IMP:9][test] R9 canonical argv — root-first/env-file/profile")

    node_yaml, modules_dir, _compose = _single_module_env(tmp_path)
    cooldown_file = tmp_path / ".converge_cooldown.json"

    platform_root = tmp_path / "platform"
    platform_root.mkdir()
    (platform_root / "docker-compose.yml").write_text("services: {}\n", encoding="utf-8")
    (platform_root / ".env").write_text("PLATFORM_VAR=1\n", encoding="utf-8")
    secrets_env = tmp_path / "secrets.env"
    secrets_env.write_text("SECRET=1\n", encoding="utf-8")
    monkeypatch.setenv("PLATFORM_REMOTE_BASE", str(platform_root))
    monkeypatch.setenv("SECRETS_ENV_FILE", str(secrets_env))

    compose_up_calls: list[list[str]] = []
    with patch.object(subprocess, "run", side_effect=_make_mock_run(compose_up_calls)):
        entry = reconciler.reconcile_runtime_state(
            node_yaml_path=node_yaml,
            modules_dir=modules_dir,
            dry_run=False,
            report_only=False,
            cooldown_file=str(cooldown_file),
        )

    assert entry["unit"] == "R9"
    assert entry["status"] == "mutated", "exited → self-heal"
    assert len(compose_up_calls) == 1, f"ровно один compose up: {compose_up_calls}"
    expected = [
        "docker",
        "compose",
        "-f",
        str(platform_root / "docker-compose.yml"),
        "--env-file",
        str(secrets_env),
        "--env-file",
        str(platform_root / ".env"),
        "--profile",
        "postgres",
        "up",
        "-d",
    ]
    assert compose_up_calls[0] == expected, (
        f"REF-0014 FAIL: argv не канонический (root-first/env/profile порядок):\n{compose_up_calls[0]}"
    )
    logger.info("[IMP:9][test] R9 canonical argv verified: root-first + env-files + profile")


# 🧪 TRAP[TEST] · REF-0014 · Edge-case · Scenario: root compose отсутствует → fallback на модульный
# ·   файл (U-49 ветка else build_compose_args), env-files и --profile сохраняются
# · Last fail: N/A (fallback-ветка канона U-49; раньше была единственной — и сломанной — веткой R9)
# · Remove if: fallback-семантика build_compose_args изменится
@pytest.mark.usefixtures("reset_state")
@ldd_trajectory
def test_r9_compose_argv_module_fallback_without_root(tmp_path, caplog, monkeypatch):
    """Без root compose: единственный -f = модульный файл; env/profile на месте."""
    caplog.set_level(logging.INFO)
    logger.info("[IMP:9][test] R9 fallback argv — module compose without root")

    node_yaml, modules_dir, compose = _single_module_env(tmp_path)
    cooldown_file = tmp_path / ".converge_cooldown.json"

    empty_root = tmp_path / "empty-root"
    empty_root.mkdir()
    (empty_root / ".env").write_text("PLATFORM_VAR=1\n", encoding="utf-8")
    secrets_env = tmp_path / "secrets.env"
    secrets_env.write_text("SECRET=1\n", encoding="utf-8")
    monkeypatch.setenv("PLATFORM_REMOTE_BASE", str(empty_root))
    monkeypatch.setenv("SECRETS_ENV_FILE", str(secrets_env))

    compose_up_calls: list[list[str]] = []
    with patch.object(subprocess, "run", side_effect=_make_mock_run(compose_up_calls)):
        entry = reconciler.reconcile_runtime_state(
            node_yaml_path=node_yaml,
            modules_dir=modules_dir,
            dry_run=False,
            report_only=False,
            cooldown_file=str(cooldown_file),
        )

    assert entry["unit"] == "R9"
    assert entry["status"] == "mutated"
    expected = [
        "docker",
        "compose",
        "-f",
        str(compose),
        "--env-file",
        str(secrets_env),
        "--env-file",
        str(empty_root / ".env"),
        "--profile",
        "postgres",
        "up",
        "-d",
    ]
    assert compose_up_calls == [expected], f"REF-0014 FAIL: fallback argv не канонический: {compose_up_calls}"
    logger.info("[IMP:9][test] R9 fallback argv verified: module -f + env + profile")


# endregion REF0014_R9_LABEL_AND_ARGV
