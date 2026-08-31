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
  (курица-яйцо: им же шифруется enc.yaml). tронyx-vps enc.yaml тоже не содержит эти ключи
  (его формат — nested data:, перешифрован основной моделью 2026-08-31 16:59).
  Второй слой: `verify_required_sops_secrets` (helpers/secrets.py) — та же глобальная
  проверка, сработает после fix decrypt (φ4 postcondition).
- Фикс: module-aware fail-loud — учитывать enabled-модули node.yaml (consumers ∩ enabled).
- Статус: **fixed** (коммиты 96b42c3 + 9b8a6af)
- Ре-верификация: `make secrets-unlock NODE=asi-team-vps` → exit 0 (module-aware: 4 enabled модуля,
  SKIP fail-loud для 6 ключей невключённых модулей; AGE_SECRET_KEY source sops→provisioner);
  `make check` → rc=0 ALL PASS. Coder-субагент: новый shared-резолвер
  `core/internal/shared/enabled_modules.py` + модуль-aware в apply_ci_default_injection +
  verify_required_sops_secrets + 4 новых unit-теста.
- Evidence: `/tmp/secrets_unlock_B1_*.log`; grep secret-definitions.yaml (AGE_SECRET_KEY provisioner)
