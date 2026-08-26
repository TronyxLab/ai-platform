# GREP_SUMMARY: status-page collectors http curl-http-code classify-http curl-vhost curl-platform-service docker-dns resolve
# STRUCTURE: ▶ curl_http_code (единая curl-граница) → ◇ rc==0 && digit? → code | FAIL-dict
#            → ▶ classify_http (mode: vhost strict / platform 2xx-3xx) → ▶ curl_vhost (--resolve via nginx IP)
#            → ▶ curl_platform_service (Docker DNS) → ⎋ check dict
# region MODULE_CONTRACT
## @purpose  HTTP probe layer of status-page collectors — single curl boundary (curl_http_code)
##           + status classification, shared by vhost and platform-service probes (dedup of
##           curl_vhost/curl_platform_service, DevPlan 170 W7-E2). No behavior change.
## @scope    Consumed by collectors/checks/platform.py, collectors/aggregate.py
## @invariants
##   - NO core/internal imports (cross-layer violation forbidden for modules)
##   - curl_http_code: единственная точка subprocess.run("curl") — ловит TimeoutExpired/OSError
##   - classify_http: vhost — 200/401/403 = PASS (strict); platform — 200-399/401/403 = PASS
##   - curl_vhost: --resolve bypasses Docker embedded DNS; nginx IP unresolved → no --resolve (P4)
##   - curl_platform_service: без --resolve — Docker DNS резолвит внутренние имена напрямую
## @rationale  DevPlan 170 W7-E2 — curl-обвязка дедуплицирована: единый subprocess-вызов +
##            классификация по режиму; коды ошибок и лог-строки сохранены 1:1 (AC-G7).
## @changes  2026-08-15 · DevPlan 170 W7-E2 — extracted from collectors.py (dedup)
# endregion MODULE_CONTRACT

import socket
import subprocess
import sys
import time
from typing import TypedDict

# ── Константы (HTTP-коды) ──
HTTP_OK: int = 200  # успешный ответ
HTTP_REDIRECT_MIN: int = 200  # нижняя граница «приемлемых» redirect-кодов
HTTP_REDIRECT_MAX: int = 400  # верхняя граница (399 включительно)
_AUTH_REQUIRED_CODES: frozenset[int] = frozenset({401, 403})  # сервис жив, нужны креды → PASS


# region DATA_CurlResult
class CurlResult(TypedDict):
    """Результат curl-пробы (единая граница curl_http_code)."""

    http_code: int
    error: str | None
    duration_ms: int


# endregion DATA_CurlResult


# region DATA_CheckResult
class CheckResult(TypedDict, total=False):
    """Результат чека (container/vhost/platform_service) — единица checks[] агрегата.

    ## @purpose  Единый формат результата проверки: target/type/status + диагностика
    ##            (http_code/duration_ms/error; контейнерные чеки добавляют running/healthy/
    ##            exit_code/status_line).
    """

    target: str
    type: str
    status: str
    http_code: int
    duration_ms: int
    error: str | None
    running: bool
    healthy: bool
    exit_code: int | None
    status_line: str


# endregion DATA_CheckResult


# region FUNC_curl_http_code
# 🧐 TRAP[DECISION] · 2026-08-26 · — · Сознательная копия curl-обёртки (AI-0064, DevPlan 17 T5.1)
# · Rejected: импорт SoT core/internal/shared/http_probe.curl_http_code
# · Reason: module-contract status-page ЗАПРЕЩАЕТ импорт core/internal (контейнерный модуль,
#   кросс-слойный гейт #8) — копия неизбежна; семантика ошибок выровнена: OSError surfaced
#   в dict.error (fail-verbose), а не глотается
# · Rev: если статус-страница получит доступ к shared-слою или переедет в core/internal —
#   заменить на импорт SoT и удалить эту копию
def curl_http_code(
    url: str,
    timeout: int,
    label: str,
    *,
    stderr_limit: int | None = None,
    extra_args: list[str] | None = None,
) -> CurlResult:
    """Единая curl-граница: выполнить curl → вернуть http_code/error/duration_ms.

    # ▶ ┌url + timeout + label┐ → subprocess.run curl (-sSk -o /dev/null -w %{http_code})
    #    → ◇ rc==0 && digit? → ⎋ {http_code, error: None, duration_ms}
    #    → ◇ TimeoutExpired → ⎋ {0, "timeout after Ns", ...}
    #    → ◇ OSError → ⎋ {0, str(e), ...}
    #    → ◇ rc!=0 / not digit → ⎋ {code|0, "curl exit N: stderr", ...}

    label — диагностический префикс (vhost/platform); stderr_limit — обрезка stderr в error
    (platform: 100 символов — сохранение 1:1); extra_args — доп. curl-флаги (--resolve для vhost).
    """
    cmd = [
        "curl",
        "-sSk",
        "-o",
        "/dev/null",
        "-w",
        "%{http_code}",
        "--max-time",
        str(timeout),
    ]
    if extra_args:
        cmd += extra_args
    cmd.append(url)
    start = time.monotonic()
    try:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout + 2, check=False)
    except subprocess.TimeoutExpired:
        elapsed_ms = int((time.monotonic() - start) * 1000)
        print(f"[IMP:7][collectors][curl] {label} timeout after {timeout}s", file=sys.stderr)
        return {"http_code": 0, "error": f"timeout after {timeout}s", "duration_ms": elapsed_ms}
    except OSError as e:
        elapsed_ms = int((time.monotonic() - start) * 1000)
        print(f"[IMP:8][collectors][curl] {label} OSError: {e}", file=sys.stderr)
        return {"http_code": 0, "error": str(e), "duration_ms": elapsed_ms}
    elapsed_ms = int((time.monotonic() - start) * 1000)
    http_code = result.stdout.strip()
    if result.returncode == 0 and http_code.isdigit():
        return {"http_code": int(http_code), "error": None, "duration_ms": elapsed_ms}
    stderr_msg = result.stderr.strip()
    if stderr_limit is not None:
        stderr_msg = stderr_msg[:stderr_limit]
    return {
        "http_code": int(http_code) if http_code.isdigit() else 0,
        "error": f"curl exit {result.returncode}: {stderr_msg}",
        "duration_ms": elapsed_ms,
    }


# endregion FUNC_curl_http_code


# region FUNC_classify_http
def classify_http(code: int, *, vhost: bool) -> str:
    """HTTP code → check status ("PASS"|"WARN") по режиму пробы.

    # ▶ ┌code + mode┐ → ◇ 200/401/403 → "PASS" (оба режима: сервис жив, auth — не сбой)
    #                  → ◇ !vhost && 200..399 → "PASS" (platform терпит redirect'ы)
    #                  → ⎋ "WARN" (остальные 4xx/5xx — сервис отвечает, но деградирован)

    vhost (strict): 200 = явный успех; 401/403 = жив, нужны креды → PASS.
    platform: дополнительно 200-399 (некоторые сервисы отдают 301/302) → PASS.
    """
    if code == HTTP_OK or code in _AUTH_REQUIRED_CODES:
        return "PASS"
    if not vhost and (HTTP_REDIRECT_MIN <= code < HTTP_REDIRECT_MAX):
        return "PASS"
    return "WARN"


# endregion FUNC_classify_http


# region FUNC_curl_vhost
def curl_vhost(domain: str, timeout: int = 5) -> CheckResult:
    """Live-curl a vhost domain. Returns check result dict."""
    # ⚠️ TRAP[BUG] · 2026-08-12 · curl exit 49 на всех vhost-чеках ноды
    # · Symptom: /health FAIL — "Could not parse CURLOPT_RESOLVE entry 'tronyx.ru:443:nginx'"
    # · Root: curl --resolve требует IP в поле address; передавалось container name `nginx`
    # ·   (Docker DNS имя) → curl exit 49 → все expose:true проекты показывались FAIL.
    # · Fix: резолвим IP контейнера nginx внутри контейнера status-page (Docker DNS,
    # ·   proxy-net — подтверждено getent hosts nginx → 172.22.0.2) и подставляем IP.
    # · Fallback: если nginx не резолвится (тестовая среда без Docker) — --resolve
    # ·   опускается (P4-рационал: без --resolve curl идёт через Docker DNS).
    try:
        nginx_ip = socket.gethostbyname("nginx")
    except OSError:
        nginx_ip = None
    # --resolve bypasses Docker embedded DNS (127.0.0.11) which resolves *.tronyx.ru → localhost (P4)
    extra_args = ["--resolve", f"{domain}:443:{nginx_ip}"] if nginx_ip else []
    raw = curl_http_code(f"https://{domain}", timeout, f"vhost {domain}", extra_args=extra_args)
    if raw["error"] is None:
        return {
            "target": domain,
            "type": "vhost",
            "status": classify_http(raw["http_code"], vhost=True),
            "http_code": raw["http_code"],
            "duration_ms": raw["duration_ms"],
            "error": None,
        }
    return {
        "target": domain,
        "type": "vhost",
        "status": "FAIL",
        "http_code": raw["http_code"],
        "duration_ms": raw["duration_ms"],
        "error": raw["error"],
    }


# endregion FUNC_curl_vhost


# region FUNC_curl_platform_service
def curl_platform_service(internal_url: str, health_path: str, timeout: int = 5) -> CheckResult:
    """Live-curl a platform service via Docker internal DNS.

    # ▶ ┌internal_url (e.g. "grafana:3000") + health_path ("/api/health")┐
    #    → subprocess.run curl (без --resolve, через Docker DNS)
    #    → ⎋ check result dict: {target, type, status, duration_ms, error}

    Unlike _curl_vhost, this does NOT use --resolve — Docker DNS resolves
    internal service names directly (grafana:3000 → container IP).
    Timeout: 5s per check. stderr обрезается до 100 символов (сохранение 1:1).
    """
    url = f"http://{internal_url}{health_path}"
    target_host = internal_url.split(":", maxsplit=1)[0]
    raw = curl_http_code(url, timeout, f"platform {target_host}", stderr_limit=100)
    if raw["error"] is None:
        return {
            "target": target_host,
            "type": "platform_service",
            "status": classify_http(raw["http_code"], vhost=False),
            "http_code": raw["http_code"],
            "duration_ms": raw["duration_ms"],
            "error": None,
        }
    return {
        "target": target_host,
        "type": "platform_service",
        "status": "FAIL",
        "http_code": raw["http_code"],
        "duration_ms": raw["duration_ms"],
        "error": raw["error"],
    }


# endregion FUNC_curl_platform_service
