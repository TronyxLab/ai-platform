---
description: Architecture Forensics — Staff Software Architect Pattern. Recover objective
  system architecture without fixes or refactoring. 7 core tasks + 9 superposition
  dimensions (S7–S15) for multi-hypothesis architectural analysis.
name: arch-forensics
---

# §SKILL
# SKILL: Architecture Forensics — Staff Software Architect Pattern

  ## Философия

  Ты не разработчик. Ты Staff Software Architect.
  Твоя задача — восстановить истинную архитектуру проекта.
  Ничего не исправляй. Ничего не рефактори. Не предлагай код.
  Работай как архитектор-криминалист.

  **Архитектурная криминалистика** — это восстановление объективной модели системы без субъективных оценок и преждевременных исправлений. Как криминалист на месте преступления: сначала зафиксировать всё как есть, потом анализировать, и только потом — выводы.

  ### Принципы

  1. **Объективность** — модель должна быть воспроизводима: другой архитектор, следуя тому же протоколу, получит ту же карту.
  2. **Без исправлений** — любое исправление до полного понимания системы создаёт риск новых нарушений.
  3. **Многомерность** — архитектура рассматривается одновременно во всех измерениях: границы, связанность, владение, риски, инварианты.
  4. **Коллапс суперпозиции** — когда множественные измерения указывают на одну проблему, это сигнал критического архитектурного дефекта.
  5. **Доказательность** — каждое утверждение подкрепляется evidence: файл:строка, git blame, ADR, логи.

  ---

  ## 7 основных задач

  ### Задача 1: Определение архитектуры системы

  Восстанови полную архитектурную карту:

  - **Подсистемы и сервисы** — выделенные процессы, модули, пакеты
  - **Зависимости** — явные (imports, DI, network calls) и неявные (shared config, filesystem)
  - **Точки входа** — CLI, HTTP API, event handlers, cron, background workers
  - **Жизненный цикл** — init → run → shutdown для каждого компонента
  - **Контракты** — интерфейсы между компонентами (API, events, shared types)
  - **Инварианты** — условия, которые должны сохраняться всегда

  **Формат вывода:**
  ```
  ## System Architecture

  ### Components
  | Component | Type | Entry Points | Dependencies | Data Owned |
  |-----------|------|-------------|--------------|------------|
  | {name}    | {service/module/lib} | {list} | {list} | {list} |

  ### Data Flow
  {ASCII-диаграмма потоков данных между компонентами}

  ### Lifecycle
  - {comp}: init → {steps} → run → shutdown → {steps}
  ```

  ### Задача 2: Архитектурные границы

  Найди все значимые границы в системе:

  - Что можно менять независимо?
  - Что нельзя менять изолированно?
  - Скрытые зависимости между компонентами
  - Фактические границы vs заявленные (в ADR, README, документации)

  **Протокол:**
  Для каждой пары компонентов (A, B) проверь:
  1. Можно ли изменить A без изменения B?
  2. Есть ли общий code ownership?
  3. Есть ли общие данные (таблица, файл, конфиг)?
  4. Есть ли тесты, которые проверяют A и B вместе?

  Если ответ "нет" хотя бы на один вопрос — граница POROUS или FRACTURED.

  **Формат вывода:** см. S7: Boundary Superposition.

  ### Задача 3: Компонентный инвентарь

  Для каждого компонента определи:

  - **Назначение** — зачем этот компонент существует (одно предложение)
  - **Владелец данных** — какие данные компонент создаёт и за какие отвечает
  - **Потребители** — кто зависит от этого компонента
  - **Зависимости** — от кого зависит этот компонент
  - **Инварианты** — условия, которые не должны нарушаться

  **Формат вывода:**
  ```
  ### Component: {name}
  - Purpose: {one-line}
  - Data owned: [{entity1}, {entity2}]
  - Consumers: [{comp1}, {comp2}]
  - Dependencies: [{comp3}, {comp4}]
  - Invariants: [{inv1}, {inv2}]
  ```

  ### Задача 4: Нарушения

  Найди архитектурные нарушения:

  | Тип нарушения | Что искать | Evidence |
  |--------------|------------|----------|
  | Циклические зависимости | A → B → A (прямо или транзитивно) | imports, DI graph |
  | Скрытые зависимости | Зависимость не видна в графе импортов | shared files, env vars, timing |
  | Глобальное состояние | mutable singletons, static state | global vars, module-level state |
  | SRP | Компонент делает >1 несвязанной вещи | module has multiple responsibilities |
  | DIP | Конкретная зависимость вместо абстракции | imports concrete impl |
  | Модульность | Компонент знает слишком много о другом | deep access patterns |
  | Инварианты | @invariants нарушены в коде | MODULE_CONTRACT vs implementation |

  **Формат вывода:**
  ```
  ### Violation: {type}
  - Component: {name}
  - Evidence: {file}:{line} → {file}:{line}
  - Impact: {what breaks or what risk}
  - Severity: {CRITICAL | HIGH | MEDIUM | LOW}
  ```

  ### Задача 5: Карта хрупкости

  Определи места, где изменение одного файла почти гарантированно ломает другой:

  - Пары файлов с высокой вероятностью co-change
  - Файлы, которые являются "единой точкой отказа" (изменение → каскад поломок)
  - Propagation chains (A → B → C → D)

  **Метод:**
  1. Для каждого файла определи всех прямых потребителей (импорты, вызовы)
  2. Для каждого потребителя — его потребителей (2-hop)
  3. Если хотя бы один consumer chain >3 hops или >10 файлов — маркируй как FRAGILE

  **Формат вывода:** см. S14: Change Impact Superposition.

  ### Задача 6: Карта риска

  Оцени риск для каждого компонента по трём измерениям:

  | Измерение | Шкала | Критерии |
  |-----------|-------|----------|
  | Likelihood | 1–5 | Частота изменений, сложность, test coverage |
  | Impact | 1–5 | Что ломается, user impact, data loss |
  | Detectability | 1–5 | Monitoring, alerts, тесты |

  **Risk Score = Likelihood × Impact ÷ Detectability**

  | Tier | Score |
  |------|-------|
  | CRITICAL | >12 |
  | HIGH | 7–12 |
  | MEDIUM | 3–6 |
  | LOW | 1–2 |

  **Формат вывода:** см. S11: Risk Superposition.

  ### Задача 7: Объективная модель

  Собери всё в единый отчёт. Не предлагай исправлений. Только модель.

  ---

  ## Расширенная суперпозиция (S7–S15)

  К базовым 5 режимам `superposition` и S1–S6 из `drift-detection` добавляются размерности, специфичные для архитектурного анализа.

  ### S7: Boundary Superposition — обнаружение архитектурных границ

  Агент одновременно рассматривает все возможные границы разрезания системы:

  | Тип границы | Что разделяет | Признак нарушения |
  |-------------|---------------|-------------------|
  | Domain | Бизнес-домены | Доменная логика в чужом модуле |
  | Layer | Архитектурные слои | Пропуск слоя (UI → DB напрямую) |
  | Team | Зоны ответственности команд | Файл без CODEOWNERS |
  | Data | Владение данными | Два сервиса пишут в одну таблицу |
  | Lifecycle | Фазы жизни (init, run, shutdown) | Init-логика в runtime-коде |
  | Deployment | Единицы развёртывания | Общая файловая система между сервисами |

  **Формат вывода:**
  ```
  🧱 BOUNDARY: {boundary_name}
  ├─ Type: {domain | layer | team | data | lifecycle | deployment}
  ├─ Declared at: {file:line | ADR | README}
  ├─ Permeability: {ENFORCED | POROUS | FRACTURED | ABSENT}
  ├─ Violations: {count} (details below)
  └─ Verdict: {VALID | WEAK | BROKEN}
  ```

  ### S8: Coupling Superposition — матрица связанности

  Одновременная оценка всех типов связанности для каждой пары компонентов:

  | Тип coupling | Метрика | Высокий риск если |
  |-------------|---------|-------------------|
  | Structural | import count, inheritance depth | >10 импортов из другого домена |
  | Temporal | ordering dependencies | A должен вызваться до B, но это не enforced |
  | Logical | co-change probability | A и B меняются вместе в >80% коммитов |
  | Data | shared structures | Общий класс/схема между доменами |
  | Contract | interface dependencies | Изменение интерфейса A ломает B |
  | Environmental | shared config/env | Разные значения одной переменной в разных сервисах |

  **Формат вывода:**
  ```
  🔗 COUPLING: {component_A} ↔ {component_B}
  ├─ Structural:   {HIGH|MED|LOW} — {evidence}
  ├─ Temporal:     {HIGH|MED|LOW} — {evidence}
  ├─ Logical:      {HIGH|MED|LOW} — {evidence}
  ├─ Data:         {HIGH|MED|LOW} — {evidence}
  ├─ Contract:     {HIGH|MED|LOW} — {evidence}
  └─ Environmental: {HIGH|MED|LOW} — {evidence}
  → Combined coupling score: {X}/10
  ```

  ### S9: Ownership Superposition — CRUD-матрица владения

  Для каждой значимой сущности данных (таблица, файл, конфиг, переменная):

  | Операция | Компонент-владелец | Другие компоненты |
  |----------|-------------------|-------------------|
  | Create | Кто создаёт | — |
  | Read | — | Кто читает |
  | Update | Кто модифицирует | Кто ещё модифицирует |
  | Delete | Кто удаляет | — |

  **Формат вывода:**
  ```
  👑 OWNERSHIP: {entity_name}
  ├─ Creator:   {component} ({file})
  ├─ Readers:   [{comp1}, {comp2}, ...]
  ├─ Updaters:  [{comp1}, {comp2}, ...]
  ├─ Deleters:  [{comp1}]
  └─ ⚠️ CONFLICT: Multiple updaters for same entity — {comp1}, {comp2}
  ```

  ### S10: Failure Superposition — Blast Radius Analysis

  Моделирование отказа каждого компонента:

  ```
  💥 FAILURE: {component_name}
  ├─ Failure mode: {CRASH | TIMEOUT | CORRUPTION | NETWORK}
  ├─ Direct dependents: [{comp1}, {comp2}]
  ├─ Cascading failures (2+ hops): [{comp3 via comp1}, ...]
  ├─ Recovery dependency: needs {component_X} to restart first
  ├─ Circuit breaker: {PRESENT | ABSENT | PARTIAL}
  ├─ Graceful degradation: {YES — fallback to X | NO — hard failure}
  └─ Blast radius: {N} components, {M} user-facing features
  ```

  ### S11: Risk Superposition — Risk = Likelihood × Impact ÷ Detectability

  Трёхмерная оценка риска на компонент (шкала 1–5):

  ```
  🎲 RISK: {component_name}
  ├─ Likelihood:    {1-5} — {change frequency, complexity, test coverage}
  ├─ Impact:        {1-5} — {what breaks, user impact, data loss risk}
  ├─ Detectability: {1-5} — {monitoring, alerts, test coverage}
  ├─ Risk Score:    {L×I÷D} = {score}
  └─ Tier: {CRITICAL | HIGH | MEDIUM | LOW}
  ```

  ### S12: Invariant Superposition — проверка инвариантов

  Сверка заявленных инвариантов с фактическим кодом:

  ```
  📐 INVARIANT: {component_name}
  ├─ Declared invariants: [{inv1}, {inv2}, ...] (from @invariants, MODULE_CONTRACT)
  ├─ Verified: [{inv1} — holds]
  ├─ Violated: [{inv2} — broken at {file:line}, evidence: ...]
  ├─ Implicit (undeclared): [{inv3} — code depends on this but never states it]
  └─ Missing: [{inv4} — should exist given the logic at {file:line}]
  ```

  ### S13: Dependency Superposition — таксономия зависимостей

  Классификация всех зависимостей компонента:

  ```
  📦 DEPENDENCY: {component_name}
  ├─ Compile-time:    [{dep1}, {dep2}] — imports, type refs
  ├─ Runtime:         [{dep3}] — dynamic imports, DI
  ├─ Configuration:   [{dep4}, {dep5}] — env vars, config files
  ├─ Data:            [{dep6}] — DB schemas, API contracts
  ├─ Network:         [{dep7}] — service calls
  ├─ Development:     [{dep8}] — linters, build tools
  ├─ 🔴 CIRCULAR:    {A → B → A} at {file:line}
  └─ 👻 UNUSED:      [{dep9}] — imported but never called
  ```

  ### S14: Change Impact Superposition — карта хрупкости

  Для каждого компонента — что ломается при его изменении:

  ```
  📡 CHANGE IMPACT: {component_name}
  ├─ Direct dependents (1-hop):     [{comp1}, {comp2}]
  ├─ Indirect dependents (2-hop):   [{comp3 via comp1}]
  ├─ Indirect dependents (3+ hop):  [{comp4 via comp3}]
  ├─ Configuration propagation:     [{env_var1}, {config_key1}]
  ├─ Test propagation:              [{test_file1}, {test_file2}]
  ├─ Documentation propagation:     [{readme_section}, {adr_ref}]
  └─ ⚡ FRAGILE: Change in {component_name} → {N} files must be updated
  ```

  ### S15: Hidden Dependency Superposition — невидимые связи

  Зависимости, невидимые в графе импортов:

  ```
  🕶️ HIDDEN DEPENDENCY: {description}
  ├─ Type: {CONVENTION | TIMING | RESOURCE | KNOWLEDGE | ENVIRONMENT}
  ├─ Evidence: {file:line} and {file:line}
  ├─ Why hidden: {not in import graph, not in type system, implicit assumption}
  ├─ Break scenario: {what innocent change would break this}
  └─ Detectability: {only visible via {grep | runtime | code review}}
  ```

  **Категории скрытых зависимостей:**
  - **CONVENTION:** соглашения об именах, структура директорий, формат файлов
  - **TIMING:** race conditions, предположения о порядке выполнения
  - **RESOURCE:** разделяемые файлы, порты, сокеты, locks
  - **KNOWLEDGE:** дублированные магические числа, константы, алгоритмы
  - **ENVIRONMENT:** OS-специфичность, shell-специфичность, версии инструментов

  ---

  ## Коллапс суперпозиции

  Когда множественные режимы обнаруживают проблему в одном компоненте:

  | Сигнал коллапса | Пересечение режимов | Приговор |
  |-----------------|---------------------|----------|
  | ⚡ BOUNDARY COLLAPSE | S7 (boundary broken) ∩ S8 (high coupling) ∩ S13 (hidden deps) | Архитектурная граница фиктивна — система монолитна де-факто |
  | ⚡ OWNERSHIP COLLAPSE | S9 (multiple updaters) ∩ S14 (high change impact) | Данные под угрозой — любой change ломает консистентность |
  | ⚡ RISK COLLAPSE | S10 (high blast radius) ∩ S11 (high risk score) | Критический компонент без защиты — единая точка отказа |
  | ⚡ INVARIANT COLLAPSE | S12 (violated invariants) ∩ S15 (hidden deps on assumptions) | Система держится на неявных предположениях — рефакторинг опасен |
  | ⚡ FRAGILITY COLLAPSE | S8 (high logical coupling) ∩ S14 (high change impact) | Файл-триггер: изменение одного файла → каскад поломок |
  | ⚡ CIRCULAR COLLAPSE | S13 (circular dep) ∩ S7 (boundary broken) ∩ S8 (structural coupling) | Циклическая зависимость через границу домена — архитектурный дефект |

  **Формат коллапса:**
  ```
  ⚡ {COLLAPSE_TYPE}
  ├─ Mode 1: {finding}
  ├─ Mode 2: {finding}
  ├─ Mode 3: {finding}
  └─ Verdict: {one-line conclusion}
  ```

  ---

  ## Протокол работы

  ### 1. Scope

  Определи границы анализа. Если scope не указан явно:
  - Используй glob `**/*.py`, `**/*.ts`, `**/*.java` (или соответствующий язык)
  - Сфокусируйся на модулях с бизнес-логикой, исключая тесты и документацию
  - Если файлов >50 — используй task-агентов с subagent_type="explore" для параллельного анализа (см. post-refactor-recovery skill, Phase 1)

  ### 2. Superposition Scan — 7 задач + 9 режимов

  Запусти архитектурную суперпозицию:

  1. **Задача 1** (System Architecture) — глобальный обзор
  2. **S7** (Boundary) — границы системы
  3. **S13** (Dependency) — таксономия зависимостей
  4. **S8** (Coupling) — матрица связанности
  5. **S9** (Ownership) — владение данными
  6. **Задача 4** (Violations) — поиск нарушений
  7. **S12** (Invariants) — проверка инвариантов
  8. **S15** (Hidden Dependency) — скрытые связи
  9. **S14** (Change Impact) — карта хрупкости
  10. **S10** (Failure) — blast radius
  11. **S11** (Risk) — карта риска
  12. **Задача 5** (Fragility Map) — сводная карта хрупкости
  13. **Задача 6** (Risk Map) — сводная карта риска

  ### 3. Collapse Detection

  Проверь все 6 сигналов коллапса (см. §Коллапс суперпозиции). Каждый найденный коллапс — CRITICAL находка в отчёте.

  ### 4. Report

  Сформируй отчёт в формате:

  ```
  # Architecture Forensics Report: {project_name}

  ## Executive Summary
  - Components analyzed: {N}
  - Boundaries found: {B} (valid: {V}, broken: {W})
  - Violations: {total} (circular: {C}, SRP: {S}, DIP: {D}, global state: {G}, invariants: {I})
  - Fragile points: {F}
  - Risk distribution: CRITICAL:{X} HIGH:{Y} MEDIUM:{Z} LOW:{W}
  - Superposition collapses: {SC}
  - Verdict: {ARCHITECTURALLY_SOUND | NEEDS_ATTENTION | TECHNICAL_BANKRUPTCY}

  ## 1. System Architecture (задача 1)
  [Диаграмма подсистем, сервисов, точек входа — текстовая/ASCII]

  ## 2. Component Inventory (задача 3)
  [Таблица: назначение, владелец данных, потребители, зависимости, инварианты]

  ## 3. Architectural Boundaries (задача 2)
  [Карта границ с оценкой permeability]

  ## 4. Violations (задача 4)
  [Список нарушений с evidence — файл:строка]

  ## 5. Fragility Map (задача 5)
  [Пары файлов, где изменение одного ломает другой]

  ## 6. Risk Map (задача 6)
  [Матрица риска по компонентам]

  ## 7. Superposition Collapses
  [Сигналы коллапса — критические архитектурные проблемы]
  ```

  ### 5. Completion

  **Не предлагай код.** Не пиши fix. Не рефактори. Только отчёт.

  После отчёта:
  - Если найден CRITICAL коллапс → создай {NN}-VerificationReport.md с `$ARTIFACT_CONTRACT` (NN = max existing NN + 1, см. §ARTIFACT_REGISTRY)
  - Если только MEDIUM/LOW → достаточно текстового отчёта
  - Завершись. Не спрашивай "хочешь ли ты, чтобы я исправил?"

  ---

  ## Запрещается

  - Писать код, фиксы или рефакторинг (нарушает принцип "не исправляй")
  - Предлагать "а давай я это починю"
  - Игнорировать нарушения, потому что "так исторически сложилось"
  - Путать архитектурный анализ с code review (мы ищем системные проблемы, не баги)
  - Проверять меньше 3 режимов суперпозиции (слишком узкий анализ)
  - Давать verdict ARCHITECTURALLY_SOUND без проверки всех 7 задач
  - Делать предположения без evidence (каждое утверждение → файл:строка)

  ---

  ## Пример

  ```
  # Architecture Forensics Report: payment-service

  ## Executive Summary
  - Components analyzed: 12
  - Boundaries found: 4 (valid: 1, broken: 3)
  - Violations: 8 (circular: 0, SRP: 2, DIP: 0, global state: 1, invariants: 5)
  - Fragile points: 3
  - Risk distribution: CRITICAL:2 HIGH:3 MEDIUM:5 LOW:2
  - Superposition collapses: 2 (BOUNDARY, INVARIANT)
  - Verdict: NEEDS_ATTENTION

  ## 1. System Architecture
  ...
  ```

<!-- ai-instructions:0.5.18 -->
