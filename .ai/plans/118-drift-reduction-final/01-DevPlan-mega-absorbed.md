# 01-DevPlan — Волна 118: финальное снижение дрейфа (post-117)

<!-- $ARTIFACT_CONTRACT
PURPOSE:          Системное снижение остаточного дрейфа после волн 116/117; подготовка к ручному тестированию на тестовом сервере.
DESCRIPTION:      Единый мега-DevPlan на 20 задач (D1-D20), сгруппированных в 5 тематических блоков.
                  Цель: унификация и значительное снижение дрейфа БЕЗ нового функционала.
                  Включает 1 bugfix (A5 — SCPChannel), остальные задачи — чистое упрощение/удаление/консолидация.
RATIONALE:        Аудит 7 субагентов + точечная grep-верификация выявили 21 валидированную находку.
                  Часть находок субагентов ОПРОВЕРГНУТА (backup-cron .sh — тонкие фасады; __pycache__ в git — 0 файлов;
                  _unit_enabled — живой; deploy_compose — живой). В план включены только подтверждённые.
ACCEPTANCE_CRITERIA:
  - AC1: make gate MODE=fast зелёный после каждой волны
  - AC2: check-manifests зелёный (все generated files актуальны)
  - AC3: ruff check/format зелёный
  - AC4: новая волна тестов покрывает каждое упрощение (anti-survivorship R5)
  - AC5: суммарное LOC-сокращение ≥ 400 (консервативная оценка)
  - AC6: 0 новых глаголов в entrypoint-manifest (без нового функционала)
  - AC7: 0 regressions в существующих тестах (pytest tests/ -m "not requires_node" проходит)
  - AC8: A5 bugfix верифицирован тестом, воспроизводящим исходный сценарий
IMPLEMENTS:       Решение пользователя 2026-08-02: 1 мега-бриф 118, A5 как bugfix, B3 verify-then-delete, только DevPlan.
IMPACTS:          core/internal/deploy/, core/internal/shared/, core/internal/bootstrap/lifecycle/,
                  core/internal/bootstrap/deploy/, tests/gates/, makefiles/, .github/workflows/, .kilo/worktrees/
REQUIRES:         main ahead by 51 commits (волна 117 не запушена) — волна 118 НЕ зависит от push 117.
-->

# DevPlan 118 — Финальное снижение дрейфа

## $START

### Контекст

- **Предыдущая волна 117** (Shell→Python + SoT dedup) завершена локально, main ahead by 51 commit, не запушена.
- **Текущая волна 118** — системное снижение остаточного дрейфа после аудита 7 субагентов.
- **Сервер tronyx-vps** — ПОКА тестовый, пересоздан, нет запущенных проектов, мигрировать нечего.
- **Цель после волны 118** — ручное тестирование на тестовом сервере.
- **Принцип**: унификация и значительное снижение дрейфа, БЕЗ нового функционала.

### Методология аудита

7 параллельных субагентов (explore) провели аудит по зонам:
1. Shell-монолиты на Python-миграцию
2. Python-монолиты и архитектура
3. Дублирование и SoT-дрейф
4. Тесты на дрейф и дубли
5. Мёртвый код и неиспользуемые экспорты
6. CI gates и entrypoint-manifest
7. Модули и docker-compose стек

Все находки **верифицированы точечными grep-проверками**. Часть находок субагентов **опровергнута**:
- ❌ backup-cron .sh/.py "дублирование" — .sh файлы это тонкие фасады (`exec python3 ...`, 21 строка), уже готовый результат Strangler.
- ❌ `__pycache__` "в git" — 0 файлов, `.gitignore:8` покрывает.
- ❌ `_unit_enabled` "мёртвый" — живой, есть Python-аналог в converge/infra.py.
- ❌ `deploy_compose` "мёртвый" — вызывается из orchestrator.py:401.
- ❌ `content_hash` "идентичны" — это ДВА РАЗНЫХ модуля (docker-build-hash vs generic-file-hash), но имя дублируется.

---

## БЛОК I — Deploy-дрейф (D1-D4)

Крупнейший кластер дублирования: 5 файлов с "deploy"/"orchestrator" в имени, перекрещивающиеся примитивы.

### D1 — Консолидация status/remove/stub (A1, HI)

**Проблема:** Тройное дублирование:
- `status()` реализован дважды: `deploy/orchestrator.py:619` (ProjectStatus) + `deploy/deploy_engine.py:501` (StatusResult) — оба вручную парсят `docker compose ps --format json`.
- `remove()` дважды: `orchestrator.py:681` + `deploy_engine.py:462` (оба `down --timeout 30` без -v).
- Stub-детекция "GENERATED-STUB" в первой строке — инлайн-копия в `orchestrator.py:639` и `deploy_engine.py:525` при существующем `shared/stub_detection.is_stub_ai_platform_yaml`.

**Решение:**
1. `status()` — канон = `DeployEngine.status()` (StatusResult). `DeployOrchestrator.status()` делегирует в engine, преобразует StatusResult→ProjectStatus.
2. `remove()` — канон = `DeployEngine.remove()`. `DeployOrchestrator.remove()` делегирует.
3. Stub-детекция — оба файла импортируют `from core.internal.shared.stub_detection import is_stub_ai_platform_yaml`. Удалить инлайн-копии.
4. Тест: set-сравнение ключей ProjectStatus/StatusResult (расширить существующий T3).

**LOC:** −60…−100. **Риск:** изменение JSON-канона status ломает orchestrator_cli/фасады — покрыть тестом.

### D2 — Удаление двойного snapshot (A1, HI)

**Проблема:** На каждый deploy делается ДВА snapshot:
- `deploy_engine.py:392` — `_capture_deploy_snapshot(project_dir)` (ps/images в файлы).
- `orchestrator.py:450` — `deploy_history.create_snapshot(version=version, ...)` (JSON-снимок).

**Решение:** DeployEngine snapshot — дубликат (DeployHistory покрывает rollback). Удалить `_capture_deploy_snapshot` + `SnapshotInfo`-запись в файл. Если rollback опирается на engine-snapshot — мигрировать на DeployHistory.

**LOC:** −40. **Риск:** проверить, что rollback() не читает engine-snapshot.

### D3 — Удаление мёртвого state_machine step-API (A2, HI)

**Проблема:** `bootstrap/lifecycle/state_machine.py` содержит legacy step-API:
- `start_step/complete_step/skip_step/fail_step/get_current_step` (строки ~334-439)
- `_check_precondition/_check_postcondition/_is_step_done/_is_step_skipped/_hash_changed` (строки ~640-705)

**Верификация:** `grep -rn "\.start_step\|\.complete_step\|\.skip_step\|\.fail_step\|\.get_current_step" core/ tests/ | grep -v state_machine.py` → **0 callers**.

CLI работает через `execute_phase/setup_state` (grouped-phases эра B9). Docstring прямо признаёт: «execute_grouped_phase удалён, фазы выполняются целиком».

**Решение:** Удалить step-API + helper-методы. Обновить тесты, ссылающиеся на удалённые методы (помечаются как removed API).

**LOC:** −120…−140. **Риск:** сломает legacy-тесты state_machine — пометить как removed API, поправить тесты.

### D4 — content_hash номенклатурный rename (A4, HI)

**Проблема:** Два модуля с именем `content_hash.py`:
- `bootstrap/deploy/content_hash.py` — Dockerfile + build context hash (для skip rebuild). API: `compute_source_hash/check_build_needed/save_build_hash`.
- `shared/content_hash.py` — generic file-list hash (для bootstrap idempotency). API: `compute_content_hash(files)`.

`diff` подтвердил: **разные реализации, разные назначения, одинаковое имя** = номенклатурный дрейф.

**Решение:** Rename `bootstrap/deploy/content_hash.py` → `bootstrap/deploy/build_cache.py` (точнее отражает назначение — Docker build cache). Обновить импорты в `docker_orchestrator.py`. shared/content_hash.py оставить как есть (он в shared/ — канон).

**LOC:** 0 (rename). **Риск:** минимальный, обновить 1-2 импорта.

---

## БЛОК II — Мёртвый код и bugfix (D5-D7)

### D5 — Bugfix: SCPChannel без metadata в context_deployer (A5, HI)

**Проблема:** `context_deployer.py:287` создаёт `channel = SCPChannel()` без аргументов. При этом `channels.py:228`:
```python
if "host" not in payload.metadata:
    return DeliveryResult(success=False, error_message="SCPChannel requires 'host' in payload.metadata", ...)
```
→ delivery всегда FAILED → `orchestrator.deploy()` получает failed result → context_deployer возвращает `ProjectDeployResult(status="failed")`.

**TRAP-обоснование** (channels.py:327): `LocalChannel` создан именно для VPS-side receive (payload уже на месте). SCPChannel с empty metadata = всегда FAILED.

**Верификация:** `grep -n "SCPChannel\|LocalChannel" core/internal/bootstrap/deploy/context_deployer.py` → подтверждено `SCPChannel()` без metadata.

**Решение:** Заменить `SCPChannel()` → `LocalChannel()` (payload уже на VPS после context_overlay). Тест: воспроизвести сценарий, который ДО фикса возвращает failed, ПОСЛЕ — deployed.

**LOC:** 0 (1 строка). **Риск:** отсутствуют после TRAP-обоснования. **Критично** для ручного тестирования deploy-context.

⚠️ **TRAP[BUG]** · 2026-08-02 · HI · SCPChannel без metadata в context_deployer — всегда FAILED delivery
· Симптом: deploy-context возвращает "DeployOrchestrator deploy failed" для всех проектов
· Причина: `SCPChannel()` без host в metadata → channels.py:228 возвращает FAILED
· Fix: `LocalChannel()` (VPS-side receive, payload уже на месте)
· Detekt: tests/unit/test_context_deployer_channel.py (new) — сценарий deploy → assert deployed

### D6 — Проверка и удаление typed-геттеров NodeYaml (B3, MED)

**Проблема:** ~500 LOC typed-геттеров в `shared/node_yaml.py`:
- `get_tor_config()` (932-949), `get_repos()` (957-973), `get_postgres_init_databases()` (981-993), `get_node_declaration()` (1001-1020), `get_acme_dns_plugin()` (1028-1039), `get_email()` (1047-1058), `get_firewall()` (874-889), `get_secrets_config()` (897-924), `get_contexts()` (854-866), `get_domain()` (1066-1081)

**Верификация (решение пользователя "verify then delete"):**
1. Шаг 1: `grep -rn "typed-tor\|typed-node\|typed-all\|typed-repos\|...|find-project\|domain-config" core/entrypoints/ core/internal/bootstrap/ Makefile makefiles/` → **0 shell-consumers** (только комментарий в issue-cert.sh:600).
2. Шаг 2: проверить git-историю / runbook на использование `python3 -m core.internal.shared.node_yaml --typed-*` оператором.
3. Шаг 3: если подтверждено "debug-only, не используется" → удалить геттеры + CLI-флаги `--typed-*` из node_yaml_cli.py.

**Условие сохранения:** если найден хотя бы 1 легитимный operator-use → KEEP с пометкой debug-only.

**LOC:** −500 (при удалении). **Риск:** проверить, что нет external потребителей (CI workflow, документация).

### D7 — invoke_module_interface консолидация (B4, MED)

**Проблема:** Идентичные `bash -c "source paths.sh && source module-interface.sh && invoke_module_interface ..."` в:
- `docker_orchestrator.py` `_invoke_healthcheck_full` (~1225-1254)
- `bootstrap/deploy/deploy_orchestrator.py` `_invoke_module_interface` (~688-709)

Различаются таймаутами/возвратами.

**Решение:** Создать `shared/module_interface.py` с `invoke(module, interface, *args) -> (bool, output)`. Оба файла делегируют.

**LOC:** −40. **Риск:** низкие, покрыть тестом.

---

## БЛОК III — SoT/хардкоды (D8-D11)

### D8 — docker_ops.py в timeout-гейте (B7, MED)

**Проблема:** `modules/hermes-agent/watchdog/docker_ops.py:123,147,173,186,193,214` — `timeout=30`, `timeout=10` в `docker stop/kill/rmi/rm`. Гейт `test_gate_timeout_literals.py` декларирует scope "core/modules (watchdog)", но `_DOMAIN_FILES` включает только `agent_watchdog.py` — `docker_ops.py` пропущен.

**Решение:**
1. `docker_ops.py` — импортировать `DOCKER_STOP_TIMEOUT`/`DOCKER_CMD_TIMEOUT` из `shared/timeouts.py`.
2. Расширить `_DOMAIN_FILES` гейта на `docker_ops.py`.

**LOC:** ~0 (импорты). **Риск:** закрытие слепой зоны канона.

### D9 — context_promoter SSH-флаги через SoT (B8, MED)

**Проблема:** `deploy/context_promoter.py:78-91` собирает `-o` флаги вручную (6-я копия), нарушая инвариант 1 ssh_opts.py.

**Решение:** Импортировать `SSH_OPTS` из `shared/ssh_opts.py`.

**LOC:** −4. **Риск:** BatchMode/StrictHostKeyChecking безвредны для `-T` probe.

### D10 — COMPOSE_PROFILES единый loader (B9, MED)

**Проблема:** Дуальный SoT:
- `scaffold/scaffold_helpers.py:60-77` читает **platform-env.yaml** (generated)
- `docker_orchestrator.py:168-175` читает **platform-infra.yaml** (авторитетный)

Гейт `check-profiles-parity` закрепляет platform-infra.yaml как SoT.

**Решение:** Единый loader (в `shared/platform_config.py` или новый `shared/compose_profiles.py`), оба потребителя делегируют. Чтение только из platform-infra.yaml.

**LOC:** −15. **Риск:** при stale platform-env.yaml два потребителя получали разные значения — после фикса консистентно.

### D11 — Хардкод `--timeout 30` ×3 + порт-дефолты (B10, B11, MED)

**Проблема:**
- `deploy_engine.py:485` — `flags=["--timeout", "30"]`
- `orchestrator.py:705` — `flags = ["--timeout", "30"]`
- `scaffold/project_remover.py:293-294` — строка `"docker compose down --timeout 30 ..."` в remote-команде
- `sync_env_defaults.py` — 6 хардкодов портов (6379/9000/9090/8080)

**Решение:** Заменить литералы на `DOCKER_STOP_TIMEOUT` / чтение дефолтов из env_defaults SoT.

**LOC:** ~0. **Риск:** минимальные.

---

## БЛОК IV — Гейты и манифест (D12-D16)

### D12 — Регистрация невидимых гейтов (A3, HI)

**Проблема:** 2 гейта существуют как файлы, но БЕЗ `@pytest.mark.gate` → не выполняются в `make gate`:
- `tests/gates/test_gate_platform_env_schema.py` (339 строк, только `@pytest.fixture`)
- `tests/gates/test_restart_consistency.py` (257 строк)

**Верификация:** гейт-интегрити собирается через `--collect-only -m gate` → эти файлы невидимы.

**Решение:**
1. Добавить `pytestmark = pytest.mark.gate` в оба файла.
2. Добавить записи в `entrypoint-manifest.yaml#gates`.
3. Запустить `make generate-entrypoint-manifest`.

**LOC:** ~3 (маркеры). **Риск:** отсутствуют. **Эффект:** +2 гейта в контуре `make gate`.

### D13 — Консолидация restart-гейтов (F7, MED)

**Проблема:** Перекрытие:
- `test_gate_make_contract.py::test_restart_soft_semantics` (зарегистрирован, manifest gates:1288)
- `tests/gates/test_restart_consistency.py` (незарегистрирован)

Оба проверяют restart = soft stop+start.

**Решение:** Перенести проверки restart-hard/module.mk из test_restart_consistency.py в test_gate_make_contract.py. Удалить test_restart_consistency.py.

**LOC:** −250 (удаление дубля). **Риск:** убедиться, что все assertions перенесены.

### D14 — Удаление dead target generate-manifests-atomic (C2, LOW)

**Проблема:** `makefiles/manifest.mk:118-162` — `generate-manifests-atomic` с поломанной mv-семантикой (`mv "$staging"/* → CURDIR` затирает root AGENTS.md). TRAP[DEBT] 2026-08-01 признал dead («нигде не вызывается»). Остался в allowed_verbs + .PHONY.

**Решение:** Удалить таргет + запись из манифеста + .PHONY.

**LOC:** −48. **Риск:** отсутствуют (target не вызывается).

### D15 — Manifest sync: templates-check дубль + check-exception-patterns loophole (C3, C4, LOW)

**Проблема:**
- `templates-check` объявлен дважды в манифесте (validate:92 + repair:479).
- `check-exception-patterns` (ci.mk:331) — реальный таргет, вызывается gate-конвейером, но НЕ в .PHONY и НЕ в манифесте. Гейт `test_all_makefile_targets_in_allowed_verbs` парсит .PHONY → loophole.

**Решение:**
1. Объединить templates-check в одну запись (repair-вариант).
2. Добавить `check-exception-patterns` в .PHONY + allowed_verbs.

**LOC:** −20 (дедуп) + +15 (register). **Риск:** после регистрации loophole закроется.

### D16 — Документация allowlist 6→8 (C5, LOW)

**Проблема:** Cross-layer allowlist: root AGENTS.md + tests/gates/AGENTS.md фиксируют «6 записей», фактически **8** (после D19/D29/T52 волны 117).

**Решение:** Обновить документацию: 6→8. Опционально: добавить комментарий «расширен до 8 (117: D19/D29/T52)».

**LOC:** ~5. **Риск:** отсутствуют.

---

## БЛОК V — Cleanup и упрощения (D17-D20)

### D17 — Удаление stale worktrees (C1, LOW)

**Проблема:** 7 stale worktrees в `.kilo/worktrees/` (117-brief-c..h), все на merged-коммитах. Засоряют grep/поиск (давали ложные дубли в начале аудита).

**Решение:** `git worktree remove .kilo/worktrees/117-brief-{c,d,e,f,g,h}` + `git branch -D 117-brief-{c,d,e,f,g,h}`.

**LOC:** −(огромное кол-во дублированного кода в поиске). **Риск:** проверить, что ветки merged в main.

### D18 — issue-cert.sh упрощение (C8, LOW)

**Проблема:** `issue-cert.sh:600-619` — shell пере-парсит вывод `node_yaml --domain-config` через `grep '^platform_domain:' | cut -d: -f2-`. Хрупко.

**Решение:** Использовать `node_yaml --format lines` (паттерн deploy.sh:156) или `--get-many`.

**LOC:** −15. **Риск:** покрыто тестом `tests/test_nginx_acme.py`.

### D19 — install-tor-proxy.sh Tier-2 экстракция (B6, MED)

**Проблема:** 422 LOC shell с реальной бизнес-логикой:
- `install_packages()` (71-108) — webtunnel degradation chain (4 вложенные if-ветки, фильтрация массива)
- `write_torrc()` (147-196) — динамический ClientTransportPlugin, ассоциативный массив TRANSPORT_BIN, fail-fast, дедупликация

Это >3 if-веток бизнес-логики (Tier-1) + >150 LOC (Tier-2).

**Решение:** Вынести парсинг Bridge-строк и деградацию транспортов в Python (`bootstrap/tor_transport.py`). Shell оставить оркестратором apt/systemd.

**LOC:** −120 (422→~300). **Риск:** перед миграцией проверить наличие unit-тестов. Если нет — добавить тесты на Python-логику ПЕРЕД миграцией.

⚠️ Условие: если unit-тесты отсутствуют и написание их рискованно — ОТЛОЖИТЬ на 119. Strangler на нетестированном коде = регрессия.

### D20 — Module.yaml D5 контракты (C7, LOW)

**Проблема:** `nginx/module.yaml` — неполный D5 (нет severity/restart/resources/env_requires). Вероятно и другие модули.

**Решение:** Прогнать `make validate-modules`, добить недостающие D5-поля во всех 14 module.yaml.

**LOC:** +30-50 (дополнения). **Риск:** отсутствуют (контракт-валидация).

---

## Порядок выполнения и зависимости

```
БЛОК V-D17 (worktrees)  ← ВЫПОЛНИТЬ ПЕРВЫМ (чистит grep-поиск для остальных)
        │
        ▼
БЛОК II-D5 (SCPChannel bugfix)  ← ВЫПОЛНИТЬ ВТОРЫМ (критично для тестирования)
        │
        ▼
Параллельно:
  БЛОК I (D1-D4)  — deploy-dedup (требует тестирования)
  БЛОК IV (D12-D16) — гейты/манифест (независимо)
        │
        ▼
БЛОК II (D6-D7)  — dead code (зависит от D1-D4 — общие файлы)
        │
        ▼
БЛОК III (D8-D11) — SoT/хардкоды (независимо)
        │
        ▼
БЛОК V (D18-D20) — упрощения (последними, low-risk)
```

## Оценки

| Метрика | Значение |
|---------|----------|
| Задач (D1-D20) | 20 |
| Ожидаемое LOC-сокращение | 400-600 (консервативно), до 1100 при D6-удалении |
| Bugfix'ов | 1 (D5 — SCPChannel) |
| Новых гейтов | +2 (D12) |
| Удалённых гейтов | -1 дубль (D13) |
| Новых глаголов | 0 (AC6) |
| Рискованных задач | 2 (D3 — state_machine, D19 — tor-proxy) — с условием "тесты ПЕРЕД миграцией" |

## Acceptance Criteria (повтор из $ARTIFACT_CONTRACT)

- **AC1:** `make gate MODE=fast` зелёный после каждой волны
- **AC2:** `make check-manifests` зелёный
- **AC3:** `ruff check . && ruff format --check .` зелёный
- **AC4:** R5 ANTI-SURVIVORSHIP — каждое упрощение покрыто тестом (для D5 — negative-тест, воспроизводящий bug)
- **AC5:** Суммарное LOC-сокращение ≥ 400
- **AC6:** 0 новых глаголов в entrypoint-manifest
- **AC7:** `pytest tests/ -m "not requires_node"` проходит без regressions
- **AC8:** D5 bugfix верифицирован тестом

## Commit Policy (U-83)

≤2 коммита на DevPlan:
- `docs(118): 118 DevPlan — drift-reduction-final`
- `feat(118): implementation — D1-D20 waves`

Если волна разбивается на подволны — каждый подблок (I-V) = свой feat-коммит (волна-коммиты = норма).

## $END

## Открытые вопросы (не блокируют старт)

1. **D6 typed-геттеры** — перед удалением проверить git-историю operator-usage. Решение: "verify then delete".
2. **D19 tor-proxy** — есть ли unit-тесты на transport-парсинг? Если нет → ОТЛОЖИТЬ.
3. **Push волны 117** — волна 118 не зависит, но перед деплоем на тестовый сервер нужно слить 117+118.
