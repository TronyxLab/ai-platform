#!/usr/bin/env python3
# GREP_SUMMARY: preflight, gate, ssh-probe, disk-space, s3-probe, ghcr-probe, dockerhub, dns-resolution, graceful-degradation, fatal-warn
# STRUCTURE: ▶ ┌node.yaml + context┐ → ○ run_preflight(ssh|disk|s3|ghcr|dockerhub|dns) → ◇ classify(FATAL/WARN) → ⊕ JSON stdout → ⎋ exit 0|1
# region MODULE_CONTRACT
## @purpose  Pre-flight checks executed BEFORE the bootstrap state machine starts.
##           Probes SSH connectivity, disk space, S3/ghcr.io/Docker Hub availability,
##           and DNS resolution. FATAL checks abort bootstrap; WARN checks degrade gracefully.
## @scope    Called from node-lifecycle.sh main() (init mode) before the first checkpoint_step.
##           NOT invoked during --dry-run or --resume. Exits 0 if all FATAL checks pass.
## @invariants
##   1. FATAL checks: ssh_connectivity, disk_space → exit 1 on failure
##   2. WARN checks: s3, ghcr, docker_hub, dns → add to warnings list, continue
##   3. Each probe has a 10s timeout to avoid hanging bootstrap
##   4. Output: JSON dict to stdout {check_name: {status, latency_ms, detail}}
##   5. status ∈ {"ok", "warn", "fatal"}
##   6. Idempotent: safe to run multiple times (no side effects beyond probes)
##   7. Does NOT mutate node state or filesystem
## @rationale StatusReport 045 revealed that bootstrap proceeds even when ghcr.io/S3 are
##           unreachable, causing late failures at deploy time. A pre-flight gate surfaces
##           these issues early with actionable diagnostics. Graceful degradation (WARN)
##           for non-essential services preserves bootstrap resilience.
## @changes  2026-07-22 | DevPlan 047 Phase 1 — Created pre-flight gate module
## @changes  2026-08-14 | план 170 W1-A1 + W2-A1 — PROBE_TIMEOUT=10 → shared.timeouts.DOCKER_CMD_TIMEOUT
##                      (значение-дубль 10, U-11 канон); probe_ghcr_auth: bash-строковая инъекция токена
##                      → subprocess-список + токен через stdin (+DI-параметр runner для unit-тестов)
# endregion MODULE_CONTRACT

from __future__ import annotations

import argparse
import json
import logging
import os
import pathlib
import socket
import subprocess
import sys
import time
from collections.abc import Callable, Mapping
from dataclasses import asdict, dataclass, field
from typing import cast

from core.internal.config import platform_config
from core.internal.shared import docker_ops  # W1: docker manifest inspect примитив (гейт docker_sole_path)
from core.internal.shared.exceptions import ConfigNotFoundError, ConfigParseError
from core.internal.shared.node_yaml import NodeYaml
from core.internal.shared.timeouts import DOCKER_CMD_TIMEOUT  # U-11 канон: docker-cmd таймаут 10s

logger = logging.getLogger(__name__)

# ── Constants ──────────────────────────────────────────────────────────────
DISK_MIN_GB = 10  # minimum free disk space in GB at /opt
DISK_MIN_BYTES = DISK_MIN_GB * 1024 * 1024 * 1024

# Checks that abort bootstrap on failure
FATAL_CHECKS = ("ssh_connectivity", "disk_space")
# Checks that log a warning but allow bootstrap to continue
WARN_CHECKS = ("s3_connectivity", "ghcr_auth", "docker_hub_probe", "dns_resolution")


# region DATACLASSES


@dataclass
class CheckResult:
    """Result of a single pre-flight check.

    ## @purpose — Capture the outcome, timing, and detail of one probe.
    ## @io — ⇥ constructor params → ⎋ serializable CheckResult
    ## @complexity — O(1)
    """

    status: str = "ok"  # ok | warn | fatal
    latency_ms: int = 0
    detail: str = ""
    error: str | None = None

    def to_dict(self) -> dict[str, str | int]:
        """Serialize to JSON-compatible dict."""
        d = asdict(self)  # typeshed: dict[str, Any]
        if self.error is None:
            d.pop("error", None)
        return cast("dict[str, str | int]", d)  # W11-G3: asdict → Any; error=None исключён


@dataclass
class PreflightResult:
    """Aggregated result of all pre-flight checks.

    ## @purpose — Collect all check results and expose fatal/warn classification.
    ## @io — ⇥ checks dict → ⎋ serializable result with summary
    ## @complexity — O(N) where N = number of checks
    """

    checks: dict[str, CheckResult] = field(default_factory=dict)
    fatals: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)

    def add(self, name: str, result: CheckResult) -> None:
        """Add a check result and classify it."""
        self.checks[name] = result
        if result.status == "fatal":
            self.fatals.append(name)
        elif result.status == "warn":
            self.warnings.append(name)

    def has_fatals(self) -> bool:
        """Return True if any FATAL check failed."""
        return len(self.fatals) > 0

    def to_dict(self) -> dict[str, dict[str, str | int]]:
        """Serialize to JSON-compatible dict."""
        return {name: result.to_dict() for name, result in self.checks.items()}


# endregion DATACLASSES


# region PROBE_FUNCTIONS


# region FUNC_probe_ssh_connectivity
## @purpose — Probe SSH connectivity by checking the local SSH service and node availability.
##            On the bootstrap node itself, this verifies the sshd socket is listening on port 22.
## @io — ⇥ host: str (default 127.0.0.1), port: int (default 22) → ⎋ CheckResult
## @complexity — O(1)
## @invariants
##   - FATAL: if sshd not listening → bootstrap cannot proceed
##   - Uses socket connection with DOCKER_CMD_TIMEOUT
def probe_ssh_connectivity(host: str = "127.0.0.1", port: int = 22) -> CheckResult:
    """Probe SSH connectivity via TCP socket check. FATAL on failure."""
    start = time.monotonic()
    try:
        with socket.create_connection((host, port), timeout=DOCKER_CMD_TIMEOUT):
            latency = int((time.monotonic() - start) * 1000)
            logger.info("[IMP:9][preflight][ssh] SSH probe OK (%dms)", latency)
            return CheckResult(status="ok", latency_ms=latency, detail=f"SSH reachable at {host}:{port}")
    except (TimeoutError, ConnectionRefusedError, OSError) as e:
        latency = int((time.monotonic() - start) * 1000)
        return CheckResult(
            status="fatal",
            latency_ms=latency,
            detail=f"SSH unreachable at {host}:{port}",
            error=str(e),
        )


# endregion FUNC_probe_ssh_connectivity


# region FUNC_probe_disk_space
## @purpose — Probe available disk space at a target path (default /opt).
##            FATAL if free space < 10 GB — bootstrap needs room for images, volumes, secrets.
## @io — ⇥ path: str (default /opt) → ⎋ CheckResult
## @complexity — O(1)
## @invariants
##   - FATAL: if free_bytes < DISK_MIN_BYTES (10 GB)
##   - Uses shutil.disk_usage (no subprocess call)
def probe_disk_space(path: str = "/opt") -> CheckResult:
    """Probe free disk space. FATAL if below threshold."""
    import shutil

    start = time.monotonic()
    # ruff: ignore[PLW0717] — тело try присваивает имена, читаемые except/после — извлечение ломает видимость
    try:
        # Use parent if path doesn't exist (common during pre-bootstrap)
        probe_path = path
        while not pathlib.Path(probe_path).exists() and probe_path != "/":
            probe_path = pathlib.Path(probe_path).parent

        usage = shutil.disk_usage(probe_path)
        latency = int((time.monotonic() - start) * 1000)
        free_gb = round(usage.free / (1024 * 1024 * 1024), 1)
        if usage.free < DISK_MIN_BYTES:
            return CheckResult(
                status="fatal",
                latency_ms=latency,
                detail=f"Disk space at {probe_path}: {free_gb} GB free (need ≥{DISK_MIN_GB} GB)",
                error=f"insufficient_disk_space: {free_gb} GB < {DISK_MIN_GB} GB",
            )
        logger.info("[IMP:9][preflight][disk] Disk OK: %s has %s GB free", probe_path, free_gb)
        return CheckResult(status="ok", latency_ms=latency, detail=f"Disk space at {probe_path}: {free_gb} GB free")
    except OSError as e:
        latency = int((time.monotonic() - start) * 1000)
        return CheckResult(
            status="fatal",
            latency_ms=latency,
            detail=f"Cannot probe disk at {path}",
            error=str(e),
        )


# endregion FUNC_probe_disk_space


# region FUNC_probe_s3_connectivity
## @purpose — Probe S3 connectivity by attempting a HEAD bucket request.
##            WARN if S3 unavailable — cert restore will fall back to acme.sh.
## @io — ⇥ endpoint: str, bucket: str, access_key: str, secret_key: str,
##          s3_client: object | None (W4b DI: fake-клиент в тестах; None → get_s3_client()) → ⎋ CheckResult
## @complexity — O(1) + network
## @invariants
##   - WARN: if S3 unreachable → graceful degradation (cert_orchestrator handles fallback)
##   - If credentials missing → WARN (not fatal — S3 is optional)
##   - s3_client параметром (W4b): ленивый default = shared/s3_client.get_s3_client (ровно текущее)
## @changes 2026-08-13 | DevPlan 160 W4b — +s3_client (инъекция фабрики)
def probe_s3_connectivity(
    endpoint: str = "",
    bucket: str = "",
    access_key: str = "",
    secret_key: str = "",
    *,
    s3_client: object | None = None,
) -> CheckResult:
    """Probe S3 connectivity via boto3 head_bucket. WARN on failure."""
    start = time.monotonic()
    if not bucket or not access_key or not secret_key:
        latency = int((time.monotonic() - start) * 1000)
        return CheckResult(
            status="warn",
            latency_ms=latency,
            detail="S3 credentials not configured — cert restore will use acme.sh only",
        )
    client_error_cls: type[BaseException] = (
        OSError  # гарантия binding для `except client_error_cls` (обе ветки try переопределяют)
    )
    # ruff: ignore[PLW0717] — тело try >5 операторов (длинный S3-probe блок) — извлечение неразумно
    try:
        if s3_client is None:
            # ⚠️ TRAP[BUG] · 2026-08-03 · P1 · lazy-импорт s3_client (boto3) — RC-сессия 121
            # · Symptom: preflight PanicException pyo3_runtime на свежей ноде — module-level импорт
            #   s3_client тянул boto3→pyopenssl(debian)→cryptography(pip) несовместимость.
            # · Fix: импорт ВНУТРИ probe (префлайт — лёгкая диагностика, не должен тянуть boto3);
            #   ImportError → WARN (boto3 появится в φ1 python_deps — preflight идёт ДО него).
            from botocore.exceptions import ClientError  # type: ignore[import-untyped]

            from core.internal.shared.s3_client import (
                get_s3_client as _shared_get_s3_client,  # pyright: ignore[reportUnknownVariableType] — W11-G1 cross-file: s3_client.get_s3_client аннотирован `-> boto3.client` (функция-as-тип) → Unknown
            )

            ep = endpoint or os.environ.get("S3_ENDPOINT_URL", "https://s3.timeweb.cloud")
            # DevPlan 117 D26: клиент создаётся через shared/s3_client.get_s3_client
            # (max_attempts=1 — быстрый probe; proxy-stripping не требуется для head_bucket)
            client = cast(
                "object",
                _shared_get_s3_client(endpoint=ep, access_key=access_key, secret_key=secret_key, max_attempts=1),
            )  # W11-G1 cross-file: shared get_s3_client → Unknown
            client_error_cls = ClientError
        else:
            # W4b (160 T4.2): переданный fake-клиент — boto3 не импортируется (тест без boto3);
            # boto-ошибки переданного клиента мапятся в общую OSError/BaseException-ветку.
            client = cast("object", s3_client)
            ep = endpoint or os.environ.get("S3_ENDPOINT_URL", "https://s3.timeweb.cloud")
            client_error_cls = OSError  # type: ignore[assignment] — переданный клиент не бото
        client.head_bucket(Bucket=bucket)  # pyright: ignore[reportAttributeAccessIssue, reportUnknownMemberType] — boto3-клиент (stub-less, object); DI-переданный fake тоже поддерживает head_bucket
        latency = int((time.monotonic() - start) * 1000)
        logger.info("[IMP:9][preflight][s3] S3 probe OK (%dms) — bucket %s reachable", latency, bucket)
        return CheckResult(status="ok", latency_ms=latency, detail=f"S3 bucket {bucket} reachable at {ep}")
    except ImportError:
        latency = int((time.monotonic() - start) * 1000)
        return CheckResult(
            status="warn",
            latency_ms=latency,
            detail="boto3 not installed — S3 cert restore unavailable",
        )
    except client_error_cls as e:
        latency = int((time.monotonic() - start) * 1000)
        resp = cast("dict[str, object]", getattr(e, "response", {}))  # W11-G3: getattr → Any (botocore-ответ)
        err_map = cast("dict[str, object]", resp.get("Error", {}))
        code = str(err_map.get("Code", "Unknown"))
        return CheckResult(
            status="warn",
            latency_ms=latency,
            detail=f"S3 head_bucket failed (code={code}) — cert restore degraded",
            error=str(e)[:200],
        )
    except (OSError, ConnectionError, TimeoutError) as e:
        latency = int((time.monotonic() - start) * 1000)
        return CheckResult(
            status="warn",
            latency_ms=latency,
            detail="S3 probe error — cert restore degraded",
            error=str(e)[:200],
        )
    # ruff: ignore[BLE001] — boto3/S3 probe — широкий спектр API, probe не должен ронять bootstrap
    except BaseException as e:
        latency = int((time.monotonic() - start) * 1000)
        logger.warning(
            "[IMP:8][preflight][s3] S3 probe crashed (%s: %s) — WARN, bootstrap continues",
            type(e).__name__,
            str(e)[:100],
        )
        return CheckResult(
            status="warn",
            latency_ms=latency,
            detail="S3 probe crashed — cert restore degraded",
            error=f"{type(e).__name__}: {str(e)[:100]}",
        )


# endregion FUNC_probe_s3_connectivity


# region FUNC_probe_ghcr_auth
## @purpose — Probe ghcr.io authentication by attempting a docker login with the token over stdin.
##            WARN if ghcr.io unavailable — context_deployer will fall back to on-node build.
## @io — ⇥ token: str (GHCR pull token), runner: Callable | None (DI: инъекция subprocess-раннера
##          для unit-тестов; None → subprocess.run) → ⎋ CheckResult
## @complexity — O(1) + network
## @invariants
##   - WARN: if ghcr.io unreachable → context_deployer uses build fallback
##   - If token missing → WARN (not fatal — build fallback is valid)
##   - Токен НИКОГДА не интерполируется в shell-строку (W2-A1 C3) — передаётся через stdin
##     (--password-stdin), в логах/детали не печатается
##   - runner — DI-параметр (DI-HYG: unit-тесты без monkeypatch.setattr на модуль)
## @changes 2026-08-14 | план 170 W2-A1 — bash -c "echo '<token>' | docker login" → subprocess-список
##                      + input=stdin (+runner DI-параметр); TRAP[BUG] shell-инъекция токена
def probe_ghcr_auth(
    token: str = "",
    *,
    runner: Callable[..., subprocess.CompletedProcess[str]] | None = None,
) -> CheckResult:
    """Probe ghcr.io auth via docker login (token over stdin). WARN on failure.

    ▶ ┌token┐ → ◇ пуст? → warn | ○ run([docker, login, --username, x-access-token,
    --password-stdin, ghcr.io], input=token) → ◇ rc==0? → ok | ⎋ warn
    """
    start = time.monotonic()
    if not token:
        latency = int((time.monotonic() - start) * 1000)
        return CheckResult(
            status="warn",
            latency_ms=latency,
            detail="GHCR_PULL_TOKEN not set — context deploy will use build fallback",
        )
    run_cmd: Callable[..., subprocess.CompletedProcess[str]] = runner if runner is not None else subprocess.run
    try:
        # ⚠️ TRAP[BUG] · 2026-08-14 · P1 · shell-инъекция токена в bash-строку docker login
        # · Symptom: GHCR_PULL_TOKEN со спецсимволами ('$', '`', ';', кавычки, пробел) ломал
        # ·   `bash -c "echo '<token>' | docker login ..."` — синтаксическая ошибка bash или,
        # ·   при целенаправленной инъекции, выполнение произвольных команд с правами bootstrap.
        # · Root: токен интерполировался в bash-строку без shlex.quote — «'» обрывал строку,
        # ·   «$»/«`»/«;» интерпретировались bash-ом как управляющие символы.
        # · Fix: subprocess.run со СПИСКОМ аргументов (0 shell) + токен через stdin
        # ·   (docker login --password-stdin) — спецсимволы токена остаются литералами.
        # · Prevention: запрет bash -c при передаче секретов; unit-тест на спецсимволы
        # ·   (tests/unit/test_preflight.py: негатив с '$', '\'', '"', пробелом, ';' — вызов
        # ·   проверяется как список аргументов без shell и одним аргументом-токеном в stdin).
        result = run_cmd(
            ["docker", "login", "--username", "x-access-token", "--password-stdin", "ghcr.io"],
            input=token,
            capture_output=True,
            text=True,
            timeout=DOCKER_CMD_TIMEOUT,
            check=False,
        )
        latency = int((time.monotonic() - start) * 1000)
        if result.returncode == 0:
            logger.info("[IMP:9][preflight][ghcr] ghcr.io auth OK (%dms)", latency)
            return CheckResult(status="ok", latency_ms=latency, detail="ghcr.io authentication successful")
        return CheckResult(
            status="warn",
            latency_ms=latency,
            detail="ghcr.io login failed — context deploy will use build fallback",
            error=result.stderr.strip()[:200],
        )
    except (subprocess.TimeoutExpired, FileNotFoundError) as e:
        latency = int((time.monotonic() - start) * 1000)
        return CheckResult(
            status="warn",
            latency_ms=latency,
            detail="ghcr.io probe error — build fallback will be used",
            error=str(e)[:200],
        )


# endregion FUNC_probe_ghcr_auth


# region FUNC_probe_docker_hub
## @purpose — Probe Docker Hub reachability by attempting to pull hello-world manifest.
##            WARN if rate-limited (429) — Docker Hub auth (docker login) mitigates.
## @io — ⇥ None → ⎋ CheckResult
## @complexity — O(1) + network
## @invariants
##   - WARN: if Docker Hub rate-limited → authenticated pulls (docker login) mitigate
##   - Non-fatal: even if Docker Hub is down, local image cache may suffice
def probe_docker_hub() -> CheckResult:
    """Probe Docker Hub reachability via manifest inspect. WARN on rate-limit."""
    start = time.monotonic()
    # ruff: ignore[PLW0717] — тело try присваивает имена, читаемые except/после — извлечение ломает видимость
    try:
        # W1 (DevPlan 128): docker manifest inspect — shared/docker_ops (raw variant — нужен stderr для 429)
        result = docker_ops.docker_manifest_inspect_raw("hello-world:latest", timeout=DOCKER_CMD_TIMEOUT)
        latency = int((time.monotonic() - start) * 1000)
        if result.returncode == 0:
            logger.info("[IMP:9][preflight][dockerhub] Docker Hub reachable (%dms)", latency)
            return CheckResult(status="ok", latency_ms=latency, detail="Docker Hub reachable")
        stderr = result.stderr.strip()
        if "429" in stderr or "rate limit" in stderr.lower():
            return CheckResult(
                status="warn",
                latency_ms=latency,
                detail="Docker Hub rate-limited (429) — authenticated docker login mitigates",
                error=stderr[:200],
            )
        return CheckResult(
            status="warn",
            latency_ms=latency,
            detail="Docker Hub manifest inspect failed — registry-mirror will be configured",
            error=stderr[:200],
        )
    except (subprocess.TimeoutExpired, FileNotFoundError) as e:
        latency = int((time.monotonic() - start) * 1000)
        return CheckResult(
            status="warn",
            latency_ms=latency,
            detail="Docker Hub probe error — registry-mirror will be configured",
            error=str(e)[:200],
        )


# endregion FUNC_probe_docker_hub


# region FUNC_probe_dns_resolution
## @purpose — Probe DNS resolution for a domain (platform domain from node.yaml).
##            WARN if DNS fails — cert issuance via DNS-01 will fail.
## @io — ⇥ domain: str → ⎋ CheckResult
## @complexity — O(1)
## @invariants
##   - WARN: if DNS resolution fails → cert issue will fail (non-fatal, nginx can start without HTTPS)
##   - Uses socket.getaddrinfo for resolution
def probe_dns_resolution(domain: str = "") -> CheckResult:
    """Probe DNS resolution for a domain. WARN on failure."""
    start = time.monotonic()
    if not domain:
        latency = int((time.monotonic() - start) * 1000)
        return CheckResult(
            status="warn",
            latency_ms=latency,
            detail="No domain configured — DNS probe skipped",
        )
    # ruff: ignore[PLW0717] — тело try присваивает имена, читаемые except/после — извлечение ломает видимость
    try:
        socket.setdefaulttimeout(DOCKER_CMD_TIMEOUT)
        try:
            socket.getaddrinfo(domain, None)
        finally:
            socket.setdefaulttimeout(None)
        latency = int((time.monotonic() - start) * 1000)
        logger.info("[IMP:9][preflight][dns] DNS resolution OK for %s (%dms)", domain, latency)
        return CheckResult(status="ok", latency_ms=latency, detail=f"DNS resolves for {domain}")
    except socket.gaierror as e:
        latency = int((time.monotonic() - start) * 1000)
        return CheckResult(
            status="warn",
            latency_ms=latency,
            detail=f"DNS resolution failed for {domain} — cert issuance may fail",
            error=str(e)[:200],
        )
    except OSError as e:
        latency = int((time.monotonic() - start) * 1000)
        return CheckResult(
            status="warn",
            latency_ms=latency,
            detail=f"DNS probe error for {domain}",
            error=str(e)[:200],
        )


# endregion FUNC_probe_dns_resolution


# endregion PROBE_FUNCTIONS


# region RUN_ALL_CHECKS


# region FUNC_run_preflight
## @purpose — Run all pre-flight checks and return aggregated result.
##            FATAL checks abort bootstrap; WARN checks are collected.
## @io — ⇥ node_yaml: str, context: str, node_name: str,
##       probes: Mapping[str, Callable] | None (W3.5-4 DI: per-check переопределение;
##       None → модульные probe-функции) → ⎋ PreflightResult
## @complexity — O(N) where N = number of checks (6 probes)
## @invariants
##   - All checks execute regardless of individual failures (no short-circuit)
##   - FATAL checks are: ssh_connectivity, disk_space
##   - WARN checks are: s3_connectivity, ghcr_auth, docker_hub_probe, dns_resolution
## @changes 2026-08-14 | W3.5-4 (164 S8) — +probes DI (тесты без monkeypatch модульных probe-функций)
def run_preflight(
    node_yaml: str = "",
    context: str = "",
    node_name: str = "",
    *,
    probes: Mapping[str, Callable[..., CheckResult]] | None = None,
) -> PreflightResult:
    """Run all pre-flight checks, return aggregated result.

    ▶ ┌node.yaml + env┐ → ○ 6 probes in sequence → ◇ classify FATAL/WARN → ⊕ PreflightResult → ⎋
    """
    result = PreflightResult()

    # Extract platform domain from node.yaml for DNS probe
    domain = os.environ.get("PLATFORM_DOMAIN", "")
    if not domain and node_yaml and os.path.isfile(node_yaml):
        domain = _extract_domain_from_node_yaml(node_yaml)

    # Extract S3 credentials from env
    s3_endpoint = os.environ.get("S3_ENDPOINT_URL", "https://s3.timeweb.cloud")
    s3_bucket = os.environ.get("S3_BUCKET", platform_config.default_s3_bucket_sentinel())
    s3_access_key = os.environ.get("S3_ACCESS_KEY", "")
    s3_secret_key = os.environ.get("S3_SECRET_KEY", "")
    ghcr_token = os.environ.get("GHCR_PULL_TOKEN", "")

    logger.info("[IMP:8][preflight] Running pre-flight checks (context=%s, node=%s)", context, node_name)

    # ── W3.5-4 DI: probes-переопределение (None → модульные функции) ──
    probe_map: Mapping[str, Callable[..., CheckResult]] = probes if probes is not None else {}
    ssh_probe = probe_map.get("ssh_connectivity", probe_ssh_connectivity)
    disk_probe = probe_map.get("disk_space", probe_disk_space)
    s3_probe = probe_map.get(
        "s3_connectivity", lambda: probe_s3_connectivity(s3_endpoint, s3_bucket, s3_access_key, s3_secret_key)
    )
    ghcr_probe = probe_map.get("ghcr_auth", lambda: probe_ghcr_auth(ghcr_token))
    docker_hub_probe = probe_map.get("docker_hub_probe", probe_docker_hub)
    dns_probe = probe_map.get("dns_resolution", lambda: probe_dns_resolution(domain))

    # ── FATAL checks ──
    result.add("ssh_connectivity", ssh_probe())
    result.add("disk_space", disk_probe())

    # ── WARN checks ──
    result.add("s3_connectivity", s3_probe())
    result.add("ghcr_auth", ghcr_probe())
    result.add("docker_hub_probe", docker_hub_probe())
    result.add("dns_resolution", dns_probe())

    if result.has_fatals():
        logger.error("[IMP:10][preflight] FATAL checks failed: %s", result.fatals)
    else:
        logger.info(
            "[IMP:9][preflight] Pre-flight passed (warnings: %s, fatals: 0)",
            len(result.warnings),
        )
    return result


# endregion FUNC_run_preflight


# region FUNC_extract_domain_from_node_yaml
## @purpose — Extract platform domain from node.yaml for DNS probe.
## @io — ⇥ node_yaml_path: str → ⎋ str (empty if not found)
## @complexity — O(N) for YAML parse
def _extract_domain_from_node_yaml(node_yaml_path: str) -> str:
    """Extract platform domain from node.yaml."""
    # ruff: ignore[PLW0717] — тело try >5 операторов (домен-резолв блок) — извлечение неразумно
    try:
        node = NodeYaml(node_yaml_path)
        cfg = node.get_domain_config()
        domain = cfg.platform_domain
        if not domain:
            domain = node.get("node.platform_domain", default="")
            if not domain:
                domain = node.get("node.domain", default="")
    except (ConfigNotFoundError, ConfigParseError, OSError) as e:
        logger.warning("[IMP:7][preflight] Failed to extract domain from %s: %s", node_yaml_path, e)
        return ""
    else:
        return domain or ""


# endregion FUNC_extract_domain_from_node_yaml


# ═══════════════════════════════════════════════════════════════════
# DevPlan 029 T7 — input-contract scope (--scope input): AGE-форма /
# env-vs-file приоритет / sops-наличие / required-ключи — ЛОКАЛЬНО, 0 remote.
# Двойная точка входа: bootstrap.sh первый шаг + operator verb validate-node-input
# (DD-4: расширение preflight, не параллельный механизм).
# ═══════════════════════════════════════════════════════════════════


# region FUNC_probe_age_key_shape
## @purpose — T7 input-probe: AGE-ключ найден (цепочка node_detect) И env-форма single-line
##            (многострочный AGE_SECRET_KEY env = операторская ошибка — fail до SSH, AC6).
## @io — ⇥ env: Mapping | None (DI; None = os.environ) → ⎋ CheckResult (fatal при отсутствии/кривой форме)
## @complexity — O(1) + чтение файлов цепочки node_detect
def probe_age_key_shape(*, env: Mapping[str, str] | None = None) -> CheckResult:
    """Validate AGE key presence + single-line shape (input contract, T7)."""
    from core.internal.shared.node_detect import detect_age_key

    source: Mapping[str, str] = os.environ if env is None else env
    raw_env = (source.get("AGE_SECRET_KEY", "") or "").strip()
    # Shape-check ТОЛЬКО для env-значения (AGE_SECRET_KEY_FILE — файл, comment-scan каноничен).
    if raw_env and ("\n" in raw_env or not raw_env.startswith("AGE-SECRET-KEY-")):
        return CheckResult(
            status="fatal",
            detail="AGE_SECRET_KEY env не single-line/не AGE-SECRET-KEY-… — задайте "
            "AGE_SECRET_KEY_FILE или single-line значение (age-keygen value)",
        )
    key = detect_age_key(env=source)
    if not key:
        return CheckResult(
            status="fatal",
            detail="AGE_SECRET_KEY не найден (цепочка: env → SOPS_AGE_KEY → AGE_SECRET_KEY_FILE "
            "→ ~/.config/age/keys.txt → /etc/age/key.txt)",
        )
    return CheckResult(status="ok", detail="AGE key found, single-line canonical form")


# endregion FUNC_probe_age_key_shape


# region FUNC_probe_env_file_priority
## @purpose — T7 input-probe (WARN): REF-0007 env-перекрытие — AGE_SECRET_KEY env задан
##            И AGE_SECRET_KEY_FILE задан с ДРУГИМ ключом → WARN оператору
##            («unset AGE_SECRET_KEY» — канон core/AGENTS.md §Hook-окружение).
## @io — ⇥ env: Mapping | None (DI) → ⎋ CheckResult (warn при конфликте env-vs-file)
## @complexity — O(1) + чтение первой AGE-строки файла
def probe_env_file_priority(*, env: Mapping[str, str] | None = None) -> CheckResult:
    """Env-vs-file priority conflict detection (input contract, T7)."""
    source: Mapping[str, str] = os.environ if env is None else env
    env_key = (source.get("AGE_SECRET_KEY", "") or "").strip()
    file_path = (source.get("AGE_SECRET_KEY_FILE", "") or "").strip()
    if not env_key or not file_path:
        return CheckResult(status="ok", detail="no env/file conflict (один источник AGE)")
    try:
        with pathlib.Path(file_path).open(encoding="utf-8") as key_fh:
            file_key = next(
                (ln.strip() for ln in key_fh if ln.strip().startswith("AGE-SECRET-KEY-")),
                "",
            )
    except OSError as e:
        return CheckResult(status="warn", detail=f"AGE_SECRET_KEY_FILE unreadable: {e}")
    env_canon = next((ln.strip() for ln in env_key.splitlines() if ln.strip().startswith("AGE-SECRET-KEY-")), env_key)
    if file_key and file_key != env_canon:
        return CheckResult(
            status="warn",
            detail="AGE_SECRET_KEY env ПЕРЕКРЫВАЕТ AGE_SECRET_KEY_FILE с другим ключом — предпочтительнее файл (unset AGE_SECRET_KEY)",
        )
    return CheckResult(status="ok", detail="env/file источники согласованы")


# endregion FUNC_probe_env_file_priority


# region FUNC_probe_sops_enc_file
## @purpose — T7 input-probe: sops-наличие — enc-файл ноды (configs_dir/secrets/{node}.enc.yaml)
##            существует; отсутствие допустимо ТОЛЬКО с node.yaml#secrets.allow_autogen=true.
## @io — ⇥ node_yaml: str, node_name: str, env: Mapping | None (DI) → ⎋ CheckResult
## @complexity — O(1) + isfile (кандидаты configs_dir/env/remote)
def probe_sops_enc_file(*, node_yaml: str, node_name: str, env: Mapping[str, str] | None = None) -> CheckResult:
    """Check SOPS/age enc-file presence for the node (input contract, T7)."""
    source: Mapping[str, str] = os.environ if env is None else env
    allow_autogen = False
    if node_yaml and pathlib.Path(node_yaml).is_file():
        try:
            raw = NodeYaml(node_yaml).get("secrets.allow_autogen", default=False)
            allow_autogen = bool(raw) if isinstance(raw, bool) else str(raw).strip().lower() == "true"
        except (ConfigNotFoundError, ConfigParseError, OSError) as exc:
            logger.warning("[IMP:7][preflight][input] allow_autogen unreadable from %s: %s", node_yaml, exc)

    # configs_dir: NODE_CONFIGS_DIR env → канон /opt/node-configs → рядом с node.yaml (repo)
    configs_dir = (source.get("NODE_CONFIGS_DIR", "") or "").strip()
    candidates: list[str] = []
    if configs_dir:
        candidates.append(str(pathlib.Path(configs_dir) / "secrets" / f"{node_name}.enc.yaml"))
    if node_yaml:
        yaml_dir = pathlib.Path(node_yaml).parent.parent  # <configs>/<node>/node.yaml
        candidates.append(str(yaml_dir / "secrets" / f"{node_name}.enc.yaml"))
    from core.internal.shared.deploy_paths import node_configs_remote

    candidates.append(str(node_configs_remote(env=source) / "secrets" / f"{node_name}.enc.yaml"))
    enc = next((c for c in candidates if pathlib.Path(c).is_file()), None)
    if enc is not None:
        return CheckResult(status="ok", detail=f"SOPS enc-file present: {enc}")
    if allow_autogen:
        return CheckResult(status="warn", detail="enc-file отсутствует — autogen-only нода (allow_autogen=true)")
    return CheckResult(
        status="fatal",
        detail=f"SOPS/age enc-file не найден для node={node_name} (искал: {', '.join(candidates)}). "
        "Предоставьте enc-файл или установите node.yaml#secrets.allow_autogen: true",
    )


# endregion FUNC_probe_sops_enc_file


# region FUNC_probe_required_keys
## @purpose — T7 input-probe: required-ключи node.yaml#secrets.required[] — каждый env_var
##            обязан резолвиться в непустое значение ЛОКАЛЬНО (0 remote).
## @io — ⇥ node_yaml: str, env: Mapping | None (DI) → ⎋ CheckResult (fatal при missing)
## @complexity — O(R) — R = required записей node.yaml
def probe_required_keys(*, node_yaml: str, env: Mapping[str, str] | None = None) -> CheckResult:
    """Check required secret env_vars from node.yaml resolve locally (input contract, T7)."""
    source: Mapping[str, str] = os.environ if env is None else env
    if not node_yaml or not pathlib.Path(node_yaml).is_file():
        return CheckResult(status="ok", detail="node.yaml не задан — required-ключи не проверяемы")
    try:
        required = NodeYaml(node_yaml).get("secrets.required", default=[])
    except (ConfigNotFoundError, ConfigParseError, OSError) as exc:
        return CheckResult(status="warn", detail=f"secrets.required unreadable: {exc}")
    if not isinstance(required, list) or not required:
        return CheckResult(status="ok", detail="node.yaml не объявляет secrets.required")
    missing: list[str] = []
    required_entries: list[object] = list(required)
    for entry_raw in required_entries:
        if not isinstance(entry_raw, dict):
            continue
        entry_map = cast("dict[str, object]", entry_raw)
        env_var = str(entry_map.get("env_var", "") or "")
        if env_var and not (source.get(env_var, "") or "").strip():
            missing.append(env_var)
    if missing:
        return CheckResult(status="fatal", detail=f"required env-ключи не заданы: {', '.join(missing)}")
    return CheckResult(status="ok", detail=f"{len(required)} required env-ключ(а) присутствуют")


# endregion FUNC_probe_required_keys


# region FUNC_run_input_preflight
## @purpose — T7: прогон input-scope проб (локальные входные контракты, 0 remote).
## @io — ⇥ node_yaml/node_name + env DI → ⎋ PreflightResult
## @complexity — O(1) + 4 локальные пробы
def run_input_preflight(
    *,
    node_yaml: str = "",
    node_name: str = "",
    env: Mapping[str, str] | None = None,
) -> PreflightResult:
    """Run local input-contract probes (AGE/sops/required) — before ANY SSH (T7)."""
    result = PreflightResult()
    result.add("age_key_shape", probe_age_key_shape(env=env))
    result.add("sops_enc_file", probe_sops_enc_file(node_yaml=node_yaml, node_name=node_name, env=env))
    result.add("env_file_priority", probe_env_file_priority(env=env))
    result.add("required_keys", probe_required_keys(node_yaml=node_yaml, env=env))
    if result.has_fatals():
        logger.error("[IMP:10][preflight][input] FATAL input-contract checks: %s", result.fatals)
    else:
        logger.info("[IMP:9][preflight][input] input-contract passed (warnings: %s)", len(result.warnings))
    return result


# endregion FUNC_run_input_preflight

# endregion RUN_ALL_CHECKS


# region CLI


# region FUNC_build_parser
## @purpose — Build CLI argument parser for preflight.py.
## @io — ⇥ None → ⎋ argparse.ArgumentParser
## @complexity — O(1)
def build_parser() -> argparse.ArgumentParser:
    """Build CLI argument parser."""
    parser = argparse.ArgumentParser(
        description="Pre-flight checks for bootstrap-node (DevPlan 047)",
    )
    parser.add_argument("--node-yaml", help="Path to node.yaml")
    parser.add_argument("--context", default="", help="Deployment context name")
    parser.add_argument("--node-name", default="", help="Node name")
    parser.add_argument(
        "--scope",
        default="full",
        choices=["full", "input"],
        help="full = SSH/disk/S3/GHCR/dockerhub/DNS пробы; input = локальный входной контракт "
        "(AGE-форма/sops/required, 0 remote, DevPlan 029 T7)",
    )
    parser.add_argument("--parse-warnings", action="store_true", help="Read JSON from stdin, output warnings to stderr")
    return parser


# endregion FUNC_build_parser


# region FUNC_main
## @purpose — CLI entry point: run pre-flight checks, output JSON, exit 0 (pass) or 1 (fatal).
## @io — ⇥ argv: list[str] | None (None = sys.argv[1:]) → ⎋ exit code (0 = pass, 1 = fatal)
## @complexity — O(N) where N = number of checks
class _CliArgs(argparse.Namespace):
    """Типизированный argparse-Namespace (W11-G3)."""

    def __init__(self) -> None:
        super().__init__()
        self.node_yaml: str | None
        self.context: str
        self.node_name: str
        self.scope: str
        self.parse_warnings: bool


def main(argv: list[str] | None = None) -> int:
    """Run pre-flight checks and output JSON to stdout. Exit 1 on FATAL."""
    parser = build_parser()
    args = parser.parse_args(argv, namespace=_CliArgs())

    # --parse-warnings mode: read JSON from stdin, print warnings, exit
    if hasattr(args, "parse_warnings") and args.parse_warnings:
        return _parse_warnings_cli()

    logging.basicConfig(
        level=logging.INFO,
        format="%(message)s",
        stream=sys.stderr,
    )

    # DevPlan 029 T7: --scope input — локальный входной контракт (0 remote), прогоняется
    # ПЕРЕД любым SSH: make validate-node-input / bootstrap.sh первый шаг. Default full —
    # существующие 6 проб (поведение неизменно).
    if args.scope == "input":
        result = run_input_preflight(node_yaml=args.node_yaml or "", node_name=args.node_name)
    else:
        result = run_preflight(
            node_yaml=args.node_yaml or "",
            context=args.context,
            node_name=args.node_name,
        )

    # Output JSON to stdout (consumed by node-lifecycle.sh)
    print(json.dumps(result.to_dict(), indent=2, ensure_ascii=False))

    if result.has_fatals():
        for fatal in result.fatals:
            check = result.checks[fatal]
            logger.error("[IMP:10][preflight] FATAL: %s — %s", fatal, check.detail)
            if check.error:
                logger.error("[IMP:10][preflight]   error: %s", check.error)
        return 1
    return 0


# endregion FUNC_main


# endregion CLI


# region FUNC_parse_warnings_cli
## @purpose — Parse JSON preflight result from stdin and output warnings to stderr.
##            Used by node-lifecycle.sh to extract warnings from preflight JSON output.
## @io — ⇥ stdin: JSON preflight result → ⎋ None (side-effect: prints warnings to stderr)
## @complexity — O(N) where N = checks
def _parse_warnings_cli() -> int:
    """Read JSON from stdin, output warnings to stderr. Exit 0."""
    import json

    try:
        result = cast(
            "dict[str, dict[str, object]]", json.load(sys.stdin)
        )  # W11-G3: json.load → Any; JSON-граница stdin
        warnings = [k for k, v in result.items() if v.get("status") == "warn"]
        if warnings:
            print(f"[IMP:7][preflight] Warnings (non-fatal): {warnings}", file=sys.stderr)
    except (json.JSONDecodeError, EOFError):
        pass
    return 0


# endregion FUNC_parse_warnings_cli


if __name__ == "__main__":
    sys.exit(main())
