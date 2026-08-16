# GREP_SUMMARY: test-generate-catalog catalog.json project-registry ai-platform-yaml scan sort empty-root missing-projects malformed-yaml
# STRUCTURE: fixtures(projects_root factory) → ◇ generate_catalog (full metadata, sort(org,name), count) → ◇ пустой вход (missing root → 0, empty root → []) → ◇ отсутствующие проекты (нет yaml → skip) → ◇ malformed yaml → non-blocking → ◇ parse_cli_args (CLI > env > default) → ⎋ LDD IMP:9
# region MODULE_CONTRACT
## @purpose  Unit tests for catalog/generate_catalog.py (DevPlan 139 W4.4 — закрытие blind spot
##            generate_catalog, 260 LOC, НОВЫЙ). Генерация каталога из project_yaml, пустой вход,
##            обработка отсутствующих/битых проектов, детерминированная сортировка.
## @scope    generate_catalog (полный скан org/project двухуровневой вложенности, поля name/type/node/
##           org/domain/database/metrics_port, sort(org,name), count), пустой вход (missing/empty root),
##           отсутствующие ai-platform.yaml (skip), malformed yaml (non-blocking WARN),
##           parse_cli_args (CLI > env > default).
## @invariants
##   - Обходит $PROJECTS_BASE/*/*/ai-platform.yaml; НЕ блокируется ошибками одного проекта (WARN + continue)
##   - catalog.json — валидный JSON-массив; сортировка по (org, name) детерминирована
##   - Отсутствующий PROJECTS_BASE → 0 (без исключений, файл не создаётся)
##   - Пустой PROJECTS_BASE → 0 + catalog.json = []
##   - tmp_path-изоляция (xdist); 0 subprocess (generate_catalog — чистый Python)
##   - Test Honesty R1-R5: negative-тесты (missing root, empty root, skip-no-yaml, malformed yaml)
##   - LDD: каждый тест — IMP:9-траектория (ldd_trajectory)
## @rationale W4 (139): 260 LOC production без тестов — центральный реестр проектов для AI-агентов
##            и мониторинга (post-deploy + make generate-catalog). Инварианты MODULE_CONTRACT —
##            в исполняемые проверки.
## @changes  2026-08-05 | Created (DevPlan 139 W4.4)
##            2026-08-11 | DevPlan 145 W3 D-I4 — basicConfig перемещён в main()
##                       (module-level side-effect убран); нейтрализация в тесте не нужна
# endregion MODULE_CONTRACT

import json
import logging
from pathlib import Path

import pytest

from core.internal.catalog.generate_catalog import generate_catalog, parse_cli_args
from tests._conftest.ldd import ldd_trajectory

pytestmark = pytest.mark.static_audit

logger = logging.getLogger(__name__)

_VALID_PROJECT_YAML = """
type: backend
target_node: tronyx-vps
needs:
  domain: app.example.com
  database: postgres
monitoring:
  metrics_port: 9090
"""


# region FUNC__write_project
## @purpose  Создать org/project/ai-platform.yaml в projects_root.
## @io       ⇥ projects_root: Path, org: str, project: str, content: str | None → ⎋ Path (проект-дир)
## @complexity O(1)
def _write_project(projects_root: Path, org: str, project: str, content: str | None = _VALID_PROJECT_YAML) -> Path:
    """Create org/project/ai-platform.yaml under projects_root."""
    proj_dir = projects_root / org / project
    proj_dir.mkdir(parents=True, exist_ok=True)
    if content is not None:
        (proj_dir / "ai-platform.yaml").write_text(content)
    return proj_dir


# endregion FUNC__write_project


# region FUNC__read_catalog
## @purpose  Прочитать catalog.json как list[dict].
## @io       ⇥ catalog_file: Path → ⎋ list[dict]
## @complexity O(N)
def _read_catalog(catalog_file: Path) -> list[dict]:
    """Read catalog.json into a Python list."""
    return json.loads(catalog_file.read_text(encoding="utf-8"))


# endregion FUNC__read_catalog


# ═══════════════════════════════════════════════════════════════════════════
# generate_catalog — полный скан
# ═══════════════════════════════════════════════════════════════════════════


# region FUNC_test_generate_catalog_full_metadata
## @purpose  Полный скан: 2 org × проекты с полными метаданными → count, catalog.json с полями
##            name/type/node/org/domain/database/metrics_port, сортировка (org, name).
# 🧪 TRAP[TEST] · generate_catalog_full_metadata · Contract · Regression: каталог не генерируется/поля не извлекаются
# · Scenario: org1/proj-b, org1/proj-a, org2/proj-c → count=3; порядок (org1,proj-a), (org1,proj-b), (org2,proj-c);
# ·   entry имеет name/type/node/org/domain/database/metrics_port
# · Last fail: N/A (новый тест W4.4)
# · Remove if: контракт generate_catalog (поля/сортировка) меняется
@ldd_trajectory
def test_generate_catalog_full_metadata(tmp_path, caplog) -> None:
    """Полный скан: count, поля, детерминированная сортировка (org, name)."""
    projects_root = tmp_path / "projects"
    catalog_file = tmp_path / "catalog.json"
    _write_project(projects_root, "org1", "proj-b")
    _write_project(projects_root, "org1", "proj-a")
    _write_project(projects_root, "org2", "proj-c")

    count = generate_catalog(str(projects_root), str(catalog_file))

    assert count == 3, f"Ожидалось 3 проекта, got {count}"
    catalog = _read_catalog(catalog_file)
    assert [e["org"] for e in catalog] == ["org1", "org1", "org2"]
    assert [e["name"] for e in catalog] == ["proj-a", "proj-b", "proj-c"], "Сортировка по (org, name)"

    first = catalog[0]
    assert first["name"] == "proj-a"
    assert first["type"] == "backend"
    assert first["node"] == "tronyx-vps"
    assert first["org"] == "org1"
    assert first["domain"] == "app.example.com"
    assert first["database"] == "postgres"
    assert first["metrics_port"] == 9090
    logger.info("[IMP:9][test] generate_catalog: 3 проекта, сортировка (org,name), поля извлечены ✓")


# endregion FUNC_test_generate_catalog_full_metadata


# region FUNC_test_generate_catalog_defaults_when_fields_absent
## @purpose  Проект без needs/monitoring/target_node → дефолты: name=proj_dir, type=unknown, node="",
##            domain/database/metrics_port=None.
# 🧪 TRAP[TEST] · generate_catalog_defaults_when_fields_absent · Contract · Regression: дефолтные поля ломаются
# · Scenario: yaml только {"type": "frontend"} → name=proj_dir, type=frontend, node="", domain=None,
# ·   database=None, metrics_port=None
# · Last fail: N/A (новый тест W4.4)
# · Remove if: логика дефолтов каталога меняется
@ldd_trajectory
def test_generate_catalog_defaults_when_fields_absent(tmp_path, caplog) -> None:
    """Минимальный yaml → дефолтные поля (None/пустые), name=proj_dir."""
    projects_root = tmp_path / "projects"
    catalog_file = tmp_path / "catalog.json"
    _write_project(projects_root, "org1", "minimal-app", content="type: frontend\n")

    count = generate_catalog(str(projects_root), str(catalog_file))

    assert count == 1
    entry = _read_catalog(catalog_file)[0]
    assert entry["name"] == "minimal-app", "name = proj_dir при отсутствии в yaml"
    assert entry["type"] == "frontend"
    assert not entry["node"]
    assert entry["domain"] is None
    assert entry["database"] is None
    assert entry["metrics_port"] is None
    logger.info("[IMP:9][test] generate_catalog: дефолтные поля (None/пустые) корректны ✓")


# endregion FUNC_test_generate_catalog_defaults_when_fields_absent


# region FUNC_test_generate_catalog_skips_missing_yaml
## @purpose  Проектная директория без ai-platform.yaml → пропускается (не в каталоге).
# 🧪 TRAP[TEST] · generate_catalog_skips_missing_yaml · NEGATIVE · Regression: проект без yaml попадает в каталог
# · Scenario: org1/proj-a (yaml), org1/no-yaml-dir (без yaml) → count=1, только proj-a
# · Last fail: N/A (новый negative-тест W4.4)
# · Remove if: поведение skip-no-yaml меняется
@ldd_trajectory
def test_generate_catalog_skips_missing_yaml(tmp_path, caplog) -> None:
    """Проект без ai-platform.yaml → skip (не в каталоге)."""
    projects_root = tmp_path / "projects"
    catalog_file = tmp_path / "catalog.json"
    _write_project(projects_root, "org1", "proj-a")
    _write_project(projects_root, "org1", "no-yaml-dir", content=None)

    count = generate_catalog(str(projects_root), str(catalog_file))

    assert count == 1, f"Только proj-a, got {count}"
    names = [e["name"] for e in _read_catalog(catalog_file)]
    assert names == ["proj-a"]
    logger.info("[IMP:9][test] generate_catalog: проект без yaml пропущен ✓")


# endregion FUNC_test_generate_catalog_skips_missing_yaml


# region FUNC_test_generate_catalog_malformed_yaml_nonblocking
## @purpose  Malformed yaml одного проекта НЕ блокирует остальные (WARN + continue): битый проект
##            попадает с дефолтами (name=proj_dir, type=unknown), валидный — с полными полями.
# 🧪 TRAP[TEST] · generate_catalog_malformed_yaml_nonblocking · Contract (invariant) · Regression: битый yaml роняет весь каталог
# · Scenario: org1/good (валидный) + org1/broken (yaml: "{unclosed") → count=2; broken → name=broken,
# ·   type=unknown; good → полные поля; catalog валиден
# · Last fail: N/A (новый тест W4.4; load_project_yaml lenient → {} → defaults, не блокирует)
# · Remove if: семантика lenient-парсинга проекта меняется
@ldd_trajectory
def test_generate_catalog_malformed_yaml_nonblocking(tmp_path, caplog) -> None:
    """Битый yaml одного проекта → WARN + continue, остальные проекты в каталоге."""
    projects_root = tmp_path / "projects"
    catalog_file = tmp_path / "catalog.json"
    _write_project(projects_root, "org1", "good")
    _write_project(projects_root, "org1", "broken", content="services:\n  app: [unclosed")

    count = generate_catalog(str(projects_root), str(catalog_file))

    assert count == 2, "Битый проект НЕ блокирует остальные"
    catalog = _read_catalog(catalog_file)
    by_name = {e["name"]: e for e in catalog}
    assert by_name["broken"]["type"] == "unknown", "Битый yaml → дефолт type=unknown"
    assert by_name["good"]["domain"] == "app.example.com", "Валидный проект извлечён полностью"
    logger.info("[IMP:9][test] generate_catalog: malformed yaml → non-blocking (WARN + continue) ✓")


# endregion FUNC_test_generate_catalog_malformed_yaml_nonblocking


# ═══════════════════════════════════════════════════════════════════════════
# generate_catalog — пустой вход
# ═══════════════════════════════════════════════════════════════════════════


# region FUNC_test_generate_catalog_missing_root
## @purpose  PROJECTS_BASE не существует → 0 (без исключений), catalog-файл НЕ создаётся.
# 🧪 TRAP[TEST] · generate_catalog_missing_root · NEGATIVE (R5) · Regression: отсутствующий root роняет каталог
# · Scenario: projects_root не существует → count=0, catalog_file не создан
# · Last fail: N/A (новый negative-тест W4.4)
# · Remove if: поведение missing-root меняется
@ldd_trajectory
def test_generate_catalog_missing_root(tmp_path, caplog) -> None:
    """PROJECTS_BASE отсутствует → 0, файл каталога не создаётся."""
    missing_root = tmp_path / "no-such-root"
    catalog_file = tmp_path / "catalog.json"

    count = generate_catalog(str(missing_root), str(catalog_file))

    assert count == 0, "Missing root → 0"
    assert not catalog_file.exists(), "Каталог не пишется при missing root"
    logger.info("[IMP:9][test] generate_catalog: missing root → 0, файл не создан ✓")


# endregion FUNC_test_generate_catalog_missing_root


# region FUNC_test_generate_catalog_empty_root
## @purpose  Пустой PROJECTS_BASE (существует, без проектов) → 0 + catalog.json = [] (валидный массив).
# 🧪 TRAP[TEST] · generate_catalog_empty_root · NEGATIVE · Regression: пустой root даёт мусорный каталог
# · Scenario: пустой dir → count=0; catalog.json содержит []
# · Last fail: N/A (новый negative-тест W4.4)
# · Remove if: поведение empty-root меняется
@ldd_trajectory
def test_generate_catalog_empty_root(tmp_path, caplog) -> None:
    """Пустой PROJECTS_BASE → 0, catalog.json = [] (валидный JSON-массив)."""
    projects_root = tmp_path / "projects"
    projects_root.mkdir()
    catalog_file = tmp_path / "catalog.json"

    count = generate_catalog(str(projects_root), str(catalog_file))

    assert count == 0
    assert _read_catalog(catalog_file) == [], "Пустой каталог = []"
    logger.info("[IMP:9][test] generate_catalog: empty root → 0 + catalog.json=[] ✓")


# endregion FUNC_test_generate_catalog_empty_root


# region FUNC_test_generate_catalog_skips_non_dir_entries
## @purpose  Некорректные записи в корне (файл вместо org-dir) → пропускаются.
# 🧪 TRAP[TEST] · generate_catalog_skips_non_dir_entries · NEGATIVE · Regression: файлы в корне ломают скан
# · Scenario: в корне файл "README.md" + org1/proj-a → count=1 (файл пропущен)
# · Last fail: N/A (новый negative-тест W4.4)
# · Remove if: скан начинает включать файлы
@ldd_trajectory
def test_generate_catalog_skips_non_dir_entries(tmp_path, caplog) -> None:
    """Файлы в корне (не org-dir) пропускаются сканом."""
    projects_root = tmp_path / "projects"
    catalog_file = tmp_path / "catalog.json"
    (projects_root).mkdir(parents=True)
    (projects_root / "loose-file.txt").write_text("not an org dir")
    _write_project(projects_root, "org1", "proj-a")

    count = generate_catalog(str(projects_root), str(catalog_file))

    assert count == 1, f"Файл-запись не должен считаться org, got {count}"
    logger.info("[IMP:9][test] generate_catalog: non-dir записи пропущены ✓")


# endregion FUNC_test_generate_catalog_skips_non_dir_entries


# ═══════════════════════════════════════════════════════════════════════════
# parse_cli_args — приоритет CLI > env > default
# ═══════════════════════════════════════════════════════════════════════════


# region FUNC_test_parse_cli_args_cli_over_env
## @purpose  CLI-аргументы приоритетнее env (CLI > env).
# 🧪 TRAP[TEST] · parse_cli_args_cli_over_env · Contract · Regression: env перебивает CLI
# · Scenario: env CATALOG_FILE=/env/x, PROJECTS_BASE=/env/p; CLI --catalog-file /cli/y →
# ·   catalog_file=/cli/y, projects_root=/env/p
# · Last fail: N/A (новый тест W4.4)
# · Remove if: приоритет CLI>env меняется
@ldd_trajectory
def test_parse_cli_args_cli_over_env(tmp_path, monkeypatch, caplog) -> None:
    """CLI-аргумент приоритетнее env; незаданный CLI-аргумент берётся из env."""
    monkeypatch.setenv("CATALOG_FILE", str(tmp_path / "env-catalog.json"))
    monkeypatch.setenv("PROJECTS_BASE", str(tmp_path / "env-projects"))

    args = parse_cli_args(["prog", "--catalog-file", str(tmp_path / "cli-catalog.json")])

    assert args.catalog_file == str(tmp_path / "cli-catalog.json"), "CLI > env для catalog-file"
    assert args.projects_root == str(tmp_path / "env-projects"), "env используется при отсутствии CLI"
    logger.info("[IMP:9][test] parse_cli_args: CLI > env (catalog-file), env fallback (projects-root) ✓")


# endregion FUNC_test_parse_cli_args_cli_over_env
