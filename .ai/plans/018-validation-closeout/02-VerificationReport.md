# 02-VerificationReport · 018-validation-closeout

$START_VERIFICATION_REPORT
## $ARTIFACT_CONTRACT
- PURPOSE: Зафиксировать вердикт закрытия хвоста приёмо-сдаточной валидации 017 и отложенного промоута tronyx-lab
- DESCRIPTION: Верификация W1–W7 плана 01-DevPlan: F-22 pollution-фикс, F-21a/b/c chaos-каналы, NOTE-N7 (sops-матрица), внешние гейты D5/G5, полный зелёный контур, context-promote + пост-промоут
- RATIONALE: Критерий владельца 017 (bootstrap одной командой, delivered=3 healthy, идемпотентность) дополнен исправленными chaos-каналами, восстановленным DR-каналом и выполненным промоутом
- ACCEPTANCE_CRITERIA: AC1–AC7 из 01-DevPlan §4 — все проверены, доказательства в таблице ниже
- IMPLEMENTS: 017-launch-validation-tronyx-vps 02-VerificationReport §Остаток (F-21/F-22/NOTE-N7, D5/G5)
- IMPACTS: tests/unit/test_ssl_s3_cache.py, test_status_page.py, test_shared_s3_client.py, test_platform_export_metrics.py, test_monitoring_prometheus_targets.py, test_backup_cron_entrypoint.py; tests/e2e/test_chaos_resilience.py; core/internal/monitoring/{config_renderer,prometheus_targets}.py; core/internal/bootstrap/converge/{node_targets,reconciler}.py; core/modules/backup-cron/scripts/entrypoint.py; .github/workflows/deploy-project.yml; нода tronyx-vps (converge, secrets, backup-cron recreate); контекст TronyxLab/ai-platform (промоут ×2)
- REQUIRES: владелец: D5-billing (лимиты TronyxLab восстанавливаются 2026-09-01 — верификация CI-execution остаётся единственным условием)

---

## Вердикт: PASS_WITH_CONDITIONS

Единственное условие: CI-execution в org TronyxLab (биллинг GitHub) — механика канала верифицирована полностью, execution-блокер чисто владельческий и восстанавливается 2026-09-01 (решение владельца в сессии 018).

## Acceptance Criteria

| # | Критерий | Статус | Доказательство |
|---|----------|--------|----------------|
| AC1 | make check 0 fail (включая TLS-metrics) | ✅ | 20/20 GREEN ×2 (перед промоутом и финал), agent-check exit 0, check-manifests чисто; logs/make/2026-08-31 (runs.jsonl) |
| AC2 | Chaos fast 9/9 на tronyx-vps | ✅ | rc=0, 9 passed 643.8s — logs/w7-chaos-fast2-*.log; F6 PASS 188s/F7 76s/F8 94s (волны W2-W4), F9 re-run 28.5s после F-24 фикса |
| AC3 | Chaos night N1-N3 | ✅ | rc=0, 3 passed 317.9s — первый полный night-прогон (017 выполнял только fast+reboot); logs/w7-chaos-night-*.log |
| AC4 | Backup verified на канонической матрице | ✅ | UPLOAD VERIFIED sha256=9f019365…0733 (ручной) + spool-retry UPLOAD OK (дамп 27.08); легаси S3_ENDPOINT в secrets.env = 0; logs/w5-backup2-*.log |
| AC5 | D5 verified или owner-gate задокументирован | ⚠️ | Условие (см. ниже): механика верифицирована, execution = billing owner-gate |
| AC6 | Промоут + пост-промоут зелёный | ✅ | context-promote SUCCESS ×2 (audit DONE); healthcheck ALL MODULES HEALTHY; e2e-verify 6/6 подряд PASS (после 2 транзиентов F-24); 3/3 проектов Up (healthy) |
| AC7 | Артефакты волны | ✅ | 03-Findings (F-22, F-21a/b/c, NOTE-N7, F-23, F-24), этот отчёт, logs/ (chaos, backup, promote, converge, e2e) |

## Волны

| Волна | Суть | Результат |
|-------|------|-----------|
| W1 | F-22: NODE_NAME env-утечка из test_ssl_s3_cache snapshot (xdist pollution) | fixed abeceb7 — monkeypatch-конверсия + hermetic delenv; make check впервые 20/20 с 27.08 |
| W2 | F-21a: docker 29 удалил `docker update --health-*`; requirepass-инъекция непригодна (redis-cli rc=0 на NOAUTH) | fixed 0260235 — CONFIG SET port 0 (ломает зависимость probe, rc=1); proof = last_restart stamp (docker restart НЕ инкрементирует RestartCount — прежнее доказательство валидно не было) |
| W3 | F-21b: bomb 2.98GiB < актуального лимита 3G (дрейф 1G→2G→3G) | fixed c5b525e — bomb = 1.3×лимит из docker inspect; R4-asserts MemAvailable/SwapTotal; kernel-OOM по cgroup-id |
| W4 | F-21c: disk_pressure — node jobs static→file_sd без wiring на single-node (016 T2.A skip) + perms 0600 + single-object формат + honesty-гейтинг | fixed bebd9fb — R11 converge-юнит; ratio 0.7365, F8 PASS |
| W5 | NOTE-N7: легаси S3_ENDPOINT (нечитаемый никем) удалён из sops-матрицы | fixed — converge/decrypt-канал восстановлен, backup VERIFIED; дрейфы план↔код задокументированы (core-deliver ≠ node-configs; make converge ≠ φ9; make backup игнорирует NODE) |
| W6 | D5/G5 | D5: Actions enabled, billing = owner-gate (см. условие); G5: waiver на prod-evidence (решение владельца) |
| W7 | Полный контур + промоут | AC1-AC7; +2 внеплановых фикса: F-23 (flock /run/lock — ночные бэкапы не работали НИКОГДА), D5-workflow (runner.temp в job-env — все project-deploy ранфы падали на парсинге) |

## Условие PASS_WITH_CONDITIONS

**D5 CI-execution (billing):** workflow `deploy-project.yml` парсится (фикс 2419325: `${{ runner.temp }}` → `$RUNNER_TEMP` в job-level env), job'ы создаются, org-secrets TronyxLab настроены (AGE_SECRET_KEY, TELEGRAM_BOT_TOKEN, VPS_HOST, VPS_SSH_KEY), receive-канал верифицирован end-to-end (`make deploy-project` → DEPLOYED healthy 3.03s — тот же forced-command, что вызывает CI). Блокер: «recent account payments have failed or your spending limit needs to be increased» — владельческий, восстановление 2026-09-01. Верификация после восстановления: push проекта → run green → `project-status` DEPLOYED.

## Попутные находки и долги

- **F-23 (P1, closed):** flock без /run/lock — все cron-задачи backup-cron fail-closed с бутстрапа; RPO 24ч был фиктивен. Fixed d8d885a + нода переведена на новый образ.
- **F-24 (P2, closed):** F9 tail-маркер (тест-контракт vs починенный токен); e2e-verify транзиенты пост-reboot (2 фейла из ~8 прогонов, всегда разные endpoints, локальный канал).
- **TRAP[DEBT] AlloyCollectorDown (LO, open):** static alloy target без контейнера — постоянный warning-алерт (alert fatigue); решение владельца: убрать из шаблона или деплоить alloy.
- **Re-decrypt затёр autogen-ключи (закрыто в сессии):** decrypt ЗАМЕНЯЕТ secrets.env; ключи класса source:autogen (REDIS_PASSWORD, ENCRYPTION_KEY) живут только в secrets.env. Восстановлены из env работающих контейнеров и персистнуты в sops-матрицу ноды. Правило: после ручного decrypt обязан идти ensure-шаг (φ9-семантика).
- **G5 waiver:** release-checklist п.1 закрыт prod-validated evidence 017/018 (chaos fast+night, e2e-verify, healthcheck) — решение владельца 018.

## Коммиты волны (feat/fix на волну — канон)

abeceb7 (W1 F-22) · 0260235 (W2) · c5b525e (W3) · bebd9fb (W4) · 321d1a7 (W7 tests) · d8d885a (W7 F-23) · 2419325 (W7 D5 workflow) · ee73d74 (W7 F-24)

$END_VERIFICATION_REPORT
