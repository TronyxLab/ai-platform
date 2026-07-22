# 038-DevPlan: Fix 6 skip в smoke-тестах (4 группы)

**Source:** Superposition-анализ 4 групп skip-тестов, обнаруженных при прогоне `make test MARKER=smoke`
**Verified against codebase:** 2026-07-22 (SHA: current HEAD)
**Prior artifacts:** none — свежий анализ

$START_DEVPLAN

$ARTIFACT_CONTRACT
PURPOSE:               Устранить 6 skip в smoke-тестах, которые молчаливо скрывают реальные проблемы:
                       (1) Langfuse port — skip вместо fail при неподнявшемся контейнере
                       (2) Hermes API key — module-level константа + отсутствие ключа в SMOKE_ENV
                       (3) macOS cert/bind-mount — безусловный skip на macOS, хотя инфраструктура работает
                       (4) Postgres foreign container — skip вместо переиспользования уже работающих контейнеров
DESCRIPTION:           4 группы изменений, 6 тестов:
                       **Group 1 (Langfuse port):** `test_platform_endpoints.py:238` — заменить `pytest.skip` на `pytest.fail`
                       с диагностикой (docker ps langfuse-test, какие порты реально открыты).
                       **Group 2 (Hermes API key):** `tests/_conftest/smoke.py:SMOKE_ENV` — добавить `API_SERVER_KEY`;
                       `tests/test_smoke_hermes.py:59,179` — перенести чтение из module-level в тело теста.
                       **Group 3 (macOS cert):** `tests/test_smoke_nginx.py:395,492` — убрать
                       `@pytest.mark.skipif(sys.platform == "darwin")` с двух тестов.
                       **Group 4 (Postgres container):** `tests/test_smoke_postgres.py:103-147` —
                       в `_docker_guard`: вместо skip при обнаружении foreign container → установить флаг
                       `_REUSE_CONTAINERS`; в `postgres_up`: если `_REUSE_CONTAINERS` → пропустить compose up/down.
RATIONALE:             Skip-тесты — это технический долг. Каждый skip скрывает потенциальную проблему:
                       - Langfuse: порт проброшен в docker-compose.test.yml (127.0.0.1:13000:3000),
                         но тест skip'ает с сообщением «langfuse is isolated» — противоречие.
                         Контейнер не стартует из-за зависимостей (postgres, redis, clickhouse, minio).
                       - Hermes: module-level константа захватывает None до того как fixture инжектит SMOKE_ENV.
                         Ключ отсутствует в SMOKE_ENV даже после инжекта.
                       - macOS: fixture уже вызывает generate-dev-certs.sh, который работает через openssl fallback.
                         Docker Desktop ≥4.x поддерживает bind-mount из /tmp. Skip не нужен.
                       - Postgres: контейнеры от platform_services и smoke-postgres идентичны (те же имена,
                         те же порты). Вместо конфликта — переиспользовать.
ACCEPTANCE_CRITERIA:
   **AC-1 (Langfuse port):**
       1. `_check_port_forwarded(13000)` возвращает False → `pytest.fail`, а не `pytest.skip`
       2. Fail-сообщение содержит диагностику: `docker ps --filter name=langfuse-test`, список открытых портов
       3. Если контейнер поднялся → тест проходит (порт доступен)
   **AC-2 (Hermes API key):**
       4. `SMOKE_ENV` содержит `"API_SERVER_KEY": "sk-test-api-server-key"`
       5. `test_hermes_api_completions` читает `API_SERVER_KEY` из `os.environ` внутри тела теста (не module-level)
       6. Module-level константа `API_SERVER_KEY = os.environ.get("API_SERVER_KEY")` удалена (line 59)
   **AC-3 (macOS cert — 2 теста):**
       7. `test_nginx_tls_cert_san` — без `@pytest.mark.skipif(sys.platform == "darwin")`
       8. `test_nginx_error_page` — без `@pytest.mark.skipif(sys.platform == "darwin")`
       9. Оба теста проходят на macOS (если нет — появляется реальный баг-репорт)
   **AC-4 (Postgres container — 2 теста):**
       10. `_docker_guard` при обнаружении `postgres-test` под `ai-platform-test` → НЕ skip, установить `_REUSE_CONTAINERS = True`
       11. `postgres_up` при `_REUSE_CONTAINERS` → пропустить compose up/down, только дождаться healthcheck
       12. `test_smoke_postgres_containers_healthy` → PASS (использует уже работающие контейнеры)
       13. `test_smoke_pgbouncer_pg_isready_6432` → PASS (использует уже работающие контейнеры)
   **AC-5 (Регрессия):**
       14. `make test MARKER=smoke` — существующие тесты не сломаны
       15. `ruff check tests/` — 0 errors
IMPLEMENTS:            ~80 LOC изменений в 5 файлах:
                       - `tests/_conftest/smoke.py` — +1 строка (API_SERVER_KEY в SMOKE_ENV)
                       - `tests/test_smoke_hermes.py` — +1/-2 строки (перенос чтения ключа в тело теста)
                       - `tests/test_smoke_nginx.py` — -2 строки (убрать 2 skipif декоратора)
                       - `tests/test_smoke_postgres.py` — ~40 строк (переиспользование контейнеров)
                       - `tests/test_platform_endpoints.py` — ~35 строк (fail с диагностикой вместо skip)
IMPACTS:               **Modified:**
                         - `tests/_conftest/smoke.py` (SMOKE_ENV)
                         - `tests/test_smoke_hermes.py` (API_SERVER_KEY)
                         - `tests/test_smoke_nginx.py` (skipif removal)
                         - `tests/test_smoke_postgres.py` (_docker_guard + postgres_up)
                         - `tests/test_platform_endpoints.py` (langfuse skip→fail)
REQUIRES:              Чистый working tree. Python 3.10+ в .venv. Docker daemon (для smoke-тестов).
TASK_SIZE:             SMALL (~80 LOC, 5 файлов)
CRITICALITY:           MEDIUM — skip-тесты скрывают проблемы, но не блокируют production
$END_ARTIFACT_CONTRACT

---

## $DOCUMENT_PLAN

```
$START_DOCUMENT_PLAN
### Document Plan
**SECTION_GOALS:**
- GOAL_LANGFUSE: Заменить pytest.skip на pytest.fail с диагностикой в test_platform_endpoints.py => GOAL_LANGFUSE
- GOAL_HERMES: Добавить API_SERVER_KEY в SMOKE_ENV + перенести чтение в тело теста => GOAL_HERMES
- GOAL_MACOS: Убрать @skipif(darwin) с 2 тестов nginx => GOAL_MACOS
- GOAL_POSTGRES: Переиспользовать контейнеры от platform_services вместо skip => GOAL_POSTGRES
- GOAL_VERIFY: make test MARKER=smoke → PASS (6 ранее skip'нутых тестов выполняются) => GOAL_VERIFY
**SECTION_USE_CASES:**
- UC_LANGFUSE_FAIL: langfuse контейнер не поднялся → тест FAIL с диагностикой → разработчик видит реальную проблему => UC_LANGFUSE_FAIL
- UC_HERMES_PASS: API_SERVER_KEY в SMOKE_ENV → тест НЕ skip'ает → выполняет реальный HTTP-запрос => UC_HERMES_PASS
- UC_MACOS_PASS: macOS-разработчик запускает smoke-тесты → cert-тесты выполняются => UC_MACOS_PASS
- UC_POSTGRES_REUSE: platform_services поднял postgres → smoke-postgres переиспользует его → PASS => UC_POSTGRES_REUSE
$END_DOCUMENT_PLAN
```

---

## 1. GOAL_LANGFUSE: test_platform_endpoints.py — skip → fail

### Root cause

`docker-compose.test.yml` явно пробрасывает порт: `127.0.0.1:13000:3000` (строка 24).
Тест вызывает `_check_port_forwarded(13000, "/api/public/health")`, которая имеет таймаут 2+3 сек.
Langfuse имеет тяжёлые зависимости (postgres, redis, clickhouse, minio) и стартует медленно.
Если контейнер не готов за 5 сек — `_check_port_forwarded` возвращает `False` → `pytest.skip`
с сообщением «langfuse is isolated from host in test environment».

**Проблема:** сообщение skip'а **врёт**. Langfuse НЕ изолирован — порт проброшен. Skip скрывает
реальную проблему (контейнер не стартовал / не готов за 5 сек).

### Fix

Заменить `pytest.skip` на `pytest.fail` с диагностическим сообщением:

```python
if not _check_port_forwarded(port, "/api/public/health"):
    # Diagnostic: collect container status and port info
    import subprocess as _sp
    _diag_parts = [f"Langfuse port {port} is not accessible."]

    # Check if langfuse-test container exists
    _ps = _sp.run(
        ["docker", "ps", "-a", "--filter", "name=langfuse-test", "--format", "{{.Names}} {{.Status}}"],
        capture_output=True, text=True, timeout=10,
    )
    _diag_parts.append(f"Container status: {_ps.stdout.strip() or 'container not found'}")

    # Check what's actually listening on localhost ports in the 13xxx range
    _lsof = _sp.run(
        ["lsof", "-iTCP", "-sTCP:LISTEN", "-P", "-n"],
        capture_output=True, text=True, timeout=5,
    )
    _port_lines = [l for l in _lsof.stdout.splitlines() if "13000" in l or "13030" in l or "19090" in l]
    if _port_lines:
        _diag_parts.append(f"Listening ports:\n" + "\n".join(_port_lines))
    else:
        _diag_parts.append("No relevant ports listening")

    pytest.fail("\n".join(_diag_parts))
```

### Обоснование

- `pytest.fail` вместо `pytest.skip` — тест честно сообщает о проблеме вместо молчаливого пропуска
- Диагностика (`docker ps`, `lsof`) даёт разработчику контекст для отладки
- Если контейнер поднялся — тест НЕ fail'ит (порт доступен → проходит штатный HTTP-запрос)

### Важно

Это изменение **НЕ гарантирует**, что тест будет проходить. Если langfuse действительно не стартует
в рамках `platform_services`, тест будет **честно падать** — и это правильное поведение. Skip не должен
скрывать инфраструктурные проблемы.

---

## 2. GOAL_HERMES: API_SERVER_KEY — module-level → test body

### Root cause (двойная)

1. **Module-level константа (строка 59):** `API_SERVER_KEY = os.environ.get("API_SERVER_KEY")` выполняется
   при импорте модуля — ДО того как `platform_env` fixture инжектит `SMOKE_ENV` в `os.environ`.
   Даже если добавить ключ в `SMOKE_ENV`, module-level константа останется `None`.

2. **Отсутствие в SMOKE_ENV:** `tests/_conftest/smoke.py:SMOKE_ENV` (строки 90-131) не содержит
   `API_SERVER_KEY`. Все переменные заданы явно, кроме этой.

### Fix — два шага

**Шаг A: Добавить в SMOKE_ENV**

`tests/_conftest/smoke.py`, в словарь `SMOKE_ENV` (после строки 131, перед закрывающей `}`):

```python
    "API_SERVER_KEY": "sk-test-api-server-key",
```

**Шаг Б: Перенести чтение в тело теста**

`tests/test_smoke_hermes.py`:

1. Удалить строку 59 (`API_SERVER_KEY = os.environ.get("API_SERVER_KEY")`)
2. В теле `test_hermes_api_completions` (строка 178), заменить:

```python
# Было (строка 178-179):
    if not API_SERVER_KEY:
        pytest.skip("API_SERVER_KEY not set — cannot authenticate")

# Стало:
    api_server_key = os.environ.get("API_SERVER_KEY")
    if not api_server_key:
        pytest.skip("API_SERVER_KEY not set — cannot authenticate")
```

3. Строка 191 — заменить `API_SERVER_KEY` на `api_server_key`:

```python
# Было:
        "Authorization": f"Bearer {API_SERVER_KEY}",
# Стало:
        "Authorization": f"Bearer {api_server_key}",
```

### Примечание

После изменений `api_server_key` будет прочитан в теле теста, **после** того как `platform_env` fixture
уже инжектила `SMOKE_ENV` в `os.environ`. Значение будет `"sk-test-api-server-key"` → тест не skip'нет.

---

## 3. GOAL_MACOS: nginx skipif — удаление

### Root cause

Два теста в `tests/test_smoke_nginx.py` имеют безусловный macOS skip:

- Строка 395: `test_nginx_tls_cert_san` — `@pytest.mark.skipif(sys.platform == "darwin", reason="...")`
- Строка 492: `test_nginx_error_page` — `@pytest.mark.skipif(sys.platform == "darwin", reason="...")`

Историческая причина: «macOS Docker Desktop has known limitations with mkcert cert generation and bind-mount». Но:

1. **Фикстура `nginx_compose()` (строка 193) уже вызывает `generate-dev-certs.sh`** — этот скрипт имеет
   fallback на openssl, который работает на macOS нативно (`/usr/bin/openssl`).
2. **Docker Desktop ≥4.x поддерживает bind-mount из `/tmp`** — тестовые данные уже монтируются туда.
3. **`is_macos()` helper** в `tests/_conftest/smoke.py:58-73` документирует причину skip'а, но
   она больше не актуальна.

### Fix

Удалить декоратор `@pytest.mark.skipif(...)` с двух тестов:

**Файл:** `tests/test_smoke_nginx.py`

1. Строки 395-398 — убрать:

```python
@pytest.mark.skipif(
    sys.platform == "darwin",
    reason="macOS: Linux-parity in CI — cert/bind-mount not supported on Docker Desktop",
)
```

2. Строки 492-495 — убрать:

```python
@pytest.mark.skipif(
    sys.platform == "darwin",
    reason="macOS: Linux-parity in CI — cert/bind-mount not supported on Docker Desktop",
)
```

### Fallback

Если тесты **действительно** упадут на macOS — это даст реальный баг-репорт с конкретной ошибкой,
а не молчаливый skip. Разработчик сможет диагностировать и исправить реальную проблему.

---

## 4. GOAL_POSTGRES: foreign container → reuse

### Root cause

`tests/test_smoke_postgres.py:_docker_guard` (строки 127-147) проверяет, не заняты ли имена контейнеров
`postgres-test` и `pgbouncer-test` другим compose-проектом. Когда `platform_services` fixture (session-scoped)
уже запустила эти контейнеры под проектом `ai-platform-test`, `_docker_guard` обнаруживает их и делает
`pytest.skip`.

**Но:** контейнеры ИДЕНТИЧНЫ — те же имена (`postgres-test`, `pgbouncer-test`), те же порты
(`15432`, `16432`), тот же postgres. Вместо skip'а — переиспользовать.

### Fix

**Шаг A: `_docker_guard` — флаг вместо skip**

Вместо `pytest.skip` при обнаружении foreign container → установить module-level флаг:

```python
# Module-level flag: True if containers from platform_services are reused
_REUSE_CONTAINERS = False
```

В `_docker_guard` (строки 127-147), заменить блок:

```python
# Было:
        if inspect_result.returncode == 0:
            project = inspect_result.stdout.strip()
            if project and project != COMPOSE_PROJECT_SMOKE:
                pytest.skip(
                    f"Foreign container '{container_name}' belongs to project "
                    f"'{project}', not '{COMPOSE_PROJECT_SMOKE}' — skip smoke"
                )

# Стало:
        if inspect_result.returncode == 0:
            project = inspect_result.stdout.strip()
            if project and project != COMPOSE_PROJECT_SMOKE:
                logger.info(
                    "[IMP:8][_docker_guard] Container '%s' belongs to project '%s' — will reuse",
                    container_name, project,
                )
                global _REUSE_CONTAINERS
                _REUSE_CONTAINERS = True
```

**Шаг Б: `postgres_up` — пропустить compose lifecycle при reuse**

В начале `postgres_up` (после проверки `compose_base` exists), добавить:

```python
    if _REUSE_CONTAINERS:
        logger.info(
            "[IMP:8][postgres_up] Reusing containers from platform_services — skipping compose up/down"
        )
        # Still wait for healthcheck — containers might not be ready yet
        max_retries = 20
        retry_interval = 3
        for attempt in range(1, max_retries + 1):
            statuses = {}
            for container_name in (CONTAINER_POSTGRES, CONTAINER_PGBOUNCER):
                health = subprocess.run(
                    ["docker", "inspect", "--format", "{{.State.Health.Status}}", container_name],
                    capture_output=True, text=True, timeout=10,
                )
                statuses[container_name] = health.stdout.strip()
            if all(s == "healthy" for s in statuses.values()):
                logger.info(
                    "[IMP:9][postgres_up] Reused containers healthy (attempt %d): %s",
                    attempt, statuses,
                )
                break
            logger.info(
                "[IMP:7][postgres_up] Waiting for reused containers (attempt %d/%d): %s",
                attempt, max_retries, statuses,
            )
            time.sleep(retry_interval)
        else:
            pytest.fail(f"Reused containers NOT healthy after {max_retries * retry_interval}s")
        yield
        # No teardown — containers belong to platform_services, not us
        return
```

### Обоснование

- Контейнеры уже запущены `platform_services` — нет смысла запускать дубликат
- Healthcheck poll гарантирует, что тесты не начнутся до готовности контейнеров
- Teardown не выполняется — контейнеры удалит `platform_services` при завершении сессии
- Экономия времени: ~30 сек compose up не выполняется

---

## 5. Implementation Phases

### Wave 1: Простые фиксы (независимые, можно параллельно)

| File | Change | LOC |
|------|--------|-----|
| `tests/_conftest/smoke.py` | +1 строка: API_SERVER_KEY в SMOKE_ENV | +1 |
| `tests/test_smoke_hermes.py` | Удалить module-level константу + читать в теле теста | +2/-2 |
| `tests/test_smoke_nginx.py` | Убрать 2 `@skipif(darwin)` декоратора | -8 |

### Wave 2: Средние фиксы

| File | Change | LOC |
|------|--------|-----|
| `tests/test_platform_endpoints.py` | Заменить skip на fail с диагностикой | ~30 |

### Wave 3: Сложный фикс

| File | Change | LOC |
|------|--------|-----|
| `tests/test_smoke_postgres.py` | _REUSE_CONTAINERS флаг + пропуск compose lifecycle | ~40 |

**Итого:** ~80 LOC, все изменения в `tests/`, без изменений production-кода.

---

## 6. Verification

```bash
# После реализации:
make test MARKER=smoke  # все 6 ранее skip'нутых тестов выполняются
ruff check tests/       # 0 errors
```

---

## 7. Rollback Plan

Все изменения — в тестовых файлах, не затрагивают production-код:

- **Langfuse:** `git revert` — вернуть `pytest.skip`
- **Hermes:** `git revert` — вернуть module-level константу
- **macOS:** `git revert` — вернуть `@skipif(darwin)`
- **Postgres:** `git revert` — вернуть `pytest.skip` в `_docker_guard`

---

## 8. Decision Register

| TRAP ID | Date | Severity | Decision |
|---------|------|----------|----------|
| TRAP[DECISION] · 2026-07-22 · MED · Langfuse skip → fail |
| · Rejected: оставить skip (скрывает проблему) |
| · Rejected: увеличить таймаут _check_port_forwarded (симптоматическое лечение) |
| · Reason: skip с сообщением «isolated» противоречит compose-конфигурации (порт проброшен). Fail с диагностикой даёт честную картину. |
| · Rev: если langfuse стабильно не стартует в CI — увеличить compose --wait-timeout или добавить depends_on healthcheck |
| TRAP[DECISION] · 2026-07-22 · LOW · Hermes API_SERVER_KEY — module-level → test body |
| · Reason: Стандартный паттерн для всех чувствительных к import-time переменных. _hermes_credentials() уже использует этот подход. |
| TRAP[DECISION] · 2026-07-22 · LOW · macOS skipif removal |
| · Reason: generate-dev-certs.sh поддерживает macOS через openssl fallback. Docker Desktop ≥4.x поддерживает bind-mount. Если тесты упадут — получим реальный баг-репорт вместо молчаливого skip. |
| TRAP[DECISION] · 2026-07-22 · MED · Postgres container reuse |
| · Rejected: запускать второй postgres (избыточно, конфликт портов) |
| · Rejected: менять порты smoke-postgres (усложнение, не решает проблему) |
| · Reason: Переиспользование уже работающих контейнеров от platform_services — zero-cost решение. Экономит ~30 сек compose up и избегает конфликта имён/портов. |
| · Rev: если platform_services не запускает postgres в каком-то сценарии → fallback на обычный compose up |
