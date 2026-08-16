# GREP_SUMMARY: gate templates-practices runtime sync_practices expected-files LANGUAGE_FOR_TYPE GENERATED-header upstream-only
# STRUCTURE: ▶ ┌tmp_path scaffold┐ → ◇ sync_practices → ◇ expected set by LANGUAGE_FOR_TYPE → ◇ GENERATED-шапка → ◇ pre-commit upstream-only → ⎋ assert
# region MODULE_CONTRACT
## @purpose  Гейт практик шаблонов в RUNTIME-модели (DevPlan 141 §2.1): шаблоны НЕ хранят
##           практики-файлы как образцы (GENERATED-дубли удалены в W1) — sync_practices
##           единственный источник. Гейт валидирует, что генератор покрывает ожидаемый
##           набор файлов по LANGUAGE_FOR_TYPE для backend/frontend, с GENERATED-шапкой,
##           и что .pre-commit-config.yaml остаётся upstream-only (аудит 137).
## @scope    Read-only гейт (make gate MODE=fast). Работает в tmp_path — репо не мутирует.
## @invariants
##   - backend (python): pyproject.toml + .pre-commit + conftest + test_health + practices.lock
##   - frontend (typescript,react): .pre-commit + conftest + test_health + practices.lock
##   - Все сгенерированные файлы (кроме practices.lock) несут GENERATED-шапку
##   - .pre-commit-config.yaml: ТОЛЬКО upstream (0 путей core/) + project-push-check
##   - R5 negative: удаление типа из LANGUAGE_FOR_TYPE детектируется (AssertionError)
## @rationale  Защита от дрейфа генератора практик (copy-paste debt, DevPlan 137 §7).
##             Runtime-модель заменяет статическую проверку файлов в шаблонах —
##             шаблоны больше не несут образцы, генератор — SoT.
## @changes  2026-08-06 · DevPlan 141 W1 — переработан из статической в runtime-модель
## @changes  2026-08-05 · DevPlan 137 W5 — создан (статическая проверка)
# endregion MODULE_CONTRACT

import logging
from pathlib import Path

import pytest

from core.internal.practices import manifest as practices_manifest
from core.internal.practices.generators import GENERATED_HEADER
from core.internal.practices.sync_practices import sync_practices
from tests.conftest import ldd_trajectory

logger = logging.getLogger(__name__)

# Ожидаемый набор GENERATED-файлов по языковому ключу ("," .join(LANGUAGE_FOR_TYPE[type])).
# Эталон = render_project_files (generators.py): pyproject.toml — только python-семейство.
# ⚠️ При изменении render_project_files — синхронизировать эталон здесь (дрейф = RED).
EXPECTED_BY_LANG: dict[str, set[str]] = {
    "python": {
        "pyproject.toml",
        ".pre-commit-config.yaml",
        "tests/conftest.py",
        "tests/test_health.py",
        "practices.lock",
    },
    "typescript,react": {
        ".pre-commit-config.yaml",
        "tests/conftest.py",
        "tests/test_health.py",
        "practices.lock",
    },
}

# Запрещённые платформенные пути в .pre-commit-config.yaml (аудит 137: upstream-only)
_PRECOMMIT_FORBIDDEN = ("core/entrypoints", "hooks/hygiene.sh", "hooks/commit_msg.sh")
_PRECOMMIT_REQUIRED = ("https://github.com/pre-commit/pre-commit-hooks", "project-push-check")


# region FUNC_expected_for_type
def _expected_for_type(ptype: str, lang_map: dict[str, tuple[str, ...]] | None = None) -> set[str]:
    """Ожидаемый набор файлов для типа проекта по текущему LANGUAGE_FOR_TYPE.

    ## @purpose  Детектор: покрывает ли EXPECTED_BY_LANG маппинг типа → языки.
    ##            Динамический доступ к practices_manifest.LANGUAGE_FOR_TYPE —
    ##            data-param (167 D3): lang_map=None → канон; negative-тест передаёт
    ##            сломанный маппинг напрямую (0 monkeypatch module-атрибута).
    ## @io        ⇥ ptype: str, lang_map: dict | None → ⎋ set[str] ⚡ AssertionError если тип/язык не покрыт
    ## @complexity O(1)
    """
    source = practices_manifest.LANGUAGE_FOR_TYPE if lang_map is None else lang_map
    langs = source.get(ptype)
    assert langs, f"LANGUAGE_FOR_TYPE не содержит тип '{ptype}' — маппинг сломан"
    lang_key = ",".join(langs)
    expected = EXPECTED_BY_LANG.get(lang_key)
    assert expected, f"lang_key {lang_key!r} (type={ptype}) не покрыт EXPECTED_BY_LANG — обновите эталон"
    return expected


# endregion FUNC_expected_for_type


# region FUNC_test_sync_generates_expected_files
@pytest.mark.gate
@ldd_trajectory
def test_gate_templates_practices_sync_generates_expected_files(caplog, tmp_path: Path) -> None:
    """sync_practices на свежем проекте из шаблона генерирует ожидаемый набор файлов.

    Новая модель (DevPlan 141): шаблоны НЕ хранят практики-файлы как образцы —
    sync_practices — единственный источник. Гейт валидирует, что генератор
    покрывает ожидаемый набор по LANGUAGE_FOR_TYPE.
    """
    covered: set[str] = set()

    for ptype in ("backend", "frontend"):
        expected = _expected_for_type(ptype)

        # Минимальный проект в tmp_path (контракт load_project_yaml: name/type/target_node)
        project_dir = tmp_path / f"test-{ptype}"
        project_dir.mkdir()
        (project_dir / "ai-platform.yaml").write_text(
            f"name: test-{ptype}\ntype: {ptype}\ntarget_node: test\nquality:\n  level: auto\n"
        )

        report = sync_practices(project_dir, force=True)

        actual: set[str] = set()
        for p in project_dir.rglob("*"):
            if p.is_file():
                rel = str(p.relative_to(project_dir))
                if rel == "ai-platform.yaml":
                    continue  # входной контекст sync_practices, не GENERATED-практика
                actual.add(rel)

        missing = expected - actual
        extra = actual - expected
        assert not missing, f"{ptype}: sync_practices не сгенерировал: {sorted(missing)}"
        assert not extra, f"{ptype}: sync_practices сгенерировал лишнее: {sorted(extra)}"
        assert report.lock_status == "written", f"{ptype}: practices.lock не записан ({report.lock_status})"

        # GENERATED-шапка во всех сгенерированных файлах (кроме practices.lock)
        for rel in expected - {"practices.lock"}:
            content = (project_dir / rel).read_text(encoding="utf-8")
            assert GENERATED_HEADER in content[:200], f"{ptype}/{rel}: нет GENERATED-шапки"

        # Upstream-only pre-commit (аудит 137, runtime-проверка сгенерированного файла)
        precommit = (project_dir / ".pre-commit-config.yaml").read_text(encoding="utf-8")
        for forbidden in _PRECOMMIT_FORBIDDEN:
            assert forbidden not in precommit, f"{ptype}: '{forbidden}' в .pre-commit-config.yaml (аудит 137)"
        for required in _PRECOMMIT_REQUIRED:
            assert required in precommit, f"{ptype}: отсутствует '{required}' в .pre-commit-config.yaml"

        logger.info(
            "[IMP:8][templates_practices] %s: %d GENERATED-файлов, upstream-only pre-commit ✓",
            ptype,
            len(expected),
        )
        covered.add(ptype)

    assert covered == {"backend", "frontend"}, f"Покрыты не все типы шаблонов: {covered}"
    logger.info("[IMP:9][templates_practices] PASS: sync_practices покрывает backend+frontend по LANGUAGE_FOR_TYPE")


# endregion FUNC_test_sync_generates_expected_files


# region FUNC_test_negative_language_for_type_drop
@pytest.mark.gate
def test_negative_language_for_type_drop_detected(tmp_path: Path) -> None:
    """R5 negative (DevPlan 141 RED-BLOCKER #1): тип, удалённый из LANGUAGE_FOR_TYPE, детектируется.

    Оригинальный дефект: статический гейт проверял ФАЙЛЫ в шаблонах, а не генератор —
    поломка маппинга LANGUAGE_FOR_TYPE оставалась бы незамеченной (тип тихо выпадал
    из проверки). Runtime-детектор _expected_for_type должен падать на сломанном маппинге.
    """
    broken = dict(practices_manifest.LANGUAGE_FOR_TYPE)
    broken.pop("backend", None)  # исходный вход: тип исчез из маппинга

    with pytest.raises(AssertionError):
        _expected_for_type("backend", lang_map=broken)

    logger.info("[IMP:9][templates_practices] R5 negative: сломанный LANGUAGE_FOR_TYPE детектирован ✓")


# endregion FUNC_test_negative_language_for_type_drop
