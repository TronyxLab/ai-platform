# 025-DevPlan: Deploy sequencing & reliability — 0 новых make-таргетов, 6 волн

$START_DEVPLAN

$ARTIFACT_CONTRACT
PURPOSE:               Устранить 7 системных причин недетерминированного поведения цепочки bootstrap↔deploy (H1-H7) через расширение существующих make-таргетов без создания новых. Fusion S7: `deploy` +NODE/+LAUNCH, `bootstrap-node` +AUTO_RECONCILE, `converge` +RECONCILE, `node-update` +RECONCILE. 0 новых таргетов в Makefile.
DESCRIPTION:           Архитектурная суперпозиция (7 вариантов, см. сессию) выбрала fusion-подход S7: reconciliation — внутренний скрипт `core/internal/deploy/reconcile-projects.sh`, вызываемый через флаги существующих entrypoint'ов. Полный цикл «новый проект → работает на VPS» = `make deploy PROJECT=<name> NODE=<node> LAUNCH=1` (одна команда). Восстановление после bootstrap = `make bootstrap-node NODE=<node> AUTO_RECONCILE=1` (авто-развёртывание всех stub-проектов). Ручное восстановление = `make converge NODE=<node> RECONCILE=1`. Все волны W1-W6 из Brief 025 сохраняются, но W6 (process unification) реализуется через расширение существующих таргетов, а не через новые `project-launch`/`launch-all`.
RATIONALE:             Fusion S7 выбран оператором из 7 вариантов суперпозиции. Ключевое преимущество: 0 новых make-таргетов → не требуется расширять entrypoint-manifest.yaml, allowed_verbs, AGENTS.md таблицу канонических операций, CI gates (no-unregistered-entrypoint). Reconciliation-скрипт существует как internal, не как entrypoint — соответствует слою internal/deploy/ (рядом с deploy-project.sh). Флаги (`--auto-reconcile`, `--reconcile`, `LAUNCH=1`) семантически прозрачны и backward-совместимы (отсутствие флага = текущее поведение).
ACCEPTANCE_CRITERIA:   1. `make deploy PROJECT=<name> NODE=<node>` выполняет pre-flight VPS readiness check перед git push. Если VPS не готова — exit 1 с сообщением «Run: make bootstrap-node NODE=<node> first».  2. `converge.sh`: exit 0=ok, exit 1=warnings (не блокирует bootstrap), exit 2=errors (блокирует). node-lifecycle.sh step_15 реагирует только на exit 2.  3. `converge.sh --report-only`: stub-проекты → `status: awaiting_deploy`, реальные → `status: converged`.  4. `make converge NODE=<node> RECONCILE=1`: stub-проекты с образом в GHCR → deployed, без образа → WARN. Идемпотентен.  5. `make bootstrap-node NODE=<node> AUTO_RECONCILE=1`: после converge → авто-деплой всех stub-проектов.  6. CI `deploy-project.yml`: каждый шаг `set -euo pipefail`, post-deliver verify (docker-compose.yml exists), fail fast.  7. `make deploy PROJECT=<name> NODE=<node> LAUNCH=1`: одна команда → pre-flight → CI deploy → wait → verify → выводит URL.  8. `make gate MODE=fast` — зелёный.  9. 0 новых имён в allowed_verbs entrypoint-manifest.yaml.
IMPLEMENTS:            Brief 025 (01-Brief.md), архитектурная суперпозиция H1-H7, fusion S7 (выбор оператора), инварианты 1 (Makefile-фасад), 6 (bootstrap-node идемпотентный), 9 (тестовый сервер recreatable).
IMPACTS:               `core/lib/vps-readiness.sh` (CREATE), `core/internal/deploy/reconcile-projects.sh` (CREATE), `core/internal/bootstrap/converge.sh` (MODIFY: exit semantics + stub detection + --reconcile), `core/internal/bootstrap/node-lifecycle.sh` (MODIFY: step_15 exit handling + reconcile step), `core/entrypoints/deploy.sh` (MODIFY: verb contract + platform-deliver stub-aware), `core/entrypoints/deploy-project.sh` (MODIFY: pre-flight + --launch), `core/entrypoints/converge.sh` (MODIFY: --reconcile passthrough), `core/entrypoints/bootstrap.sh` (MODIFY: --auto-reconcile flag), `core/entrypoints/node-update.sh` (MODIFY: RECONCILE passthrough), `Makefile` (MODIFY: deploy +NODE/+LAUNCH, bootstrap-node +AUTO_RECONCILE, converge +RECONCILE, node-update +RECONCILE), `.github/workflows/deploy-project.yml` (MODIFY: set -euo pipefail + post-deliver verify), `tests/test_sequencing.py` (CREATE), `tests/test_reconcile.py` (CREATE), `tests/test_converge_exit.py` (CREATE).
REQUIRES:              Ветка от origin/main, `make gate MODE=fast` зелёный до начала, working tree чистый. DevPlan 024 выполнен (минимально: W2 project scaffold, W0 --skip-provision). Доступ к VPS через SSH для pre-flight. CI_DEPLOY_KEY в CI secrets. S7 fusion подтверждён оператором.
$END_ARTIFACT_CONTRACT

---

## $DOCUMENT_PLAN

```
$START_DOCUMENT_PLAN
### Document Plan
**SECTION_GOALS:**
- GOAL Wave 1: VPS readiness pre-flight — создать vps-readiness.sh, внедрить в deploy/deploy-project/CI → GOAL_W1
- GOAL Wave 2: Converge exit semantics — разделить exit 0/1/2, node-lifecycle реагирует на exit 2 → GOAL_W2
- GOAL Wave 3: Stub detection — converge R3 и deploy-project.sh --status различают stub/real → GOAL_W3
- GOAL Wave 4: Reconciliation — reconcile-projects.sh, флаги в converge/bootstrap-node/node-update → GOAL_W4
- GOAL Wave 5: CI hardening — set -euo pipefail, post-deliver verify → GOAL_W5
- GOAL Wave 6: Process unification — deploy +LAUNCH, bootstrap-node +AUTO_RECONCILE → GOAL_W6
**SECTION_USE_CASES:**
- USE_CASE deploy with pre-flight check → UC_PREFLIGHT
- USE_CASE converge distinguishes stubs from deployed projects → UC_STUB_DETECT
- USE_CASE post-bootstrap auto-recovery of stub projects → UC_AUTO_RECONCILE
- USE_CASE manual reconciliation of stubs → UC_MANUAL_RECONCILE
- USE_CASE one-command full project launch → UC_LAUNCH
- USE_CASE CI deploy fail-fast on unready VPS → UC_CI_FAILFAST
$END_DOCUMENT_PLAN
```

---

## 1. Волна 1 (P0): VPS readiness pre-flight — contract enforcement

### 1.1. Создать `core/lib/vps-readiness.sh`

Общий модуль pre-flight проверок для переиспользования из Makefile, CI и entrypoints.

```bash
# core/lib/vps-readiness.sh — shared VPS readiness pre-flight checks
#
# Usage:
#   source core/lib/vps-readiness.sh
#   check_vps_ready "tronyx-vps"              # exit 0 if ready, exit 1 with diagnostics
#   check_vps_ready "tronyx-vps" --json       # JSON output for CI
#   check_vps_ready "tronyx-vps" --quick      # SSH-only check (no docker, no /opt/projects)
#
# Checks (in order):
#   1. SSH доступность ci-deploy@<host> (BatchMode=yes, connect timeout 10s)
#   2. Forced-command отвечает на "platform-deliver --ping" (значит core доставлен)
#   3. /opt/projects/ существует и writable (ci-deploy)
#   4. (опционально, только не --quick) Docker daemon отвечает
#
# Diagnostics: при FAIL — читаемое сообщение + remediation hint
```

**API:**
```bash
check_vps_ready() {
    local node_name="$1"
    local output_mode="text"  # text | json
    local quick_mode=false
    # ... resolve SSH host from NODE_HOST_MAP or node.yaml ...
    # ... run checks, return 0 or 1 ...
}
```

**Remediation hints (встроенные в диагностику):**
- SSH unavailable → "VPS unreachable. Check: ssh ci-deploy@<host>"
- No forced-command → "Core not delivered. Run: make bootstrap-node NODE=<node>"
- No /opt/projects/ → "Project base missing. Run: make bootstrap-node NODE=<node>"
- Docker unavailable → "Docker not running. Run: systemctl start docker on VPS"

### 1.2. Интегрировать в `make deploy`

**Makefile — deploy target:**
```makefile
deploy:
	@# ... существующие проверки PROJECT ...
	@if [ -n "$(NODE)" ]; then \
		echo "[IMP:7][make][deploy] Pre-flight: checking VPS readiness for NODE=$(NODE)..."; \
		source $(_platform_root)/core/lib/vps-readiness.sh && \
		check_vps_ready "$(NODE)" || { \
			echo "[IMP:10][make][deploy] FATAL: VPS not ready. Run: make bootstrap-node NODE=$(NODE) first" >&2; \
			exit 1; \
		}; \
		echo "[IMP:9][make][deploy] VPS ready — proceeding with git push"; \
	fi
	@cd "$(PROJECT)" && git push origin main
```

- NODE не указан → текущее поведение (только git push, pre-flight skipped)
- NODE указан → pre-flight перед git push
- DRY_RUN=1 → только pre-flight, без git push

### 1.3. Интегрировать в `deploy-project.sh` entrypoint

Добавить pre-flight check перед `deliver_payload()`:
```bash
# В main() после resolve_node_host(), перед deliver_payload()
if [[ "$SKIP_VERIFY" -ne 1 ]]; then
    source "${SCRIPT_DIR}/../lib/vps-readiness.sh"
    check_vps_ready "$NODE" --quick || {
        log_imp 10 "preflight" "FATAL: VPS readiness check failed"
        exit 2
    }
fi
```

### 1.4. Интегрировать в CI `deploy-project.yml`

Добавить шаг перед `deliver-payload`:
```yaml
- name: Check VPS readiness
  run: |
    set -euo pipefail
    echo "[IMP:9][preflight] Checking VPS readiness for ${{ env.ssh_host }}..."
    ssh -i ~/.ssh/ci_deploy_key -o StrictHostKeyChecking=accept-new -o ConnectTimeout=10 \
      ci-deploy@${{ env.ssh_host }} "test -d /opt/projects && echo 'VPS ready' || (echo 'VPS not bootstrapped: /opt/projects missing' && exit 1)"
  shell: bash
```

### Файлы волны 1

| Файл | Действие | Описание |
|------|----------|----------|
| `core/lib/vps-readiness.sh` | CREATE | Shared pre-flight: SSH, forced-command, /opt/projects, Docker |
| `Makefile` | MODIFY | deploy target: +NODE → pre-flight check |
| `core/entrypoints/deploy-project.sh` | MODIFY | pre-flight перед deliver_payload |
| `.github/workflows/deploy-project.yml` | MODIFY | check-vps-readiness step |

---

## 2. Волна 2 (P0): Converge exit semantics — warnings ≠ errors

### 2.1. Разделить exit codes в `converge.sh`

**Текущее состояние:** `CONVERGE_EXIT_CODE` — единая переменная, 0=converged, 1=mutations, 2=errors. Exit 2 уже выставляется для фатальных ошибок (EACCES на core/, mkdir fail, Docker daemon unavailable, nginx -t fail), но нет разделения на warnings vs errors — exit 1 (mutations) и exit 2 (errors) смешаны, converge не различает «применены мутации» и «обнаружены предупреждения без мутаций».

**Новое поведение:**
```bash
# Глобалы
CONVERGE_EXIT_CODE=0       # 0=converged, 1=warnings, 2=errors
CONVERGE_HAS_ERRORS=false  # отдельный флаг для CRITICAL failures
CONVERGE_HAS_WARNINGS=false

# В каждом R-unit:
# - ok/converged → без изменений
# - warn (R6 legacy vhosts, R2 permissions drift) → CONVERGE_HAS_WARNINGS=true
# - fail (R3 mkdir failed, R1 fatal permissions) → CONVERGE_HAS_ERRORS=true, CONVERGE_EXIT_CODE=2

# В main(): финальный exit code
if $CONVERGE_HAS_ERRORS; then
    echo "[IMP:9][converge][main] Converge complete with ERRORS (exit 2)" >&2
    exit 2
elif $CONVERGE_HAS_WARNINGS; then
    echo "[IMP:9][converge][main] Converge complete with WARNINGS (exit 1)" >&2
    exit 1
else
    echo "[IMP:9][converge][main] Converge complete — fully converged (exit 0)" >&2
    exit 0
fi
```

**Маппинг severity → exit impact:**
| R-unit | Тип failure | Severity | Exit impact |
|--------|------------|----------|:-----------:|
| R1 | permissions fix failed (chmod) | warn | exit 1 |
| R1 | permissions fatal (EACCES на core/) | error | exit 2 |
| R2 | audit.log permissions drift | warn | exit 1 |
| R2 | symlink attack detected | error | exit 2 |
| R3 | stub already exists (not overwritten) | info | no impact |
| R3 | mkdir -p failed | error | exit 2 |
| R4 | Docker daemon unavailable | error | exit 2 |
| R4 | proxy-net wrong driver | warn | exit 1 |
| R5 | /etc/hosts drift detected | warn | exit 1 |
| R6 | vhost hash mismatch | warn | exit 1 |
| R6 | nginx -t failed | error | exit 2 |

### 2.2. Обновить `node-lifecycle.sh` step_15

**Текущее:** `if bash converge.sh ...; then ...; else ...; fi` — exit 0 → `then` → step_done, exit 1/2 → `else` → step_warn "failed". Exit 1 (mutations applied) неверно трактуется как failure. Также мёртвый код: `converge_rc=$?` внутри `then`-ветки всегда 0 (exit code успешного `if`), поэтому внутренние `elif [[ $converge_rc -eq 1 ]]` и `else` недостижимы.

**Новое:**
```bash
step_15_converge() {
    # ... существующий код ...
    if bash "${converge_script}" "${converge_args[@]}" 2>&1; then
        step_done "converge" "Converge complete — no errors"
    else
        local converge_rc=$?
        if [[ $converge_rc -eq 2 ]]; then
            # ERROR — блокирует только в init-режиме
            if [[ "${MODE}" == "init" ]]; then
                step_warn "converge" "Converge CRITICAL errors (exit 2) — bootstrap continues but node is DEGRADED"
            else
                step_warn "converge" "Converge CRITICAL errors (exit 2) — node may be DEGRADED"
            fi
        elif [[ $converge_rc -eq 1 ]]; then
            # WARNINGS — не блокирует
            step_done "converge" "Converge complete with warnings (exit 1) — non-critical drift"
        fi
    fi
}
```

### Файлы волны 2

| Файл | Действие | Описание |
|------|----------|----------|
| `core/internal/bootstrap/converge.sh` | MODIFY | `CONVERGE_HAS_ERRORS`, `CONVERGE_HAS_WARNINGS`, финальный exit mapping |
| `core/internal/bootstrap/node-lifecycle.sh` | MODIFY | step_15: различать exit 1 vs exit 2 |

---

## 3. Волна 3 (P0): Stub detection — converge + deploy-project

### 3.1. Stub detection helper в converge.sh

```bash
# region FUNC__is_stub
## @purpose  Check if ai-platform.yaml is a GENERATED-STUB (not real config)
## @return 0 if stub, 1 if real file or missing
_is_stub() {
    local ai_platform_yaml="$1"
    if [[ -f "$ai_platform_yaml" ]]; then
        head -1 "$ai_platform_yaml" 2>/dev/null | grep -q "GENERATED-STUB"
    else
        return 1  # file missing = not a stub (no file at all)
    fi
}
# endregion FUNC__is_stub
```

### 3.2. Изменить R3 reconcile_projects — различать stub vs deployed

**Текущее (строка 531-533):**
```bash
else
    echo "[IMP:7][converge][${unit}] SKIP: ${stub_file} already exists (not overwritten)" >&2
fi
```

**Новое:**
```bash
else
    if _is_stub "${stub_file}"; then
        echo "[IMP:7][converge][${unit}] STUB: ${stub_file} is a GENERATED-STUB (awaiting deploy)" >&2
        report_add "${unit}" "awaiting_deploy" "Project ${proj_name}: stub present, awaiting CI deploy"
    else
        echo "[IMP:7][converge][${unit}] SKIP: ${stub_file} already exists (real config — deployed)" >&2
        report_add "${unit}" "converged" "Project ${proj_name}: deployed"
    fi
fi
```

### 3.3. `--report-only` вывод для stub-проектов

В JSON-отчёте `--report-only`: для stub-проектов `"status": "awaiting_deploy"`, для реальных `"status": "converged"`.

### 3.4. `deploy-project.sh --status` — stub-aware

В `core/internal/deploy/deploy-project.sh` (режим `--status`): если `ai-platform.yaml` — stub → `"state": "stub"` вместо `"state": "unknown"`.

### Файлы волны 3

| Файл | Действие | Описание |
|------|----------|----------|
| `core/internal/bootstrap/converge.sh` | MODIFY | `_is_stub()` helper, R3 stub-vs-deployed различие, --report-only JSON |
| `core/internal/deploy/deploy-project.sh` | MODIFY | --status: stub detection |
| `core/entrypoints/deploy.sh` | MODIFY | verb contract: --status passthrough |

---

## 4. Волна 4 (P1): Post-bootstrap reconciliation

### 4.1. Создать `core/internal/deploy/reconcile-projects.sh`

**Internal-скрипт, НЕ entrypoint.** Вызывается из:
- `converge.sh --reconcile` (через entrypoint `converge.sh`)
- `bootstrap.sh --auto-reconcile` (через entrypoint `bootstrap.sh`)
- `node-lifecycle.sh` step_15+ (при `AUTO_RECONCILE=true`)

```bash
# core/internal/deploy/reconcile-projects.sh
# GREP_SUMMARY: reconcile-projects stub-detection ghcr-check auto-deploy idempotent recovery
# STRUCTURE: ▶ read node.yaml#projects → ◇ for each: _is_stub? → ◇ ghcr image exists? → ⚡ platform-deliver + compose up → ◇ healthcheck → ⎋ summary
# region MODULE_CONTRACT
## @purpose  Post-bootstrap recovery: detect stub projects in /opt/projects/,
##           check GHCR for Docker images, deploy if found. Idempotent.
## @scope    Called ONLY from converge.sh --reconcile, bootstrap.sh --auto-reconcile,
##           or node-lifecycle.sh step_15+ (AUTO_RECONCILE=true). Not an entrypoint.
## @invariants
##   - Reads node.yaml#projects — does NOT scan filesystem blindly
##   - For each project: _is_stub() → docker manifest inspect → platform-deliver + compose up
##   - Stub without GHCR image → WARN "awaiting first CI deploy"
##   - Already deployed (real ai-platform.yaml) → SKIP
##   - Idempotent: repeat run = no-op for deployed projects
##   - Uses same ci-deploy SSH key as deploy-project.sh
##   - Audit log: RECONCILE-<project> entries
## @rationale Separate internal script (not entrypoint) per fusion S7 decision.
##            Called through existing entrypoints with --reconcile/--auto-reconcile flags.
##            Lives in internal/deploy/ alongside deploy-project.sh — same layer, same concern.
# endregion MODULE_CONTRACT

set -euo pipefail

reconcile_projects() {
    local node_name="$1"
    local node_yaml="$2"
    local dry_run="${3:-false}"
    
    # ── Extract projects from node.yaml ──
    local projects_json
    projects_json="$(python3 - "$node_yaml" <<'PYEOF' ...)"
    
    # ── For each project ──
    while IFS= read -r proj_name; do
        local proj_dir="/opt/projects/${proj_name}"
        local ai_yaml="${proj_dir}/ai-platform.yaml"
        
        # ── Check if stub ──
        if [[ -f "$ai_yaml" ]]; then
            if ! head -1 "$ai_yaml" | grep -q "GENERATED-STUB"; then
                echo "[IMP:7][reconcile][${proj_name}] SKIP: real ai-platform.yaml (already deployed)" >&2
                continue
            fi
        else
            echo "[IMP:7][reconcile][${proj_name}] SKIP: no ai-platform.yaml (project dir may not exist)" >&2
            continue
        fi
        
        echo "[IMP:9][reconcile][${proj_name}] Stub detected — checking GHCR for Docker image..." >&2
        
        # ── Check GHCR ──
        # Extract context from project path (projects/<org>/<name>/) or node.yaml
        local context="${ORG:-tronyx-lab}"
        local ghcr_image="ghcr.io/${context}/${proj_name}:latest"
        
        if docker manifest inspect "${ghcr_image}" &>/dev/null 2>&1; then
            echo "[IMP:9][reconcile][${proj_name}] Image found: ${ghcr_image} — deploying" >&2
            
            if [[ "$dry_run" != "true" ]]; then
                # ── Deliver + deploy (same mechanism as deploy-project.sh) ──
                # Use platform-deliver forced-command to write real ai-platform.yaml
                # Then docker compose pull && up -d
                deploy_project_direct "${proj_name}" "${context}" || {
                    echo "[IMP:10][reconcile][${proj_name}] FAIL: deploy failed" >&2
                    continue
                }
                echo "[IMP:9][reconcile][${proj_name}] DONE: stub → deployed" >&2
            else
                echo "[IMP:8][reconcile][${proj_name}] DRY-RUN: would deploy ${ghcr_image}" >&2
            fi
        else
            echo "[IMP:8][reconcile][${proj_name}] WARN: No image in GHCR — awaiting first CI deploy" >&2
        fi
    done <<< "${project_list}"
}
```

### 4.2. Интегрировать `--reconcile` в `converge.sh` entrypoint

```bash
# core/entrypoints/converge.sh — добавить парсинг --reconcile
while [[ $# -gt 0 ]]; do
    case "$1" in
        --reconcile) RECONCILE_MODE=true; shift ;;
        # ... существующие кейсы ...
    esac
done

# После execute_remote_converge:
if [[ "${RECONCILE_MODE:-false}" == "true" ]]; then
    echo "[IMP:9][converge][entrypoint] Reconciling stub projects..." >&2
    # Вызвать reconcile-projects.sh (или передать флаг на сервер)
    execute_remote_reconcile "${NODE_NAME}"
fi
```

### 4.3. Интегрировать `--auto-reconcile` в `bootstrap.sh`

```bash
# core/entrypoints/bootstrap.sh — добавить флаг
--auto-reconcile) AUTO_RECONCILE=true; shift ;;

# Передать в node-lifecycle.sh:
if [[ "${AUTO_RECONCILE:-false}" == "true" ]]; then
    a+=(--auto-reconcile)
fi
```

### 4.4. Интегрировать в `node-lifecycle.sh` step_15

После converge (step_15) — опциональный вызов reconcile:
```bash
step_15_converge() {
    # ... существующий converge ...
    
    # ── Optional: reconcile stub projects ──
    if [[ "${AUTO_RECONCILE:-false}" == "true" ]]; then
        step_start "reconcile-projects" "Auto-reconciling stub projects after converge"
        local reconcile_script="${CORE_DIR}/internal/deploy/reconcile-projects.sh"
        if [[ -f "$reconcile_script" ]]; then
            source "$reconcile_script"
            reconcile_projects "$NODE_NAME" "$NODE_YAML"
            step_done "reconcile-projects" "Stub reconciliation complete"
        else
            step_warn "reconcile-projects" "reconcile-projects.sh not found"
        fi
    fi
}
```

### 4.5. Makefile — флаги

```makefile
# bootstrap-node: добавить AUTO_RECONCILE
bootstrap-node:
	@... bootstrap.sh \
		... \
		$(if $(filter 1,$(AUTO_RECONCILE)),--auto-reconcile)

# converge: добавить RECONCILE
converge:
	@bash core/entrypoints/converge.sh --node $(NODE) \
		$(if $(DRY_RUN),--dry-run,) \
		$(if $(filter 1,$(RECONCILE)),--reconcile)

# node-update: добавить RECONCILE
node-update:
	@... node-update.sh \
		... \
		$(if $(filter 1,$(RECONCILE)),--reconcile)
```

### Файлы волны 4

| Файл | Действие | Описание |
|------|----------|----------|
| `core/internal/deploy/reconcile-projects.sh` | CREATE | Internal: stub→deployed для всех проектов из node.yaml |
| `core/entrypoints/converge.sh` | MODIFY | +`--reconcile` флаг → вызов reconcile |
| `core/entrypoints/bootstrap.sh` | MODIFY | +`--auto-reconcile` флаг → передача в node-lifecycle |
| `core/internal/bootstrap/node-lifecycle.sh` | MODIFY | step_15: +reconcile step при AUTO_RECONCILE=true |
| `core/entrypoints/node-update.sh` | MODIFY | +`--reconcile` passthrough |
| `core/internal/bootstrap/remote-cmd.sh` | MODIFY | +`execute_remote_reconcile()` для SSH прокси |
| `Makefile` | MODIFY | +`AUTO_RECONCILE` в bootstrap-node, +`RECONCILE` в converge/node-update |

---

## 5. Волна 5 (P1): CI failure visibility hardening

### 5.1. `deploy-project.yml` — hardening

**Изменения:**

1. **Все шаги с `set -euo pipefail`** — уже есть в deliver-payload (строка 75), нужно добавить в остальные.

2. **Post-deliver verify step** — добавить проверку файла после deliver:
```yaml
- name: Verify deliver
  if: success()
  run: |
    set -euo pipefail
    ssh -i ~/.ssh/ci_deploy_key -o StrictHostKeyChecking=accept-new \
      ci-deploy@${{ env.ssh_host }} \
      "test -f /opt/projects/${{ inputs.project_name }}/docker-compose.yml || test -f /opt/projects/${{ inputs.project_name }}/compose.yaml" \
      || { echo "[IMP:10][verify-deliver] FATAL: compose file missing on VPS — deliver may have failed"; exit 1; }
    echo "[IMP:9][verify-deliver] Compose file verified on VPS"
  shell: bash
```

3. **Deploy step — fail-fast:**
```yaml
- name: SSH deploy
  uses: appleboy/ssh-action@v1.2.5
  with:
    host: ${{ env.ssh_host }}
    username: ci-deploy
    key: ${{ secrets.CI_DEPLOY_KEY }}
    script: /opt/platform/core/entrypoints/deploy.sh ${{ inputs.project_name }} ${{ github.sha }} production
    # Добавить:
    command_timeout: 10m
```

4. **Workflow summary:** добавить `DEPLOY_URL` в `$GITHUB_STEP_SUMMARY` (если домен известен из ai-platform.yaml).

### 5.2. Split build/deploy jobs? — НЕТ

Бриф предлагает split job (build-image отдельно от deploy). Но:
- `deploy-project.yml` — это REUSABLE workflow, вызываемый из PROJECT repo
- Build-image происходит в PROJECT repo (отдельный workflow `deploy.yml`)
- `deploy-project.yml` только доставляет и деплоит

Поэтому split не требуется — build и так в отдельном workflow/job. Достаточно hardening текущих шагов.

### Файлы волны 5

| Файл | Действие | Описание |
|------|----------|----------|
| `.github/workflows/deploy-project.yml` | MODIFY | set -euo pipefail во всех шагах, verify deliver step, command_timeout |

---

## 6. Волна 6 (P2): Process unification — без новых таргетов

### 6.1. `make deploy` + `LAUNCH=1`

```makefile
deploy:
	@# ... pre-flight (если NODE задан) ...
	@# ... git push ...
	@if [ "$(filter 1,$(LAUNCH))" = "1" ]; then \
		echo "[IMP:7][make][deploy] LAUNCH mode: waiting for CI and verifying..."; \
		if [ -z "$(NODE)" ]; then \
			echo "[IMP:10][make][deploy] FATAL: LAUNCH=1 requires NODE=<node>" >&2; \
			exit 1; \
		fi; \
		bash $(_platform_root)/core/entrypoints/deploy-project.sh \
			--project "$(PROJECT)" \
			--node "$(NODE)" \
			--launch; \
	fi
```

`deploy-project.sh --launch`:
1. Pre-flight check VPS readiness (W1)
2. Ждать CI completion: `gh run watch` (если gh CLI доступен) или просто wait+retry
3. После CI success → `make verify NODE=$(NODE)` (проверить HTTP 200)
4. Вывести URL

### 6.2. `make bootstrap-node` + `AUTO_RECONCILE=1` = launch-all

```bash
make bootstrap-node NODE=tronyx-vps AUTO_RECONCILE=1
```

После bootstrap → converge → reconcile всех stub-проектов:
- Проекты с образами в GHCR → deployed
- Проекты без образов → WARN (ждут первого CI deploy)

Это заменяет `launch-all` из брифа — все проекты из node.yaml разворачиваются за одну команду.

### Файлы волны 6

| Файл | Действие | Описание |
|------|----------|----------|
| `Makefile` | MODIFY | deploy target: +LAUNCH=1 |
| `core/entrypoints/deploy-project.sh` | MODIFY | +`--launch` флаг: wait CI + verify |

---

## 7. File Manifest — полный список изменений

| # | Файл | Волна | Действие |
|---|------|:-----:|----------|
| 1 | `core/lib/vps-readiness.sh` | W1 | CREATE — shared pre-flight: SSH, forced-command, /opt/projects |
| 2 | `core/internal/deploy/reconcile-projects.sh` | W4 | CREATE — internal: stub→deployed recovery |
| 3 | `core/internal/bootstrap/converge.sh` | W2,W3,W4 | MODIFY — exit semantics + `_is_stub()` + R3 stub-aware + `--reconcile` |
| 4 | `core/internal/bootstrap/node-lifecycle.sh` | W2,W4 | MODIFY — step_15 exit handling + reconcile step |
| 5 | `core/entrypoints/converge.sh` | W4 | MODIFY — +`--reconcile` passthrough |
| 6 | `core/entrypoints/bootstrap.sh` | W4 | MODIFY — +`--auto-reconcile` flag |
| 7 | `core/entrypoints/node-update.sh` | W4 | MODIFY — +`--reconcile` passthrough |
| 8 | `core/entrypoints/deploy-project.sh` | W1,W6 | MODIFY — pre-flight + `--launch` |
| 9 | `core/entrypoints/deploy.sh` | W3 | MODIFY — verb contract: --status stub-aware |
| 10 | `core/internal/deploy/deploy-project.sh` | W3 | MODIFY — --status stub detection |
| 11 | `core/internal/bootstrap/remote-cmd.sh` | W4 | MODIFY — `execute_remote_reconcile()` |
| 12 | `Makefile` | W1,W4,W6 | MODIFY — deploy +NODE/+LAUNCH, bootstrap-node +AUTO_RECONCILE, converge/node-update +RECONCILE |
| 13 | `.github/workflows/deploy-project.yml` | W1,W5 | MODIFY — pre-flight step + set -euo pipefail + verify deliver |
| 14 | `tests/test_vps_readiness.py` | W1 | CREATE — mock SSH + forced-command responses |
| 15 | `tests/test_converge_exit.py` | W2 | CREATE — exit 0/1/2 scenarios |
| 16 | `tests/test_stub_detection.py` | W3 | CREATE — stub vs real, --report-only JSON |
| 17 | `tests/test_reconcile.py` | W4 | CREATE — stub→deploy, idempotent, missing image |
| 18 | `tests/test_sequencing.py` | W1,W6 | CREATE — full cycle: pre-flight → deploy → verify |
| 19 | `tests/gates/test_gate_sequencing.py` | W1-W6 | CREATE — gate: converge exit semantics invariant |

**ВАЖНО: 0 новых записей в `entrypoint-manifest.yaml` (allowed_verbs, forbidden_verbs, etc.)** — все изменения через флаги существующих таргетов.

---

## 8. Порядок выполнения

```
Волна 2 (converge exit semantics) → gate green   ← не зависит от 024
  └── Волна 3 (stub detection) → gate green       ← зависит от W2 (exit semantics)
      └── Волна 1 (VPS pre-flight) → gate green   ← не зависит от W2/W3, но deploy-cycle тестируется после
          └── Волна 5 (CI hardening) → gate green  ← зависит от W1 (pre-flight в CI)
              └── Волна 4 (reconciliation) → gate green ← зависит от W3 (stub detection) + 024 W2 (scaffold)
                  └── Волна 6 (unification) → gate green ← зависит от W1+W4+W5
```

**Обоснование порядка:**
- W2+W3 первыми — converge.sh внутренние изменения без зависимости от 024, минимальный риск
- W1 после W3 — pre-flight можно тестировать с учётом stub detection
- W5 после W1 — CI hardening использует pre-flight
- W4 после W3 — reconciliation требует stub detection + 024 W2 (scaffold создаёт project dirs)
- W6 последней — оркестрация поверх всего

---

## 9. Совместимость с DevPlan 024

| 024 Wave | Что делает | Влияние на 025 |
|:--------:|-----------|----------------|
| W2 (scaffold) | converge R3 вызывается из step_6b, создаёт /opt/projects/<name>/ | **Требуется для W4** — reconciliation проверяет наличие директорий |
| W0 (S1+S2+S10) | --skip-provision, единый deploy-modules | Без влияния (converge.sh не затрагивается) |
| W1 (SSL cache) | S3 кэширование сертификатов | Без влияния |
| W3 (predeploy gate) | T1-T5 тесты | Без влияния (разные тестовые файлы) |
| W4 (hermes L2) | pull-or-build | Без влияния |
| W5 (S3-S9) | Микрооптимизации | Без влияния |

**Критическая зависимость:** 024 W2 (project scaffold) должен быть выполнен до 025 W4 (reconciliation). Без scaffold reconciliation не может отличить «проект не создан» от «проект — stub».

**Совместные файлы:**
- `core/internal/bootstrap/converge.sh` — оба плана модифицируют: 024 (gen-env-platform.sh вместо touch), 025 (_is_stub + exit semantics). Порядок: 024 первый → 025 поверх.
- `core/internal/bootstrap/node-lifecycle.sh` — оба модифицируют: 024 (step_6b converge R3), 025 (step_15 exit handling + reconcile). Разные строки, конфликт маловероятен.
- `Makefile` — оба модифицируют (разные секции, конфликт маловероятен).

---

## 10. Acceptance Criteria

- [ ] `make deploy PROJECT=<name> NODE=<node>`: pre-flight VPS check → ready → git push; not ready → exit 1 «Run: make bootstrap-node»
- [ ] `make deploy PROJECT=<name>` (без NODE): текущее поведение без pre-flight (backward compat)
- [ ] `converge.sh --node <n>`: exit 0 (ok), exit 1 (warnings), exit 2 (errors)
- [ ] `node-lifecycle.sh`: step_15 converge — exit 1 = step_done, exit 2 = step_warn (не блокирует)
- [ ] `converge.sh --node <n> --report-only`: stub-проекты → `"status": "awaiting_deploy"`
- [ ] `converge.sh --node <n> --report-only`: реальные проекты → `"status": "converged"`
- [ ] `make converge NODE=<n> RECONCILE=1`: stub + GHCR image → deployed (compose up, healthy)
- [ ] `make converge NODE=<n> RECONCILE=1`: уже deployed → SKIP (идемпотентно)
- [ ] `make bootstrap-node NODE=<n> AUTO_RECONCILE=1`: после converge → все stub-проекты с образами deployed
- [ ] `make bootstrap-node NODE=<n>` (без флага): текущее поведение (backward compat)
- [ ] CI `deploy-project.yml`: deliver → verify compose file exists → fail если нет
- [ ] CI `deploy-project.yml`: все шаги с `set -euo pipefail`
- [ ] `make deploy PROJECT=<name> NODE=<node> LAUNCH=1`: одна команда → pre-flight → CI → verify → URL
- [ ] `make gate MODE=fast` — зелёный (включая тесты W1-W6)
- [ ] 0 новых имён в `allowed_verbs` entrypoint-manifest.yaml
- [ ] Полный цикл «новая VPS + 3 проекта»: `make bootstrap-node NODE=<n> AUTO_RECONCILE=1`, ≤ 20 мин

---

## 11. Не входит в этот DevPlan

- Оптимизация скорости скриптов — в 024-DevPlan
- SSL-кэширование — в 024-DevPlan
- Predeploy gate extension — в 024-DevPlan
- Hermes-agent L2 fallback — в 024-DevPlan
- Registry mirror / warm images — отклонено оператором
- CI workflow_dispatch auto-trigger из bootstrap — infinite loop risk
- `gh run watch` в LAUNCH=1 — если gh CLI недоступен, используем polling (упрощённая реализация)

$END_DEVPLAN
