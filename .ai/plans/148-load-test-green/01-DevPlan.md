$START_DOCUMENT_PLAN
### Document Plan
**SECTION_GOALS:**
- GOAL [Контракт и цели плана «полный цикл трёх волн»] => G_CONTRACT
- GOAL [Evidence: что осталось после 146-m1..m8, чего не хватает] => G_EVIDENCE
- GOAL [SUPERPOSITION: db-сценарий, сеть, auth, статистика — решения] => G_SUPERPOSITION
- GOAL [TASK-декомпозиция W1 (Coder) / W2 (Sysadmin) / W3 (QA)] => G_TASKS
- GOAL [План верификации: 3 волны × 3 режима + сводная статистика] => G_VERIFY
**SECTION_USE_CASES:**
- USE_CASE [Сводная статистика: duration_s, rps, p95/p99, max_rps по web/s3/db] => SC_STATS
- USE_CASE [PostgreSQL: скорость записи/чтения (read/write per-task)] => SC_DB_RW
$END_DOCUMENT_PLAN

$START_DEVPLAN
# 01-DevPlan — 148: Load-test green — полный цикл трёх волн + db (PostgreSQL read/write)

# region MODULE_CONTRACT
## @purpose  Закрыть остаток после 146-m1..m8: (1) db-сценарий — реальная read/write
##           нагрузка на PostgreSQL (сейчас заглушка без HTTP-моста); (2) capacity для
##           web/s3/db (нет capacity_start_rps в SoT → exit 4); (3) длительность прогона
##           (duration_s) и per-task breakdown в отчёте — статистика «сколько времени
##           выполняется» и «скорость записи vs чтения»; (4) полный цикл прогонов
##           трёх волн (web, s3, db) × трёх режимов (smoke, regression, capacity) со
##           сводной статистикой и baseline.
## @scope    core/loadtest/scenarios/{db.py, pgwire.py(NEW)}, scenarios.yaml,
##           core/internal/loadtest/{runner_remote.py, config.py, report.py, runner_cli.py},
##           tests/unit/{test_loadtest_pgwire.py(NEW), test_loadtest_report.py,
##           test_loadtest_config.py, test_loadtest_runner_remote.py},
##           docs/load-testing.md, .ai/plans/148-load-test-green/
## @invariants
##   1. Ноль новой инфраструктуры: db-сценарий — чистый stdlib PG wire protocol
##      (socket+hashlib+hmac+base64), паттерн s3.py (SigV4 без boto3); кастомный
##      locust-образ/HTTP-мост/pgbench ЗАПРЕЩЕНЫ (инвариант 5 DevPlan 146).
##   2. PostgreSQL доступен ТОЛЬКО в docker-сети shared-db-net (NO ports: directive
##      postgres/pgbouncer) → LOAD_RUNNER=node + LOAD_NETWORK=shared-db-net
##      (docker run --network, параметр runner_remote; default host — web/s3 не меняются).
##   3. Auth: SCRAM-SHA-256 (PG16 password_encryption default, pgbouncer AUTH_TYPE) +
##      md5 fallback (pg_hba 172.16.0.0/12 md5) — клиент выбирает по сообщению сервера.
##   4. Статистика: duration_s (t1-t0) в report.json/history/markdown; per-task
##      breakdown в parse_stats_csv (locust stats.csv содержит строки по задачам —
##      read_query/write_query отдельно: rps/p95/p99/error_rate).
##   5. capacity web/s3/db — capacity_start_rps в SoT (2); guard пройдёт на
##      NODE=test-e2e (is_test_node=True, тот же host) БЕЗ LOAD_ALLOW_PROD;
##      LOAD_ALLOW_PROD=1 — осознанная альтернатива на tronyx-vps.
##   6. Языковая политика: вся новая логика — Python (pgwire.py — чистый модуль без
##      locust-импорта, unit-тестируем native pytest); make/SoT — без бизнес-логики.
## @rationale Пользовательский запрос: «запустить все нагрузочные тесты, статистика
##            по трём волнам, что сколько времени выполняется и какую максимальную
##            нагрузку выдерживает; интересует PostgreSQL на скорость записи/чтения,
##            информативно для отслеживания изменений после перенастройки сервисов».
##            План — продолжение DevPlan 146 (единая подсистема, максимальное
##            переиспользование: SoT, runner, отчёты, baseline, capacity-механика).
## @changes  2026-08-12 | DevPlan 148 — Created
# endregion MODULE_CONTRACT

$ARTIFACT_CONTRACT
PURPOSE:               Полный зелёный цикл нагрузочных тестов (web/s3/db × smoke/regression/capacity) с информативной статистикой (длительность, max_rps, per-task read/write для PostgreSQL) на tronyx-vps/test-e2e.
DESCRIPTION:           1 волна (Coder): db-сценарий на stdlib PG wire protocol (read SELECT + write INSERT, SCRAM-SHA-256 + md5 auth), LOAD_NETWORK в runner_remote, duration_s + per-task breakdown в отчёт, capacity_start_rps для web/s3/db в SoT, unit-тесты, docs. 2 волна (Sysadmin): langfuse-ключи (pk-lf_/sk-lf_), mock-litellm контейнер (по решению), прогоны. 3 волна (QA/оператор): прогоны 3×3 + сводная таблица статистики + baseline-запись.
RATIONALE:             db-сценарий — единственная «дыра» подсистемы (заглушка без HTTP-моста); PostgreSQL — сервис №1 для отслеживания перф-изменений (pg_stat_database уже в saturation). Отчёт без duration_s и per-task не отвечает на вопросы пользователя (сколько времени, скорость записи vs чтения). capacity без capacity_start_rps невыполним (exit 4).
ACCEPTANCE_CRITERIA:   1) `make load-test SCENARIO=db NODE=test-e2e MODE=smoke LOAD_SCENARIO_DB=1 LOAD_RUNNER=node LOAD_NETWORK=shared-db-net LT_PG_USER=postgres LT_PG_PASSWORD=<secret> LT_PG_DB=platform` → exit 0, verdict PASS/WARN; 2) report.json содержит duration_s и tasks.{read_query,write_query}; 3) capacity web/s3/db → max_rps > 0 (PASS); 4) `make check` зелёный; 5) сводная таблица 3×3 в отчёте сессии.
IMPLEMENTS:            DevPlan 146 (146-m1..m8) — closing wave; пользовательский запрос 2026-08-12
IMPACTS:               core/loadtest/scenarios/db.py (rewrite), core/loadtest/scenarios/pgwire.py (NEW), core/loadtest/scenarios.yaml, core/internal/loadtest/runner_remote.py, config.py, report.py, runner_cli.py, tests/unit (4 файла), docs/load-testing.md
REQUIRES:              SSH-доступ к tronyx-vps (103.88.243.151); POSTGRES_PASSWORD из secrets ноды; SSH-туннель Prometheus (bgp_ff26f503e0013HpzzbhAx2BOI0, persistent) для saturation; LANGFUSE-ключи (W2, Sysadmin)
$END_ARTIFACT_CONTRACT

---

## 1. Evidence — состояние после 146-m1..m8 (2026-08-11)

| Сценарий | Режим | Статус | Причина / блокер |
|----------|-------|--------|------------------|
| web | smoke / regression | WARN, exit 0 | ✅ работает (rps 10.2/10.06, p95 0.25/0.22s, 0 errors); WARN — missing litellm_proxy_* (экспортёр не отдаёт — диагностика, не баг) |
| web | capacity | **exit 4** | ❌ `capacity_start_rps` отсутствует в SoT (есть только у llm) |
| s3 (node-runner) | smoke | WARN, exit 0 | ✅ работает (rps 5.22, p95 99ms); WARN — litellm-метрики |
| s3 | capacity | **exit 4** | ❌ нет `capacity_start_rps` |
| db | — | **заглушка** | ❌ optional, GET через несуществующий HTTP-мост (403/нет endpoint) |
| langfuse_ingest | smoke | FAIL 403 | ❌ placeholder-ключи LANGFUSE_PUBLIC_KEY/SECRET_KEY на ноде (инфраструктура, Sysadmin) |
| llm / llm_stream | smoke | FAIL | ❌ mock-модель отсутствует на проде (guard корректен; mock только на test-ноду) |
| capacity guard | — | exit 10 | ✅ корректен (tronyx-vps не test; NODE=test-e2e — test-нода, тот же host) |

**Чего не хватает для запроса пользователя:**

1. **db-сценарий не существует как нагрузка.** PostgreSQL — главный интерес («скорость записи / чтения, отслеживать изменения после перенастройки»). Сейчас `db.py` — 87 строк заглушки (GET по HTTP-пути, которого нет).
2. **Нет duration_s в отчёте/history.** Пользователь: «что сколько времени выполняется». В report.json есть только timestamp; t1-t0 вычисляется, но не персистится.
3. **Нет per-task breakdown.** Locust stats.csv содержит строки по именам задач (read_query/write_query), но parse_stats_csv берёт только Aggregated → скорость записи и чтения PostgreSQL неразличимы.
4. **capacity для web/s3/db невозможен** (exit 4: нет capacity_start_rps).
5. **Нет сводной статистики «какую максимальную нагрузку выдерживает»** — есть max_rps в capacity-отчёте, но сводная таблица по трём волнам не собрана.

**Инфраструктурные ограничения ноды (не код, W2 Sysadmin):**
- langfuse: placeholder-ключи → 403. Процедура: `pk-lf_$(openssl rand -hex 16)`, `sk-lf_$(openssl rand -hex 16)` → secrets.env ноды → перезапуск langfuse (headless init при первом старте; если БД уже инициализирована — пересоздать проект/ключи через API или очистить langfuse-БД, см. docs/load-testing.md §8-аналог).
- llm: mock-litellm — изолированный docker run на ноде (НЕ compose, НЕ прод-конфиг): `docker run -d --name lt-mock-litellm --network shared-db-net -p 127.0.0.1:14000:4000 -e DATABASE_URL=postgresql://postgres:<pw>@pgbouncer:6432/litellm -e LITELLM_MASTER_KEY=<k> -v <mock.yml>:/app/config.yml ghcr.io/berriai/litellm:v1.91.2 --config /app/config.yml` + `LOAD_ENDPOINT_LLM=http://127.0.0.1:14000` (механизм override уже есть, 146-m1 BUG-2). По решению пользователя (прод-нода!).

---

## 2. SUPERPOSITION — решения (системные, в рамках имеющейся архитектуры)

### 2.1 db-сценарий: как гнать нагрузку на PostgreSQL

| Вариант | Оценка | Вердикт |
|---------|--------|---------|
| **A. Чистый stdlib PG wire protocol в locust-сценарии** (socket, StartupMessage, auth, Simple Query; `pgwire.py` — чистый модуль, db.py — locust-фасад) | Паттерн платформы: s3.py реализует SigV4 на stdlib без boto3 (146 W1). Ноль зависимостей, работает и локально, и в контейнере; unit-тестируемо без сервера (RFC 7677 vector для SCRAM, md5 vector). ~250-300 LOC | ✅ **ПРИНЯТО** |
| B. HTTP-мост к postgres (новый сервис/endpoint) | Новая инфраструктура + нарушение инварианта 5 DevPlan 146 («нулевая новая инфраструктура») + новый compose-сервис | ❌ отклонено |
| C. psycopg2/pg8000 в locust-образе | Кастомный образ = Dockerfile + сборка + push + дрейф версий; нарушает «ноль новой инфраструктуры» | ❌ отклонено |
| D. pgbench на ноде (ssh) | Вне locust: нет отчётов/baseline/verdict, не в архитектуре подсистемы | ❌ отклонено |

**@rationale (A):** «максимальное переиспользование архитектуры» — тот же приём, что s3.py: экосистема locust-образа ограничена (нет boto3), но stdlib достаточен. PG wire protocol для Simple Query детерминирован (StartupMessage → R (auth) → Q → C/Z), SCRAM-SHA-256 реализуется через `hashlib.pbkdf2_hmac`+`hmac`+`base64`, md5 — через `hashlib.md5`. Unit-тесты на построение сообщений и auth-векторы (RFC 7677 §3) — без живого сервера.

### 2.2 Сеть: как контейнер locust достанет postgres

| Вариант | Оценка | Вердикт |
|---------|--------|---------|
| **A. LOAD_NETWORK env → `docker run --network <net>`** (default `host` — web/s3 не меняются; db: `shared-db-net`) | Минимальное изменение runner_remote (build_ssh_docker_run_cmd + run_remote_locust + config) | ✅ **ПРИНЯТО** |
| B. Проброс порта postgres на host (127.0.0.1:5432) | Нарушает «NO ports: directive — internal network only» (инвариант модуля postgres), меняет прод-поверхность | ❌ отклонено |
| C. SSH-туннель до postgres | Порт не на host → туннель не поможет; docker-сеть недоступна снаружи | ❌ отклонено |

**@rationale (A):** postgres/pgbouncer публикуют только в docker-сеть `shared-db-net` (docker-compose.base.yml: «NO ports: directive»). Контейнер генератора уже запускается через `docker run` на ноде (runner_remote) — добавление параметра сети — расширение существующего builder'а, не новая инфраструктура. DNS-алиасы `postgres`/`pgbouncer` работают внутри сети → endpoint `postgres:5432` без хардкода IP.

### 2.3 Auth: SCRAM-SHA-256 vs md5

| Вариант | Оценка | Вердикт |
|---------|--------|---------|
| **A. Оба: клиент читает AuthenticationRequest (R) и выбирает SASL(10)→SCRAM-SHA-256 | MD5(5)→md5** | PG16 default password_encryption=scram-sha-256; pgbouncer AUTH_TYPE=scram-sha-256; pg_hba md5 (172.16.0.0/12) — при scram-хранилище сервер шлёт SASL. md5 fallback — старые/тестовые БД | ✅ **ПРИНЯТО** |
| B. Только md5 | Не сработает против pgbouncer (scram) и PG16 со scram-паролем | ❌ отклонено |
| C. Только SCRAM | Сломает тестовые ноды с md5-паролями | ❌ отклонено |

**@rationale (A):** сообщение AuthenticationMD5Password (R, код 5) и AuthenticationSASL (R, код 10) различимы по коду — выбор механизма тривиален. SCRAM-SHA-256 — RFC 5802/7677, ~80 строк на stdlib, unit-тест по официальному RFC-вектору (user/pencil/salt W22ZaJ0SNY7soEsUEjb6gQ==/i=4096 → p=dHzbZapWIk4jUhN+Ute9ytag9zjfMHgsqmmiz7AndVQ=).

### 2.4 Статистика: что добавить в отчёт

| Вариант | Оценка | Вердикт |
|---------|--------|---------|
| **A. duration_s (t1-t0) + per-task breakdown (tasks: {name: {rps,p95,p99,error_rate}})** в report.json/markdown/history | parse_stats_csv уже читает все строки CSV (per-task строки есть в locust stats.csv: Name=read_query/write_query) — расширение парсера, обратная совместимость (Aggregated остаётся stats.*) | ✅ **ПРИНЯТО** |
| B. Отдельные сценарии db_read / db_write | Дублирование сценариев, лишние прогоны, хуже baseline | ❌ отклонено |
| C. Только duration_s | Не отвечает на «скорость записи vs чтения» | ❌ отклонено |

**@rationale (A):** locust stats.csv формирует строку на каждую задачу (read_query/write_query) + Aggregated. Парсер сейчас берёт только Aggregated — добавление словаря tasks = ~20 строк, даёт раздельные rps/p95/p99 для записи и чтения. duration_s — готовые t0/t1 в runner_cli, добавляется в build_report + run_row (history.json).

### 2.5 capacity: как пройти guard на production-ноде

| Вариант | Оценка | Вердикт |
|---------|--------|---------|
| **A. NODE=test-e2e для capacity** (is_test_node=True: contexts[0].name=test; host = 103.88.243.151 — та же VPS) | Штатный guard, без LOAD_ALLOW_PROD; история в history/test-e2e/ (отдельный baseline от smoke/regression на tronyx-vps — плюс: не смешиваются) | ✅ **ПРИНЯТО (рекомендуется)** |
| B. LOAD_ALLOW_PROD=1 на tronyx-vps | Осознанное решение пользователя (он просит max нагрузку на ноде); guard-флаг существует для этого | ⚠️ альтернатива по решению пользователя |
| C. Не гонять capacity | Противоречит запросу («какую максимальную нагрузку выдерживает») | ❌ отклонено |

**@rationale (A):** node-configs/test-e2e/node.yaml — реальная test-нода (contexts[0].name: test), указывает на тот же IP (пересоздаваемая тестовая VPS, инвариант 9). capacity guard проверяет is_test_node по конфигу — test-e2e проходит штатно. Отдельная история — чище для сравнения capacity-трендов.

---

## 3. TASK-декомпозиция

### TASK-1: `core/loadtest/scenarios/pgwire.py` — NEW, чистый stdlib PG wire protocol клиент
**Файл:** `core/loadtest/scenarios/pgwire.py` (новый; ~250-300 LOC)
**Содержание:**
- `build_startup_message(user, database, protocol=196608)` — StartupMessage (len + proto + params\0).
- `md5_password_hash(user, password, salt)` → `"md5" + md5(md5(password+user)+salt)` (hex).
- `scram_client_first(user, nonce)`, `scram_client_final(user, password, client_first_bare, server_first)` — RFC 5802/7677: pbkdf2_hmac-sha256(salt,i), ClientKey/StoredKey, ClientSignature, proof; возвращает client-final + server-verify (v=).
- `PGSocket` (или функции `connect/query`): socket → StartupMessage → чтение R/S/K/Z → auth по коду (0 OK, 5 MD5, 10 SASL, 3 cleartext — сообщение об ошибке) → `query(conn, sql)` → Simple Query 'Q' → парсинг ответов: T (RowDescription), D (DataRow), C (CommandComplete), E (ErrorResponse → raise PgError с сообщением сервера), Z (ReadyForQuery). Таймауты (socket timeout 10s).
- `PgError(Exception)` — сообщение сервера (для locust failure).
- GREP_SUMMARY/STRUCTURE/MODULE_CONTRACT; LDD [IMP:8/9] логи.
- **НИКАКОГО locust-импорта** — чистый модуль, unit-тестируемый native pytest.
- **Acceptance:** `pytest tests/unit/test_loadtest_pgwire.py` — PASS (RFC-вектор SCRAM, md5-вектор, парсинг сообщений, startup-фрейминг).

### TASK-2: `core/loadtest/scenarios/db.py` — REWRITE: read/write нагрузка на PostgreSQL
**Файл:** `core/loadtest/scenarios/db.py` (переписать, ~150 LOC)
**Содержание:**
- Env: `LT_ENDPOINT` (host:port, напр. `postgres:5432`), `LT_PG_USER` (default `postgres`), `LT_PG_PASSWORD`, `LT_PG_DB` (default `platform`), `LT_PG_TABLE` (default `loadtest_metrics`), `LT_TARGET_RPS`/`LT_USERS` (rps_wait_time — существующий helper).
- `DbUser(User)` (НЕ HttpUser — свой transport): `on_start` — connect + `CREATE TABLE IF NOT EXISTS loadtest_metrics (id bigserial primary key, ts timestamptz default now(), payload text)` + `DELETE FROM loadtest_metrics` (идемпотентный старт, чистая таблица между прогонами); `on_stop` — close.
- Задачи с весом 1:1 — `read_query` (SELECT count(*) FROM loadtest_metrics — «скорость чтения») и `write_query` (INSERT INTO loadtest_metrics (payload) VALUES ('loadtest-<n>') — «скорость записи»). Каждая задача — `query()` через общее соединение; исключения PgError → raise (locust засчитает failure).
- Guard LT_ENABLED (optional-контракт, как s3.py); sys.path hack для rps_wait_time (существующий паттерн).
- **Acceptance:** сценарий импортируется locust; на ноде создаёт таблицу, read/write выполняются; в stats.csv две задачи (read_query/write_query).

### TASK-3: `core/loadtest/scenarios.yaml` — SoT: db-сценарий + capacity_start_rps
**Файл:** `core/loadtest/scenarios.yaml`
**Содержание:**
- `db`: endpoint `postgres:5432` (внутри shared-db-net, DNS-алиас), `network: shared-db-net` (новое поле, default host), target_rps: 5, users: 10, capacity_start_rps: 2, max_p95: 1.0, max_p99: 3.0, max_error: 0.05; description — «pg read/write через wire protocol (LOAD_SCENARIO_DB=1, LOAD_RUNNER=node, LOAD_NETWORK=shared-db-net)».
- `web`: + `capacity_start_rps: 2`.
- `s3`: + `capacity_start_rps: 2`.
- Инварианты MODULE_CONTRACT: + network-поле, + db-read/write, + per-task.
- **Acceptance:** `LOAD_SCENARIO_DB=1` → db не optional-off; capacity web/s3/db проходит валидацию (не exit 4).

### TASK-4: `core/internal/loadtest/runner_remote.py` — параметр сети
**Файл:** `core/internal/loadtest/runner_remote.py`
**Содержание:**
- `build_ssh_docker_run_cmd(..., network: str = "host")` — `docker run --rm --network {network}`.
- `run_remote_locust(..., network="host")` — проброс параметра.
- DEFAULT_NETWORK = "host" (константа, комментарий: web/s3 — host-сеть, db — shared-db-net).
- Инварианты/STRUCTURE обновить.
- **Acceptance:** unit-тест: `--network shared-db-net` в команде при network="shared-db-net"; default → `--network host`.

### TASK-5: `core/internal/loadtest/config.py` — network + LT_PG_* passthrough
**Файл:** `core/internal/loadtest/config.py`
**Содержание:**
- `ScenarioSpec.network: str = "host"` — из SoT (raw.get("network", defaults.get("network", "host"))); валидация: только `host`|`shared-db-net` (или любой непустой docker-сети — allowlist: host, shared-db-net; остальное → ConfigValidationError 4).
- `LoadtestConfig.network` — из scenario.network; env override `LOAD_NETWORK` (приоритет env, как LOAD_ENDPOINT_*).
- Docstrings/инварианты: db требует LOAD_RUNNER=node (документировать, не валидировать жёстко — dev-локальный запуск с SSH-туннелем к сети невозможен, но предупредить через logger.warning при db + local).
- **Acceptance:** unit-тесты: network из SoT (db→shared-db-net), LOAD_NETWORK override, невалидная сеть → exit 4.

### TASK-6: `core/internal/loadtest/report.py` — duration_s + per-task breakdown
**Файл:** `core/internal/loadtest/report.py`
**Содержание:**
- `parse_stats_csv` → возвращает `(Stats, tasks: dict[str, dict])`: по строкам CSV, где Name != Aggregated — {name: {rps, p95, p99, error_rate}} (перцентили ÷1000, как BUG-3 fix).
- `build_report(..., duration_s: float | None = None, tasks: dict | None = None)` — поля `duration_s`, `tasks` в report.json.
- `render_markdown` — строки Duration + таблица tasks (| task | rps | p95 | p99 | error_rate |).
- Обратная совместимость: все параметры опциональны; существующие тесты не ломаются.
- **Acceptance:** unit-тесты: CSV с двумя задачами → tasks dict; duration_s в report.json/markdown.

### TASK-7: `core/internal/loadtest/runner_cli.py` — duration + tasks в отчёт/history
**Файл:** `core/internal/loadtest/runner_cli.py`
**Содержание:**
- `_run_one_step` → возвращает также `tasks` (из parse_stats_csv).
- `_run_single_mode`/`_run_capacity_mode`: `duration_s = round(t1 - t0, 1)` → build_report; run_row history += `"duration_s"` (и `"tasks"` для smoke/regression).
- **Acceptance:** report.json содержит duration_s; history.json run содержит duration_s; markdown показывает Duration и tasks.

### TASK-8: `tests/unit/test_loadtest_pgwire.py` — NEW
**Файл:** `tests/unit/test_loadtest_pgwire.py` (NEW)
**Содержание:**
- `test_startup_message_framing` — длина/поля (len, proto 196608, user, database).
- `test_md5_password_hash_rfc` — известный вектор md5 (user/postgres/password).
- `test_scram_rfc7677_vector` — RFC 7677 §3: user/pencil, nonce rOprNGfwEbeRWgbNEkqO, salt W22ZaJ0SNY7soEsUEjb6gQ==, i=4096 → client-final proof `dHzbZapWIk4jUhN+Ute9ytag9zjfMHgsqmmiz7AndVQ=`.
- `test_parse_backend_messages` — парсинг T/D/C/E/Z (фикстура байтовой последовательности).
- `test_query_error_raises_pgerror` — ErrorResponse → PgError с сообщением.
- LDD: IMP:9 assert (Anti-Illusion Rule); caplog.
- **Acceptance:** `pytest tests/unit/test_loadtest_pgwire.py -q` — все PASS.

### TASK-9: `tests/unit/test_loadtest_report.py` — per-task + duration
**Файл:** `tests/unit/test_loadtest_report.py`
**Содержание:**
- `test_parse_stats_csv_tasks` — CSV фикстура с read_query/write_query + Aggregated → tasks dict с rps/p95/p99/error_rate (ms→s).
- `test_build_report_duration_and_tasks` — report.json содержит duration_s и tasks.
- `test_markdown_contains_duration_and_tasks`.
- **Acceptance:** PASS.

### TASK-10: `tests/unit/test_loadtest_config.py` — network
**Файл:** `tests/unit/test_loadtest_config.py`
**Содержание:**
- `test_network_from_sot` (db → shared-db-net), `test_network_default_host` (web), `test_network_env_override` (LOAD_NETWORK), `test_network_invalid_rejected` (exit 4).
- **Acceptance:** PASS.

### TASK-11: `tests/unit/test_loadtest_runner_remote.py` — network в docker run
**Файл:** `tests/unit/test_loadtest_runner_remote.py`
**Содержание:**
- `test_docker_run_network_default_host`, `test_docker_run_network_shared_db_net` — команда содержит `--network <net>`.
- **Acceptance:** PASS.

### TASK-12: `docs/load-testing.md` — актуализация
**Файл:** `docs/load-testing.md`
**Содержание:**
- §3: db — read/write через wire protocol, env LT_PG_*, network, LOAD_RUNNER=node требование; §4: capacity web/s3/db; §7: LOAD_NETWORK; §9: duration_s + tasks в отчёте; §10: ограничения (db — только node-runner).
- **Acceptance:** grep «HTTP-мост» → 0 вхождений в контексте db (обновлено); docstring-консистентность.

---

## 4. $PARALLEL_GROUPS

### Wave 1 — Coder (код, все задачи одной волной; порядок: T1 → T2 → T3 → T4 → T5 → T6 → T7 → T8-T11 → T12)
```
coder Read .ai/plans/148-load-test-green/01-DevPlan.md, implement TASK-1..TASK-12.
Order: TASK-1 (pgwire.py) before TASK-2 (db.py imports pgwire); TASK-3 (SoT) and TASK-5 (config) independent; TASK-4 (runner_remote) independent; TASK-6/7 (report/runner_cli) after TASK-2 conceptually, files independent.
After implementation: pytest tests/unit/test_loadtest_pgwire.py tests/unit/test_loadtest_report.py tests/unit/test_loadtest_config.py tests/unit/test_loadtest_runner_remote.py -q, then make check (до чистоты).
```

### Wave 2 — Sysadmin (инфраструктура, параллельно W1-коду)
- S1: сгенерировать LANGFUSE-ключи на ноде (pk-lf_/sk-lf_, openssl rand -hex 16) → secrets.env → перезапуск langfuse; верификация: `curl https://langfuse.tronyx.ru/api/public/traces` с новым ключом → не 403.
- S2: mock-litellm контейнер (решение пользователя 2026-08-12 — ОБЯЗАТЕЛЬНАЯ задача): `docker run -d --name lt-mock-litellm --network shared-db-net -p 127.0.0.1:14000:4000 -e DATABASE_URL=postgresql://postgres:<pw>@pgbouncer:6432/litellm -e LITELLM_MASTER_KEY=<k> -v <mock.yml>:/app/config.yml ghcr.io/berriai/litellm:v1.91.2 --config /app/config.yml` + прогоны llm/llm_stream с `LOAD_ENDPOINT_LLM=http://127.0.0.1:14000` / `LOAD_ENDPOINT_LLM_STREAM=http://127.0.0.1:14000`. Прод-litellm (4000) НЕ затрагивается. Верификация: mock-probe runner'а (POST /chat/completions → openai/echo) проходит.
- S3: проверить SSH-туннель Prometheus (persistent bgp_...): `LOAD_PROMETHEUS_HOST=localhost LOAD_PROMETHEUS_PORT=19090` для прогонов с saturation.

### Wave 3 — QA/оператор (после W1-кода; прогоны + сводная статистика)
См. §5 «План верификации».

---

## 5. $TEST_SPEC

| Test file | Test function | Scenario | Module under test |
|-----------|---------------|----------|-------------------|
| `tests/unit/test_loadtest_pgwire.py` (NEW) | `test_startup_message_framing` | Фрейминг StartupMessage | `pgwire.build_startup_message` |
| `tests/unit/test_loadtest_pgwire.py` (NEW) | `test_md5_password_hash_rfc` | md5-вектор | `pgwire.md5_password_hash` |
| `tests/unit/test_loadtest_pgwire.py` (NEW) | `test_scram_rfc7677_vector` | SCRAM-SHA-256 RFC 7677 | `pgwire.scram_client_final` |
| `tests/unit/test_loadtest_pgwire.py` (NEW) | `test_parse_backend_messages` | Парсинг T/D/C/Z | `pgwire.query`/parser |
| `tests/unit/test_loadtest_pgwire.py` (NEW) | `test_query_error_raises_pgerror` | ErrorResponse → PgError | `pgwire` |
| `tests/unit/test_loadtest_report.py` | `test_parse_stats_csv_tasks` | per-task breakdown | `report.parse_stats_csv` |
| `tests/unit/test_loadtest_report.py` | `test_build_report_duration_and_tasks` | duration_s+tasks в json | `report.build_report` |
| `tests/unit/test_loadtest_report.py` | `test_markdown_contains_duration_and_tasks` | markdown | `report.render_markdown` |
| `tests/unit/test_loadtest_config.py` | `test_network_from_sot` | network из SoT | `config.parse_scenario` |
| `tests/unit/test_loadtest_config.py` | `test_network_default_host` | default host | `config.parse_scenario` |
| `tests/unit/test_loadtest_config.py` | `test_network_env_override` | LOAD_NETWORK | `config.load_config` |
| `tests/unit/test_loadtest_config.py` | `test_network_invalid_rejected` | невалидная сеть → 4 | `config.load_config` |
| `tests/unit/test_loadtest_runner_remote.py` | `test_docker_run_network_default_host` | --network host | `runner_remote.build_ssh_docker_run_cmd` |
| `tests/unit/test_loadtest_runner_remote.py` | `test_docker_run_network_shared_db_net` | --network shared-db-net | `runner_remote.build_ssh_docker_run_cmd` |

---

## 6. Acceptance Criteria

| # | Критерий | Проверка |
|---|----------|----------|
| AC1 | db smoke на test-e2e: exit 0, PASS/WARN | `LOAD_SCENARIO_DB=1 LOAD_RUNNER=node LOAD_NETWORK=shared-db-net LT_PG_USER=postgres LT_PG_PASSWORD=<secret> LT_PG_DB=platform make load-test SCENARIO=db NODE=test-e2e MODE=smoke --skip-prometheus` (первый прогон без saturation; с туннелем — полный) |
| AC2 | Отчёт db содержит tasks.read_query/write_query + duration_s | `python3 -c "import json; r=json.load(open('<latest report.json>')); print(r['duration_s'], r['tasks'])"` |
| AC3 | capacity web/s3/db → max_rps > 0 (PASS) | `make load-test SCENARIO=web NODE=test-e2e MODE=capacity` (и s3, db) |
| AC4 | `make check` зелёный | `make check` → exit 0 |
| AC5 | Сводная статистика 3×3 (см. §7) | Таблица в отчёте сессии + history.json записи с duration_s |
| AC6 | langfuse_ingest зелёный (после W2-S1) | smoke langfuse_ingest → exit 0 (ключи реальные) |
| AC7 | llm/llm_stream зелёные (после W2-S2, mock-litellm на 14000) | smoke llm + llm_stream с LOAD_ENDPOINT_* override → exit 0, 0 errors |

---

## 7. План верификации на tronyx-vps / test-e2e

### Pre-flight (dev-машина)
```bash
pytest tests/unit/test_loadtest_pgwire.py tests/unit/test_loadtest_report.py \
  tests/unit/test_loadtest_config.py tests/unit/test_loadtest_runner_remote.py -q
make check   # до чистоты
```

### Прогоны (W3) — три волны × три режима

| Волна | smoke (90s) | regression (300s) | capacity (60s×шаг) |
|-------|-------------|-------------------|--------------------|
| **web** | ✅ уже PASS/WARN (baseline есть) | ✅ уже PASS/WARN (baseline есть) | `make load-test SCENARIO=web NODE=test-e2e MODE=capacity` |
| **s3** | ✅ уже PASS/WARN | `LOAD_SCENARIO_S3=1 LOAD_RUNNER=node LT_S3_* make load-test SCENARIO=s3 NODE=tronyx-vps MODE=regression` | `LOAD_SCENARIO_S3=1 LOAD_RUNNER=node LT_S3_* make load-test SCENARIO=s3 NODE=test-e2e MODE=capacity` |
| **db** | `LOAD_SCENARIO_DB=1 LOAD_RUNNER=node LOAD_NETWORK=shared-db-net LT_PG_* make load-test SCENARIO=db NODE=tronyx-vps MODE=smoke` | то же, MODE=regression | `LOAD_SCENARIO_DB=1 LOAD_RUNNER=node LOAD_NETWORK=shared-db-net LT_PG_* make load-test SCENARIO=db NODE=test-e2e MODE=capacity` |
| **llm / llm_stream** (после W2-S2) | `LOAD_ENDPOINT_LLM=http://127.0.0.1:14000 make load-test SCENARIO=llm NODE=tronyx-vps MODE=smoke` | — (regression llm — по желанию) | `LOAD_ENDPOINT_LLM=http://127.0.0.1:14000 make load-test SCENARIO=llm NODE=test-e2e MODE=capacity` (start 2 → шаги) |

**Env-общие:** `LOAD_PROMETHEUS_HOST=localhost LOAD_PROMETHEUS_PORT=19090` (SSH-туннель) для прогонов с saturation (без `--skip-prometheus`).

### Сводная статистика (SC_STATS) — собрать по report.json/history.json

| Сценарий | Режим | duration_s | rps | p95 | p99 | max_rps (capacity) | Ошибки | Вердикт |
|----------|-------|-----------|-----|-----|-----|--------------------|--------|---------|
| web | smoke/regression/capacity | | | | | | | |
| s3 | smoke/regression/capacity | | | | | | | |
| db | smoke/regression/capacity | | | | | | | |

Источники: `load-results/<node>/<scenario>/<mode>/<ts>/report.json` (duration_s, tasks, max_rps), `core/loadtest/history/<node>/<scenario>/history.json` (тренды, delta_p95).

### НЕвыполнимое на этой ноде (ожидаемо, задокументировано)
- llm/llm_stream — только после W2-S2 (mock-litellm контейнер на 14000; до этого — ранний FAIL guard'а, корректен);
- langfuse_ingest — до генерации ключей (W2-S1);
- capacity с NODE=tronyx-vps без LOAD_ALLOW_PROD=1 (guard 10 — штатно; используем test-e2e).

---

## 8. File Manifest

| # | Файл | Изменение | TASK |
|---|------|-----------|------|
| 1 | `core/loadtest/scenarios/pgwire.py` | **NEW** — stdlib PG wire protocol (startup/md5/scram/query) | T1 |
| 2 | `core/loadtest/scenarios/db.py` | **REWRITE** — DbUser read/write через pgwire | T2 |
| 3 | `core/loadtest/scenarios.yaml` | modify — db endpoint/network, capacity_start_rps web/s3/db | T3 |
| 4 | `core/internal/loadtest/runner_remote.py` | modify — network param | T4 |
| 5 | `core/internal/loadtest/config.py` | modify — ScenarioSpec.network + LOAD_NETWORK | T5 |
| 6 | `core/internal/loadtest/report.py` | modify — duration_s + tasks | T6 |
| 7 | `core/internal/loadtest/runner_cli.py` | modify — duration/tasks в отчёт+history | T7 |
| 8 | `tests/unit/test_loadtest_pgwire.py` | **NEW** | T8 |
| 9 | `tests/unit/test_loadtest_report.py` | modify | T9 |
| 10 | `tests/unit/test_loadtest_config.py` | modify | T10 |
| 11 | `tests/unit/test_loadtest_runner_remote.py` | modify | T11 |
| 12 | `docs/load-testing.md` | modify | T12 |

**Всего: 12 файлов (2 новых, 10 изменяемых).**

---

## 9. Design Decisions Summary

| Решение | Выбор | Отклонено | @rationale |
|----------|-------|-----------|------------|
| db-транспорт | stdlib PG wire protocol (pgwire.py) | HTTP-мост, psycopg2-образ, pgbench | Паттерн s3.py (SigV4 stdlib); ноль инфраструктуры; unit-тестируемо |
| Сеть | LOAD_NETWORK → docker run --network | host-порт, SSH-туннель | postgres только в shared-db-net; расширение существующего builder |
| Auth | SCRAM-SHA-256 + md5 (по коду R) | только md5 / только scram | PG16+pgbouncer — scram; старые БД — md5 |
| Статистика | duration_s + per-task (tasks) | отдельные сценарии, только duration | locust stats.csv уже per-task; t1-t0 уже считается |
| capacity | capacity_start_rps в SoT + NODE=test-e2e | LOAD_ALLOW_PROD на проде | Штатный guard на test-ноде; отдельный baseline capacity |

---

## Next Steps

### Реализация (Coder, Wave 1)
```
coder Read .ai/plans/148-load-test-green/01-DevPlan.md, implement TASK-1..TASK-12 (порядок: T1→T2→T3→T4→T5→T6→T7→T8-T11→T12), затем pytest (4 файла) + make check до чистоты.
```

### Инфраструктура (Sysadmin, Wave 2) — после/параллельно
- S1: LANGFUSE-ключи; S2: mock-litellm контейнер на 14000 (решение пользователя, ОБЯЗАТЕЛЬНО); S3: проверка SSH-туннеля.

### Прогоны и сводная статистика (Wave 3) — после W1
- Матрица 3×3 (§7) + сводная таблица; baseline-запись; итоговый отчёт сессии.

$END_DEVPLAN
