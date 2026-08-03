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
| D-4 | ~~P-16: node.yaml tronyx-vps — миграция на contexts[] (top-level context устарел; branch/expose вне schema)~~ | LOW | **ЗАКРЫТ 125 (T7)**: contexts[] + expose + branch в node.schema.json; node.yaml tronyx-vps валидируется по branch | FIXED 2026-08-03 |
| D-5 | ~~P-17: cadvisor unhealthy (прод) — диагностика~~ | MED | **ЗАКРЫТ 125 (T8) фактом**: `docker inspect cadvisor` → healthy | running, логи без ошибок | FIXED 2026-08-03 |
| D-6 | ~~P-18: install-tor-proxy rc=1 (tor/privoxy конфиг)~~ | LOW | **ЗАКРЫТ 125 (T9)**: privoxy config mode 0600 root (mkstemp+replace) → сервис «Permission denied»; privoxy_config.py chmod 0644 + прод-фикс → tor+privoxy active, proxy 302 | FIXED 2026-08-03 |
| D-7 | ~~P-19: firewall verify 22/tcp not found~~ | LOW | **ЗАКРЫТ 125 (T10) фактом**: ufw status → `22/tcp ALLOW IN # platform-baseline` (+v6) | FIXED 2026-08-03 |
| D-8 | ~~P-21: S3 SSL cache недоступен (upload_cert None)~~ | MED | **ЗАКРЫТ дневной сессией**: dotted-import s3_ssl_cache + guard (S3 restore/upload работают — prod-бустрап φ7 restored=1/issued=1/skipped=1) | FIXED 2026-08-03 |
| D-9 | ~~P-2-5 (111): docker-дубли deploy_engine/orchestrator/docker.sh → shared~~ | MED | **ЗАКРЫТ 125 (T11) by construction**: docker.sh не существует; deploy/*.py 0 raw compose-вызовов (всё через shared/docker_compose.py); гейт docker_sole_path GREEN | FIXED 2026-08-03 |
| D-10 | ~~D7 (118): generate_platform_env f-string → jinja~~ | LOW | **ЗАКРЫТ 125 (T12) keep by design**: рендер уже структурный (yaml.dump); TRAP[DECISION] LOW в generate_platform_env.py:265 | CLOSED (keep) 2026-08-03 |
| D-11 | ~~test-env-leak-and-flakes~~ | MED | **ЗАКРЫТ 125 (T13)**: аудит os.environ в gate-скоупе — единственный leak (test_shared_timeouts PLATFORM_DEPLOY_TIMEOUT) переписан на monkeypatch-детерминизм + TRAP[DEBT] | FIXED 2026-08-03 |
| D-12 | Локальный status-metrics/htpasswd cron отсутствует (dev-локали) — файлы генерируются вручную | LOW | Документировано в Fix Recipe | 2026-08-31 |
| D-13 | ~~P-21: check-manifest-parity CI RED / локально GREEN~~ | MED | **ЗАКРЫТ 124 (35c0c71)**: hook удалён из pre-commit (TRAP[DECISION] — flake ~40% в gate-шаге, причина не установлена после venv-pin); parity полностью покрыта gates-чеком (test_gate_manifest_integrity, 15 тестов). Rev-условие в TRAP: вернуть hook при установлении причины | FIXED 2026-08-03 |
| D-14 | ~~P-22: verify <node> проверяет ВСЕ expose-домены — race при параллельных деплоях~~ | MED | **ЗАКРЫТ 125 (T1)**: verify per-project (--project); CI-verify деплоящегося проекта не зависит от 502 соседа; `make verify` без PROJECT сохраняет all-domains поведение | FIXED 2026-08-03 |
| D-15 | P-23: e2e φ8 deploy_context «No module named 'pydantic'» (non-fatal, error-path) | LOW | На проде не воспроизвёлся; ошибка обработки деплоя stub | 2026-08-31 |
