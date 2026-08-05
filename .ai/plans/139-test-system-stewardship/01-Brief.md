# 139-test-system-stewardship — 01-Brief.md

$START_BRIEF

$ARTIFACT_CONTRACT
PURPOSE:               Актуализация тестовой системы ai-platform после месяца Strangler-Fig-рефакторинга (116–138): устранить синтетические тесты, закрыть реальные blind spots, консолидировать дубли, навести порядок в TRAP-таксономии и процессных артефактах — на основе двух независимых forensic-аудитов (2026-08-05) с независимой верификацией каждого пункта против рабочего дерева.
DESCRIPTION:           МЕТА-план координации двух аудитов тестовой системы (полный forensic-аудит + аудит 7 аудиторами). 5 волн: W1 безопасная очистка (синтетика + мёртвые фикстуры + TRAP-мусор), W2 переписывание тестов реализации, W3 консолидация дублей, W4 закрытие 10 blind spots (6 из аудитов + 4 найденных при верификации), W5 процессная гигиена (TRAP-таксономия, RESOLVED-практика, anti-loop, deploy.sh тикет). Предусловия: планы 137 (project-practices) и 138 (make-targets-slim) влиты в main — их тесты не попадают под консолидацию, а 138 W3 даёт hook для интеграционного теста render-monitoring.
RATIONALE:             Два аудита (3334/3505 тестов, ~3350 TRAP) сходятся в главном: система не создаёт массовую ложную уверенность (~2500-2600 ценных тестов, R1-R5 и enforcement-гейты работают), но содержит 2 P0-файла синтетики (~845 LOC), ~25-30 файлов тестов реализации, 5-6-уровневое дублирование и реальные слепые зоны. Верификация против дерева выявила 3 новые ошибки аудиторов (tronyx-site «живой» — на деле мёртв; test-site «недостижим» — на деле имя живо в node.yaml; module_yaml_paths «0 callsites» — на деле 3 живых callsites) и 4 упущенных обоими аудитами blind spot (compose_validator 219 LOC, build_cache 280 LOC, generate_catalog 260 LOC, backup_collector 116 LOC — суммарно ~875 LOC production-кода без единого теста). Решения пользователя 2026-08-05: yaml_read.sh удалить немедленно (0 callsites — факт), 137/138 — предусловия, test_converge_exit — сверка W4-E5 страховок с последующим удалением.
ACCEPTANCE_CRITERIA:   (1) test_sequencing.py и test_converge_exit.py удалены, W4-E5 edge-страховки перенесены в unit/test_reconciler.py (сверка подтверждена diff-ревью); (2) 4 мёртвые фикстуры + yaml_read.sh удалены, строка yaml_read.sh снята из таблицы shell-исключений root AGENTS.md; (3) inventory-гейт зелёный после каждой волны удаления (changelog-записи + регенерация baseline); (4) 10 blind spot модулей имеют unit-тесты (каждый с IMP:9-траекторией); (5) 3 bash-теста переведены на Python-каноны; (6) healthcheck 4→2, module.yaml 7→3, 7 static/unit-пар консолидированы, ssh-раннеры дедублированы (verify_sweep импортирует из vps_readiness); (7) test_cross_layer_imports.py сокращён до direction-based сканирования; (8) TRAP-таксономия зафиксирована в словаре, BUGFIX/FIX/LOCAL/UPSTREAM/DRIFT/CARVE-OUT/DESIGN консолидированы; (9) make gate MODE=fast зелёный; (10) тестовое дерево легче на 15-20% без потери обнаруживаемости.
IMPLEMENTS:            Два forensic-аудита тестовой системы (2026-08-05, полный + 7-аудиторный); решения пользователя 2026-08-05 (yaml_read.sh, 137/138, test_converge_exit); DevPlan 138 W3 (render-monitoring hook, предусловие W4.7); DevPlan 119 D4 (yaml_read.sh Rev-условие); DevPlan 120 (страховки распиливания); DevPlan 131 (TRAP[DEBT] чистка как образец).
IMPACTS:               tests/ (удаление ~10 файлов, консолидация ~15, переписывание ~15, новые ~8), core/lib/yaml_read.sh (удаление), AGENTS.md + core/AGENTS.md (таблица shell-исключений, глоссарий), makefiles/ (bootstrap.mk:91 дубль), core/internal/bootstrap/deploy.sh (только тикет, не код), .kilo/agents/code.md (словарь TRAP-типов + RESOLVED-практика), tests/_conftest/counter.py (anti-loop), tests/test_inventory.yaml + test_inventory_changes.yaml (регенерация).
REQUIRES:              main зелёный; планы 137 и 138 влиты (предусловия, решение пользователя); make test-inventory-sync (процедура регенерации baseline); test-VPS доступен для e2e-прогонов (не блокирует, но желателен для финальной верификации); CI-доступ для make gate MODE=fast.
$END_ARTIFACT_CONTRACT

$START_DOCUMENT_PLAN
### Document Plan
**SECTION_GOALS:**
- GOAL [Зафиксировать синтез двух аудитов и результат верификации каждого пункта] => G1 (§1)
- GOAL [Зафиксировать 3 новые коррекции аудитов и 4 новых blind spot] => G2 (§2-§3)
- GOAL [Определить целевую архитектуру тестовой системы после cleanup] => G3 (§4)
- GOAL [Задать 5 волн с задачами, AC и зависимостями] => G4 (§5, DevPlan)
$END_DOCUMENT_PLAN

## 1. Синтез двух аудитов (2026-08-05)

Оба аудита (полный forensic + 7-параллельный) дают консистентный вердикт: 🟡 заметный технический долг, не кризис.

**Сходимость (обе модели, независимо):**

| Класс | Объём | Файлы |
|-------|-------|-------|
| P0 синтетика (ложная уверенность) | ~845 LOC | test_sequencing.py (202), test_converge_exit.py (643) |
| P1 слепые зоны | 3 модуля | scaffold/vhost_configurator.py (222), phases/system.py (655), render-monitoring hook (orphan) |
| P2 тесты реализации | ~25-30 файлов | private-методы, точные строки логов, внутренности моков |
| P2 дубли | 5-6 уровней | deploy, healthcheck (4-8 файлов), module.yaml (7+), ssh-раннеры (3 копии) |
| P3 процессные артефакты | ~35 | TRAP-таксономия, .test_counter.json ×2, anti-loop |

**Разночтения аудитов:** счёт тестов (3334 vs 3505 — реальный: 3505 nodeids в inventory, 4428 с параметризацией по collect), TRAP[TEST] (2429 vs 2437 — фактический 2429), TRAP-всего (~3137 vs ~3350 — фактический: 2429+406+360+12+10+9+7+6+4+4+3+2+1×4 ≈ 3255).

## 2. Коррекции аудитов (верификация против дерева)

### 2.1 Исправлено самим аудитом 1 (подтверждено)
- `_print_ldd_trajectory` — определение в 1 месте (tests/_conftest/ldd.py:34), 21 файл импортирует. НЕ дубль.
- `module_yaml_paths()` — 3 живых callsites в test_gate_env_shared_consistency.py. НЕ мёртв (аудит 2 ошибочно предлагал удаление).

### 2.2 Новые коррекции (результат верификации 139)
| # | Заявление аудита | Верификация | Вердикт |
|---|------------------|-------------|---------|
| C1 | «tronyx-site/ live через predeploy fallback — не трогать» (оба аудита) | Fallback-путь в коде — `tests/n/<name>/` (директории нет); `projects/tronyx-site/` — 0 ссылок по всему репо; git history — эпоха 051 | ❌ ЛОЖНО — директория мертва, удалять |
| C2 | «test-site недостижим» (оба аудита) | Директория `projects/test-site/` мертва (0 ссылок), НО имя test-site в tests/test_data/node.yaml ЖИВО (test_node_yaml_domains.py:94 ассертит test-site.example.com) | ⚠️ ЧАСТИЧНО — удалять только директорию, НЕ запись в node.yaml |
| C3 | «module_yaml_paths — 0 callsites, удалить» (аудит 2) | 3 callsites в живом гейте | ❌ ЛОЖНО — KEEP |
| C4 | «healthcheck/metrics — 4 модуля ~730 LOC» (аудит 1) | В metrics/ 7 файлов; без тестов — только backup_collector.py (116); docker/project/json_writer/cert/host имеют ссылки в тестах | ⚠️ УТОЧНЕНИЕ — цель W4 только backup_collector |

## 3. Новые blind spots (упущены обоими аудитами)

| Модуль | LOC | Вызывается | Уровень |
|--------|-----|------------|---------|
| core/internal/scaffold/compose_validator.py | 219 | project_adopter.py step 6 (adopt-флоу) | unit |
| core/internal/bootstrap/deploy/build_cache.py | 280 | docker_orchestrator.py | unit |
| core/internal/catalog/generate_catalog.py | 260 | project_yaml.py, deploy/orchestrator.py | unit |
| core/internal/healthcheck/metrics/backup_collector.py | 116 | healthcheck/metrics (рантайм) | unit |

Суммарно ~875 LOC production-кода без единого теста. Включаются в W4 (решение пользователя: максимальный фронт).

## 4. Решения пользователя (2026-08-05)

| Вопрос | Решение |
|--------|---------|
| yaml_read.sh (100 LOC, 0 callsites) | Удалить НЕМЕДЛЕННО (факт 0 callsites подтверждён; не ждать Rev-окна 2026-11-01); снять строку из таблицы shell-исключений AGENTS.md |
| Координация с планами 137/138 (авторизованы, не реализованы) | 137/138 — ПРЕДУСЛОВИЯ: МЕТА-план стартует после их влития; их тесты не попадают под консолидацию W3; 138 W3 даёт hook для интеграционного теста render-monitoring (W4.7) |
| test_converge_exit.py (643 LOC) | СВЕРКА + удаление: W4-E5 edge-страховки (drift-detection, reconcile idempotency, _is_stub edge, project-name validation) сверить с unit/test_reconciler.py; недостающие перенести (≤3 теста), затем удалить файл целиком |

$END_BRIEF
