# GREP_SUMMARY: test-project-payload-delivery, bootstrap, deliver, pending-projects, no_local_source, awaiting-deploy, DI, tmp_path, exit-code, DevPlan-017
# STRUCTURE: ▶ tmp_path node.yaml (contexts[0].name + node.host) + operator project dirs → DI deliver_fn (0 patches) → ○ per-project outcome (delivered/skipped/failed) → ⊕ DeliverySummary asserts → ◇ delivery_exit_code pure → ⎋ LDD trajectory
# region MODULE_CONTRACT
## @purpose  Unit tests for core/internal/deploy/project_payload_delivery.py — локальная
##           фаза bootstrap (P0, DevPlan 017): доставка payload'ов проектов контекста на ноду
##           после успешного SSH-exec remote lifecycle init. Критерий владельца: голая нода +
##           ОДНА команда make bootstrap-node завершается при ЖИВЫХ проектах контекста.
## @scope    Tests deliver_pending_projects (per-project outcome: delivered / skipped(no_local_source)
##           / failed с продолжением остальных) + delivery_exit_code (чистый маппинг 0|2).
## @invariants
##   - 0 subprocess / 0 сети / 0 docker: deliver-канал мокается DI-параметром deliver_fn
##   - node.yaml + операторские каталоги проектов создаются в tmp_path (zero hardcode)
##   - CONTEXT env удаляется (monkeypatch.delenv) — резолв идёт из node.yaml contexts[0].name
##   - Каждый тест валидирует IMP:9 бизнес-логику через ldd_trajectory (Anti-Illusion Rule)
## @rationale DevPlan 017 (P0): φ8 (context_deployer) помечает проекты awaiting_deploy
##            (GENERATED-STUB guard, DevPlan 153 T6 N1) — реальные исходники на операторской
##            машине; фаза доставляет payload'ы тем же каналом, что make deploy-project.
## @changes  2026-08-27 | DevPlan 017 — Created
## @changes  2026-08-27 | import-linter (independence-bootstrap-deploy) — модуль перенесён в
##            core/internal/deploy/; резолв проектов — локальный _resolve_context_projects
# endregion MODULE_CONTRACT

from __future__ import annotations

import logging
from pathlib import Path

import pytest

from core.internal.deploy import project_payload_delivery as ppd
from tests._conftest.ldd import ldd_trajectory

logger = logging.getLogger(__name__)


# ═══════════════════════════════════════════════════════════════════
# region Fixtures
# ═══════════════════════════════════════════════════════════════════


@pytest.fixture
def node_yaml_file(tmp_path: Path) -> str:
    """Create a node.yaml with contexts[0].name + node.host + 2 context projects (canonical fixture)."""
    yaml_content = """\
node:
  name: test-node
  host: 10.0.0.1
  owner_key: test-owner-key
contexts:
  - name: test-ctx
projects:
  - name: site-a
    repo: https://github.com/test/site-a
    type: frontend
    domain: site-a.example.com
    context: test-ctx
  - name: site-b
    repo: https://github.com/test/site-b
    type: backend
    domain: site-b.example.com
    context: test-ctx
"""
    yaml_path = tmp_path / "node.yaml"
    yaml_path.write_text(yaml_content, encoding="utf-8")
    return str(yaml_path)


def _make_project_dir(projects_root: Path, context: str, name: str) -> Path:
    """Create an operator project dir with minimal payload files (<projects_root>/<context>/<name>)."""
    project_dir = projects_root / context / name
    project_dir.mkdir(parents=True, exist_ok=True)
    (project_dir / "docker-compose.yml").write_text("services:\n  web:\n    image: nginx\n", encoding="utf-8")
    (project_dir / "ai-platform.yaml").write_text(f"name: {name}\n", encoding="utf-8")
    return project_dir


def _ok_deliver(_name: str, _project_dir: Path) -> tuple[bool, str]:
    """DI deliver_fn: успешная доставка (rc==0 семантика orchestrator_cli deliver)."""
    return True, '{"status": "DEPLOYED"}'


# endregion Fixtures


# ═══════════════════════════════════════════════════════════════════
# region Tests: deliver_pending_projects
# ═══════════════════════════════════════════════════════════════════


# 🧪 TRAP[TEST] · DevPlan 017 · all-local-sources-present → delivered=count
# · Regression: bootstrap оставлял проекты awaiting_deploy (GENERATED-STUB guard) при живых
# ·   локальных исходниках оператора — критерий P0 «живые проекты контекста» не выполнялся
# · Scenario: node.yaml (test-ctx) + оба операторских каталога → deliver_fn (mock) успешен
# ·   для обоих → delivered=2, skipped=0, failed=0, outcome=delivered
# · Last fail: N/A (new module)
# · Remove if: фаза доставки payload'ов удаляется
@ldd_trajectory
def test_all_local_sources_delivered(caplog, tmp_path: Path, node_yaml_file: str, monkeypatch) -> None:
    """deliver_pending_projects: все локальные исходники → delivered=count (mock deliver DI)."""
    monkeypatch.delenv("CONTEXT", raising=False)
    projects_root = tmp_path / "projects"
    _make_project_dir(projects_root, "test-ctx", "site-a")
    _make_project_dir(projects_root, "test-ctx", "site-b")

    summary = ppd.deliver_pending_projects(
        node_name="test-node",
        node_yaml_path=node_yaml_file,
        projects_root=str(projects_root),
        deliver_fn=_ok_deliver,
    )

    assert summary.delivered == 2, "оба локальных проекта должны быть доставлены"
    assert summary.skipped == 0
    assert summary.failed == 0
    assert [line.project for line in summary.lines] == ["site-a", "site-b"]
    assert all(line.outcome == "delivered" for line in summary.lines)
    logger.critical("[IMP:9][test][ppd] all-local-sources → delivered=2 — OK")


# 🧪 TRAP[TEST] · DevPlan 017 · missing local dir → skipped(no_local_source)
# · Regression: отсутствующий локальный каталог оператора (проект живёт только в CI) не должен
# ·   фейлить bootstrap — доставка недоступна локально, это НЕ ошибка доставки
# · Scenario: site-a есть, site-b каталога нет → site-a delivered, site-b skipped
# ·   c detail no_local_source, failed=0 → exit-код фазы 0
# · Last fail: N/A (new module)
# · Remove if: семантика no_local_source меняется
@ldd_trajectory
def test_missing_local_dir_skipped(caplog, tmp_path: Path, node_yaml_file: str, monkeypatch) -> None:
    """deliver_pending_projects: отсутствующий локальный каталог → skipped(no_local_source) [IMP:7]."""
    monkeypatch.delenv("CONTEXT", raising=False)
    projects_root = tmp_path / "projects"
    _make_project_dir(projects_root, "test-ctx", "site-a")  # site-b каталога НЕТ

    summary = ppd.deliver_pending_projects(
        node_name="test-node",
        node_yaml_path=node_yaml_file,
        projects_root=str(projects_root),
        deliver_fn=_ok_deliver,
    )

    assert summary.delivered == 1
    assert summary.skipped == 1
    assert summary.failed == 0, "no_local_source — НЕ failure (bootstrap не фейлится)"
    skipped_line = next(line for line in summary.lines if line.project == "site-b")
    assert skipped_line.outcome == "skipped"
    assert "no_local_source" in skipped_line.detail
    assert ppd.delivery_exit_code(summary) == 0
    logger.critical("[IMP:9][test][ppd] missing local dir → skipped(no_local_source) — OK")


# 🧪 TRAP[TEST] · DevPlan 017 · one delivery raises → failed + другие продолжаются
# · Regression: исключение при доставке одного проекта не должно останавливать остальные
# ·   (per-project изоляция — та же семантика deploy_context_projects non-fatal)
# · Scenario: deliver_fn raise для site-a, успех для site-b → failed=1, delivered=1,
# ·   обе строки в summary; delivery_exit_code → 2 (строгий INIT)
# · Last fail: N/A (new module)
# · Remove if: per-project изоляция доставки меняется
@ldd_trajectory
def test_one_delivery_raises_others_continue(caplog, tmp_path: Path, node_yaml_file: str, monkeypatch) -> None:
    """deliver_pending_projects: исключение одного deliver → failed, остальные продолжаются."""
    monkeypatch.delenv("CONTEXT", raising=False)
    projects_root = tmp_path / "projects"
    _make_project_dir(projects_root, "test-ctx", "site-a")
    _make_project_dir(projects_root, "test-ctx", "site-b")

    def _flaky_deliver(name: str, _project_dir: Path) -> tuple[bool, str]:
        if name == "site-a":
            boom_msg = "ssh channel exploded"
            raise OSError(boom_msg)
        return True, '{"status": "DEPLOYED"}'

    summary = ppd.deliver_pending_projects(
        node_name="test-node",
        node_yaml_path=node_yaml_file,
        projects_root=str(projects_root),
        deliver_fn=_flaky_deliver,
    )

    assert summary.failed == 1
    assert summary.delivered == 1, "site-b должен быть доставлен несмотря на исключение site-a"
    assert [line.project for line in summary.lines] == ["site-a", "site-b"]
    failed_line = next(line for line in summary.lines if line.project == "site-a")
    assert failed_line.outcome == "failed"
    assert "raised" in failed_line.detail
    assert ppd.delivery_exit_code(summary) == 2, "≥1 failed → строгий INIT exit 2"
    logger.critical("[IMP:9][test][ppd] one delivery raises → failed=1, site-b delivered — OK")


# endregion Tests: deliver_pending_projects


# ═══════════════════════════════════════════════════════════════════
# region Tests: delivery_exit_code (чистый маппинг)
# ═══════════════════════════════════════════════════════════════════


# 🧪 TRAP[TEST] · DevPlan 017 · exit-code маппинг pure (delivered ok / fail≥1 → 2)
# · Regression: фаза доставки молча «проходила» при мёртвых проектах контекста — bootstrap
# ·   считался успешным при awaiting_deploy (счётчики печатали deployed=0 skipped=0 failed=0)
# · Scenario: чистая функция delivery_exit_code: failed=0 → 0 (delivered/skipped-no-local),
# ·   failed=1 → 2; skipped не влияет на код
# · Last fail: N/A (new module — критерий P0 DevPlan 017)
# · Remove if: exit-контракт фазы меняется
@ldd_trajectory
def test_delivery_exit_code_mapping(caplog) -> None:
    """delivery_exit_code: failed=0 → 0; ≥1 failed → 2 (строгий INIT)."""
    ok_all = ppd.DeliverySummary()
    ok_all.add(ppd.ProjectDeliveryLine("site-a", "delivered", "ok"))
    ok_all.add(ppd.ProjectDeliveryLine("site-b", "skipped", "no_local_source"))
    assert ppd.delivery_exit_code(ok_all) == 0, "delivered/skipped → 0 (bootstrap НЕ фейлится)"

    with_failure = ppd.DeliverySummary()
    with_failure.add(ppd.ProjectDeliveryLine("site-a", "failed", "deliver rc=1"))
    with_failure.add(ppd.ProjectDeliveryLine("site-b", "delivered", "ok"))
    assert ppd.delivery_exit_code(with_failure) == 2, "≥1 failed → 2 (строгий INIT критерий)"

    all_skipped = ppd.DeliverySummary()
    all_skipped.add(ppd.ProjectDeliveryLine("site-a", "skipped", "no_local_source"))
    assert ppd.delivery_exit_code(all_skipped) == 0, "no_local_source-only → 0"

    logger.critical("[IMP:9][test][ppd] delivery_exit_code 0|2 mapping — OK")


# endregion Tests: delivery_exit_code
