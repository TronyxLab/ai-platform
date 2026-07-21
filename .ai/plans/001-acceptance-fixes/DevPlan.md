# $START — DevPlan 001: Acceptance Fixes

<!--
$ARTIFACT_CONTRACT
  PURPOSE: Fix 4 root-cause defects from orchestrator-final-report.md (verdict DEGRADED)
  DESCRIPTION: Full stack recovery env-file, SSH forced-command verbs, backup-cron pre-pull skip, hermes-agent fallback build
  RATIONALE: Platform cannot recover after full restart (3/25 containers), pre-flight checks broken, pre-pull errors, hermes missing after VPS recreate
  ACCEPTANCE_CRITERIA:
    AC-1: `docker compose up -d` succeeds from systemd platform.service without manual --env-file
    AC-2: `make gate MODE=fast` passes with all changes
    AC-3: `ssh ci-deploy@host "exit"` returns 0 (SSH connectivity check works)
    AC-4: `ssh ci-deploy@host "ping"` returns "pong" (forced-command responds)
    AC-5: `make deploy PROJECT=<name>` pre-flight checks pass without error
    AC-6: backup-cron/status-page pre-pull logs "SKIP — Local build detected" instead of registry errors
    AC-7: `deploy_docker_module hermes-agent` succeeds when image missing from GHCR (fallback build)
  IMPLEMENTS: P1 (Full stack recovery), P2 (SSH forced-command), P3 (backup-cron pre-pull), P4 (hermes-agent fallback)
  IMPACTS:
    - core/internal/bootstrap/deploy-modules.sh (env-file, pre-pull skip, hermes L1 check)
    - core/bootstrap/systemd/platform.service (WorkingDirectory, EnvironmentFile)
    - core/entrypoints/deploy.sh (ping/exit verbs)
    - core/lib/vps-readiness.sh (ping verb usage)
    - .github/workflows/deploy-project.yml (status verb usage)
    - core/modules/backup-cron/scripts/warm-images.sh (local-build skip)
    - core/modules/hermes-agent/docker-compose.base.yml (build: section)
  REQUIRES: None (all changes self-contained, no new dependencies)
  TASK_SIZE: STANDARD (7 files, 4 problem areas, single wave with 7 parallel groups)
-->

# DevPlan 001: Acceptance Fixes

## Problem Statement

По результатам `orchestrator-final-report.md` (2026-07-21, verdict DEGRADED) выявлены 4 корневых дефекта:

| # | Дефект | Impact | Причина |
|---|--------|--------|---------|
| P1 | Full stack recovery — 3/25 контейнеров | CRITICAL | `deploy-modules.sh` передаёт `--env-file` только для secrets.env, platform `.env` (142 переменные) не передаётся. Systemd unit не имеет `WorkingDirectory`. |
| P2 | `make deploy` pre-flight broken | HIGH | `ssh ci-deploy@host "exit"` интерпретируется forced-command'ом как deploy проекта "exit". Нет глаголов `ping`/`exit` в парсере. |
| P3 | backup-cron pre-pull ошибки | MEDIUM | `_pre_pull_images()` и `warm-images.sh` пытаются pull'ить локально-собираемые образы (backup-cron, status-page) из registry. |
| P4 | hermes-agent отсутствует на VPS | CRITICAL | Fallback `docker compose build` не работает — в compose-файле нет секции `build:`. L1 образ может отсутствовать локально. |

---

## Design Decisions (Superposition Collapse)

| Проблема | Выбранное решение | Отклонённые альтернативы |
|----------|-------------------|------------------------|
| P1 | `--env-file /opt/platform/.env` в compose_args + `WorkingDirectory=/opt/platform` в systemd (belt+suspenders) | Только `--env-file` (не покрывает systemd auto-recovery), только `EnvironmentFile` (конфликт с compose interpolation) |
| P2 | Добавить глаголы `ping`/`exit` в `deploy.sh` + переписать CI callers на `status`/`verify` | Только глаголы (не решает CI raw-команды), только callers (не решает vps-readiness.sh) |
| P3 | `grep -q 'build:'` в compose-файле перед pull → skip | `--ignore-buildable` флаг (требует Compose v2.20+), поле `image_source` в module.yaml (миграция 13 модулей) |
| P4 | Добавить `build:` секцию в `docker-compose.base.yml` для hermes-agent + проверка L1 в deploy-modules.sh | Полный `hermes-build-context` в fallback (дублирование логики), гарантировать GHCR (не решает автономное восстановление) |

---

## $TASKS

### Wave 1: All Fixes (7 parallel groups — all files distinct)

#### Group 1: `deploy-modules.sh` — env-file + pre-pull skip + hermes L1

**TASK-1.1 — Добавить platform `.env` в compose_args**

Файл: `core/internal/bootstrap/deploy-modules.sh`, строки 421-425

Текущий код передаёт только secrets.env:
```bash
local env_file="${SECRETS_ENV_FILE:-/run/platform/secrets.env}"
local compose_args=("-f" "$compose_file")
if [[ -f "$env_file" ]]; then
    compose_args+=("--env-file" "$env_file")
fi
```

Добавить второй `--env-file` для platform `.env`:
```bash
local env_file="${SECRETS_ENV_FILE:-/run/platform/secrets.env}"
local platform_env="${PLATFORM_ROOT:-/opt/platform}/.env"
local compose_args=("-f" "$compose_file")
if [[ -f "$env_file" ]]; then
    compose_args+=("--env-file" "$env_file")
fi
if [[ -f "$platform_env" ]]; then
    compose_args+=("--env-file" "$platform_env")
fi
```

⚠️ **Порядок важен:** `--env-file` работает как `source` — последний переопределяет предыдущий. `secrets.env` должен быть ПЕРВЫМ (низший приоритет), platform `.env` — ВТОРЫМ (переопределяет переменные с fallback'ами, но пароли из secrets.env уже заданы). Docker Compose обрабатывает `--env-file` в порядке LIFO (последний — высший приоритет).

🚨 **Перекрёстная проверка:** `_pre_pull_images()` (строка 1031-1034) тоже передаёт только secrets.env — добавить `platform_env` и там:
```bash
local env_file="${SECRETS_ENV_FILE:-/run/platform/secrets.env}"
[[ -f "$env_file" ]] && pull_args+=("--env-file" "$env_file")
[[ -f "$platform_env" ]] && pull_args+=("--env-file" "$platform_env")
```

**TASK-1.2 — Пропускать локально-собираемые модули в `_pre_pull_images()`**

Файл: `core/internal/bootstrap/deploy-modules.sh`, строки 1020-1044

После разрешения compose-файла (строка 1024-1029) и ДО вызова `docker compose pull`, добавить проверку:
```bash
# Skip modules with local build (no registry image — pull would fail)
if grep -q '^\s\+build:' "$compose_file" 2>/dev/null; then
    log_step "pre-pull:${mod_name}" "SKIP" "Local build detected (has build: section) — skipping pull"
    exit 0
fi
```

Локация: внутри subshell'а `( ... ) &`, после строки 1029 (проверка compose_file), до строки 1031 (pull_args).

**TASK-1.3 — Проверка L1 образа для hermes-agent перед fallback build**

Файл: `core/internal/bootstrap/deploy-modules.sh`, строки 448-473

Fallback `docker compose build` для hermes-agent требует наличия L1 образа (`hermes-agent-base:latest`) локально. Сейчас проверяется только L2 в registry → если L2 нет, вызывается build, но build упадёт если нет L1.

Добавить проверку L1 перед вызовом `docker compose build`:
```bash
if ! $_all_found; then
    # Ensure L1 base image exists locally (required for L1→L2 build)
    if ! docker image inspect hermes-agent-base:latest &>/dev/null 2>&1; then
        log_step "docker:${module_name}" "WARN" "L1 base image not found locally — attempting pull from GHCR"
        if ! docker pull ghcr.io/tronyx161/hermes-agent-base:latest 2>/dev/null; then
            log_step "docker:${module_name}" "BUILD" "L1 pull failed — building L1 from source"
            if ! docker compose "${compose_args[@]}" --profile "$module_name" -f "${module_dir}/docker-compose.base.yml" build \
                --build-arg CONTEXT="${CONTEXT:-personal}" 2>&1; then
                log_step "docker:${module_name}" "FAIL" "L1 build failed"
                return 1
            fi
        fi
    fi
    log_step "docker:${module_name}" "BUILD" "Building hermes-agent L1→L2 locally (fallback)"
    docker compose "${compose_args[@]}" --profile "$module_name" build 2>&1 || {
        log_step "docker:${module_name}" "FAIL" "Local build failed"
        return 1
    }
fi
```

---

#### Group 2: `platform.service` — WorkingDirectory + EnvironmentFile

**TASK-2.1 — Добавить `WorkingDirectory` и `EnvironmentFile` в systemd unit**

Файл: `core/bootstrap/systemd/platform.service`, строки 37-46

Текущий `[Service]`:
```ini
[Service]
Type=oneshot
RemainAfterExit=yes
ExecStart=/opt/platform/core/internal/bootstrap/deploy-modules.sh
ExecStop=/bin/bash -c 'for f in /opt/platform/core/modules/*/docker-compose.base.yml; do ... done'
Restart=on-failure
RestartSec=30s
User=root
StandardOutput=journal
StandardError=journal
```

Добавить:
```ini
[Service]
Type=oneshot
RemainAfterExit=yes
WorkingDirectory=/opt/platform
EnvironmentFile=-/opt/platform/.env
ExecStart=/opt/platform/core/internal/bootstrap/deploy-modules.sh
ExecStop=...
Restart=on-failure
RestartSec=30s
TimeoutStartSec=600
User=root
StandardOutput=journal
StandardError=journal
```

Комментарий к изменениям:
- `WorkingDirectory=/opt/platform` — Docker Compose auto-loads `.env` из CWD
- `EnvironmentFile=-/opt/platform/.env` — префикс `-` означает "не ошибка если файла нет" (graceful degradation на cold bootstrap)
- `TimeoutStartSec=600` — 10 минут на полный bootstrap с pull'ами образов (было без лимита — systemd default 90s)

---

#### Group 3: `deploy.sh` — ping/exit verbs

**TASK-3.1 — Добавить обработку глаголов `ping` и `exit`**

Файл: `core/entrypoints/deploy.sh`, функция `parse_verb()`, перед `case "$first_token" in` (строка 81)

После очистки `cleaned` и перед `case`, добавить:
```bash
# ── Ping verb — pre-flight connectivity check ──
if [[ "$cleaned" == "ping" ]]; then
    echo "pong"
    exit 0
fi

# ── Exit verb — SSH connectivity test (no-op success) ──
if [[ "$cleaned" == "exit" ]]; then
    exit 0
fi
```

Локация: между строкой 77 (`if [[ -z "$cleaned" ]]`) и строкой 79 (`local first_token="${cleaned%% *}"`).

---

#### Group 4: `vps-readiness.sh` — ping verb

**TASK-4.1 — Исправить check 1 (SSH connectivity)**

Файл: `core/lib/vps-readiness.sh`, строка 82

Заменить `"exit"` на `"exit"` (уже правильный глагол после TASK-3.1 — просто заработает). Без изменений в коде.

**TASK-4.2 — Исправить check 2 (forced-command ping)**

Файл: `core/lib/vps-readiness.sh`, строки 95-103

Заменить:
```bash
ping_result="$(ssh ... "platform-deliver --ping" 2>&1)" || true
if echo "${ping_result}" | grep -qi "pong\|PONG\|ready"; then
```

На:
```bash
ping_result="$(ssh ... "ping" 2>&1)" || true
if echo "${ping_result}" | grep -q "pong"; then
```

---

#### Group 5: `deploy-project.yml` — status/verify verbs

**TASK-5.1 — Check VPS readiness через глагол `status`**

Файл: `.github/workflows/deploy-project.yml`, строки 83-93

Заменить raw shell:
```yaml
- name: Check VPS readiness
  ...
  run: |
    ssh ... "test -d /opt/projects && echo 'VPS ready' || ..."
```

На использование глагола `status`:
```yaml
- name: Check VPS readiness
  env:
    NODE_HOST_MAP: ${{ vars.NODE_HOST_MAP }}
    CI_DEPLOY_KEY: ${{ secrets.CI_DEPLOY_KEY }}
  run: |
    set -euo pipefail
    echo "[IMP:9][preflight] Checking VPS readiness via forced-command status verb..."
    STATUS_JSON=$(ssh -i ~/.ssh/ci_deploy_key -o StrictHostKeyChecking=accept-new -o ConnectTimeout=10 \
      ci-deploy@${{ env.ssh_host }} "status ${{ inputs.project_name }}" 2>&1) || true
    echo "[IMP:8][preflight] Status response: ${STATUS_JSON}"
    echo "${STATUS_JSON}" | python3 -c "
import json, sys
d = json.load(sys.stdin)
status = d.get('status', '')
if status not in ('found', 'stub'):
    sys.exit(1)
"
    echo "[IMP:9][preflight] VPS readiness check passed (status: $(echo "${STATUS_JSON}" | python3 -c "import json,sys; print(json.load(sys.stdin).get('status'))"))"
  shell: bash
```

**TASK-5.2 — Verify deliver через глагол `status`**

Файл: `.github/workflows/deploy-project.yml`, строки 114-124

Заменить raw shell `test -f` на тот же подход с `status`:
```yaml
- name: Verify deliver
  if: success()
  run: |
    set -euo pipefail
    echo "[IMP:9][verify-deliver] Verifying deliver via forced-command status verb..."
    STATUS_JSON=$(ssh -i ~/.ssh/ci_deploy_key -o StrictHostKeyChecking=accept-new \
      ci-deploy@${{ env.ssh_host }} "status ${{ inputs.project_name }}" 2>&1) || true
    echo "${STATUS_JSON}" | python3 -c "
import json, sys
d = json.load(sys.stdin)
status = d.get('status', '')
if status in ('found', 'stub'):
    print(f'[IMP:9][verify-deliver] Compose file verified on VPS (status: {status})')
else:
    print(f'[IMP:10][verify-deliver] FATAL: project not found on VPS (status: {status})')
    sys.exit(1)
"
  shell: bash
```

---

#### Group 6: `warm-images.sh` — local-build skip

**TASK-6.1 — Пропускать локально-собираемые модули**

Файл: `core/modules/backup-cron/scripts/warm-images.sh`, строки 64-85

В цикле `for mod_name in "${MODULES[@]}"; do`, после разрешения compose_file (строка 74) и до вызова `docker compose pull` (строка 78), добавить:
```bash
# Skip modules with local build (no registry image — pull would fail)
if grep -q '^\s\+build:' "$compose_file" 2>/dev/null; then
    log "SKIP" "Local build module '${mod_name}' — skipping pull"
    continue
fi
```

---

#### Group 7: `hermes-agent docker-compose.base.yml` — build: section

**TASK-7.1 — Добавить `build:` секцию для fallback-сборки**

Файл: `core/modules/hermes-agent/docker-compose.base.yml`, после строки 74 (после `platform: linux/amd64`)

Добавить секцию `build:`:
```yaml
    # ⚠️ TRAP[DECISION] · 2026-07-21 · — · build: section for fallback when GHCR image unavailable
    # · Previously: "No build section — context overlay pre-built in tronyx-lab CI, never built locally"
    # · Revision: VPS must be able to rebuild hermes-agent from source when GHCR image is absent
    # ·   (e.g., after VPS recreation, registry outage, or disaster recovery).
    # · build: section uses L1 base image (hermes-agent-base:latest) — must exist locally
    # ·   (deploy-modules.sh ensures L1 via pull from GHCR or local build before compose build).
    # · context: ${PLATFORM_ROOT:-/opt/platform} — full platform source tree for L2 Dockerfile
    # · Rev: if build-from-source becomes unreliable on low-resource VPS, revert to registry-only
    build:
      context: ${PLATFORM_ROOT:-/opt/platform}
      dockerfile: core/modules/hermes-agent/context/Dockerfile
      args:
        CONTEXT: ${CONTEXT:-personal}
```

⚠️ **TRAP:** Строка 51 в текущем compose-файле гласит *"No build section — context overlay pre-built in tronyx-lab CI, never built locally."* Этот комментарий нужно обновить, отразив пересмотр решения.

---

## $TEST_SPEC

Существующие тесты, которые должны остаться зелёными:
- `make gate MODE=full` — 1365 tests
- `make test MARKER=predeploy` — pre-deploy gate tests
- `make test MARKER=smoke` — smoke tests (включая hermes-agent)

Новые тесты (добавляются группой, имплементирующей TASK):
- `tests/test_deploy_module_env.py::test_compose_args_has_platform_env` — проверяет что `deploy_docker_module` передаёт `--env-file` для platform `.env`
- `tests/test_deploy_module_env.py::test_prepull_skips_local_build` — проверяет что `_pre_pull_images` скипает модули с `build:` в compose
- `tests/test_deploy_verbs.py::test_ping_verb_returns_pong` — `parse_verb "ping"` возвращает "pong"
- `tests/test_deploy_verbs.py::test_exit_verb_returns_zero` — `parse_verb "exit"` возвращает 0
- `tests/test_vps_readiness.py::test_ping_check_uses_pong` — vps-readiness check 2 ожидает "pong"

---

## Rollback Plan

Все изменения — аддитивные (добавление строк, не удаление логики):
- `deploy-modules.sh`: добавить `--env-file`, skip-логику, L1-проверку — старые пути не затронуты
- `platform.service`: добавить `WorkingDirectory`, `EnvironmentFile`, `TimeoutStartSec`
- `deploy.sh`: добавить 2 if-блока перед case
- `vps-readiness.sh`: заменить строку вызова `"platform-deliver --ping"` на `"ping"`, заменить grep-паттерн
- `deploy-project.yml`: заменить raw shell на status verb
- `warm-images.sh`: добавить `grep -q build:` + `continue`
- `hermes-agent compose`: добавить `build:` секцию (не заменяет существующий `image:`)

Откат: `git revert` коммита. Все изменения в одном коммите на feature-ветке.
