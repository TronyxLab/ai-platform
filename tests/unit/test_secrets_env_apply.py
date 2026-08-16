"""
# GREP_SUMMARY: test_secrets_env_apply, apply-secrets-env, allowlist, R5-negative, env-write, DI-target, os-environ, 170-W6-D3
# STRUCTURE: ▶ tmp_path secrets.env → ◇ apply (allowlist ∪ prefixes → target) → ◇ R5-negative (вне allowlist НЕ пишется)
#            → ◇ default target = os.environ → ◇ FileNotFoundError пробрасывается → ⎋ LDD
# region MODULE_CONTRACT
## @purpose  Unit tests for core/internal/bootstrap/secrets_env_apply.py (DevPlan 170 W6-D3):
##           allowlist-запись секретов из secrets.env в env (изоляция мутации, выделена из
##           cert_orchestrator._source_secrets_env). Все сценарии через DI target= (0 monkeypatch
##           os.environ), кроме явного теста default-target (source-семантика).
## @scope    Тестирует apply_secrets_env: allowlist-фильтр, prefix-фильтр, R5-negative (имя вне
##           allowlist НЕ пишется), default target=os.environ, FileNotFoundError.
## @invariants
##   - ВСЯ инъекция через target= параметр (DI) — 0 monkeypatch.setenv (DI-HYG ≤98)
##   - Каждый тест валидирует IMP:9-лог через ldd_trajectory
## @rationale  R5 anti-survivorship (170 W6-D3): allowlist-контракт (154 W1 инвариант 4) —
##             негативный тест на исходный вход (имя вне allowlist), отсутствующий у монолитной
##             _source_secrets_env (мутация env была непроверяема).
## @changes  2026-08-15 | DevPlan 170 W6-D3 — создан вместе с secrets_env_apply.py
# endregion MODULE_CONTRACT
"""

import logging
import os
from pathlib import Path

import pytest

from core.internal.bootstrap.secrets_env_apply import apply_secrets_env
from tests._conftest.ldd import ldd_trajectory

pytestmark = pytest.mark.static_audit

logger = logging.getLogger(__name__)

_SECRETS_CONTENT = (
    "REGRU_API_Username=asi-user\n"
    "REGRU_API_Password=asi-pass\n"
    "WEBNAMES_API_KEY=*wkey\n"
    "S3_BUCKET=test-bucket\n"
    "GHCR_PULL_TOKEN=ghcr-secret-should-not-leak\n"
)


def _write_secrets(tmp_path: Path, content: str = _SECRETS_CONTENT) -> Path:
    """Записать secrets.env в tmp_path (Zero Hardcode)."""
    p = tmp_path / "secrets.env"
    p.write_text(content, encoding="utf-8")
    return p


# 🧪 TRAP[TEST] · Regression · allowlist-имена пишутся в target (DI), посторонние — НЕТ
# · Scenario: secrets.env с 5 парами; allowlist = {REGRU_*}; target={} → записаны только allowlist
# · Last fail: N/A (new test 170 W6-D3)
# · Remove if: apply_secrets_env allowlist-логика меняется
@ldd_trajectory
def test_apply_writes_only_allowlist(caplog, tmp_path) -> None:
    """allowlist-имена записываются в target; имена вне allowlist — не записываются."""
    env_path = _write_secrets(tmp_path)
    target: dict[str, str] = {}

    matched = apply_secrets_env(
        str(env_path),
        {"REGRU_API_Username", "REGRU_API_Password"},
        target=target,
    )

    assert matched == {"REGRU_API_Username": "asi-user", "REGRU_API_Password": "asi-pass"}
    assert target == matched, "target должен содержать ровно записанные пары"
    assert "WEBNAMES_API_KEY" not in target
    assert "GHCR_PULL_TOKEN" not in target
    logger.critical("[IMP:9][test] allowlist-имена записаны, посторонние отфильтрованы")


# 🧪 TRAP[TEST] · NEGATIVE (R5) · имя вне allowlist (и вне prefixes) НЕ пишется
# · Scenario: пустой allowlist + пустые prefixes → target остаётся пустым (даже с полным secrets.env)
# · Last fail: монолитная _source_secrets_env — фильтр был непроверяем (мутация глобального env)
# · Remove if: apply_secrets_env allowlist-логика меняется (R5-пара обязательна)
@ldd_trajectory
def test_apply_negative_outside_allowlist_not_written(caplog, tmp_path) -> None:
    """R5-negative: имя вне allowlist и вне prefixes НЕ попадает в target ни при каких условиях."""
    env_path = _write_secrets(tmp_path)
    target: dict[str, str] = {}

    matched = apply_secrets_env(str(env_path), set(), target=target)

    assert matched == {}, "пустой allowlist → ничего не записывается"
    assert target == {}, "target не должен содержать GHCR_PULL_TOKEN/WEBNAMES_API_KEY и т.д."
    logger.critical("[IMP:9][test] R5-negative: имя вне allowlist НЕ записано")


# 🧪 TRAP[TEST] · Regression · prefixes расширяют allowlist (S3_/PLATFORM_ префиксы)
# · Scenario: allowlist={WEBNAMES_API_KEY} + prefixes=("S3_",) → WEBNAMES_API_KEY и S3_BUCKET записаны
# · Last fail: N/A (new test 170 W6-D3)
# · Remove if: prefix-фильтр меняется
@ldd_trajectory
def test_apply_prefixes_extend_allowlist(caplog, tmp_path) -> None:
    """prefixes дополняют allowlist: ключи с префиксом записываются, остальные — нет."""
    env_path = _write_secrets(tmp_path)
    target: dict[str, str] = {}

    matched = apply_secrets_env(
        str(env_path),
        {"WEBNAMES_API_KEY"},
        prefixes=("S3_", "PLATFORM_"),
        target=target,
    )

    assert matched == {"WEBNAMES_API_KEY": "*wkey", "S3_BUCKET": "test-bucket"}
    assert "GHCR_PULL_TOKEN" not in target
    assert "REGRU_API_Username" not in target
    logger.critical("[IMP:9][test] prefixes (S3_) расширили allowlist — целевые ключи записаны")


# 🧪 TRAP[TEST] · Regression · target=None → os.environ (source-семантика, контракт provider-тестов)
# · Scenario: apply без target → значение пишется в реальный os.environ (уникальное имя + cleanup)
# · Last fail: N/A (new test 170 W6-D3)
# · Remove if: default target меняется
@ldd_trajectory
def test_apply_default_target_is_os_environ(caplog, tmp_path) -> None:
    """target=None → запись в os.environ (source-семантика, поведение 1:1 с _source_secrets_env)."""
    env_path = _write_secrets(tmp_path, "TEST_APPLY_ENV_KEY=value42\n")
    try:
        matched = apply_secrets_env(str(env_path), {"TEST_APPLY_ENV_KEY"})
        assert matched == {"TEST_APPLY_ENV_KEY": "value42"}
        assert os.environ.get("TEST_APPLY_ENV_KEY") == "value42", (
            "default target должен быть os.environ (креды провайдера читаются из env)"
        )
    finally:
        os.environ.pop("TEST_APPLY_ENV_KEY", None)
    logger.critical("[IMP:9][test] default target = os.environ — запись работает (source-семантика)")


# 🧪 TRAP[TEST] · Regression · отсутствующий файл → FileNotFoundError (caller ловит)
# · Scenario: apply на несуществующий путь → FileNotFoundError пробрасывается из shared parse
# · Last fail: N/A (new test 170 W6-D3)
# · Remove if: контракт исключений parse меняется
@ldd_trajectory
def test_apply_missing_file_raises(caplog, tmp_path) -> None:
    """Отсутствующий secrets.env → FileNotFoundError (обёртка cert_orchestrator ловит как non-fatal)."""
    missing = tmp_path / "nonexistent.env"
    with pytest.raises(FileNotFoundError):
        apply_secrets_env(str(missing), {"ANY_KEY"})
    logger.critical("[IMP:9][test] FileNotFoundError пробрасывается для отсутствующего файла")
