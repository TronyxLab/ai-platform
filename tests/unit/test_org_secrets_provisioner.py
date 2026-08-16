# GREP_SUMMARY: test org-secrets-provisioner context-promote gh-secret visibility plan resolution VPS AGE TELEGRAM S3
# STRUCTURE: ┌tmp node-configs + env + ssh/age fixtures┐ → ○ resolve_node_for_context → ○ resolve_secret_values → ○ ensure_context_secrets (dry-run/run_fn) → ⊕ LDD IMP:9 → ⎋ asserts
# region MODULE_CONTRACT
## @purpose  Unit-тесты core/internal/deploy/org_secrets_provisioner.py: резолв ноды по
##           контексту, резолв значений из канонических источников, visibility-план,
##           best-effort семантика ensure_context_secrets (dry-run + run_fn DI).
## @scope    Pure Python, tmp_path, 0 gh-вызовов (run_fn DI / dry_run).
## @invariants  Zero Hardcode Rule: только tmp_path; значения секретов не попадают в логи-asserts.
## @rationale 2026-08-16: авто-провижининг org-секретов при context-promote — регрессия
##            «PRIVATE visibility без --repos → CI видит пустые секреты» должна быть поймана.
# endregion MODULE_CONTRACT

import logging

import pytest

logger = logging.getLogger(__name__)

from core.internal.deploy.org_secrets_provisioner import (
    _ORG_SECRET_PLAN,
    ensure_context_secrets,
    resolve_node_for_context,
    resolve_secret_values,
)


def _write_node_config(base, name: str, host: str, contexts: list[str]) -> None:
    import yaml as _yaml

    node_dir = base / name
    node_dir.mkdir(parents=True)
    (node_dir / "node.yaml").write_text(
        _yaml.safe_dump(
            {"contexts": [{"name": c} for c in contexts], "node": {"name": name, "host": host}},
            allow_unicode=True,
        )
    )


def test_resolve_node_for_context_found(tmp_path) -> None:
    _write_node_config(tmp_path, "tronyx-vps", "1.2.3.4", ["tronyx-lab"])
    node = resolve_node_for_context("tronyx-lab", node_configs_dir=tmp_path)
    assert node is not None
    assert node.name == "tronyx-vps"
    assert node.host == "1.2.3.4"
    logger.info("[IMP:9][test_org_secrets] resolve_node found PASS")


def test_resolve_node_for_context_missing(tmp_path) -> None:
    _write_node_config(tmp_path, "tronyx-vps", "1.2.3.4", ["tronyx-lab"])
    assert resolve_node_for_context("asi-group", node_configs_dir=tmp_path) is None
    logger.info("[IMP:9][test_org_secrets] resolve_node missing PASS")


def test_resolve_secret_values_sources(tmp_path, monkeypatch) -> None:
    _write_node_config(tmp_path, "tronyx-vps", "9.9.9.9", ["tronyx-lab"])
    env_file = tmp_path / ".env"
    env_file.write_text(
        "TELEGRAM_BOT_TOKEN=tg-token\nTELEGRAM_CHAT_ID_CRITICAL=crit-chat\nS3_READONLY_ACCESS_KEY=ro-ak\n"
    )
    ssh_dir = tmp_path / "home" / ".ssh" / "ai-platform"
    ssh_dir.mkdir(parents=True)
    (ssh_dir / "tronyx-vps-ci").write_text("PRIVATE-KEY-CONTENT")
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    monkeypatch.setenv("AGE_SECRET_KEY", "AGE-SECRET-TEST")

    values = resolve_secret_values(
        "tronyx-lab",
        env={},
        node=resolve_node_for_context("tronyx-lab", node_configs_dir=tmp_path),
        env_file=env_file,
    )

    assert values["VPS_HOST"] == "9.9.9.9"
    assert values["VPS_SSH_KEY"] == "PRIVATE-KEY-CONTENT"
    assert values["AGE_SECRET_KEY"] == "AGE-SECRET-TEST"
    assert values["TELEGRAM_BOT_TOKEN"] == "tg-token"
    assert values["TELEGRAM_CHAT_ID_CRITICAL"] == "crit-chat"
    assert values["S3_READONLY_ACCESS_KEY"] == "ro-ak"
    assert "TELEGRAM_CHAT_ID_WARNING" not in values
    logger.info("[IMP:9][test_org_secrets] resolve values PASS")


def test_visibility_plan() -> None:
    """Единый план: TELEGRAM_* → all; VPS/AGE/S3_READONLY_* → selected с --repos ai-platform."""
    assert _ORG_SECRET_PLAN["TELEGRAM_BOT_TOKEN"] == ("all", None)
    assert _ORG_SECRET_PLAN["VPS_HOST"] == ("selected", ["ai-platform"])
    assert _ORG_SECRET_PLAN["AGE_SECRET_KEY"] == ("selected", ["ai-platform"])
    assert _ORG_SECRET_PLAN["S3_READONLY_ACCESS_KEY"] == ("selected", ["ai-platform"])
    logger.info("[IMP:9][test_org_secrets] visibility plan PASS")


def test_ensure_context_secrets_dry_run(tmp_path, caplog) -> None:
    _write_node_config(tmp_path, "tronyx-vps", "9.9.9.9", ["tronyx-lab"])
    caplog.set_level(logging.INFO)
    calls: list[list[str]] = []

    ok = ensure_context_secrets(
        "TronyxLab",
        "tronyx-lab",
        env={"TELEGRAM_BOT_TOKEN": "tg", "AGE_SECRET_KEY": "age"},
        run_fn=lambda _cmd, _value: calls.append(_cmd) or 0,
        dry_run=True,
        node_configs_dir=tmp_path,
    )
    assert ok is True
    assert calls == [], "dry-run: 0 gh-вызовов"
    logger.info("[IMP:9][test_org_secrets] dry-run PASS")


def test_ensure_context_secrets_run_fn_commands(tmp_path, monkeypatch) -> None:
    _write_node_config(tmp_path, "tronyx-vps", "9.9.9.9", ["tronyx-lab"])
    ssh_dir = tmp_path / "home" / ".ssh" / "ai-platform"
    ssh_dir.mkdir(parents=True)
    (ssh_dir / "tronyx-vps-ci").write_text("KEY")
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    calls: list[tuple[list[str], str]] = []

    ok = ensure_context_secrets(
        "TronyxLab",
        "tronyx-lab",
        env={"AGE_SECRET_KEY": "age-secret"},
        run_fn=lambda _cmd, _value: calls.append((_cmd, _value)) or 0,
        node_configs_dir=tmp_path,
    )
    assert ok is True
    by_name = {cmd[3]: (cmd, value) for cmd, value in calls if len(cmd) > 4}
    assert "VPS_HOST" in by_name, f"gh-вызовы: {list(by_name)}"
    vps_cmd, vps_val = by_name["VPS_HOST"]
    assert vps_val == "9.9.9.9"
    assert "--visibility" in vps_cmd and vps_cmd[vps_cmd.index("--visibility") + 1] == "selected"
    assert "--repos" in vps_cmd and vps_cmd[vps_cmd.index("--repos") + 1] == "ai-platform"
    age_cmd, age_val = by_name["AGE_SECRET_KEY"]
    assert age_val == "age-secret"
    assert age_cmd[age_cmd.index("--visibility") + 1] == "selected"
    # значения не печатаются — проверяем только структуру команд
    assert "VPS_SSH_KEY" in by_name
    logger.info("[IMP:9][test_org_secrets] run_fn commands PASS")


def test_ensure_context_secrets_gh_failure(tmp_path, monkeypatch) -> None:
    _write_node_config(tmp_path, "tronyx-vps", "9.9.9.9", ["tronyx-lab"])
    monkeypatch.setenv("AGE_SECRET_KEY", "age")

    ok = ensure_context_secrets(
        "TronyxLab",
        "tronyx-lab",
        env={},
        run_fn=lambda _cmd, _value: 1,  # gh падает
        node_configs_dir=tmp_path,
    )
    assert ok is False, "gh-сбой → False (caller решает, promote не падает)"
    logger.info("[IMP:9][test_org_secrets] gh-failure best-effort PASS")


def test_ensure_context_secrets_nothing_to_configure(tmp_path) -> None:
    _write_node_config(tmp_path, "tronyx-vps", "9.9.9.9", ["tronyx-lab"])
    # host есть, но ключи/токены отсутствуют — VPS_HOST всё равно настроится;
    # полный пустой кейс: контекст без ноды
    ok = ensure_context_secrets(
        "NoOrg", "unknown-context", env={}, run_fn=lambda _cmd, _value: 0, node_configs_dir=tmp_path
    )
    assert ok is True
    logger.info("[IMP:9][test_org_secrets] nothing-to-configure PASS")


@pytest.fixture(autouse=True)
def _no_gh_calls(monkeypatch) -> None:
    """Страховка: gh никогда не вызывается из unit-тестов (run_fn DI обязателен)."""
    import subprocess

    real_run = subprocess.run

    def _guard(*args, **kwargs):
        if args and "gh" in str(args[0][0]):
            pytest.fail("gh subprocess в unit-тестах запрещён — используй run_fn DI")
        return real_run(*args, **kwargs)

    monkeypatch.setattr("core.internal.deploy.org_secrets_provisioner.subprocess.run", _guard)
