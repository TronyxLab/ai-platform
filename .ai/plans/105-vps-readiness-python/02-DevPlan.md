$START_DEVPLAN
# DevPlan 105 — vps-readiness.sh → Python (Strangler-Fig)

$ARTIFACT_CONTRACT
PURPOSE:               Миграция последнего lib-файла с бизнес-логикой в bash (vps-readiness.sh,
                       181 LOC) → Python-модуль `core/internal/shared/vps_readiness.py` +
                       исправление латентного бага `$first` в JSON-диагностике.
DESCRIPTION:           Вынос всей бизнес-логики 4 pre-flight проверок (SSH, forced-command ping,
                       /opt/projects/, Docker daemon) в Python-модуль. Shell-фасад (≤40 LOC)
                       сохраняет API `check_vps_ready <node> [--json|--quick]` для совместимости
                        с deploy.mk. SSH-вызовы — через subprocess-bash паттерн (прецедент:
                        core/internal/scaffold/project_lister.py:287,
                        core/internal/scaffold/project_remover.py:253) с DI ssh_runner для тестирования.
                       Латентный баг `$first || json_diag+=","` (строка 170 — bash пытается
                       выполнить `false` как команду) исправлен архитектурно: Python строит JSON
                       через структуры данных, а не строковую конкатенацию.
RATIONALE:             Последний lib-файл с бизнес-логикой не в Python. После миграции: 181→40 LOC
                       shell (−78%), латентный баг исправлен, модуль покрыт unit-тестами.
                       Языковая политика (root AGENTS.md): новый код только Python, bash — тонкие
                       фасады. Strangler-Fig: shell-фасад сохраняет вызов check_vps_ready для
                       deploy.mk без изменения вызывающего кода.
ACCEPTANCE_CRITERIA:   AC1: Python-модуль `core/internal/shared/vps_readiness.py` с check_vps_ready()
                            и всеми 4 проверками (SSH, forced-command ping, /opt/projects/, Docker)
                       AC2: Shell-фасад `core/lib/vps-readiness.sh` ≤40 LOC (source ssh.sh + вызов Python)
                       AC3: `check_vps_ready <node>` работает идентично (поведение не изменилось)
                       AC4: `check_vps_ready <node> --quick` работает идентично (Docker skip)
                       AC5: `check_vps_ready <node> --json` работает идентично (JSON diagnostics)
                       AC6: Латентный баг `$first` исправлен — JSON не содержит лишних запятых
                       AC7: Все remediation hints сохранены (по одному на каждый failure mode)
                       AC8: NODE_HOST_MAP резолвинг идентичен (JSON-парсинг, node→host lookup)
                       AC9: Unit-тесты на check_vps_ready с mock ssh_runner (LDD caplog IMP:9,
                            Test Honesty R1/R2, R5 ANTI-SURVIVORSHIP)
                       AC10: `make gate MODE=fast` зелёный (gate-тест vps_readiness_sourceable
                             адаптирован под shell-фасад)
IMPLEMENTS:            Brief 105 (`.ai/plans/105-vps-readiness-python/01-Brief.md`)
IMPACTS:
                       - `core/internal/shared/vps_readiness.py` (NEW) — Python-модуль с бизнес-логикой
                       - `core/lib/vps-readiness.sh` (MODIFY) — усечение до shell-фасада ≤40 LOC
                       - `tests/unit/test_vps_readiness.py` (NEW) — unit-тесты с mock ssh_runner
                       - `tests/test_vps_readiness.py` (DELETE) — старые static-analysis тесты
                       - `tests/gates/test_gate_sequencing.py` (MODIFY) — адаптация gate-теста под shell-фасад
                       - `core/entrypoint-manifest.yaml` (MODIFY) — обновление gate-определения
                       - `tests/test_inventory.yaml` (MODIFY) — регенерация после удаления старых тестов
                       - `tests/test_inventory_changes.yaml` (MODIFY) — обновление expected removals
REQUIRES:              `core/lib/ssh.sh` (ssh_read/ssh_exec — внешняя зависимость, используется
                       через subprocess-bash паттерн). `core/internal/scripts/yaml_query.py`
                       больше НЕ требуется (Python парсит NODE_HOST_MAP JSON напрямую).
$END_ARTIFACT_CONTRACT

---

## 1. Problem Matrix

| # | Проблема | Текущее состояние | Решается как |
|---|----------|------------------|--------------|
| P1 | `core/lib/vps-readiness.sh` (181 LOC) — последний lib-файл с бизнес-логикой в bash | Подтверждено: 181 строка, 4 pre-flight проверки, функция check_vps_ready | Вынос бизнес-логики в `core/internal/shared/vps_readiness.py`, shell → фасад ≤40 LOC |
| P2 | Латентный баг `$first \|\| json_diag+=","` (строка 170) | Подтверждено: после `first=false` bash пытается выполнить `false` как команду → `false: command not found` | Python строит JSON через структуры данных (`list[dict]` → `json.dumps`), строковая конкатенация исключена |
| P3 | vps-readiness.sh — последний потребитель `yaml_query.py --stdin` для NODE_HOST_MAP | Подтверждено: строки 75, 83 вызывают `python3 yaml_query.py --stdin --get/--keys` | Python парсит `json.loads(os.environ["NODE_HOST_MAP"])` напрямую, без subprocess |
| P4 | Существующий gate-тест `test_gate_vps_readiness_sourceable` проверяет sourceability shell-скрипта | Подтверждено: тест в `tests/gates/test_gate_sequencing.py:199` | Адаптировать: shell-фасад остаётся sourceable, но проверять наличие `check_vps_ready` после source |
| P5 | Старые тесты `tests/test_vps_readiness.py` — static analysis на grep, устареют после миграции | Подтверждено: 81 LOC, 1 тест `test_ping_check_uses_pong` | Удалить. Заменить на `tests/unit/test_vps_readiness.py` с mock ssh_runner |

---

## 2. Draft Code Graph

```xml
<code_graph>
  <entity id="vps_readiness_py" type="MODULE" keywords="vps-readiness preflight ssh docker check readiness-check">
    <annotation>core/internal/shared/vps_readiness.py — Python-модуль с check_vps_ready(node, output_mode, quick_mode, *, ssh_runner, node_host_map)</annotation>
    <crossLinks>
      <link target="ssh_sh" relation="calls_via_subprocess"/>
      <link target="shell_facade" relation="called_by"/>
    </crossLinks>
  </entity>

  <entity id="shell_facade" type="SHELL_FACADE" keywords="vps-readiness facade sourceable bash">
    <annotation>core/lib/vps-readiness.sh — тонкий фасад ≤40 LOC: source ssh.sh → вызов python3 -m core.internal.shared.vps_readiness</annotation>
    <crossLinks>
      <link target="vps_readiness_py" relation="delegates_to"/>
      <link target="deploy_mk" relation="sourced_by"/>
    </crossLinks>
  </entity>

  <entity id="deploy_mk" type="MAKEFILE" keywords="deploy pre-flight vps-readiness">
    <annotation>makefiles/deploy.mk:37-38 — source + check_vps_ready, БЕЗ изменений</annotation>
    <crossLinks>
      <link target="shell_facade" relation="sources"/>
    </crossLinks>
  </entity>

  <entity id="ssh_sh" type="LIB" keywords="ssh-read ssh-exec timeout facade remote-cmd">
    <annotation>core/lib/ssh.sh — единый source of truth для SSH (TRAP[DECISION] 2026-07-21)</annotation>
    <crossLinks>
      <link target="vps_readiness_py" relation="subprocess_source"/>
    </crossLinks>
  </entity>

  <entity id="test_unit" type="TEST" keywords="unit test mock ssh_runner vps-readiness">
    <annotation>tests/unit/test_vps_readiness.py — unit-тесты: mock ssh_runner, LDD caplog, ANTI-SURVIVORSHIP на баг $first</annotation>
    <crossLinks>
      <link target="vps_readiness_py" relation="tests"/>
    </crossLinks>
  </entity>

  <entity id="test_gate" type="GATE_TEST" keywords="gate sourceable shell-facade vps_readiness">
    <annotation>tests/gates/test_gate_sequencing.py::test_gate_vps_readiness_sourceable — адаптирован под shell-фасад</annotation>
    <crossLinks>
      <link target="shell_facade" relation="tests"/>
    </crossLinks>
  </entity>

  <entity id="entrypoint_manifest" type="CONFIG" keywords="entrypoint-manifest gate vps_readiness_sourceable">
    <annotation>core/entrypoint-manifest.yaml — gate определение vps_readiness_sourceable</annotation>
    <crossLinks>
      <link target="test_gate" relation="registers"/>
    </crossLinks>
  </entity>
</code_graph>
```

---

## 3. Architecture Overview

### 3.1 SSH Integration — Design Decision

```
┌─ Production path ───────────────────────────────────────────────┐
│  deploy.mk                                                      │
│    → source core/lib/vps-readiness.sh                           │
│      → python3 -m core.internal.shared.vps_readiness NODE --json│
│        → _default_ssh_runner(host, user, cmd, timeout)          │
│          → subprocess.run(["bash", "-c",                        │
│             "source core/lib/ssh.sh && ssh_read host user cmd"])│
│            → return (exit_code, stdout)                         │
└──────────────────────────────────────────────────────────────────┘

┌─ Test path ─────────────────────────────────────────────────────┐
│  test_check_vps_ready_all_ok                                    │
│    → check_vps_ready(node, ssh_runner=mock_ssh_runner)          │
│      → mock_ssh_runner возвращает предопределённые (0, "pong")  │
│      → assert result == (True, diagnostics)                     │
└──────────────────────────────────────────────────────────────────┘
```

### 3.2 @rationale — Почему subprocess-bash, а не прямой Python SSH

**Q:** Почему Python-модуль не вызывает `subprocess.run(["ssh", ...])` напрямую, а идёт через `bash -c 'source lib/ssh.sh && ssh_read'`?

**A:** Прецедент установлен в `core/internal/scaffold/project_lister.py` (строка 44, TRAP[DECISION] 2026-07-30) и `core/internal/scaffold/project_remover.py` (строка 253):
- `lib/ssh.sh` — **единый source of truth** для всех SSH-операций платформы
- Дублирование SSH_OPTS_COMMON, timeout-логики, обработки exit=124 в Python нарушило бы DRY и создало бы риск расхождения
- subprocess overhead (~50ms на вызов) не критичен для pre-flight проверок (3-4 вызова за весь ран)
- **Когда извлекать в Python SSH runner:** если количество потребителей превысит 3 или overhead станет проблемой (TRAP[DECISION] в project_lister.py)

### 3.3 @rationale — NODE_HOST_MAP: прямой json.loads вместо yaml_query.py

**Q:** Почему Python не вызывает `yaml_query.py --stdin --get` через subprocess?

**A:** NODE_HOST_MAP — это JSON-строка в env-переменной. В Python `json.loads()` — builtin, zero-overhead. Вызов `yaml_query.py` через subprocess был вынужденным решением в bash (нет нативного JSON-парсинга). В Python это избыточно.

### 3.4 Function Signature

```python
def check_vps_ready(
    node_name: str,
    *,
    output_mode: str = "text",   # "text" | "json"
    quick_mode: bool = False,    # True → skip Docker check
    ssh_runner: Callable[[str, str, str, int], tuple[int, str]] | None = None,
    node_host_map: dict[str, str] | None = None,
) -> tuple[bool, dict[str, Any]]:
    """Run pre-flight VPS readiness checks.

    Returns:
        (True,  {"status": "ready", ...})    — all checks passed
        (False, {"status": "not_ready", ...}) — one or more checks failed
    """
```

**ssh_runner** — Dependency Injection для тестирования. Сигнатура: `(host: str, user: str, cmd: str, timeout: int) -> tuple[int, str]`. Если `None` — используется `_default_ssh_runner()` (subprocess → lib/ssh.sh).

**node_host_map** — для тестирования. Если `None` — читается из `os.environ["NODE_HOST_MAP"]`.

### 3.5 Data Flow (4 pre-flight checks)

```
▶ check_vps_ready(node, output_mode, quick_mode)
  │
  ├─ ◇ Step 0: Resolve NODE_HOST_MAP
  │   ├─ env NODE_HOST_MAP unset? → return (False, diagnostics + remediation)
  │   └─ json.loads(NODE_HOST_MAP) → lookup node_name → host
  │       └─ node not found? → return (False, diagnostics + remediation + available keys)
  │
  ├─ ◇ Step 1: SSH accessibility (user=ci-deploy, timeout=30s via ssh_read)
  │   ├─ ssh_runner(host, "ci-deploy", "exit", 30) → exit=0 → OK
  │   └─ exit≠0 → FAIL + remediation: "ssh ci-deploy@host — verify network/SSH key"
  │
  ├─ ◇ Step 2: Forced-command ping (fail-if-not-ready)
  │   ├─ ssh_runner(host, "ci-deploy", "ping", 30) → stdout contains "pong" → OK
  │   └─ no "pong" → FAIL + remediation: "make bootstrap-node NODE=node first"
  │
  ├─ ◇ Step 3: /opt/projects/ exists + writable (fail-if-not-ready)
  │   ├─ ssh_runner(host, "ci-deploy", "test -d /opt/projects && test -w /opt/projects && echo OK || echo FAIL", 30)
  │   ├─ stdout == "OK" → OK
  │   └─ stdout != "OK" → FAIL + remediation: "make bootstrap-node NODE=node"
  │
  └─ ◇ Step 4: Docker daemon (skip if quick_mode)
      ├─ quick_mode? → SKIP, log IMP:7
      ├─ ssh_runner(host, "ci-deploy", "docker info --format '{{.ServerVersion}}' 2>/dev/null || echo FAIL", 30)
      ├─ stdout ≠ "FAIL" → OK
      └─ stdout == "FAIL" → FAIL + remediation: "systemctl start docker on VPS"
```

**Fail-fast:** каждая проверка выполняется только если `all_ok == True`. Первая же неудача останавливает цепочку.

### 3.6 JSON Diagnostics — Fix для бага `$first`

В bash-версии JSON строился строковой конкатенацией:
```bash
local json_diag="["
local first=true
for msg in "${diag_messages[@]}"; do
    $first || json_diag+=","   # ← БАГ: после first=false → пытается выполнить `false`
    first=false
    json_diag+='{"check":"..."}'
done
json_diag+="]"
```

В Python-версии JSON строится через структуры данных:
```python
failures: list[dict] = []
for msg, hint in zip(diag_messages, remediation_hints):
    failures.append({"check": msg, "remediation": hint})
result = {
    "status": "not_ready",
    "node": node_name,
    "host": ssh_host,
    "failures": failures,
}
# json.dumps(result) — всегда валидный JSON, никакой конкатенации
```

---

## 4. Shell Facade Design (≤40 LOC)

```bash
#!/usr/bin/env bash
# GREP_SUMMARY: vps-readiness facade python delegation
# STRUCTURE: ▶ source ssh.sh → ◇ python3 -m core.internal.shared.vps_readiness "$@" → ⎋ exit $?
# region MODULE_CONTRACT
## @purpose  Thin shell facade — delegates to core.internal.shared.vps_readiness.py
## @scope    Sourced by makefiles/deploy.mk; preserves check_vps_ready() API
# endregion

source "${BASH_SOURCE[0]%/*}/ssh.sh"  # SSH_OPTS_COMMON, ssh_read, ssh_exec

check_vps_ready() {
    python3 -m core.internal.shared.vps_readiness "$@"
}
```

**LOC estimate:** ~15 строк (заголовки, комментарии, source, функция). Комфортно укладывается в ≤40 LOC.

Важно: shell-фасад **не source'ит** `logging.sh` (IMP-логи теперь в Python), **не source'ит** `paths.sh` (не нужен). Только `ssh.sh` для SSH_OPTS_COMMON/ssh_read/ssh_exec, используемых default ssh_runner'ом внутри Python.

---

## 5. File Manifest

| # | Файл | Действие | Тип | Описание |
|---|------|:--------:|-----|----------|
| F1 | `core/internal/shared/vps_readiness.py` | CREATE | PYTHON | Python-модуль: check_vps_ready() + _default_ssh_runner() + CLI __main__ |
| F2 | `core/lib/vps-readiness.sh` | MODIFY | SHELL | Усечение до тонкого фасада ≤40 LOC |
| F3 | `tests/unit/test_vps_readiness.py` | CREATE | PYTEST | Unit-тесты с mock ssh_runner (11 тестов, см. §$TEST_SPEC) |
| F4 | `tests/test_vps_readiness.py` | DELETE | PYTEST | Старый static-analysis тест — заменён на F3 |
| F5 | `tests/gates/test_gate_sequencing.py` | MODIFY | PYTEST | Адаптация test_gate_vps_readiness_sourceable под shell-фасад |
| F6 | `core/entrypoint-manifest.yaml` | MODIFY | YAML | Обновление gate vps_readiness_sourceable |
| F7 | `tests/test_inventory.yaml` | MODIFY | YAML | Регенерация после удаления F4 + добавления F3 |
| F8 | `tests/test_inventory_changes.yaml` | MODIFY | YAML | Обновление expected removals |

**НЕ требует изменений:**
- `makefiles/deploy.mk` — source+check_vps_ready остаётся неизменным
- `core/internal/scripts/yaml_query.py` — больше не используется vps-readiness (но остаётся для других потребителей)
- `core/internal/shared/__init__.py` — модуль не требует re-export

---

## 6. Step-by-Step Data Flow (Implementation Sequence)

```
Brief 105 → DevPlan 105 (этот документ)
  │
  ├─► Wave 1: Python-модуль (F1)
  │   └─► TASK-1: Создать core/internal/shared/vps_readiness.py
  │       · check_vps_ready() — оркестрация 4 проверок с fail-fast
  │       · _default_ssh_runner() — subprocess → lib/ssh.sh (прецедент project_lister.py)
  │       · _resolve_node_host() — json.loads(NODE_HOST_MAP) → host
  │       · _build_json_diagnostics() — структуры данных, не строковая конкатенация
  │       · CLI __main__: argparser (node, --json, --quick)
  │       · Документация: MODULE_CONTRACT, GREP_SUMMARY, STRUCTURE, LDD IMP-логи
  │
  ├─► Wave 2: Shell-фасад + тесты (F2, F3) — параллельно
  │   ├─► TASK-2: Переписать core/lib/vps-readiness.sh → фасад ≤40 LOC
  │   │   · source ssh.sh, check_vps_ready() → python3 -m ...
  │   │   · Сохранить MODULE_CONTRACT, GREP_SUMMARY
  │   │
  │   └─► TASK-3: Создать tests/unit/test_vps_readiness.py
  │       · 11 тестов (см. §$TEST_SPEC)
  │       · mock ssh_runner: lambda h,u,c,t: (exit_code, stdout)
  │       · LDD caplog IMP:9 assertion на каждый успешный сценарий
  │       · R5 ANTI-SURVIVORSHIP: test_json_no_extra_commas (входные данные, вызвавшие баг $first)
  │
  ├─► Wave 3: Gate-тест + manifest + cleanup (F4-F8) — параллельно
  │   ├─► TASK-4: Адаптировать test_gate_vps_readiness_sourceable
  │   │   · Проверять: shell-фасад sourceable + check_vps_ready определена
  │   │
  │   ├─► TASK-5: Обновить core/entrypoint-manifest.yaml
  │   │   · gate: vps_readiness_sourceable — описание под shell-фасад
  │   │
  │   └─► TASK-6: Удалить старые тесты + обновить inventories
  │       · Удалить tests/test_vps_readiness.py (F4)
  │       · Обновить test_inventory.yaml + test_inventory_changes.yaml
  │
  └─► Wave 4: Верификация (TASK-7)
      └─► TASK-7: make gate MODE=fast + pytest tests/unit/test_vps_readiness.py
          · Все unit-тесты зелёные
          · gate-тест vps_readiness_sourceable зелёный
          · make gate MODE=fast зелёный (или зафиксировать причины если нет)
```

---

## 7. $TASKS

| ID | Задача | Владелец | Артефакт | AC | Зависимости | Сложность | Волна |
|----|--------|:--------:|----------|----|:-----------:|:---------:|:-----:|
| TASK-1 | Создать `core/internal/shared/vps_readiness.py` — бизнес-логика + CLI | Coder | F1 | AC1, AC6, AC7, AC8 | — | 6 | W1 |
| TASK-2 | Переписать `core/lib/vps-readiness.sh` → shell-фасад ≤40 LOC | Coder | F2 | AC2, AC3, AC4, AC5 | TASK-1 | 3 | W2 |
| TASK-3 | Создать `tests/unit/test_vps_readiness.py` — 11 unit-тестов | Coder | F3 | AC9 | TASK-1 | 5 | W2 |
| TASK-4 | Адаптировать `test_gate_vps_readiness_sourceable` под shell-фасад | Coder | F5 | AC10 | TASK-2 | 2 | W3 |
| TASK-5 | Обновить `core/entrypoint-manifest.yaml` gate vps_readiness | Coder | F6 | AC10 | TASK-2 | 1 | W3 |
| TASK-6 | Удалить старые тесты + обновить test inventories | Coder | F4,F7,F8 | AC10 | TASK-3 | 2 | W3 |
| TASK-7 | Верификация: `make gate MODE=fast` + unit-тесты зелёные | QA | — | AC10 | TASK-4,TASK-5,TASK-6 | 2 | W4 |

---

## 8. $PARALLEL_GROUPS

### Wave 1 (independent)
- **Tasks:** TASK-1
- **Command:** `coder Read DevPlan.md, implement Wave 1: TASK-1`

### Wave 2 (depends on Wave 1, no shared files between TASK-2 and TASK-3)
- **Tasks:** TASK-2, TASK-3
- **Files TASK-2:** `core/lib/vps-readiness.sh`
- **Files TASK-3:** `tests/unit/test_vps_readiness.py` (NEW)
- **Intersection:** none → запускать параллельно
- **Command:** `coder Read DevPlan.md, implement Wave 2: TASK-2, TASK-3`

### Wave 3 (depends on Wave 2, no shared files between TASK-4/5/6)
- **Tasks:** TASK-4, TASK-5, TASK-6
- **Files TASK-4:** `tests/gates/test_gate_sequencing.py`
- **Files TASK-5:** `core/entrypoint-manifest.yaml`
- **Files TASK-6:** `tests/test_vps_readiness.py`, `tests/test_inventory.yaml`, `tests/test_inventory_changes.yaml`
- **Intersection:** none → запускать параллельно
- **Command:** `coder Read DevPlan.md, implement Wave 3: TASK-4, TASK-5, TASK-6`

### Wave 4 (depends on Wave 3)
- **Tasks:** TASK-7
- **Command:** `qa Read DevPlan.md, verify Wave 4: TASK-7`

---

## 9. $TEST_SPEC

| # | Test file | Test function | Scenario | Module under test |
|---|-----------|---------------|----------|-------------------|
| T1 | `tests/unit/test_vps_readiness.py` | `test_all_checks_pass` | Все 4 проверки успешны → (True, diagnostics) | check_vps_ready |
| T2 | `tests/unit/test_vps_readiness.py` | `test_no_node_host_map` | NODE_HOST_MAP не задана → (False, remediation) | _resolve_node_host |
| T3 | `tests/unit/test_vps_readiness.py` | `test_node_not_in_map` | Node не найден в NODE_HOST_MAP → (False, available keys) | _resolve_node_host |
| T4 | `tests/unit/test_vps_readiness.py` | `test_ssh_unreachable` | SSH check fail → (False, remediation hint) | check_vps_ready |
| T5 | `tests/unit/test_vps_readiness.py` | `test_ping_no_pong` | Forced-command ping возвращает не "pong" → (False, remediation) | check_vps_ready |
| T6 | `tests/unit/test_vps_readiness.py` | `test_projects_missing` | /opt/projects/ отсутствует → (False, remediation) | check_vps_ready |
| T7 | `tests/unit/test_vps_readiness.py` | `test_docker_unreachable` | Docker daemon не отвечает → (False, remediation) | check_vps_ready |
| T8 | `tests/unit/test_vps_readiness.py` | `test_quick_skips_docker` | --quick: Docker check skipped, остальные 3 проходят | check_vps_ready |
| T9 | `tests/unit/test_vps_readiness.py` | `test_json_output_ready` | --json: валидный JSON {"status":"ready",...} | _build_json_diagnostics |
| T10 | `tests/unit/test_vps_readiness.py` | `test_json_output_failures` | --json при ошибках: валидный JSON с failures array | _build_json_diagnostics |
| T11 | `tests/unit/test_vps_readiness.py` | `test_json_no_extra_commas` | **R5 ANTI-SURVIVORSHIP:** множественные failures → JSON не содержит `,,` или broken syntax | _build_json_diagnostics |
| G1 | `tests/gates/test_gate_sequencing.py` | `test_gate_vps_readiness_sourceable` | Shell-фасад sourceable + check_vps_ready определена | vps-readiness.sh (фасад) |

**$TEST_SPEC rules:**
- Все unit-тесты (T1-T11): `@pytest.mark.unit`, native imports, `tmp_path` где нужно, LDD caplog IMP:9 assertion
- T11: ANTI-SURVIVORSHIP (R5) — проверяет, что баг `$first` не воспроизводится: при ≥2 failures JSON должен быть валидным без лишних запятых
- G1: gate-тест, `@pytest.mark.gate`, проверяет sourceability shell-фасада

---

## 10. Acceptance Criteria Mapping

| AC | Описание | Верификация |
|----|----------|-------------|
| AC1 | Python-модуль с check_vps_ready() + 4 проверки | TASK-1: файл существует, все функции определены |
| AC2 | Shell-фасад ≤40 LOC | TASK-2: `wc -l core/lib/vps-readiness.sh` ≤ 40 |
| AC3 | check_vps_ready \<node\> идентично | TASK-3: T1 (все проверки проходят) |
| AC4 | --quick идентично | TASK-3: T8 (Docker skip) |
| AC5 | --json идентично | TASK-3: T9, T10 (JSON output) |
| AC6 | Баг `$first` исправлен | TASK-3: T11 (ANTI-SURVIVORSHIP — JSON без лишних запятых) |
| AC7 | Remediation hints сохранены | TASK-3: T4-T7 (каждый failure mode → hint) |
| AC8 | NODE_HOST_MAP резолвинг идентичен | TASK-3: T2, T3 (map unset, node not found) |
| AC9 | Unit-тесты с mock ssh_runner | TASK-3: 11 тестов, LDD IMP:9, Test Honesty R1/R2/R5 |
| AC10 | make gate MODE=fast зелёный | TASK-7: gate проходит |

---

## 11. Design Decisions

### D1: ssh_runner DI pattern
**@rationale:** Прецедент из `core/internal/scaffold/project_lister.py:287` и `core/internal/scaffold/project_remover.py:253`. `ssh_runner: Callable | None` — опциональный параметр. В production: `None` → `_default_ssh_runner()` (subprocess-bash). В тестах: mock-функция. Сигнатура: `(host, user, cmd, timeout) -> tuple[int, str]`.

### D2: NODE_HOST_MAP — прямой json.loads
**@rationale:** В Python `json.loads()` — builtin. Вызов `yaml_query.py --stdin` через subprocess был вынужденным в bash. Python-модуль парсит JSON напрямую, без fork+exec overhead.

### D3: fail-fast через `all_ok` boolean
**@rationale:** Сохранение семантики bash-версии. Каждая проверка обёрнута в `if all_ok:` — первая же неудача прерывает цепочку. Логирование IMP:9 на каждом OK, IMP:10 на каждом FAIL.

### D4: CLI через `__main__` + argparse
**@rationale:** Shell-фасад вызывает `python3 -m core.internal.shared.vps_readiness <node> [--json] [--quick]`. argparse парсит аргументы, вызывает `check_vps_ready()`, печатает JSON (если `--json`) в stdout, логи в stderr, exit 0/1.

### D5: Shell-фасад НЕ использует logging.sh/paths.sh
**@rationale:** IMP-логи теперь в Python. `paths.sh` не нужен (нет резолвинга платформенных путей в фасаде). Только `ssh.sh` — для `_default_ssh_runner()` внутри Python (ssh_read требует SSH_OPTS_COMMON из ssh.sh).

---

## 12. Risks & Mitigations

| Риск | Вероятность | Mitigation |
|------|:-----------:|------------|
| R1: `_default_ssh_runner()` subprocess-bash не находит `core/lib/ssh.sh` (cwd ≠ project root) | MEDIUM | Использовать `PLATFORM_ROOT` env или резолвить относительно `__file__`. В shell-фасаде `ssh.sh` уже sourced — для Python нужно абсолютный путь. **Решение:** `_default_ssh_runner()` принимает `ssh_lib_path` параметр (по умолчанию: резолвинг от `__file__`). |
| R2: macOS dev-машина — `timeout` не GNU (DRIFT-note из AGENTS.md) | MEDIUM | `_default_ssh_runner()` оборачивает subprocess в `timeout=timeout+5` на уровне Python (а не bash `timeout`). Проблема только если ssh_read внутри bash использует GNU timeout → на macOS потребуется `brew install coreutils`. **Митигация:** Python-level timeout через `subprocess.run(timeout=...)` + документировать в TRAP. |
| R3: Gate-тест `vps_readiness_sourceable` падает после миграции | LOW | TASK-4 адаптирует тест под shell-фасад. Shell-фасад остаётся sourceable (определяет `check_vps_ready` функцию). |
| R4: Старые тесты `test_vps_readiness.py` используют grep на shell-скрипте → ложные FAIL | HIGH | TASK-6 удаляет старые тесты. Unit-тесты (TASK-3) покрывают ту же функциональность через mock ssh_runner. |
| R5: `make gate MODE=fast` красный по причинам вне скоупа (pre-commit hook на чужих файлах) | MEDIUM | TASK-7 фиксирует ФАКТИЧЕСКОЕ состояние gate (как в DevPlan 096 R4). Если gate красный по причинам ≠ vps-readiness → документировать, не блокировать merge. |

---

## 13. Migration Path

1. **TASK-1:** Создать Python-модуль (НЕ затрагивает shell-скрипт)
2. **TASK-2:** Заменить shell-скрипт на фасад (deploy.mk продолжает работать)
3. **TASK-3:** Создать unit-тесты (валидация против AC1-AC9)
4. **TASK-4,5,6:** Gate-тест + manifest + cleanup
5. **TASK-7:** `make gate MODE=fast` + `pytest tests/unit/test_vps_readiness.py -v`

**Откат:** `git revert` коммита миграции. Shell-фасад вызывает Python-модуль — удаление Python-файла + восстановление shell из git history.

---

## 14. Next Steps

### Wave 1
```
coder Read .ai/plans/105-vps-readiness-python/02-DevPlan.md, implement Wave 1: TASK-1
```
Создать `core/internal/shared/vps_readiness.py` — Python-модуль с check_vps_ready(), _default_ssh_runner(), CLI __main__.

### Wave 2
```
coder Read .ai/plans/105-vps-readiness-python/02-DevPlan.md, implement Wave 2: TASK-2, TASK-3
```
TASK-2: переписать `core/lib/vps-readiness.sh` → фасад ≤40 LOC.
TASK-3: создать `tests/unit/test_vps_readiness.py` (11 тестов).

### Wave 3
```
coder Read .ai/plans/105-vps-readiness-python/02-DevPlan.md, implement Wave 3: TASK-4, TASK-5, TASK-6
```
TASK-4: gate-тест, TASK-5: manifest, TASK-6: старые тесты + inventory.

### Wave 4
```
qa Read .ai/plans/105-vps-readiness-python/02-DevPlan.md, verify Wave 4: TASK-7
```
`make gate MODE=fast` + unit-тесты.

$END_DEVPLAN

---

## QA Review (2026-07-31)

🔒 **Verified against SHA:** `fbe306d4284d9105193605378be28eb64b3c6795` (working tree clean)

### Методология

Проверены все утверждения DevPlan против фактической кодовой базы:
- `core/lib/vps-readiness.sh` (181 LOC), `core/lib/ssh.sh` (ssh_read/ssh_exec API),
  `core/internal/scaffold/project_lister.py` (subprocess-bash прецедент),
  `core/internal/scaffold/project_remover.py` (subprocess-bash прецедент),
  `core/entrypoint-manifest.yaml` (gate `vps_readiness_sourceable`, строка 1326),
  `tests/gates/test_gate_sequencing.py` (gate-тест, строка 199),
  `tests/test_vps_readiness.py` (81 LOC, `test_ping_check_uses_pong`),
  `tests/test_inventory.yaml` (2 записи vps_readiness: gate + static),
  `tests/test_inventory_changes.yaml` (4 старых удаления из DevPlan 001),
  `makefiles/deploy.mk` (строка 37-38 — source + check_vps_ready).

### Найденные расхождения и внесённые исправления

| # | Серьёзность | Расхождение | Исправление |
|---|:----------:|-------------|-------------|
| **F1** | **HIGH** | **Путь к `project_lister.py`/`project_remover.py`:** DevPlan ссылался на `core/internal/shared/project_lister.py`, но фактический файл находится в `core/internal/scaffold/project_lister.py` (строка 44 — TRAP[DECISION], строка 287 — `_ssh_read` DI). Аналогично `project_remover.py` — в `scaffold/`, не `shared/`. | Исправлены пути в DESCRIPTION, §3.2 (строка 149/153), §11 D1 (строка 419). |
| **F2** | **MEDIUM** | **Таймаут SSH в Brief vs код:** Brief (01-Brief.md, строка 9) говорит "SSH check (10s timeout)". Фактический код `vps-readiness.sh` использует timeout=30 на **всех 4 проверках** (строка 93, 106, 121, 136). Комментарий STRUCTURE в shell (строка 3) тоже говорит "10s timeout" — это латентный doc-баг в текущем коде (расхождение комментария и фактического `ssh_read ... 30`). DevPlan **корректно** отражает 30s в §3.5. | DevPlan НЕ меняется — он прав. Brief расходится с кодом; код является авторитетным источником. Shell STRUCTURE-комментарий (строка 3) требует отдельного fix вне скоупа 105. |
| **F3** | **LOW** | **`project_remover.py` строка `_default_ssh`:** DevPlan ссылался на строку 254; фактически определение функции `def _default_ssh(...)` начинается на строке 253. | Исправлено: 254 → 253 в §3.2. |
| **F4** | **INFO** | **`test_inventory.yaml` vs `test_inventory_changes.yaml`:** F4 (текущий `tests/test_vps_readiness.py`) содержит только 1 тест `test_ping_check_uses_pong` (81 LOC, маркер `static_audit`). `test_inventory_changes.yaml` уже содержит 4 старых удаления из DevPlan 001. При TASK-6 удалении F4 потребуется добавить 1 новую запись в `test_inventory_changes.yaml` (не 4). | DevPlan корректен — TASK-6 описывает «обновить test inventories»; 4 старых записи не затрагиваются. |

### Верификация покрытия AC

| AC | Статус | Доказательство |
|----|:------:|---------------|
| AC1: Python-модуль + 4 проверки | ✅ | TASK-1, §3.5 data flow (Steps 0-4), §$TEST_SPEC T1-T7 |
| AC2: Shell-фасад ≤40 LOC | ✅ | TASK-2, §4 (≈15 строк), AC mapping |
| AC3: `check_vps_ready <node>` без флагов | ✅ | §$TEST_SPEC T1 (output_mode="text", quick_mode=False — путь по умолчанию) |
| AC4: `--quick` идентично | ✅ | §$TEST_SPEC T8 (Docker skip) |
| AC5: `--json` идентично | ✅ | §$TEST_SPEC T9, T10 |
| AC6: Баг `$first` исправлен | ✅ | §$TEST_SPEC T11 (R5 ANTI-SURVIVORSHIP), §3.6 (JSON через структуры данных) |
| AC7: Remediation hints сохранены | ✅ | §$TEST_SPEC T4-T7 (каждый failure mode проверяется) |
| AC8: NODE_HOST_MAP резолвинг | ✅ | §$TEST_SPEC T2, T3; §3.5 Step 0 |
| AC9: Unit-тесты + mock ssh_runner | ✅ | §$TEST_SPEC T1-T11 (mock ssh_runner, LDD IMP:9, R1/R2/R5) |
| AC10: `make gate MODE=fast` зелёный | ✅ | TASK-7, gate-тест `vps_readiness_sourceable` остаётся валидным (shell-фасад определяет `check_vps_ready`) |

### Верификация инвариантов

| Инвариант | Статус | Комментарий |
|-----------|:------:|-------------|
| Makefile-фасад: все операции через `make` | ✅ HELD | `deploy.mk` продолжает вызывать `check_vps_ready()` через shell-фасад без изменений |
| Python-first: новый код = Python, bash — фасад ≤40 LOC | ✅ HELD | 181→15 LOC shell, бизнес-логика в Python |
| SSH single-source-of-truth (`lib/ssh.sh`) | ✅ HELD | `_default_ssh_runner()` вызывает `lib/ssh.sh` через subprocess — НЕ дублирует SSH-логику |
| Manifest Generation Contract | ✅ HELD | Gate `vps_readiness_sourceable` уже зарегистрирован (строка 1326); TASK-5 обновляет описание |
| macOS без GNU `timeout` | ⚠️ AT_RISK | §12 R2: Python-level `subprocess.run(timeout=...)` как fallback. Но внутри subprocess вызывается `bash -c 'source lib/ssh.sh && ssh_read ...'` — bash-уровневый `timeout` всё ещё требует GNU coreutils на macOS. **Митигация достаточна:** Python timeout ловит зависание; на macOS dev-машине subprocess может упасть по TimeoutExpired от Python, а не от bash `timeout`. Production (Linux) не затронут. |

### Кросс-зависимости с планами 099–104

Планы 099–104 **отсутствуют** в `.ai/plans/`. Ближайшие: 092 (project_lister, project_remover — Strangler-Fig в `scaffold/`), 096 (doxygen), 097 (doxygen zero-warnings). Пересечений по файлам нет:
- 105 трогает `core/lib/vps-readiness.sh` + `core/internal/shared/vps_readiness.py` (NEW)
- 092 трогал `core/internal/scaffold/project_lister.py`, `core/internal/scaffold/project_remover.py` — разные модули
- `project_lister.py`/`project_remover.py` используются в DevPlan 105 только как **прецеденты** (subprocess-bash паттерн), не как зависимости

### $TEST_SPEC качество

| Критерий | Статус |
|----------|:------:|
| Native imports (не subprocess для бизнес-логики) | ✅ |
| LDD caplog IMP:9 assertion | ✅ (указано для всех T1-T11) |
| Test Honesty R1 (нет pass-тестов) | ✅ (каждый тест имеет assert на конкретный outcome) |
| Test Honesty R2 (нет unfalsifiable asserts) | ✅ |
| R5 ANTI-SURVIVORSHIP (T11 — баг `$first`) | ✅ |
| DI ssh_runner mock (не patch внутренностей) | ✅ (Callable-инъекция, не monkeypatch) |

### Итоговая оценка

| Параметр | Значение |
|----------|----------|
| Расхождений найдено | 4 (1 HIGH, 1 MEDIUM, 1 LOW, 1 INFO) |
| Расхождений исправлено | 3 (F1, F3; F2 — код прав, Brief неточен) |
| Неисправленных блокеров | 0 |
| AC покрытие | 10/10 (100%) |
| Инварианты | 4/5 HELD, 1 AT_RISK (macOS timeout — имеет митигацию) |
| Формат ($START_DEVPLAN, $ARTIFACT_CONTRACT, $TASKS и т.д.) | ✅ Соответствует doc-protocols |

### Вердикт: **APPROVED-WITH-CORRECTIONS**

**Обоснование:** DevPlan корректен по существу — архитектура миграции (Strangler-Fig, subprocess-bash, DI ssh_runner, JSON через структуры данных) валидна. Все 10 AC покрыты. Исправления F1 (путь к precedent-файлам) и F3 (номер строки) носят уточняющий характер и не влияют на архитектурные решения.

**Оставшиеся риски:**
1. **macOS `timeout` (R2):** Python `subprocess.run(timeout=...)` перехватит зависание, но диагностика будет менее точной (Python TimeoutExpired vs bash `exit 124`). На production (Linux) проблема отсутствует.
2. **`test_inventory_changes.yaml`:** При TASK-6 потребуется аккуратно добавить 1 запись об удалении `test_ping_check_uses_pong`, не затронув 4 существующих записи от DevPlan 001. Риск низкий — стандартная операция.
3. **Gate `vps_readiness_sourceable`:** Текущий тест (строка 199) проверяет `declare -f check_vps_ready` после `source` — это останется валидным. Но тест также source'ит `ssh.sh` (через vps-readiness.sh) — на macOS без `coreutils` `ssh.sh` упадёт при source (из-за `log_imp` dependency). Это существующая проблема, не вносимая 105.
