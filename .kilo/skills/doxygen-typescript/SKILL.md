---
description: TypeScript/JSDoc Semantic Markup Adaptation — JSDoc-style Doxygen contracts
  with region markers, GREP_SUMMARY, and LDD console.log adaptation
name: doxygen-typescript
---

# §SKILL
## TypeScript/JSDoc Semantic Markup Adaptation

  When working in TypeScript/JavaScript environments, adapt Doxygen-style markup to JSDoc:

  ### Module-level
  ```
  /** @modulecontract */
  /** @purpose — the GOAL of the module */
  /** @scope — functional areas covered */
  /** @invariants — conditions that always hold */
  /** @rationale — Q&amp;A justification */
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

<!-- ai-instructions:0.6.3 -->
