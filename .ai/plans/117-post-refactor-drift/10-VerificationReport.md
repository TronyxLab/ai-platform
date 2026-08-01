$START_VERIFICATION_REPORT
# VerificationReport 117 — Brief H: Shell→Python финальная волна

$ARTIFACT_CONTRACT
PURPOSE:               Семантическая верификация брифа H программы 117 (задачи 59–66): финальная
                       волна Strangler-Fig shell→Python. Завершение миграции остатков shell,
                       верификация чистоты фасадов, cross-reference с брифом B.
DESCRIPTION:           8 задач: (59) bootstrap.sh — верификация чистоты фасада, (60) deploy.sh —
                       KEEP transitional, (61) scaffold.sh positional→named bridge → Python-хелпер,
                       (62) верификация живости node-resolver.sh/yaml_read.sh, (63) check-file-lines.sh
                       — закрыта, (64) backup-postgres.sh → Python, (65) postgres on-project-deploy.sh
                       → Python-хук, (66) cross-reference с брифом B (warm-images/disk-monitor/restore-test).
RATIONALE:             Бриф H — последняя волна программы 117 (6-я из 6). 5 из 8 задач — верификация
                       (закрыты без изменений кода), 3 — Python-порты. После волн A–G основная масса
                       shell уже мигрирована; данная волна — точечная зачистка остатков.
ACCEPTANCE_CRITERIA:   AC-H1..H9 из 09-DevPlan (см. §Таблица результатов); AC2 программы:
                       `make gate MODE=fast` и `make check-manifests` зелёные.
IMPLEMENTS:            117 01-Brief задачи 59–66; 117 09-DevPlan (Бриф H).
IMPACTS:               core/entrypoints/deploy.sh (TRAP), core/entrypoints/scaffold.sh (bridge→Python),
                       core/internal/scaffold/normalize_new_project_args.py (новый),
                       core/modules/postgres/hooks/ (on_project_deploy.py новый + wrapper),
                       core/modules/backup-cron/scripts/ (backup_postgres.py новый + wrapper),
                       core/modules/backup-cron/Dockerfile, tests/ (3 unit-теста + cross-layer allowlist + test_backup_cron.py).
REQUIRES:              117 09-DevPlan; зелёный gate после брифа G (волны A–G уже слиты в ветку 117-brief-h).
$END_ARTIFACT_CONTRACT

---

## Таблица результатов D59–D66

| Задача | Файл(ы) | Статус | Обоснование |
|--------|---------|--------|-------------|
| **D59** (MED) | core/entrypoints/bootstrap.sh | **CLOSED** (без изменений) | 160 LOC — чистый фасад: 0 inline python3, делегирование node_detect/node_yaml `--get-many`/scp_to_server/build_ssh_cmd/SSH_OPTS_COMMON (D7), stderr не глотается (D8). Оставшаяся логика — pure orchestration. Верифицировано: `rg "python3 -c" bootstrap.sh` = 0. |
| **D60** (MED) | core/entrypoints/deploy.sh | **DONE** (KEEP transitional) | 162 LOC — чистый фасад (ssh_command_parser + orchestrator_cli), 0 inline python3. Решение Option A зафиксировано TRAP[DECISION] в файле: удалить ПОСЛЕ верификации brief A на production (все ноды получили orchestrator_cli dispatch). Код не менялся — только TRAP-комментарий. |
| **D61** (MED) | scaffold.sh + normalize_new_project_args.py | **DONE** | Positional→named bridge (было 34 LOC shell) извлечён в Python-хелпер `core/internal/scaffold/normalize_new_project_args.py` (109 LOC с markup). scaffold.sh 128→103 LOC. Exit 0 всегда, defaults из PLATFORM_ORG/PLATFORM_DEFAULT_NODE. Shell-parity сохранена 1:1 (включая quirk mapping при смешанных флагах). Unit-тесты: tests/unit/test_normalize_new_project_args.py (9 тестов). |
| **D62** (LOW) | core/lib/node-resolver.sh, core/lib/yaml_read.sh | **CLOSED** (без изменений) | Оба живы: node-resolver — 13 потребителей (bootstrap.sh, deploy-context.sh, node-update.sh, node-lifecycle.sh, converge.sh, deploy.mk, ...); yaml_read — 6 потребителей (module-interface.sh, vhost_renderer.py, deploy_engine.py, issue-cert.sh, ...). yaml_read.sh header fix (D15) уже применён в main (текст «Separate tool: yaml_query.py ...» присутствует) — бриф B выполнен. |
| **D63** (LOW) | core/entrypoints/check-file-lines.sh | **CLOSED** (без изменений) | 74 LOC — pure orchestration (find + wc + count), 0 inline python3, всегда exit 0 (non-blocking). Python-порт — косметика, нарушает AC5. Верифицировано: `rg "python3" check-file-lines.sh` = 0. |
| **D64** (LOW) | backup-postgres.sh → backup_postgres.py + Dockerfile | **DONE** | Бизнес-логика (validate env → mkdir spool → pg_dumpall|gzip → gzip -t → pg_restore --list → retention cleanup → S3 upload) портирована построчно в `backup_postgres.py` (242 LOC с markup + PITR-документация в docstring). PIPESTATUS → Popen returncode; trap cleanup → finally. backup-postgres.sh → thin wrapper (21 LOC, путь /usr/local/bin/backup-postgres.sh в crontab/Makefile НЕ менялся). Dockerfile: COPY scripts/backup_postgres.py + chmod +x. Unit-тесты: tests/unit/test_backup_postgres.py (10 тестов, mock subprocess, все 3 проверки). **DEVIATION**: test_backup_cron.py::test_backup_postgres_uses_port_env Check A перенацелен на Python-файл (логика pg_dumpall больше не в shell). |
| **D65** (LOW) | postgres/hooks/on-project-deploy.sh → on_project_deploy.py | **DONE** | Бизнес-логика `_auto_create_db` (False→"", regex-валидация db_name, POSTGRES_PASSWORD, docker exec psql + парсинг «already exists»/«ERROR») портирована в `on_project_deploy.py` (167 LOC с markup). NodeYaml — прямой импорт (Python→Python, без subprocess). on-project-deploy.sh → thin wrapper (23 LOC). module.yaml путь НЕ менялся. Cross-layer allowlist: запись on-project-deploy.sh:47 перенесена на on_project_deploy.py:40 (та же зависимость D1, НЕ рост). **DEVIATION**: порядок проверок уже-exists/returncode изменён — psql возвращает rc≠0 при существующей БД, поэтому вывод проверяется на «already exists» ДО returncode-ветки (сохраняет интент идемпотентности; зафиксировано TRAP[BUG] в файле). Unit-тесты: tests/unit/test_on_project_deploy.py (10 тестов, mock docker exec, 4 сценария). |
| **D66** (LOW) | warm-images.sh / disk-monitor.sh / backup-restore-test.sh | **CLOSED** (подтверждено брифом B) | Бриф B (задача 10) РЕАЛИЗОВАН (merge 5a9bfd7 «dead code sweep» уже в ветке): все три файла отсутствуют на диске (ls = 0), cron-записи L32/L41/L50 удалены из crontab. Задача закрыта подтверждением. |

**Итог: 8 задач — 3 DONE (реализация), 5 CLOSED (верификация/подтверждение), 2 DEVIATION (задокументированы).**

---

## Отклонения от DevPlan (зафиксированы)

| # | Отклонение | Причина | Статус |
|---|-----------|---------|--------|
| 1 | D64: `test_backup_postgres_uses_port_env` Check A переведён с backup-postgres.sh на backup_postgres.py | Логика pg_dumpall портирована в Python — статический тест обязан сканировать новый SoT | Документировано в коде теста |
| 2 | D65: порядок проверок «already exists» vs returncode | Shell-оригинал имел мёртвую ветку (psql rc≠0 при существующей БД срабатывал раньше grep). Python-порт проверяет вывод до rc-ветки — идемпотентность хука сохранена | TRAP[BUG] в on_project_deploy.py + тест `test_database_already_exists_skips` |

---

## Верификация (§5 DevPlan)

| Проверка | Результат |
|----------|-----------|
| `rg "python3 -c" core/modules/postgres/hooks/` | 0 совпадений |
| `rg "python3 -c" core/modules/backup-cron/scripts/backup-postgres.sh` | 0 совпадений |
| `rg "python3 -c" core/entrypoints/scaffold.sh` | 0 совпадений |
| ruff check (6 новых/изменённых файлов) | All checks passed |
| ruff format | 3 reformatted, 3 unchanged |
| `make check-dead-code` | PASS — все DEPRECATED в grace |
| Unit-тесты (3 новых файла) | 27 passed |
| Смежные тесты (backup/postgres/scaffold/cross_layer) | 18 passed |
| `make gate MODE=fast` | **ALL PASS** (8 шагов, 341+ gate-тестов) |
| `make check-manifests` | All generated manifests up to date |

---

## Изменённые файлы

**Новые Python-модули:**
- `core/internal/scaffold/normalize_new_project_args.py` (D61)
- `core/modules/postgres/hooks/on_project_deploy.py` (D65)
- `core/modules/backup-cron/scripts/backup_postgres.py` (D64)

**Shell-правки (тонкие wrapper'ы / фасады):**
- `core/entrypoints/scaffold.sh` — 128→103 LOC (bridge → Python-хелпер)
- `core/modules/postgres/hooks/on-project-deploy.sh` — 100→23 LOC (wrapper)
- `core/modules/backup-cron/scripts/backup-postgres.sh` — 153→21 LOC (wrapper)
- `core/entrypoints/deploy.sh` — TRAP[DECISION] (KEEP transitional)

**Прочее:**
- `core/modules/backup-cron/Dockerfile` — COPY backup_postgres.py + chmod +x
- `tests/test_cross_layer_imports.py` — allowlist: on-project-deploy.sh:47 → on_project_deploy.py:40
- `tests/test_backup_cron.py` — Check A → backup_postgres.py

**Новые тесты:**
- `tests/unit/test_normalize_new_project_args.py` (9)
- `tests/unit/test_on_project_deploy.py` (10)
- `tests/unit/test_backup_postgres.py` (10)

$END_VERIFICATION_REPORT
