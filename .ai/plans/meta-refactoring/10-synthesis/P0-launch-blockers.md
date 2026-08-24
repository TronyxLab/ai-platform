# P0 — Launch Blockers (10-Synthesis)

Критерий отбора: **MAXIMUM PRODUCTION RISK REDUCTION / MINIMUM CODE CHURN**. В P0 попадают только CONFIRMED-дефекты, которые (а) делают запуск небезопасным сами по себе, или (б) отключают единственный механизм обнаружения/восстановления при инциденте. Гипотезы в P0 не попадают (попадают в P1 с шагом верификации). Формат каждой записи: 13 полей. Нумерация REF сквозная: P0 = REF-0001…REF-0017.

Правило severity при конфликте доменов: механика идентична → берём высшую оценку (см. refactoring-map.md §Contradictions).

---

## REF-0001 · Проекты не имеют канала сборки образа — первый деплой гарантированно умирает

* **Problem:** Ни шаблоны template-backend/frontend, ни reusable deploy-project.yml не собирают и не пушат образ проекта. Engine тянет строго `ghcr.io/<org>/<project>:<sha>` → 5 неудачных pull (~2 мин) → `handle_first_deploy` → PlatformFatalError exit 10; payload уже перезаписан, rollback невозможен. Дополнительно adopt-project генерирует вызов workflow с несуществующим input `image_tag` → CI принятого проекта детерминированно красный.
* **Evidence:** `templates/template-backend/.github/workflows/deploy.yml` — один delegating job (tar|ssh); build-push-action есть только в platform-test.yml (платформа) и adopter-generated; `engine/engine.py:218-220`, `lifecycle.py:123-134`; `project_adopter.py:224-240` vs `deploy-project.yml:53-65`.
* **Source findings:** FAIL-0801 (CRITICAL·B1), FAIL-0802 (HIGH).
* **Files:** templates/*/…/deploy.yml, core/internal/scaffold/project_adopter.py, .github/workflows/deploy-project.yml.
* **Root cause:** deploy-model «build ghcr.io → receive» реализован только для платформенных образов; проектное звено build/push отсутствует в payload-каноне.
* **Impact:** ни один scaffolded/adopted проект физически не может задеплоиться; единственный сигнал — красный CI.
* **Recommended change:** добавить build&push job в оба шаблона (копия блока `project_adopter.py:194-222`); удалить строку `image_tag:` из генератора; e2e scaffold→push→deploy на test-VPS.
* **Tests required:** e2e-сценарий на test-VPS (release-checklist); lint шаблонов (existing templates-check).
* **Regression risk:** низкий (новые файлы + 1 удалённая строка); риск застрять в GHCR-правах org.
* **Dependencies:** без этого REF-0002/REF-0003 невоспроизводимы end-to-end.
* **Estimated complexity:** M (0.5–1 день).
* **Why now:** B1 — нулевая функциональность платформы для проектов.
* **Why not larger refactor:** канал доставки менять не нужно — недостаёт ровно одного job'а по готовому образцу.

---

## REF-0002 · Postgres-хук провизии БД никогда не вызывается + незаживающий orphan-role

* **Problem:** Хук создания role/DB/GRANT не зарегистрирован (`module.yaml` без секции `hooks:`), gate-тест закрепляет отсутствие. Даже будучи вызванным: сбой записи кредов после CREATE ROLE оставляет роль с навсегда потерянным паролем — retry ранним return пропускает GRANT/реген; результаты GRANT не проверяются; psql-ветка без timeout=60.
* **Evidence:** `core/modules/postgres/module.yaml:35-37`; gate `tests/gates/test_gate_module_hooks.py:342-344` asserts `"postgres" not in registered`; `hooks/on_project_deploy.py:237-271` (early-return :243-248, `if created:` :275), `:264-265` (GRANT unchecked), `:132-147` (нет timeout).
* **Source findings:** BUG-0604 (CRITICAL), BUG-0605+DATA-201/DATA-501 (HIGH), DATA-205, BUG-0206, FAIL-0101 (CRITICAAL·B2), FAIL-0605.
* **Files:** core/modules/postgres/module.yaml, hooks/on_project_deploy.py, tests/gates/test_gate_module_hooks.py, post_deploy_chain (без правок — регистрация оживит цепочку).
* **Root cause:** контракт «роль/БД/GRANT создаются хук-ом при деплое» (root AGENTS.md) не реализован; idempotency реализована как early-return вместо ensure-convergence.
* **Impact:** каждый DB-проект деплоится с DSN к несуществующей роли («role does not exist»), CI зелёный; потеря `.platform-db.env` = перманентная потеря доступа (только DROP ROLE вручную).
* **Recommended change:** (1) зарегистрировать `hooks.on_project_deploy` в module.yaml + обновить gate; (2) переписать provisioning как единый ensure: role_exists+no-creds → ALTER ROLE PASSWORD + запись кредов + GRANT + реген .env.platform; (3) проверять результат каждого GRANT (failed → CRITICAL-счётчик); (4) timeout=60 во всех ветках `_psql`; (5) rider: `REVOKE CONNECT ON DATABASE <db> FROM PUBLIC` (закрывает SEC-0008 той же правкой).
* **Tests required:** unit на ensure-convergence (role_exists/no-creds path); port тестов shared-db seam из tests/e2e/test_shared_db_access.py в ci-docker gate (TEST-18); обновлённый hook-gate.
* **Regression risk:** средний — хук начнёт выполняться на каждом деплое; идемпотентность обязательна (покрывается тестами).
* **Dependencies:** независим; нужен для осмысленного e2e из REF-0001.
* **Estimated complexity:** M (0.5 дня).
* **Why now:** B2 — все needs.database проекты мертвы с первого дня.
* **Why not larger refactor:** механика хука существует и тестируема — не хватает регистрации и ensure-семантики в одном файле.

---

## REF-0003 · Неуспешный healthcheck = PARTIAL = exit 0 = зелёный CI без rollback

* **Problem:** При unhealthy poller ставит PARTIAL, `is_success()` включает PARTIAL → receive выходит 0, CI зелёный, Telegram шлёт «deployed», post-deploy chain исполняется поверх больного деплоя; документированный «rollback on healthcheck failure» не реализован нигде.
* **Evidence:** `orchestrator.py:556` (PARTIAL), `:151-153`, `receive_flow.py:559-568` (exit 0), `post_deploy_chain.py:15` (PARTIAL→info), `orchestrator_cli.py:678` (rc=0).
* **Source findings:** BUG-0602 (HIGH), DATA-602 (HIGH), FAIL-0102=FAIL-0708 (HIGH), TEST-01/TEST-04 (главный тестовый blind spot).
* **Files:** core/internal/deploy/orchestrator.py, deploy/receive_flow.py, hooks/post_deploy_chain.py, orchestrator_cli.py.
* **Root cause:** success-predикат шире health-факта (системный паттерн «best-effort swallowing»).
* **Impact:** сломанный образ обслуживается с зелёным CI/Telegram; инцидент невидим для всех сигналов платформы.
* **Recommended change:** unhealthy/timeout → статус FAILED (или ROLLED_BACK через REF-0004 ветку) + exit≠0 + notify severity=critical; PARTIAL остаётся внутренним, но не success.
* **Tests required:** DI-тест poller=unhealthy → rc≠0 (+rollback если REF-0004 готов); severity-mapping тест уведомлений (TEST-04).
* **Regression risk:** средний: легитимные slow-start деплои начнут падать — смягчается start_period/окном из REF-0103.
* **Dependencies:** REF-0004 (чтобы unhealthy-ветка реально откатывала, а не просто красила CI); иначе достаточно FAILED+alert.
* **Estimated complexity:** S (<2 ч кода + тесты).
* **Why now:** главный контракт платформы «healthcheck rollback» сегодня ложно-зелёный.
* **Why not larger refactor:** меняется предикат + exit-code, не конвейер.

---

## REF-0004 · Контур rollback структурно сломан: снапшоты без compose_state → doomed pull → всегда FAILED

* **Problem:** Снапшоты создаются без compose_state → previous_image=None → docker_tag пропускается → engine.deploy(ref="previous-rollback") пытается ПУЛЛИТЬ локальный тег из GHCR (~135 s ретраев ×5) → PlatformFatalError → FAILED, даже когда engine уже сам откатил контейнер (double rollback). Целевой снапшот выбирается «latest» без фильтра по health_status (можно откатиться на заведомо нездоровый релиз). После отката healthcheck не перепроверяется.
* **Evidence:** `orchestrator.py:549-554` (create_snapshot без compose_state), `history.py:171/:333-345`, `:1147-1164` (previous-rollback pull), `engine/lifecycle.py:66-113`, `flow.py:60`, `docker_compose.py:658-665`; `orchestrator.py:495` latest_snapshot; `engine/engine.py:251-265` (no re-verify).
* **Source findings:** BUG-0101+BUG-0502+BUG-0601 (3 направления сошлись), DATA-604 (HIGH), FAIL-0707, BUG-0503, FAIL-0804, TEST-03 (rollback-тела 0% исполнения).
* **Files:** core/internal/deploy/orchestrator.py, deploy/audit/history.py, deploy/engine/{engine,lifecycle}.py, receive_flow.py.
* **Root cause:** схема снапшота никогда не заполнялась; ответственность rollback разорвана между engine и orchestrator без координации.
* **Impact:** контрактный уровень отката недостижим (ROLLED_BACK unreachable); ~2.5 мин doomed-ретраев и lock-hold на каждый health-fail; оператор начинает ручное вмешательство на уже откаченном сервисе.
* **Recommended change:** persist `{"previous_image": <id>}` в снапшот до compose-up; skip snapshot-rollback при `rollback_performed=True`; `latest_snapshot(require_healthy=True)` с WARN-fallback; после perform_rollback — один wait_health + поле rollback_verified; payload восстанавливать только после успешного compose-rollback.
* **Tests required:** TEST-03 набор: реальный _rollback_compose/_restore_payload_files, compose_rollback=True→DEPLOYED+audit-row, False→FAILED+"Rollback failed"; unhealthy→ROLLED_BACK сквозной.
* **Regression risk:** средний — трогаем аварийный путь; покрывается characterization-тестами до правки (docker_orchestrator — freeze-файл, здесь не трогаем).
* **Dependencies:** REF-0003 (ветка вызова); BUG-0100 rider: pull-failure при существующем деплое не должен идти в first-deploy FATAL (engine.py:218 branch by is_first_deploy).
* **Estimated complexity:** M (0.5–1 день).
* **Why now:** это «страховочная сетка» платформы; без неё каждый плохой релиз = ручной инцидент.
* **Why not larger refactor:** починка заполнения поля + 3 guard-условия; разделение rollback_manager остаётся пост-launch.

---

## REF-0005 · Параллельный деплой: failed-дети считаются успешными + вечный hc_done-маркер гасит последний healthcheck

* **Problem:** `drain_all_count` игнорирует waitpid-статус (unconditional deployed+=1) → group_failed=0 → атомарный откат группы не срабатывает, exit 0 на поломанном стеке. Маркер `.hc_done_in_deploy` пишется безусловно (даже при failed-группах) и переживает прогон → φ11 пропускает единственный глубокий healthcheck по маркеру прошлого запуста (unlink→rewrite цикл). Отдельно: реальный drain очищает pid_to_name до вычисления all_names → групповые healthcheck идут по 0 модулям (живой баг, замаскированный fake-drain в тестах).
* **Evidence:** `parallel_runner.py:498-507` vs корректный `drain_completed_count`:467-475; `deploy_orchestrator.py:553-554/:928-938`; `phases/docker.py:599-610`; `parallel_runner.py:344`.
* **Source findings:** BUG-0301≡BUG-0801 (HIGH/CRITICAL), PERF-002, BUG-0501≡BUG-0703, A-10(d)/ARCH-072 echo, TEST-02 (TOP-2).
* **Files:** core/internal/bootstrap/deploy/{parallel_runner,deploy_orchestrator}.py, bootstrap/lifecycle/phases/docker.py.
* **Root cause:** финальный blocking drain без классификации exit-status (асимметрия с sibling-drains); маркер без run-scoping.
* **Impact:** DEPLOY_PARALLEL=true: деградация уходит в прод с зелёным вердиктом и без единого healthcheck; watchdog остаётся единственным целителем (сам частично сломан — REF-0014).
* **Recommended change:** в drain_all_count зеркалировать WIFEXITED/WEXITSTATUS (failed++/failed_names); маркер писать только при `failed==[]` + run-id в имени + удалять на старте init/update; all_names собирать ДО drain.
* **Tests required:** тест с РЕАЛЬНЫМ drain_all_count + mocked waitpid (красный сегодня): имена совпадают с pid_to_name, non-empty all_names; fork-smoke на concurrency.
* **Regression risk:** низкий ~5-строчный фикс + семантика маркера; затрагивает W5-E1 контракт — прогнать make check TEST_FILE=test_parallel_runner.py.
* **Dependencies:** усиливает REF-0003/0004 (без них failed-группы всё равно молча зелёные).
* **Estimated complexity:** S (2–4 ч).
* **Why now:** три аудита независимо подтвердили один дефект главного safety-механизма параллельного пути.
* **Why not larger refactor:** точечные guard'ы; переписывание parallel_runner запрещено (freeze docker_orchestrator/parallel кластера).

---

## REF-0006 · L1 privilege-gate не смотрит volumes/host-режимы и стоит только в receive-канале

* **Problem:** Единственный гейт между maintainer'ом проекта и root ноды проверяет privileged/cap_add/devices, но НЕ volumes (docker.sock, `/`), network_mode:host, pid, userns_mode, sysctls. При этом гейт вызывается ТОЛЬКО в ReceiveFlow — DeployOrchestrator.deploy (φ8/φ12, deploy-context) исполняет compose от root вообще без проверки. Сломанный YAML проходит L1 (parse filed as L2-warning).
* **Evidence:** `verify_contracts.py:286-294` (L1 set), `:637` TRAP обещает pid/sysctls — не реализовано; grep volume/socket inspection = 0; sole call `receive_flow.py:408-420`; `orchestrator.py:213-287` без verify; `verify_contracts.py:275-277` compose-config-valid→KLASS_L2.
* **Source findings:** SEC-0011 (CRITICAL·B1sec), SEC-0030 (HIGH·B2sec), FAIL-0706, TEST-05 (нет негативов receive/remove).
* **Files:** core/internal/deploy/verify_contracts.py, deploy/receive_flow.py, deploy/orchestrator.py.
* **Root cause:** deny-set неполон при осознанном TRAP; размещение гейта инвертировало замысел DevPlan 176 A.2.
* **Impact:** один коммит `volumes: ["/var/run/docker.sock:/sock"]` = root ноды (ci-deploy в docker-группе) + все секреты; TOCTOU-вариант после гейта закрывается переносом гейта внутрь deploy().
* **Recommended change:** `_check_dangerous_volumes` (deny socket-mounts + абсолютные host-binds вне allowlist + требование named volumes) + deny-keys (network_mode:host/pid/userns/cgroup/sysctls); вызвать `verify_project_contracts(dir, l1_only=True)` внутри DeployOrchestrator.deploy перед _apply_deploy; compose-config-valid → блокирующий в l1_only.
* **Tests required:** R5-негативы с точным C1-input (socket-mount, `/`-bind); параметризованные traversal-негативы receive/remove через _dispatch (TEST-05).
* **Regression risk:** средний: легитимные проекты с bind-маунтами начнут блокироваться — allowlist минимален, документируем исключения.
* **Dependencies:** SEC-0013 (docker-группа ci-deploy) — принимаемый residual, зафиксировать в доке, не чинить сейчас.
* **Estimated complexity:** M (0.5–1 день).
* **Why now:** CRITICAL-цепочка компрометации ноды через любой подключённый репозиторий.
* **Why not larger refactor:** расширение одного валидатора + одна точка вызова; socket-proxy/rootless — пост-launch.

---

## REF-0007 · Секреты светятся: argv /proc, world-readable tmp, 0644 .env.platform

* **Problem:** AGE master key и CI_DEPLOY_KEY передаются ВНУТРИ ssh-argv (видны в /proc ~30 мин любому локальному аккаунту, включая ci-deploy) и логируются в deliver_fallback; secrets.env.tmp пишется plain open("w") с chmod 0600 ПОСЛЕ записи без cleanup; реген .env.platform пишет пароль БД 0644 root; litellm-ключи — plaintext JSON в tmpdir; openssl passwd/sops --set получают секреты argv'ом.
* **Evidence:** `ssh_cmd_builder.py:190-198` (+ложный TRAP :170), `bootstrap.sh:92-98`, `core_deliverer.py:637-646`; `secrets_manager.py:524-537/:707-715`; `gen_env_platform.py:498-501`; `key_provisioner.py:331-341/:391-399`; `crypto.py:83-86`, `secrets_manager.py:401-408`.
* **Source findings:** SEC-0015 (HIGH·B6), SEC-0016 (HIGH·B7), SEC-0017 (MED-HIGH·B8), SEC-0029 (HIGH·B4 — symlink-safe write того же atomic_writer свипа), SEC-0003, DATA-1001, DATA-1004, AI-0007, FAIL-0601.
* **Files:** shared/ssh_cmd_builder.py, entrypoints/bootstrap.sh, bootstrap/core_deliverer.py, bootstrap/lifecycle/secrets_manager.py, scaffold/gen_env_platform.py, llm/key_provisioner.py, shared/crypto.py, bootstrap/deploy/context_deployer.py (stub-compose write).
* **Root cause:** out-of-band доставка ключей не реализована; canonical atomic_writer (mkstemp 0600) обойдён в ~6 местах.
* **Impact:** любой локальный аккаунт читает master AGE key (=все секреты ноды) и приватный deploy-ключ; crash оставляет permanent world-readable копию всех секретов; cross-tenant утечка DSN-пароля.
* **Recommended change:** доставка AGE/SSH-ключей вне argv (stdin → `bash -s`, или SCP 0600 root-файл + unset), redact в логах, явный WARN при env-over-file (FAIL-0601); atomic_writer(mode=0600) для secrets.env.tmp/litellm-keys + umask 077 в lifecycle entrypoints; atomic_write_text(0640)+chown ci-deploy для регена .env.platform + chmod после copy в receive_flow; sops/openssl значения через stdin.
* **Tests required:** redaction-тест stderr (TEST-07); тест mode=0600 от creation для writer'ов; import-time assert отсутствия signal-хаков (см. REF-0013).
* **Regression risk:** средний: транспортная замена в bootstrap.sh/core_deliverer — гейтить staging-прогоном node-update; writer-замены механические.
* **Dependencies:** DEP-0017: имя AGE_SECRET_KEY заморожено — не переименовывать при переносе.
* **Estimated complexity:** M (1 день на весь своп).
* **Why now:** crown jewel угрозной модели; узкое окно до запуска, когда ещё нет реальных tenant'ов.
* **Why not larger refactor:** не строим vault/SOPS-интеграцию — меняем транспорт и режим файлов.

---

## REF-0008 · TLS-конвейер: S3-restore без privkey, scan не видит restored-сертификаты, self-signed молчит

* **Problem:** Restore из S3 считает пару валидной по наличию одного fullchain.pem (privkey опционален, match не проверяется) → DR-рестарт ноды = TLS outage всех доменов; cert_is_valid игнорирует privkey (crash между двумя cp = несогласованная пара «valid on disk» → nginx падает, система здорова); expiry-scan смотрит только `/root/.acme.sh`+fullchain.cer → S3-restored сертификаты вне renewal И scan → гарантированный протухший TLS ≤90 дней без алертов; self-signed fallback нигде не алертится (~76 дней тишины); ACME-retry без backoff жжёт rate-limit; needs.domain без валидации → path traversal/RCE в cert pipeline (root).
* **Evidence:** `s3_ssl_cache.py:564-575/:605/:392-396`; `ssl_certs.py:362-397`; `cert_expiry_check.py:48/:60`; `cert_orchestrator.py:454-460/:486-497/:891-899`; `issue_cert.py:95/:393-398/:586-590`; цепочка needs.domain: `node.schema.json`→`node_yaml/projects.py:204-241`→`context_deployer.py:795-829` (контраст: `vhost_renderer.validate_vhost_identifiers:536-576`).
* **Source findings:** FAIL-0300 (CRITICAAL·B4), BUG-0700≡BUG-0901 (HIGH), DATA-701 (HIGH), FAIL-0301/0302 (HIGH), BUG-0207, SEC-0026 (HIGH·B5), BUG-1001 (rider), BUG-0606, FAIL-0309.
* **Files:** core/internal/bootstrap/{s3_ssl_cache,cert_orchestrator,issue_cert,cert_expiry_check,cron_installer}.py, shared/ssl_certs.py, node_yaml/projects.py, project_registry.py, context_deployer.py.
* **Root cause:** два disjoint cert-store; validity-predicate без pair-match; валидатор FQDN применяется в одном шаге от sink'а, но не на входе.
* **Impact:** полный ingress-TLS outage в DR-сценарии (единственном, ради которого кэш существует) без алертов; условный persistent root-RCE через attacker-owned домен.
* **Recommended change:** privkey обязателен в download_cert + openssl pubkey-match перед success; cert_is_valid проверяет пару; expiry-unit получает `--cert-dir /etc/letsencrypt/live` + fullchain.pem в CERT_FILENAMES; TG-alert на source=self_signed; sleep/backoff между ACME-attemptами (shared/retry); apply validate_vhost_identifiers на register_project И orchestrate_certs entry (fail-fast) + reloadcmd без string-interpolation (shlex.quote/env); install-cert через tmp+rename; отказ self-signed overwrite существующего LE-сертификата (BUG-0606).
* **Tests required:** pair-match unit (valid/mismatch/missing); scan-coverage тест на tmp-каталоге; validator-negative на `../`-домен (R5).
* **Regression risk:** средний: scan-расширение может дать ложные срабатывания на legacy-файлы — проверить на test-VPS.
* **Dependencies:** независим; сверка S3↔live (FAIL-0309) — строка в DR-drill REF-0009.
* **Estimated complexity:** M-L суммарно, но декомпозируется на 6 XS/S фиксов.
* **Why now:** B4 гарантирует silent TLS outage ≤90 дней; B5 — root-write примитив.
* **Why not larger refactor:** не объединяем хранилища сертификатов (миграция acme.sh state) — добавляем покрытие scan+валидацию пары.

---

## REF-0009 · Backup/DR-цепочка врёт про RPO: чистка удаляет незагруженное, freshness меряет mtime лога, restore льёт на живой кластер

* **Problem:** (1) Нет auto-retry выгрузки: при S3-outage dump лежит в spool, но cleanup удаляет всё >7 дней независимо от статуса загрузки → off-site копия не создаётся вовсе; (2) BackupFreshness считается по mtime ЛОГА (cron refresh'ит его на старте) → упавшая ночью задача выглядит свежей, dashboards зелёные (уже случавшийся класс pgbouncer-P1); (3) pg_dumpall | gzip уходит в S3 БЕЗ клиентского шифрования — все БД всех проектов в открытом виде за одним bucket-ключом; (4) restore: gunzip|psql в ЖИВОЙ кластер без ON_ERROR_STOP и pre-snapshot → «Restore complete» над полу-смесью; (5) reboot-timer 04:30 пересекает окно дампа/выгрузки; cron-строки без flock; docs говорят PostgreSQL 16, стоит 18.4.
* **Evidence:** `backup-cleanup.sh:35` + отсутствие retry-job (grep); upload.py invariant «NEVER deleted… until confirmed» ничем не enforced; `backup_collector.py:73-84` (mtime→age<25h⇒ok); upload.py:283 (нет SSE/age); postgres/Makefile:59-63; `reboot_policy.py:99-108`; crontab:28-42; module.yaml:12 vs compose:41.
* **Source findings:** BUG-0802≡DATA-502 (HIGH), BUG-0803≡FAIL-0903 (HIGH)+FAIL-0405, SEC-0018 (MED-HIGH·B15)≡DATA-503, DATA-504≡FAIL-0803 (HIGH), FAIL-0904/0905, AI-0070 (двойная установка crontab), AI-0069 (doc), FAIL-0600 (B5 ops).
* **Files:** core/modules/backup-cron/scripts/*, core/internal/healthcheck/metrics/backup_collector.py, core/modules/postgres/Makefile, reboot_policy.py, core/modules/postgres/module.yaml(+docs).
* **Root cause:** fire-and-forget upload без durable-маркера; freshness-сигнал = «cron запустился», не «бэкап удался»; DR-процедура никогда не харденилась.
* **Impact:** заявленный RPO 24h off-site не обеспечивается; в день катастрофы restore добьёт кластер; единственная защита — IMP:9 строка в логах.
* **Recommended change:** `.uploaded` sentinel (или S3 HEAD-confirm) → cleanup удаляет только подтверждённое; ежедневный spool-rescan retry; touch `{spool}/postgres/.last_verified` только после gzip -t OK, collector читает маркер; age-encrypt перед upload (+1 decrypt шаг в runbook restore); Makefile restore: down → psql `-v ON_ERROR_STOP=1` → up + mandatory pre-restore pg_dumpall; reboot OnCalendar → 05:45 (или lock-проверка backup-job); flock -n на 4 cron-строках; убрать двойную установку crontab (Dockerfile:97/101); doc-fix PostgreSQL 18.4 (до restore-drill!); выполнить `make age-key-backup` + drill на test-VPS (FAIL-0600, 0 LOC).
* **Tests required:** unit: cleanup не трогает unsentinel-файлы; collector читает stamp; restore-recipe dry-структурный тест; requires_node restore-drill в release-checklist.
* **Regression risk:** низкий-средний: изменение semantics очистки — прогнать полный цикл бэкапа на test-VPS.
* **Dependencies:** drills из release-checklist (reboot, restore, age-key-backup) — обязательная часть волны 4.
* **Estimated complexity:** M (1 день на пакет; половина — config/scripts).
* **Why now:** вся ценность платформы с данными клиентов держится на этой цепочке, а она сейчас фикция.
* **Why not larger refactor:** не строим outbox/WAL-архиватор v2 — маркер + stamp + encrypt + 3 строки рецепта.

---

## REF-0010 · Мониторинг: очередь langfuse молча испаряется (allkeys-lru), pgbouncer/minio/logging невидимы, disk-full гасит собственную сигнализацию

* **Problem:** Единственная реальная очередь платформы (langfuse web→langfuse-redis→worker) работает с `--maxmemory 64mb --maxmemory-policy allkeys-lru` → любой backlog тихо выбрасывается при 200-OK ingestion (BLOCKER). pgbouncer — единственный фасад БД — вообще без scrape/alerts (exporter ходит мимо него на postgres:5432); нет pg_up/redis_up/minio/loki/alloy правил (exporter-alive маскирует смерть демона); DiskSpace/HighMemory имеют noDataState="OK" → при ENOSPC Prometheus сам перестаёт писать и гасит свой alarm; предупреждения доставляются без push, critical re-notify раз в 24ч; alert-rules рендерятся в каталог, который prometheus не монтирует (AI-0004, rank#1 — активная silent alert loss); watchdog молчит на crash-loop>5 и на skip; внешний heartbeat отсутствует (падение всей observability = тишина).
* **Evidence:** `langfuse/docker-compose.base.yml:181-182`; `infra-metrics/docker-compose.base.yml:13` (DSN postgres:5432); `prometheus.yml.tmpl:47-157` (нет pgbouncer/langfuse/minio/loki jobs); `alert-rules.yml:81/:223/:275` (up==bool, noDataState OK); `monitoring/docker-compose.base.yml:104-105` (tsdb без retention.size); contact-points.yml:67/:88-91; `deploy_paths.py:304` fallback vs platform-infra.yaml:271; `config_renderer.py:702` (output_dir не пробрасывается); `watchdog.py:90/:372`.
* **Source findings:** FAIL-0200 (CRITICAAL·B3, 3-way agreement), FAIL-0100/1001, FAIL-0201/0204, FAIL-1000, FAIL-1002, FAIL-0504, FAIL-1003/1004, FAIL-0402/0403, AI-0004 (#1 TOP30), FAIL-0202 (maxmemory==cgroup), BUG-1002/AI-0065 (canon-collector rider).
* **Files:** core/modules/{langfuse,redis}/docker-compose.base.yml, core/internal/shared/deploy_paths.py, monitoring/config_renderer.py(+constants), prometheus.yml.tmpl, alert-rules.yml/platform-alerts.yml, infra-metrics compose, watchdog.py (notify-skip), status-page collectors (canon rider).
* **Root cause:** eviction-политика скопирована из cache-дефолтов в очередь; мониторинговая поверхность описана «по памяти», а не по факту; noDataState выбран anti-spam ценой слепоты.
* **Impact:** платформа слепа именно во время инцидентов: потеря трейсов, смерть фасада БД, disk-full и падение самого мониторинга — всё без единого сигнала; heartbeat закрывает класс «who monitors the monitor».
* **Recommended change (почти всё config/YAML):** langfuse-redis → noeviction (+maxmemory↑); redis main maxmemory 192mb (headroom); pgbouncer-exporter + job + pg_up/pgbouncer rules; второй redis_exporter instance для langfuse-redis + evicted_keys alert; minio job; scrape loki/alloy + up-rules; DiskSpace/HighMemory → noDataState=Alerting; warning-push enable + critical repeat_interval 2h; canonicalize render-dir для alert-rules (deploy_paths fallback ↔ mount); watchdog TG на skip-path; внешний heartbeat (healthchecks.io-class) на status-page /health в тот же TG.
* **Tests required:** gate: renders land in mounted dir (path-parity тест); alert-rule presence smoke (yaml-parse); остальные — runtime-проверка на test-VPS.
* **Regression risk:** низкий (декларативные изменения); риск alert-noise — настроить group_wait.
* **Dependencies:** heartbeat зависит от стабильности status-page (REF-0018) — ставить после, но не позже волны 3.
* **Estimated complexity:** M (1 день, преимущественно YAML).
* **Why now:** B3 + «слепота в момент инцидента» — худшее соотношение вред/цена всего аудита.
* **Why not larger refactor:** никакой новой observability-стека — 15 правил + 2 экспортера + 3 строки compose.

---

## REF-0011 · Конкурентность деплоя: FileLock деградирует в no-lock, payload копируется вне лока, retry повторяет мутацию

* **Problem:** Lock-open failure (EACCES на root-owned 0644 lock после root-bootstrap — штатный сценарий) → WARN → acquire возвращает успех БЕЗ лока, навсегда; ReceiveFlow копирует payload ДО взятия per-project flock → интерливинг os.replace = mixed payload при двух быстрых push; rollback()/remove() входят без лока во время активного receive; CI workflow без concurrency-group; TimeoutExpired считается retryable → весь receive повторяется 3× (двойные compose-циклы/снапшоты, либо ложный CI-red «Concurrent deploy blocked»); process-global _REENTRANT depth может тихо отключить flock.
* **Evidence:** `file_lock.py:164-181/:196-199/:62/:253-260`; `receive_flow.py:423-462` → `orchestrator.py:295-297/:472`; `orchestrator.py:711-771/:817-874` (без flock); deploy-project.yml (нет concurrency:); `channels/base.py:190-196`, `forced.py:116-123`, timeouts.py:130; file_lock.py:62/:191.
* **Source findings:** BUG-0104+BUG-0303, DATA-302+DATA-806, FAIL-0702 (Batch B), FAIL-0701+AI-0006, BUG-0302, FAIL-0700, BUG-0202+DATA-601+FAIL-0703, A-19, TEST-32/33 (FileLock без тестов).
* **Files:** core/internal/shared/file_lock.py, deploy/receive_flow.py, deploy/orchestrator.py, .github/workflows/deploy-project.yml (+template), channels/base.py.
* **Root cause:** degrade-to-no-lock задуман для dev-машин и не различает случаи; lock-периметр уже mutation-периметра; at-least-once retry поверх POST-like операции.
* **Impact:** T9.1-защита выключена ровно в штатном root→ci-deploy сценарии; mixed payload с зелёным CI; двойные полу-applied деплоя при сетевых таймаутах.
* **Recommended change:** PermissionError на СУЩЕСТВУЮЩЕМ файле → FileLockError (degrade только dir-permission/dev-случай); создавать locks 0664/chown ci-deploy (паттерн history.py:188); flock в начале ReceiveFlow (reentrant → depth+1); завернуть rollback()/remove() в тот же lock; `concurrency: {group: deploy-${{ inputs.project_name }}, cancel-in-progress: false}`; retryable = not success AND exit_code != 124; depth → instance attr + try/finally.
* **Tests required:** базовый test_file_lock.py (nested acquire/release, EACCES-existing→raise, timeout-poll) — TEST-32; interleave-тест copy-vs-lock.
* **Regression risk:** средний: fail-loud lock может сломать dev-процессы с чужими /var/lock правами — покрыть dev-кейс тестом.
* **Dependencies:** DATA-303 глобальный lifecycle mutex — опционально сюда же (S); иначе P1.
* **Estimated complexity:** M (0.5–1 день).
* **Why now:** Batch B целиком — «deploy-integrity»; без этого любые ретраи CI во launch week множат инциденты.
* **Why not larger refactor:** не редизайним locking — 6 точечных изменений известной формы.

---

## REF-0012 · CI supply-chain: 22 actions на плавающих тегах, gitleaks без checksum, PR-head код со static secrets

* **Problem:** Все 22 external GitHub Actions pinned на mutable tags (включая third-party trivy/codeql); jobs с VPS_SSH_KEY/CI_DEPLOY_KEY/AGE_SECRET_KEY; setup-gitleaks скачивает binary без SHA256; platform-test.yml на pull_request_target чекаутит PR-head и гонит `make gate` с DOCKER_HUB_*/LITELLM_MASTER_KEY/TELEGRAM_* в env; кэши (venv, /usr/local/bin/gitleaks) персистентны между PR и main (poisoning-усилитель); deploy-project.yml без permissions:{} и с сырой ${{ inputs.* }} интерполяцией в run:, SSH-флаги руками без ConnectTimeout.
* **Evidence:** `core-deploy.yml:93` (@v7), security-scan.yml:88/:108, deploy-project.yml:97/:177/:374; action.yml:39-42; platform-test.yml:52/:107/:87,:434-435,:133-136; setup-gitleaks cache key :31-34; ssh_opts SoT `ssh_opts.py:40-51` не используется в deploy-project.yml:342,362,372.
* **Source findings:** SEC-0038 (HIGH·B10, confidence 1.0), SEC-0039 (MED·B17), SEC-0009, SEC-0040, SEC-0010, AI-0022, A-13/AI-0022 (SSH flags).
* **Files:** .github/workflows/*.yml (22 refs), .github/actions/setup-gitleaks/action.yml, deploy-project.yml.
* **Root cause:** digest-pin политика применена к образам, но не к actions/binary; privileged trigger смешан с secrets.
* **Impact:** пере-point тега upstream = кража deploy-ключей без какого-либо доступа к репо; trojaned scanner отключает leak-детект во всех job'ах; hostile PR печатает LITELLM_MASTER_KEY.
* **Recommended change:** pin всех 22 actions на full commit SHA (`@<sha> # vX`) — dependabot обновляет; sha256-verify gitleaks в action; развести PR-job'ы: unprivileged build + secrets-jobs вне pull_request_target (или environment approval для форков); disable cache save/restore при pull_request_target (никогда не кэшировать /usr/local/bin); permissions:{} + env-indirect quoted interpolation; SSH_OPTS из `python3 -m core.internal.shared.ssh_opts --shell`.
* **Tests required:** структурный gate: все uses — SHA-form (regex, 30 строк); actionlint-style проверка отсутствия raw ${{ }} внутри run:.
* **Regression risk:** низкий (механика), средний по PR-workflow реструктуризации — проверить оба сценария PR.
* **Dependencies:** независим; делать в волне 0 (чистая механика).
* **Estimated complexity:** S (0.5 дня).
* **Why now:** единственный вектор компрометации, не требующий доступа к репо; XS-цена.
* **Why not larger refactor:** никаких self-hosted runner/oidc-перестроек — пины + гигиена.

---

## REF-0013 · Секреты-конвейер рапортует успех с пустым результатом и затирает operator-секреты

* **Problem:** φ4 глотает ошибки source/autogen как WARN → фаза done → skip навсегда; decrypt `_yaml_to_env` молча теряет non-flat YAML и атомарно пишет ПУСТОЙ secrets.env с «decrypted successfully», exit 0; затем Step 3.5 merge-from-parsed-copy перезаписывает файл набором `{} + generated` — необратимо уничтожая GHCR_PULL_TOKEN/TELEGRAM_*/PLATFORM_MASTER_*; свежий decrypt проигрывает stale os.environ (`if k not in os.environ`); manifest↔enc.yaml drift никто не сверяет; `make secrets-unlock NODE=X` расшифровывает alphabetically-first ноду; platform_config latch `_loaded=True` до чтения файла навсегда фиксирует пустые defaults; signal-handler'ы decrypt_secrets живут на module-level (hijack импортёра) и итерируют живой список temp-файлов.
* **Evidence:** `helpers/secrets.py:117-123/:133-135`; `decrypt_secrets.py:188-207/:338-359/:439/:141-144/:83`; `secrets_manager.py:521/:537/:199-201/:458-460`; `decrypt_secrets.py:389-391`; `makefiles/ci.mk:144`+`decrypt_secrets.py:450-456`; `platform_config.py:75-78`; DEP-0026 live-list.
* **Source findings:** BUG-0102+BUG-0905, BUG-0103, DATA-1002, DATA-1005, DATA-1006, DEP-0040 (freshness, optional), AI-0023, DEP-0025=A-20, A-09+DEP-0026+HYP-03, TEST-07/08 (непротестированные контракты).
* **Files:** bootstrap/lifecycle/helpers/secrets.py, lifecycle/secrets_manager.py, lifecycle/phases/secrets.py, secrets/decrypt_secrets.py, config/platform_config.py, makefiles/ci.mk.
* **Root cause:** success-marker до доказательства (системный паттерн #1); broad except; env-shadowing inverted precedence.
* **Impact:** нода бутстрапится с нулём секретов в окружении (отложенный взрыв на первом использовании) либо теряет irrecoverable operator-секреты; многонодовая операционная ловушка unlock.
* **Recommended change:** fail-fast: непустой enc-файл + 0 распарсенных ключей → PlatformFatalError; guard Step 3.5 (`not env_vars and file nonempty` → abort); narrow excepts до (ImportError, OSError); postcondition: parsed ⊇ {required ∧ sops} (DATA-1006 verifier); после decrypt file-wins (override-allowlist для operator-vars); NODE-filter/reject stray arg; `_loaded=True` после успешного load + reset_cache(); signal/atexit регистрацию перенести в main(), итерировать `list(_TEMP_FILES)`, +SIGHUP handler и стартовый sweep /dev/shm.
* **Tests required:** TEST-07 (stderr-redaction), TEST-08 (signal-handler contract), empty-parse→fatal unit, merge-guard unit, NODE-dispatch unit.
* **Regression risk:** средний: fail-fast может вскрыть существующие кривые enc-файлы — заранее прогнать decrypt на всех нодовых артефактах.
* **Dependencies:** транспортная часть ключей — REF-0007; имя AGE_SECRET_KEY заморожено.
* **Estimated complexity:** M (0.5–1 день).
* **Why now:** повторение задокументированного P0-класса 2026-07-23 одним уровнем глубже.
* **Why not larger refactor:** не переписываем secrets-manager — добавляем 5 guard'ов и двигаем side-effects из import-time.

---

## REF-0014 · Самолечение ноды не работает: converge R9 ломает каждый docker-модуль, watchdog сжигает cooldown впустую

* **Problem:** R9 self-heal вызывает compose c голым `-f base.yml` (без profile/env-file/root-compose) — три режима отказа воспроизведены живьём; детекция контейнеров substring-фильтром (`name=monitoring` → 0 рядов; `name=redis` матчит langfuse-redis/redis-exporter); итого R9 не детектирует и не лечит. Watchdog проставляет last_restart и сохраняет state ДО выполнения restart'ов: первый failure возвращает 1, cooldown остальных действий уже вооружён (латентность лечения 10→40+ мин), host-cron timeout 50s убивает проход посреди цикла; crash-loop>5 и skip-решения не нотифицируются.
* **Evidence:** `converge/runtime.py:318-322` vs канонический `build_compose_args` (volumes.py:193-199 precedent); `runtime.py:55-72` (substring); `watchdog.py:497-499/:669-680/:91`; system.py:411-414; `watchdog.py:90/:372`.
* **Source findings:** BUG-0701 (CRITICAL, live-reproduced), BUG-0702 (HIGH), BUG-0804, FAIL-0403, FAIL-0900 (hermes hang: runbook/timer).
* **Files:** core/internal/bootstrap/converge/runtime.py, core/internal/healthcheck/watchdog.py, (systemd timer решение для converge — отдельное решение).
* **Root cause:** канон-фикс compose-invocation применён к deploy и R7, пропущен в R9; state-commit не транзакционен с действием.
* **Impact:** после ребута/деградации платформа не самолечится; при сломанном R9 watchdog — единственный целитель, и он же задерживает лечение.
* **Recommended change:** R9 → `build_compose_args(module_name, module_dir=...)`; детекция по `label=com.docker.compose.project=<module>`; watchdog: last_restart per-action ПОСЛЕ успешного restart + re-save; TG «crash-loop detected, не рестарчу» в skip-path; решить scheduled converge (systemd timer) — минимально: задокументировать ручной `make converge` в runbook.
* **Tests required:** unit: R9 argv содержит root-first/profile/env-file (fixture на build_compose_args); watchdog: stamp-after-success sequence test.
* **Regression risk:** низкий: R9 сегодня мёртв — любое поведение лучше текущего; watchdog меняет порядок операций.
* **Dependencies:** связка с REF-0010 (watchdog-нотификации).
* **Estimated complexity:** S-M (0.5 дня).
* **Why now:** восстановление после ночного auto-reboot (политика включена!) — регулярный сценарий, не гипотеза.
* **Why not larger refactor:** не переписываем reconcile-движок — 2 точки вызова + порядок операций.

---

## REF-0015 · Resource guards ingress/receive: slowloris кладёт все vhosts, 1 MiB tar распаковывается в сотни GB

* **Problem:** Единственный nginx на все vhosts: worker_connections 1024, ноль limit_conn, ноль client-timeout таймаутов, SSE-vhost'ы держат upstream до 1h → 2–4k медленных соединений исчерпывают слоты = ALL vhosts down, unauthenticated, без precondition. Receive-канал: cap измеряет только сжатые байты (1 GiB буферизуется в RAM), extractall без ceiling на uncompressed size/count, staging в /tmp на одной FS с postgres WAL/docker layers → 1 MiB нулей (~1000:1) = ENOSPC mid-extract = node-wide outage.
* **Evidence:** `nginx.conf:29/:105-106` (только limit_req_zone), client-timeout grep=0, langfuse/grafana vhost read_timeout 3600; `receive_flow.py:99/:172-187/:316-325/:530`.
* **Source findings:** SEC-0045 (HIGH·B11), SEC-0046 (HIGH·B12).
* **Files:** core/modules/nginx/config/nginx.conf, core/internal/template_engine vhost-контракт (таймауты в шаблонах), deploy/receive_flow.py.
* **Root cause:** DoS-измерение никогда не конфигурировалось; cap по compressed-размеру.
* **Impact:** самые дешёвые full-outage векторы (один без аутентификации, один — любым держателем CI-ключа).
* **Recommended change:** limit_conn_zone + limit_conn perip 20; client_header/body_timeout 10s, send_timeout 30s, keepalive_timeout 15s; SSE read_timeout ≤300s; stream-extract с running uncompressed ceiling ~200MB + entry-count cap; default payload cap ↓ до 64MiB; statvfs guard перед extract.
* **Tests required:** unit на stream-extract ceiling (fixture tar-бомба малых размеров); nginx -t structural gate уже существует — дополнить проверкой директив.
* **Regression risk:** средний: лимиты могут резать легитимные большие payloads — выбрать ceiling с запасом ×3 от текущих.
* **Dependencies:** независим.
* **Estimated complexity:** S-M (0.5 дня).
* **Why now:** availability-blockers из BLOCKERS.md; цена конфигурационная.
* **Why not larger refactor:** никакого WAF/CDN — 6 nginx-директив + потолок распаковки.

---

## REF-0016 · Access-surface XS hardening: sshd kbd-interactive может быть включён молча, sudoers принимает произвольные аргументы

* **Problem:** Hardening drop-in не пинит KbdInteractiveAuthentication/ChallengeResponseAuthentication; vendor drop-in сортируется раньше 99-platform-*; применение best-effort (rename fail → WARN), а check-security PASS (парсит sshd -T без этих строк) → «root только по ключу» может быть ложью без сигнала. sudoers-правило node-lifecycle.sh без arg-spec: `--ci-root-key <pubkey>` пишут root-backdoor, `--state-file <path>` — arbitrary root write.
* **Evidence:** `sshd_policy.py:314-329/:87-108/:473-487`; `phases/system.py:498-518`; `setup_node.py:176` (NOPASSWD без аргументов) vs pinned-прецедент `sudoers_generator.py:211`.
* **Source findings:** SEC-0002 (MED·B18, must-fix YES), SEC-0005 (rider MaxAuthTries 3), SEC-0014 (MED-HIGH·B9, must-fix YES).
* **Files:** core/internal/bootstrap/security/sshd_policy.py, phases/system.py, bootstrap/setup_node.py.
* **Impact:** тихая потеря сильнейшего security-invariant; platform-account compromise → reboot-surviving root backdoor одной командой.
* **Recommended change:** +KbdInteractiveAuthentication no +ChallengeResponseAuthentication no (+MaxAuthTries 3) в drop-in и _SSHD_EXTRA_DIRECTIVES; нейтрализовать *cloud* sshd_config.d; apply-failure → blocking; sudoers: pin args (--mode init/update) или root-owned launcher whitelisting флагов, игнорирующий --*-key/--state-file.
* **Tests required:** drop-in content gate (парсинг итогового sshd -T на fixture); sudoers line-format gate (уже есть генератор-прецедент).
* **Regression risk:** низкий (XS-правки), требует пере-provision существующей ноды или ручного apply.
* **Dependencies:** независим.
* **Estimated complexity:** XS (≤2 ч).
* **Why now:** одни из 18 blockers с ценой «несколько строк».
* **Why not larger refactor:** это и есть минимальный фикс; fail2ban/PAM-политики — пост-launch.

---

## REF-0017 · Network placement: SoT говорит hermes-agent-net, рантайм сидит на shared-db-net; PLATFORM_LANGFUSE_URL указывает на мёртвый порт

* **Problem:** litellm/langfuse/minio фактически присоединены к shared-db-net (не hermes-agent-net как в platform-infra.yaml) → каждый backend-tenant контейнер имеет прямой network-доступ к MinIO (backup-bucket с pg_dumpall!), Langfuse, LiteLLM API без auth/TLS/rate-limit; emit'нутый `PLATFORM_LANGFUSE_URL=http://langfuse:3001` целится в host-publish порт — в контейнере слушает 3000 → первый tracing-проект получит connection refused; smoke-тест ходит на phantom hostname nginx-proxy (реальный alias `nginx`) → постоянный false-pass.
* **Evidence:** `platform-infra.yaml:121-145` vs `litellm/docker-compose.base.yml:119-126`, `langfuse/...:122-128`, `minio/...:38-45`; `platform-infra.yaml:129-130` vs langfuse compose:39/:103; practices_manifest.yaml:33-39 (tenant join allowlist); generators.py:361.
* **Source findings:** SEC-0034 (HIGH·B13), AI-0001 (rank#5), AI-0077, AI-0003.
* **Files:** platform-infra.yaml (SoT), langfuse/litellm/minio composes, gen_env_platform pipeline (regen), templates/.env.example.
* **Root cause:** compose-дрейф от объявленного SoT; url_template написан с host-port перспективы.
* **Impact:** канон «изоляция data-plane» не существует; компрометация одного tenant-контейнера даёт сеть до backup/trace-хранилищ без какой-либо эскалации.
* **Recommended change:** ОДНО решение размещения, отражённое везде: минимально-инвазивно — аддитивно добавить hermes-agent-net членство трём сервисам (НЕ убирая существующие сети до согласования зависимостей), синхронизировать platform-infra.yaml; fix URL → :3000; regen platform-env/templates; nginx alias в generated smoke-host.
* **Tests required:** manifest-parity gate: provides.*.networks ⊆ фактических attach (новый маленький gate); smoke_env_generated host-resolve тест.
* **Regression risk:** средний: сетевые изменения на живой ноде — прогнать full stack на test-VPS; аддитивный attach снижает риск.
* **Dependencies:** согласовать с REF-0010 (langfuse-exporter достижимость по сети).
* **Estimated complexity:** M (0.5 дня + реген).
* **Why now:** B13; чинится в основном YAML'ом, пока топология ещё однонодовая.
* **Why not larger refactor:** никакой редизайна сетей/socket-proxy — выравнивание декларации с рантаймом + один порт.

---

### Резюме P0
| REF | Тема | Complexity | Главный закрываемый blocker |
|-----|------|-----------|------------------------------|
| 0001 | Build&push канал проектов | M | B1 |
| 0002 | Postgres provisioning chain | M | B2 |
| 0003 | Unhealthy → FAILED/rollback | S | healthcheck-контракт |
| 0004 | Rollback contour repair | M | страховочная сетка |
| 0005 | Drain status + hc-маркер | S | W5-E1/φ11 |
| 0006 | L1 volumes/channel gate | M | B1sec/B2sec |
| 0007 | Secrets exposure hardening | M | B4sec/B6/B7/B8 |
| 0008 | TLS/cert resilience | M-L | B4f/B5f |
| 0009 | Backup/DR truth chain | M | B15f+B5ops |
| 0010 | Monitoring minimal coverage | M | B3f |
| 0011 | Deploy concurrency integrity | M | Batch B |
| 0012 | CI supply-chain pins | S | B10/B17 |
| 0013 | Secrets-chain fail-fast | M | P0-класс |
| 0014 | Self-heal restoration | S-M | R9/watchdog |
| 0015 | Ingress/receive resource guards | S-M | B11/B12 |
| 0016 | Access-surface XS | XS | B18/B9 |
| 0017 | Network placement truth | M | B13 |
