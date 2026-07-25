$START_DEVPLAN

$ARTIFACT_CONTRACT
PURPOSE:               Eliminate 7 systemic drift points in secrets/tokens domain: unify age-key detection (5→1), htpasswd generation (2→1), sync _FALLBACK_SECRETS with definitions, fix docker token leak, resolve 5 naming conflicts, unify POSTGRES_PASSWORD (6→1) and NEXTAUTH_SECRET (4→1) defaults.
DESCRIPTION:           Implements Chapter 2 of Brief 077 (Systemic Drift Audit). Creates shared Python modules `age_key.py` and `crypto.py` in `core/internal/shared/`. All shell duplicates replaced with thin wrappers calling Python. Security-critical fix: docker_registry_auth.py token no longer interpolated into bash -c (visible in /proc/PID/cmdline). Five naming conflicts resolved. Default values converged to single canonical source in secret-definitions.yaml.
RATIONALE:             Every drift point is a bug-incubation vector: changing AGE key detection logic requires editing 5 files (miss one → silent failure). Different htpasswd idempotency guarantees between shell and Python cause non-deterministic behavior. Tokens in /proc/cmdline are a security vulnerability. Divergent POSTGRES_PASSWORD defaults cause silent DB init mismatch between developers.
ACCEPTANCE_CRITERIA:
  - `core/internal/shared/age_key.py` exists with `detect_age_key(log_tag)` — single canonical implementation
  - All 5 former duplication sites redirect to Python (bootstrap.sh, node-update.sh, secrets.sh, decrypt-secrets.sh, node-lifecycle.sh)
  - `core/internal/shared/crypto.py` exists with `hash_apr1()` and `generate_htpasswd_entry()`
  - secrets_manager.py `_ensure_htpasswd()` uses shared crypto module (fixed-salt idempotency)
  - secrets.sh `_ensure_htpasswd_generated()` delegates to Python
  - `tests/unit/test_age_key.py` — 6 test cases (env, SOPS_AGE_KEY, file, empty file, missing all, log_tag propagation)
  - `tests/unit/test_crypto.py` — 4 test cases (hash_apr1 random, hash_apr1 fixed-salt, generate_htpasswd_entry, idempotency)
  - `tests/gates/test_gate_fallback_secrets_sync.py` — verifies _FALLBACK_SECRETS ≡ secret-definitions.yaml tier=generated
  - `docker_registry_auth.py:159` replaced with Popen stdin pipe (no token in cmdline)
  - `test_secrets_validation.py:62` — OPENAI_API_KEY removed from REQUIRED_SECRET_KEYS
  - `test_secrets_validation.py:375-440` — test_openai_key_matches_litellm_master_key replaced with LITELLM_MASTER_KEY presence check
  - GHCR_PUSH_TOKEN added to secret-definitions.yaml (tier: optional, source: ci-secret)
  - POSTGRES_PASSWORD unified: `.env`: `test-pg-pwd`, `hermes-agent/.env:45`: `test-pg-pwd`
  - NEXTAUTH_SECRET unified: `.env`: `ci-test-nextauth-secret-32-chars-min!!`, `hermes-agent/.env:48`: `ci-test-nextauth-secret-32-chars-min!!`
  - Gate test validates POSTGRES_PASSWORD and NEXTAUTH_SECRET consistency across .env.example + secret-definitions.yaml + platform-env.yaml
  - `make gate MODE=fast` — green
IMPLEMENTS:            Brief 077 Chapter 2 — Secrets & Tokens domain; 7 drift points (S1-S7)
IMPACTS:
  - core/internal/shared/age_key.py (NEW)
  - core/internal/shared/crypto.py (NEW)
  - core/entrypoints/bootstrap.sh (replace detect_age_key body)
  - core/entrypoints/node-update.sh (replace detect_age_key body)
  - core/lib/secrets.sh (replace SOPS_AGE_KEY fallback lines 134-138; replace _ensure_htpasswd_generated body)
  - core/internal/secrets/decrypt-secrets.sh (replace validate_env fallback lines 76-84)
  - core/internal/bootstrap/node-lifecycle.sh (replace line 44 one-liner)
  - core/internal/bootstrap/lifecycle/secrets_manager.py (replace _ensure_htpasswd body; _FALLBACK_SECRETS verified)
  - core/internal/bootstrap/docker_registry_auth.py (fix _docker_login line 159)
  - core/secret-definitions.yaml (add GHCR_PUSH_TOKEN)
  - tests/test_secrets_validation.py (remove OPENAI_API_KEY from REQUIRED_SECRET_KEYS; replace test)
  - tests/unit/test_age_key.py (NEW)
  - tests/unit/test_crypto.py (NEW)
  - tests/gates/test_gate_fallback_secrets_sync.py (NEW)
  - tests/gates/test_gate_env_defaults_consistency.py (NEW)
  - .env.example (no changes needed — already canonical values for POSTGRES_PASSWORD, NEXTAUTH_SECRET)
  - .env (unify POSTGRES_PASSWORD + NEXTAUTH_SECRET defaults)
  - core/modules/hermes-agent/.env (unify POSTGRES_PASSWORD + NEXTAUTH_SECRET + remove OPENAI_API_KEY)
  - core/modules/hermes-agent/.env.example (remove OPENAI_API_KEY, unify defaults)
  - platform-env.yaml (already canonical — verified, no changes)
REQUIRES:
  - 070 (core/internal/shared/__init__.py must exist — shared package created by DevPlan 070)
  - 072 (secrets_manager.py append-fix + LITELLM_METRICS_TOKEN removal from .env.example — non-conflicting but should be merged before to avoid line-number shifts)
$END_ARTIFACT_CONTRACT

---

# 01-DevPlan: Secrets & Tokens Complete Unification

**Severity:** HIGH — security (DRIFT-S4), correctness (DRIFT-S1/S2/S3), consistency (DRIFT-S5/S6/S7)
**Created:** 2026-07-25
**Author:** Kilo (architect agent)
**Source:** Brief 077 Chapter 2 (§2.2–§2.8)
**Dependencies:** 070 (shared/__init__.py), 072 (non-conflicting, merge order preference)

---

## Debt Intake

| DEBT | Source | Classification | Disposition |
|------|--------|---------------|-------------|
| LITELLM_METRICS_TOKEN in `.env` (gitignored, real value) | 072-T4 | IN_SCOPE | Add to T10 verification checklist — warn if present after 072 merge |
| `hermes-agent/.env.example:45` OPENAI_API_KEY placeholder | DRIFT-S5 | IN_SCOPE | Remove from .env.example (T6.3); keep in .env for backward compat (Hermes code still reads it) |
| `PLATFORM_MASTER_PASSWORD` appears in master `makefiles/deploy.mk:117-133` with GHCR_PUSH_TOKEN reference | DRIFT-S5 | DEFER | Scope: CI-push token management. Rev: when GHCR push pipeline changes |
| `nginx/install.sh` DEPRECATED duplicate (1107 LOC) with parallel `_issue_acme_cert()` | Brief 077 RC-4 | DEFER | Out of scope for secrets domain. Handled by Cert DevPlan |

---

## Requirements Analysis

### Key Success Criteria

1. **SC1 — Single Source of Truth:** Every piece of business logic (age-key detection, htpasswd hash, secret default) exists in exactly ONE file. Other files delegate via import or thin shell wrapper.

2. **SC2 — Security Regression Prevention:** After fix, `docker login` token is NOT visible via `cat /proc/PID/cmdline` or `ps auxww`. Token flows through stdin pipe only.

3. **SC3 — Sync Enforcement:** A CI gate test fails when _FALLBACK_SECRETS diverges from secret-definitions.yaml, preventing silent out-of-sync.

4. **SC4 — Default Consistency:** POSTGRES_PASSWORD and NEXTAUTH_SECRET have identical values across all 4 layers (secret-definitions, .env.example, platform-env.yaml, hermes-agent/.env).

5. **SC5 — Idempotent htpasswd:** Both shell and Python callers produce identical htpasswd hashes for the same credentials (fixed-salt via shared crypto.py).

---

## Architecture Overview

### Draft Code Graph

```
core/internal/shared/
├── __init__.py          (070 — created by DevPlan 070)
├── node_yaml.py         (070 — context extraction)
├── project_registry.py  (070 — project registration)
├── age_key.py           (NEW — 078 T1)
│   └── detect_age_key(log_tag: str = "") -> str | None
└── crypto.py            (NEW — 078 T3)
    ├── hash_apr1(password: str, salt: str | None) -> str
    └── generate_htpasswd_entry(email: str, password: str) -> str

Consumers of age_key.py:
├── core/entrypoints/bootstrap.sh      → thin shell wrapper calls `python3 age_key.py --detect`
├── core/entrypoints/node-update.sh    → thin shell wrapper calls `python3 age_key.py --detect`
├── core/lib/secrets.sh                → lines 134-138 replaced with call to `detect_age_key`
├── core/internal/secrets/decrypt-secrets.sh → lines 76-84 replaced
└── core/internal/bootstrap/node-lifecycle.sh → line 44 replaced

Consumers of crypto.py:
├── core/lib/secrets.sh:_ensure_htpasswd_generated() → delegates hash gen to Python
└── core/internal/bootstrap/lifecycle/secrets_manager.py:_ensure_htpasswd() → imports from shared
```

### Data Flow: AGE key detection (unified)

```
Caller shell (bootstrap.sh / node-update.sh)
  │
  ▼
detect_age_key() shell wrapper  ←── thin facade (~6 lines)
  │
  ▼
python3 core/internal/shared/age_key.py --detect --log-tag <tag>
  │
  ├─ [1] os.environ.get("AGE_SECRET_KEY")                  → found? return
  ├─ [2] os.environ.get("SOPS_AGE_KEY")                    → found? return
  ├─ [3] open(os.environ["AGE_SECRET_KEY_FILE"]).read()     → found? return
  └─ [4] return None (not found)
  │
  ▼
stdout: key value (or empty on failure)
stderr: [IMP:8] log messages with log_tag
```

### Data Flow: htpasswd generation (unified)

```
secrets_manager.py           OR           lib/secrets.sh
  │                                         │
  ▼                                         ▼
_ensure_htpasswd()              _ensure_htpasswd_generated()
  │                                         │
  ▼                                         ▼
from core.internal.shared.crypto import    python3 crypto.py hash-apr1
  hash_apr1, generate_htpasswd_entry         --password "$PWD" [--salt "$SALT"]
  │
  ▼
openssl passwd -apr1 [ -salt <salt> ] <password>
  │
  ▼
Fixed salt (extracted from existing hash) → idempotent
New generation (no salt) → random salt → non-deterministic first call, idempotent thereafter
```

---

## Design Decisions

### DD1: Python shared module for AGE key — @rationale

**Q:** Why Python instead of shell shared library?
**A:** Shell shared libraries require `source` which (a) pollutes the caller's namespace, (b) can't be unit-tested beyond bash bats, (c) can't enforce typed contracts. Python function operates as a clean subprocess (key → stdout, logs → stderr, exit code for found/not-found). All 5 consumers already have Python available (state_machine.py dependency).

**Q:** Why subprocess instead of import from state_machine.py?
**A:** Shell entrypoints (bootstrap.sh, node-update.sh, decrypt-secrets.sh) can't import Python modules. They must call via subprocess. For Python consumers (state_machine.py), direct import is possible and should be done for performance. The module supports both usages.

### DD2: Fixed-salt idempotency via existing hash extraction — @rationale

**Q:** Why extract salt from existing hash instead of hardcoding a fixed salt?
**A:** Hardcoded salt exposes all hashes to precomputation if the salt leaks. Extracting salt from the existing hash provides per-credential salt while maintaining idempotency. First generation uses random salt (openssl default), subsequent calls extract salt from existing file → produce identical hash. Same strategy as shell `_ensure_htpasswd_generated()` (lines 220-224).

### DD3: GHCR_PUSH_TOKEN added to definitions as optional ci-secret — @rationale

**Q:** Why add to secret-definitions.yaml?
**A:** Brief 077 DRIFT-S5 flags PULL/PUSH asymmetry as a naming conflict. GHCR_PULL_TOKEN is formalized in definitions but GHCR_PUSH_TOKEN exists only in .env.example as an ad-hoc variable. Adding it to definitions makes it discoverable, documents it (tier: optional, source: ci-secret, no ci_default), and ensures future generate_platform_env.py runs include it.

### DD4: OPENAI_API_KEY removal from REQUIRED_SECRET_KEYS — @rationale

**Q:** OPENAI_API_KEY is tier=removed in definitions but still in REQUIRED_SECRET_KEYS list and test — why remove?
**A:** OPENAI_API_KEY was removed from definitions per DevPlan 049 (2026-07-24). The test and key list are stale — they reference a removed secret. Hermes-agent still uses OPENAI_API_KEY internally (its docker-compose maps it), but this is a Hermes concern, not a platform-level secret enforcement. The test should be replaced with a LITELLM_MASTER_KEY presence check. OPENAI_API_KEY is kept in hermes-agent/.env for backward compatibility with Hermes code (out of scope for this DevPlan).

---

## $TASKS

### T1: Create `core/internal/shared/age_key.py` (S1 core)

**Owner:** Coder
**Output:** `core/internal/shared/age_key.py` (Python module + CLI)
**Dependencies:** 070 (shared/__init__.py)
**Complexity:** 3

**Acceptance Criteria:**
- `detect_age_key(log_tag: str = "") -> Optional[str]` function
- Chain: env AGE_SECRET_KEY → SOPS_AGE_KEY → AGE_SECRET_KEY_FILE path → read file
- LOG to stderr with `[IMP:8]` using `log_tag` in module field
- CLI mode: `python3 age_key.py --detect [--log-tag TAG]` → prints key to stdout, exit 0; no key → exit 1
- Key preview in logs: `cut -c1-8` for first 8 chars (masked)

**Before/After — no before (new file):**

Module contract:
```python
#!/usr/bin/env python3
# GREP_SUMMARY: age-key, detect-age-key, AGE_SECRET_KEY, SOPS_AGE_KEY, sops, decrypt
# STRUCTURE: ▶ detect_age_key → ◇ os.environ AGE_SECRET_KEY? → ◇ SOPS_AGE_KEY? → ◇ AGE_SECRET_KEY_FILE? → ⎋ key|None
# region MODULE_CONTRACT
## @purpose  Canonical single-source-of-truth for AGE_SECRET_KEY detection across the platform.
##           Replaces 5 duplicate implementations in shell entrypoints.
## @scope    Imported by state_machine.py (Python consumers) or called via CLI from shell wrappers.
## @invariants
##   1. Search chain: os.environ["AGE_SECRET_KEY"] → os.environ["SOPS_AGE_KEY"] → read(os.environ["AGE_SECRET_KEY_FILE"])
##   2. Returns None (not raise) on not-found — callers decide whether to fail
##   3. CLI mode: prints key to stdout, logs to stderr, exit 0=found, 1=not-found
##   4. Key preview: first 8 chars in logs (masked)
## @rationale 5 duplicates → 1 canonical implementation. Bug fix in detection = 1 file edit, not 5.
# endregion MODULE_CONTRACT
```

**Test:** `tests/unit/test_age_key.py`

---

### T2: Replace shell AGE key detection with Python wrappers (S1 integration)

**Owner:** Coder
**Output:** Modified bootstrap.sh, node-update.sh, secrets.sh, decrypt-secrets.sh, node-lifecycle.sh
**Dependencies:** T1
**Complexity:** 4

**Acceptance Criteria:**
- All 5 files delegate to Python `age_key.py --detect`
- Existing log output preserved (same [IMP:8] messages, same prefixes)
- Exit code behavior preserved (found = 0, not found = 1)

**File 1: `core/entrypoints/bootstrap.sh` — lines 53-76**

Before (24 lines):
```bash
detect_age_key() {
    if [[ -n "${AGE_SECRET_KEY:-}" ]]; then
        local m; m="$(echo "${AGE_SECRET_KEY}" | cut -c1-8)"
        echo "[IMP:8][bootstrap][age-key] AGE_SECRET_KEY found in environment (${m}...)" >&2
        echo "${AGE_SECRET_KEY}"; return 0
    fi
    if [[ -n "${SOPS_AGE_KEY:-}" ]]; then
        local m; m="$(echo "${SOPS_AGE_KEY}" | cut -c1-8)"
        echo "[IMP:8][bootstrap][age-key] AGE_SECRET_KEY set from SOPS_AGE_KEY (${m}...)" >&2
        echo "${SOPS_AGE_KEY}"; return 0
    fi
    if [[ -n "${AGE_SECRET_KEY_FILE:-}" ]] && [[ -f "${AGE_SECRET_KEY_FILE}" ]]; then
        local key; key="$(head -1 "${AGE_SECRET_KEY_FILE}")"
        if [[ -n "${key}" ]]; then
            local m; m="$(echo "${key}" | cut -c1-8)"
            echo "[IMP:8][bootstrap][age-key] AGE_SECRET_KEY read from file ${AGE_SECRET_KEY_FILE} (${m}...)" >&2
            echo "${key}"; return 0
        fi
        echo "[IMP:8][bootstrap][age-key] WARN: AGE_SECRET_KEY_FILE=${AGE_SECRET_KEY_FILE} is empty" >&2
    fi
    echo "[IMP:8][bootstrap][age-key] WARN: AGE_SECRET_KEY not found — Docker modules requiring secrets will fail to deploy" >&2
    return 1
}
```

After (~10 lines):
```bash
## @purpose  Thin wrapper → core/internal/shared/age_key.py (canonical, DevPlan 078)
detect_age_key() {
    local shared_dir="${CORE_DIR}/internal/shared"
    local key
    # Capture stdout, stderr passes through (PYTHONUNBUFFERED for instant log output)
    key="$(PYTHONUNBUFFERED=1 python3 "${shared_dir}/age_key.py" --detect --log-tag bootstrap 2>&2)" || {
        echo "[IMP:8][bootstrap][age-key] WARN: AGE_SECRET_KEY not found — Docker modules requiring secrets will fail to deploy" >&2
        return 1
    }
    echo "${key}"; return 0
}
```

**File 2: `core/entrypoints/node-update.sh` — lines 44-66**

Before (23 lines):
```bash
detect_age_key() {
    if [[ -n "${AGE_SECRET_KEY:-}" ]]; then
        local m; m="$(echo "${AGE_SECRET_KEY}" | cut -c1-8)"; echo "[IMP:8][node-update][age-key] AGE_SECRET_KEY found in environment (${m}...)" >&2
        echo "${AGE_SECRET_KEY}"; return 0
    fi
    if [[ -n "${SOPS_AGE_KEY:-}" ]]; then
        local m; m="$(echo "${SOPS_AGE_KEY}" | cut -c1-8)"; echo "[IMP:8][node-update][age-key] AGE_SECRET_KEY set from SOPS_AGE_KEY (${m}...)" >&2
        echo "${SOPS_AGE_KEY}"; return 0
    fi
    if [[ -n "${AGE_SECRET_KEY_FILE:-}" ]] && [[ -f "${AGE_SECRET_KEY_FILE}" ]]; then
        local key; key="$(head -1 "${AGE_SECRET_KEY_FILE}")"
        if [[ -n "${key}" ]]; then
            local m; m="$(echo "${key}" | cut -c1-8)"; echo "[IMP:8][node-update][age-key] AGE_SECRET_KEY read from file ${AGE_SECRET_KEY_FILE} (${m}...)" >&2
            echo "${key}"; return 0
        fi
        echo "[IMP:8][node-update][age-key] WARN: AGE_SECRET_KEY_FILE=${AGE_SECRET_KEY_FILE} is empty" >&2
    fi
    echo "[IMP:8][node-update][age-key] WARN: AGE_SECRET_KEY not found — Docker modules requiring secrets will fail to deploy" >&2
    return 1
}
```

After (~10 lines):
```bash
## @purpose  Thin wrapper → core/internal/shared/age_key.py (canonical, DevPlan 078)
detect_age_key() {
    local shared_dir; shared_dir="$(cd "${SCRIPT_DIR}/.." && pwd)/internal/shared"
    local key
    key="$(PYTHONUNBUFFERED=1 python3 "${shared_dir}/age_key.py" --detect --log-tag node-update 2>&2)" || {
        echo "[IMP:8][node-update][age-key] WARN: AGE_SECRET_KEY not found" >&2
        return 1
    }
    echo "${key}"; return 0
}
```

**File 3: `core/lib/secrets.sh` — lines 134-138**

Before (5 lines):
```bash
    # SOPS_AGE_KEY fallback: try alternative env var before aborting
    if [[ -z "${AGE_SECRET_KEY:-}" ]] && [[ -n "${SOPS_AGE_KEY:-}" ]]; then
        export AGE_SECRET_KEY="$SOPS_AGE_KEY"
        log_step "decrypt-secrets" "INFO" "AGE_SECRET_KEY set from SOPS_AGE_KEY fallback"
    fi
```

After (6 lines — inline usage, no separate function):
```bash
    # SOPS_AGE_KEY fallback via canonical age_key.py (DevPlan 078)
    if [[ -z "${AGE_SECRET_KEY:-}" ]]; then
        AGE_SECRET_KEY="$(PYTHONUNBUFFERED=1 python3 "${CORE_DIR}/internal/shared/age_key.py" --detect --log-tag secrets 2>&2)" || true
        [[ -n "${AGE_SECRET_KEY:-}" ]] && export AGE_SECRET_KEY && log_step "decrypt-secrets" "INFO" "AGE_SECRET_KEY set from age_key.py fallback"
    fi
```

**File 4: `core/internal/secrets/decrypt-secrets.sh` — lines 76-84**

Before (9 lines):
```bash
    # Fallback chain: AGE_SECRET_KEY → SOPS_AGE_KEY
    if [[ -z "${AGE_SECRET_KEY:-}" ]]; then
        if [[ -n "${SOPS_AGE_KEY:-}" ]]; then
            export AGE_SECRET_KEY="$SOPS_AGE_KEY"
            log_step "validate" "OK" "AGE_SECRET_KEY resolved via SOPS_AGE_KEY fallback"
        else
            log_step "validate" "FAIL" "Neither AGE_SECRET_KEY nor SOPS_AGE_KEY is set"
            exit 1
        fi
    else
        log_step "validate" "OK" "AGE_SECRET_KEY is set directly"
    fi
```

After (~9 lines):
```bash
    # Fallback chain via canonical age_key.py (DevPlan 078)
    if [[ -n "${AGE_SECRET_KEY:-}" ]]; then
        log_step "validate" "OK" "AGE_SECRET_KEY is set directly"
    else
        AGE_SECRET_KEY="$(PYTHONUNBUFFERED=1 python3 "${PLATFORM_ROOT}/core/internal/shared/age_key.py" --detect --log-tag decrypt 2>&2)" || true
        if [[ -n "${AGE_SECRET_KEY:-}" ]]; then
            export AGE_SECRET_KEY
            log_step "validate" "OK" "AGE_SECRET_KEY resolved via age_key.py fallback"
        else
            log_step "validate" "FAIL" "Neither AGE_SECRET_KEY nor SOPS_AGE_KEY is set"
            exit 1
        fi
    fi
```

**File 5: `core/internal/bootstrap/node-lifecycle.sh` — line 44**

Before (1 line):
```bash
[[ -z "${AGE_SECRET_KEY:-}" && -n "${SOPS_AGE_KEY:-}" ]] && export AGE_SECRET_KEY="$SOPS_AGE_KEY" && echo "[IMP:8][node-lifecycle][args] AGE_SECRET_KEY from SOPS_AGE_KEY" >&2
```

After (3 lines):
```bash
# Canonical age_key.py fallback (DevPlan 078 — replaces inline SOPS_AGE_KEY check)
if [[ -z "${AGE_SECRET_KEY:-}" ]]; then
    AGE_SECRET_KEY="$(PYTHONUNBUFFERED=1 python3 "${SCRIPT_DIR}/../../internal/shared/age_key.py" --detect --log-tag node-lifecycle 2>&2)" || true
    [[ -n "${AGE_SECRET_KEY:-}" ]] && export AGE_SECRET_KEY && echo "[IMP:8][node-lifecycle][args] AGE_SECRET_KEY from age_key.py" >&2
fi
```

---

### T3: Create `core/internal/shared/crypto.py` (S2 core)

**Owner:** Coder
**Output:** `core/internal/shared/crypto.py` (Python module with CLI)
**Dependencies:** 070 (shared/__init__.py)
**Complexity:** 3

**Acceptance Criteria:**
- `hash_apr1(password: str, salt: str | None = None) -> str` — wraps `openssl passwd -apr1`
- `generate_htpasswd_entry(email: str, password: str, salt: str | None = None) -> str` — returns `"email:$apr1$hash"`
- CLI mode: `python3 crypto.py hash-apr1 --password PWD [--salt SALT]` and `python3 crypto.py htpasswd-entry --email E --password P [--salt S]`
- Deterministic: same password + same salt = same hash (idempotent)
- Falls back gracefully if openssl not installed (returns empty string, logs WARN)

**Test:** `tests/unit/test_crypto.py`

---

### T4: Update secrets_manager.py _ensure_htpasswd() to use shared crypto (S2 integration)

**Owner:** Coder
**Output:** `core/internal/bootstrap/lifecycle/secrets_manager.py` — modified `_ensure_htpasswd()`
**Dependencies:** T3
**Complexity:** 3

**Acceptance Criteria:**
- Import `hash_apr1`, `generate_htpasswd_entry` from `core.internal.shared.crypto`
- Idempotency: extract existing salt from file → `hash_apr1(password, salt)` → compare
- New generation: `generate_htpasswd_entry(email, password)` (random salt, idempotent on next call)
- No `subprocess.run(["openssl", "passwd", "-apr1", password])` — replaced by shared import
- Previous behavior preserved: same log messages, same return values, same error handling

**Before (lines 396-409 in secrets_manager.py):**
```python
    try:
        # Generate password hash
        hash_result = subprocess.run(
            ["openssl", "passwd", "-apr1", password],
            capture_output=True,
            text=True,
            timeout=15,
        )
        if hash_result.returncode != 0:
            logger.warning(
                "[IMP:7][secrets_manager] openssl passwd failed: %s",
                hash_result.stderr.strip()[:200],
            )
            return False

        password_hash = hash_result.stdout.strip()
        expected_entry = f"{email}:{password_hash}"

        # Check idempotency: compare with existing file content
        htpasswd_path = Path(htpasswd_file)
        if htpasswd_path.exists():
            existing = htpasswd_path.read_text().strip()
            if existing == expected_entry:
                logger.info(...)
                os.environ["HTPASSWD_FILE"] = htpasswd_file
                return True
```

**After:**
```python
    try:
        from core.internal.shared.crypto import hash_apr1, generate_htpasswd_entry

        htpasswd_path = Path(htpasswd_file)

        # ── Idempotency: extract existing salt and verify ──
        if htpasswd_path.exists():
            existing = htpasswd_path.read_text().strip()
            if existing:
                # Extract salt from existing hash for deterministic comparison
                # APR1 format: $apr1$<salt>$<hash>
                parts = existing.split(":")
                if len(parts) == 2 and parts[0] == email:
                    hash_parts = parts[1].split("$")
                    if len(hash_parts) >= 3 and hash_parts[1] == "apr1":
                        existing_salt = hash_parts[2]
                        verify_hash = hash_apr1(password, salt=existing_salt)
                        expected_entry = f"{email}:{verify_hash}"
                        if existing == expected_entry:
                            logger.info(
                                "[IMP:8][secrets_manager] htpasswd already up-to-date for %s — skipping",
                                email,
                            )
                            os.environ["HTPASSWD_FILE"] = htpasswd_file
                            return True
                logger.info(
                    "[IMP:8][secrets_manager] htpasswd credentials changed — regenerating",
                )

        # ── Generate new entry (random salt, will be idempotent on next call) ──
        expected_entry = generate_htpasswd_entry(email, password)
        if not expected_entry:
            logger.warning("[IMP:7][secrets_manager] Failed to generate htpasswd entry")
            return False

        # Write htpasswd file
        htpasswd_path.parent.mkdir(parents=True, exist_ok=True)
        htpasswd_path.write_text(expected_entry + "\n")
        htpasswd_path.chmod(0o644)
        os.environ["HTPASSWD_FILE"] = htpasswd_file
        logger.info(
            "[IMP:9][secrets_manager] htpasswd generated at %s for %s",
            htpasswd_file,
            email,
        )
        return True

    except ImportError:
        logger.warning("[IMP:7][secrets_manager] shared crypto module not available — htpasswd skipped")
        return False
    except OSError as e:
        logger.warning("[IMP:7][secrets_manager] htpasswd OS error: %s", e)
        return False
```

**Note:** Remove the old `subprocess.run(["openssl", "passwd", "-apr1", password])` block entirely. The `subprocess.TimeoutExpired` and `FileNotFoundError` exception handlers are no longer needed (shared crypto module handles those internally).

---

### T5: Update secrets.sh _ensure_htpasswd_generated() to delegate to Python (S2 integration)

**Owner:** Coder
**Output:** `core/lib/secrets.sh` — modified `_ensure_htpasswd_generated()`
**Dependencies:** T3
**Complexity:** 2

**Acceptance Criteria:**
- `_ensure_htpasswd_generated()` delegates hash generation to `python3 crypto.py htpasswd-entry`
- Idempotency logic preserved: check existing file, extract salt, verify with Python
- Shell function remains as thin facade (~20 lines)

**Approach:** The shell function's hash generation (lines 236-237 — `openssl passwd -apr1 "$password"`) and verification (lines 220-224 — `openssl passwd -apr1 -salt "$salt" "$password"`) are replaced with calls to `python3 "${SHARED_DIR}/crypto.py" htpasswd-entry --email "$email" --password "$password" [--salt "$salt"]`.

The function keeps its shell-level file I/O (mkdir, echo >, chmod) and env var export — only the crypto operation is delegated.

---

### T6: Add DRIFT-S3 sync gate test (S3)

**Owner:** Coder
**Output:** `tests/gates/test_gate_fallback_secrets_sync.py` (NEW)
**Dependencies:** None (reads files, no mutations)
**Complexity:** 2

**Acceptance Criteria:**
- Reads `secret-definitions.yaml` → extracts tier=generated entries (name, gen_command)
- Imports `secrets_manager._FALLBACK_SECRETS`
- Asserts: same count, same names (set equality), same gen_commands (per-name)
- Fails with descriptive mismatch message on divergence
- Registered as `@pytest.mark.gate` + entry in `entrypoint-manifest.yaml`
- `make gate MODE=fast` runs this test

**Test design:**
```python
@pytest.mark.gate
def test_fallback_secrets_match_definitions():
    """_FALLBACK_SECRETS in secrets_manager.py must mirror secret-definitions.yaml tier=generated."""
    import yaml
    from core.internal.bootstrap.lifecycle.secrets_manager import _FALLBACK_SECRETS

    # Read definitions
    defs_path = Path(__file__).parents[3] / "core" / "secret-definitions.yaml"
    with open(defs_path) as f:
        data = yaml.safe_load(f)

    defs_generated = {
        s["name"]: s["gen_command"]
        for s in data["secrets"]
        if s.get("tier") == "generated" and s.get("gen_command")
    }

    fallback_map = {s["name"]: s["gen_command"] for s in _FALLBACK_SECRETS}

    # Same names
    assert defs_generated.keys() == fallback_map.keys(), (
        f"Definitions has: {sorted(defs_generated.keys())}\n"
        f"_FALLBACK_SECRETS has: {sorted(fallback_map.keys())}\n"
        f"Missing in _FALLBACK_SECRETS: {sorted(defs_generated.keys() - fallback_map.keys())}\n"
        f"Extra in _FALLBACK_SECRETS: {sorted(fallback_map.keys() - defs_generated.keys())}"
    )

    # Same gen_commands
    for name, gen_cmd in defs_generated.items():
        assert gen_cmd == fallback_map[name], (
            f"{name}: gen_command mismatch\n"
            f"  definition: {gen_cmd}\n"
            f"  _FALLBACK:   {fallback_map[name]}"
        )

    logger.info("[IMP:9][test_fallback_secrets_sync] _FALLBACK_SECRETS ≡ secret-definitions.yaml ✅")
```

---

### T7: Fix DRIFT-S4 — docker_registry_auth.py token in stdin (S4, CRITICAL)

**Owner:** Coder
**Output:** `core/internal/bootstrap/docker_registry_auth.py` — modified `_docker_login()`
**Dependencies:** None
**Complexity:** 1

**Acceptance Criteria:**
- Token NOT passed as argument to bash or docker command
- Token passed via `subprocess.run(..., input=token, ...)`
- Verify: `ps auxww | grep docker` during login shows no token
- Verify: `cat /proc/PID/cmdline | tr '\0' ' '` shows no token

**Before (lines 158-163):**
```python
        result = subprocess.run(
            ["bash", "-c", f"echo '{token}' | docker login -u '{username}' --password-stdin"],
            capture_output=True,
            text=True,
            timeout=DOCKER_RESTART_TIMEOUT,
        )
```

**After:**
```python
        result = subprocess.run(
            ["docker", "login", "-u", username, "--password-stdin"],
            input=token + "\n",            # ⚠️ stdin pipe — NOT visible in /proc/PID/cmdline
            capture_output=True,
            text=True,
            timeout=DOCKER_RESTART_TIMEOUT,
        )
```

**Rationale:** `subprocess.run` with `input=` passes data through a pipe (stdin) — the token never appears in the process command line. The old `bash -c "echo '{token}' | ..."` interpolates the token into the shell command string, making it visible to any process with `read` access to `/proc/PID/cmdline`.

---

### T8: DRIFT-S5 naming conflict resolution (S5)

**Owner:** Coder
**Output:** Modified test, definitions, env files
**Dependencies:** None
**Complexity:** 3

**Sub-tasks:**

#### T8.1: Remove OPENAI_API_KEY from REQUIRED_SECRET_KEYS

**File:** `tests/test_secrets_validation.py` — line 62
**Change:** Remove `"OPENAI_API_KEY",` from `REQUIRED_SECRET_KEYS` list.

Before:
```python
REQUIRED_SECRET_KEYS: list[str] = [
    "HERMES_DASHBOARD_PASSWORD",
    "GF_SECURITY_ADMIN_PASSWORD",
    "LANGFUSE_INIT_USER_PASSWORD",
    "OPENAI_API_KEY",           # ← REMOVE THIS LINE
    "DEEPSEEK_API_KEY",
    "CLICKHOUSE_PASSWORD",
    "POSTGRES_PASSWORD",
]
```

After:
```python
REQUIRED_SECRET_KEYS: list[str] = [
    "HERMES_DASHBOARD_PASSWORD",
    "GF_SECURITY_ADMIN_PASSWORD",
    "LANGFUSE_INIT_USER_PASSWORD",
    "DEEPSEEK_API_KEY",
    "CLICKHOUSE_PASSWORD",
    "POSTGRES_PASSWORD",
]
```

#### T8.2: Replace OPENAI_API_KEY test with LITELLM_MASTER_KEY presence check

**File:** `tests/test_secrets_validation.py` — lines 375-440 (entire test function + docstring)

The test `test_openai_key_matches_litellm_master_key` is replaced with `test_litellm_master_key_present`:
- Instead of checking OPENAI_API_KEY == LITELLM_MASTER_KEY, check that LITELLM_MASTER_KEY is set (non-empty, non-placeholder)
- OPENAI_API_KEY is tier=removed — test should NOT enforce it
- Hermes-agent .env still has OPENAI_API_KEY for backward compat (Hermes code uses it) — but this is a Hermes concern, not platform-level

**New test:**
```python
@pytest.mark.predeploy
@pytest.mark.skipif(
    os.environ.get("CI") == "true" or os.environ.get("E2E_MODE") == "ci",
    reason="CI: production env vars unavailable",
)
@ldd_trajectory
def test_litellm_master_key_present(
    caplog: pytest.LogCaptureFixture,
    modules_dir: str,
) -> None:
    """LITELLM_MASTER_KEY must be set in hermes-agent/.env — required for LiteLLM admin API access.

    OPENAI_API_KEY (tier=removed, DevPlan 049) is no longer enforced at platform level.
    """
    env_path = _get_env_path(modules_dir)
    env_vars = _parse_dotenv(env_path)

    litellm_key = env_vars.get("LITELLM_MASTER_KEY", "")

    if not litellm_key:
        pytest.fail("LITELLM_MASTER_KEY is empty or not set in hermes-agent/.env")

    if "placeholder" in litellm_key.lower() or "your-" in litellm_key.lower():
        pytest.skip("LITELLM_MASTER_KEY is placeholder — skip validation")

    logger.info("[IMP:9][test_litellm_master_key_present] ✅ LITELLM_MASTER_KEY present")
```

#### T8.3: Remove OPENAI_API_KEY from hermes-agent/.env.example

**File:** `core/modules/hermes-agent/.env.example` — lines 42-46

Before:
```
# ⚠️ OPENAI_API_KEY должен совпадать с LITELLM_MASTER_KEY в production (см. TRAP[INCIDENT] в docker-compose.base.yml)
# ·   Hermes использует OPENAI_API_KEY для аутентификации в LiteLLM proxy.
# ·   Если ключи разные → Hermes не может вызвать LiteLLM, падает на chat completions.
DEEPSEEK_API_KEY=sk-your-deepseek-key-here
OPENAI_API_KEY=sk-your-openai-key-here
LITELLM_MASTER_KEY=sk-your-litellm-master-key-here  # ⚠️ Должен совпадать с OPENAI_API_KEY
```

After:
```
# LITELLM_MASTER_KEY — master key for LiteLLM admin API access.
# Hermes-agent uses this key to authenticate with LiteLLM proxy (via OPENAI_BASE_URL).
DEEPSEEK_API_KEY=sk-your-deepseek-key-here
LITELLM_MASTER_KEY=sk-your-litellm-master-key-here
```

**Note:** Keep OPENAI_API_KEY in `hermes-agent/.env` (gitignored) — Hermes code still reads it. Remove only from the template.

#### T8.4: Add GHCR_PUSH_TOKEN to secret-definitions.yaml

**File:** `core/secret-definitions.yaml` — after GHCR_PULL_TOKEN (line 116)

Add:
```yaml
  - name: GHCR_PUSH_TOKEN
    tier: optional
    source: ci-secret
    ci_default: ""
    note: "Fine-grained PAT: write:packages for ghcr.io push (L2 image). Used by make hermes-push-l2. NOT required for pull — GHCR_PULL_TOKEN handles read."
```

#### T8.5: S3_ACCESS_KEY / S3_SECRET_KEY consumers — documentation only

No code changes. These have `consumers: []` in secrets-manifest.yaml because backup-cron accesses them through compose override rather than formal `env_requires`. This is a known architectural pattern — add a note in `secrets-manifest.yaml`:

For S3_ACCESS_KEY and S3_SECRET_KEY entries, add to `note:`:
```
consumers: [] — backup-cron accesses through compose override (S3_* vars), not formal env_requires contract. This is intentional: var names are AWS SDK-compatible (AWS_ACCESS_KEY_ID/AWS_SECRET_ACCESS_KEY), resolved through compose env mapping, not module.yaml env_requires.
```

---

### T9: DRIFT-S6 — POSTGRES_PASSWORD unified default

**Owner:** Coder
**Output:** Modified `.env`, `hermes-agent/.env`
**Dependencies:** None
**Complexity:** 1

**Acceptance Criteria:**
- `.env:25`: `POSTGRES_PASSWORD=test-pg-pwd` (was `testpass`)
- `hermes-agent/.env:45`: `POSTGRES_PASSWORD=test-pg-pwd` (was `test-postgres-password`)
- All other files already use canonical value (verified during audit)
- New gate test validates consistency

**File: `/Users/tronyx/projects/ai-platform/.env` — line 25**

Before: `POSTGRES_PASSWORD=testpass`
After: `POSTGRES_PASSWORD=test-pg-pwd`

**File: `/Users/tronyx/projects/ai-platform/core/modules/hermes-agent/.env` — line 45**

Before: `POSTGRES_PASSWORD=test-postgres-password`
After: `POSTGRES_PASSWORD=test-pg-pwd`

**File: `/Users/tronyx/projects/ai-platform/core/modules/hermes-agent/.env.example` — line 70**

Before: `POSTGRES_PASSWORD=your-postgres-password-here`
After: `POSTGRES_PASSWORD=test-pg-pwd  # ⚠️ CONSTRAINT: must match ^[A-Za-z0-9._-]+$`

---

### T10: DRIFT-S7 — NEXTAUTH_SECRET unified default

**Owner:** Coder
**Output:** Modified `.env`, `hermes-agent/.env`
**Dependencies:** None
**Complexity:** 1

**Acceptance Criteria:**
- `.env:78`: `NEXTAUTH_SECRET=ci-test-nextauth-secret-32-chars-min!!` (was `sk-test-nextauth-secret`)
- `hermes-agent/.env:48`: `NEXTAUTH_SECRET=ci-test-nextauth-secret-32-chars-min!!` (was `test-nextauth-secret-value`)
- New gate test validates consistency

**File: `/Users/tronyx/projects/ai-platform/.env` — line 78**

Before: `NEXTAUTH_SECRET=sk-test-nextauth-secret`
After: `NEXTAUTH_SECRET=ci-test-nextauth-secret-32-chars-min!!`

**File: `/Users/tronyx/projects/ai-platform/core/modules/hermes-agent/.env` — line 48**

Before: `NEXTAUTH_SECRET=test-nextauth-secret-value`
After: `NEXTAUTH_SECRET=ci-test-nextauth-secret-32-chars-min!!`

**File: `/Users/tronyx/projects/ai-platform/core/modules/hermes-agent/.env.example` — line 72**

Before: `NEXTAUTH_SECRET=your-nextauth-secret-here`
After: `NEXTAUTH_SECRET=ci-test-nextauth-secret-32-chars-min!!`

---

### T11: Add env defaults consistency gate test

**Owner:** Coder
**Output:** `tests/gates/test_gate_env_defaults_consistency.py` (NEW)
**Dependencies:** T9, T10 (after values unified)
**Complexity:** 2

**Acceptance Criteria:**
- Reads secret-definitions.yaml, .env.example, platform-env.yaml
- Validates: POSTGRES_PASSWORD ci_default == .env.example value == platform-env.yaml env_default value
- Validates: NEXTAUTH_SECRET ci_default == .env.example value == platform-env.yaml env_default value
- Validates: POSTGRES_USER, CLICKHOUSE_PASSWORD, MINIO_ROOT_USER, MINIO_ROOT_PASSWORD same check
- `@pytest.mark.gate` + registered in entrypoint-manifest.yaml
- `make gate MODE=fast` runs this test

```python
@pytest.mark.gate
def test_env_defaults_consistency():
    """CI defaults in secret-definitions must match .env.example and platform-env.yaml."""
    import yaml

    root = Path(__file__).parents[3]

    # Read definitions
    with open(root / "core/secret-definitions.yaml") as f:
        defs = {s["name"]: s.get("ci_default", "") for s in yaml.safe_load(f)["secrets"] if "ci_default" in s}

    # Read .env.example
    env_example = {}
    with open(root / ".env.example") as f:
        for line in f:
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                k, _, v = line.partition("=")
                env_example[k.strip()] = v.strip()

    # Read platform-env.yaml
    with open(root / "platform-env.yaml") as f:
        platform_env = yaml.safe_load(f).get("env_defaults", {})

    # Keys to check (secrets with ci_default that also appear in .env.example)
    check_keys = [
        "POSTGRES_PASSWORD", "POSTGRES_USER",
        "CLICKHOUSE_PASSWORD", "MINIO_ROOT_USER", "MINIO_ROOT_PASSWORD",
        "NEXTAUTH_SECRET", "LITELLM_MASTER_KEY",
        "SALT", "LANGFUSE_INIT_ORG_ID", "LANGFUSE_INIT_PROJECT_ID",
        "LANGFUSE_PUBLIC_KEY", "LANGFUSE_SECRET_KEY", "LANGFUSE_INIT_USER_PASSWORD",
        "PLATFORM_MASTER_PASSWORD", "AGE_SECRET_KEY", "GHCR_PULL_TOKEN",
    ]

    failures = []
    for key in check_keys:
        def_val = defs.get(key, "")
        env_val = env_example.get(key, "")
        plat_val = platform_env.get(key, "")

        if not def_val and not env_val:
            continue  # Both empty = ci-secret, skip

        if def_val != env_val:
            failures.append(f"{key}: secret-definitions={def_val!r} ≠ .env.example={env_val!r}")
        if env_val != plat_val:
            failures.append(f"{key}: .env.example={env_val!r} ≠ platform-env.yaml={plat_val!r}")

    assert not failures, (
        "Default value drift detected across layers:\n"
        + "\n".join(f"  • {f}" for f in failures)
        + "\n\nFix: update secret-definitions.yaml (canonical) → regenerate platform-env.yaml → update .env.example"
    )
```

---

### T12: Gate + verification

**Owner:** Coder
**Output:** All tests pass, CI gate green
**Dependencies:** T1-T11
**Complexity:** 2

**Acceptance Criteria:**
- `python3 -m pytest tests/unit/test_age_key.py tests/unit/test_crypto.py -v` — all pass
- `python3 -m pytest tests/gates/test_gate_fallback_secrets_sync.py tests/gates/test_gate_env_defaults_consistency.py -v` — all pass
- `python3 -m pytest tests/test_secrets_validation.py -v` — all pass (updated tests)
- `make fix-gate && make gate MODE=fast` — green
- `grep -r "detect_age_key" --include="*.sh" core/` — shows only thin wrappers (5 files) + shared module reference
- Verify DRIFT-S4 fix: `grep "echo.*token" core/internal/bootstrap/docker_registry_auth.py` — no match (old pattern gone)

---

## $TEST_SPEC

| Test file | Test function | Scenario | Module under test |
|-----------|---------------|----------|-------------------|
| tests/unit/test_age_key.py | test_detect_from_env | AGE_SECRET_KEY set in os.environ → returns key | age_key.py::detect_age_key |
| tests/unit/test_age_key.py | test_detect_from_sops_fallback | AGE_SECRET_KEY empty, SOPS_AGE_KEY set → returns SOPS key | age_key.py::detect_age_key |
| tests/unit/test_age_key.py | test_detect_from_file | Both empty, AGE_SECRET_KEY_FILE points to valid file → returns file content | age_key.py::detect_age_key |
| tests/unit/test_age_key.py | test_detect_from_empty_file | File exists but empty → returns None | age_key.py::detect_age_key |
| tests/unit/test_age_key.py | test_detect_not_found | Nothing set → returns None | age_key.py::detect_age_key |
| tests/unit/test_age_key.py | test_log_tag_propagation | log_tag="test-tag" → log messages contain [test-tag] | age_key.py::detect_age_key |
| tests/unit/test_crypto.py | test_hash_apr1_random_salt | hash_apr1(password) without salt → valid apr1 format, random salt | crypto.py::hash_apr1 |
| tests/unit/test_crypto.py | test_hash_apr1_fixed_salt_idempotent | hash_apr1(password, salt="abcd") called 3x → identical results | crypto.py::hash_apr1 |
| tests/unit/test_crypto.py | test_generate_htpasswd_entry | generate_htpasswd_entry(email, password) → "email:$apr1$..." | crypto.py::generate_htpasswd_entry |
| tests/unit/test_crypto.py | test_idempotent_entry_regeneration | Same email+password+salt → identical entry | crypto.py::generate_htpasswd_entry |
| tests/gates/test_gate_fallback_secrets_sync.py | test_fallback_secrets_match_definitions | _FALLBACK_SECRETS ≡ secret-definitions.yaml tier=generated | secrets_manager.py::_FALLBACK_SECRETS |
| tests/gates/test_gate_env_defaults_consistency.py | test_env_defaults_consistency | POSTGRES_PASSWORD, NEXTAUTH_SECRET etc. consistent across 3 layers | secret-definitions.yaml + .env.example + platform-env.yaml |

---

## File Manifest

| File | Action | Lines changed |
|------|--------|---------------|
| `core/internal/shared/age_key.py` | **CREATE** | ~80 lines |
| `core/internal/shared/crypto.py` | **CREATE** | ~100 lines |
| `core/entrypoints/bootstrap.sh` | MODIFY (lines 53-76 → wrapper) | -14, +10 |
| `core/entrypoints/node-update.sh` | MODIFY (lines 44-66 → wrapper) | -13, +10 |
| `core/lib/secrets.sh` | MODIFY (lines 134-138, 236-237, 220-224) | -15, +15 |
| `core/internal/secrets/decrypt-secrets.sh` | MODIFY (lines 76-84 → wrapper) | -4, +5 |
| `core/internal/bootstrap/node-lifecycle.sh` | MODIFY (line 44) | -1, +3 |
| `core/internal/bootstrap/lifecycle/secrets_manager.py` | MODIFY (lines 375-450 _ensure_htpasswd) | -75, +50 |
| `core/internal/bootstrap/docker_registry_auth.py` | MODIFY (lines 158-163) | -4, +2 |
| `core/secret-definitions.yaml` | MODIFY (add GHCR_PUSH_TOKEN after line 116) | +6 |
| `core/secrets-manifest.yaml` | MODIFY (note for S3_ACCESS_KEY, S3_SECRET_KEY) | +2 |
| `tests/test_secrets_validation.py` | MODIFY (line 62, lines 375-440) | -70, +40 |
| `.env` | MODIFY (line 25 POSTGRES_PASSWORD, line 78 NEXTAUTH_SECRET) | +2, -2 |
| `.env.example` | MODIFY (line 129 LITELLM_METRICS_TOKEN → verify 072 already,) | 0 (072 handles) |
| `core/modules/hermes-agent/.env` | MODIFY (line 45 POSTGRES_PASSWORD, line 48 NEXTAUTH_SECRET) | +2, -2 |
| `core/modules/hermes-agent/.env.example` | MODIFY (lines 42-46, 70, 72) | -5, +5 |
| `tests/unit/test_age_key.py` | **CREATE** | ~100 lines |
| `tests/unit/test_crypto.py` | **CREATE** | ~90 lines |
| `tests/gates/test_gate_fallback_secrets_sync.py` | **CREATE** | ~50 lines |
| `tests/gates/test_gate_env_defaults_consistency.py` | **CREATE** | ~50 lines |
| `core/entrypoint-manifest.yaml` | MODIFY (register 2 new gates) | +12 |

**Total:** 22 files (4 CREATE, 18 MODIFY)

---

## Verification Commands

```bash
# Unit tests for new shared modules
python3 -m pytest tests/unit/test_age_key.py tests/unit/test_crypto.py -v -s

# Gate tests for sync + consistency
python3 -m pytest tests/gates/test_gate_fallback_secrets_sync.py tests/gates/test_gate_env_defaults_consistency.py -v -s

# Existing secrets validation (updated)
python3 -m pytest tests/test_secrets_validation.py -v -s

# Full gate
make fix-gate && make gate MODE=fast

# Verify no remaining duplicated detect_age_key logic
grep -n "AGE_SECRET_KEY:-.*&&.*SOPS_AGE_KEY" core/lib/secrets.sh core/internal/secrets/decrypt-secrets.sh
# Expected: zero matches (all replaced by Python call)

# Verify token no longer in cmdline pattern
grep -n "echo.*token.*docker login" core/internal/bootstrap/docker_registry_auth.py
# Expected: zero matches

# Verify unified defaults
grep "POSTGRES_PASSWORD=" .env core/modules/hermes-agent/.env
# Expected: both show "test-pg-pwd"

grep "NEXTAUTH_SECRET=" .env core/modules/hermes-agent/.env
# Expected: both show "ci-test-nextauth-secret-32-chars-min!!"

# Verify OPENAI_API_KEY removed from REQUIRED_SECRET_KEYS
grep OPENAI_API_KEY tests/test_secrets_validation.py | grep REQUIRED_SECRET_KEYS
# Expected: no match (removed from list)
```

---

## $PARALLEL_GROUPS

### Wave 1 (independent, no shared files)
- Tasks: T1, T3, T7, T8.1, T8.3, T8.4, T9, T10
- Command: `coder Read DevPlan.md, implement Wave 1: T1 (age_key.py), T3 (crypto.py), T7 (docker_registry_auth fix), T8.1+T8.3+T8.4 (OPENAI_API_KEY removal + GHCR_PUSH_TOKEN), T9 (POSTGRES_PASSWORD), T10 (NEXTAUTH_SECRET)`

### Wave 2 (depends on Wave 1)
- Tasks: T2 (depends T1), T4 + T5 (depend T3), T6 (depends T1/T3 for module existence), T11 (depends T9/T10)
- Command: `coder Read DevPlan.md, implement Wave 2: T2 (shell wrappers), T4 (secrets_manager htpasswd), T5 (secrets.sh htpasswd), T6 (fallback sync test), T8.2 (replace test), T8.5 (S3 note), T11 (env defaults test)`

### Wave 3 (final gate)
- Tasks: T12
- Command: `coder Read DevPlan.md, implement Wave 3: T12 (gate + verification)`

---

## Design Decisions (Summary)

| ID | Decision | Rationale |
|----|----------|-----------|
| DD1 | age_key.py as subprocess (not sourced shell lib) | Shell libs pollute namespace, can't be unit-tested, can't enforce typed contracts. Subprocess with stdout/stderr separation preserves shell compatibility while enabling Python testing. |
| DD2 | Fixed-salt idempotency via existing hash extraction | Per-credential salt (not hardcoded global salt) protects against precomputation. First-gen random salt, subsequent calls extract salt from file → deterministic. |
| DD3 | GHCR_PUSH_TOKEN as tier:optional ci-secret | Completes PULL/PUSH symmetry in definitions. Optional because push is a human operator action (not automated CI). |
| DD4 | OPENAI_API_KEY removed from platform-level enforcement | Tier=removed per DevPlan 049. Kept in hermes-agent/.env for Hermes backward compat. Test replaced with LITELLM_MASTER_KEY presence check. |
| DD5 | Shared crypto.py wraps openssl (not Python cryptography lib) | Minimal dependency. openssl is already required by the platform. Random salt is acceptable for test values. If production needs higher entropy → can be extended without changing API. |

---

## Next Steps

### Wave 1
```
coder Read /Users/tronyx/projects/ai-platform/.ai/plans/078-secrets-tokens-unification/01-DevPlan.md,
implement Wave 1: T1 (age_key.py), T3 (crypto.py), T7 (docker_registry_auth pipe fix),
T8.1 (remove OPENAI_API_KEY from REQUIRED list), T8.3 (remove from hermes-agent/.env.example),
T8.4 (add GHCR_PUSH_TOKEN to definitions), T9 (POSTGRES_PASSWORD unified), T10 (NEXTAUTH_SECRET unified)
```

### Wave 2
```
coder Read /Users/tronyx/projects/ai-platform/.ai/plans/078-secrets-tokens-unification/01-DevPlan.md,
implement Wave 2: T2 (shell age-key wrappers in 5 files), T4 (secrets_manager.py htpasswd with shared crypto),
T5 (secrets.sh htpasswd delegation), T6 (fallback secrets sync gate test),
T8.2 (replace OPENAI_API_KEY test), T8.5 (S3 consumers note),
T11 (env defaults consistency gate test)
```

### Wave 3
```
coder Read /Users/tronyx/projects/ai-platform/.ai/plans/078-secrets-tokens-unification/01-DevPlan.md,
implement Wave 3: T12 (run all tests, fix-gate, gate MODE=fast, verify drift points closed)
```

$END_DEVPLAN
