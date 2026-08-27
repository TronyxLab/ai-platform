# GREP_SUMMARY: test-dockerfile-bases from-parsing base-image multi-stage alias digest-pin unpinned module-base-images prebuild
# STRUCTURE: ┌tmp_path Dockerfile┐ → ○ parse_pinned_bases (multi-stage aliases | digest-pin exact | unpinned warning | platform flag | dedupe)
#           → ⊕ module_base_images (no Dockerfile → [] | build/Dockerfile resolution) → ⎋ asserts + LDD trajectory
# region MODULE_CONTRACT
## @purpose  Unit tests for core/internal/shared/dockerfile_bases.py — парсер ВНЕШНИХ базовых
##           образов из Dockerfile (F-03 pre-pull вход): two-pass stage-алиасы, digest-pin,
##           module_base_images резолюция Dockerfile|build/Dockerfile.
## @scope    Tests: parse_pinned_bases (multi-stage/точность/warning/dedupe/флаги), module_base_images.
## @invariants
##   - Все тесты используют tmp_path (Zero Hardcode Rule)
##   - Без Docker-зависимостей (чистый парсинг, no subprocess)
##   - LDD: траектория IMP:5/IMP:8 печатается; assert на фактический сигнал парсера
## @rationale Детерминированный pre-pull build-модулей (017-launch-validation F-03) зависит от
##            корректного извлечения FROM-баз — двухпроходная alias-семантика и digest-pin
##            warning защищены от регрессий.
# endregion MODULE_CONTRACT

import logging
from pathlib import Path

import pytest

from core.internal.shared.dockerfile_bases import module_base_images, parse_pinned_bases

logger = logging.getLogger(__name__)

_MULTI_STAGE_DOCKERFILE = """\
FROM alpine:3.21@sha256:48b0309ca019d89d40f670aa1bc06e426dc0931948452e8491e3d65087abc07d AS validate
FROM nousresearch/hermes-agent:v2026.8.3@sha256:16788311e2fa3035456bdc1bafb8ec2b1777db64ebf020af9bb7eb73c3712c9e AS base
FROM base
"""


def _write_dockerfile(tmp_path: Path, content: str, name: str = "Dockerfile") -> Path:
    """Писать Dockerfile во tmp_path и вернуть путь.

    ## @purpose — helper: создание тестового Dockerfile (Zero Hardcode — tmp_path).
    ## @io — ⇥ tmp_path, content, name → ⎋ Path
    """
    path = tmp_path / name
    path.write_text(content, encoding="utf-8")
    return path


# region FUNC_test_parse_pinned_bases_multi_stage
# 🧪 TRAP[TEST] · Regression · F-03 (017-launch-validation) · multi-stage: skip internal alias, include externals
# · Scenario: hermes-agent Dockerfile — validate/base стадии с AS + `FROM base` (internal)
# · Last fail: N/A (new test — исходная форма из core/modules/hermes-agent/Dockerfile)
# · Remove if: parse_pinned_bases alias-семантика меняется
def test_parse_pinned_bases_multi_stage_skips_alias_includes_externals(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    """multi-stage: внешние базы включены, внутренний stage-алиас `FROM base` пропущен."""
    caplog.set_level(logging.INFO)
    dockerfile = _write_dockerfile(tmp_path, _MULTI_STAGE_DOCKERFILE)

    result = parse_pinned_bases(dockerfile)

    logger.info("--- LDD TRAJECTORY (IMP:5-10) ---")
    for record in list(caplog.records):
        if "[IMP:" in record.message:
            logger.info("%s", record.message)
    logger.info("--- END LDD TRAJECTORY ---")

    assert result == [
        "alpine:3.21@sha256:48b0309ca019d89d40f670aa1bc06e426dc0931948452e8491e3d65087abc07d",
        "nousresearch/hermes-agent:v2026.8.3@sha256:16788311e2fa3035456bdc1bafb8ec2b1777db64ebf020af9bb7eb73c3712c9e",
    ]
    # Внутренний алиас «base» НЕ должен попасть в результат
    assert "base" not in result
    assert any("[IMP:8][parse_pinned_bases]" in r.message for r in caplog.records), "LDD: нет IMP:8 траектории парсера"


# endregion FUNC_test_parse_pinned_bases_multi_stage


# region FUNC_test_module_base_images_no_dockerfile
# 🧪 TRAP[TEST] · Edge-case · F-03 · no Dockerfile → []
# · Scenario: модуль без собственного образа (postgres/nginx) — Dockerfile отсутствует
# · Last fail: N/A (new test)
# · Remove if: module_base_images резолюция кандидатов меняется
def test_module_base_images_no_dockerfile(tmp_path: Path, caplog: pytest.LogCaptureFixture) -> None:
    """module_base_images возвращает [] когда ни Dockerfile, ни build/Dockerfile нет."""
    caplog.set_level(logging.INFO)

    result = module_base_images(tmp_path)

    logger.info("--- LDD TRAJECTORY (IMP:5-10) ---")
    for record in list(caplog.records):
        if "[IMP:" in record.message:
            logger.info("%s", record.message)
    logger.info("--- END LDD TRAJECTORY ---")

    assert result == []
    assert any("[IMP:7][module_base_images][no_dockerfile]" in r.message for r in caplog.records)


# endregion FUNC_test_module_base_images_no_dockerfile


# region FUNC_test_module_base_images_build_subdir
# 🧪 TRAP[TEST] · Edge-case · F-03 · build/Dockerfile резолвится (второй кандидат)
# · Scenario: hermes-agent структура — Dockerfile в build/ поддиректории
# · Last fail: N/A (new test)
# · Remove if: порядок кандидатов [Dockerfile, build/Dockerfile] меняется
def test_module_base_images_build_subdir(tmp_path: Path, caplog: pytest.LogCaptureFixture) -> None:
    """module_base_images находит build/Dockerfile когда корневого Dockerfile нет."""
    caplog.set_level(logging.INFO)
    build_dir = tmp_path / "build"
    build_dir.mkdir()
    _write_dockerfile(build_dir, "FROM debian:bookworm-slim@sha256:abc\n")

    result = module_base_images(tmp_path)

    logger.info("--- LDD TRAJECTORY (IMP:5-10) ---")
    for record in list(caplog.records):
        if "[IMP:" in record.message:
            logger.info("%s", record.message)
    logger.info("--- END LDD TRAJECTORY ---")

    assert result == ["debian:bookworm-slim@sha256:abc"]


# endregion FUNC_test_module_base_images_build_subdir


# region FUNC_test_parse_pinned_bases_digest_pin_exact
# 🧪 TRAP[TEST] · Regression · F-03 · digest-pin извлечение точное (байт-в-байт, DevPlan 170 W12 C1)
# · Scenario: status-page/backup-cron Dockerfile — python/debian с tag@sha256
# · Last fail: N/A (new test — исходная форма core/modules/{status-page,backup-cron}/Dockerfile)
# · Remove if: формат FROM-ref извлечения меняется
def test_parse_pinned_bases_digest_pin_exact(tmp_path: Path, caplog: pytest.LogCaptureFixture) -> None:
    """digest-pin ref извлекается точно: name:tag@sha256:... без обрезки/добавления."""
    caplog.set_level(logging.INFO)
    content = (
        "# comment\n"
        "FROM python:3.12-alpine@sha256:78098ea6a3a9c6a7727a5d4674e4a44e57e01fac878ee9cb4d24a86bd93916ff\n"
        "FROM debian:bookworm-slim@sha256:7b140f374b289a7c2befc338f42ebe6441b7ea838a042bbd5acbfca6ec875818\n"
        "# FROM ignored-comment-ref:comment\n"
    )
    dockerfile = _write_dockerfile(tmp_path, content)

    result = parse_pinned_bases(dockerfile)

    logger.info("--- LDD TRAJECTORY (IMP:5-10) ---")
    for record in list(caplog.records):
        if "[IMP:" in record.message:
            logger.info("%s", record.message)
    logger.info("--- END LDD TRAJECTORY ---")

    assert result == [
        "python:3.12-alpine@sha256:78098ea6a3a9c6a7727a5d4674e4a44e57e01fac878ee9cb4d24a86bd93916ff",
        "debian:bookworm-slim@sha256:7b140f374b289a7c2befc338f42ebe6441b7ea838a042bbd5acbfca6ec875818",
    ]
    # Комментарий-строка с FROM-подобным текстом НЕ парсится как инструкция
    assert "ignored-comment-ref:comment" not in result


# endregion FUNC_test_parse_pinned_bases_digest_pin_exact


# region FUNC_test_parse_pinned_bases_unpinned_warns
# 🧪 TRAP[TEST] · Regression · F-03 · непинненный FROM → warning IMP:5, но включается
# · Scenario: Dockerfile с `FROM debian:bookworm-slim` (без @sha256) — политика tag@sha256 нарушена
# · Last fail: N/A (new test — digest-pin политика DevPlan 170 W12 C1)
# · Remove if: unpinned-семантика (warn + include) меняется
def test_parse_pinned_bases_unpinned_warns_but_included(tmp_path: Path, caplog: pytest.LogCaptureFixture) -> None:
    """ref без @sha256 → warning [IMP:5], но включается в результат (pre-pull безвреден)."""
    caplog.set_level(logging.WARNING)
    dockerfile = _write_dockerfile(tmp_path, "FROM debian:bookworm-slim\n")

    result = parse_pinned_bases(dockerfile)

    logger.info("--- LDD TRAJECTORY (IMP:5-10) ---")
    for record in list(caplog.records):
        if "[IMP:" in record.message:
            logger.info("%s", record.message)
    logger.info("--- END LDD TRAJECTORY ---")

    assert result == ["debian:bookworm-slim"]
    assert any(
        "[IMP:5][parse_pinned_bases][unpinned]" in r.message and "debian:bookworm-slim" in r.message
        for r in caplog.records
    ), "ожидался warning IMP:5 про отсутствие digest-pin"


# endregion FUNC_test_parse_pinned_bases_unpinned_warns


# region FUNC_test_parse_pinned_bases_platform_flag
# 🧪 TRAP[TEST] · Edge-case · F-03 · `FROM --platform=... ref` — флаг пропускается, ref извлекается
# · Scenario: multi-arch Dockerfile (--platform перед базой)
# · Last fail: N/A (new test)
# · Remove if: обработка ведущих `--`-флагов FROM меняется
def test_parse_pinned_bases_platform_flag(tmp_path: Path, caplog: pytest.LogCaptureFixture) -> None:
    """ведущий --platform-флаг пропускается, ref извлекается корректно."""
    caplog.set_level(logging.INFO)
    dockerfile = _write_dockerfile(
        tmp_path,
        "FROM --platform=linux/amd64 python:3.12-alpine@sha256:abc\n",
    )

    result = parse_pinned_bases(dockerfile)

    logger.info("--- LDD TRAJECTORY (IMP:5-10) ---")
    for record in list(caplog.records):
        if "[IMP:" in record.message:
            logger.info("%s", record.message)
    logger.info("--- END LDD TRAJECTORY ---")

    assert result == ["python:3.12-alpine@sha256:abc"]


# endregion FUNC_test_parse_pinned_bases_platform_flag


# region FUNC_test_parse_pinned_bases_deduplicates
# 🧪 TRAP[TEST] · Edge-case · F-03 · один базовый образ в двух стадиях → один ref
# · Scenario: две стадии с одной базой (build + runtime) — пулить один раз
# · Last fail: N/A (new test)
# · Remove if: dedupe-семантика parse_pinned_bases меняется
def test_parse_pinned_bases_deduplicates(tmp_path: Path, caplog: pytest.LogCaptureFixture) -> None:
    """повторяющийся внешний ref схлопывается (order-preserving dedupe)."""
    caplog.set_level(logging.INFO)
    content = (
        "FROM python:3.12-alpine@sha256:abc AS build\nFROM python:3.12-alpine@sha256:abc AS runtime\nFROM runtime\n"
    )
    dockerfile = _write_dockerfile(tmp_path, content)

    result = parse_pinned_bases(dockerfile)

    logger.info("--- LDD TRAJECTORY (IMP:5-10) ---")
    for record in list(caplog.records):
        if "[IMP:" in record.message:
            logger.info("%s", record.message)
    logger.info("--- END LDD TRAJECTORY ---")

    assert result == ["python:3.12-alpine@sha256:abc"]
    assert "runtime" not in result


# endregion FUNC_test_parse_pinned_bases_deduplicates
