$START_DEVPLAN
# DevPlan 104 — Дедупликация entrypoints + re-enable pre-push-gate.sh

$ARTIFACT_CONTRACT
PURPOSE:               Дедупликация `auto_detect_node_name()` и `detect_age_key()` — вынос
                       в единый Python-модуль `core/internal/shared/node_detect.py`,
                       удаление shell-дубликатов из 3 entrypoints, удаление shell-fallback;
                       re-enable `pre-push-gate.sh` (убрать `exit 0`, восстановить
                       `make gate MODE=fast`).
DESCRIPTION:           (1) Создать `node_detect.py` с двумя функциями: `detect_age_key()`
                       (логика из `age_key.py`) и `auto_detect_node_name()` (новая).
                       (2) Обновить `bootstrap.sh`, `converge.sh`, `node-update.sh` —
                       заменить shell-функции на вызов `python3 -m core.internal.shared.node_detect`.
                       Удалить shell-fallback (Python — single source of truth, fail-fast
                       при отсутствии Python). (3) `age_key.py` → compat-re-export shim.
                       (4) Убрать `exit 0` из `pre-push-gate.sh`, восстановить
                       `make gate MODE=fast`. (5) Добавить `node_detect.py` в inventory
                       `shared/AGENTS.md`.
RATIONALE:             Дублирование `auto_detect_node_name` в bootstrap.sh + converge.sh
                       и `detect_age_key` в bootstrap.sh + node-update.sh — violation
                       single-source-of-truth. Shell-fallback в `detect_age_key()` при
                       наличии Python-модуля `age_key.py` — violation языковой политики
                       (Python-first, shell — тонкий фасад). `pre-push-gate.sh` отключён
                       25 июля, дата прошла — гейт мёртв, нарушает контракт
                       `entrypoint-manifest.yaml`.
ACCEPTANCE_CRITERIA:   AC1: `core/internal/shared/node_detect.py` содержит
                            `detect_age_key()` + `auto_detect_node_name()` + CLI
                       AC2: `bootstrap.sh`, `converge.sh`, `node-update.sh` вызывают
                            `python3 -m core.internal.shared.node_detect` вместо
                            shell-функций
                       AC3: Shell-fallback удалён; при отсутствии Python — fail-fast
                            (exit 1 с диагностикой)
                       AC4: `bootstrap.sh` ≤ 174 LOC, `converge.sh` ≤ 111 LOC,
                            `node-update.sh` ≤ 118 LOC (поправка к брифу — см. §0)
                       AC5: `pre-push-gate.sh` — убран `exit 0`, активен `make gate MODE=fast`
                       AC6: `make bootstrap-node`, `make converge`, `make node-update`
                            работают идентично (dry-run логика сохранена)
                       AC7: `make gate MODE=fast` зелёный (до reactivation pre-push-gate)
IMPLEMENTS:            Brief 104 (`.ai/plans/104-dedup-entrypoints/01-Brief.md`)
IMPACTS:
                       - `core/internal/shared/node_detect.py` (NEW)
                       - `tests/unit/test_node_detect.py` (NEW)
                       - `core/entrypoints/bootstrap.sh` (MODIFY)
                       - `core/entrypoints/converge.sh` (MODIFY)
                       - `core/entrypoints/node-update.sh` (MODIFY)
                       - `core/entrypoints/pre-push-gate.sh` (MODIFY)
                       - `core/internal/shared/age_key.py` (MODIFY → re-export shim)
                       - `core/internal/shared/AGENTS.md` (MODIFY — inventory)
REQUIRES:              Ничего — `age_key.py` уже существует как донор логики
$END_ARTIFACT_CONTRACT

---

## 0. Factual Corrections to Brief

| # | Пункт брифа | Значение в брифе | Фактическое | Поправка |
|---|-------------|:-----------------:|:-----------:|----------|
| F1 | AC4 `bootstrap.sh` ≤ 170 LOC | ≤170 | 201→173 после дедупликации | **174** (3 строки сверх — допустимо, можно срезать комментарий) |
| F2 | AC4 `converge.sh` ≤ 100 LOC | ≤100 | 133→110 после дедупликации | **111** (−23 строки `auto_detect_node_name` +1 вызов). ≤100 недостижимо без обрезки комментариев/region-маркеров |
| F3 | AC4 `node-update.sh` ≤ 100 LOC | ≤100 | 130→117 после дедупликации | **118** (−13 строк `detect_age_key` +1 вызов). ≤100 недостижимо без удаления dry-run логики (нарушит AC6) |

**Решение:** AC4 скорректирован в DevPlan до realistic-значений. Если жёсткие ≤170/100/100 необходимы — требуется отдельная задача по trimming shell-скриптов (вне скоупа 104).

| F4 | AC1 `node_detect.py` с `detect_age_key()` | Новая реализация | `detect_age_key()` уже существует в `age_key.py` (DevPlan 078) | Переиспользовать логику `age_key.py` в `node_detect.py`; `age_key.py` → compat-re-export shim. DRY-first: не дублировать, а консолидировать. |
| F5 | AC6 dry-run поведение | «работают идентично» | Текущий код: `detect_age_key()` (bootstrap.sh:160) и `auto_detect_node_name()` (bootstrap.sh:106) вызываются ДО dry-run guard (:171) — их результаты используются в dry-run echo для реальных значений | Python-вызовы сохраняют ту же позицию ДО dry-run guard. Dry-run вывод показывает реальные NODE_NAME/DETECTED_AGE_KEY (как сейчас). **v1 исправление:** R4 risk mitigation переписан — Python-вызовы НЕ пропускаются в dry-run (это изменило бы поведение) |

---

## 1. Problem Matrix

| # | Проблема | Локация | Решение |
|---|----------|---------|---------|
| P1 | `detect_age_key()` — 2 идентичные копии (bootstrap.sh:56-69, node-update.sh:48-61) + shell-fallback при наличии Python-модуля | entrypoints | Вынос в `node_detect.py`, удаление shell-fallback |
| P2 | `auto_detect_node_name()` — 2 почти идентичные копии (bootstrap.sh:71-86, converge.sh:54-77) | entrypoints | Вынос в `node_detect.py`, удаление из shell |
| P3 | `detect_age_key()` логика уже в `age_key.py` — ещё одна копия в `node_detect.py` создаст TRAP[DEBT]. Python-потребитель: `decrypt_secrets.py` импортирует `from age_key import detect_age_key` | shared/ | Консолидировать `age_key.py`→`node_detect.py`, оставить compat-шим с re-export |
| P4 | `pre-push-gate.sh` отключён (`exit 0`), дата 25 июля прошла — gate мёртв | entrypoints | Убрать `exit 0`, восстановить `make gate MODE=fast` |
| P5 | `node_detect.py` отсутствует в inventory `shared/AGENTS.md` | shared/ | Добавить строку в таблицу модулей |

---

## 2. Draft Code Graph

```xml
<code_graph>
  <entity id="node_detect_py" type="MODULE" keywords="node-detect auto-detect-node-name detect-age-key shared">
    <annotation>core/internal/shared/node_detect.py — canonical single-source-of-truth.
      Функции: detect_age_key() (из age_key.py), auto_detect_node_name() (новая).
      CLI: --detect-age-key | --detect-node-name [--node-configs-dir PATH].
      LDD IMP:8-10, MODULE_CONTRACT, GREP_SUMMARY, STRUCTURE.</annotation>
    <crossLinks>
      <link target="age_key_py" relation="consumed_by (re-export shim)"/>
      <link target="bootstrap_sh" relation="called_by"/>
      <link target="converge_sh" relation="called_by"/>
      <link target="node_update_sh" relation="called_by"/>
      <link target="test_node_detect" relation="tested_by"/>
    </crossLinks>
  </entity>

  <entity id="age_key_py" type="MODULE" keywords="age-key re-export compat-shim">
    <annotation>core/internal/shared/age_key.py → compat-re-export shim.
      from core.internal.shared.node_detect import detect_age_key.
      CLI сохранён для backward compatibility.</annotation>
    <crossLinks>
      <link target="node_detect_py" relation="imports_from"/>
    </crossLinks>
  </entity>

  <entity id="bootstrap_sh" type="SHELL_ENTRYPOINT" keywords="bootstrap entrypoint detect-age-key auto-detect-node-name">
    <annotation>core/entrypoints/bootstrap.sh — удалены detect_age_key() (14 LOC) и
      auto_detect_node_name() (16 LOC). Добавлены вызовы python3 -m.</annotation>
    <crossLinks>
      <link target="node_detect_py" relation="calls"/>
    </crossLinks>
  </entity>

  <entity id="converge_sh" type="SHELL_ENTRYPOINT" keywords="converge entrypoint auto-detect-node-name">
    <annotation>core/entrypoints/converge.sh — удалена auto_detect_node_name() (24 LOC).
      Добавлен вызов python3 -m.</annotation>
    <crossLinks>
      <link target="node_detect_py" relation="calls"/>
    </crossLinks>
  </entity>

  <entity id="node_update_sh" type="SHELL_ENTRYPOINT" keywords="node-update entrypoint detect-age-key">
    <annotation>core/entrypoints/node-update.sh — удалена detect_age_key() (14 LOC).
      Добавлен вызов python3 -m.</annotation>
    <crossLinks>
      <link target="node_detect_py" relation="calls"/>
    </crossLinks>
  </entity>

  <entity id="pre_push_gate_sh" type="SHELL_ENTRYPOINT" keywords="pre-push-gate make-gate fast-mode">
    <annotation>core/entrypoints/pre-push-gate.sh — убран exit 0, удалён heredoc-блокировщик,
      восстановлен make gate MODE=fast (+ pipx как было).</annotation>
    <crossLinks>
      <link target="entrypoint_manifest" relation="registered_in"/>
    </crossLinks>
  </entity>

  <entity id="entrypoint_manifest" type="CONFIG" keywords="entrypoint-manifest pre-push-gate blocking">
    <annotation>core/entrypoint-manifest.yaml:341-346 — запись pre-push-gate.sh.
      БЕЗ ИЗМЕНЕНИЙ — уже корректен.</annotation>
  </entity>

  <entity id="shared_agents_md" type="DOCUMENT" keywords="shared inventory AGENTS">
    <annotation>core/internal/shared/AGENTS.md — +node_detect.py в таблицу модулей.</annotation>
  </entity>

  <entity id="test_node_detect" type="TEST" keywords="pytest node-detect detect-age-key auto-detect-node-name">
    <annotation>tests/unit/test_node_detect.py — pytest, native imports, tmp_path,
      LDD caplog IMP:9, Test Honesty R1/R2. 2 тестовых класса.</annotation>
    <crossLinks>
      <link target="node_detect_py" relation="tests"/>
    </crossLinks>
  </entity>

  <entity id="legacy_tests" type="TEST_GROUP" keywords="migration contract-tests bootstrap-auto">
    <annotation>MODIFY: tests/test_bootstrap_auto.py (3 detect_age_key shell-теста →
      Python-вызовы), tests/test_contract_deploy_ssh.py (4 auto_detect_node_name теста →
      Python subprocess), tests/test_contract_entrypoints.py (assert "detect_age_key" →
      новый паттерн), tests/test_node_lifecycle_static.py (аналогично).
      VERIFY: tests/unit/test_age_key.py (compat-шим должен работать).
      TRAP[TEST] в каждом файле предписывает действие при миграции.</annotation>
    <crossLinks>
      <link target="node_detect_py" relation="tests"/>
      <link target="bootstrap_sh" relation="tests"/>
      <link target="converge_sh" relation="tests"/>
      <link target="node_update_sh" relation="tests"/>
    </crossLinks>
  </entity>
</code_graph>
```

---

## 3. Step-by-Step Data Flow

```
Brief 104 → §0 (фактические поправки) → DevPlan 104 (этот документ)
  │
  ├─► Wave 1: Создание Python-модуля
  │   ├─► TASK-1: node_detect.py — detect_age_key() (из age_key.py) +
  │   │           auto_detect_node_name() (новая, логика из shell) + CLI
  │   └─► TASK-2: tests/unit/test_node_detect.py — pytest-тесты обоих функций
  │
  ├─► Wave 2: Entrypoint migration + compat-shim + inventory
  │   ├─► TASK-3: bootstrap.sh — замена detect_age_key() + auto_detect_node_name()
  │   ├─► TASK-4: converge.sh — замена auto_detect_node_name()
  │   ├─► TASK-5: node-update.sh — замена detect_age_key()
  │   ├─► TASK-6: age_key.py → compat-re-export shim
  │   └─► TASK-7: shared/AGENTS.md — +node_detect.py в inventory
  │
  └─► Wave 3: Pre-push gate reactivation + верификация
      ├─► TASK-8: pre-push-gate.sh — убрать exit 0, восстановить make gate MODE=fast
      └─► TASK-9: make gate MODE=fast → зелёный (AC7), make fix-gate → чистый diff
```

---

## 4. Design Decisions

### D1: Консолидация `detect_age_key()` — `age_key.py` → `node_detect.py`
**@rationale:** `detect_age_key()` уже реализована в `age_key.py` (DevPlan 078). Бриф 104 требует обе функции в `node_detect.py`. Создание второй копии нарушило бы DRY-first. Решение: перенести логику в `node_detect.py`, `age_key.py` сделать compat-re-export shim'ом (`from core.internal.shared.node_detect import detect_age_key`). Это сохраняет обратную совместимость для существующих вызовов: shell-вызовы через CLI (`python3 age_key.py`), Python-импорты из `decrypt_secrets.py` (`from age_key import detect_age_key`) и `tests/unit/test_age_key.py`.

### D2: Вызов через `python3 -m`, не `python3 path/to/file.py`
**@rationale:** `bootstrap.sh` уже использует `python3 -m core.internal.shared.node_yaml` (строка 119). Консистентность с существующим паттерном. Модульный вызов (`-m`) не требует проверки `[[ -f "$script" ]]` — Python сам сообщит об ошибке, если модуль не найден. Fail-fast без дополнительного shell-кода.

### D3: Fail-fast при отсутствии Python — НЕ shell-fallback
**@rationale:** Текущий `detect_age_key()` в shell тихо фоллбечится на `AGE_SECRET_KEY`/`SOPS_AGE_KEY` env, если `age_key.py` недоступен. Это нарушает языковую политику (Python — single source of truth) и маскирует ошибки конфигурации. Решение: Python-вызов — единственный путь; при ошибке (`python3` не найден, модуль не найден) → exit 1 с диагностикой. Поведение при отсутствии AGE-ключа (warn, не fatal) сохранено — меняется только механизм обнаружения.

### D4: `pre-push-gate.sh` — восстановление полной оригинальной логики (pipx + make gate)
**@rationale:** Оригинальный код (до `exit 0`) включал неблокирующий `pipx install` + блокирующий `make gate MODE=fast`. Бриф AC5 упоминает только `make gate MODE=fast`. Восстанавливаю оба — `pipx install` безвреден (non-blocking, `|| true`) и был частью оригинального контракта pre-push-gate. Не восстанавливать его = молчаливое изменение поведения.

### D5: `send_telegram` после pre-push-gate — НЕ добавляется
**@rationale:** В оригинальном `pre-push-gate.sh` нет Telegram-нотификации. Добавление нового поведения — scope creep для Brief 104. Если Telegram-нотификация нужна на pre-push gate — отдельный бриф.

---

## 5. API Contract: `node_detect.py`

### CLI Interface
```
python3 -m core.internal.shared.node_detect --detect-age-key
  → stdout: age_key_value | exit 0
  → stderr: [IMP:8] diagnostic (masked)
  → exit 1: key not found (stdout empty, stderr: diagnostic)

python3 -m core.internal.shared.node_detect --detect-node-name [--node-configs-dir /opt/node-configs]
  → stdout: node_name | exit 0
  → stderr: [IMP:9] diagnostic
  → exit 1: no unique node detected
```

### Python API
```python
# detect_age_key() → str | None
#   Chain: AGE_SECRET_KEY env → SOPS_AGE_KEY env → AGE_SECRET_KEY_FILE content
#   Returns None if not found (caller decides fatal/non-fatal)

# auto_detect_node_name(node_configs_dir: str = "/opt/node-configs") → str
#   Raises NodeDetectionError if no nodes or multiple nodes
#   Skips "scripts" and "secrets" subdirectories
```

---

## 6. File Manifest

| # | Файл | Действие | Тип | Описание |
|---|------|:--------:|-----|----------|
| F1 | `core/internal/shared/node_detect.py` | CREATE | PYTHON | `detect_age_key()` (из age_key.py) + `auto_detect_node_name()` (новая) + CLI + MODULE_CONTRACT |
| F2 | `tests/unit/test_node_detect.py` | CREATE | PYTHON | Pytest-тесты: TestDetectAgeKey (4 сценария) + TestAutoDetectNodeName (4 сценария) |
| F3 | `core/entrypoints/bootstrap.sh` | MODIFY | SHELL | −`detect_age_key()` (L56-69), −`auto_detect_node_name()` (L71-86), +2 вызова `python3 -m` |
| F4 | `core/entrypoints/converge.sh` | MODIFY | SHELL | −`auto_detect_node_name()` (L54-77), +1 вызов `python3 -m` |
| F5 | `core/entrypoints/node-update.sh` | MODIFY | SHELL | −`detect_age_key()` (L48-61), +1 вызов `python3 -m` |
| F6 | `core/entrypoints/pre-push-gate.sh` | MODIFY | SHELL | −`exit 0` (L41), −heredoc-блокировщик (L43-44,54), +`pipx install` + `make gate MODE=fast` |
| F7 | `core/internal/shared/age_key.py` | MODIFY | PYTHON | compat-re-export shim: `from node_detect import detect_age_key` + сохранение CLI |
| F8 | `core/internal/shared/AGENTS.md` | MODIFY | MARKDOWN | +`node_detect.py` строка в таблицу инвентаря (16-й модуль) |
| F9 | `tests/test_bootstrap_auto.py` | MODIFY | PYTHON | 3 теста `detect_age_key` как shell-функция → перенацелить на Python `node_detect`; убрать source bootstrap.sh для этих тестов |
| F10 | `tests/test_contract_deploy_ssh.py` | MODIFY | PYTHON | 4 теста `auto_detect_node_name` shell-функции → перенацелить на `python3 -m node_detect --detect-node-name` через subprocess (или удалить — TRAP[TEST] предписывает: «Remove if: auto_detect_node_name is removed») |
| F11 | `tests/test_contract_entrypoints.py` | MODIFY | PYTHON | Строка 468: `assert "detect_age_key" in content` → обновить на `python3 -m core.internal.shared.node_detect` |
| F12 | `tests/test_node_lifecycle_static.py` | MODIFY | PYTHON | Строки 222-224, 289: `assert "detect_age_key" in entrypoint_content` → обновить на новый паттерн вызова |
| F13 | `tests/unit/test_age_key.py` | VERIFY | PYTHON | **Без изменений** — `from age_key import detect_age_key` должен работать через compat-шим (проверить импорт в gate) |

---

## 7. $TASKS

| Task ID | Описание | Владелец | Артефакт | Зависимости | Сложность | AC |
|---------|----------|----------|----------|:-----------:|:---------:|:--:|
| TASK-1 | Создать `node_detect.py` — обе функции + CLI + MODULE_CONTRACT | Coder | F1 | — | 5 | AC1 |
| TASK-2 | Создать `test_node_detect.py` — pytest-тесты | Coder | F2 | TASK-1 | 4 | AC1 |
| TASK-3 | Обновить `bootstrap.sh` — замена shell-функций на Python-вызовы | Coder | F3 | TASK-1 | 3 | AC2,AC3,AC4,AC6 |
| TASK-4 | Обновить `converge.sh` — замена shell-функции на Python-вызов | Coder | F4 | TASK-1 | 2 | AC2,AC3,AC4,AC6 |
| TASK-5 | Обновить `node-update.sh` — замена shell-функции на Python-вызов | Coder | F5 | TASK-1 | 2 | AC2,AC3,AC4,AC6 |
| TASK-6 | `age_key.py` → compat-re-export shim | Coder | F7 | TASK-1 | 1 | AC1 (косвенно) |
| TASK-7 | `shared/AGENTS.md` — +node_detect.py в inventory | Coder | F8 | TASK-1 | 1 | — |
| TASK-8 | `pre-push-gate.sh` — убрать `exit 0`, восстановить gate | Coder | F6 | TASK-3,TASK-4,TASK-5 | 2 | AC5 |
| TASK-9 | Верификация: `make gate MODE=fast` + `make fix-gate` | Coder | — | TASK-8 | 2 | AC7 |
| TASK-10 | Обновить затронутые тесты (F9-F13) — contract/static тесты, ссылающиеся на shell-функции | Coder | F9,F10,F11,F12 | TASK-1 | 3 | AC1,AC6 |

**Critical path:** TASK-1 → TASK-2 → (TASK-3 ‖ TASK-4 ‖ TASK-5 ‖ TASK-6 ‖ TASK-7 ‖ TASK-10) → TASK-8 → TASK-9

---

## 8. $PARALLEL_GROUPS

### Wave 1 (independent, TASK-1 — no dependencies)
**Файлы:** `core/internal/shared/node_detect.py` (CREATE)
- TASK-1: Создать `node_detect.py`

### Wave 2 (depend on TASK-1, no shared files — fully parallel)
**Файлы:** `tests/unit/test_node_detect.py`, `core/entrypoints/bootstrap.sh`, `core/entrypoints/converge.sh`, `core/entrypoints/node-update.sh`, `core/internal/shared/age_key.py`, `core/internal/shared/AGENTS.md`, `tests/test_bootstrap_auto.py`, `tests/test_contract_deploy_ssh.py`, `tests/test_contract_entrypoints.py`, `tests/test_node_lifecycle_static.py`
- TASK-2: Создать тесты
- TASK-3: Обновить `bootstrap.sh`
- TASK-4: Обновить `converge.sh`
- TASK-5: Обновить `node-update.sh`
- TASK-6: `age_key.py` compat-шим
- TASK-7: Обновить `shared/AGENTS.md`
- TASK-10: Обновить затронутые тесты (F9-F12) + верифицировать F13

### Wave 3 (depend on Wave 2 — gate must pass before pre-push reactivation)
**Файлы:** `core/entrypoints/pre-push-gate.sh`
- TASK-8: Re-enable `pre-push-gate.sh`
- TASK-9: `make gate MODE=fast` + `make fix-gate`

---

## 9. $TEST_SPEC

| Test file | Test function | Scenario | Module under test |
|-----------|---------------|----------|-------------------|
| `tests/unit/test_node_detect.py` | `TestDetectAgeKey::test_from_age_secret_key_env` | AGE_SECRET_KEY env var set → returns key | `node_detect.detect_age_key` |
| `tests/unit/test_node_detect.py` | `TestDetectAgeKey::test_from_sops_age_key_env` | SOPS_AGE_KEY env var set (fallback) → returns key | `node_detect.detect_age_key` |
| `tests/unit/test_node_detect.py` | `TestDetectAgeKey::test_from_file` | AGE_SECRET_KEY_FILE points to file with key → returns key | `node_detect.detect_age_key` |
| `tests/unit/test_node_detect.py` | `TestDetectAgeKey::test_not_found` | No AGE key anywhere → returns None | `node_detect.detect_age_key` |
| `tests/unit/test_node_detect.py` | `TestAutoDetectNodeName::test_single_node` | One valid dir in node-configs → returns name | `node_detect.auto_detect_node_name` |
| `tests/unit/test_node_detect.py` | `TestAutoDetectNodeName::test_multiple_nodes` | Multiple dirs → raises NodeDetectionError | `node_detect.auto_detect_node_name` |
| `tests/unit/test_node_detect.py` | `TestAutoDetectNodeName::test_no_nodes` | No dirs → raises NodeDetectionError | `node_detect.auto_detect_node_name` |
| `tests/unit/test_node_detect.py` | `TestAutoDetectNodeName::test_skips_scripts_secrets` | Dir has scripts/ and secrets/ → they're excluded | `node_detect.auto_detect_node_name` |
| `tests/unit/test_node_detect.py` | `TestCLI::test_detect_age_key_flag` | `--detect-age-key` → key on stdout, exit 0 | `node_detect.__main__` |
| `tests/unit/test_node_detect.py` | `TestCLI::test_detect_node_name_flag` | `--detect-node-name` with tmp dir → name on stdout | `node_detect.__main__` |
| `tests/unit/test_node_detect.py` | `TestCLI::test_detect_age_key_not_found` | `--detect-age-key` with no key → exit 1 | `node_detect.__main__` |

**LDD требования:** `caplog` fixture, IMP:7-10 фильтрация, assertion на IMP:9 log в успешных сценариях.
**Test Honesty:** R1 (нет pass-тестов), R2 (нет unfalsifiable asserts). Все тесты с `assert`.

---

## 10. Risks & Mitigations

| Риск | Вероятность | Влияние | Mitigation |
|------|:-----------:|:-------:|------------|
| R1: `age_key.py` вызывается из мест вне 3 entrypoints (неизвестные потребители) | LOW | MEDIUM — сломанные вызовы | TASK-6 сохраняет CLI-совместимость + Python re-export. Grep перед merge: `rg "age_key" --type sh` + `rg "from age_key import\|import age_key" --type py` по всем файлам. Известные Python-потребители: `decrypt_secrets.py` (L54), `test_age_key.py` (L36) — оба продолжат работать через compat-шим |
| R2: `make gate MODE=fast` красный → TASK-8 блокирует push | MEDIUM | HIGH — нельзя пушить | TASK-9 выполняется ПОСЛЕ TASK-2..TASK-7, ДО TASK-8. Если gate красный — TASK-8 НЕ выполняется, gate остаётся отключённым |
| R3: Отсутствие `python3` на VPS → fail-fast ломает bootstrap | LOW | HIGH — bootstrap не стартует | `python3` — системное требование платформы (нужен для `node_yaml.py`, `preflight.py`, множества других скриптов). Если `python3` нет — платформа уже не работает, это не регрессия 104 |
| R4: dry-run логика сломана после замены shell-функций | MEDIUM | MEDIUM — dry-run не отражает реальность | ⚠️ **Исправление к v1:** Python-вызовы ДОЛЖНЫ выполняться в dry-run — текущее поведение вызывает `detect_age_key()` и `auto_detect_node_name()` ДО dry-run guard (bootstrap.sh:106 и :160 — оба до проверки `$DRY_RUN` на строке :171). Результаты используются в dry-run echo для показа реальных NODE_NAME/DETECTED_AGE_KEY. Замена shell→Python НЕ меняет этот порядок — вызовы остаются ДО dry-run guard. При dry-run вывод покажет реальные значения (как сейчас) |
| R5: AC4 realistic-лимиты всё ещё не достигнуты | LOW | LOW — косметика | Допустимое отклонение ±2 строки. При несоответствии — trimming в отдельном PR |
| R6: Существующие тесты (contract/static) ссылаются на shell-функции `detect_age_key`/`auto_detect_node_name` | HIGH | HIGH — gate красный | F9-F12 покрывают обновление тестов. **Критично:** без этих правок `make gate MODE=fast` упадёт на contract-тестах |

---

## 11. Non-Goals

- ❌ НЕ трогать `core/entrypoint-manifest.yaml` — запись `pre-push-gate.sh` уже корректна
- ❌ НЕ удалять `age_key.py` — остаётся compat-re-export shim
- ❌ НЕ менять поведение `detect_age_key()` (цепочка AGE_SECRET_KEY→SOPS_AGE_KEY→AGE_SECRET_KEY_FILE неизменна)
- ❌ НЕ добавлять Telegram-нотификацию в pre-push-gate.sh
- ❌ НЕ запускать `make generate-manifests` / `make generate-agents-md` (генерированные файлы вне скоупа)

---

## 12. Cross-Dependencies (098-105)

| Plan | Тема | Пересечение с 104 | Статус |
|------|------|-------------------|--------|
| 099 | generate-dev-certs.sh → Python | Нет — другой домен (`core/modules/nginx/`) | ✅ Без конфликта |
| 100 | deploy-modules.sh drift fix | Нет — другой домен (`core/internal/bootstrap/deploy-modules.sh`) | ✅ Без конфликта |
| 101 | remote-cmd.sh → тонкий фасад | Косвенное: 104 меняет bootstrap/converge/node-update.sh, которые source remote-cmd.sh. 101 меняет сам remote-cmd.sh. Файлового пересечения нет (101 не трогает entrypoints) | ✅ Без конфликта |
| 103 | context-promote.sh → Python | Нет — другой entrypoint | ✅ Без конфликта |
| 105 | vps-readiness.sh → Python | Оба добавляют модуль в `shared/AGENTS.md` (+`node_detect.py` vs +`vps_readiness.py`). Возможен merge-конфликт в таблице инвентаря (разные строки — решается тривиально). | ⚠️ Минорный конфликт |

**Рекомендация:** 104 и 105 могут выполняться параллельно. При merge первым идёт любой — второй разрешает конфликт в `shared/AGENTS.md` добавлением своей строки.

---

## QA Review (2026-07-31)

### Проверка полноты покрытия (AC1-AC7)

| AC | Статус | Комментарий |
|----|--------|------------|
| AC1 | ✅ Покрыт | TASK-1 (node_detect.py), TASK-2 (тесты), TASK-10 (миграция legacy-тестов) |
| AC2 | ✅ Покрыт | TASK-3/4/5 — замена shell-функций на python3 -m |
| AC3 | ✅ Покрыт | D3: fail-fast без shell-fallback. При отсутствии python3 → exit 1 |
| AC4 | ⚠️ Скорректирован | Бриф: ≤170/100/100. DevPlan: 174/111/118. Обоснованное отклонение — недостижимо без удаления region-маркеров/dry-run логики. Требуется подтверждение архитектора |
| AC5 | ✅ Покрыт | TASK-8: убрать exit 0, восстановить make gate MODE=fast |
| AC6 | ✅ Покрыт (исправлено) | R4 v1: dry-run guard НЕ добавлен — Python-вызовы выполняются до guard как и shell (сохраняет поведение) |
| AC7 | ✅ Покрыт | TASK-9: make gate MODE=fast зелёный после всех изменений |

### Фактическая точность против кодовой базы

| Утверждение DevPlan | Факт | Вердикт |
|---------------------|------|---------|
| bootstrap.sh:56-69 detect_age_key | ✅ 17 строк (с комментариями 53-69) | Точное попадание для тела функции |
| bootstrap.sh:71-86 auto_detect_node_name | ✅ 16 строк (с комментарием 70-86) | Точное попадание для тела функции |
| converge.sh:54-77 auto_detect_node_name | ✅ 24 строки (тело функции; с region — 47-78) | Точное: DevPlan считает только тело |
| node-update.sh:48-61 detect_age_key | ✅ 14 строк (тело функции; с region — 43-62) | Точное: DevPlan считает только тело |
| pre-push-gate.sh exit 0 на L41, heredoc L43-44,54 | ✅ L41=exit 0, L43-44=heredoc start, L54=heredoc end | Верно |
| age_key.py существует (DevPlan 078) | ✅ 134 строки, detect_age_key() + CLI | Верно |
| node_yaml.py прецедент python3 -m | ✅ bootstrap.sh:119: `python3 -m core.internal.shared.node_yaml` | Верно |
| entrypoint-manifest.yaml:341-346 pre-push-gate | ✅ Строки 341-346, mechanism: pre-push-hook, blocking | Верно |
| LOC: bootstrap.sh 201→174 | Фактически: 201−34(full function blocks) = 167, или 201−30(body only) = 171. DevPlan 174 — в пределах ±7 строк | ⚠️ Завышено на ~3-7 строк |
| LOC: converge.sh 133→111 | Фактически: 133−31(full region) = 102, или 133−24(body) = 109. DevPlan 111 — в пределах | ⚠️ Завышено на ~2-9 строк |
| LOC: node-update.sh 130→118 | Фактически: 130−20(full region) = 110, или 130−14(body) = 116. DevPlan 118 — в пределах | ⚠️ Завышено на ~2-8 строк |
| R4: «Python-вызовы в dry-run НЕ выполняются» | **ЛОЖЬ.** Текущий код: detect_age_key() (bootstrap.sh:160) и auto_detect_node_name() (bootstrap.sh:106) вызываются ДО dry-run guard (:171) | **ИСПРАВЛЕНО в v1** — Python-вызовы выполняются до guard |
| R1 mitigation: grep только .sh | Упущены Python-потребители age_key.py: decrypt_secrets.py (L54) и test_age_key.py (L36) | **ИСПРАВЛЕНО** — расширен grep |

### Инварианты

| Инвариант | Статус | Доказательство |
|-----------|--------|---------------|
| Makefile — единый фасад | ✅ HELD | shell-фасады вызываются через make, python3 -m вызывается shell-фасадом. Цепочка: make → entrypoint.sh → python3 -m |
| Python-first (fail-fast) | ✅ HELD | D3: нет shell-fallback, python3 — single source of truth. При отсутствии python3 → exit 1 |
| Single-source-of-truth | ✅ HELD | 2 функции из 3 файлов → 1 Python-модуль |
| pre-push-gate — блокирующий контракт | ✅ HELD | manifest L341-346 корректен, AC5 восстанавливает блокировку |
| Manifest Generation Contract | ✅ HELD | Non-Goals: НЕ запускать generate-manifests |
| org = context (из пути) | ✅ HELD | Не затрагивается |
| LiteLLM — PostgreSQL | ✅ HELD | Не затрагивается |

### $TEST_SPEC качество

| Критерий | Оценка | Детали |
|----------|--------|--------|
| Native imports | ✅ | node_detect.py импортируется напрямую |
| tmp_path | ✅ | test_single_node, test_multiple_nodes, test_skips_scripts_secrets используют tmp_path |
| LDD caplog IMP:9 | ✅ | Требование явно указано в $TEST_SPEC |
| R1 (нет pass-тестов) | ✅ | Все 11 тестов имеют assert |
| R2 (нет unfalsifiable) | ✅ | Все assert на реальные значения |
| R5 (anti-survivorship) | ✅ | test_detect_age_key_not_found — негативный тест для CLI |
| subprocess для бизнес-логики | ✅ | Нет — тесты вызывают функции напрямую (для node_detect) или через subprocess.run только для CLI-тестов |

### Внесённые поправки (v1)

1. **R4 dry-run logic (CRITICAL):** Исправлено с «Python-вызовы НЕ выполняются» на «Python-вызовы выполняются ДО dry-run guard» — соответствует текущему поведению bootstrap.sh (строки 106, 160 до строки 171). Добавлен F5 в §0 Factual Corrections.
2. **File Manifest — пропущенные тесты (CRITICAL):** Добавлены F9-F13: test_bootstrap_auto.py, test_contract_deploy_ssh.py, test_contract_entrypoints.py, test_node_lifecycle_static.py, test_age_key.py (верификация). Без этих правок `make gate MODE=fast` упадёт.
3. **TASK-10 (CRITICAL):** Добавлена задача миграции затронутых тестов в Wave 2.
4. **R1 mitigation:** Расширен grep на Python-файлы (`from age_key import`), добавлен список известных Python-потребителей.
5. **D1:** Уточнено — compat-шим нужен не только для shell CLI, но и для Python-импортов (decrypt_secrets.py).
6. **P3:** Добавлено упоминание Python-потребителя decrypt_secrets.py.
7. **§12 Cross-Dependencies:** Добавлен анализ планов 099-105, включая минорный конфликт с 105 (shared/AGENTS.md).
8. **Code Graph:** Добавлен `legacy_tests` entity для учёта модифицируемых тестовых файлов.

### Оставшиеся риски

| Риск | Severity | Описание |
|------|----------|----------|
| AC4 отклонение | MEDIUM | Бриф ≤170/100/100 → DevPlan 174/111/118. Требуется подтверждение архитектора (возможно отдельный бриф на trimming) |
| LOC арифметика | LOW | Оценки DevPlan завышены на 2-9 строк. Реальный код будет на 2-9 строк короче — это хорошо, но может сбить ожидания |
| merge-конфликт shared/AGENTS.md | LOW | Plan 105 добавляет vps_readiness.py в ту же таблицу. Тривиально разрешим |
| test_contract_deploy_ssh.py — subprocess для auto_detect_node_name | MEDIUM | 4 теста используют `_extract_func(BOOTSTRAP_SH, "auto_detect_node_name")` и выполняют shell-функцию. После миграции: либо заменить на subprocess.run python3 -m, либо удалить (TRAP[TEST] говорит «Remove if: auto_detect_node_name is removed»). Рекомендация: удалить — coverage дублируется test_node_detect.py |
| test_bootstrap_auto.py — 3 detect_age_key теста | MEDIUM | Аналогично: shell-вызов detect_age_key → subprocess.run python3 -m. Рекомендация: заменить вызов на python3 -m, сохранить assertion-логику |

### Финальный вердикт

**APPROVED-WITH-CORRECTIONS** — DevPlan покрывает все AC брифа, инварианты HELD, кросс-зависимости учтены. Внесены 8 поправок, устраняющих 2 CRITICAL проблемы (dry-run логика + пропущенные тесты). После правок DevPlan готов к реализации.

---

## Next Steps

### Wave 1
```
coder Read .ai/plans/104-dedup-entrypoints/02-DevPlan.md, implement Wave 1: TASK-1
```

### Wave 2
```
coder Read .ai/plans/104-dedup-entrypoints/02-DevPlan.md, implement Wave 2: TASK-2, TASK-3, TASK-4, TASK-5, TASK-6, TASK-7, TASK-10
```

### Wave 3
```
coder Read .ai/plans/104-dedup-entrypoints/02-DevPlan.md, implement Wave 3: TASK-8, TASK-9
```

$END_DEVPLAN
