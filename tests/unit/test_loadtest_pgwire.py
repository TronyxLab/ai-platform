# GREP_SUMMARY: loadtest pgwire unit wire-protocol startup-message md5 scram rfc7677 vector parse-backend data-row error-response PgError
# STRUCTURE: ▶ importlib-загрузка pgwire.py (без locust — scenarios/__init__ импортирует locust,
#           CI без load extra) → ◇ startup-framing → ◇ md5-вектор → ◇ SCRAM RFC 7677 §3
#           → ◇ parse backend T/D/C/Z → ◇ ErrorResponse → PgError → ⎋ 5 тестов, LDD IMP:9
# region MODULE_CONTRACT
## @purpose  Unit-тесты чистого stdlib PG wire protocol клиента (DevPlan 148 TASK-8,
##           core/loadtest/scenarios/pgwire.py): фрейминг StartupMessage, md5-пароль
##           (известный вектор), SCRAM-SHA-256 по официальному вектору RFC 7677 §3
##           (user/pencil → proof dHzbZapWIk4jUhN+Ute9ytag9zjfMHgsqmmiz7AndVQ=), парсинг
##           backend-сообщений (T/D/C/Z), ErrorResponse → PgError с сообщением сервера.
##           НИ ОДНОГО живого сервера — чистые функции, детерминированные фикстуры.
## @scope    Модуль загружается importlib-ом НАПРЯМУЮ (spec_from_file_location): обычный
##           импорт `from core.loadtest.scenarios import pgwire` выполняет scenarios/__init__.py,
##           который импортирует locust (load extra) — CI без locust упал бы ImportError.
##           Паттерн репозитория: tests/gates/test_gate_compose_include_sync.py (importlib,
##           no sys.path manipulation).
## @invariants
##   - Никакого locust-импорта: pgwire.py — чистый stdlib (socket/hashlib/hmac/base64)
##   - RFC 7677 §3 вектор жёстко зафиксирован (proof + server-verify) — регрессия алгоритма
##   - md5-вектор: user=postgres/password=postgres/salt=0xdeadbeef → md58ee245854025535aedb7b15709315318
##   - Парсинг данных: DataRow int16 count + int32 len (-1 → NULL), ErrorResponse поля 'M'/'C'
##   - LDD: caplog IMP:9 (Anti-Illusion Rule, .kilo/rules/testing.md)
## @rationale pgwire — ядро db-сценария (read/write PostgreSQL, DevPlan 148 TASK-1/2):
##            ошибка auth-вектора или фрейминга = молчаливый FAIL прогона на ноде.
##            Pure-function тесты ловят регрессию алгоритма ДО живого сервера.
## @changes  2026-08-12 | DevPlan 148 TASK-8 — Created
# endregion MODULE_CONTRACT

from __future__ import annotations

import importlib.util
import logging
from pathlib import Path

import pytest

from tests.helpers.gate_helpers import assert_ldd_imp9

pytestmark = pytest.mark.static_audit

logger = logging.getLogger(__name__)

# ── importlib-загрузка pgwire.py (см. @scope MODULE_CONTRACT) ─────────────────
_PGWIRE_PATH = Path(__file__).resolve().parents[2] / "core" / "loadtest" / "scenarios" / "pgwire.py"


def _load_pgwire():
    """Загрузка pgwire.py как standalone-модуля (без scenarios/__init__ → без locust)."""
    spec = importlib.util.spec_from_file_location("pgwire", _PGWIRE_PATH)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None, "spec.loader отсутствует — pgwire.py не найден"
    spec.loader.exec_module(module)
    return module


pgwire = _load_pgwire()


# T2.16a: _assert_ldd_imp9 консолидирован в gate_helpers.assert_ldd_imp9


# ═══════════════════════════════════════════════════════════════════════════════
# StartupMessage — фрейминг
# ═══════════════════════════════════════════════════════════════════════════════


# region TEST_startup_message_framing
# 🧪 TRAP[TEST] · Scenario: фрейминг StartupMessage (len + proto 196608 + пары params\0)
# · Regression: длина не включает себя (4) / протокол не 196608 / нет финального null
# ·   → сервер не распознает стартовое сообщение → молчаливый разрыв соединения
# · Last fail: N/A (new) — 148 TASK-1
# · Remove if: формат StartupMessage PG wire protocol изменён (3.x)
def test_startup_message_framing(caplog) -> None:
    """StartupMessage: len = 4+body, proto 196608, пары user/database null-terminated + финальный null."""
    caplog.set_level(logging.INFO)
    msg = pgwire.build_startup_message("postgres", "platform")
    logger.info(
        "[IMP:9][test][startup] StartupMessage: %d байт, proto=%d",
        len(msg),
        int.from_bytes(msg[4:8], "big"),
    )
    assert_ldd_imp9(caplog)
    assert len(msg) == int.from_bytes(msg[:4], "big")  # длина включает себя
    assert int.from_bytes(msg[4:8], "big") == 196608  # protocol 3.0
    # "user\0postgres\0database\0platform\0" + финальный \0
    assert msg.endswith(b"\x00")
    assert b"user\x00postgres\x00" in msg
    assert b"database\x00platform\x00" in msg
    # Не начинается с type-байта (StartupMessage БЕЗ типа) — но длина впереди
    assert len(msg) >= 4


# endregion TEST_startup_message_framing


# ═══════════════════════════════════════════════════════════════════════════════
# md5 — известный вектор
# ═══════════════════════════════════════════════════════════════════════════════


# region TEST_md5_password_hash_rfc
# 🧪 TRAP[TEST] · Scenario: md5-пароль PG (AuthenticationMD5Password, код 5)
# · Regression: inner не hex-ASCII или salt вне второго md5 → неверный пароль → auth FAIL
# · Last fail: N/A (new) — 148 TASK-1
# · Remove if: формат md5-аутентификации PG изменён
def test_md5_password_hash_rfc(caplog) -> None:
    """Вектор: user=postgres/password=postgres/salt=0xdeadbeef → md58ee245854025535aedb7b15709315318."""
    caplog.set_level(logging.INFO)
    result = pgwire.md5_password_hash("postgres", "postgres", bytes.fromhex("deadbeef"))
    logger.info("[IMP:9][test][md5] вектор: %s", result)
    assert_ldd_imp9(caplog)
    assert result == "md58ee245854025535aedb7b15709315318"
    assert result.startswith("md5") and len(result) == 35  # md5 + 32 hex


# endregion TEST_md5_password_hash_rfc


# ═══════════════════════════════════════════════════════════════════════════════
# SCRAM-SHA-256 — RFC 7677 §3 официальный вектор
# ═══════════════════════════════════════════════════════════════════════════════


# region TEST_scram_rfc7677_vector
# 🧪 TRAP[TEST] · Scenario: SCRAM-SHA-256 proof по RFC 7677 §3 (user/pencil, i=4096)
# · Regression: ошибка pbkdf2/ClientKey/StoredKey/ClientSignature/XOR → неверный proof
# ·   → сервер отвергает auth (password authentication failed) → db smoke FAIL
# · Last fail: N/A (new) — 148 TASK-1 (вектор сверен с RFC 7677 §3 и RFC 5802 §5)
# · Remove if: SCRAM-SHA-256 заменён другим механизмом auth
def test_scram_rfc7677_vector(caplog) -> None:
    """RFC 7677 §3: user/pencil, nonce rOprNGfwEbeRWgbNEkqO, salt W22ZaJ0SNY7soEsUEjb6gQ==, i=4096."""
    caplog.set_level(logging.INFO)
    user, password = "user", "pencil"
    nonce = "rOprNGfwEbeRWgbNEkqO"
    bare, full = pgwire.scram_client_first(user, nonce)
    logger.info("[IMP:9][test][scram] client-first: %r (bare=%r)", full, bare)
    server_first = "r=rOprNGfwEbeRWgbNEkqO%hvYDpWUa2RaTCAfuxFIlj)hNlF$k0,s=W22ZaJ0SNY7soEsUEjb6gQ==,i=4096"
    client_final, server_verify = pgwire.scram_client_final(user, password, bare, server_first)
    logger.info("[IMP:9][test][scram] client-final: %r", client_final)
    logger.info("[IMP:9][test][scram] server-verify: %r", server_verify)
    assert_ldd_imp9(caplog)
    # RFC 7677 §3: proof = dHzbZapWIk4jUhN+Ute9ytag9zjfMHgsqmmiz7AndVQ=
    assert client_final == (
        "c=biws,r=rOprNGfwEbeRWgbNEkqO%hvYDpWUa2RaTCAfuxFIlj)hNlF$k0,p=dHzbZapWIk4jUhN+Ute9ytag9zjfMHgsqmmiz7AndVQ="
    )
    # RFC 7677 §3: server signature v=6rriTRBi23WpRR/wtup+mMhUZUn/dB5nLTJRsjl95G4=
    assert server_verify == "6rriTRBi23WpRR/wtup+mMhUZUn/dB5nLTJRsjl95G4="


# endregion TEST_scram_rfc7677_vector


# ═══════════════════════════════════════════════════════════════════════════════
# Парсинг backend-сообщений (T/D/C/Z) — ответ Simple Query
# ═══════════════════════════════════════════════════════════════════════════════


# region TEST_parse_backend_messages
# 🧪 TRAP[TEST] · Scenario: парсинг T/D/C/Z — строки результата + command tag
# · Regression: неверный фрейминг (len/offset) или DataRow-декодирование → пустые/битые строки
# · Last fail: N/A (new) — 148 TASK-1
# · Remove if: формат backend-сообщений PG wire protocol изменён
def test_parse_backend_messages(caplog) -> None:
    """SELECT count(*) → T (RowDescription) + D (DataRow 42) + C (SELECT 1) + Z — фрейминг по длине."""
    caplog.set_level(logging.INFO)

    def _msg(mtype: int, payload: bytes) -> bytes:
        return bytes([mtype]) + (4 + len(payload)).to_bytes(4, "big") + payload

    def _c_string(s: str) -> bytes:
        return s.encode("utf-8") + b"\x00"

    # RowDescription: int16 1 колонка (payload пропускается парсером — достаточно корректного фрейминга)
    t_payload = (1).to_bytes(2, "big") + _c_string("count") + (0).to_bytes(4, "big")
    # DataRow: int16 1 колонка + int32 len 2 + "42"
    d_payload = (1).to_bytes(2, "big") + (2).to_bytes(4, "big") + b"42"
    # CommandComplete: "SELECT 1\0"; ReadyForQuery: статус 'I'
    stream = (
        _msg(ord("T"), t_payload)
        + _msg(ord("D"), d_payload)
        + _msg(ord("C"), _c_string("SELECT 1"))
        + _msg(ord("Z"), b"I")
    )
    rows, tag = pgwire.parse_query_response(stream)
    logger.info("[IMP:9][test][parse] rows=%r tag=%r", rows, tag)
    assert_ldd_imp9(caplog)
    assert rows == [[b"42"]]  # count(*) → текстовая строка (bytes)
    assert tag == "SELECT 1"


# endregion TEST_parse_backend_messages


# ═══════════════════════════════════════════════════════════════════════════════
# ErrorResponse → PgError
# ═══════════════════════════════════════════════════════════════════════════════


# region TEST_query_error_raises_pgerror
# 🧪 TRAP[TEST] · Scenario: ErrorResponse → PgError с сообщением сервера (SQLSTATE)
# · Regression: ошибка сервера (например синтаксис SQL) молча игнорируется →
# ·   locust не фиксирует failure → error_rate=0 → ложный PASS db-прогона
# · Last fail: N/A (new) — 148 TASK-1
# · Remove if: Simple Query парсинг ErrorResponse изменён
def test_query_error_raises_pgerror(caplog) -> None:
    """ErrorResponse с полями S/C/M (+ финальный 0) → PgError с сообщением и SQLSTATE."""
    caplog.set_level(logging.INFO)

    def _msg(mtype: int, payload: bytes) -> bytes:
        return bytes([mtype]) + (4 + len(payload)).to_bytes(4, "big") + payload

    def _field(code: str, value: str) -> bytes:
        return code.encode("ascii") + value.encode("utf-8") + b"\x00"

    # ErrorResponse: S "ERROR" + C "42601" + M "syntax error at or near ..." + финальный 0
    e_payload = _field("S", "ERROR") + _field("C", "42601") + _field("M", 'syntax error at or near "FROM"') + b"\x00"
    import pytest

    with pytest.raises(pgwire.PgError) as excinfo:
        pgwire.parse_query_response(_msg(ord("E"), e_payload))
    logger.info("[IMP:9][test][pgerror] PgError: %s", excinfo.value)
    assert_ldd_imp9(caplog)
    assert "syntax error at or near" in str(excinfo.value)
    assert "42601" in str(excinfo.value)
    assert excinfo.value.sqlstate == "42601"


# endregion TEST_query_error_raises_pgerror
