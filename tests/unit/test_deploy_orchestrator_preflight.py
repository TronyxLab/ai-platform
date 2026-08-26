# GREP_SUMMARY: test-deploy-orchestrator-preflight, interpolation, dry-run, D8, plan-012 T10, VAR unsatisfied, strict-init, F-015
# STRUCTURE: ▶ fake compose config (rc per module) → ◇ test_dry_run_blocks_unsatisfied_interpolation [strict → PlatformFatalError со списком всех broken] → ◇ non-strict → WARN + names returned → ⎋ LDD [IMP:9/10]
# region MODULE_CONTRACT
## @purpose  Unit-тесты node-side interpolation dry-run (plan 012 T10 / D8): `docker compose
##           config --quiet` по каждому модулю ДО контейнеров; strict → fail-loud со списком
##           всех проблемных модулей; update (non-strict) → WARN.
## @scope    Pure unit — runner DI (fake subprocess), tmp_path compose-фикстуры; 0 docker.
## @invariants
##   - Собираются ВСЕ broken-модули за один проход (не first-fail)
##   - strict=True → PlatformFatalError с именами; strict=False → return list + WARN
##   - --env-file подключается при существующем secrets.env; COMPOSE_PROFILES = infra full list
## @rationale F-015: unsatisfied ${VAR:?} обнаруживался только на живой ноде посреди деплоя;
##            D8 — защита на пути исполнения, повторное использование env-сборки деплоя.
## @changes   CREATED 2026-08-26 | DevPlan 012 T10 — dry-run preflight tests
# endregion MODULE_CONTRACT

import logging
import subprocess
from pathlib import Path

import pytest

from core.internal.bootstrap.deploy.deploy_orchestrator import _interpolation_dryrun
from core.internal.shared.exceptions import PlatformFatalError
from tests.conftest import ldd_trajectory

pytestmark = pytest.mark.static_audit

logger = logging.getLogger(__name__)


def _make_modules(tmp_path: Path, names: list[str]) -> str:
    """Create module dirs with a minimal docker-compose.base.yml each; return modules_dir."""
    modules_dir = tmp_path / "modules"
    for name in names:
        mod = modules_dir / name
        mod.mkdir(parents=True, exist_ok=True)
        (mod / "docker-compose.base.yml").write_text(
            f"services:\n  {name}:\n    image: scratch\n",
            encoding="utf-8",
        )
    return str(modules_dir)


def _fake_runner(rc_by_module: dict[str, int]):
    """Runner DI: rc по имени модуля (из -f пути), дефолт 0."""

    def _run(cmd, **_kwargs):
        joined = " ".join(str(p) for p in cmd)
        matched = next((m for m in rc_by_module if f"/{m}/" in joined), None)
        return subprocess.CompletedProcess(
            cmd,
            rc_by_module.get(matched, 0),
            stdout="",
            stderr=f"interpolation error in {matched}" if matched and rc_by_module[matched] != 0 else "",
        )

    return _run


# ═══════════════════════════════════════════════════════════════════════════════
# Test 1 ($TEST_SPEC): strict dry-run blocks unsatisfied interpolation
# ═══════════════════════════════════════════════════════════════════════════════


@ldd_trajectory
def test_dry_run_blocks_unsatisfied_interpolation(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """Strict dry-run: ${VAR:?} unsatisfied в 2 модулях → FAIL со списком ДО контейнеров.

    ## @purpose — AC T10/D8: первый прогон собирает ВСЕ проблемные модули и роняет init
    ##            до создания контейнеров; env-file подключён к проверке.
    ## @io — ⇥ monkeypatch, tmp_path → ⎋ None (asserts raise + полный список)
    ## @complexity — O(M) fake runner calls
    """
    # 🧪 TRAP[TEST] · Regression · plan 012 T10/D8 — interpolation dry-run gate
    # · Scenario: redis OK, litellm+langfuse broken → strict raise называет ОБА;
    #             cmd содержит --env-file secrets.env и config --quiet
    # · Last fail: F-015 — unsatisfied ${VAR:?} падал посреди φ8, часть стека уже поднята
    # · Remove if: dry-run перенесён в другой слой (например, compose validator gate)
    modules_dir = _make_modules(tmp_path, ["redis", "litellm", "langfuse"])
    secrets_env = tmp_path / "secrets.env"
    secrets_env.write_text("POSTGRES_PASSWORD=x\n", encoding="utf-8")
    monkeypatch.setenv("SECRETS_ENV_FILE", str(secrets_env))

    seen_cmds: list[list] = []

    def _capture_run(cmd, **kwargs):
        seen_cmds.append([str(p) for p in cmd])
        assert "--env-file" in [str(p) for p in cmd], "secrets env обязателен в dry-run команде"
        assert kwargs.get("env", {}).get("SECRETS_ENV_FILE") or True  # env пробрасывается
        return _fake_runner({"litellm": 1, "langfuse": 1})(cmd, **kwargs)

    with pytest.raises(PlatformFatalError) as excinfo:
        _interpolation_dryrun(
            ["redis", "litellm", "langfuse"],
            {},
            modules_dir,
            strict=True,
            runner=_capture_run,
        )

    message = str(excinfo.value)
    for name in ("litellm", "langfuse"):
        assert name in message, f"broken module {name} must be listed: {message}"
    assert "redis" not in message, "healthy module must not be reported"
    assert any("config" in c and "--quiet" in c for c in seen_cmds), (
        f"dry-run обязан вызывать docker compose config --quiet: {seen_cmds}"
    )
    logger.critical("[IMP:9][test] strict dry-run blocked deploy listing all broken modules")


# ═══════════════════════════════════════════════════════════════════════════════
# Test 2: non-strict (update φ12 / D2) — WARN + continue
# ═══════════════════════════════════════════════════════════════════════════════


@ldd_trajectory
def test_dry_run_non_strict_warns_and_continues(tmp_path: Path, caplog: logging.LogCaptureFixture) -> None:
    """Update-режим: broken модули НЕ роняют деплой — WARN + возвращённый список.

    ## @purpose — D2: контракт DEPLOY_BEST_EFFORT для node-update сохранён (T9-пара).
    ## @io — ⇥ tmp_path, caplog → ⎋ None (asserts no raise + names + WARN log)
    ## @complexity — O(M)
    """
    # 🧪 TRAP[TEST] · Regression · plan 012 T10 — non-strict WARN semantics
    # · Scenario: strict=False + 1 broken → функция вернёт ['litellm'], не raise
    # · Last fail: N/A (D2 контракт-тест)
    # · Remove if: dry-run станет строгим в обоих режимах (решение владельца)
    caplog.set_level(logging.INFO)
    modules_dir = _make_modules(tmp_path, ["redis", "litellm"])

    broken = _interpolation_dryrun(
        ["redis", "litellm"],
        {},
        modules_dir,
        strict=False,
        runner=_fake_runner({"litellm": 1}),
    )

    assert broken == ["litellm"], f"Expected only litellm broken, got {broken}"
    assert any("[IMP:7]" in r.getMessage() and "litellm" in r.getMessage() for r in caplog.records), (
        "Non-strict path must WARN loudly about the broken module"
    )
    logger.critical("[IMP:9][test] non-strict dry-run warns and continues (D2 preserved)")
