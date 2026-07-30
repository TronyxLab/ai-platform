<!-- GREP_SUMMARY: AGENTS.md, bootstrap, deploy-modules, orchestration, system, docker, idempotent -->

# GREP_SUMMARY: AGENTS.md, bootstrap, deploy-modules, orchestration, phases, 14-phases, state-machine
# STRUCTURE: ┌14 consolidated phases (DevPlan 087)┐ → ◇ BootstrapPhase enum → ◇ _phase_dependency_graph → ◇ precondition_check → ◇ grouped-phases (sub-checkpoints) → ◇ deploy-modules (system|docker) → ◇ idempotence (state.json + content-hash) → ◇ artifact paths → ⎋ cross-refs
# region MODULE_CONTRACT
## @purpose  Bootstrap pipeline orchestration: 14 consolidated phases (DevPlan 087),
##           node setup, module deployment, healthcheck execution, idempotent state machine
## @scope    All scripts under core/internal/bootstrap/ — node-lifecycle.sh (thin facade),
##           lifecycle/state_machine.py (BootstrapPhase enum, _phase_dependency_graph,
##           precondition_check, _resume_phase), lifecycle/phases.py (14 phase implementations),
##           lifecycle/state_migration.py (one-shot 23→14 key migration), deploy-modules,
##           setup-node, install-docker, install-tor-proxy, firewall, _topo_sort, content-hash,
##           discover_modules, remote-cmd, scp-deliver
## @invariants
##   1. node-lifecycle.sh — тонкий фасад (<80 LOC), делегирует всё state_machine.py. Режимы: --mode init (14 INIT фаз) и --mode update (5 UPDATE фаз).
##   2. state_machine.py — оркестрация: BootstrapPhase enum, _phase_dependency_graph, precondition_check(), _execute_phase(), _execute_grouped_phase(), _resume_phase()
##   3. phases.py — business logic: 14 phase_*() функций, вызываемых из state_machine.py
##   4. state_migration.py — однократная миграция 23→14 ключей при обновлении production ноды.
##   5. checkpoint_migration.py — удалён (DevPlan 087). Все чекпоинты через state.json напрямую.
##   6. Идемпотентность: state.json с 14 phase-ключами + content-hash для grouped-phase sub_steps
##   7. Артефакты: /opt/platform/core/ (core), /opt/<context>/platform/ (context-overlay)
##   8. Никаких git-операций в bootstrap — только SCP/rsync для core; git clone/pull только через ensure_context_repo() для context-overlay
## @rationale DevPlan 087: Consolidate 32+ steps → 14 phases with explicit dependency graph.
##            Eliminates 8 silent failure propagation points via precondition BLOCKS.
##            Adds partial failure recovery (_resume_phase()) for grouped phases.
# endregion MODULE_CONTRACT

# AGENTS.md — core/internal/bootstrap/

---

## Bootstrap pipeline (14 consolidated phases)

```
node-lifecycle.sh --mode init  →  state_machine.py (BootstrapPhase enum)

  φ1  system-bootstrap     # packages, docker-install, tor-proxy, firewall
  φ2  user-accounts        # ssh-access, platform-user, ci-deploy-user, projects-base
  φ3  platform-setup       # platform-dirs, docker-config, metrics-cron
  φ4  secrets-provision    # decrypt-secrets, ensure-passwords (BLOCKS φ6)
  φ5  node-configuration   # read-node-yaml, verify-core, verify-node-configs
  φ6  registry-auth        # ghcr-auth, docker-auth (BLOCKS φ8)
  φ7  certificates         # install-acme, ssl-provision
  φ8  deploy-services      # deploy-modules, deploy-context
  φ8.5 converge-services   # converge (explicit separate phase)

node-lifecycle.sh --mode update → state_machine.py

  φ9  secrets-update       # decrypt-secrets
  φ10 node-config-update   # read-node-yaml, verify-core
  φ11 registry-update      # ghcr-auth, provision, overlays, llm-keys, healthcheck
  φ12 deploy-update        # deploy-modules, ssl-provision, deploy-context
  φ13 converge-update      # converge
```

**Группировка:** φ1-φ5, φ7, φ12 — grouped phases с sub-checkpoint поддержкой.
Каждый подшаг внутри grouped-фазы имеет свой done-статус и content-hash в state.json.
При перезапуске: unchanged + done подшаги SKIP, изменившиеся EXECUTE, failed EXECUTE.
**Precondition BLOCKS:** φ4→φ6→φ8 — если φ4 не выполнен (нет секретов), φ6 (registry-auth) и φ8 (deploy-services) блокируются с читаемой ошибкой.
**Dependency graph:** см. `state_machine.py._phase_dependency_graph`.

⚠️ TRAP[BUG] · 2026-07-23 · P0 · FALSE DIAGNOSIS: webnames.ru zone_manager_unavailable ≠ DNS-01 broken
· Symptom: webnames.ru API returns `{"result":"ERROR","details":"zone_manager_unavailable"}` for `domains_list`.
· Reality: TXT record add/delete WORK. `zone_manager_unavailable` affects ONLY the listing endpoint.
  - add:    `{"result":"OK","details":1}`
  - delete: `{"result":"OK","details":1}`
· Proof: Wildcard cert `*.tronyx.ru` issued via LE staging 2026-07-23 with `acme.sh --dns dns_webnames`.
· Root cause of prior failure: Let's Encrypt rate-limit (50 certs/domain/week), NOT DNS API.
· Prevention: DO NOT disable DNS-01 or switch to HTTP-01 based on `domains_list` error. Verify add/delete first.
· Test: `curl "https://www.webnames.ru/scripts/json_domain_zone_manager.pl?apikey=$KEY&domain=$DOM&type=TXT&record=_acme-challenge.test:test123&action=add"`
· Rev: if add/delete also return zone_manager_unavailable → DNS-01 truly broken, HTTP-01 fallback applies.

**Вызов:** Только через `node-lifecycle.sh --mode init` или `node-lifecycle.sh --mode update`. Никогда напрямую.

---

## Режимы работы

`node-lifecycle.sh` поддерживает два режима, выбираемых через первый аргумент `--mode`:

### `--mode init` — полный bootstrap

Выполняет 17 шагов инициализации bare VPS: проверка SSH → apt-зависимости → Tor (опционально) → Docker → пользователи (platform, ci-deploy) → UFW → верификация core + node-configs → decrypt secrets → node.yaml валидация → GHCR auth → sudoers → node-update (вызов `--mode update`) → audit-log → Telegram. Идемпотентен: при повторном запуске шаги с неизменившимся content-hash пропускаются. Вызывается из `make bootstrap-node` через `core/entrypoints/bootstrap.sh`.

### `--mode update` — инкрементальный update

Выполняет 5 шагов на уже забутстрапленной ноде: verify-core (content-hash) → provision (networks + volumes) → issue-cert.sh (acme.sh DNS-01 wildcard cert) → deploy-modules (docker + system одним вызовом с --skip-provision) → healthcheck. S2 DevPlan 024: шаги deploy docker + deploy system объединены в один вызов deploy-modules.sh, устранён повторный полный проход main(). Оптимизирован для CI: ~5 мин вместо ~30 мин полного bootstrap. Вызывается из `make node-update` через `core/entrypoints/node-update.sh`, а также из step-14 init-режима (post-init update).

---

## deploy-modules.sh — две ветки

`deploy-modules.sh` обрабатывает два типа модулей, декларированных в `node.yaml`:

### system-модули
- Устанавливаются через install.sh в директории модуля
- Поддерживаются через `deploy_system_module()`:
  - `systemctl daemon-reload && systemctl enable --now <service>`
  - healthcheck: `healthcheck.sh` (liveness или deep mode)
- Примеры: nginx (системная установка)

### docker-модули
- Развёртываются через Docker Compose
- **Два режима** (feature flag `DEPLOY_PARALLEL`, default=false):
  - **Последовательный** (`DEPLOY_PARALLEL=false`, обратная совместимость):
    `_topo_sort.py` (сортировка по depends_on) → `docker compose pull` → `docker compose up -d`
  - **Параллельный** (`DEPLOY_PARALLEL=true`, DevPlan 050):
    `_topo_sort.py` → batch-metadata + batch-check-env → pre-pull (parallel_limit=4) → итерация по topo-группам →
    `deploy_docker_group()` (os.fork per module, content-hash skip для build-модулей) →
    parallel healthcheck внутри группы → severity-based exit
- Healthcheck: `docker inspect` → `State.Health.Status` (liveness) или `healthcheck.sh MODE=deep`
- При параллельном режиме healthcheck выполняется внутри `deploy_docker_group()`, маркер `/var/lib/platform/.bootstrap/.hc_done_in_deploy` предотвращает дублирование в node-lifecycle
- Content-hash skip (status-page, backup-cron): `content_hash.py` кэширует SHA256 исходников → пропускает `docker compose build` при совпадении
- Примеры: postgres, redis, litellm, langfuse, hermes-agent, status-page, backup-cron

### Фильтрация --modules
- `deploy-modules.sh --modules postgres,redis` → развернуть только указанные
- Без флага → все модули из `node.yaml`
- Фильтрация применяется ДО topo-sort — зависимости не резолвятся автоматически (зависимый модуль без зависимости = fail)

---

## Идемпотентность (state.json + phase-based keys)

**Механизм:** `state_machine.py` управляет state.json в `/var/lib/platform/.bootstrap/state.json`

| Механизм | Где | Что делает |
|----------|-----|------------|
| `state.json` (phase keys) | `/var/lib/platform/.bootstrap/state.json` | Единый source of truth для checkpoint'ов. Ключи — имена фаз BootstrapPhase enum. 14 ключей: system_bootstrap, user_accounts, platform_setup, secrets_provision, node_configuration, registry_auth, certificates, deploy_services, converge_services, secrets_update, node_config_update, registry_update, deploy_update, converge_update. |
| content-hash | `state_machine._step_hash()` (Python) | SHA256 content hash per sub-step для grouped phases (φ1-φ5, φ7, φ12). Хеш проверяется при _resume_phase() — unchanged+done = SKIP. |
| sub-checkpoints | nested в state.json | Grouped-фазы имеют `sub_steps: {name: {done: bool, hash: str}}` для granular idempotency |

**Пример:** `system_bootstrap` в state.json:
```json
{
  "done": true,
  "sub_steps": {
    "packages": {"done": true, "hash": "abc123"},
    "docker_install": {"done": true, "hash": "def456"},
    "tor_proxy": {"done": true, "hash": "ghi789"},
    "firewall": {"done": true, "hash": "jkl012"}
  }
}
```

**Миграция:** `state_migration.py::migrate_state_to_phases()` — однократная миграция при обновлении с 23 старых ключей на 14 новых. Composite hash: все подшаги done → фаза done. Старые ключи сохраняются для rollback. Вызывается при первом запуске после обновления.

**Сброс:** `rm /var/lib/platform/.bootstrap/state.json` или `make bootstrap-node ... --force` → следующий bootstrap будет полным.

---

## Артефакты

| Путь | Содержимое | Доставка |
|------|-----------|----------|
| `/opt/platform/core/` | core/ файлы (entrypoints, internal, lib, modules) | SCP/rsync push (core-deploy CI) |
| `/opt/<context>/platform/` | Context-overlay (ayaml, node-configs, кастомизации) | git clone/pull (ensure_context_repo()) |
| `/opt/platform/secrets/` | AGE-encrypted secrets | SCP (через decrypt-secrets.sh) |

---

---

### Lifecycle State Machine (DevPlan 087 — 14 phases)

```
INIT MODE (9 phases):
  φ1  system-bootstrap ──→ φ2  user-accounts ──→ φ3  platform-setup
                                  │                      │
                                  ├──→ φ5  node-config    │
                                  │                      ├──→ φ4  secrets-provision
                                  │                      │         │
                                  │                      │         ├──→ φ6  registry-auth
                                  │                      │         │         │
                                  │                      │         ├────←────┘
                                  │                      │         ↓
                                  │                      └──→ φ7  certificates
                                  │                                 │
                                  └──────────────────────┬───────────┘
                                                         ↓
                                                    φ8  deploy-services
                                                         │
                                                         ↓
                                                    φ8.5 converge-services

UPDATE MODE (5 phases):
  φ9  secrets-update ──────────────────────────────┐
  φ10 node-config-update (no deps)                  ├──→ φ12 deploy-update
  φ11 registry-update ─────────────────────────────┘       │
                                                            ↓
                                                       φ13 converge-update
```

**Dependency rules:**
- φ6 (registry-auth) requires φ4 (secrets) — needs credentials for docker login
- φ8 (deploy-services) requires φ4 (secrets) + φ6 (registry) + φ7 (certs) — all three prerequisites
- φ8.5 (converge) requires φ8 (deploy) — structural ordering
- φ12 (deploy-update) requires φ9 (secrets-update) + φ11 (registry-update)
- φ13 (converge-update) requires φ12 (deploy-update)

**Precondition checks per phase (state_machine.py.BootstrapState.precondition_check()):**
- φ1: root access (euid=0), apt-get/dpkg available
- φ2: useradd/id/chown commands available
- φ4: AGE_SECRET_KEY env var or /etc/age/key.txt exists
- φ5: NODE_YAML file exists and is readable
- φ8/φ12: Docker daemon running, deploy-modules.sh exists
- φ6: GHCR_PULL_TOKEN present (warning only)

**Partial failure recovery (_resume_phase()):**
Grouped phases (φ1-φ5, φ7, φ12) support sub-checkpoints. If φ4 partially fails
(decrypt-secrets OK, ensure-passwords FAIL), restart only runs the failed sub-step.
Successful sub-steps with unchanged hash are SKIPPED — no age passphrase re-entry required.

### SSL Cert Lifecycle Unification (DevPlan 052)

**Мотивация:** Bug-репорт 2026-07-25: сертификаты выпускались и сайты работали, но при повторном bootstrap не восстанавливались из S3. Корневая причина: (а) `_ssl_provision()` не прокидывала S3_* креды в subprocess — s3-ssl-cache.sh upload/download молча падали; (б) cert_orchestrator пропускал platform domain (cert на диске) — upload в S3 не вызывался.

**Архитектура (DevPlan 052 Phase 1-3):**

| Компонент | Назначение | Взаимодействие |
|-----------|-----------|----------------|
| `s3_ssl_cache.py` (NEW) | Python-порт s3-ssl-cache.sh — upload/download/check/bulk-restore через boto3 | Прямой импорт в cert_orchestrator.py (без subprocess). os.environ доступ решает проблему credential propagation. |
| `s3-ssl-cache.sh` (REDUCED) | CLI-фасад ~30 строк | Парсинг аргументов + вызов python3 s3_ssl_cache.py. Только для обратной совместимости (issue-cert.sh --reloadcmd). |
| `cert_orchestrator.py` (MODIFIED) | Unified SSL entrypoint | Прямой импорт s3_ssl_cache, upload-on-skip (cert на диске → upload в S3), upload после успешного issue. |
| `state_machine.py` (MODIFIED) | `_ssl_provision_via_orchestrator()` | Вызывает cert_orchestrator.orchestrate_certs() для ALL domains (platform + projects). |

**Ключевые изменения:**

1. **Restore-first:** `_process_single_domain()` → check disk → check S3 → issue-cert.sh. Восстановление из S3 без acme.sh API вызова.
2. **Upload-on-skip:** При существующем сертификате на диске — upload в S3 (платформенный домен теперь всегда в кеше).
3. **acme.sh --reloadcmd:** Добавлен вызов `python3 s3_ssl_cache.py upload $domain` после reload nginx — S3 синхронизация при каждом cron renewal.
4. **acme.sh --renew-hook:** `_acme_install_cron()` устанавливает `--renew-hook` с вызовом s3_ssl_cache.py upload — гарантирует S3 backup после автоматического обновления.

**Pipeline flow (after DevPlan 052):**

```
UPDATE mode:
  → ssl_provision: cert_orchestrator.orchestrate_certs(ALL domains)
      ├── disk check → upload (synced) / S3 check → download restore / issue-cert → upload
      └── Все домены обработаны restore-first в Python-процессе
  → deploy_context: cert_orchestrator.orchestrate_certs(ALL domains)
      ├── disk check → upload (synced) — второй вызов idempotent
      └── S3 синхронизирован для всех доменов

CRON:
  acme.sh --cron → renew cert → --reloadcmd (nginx reload + s3_ssl_cache upload)
  → --renew-hook (s3_ssl_cache upload с $Le_Domain) — двойная страховка
```

### Shell-фасады: сводка

| Скрипт | До (LOC) | После (LOC) | Сокращение |
|--------|----------|-------------|------------|
| `deploy-modules.sh` | 1664 | 91 | 95% |
| `converge.sh` | 1149 | 137 | 88% |
| `node-lifecycle.sh` | 1301 | 164 | 87% |
| **Итого** | **4114** | **392** | **90%** |

Inline `python3 -c` / `<<PYEOF` в фасадах: 0 (было 31 в топ-3). Все inline-блоки мигрированы в Python-функции с unit-тестами.

### Unit-тесты

Все модули имеют unit-тесты в `tests/unit/`:

- `tests/unit/test_docker_orchestrator.py`
- `tests/unit/test_sudoers_generator.py`
- `tests/unit/test_context_overlay.py`
- `tests/unit/test_secrets_validator.py`
- `tests/unit/test_orphan_reconciler.py`
- `tests/unit/test_reconciler.py`
- `tests/unit/test_state_machine.py`
- `tests/unit/test_s3_ssl_cache.py` (NEW — DevPlan 052 Phase 3)
- `tests/unit/test_cert_upload_on_skip.py` (NEW — DevPlan 052 Phase 3)

---

## Cross-references

| Файл | Назначение |
|------|-----------|
| [`../../../core/AGENTS.md`](../../AGENTS.md) | Канонические операции, структура слоёв, forbidden-списки |
| [`../../../AGENTS.md`](../../../AGENTS.md) | Архитектурные инварианты, модель деплоя, dual delivery |
| [`../../../core/entrypoint-manifest.yaml`](../../entrypoint-manifest.yaml) | YAML-реестр операций (bootstrap-node в секции bootstrap) |
