# GREP_SUMMARY: env-requires, unified, D4, R5, negative, divergence, module-yaml, secrets-manifest, validate-module-yaml, secrets-validator
# STRUCTURE: ▶ модуль с requirement не в manifest → ◇ check_requires_presence (module-driven) → ⊕ violation "not registered" │ ◇ check_runtime_env (manifest-driven) → ⊕ [] │ ▶ check_env_requires (unified) → ⊕ violation → ⎋ согласованный вердикт
# region MODULE_CONTRACT
## @purpose  R5 anti-survivorship negative-тесты для DevPlan 118 D4 — единый env-requires чекер
##           (core/internal/shared/env_requires.py). Проверяет устранение расхождения вердиктов
##           между module.yaml-driven (validate_module_yaml.check_env_requires_presence) и
##           manifest-driven (secrets_validator.check_env_requires) семантиками.
## @scope    Unit-тесты shared/env_requires.py + фасадов. tmp_path fixtures, native imports.
## @invariants
##   - Модуль с env_requires{secret,required}, отсутствующим в secrets-manifest → ОБА валидатора
##     (через единый чекер) дают согласованный вердикт (module-driven ловит расхождение)
##   - check_env_requires (unified) консолидирует обе семантики в один список
##   - LDD: IMP:9 лог в каждом успешном сценарии
## @rationale DevPlan 118 D4 AC: «negative-тест на расхождение (модуль с requirement, которого нет
##            в manifest → оба валидатора одинаково)». До D4 secrets_validator молча возвращал []
##            для модуля, требующего незарегистрированный секрет (расхождение вердиктов).
## @changes  2026-08-02 | DevPlan 118 D4 — создан
# endregion MODULE_CONTRACT

import logging
from pathlib import Path

import pytest
import yaml

from core.internal.bootstrap.deploy import secrets_validator
from core.internal.scripts import validate_module_yaml
from core.internal.shared import env_requires
from tests.conftest import ldd_trajectory

logger = logging.getLogger(__name__)


def _write_module_yaml(path: Path, env_requires_list: list) -> Path:
    """Write a minimal module.yaml with given env_requires entries."""
    data = {
        "name": "test-mod",
        "install_type": "docker",
        "description": "D4 unified env-requires test",
        "env_requires": env_requires_list,
    }
    path.write_text(yaml.dump(data))
    return path


def _write_secrets_manifest(path: Path, names: list[str]) -> Path:
    """Write secrets-manifest.yaml with given secret names (tier=required)."""
    secrets = [{"name": n, "tier": "required", "consumers": ["test-mod"], "source": "sops"} for n in names]
    path.write_text(yaml.dump({"version": 1, "secrets": secrets}))
    return path


def _write_dotenv(path: Path, names: list[str]) -> Path:
    """Write .env.example with given vars (non-empty values)."""
    path.write_text("\n".join(f"{n}=value" for n in names) + "\n")
    return path


# region FUNC_test_unified_checker_detects_unregistered_secret
## @purpose — R5 negative (D4): модуль требует секрет, которого НЕТ в secrets-manifest → единый
##            чекер check_env_requires детектирует (расхождение вердиктов устранено).
## @io — ⇥ tmp_path → ⎋ None (assert violation)
## @complexity — O(n*m + s)
@pytest.mark.unit
@ldd_trajectory

# 🧪 TRAP[TEST] · 2026-08-02 · R5 NEGATIVE · unified env-requires — незарегистрированный секрет (D4)
# · Last fail: до D4 secrets_validator.check_env_requires молча возвращал [] для модуля, требующего
# ·   секрет вне manifest (module-driven ловил, manifest-driven молчал → расходящиеся вердикты)
# · Remove if: env-requires проверка переезжает в другой механизм
def test_unified_checker_detects_unregistered_secret(tmp_path: Path, caplog: pytest.LogCaptureFixture) -> None:
    """Модуль требует SECRET_X, отсутствующий в manifest → unified чекер даёт violation."""
    caplog.set_level(logging.DEBUG)
    dotenv = _write_dotenv(tmp_path / ".env.example", ["SECRET_X"])
    manifest = _write_secrets_manifest(tmp_path / "secrets-manifest.yaml", [])  # SECRET_X НЕ зарегистрирован
    module = validate_module_yaml.load_module(
        _write_module_yaml(tmp_path / "module.yaml", [{"name": "SECRET_X", "type": "secret", "required": True}])
    )

    logger.info("[IMP:7][test_env_requires] Unified check: SECRET_X не в manifest → должен быть violation")
    unified = env_requires.check_env_requires(module, manifest, dotenv)

    # 1. Unified чекер ловит расхождение (module-driven часть)
    unregistered = [v for v in unified if "not registered" in v.lower() or "secrets-manifest" in v.lower()]
    logger.info("[IMP:8][test_env_requires] unified violations: %s", unified)
    assert len(unregistered) >= 1, f"D4 FAIL: unified чекер не детектировал SECRET_X вне manifest: {unified}"

    # 2. Фасад validate_module_yaml (module-driven) — та же семантика
    presence_violations = validate_module_yaml.check_env_requires_presence(module, dotenv, manifest)
    assert len(presence_violations) >= 1, "D4 FAIL: validate_module_yaml не детектировал SECRET_X вне manifest"

    logger.info("[IMP:9][test_env_requires] PASS: unified + module-driven согласованно ловят SECRET_X вне manifest")


# endregion FUNC_test_unified_checker_detects_unregistered_secret


# region FUNC_test_both_validators_agree_on_registered_secret
## @purpose — Позитивный контроль: секрет зарегистрирован и присутствует в env → все три пути
##            (unified, module-driven, manifest-driven) дают согласованный PASS.
## @io — ⇥ tmp_path + monkeypatch → ⎋ None (assert empty)
## @complexity — O(n*m + s)
@pytest.mark.unit
@ldd_trajectory

# 🧪 TRAP[TEST] · 2026-08-02 · REGRESSION · согласованность валидаторов при зарегистрированном секрете (D4)
# · Last fail: N/A (новый тест)
# · Remove if: env-requires проверка переезжает в другой механизм
def test_both_validators_agree_on_registered_secret(
    tmp_path: Path, caplog: pytest.LogCaptureFixture, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Секрет в manifest + в env → все валидаторы PASS (0 расхождений)."""
    caplog.set_level(logging.DEBUG)
    dotenv = _write_dotenv(tmp_path / ".env.example", ["SECRET_X"])
    manifest = _write_secrets_manifest(tmp_path / "secrets-manifest.yaml", ["SECRET_X"])
    module = validate_module_yaml.load_module(
        _write_module_yaml(tmp_path / "module.yaml", [{"name": "SECRET_X", "type": "secret", "required": True}])
    )
    monkeypatch.setenv("SECRET_X", "present-value")

    logger.info("[IMP:7][test_env_requires] Все валидаторы: SECRET_X зарегистрирован + в env")
    unified = env_requires.check_env_requires(module, manifest, dotenv)
    presence = validate_module_yaml.check_env_requires_presence(module, dotenv, manifest)
    runtime = secrets_validator.check_env_requires("test-mod", str(manifest))

    logger.info("[IMP:8][test_env_requires] unified=%s presence=%s runtime=%s", unified, presence, runtime)
    assert unified == [], f"D4 FAIL: unified вернул violation для валидного кейса: {unified}"
    assert presence == [], f"D4 FAIL: presence вернул violation для валидного кейса: {presence}"
    assert runtime == [], f"D4 FAIL: runtime вернул missing для валидного кейса: {runtime}"

    logger.info("[IMP:9][test_env_requires] PASS: все 3 пути согласованно PASS (0 расхождений)")


# endregion FUNC_test_both_validators_agree_on_registered_secret
