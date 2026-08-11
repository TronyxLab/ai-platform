# makefiles/loadtest.mk — тонкий фасад нагрузочного тестирования (DevPlan 146)
# GREP_SUMMARY: loadtest make load-test facade runner-cli scenarios modes
# STRUCTURE: ┌vars SCENARIO/NODE/MODE/LOAD_RUNNER┐ → ◇ load-test → python3 -m core.internal.loadtest.runner_cli
#           → ⎋ exit-code passthrough (0/1/4/10 по контракту shared/contracts.py)
# region MODULE_CONTRACT
## @purpose  Make-фасад подсистемы нагрузочного тестирования (DevPlan 146 W1, D6):
##           таргет load-test пробрасывает SCENARIO/NODE/MODE в runner_cli.py.
##           Языковая политика: НИКАКОЙ бизнес-логики в make — только python3 -m.
## @scope    makefiles/loadtest.mk — подключается Makefile; таргет регистрируется
##           в entrypoint-manifest.yaml (loadtest: секция) и глоссарии (generate-agents-md).
## @invariants
##   - Один таргет load-test (режим — переменная MODE), фасад <50 строк
##   - Переменные: SCENARIO (default web), NODE (default $(NODE)), MODE (smoke|regression|capacity)
##   - LOAD_RUNNER/LOAD_RPS/LOAD_DURATION/... пробрасываются env-ом (runner читает сам)
##   - exit-код runner_cli пробрасывается в make без маппинга (0/1/4/10 контракт)
## @rationale Тонкий фасад (D6 Brief 146): весь exit-контракт и guard-ы — в Python
##            (runner_cli.py), make — только удобная точка входа для оператора.
## @changes  2026-08-11 | DevPlan 146 W1 — Created
# endregion MODULE_CONTRACT

LOADTEST_SCENARIO ?= web
LOADTEST_NODE ?= $(NODE)
LOADTEST_MODE ?= smoke

.PHONY: load-test
load-test: ## Запуск нагрузочного теста: make load-test SCENARIO=web NODE=<node> MODE=smoke
	python3 -m core.internal.loadtest.runner_cli \
		--scenario $(LOADTEST_SCENARIO) --node $(LOADTEST_NODE) --mode $(LOADTEST_MODE)
