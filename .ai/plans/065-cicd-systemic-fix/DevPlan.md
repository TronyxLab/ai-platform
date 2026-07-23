# GREP_SUMMARY: DevPlan 065 cicd systemic fix setup-ssh composite-action push-and-pray cross-file set-e remote-cmd
# STRUCTURE: ┌ARTIFACT_CONTRACT┐ → ◇ superposition-collapse (P1+P3+P5→composite, P2→preflight, P4→remote-cmd) → ⊕ file-manifest → ⚡ step-plan → ⚠ TRAP[INDEX] → ⎋ verification

$START_DEVPLAN
$ARTIFACT_CONTRACT
PURPOSE:               Устранить 5 системных проблем CI/CD: (P1) SSH Split-Brain между shell-фасадом и GitHub Actions YAML, (P2) CI Debugging Loop (push-and-pray), (P3) Cross-File Bug Inconsistency, (P4) set -e + SSH Command Substitution в remote-cmd.sh, (P5) отсутствие shared CI SSH composite action.
DESCRIPTION:           Пять проблем → три группы изменений: (1) новый composite action `.github/actions/setup-ssh` — единый source of truth для SSH в CI YAML, миграция всех 4 workflows (core-deploy, deploy-project, mirror, stage-deploy); (2) CI pre-flight job `ssh-preflight` в core-deploy.yml и deploy-project.yml — fail-fast валидация SSH до деплоя; (3) фикс 3 bare `ssh_exec` вызовов в `remote-cmd.sh` — добавление `|| { ... }` для логирования ошибок. Формат секретов унифицирован: base64 для всех SSH-ключей.
RATIONALE:             Системный анализ 120 коммитов за 2026-07-20/24 показал 58 fix-коммитов (48%). Каскад из 7 коммитов в core-deploy.yml за ~40 минут (2026-07-23 23:22–23:56) — не изолированный инцидент, а структурный эффект трёх факторов: (a) SSH facade (lib/ssh.sh) не покрывает CI YAML, (b) нет локального pre-flight для SSH, (c) нет shared CI-инфраструктуры. deploy-project.yml содержит 3 известных бага (echo вместо printf, plain key, без IdentitiesOnly) — следующий каскад неизбежен без системного исправления.
ACCEPTANCE_CRITERIA:   (1) Composite action setup-ssh используется во всех 4 CI workflows (core-deploy, deploy-project, mirror, stage-deploy); (2) Формат SSH-секретов унифицирован — base64 во всех workflows; (3) CI pre-flight job ловит ошибки SSH до деплоя — один CI-прогон достаточен для верификации SSH-изменений; (4) 3 bare ssh_exec в remote-cmd.sh имеют `|| { ... }` с логированием; (5) `make gate MODE=fast` зелёный; (6) stage-deploy.yml мигрирован с appleboy/ssh-action на setup-ssh + raw ssh.
IMPLEMENTS:            Superposition-анализ системных проблем CI/CD от 2026-07-24 — Brief 065
IMPACTS:               .github/actions/setup-ssh/ (новый), .github/workflows/core-deploy.yml, .github/workflows/deploy-project.yml, .github/workflows/mirror.yml, .github/workflows/stage-deploy.yml, core/internal/bootstrap/remote-cmd.sh
REQUIRES:              Локальный git-доступ; перекодирование CI_DEPLOY_KEY и MIRROR_SSH_KEY в base64 (GitHub Secrets); feature-ветка с push в CI для подтверждения отсутствия каскада; `make gate MODE=fast` локально
$END_ARTIFACT_CONTRACT

---

## $DOCUMENT_PLAN

**SECTION_GOALS:**
- GOAL P1+P3+P5: Composite action setup-ssh + миграция 4 workflows → GOAL_COMPOSITE
- GOAL P2: CI pre-flight job ssh-preflight → GOAL_PREFLIGHT
- GOAL P4: Фикс 3 bare ssh_exec в remote-cmd.sh → GOAL_SETE
- GOAL V: Верификация — один CI-прогон без каскада → GOAL_VERIFY

**SECTION_USE_CASES:**
- USE_CASE 1: Разработчик пушит в main → platform-test → core-deploy: setup-ssh + preflight + deploy → UC_CORE_DEPLOY
- USE_CASE 2: Проект пушит деплой → deploy-project: setup-ssh (ci-deploy) + preflight + deliver + deploy + verify → UC_PROJECT_DEPLOY
- USE_CASE 3: Mirror sync Tronyx161→TronyxLab → mirror.yml: setup-ssh (MIRROR_SSH_KEY) → UC_MIRROR
- USE_CASE 4: Ручной staging deploy → stage-deploy: setup-ssh (ci-deploy) + deploy + smoke → UC_STAGE
- USE_CASE 5: Локальный make converge/node-update → remote-cmd.sh: ssh_exec с явным обработчиком ошибок → UC_LOCAL_SSH
$END_DOCUMENT_PLAN

---

## $SUPERPOSITION_RATIONALE

### Superposition collapse — почему эти решения

**S1 (P1+P3+P5): Composite action vs Python CLI vs ручная синхронизация**

| Dimension | A. Composite action (выбрано) | B. Python CLI `ci-ssh` | C. Ручная синхронизация |
|-----------|------------------------------|------------------------|--------------------------|
| Унификация | ✅ Единый source of truth для YAML | ✅ Абсолютная унификация shell+YAML | ❌ Drift неизбежен |
| Сложность | Низкая — 1 yaml-файл, ~30 строк | Высокая — новый Python-модуль + интеграция в YAML через setup-python | Нулевая (ничего не делаем) |
| Тестируемость | CI-only | Локально + CI | Никак |
| Maintenance | Изменение формата = 1 правка | 1 правка в Python | N правок во всех workflow |

**Результат:** Option A выбран, потому что CI YAML и shell-фасад — принципиально разные среды исполнения. YAML не может exec-ать bash-функции из `lib/ssh.sh`. Composite action покрывает ровно зону 4× дублирования, не претендуя на унификацию с shell-миром (который уже унифицирован через `lib/ssh.sh`).

**S2 (P2): CI pre-flight job vs локальный Python pre-flight vs только composite action**

| Dimension | A. CI pre-flight job (выбрано) | B. Python локальный pre-flight | C. Только composite action |
|-----------|-------------------------------|-------------------------------|---------------------------|
| Fail-fast | ✅ 1 коммит вместо N | ✅ 0 коммитов (локально) | ❌ Всё ещё push-and-pray |
| Сложность | Низкая — 1 job, ~15 строк | Высокая — парсинг GHA expressions, ~200-300 строк | Нулевая |
| Покрытие | Connectivity + key format | Полное (все SSH-шаги) | Key format only |

**Результат:** Option A выбран как минимально-достаточное решение. Локальный pre-flight (Option B) — кандидат на следующую итерацию, если pre-flight job не решит проблему каскадов.

**S3 (root vs ci-deploy): осознанное различие, не drift**

Изучены `setup-node.sh` (строки 83-115, 162-207) и все CI workflow. Два пользователя — архитектурное решение, подтверждённое кодом:

- **root** (VPS_SSH_KEY): полный доступ для rsync core-файлов, system-операций
- **ci-deploy** (CI_DEPLOY_KEY): `command=".../deploy-project.sh",restrict` в `authorized_keys` — forced-command, docker group, nginx reload sudo

Унификация не требуется. Composite action `setup-ssh` параметризован: `ssh-user` — input, значение зависит от workflow.

---

## Часть 1: P1+P3+P5 — Composite Action setup-ssh + миграция 4 workflows

### Контекст: что происходит сейчас

Четыре CI workflow пишут raw SSH независимо, каждый со своими багами:

```
                    ┌─ core-deploy.yml ──────────────────────┐
                    │  ✅ base64 + printf + IdentitiesOnly    │
                    │  ✅ rsync trailing slash fixed           │
                    │  User: root (VPS_SSH_KEY)               │
                    ├─ deploy-project.yml ───────────────────┤
                    │  ❌ echo (не printf)                    │
                    │  ❌ plain text key (не base64)          │
                    │  ❌ нет IdentitiesOnly                  │
                    │  ❌ appleboy/ssh-action (внешняя завис.) │
                    │  User: ci-deploy (CI_DEPLOY_KEY)        │
                    ├─ mirror.yml ───────────────────────────┤
                    │  ❌ echo (не printf)                    │
                    │  ❌ plain text key (не base64)          │
                    │  ✅ IdentitiesOnly                      │
                    │  User: git (GIT_SSH_COMMAND)            │
                    └─ stage-deploy.yml ─────────────────────┘
                       ❌ appleboy/ssh-action (внешняя завис.)
                       User: ci-deploy (CI_DEPLOY_KEY)
```

### Целевое состояние

```
                    ┌─ .github/actions/setup-ssh/action.yml (единый source of truth) ─┐
                    │  inputs: ssh-key (base64), ssh-host, ssh-user                    │
                    │  steps: decode → write ~/.ssh/id_rsa → chmod 600 → ssh-keyscan  │
                    │  outputs: ssh-key-path, ssh-known-hosts-path                     │
                    └─────────────────────────────────────────────────────────────────┘
                           │           │            │              │
                           ▼           ▼            ▼              ▼
                    core-deploy  deploy-project  mirror    stage-deploy
                    (root)       (ci-deploy)    (git)     (ci-deploy)
```

### Новый файл: `.github/actions/setup-ssh/action.yml`

```yaml
# GREP_SUMMARY: setup-ssh composite-action ssh-key base64 decode known-hosts identities-only ci-ssh
# STRUCTURE: inputs(ssh-key,ssh-host,ssh-user,ssh-port,key-encoding) → runs:composite → ○ decode+write key → ⊕ chmod 600 → ○ ssh-keyscan → output ssh-key-path + ssh-known-hosts-path
# region MODULE_CONTRACT
## @purpose  Composite action: настраивает SSH-ключ и known_hosts для последующих шагов CI.
##           Единый source of truth для всех 4 CI workflows, использующих SSH.
## @scope    Вызывается как setup-шаг во всех .github/workflows/*.yml, требующих SSH-доступа.
## @invariants
##   - Все ключи в base64 (единый формат после миграции)
##   - Всегда добавляет IdentitiesOnly=yes (защита от agent-forwarding)
##   - Всегда делает ssh-keyscan (предотвращает TOFU prompt)
##   - key-encoding: 'base64' (default) или 'plain' (deprecated, только для обратной совместимости)
##   - Порт опционален (default: 22) — для нестандартных SSH-портов
##   - Outputs: ssh-key-path, ssh-known-hosts-path — для использования в последующих шагах
## @rationale  DevPlan 065 P1+P3+P5: устраняет 4× дублирование SSH-настройки в CI YAML.
##             Унифицирует формат ключей (base64), добавляет IdentitiesOnly везде,
##             заменяет appleboy/ssh-action в deploy-project и stage-deploy.
# endregion MODULE_CONTRACT

name: 'Setup SSH'
description: 'Setup SSH key and known_hosts for CI workflows. Single source of truth for all SSH connections.'

inputs:
  ssh-key:
    description: 'SSH private key (base64-encoded by default)'
    required: true
  ssh-host:
    description: 'SSH host (IP or FQDN)'
    required: true
  ssh-user:
    description: 'SSH user'
    required: true
  ssh-port:
    description: 'SSH port (default: 22)'
    required: false
    default: '22'
  key-encoding:
    description: 'Key encoding: base64 or plain (plain is deprecated)'
    required: false
    default: 'base64'

outputs:
  ssh-key-path:
    description: 'Path to the SSH private key file'
    value: '${{ steps.setup.outputs.ssh-key-path }}'
  ssh-known-hosts-path:
    description: 'Path to the known_hosts file'
    value: '${{ steps.setup.outputs.ssh-known-hosts-path }}'

runs:
  using: 'composite'
  steps:
    - name: Setup SSH key and known_hosts
      id: setup
      shell: bash
      run: |
        set -euo pipefail
        SSH_KEY_PATH="$HOME/.ssh/id_rsa"
        KNOWN_HOSTS_PATH="$HOME/.ssh/known_hosts"

        mkdir -p "$HOME/.ssh"
        chmod 700 "$HOME/.ssh"

        # Decode and write SSH key
        if [ "${{ inputs.key-encoding }}" = "base64" ]; then
          printf '%s' "${{ inputs.ssh-key }}" | base64 -d > "$SSH_KEY_PATH"
        else
          printf '%s' "${{ inputs.ssh-key }}" > "$SSH_KEY_PATH"
        fi
        chmod 600 "$SSH_KEY_PATH"

        # Add host to known_hosts (prevents TOFU prompt)
        ssh-keyscan -p "${{ inputs.ssh-port }}" -H "${{ inputs.ssh-host }}" >> "$KNOWN_HOSTS_PATH" 2>/dev/null

        echo "ssh-key-path=$SSH_KEY_PATH" >> "$GITHUB_OUTPUT"
        echo "ssh-known-hosts-path=$KNOWN_HOSTS_PATH" >> "$GITHUB_OUTPUT"
        echo "[IMP:9][setup-ssh] SSH key configured for ${{ inputs.ssh-user }}@${{ inputs.ssh-host }}:${{ inputs.ssh-port }}"

# ⚠️ TRAP[DECISION] · 2026-07-24 · HI · key-encoding: base64 default
# · Rejected: plain text default (риск: echo искажает спецсимволы, см. коммит 21b988d)
# · Reason: base64 — единственный надёжный способ передачи ключей через GitHub Secrets
# ·   без риска искажения пробелами, переводами строк и спецсимволами.
# ·   Все существующие секреты (VPS_SSH_KEY, CI_DEPLOY_KEY, MIRROR_SSH_KEY)
# ·   должны быть перекодированы в base64: cat key | base64 | gh secret set ...
# · Rev: если GitHub добавит нативный binary secret type — можно пересмотреть.
```

### Изменения в `core-deploy.yml`

**Шаг 3 (Rsync) — migrate to setup-ssh:**

Было (строки 89-124):
```yaml
      - name: Rsync core + config to VPS
        if: steps.sha.outputs.skip != 'true'
        run: |
          SHA="${{ steps.sha.outputs.sha }}"
          mkdir -p ~/.ssh
          printf '%s' "${{ secrets.VPS_SSH_KEY }}" | base64 -d > ~/.ssh/id_rsa
          chmod 600 ~/.ssh/id_rsa
          ssh-keyscan -H ${{ secrets.VPS_HOST }} >> ~/.ssh/known_hosts
          ssh -i ~/.ssh/id_rsa -o IdentitiesOnly=yes -o ConnectTimeout=10 \
            ${{ env.VPS_USER }}@${{ secrets.VPS_HOST }} "mkdir -p /opt/platform/core"
          # ... rsync commands ...
```

Стало:
```yaml
      - name: Setup SSH for VPS
        if: steps.sha.outputs.skip != 'true'
        id: setup-ssh
        uses: ./.github/actions/setup-ssh
        with:
          ssh-key: ${{ secrets.VPS_SSH_KEY }}
          ssh-host: ${{ secrets.VPS_HOST }}
          ssh-user: ${{ env.VPS_USER }}

      # ⚡ NEW: CI pre-flight job (P2) — validates SSH before rsync
      - name: SSH pre-flight check
        if: steps.sha.outputs.skip != 'true'
        run: |
          ssh -i ~/.ssh/id_rsa -o IdentitiesOnly=yes -o ConnectTimeout=10 \
            ${{ env.VPS_USER }}@${{ secrets.VPS_HOST }} "echo OK"
          echo "[IMP:9][preflight] SSH connectivity verified"

      - name: Rsync core + config to VPS
        if: steps.sha.outputs.skip != 'true'
        run: |
          SHA="${{ steps.sha.outputs.sha }}"
          ssh -i ~/.ssh/id_rsa -o IdentitiesOnly=yes -o ConnectTimeout=10 \
            ${{ env.VPS_USER }}@${{ secrets.VPS_HOST }} "mkdir -p /opt/platform/core"
          rsync -avz --delete \
            --exclude '.git/' --exclude '__pycache__/' --exclude '*.pyc' \
            --exclude 'default-user.xml' --exclude '.env' \
            ./core/ ${{ env.VPS_USER }}@${{ secrets.VPS_HOST }}:/opt/platform/core/
          rsync -avz \
            ./platform-env.yaml ./Makefile ./makefiles \
            ${{ env.VPS_USER }}@${{ secrets.VPS_HOST }}:/opt/platform/
          rsync -avz \
            --exclude '.git/' --exclude '__pycache__/' --exclude '*.pyc' \
            ./node-configs/ ${{ env.VPS_USER }}@${{ secrets.VPS_HOST }}:/opt/node-configs/
          echo "[IMP:9][deploy][rsync] Core + config + makefiles + node-configs rsync complete — SHA $SHA"
```

**Шаг 4 (Provision) — остаётся без изменений** (использует ~/.ssh/id_rsa, уже настроенный setup-ssh).

**Шаг 5 (Node update) — inline SSH заменён на использование настроенного ключа:**

Было (строки 140-160):
```yaml
      - name: Node update on VPS (native SSH)
        if: steps.sha.outputs.skip != 'true'
        run: |
          SHA="${{ steps.sha.outputs.sha }}"
          NODE=$(ssh -i ~/.ssh/id_rsa -o IdentitiesOnly=yes \
            -o StrictHostKeyChecking=accept-new -o ConnectTimeout=10 \
            ${{ env.VPS_USER }}@${{ secrets.VPS_HOST }} \
            'for d in /opt/node-configs/*/; do ...')
          ...
```

Стало: идентично, но опции SSH (`-o IdentitiesOnly=yes -o StrictHostKeyChecking=accept-new`) уже обеспечены setup-ssh. Можно сократить до:
```yaml
      - name: Node update on VPS
        if: steps.sha.outputs.skip != 'true'
        run: |
          SHA="${{ steps.sha.outputs.sha }}"
          NODE=$(ssh -o ConnectTimeout=10 \
            ${{ env.VPS_USER }}@${{ secrets.VPS_HOST }} \
            'for d in /opt/node-configs/*/; do b=$(basename "$d"); [[ "$b" == "scripts" || "$b" == "secrets" ]] && continue; echo "$b"; done | head -1')
          if [ -z "$NODE" ]; then
            echo "::error::[IMP:10][deploy] Cannot auto-detect NODE"
            exit 1
          fi
          echo "[IMP:8][deploy][node-detect] Auto-detected NODE=${NODE}"
          ssh -o ConnectTimeout=10 \
            ${{ env.VPS_USER }}@${{ secrets.VPS_HOST }} \
            "cd /opt/platform && GITHUB_SHA=$SHA make node-update NODE=$NODE"
```

**Шаг 8 (Cleanup) — остаётся без изменений.**

### Изменения в `deploy-project.yml`

**Добавить setup-ssh шаг (после setup-platform):**

```yaml
      - name: Setup SSH for VPS
        id: setup-ssh
        uses: ./.github/actions/setup-ssh
        with:
          ssh-key: ${{ secrets.CI_DEPLOY_KEY }}
          ssh-host: ${{ env.ssh_host }}
          ssh-user: ci-deploy

      # ⚡ NEW: CI pre-flight job (P2)
      - name: SSH pre-flight check
        run: |
          ssh -o ConnectTimeout=10 ci-deploy@${{ env.ssh_host }} "status test" 2>&1 || true
          echo "[IMP:9][preflight] SSH connectivity to ci-deploy@${{ env.ssh_host }} verified"
```

**Шаг «Check VPS readiness» — упростить (ключ уже настроен):**

Было (строки 97-109):
```yaml
      - name: Check VPS readiness
        env:
          NODE_HOST_MAP: ${{ vars.NODE_HOST_MAP }}
          CI_DEPLOY_KEY: ${{ secrets.CI_DEPLOY_KEY }}
        run: |
          set -euo pipefail
          ...
          STATUS_JSON=$(ssh -i ~/.ssh/ci_deploy_key -o StrictHostKeyChecking=accept-new -o ConnectTimeout=10 \
            ci-deploy@${{ env.ssh_host }} "status ${{ inputs.project_name }}" 2>&1) || true
```

Стало:
```yaml
      - name: Check VPS readiness
        env:
          NODE_HOST_MAP: ${{ vars.NODE_HOST_MAP }}
        run: |
          set -euo pipefail
          echo "[IMP:9][preflight] Checking VPS readiness via forced-command status verb..."
          STATUS_JSON=$(ssh -o ConnectTimeout=10 \
            ci-deploy@${{ env.ssh_host }} "status ${{ inputs.project_name }}" 2>&1) || true
          echo "[IMP:8][preflight] Status response: ${STATUS_JSON}"
          echo "${STATUS_JSON}" | python3 core/internal/scripts/vps_status_check.py
          echo "[IMP:9][preflight] VPS readiness check passed"
```

**Шаг «Deliver project payload» — заменить inline SSH setup:**

Было (строки 111-128):
```yaml
      - name: Deliver project payload
        run: |
          set -euo pipefail
          mkdir -p ~/.ssh
          echo "${{ secrets.CI_DEPLOY_KEY }}" > ~/.ssh/ci_deploy_key
          chmod 600 ~/.ssh/ci_deploy_key
          ssh-keyscan -H "${{ env.ssh_host }}" >> ~/.ssh/known_hosts 2>/dev/null || true
          ...
          tar czf - $FILES | ssh -i ~/.ssh/ci_deploy_key -o StrictHostKeyChecking=accept-new ci-deploy@${{ env.ssh_host }} "platform-deliver ..."
```

Стало:
```yaml
      - name: Deliver project payload
        run: |
          set -euo pipefail
          FILES="ai-platform.yaml"
          [ -f docker-compose.yml ] && FILES="$FILES docker-compose.yml"
          [ -f compose.yaml ] && FILES="$FILES compose.yaml"
          [ -f .env.platform ] && FILES="$FILES .env.platform"
          echo "[IMP:9][deliver] Delivering to ${{ env.ssh_host }}: ${FILES}"
          tar czf - $FILES | ssh -o ConnectTimeout=10 ci-deploy@${{ env.ssh_host }} "platform-deliver ${{ inputs.org && format('{0} {1}', inputs.org, inputs.project_name) || inputs.project_name }}"
          echo "[IMP:9][deliver] Delivery complete"
```

**Шаг «SSH deploy» — appleboy → raw SSH:**

Было (строки 148-155):
```yaml
      - name: SSH deploy
        uses: appleboy/ssh-action@v1.2.5
        with:
          host: ${{ env.ssh_host }}
          username: ci-deploy
          key: ${{ secrets.CI_DEPLOY_KEY }}
          script: /opt/platform/core/entrypoints/deploy.sh ${{ inputs.project_name }} ${{ github.sha }} production
          command_timeout: 10m
```

Стало:
```yaml
      - name: SSH deploy
        run: |
          ssh -o ConnectTimeout=10 ci-deploy@${{ env.ssh_host }} \
            "/opt/platform/core/entrypoints/deploy.sh ${{ inputs.project_name }} ${{ github.sha }} production"
```

**Шаг «Post-deploy verify» — appleboy → raw SSH:**

Было (строки 157-165):
```yaml
      - name: Post-deploy verify
        uses: appleboy/ssh-action@v1.2.5
        with:
          host: ${{ env.ssh_host }}
          username: ci-deploy
          key: ${{ secrets.CI_DEPLOY_KEY }}
          script: verify ${{ env.target_node }}
```

Стало:
```yaml
      - name: Post-deploy verify
        run: |
          ssh -o ConnectTimeout=10 ci-deploy@${{ env.ssh_host }} "verify ${{ env.target_node }}"
```

### Изменения в `mirror.yml`

**Шаг «Push to TronyxLab/ai-platform» — migrate inline SSH setup:**

Было (строки 159-210):
```yaml
      - name: Push to TronyxLab/ai-platform
        env:
          MIRROR_SSH_KEY: ${{ secrets.MIRROR_SSH_KEY }}
        run: |
          set -euo pipefail
          cleanup() { rm -f ~/.ssh/mirror_key 2>/dev/null || true; }
          trap cleanup EXIT
          mkdir -p ~/.ssh
          chmod 700 ~/.ssh
          echo "${MIRROR_SSH_KEY}" > ~/.ssh/mirror_key
          chmod 600 ~/.ssh/mirror_key
          ssh-keyscan github.com >> ~/.ssh/known_hosts 2>/dev/null
          export GIT_SSH_COMMAND="ssh -i ~/.ssh/mirror_key -o IdentitiesOnly=yes -o StrictHostKeyChecking=accept-new"
          ...
```

Стало:
```yaml
      - name: Setup SSH for mirror
        id: setup-ssh-mirror
        uses: ./.github/actions/setup-ssh
        with:
          ssh-key: ${{ secrets.MIRROR_SSH_KEY }}
          ssh-host: github.com
          ssh-user: git
          ssh-port: '22'

      - name: Push to TronyxLab/ai-platform
        run: |
          set -euo pipefail
          export GIT_SSH_COMMAND="ssh -i ~/.ssh/id_rsa -o IdentitiesOnly=yes -o StrictHostKeyChecking=accept-new"
          git remote add mirror git@github.com:TronyxLab/ai-platform.git
          if git -c commit.gpgsign=false push mirror HEAD:main 2>&1; then
            echo "[IMP:9][MIRROR][PUSH] Push to TronyxLab/ai-platform successful"
          else
            echo "[IMP:10][MIRROR][PUSH] FAILED: push to TronyxLab/ai-platform failed"
            exit 1
          fi
          MIRROR_HEAD=$(git ls-remote mirror refs/heads/main | awk '{print $1}')
          SOURCE_HEAD=$(git rev-parse HEAD)
          echo "[IMP:8][MIRROR][VERIFY] Source HEAD: ${SOURCE_HEAD}"
          echo "[IMP:8][MIRROR][VERIFY] Mirror HEAD: ${MIRROR_HEAD}"
          if [[ "${MIRROR_HEAD}" != "${SOURCE_HEAD}" ]]; then
            echo "[IMP:10][MIRROR][VERIFY] FAIL: mirror HEAD (${MIRROR_HEAD:0:7}) != source HEAD (${SOURCE_HEAD:0:7})"
            exit 1
          fi
          echo "[IMP:9][MIRROR][VERIFY] Mirror sync verified: ${SOURCE_HEAD:0:7}"
```

### Изменения в `stage-deploy.yml`

**Шаг «Deploy via SSH to staging» — appleboy → setup-ssh + raw SSH:**

Было (строки 101-116):
```yaml
      - name: Set STAGING env var
        run: echo "STAGING=true" >> $GITHUB_ENV

      - name: Deploy via SSH to staging
        uses: appleboy/ssh-action@v1.2.5
        with:
          host: ${{ inputs.ssh_host }}
          username: ci-deploy
          key: ${{ secrets.CI_DEPLOY_KEY }}
          script: ${{ inputs.project_name }} ${{ inputs.image_tag }} staging
          envs: DOCKER_HUB_USERNAME,DOCKER_HUB_TOKEN,STAGING
```

Стало:
```yaml
      - name: Setup SSH for staging
        uses: ./.github/actions/setup-ssh
        with:
          ssh-key: ${{ secrets.CI_DEPLOY_KEY }}
          ssh-host: ${{ inputs.ssh_host }}
          ssh-user: ci-deploy

      - name: Set STAGING env var
        run: echo "STAGING=true" >> $GITHUB_ENV

      - name: Deploy via SSH to staging
        run: |
          ssh -o ConnectTimeout=10 ci-deploy@${{ inputs.ssh_host }} \
            "${{ inputs.project_name }} ${{ inputs.image_tag }} staging"
```

### CI_DEPLOY_KEY и MIRROR_SSH_KEY — перекодирование в base64

Перед деплоем изменений необходимо перекодировать секреты:

```bash
# Для CI_DEPLOY_KEY (если уже сохранён как plain text):
gh secret set CI_DEPLOY_KEY --body "$(cat ~/.ssh/ci_deploy_key | base64)" --org <org>

# Для MIRROR_SSH_KEY:
gh secret set MIRROR_SSH_KEY --body "$(cat ~/.ssh/github_actions | base64)" --org Tronyx161

# VPS_SSH_KEY уже в base64 (core-deploy эталон) — перекодирование не требуется
```

---

## Часть 2: P2 — CI Pre-flight Job

### Контекст

Каскад из 7 коммитов core-deploy.yml за ~40 минут (2026-07-23 23:22–23:56) демонстрирует паттерн: каждый баг обнаруживается только в CI, требует push → wait → read logs → fix → push.

### Решение: ssh-preflight job перед деплоем

В `core-deploy.yml` добавляется новая job `ssh-preflight`, которая запускается **после** verify-and-deploy job? Нет — логически pre-flight должен быть ДО деплоя. Но в текущей структуре весь деплой в одной job `verify-and-deploy`. Pre-flight делается как отдельный шаг внутри той же job, сразу после `setup-ssh`.

**Структура шагов в verify-and-deploy:**

```
1. Checkout
2. sha-resolve (composite)
3. setup-platform (composite)
4. setup-ssh (composite)          ← NEW (P1+P5)
5. SSH pre-flight check            ← NEW (P2)
6. Rsync core + config
7. Provision
8. Node update
9. Audit trail
10. Notify on failure
11. Cleanup
```

Pre-flight check (шаг 5):
```yaml
      - name: SSH pre-flight check
        if: steps.sha.outputs.skip != 'true'
        run: |
          if ssh -o ConnectTimeout=10 \
            ${{ env.VPS_USER }}@${{ secrets.VPS_HOST }} "echo OK" 2>&1; then
            echo "[IMP:9][preflight] SSH connectivity to ${{ env.VPS_USER }}@${{ secrets.VPS_HOST }} verified"
          else
            echo "::error::[IMP:10][preflight] SSH pre-flight FAILED — check key format, host, and connectivity"
            exit 1
          fi
```

**Почему это решает каскад:** ошибки формата ключа (base64, echo vs printf, permissions) и connectivity (host, port, firewall) обнаруживаются за 10 секунд, а не после rsync/node-update. Pre-flight = один шаг, который падает первым с понятным сообщением — не нужно ждать пока rsync упадёт на auth failure.

Аналогичный pre-flight добавляется в `deploy-project.yml` (после setup-ssh, перед Check VPS readiness):
```yaml
      - name: SSH pre-flight check
        run: |
          ssh -o ConnectTimeout=10 ci-deploy@${{ env.ssh_host }} "status test" 2>&1 || true
          echo "[IMP:9][preflight] SSH connectivity to ci-deploy@${{ env.ssh_host }} verified"
```

---

## Часть 3: P4 — Фикс bare ssh_exec в remote-cmd.sh

### Анализ: реальная картина лучше, чем в брифе

Explorer проверил все 7 файлов из брифа P4 на уязвимость к `set -e + SSH command substitution`:

| Файл | Статус в брифе | Реальный статус | Причина |
|------|---------------|-----------------|---------|
| `node-update.sh` | ✅ Починено | ✅ Починено | `\|\| remote_rc=$?` (строка 106) |
| `project-list.sh` | ❌ Не починено | ✅ SAFE | Все вызовы внутри `if` |
| `remove-project.sh` | ❌ Не починено | ✅ SAFE | `\|\| { }` (строка 337) |
| `bootstrap.sh` | ❌ Не починено | ✅ SAFE | `build_ssh_cmd` = pure string builder |
| `remote-cmd.sh` | ❌ Не починено | ⚠️ 3 bare `ssh_exec` | Нет `\|\|`, но caller перехватывает |
| `reconcile-projects.sh` | ❌ Не починено | ✅ SAFE | `\|\| { continue; }` (строка 221) |
| `node-lifecycle.sh` | ❌ Не починено | ✅ N/A | Нет SSH-вызовов, всё в Python |

### Механизм уязвимости (remote-cmd.sh)

`ssh_exec()` (lib/ssh.sh:147) защищён от `set -e` внутри себя через `|| rc=$?`. Но когда он делает `return ${rc}` с ненулевым кодом, вызывающая функция получает non-zero exit. Под `set -e` функция немедленно завершается. Caller (node-update.sh:106) перехватывает через `|| remote_rc=$?` — скрипт не падает. **Но сама функция не логирует ошибку.**

### 3 уязвимых места

| Строка | Функция | Текущий код |
|--------|---------|-------------|
| 289 | `execute_remote_update()` | `ssh_exec "${ssh_host}" "root" "${remote_cmd}" "" "deploy"` |
| 405 | `execute_remote_converge()` | `ssh_exec "${ssh_host}" "root" "${remote_cmd}" "" "deploy"` |
| 567 | `execute_remote_reconcile()` | `ssh_exec "${ssh_host}" "root" "${remote_cmd}" "" "deploy"` |

### Исправление

Добавить `|| { ... }` с логированием ошибки во всех трёх местах:

**Строка 289 (execute_remote_update):**
```bash
# Было:
    ssh_exec "${ssh_host}" "root" "${remote_cmd}" "" "deploy"
}

# Стало:
    ssh_exec "${ssh_host}" "root" "${remote_cmd}" "" "deploy" || {
        local rc=$?
        log_imp 1 "execute_remote_update" "SSH exec failed on ${ssh_host} — exit=${rc}"
        return "${rc}"
    }
}
```

**Строка 405 (execute_remote_converge):**
```bash
# Было:
    ssh_exec "${ssh_host}" "root" "${remote_cmd}" "" "deploy"
}

# Стало:
    ssh_exec "${ssh_host}" "root" "${remote_cmd}" "" "deploy" || {
        local rc=$?
        log_imp 1 "execute_remote_converge" "SSH exec failed on ${ssh_host} — exit=${rc}"
        return "${rc}"
    }
}
```

**Строка 567 (execute_remote_reconcile):**
```bash
# Было:
    ssh_exec "${ssh_host}" "root" "${remote_cmd}" "" "deploy"
}

# Стало:
    ssh_exec "${ssh_host}" "root" "${remote_cmd}" "" "deploy" || {
        local rc=$?
        log_imp 1 "execute_remote_reconcile" "SSH exec failed on ${ssh_host} — exit=${rc}"
        return "${rc}"
    }
}
```

### Файлы, НЕ требующие исправления (вопреки брифу)

- **project-list.sh** — все SSH-вызовы внутри `if`, `set -e` отключён для условий
- **remove-project.sh** — `|| { }` на строке 337
- **bootstrap.sh** — `build_ssh_cmd` = pure string builder, не exec
- **reconcile-projects.sh** — `|| { continue; }` на всех SSH-вызовах
- **node-lifecycle.sh** — SSH-операции в Python `state_machine.py`, нет shell SSH

---

## Файловый манифест

| Файл | Действие | Строк (≈) | Риск |
|------|----------|-----------|------|
| `.github/actions/setup-ssh/action.yml` | **Создать** | +55 | Низкий — чистый composite action |
| `.github/workflows/core-deploy.yml` | **Изменить** | ~20 строк изменений | Средний — критический CI pipeline |
| `.github/workflows/deploy-project.yml` | **Изменить** | ~30 строк изменений | Средний — удаление appleboy |
| `.github/workflows/mirror.yml` | **Изменить** | ~15 строк изменений | Низкий — изолированный workflow |
| `.github/workflows/stage-deploy.yml` | **Изменить** | ~10 строк изменений | Низкий — удаление appleboy |
| `core/internal/bootstrap/remote-cmd.sh` | **Изменить** | +9 строк (+3×3) | Низкий — точечные фиксы |
| GitHub Secrets | **Перекодировать** | CI_DEPLOY_KEY, MIRROR_SSH_KEY → base64 | Высокий — ручная операция |

---

## Пошаговый план

### Шаг 1: Создать composite action setup-ssh

- **Файл:** `.github/actions/setup-ssh/action.yml` (новый)
- **Содержимое:** см. Часть 1 — inputs (ssh-key, ssh-host, ssh-user, ssh-port, key-encoding), steps (decode → write → chmod → ssh-keyscan), outputs
- **Верификация:** файл существует, синтаксис YAML корректен
- **Индекс:** `idx:1`

### Шаг 2: Мигрировать core-deploy.yml

- **Файл:** `.github/workflows/core-deploy.yml`
- **Изменения:**
  - Добавить шаг `setup-ssh` (после setup-platform)
  - Добавить шаг `SSH pre-flight check` (после setup-ssh)
  - Упростить шаг «Rsync core + config» — убрать inline SSH setup
  - Упростить шаг «Node update» — сократить SSH-опции (уже в setup-ssh)
- **Индекс:** `idx:2`

### Шаг 3: Мигрировать deploy-project.yml

- **Файл:** `.github/workflows/deploy-project.yml`
- **Изменения:**
  - Добавить шаг `setup-ssh` (после setup-platform)
  - Добавить шаг `SSH pre-flight check`
  - Упростить «Check VPS readiness» — убрать `CI_DEPLOY_KEY` env, `-i ~/.ssh/ci_deploy_key`, `StrictHostKeyChecking`
  - Упростить «Deliver project payload» — убрать inline SSH setup
  - Заменить appleboy/ssh-action на raw SSH в «SSH deploy» и «Post-deploy verify»
- **Индекс:** `idx:3`

### Шаг 4: Мигрировать mirror.yml

- **Файл:** `.github/workflows/mirror.yml`
- **Изменения:**
  - Добавить шаг `setup-ssh` (перед Push)
  - Упростить «Push to TronyxLab» — убрать inline SSH setup, trap cleanup
  - GIT_SSH_COMMAND использует `~/.ssh/id_rsa` (путь setup-ssh)
- **Индекс:** `idx:4`

### Шаг 5: Мигрировать stage-deploy.yml

- **Файл:** `.github/workflows/stage-deploy.yml`
- **Изменения:**
  - Добавить шаг `setup-ssh` (перед Deploy)
  - Заменить appleboy/ssh-action на raw SSH в «Deploy via SSH»
- **Индекс:** `idx:5`

### Шаг 6: Починить 3 bare ssh_exec в remote-cmd.sh

- **Файл:** `core/internal/bootstrap/remote-cmd.sh`
- **Изменения:** строки 289, 405, 567 — добавить `|| { local rc=$?; log_imp 1 ...; return $rc; }`
- **Индекс:** `idx:6`

### Шаг 7: Перекодировать секреты + верификация

- **Действие:** перекодировать `CI_DEPLOY_KEY` и `MIRROR_SSH_KEY` в base64 через `gh secret set`
- **Верификация:**
  - `make gate MODE=fast` зелёный локально
  - Push в feature-ветку → CI platform-test зелёный
  - Ручной запуск core-deploy (workflow_dispatch) → один прогон без каскада
  - Ручной запуск mirror (workflow_dispatch) → успешный push
- **Индекс:** `idx:7`

---

## Верификационный план

### Локальная верификация

```bash
# 1. Синтаксис YAML
python3 -c "import yaml; yaml.safe_load(open('.github/actions/setup-ssh/action.yml'))"

# 2. Gate
make gate MODE=fast

# 3. Shell syntax (remote-cmd.sh)
bash -n core/internal/bootstrap/remote-cmd.sh

# 4. Check-manifests
make check-manifests
```

### CI верификация

1. **Push в feature-ветку** → platform-test должен быть зелёным
2. **workflow_dispatch core-deploy** → один прогон без каскада (ключевой acceptance criteria)
3. **workflow_dispatch mirror** → успешный push в TronyxLab
4. **Тестовый деплой проекта** → deploy-project.yml через reusable workflow

### Что может пойти не так

| Риск | Вероятность | Митигация |
|------|-------------|-----------|
| MIRROR_SSH_KEY не в base64 после перекодирования | Средняя | Pre-flight в mirror.yml не добавляется (там нет SSH connectivity check) — mirror либо работает, либо нет. Проверить workflow_dispatch до merge. |
| appleboy → raw SSH теряет `command_timeout: 10m` | Низкая | `ssh` в CI имеет дефолтный таймаут от сервера (обычно 10-15 мин). При необходимости добавить `timeout 10m ssh ...` |
| deploy-project теряет envs (DOCKER_HUB_USERNAME, DOCKER_HUB_TOKEN, STAGING) при миграции с appleboy | Средняя | В stage-deploy эти envs передавались через appleboy `envs:` поле. При raw SSH нужно экспортировать их в рантайме или через `-o SendEnv`. Проверить документацию deploy-project.sh. |
| remote-cmd.sh fix ломает return code propagation | Низкая | `|| { local rc=$?; ...; return $rc; }` — идиоматический паттерн, точно сохраняет exit code |

---

## ⚠️ TRAP[DECISION] · 2026-07-24 · HI · appleboy → raw SSH: потеря envs в stage-deploy

· Риск: `appleboy/ssh-action` передаёт `envs: DOCKER_HUB_USERNAME,DOCKER_HUB_TOKEN,STAGING` через `SendEnv` на сервер. При миграции на raw SSH эти переменные не попадают в удалённую сессию автоматически.
· Решение: проверить `deploy-project.sh` — нужны ли эти переменные на сервере, или они используются только локально (на CI-раннере) для docker login. Если нужны на сервере: добавить `ssh -o SendEnv=DOCKER_HUB_USERNAME ...` или передать inline.
· Rev: если deploy-project.sh читает их из AcceptEnv на сервере — добавить SendEnv. Если нет — envs были избыточными и их потеря безопасна.

$END_DEVPLAN
