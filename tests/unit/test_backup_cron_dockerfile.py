# GREP_SUMMARY: test backup-cron dockerfile date_parser s3_client retention import upload non-blocking C1
# STRUCTURE: ▶ static ACs (Dockerfile COPYs, backup_config 0 core.internal) → ▶ native import chain (retention+upload) → ▶ docker build + import retention (R5) → ▶ upload после дампа (AC-C1.3)
# region MODULE_CONTRACT
"""
@purpose  DevPlan 119 C1 regression tests for the backup-cron container build:
          (1) Dockerfile копирует date_parser.py + s3_client.py (иначе retention падает
          на ImportError в cron); (2) backup_config.py не импортирует core.internal.config
          (отсутствует в образе); (3) retention/upload импортируются чисто; (4) R5 —
          docker build + import retention → SUCCESS; (5) upload-s3.sh вызывается после
          pg_dumpall (off-site цепочка активна, «не блокировать при ошибке upload»).
@scope    tests/unit/ — статические проверки + нативный импорт без Docker; единственный
          requires_docker-тест — полная R5-верификация образа.
@invariants
  - Статические тесты (Dockerfile/backup_config/backup_postgres) — 0 Docker, всегда бегут
  - test_retention_import_in_container_negative — @pytest.mark.requires_docker (тяжёлый build)
  - Нативный импорт retention/upload/backup_config — без core.internal в sys.path (контейнерный контракт)
  - R5: до фикса C1 импорт retention падал (date_parser/s3_client не скопированы +
    backup_config → core.internal.config) — тесты ловят регрессию
@rationale  DevPlan 119 C1 $TEST_SPEC: test_retention_import_in_container_negative +
            test_upload_called_after_dump. Acceptance criteria AC-C1.1/AC-C1.2/AC-C1.3
            закреплены статическими тестами (быстрые, гоняются в gate).
@changes 2026-08-02 | Created per DevPlan 119 C1
"""
# endregion MODULE_CONTRACT

import logging
import pathlib
import subprocess
import sys

import pytest

from tests._conftest.ldd import ldd_trajectory

logger = logging.getLogger(__name__)

_ROOT = pathlib.Path(__file__).resolve().parents[2]
_DOCKERFILE = _ROOT / "core" / "modules" / "backup-cron" / "Dockerfile"
_SCRIPTS_DIR = _ROOT / "core" / "modules" / "backup-cron" / "scripts"
_BACKUP_POSTGRES_PY = _SCRIPTS_DIR / "backup_postgres.py"


# ═══════════════════════════════════════════════════════════════════════
# Статические AC-проверки (быстрые, 0 Docker)
# ═══════════════════════════════════════════════════════════════════════


@pytest.mark.static_audit
@ldd_trajectory
# 🧪 TRAP[TEST] · 2026-08-02/04 · REGRESSION + NEGATIVE (R5) · Dockerfile копирует cron-скрипты (AC-W2/C1)
# · Scenario: AC-W2 (wal_sync.py, DevPlan 132 W2) + AC-C1.1 (date_parser.py/s3_client.py, DevPlan 119 C1) —
# ·   если модуль не COPY'ится, cron падает (No such file / ImportError retention)
# · Last fail: до C1 — Dockerfile копировал retention.py БЕЗ его импортов
# · Remove if: доставка скриптов переезжает в другой механизм
@pytest.mark.parametrize("script", ["wal_sync.py", "date_parser.py", "s3_client.py"])
def test_dockerfile_copies_scripts(script: str, caplog) -> None:
    """AC-W2/AC-C1.1: Dockerfile содержит COPY scripts/<script> для всех cron-скриптов."""
    content = _DOCKERFILE.read_text(encoding="utf-8")

    assert f"COPY scripts/{script}" in content, f"AC FAIL: Dockerfile не копирует {script}"
    logger.critical("[IMP:9][dockerfile][copy] COPY scripts/%s присутствует (PASS)", script)


@pytest.mark.static_audit
@ldd_trajectory
# 🧪 TRAP[TEST] · 2026-08-02 · NEGATIVE (R5) · backup_config без core.internal (C1)
# · Scenario: AC-C1.2 — любой импорт backup_config падал на core.internal.config
#   (модуль отсутствует в образе) → upload.py и retention.py оба были сломаны
# · Last fail: до C1 — from core.internal.config import platform_config (LINT-EXEMPT)
# · Remove if: backup-cron образ начнёт включать core/internal (не планируется)
def test_backup_config_has_no_core_internal_import(caplog) -> None:
    """AC-C1.2: backup_config.py не импортирует core.internal.* (контейнерный контракт)."""
    content = (_SCRIPTS_DIR / "backup_config.py").read_text(encoding="utf-8")

    # Ищем ИМПОРТЫ core.internal (from core.internal ... / import core.internal ...),
    # а не упоминания в docstring/@changes — комментарии допустимы, импорты — нет.
    import_lines = [
        line.strip()
        for line in content.splitlines()
        if line.strip().startswith(("from core.internal", "import core.internal"))
    ]
    assert not import_lines, (
        f"AC-C1.2 FAIL: backup_config.py импортирует core.internal: {import_lines} — модуль отсутствует в образе"
    )
    logger.critical("[IMP:9][backup_config][import] 0 core.internal imports (AC-C1.2 PASS)")


# GUARD-PRESERVE (168): единственное не-Docker покрытие нативной импорт-цепочки retention/upload (R5 C1) — полная docker-версия requires_docker и скипается в gate
@pytest.mark.static_audit
@ldd_trajectory
# 🧪 TRAP[TEST] · 2026-08-02 · REGRESSION · Нативный импорт retention/upload (C1)
# · Scenario: retention.py + upload.py импортируются чисто из scripts/ (без core.internal
#   в sys.path) — то же самое произойдёт в контейнере после COPY fix
# · Last fail: до C1 — ImportError: No module named 'core.internal.config'
# · Remove if: backup-cron переходит на другой механизм конфигурации
def test_retention_and_upload_import_clean(caplog) -> None:
    """R5 (быстрая версия): нативный импорт retention/upload/backup_config без core.internal.

    НЕ трогаем sys.modules: backup_config.py больше не импортирует core.internal (C1,
    проверяется статически test_backup_config_has_no_core_internal_import) — импорт
    цепочки не тянет core.*. Pop core.* из sys.modules ломал изоляцию последующих тестов
    (двойной импорт state_machine → разные identity PhasePreconditionError — наблюдалось
    на test_bootstrap_phases, DevPlan 119 C).
    """
    # Изолируем: НЕ добавляем core/ в sys.path — контейнерный контракт (только scripts/)
    sys.path.insert(0, str(_SCRIPTS_DIR))
    try:
        import backup_config  # ruff: ignore[F401] — C1: 0 core.internal
        import date_parser  # ruff: ignore[F401]
        import retention  # ruff: ignore[F401]
        import s3_client  # ruff: ignore[F401]
        import upload  # ruff: ignore[F401]
    finally:
        sys.path.remove(str(_SCRIPTS_DIR))

    # R1: реальное утверждение — все 5 модулей загружены в sys.modules (импорт не упал)
    for mod_name in ("backup_config", "date_parser", "retention", "s3_client", "upload"):
        assert mod_name in sys.modules, f"модуль {mod_name} не импортировался — регрессия цепочки C1"

    logger.critical("[IMP:9][import][clean] retention + upload + backup_config импортируются чисто (R5 PASS)")


# ═══════════════════════════════════════════════════════════════════════
# R5: полная верификация в контейнере (docker build + import retention)
# ═══════════════════════════════════════════════════════════════════════


@pytest.mark.requires_docker
@ldd_trajectory
# 🧪 TRAP[TEST] · 2026-08-02 · NEGATIVE (R5) · docker build + import retention (C1)
# · Scenario: DevPlan AC-C1.4 — образ backup-cron собирается, retention.py импортируется
#   (было ImportError: date_parser/s3_client не скопированы + backup_config → core.internal)
# · Last fail: до C1 — ImportError при cron-запуске retention (05:00 UTC)
# · Remove if: backup-cron перестаёт использовать python-скрипты в образе
def test_retention_import_in_container_negative(caplog) -> None:
    """R5 (полная): docker build backup-cron → docker run import retention → SUCCESS."""
    module_dir = _ROOT / "core" / "modules" / "backup-cron"
    image_tag = "backup-cron-c1-r5-test"

    try:
        build = subprocess.run(
            ["docker", "build", "-q", "-t", image_tag, str(module_dir)],
            capture_output=True,
            text=True,
            timeout=900,
            check=False,
        )
        assert build.returncode == 0, f"docker build FAILED:\n{build.stdout}\n{build.stderr}"
        logger.critical("[IMP:9][container][build] docker build backup-cron OK")

        # cron запускает retention.py как СКРИПТ (/usr/local/bin/retention.py) — каталог
        # скрипта добавляется в sys.path автоматически. Эмулируем это через PYTHONPATH.
        # retention.py сам добавляет свой каталог в sys.path (строка 40) — импорт retention
        # покрывает всю цепочку: backup_config + date_parser + s3_client
        run = subprocess.run(
            [
                "docker",
                "run",
                "--rm",
                "-e",
                "PYTHONPATH=/usr/local/bin",
                image_tag,
                "python3",
                "-c",
                "import retention",
            ],
            capture_output=True,
            text=True,
            timeout=120,
            check=False,
        )
        assert run.returncode == 0, f"docker run import retention FAILED:\n{run.stdout}\n{run.stderr}"
    finally:
        subprocess.run(["docker", "rmi", image_tag], capture_output=True, text=True, check=False)

    logger.critical("[IMP:9][container][import] docker run import retention → SUCCESS (AC-C1.4 PASS)")


# ═══════════════════════════════════════════════════════════════════════
# AC-C1.3: upload-цепочка активна после успешного дампа
# ═══════════════════════════════════════════════════════════════════════


@pytest.mark.static_audit
@ldd_trajectory
# 🧪 TRAP[TEST] · 2026-08-02 · REGRESSION · upload-s3.sh вызывается после дампа (C1)
# · Scenario: AC-C1.3 — backup_postgres.py (вызываемый из backup-postgres.sh) инвокает
#   _UPLOAD_SCRIPT ПОСЛЕ успешного дампа; «не блокировать» — upload rc не проваливает бэкап
# · Last fail: до 117 D64 upload-цепочка была мёртвой (0 cron-вызовов)
# · Remove if: upload логика переезжает в другой модуль
def test_upload_called_after_dump(caplog) -> None:
    """AC-C1.3: upload-s3.sh вызывается после pg_dumpall (не блокирует при ошибке)."""
    content = _BACKUP_POSTGRES_PY.read_text(encoding="utf-8")

    # 1) Инвокация upload присутствует
    assert "_UPLOAD_SCRIPT, dump_file, s3_key" in content, "AC-C1.3 FAIL: backup_postgres.py не вызывает upload-s3.sh"
    # 2) Вызов происходит ПОСЛЕ отметки успешного дампа (backup_success = True)
    dump_success_idx = content.find("backup_success = True")
    upload_idx = content.find("_UPLOAD_SCRIPT, dump_file, s3_key")
    assert dump_success_idx != -1 and upload_idx != -1, "markers not found in backup_postgres.py"
    assert upload_idx > dump_success_idx, "AC-C1.3 FAIL: upload вызывается ДО успешного дампа"
    # 3) «Не блокировать при ошибке upload»: после инвокации есть проверка rc → return 0
    assert "return 0" in content[upload_idx:], "AC-C1.3 FAIL: upload failure должен НЕ проваливать бэкап"

    logger.critical("[IMP:9][backup_postgres][upload] upload-s3.sh после дампа, non-blocking (AC-C1.3 PASS)")
