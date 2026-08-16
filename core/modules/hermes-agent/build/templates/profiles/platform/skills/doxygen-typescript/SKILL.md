---
name: doxygen-typescript
description: TypeScript/JSDoc Semantic Markup Adaptation — JSDoc-style Doxygen contracts with region markers, GREP_SUMMARY, and LDD console.log adaptation
---

<!-- GREP_SUMMARY: doxygen, typescript, JSDoc, @modulecontract, @purpose, @invariants, @rationale, @io, @complexity, region, LDD, console.log, FUNC, METHOD, CLASS -->

# region MODULE_CONTRACT
## @purpose  SKILL: TypeScript/JSDoc Semantic Markup Adaptation — JSDoc-style Doxygen contracts with region markers, GREP_SUMMARY, and LDD console.log adaptation
## @scope    architect, coder, qa, docs
## @invariants
##   - @protected  true
##   - Тело скилла — 1:1 перенос framework-source 0.6.3
# endregion MODULE_CONTRACT

  ## TypeScript/JSDoc Semantic Markup Adaptation

  When working in TypeScript/JavaScript environments, adapt Doxygen-style markup to JSDoc:

  ### Module-level
  ```
  /** @modulecontract */
  /** @purpose — the GOAL of the module */
  /** @scope — functional areas covered */
  /** @invariants — conditions that always hold */
  /** @rationale — Q&A justification */
  // GREP_SUMMARY: comma-separated keywords
  // STRUCTURE: ▶ main → ○ step → ◇ decision → ⊕ result → ⎋ return
  #region MODULE_CONTRACT
  // ... module code ...
  #endregion
  ```

  ### Function-level
  ```
  /** @purpose — GOAL of the function */
  /** @uses — dependencies */
  /** @io — input → output types */
  /** @complexity — 1-10 scale */
  #region FUNC_functionName
  function functionName() { ... }
  #endregion
  ```

  ### Class-level
  ```
  /** @purpose — goal of the class */
  #region CLASS_ClassName
  class ClassName {
      #region METHOD_methodName
      /** @purpose — Method goal */
      methodName() { ... }
      #endregion
  }
  #endregion
  ```

  ### LDD adaptation for TypeScript
  Use `console.log` with `[IMP:X][FUNC][BLOCK]` format. IMP:9-10 for business logic.

<!-- ai-instructions:0.7.0 -->
