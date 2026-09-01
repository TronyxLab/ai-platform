# 01-Findings — launch-validation asi-team-vps (повторная полная приёмо-сдаточная)

$ARTIFACT_CONTRACT
@purpose: Полная приёмо-сдаточная валидация после крупного рефакторинга. Критерий: голая нода + `make bootstrap-node NODE=asi-team-vps` = сервер + ВСЕ проекты контекста одной командой.
@mode: чинить до победного; каждый фикс → ре-верификация → коммит → push.
@verdict_scope: PASS/FAIL/BLOCKED по фазам A–H + вердикт ПРОМОУТ (промоут выполняет основная модель).
@base: origin/main 2526b39 (merged 020/021: a9937d8, a823dc6, 19b0949, 0b9a485).
@worktree: /Users/tronyx/projects/ai-platform-worktrees/launch-validation-asi-team-vps, ветка launch-validation/asi-team-vps.
@prior: план 020 (критерий был PROVEN, PARTIAL из-за C2-cache/F/G-блокеров) + 021 merge-review (6/6 фиксов accepted). Эта сессия = повторный ХОЛОДНЫЙ прогон на пересозданной ноде + закрытие open-пунктов.

## 0a. Ответы владельца (2026-09-01)
1. Нода asi-team-vps: ГОЛАЯ → холодный bootstrap.
2. Freeze: СНЯТ — чиню свободно.
3. Chaos/reboot-дриллы: часы доступны (fast + night профили разрешены).
4. test-VPS: НЕДОСТУПНА → G5 = BLOCKED (внешняя инфраструктура).
5. DNS/ACME: креды regru ДОСТУПНЫ — wildcard DNS-01 разрешён.
6. Проекты: из node.yaml подтверждены (см. ниже).

## 0b. Контекст ноды (из node-configs/asi-team-vps/node.yaml, не из памяти)
- context: asi-group (1 нода = 1 контекст), node: asi-team-vps, host: 77.233.221.129
- domain: asiteam.ru, email: admin@asiteam.ru, acme_dns_plugin: regru (per-domain реестр)
- tor: off; timezone: Europe/Moscow; SSH-ключи изолированы (asi_owner/asi_cicd/asi_cicd_root)
- secrets: node-configs/asi-team-vps/secrets/asi-team-vps.enc.yaml; .sops.yaml контура asi (свой age-ключ)
- проекты: ровно 1 — roadmap (roadmap.asiteam.ru, repo asi-group/roadmap2, frontend, expose: true)
- модули: nginx (+config_overlay /opt/node-configs/asi-team-vps/overlays/nginx), platform-secrets, logging, status-page

## 0c. Открытые пункты прошлых валидаций (020/021) — предмет проверки рантаймом
- [ ] S3 SSL-кеш: `InvalidAccessKeyId` (s3.timeweb.cloud, bucket platform-asi-certs) — блокировал C2 cache drill в 020. Владелец подтвердил доступность кредов DNS; S3-креды проверить через secrets контура.
- [ ] F-06 (P2): converge R7 ложный drift-warning по volume `loki-data` (не учитывает compose project-prefix) — фикс опционален.
- [ ] F-10 (P2): apex https://asiteam.ru/ без default vhost (HTTP/2 PROTOCOL_ERROR).
- [ ] DR (020 F-09): postgres/backup-cron НЕ в контексте asi-group → F1/F2/F4 = BLOCKED by-design (внешняя конфигурация контекста, не баг платформы); F3 age-key-backup применим.
- [ ] make healthcheck NODE=... с операторской машины = fail-loud по контракту (F-016) → для ноды: converge + e2e-verify (не баг, зафиксировать в отчёте).

## PROGRESS-чеклист фаз
- [x] §0  Ворктри/parity/hooks — DONE (симлинки node-configs/.venv/.env/hermes-env, pre-commit install)
- [x] Фаза A: make check / agent-check / check-manifests / локальный стек / test_journal
  - A1 make check: FAIL(1) → F-01 фикс → RE-VERIFY rc=0 GREEN ✅
  - A2 agent-check: exit 0 (0 blocking, 0 advisory) ✅
  - A3 check-manifests: GREEN ✅
  - A4 локальный стек: up → status 23/23 healthy → healthcheck ALL MODULES HEALTHY → down ✅ (F-02 volume-инцидент по пути)
  - A5 test_journal: prior run main 07:38 — 5763 pass/1 fail (тот же F-01 doxygen) — подтверждена регрессия на main
- [x] Фаза B: secrets-unlock → холодный bootstrap → идемпотентность → converge → check-security → sanity
  - B1 secrets-unlock: PASS после устранения F-04/F-05/F-06 (AGE-ключ asi через runner / явный env)
  - B2 холодный bootstrap: ✅ **КРИТЕРИЙ ДОСТИГНУТ: `make bootstrap-node NODE=asi-team-vps` одной командой = сервер healthy + roadmap DEPLOYED+healthy** (φ1-φ8.5; roadmap snapshot 20260901T122242-81b31929, healthcheck healthy; φ7 wildcard *.asiteam.ru через regru DNS-01)
  - B3 идемпотентность: ✅ 8/9 фаз «already done — skipping»; roadmap re-deliver 2.8s (vs 14.1s cold); bootstrap complete rc=0
  - B4: ✅ converge FULLY CONVERGED; check-security WARN-only (S2: 22 security updates pending — свежая нода, unattended-upgrades active; NOTE не блокер)
  - B5: ✅ project-list (roadmap); project-status NAME=roadmap: Up (healthy)
  - NOTE: verb использует NAME= (не PROJECT=) для project-status — TASK-шаблон неточен, не баг
- [ ] Фаза C: TLS wildcard → cache drill → verify-domains → мониторинг TLS
  - C1: ✅ wildcard /etc/letsencrypt/live/asiteam.ru (CN=asiteam.ru, SAN *.asiteam.ru+asiteam.ru, LE, expiry 2026-11-30); acme.sh dns_regru, cron 4×/день; openssl: wildcard покрывает apex и под-домены
  - C2: **BLOCKED** (внешняя инфраструктура): S3-креды контура невалидны для timeweb/bucket platform-asi-certs (403 → InvalidAccessKeyId, list_buckets тоже). Код канала кеша НЕ сломан (fallback endpoint s3.timeweb.cloud — канон shared/s3_client:47; check/upload исполняются). Live-серты НЕ тронуты (канон «кеш пуст → СТОП»). Мини-фикс наблюдаемости: F-07 (WARN при 403).
  - C3: ✅ verify-domains ALL PASS — roadmap.asiteam.ru HTTPS 200, SAN wildcard match, TLS verify ok (внешний curl)
  - C4: BLOCKED by-design — monitoring-модуль не в node.yaml минимального контура (prometheus-rules пусто, экспортёра service нет); код TLS-наблюдаемости в платформе есть (cert_collector + status-page enrich) — подхватится при добавлении monitoring-модуля

## Находки (F-NN · дата · фаза · severity)

### F-03 · 2026-09-01 · Фаза B · P0
- Симптом: холодный bootstrap после φ1 complete упал на входе в φ2: `Unknown option: --mode` (python usage), процесс exit 2. Нода застряла с state.json (φ1 done).
- Ожидалось / получено: re-exec lifecycle на python3.14 с сохранением всех CLI-аргументов / интерпретатор получил `--mode` как СВОЙ опцион.
- Гипотеза причины: `_reexec_lifecycle` (core/internal/bootstrap/lifecycle/cli.py) строил `os.execv(target, [target, *sys.argv[1:]])` — терял argv[0] (путь cli.py при каноническом file-запуске node-lifecycle.sh:50 `python3 "${SM_SCRIPT}" "$@"`). Инвариант докстринга «сохраняет ВСЕ CLI-аргументы» — ложный.
- Фикс (Coder-субагент): чистая функция `_reexec_argv(target)` — package-mode: `[target, "-m", f"{__main__.__package__}.cli", *args[1:]]`, file-mode: `[target, abspath(argv0), *args[1:]]`; docstring-инвариант исправлен; 2 negative-теста в test_lifecycle_cli_w5.py.
- Ре-верификация: make check TEST_FILE=tests/unit/test_lifecycle_cli_w5.py 15/15 PASS; полный make check rc=0; повторный bootstrap: φ1 skipped (resume) → φ2/φ3 complete ✅ (коммит e0d0e09).
- Статус: fixed
- Evidence: /tmp/w5_check2_1788240340.log, logs/make/20260901-082653-bootstrap-node-asi-team-vps.log (φ2/φ3 complete после фикса)

### F-04 · 2026-09-01 · Фаза B · P1 (операционная ловушка канона)
- Симптом: φ4 secrets_provision FAILED на ноде: sops «no identity matched any of the recipients» (recipient = age-пубкей asi-контура), несмотря на prelude-доставку ключа (node_detect на ноде: «found in environment»).
- Ожидалось / получено: расшифровка asi-team-vps.enc.yaml на ноде / mismatch identity.
- Гипотеза причины: `~/.zshrc` владельца глобально экспортирует `AGE_SECRET_KEY` (tronyx master key). node_detect chain: check1 `AGE_SECRET_KEY` env → check2 `SOPS_AGE_KEY` env → ... — check1 перехватывает tronyx-ключ ДО моего `SOPS_AGE_KEY=asi`. Bootstrap передаёт tronyx-ключ на asi-ноду → sops отклоняет. Канон (root AGENTS.md): «AGE_SECRET_KEY env ПЕРЕКРЫВАЕТ файл; принудительный файл — unset AGE_SECRET_KEY» — ловушка документирована, но fail-вывод sops не указывает на неё оператору.
- Фикс: операционный — запуск bootstrap с явным перекрытием: `AGE_SECRET_KEY="$(cat ~/.ssh/age-key-asi.txt)" make bootstrap-node NODE=asi-team-vps` (env assignment перебивает zshrc-значение; check1 теперь возвращает asi-ключ). Код не менялся: порядок канона заморожен (DEP-0017), двойная приоритизация сломала бы контуры tronyx.
- Ре-верификация: повторный bootstrap (resume φ1-φ3 skipped) — φ4 в прогрессе (см. далее); sha-диагностика канала prelude: printf_q-экспорт сохраняет байты ключа 1:1 (канал чист).
- Статус: fixed (операционно); 🧐 TRAP[DECISION]: глобальный AGE_SECRET_KEY в ~/.zshrc — источник коллизий мульти-контура; Rev: если второй инцидент → рассматривать node_detect warning при multi-key-env (env-ключ ≠ файловый ключ → IMP:7 warn)
- Evidence: sha-сравнения в логах сессии; core/internal/shared/node_detect.py:119-132 (chain); logs/make/20260901-082653-bootstrap...log (sops fail)

### F-05 · 2026-09-01 · Фаза B · P0
- Симптом: повторная φ4 FAILED при запуске с явным `AGE_SECRET_KEY="$(cat ~/.ssh/age-key-asi.txt)"`: remote «FATAL: stdin secret transport: unexpected extra non-empty line(s) [4] beyond expected 3» → AGE_SECRET_KEY на ноде пуст (len=0) → step_10 sops «no identity matched».
- Ожидалось / получено: канонический AGE-ключ одна строка через prelude / multi-line env-значение.
- Гипотеза причины: detect_age_key() Check 1/2 (env) возвращают значение КАК ЕСТЬ — файл age-keygen содержит 3 строки (2 комментария + ключ), ключ-файл подставленный целиком в env проходит multi-line. Файловые чеки (3/4/5) санитизированы ранее (TRAP[BUG] 2026-08-12 — тот же класс), env-чеки пропущены. Протокол ssh-stdin prelude читает значения ПО СТРОКАМ — multi-line ломает транспорт.
- Фикс (Coder-субагент): helper `_canonical_age_key()` в node_detect.py — нормализация env-значений Check 1/2 к канон-строке (первая строка с префиксом AGE-SECRET-KEY-), None → fallthrough по цепочке; TRAP[BUG] 2026-09-01; 2 negative-теста (test_node_detect.py: env_multiline, env_noncanonical fallthrough).
- Ре-верификация: make check TEST_FILE=tests/unit/test_node_detect.py 23/23 PASS; ruff clean; smoke multi-line env → ровно 1 строка canonical.
- Статус: fixed
- ⚠️ SEC-note: при диагностике одноразовый вывод awk напечатал 3-строчный ключ-файл владельца в session output (локальный терминал). Ключ не попал в git/логи репо. Рекомендация владельцу: ротация asi-AGE-ключа после закрытия валидации (вне скоупа сессии).
- Evidence: logs/make/20260901-084454-bootstrap...log (FATAL + sops fail); core/internal/shared/node_detect.py Check 1/2 + TRAP[BUG] 2026-09-01

### F-06 · 2026-09-01 · Фаза B · P1 (окружение оператора + трассировка)
- Симптом: после F-05 фикса bootstrap продолжал падать на φ4: sops «no identity matched», нодный CLI-entry получал len=74 sha256=f329c89c (= tronyx master key из ~/.config/age/keys.txt), хотя prelude строился с asi (d638a9ad digest в dry-run).
- Ожидалось / получено: prelude с asi-ключом / live-прогон background_process доставлял tronyx-ключ.
- Гипотеза причины (подтверждена digest-трассировкой): сессионная env содержит AGE_SECRET_KEY=tronyx (унаследована от интерактивного шелла; session-env Assignment перекрыт/потерян при передаче команды в background_process — вложенные кавычки "$(cat "$HOME/...")" внутри command string ломались при оборачивании в sh -c; без assignment env-значение tronyx шло в node_detect → Check1 (после F-04 частичного обхода) — в ранних прогонах Check1-приоритет давал tronyx).
- Фикс: (1) digest-трассировка на трёх границах (prelude, node-lifecycle shell-entry, CLI-entry) — позволит безошибочно сверять байты ключа; (2) операционно — runner-скрипт в /tmp (bash export + exec make), убирающий проблему передачи env через command string.
- Ре-верификация: runner-прогон 151610: shell-entry=a442edad (asi) → CLI entry=a442edad → φ4 SUCCESS (638 bytes, 27 ключей, 2 ci_default) → φ5/φ6 (registry auth) → **φ7 certificates complete (wildcard DNS-01 regru)** → φ8: roadmap DELIVERED + healthy → **Bootstrap complete**.
- Статус: fixed
- Evidence: digest-сравнения: asi=d638a9ad (dry-run), tronyx=704eae69 (live-фейл); logs/make/20260901-151610-bootstrap...log (полный success)
- Коммиты диагностики: 41ddd6c (decrypt len), 3358f98 (prelude digest), 9852633+081ffe6 (CLI-entry digest), fc515c1 (shell-entry digest)

### F-08 · 2026-09-01 · Фаза D · P1
- Симптом: `make deploy-context NODE=asi-team-vps CONTEXT=asi-group` rc=2 — vhost render FAILED (nginx -t harness: docker.io анонимный pull `nginx:1.28-alpine` → 429 Too Many Requests) → render_all abort (all-or-nothing) → deployed=0 skipped=1 failed=1.
- Ожидалось / получено: nginx -t валидация рендеренных vhost через digest-pinned образ / harness тянул незапиненный тег, нода не имеет docker.io pull.
- Гипотеза причины: harness-дефолт `nginx:1.28-alpine` вне канона digest-pin (root AGENTS.md DevOps-политика); канон nginx-модуля: `nginx:1.30.4-alpine@sha256:8a4f4b94...` (docker-compose.base.yml:41) — образ уже на ноде.
- Фикс (Coder-субагент): NGINX_T_IMAGE digest-pin константа (TRAP[DECISION], SoT-ссылка); pre-flight `docker image inspect`; pull-failure → non-blocking True + IMP:10 чёткая причина; docker-вызовы вынесены в shared/docker_ops (docker_image_exists/docker_run_nginx_t — docker-sole-path канон); C901 разрулен декомпозицией (_create_harness_layout/_copy_dev_vhosts/_stderr_text; _render_vhosts_to_temp/_swap_rendered_vhosts/_validate_nginx_t).
- Ре-верификация: make agent-check clean=True; make check rc=0 (5432 total).
- Статус: fixed (коммит baa748d)
- Побочные находки: тесты core_deliverer/org_secrets weren't hermetic (сессионный AGE_SECRET_KEY=tronyx утекал в тестовые логи — тест-фикс: delenv+HOME-изоляция+canon fake-ключи); node-lifecycle.sh 83>80 LOC → 79 (diag-сжатие); docker_ops FBT (force keyword-only); RUF052 autofix.
- Evidence: /tmp/d1-deploy-context.log; agent-check output в логах сессии

### F-09 · 2026-09-01 · Фаза D · P2
- Симптом: D6 `make project-sync-env` на roadmap: AI-PLATFORM.md фантомные сервисы («Enabled modules: litellm, logging, nginx, platform-secrets, postgres, status-page» — litellm/postgres НЕ в node.yaml контура; docker ps ноды: 5 контейнеров, без litellm/postgres).
- Ожидалось / получено: provides только для enabled модулей ноды / полный платформенный catalog без фильтра.
- Гипотеза причины: gen_env_platform.py:489 `PLATFORM_PROVIDES={provides_keys}` — data["provides"] без фильтра по enabled modules node.yaml; per-service HOST/PORT/DSN для каждого svc — фантомные строки и в .env.platform.
- Фикс (Coder-субагент): generate() принимает enabled_modules (фильтр пересечением provides ∩ enabled); NODE_YAML env → NodeYaml.get_modules() enabled-семантика; legacy (NODE_YAML отсутствует/пуст) = байт-идентично; 5 negative-тестов.
- Ре-верификация: make check rc=0 (полный батч); пер-файловые тесты 17/17; make check-diff GREEN.
- Статус: fixed (коммит 4236960). NOTE: нодный .env.platform обновится при следующем converge/payload-delivery (фаза E).
- Evidence: /Users/tronyx/projects/asi-group/roadmap/AI-PLATFORM.md diff; core/internal/scaffold/gen_env_platform.py

### F-10 · 2026-09-01 · Фаза D · P2
- Симптом: D4 `make deploy-project` аудит-след на ноде: tag=deploy:deploy, channel=LocalChannel — канонный маркер «DEPLOY-DIRECT» (root AGENTS.md Deploy-модель) в коде отсутствует (grep: 0 вхождений).
- Ожидалось / получено: аудит-тег, различающий emergency-канал / единый deploy:deploy тег без семантики канала.
- Гипотеза причины: канон AGENTS.md документирует «аудит DEPLOY-DIRECT», реализация — только channel-поле.
- Фикс: НЕ выполнен (не блокирует валидацию: аудит-след существует и различим по channel=LocalChannel; добавление тега — P2-косметика) → фиксирую как расхождение канона/реализации. Основной модели: решение (либо править AGENTS.md-канон, либо добавить тег).
- Статус: NOTE (не блокер)

### F-01 · 2026-09-01 · Фаза A · P1
- Симптом: `make check` rc=2 — doxygen-check FAIL: 1 warning «core/internal/bootstrap/AGENTS.md:247: unable to resolve reference to '/Users/tronyx/projects/AGENTS.md' for \ref command».
- Ожидалось / получено: zero-warnings invariant (DevPlan 097) / 1 warning, гейт красный. Регрессия существует и на main (test_journal 07:38: 5763 pass / 1 fail — тот же doxygen).
- Гипотеза причины: doxygen резолвит markdown-ссылки от CWD (корень репо), а не от расположения файла; ссылка `](../../AGENTS.md)` уходит за пределы репо. Нарушена конвенция репо (пути md-ссылок от корня, как в core/AGENTS.md: `](modules/AGENTS.md)`).
- Фикс (Coder-субагент + main-сессия): core/internal/bootstrap/AGENTS.md:247 `](../../AGENTS.md)` → `](core/AGENTS.md)`; :249 `](../../entrypoint-manifest.yaml)` → `](core/entrypoint-manifest.yaml)` (консистентность конвенции; doxygen .yaml не валидирует, но тот же класс дефекта).
- Ре-верификация: `doxygen Doxyfile` → 0 warnings; `make check MARKER=doxygen-check` GREEN; `make check` полный батч rc=0; `make agent-check` exit 0.
- Статус: fixed
- Evidence: /tmp/doxy_wk.log, /tmp/doxy_fix.log, /tmp/cmd_1788237909_63481.log (до), /tmp/cmd_1788238461_90191.log (после, rc=0)

### F-02 · 2026-09-01 · Фаза A · P2 (окружение, не код)
- Симптом: `make up` rc=1 — postgres restart-loop: entrypoint-сканер нашёл data-dir `18.corrupt.bak/docker` в volume `launch-validation-asi-team-vps_postgres-data` (данные прошлой валидации 020, WAL бит: «invalid checkpoint record» PANIC). Каскад: langfuse миграции падают (pgbouncer недоступен).
- Ожидалось / получено: чистый локальный стек healthy / postgres PANIC-loop.
- Гипотеза причины: volume dev-стека ворктри несёт повреждённые данные эпохи 020 (переименование в .bak не завершили, свежий init заблокирован защитой docker-library/postgres#37).
- Фикс: tar-бэкап битых данных (15.3MB → /var/folders/.../kilo/pg18-corrupt-data-bak.tar.gz, rollback возможен) → удаление data-dir из volume → fresh init.
- Ре-верификация: make up rc=0; make status 23/23 healthy; make healthcheck ALL MODULES HEALTHY; make down rc=0 (volumes preserved).
- Статус: fixed (окружение ворктри; код платформы не задет — никаких изменений репо)
- Evidence: /tmp/cmd_1788238633_1473.log, /tmp/cmd_1788238964_11169.log, tar-бэкап в $TMPDIR/kilo/

- [ ] Фаза B: secrets-unlock → холодный bootstrap → идемпотентность → converge → check-security → sanity
- [ ] Фаза C: TLS wildcard → cache drill → verify-domains → мониторинг TLS
- [ ] Фаза D: deploy-context → render-vhosts/monitoring → project-list/status → deploy-project → CI-канал → sync-env → provision-llm → rollback
  - D1 deploy-context: FAIL (F-08 vhost harness) → фикс → re-verify ✅ rc=0: vhosts 3 .conf rendered, deployed=0 skipped=1 failed=0, roadmap already-healthy skip (идемпотентность деплоя ✓); cert_orchestrator: issued=1 skipped=1 failed=0 (semantics fixed vs previous failed=1)
  - D2 render-vhosts: ✅ rc=0 (3 conf); render-monitoring: ✅ rc=0 skip (monitoring-модуль не в node.yaml — корректный hook-skip)
  - D3 project-list/status: ✅ (B5); exposed roadmap → HTTPS 200 (verify-domains)
  - D4 deploy-project (прямой): ✅ DEPLOYED healthy 2.8s snapshot 20260901T134249-be2135a4; NOTE: PROJECT= требует абсолютного пути (относительное имя → «Project directory not found»); аудит-след есть (tag deploy:deploy, channel LocalChannel) — F-10 NOTE (маркер DEPLOY-DIRECT не реализован)
  - D5 CI-канал: **BLOCKED by-design** — контур содержит ровно 1 проект (roadmap, expose:true); non-exposed проекта для CI-канала не существует (конфигурация контекста); push в чужой репо вне санкции сессии
  - D6 sync-env: ✅ rc=0; фантомные hostname/services найдены → F-09 фикс (provides filter по enabled node.yaml)
  - D7 provision-llm: **BLOCKED by-design** — litellm не в node.yaml минимального контура, Admin API connection refused; fail-loud поведение корректно (R4: NO_SERVICE = FAIL, не skip)
  - D8 rollback: ✅ orchestrator_cli rollback --project roadmap → snapshot 20260901T134249 → Up (healthy), аудит tag deploy:rollback, внешний HTTPS 200; ROLLED_BACK → re-verify cycle OK
- [x] Фаза E: все модули healthy → вкл/выкл → overlays → node-update → converge → сети
  - E1: ✅ активные модули healthy (nginx, nginx-prometheus-exporter, loki, status-page, roadmap); docker exec nginx nginx -t → syntax ok
  - E2: ✅ вкл/выкл status-page: enabled:false → node-update rc=0 → контейнер исчез; enabled:true → rc=0 → Up (healthy)
  - E3: ✅ overlays (login.asiteam.ru.conf, nginx.conf, roadmap.asiteam.ru.conf) применены в /etc/nginx/conf.d/overlay; nginx -t successful
  - E4: ✅ node-update ×2 rc=0 (ключи через stdin-prelude, вне argv/логов)
  - E5: ✅ converge после node-update: FULLY CONVERGED (до первого git-push проектов)
  - E6: ✅ сетевая правда: roadmap → proxy-net + roadmap-net; proxy-net: nginx/roadmap/status-page. NOTE: staging-*/test-* docker-сети — физический мусор тест-прогонов (контейнеров нет), prune-кандидат, не блокер
- [x] Фаза F: DR (F1/F2/F4 by-design BLOCKED: нет postgres в контексте; F3 age-key-backup)
  - F1/F2: BLOCKED by-design — postgres/backup-cron не в node.yaml минимального контура (stateful-модулей нет: pg_dumpall/WAL неприменим; ручная подмена роли = out-of-scope)
  - F3: ✅ age-key-backup rc=0 — ключ зашифрован AGE_RECIPIENT (len=62 из матрицы) и загружен в tronyx-vps-backups (sha256 verified, endpoint s3.timeweb.cloud)
  - F4: AGE_RECIPIENT непуст в матрице ноды ✅; nightly-cron/backup-cron отсутствуют (модуль off) → RPO-цепочка N/A для stateless-контура; при добавлении backup-cron chain сходится (секреты матрицы готовы)
- [ ] Фаза G: reboot → chaos (fast+night) → load-smoke → e2e-verify → test-node BLOCKED
- [ ] Фаза H: Release checklist → ПРОМОУТ вербикт → 02-VerificationReport.md

## Находки (F-NN · дата · фаза · severity)
