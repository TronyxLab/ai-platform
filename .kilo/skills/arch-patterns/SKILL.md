---
description: AI-First Architectural Principles — lightweight DDD, simplified onion
  layers, modular boundaries, typed public contracts, plugin API, lazy import, interrupt
  handling
name: arch-patterns
---

# §SKILL
## AI-First Architectural Principles

  ### Principle 1: Lightweight DDD
  Architecture is organized around the business domain, not technology. Each module
  mirrors a business entity and contains only logic belonging to it. Use only the DDD
  patterns that are genuinely needed — no ceremonial abstractions.

  ### Principle 2: Simplified Onion Architecture
  Each module is split into three layers:
  - **Domain** — business rules and models. No imports from Application or Infrastructure.
  - **Application** — use cases and orchestration. Imports Domain only.
  - **Infrastructure** — DB, APIs, queues, LLM, external services. Implements contracts
    defined by inner layers.
  Dependencies point inward only. Infrastructure changes must never force Domain changes.

  ### Principle 3: Modular Architecture
  The system is a set of independent modules that can be developed, tested, refactored,
  and replaced in isolation. Inter-module interaction only through public contracts.
  One module = one business responsibility — split files instead of growing them.

  ### Principle 4: AI-Friendly Contracts
  All public interfaces are explicit, typed, and documented (Protocol / Interface / API
  schema). Modules depend on contracts, never on internals. A contract change is an
  architectural decision — record it with `## @rationale`.

  ### Applied Patterns
  - **Plugin API:** backend modules expose a clear entry point (e.g., `run()`); agents
    and tests interact via direct function imports. CLI only if strictly required.
  - **Lazy Import:** import heavy libraries inside functions, not at module level.
  - **Interrupt Handling:** wrap server start in try-except `KeyboardInterrupt`;
    log to both file and stdout.

  Note: testing patterns (DI > mocks, headless UI) live in §TESTING — single source of truth.

<!-- ai-instructions:0.5.16 -->
