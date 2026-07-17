---
description: 'Post-Refactor Recovery — full analysis and recovery after large refactoring:
  parallel audit via explore → problem registry → fix waves (Coder/Sysadmin → QA)
  → local launch → production gate'
name: post-refactor-recovery
---

# §SKILL
# SKILL: Post-Refactor Recovery

  Orchestrated recovery after a large refactoring swarm. 6 phases: Scope → Parallel Audit → Problem Registry → Fix Waves → Local Launch → Production Gate.

  ## Meta-Rules (из ретроспективы)

  Эти правила применяются ВО ВСЕХ фазах скилла. Они предотвращают целые классы ошибок, а не отдельные инциденты.

  1. **Batch audit, не последовательное чтение** — если файлов для аудита >5, НЕ читать последовательно. Запускать параллельных explore-агентов (3-4 шт.). Экономия: 30-40% токенов, 2x скорость.
  2. **Snapshot перед мутацией** — перед любым fix записать текущее состояние файла (git diff или checksum). Если fix сломал — откат по snapshot.
  3. **Pre-check перед fix** — перед написанием fix проверить актуальное состояние: доступность инструментов (shell, wget, curl), версии зависимостей, runtime-окружение. Не писать fix вслепую.
  4. **Один wave — один verify** — каждый wave фикса проходит полный цикл: Coder/Sysadmin → QA → Architect accept/reject. Не накапливать фиксы без верификации.
  5. **Defer вместо костыля** — если проблема требует архитектурного изменения, не писать временный workaround. Разместить `TRAP[DEBT]` и зафиксировать в {NN}-Debt.md (.ai/plans/NNN-slug/{NN}-Debt.md, NN = max existing NN + 1). Костыли становятся постоянными.

  ## Phase 0: SCOPE

  Определение границ проекта перед аудитом. Architect выполняет лично (1-2 сообщения).

  - Структура репозитория: `glob **/*` — общая картина
  - docker-compose файлы: все варианты (base, dev, prod, override)
  - Связанные репозитории: git submodules, монорепо структура
  - Переменные окружения: `.env`, `.env.*`, CI variables
  - Сценарии сборки: Makefile, Taskfile, package.json scripts
  - CI конфиги: `.github/workflows/`, `.gitlab-ci.yml`

  [IMP:7] Scope completed: {N} docker-compose files, {M} env files, {K} CI configs

  **Output:** список всех артефактов, подлежащих аудиту в Phase 1.

  ## Phase 1: PARALLEL AUDIT

  **ЗАПРЕЩЕНО читать файлы последовательно вручную, если файлов >5.** Использовать task-агентов с subagent_type="explore".

  Минимальный набор параллельных агентов (запускаются одновременно):

  | Agent | Scope | Glob | Grep | Returns |
  |-------|-------|------|------|---------|
  | explore-1 | configs + env | `**/*.yml, **/*.yaml, **/*.toml, **/*.env*` | `healthcheck|depends_on|volumes|networks|ports|environment` | Список конфигов с аннотациями: устаревшие, конфликтующие, дублированные |
  | explore-2 | docker + compose | `**/Dockerfile*, **/docker-compose*, **/entrypoint*` | `FROM |CMD |ENTRYPOINT |HEALTHCHECK |COPY |RUN ` | Образы, entrypoint, healthcheck, multi-stage builds |
  | explore-3 | tests + fixtures | `tests/**/*.py, **/conftest.py, **/pytest.ini` | `^def test_\|@pytest.mark\|caplog\|tmp_path\|TRAP\[` | Структура тестов, покрытие по категориям, устаревшие тесты |
  | explore-4 | CI + scripts (если есть CI) | `.github/workflows/*.yml, .gitlab-ci.yml, scripts/*.sh` | — | Pipeline stages, lint/test/build/deploy шаги |

  Каждый explore возвращает структурированный отчёт: найденные файлы, аномалии, конфликты.

  **Правило малых правок (исключение из batch audit):** если правка затрагивает ≤3 файла И не меняет архитектуру/API/схему БД — Architect может применить её напрямую без цикла Coder→QA. Пример: замена версии образа.

  [IMP:7] Parallel audit completed: {N} agents returned, {M} anomalies found

  ## Phase 2: PROBLEM REGISTRY

  Architect мерджит отчёты explore-агентов и формирует реестр проблем.

  | Problem ID | Category | Severity | Symptom | Root Cause | Files | Depend | Fix Agent |
  |------------|----------|----------|---------|------------|-------|--------|-----------|
  | P1 | docker | CRITICAL | Healthcheck failing | Next.js binds to container IP, not 0.0.0.0 | docker-compose.yml | — | Sysadmin |
  | P2 | config | HIGH | Alpine migration not applied | CI updated but compose not | docker-compose.yml | — | Sysadmin |

  **Severity scale:**
  - **CRITICAL** — prevents startup, blocks all work
  - **HIGH** — breaks tests, prevents deployment
  - **MEDIUM** — outdated config, missing lint, non-blocking
  - **LOW** — cosmetic, documentation

  **Правила сортировки:**
  1. CRITICAL проблемы блокируют всё — фиксить первыми
  2. Проблемы с зависимостями — фиксить после их зависимостей
  3. MEDIUM/LOW — в конец очереди или defer

  [IMP:8] Problem registry compiled: {N} problems (C: {X}, H: {Y}, M: {Z}, L: {W})

  **Output:** упорядоченный реестр проблем, сгруппированный в waves для Phase 3.

  ## Phase 3: FIX WAVES

  **Протокол одного wave:**

  ```
  1. SNAPSHOT — записать текущее состояние затрагиваемых файлов
     git diff {file} > .ai/snapshots/{wave-id}-{file}.diff
     (или checksum, если git недоступен)

  2. PRE-CHECK — перед написанием fix проверить:
     - Доступность инструментов в контейнере (shell, wget, curl)
     - Актуальные версии зависимостей
     - Runtime-окружение

  3. FIX — делегировать:
     - coder — для code-фиксов (.py, .ts, .js, тесты, миграции)
     - sysadmin — для infra-фиксов (Dockerfile, CI, compose, env)

  4. VERIFY — QA проверяет fix:
     - Статический анализ (синтаксис, типы)
     - Runtime-проверка (если применимо)
     - Отсутствие регрессий в затронутых тестах

  5. ACCEPT/REJECT — Architect принимает или отправляет на доработку
     - Accept → snapshot помечается как успешный
     - Reject → откат по snapshot, повтор цикла
  ```

  **Defer-механизм:**
  Если проблема требует архитектурного изменения или недели работы:
  - Разместить `TRAP[DEBT]` в затронутых файлах с описанием
  - Записать в {NN}-Debt.md (.ai/plans/NNN-slug/{NN}-Debt.md, NN = max existing NN + 1)
  - НЕ применять временный костыль

  **Исключение — малые правки:**
  Если fix затрагивает ≤3 файла И не меняет архитектуру/API/схему БД — Architect может применить напрямую (self-review). Основание: ретроспектива.

  [IMP:9] Wave {W} completed: fixed {F}, deferred {D}, remaining {R}

  ## Phase 4: LOCAL LAUNCH

  Цель: одна команда запускает всё приложение.

  **Протокол проверок (последовательно):**

  ```
  1. Lint:        pre-commit run --all-files (или эквивалент)
  2. Type-check:  mypy / tsc / etc.
  3. Unit:        pytest tests/ -m "not integration and not e2e" --collect-only → run
  4. Integration: pytest tests/ -m "integration" --collect-only → run
  5. Build:       docker compose build
  6. Up:          docker compose up -d
   7. Migrations:  docker compose exec &lt;svc&gt; &lt;migrate-cmd&gt;
  8. Health:      docker compose ps (все healthy)
  9. Smoke:       curl/wget ключевых endpoints
  ```

  **Протокол тестов:**
  - Перед КАЖДЫМ прогоном: `pytest --collect-only` → определить затронутые тесты
  - Прогонять ТОЛЬКО затронутые тесты (не полный suite)
  - Полный suite — только в финальной верификации

  **Если запуск не удался:**
  - Определить, какая именно проверка провалилась
  - Создать новую проблему в реестре (вернуться к Phase 2-3)
  - НЕ продолжать с проваленным запуском

  [IMP:10] Local launch: ALL services healthy, ALL tests passing

  ## Phase 5: PRODUCTION GATE (шаблон)

  Минимальный набор проверок для разрешения деплоя. Если хотя бы один пункт провален — deployment запрещён.

  | # | Check | Severity | Details |
  |---|-------|----------|---------|
  | 1 | Container builds reproducibly | CRITICAL | 2 последовательных build → идентичный образ |
  | 2 | Container starts without errors | CRITICAL | docker compose up --wait |
  | 3 | Healthcheck passes | CRITICAL | docker compose ps → все healthy |
  | 4 | Readiness probe passes | HIGH | Если настроен |
  | 5 | Application responds on expected port | CRITICAL | curl localhost:{port} |
  | 6 | Migrations apply cleanly (forward + rollback) | CRITICAL | Тест rollback после forward |
  | 7 | Database accessible from application | CRITICAL | Проверка подключения |
  | 8 | Cache (Redis/Memcached) accessible | HIGH | Если используется |
  | 9 | Queues operational | MEDIUM | Если используются |
  | 10 | Critical API endpoints respond 2xx | CRITICAL | smoke-тест |
  | 11 | Logs contain no CRITICAL/ERROR on startup | HIGH | docker compose logs |
  | 12 | Rollback plan documented and tested | MEDIUM | Документация |

  [IMP:10] Production gate: {passed/failed}, blocking items: {list}

  ## Appendix A: Container Verification Checklist (reference)

  ### Обязательные проверки (CRITICAL)

  ```
  ☐ Сборка образа без ошибок
  ☐ Воспроизводимость сборки (2 последовательных build → идентичный результат)
  ☐ Корректный ENTRYPOINT/CMD
  ☐ Startup без ошибок
  ☐ Healthcheck работает и возвращает success
  ☐ Подключение к зависимостям (DB, cache, queues)
  ```

  ### Рекомендуемые проверки (HIGH)

  ```
  ☐ Graceful shutdown (SIGTERM)
  ☐ Restart (docker compose restart)
  ☐ Миграции выполняются при старте
  ☐ Конфигурация через ENV валидируется
  ☐ Volume permissions корректны
  ```

  ### Опционально (MEDIUM)

  ```
  ☐ Поведение при отсутствии зависимостей (wait-for-it / healthcheck dependency)
  ☐ Логирование структурировано (JSON)
  ☐ Метрики экспортируются
  ☐ Поведение после обновления образа
  ```

  ## Appendix B: Agent-to-Role Mapping

  | Промт-агент | Реальная роль | Обоснование |
  |-------------|---------------|-------------|
  | архитектор | architect (plan) | Оркестратор, формирует реестр, принимает fix |
  | backend | coder | Исправление кода, тестов |
  | frontend | coder | Исправление кода, тестов |
  | DevOps | sysadmin | Docker, CI, инфраструктура |
  | инфраструктура | sysadmin | Docker, compose, сети |
  | Docker | sysadmin | Dockerfile, entrypoint, healthcheck |
  | PostgreSQL | coder или general | Миграции, схемы |
  | тестовая инфраструктура | coder + qa | Создание тестов + независимая проверка |
  | интеграционные тесты | coder | Написание тестов |
  | e2e | coder | Написание тестов |
  | CI/CD | sysadmin | GitHub Actions, GitLab CI |
  | observability | sysadmin | Prometheus, Grafana, логи |
  | security | sysadmin или coder | gitleaks, trivy, зависимости |
  | qa/reviewer | qa | Независимая проверка каждого fix |

  ## Agent Assignment (Fix Waves)

  | Wave Type | Agent | Validation | Acceptance |
  |-----------|-------|------------|------------|
  | Config fix (.yml, .env, .toml) | Sysadmin | QA | Architect |
  | Code fix (.py, .ts, .js) | Coder | QA | Architect |
  | Dockerfile/entrypoint fix | Sysadmin | QA | Architect |
  | CI/GitHub Actions fix | Sysadmin | QA | Architect |
  | Test fix/creation | Coder | QA | Architect |
  | Migration fix | Coder | QA | Architect |
  | Documentation fix | Coder | — (lightweight) | Architect |
  | Small fix (≤3 files, no arch impact) | Architect (direct) | — (self-review) | — |

<!-- ai-instructions:0.5.16 -->
