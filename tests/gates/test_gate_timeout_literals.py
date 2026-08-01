#!/usr/bin/env python3
# GREP_SUMMARY: gate timeout-literals U-11 shared-timeouts constants-only docker-ssh-healthcheck domain allowlist anti-drift
# STRUCTURE: ▶ AST-скан domain-файлов → ○ subprocess.* вызов с timeout=<литерал ∈ {30,60,120,180,300,600}> → ◇ cmd docker/ssh/healthcheck? → ⟦RED: offenders⟧ | allowlist-файл → skip → ⎋ PASS
# region MODULE_CONTRACT
## @purpose  Timeout-literals gate (DevPlan 116 B5 T10, U-11): `timeout=` с int-литералом
##           ∈ {30,60,120,180,300,600} в docker/ssh/healthcheck-домене core/internal → RED.
##           Единственный источник числовых значений — core/internal/shared/timeouts.py.
## @scope    Сканирует domain-файлы (docker_orchestrator, deploy_engine, reconciler, channels,
##           context_deployer, remote_executor, core_deliverer, overlay_deliverer,
##           healthcheck_poller, docker_compose, context_promoter, vps_readiness,
##           deploy/*, bootstrap/deploy/*, converge/*). Не-доменные вызовы (git, python3/bash
##           render, validate.sh, HTTP/S3) — НЕ RED (скоуп волны = docker/ssh/healthcheck).
## @invariants
##   - RED: subprocess.* вызов с timeout=литерал ∈ set в domain-файле, где cmd — docker/ssh/
##     healthcheck (первый элемент списка ∈ {docker,ssh,scp,rsync}, или "bash"+"-c")
##   - allowlist (константа _ALLOWLIST_FILES): state_machine.py (D3, мораторий до B9),
##     HTTP/S3-домены (s3_ssl_cache, backup_config, cert_orchestrator, template_engine,
##     healthcheck_poller HTTP-часть, monitor-скрипты) — сжимается волнами
##   - Не-доменные вызовы в domain-файлах (git pull/clone, python3 config_renderer,
##     bash add-vhost.sh, validate.sh) — НЕ RED: cmd не содержит docker/ssh/healthcheck маркеров
##   - f-строки и name-ссылки (timeout=COMPOSE_UP_TIMEOUT) — НЕ литералы → PASS
## @rationale U-11: 226 литералов timeout= без констант. Единый реестр timeouts.py + гейт
##            делают значения grepable и enforce-емыми; allowlist сжимается волнами.
## @changes 2026-08-01 | DevPlan 116 B5 T10 — Created
# endregion MODULE_CONTRACT

import ast
import logging

import pytest

from tests.conftest import ldd_trajectory
from tests.helpers.gate_helpers import repo_root

logger = logging.getLogger(__name__)

ROOT = repo_root()
_CORE_INTERNAL = ROOT / "core" / "internal"

_ALLOWED_TIMEOUT_LITERALS = {30, 60, 120, 180, 300, 600}

# Domain-файлы (DevPlan 116 B5 T10). Директории: deploy/*, bootstrap/deploy/*, converge/*
_DOMAIN_FILES: set[str] = {
    "bootstrap/deploy/docker_orchestrator.py",
    "deploy/deploy_engine.py",
    "bootstrap/converge/reconciler.py",
    "deploy/channels.py",
    "bootstrap/deploy/context_deployer.py",
    "bootstrap/remote_executor.py",
    "bootstrap/core_deliverer.py",
    "bootstrap/overlay_deliverer.py",
    "deploy/healthcheck_poller.py",
    "shared/docker_compose.py",
    "deploy/context_promoter.py",
    "shared/vps_readiness.py",
}
_DOMAIN_DIR_PREFIXES = ("deploy/", "bootstrap/deploy/", "bootstrap/converge/")

# ⚠️ allowlist — сжимается волнами (DevPlan 116 B5 T10):
#   - state_machine.py — мораторий инварианта 4 программы до B9 (D3)
#   - HTTP/S3-домены — вне docker/ssh/healthcheck скоупа волны
_ALLOWLIST_FILES: set[str] = {
    "bootstrap/lifecycle/state_machine.py",  # D3 — НЕ ТРОГАТЬ до B9
    "bootstrap/s3_ssl_cache.py",  # S3-домен
    "bootstrap/cert_orchestrator.py",  # HTTP/S3-домен
    "llm/template_engine.py",  # HTTP/template-домен
}
# healthcheck_poller: HTTP-часть (urllib timeout=self.timeout — name-ref, не литерал) — natural allowlist.

# Маркеры docker/ssh/healthcheck-домена в cmd-списке
_DOCKER_SSH_MARKERS = {"docker", "ssh", "scp", "rsync"}

_SUBPROCESS_FUNCS = {"run", "check_call", "check_output", "Popen", "call"}


def _is_domain_file(rel: str) -> bool:
    """Определить, относится ли файл к docker/ssh/healthcheck-домену (по списку DevPlan T10)."""
    if rel in _DOMAIN_FILES:
        return True
    return any(rel.startswith(prefix) for prefix in _DOMAIN_DIR_PREFIXES)


def _cmd_is_domain(cmd_node: ast.AST) -> bool:
    """Проверить, что cmd — docker/ssh/healthcheck-вызов (а не git/python3/bash-render/validate)."""
    if isinstance(cmd_node, ast.List):
        str_vals = [e.value for e in cmd_node.elts if isinstance(e, ast.Constant) and isinstance(e.value, str)]
        if not str_vals:
            return False
        head = str_vals[0]
        if head in _DOCKER_SSH_MARKERS:
            return True
        # bash -c = invoke_module_interface healthcheck / ssh-прокси — healthcheck-домен
        return head == "bash" and "-c" in str_vals
    if isinstance(cmd_node, ast.Constant) and isinstance(cmd_node.value, str):
        cmd_str = cmd_node.value
        return any(marker in cmd_str for marker in ("docker ", "ssh ", "scp ", "rsync "))
    return False


def _find_offenders() -> list[tuple[str, int, int]]:
    """Найти timeout= литералы в docker/ssh/healthcheck-вызовах domain-файлов.

    ▶ ┌domain files┐ → ○ AST walk → ◇ subprocess.* + timeout=литерал ∈ set + cmd domain → ⊕ offenders → ⎋ list
    """
    offenders: list[tuple[str, int, int]] = []
    for p in sorted(_CORE_INTERNAL.rglob("*.py")):
        if "__pycache__" in p.parts:
            continue
        rel = p.relative_to(ROOT).as_posix()
        if not _is_domain_file(rel):
            continue
        if rel in _ALLOWLIST_FILES:
            continue
        try:
            tree = ast.parse(p.read_text(errors="replace"))
        except (OSError, SyntaxError):
            continue
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            fn = node.func
            if not (isinstance(fn, ast.Attribute) and isinstance(fn.value, ast.Name) and fn.value.id == "subprocess"):
                continue
            if fn.attr not in _SUBPROCESS_FUNCS:
                continue
            timeout_val: int | None = None
            for kw in node.keywords:
                if kw.arg == "timeout" and isinstance(kw.value, ast.Constant) and isinstance(kw.value.value, int):
                    timeout_val = kw.value.value
                    break
            if timeout_val is None or timeout_val not in _ALLOWED_TIMEOUT_LITERALS:
                continue
            # Определяем домен по cmd
            cmd_node: ast.AST | None = None
            if node.args:
                cmd_node = node.args[0]
            else:
                for kw in node.keywords:
                    if kw.arg in ("args", "cmd", "command"):
                        cmd_node = kw.value
                        break
            if cmd_node is None:
                continue
            if not _cmd_is_domain(cmd_node):
                continue
            offenders.append((rel, node.lineno, timeout_val))
    return offenders


@pytest.mark.gate
@ldd_trajectory
def test_no_timeout_literals_in_docker_ssh_healthcheck(caplog) -> None:
    """timeout= int literals ∈ {30,60,120,180,300,600} forbidden in docker/ssh/healthcheck domain (U-11)."""
    offenders = _find_offenders()
    if offenders:
        for rel, lineno, val in offenders:
            logger.error("[IMP:10][timeout_literals] %s:%d timeout=%d literal", rel, lineno, val)
        pytest.fail(
            f"timeout= int literals in docker/ssh/healthcheck domain ({len(offenders)}):\n"
            + "\n".join(f"  - {rel}:{lineno} timeout={val}" for rel, lineno, val in offenders)
            + "\n\nЕдиный реестр: core/internal/shared/timeouts.py (U-11). "
            "Импортируй константу вместо литерала."
        )

    logger.info("[IMP:9][timeout_literals] PASS: 0 timeout= literals in docker/ssh/healthcheck domain")
