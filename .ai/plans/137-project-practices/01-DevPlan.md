# 137-project-practices — 01-DevPlan.md

$START_DEVPLAN

$ARTIFACT_CONTRACT
PURPOSE:               Наследование защитных практик платформы (линт, pre-commit, CI-gate, дрейф-гейты, деплой-контракты) в проекты, подключаемые к платформе — без копипаста и без ухода в детали: проект наследует ПОВЕДЕНИЕ (проверки исполняются платформенными каналами), а не код.
DESCRIPTION:           Двухуровневая модель практик: BASELINE (быстрые автофиксируемые проверки, overhead цикла правки ≤60s — пригоден для моков/MVP) и FULL (максимальная защита — ruff-линт, типы, LDD, дрейф-гейты, полные verify-контракты — для долгоживущих проектов). Плавный эскалатор зрелости: baseline → proposed (non-blocking варнинги) → active-full (ТОЛЬКО по согласию; автопромоута нет — решение пользователя 2026-08-05). Триггеры предложения: возраст репозитория > 30 дней ИЛИ > 50 написанных файлов кода (вычисляются локально и в CI; на VPS — из practices.lock). Каналы: scaffold-генерация тонких GENERATED-файлов, локальное делегирование через PLATFORM_DIR (make project-check) + pre-push хук проекта (K5), CI через inline quality-шаги deploy-project.yml, деплой через расширение verify на VPS.
RATIONALE:             Проекты сейчас деплоятся БЕЗ единой проверки качества (deploy-project.yml: ping→receive→verify; verify не проверяет код). Перенос практик копипастом в шаблоны породит дрейф копий (как allowlist-дрейф, DevPlan 116 T9) и заставит проекты поддерживать чужой код. Каналы делегирования уже существуют: PLATFORM_DIR в Makefile проекта + reusable workflow + forced-command verify — расширяем их, а не дублируем. Жёсткий lint (ruff check) гоняет агента по правкам каждую сессию — для моков/MVP это убивает velocity, поэтому baseline содержит только автофиксируемые/детект-разовые проверки (решение пользователя 2026-08-05).
ACCEPTANCE_CRITERIA:   (1) make new-project создаёт проект с baseline-практиками (level=auto, state=baseline), make project-check зелёный ≤60s (warm) без правок агента; (2) мок-проект (3 файла) проходит baseline за ≤60s; (3) проект зрелости (возраст >30 дней ИЛИ >50 файлов кода) получает [PRACTICES:PROPOSE] в AI-PLATFORM.md/pre-push/CI-summary/verify, деплой НЕ блокируется (proposed); (4) make project-set-practices full включает полный набор, L1-контракты блокируют деплой, L2/L3 — в active-full; (5) автопромоут ОТСУТСТВУЕТ: active-full только по явному согласию (решение пользователя 2026-08-05), переходы аудируются; (6) дрейф GENERATED-практик детектится локально (project-check), в CI (maturity-warn) и на VPS (verify, version-warn), ремонтируется make project-sync-practices; (7) practices.lock доставляется на VPS payload'ом receive; (8) gate платформы зелёный; (9) язык-ветвление python/typescript/react/sh реализовано.
IMPLEMENTS:            Решение пользователя 2026-08-05 (двухуровневая модель + эскалатор через варнинги); паттерны: Manifest Generation Contract (инвариант 11), repair-контракт L1/L2/L3, check-suite.yaml, verify verb (DevPlan 125), PLATFORM_DIR-делегирование (K3/DD11), parity-гейты с allowlist (116 T9).
IMPACTS:               templates/template-{backend,frontend,fullstack}/ (новые GENERATED-файлы практик, quality-секция ai-platform.yaml), core/internal/practices/ (новый модуль: manifest, maturity, escalator, generators, check_project, sync_practices, set_practices), core/internal/scaffold/project_scaffolder.py (шаг практик), core/internal/scaffold/gen_project_platform_md.py (Practices-секция), makefiles/ (project-check/fix/sync-practices/set-practices), core/internal/deploy/verify_contracts.py (новый, расширение verify), .github/workflows/deploy-project.yml (inline quality-шаги + FILES += practices.lock), docs/platform-project-contract.md, core/entrypoint-manifest.yaml, core/AGENTS.md (генерируется), core/check-suite.yaml (project-режим), tests/gates/test_gate_practices_*.py (~4), tests/unit/test_practices_*.py (~5), AGENTS.md.
REQUIRES:              main зелёный (136 влит); решение пользователя по политике L1-блокировки принято (в DevPlan §6.4); доступ к контекстной орг для проверки inline quality-шагов deploy-project.yml (тестовый контекст).
$END_ARTIFACT_CONTRACT

$START_DOCUMENT_PLAN
### Document Plan
**SECTION_GOALS:**
- GOAL [Зафиксировать стратегию и принцип «наследование поведения, не кода»] => G1 (§1)
- GOAL [Определить целевую архитектуру: канон практик + 4 канала доставки] => G2 (§2)
- GOAL [Задать состав BASELINE и FULL по языкам с критериями включения] => G3 (§3)
- GOAL [Спроектировать эскалатор зрелости и варнинг-предложения агенту] => G4 (§4)
- GOAL [Развернуть суперпозицию эскалации и политики блокировки] => G5 (§4.4, §4.5)
- GOAL [Разбить работу на исполняемые волны с AC] => G6 (§5)
- GOAL [Зафиксировать манифест файлов и риски] => G7 (§6, §7)
**SECTION_USE_CASES:**
- USE_CASE [Разработчик создаёт MVP-проект и работает с baseline] => SC1 (§3.2, §5 W1)
- USE_CASE [Проект взрослеет, платформа предлагает full через варнинги] => SC2 (§4, §5 W3)
- USE_CASE [Разработчик/агент включает full и деплоит с контрактами] => SC3 (§4.3, §5 W4)
- USE_CASE [Code-субагент исполняет волну] => SC4 (промт-шаблон §8)
$END_DOCUMENT_PLAN

## 1. Стратегия

**Наследование поведения, не кода.** Проект получает тонкие GENERATED-файлы (конфиги практик), а исполнение проверок делегируется платформенным каналам. Пять каналов, три из которых уже существуют (K1 — PLATFORM_DIR-делегирование, K2 — deploy-project.yml, K3 — verify verb):

```
                    ┌─ K1 ЛОКАЛЬНО (PLATFORM_DIR, существует)
                    │   make project-check/fix/sync-practices/set-practices
                    │   → python3 -m core.internal.practices.check_project --project <dir>
                    ▼
┌─ Проект ─────────────────────────────────────────────┐
│  GENERATED-файлы (из канона, DO NOT EDIT):           │
│  pyproject.toml(ruff/pytest), .pre-commit-config,    │
│  tests/conftest.py, tests/test_health.py,            │
│  practices.lock (version+level+state+maturity+hash),  │
│  ai-platform.yaml#quality (level=auto)                 │
└──────────────────────────────────────────────────────┘
                    │
                    ├─ K2 CI ПРОЕКТА (deploy-project.yml, inline quality-шаги)
                    │   lint/test/build по языку (level/language из ai-platform.yaml),
                    │   maturity-warn + варнинг-предложения в job summary
                    ▼
                    ├─ K3 ДЕПЛОЙ (verify на VPS, forced-command)
                    │   verify_contracts.py: L1 всегда (блок),
                    │   L2/L3 по state из practices.lock (доставляется
                    │   payload'ом receive), дрейф version-warn,
                    │   [PRACTICES:...] варнинги в выводе
                    ▼
                    ├─ K4 ЭСКАЛАТОР (maturity + escalator, ЛОКАЛЬНО)
                    │   baseline → proposed → active-full
                    │   maturity считается там, где есть git (локально,
                    │   pre-push, CI); VPS НЕ считает maturity — применяет lock
                    ▼
                    └─ K5 PRE-PUSH (локальный хук проекта, решение пользователя)
                        make project-check при push: [PRACTICES:PROPOSE] варнинг
                        (non-blocking); L2/L3-блок только в active-full
```

**Принципы:**
1. **Проект наследует поведение.** Проверки исполняются платформенным Python (K1/K3) и платформенным workflow (K2). В проекте — только конфиги.
2. **Минимум friction для моков.** BASELINE = автофиксируемое + разовые детекты, суммарно ≤60s на цикл правки (критерий пользователя).
3. **Максимум защиты для зрелых.** FULL включается плавно: сначала варнинг-предложение (non-blocking), потом активный режим ТОЛЬКО по явному согласию (`make project-set-practices full`). Автопромоута НЕТ (решение пользователя 2026-08-05: «варнинга хватит») — никаких резких переключений.
4. **Дрейф детектируется там, где есть git и полный checkout** (локально K1/K5, CI проекта K2), и по-минимуму на VPS (K3: version-warn по lock). В CI проекта — варнинг-сверка state (maturity вычислима: checkout полный).
5. **Язык-ветвление** по `type` проекта: python → ruff/pytest, typescript/react → build/tsc/eslint, sh → shellcheck, общий слой → gitleaks/hygiene/compose.

## 2. Целевая архитектура

### 2.1 Новый модуль `core/internal/practices/` (Python, по языковой политике)

```
core/internal/practices/
├── practices_manifest.yaml   # SoT канона: version, levels, checks[] по языкам,
│                             # пороги зрелости, allowed_external_networks,
│                             # pins версий хуков (аналог check-suite.yaml)
├── manifest.py               # чтение+валидация канона (Draft7, fail-fast)
├── maturity.py               # зрелость проекта: age/code_files (ЛОКАЛЬНО: git проекта)
├── escalator.py              # состояние практик: baseline|proposed|active-full (3 состояния)
├── generators.py             # рендер GENERATED-файлов в каталог проекта
├── check_project.py          # CLI make project-check/project-fix
├── sync_practices.py         # CLI make project-sync-practices (обновление до канона)
└── set_practices.py          # CLI make project-set-practices {baseline|full}
```
**Замечание (аудит 137):** `hooks/{hygiene.sh, commit_msg.sh}` УДАЛЕНЫ из архитектуры — проектный pre-commit ссылается только на upstream-репозитории (см. §3.3), платформенные shell-хуки не нужны (дубли upstream + нарушение языковой политики).

**practices_manifest.yaml — схема (v1):**
```yaml
version: 1
maturity:
  age_days_propose: 30        # порог возраста (решение пользователя)
  code_files_propose: 50      # порог файлов кода (не библиотек)
  # автопромоута НЕТ (решение пользователя 2026-08-05: «варнинга хватит»)
allowed_external_networks:    # allowlist L1 external-networks (реальные сети платформы)
  - proxy-net
  - shared-db-net
  - observability-net
  - backup-net
  - hermes-agent-net
  - shared-cache-net
pins:                         # версии хуков проекта = версиям платформы (анти-дрейф)
  gitleaks: v8.30.1           # паритет .github/actions/setup-gitleaks (latest 2026-08-05)
  ruff_pre_commit: 0.16.1     # паритет корневому .pre-commit-config.yaml (апгрейд 0.15.21→0.16.1, 2026-08-05)
  shellcheck_py: v0.11.0.1    # latest (2026-08-05)
  pre_commit_hooks: v6.0.0    # latest (2026-08-05)
  conventional_pre_commit: v4.4.0  # latest (2026-08-05)
  # Выравнивание версий 2026-08-05: за одно обновление (ruff 0.15.21→0.16.1) проверяется
  # механизм анти-дрейфа — гейт сверяет pins канона с корневым .pre-commit-config.yaml платформы.
checks:
  - id: gitleaks
    level: baseline           # baseline | full
    languages: [all]
    channel: [local, ci, verify]
    class: L1                  # L1 = блок всегда, L2 = блок в full, L3 = warning
    auto_fix: false
    timeout_sec: 5
  - id: hygiene                # trailing/EOF/merge-conflict/yaml/json/toml/private-key
    level: baseline
    languages: [all]
    channel: [local, ci]
    class: L3
    auto_fix: true            # pre-commit автофикс — агент не тратит время
    timeout_sec: 5
  - id: commit-msg             # Conventional Commits
    level: baseline
    languages: [all]
    channel: [local, ci]
    class: L3
    auto_fix: false
    timeout_sec: 1
  - id: compose-config         # docker compose config --quiet
    level: baseline
    languages: [all]
    channel: [local, ci, verify]
    class: L2
    auto_fix: false
    timeout_sec: 5
  - id: docker-build-check     # docker build --check (BuildKit статический)
    level: baseline
    languages: [all]
    channel: [ci]
    class: L2
    auto_fix: false
    timeout_sec: 20
  - id: ruff-format            # ruff format --check (автофикс через project-fix)
    level: baseline
    languages: [python]
    channel: [local, ci]
    class: L3
    auto_fix: true
    timeout_sec: 5
  - id: shellcheck             # только sh-файлы проекта
    level: baseline
    languages: [sh]
    channel: [local, ci]
    class: L3
    auto_fix: false
    timeout_sec: 10
  - id: pytest-baseline        # pytest -q -x --timeout=60 (если тесты есть)
    level: baseline
    languages: [python]
    channel: [local, ci]
    class: L3
    auto_fix: false
    timeout_sec: 30
  - id: build                  # npm run build / tsc --noEmit
    level: baseline
    languages: [typescript, react]
    channel: [local, ci]
    class: L2
    auto_fix: false
    timeout_sec: 30
  # ─── FULL (максимальный уровень) ───
  - id: ruff-check             # полный набор правил
    level: full
    languages: [python]
    channel: [local, ci]
    class: L3
    auto_fix: true             # ruff --fix — но правила жёсткие: full-only
    timeout_sec: 10
  - id: pyright
    level: full
    languages: [python]
    channel: [local, ci]
    class: L3
    auto_fix: false
    timeout_sec: 20
  - id: eslint
    level: full
    languages: [typescript, react]
    channel: [local, ci]
    class: L3
    auto_fix: false
    timeout_sec: 15
  - id: pytest-full            # strict-маркеры, LDD IMP:9, Honesty R1/R4
    level: full
    languages: [python]
    channel: [local, ci]
    class: L3
    auto_fix: false
    timeout_sec: 120
  - id: grep-summary           # GREP_SUMMARY в первых 10 строках кода
    level: full
    languages: [python, typescript, react, sh]
    channel: [local, ci]
    class: L3
    auto_fix: false
    timeout_sec: 5
  - id: drift-gate             # GENERATED-файлы практик не тронуты руками
    level: full                # (proposed: non-blocking)
    languages: [all]
    channel: [local, ci, verify]   # verify: только version-warn (на VPS нет файлов проекта)
    class: L2
    auto_fix: true             # repair: make project-sync-practices
    timeout_sec: 5
  - id: verify-contracts       # L1: секреты/порты/env-контракт/healthcheck/labels
    level: full                # (L1-класс исполняется и в baseline — см. §4.5)
    languages: [all]
    channel: [verify]
    class: L1
    auto_fix: false
    timeout_sec: 10
```

### 2.1A Сигнатуры модулей и контракты (детально)

Каждый новый модуль следует каноническим контрактам core (MODULE_CONTRACT region, `main() -> int`, sys.exit только в `main()`, exit-коды 0/1/2/3/4/10 из `shared/contracts.py`). Сигнатуры библиотечных функций (не CLI):

```python
# ── manifest.py ────────────────────────────────────────────────────
@dataclass(frozen=True)
class PracticeCheck:
    id: str               # "ruff-format" (kebab-case)
    level: str            # "baseline" | "full"
    languages: tuple[str, ...]   # ("python",) | ("all",)
    channel: tuple[str, ...]     # ("local", "ci", "verify")
    klass: str            # "L1" | "L2" | "L3"   (НЕ "class" — keyword)
    auto_fix: bool
    timeout_sec: int

@dataclass(frozen=True)
class PracticesManifest:
    version: int
    maturity: dict        # {age_days_propose, code_files_propose, auto_promote_deploys}
    checks: tuple[PracticeCheck, ...]

def load_manifest(path: Path | None = None) -> PracticesManifest: ...
    # default = core/internal/practices/practices_manifest.yaml
    # schema_validator.validate_yaml_against_schema (Draft7, единая точка, DevPlan 116 B6 T5)
    # raise ConfigValidationError (exit 4) при структурной ошибке

def checks_for(check_id: str, *, language: str, level: str, channel: str) -> tuple[PracticeCheck, ...]: ...
def l1_checks() -> tuple[PracticeCheck, ...]: ...   # всегда исполняются (безопасность платформы)

# ── maturity.py ────────────────────────────────────────────────────
@dataclass(frozen=True)
class Maturity:
    age_days: int
    code_files: int

def compute_maturity(project_dir: Path) -> Maturity: ...
    # ЛОКАЛЬНО (K1/K5) и в CI (K2, checkout полный) — там есть git.
    # age_days = первый коммит проекта (git log --reverse --format=%aI); fallback —
    # первый коммит, добавивший ai-platform.yaml (git log --follow --diff-filter=A),
    # затем mtime ai-platform.yaml (решение пользователя: «дата создания определяется
    # скриптом по файлам платформы в этом проекте»).
    # НЕ вызывается на VPS (нет git, payload = 4 файла) — там maturity из practices.lock.

def is_propose(m: Maturity, thresholds: dict) -> bool: ...   # age>30 ∨ files>50

# ── escalator.py ───────────────────────────────────────────────────
# State-машина (см. §4.6 диаграмму). 3 состояния, БЕЗ автопромоута (решение
# пользователя 2026-08-05). State персистится в practices.lock (см. §2.1B),
# вычисляется ТОЛЬКО там, где доступен git (локально K1/K5, CI K2); на VPS
# verify_contracts применяет готовый state из lock (evaluate() не вызывается).
class PracticesState(Enum):
    BASELINE = "baseline"        # только baseline-набор + L1
    PROPOSED = "proposed"        # full-набор non-blocking + [PRACTICES:PROPOSE] (бывшие shadow+proposed)
    ACTIVE_FULL = "active-full"  # full-набор, L1+L2+L3 блокируют (ТОЛЬКО по согласию)

@dataclass(frozen=True)
class EscalatorDecision:
    state: PracticesState
    reason: str              # "age=41d,files=87" / "manual: baseline" / "manual: full"
    warning: str | None      # "[PRACTICES:PROPOSE]..." или None

def evaluate(
    maturity: Maturity,
    level_setting: str,      # "baseline" | "full" | "auto"  (из ai-platform.yaml#quality.level)
    lock: PracticesLock,
) -> EscalatorDecision: ...
    # логика переходов (§4.6): level=baseline|full → форсируем состояние; "auto" → maturity решает

# ── generators.py ──────────────────────────────────────────────────
# Все рендеры через atomic_write_text (shared/atomic_writer, единый writer — DevPlan 119 E5).
# GENERATED-файлы имеют шапку "# GENERATED by ai-platform practices — DO NOT EDIT (make project-sync-practices)".

def render_pyproject(project_name: str, language: str, level: str) -> str: ...
def render_precommit(level: str, language: str) -> str: ...
    # ТОЛЬКО upstream-репозитории (pre-commit-hooks, gitleaks, conventional-pre-commit,
    # ruff-pre-commit, shellcheck-py) + pre-push хук → make project-check. НИКАКИХ
    # платформенных скриптов в проекте (аудит 137: hygiene.sh/commit_msg.sh отклонены —
    # дубли upstream + нарушение языковой политики + пути core/ отсутствуют в проекте).
def render_conftest(project_name: str) -> str: ...
def render_test_health() -> str: ...
def render_lock(manifest: PracticesManifest, level_setting: str, decision: EscalatorDecision, maturity: Maturity) -> str: ...
    # см. §2.1B формат practices.lock; maturity-снапшот обязателен (носитель для VPS)

# ── check_project.py (CLI main → int) ──────────────────────────────
# exit 0 (зелёный), 1 (generic — нарушен L3/L2-блокирующий в active-full),
# 4 (ConfigValidationError — нет манифеста). Логирует [IMP:9] trajectory.

# ── sync_practices.py / set_practices.py (CLI main → int) ──────────
# паритет sync_env_defaults.py / sync_requirements.py: библиотечная функция
# + CLI, write через atomic_write_text, exit 0/1.
```

**Контракт вызова из shell-фасада (Makefile проекта → PLATFORM_DIR → make project-*):**
```bash
# makefiles/project-practices.mk (тонкий фасад, паритет существующему Makefile шаблона):
project-check: ; @$(MAKE) -C $(PLATFORM_DIR) project-check PROJECT=$(CURDIR)
project-fix:   ; @$(MAKE) -C $(PLATFORM_DIR) project-fix PROJECT=$(CURDIR)
project-sync-practices: ; @$(MAKE) -C $(PLATFORM_DIR) project-sync-practices PROJECT=$(CURDIR)
project-set-practices: ; @$(MAKE) -C $(PLATFORM_DIR) project-set-practices PROJECT=$(CURDIR) LEVEL=$(LEVEL)
```
Корневой `makefiles/project-practices.mk` (в платформе):
```make
project-check: ; python3 -m core.internal.practices.check_project --project-dir $(PROJECT) $(if $(LEVEL),--level $(LEVEL),)
project-fix:   ; python3 -m core.internal.practices.check_project --project-dir $(PROJECT) --fix
project-sync-practices: ; python3 -m core.internal.practices.sync_practices --project-dir $(PROJECT)
project-set-practices: ; python3 -m core.internal.practices.set_practices --project-dir $(PROJECT) --level $(LEVEL)
```

### 2.1B Формат `practices.lock` (GENERATED, единый SoT-снапшот проекта)

`practices.lock` — детерминированный снапшот применённого канона + состояние эскалатора. Аналог `requirements.txt` (lock-файл): фиксирует «какая версия практик сейчас в проекте». Используется для дрейф-детекта (как `check-manifests`).

```yaml
# GENERATED by ai-platform practices — DO NOT EDIT (run: make project-sync-practices)
# GREP_SUMMARY: practices.lock, version, level, state, generator_hash, maturity
version: 1                    # версия канона practices_manifest.yaml
level: auto                   # явно выставленный уровень (baseline|full|auto)
state: proposed               # состояние эскалатора (baseline|proposed|active-full) — вычислено
                              # локально (K1/K5) или в CI (K2); VPS применяет как есть
maturity:                     # снапшот зрелости на момент генерации (для VPS: git недоступен)
  age_days: 41
  code_files: 87
generated_at: 2026-08-05T03:00:00Z   # UTC ISO8601 — для диагностики
generator_hash: sha256:7a3f...        # sha256(canonical_rendered_files) — drift-detect
language: python              # из ai-platform.yaml#type (python|typescript|react|sh|backend|frontend|fullstack)
files:                        # список отрендеренных GENERATED-файлов + их sha256
  pyproject.toml: sha256:1b2c...
  .pre-commit-config.yaml: sha256:9d8e...
  tests/conftest.py: sha256:4f5a...
  tests/test_health.py: sha256:c3d4...
```

**Доставка на VPS (критично, аудит 137):** `practices.lock` входит в payload receive —
`deploy-project.yml` шаг Deliver добавляет `[ -f practices.lock ] && FILES="$FILES practices.lock"`.
Без этого K3 (дрейф-гейт, state для L2/L3) не имеет носителя. VPS НЕ пишет в lock
(GENERATED-файл репозитория) — runtime-состояние на ноде не хранится вовсе (автопромоута
нет, счётчики не нужны).

**`generator_hash` алгоритм:**
```python
import hashlib, yaml
def compute_generator_hash(files: dict[str, str], version: int, level: str) -> str:
    """Deterministic hash of rendered GENERATED files (drift-detect)."""
    h = hashlib.sha256()
    h.update(f"v{version}:{level}\n".encode())
    for path in sorted(files):                      # sorted = детерминизм
        h.update(f"{path}={hashlib.sha256(files[path].encode()).hexdigest()}\n".encode())
    return f"sha256:{h.hexdigest()}"
```

**Дрейф-детект:** сравнение `generator_hash` в lock-файле проекта с актуальным рендером канона. Несовпадение → `make project-sync-practices` (repair). Аналог `make check-manifests` (byte-level comparison).

### 2.1C GENERATED-маркеры для Practices-секции AI-PLATFORM.md

Расширяем `gen_project_platform_md.py` (DevPlan 133 W1) — добавляем **вторую** GENERATED-секцию, параллельную существующей `platform_md`:

```markdown
## Practices  <!-- GENERATED:START:practices_md --> <!-- GENERATED:END:practices_md -->
```

Константы (паритет существующим `GENERATED_START = "<!-- GENERATED:START:platform_md -->"`):
```python
PRACTICES_START = "<!-- GENERATED:START:practices_md -->"
PRACTICES_END   = "<!-- GENERATED:END:practices_md -->"
```

**Replace-section семантика (идентична существующей):** повторная генерация заменяет ТОЛЬКО секцию между маркерами; ручные правки статической части сохраняются. Существующий файл БЕЗ маркеров → создаётся при `make project-sync-practices` (force=False skip, как у `platform_md`).

**Содержимое Practices-секции (пример для proposed):**
```markdown
- **Level:** full (auto-proposed)
- **State:** proposed (age=41d, files=87)
- **Generator:** practices v1, hash sha256:7a3f...

> [PRACTICES:PROPOSE][level:full][reason:age=41d,files=87]
> >>> RECOMMEND: `make project-set-practices full` (или `make project-sync-practices` для обновления канона)
> Деплой НЕ блокируется (proposed = non-blocking). active-full включается ТОЛЬКО по согласию
> (`make project-set-practices full`) — автопромоута нет (решение пользователя 2026-08-05).
```

### 2.2 Draft Code Graph (XML)

```xml
<graph>
  <entity name="practices_manifest_yaml" type="CONFIG" keywords="practices soT version levels checks maturity"
          annotation="core/internal/practices/practices_manifest.yaml — канон практик проектов (аналог check-suite.yaml)"/>
  <entity name="practices_manifest_py" type="MODULE" keywords="manifest load validate fail-fast"
          annotation="чтение+валидация канона, схема Draft7, константы порогов"/>
  <entity name="practices_maturity_py" type="MODULE" keywords="age first-commit code-files count excludes"
          annotation="core/internal/practices/maturity.py — зрелость проекта: возраст (первый коммит git, fallback ai-platform.yaml — решение пользователя), счётчик файлов кода (исключая node_modules/.venv/dist/*.lock/generated). Локально + CI; НЕ на VPS (нет git)"/>
  <entity name="practices_escalator_py" type="MODULE" keywords="state baseline proposed active promote"
          annotation="3 состояния (baseline|proposed|active-full): maturity + level из ai-platform.yaml#quality; БЕЗ автопромоута (решение 2026-08-05); вызывается локально/CI, на VPS применяется готовый state из practices.lock"/>
  <entity name="practices_generators_py" type="MODULE" keywords="render pyproject pre-commit conftest lock"
          annotation="генерация тонких GENERATED-файлов в каталог проекта, hash-файл practices.lock"/>
  <entity name="practices_check_project_py" type="MODULE" keywords="project-check baseline full run report"
          annotation="CLI: исполнение проверок канона по каталогу проекта, exit 0/1, [PRACTICES:...] вывод"/>
  <entity name="practices_sync_practices_py" type="MODULE" keywords="sync regenerate repair drift"
          annotation="CLI: перегенерация GENERATED-файлов до канона, ремонт дрейфа (аналог generate-manifests)"/>
  <entity name="practices_set_practices_py" type="MODULE" keywords="set level baseline full ai-platform-yaml"
          annotation="CLI: установка уровня практик в ai-platform.yaml#quality + practices.lock"/>
  <entity name="verify_contracts_py" type="MODULE" keywords="verify L1 contracts secrets ports env healthcheck"
          annotation="core/internal/deploy/verify_contracts.py — контракт-проверки проекта при деплое (расширение verify verb)"/>
  <entity name="deploy_project_quality_steps" type="WORKFLOW" keywords="deploy-project inline quality lint test maturity-warn blocking"
          annotation="inline quality-шаги в .github/workflows/deploy-project.yml (caller-контекст, org-agnostic): lint/test по language/level из ai-platform.yaml, maturity-warn, blocking-step для full, FILES += practices.lock. Reusable workflow quality-gate.yml ОТКЛОНЁН (аудит 137)" />
  <entity name="project_scaffolder_py" type="MODULE" keywords="new-project step practices generate"
          annotation="core/internal/scaffold/project_scaffolder.py — шаг 11: генерация baseline-практик"/>
  <entity name="gen_project_platform_md_py" type="MODULE" keywords="ai-platform md practices section"
          annotation="GENERATED-секция Practices в AI-PLATFORM.md: уровень, зрелость, [PRACTICES:PROPOSE]"/>
  <entity name="test_practices_maturity_py" type="TEST" keywords="age code-files excludes thresholds"
          annotation="юнит: maturity — счётчик файлов, исключения, возраст"/>
  <entity name="test_practices_escalator_py" type="TEST" keywords="states baseline proposed active promote"
          annotation="юнит: переходы состояний эскалатора (3 состояния, без автопромоута)"/>
  <entity name="test_practices_generators_py" type="TEST" keywords="deterministic render lock hash"
          annotation="юнит: детерминизм генераторов, практики.lock hash"/>
  <entity name="test_practices_check_project_py" type="TEST" keywords="baseline mock fast green"
          annotation="юнит: project-check на мок-проекте, ≤60s, zero-churn критерий"/>
  <entity name="test_gate_practices_manifest_py" type="GATE" keywords="soT manifest consistent checks"
          annotation="гейт: канон практик консистентен (уникальные id, классы, языки)"/>
  <entity name="test_gate_templates_practices_py" type="GATE" keywords="templates contain practices files"
          annotation="гейт: все 3 шаблона содержат практики-файлы и quality-секцию"/>
  <entity name="test_gate_practices_generators_deterministic_py" type="GATE" keywords="byte-identical render"
          annotation="гейт: двойной рендер генераторов практик — байт-сверка (аналог yaml_deterministic_output)"/>
</graph>
```

## 3. Канон практик: BASELINE vs FULL

### 3.1 Критерии включения в BASELINE (формализация решения пользователя)

1. **Время:** суммарный overhead цикла правки агента (проверка + возможные правки) ≤ 60s.
2. **Churn = 0:** проверка либо автофиксируемая (hygiene, ruff format), либо детект-класс, который агент чинит один раз и он остаётся зелёным (gitleaks, compose syntax, build).
3. **Запрет:** lint-правила с жёсткими кодинг-стандартами (ruff check, eslint, pyright) — они гоняют агента по правкам каждую сессию → только FULL.
4. **Платформенная безопасность всегда:** L1-класс (секреты в compose, публикация портов, отсутствие healthcheck, env-контракт) исполняется в verify на VPS при ЛЮБОМ уровне — это защита платформы, а не качество проекта.

### 3.2 Состав уровней по языкам

| Проверка | BASELINE | FULL | python | ts/react | sh | Канал | Класс |
|---|---|---|---|---|---|---|---|
| gitleaks | ✅ | ✅ | ✅ | ✅ | ✅ | local, ci | L1 |
| hygiene pre-commit (автофикс) | ✅ | ✅ | ✅ | ✅ | ✅ | local, ci | L3 |
| conventional commits | ✅ | ✅ | ✅ | ✅ | ✅ | local, ci | L3 |
| compose config --quiet | ✅ | ✅ | ✅ | ✅ | ✅ | local, ci, verify | L2 |
| docker build --check | ✅ | ✅ | ✅ | ✅ | ✅ | ci | L2 |
| ruff format --check (автофикс) | ✅ | ✅ | ✅ | — | — | local, ci | L3 |
| shellcheck | ✅ | ✅ | — | — | ✅ | local, ci | L3 |
| pytest (если тесты есть, -x --timeout) | ✅ | ✅ | ✅ | — | — | local, ci | L3 |
| build / tsc --noEmit | ✅ | ✅ | — | ✅ | — | local, ci | L2 |
| **ruff check (полный набор)** | — | ✅ | ✅ | — | — | local, ci | L3 |
| **pyright** | — | ✅ | ✅ | — | — | local, ci | L3 |
| **eslint** | — | ✅ | — | ✅ | — | local, ci | L3 |
| **pytest-full (strict, LDD, Honesty)** | — | ✅ | ✅ | — | — | local, ci | L3 |
| **grep-summary (разметка)** | — | ✅ | ✅ | ✅ | ✅ | local, ci | L3 |
| **drift-gate (GENERATED-файлы)** | — | ✅ | ✅ | ✅ | ✅ | local, ci, verify | L2 |
| **verify-contracts (полный набор)** | — | ✅ | ✅ | ✅ | ✅ | verify | L1 |
| L1-подмножество verify-contracts | ✅ | ✅ | ✅ | ✅ | ✅ | verify | L1 |

**Замечания:**
- BASELINE ≠ «нет защиты»: gitleaks + hygiene + compose-валидность + build + pytest — это уже >80% инцидентов моков (секреты в git, сломанный compose, не собирающийся образ).
- pytest в BASELINE исполняется только если тесты существуют (`allow_no_tests: true`, аналог gates-docker) — мок без тестов не падает.
- `tests/test_health.py` НЕ падает на свежем моке: фикстура делает TCP-probe до сервиса (порт из .env.platform) и `pytest.skip("service not running")` при недоступности — иначе AC1 («project-check зелёный на новом проекте») нарушается.
- FULL добавляет то, что гоняет агента (ruff check, pyright, eslint) и то, что требует дисциплины (LDD, разметка, дрейф-гейт) — именно это нужно долгоживущим проектам.

### 3.3 Рендер `.pre-commit-config.yaml` (только upstream-репозитории)

Pre-commit-конфиг проекта — тонкий GENERATED-файл. **Решение по аудиту 137:** платформенные
shell-хуки (`hooks/hygiene.sh`, `hooks/commit_msg.sh`) ОТКЛОНЕНЫ — они дублировали upstream
`pre-commit-hooks` (trailing-whitespace/end-of-file-fixer/check-merge-conflict/detect-private-key/
check-toml/check-json уже стоят в корневом конфиге платформы), нарушали языковую политику
(новая bash-бизнес-логика = Tier-1 Strangler-триггер) и ссылались на пути `core/`, которых
в репозитории проекта нет. Проект ссылается на те же upstream-репозитории, что и платформа
(версии — из `pins` канона, паритет):

```yaml
# GENERATED by ai-platform practices — DO NOT EDIT (make project-sync-practices)
# Source: core/internal/practices/practices_manifest.yaml v1 (pins)
repos:
  - repo: https://github.com/pre-commit/pre-commit-hooks   # rev: из pins (v6.0.0)
    hooks:
      - id: trailing-whitespace
      - id: end-of-file-fixer
      - id: check-merge-conflict
      - id: detect-private-key
      - id: check-toml
      - id: check-json
  - repo: https://github.com/gitleaks/gitleaks             # rev: из pins (v8.30.1)
    hooks:
      - id: gitleaks          # language: system, entry: gitleaks git --pre-commit
  - repo: https://github.com/compilerla/conventional-pre-commit  # rev: из pins (v4.4.0)
    hooks:
      - id: conventional-pre-commit                       # stages: [commit-msg]
# ─── language-specific (baseline) ───
  - repo: https://github.com/astral-sh/ruff-pre-commit    # rev: из pins (0.16.1)
    hooks:
      - id: ruff-format          # baseline: только format; ruff-check — full-only (project-check/CI)
# ─── sh-only (baseline) ───
  - repo: https://github.com/shellcheck-py/shellcheck-py  # rev: из pins (v0.11.0.1)
    hooks:
      - id: shellcheck
# ─── pre-push: maturity-проверка (K5, решение пользователя 2026-08-05) ───
  - repo: local
    hooks:
      - id: project-push-check
        name: practices maturity + level checks (project-check)
        entry: bash -c 'make project-check PROJECT="$(pwd)"'   # делегирование K1 (PLATFORM_DIR)
        language: system
        stages: [pre-push]
        always_run: true
```

**Поведение pre-push (K5, решение пользователя 2026-08-05):** «дата создания определяется
скриптом по файлам платформы в этом проекте» — `maturity.py` считает возраст по первому
коммиту проекта; fallback — первый коммит, добавивший `ai-platform.yaml`, затем mtime этого
файла. В состоянии `proposed` хук печатает `[PRACTICES:PROPOSE]` + рекомендацию
`make project-set-practices full` — НЕ блокирует push («варнинга хватит»). В `active-full`
L2/L3-нарушения блокируют push.

**Принцип:** хуки — только upstream (org-agnostic, ноль копий платформенного кода); вся
платформенная логика остаётся в Python (`check_project.py`). FULL-уровень добавляет
`ruff-check` — НЕ в pre-commit (он гоняет агента), а только в `project-check`/CI/verify
(см. TRAP[DECISION] §10.2).

### 3.4 Рендер `pyproject.toml` (ruff-конфиг: baseline vs full)

Ruff-конфиг — ключевая развилка скорости. **BASELINE:** `ruff format` (стиль), но `ruff check` отключён (0 правил) → агент не гоняется по правкам. **FULL:** полный набор правил.

```toml
# GENERATED by ai-platform practices — DO NOT EDIT (make project-sync-practices)
[tool.ruff]
line-length = 120
target-version = "py312"

[tool.ruff.format]
quote-style = "double"            # всегда (стиль — дешёвый)

# BASELINE: ruff check НИЧЕГО не делает (только format). Поля ниже в baseline пусты.
# FULL: полный набор правил (комментируется в render_pyproject по level).
[tool.ruff.lint]
# В baseline: select = []  (явно пусто — детект)
# В full:     select = ["E", "F", "I", "B", "UP", "S", "ANN", "D", "RET", "SIM"]
#             ignore = ["S101" (assert в тестах), "D100" (docstring в __init__)]

[tool.pytest.ini_options]
# BASELINE: -q -x --timeout=60, allow_no_tests
# FULL:     + strict-markers, strict-config, filterwarnings=error
addopts = "-q -x --timeout=60"   # baseline; full добавляет --strict-markers --strict-config
markers = []                      # full: канон-маркеры (gate, contract, smoke, ...)
```

**Хук для render_pyproject:**
```python
def render_pyproject(project_name: str, language: str, level: str) -> str:
    if language != "python":
        return ""  # pyproject.toml только для python-проектов
    select_line = (
        'select = ["E","F","I","B","UP","S","ANN","D","RET","SIM"]' if level == "full"
        else "select = []  # baseline: ruff check выключен (только format)"
    )
    addopts_line = (
        '-q -x --timeout=60 --strict-markers --strict-config' if level == "full"
        else '-q -x --timeout=60'
    )
    ...
```

**Templates (template-backend):** `templates/template-backend/pyproject.toml` — GENERATED-заглушка с baseline-конфигом + GENERATED-шапкой. При `make new-project` копируется и становится `pyproject.toml` проекта.

## 4. Эскалатор зрелости (плавное авто-включение через варнинги)

### 4.1 Метрики зрелости (maturity.py)

| Метрика | Источник | Реализация |
|---|---|---|
| `age_days` | дата создания проекта | ЛОКАЛЬНО (K1/K5) и CI (K2, checkout полный): `git log --reverse --format=%aI` первый коммит; fallback — первый коммит, добавивший `ai-platform.yaml` (`git log --follow --diff-filter=A`), затем mtime `ai-platform.yaml` (решение пользователя: «дата создания определяется скриптом по файлам платформы в этом проекте»). На VPS НЕ вычисляется (нет git) — берётся снапшот из practices.lock |
| `code_files` | написанные файлы кода (НЕ библиотеки) | счётчик расширений {py, ts, tsx, js, jsx, sh} в src/, backend/, frontend/, app/, tests/, scripts/, корень; ИСКЛЮЧАЯ: node_modules, .venv, dist, build, coverage, .next, *.lock, package-lock.json, .env*, generated-файлы практик, *.min.js. Локально и в CI; на VPS — из lock |

**Пороги (решение пользователя):** предложить FULL если `age_days > 30` ИЛИ `code_files > 50`.
**Автопромоута НЕТ (решение 2026-08-05):** state меняется ТОЛЬКО локально/CI; на VPS —
только применение lock.

### 4.2 Состояния и переходы

```
baseline ──maturity: age>30d ∨ files>50──► proposed ──согласие (set-practices full)──► active-full
   ▲                                                                                     │
   └────────────────────── set-practices baseline ◄──────────────────────────────────────┘
```

| Состояние | Проверки | Блокировка деплоя | Варнинги агенту |
|---|---|---|---|
| **baseline** | только BASELINE-набор + L1-verify | L1 (безопасность платформы) | нет |
| **proposed** | FULL-набор исполняется, результаты — в отчёты | L1 только; L2/L3 — non-blocking | `[PRACTICES:PROPOSE][level:full]` + рекомендация `make project-set-practices full` в CI summary + verify + AI-PLATFORM.md + pre-push (K5) |
| **active-full** | FULL-набор, все классы | L1+L2+L3 | `[PRACTICES:ACTIVE][level:full]` (информация) |

*Состояния `shadow-full` и `proposed` ранее различались счётчиком автопромоута; автопромоут
отклонён решением пользователя 2026-08-05 («варнинга хватит, мы тут за качество») → они
объединены в `proposed`. State-машина держит 3 состояния явно для аудита и тестируемости.*

### 4.3 Варнинг-предложения агенту (главный механизм «плавного включения»)

**Формат варнинга (аналог M-ADE Envelope):**
```
[PRACTICES:PROPOSE][level:full][reason:age=41d,files=87]
>>> RECOMMEND: make project-set-practices full (или make project-sync-practices для обновления канона)
```

**Каналы доставки варнинга агенту:**
1. **AI-PLATFORM.md** — GENERATED-секция `## Practices`: уровень, зрелость, предложение. Агент проекта читает AI-PLATFORM.md при старте сессии (приоритет инструкций: AGENTS.md проекта → AI-PLATFORM.md → контракт) → варнинг виден до начала работы.
2. **CI job summary** (deploy-project.yml, inline quality-шаги) — markdown-блок в конце прогона + `::warning::` при DRIFT-STALE.
3. **verify output** при деплое (K3) — варнинг в выводе forced-command (платформа логирует в audit_logger).
4. **make project-check** локально — варнинг в выводе.
5. **pre-push hook проекта (K5)** — `[PRACTICES:PROPOSE]` в выводе хука при push (решение пользователя: проверка зрелости локально, до push).

### 4.4 Суперпозиция эскалации (решение)

| Вариант | Механика | Плюсы | Минусы | Вердикт |
|---|---|---|---|---|
| **E1 бинарный switch** | зрелый → full сразу, blocking | просто | резко: агент внезапно получает кучу правил, деплой падает | отклонён |
| **E2 градиент по группам** | каждая группа правил по своему порогу | максимально плавно | оверкилл: пороги на каждую проверку, сложно объяснить | отклонён |
| **E3 opt-in согласие** | только set-practices full вручную | уважает автономию | никогда не включится (никто не помнит) | отклонён |
| **E4 shadow → автопромоут** | full работает non-blocking, потом сам включает | плавно, без действий | нет явного согласия, резкий момент промоута | частично |
| **E5 гибрид (ВЫБРАН, уточнён 2026-08-05)** | baseline → proposed (non-blocking варнинги) → active-full (ТОЛЬКО по согласию, `set-practices full`); L1-класс блокирует всегда; автопромоут ОТКЛОНЁН (решение пользователя: «варнинга хватит» — активный full не включается сам) | плавно + согласие, простота (3 состояния, без счётчиков на ноде) | требует явного действия для включения full (риск E3 принят осознанно) | **принят** |

### 4.5 Политика блокировки (классы L1/L2/L3)

| Класс | Примеры | baseline | proposed | active-full |
|---|---|---|---|---|
| **L1 — безопасность платформы** | секреты в compose/env, публикация портов (только ingress), отсутствие healthcheck, переопределение external-сетей, env-файл не .env.platform | 🔴 блок | 🔴 блок | 🔴 блок |
| **L2 — контракт качества** | compose config невалиден, образ не собирается (build --check), дрейф practices.lock (version) | 🟡 warning | 🟡 warning | 🔴 блок |
| **L3 — код-стандарты** | ruff check, pyright, eslint, LDD, grep-summary, hygiene | — (baseline: только автофикс-часть) | 🟡 warning | 🔴 блок |

*Нюанс: `compose-config` и `build` в BASELINE — не блокируют деплой (L2), а `gitleaks` — блокирует всегда (L1). Проверка принадлежит уровню (baseline/full) И классу (L1/L2/L3) — класс определяет блокировку на деплое, уровень — исполнение локально/в CI.*

### 4.6 State-машина эскалатора (детальная спецификация)

**Входные данные для `escalator.evaluate()`:**
1. `level_setting` из `ai-platform.yaml#quality.level` — `baseline` | `full` | `auto` (default `auto`, решение пользователя 2026-08-05).
2. `maturity` (age_days, code_files) — вычисляется ТАМ, ГДЕ ЕСТЬ GIT: локально (K1/K5) и в CI (K2, checkout полный). На VPS evaluate() НЕ вызывается.

**Таблица переходов (явная):**

| `level_setting` | Текущее `state` | Условие перехода | Новое `state` | warning |
|---|---|---|---|---|
| `baseline` | * | (forced) | `baseline` | нет |
| `full` | * | (forced) | `active-full` | нет |
| `auto` | `baseline` | `age>30 ∨ files>50` | `proposed` | `[PRACTICES:PROPOSE]` |
| `auto` | `proposed` | (stable, до ручного действия) | `proposed` | `[PRACTICES:PROPOSE]` |
| `auto` | `active-full` | (terminal, только вручную) | `active-full` | нет |

**Кто и когда вычисляет state:**
- **Локально (K1/K5):** `make project-check` / `make project-sync-practices` / pre-push хук — maturity из git проекта; результат (state + maturity-снапшот) пишется в practices.lock (`sync_practices`) и коммитится в репозиторий проекта.
- **CI (K2, deploy-project.yml):** checkout полный → maturity вычислима bash-средствами (git log + find, без python3 — гейт no-new-inline-python3) → варнинг-сверка: если `lock.state !=` вычисленный state → `[PRACTICES:DRIFT-STALE]` в summary + `::warning::` + рекомендация `make project-sync-practices`. CI НЕ пишет в lock (GENERATED, коммитится локально).
- **VPS (K3, verify):** НЕ вычисляет maturity и НЕ вызывает evaluate(). Читает `practices.lock` из payload (доставляется receive): L1 — всегда (блок); L2/L3 — по `state` (baseline → warning-only; proposed → warning-only; active-full → блок). Если lock отсутствует (legacy-проект) → grace-режим: L1 warning-only + `[PRACTICES:LEGACY]` (см. TRAP legacy-grace, §10.2). Версия lock < версии канона на ноде → `[PRACTICES:DRIFT-VERSION]` warning.

**Ручной откат:** `make project-set-practices baseline` → `level=baseline` в ai-platform.yaml → evaluate форсирует `state=baseline` (независимо от maturity). Аудит-запись через `core.internal.shared.audit_logger` (единый writer, DevPlan 116 B11 T2).

**Edge case — порог прыгнул сразу:** существующий проект (adopt-project) с возрастом >30 дней при первом же sync получает `state=proposed` сразу — нормально: варнинг PROPOSE отображается, деплой не блокируется.

**Аудит переходов (audit_logger):**
```
event=practices_state_transition project=<name> from=baseline to=proposed reason="age=41d,files=87"
event=practices_state_transition project=<name> from=proposed to=active-full reason="manual:full" auto=false
event=practices_state_transition project=<name> from=proposed to=baseline reason="manual:baseline"
```

### 4.7 CLI-контракты новых глаголов

Каждый новый глагол регистрируется в `core/entrypoint-manifest.yaml#allowed_verbs` (генерируется глоссарий) и получает entry в canonical operations table (core/AGENTS.md):

| Глагол | CLI | exit codes | notes |
|---|---|---|---|
| `project-check` | `python3 -m core.internal.practices.check_project --project-dir <p> [--level <l>] [--fix]` | 0/1/4 | 1=L3/L2-блок в active-full; 4=ConfigValidationError (нет манифеста) |
| `project-fix` | alias: `project-check --fix` | 0/1/4 | применяет auto_fix для проверок с `auto_fix: true` |
| `project-sync-practices` | `python3 -m core.internal.practices.sync_practices --project-dir <p>` | 0/1 | перерендер GENERATED-файлов до канона, обновляет practices.lock |
| `project-set-practices` | `python3 -m core.internal.practices.set_practices --project-dir <p> --level <l>` | 0/1/4 | level ∈ {baseline, full, auto}; пишет ai-platform.yaml#quality.level + lock |

**Имена в `allowed_verbs`:** `_get_all_profiles`, `adopt-project`, ..., `project-check`, `project-fix`, `project-set-practices`, `project-sync-practices` (4 новых; project-lint/project-test НЕ добавляются как отдельные глаголы — это `project-check` с флагами, чтобы не плодить глоссарий).

**Имена в `name_linter.system_exceptions`:** НЕТ — все 4 новых глагола канонические, входят в глоссарий.

## 5. Волны (порядок исполнения)

### W1 — Канон + генераторы + локальный канал (K1)
**Задачи:**
1. `core/internal/practices/` — practices_manifest.yaml (v1, таблица §3.2 + pins + allowed_external_networks) + manifest.py (валидация).
2. `generators.py` — рендер GENERATED-файлов: pyproject.toml (ruff+[tool.pytest] strict-markers), .pre-commit-config.yaml (ТОЛЬКО upstream-хуки, §3.3), tests/conftest.py (чтение .env.platform, health-фикстура с skip при недоступном сервисе), tests/test_health.py (smoke /health+/ready), practices.lock (version, level, state, maturity-снапшот, generated_at, generator_hash).
3. `check_project.py` — исполнение BASELINE по каталогу проекта, `[PRACTICES:...]` вывод, exit 0/1, `--level` override.
4. `sync_practices.py` + `set_practices.py`.
5. makefiles/: `project-check`, `project-fix` (auto_fix-проверки канона), `project-sync-practices`, `project-set-practices` — через PLATFORM_DIR-делегирование (паттерн sync-env). `project-lint`/`project-test` НЕ добавляются как глаголы (см. §4.7).
6. `project_scaffolder.py` шаг 11: генерация baseline-практик при `make new-project` (ВСЕГДА, level=auto; стартовое state=baseline — решение пользователя 2026-08-05: мок ведёт себя как baseline, эскалатор жив).
7. `gen_project_platform_md.py`: Practices-секция в AI-PLATFORM.md.
8. Юнит-тесты: test_practices_generators, test_practices_check_project (мок ≤60s).

**AC W1:** новый проект содержит 5 GENERATED-файлов практик; `make project-check PROJECT=<mock>` зелёный ≤60s (warm) без правок; practices.lock содержит version=1, level=auto, state=baseline; `project-set-practices full` меняет level; entrypoint-manifest + глоссарий обновлены (новые глаголы).

**W1 детальные шаги (чек-лист для Code-субагента):**
1. `core/internal/practices/__init__.py` — пустой (package marker).
2. `practices_manifest.yaml` — таблица §3.2, схема Draft7 в `core/internal/practices/manifest_schema.json` (валидация через `schema_validator.validate_yaml_against_schema`).
3. `manifest.py` — `load_manifest()` + dataclasses (`PracticeCheck`, `PracticesManifest`); константы порогов `MATURITY_AGE_DAYS_PROPOSE=30`, `MATURITY_CODE_FILES_PROPOSE=50` (из канона, НЕ хардкод; автопромоута нет — константа K не существует).
4. `generators.py` — `render_pyproject`/`render_precommit`/`render_conftest`/`render_test_health`/`render_lock` (с maturity-снапшотом); все writes через `shared/atomic_writer.atomic_write_text`; `compute_generator_hash` (§2.1B).
5. pre-push хук проекта (K5) — генерируется в `.pre-commit-config.yaml`: `make project-check` при push, [PRACTICES:PROPOSE] non-blocking, L2/L3-блок только в active-full (§3.3). Платформенные `hooks/hygiene.sh` и `hooks/commit_msg.sh` НЕ создаются (upstream-хуки вместо них).
6. `check_project.py` — CLI `main() -> int`; исполняет проверки из канона по `language`/`level`; L1-проверки ВСЕГДА; exit 0/1/4; LDD [IMP:9] в каждом результате.
7. `sync_practices.py` + `set_practices.py` — паритет `sync_env_defaults.py`: библиотечная функция + CLI.
8. `makefiles/project-practices.mk` (root) + добавить `include` в основной Makefile; добавить делегаты в `templates/template-*/Makefile` (паритет sync-env/status).
9. `project_scaffolder.py` — шаг 11 (после `gen_makefile`/`gen_agents`/`gen_platform_md`): вызов `generators` + `escalator.evaluate` для initial state=baseline; запись в `practices.lock`.
10. `gen_project_platform_md.py` — добавить `PRACTICES_START`/`PRACTICES_END` константы + `render_practices_section()` + replace-section в `generate()`.
11. Шаблоны: `templates/template-backend/{pyproject.toml, .pre-commit-config.yaml, tests/conftest.py, tests/test_health.py, practices.lock}` — GENERATED-заглушки (baseline; .pre-commit-config.yaml — только upstream-хуки, §3.3). Аналогично `template-frontend` (без pyproject, только build/eslint-config) и `template-fullstack` (комбинация).
12. `entrypoint-manifest.yaml` — `allowed_verbs` += 4 глагола; `core/AGENTS.md` регенерируется (`make generate-agents-md`).
13. Юнит-тесты: `test_practices_generators` (детерминизм, hash), `test_practices_check_project` (мок-проект, ≤60s), `test_practices_manifest` (schema valid, id уникальны).

### W2 — CI-канал (K2): quality-шаги в deploy-project.yml (caller-контекст)

**Решение по аудиту 137:** отдельный reusable workflow `quality-gate.yml` и composite action
`setup-project` ОТКЛОНЕНЫ — они дублируют inline-механику deploy-project.yml и плодят второй
источник истины по уровню (дрейф deploy.yml vs ai-platform.yaml). Quality исполняется КАК
ШАГИ существующего single-job `deploy-project.yml`; `level`/`language`/`type` читаются из
`ai-platform.yaml` в РАНТАЙМЕ — шаблоны deploy.yml проектов НЕ меняются (0 новых строк).

**Изменения в `.github/workflows/deploy-project.yml` (после validate, до deliver):**
1. Шаг **Quality lint/test**: python → `pip install pytest pytest-timeout ruff pyyaml` +
   `ruff format --check .` (baseline) / `ruff check .` (full) + `pytest -q -x --timeout=60`
   (exit 5 = no tests → PASS); ts/react → `actions/setup-node@v4` + `npm ci` + `npm run build`
   (+ `npm run lint` в full); sh → `pip install shellcheck-py` + `shellcheck -S error` по
   sh-файлам; gitleaks — binary download pinned v8.30.1 (паритет setup-gitleaks, org-agnostic).
   Все шаги — stdlib actions + CLI, **0 inline python3** (гейт no-new-inline-python3).
   ⚠️ pytest-timeout обязателен в установке (иначе `--timeout=60` → unrecognized arguments).
2. Шаг **Maturity warn** (bash, без python3): `age` из первого коммита, `files` через find →
   если порог превышен и `lock.state != proposed` → `::warning::[PRACTICES:DRIFT-STALE]`
   (lock устарел — запусти `make project-sync-practices` локально); если state=proposed →
   `[PRACTICES:PROPOSE]` + рекомендация `make project-set-practices full` в summary.
3. Шаг **Blocking (full)**: `quality.level == full` из ai-platform.yaml + качество RED →
   `exit 1` (receive НЕ вызывается). `baseline`/`proposed` → деплой идёт с warning (AC W2).
4. Шаг **Deliver**: FILES += `practices.lock` (обязательно — носитель state для K3):
   `[ -f practices.lock ] && FILES="$FILES practices.lock"`.
5. Post-deploy verify — без изменений (verify verb, K3).

**AC W2:** push в main проекта с RED-качеством в full НЕ деплоится (receive не вызван);
в baseline/proposed деплой идёт с warning; deploy.yml шаблонов НЕ изменён; deploy-project.yml
остаётся single-job и org-agnostic (гейт `test_gate_workflow_org_agnostic`).

**Шаблон `ai-platform.yaml` (новая quality-секция):**
```yaml
quality:
  level: auto    # baseline | full | auto (default auto, решение пользователя 2026-08-05)
```

**Паритет-гейт:** `tests/gates/test_gate_workflow_org_agnostic.py` (новый или расширить
существующий) — проверяет 0 платформенных action-литералов и 0 inline python3 в новых
шагах deploy-project.yml (паритет `test_gate_ssh_opts_sole_path` + `no-new-inline-python3`).

### W3 — Эскалатор зрелости + варнинги (K4, K5)
**Задачи:**
1. `maturity.py`: age (первый коммит; fallback — первый коммит ai-platform.yaml, затем mtime), code_files (счётчик с исключениями). Локально + CI; НЕ на VPS.
2. `escalator.py`: 3 состояния (baseline|proposed|active-full), переходы по таблице §4.6, БЕЗ автопромоута (решение пользователя 2026-08-05); аудит-записи переходов (audit_logger).
3. Интеграция: `sync_practices` пишет state + maturity-снапшот в practices.lock; verify (K3) применяет state из lock (без evaluate()); CI (K2) — maturity-warn + [PRACTICES:PROPOSE]/[DRIFT-STALE]; AI-PLATFORM.md Practices-секция рендерит состояние.
4. pre-push хук проекта (K5): `make project-check` на push; [PRACTICES:PROPOSE] non-blocking; L2/L3-блок только в active-full (генерируется в .pre-commit-config.yaml, §3.3).
5. `set_practices` пишет ai-platform.yaml#quality.level (baseline|full|auto); default `auto` — эскалатор решает; `set-practices baseline` — откат-форс.
6. Юнит: test_practices_maturity (пороги 30/50, исключения каталогов, fallback даты по ai-platform.yaml), test_practices_escalator (3 состояния, форс, откат с аудитом, отсутствие автопромоута).

**AC W3:** мок-проект 3 файла — baseline без варнингов; проект 40 дней/87 файлов — [PRACTICES:PROPOSE] в AI-PLATFORM.md + pre-push + CI summary + verify-выводе, деплой НЕ блокируется; set-practices baseline — откат, аудит-запись; state=active-full — ТОЛЬКО после явного set-practices full (никаких авто-переходов).

### W4 — Verify-контракты на VPS (K3)
**Задачи:**
1. `core/internal/deploy/verify_contracts.py`: L1 (секреты в compose/env, ports: → блок, отсутствие healthcheck, external-сети вне канона, env_file ≠ .env.platform, labels platform.*) — ВСЕГДА; L2 (compose config, build --check на ноде, дрейф-version lock) — по классу/уровню; L3 — non-blocking. L1-проверки — инварианты платформы (docs/platform-project-contract.md §2.2: «НЕ публикуй порты», «НЕ поднимай postgres/redis»), переведённые в машиночитаемые проверки.
2. Доставка носителя state: deploy-project.yml шаг Deliver += `practices.lock` (W2, обязательна). verify читает lock из `projects_base() / project`; L2/L3-блокировка — по `state` из lock; отсутствие lock (legacy) → grace-режим (L1 warning-only + `[PRACTICES:LEGACY]`).
3. Интеграция в verify verb (orchestrator_cli dispatch) + audit_logger записи.
4. Повторное использование healthcheck-канона (healthcheck_poller.py — единственный writer docker inspect).
5. Гейт: test_gate_verify_contracts (контракты зарегистрированы, negative-тесты R5).

**AC W4:** деплой проекта с `ports:` в compose — заблокирован при любом уровне; деплой мока без healthcheck — заблокирован; дрейф practices.lock (версия/state) — блок в full, warning в baseline/proposed; внешняя сеть вне allowlist канона — блок при любом уровне; все проверки в audit.

**W4 детальные шаги + интеграция в orchestrator_cli dispatch:**

Текущий `verify` verb маршрутизируется в `core/internal/verify/verify-domains.sh` (HTTPS-проверка домена, DevPlan 125 T1). Расширение: после успешной HTTPS-проверки вызывается `verify_contracts.py` для контрактов проекта.

**Изменение в `orchestrator_cli.py` (dispatch verb=verify):**
```python
# region FUNC_dispatch_verify
elif verb == "verify":
    node = parsed.get("node", "")
    project = parsed.get("project", "")
    # 1. существующая HTTPS-проверка (verify-domains.sh) — НЕ трогаем
    result_https = subprocess.run([_VERIFY_DOMAINS_SH, node, project], ...)
    if result_https.returncode != 0:
        return result_https.returncode
    # 2. NEW: контракты проекта (practices L1/L2/L3)
    from core.internal.deploy.verify_contracts import verify_project_contracts
    report = verify_project_contracts(project_dir=projects_base() / project)
    # log в audit_logger
    audit_logger.log(event="verify_contracts", project=project, findings=report.findings)
    # L1-блок всегда; L2/L3 — по state (из practices.lock)
    if report.has_blocking_violation():
        print(report.format_for_ssh())   # [PRACTICES:BLOCK]...
        return 1
    if report.has_warnings():
        print(report.format_for_ssh())   # [PRACTICES:PROPOSE]/[PRACTICES:LEGACY]...
    return 0
# endregion
```

**`verify_contracts.py` — спецификация контрактов (детально):**

| Контракт (id) | Класс | Проверка | Источник канона |
|---|---|---|---|
| `secrets-in-compose` | L1 | в docker-compose.yml НЕТ литералов `password:`/`api_key:`/`token:` (только `${VAR}`) | grep + yaml parse |
| `ports-published` | L1 | в services НЕ должно быть `ports:` (ingress = nginx платформы); `expose:` — OK | yaml: `services.*.ports` существует → L1 violation |
| `healthcheck-present` | L1 | каждый service имеет `healthcheck:` (или `labels: platform.healthcheck=...`) | yaml: отсутствие → violation; паритет healthcheck_poller.py канону |
| `external-networks` | L1 | `networks.<name>.external: true` — только из канона `allowed_external_networks` (proxy-net, shared-db-net, observability-net, backup-net, hermes-agent-net, shared-cache-net); кастомные external → violation | yaml + allowlist из practices_manifest.yaml (не хардкод) |
| `env-file-contract` | L1 | `env_file:` = `.env.platform` (НЕ `.env`, НЕ абсолютный путь) | yaml |
| `platform-labels` | L1 | `labels: platform.project`, `platform.module` присутствуют | yaml |
| `compose-config-valid` | L2 | `docker compose config --quiet` exit 0 | subprocess (на VPS) |
| `drift-practices` | L2 | на VPS: `practices.lock` отсутствует (legacy) или `lock.version` < версии канона на ноде → `[PRACTICES:DRIFT-VERSION]` warning (файловый дрейф проверяется локально K1 и в CI K2 — там есть полный checkout) | сравнение version (lock — единственный носитель на VPS) |
| `build-check` | L2 | `docker build --check` (если есть Dockerfile) | subprocess (BuildKit статический) |

**Принцип:** L1-контракты — это инварианты платформы (docs/platform-project-contract.md §2.2: «НЕ публикуй порты», «НЕ поднимай postgres/redis»; AGENTS.md root — модель деплоя), переведённые в машиночитаемые проверки. Они исполняются ПРИ ЛЮБОМ уровне практик — это защита платформы, не качество проекта.

**`healthcheck_poller.py` reuse:** контракт `healthcheck-present` НЕ дублирует логику healthcheck_poller — он только проверяет наличие `healthcheck:` ключа в compose (статическая проверка). Runtime healthcheck при деплое — отдельная фаза (deploy_engine), не verify_contracts.

**Negative-тесты R5 (обязательны):**
- `test_verify_contracts_ports_blocked` — compose с `ports: ["8080:80"]` → L1 violation, exit 1.
- `test_verify_contracts_no_healthcheck_blocked` — service без healthcheck → L1 violation.
- `test_verify_contracts_secret_literal_blocked` — `password: hunter2` → L1 violation.
- `test_verify_contracts_drift_full_blocked` — practices.lock отсутствует/устарел (version), state=active-full → L2 блок; state=baseline/proposed → L2 warning (non-blocking).
- `test_verify_contracts_baseline_green` — валидный baseline-проект → 0 violations, exit 0.

### W5 — Гейты платформы + документация + финальный gate
**Задачи:**
1. test_gate_practices_manifest (SoT консистентен), test_gate_practices_generators_deterministic (байт-сверка), test_gate_templates_practices.
2. docs/platform-project-contract.md: секция «Practices: уровни, классы, эскалатор, варнинги».
3. AGENTS.md root: описание модели наследования + новые глаголы (глоссарий генерируется).
4. `make gate MODE=fast` + финальный VerificationReport-материал.

**AC W5:** все гейты зелёные; документация описывает уровни/классы/эскалатор; глоссарий содержит project-* глаголы.

## 6. Файловый манифест

### 6.1 Новые файлы
- `core/internal/practices/__init__.py`
- `core/internal/practices/practices_manifest.yaml` + `manifest_schema.json` (Draft7)
- `core/internal/practices/{manifest.py, maturity.py, escalator.py, generators.py, check_project.py, sync_practices.py, set_practices.py}`
- `core/internal/deploy/verify_contracts.py`
- `tests/unit/test_practices_{maturity,escalator,generators,check_project,sync,set_practices}.py`
- `tests/gates/test_gate_practices_{manifest,templates_practices,generators_deterministic,workflow_org_agnostic}.py`
- `tests/gates/test_gate_verify_contracts.py` (с negative-тестами R5)
- `templates/template-backend/{pyproject.toml, .pre-commit-config.yaml, tests/conftest.py, tests/test_health.py, practices.lock}` (GENERATED-заглушки)
- `templates/template-frontend/{.eslintrc, tsconfig.json, .pre-commit-config.yaml, practices.lock}` (GENERATED-заглушки)
- `templates/template-fullstack/` (комбинация: и python, и TS-файлы)
- `makefiles/project-practices.mk` (root)

*НЕ создаются (отклонены аудитом 137): `core/internal/practices/hooks/{hygiene.sh,commit_msg.sh}`,
`.github/workflows/quality-gate.yml`, `.github/actions/setup-project/action.yml`.*

### 6.2 Изменяемые файлы
- `templates/template-{backend,frontend,fullstack}/ai-platform.yaml` (новая `quality:` секция, level=auto)
- `templates/template-{backend,frontend,fullstack}/.github/workflows/deploy.yml` — **НЕ меняется** (level/language читаются из ai-platform.yaml в рантайме, решение аудита 137)
- `templates/template-{backend,frontend,fullstack}/Makefile` (4 делегата: project-check/fix/sync-practices/set-practices)
- `templates/template-{backend,frontend,fullstack}/AGENTS.md` (ссылка на Practices в AI-PLATFORM.md)
- `templates/template-{backend,frontend,fullstack}/README.md` (таблица практик)
- `core/internal/scaffold/project_scaffolder.py` (шаг 11: генерация практик после gen_platform_md)
- `core/internal/scaffold/gen_project_platform_md.py` (+ PRACTICES_START/END маркеры, render_practices_section)
- `Makefile` (root, include makefiles/project-practices.mk)
- `core/check-suite.yaml` (опционально: project-режим через `--project-dir` флаг в существующих check'ах, или отдельная секция)
- `core/internal/deploy/orchestrator_cli.py` (dispatch verb=verify → вызов verify_contracts после verify-domains)
- `core/entrypoint-manifest.yaml` (allowed_verbs += 4 глагола; gates секция += test_gate_practices_*)
- `docs/platform-project-contract.md` (новая секция «Practices: уровни, классы, эскалатор»)
- `AGENTS.md` (root — описание модели наследования практик)
- `core/AGENTS.md` (регенерируется: 4 новых глагола в canonical operations table)
- `.github/workflows/deploy-project.yml` (inline quality-шаги: lint/test, maturity-warn, blocking(full), Deliver FILES += practices.lock)

### 6.3 Reuse существующих shared-модулей (НЕ дублировать)

| Модуль | Использование в practices |
|---|---|
| `core/internal/shared/atomic_writer.atomic_write_text` | Все writes GENERATED-файлов (единый writer, DevPlan 119 E5) |
| `core/internal/shared/schema_validator.validate_yaml_against_schema` | Валидация practices_manifest.yaml (Draft7, единая точка) |
| `core/internal/shared/project_yaml.load_project_yaml` / `get_name` / `get_target_node` | Чтение ai-platform.yaml проекта (type, target_node, quality.level) |
| `core/internal/shared/audit_logger` | Логирование переходов состояния эскалатора + verify_contracts findings |
| `core/internal/shared/contracts.py` | Exit-коды (0/1/2/3/4/10) — НЕ хардкодить |
| `core/internal/shared/exceptions.py` | `ConfigValidationError` (exit 4) при ошибке манифеста |
| `core/internal/shared/deploy_paths.platform_remote_base` / `projects_base` | Резолв путей в verify_contracts (на VPS) |
| `core/internal/deploy/healthcheck_poller.py` | Канон healthcheck-критерия (НЕ дублировать в verify_contracts) |

**Принцип (языковая политика + DDD):** practices/ — новый домен (наследование практик в проекты). Он НЕ импортирует `core/internal/scaffold/*` напрямую (scaffold — оркестратор, practices — библиотека). scaffold импортирует practices (однонаправленная зависимость: scaffold → practices → shared).

**Удалено из плана (аудит 137):** `deploy_history.py` (источник deploy_count — был нужен автопромоуту; автопромоута нет), `node_yaml` (для deploy_count — счётчиков нет).

## 7. Риски

| Риск | Вероятность | Impact | Митигация |
|---|---|---|---|
| Baseline всё равно гоняет агента (build/tsc в моке) | MED | velocity моков | `allow_no_tests`-аналоги: build исполняется только при наличии package.json с build-скриптом; порог ≤60s — критерий приёмки W1 AC (warm); ruff-check строго в full |
| Дрейф канона практик в шаблонах (копии в 3 шаблонах) | HI | copy-paste debt | GENERATED-файлы рендерятся из practices_manifest (одна SoT); гейт `test_gate_templates_practices`; `project-sync-practices` — единый repair; `test_gate_practices_generators_deterministic` (байт-сверка двойного рендера) |
| Inline quality-шаги deploy-project.yml ломаются в caller-контексте (как deploy-project.yml 2026-08-03) | HI | RED CI без причины | Только stdlib actions + CLI (setup-python@v5, setup-node@v4, pip/npm); 0 relative/qualified платформенных actions; 0 inline python3 (гейт no-new-inline-python3); гейт `test_gate_workflow_org_agnostic`; тестовый прогон в тестовом контексте перед merge W2 |
| Устаревший state в practices.lock (проект не гонял project-check после пересечения порога) | MED | VPS применяет baseline вместо proposed (меньше защиты) | pre-push хук (K5) + CI maturity-warn ([PRACTICES:DRIFT-STALE]) + verify version-warn; worst case — деплой идёт, но с варнингом (не блок, не инцидент) |
| Варнинги игнорируются (шум) | MED | эскалатор не работает | один варнинг-формат, дедупликация (то же состояние → тот же текст); варнинг на границе состояния; active-full возможен только явно — молчаливой деградации нет |
| CI проекта не имеет платформы (файловый дрейф не детектится) | BY DESIGN | дрейф спит до деплоя | quality-шаги самодостаточны (конфиги проекта — единственный источник правил); файловый дрейф — локально (project-check) + CI (maturity-warn); на VPS — version-warn по lock |
| practices.lock не попадёт в payload (регрессия доставки, W2) | LOW | K3 без state → grace | явный шаг Deliver (FILES += practices.lock) + negative-тест на FILES-список + verify: отсутствие lock → [PRACTICES:LEGACY] warning, не молчание |
| verify-контракты L1 ломают легаси-деплои (существующие проекты без practices) | HI | продакшен-блок | L1-класс минимален (6 проверок: secrets/ports/healthcheck/external-networks/env-file/labels); rollout через grace-период (W4 сначала warning-only для проектов БЕЗ practices.lock, потом блок); `make adopt-project` добавляет practices.lock к существующему |
| L1-контракт `healthcheck-present` ломает шаблон с healthcheck в platform-labels | LOW | ложный блок | контракт проверяет ИЛИ `healthcheck:` ключ, ИЛИ `labels: platform.healthcheck=...` (два канона); allowlist для сервисов без healthcheck (например, one-shot jobs) |
| `allowed_external_networks` устареет (новый модуль платформы добавит сеть) | LOW | ложный L1-блок | allowlist — единственная SoT в каноне; гейт `test_gate_practices_manifest` (id/языки/классы) + правило: добавление сети модуля = правка канона в том же PR |
| pyproject.toml / .pre-commit-config.yaml / tests/conftest.py проекта уже существуют (custom) | MED | перезапись пользовательского | generators проверяют GENERATED-шапку: нет шапки → skip + warning `manual file detected`; Practices-секция AI-PLATFORM.md рекомендует merge; adopt-project — предупреждение до перезаписи |
| pre-commit hooks версии расходятся с платформой | LOW | разное поведение линтеров | `pins` в каноне = версиям платформы (gitleaks v8.30.1, ruff 0.16.1, shellcheck v0.11.0.1, pre-commit-hooks v6.0.0, conventional-pre-commit v4.4.0 — все latest на 2026-08-05); `project-sync-practices` обновляет; гейт сверяет pins канона с корневым .pre-commit-config.yaml |
| Cold-start pre-commit / gitleaks system на свежей dev-машине >60s | MED | AC1 нарушен на холодном кэше | AC1 меряется warm (pre-commit установлен); README проекта документирует однократный `make project-sync-practices` + `pre-commit install`; gitleaks — `language: system` (паритет платформе) |

## 8. Промт-шаблон Code-субагента (SC4)

```
Исполни волну W{N} плана .ai/plans/137-project-practices/01-DevPlan.md (§5).
Проверки: per-task make test-summary TEST_FILE=... / make check-diff; фикс-цикл make check до чистоты;
полный make gate MODE=fast в конце волны. Коммиты ≤2: docs(137) / feat(137).
Правила: язык-политика (новый код Python, shell — тонкий фасад), LDD IMP:9 в тестах,
R1-R5 (negative-тесты для каждого бага), GENERATED-файлы НЕ редактируются вручную.
```

## 9. AC глобальные (сводка)

1. `make new-project` → проект с baseline-практиками (level=auto, state=baseline); `make project-check` ≤60s (warm), green, 0 правок агента.
2. Мок (3 файла) — baseline green; зрелый проект (40d/87 файлов) — [PRACTICES:PROPOSE] в AI-PLATFORM.md + pre-push + CI summary + verify, деплой не блокируется.
3. `set-practices full` → ruff check/pyright/eslint/LDD/drift-гейт активны; L1 блокирует всегда; L2/L3 блокируют на VPS в active-full.
4. Автопромоута НЕТ (решение пользователя 2026-08-05): active-full — только по явному согласию; переходы аудируются.
5. Дрейф GENERATED-практик: детект локально (project-check) + CI (maturity-warn) + VPS (version-warn); repair `project-sync-practices`.
6. practices.lock доставляется на VPS payload'ом receive; VPS применяет state без вычисления maturity.
7. Языки python/typescript/react/sh — ветвление по канону (§3.2) работает.
8. Gate платформы зелёный; документация обновлена.

## 10. Открытые вопросы и TRAP-заметки

### 10.1 Открытые вопросы (требуют решения до/во время реализации)

| ID | Вопрос | Контекст | Решение |
|---|---|---|---|
| Q1 | `commit-msg` hook (Conventional Commits) в baseline — не слишком ли жёстко для моков? | Решение пользователя 2026-08-05: baseline = автофикс+разовые детекты. commit-msg — детект (non-auto-fix), но L3 (warning). | **РЕШЕНО:** `conventional-pre-commit` v4.4.0 (upstream, pins канона), L3 (non-blocking); агент видит варнинг, но не блокируется |
| Q2 | `pytest-baseline` в проекте БЕЗ тестов — `exit 5` (pytest no-tests) трактуется как PASS? | `allow_no_tests: true` в каноне (паритет check-suite.yaml gates-docker). | **РЕШЕНО:** да: exit 5 → PASS, мок без тестов не падает |
| Q3 | maturity.code_files для fullstack-проекта (python + TS) — какой language в каноне? | fullstack = 2 языка. checks_for() фильтрует по language tuple. | **РЕШЕНО:** language из ai-platform.yaml#type; для fullstack — обе ветки (python И typescript) исполняются |
| Q4 | verify_contracts на VPS: какой `platform_remote_base()` / `projects_base()` для резолва project_dir? | deploy_paths.platform_remote_base() — канон (DevPlan 125 T3). | **РЕШЕНО:** `projects_base() / project_name`; practices.lock доставляется payload'ом receive (W2: FILES += practices.lock) |
| Q5 | PR-quality workflow для проектов (качество до push в main) | За рамками 137: deploy-project.yml триггерится только push:main. | **ОТЛОЖЕНО:** отдельный PR-workflow с тем же чтением ai-platform.yaml — future, вне скоупа 137 |

### 10.2 TRAP-заметки (для следующего агента-археолога)

⚠️ **TRAP[BUG-risk] · 2026-08-05 · HI · caller-context trap (паритет deploy-project.yml 2026-08-03)**
· Symptom (предсказанный): inline quality-шаги в `deploy-project.yml` падают с `Cannot find module 'core.internal...'` или `Unable to resolve action './.github/actions/...'` в CI проекта.
· Cause: workflow исполняется в контексте caller'а (проекта), где платформы НЕТ. Relative actions резолвятся относительно caller-repo; qualified `org/ai-platform/...` = хардкод org.
· Fix (превентивный): только stdlib-actions (`setup-python@v5`, `setup-node@v4`) + CLI (`pip install pytest pytest-timeout ruff pyyaml`, `npm ci`, gitleaks binary pinned v8.30.1); 0 inline python3 (гейт no-new-inline-python3); конфиги проекта — единственный источник правил. Гейт `test_gate_workflow_org_agnostic` (0 платформенных action-литералов).
· Rev: если появится платформенный composite action, доступный из caller'а через marketplace — пересмотреть.

⚠️ **TRAP[DECISION] · 2026-08-05 · HI · Эскалатор локальный + pre-push (K5); VPS применяет lock; автопромоута НЕТ**
· Rejected: VPS-вычисление maturity (нет git — payload = 4 файла, age/code_files невычислимы); gh-api fallback (токен на ноде, хрупко); автопромоут K=5 (решение пользователя 2026-08-05: «варнинга хватит, мы тут за качество»)
· Reason: решения пользователя 2026-08-05 — maturity только там, где есть git (локально K1/K5 и CI K2, checkout полный); «дата создания определяется скриптом по файлам платформы в этом проекте» (первый коммит, fallback ai-platform.yaml); VPS НЕ вызывает evaluate(), применяет state из practices.lock, доставляемого payload'ом receive.
· Rev: если появится runtime-состояние на ноде (например, счётчики) → отдельный store вне репозитория, НЕ в GENERATED lock.

⚠️ **TRAP[DECISION] · 2026-08-05 · HI · practices.lock доставляется payload'ом receive (FILES += practices.lock)**
· Rejected: VPS-запись в GENERATED lock (дрейф коммиченного файла, сброс при деплое, нарушение DO NOT EDIT); отсутствие доставки вовсе (K3 без носителя state — дрейф-гейт и L2/L3 по state мертвы)
· Reason: lock — снапшот канона + maturity + state, коммитится в репо проекта; verify читает его как есть; VPS не пишет в него (автопромоута нет — счётчики не нужны).
· Rev: —

⚠️ **TRAP[DECISION] · 2026-08-05 · MED · Проектный pre-commit — только upstream-репозитории**
· Rejected: платформенные hooks/{hygiene.sh, commit_msg.sh} (дубли upstream pre-commit-hooks: trailing-whitespace/end-of-file-fixer/check-merge-conflict/detect-private-key/check-toml/check-json уже в корневом конфиге платформы; нарушение языковой политики — новая bash-бизнес-логика = Tier-1 Strangler-триггер; пути `core/` отсутствуют в репозитории проекта → хук падает «executable not found»)
· Reason: генерация конфига со ссылками на upstream (pre-commit-hooks, gitleaks v8.30.1, conventional-pre-commit v4.4.0, ruff-pre-commit 0.16.1, shellcheck-py v0.11.0.1) — org-agnostic, ноль копий; версии из pins канона = версиям платформы (анти-дрейф).
· Rev: —

⚠️ **TRAP[DECISION] · 2026-08-05 · MED · Quality inline в deploy-project.yml вместо reusable workflow**
· Rejected: quality-gate.yml + setup-project action (второй SoT уровня; дрейф deploy.yml vs ai-platform.yaml#quality.level — CI гейтит baseline, а VPS блокирует full → рассинхрон гейтов)
· Reason: level/language/type читаются из ai-platform.yaml в РАНТАЙМЕ single-job workflow — копий уровня нет; шаблоны deploy.yml проектов не меняются; блокировка только при level=full (baseline/proposed — деплой с warning); 0 inline python3.
· Rev: если понадобится quality на PR (до push в main) — отдельный PR-workflow, но с тем же чтением ai-platform.yaml.

⚠️ **TRAP[DECISION] · 2026-08-05 · MED · external-networks allowlist — из канона, не хардкод**
· Rejected: allowlist `["platform","ingress"]` (не совпадает с реальными сетями: шаблоны используют proxy-net/shared-db-net; модули — observability-net/backup-net/hermes-agent-net/shared-cache-net → L1-контракт блокировал бы собственные scaffold-проекты платформы)
· Reason: `allowed_external_networks` в practices_manifest.yaml (единственная SoT); добавление сети модуля = правка канона в том же PR.
· Rev: —

⚠️ **TRAP[BUG-risk] · 2026-08-05 · MED · verify_contracts ломает легаси-деплои**
· Symptom (предсказанный): существующий проект (без practices.lock, с `ports:` в compose) вдруг перестаёт деплоиться после W4.
· Cause: L1-контракты (secrets/ports/healthcheck) начинают блокировать при любом уровне, а старые проекты их не проходят.
· Fix (превентивный): W4 rollout в 2 стадии — (1) L1-контракты в shadow-режиме (warning only) для проектов БЕЗ practices.lock; (2) через 1 спринт — блок. `make adopt-project` добавляет practices.lock существующим проектам. Флаг `--legacy-grace` в verify_contracts (env `PRACTICES_LEGACY_GRACE=1` → L1 warning-only).
· Rev: после миграции всех продакшен-проектов на practices — снять grace-флаг.

⚠️ **TRAP[DECISION] · 2026-08-05 · MED · ruff-check в FULL через pre-commit НЕ включается**
· Rejected: ruff-check в pre-commit hook (риск: каждый коммит гоняет агента по правкам → блокирует velocity даже в full)
· Reason: pre-commit = fast-feedback для форматирования (ruff-format); ruff-check (правила) — только в `make project-check`/CI/verify, где агент может батчем исправить. Паритет с платформой: ruff-check в check-suite.yaml tier=static, НЕ в pre-commit.
· Rev: если full-проект стабильно зелёный на ruff-check > 30 дней → можно добавить в pre-commit как fast-fail.

⚠️ **TRAP[DECISION] · 2026-08-05 · MED · practices.lock — GENERATED, коммитится в git проекта**
· Rejected: practices.lock в .gitignore (риск: дрейф не виден в PR, верификация на VPS ненадёжна)
· Reason: practices.lock = снапшот применённого канона (аналог requirements.txt). Коммит в git → diff в PR показывает изменение уровня/версии; verify на VPS читает committed practices.lock для state-машины эскалатора.
· Rev: если lock-файл станет источником merge-конфликтов (>3 за квартал) — рассмотреть merge-strategy=ours.

### 10.3 Зависимости от других DevPlan

- **DevPlan 133** (platform-project-contract) — `gen_project_platform_md.py` существует, Practices-секция добавляется в него (не новый генератор). `docs/platform-project-contract.md` — канон документа проекта.
- **DevPlan 125 T1** (verify per-project) — verify verb уже поддерживает `<project>` аргумент; verify_contracts вызывается ПОСЛЕ успешной verify-domains.
- **DevPlan 120** (check-suite.yaml) — паттерн SoT-манифеста; practices_manifest.yaml следует той же схеме (version + checks[]).
- **DevPlan 116 B1** (orchestrator_cli dispatch) — verify маршрутизируется через dispatch; verify_contracts вызывается из dispatch verify-ветки.
- **DevPlan 116 T9** (parity-гейты с allowlist) — паттерн для test_gate_practices_* (manifest consistency, generators deterministic, workflow org-agnostic).
- **DevPlan 119 E5** (atomic_write_text единый writer) — все GENERATED-файлы практик пишутся через него.

$END_DEVPLAN
