<!-- GREP_SUMMARY: AGENTS.md, bootstrap, deploy-modules, orchestration, system, docker, idempotent -->

# GREP_SUMMARY: AGENTS.md, bootstrap, deploy-modules, orchestration, phases, 14-phases, state-machine
# STRUCTURE: ┌14 consolidated phases (DevPlan 087)┐ → ◇ BootstrapPhase enum → ◇ _phase_dependency_graph → ◇ precondition_check → ◇ grouped-phases (sub-checkpoints) → ◇ deploy-modules (system|docker) → ◇ idempotence (state.json + content-hash) → ◇ artifact paths → ⎋ cross-refs
# region MODULE_CONTRACT
## @purpose  Bootstrap pipeline orchestration: 14 consolidated phases (DevPlan 087),
##           node setup, module deployment, healthcheck execution, idempotent state machine
## @scope    All scripts under core/internal/bootstrap/ — node-lifecycle.sh (thin facade),
##           lifecycle/state_machine.py (BootstrapPhase enum, _phase_dependency_graph,
##           precondition_check — оркестрация), lifecycle/state_store.py (persistence),
##           lifecycle/cli.py (CLI/main), lifecycle/helpers/ (7 I/O-модулей),
##           lifecycle/phases.py (14 phase implementations),
##           deploy-modules, setup-node, install-docker, install-tor-proxy, firewall,
##           topo_sort, discover_modules, remote-cmd, scp-deliver,
##           core_deliverer.py (Python Core-канал: mkdir + 5 rsync фаз, DevPlan 108)
## @invariants
##   1. node-lifecycle.sh — тонкий фасад (<80 LOC), делегирует всё lifecycle/cli.py (B9 T1, CS-7). Режимы: --mode init (9 INIT фаз) и --mode update (5 UPDATE фаз).
##   2. state_machine.py — оркестрация: BootstrapPhase enum, _phase_dependency_graph, precondition_check(), execute_phase()
##      (execute_grouped_phase удалён, волна 117 D5 — sub-step resume вне скоупа)
##   3. phases.py — business logic: 14 phase_*() функций, вызываемых из state_machine.py;
##      I/O-хелперы — lifecycle/helpers/ (односторонняя зависимость state_machine → phases → helpers, B9 T1)
##   4. checkpoint_migration.py — удалён (DevPlan 087). Все чекпоинты через state.json напрямую.
##   (Legacy 23→14 key migration removed in DevPlan 091 Wave B — cold start only.)
##   5. Идемпотентность: state.json с 14 phase-ключами + content-hash для grouped-phase sub_steps
##   6. Persistence (StepState/BootstrapState + state.json I/O) — lifecycle/state_store.py (B9 T2);
##      CLI (build_parser/main/run_init_mode/run_update_mode) — lifecycle/cli.py
##   7. Артефакты: /opt/platform/core/ (core), /opt/\<context\>/platform/ (context-overlay)
##   8. Никаких git-операций в bootstrap — только SCP/rsync для core; git clone/pull только через ensure_context_repo() для context-overlay
## @rationale DevPlan 087: Consolidate 32+ steps → 14 phases with explicit dependency graph.
##            Eliminates 8 silent failure propagation points via precondition BLOCKS.
##            Adds grouped-phase sub-checkpoints with partial-failure skip semantics.
##            DevPlan 116 B9 (U-08): SRP-декомпозиция state_machine (2284 → ~950 LOC).
# endregion MODULE_CONTRACT

# AGENTS.md — core/internal/bootstrap/

---

## Bootstrap pipeline (14 consolidated phases)

```
node-lifecycle.sh --mode init  →  lifecycle/cli.py → state_machine.py (BootstrapPhase enum)

  φ1  system-bootstrap     # packages, python3.14+deps, docker-install, tor-proxy, firewall
  φ2  user-accounts        # ssh-access, platform-user, ci-deploy-user, projects-base
  φ3  platform-setup       # platform-dirs, docker-config, metrics-cron
  φ4  secrets-provision    # decrypt-secrets, ensure-passwords (BLOCKS φ6)
  φ5  node-configuration   # read-node-yaml, verify-core, verify-node-configs
  φ6  registry-auth        # ghcr-auth, docker-auth (BLOCKS φ8)
  φ7  certificates         # install-acme, ssl-provision
  φ8  deploy-services      # deploy-modules, deploy-context
  φ8.5 converge-services   # converge (explicit separate phase)

node-lifecycle.sh --mode update → lifecycle/cli.py → state_machine.py

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
**CLI:** `lifecycle/cli.py` (build_parser/main/run_init_mode/run_update_mode); persistence — `lifecycle/state_store.py`; I/O — `lifecycle/helpers/` (B9 T1/T2).

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

Выполняет 9 фаз инициализации bare VPS: φ1 system-bootstrap (root, apt, Python 3.14, Docker, Tor, firewall) → φ2 user-accounts (platform/ci-deploy users, SSH keys) → φ3 platform-setup (Docker Hub auth, setup-node/sudoers, metrics-cron → /etc/cron.d/platform-metrics, DevPlan 116 B3 T1) → φ4 secrets-provision (decrypt, ensure-passwords) → φ5 node-configuration (node.yaml validation, core verification) → φ6 registry-auth (ghcr.io, Docker auth) → φ7 certificates (acme.sh, SSL) → φ8 deploy-services (deploy-modules, deploy-context) → φ8.5 converge-services (converge). Идемпотентен: phase-функции в phases.py обрабатывают content-hash пропуск внутри grouped-фаз. Вызывается из `make bootstrap-node` через `core/entrypoints/bootstrap.sh`.

### Python runtime на ноде (2026-08-01)

- **Цель:** голый `python3` после φ1 = Python **3.14** (deadsnakes PPA, Ubuntu 24.04).
- Механизм: `python_deps.py ensure --core-dir <core_dir>` вызывается в φ1 (шаг 1.5, FATAL):
  `DEBIAN_FRONTEND=noninteractive` apt-установка `software-properties-common` → `add-apt-repository -y ppa:deadsnakes/ppa` → `apt-get update` → `python3.14 python3.14-venv` → `python3.14 -m ensurepip --upgrade` → симлинк `/usr/local/bin/python3 → /usr/bin/python3.14` (PATH: /usr/local/bin раньше /usr/bin).
- **Системный `/usr/bin/python3` (3.12) НЕ трогается** — на нём висят cloud-init и системные утилиты. update-alternatives для python3 запрещён.
- Идемпотентность: маркер `/var/lib/platform/.bootstrap/python-deps.hash` содержит (хеш requirements.txt + версию python) — повторный бутстрап = no-op; старый маркер (только хеш, эра 3.12) трактуется как mismatch → переустановка.
- Зависимости ставятся через `/usr/local/bin/python3 -m pip install -r requirements.txt --break-system-packages` (PEP 668 — deadsnakes 3.14 тоже externally-managed).
- Не-Ubuntu 24.04 → WARN + fallback на system `python3-pip` (старый путь, без 3.14).

### `--mode update` — инкрементальный update

Выполняет 5 фаз обновления на уже забутстрапленной ноде: φ9 secrets-update → φ10 node-config-update → φ11 registry-update → φ12 deploy-update → φ13 converge-update. Каждая фаза реализована в phases.py с precondition проверками и dependency graph. Оптимизирован для CI: ~5 мин вместо ~30 мин полного bootstrap. Вызывается из `make node-update` через `core/entrypoints/node-update.sh`, а также из пост-инициализационного запуска (после φ8 deploy-services в init-режиме).

---

## deploy-modules.sh — фасад + Python-оркестратор (DevPlan 100)

`deploy-modules.sh` — тонкий shell-фасад (≤50 LOC): arg parsing, root/NODE_YAML check,
network provision, docker login, затем `exec python3 deploy/deploy_orchestrator.py`.
Вся routing-логика (PARALLEL / ORCHESTRATOR / SEQUENTIAL), деплой модулей, sudoers,
orphan-реконсиляция и severity-based exit code {0,1,2} — в `deploy/deploy_orchestrator.py`
(импортирует `docker_orchestrator`, `secrets_validator`, `topo_sort`, `sudoers_generator`,
`orphan_reconciler`, `context_overlay`, `spool_validator` нативно — без subprocess).

### Типы модулей (из node.yaml)
- **system-модули** — `invoke_module_interface <name> install` (install.sh в директории модуля),
  затем healthcheck liveness (best-effort). Примеры: nginx (системная установка)
- **docker-модули** — Docker Compose через `deploy_docker_module()` / `deploy_docker_group()`.
  Примеры: postgres, redis, litellm, langfuse, hermes-agent, status-page, backup-cron

### Режимы деплоя (feature flag `DEPLOY_PARALLEL`, default=false)
- **Последовательный** (`DEPLOY_PARALLEL=false`, обратная совместимость): for-loop по enabled-модулям:
  check-env → detect-type → `deploy_docker_module()` | `invoke_module_interface install`
- **Параллельный** (`DEPLOY_PARALLEL=true`, DevPlan 050): `topo_sort` (Kahn по depends_on) →
  pre-pull (parallel_limit=4) → batch-check-env → итерация по topo-группам →
  `deploy_docker_group()` (os.fork per module, content-hash skip для build-модулей) →
  system-модули sequential → маркер `/var/lib/platform/.bootstrap/.hc_done_in_deploy` (healthcheck уже выполнен внутри группы)
- **DeployOrchestrator** (`DEPLOY_ORCHESTRATOR=true` + `DEPLOY_PARALLEL=true`):
  `orchestrator_cli.py deploy-many --scp` (subprocess — отдельный CLI слой, DevPlan 089 T14),
  для docker-модулей, ЗАМЕНЯЕТ group-based deploy
- Healthcheck: `docker inspect` → `State.Health.Status` (liveness) или `healthcheck.sh MODE=deep`
- Content-hash skip (status-page, backup-cron): `content_hash.py` кэширует SHA256 исходников → пропускает `docker compose build` при совпадении

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
| content-hash | `state_machine._step_hash()` (Python) | SHA256 content hash per sub-step (legacy grouped phases φ1-φ5, φ7, φ12). Волна 117 D5: execute_grouped_phase удалён — фазы выполняются целиком; идемпотентность через phase-статусы (done / done_with_warnings ≠ done → перевыполнение). |
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

**Сброс:** `rm /var/lib/platform/.bootstrap/state.json` или `make bootstrap-node ... --force` → следующий bootstrap будет полным.

---

## Артефакты

| Путь | Содержимое | Доставка |
|------|-----------|----------|
| `/opt/platform/core/` | core/ файлы (entrypoints, internal, lib, modules) | SCP/rsync push (core-deploy CI) |
| `/opt/\<context\>/platform/` | Context-overlay (ayaml, node-configs, кастомизации) | git clone/pull (ensure_context_repo()) |
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

**Повторный запуск после частичного отказа (волна 117 D5):**
execute_grouped_phase (sub-step resume) удалён — фазы выполняются целиком. При повторном запуске
`run_init_mode()`/`run_update_mode()` пропускают done-фазы через `execute_phase()`; фазы со статусом
`done_with_warnings` (non-fatal issues, phase вернула False) НЕ считаются done и перевыполняются.
Если φ4 частично провалилась (decrypt-secrets OK, ensure-passwords FAIL), фаза перевыполняется
целиком, без повторного ввода age-passphrase.

### SSL Cert Lifecycle Unification (DevPlan 052)

**Мотивация:** Bug-репорт 2026-07-25: сертификаты выпускались и сайты работали, но при повторном bootstrap не восстанавливались из S3. Корневая причина: (а) `_ssl_provision()` не прокидывала S3_* креды в subprocess — s3-ssl-cache.sh upload/download молча падали; (б) cert_orchestrator пропускал platform domain (cert на диске) — upload в S3 не вызывался.

**Архитектура (DevPlan 052 Phase 1-3):**

| Компонент | Назначение | Взаимодействие |
|-----------|-----------|----------------|
| `s3_ssl_cache.py` (NEW) | Python-порт legacy shell s3-ssl-cache — upload/download/check/bulk-restore через boto3 | Прямой импорт в cert_orchestrator.py (без subprocess). os.environ доступ решает проблему credential propagation. |
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
| `deploy-modules.sh` | 1664 | 50 | 97% |
| `converge.sh` | 1149 | 137 | 88% |
| `node-lifecycle.sh` | 1301 | 164 | 87% |
| `remote-cmd.sh` | 266 | 60 | 77% |
| `scp-deliver.sh` | 251 | ≤60 | 76% (DevPlan 108 — rsync-фазы → core_deliverer.py) |
| `build-ssh-cmd.sh` | — | ~100 | — (извлечение из remote-cmd.sh, DevPlan 101 D1) |
| **Итого** | **4631** | **571** | **88%** |

Inline `python3 -c` / `<<PYEOF` в фасадах: 0 (было 31 в топ-3). Все inline-блоки мигрированы в Python-функции с unit-тестами.

**Python-оркестрация (DevPlan 101):** `remote_executor.py` (~200 LOC) — полный цикл удалённой команды (resolve → VPS self-SSH detect → sync-core → ssh_exec, exit 0/1/2/124, DRY_RUN). Shell-фасад `remote-cmd.sh` — тонкие обёртки: `build_*_ssh_cmd` (printf %q, D3, в `build-ssh-cmd.sh`) → `python3 -m core.internal.bootstrap.remote_executor execute-*` → `return $?`.

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
- `tests/unit/test_remote_executor.py` (NEW — DevPlan 101)
- `tests/unit/test_core_deliverer.py` (NEW — DevPlan 108)

---

## Cross-references

| Файл | Назначение |
|------|-----------|
| [`../../../core/AGENTS.md`](../../AGENTS.md) | Канонические операции, структура слоёв, forbidden-списки |
| `../../../AGENTS.md` (root) | Архитектурные инварианты, модель деплоя, dual delivery |
| [`../../../core/entrypoint-manifest.yaml`](../../entrypoint-manifest.yaml) | YAML-реестр операций (bootstrap-node в секции bootstrap) |
