#!/usr/bin/env python3
# GREP_SUMMARY: pgwire postgres wire-protocol stdlib socket scram-sha256 md5 auth startup-message simple-query PgError loadtest db
# STRUCTURE: ▶ build_startup_message (len+proto 196608+params\0) → ◇ connect (socket → auth по коду R:
#           0 OK | 3 cleartext→err | 5 MD5 | 10/11/12 SASL SCRAM-SHA-256) → ◇ query (Q → T/D/C/E/Z)
#           → ⎋ rows | PgError (сообщение сервера)
# region MODULE_CONTRACT
## @purpose  Чистый stdlib PG wire protocol клиент (DevPlan 148 TASK-1) для read/write
##           нагрузки на PostgreSQL в locust-сценарии db.py — БЕЗ psycopg2/pg8000/HTTP-моста
##           (в locustio/locust образе нет драйверов; инвариант «ноль новой инфраструктуры»).
##           Паттерн s3.py (SigV4 на stdlib): StartupMessage → AuthenticationRequest (R) →
##           выбор механизма по коду (0 OK, 5 MD5, 10 SASL→SCRAM-SHA-256, 3 cleartext →
##           ошибка) → Simple Query ('Q') с парсингом T/D/C/E/Z. Модуль unit-тестируем
##           native pytest без живого сервера: auth-векторы RFC 7677 §3 (SCRAM) и md5,
##           фрейминг сообщений, парсинг backend-ответов — чистые функции.
## @scope    Импортируется ТОЛЬКО db.py (locust-сценарий). Платформенный код
##           core/internal/loadtest/ этот модуль НЕ импортирует (locust — load extra).
##           Никакого locust-импорта в этом файле — чистый stdlib (socket, hashlib,
##           hmac, base64, secrets), чтобы тесты работали без load extra.
## @invariants
##   1. Протокол: StartupMessage (без type-байта, len+proto 196608+params\0), frontend-
##      сообщения с type-байтом ('p' PasswordMessage, 'Q' Query, 'X' Terminate).
##   2. Auth по коду AuthenticationRequest (R): 0 → OK; 5 → md5 (md5(hex(md5(pw+user))+
##      salt)); 10 → SASL SCRAM-SHA-256 (RFC 5802/7677: pbkdf2_hmac-sha256, ClientKey/
##      StoredKey/ClientSignature/proof, verify серверной подписи v=); 11/12 — SASL-шаги;
##      3 (cleartext) и прочие механизмы → PgError (отказ, никаких секретов в открытом виде).
##   3. Simple Query: 'Q' → ответы T (RowDescription), D (DataRow), C (CommandComplete),
##      Z (ReadyForQuery); E (ErrorResponse) → PgError с сообщением сервера (SQLSTATE).
##   4. Таймаут сокета 10s (connect/recv) — зависший сервер не вешает locust-задачу.
##   5. Модуль НЕ импортирует locust и НЕ импортирует bootstrap/deploy/* (слой shared —
##      только вниз); секреты (пароль) не логируются.
## @rationale PostgreSQL доступен ТОЛЬКО в docker-сети shared-db-net (NO ports: directive) —
##            HTTP-моста нет (заглушка db.py 146 W1 — GET по несуществующему пути). Wire
##            protocol на stdlib — единственный способ гнать нагрузку без новой инфраструктуры:
##            тот же приём, что s3.py (SigV4 без boto3); PG16 default password_encryption=
##            scram-sha-256, pgbouncer AUTH_TYPE=scram-sha-256, pg_hba md5 (172.16.0.0/12) —
##            клиент поддерживает оба механизма по сообщению сервера.
## @changes  2026-08-12 | DevPlan 148 TASK-1 — Created
# endregion MODULE_CONTRACT

from __future__ import annotations

import base64
import contextlib
import hashlib
import hmac
import logging
import re
import secrets
import socket

logger = logging.getLogger(__name__)

# ── Протокольные константы (PG wire protocol 3.0) ──────────────────────────────
PROTOCOL_VERSION = 196608  # 3.0 (0x00030000)
DEFAULT_TIMEOUT = 10.0  # s — зависший сервер не вешает locust-задачу (инвариант 4)
DEFAULT_PORT = 5432

# Backend-типы сообщений (1 байт)
_MSG_AUTH = ord("R")  # AuthenticationRequest
_MSG_PARAM_STATUS = ord("S")  # ParameterStatus
_MSG_BACKEND_KEY = ord("K")  # BackendKeyData
_MSG_READY = ord("Z")  # ReadyForQuery
_MSG_ROW_DESC = ord("T")  # RowDescription
_MSG_DATA_ROW = ord("D")  # DataRow
_MSG_CMD_COMPLETE = ord("C")  # CommandComplete
_MSG_ERROR = ord("E")  # ErrorResponse
_MSG_NOTICE = ord("N")  # NoticeResponse

# Frontend-типы
_MSG_PASSWORD = b"p"  # PasswordMessage
_MSG_QUERY = b"Q"  # Simple Query
_MSG_TERMINATE = b"X"  # Terminate

# Коды AuthenticationRequest (полезная нагрузка: int32 code)
_AUTH_OK = 0
_AUTH_CLEARTEXT = 3  # AuthenticationCleartextPassword — НЕ поддерживаем (отказ)
_AUTH_MD5 = 5  # AuthenticationMD5Password: код + 4-байтный salt
_AUTH_SASL = 10  # AuthenticationSASL: список механизмов (null-terminated)
_AUTH_SASL_CONTINUE = 11  # SASLContinue: server-first-message
_AUTH_SASL_FINAL = 12  # SASLFinal: server-final-message (v=verify)

SASL_MECHANISM = "SCRAM-SHA-256"
_GS2_HEADER = "n,,"  # без channel binding (клиент не поддерживает tls-server-end-point)
_NONCE_RE = re.compile(r"^[A-Za-z0-9_-]+$")  # PG: клиентский nonce без ',' и управляющих символов


# region DATA_PgError
class PgError(Exception):
    """Ошибка PG wire protocol (сообщение сервера из ErrorResponse / auth-отказ).

    ## @purpose  Единственный тип исключений модуля: db.py пробрасывает его наружу,
    ##            locust засчитывает как failure задачи. Несёт сообщение сервера
    ##            (ErrorResponse поле 'M') + SQLSTATE (поле 'C') — человекочитаемый
    ##            failure locust без отдельного разбора.
    ## @io — ⇥ message: str (сообщение сервера), sqlstate: str | None
    ## @complexity — O(1)
    """

    def __init__(self, message: str, sqlstate: str | None = None) -> None:
        super().__init__(message)
        self.message = message
        self.sqlstate = sqlstate

    def __str__(self) -> str:  # pragma: no cover — формат строки для locust failure
        if self.sqlstate:
            return f"{self.message} (SQLSTATE {self.sqlstate})"
        return self.message


# endregion DATA_PgError


# region FUNC_build_startup_message
def build_startup_message(user: str, database: str, protocol: int = PROTOCOL_VERSION) -> bytes:
    """Фрейминг StartupMessage: len + protocol + пары user/database (null-terminated) + финальный null.

    ▶ ┌user, database, protocol┐ → ○ body = proto + "user\0v\0" + "database\0v\0" + "\0"
      → ○ len = 4 + len(body) → ⎋ len(4) + body (БЕЗ type-байта — StartupMessage)

    ## @purpose  Первое сообщение клиента (PG wire protocol 3.0): без type-байта,
    ##            длина включает себя (4) и protocol-версию (196608 = 3.0). Параметры
    ##            user/database — обязательные key/value пары, завершающиеся null.
    ## @io — ⇥ user: str, database: str, protocol: int (default 196608) → ⎋ bytes
    ## @complexity — O(U+D) — длина параметров
    ## @invariants
    ##   - Фрейминг: len = 4 (self) + 4 (protocol) + Σ(len(k)+1+len(v)+1) + 1 (финальный null)
    ##   - Пары null-terminated; финальный нулевой байт закрывает список параметров
    """
    body = bytearray()
    body += protocol.to_bytes(4, "big")
    for key, value in (("user", user), ("database", database)):
        body += key.encode("utf-8") + b"\x00" + value.encode("utf-8") + b"\x00"
    body += b"\x00"
    length = 4 + len(body)
    logger.info(
        "[IMP:8][pgwire][startup] StartupMessage: user=%s database=%s protocol=%d len=%d",
        user,
        database,
        protocol,
        length,
    )
    return length.to_bytes(4, "big") + bytes(body)


# endregion FUNC_build_startup_message


# region FUNC_md5_password_hash
def md5_password_hash(user: str, password: str, salt: bytes) -> str:
    """PG md5-пароль: "md5" + hex(md5(hex(md5(password+user)) + salt)).

    ▶ ┌user, password, salt┐ → ○ inner = hex(md5(password+user)) → ○ md5(inner_bytes + salt)
      → ⎋ "md5" + hex (25 символов, ASCII)

    ## @purpose  Механизм AuthenticationMD5Password (R, код 5): PostgreSQL считает
    ##            двойной md5 — внутренний хеш пароля+пользователя в HEX (ASCII), затем
    ##            этот hex снова хешируется вместе с 4-байтным salt сервера. Вектор
    ##            unit-теста: user=postgres/password=postgres/salt=0xdeadbeef → известное значение.
    ## @io — ⇥ user: str, password: str, salt: bytes (4 байта от сервера) → ⎋ str "md5"+hex
    ## @complexity — O(1) — 2 md5
    ## @invariants
    ##   - inner — hex ASCII (не raw bytes): md5(password+user) → hexdigest().encode("ascii")
    ##   - Результат — 25 символов ASCII (md5 + 32 hex) — кладётся в PasswordMessage
    """
    # nosec B324: md5 здесь — ПРОТОКОЛЬНЫЙ дайджест PG wire (AuthenticationMD5Password,
    # RFC-механизм сервера), не криптографическая защита; usedforsecurity=False —
    # явная маркировка для bandit. SCRAM-SHA-256 — основной механизм (PG16 default).
    inner = hashlib.md5((password + user).encode("utf-8"), usedforsecurity=False).hexdigest().encode("ascii")
    result = "md5" + hashlib.md5(inner + salt, usedforsecurity=False).hexdigest()
    logger.info("[IMP:8][pgwire][md5] md5 hash computed (user=%s, salt=%d bytes)", user, len(salt))
    return result


# endregion FUNC_md5_password_hash


# region FUNC__parse_server_first
def _parse_server_first(server_first: str) -> tuple[str, bytes, int]:
    """Разбор server-first-message SCRAM: r=<nonce>,s=<salt-b64>,i=<iterations>.

    ▶ ┌server_first┐ → ○ split(",") → ○ r=combined nonce, s=salt, i=iterations → ⎋ (nonce, salt, i)

    ## @purpose  Извлечение параметров SASLContinue (R, код 11): комбинированный nonce
    ##            (клиентский+серверный), соль (base64) и счётчик итераций PBKDF2.
    ## @io — ⇥ server_first: str (например "r=...,s=W22ZaJ0...,i=4096") → ⎋ (str, bytes, int)
    ## @complexity — O(1)
    ## @raises — PgError: сервер вернул неполный/битый server-first (fail-fast)
    ## @invariants
    ##   - Порядок полей r,s,i гарантирован RFC 5802 (сервер шлёт строго в этом порядке)
    ##   - salt — base64 (RFC 4648), без padding-нормализации
    """
    fields: dict[str, str] = {}
    for part in server_first.split(","):
        if "=" in part:
            key, _, value = part.partition("=")
            fields[key] = value
    nonce = fields.get("r", "")
    salt_b64 = fields.get("s", "")
    try:
        iterations = int(fields.get("i", "0"))
    except ValueError as exc:
        raise PgError(f"SCRAM server-first: iterations не число: {fields.get('i')!r}") from exc
    if not nonce or not salt_b64 or iterations <= 0:
        raise PgError(f"SCRAM server-first: неполные поля (r/s/i обязательны): {server_first!r}")
    try:
        salt = base64.b64decode(salt_b64)
    except (ValueError, TypeError) as exc:
        raise PgError(f"SCRAM server-first: salt не base64: {salt_b64!r}") from exc
    if not salt:
        raise PgError("SCRAM server-first: пустая соль")
    logger.info(
        "[IMP:8][pgwire][scram] server-first parsed: nonce=%d chars salt=%d bytes iterations=%d",
        len(nonce),
        len(salt),
        iterations,
    )
    return nonce, salt, iterations


# endregion FUNC__parse_server_first


# region FUNC_scram_client_first
def scram_client_first(user: str, nonce: str) -> tuple[str, str]:
    """SCRAM client-first: (bare, full) — bare для AuthMessage, full для отправки серверу.

    ▶ ┌user, nonce┐ → ○ bare = "n=<user>,r=<nonce>" → ○ full = "n,," + bare → ⎋ (bare, full)

    ## @purpose  RFC 5802: GS2-заголовок "n,," (без channel binding) + client-first-bare.
    ##            bare входит в AuthMessage (HMAC-вход), full отправляется серверу после
    ##            выбора механизма SCRAM-SHA-256 (код 10).
    ## @io — ⇥ user: str, nonce: str (случайный, без ',') → ⎋ (bare: str, full: str)
    ## @complexity — O(1)
    ## @raises — PgError: nonce содержит недопустимые символы (',' запрещён RFC 5802)
    ## @invariants
    ##   - full = "n,," + bare (GS2 header "n,," = no channel binding, no authzid)
    ##   - nonce: [A-Za-z0-9_-] — запятая сломала бы парсинг server-first
    """
    if not _NONCE_RE.fullmatch(nonce):
        raise PgError("SCRAM client nonce содержит недопустимые символы (допустимо: A-Za-z0-9_-)")
    bare = f"n={user},r={nonce}"
    full = f"{_GS2_HEADER}{bare}"
    logger.info("[IMP:8][pgwire][scram] client-first built (user=%s)", user)
    return bare, full


# endregion FUNC_scram_client_first


# region FUNC_scram_client_final
def scram_client_final(
    user: str,
    password: str,
    client_first_bare: str,
    server_first: str,
) -> tuple[str, str]:
    """SCRAM-SHA-256 client-final + server-verify (RFC 5802/7677) — чистый stdlib.

    ▶ ┌user, password, bare, server_first┐ → ○ parse (nonce/salt/i) → ○ SaltedPassword =
      PBKDF2-HMAC-SHA256(pw, salt, i) → ○ ClientKey → StoredKey → ClientSignature →
      ○ proof = ClientKey ⊕ ClientSignature → ○ ServerKey → ServerSignature → ⎋
      (client_final "c=biws,r=...,p=<proof>", server_verify "v=<sig>")

    ## @purpose  Вычисление proof SCRAM-SHA-256 (механизм AuthenticationSASL кода 10,
    ##            шаги 11/12). RFC 7677 §3 — официальный тест-вектор: user/pencil,
    ##            nonce rOprNGfwEbeRWgbNEkqO, salt W22ZaJ0SNY7soEsUEjb6gQ==, i=4096 →
    ##            proof dHzbZapWIk4jUhN+Ute9ytag9zjfMHgsqmmiz7AndVQ= (проверяется в
    ##            tests/unit/test_loadtest_pgwire.py). Возвращает также ожидаемую
    ##            серверную подпись (v=) — PGSocket.authenticate верифицирует SASLFinal.
    ## @io — ⇥ user: str (в AuthMessage не входит напрямую, но включён в bare),
    ##         password: str, client_first_bare: str, server_first: str
    ##       → ⎋ (client_final: str, server_verify: str — base64, без префикса "v=")
    ## @complexity — O(i) — i итераций PBKDF2 (4096 по умолчанию PG)
    ## @invariants
    ##   - SaltedPassword = pbkdf2_hmac("sha256", password, salt, i) — RFC 7677
    ##   - StoredKey = SHA256(ClientKey); ClientSignature = HMAC(StoredKey, AuthMessage)
    ##   - AuthMessage = bare + "," + server_first + "," + "c=biws,r=<nonce>" (RFC 5802 §3)
    ##   - c=biws — base64("n,,") — GS2-заголовок без channel binding (фиксированный)
    ##   - Секреты (пароль) в логи НЕ попадают — только длины/структура
    """
    combined_nonce, salt, iterations = _parse_server_first(server_first)
    salted_password = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, iterations)
    client_key = hmac.new(salted_password, b"Client Key", hashlib.sha256).digest()
    stored_key = hashlib.sha256(client_key).digest()
    client_final_without_proof = f"c=biws,r={combined_nonce}"
    auth_message = f"{client_first_bare},{server_first},{client_final_without_proof}".encode()
    client_signature = hmac.new(stored_key, auth_message, hashlib.sha256).digest()
    proof = bytes(a ^ b for a, b in zip(client_key, client_signature, strict=True))
    proof_b64 = base64.b64encode(proof).decode("ascii")
    client_final = f"{client_final_without_proof},p={proof_b64}"
    server_key = hmac.new(salted_password, b"Server Key", hashlib.sha256).digest()
    server_signature = hmac.new(server_key, auth_message, hashlib.sha256).digest()
    server_verify = base64.b64encode(server_signature).decode("ascii")
    logger.info(
        "[IMP:9][pgwire][scram] client-final computed: proof=%d chars, server_verify=%d chars",
        len(proof_b64),
        len(server_verify),
    )
    return client_final, server_verify


# endregion FUNC_scram_client_final


# region FUNC__recv_exact
def _recv_exact(conn: socket.socket, n: int) -> bytes:
    """Чтение ровно n байт из сокета (цикл recv, EOF/таймаут → PgError).

    ▶ ┌conn, n┐ → ○ while len(buf) < n: recv → ◇ EOF → PgError → ◇ таймаут → PgError → ⎋ buf

    ## @purpose  Надёжное чтение фиксированного фрагмента (заголовок 5 байт, payload):
    ##            recv может вернуть меньше запрошенного — цикл добирает до n.
    ## @io — ⇥ conn: socket.socket, n: int → ⎋ bytes длины n
    ## @complexity — O(n) — байты фрагмента
    ## @raises — PgError: сокет закрыт сервером (EOF) или таймаут (socket.timeout)
    """
    buf = bytearray()
    while len(buf) < n:
        try:
            chunk = conn.recv(n - len(buf))
        except TimeoutError as exc:
            raise PgError(f"pgwire: таймаут чтения от сервера ({n - len(buf)} байт не получены)") from exc
        if not chunk:
            raise PgError("pgwire: сервер закрыл соединение (EOF) до полного сообщения")
        buf += chunk
    return bytes(buf)


# endregion FUNC__recv_exact


# region FUNC_parse_data_row
def parse_data_row(payload: bytes) -> list[object]:
    """Парсинг DataRow (D): int16 count + значения (int32 len, -1 = NULL, иначе bytes).

    ▶ ┌payload┐ → ○ int16 count → ○ per-value: int32 len → ◇ -1 → None | bytes → ⎋ list

    ## @purpose  Декодирование строки результата: значения — сырые bytes (текстовый
    ##            формат PG Simple Query) или None для NULL. bytes, не str — потребитель
    ##            (db.py read_query) сам решает, как интерпретировать (count(*) → int).
    ## @io — ⇥ payload: bytes (тело DataRow) → ⎋ list[object] — bytes | None
    ## @complexity — O(V) — V = число колонок
    ## @invariants
    ##   - len == -1 → NULL (None); len >= 0 → ровно len байт значения
    ##   - Битый payload (обрезан) → PgError (fail-fast, не молчаливый None)
    """
    if len(payload) < 2:
        raise PgError("pgwire: DataRow payload обрезан (нет count)")
    count = int.from_bytes(payload[:2], "big")
    values: list[object] = []
    offset = 2
    for _ in range(count):
        if offset + 4 > len(payload):
            raise PgError("pgwire: DataRow payload обрезан (нет длины значения)")
        length = int.from_bytes(payload[offset : offset + 4], "big", signed=True)
        offset += 4
        if length < 0:
            values.append(None)
            continue
        if offset + length > len(payload):
            raise PgError("pgwire: DataRow payload обрезан (значение длиннее payload)")
        values.append(payload[offset : offset + length])
        offset += length
    logger.info("[IMP:8][pgwire][data_row] %d значения, %d байт", len(values), offset)
    return values


# endregion FUNC_parse_data_row


# region FUNC__parse_error_fields
def _parse_error_fields(payload: bytes) -> tuple[str, str]:
    """Извлечение (message, sqlstate) из ErrorResponse (E): поля type-байт + String, финал 0.

    ▶ ┌payload┐ → ○ поля (код байт + null-terminated String) → ⎋ ('M' message, 'C' SQLSTATE)

    ## @purpose  ErrorResponse несёт структурированные поля (S severity, C SQLSTATE,
    ##            M message, D detail, H hint...). PgError хранит message и sqlstate
    ##            раздельно (атрибут .sqlstate) — единый парсер полей для всех точек raise.
    ## @io — ⇥ payload: bytes → ⎋ (message: str, sqlstate: str | None)
    ## @complexity — O(F) — F = число полей
    ## @invariants
    ##   - message — поле 'M' (fallback «неизвестная ошибка PostgreSQL»)
    ##   - sqlstate — поле 'C' (None, если сервер не прислал код)
    """
    fields: dict[str, str] = {}
    i = 0
    while i < len(payload) and payload[i] != 0:
        ftype = chr(payload[i])
        i += 1
        end = payload.index(b"\x00", i)
        fields[ftype] = payload[i:end].decode("utf-8", errors="replace")
        i = end + 1
    message = fields.get("M", "неизвестная ошибка PostgreSQL")
    sqlstate = fields.get("C")
    logger.info("[IMP:8][pgwire][error] ErrorResponse: %s (SQLSTATE %s)", message, sqlstate)
    return message, sqlstate


# endregion FUNC__parse_error_fields


# region FUNC_parse_error_message
def parse_error_message(payload: bytes) -> str:
    """Человекочитаемое сообщение ErrorResponse (для логирования/сообщений PgError).

    ▶ ┌payload┐ → ○ _parse_error_fields → ⎋ "message (SQLSTATE code)" | message

    ## @purpose  Текстовое представление ошибки сервера (дефолтный __str__ PgError):
    ##            message + SQLSTATE в скобках, если код прислан.
    ## @io — ⇥ payload: bytes → ⎋ str
    ## @complexity — O(F) — F = число полей
    """
    message, sqlstate = _parse_error_fields(payload)
    return message if not sqlstate else f"{message} (SQLSTATE {sqlstate})"


# endregion FUNC_parse_error_message


# region FUNC_read_backend_messages
def read_backend_messages(data: bytes):
    """Итератор backend-сообщений из буфера: (type: int, payload: bytes) — фрейминг по длине.

    ▶ ┌data┐ → ○ while осталось >= 5: type(1) + len(4) → payload = len-4 → yield (type, payload)

    ## @purpose  Фрейминг потока ответов сервера (type-байт + Int32 length, включающий
    ##            себя): единый парсер для auth-фазы и query-фазы. Обрезанный хвост
    ##            молчаливо игнорируется (неполный дамп в unit-фикстуре).
    ## @io — ⇥ data: bytes → generator (type: int, payload: bytes)
    ## @complexity — O(M) — M = число сообщений в буфере
    ## @invariants
    ##   - length >= 4 (иначе payload отрицателен — мусорный буфер)
    ##   - Строгий фрейминг: 1 + 4 + (length - 4) байт на сообщение
    """
    i = 0
    while i + 5 <= len(data):
        mtype = data[i]
        length = int.from_bytes(data[i + 1 : i + 5], "big")
        if length < 4:
            raise PgError(f"pgwire: некорректная длина backend-сообщения: {length}")
        end = i + 5 + (length - 4)
        if end > len(data):
            break  # обрезанный хвост — молчаливый выход (unit-фикстура / частичный recv)
        yield mtype, data[i + 5 : end]
        i = end


# endregion FUNC_read_backend_messages


# region FUNC_parse_query_response
def parse_query_response(data: bytes) -> tuple[list[list[object]], str]:
    """Парсинг ответа Simple Query: T/D/C/E/Z → (rows, command_tag).

    ▶ ┌data┐ → ○ read_backend_messages → ◇ E → raise PgError → ⊕ D → rows.append →
      ◇ C → tag → ◇ Z → стоп → ⎋ (rows, tag)

    ## @purpose  Декодирование одного ответа на 'Q': строки (DataRow) и команда
    ##            (CommandComplete, например "SELECT 1" / "INSERT 0 1" / "CREATE TABLE").
    ##            ErrorResponse → PgError (сообщение сервера) — db.py пробрасывает,
    ##            locust фиксирует failure. RowDescription/ReadyForQuery игнорируются.
    ## @io — ⇥ data: bytes (полный поток ответа) → ⎋ (rows: list[list[object]], tag: str)
    ## @complexity — O(R×V) — R строк × V колонок
    ## @raises — PgError: ErrorResponse в потоке (сообщение сервера, SQLSTATE)
    ## @invariants
    ##   - 'E' в ЛЮБОЙ точке → PgError (ошибка не молчится)
    ##   - Порядок строк сохраняется (DataRow в порядке следования)
    ##   - tag — пустая строка, если CommandComplete не встретился (обрезанный дамп)
    """
    rows: list[list[object]] = []
    tag = ""
    for mtype, payload in read_backend_messages(data):
        if mtype == _MSG_ERROR:
            message, sqlstate = _parse_error_fields(payload)
            raise PgError(message, sqlstate)
        if mtype == _MSG_DATA_ROW:
            rows.append(parse_data_row(payload))
        elif mtype == _MSG_CMD_COMPLETE:
            tag = (
                payload[:-1].decode("utf-8", errors="replace")
                if payload.endswith(b"\x00")
                else payload.decode("utf-8", errors="replace")
            )
        elif mtype == _MSG_READY:
            break
    logger.info("[IMP:9][pgwire][query] rows=%d tag=%r", len(rows), tag)
    return rows, tag


# endregion FUNC_parse_query_response


# region CLASS_PGSocket
class PGSocket:
    """Обёртка сокета: auth-хендшейк (md5/scram/ok) + Simple Query + Terminate.

    ▶ ┌conn┐ → ○ authenticate(user, password, database): StartupMessage → R/S/K/Z →
      ◇ код 0 → OK | 5 → md5 | 10→11→12 → SCRAM → ○ query(sql): 'Q' → T/D/C/E/Z
      → ○ close(): 'X' → ⎋

    ## @purpose  Соединение PG wire protocol для db.py (locust DbUser): on_start →
    ##            connect+authenticate, задачи → query(), on_stop → close(). Таймаут
    ##            сокета 10s (инвариант 4) — зависший PG не вешает пул locust-пользователей.
    ## @io — ⇥ conn: socket.socket (уже подключён), timeout: float, nonce: str | None
    ##       → методы: authenticate/query/close
    ## @complexity — O(хендшейк) + O(R×V) на query
    ## @invariants
    ##   - Верификация SCRAM server-подписи (v=) — подмена сервера детектируется
    ##   - Auth-код 3 (cleartext) → PgError (секреты не ходят в открытом виде)
    ##   - query() до authenticate() → PgError (жёсткий порядок протокола)
    """

    def __init__(self, conn: socket.socket, timeout: float = DEFAULT_TIMEOUT, nonce: str | None = None) -> None:
        self._conn = conn
        self._conn.settimeout(timeout)
        self._nonce = nonce or secrets.token_urlsafe(18)
        self._client_first_bare: str | None = None
        self._server_verify: str | None = None
        self._authenticated = False

    # region FUNC_PGSocket_send_message
    def _send(self, mtype: bytes, payload: bytes) -> None:
        """Отправка frontend-сообщения: type(1) + len(4, включает себя) + payload."""
        length = 4 + len(payload)
        self._conn.sendall(mtype + length.to_bytes(4, "big") + payload)

    # endregion FUNC_PGSocket_send_message

    # region FUNC_PGSocket_recv
    def _recv(self) -> tuple[int, bytes]:
        """Чтение одного backend-сообщения: type(1) + len(4) + payload(len-4)."""
        header = _recv_exact(self._conn, 5)
        mtype = header[0]
        length = int.from_bytes(header[1:5], "big")
        if length < 4:
            raise PgError(f"pgwire: некорректная длина backend-сообщения: {length}")
        return mtype, _recv_exact(self._conn, length - 4)

    # endregion FUNC_PGSocket_recv

    # region FUNC_PGSocket_authenticate
    def authenticate(self, user: str, password: str, database: str) -> None:
        """Auth-хендшейк: StartupMessage → цикл R/S/K/Z → выбор механизма по коду.

        ▶ ┌user, password, database┐ → ○ StartupMessage (без type) → ○ loop: _recv →
          ◇ R: код 0 → OK | 3 → PgError | 5 → md5 | 10 → SCRAM first | 11 → SCRAM final
          | 12 → verify v= → ◇ Z → authenticated=True → ⎋ None | PgError

        ## @purpose  Полный стартовый диалог (инвариант 2 MODULE_CONTRACT): сервер шлёт
        ##            AuthenticationRequest с кодом — клиент выбирает механизм. MD5 — для
        ##            pg_hba md5 (172.16.0.0/12); SASL SCRAM-SHA-256 — для PG16 (default
        ##            password_encryption) и pgbouncer AUTH_TYPE=scram-sha-256. SASLFinal
        ##            (v=) сверяется с вычисленной server-подписью (подмена сервера).
        ## @io — ⇥ user/password/database: str → ⎋ None (ready for query) | PgError
        ## @complexity — O(i) — итерации PBKDF2 (SCRAM) или O(1) (md5)
        ## @raises — PgError: cleartext/неизвестный механизм, битый диалог, ошибка сервера
        """
        self._conn.sendall(build_startup_message(user, database))
        logger.info("[IMP:8][pgwire][auth] StartupMessage отправлен (user=%s db=%s)", user, database)
        while True:
            mtype, payload = self._recv()
            if mtype == _MSG_AUTH:
                if len(payload) < 4:
                    raise PgError("pgwire: AuthenticationRequest без кода")
                code = int.from_bytes(payload[:4], "big")
                if code == _AUTH_OK:
                    logger.info("[IMP:9][pgwire][auth] AuthenticationOk (код 0)")
                elif code == _AUTH_CLEARTEXT:
                    raise PgError(
                        "сервер запросил cleartext-пароль (код 3) — механизм не поддерживается; "
                        "настройте pg_hba на md5/scram-sha-256"
                    )
                elif code == _AUTH_MD5:
                    salt = payload[4:8]
                    if len(salt) != 4:
                        raise PgError("pgwire: MD5-salt не 4 байта")
                    self._send(_MSG_PASSWORD, md5_password_hash(user, password, salt).encode("ascii") + b"\x00")
                    logger.info("[IMP:8][pgwire][auth] md5-ответ отправлен (код 5)")
                elif code == _AUTH_SASL:
                    mechanisms = [m.decode("utf-8", errors="replace") for m in payload[4:].split(b"\x00") if m]
                    if SASL_MECHANISM not in mechanisms:
                        raise PgError(f"сервер не предлагает SCRAM-SHA-256: {mechanisms}")
                    bare, full = scram_client_first(user, self._nonce)
                    self._client_first_bare = bare
                    # 🔴 TRAP[BUG] · 2026-08-12 · P0 · pgwire: SASLInitialResponse missing Int32 length prefix
                    # · Symptom: PostgreSQL rejects with "insufficient data left in message (SQLSTATE 08P01)"
                    # · Root: PG wire protocol v3 SASLInitialResponse = mechanism\0 + Int32(len) + data;
                    #   code sent mechanism\0 + data without Int32 length prefix
                    # · Fix: add 4-byte big-endian length before SASL data
                    # · Prevention: wire-protocol unit test with captured server bytes (not just mock functions)
                    sasl_data = full.encode("utf-8")
                    self._send(
                        _MSG_PASSWORD,
                        SASL_MECHANISM.encode("ascii") + b"\x00" + len(sasl_data).to_bytes(4, "big") + sasl_data,
                    )
                    logger.info(
                        "[IMP:8][pgwire][auth] SASL client-first отправлен (код 10, механизм %s)", SASL_MECHANISM
                    )
                elif code == _AUTH_SASL_CONTINUE:
                    if self._client_first_bare is None:
                        raise PgError("pgwire: SASLContinue без client-first (нарушен порядок диалога)")
                    # BUG-3b fix: payload содержит auth_code(4 байта) + sasl_data; используем [4:]
                    server_first = payload[4:].decode("utf-8", errors="replace")
                    client_final, self._server_verify = scram_client_final(
                        user, password, self._client_first_bare, server_first
                    )
                    self._send(_MSG_PASSWORD, client_final.encode("utf-8"))
                    logger.info("[IMP:8][pgwire][auth] SASL client-final отправлен (код 11)")
                elif code == _AUTH_SASL_FINAL:
                    # BUG-3b fix: payload содержит auth_code(4 байта) + sasl_data; используем [4:]
                    server_final = payload[4:].decode("utf-8", errors="replace")
                    verify = server_final[2:] if server_final.startswith("v=") else ""
                    if self._server_verify and verify and verify != self._server_verify:
                        raise PgError("SCRAM server signature (v=) не совпала — подмена сервера")
                    logger.info("[IMP:8][pgwire][auth] SASLFinal принят (код 12)")
                else:
                    raise PgError(f"неподдерживаемый механизм аутентификации (код {code})")
            elif mtype in (_MSG_PARAM_STATUS, _MSG_BACKEND_KEY, _MSG_NOTICE):
                continue  # ParameterStatus / BackendKeyData / Notice — пропуск
            elif mtype == _MSG_ERROR:
                message, sqlstate = _parse_error_fields(payload)
                raise PgError(message, sqlstate)
            elif mtype == _MSG_READY:
                self._authenticated = True
                logger.info("[IMP:9][pgwire][auth] ReadyForQuery — соединение готово к query")
                return

    # endregion FUNC_PGSocket_authenticate

    # region FUNC_PGSocket_query
    def query(self, sql: str) -> list[list[object]]:
        """Simple Query: 'Q' + sql → парсинг T/D/C/E/Z → строки результата.

        ▶ ┌sql┐ → ◇ not authenticated → PgError → ○ _send('Q', sql\0) → ○ loop _recv:
          ◇ E → PgError → ⊕ D → rows → ◇ Z → ⎋ rows

        ## @purpose  Исполнение одного SQL-выражения (Simple Query — без параметров).
        ##            db.py: CREATE TABLE / DELETE / SELECT count(*) / INSERT. Любая
        ##            ошибка сервера → PgError (locust failure).
        ## @io — ⇥ sql: str → ⎋ list[list[object]] (bytes-значения | None)
        ## @complexity — O(R×V) — R строк × V колонок
        ## @raises — PgError: до authenticate(), ошибка сервера (ErrorResponse)
        """
        if not self._authenticated:
            raise PgError("pgwire: query до authenticate() — нарушен порядок протокола")
        self._send(_MSG_QUERY, sql.encode("utf-8") + b"\x00")
        rows: list[list[object]] = []
        while True:
            mtype, payload = self._recv()
            if mtype == _MSG_ERROR:
                message, sqlstate = _parse_error_fields(payload)
                raise PgError(message, sqlstate)
            if mtype == _MSG_DATA_ROW:
                rows.append(parse_data_row(payload))
            elif mtype == _MSG_READY:
                logger.info("[IMP:9][pgwire][query] %d строк (sql=%s)", len(rows), _sanitize_sql(sql))
                return rows
        # RowDescription / CommandComplete / Notice / Notification — пропускаются циклом

    # endregion FUNC_PGSocket_query

    # region FUNC_PGSocket_close
    def close(self) -> None:
        """Terminate ('X', длина 4, без payload) + закрытие сокета (идемпотентно).

        ▶ ┌—┐ → ◇ closed → return → ○ _send('X', b"") → ○ conn.close() → ⎋ None

        ## @purpose  Вежливое завершение протокола: сервер освобождает backend-процесс.
        ##            Идемпотентно (повторный close не падает).
        ## @io — ⇥ None → ⎋ None
        ## @complexity — O(1)
        """
        if self._conn is None:
            return
        with contextlib.suppress(OSError):
            self._send(_MSG_TERMINATE, b"")  # сервер уже закрыл — Terminate игнорируем
        try:
            self._conn.close()
        finally:
            self._conn = None  # type: ignore[assignment]
            self._authenticated = False

    # endregion FUNC_PGSocket_close


# endregion CLASS_PGSocket


# region FUNC__sanitize_sql
def _sanitize_sql(sql: str) -> str:
    """Санитизация SQL для логов: первая строка + длина (секреты/значения не логируются).

    ▶ ┌sql┐ → ○ first line → ○ truncate 80 → ⎋ str

    ## @purpose  Пароль/payload в INSERT могут содержать чувствительные данные — в логи
    ##            попадает только первая строка (обычно команда) и общая длина.
    ## @io — ⇥ sql: str → ⎋ str (обрезанная первая строка)
    ## @complexity — O(1)
    """
    first = sql.strip().splitlines()[0] if sql.strip() else ""
    return first[:80] + ("..." if len(first) > 80 else "")


# endregion FUNC__sanitize_sql


# region FUNC_connect
def connect(
    host: str,
    port: int = DEFAULT_PORT,
    user: str = "postgres",
    password: str = "",
    database: str = "platform",
    timeout: float = DEFAULT_TIMEOUT,
    nonce: str | None = None,
) -> PGSocket:
    """Открытие соединения + auth-хендшейк: socket.create_connection → PGSocket.authenticate.

    ▶ ┌host, port, user, password, database┐ → ○ socket.create_connection → ○ PGSocket
      → ○ authenticate → ⎋ PGSocket (готов к query) | PgError

    ## @purpose  Единая точка входа db.py: host — DNS-алиас docker-сети (postgres/pgbouncer
    ##            в shared-db-net), порт 5432. Таймаут 10s (инвариант 4).
    ## @io — ⇥ host: str (postgres|pgbouncer|IP), port: int = 5432, user/password/database:
    ##         str, timeout: float = 10.0, nonce: str | None (детерминированный SCRAM-тест)
    ##       → ⎋ PGSocket
    ## @complexity — O(хендшейк)
    ## @raises — PgError: сеть (create_connection), auth-диалог, таймаут
    """
    try:
        conn = socket.create_connection((host, port), timeout=timeout)
    except OSError as exc:
        raise PgError(f"pgwire: не удалось подключиться к {host}:{port} — {exc}") from exc
    pg = PGSocket(conn, timeout=timeout, nonce=nonce)
    pg.authenticate(user, password, database)
    logger.info("[IMP:9][pgwire][connect] соединение установлено: %s:%d (user=%s db=%s)", host, port, user, database)
    return pg


# endregion FUNC_connect
