# GREP_SUMMARY: DevPlan, smoke-test-recovery, busybox-digest, hermes-image-tag, stale-container, compose-ps-dedup, fixture-hardening
# STRUCTURE: ▶ ┌3 само-регрессии + 1 тестовый баг┐ → ◇ contracts → ⊕ $TASKS (T1-T4) → ⊕ $PARALLEL_GROUPS (2 waves) → ⟦$TEST_SPEC⟧ → ⎋ next steps

$START_DEVPLAN

## $ARTIFACT_CONTRACT
- **PURPOSE:** Восстановить smoke-тесты (`test_platform_starts_all_containers`, `test_critical_services_healthy`) после 3 self-inflicted регрессий, внесённых коммитами `b1135b3c` (2026-07-18, «pre-release hardening») и `f2a7511` (2026-07-17, «bootstrap lifecycle»).
- **DESCRIPTION:** (D1) busybox:1.36 digest `sha256:7cae1f9...` retired с Docker Hub — заменить на актуальный; (D2) hermes-agent-context:v2026.7.1 не существует на ghcr.io — сменить default tag на `latest`; (D3) stale `nginx-test` контейнер от предыдущего crash'а не чистится pre-cleanup'ом — добавить `docker rm -f` safety net в fixture; (D4 — тестовый баг) `docker compose ps --all` возвращает все контейнеры проекта для каждого модуля — заменить на `--status running` и дедуплицировать expected_services.
- **RATIONALE:** Q: почему сейчас? A: D1+D2 — регрессии из коммита сегодня и вчера; ломают `make test`/`make gate MODE=full` на macOS. D3 — fixture не idempotent после crash'а. D4 — тестовый баг маскирует реальные проблемы за шумом (144 expected вместо 16, minio-createbuckets ×9 false negatives).
- **ACCEPTANCE_CRITERIA:** `make gate MODE=fast` зелёный; `python -m pytest tests/test_smoke_platform.py -s -v -m smoke` зелёный (все 6 тестов); nginx, monitoring, hermes-agent в `started`; нет контейнеров в restart loop.
- **IMPLEMENTS:** Диагноз из `.ai/plans/012-smoke-test-recovery/` (bug-report).
- **IMPACTS:** core/modules/monitoring/docker-compose.base.yml (1 строка), core/modules/hermes-agent/docker-compose.base.yml (1 строка), tests/_conftest/smoke.py (~15 строк), tests/test_smoke_platform.py (~10 строк).
- **REQUIRES:** Docker daemon running; НЕ требует доступа к ghcr.io (hermes-agent image pull опционален — fallback на локальный `:latest`); НЕ требует доступа к Docker Hub на момент правки (только digest update).

---

## Requirements Analysis — критерии успеха

1. **D1:** `docker pull busybox:1.36` успешен; актуальный digest зафиксирован в `docker-compose.base.yml`; `docker compose -f core/modules/monitoring/docker-compose.base.yml -f core/modules/monitoring/docker-compose.test.yml -p ai-platform-test config` валиден.
2. **D2:** `hermes-agent-context:latest` доступен локально (уже есть) ИЛИ `docker pull ghcr.io/tronyxlab/hermes-agent-context:latest` успешен; default tag в compose — `latest`; комментарий инварианта соответствует реальному тегу.
3. **D3:** Повторный запуск smoke-тестов не падает с «container name already in use»; fixture `platform_services` чистит stale контейнеры даже после crash'а предыдущего run'а.
4. **D4:** `expected_services` не содержит дубликатов (≤16 уникальных контейнеров, не 144); `minio-createbuckets` (one-shot exit 0) не попадает в `expected_services`; DIAG-логи показывают реальное количество сервисов.

## Верифицированный контекст (диагностический прогон этой сессии)

| # | Факт | Где |
|---|------|-----|
| C1 | busybox digest `sha256:7cae1f9...` — `manifest verification failed` на Docker Hub. Актуальный: `sha256:73aaf09...`. Строка добавлена сегодня (`b1135b3c`, blame L34). | core/modules/monitoring/docker-compose.base.yml:34 |
| C2 | `ghcr.io/tronyxlab/hermes-agent-context:v2026.7.1` — `not found`. `:latest` — существует (linux/amd64). Тег `v2026.7.1` установлен вчера при создании файла (`f2a7511`, new file). | core/modules/hermes-agent/docker-compose.base.yml:64 |
| C3 | `nginx-test` контейнер создан 2026-07-18T04:46 UTC (до коммита `b1135b3c` 04:53 UTC), статус `Up`. Pre-cleanup в `platform_services` не удалил — compose не признал контейнер «своим» (метки проекта не совпали с текущим набором compose-файлов). | tests/_conftest/smoke.py:582-592 |
| C4 | `docker compose ps --all -p ai-platform-test` возвращает ВСЕ 16 контейнеров проекта для каждого модуля (проверено DIAG-логами). 9 модулей × 16 контейнеров = 144 expected. `minio-createbuckets` — один экземпляр, exited, попадает во все 9 запросов → 9 false negatives. | tests/test_smoke_platform.py:351-375 |
| C5 | `docker compose ps --status running` фильтрует по состоянию (подтверждено: `docker compose ps --help` показывает `--status stringArray`). | Локальная проверка |
| C6 | Локально есть `hermes-agent-context:latest` (sha256:4628109a...), `hermes-agent-base:latest` (sha256:14836ad...). | `docker images --filter reference=*hermes*` |
| C7 | litellm в моём прогоне в списке `started: ['redis', 'clickhouse', 'minio', 'postgres', 'logging', 'backup-cron', 'infra-metrics', 'langfuse', 'litellm']`. Падение litellm могло быть transient (pull image timeout) или уже исправлено. | Вывод теста |

## §Contracts (формализация ДО имплементации)

### Contract 1 — busybox digest update (D1)
```
Файл: core/modules/monitoring/docker-compose.base.yml:34
Было:  image: busybox:1.36@sha256:7cae1f9e99a6efea08d36a15759d28c6542e6d7e4d1c4c90e912ef47fa686465
Стало: image: busybox:1.36@sha256:73aaf090f3d85aa34ee199857f03fa3a95c8ede2ffd4cc2cdb5b94e566b11662
Правило: digest получать через `docker pull busybox:1.36 && docker images busybox:1.36 --digests --format '{{.Digest}}'`
         (digest может измениться между pull и commit — проверить актуальность на момент PR)
```

### Contract 2 — hermes-agent default tag → latest (D2)
```
Файл: core/modules/hermes-agent/docker-compose.base.yml:64
Было:  image: ${CONTEXT_IMAGE:-ghcr.io/tronyxlab/hermes-agent-context:v2026.7.1}
Стало: image: ${CONTEXT_IMAGE:-ghcr.io/tronyxlab/hermes-agent-context:latest}
Параллельно: строка 8 инварианта уже говорит `:latest` — расхождение устраняется.
Локально: `hermes-agent-context:latest` уже есть в Docker images (sha256:4628109a...).
CI: image pull из ghcr.io (если недоступен — CI всё равно падает, но это infrastructure issue, не код).
```

### Contract 3 — fixture hardening: docker rm -f safety net (D3)
```
Файл: tests/_conftest/smoke.py, platform_services fixture, ПОСЛЕ глобального pre-cleanup
Добавить блок:

    # ── Safety net: remove stale test containers from crashed previous runs ──
    # ⚠️ TRAP[BUG] · docker compose down не удаляет контейнеры, созданные
    #    с другим набором compose-файлов (метки проекта не совпадают).
    #    После crash'а (Ctrl+C, OOM) контейнеры остаются и блокируют имена.
    _STALE_CONTAINER_NAMES = [
        "nginx-test", "prometheus-test", "grafana-test", "hermes-agent-test",
        "prometheus-config-init",
    ]
    for _cname in _STALE_CONTAINER_NAMES:
        subprocess.run(
            ["docker", "rm", "-f", _cname],
            capture_output=True, text=True, timeout=10,
        )
    _logger.info("[IMP:8][conftest][platform_services] Safety net: stale containers removed")

Место: после global pre-cleanup (строка ~593), до wave-parallel startup (строка ~594).
```

### Contract 4 — test ps query: --status running + dedup (D4)
```
Файл: tests/test_smoke_platform.py, test_platform_starts_all_containers
Блок BLOCK_CollectExpected (строки ~341-376):

1. Заменить `--all` на `--status running` в ps_args:
   ps_args.extend(["-p", "ai-platform-test", "ps", "--status", "running", "--format", "{{.Name}}"])

2. Удалить DIAG-логи (logger.error с "=== DIAG"): добавлены в 3714f5d для отладки, более не нужны.

3. Добавить фильтрацию one-shot контейнеров (defense in depth):
   _ONESHOT_CONTAINERS = {"ai-platform-test-minio-createbuckets-1", "prometheus-config-init"}
   expected_services = [s for s in expected_services if s not in _ONESHOT_CONTAINERS]

4. (опционально) Дедупликация — после фильтрации:
   expected_services = sorted(set(expected_services))

Место: test_smoke_platform.py:351-376.
```

## Data Flow (целевой, D3+D4)

```
platform_services fixture:
  ┌─ Docker guard (skip if no daemon/production)
  ├─ ensure_volume_dirs()
  ├─ pre-create external networks
  ├─ Global pre-cleanup: down --remove-orphans для ВСЕХ compose file'ов
  ├─ [NEW D3] Safety net: docker rm -f <stale containers>  ← блокировка имён после crash'а
  ├─ Wave-parallel startup: _start_single_module per module
  │    ├─ per-module pre-cleanup: down (без --remove-orphans)
  │    ├─ compose up -d --wait
  │    └─ post-up container existence check
  └─ yield {"started": [...], "failed": [...]}

test_platform_starts_all_containers:
  ├─ Collect expected: docker compose ps --status running [NEW D4]
  ├─ Filter one-shot containers [NEW D4]
  ├─ Deduplicate [NEW D4]
  ├─ Poll running: docker ps --format {{.Names}}
  └─ Assert: все expected в running
```

---

## $TASKS

### T1 — D1: busybox digest update (Coder, complexity 1)
Файлы: `core/modules/monitoring/docker-compose.base.yml` (1 строка).
1. Выполнить `docker pull busybox:1.36`, получить актуальный digest.
2. Обновить строку 34: `image: busybox:1.36@sha256:<current_digest>`.
3. Проверить: `docker compose -f core/modules/monitoring/docker-compose.base.yml -f core/modules/monitoring/docker-compose.test.yml -p ai-platform-test config` → exit 0.
- **Acceptance:** compose config валиден; `docker pull busybox:1.36@<new_digest>` успешен.
- **Deps:** нет.

### T2 — D2: hermes-agent default tag → latest (Coder, complexity 1)
Файлы: `core/modules/hermes-agent/docker-compose.base.yml` (строка 64).
1. Заменить `v2026.7.1` → `latest` в строке `image:`.
2. Удалить TRAP[DECISION] строки 58-63 (комментарий «Context overlay digest not resolved») — более не релевантен при `:latest`.
3. Проверить: локальный `hermes-agent-context:latest` существует (C6).
- **Acceptance:** `docker compose -f core/modules/hermes-agent/docker-compose.base.yml -f core/modules/hermes-agent/docker-compose.test.yml -p ai-platform-test config` → exit 0; образ резолвится локально.
- **Deps:** нет.

### T3 — D3: fixture hardening — stale container safety net (Coder, complexity 2)
Файлы: `tests/_conftest/smoke.py` (~15 строк).
1. Реализовать Contract 3 — добавить `docker rm -f` для известных test-container names ПОСЛЕ global pre-cleanup.
2. Убедиться, что `_STALE_CONTAINER_NAMES` включает все контейнеры, создаваемые test-оверлеями: `nginx-test`, `prometheus-test`, `grafana-test`, `hermes-agent-test`, `prometheus-config-init`.
3. TRAP[BUG] комментарий над блоком.
- **Acceptance:** ручной тест — создать `nginx-test` через `docker run -d --name nginx-test nginx:alpine`, запустить smoke-тесты → контейнер удалён, тесты не падают с «already in use».
- **Deps:** нет (параллельно с T1, T2).

### T4 — D4: test ps query fix + dedup (Coder, complexity 2)
Файлы: `tests/test_smoke_platform.py` (~10 строк изменений, ~5 строк удалений).
1. Реализовать Contract 4:
   - `--all` → `--status running` в `ps_args`
   - Добавить фильтр `_ONESHOT_CONTAINERS`
   - Дедуплицировать `expected_services`
   - Удалить DIAG-логи (`logger.error("=== DIAG ...")`)
2. Проверить, что `test_critical_services_healthy` использует тот же паттерн `docker compose ps` — если да, применить аналогичный фикс (без дублирования кода — вынести в helper).
- **Acceptance:** `expected_services` ≤ 16 (не 144); `minio-createbuckets` отсутствует в expected; тест `test_platform_starts_all_containers` проходит.
- **Deps:** D3 (нужен чистый старт контейнеров для валидации).

### T5 — QA: smoke suite verification (QA, complexity 1)
1. `python -m pytest tests/test_smoke_platform.py -s -v -m smoke` — все 6 тестов зелёные.
2. `make gate MODE=fast` зелёный.
3. Проверить, что nginx, monitoring, hermes-agent в `started` (ни одного в `failed`).
4. Проверить отсутствие restart loops.
5. LDD-трассы: IMP:9 логи присутствуют для всех критических операций.
- **Deps:** T1, T2, T3, T4.

## $PARALLEL_GROUPS

### Wave 1 (независимые, разные файлы)
- Tasks: T1 (busybox digest), T2 (hermes tag), T3 (fixture hardening)
- Command: `coder Read .ai/plans/012-smoke-test-recovery/01-DevPlan.md, implement Wave 1: T1 | T2 | T3` (3 параллельных Coder)
### Wave 2
- Tasks: T4 (ps query fix — зависит от D3 для валидации), T5 (QA — после всех)

**Критический путь:** T3 → T4 → T5.

## Acceptance Criteria (сводная)

| # | Критерий | Проверка |
|---|----------|----------|
| A1 | busybox:1.36 digest обновлён и резолвится | `docker pull` успешен; compose config валиден |
| A2 | hermes-agent default tag = latest, образ резолвится локально | compose config валиден; локальный image существует |
| A3 | Stale контейнеры удаляются safety net'ом | ручной тест с `docker run -d --name nginx-test` |
| A4 | expected_services ≤ 16, нет дубликатов, нет one-shot контейнеров | тест T4 assertions |
| A5 | nginx, monitoring, hermes-agent в `started` | smoke suite output |
| A6 | `make gate MODE=fast` зелёный | CI pre-flight check |

## File Manifest

| Файл | Изменение | Task |
|------|-----------|------|
| core/modules/monitoring/docker-compose.base.yml | L34: busybox digest → актуальный | T1 |
| core/modules/hermes-agent/docker-compose.base.yml | L64: `v2026.7.1` → `latest`; удалить L58-63 TRAP | T2 |
| tests/_conftest/smoke.py | +~15 строк: safety net после global pre-cleanup | T3 |
| tests/test_smoke_platform.py | ~10 строк изменений: `--status running`, фильтр one-shot, dedup; -5 строк DIAG | T4 |

## Design Decisions

### DD1 — Почему digest, а не tag-only для busybox?
`## @rationale` Q: зачем пиннить digest, если Docker Hub ретайрит старые? A: Reproducible builds — без digest'а образ может измениться между CI-ran'ами. Решение: пиннить digest, но **обновлять digest при каждом изменении строки** (процедура: `docker pull <tag> → docker images --digests → новый digest`). Rejected: tag-only (`busybox:1.36`) — недетерминизм между CI и локальной средой; `busybox:stable` — rolling tag, ещё хуже.

### DD2 — Почему `:latest`, а не конкретный тег для hermes-agent?
`## @rationale` Q: почему не `v2026.7.2` или другой versioned tag? A: `:latest` — то, что реально существует на ghcr.io и что используется в production (через `CONTEXT_IMAGE` env override). Versioned tag требует CI pipeline для push'а tagged image — out of scope этой задачи. Rejected: `v2026.7.1` (не существует), `main` (нестабильно, rolling).

### DD3 — Почему `docker rm -f`, а не `docker compose down` для safety net?
`## @rationale` Q: почему не улучшить compose down, чтобы он находил stale контейнеры? A: `docker compose down` требует точного совпадения compose-файлов с теми, что использовались при создании контейнера. После изменения compose-файлов между run'ами (или crash'а без teardown) метки проекта могут не совпасть → down не найдёт контейнер. `docker rm -f` по имени контейнера — безусловный, идемпотентный. Rejected: `docker rm -f $(docker ps -aq --filter name=...-test)` — избыточно (фильтр по имени проще и быстрее).

### DD4 — Почему `--status running`, а не фильтрация в Python?
`## @rationale` Q: почему не оставить `--all` и фильтровать exited в тестовом коде? A: `--all` возвращает exited контейнеры, которые потом fail'ят assertions. Фильтрация на стороне Docker (`--status running`) — один источник правды, меньше кода, меньше багов. Плюс Python-фильтр _ONESHOT_CONTAINERS как defense in depth (на случай, если `--status running` по какой-то причине не отфильтрует). Rejected: только `--status running` без _ONESHOT_CONTAINERS — хрупко (полагаемся на поведение Docker Compose, которое может измениться).

## Out of Scope
- CI pipeline для push tagged hermes-agent images (отдельная задача `make hermes-build-context`).
- Переход на `busybox:1.36-musl` или `busybox:stable` (digest pinning — текущая стратегия).
- Рефакторинг `docker compose ps` вызова в отдельный helper (задача T4 делает минимальные правки; helper — технический долг, не блокирует).
- Полный аудит всех compose-файлов на предмет stale digest'ов (отдельная задача — periodic image digest audit).

## Next Steps

### Wave 1
`Use coder role, read .ai/plans/012-smoke-test-recovery/01-DevPlan.md, implement T1` · `... implement T2` · `... implement T3` (параллельно)
### Wave 2
`Use coder role, read .ai/plans/012-smoke-test-recovery/01-DevPlan.md, implement T4`
### Wave 3
`Use QA role, read .ai/plans/012-smoke-test-recovery/01-DevPlan.md, execute T5`

$END_DEVPLAN
