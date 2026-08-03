# 01-Debt.md — RC 121 отложенные долги

<!-- GREP_SUMMARY: debt, rc-121, backlog, rev, deferred -->
# region MODULE_CONTRACT
## @purpose  Что отложено ночной RC-сессией 121 с Rev-условиями. Не входит в закрытые долги (watchdog, C6, P2-3, P2-4 — см. VerificationReport Фаза 6).
## @scope    Только отложенное с обоснованием.
# endregion MODULE_CONTRACT

## Открытые долги

| # | Долг | Severity | Почему отложено | Rev |
|---|------|----------|-----------------|-----|
| D-1 | ~~P-14: CI manifests-гейт RED~~ | HIGH | **ЗАКРЫТ дневной сессией** (pydantic→requirements SoT; dev-deps; env_defaults→.env.example; AGE fixture) | FIXED 2026-08-03 |
| D-2 | P-13: Build Hermes L1 push 403 (ghcr.io/tronyx161) | HIGH | Операторская проверка GHCR-токена/пакета | 2026-08-10 |
| D-3 | ~~P-15: Доставка проектов через CI~~ | MED | **ЗАКРЫТ дневной сессией**: 3/3 проекта push→CI→receive→200 + итерация обновления | FIXED 2026-08-03 |
| D-4 | P-16: node.yaml tronyx-vps — миграция на contexts[] (top-level context устарел; branch/expose вне schema) | LOW | Конфиг владельца (expose добавлен в schema дневной сессией) | 2026-08-31 |
| D-5 | P-17: cadvisor unhealthy (прод) — диагностика | MED | Требует времени на разбор | 2026-08-10 |
| D-6 | P-18: install-tor-proxy rc=1 (tor/privoxy конфиг) | LOW | Tor-пакеты стоят, systemd tor active | 2026-08-10 |
| D-7 | P-19: firewall verify 22/tcp not found | LOW | ufw applied | 2026-08-10 |
| D-8 | ~~P-21: S3 SSL cache недоступен (upload_cert None)~~ | MED | **ЗАКРЫТ дневной сессией**: dotted-import s3_ssl_cache + guard (S3 restore/upload работают — prod-бустрап φ7 restored=1/issued=1/skipped=1) | FIXED 2026-08-03 |
| D-9 | P-2-5 (111): docker-дубли deploy_engine/orchestrator/docker.sh → shared | MED | Крупный рефакторинг | 2026-09-30 |
| D-10 | D7 (118): generate_platform_env f-string → jinja | LOW | Опциональный | 2026-10-01 |
| D-11 | test-env-leak-and-flakes | MED | Вне скоупа ночи (не блокировал прогоны) | 2026-08-31 |
| D-12 | Локальный status-metrics/htpasswd cron отсутствует (dev-локали) — файлы генерируются вручную | LOW | Документировано в Fix Recipe | 2026-08-31 |
| D-13 | P-21: check-manifest-parity CI RED / локально GREEN (c997279, 2 rerun) | MED | Вероятна гонка с параллельной сессией 124 (её файлы захвачены в коммиты 121) — проверить после завершения 124, при остатке — диагностика через CI-лог | 2026-08-04 |
| D-14 | P-22: verify \<node\> проверяет ВСЕ expose-домены — race при параллельных деплоях (ложные фейлы tronyx-site/dance-site) | MED | verify по проекту или допуск 502 для не-своих доменов | 2026-08-10 |
| D-15 | P-23: e2e φ8 deploy_context «No module named 'pydantic'» (non-fatal, error-path) | LOW | На проде не воспроизвёлся; ошибка обработки деплоя stub | 2026-08-31 |
