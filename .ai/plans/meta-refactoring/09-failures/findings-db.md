# Findings: DB failure-modes (S1/S2) — pre-launch audit

$ARTIFACT_CONTRACT
- PURPOSE: Аудит production failure-modes PostgreSQL/pgbouncer и зависимых сервисов (litellm/langfuse/backup) перед launch
- SCOPE: research-only; ID-диапазон FAIL-0100–0199; evidence = file:symbol + цитата
- REQUIRES: сценарии S1 (postgres/pgbouncer unavailable, runtime+deploy), S2 (каскад на litellm/langfuse/clickhouse)
- ACCEPTANCE: каждый finding отвечает на 9 вопросов аудит-протокола; severity по критерию launch-risk

### FAIL-0100 · HIGH · pgbouncer — единственный фасад БД для всех потребителей, полностью вне мониторинга/алертов
- scenario: S1+S2 — падение ТОЛЬКО pgbouncer (OOM при лимите 64M, crash, crash-loop) при живом postgres
- evidence: `core/modules/postgres/docker-compose.base.yml:91` (сервис pgbouncer, `MAX_CLIENT_CONN: "50"` :110, memory limit 64M :119); `core/modules/infra-metrics/docker-compose.base.yml:13` («Postgres Exporter: DATA_SOURCE_NAME DSN → postgres:5432» — экспортёр идёт МИМО pgbouncer); `core/modules/monitoring/config/prometheus.yml.tmpl` — нет scrape-job для pgbouncer; `alerting/alert-rules.yml:81` (`up == bool 0`) покрывает только scrape-targets
- Q1: все проекты (PLATFORM_POSTGRES_DSN → `pgbouncer:6432`), litellm (:75) и langfuse (:35) теряют БД одновременно при зелёном postgres
- Q2: точка отказа — контейнер pgbouncer (docker-compose.base.yml:93 `container_name: pgbouncer`)
- Q3: crash/OOM → auto-restart (`restart: unless-stopped` :95), простой сек~мин; crash-loop (битый конфиг/DNS) — НЕ восстанавливается
- Q4: broken state — нет (stateless); Q5: retry клиентских коннектов — да, безопасен
- Q6: проект видит connection refused/timeout по DSN; платформенные алерты молчат
- Q7: alert НЕТ прямого (косвенно: Nginx5xxErrors warning `alert-rules.yml(alerting):600`, LLMAPIErrors warning :293)
- Q8: `make -C core/modules/postgres restart`; `make -C core/modules/postgres logs`; verify `make healthcheck NODE=<n>`
- Q9 (fix, config): pgbouncer-exporter в infra-metrics (аналог postgres-exporter, +scrape-job) ИЛИ минимально — alert на `container_...{name="pgbouncer"}` рестарты/unhealthy через существующий cAdvisor-канон
- confidence: high · action: добавить scrape/алерт до launch

### FAIL-0101 · CRITICAL · Хук создания проектной БД не подключён к post-deploy chain — проект с needs.database деплоится с нерабочим DSN
- scenario: S1-deploy (и даже при ПОЛНОСТЬЮ здоровом postgres)
- evidence: `core/modules/postgres/module.yaml:35-37` — комментарий «hooks.on_project_deploy: runtime-вызов…», секции `hooks:` НЕТ; диспетчер требует её: `core/internal/deploy/hooks/post_deploy_chain.py:210` (`if not hooks.get("on_project_deploy"): continue`) и `core/internal/shared/module_interface.py:261-264` (hook_path отсутствует → rc 0 «skipping»); реестр хуков — только nginx: `core/entrypoint-manifest.yaml:757-763`; callers `auto_create_db` вне хука/тестов — 0 (grep по core/)
- Q1: БД/роль `${project}_user`/GRANT/`.platform-db.env` при деплое НЕ создаются никогда автоматически
- Q2: отсутствие регистрации `hooks.on_project_deploy` в `core/modules/postgres/module.yaml`
- Q3: само не восстановится (никто не вызывает)
- Q4: broken state ДА: `.env.platform` генерируется с шаблонным паролем — `core/internal/scaffold/gen_env_platform.py:198` («без credentials поведение НЕ меняется») → `PLATFORM_POSTGRES_DSN=postgresql://myapp_user:***@pgbouncer:6432/myapp_db`
- Q5: retry = ручной запуск хука оператором; сам хук идемпотентен (`on_project_deploy.py:171` already-exists branch) — безопасен
- Q6: приложение получает невалидный DSN → auth/connection failure с первого запуска; CI при этом зелёный (см. FAIL-0102)
- Q7: alert нет; единственный детект — e2e `tests/e2e/test_shared_db_access.py:155` (ручной прогон, requires_node)
- Q8: вручную: `python3 core/modules/postgres/hooks/on_project_deploy.py <project_dir> <project>` → затем `make sync-env` (перегенерация DSN)
- Q9 (fix, config+5 строк): добавить `hooks.on_project_deploy` в postgres/module.yaml + thin sh-wrapper `exec python3 …/on_project_deploy.py "$@"` (диспетчер гоняет `bash <script>`, module_interface.py:180); противоречие с root AGENTS.md («роль/БД/GRANT создаются хук-ом postgres при деплое») закрыть тем же PR
- confidence: high · action: LAUNCH-BLOCKER для любых projects с needs.database

### FAIL-0102 · HIGH · Недоступность БД во время деплоя: healthcheck-fail → статус PARTIAL → CI «успех», rollback НЕ вызывается
- scenario: S1-deploy — postgres/pgbouncer упал в момент `git push` → CI → receive
- evidence: `core/internal/deploy/orchestrator.py:531` («Healthcheck status "healthy" → DEPLOYED, иначе PARTIAL»); `orchestrator.py:151-153` (`is_success` включает PARTIAL → `receive_flow.py:568` exit 0 для CI); rollback только из apply-ветки при compose-failure (`orchestrator.py:498-504`), `_rollback_deploy` из VERIFY не достижим
- Q1: контейнеры проекта стартуют, app не может подключиться к БД; poller истекает → PARTIAL; новый payload/образ УЖЕ перезаписали предыдущие
- Q2: `DeployOrchestrator._verify_deploy` (orchestrator.py:534-545)
- Q3: нет — деплой зафиксирован как успешный
- Q4: если новый образ битый — он остаётся активным (rollback образа не происходит); payload прошлой версии удалён из active
- Q5: повторный деплой после восстановления БД — да, безопасен (payload идемпотентен)
- Q6: CI green + приложение 500 у пользователей; Telegram-notify шлёт info (DEPLOYED/PARTIAL → success-ветка, post_deploy_chain.py:15-16)
- Q7: alert частичный: `PlatformDeployBurnRate` (`platform-alerts.yml:43-47`) считает по `platform_deploy_success` — учитывается ли PARTIAL как успех, зависит от status-page `_handle_metrics` (HYPOTHESIS: да, тогда burn-rate тоже слеп)
- Q8: runbook: после любого деплоя в окно БД-инцидента — `make status NODE=<n>` / `make healthcheck NODE=<n>`; откат руками `python3 -m core.internal.deploy.orchestrator_cli rollback`
- Q9 (fix, точечный код ≤10 LOC): unhealthy/timeout → статус FAILED (или явный audit-WARN + Telegram warning) вместо молчаливого PARTIAL-success
- confidence: high (код), med (status-page метрика) · action: кандидат в launch-blockers вместе с FAIL-0101

### FAIL-0103 · MED · OOM-риск postgres: shared_buffers 512MB + 5 autovacuum workers внутри cgroup-лимита 1G
- scenario: S1-runtime — пик нагрузки/autovacuum → cgroup OOM-kill → каскад S2 на всех потребителей
- evidence: `core/modules/postgres/config/postgresql.conf` (`shared_buffers = 512MB`, `work_mem = 8MB`, `autovacuum_max_workers = 5`, `pg_stat_statements.track = all`); `docker-compose.base.yml:50-54` (`limits: memory: 1G`)
- Q1: OOM-kill postgres → рестарт (~30-60s простоя) → разрыв серверных коннектов pgbouncer → ошибки у litellm/langfuse/проектов
- Q2: контейнер postgres (лимит :51)
- Q3: да — restart-политика поднимает; WAL защищает от порчи; но при устойчивой нагрузке — рестарт-цикл
- Q4: данные целы; Q5: retry клиентов безопасен (idempotent-повторы)
- Q6: всплеск connection errors на ~1 мин; Q7: alert ЕСТЬ: postgres-exporter `up==0` → ServiceDown critical 1m (`alerting/alert-rules.yml:74-120`) + ServiceDownShort 15s
- Q8: `docker stats`, `make healthcheck NODE=<n>`; при цикле — `make -C core/modules/postgres restart` после снижения нагрузки
- Q9 (fix, config): поднять лимит до 1.5G ИЛИ снизить shared_buffers до 256MB — однострочный конфиг-коммит
- confidence: med · action: тюнинг до launch (дёшево)

### FAIL-0104 · HIGH · langfuse-redis (64MB, allkeys-lru): тихая потеря очереди трейсингов при недоступности БД
- scenario: S2 — postgres/pgbouncer down → langfuse-worker не пишет в PG → backlog в Redis → LRU-eviction
- evidence: `core/modules/langfuse/docker-compose.base.yml:177-182` (`--maxmemory 64mb --maxmemory-policy allkeys-lru` при appendonly yes); worker владеет ingestion-очередями (:123-127 «v4: владеет очередями ingestion»); redis-exporter скрейпит ТОЛЬКО основной redis (`prometheus.yml.tmpl:126-131`, target redis://redis:6379) — langfuse-redis не мониторится
- Q1: во время БД-простоя события трейсинга копятся в langfuse-redis; при переполнении 64MB LRU молча вытесняет их
- Q2: контейнер langfuse-redis (compose:159-194)
- Q3: после восстановления PG очередь дренится, НО вытесненные события потеряны навсегда
- Q4: тихая порча данных — ДА (дыры в трейсах без ошибок)
- Q5: retry бессмыслен для вытесненного; Q6: UI langfuse работает, пользователь не видит проблемы
- Q7: alert НЕТ (ни scrape, ни правила); точная доля потерь зависит от retry-политики langfuse-worker — HYPOTHESIS
- Q8: восстановлению не подлежит; профилактика — `docker exec langfuse-redis redis-cli info memory|stats` (evicted_keys)
- Q9 (fix, config): `maxmemory-policy noeviction` (+backpressure вместо тиxой потери) и/или алерт на evicted_keys>0; память 128M уже выделена лимитом
- confidence: med · action: одна строка compose до launch
