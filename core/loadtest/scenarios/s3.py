# GREP_SUMMARY: locust s3 scenario minio optional PUT GET presigned sigv4 http-api no-boto3
# STRUCTURE: ▶ env LT_ENABLED (optional gate) → ◇ SigV4 presign (stdlib hmac/sha256) → ◇ S3User → ○ PUT+GET tasks → ⎋
# region MODULE_CONTRACT
## @purpose  Locust-сценарий s3 (DevPlan 146 W1, OPTIONAL): PUT/GET объектов MinIO через
##           HTTP API с SigV4-presigned URL — БЕЗ boto3 (boto3 отсутствует в locustio/locust
##           образе, DevPlan 146 §3.6). Presigner — чистый stdlib (hmac/hashlib/urllib),
##           работает и локально, и в контейнере на ноде.
## @scope    Запускается ТОЛЬКО locust. Включение: LOAD_SCENARIO_S3=1 + LT_S3_* env
##           (access/secret ключи MinIO, bucket, object). По умолчанию ВЫКЛЮЧЕН (optional).
## @invariants
##   - optional-контракт: LT_ENABLED != "true" → sys.exit(2) ДО создания user-классов
##   - Presign: payload hash = UNSIGNED-PAYLOAD (как botocore generate_presigned_url);
##     signed header — только host; path-style URL (endpoint/bucket/object)
##   - Ключи MinIO приходят env-ом (LT_S3_*), НЕ хардкодятся и не логируются
##   - RPS — constant_throughput (wait_time = rps_wait_time(LT_TARGET_RPS, LT_USERS),
##     единый helper — 146-m1 TASK-2/3); users — размер пула
## @rationale s3-сценарий реализован поверх HTTP API MinIO (S3-совместимый) — boto3
##            недоступен в locust-образе (риск R9, DevPlan 146 §7), SigV4-подпись
##            реализуется stdlib-ом без внешних зависимостей.
## @changes  2026-08-11 | DevPlan 146 W1 — Created
# endregion MODULE_CONTRACT

import datetime
import hashlib
import hmac
import os
import sys
import urllib.parse
from pathlib import Path

from locust import HttpUser, task

# RPS-механизм (DevPlan 146-m1 TASK-3): общий helper rps_wait_time из пакета scenarios.
# locust грузит -f файл как top-level модуль (load_locustfile: module_name = basename),
# поэтому относительный импорт `from . import rps_wait_time` невозможен — добавляем
# корень пакета в sys.path и импортируем top-level (local, remote-контейнер и pytest).
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from scenarios import rps_wait_time

LT_ENDPOINT: str = os.environ.get("LT_ENDPOINT", "").strip().rstrip("/")
LT_S3_ACCESS_KEY: str = os.environ.get("LT_S3_ACCESS_KEY", "")
LT_S3_SECRET_KEY: str = os.environ.get("LT_S3_SECRET_KEY", "")
LT_S3_BUCKET: str = os.environ.get("LT_S3_BUCKET", "loadtest")
LT_S3_OBJECT: str = os.environ.get("LT_S3_OBJECT", "loadtest-object.bin")
LT_SSL_VERIFY: bool = os.environ.get("LT_SSL_VERIFY", "false").lower() == "true"
LT_TARGET_RPS: float = float(os.environ.get("LT_TARGET_RPS", "0"))
LT_USERS: int = int(os.environ.get("LT_USERS", "1"))


# region FUNC__guard_enabled
def _guard_enabled() -> None:
    """Early exit if scenario disabled (optional-контракт db/s3).

    ▶ ┌env LT_ENABLED┐ → ◇ != "true" → sys.exit(2) → ⎋ None

    ## @purpose  Optional-сценарий вне runner не выполняется: locust падает с сообщением.
    ## @io — ⇥ None → ⎋ None | sys.exit(2)
    ## @complexity — O(1)
    """
    if os.environ.get("LT_ENABLED", "false").lower() != "true":
        sys.exit("scenario disabled (LT_ENABLED != true) — включите LOAD_SCENARIO_S3=1")


# endregion FUNC__guard_enabled


_guard_enabled()


# region FUNC__sigv4_key
def _sigv4_key(secret_key: str, date_stamp: str, region: str, service: str) -> bytes:
    """Цепочка HMAC-ключей SigV4: date → region → service → aws4_request.

    ▶ ┌secret, date, region, service┐ → ○ HMAC(AWS4+secret, date) → ○ HMAC(→, region)
      → ○ HMAC(→, service) → ○ HMAC(→, "aws4_request") → ⎋ signing_key

    ## @purpose  SigV4 signing-key derivation (AWS Signature V4) — чистый stdlib.
    ## @io — ⇥ secret_key: str, date_stamp: str (YYYYMMDD), region: str, service: str
    ##       → ⎋ bytes — signing key
    ## @complexity — O(1) — 4 HMAC-операции
    """
    k_date = hmac.new(("AWS4" + secret_key).encode("utf-8"), date_stamp.encode("utf-8"), hashlib.sha256).digest()
    k_region = hmac.new(k_date, region.encode("utf-8"), hashlib.sha256).digest()
    k_service = hmac.new(k_region, service.encode("utf-8"), hashlib.sha256).digest()
    return hmac.new(k_service, b"aws4_request", hashlib.sha256).digest()


# endregion FUNC__sigv4_key


# region FUNC_presign_url
def presign_url(
    method: str,
    url: str,
    access_key: str,
    secret_key: str,
    expires: int = 300,
    region: str = "us-east-1",
) -> str:
    """SigV4-presigned URL (path-style, UNSIGNED-PAYLOAD) — аналог botocore generate_presigned_url.

    ▶ ┌method, url, keys┐ → ○ canonical request (sorted X-Amz-* params, host header)
      → ○ string-to-sign → ○ signing key → ⊕ X-Amz-Signature → ⎋ presigned URL

    ## @purpose  Подпись URL для прямого HTTP-доступа к MinIO/S3 (PUT/GET) без boto3.
    ##            Полностью stdlib — работает в locustio/locust:2.32 на ноде.
    ## @io — ⇥ method: str (PUT|GET), url: str (http://host:9000/bucket/object),
    ##         access_key/secret_key: str, expires: int (s), region: str
    ##       → ⎋ str — URL с X-Amz-* query-параметрами и сигнатурой
    ## @complexity — O(1) — 1 canonical request + 4 HMAC + 1 SHA256
    ## @invariants
    ##   - payload hash = "UNSIGNED-PAYLOAD" (канон presigned URL, botocore-совместимо)
    ##   - SignedHeaders = только host; query-параметры сортируются по имени
    ##   - Ключи не попадают в URL/логи (только производная сигнатура)
    """
    parsed = urllib.parse.urlparse(url)
    host = parsed.netloc
    path = urllib.parse.quote(parsed.path or "/", safe="/-_.~")
    now = datetime.datetime.now(datetime.timezone.utc)
    amz_date = now.strftime("%Y%m%dT%H%M%SZ")
    date_stamp = now.strftime("%Y%m%d")
    credential_scope = f"{date_stamp}/{region}/s3/aws4_request"

    params = [
        ("X-Amz-Algorithm", "AWS4-HMAC-SHA256"),
        ("X-Amz-Credential", f"{access_key}/{credential_scope}"),
        ("X-Amz-Date", amz_date),
        ("X-Amz-Expires", str(expires)),
        ("X-Amz-SignedHeaders", "host"),
    ]
    params.sort(key=lambda kv: kv[0])
    canonical_query = "&".join(f"{urllib.parse.quote(k, safe='')}={urllib.parse.quote(v, safe='')}" for k, v in params)
    canonical_headers = f"host:{host}\n"
    canonical_request = f"{method}\n{path}\n{canonical_query}\n{canonical_headers}\nhost\nUNSIGNED-PAYLOAD"
    string_to_sign = (
        "AWS4-HMAC-SHA256\n"
        f"{amz_date}\n{credential_scope}\n"
        f"{hashlib.sha256(canonical_request.encode('utf-8')).hexdigest()}"
    )
    signing_key = _sigv4_key(secret_key, date_stamp, region, "s3")
    signature = hmac.new(signing_key, string_to_sign.encode("utf-8"), hashlib.sha256).hexdigest()
    return f"{parsed.scheme}://{parsed.netloc}{path}?{canonical_query}&X-Amz-Signature={signature}"


# endregion FUNC_presign_url


# region CLASS_S3User
class S3User(HttpUser):
    """Пользователь s3-сценария: PUT + GET объектов MinIO через presigned URL.

    ▶ ┌host=LT_ENDPOINT┐ → ○ task PUT (presigned, тело 128B) → ○ task GET (presigned) → ⎋

    ## @purpose  Нагрузка на MinIO (PUT/GET объектов) через HTTP API — boto3 не нужен.
    ##            Presigned URL вычисляется в рантайме задачи (expires=300s > run_time).
    ## @io — ⇥ env (модульный уровень) → ⎋ HTTP-запросы в цикле задач
    ## @invariants
    ##   - PUT-тело — детерминированный 128-байтный объект (не время-зависимый)
    ##   - verify=False при LT_SSL_VERIFY=false (самоподписанные серты тестовых нод)
    """

    host = LT_ENDPOINT
    wait_time = rps_wait_time(LT_TARGET_RPS, LT_USERS)

    @task
    def put_object(self) -> None:
        """PUT объекта через presigned URL (path-style endpoint/bucket/object)."""
        object_url = f"{LT_ENDPOINT}/{LT_S3_BUCKET}/{LT_S3_OBJECT}"
        url = presign_url("PUT", object_url, LT_S3_ACCESS_KEY, LT_S3_SECRET_KEY)
        self.client.put(
            url,
            data=b"loadtest-object-" + b"x" * 112,
            headers={"Content-Type": "application/octet-stream"},
            verify=LT_SSL_VERIFY,
        )

    @task
    def get_object(self) -> None:
        """GET объекта через presigned URL."""
        object_url = f"{LT_ENDPOINT}/{LT_S3_BUCKET}/{LT_S3_OBJECT}"
        url = presign_url("GET", object_url, LT_S3_ACCESS_KEY, LT_S3_SECRET_KEY)
        self.client.get(url, verify=LT_SSL_VERIFY)


# endregion CLASS_S3User
