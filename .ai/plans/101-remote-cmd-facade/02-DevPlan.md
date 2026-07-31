$START_DEVPLAN
# DevPlan 101 — remote-cmd.sh 266→≤60 LOC: execute_remote_* оркестрация → Python

$ARTIFACT_CONTRACT
PURPOSE:               Завершить Strangler-Fig декомпозицию remote-cmd.sh: вынести execute_remote_*
                       оркестрацию (resolve, VPS detect, sync-core, prepare_ssh_opts, ssh_exec) в
                       Python-модуль `remote_executor.py`. Shell-фасад сокращается до ~60 LOC:
                       printf %q builders (D3) + вызов Python. Поправка брифа: build-функции (102
                       строк кода) извлекаются в `build-ssh-cmd.sh` для достижения целевого LOC.
DESCRIPTION:           (1) Извлечь build_*_ssh_cmd функции в `core/internal/bootstrap/build-ssh-cmd.sh`
                       (printf %q, D3 — логика не меняется, только локация). (2) Создать
                       `core/internal/bootstrap/remote_executor.py` — Python-модуль с CLI
                       (execute-update | execute-converge | execute-reconcile), импортирующий
                       overlay_deliverer для resolve/extract/sync-core. (3) remote-cmd.sh → тонкий
                       фасад: source build-ssh-cmd.sh + 3 thin-обёртки, вызывающие Python CLI +
                       deliver_vhost_overlays (уже Python-фасад). (4) Обновить caller'ы
                       (bootstrap.sh → source build-ssh-cmd.sh). (5) Удалить dead code
                       (execute_remote_reconcile_entrypoint, _resolve_and_extract).
                       (6) Обновить AGENTS.md (266→60 LOC).
RATIONALE:             Q: Почему извлечение build-функций в отдельный файл?
                       A: Build-функции содержат ~102 строки shell-кода (printf %q, PLATFORM_ROOT
                       export TRAP, ci_deploy_key TRAP). Оставляя их в remote-cmd.sh, фасад физически
                       не может быть ≤60 LOC. Извлечение в build-ssh-cmd.sh сохраняет D3-решение
                       (printf %q в shell), не меняет логику, и позволяет достичь целевого LOC.
                       Это стандартная практика для shell-библиотек (аналогично lib/ssh.sh, lib/paths.sh).
                       Q: Почему новый remote_executor.py, а не расширение overlay_deliverer.py?
                       A: overlay_deliverer.py отвечает за доставку (delivery): resolve, extract,
                       sync-core, vhost overlays. remote_executor.py отвечает за исполнение
                       (execution): оркестрация полного цикла удалённой команды. Разделение по
                       ответственности (SRP): deliverer не должен знать о VPS self-SSH loop,
                       prepare_ssh_opts, ssh_exec. Кроме того, overlay_deliverer уже 421 LOC —
                       добавление ещё ~200 LOC раздует модуль. remote_executor импортирует
                       overlay_deliverer (композиция), не дублирует.
ACCEPTANCE_CRITERIA:   AC1: Python-модуль `remote_executor.py` реализует execute_remote_update,
                            execute_remote_converge, execute_remote_reconcile с CLI
                       AC2: Shell-фасад remote-cmd.sh ≤ 60 LOC (без учёта build-функций в
                            build-ssh-cmd.sh). Build-функции в build-ssh-cmd.sh (~100 LOC).
                       AC3: execute_remote_update работает идентично (bootstrap/node-update):
                            resolve → VPS detect → sync-core → ssh_exec. Exit codes: 0/1/2.
                       AC4: execute_remote_converge работает идентично.
                       AC5: execute_remote_reconcile работает идентично (≡ converge + --reconcile).
                       AC6: DRY_RUN режим сохраняет поведение (печатает команды, не выполняет,
                            exit 0).
                       AC7: AGENTS.md (core/internal/bootstrap/) обновлён: 266→60 LOC для
                            remote-cmd.sh. Добавлена запись о build-ssh-cmd.sh (~100 LOC).
                       AC8: Все TRAP-аннотации сохранены:
                            · P0 VPS self-SSH loop — мигрирует в remote_executor.py
                            · P1 PLATFORM_ROOT export — остаётся в build-ssh-cmd.sh
                            · P4 ssh_exec — мигрирует в remote_executor.py
                            · D3 printf %q — остаётся в build-ssh-cmd.sh (неприкосновенно)
IMPLEMENTS:            Brief 101 (`.ai/plans/101-remote-cmd-facade/01-Brief.md`)
IMPACTS:
                       - `core/internal/bootstrap/build-ssh-cmd.sh` (NEW)
                       - `core/internal/bootstrap/remote_executor.py` (NEW)
                       - `core/internal/bootstrap/remote-cmd.sh` (MODIFY: 266→~60 LOC)
                       - `core/entrypoints/bootstrap.sh` (MODIFY: source build-ssh-cmd.sh)
                       - `core/entrypoints/node-update.sh` (MODIFY: minor — remote-cmd.sh API unchanged)
                       - `core/entrypoints/converge.sh` (MODIFY: minor — remote-cmd.sh API unchanged)
                       - `core/internal/bootstrap/AGENTS.md` (MODIFY: LOC update)
                       - `tests/unit/test_remote_executor.py` (NEW)
REQUIRES:              `core/internal/bootstrap/overlay_deliverer.py` (resolve_node_yaml,
                       extract_node_host, sync_core_to_vps), `core/lib/ssh.sh` (ssh_exec —
                       Python mirror), `core/internal/shared/node_yaml.py` (NodeYaml)
$END_ARTIFACT_CONTRACT

---

## §Debt Intake

| Источник | Тип | Решение |
|----------|-----|---------|
| TRAP[DEBT] overlay_deliverer.py:19 — node-resolver.sh inline python3 -c | DEFER | Вне скоупа 101 — отдельный DevPlan. Не блокирует. |
| TRAP[DECISION] D3 printf %q (overlay_deliverer.py:18) | PRESERVE | Build-функции извлекаются в build-ssh-cmd.sh, логика не меняется |
| TRAP[DECISION] converge.sh passthrough arg pattern (2026-07-21) | DEFER | Wave 4 redesign — не в скоупе |
| DRIFT: AGENTS.md @rationale говорит «672→~230 LOC shell facade», фактически 266 | IN_SCOPE | Исправляется в AC7 |

---

## 1. Problem Matrix

| # | Проблема | Доказательство | Решение |
|---|----------|---------------|---------|
| P1 | remote-cmd.sh = 266 LOC при задокументированных 230 (DRIFT) | `wc -l core/internal/bootstrap/remote-cmd.sh` → 266 | Сократить до ~60 LOC фасада |
| P2 | 3 почти идентичные execute_remote_* функции — ~81 строка копипасты оркестрации | execute_remote_update:39, execute_remote_converge:21, execute_remote_reconcile:21 (с region markers: +5/+3/+3) | Вынести в Python (DRY) |
| P3 | execute_remote_reconcile_entrypoint — dead code (не вызывается ни одним внешним скриптом) | grep по всем .sh: определено только в remote-cmd.sh, вызовов извне 0 | Удалить |
| P4 | _resolve_and_extract — shell-обёртка над Python CLI | 13 строк (137-149): вызывает overlay_deliverer resolve-node + extract-host | Перенести в remote_executor.py |
| P5 | Shell-фасад не может быть ≤60 LOC с build-функциями внутри | Build-функции: ~102 строки (printf %q + TRAP-комментарии) | Извлечь в build-ssh-cmd.sh |

---

## 2. Architecture Overview

### 2.1 Layer Diagram

```
┌─ entrypoints/ ─────────────────────────────────────────────────┐
│  bootstrap.sh ──► build_ssh_cmd()         (source build-ssh-cmd.sh)  │
│  node-update.sh ─► execute_remote_update() (source remote-cmd.sh)    │
│  converge.sh ────► execute_remote_converge()(source remote-cmd.sh)   │
└──────────────────────┬──────────────────────────────────────────┘
                       │
┌─ internal/bootstrap/ (shell facades) ──────────────────────────┐
│  build-ssh-cmd.sh (~100 LOC)          remote-cmd.sh (~60 LOC)  │
│  ├─ build_ssh_cmd()        printf %q  ├─ execute_remote_update()│
│  ├─ build_update_ssh_cmd() printf %q  ├─ execute_remote_converge│
│  └─ build_converge_ssh_cmd()          ├─ execute_remote_reconcile
│                                       └─ deliver_vhost_overlays()│
└──────────────────────┬──────────────────────────────────────────┘
                       │ python3 -m core.internal.bootstrap.remote_executor
┌─ internal/bootstrap/ (Python) ─────────────────────────────────┐
│  remote_executor.py (~200 LOC NEW)                              │
│  ├─ RemoteExecutor.execute_update()                             │
│  ├─ RemoteExecutor.execute_converge()                           │
│  ├─ RemoteExecutor.execute_reconcile()                          │
│  └─ CLI (argparse: execute-update|execute-converge|execute-reconcile)│
│                                                                 │
│  overlay_deliverer.py (EXISTING, imports)                       │
│  ├─ resolve_node_yaml()                                         │
│  ├─ extract_node_host()                                         │
│  └─ sync_core_to_vps()                                          │
└────────────────────────────────────────────────────────────────┘
```

### 2.2 Draft Code Graph

```xml
<code_graph>
  <entity id="build_ssh_cmd_sh" type="SHELL_MODULE" keywords="build-ssh-cmd printf-quote D3 PLATFORM_ROOT export">
    <annotation>core/internal/bootstrap/build-ssh-cmd.sh (NEW) — ~100 LOC.
      Содержит build_ssh_cmd(), build_update_ssh_cmd(), build_converge_ssh_cmd().
      printf %q quoting (D3), PLATFORM_ROOT export, ci_deploy_key export.
      Источник: извлечено из remote-cmd.sh строки 29-132.
      TRAP[BUG] P1 PLATFORM_ROOT, TRAP[BUG] P2 ci_deploy_key — сохранены.</annotation>
    <crossLinks>
      <link target="remote_executor_py" relation="provides_command_to"/>
      <link target="bootstrap_entrypoint" relation="sourced_by"/>
    </crossLinks>
  </entity>

  <entity id="remote_executor_py" type="PYTHON_MODULE" keywords="execute-remote update converge reconcile VPS-self-SSH sync-core ssh-exec DRY_RUN">
    <annotation>core/internal/bootstrap/remote_executor.py (NEW) — ~200 LOC.
      Класс RemoteExecutor с методами execute_update/execute_converge/execute_reconcile.
      CLI: argparse с subcommands.
      Импортирует overlay_deliverer (resolve_node_yaml, extract_node_host, sync_core_to_vps).
      SSH exec через subprocess.run с SSH_OPTS mirror + timeout wrapper.
      VPS self-SSH loop: проверка /opt/platform/core/internal/bootstrap/node-lifecycle.sh.
      Exit codes: 0=success, 1=fatal, 2=local fallback (no host / VPS detected).
      DRY_RUN: печатает команды без исполнения, exit 0.
      TRAP[BUG] P0 VPS self-SSH loop, TRAP[BUG] P4 ssh_exec — мигрированы.</annotation>
    <crossLinks>
      <link target="overlay_deliverer_py" relation="imports"/>
      <link target="remote_cmd_sh" relation="called_by"/>
    </crossLinks>
  </entity>

  <entity id="remote_cmd_sh" type="SHELL_MODULE" keywords="thin-facade execute-wrapper deliver-vhost-overlays">
    <annotation>core/internal/bootstrap/remote-cmd.sh (MODIFY) — ~60 LOC.
      Source build-ssh-cmd.sh + lib/ssh.sh.
      3 thin-обёртки: execute_remote_update/converge/reconcile.
      Каждая: build command (printf %q) → python3 remote_executor.py execute-* --remote-cmd.
      deliver_vhost_overlays() — без изменений (уже Python-фасад).
      Удалено: _resolve_and_extract(), execute_remote_reconcile_entrypoint().</annotation>
    <crossLinks>
      <link target="build_ssh_cmd_sh" relation="sources"/>
      <link target="remote_executor_py" relation="delegates_to"/>
      <link target="node_update_entrypoint" relation="sourced_by"/>
      <link target="converge_entrypoint" relation="sourced_by"/>
    </crossLinks>
  </entity>

  <entity id="overlay_deliverer_py" type="PYTHON_MODULE" keywords="resolve extract sync-core vhost existing">
    <annotation>core/internal/bootstrap/overlay_deliverer.py (EXISTING, UNCHANGED) — 421 LOC.
      Предоставляет resolve_node_yaml(), extract_node_host(), sync_core_to_vps().
      SSH_OPTS mirror, RSYNC_EXCLUDES, _ssh_e() — используются remote_executor.py.</annotation>
    <crossLinks>
      <link target="remote_executor_py" relation="imported_by"/>
    </crossLinks>
  </entity>

  <entity id="bootstrap_entrypoint" type="SHELL_SCRIPT" keywords="bootstrap entrypoint build_ssh_cmd">
    <annotation>core/entrypoints/bootstrap.sh (MODIFY) — source build-ssh-cmd.sh вместо remote-cmd.sh.
      Использует только build_ssh_cmd() — не затрагивает execute_* функции.</annotation>
    <crossLinks>
      <link target="build_ssh_cmd_sh" relation="sources"/>
    </crossLinks>
  </entity>

  <entity id="node_update_entrypoint" type="SHELL_SCRIPT" keywords="node-update entrypoint">
    <annotation>core/entrypoints/node-update.sh (MODIFY: minimal).
      Продолжает source remote-cmd.sh. API execute_remote_update не меняется.
      Возможно: source build-ssh-cmd.sh тоже (если DRY_RUN использует build_update_ssh_cmd).</annotation>
    <crossLinks>
      <link target="remote_cmd_sh" relation="sources"/>
    </crossLinks>
  </entity>

  <entity id="converge_entrypoint" type="SHELL_SCRIPT" keywords="converge entrypoint">
    <annotation>core/entrypoints/converge.sh (MODIFY: minimal).
      Продолжает source remote-cmd.sh. API execute_remote_converge не меняется.</annotation>
    <crossLinks>
      <link target="remote_cmd_sh" relation="sources"/>
    </crossLinks>
  </entity>

  <entity id="test_remote_executor_py" type="TEST_MODULE" keywords="unit-test remote_executor CLI argparse mock-subprocess exit-codes">
    <annotation>tests/unit/test_remote_executor.py (NEW) — ~150 LOC.
      Unit-тесты CLI, exit codes, DRY_RUN, error propagation.
      Mock subprocess.run для SSH/rsync вызовов.</annotation>
    <crossLinks>
      <link target="remote_executor_py" relation="tests"/>
    </crossLinks>
  </entity>
</code_graph>
```

### 2.3 Design Decisions

#### D1: Build-функции → build-ssh-cmd.sh (извлечение, не изменение)
## @rationale
**Q:** Почему build-функции извлекаются в отдельный файл, а не остаются в remote-cmd.sh?
**A:** Build-функции (`build_ssh_cmd`, `build_update_ssh_cmd`, `build_converge_ssh_cmd`) содержат ~102 строки shell-кода (printf %q quoting, PLATFORM_ROOT export, ci_deploy_key export, TRAP-комментарии). Оставляя их в remote-cmd.sh, невозможно достичь целевого ≤60 LOC для фасада. Извлечение в `build-ssh-cmd.sh`:
- Сохраняет D3-решение (printf %q в shell, логика неприкосновенна)
- Следует существующему паттерну платформы (lib/ssh.sh, lib/paths.sh, lib/logging.sh — все shell-библиотеки)
- Даёт bootstrap.sh прямой доступ к build_ssh_cmd без транзитивного source через remote-cmd.sh
- Не меняет сигнатуры функций, не затрагивает printf %q логику

#### D2: remote_executor.py (новый модуль, не расширение overlay_deliverer.py)
## @rationale
**Q:** Почему новый модуль, а не расширение overlay_deliverer.py?
**A:** Разделение ответственности (SRP):
- `overlay_deliverer.py` (421 LOC) — доставка: resolve, extract, sync-core, vhost overlays
- `remote_executor.py` (~200 LOC) — исполнение: оркестрация полного цикла удалённой команды (VPS self-SSH detect, prepare_ssh_opts, ssh_exec, DRY_RUN, exit code propagation)
- remote_executor **импортирует** overlay_deliverer (композиция), не дублирует логику resolve/extract/sync-core

#### D3: printf %q builders — НЕПРИКОСНОВЕННЫ
## @rationale
**Q:** Почему не заменить printf %q на Python shlex.quote()?
**A:** TRAP[DECISION] 2026-07-26: `shlex.quote() ≠ printf '%q'`. Разное экранирование специальных символов. Build-функции содержат критичные TRAP[BUG] (P1 PLATFORM_ROOT, P2 ci_deploy_key) и сложную логику экспорта переменных. Риск регрессии при миграции неприемлем — bootstrap-node затрагивает production VPS.

#### D4: SSH exec в Python через subprocess.run (не через shell ssh_exec)
## @rationale
**Q:** Почему Python не вызывает shell-функцию ssh_exec?
**A:** Вызов shell-функции из Python через subprocess требует source'инга lib/ssh.sh и экспорта всех зависимых переменных — это добавляет сложность без выигрыша. remote_executor.py использует `subprocess.run(["ssh", *SSH_OPTS, ...])` с timeout через `subprocess.run(timeout=N)`. SSH_OPTS mirror уже существует в overlay_deliverer.py. Логика ssh_exec (exit=124 → timeout, валидация, DRY_RUN) реплицируется в Python с тем же поведением. TRAP[BUG] P4 («bare ssh_exec may silently fail under set -e») не релевантен для Python (нет set -e).

---

## 3. Step-by-Step Data Flow

### 3.1 execute_remote_update (node-update)

```
node-update.sh
  │  source remote-cmd.sh
  │  execute_remote_update "${NODE_NAME}" "${age_key}" "${args[@]}"
  ▼
remote-cmd.sh (thin wrapper, ~8 LOC)
  │  1. remote_cmd="$(build_update_ssh_cmd "${node_name}" "${age_key}" "${passthrough_args[@]}")"
  │  2. python3 -m core.internal.bootstrap.remote_executor execute-update \
  │       --node "${node_name}" --remote-cmd "${remote_cmd}" \
  │       ${DRY_RUN:+--dry-run} --passthrough-args "${passthrough_args[@]}"
  │  3. return $?
  ▼
remote_executor.py::RemoteExecutor.execute_update()
  │  1. resolve_node_yaml(node_name)          → overlay_deliverer.resolve_node_yaml()
  │  2. extract_node_host(yaml_path)           → overlay_deliverer.extract_node_host()
  │  3. if host == "": return 2 (local fallback)
  │  4. VPS self-SSH detect:
  │     check /opt/platform/core/internal/bootstrap/node-lifecycle.sh exists
  │     → if yes: log IMP:9 "Local VPS detected", return 2
  │  5. prepare_ssh_opts: ssh-keygen -R (update mode: preserve known_hosts)
  │  6. sync_core_to_vps(host, core_src, node_name, node_yaml, dry_run)
  │     → overlay_deliverer.sync_core_to_vps()
  │  7. if DRY_RUN: print ssh command, exit(0)
  │  8. ssh_exec: subprocess.run(["ssh", *SSH_OPTS, f"root@{host}", remote_cmd],
  │       timeout=600, check=False)
  │  9. return exit code (0/124/non-zero)
  ▼
  exit code → shell → node-update.sh (RC=2 → local exec fallback)
```

### 3.2 execute_remote_converge

```
converge.sh
  │  source remote-cmd.sh
  │  execute_remote_converge "${NODE_NAME}" "${PASSTHROUGH_ARGS[@]}"
  ▼
remote-cmd.sh (thin wrapper, ~6 LOC)
  │  1. remote_cmd="$(build_converge_ssh_cmd "${node_name}" "${passthrough_args[@]}")"
  │  2. python3 -m core.internal.bootstrap.remote_executor execute-converge \
  │       --node "${node_name}" --remote-cmd "${remote_cmd}" ${DRY_RUN:+--dry-run}
  │  3. return $?
  ▼
remote_executor.py::RemoteExecutor.execute_converge()
  │  1. resolve_node_yaml → extract_node_host
  │  2. if host == "": return 2
  │  3. prepare_ssh_opts (update mode)
  │  4. (NO sync-core — converge doesn't sync core)
  │  5. DRY_RUN check
  │  6. ssh_exec: subprocess.run ssh root@host remote_cmd
  │  7. return exit code
```

### 3.3 execute_remote_reconcile

```
Аналогично converge, но:
  │  remote-cmd.sh добавляет "--reconcile" к build_converge_ssh_cmd
  │  Python: тот же execute_converge с --reconcile в remote_cmd
  │  execute_remote_reconcile_entrypoint() — УДАЛЁН (dead code)
```

### 3.4 bootstrap.sh (init — только build_ssh_cmd)

```
bootstrap.sh
  │  source build-ssh-cmd.sh  (было: source remote-cmd.sh)
  │  ...
  │  REMOTE_CMD="$(build_ssh_cmd "${NODE_NAME}" "${OWNER_KEY}" "${CI_DEPLOY_KEY}" \
  │      "${DETECTED_AGE_KEY}" "${PASSTHROUGH_ARGS[@]}")"
  │  ...
  │  exec ssh "${SSH_OPTS[@]}" "root@${SSH_HOST}" "${REMOTE_CMD}"
  │
  │  (bootstrap.sh сам делает SCP через scp-deliver.sh и SSH exec —
  │   не затрагивает execute_* функции)
```

---

## 4. File Manifest

| # | Файл | Действие | Тип | Описание |
|---|------|:--------:|-----|----------|
| F1 | `core/internal/bootstrap/build-ssh-cmd.sh` | CREATE | SHELL | ~100 LOC. build_ssh_cmd, build_update_ssh_cmd, build_converge_ssh_cmd (printf %q, D3). Извлечено из remote-cmd.sh:29-132. TRAP-аннотации сохранены. |
| F2 | `core/internal/bootstrap/remote_executor.py` | CREATE | PYTHON | ~200 LOC. RemoteExecutor класс + CLI (argparse). execute_update/converge/reconcile методы. Импортирует overlay_deliverer. SSH exec через subprocess. |
| F3 | `core/internal/bootstrap/remote-cmd.sh` | MODIFY | SHELL | 266→~60 LOC. Source build-ssh-cmd.sh. 3 thin-обёртки + deliver_vhost_overlays. Удалены _resolve_and_extract, execute_remote_reconcile_entrypoint. |
| F4 | `core/entrypoints/bootstrap.sh` | MODIFY | SHELL | Строка 35: `source remote-cmd.sh` → `source build-ssh-cmd.sh`. Всё остальное без изменений. |
| F5 | `core/entrypoints/node-update.sh` | MODIFY | SHELL | Minimal: если DRY_RUN использует build_update_ssh_cmd → добавить source build-ssh-cmd.sh. Иначе без изменений. |
| F6 | `core/entrypoints/converge.sh` | MODIFY | SHELL | Minimal: если нужен доступ к build_converge_ssh_cmd → source build-ssh-cmd.sh. Иначе без изменений. |
| F7 | `core/internal/bootstrap/AGENTS.md` | MODIFY | MARKDOWN | Обновить: remote-cmd.sh 266→60 LOC, +build-ssh-cmd.sh ~100 LOC, +remote_executor.py ~200 LOC. |
| F8 | `tests/unit/test_remote_executor.py` | CREATE | PYTHON | ~150 LOC. Unit-тесты: CLI parsing, exit codes, DRY_RUN, error propagation, VPS self-SSH detect. |

---

## 5. Acceptance Criteria

| AC | Описание | Верификация |
|----|----------|-------------|
| AC1 | remote_executor.py реализует execute_* с CLI | `python3 -m core.internal.bootstrap.remote_executor execute-update --help` выводит usage. Файл существует, ~200 LOC. |
| AC2 | remote-cmd.sh ≤ 60 LOC | `wc -l core/internal/bootstrap/remote-cmd.sh` → ≤ 60. build-ssh-cmd.sh существует с build-функциями. |
| AC3 | execute_remote_update идентичен | Python-метод проходит resolve → VPS detect → sync-core → ssh_exec. Exit codes: 0/1/2. Те же IMP:9/IMP:10 логи. |
| AC4 | execute_remote_converge идентичен | Аналогично AC3. Без sync-core. exit 2 при отсутствии хоста. |
| AC5 | execute_remote_reconcile идентичен | Аналогично converge + --reconcile в remote_cmd. execute_remote_reconcile_entrypoint удалён. |
| AC6 | DRY_RUN сохраняет поведение | `--dry-run` печатает команды (IMP:8), не вызывает ssh/rsync, exit 0. |
| AC7 | AGENTS.md обновлён | 266→60 LOC для remote-cmd.sh. Добавлены build-ssh-cmd.sh (~100 LOC) и remote_executor.py (~200 LOC). Обновлена таблица «Shell-фасады: сводка» (core/internal/bootstrap/AGENTS.md:247-254) — добавлена строка для remote-cmd.sh. Обновлён @rationale в самом remote-cmd.sh (строка 13: 672→~60 LOC). |
| AC8 | TRAP-аннотации сохранены | P0 VPS self-SSH, P1 PLATFORM_ROOT, P4 ssh_exec, D3 printf %q — все присутствуют в соответствующих файлах. |
| AC9 | Обратная совместимость | bootstrap.sh, node-update.sh, converge.sh работают без изменений в поведении. `make gate MODE=fast` — без новых FAIL. |

### Стратегия верификации AC3-AC6 (идентичность поведения)

Поскольку execute_remote_* функции выполняют SSH/rsync на удалённый VPS, прямое A/B тестирование в CI невозможно. Стратегия:

1. **Unit-тесты** (test_remote_executor.py): mock subprocess.run, проверка последовательности вызовов и exit codes
2. **Статический аудит**: построчное сравнение shell-логики (resolve → VPS detect → sync-core → ssh_exec) с Python-эквивалентом
3. **IMP:9 трейс-логи**: Python-модуль выводит те же IMP:9 логи, что и shell-версия — верификация через caplog в тестах
4. **E2E на test-VPS** (опционально, не блокирует merge): `make node-update NODE=<test>` на тестовой ноде — ручная верификация

---

## 6. $TASKS

| ID | Задача | Роль | Выход | AC | Зависимости | Сложность |
|----|--------|------|-------|----|-------------|:---------:|
| TASK-1 | Создать build-ssh-cmd.sh: извлечь build_*_ssh_cmd из remote-cmd.sh | Coder | F1 | AC2, AC8 | — | 3 |
| TASK-2 | Создать remote_executor.py: RemoteExecutor класс + CLI | Coder | F2 | AC1, AC3, AC4, AC5, AC6, AC8 | TASK-1 | 7 |
| TASK-3 | Сократить remote-cmd.sh до тонкого фасада (~60 LOC) | Coder | F3 | AC2, AC5 | TASK-1, TASK-2 | 4 |
| TASK-4 | Обновить caller'ы: bootstrap.sh, node-update.sh, converge.sh | Coder | F4, F5, F6 | AC9 | TASK-1, TASK-3 | 3 |
| TASK-5 | Обновить AGENTS.md: LOC + архитектура | Coder | F7 | AC7 | TASK-3 | 2 |
| TASK-6 | Unit-тесты remote_executor.py | Coder | F8 | AC1, AC3, AC4, AC6 | TASK-2 | 5 |
| TASK-7 | Интеграционная верификация: make gate MODE=fast, test-inventory-sync | QA | — | AC9 | TASK-4, TASK-6 | 3 |

**Merge-rule check:**
- TASK-5 (AGENTS.md, 1 файл, <20 строк изменений) → ВЛИВАЕТСЯ в TASK-3 (оба модифицируют AGENTS.md контекст, но TASK-5 зависит от завершения TASK-3). **Решение: merge TASK-5 в TASK-3** — обновление AGENTS.md часть задачи сокращения фасада.
- TASK-1 (build-ssh-cmd.sh) — самостоятельный (новый файл), без зависимостей. Оставить.

**Критический путь:** TASK-1 → TASK-2 → TASK-6 → TASK-7

---

## 7. $PARALLEL_GROUPS

### Wave 1 (независимые, нет shared files)
- **TASK-1** — Создать build-ssh-cmd.sh (новый файл F1, не пересекается ни с чем)

### Wave 2 (зависит от TASK-1)
- **TASK-2** — Создать remote_executor.py (новый файл F2, не пересекается с другими)

### Wave 3 (зависит от TASK-1 + TASK-2, shared files с TASK-3 + TASK-5 → последовательно)
- **TASK-3 + TASK-5** (merged) — Сократить remote-cmd.sh + обновить AGENTS.md
  - TASK-5 влит в TASK-3 (оба затрагивают документацию bootstrap, <20 строк)

### Wave 4 (зависит от TASK-1 + TASK-3)
- **TASK-4** — Обновить caller'ы: bootstrap.sh, node-update.sh, converge.sh
  - Файлы F4/F5/F6 не пересекаются с F2/F8

### Wave 5 (зависит от TASK-2)
- **TASK-6** — Unit-тесты remote_executor.py
  - F8 — новый файл, не пересекается с другими

### Wave 6 (зависит от TASK-4 + TASK-6)
- **TASK-7** — Интеграционная верификация

```
Wave 1: TASK-1
  ↓
Wave 2: TASK-2
  ↓
Wave 3: TASK-3 (+TASK-5 merged)
  ↓
Wave 4: TASK-4
  ↓
Wave 5: TASK-6
  ↓
Wave 6: TASK-7
```

**Фактический параллелизм:** Wave 4 и Wave 5 могут выполняться параллельно (разные файлы, зависимости удовлетворены Wave 3 и Wave 2 соответственно).

---

## 8. $TEST_SPEC

| Test file | Test function | Scenario | Module under test |
|-----------|---------------|----------|-------------------|
| `tests/unit/test_remote_executor.py` | `test_cli_execute_update_help` | CLI subcommand выводит usage | `remote_executor.cli()` |
| `tests/unit/test_remote_executor.py` | `test_execute_update_no_host_returns_2` | Нет SSH host → exit code 2 (local fallback) | `RemoteExecutor.execute_update()` |
| `tests/unit/test_remote_executor.py` | `test_execute_update_vps_self_ssh_returns_2` | VPS self-SSH detect → exit code 2 | `RemoteExecutor.execute_update()` |
| `tests/unit/test_remote_executor.py` | `test_execute_update_dry_run_exits_0` | DRY_RUN печатает команды, не выполняет, exit 0 | `RemoteExecutor.execute_update()` |
| `tests/unit/test_remote_executor.py` | `test_execute_update_sync_core_fails_returns_1` | sync-core failure → exit code 1 | `RemoteExecutor.execute_update()` |
| `tests/unit/test_remote_executor.py` | `test_execute_update_ssh_exec_success_returns_0` | SSH exec успешен → exit code 0 | `RemoteExecutor.execute_update()` |
| `tests/unit/test_remote_executor.py` | `test_execute_update_ssh_exec_timeout_returns_124` | SSH timeout → exit code 124 | `RemoteExecutor.execute_update()` |
| `tests/unit/test_remote_executor.py` | `test_execute_converge_no_sync_core` | Converge НЕ вызывает sync-core (в отличие от update) | `RemoteExecutor.execute_converge()` |
| `tests/unit/test_remote_executor.py` | `test_execute_reconcile_adds_reconcile_flag` | Reconcile передаёт --reconcile в remote_cmd | `RemoteExecutor.execute_reconcile()` |
| `tests/unit/test_remote_executor.py` | `test_ldd_imp9_logs_on_success` | При успешном SSH exec выводятся IMP:9 логи (caplog) | `RemoteExecutor` (LDD trajectory) |
| `tests/unit/test_remote_executor.py` | `test_resolve_node_failure_returns_1` | NodeYaml не найден → exit 1, IMP:10 лог | `RemoteExecutor.execute_update()` |

**Test Honesty:**
- R1 (no pass-tests): каждый тест содержит assert на exit code/raised exception/log message
- R2 (no unfalsifiable): все asserts проверяют бизнес-логику (exit codes, mock-вызовы, логи), не language guarantees
- LDD caplog: `test_ldd_imp9_logs_on_success` проверяет минимум 1 IMP:9 лог при успешном сценарии

---

## 9. Risks & Mitigations

| Риск | Вероятность | Mitigation |
|------|:-----------:|------------|
| R1: Build-функции не работают после извлечения в отдельный файл | LOW | Извлечение — copy-paste без изменений. bootstrap.sh source-ит новый файл. `make gate MODE=fast` после изменений. |
| R2: Python ssh_exec (subprocess.run) поведение отличается от shell ssh_exec | MEDIUM | Python зеркалит SSH_OPTS из overlay_deliverer.py (те же флаги, что SSH_OPTS_COMMON). Timeout через `subprocess.run(timeout=N)`. Test test_execute_update_ssh_exec_timeout_returns_124 верифицирует timeout-детекцию. |
| R3: DRY_RUN поведение расходится | LOW | Python проверяет `--dry-run` флаг ДО любых мутаций. Печатает команды в stderr с IMP:8. Unit-тест test_execute_update_dry_run_exits_0. |
| R4: execute_remote_reconcile_entrypoint всё ещё вызывается где-то (не найден grep'ом) | LOW | Двойная проверка: grep по всем .sh + поиск в Python коде. Если найден caller — добавить обратно как thin wrapper. |
| R5: PLATFORM_ROOT экспорт в build-функциях теряется при переносе | LOW | Копирование строк 29-132 as-is. TRAP[BUG] P1 документирует контекст. Верификация: diff build-ssh-cmd.sh <(sed -n '29,132p' remote-cmd.sh) → идентично. |
| R6: prepare_ssh_opts (ssh-keygen -R) в Python — разное поведение | LOW | Python вызывает `subprocess.run(["ssh-keygen", "-R", host])` — эквивалент shell. Update mode (preserve known_hosts) — просто не вызывает ssh-keygen -R. |
| R7: converge.sh:108 P0 inconsistency — нет `|| remote_rc=$?` catch (в отличие от node-update.sh:101) | LOW | Предсуществующая проблема, не вызвана DevPlan 101. converge.sh НЕ изменяется в F6 — `source remote-cmd.sh` остаётся без изменений. execute_remote_converge() продолжит возвращать exit codes через тот же API. Фикс — в отдельном DevPlan. |

---

## 10. Non-Goals

- ❌ НЕ трогать printf %q логику в build-функциях (D3)
- ❌ НЕ менять overlay_deliverer.py (421 LOC — стабильный модуль)
- ❌ НЕ мигрировать build-функции в Python (D3 запрещает)
- ❌ НЕ добавлять новые shell-функции в remote-cmd.sh
- ❌ НЕ трогать scp-deliver.sh (prepare_ssh_opts — остаётся для bootstrap init)
- ❌ НЕ запускать E2E тесты на production VPS
- ❌ НЕ менять entrypoint-manifest.yaml (глаголы не меняются)

---

## 11. Поправки к брифу

| Пункт брифа | Факт | Поправка в DevPlan |
|-------------|------|-------------------|
| «документирован как 230 LOC» | @rationale в remote-cmd.sh:13 говорит «672→~230 LOC shell facade». Фактически 266 строк. | AC7 исправляет AGENTS.md и @rationale в remote-cmd.sh: 266→60 |
| «Shell-фасад ≤ 60 LOC (printf %q builders + вызов Python)» | Build-функции содержат ~102 строки — невозможно уместить всё в ≤60 LOC без извлечения. | D1: build-функции → build-ssh-cmd.sh. remote-cmd.sh ≤60 LOC (только thin-обёртки). |
| «remote_executor.py (или расширение overlay_deliverer.py)» | overlay_deliverer.py уже 421 LOC. Добавление ещё ~200 LOC нарушит SRP. | D2: новый модуль remote_executor.py. Импортирует overlay_deliverer (композиция). |
| «execute_remote_reconcile_entrypoint() — 3 строки (passthrough)» | Фактически 8 строк (с region markers). Не вызывается ни одним внешним скриптом — dead code. | Удаляется полностью. Caller'ы converge.sh уже используют execute_remote_converge с --reconcile passthrough. |
| AC8: «PLATFORM_ROOT export» — TRAP P1 | TRAP[BUG] 2026-07-31 в build_ssh_cmd:33-46 — экспорт PLATFORM_ROOT через printf %q. | Остаётся в build-ssh-cmd.sh (неприкосновенно). |

---

## 12. Implementation Commands

### Wave 1 (TASK-1)
```
coder Read .ai/plans/101-remote-cmd-facade/02-DevPlan.md, implement Wave 1 TASK-1:
Create core/internal/bootstrap/build-ssh-cmd.sh by extracting lines 29-132 from remote-cmd.sh
(build_ssh_cmd, build_update_ssh_cmd, build_converge_ssh_cmd) with all TRAP annotations preserved.
Add MODULE_CONTRACT, GREP_SUMMARY, STRUCTURE.
```

### Wave 2 (TASK-2)
```
coder Read .ai/plans/101-remote-cmd-facade/02-DevPlan.md, implement Wave 2 TASK-2:
Create core/internal/bootstrap/remote_executor.py — RemoteExecutor class with execute_update,
execute_converge, execute_reconcile methods + argparse CLI. Import overlay_deliverer for
resolve/extract/sync-core. Mirror SSH_OPTS. Handle DRY_RUN, VPS self-SSH detect, exit codes.
```

### Wave 3 (TASK-3 + TASK-5 merged)
```
coder Read .ai/plans/101-remote-cmd-facade/02-DevPlan.md, implement Wave 3 TASK-3:
Reduce core/internal/bootstrap/remote-cmd.sh to ~60 LOC: source build-ssh-cmd.sh, 3 thin execute
wrappers calling Python CLI, deliver_vhost_overlays unchanged. Remove _resolve_and_extract,
execute_remote_reconcile_entrypoint. Update AGENTS.md (core/internal/bootstrap/) with new LOC counts.
```

### Wave 4+5 (TASK-4 + TASK-6 — parallel)
```
coder Read .ai/plans/101-remote-cmd-facade/02-DevPlan.md, implement Wave 4+5:
TASK-4: Update callers — bootstrap.sh source build-ssh-cmd.sh instead of remote-cmd.sh.
TASK-6: Create tests/unit/test_remote_executor.py with 11 unit tests as per $TEST_SPEC.
```

### Wave 6 (TASK-7)
```
coder Read .ai/plans/101-remote-cmd-facade/02-DevPlan.md, implement Wave 6 TASK-7:
Run make gate MODE=fast, make test-inventory-sync, verify AC9. Report results.
```

---

## QA Review (2026-07-31)

🔒 Verified against SHA `fbe306d4284d9105193605378be28eb64b3c6795`. 4 uncommitted files (not in DevPlan scope).

### Внесённые поправки

| # | Тип | Что исправлено | Обоснование |
|---|------|---------------|-------------|
| C1 | FACTUAL | Problem Matrix P2: execute_remote_update 43→39 LOC, execute_remote_converge 25→21 LOC, execute_remote_reconcile 25→21 LOC | Фактические размеры с region markers (lines 155-194, 199-220, 235-256) — подсчитаны через `read remote-cmd.sh`. Суммарная копипаста: ~81 строка (не 78). |
| C2 | FACTUAL | Problem Matrix P4: _resolve_and_extract 16→13 строк | Фактический размер: lines 137-149 (13 строк). |
| C3 | FACTUAL | Section 11 (@rationale reference): remote-cmd.sh:3→13 | @rationale находится на строке 13 (в поле MODULE_CONTRACT), не на строке 3 (STRUCTURE). |
| C4 | CLARITY | AC7: уточнён scope обновления AGENTS.md — таблица «Shell-фасады: сводка» (строки 247-254 bootstrap/AGENTS.md), @rationale в remote-cmd.sh:13 | Текущий AGENTS.md (bootstrap) содержит таблицу для top-3 shell-фасадов, но не для remote-cmd.sh. AC7 должен явно указать добавление строки в эту таблицу. |
| C5 | RISK | Добавлен R7: предсуществующая P0 неконсистентность в converge.sh:108 — нет `|| remote_rc=$?` catch (в отличие от node-update.sh:101) | Не вызвана DevPlan 101, но касается файла в File Manifest (F6). converge.sh НЕ изменяется — риск низкий. Фикс в отдельном DevPlan. |

### Проверки без замечаний

| Проверка | Результат |
|----------|-----------|
| AC1-AC8 coverage | ✅ Все 8 AC брифа отражены в DevPlan. AC9 добавлен как интеграционный. |
| TRAP сохранность | ✅ P0 VPS self-SSH loop, P1 PLATFORM_ROOT, P4 ssh_exec, D3 printf %q — все учтены в соответствующих файлах |
| execute_remote_reconcile_entrypoint dead code | ✅ 0 внешних вызовов (grep подтверждает: определено только в remote-cmd.sh:262-265). Удаление безопасно. |
| DRY_RUN coverage | ✅ Все entrypoints (bootstrap.sh, node-update.sh, converge.sh) прокидывают DRY_RUN. Python-модуль получает --dry-run через argparse. |
| Test Honesty R1/R2 | ✅ Test spec: каждый тест имеет assert на exit code/raised exception/log message. Нет unfalsifiable asserts. |
| LDD IMP:9 coverage | ✅ test_ldd_imp9_logs_on_success — anti-illusion правило выполнено |
| Формат $ARTIFACT_CONTRACT | ✅ 7 полей: PURPOSE, DESCRIPTION, RATIONALE, ACCEPTANCE_CRITERIA, IMPLEMENTS, IMPACTS, REQUIRES |
| $START_DEVPLAN / $END_DEVPLAN | ✅ Присутствуют |
| Нет заглушек | ✅ Нет TODO, placeholder, или незавершённых секций |
| Cross-plan dependencies (100-105) | ✅ Планы 100, 102, 104, 105 не существуют — конфликтов нет. Предупреждение: гипотетический Plan 104 (entrypoints) должен учитывать изменения в bootstrap.sh F4. |
| overlay_deliverer.py LOC | ✅ 421 строка — совпадает с DevPlan |
| remote-cmd.sh LOC | ✅ 266 строк — совпадает с DevPlan |
| bootstrap.sh line 35 | ✅ source remote-cmd.sh — совпадает с DevPlan |
| converge.sh / node-update.sh sourcing | ✅ Оба source remote-cmd.sh (lines 26, 23+89). F5/F6 изменения minimal — функциональный API execute_remote_* не меняется |
| P3 dead code claim | ✅ execute_remote_reconcile_entrypoint — 0 callers, подтверждено grep |

### Оставшиеся риски

| Риск | Severity | Описание |
|------|----------|----------|
| RSK1 | LOW | DRY_RUN тип: entrypoints используют boolean (true/false), lib/ssh.sh проверяет `DRY_RUN=1`. В текущем коде несовместимость не проявляется (early exit в remote-cmd.sh предотвращает достижение ssh_exec). Python-модуль использует argparse --dry-run — проблема side-stepped, не исправлена. |
| RSK2 | LOW | converge.sh:108 P0 inconsistency (нет catch для set -e). Предсуществующая, НЕ вызвана DevPlan 101. Требует отдельного фикса. |
| RSK3 | LOW | node-update.sh:23+89 — двойной source remote-cmd.sh (на уровне модуля + внутри main). Оппортунистическая чистка возможна, но не обязательна. |

### Вердикт

**APPROVED-WITH-CORRECTIONS**

DevPlan семантически корректен: все AC брифа покрыты, архитектурные решения обоснованы (D1-D4), TRAP-аннотации сохраняются, тест-спек покрывает ключевые сценарии. Внесённые поправки — фактические уточнения (LOC-подсчёты, ссылки на строки), не меняющие архитектурных решений или плана имплементации.

$END_DEVPLAN
