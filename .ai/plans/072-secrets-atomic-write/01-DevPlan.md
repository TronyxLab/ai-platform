# DevPlan 072: Secrets Atomic Write + Token Cleanup

$ARTIFACT_CONTRACT
PURPOSE: Fix secrets_manager.py append-mode bug (creates duplicate lines on repeated calls) and remove leftover LITELLM_METRICS_TOKEN from .env.example. Complete token unification.
DESCRIPTION: secrets_manager.py:312 uses `open(secrets_env, "a")` — append mode. On repeated bootstrap runs, the same generated secrets are appended again → duplicates in secrets.env → `source` reads last value → non-deterministic behavior. Fix: read existing env first, use atomic overwrite. Also cleanup: remove LITELLM_METRICS_TOKEN from .env.example (unified to LITELLM_MASTER_KEY).
RATIONALE: Append-mode is a time-bomb. On third bootstrap with --force, secrets.env grows with duplicates. The `source` command reads the LAST occurrence, so the first bootstrap's value is lost. If that value was used to provision LiteLLM virtual keys, subsequent runs will use a different key → auth failures.
ACCEPTANCE_CRITERIA:
  - secrets_manager.py `ensure_secrets()` uses overwrite mode (not append)
  - secrets_manager.py checks existing values in parsed secrets.env before generating
  - `source_secrets_env()` is called BEFORE write, existing values are preserved
  - Repeated calls produce identical secrets.env (idempotent)
  - LITELLM_METRICS_TOKEN removed from .env.example:129
  - No grep hits for LITELLM_METRICS_TOKEN in non-comment, non-doc contexts
  - `tests/unit/test_secrets_manager.py` — test idempotency (call 3x, file unchanged)
  - `make gate MODE=fast` — green
IMPLEMENTS: Wave 6A — core unification P0
IMPACTS:
  - core/internal/bootstrap/lifecycle/secrets_manager.py (ensure_secrets:310-313)
  - .env.example:129
  - tests/unit/test_secrets_manager.py (new idempotency test)
REQUIRES: None

## Tasks

### T1: Fix ensure_secrets() — atomic overwrite
- Before the generation loop: call `source_secrets_env(secrets_env)` → get existing dict
- For each secret to generate: check existing dict FIRST, then os.environ
- Collect ALL secrets (existing + newly generated) into a single dict
- Write ONCE with `open(secrets_env, "w")` — atomic overwrite
- Preserve file ownership/permissions (if file exists, stat before overwrite)

### T2: Preserve non-generated secrets on overwrite
- The decrypted secrets.env from SOPS contains secrets NOT in the generated list
- On overwrite, ALL existing entries must be preserved, only MISSING generated secrets added
- Implementation: read existing → merge with generated → write all

### T3: Add idempotency test
- `tests/unit/test_secrets_manager.py::test_ensure_secrets_idempotent`
  - Mock secrets.env with some existing values
  - Call ensure_secrets() 3 times
  - Assert file unchanged after first call
  - Assert no duplicate lines

### T4: Remove LITELLM_METRICS_TOKEN
- Remove line 129 from .env.example
- `grep -r "LITELLM_METRICS_TOKEN" --include="*.yml" --include="*.yaml" --include="*.sh" --include="*.py" --include="*.env" .` → only doc-comment hits remain

### T5: Gate
- `make fix-gate && make gate MODE=fast` — green
- `python3 -m pytest tests/unit/test_secrets_manager.py -v`
