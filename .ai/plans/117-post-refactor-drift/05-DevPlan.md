# 05-DevPlan — Бриф D: реестры таймаутов/портов/env + CI-гейты

$ARTIFACT_CONTRACT
- PURPOSE: Реализация задач 27–36, 67–71 программного брифа 117 — создание единых реестров таймаутов, портов и env-переменных, расширение CI-гейтов для enforcement и устранение слепых зон.
- DESCRIPTION: 15 задач: (27) 8 raw ConnectTimeout=10 в CI → SSH_OPTS SoT, (28) docker timeout 15 → DOCKER_CMD_TIMEOUT=10, расширение гейта timeout_literals, (29) watchdog-таймауты → timeouts.py, (30) NGINX_CERT_DIR dual-context документирование, (31) реестр портов (STATUS_PAGE_PORT → SoT), (32) healthcheck_poll параметры → timeouts.py, (33) WATCHDOG_* декларация + PLATFORM_DOMAIN fallback, (34) три retry-политики → единый реестр, (35) healthcheck_poller docstring (КОРРЕКЦИЯ — docstring соответствует факту), (36) хардкод-порты 8000/8080 → SoT, (67) platform-gate-fast.yml pre-commit, (68) гейт timeout_literals: +10/15 + scope core/modules + workflows, (69) ssh_opts_sole_path: покрыть .github/workflows, (70) docker-sole-path: покрыть shell/make, (71) двойной pre-commit-run.
- RATIONALE: Без единого реестра каждый новый модуль/воркфлоу будет приносить ad-hoc таймауты, порты и env-переменные, ускоряя дрейф. Расширение гейтов на CI-воркфлоу и core/modules закрывает слепые зоны, где дрейф уже накопился (8 raw ConnectTimeout=10, docker timeout=15×6). AC5 программы: ноль новых глаголов/механизмов — только унификация и гейты.
- ACCEPTANCE_CRITERIA:
  - AC-D1: 0 raw `ConnectTimeout=\d+` в .github/workflows/*.yml (все используют `python3 -m core.internal.shared.ssh_opts --shell`).
  - AC-D2: 0 `timeout=15` в docker/ssh/healthcheck-домене core/internal (все → timeouts.py константы).
  - AC-D3: watchdog-таймауты (90/30/10/5/3) импортируются из timeouts.py.
  - AC-D4: STATUS_PAGE_PORT=8080 зарегистрирован в platform-infra.yaml env_defaults; хардкод-порты 8000/8080 в healthcheck_poller → конфигурируемые/env.
  - AC-D5: 0 расходящихся retry-политик (MAX_RETRIES, backoff) — все через timeouts.py.
  - AC-D6: `make gate MODE=fast` зелёный; расширенные гейты (timeout_literals, ssh_opts_sole_path, docker_sole_path) покрывают workflows + core/modules.
  - AC-D7: `make check-manifests` зелёный (после регистрации STATUS_PAGE_PORT в platform-infra.yaml).
  - AC-D8: platform-gate-fast.yml: pre-commit либо установлен, либо SKIP_PRECOMMIT=1; двойной pre-commit-run устранён.
- IMPLEMENTS: 117 01-Brief задачи 27–36, 67–71.
- IMPACTS: core/internal/shared/timeouts.py, core/internal/shared/ssh_opts.py, core/modules/hermes-agent/watchdog/agent_watchdog.py, core/internal/deploy/healthcheck_poller.py, core/internal/bootstrap/deploy/context_deployer.py, core/internal/bootstrap/deploy/docker_orchestrator.py, core/internal/bootstrap/deploy/sudoers_generator.py, core/internal/deploy/deploy_engine.py, core/internal/bootstrap/lifecycle/state_machine.py, core/platform-infra.yaml, platform-env.yaml (generated), .env.example (generated), core/modules/nginx/docker-compose.base.yml, core/modules/hermes-agent/docker-compose.base.yml, .github/workflows/core-deploy.yml, .github/workflows/deploy-project.yml, .github/workflows/platform-gate-fast.yml, .github/workflows/platform-test.yml, tests/gates/test_gate_timeout_literals.py, tests/gates/test_gate_ssh_opts_sole_path.py, tests/gates/test_gate_docker_sole_path.py, tests/unit/ (+ affected).
- REQUIRES: 117 01-Brief (реестр), зелёный gate после брифов A/B/C, верификация фактов в коде (05-DevPlan §0).

---

## 0. Коррекции к исходному брифу (по результатам верификации)

| Задача | Исходный вердикт брифа | Фактический вердикт | Действие |
|--------|----------------------|---------------------|----------|
| 27 (HIGH) | «8 сырых ConnectTimeout=10 в CI → SSH_OPTS SoT» | **Факт**: 8 вхождений `ConnectTimeout=10` в core-deploy.yml (5) + deploy-project.yml (3). **Канон**: SSH_CONNECT_TIMEOUT=30 (timeouts.py:56). | Заменить 8 литералов на `$(python3 -m core.internal.shared.ssh_opts --shell)` — CI использует 10 (неверно), канон = 30. |
| 28 (HIGH) | «docker timeout 15 vs канон 10; гейт не покрывает 15 и core/modules» | **Факт**: 6× `timeout=15` в core/internal (docker_orchestrator:322,351,728,784; context_deployer:738; sudoers_generator:411; deploy_engine:733). **Канон**: DOCKER_CMD_TIMEOUT=10 (timeouts.py:44). Гейт _ALLOWED_TIMEOUT_LITERALS = {30,60,120,180,300,600} — **не покрывает ни 10, ни 15**. | Расширить набор до {10,15,30,60,120,180,300,600}. Заменить 15→DOCKER_CMD_TIMEOUT(10). |
| 30 (HIGH) | «NGINX_CERT_DIR default /etc/letsencrypt vs SoT ./dev-certs» | **Факт**: compose default `/etc/letsencrypt` — **production VPS default** (корректно для прода). SoT в platform-infra.yaml:223 = `./dev-certs` — **dev default**. Два разных контекста, не баг. | **НЕ менять** compose default. Добавить комментарий: «VPS prod default /etc/letsencrypt; dev override через NGINX_CERT_DIR=./dev-certs (SoT platform-infra.yaml)». Зафиксировать dual-context в документации. |
| 32 (MED) | «healthcheck_poll: 10/1 vs канон 60/3; interval 2; 100s» | **Факт**: context_deployer:467 использует `timeout=10, interval=1` (жёсткий опрос). healthcheck_poller: docstring 30/10/6 = 60s окно. Никаких 100s. | Привести context_deployer к канону HEALTHCHECK_POLL_TIMEOUT=60 (timeout=60, interval=3). Healthcheck_poller значения OK (30/10/6). |
| 33 (MED) | «CONTEXT_IMAGE sha vs tag; PLATFORM_DOMAIN localhost vs SoT» | **Факт**: CONTEXT_IMAGE sha→tag **уже исправлен** (DevPlan 116 B3, platform-infra.yaml:146 использует `v2026.7.1`). PLATFORM_DOMAIN:-localhost — только в monitoring compose как compose-level fallback, не дрейф. | CONTEXT_IMAGE — закрыт. PLATFORM_DOMAIN:-localhost → документировать как легитимный fallback. |
| 35 (LOW) | «healthcheck_poller docstring vs факт (60 vs 180s)» | **Факт**: docstring (timeout=30, interval=10, retries=6 = 60s) **совпадает** с кодом (DEFAULT_POLL_TIMEOUT=30, DEFAULT_POLL_INTERVAL=10, DEFAULT_MAX_RETRIES=6). Никаких 180s. | Задача закрыта без действий — docstring корректен. Зафиксировать подтверждение. |
| 67 (HIGH) | «platform-gate-fast.yml: install-pre-commit 'false' → починить» | **Факт**: gate-fast workflow (L50) `install-pre-commit: 'false'`, но `make gate MODE=fast` (L60) запускает `make pre-commit-run` как Step 1. На cache-hit работает, на cache-miss — command not found. | Два варианта: (A) `install-pre-commit: 'true'` + кэш pre-commit, (B) `SKIP_PRECOMMIT=1` при вызове gate. Решение D67. |

---

## 1. Технический анализ и решения

### Задача 27 (HIGH) — 8 raw ConnectTimeout=10 в CI → SSH_OPTS SoT

**Факты (верифицированы):**
- core-deploy.yml: 5 вхождений (L108,128,161,179,188) `ssh -o ConnectTimeout=10`
- deploy-project.yml: 3 вхождения (L124,145,156) `ssh -o ConnectTimeout=10`
- Канон: SSH_CONNECT_TIMEOUT=30 (timeouts.py:56), используется через ssh_opts.py:46 `f"ConnectTimeout={SSH_CONNECT_TIMEOUT}"`
- CI-воркфлоу не могут импортировать Python — нужен shell-интерфейс
- ssh_opts.py уже имеет CLI `--shell`: печатает `-o BatchMode=yes -o StrictHostKeyChecking=accept-new -o ConnectTimeout=30 -o ServerAliveInterval=30 -o ServerAliveCountMax=10`

**Решение D27:** Заменить 8 raw `ssh -o ConnectTimeout=10` на конструкцию с получением флагов из SoT:
```yaml
# Шаг инициализации (до первого ssh):
- name: Get SSH_OPTS from SoT
  id: ssh_opts
  run: echo "opts=$(python3 -m core.internal.shared.ssh_opts --shell)" >> $GITHUB_OUTPUT

# Использование:
ssh ${{ steps.ssh_opts.outputs.opts }} <host> <command>
```
Это гарантирует единый ConnectTimeout (30) и все остальные SSH-флаги из канона. При изменении SSH_CONNECT_TIMEOUT в timeouts.py CI автоматически подхватывает новое значение.

**Файлы:** `.github/workflows/core-deploy.yml` (5 строк), `.github/workflows/deploy-project.yml` (3 строки).

**Риск:** LOW. ssh_opts CLI — стабильный интерфейс, тестируется unit-тестами (test_shared_ssh_opts.py).

---

### Задача 28 (HIGH) — docker timeout 15 → DOCKER_CMD_TIMEOUT=10 + расширение гейта

**Факты (верифицированы):**
- docker_orchestrator.py: `timeout=15` на L322 (docker ps), L351 (docker inspect), L728 (docker compose config), L784 (docker tag)
- context_deployer.py:738: `timeout=15` (docker ps)
- sudoers_generator.py:411: `timeout=15` (visudo)
- deploy_engine.py:733: `timeout=15` (docker compose config)
- Канон: DOCKER_CMD_TIMEOUT=10 (timeouts.py:44) — для внутренних docker ps/inspect/tag
- Гейт test_gate_timeout_literals.py: _ALLOWED_TIMEOUT_LITERALS = {30,60,120,180,300,600} — **не включает 10 и 15**

**Решение D28:**
1. **Расширить гейт**: добавить 10 и 15 в `_ALLOWED_TIMEOUT_LITERALS` → `{10, 15, 30, 60, 120, 180, 300, 600}`. Это поймает будущие ad-hoc литералы.
2. **Мигрировать 15→10**: все 6 `timeout=15` в docker-домене → `timeout=DOCKER_CMD_TIMEOUT` (=10). Для visudo (sudoers_generator) — отдельная константа? Проверить: visudo — не docker, отдельный домен. Создать `SUDOERS_CMD_TIMEOUT = 15` в timeouts.py или использовать DOCKER_CMD_TIMEOUT=10 (visudo <1s). **Рекомендация:** DOCKER_CMD_TIMEOUT=10 для visudo тоже (быстро), но если нужен запас — добавить SUDOERS_CMD_TIMEOUT=15.
3. **Расширить scope гейта**: добавить `core/modules/` в _DOMAIN_FILES (watchdog, status-page — содержат subprocess docker-вызовы). Текущий scope: только `bootstrap/deploy/`, `deploy/`, `bootstrap/converge/`, `shared/`.

**Файлы:** `timeouts.py` (+ DOCKER_CMD_TIMEOUT документирование), `docker_orchestrator.py` (×4), `context_deployer.py:738`, `sudoers_generator.py:411`, `deploy_engine.py:733`, `test_gate_timeout_literals.py` (_ALLOWED_TIMEOUT_LITERALS + _DOMAIN_FILES).

**Риск:** LOW. DOCKER_CMD_TIMEOUT=10 валидирован unit-тестами (test_shared_timeouts.py). Замена литерала на константу — механическая.

---

### Задача 29 (HIGH) — Watchdog-таймауты (90/30/10/5/3) вне timeouts.py

**Факты (верифицированы):**
- agent_watchdog.py:175: `watchdog_timeout=int(os.environ.get("WATCHDOG_TIMEOUT", "90"))`
- agent_watchdog.py:186: `poll_interval=int(os.environ.get("POLL_INTERVAL", "5"))`
- agent_watchdog.py:187: `curl_max_time=int(os.environ.get("CURL_MAX_TIME", "3"))`
- agent_watchdog.py:188: `curl_tg_max_time=int(os.environ.get("CURL_TG_MAX_TIME", "30"))`
- Все четыре — env-overridable с hardcoded дефолтами. Не используют timeouts.py.
- timeouts.py содержит только docker/ssh/healthcheck/retry-домены — watchdog-домен не покрыт.

**Решение D29:** Добавить watchdog-секцию в timeouts.py:
```python
# ── Watchdog domain ──
WATCHDOG_TIMEOUT = 90          # Общий таймаут watchdog-цикла
WATCHDOG_POLL_INTERVAL = 5     # Интервал опроса health endpoint
WATCHDOG_CURL_MAX_TIME = 3     # Таймаут curl healthcheck
WATCHDOG_CURL_TG_MAX_TIME = 30 # Таймаут curl Telegram API
```
В agent_watchdog.py заменить `int(os.environ.get("WATCHDOG_TIMEOUT", "90"))` на `int(os.environ.get("WATCHDOG_TIMEOUT", str(WATCHDOG_TIMEOUT)))`. Env-переменные сохраняют приоритет (runtime override), но дефолт — из timeouts.py.

**Файлы:** `timeouts.py` (+ watchdog секция), `agent_watchdog.py:175,186,187,188`.

**Риск:** LOW. Значения не меняются — только источник дефолта.

---

### Задача 30 (HIGH) — NGINX_CERT_DIR: dual-context (НЕ баг, документирование)

**Факты (верифицированы — КОРРЕКЦИЯ):**
- nginx compose:73: `${NGINX_CERT_DIR:-/etc/letsencrypt}` — production VPS default
- platform-infra.yaml:223: `NGINX_CERT_DIR: "./dev-certs"` — dev default
- .env.example:208: `NGINX_CERT_DIR=./dev-certs` — сгенерировано из SoT
- Два значения обслуживают разные контексты деплоя: `/etc/letsencrypt` для VPS (где certbot/ACME кладёт сертификаты), `./dev-certs` для локальной разработки (dev-cert генератор)

**Решение D30:** **НЕ менять** значения. Добавить в nginx compose комментарий, ссылающийся на SoT:
```yaml
# VPS TLS certs (/etc/letsencrypt) — production default (certbot/ACME target dir).
# Dev override: NGINX_CERT_DIR=./dev-certs (SoT: platform-infra.yaml env_defaults).
- ${NGINX_CERT_DIR:-/etc/letsencrypt}:/etc/letsencrypt:ro
```
В platform-infra.yaml добавить комментарий: «Dev default; production overridden via node-configs/env to /etc/letsencrypt».

**Файлы:** `core/modules/nginx/docker-compose.base.yml:70-73` (комментарий), `core/platform-infra.yaml:223` (комментарий).

**Риск:** NONE (только документация).

---

### Задача 31 (MED) — Реестр портов: STATUS_PAGE_PORT (8080) → SoT

**Факты (верифицированы):**
- platform-infra.yaml env_defaults: HERMES_DASHBOARD_PORT=9119 (L210), HERMES_DESKTOP_PORT=8642 (L211), NGINX_HTTP_PORT=80 (L225), NGINX_HTTPS_PORT=443 (L226), NGINX_EXPORTER_PORT=9113 (L227)
- STATUS_PAGE_PORT=8080 — **НЕ зарегистрирован** в platform-infra.yaml
- AGENT_PORT (agent_watchdog.py:147) = 9119 — это HERMES_DASHBOARD_PORT, дублирующее имя
- platform-env.yaml (generated): HERMES_AGENT_PORT=9119, HERMES_DASHBOARD_PORT=9119

**Решение D31:**
1. **Добавить STATUS_PAGE_PORT=8080** в platform-infra.yaml env_defaults.
2. **AGENT_PORT → HERMES_DASHBOARD_PORT**: в agent_watchdog.py заменить `os.environ.get("AGENT_PORT", "9119")` на `os.environ.get("HERMES_DASHBOARD_PORT", "9119")` или унифицировать на уровне импорта из platform-infra значений. **Рекомендация:** оставить AGENT_PORT как runtime env var (меньше breaking changes), но документировать соответствие: AGENT_PORT = HERMES_DASHBOARD_PORT.
3. После изменения platform-infra.yaml — перегенерировать platform-env.yaml и .env.example (`make generate-manifests`).

**Файлы:** `core/platform-infra.yaml` (+ STATUS_PAGE_PORT), `agent_watchdog.py:147` (документирование), platform-env.yaml (generated), .env.example (generated).

**Риск:** LOW. STATUS_PAGE_PORT=8080 уже используется status-page/app.py как дефолт — регистрация только формализует.

---

### Задача 32 (MED) — healthcheck_poll параметры → канон

**Факты (верифицированы — КОРРЕКЦИЯ):**
- context_deployer.py:467: `_shared_healthcheck_poll(project_name, timeout=10, interval=1)` — 10 попыток с интервалом 1с
- healthcheck_poller.py:35-37: DEFAULT_POLL_TIMEOUT=30, DEFAULT_POLL_INTERVAL=10, DEFAULT_MAX_RETRIES=6 — 60s окно
- docker_orchestrator.py:132-133: DEFAULT_HEALTHCHECK_MAX_RETRIES=10, DEFAULT_HEALTHCHECK_RETRY_INTERVAL=10 — 100s окно
- Канон: HEALTHCHECK_POLL_TIMEOUT=60 (timeouts.py:41), DOCKER_CMD_TIMEOUT=10 (timeouts.py:44)

**Решение D32:**
1. Добавить в timeouts.py:
   ```python
   HEALTHCHECK_POLL_INTERVAL = 3   # Интервал между опросами healthcheck
   HEALTHCHECK_POLL_MAX_RETRIES = 20  # HEALTHCHECK_POLL_TIMEOUT / HEALTHCHECK_POLL_INTERVAL
   ```
2. context_deployer.py:467 → `timeout=HEALTHCHECK_POLL_TIMEOUT, interval=HEALTHCHECK_POLL_INTERVAL` (60/3 = 20 попыток)
3. docker_orchestrator.py:132-133 → использовать HEALTHCHECK_POLL_TIMEOUT/HEALTHCHECK_POLL_INTERVAL
4. healthcheck_poller.py → оставить свои параметры (другая семантика: per-check timeout=30, interval=10, retries=6), но документировать различие

**Файлы:** `timeouts.py` (+ константы), `context_deployer.py:467`, `docker_orchestrator.py:132-133`.

**Риск:** LOW. Увеличение с 10с до 60с для context_deployer — потенциально замедляет CI, но 10с было нереалистично мало.

---

### Задача 33 (MED) — WATCHDOG_* декларация + PLATFORM_DOMAIN fallback

**Факты (верифицированы — КОРРЕКЦИЯ):**
- WATCHDOG_TIMEOUT, POLL_INTERVAL, CURL_MAX_TIME, CURL_TG_MAX_TIME, AGENT_PORT, CIRCUIT_BREAKER_STATE_DIR, CIRCUIT_BREAKER_SERVICES — все используются ТОЛЬКО watchdog-модулем (agent_watchdog.py + .service/.timer)
- Эти переменные не платформенные — они модульные. Регистрировать в platform-infra.yaml не требуется.
- PLATFORM_DOMAIN:-localhost в monitoring compose:172-173 — compose-level fallback, не дрейф
- CONTEXT_IMAGE sha→tag — уже исправлен (DevPlan 116 B3)

**Решение D33:**
1. **WATCHDOG_*** — **НЕ регистрировать** в platform-infra.yaml (модульные, не платформенные). Вместо этого: добавить в hermes-agent/module.yaml env_requires секцию с документированием WATCHDOG_TIMEOUT, POLL_INTERVAL и др. как опциональных переменных модуля.
2. **PLATFORM_DOMAIN:-localhost** — оставить как легитимный fallback. Добавить комментарий: «compose-level fallback; runtime PLATFORM_DOMAIN always set via platform-env.yaml».
3. **CONTEXT_IMAGE** — задача закрыта. Зафиксировать подтверждение.

**Файлы:** `core/modules/hermes-agent/module.yaml` (env_requires), `core/modules/monitoring/docker-compose.base.yml:172-173` (комментарий).

**Риск:** LOW. Только документация.

---

### Задача 34 (MED) — Три retry-политики → единый реестр

**Факты (верифицированы):**
- timeouts.py:70-73: RETRY_BACKOFF_SECONDS=[5,10,20], RETRY_COUNT=2 — для docker_compose retry_pull и channels _retry_deliver
- state_machine.py:259-260: MAX_RETRIES=3, RETRY_BACKOFF_BASE=2 (2,4,8) — **отдельная** retry-политика, не использует timeouts.py
- healthcheck_poller.py:37: DEFAULT_MAX_RETRIES=6 — **отдельная** (healthcheck-домен, другая семантика)
- docker_orchestrator.py:132: DEFAULT_HEALTHCHECK_MAX_RETRIES=10 — **отдельная**

**Анализ доменов:**
| Домен | Retry-политика | Обоснование |
|-------|---------------|-------------|
| docker pull (retry_pull) | [5,10,20]s backoff, 3 попытки | Транзиентные registry-ошибки |
| channels deliver | RETRY_COUNT=2, экспоненциальный [5,10,20] | Сетевые сбои при деплое |
| state_machine bootstrap | 3 попытки, экспоненциальный (2,4,8) | Транзиентные ошибки шагов |
| healthcheck poll (poller) | 6 попыток, интервал 10s | Ожидание старта контейнера |
| healthcheck (orchestrator) | 10 попыток, интервал 10s | Ожидание healthcheck после деплоя |

**Решение D34:**
1. state_machine.py → использовать timeouts.RETRY_BACKOFF_SECONDS и RETRY_COUNT вместо локальных MAX_RETRIES=3, RETRY_BACKOFF_BASE=2. **НО:** семантика разная — state_machine использует `RETRY_BACKOFF_BASE**attempt` (2,4,8), а не список [5,10,20]. Добавить в timeouts.py: `RETRY_BACKOFF_EXPONENTIAL_BASE = 2` для экспоненциального backoff.
2. healthcheck_poller.py DEFAULT_MAX_RETRIES=6 → HEALTHCHECK_POLL_MAX_RETRIES (из D32).
3. docker_orchestrator.py DEFAULT_HEALTHCHECK_MAX_RETRIES=10 → HEALTHCHECK_POLL_MAX_RETRIES (из D32).
4. Все потребители retry-backoff → timeouts.py (единый реестр).

**Файлы:** `timeouts.py` (+ RETRY_BACKOFF_EXPONENTIAL_BASE, HEALTHCHECK_POLL_MAX_RETRIES), `state_machine.py:259-260`, `healthcheck_poller.py:37`, `docker_orchestrator.py:132`.

**Риск:** MED. Изменение retry-параметров state_machine (3→2 retries) требует валидации на тестовой ноде. При падении — rollback: восстановить локальные константы.

---

### Задача 35 (LOW) — healthcheck_poller docstring vs факт: КОРРЕКЦИЯ (без действий)

**Факты (верифицированы):**
- Docstring (L14-16): timeout=30s, interval=10s, retries=6, total ~60s
- Код: DEFAULT_POLL_TIMEOUT=30, DEFAULT_POLL_INTERVAL=10, DEFAULT_MAX_RETRIES=6
- **Совпадают.** Никакого расхождения 60 vs 180s. Бриф ошибался.

**Решение D35:** Задача закрыта без действий. Зафиксировать подтверждение.

**Файлы:** нет изменений.

**Риск:** NONE.

---

### Задача 36 (LOW) — Порт 8000/8080 хардкод

**Факты (верифицированы):**
- healthcheck_poller.py:170-172: `f"http://{project_name}:8080/health"`, `f"http://{project_name}:8000/health"`
- Эти порты — эвристика для HTTP-healthcheck проектов. Не конфигурируются.

**Решение D36:**
1. Добавить `PROJECT_HEALTHCHECK_PORTS = [8080, 8000]` в timeouts.py (или отдельный реестр `healthcheck_config.py`).
2. healthcheck_poller.py → импортировать PROJECT_HEALTHCHECK_PORTS, генерировать URLs динамически.
3. Альтернативно: оставить список как fallback, но добавить возможность переопределения через env `PROJECT_HEALTHCHECK_PORTS` (csv).

**Рекомендация:** минимальное изменение — вынести список в конфигурируемую константу в timeouts.py. Без env-переопределения (AC5: без нового функционала).

**Файлы:** `timeouts.py` (+ PROJECT_HEALTHCHECK_PORTS), `healthcheck_poller.py:170-172`.

**Риск:** LOW. Значения не меняются — только источник.

---

### Задача 67 (HIGH) — platform-gate-fast.yml: pre-commit

**Факты (верифицированы):**
- platform-gate-fast.yml:50: `install-pre-commit: 'false'`
- platform-gate-fast.yml:60: `make gate MODE=fast` — вызывает `make pre-commit-run` (Step 1/8, ci.mk:142-143)
- На cache-hit (pre-commit установлен в раннере) — работает. На cache-miss — `command not found`.
- gate-fast — лёгкий workflow (~2-3 мин), триггер downstream (core-deploy, build-platform, mirror)

**Решение D67:** **Вариант A (рекомендуемый):** изменить `install-pre-commit: 'true'` и добавить caching pre-commit venv (как в platform-test.yml:93-97). Pre-commit — быстрая проверка (~10-20s), не противоречит «fast» дизайну. Это гарантирует, что downstream workflows не запустятся на коде, не прошедшем базовые проверки.

**Вариант B:** `make gate MODE=fast SKIP_PRECOMMIT=1` — пропустить pre-commit в fast-gate. Риск: код с ошибками форматирования/документации попадёт в main → downstream workflows запустятся зря.

**Выбор:** Вариант A (pre-commit в fast-gate). Цена ~15s, польза — fail-fast до downstream.

**Файлы:** `.github/workflows/platform-gate-fast.yml:50` (install-pre-commit → true), добавить cache-шаг.

**Риск:** LOW. Pre-commit добавляет ≤20s к 2-3 мин workflow.

---

### Задача 68 (MED) — Гейт timeout_literals: расширить набор + scope + workflows

**Факты (верифицированы):**
- Текущий набор: _ALLOWED_TIMEOUT_LITERALS = {30, 60, 120, 180, 300, 600}
- Текущий scope: _DOMAIN_FILES (13 файлов в core/internal) + _DOMAIN_DIR_PREFIXES (bootstrap/deploy/, deploy/, bootstrap/converge/)
- Пробелы: нет 10 (канон DOCKER_CMD_TIMEOUT), нет 15 (нужно исправить), нет core/modules/, нет .github/workflows/

**Решение D68:**
1. **Расширить набор**: `{10, 15, 30, 60, 120, 180, 300, 600}`.
2. **Расширить scope на core/modules/**: добавить `core.modules.` префикс в _DOMAIN_DIR_PREFIXES или явно перечислить модули с subprocess docker-вызовами (hermes-agent/watchdog, status-page, nginx dev_cert_generator). **Рекомендация:** явный список (целевой, не все модули).
3. **Workflows**: YAML не парсится AST — отдельный grep-скан: `rg "timeout=\d+" .github/workflows/*.yml` с allowlist (легитимные HTTP-клиенты: smoke-тесты, 150s для component). Для docker/ssh команд в workflow — RED если timeout=литерал из набора.

**Файлы:** `tests/gates/test_gate_timeout_literals.py` (_ALLOWED_TIMEOUT_LITERALS, _DOMAIN_FILES, + workflow-скан функция).

**Риск:** LOW. Новые детекции — после миграции D27/D28.

---

### Задача 69 (MED) — ssh_opts_sole_path: покрыть .github/workflows

**Факты (верифицированы):**
- Текущий гейт: AST-скан core/internal/*.py (3 проверки: Mirror-комментарии, SSH_OPTS-списки, ConnectTimeout-литералы)
- CI-воркфлоу: 8 raw `ssh -o ConnectTimeout=10` — **не сканируются**
- После D27 эти 8 вхождений будут заменены → гейт должен enforce-ить отсутствие регресса

**Решение D69:**
1. Добавить четвёртую проверку (d): grep-скан `.github/workflows/*.yml` на `ConnectTimeout=\d+` → RED (воркфлоу должны получать флаги через `python3 -m core.internal.shared.ssh_opts --shell`).
2. Allowlist: комментарии, документация CI (если упоминают ConnectTimeout в описательных целях — но таких нет).
3. Интегрировать в существующий test_gate_ssh_opts_sole_path.py как `test_ci_workflows_no_raw_connect_timeout`.

**Файлы:** `tests/gates/test_gate_ssh_opts_sole_path.py` (+ test_ci_workflows_no_raw_connect_timeout), `.github/workflows/core-deploy.yml`, `.github/workflows/deploy-project.yml` (после D27).

**Риск:** LOW. После D27 гейт должен быть зелёным.

---

### Задача 70 (MED) — docker-sole-path: покрыть shell/make точки

**Факты (верифицированы):**
- Текущий гейт: AST-скан core/internal/*.py subprocess-вызовов docker compose
- Не покрывает: shell-скрипты (entrypoints/*.sh, lib/*.sh), Makefile/модульные .mk, docker-compose CLI вызовы через variables

**Решение D70:**
1. Добавить grep-скан shell-скриптов: `rg "docker compose" core/entrypoints/ core/lib/ core/internal/bootstrap/*.sh` — RED если прямой вызов вне разрешённых фасадов.
2. Разрешённые точки: `core/lib/healthcheck.sh` (docker inspect не compose), `core/lib/module-interface.sh` (invoke_module_interface — не прямой docker compose), `core/entrypoints/compose-wrapper.sh` (легитимный фасад).
3. Для .mk файлов: проверить `templates/module.mk` и `templates/module-system.mk` — прямые docker compose вызовы должны быть ТОЛЬКО в module.mk (canonical compose wrapper). Добавить allowlist.

**Файлы:** `tests/gates/test_gate_docker_sole_path.py` (+ shell-скан функция), возможно `templates/module.mk` (allowlist-комментарий).

**Риск:** LOW. Shell-скан — grep-based, не AST (ложные срабатывания на комментарии). Mitigation: фильтрация по паттерну `docker compose` (с пробелом) и исключение строк с `#` в начале.

---

### Задача 71 (LOW) — Двойной pre-commit-run (platform-test.yml:100 + gate fast)

**Факты (верифицированы):**
- platform-test.yml:99-100: явный `make pre-commit-run` (с кэшированием pre-commit venv)
- platform-test.yml:117: `make gate MODE=fast` — снова запускает `make pre-commit-run` (Step 1/8)
- platform-test.yml:234: `make gate MODE=ci-docker SKIP_PRECOMMIT=1` — pre-commit пропущен (корректно)
- Двойной запуск: ~10-20s × 2 = избыточно

**Решение D71:** В platform-test.yml:117 добавить `SKIP_PRECOMMIT=1`:
```yaml
run: make gate MODE=fast SKIP_PRECOMMIT=1
```
Pre-commit уже выполнен явно на L99-100 — повтор не нужен. Сокращает CI-время на ~15s.

**Файлы:** `.github/workflows/platform-test.yml:117`.

**Риск:** NONE. Pre-commit гарантированно выполняется явно перед gate.

---

## 2. Порядок реализации

Фаза 1 — реестры (нет зависимостей между собой):
1. **D29** (watchdog-таймауты → timeouts.py) + **D34** (retry-политики) — оба добавляют константы в timeouts.py
2. **D31** (STATUS_PAGE_PORT → platform-infra.yaml)
3. **D36** (PROJECT_HEALTHCHECK_PORTS → timeouts.py)
4. **D30** (NGINX_CERT_DIR комментарии)

Фаза 2 — миграция потребителей:
5. **D27** (CI ConnectTimeout=10 → ssh_opts CLI) — 8 строк в 2 workflow
6. **D28** (timeout=15 → DOCKER_CMD_TIMEOUT) — 6 строк в 4 файла
7. **D32** (healthcheck_poll параметры → timeouts.py) — context_deployer + docker_orchestrator
8. **D33** (WATCHDOG_* документирование в module.yaml)

Фаза 3 — CI-исправления:
9. **D67** (platform-gate-fast.yml pre-commit) + **D71** (двойной pre-commit-run)

Фаза 4 — расширение гейтов:
10. **D68** (timeout_literals: +10/15 + scope + workflows)
11. **D69** (ssh_opts_sole_path: +workflows)
12. **D70** (docker-sole-path: +shell/make)

Фаза 5 — верификация:
13. `make generate-manifests` — перегенерация platform-env.yaml + .env.example (после D31)
14. `make check-manifests` — зелёный
15. `make gate MODE=fast` — все расширенные гейты зелёные
16. `rg "ConnectTimeout=\d+" .github/workflows/` — 0 raw литералов (после D27)
17. `rg "timeout=15" core/internal/` — 0 в docker/ssh/healthcheck домене (после D28)

---

## 3. Критерии приёмки (повтор из контракта)

- AC-D1: 0 raw ConnectTimeout в .github/workflows/*.yml
- AC-D2: 0 timeout=15 в docker/ssh/healthcheck-домене core/internal
- AC-D3: watchdog-таймауты импортируются из timeouts.py
- AC-D4: STATUS_PAGE_PORT в platform-infra.yaml; хардкод-порты → конфигурируемые
- AC-D5: все retry-политики → timeouts.py (RETRY_BACKOFF_*)
- AC-D6: gate MODE=fast зелёный; расширенные гейты покрывают workflows + core/modules
- AC-D7: check-manifests зелёный
- AC-D8: platform-gate-fast.yml — pre-commit green; platform-test.yml — один pre-commit-run

Дополнительно:
- `rg "ConnectTimeout=10" .github/workflows/` → 0 совпадений
- `rg "timeout=15" core/internal/bootstrap/deploy/ core/internal/deploy/` → 0 совпадений

---

## 4. Риски и митигации

| Риск | Митигация |
|------|-----------|
| D27: замена ssh на `${{ steps.ssh_opts.outputs.opts }}` в CI ломает многострочные ssh-команды | Протестировать синтаксис YAML: `ssh ${{ steps.ssh_opts.outputs.opts }}` — флаги в одной строке |
| D28: замена timeout=15→10 для visudo (sudoers_generator) — visudo может длиться >10s на слабых VPS | Добавить SUDOERS_CMD_TIMEOUT=15 в timeouts.py |
| D32: увеличение context_deployer poll с 10s до 60s замедляет CI | 10s было нереалистично мало для docker healthcheck; 60s — канон |
| D34: изменение retry-параметров state_machine (3→2) ломает bootstrap на нестабильных VPS | Оставить RETRY_COUNT=2, но дать state_machine экспоненциальный backoff через timeouts.RETRY_BACKOFF_EXPONENTIAL_BASE |
| D68: расширение scope гейта на core/modules/ вызывает false-positive на легитимных HTTP-вызовах | Явный allowlist модулей; только subprocess с docker/ssh/healthcheck-маркерами |

---

## 5. Оценка

- Изменяемые файлы: ~20 (timeouts.py, agent_watchdog.py, healthcheck_poller.py, context_deployer.py, docker_orchestrator.py, sudoers_generator.py, deploy_engine.py, state_machine.py, platform-infra.yaml, 2 compose-файла, 4 CI-workflow, 3 gate-теста, module.yaml, 2 generated файла).
- Новых файлов: 0 (только правки существующих).
- Строк кода: ~80 строк правок + ~60 строк gate-расширений.
- Трудозатраты: ~0.5-0.75 дня агент-времени. Размер: STANDARD (15 задач, бизнес-логика — миграция на SoT) → только DevPlan.

---

## 6. Отклонения от исходного брифа

| Задача | Отклонение | Причина |
|--------|-----------|---------|
| 30 | NGINX_CERT_DIR НЕ менять (dual-context документирование) | compose default /etc/letsencrypt — production VPS (корректно); SoT ./dev-certs — dev. Два разных контекста. |
| 33 | CONTEXT_IMAGE sha→tag — уже исправлен (DevPlan 116 B3) | Закрыт. WATCHDOG_* — модульные, не платформенные → module.yaml, не platform-infra.yaml. |
| 35 | healthcheck_poller docstring — без действий | Docstring совпадает с кодом (60s окно). Бриф ошибался насчёт 180s. |
| 32 | «100s» → фактически 60s (healthcheck_poller) и 10s (context_deployer) | Бриф завысил значения. Канон = 60s из timeouts.py. |

## 7. Сводка: что становится SoT

| Домен | SoT | Что покрывает |
|-------|-----|---------------|
| **Таймауты** | `core/internal/shared/timeouts.py` | docker, ssh, healthcheck, retry, watchdog (новый домен) |
| **Порты** | `core/platform-infra.yaml` env_defaults | Все platform-wide порты + STATUS_PAGE_PORT (новый) |
| **Env-переменные** | `core/platform-infra.yaml` → `platform-env.yaml` (generated) | Все platform-wide env |
| **SSH-флаги** | `core/internal/shared/ssh_opts.py` | SSH_OPTS + CI через CLI `--shell` |
| **Docker compose** | `core/internal/shared/docker_compose.py` | Все subprocess docker compose вызовы |
| **Healthcheck-критерий** | `shared/docker_compose.healthcheck_poll` | Docker health status (TRAP D5 B5) |

---

## Next Steps

### Wave 1 — реестры + миграция потребителей (Tasks: D29, D34, D31, D36, D30, D27, D28, D32, D33)
```
coder Read .ai/plans/117-post-refactor-drift/05-DevPlan.md, implement Wave 1: D29, D34, D31, D36, D30, D27, D28, D32, D33
```

### Wave 2 — CI-исправления + расширение гейтов (Tasks: D67, D71, D68, D69, D70)
```
coder Read .ai/plans/117-post-refactor-drift/05-DevPlan.md, implement Wave 2: D67, D71, D68, D69, D70
```

### Wave 3 — верификация
```
make generate-manifests && make check-manifests && make gate MODE=fast
```
