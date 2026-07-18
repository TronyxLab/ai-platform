# $START_DEBT
# 011-Debt.md — Debt Register: RC-1 Cleanup Reconciliation (Updated 2026-07-18)

<!-- GREP_SUMMARY: debt-register, stale-tests, template-python, platform-test, nightly-gate, rc1-cleanup -->
<!-- STRUCTURE: ┌RC-1 audit findings┐ → ◇ DBT items (validated) → ⊕ CLOSED (3) + OPEN (4) → ∑ status per item → ⎋ next-steps

## $ARTIFACT_CONTRACT
| Field | Value |
|-------|-------|
| PURPOSE | Register deferred debt items from RC-1 cleanup audit: validated DBT-001–006, context overlay model gap |
| DESCRIPTION | Validation of 6 debt items against current codebase; 3 closed with evidence, 3+1 kept open |
| RATIONALE | RC-1 cleanup: каждый DBT проверен на актуальность, закрытые задокументированы |
| IMPLEMENTS | RC-1 cleanup Phase 3 — knowledge consolidation |
| REQUIRES | --- |

---

## Debt Items

### DBT-001: Template Python files — TRAP[DECISION] отсутствуют
**Status: CLOSED ✅ (2026-07-18)**
- **Где:** `core/templates/template-*/**/*.py`
- **Проблема:** DevPlan T8 указывает 2 template Python файла для TRAP[DECISION], но `core/templates/` не содержит `.py` файлов.
- **Валидация:** `find . -path '*template*' -name '*.py' -not -path '*.venv/*'` → **0 файлов**. `core/templates/` содержит только: `template-manifest.yaml`, `module.mk`, `.dockerignore`, `sudo-whitelist.template`, `module-system.mk`. Шаблонные .py файлы есть в `templates/template-*/src/` (project templates) — они не являются частью core/templates/.
- **Решение:** Проблемных файлов не существует. TRAP[DECISION] не требуется. Пункт закрыт.
- **Severity:** LO (CLOSED)

### DBT-002: Stale smoke tests for refactored modules
**Status: OPEN ⚠️**
- **Где:** `tests/` — smoke-тесты модулей, рефакторенных в Wave A (010) и Wave B (011)
- **Проблема:** Ряд smoke-тестов могут проверять устаревшие контракты.
- **Обновление (2026-07-18):** `tests/smoke/` и `tests/component/` директории не существуют (flattened). 12 test_smoke_*.py и 2 test_component_*.py лежат в корне tests/ напрямую. Все модули (platform-secrets, monitoring, nginx) имеют статические тесты. Требуется ревизия: какие smoke-тесты действительно проверяют устаревшие контракты.
- **Рекомендация:** Добавить `@pytest.mark.stale` или удалить неактуальные тесты.
- **Severity:** MED

### DBT-003: platform-test.yml — hardcoded compose files устарели
**Status: CLOSED ✅ (2026-07-18)**
- **Где:** `.github/workflows/platform-test.yml`
- **Проблема:** При добавлении нового модуля без module.yaml список не обновится.
- **Валидация:** Gate-тест `test_include_matches_discovered_modules` существует в `tests/gates/test_gate_compose_include_sync.py` (line 47). Зарегистрирован в `test_inventory.yaml`. Функция `test_include_matches_discovered_modules` определена и проверяет расхождение между `make discover-modules` и docker-compose includes.
- **Решение:** Gate-тест существует и активен. Пункт закрыт.
- **Severity:** LO (CLOSED)

### DBT-004: .env.example stale comment section
**Status: CLOSED ✅ (2026-07-18)**
- **Где:** `.env.example` — секция Hermes Dashboard (~line 125-138)
- **Проблема:** Старый комментарий про BASIC_AUTH_*.
- **Валидация:** `grep BASIC_AUTH .env.example` → lines 131-132 содержат корректную документацию: контейнерные BASIC_AUTH_USERNAME / BASIC_AUTH_PASSWORD, HERMES_DASHBOARD_BASIC_AUTH_* помечены как DELETED. Артефактов не найдено.
- **Решение:** Комментарий корректен, артефактов нет. Пункт закрыт.
- **Severity:** LO (CLOSED)

### DBT-005: monitoring entrypoint.sh (host-side) — deferred
**Status: OPEN ⏳ (DEFERRED)**
- **Где:** `core/modules/monitoring/entrypoint.sh`
- **Проблема:** Host-side entrypoint для envsubst вне compose не создан. Init-контейнер в docker-compose.base.yml покрывает только docker compose use case.
- **Обновление (2026-07-18):** Файл по-прежнему не существует. Для make/CI use cases без compose требуется отдельный entrypoint.
- **Рекомендация:** Создать host-side entrypoint.sh при следующем изменении monitoring модуля.
- **Severity:** LO

### DBT-006: Nightly gate — stage numbering drift
**Status: OPEN ⏳ (DEFERRED)**
- **Где:** `.github/workflows/nightly-gate.yml`
- **Проблема:** Stage numbering сбита: Stage 4 отсутствует, Stage 4b (generate module list) вставлен между Stage 2 и Stage 5, Stage 5 и Stage 7 дублируются.
- **Обновление (2026-07-18):** Подтверждено: Stage 4 пуст, Stage 4b после Stage 5, Stage 5 (Alert) → должно быть Stage 4, Stage 5 (Cleanup) → должно быть Stage 6, Stage 7 (Summary) → должно быть Stage 7. Нумерация сбита в 4 местах.
- **Рекомендация:** Привести к Stage 1→2→3→4→5→6→7 при следующем редактировании nightly-gate.yml.
- **Severity:** LO

### DBT-007: Context-overlay model — shared repo with mirror target
**Status: OPEN ⚠️ (NEW)**
- **Где:** `tronyx-lab/platform` ↔ `TronyxLab/ai-platform` ↔ mirror.yml
- **Проблема:** Контекстный оверлей (`~/projects/tronyx-lab/platform`) использует origin remote `https://github.com/TronyxLab/AI-platform.git` — тот же репозиторий, куда mirror.yml из source пушит source main. Следующий push из source (после `make context-promote`) будет не-fast-forward и либо перезатрёт контекстные изменения, либо упадёт с ошибкой.
- **Обоснование:** AGENTS.md декларирует модель «TronyxLab/ai-platform (read-only)», но контекстный оверлей пишет в этот же репозиторий. Нужен отдельный репозиторий для контекстного оверлея (например, `tronyx-lab/platform`), а mirror.yml должен пушить в TronyxLab/ai-platform (read-only mirror без overlay-коммитов).
- **Рекомендация:**
  1. Создать отдельный репозиторий `tronyx-lab/platform` на GitHub
  2. Перенести туда context-specific коммиты
  3. Обновить origin remote в `~/projects/tronyx-lab/platform`
  4. Вернуть TronyxLab/ai-platform в read-only зеркало
- **Severity:** MED

---

## Summary (2026-07-18)

| DBT | Status | Severity | Description |
|-----|--------|----------|-------------|
| DBT-001 | ✅ CLOSED | LO | Template .py files — не существуют |
| DBT-002 | ⚠️ OPEN | MED | Stale smoke tests — требуется ревизия |
| DBT-003 | ✅ CLOSED | LO | Gate-тест существует и активен |
| DBT-004 | ✅ CLOSED | LO | .env.example корректен |
| DBT-005 | ⏳ OPEN | LO | monitoring entrypoint.sh — deferred |
| DBT-006 | ⏳ OPEN | LO | nightly-gate stage numbering — deferred |
| DBT-007 | ⚠️ OPEN | MED | Context-overlay repo model conflict |

**Next Steps:**
1. DBT-002: ревизия smoke-тестов (проверить актуальность контрактов)
2. DBT-007: создать отдельный репозиторий context-overlay
3. DBT-005/006: при следующем изменении соответствующих файлов

# $END_DEBT
