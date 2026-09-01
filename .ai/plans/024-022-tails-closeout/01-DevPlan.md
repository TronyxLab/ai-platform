$START_DEVPLAN

# DevPlan 024 — Закрытие хвостов DevPlan 022: platform-resolver glob, scaffold deploy-key, канонизация runbook overlay-доступа

## $ARTIFACT_CONTRACT

| Поле | Значение |
|------|----------|
| PURPOSE | Закрыть три TRAP-хвоста DevPlan 022 (коммит `1f6eb99`): (1) glob-резолв `NodeYaml.resolve` не покрывает канонический layout `~/projects/*/platform/node-configs/` — overlay-node.yaml не доезжает до `node-update`, desired-state расходится фиксстура↔overlay; (2) `make new-context` не провижинит read-only deploy key для приватного `<ctx>-overlay` — первый деплой проекта контекста падает на clone; (3) runbook VPS-доступа к приватному overlay живёт только в TRAP-комментариях — не канонизирован в AGENTS.md. |
| DESCRIPTION | Wave 1 (параллельно, 2 Coder-worktree): **TASK-1** — кандидат `projects/*/platform/node-configs/{node}/node.yaml` в `NodeYaml.resolve` ПЕРЕД legacy sibling-glob + IMP:7 WARN при legacy-резолве (видимость миграционного долга); **TASK-2** — `provision_deploy_key` в `context_initializer.py` (ssh-keygen → `gh repo deploy-key add` read-only → приватный ключ 0600 в `<ctx>/.secrets/` вне platform/-репо), skeleton `repos.core` → `git@github.com-overlay:<org>/<ctx>-overlay.git`, summary с node-side install-инструкцией. Wave 2 (последовательно): **TASK-3** — документация (root + bootstrap AGENTS.md) и lifecycle-закрытие трёх TRAP-аннотаций; **TASK-4** — merge + верификация (`make check`, `make agent-check`, smoke-резолв). |
| RATIONALE | Оба кодовых хвоста — прямые следствия смены канона layout (022 Option A) без доведения двух потребителей канона: резолвер (читатель layout) и scaffold (создатель контекста). Rev-условия обоих TRAP[DEBT] наступили: «первый сбой доставки node.yaml контекстной ноды» — деплои контекста tronyx-lab пойдут с legacy-фикстурой; «первый new-context после 022» — следующий scaffold породит HTTPS-URL, который VPS не клонирует (репо приватный). Чинить сейчас = до первого реального потребителя новых контекстов. |
| ACCEPTANCE_CRITERIA | (1) `NodeYaml.resolve` для ноды, присутствующей И в overlay (`projects/<ctx>/platform/node-configs/`), И в legacy sibling — возвращает overlay-путь; порядок: explicit `config_dir` → platform-glob → legacy-glob → `/opt`; (2) legacy-резолв логирует `[IMP:7]` WARN; (3) `make new-context` (gh доступен) добавляет read-only deploy key в `<org>/<ctx>-overlay`, приватный ключ 0600 в `<ctx>/.secrets/` (вне platform/-репо), skeleton `repos.core` = SSH-алиасный URL; (4) node-side runbook установки ключа задокументирован в `core/internal/bootstrap/AGENTS.md`, SSH-алиас-канон — в root `AGENTS.md`; (5) три TRAP-аннотации переведены по lifecycle (BUG-fixed / DECISION-deferred); (6) `make check` зелёный, `make agent-check` clean; (7) smoke: `node_resolver resolve --node tronyx-vps` возвращает overlay-путь. |
| IMPLEMENTS | Закрытие Debt-хвостов DevPlan 022 (TRAP[DEBT] ×2 + runbook-канонизация TRAP[DECISION]) |
| IMPACTS | `core/internal/shared/node_yaml/resolve.py`, `core/internal/scaffold/context_initializer.py`, `tests/unit/test_node_resolver.py` + смежные тесты резолва, `tests/unit/test_context_initializer.py`, `AGENTS.md` (root), `core/internal/bootstrap/AGENTS.md`, TRAP-аннотации трёх файлов |
| REQUIRES | Репозиторий ai-platform (main = origin/main, `f86b17a`); `make check` / `make agent-check`; для smoke — локальный overlay `~/projects/tronyx-lab/platform/` (есть). VPS НЕ требуется. |

---

## 1. Requirements Analysis — что чиним и почему именно сейчас

### 1.1 Верифицированные факты (сверено с кодом и git, 2026-09-01)

| Факт | Источник | Статус |
|------|----------|--------|
| TRAP[DEBT]: glob не покрывает `~/projects/*/platform/node-configs/{node}/node.yaml`; Rev — «первый сбой доставки node.yaml контекстной ноды → добавить platform/-кандидат с тестами резолва (fixture↔overlay сверка)» | `resolve.py:109-121` | ✅ подтверждён |
| Порядок кандидатов сейчас: `{config_dir}/node-configs/…` → legacy glob `~/projects/*/node-configs/…` (sorted) → `/opt/node-configs/…` | `resolve.py:98-124` | ✅ подтверждён |
| node-update NODE=tronyx-vps резолвит legacy-фикстуру `~/projects/ai-platform/node-configs/…` (ai-platform < tronyx-lab в sorted-порядке glob), содержимое overlay node.yaml (projects, monitoring, postgres_init_databases) до VPS не доходит | TRAP Observed + on-disk `~/projects/ai-platform/node-configs/tronyx-vps/` | ✅ подтверждён |
| TRAP[DEBT]: scaffold не провижинит deploy key; Rev — «первый new-context после 022 → добавить deploy-key шаг в gh_repo_create + тест» | `context_initializer.py:253-261` | ✅ подтверждён |
| Skeleton `repos.core` = `https://github.com/{org}/{context_name}-overlay.git` (HTTPS) — приватный репо не клонируется с VPS без auth | `context_initializer.py:83` | ✅ подтверждён |
| Канон доступа (решение владельца 2026-09-01): read-only deploy key + SSH-алиас `github.com-overlay`; приватный ключ живёт ТОЛЬКО на ноде; репо-URL `git@github.com-overlay:<org>/<ctx>-overlay.git` | TRAP[DECISION] `context_overlay.py:242-248` + node.yaml tronyx-lab-overlay | ✅ подтверждён |
| `ensure_context_repo` при clone-failure логирует remediation-инструкции (non-fatal WARN) — громкий fail уже есть | `context_overlay.py` @invariants | ✅ подтверждён |
| Хвост «коммиты 022 не запушены» устранён последующими сессиями: origin/main == main == `f86b17a`, 0 unpushed | `git log origin/main..main` | ✅ проверено |
| e2e conftest и tests/_conftest/node.py завязаны на Path 1 (explicit platform_root) — порядок Path 1 менять нельзя | `tests/e2e/conftest.py:129`, `tests/_conftest/node.py:14` | ✅ подтверждён |
| Тест first-match-wins ассертит перечень searched-путей в error-логе — расширение списка кандидатов потребует обновления ожидания | `tests/unit/test_lib_node_resolver.py:338` | ✅ подтверждён |
| Тесты Path 2 используют HOME-override / mock `os.path.expanduser` / уникальные имена нод (hermetic-конвенция) | `test_lib_node_resolver.py:216`, `test_domain_verifier.py:83`, `test_loadtest_config.py:127` | ✅ подтверждён |
| `gh repo deploy-key add <pubfile>` без `--allow-write` = read-only ключ; дубликат → ошибка «already exists» | gh CLI docs | ✅ канон |

### 1.2 Корневая причина

Смена канона раскладки (022: сестринские каталоги → overlay-контейнер `platform/`) выполнена в scaffold'е и документации, но два потребителя канона не доведены:
1. **Читатель** (`NodeYaml.resolve`) — glob ищет только legacy sibling `projects/*/node-configs/`; канонический путь невидим. Старый glob при этом УСПЕШНО находит dev-фикстуру `ai-platform/node-configs` (repo живёт в `~/projects/`) — silent wrong-source: резолв не падает, а доставляет устаревший desired-state.
2. **Создатель** (`new-context`) — пишет HTTPS-URL в `repos.core`, не провижинит VPS-auth. Для tronyx-lab это чинилось вручную при миграции (TASK-5 022); следующий контекст воспроизведёт тот же ручной цикл.

Плюс неполная канонизация: решение о deploy key + SSH-алиасе зафиксировано только в TRAP-комментарии — следующий агент/оператор узнает о нём из кода, а не из канона.

### 1.3 Решённые развилки (superposition, auto-collapsed — фиксируются TRAP'ами в TASK-3)

**D1. Порядок кандидатов резолва.**
- ✅ Выбрано: explicit `config_dir` (Path 1) → **NEW** platform-glob → legacy sibling-glob → `/opt`. Канон «overlay = единственный источник контекстных данных» — overlay бьёт legacy-фикстуру; explicit `config_dir` остаётся первым (operator/CI intent, e2e conftest Path 1 — контракт не ломается; на dev `/opt/platform` не существует → Path 1 пропускается и overlay выигрывает естественно).
- Rejected: platform-glob ВЫШЕ explicit `config_dir` — ломает Path 1-контракт e2e/CI (pinned PLATFORM_ROOT) ради несуществующего кейса.
- Rejected: platform-glob ПОСЛЕ legacy — не устраняет drift (legacy-фикстура снова выигрывает, TRAP-impact сохраняется).

**D2. Границы автоматизации deploy key.**
- ✅ Выбрано: scaffold автоматизирует ТОЛЬКО repo-side (keygen + `gh repo deploy-key add` + SSH-алиасный URL); node-side установка — ручной шаг по runbook, печатается в summary. Условие сходимости: ключ на ноде нужен к моменту первого `deploy-context`/`ensure_context_repo`, а не к моменту scaffold.
- Rejected: SSH-install на scaffold-этапе — нода может не существовать/не быть забутстраплена (fresh-context-first: контекст создаётся раньше ноды); scaffold получает SSH-зависимость и новые failure-моды.
- Rejected: sops-канал `OVERLAY_DEPLOY_KEY` (secret-definitions.yaml + φ5) — touch SoT-манифеста секретов и его гейтов; отдельный план при втором контексте (Rev в TASK-3).

**D3. Хранение приватного ключа.**
- ✅ Выбрано: `~/projects/<ctx>/.secrets/<ctx>-overlay-deploy-key` (0600) + `.pub` (0644). Каталог контекста не является git-репо (репо — только `platform/`) — риск коммита исключён геометрией.
- Rejected: внутри `platform/node-configs/` — ключ рядом с коммитимым overlay (расширение поверхности утечки).
- Rejected: только stdout (без файла) — потеря ключа при закрытии терминала; DR возможен (re-gen + gh re-add), но хранение дешевле пере-генерации.

---

## 2. Draft Code Graph + Data Flow

```
make new-context NODE=<n>                                    (изменения TASK-2)
  → context_initializer.py
      create_dirs()               ──► без изменений (022)
      create_skeleton_node_yaml() ──► repos.core = git@github.com-overlay:<org>/<ctx>-overlay.git
      gh_repo_create()            ──► repo <org>/<ctx>-overlay (private)
        └─ provision_deploy_key() ──► ssh-keygen ed25519 → <ctx>/.secrets/<ctx>-overlay-deploy-key(.pub) 0600
                                   ──► gh repo deploy-key add <pub> --title "vps-<ctx>-readonly"   (read-only)
                                   ──► summary: node-side install-инструкция (scp + ssh-config alias)
      register_in_platform_yaml() ──► без изменений

NodeYaml.resolve(node)                                        (изменения TASK-1)
  ▶ Path 1: {config_dir}/node-configs/<node>/node.yaml        (explicit — ПЕРВЫЙ, контракт e2e)
  ▶ Path 2: ~/projects/*/platform/node-configs/<node>/…       (NEW — канон overlay, 022 Option A)
  ▶ Path 3: ~/projects/*/node-configs/<node>/…                (legacy sibling — миграционное окно + IMP:7 WARN)
  ▶ Path 4: /opt/node-configs/<node>/node.yaml                (VPS delivered — без изменений)
  → первый isfile → NodeYaml(path)

VPS (без изменений кода): node-update доставляет overlay-node.yaml в /opt/node-configs
  (источник = overlay после TASK-1); ensure_context_repo клонирует repos.core по SSH-алиасу
  (ключ ставится по runbook TASK-3; clone-failure — громкий WARN с remediation, уже есть)
```

**Изменения потока:** один glob-кандидат + WARN в резолвере; deploy-key-шаг + URL-форма в scaffold. `ensure_context_repo`, `promote_via_ssh`, CI-воркфлоу, VPS-пути — не трогаются.

---

## $TASKS

### TASK-1 — `NodeYaml.resolve`: platform-glob кандидат (канон overlay-first)
**Владелец:** Coder · **Сложность:** 3/10 · **Файлы:** `core/internal/shared/node_yaml/resolve.py`, тесты резолва
**Исполнение:** Agent Manager worktree (параллельно с TASK-2) · **Верификация:** `make check TEST_FILE=<тест-файл>`

- В `resolve()` добавить группу кандидатов МЕЖДУ Path 1 и legacy-glob (D1):
  `sorted(glob(~/projects/*/platform/node-configs/{node_name}/node.yaml))` — все platform-матчи предшествуют всем legacy-матчам (групповой порядок детерминирован; алфавитный порядок внутри группы сохранён).
- Legacy-резолв (сработал только sibling-glob) → `[IMP:7]` WARN: overlay-канонический путь не найден, резолв через legacy sibling — миграционный сигнал (удаляется после миграции asi-group, см. Rev).
- Обновить контракт файла: MODULE_CONTRACT `@invariants` (нумерация путей), docstring resolve(), STRUCTURE, GREP_SUMMARY (+`platform-node-configs`).
- TRAP[DEBT] `resolve.py:109-121` — НЕ трогать в TASK-1 (lifecycle-перевод — TASK-3, чтобы не конфликтовать по строкам с TASK-3-правками; git merge бесконфликтен, т.к. правки в разных регионах файла).

**$TEST_SPEC** (файл по конвенции Path 2-тестов — `tests/unit/test_node_resolver.py` / `test_lib_node_resolver.py`; hermetic: уникальные имена нод или HOME-override, конвенция `test_loadtest_config.py:127`):
1. `test_platform_overlay_wins_over_legacy_fixture` — `~/projects/legacy-fixture/node-configs/<n>/node.yaml` + `~/projects/<ctx>/platform/node-configs/<n>/node.yaml` → резолв = platform-путь. *Падает на старом коде (анти-survivorship: старый glob platform-путь не находит).*
2. `test_legacy_glob_fallback_with_warn` — только legacy-матч → резолв = legacy-путь + caplog `[IMP:7]` WARN.
3. `test_explicit_config_dir_still_first` — env PLATFORM_ROOT=<tmp> + overlay в fake HOME → резолв = Path 1 (контракт e2e conftest сохранён).
4. Порядок групп: ≥2 platform-матчей и ≥2 legacy-матчей → победитель из platform-группы при любом алфавитном раскладе.

**Acceptance:** AC1, AC2; существующие тесты Path 1/Path 4 без деградации.

---

### TASK-2 — scaffold: provision_deploy_key + SSH-алиасный repos.core
**Владелец:** Coder · **Сложность:** 4/10 · **Файлы:** `core/internal/scaffold/context_initializer.py`, `tests/unit/test_context_initializer.py`
**Исполнение:** Agent Manager worktree (параллельно с TASK-1) · **Верификация:** `make check TEST_FILE=tests/unit/test_context_initializer.py`

- `_SKELETON_TEMPLATE`: `repos.core: git@github.com-overlay:{org}/{context_name}-overlay.git` (форма канона D2/TRAP[DECISION]; SSH-алиас `github.com-overlay` ставится на ноде по runbook TASK-3).
- Новая функция `provision_deploy_key(org, ctx, context_dir, *, gh_runner, keygen_runner=None) -> tuple[str | None, int]` (возвращает путь ключа | None, warnings):
  - keypair: `ssh-keygen -t ed25519 -N "" -q -C "overlay-deploy-<ctx>" -f <context_dir>/.secrets/<ctx>-overlay-deploy-key`; файл существует → skip keygen (идемпотентность); chmod 0600 приватный / 0644 pub;
  - `gh repo deploy-key add <pub> --repo <org>/<ctx>-overlay --title "vps-<ctx>-readonly"` — БЕЗ `--allow-write`; stderr содержит «already exists» → treat as success (паттерн reuse из `gh_repo_create`);
  - graceful: gh недоступен/не авторизован/`--skip-gh-repo` → warn + warnings+1, БЕЗ keygen, continue (паттерн существующих gh-гвадов);
  - DI: `keygen_runner`/`gh_runner` инъектятся (тесты без реальных ssh-keygen/gh; паттерн `gh_runner` из `gh_repo_create`).
- Вызов из `gh_repo_create()` после подтверждения репо (created | already exists), до `_git_init_and_push`.
- `report_summary()`: строка «Deploy key: <path>.pub → <repo> (read-only)» + блок node-side install-инструкции (scp ключа на ноду + ssh-config Host `github.com-overlay` → IdentityFile/IdentitiesOnly) + предупреждение «приватный ключ не коммитить».
- MODULE_CONTRACT: `@invariants` — «Skeleton repos.core = SSH-алиасный URL»; TRAP[DEBT] `:253-261` не трогать (TASK-3).

**$TEST_SPEC** (расширить `tests/unit/test_context_initializer.py`, fake-runners):
1. `test_skeleton_repos_core_ssh_alias_url` — skeleton содержит `git@github.com-overlay:<org>/<ctx>-overlay.git`, НЕ содержит `https://github.com`. *Падает на старом коде.*
2. `test_provision_deploy_key_happy_path` — fake keygen создаёт файлы → gh-вызовы содержат `deploy-key add`, `--repo <org>/<ctx>-overlay`, title, и НЕ содержат `--allow-write`; приватный ключ 0600, путь вне `context_dir/platform/`. *Падает на старом коде (deploy-key add не вызывался).*
3. `test_deploy_key_skipped_when_gh_unavailable` — gh rc≠0 → warnings≥1, keygen НЕ вызван, skeleton всё равно SSH-алиасный.
4. `test_deploy_key_add_duplicate_tolerated` — gh rc=1, stderr «already exists» → success, warnings без роста.
5. `test_deploy_key_idempotent_existing_keypair` — ключ существует → keygen НЕ вызван, pub переиспользован в add.

**Acceptance:** AC3; `make check TEST_FILE=tests/unit/test_context_initializer.py` зелёный.

---

### TASK-3 — Документация + lifecycle-закрытие TRAP
**Владелец:** Coder · **Сложность:** 2/10 · **Файлы:** `AGENTS.md` (root), `core/internal/bootstrap/AGENTS.md`, `resolve.py`, `context_initializer.py`, `context_overlay.py` (только TRAP-блоки)
**Исполнение:** последовательно, после merge Wave 1 (правки в тех же регионах, что TASK-1/2)

- Root `AGENTS.md` §«Каноническая структура контекстной папки» — 2-3 строки: VPS-доступ к приватному overlay = read-only deploy key + SSH-алиас `github.com-overlay` (`repos.core = git@github.com-overlay:<org>/<ctx>-overlay.git`); приватный ключ — только на ноде + `~/projects/<ctx>/.secrets/` (0600); `new-context` провижинит repo-side автоматически, node-side — runbook bootstrap/AGENTS.md.
- `core/internal/bootstrap/AGENTS.md` — подраздел runbook «VPS-доступ к приватному overlay (deploy key)»: keygen → `gh repo deploy-key add` (read-only) → scp на ноду → ssh-config Host `github.com-overlay` → верификация `git ls-remote` с ноды; для ретро-контекстов (tronyx-lab) и для skip-gh-case.
- TRAP-аннотации:
  - `resolve.py` TRAP[DEBT] → ⚠️ TRAP[BUG] (2026-09-01, fixed DevPlan 024): Symptom/Root/Fix/Prevention (Prevention: смена канона layout = обход ВСЕХ glob-потребителей канона тем же планом). Rev: удалить legacy sibling-glob (и WARN) после миграции asi-group.
  - `context_initializer.py` TRAP[DEBT] → 🧐 TRAP[DECISION] (2026-09-01): node-side доставка ключа — ручной шаг по runbook; Rejected: SSH-install на scaffold (D2), sops `OVERLAY_DEPLOY_KEY`+φ5 (SoT-гейты) · Rev: второй контекст / следующий fresh-node bootstrap → автоматизация sops-каналом.
  - `context_overlay.py` TRAP[DECISION] — дополнить `@links` на runbook bootstrap/AGENTS.md (текст решения не менять).

**Acceptance:** AC4, AC5; grep-проверки: root AGENTS.md содержит `github.com-overlay`; bootstrap/AGENTS.md содержит раздел runbook; `TRAP[DEBT]` в трёх файлах переведены.

---

### TASK-4 — Merge + верификация
**Владелец:** Lead (основная сессия) · **Сложность:** 3/10

1. Merge веток TASK-1/TASK-2 → `feat/024-tails-closeout` → TASK-3 в той же ветке.
2. `make check` до чистоты (батч; фикс-цикл на полном множестве ошибок) + `make agent-check` (exit 0).
3. Смежные тесты резолва: `make check TEST_FILE=tests/unit/test_lib_node_resolver.py`, `TEST_FILE=tests/unit/test_node_resolver.py`, `TEST_FILE=tests/unit/test_domain_verifier.py` — обновить first-match-wins ожидание перечня searched-путей (`:338`), если RED (R1).
4. Smoke (dev, без VPS): `python3 -m core.internal.shared.node_resolver resolve --node tronyx-vps` → IMP:9 лог показывает `~/projects/tronyx-lab/platform/node-configs/tronyx-vps/node.yaml`; контроль: нода вне overlay (legacy-only) резолвится фикстурой + WARN.
5. Approve владельца через `question` → merge в main. Push — по запросу владельца (pre-push hook quick check).

**Acceptance:** AC6, AC7; журнал `.ai/logs/runs.jsonl` (симлинк `.ai/plans/024-022-tails-closeout/logs`) фиксирует чистый прогон.

---

## 4. Риски и соседние поверхности

| # | Риск | Митигация |
|---|------|-----------|
| R1 | Расширение перечня searched-путей ломает first-match-wins ассерт (`test_lib_node_resolver.py:338`) и возможно `test_domain_verifier.py` (лог-перечень) | TASK-4 шаг 3: обновить ожидания перечня; поведение (first-match-wins) не меняется |
| R2 | Новые тесты резолва подхватят РЕАЛЬНЫЙ overlay dev-машины (`~/projects/tronyx-lab/...`) → нестабильность | Hermetic-конвенция обязательна: уникальные имена нод / HOME-override (§TASK-1 TEST_SPEC) |
| R3 | `ssh-keygen` недоступен в PATH операторской машины | Graceful: rc≠0 → warn + warnings+1, node-side runbook печатается полностью (тот же паттерн gh-not-found) |
| R4 | Дубликат deploy key при повторном scaffold (`already exists`) | Tolerant-ветка (§TASK-2, тест 4) |
| R5 | Удаление legacy-glob «по пути» сломает asi-group (не мигрирован) | Запрещено: legacy-glob сохраняется с WARN; удаление — Rev после миграции asi-group (отдельное изменение) |

## 5. Non-goals

- **Миграция asi-group** — оператор, Rev из 022 Debt Intake (вне репо); этот план только добавляет WARN-видимость legacy-резолва.
- **Node-side автоматизация deploy key** (sops `OVERLAY_DEPLOY_KEY` + φ5/converge R-unit) — deferred, Rev в TASK-3 TRAP[DECISION].
- **VPS-side кандидат** `/opt/<ctx>/platform/node-configs/` — не нужен: после TASK-1 node-update доставляет overlay-контент в `/opt/node-configs`; Rev: появится резолв-потребитель НА ноде, которому нужен живой overlay-node.yaml.
- **zram-лог TRAP[DEBT]** (`lifecycle/helpers/system.py:802`, LO) — хвост другой сессии (launch-validation), не трогаем.

## 6. Протокол исполнения и коммиты

- Wave 1: два параллельных Coder-сеанса в Agent Manager worktrees (модель наследуется), ветки `feat/024-resolver-platform-glob`, `feat/024-scaffold-deploy-key` → merge в `feat/024-tails-closeout` (конфликты не ожидаются: разные файлы).
- Wave 2: TASK-3 последовательно в той же ветке; TASK-4 — lead.
- Верификация per-task: `make check TEST_FILE=...`; финал: `make check` до чистоты + `make agent-check`.
- Коммиты (волна = свой feat-коммит): `docs(024): 022 tails closeout — DevPlan` → `feat(024): W1 resolver platform-glob + scaffold deploy-key` → `feat(024): W2 runbook docs + TRAP lifecycle`.
- Статус-хвосты прошлой сессии: push 022-коммитов уже выполнен (origin/main == main) — задач в плане не требует.

$END_DEVPLAN
