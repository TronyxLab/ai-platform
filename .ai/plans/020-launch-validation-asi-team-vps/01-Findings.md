# 01-Findings — 020 launch-validation asi-team-vps

$ARTIFACT_CONTRACT
PURPOSE:      Полная приёмо-сдаточная валидация платформы после крупного рефакторинга: с голой ноды
              ОДНА команда `make bootstrap-node NODE=asi-team-vps` поднимает сервер И деплоит все
              проекты контекста asi-group (roadmap) — без рук. Журнал находок + фиксов до победного.
DESCRIPTION:  Валидация по фазам A–H в собственном git-worktree (ветка launch-validation/asi-team-vps,
              база local main 321d1a7). Чинить до победного, push после каждого фикса.
              test-VPS недоступна → G5 = BLOCKED. Финальный промоут context-promote НЕ выполняется.
RATIONALE:    Критерий результата — одна команда с голого железа; каждая находка закрывается фиксом
              и ре-верификацией в этой сессии.
ACCEPTANCE_CRITERIA:
  AC1: make bootstrap-node NODE=asi-team-vps с голой ноды поднимает сервер И деплоит roadmap (конец = live).
  AC2: идемпотентность: повторный bootstrap = no-op; converge/check-security/e2e-verify зелёные.
  AC3: TLS wildcard DNS-01 выпущен + cache drill (восстановление из S3 БЕЗ ACME-запроса) + verify-domains.
  AC4: три канала доставки верифицированы (deploy-context / прямой / CI + rollback-контур).
  AC5: DR round-trip (бэкап→restore) + age-key-backup + RPO; chaos/reboot/load пройдены.
IMPLEMENTS:   §0a опрос владельца 2026-08-31 + контур валидации релиза asi-group.
IMPACTS:      node-configs/asi-team-vps, bootstrap-оркестрация (если деплой проектов не финальный шаг),
              каналы деплоя, TLS-кеш, DR-каналы. Работа в отдельном worktree.
REQUIRES:     age-key-asi (~/.ssh/age-key-asi.txt), креды regru DNS-01, нода 77.233.221.129 (голая),
              SOPS_AGE_KEY/AGE_SECRET_KEY, pre-commit hooks.
$END_ARTIFACT_CONTRACT

## Шапка: ответы владельца (§0a, 2026-08-31)

| # | Вопрос | Ответ |
|---|--------|-------|
| 1 | Состояние ноды asi-team-vps | **Пересоздам перед началом работы — предупреди** (холодный bootstrap) |
| 2 | Freeze на код | **Снят, чиню свободно** |
| 3 | Chaos/reboot-дриллы | **Да, часами** |
| 4 | test-VPS доступна | **Недоступна** → G5 = BLOCKED |
| 5 | Креды DNS regru | **Доступны** → wildcard выпустится |
| 6 | Проекты контекста | **Только roadmap** (другие создаются параллельно, не трогаю) |
| 7 | Git-база ворктри | **От локального main (321d1a7)** |

## Контекст узла (из node.yaml, не по памяти)

- context: `asi-group` · node: `asi-team-vps` · host: `77.233.221.129`
- domain: `asiteam.ru` · acme_dns_plugin: `regru` · email: admin@asiteam.ru
- projects: `roadmap` (roadmap.asiteam.ru, frontend, expose:true)
- modules: nginx, platform-secrets, logging, status-page
- tor: off · timezone: Europe/Moscow
- secrets: node-configs/asi-team-vps/secrets/asi-team-vps.enc.yaml (age-контур asi, отдельный ключ)

## PROGRESS-чеклист фаз

- [ ] Фаза A — локальная верификация (make check / agent-check / check-manifests / up / journal)
- [ ] Фаза B — bootstrap-node (холодный + деплой roadmap внутри) + идемпотентность + converge/security/sanity
- [ ] Фаза C — TLS wildcard + cache drill + verify-domains + мониторинг
- [ ] Фаза D — три канала доставки + rollback + provision-llm
- [ ] Фаза E — вариации конфигурации + node-update + converge + сетевая правда
- [ ] Фаза F — DR бэкап/restore + age-key-backup + RPO
- [ ] Фаза G — reboot + chaos + load-smoke + e2e-verify (+ test-node BLOCKED)
- [ ] Фаза H — Release checklist + 02-VerificationReport + ПРОМОУТ РАЗРЕШЁН/НЕ РАЗРЕШЁН

## Фаза A — локальная верификация (2026-08-31)

| # | Проверка | Результат |
|---|----------|-----------|
| A1 | `make check` (батч, 5647 pass) | ✅ PASS rc=0 |
| A2 | `make agent-check` | ✅ exit 0 (0 blocking / 0 advisory) |
| A3 | `make check MARKER=check-manifests` | ✅ GREEN |
| A4 | локальный стек | ✅ reuse поднятого основной моделью (postgres/pgbouncer/redis healthy 4-5 дней); service-exporters/status-page — нодовые модули, не локальные |
| A5 | test_journal + git | ✅ зафиксировано (branch=launch-validation/asi-team-vps) |

Примечание: `make healthcheck` локально падает на service-exporters/status-page — ожидаемо (эти модули не входят в локальный macOS-стек, верифицируются на ноде в фазе E1).

## Находки

### F-01 · 2026-08-31 19:40 · фаза B · P0
- Симптом: `make secrets-unlock NODE=asi-team-vps` → exit 10 (PlatformFatalError), fail-loud:
  POSTGRES_USER, MINIO_ROOT_USER, HERMES_DASHBOARD_PASSWORD, GF_SECURITY_ADMIN_PASSWORD,
  AGE_SECRET_KEY, TELEGRAM_BOT_TOKEN, WEBNAMES_API_KEY — «refusing to write partial secrets.env».
- Ожидалось / получено: расшифровка 14 ключей enc.yaml прошла, но fail-loud требует ещё 7.
- Гипотеза причины: `apply_ci_default_injection` (decrypt_secrets.py) fail-loud на ВСЕ
  required+sops ключи реестра secret-definitions.yaml БЕЗ учёта enabled-модулей node.yaml.
  asi-team-vps — минимальный контекст (nginx + platform-secrets + logging + status-page):
  consumers 6 из 7 ключей (postgres/minio/hermes/monitoring) НЕ включены; AGE_SECRET_KEY —
  protected env-переменная (LIFECYCLE_PROTECTED), приходит из env, никогда не в enc.yaml
  (курица-яйцо: им же шифруется enc.yaml).
- Фикс: module-aware fail-loud (consumers ∩ enabled-модули) + AGE_SECRET_KEY source sops→provisioner.
- Статус: **fixed** (коммиты 96b42c3 + 9b8a6af)
- Ре-верификация: `make secrets-unlock NODE=asi-team-vps` → exit 0 (module-aware: 4 enabled модуля,
  SKIP fail-loud для 6 ключей); `make check` → rc=0 ALL PASS.

### F-02 · 2026-08-31 20:30 · фаза B · P0
- Симптом: `make bootstrap-node NODE=asi-team-vps` exit 2. roadmap DEPLOYED+healthy, но nginx
  deploy-hook FAILED → nginx в restart-loop: `cannot load certificate /etc/letsencrypt/live/asiteam.ru/fullchain.pem`.
- Ожидалось / получено: φ7 certificates должна выпустить wildcard asiteam.ru + roadmap.asiteam.ru.
  Получено: φ7 ложно «certificates provisioned» (лог), /etc/letsencrypt/live/ ПУСТ.
- Гипотеза причины (двухслойная): (1) module-level pydantic-цепочка на системном python3 3.12
  (deploy_orchestrator:123 `from core.internal.llm import config_renderer` → pydantic) → ImportError →
  extract_domains_for_context=None заморожен; (2) ssl_provision_via_orchestrator возвращал «converged»
  при extract_domains_for_context=None (пустой список доменов) → φ7 ложный success.
- Фикс (коммит 379fd01): (A) skipped_import при extract_domains_for_context None; (B1) re-exec lifecycle
  на /usr/local/bin/python3 (3.14) после φ1; (B2) lazy-import config_renderer. +5 unit-тестов.
- Статус: fixed
- Ре-верификация: make check rc=0; φ7 теперь реально выпускает wildcard asiteam.ru (SAN *.asiteam.ru).

### F-03 · 2026-08-31 23:50 · фаза B · P0
- Симптом: φ8 deploy_services precondition «Docker daemon not running»; docker.service inactive,
  docker.socket trigger-limit-hit; platform-secrets.service failed (exit 10).
- Ожидалось / получено: после reboot decrypt должен работать. Получено: platform-secrets.service
  запускает decrypt БЕЗ NODE_NAME → module-aware fail-loud деградирует в legacy global → exit 10.
- Гипотеза причины: F-01 fix (module-aware) сделал валидацию зависимой от NODE_NAME, но
  systemd-путь platform-secrets.service (Before=docker, RequiredBy=docker) не задаёт NODE_NAME.
- Фикс: resolve_enabled_modules auto-detect единственной ноды (node_detect.auto_detect_node_name)
  при пустом node_name; явная нода → прежний резолв; 0/>1 нод → None (legacy) + WARN.
- Статус: **fixed** (коммит b3b3100, +2 unit-теста)
- Ре-верификация: make check rc=0; на ноде decrypt с auto-detect exit 0 (module-aware SKIP 6 ключей).

### F-04 · 2026-09-01 00:10 · фаза B · P0
- Симптом: φ8 deploy-modules.sh exit 10 — interpolation dry-run FAIL: 3 module(s)
  (nginx, logging, status-page): `ENCRYPTION_KEY is missing a value: ENCRYPTION_KEY_REQUIRED`.
- Ожидалось / получено: autogen-секреты (ENCRYPTION_KEY tier=generated/autogen) должны быть в
  secrets.env. Получено: secrets.env содержит только 16 ключей (14 sops + 2 ci_default), БЕЗ
  autogen-секретов.
- Гипотеза причины: `platform-secrets.service` (systemd reboot-путь) ExecStart запускает ТОЛЬКО
  `decrypt_secrets.py` (пишет 14+2 ключа, ПЕРЕЗАПИСЫВАЯ secrets.env), но НЕ вызывает
  `secrets_manager ensure` (autogen 11 секретов: ENCRYPTION_KEY, LITELLM_MASTER_KEY и др.).
  φ4 (bootstrap) делает decrypt + ensure → 27 ключей; reboot-путь теряет autogen → docker compose
  interpolation `${ENCRYPTION_KEY:?}` падает. Воспроизведено: вручную `systemctl start
  platform-secrets` после φ4 (где было 27 ключей) → secrets.env стал 16 ключей.
- Фикс: platform-secrets.service должен после decrypt генерировать autogen (ensure), как φ4.
  Реализовано: `ExecStartPost=python3 -m core.internal.bootstrap.lifecycle.secrets_manager ensure
  --manifest /opt/platform/core/secrets-manifest.yaml --secrets-env /var/lib/platform/run/secrets.env`
  — ensure идемпотентен (генерирует ТОЛЬКО missing, существующие НЕ перезаписывает; инвариант 2
  secrets_manager); порядок decrypt→ensure гарантирует systemd (ExecStartPost после успешного
  ExecStart); NODE_NAME в юните не задан → sops --set persistence пропускается (чистая догенерация
  missing); /var/lib/platform/run вне ProtectSystem=full → ReadWritePaths не требуется.
  +2 unit-теста: test_platform_secrets_unit::test_execstartpost_ensure_autogen_secrets (юнит-контракт
  ExecStartPost) + test_secrets_manager::test_ensure_secrets_reboot_path_over_partial_env (ensure
  поверх decrypt-вывода 17 ключей → 11 autogen, повторный вызов byte-identical).
- Статус: **fixed**
- Ре-верификация: make check rc=0 ALL PASS (20/20 checks, contract 305, static_audit 5306);
  make check MARKER=check-manifests GREEN.
- Симптом: `make bootstrap-node NODE=asi-team-vps` exit 2. roadmap DEPLOYED+healthy, но nginx
  deploy-hook FAILED → nginx в restart-loop: `cannot load certificate /etc/letsencrypt/live/asiteam.ru/fullchain.pem`.
- Ожидалось / получено: φ7 certificates должна выпустить wildcard asiteam.ru + roadmap.asiteam.ru.
  Получено: φ7 ложно «certificates provisioned» (лог), /etc/letsencrypt/live/ ПУСТ.
- Гипотеза причины (двухслойная):
  1. module-level pydantic-цепочка: node-lifecycle.sh запускает cli.py на системном python3 (3.12, без pydantic);
     domains.py guarded-import context_deployer → deploy/__init__ → deploy_orchestrator:123
     `from core.internal.llm import config_renderer` → llm/__init__ → policy_schema → pydantic
     → ImportError на 3.12 → `extract_domains_for_context=None` заморожен на весь процесс.
  2. ssl_provision_via_orchestrator при extract_domains_for_context=None возвращал [] доменов →
     `if not domains: return "converged"` → φ7 ложный success (контракт skipped_import нарушен).
- Фикс (коммит 379fd01): (A) ssl_provision_via_orchestrator проверяет extract_domains_for_context
  is None → "skipped_import" (не converged); (B1) re-exec lifecycle на /usr/local/bin/python3 (3.14)
  после φ1; (B2) lazy-import config_renderer в deploy_orchestrator (единственный pydantic-путь).
  +5 unit-тестов (test_phase_certificates_contract, test_lifecycle_cli_w5).
- Статус: fixed
- Ре-верификация: make check rc=0 ALL PASS; check-manifests GREEN.
