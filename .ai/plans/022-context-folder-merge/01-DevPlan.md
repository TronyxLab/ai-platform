$START_DEVPLAN

# DevPlan 022 — Слияние `node-configs/` + `platform/` в единую контекстную папку

## $ARTIFACT_CONTRACT

| Поле | Значение |
|------|----------|
| PURPOSE | Устранить дрейф локальной раскладки контекстного каталога: в каждом контексте два top-level места (`node-configs/` и `platform/`), которые дублируют друг друга и трактуются по-разному (asi-group — сёстры, tronyx-lab — вложенность + symlink). Зафиксировать один канонический layout. |
| DESCRIPTION | Косметическая стандартизация локальной структуры `~/projects/<context>/` вокруг единого контекстного overlay-репозитория (`platform/` = `<org>/ai-platform`, `repos.core`), куда как подкаталоги входят `node-configs/`, `modules/hermes-agent/`, `projects/`, `context.yaml`. Правки: документация, scaffolding (`context_initializer.py`), точечный резолвер `context_promoter.resolve_org`, тесты. Каналы доставки (SCP/git) и VPS-пути не меняются. |
| RATIONALE | Имя `platform` перегружено (исходник платформы vs контекстный overlay), а `node-configs` существует и как отдельный репозиторий, и как подкаталог overlay'а — это источник постоянной путаницы для агентов. Стандартизация по уже выбранному направлению (tronyx-lab) закрывает дрейф без изменения модели деплоя. |
| ACCEPTANCE_CRITERIA | (1) Документация описывает ровно один канонический layout контекста; (2) `make new-context` порождает вложенную структуру под `platform/`; (3) `resolve_org` читает только `platform/context.yaml`; (4) `make check` зелёный; (5) дрейф-векторы (дубли списков проектов / перекрытие context.yaml↔node.yaml) зафиксированы как единственный SoT. |
| IMPLEMENTS | Решение «почему две папки» + слияние в одну (Option A superposition) |
| IMPACTS | `AGENTS.md` (root), `core/internal/bootstrap/AGENTS.md`, `core/internal/scaffold/context_initializer.py`, `core/internal/deploy/context_promoter.py`, `tests/unit/test_context_initializer.py`, `tests/unit/test_context_promoter.py` |
| REQUIRES | Репозиторий ai-platform (source), доступ к `make check`; контекстные каталоги `~/projects/asi-group/`, `~/projects/tronyx-lab/` — только для чтения/верификации (миграция на диске — ручной шаг оператора, вне CI) |

---

## 1. Requirements Analysis — почему две папки и что чиним

### 1.1 Корневая причина (что выяснил анализ)

В каждом контексте фактически **три** репозитория/места, а не два, и они «схлопываются» в две видимые папки:

| Роль | Репозиторий | Канал доставки | Локальный путь (dev) | VPS-путь |
|------|-------------|----------------|----------------------|----------|
| **Исходник платформы** | `Tronyx161/AI-platform` → зеркало `<org>/ai-platform` | git (context-promote `push --mirror`) | `~/projects/ai-platform/` (ЕДИНСТВЕННЫЙ, не дублируется в контекст) | `core/` доставляется SCP → `/opt/platform/core/` |
| **Контекстный overlay** | `<org>/ai-platform` (node.yaml `repos.core`) | git pull (`ensure_context_repo`) | `~/projects/<context>/platform/` | `/opt/<context>/platform/` |
| **Конфиги ноды + секреты** | `<org>/<ctx>-node-configs` | SCP/rsync (core-канал) | `~/projects/<context>/node-configs/` | `/opt/node-configs/` |
| *(hermes-agent overlay)* | `<org>/<ctx>-hermes-agent` | git | `~/projects/<context>/hermes-agent/` | внутри overlay |

Две папки (`node-configs/` + `platform/`) существуют потому, что это **два разных канала и два разных trust-домена**:

1. **`platform/`** — код (контекстный overlay), доставляется **git pull**.
2. **`node-configs/`** — данные + AGE-шифрованные секреты (`*.enc.yaml`), доставляются **SCP push**, приватный репозиторий.

Их нельзя слить в один *репозиторий* наивно: секреты не должны жить в кодовом репозитории. Но их можно слить в один *каталог-контейнер*, где `node-configs/` — подкаталог overlay'а (это уже сделано в tronyx-lab: `.enc.yaml` там **tracked**, секреты age-шифрованы — допустимо).

### 1.2 Наблюдаемый дрейф (корень путаницы)

- **asi-group**: `node-configs/` (сестра) + `platform/` (полный клон исходника `asi-group/AI-platform` с `core/`, `makefiles/`, `tests/`). Имя `platform` = **исходник**.
- **tronyx-lab**: `platform/` = **контекстный overlay** (`context.yaml` + `modules/hermes-agent/` + `node-configs/` + `projects/`), и есть symlink `node-configs -> platform/node-configs`. Имя `platform` = **overlay**.

То есть слово «platform» означает разное, а `node-configs` существует в двух формах (самостоятельный репозиторий И подкаталог overlay'а). Плюс есть системная неоднозначность: `context-promote` пушит исходник в `<org>/ai-platform`, а `ensure_context_repo` клонирует `repos.core` = `<org>/ai-platform` как overlay — **один и тот же репо-нейм для двух ролей**. Это корневой долг, а не то, что чинит косметика (см. §6 Debt Intake).

### 1.3 Дубли/списки, от которых избавляемся (в рамках косметики)

| Дубль | Пара | Канон |
|-------|------|-------|
| Проекты | `node.yaml#projects[]` (операционный SoT) ↔ `platform/projects/*.yaml` (L2-оверрайды) | `projects/*.yaml` несёт **только** `monitoring.*`; `type/domain/expose` живут в `node.yaml` |
| Идентичность контекста | `context.yaml#org/default_node` ↔ `node.yaml#contexts[].name/node.name` | `context.yaml` = org-идентичность (case-sensitive) + образы; `node.yaml` = декларация ноды |
| `node-configs` | source-репо `ai-platform/node-configs/` (dev-фикстуры) ↔ overlay `platform/node-configs/` (канон) | канон = overlay; source-копия — только dev/test |

---

## 2. Superposition — каноническая структура контекста

### Option A — «Контекстный overlay = единый репозиторий-контейнер» [score: 9/10] ← РЕКОМЕНДАЦИЯ

Один top-level каталог `platform/` на контекст = overlay-репозиторий `<org>/ai-platform` (`repos.core`), всё контекстное — подкаталоги:

```
~/projects/<context>/
└── platform/                        # <org>/ai-platform — контекстный overlay (repos.core)
    ├── context.yaml                 # org (case-sensitive), default_node, образы — SoT org-идентичности
    ├── modules/
    │   └── hermes-agent/            # hermes-agent overlay (config.yaml, profiles, skills)
    ├── projects/                    # L2-оверрайды мониторинга (только monitoring.*)
    ├── node-configs/                # КАНОН: node.yaml + overlays + secrets (.enc.yaml)
    │   ├── <node>/node.yaml
    │   ├── <node>/overlays/nginx/*.conf
    │   ├── <node>/overlays/tor/
    │   └── secrets/<node>.enc.yaml
    └── .github/workflows/deploy.yml # контекстный CI (уже есть в tronyx-lab)
```

- **Плюсы:** буквально одна папка; один репозиторий = один `git clone` на VPS уже реализован (`ensure_context_repo`); совпадает с tronyx-lab — стандартизируемся по уже выбранному направлению; убирает symlink-хак и сестринский сплит.
- **Минусы:** миграция asi-group (ручной шаг оператора); `node-configs` становится tracked-подкаталогом (секреты age-шифрованы — допустимо, прецедент tronyx-lab).
- **Best when:** нужен именно косметический слом дрейфа без изменения каналов доставки.

### Option B — «Две папки + корневой манифест» [score: 4/10]

Оставить `platform/` (клон исходника) и `node-configs/` сёстрами, добавить `context.yaml` в корень `~/projects/<context>/`.

- **Плюсы:** минимальные правки.
- **Минусы:** **не выполняет цель** «слить в одну папку»; `platform` остаётся перегруженным (исходник vs overlay); плодит третий top-level файл.
- **Rejected:** противоречит явной цели задачи.

### Option C — «Монорепо контекста с подмодулем исходника» [score: 3/10]

Один репозиторий `<org>/<context>` с `platform-source/` как git submodule + `node-configs/` + `hermes-agent/`.

- **Плюсы:** строго один репозиторий.
- **Минусы:** submodule + пересборка context-promote/CI — **не косметика**, ломает модель `push --mirror`.
- **Rejected:** вне скоупа.

### Recommendation: Option A — AUTO-COLLAPSE (режим автономный)

> **Авто-коллапс в Option A (score 9/10)** — автономный режим планирования. Переопределить: ответом с именем опции (`B`/`C`) до начала реализации.

**Выбор имени `platform/` сохранён намеренно** (не переименовываем в `overlay/`): VPS-путь `/opt/<ctx>/platform/` зашит в `ensure_context_repo`, `deploy_orchestrator` (overlay-резолв), `context_promoter.resolve_org`, шаблон README — переименование = full-refactor, нарушает «косметический» контракт. Вместо этого фиксируем **смысл**: `platform/` = «контекстный overlay-репозиторий», не клон исходника.

---

## 3. Draft Code Graph + Data Flow

```
make new-context NODE=<n>
  → context-init.sh → context_initializer.py
      create_dirs()      ──►  ~/projects/<ctx>/platform/{node-configs,modules/hermes-agent,projects}
      create_skeleton_node_yaml() ──► platform/node-configs/<node>/node.yaml
      gh_repo_create()   ──►  git init в platform/ (единственный overlay-репозиторий)
      register_in_platform_yaml() ──► node.yaml (source) contexts[].name

make context-promote CONTEXT=<ctx>
  → context_promoter.resolve_org() ──► org из ~/projects/<ctx>/platform/context.yaml#org  (legacy-путь удалён)

VPS (не меняется): ensure_context_repo → git clone repos.core → /opt/<ctx>/platform/
```

**Изменения потока:** только локальный scaffold + один кандидат-путь в `resolve_org`. VPS-сторона, `ensure_context_repo`, SCP-канал node-configs, CI — **не трогаются**.

---

## $TASKS

### TASK-1 — Документация канонической структуры контекста
**Владелец:** Coder · **Сложность:** 3/10 · **Файлы:** `AGENTS.md` (root), `core/internal/bootstrap/AGENTS.md`

- Добавить в root `AGENTS.md` секцию «Каноническая структура контекстной папки» с деревом Option A и таблицей «почему две папки» (§1.1).
- Обновить строку `| <context>/ | Служебная папка контекста (node-configs, hermes-agent)…` → новое описание (overlay-контейнер `platform/`).
- В `core/internal/bootstrap/AGENTS.md` таблицу артефактов: `/opt/<context>/platform/` — описать содержимое overlay'а (`context.yaml` + `modules/` + `node-configs/` + `projects/`).
- Зафиксировать единственный SoT дублей (§1.3): `projects/*.yaml` = только `monitoring.*`; `context.yaml` ↔ `node.yaml` — непересекающиеся поля.
- TRAP[DECISION] (слияние, Option A) + TRAP[DEBT] (перегруз `repos.core` ↔ `context-promote` target) + TRAP[BUSINESS] при наличии акцента владельца.

**Acceptance:** `grep -rn "node-configs, hermes-agent\|(node-configs"` в docs не находит устаревшего описания сестринской раскладки; секция канонического дерева присутствует; TRAP-комментарии на месте.

### TASK-2 — Scaffold порождает вложенный layout
**Владелец:** Coder · **Сложность:** 4/10 · **Файлы:** `core/internal/scaffold/context_initializer.py`, `tests/unit/test_context_initializer.py`

- `create_dirs()`: вместо сестёр `hermes-agent/` + `node-configs/` создавать `platform/node-configs/`, `platform/modules/hermes-agent/`, `platform/projects/`.
- `create_skeleton_node_yaml()`: путь `platform/node-configs/<node>/node.yaml` (сейчас `node-configs/node.yaml`).
- `report_summary()`: обновить печатаемые пути.
- Обновить unit-тесты на новый layout.

**Acceptance:** `make check TEST_FILE=tests/unit/test_context_initializer.py` зелёный; тест `create_dirs` проверяет существование `platform/node-configs/`, `platform/modules/hermes-agent/`, `platform/projects/`; skeleton пишется в `platform/node-configs/node.yaml`.

### TASK-3 — resolve_org: убрать legacy-путь context.yaml
**Владелец:** Coder · **Сложность:** 2/10 · **Файлы:** `core/internal/deploy/context_promoter.py`, `tests/unit/test_context_promoter.py`

- В `resolve_org()` удалить кандидат `Path(base) / context / "context.yaml"` (строка 92) — остаётся только `platform/context.yaml`.
- Обновить docstring (STRUCTURE + @invariants) и unit-тест `test_resolve_org_*`.

**Acceptance:** `make check TEST_FILE=tests/unit/test_context_promoter.py` зелёный; org резолвится исключительно из `platform/context.yaml`; падение на отсутствие legacy-пути покрыто тестом.

### TASK-4 — Верификация полного цикла
**Владелец:** Coder · **Сложность:** 2/10 · **Файлы:** нет новых (прогон)

- `make check` до чистоты (батч всех ошибок).
- Точечные прогоны: `test_context_initializer.py`, `test_context_promoter.py`, `test_adopt_project_org_validation.py`, `test_status_collectors.py` (регресс-стража layout-резолва).

**Acceptance:** `make check` exit 0; затрагиваемые unit-тесты зелёные; pre-push hook (quick check) не блокирует.

---

## $PARALLEL_GROUPS

### Wave 1 (независимы, без общих файлов)
- Tasks: TASK-1, TASK-2, TASK-3
- Command: `coder Read .ai/plans/022-context-folder-merge/01-DevPlan.md, implement Wave 1: TASK-1, TASK-2, TASK-3`

### Wave 2 (зависит от Wave 1)
- Tasks: TASK-4
- Command: `coder Read .ai/plans/022-context-folder-merge/01-DevPlan.md, implement Wave 2: TASK-4`

**Критический путь:** TASK-1 ∥ TASK-2 ∥ TASK-3 → TASK-4.

---

## Acceptance Criteria (сводка)

| # | Критерий | Проверка |
|---|----------|----------|
| AC1 | Документация фиксирует один канонический layout (Option A) | `grep` docs + визуальная сверка секции |
| AC2 | `make new-context` порождает `platform/{node-configs,modules/hermes-agent,projects}` | unit-тест `test_context_initializer` |
| AC3 | `resolve_org` не читает legacy `context.yaml` | unit-тест `test_context_promoter` |
| AC4 | `make check` зелёный | CI/lokаль |
| AC5 | Дубли списков (§1.3) зафиксированы как единственный SoT | TRAP[DECISION] + docs |

---

## File Manifest

| Файл | Действие |
|------|----------|
| `AGENTS.md` (root) | Правка: каноническая структура + почему-две-папки + TRAP |
| `core/internal/bootstrap/AGENTS.md` | Правка: таблица артефактов overlay |
| `core/internal/scaffold/context_initializer.py` | Правка: `create_dirs` / skeleton path / `report_summary` |
| `core/internal/deploy/context_promoter.py` | Правка: `resolve_org` legacy-путь |
| `tests/unit/test_context_initializer.py` | Правка: новый layout |
| `tests/unit/test_context_promoter.py` | Правка: resolve_org |
| `~/projects/asi-group/`, `~/projects/tronyx-lab/` | **Вне репо** — ручная миграция оператора (не в скоупе кода) |

---

## Design Decisions

### D1 — Слияние = layout, не репозитории
## @rationale
Q: почему не сливаем в один git-репозиторий, а только в один каталог?
A: `node-configs` несёт AGE-шифрованные секреты и доставляется SCP (core-канал), overlay — git pull. Это разные trust-домены с разной политикой write. Слияние репозиториев потребовало бы пересборки dual-delivery и выноса секретов — full-refactor, противоречит «косметическому» контракту. Каталог-контейнер `platform/` объединяет **размещение**, не владение.

### D2 — Имя `platform/` сохранено
## @rationale
Q: почему не `overlay/`?
A: `/opt/<ctx>/platform/` и `platform/context.yaml` зашиты в `ensure_context_repo`, `deploy_orchestrator`, `resolve_org`, шаблон. Переименование = каскад на VPS-пути и CI — нарушает cosmetic-границу. Фиксируем смысл («контекстный overlay»), не имя.

### D3 — `node-configs` как tracked-подкаталог overlay
## @rationale
Q: почему `.enc.yaml` в overlay-репо допустим?
A: прецедент tronyx-lab: `platform/node-configs/secrets/*.enc.yaml` уже tracked; файлы age-шифрованы (sops), plaintext никогда не коммитится. Это устраняет отдельный `<org>/<ctx>-node-configs` репозиторий как *top-level* сущность, сохраняя секретность.

---

## $TEST_SPEC

| Test file | Test function | Scenario | Module under test |
|-----------|---------------|----------|-------------------|
| `tests/unit/test_context_initializer.py` | `test_create_dirs_*` | `create_dirs` создаёт вложенный `platform/{node-configs,modules/hermes-agent,projects}` | `context_initializer.create_dirs` |
| `tests/unit/test_context_initializer.py` | `test_create_skeleton_node_yaml_*` | skeleton пишется в `platform/node-configs/<node>/node.yaml` | `context_initializer.create_skeleton_node_yaml` |
| `tests/unit/test_context_initializer.py` | `test_report_summary_*` | summary печатает новые пути | `context_initializer.report_summary` |
| `tests/unit/test_context_promoter.py` | `test_resolve_org_*` | org из `platform/context.yaml`; legacy-путь удалён (negative) | `context_promoter.resolve_org` |
| `tests/unit/test_adopt_project_org_validation.py` | (регресс) | layout-резолв `*/node-configs/*/node.yaml` не сломан | `project_scaffolder` / org-валидация |
| `tests/unit/test_status_collectors.py` | (регресс) | сканирование node-configs не сломан | `status_collectors` |

---

## Debt Intake (Step 0)

**IN_SCOPE:** дрейф локальной раскладки (asi-group ↔ tronyx-lab) — закрывается TASK-1..3.

**DEFER (зафиксировано TRAP[DEBT] в TASK-1):**
- **Перегруз `repos.core` ↔ `context-promote` target**: `node.yaml#repos.core` = `<org>/ai-platform` (overlay для `ensure_context_repo`), а `context-promote` пушит **исходник** в тот же `<org>/ai-platform`. Один репо-нейм для двух ролей. Rev-условие: ввести явное разделение `<org>/ai-platform` (source mirror / Context CI) vs `<org>/<ctx>-overlay` (context overlay) — это уже НЕ косметика, отдельный план.

---

## Next Steps

### Wave 1
```
coder Read .ai/plans/022-context-folder-merge/01-DevPlan.md, implement Wave 1: TASK-1, TASK-2, TASK-3
```

### Wave 2
```
coder Read .ai/plans/022-context-folder-merge/01-DevPlan.md, implement Wave 2: TASK-4
```

---

## Commit Policy (U-83)

- `docs(022): 01 DevPlan — context-folder-merge (документация канонического layout)` — если только docs.
- `feat(022): 01 implementation — context folder merge (scaffold + resolve_org)` — код + тесты.

$END_DEVPLAN
