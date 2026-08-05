# GREP_SUMMARY: gate practices-generators-deterministic byte-identical double-render hash
# STRUCTURE: ▶ ┌render_project_files × 2 (по языкам/уровням)┐ → ◇ байт-сверка → ◇ lock render × 2 → ⎋ pass|fail
# region MODULE_CONTRACT
## @purpose  Гейт детерминизма генераторов практик (DevPlan 137 W5): двойной рендер
##           GENERATED-файлов — байт-сверка (аналог yaml_deterministic_output). Недетерминизм
##           (timestamp/порядок/рандом) → дрейф practices.lock generator_hash и ложные
##           drift-FAIL'ы у проектов.
## @scope    Read-only гейт (make gate MODE=fast). Покрывает все языки и оба уровня.
## @invariants
##   - render_project_files (все языки × baseline/full) — байт-идентичен при двойном рендере
##   - render_lock (фиксированный generated_at) — байт-идентичен при двойном рендере
##   - compute_generator_hash — детерминирован (sorted files внутри)
## @rationale  Генераторы — единственный источник GENERATED-файлов; детерминизм = нулевой дрейф.
## @changes  2026-08-05 · DevPlan 137 W1 — создан
# endregion MODULE_CONTRACT

import logging

import pytest

from core.internal.practices.escalator import evaluate
from core.internal.practices.generators import (
    compute_generator_hash,
    render_lock,
    render_project_files,
)
from core.internal.practices.manifest import load_manifest
from core.internal.practices.maturity import Maturity

logger = logging.getLogger(__name__)

_LANGUAGES = ["backend", "frontend", "fullstack", "python", "typescript", "sh"]


@pytest.mark.gate
def test_gate_practices_generators_deterministic_double_render() -> None:
    """Двойной рендер GENERATED-файлов — байт-идентичен (все языки × baseline/full)."""
    manifest = load_manifest()
    for language in _LANGUAGES:
        for level in ("baseline", "full"):
            first = render_project_files("demo", language, level, manifest.pins)
            second = render_project_files("demo", language, level, manifest.pins)
            assert first == second, f"недетерминизм рендера: language={language} level={level}"
            assert first.keys() == second.keys()


@pytest.mark.gate
def test_gate_practices_lock_deterministic_render() -> None:
    """Двойной рендер practices.lock (фиксированный generated_at) — байт-идентичен."""
    manifest = load_manifest()
    files = render_project_files("demo", "backend", "auto", manifest.pins)
    maturity = Maturity(age_days=0, code_files=0)
    decision = evaluate(maturity, "auto", None)
    first = render_lock(manifest, "auto", decision, maturity, files, "backend", generated_at="2026-08-05T00:00:00Z")
    second = render_lock(manifest, "auto", decision, maturity, files, "backend", generated_at="2026-08-05T00:00:00Z")
    assert first == second


@pytest.mark.gate
def test_gate_practices_generator_hash_deterministic() -> None:
    """compute_generator_hash детерминирован (порядок ключей не влияет)."""
    files = {"b.yml": "beta\n", "a.toml": "alpha\n"}
    h1 = compute_generator_hash(files, 1, "baseline")
    h2 = compute_generator_hash({"a.toml": "alpha\n", "b.yml": "beta\n"}, 1, "baseline")
    assert h1 == h2
