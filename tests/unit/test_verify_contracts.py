#!/usr/bin/env python3
"""
# GREP_SUMMARY: test-verify-contracts, K3, L1, L2, L3, ports-blocked, healthcheck-blocked, secret-literal, external-network, env-file, drift-lock, legacy-grace, baseline-green, R5, audit
# STRUCTURE: ▶ _make_project (ai-platform.yaml + compose + lock) → ◇ docker absent (monkeypatch which) → ○ verify_project_contracts (audit в tmp) → ⊕ assert severity/blocking/format → ◇ LDD IMP:9 trajectory → ⎋
# region MODULE_CONTRACT
## @purpose  Unit-тесты verify_contracts (DevPlan 137 W4, K3-канал VPS): R5-negative-тесты для
##           каждого L1-контракта (ports/healthcheck/secrets/external-net/env-file), дрейф
##           practices.lock по state (active-full → L2 блок; baseline/proposed → warning),
##           legacy-grace (без lock + PRACTICES_LEGACY_GRACE=1 → L1 warning-only + LEGACY),
##           baseline-зелёный (валидный проект → 0 violations, exit 0), аудит записей.
## @scope    $TEST_SPEC 137 W4: tests/unit/test_verify_contracts.py (8 тестов, список §5 W4
##           «Negative-тесты R5» + baseline_green). Native imports, tmp_path, monkeypatch.
## @invariants
##   - docker отсутствует в тестах (monkeypatch which → None) — L2 docker-проверки skip
##   - audit пишется в tmp-файл (audit_log_file) — НЕ в /var/log/platform (изоляция)
##   - practices.lock — минимальный валидный YAML (read_lock-парсится)
##   - LDD: IMP:9-траектория через _print_ldd_trajectory (Anti-Illusion Rule)
##   - R5: negative-тесты с ТОЧНЫМ входом бага (ports: ["8080:80"], password: hunter2, ...)
## @rationale AC W4: деплой с ports:/без healthcheck/внешней сетью вне allowlist — блок при
##            любом уровне; дрейф lock — блок в full, warning в baseline/proposed; legacy-grace.
## @changes  2026-08-05 · DevPlan 137 W4 — создан
# endregion MODULE_CONTRACT
"""

import logging
import os
from pathlib import Path

import pytest

from core.internal.deploy.verify_contracts import verify_project_contracts
from core.internal.shared.audit_logger import read_audit_log
from tests.conftest import _print_ldd_trajectory

logger = logging.getLogger(__name__)

# ── Docker-бинарник в тестах НЕ доступен (L2 docker-проверки skip, без реального docker) ──
_DOCKER_ABSENT: bool = os.environ.get("TEST_REAL_DOCKER", "0") == "1"

# Валидный baseline compose (проходит ВСЕ L1-контракты: env_file .env.platform, healthcheck,
# platform labels, external proxy-net из allowlist, БЕЗ ports/секретов)
_VALID_COMPOSE: str = """\
services:
  app:
    image: busybox:latest
    env_file:
      - .env.platform
    healthcheck:
      test: ["CMD", "echo", "ok"]
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
    print("--- verify contracts report ---")
    print(rendered)
    assert _print_ldd_trajectory(caplog), "LDD: нет IMP:9 лога verify_contracts"


# endregion HELPER__assert_blocking


# 🧪 TRAP[TEST] · 2026-08-05 · NEGATIVE (R5) · ports-published — AC W4 «ports: блок при любом уровне»
# · Last fail: legacy — проект с ports: деплоился без единой проверки (deploy-project.yml verify не проверял код)
# · Remove if: ports-published контракт меняется
def test_verify_contracts_ports_blocked(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """compose с ports: ["8080:80"] → L1 violation (ports-published), блок, exit 1."""
    if not _DOCKER_ABSENT:
        monkeypatch.setattr("core.internal.deploy.verify_contracts.shutil.which", lambda _: None)
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
# · Last fail: legacy — сервис без healthcheck деплоился (healthcheck_poller канон не enforced на деплое)
# · Remove if: healthcheck-present контракт меняется (ИЛИ healthcheck, ИЛИ platform.healthcheck label)
def test_verify_contracts_no_healthcheck_blocked(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """service без healthcheck: (и без labels platform.healthcheck) → L1 violation, блок."""
    if not _DOCKER_ABSENT:
        monkeypatch.setattr("core.internal.deploy.verify_contracts.shutil.which", lambda _: None)
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
# · Last fail: legacy — password: hunter2 в compose деплоился (секрет утекал в git проекта)
# · Remove if: secrets-in-compose контракт меняется (суффикс-матчинг password|api_key|token)
def test_verify_contracts_secret_literal_blocked(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """password: hunter2 (литерал, не ${VAR}) в environment → L1 violation, блок."""
    if not _DOCKER_ABSENT:
        monkeypatch.setattr("core.internal.deploy.verify_contracts.shutil.which", lambda _: None)
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
# · Last fail: legacy — кастомная external-сеть подключала проект к чужой сети (без контроля)
# · Remove if: external-networks контракт меняется (allowlist из practices_manifest.yaml)
def test_verify_contracts_external_network_unknown_blocked(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """external-сеть вне allowed_external_networks канона → L1 violation, блок."""
    if not _DOCKER_ABSENT:
        monkeypatch.setattr("core.internal.deploy.verify_contracts.shutil.which", lambda _: None)
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
# · Last fail: legacy — env_file: .env (секреты в коммитимом файле) деплоился
# · Remove if: env-file-contract контракт меняется (только .env.platform допустим)
def test_verify_contracts_env_file_wrong_blocked(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """env_file: .env (не .env.platform) → L1 violation, блок."""
    if not _DOCKER_ABSENT:
        monkeypatch.setattr("core.internal.deploy.verify_contracts.shutil.which", lambda _: None)
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
# · Last fail: legacy — устаревший practices.lock (version 0) не влиял на деплой (VPS не сверял канон)
# · Remove if: drift-practices контракт меняется (L2: block в active-full, warning иначе)
def test_verify_contracts_drift_full_blocked(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """lock устарел (version 0 < canon 1): state=active-full → L2 блок; baseline/proposed → warning."""
    if not _DOCKER_ABSENT:
        monkeypatch.setattr("core.internal.deploy.verify_contracts.shutil.which", lambda _: None)

    # ── active-full: L2 drift → БЛОК ──
    project_full = _make_project(tmp_path, name="proj-full", lock_state="active-full", lock_version=0)
    audit_full = tmp_path / "audit-full.jsonl"
    with caplog.at_level(logging.INFO):
        report_full = verify_project_contracts(project_full, audit_log_file=str(audit_full))
    drift_full = [f for f in report_full.findings if f.contract_id == "drift-practices"]
    assert drift_full and drift_full[0].severity == "block", (
        f"drift в active-full обязан блокировать: {report_full.format_for_ssh()}"
    )
    assert report_full.has_blocking_violation() and report_full.exit_code == 1

    # ── baseline: L2 drift → warning non-blocking ──
    project_base = _make_project(tmp_path, name="proj-base", lock_state="baseline", lock_version=0)
    audit_base = tmp_path / "audit-base.jsonl"
    with caplog.at_level(logging.INFO):
        report_base = verify_project_contracts(project_base, audit_log_file=str(audit_base))
    drift_base = [f for f in report_base.findings if f.contract_id == "drift-practices"]
    assert drift_base and drift_base[0].severity == "warning", "drift в baseline обязан быть warning"
    assert not report_base.has_blocking_violation() and report_base.exit_code == 0
    assert report_base.has_warnings()

    # ── proposed: L2 drift → warning non-blocking ──
    project_prop = _make_project(tmp_path, name="proj-prop", lock_state="proposed", lock_version=0)
    audit_prop = tmp_path / "audit-prop.jsonl"
    with caplog.at_level(logging.INFO):
        report_prop = verify_project_contracts(project_prop, audit_log_file=str(audit_prop))
    drift_prop = [f for f in report_prop.findings if f.contract_id == "drift-practices"]
    assert drift_prop and drift_prop[0].severity == "warning", "drift в proposed обязан быть warning"
    assert not report_prop.has_blocking_violation() and report_prop.exit_code == 0

    entries = read_audit_log(str(audit_full))
    assert entries[-1]["status"] == "BLOCKED"
    assert entries[-1]["state"] == "active-full"
    print("--- drift scenarios ---")
    print(report_full.format_for_ssh())
    print(report_base.format_for_ssh())
    assert _print_ldd_trajectory(caplog), "LDD: нет IMP:9 лога drift-practices"


# 🧪 TRAP[TEST] · 2026-08-05 · NEGATIVE (R5) · legacy-grace — TRAP §10.2 «L1 warning-only + [PRACTICES:LEGACY]»
# · Last fail: legacy — отсутствие practices.lock НЕ давало L1-warning (L1 сразу блокировал бы легаси-деплои)
# · Remove if: grace-флаг снят после миграции всех продакшен-проектов (TRAP §10.2 Rev)
def test_verify_contracts_legacy_grace(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """без practices.lock + PRACTICES_LEGACY_GRACE=1 → L1 warning-only + [PRACTICES:LEGACY]."""
    if not _DOCKER_ABSENT:
        monkeypatch.setattr("core.internal.deploy.verify_contracts.shutil.which", lambda _: None)
    compose = (
        "services:\n  app:\n    image: busybox:latest\n"
        '    ports:\n      - "8080:80"\n'  # L1-нарушение — но grace → warning
        '    healthcheck:\n      test: ["CMD", "echo", "ok"]\n'
        '    labels:\n      - "platform.type=backend"\n'
        "    networks:\n      - proxy-net\n"
        "networks:\n  proxy-net:\n    external: true\n"
    )
    project = _make_project(tmp_path, compose=compose, lock_state=None)
    audit = tmp_path / "audit.jsonl"

    with caplog.at_level(logging.INFO):
        report = verify_project_contracts(project, env={"PRACTICES_LEGACY_GRACE": "1"}, audit_log_file=str(audit))

    assert report.legacy_grace is True
    assert report.state == "legacy"
    assert not report.has_blocking_violation(), f"grace должен снять L1-блок: {report.format_for_ssh()}"
    assert report.exit_code == 0
    assert report.has_warnings()
    rendered = report.format_for_ssh()
    assert "[PRACTICES:LEGACY]" in rendered
    assert "[PRACTICES:PROPOSE]" in rendered  # L1 finding в warning-only режиме
    assert "[PRACTICES:BLOCK]" not in rendered
    ports = [f for f in report.findings if f.contract_id == "ports-published"]
    assert ports and ports[0].severity == "warning", f"L1 в grace обязан быть warning: {ports}"

    entries = read_audit_log(str(audit))
    assert entries[-1]["status"] == "WARN"
    assert entries[-1]["legacy_grace"] is True
    assert _print_ldd_trajectory(caplog), "LDD: нет IMP:9 лога legacy-grace"


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
    if not _DOCKER_ABSENT:
        monkeypatch.setattr("core.internal.deploy.verify_contracts.shutil.which", lambda _: None)
    project = _make_project(tmp_path, lock_state="baseline", lock_version=1)
    audit = tmp_path / "audit.jsonl"

    with caplog.at_level(logging.INFO):
        report = verify_project_contracts(project, audit_log_file=str(audit))

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
    """Проект-директория отсутствует → пустой отчёт (state=legacy, 0 findings, exit 0)."""
    audit = tmp_path / "audit.jsonl"
    with caplog.at_level(logging.INFO):
        report = verify_project_contracts(tmp_path / "nonexistent", audit_log_file=str(audit))
    assert report.findings == ()
    assert not report.has_blocking_violation()
    assert report.exit_code == 0
    assert not audit.exists(), "отсутствующий проект НЕ должен писать аудит"
    assert _print_ldd_trajectory(caplog), "LDD: нет IMP:9 лога (missing project)"
