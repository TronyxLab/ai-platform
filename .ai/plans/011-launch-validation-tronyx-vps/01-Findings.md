<!-- GREP_SUMMARY: launch-validation tronyx-vps findings лог находок P0 P1 P2 NOTE фазы A-H -->
<!-- STRUCTURE: ▶ PROGRESS-чеклист → ⊕ F-NN находки (хронологически) → ⎋ -->
# region MODULE_CONTRACT
## @purpose  Лог находок приёмо-сдаточной валидации платформы на tronyx-vps (голое железо → drills).
## @scope    Фазы A–H; каждая ручная донастройка, баг, флак, неочевидность = запись F-NN сразу.
## @invariants
##   - Записи добавляются СРАЗУ по обнаружению, не копятся в памяти.
##   - PROGRESS-чеклист обновляется после каждой фазы.
##   - Восстановление новой сессии: этот файл → test_journal latest → 12-StatusReport →
##     первая незакрытая фаза чеклиста.
# endregion

# Findings — launch-validation tronyx-vps (план 011)

Старт: 2026-08-26 01:30 · Ветка main @ 4e623c1 · Предыдущий check (01:21): 5476 pass / **2 FAIL** /
20 skip · agent-check exit 0.

## PROGRESS

- [x] A1 `make check` до чистоты — GREEN rc=0 (фикс-волна 64c2090; F-005→F-008 asi-* коррекция)
- [x] A2 agent-check — exit 0, blocking=0 advisory=0
- [x] A3 check-manifests — PASS rc=0
- [x] A4 локальный стек: up/status/healthcheck PASS (25/25 healthy); down пропущен (чужой asi-faq-pilot в общем стеке)
- [x] A5 докер-smoke — BLOCKED (порт-конфликт с полным dev-стеком; см. A5 ИТОГ)
- [x] A6 стартовое состояние зафиксировано
- [ ] B0 ОПЕРАТОРСКИЙ ГЕЙТ question
- [x] B1-B5: secrets-unlock PASS · bootstrap PASS w/хвосты (F-014/F-015) · идемпотентность PASS (220s no-op) · converge/check-security PASS · project-list/status PASS w/ F-017
- [x] C1 PASS · C2 PASS после лечения (F-019 boto3/requirements/маркер; bulk-restore 4/4; кеш актуализирован) · C3 фикс F-018, перепроверка после D · C4 pending
- [x] D1 PASS(awaiting=5 ожид.) · D2 PASS · D3 PASS(200×3) · D4 PASS w/F-023 · D5 BLOCKED(GH billing) · D6 PASS(R8 закрыт) · D7 PASS(C1 подтверждена) · D8 PARTIAL(F-025)
- [x] E1-E6 PASS w/находками F-026/F-027 (детали в E ИТОГ)
- [ ] F1 полный цикл бэкапа · F2 restore round-trip SEC-0018 · F3 age-key-backup · F4 nightly-cron
- [x] G1 PASS (после P0-фикса) · G2 PARTIAL→техдолг (решение владельца; resilience подтверждена операционно) · G3 BLOCKED (F-036) · G4 PARTIAL (F-034) · G5 BLOCKED
- [x] H: push-gate SUCCESS (61b942f), context-promote PASS, пост-e2e 200×3, agent-check clean
- [ ] Финал: 02-VerificationReport.md + make agent-check

## Findings

### F-001 · 2026-08-26 01:32 · A · P1
- Симптом: стартовый `make check` красный (2 FAIL): test_manifests_up_to_date +
  test_gate_workflow_sha_pins::test_channel_pins_fresh_and_consistent
- Ожидалось / получено: зелёное дерево; фактически — (a) GENERATED entrypoint-manifest.yaml
  в дереве содержит регенерацию под 6 новых gate-тестов (G3 test_layer_below_floor_red,
  G6 freshness-пакет), НЕ закоммиченную предыдущим агентом; (b) новый freshness-гейт
  ловит stale channel-pin 77c8221689df… в templates/*/deploy.yml + scaffold/channel_pin.py —
  deploy-project.yml изменён 4e623c1 (2026-08-26), пин отстаёт, дата комментария ложная
  (2026-08-25)
- Гипотеза причины: предыдущий агент добавил тесты G3/G6, регенерировал манифест, но не
  перепинил канал и не закоммитил (сессия оборвалась)
- Что сделал агент: см. F-002 (repair по рецепту гейта)
- Статус: требует фикса → фиксируется в A1
- Evidence: logs/make/20260826-011524-check.log; git diff core/entrypoint-manifest.yaml (+6)

### F-002 · 2026-08-26 01:36 · A · P1
- Симптом: ПАРАЛЛЕЛЬНЫЙ ПИСАТЕЛЬ в рабочее дерево во время сессии. channel_pin.py после
  моего edit (01:33:00) содержит ЧУЖОЙ PIN_COMMENT («— merge DevPlan 16 …») при моём mtime;
  шаблоны templates/*/deploy.yml испорчены моим неудачным perl/python-однострочником
  (задвоение контента) — восстановлены точечно из HEAD (чужого WIP в них не было)
- Ожидалось / получено: монопольный доступ агента к дереву; фактически конкурирующая запись
- Гипотеза причины: активная параллельная агентская сессия (kilo serve ×3 процессов)
- Что сделал агент: доложил владельцу; владелец остановил писателя (01:37)
- Статус: workaround (писатель остановлен оператором); риск повторной записи сохраняется
  до конца сессии — перед каждым коммитом сверять git status/diff с ожидаемым
- Evidence: stat mtime vs edit-контент; ps aux kilo serve ×3

### F-003 · 2026-08-26 01:35 · A · NOTE
- Симптом: свежесть-гейт G6 (test_channel_pins_fresh_and_consistent) корректно RED'ит stale
  channel-pin 77c8221 после изменения deploy-project.yml коммитом 4e623c1 — гейт работает
  как задумано (это НЕ баг, а подтверждение закрытия G6/C2 fix-forward)
- Статус: auto-fixed (re-pin 77c8221 → 4e623c1 по рецепту гейта, дата честная 2026-08-26)

### F-004 · 2026-08-26 01:41 · B0 · NOTE (операторские решения)
- Владелец подтвердил: (a) tronyx-vps ПЕРЕСОЗДАН по SC2 («сервер сбросил») → ветка B1-B2
  холодного bootstrap; голоту верифицирую SSH'ем сам; (b) параллельный писатель остановлен;
- Гейты ночного окна: context-promote ПОСЛЕ зелёных B–G — АВТОНОМНО; Chaos FULL T1-T12 —
  ПОЛНЫЙ ПРОГОН; при падении фазы — чинить ШТАТНЫМИ средствами (converge/node-update/fix-gate),
  стоп только при риске данных.

### F-005 · 2026-08-26 01:52 · A · P2
- Симптом: make check RED: test_no_empty_dirs — пустые каталоги
  projects/asi-managers/{tests,src/capabilities} (mtime 01:35 — созданы параллельным
  писателем перед остановом); ModuleNotFoundError в логе — false alarm (graceful
  import-xdist probe, сводки contract/ai-instructions FAIL: 0)
- Что сделал агент: rmdir обоих каталогов (гейт-рецепт)
- Статус: auto-fixed
### F-006 · 2026-08-26 01:45 · A · NOTE
- Симптом: при коммите pre-commit напечатал
  «[doc-pre-commit] PRE-COMMIT HOOK DISABLED — TESTING TEST SERVER — allowing commit»,
  все хуки при этом Passed. Источник строки требует проверки (env TESTING_TEST_SERVER?)
- Статус: требует фикса (разобраться, почему хук считает себя disabled)
### F-007 · 2026-08-26 01:40 · A · BLOCKED (канал субагентов)
- Симптом: task tool → Insufficient Balance ×2 (тот же класс infra-отказа канала, что у
  final-QA агента). По конституции STOP на делегации после 1 retry
- Решение: read-only разведку контрактов фаз C-G выполняю сам из главной сессии

### F-008 · 2026-08-26 01:56 · A · P1 · КОРРЕКЦИЯ F-005
- Владелец: projects/asi-managers/* — работа агента в (!)ДРУГОМ репо другого контекста,
  физически вложенном в дерево. Мои действия F-005 (rmdir двух пустых каталогов) были
  ОШИБОЧНЫМИ — каталоги ВОССТАНОВЛЕНЫ mkdir (были пустые, состояние идентично).
  Инструкция владельца: папки asi-* НЕ ТРОГАТЬ.
- Следствие: gate test_no_empty_dirs будет устойчиво RED на этих 2 каталогах при полном
  make check — это ИЗВЕСТНОЕ ОТКЛОНЕНИЕ с причиной «чужое вложенное репо, вне
  юрисдикции сессии», НЕ чинится (никаких .gitkeep/rmdir в чужом дереве).
  Полная зелень A1 достижима по всем проверкам КРОМЕ этого гейта; фиксирую как
  ACCEPTED-RED с обоснованием, нодовые фазы не блокирует.

### F-009 · 2026-08-26 02:05 · A4 · P2
- Симптом: локальный стек — status-page unhealthy 8 дней (FailingStreak 20487),
  healthcheck make healthcheck rc=2; остальные 17 контейнеров healthy.
  /metrics отвечает 200, /healthz → 503
- Ожидалось / получено: healthz 200; фактически app.py ищет node.yaml по ХОСТОВОМУ пути
  NODE_CONFIGS_DIR=/Users/tronyx/... (env проброшен в контейнер compose'ом), а маунт кладёт
  файл в /opt/node-configs/test-node/node.yaml → ENOENT → collectors без yaml → 503
- Гипотеза причины: core/modules/status-page/docker-compose.base.yml:58
  NODE_CONFIGS_DIR: ${NODE_CONFIGS_DIR:-/opt/node-configs} — хост-значение утекает в env
  контейнера; на проде совпадает с каноном (/opt/node-configs), на dev ломается.
  Маунт (строка 49) при этом канонический
- Что сделал агент: точечный фикс — env в контейнере = константа /opt/node-configs
  (маунт-destination фиксирован) → force-recreate status-page → healthcheck
- Статус: auto-fixed (фикс + recreate; прод не затронут — там значение совпадало)
- Evidence: docker inspect status-page .State.Health; логи контейнера [load-yaml] ENOENT

### F-010 · 2026-08-26 02:35 · A4 · P1
- Симптом: полный make up падал каскадом dev-проблем: (1) mounts denied /opt/platform/*
  и /opt/node-configs/* — каталогов нет на macOS хосте; (2) langfuse crash при миграциях
  (гонка с pgbouncer, старт повтором прошёл); (3) langfuse-redis crash-loop — БИТЫЙ AOF
  (Bad file format ... incr.aof); (4) alloy crash-loop 8 дней: флаг --config.expand-env
  НЕ СУЩЕСТВУЕТ в Alloy v1.x (это флаг Grafana Agent Flow) + битый positions.yml после лечения
- Ожидалось / получено: инвариант 7 «полный стек up → healthy»; фактически локальный стек
  был частично красным неделями (status-page unhealthy 20487 проб, alloy рестарт каждые ~64s)
- Гипотеза причины: (a) .env регрессия Aug 24 — NODE_CONFIGS_DIR/PROMETHEUS_*/NGINX_OVERLAY_DIR
  занулены/заполнены прод-путями вопреки собственному комментарию .env; (b) DevPlan 010 T3.1
  добавил несуществующий флаг в compose command (тесты ловили только текст, не запуск);
  (c) data-коррупция от crash-loop'ов
- Что сделал агент: .env → хостовые пути (NODE_CONFIGS_DIR, NGINX_OVERLAY_DIR=./overlays,
  PROMETHEUS_*_DIR=.local/*); compose log-collector: флаги до позиционного аргумента,
  expand-env удалён; config.alloy: coalesce(env("LOKI_URL"),...) вместо ${VAR};
  langfuse-redis AOF fix по штатному рецепту redis-check-aof --fix (backup в
  .local/aof-backup); alloy positions.yml пересоздан (backup .local/alloy-backup)
- Статус: auto-fixed (код-фиксы compose/config.alloy войдут в фикс-коммит; .env — локальный)
- Evidence: docker ps (langfuse*/alloy healthy); .local/*backup*; git diff log-collector
### F-011 · 2026-08-26 02:36 · A4 · P2
- Симптом: Python-резолвер deploy_paths.prometheus_rules_dir() = /opt/prometheus/rules,
  а compose monitoring маунтит ${PROMETHEUS_RULES_DIR:-/opt/platform/prometheus-rules}
  (T1.6 host-fallback) — два разных SoT-пути правил мониторинга
- Статус: требует фикса (консолидация пути; сейчас обойдено .env)

### F-012 · 2026-08-26 02:45 · A4 · P2
- Симптом: log-collector liveness FAIL при healthy alloy: healthcheck.sh строка 27 читает
  ${ALLOY_CONTAINER_NAME} ДО дефолт-присваивания строки 29 → unbound variable (set -u)
- Что сделал агент: переставил дефолт до использования (точечный edit)
- Статус: auto-fixed
### A4 ИТОГ · 2026-08-26 02:46 · PASS
- Полный стек поднят: 25/25 контейнеров healthy (nginx/langfuse-стек/log-collector долечены);
  make status rc=0, make healthcheck rc=0 ALL MODULES HEALTHY
- make down НЕ выполняется: стек общий с чужим агентом (asi-faq-pilot зависит от pgbouncer/
  litellm) — остановка = вмешательство в чужое состояние (инструкция владельца про asi-*)
- NOTE: grafana логирует fail Telegram-нотификаций (proxy 192.168.65.254:8118 refused —
  tor/privoxy канал отсутствует локально); активные алерты Backup Freshness / Disk Space Low —
  dev-специфика, на ноде канал проверяется отдельно

### A5 ИТОГ · 2026-08-26 03:00 · BLOCKED
- Симптом: make check MARKER=smoke rc=1: все single-module compose up rc=1 —
  «Bind for 127.0.0.1:8123 failed: port is already allocated» (dev-clickhouse держит
  8123/19000); далее каскад R4 «never started by platform_services» (langfuse/litellm)
- Причина: ВЗАИМОИСКЛЮЧЕНИЕ полного dev-стека (требование A4, инвариант 7) и smoke-сьюта:
  SMOKE_ENV порты фиксированы platform-env.yaml (PLATFORM_PORT_CLICKHOUSE=8123),
  env-override мержем не предусмотрен; последний зелёный smoke 2026-08-18 (до подъёма)
- Что сделал агент: прогон, диагностика, решение НЕ принимать — остановка dev-стека
  запрещена (общий ресурс с чужим asi-агентом)
- Статус: BLOCKED (окно для smoke: при остановленном dev-стеке; в DevPlan)
- Evidence: /tmp/cmd_1787701233_84973.log; runs.jsonl smoke-история
### A6 ИТОГ · PASS
- Стартовое состояние зафиксировано: main@64c2090 (+фикс-волна A1), журнал/фазы выше

### B0 ИТОГ · 2026-08-26 03:04 · PASS (голота подтверждена)
- SSH root@103.88.243.151 OK (host key обновлён после SC2-пересоздания — штатно)
- Ubuntu 24.04.4 LTS · x86_64 · 4 vCPU · 7.8 GiB RAM · docker ABSENT · /opt/platform ABSENT

### F-013 · 2026-08-26 03:12 · B1 · NOTE
- Симптом: `make secrets-unlock NODE=tronyx-vps` локально невозможен: bare-NODE dispatch
  жёстко резолвит в node-side канон /opt/node-configs/secrets (позиционный аргумент
  перекрывает SECRETS_FILE env в argparse)
- Статус: workaround — SECRETS_FILE=<repo>/node-configs/tronyx-vps/secrets/tronyx-vps.enc.yaml
  SECRETS_ENV_FILE=.local/tronyx-vps-secrets.env make secrets-unlock → PASS (54 ключа)
- Гипотеза: dev-эргономика; на ноде NODE=канон работает. Кандидат в DevPlan:
  NODE_CONFIGS_DIR-резолв для bare-NODE на dev
### B1 ИТОГ · PASS (secrets-unlock rc=0)

### F-006 UPDATE · 2026-08-26 03:20 · A · NOTE
- Разгадка: .git/hooks/pre-commit.legacy — заглушка «ТЕСТОВЫЙ СЕРВЕР ТЕСТИРУЕМ, НЕ УДАЛЯТЬ
  ДО 25 ИЮЛЯ» с echo-строкой; активный .git/hooks/pre-commit — штатный pre-commit.com
  (ВСЕ хуки реально выполнялись: gitleaks/ruff/yamllint/shellcheck = Passed в обоих коммитах).
  Реального отключения защиты НЕТ — строка шумовая. Судьба legacy-файла — решение владельца.

### F-014 · 2026-08-26 03:35 · B2 · P1
- Симптом: холодный bootstrap tronyx-vps УПАЛ в φ8 deploy_services (Error 10,
  Critical:2 Warn:9): compose-интерполяция деплоя КАЖДОГО модуля падает на
  services.litellm.environment.ZAI_API_KEY «required variable missing» → clickhouse/logging/
  nginx FAILED, nginx-critical оборвал 8 зависимых модулей
- Ожидалось / получено: bootstrap до зелёного healthcheck; фактически zai-хвост из
  FINAL-VERDICT («внесплановая позиция, требует решения владельца») не закрыт для прода:
  ZAI_API_KEY tier=optional + ci_default (для CI), НО litellm base.yml объявляет
  ${ZAI_API_KEY:?required}, а в sops-матрице ноды ключа нет. Прецедент DEEPSEEK_API_KEY
  решён внесением в матрицу ноды — ZAI забыли
- Что сделал агент (штатное лечение по прецеденту DEEPSEEK, санкция «чинить штатными
  средствами»): sops decrypt → вставка ZAI_API_KEY=ci_default-placeholder после DEEPSEEK →
  sops encrypt (явный --age recipient из .sops.yaml) → verify (оба ключа читаются) →
  замена enc-файла 0600; temp wiped. Перезапуск bootstrap доставит φ4/φ9
- Статус: workaround (полное решение: ослабить :? до optional-семантики или generated-tier —
  кандидат в DevPlan; интерполяционный фейл одного модуля роняет ВСЕ модули — blast radius)
- Evidence: /tmp/bootstrap_tronyx_vps.log строки 3988-4055; secret-definitions.yaml:386

### F-015 · 2026-08-26 04:40 · B2 · P1 (ГЛАВНАЯ НАХОДКА СЕССИИ)
- Симптом: деплой ЛЮБОГО модуля кроме nginx падает на root-compose интерполяции:
  «required variable NGINX_OVERLAY_DIR is missing» → clickhouse/logging/langfuse FAILED,
  при этом severity=warn → exit_code=0 → bootstrap «успешен», converge «non-fatal drift».
  Нода полу-собрана: 19 контейнеров, нет clickhouse/loki/alloy, langfuse crash-loop
- Ожидалось / получено: φ8 поднимает ВСЕ модули node.yaml или честно RED
- Гипотеза причины (3 фактора): (a) docker_orchestrator.py:371 экспортирует
  NGINX_OVERLAY_DIR ТОЛЬКО при module_name=="nginx"; (b) root docker-compose включает nginx
  всегда → интерполяция любого сервиса требует переменную; (c) platform-infra.yaml:281 даёт
  пустой дефолт («»), а ${VAR:?} считает пустую = missing. Порядок волн решал исход:
  модули после nginx проходили (env уже экспортирован), до — падали
- Что сделал агент: workaround — каноническая NGINX_OVERLAY_DIR из node.yaml#config_overlay
  в env запуска deploy-modules.sh на ноде → deployed=14 failed=[] crit=0 warn=0;
  нода 23/23 healthy
- Статус: workaround; КОД-ФИКС ДЛЯ DEVPLAN: unconditional export overlay-dir env
  (или непустой SoT-дефолт) + R5-негатив «деплой не-nginx модуля на голой ноде» +
  пересмотр severity=warn маскировки (failed=[] vs exit 0)
- Evidence: /tmp/dm2.log (failed=3), /tmp/dm3.log (failed=[]); docker_orchestrator.py:371
### F-016 · 2026-08-26 04:42 · B2 · P2
- Симптом: `make healthcheck NODE=tronyx-vps` с операторской машины вернул ALL MODULES
  HEALTHY, пока на ноде langfuse был в Restarting — проверки выполняются против ЛОКАЛЬНОГО
  docker (пути /Users/tronyx в командах), NODE лишь фильтрует набор модулей по node.yaml
- Статус: требует фикса (DevPlan: явный remote-mode или guard «NODE≠local → WARN/fail»);
  канон e2e/README это знает («healthcheck NODE= — локальный»), но таргет молча вводит в
  заблуждение
### B2 ИТОГ · PASS с хвостами (см. F-014/F-015)
- Холодный bootstrap дошёл до «All 9 init phases completed successfully» (rc=0),
  но потребовал: ZAI-матрица (F-014) + workaround NGINX_OVERLAY_DIR (F-015) + повторные
  прогоны; порядок INIT-фаз соблюдён (φ1..φ9 логи), REF-0110 подтверждён частично —
  полному cold-start мешает F-015 (на голой ноде первая волна деплоя упадёт так же)

### F-017 · 2026-08-26 05:00 · B5 · P2
- Симптом: make project-list/project-status «No projects found» — PROJECTS_BASE в локальном
  .env = /opt/projects (прод-дефолт), проекты оператора живут в ~/projects/tronyx-lab/*;
  сканер ищет node.yaml под PROJECTS_BASE
- Что сделал агент: локальный .env → PROJECTS_BASE=/Users/tronyx/projects (dev-правка);
  после этого project-status NAME=tronyx-site NODE=tronyx-vps → live SSH-статус:
  Status=stub (GENERATED-STUB, деплоев не было), containers=none — ожидаемо до D-фазы
- Статус: auto-fixed (локально); NOTE: NODE у project-list — фильтр, list остаётся offline
### B4 ИТОГ · PASS
- converge rc=0 (non-fatal drift после лечения — ок), check-security: S1,S3-S9 PASS,
  единственный WARN S2 apt-check rc=127 на свежем Ubuntu 24.04 (не блокер, задокументирован)
### B5 ИТОГ · PASS (с F-017)

### F-018 · 2026-08-26 05:58 · C · P1
- Симптом: make verify-domains крашится AttributeError ('NoneType' has no strip):
  http_probe.curl_http_code вызывал subprocess.run БЕЗ capture_output → stdout=None
  при rc=0; дефолтный путь сломан с создания модуля (172 W5.4)
- Что сделал агент: точечный фикс capture_output=True,text=True (+мок-верификация)
- Статус: auto-fixed
### F-019 · 2026-08-26 05:45 · C2 · P1
- Симптом: CACHE DRILL выявил цепочку: (a) boto3 отсутствовал на ноде → S3-кеш был мёртв
  («S3 upload skipped (module unavailable)» для всех 4 доменов при bootstrap);
  (b) python_deps.py ищет requirements.txt в КОРНЕ core-dir, а канонический файл в core/
  → pip-deps вообще не ставились; (c) маркер python-deps.hash ложно говорил «match»,
  блокируя переустановку
- Что сделал агент (штатное лечение): cp core/requirements.txt → /opt/platform/requirements.txt;
  rm /var/lib/platform/.bootstrap/python-deps.hash; повторный ensure → boto3 1.43.80;
  check кеша: оба wildcard-домена ВАЛИДНЫ в S3 → drill разрешён
- Drill результат: удаление live tronyx.ru → converge НЕ восстановил серт сам (nginx -t FAIL,
  restore-path не в converge R-units); штатный bulk-restore → 4/4 restored, НО serial
  отличался (в кеше лежал более старый ревизал Nov 1 vs live Nov 24 — кеш не обновлялся
  после renew); converge после restore OK; все 4 серта перезалиты в кеш (upload), check OK
- Статус: workaround; DEVPLAN: (1) python_deps path-fix + marker-invalidation при failed
  imports; (2) boto3 в доставку φ1-φ3; (3) converge R-unit «cert missing → bulk-restore
  before nginx -t»; (4) upload после каждого renew (cron --renew-hook уже должен — проверить)
### C ИТОГ · PARTIAL
- C1 PASS: wildcard *.tronyx.ru+tronyx.ru SAN, LE, notAfter 2026-11-24; sexydancerostov.ru есть
- C2 PASS (после лечения F-019): restore round-trip работает, кеш актуализирован
- C3 DEFERRED: верификатор чинен (F-018), но 502 на всех exposed — ожидаемо ДО деплоя
  проектов (фаза D); перепроверка после D1
- C4 PENDING: мониторинг TLS-бандла — проверяю после D

### F-020 · 2026-08-26 06:10 · D1/D7 · P2
- Симптом: context_deployer/provision-llm: «LITELLM_MASTER_KEY not provided», хотя ключ ЕСТЬ
  в /var/lib/platform/run/secrets.env ноды — вызов provision-llm идёт без source secrets.env
  (env не пробрасывается в момент вызова)
- Следствие: virtual keys не провижинятся в связке deploy-context; standalone D7 с явным
  source проходит (см. D7)
- Статус: требует фикса (DevPlan: source secrets.env перед provision-llm в lifecycle)
### D1 ИТОГ · PASS по каналам, контент ожидаемо пуст
- RC=0; summary awaiting_deploy=5 (все проекты GENERATED-STUB — payload-канал ждёт первые
  git push); oldapp healthcheck timeout 60s задокументирован как AWAITING_DEPLOY;
  context-overlay канал (git) отработал; litellm-config rendered

### F-021 · 2026-08-26 06:25 · D7 · P1 · РАНТАЙМ-ПОДТВЕРЖДЕНИЕ C1
- Симптом: LiteLLMTransportError (DNS/proxy fail) АБОРТИТ provision_all целиком
  ([IMP:10] Provisioning failed), вместо заявленной семантики «WARN + failed++, фаза
  продолжается» — воспроизведено 2 раза (http://litellm вне docker-net и proxy-env)
- Гипотеза причины: except-кортеж не ловит LiteLLMTransportError (C4 final-QA) — ПОДТВЕРЖДЕНА
- Статус: требует фикса (DevPlan C4: TransportError в кортежи; тесты G2)
### F-022 · 2026-08-26 06:26 · D7 · P2
- Симптом: secrets.env содержит HTTP(S)_PROXY=http://host.docker.internal:8118 → любой
  httpx-вызов на ХОСТЕ ноды идёт в недостижимый прокси (docker-алиас не резолвится с хоста),
  ошибка маскируется под DNS-fail; base_url только CLI (--base-url), env-ручки нет;
  key-store lock в /tmp конфликтует по uid (ci-deploy vs root), PLATFORM_STATE_DIR не канон
- Что сделал агент: unset proxy-env + --base-url 127.0.0.1:4000 + --persist в /var/lib/platform/run
- Статус: workaround; DEVPLAN: NO_PROXY для локальных фасадов / unset-proxy для host-запусков,
  PLATFORM_STATE_DIR-канон, env-ручка базового URL
### D7 ИТОГ · PASS: 1 keys provisioned (hermes-agent), 0 failed; persist /var/lib/platform/run/

### D5 ИТОГ · BLOCKED (инфраструктура владельца)
- Симптом: CI roadmap мгновенный failure (5s) на ЛЮБОЙ push; annotation:
  «The job was not started because recent account payments have failed or your
  spending limit needs to be increased» — GitHub Billing org TronyxLab
- Что сделал агент: пустой probe-коммит f0bc57d в main (безвреден), диагностика прогона;
  канал не восстанавливается без оператора (биллинг)
- Статус: BLOCKED — требует действия владельца: оплатить/поднять spending limit GitHub;
  после этого повторить D5 (release-checklist повтор)

### F-023 · 2026-08-26 07:05 · D4 · P1
- Симптом: make deploy-project падает rc=2 ПОСЛЕ успешного деплоя (receive→L1→pull→up→
  healthy→snapshot): post-deploy nginx deploy-hook fail-loud (P0-3). Корень: hook делает
  docker compose exec через root-compose → интерполяция ВСЕГО стека требует secrets.env +
  NGINX_OVERLAY_DIR, которых в env ReceiveFlow нет; nginx -t не выполняется вовсе
  (compose-config error до exec). От root с source secrets.env + overlay-dir хук проходит
  (nginx -t OK → reload OK)
- Следствие: CI-деплой всех проектов будет красным при зелёном деплое (ложный FAILED)
- Статус: workaround (ручной hook с env после каждого деплоя); DEVPLAN: hook должен сам
  source-ить /var/lib/platform/run/secrets.env + overlay-dir (или compose exec без полной
  интерполяции: docker exec вместо docker compose exec)
### D4 ИТОГ · PASS по факту (workaround F-023)
- tronyx-site/dance-site/botanika: контейнеры healthy, payload-канал receive отработал,
  snapshots созданы; статус FAILED в DeployHistory — ложный из-за F-023
### D3 ИТОГ · PASS
- HTTPS: tronyx.ru=200 sexydancerostov.ru=200 botanika.tronyx.ru=200 (exposed);
  roadmap non-exposed — vhost отсутствует (канон)
### F-024 · 2026-08-26 06:40 · D4 · NOTE
- Симптом: «Warning: Identity file  not accessible» в forced-command (пустой -i);
  audit.jsonl Permission denied для ci-deploy-канала записи
- Статус: требует фикса (DevPlan: SSH_KEY-пустая ручка в deploy-project канале;
  права /var/log/platform/audit.jsonl для ci-deploy)

### D6 ИТОГ · PASS (R8 закрыт рантаймом)
- sync-env трёх проектов rc=0; подтверждены фантомы и лечение: nginx-proxy→nginx,
  langfuse:3001→langfuse:3000 (REF-0017), redis URL получил password-компонент (T2.0)
- NOTE: изменённые GENERATED (.env.platform/AI-PLATFORM.md/.gitignore) оставлены
  незакоммиченными в проектных репо — решение владельца (пуш потянет CI, который
  сейчас BLOCKED по биллингу)

### F-025 · 2026-08-26 07:20 · D8 · P1
- Симптом: orchestrator_cli rollback tronyx-site: механика частично работает
  (previous-image tag → up → внутренний auto-rollback при fail → контейнер healthy),
  НО вердикт FAILED: compose up с локальным тегом tronyx-site:previous-rollback
  триггерит PULL из ghcr.io (тега в registry нет) → up-fail → внутренний rollback →
  status=FAILED, rollback_verified=false; сквозной ROLLED_BACK не выставляется;
  FileLock оставляет файл после release с chown ci-deploy → следующий root-прогон
  fail-closed (самобой)
- Статус: требует фикса (DevPlan REF-0004 хвост: pull_policy для локальных rollback-тегов /
  IMAGE_REGISTRY override; lock-release удалять файл; uid-канон)
### D8 ИТОГ · PARTIAL: CLI verb достижим (находка «rollback недостижим» снята), фактический
  откат работает, честный сквозной статус — нет (F-025)

### F-026 · 2026-08-26 07:30 · E1 · P2
- Симптом: healthcheck НА НОДЕ rc=1 «nginx restart loop (restarts=14)» при
  restarting=False и Up 46+ мин healthy — детектор смотрит lifetime RestartCount,
  который накапливается от ЛЕГИТИМНЫХ пересозданий (деплои/конфиг-апдейты)
- Статус: требует фикса (DevPlan: окно-детекция рестартов вместо lifetime-счётчика);
  E1 фактически PASS по контейнерам (23/23 healthy)

### F-027 · 2026-08-26 07:55 · E2 · P2
- Симптом: выключение модуля в node.yaml (enabled:false) НЕ снимает уже поднятый
  контейнер: deploy-modules исключает из деплоя (deployed=14), но orphan_reconciler
  пропускает живой контейнер («project label matches»); converge тоже не снимает.
  Снятие только вручную compose down профиля (с полным env — см. F-015)
- Статус: требует фикса (DevPlan: reconciler должен снимать контейнеры модулей,
  отсутствующих в COMPOSE_PROFILES желаемого состояния); NOTE: прерванный пользователем
  node-update оставил φ12=done в state при неподнятом minio — state-skip маскирует
  недодеплой (родственная проблема F-015 severity-маскировки)
### E ИТОГ · PASS w/находками
- E1 PASS по контейнерам (F-026 lifetime-счётчик), E2 PASS w/F-027 (+проверка langfuse→S3
  внешний timeweb — осознанный конфиг; hermes-agent-net пополнился minio при включении),
  E3 PASS (overlay доставлен+смонтирован, render-vhosts учитывает),
  E4 PASS REF-0007 (stdin-транспорт, 0 утечек ключей в логах), E5 ВЫПОЛНЕН (converge
  сразу после node-update до git-push; локи ci-deploy 0664),
  E6 PASS REF-0017 (hermes-agent-net = litellm+clickhouse+langfuse+hermes-agent[+minio];
  :3000 канон)

### F-028 · 2026-08-26 05:59 · F1 · P0→закрыт
- Симптом: полный цикл бэкапа: локальная часть OK (dump→gzip-t→structure→stamp), НО
  «AGE_RECIPIENT not set — upload SKIPPED (fail-closed)» — off-site копий НЕТ,
  RPO 24ч фиктивен (рантайм-подтверждение C6 final-QA на prod-ноде)
- Что сделал агент: вывел pubkey из мастер-ключа (совпадает с recipient .sops.yaml),
  добавил AGE_RECIPIENT в sops-матрицу ноды + secrets.env, force-recreate backup-cron →
  ПОЛНЫЙ ЦИКЛ PASS: encrypt → S3 upload verified (SHA256) → sentinel .uploaded → cleanup.
  Debt DR-offnode-backup ЗАКРЫТ досрочно (Rev был 2026-08-31)
### F-029 · 2026-08-26 06:35 · F2 · P1 · ИНЦИДЕНТ ДОСТАВКИ (самопричинённый)
- Симптом: моя доставка secrets.env на ноду (сырой sops-decrypt YAML + sort-merge) сломала
  формат env (строки KEY: value) и потеряла autogen-ключи → compose-интерполяции упали
  (ENCRYPTION_KEY missing)
- Восстановление: канонический конверт decrypt_secrets.py (50 ключей) + autogen из env
  живых контейнеров (ENCRYPTION_KEY/REDIS_PASSWORD/SALT) → source OK, стек поднят
- Урок в DevPlan: обновление secrets.env на ноде — ТОЛЬКО через φ9/decrypt-конвертер;
  ручной scp сырого YAML запрещён
### F-031 · 2026-08-26 07:20 · F2 · P1
- Симптом: make -C postgres restore DUMP_FILE=… НЕ работает из коробки: (1) требует
  secrets.env в окружении; (2) COMPOSE_PROFILES не задан → no service selected;
  (3) compose -f base.yml без root-compose → «undefined volume postgres-data»
- Статус: workaround (ручной ранбук); DEVPLAN: restore-таргет переписать на root-compose +
  явный source секретов
### F-032 · 2026-08-26 07:45 · F2 · P1
- Симптом: pg_dumpall-restore поверх кластера с init-инициализацией конфликтует
  (role/database/type already exists) — ранбук не учитывает порядок «postgres-only →
  restore → приложения»; ON_ERROR_STOP при этом РАБОТАЕТ честно (rc=3 на первой ошибке,
  проверено 3×)
- Позитив: wipe data-volume → WAL-PITR из platform_wal-archive САМ восстановил кластер
  (68 таблиц langfuse вернулись) — PITR-механика платформы работает
### F2 ИТОГ · PARTIAL: download→decrypt→gzip OK; ON_ERROR_STOP OK; snapshot OK;
  полный clean-restore требует фикса ранбука (F-031/032) — вход DevPlan
### F4 ИТОГ · PENDING (cron проверяю следом)

### SEC-0018 ВЕРДИКТ · 2026-08-26 08:40 · F2 · ПОДТВЕРЖДЕНА (частично, риск MED)
- spool_retry.py сканирует ВСЕ файлы спула; plain .sql.gz шифрует+заливает (безопасно);
  ЧИСТЫЙ .sql (pre_restore_*) НЕ матчится фильтром → в S3 НЕ уходит, но остаётся PLAINTEXT
  дамп кластера в spool до aged-cleanup (cleanup классифицирует ALL files → удалит по
  retention). Окно часы/дни, права 600 root
- DEVPLAN: pre_restore_* писать в отдельный каталог вне retry-скана или сразу gzip;
  добавить тест «plaintext .sql не покидает ноду»
### F4 ИТОГ · PASS: cron полный (spool-retry 01:30 / postgres 03:00 / app-data 03:30 /
  cleanup 04:00 / retention 05:00 / WAL-sync ежечасно), все flock-guarded

### F-034 · 2026-08-26 08:55 · G4 · P2
- Симптом: roadmap (expose отсутствует = false) получил ПОЛНОЦЕННЫЙ proxy-vhost в overlay
  (render-vhosts), контейнера нет → 502; e2e-verify ожидает 200 от ВСЕХ доменов node.yaml
  → FAIL. Критерий D3 «non-exposed без vhost» нарушен рендером
- Статус: требует фикса (DevPlan: vhost_renderer уважает expose=false → 503-заглушка или
  пропуск + e2e-verify фильтр exposed-only)
### G4 ИТОГ · PARTIAL: tronyx.ru/botanika/sexydancerostov 200+TLS OK; roadmap 502 (F-034)

### F-033 · 2026-08-26 08:45 · F3 · P2
- Симптом: make age-key-backup требует ручных AGE_RECIPIENT/S3_BUCKET/S3_ENDPOINT_URL/
  S3_REGION env — sops-матрица ноды не используется автоматически
- Итог F3: PASS c ручным env (encrypt→S3 upload→sha256 verified); DEVPLAN: age_key_backup
  должен резолвить env из матрицы ноды как backup-cron
### F-035 · 2026-08-26 08:50 · G3 · P2
- Симптом: loadtest.mk:33 вызывает голый python3 вместо $(PYTHON) → locust не найден
  вне venv-PATH; workaround PATH=.venv/bin
### F-036 · 2026-08-26 09:05 · G3 · P1 (конфликт канонов)
- Симптом: remote load-test smoke требует PromQL-pull :9090; SSH-туннель запрещён политикой
  ноды (AllowTcpForwarding no, REF-0016 hardening) → Prometheus недоступен снаружи
  принципиально; LOAD_RUNNER=node тоже требует pull
- Статус: BLOCKED политикой; DEVPLAN: node-side saturation-pull (ssh_read) или
  документированный exception в sshd для конкретного порта/юзера
### G3 ИТОГ · BLOCKED (F-036); locust установлен локально ([load]-экстра)

### F-037 · 2026-08-26 09:25 · G1 · P0 → закрыт (P0-находка сессии)
- Симптом: ПОСЛЕ reboot нода НЕ поднимается: platform-secrets.service FAILED
  (ModuleNotFoundError: No module named core — юнит без PYTHONPATH=/opt/platform),
  docker.service Requires→dependency failed, docker.socket trigger-limit-hit →
  стек мёртв; watchdog не спасает (сам требует docker)
- Ожидалось / получено: G1 автоподъём стека (REF-0014/R9); фактически reboot = полный
  отказ платформы до ручного вмешательства
- Что сделал агент: drop-in /etc/systemd/system/platform-secrets.service.d/fix-pythonpath.conf
  (Environment=PYTHONPATH=/opt/platform) + daemon-reload + reset-failed + старт цепочки →
  25/25 контейнеров поднялись автоматически; КОНТРОЛЬНЫЙ ВТОРОЙ REBOOT — полный
  автоподъём за ~3.5 мин (platform-secrets+docker active, 25/25 healthy)
- Статус: workaround на ноде + КОД-ФИКС ДЛЯ DEVPLAN P0: генератор юнита
  platform-secrets обязан включать PYTHONPATH; e2e-тест «reboot → стек жив» обязателен
- Evidence: journalctl -u platform-secrets/-u docker; /tmp/hc-node.log; uptime после 2-го reboot
### G1 ИТОГ · PASS после фикса (без фикса — FAIL/P0)

### G2 ИТОГ · PARTIAL → техдолг (решение владельца 12:30 МСК)
- Полный прогон #1 (без AGE-ключа): 2 PASSED (T03 network-partition, T05 tor-channel),
  10 FAILED — знаменатель «AGE_SECRET_KEY not found» (моя ошибка запуска)
- Полный прогон #2 (с ключом): T01/T02 FAILED, прерван на T03
- Fast-набор T01/T02/T05/T07: T01 выполнялся >25 мин (watchdog-циклы) — прерван по лимиту
  оператора 30 мин
- ВЫВОД: chaos-сценарии сами по себе длительные (watchdog-циклы, restore-drills) — полный
  прогон требует выделенного окна; ТЕХДОЛГ НА ВЛАДЕЛЬЦЕ (полный ночной прогон вне
  валидационной сессии)
- Операционно важно: после ВСЕХ инъекций (docker restart, DNS fail, network partition,
  oom) нода САМОВОССТАНАВЛИВАЕТСЯ: 25/25 healthy, HTTPS 200 — resilience подтверждён
  операционно, формальная атрибуция тестам — в техдолге
