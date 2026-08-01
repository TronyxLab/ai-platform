"""
# GREP_SUMMARY: test-validate-orchestrator, discovery, schema-routing, extension-check, detect-validator, exit-codes, caplog, LDD, subprocess-monkeypatch, D2-lint, D3-discovery, D5-extension
# STRUCTURE: ▶ tmp_path fixtures (valid/invalid node.yaml + ai-platform.yml) → ◇ unit functions (discover/resolve/detect/extension) → ◇ main() flows with monkeypatched subprocess.run → ⊕ stderr golden asserts → ◇ LDD IMP:9 via ldd_trajectory → ⎋ exit-code asserts
# region MODULE_CONTRACT
## @purpose  Unit-тесты validate_orchestrator.py (DevPlan 107 T4) — Python-порт оркестрации
##           validate.sh. Покрывают: auto-discovery (D3-регрессия), schema-routing (D1),
##           extension-check (D5), detect_validator, python/ajv-делегации, lint-поведение (D2),
##           --check-fqdn/--check-ports (DD2/DD4), агрегацию ошибок, байт-идентичный stderr (AC7).
## @scope    Native imports (запрещён subprocess для бизнес-логики — CLI-делегации мокаются
##           через monkeypatch subprocess.run, граница subprocess не тестируется через subprocess).
##           tmp_path (zero hardcode), caplog + LDD (IMP:9 через ldd_trajectory).
## @invariants
##   - Каждая тест-функция несёт # 🧪 TRAP[TEST] (Regression/Scenario/Last fail/Remove if)
##   - Каждая тест-функция эмитит IMP:9 (кодом под тестом ИЛИ logger.critical в тесте) — ldd_trajectory asserts
##   - R5 anti-survivorship: error-path тесты с точным trigger-инпутом (invalid fixtures, missing args)
##   - Реальные схемы core/schemas/*.schema.json — read-only consumer
## @rationale AC1/AC3-AC7 верификация из §5. Golden-строки байт-идентичны validate.sh stderr-формату.
## @changes
##   2026-07-31 | Created (DevPlan 107 T4)
# endregion MODULE_CONTRACT
"""

import logging
import sys
from pathlib import Path

import pytest

from tests._conftest.ldd import ldd_trajectory

logger = logging.getLogger(__name__)

# ── Module under test: namespace-package import from repo root (без __init__.py) ──
_REPO_ROOT = Path(__file__).resolve().parents[2]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from core.internal.validate import validate_orchestrator

_NODE_SCHEMA = _REPO_ROOT / "core" / "schemas" / "node.schema.json"

# ── Static fixtures (valid per node.schema.json: required node/modules/context) ──
VALID_NODE_YAML = """\
context: prod
node:
  name: test-node
  host: 10.0.0.1
  owner_key: ssh-ed25519 AAAA test@test
modules: []
"""

# Missing "modules" (required at root) — R5 negative trigger
INVALID_MISSING_FIELD_YAML = """\
context: prod
node:
  name: test-node
  host: 10.0.0.1
  owner_key: ssh-ed25519 AAAA test@test
"""

# Минимальный ai-platform.yaml (валидный YAML; subprocess мокается, контент не критичен)
VALID_AIPLATFORM_YAML = """\
name: test-project
"""


# region HELPER
class _FakeProc:
    """Фейковый CompletedProcess для monkeypatch subprocess.run (граница subprocess)."""

    def __init__(self, returncode: int = 0, stdout: str = "", stderr: str = "") -> None:
        self.returncode = returncode
        self.stdout = stdout
        self.stderr = stderr


def _make_run(captured: list, returncode: int = 0, stderr: str = "", stdout: str = ""):
    """Фабрика fake subprocess.run: захватывает cmd, возвращает _FakeProc."""

    def _run(cmd, **kwargs):
        captured.append((list(cmd), kwargs))
        return _FakeProc(returncode=returncode, stdout=stdout, stderr=stderr)

    return _run


def _write(tmp_path: Path, name: str, content: str) -> Path:
    """Write fixture content into tmp_path and return the file path."""
    p = tmp_path / name
    p.write_text(content)
    return p


# endregion HELPER


# region TEST_DISCOVER
@ldd_trajectory
def test_discover_targets_finds_yaml_and_yml(tmp_path, caplog) -> None:
    """D3-регрессия: discover находит *.yaml И *.yml, sorted, без trailing \\n."""
    # 🧪 TRAP[TEST] · Regression: DevPlan 107 D3 — shell find|sort -z сливал весь вывод в один
    # · NUL-record с trailing \n → ложные «File not found». os.walk должен дать чистые пути.
    # · Scenario: tmp-дерево с a.yaml/b.yml/c.txt/sub/d.yaml
    # · Last fail: N/A (новый Python-порт)
    # · Remove if: auto-discovery перестанет быть os.walk+sorted
    logger.critical("[IMP:9][test] test_discover_targets_finds_yaml_and_yml — discovery contract")
    (tmp_path / "a.yaml").write_text("a: 1")
    (tmp_path / "b.yml").write_text("b: 1")
    (tmp_path / "c.txt").write_text("not yaml")
    sub = tmp_path / "sub"
    sub.mkdir()
    (sub / "d.yaml").write_text("d: 1")

    result = validate_orchestrator.discover_targets(tmp_path)

    names = [p.name for p in result]
    assert names == ["a.yaml", "b.yml", "d.yaml"], f"FAIL: unexpected discovery: {names}"
    assert result == sorted(result, key=str), "FAIL: результат должен быть sorted"
    assert all(not str(p).endswith("\n") for p in result), "FAIL: trailing \\n в пути (D3-регрессия)"


# endregion TEST_DISCOVER


# region TEST_DISCOVER_EMPTY
@ldd_trajectory
def test_discover_targets_empty_dir(tmp_path, caplog) -> None:
    """Пустой каталог → [] (без «No YAML» ложных срабатываний)."""
    # 🧪 TRAP[TEST] · Regression: DevPlan 107 — пустое дерево не должно возвращать мусор
    # · Scenario: tmp_path без yaml-файлов
    # · Last fail: N/A
    # · Remove if: discover_targets сигнатура меняется
    logger.critical("[IMP:9][test] test_discover_targets_empty_dir — empty tree contract")
    (tmp_path / "readme.md").write_text("# docs")

    result = validate_orchestrator.discover_targets(tmp_path)

    assert result == [], f"FAIL: ожидался пустой список, got {result}"


# endregion TEST_DISCOVER_EMPTY


# region TEST_RESOLVE_SCHEMA
@ldd_trajectory
def test_resolve_schema_node_module_aiplatform(caplog) -> None:
    """D1: routing 1:1 — node/module/ai-platform → соответствующие схемы (.yaml и .yml)."""
    # 🧪 TRAP[TEST] · Regression: DevPlan 107 D1/AC6 — schema routing по фактическому case validate.sh
    # · Scenario: 6 basename-вариантов → 3 schema basename
    # · Last fail: N/A
    # · Remove if: schema-routing контракт меняется
    logger.critical("[IMP:9][test] test_resolve_schema_node_module_aiplatform — routing map")
    assert validate_orchestrator.resolve_schema("node.yaml") == "node.schema.json"
    assert validate_orchestrator.resolve_schema("node.yml") == "node.schema.json"
    assert validate_orchestrator.resolve_schema("module.yaml") == "module.schema.json"
    assert validate_orchestrator.resolve_schema("module.yml") == "module.schema.json"
    assert validate_orchestrator.resolve_schema("ai-platform.yaml") == "ai-platform.schema.json"
    assert validate_orchestrator.resolve_schema("ai-platform.yml") == "ai-platform.schema.json"


# endregion TEST_RESOLVE_SCHEMA


# region TEST_RESOLVE_SCHEMA_UNKNOWN
@ldd_trajectory
def test_resolve_schema_unknown_returns_none(caplog) -> None:
    """D1: policy.yaml/README.md → None (skip non-declaration; llm-policy.schema.json НЕ подключён)."""
    # 🧪 TRAP[TEST] · Regression: DevPlan 107 D1 — вне-контрактные файлы скипаются
    # · Scenario: policy.yaml (llm), README.md, docker-compose.yml
    # · Last fail: N/A
    # · Remove if: добавляется routing для llm-policy (требует отдельного решения)
    logger.critical("[IMP:9][test] test_resolve_schema_unknown_returns_none — non-declaration skip")
    assert validate_orchestrator.resolve_schema("policy.yaml") is None
    assert validate_orchestrator.resolve_schema("README.md") is None
    assert validate_orchestrator.resolve_schema("docker-compose.yml") is None


# endregion TEST_RESOLVE_SCHEMA_UNKNOWN


# region TEST_EXTENSION_REJECTS
@ldd_trajectory
def test_check_project_extension_rejects_yml(tmp_path, caplog, capsys) -> None:
    """D5: ai-platform.yml → False + REJECT stderr; НЕ прерывает (rc игнорируется в main)."""
    # 🧪 TRAP[TEST] · Regression: DevPlan 107 D5/AD-2 — .yml для ai-platform → FAIL (00 §5)
    # · Scenario: basename ai-platform.yml → REJECT-строка байт-идентична validate.sh L116
    # · Last fail: N/A
    # · Remove if: extension-контракт меняется
    f = tmp_path / "ai-platform.yml"
    f.write_text(VALID_AIPLATFORM_YAML)

    ok = validate_orchestrator.check_project_extension(f)

    err = capsys.readouterr().err
    assert ok is False, "FAIL: ai-platform.yml должен быть rejected"
    assert (
        "[IMP:9][validate][extension] FAIL: REJECT: "
        f"'{f}' uses .yml extension — platform requires .yaml for ai-platform declarations" in err
    ), f"FAIL: REJECT-строка не найдена в stderr: {err!r}"


# endregion TEST_EXTENSION_REJECTS


# region TEST_EXTENSION_ALLOWS
@ldd_trajectory
def test_check_project_extension_allows_yaml(tmp_path, caplog, capsys) -> None:
    """ai-platform.yaml и прочие .yml (docker-compose.yml) → True без REJECT."""
    # 🧪 TRAP[TEST] · Regression: DevPlan 107 — только ТОЧНЫЙ basename ai-platform.yml rejected
    # · Scenario: ai-platform.yaml → True; docker-compose.yml → True (не трогается)
    # · Last fail: N/A
    # · Remove if: extension-контракт меняется
    logger.critical("[IMP:9][test] test_check_project_extension_allows_yaml — allow-list")
    yaml_f = tmp_path / "ai-platform.yaml"
    yaml_f.write_text(VALID_AIPLATFORM_YAML)
    compose_f = tmp_path / "docker-compose.yml"
    compose_f.write_text("services: {}")

    assert validate_orchestrator.check_project_extension(yaml_f) is True
    assert validate_orchestrator.check_project_extension(compose_f) is True
    assert capsys.readouterr().err == "", "FAIL: allow-path не должен эмитить stderr"


# endregion TEST_EXTENSION_ALLOWS


# region TEST_DETECT_AJV
@ldd_trajectory
def test_detect_validator_ajv_preferred(caplog, monkeypatch) -> None:
    """ajv приоритетен: which('ajv') найден → 'ajv' (find_spec не вызывается)."""
    # 🧪 TRAP[TEST] · Regression: DevPlan 107 — detect_validator() из validate.sh L49-51
    # · Scenario: ajv в PATH → приоритет перед python-jsonschema
    # · Last fail: N/A
    # · Remove if: порядок выбора валидатора меняется
    logger.critical("[IMP:9][test] test_detect_validator_ajv_preferred — ajv priority")
    monkeypatch.setattr(validate_orchestrator.shutil, "which", lambda _name: "/usr/local/bin/ajv")

    assert validate_orchestrator.detect_validator() == "ajv"


# endregion TEST_DETECT_AJV


# region TEST_DETECT_PYTHON
@ldd_trajectory
def test_detect_validator_python_fallback(caplog, monkeypatch) -> None:
    """ajv отсутствует + jsonschema доступен → 'python'."""
    # 🧪 TRAP[TEST] · Regression: DevPlan 107 — python-jsonschema fallback (текущее окружение)
    # · Scenario: which→None, find_spec('jsonschema')→spec
    # · Last fail: N/A
    # · Remove if: порядок выбора валидатора меняется
    logger.critical("[IMP:9][test] test_detect_validator_python_fallback — python fallback")
    monkeypatch.setattr(validate_orchestrator.shutil, "which", lambda _name: None)
    monkeypatch.setattr(validate_orchestrator.importlib.util, "find_spec", lambda _name: object())

    assert validate_orchestrator.detect_validator() == "python"


# endregion TEST_DETECT_PYTHON


# region TEST_DETECT_NONE
@ldd_trajectory
def test_detect_validator_none_exits_1(caplog, monkeypatch, capsys) -> None:
    """Ни ajv, ни jsonschema → [IMP:10][validate][detect] ERROR + exit 1 (байт-идентично L54-56)."""
    # 🧪 TRAP[TEST] · Regression: DevPlan 107 — No validator found error path (AC7 golden)
    # · Scenario: which→None, find_spec→None → SystemExit(1) + точная ERROR-строка
    # · Last fail: N/A
    # · Remove if: detect-контракт меняется
    monkeypatch.setattr(validate_orchestrator.shutil, "which", lambda _name: None)
    monkeypatch.setattr(validate_orchestrator.importlib.util, "find_spec", lambda _name: None)

    from core.internal.shared.exceptions import PlatformFatalError

    with pytest.raises(PlatformFatalError) as exc_info:
        validate_orchestrator.detect_validator()

    assert exc_info.value.exit_code == 10, "FAIL: exit code должен быть 10 (D4)"
    # T3.6: сообщение перенесено в исключение (emit-вывод заменён на raise PlatformFatalError)
    assert "No validator found" in str(exc_info.value), f"FAIL: сообщение ошибки: {str(exc_info.value)!r}"


# endregion TEST_DETECT_NONE


# region TEST_PYTHON_OK
@ldd_trajectory
def test_validate_with_python_ok(tmp_path, caplog, monkeypatch, capsys) -> None:
    """subprocess rc=0 → [IMP:7][validate][python] OK: <file>, без инкремента ошибок."""
    # 🧪 TRAP[TEST] · Regression: DevPlan 107 AC7 — python happy path (validate.sh L104)
    # · Scenario: jsonschema_validate exit 0 → OK-строка
    # · Last fail: N/A
    # · Remove if: python-путь контракт меняется
    logger.critical("[IMP:9][test] test_validate_with_python_ok — happy path checkpoint")
    yaml_f = _write(tmp_path, "node.yaml", VALID_NODE_YAML)
    captured: list = []
    monkeypatch.setattr(validate_orchestrator.subprocess, "run", _make_run(captured, returncode=0))

    ok = validate_orchestrator.validate_with_python(yaml_f, _NODE_SCHEMA)

    assert ok is True
    err = capsys.readouterr().err
    assert f"[IMP:7][validate][python] OK: {yaml_f}" in err, f"FAIL: OK-строка не найдена: {err!r}"
    cmd = captured[0][0]
    assert cmd[0] == sys.executable
    assert "core.internal.scripts.jsonschema_validate" in cmd, f"FAIL: cmd: {cmd}"
    assert "--yaml-file" in cmd and "--schema-file" in cmd


# endregion TEST_PYTHON_OK


# region TEST_PYTHON_FAIL
@ldd_trajectory
def test_validate_with_python_fail_format(tmp_path, caplog, monkeypatch, capsys) -> None:
    """rc=1 + jsonschema-ошибка → golden FAIL: <file>:\\n<error> (AC7, байт-идентично L99-100)."""
    # 🧪 TRAP[TEST] · Regression: DevPlan 107 AC7 — golden multi-line FAIL format (093 AC1)
    # · Scenario: rc=1, stderr="  Error at '(root)': 'modules' is a required property"
    # ·   → [IMP:9][validate][python] FAIL: <f>:\n<err>
    # · Last fail: N/A
    # · Remove if: python FAIL-формат меняется
    yaml_f = _write(tmp_path, "node.yaml", INVALID_MISSING_FIELD_YAML)
    golden_err = "  Error at '(root)': 'modules' is a required property"
    captured: list = []
    monkeypatch.setattr(validate_orchestrator.subprocess, "run", _make_run(captured, returncode=1, stderr=golden_err))

    ok = validate_orchestrator.validate_with_python(yaml_f, _NODE_SCHEMA)

    assert ok is False
    err = capsys.readouterr().err
    expected = f"[IMP:9][validate][python] FAIL: {yaml_f}:\n{golden_err}\n"
    assert err == expected, f"FAIL: golden mismatch\nEXPECTED: {expected!r}\nACTUAL:   {err!r}"


# endregion TEST_PYTHON_FAIL


# region TEST_FILE_MISSING
@ldd_trajectory
def test_validate_file_missing_file_schema(tmp_path, caplog, capsys) -> None:
    """Несуществующий yaml → File not found; несуществующая schema → Schema not found."""
    # 🧪 TRAP[TEST] · Regression: DevPlan 107 — fail-fast до валидации (validate.sh L129-136)
    # · Scenario: missing yaml + missing schema — обе ветки
    # · Last fail: N/A
    # · Remove if: file/schema existence contract меняется
    missing_yaml = tmp_path / "missing.yaml"
    missing_schema = tmp_path / "missing.schema.json"
    existing_yaml = _write(tmp_path, "node.yaml", VALID_NODE_YAML)

    ok1 = validate_orchestrator.validate_file(missing_yaml, _NODE_SCHEMA, "python")
    ok2 = validate_orchestrator.validate_file(existing_yaml, missing_schema, "python")

    assert ok1 is False and ok2 is False
    err = capsys.readouterr().err
    assert f"[IMP:9][validate][file] FAIL: File not found: {missing_yaml}" in err
    assert f"[IMP:9][validate][schema] FAIL: Schema not found: {missing_schema}" in err


# endregion TEST_FILE_MISSING


# region TEST_MAIN_LINT
@ldd_trajectory
def test_main_flag_only_skips_discovery(caplog, monkeypatch, capsys) -> None:
    """D2/AC5: main(['--lint']) → discovery НЕ вызван, exit 0, 'OK: All files valid'."""
    # 🧪 TRAP[TEST] · Regression: DevPlan 107 D2 — make lint = validate --lint = no-op pass
    # · Scenario: --* аргумент скипается в цикле, discovery пропущен (args непустые)
    # · Last fail: N/A
    # · Remove if: lint-контракт меняется
    logger.critical("[IMP:9][test] test_main_flag_only_skips_discovery — lint no-op pass")
    monkeypatch.setattr(validate_orchestrator, "detect_validator", lambda: "python")
    import unittest.mock as mock

    discovery_mock = mock.Mock()
    monkeypatch.setattr(validate_orchestrator, "discover_targets", discovery_mock)

    rc = validate_orchestrator.main(["--lint"])

    assert rc == 0, f"FAIL: --lint должен exit 0, got {rc}"
    assert not discovery_mock.called, "FAIL: discovery НЕ должен вызываться при непустых args"
    err = capsys.readouterr().err
    assert "[IMP:8][validate][result] OK: All files valid" in err
    assert "[IMP:6][validate][skip]" not in err, "FAIL: --lint не должен валидировать файлы"


# endregion TEST_MAIN_LINT


# region TEST_MAIN_EXPLICIT
@ldd_trajectory
def test_main_explicit_file_validates(tmp_path, caplog, monkeypatch, capsys) -> None:
    """AC4: explicit node.yaml → exit 0 + OK (реальный путь передачи файлов)."""
    # 🧪 TRAP[TEST] · Regression: DevPlan 107 AC4/D4 — explicit file arg валидируется identically
    # · Scenario: main([node.yaml]) с mocked subprocess rc=0 → python OK → exit 0
    # · Last fail: N/A
    # · Remove if: explicit-file контракт меняется
    logger.critical("[IMP:9][test] test_main_explicit_file_validates — explicit file path")
    yaml_f = _write(tmp_path, "node.yaml", VALID_NODE_YAML)
    monkeypatch.setattr(validate_orchestrator, "detect_validator", lambda: "python")
    captured: list = []
    monkeypatch.setattr(validate_orchestrator.subprocess, "run", _make_run(captured, returncode=0))

    rc = validate_orchestrator.main([str(yaml_f)])

    assert rc == 0, f"FAIL: валидный node.yaml должен exit 0, got {rc}"
    err = capsys.readouterr().err
    assert f"[IMP:6][validate][validate] Validating: {yaml_f} against node.schema.json" in err
    assert f"[IMP:7][validate][python] OK: {yaml_f}" in err
    assert "[IMP:8][validate][result] OK: All files valid" in err


# endregion TEST_MAIN_EXPLICIT


# region TEST_MAIN_AGGREGATION
@ldd_trajectory
def test_main_error_aggregation_exit_1(tmp_path, caplog, monkeypatch, capsys) -> None:
    """Невалидный node.yaml → FAIL: 1 validation error(s) found + exit 1 (AC7)."""
    # 🧪 TRAP[TEST] · Regression: DevPlan 107 — агрегация ошибок (validate.sh L241-244)
    # · Scenario: invalid fixture (missing modules) → python FAIL → result FAIL → exit 1
    # · Last fail: N/A
    # · Remove if: error aggregation контракт меняется
    yaml_f = _write(tmp_path, "node.yaml", INVALID_MISSING_FIELD_YAML)
    monkeypatch.setattr(validate_orchestrator, "detect_validator", lambda: "python")
    captured: list = []
    monkeypatch.setattr(
        validate_orchestrator.subprocess,
        "run",
        _make_run(captured, returncode=1, stderr="  Error at '(root)': 'modules' is a required property"),
    )

    rc = validate_orchestrator.main([str(yaml_f)])

    assert rc == 1, f"FAIL: невалидный node.yaml должен exit 1, got {rc}"
    err = capsys.readouterr().err
    assert "[IMP:9][validate][result] FAIL: 1 validation error(s) found" in err
    assert f"[IMP:9][validate][python] FAIL: {yaml_f}:\n" in err


# endregion TEST_MAIN_AGGREGATION


# region TEST_MAIN_FQDN_DELEGATES
@ldd_trajectory
def test_main_check_fqdn_delegates(caplog, monkeypatch, capsys) -> None:
    """--check-fqdn DIR → subprocess conflict_checks check-fqdn, exit = child rc (DD2/DD4)."""
    # 🧪 TRAP[TEST] · Regression: DevPlan 107 DD4 — спец-флаг делегируется, exit passthrough
    # · Scenario: conflict_checks rc=1 → main exit 1; cmd содержит check-fqdn + dir
    # · Last fail: N/A
    # · Remove if: --check-fqdn контракт меняется
    logger.critical("[IMP:9][test] test_main_check_fqdn_delegates — спец-флаг passthrough")
    project_dir = "/tmp/fake-project"
    captured: list = []
    monkeypatch.setattr(validate_orchestrator.subprocess, "run", _make_run(captured, returncode=1))

    rc = validate_orchestrator.main(["--check-fqdn", project_dir])

    assert rc == 1, f"FAIL: child rc=1 должен проброситься, got {rc}"
    cmd = captured[0][0]
    assert "core.internal.validate.conflict_checks" in cmd, f"FAIL: cmd: {cmd}"
    assert "check-fqdn" in cmd
    assert project_dir in cmd
    assert captured[0][1].get("cwd") == str(_REPO_ROOT), "FAIL: cwd должен быть REPO_ROOT (DD3)"


# endregion TEST_MAIN_FQDN_DELEGATES


# region TEST_MAIN_FQDN_MISSING_ARG
@ldd_trajectory
def test_main_check_fqdn_missing_arg(caplog, capsys) -> None:
    """--check-fqdn без аргумента → [IMP:10][validate][fqdn] ERROR + exit 1 (байт-идентично)."""
    # 🧪 TRAP[TEST] · Regression: DevPlan 107 — спец-флаг без arg (validate.sh L175-177)
    # · Scenario: main(['--check-fqdn']) → точная ERROR-строка
    # · Last fail: N/A
    # · Remove if: fqdn arg contract меняется
    rc = validate_orchestrator.main(["--check-fqdn"])

    assert rc == 1, f"FAIL: должен exit 1, got {rc}"
    err = capsys.readouterr().err
    assert "[IMP:10][validate][fqdn] ERROR: --check-fqdn requires a project directory argument" in err, (
        f"FAIL: ERROR-строка не найдена: {err!r}"
    )


# endregion TEST_MAIN_FQDN_MISSING_ARG


# region TEST_MAIN_PORTS
@ldd_trajectory
def test_main_check_ports_default_base(caplog, monkeypatch, capsys) -> None:
    """--check-ports: PROJECTS_BASE env → передан как base; без env → fallback core/projects|''."""
    # 🧪 TRAP[TEST] · Regression: DevPlan 107 DD4 — default base resolution (validate.sh L183-194)
    # · Scenario A: env PROJECTS_BASE set → subprocess с этим base
    # · Scenario B: env unset → core/projects если существует, иначе ""
    # · Last fail: N/A
    # · Remove if: --check-ports base resolution меняется
    logger.critical("[IMP:9][test] test_main_check_ports_default_base — base resolution")
    captured: list = []
    monkeypatch.setattr(validate_orchestrator.subprocess, "run", _make_run(captured, returncode=0))

    # Scenario A: PROJECTS_BASE env
    monkeypatch.setenv("PROJECTS_BASE", "/custom/projects")
    rc_a = validate_orchestrator.main(["--check-ports"])
    assert rc_a == 0
    cmd_a = captured[-1][0]
    assert "check-ports" in cmd_a
    assert cmd_a[-1] == "/custom/projects", f"FAIL: base должен быть из env, got {cmd_a[-1]}"

    # Scenario B: без env → fallback
    monkeypatch.delenv("PROJECTS_BASE", raising=False)
    rc_b = validate_orchestrator.main(["--check-ports"])
    assert rc_b == 0
    cmd_b = captured[-1][0]
    fallback = str(_REPO_ROOT / "core" / "projects")
    expected = fallback if Path(fallback).is_dir() else ""
    assert cmd_b[-1] == expected, f"FAIL: fallback base mismatch, got {cmd_b[-1]!r}, expected {expected!r}"


# endregion TEST_MAIN_PORTS


# region TEST_AIPLATFORM_YML
@ldd_trajectory
def test_aiplatform_yml_extension_error_plus_validation(tmp_path, caplog, monkeypatch, capsys) -> None:
    """D5: ai-platform.yml → extension REJECT + migration INFO + schema-валидация ВСЁ РАВНО выполнена."""
    # 🧪 TRAP[TEST] · Regression: DevPlan 107 D5 — extension-check НЕ short-circuit'ит (validate.sh L230-234)
    # · Scenario: ai-platform.yml valid-fixture, subprocess rc=0 → extension FAIL (errors=1),
    # ·   migration INFO, validate выполнен (python OK), итог exit 1 (только extension-ошибка)
    # · Last fail: N/A
    # · Remove if: ai-platform.yml ветка меняется
    yaml_f = _write(tmp_path, "ai-platform.yml", VALID_AIPLATFORM_YAML)
    monkeypatch.setattr(validate_orchestrator, "detect_validator", lambda: "python")
    captured: list = []
    monkeypatch.setattr(validate_orchestrator.subprocess, "run", _make_run(captured, returncode=0))

    rc = validate_orchestrator.main([str(yaml_f)])

    err = capsys.readouterr().err
    assert rc == 1, f"FAIL: extension-error должен дать exit 1, got {rc}"
    assert f"[IMP:9][validate][extension] FAIL: REJECT: '{yaml_f}'" in err, "FAIL: REJECT отсутствует"
    assert f"[IMP:6][validate][migration] INFO: '{yaml_f}' — единый формат манифеста (AD-2)" in err
    assert f"[IMP:6][validate][validate] Validating: {yaml_f} against ai-platform.schema.json" in err
    assert captured, "FAIL: schema-валидация должна быть выполнена (D5: НЕ short-circuit)"
    assert "[IMP:9][validate][result] FAIL: 1 validation error(s) found" in err


# endregion TEST_AIPLATFORM_YML


# region TEST_EMIT
@ldd_trajectory
def test_emit_format(caplog, capsys) -> None:
    """emit(9, 'python', 'FAIL: x') → байт-идентичная stderr-строка (AC7) + caplog-запись."""
    # 🧪 TRAP[TEST] · Regression: DevPlan 107 AC7 — byte-identical [IMP:N][validate][block] msg
    # · Scenario: emit(9, "python", "FAIL: x") → "[IMP:9][validate][python] FAIL: x\n"
    # · Last fail: N/A
    # · Remove if: stderr-формат контракта меняется
    validate_orchestrator.emit(9, "python", "FAIL: x")

    err = capsys.readouterr().err
    assert err == "[IMP:9][validate][python] FAIL: x\n", f"FAIL: byte-mismatch: {err!r}"

    ldd_messages = [r.message for r in caplog.records if "[IMP:" in r.message]
    assert any("[IMP:9][validate][python] FAIL: x" in m for m in ldd_messages), (
        "FAIL: emit должен дублировать в logger (caplog LDD)"
    )


# endregion TEST_EMIT
