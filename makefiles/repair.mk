# GREP_SUMMARY: repair.mk, fix-executable-bit, fix-ruff, fix-gate, repair-contract, repairable, gates, REPAIR_TARGETS, M-ADE, check, check-diff
# STRUCTURE: ┌REPAIR_TARGETS export┐ → ◇ fix-executable-bit (xargs -0) → ◇ fix-ruff (SCOPE=diff) → ◇ fix-gate (composite) → ◇ check (SoT-executor) → ◇ check-diff (diff-скоуп) → ⊕ .PHONY
# region MODULE_CONTRACT
## @purpose  Repair targets for deterministic, idempotent L1 gate errors + диагностический
##           check/check-diff (DevPlan 120, экс-preflight).
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
## @rationale  Единая точка входа для auto-fix + изоляция от helpers.mk.
##             DevPlan 120: диагностическая проверка переехала на SoT-манифест
##             (check-suite.yaml), repair-таргеты остаются для auto-fix L1.
## @changes 2026-08-02 | DevPlan 120 Wave 1/4: preflight → check + check-diff + deprecated alias
# endregion MODULE_CONTRACT

# ═══ REPAIR_TARGETS — machine-readable реестр для CI-валидации ═══
REPAIR_TARGETS := fix-executable-bit fix-ruff fix-gate check check-diff

.PHONY: fix-executable-bit fix-ruff fix-gate check check-diff

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
	@# Pass 1+2 combined in one shell for _fixed continuity across both passes
	@_fixed=0; \
	if [ "$(DRY_RUN)" = "1" ]; then \
		git diff --cached --name-only --diff-filter=ACM -z -- '*.sh' 2>/dev/null | \
		while IFS= read -r -d '' f; do \
			case "$$f" in core/lib/*) continue ;; esac; \
			[ -z "$$f" ] && continue; \
			echo "  [DRY RUN] would +x (staged) $$f"; \
		done; \
		git ls-files -s -z -- '*.sh' 2>/dev/null | \
		while IFS= read -r -d '' line; do \
			mode=$$(echo "$$line" | awk '{print $$1}'); \
			f=$$(echo "$$line" | awk '{for(i=4;i<=NF;i++) printf "%s%s", $$i, (i==NF?"\n":" ")}'); \
			case "$$f" in core/lib/*) continue ;; esac; \
			[ "$$mode" = "100644" ] && echo "  [DRY RUN] would +x (tracked) $$f"; \
		done; \
	else \
		git diff --cached --name-only --diff-filter=ACM -z -- '*.sh' 2>/dev/null | \
		while IFS= read -r -d '' f; do \
			case "$$f" in core/lib/*) continue ;; esac; \
			[ -z "$$f" ] && continue; \
			[ -f "$$f" ] || continue; \
			git add --chmod=+x -- "$$f" && { echo "  [REPAIR:FIXED] +x (staged) $$f"; _fixed=$$((_fixed+1)); }; \
		done; \
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
	if [ "$(DRY_RUN)" = "1" ]; then \
		echo "[REPAIR:DRYRUN][fix-executable-bit] DRY RUN — no files modified."; \
	elif [ $$_fixed -eq 0 ]; then \
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
		_fixed=0; \
		while IFS= read -r f; do \
			[ -z "$$f" ] && continue; \
			[ -f "$$f" ] || continue; \
			if [ "$(DRY_RUN)" = "1" ]; then \
				echo "  [DRY RUN] would format $$f"; \
				_fixed=$$((_fixed+1)); \
			else \
				ruff check --fix "$$f" 2>&1 || true; \
				ruff format "$$f" 2>&1 || true; \
				echo "  [REPAIR:FIXED] ruff: $$f"; \
				_fixed=$$((_fixed+1)); \
			fi; \
		done <<< "$$_changed_list"; \
		if [ "$(DRY_RUN)" = "1" ]; then \
			echo "[REPAIR:DRYRUN][fix-ruff] Would format $$_fixed file(s) (SCOPE=$$_scope)."; \
		elif [ $$_fixed -eq 0 ]; then \
			echo "[REPAIR:NOOP][fix-ruff] No files actually needed fixing (SCOPE=$$_scope)."; \
		else \
			echo "[REPAIR:FIXED][fix-ruff] $$_fixed file(s) formatted (SCOPE=$$_scope)."; \
		fi; \
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

# ── check: диагностический executor на SoT-манифесте core/check-suite.yaml (экс-preflight) ──
## @purpose  Run ALL checks from core/check-suite.yaml (SoT), collect errors once (DevPlan 120).
##           Диагностический акселератор: fix-фаза (fix-gate + tier=fix) → fingerprint-кэш
##           (replay зелёного прогона на байт-идентичном дереве) → static-чеки параллельно +
##           pytest-чеки последовательно с xdist → единый отчёт. Кэш ТОЛЬКО здесь.
##           НЕ заменяет gate — канонический арбитр остаётся `make gate MODE=fast|full|ci-docker`.
##   Usage: make check [WORKERS=6] [JSON=1] [SKIP_FIX=1] [VERBOSE=1] [CHECK_CACHE=0]
check:
	$(eval _workers := $(or $(WORKERS),6))
	$(eval _flags := --workers $(_workers))
	$(if $(filter 1,$(JSON)),$(eval _flags := $(_flags) --json))
	$(if $(filter 1,$(SKIP_FIX)),$(eval _flags := $(_flags) --no-fix))
	$(if $(filter 1,$(VERBOSE)),$(eval _flags := $(_flags) --verbose))
	$(if $(filter 0,$(CHECK_CACHE)),$(eval _flags := $(_flags) --no-cache))
	$(PYTHON) -m core.internal.check_suite run --mode diagnostic $(_flags)

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
