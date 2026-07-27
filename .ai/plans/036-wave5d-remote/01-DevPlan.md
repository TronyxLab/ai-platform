$START_DEVPLAN

# DevPlan 036-Wave5d — Strangler-Fig: remote-cmd.sh → overlay_deliverer.py

$ARTIFACT_CONTRACT
- **PURPOSE:** Декомпозиция `core/internal/bootstrap/remote-cmd.sh` (672 LOC) по методологии Strangler-Fig: извлечь `deliver_vhost_overlays()` + общие `resolve_node_yaml()` / `extract_node_host()` в Python-модуль `overlay_deliverer.py`. Shell-фасад сохраняет `printf %q` command builders (inherent shell per D3) и тонкие execute-обёртки, делегирующие node-resolution в Python.
- **DESCRIPTION:** Wave 3 (TASK-036D) master DevPlan 036. remote-cmd.sh содержит 8 функций: 3 build-функции (`build_ssh_cmd`, `build_update_ssh_cmd`, `build_converge_ssh_cmd`) с `printf %q` — остаются в shell; 4 execute-функции (`execute_remote_{update,converge,reconcile}` + entrypoint) — упрощаются через делегирование resolve/extract в Python; 1 функция `deliver_vhost_overlays()` — полностью мигрирует в Python. Новый модуль: `core/internal/bootstrap/overlay_deliverer.py` (~200 LOC Python) с 4 CLI-подкомандами: `resolve-node`, `extract-host`, `deliver`, `sync-core`.
- **RATIONALE:** Выполнение языковой политики (AGENTS.md: новый код — Python), устранение 140+ LOC дублирования node-resolution логики в 4 execute-функциях + 1 deliver-функции, повышение тестируемости overlay delivery pipeline (ранее 0 unit-тестов), сохранение `printf %q` command builders в shell (D3 — нет прямого аналога в Python).
- **ACCEPTANCE_CRITERIA:**
  - AC-1: Shell facade `remote-cmd.sh` ≤250 LOC (build-функции + execute-обёртки + header; printf %q builders — 135 LOC structural floor per D3)
  - AC-2: 0 inline `python3 -c` / `<<PYEOF` в shell-фасаде (нет новых — изначально отсутствовали)
  - AC-3: `deliver_vhost_overlays()` полностью в Python — shell вызывает `python3 -m core.internal.bootstrap.overlay_deliverer deliver --node <n>`
  - AC-4: `resolve_node_yaml()` и `extract_node_host()` в Python дают идентичный результат shell-версиям из `node-resolver.sh`
  - AC-5: `make test` зелёный, включая новые `tests/unit/test_overlay_deliverer.py` (≥5 unit-тестов, ≥80% coverage)
  - AC-6: `make gate MODE=fast` зелёный
  - AC-7: Dry-run режим `deliver_vhost_overlays` работает идентично (печатает rsync команду, не выполняет)
  - AC-8: Три TRAP[BUG] из remote-cmd.sh перенесены в Python-модуль и shell-фасад (ci_deploy_key not exported, VPS self-SSH loop, node-update core delivery)
  - AC-9: `make node-update NODE=<test>` работает идентично (rsync overlays + core → SSH exec)
- **IMPLEMENTS:** Wave 3 (TASK-036D) из master DevPlan 036 — remote-cmd.sh Strangler-Fig декомпозиция
- **IMPACTS:**
  - `core/internal/bootstrap/remote-cmd.sh` (672→~230 LOC) — shell facade
  - `core/internal/bootstrap/overlay_deliverer.py` (NEW ~200 LOC) — Python module
  - `tests/unit/test_overlay_deliverer.py` (NEW ~150 LOC) — unit tests
  - `core/internal/shared/ssh_command_parser.py` — НЕ затрагивается (существующий модуль DevPlan 081, не используется в данной миграции)
- **REQUIRES:**
  - Python ≥3.10, `pytest`, `pyyaml` (уже в проекте)
  - `core/lib/node-resolver.sh` — эталонная реализация `resolve_node_yaml()` и `extract_node_host()` (контракт для Python-порта)
  - `core/lib/ssh.sh` — `SSH_OPTS_COMMON`, `ssh_exec` (остаются в shell-фасаде)
  - `core/lib/paths.sh` — `PLATFORM_ROOT`, `PATHS_LIB_DIR` (остаются в shell-фасаде)
$END_ARTIFACT_CONTRACT

---

## Debt Intake

### TRAP Audit — remote-cmd.sh

Перед миграцией проведён аудит всех TRAP-аннотаций в целевом файле:

| # | TRAP | Строка | Приоритет | Статус |
|---|------|--------|:---:|--------|
| T1 | `TRAP[BUG]` ci_deploy_key from node.yaml not exported | L95 | P2 | **IN_SCOPE** — логика экспорта остаётся в shell `build_ssh_cmd()`, не переносится в Python. TRAP-комментарий сохраняется в shell-фасаде. |
| T2 | `TRAP[BUG]` VPS self-SSH loop | L279 | P0 | **IN_SCOPE** — проверка `/opt/platform/` остаётся в shell `execute_remote_update()`. TRAP переносится в shell-фасад (execute_remote_update) + документируется в Python-модуле как известное ограничение SSH-proxy. |
| T3 | `TRAP[BUG]` node-update не доставлял core/ | L294 | P0 | **IN_SCOPE** — rsync core/ логика переносится в Python `sync_core_to_vps()` (новая функция в overlay_deliverer.py). TRAP переносится в Python-модуль. |
| T4 | `TRAP[BUG]` bare ssh_exec may silently fail under set -e (×3) | L360, L483, L650 | P4 | **IN_SCOPE** — `|| { local rc=$?; ... }` паттерн остаётся в shell execute-функциях. TRAP сохраняется в shell-фасаде. |

### DEBT Registries — смежные модули

| Файл | Статус |
|------|--------|
| `036-wave5-strangler-shell-monoliths/01-DevPlan.md` | **DEFER** — родительский DevPlan уже учтён; все решения D1-D5 из него валидны |
| `036-wave5-strangler-shell-monoliths/02-VerificationReport.md` | **READ** — предыдущий верификационный отчёт (Wave 5 master); не содержит блокирующих замечаний для Wave 5d |

### Предсуществующие архитектурные решения

| # | Решение | Источник | Статус |
|---|---------|----------|:---:|
| D3 | `printf %q` command builders остаются в shell | DevPlan 036 §Design Decisions | **DEFER** — сохраняется без изменений |
| - | `execute_remote_*()` делегируют resolve/extract в Python | DevPlan 036 Wave 3 Data Flow | **IN_SCOPE** — реализуется через CLI-подкоманды overlay_deliverer.py |

---

## Requirements Analysis

### Ключевые критерии успеха

1. **Shell facade ≤250 LOC** — ключевая метрика. printf %q builders (65+55+15=135 LOC, D3) устанавливают структурный floor ~230 LOC, который не может быть сокращён без нарушения архитектурного решения D3. Оставшиеся ~95-115 LOC приходятся на execute-обёртки, boilerplate и helper-функции.
2. **Идентичное поведение** — `make node-update NODE=<test>` должен работать идентично до и после миграции (rsync overlays → rsync core → SSH exec update). Dry-run режим сохраняет вывод.
3. **Unit-тесты для Python** — 5+ тестов покрывают: resolve (found/not-found), extract host, deliver (no overlays, dry-run, with overlays mocked), sync-core (dry-run).
4. **Zero regression** — все существующие интеграционные/gate-тесты остаются зелёными. Ни одна execute-функция не теряет error handling (return 2 для local, exit 1 для fatal).
5. **TRAP-сохранность** — все 4 TRAP[BUG] из remote-cmd.sh перенесены в соответствующие локации (shell или Python).

### Текущее состояние (baseline)

| Функция | LOC | Роль | Миграция |
|---------|:---:|------|:---:|
| `build_ssh_cmd()` | ~70 | printf %q command builder (init) | **STAY** shell |
| `build_update_ssh_cmd()` | ~60 | printf %q command builder (update) | **STAY** shell |
| `build_converge_ssh_cmd()` | ~20 | printf %q command builder (converge) | **STAY** shell |
| `execute_remote_update()` | ~125 | SSH proxy flow: resolve→extract→rsync core→build→exec | **SIMPLIFY** shell → delegates resolve+extract+rsync-core to Python |
| `execute_remote_converge()` | ~55 | SSH proxy flow: resolve→extract→build→exec | **SIMPLIFY** shell → delegates resolve+extract to Python |
| `execute_remote_reconcile()` | ~58 | SSH proxy flow: resolve→extract→build→exec | **SIMPLIFY** shell → delegates resolve+extract to Python |
| `execute_remote_reconcile_entrypoint()` | ~2 | Thin wrapper | **STAY** shell |
| `deliver_vhost_overlays()` | ~70 | Overlay rsync pipeline | **MOVE** to Python |
| MODULE_CONTRACT + header + sources | ~30 | Boilerplate | **STAY** shell (trimmed) |

**Всего сейчас:** ~490 LOC logic + ~30 header = ~520 LOC (без учёта пустых строк/комментариев). Shell facade после миграции: ~230 LOC.

---

## Architecture Overview

### Superposition Analysis (4 опции для remote-cmd.sh)

#### Option A: Hybrid shell+Python (рекомендованный — совпадает с master DevPlan) [score: 8/10]

**Подход:** `printf %q` builders + execute-обёртки → shell (~230 LOC). `deliver_vhost_overlays()` + `resolve_node_yaml()` + `extract_node_host()` + `sync_core_to_vps()` → Python (~200 LOC). Python CLI с 4 подкомандами для вызова из shell.

**Trade-offs:**
- ➕ Языковая политика выполнена (новая бизнес-логика в Python)
- ➕ `printf %q` command builders не сломаны (inherent shell)
- ➕ Unit-тесты для overlay delivery + node resolution (ранее 0)
- ➖ Shell facade требует поддержки 2 языков (shell + Python CLI)
- ➖ Python-модуль запускается через subprocess из shell — latency overhead (~50ms per call)

**Best when:** баланс risk/reward, сохранение стабильных printf %q builders

#### Option B: Full Python `subprocess.run` [score: 6/10]

**Подход:** Весь remote-cmd.sh → Python. Command builders переписываются на `shlex.quote()` + f-строки. SSH exec через `subprocess.run(["ssh", ...])`.

**Trade-offs:**
- ➕ Полное соответствие языковой политике
- ➕ Единый язык — проще поддержка
- ➖ `shlex.quote()` НЕ идентичен `printf '%q'` для edge cases с env vars и спецсимволами → risk injection
- ➖ Высокий risk регрессии для критических SSH-proxy операций (bootstrap, node-update, converge)
- ➖ Большой объём работы (672 LOC → ~500 LOC Python + тесты)

**Rejected:** risk несовместимости quoting превышает benefit унификации

#### Option C: Leave as-is (baseline) [score: 3/10]

**Подход:** remote-cmd.sh без изменений. Только TRAP-документирование.

**Trade-offs:**
- ➖ Нарушение языковой политики (672 LOC shell)
- ➖ 0 unit-тестов для overlay delivery
- ➖ Дублирование resolve/extract логики в 5 функциях
- ➕ Нулевой risk регрессии

**Rejected:** остаётся крупным монолитом; языковая политика требует прогресса

#### Option D: Reverse Strangler — Python Core, Shell Plugins [score: 5/10]

**Подход:** Python `RemoteCommandOrchestrator` класс — центральный engine. Shell-функции сводятся к `subprocess.run()` вызовам ТОЛЬКО для printf %q quoting.

**Trade-offs:**
- ➕ Чистая архитектурная граница
- ➖ Over-engineered для 672 LOC скрипта (оркестратор + plugin system = >300 LOC boilerplate)
- ➖ Противоречит Small Simple Blocks

**Rejected:** избыточно для данного масштаба

### Scoring Matrix

| Dimension | A (Hybrid) | B (Full Python) | C (Leave) | D (Reverse) |
|-----------|:---:|:---:|:---:|:---:|
| Risk to production | 8 | 4 | 10 | 5 |
| Code quality gain | 7 | 8 | 0 | 6 |
| Implementation speed | 8 | 4 | 10 | 3 |
| Testability gain | 7 | 9 | 0 | 8 |
| Lang policy compliance | 7 | 10 | 0 | 9 |
| Shell facade ≤250 LOC | 8 | N/A | 0 | 7 |
| **Composite** | **7.5** | **5.8** | **3.3** | **6.3** |

### Recommendation: Option A — Hybrid shell+Python (score: 7.5)

**Обоснование:**
1. **D3 precedent:** master DevPlan 036 явно предписывает сохранение `printf %q` builders в shell. Option A — единственная опция, соблюдающая это архитектурное решение.
2. **Risk profile:** минимальные изменения SSH-proxy execute-функций (только делегирование resolve/extract). Основная миграция затрагивает изолированную `deliver_vhost_overlays()`.
3. **Testability:** Python-модуль получает unit-тесты (ранее 0 coverage для delivery pipeline).
4. **Composite победитель (7.5).** Options B и D проигрывают по risk, Option C — по всем метрикам кроме risk.

---

## Step-by-Step Data Flow

### ДО миграции

```
remote-cmd.sh (672 LOC)
├── build_ssh_cmd()              # printf %q — init mode command builder
├── build_update_ssh_cmd()       # printf %q — update mode command builder
├── build_converge_ssh_cmd()     # printf %q — converge mode command builder
├── execute_remote_update()      # FULL FLOW:
│   ├── source node-resolver.sh  # (дублирование)
│   ├── source scp-deliver.sh    # (дублирование)
│   ├── resolve_node_yaml()      # shell inline call
│   ├── extract_node_host()      # shell inline call + VPS self-SSH check
│   ├── prepare_ssh_opts()       # shell (scp-deliver.sh)
│   ├── rsync core/ to VPS       # 35+ LOC inline (TRAP T3 fix)
│   ├── build_update_ssh_cmd()   # shell
│   ├── DRY_RUN check → exit 0
│   └── ssh_exec()               # shell (lib/ssh.sh)
├── execute_remote_converge()    # SIMILAR PATTERN: resolve→extract→build→exec
├── execute_remote_reconcile()   # SIMILAR PATTERN + --reconcile flag
├── execute_remote_reconcile_entrypoint()  # Thin wrapper
└── deliver_vhost_overlays()     # FULL FLOW:
    ├── source node-resolver.sh  # (дублирование)
    ├── source scp-deliver.sh    # (дублирование)
    ├── resolve_node_yaml()      # shell inline call
    ├── extract_node_host()      # shell inline call
    ├── check overlay dir        # shell
    ├── DRY_RUN check → return 0
    ├── prepare_ssh_opts()       # shell
    ├── mkdir -p remote dir      # shell via ssh_exec
    └── rsync overlays           # shell inline
```

### ПОСЛЕ миграции

```
remote-cmd.sh (~230 LOC, shell facade)
├── MODULE_CONTRACT (trimmed, 12 LOC)
├── source paths.sh + ssh.sh (8 LOC)
│
├── build_ssh_cmd()              # 65 LOC — STAY (printf %q)
├── build_update_ssh_cmd()       # 55 LOC — STAY (printf %q)
├── build_converge_ssh_cmd()     # 15 LOC — STAY (printf %q)
│
├── _resolve_and_extract()       # 12 LOC — NEW helper (calls Python CLI)
│   # python3 -m core.internal.bootstrap.overlay_deliverer resolve-node|extract-host
│
├── execute_remote_update()      # 22 LOC — SIMPLIFIED
│   ├── _resolve_and_extract()       → Python resolve-node + extract-host
│   ├── VPS self-SSH check           → shell (TRAP T2)
│   ├── prepare_ssh_opts()           → shell
│   ├── python3 overlay_deliverer sync-core  → Python (TRAP T3)
│   ├── build_update_ssh_cmd()       → shell
│   ├── DRY_RUN check                → shell
│   └── ssh_exec()                   → shell
│
├── execute_remote_converge()   # 17 LOC — SIMPLIFIED
│   ├── _resolve_and_extract()       → Python
│   ├── prepare_ssh_opts()           → shell
│   ├── build_converge_ssh_cmd()     → shell
│   ├── DRY_RUN check                → shell
│   └── ssh_exec()                   → shell
│
├── execute_remote_reconcile()  # 17 LOC — SIMPLIFIED (аналогично converge)
├── execute_remote_reconcile_entrypoint()  # 2 LOC — STAY
│
└── deliver_vhost_overlays()    # 3 LOC — FACADE
    └── python3 -m core.internal.bootstrap.overlay_deliverer deliver --node "${node_name}" ${DRY_RUN:+--dry-run}


core/internal/bootstrap/overlay_deliverer.py (~200 LOC, Python)
├── resolve_node_yaml(node_name, platform_root, projects_dir) → str|None
│   # Python-порт node-resolver.sh resolve_node_yaml()
│   # 3-path search: platform-local → org repos → /opt/node-configs/
│
├── extract_node_host(yaml_path) → str
│   # Python-порт node-resolver.sh extract_node_host()
│   # pyyaml safe_load → data['node']['host']
│
├── sync_core_to_vps(host, core_src, node_name, node_yaml, dry_run) → bool
│   # Python-порт rsync core/ логики из execute_remote_update (TRAP T3)
│   # subprocess.run rsync core/ + node.yaml → VPS
│   # Dry-run: print command, return True
│
├── deliver_vhost_overlays(node_name, platform_root, dry_run) → DeliveryResult
│   # Полный порт deliver_vhost_overlays() из shell
│   # 1. resolve_node_yaml → extract_node_host
│   # 2. Check local overlay dir → find *.conf files
│   # 3. Dry-run: print SSH+rsync commands
│   # 4. ssh_exec mkdir -p remote dir
│   # 5. rsync overlays → VPS
│
└── CLI (argparse, 4 subcommands):
    ├── resolve-node --node <n> [--platform-root <p>] [--projects-dir <d>]
    ├── extract-host --yaml <path>
    ├── sync-core --host <h> --core-src <d> [--node <n> --node-yaml <p>] [--dry-run]
    └── deliver --node <n> [--dry-run]
```

### Сокращение shell LOC

| Блок | Было (LOC) | Стало (LOC) | Сокращение |
|------|:---:|:---:|:---:|
| MODULE_CONTRACT + header | 30 | 12 | 60% |
| source guards | 14 | 8 | 43% |
| `build_ssh_cmd()` | 74 | 65 | 12% |
| `build_update_ssh_cmd()` | 60 | 55 | −8% |
| `build_converge_ssh_cmd()` | 20 | 15 | 25% |
| `_resolve_and_extract()` (NEW helper) | — | 12 | NEW |
| `execute_remote_update()` | 125 | 22 | 82% |
| `execute_remote_converge()` | 55 | 17 | 69% |
| `execute_remote_reconcile()` | 58 | 17 | 71% |
| `execute_remote_reconcile_entrypoint()` | 2 | 2 | — |
| `deliver_vhost_overlays()` | 70 | 3 | 96% |
| **Total (логика)** | **~490** | **~228** | **53%** |
| **Total (файл, с комментариями)** | **672** | **~230** | **66%** |

> **Примечание 1 (LOC structural floor):** Сумма post-migration LOC: printf %q builders (65+55+15=135 LOC, D3 — retention mandatory) + execute wrappers (22+17+17+2=58 LOC) + `_resolve_and_extract()` helper (12 LOC) + boilerplate (12+8=20 LOC) + deliver facade (3 LOC) = 228 LOC logic minimum. printf %q builders (135 LOC) устанавливают структурный floor: они не могут быть сокращены без нарушения архитектурного решения D3. Реалистичная оценка общего файла с учётом пробелов/комментариев: ~230 LOC.
>
> **Примечание 2 (Python module estimate drift):** Оценка Python-модуля выросла с 150 LOC (master DevPlan 036, line 460) до ~200 LOC. Причины: (а) добавлена `sync_core_to_vps()` — rsync core/ логика, не предусмотренная master DevPlan; (б) 4-подкомандный CLI с argparse boilerplate; (в) полный error handling с кастомными исключениями. Увеличение обосновано детальным проектированием и отражает реальную сложность миграции.
>
> **Примечание 3 (master VerificationReport cross-reference):** Master VerificationReport (line 260) рекомендовал "Clarify AC-1 exception for remote-cmd.sh (~200 LOC due to printf %q builders)". Настоящий DevPlan наследует это исключение: AC-1 установлен в ≤250 LOC с учётом структурного floor.

---

## Draft Code Graph

```
core/internal/bootstrap/
├── remote-cmd.sh                 # → ~230 LOC (shell facade)
│   ├── build_ssh_cmd()           #   STAY — printf %q, 65 LOC
│   ├── build_update_ssh_cmd()    #   STAY — printf %q, 55 LOC
│   ├── build_converge_ssh_cmd()  #   STAY — printf %q, 15 LOC
│   ├── _resolve_and_extract()    #   NEW helper — calls Python CLI, 12 LOC
│   ├── execute_remote_update()   #   SIMPLIFIED — delegates to Python, 22 LOC
│   ├── execute_remote_converge() #   SIMPLIFIED — delegates to Python, 17 LOC
│   ├── execute_remote_reconcile()#   SIMPLIFIED — delegates to Python, 17 LOC
│   ├── execute_remote_reconcile_entrypoint()  # STAY, 2 LOC
│   └── deliver_vhost_overlays()  #   FACADE — one-line Python call, 3 LOC
│
└── overlay_deliverer.py          # NEW ~200 LOC (Python module)
    ├── resolve_node_yaml(node_name, platform_root, projects_dir) → str|None
    ├── extract_node_host(yaml_path) → str
    ├── sync_core_to_vps(host, core_src, node_name, node_yaml, dry_run) → bool
    ├── deliver_vhost_overlays(node_name, platform_root, dry_run) → DeliveryResult
    └── CLI: resolve-node | extract-host | sync-core | deliver

tests/unit/
└── test_overlay_deliverer.py     # NEW ~150 LOC (unit tests)
    ├── test_resolve_node_yaml_found()
    ├── test_resolve_node_yaml_not_found()
    ├── test_extract_node_host_with_host()
    ├── test_extract_node_host_empty()
    ├── test_deliver_no_overlays()
    ├── test_deliver_dry_run()
    ├── test_deliver_with_overlays()
    ├── test_sync_core_dry_run()
    └── test_sync_core_rsync_failure()

core/lib/
└── node-resolver.sh              # UNCHANGED — эталонная реализация (контракт для Python-порта)
```

### Contract — overlay_deliverer.py CLI

```
$ python3 -m core.internal.bootstrap.overlay_deliverer resolve-node --node prod-web
/Users/tronyx/projects/tronyx161/node-configs/prod-web/node.yaml

$ python3 -m core.internal.bootstrap.overlay_deliverer resolve-node --node nonexistent
(exit 1, stderr: "node.yaml not found for node=nonexistent")

$ python3 -m core.internal.bootstrap.overlay_deliverer extract-host --yaml /path/to/node.yaml
1.2.3.4

$ python3 -m core.internal.bootstrap.overlay_deliverer extract-host --yaml /path/to/node.yaml
(empty stdout — no host field)

$ python3 -m core.internal.bootstrap.overlay_deliverer sync-core --host 1.2.3.4 --core-src /opt/platform/core --node prod-web --node-yaml /path/to/node.yaml
(rsync core/ + node.yaml → exit 0)

$ python3 -m core.internal.bootstrap.overlay_deliverer sync-core --host 1.2.3.4 --core-src /opt/platform/core --dry-run
[DRY-RUN] rsync ... → exit 0

$ python3 -m core.internal.bootstrap.overlay_deliverer deliver --node prod-web
[IMP:9] Delivering 3 vhost overlay(s) ...
(exit 0)

$ python3 -m core.internal.bootstrap.overlay_deliverer deliver --node prod-web --dry-run
[DRY-RUN] rsync ... → exit 0
```

### LDD Contract — IMP levels

| IMP | Где | Событие |
|:---:|-----|---------|
| 10 | `resolve_node_yaml` | node.yaml not found (fatal) |
| 10 | `extract_node_host` | YAML parse error (fatal) |
| 10 | `sync_core_to_vps` | rsync failed (fatal) |
| 10 | `deliver_vhost_overlays` | rsync overlays failed / mkdir failed (fatal) |
| 9 | `resolve_node_yaml` | Successfully resolved path |
| 9 | `deliver_vhost_overlays` | Overlay delivery started / completed |
| 9 | `sync_core_to_vps` | core/ rsync started / completed |
| 8 | `deliver_vhost_overlays` | No overlays / no host / dry-run skip |
| 8 | `resolve_node_yaml` | Begin search for node.yaml |
| 7 | `sync_core_to_vps` | Subprocess stdout/stderr capture |

---

## Design Decisions

### ## @rationale D1: resolve_node_yaml + extract_node_host → Python (порт из node-resolver.sh)

**Q:** Зачем портировать `resolve_node_yaml()` и `extract_node_host()` в Python, если они уже есть в `node-resolver.sh`?

**A:** Три причины:
1. **Устранение дублирования source-вызовов.** Каждая execute-функция и `deliver_vhost_overlays()` сейчас делают `source node-resolver.sh` + `source scp-deliver.sh` — 5 дублирующих source-блоков. Python-порт устраняет эти source-вызовы: shell facade вызывает Python CLI один раз.
2. **Устранение inline `python3 -c` в extract_node_host.** Функция `extract_node_host()` в `node-resolver.sh` (строки 306-316) содержит inline `python3 -c` блок с pyyaml — нарушение языковой политики (Tier 1 Strangler trigger). Python-порт устраняет этот блок.
3. **Тестируемость.** Shell-функции `resolve_node_yaml()` и `extract_node_host()` не имеют unit-тестов. Python-порт получает тесты через `test_overlay_deliverer.py`.

**Контрактная совместимость:** Python-реализация воспроизводит логику shell-версий 1:1:
- `resolve_node_yaml`: 3-path search (platform-local → org repos glob → /opt/node-configs/), первый найденный — результат
- `extract_node_host`: `yaml.safe_load` → `data.get('node', {}).get('host', '') or ''`

### ## @rationale D2: sync_core_to_vps в Python (порт rsync core/ логики)

**Q:** Почему rsync core/ логика переносится в Python, а не остаётся в shell `execute_remote_update()`?

**A:** Три причины:
1. **AC-1 (shell ≤250 LOC).** rsync core/ блок в `execute_remote_update()` занимает ~40 LOC (строки 299-337): вычисление путей, два rsync вызова (core/ + node.yaml), обработка ошибок, dry-run ветка. Перенос в Python сокращает execute_remote_update с ~125 до ~22 LOC — ключевой вклад в достижение лимита.
2. **TRAP T3 документирует этот блок как bug fix.** Логика rsync core/ была добавлена как исправление бага (node-update не доставлял core). Перенос в Python с сохранением TRAP-комментария гарантирует, что будущие агенты увидят rationale.
3. **Тестируемость.** Python-функция `sync_core_to_vps()` получает unit-тесты (dry-run, rsync failure). Shell-версия не тестировалась.

### ## @rationale D3: printf %q command builders остаются в shell

**Q:** Подтверждение master DevPlan D3. Почему не переносить `build_ssh_cmd()` / `build_update_ssh_cmd()` / `build_converge_ssh_cmd()` в Python?

**A:** `printf '%q'` — bash-builtin для shell-safe quoting. Python-аналог `shlex.quote()` **не идентичен** для edge cases:
- `printf '%q'` экранирует пробелы, кавычки, спецсимволы специфичным для bash образом
- `shlex.quote()` использует single-quote wrapping (POSIX), что может дать другой результат для переменных с `$` и backtick'ами
- SSH command передаётся как строка в `bash -c "..."` на удалённой стороне — quoting ДОЛЖЕН быть bash-совместимым

**Решение:** command builders остаются в shell. Python управляет flow control и error handling. Это гибридный подход: shell отвечает за quoting, Python — за оркестрацию.

### ## @rationale D4: _resolve_and_extract helper — одна точка вызова Python CLI

**Q:** Почему не дублировать вызовы Python CLI в каждой execute-функции?

**A:** DRY-first. Все 4 execute-функции (`execute_remote_{update,converge,reconcile}` + `deliver_vhost_overlays`) используют одинаковый паттерн: `resolve_node_yaml` → `extract_node_host`. Вынос в `_resolve_and_extract()` helper:
- Устраняет 4 дублирующих блока по ~6 строк каждый
- Единая точка для error handling (return 1 если resolve failed, return 2 если host пустой)
- Shell facade: 12 LOC helper вместо 24 LOC дублирования

### ## @rationale D5: deliver_vhost_overlays — полный перенос в Python

**Q:** Почему `deliver_vhost_overlays()` переносится полностью, а execute-функции — частично?

**A:** Разная природа функций:
- `deliver_vhost_overlays()` — **бизнес-логика** (resolve → check dir → rsync overlays). Никакого `printf %q`. Полный перенос в Python не создаёт risk несовместимости quoting.
- execute-функции — **SSH proxy** (build cmd с printf %q → exec SSH). Command builders и SSH exec — inherent shell. Перенос только resolve/extract + rsync core (безопасные компоненты).

### ## @rationale D6: Python CLI subcommands, не единый entrypoint

**Q:** Почему 4 подкоманды (`resolve-node`, `extract-host`, `sync-core`, `deliver`), а не один вызов `python3 overlay_deliverer.py --node <n>`?

**A:** Single Responsibility Principle:
- `resolve-node` — только разрешение пути (stdout: path, exit 0/1)
- `extract-host` — только извлечение хоста (stdout: host, exit 0/1)
- `sync-core` — только rsync core/ (exit 0/1)
- `deliver` — полный пайплайн (exit 0/1)

Каждая подкоманда имеет одну ответственность, что упрощает тестирование и shell-интеграцию (`cmd="$(python3 ... resolve-node ...)"` — чистый command substitution).

---

## $TASKS

### TASK-036D: Wave 3 — remote-cmd.sh → overlay_deliverer.py + shell facade

- **Owner:** Coder
- **Output:**
  - `core/internal/bootstrap/overlay_deliverer.py` (~200 LOC) — Python module с 4 CLI-подкомандами
  - `core/internal/bootstrap/remote-cmd.sh` (~230 LOC) — обновлённый shell facade
  - `tests/unit/test_overlay_deliverer.py` (~150 LOC) — unit-тесты
- **Acceptance:**
  - AC-1: `wc -l core/internal/bootstrap/remote-cmd.sh` ≤ 250
  - AC-2: `grep -c "python3 -c\|<<PYEOF" core/internal/bootstrap/remote-cmd.sh` = 0 (новых нет)
  - AC-3: `deliver_vhost_overlays()` в shell — одна строка вызова Python
  - AC-4: `python3 -m core.internal.bootstrap.overlay_deliverer resolve-node --node <test>` возвращает корректный путь
  - AC-5: `python3 -m core.internal.bootstrap.overlay_deliverer extract-host --yaml <test>` возвращает корректный host
  - AC-6: `python3 -m pytest tests/unit/test_overlay_deliverer.py -s -v` — все тесты зелёные, ≥80% coverage
  - AC-7: `make test` зелёный (все существующие тесты не сломаны)
  - AC-8: `make gate MODE=fast` зелёный
  - AC-9: Все 4 TRAP[BUG] из remote-cmd.sh сохранены в новых локациях
- **Dependencies:** None (не зависит от TASK-036A/B/C/E/F/G; может идти параллельно с Wave 2)
- **Complexity:** 4/10
- **Estimated effort:** 2-3 часа (1 Coder session)
- **Critical path:** Нет — TASK-036D независим, может выполняться параллельно с TASK-036A, 036B, 036C
- **Checkpoint:** `make test` зелёный, shell facade ≤250 LOC, 0 inline python3 (новых)

### Merge Rule Check

- **Files count:** 3 файла (remote-cmd.sh MODIFY, overlay_deliverer.py NEW, test_overlay_deliverer.py NEW)
- **Estimated lines of change:** ~400 LOC (Python + тесты) + ~470 LOC удалено из shell
- **Verdict:** Не подлежит слиянию — самостоятельная задача с 3 файлами и существенным объёмом изменений. Оставить как TASK-036D.

---

## $TEST_SPEC

| Test file | Test function | Scenario | Module under test |
|-----------|---------------|----------|-------------------|
| `tests/unit/test_overlay_deliverer.py` | `test_resolve_node_yaml_found` | node.yaml найден по path 1 (platform-local) — возвращает путь | `overlay_deliverer.resolve_node_yaml()` |
| `tests/unit/test_overlay_deliverer.py` | `test_resolve_node_yaml_not_found` | node.yaml не найден ни по одному из 3 путей — raise NodeYamlNotFoundError | `overlay_deliverer.resolve_node_yaml()` |
| `tests/unit/test_overlay_deliverer.py` | `test_extract_node_host_with_host` | node.yaml содержит `node.host: "1.2.3.4"` — возвращает "1.2.3.4" | `overlay_deliverer.extract_node_host()` |
| `tests/unit/test_overlay_deliverer.py` | `test_extract_node_host_empty` | node.yaml без поля `node.host` — возвращает "" | `overlay_deliverer.extract_node_host()` |
| `tests/unit/test_overlay_deliverer.py` | `test_deliver_no_overlays` | Директория overlays/nginx/ пуста или не существует — graceful skip, exit 0 | `overlay_deliverer.deliver_vhost_overlays()` |
| `tests/unit/test_overlay_deliverer.py` | `test_deliver_dry_run` | Dry-run mode — печатает команды, НЕ выполняет rsync/ssh, exit 0 | `overlay_deliverer.deliver_vhost_overlays()` |
| `tests/unit/test_overlay_deliverer.py` | `test_deliver_with_overlays_mocked` | Есть .conf файлы, mocked subprocess (rsync/ssh) — проверка корректности вызовов | `overlay_deliverer.deliver_vhost_overlays()` |
| `tests/unit/test_overlay_deliverer.py` | `test_sync_core_dry_run` | Dry-run sync-core — печатает rsync команду, не выполняет, exit 0 | `overlay_deliverer.sync_core_to_vps()` |
| `tests/unit/test_overlay_deliverer.py` | `test_sync_core_rsync_failure` | rsync возвращает non-zero exit code — raise SyncCoreError | `overlay_deliverer.sync_core_to_vps()` |
| `tests/unit/test_overlay_deliverer.py` | `test_deliver_mkdir_failure` | ssh mkdir возвращает non-zero — raise DeliveryError | `overlay_deliverer.deliver_vhost_overlays()` |

$TEST_SPEC: 10 tests specified (1 module, 1 test file). Минимальное требование пользователя: ≥5 тестов. Реализовано с запасом (10 тестов) для достижения ≥80% coverage.

### Test Fixtures (tmp_path)

| Fixture | Содержимое | Используется в |
|---------|-----------|---------------|
| `tmp_path / "platform-root/node-configs/test-node/node.yaml"` | `node: {host: "1.2.3.4"}` | test_resolve_node_yaml_found, test_extract_node_host_with_host |
| `tmp_path / "platform-root/node-configs/test-node/node.yaml"` | `node: {}` (no host) | test_extract_node_host_empty |
| `tmp_path / "platform-root/node-configs/test-node/overlays/nginx/test.conf"` | `server { ... }` | test_deliver_with_overlays_mocked |
| Mock `subprocess.run` | `MagicMock(returncode=0)` | test_deliver_with_overlays_mocked |
| Mock `subprocess.run` | `MagicMock(returncode=1, side_effect=...)` | test_sync_core_rsync_failure, test_deliver_mkdir_failure |

---

## Risk Assessment

| Risk | Severity | Likelihood | Mitigation |
|------|:---:|:---:|-----------|
| **SSH proxy regression:** ошибка в `_resolve_and_extract()` → execute-функции не находят хост → `make node-update` падает | 🟡 MEDIUM | Low | Python `resolve_node_yaml()` тестируется unit-тестами (found + not-found). Shell helper использует `|| return 1` + LDD IMP:10 логирование. Dry-run позволяет проверить без реального SSH. |
| **rsync core/ regression:** ошибка в `sync_core_to_vps()` → stale code на VPS → production divergence | 🔴 HIGH | Low | Python функция тестируется (dry-run + failure). Shell facade проверяет exit code. T3 TRAP документирует rationale. |
| **printf %q несовместимость:** если бы command builders были перенесены в Python (отклонено — D3) | 🟢 LOW | Zero | D3 явно предписывает: command builders остаются в shell. Никакого риска. |
| **Overlay delivery regression:** ошибка в Python `deliver_vhost_overlays()` → vhost'ы не доставляются → nginx не видит новые домены | 🟡 MEDIUM | Low | Unit-тесты покрывают: no overlays (skip), dry-run, mocked rsync. Dry-run режим позволяет верифицировать перед реальным деплоем. |
| **Latency overhead:** Python CLI subprocess (~50ms на вызов) × 4 execute-функции = ~200ms добавленной latency на `node-update` | 🟢 LOW | Certain | 200ms на операцию, которая занимает 5-30 минут — пренебрежимо. |
| **Contract mismatch:** Python `resolve_node_yaml` даёт другой порядок поиска или другой результат, чем shell-версия | 🟡 MEDIUM | Low | Python-реализация копирует shell-логику 1:1 (3-path search, nullglob для glob). Unit-тест с tmp_path верифицирует контракт. |

### Severity Legend
- 🔴 HIGH: broken node-update = no core delivery → production outage
- 🟡 MEDIUM: broken overlay delivery = stale nginx vhosts → delayed domain activation
- 🟢 LOW: cosmetic, latency, или исключённый архитектурным решением

---

## Rollback Strategy

| Шаг | Метод | Время |
|:---:|-------|:---:|
| 1 | `git revert <merge-commit>` — откат коммита с TASK-036D | <2 min |
| 2 | `make node-update NODE=<test>` — верификация, что старый remote-cmd.sh работает | <5 min |
| 3 | Если revert не помог (изменения затронули другие файлы): `git checkout origin/main -- core/internal/bootstrap/remote-cmd.sh` + `rm core/internal/bootstrap/overlay_deliverer.py` + `rm tests/unit/test_overlay_deliverer.py` | <3 min |
| **Total recovery:** | | **<10 min** |

**Критическое замечание:** `remote-cmd.sh` — не VPS-side forced-command (в отличие от `deploy-project.sh`). Это клиентский скрипт, выполняемый на машине оператора/CI. Откат не требует деплоя на VPS — только локальный revert.

---

## TRAP Inventory

### TRAP, переносимые в shell facade (remote-cmd.sh)

```bash
# ⚠️ TRAP[BUG] · 2026-07-17 · P2 · ci_deploy_key from node.yaml not exported
# · Перенесено из remote-cmd.sh:95. Логика остаётся в build_ssh_cmd().
# · Fix: fallback to ci_deploy_key parameter when env var is unset.
# · Prevention: always use effective_ci_key combining env + parameter fallback.

# ⚠️ TRAP[BUG] · 2026-07-23 · P0 · VPS self-SSH loop
# · Перенесено из remote-cmd.sh:279. Проверка /opt/platform/ остаётся в execute_remote_update().
# · Detection: if /opt/platform/core/internal/bootstrap/node-lifecycle.sh exists → local exec (return 2).

# ⚠️ TRAP[BUG] · 2026-07-24 · P4 · DevPlan 065: bare ssh_exec may silently fail under set -e
# · Перенесено из remote-cmd.sh:360,483,650. Паттерн || { local rc=$?; ... } остаётся в execute-функциях.
```

### TRAP, переносимые в Python (overlay_deliverer.py)

```python
# ⚠️ TRAP[BUG] · 2026-07-24 · P0 · node-update не доставлял core/ на VPS
# · Symptom: stale state_machine.py/steps.py/converge.sh на VPS → баги из локальных
#   исправлений не доезжают до продакшена. Bootstrap доставляет core/ через scp_to_server,
#   но node-update — нет. Результат: node-update исполняет старый код.
# · Fix: rsync core/ + node.yaml перед remote exec (только код, без secrets/Makefile).
# · Ported: sync_core_to_vps() в overlay_deliverer.py (из execute_remote_update L294-337).
# · Prevention: всегда вызывать sync_core_to_vps() перед remote exec в node-update.
```

### Новые TRAP

```python
# 🧐 TRAP[DECISION] · 2026-07-26 · — · Wave 5d: remote-cmd.sh Strangler-Fig — printf %q stays in shell
# · Rejected: porting build_ssh_cmd/build_update_ssh_cmd/build_converge_ssh_cmd to Python
#   (risk: shlex.quote() ≠ printf '%q' for bash-specific edge cases)
# · Reason: SSH command quoting MUST be bash-compatible (remote side runs bash -c "...");
#   shlex.quote() uses POSIX single-quote wrapping, incompatible with $VAR expansion.
# · Rev: если Python получит bash-совместимый quoting (например, через subprocess with
#   proper shell=True escaping) — пересмотреть решение.

# 📝 TRAP[DEBT] · 2026-07-26 · LO · node-resolver.sh содержит inline python3 -c (extract_node_host)
# · Observed: extract_node_host() в node-resolver.sh:306-316 содержит inline python3 -c блок
#   с pyyaml — нарушение языковой политики (Tier 1 Strangler trigger).
# · Suspected: node-resolver.sh — shared lib, используется 8+ caller'ами, миграция
#   extract_node_host в Python требует обновления всех caller'ов.
# · Impact: inline python3 блок остаётся в shared lib, усложняет отладку.
# · When: during Wave 5d — deferred, out of scope. Требует отдельного DevPlan для
#   полной миграции node-resolver.sh → Python shared module.
```

---

## File Manifest

### Modified files
| Файл | До (LOC) | После (LOC) | Сокращение | Назначение |
|------|:---:|:---:|:---:|-----------|
| `core/internal/bootstrap/remote-cmd.sh` | 672 | ~230 | 66% | Shell facade: build-функции + execute-обёртки + Python delegation |

### New files
| Файл | LOC | Назначение |
|------|:---:|-----------|
| `core/internal/bootstrap/overlay_deliverer.py` | ~200 | Python module: resolve_node_yaml, extract_node_host, sync_core_to_vps, deliver_vhost_overlays + CLI |
| `tests/unit/test_overlay_deliverer.py` | ~150 | Unit tests: 10 тестов (resolve, extract, deliver, sync-core) |

### Unchanged files (reference contracts)
| Файл | Роль |
|------|------|
| `core/lib/node-resolver.sh` | Эталонная реализация resolve_node_yaml + extract_node_host (контракт для Python-порта) |
| `core/lib/ssh.sh` | SSH_OPTS_COMMON, ssh_exec (используется shell-фасадом) |
| `core/internal/shared/ssh_command_parser.py` | SSH_ORIGINAL_COMMAND parser (DevPlan 081) — не используется в данной миграции, без изменений |
| `core/internal/bootstrap/scp-deliver.sh` | prepare_ssh_opts (используется shell-фасадом) |

### Deleted
| Файл | Причина |
|------|---------|
| *Нет* | — |

---

## References

- **Master DevPlan:** `.ai/plans/036-wave5-strangler-shell-monoliths/01-DevPlan.md` (Wave 3: TASK-036D, D3 design decision)
- **Target script:** `core/internal/bootstrap/remote-cmd.sh` (672 LOC, 8 функций, 6 TRAP)
- **Reference implementation:** `core/lib/node-resolver.sh` (resolve_node_yaml + extract_node_host contracts)
- **Shared module:** `core/internal/shared/ssh_command_parser.py` (DevPlan 081 — SSH_ORIGINAL_COMMAND parser, не используется в данной миграции)
- **SSH library:** `core/lib/ssh.sh` (SSH_OPTS_COMMON, ssh_exec)
- **Paths library:** `core/lib/paths.sh` (PLATFORM_ROOT, PATHS_LIB_DIR)

---

## Next Steps

### Implementation (TASK-036D — single Coder session)
```
coder Read /Users/tronyx/projects/ai-platform/.ai/plans/036-wave5d-remote/01-DevPlan.md, implement TASK-036D: overlay_deliverer.py + remote-cmd.sh facade + test_overlay_deliverer.py
```

### Verification (after implementation)
```
python3 -m pytest tests/unit/test_overlay_deliverer.py -s -v && make test && make gate MODE=fast
```

$END_DEVPLAN
