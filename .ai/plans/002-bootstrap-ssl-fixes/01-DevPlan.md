$START_DEVPLAN
## $ARTIFACT_CONTRACT
- **PURPOSE:** Устранить четыре проблемы bootstrap, ошибочно занесённые в StatusReport как «известные» — на деле это баги архитектуры, требующие исправления в коде пайплайна.
- **DESCRIPTION:** (1) Вынос SSL-провижининга из nginx/install.sh (мёртвый код для docker-типа) в отдельный pre-deploy шаг node-lifecycle.sh. (2) Добавление pre-flight проверки наличия образа hermes-agent в ghcr.io перед деплоем. (3) Улучшение диагностики Telegram/Tor — явные сообщения о несогласованности конфигурации вместо молчаливого SKIP. (4) Установка acme.sh cron — автоматически решается фиксом 1.
- **RATIONALE:** nginx переведён с system на docker (install_type: docker), но SSL-провижининг (acme.sh → DNS-01 → wildcard cert → cron) остался в install.sh, который вызывается ТОЛЬКО для system-модулей. Docker-nginx стартует с пустым `/etc/letsencrypt` → restart loop. Это не «известная проблема», а архитектурный дефект.
- **ACCEPTANCE_CRITERIA:**
  1. `ssl-provision.sh` извлекает acme.sh-логику из nginx/install.sh и вызывается как шаг update-mode пайплайна ДО docker-деплоя
  2. После SSL-провижининга nginx docker стартует с валидным сертификатом (healthcheck PASS)
  3. acme.sh cron установлен (crontab содержит acme.sh --cron)
  4. При отсутствии hermes-agent образа в ghcr.io — FAIL с сообщением о необходимых командах сборки
  5. При tor.enabled=false, но TELEGRAM_BOT_TOKEN задан — явное IMP:9 предупреждение о несогласованности
- **IMPLEMENTS:** Wave 2 bootstrap fixes (SSL, hermes-agent pre-check, Telegram diagnostics)
- **IMPACTS:** node-lifecycle.sh (update mode steps), deploy-modules.sh (pre-deploy image check), nginx/install.sh (deprecation)
- **REQUIRES:** Выполненный Wave 1 bootstrap (001) на tronyx-vps

---

## Requirements Analysis

### Key Success Criteria
1. **nginx стартует с HTTPS** — сертификат Let's Encrypt выпущен ДО запуска docker-контейнера nginx
2. **acme.sh cron работает** — ежедневный automatic renewal через crontab
3. **Hermes-agent deploy** — либо образ найден, либо чёткая ошибка с инструкцией
4. **Telegram** — диагностика несогласованности Tor/Telegram конфигурации
5. **Идемпотентность** — повторный bootstrap не перевыпускает сертификат, не дублирует cron

### Root Cause Analysis

| # | Проблема | Корневая причина | Где |
|---|----------|-----------------|-----|
| 1 | nginx restart loop (нет SSL) | nginx `install_type: docker` → `deploy-modules.sh` вызывает `deploy_docker_module()` (compose up), а НЕ `deploy_system_module()` (install.sh). Вся acme.sh-логика в install.sh — никогда не выполняется. | nginx/module.yaml:15, deploy-modules.sh:767 |
| 2 | hermes-agent image not found | L1-образ не запушен в ghcr.io, L2 не собран. Bootstrap не проверяет наличие образа перед деплоем — молча WARN. | deploy-modules.sh: deploy_docker_module() |
| 3 | Telegram не отправлен | tor.enabled=false в node.yaml → step_3 пропущен. TELEGRAM_BOT_TOKEN отсутствует → step_17 SKIP. Диагностика: молчаливый SKIP вместо явного сообщения. | node-lifecycle.sh:773-778, 522-526 |
| 4 | acme.sh cron не создан | Следствие проблемы 1 — acme.sh никогда не установлен. | nginx/install.sh:980-989 (не вызывается) |

---

## Architecture Overview

### Draft Code Graph

```
core/internal/bootstrap/
├── node-lifecycle.sh          [MODIFY] +update_step_3_ssl_provision, renumber 3→4,4→5,5→6
├── ssl-provision.sh           [NEW]     install_acme, issue_tls_cert, _acme_install_cron, _acme_verify_cert
├── deploy-modules.sh          [MODIFY]  +_check_image_exists() pre-check в deploy_docker_module()
└── AGENTS.md                  [MODIFY]  update pipeline diagram

core/modules/nginx/
└── install.sh                 [MODIFY]  deprecation header + pointer to ssl-provision.sh
```

### Step-by-Step Data Flow (update mode, after fix)

```
node-lifecycle.sh --mode update
├── 1. verify-core           # Content hash verification
├── 2. provision             # Networks + volumes from platform-env.yaml
├── 3. ssl-provision [NEW]   # ─── SSL CERT PROVISIONING ───
│   ├── extract PLATFORM_DOMAIN, PLATFORM_EMAIL, PLATFORM_ACME_DNS_PLUGIN from node.yaml
│   ├── check /etc/letsencrypt/live/<domain>/fullchain.pem → SKIP if exists (idempotent)
│   ├── install_acme → clone acme.sh + dnsapi extensions → /opt/acme.sh
│   ├── issue_tls_cert → DNS-01 wildcard via acme.sh (webnames or generic plugin)
│   ├── _acme_install_cron → crontab daily renewal
│   └── _acme_verify_cert → openssl x509 expiry >30 days check
├── 4. deploy-docker         # docker compose up -d (includes nginx with valid cert)
├── 5. deploy-system         # system modules (install.sh)
└── 6. healthcheck           # per-module healthcheck (nginx now healthy)
```

### SSL Provision Data Flow (detail)

```
ssl-provision.sh
  ▶ init → source lib/{paths,logging,checkpoint}.sh
  → extract_env() → python3 parse node.yaml → PLATFORM_DOMAIN, PLATFORM_EMAIL, PLATFORM_ACME_DNS_PLUGIN, PLATFORM_PROJECT_DOMAINS
  → ○ guard: /etc/letsencrypt/live/$PLATFORM_DOMAIN/fullchain.pem exists? → SKIP (idempotent)
  → ○ guard: PLATFORM_DOMAIN set? → FAIL if empty
  → ○ guard: PLATFORM_ACME_DNS_PLUGIN set? → FAIL if empty
  → ○ guard: webnames → WEBNAMES_API_KEY set? → FAIL if empty
  → install_acme() → git clone acme.sh + regtime-ltd/dnsapi → /opt/acme.sh
  → issue_tls_cert() → _issue_acme_cert() → DNS-01 → --install-cert → /etc/letsencrypt
  → _acme_install_cron() → acme.sh --install-cronjob (idempotent)
  → _acme_verify_cert() → openssl x509 expiry >30d
  → [optional] _issue_project_certs() → single-domain certs for PLATFORM_PROJECT_DOMAINS
  ⎋ exit 0 (success) | exit 1 (cert missing after attempt)
```

---

## Design Decisions

### ## @rationale DD1: SSL provisioning — отдельный update-шаг, а не часть provision
**Q:** Почему не добавить SSL в существующий `update_step_2_provision`?
**A:** `provision` создаёт сети/volumes из `platform-env.yaml` (бездоменная операция). SSL provision требует domain/email/dns_plugin из `node.yaml` (доменно-зависимая операция). Разная ответственность. Отдельный шаг = независимый checkpoint, независимый content-hash, лёгкий SKIP при идемпотентном перезапуске.

### ## @rationale DD2: Извлечение (extract), а не source из nginx/install.sh
**Q:** Почему не сделать `source core/modules/nginx/install.sh` и вызвать готовые функции?
**A:**
- `install.sh` завязан на systemctl, nginx apt-пакеты, и system-nginx конфигурацию — source притащит десятки нерелевантных функций и констант
- Cross-layer violation: internal/bootstrap не должен зависеть от modules/
- `install.sh` — depreciруемый код (TRAP[DEBT] от 2026-07-16), source создаст обратную зависимость
- Чистое извлечение 5 функций (~200 строк) в `ssl-provision.sh` — явный контракт

### ## @rationale DD3: Нумерация update-шагов — последовательная, не substep
**Q:** Почему перенумерация (3→4,4→5,5→6), а не `update_step_2b`?
**A:** Substep-нейминг (`2b`) нестандартен для кодовой базы. Все шаги имеют последовательную нумерацию. Перенумерация механическая (16 строк в одном файле), риск минимален, grep-аудит подтверждает отсутствие внешних ссылок на старые номера.

### ## @rationale DD4: Hermes-agent pre-check — docker manifest inspect, не docker pull
**Q:** Почему не `docker pull` с последующей очисткой?
**A:** `docker manifest inspect` проверяет наличие манифеста в registry БЕЗ загрузки слоёв. Быстрее, меньше трафика, не засоряет локальный Docker. Если manifest найден → OK. Если нет → FAIL с перечнем команд сборки.

### ## @rationale DD5: Telegram/Tor — диагностика, не автоконфигурация
**Q:** Почему не включить Tor принудительно при наличии TELEGRAM_BOT_TOKEN?
**A:** Tor требует bridges (файл `tor/bridges.txt`), которые являются внешним секретом. Автовключение без bridges = нерабочий Tor. Правильное поведение: обнаружить несогласованность (Tor выключен, но Telegram настроен) и выдать явное предупреждение.

---

## $TASKS

| Task | Description | Files | Complexity | Dependencies |
|------|-------------|-------|------------|--------------|
| T1 | Создать `ssl-provision.sh` — извлечь acme.sh функции из nginx/install.sh | `ssl-provision.sh` (NEW) | 6 | — |
| T2 | Добавить `update_step_3_ssl_provision` в node-lifecycle.sh + перенумерация | `node-lifecycle.sh` | 5 | T1 |
| T3 | Добавить pre-check образа в `deploy_docker_module()` | `deploy-modules.sh` | 3 | — |
| T4 | Добавить диагностику Telegram/Tor в node-lifecycle.sh | `node-lifecycle.sh` | 2 | — |
| T5 | Обновить AGENTS.md (pipeline diagram) | `AGENTS.md` (bootstrap) | 1 | T2 |
| T6 | Депрекейшн nginx/install.sh | `nginx/install.sh` | 1 | T1 |
| T7 | Запустить тесты (pytest + smoke) | — | 1 | T1–T6 |

### Critical Path
T1 → T2 → T5 → T7

### Merge candidates
- T4 (2 строки) сливается в T2
- T6 (1 файл, header change) сливается в T1

Merged: T1+T6 = T1', T2+T4 = T2'

---

## $TEST_SPEC
| Test file | Test function | Scenario | Module under test |
|-----------|---------------|----------|-------------------|
| tests/test_bootstrap_auto.py | `test_ssl_provision_idempotent` | Сертификат уже существует → SKIP, exit 0 | ssl-provision.sh |
| tests/test_bootstrap_auto.py | `test_ssl_provision_missing_domain` | PLATFORM_DOMAIN пуст → FAIL, exit 1 | ssl-provision.sh |
| tests/test_bootstrap_auto.py | `test_ssl_provision_missing_dns_plugin` | PLATFORM_ACME_DNS_PLUGIN пуст → FAIL | ssl-provision.sh |
| tests/test_bootstrap_auto.py | `test_acme_install_cron` | acme.sh установлен → crontab содержит acme.sh --cron | ssl-provision.sh |
| tests/test_bootstrap_auto.py | `test_image_check_exists` | Образ есть в registry → exit 0 | deploy-modules.sh (_check_image_exists) |
| tests/test_bootstrap_auto.py | `test_image_check_missing` | Образ отсутствует → FAIL с сообщением | deploy-modules.sh (_check_image_exists) |
| tests/test_bootstrap_auto.py | `test_tor_telegram_diag` | tor.enabled=false + TELEGRAM_BOT_TOKEN set → WARN в логах | node-lifecycle.sh step_3/step_17 |

---

## Acceptance Criteria (Summary)

| # | Критерий | Проверка |
|---|----------|----------|
| AC1 | ssl-provision.sh извлекает acme.sh-логику | grep `install_acme\|issue_tls_cert\|_acme_install_cron` в новом файле |
| AC2 | update_step_3_ssl_provision существует в node-lifecycle.sh | grep `update_step_3_ssl_provision` |
| AC3 | Шаги 4,5,6 перенумерованы корректно | grep `update_step_[4-6]` — 3 совпадения определений + 3 вызова |
| AC4 | deploy_docker_module проверяет образ перед deploy | grep `_check_image_exists\|manifest inspect` в deploy-modules.sh |
| AC5 | Tor/Telegram диагностика выводится на IMP:9 | grep `IMP:9.*tor.*telegram\|IMP:9.*Tor disabled but Telegram` |
| AC6 | nginx/install.sh содержит депрекейшн-заголовок | grep `DEPRECATED.*ssl-provision.sh` |
| AC7 | Все тесты проходят | `pytest tests/test_bootstrap_auto.py -v -k "ssl_provision or image_check or tor_telegram"` |

---

## File Manifest

| Файл | Действие | Строк (оценка) |
|------|----------|---------------|
| `core/internal/bootstrap/ssl-provision.sh` | NEW | ~250 |
| `core/internal/bootstrap/node-lifecycle.sh` | MODIFY | ~30 changed lines |
| `core/internal/bootstrap/deploy-modules.sh` | MODIFY | ~15 added lines |
| `core/internal/bootstrap/AGENTS.md` | MODIFY | ~5 changed lines |
| `core/modules/nginx/install.sh` | MODIFY | ~5 added lines (header) |
| `tests/test_bootstrap_auto.py` | MODIFY | ~120 added lines (7 test functions) |

---

## $PARALLEL_GROUPS

### Wave 1 (independent, no shared files)
- Tasks: T1' (ssl-provision.sh + nginx/install.sh deprecation), T3 (deploy-modules.sh image check)
- T1' touches: ssl-provision.sh (NEW), nginx/install.sh (MODIFY)
- T3 touches: deploy-modules.sh (MODIFY)
- No shared files → parallel safe

### Wave 2 (depends on Wave 1)
- Task: T2' (node-lifecycle.sh: ssl step + telegram diag)
- Depends on: T1' (ssl-provision.sh must exist for source path)

### Wave 3 (depends on Wave 2)
- Task: T5 (AGENTS.md pipeline update)
- Depends on: T2' (step numbers finalized)

### Wave 4 (verification)
- Task: T7 (run tests)

---

## Next Steps

### Wave 1
```
coder Read /Users/tronyx/projects/ai-platform/.ai/plans/002-bootstrap-ssl-fixes/01-DevPlan.md, implement Wave 1: T1' (create ssl-provision.sh, deprecate nginx/install.sh) and T3 (image pre-check in deploy-modules.sh)
```

### Wave 2
```
coder Read /Users/tronyx/projects/ai-platform/.ai/plans/002-bootstrap-ssl-fixes/01-DevPlan.md, implement Wave 2: T2' (add ssl step + tor/telegram diagnostics in node-lifecycle.sh)
```

### Wave 3
```
coder Read /Users/tronyx/projects/ai-platform/.ai/plans/002-bootstrap-ssl-fixes/01-DevPlan.md, implement Wave 3: T5 (update AGENTS.md pipeline) + T7 (run tests)
```

$END_DEVPLAN
