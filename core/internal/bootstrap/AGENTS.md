<!-- GREP_SUMMARY: AGENTS.md, bootstrap, deploy-modules, orchestration, system, docker, idempotent -->

# GREP_SUMMARY: AGENTS.md, bootstrap, deploy-modules, orchestration, system, docker, idempotent
# STRUCTURE: ┌bootstrap pipeline┐ → ◇ deploy-modules (system|docker branches) → ◇ idempotence (.done + content-hash) → ◇ artifact paths → ⎋ cross-refs
# region MODULE_CONTRACT
## @purpose  Bootstrap pipeline orchestration: node setup, module deployment, healthcheck execution
## @scope    All scripts under core/internal/bootstrap/ — node-lifecycle, deploy-modules, setup-node, install-docker, install-tor-proxy, firewall, _topo_sort, content-hash, discover_modules, remote-cmd, scp-deliver
## @invariants
##   1. node-lifecycle.sh — единственный entrypoint для bootstrap и node-update. Режимы: --mode init (полный bootstrap) и --mode update (инкрементальный update). setup-node.sh, deploy-modules.sh, firewall.sh, install-docker.sh, install-tor-proxy.sh вызываются ТОЛЬКО из node-lifecycle.sh.
##   2. deploy-modules.sh — две ветки: system (install.sh) и docker (_topo_sort + docker compose up)
##   3. Идемпотентность: .done-маркеры + per-step content-hash (content-hash.sh), не «просто повторный вызов»
##   4. Артефакты: /opt/platform/core/ (core), /opt/<context>/platform/ (context-overlay)
##   5. Никаких git-операций в bootstrap — только SCP/rsync для core; git clone/pull только через ensure_context_repo() для context-overlay
## @rationale Bootstrap — самая сложная подсистема платформы (deploy-modules.sh 560+ строк).
##            Агенты регулярно путают --modules фильтрацию, system vs docker ветки,
##            _topo_sort.py интеграцию, .done-маркеры. Единая документация сокращает ошибки.
# endregion MODULE_CONTRACT

# AGENTS.md — core/internal/bootstrap/

---

## Bootstrap pipeline

```
node-lifecycle.sh --mode init
├── 1. ssh-access           # SSH key distribution + access verification
├── 2. apt-deps             # System package dependencies
├── 3. [tor]                # Tor proxy (obfs4 bridges) for DPI bypass
├── 4. install-docker       # Docker CE installation
├── 5. user-platform        # platform system user
├── 6. user-ci-deploy       # ci-deploy user with ssh forced-command
├── 6b. projects-base       # /opt/projects base directory
├── 7. firewall             # Declarative ufw baseline
├── 8. verify-core          # Content hash verification of delivered core
├── 9. verify-node-configs  # Node config structural validation
├── 10. decrypt-secrets     # AGE-decrypt secrets from encrypted files
├── 12b. ensure-secrets     # Ensure secrets.env exists from decrypted files
├── 11. read-node-yaml      # Parse node.yaml for domain/acme/projects
├── 12. ghcr-auth           # GitHub Container Registry docker login
├── 13. sudoers             # Sudo whitelist generation
├── 13b. install-acme       # acme.sh installation (init only, via install-acme.sh)
├── 14 → node-lifecycle.sh --mode update  # provision → ssl → deploy → healthcheck
├── 16. audit-summary       # Post-init audit log
└── 17. telegram            # Notification hook

node-lifecycle.sh --mode update
├── 1. verify-core         # Content hash verification of delivered core
├── 2. provision            # Environment provision (networks + volumes)
├── 3. ssl-provision        # SSL certificate issuance via issue-cert.sh (acme.sh DNS-01)
├── 4. deploy docker        # Docker modules via deploy-modules.sh
├── 5. deploy system        # System modules via deploy-modules.sh
└── 6. healthcheck          # Per-module healthcheck after deploy
```

**Вызов:** Только через `node-lifecycle.sh --mode init` или `node-lifecycle.sh --mode update`. Никогда напрямую.

---

## Режимы работы

`node-lifecycle.sh` поддерживает два режима, выбираемых через первый аргумент `--mode`:

### `--mode init` — полный bootstrap

Выполняет 17 шагов инициализации bare VPS: проверка SSH → apt-зависимости → Tor (опционально) → Docker → пользователи (platform, ci-deploy) → UFW → верификация core + node-configs → decrypt secrets → node.yaml валидация → GHCR auth → sudoers → node-update (вызов `--mode update`) → audit-log → Telegram. Идемпотентен: при повторном запуске шаги с неизменившимся content-hash пропускаются. Вызывается из `make bootstrap-node` через `core/entrypoints/bootstrap.sh`.

### `--mode update` — инкрементальный update

Выполняет 6 шагов на уже забутстрапленной ноде: verify-core (content-hash) → provision (networks + volumes) → issue-cert.sh (acme.sh DNS-01 wildcard cert) → deploy docker modules → deploy system modules → healthcheck. Оптимизирован для CI: ~5 мин вместо ~30 мин полного bootstrap. Вызывается из `make node-update` через `core/entrypoints/node-update.sh`, а также из step-14 init-режима (post-init update).

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
- Пайплайн: `_topo_sort.py` (сортировка по depends_on) → `docker compose pull` → `docker compose up -d`
- Healthcheck: `docker inspect` → `State.Health.Status` (liveness) или `healthcheck.sh MODE=deep`
- Примеры: postgres, redis, litellm, langfuse, hermes-agent

### Фильтрация --modules
- `deploy-modules.sh --modules postgres,redis` → развернуть только указанные
- Без флага → все модули из `node.yaml`
- Фильтрация применяется ДО topo-sort — зависимости не резолвятся автоматически (зависимый модуль без зависимости = fail)

---

## Идемпотентность (.done + content-hash)

**Механизм:** `content-hash.sh` + `.done`-файлы в `/var/lib/platform/.bootstrap/`

| Механизм | Где | Что делает |
|----------|-----|------------|
| `.done`-маркер | `/var/lib/platform/.bootstrap/<step>.done` | Сигнализирует что шаг выполнен. Второй вызов = no-op |
| content-hash | `content-hash.sh` | Хеширует содержимое скрипта/конфига. Если хеш не изменился — шаг не перезапускается |

**Пример:** `install-docker.sh` создаёт `/var/lib/platform/.bootstrap/install-docker.done`. Повторный вызов видит маркер → no-op.

**Сброс:** `rm -rf /var/lib/platform/.bootstrap/` → следующий bootstrap будет полным.

---

## Артефакты

| Путь | Содержимое | Доставка |
|------|-----------|----------|
| `/opt/platform/core/` | core/ файлы (entrypoints, internal, lib, modules) | SCP/rsync push (core-deploy CI) |
| `/opt/<context>/platform/` | Context-overlay (ayaml, node-configs, кастомизации) | git clone/pull (ensure_context_repo()) |
| `/opt/platform/secrets/` | AGE-encrypted secrets | SCP (через decrypt-secrets.sh) |

---

## Cross-references

| Файл | Назначение |
|------|-----------|
| [`../../../core/AGENTS.md`](../../AGENTS.md) | Канонические операции, структура слоёв, forbidden-списки |
| [`../../../AGENTS.md`](../../../AGENTS.md) | Архитектурные инварианты, модель деплоя, dual delivery |
| [`../../../core/entrypoint-manifest.yaml`](../../entrypoint-manifest.yaml) | YAML-реестр операций (bootstrap-node в секции bootstrap) |
