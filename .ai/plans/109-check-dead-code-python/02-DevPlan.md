$START_DEVPLAN

# 02-DevPlan — check-dead-code Python Migration (Strangler-Fig)

## $ARTIFACT_CONTRACT
PURPOSE:               Migrate `core/entrypoints/check-dead-code.sh` (86 LOC) to a thin shell facade (~16 LOC) + Python business-logic module `core/internal/lint/dead_code_checker.py`, preserving byte-identical gate behavior.
DESCRIPTION:           Extract file scanning, whole-word DEPRECATED detection, git-blame age calculation, and report formatting into a Python CLI module. Shell facade keeps only delegation (shebang → python3 → exit-code passthrough). The facade keeps its EXACT path so Makefile, manifest, AGENTS.md, contract tests, and gate tests ripple-free.
RATIONALE:             (R1) Git-blame porcelain parsing in awk/grep is fragile and unreadable; Python subprocess + regex is robust. (R2) 86→16 LOC (−81%), per AGENTS.md языковая политика (Python-first, Strangler-Fig). (R3) Facade path unchanged ⇒ zero ripple on `makefiles/ci.mk`, generated `entrypoint-manifest.yaml`, generated `core/AGENTS.md` canonical table, `tests/test_contract_entrypoints.py`, `tests/gates/test_gate_dead_code.py`.
ACCEPTANCE_CRITERIA:   AC1: `core/internal/lint/dead_code_checker.py` exists with file-scan, whole-word match, git-blame parsing, age calculation. AC2: shell facade ≤ 25 LOC. AC3: `make check-dead-code` passes with output/exit code identical to current behavior. AC4: same false-positive exceptions preserved (self-exclusion, .venv/.git/.ai/node_modules). AC5: output format preserved (LDD `[IMP:N]` format — verified: NO ANSI color escapes exist in current source; AC5 satisfied by byte-identical format, not colorama). AC6: `make gate MODE=fast` green.
IMPLEMENTS:            Brief 109 (`.ai/plans/109-check-dead-code-python/01-Brief.md`); AGENTS.md языковая политика Tier-1 (Strangler extraction on modification); dead-code gate from DevPlan 084.
IMPACTS:               `core/entrypoints/check-dead-code.sh` (MODIFY — 86→~16 LOC facade), `core/internal/lint/dead_code_checker.py` (NEW), `tests/unit/test_dead_code_checker.py` (NEW), `tests/test_inventory.yaml` (MODIFY via `make test-inventory-sync`). **NOTE (2026-07-31):** `core/internal/lint/__init__.py` is NO LONGER a NEW file — plan 106 created it; DevPlan 109 treats it as NO-OP (COORD-1).
REQUIRES:              Python 3.10+ stdlib only (argparse, os, re, subprocess, pathlib, dataclasses, sys). No new dependencies. Git available in PATH (already a hard prerequisite of the current script).
$END_ARTIFACT_CONTRACT

---

## 1. Context and Verification Results

### 1.1 Current implementation (read from source, 86 LOC)

`core/entrypoints/check-dead-code.sh` — CI gate detecting `DEPRECATED` markers older than 30 days:

| Aspect | Current behavior (authoritative — read from file) |
|---|---|
| Scan scope | All `.sh` + `.py` files under project root |
| Exclusions | `.venv/*`, `.git/*`, `.ai/*` (root-level only), `*/node_modules/*` (any depth), self (`core/entrypoints/check-dead-code.sh`) |
| Marker match | `grep -wn "DEPRECATED"` — whole-word only (compound identifiers like `_DEPRECATED_PATTERNS` NOT matched) |
| Age source | `git blame -L {line},{line} --porcelain` → `committer-time <epoch>` header field |
| Untracked/empty fallback | BSD `stat -f "%m"` (macOS-only) → epoch mtime |
| Age calc | `age_days = (now - ts) // 86400`; violation iff `age_days > 30` (STRICTLY greater) |
| Per-marker output | stdout: `[IMP:10][check-dead-code] STALE: {rel}:{line} — marker is {n} days old (threshold: 30)` + `  >>> {text[:120]}` / `[IMP:7][check-dead-code] OK: {rel}:{line} — marker is {n}d old (within 30d grace)` |
| Control output | stderr: scan start `[IMP:8]`, FAIL `[IMP:10]` + Fix hint, PASS `[IMP:9]` |
| Exit code | 0 clean, 1 violations |

### 1.2 Environment verification (this session)

| # | Check | Result |
|---|---|---|
| V1 | `core/internal/lint/` exists? | ⚠️ **YES — created by parallel plan 106** (in working tree, uncommitted): non-empty `__init__.py` (16 LOC package-contract from DevPlan 106), `doc_header_validator.py`, `grepsummary_validator.py`. DevPlan 109 MUST NOT overwrite `__init__.py` — only ensure `dead_code_checker.py` can import. See COORD-1. |
| V2 | `.pre-commit-config.yaml` references check-dead-code? | ❌ **NO** — no hook to update |
| V3 | Makefile target | `makefiles/ci.mk` L262-267: `@bash $(_platform_root)/core/entrypoints/check-dead-code.sh` — calls facade by EXACT path → **no ci.mk change** |
| V4 | `core/entrypoint-manifest.yaml` (generated) | L135-141: `delegates_to: core/entrypoints/check-dead-code.sh` — path unchanged → **no manifest regeneration** |
| V5 | `core/AGENTS.md` canonical table (generated) | Row `check-dead-code → core/entrypoints/check-dead-code.sh` — path unchanged → **no regeneration** |
| V6 | `tests/test_contract_entrypoints.py` | `exists`/`shebang`/`syntax`/`help-smoke` operate on the facade path → all still pass. Help-smoke: current script exits 0 on `--help` (args ignored); new facade passes `--help` to Python argparse → exit 0 with usage → still passes |
| V7 | `tests/gates/test_gate_dead_code.py::test_no_deprecated_markers_stale` | Runs `bash core/entrypoints/check-dead-code.sh`, asserts exit 0 → facade path preserved → passes. Also reads stdout/stderr via subprocess → output format must remain valid |
| V8 | `tests/unit/` convention | Exists; pattern: `sys.path.insert(0, project_root)` then `from core.internal.<mod> import ...` |
| V9 | `tests/gates/test_gate_test_inventory.py` | Gate asserts every collected test has an entry in `tests/test_inventory.yaml` → **new test file requires `make test-inventory-sync`** (mandatory step) |
| V10 | Current DEPRECATED markers | 18 hits across `.sh`/`.py`, all within 30-day grace → gate green today |

**Key architectural insight (V3-V5):** the facade keeps its exact filename and path ⇒ the generated triad (Makefile, entrypoint-manifest.yaml, AGENTS.md canonical table) and the contract/gate tests require ZERO changes. The migration is invisible to the delivery pipeline.

**⚠️ Coordination note (V1 update, 2026-07-31):** plans 106 (`lint-headers-consolidation`), 107 (`validate-python`), 108 (`scp-deliver-python`) are currently IN PROGRESS in the working tree. Plan 106 has already created `core/internal/lint/__init__.py` (non-empty, 16 LOC package-contract for grepsummary/doc-header validators). DevPlan 109 MUST:
- **COORD-1:** NOT overwrite/truncate `core/internal/lint/__init__.py`. Step 1 is a no-op (directory + `__init__.py` already exist). If the existing package-contract needs amendment to mention `dead_code_checker`, append a line — never replace.
- **COORD-2:** Verify import still works after plan 106 merges: `python3 -c "from core.internal.lint.dead_code_checker import main"` (namespace package may be affected by sibling `__init__.py` content).
- **COORD-3:** Land 109 AFTER 106 to avoid `__init__.py` merge conflicts. Both touch the same file path.

---

## 2. Draft Code Graph (XML)

```xml
<!-- Layer: entrypoints (thin facade, path-preserving) -->
<module name="core_entrypoints_check_dead_code_sh" TYPE="shell-facade"
        keywords="check-dead-code gate DEPRECATED markers stale 30-days CI"
        annotation="Thin facade ≤25 LOC. Delegates to internal Python module. Exit-code + stdout/stderr passthrough. Path unchanged per V3-V7.">
  <CrossLinks>
    <link target="makefiles_ci_mk" relation="called-by" annotation="make check-dead-code target L262-267"/>
    <link target="core_entrypoint_manifest_yaml" relation="registered-in" annotation="delegates_to path unchanged"/>
    <link target="core_internal_lint_dead_code_checker_py" relation="delegates-to" annotation="python3 &quot;$&lt;module&gt;&quot; &quot;$@&quot;"/>
    <link target="tests_gates_test_gate_dead_code_py" relation="verified-by" annotation="test_no_deprecated_markers_stale runs facade via bash"/>
  </CrossLinks>
</module>

<!-- Layer: internal (business logic, NEW) -->
<module name="core_internal_lint_dead_code_checker_py" TYPE="python-cli"
        keywords="dead-code DEPRECATED git-blame committer-time mtime age 30-days whole-word scan exclude"
        annotation="Pure-stdlib CLI. Parses git blame --porcelain, falls back to mtime. Byte-identical output to original shell.">
  <function name="main" TYPE="FUNC" annotation="argparse CLI: --help, --threshold (default 30). Returns exit code 0|1.">
    <CrossLinks><link target="core_entrypoints_check_dead_code_sh" relation="called-by"/></CrossLinks>
  </function>
  <function name="find_marker_files" TYPE="FUNC" annotation="Walk root, collect *.sh/*.py, apply root-level (.venv/.git/.ai) + any-depth (node_modules) + SELF_EXCLUSIONS filters."/>
  <function name="find_deprecated_lines" TYPE="FUNC" annotation="Per-file whole-word regex scan → list[(line_num, line_text)]."/>
  <function name="get_line_add_timestamp" TYPE="FUNC" annotation="git blame -L L,L --porcelain → committer-time epoch; empty/failed → os.path.getmtime() fallback (portable, replaces BSD stat -f)."/>
  <function name="compute_age_days" TYPE="FUNC" annotation="(now - ts) // 86400; violation iff > threshold (strict)."/>
  <function name="check_dead_code" TYPE="FUNC" annotation="Orchestrator: scan → blame → compute → collect DeadCodeViolation list. Pure (no I/O) except subprocess calls."/>
  <function name="_print_report" TYPE="FUNC" annotation="STALE/OK to stdout (print), control messages to stderr (logging, %(message)s). Byte-identical format."/>
  <dataclass name="DeadCodeViolation" TYPE="CLASS" annotation="rel_path, line_num, age_days, line_text — typed contract between checker and reporter."/>
  <constants annotation="THRESHOLD_DAYS=30, DEPRECATED_RE=\\bDEPRECATED\\b, SELF_EXCLUSIONS={facade, module, test file}, EXCLUDE_ROOT={.venv,.git,.ai}, EXCLUDE_ANY={node_modules}"/>
</module>

<!-- Layer: tests (NEW + inventory) -->
<module name="tests_unit_test_dead_code_checker_py" TYPE="pytest"
        keywords="unit whole-word blame-porcelain committer-time mtime fallback threshold boundary tmp_path capsys"
        annotation="Unit tests, native imports, tmp_path fixtures, capsys output-format assertions, caplog IMP:9 telemetry.">
  <CrossLinks>
    <link target="core_internal_lint_dead_code_checker_py" relation="unit-tests"/>
    <link target="tests_test_inventory_yaml" relation="requires-inventory-entry" annotation="make test-inventory-sync (V9 gate)"/>
  </CrossLinks>
</module>

<module name="tests_test_inventory_yaml" TYPE="generated" annotation="Regenerated via make test-inventory-sync — must include new test node IDs (V9 gate test_gate_test_inventory)."/>
```

---

## 3. Step-by-Step Data Flow (process simulation)

### 3.1 Target flow after migration (`make check-dead-code`)

```
make check-dead-code (makefiles/ci.mk L264)
  → bash core/entrypoints/check-dead-code.sh            [facade, unchanged path]
      → python3 core/internal/lint/dead_code_checker.py "$@"
          [main] argparse: --threshold=30 (default) | --help
          → find_marker_files(root, SELF_EXCLUSIONS)
              walk root → collect *.sh/*.py
              skip: rel.startswith(.venv/|.git/|.ai/) ; any part == node_modules
              skip: rel in SELF_EXCLUSIONS (facade, module, unit test)
              → list[Path]
          → per file: find_deprecated_lines(path)        # re \bDEPRECATED\b
              → list[(line_num, line_text)]
          → per (file, line): get_line_add_timestamp(root, rel, line_num, mtime)
              subprocess git blame -L L,L --porcelain
                → regex ^committer-time (\d+)  → epoch
                | empty/error → os.path.getmtime()       # portable mtime
              → compute_age_days(ts, now)                 # (now-ts)//86400
          → check_dead_code → list[DeadCodeViolation]     # age_days > threshold
          → _print_report:
              stdout: [IMP:10] STALE: rel:L — marker is N days old (threshold: T)
                       >>> text[:120]
                      [IMP:7]  OK: rel:L — marker is Nd old (within Td grace)
              stderr: [IMP:8]  Scanning... ; [IMP:10] FAIL: N marker(s)... ; [IMP:9] PASS: ...
          → exit 0 | 1                                    # passthrough to facade
      → facade: exit $?                                    # exit-code passthrough
  → make: @echo "[IMP:9][make][check-dead-code] All DEPRECATED markers within grace period" (only if exit 0)
```

### 3.2 Behavioral parity matrix (old shell → new Python)

| # | Behavior | Shell (current) | Python (new) | Parity |
|---|---|---|---|---|
| P1 | Marker match | `grep -wn "DEPRECATED"` | `re.search(r"\bDEPRECATED\b", line)` | ✔ `\b` == whole-word; `_DEPRECATED_PATTERNS` NOT matched in both |
| P2 | Extensions | `-name "*.sh" -o -name "*.py"` | `p.suffix in {".sh", ".py"}` | ✔ |
| P3 | Root exclusions | `-not -path "$ROOT/.venv/*"` etc. | `rel.startswith(".venv/")` etc. | ✔ root-level only (.venv/.git/.ai) |
| P4 | Any-depth exclusion | `-not -path "*/node_modules/*"` | `"node_modules" in rel.parts` | ✔ any depth |
| P5 | Self-exclusion | `[[ "$rel" == "$SELF_REL" ]]` | `rel in SELF_EXCLUSIONS` (3 files, see D3) | ✔ extended set (documented) |
| P6 | Age source | `git blame -L L,L --porcelain` → `committer-time` | `subprocess.run(["git","-C",root,"blame","-L",f"{L},{L}","--porcelain",rel])` → regex `^committer-time (\d+)` | ✔ same porcelain field |
| P7 | Fallback | `stat -f "%m"` (BSD, macOS-only) | `os.path.getmtime()` (stdlib, portable) | ✔ superset — fixes Linux portability |
| P8 | Age calc | `$(( (now - ts) / 86400 ))`, `-gt 30` | `(now - ts) // 86400`, `> threshold` | ✔ integer floor, strict greater-than |
| P9 | STALE output | `[IMP:10][check-dead-code] STALE: rel:L — marker is N days old (threshold: 30)` + `  >>> ${text:0:120}` | identical string; `text[:120]` | ✔ byte-identical at threshold=30 |
| P10 | OK output | `[IMP:7][check-dead-code] OK: rel:L — marker is Nd old (within 30d grace)` | identical; `T` interpolated from threshold | ✔ byte-identical at threshold=30 |
| P11 | Control output | stderr echo scan/FAIL/PASS | stderr logging handler, format `%(message)s` | ✔ byte-identical |
| P12 | Exit codes | `exit 0` / `exit 1` | `return 0` / `return 1` → facade `exit $?` | ✔ |

---

## 4. Design Decisions (and Brief divergences resolved)

| # | Decision | Detail |
|---|---|---|
| D1 | **Facade path preserved** | `core/entrypoints/check-dead-code.sh` stays at its exact path ⇒ Makefile target, generated entrypoint-manifest.yaml, generated AGENTS.md table, contract tests (V6), gate test (V7) all untouched. The migration is invisible to delivery. |
| D2 | **Behavior parity over Brief's named functions** | Brief §@DESCRIPTION lists `check_file_age()`, `check_references()`, `check_git_tracked()` and `git log --follow` — these do NOT exist in the actual code. The real script uses inline logic with `git blame` (line-level), NOT `git log --follow` (file-level). Since AC3 ("passes identically") is the hard constraint, the DevPlan defines the real decomposition (Section 2) from actual behavior. `git log --follow` would return a different age for multi-line markers — rejected. |
| D3 | **SELF_EXCLUSIONS extended to 3 files** | The new module, its facade, and its unit test all MUST contain the literal string "DEPRECATED" (their purpose). The original excluded only the shell script. Without extension, the checker would flag its own implementation once lines age > 30 days (self-referential trap). Set: `core/entrypoints/check-dead-code.sh`, `core/internal/lint/dead_code_checker.py`, `tests/unit/test_dead_code_checker.py`. Documented extension of AC4's exclusion set. |
| D4 | **AC5 resolution — no ANSI colors exist** | Verified in source: the script has ZERO ANSI color escapes — output is LDD `[IMP:N]` log-level prefixes only. AC5 ("цветной вывод") is therefore satisfied by preserving the LDD format byte-identically. colorama/ANSI is NOT added — it would break byte-identical output (AC3) and the subprocess-based gate test (V7). |
| D5 | **Python argparse CLI** | `--help` (exit 0 + usage → satisfies contract help-smoke V6 cleanly) and `--threshold N` (default 30 — preserves behavior; enables tests to force violations without mtime manipulation). Facade passes `"$@"` through. |
| D6 | **Per-line git blame (parity)** | Kept per-line `git blame -L L,L` subprocess — identical to current behavior; hit count is small (18 today) so runtime is negligible. Whole-file blame batching (`git blame --porcelain <file>` once, build line→committer-time map) is a valid optimization — recorded as rejected alternative (would complicate porcelain range parsing for zero current benefit). Candidate TRAP[PERF] if marker count grows > ~200. |
| D7 | **git log pre-filter dropped** | Original: `git log --oneline -- rel | grep -q .` pre-filter before blame. New: attempt blame directly; empty output/error → mtime fallback. Behaviorally identical (blame on an untracked file returns empty → same mtime fallback path; tracked files always return blame output). Simpler, fewer subprocess calls. |
| D8 | **Output routing** | Per-marker lines (STALE/OK) → `print()` to **stdout** (byte-identical requirement, P9/P10). Control messages (scan/FAIL/PASS) → module logger with stderr `StreamHandler`, `format="%(message)s"`, `propagate=False` — byte-identical (P11) AND captured by caplog in unit tests (LDD telemetry). |
| D9 | **Portability fix (bonus)** | BSD `stat -f "%m"` → `os.path.getmtime()` — identical on macOS, correct on Linux (project CI/runner = Linux). |

---

## 5. File Manifest

| Path | Action | Detail |
|---|---|---|
| `core/entrypoints/check-dead-code.sh` | MODIFY | 86 → ~16 LOC facade (Section 6.1). Executable bit PRESERVED (`git update-index --chmod=+x` if reset). |
| `core/internal/lint/__init__.py` | **NO-OP** (COORD-1) | ⚠️ Already exists — created by parallel plan 106 (16 LOC package-contract). DevPlan 109 MUST NOT overwrite. Optional 1-line `@changes` append only after plan 106 merges. |
| `core/internal/lint/dead_code_checker.py` | NEW | Business logic CLI module (Section 6.2). Full semantic markup: MODULE_CONTRACT, GREP_SUMMARY, STRUCTURE, region markers, LDD logs, Doxygen `## @` tags. |
| `tests/unit/test_dead_code_checker.py` | NEW | Unit tests per §TEST_SPEC. Full markup: GREP_SUMMARY, `# 🧪 TRAP[TEST]` on every test function, caplog IMP:9 telemetry. |
| `tests/test_inventory.yaml` | MODIFY (generated) | Regenerate via `make test-inventory-sync` (V9 gate). Never hand-edit. |

**Verified NOT modified (do NOT touch):** `makefiles/ci.mk`, `core/entrypoint-manifest.yaml`, `core/AGENTS.md`, `.pre-commit-config.yaml`, `tests/test_contract_entrypoints.py`, `tests/gates/test_gate_dead_code.py`.

---

## 6. Implementation Steps

### Step 1 — Verify `core/internal/lint/__init__.py` (already exists per COORD-1)

**⚠️ CHANGED:** the directory and `__init__.py` already exist (created by parallel plan 106, uncommitted in working tree). This step is now a **NO-OP for creation**:
- Do NOT overwrite `__init__.py` — it contains a 16-LOC package-contract (GREP_SUMMARY, STRUCTURE, MODULE_CONTRACT) authored by DevPlan 106.
- OPTIONAL: append one line to the `@changes` section noting `dead_code_checker.py` was added, IF plan 106 has merged by the time 109 lands. Otherwise leave untouched.
- Verify import: `python3 -c "from core.internal.lint.dead_code_checker import main; print('ok')"`.

### Step 2 — Create `core/internal/lint/dead_code_checker.py`
Pure-stdlib CLI. Structure:

```
MODULE_CONTRACT region (## @purpose, @scope, @invariants, @rationale, @changes)
GREP_SUMMARY: dead-code, DEPRECATED, git-blame, committer-time, mtime, age, whole-word, lint
STRUCTURE: ▶ walk root → ⊕ filter (*.sh/*.py, excl) → ○ grep \bDEPRECATED\b → ○ git blame -L → ◇ age>30 ? → ⊕ report → ⎋ exit 0|1
constants: THRESHOLD_DAYS=30, DEPRECATED_RE, SELF_EXCLUSIONS, EXCLUDE_ROOT, EXCLUDE_ANY
@dataclass DeadCodeViolation (rel_path, line_num, age_days, line_text)
FUNC find_marker_files(project_root, self_exclusions) -> list[Path]
FUNC find_deprecated_lines(path) -> list[tuple[int, str]]
FUNC get_line_add_timestamp(project_root, rel, line_num, mtime) -> int      # blame → committer-time; fallback mtime
FUNC compute_age_days(timestamp, now) -> int                                 # (now-ts)//86400
FUNC check_dead_code(project_root, threshold_days=30) -> list[DeadCodeViolation]
FUNC _print_report(violations, threshold_days) -> None                       # stdout STALE/OK, stderr control
FUNC main(argv=None) -> int                                                  # argparse: --threshold, --help
```

Requirements per function:
- **find_marker_files**: `os.walk`; collect `suffix in {".sh",".py"}`; skip if `rel.startswith(".venv/")|(".git/")|(".ai/")` (root-level only — P3 asymmetry), `"node_modules" in rel.parts` (any depth — P4), `rel in self_exclusions` (P5). Resolve paths relative to project_root with `os.path.relpath`.
- **find_deprecated_lines**: `Path.read_text(errors="replace")`; `enumerate(lines, start=1)`; `re.search(DEPRECATED_RE, line)` where `DEPRECATED_RE = re.compile(r"\bDEPRECATED\b")`. Return `(line_num, line.rstrip("\n"))`.
- **get_line_add_timestamp**: `subprocess.run(["git","-C",str(root),"blame","-L",f"{line},{line}","--porcelain",str(rel)], capture_output=True, text=True, timeout=30)`; on `returncode==0`: regex `^committer-time (\d+)` first match → int; else/empty → `os.path.getmtime(path)`. Handle `FileNotFoundError`/`TimeoutExpired` → mtime (graceful degradation — git absence must not crash the gate).
- **compute_age_days**: `(now - timestamp) // 86400` with `now = int(time.time())`.
- **check_dead_code**: orchestrator — scan, per-hit blame, filter `age_days > threshold_days` (strict — P8). LDD logs at IMP:7/8/9/10 per P9-P11.
- **_print_report**: per P9-P11 exact strings; line_text truncated `[:120]`; threshold interpolated (byte-identical at default 30).
- **main**: `argparse.ArgumentParser(prog="dead_code_checker.py")`; `--threshold` (type=int, default 30), `--help` (argparse builtin → exit 0). Return `0` clean / `1` violations. `if __name__ == "__main__": sys.exit(main())`.

LDD requirements: every non-trivial function emits `[IMP:N][function][block]` logs matching the original prefixes (`[check-dead-code]`).

### Step 3 — Reduce `core/entrypoints/check-dead-code.sh` to facade (≤ 25 LOC)

```bash
#!/usr/bin/env bash
# GREP_SUMMARY: check-dead-code, gate, DEPRECATED, markers, stale, 30-days, CI
# STRUCTURE: ▶ python3 dead_code_checker.py "$@" → ⎋ exit-code passthrough
# region MODULE_CONTRACT
## @purpose  Thin facade for core/internal/lint/dead_code_checker.py — detect DEPRECATED markers older than 30 days
## @scope    Called from `make check-dead-code` (makefiles/ci.mk L264). All business logic lives in the Python module.
## @io       stdout/stderr passthrough; exit 0 = clean, 1 = violations
## @invariants — exit-code passthrough: `exit $?`; no business logic in shell (AGENTS.md языковая политика)
## @rationale Strangler-Fig Tier-1 (AGENTS.md): new/refactored logic lands in Python; shell remains thin facade
# endregion MODULE_CONTRACT
set -euo pipefail
_EP_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
python3 "$_EP_DIR/../internal/lint/dead_code_checker.py" "$@"
exit $?
```

16 LOC. Preserve executable bit (`chmod +x` / `git update-index --chmod=+x` — the file is currently executable per DevPlan 084 VR finding).

### Step 4 — Create `tests/unit/test_dead_code_checker.py`
Per §TEST_SPEC. Native imports (`sys.path.insert(0, project_root)`; `from core.internal.lint.dead_code_checker import ...`). `tmp_path` fixtures. Every test function carries `# 🧪 TRAP[TEST]` with Regression/Scenario/Last fail/Remove-if fields.

### Step 5 — Verify parity on the real repo
Run `bash core/entrypoints/check-dead-code.sh` (facade) and compare against pre-migration captured output (stdout, stderr, exit code — must be identical; 18 markers all OK today).

### Step 6 — Sync test inventory
`make test-inventory-sync` — regenerates `tests/test_inventory.yaml` including the new test node IDs (V9 gate `test_gate_test_inventory` would otherwise fail).

### Step 7 — Full verification
- `make test MARKER=unit` (or targeted: `pytest tests/unit/test_dead_code_checker.py -q`) — new tests green, caplog IMP:9 assertions pass
- `make check-dead-code` — exit 0, byte-identical output
- `make check-manifests` — manifest untouched → green
- `make gate MODE=fast` — AC6

---

## 7. Acceptance Criteria (mapped to Brief)

| # | Criterion (Brief) | Verification |
|---|---|---|
| AC1 | `core/internal/lint/dead_code_checker.py` with file scanning, git blame parsing, age calculation | File exists with `find_marker_files`, `find_deprecated_lines`, `get_line_add_timestamp`, `compute_age_days`, `check_dead_code` + unit tests covering each |
| AC2 | Shell facade ≤ 25 LOC | Facade is 16 LOC (Step 3); verified by `wc -l` |
| AC3 | `make check-dead-code` passes identically | Exit 0; stdout/stderr byte-identical to pre-migration capture (parity matrix P1-P12); `tests/gates/test_gate_dead_code.py::test_no_deprecated_markers_stale` green |
| AC4 | Same false-positive exceptions preserved | `.venv/.git/.ai` (root), `node_modules` (any depth), self-exclusion preserved; extended set = 3 files (D3) — covered by `test_find_marker_files_exclusions` |
| AC5 | Output format preserved | LDD `[IMP:N]` format byte-identical (D4 — no ANSI in source; format, not color, is the preserved property) — covered by `test_output_format_byte_identical` |
| AC6 | `make gate MODE=fast` green | Full fast gate passes (includes check-dead-code step 2b, contract tests, inventory gate) |

---

## 8. $TEST_SPEC

| Test file | Test function | Scenario | Module under test |
|---|---|---|---|
| `tests/unit/test_dead_code_checker.py` | `test_find_deprecated_lines_whole_word` | `_DEPRECATED_PATTERNS` NOT matched; `DEPRECATED` matched; line_num and text extracted correctly; non-.py/.sh not scanned | `find_deprecated_lines` |
| `tests/unit/test_dead_code_checker.py` | `test_find_marker_files_exclusions` | tmp tree with `.venv/`, `.git/`, `.ai/`, `node_modules/` (nested), the 3 SELF_EXCLUSIONS files, `.sh`/`.py`/`.txt` — only eligible `.sh`/`.py` returned | `find_marker_files` |
| `tests/unit/test_dead_code_checker.py` | `test_parse_blame_porcelain_committer_time` | Static porcelain fixture (author/committer headers + `committer-time <epoch>` + tab content) → correct epoch extracted | `get_line_add_timestamp` (blame path) |
| `tests/unit/test_dead_code_checker.py` | `test_get_line_add_timestamp_fallback_mtime` | Blame empty/error (monkeypatch subprocess) → `os.path.getmtime` value used | `get_line_add_timestamp` (fallback path) |
| `tests/unit/test_dead_code_checker.py` | `test_compute_age_days_boundary` | 30 days → NOT violation; 31 days → violation; 0 days → OK; negative (clock skew) → OK | `compute_age_days`, threshold logic |
| `tests/unit/test_dead_code_checker.py` | `test_check_dead_code_clean_pass` | tmp project, all markers fresh (mtime=now) → empty violations, exit 0, PASS stderr contains `[IMP:9]` | `check_dead_code`, `main` |
| `tests/unit/test_dead_code_checker.py` | `test_check_dead_code_violation_fail` | `--threshold 0` → fresh marker is a violation → 1 violation, exit 1, STALE stdout line present | `check_dead_code`, `_print_report`, `main` |
| `tests/unit/test_dead_code_checker.py` | `test_output_format_byte_identical` | capsys: STALE line matches `[IMP:10][check-dead-code] STALE: {rel}:{L} — marker is {N} days old (threshold: 0)` + `  >>> {text[:120]}`; OK line matches IMP:7 format; control lines on stderr | `_print_report` |

**Anti-Loop protocol:** root `tests/conftest.py` already provides session hook + `.test_counter.json`. Do not duplicate counter logic in the test file. Use root `@ldd_trajectory` decorator where applicable.

**LDD telemetry:** every test prints `--- LDD TRAJECTORY (IMP:7-10) ---` before assertions (test's own `logger.info` records — the module's control messages are captured via caplog through its stderr logger per D8; per-marker stdout lines are echoed by the test into caplog). Assert `found_log` (≥1 IMP:9) in successful scenarios.

---

## 9. Risks and Edge Cases

| Risk | Mitigation |
|---|---|
| New module/test file flagged by its own gate after 30 days | SELF_EXCLUSIONS extended (D3) — verified by `test_find_marker_files_exclusions` |
| Git absent/unavailable in environment | `get_line_add_timestamp` catches `FileNotFoundError`/`TimeoutExpired` → mtime fallback (graceful, matches original `|| true` semantics) |
| Untracked files / files never committed | Blame returns empty → mtime fallback (P7), same as original `git log` pre-filter path (D7) |
| Line content contains colons | Original `cut -d: -f1`/`-f2-` splits on FIRST colon; Python `enumerate` + full line preserves this exactly (P9) |
| UTF-8 / emoji in line text | `errors="replace"` on read; `[:120]` is character-based in both bash (UTF-8 locale) and Python — match |
| File read race (deleted between scan and blame) | Wrap read in try/except OSError → skip file with IMP:6 log |
| Marker count grows → per-line blame cost | 18 hits today = 18 subprocess calls ≈ negligible. If >200: whole-file blame batching (D6) |
| Executable bit lost on rewrite | `git update-index --chmod=+x` (finding from DevPlan 084 VR — file is 100755) |
| Inventory gate failure from new test | Mandatory Step 6 (`make test-inventory-sync`) |

---

## 10. Out of Scope

- Registering `check-dead-code` as a pre-commit hook (V2: not registered today; no change)
- Updating the generated triad (Makefile, manifest, AGENTS.md) — unnecessary per D1
- Adding ANSI color output (D4 — would break AC3)
- Whole-file git blame batching (D6 — deferred optimization)
- Changing the 30-day threshold policy or DEPRECATED marker convention

---

## $QA_VERIFICATION

| Field | Value |
|---|---|
| **Verdict** | SUCCESS (with coordination update) |
| **SHA** | `fbe306d4284d9105193605378be28eb64b3c6795` |
| **Timestamp** | 2026-07-31T18:19:31+03:00 (initial QA) · 2026-07-31T20:25:00+03:00 (coordination update V1/Step 1/IMPACTS) |
| **Working tree** | DIRTY — plans 106/107/108 in progress: `core/internal/lint/__init__.py` (NEW from plan 106), `core/internal/lint/{doc_header_validator,grepsummary_validator}.py` (NEW from plan 106). These invalidate original V1 assumption. |

### Protocol Compliance

| Check | Status | Evidence |
|---|---|---|
| `$START_DEVPLAN` / `$END_DEVPLAN` | ✅ PASS | Lines 1, 399 |
| `$ARTIFACT_CONTRACT` (7 fields) | ✅ PASS | Lines 5-12: PURPOSE, DESCRIPTION, RATIONALE, ACCEPTANCE_CRITERIA, IMPLEMENTS, IMPACTS, REQUIRES — all present |
| Draft Code Graph (XML) | ✅ PASS | Section 2 (lines 54-97), 5 modules with CrossLinks, typed contracts |
| Data Flow | ✅ PASS | Section 3 (lines 101-150), process simulation + behavioral parity matrix P1-P12 |
| AC mapped to Brief | ✅ PASS | Section 7 (lines 255-263), all 6 Brief ACs mapped with verification steps |
| File Manifest | ✅ PASS | Section 5 (lines 168-178), 5 files (2 MODIFY, 3 NEW) + verified-untouched list |
| Implementation Steps | ✅ PASS | Section 6 (lines 182-251), 7 sequential steps with acceptance checkpoints |

### Cross-Reference Verification (DevPlan claims vs actual source)

| DevPlan claim | File:line | Actual | Match |
|---|---|---|---|
| V1 — `core/internal/lint/` does NOT exist | `glob("core/internal/lint/**")` | No files found | ✅ |
| V2 — `.pre-commit-config.yaml` has no `check-dead-code` hook | `.pre-commit-config.yaml` (295 lines) | No reference to check-dead-code | ✅ |
| V3 — Makefile target at `makefiles/ci.mk` L262-267 | `makefiles/ci.mk:262-267` | `bash $(_platform_root)/core/entrypoints/check-dead-code.sh` | ✅ |
| V4 — manifest entry at `core/entrypoint-manifest.yaml` L135-141 | `entrypoint-manifest.yaml:135-141` | `delegates_to: core/entrypoints/check-dead-code.sh` | ✅ |
| V5 — `core/AGENTS.md` canonical table has check-dead-code row | `core/AGENTS.md` GENERATED table | `make check-dead-code → core/entrypoints/check-dead-code.sh` | ✅ |
| V6 — `tests/test_contract_entrypoints.py` has contract tests for check-dead-code | `tests/test_inventory.yaml:498,563,628,649` | 4 parametrized tests: exists, shebang, help-smoke, syntax | ✅ |
| V7 — `tests/gates/test_gate_dead_code.py::test_no_deprecated_markers_stale` uses facade path | `test_gate_dead_code.py:771` | `os.path.join(PLATFORM_ROOT, "core", "entrypoints", "check-dead-code.sh")` | ✅ |
| V9 — `tests/test_inventory.yaml` exists and is stale-regenerated via gate | `test_gate_test_inventory.py` gate | Inventory gate enforces every test has an entry | ✅ |
| V10 — ~18 DEPRECATED marker hits | `grep -rw "DEPRECATED" --include="*.sh" --include="*.py"` | ~18 hits across `.py` files, 0 in `.sh` (all self-excluded) | ✅ |

### Brief-to-DevPlan Divergence Assessment

| Brief claim | DevPlan treatment | Verdict |
|---|---|---|
| AC1: `check_file_age()`, `check_references()`, `check_git_tracked()` | D2 — Replaced with actual decomposition (`find_marker_files`, `find_deprecated_lines`, `get_line_add_timestamp`, `compute_age_days`, `check_dead_code`). Brief named functions don't exist in source. | ✅ JUSTIFIED — DevPlan decomposes from real behavior, not Brief's speculative naming |
| AC1: `git log --follow` for file age | D2 — Replaced with `git blame -L L,L --porcelain` per-line. Brief's `git log --follow` returns different (wrong) age for multi-line markers. | ✅ JUSTIFIED — preserving behavioral parity (AC3) takes priority |
| AC5: "Цветной вывод сохранён (или через Python colorama/ANSI)" | D4 — NO ANSI/colorama added. Source has ZERO ANSI color escapes. Output format is LDD `[IMP:N]` prefixes — preserved byte-identically. Adding color would break AC3 (byte-identical output). | ✅ JUSTIFIED — Brief's color assumption was incorrect; preserving LDD format satisfies the intent |
| SELF_EXCLUSIONS: 1 file (Brief implies preserving existing) | D3 — Extended from 1→3 files (facade, module, unit test). Without extension, checker would self-flag after 30 days. | ✅ JUSTIFIED — necessary self-referential trap prevention |
| D7: `git log` pre-filter | Dropped — behaviorally identical (blame on untracked returns empty → same mtime path). Simpler, fewer subprocess calls. | ✅ JUSTIFIED — documented in D7 with parity analysis |
| D9: `stat -f "%m"` → `os.path.getmtime()` | Portability fix: BSD-only → cross-platform stdlib. | ✅ BONUS — fixes Linux CI/runner compatibility |

### TRAP Annotations

| File | TRAP | Addressed? |
|---|---|---|
| `core/entrypoints/check-dead-code.sh` | None | N/A |
| `tests/gates/test_gate_dead_code.py:758` | `TRAP[TEST] · REGRESSION(084) · REMOVE_IF(check-dead-code.sh removed)` | ✅ Gate test path unchanged — TRAP remains valid; `check-dead-code.sh` is NOT removed (only rewritten as facade), so REMOVE_IF condition NOT triggered |

### Issues Found

| # | Severity | Issue |
|---|---|---|
| Q1 (resolved 2026-07-31) | MEDIUM | **V1 drift detected:** original QA verified V1 against SHA `fbe306d4` (clean) where `core/internal/lint/` did not exist. Parallel plan 106 then created the directory + non-empty `__init__.py` (16 LOC package-contract) in the working tree. Original Step 1 ("create empty `__init__.py`") would have **overwritten plan 106's work**. **Fix applied:** V1 rewritten to reflect existing state, Step 1 converted to NO-OP, IMPACTS corrected, COORD-1/2/3 coordination notes added. |

### Edge Cases Covered

- Self-referential trap (D3): new module + test file contain "DEPRECATED" → excluded from scan ✅
- Git absent/unavailable: `FileNotFoundError`/`TimeoutExpired` → mtime fallback ✅
- Untracked files: empty blame → mtime fallback (superset of original behavior) ✅
- Line content with colons: `enumerate` + full line preserves original semantics ✅
- UTF-8/emoji: `errors="replace"`, `[:120]` character-based → consistent with bash locale ✅
- File read race: try/except OSError → skip with IMP:6 log ✅
- Executable bit preservation: `git update-index --chmod=+x` ✅
- Inventory gate: mandatory `make test-inventory-sync` step ✅

### Recommendations for Coder

1. **Step 3 (facade reduction)**: verify `git update-index --chmod=+x` after rewriting — the file is `100755` (executable) in git index per DevPlan 084 VR finding
2. **Step 4 (unit tests)**: add `# 🧪 TRAP[TEST]` on every test function per §TEST_SPEC; ensure `caplog` captures module logger's stderr stream (per D8: `StreamHandler` to stderr, `propagate=False`)
3. **Step 5 (parity verification)**: before `wc -l` on facade, capture `bash core/entrypoints/check-dead-code.sh` stdout/stderr into temp files for byte-identical comparison against new output
4. **Step 6 (inventory sync)**: `make test-inventory-sync` is MANDATORY before `make gate MODE=fast` — skipping it causes `test_gate_test_inventory` to fail (V9)
5. **D6 optimization note**: if DEPRECATED marker count grows beyond ~50, consider whole-file blame batching as a follow-up task (recorded as candidate TRAP[PERF])

$END_DEVPLAN
