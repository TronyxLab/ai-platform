$START_BRIEF

$ARTIFACT_CONTRACT
PURPOSE:               Диагностика двух ошибок, выявленных при `make node-update NODE=tronyx-vps` 2026-07-24
DESCRIPTION:           P1 — pydantic/provision_llm_keys: pip3 отсутствует на VPS, pydantic не установлен.
                       P2 — verify-domains.sh статус-чекает не тот URL для status-page /health.
RATIONALE:             P1 блокирует provisioning LLM-ключей при каждом node-update.
                       P2 даёт ложный FAIL в verify-шаге (status-page работает, но проверяется не тот endpoint).
ACCEPTANCE_CRITERIA:   AC1: `make node-update NODE=tronyx-vps` → step provision_llm_keys не падает с ModuleNotFoundError
                       AC2: `make node-update NODE=tronyx-vps` → verify-domains.sh выводит "Status-page health check PASSED" (HTTP 200)
IMPLEMENTS:            Диагностика по результатам `make node-update NODE=tronyx-vps` (сессия 2026-07-24T19:56)
IMPACTS:               core/internal/llm/*, core/internal/bootstrap/node-lifecycle.sh, core/internal/verify/verify-domains.sh
REQUIRES:              SSH-доступ к VPS (103.88.243.151), права root
$END_ARTIFACT_CONTRACT

$START_DOCUMENT_PLAN
### Document Plan
**SECTION_GOALS:**
- GOAL Зафиксировать корневую причину P1 (pydantic) и P2 (status-page /health) => GOAL_DIAGNOSIS
- GOAL Предложить фикс для каждой проблемы => GOAL_FIX_PROPOSAL
**SECTION_USE_CASES:**
- USE_CASE Разработчик запускает make node-update → SCENARIO_NODE_UPDATE
- USE_CASE CI core-deploy вызывает make node-update для перезапуска контейнеров → SCENARIO_CI
$END_DOCUMENT_PLAN

# Brief: Ошибки `make node-update NODE=tronyx-vps` — 2026-07-24

**Источник:** `make node-update NODE=tronyx-vps` — запуск с локальной машины разработчика (macOS)

**Конфигурация VPS:**
- OS: Ubuntu Noble (24.04)
- Python: 3.12.3
- pip3: **НЕ УСТАНОВЛЕН** (`python3 -m pip --version` → "No module named pip")

---

## P1: `ModuleNotFoundError: No module named 'pydantic'` — provision_llm_keys

### Симптом
```
File "/opt/platform/core/internal/llm/policy_schema.py", line 26, in <module>
    from pydantic import BaseModel, Field, field_validator
ModuleNotFoundError: No module named 'pydantic'
```

Ошибка возникает в двух точках:
1. `state_machine.py:1196-1213` (step `provision_llm_keys`) — вызывает `python3 config_renderer.py`
2. `context_deployer.py:594-616` — вызывает `bash provision-llm.sh` → `python3 key_provisioner.py`

Оба пути ведут к `from pydantic import BaseModel` через цепочку импортов:
```
config_renderer.py:46  → from core.internal.llm.policy_schema import ...
key_provisioner.py:44  → from core.internal.llm.policy_schema import ...
                          → policy_schema.py:26: from pydantic import BaseModel
```

Сообщение об ошибке скрыто state machine — step завершается как DONE (fail-soft), но фактически LLM-ключи не провиженятся.

### Корневая причина
1. `pydantic>=2.0.0` объявлен в `pyproject.toml:33` (`[project] dependencies`)
2. `pyproject.toml` — dev-time манифест, **не используется на VPS**
3. `pip3` **не установлен** на VPS: `python3 -m pip` → "No module named pip"
4. Ни один шаг bootstrap-пайплайна не устанавливает Python-зависимости:
   - `node-lifecycle.sh --mode init` — нет pip install
   - `provision-llm.sh:22` — только устанавливает `PYTHONPATH`, но не pip
   - `state_machine.py` step `provision_llm_keys` — не устанавливает зависимости

### Trace вызова
```
make node-update NODE=tronyx-vps
→ core/entrypoints/node-update.sh
→ SSH execute_remote_update
→ node-lifecycle.sh --mode update
→ state_machine.py step "provision_llm_keys" (line 1196)
  → python3 config_renderer.py (line 1202)  ← ⚡ падает здесь
  OR bash provision-llm.sh (line 1209)       ← ⚡ или здесь
→ state_machine продолжает (fail-soft, step marked DONE)
```

### Статус
- **Состояние:** step `provision_llm_keys` завершается внешне успешно (exit 0 из state machine), но фактические LLM-ключи не генерируются
- **Побочные эффекты:** litellm-config.yml не обновляется, virtual keys не провиженятся
- **Severity:** MEDIUM — не блокирует node-update (fail-soft), но LLM-функциональность деградирует

---

## P2: verify-domains.sh status-page health check → HTTP 500

### Симптом
```
[IMP:7][verify][status-page] Checking status-page /health on https://tronyx.ru/health
[IMP:9][verify][status-page] Status-page health check FAILED (HTTP 500)
```

Но все домены при этом OK (HTTP 200), status-page контейнер работает (логи показывают `/healthz` 200, `/` 200).

### Корневая причина — URL mismatch

1. **verify-domains.sh:200-206** чекает `https://${PLATFORM_DOMAIN}/health` → `https://tronyx.ru/health`
2. **nginx overlay `tronyx.ru.conf`** имеет `location /health` → `proxy_pass $upstream_tronyx_site/health` — роутит в **tronyx-site проект**, не в status-page
3. **tronyx-site проект** не имеет обработчика `/health` с Basic Auth → возвращает 500
4. **Правильный URL:** status-page обслуживается отдельным nginx vhost `platform-vhost.conf` на поддомене `platform.tronyx.ru/health`

Трассировка запроса:
```
curl -u email:pass https://tronyx.ru/health
→ nginx (tronyx.ru vhost, overlay tronyx.ru.conf)
→ location /health → proxy_pass $upstream_tronyx_site/health
→ tronyx-site:80/health
→ ⚡ tronyx-site возвращает 500 (нет обработчика /health с Basic Auth либо проект не обрабатывает /health)
```

Правильный запрос должен быть:
```
curl -u email:pass https://platform.tronyx.ru/health
→ nginx (platform-vhost.conf, server_name platform.tronyx.ru)
→ location /health → proxy_pass http://status-page:8080
→ status-page _handle_health() → 200 PASS / 503 FAIL
```

### Подтверждение
- Status-page логи НЕ показывают запросов `/health` — значит запрос не доходит до контейнера
- nginx access log для platform.tronyx.ru показывает успешные запросы (HTTP 200)
- Домен `platform.tronyx.ru/health` при прямом обращении возвращает корректный ответ от status-page

### Статус
- **Состояние:** verify-шаг ложно-отрицательный — status-page исправен, но проверяется не тот URL
- **BLOCKER для CI:** нет (verify step fail не блокирует node-update)
- **Severity:** LOW — косметический false negative

---

## Приоритеты

| ID | Severity | Проблема | Impact |
|----|----------|----------|--------|
| P1 | MEDIUM | pip3/pydantic отсутствует на VPS | LLM-ключи не провиженятся при node-update |
| P2 | LOW | verify-domains.sh чекает не тот URL | False negative в verify-шаге |

$END_BRIEF
