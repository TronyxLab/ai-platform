# GREP_SUMMARY: ci-secrets-rotation, runbook, rotation, vps_ci_root, VPS_SSH_KEY, CI_DEPLOY_KEY, platform_personal_cicd, MIRROR_SSH_KEY, GIT_MIRROR_TOKEN, GITHUB_TOKEN, GHCR_OWNER, GHCR_PULL_TOKEN, GHCR_PUSH_TOKEN, DOCKER_HUB_TOKEN, TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID, AGE_SECRET_KEY, rollback, checklists
# STRUCTURE: ┌MODULE_CONTRACT┐ → ◇ матрица ключей/секретов → ◇ процедуры ротации (чек-листы per-key) → ◇ откат N дней → ◇ T7.7 grep-гейт → ⎋ cross-refs
# region MODULE_CONTRACT
## @purpose  Runbook ротации CI-ключей и секретов платформы ai-platform: единый канон
##           «что за секрет, где используется, как ротировать, как откатить». Закрывает
##           «знание в голове» (DevPlan 136 W7 T7.3) — ротация VPS_SSH_KEY/CI_DEPLOY_KEY
##           была непрозрачной, MIRROR_SSH_KEY/GHCR_OWNER/TELEGRAM_* не упоминались в docs.
## @scope    Все секреты CI-канала и узла: SSH-ключи (vps_ci_root, platform_personal_cicd,
##           mirror), GitHub Secrets, Docker Hub, GHCR, Telegram, AGE мастер-ключ.
##           НЕ содержит реальных значений — только имена и процедуры.
## @invariants
##   1. НИКАКИХ реальных значений ключей/токенов в этом документе и в отчётах — только имена и процедуры.
##   2. Авторитетный инвентарь секретов — core/secret-definitions.yaml (SSoT); consumers вычисляются generate_secrets_manifest.py.
##   3. Ротация SSH-ключей — двухключевой переход (add new → verify → remove old), НЕ одномоментная замена.
##   4. Откат окно N=30 дней: старый ключ/значение хранится в защищённом месте 30 дней после ротации.
##   5. GITHUB_TOKEN — авто-провижинится GitHub Actions, ручная ротация НЕ требуется.
## @rationale DevPlan 136 §4.2 (Security): «CI-ключи без runbook = знание в голове». Матрица
##            собирает фактические имена секретов из репозитория (grep .github/ makefiles/ core/)
##            и документирует процедуры, которых ранее не существовало (D14: «фикс новый CI-root
##            ключ vps_ci_root + секреты», покрытие ops — W7 runbook).
## @changes  2026-08-05 · DevPlan 136 W7 T7.3 — создан; T7.7 — grep-гейт имён секретов
## @links    core/secret-definitions.yaml (инвентарь), docs/coverage-matrix-d1-d23.md (D14),
##           .github/actions/setup-ssh/action.yml (единый SSH setup), AGENTS.md §Модель деплоя
# endregion MODULE_CONTRACT

# CI Secrets Rotation — Runbook

> Канонический runbook ротации CI-ключей и секретов. Применяется при подозрении на утечку,
> плановой ротации (каждые 90 дней — рекомендуется) или замене VPS (D14). Реальные значения
> ключей НЕ фиксируются нигде в репозитории — только в GitHub Secrets / на ноде.

---

## 1. Матрица ключей и секретов

| Секрет (имя) | Идентичность ключа | Роль | Потребители (файлы) | Хранение | Триггер ротации |
|--------------|--------------------|------|---------------------|----------|-----------------|
| `VPS_SSH_KEY` | SSH-пара `vps_ci_root` (приватный ключ CI; публичный — в `node.yaml#node.owner_key`/`ci_deploy_key`) | root-rsync `core/` + `node-configs/` на VPS (Core-канал) | `.github/workflows/core-deploy.yml`, `.github/actions/setup-ssh/action.yml` | GitHub Secrets (Tronyx161 repo + TronyxLab org) | новая VPS (D14), утечка, плановая 90д |
| `CI_DEPLOY_KEY` | SSH-пара `platform_personal_cicd` (forced-command `receive`; `command="…orchestrator_cli dispatch"`) | деплой проекта через SSH forced-command; repo-level deploy key ×N проектов | `.github/workflows/deploy-project.yml`, `.github/actions/setup-ssh/action.yml`, `core/internal/bootstrap/build-ssh-cmd.sh`, `core/internal/scaffold/project_scaffolder.py` | GitHub Secrets + repo-level deploy key в каждом репо проекта | утечка ключа проекта, плановая 90д |
| `SSH_KEY` / `SSH_HOST` | ≡ `CI_DEPLOY_KEY` (workflow_call) | проброс ключа в context-деплой | `platform-deploy.yml` (workflow_call secrets) | GitHub Secrets | синхронно с `CI_DEPLOY_KEY` |
| `MIRROR_SSH_KEY` | user-ключ `github-actions` (публичный — `.github/mirror-deploy-key.pub`) | push mirror Tronyx161→TronyxLab (`mirror.yml`) | `.github/workflows/mirror.yml`, `.github/actions/setup-ssh/action.yml` | GitHub Secrets | утечка, плановая 90д |
| `GITHUB_TOKEN` | авто-токен GitHub Actions | API-вызовы (sha-resolve verify, gh api) | `.github/actions/sha-resolve/action.yml` | auto (не хранится) | авто — ручная ротация не требуется |
| `GIT_MIRROR_TOKEN` | PAT (HTTPS) — **DEPRECATED** для mirror (2026-07-23) | HTTPS fallback в context-promote | `core/entrypoints/context-promote.sh`, `core/internal/deploy/context_promoter.py` | GitHub Secrets | при полном переходе на SSH — отозвать |
| `DOCKER_HUB_USERNAME` / `DOCKER_HUB_TOKEN` | Docker Hub учётка CI | pull-лимит-обход в CI + docker auth на ноде | `.github/workflows/platform-test.yml`, `core/internal/bootstrap/docker_registry_auth.py`, `core/internal/shared/docker_auth.py` | GitHub Secrets + node secrets.env | утечка, плановая 90д |
| `GHCR_PULL_TOKEN` | fine-grained PAT `read:packages` (все orgs) | pull ghcr.io образов на ноде | node secrets.env (sops), `docker_auth.py`, lifecycle phases | node sops-secret (НЕ GH Secret) | утечка, плановая 90д |
| `GHCR_PUSH_TOKEN` | fine-grained PAT `write:packages` | ручной push L2 (CI использует GITHUB_TOKEN) | manual `hermes-push-l2` | GitHub Secrets (optional) | утечка |
| `GHCR_OWNER` | производное значение: `github.repository_owner` (lowercase) | ghcr.io путь образов (`ghcr.io/${GHCR_OWNER}/…`) | `.github/workflows/build-platform.yml`, `makefiles/deploy.mk` | derived (не секрет) | N/A — org-имя, не ключ |
| `TELEGRAM_BOT_TOKEN` | BotFather-токен (`digits:alphanumeric`) | нотификации: healthcheck, deploy, hermes-agent, alerting | `core/internal/shared/telegram_notifier.py`, `core/internal/notify/notify-hook.sh`, `core/modules/hermes-agent/`, `core/modules/monitoring/config/alerting/contact-points.yml` | node secrets.env (sops) + GitHub Secrets (если CI-нотификации) | компрометация бота |
| `TELEGRAM_CHAT_ID` (+ `_WARNING`, `_CRITICAL`) | Telegram chat-идентификаторы | маршрутизация alerting по severity | мониторинг alerting, telegram_notifier | node secrets.env (sops) | смена чата/канала |
| `TELEGRAM_PROXY_URL` / `TELEGRAM_API_BASE` | proxy/API override | tor-обход для Telegram (SPOF-митигация D-2) | telegram_notifier, tor-proxy-healthcheck | node secrets.env (sops) | смена proxy |
| `TELEGRAM_ALLOWED_USERS` / `TELEGRAM_GETME_URL` | allowlist пользователей | доступ к hermes-agent командам | hermes-agent | node secrets.env (sops) | кадровые изменения |
| `AGE_SECRET_KEY` | AGE мастер-ключ (`AGE-SECRET-KEY-…`) | расшифровка SOPS-файлов на ноде; без него platform-secrets не стартует | `core/internal/secrets/decrypt_secrets.py`, `core/modules/platform-secrets/`, node-lifecycle, secrets.sh | нода (sops-ключ); DR — `docs/age-master-key-dr.md` (W12) | утечка, потеря ключа (см. §4) |
| `VPS_HOST` / `NODE_HOST_MAP` | host/JSON-маппинг | target адреса rsync/deploy | core-deploy.yml, deploy-project.yml | GitHub Secrets / org variable | смена IP ноды |

> Все имена собраны grep-ом по `.github/` `makefiles/` `core/` — см. §5 (T7.7 grep-гейт).

---

## 2. Процедуры ротации (чек-листы)

### 2.1 `VPS_SSH_KEY` (vps_ci_root) — root-доступ CI к VPS

**Триггер:** новая VPS (D14), подозрение на утечку, плановая ротация (90 дней).

**Чек-лист (двухключевой переход):**
- [ ] 1. Сгенерировать новую пару: `ssh-keygen -t ed25519 -C "vps_ci_root-$(date +%Y%m%d)" -f /tmp/vps_ci_root`
- [ ] 2. Добавить **новый** публичный ключ в `node.yaml#node.owner_key` (и `ci_deploy_key`, если root-ключ используется для forced-command) — через `make project-sync-env`/bootstrap rsync
- [ ] 3. Добавить новый приватный ключ в GitHub Secrets `VPS_SSH_KEY` (Tronyx161 repo + TronyxLab org)
- [ ] 4. Проверить новый канал: `make check-security NODE=<n>` (или `make converge NODE=<n>`) — rsync работает с новым ключом
- [ ] 5. Удалить старый ключ из `authorized_keys` ноды (пользователь ci-deploy / root)
- [ ] 6. Удалить старый приватный ключ из GitHub Secrets
- [ ] 7. Сохранить старый приватный ключ в защищённом месте на 30 дней (окно отката, §3)
- [ ] 8. Зафиксировать в audit: `write_audit_entry(tag="ci-secret:rotate", …)` / тикет

**Не делать:** одномоментная замена без проверки (риск: CI теряет доступ к ноде — D14 инцидент-класс).

### 2.2 `CI_DEPLOY_KEY` (platform_personal_cicd) — forced-command деплой ×N репо

**Особенность:** ключ действует на каждом проекте как repo-level deploy key (×N репозиториев). Публичная часть уже в `node.yaml#node.ci_deploy_key` (forced-command `command=` prefix).

**Чек-лист:**
- [ ] 1. `ssh-keygen -t ed25519 -C "platform_personal_cicd-$(date +%Y%m%d)" -f /tmp/ci_deploy`
- [ ] 2. Добавить новый публичный ключ в **каждый** репозиторий проекта (Settings → Deploy keys) с read-доступом
- [ ] 3. Обновить `node.yaml#node.ci_deploy_key` (forced-command префикс генерируется setup-node.sh/φ2; публичный ключ — новый)
- [ ] 4. Обновить GitHub Secrets `CI_DEPLOY_KEY` (и `SSH_KEY` workflow_call, если используется)
- [ ] 5. Проверить канал: `make deploy-project PROJECT=<p> NODE=<n>` (forced-command `receive`)
- [ ] 6. Удалить старый ключ из всех репо проектов + GitHub Secrets
- [ ] 7. Старый ключ — в защищённое место на 30 дней (окно отката)

### 2.3 `MIRROR_SSH_KEY` (github-actions) — mirror push

**Чек-лист:**
- [ ] 1. `ssh-keygen -t ed25519 -C "github-actions-mirror-$(date +%Y%m%d)" -f /tmp/mirror_key`
- [ ] 2. Добавить публичный ключ к GitHub-аккаунту (user key) или к TronyxLab/ai-platform как deploy key; обновить `.github/mirror-deploy-key.pub`
- [ ] 3. Обновить GitHub Secrets `MIRROR_SSH_KEY`
- [ ] 4. Проверить: `workflow_dispatch` mirror.yml → push + post-push verify (ретрай 10×10s, W7 T7.4)
- [ ] 5. Удалить старый ключ; окно отката 30 дней

### 2.4 `GITHUB_TOKEN` — авто

Ручная ротация **не требуется** — токен провижинится GitHub Actions на каждый job (permissions: contents/actions read). При утечке логов — только ограничить `permissions:` в воркфлоу.

### 2.5 `GIT_MIRROR_TOKEN` — deprecated

- [ ] Проверить: используется ли ещё HTTPS fallback в `context-promote.sh` (единственный потребитель)
- [ ] При полном переходе на SSH (`MIRROR_SSH_KEY` + `context-promote` SSH-primary) — отозвать PAT в GitHub (Settings → Developer settings → PAT) и удалить из Secrets
- [ ] До отзыва — держать как fallback; ротация PAT: пересоздать с теми же правами (repo read)

### 2.6 `DOCKER_HUB_USERNAME` / `DOCKER_HUB_TOKEN`

**Чек-лист:**
- [ ] 1. Docker Hub → Account Settings → Security → создать **новый** access token (Read-only)
- [ ] 2. Обновить GitHub Secrets `DOCKER_HUB_TOKEN` (+ username, если менялся)
- [ ] 3. Обновить node secrets (sops: `decrypt_secrets.py` + `docker_registry_auth.py` применяют на следующем bootstrap/up)
- [ ] 4. Проверить: CI-прогон platform-test (docker pulls), `make up` на ноде
- [ ] 5. Отозвать старый токен в Docker Hub; окно отката 30 дней

### 2.7 `GHCR_PULL_TOKEN` / `GHCR_PUSH_TOKEN` / `GHCR_OWNER`

- `GHCR_PULL_TOKEN` (node sops-secret): пересоздать fine-grained PAT `read:packages` на все orgs → обновить sops secrets.env → `make bootstrap-node`/`node-update`; проверка `docker pull ghcr.io/${GHCR_OWNER}/…`.
- `GHCR_PUSH_TOKEN` (CI): пересоздать `write:packages` PAT → GitHub Secrets; используется только для ручного L2 push.
- `GHCR_OWNER` — НЕ ключ: производное от `github.repository_owner` (lowercase). Ротация N/A; при смене org-имени обновляется автоматически (derived). Документирован здесь, т.к. отсутствовал в docs (T7.7).

### 2.8 `TELEGRAM_*` (BOT_TOKEN, CHAT_ID, CHAT_ID_WARNING, CHAT_ID_CRITICAL, PROXY_URL, ALLOWED_USERS)

**Чек-лист (ротация бота):**
- [ ] 1. `@BotFather` → `/newbot` → новый токен (или `/revoke` + `/token` для текущего)
- [ ] 2. Обновить node secrets.env (sops): `TELEGRAM_BOT_TOKEN` (и `CHAT_ID*` при смене канала)
- [ ] 3. Применить: `make node-update NODE=<n>` (φ9 secrets_update) или `make secrets-unlock` + перезапуск notify/hermes
- [ ] 4. Проверить: тестовая нотификация (`send_telegram`), Grafana alerting delivery (D-3 chain — см. Debt 126 D-3)
- [ ] 5. Отозвать старый токен в BotFather; окно отката 30 дней

### 2.9 `AGE_SECRET_KEY` — мастер-ключ

> Отдельная критичность: ключ расшифровывает ВСЕ sops-секреты ноды. DR-стратегия хранения
> и восстановления — `docs/age-master-key-dr.md` (DevPlan 136 W12, S-12).

**Чек-лист (ротация мастер-ключа):**
- [ ] 1. Сгенерировать новый ключ: `age-keygen -o /tmp/age-key-new.txt`
- [ ] 2. Перешифровать ВСЕ sops-файлы новым ключом (sops update-keys) — НЕ хранить оба ключа дольше окна миграции
- [ ] 3. Доставить новый ключ на ноду (SCP, НЕ git), обновить `AGE_SECRET_KEY` в окружении платформы
- [ ] 4. Проверить: `make secrets-unlock NODE=<n>` → расшифровка OK, platform-secrets стартует
- [ ] 5. Уничтожить старый ключ (shred) после окна отката; старый ключ в защищённом месте 30 дней
- [ ] 6. ⚠️ Потеря мастер-ключа = потеря секретов (восстановление только из DR-бэкапа, W12)

---

## 3. Откат (rollback window N=30 дней)

Принцип: **двухключевой переход** — старый ключ/значение НЕ уничтожается мгновенно, а хранится
в защищённом месте (password manager / age-encrypted файл) **30 дней** после ротации.

| Ключ | Как откатить (в окне 30 дней) |
|------|-------------------------------|
| `VPS_SSH_KEY` | Пере-добавить старый приватный ключ в GitHub Secrets; вернуть старый pub в `node.yaml`/authorized_keys → проверить `make converge` |
| `CI_DEPLOY_KEY` | Пере-добавить старый ключ в repo deploy keys ×N + GitHub Secrets → проверить `make deploy-project` |
| `MIRROR_SSH_KEY` | Вернуть старый ключ в Secrets + старый pub в аккаунт/TronyxLab → `workflow_dispatch` mirror |
| `DOCKER_HUB_TOKEN` | Вернуть старый token в Secrets + node secrets.env → docker auth проверка |
| `GHCR_PULL/PUSH_TOKEN` | Вернуть старый PAT (если не отозван) в sops/Secrets |
| `TELEGRAM_BOT_TOKEN` | Вернуть старый токен (если не revoked в BotFather) в secrets.env |
| `AGE_SECRET_KEY` | Вернуть старый ключ на ноду; sops-файлы расшифруются старым ключом (если не перешифрованы) |

**Правило:** окно 30 дней — жёсткий максимум. После него старые ключи уничтожаются
(секрет скомпрометирован или заменён — держать дольше = лишняя поверхность атаки).

---

## 4. Отдельные сценарии

- **Новая VPS (D14):** старый `VPS_SSH_KEY` не авторизуется на новом сервере → генерация
  `vps_ci_root` + обновление Secrets (Tronyx161 + TronyxLab) ДО bootstrap; покрытие ops —
  `docs/coverage-matrix-d1-d23.md` D14.
- **Утечка лога CI:** GITHUB_TOKEN не ротируется (auto); проверить `permissions:` воркфлоу;
  отозвать любые PAT, попавшие в логи (GHCR_PUSH_TOKEN и т.п.).
- **Потеря AGE мастер-ключа:** см. `docs/age-master-key-dr.md` (W12) — off-node encrypted backup,
  процедура восстановления, threat-model.

---

## 5. T7.7 — grep-гейт имён секретов (что не упоминалось в docs/)

До создания этого runbook следующие имена секретов **отсутствовали** в `docs/`
(проверено grep-ом по `docs/`; результат T7.7 DevPlan 136):

| Секрет | Статус до runbook | Теперь |
|--------|-------------------|--------|
| `GHCR_OWNER` | НЕ упоминался в docs/ | §2.7 (derived, не ключ) |
| `GIT_MIRROR_TOKEN` | НЕ упоминался в docs/ | §2.5 (deprecated) |
| `TELEGRAM_BOT_TOKEN` | НЕ упоминался в docs/ | §2.8 |
| `TELEGRAM_CHAT_ID` (+ `_WARNING`, `_CRITICAL`) | НЕ упоминался в docs/ | §2.8 |
| `TELEGRAM_PROXY_URL` / `TELEGRAM_API_BASE` / `TELEGRAM_ALLOWED_USERS` | НЕ упоминались в docs/ | §2.8 |
| `MIRROR_SSH_KEY` | НЕ упоминался в docs/ | §2.3 |
| `VPS_SSH_KEY` | только `docs/coverage-matrix-d1-d23.md` (D14, ops-обоснование) | §2.1 |
| `CI_DEPLOY_KEY` | только `docs/coverage-matrix-d1-d23.md` (D14) | §2.2 |
| `GITHUB_TOKEN` | упоминался в workflow-комментариях | §2.4 |
| `DOCKER_HUB_TOKEN` | упоминался в workflow-комментариях | §2.6 |

**Инвариант grep-гейта:** любой новый CI-секрет, добавляемый в `.github/`/`makefiles/`/`core/`,
должен попадать в матрицу §1 (или явно помечаться как auto/derived) — иначе секрет снова
оказывается «знанием в голове».

---

## Cross-references

| Файл | Назначение |
|------|-----------|
| `core/secret-definitions.yaml` | SSoT инвентаря секретов (tier/source/ci_default) |
| `core/secrets-manifest.yaml` | GENERATED-манифест с consumers |
| `.github/actions/setup-ssh/action.yml` | Единый SSH setup (id_rsa, ssh-keyscan) |
| `.github/mirror-deploy-key.pub` | Публичный ключ mirror |
| `docs/coverage-matrix-d1-d23.md` | D14 (vps_ci_root), D12 (CI_DEPLOY_KEY) |
| `docs/age-master-key-dr.md` | DR AGE мастер-ключа (DevPlan 136 W12, S-12) |
| `.ai/plans/136-bootstrap-hardening/04-Debt.md` | Реестр долгов W7 (в т.ч. ротация) |
