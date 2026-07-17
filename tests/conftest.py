# GREP_SUMMARY: conftest thin re-export layer, _conftest package
# STRUCTURE: thin re-export → from _conftest.* import public names + explicit underscore imports for consumed fixtures/helpers
# region MODULE_CONTRACT
## @purpose — Thin re-export layer for pytest conftest. All logic is in tests/_conftest/ package.
##            This file exists because pytest discovers conftest.py files by name.
## @scope — Single re-export: imports public names from _conftest/__init__.py into conftest namespace
##          plus explicit underscore-prefixed names consumed by test files.
## @invariants
##   - This file is <80 lines (excluding docstring)
##   - Public names from _conftest/__init__.py are re-exported via `from _conftest import *`
##   - Underscore-prefixed names used by test files are imported explicitly below
##   - No logic lives here — only re-exports
## @rationale — Decomposition of the original 1429-line conftest god-module into 12 submodules
##              in _conftest/ package. Thin re-export preserves backward compatibility with all
##              65+ test files that do `from conftest import ...` or `from tests.conftest import ...`.
## @changes — 2026-07-12 | Rewritten as thin re-export from _conftest package (DevPlan 031)
##            2026-07-16 | T6 cleanup: removed stale underscore re-exports no test file imports
# endregion MODULE_CONTRACT

from _conftest import *  # noqa: F403

# Also import underscore-prefixed names explicitly (not included in *)
# — autouse fixtures (needed for pytest discovery) —
from _conftest.e2e import _e2e_disable_proxy, _load_test_env  # noqa: F401

# — consumed by test files via `from conftest import ...` —
from _conftest.infra import _test_infra_was_active  # noqa: F401
from _conftest.ldd import (  # noqa: F401
    _ensure_volume_dirs,
    _handle_e2e_error,
    _print_ldd_trajectory,
)
