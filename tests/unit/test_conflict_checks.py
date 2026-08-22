"""
# GREP_SUMMARY: test-conflict-checks fqdn ports uniqueness validate deploy-block python-port
# STRUCTURE: ⚡ tmp_path → write ai-platform.yaml projects → call check_fqdn_conflict / check_port_conflict → assert ok/conflict
# region MODULE_CONTRACT
## @purpose  Unit tests for core/internal/validate/conflict_checks.py (Strangler-порт validate.sh fqdn/ports)
## @scope    FQDN uniqueness (E1), host_port uniqueness (E2), falsey-domain guard (TRAP[BUG] regression)
## @invariants
##   - tmp_path isolated projects base — zero hardcoded paths
##   - Direct function calls (native pytest, no subprocess)
##   - LDD trajectory printed before assertions
## @changes  2026-07-31 | Created (debt S-1 validate.sh Strangler)
# endregion MODULE_CONTRACT
"""

import logging
from pathlib import Path

import pytest

from core.internal.validate.conflict_checks import (
    _extract_domain,
    check_fqdn_conflict,
    check_port_conflict,
)
from tests.helpers.gate_helpers import assert_ldd_imp9

pytestmark = pytest.mark.static_audit

logger = logging.getLogger("test_conflict_checks")


def _write_project(base: Path, name: str, domain: str = "", host_port: int = 0) -> Path:
    """Create a project dir with ai-platform.yaml; return the project dir."""
    project_dir = base / name
    project_dir.mkdir(parents=True, exist_ok=True)
    lines = ["expose: true", "target_node: mynode"]
    if domain:
        lines.append(f"needs:\n  domain: {domain}")
    if host_port:
        lines.append(f"monitoring:\n  host_port: {host_port}")
    (project_dir / "ai-platform.yaml").write_text("\n".join(lines) + "\n")
    return project_dir


def test_fqdn_unique(tmp_path: Path, caplog) -> None:
    """Unique domain across projects → ok=True."""
    caplog.set_level(logging.INFO)
    base = tmp_path / "projects"
    project_dir = _write_project(base, "myapp", domain="myapp.example.com")
    _write_project(base, "other", domain="other.example.com")

    ok, msg = check_fqdn_conflict(str(project_dir), str(base))
    assert_ldd_imp9(caplog, require_imp9=False)

    assert ok is True, f"Expected no conflict, got: {msg}"
    logger.critical("[IMP:9][test] fqdn_unique: ok=%s — OK", ok)


def test_fqdn_conflict_detected(tmp_path: Path, caplog) -> None:
    """Second project claiming same FQDN → ok=False with E1 message (deploy blocked)."""
    caplog.set_level(logging.INFO)
    base = tmp_path / "projects"
    _write_project(base, "first", domain="dup.example.com")
    project_dir = _write_project(base, "second", domain="dup.example.com")

    ok, msg = check_fqdn_conflict(str(project_dir), str(base))
    assert_ldd_imp9(caplog, require_imp9=False)

    assert ok is False, "Expected E1 conflict"
    assert "E1" in msg and "dup.example.com" in msg
    logger.critical("[IMP:9][test] fqdn_conflict: ok=%s msg=%s — OK", ok, msg)


def test_fqdn_falsey_domain_skipped(tmp_path: Path, caplog) -> None:
    """needs.domain: false не является доменом → skip (TRAP[BUG] false-positive regression)."""
    caplog.set_level(logging.INFO)
    base = tmp_path / "projects"
    project_dir = base / "myapp"
    project_dir.mkdir(parents=True)
    (project_dir / "ai-platform.yaml").write_text("expose: true\nneeds:\n  domain: false\ntarget_node: mynode\n")

    ok, msg = check_fqdn_conflict(str(project_dir), str(base))
    assert_ldd_imp9(caplog, require_imp9=False)

    assert ok is True and "skipping" in msg.lower(), f"Falsey domain должен пропускаться: {msg}"
    logger.critical("[IMP:9][test] fqdn_falsey: ok=%s — OK", ok)


def test_fqdn_missing_base_skipped(tmp_path: Path, caplog) -> None:
    """PROJECTS_BASE недоступен → skip (не блокировать deploy)."""
    caplog.set_level(logging.INFO)
    base = tmp_path / "projects"
    project_dir = _write_project(base, "myapp", domain="myapp.example.com")

    ok, msg = check_fqdn_conflict(str(project_dir), str(tmp_path / "nonexistent"))
    assert_ldd_imp9(caplog, require_imp9=False)

    assert ok is True and "not available" in msg
    logger.critical("[IMP:9][test] fqdn_no_base: ok=%s — OK", ok)


def test_port_conflict_detected(tmp_path: Path, caplog) -> None:
    """Два проекта с одинаковым monitoring.host_port → ok=False."""
    caplog.set_level(logging.INFO)
    base = tmp_path / "projects"
    _write_project(base, "app-a", host_port=8080)
    _write_project(base, "app-b", host_port=8080)

    ok, msg = check_port_conflict(str(base))
    assert_ldd_imp9(caplog, require_imp9=False)

    assert ok is False and "8080" in msg
    logger.critical("[IMP:9][test] port_conflict: ok=%s msg=%s — OK", ok, msg)


def test_port_unique(tmp_path: Path, caplog) -> None:
    """Разные порты → ok=True."""
    caplog.set_level(logging.INFO)
    base = tmp_path / "projects"
    _write_project(base, "app-a", host_port=8080)
    _write_project(base, "app-b", host_port=8081)

    ok, _msg = check_port_conflict(str(base))
    assert_ldd_imp9(caplog, require_imp9=False)

    assert ok is True
    logger.critical("[IMP:9][test] port_unique: ok=%s — OK", ok)


def test_extract_domain_needs_and_top_level(tmp_path: Path) -> None:
    """needs.domain приоритетнее top-level domain."""
    base = tmp_path / "projects"
    d1 = _write_project(base, "needs-proj", domain="needs.example.com")
    assert _extract_domain(d1 / "ai-platform.yaml") == "needs.example.com"

    top = base / "top-proj"
    top.mkdir(parents=True)
    (top / "ai-platform.yaml").write_text("expose: true\ndomain: top.example.com\n")
    assert _extract_domain(top / "ai-platform.yaml") == "top.example.com"
