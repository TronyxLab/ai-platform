# 07-Brief — B9: SRP-декомпозиция монолитов

<!-- GREP_SUMMARY: SRP state_machine reconciler project_adopter private-api deploy_orchestrator monolith decomposition -->
<!-- STRUCTURE: ┌scope┐ → ◇ state_machine → ◇ reconciler → ◇ project_adopter → ◇ приватные API → ⊕ критерии → ⎋ зависимости -->
# region MODULE_CONTRACT
## @purpose  Волна B9: декомпозиция монолитов core/internal по единой ответственности (SRP).
## @scope    U-07, U-08, U-28, U-31, U-32
## @invariants
##   - Мораторий на структурные правки state_machine.py действовал до этой волны; здесь — плановая декомпозиция после стабилизации тестов.
##   - Публичные API — через __init__.py; приватные функции не используются между модулями (гейт).
# endregion MODULE_CONTRACT

$ARTIFACT_CONTRACT:
  PURPOSE: Разбить 4 монолита (state_machine 2392, reconciler 2312, deploy_orchestrator, project_adopter 1131) на модули по ответственностям и легализовать межмодульные контракты.
  DESCRIPTION: Выделение I/O/валидации/CLI из state_machine; 8 доменов reconciler по модулям; публикация 7 приватных API deploy_orchestrator; консолидация reconcile-каналов (is_stub дубликат); project_adopter по ответственностям.
  RATIONALE: RC1: Strangler перенёс логику в Python, но не декомпозировал её внутри core/internal; приватные API стали де-факто контрактом (7 импортов underscore-функций); state_machine переписан 12 волнами, 76% fix-коммитов.
  ACCEPTANCE_CRITERIA: (1) state_machine.py ≤ 1200 LOC: оркестрация + state persistence; I/O (apt/users/ssh/secrets) — в phases/helpers; цикл phases↔state_machine устранён (односторонняя зависимость); (2) reconciler.py: домены вынесены (R1-R9, sudoers, vhosts, volumes); (3) deploy_orchestrator: 7 функций опубликованы через __init__ или вызываются через публичные API; (4) is_stub — одна функция в shared; reconcile-каналы консолидированы (или --reconcile задокументирован как deprecated); (5) project_adopter: генераторы YAML через scaffold_helpers (дубль gen_ai_platform_yaml удалён); (6) все тесты зелёные; e2e bootstrap-пайплайн не регрессировал.
  IMPLEMENTS: U-07 (7 приватных API), U-08 (state_machine-монолит), U-28 (is_stub дубль + reconcile), U-31 (reconciler), U-32 (project_adopter)
  IMPACTS: core/internal/bootstrap/lifecycle/{state_machine,phases}.py, core/internal/bootstrap/converge/reconciler.py, core/internal/bootstrap/deploy/deploy_orchestrator.py, core/internal/scaffold/project_adopter.py, core/internal/deploy/reconciler_projects.py
  REQUIRES: B8 (dead-code удалён — декомпозиция на чистой базе), B5 (операционные политики в shared), B4 (типизированные исключения)

---

## Scope

| U | Проблема | Ключевые файлы |
|---|----------|----------------|
| U-08 | state_machine 2392 LOC: оркестрация+persistence+subprocess+валидация+CLI; цикл с phases.py | state_machine.py, phases.py:62,819-835 |
| U-31 | reconciler 2312 LOC: 8 доменов | bootstrap/converge/reconciler.py |
| U-07 | deploy_orchestrator: 7 приватных API сиблингов | deploy_orchestrator.py:264,437,444,580,702,714,771 |
| U-28 | is_stub_project vs _is_stub — идентичный алгоритм; параллельный reconcile-канал | reconciler_projects.py:147,159-166, bootstrap/converge/reconciler.py:834-845, converge.sh:104,125-137 |
| U-32 | project_adopter 1131 LOC: 6 ответственностей; gen_ai_platform_yaml дубль | project_adopter.py:182,203 |

## Ключевые артефакты

1. state_machine: выделить (а) state persistence (state.json), (б) subprocess I/O хелперы, (в) CLI/main, (г) валидацию; phases.py — единственный исполнитель шагов; зависимость односторонняя phases → state_machine (или общий интерфейс). Без изменения семантики фаз (14 фаз не трогаем).
2. reconciler: домены → core/internal/bootstrap/converge/{perms,audit,projects,networks,vhosts,volumes,sudoers,runtime}.py (или internal/domain/), reconciler.py — оркестратор R1-R9.
3. deploy_orchestrator: экспорт через bootstrap/deploy/__init__.py; переименование 7 приватных функций в публичные с контрактами; тест «нет underscore-импортов между модулями».
4. is_stub: одна функция в shared (по пути/контенту); reconciler_projects использует её.
5. project_adopter: генерация YAML через scaffold_helpers; вынос compose-валидации и vhost-логики.
6. Мораторий снят TRAP'ом; e2e-прогон (make test-node) обязателен.

## Гейт самоверификации волны

- Гейт «нет приватных межмодульных импортов» (ruff или ast-скан: `_`-префикс вне модуля).
- LOC-гейт (allowlist): state_machine ≤ 1200, reconciler ≤ 800.

## Зависимости

- От: B8 (чистая база), B5, B4.
- К: B10 (тесты фиксируют новые границы), B11 (гейты приватных API).
