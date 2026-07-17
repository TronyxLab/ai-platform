# GREP_SUMMARY: test-templates template-validation ai-platform.yaml glob schema parametrize LDD IMP caplog
# STRUCTURE: pathlib(templates/*/ai-platform.yaml) → parametrize → load_schema → load_yaml → validate → ⊕ result → ⎋ PASS/FAIL

# region MODULE_CONTRACT
## @purpose  Параметризованная валидация всех шаблонов проекта против JSON Schema.
##           Автоматическое обнаружение новых шаблонов через glob — без хардкода списков.
## @scope    tests/test_templates.py — параметризованный pytest тест.
## @invariants
##   - Все шаблоны в templates/*/ai-platform.yaml валидируются автоматически
##   - Список шаблонов не хардкодится — glob находит все существующие
##   - При добавлении нового шаблона тест подхватывает его без изменений
##   - At least one IMP:9 log per §TESTING
## @rationale Q: Почему paramtrize вместо хардкода? A: Устраняет рассинхронизацию
##            при добавлении новых шаблонов — не нужно обновлять список тестов.
## @changes — CREATED: 2026-07-02 | Wave 2 TASK-5 — parameterized template validation
def _module_contract():
    pass


# endregion MODULE_CONTRACT

import json
import logging
import pathlib
import re

import pytest
import yaml
from jsonschema import ValidationError, validate

logger = logging.getLogger(__name__)

# --- [IMP:7] Поиск всех шаблонов через glob — без хардкода
_TEST_DIR = pathlib.Path(__file__).resolve().parent
_PROJECT_ROOT = _TEST_DIR.parent
TEMPLATE_PATHS = sorted(str(p) for p in _PROJECT_ROOT.glob("templates/*/ai-platform.yaml"))
SCHEMA_PATH = str(_PROJECT_ROOT / "core/schemas/ai-platform.schema.json")

# Плейсхолдеры, используемые в шаблонах — заменяются add-project.sh
_PLACEHOLDER_RE = re.compile(r"^\$[A-Z_]+$")


def _replace_placeholders(obj):
    """Рекурсивно заменить placeholder'ы ($VAR) на валидные тестовые значения.

    ## @purpose — Позволяет валидировать структуру шаблонов (required поля,
    ##            типы, доп. свойства) несмотря на placeholder'ы, которые
    ##            заменяются add-project.sh при создании проекта.
    ## @io — ⇥ obj: dict|list|str|any — узел YAML → ⎋ скопированный узел с заменами
    ## @complexity — O(N) где N = количество узлов в YAML
    ## @invariants
    ##   - Строки вида $VAR заменяются на тестовые значения
    ##   - Все остальные значения копируются без изменений
    ##   - Исходный объект не мутируется
    """
    if isinstance(obj, dict):
        return {k: _replace_placeholders(v) for k, v in obj.items()}
    if isinstance(obj, list):
        return [_replace_placeholders(v) for v in obj]
    if isinstance(obj, str) and _PLACEHOLDER_RE.match(obj):
        # Карта замен: специфичные плейсхолдеры → валидные по схеме значения
        _REPLACEMENTS = {
            "$PROJECT_NAME": "testproject",
            "$NODE_NAME": "node1",
            "$DOMAIN": "example.com",
            "$DB_NAME": "testdb",
        }
        return _REPLACEMENTS.get(obj, "testvalue")
    return obj


# ⚠️ TRAP[BUG] · 2026-07-15 · MED · Параметризационные ID зависели от абсолютного пути
# · Symptom: ids=p.split("/")[1] → второй сегмент пути: Users0..2 (macOS), private0..2 (worktree
# ·   в /private/var), home0..2 (CI ubuntu) — inventory-gate ловил фантомные removals между окружениями.
# · Fix: стабильный ID = имя каталога шаблона (template-backend, ...) — не зависит от корня checkout.
@pytest.mark.parametrize("template_path", TEMPLATE_PATHS, ids=lambda p: pathlib.Path(p).parent.name)
def test_template_validates_against_schema(template_path: str) -> None:
    """Валидация YAML манифеста шаблона против JSON Schema.

    ## @purpose — Гарантирует, что все YAML-манифесты шаблонов соответствуют схеме.
    ##           Placeholder'ы ($VAR) заменяются на тестовые значения перед
    ##           валидацией — проверяется структура, а не фактические значения.
    ## @io — ⇥ template_path: str — путь к ai-platform.yaml шаблона → ⎋ None (pytest assert)
    ## @complexity — O(N) — single schema validation per call
    ## @invariants
    ##   - Шаблон должен быть валидным YAML
    ##   - После замены placeholder'ов содержимое должно соответствовать схеме
    ##   - Любое несоответствие (кроме placeholder'ов) вызывает тест-падение
    """
    logger.info("[IMP:7][test_template_validates_against_schema] Validating %s", template_path)

    # --- [IMP:8] Загрузка схемы
    with open(SCHEMA_PATH) as f:
        schema = json.load(f)
    logger.info("[IMP:8] Schema loaded from %s", SCHEMA_PATH)

    # --- [IMP:8] Загрузка шаблона
    with open(template_path) as f:
        manifest = yaml.safe_load(f)

    # --- [IMP:8] Замена placeholder'ов на валидные тестовые значения
    manifest_clean = _replace_placeholders(manifest)
    logger.info("[IMP:8] Placeholders replaced for %s", template_path)

    # --- [IMP:9] Валидация
    try:
        validate(instance=manifest_clean, schema=schema)
        logger.info("[IMP:9] VALIDATION PASSED: %s conforms to schema", template_path)
    except ValidationError as e:
        logger.error("[IMP:9] VALIDATION FAILED: %s - %s", template_path, e.message)
        raise
