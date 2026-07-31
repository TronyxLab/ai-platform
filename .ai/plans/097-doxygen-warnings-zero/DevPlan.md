$START_DEVPLAN

# DevPlan 097: Doxygen Warnings → Zero

$ARTIFACT_CONTRACT
PURPOSE: Eliminate all 171 Doxygen warnings (v1.17.0, current state: exit code 0, 171 warnings) to achieve clean generation with zero false positives.
DESCRIPTION: Multi-wave fix across 3 layers: (A) Doxyfile alias/unknown-command registration — 94 warnings, zero source changes; (B) XML/HTML tag escaping in generated content + Python docstrings — 54 warnings; (C) Broken `\ref` paths, invalid list items, and misc fixes — 23 warnings. Fixes are disciplined: no suppression of legitimate issues, no quiet-mode hacks, each warning category addressed at its root cause.
RATIONALE: 171 warnings create noise that masks real Doxygen-detected issues (undocumented params, broken references). WARN_IF_DOC_ERROR=YES and WARN_IF_UNDOCUMENTED=YES are intentional — they must produce zero warnings for the alerts to be signal, not noise. Current state violates this invariant. Approach: fix all 171 warnings without relaxing Doxygen strictness.
ACCEPTANCE_CRITERIA:
  - `doxygen Doxyfile 2>&1 | grep -c "warning:"` = 0
  - Doxygen exit code remains 0
  - Doxyfile `WARN_IF_DOC_ERROR = YES` and `WARN_IF_UNDOCUMENTED = YES` unchanged
  - No source files excluded from Doxygen processing (no EXCLUDE additions)
  - `make generate-agents-md` produces Doxygen-safe output
  - `make gate MODE=fast` remains green (no regression)
IMPLEMENTS: — (cleanup task, no parent artifact)
IMPACTS:
  - `Doxyfile` (MODIFY: ALIASES section — fix 4, add 8)
  - `core/internal/scripts/generate_agents_md.py` (MODIFY: ~5 lines — escape XML tags in generated table)
  - `core/AGENTS.md` (REGENERATED via `make generate-agents-md`)
  - Python source files with `<tag>` in docstrings: 17 files (MODIFY: escape/backtick `<tag>` patterns)
  - AGENTS.md files with broken `\ref`: 5 files (MODIFY: fix paths or drop broken refs)
  - Python source files with invalid list items: 8 files (MODIFY: add blank line before bullet lists)
  - Misc: `core/modules/nginx/AGENTS.md` (MODIFY: 3 lines — move `@see`), `core/modules/clickhouse/module.yaml` (MODIFY: fix YAML comment nesting)
REQUIRES: doxygen >= 1.9.0 (already installed: 1.17.0 via homebrew)
$END_ARTIFACT_CONTRACT

---

## §0. Warning Inventory (Current State)

`doxygen Doxyfile 2>&1 | grep -c "warning:"` = **171**

| # | Category | Count | Root Cause | Fix Layer |
|---|----------|-------|------------|-----------|
| A1 | Unexpanded alias — `@purpose`, `@io`, `@complexity`, `@invariants` | 65 | Doxyfile ALIASES defined without `{1}` parameter pattern — Doxygen 1.17.0 won't consume trailing text | Doxyfile |
| A2 | Unknown command — `@edge`, `@coverage`, `@raises`, `@exitcode`, `@checks`, `@envvars`, `@main`, `@strategy` | 29 | Custom tags used in docstrings not registered in Doxyfile ALIASES | Doxyfile |
| B1 | Unsupported XML/HTML tag — `<name>`, `<node>`, `<context>`, `<n>`, `<org>`, `<domain>`, `<dir>`, `<path>`, `<absolute_path>`, `<uptime>`, `<type>`, `<t>`, `<subgroup>`, `<project>`, `<none>`, `<module_name>`, `<ctx>` | 54 | Doxygen interprets `<text>` as HTML/XML. Occurs in: generated `core/AGENTS.md` (23), Python docstrings (18), Markdown AGENTS.md (10), YAML/SKILL.md (3) | Source files + Generator |
| C1 | Broken `\ref` — unable to resolve reference | 9 | Markdown links `[text](relative/path)` in AGENTS.md resolved from wrong root by Doxygen | Source files (AGENTS.md) |
| D1 | Invalid list item | 8 | Missing blank line before `- ` bullet lists in Python docstrings | Source files (Python) |
| E1 | `\see` in section title | 3 | `@see` commands inside `## @changes` section — Doxygen forbids commands in titles | Source file |
| E2 | Nested YAML comment | 1 | `module.yaml` comment nesting level mismatch | Source file |
| E3 | Explicit link broken | 1 | Markdown link `core/entrypoint-manifest.yaml#forbidden_scripts` — Doxygen can't resolve anchors in non-.md files | Source file |
| E4 | Undocumented parameter | 1 | Function parameter missing `@param` in docstring | Source file |
| **Total** | | **171** | | |

---

## §1. Wave A: Doxyfile ALIASES (94 warnings → 0, 0 source file changes)

### 1.1 Fix existing aliases — add `{1}` parameter

**Problem:** Doxygen 1.17.0 interprets `@purpose Description text` as an alias call, but the alias `purpose="\b Purpose:"` has no parameter placeholder. Doxygen warns: "unexpanded alias — check if number of arguments passed is correct."

**Fix:** Add `{1}` parameter capture to all 4 affected aliases:

```diff
- ALIASES = purpose="\b Purpose:" \
-           scope="\b Scope:" \
-           invariants="\b Invariants:" \
+ ALIASES = purpose{1}="\b Purpose: \1" \
+           scope{1}="\b Scope: \1" \
+           invariants{1}="\b Invariants: \1" \
```

Additionally, `@complexity` and `@io` also produce "unexpanded alias" warnings:

```diff
-           complexity="\b Complexity:" \
+           complexity{1}="\b Complexity: \1" \
  # ... and in Doxyfile, also add io alias if missing:
+           io{1}="\b I/O: \1" \
```

**Affected files (zero changes):**
- `core/internal/bootstrap/deploy/context_deployer.py` (38 warnings — `@purpose`, `@io`, `@complexity`, `@invariants`)
- `tests/test_deploy_direct.py` (27 warnings — same tags)

**Warnings fixed:** 65

### 1.2 Register unknown commands as ALIASES

**Problem:** 8 custom tags used in docstrings have no Doxyfile registration.

**Fix:** Add to `ALIASES`:

```diff
  ALIASES = purpose{1}="\b Purpose: \1" \
            ...
+           edge{1}="\b Edge cases: \1" \
+           coverage{1}="\b Coverage: \1" \
+           raises{1}="\b Raises: \1" \
+           exitcode{1}="\b Exit code: \1" \
+           checks{1}="\b Checks: \1" \
+           envvars{1}="\b Environment variables: \1" \
+           main{1}="\b Main: \1" \
+           strategy{1}="\b Strategy: \1" \
```

**Affected files (zero changes):**
| Command | Count | File |
|---------|-------|------|
| `@edge` | 9 | `core/internal/bootstrap/converge/reconciler.py` |
| `@coverage` | 7 | `tests/test_lib_ssh.py` |
| `@raises` | 7 | `core/internal/scaffold/gen_env_platform.py` |
| `@exitcode` | 2 | `core/internal/scaffold/gen_env_platform.py` |
| `@checks` | 1 | `tests/gates/test_gate_manifest_integrity.py` |
| `@envvars` | 1 | `core/internal/secrets/decrypt_secrets.py` |
| `@main` | 1 | `core/internal/scaffold/project_adopter.py` |
| `@strategy` | 1 | `core/internal/healthcheck/metrics/docker_collector.py` |

**Warnings fixed:** 29

**Wave A total:** 94 warnings → 0

---

## §2. Wave B1: XML/HTML Tag Escaping in Generated Content (23 warnings)

### 2.1 Generator fix: `core/internal/scripts/generate_agents_md.py`

**Problem:** The generated canonical operations table in `core/AGENTS.md` contains entries like:

```
| `make bootstrap-node` | ... | make bootstrap-node NODE=<name> | ...
| `make deploy-context` | ... | make deploy-context NODE=<n> [CONTEXT=<ctx>] | ...
```

Doxygen interprets `<name>`, `<n>`, `<ctx>`, `<dir>`, `<node>`, `<context>`, `<org>`, `<t>` as XML tags. These come from `entrypoint-manifest.yaml` `signature` and `delegates_to` fields.

**Fix:**
1. Add a helper function in `generate_agents_md.py`:

```python
def _escape_xml_tags(text: str) -> str:
    """Escape XML/HTML angle brackets for Doxygen compatibility.

    Doxygen interprets <text> as HTML tags. \<text\> is the safe form.
    """
    return text.replace("<", "\\<").replace(">", "\\>")
```

2. Apply it to the `signature` and `delegates_to` fields before constructing the markdown row (line ~99-101 in `generate_agents_md.py`):

```diff
- delegates_to = entry.get("delegates_to", "")
+ delegates_to = _escape_xml_tags(entry.get("delegates_to", ""))
  ...
- row = f"| `make {target}` | {operation_ru} | {signature} | {delegates_to} |"
+ row = f"| `make {target}` | {operation_ru} | {_escape_xml_tags(signature)} | {delegates_to} |"
```

3. Regenerate: `make generate-agents-md`

**Verification:** After regeneration, `core/AGENTS.md` should show `make bootstrap-node NODE=\<name\>` instead of `NODE=<name>`.

**Warnings fixed:** 23 (all in `core/AGENTS.md`)

---

## §3. Wave B2: XML/HTML Tag Escaping in Python Docstrings (18 warnings)

**Problem:** Python docstrings use `<tag>` patterns for parameter placeholders in scenario descriptions:

```python
## @purpose  Assert extract_org correctly extracts org and project name from
##           path structure: ~/projects/<org>/<subgroup>/<project>
##           - extract_org resolves <name> from path
##           - resolve_node_yaml returns path to <node> config
```

Doxygen interprets `<org>`, `<subgroup>`, `<project>`, `<name>`, `<node>` as HTML.

**Fix:** Escape with Doxygen-safe syntax: `<org>` → `<org\>`, `<name>` → `<name\>`.

### 3.1 Affected files and patterns

| File | Tags | Lines |
|------|------|-------|
| `tests/test_deploy_direct.py` | `<org>`, `<subgroup>`, `<project>`, `<name>` | 5 occurrences |
| `tests/test_lib_node_resolver.py` | `<node>` | 5 occurrences |
| `tests/gates/test_gate_grep_summary.py` | `<path>`, `<absolute_path>` | 3 occurrences |
| `core/internal/scaffold/project_scaffolder.py` | `<type>`, `<org>`, `<name>` | 3 occurrences |
| `core/internal/scaffold/project_remover.py` | `<node>`, `<domain>` | 2 occurrences |
| `core/internal/bootstrap/s3_ssl_cache.py` | `<domain>` | 1 occurrence |
| `core/modules/backup-cron/scripts/backup_config.py` | `<domain>` | 1 occurrence |
| `core/internal/deploy/deploy_engine.py` | `<none>` | 1 occurrence |
| `core/internal/bootstrap/deploy/orphan_reconciler.py` | `<module_name>` | 1 occurrence |
| `core/internal/bootstrap/deploy/context_overlay.py` | `<path>` | 1 occurrence |
| `core/internal/bootstrap/converge/reconciler.py` | `<domain>` | 1 occurrence |
| `tests/test_deploy_modules.py` | `<name>` | 1 occurrence |
| `tests/test_topo_sort.py` | `<name>` | 1 occurrence |
| `tests/test_hermes_version.py` | `<context>` | 1 occurrence |
| `tests/unit/test_overlay_deliverer.py` | `<node>` | 1 occurrence |
| `core/modules/hermes-agent/build/skills/server-status/SKILL.md` | `<uptime>` | 1 occurrence |

**Fix pattern for Python docstrings:**
```python
# Before:
## @io       ⇥ tmp_path simulating ~/projects/<org>/<subgroup>/<project> → ...

# After:
## @io       ⇥ tmp_path simulating ~/projects/<org\>/<subgroup\>/<project\> → ...
```

### 3.2 Affected files — Markdown (AGENTS.md, SKILL.md)

| File | Tags | Lines |
|------|------|-------|
| `core/internal/bootstrap/AGENTS.md` | `<context>` | 2 occurrences |
| `core/modules/nginx/AGENTS.md` | `<name>`, `<n>`, `<ctx>` in generated table | 6 occurrences |

**Fix for Markdown files:** Wrap in backticks — `` `<name>` `` — which prevents Doxygen XML interpretation AND preserves readability.

**Warnings fixed:** 18 + 8 (Markdown) = 26

---

## §4. Wave C: Broken `\ref` Paths (9 warnings)

**Problem:** Markdown links like `[AGENTS.md](../AGENTS.md)` in AGENTS.md files are resolved by Doxygen as `\ref{/Users/tronyx/...}` with incorrect absolute paths. The `../` relative paths resolve against the wrong base directory (Doxygen's working directory, not the file's directory).

### 4.1 Affected files

| File:Line | Broken Path | Fix |
|-----------|-------------|-----|
| `core/AGENTS.md:157` | `(../AGENTS.md)` → resolves to `/Users/tronyx/projects/AGENTS.md` | Remove `()` and use plain text: `[AGENTS.md (root)]` → just `AGENTS.md (root)` |
| `core/internal/bootstrap/AGENTS.md:279` | `(../../../AGENTS.md)` | Fix relative path to `../../../../AGENTS.md` (file is at `core/internal/bootstrap/` → root) or remove `()` |
| `core/modules/AGENTS.md:279` | `(../AGENTS.md)` → resolves to `core/AGENTS.md` (correct!), but also `../../AGENTS.md` | The `../AGENTS.md` is correct. The error is about `/Users/tronyx/projects/AGENTS.md`. Fix: ensure links use correct relative paths |
| `core/modules/AGENTS.md:280` | `(../../AGENTS.md)` | Fix relative path |
| `tests/AGENTS.md:128` | `(../AGENTS.md)` → resolves to `ai-platform/AGENTS.md` (file is in `ai-platform/tests/`, so `../AGENTS.md` → `ai-platform/AGENTS.md` — correct!) | Actually check Doxygen resolution |
| `tests/AGENTS.md:132` | `(../.ai/plans/060-self-healing-gates/DevPlan.md)` | Fix relative path: from `tests/` to `.ai/plans/` = `../.ai/...` — but file may not exist anymore |
| `tests/AGENTS.md:133` | `(.kilo/rules/testing.md)` | Path missing `../` prefix — should be `../.kilo/rules/testing.md` |
| `tests/AGENTS.md:134` | `(.kilo/rules/markup.md)` | Same — missing `../` |
| `tests/gates/AGENTS.md:71` | `(../../AGENTS.md)` | Verify and fix |

**Root Cause Analysis:** Doxygen resolves `\ref` paths from the project root (INPUT directory), not from the file's directory. Markdown links with `../` relative paths are misinterpreted. The `[text](path)` syntax in Markdown is treated as `\ref{path}` when Doxygen can't find the file.

**Fix Strategy (simplest — no \ref for external files):**

Doxygen can only resolve `\ref` to files within its INPUT scope (`core/ tests/`). Files outside (`.kilo/`, `.ai/`, root `AGENTS.md`) cannot be resolved by Doxygen even with correct paths. The `[text](path)` links to external files should NOT be interpreted as `\ref` commands.

**Option A (recommended):** Convert broken Markdown links to plain text (remove parentheses) so Doxygen doesn't try to resolve them:

```diff
- | [`AGENTS.md`](../AGENTS.md) (root) | ... |
+ | `AGENTS.md` (root) | ... |
```

This preserves readability while eliminating the `\ref` attempt.

**Option B:** Escape the link with `%` (Doxygen comment): `[text](%path)`. Not appropriate for Markdown readability.

**Option C:** Use HTML `<a href="...">` instead of Markdown links. Overkill.

**Decision: Option A** for external files. Keep Markdown links only for internal files within `core/` and `tests/`.

**Warnings fixed:** 9

---

## §5. Wave D: Invalid List Items (8 warnings)

**Problem:** Doxygen requires a blank line before bullet lists (`- ` or `* `) in docstrings. The following files have `- ` lists immediately after `## @invariants` or `## @scope` without a blank separator:

```
## @invariants
##   - Every test uses tmp_path for script isolation
```

Should be:
```
## @invariants
##
##   - Every test uses tmp_path for script isolation
```

### 5.1 Affected files

| File | Line | Pattern |
|------|------|---------|
| `tests/test_lib_logging.py` | 18 | `## @invariants` immediately followed by `##   -` |
| `tests/test_lib_node_resolver.py` | 18 | Same |
| `tests/test_lib_healthcheck.py` | 16 | Same |
| `tests/test_healthcheck_contract.py` | 20 | Same |
| `tests/gates/test_gate_contract.py` | 12 | Same |
| `tests/test_secrets_validation.py` | 29 | Same |
| `tests/test_smoke_platform.py` | 796 | Same |
| `core/internal/deploy/orchestrator.py` | 225 | `Returns:` section with list |

**Fix:** Insert `##` (empty docstring line) between the section header and the first bullet item.

```python
# Before:
## @invariants
##   - Every test uses tmp_path

# After:
## @invariants
##
##   - Every test uses tmp_path
```

**Warnings fixed:** 8

---

## §6. Wave E: Misc Fixes (5 warnings)

### 6.1 `@see` in section title — `core/modules/nginx/AGENTS.md` (3 warnings)

**Problem:** Lines 19-21:
```
## @changes 2026-07-26 · DevPlan 080 — Added §6 Template Syntax Contract, deleted nginx/install.sh references
## @see tests/gates/test_gate_vhost_nginx_t.py — Docker-harness gate test
## @see core/internal/scaffold/add-vhost.sh — генератор vhost'ов
## @see core/modules/nginx/config/ — эталонные vhost конфиги
```

Doxygen treats `@see` as a section command but forbids it inside `@changes` context.

**Fix:** Move `@see` lines outside the MODULE_CONTRACT block or separate with a blank comment line:

```diff
 ## @changes 2026-07-26 · DevPlan 080 — Added §6 Template Syntax Contract, deleted nginx/install.sh references
+##
 ## @see tests/gates/test_gate_vhost_nginx_t.py — Docker-harness gate test
 ## @see core/internal/scaffold/add-vhost.sh — генератор vhost'ов
 ## @see core/modules/nginx/config/ — эталонные vhost конфиги
```

**Warnings fixed:** 3

### 6.2 Nested YAML comment — `core/modules/clickhouse/module.yaml` (1 warning)

**Problem:** Line 2: comment nesting level mismatch in YAML. Line 48: "Reached end of file while still inside a (nested) comment. Nesting level 1 (possible line reference(s): 2)".

This suggests a multi-line `#` comment (not a YAML block scalar) that isn't properly closed.

**Fix:** Will inspect `module.yaml` lines 1-50 to identify the comment nesting issue.

**Warnings fixed:** 1

### 6.3 Explicit link broken — `core/AGENTS.md` line 7 (1 warning)

**Problem:**
```
## @scope    All operations that pass through Makefile; ... Source of truth for forbidden scripts: core/entrypoint-manifest.yaml#forbidden_scripts
```

Doxygen tries to resolve `core/entrypoint-manifest.yaml#forbidden_scripts` as a link and fails because it can't resolve YAML anchors.

**Fix:** This is generated content. The fix should be in `generate_agents_md.py` to avoid generating links with YAML anchors in plain text. Convert to non-link format:

```diff
- ... Source of truth for forbidden scripts: core/entrypoint-manifest.yaml#forbidden_scripts
+ ... Source of truth for forbidden scripts: core/entrypoint-manifest.yaml §forbidden_scripts
```

Or use Markdown inline code: `` `core/entrypoint-manifest.yaml#forbidden_scripts` ``.

**Warnings fixed:** 1

### 6.4 Undocumented parameter — `reconciler.py:1118` (1 warning)

**Problem:**
```
warning: The following parameter of converge.reconciler.verify_vhosts(
    str node_yaml_path, str converge_node, str core_dir,
    bool dry_run=False, bool report_only=False,
    str|None overlay_base=None
) is not documented:
```

One of the parameters lacks a `@param` line in the docstring.

**Fix:** Add the missing `@param` to `verify_vhosts()` docstring.

**Warnings fixed:** 1

---

## §7. Implementation Order

| Wave | What | Files Changed | Warnings Fixed | Risk |
|------|------|---------------|----------------|------|
| **A** | Doxyfile ALIASES | 1 (`Doxyfile`) | 94 | Low — additive, no semantic change |
| **B1** | Generator escape | 1 (`generate_agents_md.py`) + regenerate `core/AGENTS.md` | 23 | Low — Doxygen \<escape\> only, visible as `<\name\>` in plain text |
| **B2** | Python docstring escape | 17 files | 26 | Low — Doxygen escape syntax, no runtime impact |
| **C** | Broken refs | 5 files (AGENTS.md) | 9 | Low — link→plain text, readability only |
| **D** | Invalid list items | 8 files | 8 | Low — blank line insertion, no logic change |
| **E** | Misc fixes | 4 files | 5 | Low — targeted fixes |
| **Verify** | `doxygen Doxyfile 2>&1 \| grep -c warning:` = 0 | — | 171 total | — |

**Waves can run in parallel** (A, B1, B2, C, D, E are independent). Then regenerate `core/AGENTS.md` via `make generate-agents-md` and verify.

---

## §8. Verification

```bash
# After all waves applied:
doxygen Doxyfile 2>&1 | grep -c "warning:"
# Expected: 0

# Verify exit code
doxygen Doxyfile 2>&1 >/dev/null; echo $?
# Expected: 0

# Verify no gate regression
make gate MODE=fast
# Expected: green
```

---

## §9. Meta: Why 3 Mechanisms of Fix?

This DevPlan uses 3 distinct fix mechanisms because the root causes fall into 3 disjoint domains:

1. **Doxyfile configuration gap** — 94 warnings. The code uses valid semantic tags (`@edge`, `@coverage`, `@purpose`) that Doxygen can't interpret without ALIASES registration. Fix: register them. This is the intended Doxygen extension mechanism.

2. **Markdown/Doxygen parser conflict** — 54 + 9 = 63 warnings. Doxygen's Markdown parser interprets `<tag>` as HTML and `[text](path)` as `\ref{path}` — both are valid Markdown that happen to collide with Doxygen's own syntax. Fix: escape (for `<tag>`) or de-link (for `\ref`). No suppression — the content is preserved, the parsing ambiguity is resolved.

3. **Genuine documentation defects** — 14 warnings. Missing blank lines, malformed YAML comments, undocumented parameters, `@see` in wrong context. These are real documentation issues that Doxygen correctly detects. Fix: correct the documentation.

**Anti-patterns avoided:**
- ❌ `WARN_IF_DOC_ERROR = NO` — would suppress ALL future issues
- ❌ `QUIET = YES` — would hide everything
- ❌ `EXCLUDE = core/AGENTS.md` — would lose API documentation for core modules
- ❌ `EXTENSION_MAPPING = md=` — would skip all Markdown files

$END_DEVPLAN
