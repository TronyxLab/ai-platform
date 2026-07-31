# GREP_SUMMARY: test project_scaffolder new-project scaffold template copy render git-init checklist FQDN vhost register dry-run auto-domain
# STRUCTURE: ┌tmp_path fixtures┐ → ○ 9 tests → ⊕ LDD trajectory (IMP:9) → ⚡ anti-loop counter
# region MODULE_CONTRACT
## @purpose  Unit-тесты project_scaffolder.py: backend scaffold, frontend scaffold, конфликт,
##           missing template, dry-run (no mutation), auto_domain, checklist generation.
##           LDD IMP:9 + Anti-Loop + R1-R5.
## @scope    Tests under tests/ (unit, no Docker). DI over Mocks для subprocess.
## @invariants  Все тесты используют tmp_path (R1). R1-R5 compliance.
## @rationale AC4: 8 unit-тестов на project_scaffolder.py согласно DevPlan 092 §4.
## @changes 2026-07-31 · DevPlan 092 AC4 — initial implementation
# endregion MODULE_CONTRACT

from __future__ import annotations

import logging
import pathlib

import yaml

from tests.conftest import ldd_trajectory

logger = logging.getLogger(__name__)

from core.internal.scaffold.project_scaffolder import (
    auto_domain,
    copy_template,
    generate_checklist,
)

# ── auto_domain tests ─────────────────────────────────────────────────────


@ldd_trajectory
def test_auto_domain_with_env(tmp_path: pathlib.Path, monkeypatch, caplog) -> None:
    monkeypatch.setenv("PLATFORM_DOMAIN", "tronyx.ru")
    logger.info("[IMP:9][test][scaffolder] test_auto_domain_with_env")
    result = auto_domain(name="myapp", domain="")
    assert result == "myapp.tronyx.ru", f"Expected 'myapp.tronyx.ru', got {result}"


@ldd_trajectory
def test_auto_domain_explicit(tmp_path: pathlib.Path, monkeypatch, caplog) -> None:
    monkeypatch.setenv("PLATFORM_DOMAIN", "tronyx.ru")
    logger.info("[IMP:9][test][scaffolder] test_auto_domain_explicit")
    result = auto_domain(name="myapp", domain="custom.example.com")
    assert result == "custom.example.com"


@ldd_trajectory
def test_auto_domain_no_env(tmp_path: pathlib.Path, monkeypatch, caplog) -> None:
    monkeypatch.delenv("PLATFORM_DOMAIN", raising=False)
    logger.info("[IMP:9][test][scaffolder] test_auto_domain_no_env")
    result = auto_domain(name="myapp", domain="")
    assert result == "", f"Expected empty string, got '{result}'"


# ── copy_template tests ───────────────────────────────────────────────────


@ldd_trajectory
def test_copy_template_dry_run(tmp_path: pathlib.Path, caplog) -> None:
    src_dir = tmp_path / "templates" / "template-backend"
    src_dir.mkdir(parents=True)
    (src_dir / "docker-compose.yml").write_text("version: '3'\n")
    dst_dir = str(tmp_path / "projects" / "org" / "myapp")
    logger.info("[IMP:9][test][scaffolder] test_copy_template_dry_run")
    result = copy_template(src=str(src_dir), dst=dst_dir, dry_run=True)
    assert result
    assert not pathlib.Path(dst_dir).exists()


@ldd_trajectory
def test_copy_template_conflict(tmp_path: pathlib.Path, caplog) -> None:
    src_dir = tmp_path / "templates" / "template-backend"
    src_dir.mkdir(parents=True)
    (src_dir / "docker-compose.yml").write_text("version: '3'\n")
    dst = tmp_path / "projects" / "org" / "myapp"
    dst.mkdir(parents=True)
    (dst / "existing-file.txt").write_text("already here")
    logger.info("[IMP:9][test][scaffolder] test_copy_template_conflict")
    result = copy_template(src=str(src_dir), dst=str(dst), dry_run=False)
    assert not result, "Expected False for existing directory"
    assert (dst / "existing-file.txt").exists()


@ldd_trajectory
def test_copy_template_missing_source(tmp_path: pathlib.Path, caplog) -> None:
    dst = str(tmp_path / "projects" / "org" / "myapp")
    logger.info("[IMP:9][test][scaffolder] test_copy_template_missing_source")
    result = copy_template(src="/nonexistent/template", dst=dst, dry_run=False)
    assert not result


# ── scaffold scenario tests ───────────────────────────────────────────────


@ldd_trajectory
def test_new_backend_project_scaffold(tmp_path: pathlib.Path, monkeypatch, caplog) -> None:
    import core.internal.scaffold.project_scaffolder as ps

    monkeypatch.setattr(ps, "render_directory_in_place", lambda path, vars: 0)
    template_dir = tmp_path / "templates" / "template-backend"
    template_dir.mkdir(parents=True)
    (template_dir / "docker-compose.yml").write_text("version: '3'\nservices:\n  app:\n    image: app\n")
    (template_dir / "Dockerfile").write_text("FROM python:3.10\n")
    project_dir = tmp_path / "projects" / "test-org" / "test-backend"
    logger.info("[IMP:9][test][scaffolder] test_new_backend_project_scaffold")
    assert copy_template(str(template_dir), str(project_dir), dry_run=False)
    from core.internal.scaffold.scaffold_helpers import gen_ai_platform_yaml, gen_project_agents, gen_project_makefile

    yaml_path = project_dir / "ai-platform.yaml"
    r = gen_ai_platform_yaml(
        name="test-backend",
        ptype="backend",
        org="test-org",
        node="tronyx-vps",
        domain="backend.tronyx.ru",
        database="backend_db",
        mode="prod",
        output_path=yaml_path,
        minimal=False,
    )
    assert r == "generated"
    assert yaml_path.exists()
    parsed = yaml.safe_load(yaml_path.read_text())
    assert parsed["name"] == "test-backend"
    assert parsed["type"] == "backend"
    assert parsed["monitoring"]["metrics"] is True
    assert parsed["needs"]["database"] == "backend_db"
    mk = gen_project_makefile(
        name="test-backend", domain="backend.tronyx.ru", output_path=project_dir / "Makefile", force=False
    )
    assert mk == "generated"
    ag = gen_project_agents(
        name="test-backend",
        org="test-org",
        template="backend",
        node="tronyx-vps",
        domain="backend.tronyx.ru",
        output_path=project_dir / "AGENTS.md",
        force=False,
    )
    assert ag == "generated"


@ldd_trajectory
def test_new_frontend_project_scaffold(tmp_path: pathlib.Path, monkeypatch, caplog) -> None:
    import core.internal.scaffold.project_scaffolder as ps

    monkeypatch.setattr(ps, "render_directory_in_place", lambda path, vars: 0)
    template_dir = tmp_path / "templates" / "template-frontend"
    template_dir.mkdir(parents=True)
    (template_dir / "docker-compose.yml").write_text("version: '3'\n")
    project_dir = tmp_path / "projects" / "test-org" / "test-frontend"
    assert copy_template(str(template_dir), str(project_dir), dry_run=False)
    from core.internal.scaffold.scaffold_helpers import gen_ai_platform_yaml

    yaml_path = project_dir / "ai-platform.yaml"
    gen_ai_platform_yaml(
        name="test-frontend",
        ptype="frontend",
        org="test-org",
        domain="frontend.tronyx.ru",
        output_path=yaml_path,
        minimal=False,
    )
    logger.info("[IMP:9][test][scaffolder] test_new_frontend_project_scaffold")
    parsed = yaml.safe_load(yaml_path.read_text())
    assert parsed["monitoring"]["metrics"] is False
    assert parsed["monitoring"]["logs_retention"] == "3d"
    assert parsed["monitoring"]["dashboard"] is False


@ldd_trajectory
def test_checklist_generated(tmp_path: pathlib.Path, caplog) -> None:
    project_dir = tmp_path / "test-project"
    project_dir.mkdir()
    logger.info("[IMP:9][test][scaffolder] test_checklist_generated")
    result = generate_checklist(
        project_dir=str(project_dir),
        name="test-project",
        org="test-org",
        template="backend",
        domain="test.tronyx.ru",
        database="test_db",
    )
    assert result
    checklist = project_dir / "_SETUP_CHECKLIST.md"
    assert checklist.exists()
    content = checklist.read_text()
    assert "test-project" in content
    assert "gh repo create" in content
    assert "test-org/test-project" in content
    assert "CREATE DATABASE" in content


@ldd_trajectory
def test_dry_run_scaffold_no_files(tmp_path: pathlib.Path, monkeypatch, caplog) -> None:
    template_dir = tmp_path / "templates" / "template-backend"
    template_dir.mkdir(parents=True)
    (template_dir / "docker-compose.yml").write_text("version: '3'\n")
    project_dir = tmp_path / "projects" / "test-org" / "test-dryrun"
    logger.info("[IMP:9][test][scaffolder] test_dry_run_scaffold_no_files")
    copy_template(str(template_dir), str(project_dir), dry_run=True)
    assert not project_dir.exists()
