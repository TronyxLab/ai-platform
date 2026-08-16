# GREP_SUMMARY: makefile, include-split, bootstrap, deploy, scaffold, modules, ci, dev, helpers
# STRUCTURE: ┌variables┐ → ◇ include makefiles/*.mk → ◇ .DEFAULT_GOAL → ⎋ 72 .PHONY targets across 9 includes
# region MODULE_CONTRACT
## @purpose  Root Makefile for ai-platform — unified facade, delegates to makefiles/*.mk via include
## @scope    Variables + COMPOSE_PROFILES + includes — all targets in makefiles/{bootstrap,deploy,scaffold,modules,ci,dev,helpers}.mk
## @invariants
##   - Makefile is the single entry point — no direct shell script calls (AGENTS.md §1)
##   - Every .PHONY target must be in entrypoint-manifest.yaml allowed_verbs
##   - Pre-commit hooks run via lint.sh directly; validate.sh --lint — суит 'lint' check-suite.yaml (прямой вызов, План 175 W2.2)
##   - Makefile include-split preserves tab-sensitive parsing (W4-E4)
## @rationale  Centralized Makefile eliminates scattered scripts (RC-6). W4-E4 split: 747 → <150 LOC
##             with 6 thematical .mk files for navigability and reduced merge conflicts.
##             AGENTS.md invariant #1 mandates Makefile as single entry point.
## @changes 2026-07-22 | W4-E4: Makefile include-split — 747→~80 LOC, targets moved to makefiles/*.mk
# endregion MODULE_CONTRACT

SHELL := /bin/bash

# === Python virtualenv ===
VENV := .venv
PYTHON := $(VENV)/bin/python
PIP := $(VENV)/bin/pip

# === Platform root (resolved relative to this Makefile) ===
_platform_root := $(abspath $(dir $(lastword $(MAKEFILE_LIST))))

# === Docker Compose profiles — all Docker modules ===
# SoT: core/platform-infra.yaml env_defaults.COMPOSE_PROFILES (DevPlan 116 T2, U-02).
# Runtime-чтение через yaml_query.py — никаких хардкод-копий вне allowlist
# {platform-infra.yaml, platform-env.yaml, .env.example}.
# Uses ?= so existing env takes precedence.
export COMPOSE_PROFILES ?= $(shell python3 core/internal/scripts/yaml_query.py --file core/platform-infra.yaml --get env_defaults.COMPOSE_PROFILES)

# === Docker Compose shared files (resolved at parse time, used by modules.mk) ===
# DevPlan 002 W3 T3.4: docker-compose.platform-dev.yml удалён (L1 dev-оверрайд мёртв —
# единый образ hermes-agent-context с CONTEXT=test)
COMPOSE_BASE_FILES := -f docker-compose.yml
ifeq ($(shell uname -s),Darwin)
    ifneq ($(wildcard docker-compose.macos.yml),)
        COMPOSE_BASE_FILES += -f docker-compose.macos.yml
    endif
endif

# === Includes — all targets defined in makefiles/ ===
include makefiles/bootstrap.mk
include makefiles/deploy.mk
include makefiles/scaffold.mk
include makefiles/modules.mk
include makefiles/ci.mk
include makefiles/dev.mk
include makefiles/helpers.mk
include makefiles/repair.mk
include makefiles/manifest.mk
include makefiles/project-practices.mk
include makefiles/loadtest.mk

# === Default target ===
.DEFAULT_GOAL := help

# === Make output logging — every invocation's output persisted to logs/make/ ===
# SHELL wrapper (scripts/make-log-shell.sh) tees each recipe line into $MAKE_LOG_FILE.
# Per-run file: logs/make/<ts>-<goal>[<variant>][-N].log; logs/make/latest.log → symlink to last run.
#   <ts>      — %Y%m%d-%H%M%S (когда)
#   <goal>    — первый таргет (check/gate/check-diff/test-node/...)
#   <variant> — ЧТО именно гонялось: MARKER/MODE/TEST_FILE/PROJECT/NODE/SCENARIO
#               (make check MARKER=contract → ...-check-contract.log; make gate MODE=fast → ...-gate-fast.log)
#   [-N]      — суффикс коллизии: атомарное создание (noclobber, set -C) — два прогона
#               с одинаковым ts+goal+variant (параллельные агенты/одна секунда) НЕ
#               перезаписывают друг друга: второй получает -2, -3, ...
# Recursive/nested make (pre-commit hooks etc.) inherits MAKE_LOG_FILE via env and
# appends to the SAME file; only the top-level make (MAKELEVEL=0) creates files/symlink.
# Disable with: make <target> MAKE_LOG_DISABLE=1
ifneq ($(MAKE_LOG_DISABLE),1)
MAKE_LOG_DIR := logs/make
MAKE_LOG_GOAL := $(subst /,_,$(firstword $(MAKECMDGOALS)))
ifeq ($(MAKE_LOG_GOAL),)
MAKE_LOG_GOAL := default
endif
MAKE_LOG_TS := $(shell date +%Y%m%d-%H%M%S)
# Вариант цели: из имени видно, ЧТО именно прогонялось (пустые переменные → без суффикса).
# БЕЗ переносов строк — иначе $(if ...) добавляет пробелы в имя файла.
MAKE_LOG_VARIANT := $(if $(MARKER),-$(subst /,_,$(MARKER)))$(if $(MODE),-$(MODE))$(if $(TEST_FILE),-$(subst /,_,$(TEST_FILE)))$(if $(PROJECT),-$(subst /,_,$(PROJECT)))$(if $(NODE),-$(subst /,_,$(NODE)))$(if $(SCENARIO),-$(subst /,_,$(SCENARIO)))
# Уникальность: атомарное создание (umask 077 + noclobber); коллизия → суффикс -N.
# ⚠️ Простая переменная (:=) + origin-гард, НЕ ?=: ?= создаёт ЛЕНИВУЮ переменную —
# RHS с $(shell) перевычисляется при КАЖДОМ раскрытии $(MAKE_LOG_FILE) (banner, latest.log)
# и создавал бы файл-коллизию (DEVPLAN 165 follow-up: 3 файла в одну секунду).
# origin = environment|command line (nested make наследует) → RHS не вычисляется.
ifeq ($(origin MAKE_LOG_FILE),undefined)
MAKE_LOG_FILE := $(shell mkdir -p "$(MAKE_LOG_DIR)"; _f="$(MAKE_LOG_DIR)/$(MAKE_LOG_TS)-$(MAKE_LOG_GOAL)$(MAKE_LOG_VARIANT).log"; n=1; while ! (umask 077; set -C; : > "$$_f") 2>/dev/null; do _f="$(MAKE_LOG_DIR)/$(MAKE_LOG_TS)-$(MAKE_LOG_GOAL)$(MAKE_LOG_VARIANT)-$$n.log"; n=$$((n+1)); done; printf '%s' "$$_f")
endif
export MAKE_LOG_FILE
ifeq ($(MAKELEVEL),0)
# Файл уже создан атомарно в формуле имени; здесь — только симлинк latest.log + баннер
$(shell rm -f $(MAKE_LOG_DIR)/latest.log && ln -s $(notdir $(MAKE_LOG_FILE)) $(MAKE_LOG_DIR)/latest.log)
# Banner → stderr: stdout make-вывода остаётся machine-readable (parity-гейт
# test_gate_profiles_parity сравнивает stdout `make _get_all_profiles` с SoT).
$(shell printf '==> make log: %s  [latest: logs/make/latest.log]\n' '$(MAKE_LOG_FILE)' >&2)
endif
SHELL := $(_platform_root)/scripts/make-log-shell.sh
endif
