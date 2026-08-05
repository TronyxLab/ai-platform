# 138-make-targets-slim — 01-DevPlan.md

$START_DEVPLAN

$ARTIFACT_CONTRACT
PURPOSE:               Сократить поверхность make-контракта платформы (78 .PHONY → 75, глоссарий 74 → 70 глаголов) без потери функциональности: удалить deprecated-алиасы, консолидировать дубль-таргеты, скрыть технические таргеты из глоссария, автоматизировать ручной пост-деплойный шаг render-monitoring.
DESCRIPTION:           Четыре волны. W1: удаление make-таргетов compose-safe-up (алиас up-safe), preflight (алиас check), sync-env-defaults (дубль generate-env-example, один скрипт sync_env_defaults.py) + замена 6 ссылок + расширение механизма literal-банов гейта phantom-refs на «make <удалённый>» паттерны. W2: перевод _get_all_profiles (технический помощник parity-гейта) в системные исключения (SYSTEM_EXCEPTIONS генератора) — таргет жив, вне глоссария/allowed_verbs. W3: автоматизация render-monitoring — экстракция run_monitoring_reconfig() из monitoring_config_renderer.py main(), вызов из DeployOrchestrator._run_post_deploy_chain (non-blocking, паритет до-B8 module-hook), make render-monitoring остаётся ручным fallback. W4: чистка docs, регенерация манифестов (Manifest Generation Contract, инвариант 11), финальный gate.
RATIONALE:             Аудит 2026-08-05 (78 .PHONY, 74 allowed_verbs): ~25 таргетов уже вызываются автоматически (CI/hooks/каскады/гейты), ~47 — публичный API человека, 2 deprecated-алиаса и 1 дубль — мёртвый балласт. Каждый удалённый таргет = −1 запись в 4 артефактах (Makefile, allowed_verbs, глоссарий, канон-таблица) и −1 слово в UX-поверхности. render-monitoring — единственный таргет, который по смыслу должен исполняться автоматически: его module-hook удалён в волне 118 (B8, «Python-эквиваленты»), но эквивалент так и не вызывается из оркестратора — пост-деплойный рендер мониторинга висит в воздухе. Дубль sync-env-defaults ≡ generate-env-example подтверждён: оба вызывают sync_env_defaults.py с идентичными флагами.
ACCEPTANCE_CRITERIA:   (1) make-таргеты compose-safe-up, preflight, sync-env-defaults удалены из .PHONY, allowed_verbs, глоссария (74→70 глаголов); (2) 0 упоминаний literal'ов «make compose-safe-up», «make preflight», «make sync-env-defaults» во всех сканируемых корнях (core/, tests/, makefiles/, .github/, .kilo/, root-файлы) — гейт literal-банов зелёный; (3) repair-recipe check-env-defaults указывает на make generate-env-example, make check-env-defaults зелёный; (4) _get_all_profiles отсутствует в глоссарии/allowed_verbs, но make _get_all_profiles работает (test_gate_profiles_parity зелёный); (5) receive-деплой проекта с monitoring-секцией автоматически перерисовывает конфиг мониторинга ([IMP:9][hook] в post_deploy_chain), проект без monitoring-секции — skip, ошибка рендера — WARN non-fatal; (6) make render-monitoring продолжает работать (ручной fallback, тесты зелёные); (7) make gate MODE=fast зелёный, make check чистый; (8) 0 новых таргетов, 0 изменений поведения оставшихся 70 глаголов.
IMPLEMENTS:            Аудит make-поверхности 2026-08-05 (суперпозиция S1-S5); паттерны: Manifest Generation Contract (инвариант 11), phantom-refs гейт с literal-банами (DevPlan 120 AC-5), SYSTEM_EXCEPTIONS (DevPlan 119 G2), post-deploy chain best-effort (B8, волна 118), repair-контракт L1.
IMPACTS:               makefiles/modules.mk, makefiles/repair.mk, makefiles/manifest.mk (удаление таргетов), tests/gates/test_gate_phantom_refs.py (механизм literal-банов), tests/gates/test_gate_env_example_drift.py, tests/gates/test_gate_profiles_parity.py, tests/gates/test_gate_domain_parity.py (сообщения repair), core/internal/scripts/sync_env_defaults.py (2 сообщения), core/internal/scripts/generate_entrypoint_manifest.py (SYSTEM_EXCEPTIONS), core/internal/monitoring_config_renderer.py (экстракция run_monitoring_reconfig), core/internal/deploy/orchestrator.py (вызов в post_deploy_chain), core/entrypoint-manifest.yaml + core/AGENTS.md + AGENTS.md (generated, регенерация), tests/unit/test_monitoring_post_deploy.py (новый), docs/* (чистка упоминаний).
REQUIRES:              main зелёный (136/137 влиты); решение по render-monitoring принято (паритет до-B8: рендер на каждый receive, non-blocking) — DevPlan §4.3; 0 конфликтов с параллельными волнами 137 (practices — не пересекается: иные файлы).
$END_ARTIFACT_CONTRACT

$START_DOCUMENT_PLAN
### Document Plan
**SECTION_GOALS:**
- GOAL [Зафиксировать стратегию: оптимизация контракта, не «уборка команд»] => G1 (§1)
- GOAL [Определить целевую архитектуру после оптимизации (контракты + code graph)] => G2 (§2)
- GOAL [Развернуть суперпозицию решений по каждому кандидату, включая отклонённые] => G3 (§3)
- GOAL [Задать детальные контракты: literal-баны, SYSTEM_EXCEPTIONS, run_monitoring_reconfig] => G4 (§4)
- GOAL [Разбить работу на исполняемые волны с AC и чек-листами] => G5 (§5)
- GOAL [Зафиксировать файловый манифест, риски, промт-шаблон] => G6 (§6, §7, §8)
**SECTION_USE_CASES:**
- USE_CASE [Агент исполняет W1 — удаление алиасов с полной заменой ссылок] => SC1 (§5 W1)
- USE_CASE [Агент исполняет W3 — автоматизация render-monitoring] => SC2 (§5 W3)
- USE_CASE [QA верифицирует AC глобальные] => SC3 (§9)
- USE_CASE [Code-субагент исполняет волну по промт-шаблону] => SC4 (§8)
$END_DOCUMENT_PLAN

## 1. Стратегия

**Оптимизация контракта, не «уборка команд».** Make-таргеты платформы — это реестр канонических операций (инвариант 1: Makefile — единый фасад; инвариант 5: entrypoint-manifest; глоссарий глаголов; гейты no-unregistered-entrypoint / manifest-integrity / namelint следят за триадой Makefile↔manifest↔AGENTS.md). Почти каждый таргет — тонкий фасад (1 строка) над Python-модулем. Поэтому сокращение количества = сокращение **поверхности контракта**, а не удаление логики: логика остаётся в Python-модулях и entrypoints.

Три механики сокращения + одна автоматизация:

```
┌─ 78 .PHONY сегодня ──────────────────────────────────────────┐
│  74 allowed_verbs (глоссарий) + 4 системных исключения        │
│                                                               │
│  W1  −3: compose-safe-up (алиас up-safe)                      │
│          preflight (алиас check, literal-бан уже есть)        │
│          sync-env-defaults (дубль generate-env-example)       │
│  W2  −1: _get_all_profiles → системные исключения (таргет жив)│
│  ─────────────────────────────────────────────────────────    │
│  ИТОГ: 70 allowed_verbs (глоссарий) + 5 системных исключений  │
│        = 75 .PHONY; −0 новых; 0 изменений поведения остальных │
└───────────────────────────────────────────────────────────────┘
                    │
                    └─ W3: render-monitoring → АВТОМАТИЧЕСКИЙ вызов
                        в DeployOrchestrator._run_post_deploy_chain
                        (таргет остаётся ручным fallback)
```

**Принципы:**
1. **Удаление = literal-бан.** Каждый удалённый таргет немедленно попадает в механизм make-literal банов гейта phantom-refs (расширение _PREFLIGHT_BAN) — возврат имени невозможен конструктивно.
2. **Скрытие ≠ удаление.** _get_all_profiles остаётся рабочим таргетом (его вызывает parity-гейт), но перестаёт быть канонической операцией — переезжает в SYSTEM_EXCEPTIONS (прецедент DevPlan 119 G2: help/venv/pre-commit-*).
3. **Автоматизация — только там, где смысл требует.** render-monitoring — единственный таргет, который должен исполняться внутри скрипта: его module-hook удалён (волна 118), а Python-эквивалент не подключён. Восстанавливаем поведение до-B8: рендер на каждый receive, non-blocking.
4. **Синхронность артефактов.** Любое изменение таргета — в 4 точках сразу (makefiles/*.mk → allowed_verbs → глоссарий → канон-таблица), регенерация через make generate-manifests + generate-agents-md (инвариант 11), иначе CI-gate блокирует.

## 2. Целевая архитектура

### 2.1 Контракты после оптимизации

| Артефакт | Было | Стало |
|----------|------|-------|
| `.PHONY` (уникальных) | 78 | 75 |
| `allowed_verbs` | 74 | 70 |
| Глоссарий AGENTS.md | 74 записи | 70 записей |
| Системные исключения | 4 (help, venv, pre-commit-install, pre-commit-run) | 5 (+ _get_all_profiles) |
| Make-literal баны (phantom-refs) | 1 («make preflight», .kilo/+AGENTS.md скан) | 3 («make preflight», «make compose-safe-up», «make sync-env-defaults», скан ВСЕХ корней) |
| Публичный таргет регенерации .env.example | 2 (sync-env-defaults, generate-env-example) | 1 (generate-env-example) |
| Пост-деплойный рендер мониторинга | ручной (make render-monitoring) | автоматический (post_deploy_chain) + ручной fallback |

### 2.2 Draft Code Graph (XML)

```xml
<knowledge_graph>
  <entity name="makefiles_mk" type="FACADE" keywords="make targets .PHONY">
    <CrossLink>core/entrypoint-manifest.yaml</CrossLink>
    <CrossLink>AGENTS.md glossary</CrossLink>
  </entity>
  <entity name="generate_entrypoint_manifest_py" type="MODULE"
          keywords="SYSTEM_EXCEPTIONS allowed_verbs generator">
    <CrossLink>core/entrypoint-manifest.yaml</CrossLink>
  </entity>
  <entity name="test_gate_phantom_refs_py" type="GATE" keywords="phantom names literal bans">
    <CrossLink>makefiles/modules.mk</CrossLink>
    <CrossLink>makefiles/repair.mk</CrossLink>
    <CrossLink>makefiles/manifest.mk</CrossLink>
  </entity>
  <entity name="monitoring_config_renderer_py" type="MODULE"
          keywords="run_monitoring_reconfig post-deploy render">
    <CrossLink>core/internal/deploy/orchestrator.py</CrossLink>
  </entity>
  <entity name="DeployOrchestrator_CLASS" type="CLASS" keywords="post_deploy_chain receive">
    <CrossLink>monitoring_config_renderer_py</CrossLink>
  </entity>
  <entity name="check_env_defaults" type="TARGET" keywords="repair recipe generate-env-example">
    <CrossLink>makefiles/manifest.mk</CrossLink>
  </entity>
</knowledge_graph>
```

### 2.3 Step-by-step Data Flow

**W1 (удаление):** edit makefiles/*.mk → замена 6 ссылок (3 gate-теста + 2 сообщения sync_env_defaults.py + repair-recipe) → расширение literal-банов в test_gate_phantom_refs.py → `make generate-manifests` (allowed_verbs −4, регенерация secrets/platform-env/.env.example/entrypoint-manifest) → `make generate-agents-md` (глоссарий −4, канон-таблица −4) → `make check` → `make gate MODE=fast`.

**W2 (скрытие):** edit SYSTEM_EXCEPTIONS в generate_entrypoint_manifest.py → синхронизация name_linter.system_exceptions (проверить generated/ручную) → регенерация → parity-гейт зелёный.

**W3 (автоматизация):** экстракция run_monitoring_reconfig() из main() → вызов в _run_post_deploy_chain (после generate-catalog, до deploy-hooks, lazy-import, try/except → WARN) → юнит-тесты → make render-monitoring проверка (fallback жив).

## 3. Суперпозиция решений (аудит 2026-08-05)

### 3.1 S1 — Удаление deprecated-алиасов (compose-safe-up, preflight) — ПРИНЯТО
- **Факты:** 0 вызовов обоих в скриптах/CI/workflows (grep по .sh/.py/.yml/.md). preflight-таргет = `preflight: check` (NOOP-алиас); literal-бан «make preflight» уже существует в phantom-refs (DevPlan 120 AC-5) — удаление таргета завершает начатую депрекацию. compose-safe-up — «Deprecated alias for up-safe» в канон-таблице.
- **Rejected:** оставить (риск: мёртвый балласт в глоссарии, двусмысленность для агентов — два имени одной операции).
- **Rev:** — (механизм literal-банов делает возврат невозможным).

### 3.2 S2 — Консолидация sync-env-defaults ≡ generate-env-example — ПРИНЯТО
- **Факты:** оба вызывают `sync_env_defaults.py` с идентичными флагами (`--platform-env platform-env.yaml --secret-defs core/secret-definitions.yaml --output .env.example`). generate-env-example входит в каскад generate-manifests (Chain A), sync-env-defaults — standalone. 6 ссылок на sync-env-defaults: repair-recipe check-env-defaults (manifest.mk:195), 3 gate-теста (env_example_drift:67, profiles_parity:129, domain_parity:95), 2 сообщения в sync_env_defaults.py (224, 907).
- **Rejected:** удалить generate-env-example вместо sync-env-defaults (ломает Chain A — .env.example выпадает из generate-manifests).
- **Rev:** — (имя generate-env-example становится единственным публичным; скрипт sync_env_defaults.py НЕ переименовывается — имя файла = имя скрипта, вне скоупа).

### 3.3 S3 — _get_all_profiles → системные исключения — ПРИНЯТО
- **Факты:** вызывается ТОЛЬКО из test_gate_profiles_parity (проверка (c): `make _get_all_profiles` stdout == SoT). В глоссарии выглядит как операция («Вывод COMPOSE_PROFILES»), но это технический помощник gate-теста, а не каноническая операция. Прецедент: SYSTEM_EXCEPTIONS (DevPlan 119 G2).
- **Rejected:** удалить таргет (parity-гейт потеряет механизм проверки — пришлось бы дублировать чтение COMPOSE_PROFILES в тесте); оставить в глоссарии (засоряет контракт техническим таргетом).
- **Rev:** если _get_all_profiles обретёт второго потребителя-человека — вернуть в allowed_verbs.

### 3.4 S4 — Автоматизация render-monitoring — ПРИНЯТО
- **Факты:** module-hook monitoring удалён (волна 118 B8) с комментарием «Python-эквиваленты: monitoring_config_renderer.py / on_project_deploy.py», но из оркестратора рендер НЕ вызывается — `rg render|monitoring` в receive_flow.py/orchestrator.py: 0 вызовов. Рендер мониторинга после деплоя возможен только вручную (make render-monitoring). До B8 хуки выполнялись на каждый деплой.
- **Rejected:** оставить ручным (дрейф конфига мониторинга при деплоях мимо руки — инцидент-класс «молчаливый stale-конфиг»); удалить таргет (ручной fallback полезен для перерендера без деплоя).
- **Rev:** если reload_monitoring_services() на каждый receive начнёт ломать деплой-цикл (>2 инцидентов) — добавить diff-guard (рендер только при изменении конфига).

### 3.5 S5 — Остальные ~68 таргетов — ОТКЛОНЕНО (осознанное решение, фиксация)
- Индивидуальные check-* (9 шт) — точечный фикс-цикл агентов (документировано в .kilo/rules/_project.md) + repair-поля манифеста (repair_id/repair_command L1).
- generate-* индивидуальные (6 шт) — точечная регенерация при drift, входят в каскад generate-manifests.
- scaffold-таргеты (7 шт) — публичный API lifecycle, делегируют в один scaffold.sh (7 subcommand'ов) — уже консолидированы на уровне делегата.
- test vs test-summary — НЕ дубль: test = оркестратор (MARKER=all, 9 шагов, junit-merge), test-summary = компактная обёртка test_runner (TEST_FILE/TIMEOUT). Разные контракты.
- preflight.py фасад (core/internal/preflight.py) — НЕ удалять: потребитель tests/unit/test_check_suite.py (import + monkeypatch run_diagnostic). Таргет уходит, Python-фасад остаётся (0 нарушений, историческая совместимость).
- **Фиксация в §6.3 «НЕ трогать»** — чтобы следующий аудит не «оптимизировал» заново.

## 4. Детальные контракты

### 4.1 Механизм make-literal банов (расширение _PREFLIGHT_BAN)

`tests/gates/test_gate_phantom_refs.py`:
- `_PREFLIGHT_BAN = "make preflight"` → `_MAKE_LITERAL_BANS: tuple[str, ...] = ("make preflight", "make compose-safe-up", "make sync-env-defaults")`.
- Скан: расширить с (.kilo/ + AGENTS.md-файлы) на ВСЕ корни, сканируемые _PHANTOM_NAMES (core/, tests/, makefiles/, .github/, .kilo/, root-файлы) — паритет механики _scan_paths.
- Имена НЕ добавляются в _PHANTOM_NAMES (полные имена): sync-env-defaults содержится в имени файла sync_env_defaults.py (self-name, вечный RED); compose-safe-up/preflight — «preflight» легитимно живёт в core/internal/deploy/preflight.py и TRAP-записях. Literal-бан make-паттерна — точный и достаточный.
- Порядок внедрения (важно): СНАЧАЛА замена всех ссылок (§5 W1 шаги 1-4), ПОТОМ активация бана (шаг 5) — иначе гейт RED на промежуточном коммите.

### 4.2 SYSTEM_EXCEPTIONS / name_linter.system_exceptions

- `core/internal/scripts/generate_entrypoint_manifest.py`: SYSTEM_EXCEPTIONS += `_get_all_profiles`.
- Проверить при реализации: секция `name_linter.system_exceptions` в entrypoint-manifest.yaml генерится из SYSTEM_EXCEPTIONS или статична. Если статична — синхронизировать вручную (+1 запись с rationale).
- Эффект регенерации: allowed_verbs 74→71 после W1 (−3 удалённых), →70 после W2 (−1 скрытый); глоссарий 74→70; канон-таблица −4 записи; системные исключения 4→5.
- Таргет `_get_all_profiles` остаётся в makefiles/helpers.mk .PHONY — гейт test_all_makefile_targets_in_allowed_verbs пропускает системные исключения (by-design, DevPlan 119 G2).

### 4.3 run_monitoring_reconfig (экстракция из main)

Сигнатура (native Python, НЕ subprocess):
```python
def run_monitoring_reconfig(
    project_dir: Path, project_name: str, node_name: str, platform_root: Path,
) -> int:
    """Post-deploy monitoring reconfiguration (паритет до-B8 module-hook).
    Контракт: build_merged_config None → return 0 (skip, log IMP:8);
    все render-шаги non-blocking (ошибка → log, continue);
    порядок: alert_rules → prometheus → grafana → loki → reload → langfuse → catalog.
    Возвращает 0 всегда (best-effort); исключения НЕ пробрасываются в orchestrator.
    """
```
- main() CLI (для make render-monitoring) после экстракции: resolve platform_root → logging → `return run_monitoring_reconfig(project_dir, project_name, node_name, platform_root)`.
- Вызов в `DeployOrchestrator._run_post_deploy_chain` — ПОСЛЕ generate-catalog, ДО `_invoke_registered_deploy_hooks`:
```python
# ── Monitoring reconfig (DevPlan 138): паритет до-B8 module-hook ──
# B8 (волна 118) удалил monitoring deploy-hook с пометкой «Python-эквиваленты»,
# но вызов так и не был подключён — рендер висел ручным (make render-monitoring).
if project_dir and project:
    try:
        from core.internal.monitoring_config_renderer import run_monitoring_reconfig
        run_monitoring_reconfig(
            Path(project_dir), project, node_name or "", platform_root,
        )
    except Exception as e:  # noqa: BLE001 — best-effort контракт post-deploy chain
        logger.warning("[IMP:8][DeployOrchestrator][post_deploy_chain] monitoring reconfig WARN (non-fatal): %s", e)
```
- lazy-import (паритет _invoke_registered_deploy_hooks); platform_root = platform_remote_base() (уже вычислен в post_deploy_chain).
- NODE на VPS: node_name доступен как аргумент post_deploy_chain (паритет deploy-hooks NODE_NAME).

## 5. Волны (порядок исполнения)

### W1 — Удаление алиасов и дубля (S1+S2) + literal-баны
**Задачи:**
1. `makefiles/modules.mk`: удалить `compose-safe-up` из .PHONY (строка ~13) и таргет `compose-safe-up: up-safe` (строка ~28) + его комментарий.
2. `makefiles/repair.mk`: удалить `preflight` из .PHONY и таргет `preflight: check` (строки ~184-189) + комментарий «DEPRECATED alias».
3. `makefiles/manifest.mk`: удалить `sync-env-defaults` из .PHONY (строка ~27) и таргет (строки ~178-184); repair-recipe в check-env-defaults (строка ~195): `make sync-env-defaults` → `make generate-env-example`; обновить STRUCTURE/@scope-комментарии (строки 2, 5, 10).
4. Замена ссылок (6 точек):
   - `tests/gates/test_gate_env_example_drift.py:67` → `make generate-env-example`
   - `tests/gates/test_gate_profiles_parity.py:129` → `make generate-env-example`
   - `tests/gates/test_gate_domain_parity.py:95` → `make generate-env-example`
   - `core/internal/scripts/sync_env_defaults.py:224, 907` → `make generate-env-example` (строки в docstring/сообщениях)
5. `tests/gates/test_gate_phantom_refs.py`: _PREFLIGHT_BAN → _MAKE_LITERAL_BANS (3 литерала), скан на все корни (§4.1).
6. Регенерация: `make generate-manifests` + `make generate-agents-md` (allowed_verbs/глоссарий/канон-таблица/entrypoint-manifest — инвариант 11). Проверить `git diff core/entrypoint-manifest.yaml AGENTS.md core/AGENTS.md` — ожидаем ровно −3 записи в allowed_verbs/глоссарии (W1), −0 в остальном.
7. `make check` (до чистоты) → `make gate MODE=fast`.

**AC W1:** таргеты удалены из .PHONY; 0 упоминаний 3 literal-банов во всех корнях (гейт зелёный); repair-recipe указывает на generate-env-example; allowed_verbs/глоссарий = 71; `make check-env-defaults` и `make check-manifests` зелёные; gate зелёный.

### W2 — Сокрытие _get_all_profiles (S3)
**Задачи:**
1. `core/internal/scripts/generate_entrypoint_manifest.py`: SYSTEM_EXCEPTIONS += `_get_all_profiles` (с комментарием: технический помощник parity-гейта, не каноническая операция).
2. Проверить секцию `name_linter.system_exceptions` в entrypoint-manifest.yaml: generated → регенерация сама обновит; статичная → +1 запись вручную.
3. Регенерация манифестов + `make generate-agents-md`; проверить diff: глоссарий −1, allowed_verbs −1, системные исключения +1.
4. Проверка: `make _get_all_profiles` работает; `pytest tests/gates/test_gate_profiles_parity.py -q` зелёный; namelint (pre-commit) зелёный.

**AC W2:** _get_all_profiles отсутствует в allowed_verbs/глоссарии; parity-гейт и namelint зелёные; системные исключения = 5.

### W3 — Автоматизация render-monitoring (S4)
**Задачи:**
1. `core/internal/monitoring_config_renderer.py`: экстракция `run_monitoring_reconfig(project_dir, project_name, node_name, platform_root) -> int` из main() (§4.3); main() вызывает её.
2. `core/internal/deploy/orchestrator.py`: вызов в `_run_post_deploy_chain` (после generate-catalog, до deploy-hooks; lazy-import; try/except → WARN non-fatal).
3. Юнит-тесты `tests/unit/test_monitoring_post_deploy.py` (новый):
   - run_monitoring_reconfig с monitoring-конфигом → все render-шаги вызваны (mock), return 0;
   - build_merged_config → None (нет monitoring-секции) → skip, return 0, лог IMP:8;
   - сбой render-шага → лог WARN, return 0 (non-fatal);
   - post_deploy_chain вызывает reconfig с корректными аргументами (mock, assert call) — проверить, что project_dir/project/node_name пробрасываются;
   - LDD: assert ≥1 IMP:9-лог в успешном сценарии.
4. Проверка fallback: `make render-monitoring PROJECT=<mock> PROJECT_DIR=<dir>` работает (регрессия CLI main()).
5. `make check` → `make gate MODE=fast`.

**AC W3:** юнит-тесты зелёные; receive-деплой с monitoring-секцией логирует [IMP:9][hook] monitoring on-project-deploy START/DONE; без секции — skip без рендера; сбой рендера не роняет деплой; make render-monitoring работает.

### W4 — Документация, чистка, финальный gate
**Задачи:**
1. `rg -n "make compose-safe-up|make sync-env-defaults|make preflight" docs/ .kilo/ *.md` → чистка упоминаний (заменить на канонические имена или удалить). TRAP-записи в AGENTS.md НЕ трогать (исторические; literal-баны сканируют точный паттерн «make X» — «preflight.py gate» и «экс-preflight» безопасны, проверить каждое совпадение).
2. Проверить `core/internal/preflight.py:60` — сообщение «DEPRECATED — use `make check`» остаётся валидным (таргета preflight больше нет — сообщение актуально).
3. Регенерация финальная: `make fix-gate` → `make check` (до чистоты) → `make gate MODE=fast`.
4. Commit-политика (U-83): ≤2 коммита: `docs(138): N DevPlan — make targets slim` (этот файл) + `feat(138): N implementation — make targets slim (W1-W4)`.

**AC W4:** 0 упоминаний удалённых make-паттернов в docs/.kilo/; gate зелёный; глоссарий 68 глаголов; docs/platform-project-contract.md (если ссылается на sync-env-defaults) обновлён.

## 6. Файловый манифест

### 6.1 Новые файлы
| Файл | Волна | Назначение |
|------|-------|-----------|
| `tests/unit/test_monitoring_post_deploy.py` | W3 | Юнит-тесты run_monitoring_reconfig + вызов из post_deploy_chain |

### 6.2 Изменяемые файлы
| Файл | Волна | Изменение |
|------|-------|-----------|
| `makefiles/modules.mk` | W1 | −compose-safe-up |
| `makefiles/repair.mk` | W1 | −preflight |
| `makefiles/manifest.mk` | W1 | −sync-env-defaults; repair-recipe → generate-env-example; комментарии |
| `tests/gates/test_gate_phantom_refs.py` | W1 | _PREFLIGHT_BAN → _MAKE_LITERAL_BANS (3), скан всех корней |
| `tests/gates/test_gate_env_example_drift.py` | W1 | сообщение → generate-env-example |
| `tests/gates/test_gate_profiles_parity.py` | W1 | сообщение → generate-env-example |
| `tests/gates/test_gate_domain_parity.py` | W1 | сообщение → generate-env-example |
| `core/internal/scripts/sync_env_defaults.py` | W1 | 2 сообщения → generate-env-example |
| `core/internal/scripts/generate_entrypoint_manifest.py` | W2 | SYSTEM_EXCEPTIONS += _get_all_profiles |
| `core/entrypoint-manifest.yaml` | W1/W2 | generated — allowed_verbs 68, system_exceptions 5 |
| `core/AGENTS.md` | W1/W2 | generated — канон-таблица, системные исключения |
| `AGENTS.md` | W1/W2 | generated — глоссарий 68 |
| `core/internal/monitoring_config_renderer.py` | W3 | экстракция run_monitoring_reconfig |
| `core/internal/deploy/orchestrator.py` | W3 | вызов в post_deploy_chain |
| `docs/*` (при совпадении) | W4 | чистка упоминаний |
| `.ai/plans/138-make-targets-slim/01-DevPlan.md` | — | этот файл |

### 6.3 НЕ трогать (осознанные решения, фиксация для будущих аудитов)
| Артефакт | Причина |
|----------|---------|
| 70 оставшихся глаголов | публичный API / каскады / фикс-цикл / repair-поля (§3.5) |
| `test` vs `test-summary` | разные контракты (оркестратор vs компактная обёртка) |
| `core/internal/preflight.py` | потребитель tests/unit/test_check_suite.py |
| `core/internal/deploy/preflight.py` | другой модуль (pre-deploy validation), не связан с make-таргетом |
| `core/lib/*` модульные Makefile | двухуровневая семантика root/module (инвариант, глоссарий) |
| Имя файла `sync_env_defaults.py` | имя скрипта = имя файла, вне скоупа контракта |
| TRAP-записи AGENTS.md с упоминаниями preflight/compose-safe-up | историческая документация, literal-бан матчит только «make X» |

## 7. Риски

| # | Риск | Вероятность | Митигация |
|---|------|-------------|-----------|
| R1 | Literal-бан найдёт исторические упоминания в новых корнях (core/tests/makefiles/.github) | СРЕДНЯЯ | Порядок: замена ссылок → активация бана (W1 шаги 4→5); W4 полный grep |
| R2 | repair-recipe generate-env-example тянет каскад (secrets-manifest → platform-env → .env.example) — repair медленнее и шире | НИЗКАЯ | Приемлемо: каскад идемпотентен, repair-safe (L1); байт-сравнение check-env-defaults то же |
| R3 | reload_monitoring_services() на каждый receive — нагрузка/гонки | НИЗКАЯ | Паритет до-B8 (хук был на каждый деплой); non-blocking; Rev-условие §3.4 (diff-guard при >2 инцидентов) |
| R4 | Регенерация манифестов рассинхронизирует CI-gate test_all_makefile_targets_in_allowed_verbs | НИЗКАЯ | Все правки в одном коммите; финальный make gate MODE=fast обязателен |
| R5 | W3 — исключение в run_monitoring_reconfig пробросится в receive | НИЗКАЯ | try/except вокруг вызова (WARN non-fatal); тест на сбой рендера |
| R6 | Параллельная волна 137 (practices) меняет makefiles/ | НИЗКАЯ | Пересечения нет (project-*.mk, quality-gate.yml); при конфликте — rebase на main |

## 8. Промт-шаблон Code-субагента (SC4)

```
Исполни волну W{N} из .ai/plans/138-make-targets-slim/01-DevPlan.md (§5 W{N}).

КОНТЕКСТ: платформа ai-platform. Инвариант 11 (Manifest Generation Contract):
generated-файлы (entrypoint-manifest.yaml, AGENTS.md глоссарий, канон-таблица core/AGENTS.md)
НЕ редактируются вручную — только make generate-manifests + make generate-agents-md.
Триада Makefile↔manifest↔AGENTS.md синхронна; гейты namelint/phantom-refs/manifest-integrity RED при рассинхроне.

ШАГИ:
1. Прочитай DevPlan §5 W{N} и §4 (детальные контракты), §6 (файловый манифест).
2. Выполни задачи волны строго по чек-листу. Для W1: СНАЧАЛА замена ссылок (шаги 1-4), ПОТОМ literal-баны (шаг 5) — иначе промежуточный RED.
3. Регенерация: make generate-manifests && make generate-agents-md. Проверь git diff core/entrypoint-manifest.yaml AGENTS.md core/AGENTS.md: ожидаемый дельта-сигнал (allowed_verbs/глоссарий: −3 для W1, −1 для W2). Любое отклонение — стоп и разбор.
4. Верификация: make check (до чистоты, батчем) → make gate MODE=fast. Для W3 дополнительно: pytest tests/unit/test_monitoring_post_deploy.py -q.
5. НЕ трогай файлы из §6.3 (осознанные решения).
6. Верни: список изменённых файлов, дельта-сигнал манифестов, результат gate (fast), подтверждение AC волны.
```

## 9. AC глобальные (сводка)

1. Глоссарий: 74 → 70 глаголов; системные исключения: 4 → 5; .PHONY: 78 → 75.
2. 0 упоминаний «make compose-safe-up», «make preflight», «make sync-env-defaults» во всех сканируемых корнях (гейт literal-банов зелёный).
3. `make check-env-defaults` зелёный; repair-recipe указывает на generate-env-example.
4. `make _get_all_profiles` работает; parity-гейты (profiles/domain/env-example-drift) зелёные.
5. receive-деплой с monitoring-секцией автоматически рендерит мониторинг ([IMP:9][hook] в логах post_deploy_chain); без секции — skip; сбой — WARN, деплой жив.
6. `make render-monitoring` работает (ручной fallback).
7. `make gate MODE=fast` зелёный (pre-push hook прогонит автоматически).
8. 0 новых таргетов; поведение оставшихся 70 глаголов не изменено.

## 10. Открытые вопросы и TRAP-заметки

### 10.1 Открытые вопросы (решить до/во время реализации)
- **O1 (W2):** секция `name_linter.system_exceptions` в entrypoint-manifest.yaml — generated из SYSTEM_EXCEPTIONS или статичная? Проверить generate_entrypoint_manifest.py (строки ~174 «Filter: exclude system_exceptions») и YAML-шаблон; если статичная — синхронизировать вручную.
- **O2 (W1):** exact-проверка строк рецептов (номера могут сдвинуться): перед edit прочитать актуальные строки makefiles/*.mk.
- **O3 (W3):** node_name в post_deploy_chain — доступен как аргумент (паритет deploy-hooks NODE_NAME)? Подтвердить сигнатуру при реализации; если нет — пустая строка (renderer принимает default "").

### 10.2 TRAP-заметки (для следующего агента-археолога)
- Удаление таргета ≠ удаление Python-модуля: фасады (preflight.py, sync_env_defaults.py) остаются — их потребители живут (тесты, entrypoints).
- «make preflight» НЕ добавлять в _PHANTOM_NAMES: «preflight» легитимно живёт в core/internal/deploy/preflight.py и TRAP-записях. Literal-бан «make preflight» — единственный корректный механизм.
- Числа: 78 .PHONY = 74 allowed_verbs + 4 системных исключения (до); 75 = 70 + 5 (после). Использовать как дельта-сигнал при регенерации.

### 10.3 Зависимости от других DevPlan
- DevPlan 137 (project-practices): параллельная волна, файловых пересечений нет (project-practices.mk vs modules/repair/manifest.mk) — при конфликте rebase.
- DevPlan 120 (check-suite, phantom-refs AC-5): источник механики literal-банов.
- DevPlan 119 (G2 SYSTEM_EXCEPTIONS): прецедент системных исключений.
- Волна 118 (B8): удаление monitoring module-hook — источник задачи W3.

$END_DEVPLAN
