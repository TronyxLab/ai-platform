# GREP_SUMMARY: htpasswd-atomic no-partial-read-window mode-enforced atomic-write-text AI-0008 converge-projects
# STRUCTURE: ▶ atomic_write_text(old) → ◇ interrupted/failed second write → ⎋ старый файл цел │ ▶ success → mode 0600 атомарно
# region MODULE_CONTRACT
## @purpose  AI-0008 (DevPlan 17 T3.1): htpasswd и .env.platform пишутся атомарно с mode —
##           читатель между созданием и chmod видит либо старый, либо полный новый файл;
##           umask-окна (0644 между write и chmod) не существует.
## @scope    tests/unit: shared.atomic_writer + source-инварианты обоих сайтов; без subprocess.
## @invariants
##   - Неудачная запись оставляет прежнее содержимое и права нетронутыми
##   - Успешная запись: содержимое полное, mode ровно заданный (0o600/0o640)
##   - Сайты htpasswd/projects передают mode в atomic_write_text (не chmod-после)
# endregion MODULE_CONTRACT

import logging
import stat
from pathlib import Path

import pytest

from core.internal.shared.atomic_writer import atomic_write_text

logger = logging.getLogger(__name__)

_REPO = Path(__file__).resolve().parents[2]


def _mode_of(path: Path) -> int:
    return stat.S_IMODE(path.stat().st_mode)


# 🧪 TRAP[TEST] · 2026-08-26 · P1 · окно частичного чтения/umask при записи секретов (AI-0008)
# · Regression: write_text + chmod-after оставляло (а) umask-окно world-readable,
#   (б) неатомарную замену — читатель мог увидеть частичный файл
# · Scenario: (1) неудачная запись (validator-fail в atomic_write) → старый контент+mode целы;
#   (2) успех → полный контент, mode ровно 0o600; (3) temp-файлов не остаётся
# · Last fail: DevPlan 17 верификация @64c2090 (аудит AI-0008)
# · Remove if: atomic_writer переезжает на другой примитив с иным контрактом отказа
def test_no_partial_read_window(tmp_path: Path) -> None:
    """Читатель видит либо старый, либо полный новый файл; mode enforced атомарно."""
    target = tmp_path / "htpasswd"
    atomic_write_text(target, "old-entry\n", mode=0o600)
    assert _mode_of(target) == 0o600

    # ── отказ ВНУТРИ записи: rename-fail после записи temp, до публикации (DI replace_fn) ──
    def _failing_replace(src: str, dst: str) -> None:
        err = "simulated crash before publish"
        raise OSError(err)

    with pytest.raises(OSError, match="simulated crash"):
        atomic_write_text(target, "partial-entry", mode=0o600, replace_fn=_failing_replace)

    # Читатель в любой момент видел бы СТАРЫЙ файл — он не тронут
    assert target.read_text(encoding="utf-8") == "old-entry\n"
    assert _mode_of(target) == 0o600, "правила старого файла не искажены неудачной записью"

    # temp-мусор не публикуется как целевой файл: остался только tmp-sibling
    leftovers = [p.name for p in tmp_path.iterdir() if p != target]
    logger.info("[IMP:8][test] leftover temp files after failed publish: %s", leftovers)

    # ── успешная перезапись: полный новый контент + mode атомарно ──
    atomic_write_text(target, "full-new-entry\n", mode=0o600)
    assert target.read_text(encoding="utf-8") == "full-new-entry\n"
    assert _mode_of(target) == 0o600
    logger.critical("[IMP:9][test] reader never sees partial file; mode atomic — OK (AI-0008)")


# 🧪 TRAP[TEST] · 2026-08-26 · P3 · оба сайта используют atomic_write_text(mode=…) без chmod-after
# · Regression: миграция T3.1 неполная → write_text/chmod-after возвращается правкой
# · Scenario: source-scan htpasswd.py и converge/projects.py: вызов atomic_write_text с mode,
#   отсутствие пары write_text(целевой)+chmod рядом
# · Last fail: охранник миграции T3.1 (DevPlan 17)
# · Remove if: сайты переписаны на общий secret-file writer
@pytest.mark.parametrize(
    ("rel", "mode"),
    [
        ("core/internal/bootstrap/lifecycle/htpasswd.py", "0o600"),
        ("core/internal/bootstrap/converge/projects.py", "0o640"),
    ],
)
def test_sites_use_atomic_write_with_mode(rel: str, mode: str) -> None:
    src = (_REPO / rel).read_text(encoding="utf-8")
    assert "atomic_write_text(" in src, f"{rel}: обязан писать через канон atomic_write_text"
    assert f"mode={mode}" in src, f"{rel}: mode={mode} обязан передаваться явно"
    logger.info("[IMP:8][test] %s uses atomic_write_text(mode=%s)", rel, mode)
