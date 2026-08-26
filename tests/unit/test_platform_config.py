# 🧪 TRAP[TEST] · Regression · test_platform_config — platform_config facade · ""-семантика (DevPlan 116 B5 T8, D2)
# GREP_SUMMARY: test, platform_config, defaults, S3_REGION, S3_PREFIX, S3_BUCKET, CONTEXT, PLATFORM_CONTEXT, fail-visible, PLATFORM_ROOT
# STRUCTURE: ▶ fixtures(PLATFORM_ROOT → tmp dir) → test_load → test_missing_file_empty → test_typed_accessors
# region MODULE_CONTRACT
## @purpose  Unit tests for platform_config facade module.
##           Verifies YAML loading via PLATFORM_ROOT, ""-семантику при отсутствии файла (D2),
##           и все typed accessors.
## @scope    Tests core/internal/config/platform_config.py
## @invariants
##   - Тесты используют PLATFORM_ROOT env + tmp_path (Zero Hardcode Rule; T8.3)
##   - Литеральных fallback'ов НЕТ (D2): отсутствие файла → "" + WARNING
##   - Reload через monkeypatch PLATFORM_ROOT + сброс module-level кэша
## @rationale DevPlan 116 B5 T8: fallback-константы удалены (fail-visible); cwd-эвристика заменена
##            PLATFORM_ROOT env + script-relative резолвингом. Тесты изолированы через PLATFORM_ROOT.
# endregion MODULE_CONTRACT

import logging
from collections.abc import Generator
from contextlib import suppress
from pathlib import Path

import pytest
import yaml

logger = logging.getLogger(__name__)

pytestmark = pytest.mark.static_audit


@pytest.fixture
def env_defaults_yaml() -> dict:
    """Return a dict matching platform-infra.yaml env_defaults section (SoT)."""
    return {
        "env_defaults": {
            "S3_REGION": "ru-1",
            "S3_PREFIX": "platform/backups",
            "S3_BUCKET": "test-bucket",
            "CONTEXT": "test",
            "PLATFORM_CONTEXT": "personal",
        }
    }


@pytest.fixture
def platform_root(tmp_path: Path) -> Path:
    """Create an isolated PLATFORM_ROOT directory (пустой — отсутствие platform-infra.yaml)."""
    root = tmp_path / "platform-root"
    root.mkdir(parents=True, exist_ok=True)
    return root


def _reload_platform_config(monkeypatch: pytest.MonkeyPatch, platform_root: Path) -> Generator:
    """Reload platform_config fresh with PLATFORM_ROOT isolated.

    ## @purpose — Изоляция: PLATFORM_ROOT указывает на tmp-директорию (не на репозиторий),
    ##            чтобы script-relative резолвинг не находил реальный platform-infra.yaml репо.
    ## 2026-08-04 (DevPlan 129 W4): del sys.modules ЗАМЕНЁН на reload_safe.reload_module —
    ##   канон reload-безопасности (удаление модуля из sys.modules пересоздаёт объект при
    ##   следующем import; старые __globals__-ссылки других модулей остаются на старый объект →
    ##   reload-гонка monkeypatch, DevPlan 129 W4).
    ## @io — ⇥ monkeypatch, platform_root → ⎋ Generator[module]
    """
    from _conftest.reload_safe import reload_module

    monkeypatch.setenv("PLATFORM_ROOT", str(platform_root))

    reload_module(
        "core.internal.config.platform_config",
        expected_file_substring="internal/config/platform_config.py",
    )
    from core.internal.config import platform_config as pc

    pc._defaults = {}
    pc._loaded = False

    yield pc

    monkeypatch.delenv("PLATFORM_ROOT", raising=False)


@pytest.fixture
def isolated_config(monkeypatch: pytest.MonkeyPatch, platform_root: Path) -> Generator:
    """platform_config с ПУСТЫМ PLATFORM_ROOT (файл отсутствует → ""-семантика).

    Дополнительно перенаправляет script-relative резолвинг (parents[3] от __file__) в tmp-дерево,
    чтобы канонический fallback не находил реальный platform-infra.yaml репозитория (Zero Hardcode).
    """
    gen = _reload_platform_config(monkeypatch, platform_root)
    pc = next(gen)
    # Перенаправляем script-relative корень в tmp (parents[3] от /tmp/.../fake/.../config.py = /tmp/.../fake)
    pc.__file__ = str(platform_root / "fake" / "core" / "internal" / "config" / "platform_config.py")
    yield pc
    with suppress(StopIteration):
        next(gen)


@pytest.fixture
def isolated_config_with_yaml(
    monkeypatch: pytest.MonkeyPatch, platform_root: Path, env_defaults_yaml: dict
) -> Generator:
    """platform_config с PLATFORM_ROOT, содержащим core/platform-infra.yaml (SoT, DevPlan 117 D23)."""
    infra_dir = platform_root / "core"
    infra_dir.mkdir(parents=True, exist_ok=True)
    with Path(infra_dir / "platform-infra.yaml").open("w", encoding="utf-8") as f:
        yaml.dump(env_defaults_yaml, f)
    yield from _reload_platform_config(monkeypatch, platform_root)


# region TEST_load_from_yaml
## @purpose  Verify that env_defaults are loaded from platform-infra.yaml via PLATFORM_ROOT (T8.3 + D23)
## @scenario Write core/platform-infra.yaml в PLATFORM_ROOT → import platform_config → verify all accessors
## @complexity 1
def test_load_from_yaml(isolated_config_with_yaml, caplog: pytest.LogCaptureFixture) -> None:
    """Verify that env_defaults are loaded from platform-infra.yaml (PLATFORM_ROOT)."""
    caplog.set_level(logging.INFO)
    pc = isolated_config_with_yaml

    # Trigger load via accessors
    assert pc.default_s3_region() == "ru-1"
    assert pc.default_context() == "test"
    # Sentinel
    assert not pc.default_s3_bucket_sentinel()
    assert not pc.default_context_sentinel()

    # Check LDD telemetry
    found_yaml_log = False
    for record in list(caplog.records):
        if "[IMP:" in record.message and "Loaded" in record.message:
            found_yaml_log = True
            break
    assert found_yaml_log, "Missing log: Loaded N defaults from platform-infra.yaml"


# endregion TEST_load_from_yaml


# region TEST_missing_file_empty_semantics
## @purpose  Verify ""-семантику (D2, T8) при отсутствии platform-infra.yaml — НЕ литеральные fallback'и
## @scenario Пустой PLATFORM_ROOT → все accessors возвращают "" (fail-visible)
## @complexity 1
def test_missing_file_empty_semantics(isolated_config, caplog: pytest.LogCaptureFixture) -> None:
    """Verify ""-семантику (D2): отсутствие platform-infra.yaml → '', без литеральных fallback'ов."""
    caplog.set_level(logging.INFO)
    pc = isolated_config

    # DevPlan 116 B5 T8 (D2): fallback-константы УДАЛЕНЫ — все accessors возвращают ""
    assert not pc.default_s3_region()
    assert not pc.default_context()
    # Sentinel values всегда ""
    assert not pc.default_s3_bucket_sentinel()
    assert not pc.default_context_sentinel()

    # LDD: громкий WARNING о ненайденном файле (fail-visible, D2)
    found_warning = False
    for record in list(caplog.records):
        if "[IMP:" in record.message and "not found" in record.message:
            found_warning = True
            break
    assert found_warning, "Missing WARNING log: platform-infra.yaml not found"


# endregion TEST_missing_file_empty_semantics


# region TEST_typed_accessors
## @purpose  Verify all typed accessors return correct types and values
## @scenario Test each accessor independently with a known YAML
## @complexity 1
def test_typed_accessors(isolated_config_with_yaml, caplog: pytest.LogCaptureFixture) -> None:
    """Verify all typed accessor functions."""
    caplog.set_level(logging.INFO)
    pc = isolated_config_with_yaml

    # ── default_s3_region ──
    val = pc.default_s3_region()
    assert isinstance(val, str)
    assert val == "ru-1"
    logger.info("%s", f"[IMP:9][test][s3_region] default_s3_region() = {val}")

    # ── default_s3_bucket_sentinel ──
    val = pc.default_s3_bucket_sentinel()
    assert isinstance(val, str)
    assert not val
    logger.info("%s", f"[IMP:9][test][s3_bucket_sentinel] default_s3_bucket_sentinel() = '{val}'")

    # ── default_context ──
    val = pc.default_context()
    assert isinstance(val, str)
    assert val == "test"
    logger.info("%s", f"[IMP:9][test][context] default_context() = {val}")

    # ── default_context_sentinel ──
    val = pc.default_context_sentinel()
    assert isinstance(val, str)
    assert not val
    logger.info("%s", f"[IMP:9][test][context_sentinel] default_context_sentinel() = '{val}'")

    # LDD trajectory
    logger.info("--- LDD TRAJECTORY (IMP:7-10) ---")
    for record in list(caplog.records):
        if "[IMP:" in record.message:
            imp_level = int(record.message.split("[IMP:")[1].split("]")[0])
            if imp_level >= 7:
                logger.info("%s", record.message)
    logger.info("--- END LDD TRAJECTORY ---")


# endregion TEST_typed_accessors


# region TEST_get_default
## @purpose  Verify get_default without fallback-аргумента (T8: сигнатура get_default(key) → str)
## @scenario Call get_default с известным/неизвестным ключом
## @complexity 1
def test_get_default(isolated_config_with_yaml, caplog: pytest.LogCaptureFixture) -> None:
    """Verify get_default(key) — без fallback-аргумента (DevPlan 116 B5 T8)."""
    caplog.set_level(logging.INFO)
    pc = isolated_config_with_yaml

    # Known key — returns from YAML
    assert pc.get_default("S3_REGION") == "ru-1"

    # Unknown key — returns "" (fail-visible, D2 — НЕ литеральный fallback)
    assert not pc.get_default("NONEXISTENT_KEY")


# endregion TEST_get_default


# 🧪 TRAP[TEST] · REF-0013 · Regression · platform_config _loaded-latch
# · Last fail: `_loaded=True` ставился ДО чтения файла → первый же неудачный load навсегда
#   фиксировал пустые defaults (повторный вызов = no-op на latch, retry невозможен).
# · Remove if: платформа сознательно выбирает «один load-попытка навсегда» (неожиданно).
# region TEST_failed_load_retries_until_success
## @purpose  REF-0013: неудачный load НЕ закрывает latch — после появления SoT-файла
##           следующий get_default() успешно загружает defaults (retry работает).
## @scenario Пустой PLATFORM_ROOT → "" → записать core/platform-infra.yaml → снова get_default
## @complexity 1
def test_failed_load_retries_until_success(isolated_config, caplog: pytest.LogCaptureFixture) -> None:
    """Failed load keeps the latch open; next call retries and succeeds."""
    import yaml as yaml_mod

    caplog.set_level(logging.INFO)
    pc = isolated_config

    # Шаг 1: файл отсутствует → "" (fail-visible), latch остаётся открытым
    assert not pc.default_context()
    assert not pc._loaded, "Failed load must NOT set the success latch (REF-0013)"

    # Шаг 2: SoT-файл появляется → следующий вызов обязан подхватить (retry).
    # Script-relative корень перенаправлен fixture в <platform_root>/fake (parents[3] от __file__).
    infra_dir = Path(pc.__file__).parents[3] / "core"
    infra_dir.mkdir(parents=True, exist_ok=True)
    with Path(infra_dir / "platform-infra.yaml").open("w", encoding="utf-8") as f:
        yaml_mod.dump({"env_defaults": {"CONTEXT": "retried-context", "S3_REGION": "ru-2"}}, f)

    assert pc.default_context() == "retried-context", (
        "Latch was closed after failed load — retry impossible (REF-0013 regression)"
    )
    assert pc.default_s3_region() == "ru-2"
    assert pc._loaded, "Successful load must set the latch"

    logger.info("[IMP:9][test_failed_load_retries_until_success] PASS: failed→retry→success sequence")


# endregion TEST_failed_load_retries_until_success


# region TEST_reset_cache_clears_loaded_state
## @purpose  reset_cache() (REF-0013): сбрасывает и словарь, и latch; следующий get_default
##           перечитывает SoT.
## @scenario Загрузить YAML → reset_cache() → убедиться в очистке → перезагрузить
## @complexity 1
def test_reset_cache_clears_loaded_state(isolated_config_with_yaml) -> None:
    """reset_cache clears _defaults/_loaded; subsequent access re-reads the SoT file."""
    pc = isolated_config_with_yaml

    assert pc.get_default("CONTEXT") == "test"  # первичная загрузка
    assert pc._loaded

    pc.reset_cache()

    assert not pc._loaded, "reset_cache must clear the latch"
    assert pc._defaults == {}, "reset_cache must clear cached defaults"
    assert pc.get_default("S3_REGION") == "ru-1", "Post-reset access must re-load from SoT"
    logger.info("[IMP:9][test_reset_cache_clears_loaded_state] PASS: cache reset + re-load")


# endregion TEST_reset_cache_clears_loaded_state
