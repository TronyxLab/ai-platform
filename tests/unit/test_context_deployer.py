"""
# GREP_SUMMARY: test_context_deployer, project-deploy, ghcr-pull, build-fallback, idempotent, healthcheck-gate, audit-log, DI, runner, facts, fn-injection
# STRUCTURE: ▶ tmp_path + node.yaml + DI (deploy_projects_fn/certs_fn/health_fn/orchestrator_deploy_fn) → ◇ filter projects → ◇ ghcr pull → ◇ build fallback → ◇ idempotent skip → ⎋ LDD trajectory
# region MODULE_CONTRACT
## @purpose  Unit tests for context_deployer.py — context project deploy orchestration.
## @scope    Tests resolve_context_projects, deploy_context_projects, deploy_context context
##           resolution (contexts[] canon, DevPlan 116 B6 T2).
## @invariants
##   - Все docker/subprocess/функции-зависимости через DI-параметры (E1): deploy_projects_fn,
##     certs_fn, cert_validity_fn, health_fn, audit_fn, orchestrator_deploy_fn, nginx_reload_fn,
##     stub_detector_fn, healthcheck_poll_fn — 0 monkeypatch subprocess/функций
##   - node.yaml создаётся в tmp_path
##   - Каждый тест валидирует IMP:9 бизнес-логику через ldd_trajectory
## @rationale DevPlan 047 Phase 7: context deployer bridges bootstrap "last mile".
## @changes  2026-07-22 | DevPlan 047 Phase 7 — Created
##           2026-08-13 | E1 (160) — DI-конвертация (setattr 17 → 0, −100%)
# endregion MODULE_CONTRACT
"""

import logging
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

from tests._conftest.ldd import ldd_trajectory

logger = logging.getLogger(__name__)

# ── Import the module under test ──
_MODULE_DIR = Path(__file__).resolve().parent.parent.parent / "core" / "internal" / "bootstrap" / "deploy"
sys.path.insert(0, str(_MODULE_DIR))
import context_deployer as cd

# ═══════════════════════════════════════════════════════════════════
# region Fixtures
# ═══════════════════════════════════════════════════════════════════


@pytest.fixture
def node_yaml_file(tmp_path):
    """Create a node.yaml with test projects (contexts[] canon — DevPlan 116 B6 T1)."""
    yaml_content = """\
node:
  name: test-node
  platform_domain: test.example.com
contexts:
  - name: test-ctx
projects:
  - name: webapp
    repo: https://github.com/test/webapp
    type: backend
    domain: webapp.example.com
    context: test-ctx
  - name: api
    repo: https://github.com/test/api
    type: backend
    domain: api.example.com
    context: test-ctx
  - name: other-ctx-project
    repo: https://github.com/test/other
    type: frontend
    domain: other.other.com
    context: other-ctx
"""
    yaml_path = tmp_path / "node.yaml"
    yaml_path.write_text(yaml_content, encoding="utf-8")
    return str(yaml_path)


def _noop_deploy_projects(node_yaml, context, **kw):
    """DI deploy_projects_fn: 0 проектов задеплоено (stub для deploy_context-тестов)."""
    return []


# endregion Fixtures


# ═══════════════════════════════════════════════════════════════════
# region Tests: resolve_context_projects
# ═══════════════════════════════════════════════════════════════════


# 🧪 TRAP[TEST] · Regression · resolve_context_projects filters projects by context
# · Scenario: node.yaml with 3 projects — test-ctx → 2 (webapp/api), other-ctx-project excluded;
# ·   nonexistent-ctx → 0 projects (empty for non-matching context)
# · Last fail: N/A (new test)
# · Remove if: context filtering logic changes
@ldd_trajectory
@pytest.mark.parametrize(
    ("context", "expected_len", "must_include", "must_exclude"),
    [
        pytest.param("test-ctx", 2, ["webapp", "api"], ["other-ctx-project"], id="by-context"),
        pytest.param("nonexistent-ctx", 0, [], [], id="no-match"),
    ],
)
def test_filter_projects_by_context(caplog, node_yaml_file, context, expected_len, must_include, must_exclude):
    """resolve_context_projects should filter projects by context."""
    projects = cd.resolve_context_projects(node_yaml_file, context)
    assert len(projects) == expected_len
    names = [p.name for p in projects]
    for name in must_include:
        assert name in names
    for name in must_exclude:
        assert name not in names
    logger.critical("[IMP:9][test] Filter projects by context — %d matched", len(projects))


# endregion Tests: resolve_context_projects


# ═══════════════════════════════════════════════════════════════════
# region Tests: deploy_context context resolution (DevPlan 116 B6 T2)
# ═══════════════════════════════════════════════════════════════════


# 🧪 TRAP[TEST] · Regression · deploy_context resolves context from contexts[0].name
# · Scenario: node.yaml has contexts[0].name=test-ctx (no CONTEXT env) → deploy_context uses it
# · Last fail: N/A (DevPlan 116 B6 T2 — extract-alias removed, facade get_context)
# · Remove if: context resolution logic changes
@ldd_trajectory
def test_deploy_context_resolves_from_contexts(caplog, node_yaml_file, monkeypatch):
    """deploy_context should resolve context from contexts[0].name via NodeYaml.get_context()."""
    monkeypatch.delenv("CONTEXT", raising=False)
    caplog.set_level(logging.INFO)

    result = cd.deploy_context(
        core_dir="/nonexistent/core",
        node_name="test-node",
        node_yaml=node_yaml_file,
        context="",
        deploy_projects_fn=_noop_deploy_projects,
    )
    assert result.failed == 0, "context resolved → deploy should proceed"
    found = any("[IMP:9][deploy_context] Using context=test-ctx" in r.message for r in caplog.records)
    assert found, "deploy_context did not resolve context=test-ctx from contexts[0].name"
    logger.critical("[IMP:9][test] deploy_context resolved context from contexts[0].name — OK")


# 🧪 TRAP[TEST] · Regression · deploy_context with broken/missing node.yaml → failed=1, readable log
# · Scenario: node.yaml path missing, no CONTEXT env → DeployResult.failed=1, "CONTEXT not set" log (not traceback)
# · Last fail: N/A (DevPlan 116 B6 T2)
# · Remove if: fail-path semantics change
@ldd_trajectory
def test_deploy_context_broken_yaml_failed(caplog, tmp_path, monkeypatch):
    """deploy_context with unreadable node.yaml → DeployResult.failed=1 + readable log."""
    monkeypatch.delenv("CONTEXT", raising=False)
    caplog.set_level(logging.INFO)

    missing = str(tmp_path / "missing" / "node.yaml")
    result = cd.deploy_context(
        core_dir="/nonexistent/core",
        node_name="test-node",
        node_yaml=missing,
        context="",
    )
    assert result.failed == 1
    assert any("CONTEXT not set" in r.message for r in caplog.records), "expected readable CONTEXT-not-set log"
    logger.critical("[IMP:9][test] deploy_context broken yaml → failed=1 with readable log — OK")


# endregion Tests: deploy_context context resolution (DevPlan 116 B6 T2)


# ═══════════════════════════════════════════════════════════════════
# region Tests: A5 — cert_orchestrator normal import (DevPlan 118 A5)
# ═══════════════════════════════════════════════════════════════════


# 🧪 TRAP[TEST] · Regression · A5 — deploy_context uses the REAL cert_orchestrator module
# · Scenario: certs_fn (DI) вызывается с невалидными доменами; identity-check: cd.orchestrate_certs
# ·   — реальная функция cert_orchestrator (A5: importlib-обход отсутствует)
# · Last fail: importlib.util.spec_from_file_location — обход системы импорта (тихий полом)
# · Remove if: cert orchestration moves out of deploy_context
@ldd_trajectory
def test_deploy_context_uses_real_cert_orchestrator(caplog, node_yaml_file):
    """deploy_context must orchestrate certs via the real cert_orchestrator module (A5)."""
    import core.internal.bootstrap.cert_orchestrator as cert_mod

    caplog.set_level(logging.INFO)

    # A5 core: identity — context_deployer references the REAL module function, not an importlib copy.
    assert cd.orchestrate_certs is cert_mod.orchestrate_certs, (
        "A5 FAIL: context_deployer.orchestrate_certs must be the real cert_orchestrator function "
        "(importlib spec_from_file_location creates an unrelated copy)"
    )

    calls: list = []

    def _fake_certs(domains, script, secrets_env, **kw):
        calls.append((domains, script))
        return SimpleNamespace(domains={})

    result = cd.deploy_context(
        core_dir="/nonexistent/core",
        node_name="test-node",
        node_yaml=node_yaml_file,
        context="test-ctx",  # explicit arg приоритетен (E7: _resolve_context не читает env)
        certs_fn=_fake_certs,  # DI: вместо monkeypatch cd.orchestrate_certs
        cert_validity_fn=lambda *_, **__: False,  # DI: все домены invalid → orchestrate
        deploy_projects_fn=_noop_deploy_projects,
    )

    assert calls, "orchestrate_certs must be invoked when context domains lack valid certs"
    assert result.failed == 0
    logger.critical("[IMP:9][test] deploy_context cert orchestration via real module — OK")


# 🧪 TRAP[TEST] · NEGATIVE (R5) · A5 — importlib bypass removed from context_deployer
# · Scenario: AST-scan — spec_from_file_location + cross-module private _is_cert_valid must be absent
# · Last fail: context_deployer.py:645-653 importlib.util.spec_from_file_location("cert_orchestrator", ...)
# · Remove if: importlib bypass is legitimately reintroduced
@ldd_trajectory
def test_deploy_context_no_importlib_bypass_negative(caplog):
    """R5 negative: importlib bypass and private-cert-API usage removed from context_deployer (A5)."""
    import ast

    caplog.set_level(logging.INFO)

    src = Path(cd.__file__).read_text(encoding="utf-8")
    tree = ast.parse(src)
    forbidden: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Attribute) and node.attr == "spec_from_file_location":
            forbidden.append(f"{node.lineno}: spec_from_file_location")
        if isinstance(node, ast.Attribute) and node.attr == "_is_cert_valid":
            forbidden.append(f"{node.lineno}: _is_cert_valid (private API cross-module)")

    assert not forbidden, f"A5 FAIL: importlib bypass still present: {', '.join(forbidden)}"
    assert "orchestrate_certs" in src, "A5: normal import of cert_orchestrator.orchestrate_certs required"
    logger.critical("[IMP:9][test] importlib bypass removed / normal import present — OK")


# endregion Tests: A5 — cert_orchestrator normal import (DevPlan 118 A5)


# ═══════════════════════════════════════════════════════════════════
# region Tests: deploy_context_projects
# ═══════════════════════════════════════════════════════════════════


# 🧪 TRAP[TEST] · Regression · deploy skips already-healthy projects (idempotent)
# · Scenario: health_fn returns True → project skipped
# · Last fail: N/A (new test)
# · Remove if: idempotent skip logic changes
@ldd_trajectory
def test_idempotent_skip_healthy(caplog, node_yaml_file):
    """deploy_context_projects should skip healthy projects."""
    results = cd.deploy_context_projects(
        node_yaml_file,
        "test-ctx",
        health_fn=lambda _: True,  # DI: вместо monkeypatch cd._is_project_healthy
        audit_fn=lambda _, __: None,  # DI: вместо monkeypatch cd._write_audit
    )
    assert len(results) == 2
    for r in results:
        assert r.status == "skipped"
        assert r.channel == "skip"
    logger.critical("[IMP:9][test] Idempotent skip — healthy projects not re-deployed")


# ── REMOVED (DevPlan 091 Wave A, AC4) ─────────────────────────────────────────
# The following bypass-path tests were removed together with context_deployer._deploy_single_project():
#   - test_ghcr_pull_success              (tested _shared_retry_pull + ghcr channel)
#   - test_ghcr_fails_fallback_build      (tested retry_pull→build fallback)
#   - test_health_gate_timeout            (tested _shared_healthcheck_poll unhealthy path)
#   - test_non_fatal_continues_on_failure (tested bypass non-fatal propagation)
# Rationale: these asserted behavior of the parallel pull→build→up→healthcheck path that
# bypassed DeployOrchestrator. That path was deleted (AC4 cleanup); equivalent coverage now
# lives in tests/unit/test_orchestrator.py (DeployOrchestrator unit tests) and
# tests/unit/test_deploy_single_orchestrator.py (routing invariant tests).
# ⚠️ TRAP[DECISION] · 2026-07-30 · MED · Removed bypass-path unit tests with the bypass code
# · Rejected: keep tests as xfail markers (risk: dead xfail markers accumulate, hide regressions)
# · Reason: tests of deleted code are dead tests (R1 Test Honesty). Coverage of deploy semantics
#   is preserved via test_orchestrator.py + test_deploy_single_orchestrator.py.
# · Rev: if a parallel deploy path is reintroduced with Architect sign-off — recreate equivalent tests.


# endregion Tests: deploy_context_projects


# ═══════════════════════════════════════════════════════════════════
# region Tests: _ensure_bootstrap_compose
# ═══════════════════════════════════════════════════════════════════


# 🧪 TRAP[TEST] · NEGATIVE (R5) · _ensure_bootstrap_compose конвенция проектов (ночная сессия 141, B9)
# · Scenario: оригинальная форма — сервис stuba `{name}-proxy` не совпадал с service=project_name
# ·   DeployOrchestrator (orchestrator.py:334) → «no such service» → first-deploy FATAL;
# ·   healthcheck curl (нет в nginx:alpine), host-порт {port} (конфликт), нет proxy-net.
# · Last fail: 2026-08-06 холодный бутстрап — tronyx-site/botanika/roadmap пул «no such service»
# · Remove if: конвенция реальных compose проектов (сервис=имя, proxy-net) изменится
@ldd_trajectory
def test_bootstrap_compose_generation(caplog, tmp_path):
    """_ensure_bootstrap_compose should create docker-compose.yml with nginx:alpine, correct labels, and healthcheck."""
    project = cd.ProjectInfo(
        name="test-webapp",
        repo="https://github.com/test/webapp",
        type="backend",
        domain="webapp.test.example.com",
        context="test-ctx",
    )
    project_dir = str(tmp_path / "projects" / "test-webapp")

    result = cd._ensure_bootstrap_compose(project_dir, project)

    assert result is True, "_ensure_bootstrap_compose should return True on success"
    compose_file = tmp_path / "projects" / "test-webapp" / "docker-compose.yml"
    assert compose_file.is_file(), "docker-compose.yml should be created"

    content = compose_file.read_text(encoding="utf-8")
    assert "image: nginx:alpine" in content, "Should use nginx:alpine image"
    assert "ai-platform.bootstrap=true" in content, "Should have ai-platform.bootstrap label"
    assert "ai-platform.project=test-webapp" in content, "Should have ai-platform.project label"
    assert "healthcheck:" in content, "Should have healthcheck section"
    assert "restart: unless-stopped" in content, "Should have restart policy"
    assert "GENERATED-STUB" in content, "Should indicate it's a generated stub"
    # B9: сервис = project.name (совпадает с service=project_name деплоя и upstream nginx)
    assert "  test-webapp:" in content, "B9-R5 FAIL: сервис stuba обязан называться как проект"
    assert "-proxy" not in content, "B9-R5 FAIL: -proxy суффикс ломает docker compose pull <project>"
    # B9: proxy-net external + своя сеть (nginx-overlay резолвит tronyx-site:80)
    assert "proxy-net" in content and "external: true" in content, (
        "B9-R5 FAIL: stub обязан подключаться к proxy-net (внешняя сеть nginx)"
    )
    # B9: healthcheck wget (в nginx:alpine нет curl)
    assert "wget" in content, "B9-R5 FAIL: healthcheck должен использовать wget (nginx:alpine без curl)"
    assert "ports:" not in content, "B9-R5 FAIL: host-порты в stub конфликтуют между проектами"
    logger.critical(
        "[IMP:9][test] Bootstrap compose generated for %s — image=nginx:alpine, label=ai-platform.bootstrap=true",
        project.name,
    )


# 🧪 TRAP[TEST] · Regression · _ensure_bootstrap_compose does NOT overwrite existing docker-compose.yml
# · Scenario: docker-compose.yml already exists → returns True, does NOT modify file
# · Last fail: N/A (new test)
# · Remove if: idempotent skip logic changes
@ldd_trajectory
def test_bootstrap_compose_idempotent(caplog, tmp_path):
    """_ensure_bootstrap_compose should NOT overwrite an existing docker-compose.yml."""
    project = cd.ProjectInfo(
        name="test-webapp",
        repo="https://github.com/test/webapp",
        type="backend",
        domain="webapp.test.example.com",
        context="test-ctx",
    )
    project_dir = str(tmp_path / "projects" / "test-webapp")
    Path(project_dir).mkdir(exist_ok=True, parents=True)

    # Create a pre-existing docker-compose.yml with different content
    existing_content = "# REAL DELIVERY — this should NOT be overwritten\nversion: '3.8'\nservices:\n  webapp:\n    image: custom:latest\n"
    compose_file = Path(project_dir) / "docker-compose.yml"
    with Path(compose_file).open("w", encoding="utf-8") as f:
        f.write(existing_content)

    result = cd._ensure_bootstrap_compose(project_dir, project)

    assert result is True, "_ensure_bootstrap_compose should return True (skip) when compose exists"
    # Verify the file was NOT overwritten
    content = Path(compose_file).read_text(encoding="utf-8")
    assert content == existing_content, "Existing docker-compose.yml should NOT be overwritten"
    assert "REAL DELIVERY" in content, "Original content should be preserved intact"
    assert "nginx:alpine" not in content, "New content should NOT appear"
    logger.critical("[IMP:9][test] Bootstrap compose idempotent — existing file preserved unchanged")


# endregion Tests: _ensure_bootstrap_compose


# ── _step_nginx_reload tests (HOLE-1, DevPlan 119 F4) ─────────────────────────


# region FUNC_test_step_nginx_reload_success
## @purpose — _step_nginx_reload() успешный: делегирует в nginx_reload_fn (DI),
##            non-fatal — ошибки (OSError/FileNotFoundError) → WARN, не raise.
## @io — ⇥ caplog → ⎋ None (asserts делегирование + no-raise)
## @complexity — O(1)
## @invariants
##   - nginx_reload_fn вызывается без аргументов (контракт shared docker_compose.nginx_reload)
##   - Ошибка reload → перехвачена (non-fatal контракт D6)
# 🧪 TRAP[TEST] · DevPlan 119 F4 (HOLE-1) · _step_nginx_reload success
# · Last fail: N/A — _step_nginx_reload (context_deployer:824) не покрыт тестами
# · Remove if: _step_nginx_reload контракт меняется
def test_step_nginx_reload_success(caplog) -> None:
    """_step_nginx_reload успешно вызывает nginx_reload (non-fatal)."""
    caplog.set_level(logging.DEBUG)

    called: dict = {}

    def _fake_nginx_reload():
        called["ok"] = True
        logger.info("[IMP:9][test][_step_nginx_reload] nginx_reload вызван")

    cd._step_nginx_reload(nginx_reload_fn=_fake_nginx_reload)

    logger.info("--- LDD TRAJECTORY (IMP:7-10) ---")
    for record in list(caplog.records):
        if "[IMP:" in record.message:
            logger.info("%s", record.message)
    logger.info("--- END LDD TRAJECTORY ---")
    assert called.get("ok"), "nginx_reload_fn должен быть вызван"
    found_imp9 = any("[IMP:9]" in r.message for r in caplog.records)
    assert found_imp9, "Critical LDD Error: No IMP:9 business logic log found"


# endregion FUNC_test_step_nginx_reload_success


# region FUNC_test_step_nginx_reload_failure_nonfatal
## @purpose — R5 negative (DevPlan 119 F4): nginx_reload_fn бросает OSError (контейнер недоступен)
##            → _step_nginx_reload ловит и логирует WARN (non-fatal), НЕ raise.
## @io — ⇥ caplog → ⎋ None (asserts no-raise при OSError)
## @complexity — O(1)
## @invariants
##   - OSError от reload → перехвачен (non-fatal контракт D6)
# 🧪 TRAP[TEST] · NEGATIVE (R5) · _step_nginx_reload failure — DevPlan 119 F4
# · Last fail: необработанный OSError из nginx reload падал бы deploy_context
# · Remove if: _step_nginx_reload становится fatal
def test_step_nginx_reload_failure_nonfatal(caplog) -> None:
    """R5: OSError от nginx_reload → перехвачен (non-fatal), no-raise."""
    caplog.set_level(logging.DEBUG)

    def _raise_oserror():
        msg = "docker exec failed — container not reachable"
        raise OSError(msg)

    # Должно пройти без исключения (non-fatal контракт D6)
    cd._step_nginx_reload(nginx_reload_fn=_raise_oserror)

    logger.info("--- LDD TRAJECTORY (IMP:7-10) ---")
    for record in list(caplog.records):
        if "[IMP:" in record.message:
            logger.info("%s", record.message)
    logger.info("--- END LDD TRAJECTORY ---")
    logger.info("[IMP:9][test] R5 PASS: nginx reload OSError handled (non-fatal)")


# endregion FUNC_test_step_nginx_reload_failure_nonfatal


# ═══════════════════════════════════════════════════════════════════
# Stub guard (DevPlan 153 T6, N1) — stub контейнер/compose не маскирует проект
# ═══════════════════════════════════════════════════════════════════


# region FUNC_test_is_project_healthy_stub_aware
# 🧪 TRAP[TEST] · Regression · _is_project_healthy: stub-контейнер НЕ healthy, реальный — healthy
# · Scenario: stub_detector_fn=True → False (healthcheck_poll НЕ вызывается, stub не скипается, N1);
# ·   stub_detector_fn=False → True (стандартный healthcheck_poll путь)
# · Last fail: RC-прогон 2026-08-12 — stub nginx:alpine проходил healthcheck → deploy-context SKIP (N1)
# · Remove if: stub-guard логика меняется
@ldd_trajectory
@pytest.mark.parametrize(
    ("stub_detected", "expected"),
    [
        pytest.param(False, True, id="real-container"),
        pytest.param(True, False, id="stub-container"),
    ],
)
def test_is_project_healthy_stub_aware(caplog, stub_detected, expected):
    """Stub container must NOT be healthy for skip-logic; real container uses standard healthcheck_poll."""

    def _poll(*_, **__):
        if stub_detected:
            pytest.fail("healthcheck_poll не вызывается для stub")
        return "healthy"

    assert (
        cd._is_project_healthy(
            "test-proj",
            stub_detector_fn=lambda _: stub_detected,
            healthcheck_poll_fn=_poll,
        )
        is expected
    )
    logger.critical("[IMP:9][test] _is_project_healthy stub_detected=%s → %s — guard active", stub_detected, expected)


# endregion FUNC_test_is_project_healthy_stub_aware


# region FUNC_test_stub_compose_awaiting_deploy
# 🧪 TRAP[TEST] · Regression · GENERATED-STUB compose → status=awaiting_deploy (не deployed/skipped)
# · Scenario: docker-compose.yml с GENERATED-STUB → _deploy_single_project_via_orchestrator
# ·   возвращает awaiting_deploy БЕЗ вызова DeployOrchestrator (orchestrator_deploy_fn не вызывается)
# · Last fail: RC-прогон 2026-08-12 — stub маскировался как «здоровый проект» (N1)
# · Remove if: stub-guard логика меняется
@ldd_trajectory
def test_stub_compose_awaiting_deploy(caplog, tmp_path):
    """GENERATED-STUB docker-compose.yml → status=awaiting_deploy, channel=stub."""
    project = cd.ProjectInfo(
        name="test-stub-proj",
        repo="https://github.com/test/stub-proj",
        type="frontend",
        domain="stub.example.com",
        context="test-ctx",
    )
    project_dir = tmp_path / "projects" / "test-stub-proj"
    project_dir.mkdir(parents=True)
    (project_dir / "docker-compose.yml").write_text(
        "# GENERATED-STUB: Bootstrap reverse proxy. Replaced by CI receive (dispatch-канал).\n"
        "services:\n  test-stub-proj:\n    image: nginx:alpine\n",
        encoding="utf-8",
    )

    # DeployOrchestrator НЕ должен вызываться для stub (orchestrator_deploy_fn → fail if called)
    def _must_not_deploy(**kw):
        pytest.fail("DeployOrchestrator не должен вызываться для stub")

    result = cd._deploy_single_project_via_orchestrator(
        project,
        str(tmp_path / "projects"),
        health_fn=lambda _: False,
        orchestrator_deploy_fn=_must_not_deploy,
    )

    assert result.status == "awaiting_deploy"
    assert result.channel == "stub"
    assert result.health == "awaiting_deploy"
    logger.critical("[IMP:9][test] Stub compose → awaiting_deploy — проект не маскируется")


# endregion FUNC_test_stub_compose_awaiting_deploy


# region FUNC_test_real_compose_deploys
# 🧪 TRAP[TEST] · Regression · реальный compose (без GENERATED-STUB) → деплой через DeployOrchestrator
# · Scenario: docker-compose.yml реальной доставки → orchestrator_deploy_fn (DI) вызывается
# · Last fail: N/A (new test, DevPlan 153 T6 — регрессия на реальные проекты)
# · Remove if: stub-guard логика меняется
@ldd_trajectory
def test_real_compose_deploys(caplog, tmp_path):
    """Real docker-compose.yml (no GENERATED-STUB) proceeds to DeployOrchestrator."""
    project = cd.ProjectInfo(
        name="test-real-proj",
        repo="https://github.com/test/real-proj",
        type="frontend",
        domain="real.example.com",
        context="test-ctx",
    )
    project_dir = tmp_path / "projects" / "test-real-proj"
    project_dir.mkdir(parents=True)
    (project_dir / "docker-compose.yml").write_text(
        "services:\n  test-real-proj:\n    image: ghcr.io/test/real-proj:latest\n", encoding="utf-8"
    )

    def fake_deploy(**kw):
        return SimpleNamespace(is_success=lambda: True, healthcheck_status="healthy", error_info=None)

    result = cd._deploy_single_project_via_orchestrator(
        project,
        str(tmp_path / "projects"),
        health_fn=lambda _: False,
        orchestrator_deploy_fn=fake_deploy,
    )

    assert result.status == "deployed"
    assert result.channel == "orchestrator"
    logger.critical("[IMP:9][test] Real compose → deployed via DeployOrchestrator — stub guard не мешает")


# endregion FUNC_test_real_compose_deploys


# ═══════════════════════════════════════════════════════════════════
# REF-0103 — single-shot cold-skip gate
# ═══════════════════════════════════════════════════════════════════


# region FUNC_test_cold_skip_gate_single_shot
# 🧪 TRAP[TEST] · Regression · REF-0103 · cold-skip gate = ОДНА проверка (attempts=1)
# · Scenario: _is_project_healthy(single_shot=True) → healthcheck_poll получает attempts=1
# ·   (отсутствующий проект не сжигает полное окно поллинга в idempotent-skip проверке);
# ·   default-вызов сохраняет deadline-driven режим (attempts не передаётся).
# · Last fail: REF-0103 — skip-gate поллил 60s на каждый cold-проект контекста.
# · Remove if: single-shot семантика gate меняется
@ldd_trajectory
@pytest.mark.parametrize(
    ("single_shot", "expected_attempts_kwarg"),
    [
        pytest.param(True, 1, id="single-shot-gate"),
        pytest.param(False, None, id="default-deadline-driven"),
    ],
)
def test_is_project_healthy_single_shot_attempts(caplog, single_shot, expected_attempts_kwarg):
    """single_shot=True → healthcheck_poll(attempts=1); default → прежнее поведение."""
    captured: dict = {}

    def _poll(name, **kwargs):
        captured.update(kwargs)
        return "unhealthy"

    kwargs = {"single_shot": True} if single_shot else {}
    result = cd._is_project_healthy(
        "test-proj",
        stub_detector_fn=lambda _: False,
        healthcheck_poll_fn=_poll,
        **kwargs,
    )

    assert result is False
    assert captured.get("attempts") == expected_attempts_kwarg, (
        f"REF-0103: single_shot={single_shot} → attempts kwarg {expected_attempts_kwarg}, got {captured}"
    )
    logger.critical(
        "[IMP:9][test][REF-0103] single_shot=%s → healthcheck_poll kwargs=%s",
        single_shot,
        captured,
    )


# 🧪 TRAP[TEST] · Regression · REF-0103 · default-путь skip-gate использует single-shot
# · Scenario: _deploy_single_project_via_orchestrator БЕЗ injected health_fn → внутренний
# ·   gate вызывает _is_project_healthy(name, single_shot=True) (проверяется monkeypatch'ом
# ·   cd._is_project_healthy — module-global lookup в замыкании gate).
# · Remove if: gate-wiring меняется
@ldd_trajectory
def test_deploy_gate_uses_single_shot_by_default(caplog, tmp_path):
    """Default cold-skip gate → _is_project_healthy(..., single_shot=True)."""
    project = cd.ProjectInfo(
        name="gate-proj",
        repo="https://github.com/test/gate-proj",
        type="frontend",
        domain="gate.example.com",
        context="test-ctx",
    )
    project_dir = tmp_path / "projects" / "gate-proj"
    project_dir.mkdir(parents=True)
    # Stub compose → выход awaiting_deploy ДО orchestrator; нам важен только вызов gate.
    (project_dir / "docker-compose.yml").write_text(
        "# GENERATED-STUB: Bootstrap reverse proxy.\nservices:\n  gate-proj:\n    image: nginx:alpine\n",
        encoding="utf-8",
    )

    seen: dict = {}

    def _fake_is_healthy(name, **kwargs):
        seen["name"] = name
        seen.update(kwargs)
        return False

    orig = cd._is_project_healthy
    cd._is_project_healthy = _fake_is_healthy
    try:
        result = cd._deploy_single_project_via_orchestrator(
            project,
            str(tmp_path / "projects"),
            orchestrator_deploy_fn=lambda **_kw: pytest.fail("stub должен выйти до orchestrator"),
        )
    finally:
        cd._is_project_healthy = orig

    assert seen.get("name") == "gate-proj"
    assert seen.get("single_shot") is True, f"REF-0103 FAIL: gate без single_shot: {seen}"
    assert result.status == "awaiting_deploy"
    logger.critical("[IMP:9][test][REF-0103] default gate → single_shot=True OK")


# endregion FUNC_test_cold_skip_gate_single_shot


# ═══════════════════════════════════════════════════════════════════
# _step_vhosts tests (холодный bootstrap R6 — rc-capture + retry + файл-верификация)
# ═══════════════════════════════════════════════════════════════════


class _VhostRunner:
    """Scripted CommandRunner для _step_vhosts: rc-последовательность + stderr.

    ## @purpose — DI-канал runner (W4b): _step_vhosts вызывает runner.run(cmd, timeout=60,
    ##            check=False) — fake возвращает CompletedProcess по заданной rc-последовательности.
    ## @io — ⇥ rc_sequence: list[int], stderr: str → ⎋ CompletedProcess (scripted)
    ## @complexity — O(1)
    """

    def __init__(self, rc_sequence: list[int], stderr: str = "") -> None:
        self.calls = 0
        self._rcs = list(rc_sequence)
        self._stderr = stderr

    def run(self, cmd, *, timeout=60, check=False, non_fatal=False, fatal_rc=()):  # ruff: ignore[ARG002]
        self.calls += 1
        rc = self._rcs.pop(0) if self._rcs else 0
        return subprocess.CompletedProcess(list(cmd), rc, "", self._stderr)


class _VhostFacts:
    """EnvironmentFacts-fake для _step_vhosts: add-vhost.sh существует (или нет), остальное — нет.

    ## @purpose — path_isfile(path) → True ТОЛЬКО для internal/scaffold/add-vhost.sh при
    ##            script_exists=True; domain_verifier.py/cert-paths — False (другие шаги skip).
    ## @complexity — O(1)
    """

    def __init__(self, script_exists: bool = True) -> None:
        self._script_exists = script_exists

    def path_isfile(self, path) -> bool:
        return self._script_exists and str(path).endswith("internal/scaffold/add-vhost.sh")


# region FUNC_test_step_vhosts_rc_fail_twice_returns_false
## @purpose — R6 (холодный bootstrap): rc!=0 дважды → _step_vhosts возвращает False
##            (retry исчерпан) + IMP:10 лог; success-лог «Vhosts rendered» ОТСУТСТВУЕТ.
## @io — ⇥ caplog, tmp_path, monkeypatch → ⎋ None (asserts bool + logs)
## @complexity — O(1)
## @invariants
##   - Ровно 2 вызова runner.run (первый + ОДИН retry)
##   - False = неуспех-пропагация по паттерну шага (deploy_context → result.failed)
# 🧪 TRAP[TEST] · Regression · _step_vhosts rc!=0 дважды → False (R6 инцидент)
# · Scenario: add-vhost.sh --render-all rc=1 дважды (retry исчерпан) → return False,
# ·   IMP:10 лог, «Vhosts rendered» (ложный success) отсутствует
# · Last fail: 2026-08-31 холодный bootstrap — render ТИХО падал (rc!=0 не проверялся),
# ·   deploy отчитывался успехом, converge R6 FAIL ×3 (vhost not found)
# · Remove if: vhost render перестаёт репортить неуспех через возврат шага
def test_step_vhosts_rc_fail_twice_returns_false(caplog, tmp_path, monkeypatch) -> None:
    """_step_vhosts rc!=0 дважды → False + IMP:10, без ложного success-лога."""
    monkeypatch.setenv("NODE_CONFIGS_DIR", str(tmp_path / "node-configs"))
    caplog.set_level(logging.DEBUG)
    runner = _VhostRunner([1, 1], stderr="render exploded")
    ok = cd._step_vhosts(str(tmp_path / "core"), "test-node", runner=runner, facts=_VhostFacts())
    assert ok is False
    assert runner.calls == 2, "rc!=0 → ровно ОДИН retry (2 вызова)"
    msgs = [r.message for r in caplog.records]
    assert any("IMP:10" in m and "Vhost render FAILED" in m for m in msgs), "IMP:10 при исчерпанном retry"
    assert not any("Vhosts rendered" in m for m in msgs), "ложный success-лог запрещён (R6)"
    for record in list(caplog.records):
        if "[IMP:" in record.message:
            logger.info("%s", record.message)
    logger.info("[IMP:9][test] _step_vhosts rc!=0 дважды → False — R6 propagation OK")


# endregion FUNC_test_step_vhosts_rc_fail_twice_returns_false


# region FUNC_test_step_vhosts_rc_ok_no_files_returns_false
## @purpose — R6: rc==0 но в overlays/nginx нет ни одного *.conf → тот же неуспех-путь
##            (retry → IMP:10 + False). «Лог success без файлов на диске» невозможен.
## @io — ⇥ caplog, tmp_path, monkeypatch → ⎋ None (asserts bool + calls)
## @complexity — O(1)
## @invariants — rc==0 без файлов = неуспех (не маскируется success-логом)
# 🧪 TRAP[TEST] · Regression · _step_vhosts rc==0 без файлов → False (R6 файл-верификация)
# · Scenario: render-all rc=0 дважды, overlay_dir пуст/отсутствует → return False + IMP:10
# · Last fail: 2026-08-31 — rc!=0 не проверялся; здесь граничный случай «успех без файлов»
# · Remove if: файл-верификация vhost-рендера убирается
def test_step_vhosts_rc_ok_no_files_returns_false(caplog, tmp_path, monkeypatch) -> None:
    """_step_vhosts rc==0 но без *.conf на диске → False (пустой overlay = неуспех)."""
    monkeypatch.setenv("NODE_CONFIGS_DIR", str(tmp_path / "node-configs"))
    caplog.set_level(logging.DEBUG)
    runner = _VhostRunner([0, 0])
    ok = cd._step_vhosts(str(tmp_path / "core"), "test-node", runner=runner, facts=_VhostFacts())
    assert ok is False
    assert runner.calls == 2, "rc==0 без файлов → retry тоже выполняется"
    msgs = [r.message for r in caplog.records]
    assert any("IMP:10" in m and "Vhost render FAILED" in m for m in msgs)
    assert any("no *.conf" in m for m in msgs), "причина — отсутствие .conf в overlay"
    for record in list(caplog.records):
        if "[IMP:" in record.message:
            logger.info("%s", record.message)
    logger.info("[IMP:9][test] _step_vhosts rc==0 без файлов → False — overlay-guard OK")


# endregion FUNC_test_step_vhosts_rc_ok_no_files_returns_false


# region FUNC_test_step_vhosts_rc_fail_then_ok_returns_true
## @purpose — R6 transient: первый rc!=0 → retry успешен (rc=0 + файлы на диске) → True.
## @io — ⇥ caplog, tmp_path, monkeypatch → ⎋ None (asserts True + 2 вызова)
## @complexity — O(1)
## @invariants — Транзиентный отказ переживается одним retry
# 🧪 TRAP[TEST] · Regression · _step_vhosts rc!=0 → retry rc=0 + файлы → True
# · Scenario: первый запуск rc=1 (transient), второй rc=0; .conf существует → True
# · Last fail: N/A (new test)
# · Remove if: retry-логика vhost-рендера меняется
def test_step_vhosts_rc_fail_then_ok_returns_true(caplog, tmp_path, monkeypatch) -> None:
    """_step_vhosts: transient rc!=0 переживается retry'ем (rc=0 + файл) → True."""
    monkeypatch.setenv("NODE_CONFIGS_DIR", str(tmp_path / "node-configs"))
    caplog.set_level(logging.DEBUG)
    overlay = tmp_path / "node-configs" / "test-node" / "overlays" / "nginx"
    overlay.mkdir(parents=True)
    (overlay / "webapp.example.com.conf").write_text("# GENERATED by vhost_renderer.py\n", encoding="utf-8")
    runner = _VhostRunner([1, 0])
    ok = cd._step_vhosts(str(tmp_path / "core"), "test-node", runner=runner, facts=_VhostFacts())
    assert ok is True
    assert runner.calls == 2, "rc!=0 → retry выполнен"
    for record in list(caplog.records):
        if "[IMP:" in record.message:
            logger.info("%s", record.message)
    logger.info("[IMP:9][test] _step_vhosts transient rc!=0 → retry rc=0 + файл → True")


# endregion FUNC_test_step_vhosts_rc_fail_then_ok_returns_true


# region FUNC_test_step_vhosts_rc_ok_with_files_returns_true
## @purpose — R6 happy path: rc=0 + ≥1 *.conf в overlay → True + IMP:9 «Vhosts rendered (N .conf)».
## @io — ⇥ caplog, tmp_path, monkeypatch → ⎋ None (asserts True + count-лог)
## @complexity — O(1)
## @invariants — success-лог несёт факт (число отрендеренных .conf)
# 🧪 TRAP[TEST] · Regression · _step_vhosts rc=0 + файлы → True (happy path)
# · Scenario: render-all rc=0, overlay содержит webapp.example.com.conf → True, 1 вызов
# · Last fail: N/A (new test)
# · Remove if: success-критерий vhost-рендера меняется
@ldd_trajectory
def test_step_vhosts_rc_ok_with_files_returns_true(caplog, tmp_path, monkeypatch) -> None:
    """_step_vhosts: rc=0 с файлами на диске → True, IMP:9 с числом .conf."""
    monkeypatch.setenv("NODE_CONFIGS_DIR", str(tmp_path / "node-configs"))
    overlay = tmp_path / "node-configs" / "test-node" / "overlays" / "nginx"
    overlay.mkdir(parents=True)
    (overlay / "webapp.example.com.conf").write_text("# GENERATED by vhost_renderer.py\n", encoding="utf-8")
    runner = _VhostRunner([0])
    ok = cd._step_vhosts(str(tmp_path / "core"), "test-node", runner=runner, facts=_VhostFacts())
    assert ok is True
    assert runner.calls == 1, "rc=0 с файлами → без retry"
    assert any("Vhosts rendered for node=test-node (1 .conf" in r.message for r in caplog.records)
    logger.critical("[IMP:9][test] _step_vhosts rc=0 + файлы → True — count-лог OK")


# endregion FUNC_test_step_vhosts_rc_ok_with_files_returns_true


# region FUNC_test_step_vhosts_script_missing_returns_true
## @purpose — Отсутствие add-vhost.sh → skip (True), НЕ неуспех: рендерить нечего.
## @io — ⇥ caplog, tmp_path → ⎋ None (asserts True + 0 subprocess-вызовов)
## @complexity — O(1)
# 🧪 TRAP[TEST] · Regression · _step_vhosts script missing → True (skip)
# · Scenario: facts.path_isfile(add-vhost.sh)=False → skip, runner не вызывается
# · Last fail: N/A (new test)
# · Remove if: skip-семантика отсутствия скрипта меняется
def test_step_vhosts_script_missing_returns_true(caplog, tmp_path) -> None:
    """_step_vhosts без add-vhost.sh → True (skip — нечего рендерить, не неуспех)."""
    caplog.set_level(logging.DEBUG)
    runner = _VhostRunner([0])
    ok = cd._step_vhosts(str(tmp_path / "core"), "test-node", runner=runner, facts=_VhostFacts(script_exists=False))
    assert ok is True
    assert runner.calls == 0, "script отсутствует → subprocess не вызывается"
    for record in list(caplog.records):
        if "[IMP:" in record.message:
            logger.info("%s", record.message)
    logger.info("[IMP:9][test] _step_vhosts script missing → True (skip)")


# endregion FUNC_test_step_vhosts_script_missing_returns_true


# region FUNC_test_deploy_context_vhost_failure_sets_failed
## @purpose — Интеграция R6: неуспех _step_vhosts (rc!=0 дважды) агрегируется в
##            ContextDeployResult.failed (паттерн _step_deploy_projects — канонический add()).
##            Exit deploy-context = 1 → фаза не отчитывается успехом.
## @io — ⇥ caplog, tmp_path, monkeypatch → ⎋ None (asserts result.failed == 1)
## @complexity — O(1)
## @invariants
##   - vhost-неуспех НЕ маскируется: result.failed == 1 при успешных проектах (0)
##   - В results появляется failed-запись с channel="render"
# 🧪 TRAP[TEST] · Regression · deploy_context vhost failure → result.failed=1
# · Scenario: _step_vhosts rc=1 дважды → deploy_context добавляет failed-запись →
# ·   result.failed == 1 (CLI exit 1 — деплой-фаза не отчитывается успехом)
# · Last fail: 2026-08-31 — bootstrap отчитывался успехом при ТИХОМ падении рендера
# · Remove if: пропагация vhost-неуспеха в result.failed меняется
def test_deploy_context_vhost_failure_sets_failed(caplog, tmp_path, monkeypatch) -> None:
    """deploy_context: vhost render rc!=0 дважды → result.failed == 1 (exit ≠ 0)."""
    monkeypatch.delenv("CONTEXT", raising=False)
    monkeypatch.setenv("NODE_CONFIGS_DIR", str(tmp_path / "node-configs"))
    caplog.set_level(logging.DEBUG)
    yaml_path = tmp_path / "node.yaml"
    yaml_path.write_text("node:\n  name: test-node\ncontexts:\n  - name: test-ctx\n", encoding="utf-8")
    runner = _VhostRunner([1, 1], stderr="render exploded")
    result = cd.deploy_context(
        core_dir=str(tmp_path / "core"),
        node_name="test-node",
        node_yaml=str(yaml_path),
        context="test-ctx",  # explicit arg приоритетен (E7: _resolve_context не читает env)
        runner=runner,
        facts=_VhostFacts(),
        deploy_projects_fn=_noop_deploy_projects,
        nginx_reload_fn=lambda: None,
    )
    assert result.failed == 1, f"vhost-неуспех обязан дать failed=1: {result.to_dict()}"
    assert any(r.status == "failed" and r.channel == "render" for r in result.results), (
        "в results обязана быть failed-запись vhost-рендера (паттерн _step_deploy_projects)"
    )
    for record in list(caplog.records):
        if "[IMP:" in record.message:
            logger.info("%s", record.message)
    logger.info("[IMP:9][test] deploy_context vhost failure → result.failed=1 — exit≠0 OK")


# endregion FUNC_test_deploy_context_vhost_failure_sets_failed
