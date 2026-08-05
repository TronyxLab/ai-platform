# GREP_SUMMARY: makefile, include-split, bootstrap, deploy, scaffold, modules, ci, dev, helpers
# STRUCTURE: ┌variables┐ → ◇ include makefiles/*.mk → ◇ .DEFAULT_GOAL → ⎋ 72 .PHONY targets across 9 includes
# region MODULE_CONTRACT
## @purpose  Root Makefile for ai-platform — unified facade, delegates to makefiles/*.mk via include
## @scope    Variables + COMPOSE_PROFILES + includes — all targets in makefiles/{bootstrap,deploy,scaffold,modules,ci,dev,helpers}.mk
## @invariants
##   - Makefile is the single entry point — no direct shell script calls (AGENTS.md §1)
##   - Every .PHONY target must be in entrypoint-manifest.yaml allowed_verbs
##   - Pre-commit hooks run via lint.sh directly; validate.sh --lint via `make lint` (CI/manual)
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
COMPOSE_BASE_FILES := -f docker-compose.yml -f docker-compose.platform-dev.yml
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

# === Default target ===
.DEFAULT_GOAL := help
