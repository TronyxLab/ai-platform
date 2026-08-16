# GREP_SUMMARY: test-gate-imports AST-сканер pyproject runtime-deps third-party-imports allowlist gate
# STRUCTURE: ▶ collect_third_party_imports(core/**/*.py AST) → ◇ parse pyproject [project].dependencies → ⊕ сверка third-party ⊆ runtime-deps ∪ allowlist → ⎋ PASS/FAIL
# region MODULE_CONTRACT
## @purpose  CI gate: третьи-сторонние импорты в core/**/*.py обязаны быть объявлены в runtime-dependencies pyproject.toml (U-50, DevPlan 116 B7 T6)
## @scope    core/**/*.py — production-код платформы (тесты вне скоупа: они в tests/, dev-extra)
## @invariants
##   - AST-сканер собирает `import X` / `from X import ...` где X — НЕ relative (не начинается с .)
##   - stdlib модули не сканируются (sys.stdlib_module_names)
##   - core-internal bare-imports (без core. префикса, напр. `from template_engine import ...`)
##     классифицируются как internal (модуль существует под core/) — не third-party
##   - Сверка: third_party ⊆ {имена из [project].dependencies} ∪ allowlist
##   - allowlist — ТОЛЬКО с обоснованием (optional guarded deps и т.п.)
##   - Negative-тест: подстановка фиктивного импорта → RED (R5 anti-survivorship)
## @rationale U-50: httpx импортировался (admin_client.py) без декларации в pyproject. Гейт
##   предотвращает дрейф между runtime-импортами и декларацией зависимостей. Trinity:
##   файл в tests/gates/ + @pytest.mark.gate + entrypoint-manifest gates (авто-дискавери).
## @changes 2026-08-01 | Created (DevPlan 116 B7 T6)
# endregion MODULE_CONTRACT

import ast
import pathlib
import sys

import pytest
import tomllib

from tests.helpers.gate_helpers import repo_root

# ── Allowlist: third-party импорты в core/, НЕ объявленные в runtime deps — с обоснованием ──
# Каждое имя обязано иметь причину. Расширение — только через DevPlan/Architect.
_IMPORT_ALLOWLIST: dict[str, str] = {
    # ruamel.yaml — ОПЦИОНАЛЬНАЯ guarded-зависимость node_yaml._write_back() (коммент-презервинг).
    # Импорт в try/except ImportError с fallback на PyYAML — НЕ требуется в runtime, поэтому
    # не в [project].dependencies. (DevPlan 116 B7 T6 — allowlist-паттерн B2/B4/B5)
    "ruamel": "optional guarded dep — node_yaml._write_back() comment-preservation with PyYAML fallback",
    # providers — внутренний plugin-пакет hermes-agent (build/plugins/model-providers/),
    # sibling-пакет внутри контейнерного runtime, НЕ PyPI-зависимость. Сканируется только
    # потому, что живёт под core/modules/hermes-agent/build/ (L1/L2 build-context).
    "providers": "hermes-agent plugin sibling package (build/plugins/model-providers/) — container-internal, not PyPI",
    # locust — LOAD extra (DevPlan 146, pyproject [project.optional-dependencies] load):
    # импортируется ТОЛЬКО core/loadtest/scenarios/*.py (запускаются locust-рантаймом),
    # платформенный код (core/internal/loadtest/) locust не импортирует (preflight find_spec).
    "locust": "load extra (DevPlan 146) — locust scenario files only (core/loadtest/scenarios/), never imported by platform",
    # gevent — runtime-зависимость locust: llm_stream.py использует gevent.Timeout
    # для chunk-timeout SSE (DevPlan 146 W1); импорт внутри locust-рантайма.
    "gevent": "locust runtime dep — llm_stream.py chunk-timeout (DevPlan 146), import inside locust runtime",
}

# PyPI-имя → импорт-имя (пакет может экспортировать модуль с другим именем)
_PYPI_IMPORT_ALIASES: dict[str, set[str]] = {
    "pyyaml": {"yaml"},
    "python-dotenv": {"dotenv"},
}


def collect_third_party_imports(core_dir: pathlib.Path) -> dict[str, set[str]]:
    """Собрать все third-party импорты из core/**/*.py через AST.

    ## @purpose  AST-сканер: import X / from X import — не relative, не stdlib, не core-internal.
    ## @io       ⇥ core_dir: path → ⎋ {top_level_module: {file_paths}}
    ## @complexity O(F*N) — F файлов, N нод AST на файл
    ## @invariants
    ##   - Relative-импорты (from .x / from ..x) пропускаются
    ##   - stdlib исключается через sys.stdlib_module_names
    ##   - core-internal bare-имена исключаются: топ-имя == stem .py модуля под core/
    """
    third_party: dict[str, set[str]] = {}

    # Имена core-internal модулей (для bare-imports без core. префикса)
    internal_names: set[str] = set()
    for p in core_dir.rglob("*.py"):
        if "__pycache__" in p.parts:
            continue
        internal_names.add(p.stem)
        internal_names.add(p.parent.name)

    for py_file in core_dir.rglob("*.py"):
        if "__pycache__" in py_file.parts:
            continue
        try:
            tree = ast.parse(py_file.read_text(encoding="utf-8"))
        except SyntaxError:
            continue
        for node in ast.walk(tree):
            top: str | None = None
            if isinstance(node, ast.Import):
                for alias in node.names:
                    top = alias.name.split(".")[0]
                    _register(top, py_file, internal_names, third_party)
            elif isinstance(node, ast.ImportFrom) and node.module:
                if node.level > 0:
                    continue  # relative import
                top = node.module.split(".")[0]
                _register(top, py_file, internal_names, third_party)
    return third_party


def _register(top: str, py_file: pathlib.Path, internal_names: set[str], acc: dict[str, set[str]]) -> None:
    """Зарегистрировать импорт, если он third-party (не stdlib, не internal)."""
    if top in sys.stdlib_module_names:
        return
    if top == "core":
        return
    if top in internal_names:
        return  # core-internal bare import (напр. template_engine, age_key)
    acc.setdefault(top, set()).add(str(py_file.relative_to(py_file.parent.parent.parent)))


def parse_runtime_deps(pyproject_path: pathlib.Path) -> set[str]:
    """Извлечь имена runtime-зависимостей из pyproject.toml ([project].dependencies).

    ## @io  ⇥ pyproject_path → ⎋ set[str] топ-уровневых имён пакетов
    ## @complexity O(D) — D зависимостей
    """
    with pathlib.Path(pyproject_path).open("rb") as f:
        data = tomllib.load(f)
    deps: set[str] = set()
    for spec in data["project"].get("dependencies", []):
        name = spec.split(">=")[0].split("==")[0].split("~=")[0].strip()
        deps.add(name.replace("_", "-").lower())
    return deps


# 🧪 TRAP[TEST] · gate/imports · Regression: необъявленный third-party импорт в core/
# · Scenario: AST-скан core/**/*.py → сверка с pyproject runtime deps + allowlist
# · Last fail: N/A (новый гейт, DevPlan 116 B7 T6)
# · Remove if: dependency management переезжает с pyproject.toml на другой механизм
@pytest.mark.gate
def test_core_imports_covered_by_pyproject(caplog) -> None:
    """Все third-party импорты core/**/*.py объявлены в runtime deps или allowlist."""
    project_root = repo_root()
    core_dir = project_root / "core"
    pyproject_path = project_root / "pyproject.toml"

    third_party = collect_third_party_imports(core_dir)
    runtime_deps = parse_runtime_deps(pyproject_path)

    caplog.set_level(10)
    print("--- LDD TRAJECTORY (IMP:7-10) ---")
    for record in caplog.records:
        if "[IMP:" in record.message:
            imp_level = int(record.message.split("[IMP:")[1].split("]")[0])
            if imp_level >= 7:
                print(record.message)
    print("--- END LDD TRAJECTORY ---")

    undeclared: dict[str, str] = {}
    # Инвертируем алиасы: import-имя → pyproject-имя (напр. yaml → pyyaml)
    alias_to_pypi: dict[str, str] = {imp: pypi for pypi, imps in _PYPI_IMPORT_ALIASES.items() for imp in imps}
    for mod in sorted(third_party):
        pypi_name = alias_to_pypi.get(mod, mod)
        if pypi_name in runtime_deps:
            continue
        if mod in _IMPORT_ALLOWLIST:
            continue
        undeclared[mod] = ", ".join(sorted(third_party[mod]))

    print("[IMP:8][test_core_imports_covered_by_pyproject] runtime deps: " + str(sorted(runtime_deps)))
    print(f"[IMP:8][test_core_imports_covered_by_pyproject] third-party found: {sorted(third_party)}")
    assert not undeclared, (
        "Third-party imports in core/ not declared in pyproject runtime deps or allowlist:\n"
        + "\n".join(f"  {mod} (files: {files})" for mod, files in undeclared.items())
    )
    # IMP:9 assert
    assert len(third_party) >= 1, "Scanner should find at least one third-party import in core/"
    print(
        "[IMP:9][test_core_imports_covered_by_pyproject] ALL core/ imports covered by pyproject runtime deps + allowlist"
    )


# 🧪 TRAP[TEST] · gate/imports-negative · Regression: гейт пропускает необъявленный импорт
# · Scenario: подстановка фиктивного импорта в AST-скан → RED (R5 anti-survivorship)
# · Last fail: N/A (новый гейт)
# · Remove if: гейт переезжает на другой механизм детекции
@pytest.mark.gate
def test_gate_imports_negative_fictitious_import(tmp_path, monkeypatch, caplog) -> None:
    """Negative-тест: фиктивный необъявленный импорт должен быть пойман сканером (RED)."""
    caplog.set_level(10)
    # Подменяем core_dir на tmp_path с файлом, импортирующим необъявленный пакет
    fake_core = tmp_path / "core"
    fake_core.mkdir()
    (fake_core / "fake_module.py").write_text("import some_undeclared_fake_pkg_xyz\n")

    third_party = collect_third_party_imports(fake_core)

    print(f"[IMP:8][negative] fake third-party imports detected: {sorted(third_party)}")
    assert "some_undeclared_fake_pkg_xyz" in third_party, (
        "AST scanner must detect fictitious undeclared import (R5 anti-survivorship)"
    )
    print("[IMP:9][negative] Scanner catches undeclared import — gate is falsifiable")
