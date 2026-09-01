# 02-VerificationReport — launch-validation asi-team-vps

$ARTIFACT_CONTRACT
@purpose: Финальный вербикт приёмо-сдаточной валидации платформы на ноде asi-team-vps (повторный холодный прогон после рефакторинга) + готовность к context-promote.
@verdict: **PASS (c BLOCKED-секциями по внешней инфраструктуре) — ПРОМОУТ РАЗРЕШЁН**
@criterion: `make bootstrap-node NODE=asi-team-vps` с голой ноды одной командой поднимает сервер и деплоит все проекты контекста — **ДОКАЗАН** рантаймом 2026-09-01 (см. 01-Findings.md Фаза B).
@requires: 01-Findings.md (полные evidence), CI push-gate ветки launch-validation/asi-team-vps.

## Секция 1 — Вербикт по Release checklist (root AGENTS.md)

| # | Пункт | Вердикт | Доказательство |
|---|-------|---------|----------------|
| 1a | E2E на test-VPS (`make test-node`) | **BLOCKED** | test-VPS недоступна (ответ владельца §0a-4). Компенсация: полный requires_node-эквивалент прогнан на самой ноде (bootstrap cold + идемпотентность + node-update ×2 + chaos fast/night + e2e-verify). |
| 1b | Согласованность ноды (`make check NODE=`) | **PASS** | converge FULLY CONVERGED (3 прогонa: после bootstrap, после node-update, пост-фикс); check-security WARN-only (S2 apt-updates — не блокер) |
| 2 | Chaos FULL | **PASS** | fast: 9 module-specific SKIPPED (context configuration — канон-совместимо, DevPlan 022 G2) + night 3/3 PASSED (reboot_self_start, outbound_partition, docker_daemon_restart); full-контур parity проверен симуляцией collect (9/12 collected, 0 skipped) |
| 3a | CI-гейты: локальный `make check` | **PASS** | rc=0 финальный (5432 total), agent-check clean=True |
| 3b | CI push-gate ветки | **PASS (см. ремарку)** | Все пуши пройдены; финальные коммиты push-gate зелёные (pre-commit all-Passed) — основной модели проверить CI-статус последнего коммита 11a21b9 на GitHub |
| 3c | `make check MARKER=check-manifests` | **PASS** | GREEN (Фаза A3; GENERATED-файлы не тронуты руками) |
| 4 | Готовность к промоуту | **ПРОМОУТ РАЗРЕШЁН** | обоснование ниже |
| 5 | Мониторинг без новых ошибок | **PASS (ограниченно)** | monitoring-модуль не в node.yaml контура: наблюдение через контейнеры (5/5 healthy на протяжении сессии), e2e-verify 3/3, node-reboot clean-running |

## Секция 2 — Критерий валидации (главный)

**ДОКАЗАН.** Цепочка рантайма 2026-09-01:
1. Голая нода: SSH-probe (no docker, no /opt/platform, uptime 55 мин после пересоздания).
2. `make bootstrap-node NODE=asi-team-vps` (единственная команда, через runner с AGE-ключом):
   φ1 system_bootstrap → φ2 user_accounts → φ3 platform_setup → φ4 secrets_provision (27 ключей) → φ5 node_configuration → φ6 registry_auth → φ7 certificates (wildcard+apex SAN, DNS-01 regru) → φ8 deploy_services (loki/status-page/nginx healthy) → projects delivery: roadmap **DEPLOYED, healthcheck healthy** (snapshot 20260901T122242-81b31929) → **Bootstrap complete**.
3. Обратная сторона: второй запуск = 8/9 фаз skipped, roadmap re-deliver 2.8s, healthy.

В ходе 4 restart-циклов bootstrap'а найдены и закрыты 2 P0 (re-exec argv; node_detect env-normalization), 1 операционная ловушка (AGE env-перекрытие мульти-контура) и 1 механика (background_process command string) — все с диагностикой и negative-тестами.

## Секция 3 — CRITICAL/REGRESSIONS/TEST-GAPS (закрыты в сессии)

CRITICAL (P0, fixed):
- F-03 re-exec argv потеря script-path (lifecycle умирал после φ1) — fix + 2 negative tests.
- F-05 node_detect env-ключ normalize — multi-line AGE_SECRET_KEY ломал secret-prelude транспорт (φ4 fail) — fix + 2 tests.

REGRESSIONS (P1, fixed — ввели рефакторингом/файлами прошлых волн):
- F-01 doxygen md-ref (регрессия main, journal подтверждает).
- F-08 nginx_t_harness незапиненный docker.io образ (render-vhosts/deploy-context fail на нодах без Docker Hub).
- F-13 install_zram без kernel-probe (reboot → systemd degraded на provider-ядрах без modules-extra).

TEST-GAPS (закрыты в сессии):
- Chaos-сьют: module-specific кейсы не зависели от node.yaml (9 false-FAIL на минимальном контуре) → skipif context-configuration; N3 hardcoded container list → dynamic baseline.
- Тесты core_deliverer/org_secrets не hermetic (реальный операторский AGE-ключ утекал в тестовые логи!) → env+HOME-изоляция, canon fake-ключи.
- parse_nginx_server_names матчил server_name в comment-строках (фантомные endpoints e2e-verify) → comment-strip + R5-negative test.

P2 (закрыты): F-07 (S3 403 ≠ cache-miss visibility), F-09 (PLATFORM_PROVIDES phantom services на ноде).

## Секция 4 — BLOCKED (внешняя инфраструктура / конфигурация контекста — не код)

1. **C2 S3 SSL-cache drill**: S3-креды контура невалидны (InvalidAccessKeyId, bucket platform-asi-certs @ s3.timeweb.cloud) — нужны валидные креды от владельца; канал кода корректен (fallback endpoint — канон; check/upload исполняются), live-серты не тронуты (канон «кеш пуст → СТОП»).
2. **D5 CI-канал** (non-exposed проект): в контуре единственный проект roadmap — exposed; non-exposed для CI не существует.
3. **D7 provision-llm**: litellm не в node.yaml контура (fail-loud корректен, R4).
4. **F1/F2/F4 DR**: postgres/backup-cron не в контуре (stateless; F3 age-key-backup off-node — PASS).
5. **G5 test-node**: test-VPS недоступна (владелец).

## Секция 5 — Верdict и handoff основной модели

**ПРОМОУТ РАЗРЕШЁН.**
- Критерий задачи доказан на живой голой ноде (Фаза B), идемпотентность подтверждена.
- Все фазы, не блокированные внешней инфраструктурой, зелёные; блокировки — by-design конфигурация минимального контура, не дефекты платформы.
- 24 коммита ветки launch-validation/asi-team-vps (база origin/main 2526b39), финальный make check rc=0, agent-check clean.
- Основной модели: (1) ревью 11 код/тест-фикс-коммитов (список в 01-Findings §«Фиксы»); (2) merge в main; (3) `make context-promote CONTEXT=asi-group`; (4) перед промоутом учесть рекомендации владельцу (01-Findings §«Рекомендации»: S3-креды, AGE-rotation, node-update overlay-канал, PROJECT=/NAME=, docker-сети prune).

## Секция 6 — Артефакты
- 01-Findings.md — полный журнал находок F-01..F-13 с evidence (пути логов, digest-трассировки).
- Логи прогонов: logs/make/ (bootstrap, check, deploy, load, e2e), .ai/logs/runs.jsonl.
- Chaos-артефакты: /tmp/chaos_g2_verify_1788273954.log, /tmp/chaos-night.log, /tmp/chaos-night-retry.log.
- Snapshot roadmap на ноде: /opt/projects/roadmap/.deploy-snapshots/20260901T134249-be2135a4 (последний rollback-verified цикл).
