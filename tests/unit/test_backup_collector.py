#!/usr/bin/env python3
# GREP_SUMMARY: test-backup-collector backup-status get-backup-status mtime postgres-log app-data-log stale unknown read-error env-override
# STRUCTURE: fixtures(tmp log factory) → ◇ get_backup_status (ok both fresh, stale ≥25h, unknown no logs, one-missing-one-fresh) → ◇ read-error (OSError → None + WARN) → ◇ env-override paths → ⎋ LDD IMP:9
# region MODULE_CONTRACT
## @purpose  Unit tests for healthcheck/metrics/backup_collector.py (DevPlan 139 W4.6 — закрытие
##            blind spot backup_collector, 116 LOC, НОВЫЙ). Сборка backup-метрик (mtime-свежесть),
##            отсутствие бэкапов, ошибки чтения (graceful degradation).
## @scope    get_backup_status: оба лога свежие → "ok"; любой ≥25h → "stale"; ни одного → "unknown";
##           один отсутствует + один свежий → "ok"; OSError чтения → None + WARN; ISO-формат timestamps;
##           env-оверрайд путей (BACKUP_POSTGRES_LOG/BACKUP_APP_DATA_LOG).
## @invariants
##   - Threshold: <25h = "ok", ≥25h = "stale", лог отсутствует = "unknown" (нет логов вообще)
##   - Graceful degradation: ошибки чтения → None + WARN, никогда не raise
##   - Все пути конфигурируемы через env с сенсибл-дефолтами (не хардкод в тестах)
##   - tmp_path-изоляция (xdist); mtime задаётся через os.utime (детерминизм, без сна)
##   - Test Honesty R1-R5: negative-тесты (нет логов, stale, read-error) — 0 pass-тестов
##   - LDD: каждый тест — IMP:9-траектория (ldd_trajectory; модуль логирует IMP:9 всегда)
## @rationale W4 (139): 116 LOC production без тестов — backup-cron пишет логи, но collector
##            не читался → silent failure risk. Инварианты MODULE_CONTRACT — в исполняемые проверки.
## @changes  2026-08-05 | Created (DevPlan 139 W4.6)
# endregion MODULE_CONTRACT

import logging
import os
import re
import time
from pathlib import Path

from core.internal.healthcheck.metrics.backup_collector import get_backup_status
from tests._conftest.ldd import ldd_trajectory

logger = logging.getLogger(__name__)

_STALE_THRESHOLD_SECONDS = 25 * 3600  # 25h — зеркалит канон backup_collector
_ISO_RE = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z$")


# region FUNC__touch_log
## @purpose  Создать log-файл с заданным mtime (age_seconds назад от now).
## @io       ⇥ path: Path, age_seconds: float | None → ⎋ Path
## @complexity O(1)
def _touch_log(path: Path, age_seconds: float | None = None) -> Path:
    """Create a log file with mtime = now - age_seconds (None → now)."""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("backup log line\n")
    if age_seconds is not None:
        mtime = time.time() - age_seconds
        os.utime(path, (mtime, mtime))
    return path


# endregion FUNC__touch_log


# region FUNC__point_env
## @purpose  Направить BACKUP_*_LOG env на tmp-файлы (изоляция от /var/log).
## @io       ⇥ monkeypatch, postgres: Path | None, app_data: Path | None → ⎋ None
## @complexity O(1)
def _point_env(monkeypatch, postgres: Path | None, app_data: Path | None) -> None:
    """Set BACKUP_POSTGRES_LOG/BACKUP_APP_DATA_LOG env to tmp paths (or absent)."""
    monkeypatch.setenv("BACKUP_POSTGRES_LOG", str(postgres)) if postgres is not None else monkeypatch.delenv(
        "BACKUP_POSTGRES_LOG", raising=False
    )
    monkeypatch.setenv("BACKUP_APP_DATA_LOG", str(app_data)) if app_data is not None else monkeypatch.delenv(
        "BACKUP_APP_DATA_LOG", raising=False
    )


# endregion FUNC__point_env


# ═══════════════════════════════════════════════════════════════════════════
# get_backup_status
# ═══════════════════════════════════════════════════════════════════════════


# region FUNC_test_status_ok_both_fresh
## @purpose  Оба лога свежие (<25h) → status "ok", оба timestamp ISO (не None).
# 🧪 TRAP[TEST] · get_backup_status_ok_both_fresh · Contract · Regression: свежие бэкапы не "ok"
# · Scenario: postgres+app-data свежие (age 1h) → status "ok"; last_postgres_at/last_app_data_at
# ·   в ISO-формате; IMP:9 «Backup status: ok»
# · Last fail: N/A (новый тест W4.6)
# · Remove if: критерий свежести (<25h) меняется
@ldd_trajectory
def test_status_ok_both_fresh(tmp_path, monkeypatch, caplog) -> None:
    """Оба лога свежие → "ok", ISO-формат timestamps."""
    postgres = _touch_log(tmp_path / "backup" / "postgres.log", age_seconds=3600)
    app_data = _touch_log(tmp_path / "backup" / "app-data.log", age_seconds=1800)
    _point_env(monkeypatch, postgres, app_data)

    result = get_backup_status()

    assert result["status"] == "ok", f"Ожидался ok, got {result['status']}"
    assert result["last_postgres_at"] is not None and _ISO_RE.match(result["last_postgres_at"])
    assert result["last_app_data_at"] is not None and _ISO_RE.match(result["last_app_data_at"])
    logger.info(
        "[IMP:9][test] get_backup_status: оба свежие → ok (postgres=%s, app-data=%s) ✓",
        result["last_postgres_at"],
        result["last_app_data_at"],
    )


# endregion FUNC_test_status_ok_both_fresh


# region FUNC_test_status_stale_when_any_old
## @purpose  Любой лог ≥25h → "stale" (postgres свежий, app-data 30h назад).
# 🧪 TRAP[TEST] · get_backup_status_stale · NEGATIVE (R5) · Regression: устаревший бэкап не "stale"
# · Scenario: postgres age=1h, app-data age=30h (>25h) → status "stale"
# · Last fail: N/A (новый negative-тест W4.6)
# · Remove if: порог 25h меняется
@ldd_trajectory
def test_status_stale_when_any_old(tmp_path, monkeypatch, caplog) -> None:
    """app-data ≥25h → status "stale"."""
    postgres = _touch_log(tmp_path / "backup" / "postgres.log", age_seconds=3600)
    app_data = _touch_log(tmp_path / "backup" / "app-data.log", age_seconds=_STALE_THRESHOLD_SECONDS + 3600)
    _point_env(monkeypatch, postgres, app_data)

    result = get_backup_status()

    assert result["status"] == "stale", f"Любой ≥25h → stale, got {result['status']}"
    assert result["last_app_data_at"] is not None, "Устаревший лог всё равно читается"
    logger.info("[IMP:9][test] get_backup_status: app-data 26h → stale ✓")


# endregion FUNC_test_status_stale_when_any_old


# region FUNC_test_status_unknown_no_logs
## @purpose  Ни одного лога → "unknown", оба timestamp None.
# 🧪 TRAP[TEST] · get_backup_status_unknown_no_logs · NEGATIVE (R5) · Regression: отсутствие бэкапов не детектируется
# · Scenario: env указывает на несуществующие пути → status "unknown"; оба None
# · Last fail: N/A (новый negative-тест W4.6)
# · Remove if: семантика «нет логов → unknown» меняется
@ldd_trajectory
def test_status_unknown_no_logs(tmp_path, monkeypatch, caplog) -> None:
    """Нет ни одного лога → "unknown", timestamps None."""
    _point_env(monkeypatch, tmp_path / "missing" / "postgres.log", tmp_path / "missing" / "app-data.log")

    result = get_backup_status()

    assert result["status"] == "unknown", f"Ожидался unknown, got {result['status']}"
    assert result["last_postgres_at"] is None and result["last_app_data_at"] is None
    logger.info("[IMP:9][test] get_backup_status: логов нет → unknown ✓")


# endregion FUNC_test_status_unknown_no_logs


# region FUNC_test_status_ok_one_missing_one_fresh
## @purpose  Один лог отсутствует, второй свежий → "ok" (present и не stale).
# 🧪 TRAP[TEST] · get_backup_status_one_missing_one_fresh · Behavioral · Regression: отсутствие одного лога = stale
# · Scenario: postgres отсутствует, app-data свежий → "ok" (ничего не stale); postgres None
# · Last fail: N/A (новый тест W4.6)
# · Remove if: логика классификации при частичном наличии меняется
@ldd_trajectory
def test_status_ok_one_missing_one_fresh(tmp_path, monkeypatch, caplog) -> None:
    """Один лог отсутствует + второй свежий → "ok"."""
    app_data = _touch_log(tmp_path / "backup" / "app-data.log", age_seconds=3600)
    _point_env(monkeypatch, tmp_path / "missing" / "postgres.log", app_data)

    result = get_backup_status()

    assert result["status"] == "ok", f"Present+fresh без stale → ok, got {result['status']}"
    assert result["last_postgres_at"] is None, "Отсутствующий лог → None"
    logger.info("[IMP:9][test] get_backup_status: postgres нет, app-data свежий → ok ✓")


# endregion FUNC_test_status_ok_one_missing_one_fresh


# region FUNC_test_status_read_error_graceful
## @purpose  Ошибка чтения (OSError на getmtime) → None + WARN, НЕ raise; оба ошибочные → "unknown".
# 🧪 TRAP[TEST] · get_backup_status_read_error · NEGATIVE (R5) · Regression: ошибка чтения роняет collector
# · Scenario: os.path.getmtime → OSError("boom") для обоих путей → status "unknown", оба None,
# ·   WARN-лог «Error reading»
# · Last fail: N/A (новый negative-тест W4.6)
# · Remove if: graceful-degradation контракт (ошибки чтения → None) меняется
@ldd_trajectory
def test_status_read_error_graceful(tmp_path, monkeypatch, caplog) -> None:
    """OSError на чтении → None + WARN (graceful), оба → unknown, не raise."""
    postgres = _touch_log(tmp_path / "backup" / "postgres.log")
    app_data = _touch_log(tmp_path / "backup" / "app-data.log")
    _point_env(monkeypatch, postgres, app_data)

    def _boom(_path: str) -> float:
        raise OSError("permission denied (test)")

    monkeypatch.setattr("os.path.getmtime", _boom)

    result = get_backup_status()

    assert result["status"] == "unknown", f"Ошибки чтения → unknown, got {result['status']}"
    assert result["last_postgres_at"] is None and result["last_app_data_at"] is None
    warns = [r.message for r in caplog.records if "Error reading" in r.message]
    assert len(warns) >= 2, "Ожидались WARN-логи об ошибках чтения (оба лога)"
    logger.info("[IMP:9][test] get_backup_status: OSError чтения → None + WARN (graceful), unknown ✓")


# endregion FUNC_test_status_read_error_graceful


# region FUNC_test_env_override_paths_used
## @purpose  Env-оверрайд путей используется (не дефолтный /var/log/platform/backup/): файл вне
##            дефолтной локации читается, status "ok".
# 🧪 TRAP[TEST] · get_backup_status_env_override · Contract (env paths) · Regression: env-пути игнорируются
# · Scenario: BACKUP_*_LOG указывают на tmp-файлы (вне /var/log) → читаются → "ok";
# ·   дефолтный /var/log/platform/backup/postgres.log НЕ существует (не влияет)
# · Last fail: N/A (новый тест W4.6)
# · Remove if: env-конфигурируемость путей меняется
@ldd_trajectory
def test_env_override_paths_used(tmp_path, monkeypatch, caplog) -> None:
    """Env-пути читаются (tmp), дефолтный /var/log путь не задействован."""
    postgres = _touch_log(tmp_path / "custom-logs" / "pg.log", age_seconds=60)
    app_data = _touch_log(tmp_path / "custom-logs" / "app.log", age_seconds=120)
    _point_env(monkeypatch, postgres, app_data)

    result = get_backup_status()

    assert result["status"] == "ok", "Env-указанные файлы читаются"
    read_logs = [r.message for r in caplog.records if str(postgres) in r.message]
    assert read_logs, "Ожидался лог чтения env-указанного пути"
    logger.info("[IMP:9][test] get_backup_status: env-оверрайд путей работает (tmp-логи) ✓")


# endregion FUNC_test_env_override_paths_used
