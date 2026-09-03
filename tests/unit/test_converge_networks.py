"""
# GREP_SUMMARY: test-converge-networks, r4, reconcile-networks, proxy-net, docker-network, mock-docker
# STRUCTURE: ▶ tmp_path + monkeypatch + mock subprocess → ◇ R4 reconcile_networks 3× (no-docker/proxy-net-missing/proxy-net-exists) → ⎋ verdict
# region MODULE_CONTRACT
## @purpose  Unit tests for converge/networks.py via reconciler.reconcile_networks (R4).
## @scope    Tests proxy-net reconciliation: docker daemon availability, network create,
##           network-exists skip. Uses mock subprocess.run for docker commands.
##           Does NOT require a real docker daemon.
## @invariants
##   - All docker-dependent tests mock subprocess.run to avoid real docker calls
##   - File operations use tmp_path exclusively
##   - Each test validates IMP:9 business logic log presence via caplog
## @rationale Direct function testing with mock subprocess.run for docker-dependent units.
##   Вынесен из монолита test_reconciler.py (DevPlan 118 F6).
## @changes 2026-08-02 · F6 split — R4 networks (DevPlan 118)
## @changes 2026-09-03 · live-drill (prod) — +deployed-проект с 0 контейнеров → WARN (R3-consistent
##           deployed-детекция: real vs GENERATED-STUB ai-platform.yaml); stub → NO warn
# endregion MODULE_CONTRACT
"""

import json
import logging
import subprocess
import sys
from pathlib import Path
from unittest.mock import patch

import pytest

# Load the LDD trajectory decorator from shared conftest
from tests._conftest.ldd import ldd_trajectory

logger = logging.getLogger(__name__)

# ── Import the module under test ──
_MODULE_DIR = Path(__file__).resolve().parent.parent.parent / "core" / "internal" / "bootstrap" / "converge"
sys.path.insert(0, str(_MODULE_DIR))
import reconciler

import core.internal.bootstrap.converge.networks as _converge_networks
from core.internal.bootstrap.converge import infra

# Re-export for fixture cleanups
MODULE = reconciler


# ═══════════════════════════════════════════════════════════════════
# region Fixtures


@pytest.fixture
def reset_state():
    """Reset reconciler module state before each test."""
    infra.reset_state()
    infra.node_name = "test-node"
    infra.core_dir = str(Path(__file__).resolve().parent.parent.parent / "core")
    yield


# endregion Fixtures


# region FUNC_test_reconcile_networks_no_docker
## 🧪 TRAP[TEST] · R4 no docker · Scenario: docker daemon unavailable → fail
## · Regression: converge.sh lines 699-704
## · Last fail: never
## · Remove if: reconciler.R4 docker check logic changes
@pytest.mark.usefixtures("reset_state")
@ldd_trajectory
def test_reconcile_networks_no_docker(tmp_path, caplog):
    """R4: Docker daemon not available → status=fail."""
    caplog.set_level(logging.INFO)

    # Mock subprocess.run to return failure for docker info
    def mock_run_no_docker(cmd, *args, **kwargs):
        cmd_str = " ".join(cmd) if isinstance(cmd, list) else str(cmd)
        if "docker info" in cmd_str:
            return subprocess.CompletedProcess(
                args=cmd, returncode=1, stdout="", stderr="Cannot connect to the Docker daemon"
            )
        return subprocess.CompletedProcess(args=cmd, returncode=0, stdout="", stderr="")

    with patch.object(subprocess, "run", side_effect=mock_run_no_docker):
        entry = reconciler.reconcile_networks(str(tmp_path), dry_run=False, report_only=False)

    assert entry["unit"] == "R4"
    assert entry["status"] == "fail"
    assert "not available" in entry["detail"]


# endregion FUNC_test_reconcile_networks_no_docker


# region FUNC_test_reconcile_networks_create_proxy_net
## 🧪 TRAP[TEST] · R4 create proxy-net · Scenario: proxy-net missing → created
## · Regression: converge.sh lines 707-719
## · Last fail: 2026-07-31 — IsADirectoryError: tmp_path dir passed as node.yaml to NodeYaml
## · Remove if: reconciler.R4 network create logic changes
@pytest.mark.usefixtures("reset_state")
@ldd_trajectory
def test_reconcile_networks_create_proxy_net(tmp_path, caplog):
    """R4: proxy-net missing → docker network create called."""
    caplog.set_level(logging.INFO)

    # ⚠️ TRAP[BUG] · 2026-07-31 · P1 · IsADirectoryError in _check_proxy_connectivity
    # · Symptom: reconcile_networks(str(tmp_path)) → NodeYaml(dir).get_list() → IsADirectoryError
    # · Root: _check_proxy_connectivity parses node.yaml via NodeYaml; a directory is not a file
    # · Fix: fixture writes a real node.yaml file; pass its path, not the tmp_path dir
    yaml_path = tmp_path / "node.yaml"
    yaml_path.write_text(
        "contexts:\n  - name: test-context\nprojects:\n  - name: myapp\n    domain: myapp.example.com\n"
    )

    create_called = []

    def mock_run(cmd, *args, **kwargs):
        cmd_str = " ".join(cmd) if isinstance(cmd, list) else str(cmd)
        # docker info → success
        if "docker info" in cmd_str:
            return subprocess.CompletedProcess(args=cmd, returncode=0, stdout="", stderr="")
        # docker network inspect proxy-net → not found
        if "network inspect" in cmd_str:
            return subprocess.CompletedProcess(args=cmd, returncode=1, stdout="", stderr="not found")
        # docker network create → track call
        if "network create" in cmd_str:
            create_called.append(cmd)
            return subprocess.CompletedProcess(args=cmd, returncode=0, stdout="proxy-net\n", stderr="")
        # docker ps → empty (no containers to check)
        if "docker ps" in cmd_str:
            return subprocess.CompletedProcess(args=cmd, returncode=0, stdout="", stderr="")
        return subprocess.CompletedProcess(args=cmd, returncode=0, stdout="", stderr="")

    with patch.object(subprocess, "run", side_effect=mock_run):
        entry = reconciler.reconcile_networks(str(yaml_path), dry_run=False, report_only=False)

    assert entry["unit"] == "R4"
    assert infra.has_warnings or not infra.has_errors
    assert len(create_called) > 0, "docker network create should have been called"
    assert _converge_networks.PROXY_NET in " ".join(create_called[0])


# endregion FUNC_test_reconcile_networks_create_proxy_net


# region FUNC_test_reconcile_networks_exists
## 🧪 TRAP[TEST] · R4 proxy-net exists · Scenario: proxy-net already exists → SKIP
## · Regression: converge.sh lines 720-731
## · Last fail: 2026-07-31 — IsADirectoryError: tmp_path dir passed as node.yaml to NodeYaml
## · Remove if: reconciler.R4 network check logic changes
@pytest.mark.usefixtures("reset_state")
@ldd_trajectory
def test_reconcile_networks_exists(tmp_path, caplog):
    """R4: proxy-net already exists (bridge) → no create."""
    caplog.set_level(logging.INFO)

    # ⚠️ TRAP[BUG] · 2026-07-31 · P1 · IsADirectoryError in _check_proxy_connectivity
    # · Symptom: reconcile_networks(str(tmp_path)) → NodeYaml(dir).get_list() → IsADirectoryError
    # · Root: _check_proxy_connectivity parses node.yaml via NodeYaml; a directory is not a file
    # · Fix: fixture writes a real node.yaml file; pass its path, not the tmp_path dir
    yaml_path = tmp_path / "node.yaml"
    yaml_path.write_text(
        "contexts:\n  - name: test-context\nprojects:\n  - name: myapp\n    domain: myapp.example.com\n"
    )

    create_called = []

    def mock_run(cmd, *args, **kwargs):
        cmd_str = " ".join(cmd) if isinstance(cmd, list) else str(cmd)
        if "docker info" in cmd_str:
            return subprocess.CompletedProcess(args=cmd, returncode=0, stdout="", stderr="")
        if "network inspect" in cmd_str:
            # Return valid JSON with bridge driver
            inspect_json = json.dumps([{"Name": "proxy-net", "Driver": "bridge"}])
            return subprocess.CompletedProcess(args=cmd, returncode=0, stdout=inspect_json, stderr="")
        if "network create" in cmd_str:
            create_called.append(cmd)
            return subprocess.CompletedProcess(args=cmd, returncode=0, stdout="", stderr="")
        if "docker ps" in cmd_str:
            return subprocess.CompletedProcess(args=cmd, returncode=0, stdout="", stderr="")
        return subprocess.CompletedProcess(args=cmd, returncode=0, stdout="", stderr="")

    with patch.object(subprocess, "run", side_effect=mock_run):
        entry = reconciler.reconcile_networks(str(yaml_path), dry_run=False, report_only=False)

    assert entry["unit"] == "R4"
    assert len(create_called) == 0, "docker network create should NOT have been called"


# endregion FUNC_test_reconcile_networks_exists


# ═══════════════════════════════════════════════════════════════════
# region R4-honesty (live-drill 2026-09-03): deployed-проект с 0 running-контейнеров
# ═══════════════════════════════════════════════════════════════════
# Live: docker rm контейнера проекта → converge "FULLY CONVERGED" (exit 0), контейнер
# отсутствует. Deployed-детекция — ТОТ ЖЕ источник, что R3: ai-platform.yaml real-vs-STUB.


def _r4_networks_mock_run(cmd, *args, **kwargs):
    """subprocess.run mock: docker info OK, proxy-net exists (bridge), docker ps empty."""
    cmd_str = " ".join(cmd) if isinstance(cmd, list) else str(cmd)
    if "docker info" in cmd_str:
        return subprocess.CompletedProcess(args=cmd, returncode=0, stdout="", stderr="")
    if "network inspect" in cmd_str:
        inspect_json = json.dumps([{"Name": "proxy-net", "Driver": "bridge"}])
        return subprocess.CompletedProcess(args=cmd, returncode=0, stdout=inspect_json, stderr="")
    if "docker ps" in cmd_str:
        return subprocess.CompletedProcess(args=cmd, returncode=0, stdout="", stderr="")
    return subprocess.CompletedProcess(args=cmd, returncode=0, stdout="", stderr="")


def _write_project_yaml(tmp_path, *names):
    """Write node.yaml with given project names."""
    yaml_path = tmp_path / "node.yaml"
    body = "contexts:\n  - name: test-context\nprojects:\n"
    for n in names:
        body += f"  - name: {n}\n    domain: {n}.example.com\n"
    yaml_path.write_text(body, encoding="utf-8")
    return yaml_path


# 🧪 TRAP[TEST] · REGRESSION · R4-honesty live-drill 2026-09-03 — deployed + 0 контейнеров → WARN
# · Scenario: node.yaml проект myapp DEPLOYED (real ai-platform.yaml, не GENERATED-STUB),
#   docker ps → 0 running-контейнеров → R4 ОБЯЗАН добавить warn-drift с точным текстом
#   "deployed but no running containers"; exit-код НЕ меняется (reconcile контейнеров —
#   deploy-project канал по дизайну)
# · Last fail: live prod — docker rm контейнера → converge "FULLY CONVERGED" exit 0
# · Remove if: warn-детекция перенесена в deploy-project/R9 (иной канал поверх R4)
@pytest.mark.usefixtures("reset_state")
@ldd_trajectory
def test_proxy_connectivity_warns_deployed_project_no_containers(tmp_path, caplog, monkeypatch):
    """R4-honesty: deployed-проект с 0 running-контейнеров → warn-drift, exit-код не меняется."""
    caplog.set_level(logging.INFO)
    yaml_path = _write_project_yaml(tmp_path, "myapp")
    projects_dir = tmp_path / "projects"
    app_dir = projects_dir / "myapp"
    app_dir.mkdir(parents=True)
    (app_dir / "ai-platform.yaml").write_text("project: myapp\nservice: myapp\n", encoding="utf-8")
    monkeypatch.setattr(_converge_networks, "PROJECTS_BASE", str(projects_dir))

    with patch.object(subprocess, "run", side_effect=_r4_networks_mock_run):
        entry = reconciler.reconcile_networks(str(yaml_path), dry_run=False, report_only=False)

    assert entry["unit"] == "R4"
    warns = [d for d in infra.drifts if d["status"] == "warn"]
    assert len(warns) == 1, f"ожидался ровно 1 warn-drift для deployed-проекта, got: {infra.drifts}"
    assert "deployed but no running containers" in warns[0]["detail"], (
        f"warn-текст не точен (R5 anti-survivorship wording): {warns[0]['detail']}"
    )
    assert "myapp" in warns[0]["detail"]
    # warn НЕ эскалирует exit: converge остаётся 0 (контейнер-reconcile — канал deploy-project)
    assert infra.exit_code == 0, f"warn-drift изменил exit-код: {infra.exit_code}"
    assert not infra.has_errors and not infra.has_warnings
    logger.info("[IMP:9][test][r4-honesty] deployed + 0 контейнеров → warn (exit 0) — PASS")


# 🧪 TRAP[TEST] · NEGATIVE (R5) · R4-honesty live-drill 2026-09-03 — stub/awaiting НЕ warn
# · Scenario: проект myapp ожидает деплой (ai-platform.yaml = GENERATED-STUB), docker ps → 0
#   контейнеров → R4 НЕ должен warn (deployed-гейт R3: stub ≠ deployed; false-warn ожидающих
#   CI-deploy проектов исключён)
# · Last fail: исходный live-drill вход (0 контейнеров) — доказывает, что warn не blanket
# · Remove if: deployed-детекция R4 заменена на иной источник классификации
@pytest.mark.usefixtures("reset_state")
@ldd_trajectory
def test_proxy_connectivity_no_warn_for_stub_project_no_containers(tmp_path, caplog, monkeypatch):
    """R4-honesty negative: stub/awaiting-проект с 0 контейнеров → NO warn (deployed-гейт)."""
    caplog.set_level(logging.INFO)
    yaml_path = _write_project_yaml(tmp_path, "myapp")
    projects_dir = tmp_path / "projects"
    app_dir = projects_dir / "myapp"
    app_dir.mkdir(parents=True)
    (app_dir / "ai-platform.yaml").write_text(
        "# GENERATED-STUB by converge — overwritten by CI deliver\nproject: myapp\n", encoding="utf-8"
    )
    monkeypatch.setattr(_converge_networks, "PROJECTS_BASE", str(projects_dir))

    with patch.object(subprocess, "run", side_effect=_r4_networks_mock_run):
        entry = reconciler.reconcile_networks(str(yaml_path), dry_run=False, report_only=False)

    assert entry["unit"] == "R4"
    warns = [d for d in infra.drifts if d["status"] == "warn"]
    assert warns == [], f"stub/awaiting-проект дал ложный warn: {infra.drifts}"
    assert infra.exit_code == 0
    logger.info("[IMP:9][test][r4-honesty] stub + 0 контейнеров → NO warn (deployed-гейт) — PASS")


# 🧪 TRAP[TEST] · NEGATIVE (R5) · R4-honesty live-drill 2026-09-03 — running-контейнер на
# proxy-net → NO warn (поведение не изменено)
# · Scenario: deployed-проект myapp с running-контейнером myapp-1, подключённым к proxy-net →
#   R4 не warn (0-контейнерная ветка не затронута)
# · Last fail: исходный live-drill вход (deployed-проект) — доказывает, что warn ТОЛЬКО для 0
# · Remove if: R4 connectivity-проверка заменена
@pytest.mark.usefixtures("reset_state")
@ldd_trajectory
def test_proxy_connectivity_no_warn_for_running_container_on_proxy_net(tmp_path, caplog, monkeypatch):
    """R4-honesty negative: running-контейнер на proxy-net → NO warn, поведение неизменно."""
    caplog.set_level(logging.INFO)
    yaml_path = _write_project_yaml(tmp_path, "myapp")
    projects_dir = tmp_path / "projects"
    app_dir = projects_dir / "myapp"
    app_dir.mkdir(parents=True)
    (app_dir / "ai-platform.yaml").write_text("project: myapp\nservice: myapp\n", encoding="utf-8")
    monkeypatch.setattr(_converge_networks, "PROJECTS_BASE", str(projects_dir))

    def mock_run(cmd, *args, **kwargs):
        cmd_str = " ".join(cmd) if isinstance(cmd, list) else str(cmd)
        if "docker info" in cmd_str:
            return subprocess.CompletedProcess(args=cmd, returncode=0, stdout="", stderr="")
        if "network inspect" in cmd_str:
            inspect_json = json.dumps([{"Name": "proxy-net", "Driver": "bridge"}])
            return subprocess.CompletedProcess(args=cmd, returncode=0, stdout=inspect_json, stderr="")
        if "docker ps" in cmd_str:
            return subprocess.CompletedProcess(args=cmd, returncode=0, stdout="myapp-1\n", stderr="")
        if "docker inspect" in cmd_str:
            return subprocess.CompletedProcess(args=cmd, returncode=0, stdout="proxy-net \n", stderr="")
        return subprocess.CompletedProcess(args=cmd, returncode=0, stdout="", stderr="")

    with patch.object(subprocess, "run", side_effect=mock_run):
        entry = reconciler.reconcile_networks(str(yaml_path), dry_run=False, report_only=False)

    assert entry["unit"] == "R4"
    warns = [d for d in infra.drifts if d["status"] == "warn"]
    assert warns == [], f"running-контейнер на proxy-net дал ложный warn: {infra.drifts}"
    assert infra.exit_code == 0
    logger.info("[IMP:9][test][r4-honesty] running-контейнер на proxy-net → NO warn — PASS")


# endregion R4-honesty (live-drill 2026-09-03)
