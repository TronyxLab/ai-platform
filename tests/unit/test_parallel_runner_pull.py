# GREP_SUMMARY: parallel-runner-pull oserror logged needs-build compose-read AI-0044
# STRUCTURE: ▶ monkeypatched read_text(OSError) → ⎋ True (skip-pull) + IMP:8 needs-build warn, retry_pull не зовётся
# region MODULE_CONTRACT
## @purpose  AI-0044 (DevPlan 17 T3.4, $TEST_SPEC row): OSError при чтении compose →
##           warn + needs-build (skip-pull), БЕЗ retry_pull по несуществующему файлу
##           и без молчаливого pass.
## @scope    tests/unit: tmp compose-dir с нечитаемым файлом; без docker.
## @invariants
##   - OSError на чтение compose → return True + IMP:8 «needs-build» в логе
# endregion MODULE_CONTRACT

import logging
from pathlib import Path

import pytest

from core.internal.bootstrap.deploy import parallel_runner

logger = logging.getLogger(__name__)


# 🧪 TRAP[TEST] · 2026-08-26 · P3 · OSError → warn + needs-build (AI-0044)
# · Regression: except OSError: pass глотал ошибку чтения compose молча — модуль
#   ушёл бы в retry_pull по битому пути и красил фазу без диагностики
# · Scenario: resolve_compose_file находит файл, но read_text бросает OSError
#   (chmod 000) → pull_module_images True + IMP:8 needs-build; retry_pull НЕ вызван
# · Last fail: DevPlan 17 верификация @64c2090 ($TEST_SPEC T3.4)
# · Remove if: pull переезжает на streaming-read с централизованной диагностикой
def test_oserror_logged_needs_build(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    caplog.set_level(logging.INFO)

    module_dir = tmp_path / "mymod"
    module_dir.mkdir()
    compose = module_dir / "docker-compose.base.yml"
    compose.write_text("services: {}\n", encoding="utf-8")
    compose.chmod(0o000)  # нечитаемый файл → OSError при read_text

    retried: list[str] = []
    monkeypatch.setattr(parallel_runner, "_shared_retry_pull", lambda *a, **_kw: retried.append(a) or True)
    monkeypatch.setattr(parallel_runner, "resolve_compose_file", lambda *_a, **_kw: compose)

    result = parallel_runner.pull_module_images("mymod", None, None, str(tmp_path), str(tmp_path))

    assert result is True, "needs-build семантика: skip-pull с успехом фазы"
    assert not retried, "retry_pull по нечитаемому compose запрещён"
    assert any("needs-build" in r.getMessage() for r in caplog.records), (
        "IMP:8 needs-build warn обязателен"
    )
    logger.critical("[IMP:9][test] OSError → warn + needs-build, no retry_pull — OK (AI-0044)")
