# failures-r2-1.md — B18: контейнеры модулей исчезают после бутстрапа (test_02/test_03)

$START_FAILURE_REPORT

- **Дата/время:** 2026-08-06 11:40-12:30 MSK (2-й цикл, Фаза 2)
- **Попытка:** test-node-cold (attempt 1) — 8 passed / 2 failed за 28:42
- **Связанные тесты:** test_02_update_mode_5_phases, test_03_converge_idempotent

## Симптом

1. `make node-update NODE=tronyx-vps` — exit 0 («Node update complete»), но state.json:
   UPDATE done=2/5, pending=`['registry_update', 'deploy_update', 'converge_update']`.
   В make-логе: φ11 registry_update выполнился (GHCR auth, provision, overlay, LLM keys,
   healthcheck nginx/postgres/redis/clickhouse/litellm/langfuse FAILED after 10 attempts),
   фаз φ12/φ13 в логе НЕТ.
2. `make converge NODE=tronyx-vps` ×2 — exit 0 (FULLY CONVERGED), но `reconciler processed
   test-project=False` (test_03 assert: "test-project" отсутствует в выводе R3).
3. docker ps на ноде: контейнеры МОДУЛЕЙ (nginx/postgres/...) отсутствуют ВООБЩЕ (не даже Exited);
   присутствуют только 5 проектов (tronyx-site, dance-site, botanika, legacy, roadmap) + test-project.

## Доказательство [IMP:9]-трейса

- audit.jsonl (нода, /var/log/platform/audit.jsonl): бутстрап 08:42:57-08:49:10Z —
  ВСЕ 13 модулей START→DEPLOYED (nginx 08:43:01, postgres 08:43:21, ... hermes-agent 08:49:10).
  → контейнеры БЫЛИ созданы бутстрапом, удалены в окне 11:49-12:05 MSK.
- make-лог 20260806-114034-test-node.log:
  - 657: `deploy-modules.sh --skip-provision` exit=0 (subprocess, внутренний вывод скрыт при success)
  - 1548-1565: healthcheck φ11: nginx/postgres/redis/clickhouse/litellm/langfuse FAILED (контейнеров нет)
  - 1734+: converge R7: `docker compose config failed for postgres: required variable POSTGRES_PASSWORD is missing`
- Первый ручной прогон оркестратора (12:14Z): deployed=12 failed=[infra-metrics, backup-cron] —
  контейнеры создались (audit 09:14:06-09:22Z), к 12:18 отсутствовали. Второй ручной прогон (12:22):
  контейнеры созданы и ЖИВЫ (25 шт.) по сей момент — удаление НЕ воспроизводится.

## Гипотезы (проверены, не подтверждены)

| Гипотеза | Проверка | Вердикт |
|----------|----------|---------|
| deploy-оркестратор удаляет контейнеры postflight (orphan) | 2 ручных прогона: config failed → 0 orphans → живут; код: итерация по сервисам модуля, при config fail список пуст | не подтверждена |
| converge R1-R9 удаляет | reconcile на живом стеке: 25→25 | не подтверждена |
| context_deployer/deploy_engine (проекты) удаляют | down только в remove()-пути; проекты — свои compose project | не подтверждена |
| overlay reload φ11 (nginx) | docker exec nginx reload, не compose up | не подтверждена |
| COMPOSE_PROFILES env (от Makefile) + --remove-orphans последовательно удаляют контейнеры модулей | контейнеры сейчас project=platform (от -f root compose); при COMPOSE_PROFILES=все-13 защита активна (setdefault); при чужом COMPOSE_PROFILES — возможна каскадная зачистка | НЕ ИСКЛЮЧЕНА (кандидат №1), требует воспроизведения с COMPOSE_PROFILES=platform |

## Сопутствующие находки (отдельные дефекты, не связаны с удалением)

- **B18a:** infra-metrics `docker compose up` fail: `Container redis-exporter Creating` — имя
  конфликтует с существующим контейнером при повторном деплое (первый деплой в audit = DEPLOYED).
- **B18b:** backup-cron `docker compose build` fail на Dockerfile:33 (apt postgresql-client-16).
- **R7/фоновый:** `docker compose config` (orphan_reconciler, converge R7) вызывается БЕЗ
  `--env-file /run/platform/secrets.env` → падает «POSTGRES_PASSWORD missing» (26 вхождений/прогон).
  В 1-м цикле этот вывод был скрыт (subprocess exit 0). Влияние: orphan-детекция слепа (0 сервисов).
- test_03: node.yaml tronyx-vps НЕ содержит test-project — тестовое ожидание не соответствует
  конфигурации ноды (в 1-м цикле тест также failed, фикс не входил в волну).

## Статус

- Стек восстановлен вручную (25 контейнеров healthy) — целевое состояние бутстрапа достигнуто.
- Повторный node-update (12:28) — NO-OP (все 5 фаз done), контейнеры живы.
- Требуется от local-validation: (1) анализ кандидата №1 (COMPOSE_PROFILES), (2) B18a/B18b, (3) R7 env-file.

$END_FAILURE_REPORT
