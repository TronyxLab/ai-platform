# GREP_SUMMARY: gate templates-practices template-backend frontend fullstack practices-files GENERATED-header quality-section
# STRUCTURE: ▶ ┌3 шаблона┐ → ◇ (a) .pre-commit-config.yaml + practices.lock во всех → ◇ (b) pyproject в backend/fullstack (не frontend) → ◇ (c) GENERATED-шапка во всех практиках-файлах → ◇ (d) ai-platform.yaml quality.level=auto → ⎋ pass|fail
# region MODULE_CONTRACT
## @purpose  Гейт наличия практик-файлов в шаблонах (DevPlan 137 W5): все 3 шаблона содержат
##           GENERATED-заглушки практик (.pre-commit-config.yaml, practices.lock; pyproject.toml
##           — только python-семейство) с GENERATED-шапкой, и ai-platform.yaml несёт
##           quality-секцию (level=auto — решение 2026-08-05). Защищает от дрейфа копий
##           шаблонов (copy-paste debt, DevPlan 137 §7).
## @scope    Read-only гейт (make gate MODE=fast).
## @invariants
##   - Каждый шаблон: .pre-commit-config.yaml + practices.lock с GENERATED-шапкой
##   - pyproject.toml — только backend/fullstack (frontend без pyproject)
##   - ai-platform.yaml#quality.level == auto (default, эскалатор жив)
##   - pre-commit-заглушка — ТОЛЬКО upstream (0 путей core/ — аудит 137)
## @rationale  Шаблоны — payload new-project; отсутствие практик = новый проект без защиты.
## @changes  2026-08-05 · DevPlan 137 W1 — создан
# endregion MODULE_CONTRACT

import logging

import pytest

from core.internal.practices.generators import GENERATED_HEADER
from tests.helpers.gate_helpers import repo_root

logger = logging.getLogger(__name__)

ROOT = repo_root()
_TEMPLATES = {
    "backend": ROOT / "templates" / "template-backend",
    "frontend": ROOT / "templates" / "template-frontend",
    "fullstack": ROOT / "templates" / "template-fullstack",
}


@pytest.mark.gate
def test_gate_templates_contain_practices_files() -> None:
    """Каждый шаблон содержит .pre-commit-config.yaml + practices.lock; pyproject — python-семейство."""
    for name, tdir in _TEMPLATES.items():
        assert (tdir / ".pre-commit-config.yaml").is_file(), f"template-{name}: нет .pre-commit-config.yaml"
        assert (tdir / "practices.lock").is_file(), f"template-{name}: нет practices.lock"
    assert (_TEMPLATES["backend"] / "pyproject.toml").is_file()
    assert (_TEMPLATES["fullstack"] / "pyproject.toml").is_file()
    assert not (_TEMPLATES["frontend"] / "pyproject.toml").exists(), "frontend не должен иметь pyproject"


@pytest.mark.gate
def test_gate_templates_practices_have_generated_header() -> None:
    """Практики-файлы шаблонов начинаются с GENERATED-шапки (маркер дрейф-детекта)."""
    for name, tdir in _TEMPLATES.items():
        for rel in (".pre-commit-config.yaml", "practices.lock"):
            content = (tdir / rel).read_text(encoding="utf-8")
            assert content.startswith(GENERATED_HEADER), f"template-{name}/{rel}: нет GENERATED-шапки"


@pytest.mark.gate
def test_gate_templates_precommit_upstream_only() -> None:
    """Pre-commit-заглушки шаблонов — только upstream-репозитории (0 платформенных скриптов, аудит 137)."""
    for name, tdir in _TEMPLATES.items():
        content = (tdir / ".pre-commit-config.yaml").read_text(encoding="utf-8")
        assert "core/entrypoints" not in content, f"template-{name}: платформенный entrypoint в pre-commit (аудит 137)"
        assert "hooks/hygiene.sh" not in content, f"template-{name}: hygiene.sh в pre-commit (аудит 137)"
        assert "hooks/commit_msg.sh" not in content, f"template-{name}: commit_msg.sh в pre-commit (аудит 137)"
        assert "https://github.com/pre-commit/pre-commit-hooks" in content
        assert "project-push-check" in content  # pre-push K5 хук


@pytest.mark.gate
def test_gate_templates_ai_platform_yaml_quality() -> None:
    """ai-platform.yaml шаблонов несёт quality.level=auto (эскалатор жив, решение 2026-08-05).
    Текстовый матч quality-блока (YAML-парсинг невозможен: {{PROJECT_NAME}} — flow-синтаксис)."""
    import re

    for name, tdir in _TEMPLATES.items():
        content = (tdir / "ai-platform.yaml").read_text(encoding="utf-8")
        assert re.search(r"^quality:\s*\n\s+level:\s*auto\b", content, re.MULTILINE), (
            f"template-{name}: quality.level != auto"
        )
