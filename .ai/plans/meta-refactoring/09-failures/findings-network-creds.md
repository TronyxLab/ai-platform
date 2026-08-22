# FAIL-findings · S1 Expired credentials (non-TLS: AGE / SSH / GHCR / DB roles)

$ARTIFACT_CONTRACT
## @purpose Pre-launch audit: failure-modes кредов без TLS-домена (research-only, код не менялся)
## @scope core/internal/bootstrap/{core_deliverer,lifecycle/phases/secrets,docker_registry_auth}.py,
##        shared/{node_detect,docker_auth,ssh_opts}.py, security/deploy_channel_posture.py,
##        modules/postgres/hooks/on_project_deploy.py, lib/docker.sh, deploy/engine/
## @rationale максимум снижения риска / минимум churn: runbook/ops > config > код
## ACCEPTANCE_CRITERIA каждый finding: file:symbol-цитата, 9 ответов, confidence, action
## IMPLEMENTS pre-launch audit wave 09-failures; не дублирует FAIL-0300..0302 (TLS), 0303 (Tor SPOF)
## IMPACTS launch-blockers candidates (FAIL-0600)

---

### FAIL-0600 · CRITICAL · Потеря AGE master-ключа = невосстановимость секретов; off-node backup ещё не в контуре DR
1. Что происходит: отсутствие/битый ключ при bootstrap φ4 или node-update φ9 → decrypt падает;
   при РЕАЛЬНОЙ потере ключа — секреты (postgres, telegram, GHCR) невосстановимы на новой ноде.
2. Где отказ: core/internal/bootstrap/lifecycle/phases/secrets.py:56 `_run_secrets_step`
   («Secrets decryption FAILED — aborting» → PlatformFatalError); цепочка детекции —
   core/internal/shared/node_detect.py:113+ (`AGE_SECRET_KEY env → SOPS_AGE_KEY → FILE →
   ~/.config/age/keys.txt → /etc/age/key.txt restore-first`).
3. Auto-recovery: НЕТ (FATAL by design, корректно).
4. Broken state: state.json φ4/φ9 not done; precondition BLOCKS φ6/φ8 (containment хороший);
   HYPOTHESIS: secrets.env от прошлого успешного decrypt остаётся plaintext на диске и
   продолжает использоваться деплоем — фаза «красная», но стек живёт со старыми секретами.
5. Retry безопасен: да — после доставки корректного ключа повтор идемпотентен.
6. User impact: при потере ключа RTO из «часов» (runbook) превращается в дни ручного
   перепровижининга всех кредов всех проектов.
7. Alert: НЕТ — bootstrap/update идут с машины оператора/CI; нодный monitoring о них не знает.
8. Восстановление: `make secrets-unlock NODE=<n>` (проверка); DR — core/AGENTS.md §«DR мастер-ключа
   AGE» (restore-first, tmpfs). Но сам off-node encrypted backup — Debt («DR-offnode-backup»,
   core/AGENTS.md §5 Completion status).
9. Минимальный фикс до launch: ОПЕРАЦИОННЫЙ, 0 кода — выполнить `make age-key-backup`
   (реализован: core/internal/deploy/age_key_backup.py) + сверка restore из бэкапа на test-VPS.
Confidence: HIGH (кода/доков). Action: закрыть Debt до launch — кандидат в launch-blockers.

### FAIL-0601 · MED · Stale AGE_SECRET_KEY в env сессии молча перекрывает файл-ключ → FATAL с ложной диагностикой
1. Что происходит: оператор с устаревшим `AGE_SECRET_KEY` в env запускает bootstrap/converge/
   core-deliver — env побеждает файл по всей цепочке node_detect → sops «no key could decrypt»
   → PlatformFatalError; сообщение об ошибке не указывает, что виноват именно env-override.
2. Где отказ: core/internal/shared/node_detect.py:117 (`Check 1: AGE_SECRET_KEY env` — первый
   приоритет); контракт задокументирован core/AGENTS.md §Hook-окружение («env ПЕРЕКРЫВАЕТ файл…
   иначе core-deliver печатает warning» — warning только в core-deliver, не в lifecycle-фазах).
3. Auto-recovery: нет. 4. Broken state: нет (fail-fast до мутаций, идемпотентно).
5. Retry безопасен: да после `unset AGE_SECRET_KEY`.
6. User impact: потерянный час диагностики «ключ же правильный в файле»; при fallback-деплое —
7. Alert: нет (терминал/CI-log only).
8. Восстановление: `unset AGE_SECRET_KEY` → повтор; проверить `python3 -m core.internal.shared.node_detect --detect-age-key`.
9. Минимальный фикс: в _run_secrets_step при наличии И env, И файла печатать explicit notice
   «env перекрывает файл (пути…)» — одна лог-строка, churn минимален.
   NOTE (смежное, security): core_deliverer.py:637-643 deliver_fallback передаёт ключ в remote
   command line (`ssh … "…AGE_SECRET_KEY='…' make node-update"`) → ключ виден в `ps aux` на ноде
   весь node-update (до SSH_CMD_TIMEOUT=1800s) — пост-launch: передача через stdin.
Confidence: HIGH. Action: лог-строка в φ4/φ9; stdin-канал — backlog.

### FAIL-0602 · HIGH · Истечение GHCR_PULL_TOKEN обнаруживается только падением pull при деплое (нет proactive expiry-check)
1. Что происходит: PAT истёк → ghcr login fail = WARN non-fatal → пайплайн продолжается →
   `docker compose pull` приватных образов падает 401/403 уже на этапе pull.
2. Где отказ: core/internal/shared/docker_auth.py:151 `ghcr_login()` (bool, «Non-fatal»);
   core/internal/bootstrap/lifecycle/phases/docker.py:67 `_ghcr_auth_step` («GHCR auth failed
   (non-fatal)» → WARN); core/internal/bootstrap/deploy-modules.sh:62 `docker_login; ghcr_login`
   — фасады lib/docker.sh всегда продолжают анонимно («If login fails → warning log, continue»),
   set -e не срабатывает.
3. Auto-recovery: нет. 4. Broken state: нет для существующих проектов — engine откатывается:
   deploy/engine/engine.py:206-234 (`save_previous_image` ДО pull → perform_rollback на
   локальный предыдущий образ, offline-safe); для ПЕРВОГО деплоя — FATAL exit 10 без rollback
   (core/internal/deploy/first_deploy.py:28-55).
5. Retry безопасен: да, после ротации токена — деплой повторяется идемпотентно.
6. User impact: блокировка delivery-pipeline всех приватных образов (context/hermes/projects);
   platform module deploy при pull-failure роняет φ8/φ12 (PlatformFatalError).
7. Alert: частично — PlatformDeployBurnRate (<0.75 за 24h, severity=warning,
   monitoring/config/platform-alerts.yml:43-48) сработает только при массовых фейлах за сутки;
   proactive сигнала «токен истекает N дней» НЕТ.
8. Восстановление: ротация PAT → `sops update-keys` → `make secrets-unlock NODE=<n>` →
   `make node-update NODE=<n>` (φ11 ghcr-auth перелогинивает ci-deploy).
9. Минимальный фикс: поле expiry в secret-definitions.yaml + предупреждение в cert_expiry_check-
   стиле (или проверка `docker manifest inspect` приватного образа в vps_readiness preflight).
Confidence: HIGH. Action: expiry-check — кандидат в quick wins до launch.

### FAIL-0603 · MED · Отсутствие GHCR токена = тихий anonymous fallback → приватные образы падают только на pull
1. Что происходит: GHCR_PULL_TOKEN не задан (не расшифрован, забыт в sops) → ghcr_login
   возвращает True «anonymous fallback» → публичные образы тянутся, приватные — нет.
2. Где отказ: core/internal/shared/docker_auth.py:174-176 (`if not token: … return True`);
   precondition φ6: «GHCR_PULL_TOKEN (warning only)» (lifecycle/phases/preconditions.py,
   bootstrap/AGENTS.md §dependency rules).
3. Auto-recovery: нет. 4. Broken state: как FAIL-0602 (rollback/FATAL-first-deploy).
5. Retry безопасен: да. 6. User impact: первый деплой проекта в контекст невозможен (exit 10).
7. Alert: нет отдельного; см. burn-rate FAIL-0602.
8. Восстановление: как FAIL-0608 (sops-цепочка) + `make node-update`.
9. Минимальный фикс: если node.yaml#contexts непустой (есть приватные образы) — эскалировать
   отсутствие токена до FAIL в preflight, а не warning.
Confidence: HIGH (код) / impact HYPOTHESIS (зависит от доли private-образов). Action: preflight-эскалация.

### FAIL-0604 · MED · Отзыв/ротация CI_DEPLOY_KEY/VPS_SSH_KEY: отказ на SSH-auth, детект только manual check-security
1. Что происходит: ключ удалён из authorized_keys / отозван в GitHub Secrets → CI deploy падает
   «Permission denied (publickey)» мгновенно (BatchMode, без интерактива); root-канал rsync — так же.
2. Где отказ: канал — core/internal/shared/ssh_opts.py:40 `SSH_OPTS` (BatchMode=yes,
   ConnectTimeout из SoT); целостность on-node стороны — core/internal/bootstrap/security/
   deploy_channel_posture.py:62 `check_forced_command` (S7: missing/perms/owner/per-line
   command=restrict) — но вызывается ТОЛЬКО вручную `make check-security NODE=<n>`.
3. Auto-recovery: нет. 4. Broken state: нет (отказ до мутаций; receive атомарен).
5. Retry безопасен: да после починки ключа.
6. User impact: деплои проектов (git push CI) стоят до ротации; S7 не увидит рассинхрон
   CI-side (ключ в GitHub Secrets ≠ ключ на ноде) — это вне его file-scope.
7. Alert: только красный CI; S7 — manual; мониторинга SSH-auth failures нет.
8. Восстановление: runbook core/AGENTS.md §«Ротация SSH/CI-ключей» (two-key transition):
   ssh-keygen → pub в ~ci-deploy/.ssh/authorized_keys (root) → обновить Secrets → verify.
9. Минимальный фикс: шаг S7/vps_readiness в release-checklist перед launch + periodic
   forced-command ping (vps_readiness.check_vps_ready уже делает forced-command ping —
   включить в e2e-verify sweep).
Confidence: HIGH. Action: процессный (checklist), 0 кода.

### FAIL-0605 · MED · Ручная смена пароля роли БД на ноде → permanent desync .platform-db.env/.env.platform (hook не реконсилит)
1. Что происходит: DBA/оператор делает ALTER ROLE … PASSWORD на ноде → hook при следующем
   деплое видит роль существующей и НЕ трогает пароль; .platform-db.env хранит старый пароль →
   DSN проекта в .env.platform невалиден → project auth failure к postgres.
2. Где отказ: core/modules/postgres/hooks/on_project_deploy.py:237-248
   `ensure_project_db_access` («Role already exists — SKIP creation (password unchanged)»;
   «password unknown (no .platform-db.env) — credentials NOT refreshed»); password-injection в
   .env.platform — только при ПЕРВОМ создании роли (on_project_deploy.py:274-276).
3. Auto-recovery: НЕТ — обратной синхронизации hook→файл при внешней смене пароля не существует.
4. Broken state: ДА — рассинхрон персистентен до ручного вмешательства.
5. Retry безопасен: да (деплой не ломает дальше), но не чинит.
6. User impact: проект жив, но БД-операции валятся с auth error; nginx 5xx alert (grafana
   alerting) сработает только если проект отдаёт 5xx наружу.
7. Alert: косвенный только (nginx-5xx / ServiceDown при смерти exporter'а).
8. Восстановление: вернуть пароль роли (`docker exec postgres psql -U postgres -c "ALTER ROLE…"`)
   ИЛИ обновить .platform-db.env (0600) + `make sync-env` в проекте.
9. Минимальный фикс до launch: строка в runbook/AI-PLATFORM.md «пароль роли меняется ТОЛЬКО
   через drop+recreate hook'а»; post-launch: опция `--rotate-password` в hook'е (атомарно:
   ALTER ROLE + rewrite .platform-db.env + sync-env).
Confidence: HIGH (код). Action: runbook-строка; rotate-опция — backlog.
