# 01-Findings — Приёмо-сдаточная валидация платформы (027)

## Ответы владельца (§0, 2026-09-01)
1. Нода: **голая, холодный bootstrap** (tronyx-vps).
2. Freeze: **снят** — чинить до победного.
3. Chaos/reboot: **разрешены** полные дриллы (часы).
4. `context-promote`: **разрешён** (при зелёных B–G).
5. test-VPS: **недоступна** → G5/H1 = BLOCKED (внешняя инфраструктура).
6. DNS/ACME креды (webnames): **доступны**.
7. Контекст/нода: **tronyx-vps**; проекты — из node.yaml (не менять).

## PROGRESS
- [x] A. Локальная верификация — check rc=0 (5832/18skip), agent-check 0, check-manifests PASS, локальный стек up→healthy→down OK
- [x] B. Голая нода → ОДНА команда: bootstrap rc=0, 3 проекта delivered+healthy (oldapp skipped=no_local_source — доставит CI), vhosts 3/3; повторный bootstrap = no-op 66s (skip-health, delivered=0); converge rc=0; healthcheck ноды ALL HEALTHY; check-security 8 PASS + WARN S2 (29 security updates, unattended-upgrades активен); project-list/status OK
- [x] C. TLS: 3 серта восстановлены при bootstrap из S3; cache-drill OK (converge R-ssl самолечит, restore без ACME — dates unchanged); verify-domains 3/3 HTTP 200; мониторинг видит platform_tls_days_left/self_signed (алерты Expiry Warning/Critical + SelfSigned)
- [x] D. Каналы доставки: deploy-context idempotent rc=0; vhosts+monitoring render OK; deploy-project DEPLOYED+аудит; CI-канал E2E GREEN (F-05/F-06/F-07 чейн пофикшен); sync-env rc=0 ×3 (F-08); provision-llm 1 key; rollback-контур через forced-command verb → healthy → re-deploy
- [x] E. Вариации конфигурации + node-update (E1 toggle, E3 overlays, E4 идемпотентность после F-10, E6 сетевая правда)
- [x] F. DR: backup + restore (F1 manual backup S3-verified, F2 nightly 03:00Z PASS, F3 full restore drill PASS)
- [x] G. Resilience (reboot G1 ✓, chaos G2 ✓ после F-11, load G3 ✓, e2e-verify G4 ✓; G5 BLOCKED test-VPS)
- [x] H. Release checklist + промоут (CI main все зелёные после F-12/F-13/F-14; context-promote DONE; пост-deploy e2e-verify 3/3 + ALL MODULES HEALTHY)

Стартовое состояние: ветка main, HEAD dd73c61, `make check` зелёный (5832 pass / 18 skip, журнал 23:32:59).

## Находки

### F-01 · 2026-09-02 · B · P0
- Симптом: холодный bootstrap-node tronyx-vps → exit 10 на φ8 (deploy-context: vhost-render 0/3), 4 проекта остались GENERATED-STUB.
- Ожидалось / получено: bootstrap завершается при живых проектах / strict-guard FAIL, локальная фаза payload delivery (bootstrap.sh:97) не выполнялась (курица-яйцо).
- Гипотеза причины: подтверждена — converge R3 создаёт stub ai-platform.yaml без expose → vhost_renderer._project_expose_enabled трактовал его как expose:false → 0 vhost.
- Фикс (Coder-субагент): vhost_renderer.py — stub-детекция через shared/stub_detection.is_stub_ai_platform_yaml → WARN + return True (node.yaml авторитетен); +3 теста в test_vhost_renderer.py; TRAP[BUG] на месте фикса.
- Ре-верификация: make check rc=0 (5474 pass), make check-diff GREEN, agent-check PASS.
- Статус: fixed
- Evidence: /tmp/b2-bootstrap.log (строки 1148-1186), воспроизведение render-all на ноде, отчёт субагента ses_fa126a28affeWFEuWUlCxq2vkb

### F-02 · 2026-09-02 · C · P1
- Симптом: удалён live-серт (в S3 кеше есть) → make converge → R6 nginx -t FAIL, серт НЕ восстановлен (restore только ручным вызовом ssl_provision_via_orchestrator).
- Гипотеза причины: подтверждена — в конвейере converge не было cert-restore шага до R6.
- Фикс (Coder): новый R-юнит R-ssl (converge/ssl.py→ssl_certs.py) ПЕРЕД R6 — ssl_provision_via_orchestrator, статус-маппинг provisioned→mutated/converged→no-op/error→fail; +7 тестов (test_converge_ssl_certs.py).
- Ре-верификация: cache-drill — rm cert+acme.sh state → make converge rc=0, R-ssl mutated, серт restored from S3 (dates unchanged, ноль ACME-обращений).
- Статус: fixed
- Evidence: /tmp/c2-converge4.log

### F-03 · 2026-09-02 · C · P1
- Симптом: первый cache-drill прогон → S3-download падал «module 'ssl' has no attribute OPENSSL_VERSION» — converge/ssl.py затенял stdlib ssl → restore не работал, оркестратор ушёл в ACME-выпуск.
- Фикс: переименование converge/ssl.py → ssl_certs.py (+импорты reconciler/тестов).
- Ре-верификация: make check rc=0; повторный cache-drill — S3 restore OK без ACME.
- Статус: fixed
- Evidence: /tmp/c2-converge3.log (баг), /tmp/c2-converge4.log (фикс)

### F-04 · 2026-09-02 · A/C · NOTE (fixed)
- Симптом: test_add_vhost_marker_still_ok падал только в полном прогоне — 2 теста без @usefixtures("reset_state"), state-загрязнение infra от соседних файлов.
- Фикс: добавлены маркеры фиксстуры. make check rc=0.
- Статус: fixed
### F-05 · 2026-09-02 · D · P0
- Симптом: CI-канал деплоя всех проектов сломан — шаг Gitleaks scan (L1) падал молча (0 строк вывода, exit 1 за 0.3s).
- Гипотеза причины: подтверждена — upstream gitleaks v8.30.1 переименовал checksums.txt → gitleaks_<ver>_checksums.txt; curl -sL (bash -e) сохранял «Not Found», grep пуст → abort до echo-диагностики.
- Фикс: deploy-project.yml + setup-gitleaks action — версия-префиксный ассет + fallback на legacy имя + curl --fail (явная диагностика).
- Ре-верификация: probe-раны — gitleaks L1 scan passed (v8.30.1), sha256 verified.
- Статус: fixed
- Evidence: /tmp/d5-job.log (баг), run 33587152773 (фикс gitleaks)

### F-06 · 2026-09-02 · D · P0
- Симптом: после gitleaks — SSH preflight «Identity file $RUNNER_TEMP/deploy_key not accessible» (ЛИТЕРАЛ в warning) → Permission denied.
- Гипотеза причины: подтверждена — job-level env SSH_OPTS содержал $RUNNER_TEMP; GitHub env-значения — литеральные строки, bash НЕ делает рекурсивного расширения при `ssh ${SSH_OPTS}` (TRAP[BUG] 2026-08-31 был неверен). Первый реальный CI-ран после биллинг-блока.
- Фикс: SSH_OPTS в step-level env каждого SSH-шага с ${{ runner.temp }} (контекст runner в job-level env недоступен). Секрет CI_DEPLOY_KEY в 3 проектных репо заменён на валидный (base64-форма, setup-ssh декодирует сам).
- Ре-верификация: preflight pong OK (run 33591414425).
- Статус: fixed
- Evidence: /tmp/d5-job3.log, run 33587937440

### F-07 · 2026-09-02 · D · P0
- Симптом: «Invalid or reserved project name: '"dance-site"'» — CI шлёт receive/verify с ручными кавычками, серверный dispatch парсил args наивным split().
- Гипотеза причины: подтверждена — _handle_* в orchestrator_cli.py не снимали кавычки (локальный канал shlex.quote кавычек не добавлял — потому bootstrap/deploy-project работали).
- Фикс (Coder): общий хелпер _parse_tokens (shlex.split + fallback split при unmatched quote) во всех verb-хендлерах + T9.7-блок dispatch; +7 тестов (инъекция-негатив сохранён); channel pin refresh (workflow-sha-pins gate).
- Ре-верификация: run 33592708886 — GREEN end-to-end (build→ghcr→receive→deploy→verify); на ноде dance-site healthy, аудит deploy:deploy DEPLOYED.
- Статус: fixed
- Evidence: /tmp/d5-job6.log (баг), run 33591414425 (до), run 33592708886 (после)
### F-08 · 2026-09-02 · D · P2
- Симптом: make project-sync-env NAME=<n> PROJECT_DIR=<dir> → exit 2 (unrecognized --name у gen_project_platform_md.py).
- Гипотеза причины: фасад scaffold.sh пробрасывает "$@" в оба генератора; md-генератор не принимал --name (канон AGENTS.md документирует NAME=).
- Фикс: gen_project_platform_md.py принимает --name и игнорирует (паритет gen_env_platform.py; имя выводится из project yaml).
- Ре-верификация: rc=0 для tronyx-site/dance-site/botanika; .env.platform + AI-PLATFORM.md обновлены (diff 2 файла).
- Статус: fixed
- Evidence: /tmp/d6a.log (баг), /tmp/d6a2.log (фикс)

### F-09 · 2026-09-02 · E · P1
- Симптом: toggle-дрилл E2 — включение модуля status-page (enabled после bootstrap-off) не давало эффекта: converge R9 репортил «no action needed», контейнера нет, healthcheck FAIL.
- Гипотеза причины: подтверждена — R9 деплоил только «запланированные, но не healthy» модули; absent-контейнер у enabled-модуля не трактовался как дрейф.
- Фикс (Coder): converge/runtime.py — R9 деплоит absent enabled-модули (docker compose up -d) до штатной reconcile-логики; +4 теста (test_converge_runtime.py).
- Ре-верификация: toggle-дрилл повторно — off→skip (модуль disabled), on→R9 задеплоил→healthy; make check rc=0.
- Статус: fixed
- Evidence: /tmp/e2-converge-fix.log

### F-10 · 2026-09-02 · E4 · P2
- Симптом: повторный make node-update NODE=tronyx-vps → фаза converge_update done_with_warnings (rc=1) — идемпотентность update-режима нарушена (E4).
- Гипотеза причины: подтверждена — R-ssl репортил mutated на КАЖДОМ converge: ssl_provision_via_orchestrator возвращал "provisioned" безусловно при любом успехе orchestrate_certs (даже restored=3/issued=0/skipped=0 = ничего не делал).
- Фикс: helpers/domains.py — трёхветочный маппинг по счётчикам CertResult: failed>0 → error (честный warning, resume перевыполнит), issued/restored>0 → provisioned, иначе converged (no-op); R-ssl: converged → no-op без set_exit(1). +4 unit-теста (all-skipped/issued/restored/failed), NEGATIVE R5 на исходный вход.
- Ре-верификация: core-deliver на ноду (F-10 маркер в /opt/platform/core подтверждён) → make node-update rc=0 (0 warnings, 0 errors, audit DONE) → прямой converge на ноде: «R-ssl | converged | All certs converged (no issuance needed)», FULLY CONVERGED exit 0.
- Статус: fixed
- Evidence: /tmp/e4b.log (баг), /tmp/f10-test2_*.log (11/11 PASS), /tmp/f10-update_*.log, /tmp/f10-converge_*.log

### E-фаза примечания
- E1 healthcheck: ALL MODULES HEALTHY.
- E2 toggle-дрилл: off→converge skip; on→R9 деплой→healthy (после F-09).
- E3 overlays: nginx vhost-оверлеи + tor bridges.txt на ноде; tor systemd active.
- E4 node-update идемпотентность: первый прогон → converge_update done_with_warnings (F-10); после фикса rc=0, state converge_update=done.
- E5 converge после node-update: прямой converge на ноде — FULLY CONVERGED exit 0 (R-ssl converged no-op).
- E6 сетевая правда: проекты (tronyx-site/dance-site/botanika) — 0 published-портов, сети = proxy-net + внутренняя; единственная публичная точка — nginx 80/443; платформенные сервисы loopback-only (127.0.0.1); hermes-agent 8642/9119 loopback; oldapp на ноде отсутствует (non-exposed, доставит CI).

### F-11 · 2026-09-02 · G2 · P2 (два тест-фикса чаос-сьюта)
- Симптом 1: test_oom_clickhouse_kernel_kill FAIL — бомба убита ядром (journalctl строки есть, cgroup scope совпадает), но evidence=None.
- Root 1: kernel_oom_pattern строил скоуп из короткого id (`docker-<12hex>\.scope`), а systemd cgroup-driver называет скоуп `docker-<полный 64hex>.scope`; имени clickhouse ядро не знает; docker/<id> — cgroupfs-стиль. Ни одна из 3 kernel-строк не матчилась.
- Фикс 1: + альтернатива docker-<full-id>.scope (TRAP[BUG] в тесте).
- Симптом 2: test_outbound_partition_inbound_alive (night) FAIL — все 3 сайта 000 сразу после revert при живом outbound; транзиент <1 мин, само-heal, изолированная репродукция (инструментированный дрилл с iptables-save/restore + conntrack -F) — 200 на всех шагах.
- Фикс 2: bounded settle-retry (3×/10s) для post-revert sites probe — инвариант проверяется в settled-состоянии, финальный assert остался жёстким.
- Ре-верификация: F7 PASS (76s), N2 PASS (58s); fast subset 9/9, night 3/3.
- Статус: fixed (коммит 244b351)
- Evidence: /tmp/g2-chaos_*.log, /tmp/g2-f7fix_*.log, /tmp/g2-n2fix_*.log, /tmp/n2x-repro.log

### F-12 · 2026-09-02 · H · P2 (CI-инфраструктура: runner disk)
- Симптом: platform-test (run 33604972863) FAIL — ClickHouse error 243 NOT_ENOUGH_SPACE → langfuse exit 1 → R4-каскад. После существующего prune (DevPlan 007 W2) было 14G free — образы стека выросли (hermes L2 и др.), к старту langfuse <1GB.
- Фикс: расширение шага Free disk space — rm host-тулкитов раннера (android ~10G, dotnet, ghc/ghcup, CodeQL, boost, jvm) + apt clean; hostedtoolcache не трогается (нужен setup-python). TRAP[BUG] с Rev.
- Статус: fixed (коммит 34c9028), CI перезапущен.
- Evidence: /tmp/h1-check2 (зелёный make check), gh run 33604972863 (фактура диска: before 61G used / after-cleanup 14G free).

### F-13 · 2026-09-02 · H · P2 (platform-test debt: redis/loki smoke vs T2.0a-контракт)
- Симптом: platform-test красен НЕПРЕРЫВНО с 2026-08-17 (последний зелёный — 32079402225); в окне 027 падали test_loki_ready + 4 redis-теста (NOAUTH ×3 + port-published).
- Root: DevPlan 010 T2.0a (requirepass обязателен + loopback-фасад 127.0.0.1:6379) не сопровождался обновлением smoke-контракта; Loki /ready 503 «schedulers 0» — frontend-worker коннектится к in-process scheduler позже под CI-нагрузкой (container liveness ≠ /ready).
- Фикс: test_smoke_redis.py — _compose_exec оборачивает redis-cli в sh -c с -a "$REDIS_PASSWORD" из container env (тот же канал, что compose-command); port-assert — запрещена только НЕ-loopback публикация. test_smoke_logging.py — poll-цикл /ready 10×3s (for-range, контракт гейта test_gate_http_retry_policy).
- Статус: fixed (коммиты c4d1a9b + F-13b)
- NOTE (системное): platform-test был красен ~2.5 недели ДО 027 — красный CI main не блокировал промоуты (нет branch protection на push-путь). Rev: рассмотреть required-check для platform-test на main.

### F-14 · 2026-09-02 · H · P3 (hermes API smoke — startup reset)
- Симптом: после F-13 следующий слой — test_hermes_api_completions: ConnectionResetError(104) mid-response на первом chat-completions (транзиент прогрева API/LiteLLM-роутинга; вчера этот тест проходил).
- Фикс: _assert_chat_completion — 3 транспорт-попытки с 10s интервалом; контентные ассерты НЕ ретраются; финальный RequestException → _handle_e2e_error (R4 сохранён).
- Ре-верификация: platform-test SUCCESS (первый зелёный с 2026-08-17).
- Статус: fixed

### H-фаза примечания (release checklist + промоут)
- make check: ALL PASS (после F-10 follow-up тест-контрактов); agent-check clean (advisory=4, blocking=0).
- CI main (все workflows): push-gate ✓, platform-gate-fast ✓, platform-test ✓ (F-12/F-13/F-14), security-scan ✓, core-deploy ✓, Mirror ✓.
- Релиз-чеклист п.1 (test-VPS E2E): BLOCKED — документированное отклонение (решение владельца §0.5).
- Релиз-чеклист п.2 (resilience): chaos fast 9/9 + night 3/3 (после bootstrap — выполнено в G).
- Релиз-чеклист п.4: context-promote CONTEXT=tronyx-lab → SUCCESS (аудит tag=context-promote:tronyx-lab DONE); Context CI TronyxLab: core-deploy ✓, platform-gate-fast ✓, security-scan ✓; пост-deploy e2e-verify 3/3 PASS + healthcheck ALL MODULES HEALTHY.
- Релиз-чеклист п.5: AGE_RECIPIENT=SET; 0 BackupUploadFailure.

## ИТОГОВЫЙ ВЕРДИКТ (027)
- Критерий «голая нода → ОДНА команда до всех проектов live»: ВЫПОЛНЕН (B).
- Фазы A-F: PASS (полностью), G: PASS с documented deviation (G5/H1 test-VPS — внешний блокер), H: PASS (промоут выполнен при зелёных B-G; CI main приведён к зелёному впервые с 2026-08-17).
- 14 находок зафиксировано и закрыто: F-01..F-14 (P0 ×4, P1 ×3, P2 ×6, NOTE/P3 ×1), все с ре-верификацией и evidence.
- Остаточные долги: NOTE aws-CLI в backup-cron контейнере (не мешает пайплайну); platform-test required-check — кандидат на rev.
### G-фаза примечания
- G1 reboot: SSH back ~70s; 25/25 контейнеров Up, 0 unhealthy; healthcheck ALL MODULES HEALTHY; tor@default active; verify-domains 3/3 HTTP 200.
- G2 chaos: fast subset 9/9 (после F-11), night 3/3 (после F-11). Длительности: watchdog 199s, OOM CH 160s — в пределах контрактов.
- G3 load smoke: локальный раннер из dev-машины — p100 5000ms + 1 reset (internet-путь dev→нода, NOT node capacity); канонический LOAD_RUNNER=node — PASS: 10.2 rps, p95 25ms, p99 730ms, 0 errors / 900 req. NOTE: локальный раннер требует PATH с .venv/bin (locust — load-extra).
- G4 e2e-verify: 3/3 endpoints HTTP 200 + TLS ok.
- G5: BLOCKED — test-VPS недоступна (решение владельца §0.5); make test-node не выполнялся; компенсации: полный G1-G4 на prod-ноде + release-checklist item 1 = documented deviation.
- Релиз-чеклист п.5 (off-site DR): AGE_RECIPIENT=SET в env backup-cron ноды; BackupUploadFailure в логах контейнера: 0; nightly upload 03:00Z verified (F2).

### F-фаза примечания (DR)
- F1 manual backup: `make -C /opt/platform/core/modules/backup-cron backup` → pg_dumpall→age→S3, UPLOAD VERIFIED sha256=913efe99… (135052 bytes), sentinel+spool cleanup OK.
- F2 nightly: cron в контейнере /etc/cron.d/platform-backup (03:00 postgres, spool-retry 01:30, app-data 03:30, cleanup 04:00, retention 05:00, WAL-sync hourly); прогон 03:00:04Z — UPLOAD COMPLETE, BackupUploadFailure отсутствует; crond активен.
- F3 restore drill (канонический): S3 download (sha256 match с F1) → `make restore DUMP_FILE=…` → pre-restore snapshot OK → clean stop/start → age-decrypt (ключ контентом через env) → restore_psql.sh → post-check «all 3 expected databases present» → post-restore healthcheck ALL MODULES HEALTHY. Артефакты дрилла удалены (shred).
- NOTE (F2): ad-hoc `aws` CLI внутри backup-cron контейнера сломан (distro awscli импортирует botocore из /usr/lib, urllib3 резолвится из pip /usr/local → ImportError DEFAULT_CIPHERS). Пайплайн бэкапов НЕ затронут (скрипты используют pip-boto3 консистентно). S3-операции вне пайплайна (listing/download при DR-диагностике) — через python/boto3.

### D-фаза примечания
- D1 deploy-context: rc=0, deployed=0 skipped=3 (канал ре-верифицирован, идемпотентен).
- D4 deploy-project: DEPLOYED healthy; аудит-след tag=deploy:deploy proc=orchestrator_cli (литерального маркера DEPLOY-DIRECT в коде нет — NOTE).
- D5 CI-канал: полное E2E (F-05/F-06/F-07 чейн) — git push → CI build → ghcr → forced-command receive → deploy → verify GREEN (run 33592708886).
- D7 provision-llm: локально rc=0, 1 key persisted (transient refusal при опущенном стеке обработан честно: exit 1, без дублей).
- D8 rollback-контур: forced-command `rollback botanika` → snapshot-rollback → health healthy → re-deploy через CI восстановил (botanika healthy, аудит OK).
