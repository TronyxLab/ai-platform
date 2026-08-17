---
name: doxygen-python
description: Python Doxygen Markup Template — Doxygen-style contract tags for Python modules with region markers and dummy function
---

# region MODULE_CONTRACT
## @purpose  SKILL: Python Doxygen Markup Template — Doxygen-style contract tags for Python modules with region markers and dummy function
## @scope    architect, coder, qa, docs
## @invariants
##   - @protected  true
##   - Тело скилла — 1:1 перенос framework-source 0.6.3
# endregion MODULE_CONTRACT

  ## Python Doxygen Markup Template

  Every module (.py file) MUST use the following Doxygen-style contract tags wrapped in `# region` / `# endregion` blocks:

  ### Module-level (in `# region MODULE_CONTRACT`)
  ```
  # region MODULE_CONTRACT [DOMAIN(X): ...; CONCEPT(Y): ...; TECH(Z): ...]
  ## @modulecontract
  ## @purpose — the GOAL of the module (why it exists, not what it does)
  ## @scope — main functional areas covered
  ## @input — module-wide input data
  ## @output — what the module provides to the system
  ## @links — USES_API(X), READS_DATA_FROM(Y)
  ## @links_to_spec — technical requirements reference
  ## @invariants — conditions/state that always hold
  ## @rationale — Q: why this approach? A: justification
  ## @changes — LAST_CHANGE: version + description
  ## @modulemap — all functions/classes with [Weight] descriptions
  ## @usecases — Actor → Action → Goal
  def _module_contract():
      pass
  # endregion MODULE_CONTRACT
  # GREP_SUMMARY: comma, separated, keywords
  # STRUCTURE: ▶ main → ○ step → ◇ decision → ⊕ result → ⎋ return
  ```

  ### Function-level (in `# region FUNC_Name`)
  ```
  # region FUNC_Name [DOMAIN(X): ...; CONCEPT(Y): ...; TECH(Z): ...]
  ## @purpose — GOAL of the function (outcome, not line-by-line summary)
  ## @uses — APIs or modules used
  ## @io — Input types → Output types
  ## @complexity — 1-10 scale
  def func_name():
      ...
  # endregion FUNC_Name
  ```

  ### Class-level (in `# region CLASS_Name`)
  ```
  # region CLASS_Name [DOMAIN(X): ...; CONCEPT(Y): ...; TECH(Z): ...]
  ## @purpose — Goal of the class
  class ClassName:
      # region METHOD_method_name
      ## @purpose — Method goal
      def method_name():
          ...
      # endregion METHOD_method_name
  # endregion CLASS_Name
  ```

  ### Doxygen ALIASES
  In Doxyfile, include: purpose, invariants, rationale, io, modulemap, complexity, uses, scope, input, output, links, modulecontract, changes, usecases.

<!-- ai-instructions:0.7.0 -->
