#!/usr/bin/env python3
# GREP_SUMMARY: users-helpers, create-user, add-ssh-key, ensure-projects-base, useradd, authorized-keys, converge-r3, command-runner, DI, runner-param
# STRUCTURE: ▶ create_user ┌id check → useradd --system┐ → ⚡ add_ssh_key ┌authorized_keys append + chmod 0600┐ → ⚡ ensure_projects_base ┌/opt/projects + converge R3┐ → ⎋
# region MODULE_CONTRACT
## @purpose  User-management I/O-хелперы bootstrap-фаз (пользователи, SSH-ключи, projects base) —
##           извлечены из state_machine (B9 T1, U-08). Все функции публичные.
## @scope    users.py: create_user, add_ssh_key, ensure_projects_base.
##           Используются phases.py (φ2 user_accounts).
## @invariants
##   - create_user идемпотентен (id check перед useradd); системный пользователь с home
##   - add_ssh_key: duplicate-check по содержимому authorized_keys; forced-command префикс
##     для ci-deploy (orchestrator_cli dispatch — SSH_ORIGINAL_COMMAND-диспетчер;
##     единственный писатель ci-deploy ключа — этот модуль)
##   - ensure_projects_base: /opt/projects ownership ci-deploy + вызов converge R3 (non-fatal)
##   - Все subprocess через shared/subprocess_io.run_subprocess (единый канон, B4);
##     W4b: канал инкапсулирован в CommandRunner-параметр (runner=None → default_command_runner())
## @rationale Strangler-Fig: извлечение I/O из state_machine-монолита (DevPlan 116 B9 D1).
##            W4b (160 T4.2): runner-параметр убирает monkeypatch subprocess.run/
##            run_subprocess из тестов (fake-раннер с ассертами вместо патчей).
## @changes  2026-08-01 · Extracted from state_machine (B9 T1)
## @changes  2026-08-13 · DevPlan 160 W4b — +runner: CommandRunner | None = None (DI)
# endregion MODULE_CONTRACT


# region CI_SECRETS_ROTATION
## Runbook ротации CI-ключей и секретов (ПОЛНЫЙ, операционный) — бывш.
## ci-secrets-rotation.md (мигрирован Волной D DevPlan 164, каталог документации удалён).
## Единый канон «что за секрет, где используется, как ротировать, как откатить».
## Применяется при подозрении на утечку, плановой ротации
## (каждые 90 дней — рекомендуется) или замене VPS. Реальные значения ключей НЕ
## фиксируются в репозитории — только имена и процедуры (GitHub Secrets / на ноде).
## Авторитетный инвентарь секретов — core/secret-definitions.yaml (SSoT); consumers
## вычисляются generate_secrets_manifest.py. Ротация SSH-ключей — ДВУХКЛЮЧЕВОЙ переход
## (add new → verify → remove old), НЕ одномоментная замена. Откат-окно N=30 дней.
##
## §1. Матрица ключей и секретов
##      | Секрет | Идентичность | Роль | Потребители | Хранение | Триггер |
##      | VPS_SSH_KEY | SSH-пара vps_ci_root (приватный CI; публичный — node.yaml#node.owner_key/ci_deploy_key) | root-rsync core/ + node-configs/ на VPS (Core-канал) | .github/workflows/core-deploy.yml, .github/actions/setup-ssh/action.yml | GitHub Secrets (Tronyx161 repo + TronyxLab org) | новая VPS, утечка, плановая 90д |
##      | CI_DEPLOY_KEY | SSH-пара platform_personal_cicd (forced-command receive; command=…orchestrator_cli dispatch) | деплой проекта через SSH forced-command; repo-level deploy key ×N проектов | .github/workflows/deploy-project.yml, setup-ssh/action.yml, build-ssh-cmd.sh, project_scaffolder.py | GitHub Secrets + repo-level deploy key в каждом репо проекта | утечка, плановая 90д |
##      | SSH_KEY / SSH_HOST | ≡ CI_DEPLOY_KEY (workflow_call) | проброс ключа в context-деплой | platform-deploy.yml (workflow_call secrets) | GitHub Secrets | синхронно с CI_DEPLOY_KEY |
##      | MIRROR_SSH_KEY | user-ключ github-actions (публичный — .github/mirror-deploy-key.pub) | push mirror Tronyx161→TronyxLab (mirror.yml) | mirror.yml, setup-ssh/action.yml | GitHub Secrets | утечка, плановая 90д |
##      | GITHUB_TOKEN | авто-токен GitHub Actions | API-вызовы (sha-resolve verify, gh api) | sha-resolve/action.yml | auto (не хранится) | авто — ручная ротация НЕ требуется |
##      | GIT_MIRROR_TOKEN | PAT (HTTPS) — REMOVED (177 W2.1) | HTTPS fallback в context-promote удалён; mirror.yml — SSH-only (MIRROR_SSH_KEY) | — (отозван в GitHub Secrets) | GitHub Secrets (исторический) | отозван при полном переходе на SSH (2026-07-23) |
##      | DOCKER_HUB_USERNAME / DOCKER_HUB_TOKEN | Docker Hub учётка CI | pull-лимит-обход в CI + docker auth на ноде | platform-test.yml, docker_registry_auth.py, docker_auth.py | GitHub Secrets + node secrets.env | утечка, плановая 90д |
##      | GHCR_PULL_TOKEN | fine-grained PAT read:packages (все orgs) | pull ghcr.io образов на ноде | node secrets.env (sops), docker_auth.py, lifecycle phases | node sops-secret (НЕ GH Secret) | утечка, плановая 90д |
##      | GHCR_PUSH_TOKEN | fine-grained PAT write:packages | ручной push L2 (CI использует GITHUB_TOKEN) | manual hermes-push-l2 | GitHub Secrets (optional) | утечка |
##      | TELEGRAM_BOT_TOKEN | BotFather-токен (digits:alphanumeric) | нотификации: healthcheck, deploy, hermes-agent, alerting | telegram_notifier.py, notify-hook.sh, hermes-agent, alerting/contact-points.yml | node secrets.env (sops) + GitHub Secrets | компрометация бота |
##      | TELEGRAM_CHAT_ID (+ _WARNING, _CRITICAL) | Telegram chat-идентификаторы | маршрутизация alerting по severity | alerting, telegram_notifier | node secrets.env (sops) | смена чата/канала |
##      | TELEGRAM_PROXY_URL / TELEGRAM_API_BASE | proxy/API override | tor-обход для Telegram (SPOF-митигация D-2) | telegram_notifier, tor-proxy-healthcheck | node secrets.env (sops) | смена proxy |
##      | TELEGRAM_ALLOWED_USERS / TELEGRAM_GETME_URL | allowlist пользователей | доступ к hermes-agent командам | hermes-agent | node secrets.env (sops) | кадровые изменения |
##      | AGE_SECRET_KEY | AGE мастер-ключ (AGE-SECRET-KEY-…) | расшифровка SOPS-файлов на ноде; без него platform-secrets не стартует | decrypt_secrets.py, platform-secrets, node-lifecycle, secrets.sh | нода (sops-ключ); DR — DR_PROCEDURE в core/internal/deploy/age_key_backup.py | утечка, потеря ключа (§2.9) |
##      | VPS_HOST / NODE_HOST_MAP | host/JSON-маппинг | target адреса rsync/deploy | core-deploy.yml, deploy-project.yml | GitHub Secrets / org variable | смена IP ноды |
##
## §2. Процедуры ротации (чек-листы; SSH-ключи — двухключевой переход)
##      2.1 VPS_SSH_KEY (vps_ci_root) — root-доступ CI к VPS:
##        [ ] 1. ssh-keygen -t ed25519 -C "vps_ci_root-$(date +%Y%m%d)" -f /tmp/vps_ci_root
##        [ ] 2. Добавить НОВЫЙ публичный ключ в node.yaml#node.owner_key (и ci_deploy_key,
##                если root-ключ используется для forced-command) — make project-sync-env/bootstrap rsync
##        [ ] 3. Добавить новый приватный ключ в GitHub Secrets VPS_SSH_KEY (Tronyx161 + TronyxLab)
##        [ ] 4. Проверить новый канал: make check-security NODE={n} (или make converge NODE={n})
##        [ ] 5. Удалить старый ключ из authorized_keys ноды (пользователь ci-deploy / root)
##        [ ] 6. Удалить старый приватный ключ из GitHub Secrets
##        [ ] 7. Сохранить старый приватный ключ в защищённом месте на 30 дней (окно отката, §3)
##        [ ] 8. Зафиксировать в audit: write_audit_entry(tag="ci-secret:rotate", …) / тикет
##        НЕ делать: одномоментная замена без проверки (риск: CI теряет доступ к ноде).
##      2.2 CI_DEPLOY_KEY (platform_personal_cicd) — forced-command деплой ×N репо:
##        [ ] 1. ssh-keygen -t ed25519 -C "platform_personal_cicd-$(date +%Y%m%d)" -f /tmp/ci_deploy
##        [ ] 2. Добавить новый публичный ключ в КАЖДЫЙ репозиторий проекта (Settings → Deploy
##                keys) с read-доступом
##        [ ] 3. Обновить node.yaml#node.ci_deploy_key (forced-command префикс генерируется
##                setup-node.sh/φ2; публичный ключ — новый)
##        [ ] 4. Обновить GitHub Secrets CI_DEPLOY_KEY (и SSH_KEY workflow_call, если используется)
##        [ ] 5. Проверить канал: make deploy-project PROJECT={p} NODE={n} (forced-command receive)
##        [ ] 6. Удалить старый ключ из всех репо проектов + GitHub Secrets
##        [ ] 7. Старый ключ — в защищённое место на 30 дней (окно отката)
##      2.3 MIRROR_SSH_KEY (github-actions) — mirror push:
##        [ ] 1. ssh-keygen -t ed25519 -C "github-actions-mirror-$(date +%Y%m%d)" -f /tmp/mirror_key
##        [ ] 2. Добавить публичный ключ к GitHub-аккаунту (user key) или к TronyxLab/ai-platform
##                как deploy key; обновить .github/mirror-deploy-key.pub
##        [ ] 3. Обновить GitHub Secrets MIRROR_SSH_KEY
##        [ ] 4. Проверить: workflow_dispatch mirror.yml → push + post-push verify (ретрай 10×10s)
##        [ ] 5. Удалить старый ключ; окно отката 30 дней
##      2.4 GITHUB_TOKEN — авто: ручная ротация НЕ требуется (токен провижинится GitHub Actions
##        на каждый job, permissions: contents/actions read); при утечке логов — только
##        ограничить permissions: в воркфлоу.
##      2.5 GIT_MIRROR_TOKEN — REMOVED (177 W2.1, 2026-08-16): HTTPS fallback в context-promote
##        удалён (SSH-only канал), mirror.yml — SSH-only с 2026-07-23. Действие: отозвать PAT
##        (Settings → Developer settings → PAT) и удалить из Secrets; запись оставлена для
##        исторического аудита — НЕ возобновлять без Rev-условия (CI-driven context-promote).
##      2.6 DOCKER_HUB_USERNAME / DOCKER_HUB_TOKEN:
##        [ ] 1. Docker Hub → Account Settings → Security → создать НОВЫЙ access token (Read-only)
##        [ ] 2. Обновить GitHub Secrets DOCKER_HUB_TOKEN (+ username, если менялся)
##        [ ] 3. Обновить node secrets (sops: decrypt_secrets.py + docker_registry_auth.py
##                применяют на следующем bootstrap/up)
##        [ ] 4. Проверить: CI-прогон platform-test (docker pulls), make up на ноде
##        [ ] 5. Отозвать старый токен в Docker Hub; окно отката 30 дней
##      2.7 GHCR_PULL_TOKEN / GHCR_PUSH_TOKEN (GHCR_OWNER удалён DevPlan 002 — L1-образ не публикуется):
##        GHCR_PULL_TOKEN (node sops-secret): пересоздать fine-grained PAT read:packages на все
##        orgs → обновить sops secrets.env → make bootstrap-node/node-update; проверка
##        docker pull ghcr.io/${GHCR_OWNER}/….
##        GHCR_PUSH_TOKEN (CI): пересоздать write:packages PAT → GitHub Secrets; только для
##        ручного L2 push.
##      2.8 TELEGRAM_* (BOT_TOKEN, CHAT_ID, CHAT_ID_WARNING, CHAT_ID_CRITICAL, PROXY_URL,
##        API_BASE, ALLOWED_USERS):
##        [ ] 1. BotFather (telegram) → /newbot → новый токен (или /revoke + /token для текущего)
##        [ ] 2. Обновить node secrets.env (sops): TELEGRAM_BOT_TOKEN (и CHAT_ID* при смене канала)
##        [ ] 3. Применить: make node-update NODE={n} (φ9 secrets_update) или make secrets-unlock
##                + перезапуск notify/hermes
##        [ ] 4. Проверить: тестовая нотификация (send_telegram), Grafana alerting delivery
##        [ ] 5. Отозвать старый токен в BotFather; окно отката 30 дней
##      2.9 AGE_SECRET_KEY — мастер-ключ (отдельная критичность: расшифровывает ВСЕ sops-
##        секреты ноды; DR-стратегия — DR_PROCEDURE в core/internal/deploy/age_key_backup.py):
##        [ ] 1. Сгенерировать новый ключ: age-keygen -o /tmp/age-key-new.txt
##        [ ] 2. Перешифровать ВСЕ sops-файлы новым ключом (sops update-keys) — НЕ хранить оба
##                ключа дольше окна миграции
##        [ ] 3. Доставить новый ключ на ноду (SCP, НЕ git), обновить AGE_SECRET_KEY в окружении
##        [ ] 4. Проверить: make secrets-unlock NODE={n} → расшифровка OK, platform-secrets стартует
##        [ ] 5. Уничтожить старый ключ (shred) после окна отката; старый ключ в защищённом
##                месте 30 дней
##        [ ] 6. ⚠️ Потеря мастер-ключа = потеря секретов (восстановление только из DR-бэкапа)
##
## §3. Откат (rollback window N=30 дней)
##      Принцип: двухключевой переход — старый ключ/значение НЕ уничтожается мгновенно, а
##      хранится в защищённом месте (password manager / age-encrypted файл) 30 дней после
##      ротации. Как откатить (в окне 30 дней):
##      | Ключ | Откат |
##      | VPS_SSH_KEY | Пере-добавить старый приватный ключ в GitHub Secrets; вернуть старый pub
##      |             | в node.yaml/authorized_keys → проверить make converge |
##      | CI_DEPLOY_KEY | Пере-добавить старый ключ в repo deploy keys ×N + GitHub Secrets →
##      |               | проверить make deploy-project |
##      | MIRROR_SSH_KEY | Вернуть старый ключ в Secrets + старый pub в аккаунт/TronyxLab →
##      |                | workflow_dispatch mirror |
##      | DOCKER_HUB_TOKEN | Вернуть старый token в Secrets + node secrets.env → docker auth проверка |
##      | GHCR_PULL/PUSH_TOKEN | Вернуть старый PAT (если не отозван) в sops/Secrets |
##      | TELEGRAM_BOT_TOKEN | Вернуть старый токен (если не revoked в BotFather) в secrets.env |
##      | AGE_SECRET_KEY | Вернуть старый ключ на ноду; sops-файлы расшифруются старым ключом
##      |                | (если не перешифрованы) |
##      Правило: окно 30 дней — жёсткий максимум. После него старые ключи уничтожаются
##      (секрет скомпрометирован или заменён — держать дольше = лишняя поверхность атаки).
##
## §4. Отдельные сценарии
##      - Новая VPS: старый VPS_SSH_KEY не авторизуется на новом сервере → генерация
##        vps_ci_root + обновление Secrets (Tronyx161 + TronyxLab) ДО bootstrap.
##      - Утечка лога CI: GITHUB_TOKEN не ротируется (auto); проверить permissions: воркфлоу;
##        отозвать любые PAT, попавшие в логи (GHCR_PUSH_TOKEN и т.п.).
##      - Потеря AGE мастер-ключа: см. DR_PROCEDURE в core/internal/deploy/age_key_backup.py —
##        off-node encrypted backup, процедура восстановления, threat-model.
##
## §5. grep-гейт имён секретов
##      До создания runbook следующие имена ОТСУТСТВОВАЛИ в предыдущей документации
##      (каталог документации удалён Волной D DevPlan 164): GHCR_OWNER (§2.7 — удалён
##      DevPlan 002), GIT_MIRROR_TOKEN (§2.5 deprecated), TELEGRAM_BOT_TOKEN/CHAT_ID*/PROXY_URL/API_BASE/
##      ALLOWED_USERS (§2.8), MIRROR_SSH_KEY (§2.3); VPS_SSH_KEY/CI_DEPLOY_KEY упоминались без
##      процедур; GITHUB_TOKEN/DOCKER_HUB_TOKEN — только в workflow-комментариях. Инвариант
##      grep-гейта: любой новый CI-секрет в .github//makefiles//core/ обязан попадать в
##      матрицу §1 (или явно помечаться auto/derived) — иначе секрет снова «знание в голове».
## @links    core/secret-definitions.yaml (SSoT инвентаря), core/secrets-manifest.yaml
##           (GENERATED с consumers), .github/actions/setup-ssh/action.yml (единый SSH setup),
##           .github/mirror-deploy-key.pub (публичный ключ mirror),
##           core/internal/deploy/age_key_backup.py (DR AGE мастер-ключа)
# endregion CI_SECRETS_ROTATION

from __future__ import annotations

import logging
import os
import pathlib

from core.internal.shared.subprocess_io import CommandRunner, default_command_runner

# W1-A1 (план 170): литералы таймаутов → канон SoT (AMBER-зачистка research-D §D1).
# 10 (id подвызовы) → DOCKER_CMD_TIMEOUT; 120 (converge R3) → LIFECYCLE_CMD_TIMEOUT.
from core.internal.shared.timeouts import DOCKER_CMD_TIMEOUT, LIFECYCLE_CMD_TIMEOUT

logger = logging.getLogger(__name__)


# region FUNC_create_user
## @purpose  Idempotent user creation with optional group membership.
## @io       ⇥ username: str, groups: Optional[list[str]], runner: CommandRunner | None → ⎋ None
## @complexity O(1)
def create_user(username: str, groups: list[str] | None = None, *, runner: CommandRunner | None = None) -> None:
    """Create a system user if not exists."""
    runner = runner if runner is not None else default_command_runner()
    # Check if user exists
    result = runner.run(["id", username], timeout=DOCKER_CMD_TIMEOUT)
    if result.returncode == 0:
        # ⚠️ TRAP[BUG] · 2026-08-06 · HI · B20b (141 r2): существующий юзер не получал группы
        # · Symptom: пост-деплой чейн (receive под ci-deploy) писал с Permission denied в
        # ·   /opt/platform артефакты root:platform (catalog.json, prometheus-targets) — WARN'ы
        # ·   скрыты → молчаливая деградация. ci-deploy не был в группе platform.
        # · Fix: при существующем юзере — usermod -aG (идемпотентен) для недостающих групп.
        # ·   Бутстрап повторно создаёт группы; существующие ноды чинятся при перевыполнении φ2.
        if groups:
            id_groups = runner.run(["id", "-Gn", username], timeout=DOCKER_CMD_TIMEOUT)
            current: set[str] = (
                set(id_groups.stdout.split()) if id_groups.returncode == 0 else set()
            )  # W11-G3: set() → set[Unknown] | set[str] — аннотация фиксирует контракт
            missing = [g for g in groups if g not in current]
            if missing:
                runner.run(["usermod", "-aG", ",".join(missing), username], check=True)
                logger.info("[IMP:9][user] User '%s' added to groups: %s", username, ",".join(missing))
        logger.info("[IMP:7][user] User '%s' already exists — skipping creation", username)
        return

    groups_str = ",".join(groups) if groups else ""
    cmd = [
        "useradd",
        "--system",
        "--shell",
        "/bin/bash",
        "--create-home",
        "--home-dir",
        f"/home/{username}",
    ]
    if groups_str:
        cmd.extend(["--groups", groups_str])
    cmd.append(username)
    # B4: единый канон shared/subprocess_io (check=True = lifecycle raise-семантика)
    runner.run(cmd, check=True)
    logger.info("[IMP:9][user] User '%s' created", username)


# endregion FUNC_create_user


# region FUNC_add_ssh_key
## @purpose  Add an SSH public key to user's authorized_keys (with forced-command support).
##           T9.18 (B-5, DevPlan 136 W9): существующая запись с тем же ключом НЕ пропускается
##           вслепую — сверяется command= префикс; дрейф (другой/отсутствующий forced-command)
##           реконсилируется перезаписью строки (иначе ci-deploy канал молча оставался бы на
##           старом префиксе после обновления платформы).
## @io       ⇥ username: str, key: str, forced_command_prefix: str | None = None,
##              home_dir: str | None = None (override для тестов; None → /home/`username`),
##              runner: CommandRunner | None = None → ⎋ None
## @complexity O(N) где N = строки authorized_keys
## @invariants
##   - Ключ отсутствует → append (существующее поведение)
##   - Ключ есть + forced_command_prefix задан: строка == ожидаемая → no-op;
##     префикс дрейфует (другой/пустой) → строка перезаписывается (reconcile)
##   - Ключ есть + forced_command_prefix НЕ задан (owner-ключи): key in content → skip (default)
##   - Запись всегда завершается chmod 0600 + chown (единый контракт)
## @changes 2026-08-05 | DevPlan 136 W9 T9.18 (B-5) — reconcile command= префикса
##           + home_dir override (unit-тест без реального /home)
## @changes 2026-08-13 | DevPlan 160 W4b — +runner: CommandRunner | None = None (DI)
def add_ssh_key(
    username: str,
    key: str,
    forced_command_prefix: str | None = None,
    home_dir: str | None = None,
    *,
    runner: CommandRunner | None = None,
) -> None:
    """Add an SSH public key to user's authorized_keys (with forced-command reconcile, T9.18).

    Ротация SSH-ключей (VPS_SSH_KEY / CI_DEPLOY_KEY — ключи, которые пишет эта функция):
    полный runbook — блок CI_SECRETS_ROTATION §2.1/§2.2 (ниже MODULE_CONTRACT этого модуля).
    Двухключевой переход (НЕ одномоментная замена):
      1. Сгенерировать новую пару: ssh-keygen -t ed25519 -C "vps_ci_root-$(date +%Y%m%d)"
         (для ci-deploy: "platform_personal_cicd-$(date +%Y%m%d)").
      2. Добавить новый публичный ключ этой функцией (add_ssh_key нового ключа) в
         authorized_keys (forced-command запись — reconcile T9.18); для CI_DEPLOY_KEY —
         ещё в node.yaml#node.ci_deploy_key и repo deploy keys ×N проектов.
      3. Новый приватный ключ → GitHub Secrets (VPS_SSH_KEY / CI_DEPLOY_KEY [+ SSH_KEY
         workflow_call, если используется]).
      4. Verify нового канала: make check-security NODE={n} (или make converge) для
         VPS_SSH_KEY; make deploy-project PROJECT={p} NODE={n} (forced-command receive)
         для CI_DEPLOY_KEY.
      5. Удалить старый ключ из authorized_keys (этой функцией reconcile НЕ удаляет —
         вручную), из repo deploy keys ×N и из GitHub Secrets.
      6. Старый приватный ключ — в защищённое место на 30 дней (окно отката, §3 runbook).
      ⚠️ Единственный писатель ci-deploy ключа — этот модуль (канон root AGENTS.md); ротация
      ключа выполняется ТОЛЬКО через этот модуль/φ2, не прямыми правками файлов.
    """
    runner = runner if runner is not None else default_command_runner()
    home = home_dir or f"/home/{username}"
    ssh_dir = os.path.join(home, ".ssh")
    auth_keys = os.path.join(ssh_dir, "authorized_keys")

    os.makedirs(ssh_dir, mode=0o700, exist_ok=True)
    # Ensure ownership (B4: non_fatal=True + fatal_rc=(127,) — exit=127 всегда fatal, TRAP[BUG])
    runner.run(["chown", f"{username}:{username}", ssh_dir], non_fatal=True, fatal_rc=(127,))

    expected_entry = f"{forced_command_prefix} {key}" if forced_command_prefix else key

    # ── Reconcile (T9.18): ключ присутствует — проверить/исправить command= префикс ──
    if os.path.isfile(auth_keys):
        try:
            with pathlib.Path(auth_keys).open(encoding="utf-8") as f:
                lines = f.read().splitlines()
        except OSError:
            lines = []
        for i, line in enumerate(lines):
            if not line.strip() or key not in line:
                continue
            if line.strip() == expected_entry:
                logger.info("[IMP:7][ssh_key] Key already present with matching prefix for %s — skipping", username)
                return
            if forced_command_prefix is not None:
                # ⚠️ TRAP[BUG] · 2026-08-05 · P1 · stale forced-command prefix не чинился
                # · Symptom: обновление платформы меняет command= префикс (orchestrator_cli dispatch,
                #   DevPlan 116 B1) — но authorized_keys уже содержит ключ с СТАРЫМ префиксом →
                #   add_ssh_key («key in content») пропускал → канал оставался на старом диспетчере.
                # · Root: duplicate-check по ключу без сравнения префикса (B-5, T9.18).
                # · Fix: при несовпадении строки перезаписать ЕЁ (reconcile), не дублировать.
                # · Prevention: сравнение полной записи (prefix + key), не только ключа.
                logger.warning(
                    "[IMP:8][ssh_key] Key for %s has STALE forced-command prefix — reconciling (T9.18)",
                    username,
                )
                lines[i] = expected_entry
                with pathlib.Path(auth_keys).open("w", encoding="utf-8") as f:
                    f.write("\n".join(lines) + "\n")
                os.chmod(auth_keys, 0o600)
                runner.run(["chown", f"{username}:{username}", auth_keys], non_fatal=True, fatal_rc=(127,))
                logger.info("[IMP:9][ssh_key] SSH key prefix reconciled for %s", username)
                return
            # owner-ключ (без prefix): поведение — ключ есть → skip
            logger.info("[IMP:7][ssh_key] Key already present for %s — skipping", username)
            return

    entry = f"{forced_command_prefix} {key}\n" if forced_command_prefix else f"{key}\n"
    with pathlib.Path(auth_keys).open("a", encoding="utf-8") as f:
        f.write(entry)
    os.chmod(auth_keys, 0o600)
    runner.run(["chown", f"{username}:{username}", auth_keys], non_fatal=True, fatal_rc=(127,))
    logger.info("[IMP:9][ssh_key] SSH key added for %s", username)


# endregion FUNC_add_ssh_key


# region FUNC_ensure_projects_base
## @purpose  Ensure /opt/projects base directory exists with correct ownership + converge R3.
## @io       ⇥ core_dir, node_name, runner: CommandRunner | None → ⎋ None
## @complexity O(1) + subprocess
def ensure_projects_base(core_dir: str, node_name: str, *, runner: CommandRunner | None = None) -> None:
    """Ensure /opt/projects base directory exists with correct ownership."""
    runner = runner if runner is not None else default_command_runner()
    # Канонический корень проектов — shared/deploy_paths (литерал /opt/projects не используется)
    from core.internal.shared.deploy_paths import projects_base

    projects_dir = str(projects_base())
    os.makedirs(projects_dir, exist_ok=True)
    runner.run(["chown", "ci-deploy:ci-deploy", projects_dir], non_fatal=True, fatal_rc=(127,))
    logger.info("[IMP:9][projects_base] %s ownership set to ci-deploy:ci-deploy", projects_dir)

    # Call converge R3
    converge_script = os.path.join(core_dir, "internal", "bootstrap", "converge.sh")
    if os.path.isfile(converge_script) and node_name:
        logger.info("[IMP:8][projects_base] Calling converge R3 for project scaffold")
        runner.run(
            ["bash", converge_script, "--node", node_name, "--units", "R3"],
            non_fatal=True,
            fatal_rc=(127,),
            timeout=LIFECYCLE_CMD_TIMEOUT,  # B4: lifecycle default (120) — converge R3 может занимать >30s
        )


# endregion FUNC_ensure_projects_base
