$START_DEVPLAN

# DevPlan 036C — Wave 5c: Strangler-Fig adopt-project.sh (906 LOC) → project_adopter.py

$ARTIFACT_CONTRACT
- **PURPOSE:** Strangler-Fig декомпозиция adopt-project.sh (906 LOC) в Python-модуль `project_adopter.py` (~500 LOC) с shell-фасадом ≤150 LOC, 0 inline python3.
- **DESCRIPTION:** adopt-project.sh — wizard адаптации существующих проектов в ai-platform lifecycle. Генерирует ai-platform.yaml, упрощает deploy.yml, валидирует compose-сети, регистрирует проект в node.yaml, настраивает vhost. Из 906 LOC ~600 — бизнес-логика, подлежащая извлечению в Python. 2 inline python3-блока (validate_compose_networks — оба уровня: docker compose config fallback + JSON-анализ). Shell-фасад оставляет parse_args (с авто-детекцией org/name/domain из ai-platform.yaml) + вызов Python-модуля + exit.
- **RATIONALE:** Выполнение языковой политики (AGENTS.md: новый код — Python), устранение 2 inline python3-блоков, дедупликация с project_registry.py (уже существует — используется register_in_node_yaml) и gen_env_platform.py (уже существует — gen_env_platform), повышение тестируемости compose-валидации (самая сложная функция: 140 LOC с docker/py3 fallback).
- **ACCEPTANCE_CRITERIA:**
  - AC-1: Shell-фасад adopt-project.sh ≤150 LOC (parse_args + вызов Python + exit)
  - AC-2: 0 inline `python3 -c` / `<<PYEOF` в shell-фасаде
  - AC-3: `make adopt-project DIR=<test>` работает идентично shell-версии
  - AC-4: Unit-тесты в `tests/unit/test_project_adopter.py` — ≥7 тестов, ≥80% coverage project_adopter.py
  - AC-5: Все существующие тесты зелёные (`make test`)
  - AC-6: `make gate MODE=fast` зелёный
  - AC-7: Все TRAP из adopt-project.sh перенесены в Python-модуль как docstring-комментарии
- **IMPLEMENTS:** TASK-036C из мастер DevPlan 036 (Wave 5 Strangler-Fig shell-монолитов)
- **IMPACTS:**
  - `core/internal/scaffold/adopt-project.sh` — 906 → ~150 LOC (shell-фасад)
  - `core/internal/scaffold/project_adopter.py` — NEW ~500 LOC (бизнес-логика adopt)
  - `tests/unit/test_project_adopter.py` — NEW ~300 LOC (unit-тесты)
  - Не затрагивает: `core/internal/scaffold/gen-env-platform.sh` / `gen_env_platform.py` (уже извлечён), `core/internal/shared/project_registry.py` (уже существует)
- **REQUIRES:**
  - Python ≥3.10, `pytest`, `pyyaml` (уже в проекте)
  - `core/internal/shared/project_registry.py` (DevPlan 070 — DRIFT-B5, уже существует)
  - `core/internal/scaffold/gen_env_platform.py` (Plan 082, уже существует)
   - `core/internal/scaffold/vhost_renderer.py` — **TASK-036B (DevPlan 036B, .ai/plans/036-wave5b-vhost/01-DevPlan.md)**: предоставляет vhost_renderer.py для configure_vhost(). project_adopter.py должен вызывать vhost_renderer через Python API (не subprocess). **ЗАВИСИТ (c fallback — см. D4)**: TASK-036C может стартовать параллельно через subprocess add-vhost.sh; прямой import vhost_renderer требует завершения TASK-036B.
  - `core/internal/scaffold/vhost_yaml_reader.py` (уже существует — используется для чтения node.yaml проектов)
$END_ARTIFACT_CONTRACT

---

## Debt Intake

### TRAP-аудит adopt-project.sh (2 TRAP)

| # | TRAP | Строка | Статус |
|---|------|--------|--------|
| D1 | TRAP[DECISION] local parse_args — env auto-detection logic too complex for full parse_args adoption | 60 | **IN_SCOPE:** parse_args остаётся в shell-фасаде. Python-модуль получает уже разрешённые значения. TRAP переносится в docstring project_adopter.py. |
| B1 | TRAP[BUG] молчаливый дефолт "personal" + отсутствие casing-нормализации → конфиг-drift dance-site | 138 | **IN_SCOPE:** Бизнес-логика валидации org (validate_org_against_node_yaml) извлекается в Python. Оригинальный баг уже исправлен (fail-fast exit 1 + lowercase + сверка с node.yaml). TRAP переносится в docstring как историческая справка. |

### DEBT-регистры из других волн

| DEBT | Источник | Статус |
|------|----------|--------|
| D3 (конфиг-drift dance-site) | `.ai/plans/007-dance-site-launch/02-Debt.md` | **DEFER:** Связан с B1 — уже исправлен. Документирован как исторический контекст. |
| DRIFT-B5 (3 heredoc → project_registry.py) | Brief 077 / DevPlan 070 | **RESOLVED:** project_registry.py уже существует. adopt-project.sh использует его через shell-вызов — после миграции вызов станет прямым Python-импортом. |

### Вывод

Инвентаризация TRAP/DEBT завершена. Все TRAP в adopt-project.sh — IN_SCOPE (переносятся в Python). Никаких новых DEBT не обнаружено.

---

## Requirements Analysis

### Ключевые критерии успеха

1. **Zero inline python3:** Устранить 2 блока inline `python3 -c` / `python3 - <<PYEOF` в validate_compose_networks. Самый сложный блок: 57 строк анализа JSON-структуры compose с проверкой proxy-net external + service connections.
2. **Shell-фасад ≤150 LOC:** parse_args (авто-детекция из ai-platform.yaml + path derivation) + вызов Python + exit.
3. **Дедупликация:** `register_in_node_yaml` уже делегирует `project_registry.py` через subprocess — миграция должна перевести на прямой import. `gen_env_platform` уже имеет Python-модуль gen_env_platform.py — shell-фасад оставляет вызов через subprocess (модуль спроектирован как CLI, не как библиотека).
4. **Сохранение обратной совместимости:** Все существующие пути вызова (`make adopt-project`, `scaffold.sh adopt-project`) работают идентично.
5. **Unit-тесты для compose-валидации:** Самая сложная функция (validate_compose_networks: 140 LOC, 3 метода парсинга, 2 fallback-ветки) должна быть покрыта минимум 4 тестами.

### Текущее состояние (baseline)

| Метрика | Значение |
|---------|----------|
| LOC adopt-project.sh | 906 |
| Inline python3 блоков | 2 (в validate_compose_networks) |
| Функций бизнес-логики | 12 (подлежат извлечению) |
| Функций оркестрации | 2 (parse_args, main — остаются в shell) |
| Использует внешние модули | project_registry.py (subprocess), gen-env-platform.sh (subprocess) |
| Вызывается из | `core/entrypoints/scaffold.sh` → `exec adopt-project.sh "$@"` |

---

## Superposition Analysis

### 4 опции для миграции adopt-project

#### Option A: Полный Strangler-Fig — все функции в Python [score: 9/10] ⭐

**Подход:** Shell-фасад (~120 LOC): parse_args → validate_org → вызов Python. Python-модуль `project_adopter.py` (~500 LOC): все 10 бизнес-функций. `configure_vhost` вызывает vhost_renderer.py через Python API (прямой import). `register_in_node_yaml` — прямой import project_registry. `gen_env_platform` — остаётся subprocess (CLI-only модуль).

**Trade-offs:**
- ➕ Полное устранение inline python3, shell ≤150 LOC
- ➕ Максимальная тестируемость
- ➕ Дедупликация с project_registry (прямой import вместо subprocess)
- ➕ Соответствие языковой политике
- ➖ Зависимость от TASK-036B (vhost_renderer API должен быть стабилен)
- ➖ gen_env_platform остаётся subprocess (CLI-only дизайн — осознанное решение)

**Best when:** vhost_renderer.py готов и имеет стабильный Python API.

#### Option B: Извлечение template-генерации + compose-валидации, остальное shell [score: 6/10]

**Подход:** Только generate_minimal_ai_platform_yaml + simplify_deploy_yml + validate_compose_networks + gen_project_makefile + gen_project_agents → Python. register_in_node_yaml, configure_vhost, validate_org_against_node_yaml → остаются в shell.

**Trade-offs:**
- ➖ 3 функции бизнес-логики всё ещё в shell — языковая политика нарушена частично
- ➖ register_in_node_yaml остаётся с subprocess-вызовом project_registry.py
- ➕ Меньше зависимость от TASK-036B

**Rejected:** неполная миграция оставляет shell с бизнес-логикой (>150 LOC).

#### Option C: compose-валидация только [score: 3/10]

**Подход:** Только validate_compose_networks → Python (устраняет самый большой inline python3 блок).

**Trade-offs:**
- ➖ 906 LOC shell остаётся практически нетронутым
- ➖ Языковая политика не выполнена
- ➖ Не решает проблему тестируемости остальных функций

**Rejected:** слишком консервативно, не решает задачу.

#### Option D: Оставить как есть + документировать TRAP [score: 2/10]

**Подход:** Ничего не менять, только добавить TRAP-комментарий о решении.

**Trade-offs:**
- ➖ Языковая политика нарушена
- ➖ Inline python3 остаётся
- ➖ Тестируемость нулевая

**Rejected:** противоречит целям Wave 5.

### Scoring Matrix

| Dimension | A (Full SF) | B (Partial) | C (Compose-only) | D (No-op) |
|-----------|:---:|:---:|:---:|:---:|
| Lang policy compliance | 9 | 5 | 3 | 1 |
| Testability gain | 9 | 5 | 3 | 0 |
| Risk to production | 8 | 9 | 10 | 10 |
| Implementation speed | 7 | 8 | 9 | 10 |
| De-duplication | 9 | 5 | 2 | 0 |
| **Composite** | **8.4** | **6.4** | **5.4** | **4.2** |

### Recommendation: Option A — Full Strangler-Fig (score: 8.4)

**Обоснование:**
1. **Wave 4 precedent:** Аналогичная миграция уже выполнена для top-3 скриптов (4114→392 LOC). Процесс отлажен.
2. **project_registry.py уже существует:** Прямой import вместо subprocess — тривиальное изменение, дающее дедупликацию.
3. **gen_env_platform.py — CLI-first дизайн:** Оставляем subprocess-вызов — модуль спроектирован как CLI, имеет `sys.exit()` внутри функций. Переписывание на библиотечный API — out of scope для этой волны.
4. **Зависимость от TASK-036B:** Блокирующая, но vhost_renderer.py должен предоставить Python API для вызова configure_vhost. Если vhost_renderer.py ещё не готов — configure_vhost остаётся в shell-фасаде как fallback subprocess-вызов add-vhost.sh. См. Design Decision D4.

---

## Architecture Overview

### Configuration DRY Audit

Перед проектированием проверены конфигурационные файлы на дублирование:

| Конфиг-значение | adopt-project.sh | compose файлы | CI workflow | .env.platform |
|-----------------|:---:|:---:|:---:|:---:|
| `ghcr.io` registry | L267, L296 | compose | deploy.yml template | — |
| `COMPOSE_PROFILES` | L388 (hardcoded) | — | — | — |
| `proxy-net` name | L441, L461 | compose | — | — |
| `IMAGE_NAME` template | L279 | — | deploy.yml | — |

**Находка:** `COMPOSE_PROFILES` (строка 388) хардкожена в adopt-project.sh для `docker compose config`. Это значение уже определено в Makefile (`make _get_all_profiles`). При миграции в Python — использовать `os.environ.get("COMPOSE_PROFILES", default_profiles)` с fallback-значением из platform-env.yaml. Не дублировать хардкод.

**Решение:** В Python-модуле `validate_compose_networks()` читает `COMPOSE_PROFILES` из env или использует разумный default из platform-env.yaml. Хардкод удаляется.

### DUAL_MECHANISM_DETECTION

Перед проектированием проверено: нет дублирующих механизмов в project_adopter.

- `register_in_node_yaml` → **уже делегирует project_registry.py** (единственный механизм регистрации). Переход на прямой import — конвергенция к одному механизму.
- `gen_env_platform` → единственный механизм генерации .env.platform. CLI-first дизайн.
- `configure_vhost` → единственный механизм (add-vhost.sh / vhost_renderer.py).

**Вывод:** Двойных механизмов нет. Миграция конвергирует register_in_node_yaml с project_registry (subprocess → import).

### KNOWLEDGE_DEDUP

| Знание | Дублируется в | Действие |
|--------|--------------|----------|
| `proxy-net` external validation logic | adopt-project.sh + add-project.sh (cosmetic) | Извлекается в project_adopter.py как единственный source |
| `ghcr.io/<org>/<project>` template | adopt-project.sh + deploy.yml template | Остаётся дублированием между CI-шаблоном и adopt-скриптом (разные контексты) |
| `COMPOSE_PROFILES` | adopt-project.sh:388 + Makefile | Устраняется — Python читает из env/platform-env.yaml |
| `node.yaml` path: `projects/<org>/node-configs/<node>/node.yaml` | adopt-project.sh + add-vhost.sh + add-project.sh + remove-project.sh | **📝 TRAP[DEBT]:** Кандидат на shared `NodeConfigPathResolver` (отдельный DevPlan). Не в scope этой волны. |

---

## Step-by-Step Data Flow

### ДО (906 LOC shell)

```
adopt-project.sh (906 LOC)
├── parse_args() ── парсинг --dir/--name/--org/--node/--domain/--force + авто-детекция
├── validate_org_against_node_yaml() ── сверка org с node.yaml context (fail-fast)
├── main():
│   ├── generate_minimal_ai_platform_yaml() ── heredoc YAML-генерация (~45 LOC)
│   ├── simplify_deploy_yml() ── heredoc CI-шаблон (~100 LOC) + интерактивный prompt
│   ├── delete_platform_deploy_yml() ── rm -f (тривиально)
│   ├── gen_env_platform() ── subprocess: gen-env-platform.sh
│   ├── gen_project_makefile() ── heredoc Makefile-шаблон (~25 LOC)
│   ├── gen_project_agents() ── heredoc AGENTS.md-шаблон (~30 LOC)
│   ├── validate_compose_networks() ── 2 inline python3-блока (140 LOC total):
│   │   ├── Метод 1: docker compose config (55 LOC)
│   │   ├── Метод 2: python3 yaml fallback (15 LOC)
│   │   └── Анализ: python3 JSON-парсинг proxy-net + services (57 LOC)
│   ├── register_in_node_yaml() ── yq ИЛИ subprocess project_registry.py
│   ├── configure_vhost() ── subprocess add-vhost.sh + sed ai-platform.yaml
│   └── print_diff_report() ── printf (тривиально)
```

### ПОСЛЕ: shell-фасад (~120 LOC) + project_adopter.py (~500 LOC)

```
adopt-project.sh (~120 LOC, shell-фасад)
├── source lib/logging.sh, lib/args.sh, lib/python_deps.sh
├── parse_args() ── парсинг CLI + авто-детекция из ai-platform.yaml (СОХРАНЯЕТСЯ в shell per D1)
├── validate_org_against_node_yaml() ── grep-based быстрая проверка в shell (опционально, дублируется в Python)
│   └── FAIL-FAST если org не совпадает → exit 1 до вызова Python
└── python3 -m core.internal.scaffold.project_adopter adopt \
      --project-dir "$PROJECT_DIR" \
      --project-name "$PROJECT_NAME" \
      --project-org "$PROJECT_ORG" \
      --project-node "$PROJECT_NODE" \
      ${PROJECT_DOMAIN:+--project-domain "$PROJECT_DOMAIN"} \
      ${FORCE:+--force} \
    && exit 0 || exit $?

core/internal/scaffold/project_adopter.py (~500 LOC)
├── class ProjectAdopter:
│   ├── __init__(project_dir, name, org, node, domain, force)
│   ├── adopt() → AdoptionResult ── оркестрация всех шагов
│   ├── generate_minimal_ai_platform_yaml() → Path ── YAML-генерация (PyYAML dump)
│   ├── simplify_deploy_yml() → bool ── CI workflow rewriting (string template)
│   ├── delete_platform_deploy_yml() → bool ── удаление deprecated файла
│   ├── gen_env_platform() → bool ── subprocess.run gen_env_platform.py CLI
│   ├── gen_project_makefile() → Path ── Makefile-генерация
│   ├── gen_project_agents() → Path ── AGENTS.md-генерация
│   ├── validate_compose_networks(compose_path) → ValidationResult ── compose-анализ
│   │   ├── _parse_compose_docker(compose_path) → dict | None ── docker compose config
│   │   ├── _parse_compose_pyyaml(compose_path) → dict ── PyYAML fallback
│   │   └── _analyze_proxy_net(data) → (bool, int) ── анализ proxy-net + services
│   ├── register_in_node_yaml() → bool ── прямой import project_registry.register_project()
│   ├── configure_vhost() → bool ── вызов vhost_renderer.py API (ИЛИ subprocess add-vhost.sh)
│   └── print_diff_report(changes) → None ── форматированный вывод
├── validate_org_against_node_yaml(org, node_yaml_path) → str ── полная Python-версия
│   └── Возвращает канонический org (с правильным casing) или raise на mismatch
└── CLI: argparse subcommand "adopt"
```

### Уровень shell-фасада: только parse_args + validate_org (fast-fail) + вызов Python

```
adopt-project.sh
  1. parse_args → заполняет PROJECT_DIR, PROJECT_NAME, PROJECT_ORG, PROJECT_NODE,
                   PROJECT_DOMAIN, FORCE
     - Авто-детекция name из basename dir
     - Авто-детекция node/domain из ai-platform.yaml (grep-based)
     - Derive org из path: projects/<org>/<project>/
     - Применение env defaults: PLATFORM_ORG, PLATFORM_DEFAULT_NODE
     - FAIL-FAST если PROJECT_ORG пуст (TRAP[BUG] B1 fix)
  2. validate_org_against_node_yaml (grep-based, shell) → FAIL-FAST or update PROJECT_ORG casing
  3. python3 -m core.internal.scaffold.project_adopter adopt ... (все аргументы)
  4. exit с кодом из Python
```

---

## Draft Code Graph

```
core/internal/scaffold/
├── adopt-project.sh                # MODIFIED: 906 → ~120 LOC (shell facade)
├── project_adopter.py              # NEW: ~500 LOC
├── gen_env_platform.py             # EXISTS: subprocess target (не изменяется)
├── vhost_renderer.py               # NEW (TASK-036B): Python API для configure_vhost
├── vhost_yaml_reader.py            # EXISTS: чтение node.yaml проектов
├── add-vhost.sh                    # MODIFIED (TASK-036B): → ~150 LOC
├── gen-env-platform.sh             # EXISTS: shell-фасад (не изменяется)
└── context_registry.py             # EXISTS: не затронут

core/internal/shared/
└── project_registry.py             # EXISTS: прямой import из project_adopter

tests/unit/
└── test_project_adopter.py         # NEW: ~300 LOC
```

### Контракты (Contract Formalization)

#### project_adopter.py → project_registry.py (import)

```python
# Контракт: прямой вызов register_project() вместо subprocess
from core.internal.shared.project_registry import register_project

register_project(
    name="my-project",
    repo="my-org/my-project",
    project_type="adopted",
    node_yaml_path="/path/to/node.yaml",
    domain="example.com",     # optional
    log_prefix="adopt"
)
# Возвращает: None (всегда exits via sys.exit)
# ⚠️ ВАЖНО: register_project вызывает sys.exit(0) при успехе/skip.
# Для использования как библиотека нужно обернуть в try/except SystemExit.
```

#### project_adopter.py → vhost_renderer.py (import)

```python
# Контракт: вызов vhost_renderer API (зависит от TASK-036B)
# Если vhost_renderer НЕ готов → fallback на subprocess add-vhost.sh
from core.internal.scaffold.vhost_renderer import configure_vhost_for_project

configure_vhost_for_project(
    project_dir=Path("/projects/my-org/my-project"),
    domain="example.com",
    node_configs_dir=Path("/projects/my-org/node-configs"),
)
# Возвращает: bool (True если vhost настроен)
```

#### project_adopter.py → gen_env_platform.py (subprocess)

```python
# Контракт: CLI-first дизайн gen_env_platform.py
# Функции модуля используют sys.exit() внутри → НЕЛЬЗЯ импортировать как библиотеку.
# Используется subprocess.run
import subprocess

result = subprocess.run([
    "python3", "-m", "core.internal.scaffold.gen_env_platform",
    "--yaml", str(platform_env_yaml),
    "--name", project_name,
    "--domain", domain,
], capture_output=True, text=True)
# stdout → содержимое .env.platform (пишется в файл)
```

---

## Design Decisions

### ## @rationale D1: parse_args остаётся в shell-фасаде

**Q:** Почему parse_args не извлекается в Python, если мы следуем полному Strangler-Fig?

**A:** parse_args содержит сложную логику авто-детекции, которая зависит от shell-контекста:
1. `basename "$PROJECT_DIR"` — авто-детекция имени проекта
2. `grep` + `awk` парсинг ai-platform.yaml для извлечения target_node, domain — зависит от форматирования YAML
3. Path derivation org: `basename "$(dirname "$(cd "$PROJECT_DIR" && pwd -P)")"` — навигация по файловой системе
4. ENV-переменные: `PLATFORM_ORG`, `PLATFORM_DEFAULT_NODE` — shell-окружение

Перенос в Python потребовал бы репликации всех этих shell-специфичных операций (subprocess-вызовы для pwd, чтение env, grep-парсинг YAML), что не дало бы прироста в тестируемости при удвоении сложности. TRAP[DECISION] в строке 60 уже документирует это решение.

**Python-модуль получает уже разрешённые значения** — контракт: все поля обязательны и валидированы.

### ## @rationale D2: validate_compose_networks — извлечение с сохранением двухуровневого fallback

**Q:** Зачем сохранять оба метода парсинга (docker compose config + PyYAML)?

**A:**
1. **`docker compose config`** — разрешает anchors (`&ref` / `*ref`), aliases, `extends`, переменные окружения. PyYAML не делает этого.
2. **PyYAML fallback** — работает без Docker daemon (CI, macOS без Docker Desktop).
3. **Best-effort:** Если ни один метод не доступен → WARN + skip (return 0). Это сохраняет обратную совместимость — adopt-project не должен требовать Docker для работы.

В Python-модуле методы изолированы в `_parse_compose_docker()` и `_parse_compose_pyyaml()` для тестирования каждого отдельно.

### ## @rationale D3: register_in_node_yaml — прямой import project_registry вместо subprocess

**Q:** Почему не оставить subprocess-вызов project_registry.py?

**A:** project_registry.py уже существует и содержит чистую Python-логику. Subprocess-вызов создаёт ненужный overhead (запуск интерпретатора, сериализация аргументов через CLI, парсинг вывода). Прямой import:
- Устраняет 1 subprocess-вызов
- Даёт немедленную обратную связь (исключения вместо парсинга exit code)
- Дедуплицирует логику

**Ограничение:** `register_project()` вызывает `sys.exit(0)` при успехе — нужно обернуть вызов в `try/except SystemExit`. Альтернатива: рефакторинг `register_project` на return вместо exit (out of scope для этой волны — отдельная задача по переходу project_registry с CLI-only на библиотечный API).

**Решение:** В project_adopter.py — обёртка `_register_project_safe()` с try/except SystemExit.

### ## @rationale D4: configure_vhost — абстракция с fallback на subprocess

**Q:** Как project_adopter вызывает configure_vhost, если vhost_renderer.py (TASK-036B) может быть ещё не готов?

**A:** Два режима:
1. **vhost_renderer.py готов (предпочтительный):** Прямой import `configure_vhost_for_project()` → Python API.
2. **vhost_renderer.py НЕ готов (fallback):** subprocess-вызов `add-vhost.sh` как временная мера.

Переключение через `HAS_VHOST_RENDERER` (try/except ImportError). Shell-фасад НЕ знает о режиме — это деталь реализации Python-модуля.

```python
try:
    from core.internal.scaffold.vhost_renderer import configure_vhost_for_project
    HAS_VHOST_RENDERER = True
except ImportError:
    HAS_VHOST_RENDERER = False
```

Это позволяет начать разработку project_adopter.py независимо от TASK-036B, с последующим переключением на прямой import когда vhost_renderer.py будет готов.

**Rev:** После стабилизации vhost_renderer API — удалить fallback-ветку и HAS_VHOST_RENDERER.

### ## @rationale D5: gen_env_platform — сохраняется как subprocess (CLI-first дизайн)

**Q:** Почему не перевести gen_env_platform на библиотечный API и не импортировать напрямую?

**A:** `gen_env_platform.py` спроектирован как CLI-утилита:
- Все функции используют `sys.exit()` (не `return`) — нарушает библиотечный контракт
- Функция `generate()` пишет в stdout, а не возвращает значение
- CLI-first дизайн — осознанное решение (Plan 082)

Рефакторинг gen_env_platform на библиотечный API — отдельная задача (DEBT: `gen_env_platform` library API). В scope этой волны — subprocess.run с захватом stdout и записью в файл. Это быстрее и безопаснее, чем рефакторить чужой модуль.

### ## @rationale D6: validate_org_against_node_yaml — дублирование в shell и Python

**Q:** Зачем дублировать validate_org в shell-фасаде, если Python-модуль тоже это делает?

**A:** Fail-fast принцип: если org не совпадает с node.yaml context, нет смысла запускать Python-модуль (импорт yaml, загрузка модуля). Shell-версия делает grep-based проверку за <10ms и завершается с кодом 1 до запуска Python. Python-версия делает полную проверку с PyYAML для надёжности. Дублирование оправдано производительностью fail-fast пути.

---

## $TASKS

### TASK-036C: Wave 5c — adopt-project.sh → project_adopter.py

- **Owner:** Coder
- **Output:**
  - `core/internal/scaffold/project_adopter.py` (~500 LOC)
  - `core/internal/scaffold/adopt-project.sh` (906 → ~120 LOC)
  - `tests/unit/test_project_adopter.py` (~300 LOC)
- **Acceptance:**
  - Shell ≤150 LOC, 0 inline python3
  - `make adopt-project DIR=<test-project>` работает идентично
  - `make test` зелёный (включая unit-тесты)
  - `make gate MODE=fast` зелёный
  - Все TRAP из adopt-project.sh перенесены в docstring project_adopter.py
- **Dependencies:** TASK-036B (vhost_renderer.py — используется для configure_vhost, D4: try/except ImportError → subprocess add-vhost.sh), но может стартовать параллельно с fallback-режимом
- **Complexity:** 5/10
- **Checkpoint:** `make test` зелёный, shell facade ≤150 LOC, 0 inline python3
- **Sign-off:** QA review VerificationReport для волны перед переходом к следующей

---

## Acceptance Criteria Summary

| ID | Критерий | Метод проверки |
|----|----------|---------------|
| AC-1 | Shell ≤150 LOC | `wc -l core/internal/scaffold/adopt-project.sh` |
| AC-2 | 0 inline python3 | `grep -c "python3 -c\|<<PYEOF" core/internal/scaffold/adopt-project.sh` → 0 |
| AC-3 | adopt-project работает идентично | `make adopt-project DIR=<test-project>` — сравнить вывод и изменения до/после |
| AC-4 | Unit-тесты ≥7, coverage ≥80% | `pytest tests/unit/test_project_adopter.py --cov=core.internal.scaffold.project_adopter --cov-report=term` |
| AC-5 | Все тесты зелёные | `make test` → 0 failures |
| AC-6 | Gate зелёный | `make gate MODE=fast` → 0 failures |
| AC-7 | TRAP перенесены | grep TRAP в project_adopter.py → D1 + B1 + новый TRAP[DECISION] о миграции |

---

## $TEST_SPEC

### Unit Tests (project_adopter.py)

| # | Test file | Test function | Scenario | Module under test |
|---|-----------|---------------|----------|-------------------|
| 1 | `tests/unit/test_project_adopter.py` | `test_generate_minimal_yaml_no_domain` | Генерация ai-platform.yaml для backend-проекта без домена | `ProjectAdopter.generate_minimal_ai_platform_yaml()` |
| 2 | `tests/unit/test_project_adopter.py` | `test_generate_minimal_yaml_with_domain` | Генерация ai-platform.yaml с доменом → expose:true, domain задан | `ProjectAdopter.generate_minimal_ai_platform_yaml()` |
| 3 | `tests/unit/test_project_adopter.py` | `test_generate_minimal_yaml_type_detection` | Авто-детекция типа: frontend/backend/fullstack | `ProjectAdopter.generate_minimal_ai_platform_yaml()` |
| 4 | `tests/unit/test_project_adopter.py` | `test_validate_compose_networks_has_proxy` | Compose с proxy-net external + 1 service connected → PASS | `ProjectAdopter.validate_compose_networks()` |
| 5 | `tests/unit/test_project_adopter.py` | `test_validate_compose_networks_no_proxy` | Compose без proxy-net external → FAIL с инструкцией | `ProjectAdopter.validate_compose_networks()` |
| 6 | `tests/unit/test_project_adopter.py` | `test_validate_compose_networks_no_services` | Compose с proxy-net external, но 0 services connected → FAIL | `ProjectAdopter.validate_compose_networks()` |
| 7 | `tests/unit/test_project_adopter.py` | `test_validate_compose_networks_no_domain_skip` | Нет домена → skip валидации (return True) | `ProjectAdopter.validate_compose_networks()` |
| 8 | `tests/unit/test_project_adopter.py` | `test_validate_org_mismatch` | Org не совпадает с node.yaml context → raise ValueError | `validate_org_against_node_yaml()` |
| 9 | `tests/unit/test_project_adopter.py` | `test_validate_org_casing_mismatch` | Org совпадает case-insensitive, но разный casing → возвращает node.yaml вариант | `validate_org_against_node_yaml()` |
| 10 | `tests/unit/test_project_adopter.py` | `test_simplify_deploy_yml` | Генерация deploy.yml с reusable workflow | `ProjectAdopter.simplify_deploy_yml()` |
| 11 | `tests/unit/test_project_adopter.py` | `test_simplify_deploy_yml_already_simplified` | deploy.yml уже использует reusable workflow → skip | `ProjectAdopter.simplify_deploy_yml()` |
| 12 | `tests/unit/test_project_adopter.py` | `test_register_in_node_yaml_new` | Регистрация нового проекта через project_registry import | `ProjectAdopter.register_in_node_yaml()` |
| 13 | `tests/unit/test_project_adopter.py` | `test_configure_vhost_mocked` | Вызов configure_vhost с mocked vhost_renderer | `ProjectAdopter.configure_vhost()` |
| 14 | `tests/unit/test_project_adopter.py` | `test_generate_makefile` | Генерация Makefile с правильными target-ами | `ProjectAdopter.gen_project_makefile()` |
| 15 | `tests/unit/test_project_adopter.py` | `test_generate_agents` | Генерация AGENTS.md с правильными полями | `ProjectAdopter.gen_project_agents()` |

$TEST_SPEC: 15 tests (10 test functions with sub-scenarios), project_adopter.py

---

## Risk Assessment

| Risk | Severity | Likelihood | Mitigation |
|------|:--------:|:----------:|------------|
| Python-модуль генерирует YAML с неправильным форматированием → ai-platform.yaml не читается парсерами | 🟡 MEDIUM | LOW | PyYAML dump с `default_flow_style=False, sort_keys=False`; snapshot-тест сравнивает вывод с эталонным heredoc из shell-версии |
| validate_compose_networks regression: PyYAML не разрешает anchors → false negative на валидном compose | 🟡 MEDIUM | MEDIUM | Сохранён `docker compose config` как первый метод; PyYAML только fallback; тест с реальным compose-файлом имеющим anchors |
| configure_vhost: vhost_renderer.py API изменяется между TASK-036B и TASK-036C | 🟡 MEDIUM | LOW | Абстракция с try/except ImportError + fallback на subprocess add-vhost.sh (D4) |
| register_in_node_yaml: project_registry.register_project() sys.exit(0) ломает поток выполнения | 🟢 LOW | HIGH | Обёртка `_register_project_safe()` с try/except SystemExit (D3) |
| gen_env_platform subprocess: изменение CLI-интерфейса gen_env_platform.py | 🟢 LOW | LOW | CLI зафиксирован Plan 082; если изменится — тест упадёт |
| Shell-фасад: parse_args авто-детекция org ломается на нестандартных путях | 🟢 LOW | LOW | Логика авто-детекции НЕ изменяется — копируется как есть из оригинального shell |
| Pre-existing: yq не установлен на машине разработчика → shell fallback на python3 работает | 🟢 LOW | N/A | Не регрессия — существующее поведение сохраняется в shell-фасаде |

**Общий уровень риска: 🟡 MEDIUM** — локальные операции, нет VPS-компонентов, но compose-валидация сложная (3 метода парсинга).

---

## Rollback Strategy

| Сценарий | Метод | Время |
|----------|-------|:-----:|
| Python-модуль не работает | `git revert` merge-коммита → shell-фасад восстановлен | <5 min |
| adopt-project был прерван (частичная регистрация) | Ручная очистка: `make remove-project NAME=<name>` + удаление сгенерированных файлов | <10 min |
| vhost_renderer.py не готов (TASK-036B delayed) | Python-модуль автоматически fallback на subprocess add-vhost.sh (D4) | 0 min (автоматически) |
| project_registry regression | `git revert` → subprocess-вызов восстановлен | <5 min |

**Критическое правило:** adopt-project — wizard, а не критический production-путь. При любом regression: revert, ручная регистрация если нужно.

---

## TRAP Inventory

### TRAP, переносимые из adopt-project.sh → project_adopter.py

```python
# 🧐 TRAP[DECISION] · 2026-07-21 · — · parse_args (env auto-detection) stays in shell facade
# · Rejected: full Python parse_args with subprocess-based path/env detection
# · Reason: auto-detection (basename dir, grep YAML, pwd -P, env vars) is inherently shell-bound.
#          Extracting would add subprocess overhead without testability gain.
# · Rev: если parse_args потребует сложной логики (>50 LOC новых проверок) → извлечь в Python

# ⚠️ TRAP[BUG] · 2026-07-17 · P1 · Silent default "personal" org + missing casing normalization — config drift
# · Symptom: PROJECT_ORG defaulted to "personal" when --org not provided; ghcr.io casing mismatch
# · Root: отсутствие fail-fast для пустого org + отсутствие lowercase-нормализации ghcr paths
# · Fix: fail-fast exit 1 с подсказкой + lowercase для ghcr + exact-case для uses: + сверка с node.yaml
# · Prevention: org всегда явный — отказ вместо молчания
```

### Новый TRAP после миграции

```python
# 🧐 TRAP[DECISION] · 2026-07-26 · — · Wave 5c: adopt-project.sh Strangler-Fig migrated to project_adopter.py
# · Rejected: keeping adopt logic in shell (906 LOC monolith with 2 inline python3 blocks)
# · Reason: языковая политика (AGENTS.md), тестируемость compose-валидации, дедупликация с project_registry
# · Rev: если project_adopter.py вызывает >20% ошибок adopt vs shell-версия → профилировать и фиксить

# 📝 TRAP[DEBT] · 2026-07-26 · LO · gen_env_platform.py — CLI-first design prevents direct import
# · Observed: gen_env_platform.py функции используют sys.exit() вместо return → нельзя импортировать как библиотеку
# · Suspected: осознанный CLI-first дизайн (Plan 082). Рефакторинг на библиотечный API — отдельная задача.
# · Impact: project_adopter использует subprocess.run вместо прямого import (overhead ~100ms)
# · When: during Wave 5c migration — deferred, out of scope

# 📝 TRAP[DEBT] · 2026-07-26 · LO · node.yaml path resolution duplicated across 4+ scripts
# · Observed: `projects/<org>/node-configs/<node>/node.yaml` путь вычисляется в adopt-project.sh,
#   add-vhost.sh, add-project.sh, remove-project.sh с идентичной логикой
# · Suspected: кандидат на shared NodeConfigPathResolver (отдельный DevPlan)
# · Impact: изменение структуры путей потребует правок в 4+ местах
# · When: during Wave 5c migration — deferred, out of scope (see KNOWLEDGE_DEDUP)
```

---

## File Manifest

### Modified files

| Файл | До (LOC) | После (LOC) | Сокращение |
|------|----------|-------------|------------|
| `core/internal/scaffold/adopt-project.sh` | 906 | ~120 | 87% |

### New files

| Файл | LOC | Назначение |
|------|-----|-----------|
| `core/internal/scaffold/project_adopter.py` | ~500 | Бизнес-логика adopt: YAML-генерация, CI-rewriting, compose-валидация, регистрация, vhost, шаблоны |
| `tests/unit/test_project_adopter.py` | ~300 | Unit-тесты для project_adopter (15 тестовых сценариев) |

### Unchanged dependency files

| Файл | Причина |
|------|--------|
| `core/internal/shared/project_registry.py` | Уже существует — используется через прямой import |
| `core/internal/scaffold/gen_env_platform.py` | Уже существует — используется через subprocess (CLI-first) |
| `core/internal/scaffold/gen-env-platform.sh` | Уже существует — не изменяется |
| `core/internal/scaffold/vhost_yaml_reader.py` | Уже существует — не затронут |
| `core/entrypoints/scaffold.sh` | Вызывает `adopt-project.sh` — интерфейс не меняется |

---

## Shell Facade Structure (целевой)

```bash
#!/usr/bin/env bash
# ~120 LOC

# ── Source libraries ──
source logging.sh, args.sh, python_deps.sh

# ── Usage definition (KARGS convention) ──
USAGE_SCRIPT="adopt-project.sh"
USAGE_OPTIONS=(...)

# ── parse_args (сохраняется per D1) ──
parse_args() {
    # CLI parsing + auto-detection (name, node, domain, org)
    # FAIL-FAST: --dir required, PROJECT_ORG required (TRAP[BUG] B1)
    # ~90 LOC
}

# ── validate_org_against_node_yaml (grep-based fast-fail) ──
validate_org_against_node_yaml() {
    # grep context: из node.yaml → case-insensitive compare
    # FAIL-FAST если mismatch, update casing если casing mismatch
    # ~25 LOC
}

# ── main ──
main() {
    parse_args "$@"
    validate_org_against_node_yaml

    python3 -m core.internal.scaffold.project_adopter adopt \
        --project-dir "$PROJECT_DIR" \
        --project-name "$PROJECT_NAME" \
        --project-org "$PROJECT_ORG" \
        --project-node "$PROJECT_NODE" \
        ${PROJECT_DOMAIN:+--project-domain "$PROJECT_DOMAIN"} \
        ${FORCE:+--force}
    # Exit code from Python propagated
}
main "$@"
```

**Гарантии:**
- ≤150 LOC (цель: ~120 LOC)
- 0 `python3 -c` / `<<PYEOF`
- Все функции бизнес-логики удалены из shell
- Shell содержит ТОЛЬКО: source libs, usage definition, parse_args, validate_org (grep), main (вызов Python)

---

## Next Steps

### Implementation Command

```
coder Read .ai/plans/036-wave5c-adopt/01-DevPlan.md, implement TASK-036C: adopt-project.sh → project_adopter.py
```

### Pre-implementation Checklist

- [ ] TASK-036B (vhost_renderer.py) готов ИЛИ принят fallback-режим (D4)
- [ ] `make test` зелёный на текущем HEAD
- [ ] `make gate MODE=fast` зелёный на текущем HEAD

### Implementation Order (within TASK-036C)

1. Создать `project_adopter.py` с классом `ProjectAdopter` и всеми методами
2. Создать `tests/unit/test_project_adopter.py` с 15 тестами
3. Запустить тесты → все зелёные
4. Обрезать `adopt-project.sh` до shell-фасада (~120 LOC)
5. Проверить: `make adopt-project DIR=<test>` → идентичное поведение
6. Проверить: `make test && make gate MODE=fast` → зелёные
7. Добавить TRAP-комментарии в project_adopter.py

### Verification

```
# Unit tests
python -m pytest tests/unit/test_project_adopter.py -s -v

# Integration smoke test
make adopt-project DIR=/tmp/test-adopt-project --force

# Full gate
make test && make gate MODE=fast
```

$END_DEVPLAN
