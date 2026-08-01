# 09-DevPlan — Бриф H: Shell→Python финальная волна

$ARTIFACT_CONTRACT
- PURPOSE: Реализация задач 59–66 программного брифа 117 — финальная волна Strangler-Fig shell→Python. Завершение миграции оставшихся shell-скриптов, верификация живости библиотек, cross-reference с брифом B.
- DESCRIPTION: 8 задач: (59) bootstrap.sh — верификация чистоты фасада после волн A+D, (60) deploy.sh — решение по срокам жизни переходного диспетчера, (61) scaffold.sh — извлечение positional→named bridge в Python, (62) верификация живости node-resolver.sh/yaml_read.sh, (63) check-file-lines.sh — оценка целесообразности Python-порта, (64) backup-postgres.sh → Python, (65) postgres on-project-deploy.sh → Python-хук, (66) cross-reference с брифом B (задача 10): судьба warm-images.sh/disk-monitor.sh/backup-restore-test.sh.
- RATIONALE: Бриф H — последняя волна программы 117 (6-я из 6). После волн A–E/G основная масса shell-кода уже мигрирована. Задачи 59–66 — точечная зачистка остатков: часть файлов уже очищена предыдущими волнами (требуется только верификация), часть — кросс-референсы к брифу B, оставшиеся 3 скрипта — финальный Python-порт.
- ACCEPTANCE_CRITERIA:
  - AC-H1: bootstrap.sh верифицирован как чистый фасад (волны A+D уже применены — без дополнительных изменений).
  - AC-H2: deploy.sh оставлен как переходный (решение зафиксировано); альтернатива — удаление после верификации brief A на production.
  - AC-H3: scaffold.sh positional→named bridge извлечён в Python-хелпер; shell-фасад <100 LOC.
  - AC-H4: node-resolver.sh и yaml_read.sh подтверждены живыми; yaml_read.sh header fix → бриф B (task 15).
  - AC-H5: check-file-lines.sh — решение «оставить как есть» зафиксировано (pure orchestration, 0 inline python3).
  - AC-H6: backup-postgres.sh портирован в Python; shell → 10-line wrapper.
  - AC-H7: postgres on-project-deploy.sh портирован в Python; shell → thin wrapper.
  - AC-H8: warm-images.sh/disk-monitor.sh/backup-restore-test.sh — cross-reference к брифу B (task 10, D10) зафиксирован.
  - AC-H9: `make gate MODE=fast`, `make check-manifests` зелёные.
- IMPLEMENTS: 117 01-Brief задачи 59–66.
- IMPACTS: core/entrypoints/ (bootstrap.sh — без изменений, deploy.sh — без изменений, scaffold.sh — правка), core/internal/scaffold/ (новый Python-хелпер), core/modules/backup-cron/scripts/ (backup_postgres.py — новый, backup-postgres.sh → wrapper), core/modules/postgres/hooks/ (on_project_deploy.py — новый, on-project-deploy.sh → wrapper), core/lib/ (node-resolver.sh, yaml_read.sh — без изменений), core/entrypoints/check-file-lines.sh (без изменений).
- REQUIRES: 117 01-Brief (реестр), результаты верификации скриптов от 2026-08-01, зелёный gate после брифа G.

---

## 0. Коррекции к исходному брифу (по результатам верификации)

| Задача | Исходный вердикт брифа | Фактический вердикт | Действие |
|--------|----------------------|---------------------|----------|
| 59 (MED) | bootstrap.sh (150 LOC, строки 73–147): оркестрация-логика + exec ssh → Python/чистый фасад | **Уже чистый фасад**. Волна A (D7: exec ssh → SSH_OPTS_COMMON) и волна D (D8: stderr не глотаем) применены. Файл 160 LOC — чистый shell-фасад: batch-extract через `node_yaml --get-many`, делегирование SCP/SSH через lib, 0 inline python3. Оставшаяся логика — pure orchestration (построение массивов аргументов, условное ветвление). | **Закрыта без действий.** Верифицировать, зафиксировать. |
| 60 (MED) | deploy.sh (161 LOC): legacy-диспетчер — решение по срокам жизни (B1 T7 transitional) | **Уже чистый фасад.** 162 LOC: делегирует parsing → `ssh_command_parser` (Python), dispatch → `orchestrator_cli` (Python). Verb dispatch case/esac — pure routing. После brief A (D1) канонический канал — `orchestrator_cli dispatch`; deploy.sh — SSH forced-command entrypoint для обратной совместимости. | **KEEP transitional.** Зафиксировать решение: удалить после верификации brief A на production (не в этой волне). |
| 63 (LOW) | check-file-lines.sh (74 LOC): логика гейта → Python | **Pure shell orchestration.** 74 LOC: find + wc + count + print. 0 inline python3, 0 бизнес-логики. Скрипт всегда exit 0 (non-blocking warning). Ни один Strangler-триггер не срабатывает. | **Закрыта без действий.** Python-порт — косметическое изменение, нарушающее AC5 (без нового функционала). |
| 66 (LOW) | warm-images.sh / disk-monitor.sh / backup-restore-test.sh — судьба вместе с #10 | **Все три файла всё ещё присутствуют** на диске. Бриф B (задача 10, D10) уже принял решение об удалении, но ещё не реализован. | **DEFER к брифу B.** Зафиксировать cross-reference; после реализации брифа B файлы должны отсутствовать. |

---

## 1. Технический анализ и решения

### Задача 59 (MED) — bootstrap.sh: верификация чистоты фасада

**Факты (верифицированы):**
- `core/entrypoints/bootstrap.sh` — 160 LOC (бриф утверждал 150 — расхождение +10 строк).
- Волна A (DevPlan 02, D7): `exec ssh` заменён на `SSH_OPTS_COMMON` из lib/ssh.sh (Python SoT `ssh_opts.py`). Применено — строка 157: `exec ssh "${SSH_OPTS_COMMON[@]}"`.
- Волна A (DevPlan 02, D8): stderr не глотается. Применено — строки 75-81: `_batch_err` mktemp, ошибки логируются через `log_imp`.
- Делегирование: `node_detect` (Python), `node_yaml --get-many` (Python), `scp_to_server` (shell lib), `build_ssh_cmd` (shell lib), `SSH_OPTS_COMMON` (Python SoT).
- Оставшаяся shell-логика: tab-delimited парсинг (строки 84–92), валидация OWNER_KEY (95), построение массивов аргументов (123–128), условное ветвление local/remote (121–157). Всё это — **pure orchestration**, допустимая по языковой политике.

**Решение D59:** задача закрыта без действий. Файл уже является чистым фасадом по критериям языковой политики:
- 0 inline `python3 -c` или heredoc-блоков.
- Все `python3 -m` вызовы — канонический паттерн (зарегистрированы в cross-layer allowlist).
- Бизнес-логика отсутствует в shell — всё делегировано Python-модулям.
- 160 LOC — в пределах разумного для entrypoint-фасада (языковая политика: «<150 строк» — guideline, не hard limit; порог thin-wrapper гейта = 150, файл зарегистрирован в allowlist).

**Файлы:** без изменений.

**Риск:** NONE.

---

### Задача 60 (MED) — deploy.sh: решение по срокам жизни

**Факты (верифицированы):**
- `core/entrypoints/deploy.sh` — 162 LOC.
- **Не вызывается из Makefile `deploy`**: `make deploy` → git push → CI → `orchestrator_cli dispatch` (канонический канал). `deploy.mk` не содержит вызовов `deploy.sh`.
- **Роль в системе**: SSH forced-command entrypoint на VPS. Когда `authorized_keys` содержит `command="...deploy.sh"`, SSH_ORIGINAL_COMMAND парсится через `ssh_command_parser` (Python), dispatch — через `orchestrator_cli` (Python).
- После brief A (D1): канонический forced-command = `orchestrator_cli dispatch`. deploy.sh — переходный entrypoint для обратной совместимости.
- Файл уже чистый фасад: `parse_verb()` делегирует `ssh_command_parser --format lines` (Python CLI), `_dispatch_verb()` маршрутизирует глаголы через `exec $ORCHESTRATOR_CLI`. 0 inline python3.
- Зарегистрирован в gate-allowlist: `test_gate_thin_wrapper.py:55` («152 LOC (2 over limit) — K1 verb contract dispatch»), `test_gate_no_unregistered_entrypoint.py:70`, `test_gate_single_orchestrator.py:79`.
- **Потребители**: тесты (`test_deploy_verbs.py`, `test_project_lifecycle.py`), CI workflow (`deploy-project.yml` legacy path), `ssh_command_parser.py` (стрипит path-префикс).

**Решение D60 — superposition (Mode 2 BINARY):**

| Критерий | Option A: Оставить как переходный | Option B: Удалить сейчас |
|----------|----------------------------------|-------------------------|
| Риск регрессии | Низкий (файл стабилен, 0 изменений) | Средний (тесты завязаны на deploy.sh; CI workflow может ссылаться) |
| Соответствие канону | Частичное (дублирует dispatch-канал) | Полное (единственный канал — orchestrator_cli) |
| Обратная совместимость | Полная (существующие ноды продолжают работать) | Нарушена (существующие authorized_keys с deploy.sh сломаются) |
| Трудозатраты | 0 | ~1 час (удаление + обновление тестов + CI) |
| AC5 (без нового функционала) | Соответствует | Соответствует |

**Решение D60: Option A — оставить как переходный.** Обоснование:
1. Файл уже чистый фасад — удаление не даёт снижения сложности.
2. Удаление сейчас нарушит обратную совместимость для нод, где authorised_keys ещё содержит `deploy.sh` (brief A может быть не развёрнут на всех нодах).
3. Естественная точка удаления: после верификации brief A на production-ноде (ручное тестирование AC3 программы), когда все ноды гарантированно имеют `orchestrator_cli dispatch`.
4. Cross-reference: удаление deploy.sh — часть финальной зачистки после верификации всей программы 117.

**Файлы:** без изменений в этой волне. TRAP[DECISION] с условием удаления будет добавлен в файл при реализации.

**Риск:** LOW. Файл стабилен, не требует изменений.

---

### Задача 61 (MED) — scaffold.sh: CLI-нормализация → Python

**Факты (верифицированы):**
- `core/entrypoints/scaffold.sh` — 128 LOC.
- Структура: case/esac диспетчер (строки 31–128) → exec соответствующего internal-скрипта.
- Единственная логика тяжелее pure-routing: **positional→named bridge для new-project** (строки 35–66):
  - Обнаружение позиционных аргументов (позиция 0 → `--name`, позиция 1 → `--template`).
  - Инжекция `--org` и `--node` defaults из env vars (`PLATFORM_ORG`, `PLATFORM_DEFAULT_NODE`).
  - Построение массива args.
- Остальные subcommand-ветки — чистый exec (1 строка).

**Анализ Strangler-триггера:**
- Tier 1 (немедленный): новых `python3 -c`/heredoc нет. Но positional→named bridge — это **аргументный парсинг** (бизнес-логика), не оркестрация. По языковой политике должен быть в Python.
- Tier 2 (плановый): файл имеет ≥1 Tier-1 экстракцию → кандидат на частичную декомпозицию.

**Решение D61:** извлечь ТОЛЬКО positional→named bridge в Python-хелпер `core/internal/scaffold/normalize_new_project_args.py`. Shell-фасад:
- Сохраняет case/esac dispatch (pure routing — легитимный shell).
- Для ветки `new-project`: вызывает Python-хелпер для нормализации, затем exec add-project.sh.

**API Python-хелпера:**
```
python3 -m core.internal.scaffold.normalize_new_project_args [positional args...]
```
- Вход: позиционные аргументы + env vars (PLATFORM_ORG, PLATFORM_DEFAULT_NODE).
- Выход (stdout): нормализованные аргументы в формате `--name X --template Y --org Z --node W`.
- Exit code: 0 (всегда — defaults инжектятся, отсутствие name/template валидирует add-project.sh).

**Файлы:**
- `core/internal/scaffold/normalize_new_project_args.py` — **новый** (~40 LOC).
- `core/entrypoints/scaffold.sh` — **правка**: строки 35–68 заменить на вызов Python-хелпера (~10 строк вместо 30). Ожидаемый размер: ~108 LOC.

**Риск:** LOW. Python-хелпер — чистая функция без сайд-эффектов. Shell-фасад сохраняет обратную совместимость (те же exit codes).

---

### Задача 62 (LOW) — Верификация живости node-resolver.sh/yaml_read.sh

**Факты (верифицированы):**

**node-resolver.sh (273 LOC):**
- Активные потребители (6+):
  - `core/entrypoints/bootstrap.sh:66` — `source "${CORE_DIR}/lib/node-resolver.sh"`
  - `core/entrypoints/deploy-context.sh:42` — resolve node.yaml
  - `core/entrypoints/node-update.sh:99` — resolve node.yaml
  - `core/internal/bootstrap/node-lifecycle.sh:70` — resolve node.yaml
  - `core/internal/bootstrap/converge.sh:68` — resolve node.yaml
  - `makefiles/deploy.mk:54` — `resolve_node_yaml` + `extract_node_host`
- Функции делегируют Python-модулям: `resolve_node_yaml()` → NodeYaml CLI (`python3 -m core.internal.shared.node_yaml --resolve`), `extract_node_host()` → NodeYaml CLI (`--get node.host`), `resolve_node_from_env()` → yaml_query.py (строка 216).
- **ЖИВ.** Удаление сломает bootstrap, deploy-context, node-update, converge.

**yaml_read.sh (98 LOC):**
- Активные потребители:
  - `core/lib/module-interface.sh:32` — `source "${_IM_DIR}/yaml_read.sh"` (используется ВСЕМИ модульными операциями).
  - 13+ исторических потребителей (сейчас используют NodeYaml CLI напрямую — issue-cert.sh:43).
- Функции: `yaml_get_field()`, `yaml_get_list()` → NodeYaml CLI.
- Header (строка 12): «Replaces inline `python3 -c "import yaml; ..."` patterns and the old yaml_query.py script» — вводящий в заблуждение claim («old yaml_query.py» — НЕ заменён, см. бриф B задачу 15).
- **ЖИВ.** Header fix — бриф B (task 15, D15).

**Решение D62:**
- **node-resolver.sh:** подтверждён живым. Без изменений.
- **yaml_read.sh:** подтверждён живым. Header fix (строка 12) — в зоне ответственности брифа B (задача 15, D15: заменить текст на «Separate tool: yaml_query.py handles arbitrary YAML dotted-keys; this facade targets node.yaml domain via NodeYaml CLI»). В бриф H: только cross-reference.

**Файлы:** без изменений.

**Риск:** NONE для node-resolver.sh/yaml_read.sh. Риск конфликта с брифом B: низкий — бриф B правит только комментарий (1 строка).

---

### Задача 63 (LOW) — check-file-lines.sh: логика гейта → Python

**Факты (верифицированы):**
- `core/entrypoints/check-file-lines.sh` — 74 LOC.
- Алгоритм: parse `--max-lines` → find файлы → wc -l → сравнить с лимитом → напечатать WARNING → exit 0.
- **0 inline python3**, 0 heredoc-блоков. Чистый shell: find + wc + read + арифметика.
- Всегда `exit 0` (non-blocking warning по дизайну, DevPlan 030 AC5).
- Никакой «логики гейта» не содержит — это информационный сканер, не блокирующий CI.

**Анализ Strangler-триггера:**
- Tier 1: нет новых inline python3/heredoc → не срабатывает.
- Tier 2: нет накопленных экстракций → не срабатывает.
- Языковая политика: «Bash остаётся для чистой оркестрации (последовательность subprocess-вызовов без логики)» — find + wc + подсчёт = чистая оркестрация.

**Решение D63:** задача закрыта без действий. Python-порт был бы косметическим изменением:
- Python не ускорит файловое сканирование (find — оптимизированный C-код).
- Добавит ~50 LOC Python + 5 LOC shell-wrapper — чистое увеличение codebase.
- Нарушает AC5 программы («ноль новых глаголов/механизмов; все изменения — унификация/удаление») — порт не является ни унификацией, ни удалением.

**Файлы:** без изменений.

**Риск:** NONE.

---

### Задача 64 (LOW) — backup-postgres.sh → Python

**Факты (верифицированы):**
- `core/modules/backup-cron/scripts/backup-postgres.sh` — 153 LOC (90 строк бизнес-логики + 63 строки PITR-документации в комментариях).
- Выполняется внутри backup-cron Docker-контейнера через cron (crontab, строка 26: `0 3 * * * /usr/local/bin/backup-postgres.sh`).
- Бизнес-логика: validate env → mkdir spool → pg_dumpall через pipe → PIPESTATUS-проверка → gzip -t integrity → pg_restore --list validate → retention cleanup → S3 upload.
- Модуль backup-cron уже Python-first: 5 Python-скриптов (`retention.py`, `upload.py`, `s3_client.py`, `backup_config.py`, `date_parser.py`). backup-postgres.sh — последний shell-скрипт в модуле.
- **0 inline python3** — скрипт использует только нативные shell-команды (pg_dumpall, gzip, pg_restore).
- PITR-документация (строки 91–153) — 63 строки комментариев с restore-процедурой. Должны быть сохранены (перенесены в docstring Python-модуля).

**Решение D64:** портировать в Python.
- Новый файл: `core/modules/backup-cron/scripts/backup_postgres.py` (~120 LOC).
- Использовать `subprocess.run()` для pg_dumpall/gzip/pg_restore.
- PIPE-статусы заменить на явную проверку `result.returncode`.
- PITR-документацию перенести в module docstring.
- Точка входа: `def main() -> int` + `if __name__ == "__main__": sys.exit(main())`.
- Shell-фасад: `backup-postgres.sh` → 10-line wrapper:
  ```bash
  #!/usr/bin/env bash
  set -euo pipefail
  exec python3 /usr/local/bin/backup_postgres.py "$@"
  ```
- Крон-запись: проверить путь в crontab (сейчас: `/usr/local/bin/backup-postgres.sh`). Либо оставить shell-враппер на том же пути, либо обновить cron на `.py` + обеспечить `chmod +x` на Python-файле с shebang.

**Альтернатива (отклонена):** вызывать Python напрямую из cron (`python3 /usr/local/bin/backup_postgres.py`). Отклонено — shell-враппер обеспечивает обратную совместимость (путь в crontab не меняется).

**Файлы:**
- `core/modules/backup-cron/scripts/backup_postgres.py` — **новый** (~120 LOC).
- `core/modules/backup-cron/scripts/backup-postgres.sh` — **правка**: замена на 10-line wrapper.
- `core/modules/backup-cron/Dockerfile` — **правка**: добавить `COPY scripts/backup_postgres.py /usr/local/bin/` + `chmod +x`.
- `core/modules/backup-cron/scripts/crontab` — без изменений (путь `/usr/local/bin/backup-postgres.sh` остаётся).

**Риск:** MEDIUM. Критический скрипт (production backup). Митигация:
1. Построчное соответствие старой и новой логики (pg_dumpall pipe → subprocess.Popen с piped stdout).
2. Сохранить все проверки: PIPESTATUS → returncode, gzip -t, pg_restore --list.
3. Dry-run тест: запустить Python-версию локально с моковым PostgreSQL.
4. Сохранить PITR-документацию в docstring.

---

### Задача 65 (LOW) — on-project-deploy.sh: inline python3 -m → Python-хук

**Факты (верифицированы):**

**postgres/hooks/on-project-deploy.sh (100 LOC):**
- Использует `python3 -m core.internal.shared.node_yaml` (строка 47) — канонический паттерн, НЕ inline `-c`.
- Содержит бизнес-логику в shell:
  - Строки 52–54: конвертация `"False"` → `""` для `needs.database: false`.
  - Строки 63–66: regex-валидация `db_name`.
  - Строки 68–72: проверка наличия POSTGRES_PASSWORD.
  - Строки 75–87: docker exec psql + парсинг вывода (grep «already exists»/«ERROR»).
- По языковой политике: конвертация значений, regex-валидация и парсинг вывода = бизнес-логика → Python.
- Нет отдельного Python-файла в `postgres/hooks/` (glob подтвердил: 0 `.py` файлов).

**monitoring/hooks/on-project-deploy.sh (44 LOC):**
- **Уже thin wrapper**: весь мониторинг в `monitoring_config_renderer.py`. Shell — только приём аргументов + exec Python.
- Без изменений.

**Решение D65:** портировать postgres-хук в Python.
- Новый файл: `core/modules/postgres/hooks/on_project_deploy.py` (~80 LOC).
- Бизнес-логика `_auto_create_db()` → `auto_create_db()` в Python.
- Использовать `subprocess.run()` для docker exec psql.
- Использовать `node_yaml` через прямой импорт (Python→Python, без subprocess): `from core.internal.shared.node_yaml import NodeYaml`.
- Shell-фасад: `on-project-deploy.sh` → thin wrapper (~15 LOC):
  ```bash
  #!/usr/bin/env bash
  set -euo pipefail
  SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
  exec python3 "${SCRIPT_DIR}/on_project_deploy.py" "$@"
  ```
- Интерфейс module.yaml (`on_project_deploy: hooks/on-project-deploy.sh`) — **без изменений**: shell-враппер сохраняет обратную совместимость.

**Файлы:**
- `core/modules/postgres/hooks/on_project_deploy.py` — **новый** (~80 LOC).
- `core/modules/postgres/hooks/on-project-deploy.sh` — **правка**: замена на thin wrapper (~15 LOC).
- `core/modules/postgres/module.yaml` — без изменений (путь к хуку не меняется).

**Риск:** LOW. Хук — non-fatal (ошибка логируется, деплой не блокируется). Тестирование: mock docker exec, проверить все 4 сценария (нет ai-platform.yaml, нет needs.database, DB уже существует, успешное создание).

---

### Задача 66 (LOW) — warm-images.sh / disk-monitor.sh / backup-restore-test.sh: cross-reference

**Факты (верифицированы):**
- Все три файла **присутствуют** на диске:
  - `core/modules/backup-cron/scripts/warm-images.sh` (94 LOC)
  - `core/modules/backup-cron/scripts/disk-monitor.sh` (69 LOC)
  - `core/modules/backup-cron/scripts/backup-restore-test.sh` (110 LOC)
- Бриф B (задача 10, D10) уже принял решение: **удалить все три** (broken cron-записи: нет docker CLI/socket в контейнере; host-пути не существуют).
- Бриф B ещё **не реализован** (файлы на диске — доказательство).
- Строки crontab: L32 (warm-images), L41 (restore-test), L50 (disk-monitor) — тоже ещё присутствуют.

**Решение D66:** задача является cross-reference к брифу B (задача 10, D10). Никаких независимых действий в рамках брифа H:
- После реализации брифа B: все три файла должны отсутствовать на диске.
- После реализации брифа B: строки crontab L32/L41/L50 должны быть удалены.
- Верификация в рамках брифа H: убедиться, что бриф B реализован → файлы удалены. Если нет → эскалировать (бриф B должен быть выполнен до брифа H по порядку волн).

**Файлы:** без изменений в этой волне (удаление — ответственность брифа B).

**Риск:** NONE (в рамках брифа H). Процессный риск: если бриф B не реализован до брифа H — файлы останутся; AC-H8 фиксирует это как известное состояние.

---

## 2. Порядок реализации

Фаза 1 — быстрые задачи (верификация, без изменений кода):
1. **D59** (bootstrap.sh — закрыта) + **D60** (deploy.sh — KEEP transitional) + **D62** (node-resolver/yaml_read — живы) + **D63** (check-file-lines — закрыта) + **D66** (cross-ref B) — зафиксировать в коде через TRAP-комментарии где уместно.

Фаза 2 — Python-порты:
2. **D61** (scaffold.sh positional→named bridge) — новый Python-хелпер + shell-правка.
3. **D65** (postgres on-project-deploy hook → Python) — новый Python-хук + shell → wrapper.
4. **D64** (backup-postgres.sh → Python) — новый Python-скрипт + shell → wrapper + Dockerfile.

Фаза 3 — верификация:
5. `make check-dead-code` — зелёный (новых файлов нет в allowlist; старые shell-файлы заменены на wrapper'ы).
6. `make gate MODE=fast` + `make check-manifests` — зелёные.
7. Точечные тесты: unit-тест для `normalize_new_project_args.py`, unit-тест для `on_project_deploy.py` (mock docker exec).

---

## 3. Критерии приёмки (повтор из контракта)

- AC-H1: bootstrap.sh верифицирован как чистый фасад — 0 изменений.
- AC-H2: deploy.sh оставлен как переходный — решение зафиксировано TRAP[DECISION] с Rev-условием.
- AC-H3: scaffold.sh positional→named bridge в Python; shell <100 LOC.
- AC-H4: node-resolver.sh и yaml_read.sh живы; cross-ref к брифу B task 15.
- AC-H5: check-file-lines.sh оставлен как есть — решение зафиксировано.
- AC-H6: backup-postgres.sh → Python; shell-wrapper <15 LOC; crontab-путь не изменился.
- AC-H7: postgres on-project-deploy.sh → Python; shell-wrapper <15 LOC; module.yaml не изменился.
- AC-H8: warm-images/disk-monitor/restore-test — cross-ref к брифу B D10.
- AC-H9: `make gate MODE=fast` + `make check-manifests` зелёные.

Дополнительно:
- `rg "python3 -c" core/modules/postgres/hooks/` → 0 совпадений.
- `rg "python3 -c" core/modules/backup-cron/scripts/backup-postgres.sh` → 0 совпадений.
- `rg "python3 -c" core/entrypoints/scaffold.sh` → 0 совпадений.
- Все новые Python-файлы проходят `ruff check` + `ruff format`.

---

## 4. Риски и митигации

| Риск | Митигация |
|------|-----------|
| D64: порт backup-postgres.sh сломает production backup (pg_dumpall pipe, gzip integrity, pg_restore --list) | Построчное соответствие старой/новой логики; dry-run с моковым PostgreSQL; проверка PIPESTATUS→returncode всех трёх шагов |
| D64: Python-путь в crontab может не работать (разный PYTHONPATH в контейнере) | Оставить shell-wrapper на старом пути `/usr/local/bin/backup-postgres.sh`; проверить `python3` доступен в контейнере (Dockerfile ставит `python3`) |
| D65: postgres-хук — docker exec может отличаться в Python subprocess | Сохранить точную команду: `docker exec postgres psql -U postgres -c "CREATE DATABASE ..."`; парсинг вывода через subprocess stdout |
| D61: Python-хелпер scaffold может не найти модуль (PYTHONPATH) | Использовать `python3 -m core.internal.scaffold.normalize_new_project_args` — PYTHONPATH уже настроен для всех entrypoints |
| Dead-code gate завалится на новых Python-файлах (если считает их неиспользуемыми) | Проверить references: Python-хелпер вызывается из scaffold.sh; backup_postgres.py — из wrapper'а; on_project_deploy.py — из wrapper'а |
| Конфликт с брифом B (yaml_read.sh header fix) | Бриф B правит 1 строку комментария (yaml_read.sh:12); бриф H не трогает yaml_read.sh — конфликта нет |

---

## 5. Оценка

- Изменяемые файлы: ~6 (3 новых Python-файла + 3 shell-правки).
- Новые Python-файлы: 3 (`normalize_new_project_args.py` ~40 LOC, `backup_postgres.py` ~120 LOC, `on_project_deploy.py` ~80 LOC).
- Строк кода: ~240 строк нового Python, ~80 строк удалено из shell (scaffold bridge, backup-postgres logic, postgres hook logic).
- Трудозатраты: ~0.25-0.5 дня агент-времени. Размер: **STANDARD** (9-20 файлов в сумме с верификацией, бизнес-логика — 3 Python-порта) → только DevPlan.
- **5 из 8 задач** закрыты без изменений кода (59, 60, 62, 63, 66) — верификация + документация.
- **3 из 8 задач** требуют реализации (61, 64, 65) — все LOW/MED, точечные изменения.

## 6. Отклонения от исходного брифа

| Задача | Отклонение | Причина |
|--------|-----------|---------|
| 59 | bootstrap.sh — без изменений | Уже чистый фасад после волн A+D. Бриф оценивал необходимость Python-порта ошибочно — файл делегирует всю логику Python-модулям. |
| 60 | deploy.sh — KEEP transitional | Файл уже чистый фасад (делегирует ssh_command_parser + orchestrator_cli). Удаление сейчас нарушит обратную совместимость. Удалить после верификации brief A на production. |
| 63 | check-file-lines.sh — без изменений | Pure shell orchestration (find+wc+count). 0 inline python3, 0 бизнес-логики. Python-порт — косметика, нарушает AC5. |
| 66 | warm-images/disk-monitor/backup-restore-test — DEFER | Бриф B (задача 10, D10) уже принял решение об удалении, но ещё не реализован. Файлы всё ещё на диске. |

---

## 7. Пересечения с другими брифами

| Бриф | Задача | Пересечение | Решение |
|------|--------|-------------|---------|
| A | 7 (exec ssh → SSH_OPTS_COMMON) | bootstrap.sh:157 — уже исправлено волной A | Подтверждено в D59 |
| A | 8 (проглоченные ошибки) | bootstrap.sh:73-81 — уже исправлено волной A | Подтверждено в D59 |
| B | 10 (backup-cron: 3 broken cron) | warm-images.sh, disk-monitor.sh, backup-restore-test.sh — удаление | Cross-reference D66: ответственность брифа B |
| B | 15 (yaml_query.py жив) | yaml_read.sh:12 header fix | Cross-reference D62: ответственность брифа B |
| G | 58 (точечная декомпозиция) | postgres hook, backup-postgres | Бриф G точечно декомпозирует Python-монолиты; бриф H портирует shell-скрипты — зоны ответственности не пересекаются |

---

## Next Steps

### Wave 1
Use coder role and read `/Users/tronyx/projects/ai-platform/.ai/plans/117-post-refactor-drift/09-DevPlan.md`, implement Wave 1: D61 (scaffold.sh normalize_new_project_args.py), D65 (postgres on_project_deploy.py), D64 (backup_postgres.py).

После реализации:
```bash
make check-dead-code
make gate MODE=fast
make check-manifests
```
