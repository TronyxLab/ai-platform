# 02-DevPlan — Локализация ci-docker smoke-hang v2: langfuse container crash (Linux CI)

<!-- GREP_SUMMARY: cicd, smoke-hang, langfuse, exited-1, linux, diagnostic-dump, container-logs, clickhouse, prisma, s3, wave-pipeline, D1-fix -->
<!-- STRUCTURE: ┌диагноз (langfuse Exited 1)┐ → ◇ Wave 1 диагностика (log-capture) → ◇ Wave 2 root-cause → ◇ Wave 3 fix + verify → ⎋ acceptance -->
# region MODULE_CONTRACT
## @purpose  Локализовать и исправить новый root-cause ci-docker smoke-сьюта: после фикса
##           DevPlan 006 (DNS-alias'ы + SMOKE_ENV) в CI (Linux, run 32058540726) langfuse-test
##           выходит с кодом 1 спустя ~4-5 мин — каскад: hermes-agent не стартует, wave 3 не healthy,
##           smoke висит до 900s-kill.
## @scope    core/modules/langfuse/, core/modules/clickhouse/, .github/workflows/platform-test.yml
##           (diagnostic dump), tests/_conftest/ (health/containers).
## @invariants
##   1. Диагностика ПРЕЖДЕ фикса: langfuse-логи обязаны попадать в CI-лог (dump контейнеров).
##   2. Локальный macOS-прогон — эталон (38 passed / 1 skipped, DevPlan 006 W6): правка НЕ должна
##      ломать локальный проход; root-cause — Linux-специфичный (CI-only).
##   3. Streaming-канон (006) сохраняется: heartbeat + [child]-стрим — наблюдаемость не регрессирует.
## @rationale DevPlan 006 вскрыл слепоту (0 вывода → живой стрим). Теперь видно: langfuse-test
##            Exited (1) через ~5 мин. Причина не в P1001 (DNS-alias закрыт) — нужны контейнерные логи.
## @changes 2026-08-17 | Created — следствие run 32058540726 (platform-test failure после 006)
# endregion MODULE_CONTRACT

## $ARTIFACT_CONTRACT

- **PURPOSE:** Найти и исправить причину краха langfuse-test (exit 1) в CI smoke (Linux), не сломав локальный (macOS) проход.
- **RATIONALE:** 006 дал наблюдаемость; теперь видно точку отказа — langfuse web-контейнер падает. Без контейнерных логов фикс вслепую недопустим.
- **IMPLEMENTS:** follow-up DevPlan 006 (Wave 6 локализация — вторая, CI-only точка hang).
- **IMPACTS:** platform-test.yml (diagnostic dump), langfuse/clickhouse test compose, возможно tests/_conftest/*.
- **ACCEPTANCE_CRITERIA:**
  1. langfuse-лог виден в CI-логе (dump контейнеров) — root-cause назван строкой.
  2. ci-docker smoke зелёный в CI (<15 мин smoke-шаг), локальный macOS smoke НЕ регрессирует.
  3. Временная диагностика удалена/оформлена каноном (если не принята).
  4. make agent-check exit 0; локальный make check без новых фейлов.

---

## 1. Диагноз (run 32058540726, 2026-08-17)

Факты из CI-лога (platform-test, headSha 4c8b893 — коммит 006):

| Сигнал | Значение |
|---|---|
| smoke-шаг | pytest tests/ -m smoke → killpg на 900s (exit 124) |
| Первый видимый FAIL | test_hermes_dashboard_endpoint: ConnectionError http://localhost:19119 |
| Ключевой FAIL | test_langfuse_health_endpoint: langfuse-test Exited (1) 38s назад, порт 13000 не слушает |
| Каскад | test_smoke_hermes: Module 'hermes-agent' was never started by platform_services |
| Каскад | test_smoke_langfuse: Module 'langfuse' was never started |
| Позитив | test_prometheus_healthy_endpoint PASSED; component-сьют 10 passed (57s) |
| Heartbeat | [stream][heartbeat] elapsed=360s…900s — стрим работает |

Вывод: langfuse web-контейнер (langfuse-test) стартует, живёт ~4-5 мин, затем Exited (1).
Каскад: hermes-agent (depends_on langfuse в wave) не получает healthy → wave не готов →
тесты ждут wave-event до 900s-kill. Component (clickhouse/hermes отдельным стеком) — зелёный.

## 2. Гипотезы (по убыванию вероятности)

1. Prisma/ClickHouse-миграция langfuse v4 падает в Linux (exit 1 = graceful failure миграции,
   не OOM-137): 423 Prisma + ClickHouse migrations; memory 1536M / pids 256 / NODE_OPTIONS 1024M.
2. S3-конфиг: LANGFUSE_S3_EVENT_UPLOAD_ENDPOINT резолвится в ${S3_ENDPOINT_URL:-https://s3.timeweb.cloud}
   → в CI = production Timeweb endpoint с dummy-кредами (SMOKE_ENV S3_ENDPOINT_URL="" → :- дефолт).
3. ClickHouse-доступ из langfuse: clickhouse:9000 (native) — alias на test-observability-net
   уже есть (TRAP[FIX] 2026-07-22), но проверить достижимость.
4. env-контракт: NEXTAUTH_SECRET/SALT/ENCRYPTION_KEY отсутствуют/невалидны в CI (secrets ci_default).

## 3. Волны исполнения

- Wave 1 — диагностика (обязательно первая): в platform-test.yml diagnostic-dump шаг добавить
  docker logs langfuse-test (+ langfuse-worker-test, hermes-agent-test) при if: failure().
  Re-run CI (workflow_dispatch или push) → получить точную строку краха.
- Wave 2 — root-cause: по логу определить (миграция / S3 / clickhouse / env) → точечная правка
  в core/modules/langfuse/* или test-overlay.
- Wave 3 — fix + verify: ci-docker smoke зелёный в CI; локальный make check MARKER=smoke НЕ регрессирует;
  make agent-check exit 0; зачистка временной диагностики (или оформление каноном).

## 4. Do NOT

1. НЕ фиксить вслепую без контейнерного лога (Wave 1 обязателен).
2. НЕ ломать локальный macOS-проход (правки Linux-only допустимы, но обоснованы).
3. НЕ трогать streaming-канон 006 (heartbeat/[child]) — наблюдаемость не регрессирует.
4. НЕ менять production-семантику S3 (Timeweb) — правится только test-overlay/CI-env.

## 5. Размер/обратимость

- Размер: MEDIUM (диагностика + точечный фикс; без arch-изменений).
- Обратимость: диагностика (log-capture) аддитивна; фикс — точечный, откатываем.