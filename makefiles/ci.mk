# GREP_SUMMARY: ci.mk, test, test-summary, gate, validate, lint, check-file-lines, pre-commit, scripts-audit, audit, secrets-unlock
# STRUCTURE: ┌variables┐ → ◇ test → ◇ test-summary → ◇ gate → ◇ validate → ◇ lint → ◇ check-file-lines → ◇ pre-commit-install → ◇ pre-commit-run → ◇ scripts-audit → ◇ audit → ◇ secrets-unlock
# region MODULE_CONTRACT
## @purpose  CI and quality targets — test, test-summary (agent-oriented wrapper), gate, validate, lint, pre-commit, audit, secrets
## @scope    Included from root Makefile; uses pytest + shell entrypoints + Python test_runner
## @invariants
##   - gate MODE=fast must pass before push (CI pre-flight rule)
##   - test MARKER=all runs canonical order: validate→lint→gates→contract→static→predeploy→smoke→component→integration
##   - test-summary delegates to core/internal/test_runner.py — compact agent-oriented output
## @rationale Makefile include-split W4-E4: CI targets isolated from bootstrap/deploy
# endregion MODULE_CONTRACT

.PHONY: test test-summary test-node gate validate lint check-file-lines pre-commit-install pre-commit-run scripts-audit audit secrets-unlock check-dead-code

## test: Run tests with MARKER filter. Usage: make test [MARKER=static|smoke|component|integration|predeploy|contract|e2e|all]
##   MARKER=all (default) — full suite in canonical order: validate → lint → gates → contract → static → predeploy → smoke → component → integration
##   MARKER=static — schema validation + lint + static_audit + unit (no Docker, compact output via test_runner)
##   MARKER=static_audit — pure pytest static_audit only (no validate/lint, compact output via test_runner)
##   MARKER=contract — contract tests for entrypoint scripts (no Docker, compact output via test_runner)
##   MARKER=smoke — compose lifecycle + healthchecks (needs Docker, verbose)
##   MARKER=component — hermes-agent + observability health endpoints (needs Docker, verbose)
##   MARKER=integration — full hermes LLM stack (needs Docker, verbose)
##   MARKER=predeploy — container/config/network validation (needs Docker, verbose)
##   MARKER=e2e — manual end-to-end tests against *.tronyx.ru (external, no Docker, dev-only, verbose)
test:
	$(eval MARKER := $(or $(MARKER),all))
	@if [ "$(MARKER)" = "static" ]; then \
		echo "[IMP:7][make][test] Running static pipeline (validate+lint+pytest) — compact via test_runner..."; \
		$(PYTHON) -m core.internal.test_runner --marker static --junit-output tests/report-static.xml && \
		cp tests/report-static.xml tests/report.xml; \
	elif [ "$(MARKER)" = "static_audit" ]; then \
		echo "[IMP:7][make][test] Running static_audit only (no validate/lint) — compact via test_runner..."; \
		$(PYTHON) -m core.internal.test_runner --marker static_audit --junit-output tests/report-static.xml && \
		cp tests/report-static.xml tests/report.xml; \
	elif [ "$(MARKER)" = "smoke" ]; then \
		echo "[IMP:7][make][test] Running smoke tests..."; \
		PYTEST_NO_ESCALATION=1 $(PYTHON) -m pytest tests/ -m "smoke" -v --tb=short -rs \
			--junitxml=tests/report-smoke.xml && \
		cp tests/report-smoke.xml tests/report.xml; \
	elif [ "$(MARKER)" = "component" ]; then \
		echo "[IMP:7][make][test] Running component tests..."; \
		PYTEST_NO_ESCALATION=1 $(PYTHON) -m pytest tests/ -m "component" -v --tb=short -rs \
			--junitxml=tests/report-component.xml && \
		cp tests/report-component.xml tests/report.xml; \
	elif [ "$(MARKER)" = "integration" ]; then \
		echo "[IMP:7][make][test] Running integration tests..."; \
		PYTEST_NO_ESCALATION=1 $(PYTHON) -m pytest tests/ -m "integration" -v --tb=short -rs \
			--junitxml=tests/report-integration.xml && \
		cp tests/report-integration.xml tests/report.xml; \
	elif [ "$(MARKER)" = "predeploy" ]; then \
		echo "[IMP:7][make][test] Running predeploy tests..."; \
		PYTEST_NO_ESCALATION=1 $(PYTHON) -m pytest tests/ -m "predeploy" -v --tb=short -rs \
			--junitxml=tests/report-predeploy.xml && \
		cp tests/report-predeploy.xml tests/report.xml; \
	elif [ "$(MARKER)" = "contract" ]; then \
		echo "[IMP:7][make][test] Running contract tests — compact via test_runner..."; \
		$(PYTHON) -m core.internal.test_runner --marker contract --junit-output tests/report-contract.xml && \
		cp tests/report-contract.xml tests/report.xml; \
	elif [ "$(MARKER)" = "e2e" ]; then \
		echo "[IMP:7][make][test] Running E2E tests (manual, targets external *.tronyx.ru)..."; \
		PYTEST_NO_ESCALATION=1 $(PYTHON) -m pytest tests/ -m "e2e" -v --tb=short -rs \
			--junitxml=tests/report-e2e.xml && \
		cp tests/report-e2e.xml tests/report.xml; \
	elif [ "$(MARKER)" = "all" ]; then \
		echo "[IMP:7][make][test] Running full test suite (canonical order: validate→lint→gates→contract→static→predeploy→smoke→component→integration)..."; \
		rm -f tests/report.xml tests/report*.xml && \
		echo "[IMP:7][make][test] Step 1/9: validate..."; \
		bash $(_platform_root)/core/entrypoints/validate.sh || { echo "[IMP:9][make][test] FAIL: validate"; exit 1; }; \
		echo "[IMP:7][make][test] Step 2/9: lint..."; \
		bash $(_platform_root)/core/entrypoints/validate.sh --lint; \
		echo "[IMP:7][make][test] Step 3/9: gates..."; \
		PYTEST_NO_ESCALATION=1 $(PYTHON) -m pytest tests/gates/ -m "gate and not skip_enforcement" -v || { echo "[IMP:9][make][test] FAIL: gates"; exit 1; }; \
		echo "[IMP:7][make][test] Step 4/9: contract..."; \
		$(MAKE) test MARKER=contract || { echo "[IMP:9][make][test] FAIL: contract"; exit 1; }; \
		echo "[IMP:7][make][test] Step 5/9: static..."; \
		$(MAKE) test MARKER=static_audit || { echo "[IMP:9][make][test] FAIL: static"; exit 1; }; \
		echo "[IMP:7][make][test] Step 6/9: predeploy..."; \
		$(MAKE) test MARKER=predeploy || { echo "[IMP:9][make][test] FAIL: predeploy"; exit 1; }; \
		echo "[IMP:7][make][test] Step 7/9: smoke..."; \
		$(MAKE) test MARKER=smoke || { echo "[IMP:9][make][test] FAIL: smoke"; exit 1; }; \
		echo "[IMP:7][make][test] Step 8/9: component..."; \
		$(MAKE) test MARKER=component || { echo "[IMP:9][make][test] FAIL: component"; exit 1; }; \
		echo "[IMP:7][make][test] Step 9/9: integration..."; \
		$(MAKE) test MARKER=integration || { echo "[IMP:9][make][test] FAIL: integration"; exit 1; }; \
		echo "[IMP:7][make][test] Merging JUnit XML reports..."; \
		$(PYTHON) tests/merge_junit.py \
			tests/report-contract.xml \
			tests/report-static.xml \
			tests/report-predeploy.xml \
			tests/report-smoke.xml \
			tests/report-component.xml \
			tests/report-integration.xml \
			-o tests/report.xml; \
	else \
		echo "[IMP:9][make][test] ERROR: Unknown MARKER='$(MARKER)'. Valid values: static, static_audit, smoke, component, integration, predeploy, contract, e2e, all" >&2; \
		exit 1; \
	fi
	@echo "[IMP:9][make][test] Tests complete (MARKER=$(MARKER))"

## test-node: Run E2E pipeline tests against a test-VPS (requires NODE, AGE_SECRET_KEY, SSH access)
##   Usage: make test-node NODE=<name> [AGE_SECRET_KEY_FILE=<file>]
##   NOT included in `make test MARKER=all` or `make gate` — expensive, requires dedicated test-VPS
##   Per AGENTS.md invariant 9: test-VPS is recreatable — tests use cold-start only
##   Marker: requires_node (orthogonal to e2e = HTTP checks against *.tronyx.ru)
test-node:
	@if [ -z "$(NODE)" ]; then \
		echo "[IMP:9][make][test-node] ERROR: NODE not set — usage: make test-node NODE=<name>" >&2; \
		exit 1; \
	fi
	@echo "[IMP:9][make][test-node] Running E2E pipeline tests NODE=$(NODE)..."
	PYTEST_NO_ESCALATION=1 $(PYTHON) -m pytest tests/e2e/ -m "requires_node" -v --tb=short -rs \
		--junitxml=tests/report-node.xml
	@echo "[IMP:9][make][test-node] E2E pipeline tests complete NODE=$(NODE)"

## test-summary: Run tests via compact agent-oriented wrapper. Usage: make test-summary [MARKER=static_audit|smoke|component|integration|predeploy|contract|e2e|all|static] [TIMEOUT=1800] [TEST_FILE=<path>]
##   Delegates to core/internal/test_runner.py — outputs compact summary (<100 lines, PASS/FAIL counts).
##   MARKER=static_audit (default) — static analysis only, no Docker.
##   MARKER=static — validate.sh + lint + pytest static_audit (full static pipeline).
##   TIMEOUT=N — subprocess timeout in seconds (default 1800 per DevPlan 098 AC8).
##   TEST_FILE=<path> — run pytest on a single file (e.g. tests/unit/test_foo.py). Overrides MARKER.
test-summary:
	$(eval MARKER := $(or $(MARKER),static_audit))
	$(eval TIMEOUT := $(or $(TIMEOUT),1800))
	$(if $(TEST_FILE), \
		@$(PYTHON) -m core.internal.test_runner --test-file $(TEST_FILE) --timeout $(TIMEOUT), \
		@$(PYTHON) -m core.internal.test_runner --marker $(MARKER) --timeout $(TIMEOUT) \
	)

## gate: Production Gate. Usage: make gate [MODE=fast|full|ci-docker] [PROJECT=<name>]
##   MODE=full (default) — validate → lint → gates → contract → static → predeploy → smoke → component
##   MODE=fast — validate → lint → gates → contract → static → predeploy (no Docker)
##   MODE=ci-docker — predeploy-docker → smoke → component (Docker stack, no static duplication)
##   PROJECT=<name> — filter predeploy tests to a specific project (used in CI deploy workflow)
gate:
	$(eval MODE := $(or $(MODE),full))
	@if [ "$(MODE)" = "fast" ]; then \
		echo "[IMP:7][make][gate] MODE=fast — 8 steps: pre-commit, validate, check-dead-code, check-exception-patterns, gates, contract, static, predeploy..."; \
		rm -f tests/report.xml tests/report*.xml && \
		if [ -z "$(SKIP_PRECOMMIT)" ] || [ "$(SKIP_PRECOMMIT)" != "1" ]; then \
			echo "[IMP:7][make][gate] Step 1/8: pre-commit-run..."; \
			$(MAKE) pre-commit-run || { echo "[IMP:9][make][gate] FAIL: pre-commit-run"; exit 1; }; \
		else \
			echo "[IMP:7][make][gate] Step 1/8: pre-commit-run skipped (SKIP_PRECOMMIT=1)"; \
		fi; \
		echo "[IMP:7][make][gate] Step 2/8: validate..."; \
		bash $(_platform_root)/core/entrypoints/validate.sh || { echo "[IMP:9][make][gate] FAIL: validate"; exit 1; }; \
		echo "[IMP:7][make][gate] Step 2b/8: check-dead-code..."; \
		$(MAKE) check-dead-code || { echo "[IMP:9][make][gate] FAIL: check-dead-code"; exit 1; }; \
		echo "[IMP:7][make][gate] Step 2c/8: check-exception-patterns..."; \
		$(MAKE) check-exception-patterns || { echo "[IMP:9][make][gate] FAIL: check-exception-patterns"; exit 1; }; \
		echo "[IMP:7][make][gate] Step 3a/8: anti-drift CI gates (static, parallel)..."; \
		PYTEST_NO_ESCALATION=1 $(PYTHON) -m pytest tests/gates/ -m "gate and not requires_docker" -n auto -v || { echo "[IMP:9][make][gate] FAIL: gates (static)"; exit 1; }; \
		echo "[IMP:7][make][gate] Step 3b/6: anti-drift CI gates (Docker, sequential)..."; \
		PYTEST_NO_ESCALATION=1 $(PYTHON) -m pytest tests/gates/ -m "gate and requires_docker" -v \
			|| test $$? -eq 5 \
			|| { echo "[IMP:9][make][gate] FAIL: gates (Docker)"; exit 1; }; \
		echo "[IMP:7][make][gate] Step 4/6: contract tests..."; \
		$(MAKE) test MARKER=contract || { echo "[IMP:9][make][gate] FAIL: contract"; exit 1; }; \
		echo "[IMP:7][make][gate] Step 5/6: static tests (no Docker) — compact via test_runner..."; \
		$(PYTHON) -m core.internal.test_runner --marker static_audit --junit-output tests/report-static.xml || { echo "[IMP:9][make][gate] FAIL: static"; exit 1; }; \
		echo "[IMP:7][make][gate] Step 6/6: predeploy tests (PROJECT=$(or $(PROJECT),all))..."; \
		PYTEST_NO_ESCALATION=1 $(PYTHON) -m pytest tests/ -m "predeploy and not requires_docker" -v --tb=short -rs \
			$(if $(PROJECT),-k "$(PROJECT)",) \
			--junitxml=tests/report-predeploy.xml || { echo "[IMP:9][make][gate] FAIL: predeploy"; exit 1; }; \
	elif [ "$(MODE)" = "full" ]; then \
		echo "[IMP:7][make][gate] MODE=full — running complete gate pipeline (canonical order)..."; \
		GATE_FAILED=0; \
		rm -f tests/report.xml tests/report*.xml; \
		if [ -z "$(SKIP_PRECOMMIT)" ] || [ "$(SKIP_PRECOMMIT)" != "1" ]; then \
			echo "[IMP:7][make][gate] Step 1/11: pre-commit-run..."; \
			$(MAKE) pre-commit-run || { echo "[IMP:9][make][gate] FAIL: pre-commit-run"; GATE_FAILED=1; }; \
		else \
			echo "[IMP:7][make][gate] Step 1/11: pre-commit-run skipped (SKIP_PRECOMMIT=1)"; \
		fi; \
		echo "[IMP:7][make][gate] Step 2/11: validate..."; \
		$(MAKE) validate || { echo "[IMP:9][make][gate] FAIL: validate"; GATE_FAILED=1; }; \
		echo "[IMP:7][make][gate] Step 2b/11: check-dead-code..."; \
		$(MAKE) check-dead-code || { echo "[IMP:9][make][gate] FAIL: check-dead-code"; GATE_FAILED=1; }; \
		echo "[IMP:7][make][gate] Step 3/11: lint..."; \
		$(MAKE) lint || { echo "[IMP:9][make][gate] FAIL: lint"; GATE_FAILED=1; }; \
		echo "[IMP:7][make][gate] Step 4/10: check-file-lines (non-blocking)..."; \
		$(MAKE) check-file-lines || true; \
		echo "[IMP:7][make][gate] Step 5/10: anti-drift gates (fail-fast)..."; \
		PYTEST_NO_ESCALATION=1 $(PYTHON) -m pytest tests/gates/ \
			-m "gate and not skip_enforcement" -v || { echo "[IMP:9][make][gate] FAIL: anti-drift gates"; GATE_FAILED=1; }; \
		echo "[IMP:7][make][gate] Step 6/10: contract tests..."; \
		$(MAKE) test MARKER=contract || { echo "[IMP:9][make][gate] FAIL: contract"; GATE_FAILED=1; }; \
		echo "[IMP:7][make][gate] Step 7/10: static tests..."; \
		$(MAKE) test MARKER=static_audit || { echo "[IMP:9][make][gate] FAIL: static"; GATE_FAILED=1; }; \
		echo "[IMP:7][make][gate] Step 8/10: predeploy tests..."; \
		$(MAKE) test MARKER=predeploy || { echo "[IMP:9][make][gate] FAIL: predeploy"; GATE_FAILED=1; }; \
		echo "[IMP:7][make][gate] Step 9/10: smoke tests..."; \
		$(MAKE) test MARKER=smoke || { echo "[IMP:9][make][gate] FAIL: smoke"; GATE_FAILED=1; }; \
		echo "[IMP:7][make][gate] Step 10/10: component tests..."; \
		$(MAKE) test MARKER=component || { echo "[IMP:9][make][gate] FAIL: component"; GATE_FAILED=1; }; \
		echo "[IMP:7][make][gate] Merging JUnit XML reports..."; \
		$(PYTHON) tests/merge_junit.py \
			tests/report-contract.xml \
			tests/report-static.xml \
			tests/report-predeploy.xml \
			tests/report-smoke.xml \
			tests/report-component.xml \
			-o tests/report.xml || { echo "[IMP:9][make][gate] FAIL: JUnit merge"; GATE_FAILED=1; }; \
		if [ $$GATE_FAILED -ne 0 ]; then \
			echo "[IMP:9][make][gate] Gate: FAILURES DETECTED (MODE=full) — see individual FAIL messages above"; \
			exit 1; \
		fi; \
	elif [ "$(MODE)" = "ci-docker" ]; then \
		echo "[IMP:7][make][gate] MODE=ci-docker — running Docker-dependent gate pipeline (smoke + component, no static duplication)..."; \
		GATE_FAILED=0; \
		rm -f tests/report.xml tests/report*.xml; \
		echo "[IMP:7][make][gate] Step 1/3: predeploy tests (Docker-dependent only)..."; \
		PYTEST_NO_ESCALATION=1 $(PYTHON) -m pytest tests/ -m "predeploy and requires_docker" -v --tb=short -rs \
			--junitxml=tests/report-predeploy.xml || { echo "[IMP:9][make][gate] FAIL: predeploy-docker"; GATE_FAILED=1; }; \
		echo "[IMP:7][make][gate] Step 2/3: smoke tests..."; \
		$(MAKE) test MARKER=smoke || { echo "[IMP:9][make][gate] FAIL: smoke"; GATE_FAILED=1; }; \
		echo "[IMP:7][make][gate] Step 3/3: component tests..."; \
		$(MAKE) test MARKER=component || { echo "[IMP:9][make][gate] FAIL: component"; GATE_FAILED=1; }; \
		echo "[IMP:7][make][gate] Merging JUnit XML reports..."; \
		$(PYTHON) tests/merge_junit.py \
			tests/report-predeploy.xml \
			tests/report-smoke.xml \
			tests/report-component.xml \
			-o tests/report.xml || { echo "[IMP:9][make][gate] FAIL: JUnit merge"; GATE_FAILED=1; }; \
		if [ $$GATE_FAILED -ne 0 ]; then \
			echo "[IMP:9][make][gate] Gate: FAILURES DETECTED (MODE=ci-docker) — see individual FAIL messages above"; \
			exit 1; \
		fi; \
	else \
		echo "[IMP:9][make][gate] ERROR: Unknown MODE='$(MODE)'. Valid values: fast, full, ci-docker" >&2; \
		exit 1; \
	fi
	@echo "[IMP:9][make][gate] Gate: ALL PASS (MODE=$(MODE))"

## validate: Run schema validation only (standalone). Same as `make test MARKER=static` step.
##   Invoked automatically as part of `make test MARKER=static` and `make gate MODE=fast`.
##   To run full validation suite: make test MARKER=static
validate:
	@echo "[IMP:9][make][validate] Running schema validation..."
	@bash $(_platform_root)/core/entrypoints/validate.sh
	@echo "[IMP:9][make][validate] Schema validation complete"

## lint: Run shellcheck + yamllint + pytest (best-effort, warn+skip on missing tools)
lint:
	@echo "[IMP:7][make][lint] Starting lint checks"
	@bash $(_platform_root)/core/entrypoints/validate.sh --lint

## check-file-lines: Check for files exceeding line limit (default 500) — non-blocking warning
##   Usage: make check-file-lines [MAX_LINES=500]
##   Delegates to core/entrypoints/check-file-lines.sh
check-file-lines:
	@echo "[IMP:7][make][check-file-lines] Checking file line limits..."
	@bash $(_platform_root)/core/entrypoints/check-file-lines.sh $(if $(MAX_LINES),--max-lines $(MAX_LINES))
	@echo "[IMP:9][make][check-file-lines] Check complete"

## check-dead-code: CI gate — detect DEPRECATED markers older than 30 days
##   Scans all .sh and .py files for stale DEPRECATED markers with git-log age check
check-dead-code:
	@echo "[IMP:7][make][check-dead-code] Checking for stale DEPRECATED markers..."
	@bash $(_platform_root)/core/entrypoints/check-dead-code.sh
	@echo "[IMP:9][make][check-dead-code] All DEPRECATED markers within grace period"

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
pre-commit-run:
	@echo "[IMP:7][make][pre-commit-run] Running all pre-commit hooks..."
	@pre-commit run --all-files 2>&1 || { \
		echo "[IMP:9][make][pre-commit-run] Some pre-commit hooks failed — review output above"; \
		exit 1; \
	}
	@echo "[IMP:9][make][pre-commit-run] All pre-commit hooks passed"

## scripts-audit: Проверить регистрацию всех shebang-скриптов в manifest или exceptions
scripts-audit:
	@echo "[IMP:7][make][scripts-audit] Auditing shebang script registration..."
	@bash $(_platform_root)/core/internal/scripts-audit.sh

## secrets-unlock: Decrypt SOPS/age secrets
##   Usage: make secrets-unlock [NODE=<name>]
##   Delegates to core/entrypoints/secrets.sh
secrets-unlock:
	@echo "[IMP:7][make][secrets-unlock] Decrypting secrets..."
	@$(_platform_root)/core/entrypoints/secrets.sh $(NODE)
	@echo "[IMP:9][make][secrets-unlock] Secrets decrypted"

## check-exception-patterns: CI gate — ensure bare except Exception only in __main__ or # noqa: EXC-marked
check-exception-patterns:
	@echo "[IMP:7][gate] Checking for bare except Exception in non-CLI code..."
	@! grep -rEn 'except[[:space:]]+Exception' core/internal/ --include='*.py' \
		| grep -vE ':[[:space:]]*#' \
		| grep -v '__main__' \
		| grep -v '# noqa: EXC' \
		|| (echo "[IMP:9][gate] FAIL: bare except Exception found in non-CLI code" && exit 1)
	@echo "[IMP:9][gate] All exception handlers are typed — OK"


## audit: Run platform system audit
##   Usage: make audit [NODE=<name>]
##   Delegates to core/entrypoints/audit.sh
audit:
	@echo "[IMP:7][make][audit] Running platform audit..."
	@$(_platform_root)/core/entrypoints/audit.sh $(NODE)
	@echo "[IMP:9][make][audit] Audit complete"
