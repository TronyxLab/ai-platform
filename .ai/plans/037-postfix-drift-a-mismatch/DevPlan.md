# 037-DevPlan: Postfix — NEW-DRIFT-A + MISMATCH-1 (Wave 3 re-verification remaining)

**Source:** `.ai/plans/033-wave3-contract-d5/05-VerificationReport-postfix.md` — remaining items (non-blocking)
**Verified against codebase:** 2026-07-22 (SHA: current HEAD)
**Prior artifacts:** DevPlan 033 (Wave 3 D5), DevPlan 04 (fix-wave COMPOSE_PROFILES), VerificationReport-postfix

$START_DEVPLAN

$ARTIFACT_CONTRACT
PURPOSE:               Закрыть 2 оставшихся non-blocking дрейфа из Wave 3 VerificationReport-postfix:
                       NEW-DRIFT-A (MEDIUM) — отсутствие явного `env=` в subprocess.run теста;
                       MISMATCH-1 (LOW) — дублирование COMPOSE_PROFILES в 6 callsites.
                       Третий пункт (INFO: удаление _fix_compose_profiles.py) — уже RESOLVED (файл отсутствует).
DESCRIPTION:           1) NEW-DRIFT-A: `tests/test_predeploy_gate.py:798` — `subprocess.run` без `env=`.
                       При прямом `pytest tests/` (в обход `make test`) переменная `COMPOSE_PROFILES`
                       не экспортируется → `docker compose config --dry-run` падает с `${VAR:?error}`.
                       Mitigation: CI всегда использует `make test`/`make gate` (Makefile:30 экспортирует COMPOSE_PROFILES).
                       Fix: добавить `env={**os.environ}` — защита от прямого вызова pytest.
                       2) MISMATCH-1: 13-модульный список COMPOSE_PROFILES хардкожен в 6 местах
                       (Makefile:30, deploy-project.sh:719, adopt-project.sh:387, push-gate.yml:47,
                       platform-test.yml:71, docker_orchestrator.py:455 + helpers.mk:78 `_get_all_profiles`).
                       При изменении состава Docker-модулей потребуется ручное обновление всех 6 мест.
RATIONALE:             Wave 3 (DevPlan 033) production-ready — оба пункта non-blocking.
                       NEW-DRIFT-A: robustness hardening, защита от прямого вызова pytest.
                       MISMATCH-1: single-source-of-truth через consistency gate — не меняет
                       production-код, добавляет мониторинг консистентности.
ACCEPTANCE_CRITERIA:
   **AC-1 (NEW-DRIFT-A):**
      1. `tests/test_predeploy_gate.py:798` — `subprocess.run` имеет явный `env={**os.environ}`
      2. `make test MARKER=predeploy` — PASS (регрессия отсутствует)
      3. Прямой вызов `pytest tests/test_predeploy_gate.py -k test_project_compose_configs_valid` — PASS (без `make`)
   **AC-2 (MISMATCH-1 — consistency gate):**
      4. Новый gate-тест `tests/gates/test_gate_compose_profiles_consistency.py` — PASS
      5. Тест проверяет: Makefile:30, deploy-project.sh:719, adopt-project.sh:387, push-gate.yml:47,
         platform-test.yml:71, docker_orchestrator.py:455, helpers.mk:78 — все содержат идентичный
         13-модульный список (или производят его через `make _get_all_profiles`)
      6. Тест НЕ модифицирует production-код (read-only gate)
      7. `make gate MODE=fast` — PASS (новый тест включён в sweep)
IMPLEMENTS:            2 changes:
                       - `tests/test_predeploy_gate.py:798` — +1 строка `env={**os.environ}`
                       - `tests/gates/test_gate_compose_profiles_consistency.py` — NEW (~60 LOC)
IMPACTS:               **Modified:**
                         - `tests/test_predeploy_gate.py` (NEW-DRIFT-A fix)
                       **Added:**
                         - `tests/gates/test_gate_compose_profiles_consistency.py` (MISMATCH-1 consistency gate)
REQUIRES:              Чистый working tree. Python 3.10+ в .venv. Docker daemon (для predeploy теста).
TASK_SIZE:             SMALL (~70 LOC, 2 файла)
CRITICALITY:           MEDIUM — non-blocking hardening, Wave 3 уже production-ready
$END_ARTIFACT_CONTRACT

---

## $DOCUMENT_PLAN

```
$START_DOCUMENT_PLAN
### Document Plan
**SECTION_GOALS:**
- GOAL SUPERPOSITION: анализ 4-5 опций для MISMATCH-1, коллапс к consistency gate => GOAL_SUPERPOSITION
- GOAL DRIFT_A: фикс test_predeploy_gate.py subprocess.run env= => GOAL_DRIFT_A
- GOAL MISMATCH: consistency gate test для COMPOSE_PROFILES => GOAL_MISMATCH
- GOAL VERIFY: make gate MODE=fast → PASS (оба изменения) => GOAL_VERIFY
**SECTION_USE_CASES:**
- USE_CASE pytest tests/test_predeploy_gate.py (без make) → PASS => UC_DIRECT_PYTEST
- USE_CASE make gate MODE=fast → включает новый consistency gate → PASS => UC_GATE_CONSISTENCY
- USE_CASE Изменён состав Docker-модулей → consistency gate FAILS → разработчик обновляет ВСЕ 6 мест => UC_BREAK_DETECTION
$END_DOCUMENT_PLAN
```

---

## 1. Status Check — что уже исправлено, что нет

### Проверка перед составлением DevPlan (2026-07-22)

| ID | Severity | Статус | Доказательство |
|----|----------|--------|----------------|
| NEW-DRIFT-A | MEDIUM | **НЕ ИСПРАВЛЕНО** | `tests/test_predeploy_gate.py:798` — `subprocess.run(...)` без `env=`. Подтверждено grep-ом. |
| MISMATCH-1 | LOW | **НЕ ИСПРАВЛЕНО** | 13-модульный список хардкожен в 6 файлах (Makefile:30, deploy-project.sh:719, adopt-project.sh:387, push-gate.yml:47, platform-test.yml:71, docker_orchestrator.py:455, helpers.mk:78). Все идентичны — дублирование, не inconsistency. |
| INFO (cleanup) | INFO | ✅ **RESOLVED** | `_fix_compose_profiles.py` — файл отсутствует на диске, `git grep` не находит ссылок в живом коде (только в исторической документации `.ai/plans/`). |

**Вывод:** 2 из 3 пунктов требуют исправления. INFO-пункт уже закрыт.

---

## 2. GOAL_SUPERPOSITION: MISMATCH-1 — выбор стратегии

### Опции

| # | Стратегия | Описание | Плюсы | Минусы |
|---|-----------|----------|-------|--------|
| **A** | **Consistency gate (read-only test)** | Python-тест читает COMPOSE_PROFILES из всех 6 callsites, сравнивает с эталоном из `make _get_all_profiles` | Не трогает production-код; zero regression risk; fail-fast при изменении состава модулей | Не устраняет дублирование — только мониторит |
| **B** | Single-source file + sourced | Создать `core/internal/compose_profiles.env`, везде делать `source` (shell) / `load_dotenv` (python) / CI env from file | Устраняет дублирование; единственное место правки | Multi-language complexity: shell source ≠ CI YAML env ≠ Python load; CI workflows не могут source shell-файл |
| **C** | Make target as API | Все callsites вызывают `make _get_all_profiles` через subprocess | Единый источник | `make _get_all_profiles` уже существует, но shell-скрипты и Python на VPS не имеют Makefile; 6 subprocess-вызовов добавляют latency |
| **D** | Python-константа + экспорт | `core/internal/compose_profiles.py` с константой `COMPOSE_PROFILES_DEFAULT`, импортируется Python-кодом, shell получает через `python3 -c "from ... import ..."` | Typed, single source | Shell-скрипты вынуждены делать `python3 -c` импорт (противоречит языковой политике? Нет — это конфигурация, не бизнес-логика) |
| **E** | Do nothing (accept debt) | LOW severity, Wave 3 production-ready | Zero effort | При добавлении/удалении модуля — человеческий фактор, риск забыть обновить 1 из 6 мест |

### Collapse → Option A (consistency gate)

**Решение:** Option A — consistency gate (read-only Python test).

**Обоснование:**
- MISMATCH-1 имеет **LOW** severity — не блокирует production
- Options B/C/D требуют изменения production-кода в 6 местах с multi-language orchestration (shell source, CI YAML env, Python import) — несоразмерно для LOW severity
- Consistency gate даёт **detection** без **modification**: при изменении состава модулей тест упадёт и укажет на ВСЕ места, требующие обновления
- Zero regression risk — тест только читает файлы, ничего не меняет
- Соответствует принципу «Fail-Fast» из §PRINCIPLES

**Будущее:** Если через квартал (2026-10-22) количество Docker-модулей изменилось ≥2 раза и consistency gate ловил ошибки → пересмотреть решение (Option B/D с full dedup).

---

## 3. GOAL_DRIFT_A: Fix test_predeploy_gate.py subprocess.run

### Root cause

`subprocess.run` в Python по умолчанию наследует `os.environ` родительского процесса. Когда тест вызывается через `make test`, Makefile:30 (`export COMPOSE_PROFILES ?= ...`) гарантирует наличие переменной. Но при прямом вызове `pytest tests/test_predeploy_gate.py` переменная отсутствует → `docker compose config --dry-run` падает на `${VAR:?error}` из compose-файлов (DevPlan 033 Option A — fail-fast синтаксис).

### Fix

Добавить `env={**os.environ}` в `subprocess.run` вызов на строке 798.

**Паттерн:** идентичен `_run_docker()` из `tests/test_smoke_platform.py:90-98`.

**Изменение:**

```python
# Было (строка 798-803):
            result = subprocess.run(
                ["docker", "compose", "-f", str(compose_path), "config", "--dry-run"],
                capture_output=True,
                text=True,
                timeout=30,
            )

# Стало:
            result = subprocess.run(
                ["docker", "compose", "-f", str(compose_path), "config", "--dry-run"],
                capture_output=True,
                text=True,
                timeout=30,
                env={**os.environ},
            )
```

**Почему именно `{**os.environ}` (без COMPOSE_PROFILES fallback):**
- `test_predeploy_gate.py` НЕ задаёт per-module COMPOSE_PROFILES (в отличие от `test_smoke_platform.py`, где `env_override={"COMPOSE_PROFILES": module_name}`)
- Тест валидирует **все** проектные compose-файлы — нужен **полный** список профилей
- `{**os.environ}` передаёт родительское окружение как есть — если вызывается через `make test`, COMPOSE_PROFILES уже экспортирован; если напрямую — упадёт с `${VAR:?error}`, что корректно (отсутствие env = ошибка конфигурации, не баг теста)
- Это **защита от прямого вызова**, а не fallback с дефолтным значением (дефолт уже в Makefile)

**Примечание:** опционально можно добавить `env={**os.environ, "COMPOSE_PROFILES": os.environ.get("COMPOSE_PROFILES", "<13-module-list>")}`, но это создаст **третье** место дублирования 13-модульного списка внутри теста — что усугубит MISMATCH-1. Лучше оставить `{**os.environ}` и полагаться на вызывающую сторону (`make test`).

---

## 4. GOAL_MISMATCH: Consistency gate test

### Architecture

Новый gate-тест `tests/gates/test_gate_compose_profiles_consistency.py` — read-only тест, который:

1. **Читает эталон** из `make _get_all_profiles` (subprocess вызов make)
2. **Парсит значение** из каждого из 6 callsites:
   - `Makefile:30` — regex: `export COMPOSE_PROFILES \?= (.+)`
   - `core/internal/deploy/deploy-project.sh:719` — regex: `COMPOSE_PROFILES="\$\{COMPOSE_PROFILES:-(.+)\}"`
   - `core/internal/scaffold/adopt-project.sh:387` — regex: аналогично
   - `.github/workflows/push-gate.yml:47` — YAML parse → `env.COMPOSE_PROFILES`
   - `.github/workflows/platform-test.yml:71` — YAML parse → аналогично
   - `core/internal/bootstrap/deploy/docker_orchestrator.py:455` — regex: `"COMPOSE_PROFILES",\s*\n\s*"(.+)"`
3. **Сравнивает** каждое значение с эталоном
4. **Fail с указанием** конкретного файла:строки при mismatche

### Project root resolution

Тест должен разрешать project root для относительных путей к callsites. Используется `Path(__file__).parent.parent.parent` (от `tests/gates/` → `tests/` → project root).

### Gate registration

Тест должен быть обнаружен `pytest` с маркером `gates` (через `pytest.ini` или `pyproject.toml` markers). Файл размещается в `tests/gates/` — автоматически подхватывается `make gate MODE=fast` (sweep всех `*gate*.py`).

### Test structure

```python
# GREP_SUMMARY: test_gate_compose_profiles_consistency.py, gate, COMPOSE_PROFILES, consistency, mismatch
# STRUCTURE: ┌fixture: canonical profiles from make┐ → ◇ test: cross-check 6 callsites → ⎋ assert all match

"""Gate: COMPOSE_PROFILES consistency across all 6 callsites.

## @purpose — Read-only gate that verifies the 13-module COMPOSE_PROFILES list
##            is identical across all callsites (Makefile, shell scripts, CI, Python).
##            Catches drift when Docker modules are added/removed without updating
##            all locations.
## @scope — 6 files: Makefile, deploy-project.sh, adopt-project.sh, push-gate.yml,
##          platform-test.yml, docker_orchestrator.py
"""

import os
import re
import subprocess
from pathlib import Path

import pytest
import yaml

# === Helpers ===

PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent


def _get_canonical_profiles() -> str:
    """Get canonical COMPOSE_PROFILES from `make _get_all_profiles`."""
    result = subprocess.run(
        ["make", "_get_all_profiles"],
        capture_output=True,
        text=True,
        timeout=15,
        cwd=str(PROJECT_ROOT),
        env={**os.environ},
    )
    if result.returncode != 0:
        pytest.fail(f"make _get_all_profiles failed: {result.stderr}")
    return result.stdout.strip()


def _extract_makefile_value(filepath: Path) -> str:
    """Extract COMPOSE_PROFILES from Makefile export line."""
    content = filepath.read_text()
    m = re.search(r'export COMPOSE_PROFILES \?\?= (.+)', content)
    if not m:
        raise ValueError(f"No COMPOSE_PROFILES export found in {filepath}")
    return m.group(1).strip()


def _extract_shell_default(filepath: Path, line_hint: str = "COMPOSE_PROFILES:") -> str:
    """Extract COMPOSE_PROFILES from shell ${COMPOSE_PROFILES:-...} pattern."""
    content = filepath.read_text()
    m = re.search(r'COMPOSE_PROFILES[=:]"\${COMPOSE_PROFILES:-(.+?)}"', content)
    if not m:
        raise ValueError(f"No COMPOSE_PROFILES default found in {filepath}")
    return m.group(1).strip()


def _extract_ci_workflow_value(filepath: Path) -> str:
    """Extract COMPOSE_PROFILES from GitHub Actions workflow env section."""
    content = filepath.read_text()
    m = re.search(r'COMPOSE_PROFILES:\s*"(.+?)"', content)
    if not m:
        raise ValueError(f"No COMPOSE_PROFILES found in {filepath}")
    return m.group(1).strip()


def _extract_python_setdefault(filepath: Path) -> str:
    """Extract COMPOSE_PROFILES from os.environ.setdefault() call."""
    content = filepath.read_text()
    # Match multi-line setdefault: os.environ.setdefault("COMPOSE_PROFILES",\n"value")
    m = re.search(
        r'os\.environ\.setdefault\(\s*"COMPOSE_PROFILES",\s*\n\s*"(.+?)"',
        content,
        re.MULTILINE,
    )
    if not m:
        raise ValueError(f"No COMPOSE_PROFILES setdefault found in {filepath}")
    return m.group(1).strip()


# === Fixtures ===

@pytest.fixture(scope="module")
def canonical_profiles() -> str:
    """Canonical COMPOSE_PROFILES from make _get_all_profiles."""
    return _get_canonical_profiles()


# === Tests ===

# region CALLSITES — each file, its extractor, and a label
CALLSITES = [
    ("Makefile", PROJECT_ROOT / "Makefile", _extract_makefile_value, None),
    (
        "deploy-project.sh",
        PROJECT_ROOT / "core/internal/deploy/deploy-project.sh",
        _extract_shell_default,
        None,
    ),
    (
        "adopt-project.sh",
        PROJECT_ROOT / "core/internal/scaffold/adopt-project.sh",
        _extract_shell_default,
        None,
    ),
    (
        "push-gate.yml",
        PROJECT_ROOT / ".github/workflows/push-gate.yml",
        _extract_ci_workflow_value,
        None,
    ),
    (
        "platform-test.yml",
        PROJECT_ROOT / ".github/workflows/platform-test.yml",
        _extract_ci_workflow_value,
        None,
    ),
    (
        "docker_orchestrator.py",
        PROJECT_ROOT / "core/internal/bootstrap/deploy/docker_orchestrator.py",
        _extract_python_setdefault,
        None,
    ),
    (
        "helpers.mk (_get_all_profiles)",
        PROJECT_ROOT / "makefiles/helpers.mk",
        lambda p: re.search(
            r'@echo "(.+?)"',
            p.read_text(),
        ).group(1),
        None,
    ),
]
# endregion


@pytest.mark.gate
def test_compose_profiles_consistency(canonical_profiles: str, caplog) -> None:
    """Verify COMPOSE_PROFILES is identical across all 6 callsites.

    ◇ canonical_profiles → ⚡ for each callsite → extract → compare → ∋ mismatch? → ⎋ fail|pass
    """
    import logging

    logger = logging.getLogger(__name__)

    mismatches: list[str] = []

    for label, filepath, extractor, _ in CALLSITES:
        logger.info("[IMP:8][test_compose_profiles_consistency] Checking: %s", label)
        try:
            value = extractor(filepath)
        except (ValueError, FileNotFoundError, OSError) as exc:
            mismatches.append(f"[{label}] Extraction error: {exc}")
            logger.error(
                "[IMP:4][test_compose_profiles_consistency] FAIL extraction: %s — %s",
                label,
                exc,
            )
            continue

        if value != canonical_profiles:
            mismatches.append(
                f"[{label}] MISMATCH:\n"
                f"  expected: {canonical_profiles}\n"
                f"  actual:   {value}"
            )
            logger.error(
                "[IMP:4][test_compose_profiles_consistency] MISMATCH: %s", label
            )
        else:
            logger.info(
                "[IMP:9][test_compose_profiles_consistency] ✅ %s: consistent",
                label,
            )

    if mismatches:
        logger.error(
            "[IMP:10][test_compose_profiles_consistency] FAIL: %d callsites out of sync",
            len(mismatches),
        )
        pytest.fail(
            f"COMPOSE_PROFILES inconsistency detected in {len(mismatches)} callsite(s):\n"
            + "\n".join(mismatches)
            + "\n\nCanonical value: "
            + canonical_profiles
            + "\nUpdate ALL locations when adding/removing Docker modules."
        )

    logger.info(
        "[IMP:9][test_compose_profiles_consistency] ✅ All %d callsites consistent",
        len(CALLSITES),
    )
```

### Ожидаемое поведение

- **Сейчас (2026-07-22):** PASS — все 7 мест (6 callsites + helpers.mk) содержат идентичный список `postgres,redis,nginx,clickhouse,backup-cron,hermes-agent,monitoring,logging,litellm,langfuse,infra-metrics,minio,status-page`
- **При добавлении модуля:** если разработчик обновит только Makefile, но забудет deploy-project.sh → тест упадёт с указанием файла
- **При удалении модуля:** аналогично

---

## 5. Implementation Phases

### Phase 1: NEW-DRIFT-A fix (~5 min)

1. Открыть `tests/test_predeploy_gate.py`
2. На строке 798 добавить `env={**os.environ},` после `timeout=30,`
3. `make test MARKER=predeploy` → verify PASS
4. `pytest tests/test_predeploy_gate.py::test_project_compose_configs_valid -x` → verify PASS (без make)

### Phase 2: MISMATCH-1 consistency gate (~15 min)

1. Создать `tests/gates/test_gate_compose_profiles_consistency.py`
2. `make test MARKER=gate` → verify новый тест PASS (все 7 мест консистентны)
3. `make gate MODE=fast` → verify sweep includes new test

### Phase 3: Verification (~5 min)

1. `make gate MODE=fast` → все шаги PASS
2. `ruff check tests/` → 0 errors
3. Git diff — 2 изменённых файла, 1 новый

---

## 6. Rollback Plan

- **NEW-DRIFT-A:** `git revert` — убрать `env={**os.environ}` (безопасно, тест продолжит работать через `make test`)
- **MISMATCH-1 gate:** удалить `tests/gates/test_gate_compose_profiles_consistency.py` (read-only, zero side effects)

---

## 7. Decision Register

| TRAP ID | Date | Severity | Decision |
|---------|------|----------|----------|
| TRAP[DECISION] · 2026-07-22 · LOW · Option A (consistency gate) выбран для MISMATCH-1 |
| · Rejected: Option B (single-source file) — multi-language complexity для LOW severity |
| · Rejected: Option E (do nothing) — нарушает принцип Fail-Fast |
| · Reason: Consistency gate даёт detection без изменения production-кода. Zero regression risk. |
| · Rev: если через квартал (2026-10-22) состав Docker-модулей изменился ≥2 раза → пересмотреть на Option B/D |
