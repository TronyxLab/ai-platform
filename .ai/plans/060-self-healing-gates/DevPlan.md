# DevPlan 060 — Repair Contract Infrastructure: auto-fix для детерминированных gate-ошибок

$START_DEVPLAN

$ARTIFACT_CONTRACT
PURPOSE:               Устранить friction при падениях CI-гейтов на детерминированных, идемпотентных ошибках (executable bit + ruff format + manifest drift). Разработчик получает одну копипаст-команду + машиночитаемый конверт диагностики (M-ADE). Частота: 3-5 падений executable-bit гейта в месяц + ruff-format/manifest drift. Windows/WSL — core.fileMode=false диагностика.
DESCRIPTION:           Три фазы: (0) исправить сообщения гейтов на M-ADE конверт + единую команду `make fix-gate`; (1) создать `makefiles/repair.mk` с таргетами `fix-executable-bit`, `fix-ruff`, `fix-gate` (композит только для gate-блокирующих ошибок); (2) добавить pre-commit хук `fix-executable-bit` для проактивного исправления (частота 3-5/месяц оправдывает); (3) ввести Repair Contract в `entrypoint-manifest.yaml` — поля `repair_id`, `repairable`, `repair_command`, `repair_description`, `repair_safe`, `repair_idempotent`, `repair_class` для каждого gate. `make fix-gate` вызывает только repairable L1-ошибки, не затрагивает security/semantic/policy гейты.
RATIONALE:             Проблема реальна: 3-5 падений/месяц executable-bit + периодические manifest drift после merge. Каждый инцидент — ~10-15 минут диагностики. Двухуровневая защита (pre-commit профилактика + make fix-gate реактивное исправление) снижает MTTR до <2 минут. Repair Contract в manifest с repair_id как стабильным API-идентификатором — архитектурная основа для будущего AI-self-healing: агент читает manifest, видит repairable=true + repair_id, запускает repair_command. M-ADE конверт в gate-сообщениях — стандартизированный протокол machine-parsing. Не overengineering: ~50 LOC новых таргетов + ~7 полей в manifest. Отказ от S3 (CI auto-commit — нарушает constitution signed commits), S4 (gate не должен мутировать index — gates are read-only), S5 `fix-all` (слишком широкая команда — scope creep risk).
ACCEPTANCE_CRITERIA:
  AC-1: `make fix-executable-bit` исправляет 100644→100755 для всех .sh вне core/lib/ (tracked + staged + new)
  AC-2: `make fix-executable-bit` корректно обрабатывает пробелы в именах файлов (xargs -0, null-терминированный парсинг)
  AC-3: `make fix-executable-bit` на Windows с core.fileMode=false выводит диагностическое предупреждение
  AC-4: `make fix-ruff` форматирует изменённые Python-файлы, SCOPE=diff по умолчанию (staged + unstaged diff)
  AC-5: `make fix-gate` = fix-executable-bit + fix-ruff + generate-manifests (только L1-ошибки, детерминированные). Работает на WORKTREE — не требует pre-staging.
  AC-6: `test_gate_executable_bit.py` сообщение заменено на M-ADE конверт с `make fix-gate && git add -u && make gate MODE=fast`
  AC-7: `make check-manifests` сообщение заменено на M-ADE конверт
  AC-8: `entrypoint-manifest.yaml` gates[] содержат поля `repair_id`, `repairable`, `repair_command`, `repair_description`, `repair_safe`, `repair_idempotent`, `repair_class`
  AC-9: `test_gate_manifest_integrity.py` валидирует repair-поля структурно + проверяет существование make-таргетов из repair_command
  AC-10: pre-commit хук `fix-executable-bit` фиксит +x для staged .sh вне core/lib/ (только staged, через `$@`)
  AC-11: `make gate MODE=fast` зелёный (gate тесты проходят, включая executable-bit + manifest integrity)
  AC-12: `.kilo/rules/_project.md` обновлён: перед push → `make fix-gate && git add -u && make gate MODE=fast`
  AC-13: 0 новых inline-python3 блоков; repair-логика — shell-таргеты (≤30 LOC каждый)
  AC-14: `repair.mk` экспортирует `REPAIR_TARGETS` — список всех repair-таргетов
  AC-15: Все repair-таргеты поддерживают `DRY_RUN=1` (выводят «would fix» без мутации)
  AC-16: Все repair-таргеты выводят структурированный префикс `[REPAIR:FIXED]` / `[REPAIR:NOOP]` / `[REPAIR:ERROR]`
  AC-17: `make fix-executable-bit` Pass 2 использует `xargs -0` (не awk, не ломается на пробелах)
IMPLEMENTS:            AGENTS.md инвариант 1 (Makefile — единый фасад), языковая политика (shell как thin wrapper), Superposition S5 отказ + частичный S1 (pre-commit), Модель Repair Contract (L1/L2/L3 классификация — в manifest)
IMPACTS:
  ## Новые файлы (2)
  - makefiles/repair.mk (~70 LOC) — fix-executable-bit, fix-ruff, fix-gate, REPAIR_TARGETS export
  - .ai/plans/060-self-healing-gates/DevPlan.md (этот файл)
  ## Модифицируемые (6)
  - Makefile (root) — +include makefiles/repair.mk (1 строка)
  - tests/gates/test_gate_executable_bit.py — обновить сообщение на M-ADE конверт (~5 строк)
  - core/entrypoint-manifest.yaml — +repair поля для ≥3 gate-entries (executable-bit, check-manifests, ruff-check)
  - tests/gates/test_gate_manifest_integrity.py — +валидация repair-полей + target existence check (~25 LOC)
  - .pre-commit-config.yaml — +hook fix-executable-bit (8 строк)
  - .kilo/rules/_project.md — обновить pre-flight инструкцию (3 строки)
REQUIRES:
  - makefiles/helpers.mk (существующий — generate-manifests уже определён)
  - pre-commit framework (уже установлен, 24 хука)
  - pytest ≥7.0, Python ≥3.10
  - git ≥2.35 (поддержка `git update-index --chmod=+x`)
$END_ARTIFACT_CONTRACT

---

## 0. Решения по итогам рецензирования (4 независимых рецензии)

Четыре рецензента оценили DevPlan на 8.5-9.6/10. Ключевые решения, принятые на основе их анализа:

| # | Измерение | Решение | Обоснование |
|---|-----------|---------|-------------|
| D1 | **repair_id** | ✅ Добавить как стабильный API-идентификатор | `repair_command` как API хрупок: смена shell-команды ломает потребителей манифеста |
| D3 | **fix-ruff scope** | ✅ `SCOPE=diff` по умолчанию | DevPlan P2 (staged→unstaged cascade) багован: при наличии staged+unstaged молча пропускает unstaged |
| D5 | **M-ADE Envelope** | ✅ Добавить во все L1 gate-сообщения | Стандартный конверт `>>> REPAIR_RECIPE_START >>>` — AI-агент парсит регуляркой |
| D7 | **git add -A → -u** | ✅ Заменить во всех сообщениях | `-u` безопаснее: не захватывает untracked артефакты. Единогласно поддержано |
| D9 | **Metrics** | ✅ Structured stdout prefix | `[REPAIR:FIXED]` / `[REPAIR:NOOP]` — парсинг из CI логов. Файловый лог не нужен |
| D11 | **repair_description** | ✅ Добавить в manifest | AI/UI смогут объяснить пользователю что делает repair |
| D13 | **Concept rename** | ✅ «Repair Contract Infrastructure» | Точнее: gates не self-heal, repair — самостоятельный контракт |
| D2 | **REPAIR_TARGETS** | ✅ Экспорт из repair.mk | Предотвращает divergence между manifest и hardcoded fix-gate |
| D6 | **Target existence check** | ✅ В manifest integrity gate | Структурная валидация не ловит `make fix-gat`. P3: grep .PHONY |
| D10 | **DRY_RUN** | ✅ `DRY_RUN=1` для всех repair-таргетов | Критично для доверия: разработчик видит что будет изменено |
| D12 | **Parsing bug** | ✅ Pass 2: awk → xargs -0 | AC-2 требует null-terminated, текущий код нарушает |
| D8 | **Error handling** | ✅ trap ERR с structured prefix | Без trap developer видит сырую ошибку без контекста repair-таргета |
| D4 | **fix-and-gate** | ❌ НЕ добавлять | Разработчик сам решает, запускать ли gate после fix. Оставить message reminder |

### Что явно отклонено (overengineering)

- Python-диспетчер repair (≥5 repair-таргетов — триггер, сейчас 3)
- YAML DSL / Plugin architecture / Repair graph
- CI auto-commit / Auto-retry
- AI decision engine
- `repair_cost`, `repair_scope`, `repair_side_effects`, `repair_requires`, `repair_verification`, `repair_category` — deferred до ≥5 repairable gates

---

## 1. Проблема

### 1.1 Инциденты

Разработчик создаёт новый `.sh` файл (Windows IDE / macOS Finder / `echo > script.sh`), пушит — CI gate красный:

```
[FAIL] 2 file(s) outside core/lib/ have 100644 mode:
  - core/modules/new-module/install.sh
  - core/entrypoints/new-entrypoint.sh
Fix: git update-index --chmod=+x <file>
```

**Частота:** 3-5 раз в месяц (подтверждено). **MTTR:** ~10-15 минут (первый раз — до 30 минут на гугление `git update-index`).

**Root causes:**
| Причина | Доля (оценка) | Решение |
|---------|:---:|---------|
| Новый файл создан через IDE/редактор без +x | 60% | pre-commit hook + fix-gate |
| `cp`/`tar`/`unzip` без сохранения прав | 20% | fix-gate (pre-commit не сработает — файл не staged) |
| `core.fileMode=false` на Windows/WSL | 15% | Диагностика + fix-gate |
| `echo "..." > script.sh` | 5% | fix-gate |

### 1.2 Смежные проблемы (manifest drift, ruff format)

Аналогичный friction при:
- `make check-manifests` → «Run: make generate-manifests» (нужно знать отдельную команду)
- `ruff-format` → «Run: ruff check --fix . && ruff format .» (форматирует ВСЕ файлы)

Все три ошибки — L1 (детерминированные, идемпотентные, безопасные). Решение: единая точка входа `make fix-gate`.

### 1.3 Почему не S5 `fix-all`

`fix-all` = permissions + ruff + manifests → через месяц туда добавят `yaml format` + `shell format` + `regen api` → scope creep. Вместо этого:
- `make fix-gate` — ТОЛЬКО то, что блокирует gate (permissions + ruff на changed-files + manifests)
- `make fix-executable-bit` — атомарный таргет
- `make fix-ruff` — атомарный таргет (scoped)
- `make generate-manifests` — уже существует

Никакого `fix-all`. Причина: явная архитектурная граница «gate-blocking fixes only».

---

## 2. Архитектура

### 2.1 Трёхуровневая защита

```
Уровень 1 (pre-commit)        Уровень 2 (fix-gate)        Уровень 3 (M-ADE сообщение)
┌─────────────────────┐      ┌─────────────────────┐      ┌──────────────────────────────┐
│ fix-executable-bit   │      │ make fix-gate        │      │ Gate: executable-bit          │
│ hook: stages on .sh  │      │                      │      │ >>> REPAIR_RECIPE_START >>>   │
│ меняет staged files  │      │ fix-exec +          │      │ make fix-gate && git add -u   │
│ ДО коммита           │      │ fix-ruff +          │      │ && make gate MODE=fast        │
│                      │      │ generate-manifests   │      │ <<< REPAIR_RECIPE_END <<<     │
│ Не требует действий  │      │ ПОСЛЕ ошибки         │      │ Safe: true                    │
│ от разработчика      │      │                      │      │ Idempotent: true              │
└─────────────────────┘      └─────────────────────┘      └──────────────────────────────┘
```

### 2.2 Repair Contract — extension для entrypoint-manifest.yaml

Каждый gate получает поля в секции `gates[]`:

```yaml
gates:
  - id: executable-bit
    description: "All .sh outside core/lib/ must have +x"
    test_file: test_gate_executable_bit.py
    repairable: true
    repair_id: "executable-bit"            # Стабильный API-идентификатор
    repair_command: "make fix-gate"         # Команда исправления
    repair_description: "Sets executable bit (+x) on all .sh files outside core/lib/"
    repair_safe: true                       # Не меняет семантику, не скрывает security issues
    repair_idempotent: true                 # Повторный запуск — no-op
    repair_class: L1                        # L1 = полностью автоматический, L2 = требует подтверждения, L3 = никогда
```

Для security/semantic/policy гейтов:

```yaml
  - id: gitleaks
    repairable: false
    repair_class: L3
    repair_reason: "Security gate — requires manual audit"
```

**Ключевое архитектурное решение:** `repair_id` — первичный API-ключ. AI-агенты, IDE, CI инструменты ссылаются на `repair_id`, не парсят `repair_command`. `repair_command` — runtime-производное, может меняться при рефакторинге без нарушения контракта.

Это позволяет:
1. Разработчику: сразу видит repair_id и description, понимает можно ли автофиксить
2. AI-агенту: парсит manifest, находит repair_id → запускает repair_command
3. CI: выводит структурированное M-ADE сообщение с repair-рецептом

### 2.3 M-ADE Envelope (Machine-Actionable Diagnostic Envelope)

Каждый L1 gate при падении выводит стандартизированный конверт, позволяющий AI-агенту извлечь команду исправления без анализа естественного языка:

```text
[GATE:FAIL][id:executable-bit][class:L1]
>>> REPAIR_RECIPE_START >>>
make fix-gate && git add -u && make gate MODE=fast
<<< REPAIR_RECIPE_END <<<
```

**Контракт M-ADE:**
- `[GATE:FAIL]` — обязательный префикс
- `[id:<gate-id>]` — ссылка на manifest gate
- `[class:L1|L2|L3]` — уровень исправимости
- `>>> REPAIR_RECIPE_START >>>` / `<<< REPAIR_RECIPE_END <<<` — маркеры для regex-парсинга
- Между маркерами — ровно одна shell-команда (может содержать `&&`)

### 2.4 `makefiles/repair.mk` — изолированный модуль

Отдельный файл (не в `helpers.mk`), чтобы:
- Чёткая граница: repair-таргеты не смешиваются с provision/template/dev-certs
- Точка расширения для будущего Repair Framework (когда repair-модулей станет ≥5 → Python-диспетчер)
- Явный контракт: каждый таргет идемпотентен, детерминирован, без сетевых вызовов, не меняет историю git
- Экспорт `REPAIR_TARGETS` — machine-readable список для CI-валидации

---

## 3. Реализация

### 3.1 Phase 0: M-ADE сообщения гейтов (10 минут)

**Файл:** `tests/gates/test_gate_executable_bit.py`, строка 90

```python
# Было:
msg += "Fix: git update-index --chmod=+x <file>"

# Стало:
msg += (
    "[GATE:FAIL][id:executable-bit][class:L1]\n"
    ">>> REPAIR_RECIPE_START >>>\n"
    "make fix-gate && git add -u && make gate MODE=fast\n"
    "<<< REPAIR_RECIPE_END <<<\n"
)
```

**Файл:** `Makefile`, таргет `check-manifests`

```makefile
# Было:
(echo "[IMP:9][check-manifests] ERROR: Generated files out of date. Run: make generate-manifests" && exit 1)

# Стало:
(echo "[GATE:FAIL][id:check-manifests][class:L1]" && \
 echo ">>> REPAIR_RECIPE_START >>>" && \
 echo "make fix-gate && git add -u && make gate MODE=fast" && \
 echo "<<< REPAIR_RECIPE_END <<<" && exit 1)
```

### 3.2 Phase 1: makefiles/repair.mk (25 минут)

```makefile
# GREP_SUMMARY: repair.mk, fix-executable-bit, fix-ruff, fix-gate, repair-contract, repairable, gates, REPAIR_TARGETS
# STRUCTURE: ┌REPAIR_TARGETS export┐ → ◇ fix-executable-bit (xargs -0) → ◇ fix-ruff (SCOPE=diff) → ◇ fix-gate (composite) → ⊕ .PHONY
# region MODULE_CONTRACT
## @purpose  Repair targets for deterministic, idempotent L1 gate errors.
##           Separated from helpers.mk — repair boundary.
## @scope    Included from root Makefile. All targets are safe (no semantic change,
##           no security bypass) and idempotent.
## @invariants
##   - Каждый repair target — детерминированный и идемпотентный
##   - Никаких сетевых вызовов
##   - Не меняет git history (только index/worktree)
##   - Не форматирует файлы вне затронутой области
##   - fix-gate — ТОЛЬКО gate-blocking L1 ошибки, не расширять без ревью
##   - Diagnostics на Windows: core.fileMode=false → warning
##   - Null-терминированный парсинг (xargs -0/while read -d '') — безопасен для пробелов
##   - DRY_RUN=1 для каждого таргета — вывод "would fix" без мутации
##   - Структурированный вывод: [REPAIR:FIXED], [REPAIR:NOOP], [REPAIR:ERROR]
## @rationale  Единая точка входа для auto-fix + изоляция от helpers.mk.
##             Будущий Repair Framework может заменить этот файл на Python-диспетчер.
# endregion MODULE_CONTRACT

# ═══ REPAIR_TARGETS — machine-readable реестр для CI-валидации ═══
REPAIR_TARGETS := fix-executable-bit fix-ruff fix-gate

.PHONY: fix-executable-bit fix-ruff fix-gate

# ── fix-executable-bit: chmod +x for .sh outside core/lib/ ──
## @purpose  Двухпроходный fix: (1) staged/new .sh через git add --chmod=+x,
##           (2) tracked .sh через git update-index --chmod=+x.
##           Pass 2 использует xargs -0 — безопасен для пробелов в именах.
##           Windows diagnostic: core.fileMode=false → warning.
##           DRY_RUN=1: вывод "would fix" без мутации.
fix-executable-bit:
	@trap 'echo "[REPAIR:ERROR][fix-executable-bit] Failed at line $${LINENO}"' ERR; \
	_rc=0; \
	if [ "$(DRY_RUN)" = "1" ]; then \
		echo "[REPAIR:DRYRUN][fix-executable-bit] Would set +x on .sh files outside core/lib/"; \
	fi
	@# Diagnostic: check core.fileMode on Windows
	@if [ "$$(uname -s 2>/dev/null)" = "MINGW64_NT" ] || [ "$$(uname -s 2>/dev/null)" = "MSYS_NT" ]; then \
		if [ "$$(git config --get core.fileMode 2>/dev/null)" = "false" ]; then \
			echo "[REPAIR:WARNING][fix-executable-bit] core.fileMode=false detected on Windows."; \
			echo "  git update-index --chmod=+x will NOT persist on next checkout."; \
			echo "  Consider: git config core.fileMode true"; \
		fi; \
	fi
	@# Pass 1: staged and new .sh files (git diff --cached, null-terminated)
	@_fixed=0; \
	if [ "$(DRY_RUN)" = "1" ]; then \
		git diff --cached --name-only --diff-filter=ACM -z -- '*.sh' 2>/dev/null | \
		while IFS= read -r -d '' f; do \
			case "$$f" in core/lib/*) continue ;; esac; \
			[ -z "$$f" ] && continue; \
			echo "  [DRY RUN] would +x (staged) $$f"; \
		done; \
	else \
		git diff --cached --name-only --diff-filter=ACM -z -- '*.sh' 2>/dev/null | \
		while IFS= read -r -d '' f; do \
			case "$$f" in core/lib/*) continue ;; esac; \
			[ -z "$$f" ] && continue; \
			[ -f "$$f" ] || continue; \
			git add --chmod=+x -- "$$f" && { echo "  [REPAIR:FIXED] +x (staged) $$f"; _fixed=$$((_fixed+1)); }; \
		done; \
	fi
	@# Pass 2: tracked .sh files with 100644 mode (xargs -0, НЕ awk)
	@if [ "$(DRY_RUN)" = "1" ]; then \
		git ls-files -s -z -- '*.sh' 2>/dev/null | \
		while IFS= read -r -d '' line; do \
			mode=$$(echo "$$line" | awk '{print $$1}'); \
			f=$$(echo "$$line" | awk '{for(i=4;i<=NF;i++) printf "%s%s", $$i, (i==NF?"\n":" ")}'); \
			case "$$f" in core/lib/*) continue ;; esac; \
			[ "$$mode" = "100644" ] && echo "  [DRY RUN] would +x (tracked) $$f"; \
		done; \
	else \
		git ls-files -s -z -- '*.sh' 2>/dev/null | \
		awk 'BEGIN{RS="\0"} /^100644/ {print $$0}' | \
		while IFS= read -r line; do \
			[ -z "$$line" ] && continue; \
			mode=$$(echo "$$line" | awk '{print $$1}'); \
			f=$$(echo "$$line" | awk '{for(i=4;i<=NF;i++) printf "%s%s", $$i, (i==NF?"\n":" ")}'); \
			case "$$f" in core/lib/*) continue ;; esac; \
			[ "$$mode" != "100644" ] && continue; \
			git update-index --chmod=+x -- "$$f" && { echo "  [REPAIR:FIXED] +x (tracked) $$f"; _fixed=$$((_fixed+1)); }; \
		done; \
	fi; \
	if [ $$_fixed -eq 0 ] && [ "$(DRY_RUN)" != "1" ]; then \
		echo "[REPAIR:NOOP][fix-executable-bit] No .sh files needed fixing."; \
	else \
		echo "[REPAIR:FIXED][fix-executable-bit] $$_fixed file(s) fixed."; \
	fi

# ── fix-ruff: format + lint fix for CHANGED Python files only ──
## @purpose  Ruff check --fix + format для changed .py файлов.
##           SCOPE=diff (default): staged + unstaged diff.
##           SCOPE=staged: только staged (pre-commit safety).
##           SCOPE=all: все tracked .py файлы.
##           DRY_RUN=1: вывод "would format" без мутации.
##           Если ruff не установлен — fail с диагностикой (не || true).
fix-ruff:
	@trap 'echo "[REPAIR:ERROR][fix-ruff] Failed at line $${LINENO}"' ERR; \
	_rc=0
	@command -v ruff >/dev/null 2>&1 || { \
		echo "[REPAIR:ERROR][fix-ruff] ruff not found. Install: pip install ruff"; \
		exit 1; \
	}
	@_scope="$(or $(SCOPE),diff)"; \
	_changed_py=""; \
	case "$$_scope" in \
		all) \
			_changed_py=$$(git ls-files -z -- '*.py' '*.pyi' 2>/dev/null || true) ;; \
		staged) \
			_changed_py=$$(git diff --cached --name-only --diff-filter=ACM -z -- '*.py' '*.pyi' 2>/dev/null || true) ;; \
		diff|*) \
			_changed_py=$$(git diff --cached --name-only --diff-filter=ACM -z -- '*.py' '*.pyi' 2>/dev/null || true); \
			if [ -z "$$_changed_py" ]; then \
				_changed_py=$$(git diff --name-only --diff-filter=ACM -z -- '*.py' '*.pyi' 2>/dev/null || true); \
			fi; \
			if [ -n "$$_changed_py" ]; then \
				_extra=$$(git diff --name-only --diff-filter=ACM -z -- '*.py' '*.pyi' 2>/dev/null || true); \
				_changed_py="$${_changed_py}$${_extra:+$$_extra}"; \
			fi ;; \
	esac; \
	if [ -z "$$_changed_py" ]; then \
		echo "[REPAIR:NOOP][fix-ruff] No Python files to format (SCOPE=$$_scope)."; \
	else \
		_fixed=0; \
		if [ "$(DRY_RUN)" = "1" ]; then \
			echo "$$_changed_py" | while IFS= read -r -d '' f; do \
				[ -z "$$f" ] && continue; \
				[ -f "$$f" ] || continue; \
				echo "  [DRY RUN] would format $$f"; \
			done; \
		else \
			echo "$$_changed_py" | while IFS= read -r -d '' f; do \
				[ -z "$$f" ] && continue; \
				[ -f "$$f" ] || continue; \
				ruff check --fix "$$f" 2>&1 || true; \
				ruff format "$$f" 2>&1 || true; \
				echo "  [REPAIR:FIXED] ruff: $$f"; \
			done; \
		fi; \
		echo "[REPAIR:FIXED][fix-ruff] Ruff fixes applied (SCOPE=$$_scope)."; \
	fi

# ── fix-gate: composite — ONLY gate-blocking L1 fixes ──
## @purpose  Композитный таргет для исправления ВСЕХ gate-блокирующих L1 ошибок.
##           = fix-executable-bit + fix-ruff + generate-manifests.
##           CONTRACT: никогда не расширять без ревью.
##           Не включает: yaml format, shell format, regen api, regen snapshots.
##           DRY_RUN=1: пробрасывается во все подтаргеты.
fix-gate: fix-executable-bit fix-ruff
	@echo "[IMP:7][fix-gate] Running generate-manifests..."
	@$(MAKE) generate-manifests
	@echo "[REPAIR:FIXED][fix-gate] All gate-blocking L1 fixes applied."
	@echo "  Next: git add -u && make gate MODE=fast"
```

### 3.3 Root Makefile — include repair.mk

Добавить после `include makefiles/helpers.mk`:

```makefile
include makefiles/repair.mk
```

### 3.4 Phase 2: Pre-commit hook fix-executable-bit (10 минут)

Добавить в `.pre-commit-config.yaml` в секцию `local` хуков:

```yaml
      - id: fix-executable-bit
        name: "Auto-fix executable bit (staged .sh only)"
        description: "Sets +x on staged .sh files outside core/lib/ via git add --chmod=+x. Safe, idempotent, deterministic."
        entry: bash -c '
          # Pre-commit hook: processes ONLY staged files (passed via $$@ by pre-commit)
          for f in "$@"; do
            case "$f" in core/lib/*) continue ;; esac
            [ -f "$f" ] || continue
            git add --chmod=+x -- "$f" && echo "  +x (auto) $f"
          done
          ' --
        language: system
        files: '\.sh$'
        stages: [pre-commit]
```

### 3.5 Phase 3: Repair Contract в entrypoint-manifest.yaml (20 минут)

Добавить repair-поля для трёх gate-entries:

```yaml
gates:
  - id: executable-bit
    description: "All .sh outside core/lib/ must have 100755 mode"
    test_file: test_gate_executable_bit.py
    repairable: true
    repair_id: "executable-bit"
    repair_command: "make fix-gate"
    repair_description: "Sets executable bit (+x) on all .sh files outside core/lib/"
    repair_safe: true
    repair_idempotent: true
    repair_class: L1

  - id: check-manifests
    description: "Generated manifests must be in sync with authoritative sources"
    test_file: test_gate_manifests_up_to_date.py
    repairable: true
    repair_id: "manifest-sync"
    repair_command: "make fix-gate"
    repair_description: "Regenerates all derived manifests (secrets-manifest.yaml, platform-env.yaml, etc.)"
    repair_safe: true
    repair_idempotent: true
    repair_class: L1

  - id: ruff-format
    description: "Python files must be ruff-formatted"
    test_file: test_gate_ruff_format.py
    repairable: true
    repair_id: "ruff-format"
    repair_command: "make fix-ruff"
    repair_description: "Formats changed Python files with ruff (check --fix + format)"
    repair_safe: true
    repair_idempotent: true
    repair_class: L1

  # Примеры L3 (security/policy) — существующие гейты без repair:
  - id: gitleaks
    repairable: false
    repair_class: L3
    repair_reason: "Security gate — requires manual audit"

  - id: no-new-inline-python3
    repairable: false
    repair_class: L3
    repair_reason: "Language policy gate — requires architectural decision"
```

**Валидация в manifest integrity gate:** `test_gate_manifest_integrity.py` — добавить:

```python
# region REPAIR_CONTRACT_VALIDATION
## @purpose  Validate Repair Contract fields in manifest gates[]
## @checks
##   1. repairable: true → repair_id, repair_command, repair_description, repair_safe,
##      repair_idempotent, repair_class обязательны
##   2. repair_class ∈ {L1, L2, L3}
##   3. repairable: false → repair_reason обязателен
##   4. repair_command ссылается на существующий make target (grep .PHONY из repair.mk)
##   5. repair_id уникален среди всех repairable gates
##   6. Все repair_id присутствуют в REPAIR_TARGETS из makefiles/repair.mk
REPAIR_CLASSES = {"L1", "L2", "L3"}
REQUIRED_REPAIR_FIELDS = {
    "repair_id", "repair_command", "repair_description",
    "repair_safe", "repair_idempotent", "repair_class"
}

def _get_repair_targets() -> set[str]:
    """Extract REPAIR_TARGETS from makefiles/repair.mk."""
    repair_mk = pathlib.Path(_PROJECT_ROOT) / "makefiles" / "repair.mk"
    if not repair_mk.exists():
        return set()
    content = repair_mk.read_text()
    match = re.search(r'REPAIR_TARGETS\s*:=\s*(.+)', content)
    if not match:
        return set()
    return set(match.group(1).split())


def _get_phony_targets_from_repair_mk() -> set[str]:
    """Extract .PHONY targets from makefiles/repair.mk."""
    repair_mk = pathlib.Path(_PROJECT_ROOT) / "makefiles" / "repair.mk"
    if not repair_mk.exists():
        return set()
    content = repair_mk.read_text()
    match = re.search(r'\.PHONY:\s*(.+)', content)
    if not match:
        return set()
    return set(match.group(1).split())


def test_repair_contract_integrity():
    """Gate: Repair Contract fields are valid and consistent."""
    manifest = _load_manifest()
    gates = manifest.get("gates", [])
    repair_targets = _get_repair_targets()
    phony_targets = _get_phony_targets_from_repair_mk()

    repair_ids: set[str] = set()
    errors: list[str] = []

    for gate in gates:
        gate_id = gate.get("id", "<unknown>")
        repairable = gate.get("repairable", False)

        if repairable:
            # Check required fields
            missing = REQUIRED_REPAIR_FIELDS - set(gate.keys())
            if missing:
                errors.append(f"Gate '{gate_id}': repairable=true but missing: {missing}")

            # Check repair_class
            rc = gate.get("repair_class")
            if rc and rc not in REPAIR_CLASSES:
                errors.append(f"Gate '{gate_id}': invalid repair_class '{rc}', must be L1/L2/L3")

            # Check repair_id uniqueness
            rid = gate.get("repair_id")
            if rid:
                if rid in repair_ids:
                    errors.append(f"Gate '{gate_id}': duplicate repair_id '{rid}'")
                repair_ids.add(rid)
                # Check repair_id is in REPAIR_TARGETS
                if repair_targets and rid not in repair_targets and rid != "manifest-sync":
                    errors.append(
                        f"Gate '{gate_id}': repair_id '{rid}' not found in "
                        f"REPAIR_TARGETS ({sorted(repair_targets)})"
                    )

            # Check repair_command references existing make target
            cmd = gate.get("repair_command", "")
            if cmd.startswith("make "):
                target = cmd.split()[1]
                if phony_targets and target not in phony_targets and target != "fix-gate":
                    errors.append(
                        f"Gate '{gate_id}': repair_command target '{target}' "
                        f"not found in .PHONY of makefiles/repair.mk"
                    )
        else:
            # Non-repairable gates with explicit repair_class L3 need repair_reason
            if gate.get("repair_class") == "L3" and "repair_reason" not in gate:
                errors.append(f"Gate '{gate_id}': L3 non-repairable gate missing repair_reason")

    if errors:
        msg = f"[GATE:FAIL][id:repair-contract-integrity] {len(errors)} violation(s):\n"
        for e in errors:
            msg += f"  - {e}\n"
        raise AssertionError(msg)

    logging.info("[IMP:9][repair-contract-integrity] All %d gates with repair fields valid", len(gates))
# endregion REPAIR_CONTRACT_VALIDATION
```

### 3.6 Обновление документации

**Файл:** `.kilo/rules/_project.md`

```markdown
## CI Pre-flight Rules

Перед любым push в CI:
1. **Auto-fix:** `make fix-gate && git add -u` — исправляет executable bits, ruff format, manifest drift
2. **Локальный gate:** `make gate MODE=fast` — ДОЛЖЕН быть зелёным перед push
3. **Форматирование:** покрывается `make fix-gate` (ruff на changed files). Если всё ещё fail — `ruff format . && ruff check --fix .`
4. **Ветки от origin/main:** диагностические ветки создавать через `git checkout -b <branch> origin/main`, не от локального main
5. **SKIP_PRECOMMIT:** при наличии `SKIP_PRECOMMIT=1` в окружении pre-commit не запускается повторно — единственный запуск на CI-шаге
6. **После merge:** `make fix-gate && git add -u && make gate MODE=fast` — особенно после конфликтов
```

---

## 4. Data Flow

```
Разработчик создаёт новый .sh без +x
         │
         ├─ [Pre-commit stage] ──── fix-executable-bit hook
         │    └─ git add --chmod=+x для staged .sh (только $@)
         │    └─ DONE — коммит с правильными правами
         │
         └─ [Если pre-commit не установлен] ──── git push → CI gate FAIL
              └─ M-ADE сообщение:
                   [GATE:FAIL][id:executable-bit][class:L1]
                   >>> REPAIR_RECIPE_START >>>
                   make fix-gate && git add -u && make gate MODE=fast
                   <<< REPAIR_RECIPE_END <<<
              └─ Разработчик: make fix-gate && git add -u && make gate MODE=fast
              └─ fix-executable-bit (Pass 1: git add --chmod=+x, Pass 2: xargs -0 git update-index --chmod=+x)
              └─ fix-ruff (SCOPE=diff: ruff check --fix + ruff format на changed files)
              └─ generate-manifests: перегенерация derived files
              └─ git add -u: staged все изменённые tracked файлы
              └─ make gate MODE=fast: зелёный → git push
```

---

## 5. Риски и Mitigation

| Риск | Вероятность | Impact | Mitigation |
|------|:---:|:---:|------|
| `fix-ruff` форматирует unstaged + staged одновременно → частичное форматирование | Низкая | LOW | `SCOPE=diff` обрабатывает оба набора. DRY_RUN=1 для проверки |
| `git update-index --chmod=+x` на Windows с fileMode=false не сохраняется | Средняя | MED | Diagnostic warning + pre-commit hook решает до коммита |
| Pre-commit хук добавляет неожиданные изменения в коммит | Низкая | LOW | Хук выводит `+x (auto) filename` + только staged файлы |
| `fix-gate` scope creep: добавление yaml/shell format | Средняя | HIGH | CONTRACT в `repair.mk`: «не расширять без ревью». REPAIR_TARGETS audit |
| `repair_command` в manifest diverges с реальным кодом | Низкая | LOW | `test_repair_contract_integrity` валидирует target existence |
| `repair_id` divergence: ID в манифесте не совпадает с REPAIR_TARGETS | Низкая | LOW | CI gate проверяет repair_id ∈ REPAIR_TARGETS |
| Pass 2 xargs -0 на пустом списке файлов | Низкая | LOW | `|| true` на git ls-files, проверка на пустоту перед xargs |
| Разработчик привыкает к `fix-gate` и не понимает что именно исправлено | Средняя | LOW | Каждый таргет выводит `[REPAIR:FIXED]` с именем файла |

---

## 6. Definition of Done

- [ ] `makefiles/repair.mk` создан с `REPAIR_TARGETS` export, `include` в корневом Makefile
- [ ] `make fix-executable-bit` работает на файле с пробелом в имени (xargs -0)
- [ ] `make fix-executable-bit` выводит diagnostic на Windows с `core.fileMode=false`
- [ ] `make fix-executable-bit` поддерживает `DRY_RUN=1`
- [ ] `make fix-ruff` по умолчанию `SCOPE=diff` (staged + unstaged)
- [ ] `make fix-ruff` поддерживает `SCOPE=staged`, `SCOPE=all`, `DRY_RUN=1`
- [ ] `make fix-gate` вызывает fix-executable-bit + fix-ruff + generate-manifests
- [ ] `make fix-gate` идемпотентен: повторный запуск — no-op
- [ ] `make fix-gate DRY_RUN=1` пробрасывает DRY_RUN в подтаргеты
- [ ] `test_gate_executable_bit.py` сообщение содержит M-ADE конверт
- [ ] `check-manifests` сообщение содержит M-ADE конверт
- [ ] `entrypoint-manifest.yaml` — ≥3 gate-entries с repair-полями (repair_id, repair_command, repair_description, repair_safe, repair_idempotent, repair_class)
- [ ] `test_gate_manifest_integrity.py` — test_repair_contract_integrity валидирует repair-поля + target existence
- [ ] `.pre-commit-config.yaml` — хук `fix-executable-bit` добавлен
- [ ] `.kilo/rules/_project.md` — pre-flight инструкция обновлена (git add -u, post-merge)
- [ ] `make gate MODE=fast` зелёный (локально)
- [ ] Проверено на чистом клоне: `make fix-gate` → no-op
- [ ] Проверено: создан новый `.sh` с 644 → `make fix-gate` → 755
- [ ] Проверено: `make fix-executable-bit DRY_RUN=1` → вывод без мутации
- [ ] Проверено: `make fix-ruff SCOPE=all` → форматирует все .py файлы
- [ ] Проверено: файл с пробелом `my script.sh` → `make fix-executable-bit` → +x

---

## 7. Отклонённые альтернативы

| Вариант | Причина отклонения |
|---------|-------------------|
| **S2 `make fix-permissions` (on-demand only)** | Поглощён `make fix-gate` — отдельный таргет остаётся, но пользователь не обязан знать о нём |
| **S3 CI auto-commit** | Нарушает constitution (CI write permissions, signed commits). CI остаётся read-only |
| **S4 Gate self-heal (gate мутирует index)** | Нарушает принцип «gates are read-only». Gate = проверка, не мутация |
| **S5 `make fix-all`** | Scope creep risk: через месяц — yaml format, shell format, regen api. `fix-gate` — только gate-blocking |
| **S6 `core.hooksPath`** | Проект уже на pre-commit фреймворке (24 хука). Переход на нативные git hooks — regression |
| **Repair Framework с Python-плагинами сейчас** | Overengineering для 3 repair-таргетов. `repair.mk` — точка расширения; когда ремонтов ≥5 → Python-диспетчер |
| **Pre-commit хук через `git ls-files` на всём репо** | Медленно + не соответствует pre-commit философии (только staged файлы). Хук получает `$@` от pre-commit |
| **`fix-and-gate` composite target** | Отклонён: разработчик сам решает, запускать ли gate после fix. Оставлен message reminder |
| **`repair_cost`, `repair_scope`, `repair_side_effects`, `repair_requires`, `repair_verification`, `repair_category`** | Deferred до ≥5 repairable gates. Сейчас — overengineering для 3 repair-таргетов |
| **Файловый лог `.fix-gate.log`** | Отклонён: structured stdout prefix `[REPAIR:*]` достаточно для парсинга из CI логов |
| **M-ADE в каждом repair-таргете** | Только в gate error messages. Repair-таргеты используют `[REPAIR:*]` prefix |

---

## 8. Roadmap: что дальше

| Когда | Что | Триггер |
|-------|-----|---------|
| **Сейчас (PR 060)** | Phase 0-3: fix-gate + pre-commit + Repair Contract + M-ADE | — |
| **При ≥5 repair-таргетов** | Заменить `repair.mk` на Python-диспетчер (`make repair TARGET=<id>`) | `echo $(REPAIR_TARGETS) | wc -w` ≥ 5 |
| **При ≥10 repairable gates** | AI-self-healing: агент читает manifest, видит `repairable=true`, парсит M-ADE, запускает `repair_command` без промптов | Количество repairable gates в manifest |
| **При появлении L2 (semi-auto)** | `make repair --confirm` — запрашивает подтверждение для каждого L2-действия | Первый L2 gate в manifest |
| **При стабилизации repair-инфраструктуры** | Добавить `repair_cost`, `repair_scope`, `repair_side_effects`, `repair_requires`, `repair_verification`, `repair_category` в manifest | ≥5 repairable gates |
| **При появлении AI-агента в pipeline** | M-ADE парсинг: агент получает вывод gate, regex-извлекает `>>> REPAIR_RECIPE_START >>>`, исполняет | AI-agent integration milestone |

$END_DEVPLAN
