# GREP_SUMMARY: repair.mk, fix-executable-bit, fix-ruff, fix-pycache, fix-gate, repair-contract, repairable, gates, REPAIR_TARGETS, M-ADE, check, check-diff
# STRUCTURE: ┌REPAIR_TARGETS export┐ → ◇ fix-executable-bit (Python-порт, Strangler T3.4) → ◇ fix-ruff (batch ruff, SCOPE=diff) → ◇ fix-pycache (__pycache__ cleanup, 172 W1.5) → ◇ fix-gate (composite) → ◇ check (SoT-executor) → ◇ check-diff (diff-скоуп) → ⊕ .PHONY
# region MODULE_CONTRACT
## @purpose  Repair targets for deterministic, idempotent L1 gate errors + диагностический
##           check/check-diff (DevPlan 120).
## @scope    Included from root Makefile. All targets are safe (no semantic change,
##           no security bypass) and idempotent.
## @invariants
##   - Каждый repair target — детерминированный и идемпотентный
##   - check/check-diff НЕ содержат hardcoded-списков проверок — делегируют check_suite
##     (единственный источник — core/check-suite.yaml, AC-1 DevPlan 120)
##   - Никаких сетевых вызовов
##   - Не меняет git history (только index/worktree)
##   - fix-gate — ТОЛЬКО gate-blocking L1 ошибки, не расширять без ревью
##   - Diagnostics на Windows: core.fileMode=false → warning
##   - Null-терминированный парсинг (xargs -0/while read -d '') — безопасен для пробелов
##   - DRY_RUN=1 для каждого таргета — вывод "would fix" без мутации
##   - Структурированный вывод: [REPAIR:FIXED], [REPAIR:NOOP], [REPAIR:ERROR]
##   - fix-executable-bit — тонкий фасад: вся логика в core/internal/scripts/fix_executable_bit.py
##     (Strangler T3.4; вывод [REPAIR:*] байт-в-байт с прежним shell-рецептом)
##   - fix-ruff — batch-invocation: ОДИН вызов ruff check --fix + ОДИН ruff format на набор файлов
##     (не пофайлово в цикле); тот же набор файлов чинится, вывод/exit сохранены (T3.4)
## @rationale  Единая точка входа для auto-fix + изоляция от helpers.mk.
##             DevPlan 120: диагностическая проверка переехала на SoT-манифест
##             (check-suite.yaml), repair-таргеты остаются для auto-fix L1.
## @changes 2026-08-02 | DevPlan 120 Wave 1/4: preflight → check + check-diff + deprecated alias
## @changes 2026-08-22 | Strangler T3.4 Wave 3: fix-executable-bit → Python-модуль (тонкий рецепт);
##            fix-ruff → batch ruff (один вызов на набор файлов)
# endregion MODULE_CONTRACT

# ═══ REPAIR_TARGETS — machine-readable реестр для CI-валидации ═══
REPAIR_TARGETS := fix-executable-bit fix-ruff fix-pycache fix-gate check check-diff

.PHONY: fix-executable-bit fix-ruff fix-pycache fix-gate check check-diff

# ── fix-executable-bit: chmod +x for .sh outside core/lib/ (Strangler T3.4 → Python) ──
## @purpose  Двухпроходный fix: (1) staged/new .sh через git add --chmod=+x,
##           (2) tracked .sh через git update-index --chmod=+x.
##           Вся логика — core/internal/scripts/fix_executable_bit.py (Python-first канон);
##           рецепт — тонкий фасад. Вывод [REPAIR:*] байт-в-байт с прежним shell-рецептом.
##           Windows diagnostic: core.fileMode=false → warning.
##           DRY_RUN=1: вывод "would fix" без мутации.
fix-executable-bit:
	@DRY_RUN="$(DRY_RUN)" $(PYTHON) -m core.internal.scripts.fix_executable_bit

# ── fix-ruff: format + lint fix for CHANGED Python files only ──
## @purpose  Ruff check --fix + format для changed .py файлов.
##           SCOPE=diff (default): staged + unstaged diff.
##           SCOPE=staged: только staged (pre-commit safety).
##           SCOPE=all: все tracked .py файлы.
##           DRY_RUN=1: вывод "would format" без мутации.
##           Если ruff не установлен — fail с диагностикой (не || true).
##           T3.4: batch-invocation — ОДИН вызов ruff check --fix + ОДИН ruff format
##           на весь набор (не пофайловый цикл); tr|xargs -0 — пробелы в именах безопасны.
fix-ruff:
	@trap 'echo "[REPAIR:ERROR][fix-ruff] Failed at line $${LINENO}"' ERR; \
	command -v ruff >/dev/null 2>&1 || { \
		echo "[REPAIR:ERROR][fix-ruff] ruff not found. Install: pip install ruff"; \
		exit 1; \
	}; \
	_scope="$(or $(SCOPE),diff)"; \
	_changed_list=""; \
	case "$$_scope" in \
		all) \
			_changed_list=$$(git ls-files -- '*.py' '*.pyi' 2>/dev/null || true) ;; \
		staged) \
			_changed_list=$$(git diff --cached --name-only --diff-filter=ACM -- '*.py' '*.pyi' 2>/dev/null || true) ;; \
		diff|*) \
			_changed_list=$$( \
				{ git diff --cached --name-only --diff-filter=ACM -- '*.py' '*.pyi' 2>/dev/null; \
				  git diff --name-only --diff-filter=ACM -- '*.py' '*.pyi' 2>/dev/null; } | sort -u) ;; \
	esac; \
	if [ -z "$$_changed_list" ]; then \
		echo "[REPAIR:NOOP][fix-ruff] No Python files to format (SCOPE=$$_scope)."; \
	else \
		_count=$$(printf '%s\n' "$$_changed_list" | grep -c .); \
		if [ "$(DRY_RUN)" = "1" ]; then \
			printf '%s\n' "$$_changed_list" | while IFS= read -r f; do \
				[ -z "$$f" ] && continue; \
				echo "  [DRY RUN] would format $$f"; \
			done; \
			echo "[REPAIR:DRYRUN][fix-ruff] Would format $$_count file(s) (SCOPE=$$_scope)."; \
		else \
			printf '%s\n' "$$_changed_list" | tr '\n' '\0' | xargs -0 ruff check --fix 2>&1 || true; \
			printf '%s\n' "$$_changed_list" | tr '\n' '\0' | xargs -0 ruff format 2>&1 || true; \
			echo "[REPAIR:FIXED][fix-ruff] $$_count file(s) processed (SCOPE=$$_scope)."; \
		fi; \
	fi

# ── fix-pycache: очистка __pycache__ рабочего дерева (DevPlan 172 W1.5) ──
## @purpose  Удаляет __pycache__-каталоги из core/ и tests/ (рабочее дерево;
##           .pyc внутри tracked hermes-agent/build/ не трогаются — build-payload).
##           DRY_RUN=1: вывод "would remove" без мутации.
fix-pycache:
	@trap 'echo "[REPAIR:ERROR][fix-pycache] Failed at line $${LINENO}"' ERR; \
	if [ "$(DRY_RUN)" = "1" ]; then \
		find core tests -type d -name __pycache__ -not -path "*/build/*" 2>/dev/null | \
			sed 's/^/  [DRY RUN] would remove /'; \
		echo "[REPAIR:DRYRUN][fix-pycache] DRY RUN — no files modified."; \
	else \
		_removed=0; \
		while IFS= read -r -d '' d; do \
			[ -z "$$d" ] && continue; \
			rm -rf "$$d" && { echo "  [REPAIR:FIXED] removed $$d"; _removed=$$((_removed+1)); }; \
		done < <(find core tests -type d -name __pycache__ -not -path "*/build/*" -print0 2>/dev/null); \
		if [ $$_removed -eq 0 ]; then \
			echo "[REPAIR:NOOP][fix-pycache] No __pycache__ directories found."; \
		else \
			echo "[REPAIR:FIXED][fix-pycache] $$_removed directory(ies) removed."; \
		fi; \
	fi

# ── fix-gate: composite — ONLY gate-blocking L1 fixes ──
## @purpose  Композитный таргет для исправления ВСЕХ gate-блокирующих L1 ошибок.
##           = fix-executable-bit + fix-ruff + fix-pycache + generate-manifests.
##           CONTRACT: никогда не расширять без ревью (fix-pycache — ревью DevPlan 172 W1.5).
##           Не включает: yaml format, shell format, regen api, regen snapshots.
##           DRY_RUN=1: пробрасывается во все подтаргеты.
fix-gate: fix-executable-bit fix-ruff
	@echo "[IMP:7][fix-gate] Running generate-manifests..."
	@$(MAKE) generate-manifests
	@echo "[IMP:7][fix-gate] Running fix-pycache..."
	@$(MAKE) fix-pycache
	@echo "[REPAIR:FIXED][fix-gate] All gate-blocking L1 fixes applied."
	@echo "  Next: git add -u && make gate MODE=fast"

# ── check: диагностический executor на SoT-манифесте core/check-suite.yaml  ──
## @purpose  Run ALL checks from core/check-suite.yaml (SoT), collect errors once (DevPlan 120).
##           Диагностический акселератор: fix-фаза (fix-gate + tier=fix) → fingerprint-кэш
##           (replay зелёного прогона на байт-идентичном дереве) → static-чеки параллельно +
##           pytest-чеки последовательно с xdist → единый отчёт. Кэш ТОЛЬКО здесь.
##           НЕ заменяет gate — канонический арбитр остаётся `make gate MODE=fast|full|ci-docker`.
##           DevPlan 165: ЕДИНАЯ тестовая команда агента — MARKER=<suite> (один чек по id из
##           манифеста, включая diagnostic:false как integration) и TEST_FILE=<path> (один файл);
##           оба режима — БЕЗ кэша (детерминизм). Прогон всегда пишется в журнал
##           .ai/logs/runs.jsonl (shared.test_journal).
##   Usage: make check [WORKERS=6] [JSON=1] [SKIP_FIX=1] [VERBOSE=1] [CHECK_CACHE=0]
##          [MARKER=<suite>] [TEST_FILE=<path>]
check:
	$(eval _workers := $(or $(WORKERS),6))
	$(eval _flags := --workers $(_workers))
	$(if $(filter 1,$(JSON)),$(eval _flags := $(_flags) --json))
	$(if $(filter 1,$(SKIP_FIX)),$(eval _flags := $(_flags) --no-fix))
	$(if $(filter 1,$(VERBOSE)),$(eval _flags := $(_flags) --verbose))
	$(if $(filter 0,$(CHECK_CACHE)),$(eval _flags := $(_flags) --no-cache))
	$(if $(MARKER),$(eval _flags := $(_flags) --only $(MARKER)))
	$(if $(TEST_FILE),$(eval _flags := $(_flags) --test-file $(TEST_FILE)))
	$(PYTHON) -m core.internal.check_suite run $(_flags)

# ── check-diff: узкий diff-таргет (DevPlan 120 §3.5, без кэша) ──
## @purpose  Быстрая диагностика по изменённым файлам: pre-commit run --files <diff> +
##           ruff по изменённым .py + pytest по изменённым test-файлам. Без изменений → exit 0.
##   Usage: make check-diff
check-diff:
	$(PYTHON) -m core.internal.check_suite run --mode diff

# ⚠️ TRAP[DECISION] · 2026-07-23 · — · fix-ruff: newline-separated not null-terminated
# · Rejected: null-separated storage in bash variables ($()) loses all but first file
# · Reason: bash cannot store null bytes in variables. DevPlan code used -z with $().
#            Implemented with newline-separated + sort -u for correctness.
# · Rev: если появится файл .py с newline в имени → перейти на временные файлы
