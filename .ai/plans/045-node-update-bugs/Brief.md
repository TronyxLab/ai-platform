$START_BRIEF

$ARTIFACT_CONTRACT
PURPOSE:               Диагностика трёх ошибок, выявленных при `make node-update NODE=tronyx-vps` 2026-07-24
DESCRIPTION:           Анализ 14 проваленных healthcheck, ошибки `__dict__` в deploy_context, и converge warnings
RATIONALE:             Без исправлений node-update не обеспечивает healthcheck-верификацию и не деплоит проекты контекста
ACCEPTANCE_CRITERIA:   Корректный root cause для каждой ошибки, приоритеты, предлагаемые исправления
IMPLEMENTS:            Диагностический бриф → DevPlan → исправления
IMPACTS:               core/internal/bootstrap/lifecycle/state_machine.py:1804, state_machine.py:1929-2009, steps.py:828-885
REQUIRES:              Доступ к VPS для верификации (SSH root@103.88.243.151), Python >= 3.10 на VPS
$END_ARTIFACT_CONTRACT

$DOCUMENT_PLAN
### Document Plan
**SECTION_GOALS:**
- GOAL Диагностика healthcheck-бага в state_machine.py → GOAL_HC_ROOT_CAUSE
- GOAL Диагностика __dict__ ошибки в deploy_context → GOAL_DC_ROOT_CAUSE
- GOAL Оценка converge warnings → GOAL_CV_SEVERITY
- GOAL Приоритизация исправлений → GOAL_PRIORITY
**SECTION_USE_CASES:**
- USE_CASE Разработчик запускает make node-update → SCENARIO_NODE_UPDATE
- USE_CASE CI запускает node-update после деплоя core → SCENARIO_CI_UPDATE
$END_DOCUMENT_PLAN

---

# Brief: Ошибки `make node-update` на tronyx-vps

**Дата:** 2026-07-24
**Источник:** `make node-update NODE=tronyx-vps` — запуск с локальной машины разработчика (macOS)

## Результат выполнения

Pipeline завершился с exit code 0, но с тремя проблемами:

| # | Проблема | Серьёзность | Шаг |
|---|----------|------------|-----|
| 1 | **14/14 healthchecks FAILED** — все модули не прошли проверку | 🔴 P0 BLOCKING | Step 6 (healthcheck) |
| 2 | **`'NoneType' object has no attribute '__dict__'`** в cert orchestration и project deploy | 🟠 P1 | Step 8 (deploy_context) |
| 3 | **Converge warnings** (stale /etc/hosts, legacy vhost markers) | 🟡 P2 COSMETIC | Step 7 (converge) |

---

## Проблема 1: Healthcheck — все 14 модулей FAILED

### Симптом

```
[IMP:7][healthcheck:nginx] Healthcheck FAILED after 4 attempts
[IMP:7][healthcheck:platform-secrets] Healthcheck FAILED after 4 attempts
... (все 14 модулей)
[IMP:7][healthcheck] 14 healthcheck(s) failed — node partially ready
```

### Root Cause

**Файл:** `core/internal/bootstrap/lifecycle/state_machine.py`, строка 1804

```python
hc_result = subprocess.run(
    ["invoke_module_interface", mod_name, "healthcheck", "liveness"],
    capture_output=True, text=True, timeout=30,
)
```

**Ошибка:** `invoke_module_interface` — это **bash-функция**, определённая в `core/lib/module-interface.sh`. Она НЕ является исполняемым файлом. `subprocess.run()` ищет исполняемый файл в `$PATH`, не находит → `FileNotFoundError`. Исключение ловится строкой 1818:

```python
except (subprocess.TimeoutExpired, FileNotFoundError):
    pass
```

Все 4 попытки для каждого из 14 модулей падают одинаково.

### Доказательство

**Правильный паттерн уже существует** в `core/internal/bootstrap/deploy/docker_orchestrator.py:1172-1177`:

```python
bash_cmd = (
    f"source '{_PATHS_SH}' && "
    f"source '{_INVOKE_MODULE_INTERFACE_SH}' && "
    f"invoke_module_interface '{module_name}' healthcheck '{check_type}'"
)
result = subprocess.run(["bash", "-c", bash_cmd], ...)
```

То есть `docker_orchestrator.py` (используется в W2-E1 docker-деплое) правильно вызывает `invoke_module_interface` через `bash -c` с предварительным `source` библиотек. Но `state_machine.py:_run_healthchecks()` (W5-E6 state machine) вызывает функцию напрямую — это баг, допущенный при миграции.

### Предлагаемое исправление

Заменить вызов в `_run_healthchecks()` на bash-совместимый. Два варианта:

**Вариант A (рекомендуемый):** Вызывать `modules-healthcheck.sh` напрямую:

```python
hc_script = os.path.join(core_dir, "internal", "healthcheck", "modules-healthcheck.sh")
hc_result = subprocess.run(
    ["bash", hc_script],
    capture_output=True, text=True, timeout=120,
)
```

`modules-healthcheck.sh` уже содержит логику обхода всех модулей, проверки Docker health status, retry-логику.

**Вариант B:** Повторить паттерн из `docker_orchestrator.py` — `bash -c "source paths.sh && source module-interface.sh && invoke_module_interface ..."`.

---

## Проблема 2: deploy_context — `'NoneType' object has no attribute '__dict__'`

### Симптом

```
[IMP:7][deploy_context] Cert orchestration failed (non-fatal): 'NoneType' object has no attribute '__dict__'
[IMP:7][deploy_context] Project deploy failed (non-fatal): 'NoneType' object has no attribute '__dict__'
```

### Диагностика

Выполняется **inline-версия** `_step_deploy_context_inline()` (state_machine.py:1929), а не `steps._step_deploy_context()` (steps.py:828). Это подтверждается форматом логов — `[IMP:9][deploy_context]` вместо `[IMP:9][step:deploy_context]`.

**Почему inline:** state_machine.py строка 48 — `from . import steps as _steps`. Относительный импорт падает (`ImportError`), потому что `state_machine.py` запускается как standalone-скрипт (`python3 state_machine.py`), а не как модуль пакета (`python3 -m lifecycle.state_machine`). `_steps = None` → используется fallback.

### Анализ ошибки cert orchestration

Inline-версия (строка 1963-1964):
```python
cert_result = cert_mod.orchestrate_certs(domains, s3_cache_script, issue_cert_script, secrets_env)
logger.info("[IMP:9][deploy_context] Cert orchestration complete: %s", cert_result.to_dict())
```

Вызов `to_dict()` → `DomainCertResult.to_dict()` (cert_orchestrator.py:61):
```python
d = asdict(self)
```

`dataclasses.asdict()` на `DomainCertResult` рекурсивно обходит поля. Ошибка `'NoneType' object has no attribute '__dict__'` возникает, когда `vars()` вызывается на объекте, не имеющем `__dict__`. Возможные причины:
- `asdict()` вызывает `_asdict_inner()` на поле, которое содержит объект без `__dict__` (не dataclass, не dict, не list/tuple)
- `copy.deepcopy()` на специфичном объекте в Python 3.10/3.11
- Версия Python на VPS имеет баг в `dataclasses`

### Анализ ошибки context_deployer

Inline-версия (строка 1981-1982):
```python
results = deployer_mod.deploy_context_projects(node_yaml, context) or []
logger.info("[IMP:9][deploy_context] Project deploy complete: %d projects", len(results))
```

Здесь `to_dict()` НЕ вызывается. Ошибка возникает **внутри** `deploy_context_projects()`, которая вызывает `resolve_context_projects()` (использует `yaml.safe_load()`), затем `_deploy_single_project()` (subprocess-вызовы docker).

То, что обе ошибки идентичны (`'NoneType' object has no attribute '__dict__'`), наводит на мысль о **системной причине**: возможно, проблема в `importlib` загрузке, окружении Python на VPS, или конфликте версий библиотек.

### Необходимая диагностика

Без полного traceback точная причина не может быть установлена. Необходимо:

1. **Добавить `traceback.format_exc()` в except-блоки** `_step_deploy_context_inline()` (строки 1967 и 1985) для получения полного стека вызовов
2. **Проверить версию Python на VPS:** `ssh tronyx-vps "python3 --version"`
3. **Проверить `importlib` корректность:** `ssh tronyx-vps "python3 -c 'import importlib.util; print(importlib.util.__file__)'"`
4. **Запустить deploy_context изолированно:** `make deploy-context NODE=tronyx-vps CONTEXT=tronyx-lab`

---

## Проблема 3: Converge warnings

### Симптомы

1. `[IMP:9][converge][R5] WARN: Stale /etc/hosts entry found for project 'botanika': ['botanika']`
2. Три vhost-конфига без маркера `GENERATED`:
   - `tronyx.ru.conf`
   - `sexydancerostov.ru.conf`
   - `botanika.tronyx.ru.conf`

### Root Cause

**R5 — detect_hosts_drift:** `reconciler.py:987-1060`. Read-only детектор: ищет имена проектов в `/etc/hosts`. Запись для `botanika` была добавлена вручную (вне платформы) и не была удалена. Платформа **не имеет механизма автоочистки** `/etc/hosts`.

**R6 — verify_vhosts:** `reconciler.py:1064-1186`. Три vhost-файла созданы до появления `add-vhost.sh` (legacy), поэтому не имеют маркера `# GENERATED by add-vhost.sh` на первой строке. Маркер важен для `make remove-project` — файлы без маркера не удаляются автоматически.

### Серьёзность

**LOW (exit code 1).** Converge-шаг выходит с кодом 1 (warnings), но это не блокирует pipeline — `make node-update` завершается успешно.

### Исправление

- **R5:** `ssh tronyx-vps "sudo sed -i '/botanika/d' /etc/hosts"` — ручная очистка
- **R6:** `make render-vhosts NODE=tronyx-vps` — регенерация vhost-конфигов с маркером

---

## Приоритеты исправлений

| Приоритет | Проблема | Действие | Блокирует |
|-----------|----------|----------|-----------|
| 🔴 P0 | Healthcheck не работает | Исправить `_run_healthchecks()` — вариант A (bash modules-healthcheck.sh) | Верификацию здоровья ноды после node-update |
| 🟠 P1 | deploy_context `__dict__` error | 1. Добавить traceback в логи 2. Повторить node-update 3. Проанализировать traceback → исправить | Деплой проектов контекста |
| 🟡 P2 | Converge warnings | Ручная очистка /etc/hosts + render-vhosts | Ничего (косметические) |

---

## План действий

### Phase 1: Диагностика P1 (сейчас)

1. Добавить `import traceback; traceback.format_exc()` в except-блоки `_step_deploy_context_inline()` в state_machine.py
2. Запустить `make node-update NODE=tronyx-vps` повторно
3. Проанализировать полный traceback для точного определения источника `__dict__` ошибки

### Phase 2: Исправление P0 + P1

На основе результатов Phase 1 — зафиксировать оба бага.

### Phase 3: Косметика P2

Выполнить ручную очистку `/etc/hosts` и `make render-vhosts`.

---

## Связанные файлы

| Файл | Роль |
|------|------|
| `core/internal/bootstrap/lifecycle/state_machine.py:1804` | Баг P0 — некорректный вызов healthcheck |
| `core/internal/bootstrap/lifecycle/state_machine.py:1929-2009` | Inline fallback deploy_context (P1) |
| `core/internal/bootstrap/lifecycle/steps.py:828-885` | Каноническая версия deploy_context (не используется из-за ImportError) |
| `core/internal/bootstrap/deploy/docker_orchestrator.py:1172-1177` | Правильный паттерн вызова invoke_module_interface |
| `core/internal/bootstrap/cert_orchestrator.py:59-64` | DomainCertResult.to_dict() → asdict() |
| `core/internal/bootstrap/deploy/context_deployer.py:260-290` | deploy_context_projects() |
| `core/internal/bootstrap/converge/reconciler.py:987-1060` | R5 detect_hosts_drift |
| `core/internal/bootstrap/converge/reconciler.py:1064-1186` | R6 verify_vhosts |
| `core/internal/healthcheck/modules-healthcheck.sh` | Правильный healthcheck (через docker inspect) |
| `node-configs/tronyx-vps/node.yaml` | Конфигурация ноды (context=tronyx-lab) |

$END_BRIEF
