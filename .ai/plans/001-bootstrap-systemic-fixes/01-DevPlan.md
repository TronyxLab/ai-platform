# GREP_SUMMARY: devplan bootstrap systemic-fixes clickhouse default-user rsync-exclude env-requires-gate hermes-image-check healthcheck-robustness minio-credentials
$START_DEVPLAN

# DevPlan — Системные фиксы bootstrap/deploy (Wave 1 post-mortem)

## $ARTIFACT_CONTRACT
- **PURPOSE:** Устранить класс ошибок «деплой завершился, но модули в restart loop / с пустыми секретами», обнаруженных после Wave 1 bootstrap tronyx-vps, — системно, на уровне кода установки/настройки сервера (не хот-фиксами на VPS).
- **DESCRIPTION:** 5 задач: (T1) ClickHouse users.d mount-рефакторинг — пароль всегда регенерируется из env; (T2) rsync-исключение runtime-артефактов при доставке core/; (T3) fail-fast гейт env_requires перед compose up + URL-safe констрейнт паролей; (T4) hermes-agent image check из compose config вместо hardcoded + reconciliation чужих контейнеров; (T5) устойчивый modules-healthcheck (restart loop = FAIL, oneshot = PASS).
- **RATIONALE:** Q: почему код bootstrap, а не фиксы на сервере? A: сервер пересоздаваем (инвариант №9 AGENTS.md), там работает другой агент; единственный способ гарантировать сходимость — чтобы `make bootstrap-node` / `make node-update` сами приводили ноду в валидное состояние.
- **ACCEPTANCE_CRITERIA:** См. §Acceptance Criteria — 6 измеримых критериев, включая «повторный node-update приводит langfuse/hermes-agent/minio-createbuckets в healthy».
- **IMPLEMENTS:** Запрос владельца «исправить эти ошибки системно в моменте установки/настройки сервера» (2026-07-17).
- **IMPACTS:** core/modules/clickhouse/*, core/internal/bootstrap/{scp-deliver.sh,deploy-modules.sh}, .github/workflows/core-deploy.yml, core/internal/healthcheck/modules-healthcheck.sh, tests/*, .env.example.
- **REQUIRES:** Доступ только к локальному репозиторию. VPS НЕ трогаем (там работает другой агент). SOPS-секреты — ручной ops-шаг T7.

---

## 1. Requirements Analysis — findings с VPS (verified read-only)

| # | Симптом | Root cause (проверено) |
|---|---------|------------------------|
| P1 | langfuse restart loop, `Authentication failed` (code 516) | `default-user.xml` на VPS содержит `testpass` (mtime 09:26 = момент деплоя), env ClickHouse = `ch-test-2026`. Источник: **локальный стейл-артефакт** `core/modules/clickhouse/config/users.d/default-user.xml` (gitignored, сгенерирован локальным dev-compose) доставлен `rsync -avz --delete core/` (scp-deliver.sh / core-deploy.yml не исключают его). ClickHouse hot-reload'ит users.d → auth сломан. |
| P2 | hermes-agent RestartCount=101, «Input is not a terminal» | Контейнер на VPS: `Tty=false, OpenStdin=false, Cmd=null`, image `ghcr.io/tronyx161/hermes-agent-tronyx-lab:latest` — НЕ соответствует compose (`tty: true`, `command: gateway run`, `${CONTEXT_IMAGE:-ghcr.io/tronyxlab/hermes-agent-context:v2026.7.1}`). `docker compose ls` не содержит проект hermes-agent → compose up не проходил; deploy-modules.sh:392-410 проверяет **hardcoded** образы `ghcr.io/tronyx161/hermes-agent-{base,${ctx}}:latest`, дрейфующие от compose. Без command-override образ запускает интерактивный CLI → нет TTY → exit → restart loop. |
| P3 | minio-createbuckets Exited(1) `Access Denied` | `MINIO_ROOT_USER=`/`MINIO_ROOT_PASSWORD=` пустые в обоих контейнерах — переменных нет в secrets.env. `module.yaml env_requires` декларируется, но **нигде не валидируется** при деплое; `step_12b_ensure_secrets` покрывает только 7 hardcoded секретов. |
| P4 | «platform-secrets: нет контейнера» | Ложная тревога: это system-модуль (systemd oneshot, `active (exited)`, secrets.env создан). Аудит ожидал контейнер. |
| P5 | healthcheck WARN nginx/postgres `status=not-found` | `modules-healthcheck.sh` берёт только первый `container_name` (`head -1`), не детектит `State.Restarting`/RestartCount (restart loop hermes показывался как «starting», не FAIL). |

**Success criteria (ключевые):**
1. Ротация `CLICKHOUSE_PASSWORD` в секретах сходится без ручных действий при следующем `docker compose up -d`.
2. Runtime-артефакты никогда не доставляются rsync'ом на VPS.
3. Модуль с пустым/отсутствующим `env_requires`-секретом FAIL'ится до `compose up` с внятным сообщением (не деплой с пустым паролем).
4. Restart loop любого контейнера = FAIL в healthcheck, а не WARN.
5. hermes-agent image-check не может разойтись с compose-декларацией (единый источник — `docker compose config`).

## 2. Design Decisions

### D1 — ClickHouse: per-file ro mount вместо dir mount
## @rationale Q: почему менять mount, а не чистить файл в deploy? A: entrypoint ClickHouse пишет `default-user.xml` при каждом старте контейнера из текущего env. Если users.d не bind-mounted как каталог, файл живёт в контейнере (эфемерно) и всегда соответствует env → ротация пароля сходится автоматически, репозиторий не загрязняется. Rejected: (a) `rm default-user.xml` перед compose up в deploy-modules.sh — лечит симптом, оставляет локальное загрязнение репо; (b) SQL-driven пароль-init — избыточно, `CLICKHOUSE_DEFAULT_ACCESS_MANAGEMENT=1` уже используется.

### D2 — rsync: явный exclude, не gitignore-фильтр
## @rationale Q: почему `--exclude 'default-user.xml'`, а не `--filter=':- .gitignore'`? A: gitignore-фильтр широк и может молча исключить нужные файлы (напр. будущие runtime-конфиги, которые должны доставляться). Явный список детерминирован и тестируем static-гейтом. Rejected: gitignore-filter.

### D3 — env_requires: fail-fast, не auto-generate
## @rationale Q: почему не генерировать отсутствующие секреты (как step_12b)? A: auto-generate для stateful-сервисов создаёт ровно тот же класс дрейфа, что P1 (пароль в данных ≠ пароль в env при следующем bootstrap). Missing env_requires → module FAIL → существующая severity-агрегация (critical→exit 2, warn→exit 1). Rejected: silent WARN + deploy с пустым паролем (текущее поведение).

### D4 — hermes image check: derive из compose config
## @rationale Q: откуда брать образ для pre-deploy check? A: `docker compose -f base.yml --profile hermes-agent config --images` резолвит `${CONTEXT_IMAGE}` из env-file — единственный источник истины. Hardcoded `ghcr.io/tronyx161/hermes-agent-*` удаляется (knowledge dedup, Step 1.11). Дополнительно: контейнер с совпадающим `container_name`, но без label `com.docker.compose.project` данного проекта → stop/rm перед up (иначе compose up падает name-conflict и стейл-контейнер крутится вечно — текущее состояние hermes-agent).

### Configuration DRY
Дублируемое знание «образ hermes L2» существовало в 2 местах (compose + deploy-modules.sh) и разошлось (`tronyxlab/hermes-agent-context:v2026.7.1` vs `tronyx161/hermes-agent-tronyx-lab:latest`). T4 сводит к одному источнику (compose). Прочие дубли (redis digest) — вне scope, уже под TRAP[DECISION].

## 3. Architecture / Data Flow (после фиксов)

```
make bootstrap-node / node-update
  ├─ scp-deliver / core-deploy CI: rsync core/ --exclude 'default-user.xml' ...   (T2)
  ├─ decrypt-secrets → /run/platform/secrets.env
  ├─ deploy-modules.sh (per module):
  │    1. env_requires gate: module.yaml → все vars непустые в env/secrets.env?  (T3)
  │    │    └─ нет → log FAIL (список vars) → module failed → severity exit
  │    2. hermes-agent: images := docker compose config --images (resolved)      (T4)
  │    3. orphan reconciliation: container_name занят чужим контейнером → rm     (T4)
  │    4. docker compose up -d  → ClickHouse entrypoint пишет default-user.xml
  │                               ВНУТРИ контейнера из текущего env             (T1)
  └─ modules-healthcheck.sh: все container_name; Restarting/RestartCount>5=FAIL (T5)
```

## 4. $TASKS

| ID | Задача | Файлы | Acceptance | Deps | Cx |
|----|--------|-------|-----------|------|----|
| T1 | ClickHouse mount-рефакторинг: `./config/users.d:...` (dir) → `./config/users.d/10-users.xml:/etc/clickhouse-server/users.d/10-users.xml:ro` (per-file). Удалить локальный артефакт `config/users.d/default-user.xml`. Обновить MODULE_CONTRACT invariants. Переписать tests/test_clickhouse_config.py: (a) base.yml монтирует только 10-users.xml per-file ro; (b) users.d как каталог НЕ монтируется; (c) в репо-каталоге users.d нет default-user.xml (guard). | clickhouse/docker-compose.base.yml, clickhouse/module.yaml, tests/test_clickhouse_config.py, (delete) config/users.d/default-user.xml | `make test MARKER=static` PASS; `docker compose config` для clickhouse валиден; grep не находит dir-mount users.d | — | 3 |
| T2 | rsync-исключения runtime-артефактов: `--exclude 'default-user.xml'` в оба rsync core/ (scp-deliver.sh Phase 1, core-deploy.yml). Также `--exclude '.env'` для core/ (гигиена: core/modules/hermes-agent/.env обнаружен на VPS). Static-тест: оба файла содержат exclude. | core/internal/bootstrap/scp-deliver.sh, .github/workflows/core-deploy.yml, tests/test_deploy_delivery_static.py (new) | static-тест PASS; shellcheck scp-deliver.sh чист | — | 2 |
| T3 | env_requires gate в deploy-modules.sh: функция `_check_env_requires(module)` — парсит module.yaml env_requires (python3, как _get_module_severity), для каждой var проверяет непустоту в `${!var}` ИЛИ в `--env-file` secrets.env; missing → `log_step FAIL` со списком + return 1 (до compose up, в deploy_docker_module и deploy_system_module). + Констрейнт URL-safe: комментарий в .env.example у CLICKHOUSE_PASSWORD (`только [A-Za-z0-9._-]` — пароль встраивается в CLICKHOUSE_MIGRATION_URL без encoding) + static-тест charset dev-значений. + .env.example: MINIO_ROOT_USER/PASSWORD помечены как обязательные (env_requires minio). | core/internal/bootstrap/deploy-modules.sh, .env.example, tests/test_deploy_gates_static.py (new) | Unit: модуль с пустой env_requires-var не доходит до compose up, exit-код по severity; static-тесты PASS | — | 5 |
| T4 | hermes image check + orphan reconciliation: (a) заменить hardcoded l1/l2 образы на `docker compose "${compose_args[@]}" config --images`; _check_image_exists для каждого; (b) generic pre-up: для каждого container_name из compose config — если контейнер существует и его label `com.docker.compose.project` ≠ проекту модуля → docker stop/rm + log INFO; (c) static-тест: deploy-modules.sh не содержит `ghcr.io/tronyx161/hermes-agent`. | core/internal/bootstrap/deploy-modules.sh, tests/test_deploy_gates_static.py | grep hardcoded-образов пуст; повторный deploy hermes-agent поверх чужого контейнера не падает name-conflict | T3 (тот же файл) | 5 |
| T5 | modules-healthcheck.sh robustness: (a) все `container_name` из base.yml (не `head -1`); (b) `docker inspect .State.Restarting=true` ИЛИ `RestartCount>5` → FAIL (ловит restart loop, который сейчас «starting»=WARN); (c) `not-found` для docker-модуля, объявленного в node.yaml, → FAIL, для необъявленного → SKIP (если node.yaml недоступен — текущее SKIP-поведение); (d) system oneshot (platform-secrets) остаётся PASS через module healthcheck.sh. Static-тест на (a)+(b). | core/internal/healthcheck/modules-healthcheck.sh, tests/test_healthcheck_static.py (new) | Restart-looping контейнер даёт exit 1; platform-secrets PASS; static-тест PASS | — | 4 |
| T7 | **OPS (ручной, оператор):** добавить в SOPS `node-configs/secrets/tronyx-vps.enc.yaml`: `MINIO_ROOT_USER`, `MINIO_ROOT_PASSWORD` (сгенерировать), проверить `CLICKHOUSE_PASSWORD` URL-safe; затем — после завершения работы другого агента — `make node-update NODE=tronyx-vps`. Ожидание: langfuse healthy, hermes-agent пересоздан (gateway run), createbuckets exit 0. | node-configs (вне репо), VPS | Все 6 acceptance criteria §5 на живой ноде | T1-T5 задеплоены | 2 |

Merge-rule: бывший T6 (URL-safe констрейнт, ≤2 файла/≤20 строк) влит в T3.

## 5. Acceptance Criteria

| # | Критерий | Проверка |
|---|----------|---------|
| A1 | Ротация CLICKHOUSE_PASSWORD сходится: смена значения в secrets + `compose up -d` → langfuse-миграции проходят | Локально: сменить пароль в .env, `make restart` clickhouse+langfuse, langfuse healthy |
| A2 | `git status --ignored core/modules/clickhouse/` не содержит default-user.xml после локального запуска стека | локальный smoke |
| A3 | rsync-манифесты (scp-deliver.sh, core-deploy.yml) исключают default-user.xml и .env | static-тест |
| A4 | Модуль minio без MINIO_ROOT_USER в env → deploy FAIL до compose up, сообщение перечисляет vars | unit/static-тест |
| A5 | deploy-modules.sh не содержит hardcoded hermes-образов; image check работает от compose config | static-тест + shellcheck |
| A6 | Контейнер в restart loop → `make healthcheck` exit 1 | static-тест + локальный smoke |

## 6. File Manifest

| Файл | Действие |
|------|----------|
| core/modules/clickhouse/docker-compose.base.yml | edit (mount) |
| core/modules/clickhouse/module.yaml | edit (invariants) |
| core/modules/clickhouse/config/users.d/default-user.xml | **delete** (локальный артефакт) |
| core/internal/bootstrap/scp-deliver.sh | edit (excludes) |
| .github/workflows/core-deploy.yml | edit (excludes) |
| core/internal/bootstrap/deploy-modules.sh | edit (env_requires gate, image check, orphan rm) |
| core/internal/healthcheck/modules-healthcheck.sh | edit (robustness) |
| .env.example | edit (констрейнты, MINIO required) |
| tests/test_clickhouse_config.py | rewrite |
| tests/test_deploy_delivery_static.py | new |
| tests/test_deploy_gates_static.py | new |
| tests/test_healthcheck_static.py | new |

## 7. $TEST_SPEC

| Test file | Test function | Scenario | Module under test |
|-----------|---------------|----------|-------------------|
| tests/test_clickhouse_config.py | test_users_d_per_file_mount | base.yml монтирует только 10-users.xml, ro; dir-mount users.d отсутствует | clickhouse compose |
| tests/test_clickhouse_config.py | test_no_default_user_artifact_in_repo | В repo users.d/ нет default-user.xml | clickhouse config |
| tests/test_deploy_delivery_static.py | test_rsync_excludes_runtime_artifacts | scp-deliver.sh и core-deploy.yml содержат exclude default-user.xml и .env | delivery |
| tests/test_deploy_gates_static.py | test_env_requires_gate_present | deploy-modules.sh содержит _check_env_requires, вызывается до compose up в обеих ветках | deploy-modules |
| tests/test_deploy_gates_static.py | test_no_hardcoded_hermes_images | Нет `ghcr.io/tronyx161/hermes-agent` в deploy-modules.sh | deploy-modules |
| tests/test_deploy_gates_static.py | test_clickhouse_password_url_safe | dev-значения CLICKHOUSE_PASSWORD (.env.example, platform-env.yaml) соответствуют `^[A-Za-z0-9._-]+$`; комментарий-констрейнт присутствует | env contract |
| tests/test_deploy_gates_static.py | test_minio_env_requires_documented | MINIO_ROOT_USER/PASSWORD задокументированы в .env.example | env contract |
| tests/test_healthcheck_static.py | test_healthcheck_checks_all_containers | modules-healthcheck.sh перебирает все container_name (нет `head -1` в docker-ветке) | healthcheck |
| tests/test_healthcheck_static.py | test_healthcheck_detects_restart_loop | Присутствует проверка State.Restarting/RestartCount → FAIL | healthcheck |

Все тесты — маркер `static_audit`, с LDD-телеметрией (IMP:7-10, caplog) по §TESTING.

## 8. $PARALLEL_GROUPS

### Wave 1 (независимые, без общих файлов)
- Tasks: T1, T2, T5
- Command: `coder Read .ai/plans/001-bootstrap-systemic-fixes/01-DevPlan.md, implement Wave 1: T1, T2, T5`

### Wave 2 (общий файл deploy-modules.sh — одна сессия)
- Tasks: T3, T4
- Command: `coder Read .ai/plans/001-bootstrap-systemic-fixes/01-DevPlan.md, implement Wave 2: T3, T4`

### Wave 3 (ручной ops-шаг, оператор)
- Tasks: T7 — SOPS + `make node-update NODE=tronyx-vps` (после ухода другого агента с VPS)

## 9. Constraints / Out of scope

- **VPS не трогаем** — там работает другой агент. Сходимость достигается следующим `node-update` (T7).
- Из scope исключены: аналогичная ротация POSTGRES_PASSWORD (initdb-пароль в data dir — отдельная задача; зафиксировать как TRAP[DEBT] в postgres/docker-compose.base.yml при реализации T1), langfuse upstream URL-encoding (наш констрейнт charset достаточен), redis-digest dedup (существующий TRAP[DECISION]).
- Существующее поведение `${VAR:-}` в base.yml (DD3) сохраняется — гейт живёт в deploy-modules.sh, не в compose.

## Next Steps
### Wave 1
Use coder role and read /Users/tronyx/projects/ai-platform/.ai/plans/001-bootstrap-systemic-fixes/01-DevPlan.md, implement Wave 1: T1, T2, T5
### Wave 2
Use coder role and read /Users/tronyx/projects/ai-platform/.ai/plans/001-bootstrap-systemic-fixes/01-DevPlan.md, implement Wave 2: T3, T4
### Wave 3 (manual)
Operator: SOPS update (MINIO creds, CLICKHOUSE_PASSWORD charset) → `make node-update NODE=tronyx-vps`

$END_DEVPLAN
