$START_DOCUMENT_PLAN
### Document Plan
**SECTION_GOALS:**
- GOAL [Контракт и цели DevPlan] => G_CONTRACT
- GOAL [Код-граф (XML): сущности, типы, cross-links] => G_GRAPH
- GOAL [Дизайн: конфиг SoT, режимы, PromQL, baseline, remote] => G_DESIGN
- GOAL [Data Flow: сквозные потоки прогонов] => G_FLOW
- GOAL [Файловый манифест: новые и изменяемые файлы] => G_FILES
- GOAL [Волны реализации: W1-W5 с критериями] => G_WAVES
- GOAL [Acceptance Criteria и риски] => G_ACCEPT
**SECTION_USE_CASES:**
- USE_CASE [После деплоя: smoke-прогон web] => SC_SMOKE
- USE_CASE [Ежемесячный regression-прогон с baseline-сравнением] => SC_REGRESSION
- USE_CASE [Поиск максимальной нагрузки litellm (capacity)] => SC_CAPACITY
- USE_CASE [Прогон с ноды (LOAD_RUNNER=node) при слабом канале] => SC_REMOTE
$END_DOCUMENT_PLAN

$START_DEVPLAN
# 02-DevPlan — Load Testing Platform (146)

# region MODULE_CONTRACT
## @purpose  Реализация системы нагрузочных тестов платформы: Locust-сценарии, 3 режима,
##           PromQL-анализ насыщения, baseline-сравнение, make-таргет load-test.
## @scope    core/loadtest/ (сценарии+SoT), core/internal/loadtest/ (Python-подсистема),
##           makefiles/loadtest.mk, tests/unit, tests/e2e, docs/load-testing.md
## @invariants
##   1. Языковая политика: вся бизнес-логика — Python (core/internal/loadtest/);
##      makefiles/loadtest.mk — тонкий фасад (python3 -m ...).
##   2. SoT сценариев — core/loadtest/scenarios.yaml; locust-файлы НЕ содержат
##      захардкоженных RPS/порогов (читают из SoT через env при старте).
##   3. Генератор НЕ запускается внутри стека (вне ноды — локально; на ноде — отдельный
##      docker run, НЕ через docker compose сервисов).
##   4. capacity-режим на prod-ноде — только с LOAD_ALLOW_PROD=1 (guard).
##   5. Saturation-метрики — ТОЛЬКО post-run PromQL pull из существующего Prometheus;
##      новые экспортёры/pushgateway запрещены (нулевая новая инфраструктура).
##   6. baseline history.json — компактный, коммитится в репо (core/loadtest/history/);
##      полные report.json/CSV/markdown — в gitignored load-results/ (LOAD_RESULTS_DIR,
##      целиком в .gitignore, без negate-паттернов).
##   7. NODE резолвится через core/internal/shared/node_resolver.py (Python SoT).
##   8. Мок-модель litellm (echo) — обязательный компонент LLM-сценариев (детерминизм);
##      доставляется на тестовую ноду ОТДЕЛЬНЫМ mock-конфигом (не policy.yaml, инвариант
##      «providers: только DeepSeek»; не litellm-config.test.yml — тот только для
##      docker-compose.test.yml component-тестов).
##   9. Exit-коды — по контракту shared/contracts.py: 0 ok, 1 generic (FAIL-вердикт),
##      2 ConfigNotFound, 3 ConfigParse, 4 ConfigValidation, 10 Fatal (guard-блок
##      capacity на prod-ноде). Никаких «exit 2 = FAIL» — конфликт с контрактом.
##   10. Тайминги прогонов согласованы со scrape_interval Prometheus (global 30s,
##       cadvisor/node-exporter 60s): smoke ≥ 90s (≥3 сэмпла по 30s-метрикам, ≥2 по 60s);
##       rate-окна запросов ≤ run_time/2; метрика с <2 сэмплами — insufficient → WARN.
##   11. Точный RPS прогона задаётся locust --max-rps (users — только размер пула,
##       users = target_rps × 2; RPS = users/latency, поэтому users ≠ rps).
## @rationale Brief 146: D1 Locust (Python-first), D2 гибридный runner, D3 полный набор
##           сценариев, D4 post-run PromQL (существующая телеметрия), D5 3 режима,
##           D6 make-фасад. Прецедент e2e: test_chaos_resilience (NodeSSHClient).
## @changes 2026-08-11 | DevPlan 146 — Created
##          2026-08-11 | DevPlan 146 — Review: exit-коды по контракту shared/contracts.py
##          (инвариант 9); тайминги vs scrape_interval Prometheus 30/60s (инвариант 10,
##          smoke 30s→90s, rate-окна ≤ run_time/2, insufficient_metrics); users ≠ rps —
##          точный RPS через locust --max-rps (инвариант 11); mock-модель — отдельный
##          litellm-config.mock.yml для тестовой ноды (не test.yml, не policy.yaml);
##          history.json → core/loadtest/history/ + host-детекция пересоздания VPS;
##          LOAD_IMAGE/LOAD_CPUS для remote; s3 без boto3 (отсутствует в locust-образе)
# endregion MODULE_CONTRACT

$ARTIFACT_CONTRACT
PURPOSE:               Реализовать эксплуатационную подсистему нагрузочных тестов: генератор (Locust), сценарии по сервисам, 3 режима прогона, отчёт с saturation-метриками, baseline-сравнение по датам.
DESCRIPTION:           5 волн: W1 каркас+сценарии+smoke; W2 PromQL-отчёт; W3 baseline/regression; W4 capacity; W5 remote-режим+e2e+документация.
RATIONALE:             Python-first политика (Locust), нулевая новая мониторинговая инфраструктура (PromQL из существующего Prometheus), безопасность эксплуатации (safety-stop, prod-guard), воспроизводимость (SoT scenarios.yaml, mock-модель). Тайминги и exit-коды — по контрактам платформы (prometheus.yml.tmpl scrape 30/60s; shared/contracts.py 0/1/2/3/4/10).
ACCEPTANCE_CRITERIA:   1) make load-test (smoke ≥90s) на тестовой VPS — отчёт с saturation (≥2 сэмпла на метрику); 2) regression FAIL при 1.5× baseline p95 (exit 1); 3) capacity находит max RPS с автостопом (--max-rps, max_steps); 4) LOAD_RUNNER=node работает; 5) make check зелёный.
IMPLEMENTS:            Brief 146 (01-Brief.md §4-6)
IMPACTS:               Makefile, entrypoint-manifest.yaml, core/AGENTS.md, root AGENTS.md (глоссарий), pyproject.toml (load extra), .gitignore, core/modules/litellm/config/litellm-config.mock.yml (новый mock-конфиг), tests/
REQUIRES:              Locust ≥2.32 (pip install -e ".[load]"), тестовая VPS (NODE), Prometheus на ноде, доступ к эндпоинтам сценариев
$END_ARTIFACT_CONTRACT

---

## 1. Draft Code Graph (XML Knowledge Graph)

```xml
<knowledge_graph>
  <!-- Конфиг SoT -->
  <entity name="core_loadtest_scenarios_yaml" TYPE="YAML">
    <keywords>scenarios config rps thresholds endpoints header body users spawn-rate</keywords>
    <annotation>Единый SoT: имя сценария → endpoint, метод, headers, body-шаблон, target_rps, users, пороги (max_p95, max_p99, max_error), start_rps (capacity), ssl_verify</annotation>
    <crosslink from="core_loadtest_scenarios_yaml" to="core_internal_loadtest_config_py"/>
  </entity>

  <!-- Сценарии (Locust) -->
  <entity name="core_loadtest_scenarios_web_py" TYPE="PYTHON">
    <keywords>locust HttpUser nginx static sites status-page GET</keywords>
    <annotation>web.py: GET-пути платформы (nginx front, сайты из node.yaml, status-page); weight/пропорции</annotation>
    <crosslink from="core_loadtest_scenarios_web_py" to="core_loadtest_scenarios_yaml"/>
  </entity>
  <entity name="core_loadtest_scenarios_llm_py" TYPE="PYTHON">
    <keywords>locust litellm chat completions mock POST json</keywords>
    <annotation>llm.py: POST /chat/completions на mock-модель (echo), тело из SoT</annotation>
    <crosslink from="core_loadtest_scenarios_llm_py" to="core_loadtest_scenarios_yaml"/>
    <crosslink from="core_loadtest_scenarios_llm_py" to="core_modules_litellm_config_test_yml"/>
  </entity>
  <entity name="core_loadtest_scenarios_llm_stream_py" TYPE="PYTHON">
    <keywords>locust SSE streaming chat completions custom client</keywords>
    <annotation>llm_stream.py: кастомный SSE-клиент (stream=True, чтение чанков, таймаут)</annotation>
    <crosslink from="core_loadtest_scenarios_llm_stream_py" to="core_loadtest_scenarios_yaml"/>
  </entity>
  <entity name="core_loadtest_scenarios_langfuse_ingest_py" TYPE="PYTHON">
    <keywords>locust langfuse traces postgres clickhouse POST public api</keywords>
    <annotation>langfuse_ingest.py: POST /api/public/traces (публичный ключ из env) → нагрузка на langfuse+postgres+clickhouse</annotation>
    <crosslink from="core_loadtest_scenarios_langfuse_ingest_py" to="core_loadtest_scenarios_yaml"/>
  </entity>
  <entity name="core_loadtest_scenarios_db_py" TYPE="PYTHON">
    <keywords>locust postgres pgbouncer optional SQL read</keywords>
    <annotation>db.py (optional): read-запросы через pgbouncer (docker exec? нет — через API/порт) — детали W1: если нет HTTP-пути, сценарий помечается optional и пропускается</annotation>
    <crosslink from="core_loadtest_scenarios_db_py" to="core_loadtest_scenarios_yaml"/>
  </entity>
  <entity name="core_loadtest_scenarios_s3_py" TYPE="PYTHON">
    <keywords>locust minio s3 optional PUT GET</keywords>
    <annotation>s3.py (optional): PUT/GET объектов minio (boto3 внутри locust-задачи или HTTP)</annotation>
    <crosslink from="core_loadtest_scenarios_s3_py" to="core_loadtest_scenarios_yaml"/>
  </entity>

  <!-- Python-подсистема -->
  <entity name="core_internal_loadtest_config_py" TYPE="PYTHON">
    <keywords>yaml load env validate scenario node domain resolver</keywords>
    <annotation>config.py: загрузка scenarios.yaml (canonical: yaml.safe_load через shared), резолв NODE → host/domain (node_resolver), merge env-оверрайдов (LOAD_RPS, LOAD_DURATION), валидация порогов, fail-fast</annotation>
    <crosslink from="core_internal_loadtest_config_py" to="core_internal_shared_node_resolver_py"/>
  </entity>
  <entity name="core_internal_loadtest_runner_cli_py" TYPE="PYTHON">
    <keywords>locust headless orchestration modes exit-code csv env build command</keywords>
    <annotation>runner_cli.py (CLI, entrypoint): main --scenario --node --mode; строит locust-команду (headless, --run-time, --users, --spawn-rate, --max-rps, --csv), запускает (local subprocess | remote через runner_remote), ждёт, собирает отчёт, exit по контракту shared/contracts.py: 0 PASS/WARN, 1 FAIL/ошибка, 4 config-ошибка, 10 guard-блок</annotation>
    <crosslink from="core_internal_loadtest_runner_cli_py" to="core_internal_loadtest_config_py"/>
    <crosslink from="core_internal_loadtest_runner_cli_py" to="core_internal_loadtest_prometheus_pull_py"/>
    <crosslink from="core_internal_loadtest_runner_cli_py" to="core_internal_loadtest_report_py"/>
    <crosslink from="core_internal_loadtest_runner_cli_py" to="core_internal_loadtest_baseline_py"/>
    <crosslink from="core_internal_loadtest_runner_cli_py" to="core_internal_loadtest_runner_remote_py"/>
  </entity>
  <entity name="core_internal_loadtest_capacity_py" TYPE="PYTHON">
    <keywords>ramp steps rps doubling stabilization safety-stop error-rate p99</keywords>
    <annotation>capacity.py: итеративный профиль шагов (start_rps ×2, max_steps=8), стабилизация 60s, точный RPS шага через locust --max-rps (users = rps×2 пул), критерий останова (error>5% | p99>max_p99 | users overflow), сборка профиля последовательными headless-прогонами</annotation>
    <crosslink from="core_internal_loadtest_capacity_py" to="core_internal_loadtest_runner_cli_py"/>
  </entity>
  <entity name="core_internal_loadtest_prometheus_pull_py" TYPE="PYTHON">
    <keywords>promql prometheus range-query cadvisor postgres redis nginx clickhouse litellm node-exporter</keywords>
    <annotation>prometheus_pull.py: HTTP-клиент Prometheus ноды (/api/v1/query_range, окно [t0-60s, t1+60s], step=30s), discovery имён метрик (label/__name__/values, fail-ранний), расчёт агрегатов avg/max за окно (rate-окна ≤ run_time/2), секция saturation в отчёте; метрика с <2 сэмплами → insufficient → WARN</annotation>
    <crosslink from="core_internal_loadtest_prometheus_pull_py" to="core_internal_loadtest_report_py"/>
  </entity>
  <entity name="core_internal_loadtest_report_py" TYPE="PYTHON">
    <keywords>json markdown verdict junit stats locust-csv parse</keywords>
    <annotation>report.py: парс locust CSV (stats/history), сборка report.json {scenario, mode, timestamp, rps, p50/p95/p99, error_rate, max_rps, saturation{...}, verdict}, markdown-сводка, junit.xml (опция)</annotation>
    <crosslink from="core_internal_loadtest_report_py" to="core_internal_loadtest_prometheus_pull_py"/>
    <crosslink from="core_internal_loadtest_report_py" to="core_internal_loadtest_baseline_py"/>
  </entity>
  <entity name="core_internal_loadtest_baseline_py" TYPE="PYTHON">
    <keywords>history json baseline compare regression delta p95 error thresholds</keywords>
    <annotation>baseline.py: чтение/запись core/loadtest/history/&lt;node&gt;/&lt;scenario&gt;/history.json (компактные строки прогонов, поле host для детекции пересоздания ноды), сравнение с previous (p95>1.5× или error>+2pp → FAIL), delta-блок в отчёте, репо-коммит подразумевается оператором</annotation>
    <crosslink from="core_internal_loadtest_baseline_py" to="core_internal_loadtest_report_py"/>
  </entity>
  <entity name="core_internal_loadtest_runner_remote_py" TYPE="PYTHON">
    <keywords>ssh scp docker-run locust container node remote</keywords>
    <annotation>runner_remote.py (LOAD_RUNNER=node): rsync сценария+SoT на ноду (канон shared.ssh_opts + subprocess ssh, НЕ tests/_conftest), docker run locustio/locust:2.32 (образ через LOAD_IMAGE — Docker Hub rate-limit, канон registry; --network host, --cpus LOAD_CPUS), rsync обратно CSV; s3-сценарий — через HTTP API minio (boto3 отсутствует в locust-образе)</annotation>
    <crosslink from="core_internal_loadtest_runner_remote_py" to="core_internal_shared_ssh_opts_py"/>
  </entity>

  <!-- Makefile и реестр -->
  <entity name="makefiles_loadtest_mk" TYPE="MAKEFILE">
    <keywords>load-test load-test-smoke regression capacity make target facade</keywords>
    <annotation>loadtest.mk: таргет load-test (SCENARIO/NODE/MODE vars, python3 -m core.internal.loadtest.runner_cli), thin facade</annotation>
    <crosslink from="makefiles_loadtest_mk" to="core_internal_loadtest_runner_cli_py"/>
  </entity>
  <entity name="core_entrypoint_manifest_yaml" TYPE="YAML">
    <keywords>registry load-test canonical target glossary</keywords>
    <annotation>+load-test запись (delegates_to: makefiles/loadtest.mk → runner_cli.py)</annotation>
    <crosslink from="core_entrypoint_manifest_yaml" to="makefiles_loadtest_mk"/>
  </entity>

  <!-- Тесты -->
  <entity name="tests_unit_test_loadtest_config_py" TYPE="PYTHON">
    <keywords>pytest config scenarios.yaml validation fail-fast</keywords>
    <annotation>Юнит: парсинг SoT, дефолты, валидация порогов, NODE-резолв (tmp_path фикстуры)</annotation>
    <crosslink from="tests_unit_test_loadtest_config_py" to="core_internal_loadtest_config_py"/>
  </entity>
  <entity name="tests_unit_test_loadtest_baseline_py" TYPE="PYTHON">
    <keywords>pytest history compare regression delta thresholds</keywords>
    <annotation>Юнит: запись/чтение history.json, пороги регрессии (1.5×, +2pp), negative: baseline отсутствует</annotation>
    <crosslink from="tests_unit_test_loadtest_baseline_py" to="core_internal_loadtest_baseline_py"/>
  </entity>
  <entity name="tests_unit_test_loadtest_report_py" TYPE="PYTHON">
    <keywords>pytest locust-csv parse verdict json markdown</keywords>
    <annotation>Юнит: парс CSV-фикстур, сборка report.json, вердикт PASS/WARN/FAIL</annotation>
    <crosslink from="tests_unit_test_loadtest_report_py" to="core_internal_loadtest_report_py"/>
  </entity>
  <entity name="tests_unit_test_loadtest_prometheus_pull_py" TYPE="PYTHON">
    <keywords>pytest promql query builder mock responses parse</keywords>
    <annotation>Юнит: построение запросов, парс ответов (fixture JSON), discovery имён, отсутствие метрик → ранний FAIL с диагностикой</annotation>
    <crosslink from="tests_unit_test_loadtest_prometheus_pull_py" to="core_internal_loadtest_prometheus_pull_py"/>
  </entity>
  <entity name="tests_unit_test_loadtest_capacity_py" TYPE="PYTHON">
    <keywords>pytest ramp steps safety-stop deterministic simulation</keywords>
    <annotation>Юнит: логика ступеней (детерминированная симуляция: fake runner), критерии останова, max_rps</annotation>
    <crosslink from="tests_unit_test_loadtest_capacity_py" to="core_internal_loadtest_capacity_py"/>
  </entity>
  <entity name="tests_e2e_test_load_test_py" TYPE="PYTHON">
    <keywords>pytest requires_node e2e smoke load-test node ssh</keywords>
    <annotation>E2E: smoke-прогон web-сценария на тестовой VPS (requires_node), проверка отчёта + exit 0; НЕ в make test/gate (фильтр not requires_node)</annotation>
    <crosslink from="tests_e2e_test_load_test_py" to="tests__conftest_node_NodeSSHClient"/>
  </entity>
  <entity name="docs_load_testing_md" TYPE="MARKDOWN">
    <keywords>documentation load-test usage modes metrics interpretation</keywords>
    <annotation>Документация DevOps: запуск, режимы, метрики, интерпретация отчёта, baseline, remote</annotation>
  </entity>
</knowledge_graph>
```

## 2. Step-by-step Data Flow

```
ПРОГОН smoke (локальный) — SCENARIO=web, NODE=test-vps, MODE=smoke
 1. make load-test SCENARIO=web NODE=test-vps MODE=smoke
    → makefiles/loadtest.mk → python3 -m core.internal.loadtest.runner_cli
 2. config.py: load scenarios.yaml → web (endpoint https://<domain>/, target_rps=10,
    users=20, max_p95=1s); node_resolver.resolve("test-vps") → host+platform_domain
 3. runner_cli: build locust cmd:
    locust -f core/loadtest/scenarios/web.py --headless -u 20 -r 20
    --max-rps 10 --run-time 90s --csv load-results/test-vps/web/smoke/run
    (LOCUST_* env: URL)   # smoke ≥90s: ≥3 сэмпла по 30s-метрикам, ≥2 по 60s
    subprocess.run (локально), timeout guard = run_time × 2 + 60s
 4. post-run: prometheus_pull.query_range(host:9090, window=[t0-60s, t1+60s], step=30s):
    per-container CPU/mem (cadvisor), nginx_http_requests_total rate (окно ≤ run_time/2),
    pg_stat_database_numbackends, redis_commands_processed_total rate,
    clickhouse, litellm_proxy_*, node_load1/5/15 → saturation dict;
    метрика с <2 сэмплами → "insufficient_metrics" → WARN (не FAIL)
 5. report.py: parse locust CSV stats → rps/p50/p95/p99/error_rate;
    build report.json + markdown; verdict = PASS (0 errors, p95<max_p95)
 6. baseline.py: append core/loadtest/history/test-vps/web/history.json row
    (+ host-поле); delta vs previous (нет previous → PASS, пометка "first run")
 7. exit 0; stdout: markdown-сводка + путь report.json

ПРОГОН regression (сравнение)
 5'. report: verdict = PASS если p95 <= 1.5×prev_p95 AND error <= prev+2pp
     else FAIL (exit 1); delta-таблица в markdown

ПРОГОН capacity (MODE=capacity, SCENARIO=llm)
 2'. config: start_rps=2, шаги ×2, max_steps=8, стабилизация 60s,
     max_p99=3s, max_error=5%
 3'. capacity.py: цикл: for step in [2,4,8,...,N]:
       runner_cli._run_locust(rps=step, run_time=60s)   # --max-rps step, users=step×2
       stats = parse csv
       if stats.error_rate > 5% or stats.p99 > 3s:
           break  # saturation достигнута
     max_rps = последний успешный шаг
     профиль шагов → в отчёт; общий timeout = max_steps × (run_time + 30s) + 120s

ПРОГОН remote (LOAD_RUNNER=node)
 2''. runner_remote: rsync core/loadtest/ → /tmp/loadtest-<ts>/ (ssh_opts),
      docker run --rm --network host --cpus ${LOAD_CPUS:-2} -v ... \
      ${LOAD_IMAGE:-locustio/locust:2.32} -f ... --headless
      (те же env, включая --max-rps), rsync обратно CSV
```

## 3. Дизайн

### 3.1 core/loadtest/scenarios.yaml (SoT)

```yaml
# GREP_SUMMARY: loadtest scenarios SoT rps thresholds endpoints ports
# STRUCTURE: ┌defaults┐ → ◇ per-scenario (endpoint, method, headers, body, target_rps, users, thresholds, optional)
defaults:
  ssl_verify: false          # тестовые ноды: самоподписанные серты (CA-путь — extension)
  run_time: 300              # s, для regression
  max_error: 0.05            # 5% — safety-stop capacity / FAIL-критерий
  max_p99: 3.0               # s — safety-stop capacity
  max_p95: 1.0               # s — FAIL-критерий smoke/regression
  baseline_delta_p95: 1.5    # множитель регрессии
  baseline_delta_error_pp: 2.0  # процентных пункта
  # users — РАЗМЕР ПУЛА (users = target_rps × 2), НЕ контроль RPS.
  # Точный RPS задаёт locust --max-rps (RPS = users/latency, users ≠ rps).
scenarios:
  web:
    description: "nginx front: статика, сайты, status-page"
    endpoint: "https://{domain}/"          # {domain} = platform domain (node.yaml)
    paths: ["/", "/status", ...]           # уточняются W1 по тестовой ноде
    users: 20
    target_rps: 10
  llm:
    description: "litellm /chat/completions non-stream (mock-модель)"
    endpoint: "http://{host}:4000"         # litellm без nginx-vhost (см. 3.2)
    path: "/chat/completions"
    model: "mock-echo"                     # модель из mock-конфига (3.2)
    body_template: {model: "{model}", messages: [{role: "user", content: "ping"}]}
    users: 40
    target_rps: 20
    capacity_start_rps: 2
  llm_stream: {…}   # SSE: stream=true, кастомный клиент, chunk timeout 10s
  langfuse_ingest:
    endpoint: "https://n.{domain}"
    path: "/api/public/traces"
    headers: {Authorization: "Bearer {LANGFUSE_PUBLIC_KEY}"}   # из секретов ноды
    body_template: {…trace fixture…}
  db: {optional: true, …}    # pg read через pgbouncer (порт 5432? уточнить) —
                             # если HTTP-пути нет → optional, docs описывают ограничение
  s3: {optional: true, …}    # minio PUT/GET через HTTP API (presigned URL) — НЕ boto3:
                             # boto3 отсутствует в locustio/locust-образе (remote-режим)
```

Инвариант 2: locust-файлы читают endpoint/target_rps/users/пороги ТОЛЬКО из env (LOCUST_*), которые заполняет config.py из SoT. Никаких хардкодов в .py сценариях; точный RPS — флаг `--max-rps` (инвариант 11).

### 3.2 Mock-модель litellm (W1)

**Канал доставки:** mock-модель НЕ добавляется в `litellm-config.test.yml` (он используется
только `docker-compose.test.yml` для component-тестов) и НЕ вносится в `policy.yaml`
(инвариант «providers: только DeepSeek», генератор `config_renderer.py` не фильтрует
модели по окружению). Создаётся отдельный конфиг `core/modules/litellm/config/litellm-config.mock.yml`
(полный конфиг: `general_settings` из канона + одна модель):

```yaml
model_list:
  - model_name: mock-echo
    litellm_params:
      model: "openai/echo"       # litellm echo (детерминированный ответ, фикс latency ~50ms)
```

**Установка на тестовую ноду** (документируется в docs/load-testing.md, W1):
scp конфига на ноду → замена/монтирование litellm-config → `make restart MODULES=litellm`.
Прод-конфиг (policy.yaml → litellm-config.yml) НЕ трогается; mock-модель на проде
отсутствует, поэтому сценарий llm на прод-ноде (LOAD_ALLOW_PROD=1) делает ранний FAIL
с понятным сообщением («mock-модель не обнаружена — установите litellm-config.mock.yml»).

**W1-верификация:** `openai/echo` поддерживается litellm-версией на ноде (встроенная
фейковая модель, без API key). Если версия ноды отклоняет `openai/echo` — fallback:
`model: "echo"` или `hosted_vllm/...`; фиксируется по фактическому ответу `/chat/completions`
первым smoke-прогоном (см. риск R1).

### 3.3 Режимы (детально)

| Режим | Длительность | RPS/Users | Критерий вердикта | Эксплуатационное применение |
|-------|-------------|-----------|--------------------|------------------------------|
| smoke | 90s (мин — см. инвариант 10) | target_rps из SoT через `--max-rps`, users = rps×2 | 0 errors AND p95 < max_p95 → PASS | после деплоя/обновления |
| regression | 300s | target_rps из SoT через `--max-rps`, users = rps×2 | p95 <= 1.5×prev_p95 AND error <= prev+2pp AND p95 < max_p95 | ежемесячно, сравнение по датам |
| capacity | steps 60s, max_steps=8 | start_rps, ×2 до насыщения; шаг — `--max-rps step`, users = step×2 | автостоп (error>5% \| p99>3s); max_rps = последний успешный шаг | поиск максимальной нагрузки; только тестовая нода без LOAD_ALLOW_PROD=1 |

**Почему users = rps×2, а не users = rps:** в Locust RPS = users / avg_response_time —
пул users=rps при latency 100ms даёт ~10× целевой RPS. `--max-rps` (встроенный rate-limit
Locust 2.x) задаёт точный целевой RPS; users — только размер пула, достаточный чтобы
RPS не был ограничен пулом (users = rps × 2 как запас при latency ≤ 2s; для сценариев с
latency > 2s — увеличивается вручную в SoT).

Формат локаст-запуска для capacity: **последовательные headless-прогоны по шагу**
(детерминированнее, чем locust --steps — нет встроенной семантики стабилизации+проверки
между шагами). Каждый шаг: отдельный прогон run_time=60s с `--max-rps <step>`
(users = step×2, spawn-rate = step).

### 3.4 PromQL-секция (W2)

Пул запросов (имена уточняются discovery на первом прогоне, но запросы строятся по паттерну):

```python
# rate-окна ≤ run_time/2 (иначе [5m] при 300s-прогоне захватывает пре-ран);
# scrape_interval: 30s global, 60s cadvisor/node-exporter (prometheus.yml.tmpl)
RATE_WINDOW = "1m"           # для smoke 90s / capacity 60s
RATE_WINDOW_LONG = "2m"      # для regression 300s
QUERY_STEP = "30s"           # = min scrape_interval
QUERY_PAD = 60               # window [t0-60s, t1+60s] — страховка на scrape-лаг
QUERIES = {
  "cpu_nginx":   f'rate(container_cpu_usage_seconds_total{{name="nginx"}}[{RATE_WINDOW}])',
  "mem_nginx":   'container_memory_working_set_bytes{name="nginx"}',
  # ... то же для litellm, langfuse, postgres, pgbouncer, redis, clickhouse, minio
  "nginx_rps":   f'rate(nginx_http_requests_total[{RATE_WINDOW}])',
  "nginx_conns": 'nginx_connections_active',
  "pg_backends": 'pg_stat_database_numbackends',
  "redis_ops":   f'rate(redis_commands_processed_total[{RATE_WINDOW}])',
  "redis_clients": 'redis_connected_clients',
  "litellm_reqs": f'rate(litellm_proxy_total_requests[{RATE_WINDOW}])',
  "litellm_err":  f'rate(litellm_proxy_failed_requests[{RATE_WINDOW}])',
  "load1": 'node_load1', "mem_avail": 'node_memory_MemAvailable_bytes',
  "net_rx": f'rate(node_network_receive_bytes_total[{RATE_WINDOW}])',
}
```

Отчёт: avg/max за окно прогона; `"saturation": {"cpu_nginx_pct": 42.3, "pg_backends_max": 17, ...}`.
Если метрика не найдена (discovery) → секция `"missing_metrics": [...]` + WARN (не FAIL —
экспортёр может быть выключен). Если метрика найдена, но сэмплов < 2 за окно
(60s-джобы при 90s-прогоне) → `"insufficient_metrics": [...]` + WARN (статистически
недостоверно; smoke ≥ 90s минимизирует, но не исключает для 60s-джобов).

### 3.5 Baseline (W3)

`core/loadtest/history/<node>/<scenario>/history.json` (коммитится в репо — вне
`load-results/`, чтобы .gitignore был простым `load-results/` без negate-паттернов):
```json
{"runs": [
  {"ts": "2026-08-11T18:00:00Z", "host": "test-vps.example.com", "mode": "regression",
   "rps": 20, "p50": 0.12, "p95": 0.31, "p99": 0.55, "error_rate": 0.0, "max_rps": null,
   "verdict": "PASS", "delta_vs_prev": null, "version": "git-sha"}
]}
```

**Поле `host`** (hostname ноды) — детекция пересоздания тестовой VPS (инвариант 9
платформы: тестовый сервер пересоздаётся). При смене `host` относительно предыдущего
прогона baseline считается невалидным: вердикт не FAIL, а PASS с пометкой
`"baseline_reset": "node recreated"` (сравнение с другим железом — мусор).

Полные report.json + CSV + markdown: `load-results/<node>/<scenario>/<mode>/<ts>/` —
директория целиком в .gitignore (LOAD_RESULTS_DIR переопределяем).

### 3.6 Remote-режим (W5)

`LOAD_RUNNER=node`:
1. `runner_remote.ship()` — rsync `core/loadtest/` → `/tmp/loadtest-<ts>/` на ноде (ssh через shared.ssh_opts, канон; НЕ tests/_conftest — runtime не импортирует тестовую инфраструктуру).
2. `docker run --rm --network host --cpus ${LOAD_CPUS:-2} -v /tmp/loadtest-<ts>:/lt -w /lt ${LOAD_IMAGE:-locustio/locust:2.32} -f ... --headless ...` (те же env, включая `--max-rps`; CPU-limit 2 ядра — не съедает хост под capacity, документируется как генераторная нагрузка).
3. `docker run` отдельно от стека: НЕ compose-сервис, НЕ в observability-net (инвариант 3).
4. rsync обратно: CSV + отчёт собирается локально (PromQL-пул — с локальной машины к Prometheus ноды).

**Образ:** `LOAD_IMAGE` параметризуется — default `locustio/locust:2.32` (Docker Hub);
при rate-limit/недоступности Docker Hub на ноде (известная проблема платформы) —
переопределяется на ghcr.io-зеркало/локально закешированный образ. **boto3 в образе
отсутствует** — s3-сценарий реализуется через HTTP API minio (presigned URL),
не через boto3 (boto3 доступен только в локальном режиме, где он runtime-зависимость).

### 3.7 Guard-ы

- **Exit-коды по контракту** `shared/contracts.py` (инвариант 9; НЕ «exit 2 = FAIL»):

| Код | Семантика | Ситуация |
|-----|-----------|----------|
| 0 | ok | PASS и WARN (WARN не блокирует) |
| 1 | generic error | вердикт FAIL (regression/capacity), ошибка прогона, недоступный Prometheus, отсутствие locust |
| 4 | ConfigValidation | неизвестный сценарий, пустые пороги, rps<=0 (config.validate fail-fast) |
| 10 | Fatal — ручное вмешательство | capacity на нетестовой ноде без LOAD_ALLOW_PROD=1 (guard) |

- `LOAD_ALLOW_PROD=1` — разрешает MODE=capacity на нодах, где node.yaml#role != test. По умолчанию capacity на нетестовой ноде → ранний exit 10 с сообщением.
- timeout guard на каждый прогон: `run_time × 2 + 60s`; для capacity — суммарный `max_steps × (run_time + 30s) + 120s`.
- `config.validate()` — fail-fast (exit 4): неизвестный сценарий, пустой endpoint, target_rps<=0, пороги нечисловые.
- preflight locust: при запуске проверка `import locust`; отсутствие → exit 1 с инструкцией `pip install -e ".[load]"`.

## 4. File Manifest

### Новые файлы (25)
| # | Файл | Назначение | Волна |
|---|------|-----------|-------|
| 1 | `core/loadtest/scenarios.yaml` | SoT сценариев (endpoint, target_rps, пороги) | W1 |
| 2 | `core/loadtest/scenarios/__init__.py` | пакет | W1 |
| 3 | `core/loadtest/scenarios/web.py` | locust web-сценарий | W1 |
| 4 | `core/loadtest/scenarios/llm.py` | locust llm (mock-модель) | W1 |
| 5 | `core/loadtest/scenarios/llm_stream.py` | locust SSE | W1 |
| 6 | `core/loadtest/scenarios/langfuse_ingest.py` | locust traces | W1 |
| 7 | `core/loadtest/scenarios/db.py` | locust db read (optional) | W1 |
| 8 | `core/loadtest/scenarios/s3.py` | locust minio (optional, HTTP API без boto3) | W1 |
| 9 | `core/internal/loadtest/__init__.py` | пакет | W1 |
| 10 | `core/internal/loadtest/config.py` | SoT+env, NODE-резолв, валидация (exit 4) | W1 |
| 11 | `core/internal/loadtest/runner_cli.py` | CLI-оркестратор (CLI entrypoint, exit по контракту) | W1 |
| 12 | `core/internal/loadtest/prometheus_pull.py` | PromQL range-запросы (окна/шаг/сэмплы) | W2 |
| 13 | `core/internal/loadtest/report.py` | report.json/markdown/verdict | W2 |
| 14 | `core/internal/loadtest/baseline.py` | history.json (core/loadtest/history/) + host-детекция | W3 |
| 15 | `core/internal/loadtest/capacity.py` | ступенчатый ramp (--max-rps, max_steps) + safety-stop | W4 |
| 16 | `core/internal/loadtest/runner_remote.py` | LOAD_RUNNER=node (LOAD_IMAGE/LOAD_CPUS) | W5 |
| 17 | `makefiles/loadtest.mk` | make-таргет load-test (тонкий фасад) | W1 |
| 18 | `core/modules/litellm/config/litellm-config.mock.yml` | mock-конфиг для тестовой ноды (openai/echo) | W1 |
| 19 | `tests/unit/test_loadtest_config.py` | юнит config | W1 |
| 20 | `tests/unit/test_loadtest_prometheus_pull.py` | юнит promql | W2 |
| 21 | `tests/unit/test_loadtest_report.py` | юнит отчёт/verdict | W2 |
| 22 | `tests/unit/test_loadtest_baseline.py` | юнит baseline (host-reset, пороги) | W3 |
| 23 | `tests/unit/test_loadtest_capacity.py` | юнит capacity | W4 |
| 24 | `tests/e2e/test_load_test.py` | e2e smoke (requires_node) | W5 |
| 25 | `docs/load-testing.md` | DevOps-документация (вкл. установку mock-конфига) | W5 |

### Изменяемые файлы (6)
| # | Файл | Изменение | Волна |
|---|------|-----------|-------|
| 26 | `Makefile` | `include makefiles/loadtest.mk` | W1 |
| 27 | `core/entrypoint-manifest.yaml` | +load-test (delegates_to → runner_cli) | W1 |
| 28 | `core/AGENTS.md` | +операция load-test (генерируемые секции — make generate-agents-md) | W1 |
| 29 | `AGENTS.md` (root) | глоссарий +load-test (генерируется) | W1 |
| 30 | `pyproject.toml` | `[project.optional-dependencies] load = ["locust>=2.32,<3"]` (requirements.txt НЕ регенерируется — генерится из [project].dependencies) | W1 |
| 31 | `.gitignore` | `load-results/` (целиком, без negate-паттернов — history.json живёт в core/loadtest/history/) | W3 |

## 5. Волны реализации

### W1 — Каркас + сценарии + smoke (14 новых + 5 изменяемых)
- scenarios.yaml (web/llm/llm_stream/langfuse_ingest + db/s3 optional) — endpoint-пути уточняются по тестовой ноде (первый прогон).
- locust-сценарии (web, llm, llm_stream SSE, langfuse_ingest, db, s3) — читают env из config.py; SSE — кастомный клиент с chunk-timeout; точный RPS — `--max-rps` (users = пул).
- config.py + runner_cli.py (режим smoke; локальный subprocess locust; CSV-парс минимальный; exit по контракту: 0 PASS/WARN, 1 FAIL, 4 config, 10 guard).
- makefiles/loadtest.mk + Makefile include; pyproject load-extra; entrypoint-manifest + глоссарий (generate-agents-md).
- litellm mock-модель: `litellm-config.mock.yml` + установка на тестовую ноду (docs W1); верификация `openai/echo` на версии litellm ноды.
- tests/unit/test_loadtest_config.py (парсинг, валидация, NODE-резолв).
- **Критерий W1**: `python3 -m core.internal.loadtest.runner_cli --scenario web --node <test> --mode smoke` собирает CSV и выводит rps/p95/error без ошибок (прогон ≥ 90s, RPS ≈ target_rps под --max-rps); `make check` зелёный.

### W2 — PromQL-отчёт (4 файла)
- prometheus_pull.py (query_range c окном [t0-60s, t1+60s], step=30s, rate-окна ≤ run_time/2, discovery имён, агрегаты avg/max, missing_metrics + insufficient_metrics).
- report.py (парс CSV → report.json, markdown-сводка, вердикт PASS/WARN/FAIL по smoke-критерию).
- tests: test_loadtest_prometheus_pull.py, test_loadtest_report.py.
- **Критерий W2**: на тестовой ноде smoke-прогон (≥90s) выдаёт report.json с saturation-секцией (CPU/mem контейнеров, pg backends, redis ops, nginx conns, node load) — по 30s-метрикам ≥3 сэмпла, 60s-джобы не в insufficient_metrics; markdown-сводка.

### W3 — Baseline + regression (3 файла)
- baseline.py (history.json в core/loadtest/history/<node>/<scenario>/: запись строки с host, чтение, сравнение с previous, delta; регрессионные пороги из SoT: 1.5× p95, +2pp error; host-reset при пересоздании ноды → PASS c baseline_reset).
- runner_cli: режим regression; junit.xml (опция `--junit`); .gitignore: `load-results/` целиком.
- tests: test_loadtest_baseline.py (включая negative: первый прогон, отсутствие previous, превышение порога → FAIL exit 1, смена host → baseline_reset).
- **Критерий W3**: два прогона regression — второй PASS с delta=0 при неизменной системе; при искусственном baseline (поднятый prev_p95) — FAIL, exit 1.

### W4 — Capacity (2 файла)
- capacity.py (ступенчатый профиль start_rps×2, max_steps=8, стабилизация 60s/шаг, шаг через `--max-rps` + users=step×2, safety-stop error>5% или p99>3s, max_rps; суммарный timeout max_steps × (run_time + 30s) + 120s).
- runner_cli: режим capacity + prod-guard (LOAD_ALLOW_PROD=1 для нетестовых нод, exit 10).
- tests: test_loadtest_capacity.py (детерминированная симуляция fake-runner: 3 сценария — насыщение по error, по p99, без насыщения в лимите шагов).
- **Критерий W4**: на тестовой ноде capacity по web находит max_rps и останавливается автостопом; отчёт содержит профиль шагов (rps шага = фактический, ограниченный --max-rps).

### W5 — Remote + e2e + docs (3 файла)
- runner_remote.py (rsync туда/обратно, docker run ${LOAD_IMAGE:-locustio/locust:2.32} на ноде, --network host, --cpus ${LOAD_CPUS:-2}; boto3 не используется — s3 через HTTP API).
- tests/e2e/test_load_test.py (requires_node: smoke web против тестовой VPS; проверка report.json; НЕ в make test/gate).
- docs/load-testing.md (запуск, режимы, метрики, интерпретация, baseline, remote, guard-ы, установка mock-конфига).
- **Критерий W5**: `LOAD_RUNNER=node make load-test SCENARIO=web NODE=<test> MODE=smoke` — отчёт собран из нодного прогона; e2e-тест проходит на тестовой VPS; документация покрывает все use-cases.

## 6. Acceptance Criteria (итоговые)

| # | Критерий | Проверка |
|---|----------|----------|
| AC1 | smoke-прогон на тестовой VPS | `make load-test SCENARIO=web NODE=<test> MODE=smoke` → exit 0, report.json с saturation, markdown-сводка |
| AC2 | regression-сравнение | повторный прогон PASS (delta≈0); искусственный baseline → FAIL, exit 1 |
| AC3 | capacity находит max RPS | профиль шагов в отчёте, автостоп сработал, max_rps зафиксирован |
| AC4 | remote-режим | LOAD_RUNNER=node — отчёт из нодного прогона |
| AC5 | gate-чистота | `make check` зелёный; юнит-тесты (5 файлов) проходят; e2e — на тестовой VPS |
| AC6 | LLM-детерминизм | llm/llm-stream используют mock-echo; без mock на prod → ранний FAIL с сообщением |
| AC7 | prod-guard | capacity без LOAD_ALLOW_PROD=1 на prod-ноде → exit 10, без нагрузки |

## 7. Риски и ограничения

| Риск | Severity | Митигация | Волна |
|------|----------|-----------|-------|
| Endpoint-пути (litellm без nginx-vhost, langfuse auth) не совпадают с предположениями | MED | W1: первый smoke-прогон на тестовой ноде уточняет paths в scenarios.yaml; db/s3 — optional | W1 |
| Имена метрик Prometheus отличаются от ожиданий (pgbouncer-метрик может не быть) | MED | W2: discovery через label/__name__/values; missing_metrics → WARN, не FAIL; insufficient (сэмплов <2 при scrape 30/60s) → WARN | W2 |
| `openai/echo` не принят версией litellm на ноде (mock-модель) | MED | W1: верификация первым smoke-прогоном; fallback `model: "echo"`; mock-конфиг изолирован от policy.yaml | W1 |
| Locust SSE-клиент не покрывает chunk-timeout | MED | Кастомный клиент с явным read timeout; юнит-тест на структуру | W1 |
| Capacity на prod-ноде | HIGH | LOAD_ALLOW_PROD guard (exit 10) + документирование; default target — тестовая нода | W4 |
| Дрейф baseline (ручные правки history.json) | LOW | Компактный формат + вердикт delta виден в diff; регенерация — только через прогон | W3 |
| Пересоздание тестовой VPS (инвариант 9) инвалидирует baseline | MED | Поле `host` в history.json: смена host → baseline_reset (PASS + пометка), не мусорный FAIL | W3 |
| Docker Hub rate-limit/недоступность образа locust на ноде (remote) | MED | `LOAD_IMAGE` параметризация (ghcr.io-зеркало/кэш); известная проблема платформы (StatusReport 045) | W5 |
| boto3 отсутствует в locustio/locust-образе | LOW | s3-сценарий через HTTP API minio (presigned URL); boto3 — только локальный режим (runtime-зависимость) | W1 |
| Locust в dev-окружении (macOS) — py3.10+ ок | LOW | load-extra; не runtime-зависимость ядра платформы; preflight `import locust` с инструкцией | W1 |

## 8. Порядок верификации (Code-агент)

1. Per-wave: `make test-summary TEST_FILE=tests/unit/test_loadtest_*.py` → фикс-цикл `make check` до чистоты (W1-W5). Юнит-покрытие включает: маппинг вердикт→exit-код по контракту (0/1/4/10), insufficient-семантику prometheus_pull, host-reset baseline, max_steps/--max-rps capacity.
2. Финал: `make check` до чистоты; e2e smoke — отдельно на тестовой VPS: `make test-node NODE=<test>` (существующий канал) + ручной прогон load-test по AC1-AC4.
3. Коммиты (≤2 per wave): `feat(146): <wave> load-testing — <slug>`.

$END_DEVPLAN
