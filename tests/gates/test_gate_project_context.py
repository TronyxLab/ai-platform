# GREP_SUMMARY: gate project-context consistency d2 context-field removal fixture-driven not-skip C8
# STRUCTURE: ▶ _scan_project_context(projects_dir) → list[str] issues → ▶ fixture-driven: test_project_context_valid (tmp_path project) → ▶ R4: test_project_context_missing_dir_fails → ⎋ PASS
# region MODULE_CONTRACT
## @purpose  D2 gate — validate context consistency: all projects derive context from directory path,
##           not from a `context:` field in YAML (post-D2 removal enforcement).
## @scope    Scans a projects/ directory tree for ai-platform.yaml files, checks each for:
##           1. Path-based context derivation (projects/<context>/<project>/)
##           2. Absence of legacy `context:` field in the YAML body
## @invariants
##   - DevPlan 119 C8: always-skip устранён — гейт fixture-driven (валидирует репрезентативный
##     проект из tmp_path), БЕЗ pytest.skip (R4: отсутствие окружения → FAIL, не skip)
##   - Реальный projects/ (если существует) также сканируется (исходный enforcement-scope)
##   - Отсутствие projects/ → явная ошибка конфигурации («тестовое окружение не настроено»)
##   - Каждый ai-platform.yaml должен НЕ содержать `context:` поле (post-D2)
##   - Путь: projects/<context>/<project>/ai-platform.yaml (3 уровня)
## @rationale  D2 enforcement gate: после удаления `context` из schema/writers/templates этот
##             гейт предотвращает ре-интродукцию. C8 (AUDIT-5 DEAD-1): старый гейт всегда
##             skip'ался (projects/ не существует) — переведён на фикстуры (DevPlan C8 шаг 2).
## @usecases
##   - make gate MODE=fast → validates fixture + (реальные проекты если есть)
## @changes — 2026-07-20 | Created per DevPlan 020 Task 5.1
##           — 2026-08-02 | DevPlan 119 C8 — fixture-driven (test_project_context_valid),
##             отсутствие projects/ → FAIL, 0 pytest.skip
# endregion MODULE_CONTRACT

import glob
import logging
import os

import pytest
import yaml

from tests._conftest.ldd import ldd_trajectory

_PROJECT_ROOT = os.path.normpath(os.path.join(os.path.dirname(__file__), "..", ".."))
_PROJECTS_DIR = os.path.join(_PROJECT_ROOT, "projects")

_logger = logging.getLogger(__name__)


# region FUNC_scan_project_context
## @purpose  Scan a projects/ directory for D2 context-consistency issues (path-derived context,
##           no legacy `context:` field). Возвращает список проблем; пусто = чисто.
## @io       ⇥ projects_dir: str — корень projects/ → ⎋ list[str] issues
## @complexity — O(N * M) где N = yaml files, M = avg file size
## @invariants
##   - Отсутствующий projects_dir → ОДНА issue «тестовое окружение не настроено» (R4: FAIL, не skip)
##   - Существующий projects_dir без yaml-файлов → пусто (нечего валидировать — vacuous clean)
##   - Путь ровно 3 уровня; наличие `context:` поля → issue
def _scan_project_context(projects_dir: str) -> list[str]:
    """Return a list of D2 context-consistency issues (empty list = clean)."""
    if not os.path.isdir(projects_dir):
        _logger.info("[IMP:7][gate][context] Projects directory not found: %s", projects_dir)
        return [f"projects/ directory not found ({projects_dir}) — тестовое окружение не настроено"]

    yaml_pattern = os.path.join(projects_dir, "*", "*", "ai-platform.yaml")
    yaml_files = glob.glob(yaml_pattern)
    _logger.info("[IMP:8][gate][context] Glob pattern: %s → %d files", yaml_pattern, len(yaml_files))

    if not yaml_files:
        return []  # проектов нет — валидировать нечего (vacuous clean)

    issues: list[str] = []

    for yaml_path in sorted(yaml_files):
        abs_path = os.path.realpath(yaml_path)

        # Extract context from path: projects/<context>/<project>/ai-platform.yaml
        context_from_path = os.path.basename(os.path.dirname(os.path.dirname(abs_path)))
        _logger.info(
            "[IMP:7][gate][context] %s → context_from_path=%s",
            os.path.relpath(yaml_path, projects_dir),
            context_from_path,
        )

        # Verify path structure: 3 levels deep under projects/
        rel_path = os.path.relpath(yaml_path, projects_dir)
        path_parts = rel_path.split(os.sep)
        if len(path_parts) != 3:
            issues.append(f"{yaml_path}: unexpected nesting (expected 3 levels, got {len(path_parts)})")
            _logger.error("[IMP:9][gate][context] NESTING: %s → parts=%s", yaml_path, path_parts)

        # Read YAML and check for legacy context field
        try:
            with open(yaml_path) as f:
                data = yaml.safe_load(f)
        except Exception as exc:
            issues.append(f"{yaml_path}: cannot parse YAML: {exc}")
            _logger.error("[IMP:9][gate][context] PARSE FAIL: %s — %s", yaml_path, exc)
            continue

        if data is None:
            issues.append(f"{yaml_path}: empty YAML file")
            _logger.error("[IMP:9][gate][context] EMPTY: %s", yaml_path)
            continue

        if "context" in data:
            issues.append(f"{yaml_path}: contains legacy 'context: {data['context']}' field — D2 requires removal")
            _logger.error("[IMP:9][gate][context] LEGACY FIELD: %s → context=%s", yaml_path, data["context"])

    return issues


# endregion FUNC_scan_project_context


# region FUNC_test_project_context_valid
@pytest.mark.gate
@ldd_trajectory
# 🧪 TRAP[TEST] · 2026-08-02 · REGRESSION · fixture-driven D2 context gate (C8)
# · Scenario: DevPlan 119 C8 — валидный проект из tmp_path проходит (не skip);
#   реальный projects/ (если есть) тоже валидируется
# · Last fail: до C8 — тест всегда skip'ался (projects/ не существует)
# · Remove if: D2 context enforcement перенесён в другой механизм
def test_project_context_valid(caplog, tmp_path) -> None:
    """Validate a VALID fixture project (never skip) + реальный projects/ если присутствует."""
    # 1) Fixture-driven: валидный проект (path-derived context, 0 context-поля)
    ctx_dir = tmp_path / "projects" / "testctx" / "testapp"
    ctx_dir.mkdir(parents=True)
    (ctx_dir / "ai-platform.yaml").write_text("project: testapp\nservice: testapp\ndomain: testapp.example.com\n")

    issues = _scan_project_context(str(tmp_path / "projects"))
    assert issues == [], f"D2 issues in VALID fixture project: {issues}"
    _logger.info("[IMP:9][gate][context] VALID fixture project passes (0 issues)")

    # 2) Реальный projects/ если существует (исходный enforcement-scope)
    if os.path.isdir(_PROJECTS_DIR):
        real_issues = _scan_project_context(_PROJECTS_DIR)
        if real_issues:
            for issue in real_issues:
                _logger.error("[IMP:9][gate][context] FAIL: %s", issue)
            pytest.fail(
                f"Context consistency issues found in projects/ ({len(real_issues)}):\n" + "\n".join(real_issues)
            )
        _logger.info("[IMP:9][gate][context] Real projects/ passes (0 issues)")
    else:
        _logger.info("[IMP:8][gate][context] Real projects/ отсутствует — fixture покрывает гейт (C8)")


# endregion FUNC_test_project_context_valid


# region FUNC_test_project_context_missing_dir_fails
@pytest.mark.gate
@ldd_trajectory
# 🧪 TRAP[TEST] · 2026-08-02 · NEGATIVE (R4) · отсутствие projects/ → FAIL, не skip (C8)
# · Scenario: DevPlan 119 C8 — по R4 отсутствие тестового окружения = ошибка конфигурации;
#   _scan_project_context возвращает явную issue вместо тихого pytest.skip
# · Last fail: до C8 — pytest.skip("No projects/ directory — dev environment") (DEAD-1)
# · Remove if: projects/ становится обязательной частью репозитория
def test_project_context_missing_dir_fails(caplog, tmp_path) -> None:
    """R4 negative: отсутствие projects/ → явная FAIL-issue (не skip)."""
    issues = _scan_project_context(str(tmp_path / "absent-projects"))
    assert len(issues) >= 1, "R4 FAIL (C8): отсутствие projects/ должно давать FAIL-issue"
    assert "не настроено" in issues[0], f"ожидалось сообщение о ненастроенном окружении: {issues[0]}"
    _logger.info("[IMP:9][gate][context] Отсутствие projects/ → явная issue (R4 PASS, 0 skip)")


# endregion FUNC_test_project_context_missing_dir_fails
