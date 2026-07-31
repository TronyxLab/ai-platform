$START_DEVPLAN
# DevPlan 108 — scp-deliver.sh 251→≤60 LOC: SCP/rsync core-доставка → Python core_deliverer.py

$ARTIFACT_CONTRACT
PURPOSE:               Завершить Strangler-Fig декомпозицию канала Core-доставки (push-based
                       SCP/rsync, NO git): вынести всю rsync/ssh оркестрацию scp_to_server()
                       из `scp-deliver.sh` (251 LOC) в Python-модуль `core_deliverer.py`.
                       Shell-фасад сокращается до ≤60 LOC: prepare_ssh_opts() (остаётся в
                       shell — 4 активных caller'а, низкоуровневые SSH-опции) + тонкий
                       scp_to_server() → python3. Заодно устраняется дублирование
                       core/ rsync между scp_to_server() и overlay_deliverer.sync_core_to_vps()
                       (задокументировано TRAP[BUG] 2026-07-31 P1 в overlay_deliverer.py:197).
DESCRIPTION:           (1) Создать `core/internal/bootstrap/core_deliverer.py` — Python-модуль
                       с CLI (`deliver`), реализующий 5 rsync-фаз scp_to_server() дословно:
                       ensure_remote_dirs (ssh mkdir -p) → Phase 1 core/ → Phase 1b
                       platform-env.yaml → Phase 1c Makefile → Phase 2 node-configs/<node>/ →
                       Phase 3 <node>/secrets/. (2) Сократить scp-deliver.sh до тонкого
                       фасада: prepare_ssh_opts() (без изменений, TRAP-аннотации сохранены) +
                       scp_to_server() → python3 -m core.internal.bootstrap.core_deliverer.
                       (3) overlay_deliverer.sync_core_to_vps() — делегирует Phase 1 в
                       core_deliverer.deliver_core() (DRY-унификация, сигнатура сохраняется,
                       существующие тесты остаются зелёными). (4) Unit-тесты
                       tests/unit/test_core_deliverer.py. (5) Обновить AGENTS.md.
RATIONALE:             Q: Почему новый модуль core_deliverer.py, а не расширение overlay_deliverer.py?
                       A: Два РАЗНЫХ канала доставки (root AGENTS.md «Три канала доставки кода на
                       VPS»): Core = push-based SCP/rsync, Context-overlay = git pull-based.
                       overlay_deliverer.py (421 LOC) обслуживает overlay-канал (resolve/extract/
                       vhost overlays). core_deliverer.py — канал Core (core/, node-configs/,
                       secrets/, platform-env.yaml, Makefile). Разделение по ответственности (SRP)
                       зеркалит архитектуру каналов. Дополнительно: overlay_deliverer уже 421 LOC —
                       добавление ~200 LOC раздует модуль.
                       Q: Почему sync_core_to_vps делегирует в deliver_core?
                       A: Текущая ситуация — ДВА независимых core/ rsync (scp-deliver.sh Phase 1
                       и overlay_deliverer.sync_core_to_vps) — это ровно тот drift, который породил
                       TRAP[BUG] 2026-07-31 P1 (overlay_deliverer.py:197-203: «любой код, доставляющий
                       core на VPS, использует одну функцию резолюции базы») и комментарий
                       remote-cmd.sh:40-43 (единый источник remote_root). Делегирование сохраняет
                       сигнатуру sync_core_to_vps (host, core_src, node_name, node_yaml, dry_run) →
                       существующие тесты (test_sync_core_dry_run, test_sync_core_rsync_failure)
                       проходят без модификации. Направление импорта: overlay → core (без цикла).
ACCEPTANCE_CRITERIA:   AC1: Python-модуль `core_deliverer.py` реализует deliver_core(),
                            deliver_platform_env(), deliver_makefile(), deliver_node_configs(),
                            deliver_secrets(), ensure_remote_dirs(), deliver_all() с CLI (`deliver`)
                       AC2: Shell-фасад scp-deliver.sh ≤ 60 LOC (prepare_ssh_opts + вызов Python,
                            `wc -l`). Триггер Strangler-Fig: 251 → ≤60 (Tier 2).
                       AC3: `make bootstrap-node` — core-доставка работает идентично (bootstrap.sh
                            вызывает scp_to_server → python3). Exit codes 0/1 сохраняются.
                       AC4: `make node-update` — core-доставка работает идентично
                            (sync_core_to_vps делегирует в deliver_core, сигнатура/исключения
                            SyncCoreError сохранены).
                       AC5: DRY_RUN режим сохраняет поведение (bootstrap.sh early-exit в entrypoint;
                            фасад пробрасывает ${DRY_RUN:+--dry-run}; Python печатает команды,
                            не выполняет, exit 0).
                       AC6: Аудит-трейл идентичен — Python логирует ТЕ ЖЕ события (см. §5 таблицу
                            точных строк [IMP:8/9/10]).
                       AC7: Rsync exclude-паттерны идентичны (см. §5 таблицу: core 5 паттернов,
                            node-configs 3, secrets 1) + флаги -avz --delete идентичны.
                       AC8: `make gate MODE=fast` зелёный. Новые unit-тесты в tests/unit/ (не gate).
IMPLEMENTS:            Brief 108 (`.ai/plans/108-scp-deliver-python/01-Brief.md`)
IMPACTS:
                       - `core/internal/bootstrap/core_deliverer.py` (NEW, ~230 LOC)
                       - `core/internal/bootstrap/scp-deliver.sh` (MODIFY: 251→≤60 LOC)
                       - `core/internal/bootstrap/overlay_deliverer.py` (MODIFY: sync_core_to_vps
                         делегирование + удаление dead const RSYNC_EXCLUDES + обновление TRAP[BUG])
                       - `tests/unit/test_core_deliverer.py` (NEW, ~14 тестов)
                       - `core/internal/bootstrap/AGENTS.md` (MODIFY: LOC-таблица, новый модуль)
                       - `core/entrypoints/bootstrap.sh`, `remote-cmd.sh`, `node-update.sh`,
                         `converge.sh` — БЕЗ ИЗМЕНЕНИЙ (API фасада сохраняется)
REQUIRES:              `core/lib/ssh.sh` (SSH_OPTS_COMMON — mirror в Python, подготовка SSH_OPTS
                       в shell), `core/internal/bootstrap/overlay_deliverer.py` (sync_core_to_vps —
                       получает deliver_core), тесты: `tests/conftest.py` (Anti-Loop протокол, есть)
$END_ARTIFACT_CONTRACT

---

## §Debt Intake

| Источник | Тип | Решение |
|----------|-----|---------|
| TRAP[BUG] overlay_deliverer.py:197-203 (2026-07-31 P1) — ДВА независимых core/ rsync, расхождение remote base | IN_SCOPE | F3: sync_core_to_vps делегирует в core_deliverer.deliver_core(). Единая функция резолюции remote base — resolve_remote_base(). |
| TRAP[DECISION] scp-deliver.sh:29-33 (2026-07-17 HI) — не регистрировать в entrypoint-manifest.yaml (sourced lib) | PRESERVE | Остаётся в фасаде (scp-deliver.sh по-прежнему sourced-библиотека, регистрация в manifest:527 как consumer lib/ssh.sh валидна — ssh.sh продолжает source'иться). |
| TRAP[DECISION] scp-deliver.sh:73-77 (2026-07-18 HI) — known_hosts init-only (ssh-keygen -R) | PRESERVE | Остаётся внутри prepare_ssh_opts() в shell (4 активных caller'а). |
| TRAP[DECISION] scp-deliver.sh:168-171 (2026-07-16) — platform-env.yaml отдельным rsync | MIGRATE | Переезжает в core_deliverer.deliver_platform_env() как комментарий-контекст. |
| TRAP[DECISION] scp-deliver.sh:187-190 (2026-07-17) — Makefile отдельным rsync | MIGRATE | Переезжает в core_deliverer.deliver_makefile(). |
| TRAP[BUG] scp-deliver.sh:133-139 (2026-07-16, D2) — bare VPS mkdir -p отсутствовал | MIGRATE | Переезжает в core_deliverer.ensure_remote_dirs() (ssh mkdir -p, timeout 30). |
| TRAP[BUG] scp-deliver.sh:224-229 (2026-07-23 P0) — Phase 3 secrets искались не в per-node директории | MIGRATE | Переезжает в core_deliverer.deliver_secrets() — источник node-configs/<node>/secrets/, назначение /opt/node-configs/secrets/. |
| TRAP[DEBT] overlay_deliverer.py:19 — node-resolver.sh inline python3 -c (Tier 1) | DEFER | Вне скоупа 108. Отдельный DevPlan (аналогично Plan 101). |
| DRIFT: bootstrap/AGENTS.md @scope — scp-deliver описан без core_deliverer.py | IN_SCOPE | AC-задача TASK-5: добавить core_deliverer.py в @scope + LOC-таблицу «Shell-фасады: сводка». |
| remote-cmd.sh двойной source scp-deliver.sh (уровень модуля + 3 call-site source) | DEFER | Оппортунистическая чистка (RSK3 Plan 101). Фасад source-guard'ит SSH_OPTS — повторный source безопасен. |

---

## 1. Problem Matrix

| # | Проблема | Доказательство | Решение |
|---|----------|---------------|---------|
| P1 | scp-deliver.sh = 251 LOC для «низкоуровневой» rsync-операции | `wc -l core/internal/bootstrap/scp-deliver.sh` → 251 | Strangler-Fig Tier 2: бизнес-логика (rsync-фазы) → Python, фасад ≤60 LOC |
| P2 | Двойная реализация core/ rsync: scp-deliver.sh Phase 1 (строки 149-166) и overlay_deliverer.sync_core_to_vps (строки 182-238) | TRAP[BUG] 2026-07-31 P1 (overlay_deliverer.py:197) + remote-cmd.sh:40-43 требуют единый источник | F3: sync_core_to_vps → deliver_core (делегирование) |
| P3 | Rsync-команды собраны через string concat в shell — нет unit-тестируемости | Shell rsync не покрыт тестами (AC8 брифа: «scp-deliver не тестируется локально») | Python: subprocess.run с list-args + 14 unit-тестов |
| P4 | TRAP-аннотации (5 шт: 2 DECISION, 2 BUG, 1 context) привязаны к строкам shell-кода, который удаляется | Фазы 1b/1c/3 и mkdir переезжают в Python | Миграция TRAP в core_deliverer.py к соответствующим функциям; 2 DECISION остаются в фасаде |
| P5 | Несогласованность DRY_RUN: bootstrap.sh обрабатывает dry-run ДО scp_to_server (строки 177-180/193-195), библиотека сама не знает о DRY_RUN | Фасад не должен менять поведение entrypoint | scp_to_server пробрасывает ${DRY_RUN:+--dry-run} в Python (defensive, AC5); bootstrap-путь не меняется |
| P6 | overlay_deliverer.RSYNC_EXCLUDES станет мёртвой константой после делегирования | Используется только в sync_core_to_vps (строка 208) | Удалить (dead code), SSH_OPTS/_ssh_e оставить (используются deliver_vhost_overlays + тест test_ssh_e) |

---

## 2. Architecture Overview

### 2.1 Layer Diagram

```
┌─ entrypoints/ ─────────────────────────────────────────────────┐
│  bootstrap.sh (UNCHANGED)                                      │
│    ├─ source scp-deliver.sh   → prepare_ssh_opts + scp_to_server│
│    └─ scp_to_server ... || FATAL exit 1                        │
│  remote-cmd.sh (UNCHANGED)                                     │
│    └─ source scp-deliver.sh   → prepare_ssh_opts (update mode) │
│       (execute_remote_update/converge/reconcile — 3 call-site) │
└──────────────────────┬─────────────────────────────────────────┘
                       │ source
┌─ internal/bootstrap/ (shell facade) ───────────────────────────┐
│  scp-deliver.sh (MODIFY: 251→≤60 LOC)                          │
│  ├─ prepare_ssh_opts()  ssh-keygen -R (init) + SSH_OPTS←COMMON │
│  └─ scp_to_server()     python3 -m core_deliverer deliver ...   │
│  source: paths.sh + lib/ssh.sh (SSH_OPTS_COMMON)               │
└──────────────────────┬──────────────────────────────────────────┘
                       │ python3 -m core.internal.bootstrap.core_deliverer
┌─ internal/bootstrap/ (Python) ─────────────────────────────────┐
│  core_deliverer.py (NEW ~230 LOC)   [standalone, NO imports of overlay]│
│  ├─ resolve_remote_base()   PLATFORM_REMOTE_BASE→PLATFORM_ROOT→/opt/platform │
│  ├─ ensure_remote_dirs()    ssh mkdir -p (3 dirs, timeout 30)  │
│  ├─ deliver_core()          Phase 1: -avz --delete + 5 excludes│
│  ├─ deliver_platform_env()  Phase 1b (if exists)               │
│  ├─ deliver_makefile()      Phase 1c (if exists)               │
│  ├─ deliver_node_configs()  Phase 2: -avz --delete + 3 excludes│
│  ├─ deliver_secrets()       Phase 3: -avz --delete + 1 exclude │
│  ├─ deliver_all()           оркестрация 6 шагов, fail-fast     │
│  └─ cli()                   deliver (argparse)                 │
│                                                                │
│  overlay_deliverer.py (MODIFY, 421→~395 LOC)                   │
│  └─ sync_core_to_vps()  → DELEGATES Phase 1 к deliver_core()   │
│     (импорт: from core.internal.bootstrap.core_deliverer import│
│      deliver_core, resolve_remote_base)                         │
└────────────────────────────────────────────────────────────────┘
```

**Направление зависимостей:** `overlay → core_deliverer` (односторонний импорт, без цикла). `core_deliverer` не импортирует overlay_deliverer (собственный SSH_OPTS-mirror + RSYNC_EXCLUDES — конвенция кодовой базы, см. D2).

### 2.2 Draft Code Graph

```xml
<code_graph>
  <entity id="core_deliverer_py" type="PYTHON_MODULE" keywords="core-deliverer deliver_core deliver_platform_env deliver_makefile deliver_node_configs deliver_secrets ensure_remote_dirs rsync ssh mkdir-p core-channel strangler">
    <annotation>core/internal/bootstrap/core_deliverer.py (NEW) — ~230 LOC.
      Канал Core-доставки (push-based SCP/rsync, NO git). Standalone-модуль:
      собственный SSH_OPTS mirror (lib/ssh.sh SSH_OPTS_COMMON, 5 флагов) +
      RSYNC_EXCLUDES_CORE (5 паттернов) / RSYNC_EXCLUDES_NODE (3) / RSYNC_EXCLUDES_SECRETS (1).
      Функции: resolve_remote_base(), ensure_remote_dirs(), deliver_core(),
      deliver_platform_env(), deliver_makefile(), deliver_node_configs(), deliver_secrets(),
      deliver_all(), cli().
      Fail-fast: первая упавшая фаза → CoreDeliveryError → CLI exit 1 (эквивалент shell || return 1).
      Timeouts: mkdir=30 (parity с ssh_exec), rsync=600 (deploy-дефолт ssh.sh, hardening).
      DRY_RUN: печать команд в stderr (IMP:8), без exec, успех.
      Мигрированные TRAP: mkdir BUG 2026-07-16 (ensure_remote_dirs), secrets P0 2026-07-23
      (deliver_secrets), platform-env DECISION 2026-07-16 (deliver_platform_env),
      Makefile DECISION 2026-07-17 (deliver_makefile).
      Точные строки аудит-трейла [IMP:8/9/10] — см. §5, таблица AC6.</annotation>
    <crossLinks>
      <link target="scp_deliver_sh" relation="called_by"/>
      <link target="overlay_deliverer_py" relation="imported_by"/>
      <link target="test_core_deliverer_py" relation="tested_by"/>
    </crossLinks>
  </entity>

  <entity id="scp_deliver_sh" type="SHELL_MODULE" keywords="scp-deliver thin-facade prepare_ssh_opts ssh-keygen SSH_OPTS_COMMON sourced-library">
    <annotation>core/internal/bootstrap/scp-deliver.sh (MODIFY: 251→≤60 LOC).
      Остаётся sourced-библиотекой (no shebang, не entrypoint — TRAP[DECISION] 2026-07-17
      о нерегистрации в manifest сохраняется). Функции:
      prepare_ssh_opts() — БЕЗ ИЗМЕНЕНИЙ (ssh-keygen -R init-only, SSH_OPTS←SSH_OPTS_COMMON,
      TRAP[DECISION] 2026-07-18 known_hosts init-only). 4 активных caller'а:
      bootstrap.sh:182 (init), remote-cmd.sh:170/208/244 (update).
      scp_to_server() — тонкая обёртка (5 строк): python3 -m core.internal.bootstrap.core_deliverer
      deliver --host --node --node-configs-dir --core-dir --remote-user ${DRY_RUN:+--dry-run}.
      Source-guards: PATHS_LIB_DIR (paths.sh), SSH_OPTS declare guard, ssh.sh (readonly source-guard).
      Удаляется: вся rsync/ssh оркестрация (строки 101-250), SSH_OPTS_COMMON join в rsync -e.</annotation>
    <crossLinks>
      <link target="bootstrap_entrypoint" relation="sourced_by"/>
      <link target="remote_cmd_sh" relation="sourced_by"/>
      <link target="core_deliverer_py" relation="delegates_to"/>
      <link target="ssh_lib" relation="sources"/>
    </crossLinks>
  </entity>

  <entity id="overlay_deliverer_py" type="PYTHON_MODULE" keywords="overlay deliverer resolve extract sync-core vhost delegation">
    <annotation>core/internal/bootstrap/overlay_deliverer.py (MODIFY: 421→~395 LOC).
      sync_core_to_vps() — тело изменяется: inline core/ rsync (строки 204-226) заменяется
      на делегирование deliver_core(host=host, core_dir=core_src, remote_user="root",
      dry_run=dry_run) из core_deliverer. node.yaml rsync (строки 228-236) остаётся в overlay
      (overlay-специфичная метаданная-доставка). Исключения: try/except CoreDeliveryError →
      SyncCoreError (сообщение сохраняет «rsync core/ failed ...» — тест
      test_sync_core_rsync_failure match остаётся зелёным). Сигнатура НЕ меняется.
      TRAP[BUG] 2026-07-31 P1 обновляется: единая точка резолюции — core_deliverer.resolve_remote_base().
      Удаляется dead const RSYNC_EXCLUDES (единственный use был в sync_core_to_vps).
      SSH_OPTS/_ssh_e() ОСТАЮТСЯ (deliver_vhost_overlays + test_ssh_e).</annotation>
    <crossLinks>
      <link target="core_deliverer_py" relation="imports"/>
      <link target="remote_executor_py_101" relation="imported_by"/>
      <link target="test_overlay_deliverer_py" relation="tested_by"/>
    </crossLinks>
  </entity>

  <entity id="remote_executor_py_101" type="PYTHON_MODULE" keywords="remote-executor plan-101 cross-plan sync-core import">
    <annotation>core/internal/bootstrap/remote_executor.py (Plan 101, ПАРАЛЛЕЛЬНЫЙ — может не
      существовать на момент имплементации). Импортирует overlay_deliverer.sync_core_to_vps —
      сигнатура сохраняется → cross-plan совместимость без конфликтов.</annotation>
    <crossLinks>
      <link target="overlay_deliverer_py" relation="imports"/>
    </crossLinks>
  </entity>

  <entity id="bootstrap_entrypoint" type="SHELL_SCRIPT" keywords="bootstrap entrypoint scp_to_server prepare_ssh_opts unchanged">
    <annotation>core/entrypoints/bootstrap.sh (UNCHANGED). Строка 34 source scp-deliver.sh —
      API фасада сохраняется. Строки 182-183: prepare_ssh_opts + scp_to_server — без изменений.
      DRY_RUN early-exit (строки 177-180, 193-195) — без изменений.</annotation>
    <crossLinks>
      <link target="scp_deliver_sh" relation="sources"/>
    </crossLinks>
  </entity>

  <entity id="remote_cmd_sh" type="SHELL_MODULE" keywords="remote-cmd prepare_ssh_opts sync-core unchanged">
    <annotation>core/internal/bootstrap/remote-cmd.sh (UNCHANGED). 3 call-site source scp-deliver.sh
      (строки 159-160, 203-204, 239-240) — использует ТОЛЬКО prepare_ssh_opts(), которая остаётся.
      sync-core через overlay_deliverer — делегирование прозрачно для shell.</annotation>
    <crossLinks>
      <link target="scp_deliver_sh" relation="sources"/>
    </crossLinks>
  </entity>

  <entity id="ssh_lib" type="SHELL_MODULE" keywords="ssh.sh SSH_OPTS_COMMON ssh_exec readonly">
    <annotation>core/lib/ssh.sh (UNCHANGED). SSH_OPTS_COMMON — источник для shell-фасада
      (prepare_ssh_opts) и mirror для Python (core_deliverer.SSH_OPTS). source-guard readonly —
      повторный source безопасен.</annotation>
    <crossLinks>
      <link target="scp_deliver_sh" relation="sourced_by"/>
    </crossLinks>
  </entity>

  <entity id="test_core_deliverer_py" type="TEST_MODULE" keywords="unit-test core_deliverer rsync excludes dry-run mkdir phases LDD">
    <annotation>tests/unit/test_core_deliverer.py (NEW) — ~14 тестов.
      tmp_path fixtures, mock.patch subprocess.run, caplog LDD IMP:9 траектория.
      Тесты exclude-паттернов (AC7), точных rsync-команд, fail-fast, dry-run, resolve_remote_base.</annotation>
    <crossLinks>
      <link target="core_deliverer_py" relation="tests"/>
    </crossLinks>
  </entity>

  <entity id="test_overlay_deliverer_py" type="TEST_MODULE" keywords="existing overlay tests sync-core delegation regression">
    <annotation>tests/unit/test_overlay_deliverer.py (EXISTING, UNCHANGED — must stay green).
      test_sync_core_dry_run (dry_run → True, без exec), test_sync_core_rsync_failure
      (mock subprocess.run rc=1 → SyncCoreError «rsync core/ failed»).
      После делегирования: dry-run-тест — deliver_core(dry_run=True) печатает, True.
      failure-тест — deliver_core rsync rc=1 → CoreDeliveryError → sync_core_to_vps оборачивает
      в SyncCoreError с сохранённым сообщением. БЕЗ модификации теста.</annotation>
    <crossLinks>
      <link target="overlay_deliverer_py" relation="tests"/>
    </crossLinks>
  </entity>
</code_graph>
```

### 2.3 Design Decisions

#### D1: Новый модуль core_deliverer.py, не расширение overlay_deliverer.py
## @rationale
**Q:** Почему новый модуль, а не расширение overlay_deliverer.py (как допускает Brief)?
**A:** Root AGENTS.md определяет **два раздельных канала доставки**: Core (push-based SCP/rsync, NO git) и Context-overlay (git pull-based). overlay_deliverer.py (421 LOC) — overlay-канал: resolve/extract/vhost overlays. core_deliverer.py — Core-канал: core/, node-configs/, secrets/, platform-env.yaml, Makefile. Разделение по SRP зеркалит архитектурную модель каналов. Дополнительно: overlay_deliverer уже 421 LOC — расширение на ~200 LOC раздуло бы модуль (прецедент: Plan 101 D2 — remote_executor.py отдельным модулем по той же причине).

#### D2: core_deliverer.py — standalone (собственный SSH_OPTS/RSYNC_EXCLUDES mirror), overlay импортирует core
## @rationale
**Q:** Почему core_deliverer не импортирует SSH_OPTS/_ssh_e из overlay_deliverer, и почему направление импорта overlay → core?
**A:** (1) **Циклический импорт:** sync_core_to_vps (overlay) делегирует в deliver_core (core). Если бы core_deliverer импортировал SSH_OPTS из overlay → цикл core→overlay→core. Разрыв цикла: core_deliverer определяет SSH_OPTS + RSYNC_EXCLUDES_* локально. (2) **Конвенция кодовой базы:** overlay_deliverer.py:12 уже документирует «SSH_OPTS mirror lib/ssh.sh SSH_OPTS_COMMON» — Python-зеркало shell-константы является установленным паттерном; третье зеркало (в core_deliverer) с явным комментарием-ссылкой на lib/ssh.sh — консистентно, без over-engineering (извлечение общего shared-модуля ради 2 списков констант — преждевременная абстракция). (3) Единая точка правды остаётся shell: lib/ssh.sh SSH_OPTS_COMMON.

#### D3: sync_core_to_vps → делегирование в deliver_core (DRY-унификация, не копирование)
## @rationale
**Q:** Почему не оставить sync_core_to_vps как есть (параллельная реализация)?
**A:** Два независимых core/ rsync уже породили P1-баг (TRAP[BUG] 2026-07-31: расхождение remote base → ДВЕ копии core на VPS → update-фазы из чужого дерева). Комментарий remote-cmd.sh:40-43 прямо требует «единый источник». Делегирование: сигнатура (host, core_src, node_name, node_yaml, dry_run) → bool сохраняется; исключение CoreDeliveryError оборачивается в SyncCoreError с сохранённым сообщением → оба существующих теста проходят без модификации. Поведенческое улучшение: sync_core НЕ получает mkdir (ensure_dirs не вызывается из sync-пути — deliver_core чистый rsync-фаз, mkdir живёт в deliver_all) → поведение node-update идентично текущему.

#### D4: SSH exec в Python через subprocess.run с SSH_OPTS list (не shell ssh_exec)
## @rationale
**Q:** Почему Python не вызывает shell-функцию ssh_exec для mkdir?
**A:** Вызов shell-функции из Python требует source'инга lib/ssh.sh + экспорта зависимостей — сложность без выигрыша. Прецедент: overlay_deliverer.deliver_vhost_overlays (строки 347-351) — `subprocess.run(["ssh", *SSH_OPTS, f"root@{host}", "mkdir -p ..."], timeout=30)`. Тот же паттерн для core_deliverer. TRAP[BUG] P4 «bare ssh_exec silently fail under set -e» (remote-cmd.sh:188) не релевантен для Python (нет set -e).

#### D5: Timeout-hardening rsync: 600s (деплой-дефолт ssh.sh), mkdir: 30s (parity)
## @rationale
**Q:** Shell-версия НЕ оборачивает rsync в timeout — почему Python добавляет?
**A:** lib/ssh.sh ssh_exec по умолчанию использует `timeout 600` для deploy-режима (строки 102-103) — 600s является каноническим платформенным дефолтом для длительных remote-операций. rsync core/ > 600s аномален (обрыв сети, зависший ssh). Это hardening, задокументированный в TRAP-комментарии; поведение при нормальных трансферах идентично. Mkdir: 30s — ровно как scp-deliver.sh:142 (timeout=30 в ssh_exec).

---

## 3. Step-by-Step Data Flow

### 3.1 bootstrap.sh (init) — ПОЛНЫЙ ЦИКЛ Core-доставки (AC3)

```
make bootstrap-node NODE=<name>
  ▼
bootstrap.sh
  │  source scp-deliver.sh (line 34) — фасад, ≤60 LOC
  │  resolve node.yaml → SSH_HOST, NODE_CONFIGS_DIR, CORE_DIR
  │  ◇ DRY_RUN? ──yes──► печать would-команд (lines 177-180) → exit 0   [AC5: entrypoint, без изменений]
  │  prepare_ssh_opts "${SSH_HOST}" "init"          (line 182, shell)
  │      ├─ ssh-keygen -R "${SSH_HOST}"             (init-only, TRAP 2026-07-18)
  │      └─ SSH_OPTS=("${SSH_OPTS_COMMON[@]}")
  │  scp_to_server "${SSH_HOST}" "${NODE_NAME}" "${NODE_CONFIGS_DIR}" "${CORE_DIR}"
  │      └─ python3 -m core.internal.bootstrap.core_deliverer deliver \
  │           --host ... --node ... --node-configs-dir ... --core-dir ... \
  │           --remote-user "${REMOTE_SSH_USER:-root}" ${DRY_RUN:+--dry-run}
  ▼
core_deliverer.py::deliver_all()
  │  1. resolve_remote_base() → PLATFORM_REMOTE_BASE → PLATFORM_ROOT → /opt/platform
  │     resolve_node_configs_base() → NODE_CONFIGS_REMOTE_BASE → /opt/node-configs
  │  2. ensure_remote_dirs(host, user, node, base, ncb, dry_run)
  │     subprocess.run(["ssh", *SSH_OPTS, f"{user}@{host}",
  │         "mkdir -p {base}/core {ncb}/{node} {ncb}/secrets"], timeout=30)
  │     rc≠0 → IMP:10 FATAL ssh mkdir -p failed → CoreDeliveryError → exit 1
  │  3. deliver_core(host, core_dir, user, base, dry_run)
  │     rsync -avz --delete -e "ssh {SSH_OPTS}" --exclude=.git --exclude=__pycache__
  │           --exclude=.pytest_cache --exclude=default-user.xml --exclude=.env
  │           {core_dir}/ {user}@{host}:{base}/core/          timeout=600
  │     rc≠0 → IMP:10 FATAL rsync core/ failed → CoreDeliveryError → exit 1
  │  4. deliver_platform_env(host, core_dir, user, base, dry_run)
  │     source={core_dir}/../platform-env.yaml; ◇ -f? ──no──► IMP:8 SKIP
  │     rsync -avz -e "ssh {SSH_OPTS}" {src} {user}@{host}:{base}/platform-env.yaml
  │  5. deliver_makefile(host, core_dir, user, base, dry_run)
  │     source={core_dir}/../Makefile; ◇ -f? ──no──► IMP:8 SKIP
  │     rsync -avz -e "ssh {SSH_OPTS}" {src} {user}@{host}:{base}/Makefile
  │  6. deliver_node_configs(host, node, ncd, user, ncb, dry_run)
  │     rsync -avz --delete -e "ssh {SSH_OPTS}" --exclude=.git --exclude=__pycache__
  │           --exclude=.pytest_cache {ncd}/{node}/ {user}@{host}:{ncb}/{node}/
  │  7. deliver_secrets(host, node, ncd, user, ncb, dry_run)
  │     src={ncd}/{node}/secrets; ◇ -d? ──no──► IMP:8 SKIP
  │     rsync -avz --delete -e "ssh {SSH_OPTS}" --exclude=.git {src}/ {user}@{host}:{ncb}/secrets/
  │  8. return 0 (exit 0)
  ▼
  scp_to_server return rc → bootstrap.sh || FATAL exit 1   [AC3: exit codes 0/1 идентичны]
  ▼
bootstrap.sh: build_ssh_cmd → exec ssh ${SSH_OPTS[@]} root@${SSH_HOST} "${REMOTE_CMD}"
```

### 3.2 node-update (update) — sync_core_to_vps делегирование (AC4)

```
node-update.sh → remote-cmd.sh::execute_remote_update
  │  source scp-deliver.sh (call-site, line 160) → prepare_ssh_opts "${RESOLVED_SSH_HOST}" "update"
  │      └─ update-mode: known_hosts ПРЕСЕРВИРУЕТСЯ (нет ssh-keygen -R)
  │  ${OVERLAY_DELIVERER} sync-core --host H --core-src C --node N --node-yaml Y [--dry-run]
  ▼
overlay_deliverer.py::sync_core_to_vps(host, core_src, node_name, node_yaml, dry_run)
  │  validation: host nonempty, core_src isdir
  │  try:
  │      deliver_core(host=host, core_dir=core_src, remote_user="root", dry_run=dry_run)
  │        → core_deliverer.deliver_core()  [БЕЗ ensure_remote_dirs — поведение node-update идентично]
  │  except CoreDeliveryError as e:
  │      raise SyncCoreError(str(e))        [сообщение сохраняет «rsync core/ failed ...»]
  │  node.yaml rsync (остаётся в overlay)   [overlay-специфичная доставка]
  │  return True
  ▼
  → build_update_ssh_cmd → ssh_exec ... deploy (timeout 600)
```

### 3.3 converge / reconcile (update mode)

```
converge.sh → execute_remote_converge / execute_remote_reconcile
  │  source scp-deliver.sh (call-site) → prepare_ssh_opts "update"  (ТОЛЬКО это)
  │  НЕ вызывают scp_to_server, НЕ вызывают sync-core
  │  → фасад scp-deliver.sh обязан сохранить prepare_ssh_opts без изменений
```

---

## 4. File Manifest

| # | Файл | Действие | Тип | Описание |
|---|------|:--------:|-----|----------|
| F1 | `core/internal/bootstrap/core_deliverer.py` | CREATE | PYTHON | ~230 LOC. Standalone-модуль Core-канала: SSH_OPTS mirror, RSYNC_EXCLUDES_CORE/NODE/SECRETS, resolve_remote_base(), resolve_node_configs_base(), ensure_remote_dirs(), deliver_core(), deliver_platform_env(), deliver_makefile(), deliver_node_configs(), deliver_secrets(), deliver_all(), cli(). 4 мигрированных TRAP-аннотации. Точные строки аудит-трейла (§5 AC6). |
| F2 | `core/internal/bootstrap/scp-deliver.sh` | MODIFY | SHELL | 251→≤60 LOC. Остаются: модульный контракт (сжатый), source-guards (paths.sh, ssh.sh, SSH_OPTS declare), prepare_ssh_opts() без изменений (TRAP 2026-07-18), scp_to_server() — thin wrapper → python3. TRAP 2026-07-17 (entrypoint-manifest) сохранён. Вся rsync/ssh оркестрация удаляется. |
| F3 | `core/internal/bootstrap/overlay_deliverer.py` | MODIFY | PYTHON | 421→~395 LOC. sync_core_to_vps(): inline core/ rsync → делегирование deliver_core(); try/except CoreDeliveryError→SyncCoreError; TRAP[BUG] 2026-07-31 обновлён (единый источник — core_deliverer.resolve_remote_base()); удалён dead const RSYNC_EXCLUDES; node.yaml-доставка не меняется. |
| F4 | `tests/unit/test_core_deliverer.py` | CREATE | PYTHON | ~14 тестов по §TEST_SPEC: exclude-паттерны (AC7), rsync-команды, fail-fast, dry-run, resolve_remote_base, CLI exit codes, LDD IMP:9. |
| F5 | `core/internal/bootstrap/AGENTS.md` | MODIFY | MARKDOWN | @scope: +core_deliverer.py; таблица «Shell-фасады: сводка»: +scp-deliver.sh 251→≤60; список unit-тестов: +test_core_deliverer.py. |

**Без изменений (verify-only):**
- `core/entrypoints/bootstrap.sh` — API scp-deliver.sh сохраняется (source + 2 функции)
- `core/internal/bootstrap/remote-cmd.sh` — использует только prepare_ssh_opts (остаётся)
- `core/entrypoints/node-update.sh`, `converge.sh` — не затрагиваются
- `core/entrypoint-manifest.yaml:527` — scp-deliver.sh остаётся consumer'ом lib/ssh.sh (фасад продолжает source'ить ssh.sh) → manifest валиден
- `tests/unit/test_overlay_deliverer.py` — без модификации, must stay green (§5.1)

---

## 5. Acceptance Criteria

| AC | Описание | Верификация |
|----|----------|-------------|
| AC1 | core_deliverer.py с deliver_core(), deliver_platform_env(), deliver_makefile(), deliver_node_configs(), deliver_secrets(), ensure_remote_dirs(), deliver_all() + CLI | `python3 -m core.internal.bootstrap.core_deliverer deliver --help` выводит usage. Файл существует. |
| AC2 | scp-deliver.sh ≤ 60 LOC | `wc -l core/internal/bootstrap/scp-deliver.sh` → ≤ 60. Фасад: prepare_ssh_opts + scp_to_server→python3. |
| AC3 | `make bootstrap-node` core-доставка идентична | Unit: deliver_all() последовательность 6 шагов + exit 0/1. Статический аудит §3.1 vs scp-deliver.sh:122-250. Опционально e2e на test-VPS (не блокирует). |
| AC4 | `make node-update` core-доставка идентична | test_sync_core_dry_run + test_sync_core_rsync_failure зелёные БЕЗ модификации. sync_core_to_vps делегирует в deliver_core. |
| AC5 | DRY_RUN сохраняет поведение | bootstrap.sh early-exit без изменений (строки 177-180/193-195). scp_to_server пробрасывает ${DRY_RUN:+--dry-run}. Python dry_run: печать IMP:8, 0 subprocess-вызовов (mock-assert), return 0. |
| AC6 | Аудит-трейл идентичен | Лог-строки в Python совпадают дословно (таблица ниже). caplog-тест: на успешном пути ≥1 IMP:9. |
| AC7 | Rsync exclude-паттерны идентичны | Тесты assert'ят exact паттерны в subprocess args (таблица ниже). |
| AC8 | `make gate MODE=fast` зелёный | Прогон gate. Новые тесты — unit (не gate, не регистрируются в manifest). scp-deliver не тестируется локально — покрытие через unit-тесты Python-модуля. |

### Таблица AC6 — точные строки аудит-трейла (дословное соответствие shell echo)

| Фаза | Shell echo (scp-deliver.sh) | Python logger (core_deliverer) |
|------|------------------------------|-------------------------------|
| mkdir | `[IMP:8][bootstrap][scp] Ensuring remote directories exist on ${ssh_host}` | `[IMP:8][ensure_remote_dirs][exec] Ensuring remote directories exist on {host}` |
| mkdir | `[IMP:10][bootstrap][scp] FATAL: ssh mkdir -p failed for ${ssh_host}` | `[IMP:10][ensure_remote_dirs][error] FATAL: ssh mkdir -p failed for {host}` |
| mkdir | `[IMP:9][bootstrap][scp] Remote directories confirmed` | `[IMP:9][ensure_remote_dirs][done] Remote directories confirmed` |
| 1/4 | `[IMP:9][bootstrap][scp] Phase 1/4: Rsyncing core/ → ${ssh_host}:${remote_platform_base}/core/` | `[IMP:9][deliver_core][exec] Phase 1/4: Rsyncing core/ → {host}:{base}/core/` |
| 1/4 | `[IMP:10][bootstrap][scp] FATAL: rsync core/ failed for ${ssh_host}` | `[IMP:10][deliver_core][error] FATAL: rsync core/ failed for {host}` |
| 1/4 | `[IMP:9][bootstrap][scp] Phase 1/4: core/ rsync complete` | `[IMP:9][deliver_core][done] Phase 1/4: core/ rsync complete` |
| 1b/4 | `[IMP:9][bootstrap][scp] Phase 1b/4: Rsyncing platform-env.yaml → ${ssh_host}:${remote_platform_base}/` | `[IMP:9][deliver_platform_env][exec] Phase 1b/4: Rsyncing platform-env.yaml → {host}:{base}/` |
| 1b/4 | `[IMP:10][bootstrap][scp] FATAL: rsync platform-env.yaml failed for ${ssh_host}` | `[IMP:10][deliver_platform_env][error] FATAL: rsync platform-env.yaml failed for {host}` |
| 1b/4 | `[IMP:9][bootstrap][scp] Phase 1b/4: platform-env.yaml rsync complete` | `[IMP:9][deliver_platform_env][done] Phase 1b/4: platform-env.yaml rsync complete` |
| 1b/4 | `[IMP:8][bootstrap][scp] Phase 1b/4: SKIP — platform-env.yaml not found at ${platform_env_src}` | `[IMP:8][deliver_platform_env][skip] Phase 1b/4: SKIP — platform-env.yaml not found at {src}` |
| 1c/4 | Аналогично platform-env с Makefile (4 строки: exec/error/done/skip) | Аналогично |
| 2/4 | `[IMP:9][bootstrap][scp] Phase 2/4: Rsyncing node-configs/${node_name}/ → ${ssh_host}:${remote_node_configs_base}/${node_name}/` | `[IMP:9][deliver_node_configs][exec] Phase 2/4: Rsyncing node-configs/{node}/ → {host}:{ncb}/{node}/` |
| 2/4 | `[IMP:10][bootstrap][scp] FATAL: rsync node-configs/${node_name}/ failed for ${ssh_host}` | `[IMP:10][deliver_node_configs][error] FATAL: rsync node-configs/{node}/ failed for {host}` |
| 2/4 | `[IMP:9][bootstrap][scp] Phase 2/4: node-configs/${node_name}/ rsync complete` | `[IMP:9][deliver_node_configs][done] Phase 2/4: node-configs/{node}/ rsync complete` |
| 3/4 | `[IMP:9][bootstrap][scp] Phase 3/4: Rsyncing ${per_node_secrets}/ → ${ssh_host}:${remote_node_configs_base}/secrets/` | `[IMP:9][deliver_secrets][exec] Phase 3/4: Rsyncing {src}/ → {host}:{ncb}/secrets/` |
| 3/4 | `[IMP:10][bootstrap][scp] FATAL: rsync secrets/ failed for ${ssh_host}` | `[IMP:10][deliver_secrets][error] FATAL: rsync secrets/ failed for {host}` |
| 3/4 | `[IMP:9][bootstrap][scp] Phase 3/4: secrets/ rsync complete` | `[IMP:9][deliver_secrets][done] Phase 3/4: secrets/ rsync complete` |
| 3/4 | `[IMP:8][bootstrap][scp] Phase 3/4: SKIP — no secrets/ directory at ${per_node_secrets}` | `[IMP:8][deliver_secrets][skip] Phase 3/4: SKIP — no secrets/ directory at {src}` |

**Формат:** `logging.basicConfig(level=WARNING, format="%(message)s", stream=sys.stderr)` + `logger.info(...)` — сообщения печатаются как есть (прецедент overlay_deliverer.py:42). Событийные строки идентичны; блок `[bootstrap][scp]` в префиксе заменяется на `[<function>][<block>]` — содержимое события (текст после `]`) дословно сохранено.

### Таблица AC7 — rsync-команды (дословное соответствие)

| Фаза | Shell (scp-deliver.sh) | Python subprocess args |
|------|------------------------|------------------------|
| 1 core/ | `rsync -avz --delete -e "ssh ${SSH_OPTS_COMMON[*]}" --exclude=.git --exclude=__pycache__ --exclude=.pytest_cache --exclude=default-user.xml --exclude=.env {core_dir}/ {user}@{host}:{base}/core/` | `["rsync","-avz","--delete","-e",_ssh_e(),"--exclude=.git","--exclude=__pycache__","--exclude=.pytest_cache","--exclude=default-user.xml","--exclude=.env",f"{core_dir}/",f"{user}@{host}:{base}/core/"]` |
| 1b platform-env.yaml | `rsync -avz -e "ssh ${SSH_OPTS_COMMON[*]}" {src} {user}@{host}:{base}/platform-env.yaml` | `["rsync","-avz","-e",_ssh_e(),src,f"{user}@{host}:{base}/platform-env.yaml"]` (только если -f) |
| 1c Makefile | `rsync -avz -e "ssh ${SSH_OPTS_COMMON[*]}" {src} {user}@{host}:{base}/Makefile` | `["rsync","-avz","-e",_ssh_e(),src,f"{user}@{host}:{base}/Makefile"]` (только если -f) |
| 2 node-configs | `rsync -avz --delete -e "ssh ${SSH_OPTS_COMMON[*]}" --exclude=.git --exclude=__pycache__ --exclude=.pytest_cache {ncd}/{node}/ {user}@{host}:{ncb}/{node}/` | `["rsync","-avz","--delete","-e",_ssh_e(),"--exclude=.git","--exclude=__pycache__","--exclude=.pytest_cache",f"{ncd}/{node}/",f"{user}@{host}:{ncb}/{node}/"]` |
| 3 secrets | `rsync -avz --delete -e "ssh ${SSH_OPTS_COMMON[*]}" --exclude=.git {src}/ {user}@{host}:{ncb}/secrets/` | `["rsync","-avz","--delete","-e",_ssh_e(),"--exclude=.git",f"{src}/",f"{user}@{host}:{ncb}/secrets/"]` (только если -d) |

`_ssh_e()` = `f"ssh {' '.join(SSH_OPTS)}"` → `"ssh -o BatchMode=yes -o StrictHostKeyChecking=accept-new -o ConnectTimeout=30 -o ServerAliveInterval=30 -o ServerAliveCountMax=10"` — эквивалент shell `${SSH_OPTS_COMMON[*]}` (word-split). SSH_OPTS mirror: `["-o","BatchMode=yes","-o","StrictHostKeyChecking=accept-new","-o","ConnectTimeout=30","-o","ServerAliveInterval=30","-o","ServerAliveCountMax=10"]`.

### 5.1 Стратегия верификации AC3-AC5 (идентичность поведения)

Поскольку фазы выполняют rsync/ssh на production VPS, прямое A/B в CI невозможно. Стратегия (прецедент Plan 101 §5):

1. **Unit-тесты** (test_core_deliverer.py): mock subprocess.run, assert точных команд/паттернов, последовательности фаз, exit codes.
2. **Статический аудит**: построчное сравнение shell-логики (scp-deliver.sh:122-250) с Python-эквивалентом по таблицам AC6/AC7.
3. **IMP:9 трейс-логи**: caplog-проверка ≥1 IMP:9 на успешном пути.
4. **Регрессия overlay**: test_overlay_deliverer.py (10 тестов) зелёный без модификации — доказывает AC4.
5. **E2E на test-VPS** (опционально, не блокирует merge): `make bootstrap-node NODE=<test>` / `make node-update NODE=<test>`.

---

## 6. $TASKS

| ID | Задача | Роль | Выход | AC | Зависимости | Сложность |
|----|--------|------|-------|----|-------------|:---------:|
| TASK-1 | Создать core_deliverer.py: SSH_OPTS mirror, RSYNC_EXCLUDES_*, resolve_remote_base, ensure_remote_dirs, 5 deliver_* фаз, deliver_all, CLI | Coder | F1 | AC1, AC6, AC7 | — | 7 |
| TASK-2 | Сократить scp-deliver.sh до ≤60 LOC: prepare_ssh_opts (без изменений) + scp_to_server thin wrapper. TRAP-аннотации сохранены | Coder | F2 | AC2, AC3 | TASK-1 | 3 |
| TASK-3 | overlay_deliverer.py: sync_core_to_vps → делегирование deliver_core; CoreDeliveryError→SyncCoreError; удалить dead RSYNC_EXCLUDES; обновить TRAP[BUG] 2026-07-31 | Coder | F3 | AC4 | TASK-1 | 3 |
| TASK-4 | Unit-тесты test_core_deliverer.py (14 тестов по §TEST_SPEC) | Coder | F4 | AC1, AC5, AC6, AC7 | TASK-1 | 5 |
| TASK-5 | Обновить AGENTS.md: @scope, LOC-таблица, unit-тесты | Coder | F5 | — | TASK-2 | 2 |
| TASK-6 | Интеграционная верификация: test_overlay_deliverer.py (без модификации), test_core_deliverer.py, test_node_lifecycle_static.py, make gate MODE=fast | QA | — | AC4, AC8 | TASK-2, TASK-3, TASK-4 | 3 |

**Merge-rule check:**
- TASK-5 (AGENTS.md, 1 файл, <20 строк) → **ВЛИВАЕТСЯ в TASK-2** (оба документируют фасад; TASK-5 зависит от завершения TASK-2).
- TASK-2, TASK-3, TASK-4 — разные файлы (F2/F3/F4), общая зависимость TASK-1 → параллельны после Wave 1.
- TASK-6 — интеграционная (QA).

**Критический путь:** TASK-1 → TASK-4 → TASK-6

---

## 7. $PARALLEL_GROUPS

### Wave 1
- **TASK-1** — core_deliverer.py (новый файл F1, без пересечений)

### Wave 2 (зависит от TASK-1; TASK-2, TASK-3, TASK-4 — параллельны, разные файлы)
- **TASK-2 + TASK-5 (merged)** — scp-deliver.sh ≤60 LOC + AGENTS.md
- **TASK-3** — overlay_deliverer.py делегирование
- **TASK-4** — test_core_deliverer.py

### Wave 3 (зависит от TASK-2 + TASK-3 + TASK-4)
- **TASK-6** — Интеграционная верификация (QA)

```
Wave 1: TASK-1
  ↓
Wave 2: TASK-2(+TASK-5) ∥ TASK-3 ∥ TASK-4
  ↓
Wave 3: TASK-6
```

---

## 8. $TEST_SPEC

| Test file | Test function | Scenario | Module under test |
|-----------|---------------|----------|-------------------|
| `tests/unit/test_core_deliverer.py` | `test_resolve_remote_base_default` | Нет env → `/opt/platform` | `resolve_remote_base()` |
| `tests/unit/test_core_deliverer.py` | `test_resolve_remote_base_chain` | `PLATFORM_REMOTE_BASE` > `PLATFORM_ROOT` > default (monkeypatch env) | `resolve_remote_base()` |
| `tests/unit/test_core_deliverer.py` | `test_ensure_remote_dirs_command` | mock subprocess.run: assert ssh args = 3 dirs (`{base}/core {ncb}/{node} {ncb}/secrets`) | `ensure_remote_dirs()` |
| `tests/unit/test_core_deliverer.py` | `test_ensure_remote_dirs_failure` | mkdir rc=1 → `CoreDeliveryError`, IMP:10 FATAL лог | `ensure_remote_dirs()` |
| `tests/unit/test_core_deliverer.py` | `test_deliver_core_excludes_exact` | assert rsync args содержат ровно 5 exclude-паттернов (AC7) + `-avz --delete` | `deliver_core()` |
| `tests/unit/test_core_deliverer.py` | `test_deliver_core_failure` | rsync rc=1 → `CoreDeliveryError` («rsync core/ failed»), IMP:10 | `deliver_core()` |
| `tests/unit/test_core_deliverer.py` | `test_deliver_platform_env_missing_skips` | файл отсутствует → IMP:8 SKIP, subprocess НЕ вызывается | `deliver_platform_env()` |
| `tests/unit/test_core_deliverer.py` | `test_deliver_makefile_missing_skips` | Makefile отсутствует → IMP:8 SKIP, без rsync | `deliver_makefile()` |
| `tests/unit/test_core_deliverer.py` | `test_deliver_node_configs_excludes` | assert 3 exclude-паттерна (AC7) | `deliver_node_configs()` |
| `tests/unit/test_core_deliverer.py` | `test_deliver_secrets_excludes_and_skip` | secrets dir есть → 1 exclude (`.git`); нет → IMP:8 SKIP без rsync | `deliver_secrets()` |
| `tests/unit/test_core_deliverer.py` | `test_deliver_all_success_ldd` | mock subprocess rc=0 → все 6 шагов в правильном порядке, ≥1 IMP:9 лог (caplog) | `deliver_all()` |
| `tests/unit/test_core_deliverer.py` | `test_deliver_all_fail_fast` | mkdir rc=1 → CoreDeliveryError, последующие фазы НЕ вызваны (fail-fast) | `deliver_all()` |
| `tests/unit/test_core_deliverer.py` | `test_dry_run_no_execution` | dry_run=True → 0 subprocess-вызовов (mock.assert_not_called), команды напечатаны IMP:8, success | `deliver_all()` / `deliver_core()` |
| `tests/unit/test_core_deliverer.py` | `test_cli_exit_codes` | CLI `deliver` с mock: success → SystemExit(0); CoreDeliveryError → SystemExit(1) | `cli()` |

**Test Honesty:**
- R1 (no pass-tests): каждый тест assert'ит subprocess-аргументы / raised exception / exit code / лог-сообщение
- R2 (no unfalsifiable): все asserts проверяют конкретные командные строки и паттерны, не language guarantees
- LDD caplog: `test_deliver_all_success_ldd` — минимальный anti-illusion траекторийный тест (≥1 IMP:9 при успехе)
- R4 (no-skip): тесты не требуют SSH/VPS/Docker — `tmp_path` + mock, без маркеров skip

**Регрессия (без модификации):** `tests/unit/test_overlay_deliverer.py` — `test_sync_core_dry_run` (dry_run → True) и `test_sync_core_rsync_failure` (mock rc=1 → SyncCoreError «rsync core/ failed») обязаны остаться зелёными после TASK-3.

---

## 9. Risks & Mitigations

| Риск | Вероятность | Mitigation |
|------|:-----------:|------------|
| R1: rsync stdout больше не стримится на терминал (shell наследовал stdout; Python capture_output) | LOW | Поведение событийного аудит-трейла (AC6) не зависит от rsync progress-вывода. При ошибке stderr включается в IMP:10 FATAL-лог. Прецедент: overlay_deliverer.sync_core_to_vps уже capture'ит. |
| R2: test_sync_core_rsync_failure ломается после делегирования (ожидает SyncCoreError, а deliver_core кидает CoreDeliveryError) | MEDIUM | Обязательное обёртывание: sync_core_to_vps try/except CoreDeliveryError → raise SyncCoreError(str(e)). Сообщение CoreDeliveryError начинается с «rsync core/ failed for {host}» → pytest.raises(match="rsync core/ failed") зелёный. Закреплено в TASK-3 acceptance. |
| R3: Timeout 600s для rsync убивает легитимно долгий трансфер | LOW | 600s = канонический deploy-дефолт ssh.sh (ssh_exec timeout 600). Трансфер > 600s аномален. Документируется TRAP-комментарием в deliver_*. Если понадобится — параметризация timeout. |
| R4: `python3 -m core.internal.bootstrap.core_deliverer` требует cwd = repo root | LOW | Прецедент: overlay_deliverer вызывается так же (remote-cmd.sh:141). bootstrap.sh выполняется из Makefile с repo-root cwd. |
| R5: Двойной source фасада (bootstrap.sh:34 + remote-cmd.sh 3 call-site) | LOW | SSH_OPTS declare-guard + ssh.sh readonly source-guard + paths.sh PATHS_LIB_DIR guard — повторный source безопасен (проверено текущей архитектурой source'инга). |
| R6: Cross-plan 101: remote_executor.py (если создан) импортирует sync_core_to_vps — ломается при изменении сигнатуры | LOW | Сигнатура sync_core_to_vps НЕ меняется (host, core_src, node_name, node_yaml, dry_run) → bool. Исключения: SyncCoreError сохраняется. Делегирование прозрачно для импортёра. |
| R7: wc -l фасада > 60 из-за TRAP-комментариев | MEDIUM | Сжатие модульного контракта до 8-10 строк; TRAP-аннотации — компактный 2-строчный формат. Целевой файл ~51-55 строк (§2.1). Если TRAP-сохранность требует >60 — приоритет отдаётся TRAP'ам (AC8-эквивалент), критерий интерпретируется как ≤60 строк кода фасада. |
| R8: DRY_RUN семантика (boolean в entrypoints vs DRY_RUN=1 в ssh.sh) | LOW | Предсуществующая неконсистентность (RSK1 Plan 101). side-stepped: фасад пробрасывает `${DRY_RUN:+--dry-run}`, Python принимает argparse store_true. |
| R9: exclude-паттерны «уплывают» при копировании | LOW | Тесты assert'ят exact паттерны (AC7) — любой drift падает тест. Плюс таблица AC7 как канонический reference. |

---

## 10. Non-Goals

- ❌ НЕ трогать prepare_ssh_opts() (4 активных caller'а: bootstrap.sh:182 + remote-cmd.sh:170/208/244; низкоуровневые SSH-опции, остаётся в shell per Brief)
- ❌ НЕ менять entrypoint-manifest.yaml (scp-deliver.sh:527 — consumer lib/ssh.sh, валиден)
- ❌ НЕ менять bootstrap.sh, remote-cmd.sh, node-update.sh, converge.sh (API фасада сохраняется)
- ❌ НЕ трогать build-функции printf %q / build-ssh-cmd.sh (домен Plan 101, D3)
- ❌ НЕ мигрировать vhost overlays / resolve / extract из overlay_deliverer (не в скоупе)
- ❌ НЕ создавать shared-модуль для SSH_OPTS (D2: mirror-конвенция, over-engineering)
- ❌ НЕ добавлять mkdir в sync_core-путь (deliver_core вызывается без ensure_remote_dirs — поведение node-update идентично)
- ❌ НЕ менять глаголы/таргеты Makefile (новых make-таргетов нет)
- ❌ НЕ запускать E2E на production VPS (только test-VPS, опционально, не блокирует)

---

## 11. Поправки к брифу

| Пункт брифа | Факт (из кода) | Поправка в DevPlan |
|-------------|---------------|-------------------|
| «deliver_core(), deliver_node_configs(), ensure_remote_dirs()» | scp_to_server() реально имеет 5 rsync-фаз: core/, platform-env.yaml, Makefile, node-configs/<node>/, secrets/ | +deliver_platform_env(), deliver_makefile(), deliver_secrets() (краткие, по 15-20 LOC каждая) |
| «scp secrets/» | Phase 3 источник — node-configs/<node>/secrets/ (per-node), назначение /opt/node-configs/secrets/ (TRAP[BUG] 2026-07-23 P0) | deliver_secrets(): src={ncd}/{node}/secrets, dst={ncb}/secrets/ |
| «deliver_core() — основная логика: rsync core/ + scp secrets/» | Core-rsync уже частично существует в overlay_deliverer.sync_core_to_vps (дублирование) | F3: делегирование — устранение дублирования (P2, D3) |
| «prepare_ssh_opts() остаётся в shell» | Подтверждено: 4 активных caller'а (bootstrap.sh + remote-cmd.sh ×3), управляет known_hosts | Остаётся без изменений; в фасаде ≤60 LOC |
| «DRY_RUN режим» | bootstrap.sh обрабатывает dry-run ДО scp_to_server (строки 177-180/193-195) | Фасад пробрасывает ${DRY_RUN:+--dry-run} defensive; entrypoint-путь не меняется (AC5) |
| «AC2: ≤60 LOC» | Модульный контракт + TRAP-аннотации занимают ~15 строк | Целевой файл ~51-55 строк; приоритет TRAP-сохранности (R7) |

---

## 12. Implementation Commands

### Wave 1 (TASK-1)
```
coder Read .ai/plans/108-scp-deliver-python/02-DevPlan.md, implement Wave 1 TASK-1:
Create core/internal/bootstrap/core_deliverer.py (~230 LOC) — standalone Python module for the
Core delivery channel (push-based SCP/rsync, NO git). SSH_OPTS mirror (lib/ssh.sh SSH_OPTS_COMMON),
RSYNC_EXCLUDES_CORE (5 patterns) / _NODE (3) / _SECRETS (1). Functions: resolve_remote_base(),
resolve_node_configs_base(), ensure_remote_dirs(), deliver_core(), deliver_platform_env(),
deliver_makefile(), deliver_node_configs(), deliver_secrets(), deliver_all(), cli().
Preserve EXACT rsync commands and log strings per §5 tables AC6/AC7. Fail-fast per phase.
Timeouts: mkdir=30, rsync=600. DRY_RUN prints commands (IMP:8), no exec. Migrate 4 TRAP
annotations (mkdir BUG 2026-07-16, secrets P0 2026-07-23, platform-env DECISION 2026-07-16,
Makefile DECISION 2026-07-17). Full semantic markup: MODULE_CONTRACT, GREP_SUMMARY, STRUCTURE,
region markers, LDD logs.
```

### Wave 2 (TASK-2 + TASK-5 merged — parallel with TASK-3, TASK-4)
```
coder Read .ai/plans/108-scp-deliver-python/02-DevPlan.md, implement Wave 2 TASK-2+TASK-5:
Reduce core/internal/bootstrap/scp-deliver.sh to <=60 LOC: keep module contract (compressed),
source guards (paths.sh, ssh.sh, SSH_OPTS declare), prepare_ssh_opts() UNCHANGED (with
TRAP[DECISION] 2026-07-18 known_hosts init-only), scp_to_server() thin wrapper calling
python3 -m core.internal.bootstrap.core_deliverer deliver --host --node --node-configs-dir
--core-dir --remote-user ${REMOTE_SSH_USER:-root} ${DRY_RUN:+--dry-run}. Keep TRAP[DECISION]
2026-07-17 (entrypoint-manifest registration). Delete all rsync/ssh orchestration.
Update core/internal/bootstrap/AGENTS.md: @scope +core_deliverer.py, Shell-facades table
+scp-deliver.sh 251→<=60, unit-tests list +test_core_deliverer.py.
```

### Wave 2 (TASK-3 — parallel)
```
coder Read .ai/plans/108-scp-deliver-python/02-DevPlan.md, implement Wave 2 TASK-3:
Modify core/internal/bootstrap/overlay_deliverer.py: sync_core_to_vps() delegates Phase 1
core/ rsync to core_deliverer.deliver_core(host, core_dir, remote_user="root", dry_run) via
`from core.internal.bootstrap.core_deliverer import CoreDeliveryError, deliver_core,
resolve_remote_base`. Wrap CoreDeliveryError -> SyncCoreError(str(e)) preserving "rsync core/
failed" message. Keep node.yaml rsync. Remove dead RSYNC_EXCLUDES constant. Update TRAP[BUG]
2026-07-31 comment (single resolution point = core_deliverer.resolve_remote_base()).
Signature unchanged. Verify tests/unit/test_overlay_deliverer.py stays green.
```

### Wave 2 (TASK-4 — parallel)
```
coder Read .ai/plans/108-scp-deliver-python/02-DevPlan.md, implement Wave 2 TASK-4:
Create tests/unit/test_core_deliverer.py with 14 tests per §TEST_SPEC. tmp_path fixtures,
mock.patch subprocess.run, caplog IMP:9 LDD trajectory (local _assert_imp9 helper like
test_overlay_deliverer.py). Assert exact exclude patterns (AC7) and command args.
Each test function annotated with TRAP[TEST] (Regression/Scenario/Last fail/Remove if).
```

### Wave 3 (TASK-6)
```
coder Read .ai/plans/108-scp-deliver-python/02-DevPlan.md, implement Wave 3 TASK-6:
Run: pytest tests/unit/test_core_deliverer.py tests/unit/test_overlay_deliverer.py -s -v
(overlay must stay green WITHOUT modification), pytest tests/test_node_lifecycle_static.py,
make gate MODE=fast. Verify AC1-AC8. Report results.
```

---

## 13. Verification Checklist (QA)

| Проверка | Критерий |
|----------|----------|
| AC1 | `python3 -m core.internal.bootstrap.core_deliverer deliver --help` → usage |
| AC2 | `wc -l core/internal/bootstrap/scp-deliver.sh` → ≤ 60 |
| AC3 | deliver_all() последовательность фаз + exit 0/1 (unit) |
| AC4 | test_overlay_deliverer.py (10 тестов) зелёный без модификации |
| AC5 | dry-run: 0 subprocess-вызовов, IMP:8 печать, success |
| AC6 | caplog: точные строки по §5 таблице, ≥1 IMP:9 на успехе |
| AC7 | exclude-паттерны: тесты assert'ят 5/3/1 паттерны |
| AC8 | `make gate MODE=fast` — без новых FAIL |
| TRAP | 4 мигрированных в core_deliverer.py + 2 сохранённых в фасаде + обновлённый TRAP[BUG] в overlay_deliverer.py |
| DEAD CODE | `grep RSYNC_EXCLUDES core/internal/bootstrap/overlay_deliverer.py` → 0 совпадений (после TASK-3) |
| GREP_SUMMARY | На каждом файле (F1-F4): # GREP_SUMMARY присутствует |
| REGIONS | Все # region/# endregion сбалансированы (F1, F2, F3) |

---

## $QA_VERIFICATION

**Verdict:** SUCCESS
**Timestamp:** 2026-07-31T18:19:25+03:00
**SHA:** fbe306d4284d9105193605378be28eb64b3c6795

### Verification Summary

| Domain | Result | Detail |
|--------|:------:|--------|
| **Protocol compliance** | PASS | `$START_DEVPLAN`/`$END_DEVPLAN` ✓, `$ARTIFACT_CONTRACT` 7/7 fields ✓, Draft Code Graph (§2.2) ✓, Data Flow (§3) ✓, File Manifest (§4) ✓, Implementation Steps (§6+§12) ✓, Parallel Groups (§7) ✓ |
| **Brief compliance** | PASS | All 8 Brief ACs covered (AC1-AC8). Deviations documented in §11 «Поправки к брифу»: expanded 3→6 functions (`deliver_platform_env`, `deliver_makefile`, `deliver_secrets` added), `sync_core_to_vps` delegation (D3). All justified with code evidence. |
| **TRAP coverage** | PASS | 6 TRAPs from `scp-deliver.sh`: 2 PRESERVE (`entrypoint-manifest` :29-33, `known_hosts` :73-77), 4 MIGRATE (`mkdir` BUG :133-139, `platform-env` :168-171, `Makefile` :187-190, `secrets` P0 :224-229). 2 TRAPs from `overlay_deliverer.py`: 1 IN_SCOPE (`TRAP[BUG]` P1 :197-203), 1 DEFER (`TRAP[DEBT]` :19). 1 DRIFT (AGENTS.md) → IN_SCOPE. |
| **Factual accuracy** | PASS | All line-number references verified against actual source: TRAP locations ✓, call-site locations (bootstrap.sh:182, remote-cmd.sh:170/208/244) ✓, `entrypoint-manifest.yaml:527` consumer entry ✓, `overlay_deliverer.py:12` SSH_OPTS mirror comment ✓, AC6 log strings ditto ✓, AC7 rsync commands ditto ✓. |
| **Implementation readiness** | PASS | AC6 table: 23 exact log strings for parity verification. AC7 table: 5 exact rsync command matrices. Test spec: 14 scenarios covering excludes, dry-run, fail-fast, IMP:9, CLI exit codes. Risk matrix: 9 risks with mitigations. Non-Goals (§10): 9 explicit exclusions. |
| **Cross-plan consistency** | PASS | Plan 101 `remote_executor.py` (parallel): `sync_core_to_vps` signature preserved → no conflict. Plan 036 Wave 5d: `test_overlay_deliverer.py` regression constraint satisfied (signature unchanged, `deliver_core` → `CoreDeliveryError` → `SyncCoreError` wrapper → message preserved). |

### Issues Found

| Severity | ID | Description | Action |
|:--------:|----|-------------|--------|
| WARNING | Q1 | `scp-deliver.sh:80` — stale docstring claims `prepare_ssh_opts() has 8 active callers`. Actual count = 4 (bootstrap.sh:182 init + remote-cmd.sh:170/208/244 update). Pre-W2-E1 era artifact. | Correct in TASK-2 when reducing facade. |
| INFO | Q2 | Brief `01-Brief.md:33` — self-referential `@IMPLEMENTS Brief 108` (should reference what it implements, not itself). Non-blocking; Brief quality note only. | No action needed in DevPlan. |

### Technical Notes

- **D3 (sync_core_to_vps → делегирование):** Verified that `overlay_deliverer.sync_core_to_vps` (line 186) capture_output=True (line 223), so behavioral change is transparent. `CoreDeliveryError` → `SyncCoreError` wrapper preserves `"rsync core/ failed"` message substring → existing tests `test_sync_core_rsync_failure` and `test_sync_core_dry_run` stay green.
- **D5 (rsync timeout 600s):** Confirmed canonical deploy default in `lib/ssh.sh:119` (`timeout="${4:-600}"`). `scp-deliver.sh:142` mkdir timeout=30 matches. Hardening is defensive, not behavioral change.
- **D2 (SSH_OPTS mirror):** Verified `overlay_deliverer.py:12` ("SSH_OPTS mirror lib/ssh.sh SSH_OPTS_COMMON") — third mirror in `core_deliverer.py` follows established pattern. Single source of truth remains `lib/ssh.sh SSH_OPTS_COMMON`.
- **R8 (DRY_RUN семантика):** `bootstrap.sh:177-180` early-exit + `scp_to_server` пробрасывает `${DRY_RUN:+--dry-run}` → Python `argparse store_true`. No behavioral change.

$END_DEVPLAN
