# GREP_SUMMARY: test-node-resolver node_resolver.py W2 resolve_node_yaml extract_node_host env NODE_NAME 4-path-search platform-node-configs overlay-wins legacy-warn CLI exit-contract LDD IMP:9
# STRUCTURE: ▶ 20 tests → ○ resolve (env PLATFORM_ROOT/HOME/NODE_NAME, explicit platform_root, platform-overlay-wins, legacy-warn, group-order, not-found, idempotent)
#            → ○ extract host (present/missing/bool-normalized/file-missing) → ○ CLI (resolve/host exit 0|1, stdout one-line, idempotent) → ⎋ PASS

# region MODULE_CONTRACT [DOMAIN(TESTING):3; CONCEPT(NODE-RESOLVER-PYTHON):3; TECH(PYTEST):2]
## @purpose  Unit-тесты core/internal/shared/node_resolver.py (DevPlan 127 W2, S8/P2-1) —
##           Python-резолв node.yaml (перенос из core/lib/node-resolver.sh, 215 LOC).
##           Покрытие: резолв по env (PLATFORM_ROOT / HOME glob / NODE_NAME), explicit
##           platform_root (hermetic DI), not-found → читаемая ошибка, extract_node_host
##           (включая T6-нормализацию bool), CLI exit-контракт 0/1 + stdout одна строка.
## @scope    Native pytest, НИКАКИХ subprocess (bash-фасад регрессионно покрыт
##           tests/test_lib_node_resolver.py). tmp_path + monkeypatch env — Zero Hardcode.
## @invariants
##   - Каждый тест: caplog.set_level(INFO) + assert IMP:9-лог присутствует (LDD telemetry)
##   - env-изоляция: monkeypatch.delenv(PLATFORM_ROOT/PLATFORM_REMOTE_BASE) в тестах,
##     где поиск должен быть hermetic (HOME glob / not-found)
##   - node-имена уникальны (127-w2-*) — исключают коллизии с реальным /opt/node-configs/
## @rationale W2 (DevPlan 127): резолв перенесён в Python — native-тесты без bash-обёртки;
##            CLI покрывает байт-совместимый exit-контракт для shell-фасада.
## @modulemap
##   - test_resolve_*        [W:110] 3-path search: env/glob/NODE_NAME/explicit/not-found/идемпотентность
##   - test_extract_node_host* [W:60] host-извлечение + T6-нормализация + missing file
##   - test_cli_*            [W:80]  CLI exit 0/1, stdout одна строка, идемпотентность
## @usecases  Разработчик: pytest tests/unit/test_node_resolver.py — регрессия резолва;
##            QA: exit-контракт CLI (0/1) для фасада node-resolver.sh.
# endregion MODULE_CONTRACT

import logging
from pathlib import Path

import pytest

from core.internal.shared import node_resolver
from core.internal.shared.exceptions import ConfigNotFoundError, ConfigParseError
from tests.helpers.gate_helpers import assert_ldd_imp9

logger = logging.getLogger(__name__)


# T2.16a: _assert_imp9 консолидирован в gate_helpers.assert_ldd_imp9


# region FUNC__make_node_yaml
def _make_node_yaml(base: Path, node: str, host: str = "1.2.3.4") -> Path:
    """Создать {base}/node-configs/{node}/node.yaml (path 1 кандидат).

    ▶ ┌base, node, host┐ → ⊕ mkdir + write → ⎋ Path
    """
    p = base / "node-configs" / node / "node.yaml"
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(f"node:\n  host: {host}\n")
    return p


# endregion FUNC__make_node_yaml


# ── resolve: 3-path search ─────────────────────────────────────────────────────


# region FUNC_test_resolve_by_env_platform_root
# 🧪 TRAP[TEST] · 2026-08-04 · REGRESSION · W2 резолв по env PLATFORM_ROOT (path 1)
# · Scenario: PLATFORM_ROOT env → node.yaml найден в path 1; IMP:9 "Resolved node.yaml"
# · Last fail: N/A (preventive — канон NodeYaml.resolve env-driven)
# · Remove if: резолв перестанет читать PLATFORM_ROOT env
def test_resolve_by_env_platform_root(
    tmp_path: Path, caplog: pytest.LogCaptureFixture, monkeypatch: pytest.MonkeyPatch
) -> None:
    caplog.set_level(logging.INFO)
    platform_root = tmp_path / "platform"
    node_yaml = _make_node_yaml(platform_root, "mynode")

    resolved = node_resolver.resolve_node_yaml("mynode", env={"PLATFORM_ROOT": str(platform_root)})

    assert resolved == str(node_yaml), resolved
    assert any("Resolved node.yaml:" in r.message for r in caplog.records), caplog.text
    assert_ldd_imp9(caplog)


# endregion FUNC_test_resolve_by_env_platform_root


# region FUNC_test_resolve_by_home_projects_glob
# 🧪 TRAP[TEST] · 2026-08-04 · REGRESSION · W2 резолв по HOME glob (path 2)
# · Scenario: PLATFORM_ROOT удалён; HOME=tmp → ~/projects/ctx/node-configs/N/node.yaml найден
# · Last fail: N/A (preventive — канон NodeYaml.resolve glob)
# · Remove if: glob-поиск path 2 удалён/изменён
def test_resolve_by_home_projects_glob(
    tmp_path: Path, caplog: pytest.LogCaptureFixture, monkeypatch: pytest.MonkeyPatch
) -> None:
    caplog.set_level(logging.INFO)
    node_yaml = tmp_path / "projects" / "ctx" / "node-configs" / "globnode" / "node.yaml"
    node_yaml.parent.mkdir(parents=True)
    node_yaml.write_text("node:\n  host: 10.0.0.1\n")
    # HOME glob: os.path.expanduser("~/projects") читает РЕАЛЬНЫЙ HOME — setenv легитимен
    # (path 2 канона NodeYaml.resolve; env-dict DI покрывает только NODE_NAME/PLATFORM_ROOT)
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.delenv("PLATFORM_ROOT", raising=False)

    resolved = node_resolver.resolve_node_yaml("globnode")

    assert resolved == str(node_yaml), resolved
    assert_ldd_imp9(caplog)


# endregion FUNC_test_resolve_by_home_projects_glob


# region FUNC_test_platform_overlay_wins_over_legacy_fixture
# 🧪 TRAP[TEST] · 2026-09-01 · REGRESSION · DevPlan 024 TASK-1: platform-overlay glob бьёт legacy-фикстуру
# · Scenario: legacy ~/projects/legacy-fixture/node-configs/N/ + overlay ~/projects/<ctx>/platform/node-configs/N/
#             → резолв = platform-путь, БЕЗ legacy WARN
# · Last fail: 2026-09-01 — старый glob platform-путь не находил (анти-survivorship: падает на старом коде,
#   sorted legacy-glob возвращал 'legacy-fixture' < '<ctx>')
# · Remove if: порядок кандидатов резолва изменён (platform-glob удалён/перенесён)
def test_platform_overlay_wins_over_legacy_fixture(
    tmp_path: Path, caplog: pytest.LogCaptureFixture, monkeypatch: pytest.MonkeyPatch
) -> None:
    caplog.set_level(logging.INFO)
    node = "overlay-wins-024"
    legacy_yaml = tmp_path / "projects" / "legacy-fixture" / "node-configs" / node / "node.yaml"
    overlay_yaml = tmp_path / "projects" / "test-ctx" / "platform" / "node-configs" / node / "node.yaml"
    for p in (legacy_yaml, overlay_yaml):
        p.parent.mkdir(parents=True)
        p.write_text("node:\n  host: 10.0.0.1\n")
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.delenv("PLATFORM_ROOT", raising=False)
    monkeypatch.delenv("PLATFORM_REMOTE_BASE", raising=False)

    resolved = node_resolver.resolve_node_yaml(node)

    assert resolved == str(overlay_yaml), resolved
    # Overlay-канон найден → миграционный WARN НЕ эмитится
    assert not any("legacy-fallback" in r.message for r in caplog.records), caplog.text
    assert_ldd_imp9(caplog)


# endregion FUNC_test_platform_overlay_wins_over_legacy_fixture


# region FUNC_test_legacy_glob_fallback_with_warn
# 🧪 TRAP[TEST] · 2026-09-01 · SCENARIO · DevPlan 024 TASK-1: legacy-резолв → [IMP:7] WARN (миграционный сигнал)
# · Scenario: только legacy-матч → резолв = legacy-путь + WARN legacy-fallback (видимость долга asi-group)
# · Last fail: N/A (preventive — WARN-сигнал добавлен 024 TASK-1)
# · Remove if: legacy sibling-glob удалён (после миграции asi-group — Rev TRAP[BUG] resolve.py)
def test_legacy_glob_fallback_with_warn(
    tmp_path: Path, caplog: pytest.LogCaptureFixture, monkeypatch: pytest.MonkeyPatch
) -> None:
    caplog.set_level(logging.INFO)
    node = "legacy-warn-024"
    legacy_yaml = tmp_path / "projects" / "ctx-legacy" / "node-configs" / node / "node.yaml"
    legacy_yaml.parent.mkdir(parents=True)
    legacy_yaml.write_text("node:\n  host: 10.0.0.1\n")
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.delenv("PLATFORM_ROOT", raising=False)
    monkeypatch.delenv("PLATFORM_REMOTE_BASE", raising=False)

    resolved = node_resolver.resolve_node_yaml(node)

    assert resolved == str(legacy_yaml), resolved
    warn_records = [r for r in caplog.records if "legacy-fallback" in r.message and "[IMP:7]" in r.message]
    assert warn_records, f"Expected [IMP:7] legacy-fallback WARN, got: {caplog.text}"
    assert_ldd_imp9(caplog)


# endregion FUNC_test_legacy_glob_fallback_with_warn


# region FUNC_test_explicit_config_dir_still_first
# 🧪 TRAP[TEST] · 2026-09-01 · REGRESSION · DevPlan 024 TASK-1 (D1): explicit config_dir (Path 1) — ПЕРВЫЙ
# · Scenario: env PLATFORM_ROOT=<tmp> + overlay-матч в fake HOME → резолв = Path 1
#             (контракт e2e conftest Path 1 сохранён)
# · Last fail: N/A (preventive — порядок Path 1 не менялся)
# · Remove if: platform-glob поднят выше explicit config_dir (ломает e2e-контракт)
def test_explicit_config_dir_still_first(
    tmp_path: Path, caplog: pytest.LogCaptureFixture, monkeypatch: pytest.MonkeyPatch
) -> None:
    caplog.set_level(logging.INFO)
    node = "explicit-first-024"
    platform_root = tmp_path / "platform"
    node_yaml = _make_node_yaml(platform_root, node)
    overlay_yaml = tmp_path / "projects" / "some-ctx" / "platform" / "node-configs" / node / "node.yaml"
    overlay_yaml.parent.mkdir(parents=True)
    overlay_yaml.write_text("node:\n  host: 10.0.0.9\n")
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.delenv("PLATFORM_ROOT", raising=False)
    monkeypatch.delenv("PLATFORM_REMOTE_BASE", raising=False)

    resolved = node_resolver.resolve_node_yaml(node, env={"PLATFORM_ROOT": str(platform_root)})

    assert resolved == str(node_yaml), resolved
    assert_ldd_imp9(caplog)


# endregion FUNC_test_explicit_config_dir_still_first


# region FUNC_test_platform_group_beats_legacy_group
# 🧪 TRAP[TEST] · 2026-09-01 · SCENARIO · DevPlan 024 TASK-1 (D1): групповой порядок детерминирован
# · Scenario: ≥2 platform-матчей (aaa, zzz) + ≥2 legacy-матчей (aaa, zzz) → победитель из
#             platform-группы (первый в sorted), legacy-пути не выигрывают ни при каком раскладе
# · Last fail: N/A (preventive — групповой порядок канона 024)
# · Remove if: группировка кандидатов изменена (interleaved порядок)
def test_platform_group_beats_legacy_group(
    tmp_path: Path, caplog: pytest.LogCaptureFixture, monkeypatch: pytest.MonkeyPatch
) -> None:
    caplog.set_level(logging.INFO)
    node = "group-order-024"
    overlay_a = tmp_path / "projects" / "aaa" / "platform" / "node-configs" / node / "node.yaml"
    overlay_z = tmp_path / "projects" / "zzz" / "platform" / "node-configs" / node / "node.yaml"
    legacy_a = tmp_path / "projects" / "aaa" / "node-configs" / node / "node.yaml"
    legacy_z = tmp_path / "projects" / "zzz" / "node-configs" / node / "node.yaml"
    for p in (overlay_a, overlay_z, legacy_a, legacy_z):
        p.parent.mkdir(parents=True)
        p.write_text("node:\n  host: 10.0.0.1\n")
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.delenv("PLATFORM_ROOT", raising=False)
    monkeypatch.delenv("PLATFORM_REMOTE_BASE", raising=False)

    resolved = node_resolver.resolve_node_yaml(node)

    assert resolved == str(overlay_a), resolved
    assert resolved != str(legacy_a) and resolved != str(legacy_z), "Legacy group must not win"
    assert resolved != str(overlay_z), "Winner must be first of sorted platform group"
    assert not any("legacy-fallback" in r.message for r in caplog.records), caplog.text
    assert_ldd_imp9(caplog)


# endregion FUNC_test_platform_group_beats_legacy_group


# region FUNC_test_resolve_by_node_env
# 🧪 TRAP[TEST] · 2026-08-04 · REGRESSION · W2 резолв по NODE_NAME env (без аргумента node)
# · Scenario: NODE_NAME env + PLATFORM_ROOT env → resolve_node_yaml() без аргумента находит
# · Last fail: N/A (preventive — канон NodeYaml.resolve NODE_NAME env)
# · Remove if: резолв перестанет читать NODE_NAME env
def test_resolve_by_node_env(tmp_path: Path, caplog: pytest.LogCaptureFixture, monkeypatch: pytest.MonkeyPatch) -> None:
    caplog.set_level(logging.INFO)
    platform_root = tmp_path / "platform"
    node_yaml = _make_node_yaml(platform_root, "envnode")

    resolved = node_resolver.resolve_node_yaml(env={"PLATFORM_ROOT": str(platform_root), "NODE_NAME": "envnode"})

    assert resolved == str(node_yaml), resolved
    assert_ldd_imp9(caplog)


# endregion FUNC_test_resolve_by_node_env


# region FUNC_test_resolve_explicit_platform_root
# 🧪 TRAP[TEST] · 2026-08-04 · REGRESSION · W2 explicit platform_root (hermetic DI)
# · Scenario: platform_root передан аргументом → найден path 1 независимо от env
# · Last fail: N/A (preventive — DI-канал для hermetic-тестов)
# · Remove if: параметр platform_root удалён
def test_resolve_explicit_platform_root(
    tmp_path: Path, caplog: pytest.LogCaptureFixture, monkeypatch: pytest.MonkeyPatch
) -> None:
    caplog.set_level(logging.INFO)
    platform_root = tmp_path / "platform"
    node_yaml = _make_node_yaml(platform_root, "explicitnode")
    # Убираем env-«шум» dev-машины — аргумент должен доминировать
    monkeypatch.delenv("PLATFORM_ROOT", raising=False)
    monkeypatch.delenv("PLATFORM_REMOTE_BASE", raising=False)

    resolved = node_resolver.resolve_node_yaml("explicitnode", platform_root=str(platform_root))

    assert resolved == str(node_yaml), resolved
    assert_ldd_imp9(caplog)


# endregion FUNC_test_resolve_explicit_platform_root


# region FUNC_test_resolve_not_found_readable_error
# 🧪 TRAP[TEST] · 2026-08-04 · REGRESSION · W2 not-found → читаемая ошибка ConfigNotFoundError
# · Scenario: пустые env/пути → ConfigNotFoundError; "node.yaml not found for node=" в сообщении
# · Last fail: N/A (preventive — readable error контракт)
# · Remove if: семантика not-found изменена
def test_resolve_not_found_readable_error(
    tmp_path: Path, caplog: pytest.LogCaptureFixture, monkeypatch: pytest.MonkeyPatch
) -> None:
    caplog.set_level(logging.INFO)
    empty = tmp_path / "empty"
    empty.mkdir()
    monkeypatch.delenv("PLATFORM_ROOT", raising=False)
    monkeypatch.delenv("PLATFORM_REMOTE_BASE", raising=False)
    monkeypatch.setenv("HOME", str(tmp_path))

    with pytest.raises(ConfigNotFoundError) as excinfo:
        node_resolver.resolve_node_yaml("nonexistent-127-w2", platform_root=str(empty))

    assert "node.yaml not found for node=nonexistent-127-w2" in str(excinfo.value), str(excinfo.value)
    assert any("node.yaml not found" in r.message for r in caplog.records), caplog.text
    assert_ldd_imp9(caplog)


# endregion FUNC_test_resolve_not_found_readable_error


# region FUNC_test_resolve_idempotent_same_path
# 🧪 TRAP[TEST] · 2026-08-04 · REGRESSION · W2 идемпотентность: повторный резолв = тот же путь
# · Scenario: resolve дважды → идентичный абсолютный путь
# · Last fail: N/A (preventive)
# · Remove if: резолв перестанет быть детерминированным
def test_resolve_idempotent_same_path(
    tmp_path: Path, caplog: pytest.LogCaptureFixture, monkeypatch: pytest.MonkeyPatch
) -> None:
    caplog.set_level(logging.INFO)
    platform_root = tmp_path / "platform"
    node_yaml = _make_node_yaml(platform_root, "idemnode")
    monkeypatch.delenv("PLATFORM_ROOT", raising=False)

    first = node_resolver.resolve_node_yaml("idemnode", platform_root=str(platform_root))
    second = node_resolver.resolve_node_yaml("idemnode", platform_root=str(platform_root))

    assert first == second == str(node_yaml)
    assert_ldd_imp9(caplog)


# endregion FUNC_test_resolve_idempotent_same_path


# ── extract_node_host ──────────────────────────────────────────────────────────


# region FUNC_test_extract_node_host
# 🧪 TRAP[TEST] · 2026-08-04 · REGRESSION · W2 host-извлечение из node.yaml
# · Scenario: node.host: 1.2.3.4 → "1.2.3.4"; IMP:9 "Extracted host: 1.2.3.4"
# · Last fail: N/A (preventive)
# · Remove if: формат host-извлечения изменён
def test_extract_node_host(tmp_path: Path, caplog: pytest.LogCaptureFixture) -> None:
    caplog.set_level(logging.INFO)
    yaml_file = tmp_path / "node.yaml"
    yaml_file.write_text("node:\n  host: 1.2.3.4\n")

    host = node_resolver.extract_node_host(str(yaml_file))

    assert host == "1.2.3.4", host
    assert any("Extracted host: 1.2.3.4" in r.message for r in caplog.records), caplog.text
    assert_ldd_imp9(caplog)


# endregion FUNC_test_extract_node_host


# region FUNC_test_extract_node_host_missing
# 🧪 TRAP[TEST] · 2026-08-04 · REGRESSION · W2 отсутствующий host → "" (не ошибка)
# · Scenario: node.yaml без node.host → ""; exit-семантика shell: empty, не ошибка
# · Last fail: N/A (preventive)
# · Remove if: поведение для отсутствующего поля изменено
def test_extract_node_host_missing(tmp_path: Path, caplog: pytest.LogCaptureFixture) -> None:
    caplog.set_level(logging.INFO)
    yaml_file = tmp_path / "node.yaml"
    yaml_file.write_text("other:\n  key: value\n")

    host = node_resolver.extract_node_host(str(yaml_file))

    assert not host, repr(host)
    assert any("No host field in node.yaml" in r.message for r in caplog.records), caplog.text
    assert_ldd_imp9(caplog)


# endregion FUNC_test_extract_node_host_missing


# region FUNC_test_extract_node_host_bool_normalized
# 🧪 TRAP[TEST] · 2026-08-04 · REGRESSION · W2 T6-нормализация: bool → lowercase
# · Scenario: host: true → "true" (НЕ "True") — parity с node_yaml --get (DevPlan 123 T6)
# · Last fail: N/A (preventive — канон _format_cli_value)
# · Remove if: нормализация CLI-вывода изменена
def test_extract_node_host_bool_normalized(tmp_path: Path, caplog: pytest.LogCaptureFixture) -> None:
    caplog.set_level(logging.INFO)
    yaml_file = tmp_path / "node.yaml"
    yaml_file.write_text("node:\n  host: true\n")

    host = node_resolver.extract_node_host(str(yaml_file))

    assert host == "true", repr(host)
    assert_ldd_imp9(caplog)


# endregion FUNC_test_extract_node_host_bool_normalized


# region FUNC_test_extract_node_host_file_missing
# 🧪 TRAP[TEST] · 2026-08-04 · REGRESSION · W2 missing file → ConfigNotFoundError (CLI exit 1)
# · Scenario: несуществующий путь → ConfigNotFoundError
# · Last fail: N/A (preventive)
# · Remove if: семантика missing file изменена
def test_extract_node_host_file_missing(tmp_path: Path, caplog: pytest.LogCaptureFixture) -> None:
    caplog.set_level(logging.INFO)
    missing = tmp_path / "no-such-node.yaml"

    with pytest.raises(ConfigNotFoundError):
        node_resolver.extract_node_host(str(missing))
    assert any("node.yaml not found" in r.message for r in caplog.records), caplog.text
    assert_ldd_imp9(caplog)


# endregion FUNC_test_extract_node_host_file_missing


# region FUNC_test_extract_node_host_parse_error
# 🧪 TRAP[TEST] · 2026-08-04 · REGRESSION · W2 битый YAML → ConfigParseError (CLI exit 1)
# · Scenario: невалидный YAML → ConfigParseError
# · Last fail: N/A (preventive)
# · Remove if: семантика parse-ошибки изменена
def test_extract_node_host_parse_error(tmp_path: Path, caplog: pytest.LogCaptureFixture) -> None:
    caplog.set_level(logging.INFO)
    yaml_file = tmp_path / "broken.yaml"
    yaml_file.write_text("node: [unclosed\n")

    with pytest.raises(ConfigParseError):
        node_resolver.extract_node_host(str(yaml_file))
    assert_ldd_imp9(caplog)


# endregion FUNC_test_extract_node_host_parse_error


# ── CLI exit-контракт ──────────────────────────────────────────────────────────


# region FUNC_test_cli_resolve_prints_path
# 🧪 TRAP[TEST] · 2026-08-04 · REGRESSION · W2 CLI resolve: stdout одна строка + exit 0
# · Scenario: main(["resolve","--node",n,"--platform-root",p]) == 0; stdout == path + "\n"
# · Last fail: N/A (preventive — stdout контракт для shell $())
# · Remove if: CLI-контракт изменён
def test_cli_resolve_prints_path(
    tmp_path: Path, caplog: pytest.LogCaptureFixture, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture
) -> None:
    caplog.set_level(logging.INFO)
    platform_root = tmp_path / "platform"
    node_yaml = _make_node_yaml(platform_root, "clinode")
    monkeypatch.delenv("PLATFORM_ROOT", raising=False)

    rc = node_resolver.main(["resolve", "--node", "clinode", "--platform-root", str(platform_root)])

    assert rc == 0, rc
    assert capsys.readouterr().out == f"{node_yaml}\n", capsys.readouterr().out
    assert_ldd_imp9(caplog)


# endregion FUNC_test_cli_resolve_prints_path


# region FUNC_test_cli_resolve_not_found_exit1
# 🧪 TRAP[TEST] · 2026-08-04 · REGRESSION · W2 CLI resolve not-found: exit 1 + читаемая ошибка
# · Scenario: пустой platform_root → exit 1; stderr содержит "node.yaml not found for node="
# · Last fail: N/A (preventive — shell || return 1 совместимость)
# · Remove if: CLI exit-контракт изменён
def test_cli_resolve_not_found_exit1(
    tmp_path: Path, caplog: pytest.LogCaptureFixture, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture
) -> None:
    caplog.set_level(logging.INFO)
    empty = tmp_path / "empty"
    empty.mkdir()
    monkeypatch.delenv("PLATFORM_ROOT", raising=False)
    monkeypatch.delenv("PLATFORM_REMOTE_BASE", raising=False)
    monkeypatch.setenv("HOME", str(tmp_path))

    rc = node_resolver.main(["resolve", "--node", "missing-127-w2", "--platform-root", str(empty)])

    assert rc == 1, rc
    err = capsys.readouterr().err
    assert "node.yaml not found for node=missing-127-w2" in err, err
    assert_ldd_imp9(caplog)


# endregion FUNC_test_cli_resolve_not_found_exit1


# region FUNC_test_cli_host_extracts
# 🧪 TRAP[TEST] · 2026-08-04 · REGRESSION · W2 CLI host: stdout host + exit 0
# · Scenario: main(["host","--file",yaml]) == 0; stdout "1.2.3.4\n"
# · Last fail: N/A (preventive)
# · Remove if: CLI-контракт изменён
def test_cli_host_extracts(tmp_path: Path, caplog: pytest.LogCaptureFixture, capsys: pytest.CaptureFixture) -> None:
    caplog.set_level(logging.INFO)
    yaml_file = tmp_path / "node.yaml"
    yaml_file.write_text("node:\n  host: 1.2.3.4\n")

    rc = node_resolver.main(["host", "--file", str(yaml_file)])

    assert rc == 0, rc
    assert capsys.readouterr().out == "1.2.3.4\n"
    assert_ldd_imp9(caplog)


# endregion FUNC_test_cli_host_extracts


# region FUNC_test_cli_host_missing_file_exit1
# 🧪 TRAP[TEST] · 2026-08-04 · REGRESSION · W2 CLI host missing file: exit 1 (shell || return 1)
# · Scenario: --file не существует → exit 1
# · Last fail: N/A (preventive)
# · Remove if: CLI exit-контракт изменён
def test_cli_host_missing_file_exit1(
    tmp_path: Path, caplog: pytest.LogCaptureFixture, capsys: pytest.CaptureFixture
) -> None:
    caplog.set_level(logging.INFO)
    missing = tmp_path / "no-such.yaml"

    rc = node_resolver.main(["host", "--file", str(missing)])

    assert rc == 1, rc
    assert "node.yaml" in capsys.readouterr().err
    assert_ldd_imp9(caplog)


# endregion FUNC_test_cli_host_missing_file_exit1


# region FUNC_test_cli_resolve_idempotent
# 🧪 TRAP[TEST] · 2026-08-04 · REGRESSION · W2 CLI идемпотентность: два resolve = один stdout
# · Scenario: main resolve дважды → оба exit 0, одинаковый stdout
# · Last fail: N/A (preventive)
# · Remove if: резолв перестанет быть детерминированным
def test_cli_resolve_idempotent(
    tmp_path: Path, caplog: pytest.LogCaptureFixture, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture
) -> None:
    caplog.set_level(logging.INFO)
    platform_root = tmp_path / "platform"
    node_yaml = _make_node_yaml(platform_root, "idemcli")
    monkeypatch.delenv("PLATFORM_ROOT", raising=False)
    args = ["resolve", "--node", "idemcli", "--platform-root", str(platform_root)]

    rc1 = node_resolver.main(args)
    out1 = capsys.readouterr().out
    rc2 = node_resolver.main(args)
    out2 = capsys.readouterr().out

    assert rc1 == rc2 == 0
    assert out1 == out2 == f"{node_yaml}\n"
    assert_ldd_imp9(caplog)


# endregion FUNC_test_cli_resolve_idempotent
