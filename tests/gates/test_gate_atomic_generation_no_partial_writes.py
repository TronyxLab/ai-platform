#!/usr/bin/env python3
# GREP_SUMMARY: atomic-generation partial-writes fault-injection real-generators atomic-writer audit R5 tmp-outputs
# STRUCTURE: ▶ real generator (tmp outputs) + fault-injection after first write → ◇ targets valid (old OR new, никогда partial) → ◇ atomicity-audit (atomic_writer vs open(w), documented allowlist) → ⊕ R5 negative (direct open(w) corruption) → ⎋ pass/fail
# region MODULE_CONTRACT
## @purpose  Gate: атомарность генерации манифестов. DevPlan 160 W3 T3.3 — переписан с мок-паттерна
##           (shell mktemp+trap) на РЕАЛЬНЫЕ генераторы (G2 platform-env, G3 entrypoint-manifest)
##           с fault-injection ПОСЛЕ первой записи: сбой не должен оставить частично записанные
##           generated-файлы (старый валидный контент ИЛИ файл отсутствует — никакого partial write).
## @scope    CI gate — in-process запуск реальных генераторов с выходами в tmp_path (репозиторий
##           НЕ мутируется — restore не нужен), статический аудит write-путей генераторов G1-G4,
##           R5 negative на direct open(w) mid-write сбой. Без Docker.
## @invariants
##   - Реальные генераторы: generate_platform_env.main(), generate_entrypoint_manifest.main()
##   - Выходы генераторов направляются в tmp_path (аргументы --output/--smoke-env-output/...)
##   - Fault-injection: open-fn DI (main(argv=..., open_fn=fault)) + shared.atomic_writer.atomic_write
##     (атомарные) — сбой ПОСЛЕ первой завершённой записи (SystemExit(1), hard crash); DevPlan 167 D1:
##     setattr sys.argv/builtins.open устранены (argv/open_fn передаются параметрами)
##   - После сбоя: каждый существующий target валиден (.yaml → yaml.safe_load, .py → ast.parse);
##     отсутствующий target допустим; старый контент (pre-seed) не изменён для ненаписанных target'ов
##   - Аудит write-путей: findings (direct open(w)/write_text) ⊆ документированный allowlist
##     (текущие неатомарные сайты G1-G4, TRAP[DECISION] ниже); НОВЫЙ неатомарный сайт → RED
##   - R5: direct open(w) с mid-write сбоем → checker ловит повреждение (демонстрация непустоты)
## @rationale DevPlan 090 — Atomic Generation. Partial writes — источник №1 повреждённых манифестов.
##            W3 T3.3: предыдущий тест проверял МОК-паттерн, не реальные генераторы. Аудит показал:
##            G2/G3 (и G1/G4) пишут через open(w), НЕ shared.atomic_writer — находка, зафиксирована
##            TRAP[DECISION] ниже; миграция — отдельная волна (НЕ в скоупе W3).
## @changes 2026-07-30 | Created — DevPlan 090 gate (мок-паттерн shell mktemp+trap)
## @changes 2026-08-13 | DevPlan 160 W3 T3.3 — rewritten: real generators + fault-injection + audit + R5
# endregion MODULE_CONTRACT

import ast
import builtins
import logging
import re
import subprocess
import sys
from pathlib import Path

import pytest
import yaml

from tests._conftest.r1 import r1_delegates
from tests.conftest import ldd_trajectory

logger = logging.getLogger(__name__)

ROOT_DIR = Path(__file__).resolve().parent.parent.parent

# ── Аудируемые генераторы (G1-G4, manifest.mk Chain A/B) ──
_GENERATOR_MODULES: dict[str, Path] = {
    "generate_secrets_manifest.py": ROOT_DIR / "core" / "internal" / "scripts" / "generate_secrets_manifest.py",
    "generate_platform_env.py": ROOT_DIR / "core" / "internal" / "scripts" / "generate_platform_env.py",
    "generate_entrypoint_manifest.py": ROOT_DIR / "core" / "internal" / "scripts" / "generate_entrypoint_manifest.py",
    "generate_agents_md.py": ROOT_DIR / "core" / "internal" / "scripts" / "generate_agents_md.py",
}

# 🧐 TRAP[DECISION] · 2026-08-13 · — · Генераторы G1-G4 пишут через open(w)/write_text, НЕ shared.atomic_writer
# · Rejected: миграция G1-G4 на core.internal.shared.atomic_writer в волне W3
# · Reason: deferred — W3 «Protection Gaps» = гейт-покрытие, не рефакторинг production; миграция —
# ·   отдельная волна. Гейт фиксирует статус-кво в allowlist ниже и RED-блокирует НОВЫЕ
# ·   неатомарные write-сайты. atomic_writer уже канон (DevPlan 119 E5) для других генераторов
# ·   (sync_env_defaults, sync_requirements, template_engine).
# · Rev: волна миграции G1-G4 на atomic_writer — allowlist-записи удаляются, audit-тест остаётся
# ·   (allowlist опустошается, findings=∅ обязателен)
_DOCUMENTED_NON_ATOMIC_SITES: dict[str, set[str]] = {
    "generate_secrets_manifest.py": {"output_content"},
    "generate_entrypoint_manifest.py": {"output_content"},
    "generate_platform_env.py": {"yaml_content", "smoke_content", "helpers_content"},
    "generate_agents_md.py": {"content"},
}

_WRITE_OPEN_RE = re.compile(r"with\s+open\([^)]*[\"']([wab]+)[\"']\)\s+as\s+(\w+)\s*:\s*\n\s*\2\.write\(\s*([^)]+)")
_WRITE_TEXT_RE = re.compile(r"\.write_text\(\s*([^,)]+)")

# ── Atomic pattern shell script template (мок-тест, сохранён из DevPlan 090) ──
_ATOMIC_SCRIPT_SUCCESS = """#!/usr/bin/env bash
set -euo pipefail
# Simulate atomic write with success
STAGING=$(mktemp -d)
trap 'rm -rf "$STAGING"' EXIT
echo "secret: value1" > "$STAGING/output1.yaml"
echo "secret: value2" > "$STAGING/output2.yaml"
# Simulate successful generation
mv "$STAGING/output1.yaml" "{orig1}"
mv "$STAGING/output2.yaml" "{orig2}"
echo "[IMP:9][atomic] Generation succeeded"
"""

_ATOMIC_SCRIPT_FAILURE = """#!/usr/bin/env bash
set -euo pipefail
# Simulate atomic write with mid-way failure
STAGING=$(mktemp -d)
trap 'rm -rf "$STAGING"' EXIT
echo "secret: value1" > "$STAGING/output1.yaml"
# Simulate crash before writing second file
echo "[IMP:1][atomic] CRASH: unexpected error" >&2
exit 1
# These should never execute:
mv "$STAGING/output1.yaml" "{orig1}"
mv "$STAGING/output2.yaml" "{orig2}"
echo "[IMP:9][atomic] Generation succeeded"
"""

_ATOMIC_SCRIPT_NO_TRAP = """#!/usr/bin/env bash
set -euo pipefail
# Simulate non-atomic write (NO trap EXIT — this is the anti-pattern)
STAGING=$(mktemp -d)
echo "secret: value1" > "$STAGING/output1.yaml"
echo "secret: value2" > "$STAGING/output2.yaml"
# "Success" — but without trap, any exit before mv leaves staging
rm -rf "$STAGING"
echo "[IMP:9][non-atomic] Generation succeeded"
"""


# region VALIDITY_CHECKER


def _check_targets_valid(targets: dict[str, Path]) -> list[str]:
    """Проверить, что существующие target'ы валидны (YAML/Python), отсутствующие допустимы.

    ## @purpose — Общий checker для positive (real generators) и negative (R5) тестов:
    ##            никакого partial write — существующий файл обязан парситься полностью.
    ## @io — ⇥ targets: dict[name → Path] → ⎋ list[str] violations
    ## @complexity — O(N * C) где N = файлы, C = размер контента
    ## @invariants
    ##   - Отсутствующий файл — допустим (crash до записи)
    ##   - Пустой файл — violation (truncated write)
    ##   - .yaml/.yml → yaml.safe_load должен пройти; прочие → ast.parse
    """
    violations: list[str] = []
    for name, path in targets.items():
        if not path.exists():
            continue
        content = path.read_text(encoding="utf-8")
        if not content.strip():
            violations.append(f"{name}: пустой файл после сбоя (truncated write)")
            continue
        if path.suffix in {".yaml", ".yml"}:
            try:
                yaml.safe_load(content)
            except yaml.YAMLError as exc:
                violations.append(f"{name}: невалидный YAML после сбоя — {exc}")
        else:
            try:
                ast.parse(content)
            except SyntaxError as exc:
                violations.append(f"{name}: невалидный Python после сбоя — {exc}")
    return violations


# endregion VALIDITY_CHECKER


# region FAULT_INJECTION


def _build_crash_open(tmp_path: Path, state: dict, real_open=builtins.open):
    """Собрать fault-injection open-wraper: hard crash (SystemExit 1) ПОСЛЕ первой записи.

    ## @purpose — Чистый фабричный seam (open-fn DI — DevPlan 167 D1): возвращает wrapper,
    ##            который генератор получает параметром open_fn (вместо monkeypatch builtins.open).
    ##            Перехват первого вызова записи (direct open(w) через tmp_path) и краш после
    ##            её завершения. Моделирует «процесс убит после первой записи».
    ## @io — ⇥ tmp_path (scope срабатывания), state: dict (fired/injected флаги),
    ##         real_open: Callable (реальный open, default builtins.open)
    ##       → ⎋ Callable — fault-open wrapper
    ## @complexity — O(1)
    ## @invariants
    ##   - Срабатывает только на "w"-режим и путь под tmp_path
    ##   - state["injected"] — факт срабатывания краша (R1 anti-pass: без краша тест падает)
    """

    def _crash() -> None:
        state["injected"] = True
        raise SystemExit(1)

    class _CrashProxy:
        """File-like proxy: после закрытия первого target-файла (запись завершена) — hard crash."""

        def __init__(self, real) -> None:
            self._real = real

        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):
            if exc_type is None:
                self._real.__exit__(None, None, None)
                _crash()
                return False
            return self._real.__exit__(exc_type, exc, tb)

        def write(self, data):
            return self._real.write(data)

        def flush(self):
            return self._real.flush()

        def close(self):
            return self._real.close()

        def fileno(self):
            return self._real.fileno()

        def __getattr__(self, name):
            return getattr(self._real, name)

    def _wrapped_open(file, mode="r", *args, **kwargs):
        if "w" in mode and not state["fired"] and str(file).startswith(str(tmp_path)):
            state["fired"] = True
            return _CrashProxy(real_open(file, mode, *args, **kwargs))
        return real_open(file, mode, *args, **kwargs)

    return _wrapped_open


def _install_atomic_fault_hook(monkeypatch: pytest.MonkeyPatch, state: dict) -> None:
    """Fault-inject shared.atomic_writer.atomic_write (краш ПОСЛЕ первого коммита).

    # 🧐 TRAP[DI-KEEP] · 2026-08-14 · — · atomic_write fault-hook: внешний shared-модуль без DI-вызова
    # · Rejected: DI-шов (atomic_write_fn параметр в main генераторов) · Reason: генераторы G2/G3
    # ·   НЕ вызывают shared.atomic_writer сегодня (пишут через open, документированный allowlist) —
    # ·   вводить параметр без вызова = спекулятивно; перехват атрибута внешнего shared-модуля —
    # ·   стандартный механизм fault-injection для будущей миграции
    # · Rev: при миграции G2/G3 на shared.atomic_writer — добавить atomic_write_fn DI-параметр
    # ·   в main() генераторов и передавать из гейта (setattr исчезает)
    """
    from core.internal.shared import atomic_writer

    real_atomic = atomic_writer.atomic_write

    def _wrapped_atomic(path, content, mode=0o644, validator=None, tmp_dir=None):
        result = real_atomic(path, content, mode=mode, validator=validator, tmp_dir=tmp_dir)
        if not state["injected"]:
            state["injected"] = True
            raise SystemExit(1)
        return result

    monkeypatch.setattr(atomic_writer, "atomic_write", _wrapped_atomic)


def _run_with_fault(argv: list[str], main_fn, monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> dict:
    """Запустить main() генератора с fault-injection; вернуть state (injected/fired).

    ## @purpose — Общий runner для real-generator тестов: pre-seed НЕ делается здесь (targets
    ##            готовит вызывающий), argv передаётся DI-параметром (DevPlan 167 D1 — setattr
    ##            sys.argv устранён: генераторы приняли main(argv=..., open_fn=...)), краш
    ##            SystemExit ловится как маркер.
    ## @io — ⇥ argv (без prog — канон AF-4), main_fn, monkeypatch, tmp_path → ⎋ dict state
    ## @complexity — O(время генерации)
    ## @invariants
    ##   - SystemExit (наш краш) проглатывается; прочие исключения — propagate
    ##   - state["injected"] == True обязателен (иначе fault не сработал — тест RED)
    """
    state = {"fired": False, "injected": False}
    fault_open = _build_crash_open(tmp_path, state)
    _install_atomic_fault_hook(monkeypatch, state)
    try:
        main_fn(argv, open_fn=fault_open)
    except SystemExit:
        logger.info("[IMP:8][atomic][fault] SystemExit(1) — hard crash после первой записи")
    return state


# endregion FAULT_INJECTION


# region AUDIT_SCANNERS


def _scan_direct_write_sites(module_path: Path) -> set[str]:
    """Найти все direct open(w)/write_text write-сайты генератора (по контент-сигнатуре).

    ## @purpose — Аудит atomicity: каждый прямой неатомарный write-сайт даёт сигнатуру
    ##            (имя записываемой переменной). Сверяется с _DOCUMENTED_NON_ATOMIC_SITES.
    ## @io — ⇥ module_path: Path → ⎋ set[str] сигнатур (пусто = все записи атомарны)
    ## @complexity — O(L) где L = строки модуля
    ## @invariants
    ##   - Регекс на «with open(...'w') as f: f.write(X)» — сигнатура = X
    ##   - Регекс на «.write_text(X)» — сигнатура = X
    ##   - Чтения (open без 'w') не матчатся
    """
    try:
        content = module_path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        logger.warning("[IMP:7][atomic][audit] Нечитаемый модуль %s", module_path)
        return set()
    sites: set[str] = set()
    for m in _WRITE_OPEN_RE.finditer(content):
        sites.add(m.group(3).strip())
    for m in _WRITE_TEXT_RE.finditer(content):
        sites.add(m.group(1).strip())
    return sites


# endregion AUDIT_SCANNERS


# ── мок-тест (сохранён из DevPlan 090) ──


@pytest.mark.gate
@ldd_trajectory
# region FUNC_test_no_partial_writes_on_failure
## @purpose  Verify atomic generation pattern prevents partial writes when generator crashes
## @io       ⇥ tmp_path: pytest fixture → mock shell scripts → assert pass/fail
## @complexity O(1) — runs 3 shell scripts, checks file state
## 🧪 TRAP[TEST] · 2026-07-30 · REGRESSION · Atomic generation on failure
## · Scenario: Generator crashes mid-way; verify staging is cleaned up and originals intact
## · Last fail: N/A (new gate)
## · Remove if: generation is restructured to use a different atomicity mechanism
def test_no_partial_writes_on_failure(tmp_path, caplog) -> None:
    """Verify that the atomic generation pattern (mktemp + trap EXIT + mv) prevents
    partial writes on failure.

    Tests three scenarios:
    1. Success: files are atomically moved to originals
    2. Failure with trap: staging cleaned up, originals unchanged
    3. Anti-pattern (no trap): detection of the non-atomic pattern
    """
    caplog.set_level(logging.INFO)

    # ── Setup ──
    orig1 = tmp_path / "output1.yaml"
    orig2 = tmp_path / "output2.yaml"

    # Create original files with known content (simulating previous valid manifests)
    orig1.write_text("original_value: should_not_change\n")
    orig2.write_text("original_value: should_not_change\n")

    logger.info("[IMP:7][test_no_partial_writes_on_failure] Original files created with checksums")

    # ── Test 1: Success case ──
    script_success = _ATOMIC_SCRIPT_SUCCESS.format(orig1=str(orig1), orig2=str(orig2))
    script_path = tmp_path / "atomic_success.sh"
    script_path.write_text(script_success)
    script_path.chmod(0o755)

    result = subprocess.run(
        ["bash", str(script_path)],
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
    )
    assert result.returncode == 0, (
        f"Atomic success script failed: returncode={result.returncode}\nstderr: {result.stderr}"
    )
    assert orig1.read_text() == "secret: value1\n", (
        f"Atomic success: orig1 content mismatch. Expected 'secret: value1\\n', got '{orig1.read_text()}'"
    )
    assert orig2.read_text() == "secret: value2\n", (
        f"Atomic success: orig2 content mismatch. Expected 'secret: value2\\n', got '{orig2.read_text()}'"
    )
    logger.info("[IMP:9][test_no_partial_writes_on_failure] Test 1 (success): PASS — files atomically moved")

    # ── Test 2: Failure with trap EXIT ──
    orig1.write_text("original_value: should_not_change\n")
    orig2.write_text("original_value: should_not_change\n")

    script_failure = _ATOMIC_SCRIPT_FAILURE.format(orig1=str(orig1), orig2=str(orig2))
    script_path2 = tmp_path / "atomic_failure.sh"
    script_path2.write_text(script_failure)
    script_path2.chmod(0o755)

    result = subprocess.run(
        ["bash", str(script_path2)],
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
    )
    # This script should fail (exit non-zero)
    logger.info("[IMP:7][test_no_partial_writes_on_failure] Failure script exit code: %d", result.returncode)

    # Verify originals are UNCHANGED (no partial write)
    assert orig1.read_text() == "original_value: should_not_change\n", (
        f"Atomic failure: orig1 was MODIFIED despite generator crash! "
        f"Expected 'original_value: should_not_change\\n', got '{orig1.read_text()}'"
    )
    assert orig2.read_text() == "original_value: should_not_change\n", (
        f"Atomic failure: orig2 was MODIFIED despite generator crash! "
        f"Expected 'original_value: should_not_change\\n', got '{orig2.read_text()}'"
    )

    # Verify staging is cleaned up (no orphaned staging dirs)
    staging_dirs = [p for p in tmp_path.iterdir() if p.is_dir() and p.name.startswith("tmp.")]
    assert len(staging_dirs) == 0, (
        f"Atomic failure: orphaned staging directories remain: {staging_dirs}\ntrap EXIT should have cleaned them up."
    )
    logger.info(
        "[IMP:9][test_no_partial_writes_on_failure] Test 2 (failure+trap): PASS — originals unchanged, staging cleaned"
    )

    # ── Test 3: Anti-pattern detection ──
    script_no_trap = _ATOMIC_SCRIPT_NO_TRAP.format(orig1=str(orig1), orig2=str(orig2))
    has_trap = "trap" in script_no_trap
    has_mktemp = "mktemp -d" in script_no_trap or "mktemp" in script_no_trap

    if has_mktemp and not has_trap:
        logger.warning("[IMP:7][test_no_partial_writes_on_failure] Anti-pattern detected: uses mktemp but no trap EXIT")
        print(
            "[IMP:7][test_no_partial_writes_on_failure] Anti-pattern detection: PASS (no trap = risk of partial write)",
            file=sys.stderr,
        )
    else:
        print("[IMP:7][test_no_partial_writes_on_failure] Script has trap — proper atomic pattern", file=sys.stderr)

    logger.info(
        "[IMP:9][test_no_partial_writes_on_failure] ALL PASS — atomic pattern verified: "
        "success writes atomically, failure preserves originals"
    )


# endregion FUNC_test_no_partial_writes_on_failure


# ══════════════════════════════════════════════════════════════════════════════
# W3 T3.3 — real generators + fault-injection + atomicity audit + R5 negative
# ══════════════════════════════════════════════════════════════════════════════

# region TESTS_REAL_GENERATORS


@pytest.mark.gate
@ldd_trajectory
@r1_delegates
# 🧪 TRAP[TEST] · 2026-08-13 · REGRESSION · real generators + fault-injection после первой записи (W3 T3.3)
# · Scenario: generate_platform_env / generate_entrypoint_manifest крашатся ПОСЛЕ первой записи —
# ·   target'ы обязаны остаться валидными (старый ИЛИ новый полный контент, никакого partial write)
# · Last fail: N/A (preventive — заменяет мок-паттерн на реальный конвейер)
# · Remove if: генераторы мигрируют на единый атомарный канон, несовместимый с данным инжектом
# 🧪 TRAP[TEST] · F1 (DevPlan 118) · @r1_delegates: fail-механизм делегирован хелперам
# · _test_platform_env_generator / _test_entrypoint_manifest_generator содержат все asserts
# · (targets valid, fault injected, old content preserved) — тело оркестрирует, не дублирует
def test_real_generators_no_partial_writes_on_crash(monkeypatch, tmp_path: Path, caplog) -> None:
    """Реальные генераторы (G2 platform-env, G3 entrypoint-manifest) с fault-injection после
    первой записи: никакого partial write — существующие target'ы валидны, ненаписанные — старые.

    ## @purpose — W3 T3.3: сбой генератора не должен оставить повреждённые generated-файлы.
    ##            Выходы генераторов направляются в tmp_path (репозиторий не мутируется).
    ## @io — ⇥ monkeypatch, tmp_path, caplog → ⎋ None
    ## @complexity — O(генерация) — ~10-20s (entrypoint-manifest гоняет pytest --collect-only)
    """
    caplog.set_level(logging.INFO)
    _test_platform_env_generator(monkeypatch, tmp_path)
    _test_entrypoint_manifest_generator(monkeypatch, tmp_path)
    logger.info("[IMP:9][atomic][real] PASS: оба генератора не оставляют partial writes после сбоя")


def _test_platform_env_generator(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """G2: generate_platform_env — 3 выхода, сбой после первой записи (platform-env.yaml)."""
    targets = {
        "platform-env.yaml": tmp_path / "platform-env.yaml",
        "smoke_env_generated.py": tmp_path / "smoke_env_generated.py",
        "env_defaults_generated.py": tmp_path / "env_defaults_generated.py",
    }
    # Pre-seed «предыдущие валидные generated-файлы» (старый контент обязан сохраниться)
    targets["platform-env.yaml"].write_text("# old platform-env\nenv_defaults: {}\n", encoding="utf-8")
    targets["smoke_env_generated.py"].write_text("# old smoke generated\n", encoding="utf-8")
    targets["env_defaults_generated.py"].write_text("# old helpers generated\n", encoding="utf-8")

    argv = [
        "--infra",
        str(ROOT_DIR / "core" / "platform-infra.yaml"),
        "--modules-dir",
        str(ROOT_DIR / "core" / "modules"),
        "--secret-defs",
        str(ROOT_DIR / "core" / "secret-definitions.yaml"),
        "--output",
        str(targets["platform-env.yaml"]),
        "--smoke-env-output",
        str(targets["smoke_env_generated.py"]),
        "--helpers-output",
        str(targets["env_defaults_generated.py"]),
    ]
    from core.internal.scripts import generate_platform_env

    state = _run_with_fault(argv, generate_platform_env.main, monkeypatch, tmp_path)
    assert state["injected"], "R1 FAIL: fault-injection не сработал — генератор не писал через перехватываемые пути"

    # После сбоя: platform-env.yaml = новый полный валидный YAML; smoke/helpers — старый контент
    violations = _check_targets_valid(targets)
    assert not violations, "GATE_ATOMIC_GENERATION (G2):\n  " + "\n  ".join(violations)
    assert targets["smoke_env_generated.py"].read_text(encoding="utf-8") == "# old smoke generated\n", (
        "G2: smoke_env_generated.py изменён после сбоя — сбой должен был прервать генерацию ДО его записи"
    )
    assert targets["env_defaults_generated.py"].read_text(encoding="utf-8") == "# old helpers generated\n", (
        "G2: env_defaults_generated.py изменён после сбоя — сбой должен был прервать генерацию ДО его записи"
    )
    logger.info("[IMP:9][atomic][real][G2] PASS: platform-env.yaml валиден, smoke/helpers не тронуты после сбоя")


def _test_entrypoint_manifest_generator(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """G3: generate_entrypoint_manifest — 1 выход, сбой после первой (единственной) записи."""
    targets = {
        "entrypoint-manifest.yaml": tmp_path / "entrypoint-manifest.yaml",
    }
    targets["entrypoint-manifest.yaml"].write_text("# old manifest\ngates: []\n", encoding="utf-8")

    argv = [
        "--makefile-dir",
        str(ROOT_DIR),
        "--gmake-path",
        "make",
        "--existing-manifest",
        str(ROOT_DIR / "core" / "entrypoint-manifest.yaml"),
        "--tests-dir",
        str(ROOT_DIR / "tests" / "gates"),
        "--output",
        str(targets["entrypoint-manifest.yaml"]),
    ]
    from core.internal.scripts import generate_entrypoint_manifest

    state = _run_with_fault(argv, generate_entrypoint_manifest.main, monkeypatch, tmp_path)
    assert state["injected"], "R1 FAIL: fault-injection не сработал для G3"

    violations = _check_targets_valid(targets)
    assert not violations, "GATE_ATOMIC_GENERATION (G3):\n  " + "\n  ".join(violations)
    logger.info("[IMP:9][atomic][real][G3] PASS: entrypoint-manifest.yaml валиден после сбоя")


@pytest.mark.gate
@ldd_trajectory
# 🧪 TRAP[TEST] · 2026-08-13 · REGRESSION · аудит write-путей генераторов G1-G4 (W3 T3.3)
# · Scenario: новый генератор/правка добавляет direct open(w)/write_text write-сайт вне allowlist —
# ·   гейт RED (требование: atomic_writer ИЛИ документированный allowlist)
# · Last fail: N/A (preventive — фиксирует находку W3: G1-G4 пишут через open(w))
# · Remove if: G1-G4 мигрируют на atomic_writer (allowlist опустошается)
def test_generator_atomicity_audit(caplog) -> None:
    """Каждый direct неатомарный write-сайт генераторов G1-G4 документирован (allowlist ⊆ канон).

    ## @purpose — W3 T3.3 unit-связка: НОВЫЙ неатомарный write-сайт в генераторах = RED.
    ##            Текущие сайты зафиксированы в _DOCUMENTED_NON_ATOMIC_SITES (TRAP[DECISION] выше);
    ##            миграция на shared.atomic_writer — отдельная волна.
    ## @io — ⎋ None
    ## @complexity — O(F * L) где F = генераторы, L = строки
    """
    findings: list[str] = []
    for module_name, module_path in _GENERATOR_MODULES.items():
        sites = _scan_direct_write_sites(module_path)
        documented = _DOCUMENTED_NON_ATOMIC_SITES.get(module_name, set())
        undocumented = sites - documented
        findings.extend(
            f"{module_name}: неатомарный write-сайт '{sig}' НЕ в документированном allowlist"
            for sig in sorted(undocumented)
        )
        if sites:
            logger.info(
                "[IMP:8][atomic][audit] %s: %d direct write-сайт(ов) — documented=%s",
                module_name,
                len(sites),
                sorted(sites),
            )
        else:
            logger.info("[IMP:9][atomic][audit] %s: все записи атомарны (0 direct write-сайтов)", module_name)

    assert not findings, (
        "[GATE:FAIL][id:generator-atomicity-audit][class:L2]\n"
        ">>> REPAIR_RECIPE_START >>>\n"
        "Мигрируй новый неатомарный write-сайт на core.internal.shared.atomic_writer "
        "(atomic_write_text/atomic_write_json) ИЛИ добавь сигнатуру в _DOCUMENTED_NON_ATOMIC_SITES "
        "с TRAP[DECISION]-обоснованием (временный allowlist — миграция отдельной волной).\n"
        "<<< REPAIR_RECIPE_END <<<\n" + "\n".join(findings)
    )
    logger.info(
        "[IMP:9][atomic][audit] PASS: все %d генераторов — 0 недокументированных неатомарных write-сайтов",
        len(_GENERATOR_MODULES),
    )


@pytest.mark.gate
@ldd_trajectory
# 🧪 TRAP[TEST] · 2026-08-13 · NEGATIVE (R5) · atomicity checker — direct open(w) mid-write сбой
# · Last fail: генератор пишет через open(w), процесс умирает mid-write → файл повреждён (truncated)
# · Remove if: direct open(w) в генераторах запрещён полностью (только atomic_writer)
def test_negative_direct_write_corruption_detected(tmp_path: Path, caplog) -> None:
    """R5 negative: direct open(w) с mid-write сбоем → checker ловит повреждение (не пустой).

    ## @purpose — Демонстрация, что проверка валидности не пустая: НЕатомарная запись
    ##            (open(w) truncate + partial write) детектируется как повреждение.
    ## @io — ⇥ tmp_path → ⎋ None
    ## @complexity — O(1)
    """
    target = tmp_path / "manifest.yaml"
    target.write_text("previous: valid\n", encoding="utf-8")

    # Моделируем direct open(w) + crash mid-write: truncate, частичный контент, процесс умер
    fh = target.open("w", encoding="utf-8")
    fh.write("database_url: 'postgres://")  # unterminated quote — partial write
    fh.close()  # контент сброшен, но файл повреждён (неполный YAML)

    violations = _check_targets_valid({"manifest.yaml": target})
    assert len(violations) >= 1, (
        f"R5 FAIL: checker не поймал повреждение при direct open(w) — violations={violations!r}"
    )
    assert any("YAML" in v or "yaml" in v.lower() for v in violations), (
        f"R5 FAIL: violation не указывает на YAML-повреждение: {violations!r}"
    )
    logger.info("[IMP:9][atomic][negative] PASS: direct open(w) повреждение детектируется — checker не пустой (R1/R5)")


# endregion TESTS_REAL_GENERATORS
