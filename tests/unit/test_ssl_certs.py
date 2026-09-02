# GREP_SUMMARY: test-shared-ssl-certs openssl x509 parseable expiry issuer lets-encrypt checkend constants san wildcard cn-fallback on-disk-coverage wildcard-parent
# STRUCTURE: ▶ test_parseable [ok|fail|timeout] → test_expiry [ok|expired] → test_issuer [le|non-le|fail] → test_constants
#            → ▶ REAL-openssl certs (tmp_path): san-only → wildcard → cn-fallback → san-no-cn-fallback → R5-original-bug
# region MODULE_CONTRACT
## @purpose  Unit tests for core/internal/shared/ssl_certs.py — единый SoT openssl-примитивов (DevPlan 117 D21)
##           + SAN-aware domain matching (DevPlan 004 W1/W2 — реальные openssl-сертификаты).
## @scope    Tests: cert_is_parseable, cert_check_expiry, cert_get_issuer, cert_is_le_issuer, константы;
##           W2: cert_get_san_list, _cert_covers_domain (SAN primary/CN fallback/wildcard), cert_is_valid
##           на РЕАЛЬНЫХ openssl-сертификатах (SAN-only LE-style, CN-only legacy, wildcard, SAN+CN).
## @invariants
##   - Мок-секции (parseable/expiry/issuer/CLI): openssl вызовы мокаются (subprocess.run)
##   - W2 SAN-секции: РЕАЛЬНЫЕ openssl-сертификаты в tmp_path (TRAP[TEST]-принцип: моки subprocess
##     не ловят класс SAN-only — нужны настоящие PEM из openssl); monkeypatch cert_is_le_issuer
##     (issuer self-generated; существующий DI-паттерн — cert_is_valid вызывает точку модуля)
##   - Non-fatal контракт: subprocess ошибки → False/None/[] (никогда не raise)
##   - LDD: IMP:9 в успешных сценариях (assert в каждом тесте)
## @changes 2026-08-01 | DevPlan 117 D21 — создан
## @changes 2026-08-16 | DevPlan 004 W2 — +SAN-aware секция: _make_cert helper (openssl в tmp_path),
##           test_cert_is_valid_san_only_cert / _san_wildcard / _cn_fallback / _san_present_no_cn_fallback /
##           _san_only_original_bug (R5), параметризованные subject-паттерны cert_subject_matches_domain
## @changes 2026-09-02 | DevPlan 030 TASK-1 — +TEST_CERT_COVERS_DOMAIN секция (cert_covers_domain
##           direct|wildcard-parent|none, F14; 0 subprocess — cert_get_subject мокается monkeypatch)
# endregion MODULE_CONTRACT

import logging
import subprocess
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

import core.internal.shared.ssl_certs as ssl_certs_module
from core.internal.shared.ssl_certs import (
    DEFAULT_EXPIRY_THRESHOLD,
    DEFAULT_OPENSSL_TIMEOUT,
    cert_check_expiry,
    cert_covers_domain,
    cert_get_issuer,
    cert_get_san_list,
    cert_is_le_issuer,
    cert_is_parseable,
    cert_is_valid,
    cert_subject_matches_domain,
)
from core.internal.shared.ssl_certs import (
    main as ssl_certs_cli_main,
)
from tests.helpers.gate_helpers import assert_ldd_imp9

pytestmark = pytest.mark.static_audit

logger = logging.getLogger(__name__)


# T2.16a: _assert_imp9 консолидирован в gate_helpers.assert_ldd_imp9
# region TEST_constants
def test_default_constants(caplog: pytest.LogCaptureFixture) -> None:
    """DEFAULT_OPENSSL_TIMEOUT=10, DEFAULT_EXPIRY_THRESHOLD=2592000 (30 дней)."""
    caplog.set_level(logging.INFO)
    assert DEFAULT_OPENSSL_TIMEOUT == 10
    assert DEFAULT_EXPIRY_THRESHOLD == 2592000
    logger.info(
        "[IMP:9][test][constants] DEFAULT_OPENSSL_TIMEOUT=%d DEFAULT_EXPIRY_THRESHOLD=%d",
        DEFAULT_OPENSSL_TIMEOUT,
        DEFAULT_EXPIRY_THRESHOLD,
    )


# endregion TEST_constants


# region TEST_cert_is_parseable
def test_parseable_ok(caplog: pytest.LogCaptureFixture) -> None:
    """openssl x509 -noout returncode=0 → True."""
    caplog.set_level(logging.INFO)
    with patch("core.internal.shared.ssl_certs.subprocess.run") as mock_run:
        mock_run.return_value = MagicMock(returncode=0)
        assert cert_is_parseable("/tmp/cert.pem") is True


def test_parseable_fail(caplog: pytest.LogCaptureFixture) -> None:
    """openssl returncode!=0 → False (corrupt cert)."""
    caplog.set_level(logging.INFO)
    with patch("core.internal.shared.ssl_certs.subprocess.run") as mock_run:
        mock_run.return_value = MagicMock(returncode=1)
        assert cert_is_parseable("/tmp/cert.pem") is False


def test_parseable_timeout(caplog: pytest.LogCaptureFixture) -> None:
    """openssl TimeoutExpired → False (никогда не raise)."""
    caplog.set_level(logging.INFO)
    with patch("core.internal.shared.ssl_certs.subprocess.run", side_effect=TimeoutError("timeout")):
        assert cert_is_parseable("/tmp/cert.pem") is False


# endregion TEST_cert_is_parseable


# region TEST_cert_check_expiry
def test_expiry_ok(caplog: pytest.LogCaptureFixture) -> None:
    """openssl -checkend returncode=0 → True (>threshold осталось)."""
    caplog.set_level(logging.INFO)
    with patch("core.internal.shared.ssl_certs.subprocess.run") as mock_run:
        mock_run.return_value = MagicMock(returncode=0)
        assert cert_check_expiry("/tmp/cert.pem", 2592000) is True
        # Threshold передаётся в команду
        args = mock_run.call_args.args[0]
        assert "2592000" in args


def test_expiry_expired(caplog: pytest.LogCaptureFixture) -> None:
    """openssl -checkend returncode!=0 → False (истёк / в пределах порога)."""
    caplog.set_level(logging.INFO)
    with patch("core.internal.shared.ssl_certs.subprocess.run") as mock_run:
        mock_run.return_value = MagicMock(returncode=1)
        assert cert_check_expiry("/tmp/cert.pem", 2592000) is False


# 🧪 TRAP[TEST] · 2026-08-02 · Regression · cert_check_expiry использует DEFAULT_OPENSSL_TIMEOUT (B5)
# · Scenario: timeout kwarg == DEFAULT_OPENSSL_TIMEOUT (10), не литерал 30
# · Last fail: cert_orchestrator/nginx_harness — timeout=30 для openssl (дубль SoT, AUDIT-4 T4)
# · Remove if: ssl_certs перестаёт выполнять openssl subprocess
def test_openssl_timeout_default(caplog: pytest.LogCaptureFixture) -> None:
    """cert_check_expiry передаёт timeout=DEFAULT_OPENSSL_TIMEOUT в openssl subprocess (B5)."""
    caplog.set_level(logging.INFO)
    with patch("core.internal.shared.ssl_certs.subprocess.run") as mock_run:
        mock_run.return_value = MagicMock(returncode=0)
        assert cert_check_expiry("/tmp/cert.pem", 2592000) is True
        assert mock_run.call_args.kwargs["timeout"] == DEFAULT_OPENSSL_TIMEOUT
    logger.info("[IMP:9][test][openssl_timeout] cert_check_expiry timeout=%s (канон)", DEFAULT_OPENSSL_TIMEOUT)


# endregion TEST_cert_check_expiry


# region TEST_cert_get_issuer
def test_get_issuer_ok(caplog: pytest.LogCaptureFixture) -> None:
    """openssl -issuer returncode=0 → issuer строка (stripped)."""
    caplog.set_level(logging.INFO)
    with patch("core.internal.shared.ssl_certs.subprocess.run") as mock_run:
        mock_run.return_value = MagicMock(returncode=0, stdout="issuer=C = US, O = Let's Encrypt, CN = R11\n")
        issuer = cert_get_issuer("/tmp/cert.pem")
        assert issuer == "issuer=C = US, O = Let's Encrypt, CN = R11"


def test_get_issuer_fail(caplog: pytest.LogCaptureFixture) -> None:
    """openssl returncode!=0 → None."""
    caplog.set_level(logging.INFO)
    with patch("core.internal.shared.ssl_certs.subprocess.run") as mock_run:
        mock_run.return_value = MagicMock(returncode=1, stdout="")
        assert cert_get_issuer("/tmp/cert.pem") is None


def test_get_issuer_empty(caplog: pytest.LogCaptureFixture) -> None:
    """openssl успех, но пустой issuer → None (пустая строка не является валидным issuer)."""
    caplog.set_level(logging.INFO)
    with patch("core.internal.shared.ssl_certs.subprocess.run") as mock_run:
        mock_run.return_value = MagicMock(returncode=0, stdout="   \n")
        assert cert_get_issuer("/tmp/cert.pem") is None


# endregion TEST_cert_get_issuer


# region TEST_cert_is_le_issuer
def test_is_le_issuer_accepts_le(caplog: pytest.LogCaptureFixture) -> None:
    """Issuer содержит 'Let's Encrypt' → True."""
    caplog.set_level(logging.INFO)
    with patch("core.internal.shared.ssl_certs.subprocess.run") as mock_run:
        mock_run.return_value = MagicMock(returncode=0, stdout="issuer=C = US, O = Let's Encrypt, CN = R11\n")
        assert cert_is_le_issuer("/tmp/cert.pem") is True


def test_is_le_issuer_rejects_mkcert(caplog: pytest.LogCaptureFixture) -> None:
    """Issuer mkcert/self-signed → False (P0 regression, DevPlan 117 D21)."""
    caplog.set_level(logging.INFO)
    with patch("core.internal.shared.ssl_certs.subprocess.run") as mock_run:
        mock_run.return_value = MagicMock(returncode=0, stdout="issuer=O = mkcert development CA, CN = mkcert\n")
        assert cert_is_le_issuer("/tmp/cert.pem") is False
        assert_ldd_imp9(caplog, needle="not Let's Encrypt")


def test_is_le_issuer_openssl_failure(caplog: pytest.LogCaptureFixture) -> None:
    """openssl возвращает ненулевой код → False (никогда не raise)."""
    caplog.set_level(logging.INFO)
    with patch("core.internal.shared.ssl_certs.subprocess.run") as mock_run:
        mock_run.return_value = MagicMock(returncode=1, stdout="")
        assert cert_is_le_issuer("/tmp/cert.pem") is False


# endregion TEST_cert_is_le_issuer


# ═══════════════════════════════════════════════════════════════════════════
# region TEST_cert_is_valid (DevPlan 118 C9 — единая комбинация)
# ═══════════════════════════════════════════════════════════════════════════


def _fake_run_results(
    caplog: pytest.LogCaptureFixture,
    *,
    parseable=True,
    issuer="Let's Encrypt",
    subject=None,
    checkend=True,
) -> patch:
    """Вернуть патчер subprocess.run с fake openssl-результатами (C9 — комбинация живёт в shared)."""
    import subprocess as _sp

    def _run(cmd, **kwargs):
        joined = " ".join(cmd)
        if "-noout" in joined and "-subject" not in joined and "-issuer" not in joined and "-checkend" not in joined:
            return _sp.CompletedProcess(cmd, 0 if parseable else 1, stdout="", stderr="")
        if "-issuer" in joined:
            out = f"issuer=O = {issuer}" if issuer else ""
            return _sp.CompletedProcess(cmd, 0 if issuer else 1, stdout=out, stderr="")
        if "-subject" in joined:
            out = subject or "subject=CN = example.com"
            return _sp.CompletedProcess(cmd, 0, stdout=out, stderr="")
        if "-checkend" in joined:
            return _sp.CompletedProcess(cmd, 0 if checkend else 1, stdout="", stderr="")
        return _sp.CompletedProcess(cmd, 0, stdout="", stderr="")

    caplog.set_level(logging.INFO)
    return patch("core.internal.shared.ssl_certs.subprocess.run", _run)


# 🧪 TRAP[TEST] · Regression · cert_is_valid — все проверки OK (C9)
# · Scenario: parseable + LE + domain match + expiry → True
# · Last fail: три реализации «валиден» расходились (s3_ssl_cache/cert_orchestrator/context_deployer)
# · Remove if: cert_is_valid combination changes
# GUARD-PRESERVE (168): P0-mkcert guard — единственное покрытие happy-path комбинации cert_is_valid (C9), R5-база для негативов
def test_cert_is_valid_ok(caplog: pytest.LogCaptureFixture) -> None:
    """Все проверки проходят → True."""
    with _fake_run_results(caplog):
        assert cert_is_valid("/tmp/cert.pem", expected_domains="example.com") is True


# 🧪 TRAP[TEST] · NEGATIVE (R5) · cert_is_valid — 3 R5-негатива консолидированы (168 Batch 9, приём P)
# · Scenario: expired (checkend fail) / non-LE (mkcert issuer, P0) / domain mismatch (expected_domains≠subject) → False
# · Last fail: 052 expired certs restored; 2026-07-22 P0 — только expiry проверялся; 052 wrong domain's cert served
# · Remove if: соответствующие проверки удалены из cert_is_valid (P0 mkcert — NEVER)
@pytest.mark.parametrize(
    "run_kwargs,valid_kwargs",
    [
        ({"checkend": False}, {}),  # expired — -checkend returncode!=0
        ({"issuer": "mkcert development CA"}, {}),  # non-LE — P0 mkcert certs survived bootstrap
        ({"subject": "subject=CN = example.com"}, {"expected_domains": "other.com"}),  # domain mismatch
    ],
    ids=["expired", "not_le", "domain_mismatch"],
)
def test_cert_is_valid_negative(caplog: pytest.LogCaptureFixture, run_kwargs: dict, valid_kwargs: dict) -> None:
    """NEGATIVE (R5): expired / non-LE / domain mismatch → cert_is_valid(...) is False (C9)."""
    with _fake_run_results(caplog, **run_kwargs):
        assert cert_is_valid("/tmp/cert.pem", **valid_kwargs) is False


# 🧪 TRAP[TEST] · Regression · cert_is_valid — expected_domains=None пропускает domain-check (C9)
# · Scenario: без expected_domains → True (cert_orchestrator/context_deployer семантика)
# · Last fail: cert_orchestrator._is_cert_valid — только expiry+LE
# · Remove if: domain-check opt-in semantics changes
# GUARD-PRESERVE (168): единственное покрытие ветки expected_domains=None (opt-in domain-check, cert_orchestrator/context_deployer семантика)
def test_cert_is_valid_no_domains_skips_domain_check(caplog: pytest.LogCaptureFixture) -> None:
    """expected_domains=None → domain-check пропускается (True)."""
    with _fake_run_results(caplog):
        assert cert_is_valid("/tmp/cert.pem") is True


# 🧪 TRAP[TEST] · Regression · cert_is_valid — check_expiry=False пропускает expiry (C9)
# · Scenario: check_expiry=False → True даже при checkend fail (s3 download семантика)
# · Last fail: s3_ssl_cache.download_cert check_expiry=False
# · Remove if: check_expiry opt-out semantics changes
# GUARD-PRESERVE (168): единственное покрытие ветки check_expiry=False (opt-out expiry, s3 download семантика)
def test_cert_is_valid_check_expiry_false(caplog: pytest.LogCaptureFixture) -> None:
    """check_expiry=False → expiry-check пропускается (s3 download семантика)."""
    with _fake_run_results(caplog, checkend=False):
        assert cert_is_valid("/tmp/cert.pem", check_expiry=False) is True


# endregion TEST_cert_is_valid


# ═══════════════════════════════════════════════════════════════════════════
# region TEST_SAN_AWARE (DevPlan 004 W2 — РЕАЛЬНЫЕ openssl-сертификаты)
# ═══════════════════════════════════════════════════════════════════════════


def _make_cert(tmp_path: Path, subject: str, san: str | None = None, days: int = 60) -> Path:
    """Сгенерировать РЕАЛЬНЫЙ openssl-сертификат в tmp_path (TRAP[TEST]-принцип W2).

    ▶ ┌tmp_path, subject, san?┐ → ⚡ openssl req -x509 -newkey rsa:2048 (-addext SAN)?
      → ⎋ Path (cert.pem)

    ## @purpose — Моки subprocess не ловят класс SAN-only (subj "/" + SAN): нужны настоящие
    ##            PEM из openssl. subprocess в тестах разрешён для генерации фикстур.
    ## @io — ⇥ tmp_path: Path; subject: str (openssl -subj; "/" = SAN-only LE-style);
    ##       san: str | None (DNS-имя для subjectAltName; None = без -addext);
    ##       days: int (срок) → ⎋ Path к cert.pem
    ## @invariants
    ##   - SAN-only LE-style: subject="/", san="example.com" (subject пуст — как современные LE)
    ##   - CN-only legacy: subject="/CN=example.com", san=None
    ##   - Wildcard: san="*.example.com"
    ##   - check=True: fail-fast если openssl недоступен/сломан (окружение dev-машины)
    """
    cert = tmp_path / f"cert_{abs(hash((subject, san, days)))}.pem"
    key = tmp_path / f"key_{abs(hash((subject, san, days)))}.pem"
    cmd = [
        "openssl",
        "req",
        "-x509",
        "-nodes",
        "-newkey",
        "rsa:2048",
        "-days",
        str(days),
        "-subj",
        subject,
        "-keyout",
        str(key),
        "-out",
        str(cert),
    ]
    if san is not None:
        cmd += ["-addext", f"subjectAltName=DNS:{san}"]
    subprocess.run(cmd, check=True, capture_output=True, timeout=30)
    return cert


@pytest.fixture
def le_issuer_always_true(monkeypatch: pytest.MonkeyPatch) -> None:
    """Патч cert_is_le_issuer → True: issuer у тест-сертов self-generated (DI-паттерн W2).

    cert_is_valid вызывает точку модуля (cert_is_le_issuer разрешается в namespace
    ssl_certs на момент вызова) — патчим атрибут модуля.
    """
    monkeypatch.setattr(ssl_certs_module, "cert_is_le_issuer", lambda *_a, **_k: True)


# 🧪 TRAP[TEST] · 2026-08-16 · Regression · SAN-only LE-style сертификат проходит cert_is_valid (AC1)
# · Scenario: openssl-сертификат subject="/" + SAN=DNS:example.com; expected_domains="example.com" → True
# · Last fail: bootstrap 2026-08-16 — SAN-only certs rejected on restore → re-issue (LE rate-limit риск)
# · Remove if: SAN-aware matching удалён из ssl_certs (возврат CN-only)
def test_cert_is_valid_san_only_cert(
    tmp_path: Path, caplog: pytest.LogCaptureFixture, le_issuer_always_true: None
) -> None:
    """SAN-only серт (subject пуст) + expected_domains → True (AC1, W1 SAN primary)."""
    caplog.set_level(logging.INFO)
    cert = _make_cert(tmp_path, subject="/", san="example.com")
    assert cert_get_san_list(str(cert)) == ["example.com"], "SAN extraction must return the DNS entry"
    assert cert_is_valid(str(cert), expected_domains="example.com") is True
    assert_ldd_imp9(caplog, needle="cert_is_valid: OK")


# 🧪 TRAP[TEST] · 2026-08-16 · Regression · Wildcard SAN — одноуровневая семантика (AC2)
# · Scenario: SAN=*.example.com: app.example.com → True; apex example.com → False,
#   a.b.example.com → False, other.com → False. РАСХОЖДЕНИЕ с формулировкой DevPlan AC2
#   («покрывает example.com и app.example.com»): канон verify_sweep/tls_check.san_matches_domain —
#   wildcard НЕ покрывает apex (пример: «*.example.com» не соответствует «example.com»,
#   RFC 6125 §6.4.3 — wildcard матчит ровно одну метку слева). Семантика canonical wildcard
#   одноуровневая — следует канону verify_sweep, НЕ буквальной формулировке AC2.
# · Last fail: N/A (new — W1 wildcard semantics)
# · Remove if: wildcard-семантика меняется (многоуровневый wildcard / apex-покрытие)
@pytest.mark.parametrize(
    "domain,expected",
    [
        ("app.example.com", True),  # ровно одна метка слева — wildcard покрывает
        ("example.com", False),  # apex НЕ покрывается wildcard (канон verify_sweep, не AC2-буквально)
        ("a.b.example.com", False),  # две метки слева — wildcard не покрывает
        ("other.com", False),  # чужой домен
    ],
    ids=["one_label", "apex_not_covered", "two_labels", "other_domain"],
)
def test_cert_is_valid_san_wildcard(
    tmp_path: Path,
    caplog: pytest.LogCaptureFixture,
    le_issuer_always_true: None,
    domain: str,
    expected: bool,
) -> None:
    """SAN=*.example.com: одноуровневое wildcard-покрытие (app.example.com True; apex False)."""
    caplog.set_level(logging.INFO)
    cert = _make_cert(tmp_path, subject="/", san="*.example.com")
    result = cert_is_valid(str(cert), expected_domains=domain)
    assert result is expected
    if expected:
        assert_ldd_imp9(caplog, needle="cert_is_valid: OK")
    else:
        assert any("SAN/CN does not match" in r.message for r in caplog.records), "mismatch must log IMP:8 reason"


# 🧪 TRAP[TEST] · 2026-08-16 · Regression · CN-only legacy сертификат — CN-fallback работает (AC3)
# · Scenario: openssl-сертификат subject="/CN=example.com" без SAN → cert_is_valid True;
#   параметризованные subject-строки для cert_subject_matches_domain (pure-string, без subprocess)
# · Last fail: N/A ( RFC2253/trailing-dot паттерны — new W1 T1.3)
# · Remove if: CN-fallback ветка удалена (SAN-only будущее)
@pytest.mark.parametrize(
    "subject",
    [
        "subject=CN = example.com",  # slashed-формат с пробелами
        "subject=CN= example.com",  # slashed-формат, = и пробел
        "subject=CN=example.com",  # RFC2253 без пробелов (W1)
        "subject=CN=example.com.",  # trailing dot (W1)
        "subject=CN = example.com.",  # trailing dot + пробелы (W1)
    ],
    ids=["spaced", "eq_space", "rfc2253", "trailing_dot", "trailing_dot_spaced"],
)
def test_cert_subject_matches_domain_patterns(subject: str, caplog: pytest.LogCaptureFixture) -> None:
    """cert_subject_matches_domain: slashed + RFC2253 + trailing-dot паттерны (T1.3)."""
    caplog.set_level(logging.INFO)
    assert cert_subject_matches_domain(subject, "example.com") is True
    assert cert_subject_matches_domain(subject, "other.com") is False
    logger.info("[IMP:9][test][subject_patterns] %r matches example.com, not other.com", subject)


def test_cert_is_valid_cn_fallback(
    tmp_path: Path, caplog: pytest.LogCaptureFixture, le_issuer_always_true: None
) -> None:
    """CN-only legacy серт (без SAN) → True через CN-fallback (AC3)."""
    caplog.set_level(logging.INFO)
    cert = _make_cert(tmp_path, subject="/CN=example.com", san=None)
    assert cert_get_san_list(str(cert)) == [], "CN-only cert must have empty SAN list"
    assert cert_is_valid(str(cert), expected_domains="example.com") is True
    assert_ldd_imp9(caplog, needle="cert_is_valid: OK")


# 🧪 TRAP[TEST] · 2026-08-16 · Regression · SAN present → CN non-authoritative (TRAP[DECISION] T1.2)
# · Scenario: серт SAN=other.com + CN=example.com, expected_domains="example.com" → False
#   (CN-fallback при непустом SAN запрещён — RFC 6125; иначе «CN совпал случайно»)
# · Last fail: N/A (new — W1 SAN-deprecates-CN)
# · Remove if: появление legacy-сертификатов с рассинхроном CN/SAN (Rev TRAP[DECISION])
def test_cert_is_valid_san_present_no_cn_fallback(
    tmp_path: Path, caplog: pytest.LogCaptureFixture, le_issuer_always_true: None
) -> None:
    """SAN=other.com + CN=example.com, expected example.com → False (CN non-authoritative)."""
    caplog.set_level(logging.INFO)
    cert = _make_cert(tmp_path, subject="/CN=example.com", san="other.com")
    assert cert_is_valid(str(cert), expected_domains="example.com") is False
    assert any("SAN/CN does not match" in r.message for r in caplog.records)
    # Контр-кейс: тот же серт, expected=other.com → True (SAN матчится)
    assert cert_is_valid(str(cert), expected_domains="other.com") is True
    assert_ldd_imp9(caplog, needle="cert_is_valid: OK")


# 🧪 TRAP[TEST] · NEGATIVE (R5) · SAN-only original bug — исходная форма бага DevPlan 004
# · Scenario: SAN-only серт (subject пуст), expected_domains=domain → True. До фикса W1
#   cert_is_valid матчил ТОЛЬКО по subject-CN → SAN-only серт отвергался («Cached cert failed
#   validation» → S3 miss → пере-выпуск → LE rate-limit 50/домен/нед).
# · Last fail: bootstrap 2026-08-16 — SAN-only certs rejected on restore → re-issue
# · Remove if: SAN-aware matching удалён из ssl_certs
def test_cert_is_valid_san_only_original_bug(
    tmp_path: Path, caplog: pytest.LogCaptureFixture, le_issuer_always_true: None
) -> None:
    """R5: исходная форма бага (SAN-only, subject пуст) больше НЕ отвергается."""
    caplog.set_level(logging.INFO)
    cert = _make_cert(tmp_path, subject="/", san="botanika.tronyx.ru")
    # Исходный вход бага: валидный LE-серт из S3-кеша, subject пуст, SAN=domain
    assert cert_is_valid(str(cert), expected_domains="botanika.tronyx.ru") is True
    assert_ldd_imp9(caplog, needle="cert_is_valid: OK")


# endregion TEST_SAN_AWARE


# ═══════════════════════════════════════════════════════════════════════════
# region TEST_CERT_COVERS_DOMAIN (DevPlan 030 TASK-1 — F14 on-disk coverage)
# ═══════════════════════════════════════════════════════════════════════════


# 🧪 TRAP[TEST] · 2026-09-02 · Regression · cert_covers_domain — wildcard-parent покрытие (F14)
# · Scenario: roadmap.asiteam.ru покрыт *.asiteam.ru (live/asiteam.ru/fullchain.pem CN=*.asiteam.ru);
#   assertion (a) φ-final-verify падал на ложном «no certificate on disk» (проверял только
#   direct-каталог live/{domain}/).
# · Last fail: asi-team-vps cold bootstrap — wildcard *.asiteam.ru реально выпущен, но direct-каталог
#   отсутствовал → exit 10.
# · Remove if: on-disk coverage перестаёт учитывать wildcard-родителя
# GUARD-PRESERVE (168): единственное покрытие on-disk direct|wildcard-parent веток cert_covers_domain
# (F14) — дедупликация _log_post_issue_coverage в shared.
def test_cert_covers_domain_direct(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    """Direct: live/{domain}/fullchain.pem с CN=domain → True (0 subprocess)."""
    caplog.set_level(logging.INFO)
    le_live = tmp_path / "live"
    cert_dir = le_live / "app.example.com"
    cert_dir.mkdir(parents=True)
    (cert_dir / "fullchain.pem").write_text("dummy", encoding="utf-8")
    monkeypatch.setattr(ssl_certs_module, "cert_get_subject", lambda _p: "subject=CN = app.example.com")

    assert cert_covers_domain(le_live, "app.example.com") is True
    logger.info("[IMP:9][test][cert_covers_domain] direct coverage OK")


def test_cert_covers_domain_wildcard_parent(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    """Wildcard: live/example.com/fullchain.pem с CN=*.example.com покрывает roadmap.example.com (F14)."""
    caplog.set_level(logging.INFO)
    le_live = tmp_path / "live"
    parent_dir = le_live / "example.com"
    parent_dir.mkdir(parents=True)
    (parent_dir / "fullchain.pem").write_text("dummy", encoding="utf-8")
    monkeypatch.setattr(ssl_certs_module, "cert_get_subject", lambda _p: "subject=CN = *.example.com")

    assert cert_covers_domain(le_live, "roadmap.example.com") is True
    logger.info("[IMP:9][test][cert_covers_domain] wildcard parent coverage OK")


def test_cert_covers_domain_none(tmp_path: Path, caplog: pytest.LogCaptureFixture) -> None:
    """Ни direct, ни wildcard → False (fail-closed, R3 — не маскирует реальный missing-cert)."""
    caplog.set_level(logging.INFO)
    le_live = tmp_path / "live"
    le_live.mkdir(parents=True)  # пустая live-директория — ни direct, ни wildcard-родителя

    assert cert_covers_domain(le_live, "roadmap.example.com") is False
    logger.info("[IMP:9][test][cert_covers_domain] no coverage → False")


# endregion TEST_CERT_COVERS_DOMAIN


# ═══════════════════════════════════════════════════════════════════════════
# region TEST_CLI (DevPlan 119 D1 — CLI-фасад для issue-cert.sh, паттерн ssh_opts --shell)
# ═══════════════════════════════════════════════════════════════════════════


# 🧪 TRAP[TEST] · 2026-08-02 · Regression · CLI --is-le: LE-сертификат → exit 0 (D1, TEST_SPEC)
# · Scenario: issuer "Let's Encrypt" → main(["--is-le", cert]) == 0
# · Last fail: N/A (new — D1 CLI)
# · Remove if: ssl_certs CLI --is-le удалён
def test_cli_is_le(caplog: pytest.LogCaptureFixture) -> None:
    """CLI --is-le: LE-сертификат → exit 0 (issue-cert.sh _is_le_cert замена, D1)."""
    caplog.set_level(logging.INFO)
    with patch("core.internal.shared.ssl_certs.subprocess.run") as mock_run:
        mock_run.return_value = MagicMock(returncode=0, stdout="issuer=C = US, O = Let's Encrypt, CN = R11\n")
        rc = ssl_certs_cli_main(["--is-le", "/tmp/le-cert.pem"])
    assert rc == 0
    assert_ldd_imp9(caplog, needle="--is-le")


# 🧪 TRAP[TEST] · 2026-08-02 · Regression · CLI --is-le: mkcert → exit 1 (D1)
# · Scenario: issuer mkcert → main(["--is-le", cert]) == 1
# · Last fail: 2026-07-22 P0 — mkcert certs survived bootstrap (без issuer check)
# · Remove if: ssl_certs CLI --is-le удалён
def test_cli_is_le_mkcert_rejected(caplog: pytest.LogCaptureFixture) -> None:
    """CLI --is-le: mkcert/self-signed issuer → exit 1 (P0 регрессия)."""
    caplog.set_level(logging.INFO)
    with patch("core.internal.shared.ssl_certs.subprocess.run") as mock_run:
        mock_run.return_value = MagicMock(returncode=0, stdout="issuer=O = mkcert development CA, CN = mkcert\n")
        rc = ssl_certs_cli_main(["--is-le", "/tmp/mkcert.pem"])
    assert rc == 1


# 🧪 TRAP[TEST] · 2026-08-02 · Regression · CLI --check-expiry: >30 дней → exit 0 (D1)
# · Scenario: openssl -checkend returncode=0 → main(["--check-expiry", cert, "30"]) == 0
# · Last fail: N/A (new — D1 CLI; replaces _acme_verify_cert openssl -enddate pipeline)
# · Remove if: ssl_certs CLI --check-expiry удалён
def test_cli_check_expiry_ok(caplog: pytest.LogCaptureFixture) -> None:
    """CLI --check-expiry CERT 30: >30 дней до истечения → exit 0."""
    caplog.set_level(logging.INFO)
    with patch("core.internal.shared.ssl_certs.subprocess.run") as mock_run:
        mock_run.return_value = MagicMock(returncode=0)
        rc = ssl_certs_cli_main(["--check-expiry", "/tmp/cert.pem", "30"])
        # Порог передаётся в секундах: 30 дней × 86400 = 2592000
        args = mock_run.call_args.args[0]
        assert "2592000" in args
    assert rc == 0
    assert_ldd_imp9(caplog, needle="--check-expiry")


# 🧪 TRAP[TEST] · 2026-08-02 · Regression · CLI --check-expiry: expires within days → exit 1 (D1)
# · Scenario: -checkend returncode!=0 → main(["--check-expiry", cert, "30"]) == 1
# · Last fail: N/A (new — D1 CLI)
# · Remove if: ssl_certs CLI --check-expiry удалён
def test_cli_check_expiry_fail(caplog: pytest.LogCaptureFixture) -> None:
    """CLI --check-expiry: истёк/в пределах 30 дней → exit 1 (как shell _acme_verify_cert)."""
    caplog.set_level(logging.INFO)
    with patch("core.internal.shared.ssl_certs.subprocess.run") as mock_run:
        mock_run.return_value = MagicMock(returncode=1)
        rc = ssl_certs_cli_main(["--check-expiry", "/tmp/cert.pem", "30"])
    assert rc == 1


# 🧪 TRAP[TEST] · NEGATIVE (R5) · test_issue_cert_wrapper_consistency — shell/Python parity (D1)
# · Scenario: удалённая _is_le_cert()/_acme_verify_cert() заменены CLI (issue-cert.sh вызывает
# ·   python3 -m ... --is-le/--check-expiry). Вердикт CLI (main) == вердикт функции на том же
# ·   входе (исходный вход P0: mkcert-сертификат / истёкший cert).
# · Last fail: 2026-07-22 P0 — shell-функции не имели parity-покрытия; удаление без negative = survivorship
# · Remove if: issue-cert.sh перестаёт вызывать ssl_certs CLI (возврат shell-функций)
def test_issue_cert_wrapper_consistency_negative(caplog: pytest.LogCaptureFixture) -> None:
    """R5: CLI-обёртка (что теперь вызывает issue-cert.sh) == функция на исходных входах P0."""
    caplog.set_level(logging.INFO)

    # Исходный вход 1 (P0 2026-07-22): mkcert-сертификат, который пережил bootstrap
    with patch("core.internal.shared.ssl_certs.subprocess.run") as mock_run:
        mock_run.return_value = MagicMock(returncode=0, stdout="issuer=O = mkcert development CA, CN = mkcert\n")
        assert cert_is_le_issuer("/tmp/mkcert.pem") is False
        assert ssl_certs_cli_main(["--is-le", "/tmp/mkcert.pem"]) == 1

    # Исходный вход 2 (AC-8): сертификат, истекающий в пределах 30 дней — оба пути отвергают
    with patch("core.internal.shared.ssl_certs.subprocess.run") as mock_run:
        mock_run.return_value = MagicMock(returncode=1)
        assert cert_check_expiry("/tmp/expiring.pem", 30 * 86400) is False
        assert ssl_certs_cli_main(["--check-expiry", "/tmp/expiring.pem", "30"]) == 1


# 🧪 TRAP[TEST] · 2026-08-02 · Regression · CLI без аргументов → usage error (D1, fail-fast)
# · Scenario: main([]) → SystemExit(2) (argparse parser.error)
# · Last fail: N/A (new — D1 CLI)
# · Remove if: ssl_certs CLI удалён
# GUARD-PRESERVE (168): P0-mkcert guard-часть CLI — fail-fast usage error (argparse exit 2, D1 канон ssh_opts), единственное покрытие no-args ветки
def test_cli_no_args_usage_error(caplog: pytest.LogCaptureFixture) -> None:
    """CLI без --is-le/--check-expiry → parser.error (fail-fast канон, как ssh_opts)."""
    caplog.set_level(logging.INFO)
    with pytest.raises(SystemExit) as excinfo:
        ssl_certs_cli_main([])
    assert excinfo.value.code == 2


# endregion TEST_CLI
