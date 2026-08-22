# Findings 004 · Hidden dependencies (CRITICAL/HIGH)

## DEP-0016 · static detector `--only` accepts unknown names → silent false PASS
- Severity: CRITICAL · Category: hidden-dep · Confidence: HIGH
- Files: `core/internal/static/registry.py:65,72-143,166`, `static/__main__.py:156`, driver `core/check-suite.yaml:80`
- Dependency chain: check-suite.yaml `cmd: ... static check --only exception_patterns` → importlib by name → filter `if spec.name not in only: continue`
- Coupling mechanism: detector names live in 3 stores (registry tuple, module filenames, check-suite strings) with NO existence validation of `--only`
- Why dangerous: rename a detector (e.g. `exception_patterns`→`exceptions`) → `--only` matches nothing → 0 findings → exit 0 → audit gate reports PASS while checking nothing
- Evidence: registry.py:166 filter logic; __main__ exit contract `return 1 if findings else 0`
- Scenario: launch-week detector refactor silently disables a static audit gate; team believes checks run
- Impact: false confidence in exactly the checks that guard the release
- Minimal decoupling: validate `--only` against registry names; unknown name → exit 2 (config error)
- Code churn: S · Regression risk: LOW · Phase: **Pre-launch candidate** (highest value/effort ratio in audit)

## DEP-0017 · `AGE_SECRET_KEY` contract string hardcoded in 35+ files (lower bound)
- Severity: HIGH · Category: hidden-dep · Confidence: HIGH
- Files: SoT `secret-definitions.yaml:114`; consumers: `makefiles/bootstrap.mk`, `entrypoints/{bootstrap,core-deliver,secrets}.sh`, `node-lifecycle.sh:24,44`, `lib/secrets.sh:37-49`, `node_detect.py:119-191`, `ssh_cmd_builder.py:192,251`, `core_deliverer.py:637`, `core-deploy.yml:258`, 8+ test files
- Dependency chain: secret name → shell/make/python/CI consumers with no shared constant
- Coupling mechanism: literal string duplication across 4 languages (yaml/make/sh/py)
- Why dangerous: rename in SoT → consumers silently keep old name → partial decrypt failures at RUNTIME on node, not at gate (parity gate covers only generated files)
- Evidence: grep hit limit 150 matches / 35+ distinct files
- Scenario: secrets rotation/rename during launch hardening → bootstrap node cannot decrypt, discovered mid-deploy
- Impact: secrets pipeline is launch-critical; failure mode is runtime-only
- Minimal decoupling: pre-launch: freeze the name (policy); post-launch: single `lib/secrets.sh` accessor + grep gate for literals outside allowlist
- Code churn: M (post-launch) · Regression risk: MED · Phase: **Pre-launch freeze**, post-launch constant

## DEP-0018 · file-path importlib dispatch to siblings with warning-swallow (φ7/φ8)
- Severity: HIGH · Category: hidden-dep · Confidence: HIGH
- Files: `core/internal/bootstrap/lifecycle/helpers/domains.py:72-73,117-118,167-170`; related `cert_orchestrator.py:679` (hardcoded `python3` bypassing venv); TRAP[BUG] 2026-08-03 at :76,121
- Dependency chain: lifecycle phase helpers → `spec_from_file_location("context_deployer", Path(core_dir)/"internal"/"bootstrap"/"deploy"/"context_deployer.py")`
- Coupling mechanism: string-built filesystem path + importlib; wrapped in `except Exception → warning`
- Why dangerous: moving/renaming context_deployer.py breaks φ7/φ8 SILENTLY — catch-all downgrades to warning, phase completes `done_with_warnings`; TRAP covers sys.modules ordering, not the path string
- Evidence: domains.py path construction + :102,128 warning fallbacks
- Scenario: deploy-path refactor renames context_deployer.py → context deploy phases silently no-op during bootstrap; discovered only by e2e
- Impact: silent degradation of the node deploy pipeline
- Minimal decoupling: direct guarded import (module exists in same package tree); keep importlib only for the documented standalone-script case
- Code churn: S · Regression risk: MED · Phase: **Pre-launch candidate** (fail-loud > fail-soft here)

## DEP-0019 · detector-name triple coupling: registry ↔ filenames ↔ check-suite strings
- Severity: MED · Category: hidden-dep · Confidence: MED (parity gates partially cover)
- Files: `static/registry.py:72-143` (14 names), `core/internal/static/<detector>.py` filenames, `check-suite.yaml:80`, `agent_check/__init__.py:641-657`
- Coupling mechanism: same logical names in 3 disjoint stores; rename file only → ImportError (visible); rename registry name only → `--only` no-op (silent, see DEP-0016)
- Why dangerous: two of three rename directions are silent or late
- Evidence: registry import-by-name `_import_detector("dead_code")`
- Scenario: partial rename → agent_check runs stale module or skips detectors
- Impact: static-audit integrity
- Minimal decoupling: single names list in registry; derive file names + validate check-suite strings at load (ties into DEP-0016 fix)
- Code churn: S · Regression risk: LOW · Phase: Pre-launch candidate (same fix as DEP-0016)
