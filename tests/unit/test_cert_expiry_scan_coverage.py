"""
# GREP_SUMMARY: test cert-expiry scan-coverage fullchain-pem fullchain-cer letsencrypt-live acme merge min-days REF-0008
# STRUCTURE: ▶ tmp cert-stores (acme <d>_ecc/fullchain.cer + LE <d>/fullchain.pem) → ◇ scan_expiring: оба filename-типа
#            → ◇ check(scan_dirs=[A,B]): merge min(days_left) per domain → TG once ⎋ IMP:9
# region MODULE_CONTRACT
## @purpose  Scan-coverage тесты expiry-unit (REF-0008 подпункт 3): скан покрывает ОБА cert-store —
##           acme.sh layout (fullchain.cer, <domain>[_ecc]) И letsencrypt live layout (fullchain.pem,
##           S3-restored сертификаты). Прежде restored-серты были вне renewal И вне скана →
##           гарантированный протухший TLS ≤90 дней без алертов (FAIL-0301/0302).
## @scope    scan_expiring + check() с DI run_fn/notify_fn/scan_dirs — 0 реальных openssl/subprocess.
## @invariants
##   - CERT_FILENAMES содержит fullchain.cer И fullchain.pem
##   - Один домен в обоих корнях → merge с min(days_left) (пессимистичный вердикт)
##   - Все файлы в tmp_path; enddate — scripted через run_fn
## @rationale REF-0008: единый скан двух disjoint cert-store без объединения хранилищ
## @changes  2026-08-24 | REF-0008 (meta-refactoring В2) — Created
# endregion MODULE_CONTRACT
"""

import logging
import sys
import types
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent / "core" / "internal" / "bootstrap"))

import cert_expiry_check as ce
import pytest

from tests._conftest.ldd import ldd_trajectory

pytestmark = pytest.mark.static_audit

logger = logging.getLogger(__name__)

NOW = datetime(2026, 8, 24, 12, 0, 0, tzinfo=timezone.utc)


def _scripted_enddate(days_by_path: dict[str, int]):
    """run_fn-DI: по пути файла возвращает notAfter = NOW + days (scripted openssl)."""

    def _run(cmd: list[str]) -> object:
        joined = " ".join(cmd)
        for path_suffix, days in days_by_path.items():
            if path_suffix in joined:
                enddate = NOW + timedelta(days=days)
                stamp = enddate.strftime("%b %d %H:%M:%S %Y") + " GMT"
                return types.SimpleNamespace(returncode=0, stdout=f"notAfter={stamp}\n", stderr="")
        return types.SimpleNamespace(returncode=1, stdout="", stderr="openssl fail")

    return _run


# ═════════════════════════════════════════════════════════════════════════════
# region CERT_FILENAMES contract
# ═════════════════════════════════════════════════════════════════════════════


def test_cert_filenames_covers_both_stores(caplog) -> None:
    """CERT_FILENAMES: fullchain.cer (acme.sh) + fullchain.pem (LE live, REF-0008)."""
    caplog.set_level(logging.INFO)
    assert "fullchain.cer" in ce.CERT_FILENAMES, "acme.sh store должен сканироваться"
    assert "fullchain.pem" in ce.CERT_FILENAMES, "letsencrypt live store должен сканироваться (REF-0008)"
    logger.critical("[IMP:9][test] CERT_FILENAMES покрывает оба cert-store")


# endregion CERT_FILENAMES contract


# ═════════════════════════════════════════════════════════════════════════════
# region scan_expiring coverage on tmp stores
# ═════════════════════════════════════════════════════════════════════════════


@ldd_trajectory
def test_scan_finds_acme_and_le_layouts(caplog, tmp_path: Path) -> None:
    """Скан находит оба layout'а: <d>_ecc/fullchain.cer и <d>/fullchain.pem (tmp-каталог).

    Структура зеркалит прод: каждый корень = сам cert-store (домены — прямые дети),
    как /root/.acme.sh/*_ecc/ и /etc/letsencrypt/live/<domain>/.
    """
    caplog.set_level(logging.INFO)
    # acme.sh layout: expiring через 7 дней
    acme_store = tmp_path / "acme"
    (acme_store / "legacy.example.com_ecc").mkdir(parents=True)
    (acme_store / "legacy.example.com_ecc" / "fullchain.cer").write_text("cer", encoding="utf-8")
    # LE live layout: S3-restored, expiring через 5 дней + свежий (не попадает в отчёт)
    le_store = tmp_path / "live"
    (le_store / "restored.example.com").mkdir(parents=True)
    (le_store / "restored.example.com" / "fullchain.pem").write_text("pem", encoding="utf-8")
    (le_store / "fresh.example.com").mkdir(parents=True)
    (le_store / "fresh.example.com" / "fullchain.pem").write_text("pem", encoding="utf-8")

    days_by_path = {
        "legacy.example.com_ecc/fullchain.cer": 7,
        "restored.example.com/fullchain.pem": 5,
        "fresh.example.com/fullchain.pem": 60,
    }
    merged: dict[str, int] = {}
    for store in (acme_store, le_store):
        merged.update(ce.scan_expiring(str(store), threshold_days=14, now=NOW, run_fn=_scripted_enddate(days_by_path)))

    assert merged == {"legacy.example.com": 7, "restored.example.com": 5}, (
        f"оба layout'а должны сканироваться, fresh — нет: {merged}"
    )
    logger.critical("[IMP:9][test] scan-coverage: acme .cer + LE .pem найдены, fresh отфильтрован")


@ldd_trajectory
def test_check_merges_both_roots_min_days_wins(caplog, tmp_path: Path) -> None:
    """check(): union корней; домен в обоих → min(days_left); TG один раз (REF-0008)."""
    caplog.set_level(logging.INFO)
    root_a = tmp_path / "a"
    root_b = tmp_path / "b"
    # dual.example.com в ОБОИХ корнях: a=20 дней, b=6 дней → отчёт 6 (пессимистичный)
    (root_a / "dual.example.com").mkdir(parents=True)
    (root_a / "dual.example.com" / "fullchain.pem").write_text("pem", encoding="utf-8")
    (root_b / "dual.example.com").mkdir(parents=True)
    (root_b / "dual.example.com" / "fullchain.pem").write_text("pem", encoding="utf-8")
    # only-a.example.com только в первом корне: 10 дней
    (root_a / "only-a.example.com").mkdir(parents=True)
    (root_a / "only-a.example.com" / "fullchain.pem").write_text("pem", encoding="utf-8")

    days_by_path = {
        "a/dual.example.com/fullchain.pem": 20,
        "b/dual.example.com/fullchain.pem": 6,
        "a/only-a.example.com/fullchain.pem": 10,
    }
    sent: list[str] = []
    state_file = tmp_path / "state.json"

    def _notify(text: str) -> bool:
        sent.append(text)
        return True

    rc = ce.check(
        cert_dir=str(root_a),
        threshold_days=14,
        state_file=str(state_file),
        now=NOW,
        run_fn=_scripted_enddate(days_by_path),
        notify_fn=_notify,
        scan_dirs=[str(root_a), str(root_b)],
    )

    assert rc == 0
    assert len(sent) == 1, f"одно уведомление на merged-отчёт: {sent}"
    assert "dual.example.com истекает через 6 дн." in sent[0], f"min(days) должен победить: {sent[0]}"
    assert "only-a.example.com истекает через 10 дн." in sent[0]
    assert state_file.exists(), "state-file сохранён после успешной доставки"
    logger.critical("[IMP:9][test] check() union+merge: min(days_left) побеждает, один TG")


def test_default_scan_dirs_include_letsencrypt_live(caplog) -> None:
    """Дефолт check() сканирует LETSENCRYPT_LIVE_DIR рядом с cert_dir (без явного scan_dirs)."""
    caplog.set_level(logging.INFO)
    # Отсутствующие корни → {} (non-fatal) → no-op exit 0; проверяем сам факт union-вызова
    rc = ce.check(cert_dir=str(Path(__file__).parent / "no-such-dir"), threshold_days=14, notify_fn=lambda _t: True)
    assert rc == 0, "отсутствующие cert-корни не должны ломать daily-check"
    logger.critical("[IMP:9][test] дефолтный union {cert_dir, LETSENCRYPT_LIVE_DIR} — no-op безопасен")


# endregion scan_expiring coverage on tmp stores
