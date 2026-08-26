<!-- GREP_SUMMARY: AGENTS.md, bootstrap, deploy-modules, orchestration, system, docker, idempotent -->

# GREP_SUMMARY: AGENTS.md, bootstrap, deploy-modules, orchestration, phases, 14-phases, state-machine, runbook, new-vps
# STRUCTURE: ┌14 consolidated phases┐ → ◇ BootstrapPhase enum → ◇ _phase_dependency_graph → ◇ precondition_check → ◇ grouped-phases (выполняются целиком) → ◇ deploy-modules (system|docker) → ◇ idempotence (state.json status + phase-input hash) → ◇ artifact paths → ⎋ cross-refs
# region MODULE_CONTRACT
## @purpose  Bootstrap pipeline orchestration: 14 consolidated phases, node setup, module deployment, healthcheck execution, idempotent state machine
## @scope    All scripts under core/internal/bootstrap/ — node-lifecycle.sh (thin facade),
##           lifecycle/state_machine.py (BootstrapPhase enum, _phase_dependency_graph,
##           precondition_check — оркестрация), lifecycle/state_store.py (persistence),
##           lifecycle/cli.py (CLI/main), lifecycle/helpers/ (7 I/O-модулей),
##           lifecycle/phases/ (14 phase implementations — пакет),
##           deploy-modules, setup-node, install-docker, install-tor-proxy, firewall,
##           topo_sort, discover_modules, remote-cmd, scp-deliver,
##           core_deliverer.py (Python Core-канал: mkdir + 5 rsync фаз)
## @invariants
##   1. node-lifecycle.sh — тонкий фасад (<80 LOC), делегирует всё lifecycle/cli.py. Режимы: --mode init (9 INIT фаз) и --mode update (5 UPDATE фаз).
##   2. state_machine.py — оркестрация: BootstrapPhase enum, _phase_dependency_graph, precondition_check(), execute_phase()
##      (execute_grouped_phase удалён — sub-step resume вне скоупа)
##   3. phases/ — business logic: 14 phase_*() функций в доменных модулях (system/docker/secrets/certs),
##      вызываемых из state_machine.py; агрегатор phases/__init__.py re-export'ит API;
##      I/O-хелперы — lifecycle/helpers/ (односторонняя зависимость state_machine → phases → helpers)
##   4. checkpoint_migration.py — удалён. Все чекпоинты через state.json напрямую.
##      (23→14 key migration — cold start only.)
##   5. Идемпотентность: state.json с 14 phase-ключами (StepState: status + phase-input hash)
##   6. Persistence (StepState/BootstrapState + state.json I/O) — lifecycle/state_store.py;
##      CLI (build_parser/main/run_init_mode/run_update_mode) — lifecycle/cli.py
##   7. Артефакты: /opt/platform/core/ (core), /opt/\<context\>/platform/ (context-overlay)
##   8. Никаких git-операций в bootstrap — только SCP/rsync для core; git clone/pull только через ensure_context_repo() для context-overlay
## @rationale Consolidate 32+ steps → 14 phases with explicit dependency graph. Eliminates 8 silent
##            failure propagation points via precondition BLOCKS. Sub-checkpoints удалены — фазы
##            выполняются ЦЕЛИКОМ; частичный отказ даёт done_with_warnings (≠ done → фаза
##            перевыполняется при следующем run). SRP-декомпозиция state_machine.
# endregion MODULE_CONTRACT

# AGENTS.md — core/internal/bootstrap/

---

## Bootstrap pipeline (14 consolidated phases)

```
node-lifecycle.sh --mode init  →  lifecycle/cli.py → state_machine.py (BootstrapPhase enum)

  φ1  system-bootstrap     # packages, python3.14+deps, docker-install, tor-proxy, firewall
  φ2  user-accounts        # ssh-access, platform-user, ci-deploy-user, projects-base
  φ3  platform-setup       # platform-dirs, docker-config, metrics-cron
  φ4  secrets-provision    # decrypt-secrets, ensure + autogen master-кредов (первый bootstrap), htpasswd (BLOCKS φ6)
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

**Группировка:** φ1-φ5, φ7, φ12 — grouped phases. Sub-checkpoints (sub-step resume) удалены —
фазы выполняются ЦЕЛИКОМ, без per-sub-step done/hash в state.json.
При перезапуске: done-фазы SKIP (status в state.json), `done_with_warnings` ≠ done →
перевыполняется; content-hash инвалидация на уровне фазы (deploy/converge-фазы
перевыполняются при изменении входов).
**Precondition BLOCKS:** φ4→φ6→φ8 — если φ4 не выполнен (нет секретов), φ6 (registry-auth) и φ8 (deploy-services) блокируются с читаемой ошибкой.
**Dependency graph:** см. `state_machine.py._phase_dependency_graph`.
**CLI:** `lifecycle/cli.py` (build_parser/main/run_init_mode/run_update_mode); persistence — `lifecycle/state_store.py`; I/O — `lifecycle/helpers/`.

⚠️ TRAP[BUG] · — · P0 · FALSE DIAGNOSIS: webnames.ru zone_manager_unavailable ≠ DNS-01 broken
· Symptom: webnames.ru API возвращает `{"result":"ERROR","details":"zone_manager_unavailable"}` для `domains_list`.
· Reality: TXT record add/delete работают (`{"result":"OK","details":1}`); `zone_manager_unavailable` затрагивает ТОЛЬКО listing endpoint.
· Root: прежние отказы issue вызывались rate-limit Let's Encrypt (50 certs/domain/week), НЕ DNS API.
· Prevention: НЕ отключать DNS-01 и НЕ переключаться на HTTP-01 на основе `domains_list` ошибки — сначала проверить add/delete.
· Test: `curl "https://www.webnames.ru/scripts/json_domain_zone_manager.pl?apikey=$KEY&domain=$DOM&type=TXT&record=_acme-challenge.test:test123&action=add"`
· Rev: если add/delete тоже возвращают zone_manager_unavailable → DNS-01 действительно сломан, применим HTTP-01 fallback.

**Вызов:** Только через `node-lifecycle.sh --mode init` или `node-lifecycle.sh --mode update`. Никогда напрямую.

---

## Режимы работы

`node-lifecycle.sh` поддерживает два режима, выбираемых через первый аргумент `--mode`:

### `--mode init` — полный bootstrap

Выполняет 9 фаз инициализации bare VPS: φ1 system-bootstrap (root, apt, Python 3.14, Docker, Tor, firewall) → φ2 user-accounts (platform/ci-deploy users, SSH keys) → φ3 platform-setup (Docker Hub auth, setup-node/sudoers, metrics-cron → /etc/cron.d/platform-metrics) → φ4 secrets-provision (decrypt, ensure-secrets + autogen master-кредов при первом bootstrap + htpasswd) → φ5 node-configuration (node.yaml validation, core verification) → φ6 registry-auth (ghcr.io, Docker auth) → φ7 certificates (acme.sh, SSL) → φ8 deploy-services (deploy-modules, deploy-context) → φ8.5 converge-services (converge). Идемпотентен: done-фазы пропускаются по status в state.json; content-hash инвалидация на уровне фазы. Вызывается из `make bootstrap-node` через `core/entrypoints/bootstrap.sh`.

### Python runtime на ноде

- **Цель:** голый `python3` после φ1 = Python **3.14** (deadsnakes PPA, Ubuntu 24.04).
- Механизм: `python_deps.py ensure --core-dir <core_dir>` вызывается в φ1 (шаг 1.5, FATAL):
  `DEBIAN_FRONTEND=noninteractive` apt-установка `software-properties-common` → `add-apt-repository -y ppa:deadsnakes/ppa` → `apt-get update` → `python3.14 python3.14-venv` → `python3.14 -m ensurepip --upgrade` → симлинк `/usr/local/bin/python3 → /usr/bin/python3.14` (PATH: /usr/local/bin раньше /usr/bin).
- **Системный `/usr/bin/python3` (3.12) НЕ трогается** — на нём висят cloud-init и системные утилиты. update-alternatives для python3 запрещён.
- Идемпотентность: маркер `/var/lib/platform/.bootstrap/python-deps.hash` содержит (хеш requirements.txt + версию python) — повторный бутстрап = no-op; старый маркер (только хеш, эра 3.12) трактуется как mismatch → переустановка.
- Зависимости ставятся через `/usr/local/bin/python3 -m pip install -r requirements.txt --break-system-packages` (PEP 668 — deadsnakes 3.14 тоже externally-managed).
- Не-Ubuntu 24.04 → WARN + fallback на system `python3-pip` (старый путь, без 3.14).

### `--mode update` — инкрементальный update

Выполняет 5 фаз обновления на уже забутстрапленной ноде: φ9 secrets-update → φ10 node-config-update → φ11 registry-update → φ12 deploy-update → φ13 converge-update. Каждая фаза реализована в phases/ с precondition проверками и dependency graph. Оптимизирован для CI: ~5 мин вместо ~30 мин полного bootstrap. Вызывается из `make node-update` через `core/entrypoints/node-update.sh`.

---

## deploy-modules.sh — фасад + Python-оркестратор

`deploy-modules.sh` — тонкий shell-фасад (≤50 LOC): arg parsing, root/NODE_YAML check,
network provision, docker login, затем `exec python3 deploy/deploy_orchestrator.py`.
Вся routing-логика (PARALLEL / ORCHESTRATOR / SEQUENTIAL), деплой модулей, sudoers,
orphan-реконсиляция и severity-based exit code {0,1,2} — в `deploy/deploy_orchestrator.py`
(импортирует `docker_orchestrator`, `secrets_validator`, `topo_sort`, `sudoers_generator`,
`orphan_reconciler`, `context_overlay`, `spool_validator` нативно — без subprocess).

### Типы модулей (из node.yaml)
- **system-модули** — `invoke_module_interface <name> install` (install.sh в директории модуля),
  затем healthcheck liveness (best-effort). Примеры: platform-secrets (systemd oneshot)
- **docker-модули** — Docker Compose через `deploy_docker_module()` / `deploy_docker_group()`.
  Примеры: postgres, redis, litellm, langfuse, hermes-agent, status-page, backup-cron, nginx (docker-модуль)

### Режимы деплоя (feature flag `DEPLOY_PARALLEL`, default=false)
- **Последовательный** (`DEPLOY_PARALLEL=false`, обратная совместимость): for-loop по enabled-модулям:
  check-env → detect-type → `deploy_docker_module()` | `invoke_module_interface install`
- **Параллельный** (`DEPLOY_PARALLEL=true`): `topo_sort` (Kahn по depends_on) →
  pre-pull (parallel_limit=4) → batch-check-env → итерация по topo-группам →
  `deploy_docker_group()` (os.fork per module, content-hash skip для build-модулей) →
  system-модули sequential → маркер `/var/lib/platform/.bootstrap/.hc_done_in_deploy` (healthcheck уже выполнен внутри группы)
- **DeployOrchestrator** (`DEPLOY_ORCHESTRATOR=true` + `DEPLOY_PARALLEL=true`):
  `orchestrator_cli.py deploy-many --scp` (subprocess — отдельный CLI слой),
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
| content-hash | `state_machine._phase_input_hash()` + `phase_needs_rerun()` | Phase-input hash для hash-инвалидации (deploy/converge-фазы; фазы без hash — done сохраняется). Фазы выполняются целиком; идемпотентность через phase-статусы (done / done_with_warnings ≠ done → перевыполнение). |

**Пример:** `system_bootstrap` в state.json:
```json
{
  "name": "system_bootstrap",
  "status": "done",
  "hash": "abc123"
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

### Lifecycle State Machine — dependency rules (код: `lifecycle/state_machine.py`)

- φ6 (registry-auth) requires φ4 (secrets); φ8 (deploy-services) requires φ4+φ6+φ7;
  φ8.5 requires φ8; φ12 requires φ9+φ11; φ13 requires φ12.
- Preconditions (per-phase, `precondition_check()`): φ1 root/apt; φ4 AGE_SECRET_KEY или
  /etc/age/key.txt; φ5 NODE_YAML; φ8/φ12 Docker daemon; φ6 GHCR_PULL_TOKEN (warning only).
- Повторный запуск после частичного отказа: done-фазы SKIP; `done_with_warnings` ≠ done →
  перевыполнение целиком (sub-step resume удалён).

### SSL Cert Lifecycle + DNS-провайдеры (кратко; код — bootstrap/cert_orchestrator.py)

- **Restore-first:** `_process_single_domain()` → disk check → S3 check → issue_cert.py;
  upload-on-skip (существующий сертификат → S3), `--reloadcmd`/`--renew-hook` — S3-синхронизация
  при каждом cron renewal (s3_ssl_cache.py, прямой импорт — credential propagation без subprocess).
- **Реестр провайдеров:** `certs-providers.yaml` (SoT: name/plugin/mode/creds) +
  `provider_registry.py` (longest-suffix per-domain resolve; strict creds allowlist);
  node.yaml: `acme_dns_plugin` (single) + `acme_dns_plugins: {domain: provider}`.
  Неизвестное имя провайдера → `ConfigValidationError(4)` (fail-fast, не тихий fallback).
- ⚠️ TRAP[DECISION] · — · regru — env-passthrough (renew через cron требует кредов в account.conf);
  reg.ru API требует IP ноды в панели (`ACCESS_DENIED_FROM_IP`) · Rev: токен-API reg.ru → inject+shred.
- **Unit-тесты:** tests/unit/test_{docker_orchestrator,sudoers_generator,context_overlay,
  secrets_validator,orphan_reconciler,reconciler,state_machine,s3_ssl_cache,remote_executor,
  core_deliverer}.py.

## Runbook: новый VPS с нуля

Каноническая процедура пересоздания ноды (инвариант 9 root: тестовый сервер пересоздаётся
заново). Все команды — с dev-машины оператора.

**0. Провайдер:** Ubuntu 24.04 (канон φ1), ≥2 vCPU/8GB RAM, ≥50GB SSD, порты 22/80/443.

**1. DNS:** A/AAAA на IP ноды для платформенного и проектных доменов (заранее — TTL к шагу 5).

**2. node.yaml** (`node-configs/<NODE>/node.yaml`, приватный sops-репо): contexts[].name
(context = org), node.{name,host,owner_key,timezone: UTC}, domain, email, modules[], projects[].
Timezone — канон UTC (backup-cron crontab 03:00-05:00 UTC); не переключать на MSK «молча».
Валидация: schema-проверка NodeYaml при bootstrap/converge; placement.yaml (multi-node) валидируется fail-fast `validate_topology` при деплое (`deploy_orchestrator._placement_for_node`).

**3. sops/secrets:** `node-configs/secrets/<NODE>.enc.yaml` (sops/age). Цепочка детекции ключа —
node_detect.py (env → SOPS_AGE_KEY → FILE → ~/.config/age/keys.txt → /etc/age/key.txt restore-first).
Проверка: `make secrets-unlock NODE=<NODE>`. Plaintext-ключ вне ноды — только в защищённом месте.
Dev-машина без /opt: bare-NODE резолвится в репо `node-configs/<NODE>/secrets/<NODE>.enc.yaml`
(plan 012 T18/F-013).

**4. Bootstrap (one-command, plan 012):** `make bootstrap-node NODE=<NODE> AGE_SECRET_KEY_FILE=~/.config/age/keys.txt`
(dry-run: DRY_RUN=1). 9 INIT фаз (~30 мин), идемпотентен (повтор = no-op). Python 3.14 на ноде —
deadsnakes PPA (φ1, FATAL); системный /usr/bin/python3 НЕ трогается. Без единого ручного обхода:
- **strict-init (T9)**: init-режим failed≠∅/crit>0 → exit 2 + state=failed (resumable — повтор доводит);
  update-режим (φ12) сохраняет WARN→0 (DEPLOY_BEST_EFFORT, D2).
- **Auto-inject ci_default (T3)**: optional+ci_default ключ, отсутствующий в матрице → дописан в
  secrets.env с `# auto-injected ci_default (plan 012)` + WARN; required/generated missing → FAIL
  со списком ДО φ8. Parity-гейт `${VAR:?}` ↔ SoT (test_gate_compose_interpolation_sot).
- **Interpolation dry-run (T10)**: перед деплоем каждой группы `docker compose config --quiet`
  с собранным env — первый unsatisfied `${VAR:?}` → FAIL со списком модулей ДО контейнеров.
- **Post-bootstrap report (T17)**: после φ8.5 печатается summary (deployed/failed, TLS,
  awaiting_projects, 3 next commands; JSON при REPORT_JSON=1).

**5. Verify:** `make check-security NODE=<NODE>` (S1-S9) → `make e2e-verify NODE=<NODE>`
(HTTP+TLS sweep; ожидает ответ только от exposed-доменов — F-034) → `make converge NODE=<NODE>`.

**6. Пост-bootstrap аудит:** registry-mirrors — если docker.io covered (кредами) → удалить
mirrors из daemon.json (mirror.gcr.io даёт 404 на docker.io-пулах); fstab — убрать
`nobarrier,commit=30` → `defaults/relatime,commit=5` (weekly fstrim остаётся).

**7. Операционные проверки:** квартальный DR-drill на временном VPS (restore postgres из S3 +
checksum-сверка); chaos-сьют T1-T12 — только test-node
(`PYTEST_NO_ESCALATION=1 pytest tests/e2e/test_chaos_resilience.py -m chaos`).

## Cross-references

| Файл | Назначение |
|------|-----------|
| [`../../../core/AGENTS.md`](../../AGENTS.md) | Канонические операции, структура слоёв |
| `../../../AGENTS.md` (root) | Архитектурные инварианты, модель деплоя, dual delivery |
| [`../../entrypoint-manifest.yaml`](../../entrypoint-manifest.yaml) | YAML-реестр операций (bootstrap-node в секции bootstrap) |
| `core/AGENTS.md` §«DR мастер-ключа AGE» | AGE master key DR (цепочка детекции, off-node backup, restore) |
| `lifecycle/state_machine.py` | 14 фаз, dependency graph, precondition_check |
| `lifecycle/phases/system.py` | φ1 timezone, zram |
| `core/internal/shared/docker_auth.py` | GHCR/Docker Hub auth |
| `core/schemas/node.schema.json` | node.yaml schema |
| `tests/e2e/test_chaos_resilience.py` | Chaos-сьют T1-T12 |
