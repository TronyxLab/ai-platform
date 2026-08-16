#!/usr/bin/env python3
# GREP_SUMMARY: gate watchdog clean-env cron-emulation no-pythonpath env-i subprocess stdlib-only 142-W2-regression
# STRUCTURE: ▶ ┌repo_root/core/internal/healthcheck/watchdog.py┐ → ⚡ env -i HOME PATH python3 -S --dry-run → ◇ returncode 0? → ⊕ assert stderr-detail → ⎋ PASS (cron-emulation)
# region MODULE_CONTRACT
## @purpose  CI-gate (DevPlan 162 W1-2): watchdog.py ДОЛЖЕН работать в cron-env — чистом
##           окружении БЕЗ PYTHONPATH. Регрессия 142 W2 (импорт core.internal на module-level
##           в stdlib-only скрипте) прошла в production, потому что unit-тесты гоняют модуль
##           через pytest (с PYTHONPATH) и cron-emulation отсутствовал. Гейт реально запускает
##           watchdog.py как subprocess в `env -i` и падает на exit != 0.
## @scope    tests/gates/test_gate_watchdog_clean_env.py — два subprocess-прогона:
##           1) primary: `env -i ... python3 -S watchdog.py --dry-run` (sys.executable, -S = без
##              site-packages → editable-установка платформы невидима → строгий stdlib-only);
##           2) CI-parity: точная команда плана `env -i ... /usr/bin/python3 watchdog.py --dry-run`,
##              только если /usr/bin/python3 >= 3.10 (macOS-системный 3.9 не умеет PEP 604).
##           Оба прогона — cron-emulation, 0 мутаций (--dry-run не пишет state, не рестартит).
## @invariants
##   - watchdog.py — stdlib-only: cron запускает его БЕЗ PYTHONPATH (@invariant 1, строки 8-13)
##   - env -i полностью очищает окружение — PYTHONPATH/venv-переменные недоступны дочернему процессу
##   - -S (primary) удаляет site-packages: даже editable-установка платформы не резолвит core.internal
##   - docker CLI недоступен в чистом env → watchdog логирует IMP:7 и exit 0 (контракт: non-fatal)
##   - --dry-run = 0 мутаций (state не пишется) — гейт read-only для продакшн-артефактов
##   - НЕ ослаблять: любые попытки подменить python3 или добавить PYTHONPATH = RED
## @rationale РЕГРЕССИЯ 142 W2: test_watchdog.py импортирует watchdog через pytest (PYTHONPATH
##            есть) — cron-env-регрессия невидима для unit-тестов. Единственный способ поймать —
##            реальный subprocess в чистом окружении (DevPlan 162 W1-2, evidence: grep tests/ —
##            нет subprocess-вызова env -i ... watchdog.py).
##            ▶ Отклонение от литерала плана (зафиксировано 2026-08-13): план предлагал
##            `/usr/bin/python3` как единственный прогон. На macOS-дев-машинах /usr/bin/python3 —
##            системный 3.9.6 (без PEP 604: `list[str]`/`dict | None` в watchdog.py → SyntaxError),
##            т.е. локальный прогон даёт ложный RED. Решение: primary = sys.executable -S
##            (сильнее: site-packages недоступны вовсе, работает на macOS и Linux), CI-parity =
##            точная команда плана на Linux (/usr/bin/python3 = 3.10+/3.12). Оба прогона падают
##            на импорте core.internal (ModuleNotFoundError/ImportError → rc != 0 → RED).
## @changes  2026-08-13 | DevPlan 162 W1-2 — создан (CI-шаг watchdog clean env, P0)
## @changes  2026-08-13 | macOS-фикс: primary sys.executable -S + CI-parity /usr/bin/python3 (>=3.10)
# endregion MODULE_CONTRACT

import logging
import subprocess
import sys
from pathlib import Path

import pytest

from tests.conftest import ldd_trajectory

logger = logging.getLogger(__name__)

# core/internal/healthcheck/watchdog.py — 3 уровня вверх от tests/gates/
_WATCHDOG_PATH: Path = (
    Path(__file__).resolve().parent.parent.parent / "core" / "internal" / "healthcheck" / "watchdog.py"
)

# PEP 604 (list[str] / dict | None) в watchdog.py требует Python 3.10+ — системный
# /usr/bin/python3 на macOS = 3.9.6 (ложный SyntaxError), на CI/Linux = 3.10+/3.12.
_PEP604_MIN = (3, 10)


def _clean_env() -> dict[str, str]:
    """env -i (cron-emulation): только HOME + PATH; PYTHONPATH/venv-переменные отсутствуют."""
    return {"HOME": str(Path.home()), "PATH": "/usr/bin:/bin"}


@ldd_trajectory
@pytest.mark.gate
# 🧪 TRAP[TEST] · 2026-08-13 · REGRESSION · watchdog 142 W2 clean-env
# · Scenario: watchdog.py запускается как subprocess в `env -i` (cron-emulation: HOME + PATH только,
#   PYTHONPATH отсутствует); primary — sys.executable -S (без site-packages), CI-parity — /usr/bin/python3
#   при >= 3.10; --dry-run на ноде без docker → exit 0 (docker unavailable = non-fatal).
# · Last fail: 2026-08-13 — «watchdog died in cron (ModuleNotFoundError), regression 142 W2 passed to production»
# · Remove if: watchdog перестаёт исполняться из cron (или cron-emulation заменён иным механизмом)
def test_watchdog_runs_without_pythonpath(caplog) -> None:
    """Регрессия 142 W2: watchdog должен работать в cron env (без PYTHONPATH)."""
    caplog.set_level(logging.INFO)
    logger.info(
        "[IMP:8][test_watchdog_runs_without_pythonpath] Running watchdog in clean env (cron-emulation): %s",
        _WATCHDOG_PATH,
    )

    # ── Primary: sys.executable -S — строгий stdlib-only (site-packages отключены) ──
    result = subprocess.run(
        [
            "env",
            "-i",
            f"HOME={Path.home()}",
            f"PATH={Path(sys.executable).parent}:/usr/bin:/bin",
            sys.executable,
            "-S",
            str(_WATCHDOG_PATH),
            "--dry-run",
        ],
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
    )
    if result.returncode != 0:
        logger.error(
            "[IMP:9][test_watchdog_runs_without_pythonpath] FAIL — watchdog died in clean env (rc=%d)",
            result.returncode,
        )
        pytest.fail(
            f"watchdog failed in clean env (rc={result.returncode}):\n{result.stderr}\n--- stdout ---\n{result.stdout}"
        )

    # ── CI-parity: точная команда плана (env -i ... /usr/bin/python3), если >= 3.10 ──
    usrsbin_py3 = Path("/usr/bin/python3")
    if usrsbin_py3.exists():
        ver = subprocess.run(
            [str(usrsbin_py3), "-c", "import sys; print('%d.%d' % sys.version_info[:2])"],
            capture_output=True,
            text=True,
            timeout=10,
            check=False,  # probe: fallback (0, 0) on failure below
        ).stdout.strip()
        ver_tuple = tuple(int(x) for x in ver.split(".")[:2]) if ver else (0, 0)
        if ver_tuple >= _PEP604_MIN:
            result2 = subprocess.run(
                [
                    "env",
                    "-i",
                    *[f"{k}={v}" for k, v in _clean_env().items()],
                    "/usr/bin/python3",
                    str(_WATCHDOG_PATH),
                    "--dry-run",
                ],
                capture_output=True,
                text=True,
                timeout=30,
                check=False,
            )
            if result2.returncode != 0:
                logger.error(
                    "[IMP:9][test_watchdog_runs_without_pythonpath] FAIL — /usr/bin/python3 parity (rc=%d)",
                    result2.returncode,
                )
                pytest.fail(
                    f"watchdog failed with /usr/bin/python3 in clean env (rc={result2.returncode}):\n{result2.stderr}"
                )
        else:
            logger.info(
                "[IMP:7][test_watchdog_runs_without_pythonpath] /usr/bin/python3 = %s (< 3.10, PEP 604) — CI-parity пропущен (macOS-системный python), primary проверка выполнена",
                ver or "unknown",
            )

    logger.info(
        "[IMP:9][test_watchdog_runs_without_pythonpath] PASS — watchdog exits 0 in cron-env (no PYTHONPATH, stdlib-only): %s",
        (result.stderr or "").strip().replace("\n", " | ") or "no stderr",
    )
