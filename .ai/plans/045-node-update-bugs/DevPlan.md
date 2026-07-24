$START_DEVPLAN

$ARTIFACT_CONTRACT
PURPOSE:               Исправить 3 ошибки node-update (healthcheck, deploy_context, converge warnings)
DESCRIPTION:           P0: замена bash-функции на subprocess-вызов modules-healthcheck.sh. P1: добавление traceback.format_exc() + диагностика __dict__ бага. P2: ручная очистка /etc/hosts + render-vhosts.
RATIONALE:             Без healthcheck-верификации node-update не гарантирует работоспособность ноды после обновления. Без deploy_context контекстные проекты не деплоятся. Converge warnings — косметика, мешает CI.
ACCEPTANCE_CRITERIA:   (1) `make healthcheck NODE=tronyx-vps` проходит для всех 14 модулей. (2) deploy_context — полный traceback записан, root cause определён, fix применён. (3) `make converge NODE=tronyx-vps` — exit code 0, без warnings. (4) `make gate MODE=fast` зелёный.
IMPLEMENTS:            Brief:.ai/plans/045-node-update-bugs/Brief.md — диагностика трёх ошибок
IMPACTS:               state_machine.py:1804 (healthcheck fix), state_machine.py:1929-2009 (deploy_context diagnostics), reconciler.py:987-1190 (no changes — P2 manual)
REQUIRES:              Доступ к VPS (SSH root@103.88.243.151), Python >= 3.10 на VPS, успешный `make fix-gate && make gate MODE=fast` локально перед push
$END_ARTIFACT_CONTRACT

$DOCUMENT_PLAN
### Document Plan
**SECTION_GOALS:**
- GOAL P0: Исправить _run_healthchecks() → GOAL_FIX_HC
- GOAL P1: Добавить traceback в _step_deploy_context_inline() → GOAL_DIAG_DC
- GOAL P1: Определить root cause __dict__ → GOAL_FIX_DC
- GOAL P2: Ручная очистка converge warnings → GOAL_FIX_CV
- GOAL VERIFY: Все три исправления верифицированы на VPS → GOAL_VERIFY
**SECTION_USE_CASES:**
- USE_CASE Разработчик запускает make node-update после деплоя core → SCENARIO_NODE_UPDATE
- USE_CASE CI core-deploy вызывает make node-update для перезапуска контейнеров → SCENARIO_CI
$END_DOCUMENT_PLAN

---

# DevPlan: Исправление ошибок `make node-update`

**План #:** 045
**Дата:** 2026-07-24
**Статус:** В разработке

---

## Draft Code Graph

```xml
<graph>
  <!-- P0: Healthcheck fix -->
  <entity id="state_machine_run_healthchecks_FUNC" type="FUNCTION" file="core/internal/bootstrap/lifecycle/state_machine.py" line="1780">
    <keyword>healthcheck</keyword>
    <keyword>bug-P0</keyword>
    <annotation>Заменяет subprocess.run(["invoke_module_interface", ...]) на bash-вызов modules-healthcheck.sh</annotation>
    <CrossLinks>
      <link target="modules_healthcheck_sh_SCRIPT" relation="calls"/>
      <link target="docker_orchestrator_invoke_hc_FUNC" relation="canonical-pattern"/>
    </CrossLinks>
  </entity>

  <entity id="modules_healthcheck_sh_SCRIPT" type="SCRIPT" file="core/internal/healthcheck/modules-healthcheck.sh" line="1">
    <keyword>healthcheck</keyword>
    <keyword>orchestration</keyword>
    <annotation>Канонический healthcheck-оркестратор: docker inspect + healthcheck.sh liveness</annotation>
    <CrossLinks>
      <link target="state_machine_run_healthchecks_FUNC" relation="called-by"/>
    </CrossLinks>
  </entity>

  <entity id="docker_orchestrator_invoke_hc_FUNC" type="FUNCTION" file="core/internal/bootstrap/deploy/docker_orchestrator.py" line="1168">
    <keyword>healthcheck</keyword>
    <keyword>canonical-pattern</keyword>
    <annotation>Правильный паттерн: bash -c "source paths.sh && source module-interface.sh && invoke_module_interface"</annotation>
  </entity>

  <!-- P1: deploy_context __dict__ diagnostics -->
  <entity id="state_machine_deploy_context_inline_FUNC" type="FUNCTION" file="core/internal/bootstrap/lifecycle/state_machine.py" line="1930">
    <keyword>deploy_context</keyword>
    <keyword>bug-P1</keyword>
    <annotation>Добавлен import traceback + format_exc() в оба except-блока</annotation>
    <CrossLinks>
      <link target="cert_orchestrator_DomainCertResult_CLASS" relation="calls-to_dict"/>
      <link target="context_deployer_deploy_context_projects_FUNC" relation="calls"/>
    </CrossLinks>
  </entity>

  <entity id="cert_orchestrator_DomainCertResult_CLASS" type="DATACLASS" file="core/internal/bootstrap/cert_orchestrator.py" line="45">
    <keyword>dataclass</keyword>
    <keyword>asdict</keyword>
    <annotation>to_dict() вызывает asdict(self) — источник __dict__ ошибки при не-dataclass полях</annotation>
  </entity>

  <entity id="context_deployer_deploy_context_projects_FUNC" type="FUNCTION" file="core/internal/bootstrap/deploy/context_deployer.py" line="260">
    <keyword>deploy_context</keyword>
    <keyword>importlib</keyword>
    <annotation>Загружается через importlib.util на VPS — возможный источник __dict__ из-за окружения</annotation>
  </entity>

  <!-- P2: Converge warnings — manual fix only -->
  <entity id="reconciler_detect_hosts_drift_FUNC" type="FUNCTION" file="core/internal/bootstrap/converge/reconciler.py" line="1000">
    <keyword>converge</keyword>
    <keyword>P2</keyword>
    <annotation>Read-only детектор — код не меняется. Ручная очистка /etc/hosts.</annotation>
  </entity>

  <entity id="reconciler_verify_vhosts_FUNC" type="FUNCTION" file="core/internal/bootstrap/converge/reconciler.py" line="1100">
    <keyword>converge</keyword>
    <keyword>P2</keyword>
    <annotation>Read-only верификатор — код не меняется. Ручной render-vhosts.</annotation>
  </entity>

  <!-- Gate test -->
  <entity id="test_core_deploy_auto_detects_node_FUNC" type="TEST" file="tests/gates/test_gate_workflow_consistency.py" line="318">
    <keyword>gate</keyword>
    <keyword>node-update</keyword>
    <annotation>Проверяет авто-детект NODE в CI. Не требует изменений для P0/P1/P2.</annotation>
  </entity>
</graph>
```

---

## Step-by-Step Data Flow

### P0: Healthcheck fix

```
┌─ state_machine.py:_run_healthchecks() ─┐
│  Текущий код:                            │
│  subprocess.run(["invoke_module_interface", mod, "healthcheck", "liveness"]) │
│  → FileNotFoundError (bash-функция)      │
│  → except: pass → healthcheck FAILED     │
└─────────────────────────────────────────┘
                    │
                    ▼ FIX
┌─ state_machine.py:_run_healthchecks() ─┐
│  Новый код:                              │
│  hc_script = os.path.join(core_dir,      │
│    "internal", "healthcheck",            │
│    "modules-healthcheck.sh")             │
│  subprocess.run(["bash", hc_script],     │
│    capture_output=True, text=True,       │
│    timeout=120)                          │
└─────────────────────────────────────────┘
                    │
                    ▼
┌─ modules-healthcheck.sh ───────────────┐
│  1. Итерирует core/modules/*/module.yaml│
│  2. Для docker: docker inspect health   │
│  3. Для system: healthcheck.sh liveness │
│  4. exit 0 если все healthy             │
│  5. exit 1 если есть unhealthy          │
└─────────────────────────────────────────┘
```

### P1: deploy_context diagnostics → fix

```
┌─ state_machine.py:1929 ────────────────┐
│  _step_deploy_context_inline()           │
│                                          │
│  try:                                    │
│    importlib.util.spec_from_file_location(...) │
│    cert_mod.orchestrate_certs(...)       │
│    cert_result.to_dict()  ← __dict__     │
│  except Exception as e:                  │
│    logger.warning("...: %s", e)  ← было │
│                                          │
│  FIX:                                    │
│  import traceback                        │
│  except Exception as e:                  │
│    tb = traceback.format_exc()           │
│    logger.warning(                       │
│      "Cert orchestration failed: %s\n%s",│
│      e, tb)                              │
└─────────────────────────────────────────┘
                    │
                    ▼ после node-update с traceback
┌─ Анализ traceback ─────────────────────┐
│  Возможные причины:                      │
│  1. importlib-загруженный модуль имеет   │
│     поле не-dataclass без __dict__       │
│  2. Python < 3.10 на VPS — нет           │
│     dataclasses.asdict для mixed types   │
│  3. Конфликт PYTHONPATH на VPS           │
│                                          │
│  После анализа → точечный фикс           │
└─────────────────────────────────────────┘
```

### P2: Converge warnings — manual

```
┌─ VPS: ручная очистка ─────────────────┐
│  ssh tronyx-vps "sudo sed -i            │
│    '/botanika/d' /etc/hosts"            │
│                                          │
│  make render-vhosts NODE=tronyx-vps     │
│  → регенерирует vhosts с маркером       │
│    # GENERATED by add-vhost.sh          │
└─────────────────────────────────────────┘
```

---

## File Manifest

| # | Файл | Действие | Изменение |
|---|------|----------|-----------|
| 1 | `core/internal/bootstrap/lifecycle/state_machine.py` | **Изменить** | P0: заменить вызов healthcheck в `_run_healthchecks()` (строка 1804) — убрать цикл по модулям с `invoke_module_interface`, вместо этого вызвать `modules-healthcheck.sh` один раз. P1: добавить `import traceback` и `traceback.format_exc()` в except-блоки `_step_deploy_context_inline()` (строки 1967, 1985). |
| 2 | `tests/unit/test_state_machine.py` | **Изменить** | Unit-тест для `_run_healthchecks()` с mock `subprocess.run` (патч на уровне модуля, не объекта), проверяющий вызов `bash modules-healthcheck.sh` с аргументом `core_dir`. Gate-тест на `make node-update` workflow — без изменений. |
| 3 | ~~`core/internal/bootstrap/converge/reconciler.py`~~ | **Без изменений** | P2 решается ручной очисткой на VPS, код не меняется. |
| 4 | ~~`core/internal/bootstrap/cert_orchestrator.py`~~ | **Без изменений (pending diagnostic)** | `to_dict()` → `asdict(self)` — фикс будет определён после получения traceback с VPS. |
| 5 | ~~`core/internal/bootstrap/deploy/context_deployer.py`~~ | **Без изменений (pending diagnostic)** | Ошибка внутри `deploy_context_projects()` — фикс будет определён после получения traceback. |
| 6 | `core/internal/bootstrap/lifecycle/steps.py` | **Без изменений** | Каноническая версия `_step_deploy_context()` уже корректна, но не используется из-за `ImportError` standalone-запуска. Этот архитектурный долг исправляется отдельно (DevPlan 047). |

---

## Implementation Plan: P0 (Healthcheck)

### Изменяемый файл: `core/internal/bootstrap/lifecycle/state_machine.py`

**⚠️ CRITICAL: Изменение сигнатуры функции и call sites**

Текущая сигнатура: `def _run_healthchecks(node_yaml: str) -> None:` (строка 1753) — параметр `core_dir` отсутствует.
Вызов (строка 1184 в `_execute_update_step`): `_run_healthchecks(node_yaml)` — `core_dir` **доступен** в `_execute_update_step(core_dir, ...)`, но не передаётся.

**Необходимые изменения:**

1. **Изменить сигнатуру** (строка 1753):
   ```python
   # Было:
   def _run_healthchecks(node_yaml: str) -> None:
   # Стало:
   def _run_healthchecks(core_dir: str, node_yaml: str) -> None:
   ```

2. **Изменить вызов** (строка 1184 в `_execute_update_step`):
   ```python
   # Было:
   _run_healthchecks(node_yaml)
   # Стало:
   _run_healthchecks(core_dir, node_yaml)
   ```

3. **Изменить docstring** (строка 1756-1758):
   ```python
   # Было:
   ## @io — ⇥ node_yaml → ⎋ None (non-fatal)
   # Стало:
   ## @io — ⇥ core_dir: str, node_yaml → ⎋ None (non-fatal)
   ```

Без этих изменений код упадёт с `NameError: name 'core_dir' is not defined`.

**Текущий код** (строки ~1800-1830) — цикл по модулям с попыткой вызвать bash-функцию:

```python
for mod_name, mod_value in module_items:
    if not mod_name:
        continue
    if isinstance(mod_value, dict):
        enabled = str(mod_value.get("enabled", True)).lower()
    else:
        enabled = str(mod_value).lower()
    if enabled != "true":
        continue

    passed = False
    for attempt in range(1, hc_max_retries + 1):
        try:
            hc_result = subprocess.run(
                ["invoke_module_interface", mod_name, "healthcheck", "liveness"],
                capture_output=True, text=True, timeout=30,
            )
            if hc_result.returncode == 0:
                logger.info("[IMP:9][healthcheck:%s] Healthcheck PASS ...")
                passed = True
                break
        except (subprocess.TimeoutExpired, FileNotFoundError):
            pass

    if not passed:
        logger.warning("[IMP:7][healthcheck:%s] Healthcheck FAILED ...")
        hc_fail += 1
```

**Новый код** — вызов `modules-healthcheck.sh` один раз, вместо цикла по модулям:

```python
# region FUNC__run_healthchecks (fix P0)
## @purpose  Run all module healthchecks via modules-healthcheck.sh.
##           Fixes P0 bug: was calling bash function `invoke_module_interface`
##           as executable (FileNotFoundError silently caught).
## @io       stdout/stderr: healthcheck LDD logs, exit code from script
hc_script = os.path.join(core_dir, "internal", "healthcheck", "modules-healthcheck.sh")
if not os.path.isfile(hc_script):
    logger.warning("[IMP:7][healthcheck] modules-healthcheck.sh not found at %s", hc_script)
    hc_fail += 1
else:
    try:
        hc_result = subprocess.run(
            ["bash", hc_script],
            capture_output=True, text=True, timeout=120,
        )
        if hc_result.stdout:
            logger.info("[IMP:7][healthcheck] %s", hc_result.stdout.strip())
        if hc_result.stderr:
            logger.info("[IMP:7][healthcheck] %s", hc_result.stderr.strip())
        if hc_result.returncode != 0:
            logger.warning("[IMP:7][healthcheck] modules-healthcheck.sh exit=%d", hc_result.returncode)
            hc_fail += 1
        else:
            logger.info("[IMP:9][healthcheck] All modules healthy")
    except subprocess.TimeoutExpired:
        logger.error("[IMP:10][healthcheck] modules-healthcheck.sh timed out after 120s")
        hc_fail += 1
    except FileNotFoundError:
        logger.error("[IMP:10][healthcheck] bash not found — platform integrity error")
        hc_fail += 1
# endregion FUNC__run_healthchecks
```

**Важно:** `modules-healthcheck.sh` использует `invoke_module_interface` (bash-функцию из `module-interface.sh`), которая подключается через `source "${_HEALTHCHECK_LIB_DIR}/paths.sh"` на строке 23. Скрипт самодостаточен — source библиотек внутри скрипта достаточно.

**Trade-off:** Вариант A (целый скрипт) проще и надёжнее, чем Вариант B (source + invoke_module_interface для каждого модуля в цикле Python). `modules-healthcheck.sh` уже содержит всю retry-логику, docker inspect и restart-loop detection.

---

### ⚠️ Semantic Change: `node.yaml` vs filesystem — задокументированное различие

**Это решение меняет семантику healthcheck-шага.** Текущий код итерирует модули из `node.yaml`, фильтрует по `enabled`, делает retry с интервалом. Новый код вызывает `modules-healthcheck.sh`, который сканирует `core/modules/*/module.yaml` (файловую систему).

| Аспект | Текущий код `_run_healthchecks()` | Новый код (через `modules-healthcheck.sh`) |
|--------|-----------------------------------|-------------------------------------------|
| Источник модулей | `node.yaml` → `modules` (dict/list) | `core/modules/*/module.yaml` (файловая система) |
| Фильтрация `enabled` | Да — `enabled != "true"` → skip | Нет |
| Кастомные модули (node.yaml без module.yaml) | Да — обрабатываются | Нет — пропускаются |
| Retry-логика | 4 попытки, интервал 3s (в Python) | **Удалена** — полагаемся на Docker HEALTHCHECK как единственный механизм перепроверки. `modules-healthcheck.sh` делает однократный проход: если модуль в состоянии `starting` — WARN, но не перепроверяется. |
| Логирование | Per-module `[IMP:9][healthcheck:<name>]` | Агрегированное `[IMP:7][healthcheck]` из stdout скрипта |

**Практическая оценка:** На `tronyx-vps` набор модулей в `node.yaml` 1:1 с `core/modules/`. Риск расхождения — теоретический для текущей конфигурации, но должен быть задокументирован. Если в будущем появится модуль, зарегистрированный только в `node.yaml` (без `core/modules/<name>/module.yaml`), его healthcheck будет пропущен.

**Дизайн-решение:** Принято осознанно — `modules-healthcheck.sh` является каноническим оркестратором (используется в `make healthcheck`), и унификация healthcheck-пути через единый скрипт приоритетнее, чем сохранение `node.yaml`-итерации. Docker HEALTHCHECK (`docker inspect State.Health.Status`) покрывает сценарий «starting» лучше, чем фиксированный retry-loop.

---

## Implementation Plan: P1 (deploy_context diagnostics)

### Изменяемый файл: `core/internal/bootstrap/lifecycle/state_machine.py`

**Шаг 1: Добавить traceback в except-блоки**

Добавить `import traceback` в начало файла (если ещё нет) и заменить оба except-блока:

```python
# Было (строка 1967):
except Exception as e:
    logger.warning("[IMP:7][deploy_context] Cert orchestration failed (non-fatal): %s", e)

# Стало:
except Exception as e:
    tb = traceback.format_exc()
    logger.warning("[IMP:7][deploy_context] Cert orchestration failed (non-fatal): %s\n%s", e, tb)
```

Аналогично для project deploy (строка 1985):

```python
# Было:
except Exception as e:
    logger.warning("[IMP:7][deploy_context] Project deploy failed (non-fatal): %s", e)

# Стало:
except Exception as e:
    tb = traceback.format_exc()
    logger.warning("[IMP:7][deploy_context] Project deploy failed (non-fatal): %s\n%s", e, tb)
```

**Шаг 2: Развернуть на VPS и получить traceback**

```bash
make converge NODE=tronyx-vps && make node-update NODE=tronyx-vps
# Проанализировать логи, найти traceback для обеих ошибок
```

**Шаг 3: Анализ traceback → точечный фикс**

После получения полного traceback:
- Если ошибка в `dataclasses.asdict()` → добавить защиту: `try: d = asdict(self); except TypeError: d = {"domain": self.domain, ...}`
- Если ошибка в `importlib.util.exec_module` → проверить версию Python на VPS, возможно, перейти на `runpy.run_path()` 
- Если ошибка в версии Python < 3.10 → обновить Python на VPS или добавить compatibility shim

⚠️ TRAP[DECISION] · 2026-07-24 · P1 · Deploy_context fix будет уточнён после traceback-диагностики
· Текущий код: `try/except Exception as e: logger.warning("...: %s", e)` — маскирует root cause.
· Шаг 1 этого DevPlan добавляет traceback.format_exc(). После получения полного traceback — Шаг 3 с точечным фиксом.
· Rev: после Шага 2 — дополнить DevPlan секцией P1-FIX с конкретным кодом.

---

## Implementation Plan: P2 (Converge warnings — manual)

**Без изменений кода.** Выполнить на VPS:

```bash
# Удалить stale /etc/hosts запись
ssh tronyx-vps "sudo sed -i '/botanika/d' /etc/hosts"

# Регенерировать vhost-конфиги с маркером GENERATED
make render-vhosts NODE=tronyx-vps
```

---

## Unit Test Plan

### Новый тест: `tests/unit/test_state_machine.py` (добавить в существующий файл)

Следуя конвенции `tests/AGENTS.md` — unit-тесты Python-модулей без Docker размещаются в `tests/unit/`.

```python
# region TEST__run_healthchecks_calls_modules_healthcheck_sh
## @purpose  Verify P0 fix: _run_healthchecks() calls modules-healthcheck.sh,
##           not invoke_module_interface bash function directly.
## ⚠️ TRAP[MOCK] · mock.patch("subprocess.run") — patch namespace BEFORE import,
##    иначе mock.patch.object(sp_mod, "run") пачит subprocess.run в тестовом модуле,
##    а не в state_machine.py (где реально вызывается subprocess.run).
def test_run_healthchecks_calls_modules_healthcheck_sh(tmp_path):
    """_run_healthchecks must invoke bash modules-healthcheck.sh (not invoke_module_interface)."""
    from unittest import mock

    # Import must be inside the patch context for the mock to take effect
    with mock.patch("subprocess.run") as mock_run:
        mock_run.return_value = mock.MagicMock(
            returncode=0, stdout="ALL MODULES HEALTHY", stderr=""
        )

        # Simulate core_dir with modules-healthcheck.sh present
        core_dir = str(tmp_path)
        hc_dir = tmp_path / "internal" / "healthcheck"
        hc_dir.mkdir(parents=True)
        (hc_dir / "modules-healthcheck.sh").write_text("#!/bin/bash\necho ALL MODULES HEALTHY\n")

        from core.internal.bootstrap.lifecycle.state_machine import _run_healthchecks
        result = _run_healthchecks(core_dir, node_yaml=str(tmp_path / "node.yaml"))

        # Verify bash was called with modules-healthcheck.sh
        assert mock_run.called
        call_args = mock_run.call_args[0][0]
        assert call_args[0] == "bash"
        assert "modules-healthcheck.sh" in call_args[1]
# endregion TEST__run_healthchecks_calls_modules_healthcheck_sh
```

### Обновление gate-теста

Файл `tests/gates/test_gate_workflow_consistency.py` — существующий тест `test_core_deploy_auto_detects_node` (строка 318) уже проверяет авто-детект NODE. Для P0 добавляем проверку, что CI workflow вызывает `make node-update` (не `make healthcheck` напрямую).

---

## Acceptance Criteria

| # | Критерий | Проверка | Приоритет |
|---|----------|----------|-----------|
| AC1 | `make healthcheck NODE=tronyx-vps` возвращает 0 | SSH на VPS → `make healthcheck NODE=tronyx-vps` | P0 |
| AC2 | Все 14 модулей проходят healthcheck | Логи содержат `ALL MODULES HEALTHY` | P0 |
| AC3 | `_run_healthchecks()` вызывает `modules-healthcheck.sh` (не `invoke_module_interface`) | Unit-тест `test_run_healthchecks_calls_modules_healthcheck_sh` | P0 |
| AC4 | deploy_context except-блоки содержат `traceback.format_exc()` | Код-ревью state_machine.py:1967, 1985 | P1 |
| AC5 | Полный traceback для обеих ошибок deploy_context получен | Логи после `make node-update NODE=tronyx-vps` | P1 |
| AC6 | Root cause __dict__ определён и исправлен | DevPlan-045 дополнен секцией P1-FIX | P1 |
| AC7 | `make converge NODE=tronyx-vps` — exit code 0, без R5/R6 warnings | SSH на VPS → `make converge NODE=tronyx-vps` | P2 |
| AC8 | Vhost-конфиги содержат маркер `GENERATED` | grep на VPS: `/opt/nginx/overlay/*.conf` | P2 |
| AC9 | `make gate MODE=fast` зелёный локально | `make fix-gate && make gate MODE=fast` | ALL |
| AC10 | `make node-update NODE=tronyx-vps` успешен (exit 0) без ошибок | Полный цикл node-update после всех фиксов | ALL |

---

## Rollback Plan

В случае регрессии:

1. **P0:** `git revert <commit>` — восстанавливает вызов `invoke_module_interface`. Healthcheck продолжит молча падать (как сейчас), но pipeline не сломается.
2. **P1:** Traceback-изменения безопасны — только добавляют логирование, не меняют поведение. Revert не требуется.
3. **P2:** Ручные операции на VPS — обратимы: `sudo cp /etc/hosts.bak /etc/hosts`, `git checkout` старых vhost-конфигов.

---

## Связанные артефакты

| Файл | Роль |
|------|------|
| `.ai/plans/045-node-update-bugs/Brief.md` | Диагностический бриф — источник требований |
| `core/internal/bootstrap/AGENTS.md` | Bootstrap pipeline описание |
| `core/AGENTS.md` | Каталог операций (node-update target) |
| `core/entrypoints/node-update.sh` | Entrypoint → делегирует в node-lifecycle.sh --mode update |

$END_DEVPLAN
