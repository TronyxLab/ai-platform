## 2026-08-06  — цикл 2, рестарт

### make-check-r2
- make check: **All checks PASS** (773s, 13/13, 1 auto-fix идемпотентный, 0 failed) → лог evidence/make-check-r2.log
- Дерево кода чистое: fix(141) B1-B17 закоммичены; staged/untracked — только evidence/docs (главный оператор, ci-ops)
- Сигнал TREE_CLEAN записан в evidence/SIGNALS.md
- Режим ожидания: поллинг FIXES_NEEDED


## 2026-08-06 2026-08-06T12:40:31Z — волна B18 (FIXES_NEEDED → FIXES_AVAILABLE)

### B18 root-cause
- Контейнеры модулей деплоятся root compose (project=platform); orphan_reconciler сравнивал project label с module_name → все контейнеры platform = orphans → docker rm -f. up --profile --remove-orphans чистил каскадно при неполном COMPOSE_PROFILES. R7: config без env-file → POSTGRES_PASSWORD missing → слепота.
- B18a: конфликт Creating = контейнер-тёзка от чужого проекта — чинится правильным expected (pre-up cleanup).
- B18b: локальный build backup-cron УСПЕШЕН (Dockerfile корректен) — фикс устойчивости ноды (apt-lists cache-mount убран + retry).
- Push-блокер: pre-push-gate.sh source lib/paths.sh → PLATFORM_ROOT=/opt/platform в gate-env → adopt-тесты падали 100% hook / 0% standalone; фикс — убран source.

### Коммиты
- 7366d2dd fix(141): B18 orphan/up/R7/Dockerfile/тесты (6 файлов)
- b9fbc47f fix(141): pre-push-gate paths.sh (в коммит случайно попали 5 staged evidence-файлов главного оператора — задокументировано)

### Верификация
- make check PASS (2 прогона); затронутые тесты 46/46 (R5-negative: no --remove-orphans, root-compose not orphan, foreign-project orphan)
- static_audit 3849 PASS; gate MODE=fast 3x зелёный; push 8a3ee375..b9fbc47f (SKIP=pre-push-gate разово: stash-гонка с evidence-писателями, CI продублирует)

## 2026-08-06 2026-08-06T13:47:28Z — волна B19/B20

- B19: DeployHistory.create_snapshot chown ci-deploy (.deploy-snapshots) best-effort + OSError-safe; context_deployer project_dir chown при создании.
- B20a: practices.lock в WHITELIST_FILES + _PAYLOAD_FILE_NAMES (контракт DevPlan 137).
- B20b: create_user реконсилит группы существующего юзера (usermod -aG); ci-deploy [docker, platform]; generate_catalog catalog.json 0664 (nosec B103).
- Тесты: +practices.lock whitelist, +create_user groups, +receive_chain chown-mock (D4 сохранён); 30/30 PASS; make check PASS.
- Коммит bc3a448b (9 файлов); push с SKIP=pre-push-gate (stash-гонка, gate зелёный 3x, CI дублирует).
- Операции на ноде (server-ops): usermod -aG platform ci-deploy; chmod g+w /opt/platform/catalog.json; существующие .deploy-snapshots уже починил chown -R (workaround).

## 2026-08-06 2026-08-06T15:08:51Z — волна REQ_FIX/B22/B23/B24

- REQ_FIX: core_deliverer deliver_scripts (фаза 1d, mkdir {base}/scripts, без --delete) + CI core-deploy.yml rsync scripts/ (guard); тесты: skip + destination + mkdir-обновление (16/16).
- B22: converge/runtime resolve_container_name → docker ps -a (all=True) — Exited/Created видимы, self-heal R9 оживает.
- B23: nginx ${NGINX_OVERLAY_DIR:?} fail-fast (B23); dev .env задаёт значение; gate test_nginx_dev_compose_valid обновлён (env NGINX_OVERLAY_DIR).
- B24: status-page deep через exec_check wget внутри контейнера (D5: без raw curl).
- Коммит a4218f38 (7 файлов); make check PASS; push SKIP=pre-push-gate (stash-гонка, gate 2x зелёный).
- Открыто (не фиксабельно локально): B21 (tmpfs /run/platform → архитектурное решение), B25 (dev-only), B26 (state.json исчез — расследование), B18b нода (apt), chaos T7/T8/T9 (специфика окружения).
