---
name: react-engineering
description: React Engineering Rules — function components and hooks, local and derived state, synchronization effects, stable identity, race-safe async, typed boundaries
---

<!-- @protect: Coder produces React that ignores React discipline — legacy class components, duplicated server state in local state, speculative memoization, uncontrolled side effects during render, untyped API data propagating through the component tree. -->

# region MODULE_CONTRACT
## @purpose  SKILL: React Engineering Rules — function components and hooks, local and derived state, synchronization effects, stable identity, race-safe async, typed boundaries
## @scope    architect, coder, qa
## @invariants
##   - @protected  true
##   - Тело скилла — 1:1 перенос framework-source 0.6.3
# endregion MODULE_CONTRACT

  ## React Engineering Rules

  Language-level engineering rules for React implementation. Load this skill when writing, planning, or reviewing React code.

  ### Components & Composition
  - Use modern React patterns; prefer function components and hooks. Do not introduce legacy class components.
  - Keep components focused on rendering and UI composition; move business logic, data access and complex state transitions into dedicated modules/hooks.
  - Prefer composition over deeply configurable "god components" with dozens of boolean props.
  - Keep UI primitives separate from domain components; do not couple reusable components to business entities unnecessarily.
  - When changing shared components, inspect all consumers before changing their contract.
  - Keep accessibility semantics in the component itself; do not defer basic keyboard, focus and ARIA behavior to consumers.

  ### State & Data Flow
  - Keep server state separate from UI state. Do not duplicate, mirror or cache server data in local component state without a concrete reason.
  - Keep state as local as possible. Do not lift state or introduce global state unless multiple independent consumers actually require it.
  - Prefer derived values over duplicated state. Do not store values that can be deterministically computed from existing props/state.
  - Do not duplicate business rules between frontend components. Establish a single source of truth.
  - Treat loading, error, empty, cancellation and stale-data states as part of the component contract.
  - Do not introduce a new state-management library, abstraction layer or component pattern when existing project primitives solve the problem.

  ### Effects & Synchronization
  - Treat `useEffect` as a synchronization boundary, not as a general-purpose lifecycle or data-flow mechanism. Do not use effects for values that can be derived during render.
  - Every effect must have an explicit external dependency it synchronizes with and a correct cleanup strategy where applicable.
  - Do not fix React warnings by suppressing them; fix the underlying identity, dependency or lifecycle problem. When a suppression is unavoidable, document the invariant that makes it safe.

  ### Identity & Performance
  - Preserve stable identity intentionally. Use `key` from stable domain identity; never use array indexes when item identity can change.
  - Do not use `useMemo`, `useCallback` or `React.memo` speculatively. Add memoization only when it prevents a demonstrated cost or preserves a required identity contract.

  ### Async & Purity
  - Keep async operations cancellable and race-safe. Prevent stale responses from overwriting newer state.
  - Do not perform uncontrolled side effects during render. Rendering must remain pure.

  ### Types & Data Boundaries
  - Validate external data at the boundary; do not let untyped API/JSON data propagate through the component tree.
  - Prefer explicit TypeScript types and discriminated unions over `any`, type assertions and optional-property soup.
  - Do not hide uncertainty with `any`, casts, long optional-chaining chains or fallback UI; validate at the boundary or surface the uncertainty explicitly.
  - Keep API/client code outside presentation components. Components should consume typed application interfaces rather than construct HTTP requests directly.

  ### Props & Context
  - Keep component props narrow and intentional. Do not pass large application objects when a component needs only a few fields.
  - Avoid prop drilling by default, but do not introduce Context/global state merely to eliminate a small amount of prop passing.
  - Keep Context low-frequency and dependency-oriented; do not use it as a general state-management mechanism.
  - Avoid hidden global mutable state, module-level side effects and singleton stores unless their lifecycle and ownership are explicit.

  ### Change Discipline
  - Before modifying a component, trace its data flow, state ownership and side effects; do not reason from the component file alone.

<!-- ai-instructions:0.7.1 -->
