# GREP_SUMMARY: quarantine, registry, flaky, docker, network, pytest_collection_modifyitems, skip, Rev-date, debt-ref, W6
# STRUCTURE: ▶ QUARANTINE registry → ◇ validate (Rev-дата обязательна) → ◇ apply (docker/network items → pytest.skip) → ⎋ skip-reason
# region MODULE_CONTRACT
## @purpose  Quarantine-протокол (DevPlan 160 W6 T6.3): реестр временного карантина флак
##           docker/сетевых тестов + pytest_collection_modifyitems-hook, который для nodeid
##           из реестра (с docker/network-маркерами) делает pytest.skip с диагностическим
##           reason «[QUARANTINE] ... Rev: <until> (Debt: <debt_ref>)».
## @scope    Только docker/сетевые слои (маркеры requires_docker/smoke/component/integration).
##           Детерминированные слои (static/unit/gates) карантину НЕ подлежат — «флак = баг».
## @invariants
##   - QUARANTINE пуст по умолчанию (реестр заполняется при флаке — политика tests/AGENTS.md)
##   - Запись БЕЗ until (Rev-даты) → validate_quarantine() возвращает ошибку, хук РАISE (RED)
##   - Skip применяется ТОЛЬКО к item'ам с docker/network-маркерами — детерминированные
##     тесты с совпадающим nodeid НЕ карантинятся (защита от случайного карантина static/unit/gates)
##   - Reason: "[QUARANTINE] {nodeid} — {reason} — Rev: {until} (Debt: {debt_ref})"
##   - Хук «конфигурируемый»: вызывается ИЗ tests/conftest.py (1 строка), сам себя НЕ регистрирует
## @rationale DevPlan 160 W6 T6.3: флак docker-слоя ≠ регрессия кода. Карантин — временная
##            мера с обязательной Rev-датой и Debt-артефактом (root-причина расследуется).
##            Детерминированные слои карантинить НЕЛЬЗЯ — это маскировка бага (R4-дух).
## @changes  2026-08-13 | DevPlan 160 W6 T6.3 — Created
## @modulemap
##   QUARANTINE [W:1] — реестр nodeid → {until, reason, debt_ref} (пуст по умолчанию)
##   validate_quarantine [W:3] — ошибки записей без Rev-даты/с невалидной датой
##   _is_quarantinable [W:2] — item с docker/network-маркером (только такие карантинятся)
##   _skip_reason [W:2] — формат reason-строки (машиночитаемый для QA-агента)
##   pytest_collection_modifyitems [W:4] — validate (RED) + apply skip (docker/network)
## @usecases
##   - Флак smoke/component/integration теста → запись в реестр + Debt-артефакт с Rev-датой
##   - QA-агент видит reason «[QUARANTINE]» в skip-отчёте → понимает: флак, срок, долг
# endregion MODULE_CONTRACT

from __future__ import annotations

import logging
import re
from typing import Any

import pytest

logger = logging.getLogger(__name__)

# ── Реестр карантина (пуст по умолчанию — заполняется при флаке, DevPlan 160 W6 T6.3) ──
# Формат записи:
#   "tests/test_foo.py::test_bar": {
#       "until": "2026-09-01",     # Rev-дата пересмотра (ОБЯЗАТЕЛЬНА — иначе RED)
#       "reason": "flaky under load (langfuse ...)",  # краткое описание флака
#       "debt_ref": "160-test-architecture-revamp/02-DevPlan.md T1.3",  # Debt-артефакт/ссылка
#   }
QUARANTINE: dict[str, dict[str, str]] = {}

# Только docker/сетевые маркеры подлежат карантину. Детерминированные слои
# (static/unit/gates) НЕ входят — «флак = баг», карантин для них запрещён.
_QUARANTINABLE_MARKERS: tuple[str, ...] = ("requires_docker", "smoke", "component", "integration")

# Rev-дата: ISO YYYY-MM-DD
_DATE_RE: re.Pattern = re.compile(r"^\d{4}-\d{2}-\d{2}$")


def _is_quarantinable(item: Any) -> bool:
    """True если item несёт docker/network-маркер (только такие можно карантинить).

    ▶ ○ item.get_closest_marker(name) ∋ _QUARANTINABLE_MARKERS → ⎋ bool

    ## @purpose — Фильтр «кто может быть отправлен в карантин»: исключительно
    ##            docker/сетевые слои. Детерминированные тесты (static/unit/gates) —
    ##            даже с совпадающим nodeid — НЕ карантинятся (защита от маскировки бага).
    ## @io — ⇥ item: pytest.Item (или stub с get_closest_marker) → ⎋ bool
    ## @complexity — O(M) где M = _QUARANTINABLE_MARKERS
    """
    return any(item.get_closest_marker(m) for m in _QUARANTINABLE_MARKERS)


def _skip_reason(nodeid: str, entry: dict[str, str]) -> str:
    """Формат skip-reason для QA-агента и человека.

    ▶ ┌nodeid + entry┐ → ⊕ "[QUARANTINE] ... — Rev: until (Debt: debt_ref)" → ⎋ str

    ## @purpose — Единый диагностический формат: nodeid, причина флака, Rev-дата,
    ##            ссылка на Debt-артефакт. QA-агент видит «[QUARANTINE]» и понимает:
    ##            временный карантин флака, НЕ регрессия.
    ## @io — ⇥ nodeid: str, entry: {until, reason, debt_ref} → ⎋ str
    ## @complexity — O(1)
    """
    return (
        f"[QUARANTINE] {nodeid} — {entry.get('reason', 'flaky')} — "
        f"Rev: {entry.get('until', 'MISSING')} (Debt: {entry.get('debt_ref', 'N/A')})"
    )


def validate_quarantine() -> list[str]:
    """Валидация реестра: Rev-дата обязательна. Возвращает список ошибок (пусто = ОК).

    ▶ ┌QUARANTINE┐ → ○ entry: ◇ until отсутствует/невалидна → ⊕ errors → ⎋ list[str]

    ## @purpose — «Запись без Rev-даты = RED» (DevPlan 160 W6 T6.3): карантин —
    ##            временная мера, каждая запись ОБЯЗАНА иметь until (Rev-дату пересмотра).
    ##            Хук вызывает валидацию при сборе коллекции → невалидная запись
    ##            роняет сессию (RED), а не молча живёт.
    ## @io — ⎋ list[str] (пусто = реестр валиден)
    ## @complexity — O(N) где N = записей реестра
    ## @invariants
    ##   - until обязателен и формата YYYY-MM-DD
    ##   - reason/debt_ref рекомендуются, но не обязательны (until — единственный жёсткий ключ)
    """
    errors: list[str] = []
    for nodeid, entry in sorted(QUARANTINE.items()):
        until = entry.get("until", "")
        if not until:
            errors.append(f"{nodeid}: MISSING until (Rev-дата обязательна — запись без Rev-даты = RED)")
        elif not _DATE_RE.match(until):
            errors.append(f"{nodeid}: invalid until={until!r} (формат YYYY-MM-DD)")
    return errors


def pytest_collection_modifyitems(items: list[Any]) -> int:
    """Collection-hook (конфигурируемый): валидация реестра (RED) + применение skip.

    ▶ ┌items┐ → ◇ validate_quarantine → ⚡ RuntimeError при ошибках → ○ item ∈ items:
    ◇ nodeid ∈ QUARANTINE AND _is_quarantinable → ⊕ pytest.mark.skip(reason) → ⎋ skipped_count

    ## @purpose — Применение карантина на этапе сбора: nodeid из реестра с docker/network-
    ##            маркером получает pytest.skip (тест не исполняется); валидность реестра
    ##            проверяется ПЕРЕД применением (невалидная запись = RuntimeError → RED).
    ## @io — ⇥ items: list[pytest.Item] → ⎋ int (число карантинированных тестов)
    ## @complexity — O(I × M) где I = items, M = маркеры
    ## @invariants
    ##   - Невалидный реестр (запись без Rev-даты) → RuntimeError (fail-loud, НЕ тихий skip)
    ##   - Skip только для docker/network-маркеров (детерминированные слои защищены)
    ##   - Пустой реестр = no-op (0)
    ## @rationale Хук НЕ регистрируется сам (не импортируется в conftest-неймспейс под
    ##            pytest_-префиксом) — включается явным вызовом из tests/conftest.py
    ##            (1 строка), т.е. «конфигурируемый» (DevPlan 160 W6 T6.3).
    """
    errors = validate_quarantine()
    if errors:
        raise RuntimeError(
            "[QUARANTINE] Невалидный реестр карантина (запись без Rev-даты = RED):\n" + "\n".join(errors)
        )

    skipped = 0
    for item in items:
        nodeid = getattr(item, "nodeid", "")
        if nodeid in QUARANTINE and _is_quarantinable(item):
            entry = QUARANTINE[nodeid]
            item.add_marker(pytest.mark.skip(reason=_skip_reason(nodeid, entry)))
            logger.info("[IMP:8][quarantine][skip] %s (Rev: %s)", nodeid, entry.get("until"))
            skipped += 1
    if skipped:
        logger.warning(
            "[IMP:7][quarantine][summary] %d docker/network test(s) quarantined — Rev-даты: %s",
            skipped,
            ", ".join(sorted(QUARANTINE[n].get("until", "MISSING") for n in QUARANTINE)),
        )
    return skipped
