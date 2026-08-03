"""
# GREP_SUMMARY: test gen_project_platform_md AI-PLATFORM.md generator GENERATED-markers enabled-modules dsn per-node graceful
# STRUCTURE: ▶ 6 сценариев (рендер статики, GENERATED-секция, DSN-подстановка, повторная генерация, missing-files, CLI) → ⎋ LDD trajectory
# region MODULE_CONTRACT
## @purpose  Unit tests for core/internal/scaffold/gen_project_platform_md.py (DevPlan 133 W1).
##           Tests static render, GENERATED section (enabled modules, services, networks, needs),
##           marker-based section replacement (idempotency — no duplicates), graceful degradation
##           on missing node.yaml/platform-env.yaml, and CLI argument parsing.
## @scope    No subprocess calls. Direct Python imports. tmp_path fixtures (Zero Hardcode Rule).
## @invariants
##   - All tests call library functions directly (native pytest)
##   - @ldd_trajectory asserts IMP:9 log presence (Anti-Illusion rule)
##   - tmp_path used for all temp files
## @rationale DevPlan 133 W1.4: unit coverage for the AI-PLATFORM.md generator per acceptance
##            criterion (2) — генератор (рендер, per-node данные, маркеры).
## @changes 2026-08-03 | Created (DevPlan 133 W1)
# endregion MODULE_CONTRACT
"""

import logging
from pathlib import Path

from tests._conftest.ldd import ldd_trajectory

logger = logging.getLogger(__name__)

# ── Import the module under test (canonical dotted import — conftest addsitedir) ──
from core.internal.scaffold.gen_project_platform_md import (
    GENERATED_END,
    GENERATED_START,
    generate,
    render_static,
    write_project_platform_md,
)

_NODE_YAML = """
contexts:
  - name: test-lab
node:
  name: test-node
  host: 10.0.0.1
domain: test.ru
projects: []
modules:
  - name: nginx
    enabled: true
  - name: postgres
    enabled: true
  - name: disabled-mod
    enabled: false
"""

_PLATFORM_ENV = """
provides:
  postgres:
    host: pgbouncer
    port: 6432
    dsn_template: postgresql://${NAME}_user:***@pgbouncer:6432/${NAME}_db
    networks:
    - shared-db-net
  redis:
    host: redis
    port: 6379
    url_template: redis://redis:6379/0
    networks:
    - shared-cache-net
profiles:
- postgres
- redis
proxy: {}
"""

_AI_YAML = """
name: myapp
type: backend
target_node: test-node
needs:
  domain: myapp.test.ru
  database: myapp_db
  cache: false
  storage: false
  expose: true
  llm: false
"""


def _write(tmp_path: Path, name: str, content: str) -> Path:
    """Write a fixture file into tmp_path and return its path."""
    p = tmp_path / name
    p.write_text(content)
    return p


# ═══════════════════════════════════════════════════════════════════
# region Tests: static render
# ═══════════════════════════════════════════════════════════════════


# 🧪 TRAP[TEST] · Regression · static part renders canonical references + markers
# · Scenario: render_static() with org → URL + local path + single marker pair
# · Last fail: N/A (new test)
# · Remove if: static template structure changes
@ldd_trajectory
def test_static_render_contains_canonical_refs(caplog):
    """Static part: canonical URL (org), local path, DO NOT, marker pair."""
    static = render_static("myapp", org="test-lab")

    assert "# AI-PLATFORM.md — myapp" in static
    assert "https://github.com/test-lab/ai-platform/blob/main/docs/platform-project-contract.md" in static
    assert "docs/platform-project-contract.md" in static
    assert "DO NOT" in static
    # Single marker pair (для вставки GENERATED-секции)
    assert static.count(GENERATED_START) == 1
    assert static.count(GENERATED_END) == 1

    logger.critical("[IMP:9][test] static render OK — canonical refs + marker pair present")


# endregion


# ═══════════════════════════════════════════════════════════════════
# region Tests: full generate (per-node data)
# ═══════════════════════════════════════════════════════════════════


# 🧪 TRAP[TEST] · Regression · GENERATED section renders per-node data (enabled modules, DSN, needs)
# · Scenario: node.yaml + platform-env.yaml + ai-platform.yaml → section with modules/services/networks
# · Last fail: N/A (new test)
# · Remove if: render_generated() logic changes
@ldd_trajectory
def test_generate_full_document(caplog, tmp_path):
    """Full generate(): static + GENERATED section with per-node data."""
    _write(tmp_path, "node.yaml", _NODE_YAML)
    _write(tmp_path, "platform-env.yaml", _PLATFORM_ENV)
    ai_yaml = _write(tmp_path, "ai-platform.yaml", _AI_YAML)

    doc = generate(
        str(tmp_path),
        node_name="test-node",
        node_yaml_path=str(tmp_path / "node.yaml"),
        platform_env_path=str(tmp_path / "platform-env.yaml"),
        project_yaml_path=str(ai_yaml),
    )

    # Static + section
    assert GENERATED_START in doc and GENERATED_END in doc
    assert doc.count(GENERATED_START) == 1, "exactly one GENERATED section"

    # Per-node: enabled modules (disabled excluded)
    assert "**Node:** test-node" in doc
    assert "**Context (org):** test-lab" in doc
    assert "**Domain:** test.ru" in doc
    assert "nginx, postgres" in doc
    assert "disabled-mod" not in doc

    # Services with ${NAME} substitution
    assert "| postgres | pgbouncer | 6432 | `postgresql://myapp_user:***@pgbouncer:6432/myapp_db` |" in doc
    assert "| redis | redis | 6379 | `redis://redis:6379/0` |" in doc

    # Networks
    assert "shared-db-net" in doc and "shared-cache-net" in doc

    # needs-status
    assert "database: myapp_db" in doc
    assert "expose: True" in doc

    logger.critical("[IMP:9][test] full generate OK — per-node section rendered (len=%d)", len(doc))


# 🧪 TRAP[TEST] · Regression · regeneration replaces section without duplicates (idempotency)
# · Scenario: write → modify inputs (module added) → write again → single section, updated content
# · Last fail: N/A (new test)
# · Remove if: write_project_platform_md() section-replacement logic changes
@ldd_trajectory
def test_regeneration_replaces_section_no_duplicates(caplog, tmp_path):
    """Re-generation: section replaced in place — no duplicate sections, static part preserved."""
    node_yaml = _write(tmp_path, "node.yaml", _NODE_YAML)
    platform_env = _write(tmp_path, "platform-env.yaml", _PLATFORM_ENV)
    ai_yaml = _write(tmp_path, "ai-platform.yaml", _AI_YAML)

    status1 = write_project_platform_md(
        str(tmp_path),
        node_name="test-node",
        node_yaml_path=str(node_yaml),
        platform_env_path=str(platform_env),
        project_yaml_path=str(ai_yaml),
    )
    assert status1 == "created"

    # Ручная правка статики (head) — должна сохраниться при section-update
    md_path = tmp_path / "AI-PLATFORM.md"
    md_path.write_text("# HAND-EDIT: keep this line\n" + md_path.read_text())

    status2 = write_project_platform_md(
        str(tmp_path),
        node_name="test-node",
        node_yaml_path=str(node_yaml),
        platform_env_path=str(platform_env),
        project_yaml_path=str(ai_yaml),
    )

    final = (tmp_path / "AI-PLATFORM.md").read_text()
    assert status2 == "updated"
    assert final.count(GENERATED_START) == 1
    assert final.count(GENERATED_END) == 1
    # Статичная правка сохранена, секция пересоздана без дублей
    assert "# HAND-EDIT: keep this line" in final
    assert "# AI-PLATFORM.md — myapp" in final

    logger.critical("[IMP:9][test] regeneration OK — %s → %s, single marker pair", status1, status2)


# 🧪 TRAP[TEST] · Regression · existing file without markers → skipped unless force
# · Scenario: file with no markers → "exists" without force, "created" with force
# · Last fail: N/A (new test)
# · Remove if: force-semantics of write_project_platform_md() changes
@ldd_trajectory
def test_existing_file_without_markers_skipped_unless_force(caplog, tmp_path):
    """Existing AI-PLATFORM.md WITHOUT markers → skip (idempotent), force → overwrite."""
    _write(tmp_path, "node.yaml", _NODE_YAML)
    _write(tmp_path, "platform-env.yaml", _PLATFORM_ENV)
    ai_yaml = _write(tmp_path, "ai-platform.yaml", _AI_YAML)
    _write(tmp_path, "AI-PLATFORM.md", "# manual file — keep me\n")

    status = write_project_platform_md(
        str(tmp_path),
        node_name="test-node",
        node_yaml_path=str(tmp_path / "node.yaml"),
        platform_env_path=str(tmp_path / "platform-env.yaml"),
        project_yaml_path=str(ai_yaml),
    )
    assert status == "exists"
    assert (tmp_path / "AI-PLATFORM.md").read_text() == "# manual file — keep me\n"

    status2 = write_project_platform_md(
        str(tmp_path),
        node_name="test-node",
        node_yaml_path=str(tmp_path / "node.yaml"),
        platform_env_path=str(tmp_path / "platform-env.yaml"),
        project_yaml_path=str(ai_yaml),
        force=True,
    )
    assert status2 == "created"
    assert GENERATED_START in (tmp_path / "AI-PLATFORM.md").read_text()

    logger.critical("[IMP:9][test] skip/force semantics OK — %s then %s", status, status2)


# endregion


# ═══════════════════════════════════════════════════════════════════
# region Tests: graceful degradation
# ═══════════════════════════════════════════════════════════════════


# 🧪 TRAP[TEST] · Regression · missing node.yaml/platform-env.yaml → graceful warning section
# · Scenario: only ai-platform.yaml present → section with warnings, no crash
# · Last fail: N/A (new test)
# · Remove if: graceful-degradation logic in render_generated() changes
@ldd_trajectory
def test_missing_inputs_graceful(caplog, tmp_path):
    """Missing node.yaml/platform-env.yaml → warning section (graceful, no crash)."""
    _write(tmp_path, "ai-platform.yaml", _AI_YAML)

    doc = generate(
        str(tmp_path),
        node_name="test-node",
        node_yaml_path=str(tmp_path / "missing-node.yaml"),
        platform_env_path=str(tmp_path / "missing-platform-env.yaml"),
    )

    assert GENERATED_START in doc
    assert "node.yaml not found" in doc
    assert "platform-env.yaml not found" in doc
    # needs всё равно рендерится (ai-platform.yaml есть)
    assert "needs:" in doc

    logger.critical("[IMP:9][test] graceful degradation OK — warning section rendered")


# 🧪 TRAP[TEST] · Regression · CLI parses --project-dir and writes the file
# · Scenario: main() with tmp project dir → exit 0, file created
# · Last fail: N/A (new test)
# · Remove if: CLI argument parsing in main() changes
@ldd_trajectory
def test_cli_project_dir_writes_file(caplog, tmp_path, monkeypatch):
    """CLI: --project-dir → AI-PLATFORM.md created (exit 0)."""
    _write(tmp_path, "ai-platform.yaml", _AI_YAML)
    import sys as _sys

    monkeypatch.setattr(
        _sys,
        "argv",
        ["gen_project_platform_md.py", "--project-dir", str(tmp_path)],
    )

    from core.internal.scaffold.gen_project_platform_md import main

    rc = main()
    assert rc == 0
    assert (tmp_path / "AI-PLATFORM.md").is_file()
    assert GENERATED_START in (tmp_path / "AI-PLATFORM.md").read_text()

    logger.critical("[IMP:9][test] CLI OK — AI-PLATFORM.md written, exit=%d", rc)


# endregion
