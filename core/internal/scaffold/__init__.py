# GREP_SUMMARY: scaffold package-init exports project_lister context_initializer project_remover scaffold_helpers project_scaffolder
# STRUCTURE: ┌package-init┐ → ◇ lazy re-exports (PEP 562 __getattr__) → ⊕ __all__ → ⎋ sibling modules
# region MODULE_CONTRACT
## @purpose  Package init for core.internal.scaffold — project scaffolding operations
##            (DP-092 Wave 1-4 Strangler-Fig Python migration).
## @scope    Package-level exports: project_lister, context_initializer, project_remover,
##           scaffold_helpers, project_scaffolder, context_registry, gen_env_platform,
##           vhost_renderer, project_adopter.
## @invariants
##   1. All business logic lives in sibling .py modules.
##   2. Shell files are now thin facades delegating to Python modules.
##   3. Re-exports are LAZY (PEP 562 __getattr__) — importing the package does not
##      import sibling modules, avoiding import cycles (scaffold_helpers ↔ project_*).
##   4. __all__ lists the canonical re-export names for `from core.internal.scaffold import *`.
## @rationale VR 092 S1: package advertised exports but had none — cosmetic gap flagged
##            by QA. Lazy re-exports provide the documented namespace without eager
##            import cost or circular-import risk at package load time.
## @changes  2026-07-31 | VR 092 S1 — Added lazy re-exports (was contract-only __init__)
# endregion MODULE_CONTRACT

from __future__ import annotations

import importlib as _importlib
from types import ModuleType

__all__ = [
    "context_initializer",
    "project_lister",
    "project_remover",
    "project_scaffolder",
    "scaffold_helpers",
]

_LAZY_EXPORTS = frozenset(__all__)


# region FUNC___getattr__
## @purpose  Lazy module re-export — import sibling module on attribute access (PEP 562).
## @io       ⇥ name: str → ⎋ module | raise AttributeError
## @complexity — O(1) + first-access import cost
def __getattr__(name: str) -> ModuleType:
    """Lazily import a sibling module when accessed as a package attribute."""
    if name in _LAZY_EXPORTS:
        module = _importlib.import_module(f"{__name__}.{name}")
        globals()[name] = module
        return module
    msg = f"module {__name__!r} has no attribute {name!r}"
    raise AttributeError(msg)


# endregion FUNC___getattr__
