# 02-production-bugs · Forensic-поиск скрытых production bugs

Дата: 2026-08-22 · Режим: READ-ONLY аудит, код не исправляется. Commit: 4425ce0.

## Scope
- Объект: репозиторий ai-platform (`core/internal/*`, `core/entrypoints`, `core/lib`, `core/modules/*`, makefiles). Искались **реальные дефекты** (swallowed exceptions, race conditions, resource leaks, non-idempotent retry, broken cancellation, failures после restart, partial dependency failure) — не code smells.
- Каждый finding содержит конкретный execution path: `input → A → B → C → failure` (реальные символы, file:line hops). Недоказуемое статически помечено `HYPOTHESIS`, confidence <60%.

## Метод
10 параллельных субагентов, каждый по одному направлению. Evidence = файл:строка + символ + цитата. Направления 7 и 9 пережили транспортные сбои сабагент-сессий — перезапущены заново, результат полный.

## Направления → файлы
1. error handling → [findings-001.md](findings-001.md)
2. retry/timeout → [findings-002.md](findings-002.md)
3. async/concurrency → [findings-003.md](findings-003.md)
4. resource lifecycle → [findings-004.md](findings-004.md)
5. state transitions → [findings-005.md](findings-005.md)
6. partial failures → [findings-006.md](findings-006.md)
7. restart behavior → [findings-007.md](findings-007.md)
8. background jobs → [findings-008.md](findings-008.md)
9. external dependencies → [findings-009.md](findings-009.md)
10. edge cases → [findings-010.md](findings-010.md)

## Формат находки

`BUG-XXXX`: Severity · Confidence · File · Symbol · Trigger · Execution path · Actual behavior · Expected behavior · Impact · Minimal fix · Required regression test.

## Легенда
- Severity: CRITICAL (data loss / security / outage) / HIGH / MEDIUM / LOW
- Confidence: %; `HYPOTHESIS` = недоказано, только предположение

## Идентификаторы
`BUG-XXXX` сквозные, блок на направление: направление N → BUG-N000–N099 (BUG-0100–0199 … BUG-1000–1099).

## Итог
- ~57 находок; CRITICAL ×3, HIGH ×17 (см. [summary.md](summary.md))
- TOP-риски: [summary.md](summary.md)
