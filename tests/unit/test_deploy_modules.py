# GREP_SUMMARY: deploy-modules, test, static-audit, skip-provision, state-machine, topo-sort, facade, rsync, sudoers, orphan, overlay
# STRUCTURE: ▶ test_skip_provision_flag (static grep phases.py) → ▶ test_merge_deploy_steps (static grep node-lifecycle.sh) → ▶ test_topo_sort_enriched (native topo_sort.py call with mock yamls) → ▶ фасад-генераторы (sudoers/orphan/overlay) + CI rsync → ⎋ LDD [IMP:9]
# region MODULE_CONTRACT
## @purpose  Static audit фасадного домена деплоя: shell-фасады (node-lifecycle.sh, phases.py,
##           deploy-modules.sh), CI rsync (core-deploy.yml), системные генераторы
##           (sudoers_generator, orphan_reconciler, context_overlay) и topo_sort.
##           Сплит test_deploy_modules.py по подобластям (DevPlan 139 W3 T6): пакеты
##           (docker) → test_deploy_modules_packages.py, env (secrets_validator) →
##           test_deploy_modules_env.py, фасады → настоящий файл.
## @scope    S1: --skip-provision pass-through (phases.py φ8/φ12).
##           S2: merged deploy-modules step (node-lifecycle.sh тонкий фасад).
##           S10: topo_sort.py enriched output (native, mock module.yamls).
##           S5: CI rsync консолидация (core-deploy.yml).
##           S6: batch sudoers + детерминизм (sudoers_generator.py).
##           S8: batch orphan reconciliation (orphan_reconciler.py).
##           S9: git pull caching (context_overlay.py).
## @invariants
##   - Все тесты читают исходники как текст (static audit) или используют native imports
##   - LDD траектория через caplog IMP:7-10 (assert_ldd_imp9 из gate_helpers, T2.16a)
##   - Каждый успешный сценарий — ≥1 IMP:9 лог
## @rationale  W4-E1 extraction проверил контракты Python-модулей; сплит группирует тесты
##             по бизнес-подобластям (фасады/пакеты/env) — файл легче читать, coverage сохранён.
## @changes    2026-07-22 — W4-E1 adaptation: all deploy-modules.sh function checks → Python module checks
##             2026-08-05 — DevPlan 139 W3 T6: сплит по подобластям (пакеты/env вынесены)
# endregion MODULE_CONTRACT

import logging

import pytest

from tests.helpers.deploy_modules_audit import (
    DEPLOY_MODULES_SH,
    DEPLOY_PYTHON_DIR,
    NODE_LIFECYCLE_SH,
    ORCHESTRATOR_PY,
    PHASES_PY,
    STATE_MACHINE_PY,
    _enrich_modules_output,
    _extract_python_func,
    _setup_module_yaml,
)
from tests.helpers.gate_helpers import assert_ldd_imp9, repo_root

logger = logging.getLogger(__name__)


# ══════════════════════════════════════════════════════════════════════════════
# S1: --skip-provision flag
# ══════════════════════════════════════════════════════════════════════════════

# region FUNC_test_skip_provision_flag
## @purpose  Static audit: verify --skip-provision flag is passed from phases.py (Python CLI)
##           to deploy-modules.sh. After W4-E1 extraction, the flag is injected by phases.py
##           in the deploy phase (φ8/φ12), not parsed by deploy-modules.sh itself.
## @io       ⇥ caplog, PHASES_PY, DEPLOY_MODULES_SH → ⎋ None (pytest.fail if flag missing)
## @complexity 1 — static grep on file content


@pytest.mark.static_audit
def test_skip_provision_flag(caplog) -> None:
    """--skip-provision passed from phases.py (φ8/φ12) + SKIP_PROVISION guard в deploy-modules.sh."""
    caplog.set_level(logging.DEBUG)
    logger.info("[IMP:7][test_skip_provision_flag] Reading phases.py ...")
    content = PHASES_PY.read_text()

    assert '"--skip-provision"' in content, (
        "S1 violation: --skip-provision not passed from phases.py deploy phase (φ8/φ12)"
    )
    logger.info("[IMP:9][test_skip_provision_flag] --skip-provision passed from phases.py OK")

    dm_content = DEPLOY_MODULES_SH.read_text()
    assert "--skip-provision)" in dm_content, "S1 violation: --skip-provision not parsed in deploy-modules.sh main()"
    assert "SKIP_PROVISION" in dm_content, "S1 violation: SKIP_PROVISION not set in deploy-modules.sh"
    assert 'if [[ "${SKIP_PROVISION}" != "true" ]]; then' in dm_content, (
        "S1 violation: provisioner block not guarded by SKIP_PROVISION check in deploy-modules.sh"
    )
    logger.info("[IMP:9][test_skip_provision_flag] deploy-modules.sh SKIP_PROVISION guard OK")


# endregion FUNC_test_skip_provision_flag


# ══════════════════════════════════════════════════════════════════════════════
# S2: Merged deploy steps
# ══════════════════════════════════════════════════════════════════════════════

# region FUNC_test_merge_deploy_steps
## @purpose  Static audit: node-lifecycle.sh — тонкий фасад (no step functions), deploy-modules
##           выполняется в phases.py (φ8 phase_deploy_services / φ12 phase_deploy_update),
##           update_step_5_deploy_system удалён, checkpoints = phase keys в state.json.
## @io       ⇥ caplog, NODE_LIFECYCLE_SH, PHASES_PY, STATE_MACHINE_PY → ⎋ None
## @complexity 1 — static grep on file content


@pytest.mark.static_audit
def test_merge_deploy_steps(caplog) -> None:
    """Фасад без step-функций; deploy_modules в phases.py; checkpoints = phase keys."""
    caplog.set_level(logging.DEBUG)
    logger.info("[IMP:7][test_merge_deploy_steps] Reading node-lifecycle.sh ...")
    content = NODE_LIFECYCLE_SH.read_text()

    # ── 1. node-lifecycle.sh is a thin facade — NO step functions (DevPlan 087) ──
    assert "update_step_4_deploy_docker" not in content, (
        "S2 violation: update_step_4_deploy_docker still exists — must be renamed to update_step_4_deploy_modules"
    )
    assert "update_step_4" not in content, (
        "S2 violation: node-lifecycle.sh facade must NOT contain step functions (update_step_4) — "
        "phase logic lives in lifecycle/phases.py"
    )
    phases_content = PHASES_PY.read_text()
    assert "deploy_modules" in phases_content, "S2 violation: deploy_modules phase not found in phases.py (φ8/φ12)"
    logger.info("[IMP:9][test_merge_deploy_steps] Facade has no step functions; deploy_modules in phases.py OK")

    # ── 2. update_step_5_deploy_system must be REMOVED ──
    assert "update_step_5_deploy_system" not in content, (
        "S2 violation: update_step_5_deploy_system still exists — must be removed"
    )
    logger.info("[IMP:9][test_merge_deploy_steps] Step 5 function removed OK")

    # ── 3. deploy-modules.sh called with --skip-provision (via phases.py) ──
    sm_content = STATE_MACHINE_PY.read_text()
    assert '"deploy_services"' in sm_content, "S2 violation: deploy_services phase not registered in state_machine.py"
    assert '"--skip-provision"' in phases_content, (
        "S2 violation: --skip-provision not passed from phases.py deploy phase (φ8/φ12)"
    )
    assert "deploy-modules.sh" in phases_content, (
        "S2 violation: deploy-modules.sh not invoked from phases.py deploy phase (φ8/φ12)"
    )
    logger.info("[IMP:9][test_merge_deploy_steps] --skip-provision flag in phases.py OK")

    # ── 4. Checkpoints are now phase keys in state.json (DevPlan 087) ──
    assert "checkpoint_step" not in content, (
        "S2 violation: node-lifecycle.sh facade must NOT contain checkpoint_step — "
        "checkpoints are phase keys in state.json (BootstrapPhase enum)"
    )
    assert "phase_deploy_services" in phases_content, "S2 violation: phase_deploy_services (φ8) not found in phases.py"
    assert "phase_deploy_update" in phases_content, "S2 violation: phase_deploy_update (φ12) not found in phases.py"
    logger.info(
        "[IMP:9][test_merge_deploy_steps] Checkpoints as phase keys: φ8 phase_deploy_services + φ12 phase_deploy_update OK"
    )

    # ── 5. Dry-run output updated ──
    assert "deploy-docker → deploy-system" not in content, (
        "S2 violation: dry-run output still shows old 'deploy-docker → deploy-system'"
    )
    logger.info("[IMP:9][test_merge_deploy_steps] Dry-run output updated OK")


# endregion FUNC_test_merge_deploy_steps


# ══════════════════════════════════════════════════════════════════════════════
# S10: Enriched topo_sort.py output
# ══════════════════════════════════════════════════════════════════════════════

# region FUNC_test_topo_sort_enriched_output
## @purpose  Verify that topo_sort.py main() returns enriched output with modules dict
##           containing install_type and severity for ALL modules (not just docker).
## @io       ⇥ tmp_path, caplog → ⎋ None (pytest.fail if enriched output missing)
## @complexity 2 — I/O: mock module.yamls, call topo_sort functions, parse JSON output


@pytest.mark.static_audit
def test_topo_sort_enriched_output(caplog, tmp_path) -> None:
    """Enriched output: modules dict (install_type+severity) для system+docker; groups — только docker.

    W2 T2.8 (DevPlan 160): маркер smoke→static_audit — тест чисто статический
    (tmp_path mock module.yamls + topo_sort функции, 0 Docker-зависимостей).
    """
    caplog.set_level(logging.DEBUG)
    logger.info("[IMP:7][test_topo_sort_enriched_output] Setting up mock module.yamls in %s ...", tmp_path)

    _setup_module_yaml(tmp_path, "postgres", install_type="docker", severity="critical")
    _setup_module_yaml(tmp_path, "redis", install_type="docker", severity="critical")
    _setup_module_yaml(tmp_path, "nginx", install_type="system", severity="critical")
    _setup_module_yaml(tmp_path, "hermes-agent", install_type="docker", severity="warn")

    logger.info("[IMP:8][test_topo_sort_enriched_output] Building enriched output ...")
    parsed = _enrich_modules_output(tmp_path, ["postgres", "redis", "nginx", "hermes-agent"])

    # ── Verify enriched output structure ──
    assert "groups" in parsed, "S10 violation: 'groups' key missing from enriched output"
    assert isinstance(parsed["groups"], list), "S10 violation: 'groups' must be a list"
    logger.info("[IMP:9][test_topo_sort_enriched_output] 'groups' key present OK (backward compat)")

    assert "modules" in parsed, "S10 violation: 'modules' key missing from enriched output"
    assert isinstance(parsed["modules"], dict), "S10 violation: 'modules' must be a dict"
    logger.info("[IMP:9][test_topo_sort_enriched_output] 'modules' key present OK (enrichment)")

    modules = parsed["modules"]
    assert modules["postgres"]["install_type"] == "docker", "S10 violation: postgres install_type"
    assert modules["postgres"]["severity"] == "critical", "S10 violation: postgres severity"
    assert modules["nginx"]["install_type"] == "system", "S10 violation: nginx install_type"
    assert modules["hermes-agent"]["install_type"] == "docker", "S10 violation: hermes-agent install_type"
    assert modules["hermes-agent"]["severity"] == "warn", "S10 violation: hermes-agent severity"
    logger.info("[IMP:9][test_topo_sort_enriched_output] Module metadata OK (docker/system + severities)")

    group_modules = set()
    for g in parsed["groups"]:
        group_modules.update(g)
    assert "nginx" not in group_modules, (
        "S10 violation: system module 'nginx' should not appear in docker deploy groups"
    )
    logger.info("[IMP:9][test_topo_sort_enriched_output] System module correctly excluded from docker groups")

    assert len(parsed["groups"]) > 0, "S10 violation: empty groups in enriched output"
    for group in parsed["groups"]:
        assert isinstance(group, list), "S10 violation: each group must be a list of module names"
    logger.info("[IMP:9][test_topo_sort_enriched_output] All group entries are valid lists")

    assert_ldd_imp9(caplog)


# 🧪 TRAP[TEST] · Regression: S10 enriched output must include all modules (system + docker)
# · Scenario: 4-module mix (2 docker, 1 system, 1 docker) → all 4 appear in modules, only 3 in groups
# · Last fail: N/A
# · Remove if: topo_sort.py output changes schema
# endregion FUNC_test_topo_sort_enriched_output


# ══════════════════════════════════════════════════════════════════════════════
# S5: RSync consolidation
# ══════════════════════════════════════════════════════════════════════════════

# region FUNC_test_rsync_consolidation
## @purpose  Static audit (S5 → REF-0112): core-deploy.yml консолидированный шаг доставки —
##           МОДУЛЬНЫЙ вызов `core_deliverer ci-deliver`; inline-rsync отсутствует (один owner
##           exclude-set'ов); отдельные 5b/5c steps не вернулись.
## @io       ⇥ caplog, .github/workflows/core-deploy.yml → ⎋ None
## @complexity 1 — static grep


@pytest.mark.static_audit
def test_rsync_consolidation(caplog) -> None:
    """core-deploy.yml: доставка файлов через core_deliverer ci-deliver, без inline rsync."""
    caplog.set_level(logging.DEBUG)
    core_deploy_yml = repo_root() / ".github" / "workflows" / "core-deploy.yml"
    logger.info("[IMP:7][test_rsync_consolidation] Reading core-deploy.yml ...")
    content = core_deploy_yml.read_text()

    assert "Deliver core + config to VPS" in content, (
        "Consolidated delivery step 'Deliver core + config to VPS' not found"
    )
    logger.info("[IMP:9][test_rsync_consolidation] Consolidated delivery step name found OK")

    assert "name: Rsync platform-env.yaml to VPS" not in content, (
        "Violation: separate 5b step 'Rsync platform-env.yaml to VPS' still present"
    )
    assert "name: Rsync Makefile to VPS" not in content, (
        "Violation: separate 5c step 'Rsync Makefile to VPS' still present"
    )
    logger.info("[IMP:9][test_rsync_consolidation] Separate 5b/5c YAML steps removed OK")

    # REF-0112: файловая фаза = модульный вызов deliverer; inline-rsync запрещён
    # (дивергентные exclude-set'ы двух каналов = исходный баг)
    has_module_call = "core.internal.bootstrap.core_deliverer" in content and "ci-deliver" in content
    has_inline_delete_rsync = "rsync -avz --delete" in content
    logger.critical("[IMP:9][test_rsync_consolidation] module call present: %s", has_module_call)
    logger.critical("[IMP:9][test_rsync_consolidation] inline --delete rsync absent: %s", not has_inline_delete_rsync)
    assert has_module_call, (
        "core-deploy.yml must invoke 'python3 -m core.internal.bootstrap.core_deliverer ci-deliver' "
        "(REF-0112: single-owner exclude-sets)"
    )
    assert not has_inline_delete_rsync, (
        "core-deploy.yml contains inline 'rsync -avz --delete' — divergent exclude channel (REF-0112 regression)"
    )
    logger.info("[IMP:9][test_rsync_consolidation] Module-call contract verified OK")

    assert_ldd_imp9(caplog)


# 🧪 TRAP[TEST] · Regression: S5 consolidation (3 steps → 1) evolved into REF-0112 module-call
# · Scenario: static audit of core-deploy.yml for consolidated step + no inline rsync
# · Last fail: карточка REF-0112 — CI inline-rsync тянул чужой exclude-set в prod-tree
# · Remove if: CI deployment strategy changes fundamentally
# endregion FUNC_test_rsync_consolidation


# ══════════════════════════════════════════════════════════════════════════════
# S6: Batch sudoers (+ W4-E5 determinism)
# ══════════════════════════════════════════════════════════════════════════════

# region FUNC_test_batch_sudoers
## @purpose  Static audit: batch_generate_sudoers() в sudoers_generator.py + вызов из orchestrator.
## @io       ⇥ caplog, DEPLOY_PYTHON_DIR/sudoers_generator.py → ⎋ None
## @complexity 1 — static grep on file content


@pytest.mark.static_audit
def test_batch_sudoers(caplog) -> None:
    """batch_generate_sudoers + render_sudoers_rules существуют; orchestrator их использует."""
    caplog.set_level(logging.DEBUG)
    logger.info("[IMP:7][test_batch_sudoers] Reading sudoers_generator.py ...")
    py_content = _extract_python_func(DEPLOY_PYTHON_DIR / "sudoers_generator.py", "batch_generate_sudoers")

    assert "def batch_generate_sudoers(" in py_content, (
        "S6 violation: batch_generate_sudoers() not found in sudoers_generator.py"
    )
    logger.info("[IMP:9][test_batch_sudoers] batch_generate_sudoers() function declared OK")

    sg_content = (DEPLOY_PYTHON_DIR / "sudoers_generator.py").read_text()
    assert "def render_sudoers_rules(" in sg_content, (
        "S6 violation: render_sudoers_rules() helper not found in sudoers_generator.py"
    )
    logger.info("[IMP:9][test_batch_sudoers] render_sudoers_rules() helper OK")

    orch_content = ORCHESTRATOR_PY.read_text()
    assert "sudoers_generator" in orch_content, "S6 violation: sudoers_generator not imported in deploy_orchestrator.py"
    assert "batch_generate_sudoers" in orch_content, (
        "S6 violation: batch_generate_sudoers not used in deploy_orchestrator.py"
    )
    logger.info("[IMP:9][test_batch_sudoers] sudoers_generator imported in deploy_orchestrator.py OK")

    assert_ldd_imp9(caplog)


# 🧪 TRAP[TEST] · Regression: S6 batch sudoers must replace per-module calls
# · Scenario: static grep of deploy-modules.sh for batch_generate_sudoers and removed per-module calls
# · Last fail: N/A
# · Remove if: sudoers generation approach changes
# endregion FUNC_test_batch_sudoers


# region FUNC_test_batch_sudoers_determinism
## @purpose  W4-E5: sudoers_generator.py детерминизм (visudo-гейт, 0 datetime/random в рендере).
## @io       caplog → ⎋ None (pytest.fail if determinism pattern absent)
## @complexity 1 — static grep


@pytest.mark.static_audit
def test_batch_sudoers_determinism(caplog) -> None:
    """Детерминизм: visudo-гейт записи, 0 datetime/random в render_sudoers_rules."""
    caplog.set_level(logging.DEBUG)
    logger.info("[IMP:7][test_batch_sudoers_determinism] START — checking sudoers_generator.py")
    sg_content = (DEPLOY_PYTHON_DIR / "sudoers_generator.py").read_text()
    content = _extract_python_func(DEPLOY_PYTHON_DIR / "sudoers_generator.py", "batch_generate_sudoers")

    assert "_validate_with_visudo" in sg_content, (
        "W4-E5 violation: sudoers_generator.py must validate with visudo before write"
    )
    logger.info("[IMP:9][test_batch_sudoers_determinism] _validate_with_visudo present")

    render_func = _extract_python_func(DEPLOY_PYTHON_DIR / "sudoers_generator.py", "render_sudoers_rules")
    assert "datetime" not in render_func, (
        "W4-E5 violation: render_sudoers_rules must NOT use datetime (breaks determinism)"
    )
    assert "random" not in render_func, "W4-E5 violation: render_sudoers_rules must NOT use random (breaks determinism)"
    logger.info("[IMP:9][test_batch_sudoers_determinism] no non-deterministic sources OK")

    assert "ALL=(root) NOPASSWD:" in render_func or "NOPASSWD" in render_func, (
        "W4-E5 violation: render_sudoers_rules must produce NOPASSWD sudoers rules"
    )
    logger.info("[IMP:9][test_batch_sudoers_determinism] stable sudoers rule format present")

    assert ("for" in content and "mod_name" in content) or "module" in content.lower(), (
        "W4-E5 violation: batch_generate_sudoers must iterate modules (deterministic order)"
    )
    logger.info("[IMP:9][test_batch_sudoers_determinism] deterministic iteration OK")

    assert_ldd_imp9(caplog)


# 🧪 TRAP[TEST] · Regression: W4-E5 batch sudoers determinism (same input → identical output)
# · Remove if: sudoers generation intentionally adds timestamps (then relax the check)
# endregion FUNC_test_batch_sudoers_determinism


# ══════════════════════════════════════════════════════════════════════════════
# S8: Batch orphan reconciliation (+ W4-E5 foreign marking)
# ══════════════════════════════════════════════════════════════════════════════

# region FUNC_test_batch_orphan
## @purpose  Static audit: batch_orphan_reconciliation() в orphan_reconciler.py + orchestrator.
## @io       ⇥ caplog, DEPLOY_PYTHON_DIR/orphan_reconciler.py → ⎋ None
## @complexity 1 — static grep on file content


@pytest.mark.static_audit
def test_batch_orphan(caplog) -> None:
    """batch_orphan_reconciliation + docker ps + orchestrator import."""
    caplog.set_level(logging.DEBUG)
    logger.info("[IMP:7][test_batch_orphan] Reading orphan_reconciler.py ...")
    content = _extract_python_func(DEPLOY_PYTHON_DIR / "orphan_reconciler.py", "batch_orphan_reconciliation")

    assert "def batch_orphan_reconciliation(" in content, (
        "S8 violation: batch_orphan_reconciliation() function not found in orphan_reconciler.py"
    )
    logger.info("[IMP:9][test_batch_orphan] batch_orphan_reconciliation() function declared OK")

    or_content = (DEPLOY_PYTHON_DIR / "orphan_reconciler.py").read_text()
    assert "docker ps" in or_content or "docker container" in or_content, (
        "S8 violation: batch_orphan_reconciliation must call docker ps"
    )
    logger.info("[IMP:9][test_batch_orphan] batch_orphan_reconciliation uses docker ps OK")

    orch_content = ORCHESTRATOR_PY.read_text()
    assert "orphan_reconciler" in orch_content, "S8 violation: orphan_reconciler not imported in deploy_orchestrator.py"
    assert "batch_orphan_reconciliation" in orch_content, (
        "S8 violation: batch_orphan_reconciliation not used in deploy_orchestrator.py"
    )
    logger.info("[IMP:9][test_batch_orphan] orphan_reconciler imported in deploy_orchestrator.py OK")

    assert_ldd_imp9(caplog)


# 🧪 TRAP[TEST] · Regression: S8 batch orphan reconciliation must exist
# · Remove if: orphan reconciliation approach changes
# endregion FUNC_test_batch_orphan


# region FUNC_test_orphan_reconciliation_marks_foreign
## @purpose  W4-E5: orphan-детекция (docker ps + compose project labels + marking).
## @io       caplog → ⎋ None
## @complexity 1 — static grep


@pytest.mark.static_audit
def test_orphan_reconciliation_marks_foreign(caplog) -> None:
    """Orphan-детекция: enumerate containers, compare project labels, mark orphans."""
    caplog.set_level(logging.DEBUG)
    logger.info("[IMP:7][test_orphan_reconciliation] START — static audit of orphan detection")
    or_content = (DEPLOY_PYTHON_DIR / "orphan_reconciler.py").read_text()
    content = _extract_python_func(DEPLOY_PYTHON_DIR / "orphan_reconciler.py", "batch_orphan_reconciliation")

    assert "def batch_orphan_reconciliation(" in content, (
        "W4-E5 violation: batch_orphan_reconciliation() not found in orphan_reconciler.py"
    )
    logger.info("[IMP:8][test_orphan_reconciliation] function located")

    assert "docker ps" in or_content or "_get_existing_containers" in or_content, (
        "W4-E5 violation: orphan reconciler must list docker containers"
    )
    logger.info("[IMP:9][test_orphan_reconciliation] docker container enumeration present")

    assert "compose.project" in or_content or "_inspect_project_label" in or_content, (
        "W4-E5 violation: orphan detection must compare against compose project labels"
    )
    logger.info("[IMP:9][test_orphan_reconciliation] compose project label comparison present")

    assert "orphan" in or_content.lower(), "W4-E5 violation: orphan reconciler must mark/log orphan containers"
    logger.info("[IMP:9][test_orphan_reconciliation] orphan marking pattern present")

    orch_content = ORCHESTRATOR_PY.read_text()
    assert "orphan_reconciler" in orch_content, (
        "W4-E5 violation: orphan_reconciler must be imported in deploy_orchestrator.py"
    )
    logger.info("[IMP:9][test_orphan_reconciliation] orphan_reconciler imported in deploy_orchestrator.py OK")

    assert_ldd_imp9(caplog)


# 🧪 TRAP[TEST] · Regression: W4-E5 orphan reconciliation marks foreign containers
# · Remove if: orphan detection migrates to docker_orchestrator.py (then point test at new module)
# endregion FUNC_test_orphan_reconciliation_marks_foreign


# ══════════════════════════════════════════════════════════════════════════════
# S9: Git pull caching
# ══════════════════════════════════════════════════════════════════════════════

# region FUNC_test_git_pull_caching
## @purpose  Static audit: context_overlay.py timestamp-based git pull caching (300s skip).
## @io       ⇥ caplog, DEPLOY_PYTHON_DIR/context_overlay.py → ⎋ None
## @complexity 1 — static grep on file content


@pytest.mark.static_audit
def test_git_pull_caching(caplog) -> None:
    """Timestamp-кэш git pull: CONTEXT_PULL_TS_PATH + 300s threshold + _update_timestamp."""
    caplog.set_level(logging.DEBUG)
    logger.info("[IMP:7][test_git_pull_caching] Reading context_overlay.py ...")
    content = (DEPLOY_PYTHON_DIR / "context_overlay.py").read_text()

    assert "CONTEXT_PULL_TS_PATH" in content, "S9 violation: pull_ts_path constant not found in context_overlay.py"
    logger.info("[IMP:9][test_git_pull_caching] CONTEXT_PULL_TS_PATH constant found OK")

    assert "time.time()" in content, "S9 violation: 'time.time()' not found for timestamp"
    logger.info("[IMP:9][test_git_pull_caching] time.time() used for timestamp OK")

    assert "CONTEXT_PULL_CACHE_SECONDS" in content, "S9 violation: CONTEXT_PULL_CACHE_SECONDS constant not found"
    assert "300" in content, "S9 violation: 300 second cache threshold not found"
    logger.info("[IMP:9][test_git_pull_caching] 300s cache threshold OK")

    assert "SKIP" in content and "cache" in content.lower(), (
        "S9 violation: cache skip message not found in context_overlay.py"
    )
    logger.info("[IMP:9][test_git_pull_caching] Cache skip message OK")

    assert "_update_timestamp" in content, "S9 violation: _update_timestamp function not found in context_overlay.py"
    logger.info("[IMP:9][test_git_pull_caching] _update_timestamp exists OK")

    assert_ldd_imp9(caplog)


# 🧪 TRAP[TEST] · Regression: S9 git pull caching must have 300s timestamp-based skip
# · Remove if: git pull caching strategy changes
# endregion FUNC_test_git_pull_caching
