# GREP_SUMMARY: conftest, gates, anti-loop, pytest, counter, re-export, xdist-worker, master-guard, unified-counter
# STRUCTURE: ┌тонкий ре-экспорт _conftest.counter┐ → ⎋ (session-хуки — root conftest _conftest/session.py)
# region MODULE_CONTRACT
## @purpose  Тонкий ре-экспорт Anti-Loop counter для gate-тестов (DevPlan 136 W12 T12.1, T-1/T-2).
##           УНИФИКАЦИЯ: один counter-модуль (_conftest/counter.py), один путь файла
##           (tests/.test_counter.json), один ключ ("attempts"). Собственный counter-модуль,
##           собственный .test_counter.json (tests/gates/) и собственные session-хуки УДАЛЕНЫ —
##           dual counter (T-1) расщеплял anti-loop состояние, а reset поднабором (T-2) стирал
##           evidence фейла полного прогона.
## @scope    Конфигурация pytest для tests/gates/; counter-функции re-экспортируются для обратной
##           совместимости. Session-хуки НЕ регистрируются здесь — root tests/conftest.py
##           (через _conftest/session.py) загружается для ЛЮБОГО прогона под tests/ (включая
##           tests/gates/), его pytest_sessionstart/finish покрывают gates-сессии с master-гейтом.
## @invariants
##   - НЕТ собственных pytest_sessionstart/pytest_sessionfinish — иначе двойная регистрация
##     хуков с root conftest (двойной increment/reset за прогон)
##   - НЕТ собственного counter-файла/ключа — единственный файл tests/.test_counter.json
##   - Reset счётчика — только при 100% PASS ПОЛНОЙ сессии (_is_full_session в session.py),
##     не при 100% PASS поднабора (gates-прогон — всегда поднабор: -m "gate ...")
##   - Master-guard (DevPlan 124 T4): gates-чеки исполняются с -n auto (check-suite gates
##     xdist: true) — increment/reset выполняет master root-conftest (PYTEST_XDIST_WORKER гейт)
## @rationale DevPlan 136 W12 T12.1: унификация counter устраняет T-1 (dual counter) и T-2
##            (reset поднабором); ре-экспорт сохраняет совместимость именований.
## @changes 2026-07-30 | DevPlan 088 Wave 4 — создан с Anti-Loop (собственный counter)
## @changes 2026-08-03 | DevPlan 124 T4 — master-guard (_is_xdist_worker)
## @changes 2026-08-05 | DevPlan 136 W12 T12.1 — переписан как тонкий ре-экспорт _conftest.counter;
##           dual counter и собственные session-хуки удалены
# endregion MODULE_CONTRACT

"""
Anti-Loop protocol for gate tests — thin re-export (DevPlan 136 W12 T12.1).

С 2026-08-05 counter для gates — ЕДИНЫЙ модуль tests/_conftest/counter.py (файл
tests/.test_counter.json), session-хуки — root tests/conftest.py (_conftest/session.py),
которые загружаются и для tests/gates/ прогонов. Здесь только re-экспорт counter-функций
для обратной совместимости импортов.

Escalation levels (peчатаются root session.py при не-PYTEST_NO_ESCALATION прогонах):
  Attempt 1-2: Output CHECKLIST of common errors
  Attempt 3:   Suggest external help (MCP tavily or Context 7)
  Attempt 4:   Warning: looping risk — pause and reflect
  Attempt 5+:  CRITICAL ERROR: agent looping detected — STOP
"""

# ── Единый counter-модуль (T12.1: dual counter удалён) ──────────────────────
from _conftest.counter import (  # ruff: ignore[F401]
    _increment_counter,
    _read_counter,
    _read_scope,
    _record_scope,
    _reset_counter,
    _write_counter,
)

# ⚠️ TRAP[DECISION] · 2026-08-05 · — · gates/conftest — тонкий ре-экспорт, без session-хуков
# · Rejected: оставить собственные pytest_sessionstart/finish + tests/gates/.test_counter.json
# · Reason: dual counter (T-1) — расщеплённое anti-loop состояние; собственный reset поднабором
#   (T-2) — ложный сброс attempts при 100% PASS gates-поднабора, стирающий evidence фейла
#   полного прогона. Root conftest session-хуки уже покрывают gates-прогоны (root conftest
#   загружается для tests/gates/) — собственные хуки = двойной increment/reset.
# · Rev: если gates-прогоны начнут исполняться ИЗОЛИРОВАННО (pytest --rootdir вне tests/) —
#   пересмотреть регистрацию session-хуков в root conftest.
