"""
# GREP_SUMMARY: test-verify-contracts, K3, L1, L2, L3, ports-blocked, healthcheck-blocked, secret-literal, external-network, env-file, limits-present, privileged-blocked, cap-add-blocked, devices-blocked, drift-lock, unmanaged, baseline-green, R5, audit
# STRUCTURE: ▶ _make_project (ai-platform.yaml + compose + lock) → ◇ docker absent (monkeypatch which) → ○ verify_project_contracts (audit в tmp) → ⊕ assert severity/blocking/format → ◇ LDD IMP:9 trajectory → ⎋
# region MODULE_CONTRACT
## @purpose  Unit-тесты verify_contracts (DevPlan 137 W4, K3-канал VPS): R5-negative-тесты для
##           каждого L1-контракта (ports/healthcheck/secrets/external-net/env-file/limits/
##           privileged/cap-add/devices — последние три DevPlan 176 A.1, C1 root-эскалация),
##           дрейф practices.lock по state (active-full → L2 блок; baseline/proposed → warning),
##           unmanaged (без lock → L1 блок),
##           baseline-зелёный (валидный проект → 0 violations, exit 0), аудит записей.
## @scope    $TEST_SPEC 137 W4: tests/unit/test_verify_contracts.py (8 тестов, список §5 W4
##           «Negative-тесты R5» + baseline_green); 176 A.1: +privileged/cap_add/devices R5.
##           Native imports, tmp_path, monkeypatch.
## @invariants
##   - docker отсутствует в тестах (monkeypatch which → None) — L2 docker-проверки skip
##   - audit пишется в tmp-файл (audit_log_file) — НЕ в /var/log/platform (изоляция)
##   - practices.lock — минимальный валидный YAML (read_lock-парсится)
##   - LDD: IMP:9-траектория через _print_ldd_trajectory (Anti-Illusion Rule)
##   - R5: negative-тесты с ТОЧНЫМ входом бага (ports: ["8080:80"], password: hunter2,
##     privileged: true, cap_add: [...], devices: [...] — C1 root-эскалация)
## @rationale AC W4: деплой с ports:/без healthcheck/внешней сетью вне allowlist — блок при
##            любом уровне; дрейф lock — блок в full, warning в baseline/proposed.
##            AC W4-3 (162): сервис без deploy.resources.limits.memory+cpus — блок (OOM-риск).
##            AC A.1 (176, C1): privileged/cap_add/devices — блок при любом уровне (root-эскалация).
## @changes  2026-08-05 · DevPlan 137 W4 — создан
## @changes  2026-08-13 · DevPlan 162 W4-3 — +limits-present (negative R5: без лимитов / частичные)
## @changes  2026-08-16 · DevPlan 176 A.1 — +privileged/cap-add/devices (R5-negative, C1);
##           +privileged: false → OK (точность truthy-контракта)
# endregion MODULE_CONTRACT
"""

import logging
import os
from pathlib import Path

import pytest

from core.internal.deploy.verify_contracts import verify_project_contracts
from core.internal.shared.audit_logger import read_audit_log
from tests.conftest import _print_ldd_trajectory

pytestmark = pytest.mark.static_audit

logger = logging.getLogger(__name__)

# ── Docker-бинарник в тестах НЕ доступен (L2 docker-проверки skip, без реального docker) ──
_DOCKER_ABSENT: bool = os.environ.get("TEST_REAL_DOCKER", "0") == "1"


# region CLASS_NoDockerFacts
class _NoDockerFacts:
    """EnvironmentFacts-fake (DevPlan 160 W4b): which('docker') → None — L2 docker-проверки skip."""

    def is_root(self) -> bool:  # pragma: no cover
        return True

    def which(self, _binary) -> str | None:
        return None

    def path_isfile(self, _path) -> bool:  # pragma: no cover
        return False


# endregion CLASS_NoDockerFacts

# Общий facts-fake для всех тестов verify_contracts (docker отсутствует)
_FACTS_NO_DOCKER = _NoDockerFacts()

# Валидный baseline compose (проходит ВСЕ L1-контракты: env_file .env.platform, healthcheck,
# platform labels, external proxy-net из allowlist, deploy.resources.limits.memory+cpus,
# БЕЗ ports/секретов)
_VALID_COMPOSE: str = """\
services:
  app:
    image: busybox:latest
    env_file:
      - .env.platform
    healthcheck:
      test: ["CMD", "echo", "ok"]
    deploy:
      resources:
        limits:
          memory: "128M"
          cpus: "0.25"
    labels:
      - "platform.type=backend"
      - "platform.domain=example.com"
    networks:
      - proxy-net
networks:
  proxy-net:
    external: true
"""

_LOCK_TPL: str = """\
version: {version}
level: auto
state: {state}
language: python
generator_hash: sha256:test
maturity:
  age_days: 1
  code_files: 1
generated_at: 2026-08-05T00:00:00Z
files: {{}}
"""


# region HELPER__make_project
def _make_project(
    tmp_path: Path,
    name: str = "mockproject",
    compose: str = _VALID_COMPOSE,
    lock_state: str | None = "baseline",
    lock_version: int = 1,
    *,
    write_env_platform: bool = True,
) -> Path:
    """Create mock project dir (ai-platform.yaml + compose + optional practices.lock)."""
    project = tmp_path / name
    project.mkdir()
    (project / "ai-platform.yaml").write_text(
        "name: " + name + "\ntype: backend\ntarget_node: test-node\n", encoding="utf-8"
    )
    (project / "docker-compose.yml").write_text(compose, encoding="utf-8")
    if write_env_platform:
        (project / ".env.platform").write_text("PLATFORM_DOMAIN=example.com\n", encoding="utf-8")
    if lock_state is not None:
        (project / "practices.lock").write_text(
            _LOCK_TPL.format(version=lock_version, state=lock_state), encoding="utf-8"
        )
    return project


# endregion HELPER__make_project


# region HELPER__assert_blocking
def _assert_blocking(report, expected_contract: str, caplog: pytest.LogCaptureFixture) -> None:
    """Assert blocking violation on expected contract + audit BLOCKED + LDD trajectory."""
    assert report.has_blocking_violation(), f"ожидался блок по {expected_contract}: {report.format_for_ssh()}"
    assert report.exit_code == 1, f"exit_code должен быть 1 при L1-блоке: {report.format_for_ssh()}"
    blocked = [f for f in report.findings if f.severity == "block"]
    assert any(f.contract_id == expected_contract for f in blocked), (
        f"нет blocking-finding по {expected_contract}: {blocked}"
    )
    rendered = report.format_for_ssh()
    assert "[PRACTICES:BLOCK]" in rendered and f"[{expected_contract}]" in rendered
    logger.info("--- verify contracts report ---")
    logger.info("%s", rendered)
    assert _print_ldd_trajectory(caplog), "LDD: нет IMP:9 лога verify_contracts"


# endregion HELPER__assert_blocking


# 🧪 TRAP[TEST] · 2026-08-05 · NEGATIVE (R5) · ports-published — AC W4 «ports: блок при любом уровне»
# · Last fail: prior — проект с ports: деплоился без единой проверки (deploy-project.yml verify не проверял код)
# · Remove if: ports-published контракт меняется
def test_verify_contracts_ports_blocked(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """compose с ports: ["8080:80"] → L1 violation (ports-published), блок, exit 1."""
    compose = (
        "services:\n  app:\n    image: busybox:latest\n"
        '    ports:\n      - "8080:80"\n'
        '    healthcheck:\n      test: ["CMD", "echo", "ok"]\n'
        '    labels:\n      - "platform.type=backend"\n'
        "    networks:\n      - proxy-net\n"
        "networks:\n  proxy-net:\n    external: true\n"
    )
    project = _make_project(tmp_path, compose=compose)
    audit = tmp_path / "audit.jsonl"

    with caplog.at_level(logging.INFO):
        report = verify_project_contracts(project, audit_log_file=str(audit))

    _assert_blocking(report, "ports-published", caplog)
    entries = read_audit_log(str(audit))
    assert entries and entries[-1]["tag"] == "verify_contracts"
    assert entries[-1]["status"] == "BLOCKED"
    finding_ids = [f["id"] for f in entries[-1].get("findings", [])]
    assert "ports-published" in finding_ids, f"аудит не содержит ports-published: {finding_ids}"


# 🧪 TRAP[TEST] · 2026-08-05 · NEGATIVE (R5) · healthcheck-present — AC W4 «мок без healthcheck заблокирован»
# · Last fail: prior — сервис без healthcheck деплоился (healthcheck_poller канон не enforced на деплое)
# · Remove if: healthcheck-present контракт меняется (ИЛИ healthcheck, ИЛИ platform.healthcheck label)
def test_verify_contracts_no_healthcheck_blocked(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """service без healthcheck: (и без labels platform.healthcheck) → L1 violation, блок."""
    compose = (
        "services:\n  app:\n    image: busybox:latest\n"
        "    env_file:\n      - .env.platform\n"
        '    labels:\n      - "platform.type=backend"\n'
        "    networks:\n      - proxy-net\n"
        "networks:\n  proxy-net:\n    external: true\n"
    )
    project = _make_project(tmp_path, compose=compose)
    audit = tmp_path / "audit.jsonl"

    with caplog.at_level(logging.INFO):
        report = verify_project_contracts(project, audit_log_file=str(audit))

    _assert_blocking(report, "healthcheck-present", caplog)
    entries = read_audit_log(str(audit))
    assert entries[-1]["status"] == "BLOCKED"


# 🧪 TRAP[TEST] · 2026-08-05 · NEGATIVE (R5) · secrets-in-compose — AC W4 «секрет в compose → блок»
# · Last fail: prior — password: hunter2 в compose деплоился (секрет утекал в git проекта)
# · Remove if: secrets-in-compose контракт меняется (суффикс-матчинг password|api_key|token)
def test_verify_contracts_secret_literal_blocked(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """password: hunter2 (литерал, не ${VAR}) в environment → L1 violation, блок."""
    compose = (
        "services:\n  app:\n    image: busybox:latest\n"
        "    environment:\n      password: hunter2\n"
        '    healthcheck:\n      test: ["CMD", "echo", "ok"]\n'
        '    labels:\n      - "platform.type=backend"\n'
        "    networks:\n      - proxy-net\n"
        "networks:\n  proxy-net:\n    external: true\n"
    )
    project = _make_project(tmp_path, compose=compose)
    audit = tmp_path / "audit.jsonl"

    with caplog.at_level(logging.INFO):
        report = verify_project_contracts(project, audit_log_file=str(audit))

    _assert_blocking(report, "secrets-in-compose", caplog)
    entries = read_audit_log(str(audit))
    assert entries[-1]["status"] == "BLOCKED"


# 🧪 TRAP[TEST] · 2026-08-05 · NEGATIVE (R5) · external-networks — AC W4 «сеть вне allowlist → блок»
# · Last fail: prior — кастомная external-сеть подключала проект к чужой сети (без контроля)
# · Remove if: external-networks контракт меняется (allowlist из practices_manifest.yaml)
def test_verify_contracts_external_network_unknown_blocked(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """external-сеть вне allowed_external_networks канона → L1 violation, блок."""
    compose = (
        "services:\n  app:\n    image: busybox:latest\n"
        "    env_file:\n      - .env.platform\n"
        '    healthcheck:\n      test: ["CMD", "echo", "ok"]\n'
        '    labels:\n      - "platform.type=backend"\n'
        "    networks:\n      - evil-net\n"
        "networks:\n  evil-net:\n    external: true\n"
    )
    project = _make_project(tmp_path, compose=compose)
    audit = tmp_path / "audit.jsonl"

    with caplog.at_level(logging.INFO):
        report = verify_project_contracts(project, audit_log_file=str(audit))

    _assert_blocking(report, "external-networks", caplog)
    entries = read_audit_log(str(audit))
    assert entries[-1]["status"] == "BLOCKED"


# 🧪 TRAP[TEST] · 2026-08-05 · NEGATIVE (R5) · env-file-contract — AC W4 «env_file не .env.platform → блок»
# · Last fail: prior — env_file: .env (секреты в коммитимом файле) деплоился
# · Remove if: env-file-contract контракт меняется (только .env.platform допустим)
def test_verify_contracts_env_file_wrong_blocked(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """env_file: .env (не .env.platform) → L1 violation, блок."""
    compose = (
        "services:\n  app:\n    image: busybox:latest\n"
        "    env_file:\n      - .env\n"
        '    healthcheck:\n      test: ["CMD", "echo", "ok"]\n'
        '    labels:\n      - "platform.type=backend"\n'
        "    networks:\n      - proxy-net\n"
        "networks:\n  proxy-net:\n    external: true\n"
    )
    project = _make_project(tmp_path, compose=compose)
    audit = tmp_path / "audit.jsonl"

    with caplog.at_level(logging.INFO):
        report = verify_project_contracts(project, audit_log_file=str(audit))

    _assert_blocking(report, "env-file-contract", caplog)
    entries = read_audit_log(str(audit))
    assert entries[-1]["status"] == "BLOCKED"


# 🧪 TRAP[TEST] · 2026-08-05 · NEGATIVE (R5) · drift-practices — AC W4 «дрейф lock: блок в full, warning в baseline/proposed»
# · Last fail: prior — устаревший practices.lock (version 0) не влиял на деплой (VPS не сверял канон)
# · Remove if: drift-practices контракт меняется (L2: block в active-full, warning иначе)
def test_verify_contracts_drift_full_blocked(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """lock устарел (version 0 < canon 1): state=active-full → L2 блок; baseline/proposed → warning."""

    # ── active-full: L2 drift → БЛОК ──
    project_full = _make_project(tmp_path, name="proj-full", lock_state="active-full", lock_version=0)
    audit_full = tmp_path / "audit-full.jsonl"
    with caplog.at_level(logging.INFO):
        report_full = verify_project_contracts(project_full, audit_log_file=str(audit_full), facts=_FACTS_NO_DOCKER)
    drift_full = [f for f in report_full.findings if f.contract_id == "drift-practices"]
    assert drift_full and drift_full[0].severity == "block", (
        f"drift в active-full обязан блокировать: {report_full.format_for_ssh()}"
    )
    assert report_full.has_blocking_violation() and report_full.exit_code == 1

    # ── baseline: L2 drift → warning non-blocking ──
    project_base = _make_project(tmp_path, name="proj-base", lock_state="baseline", lock_version=0)
    audit_base = tmp_path / "audit-base.jsonl"
    with caplog.at_level(logging.INFO):
        report_base = verify_project_contracts(project_base, audit_log_file=str(audit_base), facts=_FACTS_NO_DOCKER)
    drift_base = [f for f in report_base.findings if f.contract_id == "drift-practices"]
    assert drift_base and drift_base[0].severity == "warning", "drift в baseline обязан быть warning"
    assert not report_base.has_blocking_violation() and report_base.exit_code == 0
    assert report_base.has_warnings()

    # ── proposed: L2 drift → warning non-blocking ──
    project_prop = _make_project(tmp_path, name="proj-prop", lock_state="proposed", lock_version=0)
    audit_prop = tmp_path / "audit-prop.jsonl"
    with caplog.at_level(logging.INFO):
        report_prop = verify_project_contracts(project_prop, audit_log_file=str(audit_prop), facts=_FACTS_NO_DOCKER)
    drift_prop = [f for f in report_prop.findings if f.contract_id == "drift-practices"]
    assert drift_prop and drift_prop[0].severity == "warning", "drift в proposed обязан быть warning"
    assert not report_prop.has_blocking_violation() and report_prop.exit_code == 0

    entries = read_audit_log(str(audit_full))
    assert entries[-1]["status"] == "BLOCKED"
    assert entries[-1]["state"] == "active-full"
    logger.info("--- drift scenarios ---")
    logger.info("%s", report_full.format_for_ssh())
    logger.info("%s", report_base.format_for_ssh())
    assert _print_ldd_trajectory(caplog), "LDD: нет IMP:9 лога drift-practices"


# 🧪 TRAP[TEST] · 2026-08-05 · NEGATIVE (R5) · unmanaged — без practices.lock L1 блокирует деплой
# · Last fail: отсутствие practices.lock давало L1 warning-only (переходный grace-период, удалён)
# · Remove if: семантика отсутствующего practices.lock меняется
def test_verify_contracts_unmanaged_blocked(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """без practices.lock → state=unmanaged, L1-контракты блокируют деплой."""
    compose = (
        "services:\n  app:\n    image: busybox:latest\n"
        '    ports:\n      - "8080:80"\n'  # L1-нарушение → block
        '    healthcheck:\n      test: ["CMD", "echo", "ok"]\n'
        '    labels:\n      - "platform.type=backend"\n'
        "    networks:\n      - proxy-net\n"
        "networks:\n  proxy-net:\n    external: true\n"
    )
    project = _make_project(tmp_path, compose=compose, lock_state=None)
    audit = tmp_path / "audit.jsonl"

    with caplog.at_level(logging.INFO):
        report = verify_project_contracts(project, audit_log_file=str(audit), facts=_FACTS_NO_DOCKER)

    assert report.state == "unmanaged"
    assert report.has_blocking_violation(), f"unmanaged должен блокировать L1: {report.format_for_ssh()}"
    assert report.exit_code == 1
    rendered = report.format_for_ssh()
    assert "[PRACTICES:UNMANAGED]" in rendered
    assert "[PRACTICES:BLOCK]" in rendered
    ports = [f for f in report.findings if f.contract_id == "ports-published"]
    assert ports and ports[0].severity == "block", f"L1 в unmanaged обязан быть block: {ports}"

    entries = read_audit_log(str(audit))
    assert entries[-1]["status"] == "BLOCKED"
    assert entries[-1]["state"] == "unmanaged"
    assert _print_ldd_trajectory(caplog), "LDD: нет IMP:9 лога unmanaged"


# 🧪 TRAP[TEST] · 2026-08-05 · unit · baseline-green — AC W4 «валидный проект → 0 violations, exit 0»
# · Regression: валидный baseline-проект НЕ должен блокироваться (шаблоны платформы deployable)
# · Last fail: N/A
# · Remove if: состав L1-контрактов меняется
def test_verify_contracts_baseline_green(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Валидный baseline-проект (healthcheck, env_file .env.platform, labels, proxy-net) → 0 violations, exit 0."""
    project = _make_project(tmp_path, lock_state="baseline", lock_version=1)
    audit = tmp_path / "audit.jsonl"

    with caplog.at_level(logging.INFO):
        report = verify_project_contracts(project, audit_log_file=str(audit), facts=_FACTS_NO_DOCKER)

    assert report.state == "baseline"
    assert len(report.findings) == 0, f"валидный baseline-проект обязан иметь 0 findings: {report.format_for_ssh()}"
    assert not report.has_blocking_violation()
    assert not report.has_warnings()
    assert report.exit_code == 0
    rendered = report.format_for_ssh()
    assert "[PRACTICES:OK]" in rendered and "[PRACTICES:RESULT]" in rendered

    entries = read_audit_log(str(audit))
    assert entries and entries[-1]["tag"] == "verify_contracts"
    assert entries[-1]["status"] == "OK"
    assert _print_ldd_trajectory(caplog), "LDD: нет IMP:9 лога baseline-green"


# 🧪 TRAP[TEST] · 2026-08-05 · unit · отсутствие проекта — graceful skip (0 findings, без аудита)
# · Regression: verify без project_dir на VPS не должен падать/блокировать
# · Last fail: N/A
# · Remove if: семантика отсутствующего проекта меняется
def test_verify_contracts_missing_project_dir(
    tmp_path: Path,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Проект-директория отсутствует → пустой отчёт (state=unmanaged, 0 findings, exit 0)."""
    audit = tmp_path / "audit.jsonl"
    with caplog.at_level(logging.INFO):
        report = verify_project_contracts(tmp_path / "nonexistent", audit_log_file=str(audit))
    assert report.findings == ()
    assert not report.has_blocking_violation()
    assert report.exit_code == 0
    assert not audit.exists(), "отсутствующий проект НЕ должен писать аудит"
    assert _print_ldd_trajectory(caplog), "LDD: нет IMP:9 лога (missing project)"


# ═══════════════════════════════════════════════════════════════════
# DevPlan 162 W4-3: L1 limits-present (deploy.resources.limits.memory + cpus)
# ═══════════════════════════════════════════════════════════════════


# 🧪 TRAP[TEST] · 2026-08-13 · NEGATIVE (R5) · limits-present — AC W4-3 «сервис без лимитов → блок»
# · Last fail: проектные контейнеры (tronyx-site, botanika, dance-site) без memory/CPU лимитов —
# ·   OOM-риск общего стека (суммарные лимиты ~10.5G > 7.8G RAM ноды, DevPlan 162 W4-1 evidence)
# · Remove if: limits-present контракт меняется (проверка НАЛИЧИЯ, не значений — TRAP[DECISION])
def test_verify_contracts_limits_missing_blocked(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """service без deploy.resources.limits.memory И cpus → L1 violation (limits-present), блок."""
    compose = (
        "services:\n  app:\n    image: busybox:latest\n"
        "    env_file:\n      - .env.platform\n"
        '    healthcheck:\n      test: ["CMD", "echo", "ok"]\n'
        '    labels:\n      - "platform.type=backend"\n'
        "    networks:\n      - proxy-net\n"
        "networks:\n  proxy-net:\n    external: true\n"
    )
    project = _make_project(tmp_path, compose=compose)
    audit = tmp_path / "audit.jsonl"

    with caplog.at_level(logging.INFO):
        report = verify_project_contracts(project, audit_log_file=str(audit), facts=_FACTS_NO_DOCKER)

    _assert_blocking(report, "limits-present", caplog)
    entries = read_audit_log(str(audit))
    assert entries[-1]["status"] == "BLOCKED"
    finding_ids = [f["id"] for f in entries[-1].get("findings", [])]
    assert "limits-present" in finding_ids, f"аудит не содержит limits-present: {finding_ids}"


# 🧪 TRAP[TEST] · 2026-08-13 · NEGATIVE (R5) · limits-present — частичные лимиты (только memory)
# · Last fail: контейнер с memory-лимитом но БЕЗ cpus — CPU не ограничен (W4-3: и memory, и cpus)
# · Remove if: limits-present контракт меняется (memory+cpus пара обязательна)
def test_verify_contracts_limits_partial_blocked(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """service с limits.memory, но БЕЗ limits.cpus → L1 violation (limits-present), блок."""
    compose = (
        "services:\n  app:\n    image: busybox:latest\n"
        "    env_file:\n      - .env.platform\n"
        '    healthcheck:\n      test: ["CMD", "echo", "ok"]\n'
        '    deploy:\n      resources:\n        limits:\n          memory: "128M"\n'
        '    labels:\n      - "platform.type=backend"\n'
        "    networks:\n      - proxy-net\n"
        "networks:\n  proxy-net:\n    external: true\n"
    )
    project = _make_project(tmp_path, compose=compose)
    audit = tmp_path / "audit.jsonl"

    with caplog.at_level(logging.INFO):
        report = verify_project_contracts(project, audit_log_file=str(audit))

    _assert_blocking(report, "limits-present", caplog)
    blocked = [f for f in report.findings if f.severity == "block"]
    limits_finding = next(f for f in blocked if f.contract_id == "limits-present")
    assert "cpus" in limits_finding.message, f"сообщение должно указывать отсутствие cpus: {limits_finding.message}"
    entries = read_audit_log(str(audit))
    assert entries[-1]["status"] == "BLOCKED"


# ═══════════════════════════════════════════════════════════════════
# DevPlan 176 A.1 (C1 root-эскалация): L1 privileged / cap-add / devices
# ═══════════════════════════════════════════════════════════════════


# region HELPER__baseline_compose_with
def _baseline_compose_with(*extra_yaml_lines: str) -> str:
    """Baseline-valid compose + extra service-level YAML lines (176 A.1 R5-negative tests).

    ## @purpose — базовый compose проходит ВСЕ L1-контракты; extra-строки инжектятся
    ##            в service app (проверяется ровно один новый контракт, без шума).
    ## @io — ⇥ extra_yaml_lines: str (с отступом service-уровня, 4 пробела) → ⎋ str
    """
    return "\n".join([
        "services:",
        "  app:",
        "    image: busybox:latest",
        *extra_yaml_lines,
        "    env_file:",
        "      - .env.platform",
        "    healthcheck:",
        '      test: ["CMD", "echo", "ok"]',
        "    deploy:",
        "      resources:",
        "        limits:",
        '          memory: "128M"',
        '          cpus: "0.25"',
        "    labels:",
        '      - "platform.type=backend"',
        "    networks:",
        "      - proxy-net",
        "networks:",
        "  proxy-net:",
        "    external: true",
    ])


# endregion HELPER__baseline_compose_with


# 🧪 TRAP[TEST] · 2026-08-16 · NEGATIVE (R5) · privileged — AC A.1 (176 C1) «privileged: true → блок»
# · Last fail: prior — receive исполнял compose с privileged:true + /:/host без проверки
# ·   (root-эскалация C1: ci-deploy в docker-группе; в 7 L1-контрактах не было privileged)
# · Remove if: privileged контракт меняется (truthy-семантика, DevPlan 176 A.1)
def test_verify_contracts_privileged_blocked(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """services.*.privileged: true → L1 violation (privileged), блок, exit 1."""
    compose = _baseline_compose_with("    privileged: true")
    project = _make_project(tmp_path, compose=compose)
    audit = tmp_path / "audit.jsonl"

    with caplog.at_level(logging.INFO):
        report = verify_project_contracts(project, audit_log_file=str(audit), facts=_FACTS_NO_DOCKER)

    _assert_blocking(report, "privileged", caplog)
    entries = read_audit_log(str(audit))
    assert entries[-1]["status"] == "BLOCKED"
    finding_ids = [f["id"] for f in entries[-1].get("findings", [])]
    assert "privileged" in finding_ids, f"аудит не содержит privileged: {finding_ids}"


# 🧪 TRAP[TEST] · 2026-08-16 · NEGATIVE (R5) · cap-add — AC A.1 (176 C1) «cap_add → блок»
# · Last fail: prior — compose с cap_add: ["NET_ADMIN"] исполнялся без проверки (C1 root-эскалация)
# · Remove if: cap-add контракт меняется (любое значение ключа = violation, DevPlan 176 A.1)
def test_verify_contracts_cap_add_blocked(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """services.*.cap_add (любое значение) → L1 violation (cap-add), блок, exit 1."""
    compose = _baseline_compose_with("    cap_add:", '      - "NET_ADMIN"')
    project = _make_project(tmp_path, compose=compose)
    audit = tmp_path / "audit.jsonl"

    with caplog.at_level(logging.INFO):
        report = verify_project_contracts(project, audit_log_file=str(audit), facts=_FACTS_NO_DOCKER)

    _assert_blocking(report, "cap-add", caplog)
    entries = read_audit_log(str(audit))
    assert entries[-1]["status"] == "BLOCKED"
    finding_ids = [f["id"] for f in entries[-1].get("findings", [])]
    assert "cap-add" in finding_ids, f"аудит не содержит cap-add: {finding_ids}"


# 🧪 TRAP[TEST] · 2026-08-16 · NEGATIVE (R5) · devices — AC A.1 (176 C1) «devices → блок»
# · Last fail: prior — compose с devices: ["/dev/sda:/dev/xvda"] исполнялся без проверки
# ·   (прямой доступ к host-устройствам ноды, C1 root-эскалация)
# · Remove if: devices контракт меняется (любое значение ключа = violation, DevPlan 176 A.1)
def test_verify_contracts_devices_blocked(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """services.*.devices (любое значение) → L1 violation (devices), блок, exit 1."""
    compose = _baseline_compose_with("    devices:", '      - "/dev/sda:/dev/xvda"')
    project = _make_project(tmp_path, compose=compose)
    audit = tmp_path / "audit.jsonl"

    with caplog.at_level(logging.INFO):
        report = verify_project_contracts(project, audit_log_file=str(audit), facts=_FACTS_NO_DOCKER)

    _assert_blocking(report, "devices", caplog)
    entries = read_audit_log(str(audit))
    assert entries[-1]["status"] == "BLOCKED"
    finding_ids = [f["id"] for f in entries[-1].get("findings", [])]
    assert "devices" in finding_ids, f"аудит не содержит devices: {finding_ids}"


# 🧪 TRAP[TEST] · 2026-08-16 · unit · privileged: false → OK (точность truthy-контракта, 176 A.1)
# · Regression: explicit `privileged: false` (docker-дефолт, привилегий не даёт) НЕ блокируется
# ·   — ложный блок сломал бы легитимные проекты, явно декларирующие отсутствие привилегий
# · Last fail: N/A (new contract)
# · Remove if: privileged truthy-семантика меняется (false → violation)
def test_verify_contracts_privileged_false_ok(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """privileged: false (явный) → НЕ violation (docker-дефолт, без привилегий)."""
    compose = _baseline_compose_with("    privileged: false")
    project = _make_project(tmp_path, compose=compose)
    audit = tmp_path / "audit.jsonl"

    with caplog.at_level(logging.INFO):
        report = verify_project_contracts(project, audit_log_file=str(audit), facts=_FACTS_NO_DOCKER)

    assert len(report.findings) == 0, f"privileged: false обязан быть OK (0 findings): {report.format_for_ssh()}"
    assert not report.has_blocking_violation()
    assert report.exit_code == 0
    entries = read_audit_log(str(audit))
    assert entries[-1]["status"] == "OK"
    assert _print_ldd_trajectory(caplog), "LDD: нет IMP:9 лога privileged-false"


# ═══════════════════════════════════════════════════════════════════
# REF-0006 (DevPlan 11 В2): L1 dangerous-volumes / host-mode-keys +
# l1_only compose-config-valid parse-fail → БЛОК
# ═══════════════════════════════════════════════════════════════════


# 🧪 TRAP[TEST] · 2026-08-25 · NEGATIVE (R5) · dangerous-volumes — C1-вход «socket-mount»
# · Last fail: REF-0006 — L1 не смотрел volumes; коммит volumes:
#   ["/var/run/docker.sock:/sock"] = root ноды (ci-deploy в docker-группе) + все секреты
# · Remove if: dangerous-volumes контракт меняется (socket deny-set)
def test_verify_contracts_docker_socket_mount_blocked(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Точный C1-вход: - /var/run/docker.sock:/var/run/docker.sock → L1 блок (dangerous-volumes)."""
    compose = _baseline_compose_with(
        "    volumes:",
        '      - "/var/run/docker.sock:/var/run/docker.sock"',
    )
    project = _make_project(tmp_path, compose=compose)
    audit = tmp_path / "audit.jsonl"

    with caplog.at_level(logging.INFO):
        report = verify_project_contracts(project, audit_log_file=str(audit), facts=_FACTS_NO_DOCKER)

    _assert_blocking(report, "dangerous-volumes", caplog)
    blocked = next(f for f in report.findings if f.contract_id == "dangerous-volumes" and f.severity == "block")
    assert "docker.sock" in blocked.message, f"сообщение обязано называть socket-маунт: {blocked.message}"
    entries = read_audit_log(str(audit))
    finding_ids = [f["id"] for f in entries[-1].get("findings", [])]
    assert "dangerous-volumes" in finding_ids, f"аудит не содержит dangerous-volumes: {finding_ids}"


# 🧪 TRAP[TEST] · 2026-08-25 · NEGATIVE (R5) · dangerous-volumes — C1-вход «/»-bind
# · Last fail: REF-0006 — privileged:true + /:/host был точным вектором C1; bind "/:/host"
# ·   без privileged оставался незамеченным (volumes вне L1)
# · Remove if: dangerous-volumes контракт меняется (абсолютные binds вне allowlist)
def test_verify_contracts_root_bind_blocked(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Точный C1-вход: - /:/host → L1 блок (абсолютный host-bind вне allowlist)."""
    compose = _baseline_compose_with("    volumes:", '      - "/:/host"')
    project = _make_project(tmp_path, compose=compose)
    audit = tmp_path / "audit.jsonl"

    with caplog.at_level(logging.INFO):
        report = verify_project_contracts(project, audit_log_file=str(audit), facts=_FACTS_NO_DOCKER)

    _assert_blocking(report, "dangerous-volumes", caplog)
    # R1: явный ассерт поверх хелпера — «/»-bind обязан называться в сообщении находки
    blocked = next(f for f in report.findings if f.contract_id == "dangerous-volumes" and f.severity == "block")
    assert "/:/host" in blocked.message or "/host" in blocked.message, (
        f"сообщение обязано называть root-bind: {blocked.message}"
    )


# 🧪 TRAP[TEST] · 2026-08-25 · NEGATIVE (R5) · dangerous-volumes — матрица запрещённых источников
# · Scenario: параметризованные входы REF-0006 (short/long syntax): абсолютный bind вне
#   allowlist, относительный traversal (../ и ./), ${VAR}-источник (fail-closed),
#   long-syntax bind docker.sock, каталог-префикс /var/run/docker
# · Remove if: классификация volume-источников меняется
@pytest.mark.parametrize(
    ("volume_yaml", "expect_substring"),
    [
        ('      - "/srv/data:/data"', "allowlist"),
        ('      - "../escape:/data"', "traversal"),
        ('      - "./local:/data"', "traversal"),
        ('      - "${DATA_DIR}:/data"', "named volume"),
        ('      - "/var/run/docker:/var/run/docker"', "docker"),
        ("      - { type: bind, source: /var/run/docker.sock, target: /sock }", "docker"),
        ("      - { type: bind, source: ../outside, target: /x }", "traversal"),
    ],
)
def test_verify_contracts_dangerous_volume_matrix_blocked(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    caplog: pytest.LogCaptureFixture,
    volume_yaml: str,
    expect_substring: str,
) -> None:
    """Каждый запрещённый источник маунта → L1 блок с релевантным сообщением."""
    compose = _baseline_compose_with("    volumes:", volume_yaml)
    project = _make_project(tmp_path, compose=compose)

    with caplog.at_level(logging.INFO):
        report = verify_project_contracts(project, facts=_FACTS_NO_DOCKER)

    _assert_blocking(report, "dangerous-volumes", caplog)
    vol_finding = next(f for f in report.findings if f.contract_id == "dangerous-volumes")
    assert expect_substring in vol_finding.message, (
        f"сообщение должно содержать {expect_substring!r}: {vol_finding.message}"
    )


# 🧪 TRAP[TEST] · 2026-08-25 · unit · dangerous-volumes — легитимные источники НЕ блокируются
# · Regression: named volumes (канон персистентности), anonymous container-only volumes,
#   long-syntax type: volume, mode-суффиксы (ro) — деплоятся без ложного блока
# · Last fail: N/A
# · Remove if: named-volume семантика меняется
@pytest.mark.parametrize(
    "volume_yaml",
    [
        '      - "appdata:/var/lib/app"',
        '      - "appdata:/var/lib/app:ro"',
        '      - "/var/cache/app"',
        "      - { type: volume, source: appdata, target: /var/lib/app }",
        "      - { type: tmpfs, target: /tmp }",
    ],
)
def test_verify_contracts_named_volumes_ok(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    caplog: pytest.LogCaptureFixture,
    volume_yaml: str,
) -> None:
    """Named/anonymous/tmpfs volume-записи → 0 findings от dangerous-volumes."""
    compose = _baseline_compose_with("    volumes:", volume_yaml)
    project = _make_project(tmp_path, compose=compose)

    with caplog.at_level(logging.INFO):
        report = verify_project_contracts(project, facts=_FACTS_NO_DOCKER)

    vol_findings = [f for f in report.findings if f.contract_id == "dangerous-volumes"]
    assert not report.has_blocking_violation(), f"легитимный volume заблокирован: {report.format_for_ssh()}"
    assert vol_findings == [], f"dangerous-volumes не должен находить нарушений: {vol_findings}"


# 🧪 TRAP[TEST] · 2026-08-25 · NEGATIVE (R5) · host-mode-keys — матрица namespace-ключей
# · Last fail: TRAP[BUG] 2026-08-16 обещал pid/sysctls в R5-наборе; network_mode:host/
#   pid:host/userns_mode/cgroup давали host-namespace без проверки (REF-0006)
# · Remove if: host-mode-keys контракт меняется
@pytest.mark.parametrize(
    ("mode_yaml", "expect_key"),
    [
        ('    network_mode: "host"', "network_mode"),
        ('    pid: "host"', "pid"),
        ('    userns_mode: "host"', "userns_mode"),
        ('    cgroup: "host"', "cgroup"),
        ('    cgroup_parent: "platform.slice"', "cgroup_parent"),
        ("    sysctls:\n      net.core.somaxconn: 1024", "sysctls"),
    ],
)
def test_verify_contracts_host_mode_keys_blocked(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    caplog: pytest.LogCaptureFixture,
    mode_yaml: str,
    expect_key: str,
) -> None:
    """network_mode/pid/userns_mode/cgroup==host и cgroup_parent/sysctls → L1 блок."""
    compose = _baseline_compose_with(*mode_yaml.splitlines())
    project = _make_project(tmp_path, compose=compose)

    with caplog.at_level(logging.INFO):
        report = verify_project_contracts(project, facts=_FACTS_NO_DOCKER)

    _assert_blocking(report, "host-mode-keys", caplog)
    mode_finding = next(f for f in report.findings if f.contract_id == "host-mode-keys")
    assert expect_key in mode_finding.message, f"сообщение должно называть {expect_key}: {mode_finding.message}"


# 🧪 TRAP[TEST] · 2026-08-25 · unit · host-mode-keys — bridge/none НЕ блокируются
# · Regression: только значение "host" шарит namespace ноды; bridge/none — изоляция по умолчанию
# · Last fail: N/A
# · Remove if: host-value семантика меняется
def test_verify_contracts_network_mode_bridge_ok(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """network_mode: bridge → НЕ violation (namespace ноды не шарится)."""
    compose = _baseline_compose_with('    network_mode: "bridge"')
    project = _make_project(tmp_path, compose=compose)

    with caplog.at_level(logging.INFO):
        report = verify_project_contracts(project, facts=_FACTS_NO_DOCKER)

    mode_findings = [f for f in report.findings if f.contract_id == "host-mode-keys"]
    assert mode_findings == [], f"network_mode: bridge не должен блокироваться: {mode_findings}"


# 🧪 TRAP[TEST] · 2026-08-25 · NEGATIVE (R5) · l1_only — сломанный YAML теперь БЛОК
# · Last fail: REF-0006 evidence — «сломанный YAML проходит L1 (parse filed as L2-warning)»:
#   pre-deploy гейт receive пропускал непарсящийся compose (severity warning при state≠active-full)
# · Scenario: один и тот же битый compose: l1_only=True → blocking; l1_only=False → warning
# · Remove if: l1_only severity-политика меняется
def test_verify_contracts_l1_only_broken_yaml_blocked(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """l1_only=True: compose-config-valid parse-fail → БЛОК; полный режим (False) → warning."""
    broken_compose = "services:\n  app:\n    image: busybox\n   broken_indent: [\n"
    project = _make_project(tmp_path, compose=broken_compose)

    with caplog.at_level(logging.INFO):
        report_l1 = verify_project_contracts(project, l1_only=True, facts=_FACTS_NO_DOCKER)

    parse_l1 = [f for f in report_l1.findings if f.contract_id == "compose-config-valid"]
    assert parse_l1, "битый compose обязан дать finding compose-config-valid"
    assert parse_l1[0].severity == "block", (
        f"l1_only: parse-fail обязан блокировать (REF-0006): {report_l1.format_for_ssh()}"
    )
    assert report_l1.has_blocking_violation() and report_l1.exit_code == 1

    with caplog.at_level(logging.INFO):
        report_full = verify_project_contracts(project, l1_only=False, facts=_FACTS_NO_DOCKER)

    parse_full = [f for f in report_full.findings if f.contract_id == "compose-config-valid"]
    assert parse_full and parse_full[0].severity == "warning", (
        "полный K3-режим сохраняет L2-warn семантику для parse-fail"
    )
    assert not any(f.severity == "block" for f in parse_full), (
        "в полном режиме parse-fail не должен быть blocking (state=baseline)"
    )
    logger.info("--- l1_only vs full on broken yaml ---")
    logger.info("%s", report_l1.format_for_ssh())
    logger.info("%s", report_full.format_for_ssh())
    assert _print_ldd_trajectory(caplog), "LDD: нет IMP:9 лога l1_only parse-fail"


# 🧪 TRAP[TEST] · 2026-08-25 · unit · l1_only — docker-L2 subprocess не исполняется
# · Regression: pre-up гейт остаётся без docker-латентности (176 A.2 инвариант сохранён);
#   расширение REF-0006 не должно тянуть compose-config/build-check в l1_only
# · Last fail: N/A
# · Remove if: l1_only scope меняется
def test_verify_contracts_l1_only_skips_docker_checks(monkeypatch, tmp_path: Path) -> None:
    """l1_only=True: валидный проект без lock → 0 findings (drift/docker-L2 пропущены)."""
    project = _make_project(tmp_path, lock_state=None)  # unmanaged: drift-finding был бы в full-режиме
    report = verify_project_contracts(project, l1_only=True, facts=_FACTS_NO_DOCKER)
    contract_ids = {f.contract_id for f in report.findings}
    assert "drift-practices" not in contract_ids, "l1_only не должен исполнять drift"
    assert "build-check" not in contract_ids, "l1_only не должен исполнять build-check"
    assert not report.has_blocking_violation()
