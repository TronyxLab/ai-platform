# 04-data-consistency · Data & State Integrity Forensic Audit

```text
$ARTIFACT_CONTRACT
PURPOSE: forensic audit целостности данных и состояния — доказуемые нарушения инвариантов
ACCEPTANCE_CRITERIA: каждый серьёзный finding имеет восстановленный сценарий START STATE → op → failure → END STATE
IMPLEMENTS: pre-launch аудит, целостность данных/состояния (meta-refactoring wave 04)
IMPACTS: none — анализ только, код НЕ исправляется
REQUIRES: core/ production tree (deploy/bootstrap/lifecycle/state/secrets), shared-модули
METHOD: до 10 параллельных research-субагентов + синтез summary.md
STATUS: wave 2 запущена
```

## Scope

Исследуются: transaction boundaries, atomicity, consistency, idempotency, duplicate writes,
lost updates, stale reads, cache consistency, Redis/PostgreSQL interaction, retry, rollback,
partial completion, crash recovery, state machines, migrations, serialization, concurrent updates.

Группировка по субагентам:

| # | Фокус | Файл находок | ID-диапазон |
|---|-------|--------------|-------------|
| 1 | transaction boundaries & atomicity файловых/мульти-артефактных записей | `findings-01-transactions-atomicity.md` | DATA-101… |
| 2 | idempotency & duplicate writes (bootstrap/deploy/hooks/provisioning re-run) | `findings-02-idempotency.md` | DATA-201… |
| 3 | lost updates & concurrent updates (file locks, parallel deploys, read-modify-write) | `findings-03-concurrency-lost-updates.md` | DATA-301… |
| 4 | stale reads & cache consistency (generated manifests, fingerprint cache, .env.platform) | `findings-04-stale-cache.md` | DATA-401… |
| 5 | Redis/PostgreSQL interaction (litellm PG, project DB hooks, backup/restore consistency) | `findings-05-db-redis.md` | DATA-501… |
| 6 | retry & rollback semantics (healthcheck rollback, partial compose, non-idempotent retries) | `findings-06-retry-rollback.md` | DATA-601… |
| 7 | crash recovery & partial completion (interrupted deploy/bootstrap/cert issuance) | `findings-07-crash-recovery.md` | DATA-701… |
| 8 | state machines integrity (bootstrap lifecycle transitions, persisted state drift) | `findings-08-state-machines.md` | DATA-801… |
| 9 | migrations & serialization (versioned generated files, locks, yaml/json edge cases) | `findings-09-migrations-serialization.md` | DATA-901… |
| 10 | secrets/state integrity (AGE round-trip, rotation atomicity, permissions) | `findings-10-secrets-state.md` | DATA-1001… |

Итоговая синтеза: `summary.md` (TOP-10 рисков целостности данных).

## Формат находки

```markdown
## DATA-N0X: <title>
- **Severity:** CRITICAL|HIGH|MEDIUM|LOW · **Confidence:** HIGH|MEDIUM|LOW
- **Files:** path:L10-L50
- **Symbols:** `func`, `Class`
- **Invariant:** нарушаемый инвариант (одно предложение)
- **Violating scenario:**
  - START STATE: …
  - → operation
  - → failure/concurrency/retry
  - END STATE: …
  Почему END STATE некорректен: …
- **Evidence:** проверенная цитата (≤3 строки)
- **Impact:** …
- **Minimal fix:** …
- **Required test:** какой тест ловит регрессию
- **Phase:** Pre-launch | Post-launch
```

Для MEDIUM/LOW допускается сокращённый сценарий одной строкой.

## Правила доказательности

Только доказуемые находки: Files с file:line; Evidence воспроизводим; спекуляция = Confidence ≤ LOW и явная пометка гипотезы. Не исправлять код.
