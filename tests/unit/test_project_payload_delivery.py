# GREP_SUMMARY: test-project-payload-delivery, bootstrap, deliver, pending-projects, no_local_source, awaiting-deploy, DI, tmp_path, exit-code, DevPlan-017, health-probe, skip-health, B3, health-verb, idempotent
# STRUCTURE: ▶ tmp_path node.yaml (contexts[0].name + node.host) + operator project dirs → DI deliver_fn (0 patches) → ○ per-project outcome (delivered/skipped/failed) → ◇ B3 health-probe DI (skip-health) → ⊕ DeliverySummary asserts → ◇ _build_default_health_probe verb-контракт (healthy→True; missing/unhealthy/error→False) → ◇ delivery_exit_code pure → ⎋ LDD trajectory
# region MODULE_CONTRACT
## @purpose  Unit tests for core/internal/deploy/project_payload_delivery.py — локальная
##           фаза bootstrap (P0, DevPlan 017): доставка payload'ов проектов контекста на ноду
##           после успешного SSH-exec remote lifecycle init. Критерий владельца: голая нода +
##           ОДНА команда make bootstrap-node завершается при ЖИВЫХ проектах контекста.
##           B3 (идемпотентность): health-предпробка «уже live» (DI health_probe_fn) —
##           healthy проект → skipped(skip-health:healthy), полный receive НЕ вызывается.
## @scope    Tests deliver_pending_projects (per-project outcome: delivered / skipped(no_local_source)
##           / failed с продолжением остальных / skipped(skip-health)) + delivery_exit_code
##           (чистый маппинг 0|2).
## @invariants
##   - 0 subprocess / 0 сети / 0 docker: deliver-канал и health-пробка мокаются DI-параметрами
##     deliver_fn + health_probe_fn (W4d-канон — тесты без monkeypatch subprocess/docker)
##   - node.yaml + операторские каталоги проектов создаются в tmp_path (zero hardcode)
##   - CONTEXT env удаляется (monkeypatch.delenv) — резолв идёт из node.yaml contexts[0].name
##   - Каждый тест валидирует IMP:9 бизнес-логику через ldd_trajectory (Anti-Illusion Rule)
## @rationale DevPlan 017 (P0): φ8 (context_deployer) помечает проекты awaiting_deploy
##            (GENERATED-STUB guard, DevPlan 153 T6 N1) — реальные исходники на операторской
##            машине; фаза доставляет payload'ы тем же каналом, что make deploy-project.
##            B3: повторный bootstrap = no-op — healthy проект скипается до полного receive
##            (tar+snapshots+hooks ~2.5s/проект); пробка best-effort (raise → deliver).
## @changes  2026-08-27 | DevPlan 017 — Created
## @changes  2026-08-27 | import-linter (independence-bootstrap-deploy) — модуль перенесён в
##            core/internal/deploy/; резолв проектов — локальный _resolve_context_projects
## @changes  2026-08-27 | B3 — +3 теста health-пробки: all-healthy → 0 deliveries;
##            mixed healthy/not-docker; probe raises → deliver (recording deliver_fn)
## @changes  2026-08-27 | B3 fix-forward — +тесты _build_default_health_probe на verb
##            `health <project>`: healthy→True; missing/unhealthy/error→False; ssh сбой→False;
##            remote-команда = verb (НЕ raw docker inspect) — контракт remote-probe
# endregion MODULE_CONTRACT

from __future__ import annotations

import logging
import subprocess
from collections.abc import Callable
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


def _recording_deliver(calls: list[str]) -> Callable[[str, Path], tuple[bool, str]]:
    """DI deliver_fn с регистрацией вызовов (assert 0 deliveries при skip-health)."""

    def _deliver(name: str, _project_dir: Path) -> tuple[bool, str]:
        calls.append(name)
        return True, '{"status": "DEPLOYED"}'

    return _deliver


def _not_live_probe(_name: str) -> bool:
    """DI health_probe_fn: контейнер НЕ healthy (opt-out пробки — 0 ssh/сети в unit-тестах).

    Без этого opt-out дефолтная пробка (None → _build_default_health_probe) выполнит РЕАЛЬНЫЙ
    ssh docker inspect к node.host фикстуры (10.0.0.1) — ConnectTimeout 30s/проект × N.
    """
    return False


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
        health_probe_fn=_not_live_probe,
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
        health_probe_fn=_not_live_probe,
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
        health_probe_fn=_not_live_probe,
    )

    assert summary.failed == 1
    assert summary.delivered == 1, "site-b должен быть доставлен несмотря на исключение site-a"
    assert [line.project for line in summary.lines] == ["site-a", "site-b"]
    failed_line = next(line for line in summary.lines if line.project == "site-a")
    assert failed_line.outcome == "failed"
    assert "raised" in failed_line.detail
    assert ppd.delivery_exit_code(summary) == 2, "≥1 failed → строгий INIT exit 2"
    logger.critical("[IMP:9][test][ppd] one delivery raises → failed=1, site-b delivered — OK")


# 🧪 TRAP[TEST] · B3 (DevPlan 017) · all-healthy → 0 deliveries, skipped=healthy×N
# · Regression: повторный bootstrap (B3 идемпотентность) передеплоивал здоровые проекты —
# ·   полный receive (tar+snapshots+hooks ~2.5s/проект) на каждый резюм
# · Scenario: оба проекта healthy (health_probe_fn → True) → deliver_fn НЕ вызывается ни разу
# ·   (recording), delivered=0, skipped=2, detail "skip-health:healthy" на каждой строке
# · Last fail: N/A (новая B3-пробка)
# · Remove if: health-предпробка удаляется
@ldd_trajectory
def test_all_projects_healthy_skipped(caplog, tmp_path: Path, node_yaml_file: str, monkeypatch) -> None:
    """B3: оба проекта healthy → 0 deliveries, skipped(skip-health:healthy)×2."""
    monkeypatch.delenv("CONTEXT", raising=False)
    projects_root = tmp_path / "projects"
    _make_project_dir(projects_root, "test-ctx", "site-a")
    _make_project_dir(projects_root, "test-ctx", "site-b")
    delivered_calls: list[str] = []

    summary = ppd.deliver_pending_projects(
        node_name="test-node",
        node_yaml_path=node_yaml_file,
        projects_root=str(projects_root),
        deliver_fn=_recording_deliver(delivered_calls),
        health_probe_fn=lambda _name: True,
    )

    assert summary.delivered == 0, "healthy-проекты НЕ передеплоиваются (B3 no-op)"
    assert summary.skipped == 2
    assert summary.failed == 0
    assert delivered_calls == [], "deliver_fn НЕ вызывается при healthy-проектах"
    assert all(line.outcome == "skipped" for line in summary.lines)
    assert all("skip-health" in line.detail and "healthy" in line.detail for line in summary.lines)
    assert ppd.delivery_exit_code(summary) == 0, "skip-health — НЕ failure"
    logger.critical("[IMP:9][test][ppd] all-healthy → 0 deliveries, skipped(skip-health)=2 — OK")


# 🧪 TRAP[TEST] · B3 (DevPlan 017) · mixed: healthy skip + not-docker delivered
# · Regression: пробка не должна блокировать доставку проекта, у которого контейнер
# ·   отсутствует/не в docker (probe=False) — он обязан уйти в нормальный deliver-путь
# · Scenario: site-a healthy (probe=True) → skip(skip-health); site-b не-docker (probe=False)
# ·   → delivered; счётчики delivered=1, skipped=1; deliver вызван ровно для site-b
# · Last fail: N/A (новая B3-пробка)
# · Remove if: health-предпробка удаляется
@ldd_trajectory
def test_mixed_healthy_and_not_docker_delivered(caplog, tmp_path: Path, node_yaml_file: str, monkeypatch) -> None:
    """B3: 1 healthy → skip(skip-health), 1 не-docker → delivered (probe=False → deliver)."""
    monkeypatch.delenv("CONTEXT", raising=False)
    projects_root = tmp_path / "projects"
    _make_project_dir(projects_root, "test-ctx", "site-a")
    _make_project_dir(projects_root, "test-ctx", "site-b")
    delivered_calls: list[str] = []

    summary = ppd.deliver_pending_projects(
        node_name="test-node",
        node_yaml_path=node_yaml_file,
        projects_root=str(projects_root),
        deliver_fn=_recording_deliver(delivered_calls),
        health_probe_fn=lambda name: name == "site-a",
    )

    assert summary.delivered == 1, "site-b (не-docker) обязан быть доставлен"
    assert summary.skipped == 1
    assert summary.failed == 0
    assert delivered_calls == ["site-b"], "deliver вызывается ТОЛЬКО для не-healthy проекта"
    skip_line = next(line for line in summary.lines if line.project == "site-a")
    assert skip_line.outcome == "skipped"
    assert "skip-health" in skip_line.detail and "healthy" in skip_line.detail
    logger.critical("[IMP:9][test][ppd] mixed: site-a skip-health, site-b delivered — OK")


# 🧪 TRAP[TEST] · B3 (DevPlan 017) · probe raises (ssh fail) → проект в delivery пути
# · Regression: сбой health-пробки (ssh недоступен/raise) НЕ должен маскироваться и НЕ должен
# ·   фейлить фазу — проект уходит в нормальный delivery-путь (реальный SSH-сбой проявится
# ·   в deliver естественно, IMP:10); пробка — только «skip или нет»
# · Scenario: health_probe_fn raise для каждого проекта → deliver_fn вызывается (recording
# ·   отмечает оба вызова), delivered=2, failed=0 (сбой пробки ≠ failure доставки)
# · Last fail: N/A (новая B3-пробка)
# · Remove if: семантика best-effort пробки меняется
@ldd_trajectory
def test_probe_raises_falls_through_to_deliver(caplog, tmp_path: Path, node_yaml_file: str, monkeypatch) -> None:
    """B3: probe raise (ssh fail) → not-live → deliver продолжается (mock отмечает вызов)."""
    monkeypatch.delenv("CONTEXT", raising=False)
    projects_root = tmp_path / "projects"
    _make_project_dir(projects_root, "test-ctx", "site-a")
    _make_project_dir(projects_root, "test-ctx", "site-b")
    delivered_calls: list[str] = []

    def _broken_probe(_name: str) -> bool:
        boom_msg = "ssh connection refused (probe)"
        raise OSError(boom_msg)

    summary = ppd.deliver_pending_projects(
        node_name="test-node",
        node_yaml_path=node_yaml_file,
        projects_root=str(projects_root),
        deliver_fn=_recording_deliver(delivered_calls),
        health_probe_fn=_broken_probe,
    )

    assert delivered_calls == ["site-a", "site-b"], "probe raise → оба проекта в delivery пути"
    assert summary.delivered == 2, "deliver успешен несмотря на сбой пробки"
    assert summary.skipped == 0
    assert summary.failed == 0, "сбой пробки ≠ failure фазы (best-effort, НЕ маскируется)"
    assert ppd.delivery_exit_code(summary) == 0
    logger.critical("[IMP:9][test][ppd] probe raises → deliver вызван для обоих — OK")


# endregion Tests: deliver_pending_projects


# ═══════════════════════════════════════════════════════════════════
# region Tests: _build_default_health_probe (B3 fix-forward — verb `health <project>`)
# ═══════════════════════════════════════════════════════════════════


def _probe_with_ssh(monkeypatch, records: list[list[str]], *, rc: int, stdout: str = ""):
    """Пробка с fake subprocess.run (ssh-транспорт мокается — 0 сетей/docker в unit).

    ## @purpose — _build_default_health_probe использует subprocess.run(["ssh", ...]) —
    ##            транспорт пробки; тест подменяет ТОЛЬКО транспорт (не бизнес-логику).
    ##            records собирает argv — assert'ы remote-команды (verb `health <project>`).
    """

    def _fake_run(argv, **kwargs):
        records.append(list(argv))
        return subprocess.CompletedProcess(args=argv, returncode=rc, stdout=stdout, stderr="")

    monkeypatch.setattr(ppd.subprocess, "run", _fake_run)
    return ppd._build_default_health_probe("10.0.0.1")


# 🧪 TRAP[TEST] · B3 fix-forward · probe healthy → True (skip-health eligible)
# · Regression: probe слал raw `docker inspect` → ci-deploy forced-command → unknown verb exit 4
# ·   → skip-health мёртв (deliver на каждом резюме bootstrap)
# · Scenario: ssh-раннер rc 0 stdout "healthy" → probe("site-a") is True;
# ·   remote-команда (argv[-1]) == "health site-a" (verb, НЕ docker inspect)
# · Last fail: 2026-08-27 — raw docker inspect под ci-deploy (S7) всегда exit 4
# · Remove if: health-предпробка удаляется
@ldd_trajectory
def test_probe_healthy_returns_true(caplog, monkeypatch) -> None:
    """probe: rc 0 + 'healthy' → True; remote-команда — verb `health <project>`."""
    records: list[list[str]] = []
    probe = _probe_with_ssh(monkeypatch, records, rc=0, stdout="healthy\n")

    assert probe("site-a") is True
    assert records, "ssh-транспорт обязан быть вызван"
    assert records[0][-1] == "health site-a", f"remote-команда = verb health, got {records[0][-1]!r}"
    assert "docker" not in records[0][-1], "raw docker-команда НЕ уходит (forced-command security)"
    assert "ci-deploy@10.0.0.1" in records[0], "канал = ci-deploy@host (тот же, что deliver)"
    logger.critical("[IMP:9][test][probe] healthy → True (verb health site-a) — OK")


# 🧪 TRAP[TEST] · B3 fix-forward · probe missing → False (deliver продолжается)
# · Scenario: ssh rc 0 stdout "missing" → probe False (факт получен, но НЕ healthy)
# · Last fail: N/A (новый verb-контракт)
# · Remove if: слово-контракт health меняется
@ldd_trajectory
def test_probe_missing_returns_false(caplog, monkeypatch) -> None:
    """probe: rc 0 + 'missing' (контейнер отсутствует) → False (не healthy → deliver)."""
    records: list[list[str]] = []
    probe = _probe_with_ssh(monkeypatch, records, rc=0, stdout="missing")

    assert probe("site-a") is False
    assert records[0][-1] == "health site-a"
    logger.critical("[IMP:9][test][probe] missing → False (deliver продолжается) — OK")


# 🧪 TRAP[TEST] · B3 fix-forward · probe unhealthy → False (не healthy → deliver)
# · Scenario: ssh rc 0 stdout "unhealthy" → probe False (статус получен, НЕ healthy)
# · Last fail: N/A (новый verb-контракт)
# · Remove if: слово-контракт health меняется
@ldd_trajectory
def test_probe_unhealthy_returns_false(caplog, monkeypatch) -> None:
    """probe: rc 0 + 'unhealthy' → False (не healthy → deliver продолжается)."""
    records: list[list[str]] = []
    probe = _probe_with_ssh(monkeypatch, records, rc=0, stdout="unhealthy")

    assert probe("site-a") is False
    logger.critical("[IMP:9][test][probe] unhealthy → False (deliver продолжается) — OK")


# 🧪 TRAP[TEST] · B3 fix-forward · probe error → False (+ IMP:7 лог внутренней ошибки)
# · Scenario: ssh rc 1 stdout "error" → probe False (внутренняя ошибка verb/ssh → not-live)
# · Last fail: N/A (новый verb-контракт)
# · Remove if: error-семантика пробки меняется
@ldd_trajectory
def test_probe_error_returns_false(caplog, monkeypatch) -> None:
    """probe: rc 1 + 'error' (внутренняя ошибка инспекта) → False + IMP:7 лог."""
    records: list[list[str]] = []
    probe = _probe_with_ssh(monkeypatch, records, rc=1, stdout="error")

    assert probe("site-a") is False
    assert any("[IMP:7]" in r.message and "probe error" in r.message for r in caplog.records), (
        "внутренняя ошибка пробки обязана логироваться IMP:7 (не маскируется)"
    )
    logger.critical("[IMP:9][test][probe] error → False + IMP:7 лог — OK")


# 🧪 TRAP[TEST] · B3 fix-forward · probe ssh timeout → False (best-effort, НЕ raise)
# · Scenario: subprocess.run raise TimeoutExpired → probe False (not-live → deliver;
# ·   реальный SSH-сбой проявится в deliver естественно, IMP:10)
# · Last fail: N/A (best-effort контракт B3)
# · Remove if: best-effort семантика пробки меняется
@ldd_trajectory
def test_probe_ssh_timeout_returns_false(caplog, monkeypatch) -> None:
    """probe: ssh timeout (TimeoutExpired) → False, НЕ raise (best-effort)."""

    def _timeout_run(argv, **kwargs):
        raise subprocess.TimeoutExpired(cmd=argv, timeout=kwargs.get("timeout", 60))

    monkeypatch.setattr(ppd.subprocess, "run", _timeout_run)
    probe = ppd._build_default_health_probe("10.0.0.1")

    assert probe("site-a") is False
    assert any("[IMP:7]" in r.message and "probe error" in r.message for r in caplog.records)
    logger.critical("[IMP:9][test][probe] ssh timeout → False, НЕ raise — OK")


# endregion Tests: _build_default_health_probe


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
