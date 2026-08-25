"""
# GREP_SUMMARY: test ssl-certs pair-match pubkey openssl x509 pkey pubout cert-is-valid key-path mismatch missing REF-0008
# STRUCTURE: ▶ real-openssl pair fixtures (tmp_path) → ◇ cert_key_pair_matches: match/mismatch/missing/garbage
#            → ◇ cert_is_valid(key_path=…) wiring via fake ssl_certs.subprocess: pair OK → True │ MISMATCH → False ⎋ IMP:9
# region MODULE_CONTRACT
## @purpose  Unit-тесты pair-match (REF-0008 подпункты 1/2): cert_key_pair_matches (pubkey-match
##           cert↔key) и проводка key_path в shared/ssl_certs.cert_is_valid. Класс бага FAIL-0300:
##           несогласованная пара «valid on disk» → nginx SSL_CTX_use_PrivateKey_file mismatch.
## @scope    cert_key_pair_matches — РЕАЛЬНЫЙ openssl на tmp-фикстурах (генерация self-signed пар);
##           cert_is_valid(key_path=) — fake ssl_certs.subprocess (паттерн test_s3_ssl_cache DI-KEEP).
## @invariants
##   - Все файлы в tmp_path (Zero Hardcode)
##   - cert_key_pair_matches никогда не raise: garbage/missing → False
##   - Каждый тест валидирует IMP:9 через ldd_trajectory
## @rationale REF-0008: restore из S3 считал пару валидной по одному fullchain.pem — DR-рестарт
##            ноды давал TLS outage всех доменов. Pair-match обязателен до commit на диск.
## @changes  2026-08-24 | REF-0008 (meta-refactoring В2) — Created
# endregion MODULE_CONTRACT
"""

import logging
import subprocess as _sp
import types
from pathlib import Path

import pytest

from core.internal.shared import ssl_certs
from core.internal.shared.ssl_certs import cert_is_valid, cert_key_pair_matches
from tests._conftest.ldd import ldd_trajectory

pytestmark = pytest.mark.static_audit

logger = logging.getLogger(__name__)

# QA R11/T2.F: соответствие issue_cert.KEY_LENGTH → openssl-кривая
_EC_CURVE_BY_KEYTYPE = {"ec-256": "prime256v1"}


def _gen_pair(tmp_path: Path, name: str, cn: str, key_type: str = "rsa:2048") -> tuple[Path, Path]:
    """Сгенерировать самоподписанную пару cert+key РЕАЛЬНЫМ openssl (tmp_path, Zero Hardcode).

    QA R11/T2.F: key_type параметризован — prod KEY_LENGTH='ec-256' (issue_cert.py) рядом
    с rsa:2048; pair-match обязан работать на обоих типах ключей.
    """
    cert_path = tmp_path / f"{name}-fullchain.pem"
    key_path = tmp_path / f"{name}-privkey.pem"
    # openssl -newkey синтаксис: rsa:N — напрямую; EC — 'ec' + pkeyopt кривой
    # (issue_cert.KEY_LENGTH='ec-256' ↔ openssl кривая prime256v1)
    newkey_args = (
        ["rsa:2048"]
        if key_type == "rsa:2048"
        else ["ec", "-pkeyopt", f"ec_paramgen_curve:{_EC_CURVE_BY_KEYTYPE.get(key_type, 'prime256v1')}"]
    )
    cmd = [
        "openssl",
        "req",
        "-x509",
        "-newkey",
        *newkey_args,
        "-nodes",
        "-keyout",
        str(key_path),
        "-out",
        str(cert_path),
        "-days",
        "3650",
        "-subj",
        f"/CN={cn}",
    ]
    proc = _sp.run(cmd, capture_output=True, text=True, timeout=60, check=False)
    assert proc.returncode == 0, f"openssl fixture generation failed ({key_type}): {proc.stderr[:200]}"
    return cert_path, key_path


# ═════════════════════════════════════════════════════════════════════════════
# region cert_key_pair_matches — REAL openssl (valid / mismatch / missing)
# ═════════════════════════════════════════════════════════════════════════════


@ldd_trajectory
def test_pair_match_valid(caplog, tmp_path: Path) -> None:
    """Согласованная пара (pubkey cert == pubout key) → True."""
    caplog.set_level(logging.INFO)
    cert_path, key_path = _gen_pair(tmp_path, "valid", "example.com")

    assert cert_key_pair_matches(str(cert_path), str(key_path)) is True, (
        "согласованная пара обязана проходить pubkey-match"
    )
    logger.critical("[IMP:9][test] pair-match VALID — matched pair accepted")


# 🧪 TRAP[TEST] · 2026-08-25 · Regression · QA R11/T2.F — EC pair-match (прод KEY_LENGTH)
# · Scenario: prod выпускает ec-256 (issue_cert.KEY_LENGTH), а pair-match гонялся только на
#   rsa:2048 — несовместимость pubkey-извлечения для EC-ключей осталась бы незамеченной до DR
# · Last fail: N/A (preventive coverage)
# · Remove if: prod KEY_LENGTH сменит тип (обновить параметризацию)
@pytest.mark.parametrize("key_type", ["rsa:2048", "ec-256"])
def test_pair_match_valid_by_key_type(caplog, tmp_path: Path, key_type: str) -> None:
    """Согласованная пара для каждого прод-типа ключа → True (rsa:2048 + ec-256)."""
    caplog.set_level(logging.INFO)
    cert_path, key_path = _gen_pair(tmp_path, f"valid-{key_type.replace(':', '-')}", "example.com", key_type)

    assert cert_key_pair_matches(str(cert_path), str(key_path)) is True, (
        f"pair-match обязан работать на {key_type} (прод-тип issue_cert.KEY_LENGTH)"
    )
    logger.critical("[IMP:9][test] pair-match VALID for %s", key_type)


# 🧪 TRAP[TEST] · 2026-08-25 · NEGATIVE (R5) · QA R11/T2.F — кросс-типовая пара отвергается
# · Scenario: EC-cert + RSA-key — pubkey-матч обязан провалиться независимо от типов
# · Last fail: N/A (preventive)
# · Remove if: вместе с pair-match механизмом
def test_pair_match_cross_key_type_rejected(caplog, tmp_path: Path) -> None:
    """EC-cert против RSA-key → False (pubkey не совпадают по определению)."""
    caplog.set_level(logging.INFO)
    cert_ec, _ = _gen_pair(tmp_path, "ec", "ec.example.com", "ec-256")
    _, key_rsa = _gen_pair(tmp_path, "rsa", "rsa.example.com", "rsa:2048")

    assert cert_key_pair_matches(str(cert_ec), str(key_rsa)) is False, "кросс-типовая пара обязана отвергаться"
    logger.critical("[IMP:9][test] pair-match cross-type rejected")


@ldd_trajectory
def test_pair_match_mismatch_rejected(caplog, tmp_path: Path) -> None:
    """Несогласованная пара (cert из выпуска A + key из выпуска B) → False (REF-0008 FAIL-0300)."""
    caplog.set_level(logging.INFO)
    cert_a, key_a = _gen_pair(tmp_path, "a", "a.example.com")
    _cert_b, key_b = _gen_pair(tmp_path, "b", "b.example.com")

    assert cert_key_pair_matches(str(cert_a), str(key_b)) is False, "чужой ключ должен отвергаться"
    # Sanity: родная пара того же выпуска проходит (мисматч не из-за кривых фикстур)
    assert cert_key_pair_matches(str(cert_a), str(key_a)) is True
    assert any("MISMATCH" in r.message for r in caplog.records), "IMP:8 WARN о mismatch обязателен"
    logger.critical("[IMP:9][test] pair-match MISMATCH — foreign key rejected (FAIL-0300 regression)")


@ldd_trajectory
def test_pair_match_missing_files_nonfatal(caplog, tmp_path: Path) -> None:
    """Отсутствующий cert/key → False, БЕЗ raise (non-fatal канон ssl_certs)."""
    caplog.set_level(logging.INFO)
    cert_path, _key = _gen_pair(tmp_path, "present", "example.com")

    assert cert_key_pair_matches(str(cert_path), str(tmp_path / "no-such-key.pem")) is False
    assert cert_key_pair_matches(str(tmp_path / "no-such-cert.pem"), str(tmp_path / "also-missing.pem")) is False
    logger.critical("[IMP:9][test] pair-match MISSING files — False без raise")


@ldd_trajectory
def test_pair_match_garbage_content_nonfatal(caplog, tmp_path: Path) -> None:
    """Мусор вместо PEM → False (openssl rc!=0 проглатывается)."""
    caplog.set_level(logging.INFO)
    cert = tmp_path / "garbage-fullchain.pem"
    key = tmp_path / "garbage-privkey.pem"
    cert.write_text("not a pem", encoding="utf-8")
    key.write_text("also not a pem", encoding="utf-8")

    assert cert_key_pair_matches(str(cert), str(key)) is False
    logger.critical("[IMP:9][test] pair-match GARBAGE — graceful False")


# endregion cert_key_pair_matches — REAL openssl (valid / mismatch / missing)


# ═════════════════════════════════════════════════════════════════════════════
# region cert_is_valid(key_path=…) wiring — fake ssl_certs.subprocess
# ═════════════════════════════════════════════════════════════════════════════

_PUBKEY_A = "-----BEGIN PUBLIC KEY-----\nAAAAkeyA\n-----END PUBLIC KEY-----\n"
_PUBKEY_B = "-----BEGIN PUBLIC KEY-----\nBBBBkeyB\n-----END PUBLIC KEY-----\n"


def _fake_ssl_subprocess(pubkey_cert: str, pubkey_key: str):
    """Fake ssl_certs.subprocess: parseable OK, LE issuer, заданные pubkey-выводы."""

    def _run(cmd, **_kwargs):
        joined = " ".join(cmd)
        if "-pubkey" in joined:
            return _sp.CompletedProcess(cmd, 0, stdout=pubkey_cert, stderr="")
        if "-pubout" in joined:
            return _sp.CompletedProcess(cmd, 0, stdout=pubkey_key, stderr="")
        if "-issuer" in joined:
            return _sp.CompletedProcess(cmd, 0, stdout="issuer=C = US, O = Let's Encrypt, CN = R11", stderr="")
        # -noout parse / прочие → rc 0
        return _sp.CompletedProcess(cmd, 0, stdout="", stderr="")

    return types.SimpleNamespace(
        run=_run,
        TimeoutExpired=_sp.TimeoutExpired,
        FileNotFoundError=FileNotFoundError,
        OSError=OSError,
    )


@pytest.mark.parametrize(
    ("cert_pub", "key_pub", "expected"),
    [
        (_PUBKEY_A, _PUBKEY_A, True),
        (_PUBKEY_A, _PUBKEY_B, False),
    ],
)
def test_cert_is_valid_key_path_wiring(
    monkeypatch, tmp_path: Path, cert_pub: str, key_pub: str, *, expected: bool
) -> None:
    """cert_is_valid(key_path=…): согласованная пара → True; несогласованная → False (REF-0008 п.2)."""
    cert_path = tmp_path / "fullchain.pem"
    key_path = tmp_path / "privkey.pem"
    cert_path.write_text("fake pem", encoding="utf-8")
    key_path.write_text("fake pem", encoding="utf-8")
    monkeypatch.setattr(ssl_certs, "subprocess", _fake_ssl_subprocess(cert_pub, key_pub))

    result = cert_is_valid(str(cert_path), expected_domains=None, check_expiry=False, key_path=str(key_path))

    assert result is expected, f"pair {'OK' if expected else 'MISMATCH'} должен давать {expected}"
    logger.critical("[IMP:9][test] cert_is_valid key_path wiring: %s", "match→True" if expected else "mismatch→False")


def test_cert_is_valid_without_key_path_unchanged(monkeypatch, tmp_path: Path) -> None:
    """key_path=None (дефолт) — pair-check пропускается, прежняя семантика 1:1 (additive-only)."""
    cert_path = tmp_path / "fullchain.pem"
    cert_path.write_text("fake pem", encoding="utf-8")
    monkeypatch.setattr(ssl_certs, "subprocess", _fake_ssl_subprocess(_PUBKEY_A, _PUBKEY_B))

    # Пара рассинхронизирована, НО key_path не передан → pair-check не выполняется
    result = cert_is_valid(str(cert_path), check_expiry=False)

    assert result is True, "без key_path прежнее поведение (только parseable+LE) должно сохраняться"
    logger.critical("[IMP:9][test] cert_is_valid без key_path — прежняя семантика сохранена")


# endregion cert_is_valid(key_path=…) wiring — fake ssl_certs.subprocess
