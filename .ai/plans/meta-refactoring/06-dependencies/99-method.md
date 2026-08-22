# 99-method · Method, scopes, limitations

## Execution
- 9 parallel research subagents (explore), 2 waves × read-only greps/reads. Main model: recon, ID assignment, synthesis. No code modified; no make targets run.
- Wave 1: hubs/fan-in · cycles · hidden deps · mutable state. Wave 2: infra leakage · init-order · shell↔make coupling · unstable abstractions · gate-pinned amplification.

## Evidence standard
- Every finding: severity, category, files+symbols, evidence (file:line), scenario, impact, confidence, minimal decoupling, churn (S/M/L), regression risk, phase.
- HIGH confidence = import lines/callers/fields read directly. MED = strong grep signal with partial trace. `HYPOTHESIS` markers = plausible-unverified, never stated as fact.
- TRAP[BUG]/TRAP[DECISION]/TRAP[INCIDENT] comments used as evidence of intent — reduces false positives on "unusual-looking" code (e.g. DEP-0009, DEP-0027 mitigations, DEP-0035 bypass).

## Known limitations
- Subagent step limits left unverified: full node_yaml consumer count (~33 seen vs claimed ≥26py+8sh); 7 small entrypoints' LOC unmeasured; CI workflow contents inferred from docs in one agent (DEP-0046 MED confidence); notifications.py:253 lru_cache env-dependency unread; practices/check_project env_facts usage unconfirmed.
- Fan-in counts ±2 (grep-based distinct-file counts).
- Tests counted only as coupling-breadth evidence, not audited themselves (out of scope for dependency wave).

## Stats
- Production tree scanned: core/ = 368 .py (~113k LOC) + 76 .sh + core/lib/*.sh
- Findings: 60 (CRITICAL 4 / HIGH 12 / MED 27 / LO 17 incl. positive controls)
- Pre-launch quick-win list: 12 items (see 00-cascade-answer.md), all S churn
