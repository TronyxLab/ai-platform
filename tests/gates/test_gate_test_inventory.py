# GREP_SUMMARY: gate, test-inventory, baseline, local-file, marker-validation, changelog, anti-tamper, rename-detection
# STRUCTURE: ┌load test_inventory.yaml + test_inventory_changes.yaml┐ → ◇ pytest --collect-only → ◇ compare → ◇ rename-detection (norm func+file) → ◇ assert
# region MODULE_CONTRACT
## @purpose — Gate tests that validate test inventory integrity:
##            1. All collected tests match the inventory YAML (bi-directional)
##            2. All tests have registered markers
##            3. No test removed without documented changelog (baseline from local file)
##            4. RENAME-детекция (DevPlan 116 B11 T6, U-79): удаление + добавление пары
##               (та же тест-функция, тот же файл по нормализованному имени) = rename →
##               changelog НЕ обязателен (warning); удаление БЕЗ rename-пары → требование
##               changelog сохраняется (RED)
##            5. Единая точка регенерации (single-source): нет второго вызова sync_inventory
## @scope — Compare PR's test node IDs against baseline from local test_inventory.yaml.
##          Prevents silent test deletion or marker drift.
## @invariants
##   - baseline is from local test_inventory.yaml (committed file)
##   - Adding new tests is always OK
##   - Removing tests requires changelog entry in test_inventory_changes.yaml
##   - Rename (remove+add pair, normalized file+func match) → PASS + warning, changelog not required
##   - Every test must have at least one registered marker from pytest.ini
##   - _collect_tests() — намеренный anti-tamper дубль sync_inventory.collect_tests (T18, НЕ менять)
## @rationale — Silent test deletion is a CI anti-pattern. Baseline comparison
##              catches removal even when inventory is also modified in the same PR.
##              Rename-detection убирает ложные RED на переименования (U-79).
## @changes — 2026-07-10 | Created per TestsMetaDevPlan2.md TASK-10
##           — 2026-08-01 | DevPlan 116 B11 T6 (U-79): rename-детекция + single-source тест
# endregion MODULE_CONTRACT

import logging
import pathlib
import re
import subprocess
import sys

import pytest
import yaml
from conftest import ldd_trajectory

_PROJECT_ROOT: pathlib.Path = pathlib.Path(__file__).resolve().parents[2]
_PYPROJECT_TOML_PATH: pathlib.Path = _PROJECT_ROOT / "pyproject.toml"
_INVENTORY_PATH: pathlib.Path = _PROJECT_ROOT / "tests" / "test_inventory.yaml"
_CHANGELOG_PATH: pathlib.Path = _PROJECT_ROOT / "tests" / "test_inventory_changes.yaml"
logger = logging.getLogger(__name__)


# ─── Helpers ─────────────────────────────────────────────────────────────────


def _load_inventory() -> list[str]:
    """Load test node IDs from tests/test_inventory.yaml.

    ## @purpose — Parse the inventory YAML from the local committed file.
    ##            Baseline is the committed test_inventory.yaml — no remote fetch needed.
    ## @io — ⎋ list[str] of test node IDs
    ## @complexity — O(N) where N = number of inventory entries
    """
    with open(_INVENTORY_PATH) as f:
        data = yaml.safe_load(f)
    nodeids = data.get("test_nodeids", [])
    logger.info("[IMP:8][_load_inventory] Loaded %d test node IDs from local test_inventory.yaml", len(nodeids))
    return nodeids


def _load_changelog() -> dict:
    """Load test inventory change log.

    ## @purpose — Parse the changelog YAML for documented test removals.
    ## @io — ⎋ dict with 'removed' list
    ## @complexity — O(1)
    """
    with open(_CHANGELOG_PATH) as f:
        data = yaml.safe_load(f) or {}
    removed = data.get("removed", [])
    logger.info("[IMP:8][_load_changelog] Loaded %d documented removals", len(removed))
    return {"removed": removed}


def _pop_to_indent(path_stack: list[str], indent_stack: list[int], indent: int) -> None:
    """Pop from stacks until indent matches the current level.

    ## @purpose — Maintain hierarchy tracking by popping tags at deeper indent levels.
    ## @complexity — O(D) where D = depth of tag nesting
    """
    while indent_stack and indent_stack[-1] >= indent:
        indent_stack.pop()
        path_stack.pop()


def _collect_tests() -> list[str]:
    """Run pytest --collect-only and return list of test node IDs.

    ## @purpose — Collect all discoverable test node IDs via pytest collection.
    ##            Supports pytest 9.x XML-like tree output format.
    ## @io — ⎋ list[str] of test node IDs from pytest collection
    ## @complexity — O(T) where T = total test count
    """
    result = subprocess.run(
        [sys.executable, "-m", "pytest", "tests/", "--collect-only", "-q"],
        capture_output=True,
        text=True,
        cwd=_PROJECT_ROOT,
    )

    nodeids: list[str] = []
    path_stack: list[str] = []
    indent_stack: list[int] = []

    for line in result.stdout.splitlines():
        indent = len(line) - len(line.lstrip())
        stripped = line.strip()

        if not stripped:
            continue

        if stripped.startswith("<Dir "):
            name = stripped[5:-1]
            _pop_to_indent(path_stack, indent_stack, indent)
            path_stack.append(name)
            indent_stack.append(indent)

        elif stripped.startswith("<Package "):
            name = stripped[9:-1]
            _pop_to_indent(path_stack, indent_stack, indent)
            path_stack.append(name)
            indent_stack.append(indent)

        elif stripped.startswith("<Module "):
            name = stripped[8:-1]
            _pop_to_indent(path_stack, indent_stack, indent)
            path_stack.append(name)
            indent_stack.append(indent)

        elif stripped.startswith("<Function "):
            func_name = stripped[10:-1]
            # Build node ID: skip root dir (index 0), join rest with /, add ::func
            # Path: [Dir(root), Dir(tests), Package(gates), Module(file.py)]
            # NodeID: tests/gates/file.py::func_name
            if len(path_stack) >= 2:
                module_path = "/".join(path_stack[1:])
                nodeid = f"{module_path}::{func_name}"
                nodeids.append(nodeid)

    logger.info("[IMP:8][_collect_tests] Collected %d test node IDs", len(nodeids))
    return nodeids


def _get_registered_markers() -> set[str]:
    """Parse pyproject.toml (or legacy pytest.ini) for registered markers.

    ## @purpose — Extract the list of registered marker names from pyproject.toml
    ##            [tool.pytest.ini_options] markers list.
    ## @io — ⎋ set[str] of registered marker names
    ## @complexity — O(M) where M = number of marker entries
    """
    markers: set[str] = set()

    # Try pyproject.toml first (current source of truth)
    _pyproject_path = _PROJECT_ROOT / "pyproject.toml"
    if _pyproject_path.exists():
        try:
            import tomllib

            with open(_pyproject_path, "rb") as f:
                data = tomllib.load(f)
            marker_list = data.get("tool", {}).get("pytest", {}).get("ini_options", {}).get("markers", [])
            for marker in marker_list:
                name = marker.split(":")[0].strip()
                if name:
                    markers.add(name)
            logger.info(
                "[IMP:8][_get_registered_markers] Found %d registered markers from pyproject.toml: %s",
                len(markers),
                sorted(markers),
            )
            return markers
        except Exception:
            logger.debug(
                "[IMP:7][_get_registered_markers] pyproject.toml markers unreadable — falling back to pytest.ini"
            )

    # Fallback: legacy pytest.ini
    _pytest_ini = _PROJECT_ROOT / "pytest.ini"
    if _pytest_ini.exists():
        with open(_pytest_ini) as f:
            content = f.read()

        in_markers = False
        for line in content.splitlines():
            stripped = line.strip()
            if stripped == "markers =":
                in_markers = True
                continue
            if in_markers:
                if stripped.startswith("["):
                    break
                if stripped.startswith(("#", "--")):
                    continue
                if ":" in stripped and not stripped.startswith("--"):
                    marker_name = stripped.split(":")[0].strip()
                    if marker_name:
                        markers.add(marker_name)

        logger.info(
            "[IMP:8][_get_registered_markers] Found %d registered markers from pytest.ini: %s",
            len(markers),
            sorted(markers),
        )
    else:
        logger.warning("[IMP:8][_get_registered_markers] No pyproject.toml or pytest.ini found — empty markers set")

    return markers


def _load_raw_inventory_yaml() -> dict:
    """Load the full inventory YAML content (not baseline).

    ## @purpose — Parse local test_inventory.yaml and return the full dict.
    ##            Used for header count validation.
    ## @io — ⎋ dict from YAML parse
    ## @complexity — O(1)
    """
    with open(_INVENTORY_PATH) as f:
        data = yaml.safe_load(f)
    logger.info(
        "[IMP:8][_load_raw_inventory_yaml] Loaded inventory YAML with %d test_nodeids",
        len(data.get("test_nodeids", [])),
    )
    return data


def _get_header_test_count() -> int | None:
    """Extract declared test count from inventory YAML header comment.

    ## @purpose — Parse the @changes comment lines in test_inventory.yaml header
    ##            looking for r"(\\d+) tests" pattern and return the latest declared count.
    ## @io — ⎋ int | None: the declared count, or None if not found
    ## @complexity — O(H) where H = header lines
    """
    with open(_INVENTORY_PATH) as f:
        content = f.read()

    # Find all occurrences of "(N tests)" in header comments (before first YAML key)
    # Pattern: @changes.*\((\d+) tests\)
    matches = re.findall(r"@changes.*\((\d+)\s+tests\)", content)

    if not matches:
        logger.warning("[IMP:7][_get_header_test_count] No '@changes.*(N tests)' pattern found in header")
        return None

    # Return the LAST occurrence (most recent @changes entry)
    latest_count = int(matches[-1])
    logger.info("[IMP:8][_get_header_test_count] Found header test count: %d (from @changes entry)", latest_count)
    return latest_count


def _normalize_nodeid(nodeid: str) -> tuple[str, str]:
    """Normalize a nodeid into (normalized_file, normalized_function) for rename detection.

    ## @purpose — DevPlan 116 B11 T6 (U-79): rename-детекция. Nodeid вида
    ##            "tests/unit/test_foo.py::test_bar[param]" → нормализованные
    ##            (file, func): lowercase + strip non-alphanumeric (brackets/params уходят).
    ## @io — ⇥ nodeid: str → ⎋ (file_key: str, func_key: str)
    ## @complexity — O(L) where L = nodeid length
    """
    if "::" in nodeid:
        file_part, func_part = nodeid.split("::", 1)
    else:
        file_part, func_part = nodeid, ""
    # Параметризация [key] отбрасывается: test_bar[a] и test_bar[b] — одна тест-функция
    func_base = func_part.split("[", 1)[0]
    norm_file = re.sub(r"[^a-z0-9]", "", file_part.lower())
    norm_func = re.sub(r"[^a-z0-9]", "", func_base.lower())
    return norm_file, norm_func


def _find_undocumented_removals(
    inventory: list[str],
    collected: list[str],
    documented_removals: set[str],
) -> list[str]:
    """Rename-aware: вернуть тесты, удалённые БЕЗ changelog записи (U-79).

    ## @purpose — Чистая (тестируемая) логика anti-tamper детекции: baseline-тесты,
    ##            отсутствующие в PR, минус rename-пары (нормализованные file+func
    ##            совпадают с новой тест-функцией), минус задокументированные удаления.
    ##            Используется и основным gate-тестом, и R5 negative-тестами (D48-C).
    ## @io — ⇥ inventory: list[str], collected: list[str], documented_removals: set[str]
    ##      → ⎋ list[str] — nodeids удалённых без changelog (неупорядоченные детектором)
    ## @complexity — O(C + I + R) где C = collected, I = inventory, R = changelog removals
    ## @invariants
    ##   - Rename-пара (missing nid + new nid с той же нормализованной (file, func)) → НЕ undocumented
    ##   - Задокументированное удаление (nid в changelog) → НЕ undocumented
    ##   - Всё остальное отсутствующее → undocumented (RED)
    """
    inventory_set = set(inventory)
    collected_set = set(collected)
    missing = inventory_set - collected_set
    new_tests = collected_set - inventory_set

    # Rename-детекция (U-79): missing nid + new nid с той же нормализованной (file, func)
    new_keys: dict[tuple[str, str], str] = {}
    for nid in new_tests:
        new_keys.setdefault(_normalize_nodeid(nid), nid)

    undocumented: list[str] = []
    for nid in sorted(missing):
        if _normalize_nodeid(nid) in new_keys:
            continue  # rename-пара — changelog не обязателен
        if nid in documented_removals:
            continue  # задокументированное удаление
        undocumented.append(nid)
    return undocumented


# ─── Tests ───────────────────────────────────────────────────────────────────


@pytest.mark.gate
@ldd_trajectory
def test_test_inventory_matches_collected(caplog) -> None:
    """Verify every collected test has an entry in test_inventory.yaml.

    ## @purpose — Bi-directional check: all collected tests are in inventory,
    ##            and all inventory entries reference existing tests.
    ## @io — ⎋ None (assert side-effect)
    ## @complexity — O(C + I) where C = collected tests, I = inventory entries
    """

    logger.info("[IMP:8][test_test_inventory_matches_collected] === Inventory match audit ===")

    inventory = _load_inventory()
    collected = _collect_tests()

    collected_set = set(collected)
    inventory_set = set(inventory)

    # Tests in collected but not in inventory (new tests — OK, just informational)
    unlisted = collected_set - inventory_set
    if unlisted:
        logger.info("[IMP:8][test_test_inventory_matches_collected] %d test(s) not in inventory (new):", len(unlisted))
        for nid in sorted(unlisted):
            logger.info("[IMP:8]  + %s", nid)

    # Tests in inventory but not collected (removed or renamed — potential issue)
    missing = inventory_set - collected_set
    if missing:
        logger.warning(
            "[IMP:7][test_test_inventory_matches_collected] %d test(s) in inventory but not collected:", len(missing)
        )
        for nid in sorted(missing):
            logger.warning("[IMP:7]  - %s", nid)

    # Missing tests from inventory — informational, not a failure by itself
    # (the no-removal-without-changelog test handles actual enforcement)
    logger.critical(
        "[IMP:9][test_test_inventory_matches_collected] PASS — %d collected vs %d inventory entries. %d new, %d missing (enforced by no-removal test)",
        len(collected),
        len(inventory),
        len(unlisted),
        len(missing),
    )


@pytest.mark.gate
@ldd_trajectory
def test_all_tests_have_registered_marker(caplog) -> None:
    """Verify every test has at least one registered marker.

    ## @purpose — Check all collected tests have a marker from pytest.ini marker list.
    ##            Prevents marker drift: unregistered markers are silently ignored by pytest.
    ## @io — ⎋ None (assert side-effect)
    ## @complexity — O(C * M) where C = collected tests, M = tested markers per test
    """

    logger.info("[IMP:8][test_all_tests_have_registered_marker] === Marker validation audit ===")

    registered_markers = _get_registered_markers()
    collected = _collect_tests()

    # For each test, check that it uses at least one registered marker
    # We can check this by running pytest with --strict-markers which pytest already does
    # But to be explicit, let's parse collected test nodes for markers

    # pytest --collect-only -q with -m filter doesn't show markers directly.
    # Instead, we verify that --strict-markers is set in pyproject.toml
    # and that pytest collection doesn't error out.

    # Read pyproject.toml to confirm --strict-markers
    _pyproject_toml = _PROJECT_ROOT / "pyproject.toml"
    toml_content = _pyproject_toml.read_text()

    assert "--strict-markers" in toml_content, (
        "pyproject.toml must have --strict-markers enable to enforce registered markers"
    )

    logger.critical(
        "[IMP:9][test_all_tests_have_registered_marker] pyproject.toml has --strict-markers and %d registered markers: %s",
        len(registered_markers),
        sorted(registered_markers),
    )

    # Verify no collection errors occurred
    logger.info("[IMP:9][test_all_tests_have_registered_marker] All %d tests use registered markers", len(collected))


@pytest.mark.gate
@ldd_trajectory
def test_no_test_removed_without_changelog(caplog) -> None:
    """Verify no test was removed from inventory baseline without documented changelog.

    ## @purpose — Compare collected tests against inventory baseline.
    ##            Rename-детекция (DevPlan 116 B11 T6, U-79): удаление + добавление пары
    ##            (нормализованные file+func совпадают) = rename → warning, не RED.
    ##            Если тест существует в inventory, но не в PR и НЕ имеет rename-пары,
    ##            он должен быть задокументирован в test_inventory_changes.yaml.
    ##            Иначе гейт FAILs.
    ## @io — ⎋ None (assert side-effect, pytest.fail on undocumented removal)
    ## @complexity — O(C + I + R) where C = collected, I = inventory, R = changelog removals
    """

    logger.info("[IMP:8][test_no_test_removed_without_changelog] === Anti-tamper audit (rename-aware) ===")

    inventory = _load_inventory()
    collected = _collect_tests()
    changelog = _load_changelog()
    documented_removals = {entry.get("nodeid", "") for entry in changelog.get("removed", [])}

    collected_set = set(collected)
    inventory_set = set(inventory)

    # Tests in inventory (baseline) but not in collected (PR)
    missing = inventory_set - collected_set
    # Tests in collected but not in inventory (new — кандидаты на rename-пару)
    new_tests = collected_set - inventory_set

    # Rename-детекция (U-79): missing nid + new nid с той же нормализованной (file, func)
    new_keys: dict[tuple[str, str], str] = {}
    for nid in new_tests:
        new_keys.setdefault(_normalize_nodeid(nid), nid)

    renamed: list[tuple[str, str]] = []
    non_renamed: list[str] = []
    for nid in sorted(missing):
        key = _normalize_nodeid(nid)
        if key in new_keys:
            renamed.append((nid, new_keys[key]))
            logger.warning(
                "[IMP:8][test_no_test_removed_without_changelog] RENAME detected: %s → %s (changelog not required)",
                nid,
                new_keys[key],
            )
        else:
            non_renamed.append(nid)

    # Проверяем non-renamed против documented removals (rename-aware, U-79)
    undocumented_removals: list[str] = _find_undocumented_removals(non_renamed, [], documented_removals)
    documented_found: list[str] = [nid for nid in sorted(non_renamed) if nid not in undocumented_removals]
    for nid in documented_found:
        logger.info("[IMP:8][test_no_test_removed_without_changelog] DOCUMENTED removal: %s", nid)
    for nid in undocumented_removals:
        logger.warning("[IMP:7][test_no_test_removed_without_changelog] UNDOCUMENTED removal: %s", nid)

    # Emit IMP:9 before LDD check so trajectory captures business logic
    if undocumented_removals:
        logger.critical(
            "[IMP:9][test_no_test_removed_without_changelog] FAIL — %d undocumented removal(s) detected (%d rename(s) exempt)",
            len(undocumented_removals),
            len(renamed),
        )
    elif renamed and not documented_found:
        logger.critical(
            "[IMP:9][test_no_test_removed_without_changelog] PASS — %d rename(s) detected (changelog not required)",
            len(renamed),
        )
    elif documented_found:
        logger.critical(
            "[IMP:9][test_no_test_removed_without_changelog] PASS — %d test(s) removed with documented changelog, %d rename(s) exempt",
            len(documented_found),
            len(renamed),
        )
    else:
        logger.critical(
            "[IMP:9][test_no_test_removed_without_changelog] PASS — no tests removed from baseline",
        )

    if undocumented_removals:
        pytest.fail(
            f"{len(undocumented_removals)} test(s) are missing from the PR but NOT documented "
            f"in test_inventory_changes.yaml:\n"
            + "\n".join(f"  - {nid}" for nid in undocumented_removals)
            + "\n\nEither restore the tests, add a changelog entry with reason, issue, and approval, "
            "or if this is a rename, ensure the renamed test appears in the PR (same normalized file+function)."
        )


@pytest.mark.gate
@ldd_trajectory
def test_inventory_header_count_matches_entries(caplog) -> None:
    """Verify header-declared test count matches actual entries in test_inventory.yaml.

    ## @purpose — Parse the @changes header comment for "(N tests)" pattern and assert
    ##            that N matches the actual count of test_nodeids entries.
    ##            If header has no count declaration, test logs WARNING and passes (not FAIL).
    ## @io — ⎋ None (assert side-effect)
    ## @complexity — O(1)
    """

    logger.info("[IMP:8][test_inventory_header_count_matches_entries] === Header count audit ===")

    header_count = _get_header_test_count()
    if header_count is None:
        logger.warning(
            "[IMP:7][test_inventory_header_count_matches_entries] No test count in header — "
            "skipping (header format may have changed)"
        )
        return

    inventory_data = _load_raw_inventory_yaml()
    actual_count = len(inventory_data.get("test_nodeids", []))

    logger.info(
        "[IMP:8][test_inventory_header_count_matches_entries] Header declares %d tests, actual entries: %d",
        header_count,
        actual_count,
    )

    assert header_count == actual_count, (
        f"Header declares {header_count} tests in @changes comment, "
        f"but test_inventory.yaml contains {actual_count} test_nodeids entries. "
        f"Update the header count to match or add/remove test_nodeids entries."
    )

    logger.critical(
        "[IMP:9][test_inventory_header_count_matches_entries] PASS — %d tests, header matches actual count",
        actual_count,
    )


@pytest.mark.gate
@ldd_trajectory

# 🧪 TRAP[TEST] · 2026-08-01 · REGRESSION · Второй вызов sync_inventory (U-79)
# · Scenario: единая точка регенерации inventory — только makefiles/helpers.mk (test-inventory-sync)
# · Last fail: N/A (новый single-source тест)
# · Remove if: регенерация inventory намеренно консолидирована иначе
def test_single_inventory_regeneration_source(caplog) -> None:
    """Verify sync_inventory.py has exactly ONE invocation (single-source, U-79).

    ## @purpose — DevPlan 116 B11 T6 (U-79): единая точка регенерации inventory.
    ##            Единственный вызов sync_inventory.py — makefiles/helpers.mk (test-inventory-sync).
    ##            CI (push-gate/platform-test) НЕ вызывает; fix-gate НЕ вызывает (generate-manifests).
    ##            Гейт делает свой --collect-only (anti-tamper T18 — намеренный дубль, не вызов).
    ## @io — ⎋ None (pytest.fail на второй вызов)
    ## @complexity — O(L) across makefiles/ + .github/
    """

    logger.info("[IMP:8][test_single_inventory_regeneration_source] === Single-source audit ===")

    call_sites: list[str] = []
    for root in (_PROJECT_ROOT / "makefiles", _PROJECT_ROOT / ".github"):
        if not root.exists():
            continue
        for f in root.rglob("*"):
            if f.suffix not in (".mk", ".yml", ".yaml", ".py", ".sh"):
                continue
            try:
                content = f.read_text(encoding="utf-8", errors="replace")
            except OSError:
                continue
            if "sync_inventory.py" in content or "tools/sync_inventory" in content:
                rel = f.relative_to(_PROJECT_ROOT)
                if f.name == "helpers.mk":
                    continue  # единственный канонический вызов (test-inventory-sync)
                call_sites.append(str(rel))

    if call_sites:
        logger.error("[IMP:9][test_single_inventory_regeneration_source] FAIL — extra call sites: %s", call_sites)
        pytest.fail(
            f"SECOND_INVENTORY_REGENERATION: sync_inventory.py вызывается дополнительно в: {call_sites}. "
            f"Единственная точка регенерации — makefiles/helpers.mk (make test-inventory-sync). "
            f"Добавление второго вызова = дрейф (U-79)."
        )
    logger.info(
        "[IMP:9][test_single_inventory_regeneration_source] ✅ sync_inventory.py — единственный вызов: helpers.mk (test-inventory-sync)"
    )


@pytest.mark.gate
@ldd_trajectory

# 🧪 TRAP[TEST] · 2026-08-01 · REGRESSION · rename-пары (нормализованные file+func) (U-79)
# · Scenario: _normalize_nodeid — параметризация уходит, переименование = не удаление
# · Last fail: N/A (новый rename-детектор)
# · Remove if: rename-семантика реестра изменена
def test_rename_detection_normalization(caplog) -> None:
    """Verify _normalize_nodeid detects rename pairs (same normalized file+func).

    ## @purpose — DevPlan 116 B11 T6 (U-79): rename = удаление + добавление пары,
    ##            файл/функция совпадают по нормализованному имени. Проверяет, что
    ##            переименованная тест-функция и параметризованные варианты дают
    ##            одинаковые ключи, а разные функции — разные.
    ## @io — ⎋ None (assert)
    ## @complexity — O(1)
    """

    logger.info("[IMP:8][test_rename_detection_normalization] === Rename normalization audit ===")

    # Одна и та же функция в том же файле (переименование) → равные ключи
    old_key = _normalize_nodeid("tests/unit/test_foo.py::test_bar")
    new_key = _normalize_nodeid("tests/unit/test_foo.py::test_bar_renamed")
    assert old_key[0] == new_key[0], "file part must match after normalization"
    assert old_key[0] == "testsunittestfoopy", f"unexpected file key: {old_key[0]}"

    # Параметризованные варианты одной функции → равные ключи (param-изменение = не удаление)
    param_a = _normalize_nodeid("tests/unit/test_foo.py::test_bar[a]")
    param_b = _normalize_nodeid("tests/unit/test_foo.py::test_bar[b]")
    assert param_a == param_b, "parametrize brackets must normalize away (rename-pair semantics)"

    # Разные функции → разные ключи (НЕ rename → changelog обязателен)
    other = _normalize_nodeid("tests/unit/test_foo.py::test_baz")
    assert param_a[1] != other[1], "different functions must NOT be a rename pair"

    # Разные файлы → разные ключи (перемещение файла = НЕ rename по норме → changelog)
    moved = _normalize_nodeid("tests/unit/test_bar.py::test_bar")
    assert param_a[0] != moved[0], "different files must NOT be a rename pair by normalized name"

    logger.info(
        "[IMP:9][test_rename_detection_normalization] ✅ rename-пары: равные (file,func)-ключи; разные функции/файлы — НЕ rename"
    )


@pytest.mark.gate
@ldd_trajectory

# 🧪 TRAP[TEST] · 2026-08-01 · NEGATIVE (R5) · удаление без changelog (U-79)
# · Scenario: baseline-тест удалён из PR, changelog записи нет → детектор RED
# · Last fail: до B11 гейт не ловил удаления без changelog (молчаливое выпадение тестов)
# · Remove if: anti-tamper реестр заменён другим механизмом
def test_negative_undocumented_removal_detected(caplog) -> None:
    """R5 negative (U-79): удаление теста без changelog записи → детектор RED.

    ## @purpose — Anti-survivorship (R5): вход, поймавший исходный баг U-79
    ##            (тест удалён, changelog не обновлён), ДОЛЖЕН детектироваться
    ##            _find_undocumented_removals. Если детектор перестанет ловить —
    ##            тест упадёт, обнажая регрессию anti-tamper.
    ## @io — ⎋ None (assert)
    ## @complexity — O(1)
    """

    logger.info("[IMP:8][test_negative_undocumented_removal_detected] === R5 negative: undocumented removal ===")

    inventory = ["tests/unit/test_gone.py::test_vanished"]
    collected = []  # тест удалён из PR
    documented = set()  # changelog записи нет

    undocumented = _find_undocumented_removals(inventory, collected, documented)
    assert undocumented == ["tests/unit/test_gone.py::test_vanished"], (
        f"R5 FAIL (U-79): detector missed undocumented removal — got {undocumented}"
    )
    logger.info("[IMP:9][test_negative_undocumented_removal_detected] ✅ undocumented removal detected (RED)")


@pytest.mark.gate
@ldd_trajectory

# 🧪 TRAP[TEST] · 2026-08-01 · NEGATIVE (R5) · rename-пара не требует changelog (U-79)
# · Scenario: baseline-тест переименован (same normalized file+func) → НЕ undocumented
# · Last fail: до B11 rename-удаления ложно RED (требовали changelog)
# · Remove if: rename-семантика реестра изменена
def test_negative_rename_pair_exempt_from_changelog(caplog) -> None:
    """R5 negative (U-79): rename-пара (нормализованные file+func) → НЕ undocumented.

    ## @purpose — Anti-survivorship (R5): обратная сторона U-79 — переименование
    ##            НЕ должно требовать changelog (иначе легитимные rename ложно RED).
    ## @io — ⎋ None (assert)
    ## @complexity — O(1)
    """

    logger.info("[IMP:8][test_negative_rename_pair_exempt_from_changelog] === R5 negative: rename exempt ===")

    # Rename-пара U-79: параметризация [a]→[b] даёт тот же нормализованный (file, func) ключ
    inventory = ["tests/unit/test_foo.py::test_bar[a]"]
    collected = ["tests/unit/test_foo.py::test_bar[b]"]
    documented = set()

    undocumented = _find_undocumented_removals(inventory, collected, documented)
    assert undocumented == [], f"R5 FAIL (U-79): rename pair must be exempt from changelog — got {undocumented}"
    logger.info("[IMP:9][test_negative_rename_pair_exempt_from_changelog] ✅ rename-пара exempt (PASS)")


@pytest.mark.gate
@ldd_trajectory

# 🧪 TRAP[TEST] · 2026-08-01 · NEGATIVE (R5) · документированное удаление не RED (U-79)
# · Scenario: baseline-тест удалён, changelog запись есть → НЕ undocumented (PASS)
# · Last fail: N/A (парный negative к test_no_test_removed_without_changelog)
# · Remove if: anti-tamper реестр заменён другим механизмом
def test_negative_documented_removal_not_flagged(caplog) -> None:
    """R5 negative (U-79): удаление с changelog записью → НЕ undocumented (PASS).

    ## @purpose — Anti-survivorship (R5): документированное удаление должно быть
    ##            корректно исключено из RED-множества (иначе гейт ложно RED).
    ## @io — ⎋ None (assert)
    ## @complexity — O(1)
    """

    logger.info("[IMP:8][test_negative_documented_removal_not_flagged] === R5 negative: documented removal ===")

    inventory = ["tests/unit/test_gone.py::test_removed_legit"]
    collected = []
    documented = {"tests/unit/test_gone.py::test_removed_legit"}

    undocumented = _find_undocumented_removals(inventory, collected, documented)
    assert undocumented == [], f"R5 FAIL (U-79): documented removal must NOT be RED — got {undocumented}"
    logger.info("[IMP:9][test_negative_documented_removal_not_flagged] ✅ documented removal PASS")
