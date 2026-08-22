# 05-tests — Adversarial Test Audit

**Дата:** 2026-08-22 · **Метод:** test-auditor (adversarial, статический) · **Тесты не переписываются**

## Scope

Аудит тестовой системы `ai-platform`: 522 тестовых файла, ~176k LOC (355 unit + 134 gate +
integration/e2e/contracts). Объём тестов НЕ считается признаком качества. Цель — определить,
насколько тесты реально защищают production behavior, и найти тесты, создающие ложную уверенность.

## Направления (10 параллельных агентов)

| # | Файл | Диапазон ID |
|---|------|-------------|
| 1 | findings-critical-paths.md — критические пути | TEST-001..009 |
| 2 | findings-missing-coverage.md — отсутствующее покрытие | TEST-010..019 |
| 3 | findings-weak-assertions.md — слабые assertions | TEST-020..029 |
| 4 | findings-over-mocking.md — over-mocking | TEST-030..039 |
| 5 | findings-integration-gaps.md — интеграционные разрывы | TEST-040..049 |
| 6 | findings-failure-paths.md — failure paths | TEST-050..059 |
| 7 | findings-concurrency-recovery.md — concurrency/recovery | TEST-060..069 |
| 8 | findings-flaky-tests.md — flaky tests | TEST-070..079 |
| 9 | findings-duplicate-dead-tests.md — дубли/мёртвые тесты | TEST-080..089 |
| 10 | findings-false-confidence.md — ложная уверенность | TEST-090..099 |

## Формат находки

`TEST-XXXX`: Test · Production code · Claimed guarantee · Actual guarantee · Blind spot ·
Possible production bug · Recommended test · Existing test to remove/merge · Confidence.

## Итог

- TOP-риски: [summary.md](summary.md)
- Критические слепые зоны: [critical-blind-spots.md](critical-blind-spots.md)
