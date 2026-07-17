# GREP_SUMMARY: helpers FQDNRegistry FQDNConflictError E1 first-claim idempotent load-schema SCHEMAS_DIR
# STRUCTURE: FQDNRegistry.claim(fqdn, project) → raises FQDNConflictError on conflict; owner_of(fqdn) → str|None || load_schema(name) → dict
# region MODULE_CONTRACT [DOMAIN(TESTING):3; CONCEPT(FQDN):2; TECH(PYTHON):2]
## @purpose — Shared test helpers: FQDNRegistry for simulating E1 conflict detection,
##           FQDNConflictError for conflict signalling, and load_schema for
##           loading JSON Schema files from core/schemas/.
## @scope — Used by test_project_schema.py, test_validate.py, and schema validation tests;
##          extracted into helpers.py to eliminate duplication (TASK-5).
## @invariants
##   - FQDNRegistry: first-claim wins, second-claim raises FQDNConflictError
##   - Same project re-claiming its own FQDN is idempotent (no error)
##   - load_schema raises FileNotFoundError for unknown schema names
##   - No pytest fixtures here (they belong in conftest.py)
## @rationale — helpers.py (not conftest.py) because FQDNRegistry is test domain logic,
##              not pytest infrastructure. Keeping conftest focused on fixtures/hooks.
##              load_schema() is a pure utility with no side effects — belongs in helpers.
## @changes — 2026-07-01 | Extracted from test_project_schema.py and test_validate.py
##            2026-07-06 | Added load_schema() + SCHEMAS_DIR per D10 fix (T5)
def _module_contract():
    pass


# endregion MODULE_CONTRACT

import json
from pathlib import Path

SCHEMAS_DIR = Path(__file__).resolve().parent.parent / "core" / "schemas"

# ⚠️ Test-only password — NOT for production use. Centralised here to avoid
# hardcoded duplication across test files (W1 fix).
_CLICKHOUSE_PASSWORD = "test-clickhouse-pwd-not-for-prod"


def load_schema(schema_name: str) -> dict:
    """Load a JSON Schema file from core/schemas/.

    ## @purpose — Provide a single source of truth for schema loading across all test files.
    ## @io
    ## - input: schema_name (str) — filename without path, e.g. "manifest.json"
    ## - output: dict — parsed JSON schema
    ## @complexity: O(1)
    ## @invariants
    ##   - Raises FileNotFoundError if schema file does not exist
    ##   - Returns parsed dict (not str)
    ## @rationale — D10: previously duplicated in multiple test files; centralised here.
    """
    schema_path = SCHEMAS_DIR / schema_name
    if not schema_path.exists():
        raise FileNotFoundError(f"Schema not found: {schema_path}")
    with open(schema_path) as f:
        return json.load(f)


class FQDNRegistry:
    """
    Simulates first-claim FQDN ownership registry.

    @invariants
      - First project to claim a FQDN owns it
      - Second project claiming same FQDN → raises FQDNConflictError (E1)
      - Same project re-claiming is idempotent
    """

    def __init__(self) -> None:
        self._registry: dict[str, str] = {}

    def claim(self, fqdn: str, project_name: str) -> None:
        """
        Claim a FQDN for a project.

        Raises FQDNConflictError if already owned by another project.
        Idempotent if same project re-claims its own FQDN.
        """
        if fqdn in self._registry and self._registry[fqdn] != project_name:
            existing = self._registry[fqdn]
            raise FQDNConflictError(
                f"E1: FQDN '{fqdn}' already claimed by '{existing}', '{project_name}' cannot claim it — deploy blocked"
            )
        self._registry[fqdn] = project_name

    def owner_of(self, fqdn: str) -> str | None:
        """Return the project name that owns the given FQDN, or None."""
        return self._registry.get(fqdn)


class FQDNConflictError(Exception):
    """Raised when FQDN conflict (E1) is detected."""
