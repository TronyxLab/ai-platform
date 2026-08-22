# GREP_SUMMARY: practices-profile, project_profile, resolve-language, ai-platform-yaml, name, type, language, level, dedup
# STRUCTURE: ▶ project_profile(project_dir) → ⊕ load_project_yaml (1 чтение) → ◇ type → LANGUAGE_FOR_TYPE (unknown → ()) → ⊕ languages/language/name/level → ⎋ ProjectProfile
# region MODULE_CONTRACT
## @purpose  Единая резолюция свойств проекта из ai-platform.yaml (T2.12): name/type/languages/
##           language/level. Дедупликация 3 копий: check_project/runner.py:138 (resolve_language +
##           inline quality.level), sync_practices.py:83 (name/type/language/level),
##           check_project/drift.py:83 (name/language/type для canon-hash). Одно чтение
##           project_yaml вместо N независимых чтений у потребителей.
## @scope    Потребители: check_project/runner.py (languages + level), sync_practices.py
##           (name/language/level/ptype), check_project/drift.py (name/language/ptype),
##           check_project/checks/file.py (resolve_language — переходные-traces/agent-check).
## @invariants
##   - languages: LANGUAGE_FOR_TYPE.get(type); неизвестный/отсутствующий type → () —
##     runner-семантика «не угадываем язык» (только all-проверки, безопасный fallback)
##   - language: languages[0] if languages else "python" — рендер-семантика sync/drift
##     (render_project_files требует конкретный язык; python — дефолт шаблонов платформы)
##   - level: ai-platform.yaml quality.level; отсутствует/не-dict/пусто → "auto"
##   - name: get_name(data) or project_dir.name (B1-семантика sync/drift)
##   - Читает ТОЛЬКО статические свойства ai-platform.yaml — не lock/maturity/state
## @rationale Три потребителя резолвили одни и те же свойства с разными fallback-семантиками:
##            runner — кортеж языков (() для unknown); sync/drift — одиночный язык (python для
##            unknown). Расхождение НЕ баг, а контекст: выбор проверок (не угадывать язык) vs
##            рендер GENERATED-файлов (нужен конкретный язык). Единый профиль сохраняет ОБЕ
##            семантики (поля languages + language) — механика резолва в одном месте, поведение
##            каждого потребителя не меняется (отчёт T2.12).
## @changes 2026-08-22 | T2.12 — создан (выделен из 3 потребителей: runner/sync_practices/drift)
# endregion MODULE_CONTRACT

from __future__ import annotations

import logging
from dataclasses import dataclass
from pathlib import Path

from core.internal.practices.manifest import LANGUAGE_FOR_TYPE
from core.internal.shared.project_yaml import get_name, get_project_type, load_project_yaml

logger = logging.getLogger(__name__)


# region DATACLASS_ProjectProfile
## @purpose  Frozen-снимок статических свойств проекта из ai-platform.yaml (T2.12).
##           Поля languages/language несут РАЗНЫЕ семантики fallback (см. MODULE_CONTRACT
##           @rationale) — обе нужны потребителям.
## @io       ⇥ name/ptype/languages/language/level → ⎋ ProjectProfile
## @complexity O(1)
@dataclass(frozen=True)
class ProjectProfile:
    """Статические свойства проекта: name/type/languages/language/level (T2.12)."""

    name: str
    """Имя проекта (get_name or project_dir.name)."""

    ptype: str
    """type из ai-platform.yaml ("" если отсутствует)."""

    languages: tuple[str, ...]
    """Кортеж языков канона; () для неизвестного type (runner-семантика: только all-проверки)."""

    language: str
    """Одиночный язык для рендера; languages[0] else "python" (sync/drift-семантика)."""

    level: str
    """quality.level (baseline|full|auto); default "auto"."""


# endregion DATACLASS_ProjectProfile


# region FUNC_project_profile
## @purpose  Разрешить статические свойства проекта из ai-platform.yaml ОДНИМ чтением (T2.12).
## @io       ⇥ project_dir: Path → ⎋ ProjectProfile
## @complexity O(Y) — Y = размер ai-platform.yaml (1 чтение + парсинг)
## @invariants
##   - level: quality не-dict → {} → "auto"; пустое level → "auto"
##   - name: get_name(data) or project_dir.name (B1 fallback sync/drift)
##   - languages/language: неизвестный type → () / "python" (документированное расхождение)
def project_profile(project_dir: Path) -> ProjectProfile:
    """Resolve project properties (name/type/languages/language/level) from ai-platform.yaml."""
    project_dir = Path(project_dir)
    data = load_project_yaml(project_dir)
    ptype = get_project_type(data)
    languages = LANGUAGE_FOR_TYPE.get(ptype)
    languages_tuple = languages if languages is not None else ()
    # W11-G4 cross-file (shared/project_yaml → dict[str, object] после типизации G1):
    # .get возвращает object — isinstance-гейт сохраняет прежнюю семантику `or {}`
    quality_data = data.get("quality")
    quality: dict[str, object] = quality_data if isinstance(quality_data, dict) else {}
    level = str(quality.get("level", "auto") or "auto")
    profile = ProjectProfile(
        name=get_name(data) or project_dir.name,
        ptype=ptype,
        languages=languages_tuple,
        language=languages_tuple[0] if languages_tuple else "python",
        level=level,
    )
    logger.info(
        "[IMP:8][practices_profile][resolve] type=%r languages=%s language=%s level=%s name=%s",
        ptype,
        profile.languages,
        profile.language,
        profile.level,
        profile.name,
    )
    return profile


# endregion FUNC_project_profile


# region FUNC_resolve_language
## @purpose  Языки проекта из ai-platform.yaml type (перенесено из check_project/runner.py T2.12).
##           Неизвестный/отсутствующий type → () — только all-проверки (безопасный fallback,
##           «не угадываем язык»). re-export: runner.py/checks/file.py импортируют сюда.
## @io       ⇥ project_dir: Path → ⎋ tuple[str, ...] языков канона
## @complexity O(Y) — делегирует в project_profile
def resolve_language(project_dir: Path) -> tuple[str, ...]:
    """Resolve canon languages from ai-platform.yaml type (backend → python, frontend → ts).

    ## @purpose  type из ai-platform.yaml (backend|frontend|python|typescript|react|sh)
    ##           → кортеж языков канона (§3.2). Неизвестный/отсутствующий type → пустой кортеж
    ##           (только all-проверки — безопасный fallback, не угадываем язык).
    ## @io       ⇥ project_dir: Path → ⎋ tuple[str, ...]
    ## @complexity O(Y) — делегирует в project_profile (T2.12)
    """
    return project_profile(project_dir).languages


# endregion FUNC_resolve_language
