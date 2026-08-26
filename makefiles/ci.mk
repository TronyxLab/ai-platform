# GREP_SUMMARY: ci.mk, test-node, e2e-verify, gate, pre-commit, scripts-audit, secrets-unlock, check-suite-portal, test-journal
# STRUCTURE: ┌variables┐ → ◇ test-node (journal) → ◇ e2e-verify (journal) → ◇ gate (check_suite portal) → ◇ pre-commit-install → ◇ pre-commit-run → ◇ scripts-audit → ◇ secrets-unlock
# region MODULE_CONTRACT
## @purpose  CI and quality targets — test-node, e2e-verify, gate, pre-commit, scripts-audit, secrets
## @scope    Included from root Makefile; uses pytest + shell entrypoints + Python check_suite executor
## @invariants
##   - gate MODE=fast must pass before push (CI pre-flight rule)
##   - gate — портал на core/check-suite.yaml (DevPlan 120): 0 hardcoded-списков/маркерных
##     выражений в таргете; порядок шагов из манифеста (паритет прежнего ci.mk — golden-гейт)
##   - ЕДИНАЯ диагностическая команда агента — make check [MARKER=<suite>] [TEST_FILE=<path>]
##     (repair.mk → check_suite --only/--test-file); таргеты test/test-summary УДАЛЕНЫ
##     (DevPlan 165) — таргеты запрещены категорийно (namelint, инвариант глоссария)
##   - test-node/e2e-verify журналируются shared.test_journal (record --exit-code) —
##     rc прогона пробрасывается, журнал не влияет на exit-код
## @rationale Makefile include-split W4-E4: CI targets isolated from bootstrap/deploy.
##            DevPlan 120 (Wave 2): gate через executor — CI/workflows БЕЗ изменений получают xdist.
##            DevPlan 165: поверхность тестовых команд сведена к make check + make gate.
## @changes 2026-07-31 | DevPlan 097 close-out: doxygen-check target + gate step (zero-warnings guard)
## @changes 2026-08-02 | DevPlan 120 Wave 2: gate → check_suite run --gate-mode (портал манифеста)
## @changes 2026-08-13 | DevPlan 165: test/test-summary удалены; journal-обёртки test-node/e2e-verify
## @changes 2026-08-16 | План 175 W2.2: validate/lint/check-file-lines/check-dead-code/doxygen-check/
##                      check-exception-patterns удалены — суиты check-suite.yaml вызывают инструменты напрямую
# endregion MODULE_CONTRACT

.PHONY: test-node e2e-verify gate pre-commit-install pre-commit-run scripts-audit secrets-unlock

## test-node: Run E2E pipeline tests against a test-VPS (requires NODE, AGE_SECRET_KEY, SSH access)
##   Usage: make test-node NODE=<name> [AGE_SECRET_KEY_FILE=<file>]
##   NOT included in `make check` or `make gate` — expensive, requires dedicated test-VPS
##   Per AGENTS.md invariant 9: test-VPS is recreatable — tests use cold-start only
##   Marker: requires_node (orthogonal to e2e = HTTP checks against *.tronyx.ru)
##   Chaos excluded (DevPlan 136 W6 T6.1, B4): chaos fault-injection targets a
##   BOOTSTRAPPED node and breaks the bare-node cold-start suite. Resilience drills run
##   separately (DevPlan 013, two tiers):
##     fast:  PYTEST_NO_ESCALATION=1 $(PYTHON) -m pytest tests/e2e/test_chaos_resilience.py -m "chaos and not night" -v --tb=short -rs
##     night (separate operator window ~25 min): ... -m night -v --tb=short -rs
##   (pre-flight «голоты» check is skipped for chaos sessions — see tests/e2e/README.md)
##   DevPlan 165: прогон журналируется (test_journal record --junit report-node.xml);
##   rc прогона пробрасывается неизменным.
test-node:
	@if [ -z "$(NODE)" ]; then \
		echo "[IMP:9][make][test-node] ERROR: NODE not set — usage: make test-node NODE=<name>" >&2; \
		exit 1; \
	fi
	@echo "[IMP:9][make][test-node] Running E2E pipeline tests NODE=$(NODE) (chaos excluded — B4)..."
	@PYTEST_NO_ESCALATION=1 $(PYTHON) -m pytest tests/e2e/ -m "requires_node and not chaos" -v --tb=short -rs \
		--junitxml=tests/report-node.xml; _rc=$$?; \
	$(PYTHON) -m core.internal.shared.test_journal record --goal test-node --junit tests/report-node.xml --exit-code $$_rc || true; \
	exit $$_rc

## e2e-verify: HTTP+TLS sweep verification of all endpoints on a node (requires NODE, SSH for remote collect)
##   Usage: make e2e-verify NODE=<name> [MODE=local|remote] [JSON=1]
##   NOT included in `make check` or `make gate` — requires a live node
##   R4 semantics: no node / SSH unavailable → FAIL (exit 1), never skip
##   DevPlan 136 W5: acceptance script — table + exit 0 when all endpoints green (W6.7)
##   DevPlan 165: прогон журналируется (test_journal record — exit code);
##   rc прогона пробрасывается неизменным.
##   MODE=remote (default) — collect endpoints via SSH reading nginx conf.d;
##   MODE=local — collect from local node-configs (node.yaml projects + overlays/nginx)
e2e-verify:
	@if [ -z "$(NODE)" ]; then \
		echo "[IMP:9][make][e2e-verify] ERROR: NODE not set — usage: make e2e-verify NODE=<name>" >&2; \
		exit 1; \
	fi
	@echo "[IMP:9][make][e2e-verify] Running endpoint sweep verification NODE=$(NODE) MODE=$(or $(MODE),remote)..."
	@$(PYTHON) -m core.internal.verify_sweep sweep \
		--node "$(NODE)" \
		--mode $(or $(MODE),remote) \
		$(if $(JSON),--json); _rc=$$?; \
	$(PYTHON) -m core.internal.shared.test_journal record --goal e2e-verify --exit-code $$_rc || true; \
	exit $$_rc

## gate: Production Gate (портал на SoT-манифесте core/check-suite.yaml, DevPlan 120).
##   Usage: make gate [MODE=fast|full|ci-docker] [PROJECT=<name>] [SKIP_PRECOMMIT=1]
##   MODE=full (default) — validate → lint → gates → contract → static → predeploy → smoke → component
##   MODE=fast — pre-commit → validate → check-dead-code → check-exception-patterns → doxygen-check →
##     gates → gates-docker → contract → static → predeploy (no Docker, fail-fast)
##   MODE=ci-docker — predeploy-docker → smoke → component (Docker stack, no static duplication)
##   PROJECT=<name> — filter predeploy tests to a specific project (used in CI deploy workflow)
##   SKIP_PRECOMMIT=1 — пропуск pre-commit шага (CI-паритет)
##   Семантика неизменна (порядок шагов = прежний ci.mk, fail-fast fast / accumulate+merge
##   full/ci-docker); изменяется ТОЛЬКО способ исполнения pytest-шагов (xdist). БЕЗ кэша.
gate:
	$(eval MODE := $(or $(MODE),full))
	@$(PYTHON) -m core.internal.check_suite run --gate-mode $(MODE) \
		$(if $(PROJECT),--project "$(PROJECT)",) \
		$(if $(filter 1,$(SKIP_PRECOMMIT)),--skip-precommit,)
	@echo "[IMP:9][make][gate] Gate: ALL PASS (MODE=$(MODE))"

## validate: УДАЛЁН (План 175 W2.2) — суит 'validate' check-suite.yaml вызывает bash core/entrypoints/validate.sh напрямую.

## lint: УДАЛЁН (План 175 W2.2) — суит 'lint' check-suite.yaml вызывает bash core/entrypoints/validate.sh --lint напрямую.

## check-file-lines: УДАЛЁН (План 175 W2.2) — суит 'check-file-lines' → bash core/entrypoints/check-file-lines.sh.

## check-dead-code: УДАЛЁН (План 175 W2.2) — суит 'check-dead-code' → bash core/entrypoints/check-dead-code.sh.

## doxygen-check: УДАЛЁН (План 175 W2.2) — суит 'doxygen-check' → python3 core/internal/lint/doxygen_checker.py.

## check-exception-patterns: УДАЛЁН (План 175 W2.2) — суит 'check-exception-patterns'
##   → python3 -m core.internal.static check --only exception_patterns.

## pre-commit-install: Install pre-commit hooks (gitleaks + code-quality + check-doc-headers + pre-push + commit-msg)
pre-commit-install:
	@echo "[IMP:7][make][pre-commit-install] Installing pre-commit hooks..."
	@$(PIP) install pre-commit 2>/dev/null || pip3 install pre-commit 2>/dev/null || { \
		echo "[IMP:10][make][pre-commit-install] FATAL: pip install pre-commit failed"; \
		exit 1; \
	}
	@pre-commit install --hook-type pre-commit --hook-type pre-push --hook-type commit-msg 2>/dev/null || { \
		echo "[IMP:9][make][pre-commit-install] ERROR: pre-commit install failed — check if pre-commit is installed (pip install pre-commit)"; \
		exit 1; \
	}
	@echo "[IMP:9][make][pre-commit-install] Pre-commit hooks installed: gitleaks, ruff (verify only — run: ruff check --fix . && ruff format .), yamllint, check-github-workflows, check-compose-spec, check-yaml, trailing-whitespace, end-of-file-fixer, check-doc-headers, basedpyright, bandit, pre-push-gate, commit-msg"
	@echo "[IMP:8][make][pre-commit-install] Run 'pre-commit run --all-files' to validate all files"

## pre-commit-run: Run all pre-commit hooks against all files (CI use)
##   DevPlan 123 T12 (FL9): при RED выводится ПОЛНЫЙ список Failed-хуков
##   (парсинг вывода pre-commit — имя хука до первой группы точек).
pre-commit-run:
	@echo "[IMP:7][make][pre-commit-run] Running all pre-commit hooks..."
	@set -o pipefail; out=$$(pre-commit run --all-files 2>&1); rc=$$?; printf '%s\n' "$$out"; \
	if [ $$rc -ne 0 ]; then \
		echo "" >&2; \
		echo "[IMP:10][make][pre-commit-run] FAILED hooks (полный список):" >&2; \
		failed=$$(printf '%s\n' "$$out" | grep -E '\.{3,}Failed' | sed 's/^\([^.]*\)\.\{3,\}.*/\1/' | sort -u); \
		if [ -n "$$failed" ]; then \
			printf '%s\n' "$$failed" | sed 's/^/  ✗ /' >&2; \
		else \
			echo "  (не удалось распарсить список хуков — см. вывод выше)" >&2; \
		fi; \
		exit 1; \
	fi
	@echo "[IMP:9][make][pre-commit-run] All pre-commit hooks passed"

## scripts-audit: Проверить регистрацию всех shebang-скриптов в manifest или exceptions
scripts-audit:
	@echo "[IMP:7][make][scripts-audit] Auditing shebang script registration..."
	@bash $(_platform_root)/core/internal/scripts-audit.sh

## secrets-unlock: Decrypt SOPS/age secrets
##   Usage: make secrets-unlock [NODE=<name>]
##   NODE=<name> — bare имя ноды: расшифровывает <node-configs-secrets-dir>/<NODE>.enc.yaml
##   (dispatch в decrypt_secrets.py::resolve_enc_path, REF-0013 — без glob-подмены чужой нодой).
##   Без NODE — SECRETS_FILE env или glob *.enc.yaml (single-node канон).
##   Delegates to core/entrypoints/secrets.sh
secrets-unlock:
	@echo "[IMP:7][make][secrets-unlock] Decrypting secrets..."
	@if [ -n "$(NODE)" ] && ! printf '%s' "$(NODE)" | grep -Eq '^[A-Za-z0-9][A-Za-z0-9._-]*$$'; then \
		echo "[IMP:10][make][secrets-unlock] ERROR: NODE='$(NODE)' не похоже на имя ноды (разделители путей/пробелы запрещены) — путь к enc-файлу задавайте через SECRETS_FILE" >&2; \
		exit 1; \
	fi
	@$(_platform_root)/core/entrypoints/secrets.sh $(NODE)
	@echo "[IMP:9][make][secrets-unlock] Secrets decrypted"
