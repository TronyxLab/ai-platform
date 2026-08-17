---
name: python-engineering
description: Python Engineering Rules — modern Python 3.14+, Pyright strict, typed data models, correct async, explicit lifecycle, LDD-compliant structured logging
---

<!-- @protect: Coder produces Python that ignores language discipline — untyped public APIs, blocking I/O in async, implicit state, silent exception swallowing. -->

# region MODULE_CONTRACT
## @purpose  SKILL: Python Engineering Rules — modern Python 3.14+, Pyright strict, typed data models, correct async, explicit lifecycle, LDD-compliant structured logging
## @scope    architect, coder, qa
## @invariants
##   - @protected  true
##   - Тело скилла — 1:1 перенос framework-source 0.6.3
# endregion MODULE_CONTRACT

  ## Python Engineering Rules

  Language-level engineering rules for Python implementation. Load this skill when writing, planning, or reviewing Python code.

  ### Language & Tooling
  - Target **Python 3.14+**; use current language and library idioms (deferred annotations, `asyncio.TaskGroup`, `asyncio.timeout()`, `typing.Self`, `TypeAlias`).
  - Type all public APIs and cross-module boundaries. Keep **Pyright strict**. Avoid `Any` and untyped escape hatches unless justified with `## @rationale`.

  ### Data & State
  - Prefer explicit data models — `dataclass` (prefer `frozen=True`), `TypedDict`, `Protocol` — over unstructured `dict[str, Any]`.
  - Treat mutability and object ownership explicitly. Avoid shared mutable state and mutable default arguments.
  - Avoid hidden global singletons, module-level initialization and implicit runtime state. Module-level state must be explicit and immutable (e.g., logger, constants).

  ### Imports & Startup
  - Keep imports side-effect free. Application startup, resource acquisition and background workers must be explicit — see arch-patterns (Lazy Import).

  ### Async
  - Keep async boundaries correct: never perform blocking I/O or `time.sleep()` in async code; do not add `async` without an I/O/concurrency reason.
  - Prefer modern primitives: `asyncio.TaskGroup` for task management, `asyncio.timeout()` for deadlines.

  ### Resources & Lifecycle
  - Make resource lifecycle explicit: DB sessions, HTTP clients, files, subprocesses, tasks and connections must have clear ownership and cleanup. Prefer context managers (`with`), `contextlib.ExitStack`, or try-finally.

  ### Errors
  - Preserve exception semantics. Never silently swallow exceptions; avoid broad `except Exception` unless logging + re-raising or deliberately handling a defined boundary (§CONSTITUTION: all errors visible).

  ### Architecture
  - Keep domain/application code independent from framework and infrastructure details; prefer composition and protocols over unnecessary inheritance and class abstractions — see arch-patterns (Onion, AI-Friendly Contracts, Small Simple Blocks).
  - Do not introduce abstractions without a concrete caller or use case.

  ### Streams & Data
  - Prefer generators/iterators for potentially large streams; do not materialize data unnecessarily.
  - Use timezone-aware datetimes; store UTC, convert at the presentation boundary.

  ### Logging
  - Use structured logging via `logging` (`logging.getLogger(__name__)`) with `[IMP:1-10]` LDD markers in every non-trivial function. `print()` is not application logging — reserved for CLI user-facing output and LDD trajectory in tests (§TESTING).

  ### System Interaction
  - Use `pathlib`, `subprocess` and standard-library facilities appropriately instead of ad-hoc shell/string manipulation.

  ### Tests
  - Tests must verify behavior and boundaries, not implementation details. Mock dependencies at their point of use (§TESTING: DI > mocks).

  ### Change Discipline
  - Before changing async, concurrency, lifecycle, imports or dependency boundaries, inspect the existing execution model.

<!-- ai-instructions:0.7.0 -->
