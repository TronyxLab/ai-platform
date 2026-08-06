
## 2026-08-06 12:30 MSK — Фаза 2: test-node-cold (8/10) + расследование B18
- 11:40:34-12:09:16 MSK: `make test-node NODE=tronyx-vps` — 8 passed / 2 failed (28:42).
  PASSED: test_01 (cold bootstrap 9 INIT фаз, с 1-й попытки!), test_04-08 (deploy/healthcheck/backup/restore/rebootstrap), 2 failure-сценария.
  FAILED: test_02 (UPDATE done=2/5: pending registry_update/deploy_update/converge_update), test_03 (reconciler: test-project отсутствует в выводе).
- РАССЛЕДОВАНИЕ: audit.jsonl доказал — бутстрап создал ВСЕ контейнеры модулей (11:42-49, DEPLOYED). К 12:05 (healthcheck φ11) они исчезли.
  Механизм удаления НЕ воспроизведён: повторные deploy-оркестратор (25 контейнеров поднято вручную 12:22), node-update (12:28, все 5 фаз done — no-op), converge (12:26, 25→25) — контейнеры не удаляют.
  Корень не найден в коде: deploy_orchestrator, converge R1-R9, context_deployer, deploy_engine, overlay-reload, orphan_reconciler — проверены.
- test_03: node.yaml tronyx-vps НЕ содержит test-project (5 проектов: tronyx-site, dance-site, botanika, legacy, roadmap) — тест ждёт его в выводе R3 (известное несоответствие, 1-й цикл: те же 3 failed, фикс не входил в 12 коммитов).
- ТЕКУЩЕЕ СОСТОЯНИЕ НОДЫ: 25 контейнеров (nginx, postgres, pgbouncer, redis, clickhouse, minio, litellm, langfuse, langfuse-redis, prometheus, grafana, loki, promtail, node-exporter, cadvisor, nginx-prometheus-exporter, redis-exporter, postgres-exporter, hermes-agent, status-page + 6 проектов) — все healthy (кроме failed-модулей infra-metrics/backup-cron).
- ДВА МОДУЛЯ FAILED (новые находки):
  B18a: infra-metrics — «Container redis-exporter Creating» up fail (в audit: бутстрап DEPLOYED успешно; конфликт при повторном деплое — контейнер уже существует, orphan-механизм не удаляет его из-за config failed).
  B18b: backup-cron — docker build fail Dockerfile:33 (apt postgresql-client-16 установка, подробности в логе).
- /opt/platform/.env ОТСУТСТВУЕТ на ноде; docker compose config (R7/orphan) падает «POSTGRES_PASSWORD missing» (26 вхождений) — env передаётся ТОЛЬКО через --env-file /run/platform/secrets.env в up-путях; config-пути (orphan, R7) его не используют. В 1-м цикле те же config-вызовы были скрыты (exit 0), не влияли.

## 2026-08-06 12:50 MSK — Фаза 3: node-update + converge + deploy ×4 + K3
- node-update: 5/5 фаз done (1-й run — флак SSH-timeout с exit 0; retry OK; флак зафиксирован, не баг).
- converge: exit 0 (warn: R6 legacy vhost-маркеры ×4, R7 config без env ×10 — detect-only).
- deploy-project ×4: tronyx-site 3.0s (2 ретрая: B19 chown .deploy-snapshots), dance-site 12.8s, botanika 12.6s, roadmap 12.2s — все DEPLOYED healthy.
- K3 verify verb (orchestrator_cli dispatch verify <node> <project>): dance-site/botanika/roadmap state=legacy blocking=0 ([PRACTICES:PROPOSE][L2]); tronyx-site state=proposed findings=0 (lock доставлен ручным receive — канонический канал НЕ доставляет, B20a).
- project-sync-practices tronyx-site: practices.lock + .pre-commit-config.yaml + tests/conftest.py + tests/test_health.py сгенерированы (state=proposed, level=auto).
- DevPlan 138 W3 (render-monitoring автозапуск): НЕ работает — пост-чейн под ci-deploy без прав на /opt/platform (catalog.json 644 root:platform, prometheus-targets root:platform; ci-deploy: ci-deploy,adm,docker) → B20b. catalog.json не регенерирован, prometheus-targets пуст.
- B19/B20a/B20b → failures-r2-2.md. Сигналы P3_DEPLOYS_DONE + CI_DEPLOY_CANDIDATE=tronyx-site + FIXES_NEEDED отправлены.
- Ручной диагностический receive (root, полный tar 47MB) упал на pull (root не авторизован ghcr — unauthorized; канон: ci-deploy) — контейнер tronyx-site не пострадал; /opt/projects/tronyx-site пополнен файлами проекта (не канон, зафиксировано).

## 2026-08-06 12:55 MSK — Фаза 4: chaos-сьют 8 failed / 3 passed (43:19)
- PASSED: T4 (clock skew), T5 (tor/telegram), T6 (postgres sigkill). FAILED: T1 (backup-cron+cadvisor не recovered — B18b, cadvisor restart-policy), T2 (recovery неполная), T3 (backup-cron нет), T7 (OOM-жертва не названа), T8 (disk pressure — IndexError), T9 (age CLI отсутствует на ноде — тест-окружение), T10 (backup-cron нет — B18b), T11 (reboot).
- T11 reboot вскрыл B21: /run/platform (tmpfs) bind-mount-источники (.htpasswd-platform, status-metrics.json) исчезают при перезагрузке → docker создаёт директории → nginx/status-page Exited(127). Восстановление: htpasswd CLI (secrets_manager) + platform-export-metrics.sh; nginx пересоздан с NGINX_OVERLAY_DIR (мой ручной compose up до этого смонтировал пустой ./overlays → 444 на всех сайтах; B23).
- B22: converge R9 видит только running (docker ps) → не чинит exited/created (nginx Exited остался после converge).
- cadvisor: Created (8080 конфликт с test-project-web) — test-project-web остановлен → cadvisor поднят.
- Финальное состояние ноды: 25/25 healthy, сайты 4/4 200. make healthcheck exit=1 — TRAP T10: таргет локальный (dev), dev status-page unhealthy 24h (B25 dev-only).
- Сигналы P4_CHAOS_DONE/P4_STACK_UP + FIXES_NEEDED (B21-B25) отправлены.
