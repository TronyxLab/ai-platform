# 01-Debt.md — RC 121 отложенные долги

<!-- GREP_SUMMARY: debt, rc-121, backlog, rev, deferred -->
# region MODULE_CONTRACT
## @purpose  Что отложено ночной RC-сессией 121 с Rev-условиями. Не входит в закрытые долги (watchdog, C6, P2-3, P2-4 — см. VerificationReport Фаза 6).
## @scope    Только отложенное с обоснованием.
# endregion MODULE_CONTRACT

## Открытые долги

| # | Долг | Severity | Почему отложено | Rev |
|---|------|----------|-----------------|-----|
| D-1 | P-14: CI manifests-гейт RED (4 рана подряд, локально GREEN, не воспроизводится) | HIGH | Требует доступа к CI-окружению (лог pre-commit с diff) | 2026-08-10 |
| D-2 | P-13: Build Hermes L1 push 403 (ghcr.io/tronyx161) | HIGH | Операторская проверка GHCR-токена/пакета | 2026-08-10 |
| D-3 | P-15: Доставка проектов через CI (push tronyx-site/dance-site/botanika → receive) | MED | Время; локальные main позади origin/main | 2026-08-10 |
| D-4 | P-16: node.yaml tronyx-vps — миграция на contexts[] (top-level context устарел; branch/expose вне schema) | LOW | Конфиг владельца | 2026-08-31 |
| D-5 | P-17: cadvisor unhealthy (прод) — диагностика | MED | Требует времени на разбор | 2026-08-10 |
| D-6 | P-18: install-tor-proxy rc=1 (tor/privoxy конфиг) | LOW | Tor-пакеты стоят, systemd tor active | 2026-08-10 |
| D-7 | P-19: firewall verify 22/tcp not found | LOW | ufw applied | 2026-08-10 |
| D-8 | P-21: S3 SSL cache недоступен (upload_cert None) — бэкап сертификатов в S3 не работает | MED | s3_ssl_cache «module not available» — boto3/конфиг | 2026-08-10 |
| D-9 | P-2-5 (111): docker-дубли deploy_engine/orchestrator/docker.sh → shared | MED | Крупный рефакторинг | 2026-09-30 |
| D-10 | D7 (118): generate_platform_env f-string → jinja | LOW | Опциональный | 2026-10-01 |
| D-11 | test-env-leak-and-flakes | MED | Вне скоупа ночи (не блокировал прогоны) | 2026-08-31 |
| D-12 | Локальный status-metrics/htpasswd cron отсутствует (dev-локали) — файлы генерируются вручную | LOW | Документировано в Fix Recipe | 2026-08-31 |
