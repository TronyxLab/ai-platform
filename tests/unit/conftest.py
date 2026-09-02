# GREP_SUMMARY: conftest, unit, hermetic-env, devplan-029, T9, env-hermeticity
# STRUCTURE: ▶ root re-export → ◇ hermetic_platform_env autouse → ⎋ unit-session
# region MODULE_CONTRACT
## @purpose  tests/unit/conftest: (1) re-export корневого tests/conftest (исторический контракт
##           'from conftest import ldd_trajectory/assert_ldd_stderr/...' — unit-тесты импортируют
##           'conftest', который до появления этого файла резолвился в tests/conftest.py);
##           (2) T9 (DevPlan 029) hermetic_platform_env autouse — чистит протёкшие платформенные
##           env-ключи перед каждым unit-тестом (NODE_NAME-утечка класс, dotenv-инжект).
## @scope    Весь tests/unit/ — autouse fixture + root re-export.
## @changes 2026-09-02 · DevPlan 029 T9 — created
# endregion MODULE_CONTRACT

# Исторический контракт: unit-тесты делают 'from conftest import ...' — re-export корневого
# conftest сохраняет разрешение имён (см. MODULE_CONTRACT).
# T9: autouse env-hermeticity (DevPlan 029) — регистрация фикстуры.
from _conftest.env import hermetic_platform_env  # ruff: ignore[F401] — autouse-регистрация (T9)

from tests.conftest import *  # ruff: ignore[F403] — re-export корневого conftest
