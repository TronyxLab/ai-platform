# 01-architecture — Forensic Architecture Audit

## Scope

Pre-launch forensic audit of `ai-platform` (~953 py files / ~290k LOC incl. tests; 77 shell scripts; 15 docker modules).
Read-only: no code changed. Method: 10 parallel forensic subagents per run, one per direction, each returning provable
findings with file:line evidence; curator deduplicated, assigned IDs, ranked.

## ⚠️ Provenance — three runs wrote here

Parallel sessions used this folder concurrently. Current content groups:

| Group | Files | What |
|-------|-------|------|
| **run-a** (2026-08-22, consolidated by its sweep) | `findings-001…010.md`, `summary.md` (merged run-a+run-b, 103 findings), `attic/` (displaced originals incl. `attic/run-b/`) | First consolidated set; its summary documents the run-a/run-b collision itself |
| **run-c** (2026-08-24 re-run, this session) | `findings-01-boundaries.md` … `findings-10-hotspots.md`, `summary-run-c.md` | Independent re-run on current tree: 48 findings `ARCH-0001…0048`. Two files (01, 10) lost in the run-a sweep were rewritten verbatim from session record |

ID namespaces do NOT overlap: run-a uses `ARCH-01xx/02xx/40xx/80xx…`, run-c uses `ARCH-0001…0048`.
Cross-run agreement on key risks (PRIVOXY_PORT reach-in, shared-leaf breach, state-machine hash gaps,
deploy god-cluster) is corroborating evidence, not duplication.

## Directions → run-c files

| # | Direction | File |
|---|-----------|------|
| 1 | Module/package boundaries | `findings-01-boundaries.md` |
| 2 | Dependency direction | `findings-02-dep-direction.md` |
| 3 | Circular dependencies | `findings-03-cycles.md` |
| 4 | God modules/classes | `findings-04-god-modules.md` |
| 5 | Hidden global state | `findings-05-global-state.md` |
| 6 | Infra/application/domain coupling | `findings-06-coupling.md` |
| 7 | Duplicated business logic | `findings-07-duplication.md` |
| 8 | Initialization/lifecycle architecture | `findings-08-lifecycle.md` |
| 9 | Abstractions & overengineering | `findings-09-abstractions.md` |
| 10 | Architectural hotspots | `findings-10-hotspots.md` |

## Finding format

`ARCH-XXXX`: Severity (P0–P3) · Confidence (0–1) · Files · Symbols · Evidence · Failure/maintenance scenario ·
Impact · Minimal fix · Code churn (S/M/L) · Phase (pre-launch / post-launch).

## Exclusions

Documented decisions (TRAP[DECISION] in AGENTS.md/code: 3 template mechanisms, dual delivery, thin-shell policy,
L1→L2 collapse, etc.) are context, not findings. Issues already enforced by `tests/gates/` are marked "already enforced".

TOP-10: run-c → `summary-run-c.md`; merged runs a+b → `summary.md`.
Companion security audit: `.ai/plans/meta-refactoring/03-security/` (49 findings `SEC-0001…0049`, verdict in `BLOCKERS.md`).
