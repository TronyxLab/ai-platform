$START_DEVPLAN

# DevPlan 022-R2 — Слияние `node-configs/` + `platform/` в единую контекстную папку + разделение ролей репозитория

> Ревизия `01-DevPlan.md` (R1: авторитетный DevPlan = старший NN). Ключевые отличия от 01:
> (1) Debt «перегруз `repos.core` ↔ `context-promote` target» поднят из DEFER в скоуп — без него
> Option A само-разрушается (`git push --mirror` стирает контекстные коммиты в `<org>/ai-platform`);
> (2) миграция tronyx-lab — squash всей истории в один коммит (решение владельца), история не сохраняется;
> (3) зафиксирован протокол исполнения: Coder-субагенты в отдельных worktree, модель наследуется
> от текущей сессии, финальный вопрос о слиянии в main — через `question` tool.

## $ARTIFACT_CONTRACT

| Поле | Значение |
|------|----------|
| PURPOSE | Устранить дрейф локальной раскладки контекстного каталога (asi-group ↔ tronyx-lab) и убрать само-разрушающий механизм Option A: контекстный overlay получает собственный репозиторий `<org>/<ctx>-overlay`, `<org>/ai-platform` остаётся только зеркалом исходника + Context CI. Зафиксировать один канонический layout. |
| DESCRIPTION | Косметическая стандартизация `~/projects/<context>/` вокруг единого overlay-репозитория `<org>/<ctx>-overlay` (`repos.core`), в который как подкаталоги входят `context.yaml`, `modules/hermes-agent/`, `projects/`, `node-configs/`. Правки: документация, scaffolding (`context_initializer.py` — вложенный layout + один репо `<ctx>-overlay`), точечный резолв `resolve_org` (удаление legacy-пути), тесты. `promote_via_ssh` и VPS-пути не меняются. Миграция tronyx-lab: squash истории в 1 коммит → push в новый репо → смена `repos.core`. |
| RATIONALE | Проверка кода показала: `context_promoter.promote_via_ssh` делает безусловный `git push --mirror` в `<org>/ai-platform`, force-update'я все refs и удаляя отсутствующие в source. Контекстные коммиты tronyx-lab (101 коммит, включая squash «reconciliation of 29 context-specific commits») при следующем promote будут стёрты. Хранить context.yaml/node-configs в этом репо (как предлагает Option A из 01-DevPlan) — закладывать данные под wipe. Разделение ролей репо — precondition Option A, не отдельный долг. |
| ACCEPTANCE_CRITERIA | (1) Документация описывает ровно один канонический layout + две роли репо (`<org>/ai-platform` = CI-зеркало, `<org>/<ctx>-overlay` = overlay); (2) `make new-context` порождает вложенную структуру под `platform/` и один репо `<ctx>-overlay`; (3) `resolve_org` читает только `platform/context.yaml`; (4) `make check` зелёный; (5) overlay-репо tronyx-lab содержит ровно 1 (один) коммит; (6) `repos.core` tronyx-lab указывает на `<ctx>-overlay`. |
| IMPLEMENTS | Слияние `node-configs/` + `platform/` в один каталог-контейнер (Option A) + разрешение Debt «dual role `<org>/ai-platform`» |
| IMPACTS | `AGENTS.md` (root), `core/internal/bootstrap/AGENTS.md`, `core/internal/scaffold/context_initializer.py`, `core/internal/deploy/context_promoter.py`, `tests/unit/test_context_initializer.py`, `tests/unit/test_context_promoter.py`, *(вне репо)* `~/projects/tronyx-lab/platform/`, `TronyxLab/tronyx-lab-overlay` (новый), VPS `/opt/tronyx-lab/platform/` |
| REQUIRES | Репозиторий ai-platform (source); `gh` CLI (создание overlay-репо); доступ к `make check`; VPS tronyx-vps для шага 7 миграции (SCP + converge) |

---

## 1. Requirements Analysis — что чиним и почему именно сейчас

### 1.1 Верифицированные факты (сверено с кодом и диском, 2026-09-01)

| Факт | Источник | Статус |
|------|----------|--------|
| asi-group: `platform/` = полный клон исходника + сестринский `node-configs/` | on-disk `ls ~/projects/asi-group/` | ✅ подтверждён |
| tronyx-lab: `platform/` = overlay (context.yaml, modules/, node-configs/, projects/) + symlink | on-disk `ls ~/projects/tronyx-lab/` | ✅ подтверждён |
| `resolve_org` читает legacy-кандидат `<ctx>/context.yaml` (строка 92) | `context_promoter.py:92` | ✅ подтверждён |
| `create_dirs` создаёт сестринские `hermes-agent/` + `node-configs/` | `context_initializer.py:148-154` | ✅ подтверждён |
| skeleton пишется в `context_dir/node-configs/node.yaml` | `context_initializer.py:531` | ✅ подтверждён |
| `gh_repo_create` создаёт `<ctx>-node-configs` + `<ctx>-hermes-agent`, пушит `node-configs/` в отдельный репо | `context_initializer.py:222-309` | ✅ подтверждён |
| `promote_via_ssh` = безусловный `git push --mirror` → `git@github.com:{org}/ai-platform.git` | `context_promoter.py:175,183-190` | ✅ подтверждён |
| `--mirror` force-update'ит все refs и удаляет remote refs, отсутствующие в source | git docs | ✅ канон git |
| tronyx-lab/platform: 101 коммит, верхние — контекстные; есть незакоммиченные правки node-configs/nginx | `git log` + `git status` | ✅ подтверждён |
| `repos.core` = `<org>/ai-platform` в обоих контекстах; `context.yaml#org: TronyxLab` | node.yaml обоих контекстов | ✅ подтверждён |

### 1.2 Корневая причина (уточнение против 01-DevPlan)

01-DevPlan верно описал три сущности, но ошибся в классификации Debt: «один репо-нейм на две роли» — не фоновый долг, а **активный механизм уничтожения данных**. Хронология tronyx-lab это подтверждает: контекстные коммиты уже переживали wipe и восстанавливались вручную (коммит 4868320 «squash reconciliation of 29 context-specific commits»). Каждый следующий `make context-promote CONTEXT=tronyx-lab` сотрёт context.yaml, node-configs (с `.enc.yaml`), projects/ и контекстный `deploy.yml` из `TronyxLab/AI-platform`; VPS-клон после этого не сможет `pull --ff-only` (non-fatal → молча протухает).

**Вывод:** разделение ролей репозитория включается в этот план (Wave 1), миграция — TASK-5.

### 1.3 Целевая модель репозиториев контекста

| Репозиторий | Роль | Кто пишет |
|-------------|------|-----------|
| `Tronyx161/AI-platform` → зеркало `<org>/ai-platform` | Исходник + Context CI (reusable workflows) | `context-promote` (`push --mirror`) |
| `<org>/<ctx>-overlay` | Контекстный overlay: `context.yaml` + `node-configs/` + `projects/` + `modules/hermes-agent/` + контекстный `.github/workflows/deploy.yml` | Оператор/агент локально, git push |
| *(упразднён)* `<org>/<ctx>-node-configs`, `<org>/<ctx>-hermes-agent` | Ликвидируются как отдельные репо | — |

VPS-путь `/opt/<ctx>/platform/` (клон `repos.core`) не меняется — меняется только URL в `repos.core`.

### 1.4 Дубли/списки (SoT-фиксация, перенос из 01 без изменений)

| Дубль | Канон |
|-------|-------|
| `node.yaml#projects[]` (операционный SoT) ↔ `platform/projects/*.yaml` | `projects/*.yaml` несёт только `monitoring.*` |
| `context.yaml#org/default_node` ↔ `node.yaml#contexts[].name` | непересекающиеся поля |
| source-репо `ai-platform/node-configs/` ↔ overlay `platform/node-configs/` | канон = overlay; source-копия — dev/test фикстуры |

---

## 2. Draft Code Graph + Data Flow

```
make new-context NODE=<n>
  → context-init.sh → context_initializer.py
      create_dirs()      ──►  ~/projects/<ctx>/platform/{node-configs,modules/hermes-agent,projects}
      create_skeleton_node_yaml() ──► platform/node-configs/<node>/node.yaml
                                    (skeleton repos.core = https://github.com/<org>/<ctx>-overlay.git)
      gh_repo_create()   ──►  ОДИН репо <org>/<ctx>-overlay (private) + git init+push platform/
      register_in_platform_yaml() ──► node.yaml contexts[]: node_configs_repo=<ctx>-overlay, hermes_agent_repo=""

make context-promote CONTEXT=<ctx>   (БЕЗ ИЗМЕНЕНИЙ в механике push)
  → resolve_org() ──► org из ~/projects/<ctx>/platform/context.yaml#org  (legacy-путь удалён)
  → push --mirror → <org>/ai-platform   (роль: CI-зеркало; overlay-репо НЕ затрагивается)

Миграция tronyx-lab (TASK-5, локальный git/gh + VPS):
  squash 101 коммита → 1 снапшот-коммит → push TronyxLab/tronyx-lab-overlay
  → repos.core → <ctx>-overlay → VPS: re-clone /opt/tronyx-lab/platform

VPS (не меняется): ensure_context_repo → git clone repos.core → /opt/<ctx>/platform/
```

**Изменения потока:** локальный scaffold + один кандидат-путь в `resolve_org` + URL в `repos.core`. `promote_via_ssh`, `ensure_context_repo`, SCP-канал, CI-воркфлоу — не трогаются.

---

## $TASKS

### TASK-1 — Документация: канонический layout + две роли репо
**Владелец:** Coder · **Сложность:** 3/10 · **Файлы:** `AGENTS.md` (root), `core/internal/bootstrap/AGENTS.md`

- Root `AGENTS.md`: секция «Каноническая структура контекстной папки» — дерево Option A (overlay-репо `<org>/<ctx>-overlay` с `context.yaml`, `modules/hermes-agent/`, `projects/`, `node-configs/`, контекстным `.github/workflows/deploy.yml`) + таблица «почему две папки» (§1.1) + таблица ролей репо (§1.3).
- Обновить строку таблицы `| <context>/ | Служебная папка контекста (node-configs, hermes-agent)…` → «контекстный overlay-контейнер `platform/` = `<org>/<ctx>-overlay`».
- `core/internal/bootstrap/AGENTS.md` таблица артефактов: `/opt/<context>/platform/` = clone `repos.core` (`<ctx>-overlay`), содержимое — `context.yaml` + `modules/` + `node-configs/` + `projects/`.
- TRAP[DECISION]: Option A (слияние каталогов) + разделение ролей репо с Rejected: единый репо (mirror wipe). TRAP[DEBT] на оставшийся пункт (asi-group миграция — оператор).

**Acceptance:** `grep -rn "node-configs, hermes-agent"` в root AGENTS.md не находит устаревшего описания; секции канонического дерева и ролей репо присутствуют; TRAP-комментарии на месте.

### TASK-2 — Scaffold: вложенный layout + один overlay-репо
**Владелец:** Coder · **Сложность:** 5/10 · **Файлы:** `core/internal/scaffold/context_initializer.py`, `tests/unit/test_context_initializer.py`

- `create_dirs()`: вместо сестёр `hermes-agent/` + `node-configs/` — `platform/node-configs/`, `platform/modules/hermes-agent/`, `platform/projects/`.
- `create_skeleton_node_yaml()`: путь `platform/node-configs/<node>/node.yaml` (вызов в `main()` строка 531); в `_SKELETON_TEMPLATE` добавить секцию `repos: core: https://github.com/<org>/<ctx>-overlay.git`.
- `gh_repo_create()`: создать **один** приватный репо `<org>/<ctx>-overlay` («Context overlay for '<ctx>'»); `_git_init_and_push(context_dir / "platform", overlay_repo, ctx)`; вернуть `(overlay_repo, None, warnings)`. Описания репо `<ctx>-node-configs`/`<ctx>-hermes-agent` и второй push-блок удалить.
- `main()`: `search_dirs` glob — первый паттерн `*/node-configs/<node>/node.yaml` → `*/platform/node-configs/<node>/node.yaml`; второй (`ai-platform/node-configs/…`, dev-фикстуры source) сохранить.
- `register_in_platform_yaml()`: `node_cfg_repo=overlay_repo`, `hermes_agent_repo=""` (см. D4).
- `report_summary()`: печатаемые пути — вложенные.
- Обновить unit-тесты на новый layout (включая фиктивный `gh_runner`-контракт: ровно один `repo create`).

**Acceptance:** `make check TEST_FILE=tests/unit/test_context_initializer.py` зелёный; тесты проверяют вложенные пути, один репо, skeleton `repos.core` = overlay, резолв platform node.yaml по новому glob.

### TASK-3 — resolve_org: удаление legacy-пути
**Владелец:** Coder · **Сложность:** 2/10 · **Файлы:** `core/internal/deploy/context_promoter.py`, `tests/unit/test_context_promoter.py`

- В `resolve_org()` удалить кандидат `Path(base) / context / "context.yaml"` (строка 92) — остаётся только `platform/context.yaml`.
- Обновить docstring (STRUCTURE-диаграмма, @invariants, @complexity-комментарий «×2 пути» → «×1»).
- `promote_via_ssh` НЕ менять (target `<org>/ai-platform` — роль CI-зеркала). Добавить TRAP[DECISION] у `promote_via_ssh`: роли репо разделены (D3), mirror push больше не затрагивает контекстное состояние.
- Обновить unit-тесты `test_resolve_org_*`: позитив (platform/context.yaml) + негатив (legacy `<ctx>/context.yaml` игнорируется).

**Acceptance:** `make check TEST_FILE=tests/unit/test_context_promoter.py` зелёный; негативный тест падал бы на старом коде.

### TASK-4 — Верификация полного цикла (Wave 2)
**Владелец:** Coder · **Сложность:** 2/10 · **Файлы:** нет новых (прогон; выполняется в worktree после слияния веток Wave 1)

- `make check` до чистоты (батч всех ошибок, fingerprint-кэш).
- Точечные прогоны: `test_context_initializer.py`, `test_context_promoter.py`, `test_context_overlay.py`, `test_node_yaml_mutation.py`, `test_adopt_project_org_validation.py`, `test_status_collectors.py`.

**Acceptance:** `make check` exit 0; регресс-тесты зелёные; pre-push hook (quick check) не блокирует.

### TASK-5 — Миграция tronyx-lab: squash истории + новый overlay-репо (локальный шаг, не worktree)
**Владелец:** Orchestrator (исполняет сессия-оркестратор локально, после approve пользователя) · **Сложность:** 4/10 · **Файлы:** вне репо (`~/projects/tronyx-lab/platform/`, GitHub, VPS)

> Субагенты-кодеры работают в worktree репозитория ai-platform и не имеют доступа к `~/projects/tronyx-lab/` — миграция выполняется оркестратором по этому runbook. История клона НЕ сохраняется (решение владельца): все 101 коммит + незакоммиченные правки сливаются в один снапшот-коммит.

1. `git -C ~/projects/tronyx-lab/platform add -A && git commit -m "chore: snapshot node-configs/nginx overlays"` — зафиксировать незакоммиченные правки.
2. Squash всей истории в один коммит (rebase --root squash, скриптуемый эквивалент):
   `git reset --soft "$(git rev-list --max-parents=0 HEAD)" && git commit --amend -m "chore: initial overlay snapshot for tronyx-lab (squash of 101 commits)"`.
   Верификация: `git log --oneline | wc -l` = **1**; `git diff <старый-HEAD> HEAD` (сохранённый заранее sha) пуст.
3. `gh repo create TronyxLab/tronyx-lab-overlay --private --description "Context overlay for tronyx-lab (node-configs, projects, hermes-agent)"`.
4. `git remote set-url origin https://github.com/TronyxLab/tronyx-lab-overlay.git && git push -u origin main`.
5. Обновить `node-configs/tronyx-vps/node.yaml#repos.core` → `https://github.com/TronyxLab/tronyx-lab-overlay.git`; закоммитить (уже в overlay-репо — теперь единственный источник).
6. Удалить symlink `~/projects/tronyx-lab/node-configs` (сестринский хак больше не нужен; layout — только `platform/`).
7. VPS: `rm -rf /opt/tronyx-lab/platform` (клон диверджировал от squash-истории, `pull --ff-only` невозможен) → `make node-update NODE=tronyx-vps` (SCP обновлённого node.yaml) → `make converge NODE=tronyx-vps` — `ensure_context_repo` клонирует новый overlay-репо в `/opt/tronyx-lab/platform/`.
8. Следующий `make context-promote CONTEXT=tronyx-lab` валиден: mirror-wipe `<org>/ai-platform` больше не затрагивает контекстное состояние.

**Acceptance:** `TronyxLab/tronyx-lab-overlay` содержит ровно 1 коммит и всё содержимое прежнего `platform/` (сверка `git diff` деревьев до/после); локальный node.yaml и VPS клонируют overlay-репо; `make status`/`make converge` без регрессий; после контрольного `context-promote` контент overlay-репо не изменился (`git -C <overlay> log --oneline | wc -l` = 1, `git status` чист).

> asi-group: `platform/` = полный клон исходника, overlay-структуры нет — миграция по канону TASK-1 выполняется оператором отдельно (вне этого DevPlan, TRAP[DEBT] в TASK-1).

---

## $PARALLEL_GROUPS

### Wave 1 (независимы, без общих файлов — по worktree на задачу)
- Tasks: TASK-1, TASK-2, TASK-3
- Command: Agent Manager, mode=worktree, по одной Coder-сессии на задачу (модель наследуется от текущей сессии — см. §Execution Protocol)

### Wave 2 (зависит от Wave 1: требует слияния веток Wave 1 в одну)
- Tasks: TASK-4
- Command: оркестратор сливает ветки Wave 1 → Coder-сессия в общем worktree

### Post-merge (локально, вне worktree)
- Tasks: TASK-5 — после approve пользователя (вопрос «слить в main?»), до/вместе с merge

**Критический путь:** TASK-1 ∥ TASK-2 ∥ TASK-3 → merge → TASK-4 → question(squash-merge в main) → TASK-5.

---

## Acceptance Criteria (сводка)

| # | Критерий | Проверка |
|---|----------|----------|
| AC1 | Документация фиксирует один layout (Option A) + две роли репо | `grep` docs + визуальная сверка |
| AC2 | `new-context` порождает `platform/{node-configs,modules/hermes-agent,projects}` + один репо `<ctx>-overlay` | unit-тест `test_context_initializer` |
| AC3 | `resolve_org` не читает legacy `context.yaml` | unit-тест `test_context_promoter` (негатив-кейс) |
| AC4 | `make check` зелёный | CI/локаль |
| AC5 | Overlay-репо tronyx-lab = ровно 1 коммит, полный снапшот прежнего состояния | `git log --oneline \| wc -l` = 1; `git diff` пуст |
| AC6 | `repos.core` tronyx-lab → `<ctx>-overlay`; VPS переклонирован; контрольный `context-promote` не задевает overlay | `make converge` + `git status` overlay-репо |

---

## File Manifest

| Файл | Действие | Задача |
|------|----------|--------|
| `AGENTS.md` (root) | Правка: канонический layout + роли репо + TRAP | TASK-1 |
| `core/internal/bootstrap/AGENTS.md` | Правка: таблица артефактов overlay | TASK-1 |
| `core/internal/scaffold/context_initializer.py` | Правка: `create_dirs` / skeleton / `gh_repo_create` / `search_dirs` / `report_summary` | TASK-2 |
| `tests/unit/test_context_initializer.py` | Правка: новый layout + один репо | TASK-2 |
| `core/internal/deploy/context_promoter.py` | Правка: `resolve_org` legacy-путь + TRAP[DECISION] | TASK-3 |
| `tests/unit/test_context_promoter.py` | Правка: resolve_org позитив+негатив | TASK-3 |
| `~/projects/tronyx-lab/platform/`, GitHub, VPS | **Вне репо** — миграция TASK-5 (оркестратор) | TASK-5 |

---

## Design Decisions

### D1 — Слияние = layout + разделение ролей репо, не монорепо
## @rationale
Q: почему не сливаем всё в один git-репозиторий с исходником?
A: Секреты (`*.enc.yaml`) не должны жить в репо, куда пушит `--mirror` из исходника; зеркальный wipe уничтожил бы их. Разделение ролей (`<org>/ai-platform` = CI-зеркало, `<ctx>-overlay` = данные контекста) закрывает wipe-механизм, сохраняя dual-delivery (SCP node-configs + git overlay) без изменения VPS-путей.

### D2 — Имя `platform/` сохранено
## @rationale
Q: почему не `overlay/`?
A: `/opt/<ctx>/platform/` и `platform/context.yaml` зашиты в `ensure_context_repo` (`context_overlay.py:111`), `resolve_org` (`:91`), шаблоны. Переименование = каскад на VPS-пути — нарушает косметический контракт. Фиксируем смысл («контекстный overlay-контейнер»), не имя.

### D3 — Две роли репозитория вместо одной
## @rationale
Q: почему `<ctx>-overlay` отдельный, а не продолжаем жить в `<org>/ai-platform`?
A: `promote_via_ssh` делает безусловный `git push --mirror` — force-update всех refs + удаление отсутствующих в source. Контекстные коммиты tronyx-lab (101 шт., включая «squash reconciliation of 29 context-specific commits») доказывают, что wipe-цикл уже происходил и требовал ручной реконсиляции. Отдельный overlay-репо делает wipe безвредным; `context-promote` не меняется ни строкой.

### D4 — Регистрация: overlay-репо в существующее поле `node_configs_repo`
## @rationale
Q: почему не переименовываем поле реестра в `overlay_repo`?
A: Схема `node.yaml#contexts[]` — `additionalProperties: false` (`domains.py:188`); рантайм-потребителей чтения этих полей нет (только запись `register_context`). Семантика «репо, где живут node-configs» сохраняется (теперь это overlay-репо). Переименование = churn схемы + гейтов без поведенческой выгоды.

### D5 — История tronyx-lab: squash в 1 коммит (решение владельца)
## @rationale
Q: почему squash, а не сохранение 101 коммита?
A: Владелец (2026-09-01): история правок node-configs не нужна — новый репо начинает жизнь с чистого снапшота. Mechanism: `git reset --soft <root>` + `git commit --amend` (эквивалент `rebase --root` + squash всех коммитов, скриптуемо, без интерактива). Незакоммиченные правки включаются в снапшот (шаг 1 TASK-5). Дифф-верификация до/после гарантирует lossless-снапшот.

### D6 — `node-configs` как tracked-подкаталог overlay
## @rationale
Q: почему `.enc.yaml` в overlay-репо допустим?
A: Прецедент tronyx-lab: `platform/node-configs/secrets/*.enc.yaml` уже tracked; age-шифрование (sops), plaintext никогда не коммитится. Отдельный `<ctx>-node-configs` репо упраздняется как top-level сущность.

---

## $TEST_SPEC

| Test file | Test function | Scenario | Module under test |
|-----------|---------------|----------|-------------------|
| `tests/unit/test_context_initializer.py` | `test_create_dirs_nested_layout` | `create_dirs` создаёт `platform/{node-configs,modules/hermes-agent,projects}`, НЕ создаёт сестёр `hermes-agent/`, `node-configs/` | `context_initializer.create_dirs` |
| `tests/unit/test_context_initializer.py` | `test_create_skeleton_node_yaml_nested_path` | skeleton пишется в `platform/node-configs/<node>/node.yaml`; шаблон содержит `repos.core` → `<ctx>-overlay` | `create_skeleton_node_yaml` + `_SKELETON_TEMPLATE` |
| `tests/unit/test_context_initializer.py` | `test_gh_repo_create_single_overlay_repo` | ровно один `gh repo create <ctx>-overlay`; `_git_init_and_push` на `platform/`; return `(repo, None, warnings)` | `gh_repo_create` |
| `tests/unit/test_context_initializer.py` | `test_report_summary_nested_paths` | summary печатает `platform/...`-пути | `report_summary` |
| `tests/unit/test_context_initializer.py` | `test_main_resolves_platform_node_yaml_nested` | `search_dirs` находит `*/platform/node-configs/<node>/node.yaml` | `main()` glob-резолв |
| `tests/unit/test_context_promoter.py` | `test_resolve_org_platform_context_yaml_positive` | org из `platform/context.yaml` (case-sensitive) | `resolve_org` |
| `tests/unit/test_context_promoter.py` | `test_resolve_org_legacy_path_ignored_negative` | `<ctx>/context.yaml` в корне контекста игнорируется (тест падал бы на старом коде) | `resolve_org` |
| `tests/unit/test_context_overlay.py` | (регресс) | контракт `/opt/<ctx>/platform` clone/pull не изменён | `ensure_context_repo` |
| `tests/unit/test_node_yaml_mutation.py` | (регресс) | `register_context` с `node_configs_repo=<overlay>`, пустым `hermes_agent_repo` | `node_yaml.domains` |
| `tests/unit/test_adopt_project_org_validation.py` | (регресс) | org-валидация при layout-резолве | project scaffolder |
| `tests/unit/test_status_collectors.py` | (регресс) | сканирование node-configs не сломано | `status_collectors` |

---

## Debt Intake (Step 0)

**IN_SCOPE (переведено из 01-DEFER):**
- Перегруз `repos.core` ↔ `context-promote` target — разрешается дизайном D3 + TASK-2 (scaffold) + TASK-5 (миграция). Причина перевода: mirror-wipe — активный механизм потери данных, блокирующий Option A, а не фоновый долг.

**DEFER:**
- **Миграция asi-group** — `platform/` = полный клон исходника, overlay-структуры нет; оператор создаёт overlay-репо по канону TASK-1. Rev-условие: следующий деплой в контексте asi-group.
- **Source-репо `ai-platform/node-configs/`** (dev-фикстуры) — остаётся как dev/test-данные; канон зафиксирован в TASK-1 docs.

---

## Execution Protocol

Все задачи Wave 1–2 выполняются **субагентами-кодерами через Agent Manager** в **отдельных worktree**, на **модели текущей сессии** (z-ai/glm-5.3-flash — наследуется по умолчанию, оверрайд не задавать), роль — Coder:

1. **Wave 1** — три параллельные сессии (по одной на TASK-1/2/3), mode=worktree, ветки `feat/022-docs`, `feat/022-scaffold`, `feat/022-resolve-org`. Промпты — из §Next Steps.
2. **Merge Wave 1** — оркестратор сливает три ветки в одну (`feat/022-context-folder-merge`), разрешая конфликты (не ожидается: файлы не пересекаются).
3. **TASK-4** — Coder-сессия (или оркестратор) в общем worktree: `make check` до чистоты + точечные прогоны.
4. **Вопрос о слиянии** — оркестратор спрашивает пользователя (`question` tool): «Слить в main?» Варианты: squash-merge в main / оставить ветку / доработки. **Без явного ответа слияние не выполняется.**
5. **TASK-5** — после merge: миграция tronyx-lab выполняется сессией-оркестратором локально (runbook TASK-5), требует `gh` и SSH-доступа к VPS tronyx-vps.
6. Каждый Coder-промпт завершается `make check TEST_FILE=…` (per-task) + `make agent-check` перед объявлением готовности.

---

## Next Steps

### Wave 1 — запуск сессий (Agent Manager, worktree, модель — текущая)

```
Сессия 1 (TASK-1): Read .ai/plans/022-context-folder-merge/02-DevPlan.md — реализуй TASK-1 (docs: канонический layout + роли репо + TRAP). Верификация: grep-критерии из TASK-1 Acceptance + make agent-check.
Сессия 2 (TASK-2): Read .ai/plans/022-context-folder-merge/02-DevPlan.md — реализуй TASK-2 (scaffold: вложенный layout + один overlay-репо). Верификация: make check TEST_FILE=tests/unit/test_context_initializer.py + make agent-check.
Сессия 3 (TASK-3): Read .ai/plans/022-context-folder-merge/02-DevPlan.md — реализуй TASK-3 (resolve_org: удаление legacy-пути, негатив-тест). Верификация: make check TEST_FILE=tests/unit/test_context_promoter.py + make agent-check.
```

### Wave 2
```
Сессия 4 (TASK-4): в общем worktree после слияния веток Wave 1 — make check до чистоты + точечные прогоны из TASK-4.
```

### Финализация
```
question → «Слить feat/022-context-folder-merge в main?» → squash-merge | оставить | доработать
После merge: TASK-5 (миграция tronyx-lab, runbook выше).
```

---

## Commit Policy (U-83)

Worktree-ветки: по 1 коммиту на задачу (`feat(022): …`). При squash-merge в main — ≤2 коммита на DevPlan:
- `docs(022): 02 DevPlan — context layout + repo role split` (план + docs TASK-1)
- `feat(022): 02 implementation — context folder merge + overlay repo split (scaffold + resolve_org)` (TASK-2/3 + тесты)

$END_DEVPLAN
