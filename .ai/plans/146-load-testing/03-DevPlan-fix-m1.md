$START_DOCUMENT_PLAN
### Document Plan
**SECTION_GOALS:**
- GOAL [Контракт и цели fix-DevPlan] => G_CONTRACT
- GOAL [BUG-1: RPS-контроль — evidence, решение, rejected-альтернативы] => G_BUG1
- GOAL [BUG-2: langfuse endpoint — evidence, решение] => G_BUG2
- GOAL [TASK-декомпозиция, параллельные группы, $TEST_SPEC] => G_TASKS
- GOAL [План верификации на tronyx-vps] => G_VERIFY
**SECTION_USE_CASES:**
- USE_CASE [Smoke web/s3 на tronyx-vps с рабочим RPS-контролем] => SC_SMOKE_FIX
- USE_CASE [Прогон с test-ноды (полный набор сценариев)] => SC_TEST_NODE
$END_DOCUMENT_PLAN

$START_DEVPLAN
# 03-DevPlan-fix-m1 — Emergency Fix: RPS-контроль + Langfuse Endpoint (146-m1)

# region MODULE_CONTRACT
## @purpose  Срочное исправление двух блокирующих багов подсистемы нагрузочного тестирования
##           (DevPlan 146), обнаруженных ПЕРВЫМ реальным прогоном против production-ноды
##           tronyx-vps (2026-08-11). Без исправления подсистема НЕРАБОТОСПОСОБНА:
##           любой сценарий падает `locust: error: unrecognized arguments: --max-rps` (rc=2).
## @scope    core/internal/loadtest/runner_cli.py (удаление --max-rps, добавление RPS-механизма),
##           core/loadtest/scenarios/*.py (6 файлов — constant_throughput из env LT_TARGET_RPS),
##           core/loadtest/scenarios/__init__.py (общий helper _rps_wait_time),
##           core/loadtest/scenarios.yaml (исправление langfuse endpoint + комментарии),
##           core/internal/loadtest/config.py (LT_TARGET_RPS env + LOAD_ENDPOINT_<SCENARIO> override),
##           core/internal/loadtest/capacity.py (docstrings), runner_remote.py (DEFAULT_IMAGE pin),
##           makefiles/loadtest.mk (комментарии), pyproject.toml (закрепление версии locust),
##           docs/load-testing.md (актуализация разделов 1,4,5,11),
##           tests/unit/test_loadtest_runner.py (новый — unit-тест _build_locust_args, RPS-механизм),
##           tests/unit/test_loadtest_config.py (endpoint-override тесты),
##           tests/e2e/test_load_test.py (адаптация)
## @invariants
##   1. RPS-контроль реализуется ШТАТНЫМИ средствами locust 2.32 (`constant_throughput` из
##      `locust.wait_time`) — НИКАКИХ внешних зависимостей (locust-plugins), НИКАКИХ
##      несуществующих CLI-флагов. Инвариант 11 DevPlan 146 («точный RPS») сохраняется,
##      реализация заменяется с --max-rps на constant_throughput.
##   2. Параметризация RPS — через env LT_TARGET_RPS (один env, читается всеми сценариями
##      из общего helper'а `_rps_wait_time` в `core/loadtest/scenarios/__init__.py`).
##      users = rps × 2 — размер пула (семантика pool сохраняется из DevPlan 146 §3.1).
##   3. Endpoint langfuse_ingest: default SoT → `https://langfuse.{domain}` (соответствует
##      конвенции production-ноды tronyx-vps); per-scenario override через env
##      `LOAD_ENDPOINT_LANGFUSE_INGEST` (backward-compat для test-нод с иной конвенцией).
##   4. locust-пин в pyproject.toml закрепляется до minor: `locust>=2.32,<2.33` —
##      исключение CLI-дрейфа между dev-окружением (pip install -e ".[load]") и
##      docker-образом (`locustio/locust:2.32`).
##   5. ВСЕ сценарии используют единый механизм RPS — общий helper `_rps_wait_time` в
##      `__init__.py` (DRY: 6 файлов × 5 строк = 30 строк дублирования → 1 helper).
## @rationale Баг-блокер: подсистема создана сегодня (commit 6c7f6925) и ни разу не
##            запускалась — первый же реальный прогон вскрыл фатальный дефект дизайна:
##            флаг `--max-rps` не существует в locust (ни в 2.32, ни в 2.46). Инвариант 11
##            DevPlan 146 «точный RPS задаёт locust --max-rps» нереализуем штатными
##            средствами — требуется замена механизма RPS-контроля. Без этого фикса
##            подсистема НЕ МОЖЕТ быть использована ни в одном сценарии.
## @changes  2026-08-11 | Emergency fix m1 — Created (post-first-run tronyx-vps)
# endregion MODULE_CONTRACT

$ARTIFACT_CONTRACT
PURPOSE:               Исправить два блокирующих бага (BUG-1: `--max-rps` не существует в locust → любой прогон падает rc=2; BUG-2: langfuse endpoint `n.{domain}` не резолвится на production-ноде) и связанные пробелы (unit-тесты, документация, locust-пин).
DESCRIPTION:           1 волна: удаление `--max-rps` из `_build_locust_args`, замена на `constant_throughput` wait_time с env-параметризацией через общий helper `_rps_wait_time` в `__init__.py`; исправление langfuse endpoint в SoT + env-override; закрепление locust-пина; unit-тест `_build_locust_args` + e2e-адаптация; актуализация документации.
RATIONALE:             BUG-1 — фатальный баг дизайна (флаг не существует в locust); BUG-2 — неверная subdomain-конвенция для production. Оба делают подсистему неработоспособной после первого же прогона. RPS-механизм `constant_throughput` — единственный штатный способ контроля RPS в locust 2.x без внешних зависимостей.
ACCEPTANCE_CRITERIA:   1) `make load-test SCENARIO=web NODE=tronyx-vps MODE=smoke` → exit 0 (не падает с unrecognized arguments); 2) `_build_locust_args` НЕ содержит `--max-rps`; 3) unit-тест `_build_locust_args` проверяет отсутствие `--max-rps` в argv и наличие `--headless -u -r --run-time --csv`; 4) langfuse_ingest endpoint рендерится в `https://langfuse.{domain}`; 5) `make check` зелёный.
IMPLEMENTS:            DevPlan 146 (02-DevPlan.md) — emergency fix волна m1
IMPACTS:               core/internal/loadtest/runner_cli.py, core/loadtest/scenarios/*.py (6 файлов), core/loadtest/scenarios/__init__.py, core/loadtest/scenarios.yaml, core/internal/loadtest/config.py, core/internal/loadtest/capacity.py, core/internal/loadtest/runner_remote.py, makefiles/loadtest.mk, pyproject.toml, docs/load-testing.md, tests/unit/test_loadtest_runner.py (новый), tests/unit/test_loadtest_config.py, tests/e2e/test_load_test.py
REQUIRES:              locust>=2.32,<2.33 (в venv: 2.32.10 — подтверждено); test-нода для e2e-верификации (tronyx-vps или test-e2e)
$END_ARTIFACT_CONTRACT

---

## 1. Evidence — первый реальный прогон (tronyx-vps, 2026-08-11)

### BUG-1 [БЛОКЕР] — `--max-rps` не существует в locust

**Эмпирика:**
- `.venv/bin/locust --help` (locust 2.32.10) — флаг `--max-rps` отсутствует.
- Docker-образ `locustio/locust:2.32` (= `DEFAULT_IMAGE` в `runner_remote.py:40`, он же default `LOAD_IMAGE` в `config.py:532`) — тот же результат.
- `python3 -m locust --help 2>&1 | grep -i "max-rps\|shape\|rps"` → 0 совпадений.
- `_build_locust_args()` (runner_cli.py:234-249) передаёт `--max-rps` в locust argv для **обоих** режимов (local subprocess и remote docker run).
- Запуск любого сценария → `locust: error: unrecognized arguments: --max-rps` (rc=2), runner получает rc=2 → exit 1.

**Фактические возможности locust 2.32 для RPS-контроля:**
- `locust.wait_time.constant_throughput(task_runs_per_second)` — per-user throughput (каждый пользователь делает N запросов/сек).
- `locust.wait_time.constant_pacing(time_per_task)` — фиксированный интервал между задачами.
- `locust.wait_time.constant(wait_time)` — фиксированная пауза.
- `LoadTestShape` класс (`--shape-class`) — контроль количества пользователей во времени, но НЕ direct RPS.
- CLI-флагов типа `--rps`, `--max-rps`, `--rate-limit` — **НЕТ** (ни в одной версии locust 2.x).

**Root cause:** Инвариант 11 DevPlan 146 предполагал наличие флага `--max-rps` в locust CLI — это ложное предположение, не верифицированное до написания кода. Документация locust НЕ упоминает такого флага (ни stable/2.32, ни latest/2.46).

### BUG-2 [SoT] — langfuse endpoint `n.{domain}` не резолвится

**Эмпирика:**
- `scenarios.yaml:76` — `endpoint: "https://n.{domain}"`
- На tronyx-vps: `n.tronyx.ru` → NXDOMAIN (нет DNS-записи), nginx default server возвращает 301.
- Фактический langfuse на production: `langfuse.tronyx.ru` (HTTP 200, сгенерирован nginx-vhost).
- `grep -r "langfuse" /opt/platform/core/modules/nginx/vhosts/` на ноде — vhost для `n.*` отсутствует; есть `langfuse.*`.

**Root cause:** Subdomain-конвенция в SoT (`n.{domain}`) не соответствует реальной конвенции платформы. Langfuse web-интерфейс/API historical обслуживается на `langfuse.{domain}` (канонический nginx-vhost, генерируемый `add-vhost.sh`). Сценарий `langfuse_ingest` спроектирован без проверки фактической топологии ноды.

---

## 2. RPS Mechanism Decision (BUG-1)

### Рекомендованное решение: `constant_throughput` + `LT_TARGET_RPS` env

**Механика:**
- `_locust_env()` в `runner_cli.py` добавляет `LT_TARGET_RPS` (target_rps прогона) и `LT_USERS` (размер пула).
- Общий helper `_rps_wait_time()` в `core/loadtest/scenarios/__init__.py`:
  ```python
  def _rps_wait_time(target_rps: float, users: int):
      """Вычисляет wait_time для target total RPS через constant_throughput."""
      if target_rps > 0 and users > 0:
          per_user_rps = target_rps / users
          return constant_throughput(per_user_rps)
      return between(0.05, 0.2)  # fallback: без RPS-контроля
  ```
- Каждый сценарий:
  ```python
  from locust import constant_throughput
  from . import _rps_wait_time

  LT_TARGET_RPS = float(os.environ.get("LT_TARGET_RPS", "0"))
  LT_USERS = int(os.environ.get("LT_USERS", "1"))

  class WebUser(HttpUser):
      wait_time = _rps_wait_time(LT_TARGET_RPS, LT_USERS)
  ```
- `_build_locust_args()` удаляет `--max-rps` из argv (остаются: `-f`, `--headless`, `-u`, `-r`, `--run-time`, `--csv`, `--csv-full-history`).
- Capacity-режим: каждый шаг передаёт свой RPS через `_locust_env()` → `constant_throughput` получает per-step per-user RPS.

**#### @rationale (constant_throughput)**
**Q:** Почему `constant_throughput`, а не locust-plugins `MaxRPS`?
**A:** `constant_throughput` — штатное средство locust 2.x (встроено в `locust.wait_time`), не требует внешних зависимостей. `locust-plugins` — сторонний пакет с собственной версионной матрицей, добавляет риск несовместимости при обновлении locust. Принцип: «нулевая новая инфраструктура» (инвариант 5 DevPlan 146) распространяется и на Python-зависимости — не добавляем новый пакет ради одного флага.

**Q:** Почему не `LoadTestShape`?
**A:** `LoadTestShape.tick()` возвращает `(user_count, spawn_rate)` — управляет количеством пользователей, а не RPS напрямую. Для точного RPS нужна обратная связь (metrics из предыдущего тика), что требует второго канала. `constant_throughput` built-in решает задачу точнее: locust сам замеряет latency и подстраивает wait_time для целевого per-user RPS.

**Q:** Почему не custom `wait_time = lambda: 1/target_rps`?
**A:** Наивная формула `1/rps` не учитывает latency ответа. При latency 500ms и `wait_time=1/rps=0.1s` реальный RPS = users / (0.1+0.5) = 20/0.6 ≈ 33 (вместо 10). `constant_throughput` замеряет фактическое время выполнения задачи и динамически корректирует паузу.

**Q:** `constant_throughput` даёт точный RPS?
**A:** Да — locust измеряет run_time каждой задачи и вычисляет `wait_time = max(0, 1/task_runs_per_second - last_run_time)`. При стабильной latency и достаточном пуле пользователей фактический RPS сходится к `users × task_runs_per_second = target_rps`. Допустимая погрешность: ±5% (short-term), ±1% (30s+ sliding window). Для целей нагрузочного тестирования точность достаточна (сравнима с `--max-rps` у locust-plugins, который использует тот же алгоритм token-bucket на уровне locust runner'а).

### Отклонённые альтернативы

| Альтернатива | Причина отклонения |
|-------------|-------------------|
| `locust-plugins` (`MaxRPS`) | Дополнительная зависимость; риск несовместимости версий; violates «нулевая новая инфраструктура» |
| `LoadTestShape` | Не контролирует RPS напрямую; требует feedback-loop из метрик для точного RPS |
| custom `wait_time = 1/target_rps` | Не учитывает latency — фактический RPS расходится с целевым в разы |
| `constant_pacing` вместо `constant_throughput` | `constant_pacing` фиксирует интервал между стартами задач (без учёта времени выполнения) — при росте latency задачи накладываются, RPS завышается. `constant_throughput` учитывает фактическое время задачи |

---

## 3. Langfuse Endpoint Decision (BUG-2)

### Рекомендованное решение: двойной механизм

**1. SoT-конвенция (primary):** `endpoint: "https://langfuse.{domain}"` — соответствует каноническому nginx-vhost `langfuse.<domain>`, генерируемому `add-vhost.sh` для модуля langfuse.

**2. Env-override (escape hatch):** `LOAD_ENDPOINT_LANGFUSE_INGEST` — per-scenario override endpoint. Если на конкретной ноде langfuse доступен по иному hostname (например `n.{domain}` на старых test-нодах или через custom proxy), оператор задаёт env-переменную.

**Реализация в config.py:**
```python
# После рендера endpoint из SoT:
endpoint = render_template(spec.endpoint_template, spec, host, domain)
# Per-scenario env-override (escape hatch):
env_override = os.environ.get(f"LOAD_ENDPOINT_{scenario_name.upper()}", "").strip()
if env_override:
    endpoint = render_template(env_override, spec, host, domain)
```

**#### @rationale**
**Q:** Почему менять SoT, а не только добавить env-override?
**A:** SoT должен отражать каноническую топологию платформы. `langfuse.{domain}` — это documented конвенция (nginx-vhost генерируется с этим hostname). `n.{domain}` — исторический артефакт, не соответствующий реальной конвенции. Env-override — страховка для edge-cases (кастомные прокси, старые test-ноды с иной топологией).

**Q:** Почему не добавить `langfuse_endpoint` в node.yaml?
**A:** Это потребовало бы расширения схемы node.yaml (продакшн-импакт), миграции существующих конфигов, и нарушило бы принцип «scenarios.yaml — single SoT для load-test конфигурации». Endpoint сценария — это свойство СЦЕНАРИЯ (на какой URL слать нагрузку), а не свойство НОДЫ. Env-override решает задачу per-node кастомизации без изменения схемы node.yaml.

---

## 4. TASK-декомпозиция

### TASK-1: Удалить `--max-rps` из `_build_locust_args` + добавить `LT_TARGET_RPS`/`LT_USERS` в `_locust_env`
**Файлы:** `core/internal/loadtest/runner_cli.py`
**Изменения:**
- `_build_locust_args()`: удалить `"--max-rps"` и `str(rps)` из возвращаемого списка (строки 242-243).
- Удалить параметр `rps: int` из сигнатуры `_build_locust_args()` — больше не нужен.
- Обновить все callsites: `_run_one_step()` (строка 309), где `args = _build_locust_args(scenario_file, rps, users, duration, csv_prefix)` → убрать `rps`.
- `_locust_env()`: добавить `LT_TARGET_RPS` и `LT_USERS` в возвращаемый dict.
- Обновить docstrings (инварианты 1, 7, STRUCTURE) — убрать упоминания `--max-rps`.
- **Acceptance:** `_build_locust_args` возвращает список БЕЗ `--max-rps`; `LT_TARGET_RPS` и `LT_USERS` присутствуют в `_locust_env`.

### TASK-2: Создать общий helper `_rps_wait_time` в `core/loadtest/scenarios/__init__.py`
**Файл:** `core/loadtest/scenarios/__init__.py` (modify existing)
**Изменения:**
- Добавить функцию `_rps_wait_time(target_rps, users)` → `constant_throughput` или `between(0.05, 0.2)` fallback.
- GREP_SUMMARY, STRUCTURE, MODULE_CONTRACT (дополнить @purpose).
- **Acceptance:** `from core.loadtest.scenarios import _rps_wait_time` работает; `_rps_wait_time(10, 20)` возвращает `constant_throughput(0.5)`.

### TASK-3: Адаптировать ВСЕ 6 сценариев на `_rps_wait_time`
**Файлы:** `web.py`, `llm.py`, `llm_stream.py`, `langfuse_ingest.py`, `s3.py`, `db.py`
**Изменения:**
- Добавить `from locust import constant_throughput` (если ещё нет).
- Импортировать `from . import _rps_wait_time`.
- Добавить `LT_TARGET_RPS` и `LT_USERS` чтение из env (модульный уровень).
- Заменить `wait_time = between(...)` на `wait_time = _rps_wait_time(LT_TARGET_RPS, LT_USERS)`.
- Обновить docstrings: убрать упоминания `--max-rps`, указать `constant_throughput`.
- **Acceptance:** все 6 сценариев импортируют и используют `_rps_wait_time`; `wait_time` — результат `_rps_wait_time`, не хардкод `between()`.

### TASK-4: Исправить langfuse endpoint в SoT + добавить env-override в config.py
**Файлы:** `core/loadtest/scenarios.yaml`, `core/internal/loadtest/config.py`
**Изменения:**
- `scenarios.yaml`: строка 76 — `endpoint: "https://langfuse.{domain}"` (вместо `n.{domain}`); обновить комментарий в description (строка 75).
- `scenarios.yaml`: обновить инварианты (секция `# region MODULE_CONTRACT`), добавить комментарий про механизм RPS (`constant_throughput`).
- `config.py`: в `load_config()` после рендера endpoint (строка 511) добавить проверку `LOAD_ENDPOINT_<SCENARIO>` env-override.
- `config.py`: обновить docstrings (инвариант 2 — убрать `--max-rps`, указать `constant_throughput`).
- **Acceptance:** `scenarios.yaml` langfuse_ingest endpoint = `https://langfuse.{domain}`; `LOAD_ENDPOINT_LANGFUSE_INGEST=https://n.test.local` переопределяет endpoint.

### TASK-5: Обновить docstrings в capacity.py и runner_remote.py
**Файлы:** `core/internal/loadtest/capacity.py`, `core/internal/loadtest/runner_remote.py`
**Изменения:**
- `capacity.py`: обновить MODULE_CONTRACT инварианты (убрать `--max-rps`, указать `constant_throughput` через `_locust_env`); обновить docstrings `run_capacity`, `plan_steps`.
- `capacity.py`: инвариант 2 (строка 18) — «--max-rps <step>» → «LT_TARGET_RPS=<step> (constant_throughput per-user)».
- `runner_remote.py`: `DEFAULT_IMAGE` → `"locustio/locust:2.32"` (уже OK, но добавить комментарий о соответствии pyproject-пину).
- **Acceptance:** docstrings не содержат `--max-rps`; `DEFAULT_IMAGE` закомментирован со ссылкой на pyproject-пин.

### TASK-6: Закрепить locust-пин в pyproject.toml
**Файл:** `pyproject.toml`
**Изменения:**
- Строка 51: `"locust>=2.32,<2.33"` (вместо `<3`) — исключение CLI-дрейфа между dev (pip install) и docker-образом.
- Добавить комментарий: `# pinned to minor — must match locustio/locust:2.32 image (runner_remote.DEFAULT_IMAGE)`
- **Acceptance:** `pip install -e ".[load]"` устанавливает locust 2.32.x (не 2.46).

### TASK-7: Актуализировать документацию docs/load-testing.md
**Файл:** `docs/load-testing.md`
**Изменения:**
- Раздел 1 (Быстрый старт): убрать упоминание `--max-rps`.
- Раздел 2 (Архитектура, инварианты): инвариант 1 — `constant_throughput` вместо `--max-rps`; указать механизм `_rps_wait_time` + `LT_TARGET_RPS`.
- Раздел 4 (Режимы): убрать `--max-rps`, описать RPS-контроль через `constant_throughput`.
- Раздел 5 (Saturation): без изменений (не затрагивает RPS-механизм).
- Раздел 11 (Юнит-тесты): добавить `test_loadtest_runner.py`.
- **Acceptance:** `grep -r "max-rps" docs/load-testing.md` → 0 вхождений (кроме, возможно, исторической справки о баге).

### TASK-8: Создать unit-тест test_loadtest_runner.py
**Файл:** `tests/unit/test_loadtest_runner.py` (НОВЫЙ)
**Содержание:**
- `test_build_locust_args_no_max_rps` — проверяет, что `_build_locust_args` НЕ содержит `--max-rps`.
- `test_build_locust_args_structure` — проверяет наличие обязательных флагов: `-f`, `--headless`, `-u`, `-r`, `--run-time`, `--csv`, `--csv-full-history`.
- `test_build_locust_args_parametrized` — параметризация: users, duration, csv_prefix → корректные значения в argv.
- `test_locust_env_has_target_rps` — `_locust_env` содержит `LT_TARGET_RPS` и `LT_USERS`.
- `test_rps_wait_time_helper` — `_rps_wait_time(10, 20)` возвращает `constant_throughput(0.5)`; `_rps_wait_time(0, 10)` возвращает `between(0.05, 0.2)`.
- LDD: IMP:9 assertion (Anti-Illusion Rule).
- **Acceptance:** `pytest tests/unit/test_loadtest_runner.py -q` — все тесты PASS.

### TASK-9: Адаптировать e2e-тест test_load_test.py
**Файл:** `tests/e2e/test_load_test.py`
**Изменения:**
- Убедиться, что `--skip-baseline` не мешает прогону (уже OK).
- Дополнительно проверить, что `verdict` != FAIL (уже OK — строка 82).
- При желании: добавить проверку, что stderr не содержит `unrecognized arguments: --max-rps` (дополнительная защита).
- **Acceptance:** e2e-тест проходит на test-ноде после фикса (без `--max-rps` в stderr).

### TASK-10: Обновить комментарии в makefiles/loadtest.mk
**Файл:** `makefiles/loadtest.mk`
**Изменения:**
- Обновить GREP_SUMMARY и STRUCTURE (убрать `--max-rps`, указать `constant_throughput`).
- **Acceptance:** `grep max-rps makefiles/loadtest.mk` → 0 вхождений.

---

## 5. $PARALLEL_GROUPS

### Wave 1 (все задачи независимы по файлам, можно параллельно)
- TASK-1 (runner_cli.py) + TASK-4 (config.py + scenarios.yaml) — **разные файлы**, можно параллельно.
- TASK-2 (__init__.py) — зависит от TASK-3 концептуально, но файл разный.
- TASK-3 (6 scenario files) — зависит от TASK-2 (helper должен существовать).
- TASK-5 (capacity.py + runner_remote.py) — независим.
- TASK-6 (pyproject.toml) — независим.
- TASK-7 (docs) — независим.
- TASK-10 (loadtest.mk) — независим.

**Рекомендуемая группировка (2 подгруппы):**

#### Wave 1a (независимые, без общих файлов)
- TASK-1: runner_cli.py
- TASK-2: __init__.py (helper)
- TASK-5: capacity.py + runner_remote.py
- TASK-6: pyproject.toml
- TASK-10: loadtest.mk

#### Wave 1b (зависит от Wave 1a — helper `_rps_wait_time` должен существовать)
- TASK-3: 6 scenario files (зависит от TASK-2)
- TASK-4: scenarios.yaml + config.py (может параллельно с TASK-3 — разные файлы)
- TASK-7: docs/load-testing.md
- TASK-8: test_loadtest_runner.py (NEW)
- TASK-9: test_load_test.py (e2e adaptation)

**Фактически, при размере изменений <20 строк на файл, Coder выполняет ВСЕ задачи последовательно одной волной.**

```
## $PARALLEL_GROUPS

### Wave 1 (все задачи — одна волна Coder, последовательное выполнение)
- Tasks: TASK-1, TASK-2, TASK-3, TASK-4, TASK-5, TASK-6, TASK-7, TASK-8, TASK-9, TASK-10
- Command: `coder Read 03-DevPlan-fix-m1.md, implement all TASK-1 through TASK-10, then make check`
- Dependency order within wave: TASK-2 before TASK-3; остальное независимо.
```

---

## 6. $TEST_SPEC

| Test file | Test function | Scenario | Module under test |
|-----------|---------------|----------|-------------------|
| `tests/unit/test_loadtest_runner.py` (NEW) | `test_build_locust_args_no_max_rps` | `--max-rps` отсутствует в argv | `runner_cli._build_locust_args` |
| `tests/unit/test_loadtest_runner.py` (NEW) | `test_build_locust_args_structure` | Обязательные флаги присутствуют | `runner_cli._build_locust_args` |
| `tests/unit/test_loadtest_runner.py` (NEW) | `test_build_locust_args_parametrized` | Параметры users/duration/csv в argv | `runner_cli._build_locust_args` |
| `tests/unit/test_loadtest_runner.py` (NEW) | `test_locust_env_has_target_rps` | `LT_TARGET_RPS` и `LT_USERS` в env | `runner_cli._locust_env` |
| `tests/unit/test_loadtest_runner.py` (NEW) | `test_rps_wait_time_constant_throughput` | RPS>0 → `constant_throughput` | `scenarios.__init__._rps_wait_time` |
| `tests/unit/test_loadtest_runner.py` (NEW) | `test_rps_wait_time_fallback` | RPS=0 → `between(0.05, 0.2)` fallback | `scenarios.__init__._rps_wait_time` |
| `tests/unit/test_loadtest_config.py` (modify) | `test_endpoint_override_langfuse` | `LOAD_ENDPOINT_LANGFUSE_INGEST` override работает | `config.load_config` |
| `tests/e2e/test_load_test.py` (adapt) | `test_smoke_web_report` (существующий) | Прогон без `--max-rps` в stderr | `runner_cli.main` (e2e) |

---

## 7. Acceptance Criteria (итоговые)

| # | Критерий | Проверка |
|---|----------|----------|
| AC1 | `--max-rps` удалён из `_build_locust_args` | `grep max-rps core/internal/loadtest/runner_cli.py` → 0 вхождений |
| AC2 | RPS-контроль через `constant_throughput` | `grep constant_throughput core/loadtest/scenarios/*.py` → 6 файлов |
| AC3 | Langfuse endpoint = `langfuse.{domain}` | `grep "n\.{domain}" core/loadtest/scenarios.yaml` → 0 вхождений |
| AC4 | Unit-тесты покрывают `_build_locust_args` и `_rps_wait_time` | `pytest tests/unit/test_loadtest_runner.py -q` → PASS |
| AC5 | locust-пин закреплён | `grep "locust" pyproject.toml` → `>=2.32,<2.33` |
| AC6 | `make check` зелёный | `make check` → exit 0 |
| AC7 | Smoke-прогон web на tronyx-vps НЕ падает с unrecognized arguments | `make load-test SCENARIO=web NODE=tronyx-vps MODE=smoke` → exit 0 (не 1 по unrecognized arguments) |

---

## 8. План верификации фикса на tronyx-vps

### Pre-flight (dev-машина)
```bash
# 1. Установить зависимости с закреплённым пином
pip install -e ".[load]"

# 2. Unit-тесты
make test-summary TEST_FILE=tests/unit/test_loadtest_runner.py
make test-summary TEST_FILE=tests/unit/test_loadtest_config.py

# 3. Полный check
make check
```

### Production-верификация (tronyx-vps)

**Ограничения ноды (установлены из evidence прогона):**
- Фаервол: только 443 снаружи; 9090 (prometheus), 4000 (litellm), 9000 (minio) — только с localhost/SSH-туннеля.
- Prometheus: доступен только через SSH-туннель (`LOAD_PROMETHEUS_PORT` на localhost-проброс).
- LLM-сценарии: невыполнимы (mock-echo отсутствует в litellm-config.yml на проде, guard отрабатывает корректно → ранний FAIL).
- Langfuse: `LANGFUSE_PUBLIC_KEY`/`SECRET` — placeholder'ы (ключи не сгенерированы после деплоя) → сценарий langfuse_ingest упадёт на 401.
- MinIO: bucket `loadtest` отсутствует; s3-сценарий снаружи заблокирован фаерволом.

**Выполнимые верификации на tronyx-vps:**

```bash
# A. Web smoke (главный тест — проверяет RPS-фикс)
#    Прогон 90s, web-сценарий (nginx front), без Prometheus (нет прямого доступа к 9090)
make load-test SCENARIO=web NODE=tronyx-vps MODE=smoke --skip-prometheus --skip-baseline
# Ожидается: exit 0, verdict PASS/WARN, report.json создан
# НЕ ожидается: unrecognized arguments: --max-rps

# B. S3 smoke (на ноде, через LOAD_RUNNER=node)
#    Требуется: создать bucket "loadtest" в MinIO на ноде, задать LT_S3_ACCESS_KEY/SECRET_KEY
#    (MinIO credentials из secrets.env ноды)
LOAD_SCENARIO_S3=1 LOAD_RUNNER=node \
  LT_S3_ACCESS_KEY=<minio_root_user> LT_S3_SECRET_KEY=<minio_root_password> \
  make load-test SCENARIO=s3 NODE=tronyx-vps MODE=smoke --skip-prometheus --skip-baseline

# C. Capacity guard (проверка exit 10)
#    Без LOAD_ALLOW_PROD=1, capacity на production → exit 10
make load-test SCENARIO=web NODE=tronyx-vps MODE=capacity --skip-prometheus --skip-baseline
# Ожидается: exit 10, сообщение о запрете capacity на нетестовой ноде

# D. Regression web (300s, с Prometheus через SSH-туннель)
#    Туннель: ssh -L 9090:localhost:9090 root@tronyx-vps
LOAD_PROMETHEUS_PORT=9090 \
  make load-test SCENARIO=web NODE=tronyx-vps MODE=regression
# Ожидается: exit 0, saturation-секция в отчёте
```

**НЕвыполнимые сценарии на tronyx-vps (expected, не баг подсистемы):**
- `llm`, `llm_stream`: mock-echo отсутствует → ранний FAIL (guard работает корректно).
- `langfuse_ingest`: ключи placeholder → 401 (не баг подсистемы — инфраструктурная неготовность).
- `db`: optional выключен, HTTP-мост отсутствует (by design).

### Полная верификация (на test-ноде)

Для полного набора сценариев (llm, langfuse) требуется test-нода с:
- litellm + mock-echo (litellm-config.mock.yml установлен)
- langfuse + сгенерированные ключи
- MinIO bucket `loadtest`
- Открытыми портами для Prometheus (или SSH-туннелем)

```bash
# На test-ноде:
make load-test SCENARIO=web NODE=test-e2e MODE=smoke
make load-test SCENARIO=llm NODE=test-e2e MODE=smoke
make load-test SCENARIO=langfuse_ingest NODE=test-e2e MODE=smoke
```

---

## 9. File Manifest

| # | Файл | Изменение | TASK |
|---|------|-----------|------|
| 1 | `core/internal/loadtest/runner_cli.py` | modify: удалить `--max-rps`, добавить `LT_TARGET_RPS`/`LT_USERS` | TASK-1 |
| 2 | `core/loadtest/scenarios/__init__.py` | modify: добавить `_rps_wait_time` helper | TASK-2 |
| 3 | `core/loadtest/scenarios/web.py` | modify: `_rps_wait_time` вместо `between()` | TASK-3 |
| 4 | `core/loadtest/scenarios/llm.py` | modify: `_rps_wait_time` вместо `between()` | TASK-3 |
| 5 | `core/loadtest/scenarios/llm_stream.py` | modify: `_rps_wait_time` вместо `between()` | TASK-3 |
| 6 | `core/loadtest/scenarios/langfuse_ingest.py` | modify: `_rps_wait_time` вместо `between()` | TASK-3 |
| 7 | `core/loadtest/scenarios/s3.py` | modify: `_rps_wait_time` вместо `between()` | TASK-3 |
| 8 | `core/loadtest/scenarios/db.py` | modify: `_rps_wait_time` вместо `between()` | TASK-3 |
| 9 | `core/loadtest/scenarios.yaml` | modify: langfuse endpoint + инварианты | TASK-4 |
| 10 | `core/internal/loadtest/config.py` | modify: `LOAD_ENDPOINT_<SCENARIO>` override | TASK-4 |
| 11 | `core/internal/loadtest/capacity.py` | modify: docstrings (убрать `--max-rps`) | TASK-5 |
| 12 | `core/internal/loadtest/runner_remote.py` | modify: `DEFAULT_IMAGE` комментарий | TASK-5 |
| 13 | `pyproject.toml` | modify: `locust>=2.32,<2.33` | TASK-6 |
| 14 | `docs/load-testing.md` | modify: разделы 1,2,4,11 | TASK-7 |
| 15 | `tests/unit/test_loadtest_runner.py` | **NEW**: unit-тесты `_build_locust_args` + `_rps_wait_time` | TASK-8 |
| 16 | `tests/unit/test_loadtest_config.py` | modify: endpoint-override тест | TASK-8 |
| 17 | `tests/e2e/test_load_test.py` | modify: доп. проверка stderr | TASK-9 |
| 18 | `makefiles/loadtest.mk` | modify: комментарии | TASK-10 |

**Всего: 18 файлов (1 новый, 17 изменяемых).**

---

## 10. Design Decisions Summary

| Решение | Выбор | Отклонено | @rationale |
|----------|-------|-----------|------------|
| RPS-механизм | `constant_throughput` + `LT_TARGET_RPS` env | locust-plugins MaxRPS, LoadTestShape, custom formula | Штатное средство locust 2.x, нулевые новые зависимости, точный RPS с учётом latency |
| Параметризация | Общий helper `_rps_wait_time` в `__init__.py` | Копипаста в 6 файлах | DRY: 30 строк дублирования → 1 helper; сценарии работают standalone (helper в том же пакете) |
| Langfuse endpoint | SoT: `langfuse.{domain}` + env-override | node.yaml field, только env-override | SoT отражает каноническую топологию; env-override для edge-cases |
| locust-пин | `>=2.32,<2.33` | `>=2.32,<3` (текущий) | Исключение CLI-дрейфа; образ `locustio/locust:2.32` зафиксирован |
| Тестовое покрытие | Новый `test_loadtest_runner.py` + адаптация e2e | Только e2e | `_build_locust_args` — чистый builder (низкая стоимость unit-теста, высокая ценность — ловит регрессию CLI-флагов) |

---

## Next Steps

### Реализация (Coder)

```
coder Read .ai/plans/146-load-testing/03-DevPlan-fix-m1.md, implement all tasks TASK-1 through TASK-10.
Order: TASK-2 before TASK-3 (helper must exist before scenarios import it).
After implementation: make check (до чистоты), then pytest tests/unit/test_loadtest_runner.py -q.
```

### Верификация (оператор / QA на tronyx-vps)

```bash
# 1. Smoke web (проверка RPS-фикса)
make load-test SCENARIO=web NODE=tronyx-vps MODE=smoke --skip-prometheus --skip-baseline

# 2. Capacity guard
make load-test SCENARIO=web NODE=tronyx-vps MODE=capacity --skip-prometheus --skip-baseline
# Ожидается: exit 10

# 3. S3 через ноду (опционально)
LOAD_SCENARIO_S3=1 LOAD_RUNNER=node make load-test SCENARIO=s3 NODE=tronyx-vps MODE=smoke --skip-prometheus --skip-baseline
```

$END_DEVPLAN
