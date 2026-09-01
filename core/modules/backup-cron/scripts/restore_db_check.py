#!/usr/bin/env python3
# GREP_SUMMARY: restore-db-check post-restore verification DATA-504 backup-cron expected-dbs pg_database exit-3
# STRUCTURE: ▶ ┌expected-file┐ → ○ read names → ⚡ docker exec psql SELECT datname → ⊕ compare (minus SYSTEM_DBS) → ◇ missing? → ⎋ rc {0|3}
# region MODULE_CONTRACT
## @purpose  Второй уровень защиты DR-restore (DATA-504 контракт): «restore не может молча
##           недопримениться». После заливки дампа сверяет ОЖИДАЕМЫЕ БД (имена CREATE DATABASE
##           из дампа, собранные restore_self_role_filter.awk в EXPECTED_DBS_FILE) с
##           ФАКТИЧЕСКИМ кластером (docker exec postgres psql -tAc SELECT datname FROM pg_database).
##           Любая ожидаемая БД отсутствует → IMP:10 + exit 3 с перечнем недостающих.
##           P0-контекст: чёрная дыра фильтра давала rc=0 над пустым кластером (базы удалены,
##           данные потеряны) — эта проверка делает такой молчаливый исход невозможным.
## @scope    Вызывается из core/modules/backup-cron/scripts/restore_psql.sh ПОСЛЕ psql-заливки
##           (все ветки дампа: .plain/.gz/.age/.gz.age).
## @io       ⇥ argv: --expected-file <path> (по одной БД на строку); env POSTGRES_USER
##           (через secrets.env, для docker exec psql -U); ⎋ exit 0 | 3
## @invariants
##   - Системные БД template0/template1/postgres НЕ участвуют в сравнении (постgres всегда
##     существует — его присутствие ничего не доказывает о восстановлении user-данных).
##   - Fail-closed: пустой expected-файл (сбой сбора имён) → exit 3, НЕ vacuous-pass.
##   - Сравнение только expected ⊆ actual (лишние БД кластера — не ошибка).
##   - LDD-логи IMP:9 (OK) / IMP:10 (missing/сбой) — машиночитаемые для оператора и тестов.
## @rationale Чистая Python-логика (сравнение) — юнит-тестируема нативно; docker exec —
##            только тонкий I/O-слой с DI-seam (runner) в стиле соседних backup-тестов.
## @changes 2026-09-01 | P0: created — post-restore DB presence guard (DATA-504)
# endregion MODULE_CONTRACT

from __future__ import annotations

import argparse
import logging
import subprocess
import sys
from pathlib import Path
from typing import Protocol

logger = logging.getLogger("restore-db-check")

# Системные БД — всегда существуют; их наличие не является свидетельством восстановления.
SYSTEM_DBS = frozenset({"template0", "template1", "postgres"})

# LDD-пороги маппинга на logging-уровни (IMP:10 → ERROR, IMP:9 → INFO).
_LDD_CRITICAL = 10
_LDD_INFO = 9


class _RunResult(Protocol):
    """Минимальный контракт результата subprocess для DI-seam (тестовый fake)."""

    returncode: int
    stdout: str
    stderr: str


class _Runner(Protocol):
    """subprocess-подобный runner для query_cluster_dbs (DI, 0 патчей в тестах)."""

    def run(self, cmd: list[str], **kwargs: object) -> _RunResult: ...


class _CliArgs(argparse.Namespace):
    """Типизированный argparse-Namespace (W11-G3): parse_args(namespace=...) — без Any-каскада.

    ## @invariants — ТОЛЬКО аннотации БЕЗ значений: argparse заполняет дефолты из
    ##              add_argument (class-значения ломали бы hasattr-defaults).
    """

    def __init__(self) -> None:
        super().__init__()
        self.expected_file: str


def _ensure_logging() -> None:
    """Attach a stderr handler once so CLI invocations emit [IMP:n] lines to the operator."""
    if not logger.handlers:
        handler = logging.StreamHandler(sys.stderr)
        handler.setFormatter(logging.Formatter("%(message)s"))
        logger.addHandler(handler)
    # IMP:1-10 мапятся на logging-уровни 1-10 (ниже DEBUG) — НЕ фильтровать (LDD-канон).
    logger.setLevel(logging.DEBUG)


def _log(imp_level: int, msg: str) -> None:
    """Emit an [IMP:n]-tagged LDD line (logger-canon: [IMP:n] в тексте, стандартный уровень).

    ## @purpose — Маппинг LDD-уровней на logging-уровни: IMP:9 → INFO, IMP:10 → ERROR,
    ##             чтобы caplog/stdlib-хендлеры (>= DEBUG) реально захватывали записи.
    ## @complexity 1 — level map + emit
    """
    _ensure_logging()
    text = f"[IMP:{imp_level}][restore-db-check] {msg}"
    if imp_level >= _LDD_CRITICAL:
        logger.error(text)
    elif imp_level >= _LDD_INFO:
        logger.info(text)
    else:
        logger.debug(text)


def read_expected_names(path: str | Path) -> list[str]:
    """Read expected DB names (one per line) from the filter's side-output file.

    ## @purpose — Parse EXPECTED_DBS_FILE: blank/strip-tolerant, order-agnostic.
    ## @io — ⇥ path: file path → ⎋ list[str]
    ## @complexity 1 — file read + split
    """
    names: list[str] = []
    for raw in Path(path).read_text(encoding="utf-8").splitlines():
        name = raw.strip()
        if name:
            names.append(name)
    return names


def compare_dbs(expected: list[str], actual: list[str]) -> list[str]:
    """Return sorted expected DBs missing from the cluster (system DBs excluded).

    ## @purpose — Core DATA-504 comparison: expected (from dump) minus actual (cluster),
    ##             excluding template0/template1/postgres on both sides.
    ## @io — ⇥ expected, actual → ⎋ sorted missing list
    ## @complexity 2 — set difference
    """
    expected_non_system = {n for n in expected if n not in SYSTEM_DBS}
    actual_non_system = {n for n in actual if n not in SYSTEM_DBS}
    return sorted(expected_non_system - actual_non_system)


def query_cluster_dbs(runner: _Runner | None = None) -> list[str]:
    """Query the live cluster for database names (docker exec postgres psql).

    ## @purpose — I/O seam: actual DBs from pg_database catalog. runner DI для юнит-тестов
    ##             (subprocess-модуль по умолчанию; fake — в тестах, 0 патчей).
    ## @io — ⇥ runner: subprocess-like → ⎋ list[str]; RuntimeError на rc != 0
    ## @complexity 1 — single subprocess call
    """
    cmd = [
        "docker",
        "exec",
        "-i",
        "postgres",
        "sh",
        "-c",
        'exec psql -U "$POSTGRES_USER" -d postgres -tAc "SELECT datname FROM pg_database"',
    ]
    if runner is None:
        proc = subprocess.run(cmd, capture_output=True, text=True, check=False)
    else:
        proc = runner.run(cmd, capture_output=True, text=True, check=False)
    if proc.returncode != 0:
        _log(10, f"cluster DB query failed rc={proc.returncode}: {proc.stderr.strip()}")
        err = f"cluster DB query failed rc={proc.returncode}"
        raise RuntimeError(err)
    return [line.strip() for line in proc.stdout.splitlines() if line.strip()]


def main(argv: list[str], runner: _Runner | None = None) -> int:
    """Post-restore DB presence gate: exit 0 (all present) | 3 (missing / check failed).

    ## @purpose — DATA-504 оркестрация: read expected → query cluster → compare → verdict.
    ## @io — ⇥ argv[1:]: --expected-file; runner: DI-seam → ⎋ int exit code
    ## @complexity 3 — orchestration of pure + I/O steps
    """
    _ensure_logging()
    parser = argparse.ArgumentParser(prog="restore_db_check.py")
    _ = parser.add_argument("--expected-file", required=True, help="file with expected DB names (one per line)")
    args = parser.parse_args(argv[1:], namespace=_CliArgs())

    expected = read_expected_names(args.expected_file)
    if not expected:
        # Fail-closed: сбой сбора имён НЕ должен превращаться в vacuous-pass.
        _log(10, "expected DB list EMPTY — collection failed or degenerate dump; aborting (DATA-504)")
        return 3

    try:
        actual = query_cluster_dbs(runner=runner)
    except RuntimeError:
        return 3

    missing = compare_dbs(expected, actual)
    if missing:
        _log(10, f"DATA-504: MISSING DATABASES after restore: {', '.join(missing)}")
        return 3
    checkable = [n for n in expected if n not in SYSTEM_DBS]
    _log(9, f"post-restore DB check OK: all {len(checkable)} expected databases present")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
