# 01-architecture · Архитектурный forensic audit (pre-launch)

Дата: 2026-08-22 · Режим: READ-ONLY аудит, код не исправляется. Commit: 4425ce0.

## Scope
- Объект: репозиторий ai-platform (`core/internal/*` ~30 доменов, `core/modules/*` 15 модулей, `core/entrypoints`, `core/lib/*.sh`, Makefiles). Тесты (`tests/`) — вне первичного фокуса.
- Принцип: **maximum production risk reduction / minimum code churn**. Неделя до production launch — больших переписываний не предлагать.

## Метод
10 параллельных субагентов-форензиков, каждый по одному направлению; часть агентов делегировала вложенные параллельные проходы — их сырые отчёты сохранены в `attic/` (22 файла) и консолидированы в канонические `findings-001..010.md`. Только доказательные находки (evidence = конкретный файл/символ/строка/цитата). Неподтверждённые наблюдения помечены `HYPOTHESIS` и не считаются фактами.

## Направления → канонические файлы
1. module/package boundaries → [findings-001.md](findings-001.md)
2. dependency direction → [findings-002.md](findings-002.md)
3. circular dependencies → [findings-003.md](findings-003.md)
4. God modules/classes → [findings-004.md](findings-004.md)
5. hidden global state → [findings-005.md](findings-005.md)
6. infra/app/domain coupling → [findings-006.md](findings-006.md)
7. duplicated business logic → [findings-007.md](findings-007.md)
8. initialization/lifecycle architecture → [findings-008.md](findings-008.md)
9. abstractions/overengineering → [findings-009.md](findings-009.md)
10. architectural hotspots → [findings-010.md](findings-010.md)

## Легенда
- Severity: CRITICAL / HIGH / MEDIUM / LOW (по production-риску)
- Confidence: %; `HYPOTHESIS` = только предположение
- Churn: S <50 строк / M 50–300 / L >300
- WHEN: pre-launch / post-launch

## Идентификаторы
`ARCH-XXXX` сквозные, блок на направление: направление N → ARCH-N\*00–N\*99 (ARCH-0100–0199, …, ARCH-0900–0999, ARCH-1000–1099). Сырые проходы в `attic/` используют собственную нумерацию (ARCH-0NN, ARCH-N00 и пр.) — при цитировании сверяться с каноническим файлом направления.

## Индекс файлов
- `findings-001.md` … `findings-010.md` — канонические находки по направлениям (консолидированные, перекрёстные ссылки проставлены)
- `attic/` — сырые параллельные проходы вложенных субагентов (исходный материал, вне индекса находок)
- [summary.md](summary.md) — TOP-10 архитектурных рисков
