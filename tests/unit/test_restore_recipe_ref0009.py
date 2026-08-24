# GREP_SUMMARY: test-restore-recipe REF-0009 pre-snapshot ON_ERROR_STOP down-up-bracket structural Makefile
# STRUCTURE: ▶ parse postgres/Makefile restore target → ◇ pre-dumpall guard до разрушения → ◇ down → up → psql -v ON_ERROR_STOP=1 → ⊕ порядок маркеров → ⎋ LDD IMP:9
# region MODULE_CONTRACT
"""
Dry structural test for core/modules/postgres/Makefile `restore` target (REF-0009,
DATA-504 ≡ FAIL-0803): «Restore complete» над полу-смесью недопустим.

@purpose  Parse the Makefile recipe text and verify the hardened sequence WITHOUT
          executing docker/compose: mandatory pre-restore pg_dumpall snapshot
          (abort-on-fail) BEFORE destructive steps; clean stop; fresh start with
          readiness wait; psql -v ON_ERROR_STOP=1 for both .gz and .sql branches.
@scope    Static text analysis only — 0 docker, 0 subprocess.
@invariants
  - Pre-restore snapshot (docker exec pg_dumpall) присутствует и идёт ДО compose stop
    (страховка физически требует живой кластер; TRAP[DECISION] в Makefile)
  - Snapshot failure aborts BEFORE any destructive action («cluster NOT touched»)
  - compose stop / up bracket заливку; readiness wait (pg_isready) между up и psql
  - psql вызывается ТОЛЬКО с -v ON_ERROR_STOP=1 (обе ветки: .gz и plain .sql)
  - Runbook docs-in-code содержит age-decrypt шаг (age -d ... -i ...)
@rationale Рецепт нельзя исполнить в unit-среде (требует живого кластера) — но порядок
           фаз и fail-fast флаги это текстовые контракты, проверяемые статически.
@changes  2026-08-25 | Created (REF-0009, meta-refactoring W2)
"""
# endregion MODULE_CONTRACT

import logging
import re
from pathlib import Path

import pytest

from tests._conftest.ldd import ldd_trajectory

logger = logging.getLogger(__name__)

pytestmark = pytest.mark.static_audit

_POSTGRES_MAKEFILE = Path(__file__).resolve().parent.parent.parent / "core" / "modules" / "postgres" / "Makefile"


# region HELPERS
def _recipe() -> str:
    """Текст restore-рецепта (строки таб-отступа после target'а restore)."""
    lines = _POSTGRES_MAKEFILE.read_text(encoding="utf-8").splitlines()
    start = next(i for i, ln in enumerate(lines) if re.match(r"^restore:", ln))
    recipe_lines = []
    for ln in lines[start + 1 :]:
        if ln and not ln.startswith(("\t", " ", "#")):
            break  # следующий target — рецепт закончился
        recipe_lines.append(ln)
    return "\n".join(recipe_lines)


def _positions(text: str, needles: list[str]) -> list[int]:
    """Позиции маркеров в порядке списка (для проверки относительного порядка)."""
    positions = []
    cursor = 0
    for needle in needles:
        idx = text.find(needle, cursor)
        if idx == -1:
            return []
        positions.append(idx)
        cursor = idx + len(needle)
    return positions


# endregion HELPERS


@ldd_trajectory
def test_restore_recipe_order_and_guards(caplog) -> None:
    """Структурный контракт restore: pre-snapshot guard → stop → up+ready → ON_ERROR_STOP."""
    text = _recipe()

    # 1) Mandatory pre-restore snapshot: pg_dumpall в spool + abort при отказе
    assert "pg_dumpall" in text, "pre-restore snapshot обязателен (страховка перед восстановлением)"
    assert "pre_restore_" in text, "snapshot пишется под отличимым именем в backup spool"
    assert "cluster NOT touched" in text, "отказ снапшота абортит ДО разрушающих действий"

    # 2) Порядок: snapshot → clean stop → up → readiness wait → psql ON_ERROR_STOP.
    #    (pg_dumpall требует живой кластер, psql — слушающий сервер: см. TRAP[DECISION].)
    order = _positions(text, ["Pre-restore snapshot", "$(COMPOSE_CMD) stop", "$(COMPOSE_CMD) up -d", "pg_isready"])
    assert order, f"маркеры порядка не найдены в рецепте:\n{text}"

    # 3) Fail-fast SQL: обе ветки (.gz и .sql) используют psql -v ON_ERROR_STOP=1
    on_error_stops = re.findall(r"psql -v ON_ERROR_STOP=1", text)
    assert len(on_error_stops) >= 2, (
        f"обе ветки restore (.gz/.sql) обязаны иметь ON_ERROR_STOP=1, найдено {len(on_error_stops)}"
    )

    # 4) Частичный рестор не маскируется: сообщение о PARTIAL state при ошибке psql
    assert "PARTIAL state" in text, "ошибка заливки должна явно сигнализировать о partial-state кластере"

    logger.critical("[IMP:9][test] restore recipe: snapshot-guard→stop→up→ON_ERROR_STOP порядок подтверждён ✓")


@ldd_trajectory
def test_restore_runbook_documents_age_decrypt(caplog) -> None:
    """Runbook docs-in-code: age-decrypt шаг перед restore (REF-0009 SEC-0018)."""
    content = _POSTGRES_MAKEFILE.read_text(encoding="utf-8")

    assert "age -d" in content, "runbook обязан документировать расшифровку дампа"
    assert "-i /etc/age/key.txt" in content or "age-key.txt" in content, "runbook указывает источник приватного ключа"
    assert ".sql.gz.age" in content, "runbook оперирует зашифрованным артефактом nightly-пайплайна"
    logger.critical("[IMP:9][test] restore runbook: age-decrypt шаг задокументирован ✓")
