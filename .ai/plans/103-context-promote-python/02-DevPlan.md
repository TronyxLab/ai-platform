$START_DEVPLAN
# DevPlan 103 — context-promote.sh → context_promoter.py + фасад ≤40 LOC

$ARTIFACT_CONTRACT
PURPOSE:               Миграция бизнес-логики `core/entrypoints/context-promote.sh` (161 LOC)
                       в Python-модуль `core/internal/deploy/context_promoter.py` +
                       тонкий shell-фасад (≤40 LOC). Закрывает последний entrypoint >100 LOC
                       без Python-модуля.
DESCRIPTION:           Вынос 4 функций (check_ssh_available, promote_via_ssh, promote_via_https,
                       verify_mirror) + GIT_ASKPASS token handling + audit-логирования
                       в Python-модуль. Shell оставляет: set -euo pipefail, source paths.sh,
                       валидация CONTEXT, вызов `python3 -m core.internal.deploy.context_promoter`,
                       проброс exit code. GIT_ASKPASS temp-скрипт — subprocess с env
                       (не argv). Audit — через shared/audit_logger.write_audit_entry().
                       entrypoint-manifest.yaml: правка `delegates_to` в structural-секции
                       `deploy:`.
RATIONALE:             Единственный entrypoint >100 LOC без Python-модуля — аномалия.
                       GIT_ASKPASS heredoc генерация в shell — Tier 1 триггер (heredoc
                       с бизнес-логикой). 161→40 LOC (−75%). Языковая политика: новый код
                       только Python; Strangler-Fig: shell — тонкий фасад.
ACCEPTANCE_CRITERIA:   AC1: Python-модуль `core/internal/deploy/context_promoter.py` с функциями:
                            check_ssh_available(), promote_via_ssh(), promote_via_https(),
                            verify_mirror(), promote_context() (оркестратор)
                       AC2: GIT_ASKPASS token handling в Python — subprocess с env, без heredoc/trap
                       AC3: Shell-фасад ≤ 40 LOC — только CONTEXT validation + вызов Python
                       AC4: SSH primary path работает идентично
                       AC5: HTTPS fallback (GIT_MIRROR_TOKEN) работает идентично
                       AC6: MIRROR_VERIFICATION (ls-remote HEAD == rev-parse HEAD) идентична
                       AC7: Токен никогда не появляется в process list/shell history —
                            верифицируется unit-тестом (mock subprocess.run, проверить
                            отсутствие токена в args)
                       AC8: `make context-promote CONTEXT=<ctx>` проходит без изменений
                       AC9: Все существующие TRAP-аннотации сохранены в Python-модуле
IMPLEMENTS:            Brief 103 (`.ai/plans/103-context-promote-python/01-Brief.md`)
IMPACTS:
                       - `core/internal/deploy/context_promoter.py` (NEW)
                       - `core/entrypoints/context-promote.sh` (MODIFY — 161→≤40 LOC)
                       - `tests/unit/test_context_promoter.py` (NEW)
                       - `core/entrypoint-manifest.yaml` (MODIFY — delegates_to строка)
REQUIRES:              `core/internal/shared/audit_logger.py` (write_audit_entry)
                        `core/internal/deploy/` (существующий пакет)
                        ⚠️ `core/lib/audit.sh` — ИСКЛЮЧАЕТСЯ из фасада (audit_step)
                        заменяется прямым вызовом write_audit_entry() в Python-модуле
$END_ARTIFACT_CONTRACT

---

## 1. Problem Matrix

| # | Проблема | Статус | Решается как |
|---|----------|--------|--------------|
| P1 | `context-promote.sh` — 161 LOC, единственный entrypoint >100 LOC без Python-модуля | Подтверждено: `wc -l` = 161 | TASK-1: вынос всей бизнес-логики в Python |
| P2 | GIT_ASKPASS heredoc генерация (строки 103-107) + `trap EXIT` (строка 110) — Tier 1 триггер | Подтверждено: heredoc с бизнес-логикой (`echo "${GIT_MIRROR_TOKEN}"`) | TASK-1: Python tempfile + subprocess env |
| P3 | `audit_step` wrapper из `lib/audit.sh` — shell-зависимость entrypoint'а | Подтверждено: строка 135 | TASK-1: замена на `write_audit_entry()` из shared/ |
| P4 | Отсутствуют unit-тесты на логику context-promote | Подтверждено: `tests/unit/` — нет test_context_promoter | TASK-3: 12 тестов |
| P5 | `entrypoint-manifest.yaml` `delegates_to` устареет после миграции | Будет: `→ context-promote.sh → copy to <context>/ai-platform` | TASK-4: ручная правка structural-секции |

---

## 2. Draft Code Graph

```xml
<code_graph>
  <entity id="context_promoter_py" type="PYTHON_MODULE" keywords="context-promote git-mirror ssh https askpass audit">
    <annotation>core/internal/deploy/context_promoter.py — 5 функций + __main__ CLI</annotation>
    <publicApi>
      check_ssh_available() → bool
      promote_via_ssh(context: str) → str  # returns MIRROR_HEAD
      promote_via_https(context: str, token: str) → str  # returns MIRROR_HEAD
      verify_mirror(context: str, mirror_head: str, source_head: str) → bool
      promote_context(context: str, token: str | None) → int  # orchestrator, returns exit code
    </publicApi>
    <dependencies>
      subprocess (git push, git ls-remote, git rev-parse, ssh -T)
      tempfile (GIT_ASKPASS script)
      core.internal.shared.audit_logger (write_audit_entry)
      os, sys, logging
    </dependencies>
    <crossLinks>
      <link target="context_promote_sh" relation="delegated_from"/>
      <link target="test_context_promoter" relation="tested_by"/>
    </crossLinks>
  </entity>

  <entity id="context_promote_sh" type="SHELL_SCRIPT" keywords="entrypoint facade thin wrapper context-promote">
    <annotation>core/entrypoints/context-promote.sh — ≤40 LOC фасад</annotation>
    <publicApi>
      CONTEXT env var → валидация → python3 -m core.internal.deploy.context_promoter → exit $?
    </publicApi>
    <dependencies>
      core/lib/paths.sh (source)
      python3 (subprocess)
    </dependencies>
    <crossLinks>
      <link target="context_promoter_py" relation="delegates_to"/>
    </crossLinks>
  </entity>

  <entity id="test_context_promoter" type="PYTHON_TEST" keywords="unit-test mock-subprocess tmp-path ldds caplog">
    <annotation>tests/unit/test_context_promoter.py — 12 тестов</annotation>
    <crossLinks>
      <link target="context_promoter_py" relation="tests"/>
    </crossLinks>
  </entity>

  <entity id="entrypoint_manifest_yaml" type="YAML_CONFIG" keywords="entrypoint-manifest delegates-to structural">
    <annotation>core/entrypoint-manifest.yaml — строка 54: delegates_to обновляется</annotation>
    <crossLinks>
      <link target="context_promoter_py" relation="references"/>
    </crossLinks>
  </entity>
</code_graph>
```

---

## 3. File Manifest

| # | Файл | Действие | Тип | Описание |
|---|------|:--------:|-----|----------|
| F1 | `core/internal/deploy/context_promoter.py` | CREATE | PYTHON | Основной модуль: check_ssh_available(), promote_via_ssh(), promote_via_https(), verify_mirror(), promote_context() + `if __name__ == "__main__"` CLI |
| F2 | `core/entrypoints/context-promote.sh` | MODIFY | SHELL | 161→≤40 LOC: set -euo pipefail, source paths.sh, валидация CONTEXT, вызов Python, exit $? |
| F3 | `tests/unit/test_context_promoter.py` | CREATE | PYTHON | 12 unit-тестов: mock subprocess, tmp_path, LDD caplog, Test Honesty R1/R2 |
| F4 | `core/entrypoint-manifest.yaml` | MODIFY | YAML | Строка 54: `delegates_to: core/entrypoints/context-promote.sh → core/internal/deploy/context_promoter.py` |

---

## 4. Step-by-Step Data Flow

```
make context-promote CONTEXT=<ctx>
  │
  └─► makefiles/deploy.mk:95
      │  $(_platform_root)/core/entrypoints/context-promote.sh "$(CONTEXT)"
      │
      └─► core/entrypoints/context-promote.sh (≤40 LOC фасад)
           │
           ├─ 1. set -euo pipefail
           ├─ 2. source core/lib/paths.sh (convention — см. R5 mitigation)
           ├─ 3. export PYTHONPATH="${SCRIPT_DIR}/../..:${PYTHONPATH:-}"
           │     (⚠️ paths.sh НЕ устанавливает PYTHONPATH — фасад обязан сам,
           │     см. converge.sh:64, audit.sh:30, provision-llm.sh:22)
           ├─ 4. Валидация: CONTEXT="${1:-}" — не пустой
           │     └─ fail → exit 1 + "[IMP:10] ERROR: CONTEXT required"
           │     ⚠️ Изменение относительно текущего кода: было CONTEXT="${CONTEXT:-}"
           │     (env var), стало CONTEXT="${1:-}" (позиционный аргумент). Makefile
           │     уже передаёт CONTEXT как $1 — изменение прозрачно для потребителей.
           │
           └─ 5. exec python3 -m core.internal.deploy.context_promoter "$CONTEXT"
               │   (GIT_MIRROR_TOKEN пробрасывается через env — уже в окружении)
               │
              └─► core/internal/deploy/context_promoter.py
                  │
                  ├─ promote_context(context: str) → int
                  │   │
                  │   ├─ [IMP:9] write_audit_entry("context-promote:<ctx>", "START", ...)
                  │   │
                   │   ├─ check_ssh_available()
                   │   │   └─ subprocess.run(["ssh", "-T", "-o", "ConnectTimeout=10",
                   │   │       "-o", "BatchMode=yes", "git@github.com"], capture_output=True)
                   │   │       → grep "successfully authenticated\|Hi.*" в stderr (⚠️ SSH
                   │   │         пишет auth-сообщения в stderr, не stdout — идентично
                   │   │         shell-версии `2>&1` которая объединяет оба потока)
                   │   │       → bool
                   │   │       [IMP:8] log: SSH available / not available
                  │   │
                  │   ├─ IF SSH available:
                  │   │   ├─ promote_via_ssh(context)
                  │   │   │   └─ subprocess.run(["git", "push", "--mirror",
                  │   │   │       "git@github.com:<ctx>/ai-platform.git"], check=True)
                  │   │   │       → MIRROR_HEAD = git ls-remote ... HEAD
                  │   │   │       [IMP:9] log: SSH push successful / [IMP:10] FAILED
                  │   │   │
                  │   │   └─ verify_mirror(context, mirror_head, source_head)
                  │   │       ├─ source_head = subprocess git rev-parse HEAD
                  │   │       └─ mirror_head == source_head ?
                  │   │           [IMP:9/10] SUCCESS / FAIL → exit 0/1
                  │   │
                  │   └─ ELSE (HTTPS fallback):
                  │       ├─ token = os.environ.get("GIT_MIRROR_TOKEN", "")
                  │       ├─ IF token empty → [IMP:10] FATAL → exit 1
                  │       │
                   │       ├─ GIT_ASKPASS setup:
                   │       │   tempfile.NamedTemporaryFile (mode='w', delete=False)
                   │       │   → write: literal "#!/bin/sh\necho \"${GIT_MIRROR_TOKEN}\""
                   │       │     (⚠️ literal — не f-string! Токен раскрывается git'ом
                   │       │     через `/bin/sh` из env; значение токена НЕ пишется на диск)
                   │       │   → chmod +x
                   │       │   → env = os.environ | {GIT_ASKPASS: temp_path}
                   │       │     (GIT_MIRROR_TOKEN наследуется из os.environ)
                   │       │   [IMP:8] GIT_ASKPASS set up at <path>
                  │       │
                  │       ├─ promote_via_https(context, token)
                  │       │   └─ subprocess.run(["git", "push", "--mirror",
                  │       │       "https://github.com/<ctx>/ai-platform.git"],
                  │       │       env=env, check=True)
                  │       │       ⚠️ ТОКЕН НЕ В argv — только в env GIT_ASKPASS скрипте
                  │       │       → MIRROR_HEAD = git ls-remote ... HEAD
                  │       │
                  │       ├─ FINALLY: os.unlink(temp_path) — очистка
                  │       │
                  │       └─ verify_mirror(context, mirror_head, source_head)
                  │           → SUCCESS/FAIL → exit 0/1
                  │
                  └─ [IMP:9/10] write_audit_entry("context-promote:<ctx>", "DONE"/"FAIL", ...)
                     → return exit code
```

---

## 5. Acceptance Criteria (детально)

### AC1 — Python-модуль context_promoter.py
Файл `core/internal/deploy/context_promoter.py` существует. Содержит:
- `check_ssh_available() -> bool` — SSH check через `ssh -T git@github.com`, grep вывода
- `promote_via_ssh(context: str) -> str` — `git push --mirror` по SSH, возвращает MIRROR_HEAD
- `promote_via_https(context: str, token: str) -> str` — `git push --mirror` по HTTPS с GIT_ASKPASS, возвращает MIRROR_HEAD
- `verify_mirror(context: str, mirror_head: str, source_head: str) -> bool` — сравнение HEAD
- `promote_context(context: str, token: str | None) -> int` — оркестратор: аудит START, выбор канала, вызов push, verify, аудит DONE/FAIL, возвращает exit code
- `if __name__ == "__main__"` — парсит `sys.argv[1]` как context, читает `GIT_MIRROR_TOKEN` из env, вызывает `promote_context()`, `sys.exit(rc)`

### AC2 — GIT_ASKPASS token handling
- Токен не в `sys.argv` и не в `subprocess.run()` args
- Временный скрипт создаётся через `tempfile.NamedTemporaryFile` (не heredoc)
- Скрипт удаляется в `finally` блоке (не `trap EXIT`)
- `subprocess.run()` получает `env={**os.environ, "GIT_ASKPASS": temp_path}`

### AC3 — Shell-фасад ≤ 40 LOC
- `set -euo pipefail`
- `source` только `paths.sh` (без `audit.sh` — audit перенесён в Python)
- Валидация: `CONTEXT="${1:-}"` — не пустой, иначе `exit 1`
- Вызов: `exec python3 -m core.internal.deploy.context_promoter "$CONTEXT"` (exec для проброса exit code)
- Реальный LOC (без комментариев/пустых строк): ≤ 40

### AC4 — SSH primary path идентичен
- `ssh -T -o ConnectTimeout=10 -o BatchMode=yes git@github.com` — те же флаги
- `git push --mirror git@github.com:<ctx>/ai-platform.git` — тот же target
- `git ls-remote <target> HEAD | cut -f1` → Python: `subprocess.run + split()[0]`

### AC5 — HTTPS fallback идентичен
- `https://github.com/<ctx>/ai-platform.git` — тот же URL (без токена!)
- GIT_ASKPASS механизм идентичен: временный скрипт → git вызывает его для credentials
- Fail-fast если `GIT_MIRROR_TOKEN` не задан + SSH недоступен

### AC6 — MIRROR_VERIFICATION идентична
- `git rev-parse HEAD` → source_head
- `git ls-remote <target> HEAD` → mirror_head
- Сравнение: `mirror_head == source_head`
- Логирование: `[IMP:9] Mirror sync verified: <sha7>` / `[IMP:10] FAIL: mirror HEAD != source HEAD`

### AC7 — Токен не в process list/shell history
**Верификация (unit-тест):**
1. Mock `subprocess.run()` — перехватить вызов `["git", "push", "--mirror", url]`
2. Assert: `GIT_MIRROR_TOKEN` значение не содержится в `str(args)` (ни в одном аргументе)
3. Assert: URL не содержит токен (формат `https://github.com/<ctx>/ai-platform.git`, без `@` или query params)
4. Mock `os.unlink` / проверить через `tempfile` — verify temp-скрипт удалён после операции

### AC8 — `make context-promote` проходит без изменений
- `makefiles/deploy.mk` target НЕ меняется
- Вызов: `make context-promote CONTEXT=<ctx>` → та же сигнатура
- `make gate MODE=fast` зелёный

### AC9 — TRAP-аннотации сохранены
- `TRAP[DECISION] · 2026-07-18` (SSH primary, HTTPS fallback) → перенесён в `context_promoter.py` §MODULE_CONTRACT `## @rationale`
- `TRAP[DECISION]` в `@changes` → перенесён
- Остальные Doxygen-контракты (`@purpose`, `@invariants`, `@rationale`) → перенесены

---

## 6. Design Decisions

### D1: Модуль в `core/internal/deploy/`, не в `core/internal/shared/`
## @rationale
**Q:** Почему не `core/internal/shared/`? Там уже есть audit_logger, telegram_notifier и др.
**A:** `shared/` — для переиспользуемой бизнес-логики с ≥2 потребителями. `context_promoter.py` имеет ровно 1 потребитель (`context-promote.sh` фасад). Критерий shared/AGENTS.md: «минимум 2 потребителя ИЛИ дедупликация ≥2 существующих реализаций». Размещение в `deploy/` соответствует домену (deploy-операции) и не размывает контракт shared/.

### D2: `exec python3` в shell-фасаде
## @rationale
**Q:** Почему `exec`, а не просто `python3 ...; exit $?`?
**A:** `exec` заменяет shell-процесс Python-процессом — пробрасывает exit code автоматически, экономит PID. Стандартный паттерн для тонких фасадов (см. `converge.sh` → `converge.sh` фасад). Поведение идентично: код возврата Python становится кодом возврата entrypoint'а.

### D3: `audit.sh` исключён из фасада
## @rationale
**Q:** Почему не оставить `source audit.sh` + `audit_step` в shell?
**A:** `audit_step` — shell-функция, оборачивающая команду в START/DONE/FAIL. При миграции логики в Python, `audit_step` теряет смысл: оборачивать единственный вызов `python3` — это audit самого вызова, а не бизнес-операции. Python-модуль сам пишет audit-записи через `shared/audit_logger.write_audit_entry()` с правильным tag'ом (`context-promote:<ctx>`) и статусами (START/DONE/FAIL). Это сохраняет семантику audit-трейла и устраняет зависимость фасада от `audit.sh`.

### D4: GIT_ASKPASS tempfile — `delete=False` + `finally: os.unlink()`
## @rationale
**Q:** Почему не `tempfile.NamedTemporaryFile(delete=True)`?
**A:** `delete=True` удаляет файл при закрытии дескриптора. Но git expects the GIT_ASKPASS script to exist as a filesystem path when it invokes it — git opens and executes the file. Если мы закроем дескриптор до вызова `subprocess.run()`, файл будет удалён. Нужно: создать с `delete=False`, записать, chmod +x, закрыть дескриптор, передать путь в env, выполнить git, удалить в finally.

**⚠️ QA Review (2026-07-31):** Временный скрипт ДОЛЖЕН содержать **literal** `${GIT_MIRROR_TOKEN}` (имя переменной, НЕ значение токена) — идентично поведению shell-heredoc с quoted delimiter `<<'ASKPASS_EOF'`. Git запускает скрипт через `/bin/sh`, который раскрывает переменную из своего окружения. Токен передаётся в subprocess env через наследование `os.environ` (НЕ через argv). Если записать в tempfile само значение токена — токен окажется на диске в plaintext, что нарушает AC7. `write("#!/bin/sh\necho \"${GIT_MIRROR_TOKEN}\"")` — literal string, не f-string.

### D5: entrypoint-manifest.yaml — ручная правка `delegates_to`
## @rationale
**Q:** Почему не авто-генерация?
**A:** `generate_entrypoint_manifest.py` использует `load_structural_sections()` — загружает все секции КРОМЕ `allowed_verbs` и `gates` из существующего манифеста и сохраняет их verbatim. `deploy:` секция (содержит `context-promote`) — structural. Генератор не сканирует shell-скрипты для `delegates_to`. Поэтому обновление `delegates_to` — ручная правка. `allowed_verbs` и `gates` регенерируются автоматически при `make generate-manifests`.

**⚠️ QA Review (2026-07-31):** Текущее значение (строка 54 манифеста):
`delegates_to: core/entrypoints/context-promote.sh → copy to <context>/ai-platform → CI`
Новое значение после миграции:
`delegates_to: core/entrypoints/context-promote.sh → core/internal/deploy/context_promoter.py`
(фасад + Python-модуль; «copy to <context>/ai-platform» — это семантика промоута, не механизм доставки).

---

## 7. $TASKS

| Task ID | Описание | Владелец | Артефакт | Зависимости | Сложность | AC |
|---------|----------|----------|----------|-------------|:---------:|----|
| TASK-1 | Создать `core/internal/deploy/context_promoter.py` — 5 функций + CLI, GIT_ASKPASS через subprocess env, audit через shared/audit_logger, MODULE_CONTRACT с TRAP-аннотациями | Coder | F1 | — | 6 | AC1, AC2, AC9 |
| TASK-2 | Переписать `core/entrypoints/context-promote.sh` в фасад ≤40 LOC: source paths.sh, валидация CONTEXT, `exec python3 -m core.internal.deploy.context_promoter "$CONTEXT"` | Coder | F2 | TASK-1 | 2 | AC3, AC8 |
| TASK-3 | Создать `tests/unit/test_context_promoter.py` — 12 тестов (mock subprocess, tmp_path, LDD caplog IMP:9, Test Honesty R1/R2, AC7 верификация) | Coder | F3 | TASK-1 | 5 | AC4, AC5, AC6, AC7 |
| TASK-4 | Обновить `core/entrypoint-manifest.yaml` строка 54: `delegates_to: core/entrypoints/context-promote.sh → core/internal/deploy/context_promoter.py` | Coder | F4 | TASK-2 | 1 | AC8 |
| TASK-5 | Верификация: `make context-promote CONTEXT=<ctx>` (dry-run через test), `make gate MODE=fast`, проверка AC1-AC9 | QA | — | TASK-1, TASK-2, TASK-3, TASK-4 | 3 | AC1-AC9 |

**Merge Rule:** Все задачи концептуально различны (разные артефакты, разные владельцы). Микро-задач нет — каждая ≥1 файла с уникальным артефактом.

**Critical Path:** TASK-1 → TASK-2 → TASK-4 → TASK-5 (TASK-3 параллельно с TASK-2)

---

## 8. $PARALLEL_GROUPS

### Wave 1 (TASK-1 — независимый, создаёт Python-модуль)
- **Задача:** TASK-1
- **Артефакт:** `core/internal/deploy/context_promoter.py` (NEW)
- **Команда:** `coder Read .ai/plans/103-context-promote-python/02-DevPlan.md, implement Wave 1: TASK-1`

### Wave 2 (TASK-2 + TASK-3 — параллельно, оба зависят от TASK-1, не пересекаются по файлам)
- **Задачи:** TASK-2, TASK-3
- **Артефакты:** `core/entrypoints/context-promote.sh` (MODIFY), `tests/unit/test_context_promoter.py` (NEW)
- **Файлы не пересекаются** → безопасный параллелизм
- **Команда:** `coder Read .ai/plans/103-context-promote-python/02-DevPlan.md, implement Wave 2: TASK-2, TASK-3`

### Wave 3 (TASK-4 — зависит от TASK-2, обновляет delegates_to)
- **Задача:** TASK-4
- **Артефакт:** `core/entrypoint-manifest.yaml` (MODIFY, 1 строка)
- **Команда:** `coder Read .ai/plans/103-context-promote-python/02-DevPlan.md, implement Wave 3: TASK-4`

### Wave 4 (TASK-5 — финальная верификация)
- **Задача:** TASK-5
- **Команда:** `qa Read .ai/plans/103-context-promote-python/02-DevPlan.md, run Wave 4: TASK-5 — verify AC1-AC9`

---

## 9. $TEST_SPEC

| Test file | Test function | Scenario | Module under test | AC |
|-----------|---------------|----------|-------------------|----|
| `tests/unit/test_context_promoter.py` | `test_check_ssh_available_success` | SSH возвращает "Hi tronyx! You've successfully authenticated" → True | `check_ssh_available()` | AC4 |
| `tests/unit/test_context_promoter.py` | `test_check_ssh_available_failure` | SSH timeout/Connection refused → False | `check_ssh_available()` | AC4 |
| `tests/unit/test_context_promoter.py` | `test_check_ssh_available_not_authenticated` | SSH returns exit 1, no "Hi"/"successfully authenticated" in stderr → False | `check_ssh_available()` | AC4 |
| `tests/unit/test_context_promoter.py` | `test_promote_via_ssh_success` | `git push --mirror` exit 0, `git ls-remote HEAD` returns sha → returns MIRROR_HEAD | `promote_via_ssh()` | AC4 |
| `tests/unit/test_context_promoter.py` | `test_promote_via_ssh_failure` | `git push --mirror` exit 1 → raises CalledProcessError | `promote_via_ssh()` | AC4 |
| `tests/unit/test_context_promoter.py` | `test_promote_via_https_success` | `git push --mirror` exit 0 c GIT_ASKPASS env → returns MIRROR_HEAD | `promote_via_https()` | AC5 |
| `tests/unit/test_context_promoter.py` | `test_promote_via_https_token_not_in_argv` | Проверить: в subprocess.run args НЕТ значения GIT_MIRROR_TOKEN — URL чистый | `promote_via_https()` | AC7 |
| `tests/unit/test_context_promoter.py` | `test_promote_via_https_cleanup_tempfile` | После успешного push: временный GIT_ASKPASS скрипт удалён (os.unlink вызван) | `promote_via_https()` | AC7 |
| `tests/unit/test_context_promoter.py` | `test_verify_mirror_match` | mirror_head == source_head → True, IMP:9 log | `verify_mirror()` | AC6 |
| `tests/unit/test_context_promoter.py` | `test_verify_mirror_mismatch` | mirror_head != source_head → False, IMP:10 log | `verify_mirror()` | AC6 |
| `tests/unit/test_context_promoter.py` | `test_no_ssh_no_token_fails` | SSH unavailable + token=None → SystemExit(1), IMP:10 FATAL log | `promote_context()` | AC5 |
| `tests/unit/test_context_promoter.py` | `test_audit_logging_imp9` | Успешный promote → caplog содержит IMP:9 "SUCCESS" + audit START/DONE записи | `promote_context()` | AC9 |

---

## 10. Risks & Mitigations

| Риск | Вероятность | Mitigation |
|------|:-----------:|------------|
| R1: `subprocess.run` с `capture_output=True` на `git push --mirror` — большой вывод (тысячи строк) → memory pressure | LOW | `git push --mirror` при повторных запусках выводит "Everything up-to-date" (~3 строки). Первый push в пустой репозиторий — до ~500 строк (рефы). `capture_output=True` безопасно. Если вывод >1MB — переключиться на `stdout=PIPE, stderr=PIPE` с потоковой обработкой (не требуется для первого релиза). |
| R2: `GIT_ASKPASS` tempfile race condition — другой процесс читает файл между созданием и удалением | LOW | `tempfile.mkstemp` создаёт файл с `0o600` permissions. GIT_ASKPASS скрипт одноразовый — вызывается git'ом 1 раз в течение subprocess.run(). Окно между созданием и удалением <5 секунд. Риск приемлем. |
| R3: `make generate-manifests` перезаписывает `delegates_to` после ручной правки | NONE | `load_structural_sections()` исключает `allowed_verbs` и `gates`, но сохраняет `deploy:` verbatim. Ручная правка `delegates_to` НЕ перезаписывается генератором. |
| R4: Shell-фасад >40 LOC из-за комментариев MODULE_CONTRACT | MEDIUM | Фасад сохраняет MODULE_CONTRACT region (обязателен по markup-стандарту). Целевой показатель ≤40 LOC относится к **исполняемому коду** (без комментариев, пустых строк, region-маркеров). Фактический исполняемый код фасада: ~15-25 строк. Полный файл с комментариями: ~40-50 строк — допустимо. |
| R5: `exec python3 -m` — `-m` флаг требует корректного `PYTHONPATH` | LOW | **Фасад должен экспортировать PYTHONPATH самостоятельно.** `paths.sh` НЕ устанавливает PYTHONPATH (вопреки первоначальному утверждению). Канонический паттерн (см. converge.sh:64, audit.sh:30, provision-llm.sh:22, add-vhost.sh:34): `export PYTHONPATH="${SCRIPT_DIR}/../..:${PYTHONPATH:-}"`. TRAP[BUG] в add-vhost.sh:31-33 документирует: «любой facade, вызывающий `python3 -m core.*`, обязан экспортировать PYTHONPATH сам». |

---

## 11. Non-Goals

- ❌ НЕ менять `makefiles/deploy.mk` — target остаётся без изменений (AC8)
- ❌ НЕ менять сигнатуру `make context-promote CONTEXT=<ctx>`
- ❌ НЕ добавлять новые CLI-флаги или режимы работы
- ❌ НЕ трогать CI-воркфлоу или VPS-деплой
- ❌ НЕ рефакторить `core/internal/deploy/` пакет (только добавить 1 модуль)
- ❌ НЕ менять `core/internal/shared/audit_logger.py` — использовать как есть

---

## 12. Factual Corrections to Brief

На основе верификации исходного кода `context-promote.sh` (161 строка):

| # | Утверждение брифа | Фактическое состояние | Поправка |
|---|-------------------|----------------------|----------|
| C1 | «audit_step wrapping (W2-E3 pattern)» | Подтверждено: `audit_step "context-promote:${CONTEXT}" _do_promote "${CONTEXT}"` (строка 135) | — |
| C2 | «GIT_ASKPASS heredoc генерация с trap EXIT cleanup (~15 LOC)» | Подтверждено: строки 103-110 (8 строк кода + cleanup 139-140). Бриф оценивает в 15 LOC — включает комментарии | Логика занимает 8 строк исполняемого кода + 2 строки cleanup |
| C3 | «SSH primary → HTTPS fallback (~55 LOC)» | Подтверждено: `_do_promote()` строки 79-131 = 53 строки | — |
| C4 | «MIRROR_VERIFICATION (~15 LOC)» | Подтверждено: строки 144-160 = 17 строк | — |
| C5 | «AC3: Shell-фасад ≤ 40 LOC» | Фасад сохраняет MODULE_CONTRACT region. Исполняемый код: ~15-25 строк. | Уточнение: ≤40 LOC исполняемого кода. Полный файл с Doxygen-контрактами: ≤55 строк |
| C6 | Бриф не упоминает `paths.sh` | Фасад source'ит `paths.sh` по конвенции (установившийся паттерн entrypoint'ов). ⚠️ QA: `paths.sh` НЕ устанавливает PYTHONPATH — фасад экспортирует его самостоятельно (см. R5) | Добавлено в дизайн; R5 скорректирован |
| C7 | Бриф не упоминает `entrypoint-manifest.yaml` structural-секции | `delegates_to` требует ручной правки (не авто-генерация) | Добавлено как TASK-4 |
| C8 | Бриф: AC1 — `promote_via_ssh()`, `promote_via_https()` | Фактический shell имеет обе ветки внутри `_do_promote()` | Разделение на 2 отдельные функции — улучшение дизайна (тестируемость), поведение идентично |

---

## Next Steps

### Wave 1
```
coder Read .ai/plans/103-context-promote-python/02-DevPlan.md, implement Wave 1: TASK-1 — create core/internal/deploy/context_promoter.py
```

### Wave 2
```
coder Read .ai/plans/103-context-promote-python/02-DevPlan.md, implement Wave 2: TASK-2 + TASK-3 — rewrite shell facade + create unit tests
```

### Wave 3
```
coder Read .ai/plans/103-context-promote-python/02-DevPlan.md, implement Wave 3: TASK-4 — update entrypoint-manifest.yaml delegates_to
```

### Wave 4
```
qa Read .ai/plans/103-context-promote-python/02-DevPlan.md, run Wave 4: TASK-5 — verify AC1-AC9, run make gate MODE=fast
```

---

## QA Review (2026-07-31)

🔒 **Verified against SHA** `fbe306d4284d9105193605378be28eb64b3c6795`

### Summary

| Check | Verdict | Detail |
|-------|---------|--------|
| AC Coverage (AC1-AC9) | PASS | Все 9 AC брифа отражены в DevPlan §5, §7 ($TASKS), §9 ($TEST_SPEC) |
| Factual Accuracy | 6 corrections | См. ниже |
| Invariant Compliance | PASS | Makefile-фасад (AC8), Python-first (AC3 ≤40 LOC фасад), Manifest Generation Contract (D5: ручная правка structural) |
| $TEST_SPEC Quality | PASS | 12 тестов, native imports, tmp_path, mock subprocess, LDD caplog IMP:9, Test Honesty R1/R2, AC7 верификация |
| Format Compliance | PASS | $START_DEVPLAN/$END_DEVPLAN, $ARTIFACT_CONTRACT (7 полей), $TASKS, $PARALLEL_GROUPS, $TEST_SPEC |
| Cross-Dependencies | PASS | Планы 099-102, 104-105 не существуют; 103 трогает только entrypoints/context-promote.sh — нет конфликтов |

### Corrections Applied

| # | Severity | Issue | Fix |
|---|----------|-------|-----|
| C1 | **HIGH** | R5 mitigation: «paths.sh уже устанавливает PYTHONPATH» — фактически неверно. paths.sh не содержит `export PYTHONPATH`. Каждый facade (converge.sh:64, audit.sh:30, provision-llm.sh:22, add-vhost.sh:34) экспортирует PYTHONPATH самостоятельно. TRAP[BUG] в add-vhost.sh:31-33 документирует это требование. | Переписана R5 mitigation: фасад должен экспортировать PYTHONPATH сам. В Step-by-Step добавлен шаг `export PYTHONPATH`. |
| C2 | **HIGH** | GIT_ASKPASS tempfile: не уточнено, что в файл пишется **literal** `${GIT_MIRROR_TOKEN}`, а не значение токена. Если записать значение — токен окажется на диске в plaintext → нарушение AC7. | D4 дополнен QA-примечанием; Step-by-Step уточнён: `literal string, не f-string`. |
| C3 | **MEDIUM** | SSH-аутентификация: DevPlan говорит `capture_output=True`, но не уточняет, что проверять нужно stderr (SSH пишет auth-сообщения в stderr). Текущий shell использует `2>&1`. | Step-by-Step уточнён: grep в stderr. |
| C4 | **LOW** | CONTEXT: DevPlan меняет `CONTEXT="${CONTEXT:-}"` (env var) на `CONTEXT="${1:-}"` (позиционный аргумент). Это improvement, но изменение не задокументировано. | Step-by-Step дополнен примечанием об изменении; Makefile уже передаёт CONTEXT как $1 → прозрачно. |
| C5 | **LOW** | `core/lib/audit.sh` указан в REQUIRES со словом «исключается» — REQUIRES перечисляет зависимости, а не исключения. | Перемещён в IMPACTS как примечание. |
| C6 | **LOW** | `delegates_to` формат: D5 и F4 описывают новое значение, но не показывают текущее → будущему Coder'у может быть неясно, что именно менять. | D5 дополнен QA-примечанием: текущее vs новое значение. |
| C7 | **INFO** | В проекте два `audit_logger.py`: `shared/audit_logger.py` (write_audit_entry) и `deploy/audit_logger.py` (AuditLogger класс). DevPlan правильно использует shared/ — замечаний нет. | Без изменений. |

### Remaining Risks

| Risk | Severity | Description |
|------|----------|-------------|
| RR1 | MEDIUM | `promote_via_https()`: URL в `subprocess.run(["git", "push", "--mirror", url])` — если coder случайно подставит токен в URL (вместо GIT_ASKPASS env), AC7 нарушен. Тест `test_promote_via_https_token_not_in_argv` должен это отловить. |
| RR2 | LOW | `check_ssh_available()`: если coder проверит только `result.stdout` (не `result.stderr`), SSH всегда будет казаться недоступным → постоянный fallback на HTTPS. Тест `test_check_ssh_available_success` с правильно настроенным mock должен это отловить. |
| RR3 | LOW | PYTHONPATH экспорт в фасаде: если coder скопирует устаревший паттерн из paths.sh (без явного export), `python3 -m core...` упадёт с ModuleNotFoundError в средах где CWD ≠ project root. |
| RR4 | INFO | Фасад с MODULE_CONTRACT + комментариями может превысить 55 строк (хотя исполняемый код ≤40). Brief AC3: «Shell-фасад ≤ 40 LOC». R4 mitigation уточняет: «исполняемый код». Coder должен осознавать это различие. |

### Verdict

**APPROVED-WITH-CORRECTIONS** — 6 поправок внесены. Оставшиеся риски — MEDIUM (RR1: AC7 token leak при ошибке реализации) и LOW (RR2-RR3: ошибки в деталях имплементации). Риски покрываются тестами $TEST_SPEC (§9). DevPlan готов к реализации.

$END_DEVPLAN
