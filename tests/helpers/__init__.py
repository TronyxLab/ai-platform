# GREP_SUMMARY: helpers, package, FQDNRegistry, load-schema, gate-helpers-bridge
# STRUCTURE: ┌package marker┐ → ◇ re-exports from helpers.py (legacy flat module) → ⎋ backward-compat for `from helpers import load_schema`
# region MODULE_CONTRACT
## @purpose  Package marker + backward-compat bridge for tests/helpers/.
##           Wave 1 (W1-E4) created tests/helpers/ as a package for gate_helpers.py,
##           which shadowed the legacy flat tests/helpers.py module. To preserve
##           backward compatibility with ~4 existing test files using
##           `from helpers import load_schema, FQDNRegistry, FQDNConflictError`,
##           this __init__.py re-exports the legacy helpers.py content.
## @scope    All test files importing from `helpers` (flat) or `helpers.gate_helpers` (submodule)
## @invariants
##   - `from helpers import load_schema` works (re-exported from legacy helpers.py)
##   - `from helpers.gate_helpers import ...` works (submodule)
##   - Legacy helpers.py moved here; tests/helpers.py file removed
## @rationale Namespace collision fix: package + module can't coexist with the same
##            name in Python. Wave 1 chose package; legacy code needs flat import.
##            Solution: move flat content INTO the package's __init__.py — both work.
## @changes  2026-07-21 | Wave 2 W2-E2 — merged legacy helpers.py into package __init__
# endregion MODULE_CONTRACT

import json
from pathlib import Path

# Preserve SCHEMAS_DIR resolution: legacy helpers.py was at tests/helpers.py,
# so __file__.parent.parent was tests/. Now __file__ is tests/helpers/__init__.py,
# so parent.parent.parent is tests/. Adjust accordingly.
SCHEMAS_DIR = Path(__file__).resolve().parent.parent.parent / "core" / "schemas"

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


# Expose submodule for `from helpers.gate_helpers import ...`
from . import gate_helpers  # noqa: F401
