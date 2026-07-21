# 025-Brief: Deploy sequencing & reliability — bootstrap↔deploy contract, stub detection, auto-reconciliation, process unification

$START_BRIEF

$ARTIFACT_CONTRACT
PURPOSE:               Устранить 7 системных причин недетерминированного поведения цепочки bootstrap→deploy, выявленных архитектурной суперпозицией (H1-H7) и НЕ покрытых DevPlan 024: отсутствие контракта очерёдности bootstrap↔deploy, silent CI failures, conflated converge exit semantics, checkpoint drift на stub-состояниях, отсутствие post-bootstrap reconciliation, неявный контракт готовности VPS, фрагментация процесса на 3+ ручных команд. Цель: полный цикл «новый проект → работает на VPS» должен занимать ОДНУ команду, занимать ≤5 минут, и быть 100% детерминированным.
DESCRIPTION:           Архитектурная криминалистика (arch-forensics skill, 7 задач + 9 режимов суперпозиции) вскрыла системный дефект платформы — отсутствует явный жизненный цикл проекта и оркестрация перехода между состояниями (VPS:unbootstrapped → bootstrapped → project:stub → project:deployed). DevPlan 024 оптимизирует скорость существующих шагов (SSL cache, --skip-provision, батчинг), но не адресует корневую причину 4-дневного (~18 итераций) деплой-цикла: CI deploy и bootstrap — два разорванных процесса без контракта очерёдности, без механизма восстановления после рассинхронизации, без единой точки входа. Бриф предлагает 6 волн, закрывающих оставшиеся разрывы:
  W1 — VPS readiness pre-flight (contract enforcement): `make deploy` и CI workflow проверяют, что VPS заbootstraplen ДО попытки деплоя.
  W2 — Converge exit semantics fix: разделение exit codes (0=ok, 1=warnings, 2=errors), bootstrap реагирует только на exit 2.
  W3 — Stub detection in converge: converge.sh R3 различает GENERATED-STUB ai-platform.yaml от реального, не маскирует отсутствие деплоя.
  W4 — Post-bootstrap reconciliation (`make reconcile-projects`): после bootstrap (или вручную) — проверяет все проекты из node.yaml, для каждого: если stub + есть Docker-образ в GHCR → делает platform-deliver + docker compose up.
  W5 — CI failure visibility hardening: `set -euo pipefail` во всех шагах CI, post-deliver verification (ssh check docker-compose.yml exists), split job status (build vs deploy).
  W6 — Process unification (`make project-launch`, `make launch-all`): единая команда для полного цикла: check VPS → bootstrap if needed → deploy all → verify.
RATIONALE:             Сессии 018-020 (Jul 18-20) заняли суммарно >8 часов на деплой 3 проектов. 024-DevPlan сократит это до ~15-20 мин за счёт устранения fix-циклов и ускорения скриптов. Но оставшиеся 7 системных проблем означают, что: (a) при ПЕРВОМ деплое после bootstrap всё равно потребуется ручное вмешательство (H1+H5: CI deploy запущен ДО bootstrap → stubs → redeploy вручную), (b) при ПОВТОРНОМ bootstrap проекты опять уйдут в stub-состояние и потребуют ручного redeploy (H3+H4: converge создаёт stubs, checkpoints пропускают converge, stubs неотличимы от реальных файлов), (c) нет гарантии что `make deploy` вообще что-то задеплоит (H6: нет pre-flight проверки VPS), (d) процесс остаётся фрагментированным на 3+ команд в правильном порядке (H7). Без адресации этих проблем платформа остаётся хрупкой: оператор не может быть уверен, что перезапуск сессии приведёт к тому же результату.
ACCEPTANCE_CRITERIA:   1. `make deploy PROJECT=<dir>` выполняет pre-flight проверку: SSH-доступность VPS, наличие ci-deploy forced-command, наличие /opt/projects/. Если VPS не готов — чёткое сообщение об ошибке: «VPS not bootstrapped. Run: make bootstrap-node NODE=<node> first».  2. converge.sh: exit 0 при ok, exit 1 при warnings (не блокирует bootstrap), exit 2 при errors (блокирует).  3. converge.sh R3: различает stub ai-platform.yaml (содержит `GENERATED-STUB by converge`) и реальный. Stub-проекты в --report-only: статус `awaiting_deploy`, а не `converged`.  4. `make reconcile-projects NODE=<node>`: для каждого проекта из node.yaml — проверяет stub vs deployed, при stub + образ в GHCR → делает platform-deliver + compose up. Идемпотентен.  5. CI workflow deploy-project.yml: каждый шаг с `set -euo pipefail`, шаг post-deliver-verify проверяет наличие docker-compose.yml на VPS через ssh. Job status split: build (всегда success при успешной сборке) vs deploy (success только при успешном деплое).  6. `make project-launch NAME=<name> NODE=<node>`: единая команда — проверяет VPS → если нужно, bootstrap → deploy → healthcheck → выводит URL.  7. Полный цикл «новая VPS + 3 проекта»: ОДНА команда (`make launch-all CONTEXT=tronyx-lab NODE=tronyx-vps`), ≤ 20 мин, детерминированный результат.
IMPLEMENTS:            Инварианты 1 (Makefile-фасад), 3 (org = context), 6 (bootstrap-node идемпотентный), 9 (тестовый сервер может быть пересоздан). Результаты архитектурной суперпозиции H1-H7 (arch-forensics skill, 2026-07-21).
IMPACTS:               core/internal/bootstrap/converge.sh (R3 stub detection + exit semantics), core/internal/bootstrap/node-lifecycle.sh (VPS readiness pre-flight, converge exit code handling), core/entrypoints/deploy-project.sh (pre-flight VPS check), core/internal/deploy/deploy-project.sh (stub detection в --status), core/internal/deploy/reconcile-projects.sh (CREATE), .github/workflows/deploy-project.yml (set -euo pipefail, post-deliver verify, job status split), Makefile (новые target-ы: reconcile-projects, project-launch, launch-all; pre-flight в deploy), core/lib/vps-readiness.sh (CREATE: shared pre-flight check), tests/test_sequencing.py (CREATE), tests/test_reconcile.py (CREATE).
REQUIRES:              Ветка от origin/main, `make gate MODE=fast` зелёный до начала, working tree чистый. DevPlan 024 выполнен (W2 — project scaffold создаёт /opt/projects/<name>/ при bootstrap; без этого reconciliation не сможет отличить stub от deployed). Доступ к VPS через SSH для pre-flight проверок. CI_DEPLOY_KEY для CI-верификации.
$END_ARTIFACT_CONTRACT

---

## 1. Диагноз: что произошло и почему 024 этого не чинит

### 1.1. Хронология деградации (Jul 18–21)

```
Jul 18 08:41–13:27   tronyx-site CI: 9 FAIL подряд (SSH host, workflow, permissions, executable)
Jul 20 12:31         botanika CI: 3 FAIL (GHCR auth, context field)
Jul 20 16:59         tronyx-site CI: ✅ SUCCESS (SHA 9e45334)
Jul 20 17:05         botanika CI: ✅ SUCCESS (SHA 8e2d859)
Jul 20 17:07–18:41   dance-site CI: 7 FAIL (GHCR auth, provenance)
Jul 20 18:51         dance-site CI: ✅ SUCCESS (SHA fe57d8d)
                     ⚠️ ВСЕ три CI = SUCCESS, но VPS ещё НЕ bootstraplen
                     ⚠️ platform-deliver forced-command падал (нет ci-deploy, нет /opt/projects/)
Jul 21 04:15         BOOTSTRAP VPS — converge exit 1, проекты = STUBS
Jul 21 04:34         Node-update (recovery) — converge exit 1
Jul 21 08:00         СЕЙЧАС: платформа жива (22 контейнера healthy), проекты — stubs
                     ⚠️ CI не перезапустить: SHA уже на origin → push = no-op → CI не триггерится
```

**Корневая причина цепочки:** CI deploy и bootstrap — два независимых процесса. CI запускается по `git push` в любой момент, включая ДО готовности VPS. Bootstrap создаёт stub-заглушки, которые неотличимы от реальных файлов для converge. После bootstrap нет механизма «догнать» пропущенные CI-деплои. Оператор остаётся с «успешным» CI и пустыми проектными директориями.

### 1.2. Что покрывает DevPlan 024

| Проблема | Статус в 024 |
|----------|:---:|
| `/opt/projects/<name>/` не существует при деплое | ✅ W2: scaffold в step_6b |
| SSL 4 мин на каждый bootstrap | ✅ W1: S3 cache |
| Отсутствие predeploy валидации проектных compose | ✅ W3: predeploy gate |
| Hermes-agent L2 пересборка с нуля | ✅ W4: pull-or-build |
| 5× вызов provisioner, 2× deploy-modules.sh, 26 python3 спавнов | ✅ W0: S1, S2, S10 |
| Микрооптимизации (healthcheck parallel, sudoers batch, etc.) | ✅ W5: S3-S9 |

### 1.3. Что НЕ покрывает DevPlan 024 — 7 системных разрывов

| ID | Разрыв | Severity | Почему 024 не чинит |
|----|--------|:--------:|---------------------|
| **H1** | CI deploy запускается до готовности VPS — deploy падает молча | 🔴 CRITICAL | W2 создаёт project dirs, но CI всё ещё может запуститься до bootstrap |
| **H5** | После bootstrap проекты в stubs — redeploy не происходит автоматически | 🔴 CRITICAL | W2 создаёт stubs, но нет механизма их «догнать» до deployed |
| **H7** | Процесс фрагментирован: 3+ команд в правильном порядке | 🔴 CRITICAL | 024 ускоряет шаги, но не объединяет их в единый flow |
| **H4** | Stub-файлы неотличимы от реальных — converge считает stub = converged | 🟡 HIGH | W2 создаёт stubs с `GENERATED-STUB` маркером, но converge не различает |
| **H3** | Converge exit 1 = warnings, но bootstrap интерпретирует как failure | 🟡 HIGH | Выходит за scope 024 (только оптимизация скорости) |
| **H2** | CI-шаги падают молча — workflow помечен SUCCESS при failed deploy | 🟡 HIGH | W3 добавляет predeploy gate, но не hardening CI-шагов |
| **H6** | Нет явного контракта «VPS должна быть готова перед deploy» | 🟢 MEDIUM | Выходит за scope 024 |

---

## 2. Решения (6 волн)

### Волна 1 (P0): VPS readiness pre-flight — contract enforcement

**Проблема (H1+H6):** `make deploy` и CI workflow предполагают, что VPS уже заbootstraplen, но никогда этого не проверяют. CI deploy, запущенный до bootstrap, падает с неинформативной ошибкой (или молча «успешен», если build-image прошёл, а deploy — нет).

**Решение:**
1. Создать `core/lib/vps-readiness.sh` — общий модуль pre-flight проверок:
   - SSH-доступность `ci-deploy@<host>`
   - Forced-command отвечает на `platform-deploy` (значит `/opt/platform/core/` доставлен)
   - `/opt/projects/` существует и доступен на запись
   - (опционально) Docker daemon отвечает
2. Интегрировать в `make deploy` (Makefile): перед `git push` — проверить readiness. Если VPS не готова — `echo "VPS not bootstrapped. Run: make bootstrap-node NODE=<node>" && exit 1`.
3. Интегрировать в CI `deploy-project.yml`: шаг `check-vps-readiness` перед `deliver-payload`. Fail fast с читаемой ошибкой.
4. Не блокировать `make deploy` если нет NODE (только PROJECT) — в этом случае проверка skipped, ответственность на CI.

**API `vps-readiness.sh`:**
```bash
# Использование:
source core/lib/vps-readiness.sh
check_vps_ready "tronyx-vps"   # exit 0 если готов, exit 1 с diagnostics если нет
check_vps_ready "tronyx-vps" --json  # JSON output для CI
```

**Файлы:** `core/lib/vps-readiness.sh` (CREATE), `Makefile` (MODIFY: deploy target), `.github/workflows/deploy-project.yml` (MODIFY: add check step).

---

### Волна 2 (P0): Converge exit semantics — warnings ≠ errors

**Проблема (H3):** `converge.sh` возвращает exit 1 при ЛЮБЫХ warn-юнитах (R6 legacy vhosts, R2 audit.log permissions). При bootstrap это записывается как «Converge failed (exit 1)», indistinguishable от реального падения (например, R3 не смог создать директории). Оператор не знает, критична ли ошибка.

**Решение:**
1. Разделить exit codes в `converge.sh`:
   - `exit 0` — все юниты ok или skipped (no-op, already converged)
   - `exit 1` — есть WARN-юниты (R6 legacy vhosts, R2 permissions mismatch), но CRITICAL юниты ok. Система функциональна, но есть косметический drift.
   - `exit 2` — есть ERROR: CRITICAL-юнит упал (R3 не смог создать проект, R1 permissions фатально сломаны). Система в degraded state.
2. В `node-lifecycle.sh`: `step_15_converge()` — только exit 2 считается failure. Exit 1 → `audit_log "converge:warnings"` (не failure).
3. В `audit.log`: различать `converge:complete (ok)`, `converge:complete (warnings=N)`, `converge:failed (errors=N)`.

**Инвариант:** severity из `module.yaml` (critical/warn/ok) маппится на exit semantics converge: critical → ERROR (exit 2), warn → WARNING (exit 1), ok/info → no impact.

**Файлы:** `core/internal/bootstrap/converge.sh` (MODIFY: exit code logic), `core/internal/bootstrap/node-lifecycle.sh` (MODIFY: step_15 handling).

---

### Волна 3 (P0): Stub detection in converge — не маскировать отсутствие деплоя

**Проблема (H4):** converge.sh R3 создаёт stub `ai-platform.yaml` с маркером `# GENERATED-STUB by converge`. Но при проверке существования (`[[ -f $file ]]`) stub считается валидным файлом, и converge пропускает проект как «уже converged». После этого НИКТО не знает, что проект на самом деле не задеплоен.

**Решение:**
1. `converge.sh` R3 `reconcile_projects()`:
   - При проверке существующего `ai-platform.yaml` — прочитать первую строку.
   - Если содержит `GENERATED-STUB` → статус `awaiting_deploy` (не `converged`), не перезаписывать.
   - Если не содержит → статус `converged` (реальный файл от CI).
2. `--report-only` вывод: для stub-проектов → `"status": "awaiting_deploy"`, для реальных → `"status": "converged"`.
3. `deploy-project.sh --status <project>`: проверять, является ли `ai-platform.yaml` stub-ом. Если да → `"state": "stub"` (а не `"state": "unknown"` или `"state": "ready"`).

**Stub detection helper:**
```bash
_is_stub() {
    local ai_platform_yaml="$1"
    [[ -f "$ai_platform_yaml" ]] && head -1 "$ai_platform_yaml" | grep -q "GENERATED-STUB"
}
```

**Файлы:** `core/internal/bootstrap/converge.sh` (MODIFY: R3 stub detection), `core/internal/deploy/deploy-project.sh` (MODIFY: --status stub detection).

---

### Волна 4 (P1): Post-bootstrap reconciliation — `make reconcile-projects`

**Проблема (H5):** После bootstrap (или после восстановления VPS) проекты остаются в stub-состоянии. CI для них уже отработал (и возможно пометил success), но `make deploy` — no-op (SHA на origin). Оператор должен вручную: либо `workflow_dispatch` через GitHub UI, либо `make deploy-project` (emergency fallback). Нет автоматического механизма «догнать» состояние.

**Решение:**
1. Создать `core/entrypoints/reconcile-projects.sh` — новый entrypoint:
   - Читает `node.yaml#projects`
   - Для каждого проекта:
     - Проверяет `_is_stub /opt/projects/<name>/ai-platform.yaml`
     - Если stub → проверяет наличие Docker-образа в GHCR (`docker manifest inspect ghcr.io/<context>/<project>:latest`)
     - Если образ есть → делает `platform-deliver` (tar актуальных файлов из context-overlay) + `docker compose pull && docker compose up -d`
     - Если образа нет → WARN: «project awaiting first CI deploy — push project repo to trigger»
     - Если не stub → SKIP (уже задеплоен)
   - Идемпотентен: повторный вызов = no-op для уже deployed проектов.
2. Интегрировать в bootstrap: после `step_15_converge` (или внутри converge) — опциональный вызов reconcile (флаг `--reconcile` или `AUTO_RECONCILE=true`).
3. Makefile target: `make reconcile-projects NODE=<node>`.
4. Не заменяет CI deploy — это recovery/initial-setup механизм, не continuous delivery.

**Файлы:** `core/entrypoints/reconcile-projects.sh` (CREATE), `Makefile` (MODIFY: target), `core/internal/bootstrap/node-lifecycle.sh` (MODIFY: optional reconcile after converge).

---

### Волна 5 (P1): CI failure visibility hardening

**Проблема (H2):** CI workflow шаги (deliver-payload, deploy) не имеют `set -euo pipefail`. Падение `platform-deliver` forced-command может быть не замечено. Job status `success` означает «build-image прошёл», а не «проект задеплоен и healthy».

**Решение:**
1. `deploy-project.yml`:
   - Все шаги: `set -euo pipefail` в shell.
   - Шаг `deliver-payload`: после tar+ssh — добавить verify: `ssh ci-deploy@host "test -f /opt/projects/<project>/docker-compose.yml"` → fail если файла нет.
   - Шаг `deploy`: проверять exit code `deploy.sh`. Fail на любой non-zero exit.
2. Split job status — build-image и deploy сделать **разными jobs** (не steps одной job):
   - `build-image` job: success = образ собран и запушен в GHCR.
   - `deploy` job: depends on `build-image`, success = проект запущен и healthy на VPS.
   - Это делает видимым: build прошёл, deploy упал.
3. Workflow summary: в конце `deploy` job — `echo "deploy-url=https://<domain>" >> $GITHUB_STEP_SUMMARY`.

**Файлы:** `.github/workflows/deploy-project.yml` (MODIFY: hardening + job split).

---

### Волна 6 (P2): Process unification — `make project-launch` и `make launch-all`

**Проблема (H7):** Полный цикл «новый проект → работает на VPS» требует 3+ ручных команд в строгом порядке: `make new-project` → `make bootstrap-node` → `make deploy`. Оператор должен знать магическую последовательность. При ошибке на любом шаге — ручная диагностика и повтор.

**Решение:**
1. `make project-launch NAME=<name> NODE=<node>`:
   - Проверяет существование проекта локально (`~/projects/<context>/<name>/`)
   - Проверяет VPS readiness (W1 pre-flight)
   - Если VPS не готова → предлагает bootstrap (интерактивно или через флаг `--bootstrap`)
   - Если проект не запушен → `git push`
   - Вызывает `make deploy` (CI)
   - Ждёт CI (опционально: `--watch` флаг для `gh run watch`)
   - Проверяет healthcheck: `curl -sS https://<domain>/` → выводит результат
2. `make launch-all CONTEXT=<context> NODE=<node>`:
   - Для всех проектов из `node.yaml#projects` → делает `project-launch`
   - Параллельно где возможно (разные проекты, независимые деплои)
3. Не заменяет существующие атомарные target-ы (`deploy`, `bootstrap-node`) — добавляет оркестрацию поверх.

**Makefile targets:**
```makefile
project-launch:
	@bash core/entrypoints/project-launch.sh --name "$(NAME)" --node "$(NODE)" $(if $(BOOTSTRAP),--bootstrap) $(if $(WATCH),--watch)

launch-all:
	@bash core/entrypoints/launch-all.sh --context "$(CONTEXT)" --node "$(NODE)" $(if $(BOOTSTRAP),--bootstrap)
```

**Файлы:** `core/entrypoints/project-launch.sh` (CREATE), `core/entrypoints/launch-all.sh` (CREATE), `Makefile` (MODIFY: targets).

---

## 3. Карта покрытия: 024 + 025

| Слой проблемы | 024 (оптимизация) | 025 (надёжность) |
|---------------|:---:|:---:|
| `/opt/projects/` не существует | ✅ W2 scaffold | ✅ W3 stub detection |
| SSL 4 мин | ✅ W1 S3 cache | — |
| Predeploy валидация | ✅ W3 gate | — |
| Hermes L2 пересборка | ✅ W4 pull-or-build | — |
| 5× provisioner, 2× deploy-modules | ✅ W0 S1,S2,S10 | — |
| CI deploy до bootstrap | — | ✅ W1 pre-flight |
| После bootstrap проекты = stubs | — | ✅ W4 reconciliation |
| Converge warnings = errors | — | ✅ W2 exit semantics |
| Stub маскируется под deployed | — | ✅ W3 stub detection |
| CI failures невидимы | — | ✅ W5 hardening |
| VPS readiness не проверяется | — | ✅ W1 pre-flight |
| 3+ команд вручную | — | ✅ W6 unification |

---

## 4. Приоритеты и оценки

| Приоритет | Волна | Эффект | Усилие | Зависимости |
|:---------:|-------|--------|:------:|-------------|
| **P0** | W2 (converge exit semantics) | Устраняет ложные failure-ы при bootstrap | Низкое | Нет |
| **P0** | W3 (stub detection) | Converge перестаёт маскировать отсутствие деплоя | Низкое | W2 (exit semantics) |
| **P0** | W1 (VPS pre-flight) | CI deploy fail-fast с читаемой ошибкой вместо silent failure | Среднее | Нет |
| **P1** | W5 (CI hardening) | Видимость: build success ≠ deploy success | Среднее | W1 (pre-flight check в CI) |
| **P1** | W4 (reconciliation) | Авто-восстановление после bootstrap: stubs → deployed | Высокое | W3 (stub detection), 024-W2 (scaffold) |
| **P2** | W6 (unification) | Одна команда вместо 3+, детерминированный результат | Среднее | W1+W4+W5 |

---

## 5. Definition of Done

1. `make deploy PROJECT=<name> NODE=<node>`:
   - Pre-flight: проверяет VPS readiness (SSH, forced-command, /opt/projects/)
   - Если VPS не готова — `exit 1` с сообщением «Run: make bootstrap-node NODE=<node> first»
2. `converge.sh --node tronyx-vps`:
   - Exit 0: всё ok
   - Exit 1: есть warnings (legacy vhosts, permissions drift) — bootstrap продолжает
   - Exit 2: есть errors (project dir creation failed) — bootstrap стоп
3. `converge.sh --node tronyx-vps --report-only`:
   - Stub-проекты: `"status": "awaiting_deploy"`
   - Реальные проекты: `"status": "converged"`
4. `make reconcile-projects NODE=tronyx-vps`:
   - Stub-проекты с Docker-образом в GHCR → deployed (docker compose up, healthy)
   - Stub-проекты без образа → WARN «awaiting first CI deploy»
   - Уже deployed проекты → SKIP
   - Идемпотентен: повторный вызов = no-op
5. CI `deploy-project.yml`:
   - При падении `deliver-payload` или `deploy` → job FAIL (красный), даже если build-image success
   - После deliver: verify `docker-compose.yml` существует на VPS
6. `make project-launch NAME=tronyx-site NODE=tronyx-vps`:
   - Одна команда → проект работает на VPS, `curl https://domain` возвращает 200
7. `make launch-all CONTEXT=tronyx-lab NODE=tronyx-vps`:
   - Одна команда → все проекты из node.yaml работают на VPS
8. `make gate MODE=fast` — зелёный (включая тесты из 024 и 025)
9. Полный цикл «голая VPS → 3 проекта работают»: ОДНА команда, ≤ 20 мин, 100% детерминированный результат (5/5 запусков — success)

---

## 6. Не входит в этот бриф

- Оптимизация скорости скриптов (S1-S10) — в 024-DevPlan
- SSL-кэширование на S3 — в 024-DevPlan
- Project scaffold (создание /opt/projects/) — в 024-DevPlan
- Predeploy gate extension — в 024-DevPlan
- Hermes-agent L2 fallback — в 024-DevPlan
- Registry mirror / warm images — отклонено оператором (024 Brief §5)
- Org-level GitHub secrets (B3) — отложено (024 Brief §5)
- CI workflow_dispatch auto-trigger из bootstrap — потенциально infinite loop risk, требует отдельного анализа

$END_BRIEF
