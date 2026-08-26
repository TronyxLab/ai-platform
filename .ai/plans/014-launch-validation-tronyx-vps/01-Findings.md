<!-- GREP_SUMMARY: findings launch-validation tronyx-vps фазы A-H находки P0 P1 P2 NOTE леджер сессии -->
<!-- STRUCTURE: ▶ PROGRESS-чеклист → ⚡ находки F-01.. по фазам → ⎋ итог для DevPlan -->

# 01-Findings — launch-validation tronyx-vps (план 014, повторная валидация после фиксов 012/013/017)

Дата старта: 2026-08-26 21:08 МСК · Нода: tronyx-vps (103.88.243.151) · Контекст: tronyx-lab
Стартовое дерево: `main` @ `6fff904` (clean). Повторная приёмо-сдаточная валидация после
предыдущего прогона 011 (F-001..F-037) и фикс-планов 012 (fast-bootstrap-deploy),
013 (resilience-drills-rework), 017 (ai-code-fixes).

## PROGRESS-чеклист (обновляется после каждой фазы)

- [x] Фаза A — локальная верификация (check/agent-check/check-manifests/стек) — PASS с F-01/F-02/F-03
- [x] Фаза B — bootstrap/идемпотентность/converge/check-security/sanity — PASS (B5 PARTIAL F-11)
- [ ] Фаза C — TLS + cache drill + verify-domains + мониторинг — BLOCKED (F-08 S3-кеш)
- [ ] Фаза D — deploy-context + 3 канала + provision-llm + rollback — NOT RUN (D5 GH Billing)
- [ ] Фаза E — вариации модулей + node-update + критический порядок — NOT RUN
- [ ] Фаза F — бэкап/restore/age-key/nightly — NOT RUN
- [ ] Фаза G — reboot/chaos/load-smoke/e2e-verify/test-node — NOT RUN (G5 test-VPS)
- [ ] Фаза H — Release checklist + сверка открытых пунктов QA — NOT RUN
- [x] 02-VerificationReport.md + черновик DevPlan — DONE (коммит fde3fe8)

---

## Фаза A — локальная верификация

### F-01 · 2026-08-26 21:20 · A1 · P1
- Симптом: `make check` RED (exit 2, 3 failed checks). Первый отказ — schema-валидация
  node-configs: «Additional properties are not allowed ('postgres_init_databases' was
  unexpected)» для node-configs/tronyx-vps/node.yaml:46 и node-configs/asi-team-vps/node.yaml:45.
- Ожидалось / получено: зелёный `make check` на чистом дереве main@6fff904; фактически
  schema-drift между node.schema.json (свойство удалено планом 17) и node-configs (записи
  остались).
- Гипотеза причины: план 17 (ai-code-fixes) AC «снять свойства из схем + записи из
  node-configs (обе ноды)» выполнен частично — свойство снято из node.schema.json и
  accessor удалён (node_yaml/__init__.py:18), но записи в двух node.yaml НЕ удалены.
  Поле мёртво: 0 потребителей в core/ и tests/ (grep подтверждает), исторический accessor
  get_postgres_init_databases удалён волной 118 B3.
- Что сделал агент: проверил отсутствие потребителей (rg по core//tests/ = только
  комментарий об удалении) → тривиальный фикс: удалить dead-поле из обоих node.yaml.
- Статус: auto-fixed (см. F-01-fix)
- Evidence: /tmp/check_a1_1787767810.log; git log node.schema.json (09b4081 feat(17))

### F-02 · 2026-08-26 21:20 · A1 · NOTE (infra-flake, задокументирован)
- Симптом: `make check` RED — core/entrypoints/pyright-hook.sh FAIL (exit 124, Timeout
  after 120s). Полный pyright-прогон репо >120s при xdist CPU-конкуренции.
- Ожидалось / получено: pyright PASS; фактически таймаут на dev-машине под параллельной
  нагрузкой (check запущен с WORKERS=6 + фоновые процессы сессии).
- Гипотеза причины: известный infra-флак, задокументирован TRAP[DEBT] в core/check-suite.yaml
  (:132-143) + TRAP[DECISION] «keep 120s» (c7af0bc).
- Что сделал агент: повторный прогон pyright в изоляции (см. F-02-verify).
- Статус: requires verification (флак vs регрессия)
- Evidence: /tmp/check_a1_1787767810.log (pyright-hook.sh exit 124)

### F-03 · 2026-08-26 21:20 · A1 · P2 (perf-threshold)
- Симптом: `make check` RED — tests/unit/test_secrets_env_parser_benchmark.py::
  test_parse_benchmark_1000_vars FAIL: «parse() benchmark: 80.48 ms exceeds development
  threshold (50.0 ms). Performance regression in secrets_env_parser detected.»
- Ожидалось / получено: parse 1000 vars ≤50ms; фактически 80.48ms.
- Гипотеза причины: (a) реальная perf-регрессия secrets_env_parser; (b) флак под xdist
  CPU-конкуренцией (бенчмарк чувствителен к загрузке). Требует изолированного повторного
  прогона для классификации.
- Что сделал агент: изолированный повтор (см. F-03-verify).
- Статус: requires verification (флак vs регрессия)
- Evidence: /tmp/check_a1_1787767810.log (static_audit 1 FAIL)

### F-04 · 2026-08-26 21:28 · B0-preflight · NOTE (host key changed)
- Симптом: SSH root@103.88.243.151 → «REMOTE HOST IDENTIFICATION HAS CHANGED».
  Новый ED25519 fingerprint SHA256:Dlx4uj1h62v7AVq/vc7vxRBHsd2tgoiwngmwFbb6Fxo;
  в known_hosts:31 лежит ECDSA-ключ от предыдущего состояния (после SC2 в сессии 011).
- Ожидалось / получено: стабильный host key от забутстрапленной ноды; фактически ключ
  сменился → нода, вероятно, пересоздана повторно (SC2) для этого раунда валидации.
- Гипотеза причины: (a) оператор пересоздал VPS (окно «голое железо»); (b) MITM.
  Разрешение — B0-гейт у владельца.
- Что сделал агент: НЕ принял новый ключ, НЕ удалил старый (безопасность) → B0 question.
- Статус: requires operator confirmation
- Evidence: ssh stdout; known_hosts:31 (ECDSA, старый)

### F-02-verify · 2026-08-26 21:28 · A1 · NOTE
- Изолированный pyright-hook.sh (full-repo basedpyright, без xdist) — >200s, всё ещё
  выполняется. Подтверждает: НЕ только xdist-конкуренция — full-repo скан сам по себе
  >120s timeout check-suite. Регрессии кода нет (изменённые файлы pyright-clean по TRAP),
  но 120s timeout теперь систематически мал для full-repo скоупа.
- Статус: requires verification (финальный rc pyright — по завершении фонового прогона)
- Evidence: /tmp/pyright_iso.log (пуст на момент записи — процесс активен, 4 basedpyright)

### F-03-verify · 2026-08-26 21:24 · A1 · NOTE
- Изолированный прогон test_secrets_env_parser_benchmark (no:xdist): 2 passed, benchmark
  call 0.02s (порог 50ms) → флак под xdist CPU-конкуренцией, НЕ perf-регрессия.
- Статус: closed (flake confirmed)
- Evidence: pytest tests/unit/test_secrets_env_parser_benchmark.py 2 passed 0.17s

### A-фаза ИТОГ · 2026-08-26 21:28 · PASS с оговорками
- A1 `make check`: 3 отказа → F-01 (FIXED: dead postgres_init_databases убран из 2 node.yaml),
  F-02 (pyright full-repo timeout — инфра-флак/лимит, документирован), F-03 (benchmark —
  xdist-флак, закрыт изоляцией). Дерево чистое; F-01 фикс не закоммичен (по Commit Policy
  — решу в конце сессии).
- A2 agent-check: PASS (blocking=0 advisory=0).
- A3 check-manifests: GREEN (all generated up to date).
- A4 локальный стек: REUSED (up 19h, 26 контейнеров); make status rc=0; make healthcheck
  rc=0 ALL MODULES HEALTHY. `make down` пропущен (общий стек с чужим asi-faq-pilot +
  capjobs-redis-test — прецедент 011, инструкция владельца asi-* не трогать).
- A5 стартовое состояние: main@6fff904 clean; journal latest = check exit 1 (предыдущий агент).

### B0 ИТОГ · 2026-08-26 21:31 · PASS (операторский гейт)
- Владелец подтвердил: (a) нода tronyx-vps ПЕРЕСОЗДАНА (SC2, голая) → маршрут холодного
  bootstrap B1-B2; (b) смена SSH host key легитимна (новый ED25519 Dlx4uj1h62v7AVq…).
- Действия: принять новый host key, верифицировать «голоту» (docker //opt/platform отсутствуют).

### F-05 · 2026-08-26 21:40 · B1 · P0 (блокирует bootstrap)
- Симптом: `make secrets-unlock NODE=tronyx-vps` exit 10 — D3 fail-loud:
  «required/generated keys missing from decrypted matrix: REDIS_PASSWORD, ENCRYPTION_KEY,
  VPS_HOST, VPS_SSH_KEY, CI_DEPLOY_KEY, SSH_HOST, SSH_KEY, E2E_GRAFANA_URL, NODE_HOST_MAP,
  LITELLM_PROJECT_KEYS — refusing to write partial secrets.env».
- Ожидалось / получено: decrypt 58 ключей матрицы + запись secrets.env; фактически FatalError
  на 10 ключах, которых В МАТРИЦЕ И НЕ ДОЛЖНО БЫТЬ.
- Гипотеза причины (подтверждена чтением кода): регрессия плана 012 T3 —
  `apply_ci_default_injection` (core/internal/secrets/decrypt_secrets.py:637) fail-loud'ит на
  `tier in (required, generated)` БЕЗ фильтра по `source`. Все 10 отсутствующих ключей —
  НЕ sops: 7 × source=ci-secret (GitHub Secrets, в матрице ноды не живут), 2 × source=autogen
  (REDIS_PASSWORD/ENCRYPTION_KEY — генерируются в рантайме openssl), 1 × source=provisioner
  (LITELLM_PROJECT_KEYS — провижинится в рантайме). Собственный postcondition модуля
  `verify_required_sops_secrets` уже использует верную семантику {required ∧ source=sops} —
  D3 ей противоречит. Тест test_missing_required_fails_loud кодирует ошибочную семантику
  (MISSING_GEN: generated/source=autogen ожидается в fail-loud).
- Блокирует: B1 (локальный secrets-unlock) И B2 φ4 (helpers_secrets.decrypt_secrets →
  lib/secrets.sh step_10 → decrypt_secrets.py main() → apply_ci_default_injection).
- Что сделал агент: минимальный фикс — фильтр source=sops в fail-loud ветке (1 строка) +
  коррекция теста (негатив: non-sops НЕ fail-loud). См. F-05-fix.
- Статус: auto-fixed (см. F-05-fix)
- Evidence: secrets-unlock stderr; decrypt_secrets.py:637; helpers/secrets.py:62-88;
  secret-definitions.yaml (source-сводка: 18 sops / 22 ci-secret / 11 autogen / 1 provisioner)

### F-05-fix · 2026-08-26 21:42 · B1 · auto-fixed
- Код: decrypt_secrets.py — `elif tier in _FAIL_LOUD_TIERS:` → `elif tier in _FAIL_LOUD_TIERS and definition.get("source") == "sops":`
- Тест: test_missing_required_fails_loud — MISSING_GEN(autogen)/MISSING_CI(ci-secret) теперь
  НЕ в списке ошибки (негатив source-фильтра), sops required остаются fail-loud.
- Верификация: make check TEST_FILE=tests/unit/test_decrypt_secrets.py (+связанные) → secrets-unlock.

### F-06 · 2026-08-26 21:48 · A1 · P2 (процессный утечка)
- Симптом: полный basedpyright НЕ завершается: >15 мин wall (мой прогон), при этом в системе
  висит УТЁКШИЙ basedpyright PID 23783 с накопленным CPU 209 мин (≈3.5 ч) — орфан от
  предыдущего `make check`, чей pyright-шаг был убит по timeout=120s, но дочерний процесс
  остался жить.
- Ожидалось / получено: timeout check-suite убивает дерево процессов pyright; фактически
  дочерний basedpyright орфанится и продолжает жечь CPU часами.
- Гипотеза причины: check-suite executor убивает ТОЛЬКО шелл/родителя pyright-hook.sh по
  таймауту, не process-group; nodejs-wheel/basedpyright выживает. Накопленный орфан создаёт
  CPU-конкуренцию → флаки тестов (F-03 benchmark 80ms под xdist), деградация всей машины.
- Что сделал агент: остановил свой фоновый pyright; утёкший орфан задокументирован
  (kill — решение владельца, процесс не мой).
- Статус: requires fix (DevPlan: kill process-group по timeout / changed-files scope для
  gate-pyright / reaper орфанов)
- Evidence: ps aux (basedpyright PID 23783 TIME 209:44, PID 85901 28:03); /tmp/pyright_iso.log пуст

### F-02-verify ИТОГ · 2026-08-26 21:48 · A1 · NOTE
- Подтверждено: pyright full-repo на dev-машине НЕ укладывается в 120s (в изоляции >15 мин
  из-за накопленного CPU-орфана F-06). Регрессии кода нет; известный инфра-флак + утечка
  процесса (F-06). make check на этой машине систематически красный по pyright-шагу.
- Статус: accepted-RED (не блокирует нодовые фазы; задокументирован TRAP[DEBT] в check-suite)

### B1 ИТОГ · 2026-08-26 21:46 · PASS (после F-05-fix)
- `make secrets-unlock NODE=tronyx-vps` rc=0: 58 ключей дешифровано, 1 ci_default auto-injected
  (GHCR_PUSH_TOKEN — optional+ci_default ci-secret, test-значение; на ноде не потребляется).
- NOTE: инъекция GHCR_PUSH_TOKEN (source=ci-secret) — смежный семантический хвост F-05:
  injection-ветка тоже без source-фильтра; безвредно (ключ CI, не потребляется на ноде),
  но кандидат в DevPlan (source=sops фильтр для injection симметрично fail-loud).

### F-07 · 2026-08-26 22:13 · B2 · P1 (блокирует φ8)
- Симптом: bootstrap φ8 deploy_services FAIL (exit 10): «Interpolation dry-run failed for
  modules: postgres, clickhouse, langfuse, monitoring, logging, backup-cron, hermes-agent».
  Деталь dry-run: «service postgres refers to undefined volume postgres-data: invalid compose
  project» (и clickhouse-data/langfuse-redis-data/prometheus-config-gen/loki-data/backup-spool/
  hermes-data — все stateful-модули).
- Ожидалось / получено: φ8 поднимает все 15 модулей; фактически dry-run (plan 012 T10) ложно
  RED'ит все stateful-модули ДО создания контейнеров.
- Гипотеза причины (подтверждена чтением кода): `_interpolation_dryrun`
  (deploy_orchestrator.py:402) строит `docker compose -f <module>/base.yml config --quiet` —
  ИЗОЛИРОВАННЫЙ модульный файл. Но named volumes (postgres-data и пр.) объявлены в ROOT
  docker-compose.yml (единственный SoT, 13 имён), модульные base.yml их только ССЫЛАЮТ.
  Изолированный -f даёт «undefined volume». Это ровно тот баг-класс, что задокументирован
  TRAP[BUG] compose_args.py:98 («изолированный модульный -f: undefined volume backup-spool»),
  но dry-run T10 переоткрыл его ручной сборкой cmd в обход канонического build_compose_args.
- Что сделал агент: фикс — dry-run использует build_compose_args (root-compose-first + env-file
  + overlay + profile), см. F-07-fix.
- Статус: auto-fixed (см. F-07-fix)
- Evidence: /tmp/bootstrap_b2.log:1417-1429; compose_args.py:98-109 (TRAP[BUG]);
  docker-compose.yml:53-61 (root volumes SoT)

### F-07-fix · 2026-08-26 22:15 · B2 · auto-fixed
- deploy_orchestrator.py: импорт build_compose_args из compose_args; в _interpolation_dryrun
  ручная сборка `-f <module>` заменена на `*build_compose_args(compose_file, secrets_env, None,
  overlays.get(name), name)` → root-compose-first (U-49 канон). На ноде резолвит
  /opt/platform/docker-compose.yml (volumes SoT), на dev — fallback на module base.yml.
- Верификация: make check TEST_FILE=tests/unit/test_deploy_orchestrator_preflight.py → 2 passed.

### B2 ИТОГ · 2026-08-26 22:30 · PASS (после F-05-fix + F-07-fix)
- Холодный bootstrap rc=0 «All 9 init phases completed successfully», 22 контейнера на ноде
  (21 healthy + status-page unhealthy — см. F-09).
- Порядок фаз соблюдён: φ1 system_bootstrap → φ2 user_accounts → φ3 platform_setup →
  φ4 secrets_provision → φ5 node_configuration → φ6 registry_auth → φ7 certificates →
  φ8 deploy_services → φ8.5 converge. Идемпотентность: повторный прогон φ1-φ7 = «already
  done — skipping» (state.json resume работает).
- F-05 (D3 fail-loud) и F-07 (dry-run isolated -f) блокировали холодный bootstrap; оба
  исправлены, bootstrap прошёл. NOTE: в логе остались dry-run «undefined volume» от
  Python 3.12 (cpython-312.pyc 22:01, системный /usr/bin/python3 до установки 3.14) —
  финальный деплой прошёл через Python 3.14 (cpython-314.pyc 22:19:45) с фиксом.
  build_compose_args на ноде возвращает root-compose-first (проверено напрямую).
- deploy_context: deployed=0 (5 проектов awaiting CI payload — ожидаемо на голой ноде).

### F-08 · 2026-08-26 22:30 · B2 · P1 (S3-кеш сертификатов мёртв)
- Симптом: cert_orchestrator «s3_ssl_cache module not loaded — S3 restore unavailable»
  для ВСЕХ 4 доменов; tronyx.ru issue_cert failed (exit=1) → self-signed fallback
  REFUSED (BUG-0606 guard: «valid LE certificate exists» от φ7 первого прогона).
- Ожидалось / получено: restore-first из S3-кеша (F-019 из 011 должен быть закрыт планом
  012); фактически S3-кеш НЕДОСТУПЕН (boto3 не загружен) — рантайм-повтор F-019.
- Гипотеза: F-019 (boto3 не доставлен / python_deps path) НЕ закрыт планом 012 для свежей
  ноды, либо s3_ssl_cache импортируется только при наличии boto3. КРИТИЧНО для C2 (cache
  drill невозможен без S3-кеша).
- Статус: requires fix (блокирует C2)
- Evidence: /tmp/bootstrap_b2b.log:133,164,181,197; «s3_ssl_cache module not loaded»

### F-09 · 2026-08-26 22:30 · B2 · P2 (status-page unhealthy)
- Симптом: status-page на ноде «Up 11 minutes (unhealthy)» — единственный нездоровый
  контейнер из 22.
- Статус: requires investigation (логи контейнера + F-009 из 011 = NODE_CONFIGS_DIR —
  возможно повтор).
- Evidence: docker ps (status-page unhealthy)

### F-10 · 2026-08-26 22:30 · B2 · P2 (cert coverage)
- Симптом: botanika.tronyx.ru и roadmap.tronyx.ru — «NO cert coverage after issue (ни
  direct, ни wildcard родителя)» → возможен «Missing cert» alarm. tronyx.ru wildcard
  существует (φ7 первого прогона), sexydancerostov.ru direct OK.
- Статус: requires investigation (проверка live/ каталога + coverage-детектор)
- Evidence: /tmp/bootstrap_b2b.log:180,196

### F-07-followup · 2026-08-26 22:40 · B4 · NOTE (стемл-байткод)
- Dry-run «undefined volume» сохранялся в логах bootstrap после F-07-fix: стейл .pyc на ноде
  (cpython-312.pyc 22:01 + cpython-314.pyc 22:19:45 — оба БЕЗ build_compose_args), mtime .py
  (22:18) < mtime .pyc (22:19:45) → Python не перекомпилировал. Лечение: rm __pycache__ →
  перекомпиляция с фиксом → dry-run PASS. На свежей ноде (cold bootstrap) проблема не
  воспроизводится (нет стейл-байткода). NOTE: rsync -t + инкрементальная доставка кода на
  живую ноду может воспроизводить этот класс — кандидат в DevPlan (touch/invalidate .pyc
  при core-deliver).
- Статус: auto-fixed (ручная инвалидация .pyc на ноде)

### F-07-class · 2026-08-26 22:40 · B4 · P1 (второй экземпляр баг-класса)
- converge/volumes.py:223 (R7 reconcile_volumes) использовал ТОТ ЖЕ isolated -f <module>/base.yml
  → «undefined volume» для stateful-модулей → R7 пропускал их volume-проверку (detection gap).
- Fix: build_compose_args (root-compose-first) — тот же паттерн, что F-07. converge R7 теперь
  GREEN «All named volumes exist».
- Статус: auto-fixed (см. F-07-class-fix)

### F-10-fix · 2026-08-26 22:45 · B4 · auto-fixed (P1)
- vhost_cli.py main(): platform_domain резолвился ТОЛЬКО из --platform-domain/env, НЕ из
  node.yaml → resolve_cert_domain всегда давал direct-cert путь для subdomains
  (botanika/roadmap.tronyx.ru) → nginx -t FAIL (R6), т.к. на ноде только wildcard *.tronyx.ru.
- Fix: fallback на NodeYaml.get("domain") (top-level, НЕ node.domain) → wildcard cert.
  Перегенерация render-vhosts: botanika/roadmap → cert=tronyx.ru (wildcard). converge R6 GREEN.
- Верификация: make render-vhosts NODE=tronyx-vps (platform_domain resolved from node.yaml);
  converge R6 «nginx -t passed».
- Статус: auto-fixed

### B4 ИТОГ · 2026-08-26 22:46 · PASS
- converge NODE=tronyx-vps: GREEN rc=0 (R6 nginx -t passed; R7 All named volumes exist;
  R2 audit.jsonl 0664 mutated — единственный mutated, штатно).
- check-security NODE=tronyx-vps: 8 PASS + 1 WARN (S2 apt-check rc=127 — известный на свежем
  Ubuntu 24.04, не блокер; прецедент 011).
- healthcheck: 22 контейнера, 21 healthy + status-page unhealthy (F-09, P2).

### СВОДКА ФИКСОВ СЕССИИ (для коммита) · 2026-08-26 22:46
- F-01 (node.yaml dead-поле postgres_init_databases) — локально + на ноде; node-configs gitignored
- F-05 (decrypt_secrets.py D3 fail-loud source=sops фильтр) + test_decrypt_secrets.py
- F-07 (deploy_orchestrator.py dry-run → build_compose_args)
- F-07-class (converge/volumes.py R7 → build_compose_args)
- F-10 (vhost_cli.py platform_domain → NodeYaml.get("domain") fallback)

### F-11 · 2026-08-26 23:10 · B5 · P2 (dev-эргономика)
- Симптом: make project-list / project-status на dev — «Found 0 node.yaml file(s) (filter='*')»,
  «No node.yaml files found under /Users/tronyx/projects/ai-platform». Сканер ищет node.yaml
  под КОРНЕМ репо, а не под NODE_CONFIGS_DIR (node-configs/), хотя .env корректно задаёт
  NODE_CONFIGS_DIR=…/node-configs и PROJECTS_BASE=/Users/tronyx/projects.
- Ожидалось / получено: offline-list проектов из node-configs/*/node.yaml; фактически 0 файлов.
- Гипотеза: scan-root резолвится в repo-root, а не NODE_CONFIGS_DIR (не закрыто plan 012 T19/F-017
  для bare-NODE/dev-режима). project-status (live SSH) тоже падает на find_node (0 node.yaml).
- Статус: requires fix (DevPlan); node-side проекты при этом reconciled (converge R3 — 5 STUB).
- Evidence: make project-list/project-status вывод; .env NODE_CONFIGS_DIR/PROJECTS_BASE корректны
