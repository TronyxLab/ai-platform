# GREP_SUMMARY: test-payload-whitelist-triple-sync, REF-0105, triple-sync, whitelist, PROJECT_PAYLOAD_FILENAMES, deploy-project.yml, FILES, concurrency-group, structural
# STRUCTURE: ▶ constant ┌compose-part ≡ PROJECT_COMPOSE_FILENAMES┐ → ◇ payload_deliverer (WHITELIST/_PAYLOAD) ≡ constant → ◇ CI workflow FILES-lines ≡ constant → ◇ templates concurrency present → ⎋ drift = FAIL
# region MODULE_CONTRACT
## @purpose  Structural triple-sync тест whitelist'а payload'а (REF-0105 карточка): ЕДИНАЯ
##           константа shared/compose_files.PROJECT_PAYLOAD_FILENAMES ↔ Python-потребитель
##           (payload_deliverer.WHITELIST_FILES/_PAYLOAD_FILE_NAMES) ↔ CI-сторона
##           (deploy-project.yml FILES + templates deploy.yml). Дрейф любой из трёх сторон = RED.
## @scope    unit/static; читает тексты workflow из репозитория (repo_root helper).
## @invariants
##   - R1/R2: содержательные assert'ы на состав и парность множеств (не константные)
## @rationale  $TEST_SPEC REF-0105: whitelist triple-sync structural test — повтор класса B20a
##            (3 расходящиеся копии списка файлов payload'а).
## @changes  2026-08-24 · Created (REF-0105, meta-refactoring В1)
# endregion MODULE_CONTRACT

import logging
import re
from pathlib import Path

import pytest

from core.internal.deploy.payload_deliverer import _PAYLOAD_FILE_NAMES, WHITELIST_FILES
from core.internal.shared.compose_files import (
    PROJECT_COMPOSE_FILENAMES,
    PROJECT_PAYLOAD_FILENAMES,
)
from tests.helpers.gate_helpers import repo_root

pytestmark = pytest.mark.static_audit

logger = logging.getLogger(__name__)

_PLATFORM_WORKFLOW = Path(".github/workflows/deploy-project.yml")
_TEMPLATE_WORKFLOWS = (
    Path("templates/template-backend/.github/workflows/deploy.yml"),
    Path("templates/template-frontend/.github/workflows/deploy.yml"),
)


def _read_repo_file(rel: Path) -> str:
    return (repo_root() / rel).read_text(encoding="utf-8")


def test_compose_part_of_payload_constant_matches_canon() -> None:
    """Compose-префикс единой константы ≡ канонический PROJECT_COMPOSE_FILENAMES."""
    prefix = PROJECT_PAYLOAD_FILENAMES[: len(PROJECT_COMPOSE_FILENAMES)]
    assert prefix == PROJECT_COMPOSE_FILENAMES, "compose-часть payload-константы обязана быть каноном"
    assert set(PROJECT_PAYLOAD_FILENAMES).isdisjoint({"docker-compose.base.yml"}), (
        "модульный docker-compose.base.yml не входит в проектные payload'ы"
    )
    logger.critical("[IMP:9][test] compose-part of PROJECT_PAYLOAD_FILENAMES == canon")


def test_python_consumers_derive_from_single_constant() -> None:
    """payload_deliverer: WHITELIST_FILES/_PAYLOAD_FILE_NAMES — проекции единой константы."""
    assert list(_PAYLOAD_FILE_NAMES) == list(PROJECT_PAYLOAD_FILENAMES), (
        "порядок assemble_payload обязан совпадать с каноном (B20a-класс дрейфа)"
    )
    assert frozenset(PROJECT_PAYLOAD_FILENAMES) == WHITELIST_FILES, "whitelist receive-канала ≡ константа"
    logger.critical("[IMP:9][test] python consumers derive from single constant")


def test_ci_workflow_files_match_constant() -> None:
    """CI-сторона (deploy-project.yml): FILES собирает ровно имена единой константы."""
    text = _read_repo_file(_PLATFORM_WORKFLOW)
    # ai-platform.yaml — безусловная голова FILES; остальные — условные хвосты
    assert 'FILES="ai-platform.yaml"' in text, "безусловный ai-platform.yaml в FILES"
    conditional = dict(re.findall(r"\[ -f (\S+) \] && FILES=\"\$FILES (\S+)\"", text))
    expected_tail = [n for n in PROJECT_PAYLOAD_FILENAMES if n != "ai-platform.yaml"]
    assert sorted(conditional.keys()) == sorted(expected_tail), (
        f"FILES-хвосты ≠ канону: workflow={sorted(conditional)}, canon={sorted(expected_tail)}"
    )
    for cond, appended in conditional.items():
        assert cond == appended, f"условие и добавление обязаны совпадать: {cond} vs {appended}"
    logger.critical("[IMP:9][test] CI workflow FILES list == single constant (triple-sync leg 3)")


@pytest.mark.parametrize("rel", [str(p) for p in _TEMPLATE_WORKFLOWS])
def test_template_workflows_have_serializing_concurrency(rel: str) -> None:
    """REF-0011/REF-0105: шаблонные deploy.yml сериализуют деплой (concurrency, no-cancel)."""
    text = _read_repo_file(Path(rel))
    assert re.search(r"^concurrency:", text, re.MULTILINE), f"{rel}: нет concurrency-блока"
    assert "cancel-in-progress: false" in text, f"{rel}: cancel-in-progress обязателен false"
    assert re.search(r"group: deploy-\$\{\{", text, re.MULTILINE), f"{rel}: group по канону deploy-*"
    logger.critical("[IMP:9][test] template workflow serializes deploys: %s", rel)
