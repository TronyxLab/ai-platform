# GREP_SUMMARY: Brief 065 systemic CI fix ssh-split-brain push-and-pray set-e-cross-file cicd-debug-loop
# STRUCTURE: ┌ARTIFACT_CONTRACT┐ → ◇ problem-registry (5) → ⊕ cross-file-risk-matrix → ⚠ open-questions

$START_BRIEF
$ARTIFACT_CONTRACT
PURPOSE:               Устранить системные причины CI debugging loop (push-and-pray): SSH split-brain между shell-фасадом и GitHub Actions YAML, кросс-файловое дублирование багов, отсутствие shared CI-инфраструктуры для SSH.
DESCRIPTION:           Пять системных проблем, обнаруженных при анализе 120 коммитов за 2026-07-20/24 (58 fix-коммитов, 48%):
                       (P1) SSH Split-Brain — Wave 2 создал lib/ssh.sh facade для 6 shell-скриптов, но GitHub Actions YAML (4 workflow) используют raw SSH независимо, без синхронизации форматов и паттернов.
                       (P2) CI Debugging Loop — core-deploy.yml получил 7 коммитов за ~40 минут (2026-07-23 23:22–23:56), каждый — реакция на CI failure. Отсутствует локальный pre-flight для SSH CI-операций.
                       (P3) Cross-File Bug Inconsistency — одни и те же баги (echo vs printf, base64, IdentitiesOnly, actions:read) починены в core-deploy.yml, но НЕ в deploy-project.yml, mirror.yml, stage-deploy.yml.
                       (P4) set -e + SSH Command Substitution — 15+ файлов с паттерном $(ssh_read ...) или $(ssh ...) в контексте set -euo pipefail; любой ненулевой exit code SSH убивает скрипт. Починено точечно в node-update.sh, но не в project-list.sh, bootstrap.sh, remove-project.sh.
                       (P5) Нет shared CI SSH composite action — каждый workflow заново изобретает: декодирование ключа, IdentitiesOnly, known_hosts, формат секрета.
RATIONALE:             Системный анализ 2026-07-24 показал каскад из 7 коммитов в core-deploy.yml за 40 минут с паттерном «commit → ждать CI → fix → commit». Это не изолированный инцидент, а структурный эффект трёх факторов: (a) SSH facade не покрывает CI YAML, (b) нет локального pre-flight для SSH, (c) нет shared CI-инфраструктуры. Точечные фиксы продолжат каскад — следующий баг уже ждёт в deploy-project.yml (echo вместо printf, нет base64, нет IdentitiesOnly).
ACCEPTANCE_CRITERIA:   (1) Shared composite action setup-ssh используется во всех 4 CI workflows (core-deploy, deploy-project, mirror, stage-deploy); (2) Формат SSH-секретов унифицирован (base64 + printf) во всех workflows; (3) Один CI-прогон достаточен для верификации SSH-изменений (нет каскада); (4) set -e + $(ssh_*) паттерн обработан универсально во всех затронутых shell-скриптах; (5) make gate MODE=fast зелёный.
IMPLEMENTS:            Анализ системных проблем CI/CD от 2026-07-24
IMPACTS:               .github/actions/setup-ssh/ (новый), .github/workflows/core-deploy.yml, .github/workflows/deploy-project.yml, .github/workflows/mirror.yml, .github/workflows/stage-deploy.yml, core/entrypoints/node-update.sh (set -e fix уже сделан), core/internal/scaffold/project-list.sh, core/internal/scaffold/remove-project.sh, core/entrypoints/bootstrap.sh, core/internal/bootstrap/remote-cmd.sh, core/internal/deploy/reconcile-projects.sh, core/lib/ssh.sh (возможно, ssh_read обёртка с подавлением set -e)
REQUIRES:              Локальный git-доступ; secrets VPS_SSH_KEY/CI_DEPLOY_KEY/MIRROR_SSH_KEY для верификации в CI; feature-ветка с push в CI для подтверждения отсутствия каскада
$END_ARTIFACT_CONTRACT

---

## $DOCUMENT_PLAN

**SECTION_GOALS:**
- GOAL P1: SSH Split-Brain — унификация SSH в CI YAML через composite action → GOAL_SPLITBRAIN
- GOAL P2: CI Debugging Loop — локальный pre-flight/dry-run для SSH CI-операций → GOAL_DEBUGLOOP
- GOAL P3: Cross-File Consistency — синхронизация форматов секретов и SSH-паттернов между всеми workflows → GOAL_CROSSFILE
- GOAL P4: set -e + SSH — универсальный паттерн обработки exit code во всех shell-скриптах → GOAL_SETE
- GOAL V: Верификация — один CI-прогон без каскада → GOAL_VERIFY

$END_DOCUMENT_PLAN

---

## Реестр проблем

### P1: SSH Split-Brain

**Суть:** Wave 2 (aaf8a4c, 2026-07-21) создал `core/lib/ssh.sh` facade (`ssh_read`, `ssh_exec`, `SSH_OPTS_COMMON`) и мигрировал 6 shell-скриптов. Но GitHub Actions YAML не может использовать bash-facade — 4 workflow пишут raw SSH параллельно и независимо.

**Две несвязанные реальности:**

| Аспект | Shell facade (lib/ssh.sh) | CI YAML (core-deploy, deploy-project, mirror) |
|--------|--------------------------|-----------------------------------------------|
| SSH key формат | Через переменные окружения | GitHub Secrets → echo/printf → файл |
| Key encoding | Не кодируется в фасаде | base64 в core-deploy, plain в deploy-project |
| IdentitiesOnly | SSH_OPTS_COMMON содержит | Есть в core-deploy, mirror; НЕТ в deploy-project |
| Таймауты | Параметр timeout в ssh_read | -o ConnectTimeout=10 жёстко |
| User | Параметром | VPS_USER env / ci-deploy жёстко |

**Почему это проблема:** Любое изменение в одном мире (например, смена формата ключа на base64) требует ручной синхронизации в другом. Сегодня: 7 коммитов только на синхронизацию core-deploy.yml после изменений в secrets.

### P2: CI Debugging Loop (Push-and-Pray)

**Каскад core-deploy.yml 2026-07-23 23:22–23:56:**

```
c030a68  (23:22) NODE auto-detect + makefiles rsync
         → CI run → FAIL: make node-update без NODE=
c7a666c  (23:37) P0: root user, force-recreate, secrets
         → CI run → FAIL: SSH auth
7566aa9  (23:42) base64 SSH key, убран secrets-check gate
         → CI run → FAIL: echo искажает base64
21b988d  (23:45) printf + -i + IdentitiesOnly
         → CI run → FAIL: makefiles trailing slash
afff074  (23:49) rsync trailing slash для makefiles/
         → CI run → FAIL: что-то ещё
aa6e101  (23:52) detect local VPS, skip self-SSH proxy
         → CI run → FAIL: set -e + return 2
0c973fb  (23:56) set -e kills node-update.sh на return 2
```

**Корневая причина:** Нет локального pre-flight для GitHub Actions SSH-команд. Первый реальный запуск — в CI. Каждый баг обнаруживается через `push → ждать CI → fail → читать логи → fix → push → ...`.

**Частичное решение (уже сделано):** CI test deduplication (DevPlan 063) — fast gate ловит часть проблем локально. Но SSH-операции в YAML принципиально непроверяемы локально.

### P3: Cross-File Bug Inconsistency

**Матрица: где баг починен / где НЕ починен:**

| Паттерн | Починен в | НЕ починен в |
|---------|-----------|-------------|
| `printf` вместо `echo` для SSH key | core-deploy.yml (21b988d) | **deploy-project.yml** (echo $CI_DEPLOY_KEY) |
| base64-декодирование ключа | core-deploy.yml (7566aa9) | **deploy-project.yml** (plain text) |
| `IdentitiesOnly=yes` | core-deploy.yml, mirror.yml | **deploy-project.yml** (нет флага) |
| `actions:read` permission | platform-test, push-gate, core-deploy | **stage-deploy.yml** (?) |
| `set -e` + ssh return ≠ 0 | node-update.sh (0c973fb) | **project-list.sh, bootstrap.sh, remove-project.sh** |
| makefiles/ trailing slash | core-deploy.yml (afff074) | N/A (только в YAML) |

**Вывод:** deploy-project.yml — следующая жертва каскада. Он содержит минимум 3 известных бага (echo, plain key, без IdentitiesOnly), которые проявятся при первом же деплое после изменения формата секретов.

### P4: set -e + SSH Command Substitution

**Паттерн:** `$(ssh_read ...)` или `$(ssh ...)` внутри скрипта с `set -euo pipefail`. Если SSH возвращает ненулевой код (timeout, connection refused, auth failure, command error), `set -e` убивает весь скрипт.

**Затронутые файлы (15+):**

| Файл | Строка | Паттерн | Статус |
|------|--------|---------|--------|
| `core/entrypoints/node-update.sh` | — | execute_remote_update return 2 | ✅ Починено (0c973fb) |
| `core/internal/scaffold/project-list.sh` | 299 | `ssh_output="$(ssh_read ...)"` | ❌ Не починено |
| `core/internal/scaffold/remove-project.sh` | 337 | `ssh_output="$(ssh_exec ...)"` | ❌ Не починено |
| `core/entrypoints/bootstrap.sh` | 182 | `REMOTE_CMD="$(build_ssh_cmd ...)"` | ❌ Не починено |
| `core/internal/bootstrap/remote-cmd.sh` | 270, 394 | `remote_cmd="$(build_*_ssh_cmd ...)"` | ❌ Не починено |
| `core/internal/deploy/reconcile-projects.sh` | 221 | `ssh_read ... \|\| {` | ✅ Частично (есть fallback) |
| `core/internal/bootstrap/node-lifecycle.sh` | 141 | `CHECKPOINT_STEP_HASH="$(_step_hash "ssh-access")"` | ❌ Косвенно (через _step_hash) |

**Варианты универсального решения (для DevPlan):**
- A. Обёртка `ssh_read_safe` с `|| true` и проверкой exit code до использования результата
- B. `set +e` / `set -e` вокруг каждого вызова (verbose, error-prone)
- C. `trap` на ERR с игнорированием SSH-ошибок (слишком широко)

### P5: Нет Shared CI SSH Composite Action

Каждый workflow заново реализует:
1. Декодирование SSH-ключа из secrets
2. Запись в ~/.ssh/
3. `chmod 600`
4. `ssh-keyscan`
5. SSH-команду с опциями

**Текущее состояние:**

| Workflow | Key format | Key writing | IdentitiesOnly | User |
|----------|-----------|-------------|----------------|------|
| core-deploy.yml | base64 | printf | yes | VPS_USER env |
| deploy-project.yml | plain | echo | **нет** | ci-deploy (hardcoded) |
| mirror.yml | plain | echo | yes | git (GIT_SSH_COMMAND) |
| stage-deploy.yml | — (appleboy) | — | — | — |

---

## Открытые вопросы (→ DevPlan)

1. **Как тестировать SSH CI-операции локально?** Dry-run через `ssh -o ConnectTimeout=1 -T ...` с ожидаемым failure? Отдельный test workflow с self-hosted runner? Mock SSH server в Docker?

2. **Должен ли composite action покрывать shell-скрипты?** Сейчас shell facade (lib/ssh.sh) и CI YAML — две разные вселенные. Нужно ли их свести к одному интерфейсу или достаточно синхронизировать форматы?

3. **Унификация формата секретов:** base64 для всех ключей или plain text с валидацией? base64 решает проблему `echo` vs `printf` + спецсимволы, но добавляет шаг декодирования.

4. **set -e паттерн:** универсальная обёртка `ssh_read_safe` или точечные фиксы в каждом скрипте? Обёртка — одно место для правки, но меняет контракт возврата (нужно проверять и exit code, и вывод).

5. **Консистентность `VPS_USER`:** Сейчас core-deploy использует `root`, deploy-project — `ci-deploy`. Это осознанное различие или drift?

6. **`stage-deploy.yml` на appleboy/ssh-action:** Мигрировать на composite action или оставить как есть (единственный workflow без raw SSH)?

$END_BRIEF
