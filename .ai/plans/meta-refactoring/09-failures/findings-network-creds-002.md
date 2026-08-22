# FAIL-findings · S2 Network partition (сети контейнеров / внешние хосты / DNS / SSH) — 002

$ARTIFACT_CONTRACT
## @purpose Pre-launch audit: failure-modes сетевой недоступности (research-only, код не менялся)
## @scope provision-environment.sh, deploy-modules.sh, context_overlay.py, firewall.py,
##        docker_user_policy.py, monitoring/config/*.yml, healthcheck/watchdog.py, tor_*.py
## @rationale максимум снижения риска / минимум churn; не дублирует 0302 (ACME), 0303 (Tor SPOF), 0401/0504
## ACCEPTANCE_CRITERIA каждый finding: file:symbol-цитата, 9 ответов, confidence, action
## IMPLEMENTS pre-launch audit wave 09-failures
## IMPACTS launch-blockers candidates (FAIL-0608)

---

### FAIL-0606 · MED · Runtime-изоляция shared-db-net/proxy-net: контейнеры живы → app-level тихий отказ с частичным alert
1. Что происходит: сегмент external-сети деградирует (DOCKER-USER misconfig, docker network
   prune + пересоздание, daemon restart race) → проект не достаёт до pgbouncer:6432/postgres,
   НО контейнеры healthy (healthcheck'и слушают 127.0.0.1 внутри контейнера) → платформа
   считает всё зелёным.
2. Где отказ: топология — core/modules/*/docker-compose.base.yml (`shared-db-net: {}`,
   `proxy-net`, `hermes-agent-net`, `observability-net` — все external);
   ingress-политика — core/internal/bootstrap/docker_user_policy.py (DOCKER-USER chain,
   DROP last); guard от «deploy без сетей» — deploy-modules.sh:31-47 TRAP[BUG] (provision
   fail-fast exit 1 — покрывает только deploy-time, НЕ runtime).
3. Auto-recovery: частично — watchdog.py рестартует unhealthy, но здесь контейнеры healthy;
   пересоздание сетей — `make converge NODE=<n>` / node-update φ11 provision.
4. Broken state: ДА — до ручной реконсиляции; restart проекта сеть не пересоздаёт.
5. Retry безопасен: да (converge идемпотентен).
6. User impact: проект 5xx/таймауты на БД-операциях при формально зелёном status-page.
7. Alert: частичный — если exporter проекта на shared-db-net умирает вместе со связностью,
   Prometheus ServiceDown (`up == 0`, alert-rules.yml:39-47) firing через 2m; postgres-side:
   ServiceDownShort (<1m, grafana alert-rules.yml:123). Если рвётся только app→db, а
   scrape-target жив — молча.
8. Восстановление: `make converge NODE=<n>`; диагностика `docker network inspect shared-db-net`.
9. Минимальный фикс: blackbox/tcp-probe pgbouncer:6432 из сети проектов в prometheus rules
   (одна recording-rule) — post-launch приемлемо; до launch — runbook-шаг в healthcheck deep.
Confidence: HIGH (топология) / сценарий HYPOTHESIS (требует runtime-воспроизведения). Action: probe-правило.

### FAIL-0607 · LOW · Ручное удаление external network/volume → fail-fast на следующем deploy (не тихий отказ)
1. Что происходит: `docker network prune`/ручное удаление → следующий деплой модуля падает
   «network declared as external, but could not be found» — громко и рано.
2. Где отказ: compose external-декларации (modules/*/docker-compose.base.yml); защита —
   deploy-modules.sh:42-47 (provision networks/volumes FATAL exit 1 при провале, TRAP[BUG]
   2026-08-05 убрал `|| true` маскировку).
3. Auto-recovery: да, косвенно — provision пересоздаёт из platform-env.yaml перед деплоем.
4. Broken state: нет (fail-fast до мутаций). 5. Retry безопасен: да.
6. User impact: минимальный — задержка деплоя на время диагностики.
7. Alert: CI red; для running-стека удаление сети = см. FAIL-0606.
8. Восстановление: `make converge NODE=<n>` или повторный deploy (provision пересоздаст).
9. Минимальный фикс: не требуется (корректное поведение; зафиксировано как позитивный контроль).
Confidence: HIGH. Action: none.

### FAIL-0608 · HIGH · GitHub недоступен при bootstrap/node-update → нода остаётся без context-overlay при формально успешном пайплайне
1. Что происходит: git clone/pull контекстного репо падает (network partition к github.com,
   rate-limit, отзыв MIRROR/deploy-key) → ensure_context_repo возвращает 1 → фазы считают
   context-deploy non-fatal → bootstrap/update завершается done_with_warnings; vhost'ы,
   node-configs overlay и проекты контекста НЕ применены, а пайплайн «почти зелёный».
2. Где отказ: core/internal/bootstrap/deploy/context_overlay.py:91 `ensure_context_repo`
   (clone failure → return 1); pull вообще всегда 0: context_overlay.py:170 `_pull_with_cache`
   («Returns 0 (always — pull failure is non-fatal)», WARN only);
   потребители: lifecycle/phases/docker.py:193-195 (φ8: «Context deploy failed (non-fatal)») и :726-730 (φ12).
3. Auto-recovery: нет (следующий run повторит фазу — done_with_warnings ≠ done, перевыполнение;
   это и есть механизм восстановления).
4. Broken state: ДА — nginx обслуживает без overlay-vhost'ов; новые проекты контекста отсутствуют.
5. Retry безопасен: да (clone/pull идемпотентны, ff-only).
6. User impact: свежая нода «готова», но проекты не публикуются (тихий partial-outage);
   на существующей — дрейф конфигурации незаметен.
7. Alert: НЕТ платформенного — только текст warnings в CI-log; burn-rate не сработает
   (деплой формально завершён). Единственный сигнал — PlatformDeployBurnRate при массовых
   фейлах project-CI, что здесь не так.
8. Восстановление: устранить причину → `make node-update NODE=<n>` (φ12 deploy-context) или
   `make deploy-context NODE=<n> CONTEXT=<ctx>`.
9. Минимальный фикс: при clone/pull failure на INIT (первом clone) эскалировать до FATAL
   φ8 (нода без overlay непригодна); на UPDATE оставить non-fatal + explicit metric/gauge
   `platform_context_repo_stale_ts` для alerting.
Confidence: HIGH (код) / severity HYPOTHESIS→HIGH при первом bootstrap в окне partition.
Action: кандидат в launch-blockers (эскалация INIT-ветки).

### FAIL-0609 · LOW · DNS failure host-нодe: внешний периметр деградирует, межконтейнерный стек продолжает работать
1. Что происходит: отказ upstream-DNS на ноде → docker pull (ghcr/docker.io), git clone/pull,
   ACME DNS-01, telegram-direct падают; контейнеры общаются по embedded-DNS (127.0.0.11) —
   внутренний стек не замечает.
2. Где отказ: host resolv.conf (вне репо); daemon.json dns НЕ конфигурируется —
   core/internal/bootstrap/docker_registry_auth.py:241 `_write_daemon_json` пишет только
   log-driver; healthcheck'и используют 127.0.0.1/имена сервисов — независимы от host-DNS.
3. Auto-recovery: нет общего; точечные retry есть (docker_compose pull retry, ACME — FAIL-0302).
4. Broken state: нет персистентного — после восстановления DNS операции воспроизводимы.
5. Retry безопасен: да везде (idempotent-фазы).
6. User impact: окно невозможности деплоев/cert-renewal; работающий прод не затронут.
7. Alert: нет прямого; последствия видны как FAIL-0602/0608 классы.
8. Восстановление: починка resolv.conf/systemd-resolved на ноде (provider console при необходимости),
   затем `make node-update NODE=<n>`.
9. Минимальный фикс: не требуется до launch (принять риск; задокументировать в runbook DNS-check
   `docker exec <c> getent hosts registry-1.docker.io`).
Confidence: HIGH (архитектура) / impact LOW. Action: runbook-строка.

### FAIL-0610 · MED · Потеря SSH-доступа оператора к ноде: lockout-safe ufw, но нет fallback-канала и нет SSH-reachability alert
1. Что происходит: sshd crash/misconfig, provider-инцидент, блокировка IP оператора →
   все push-каналы (core-deliver rsync/scp, forced-command receive, check-security) недоступны;
   pull-каналы (context-overlay git с ноды, мониторинг) продолжают работать.
2. Где отказ: периметр — core/internal/bootstrap/firewall.py:182-190 `build_rules`
   (ufw enable → default deny → `allow 22/tcp` ПЕРВЫМ, lockout-safe; 22 открыт Anywhere —
   смена IP оператора НЕ локает); DOCKER-USER — FORWARD-only, INPUT/SSH не трогает
   (docker_user_policy.py). Fallback-канала НЕТ: tor/privoxy — только outbound для telegram
   (tor_transport.py, privoxy_config.py), SSH-over-tor не реализован.
3. Auto-recovery: нет (sshd под systemd restart-политикой провайдера; platform-reboot.timer
   не про sshd).
4. Broken state: нет на ноде; теряется ONLY управление.
5. Retry безопасен: да.
6. User impact: окно без управления нодой; DR-процедуры (runbook §Runbook) требуют SSH.
7. Alert: внешний канал есть — Timeweb Zabbix passive checks (firewall.py:125-126
   ZABBIX_MONITORING_IPS → 10050) детектит host-down, но НЕ sshd-specific; платформенного
   blackbox-probe ssh:// НЕТ.
8. Восстановление: provider VPS-console/VNC → `systemctl status sshd` / ufw status;
   превентивно — второй операторский ключ в authorized_keys (S7 требует forced-command
   prefix на КАЖДОЙ строке — операторский ключ с command="…dispatch" тоже пройдёт S7).
9. Минимальный фикс: добавить второй forced-command ключ оператора ДО launch (0 кода) +
   опционально blackbox tcp-probe :22 в prometheus (post-launch).
Confidence: HIGH (периметр) / сценарий HYPOTHESIS. Action: второй ключ + probe.

### FAIL-0611 · LOW · Docker Hub token истечение/отсутствие: деградация pulls до 429 rate-limit (WARN-only)
1. Что происходит: DOCKER_HUB_USERNAME/TOKEN отсутствуют или истекли → φ3 login skip/WARN →
   pulls публичных образов идут анонимно → toomanyrequests 429 при интенсивных pull'ах
   (массовый bootstrap, restore-учения).
2. Где отказ: core/internal/bootstrap/docker_registry_auth.py:117-122 `configure_docker_auth`
   («credentials not set — rate-limit (429) may apply», return True non-fatal); login fail —
   :139-141 WARN.
3. Auto-recovery: нет; mirror.gcr.io удалён (DevPlan 164 W0-3.7, invariant 7 «auth покрывает
   rate-limit») — fallback нет.
4. Broken state: нет (pull retry + compose up internal retry).
5. Retry безопасен: да, после истечения окна rate-limit.
6. User impact: замедление/отказ bootstrap-деплоя в пиковые окна; прод не затронут.
7. Alert: нет; CI red при явном фейле pull.
8. Восстановление: ротация Docker Hub token → sops → `make node-update` (φ3 не переигрывается,
   но login повторится при следующем deploy-modules — lib/docker.sh фасад читает env каждый раз).
9. Минимальный фикс: убедиться ДО launch, что токены в secrets.env актуальны (ops-чек);
   код не трогать.
Confidence: HIGH (код) / вероятность LOW. Action: ops-чек токена.

---

## Сводка S1+S2 (network-creds)

| ID | Sev | Суть | Фикс |
|----|-----|------|------|
| FAIL-0600 | CRITICAL | AGE key loss = невосстановимость; backup Debt | выполнить age-key-backup + drill |
| FAIL-0601 | MED | stale env AGE ключ, ложная диагностика (+ps-exposure note) | лог-строка φ4/φ9 |
| FAIL-0602 | HIGH | GHCR token expiry → падение на pull, нет proactive check | expiry-check |
| FAIL-0603 | MED | GHCR anonymous fallback маскирует отсутствие токена | preflight-эскалация |
| FAIL-0604 | MED | SSH ключи: отказ на auth, детект manual-only | checklist + periodic ping |
| FAIL-0605 | MED | .platform-db.env desync после ALTER ROLE | runbook + rotate-опция backlog |
| FAIL-0606 | MED | runtime-изоляция сетей = тихий app-level отказ | db tcp-probe rule |
| FAIL-0607 | LOW | удаление external network — fail-fast корректный | none |
| FAIL-0608 | HIGH | GitHub partition при bootstrap → нода без overlay, «зелёный» пайплайн | INIT-эскалация FATAL |
| FAIL-0609 | LOW | host DNS failure — внешний периметр, стек жив | runbook |
| FAIL-0610 | MED | потеря SSH-доступа: нет fallback/alert | второй ключ + probe |
| FAIL-0611 | LOW | Docker Hub 429 деградация | ops-чек |

Launch-blocker candidates: **FAIL-0600** (CRITICAL, чисто операционный — закрыть Debt),
**FAIL-0608** (INIT-эскалация, маленький код-чендж).
