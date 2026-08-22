# Summary — TOP-10 рисков целостности данных и состояния

Аудит: 10 параллельных форензик-направлений · **59 находок** (CRITICAL 1 / HIGH 22 / MEDIUM 29 / LOW 7) · код не менялся.
Принцип отбора TOP-10: production failure likelihood × blast radius ÷ code churn.
Формат находок и сценарии (START → op → failure → END) — в `findings-01…10-*.md`.

## TOP-10

| # | ID | Risk (one-line) | Sev | Files | Minimal fix | Phase |
|---|----|-----------------|-----|-------|-------------|-------|
| 1 | DATA-902 | `persist_project_key`: неатомарная запись LLM key-store + silent-reset загрузчика → один corrupt = overwrite-all, потеря ВСЕХ ключей проектов | CRIT | llm/ key-store | atomic_writer + валидация при load, бэкап перед overwrite | Pre-launch |
| 2 | DATA-602/603/604 | Деплой-семантика успеха сломана: PARTIAL-нездоров = exit 0 + telegram без rollback; периметр rollback = образ+payload (миграции/volumes/monitoring не откатываются); snapshot-rollback структурно нерабочий (нет compose_state → pull несуществующего тега) | HIGH | deploy/orchestrator.py, deploy_orchestrator.py | PARTIAL → non-zero exit; snapshot хранит compose_state+tag; rollback-периметр задокументировать и проверить тестом | Pre-launch |
| 3 | DATA-201/501/205 | Postgres-хук проекта: crash между CREATE ROLE и записью creds теряет пароль роли навсегда; role-exists ранний return → GRANT/creds/regen никогда (после потери .platform-db.env); результат GRANT игнорируется при ложном IMP:9 | HIGH | modules/postgres/hooks/on_project_deploy.py | единый идемпотентный ensure (role+db+grant+creds) с записью creds до exit; verify-шаг | Pre-launch |
| 4 | DATA-502/503 | Бэкапы: upload-failure без retry между ночами, дамп удаляется из spool → тихая потеря RPO 24h; pg_dumpall уходит в S3 БЕЗ шифрования | HIGH | modules/backup-cron/scripts/upload.py | spool-очередь с retention до подтверждённого upload; шифрование до upload (age) | Pre-launch |
| 5 | DATA-504 | Restore выполняется в живой стек: без ON_ERROR_STOP и без гарантированного pre-restore snapshot → полувосстановленное состояние | HIGH | restore-скрипт | down → restore (ON_ERROR_STOP=1) → up; snapshot как precondition | Pre-launch |
| 6 | DATA-301/901 | NodeYaml RMW без flock: конкурентные писатели теряют проекты из node.yaml; write-back safe_load→dump стирает комментарии (шумные diff, конфликты) | HIGH | shared/node_yaml/ | lock + comment-preserving edit (ruamel) или генерация из реестра | Pre-launch |
| 7 | DATA-601 | `_retry_deliver` повторяет удалённую мутацию receive при неоднозначном сбое транспорта (ответ потерян, мутация применилась) → двойной payload поверх half-applied | HIGH | deploy/ deliver path | идемпотентный receive (delivery-id + дедуп) или retry только read-after-write проверкой | Pre-launch |
| 8 | DATA-701 | `cert_is_valid` игнорирует privkey.pem: crash в install-cert оставляет «valid on disk» с битой парой → nginx down, silent-stuck при следующих прогонах | HIGH | cert_orchestrator/issue_cert | валидация ПАРЫ (cert+key match) + tmp+rename установка | Pre-launch |
| 9 | DATA-801/303/302 | Конкурентный bootstrap/node-update без run-lock: lost-update чекпоинтов, двойной ACME-issue (rate limit); deploy-lock тихо деградирует в no-lock (root-owned lock file vs ci-deploy) | HIGH | lifecycle/, shared/file_lock.py | run-lock на ноде (mkdir-атомарный), lock-failure = FAIL не skip | Pre-launch |
| 10 | DATA-1001/706/1003 | Секреты вне lifecycle: AGE мастер-ключ в argv ssh-команд (ps visible) и dry-run логах; temp AGE-ключ переживает смерть процесса (handlers только в decrypt_secrets); ротация AGE неатомарна → DR-окно | HIGH | secrets/, crypto.py, core_deliverer | ключ через fd/env-file 0600 + finally-cleanup; ротация = two-phase с verify | Pre-launch |

## Ближняя периферия (11–15)

- **DATA-401** · `.env.platform` без контракта свежести: stale DSN доставляется payload'ом молча (MED, pre-launch guard: version/hash поле + deploy-time warn).
- **DATA-1002** · φ4 продолжает работу при нечитаемом secrets.env (частичный набор секретов в стек).
- **DATA-202** · LiteLLM provisioning: transient lookup-failure → GENERATE → duplicate key rows.
- **DATA-405** · nginx «rendered but not applied»: нет маркера, reload в другом канале.
- **DATA-704** · per-file os.replace пакет без транзакционности → mix payload в target_dir + осиротевший backup-dir.

## Кросс-куттинг темы (системные паттерны)

1. **Нет порядка «делай → mark» и run-локов** — каждое окно между мутацией и фиксацией состояния = zombie/orphan при crash (DATA-702/705/801/303).
2. **Success-критерий шире факта** — PARTIAL трактуется как успех, IMP:9 логируется при фейле, exit 0 в CI (DATA-602, DATA-205, DATA-606).
3. **RMW без lock/atomicity на файлах состояния** — node.yaml, key-store, vhost/ai-platform.yaml, .env.platform (DATA-301/104/305/901/903).
4. **Периметр rollback уже периметра мутации** — образ откатывается, схема/состояние/конфиги остаются (DATA-603, fix-forward канон требует явного перечня неоткатываемого).
5. **Секреты обслуживаются ad-hoc механикой** вне единого lifecycle (argv, temp, env-override, glob-first) — DATA-1001/1004/1005/706.

## Распределение по фазам

- **Pre-launch (≈25):** все CRITICAL/HIGH из TOP-10 — каждый закрыт S/M-фиксом или characterization-тестом.
- **Post-launch (≈34):** MED/LOW — консолидация retry-политик, schema_version state-файлов, comment-preserving YAML, TTL-кэши.

## Доверие к результату

Все находки evidence-based (file:line + цитаты); сценарии START→END восстановлены для всех HIGH/CRITICAL.
Ограничение: живые нода/БД/Redis не инспектировались (статический анализ кода + read-only git).
