# GREP_SUMMARY: __main__, python -m ai_instructions, entrypoint, cli, exit code
# STRUCTURE: ┌python -m ai_instructions┐ → ⚡ sys.exit(main()) → ⎋ process exit code
# region MODULE_CONTRACT
## @purpose  Enable `python -m ai_instructions` as an alternative to the console script
## @scope    Module execution entry point only
## @invariants
##   - Propagates main() exit code so `check` drift (exit 1) is observable via -m
## @rationale runpy discards main()'s return value; sys.exit preserves CLI exit codes
# endregion MODULE_CONTRACT

"""Allow ``python -m ai_instructions``."""

import sys

from ai_instructions.runtime.cli import main

if __name__ == "__main__":
    sys.exit(main())
