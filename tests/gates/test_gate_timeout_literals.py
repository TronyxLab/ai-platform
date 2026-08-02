#!/usr/bin/env python3
# GREP_SUMMARY: gate timeout-literals U-11 shared-timeouts constants-only docker-ssh-healthcheck domain allowlist anti-drift
# STRUCTURE: ▶ AST-скан domain-файлов → ○ subprocess.* вызов с timeout=<литерал ∈ {10,15,30,60,120,180,300,600}> → ◇ cmd docker/ssh/healthcheck? → ⟦RED: offenders⟧ | allowlist-файл → skip → ⎋ workflow-скан (timeout=literal RED) → PASS
# region MODULE_CONTRACT
## @purpose  Timeout-literals gate (DevPlan 116 B5 T10, U-11 + DevPlan 117 D68): `timeout=` с int-литералом
##           ∈ {10,15,30,60,120,180,300,600} в docker/ssh/healthcheck-домене core/internal + core/modules
##           (явный список) → RED. Единственный источник числовых значений — core/internal/shared/timeouts.py.
##           Workflows: `timeout=\d+` в .github/workflows/*.yml → RED (все docker/ssh-вызовы CI через SoT).
## @scope    Сканирует domain-файлы (docker_orchestrator, deploy_engine, reconciler, channels,
##           context_deployer, remote_executor, core_deliverer, overlay_deliverer,
##           healthcheck_poller, docker_compose, context_promoter, vps_readiness,
##           deploy/*, bootstrap/deploy/*, converge/*, scaffold/*, reconciler_projects (119 A2),
##           bootstrap/docker_registry_auth (119 A2)) + явный список core/modules
##           (watchdog subprocess-вызовы: agent_watchdog, docker_ops, circuit_breaker (119 A2))
##           + .github/workflows/*.yml (D68, workflow-скан).
##           Не-доменные вызовы (git, python3/bash render, validate.sh, HTTP/S3) — НЕ RED.
## @invariants
##   - RED: subprocess.* вызов с timeout=литерал ∈ set в domain-файле, где cmd — docker/ssh/
##     healthcheck (первый элемент списка ∈ {docker,ssh,scp,rsync}, или "bash"+"-c")
##   - Модульные domain-файлы (watchdog: agent_watchdog, docker_ops — ВЕСЬ файл docker/ssh-домен,
##     DevPlan 118 C1): timeout=литерал на ЛЮБОМ вызове → RED (docker_ops._run_docker передаёт
##     литералы аргументом, не через subprocess.* — покрыто этим правилом)
##   - allowlist (константа _ALLOWLIST_FILES): state_machine.py (D3, мораторий до B9),
##     HTTP/S3-домены (s3_ssl_cache, backup_config, cert_orchestrator, template_engine,
##     healthcheck_poller HTTP-часть, monitor-скрипты) — сжимается волнами
##   - Не-доменные вызовы в domain-файлах (git pull/clone, python3 config_renderer,
##     bash add-vhost.sh, validate.sh) — НЕ RED: cmd не содержит docker/ssh/healthcheck маркеров
##   - f-строки и name-ссылки (timeout=COMPOSE_UP_TIMEOUT) — НЕ литералы → PASS
##   - Workflow-скан (D68): `.github/workflows/*.yml` — `timeout=\d+` в run-шаге → RED;
##     allowlist: timeout на actions/cache, docker/setup-buildx-action (не subprocess docker/ssh).
##   - Пути core/internal-файлов — ОТНОСИТЕЛЬНО core/internal/ (фикс латентного бага
##     _is_domain_file, DevPlan 118 C1: прежний ROOT-relative rel не матчил ни один файл)
## @rationale U-11: 226 литералов timeout= без констант. Единый реестр timeouts.py + гейт
##            делают значения grepable и enforce-емыми; allowlist сжимается волнами.
##            DevPlan 117 D68: набор расширен {10,15} (канон DOCKER_CMD_TIMEOUT/SUDOERS),
##            scope → core/modules (watchdog), workflow-скан закрывает слепую зону K4.
##            DevPlan 118 C1: фикс пути (rel → core/internal), docker_ops.py в module-scope;
##            C11: scaffold/ в domain-префиксах.
## @changes 2026-08-01 | DevPlan 116 B5 T10 — Created
## @changes 2026-08-01 | DevPlan 117 D68 — набор +10/15, scope core/modules, workflow-скан
## @changes 2026-08-02 | DevPlan 118 C1/C11 — фикс _is_domain_file (латентный no-op),
##                      +docker_ops.py (module-rule «любой вызов»), +scaffold/ префикс
## @changes 2026-08-02 | DevPlan 119 A2 (AUDIT-4 T1) — слепое пятно: +reconciler_projects.py,
##                      +bootstrap/docker_registry_auth.py (domain), +circuit_breaker.py (module);
##                      +3 теста (2 канон-проверки + 1 R5 negative)
# endregion MODULE_CONTRACT

import ast
import logging
import re

import pytest

from tests.conftest import ldd_trajectory
from tests.helpers.gate_helpers import repo_root

logger = logging.getLogger(__name__)

ROOT = repo_root()
_CORE_INTERNAL = ROOT / "core" / "internal"
_CORE_MODULES = ROOT / "core" / "modules"
_WORKFLOWS_DIR = ROOT / ".github" / "workflows"

_ALLOWED_TIMEOUT_LITERALS = {10, 15, 30, 60, 120, 180, 300, 600}

# Domain-файлы (DevPlan 116 B5 T10). Директории: deploy/*, bootstrap/deploy/*, converge/*, scaffold/* (C11).
# Пути ОТНОСИТЕЛЬНО core/internal/ (фикс C1 — прежний ROOT-relative не матчил ни один файл).
# 2026-08-02 (DevPlan 119 A2): +reconciler_projects.py, +bootstrap/docker_registry_auth.py —
#   слепое пятно гейта (AUDIT-4 T1), литералы уже мигрированы на shared/timeouts.
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
    "reconciler_projects.py",
    "bootstrap/docker_registry_auth.py",
}
_DOMAIN_DIR_PREFIXES = ("deploy/", "bootstrap/deploy/", "bootstrap/converge/", "scaffold/")

# Модульные domain-файлы (core/modules, DevPlan 117 D68 + 118 C1): ВЕСЬ файл docker/ssh-домен —
# timeout=литерал на ЛЮБОМ вызове → RED (docker_ops._run_docker передаёт литералы аргументом,
# не через subprocess.*). Пути ОТНОСИТЕЛЬНО core/modules/ (фикс C1 — прежний ROOT-relative
# rel не матчил ни один файл, та же латентная ошибка, что и в _is_domain_file).
_MODULE_DOMAIN_FILES: set[str] = {
    "hermes-agent/watchdog/agent_watchdog.py",
    "hermes-agent/watchdog/docker_ops.py",
    # DevPlan 119 A2: circuit_breaker.py — слепое пятно гейта (AUDIT-4 T1),
    # timeout=10 литерал мигрирован на WATCHDOG_CB_CHECK_TIMEOUT
    "hermes-agent/watchdog/circuit_breaker.py",
}

# Workflow-скан (DevPlan 117 D68): timeout=\d+ в run-шагах workflows → RED.
# Allowlist — только не-subprocess timeout (actions/cache, docker actions).
_WORKFLOW_TIMEOUT_LITERAL = re.compile(r"timeout=(\d+)")
_WORKFLOW_ALLOWLIST_LINES: tuple[str, ...] = ()

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

    ▶ ┌core/internal domain files┐ → ○ AST walk → ◇ subprocess.* + timeout=литерал ∈ set + cmd domain
      → ⊕ offenders → ⎋ list. Пути — ОТНОСИТЕЛЬНО core/internal/ (фикс C1).
    """
    offenders: list[tuple[str, int, int]] = []
    for p in sorted(_CORE_INTERNAL.rglob("*.py")):
        if "__pycache__" in p.parts:
            continue
        rel = p.relative_to(_CORE_INTERNAL).as_posix()
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


def _find_module_offenders() -> list[tuple[str, int, int]]:
    """Найти timeout= литералы на ЛЮБОМ вызове в модульных domain-файлах (watchdog, DevPlan 118 C1).

    ▶ ┌core/modules watchdog files┐ → ○ AST walk → ◇ ЛЮБОЙ Call с timeout=литерал ∈ set → ⊕ offenders → ⎋ list
    ## @purpose  docker_ops._run_docker(...) передаёт timeout литералом АРГУМЕНТОМ (не через
    ##            subprocess.*) — правило «любой вызов» покрывает этот паттерн: файлы из
    ##            _MODULE_DOMAIN_FILES целиком docker/ssh-домен (agent_watchdog, docker_ops).
    """
    offenders: list[tuple[str, int, int]] = []
    for p in sorted(_CORE_MODULES.rglob("*.py")):
        if "__pycache__" in p.parts:
            continue
        rel = p.relative_to(_CORE_MODULES).as_posix()
        if rel not in _MODULE_DOMAIN_FILES:
            continue
        try:
            tree = ast.parse(p.read_text(errors="replace"))
        except (OSError, SyntaxError):
            continue
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            for kw in node.keywords:
                if (
                    kw.arg == "timeout"
                    and isinstance(kw.value, ast.Constant)
                    and isinstance(kw.value.value, int)
                    and kw.value.value in _ALLOWED_TIMEOUT_LITERALS
                ):
                    offenders.append((rel, node.lineno, kw.value.value))  # noqa: PERF401 — вложенные циклы, extend нечитаем
    return offenders


@pytest.mark.gate
@ldd_trajectory
def test_no_timeout_literals_in_docker_ssh_healthcheck(caplog) -> None:
    """timeout= int literals ∈ {10,15,30,60,120,180,300,600} forbidden in docker/ssh/healthcheck domain (U-11, D68, C1/C11)."""
    offenders = _find_offenders() + _find_module_offenders()
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


# ── (DevPlan 118 C4) grep-скан: 0 литералов `--timeout 30` / `"--timeout", "30"` в core/ ──────
# Docker compose down --timeout — строковый литерал (не subprocess kwarg) — AST-скан не ловит.
# Отдельный grep-гейт: литерал 30 после --timeout запрещён; канон — DOCKER_STOP_TIMEOUT.

_RAW_TIMEOUT_30 = re.compile(r'--timeout[\s",]*30')


def _find_raw_timeout_30_literals() -> list[tuple[str, int, str]]:
    """Find raw `--timeout 30` / `"--timeout", "30"` literals in core/*.py.

    ▶ ┌core/*.py┐ → ○ line scan → ◇ regex --timeout…30 → ⊕ offenders → ⎋ list
    ## @purpose  C4 (DevPlan 118): AC-C4 «0 литералов --timeout 30 в core/» — docker compose
    ##            down --timeout 30 удаляется через канон DOCKER_STOP_TIMEOUT.
    """
    offenders: list[tuple[str, int, str]] = []
    for p in sorted((ROOT / "core").rglob("*.py")):
        if "__pycache__" in p.parts:
            continue
        rel = p.relative_to(ROOT).as_posix()
        try:
            lines = p.read_text(errors="replace").splitlines()
        except OSError:
            continue
        for i, line in enumerate(lines, 1):
            if _RAW_TIMEOUT_30.search(line):
                offenders.append((rel, i, line.strip()))
    return offenders


@pytest.mark.gate
@ldd_trajectory
def test_no_raw_down_timeout_30_literals(caplog) -> None:
    """0 raw `--timeout 30` literals in core/ — docker compose down timeout from DOCKER_STOP_TIMEOUT (C4)."""
    offenders = _find_raw_timeout_30_literals()
    if offenders:
        for rel, lineno, line in offenders:
            logger.error("[IMP:10][timeout_literals][C4] %s:%d %s", rel, lineno, line)
        pytest.fail(
            f"Raw `--timeout 30` literals in core/ ({len(offenders)}):\n"
            + "\n".join(f"  - {rel}:{lineno} {line}" for rel, lineno, line in offenders)
            + "\n\nКанон: DOCKER_STOP_TIMEOUT из core/internal/shared/timeouts.py (DevPlan 118 C4)."
        )

    logger.info("[IMP:9][timeout_literals][C4] PASS: 0 raw --timeout 30 literals in core/")


# ── R5 anti-survivorship (DevPlan 118 C1/C4): negative-тесты на удалённые литералы ──
# · Last fail: исходные входы, поймавшие дрейф — docker_ops._run_docker(..., timeout=30)
# ·   и deploy_engine flags = ["--timeout", "30"] (DevPlan 118 C1/C4).


@pytest.mark.gate
@ldd_trajectory
def test_r5_negative_raw_timeout_30_detected(caplog) -> None:
    """R5 negative: исходный вход C4 (["--timeout", "30"], "down --timeout 30") детектируется."""
    for original_form in ('["--timeout", "30"]', "docker compose down --timeout 30"):
        assert _RAW_TIMEOUT_30.search(original_form), f"R5 FAIL: detector missed original C4 trigger: {original_form}"
    logger.info("[IMP:9][timeout_literals][C4][R5] PASS: original --timeout 30 inputs detected")


@pytest.mark.gate
@ldd_trajectory
def test_r5_negative_module_rule_detects_run_docker_literal(caplog) -> None:
    """R5 negative: исходный вход C1 (self._run_docker([...], timeout=30)) — module-rule ловит."""
    import textwrap

    tmp = ROOT / "core" / "modules" / "hermes-agent" / "watchdog"
    probe = tmp / "_gate_probe_tmp.py"
    probe_rel = "hermes-agent/watchdog/_gate_probe_tmp.py"
    probe.write_text(
        textwrap.dedent(
            """\
            import subprocess
            class D:
                def _run_docker(self, args, timeout=600):
                    return subprocess.run(["sudo", "docker", *args], timeout=timeout)
                def cleanup(self):
                    return self._run_docker(["image", "ls"], timeout=30)
            """
        )
    )
    try:
        _MODULE_DOMAIN_FILES.add(probe_rel)
        offenders = _find_module_offenders()
        hits = [(rel, ln, val) for rel, ln, val in offenders if "_gate_probe_tmp" in rel]
        assert hits, "R5 FAIL: module-rule missed original C1 trigger (self._run_docker timeout=30)"
    finally:
        _MODULE_DOMAIN_FILES.discard(probe_rel)
        probe.unlink(missing_ok=True)
    logger.info("[IMP:9][timeout_literals][C1][R5] PASS: original docker_ops timeout=30 input detected")


# ── (D68) workflow-скан: timeout=literal в .github/workflows/*.yml → RED ──────


def _find_workflow_timeout_literals() -> list[tuple[str, int, int]]:
    """Find `timeout=<digits>` literals in CI workflow run-steps.

    ▶ ┌_WORKFLOWS_DIR┐ → ○ for each *.yml → ○ line scan → ◇ run: содержит timeout=число?
    │                   → ⊕ offenders (строка run-шага, не шага action) → ⎋ list
    """
    offenders: list[tuple[str, int, int]] = []
    for p in sorted(_WORKFLOWS_DIR.glob("*.yml")):
        rel = p.relative_to(ROOT).as_posix()
        try:
            lines = p.read_text(errors="replace").splitlines()
        except OSError:
            continue
        for i, line in enumerate(lines, 1):
            stripped = line.strip()
            if not stripped.startswith("run:"):
                continue
            for match in _WORKFLOW_TIMEOUT_LITERAL.finditer(stripped):
                if stripped in _WORKFLOW_ALLOWLIST_LINES:
                    continue
                offenders.append((rel, i, int(match.group(1))))
    return offenders


@pytest.mark.gate
@ldd_trajectory
def test_no_timeout_literals_in_ci_workflows(caplog) -> None:
    """CI workflows must not contain timeout= literals in run-steps (DevPlan 117 D68, K4)."""
    offenders = _find_workflow_timeout_literals()
    if offenders:
        for rel, lineno, val in offenders:
            logger.error("[IMP:10][timeout_literals][workflow] %s:%d timeout=%d literal", rel, lineno, val)
        pytest.fail(
            f"timeout= literals in CI workflow run-steps ({len(offenders)}):\n"
            + "\n".join(f"  - {rel}:{lineno} timeout={val}" for rel, lineno, val in offenders)
            + "\n\nВсе docker/ssh-вызовы CI — через SoT (timeouts.py / ssh_opts --shell). "
            "Raw timeout литералы в run-шагах запрещены (DevPlan 117 D68)."
        )

    logger.info("[IMP:9][timeout_literals][workflow] PASS: 0 timeout= literals in CI workflow run-steps")


# ── DevPlan 119 A2: слепое пятно гейта (AUDIT-4 T1) — 3 новых domain-файла ──
# docker_registry_auth.py / reconciler_projects.py / circuit_breaker.py были ВНЕ scope —
# их timeout= литералы (10/30/10) ускользали от гейта. Теперь: расширенный scope выше
# (_DOMAIN_FILES + _MODULE_DOMAIN_FILES) + специфичные проверки канона ниже.


@pytest.mark.gate
@ldd_trajectory
# 🧪 TRAP[TEST] · 2026-08-02 · REGRESSION · docker_registry_auth использует shared/timeouts (DevPlan 119 A2)
# · Scenario: docker_registry_auth.py импортирует таймауты из shared/timeouts; локальной константы нет
# · Last fail: DOCKER_RESTART_TIMEOUT = 60 локально (дубль SoT) + timeout=10 литерал (docker info)
# · Remove if: docker_registry_auth.py перестаёт выполнять docker-операции
def test_timeout_in_docker_registry_auth(caplog) -> None:
    """docker_registry_auth.py: таймауты импортируются из shared/timeouts (канон U-11, DevPlan 119 A2)."""
    caplog.set_level(logging.INFO)
    filepath = _CORE_INTERNAL / "bootstrap" / "docker_registry_auth.py"
    assert filepath.is_file(), f"[IMP:10][A2] docker_registry_auth.py not found: {filepath}"
    content = filepath.read_text(errors="replace")

    assert "from core.internal.shared.timeouts import" in content, (
        "[IMP:10][A2] docker_registry_auth.py does not import timeouts from shared/timeouts"
    )
    assert "DOCKER_CMD_TIMEOUT" in content, "[IMP:10][A2] DOCKER_CMD_TIMEOUT not used in docker_registry_auth.py"
    assert "DOCKER_RESTART_TIMEOUT = 60" not in content, (
        "[IMP:10][A2] local DOCKER_RESTART_TIMEOUT = 60 still in docker_registry_auth.py (SoT drift)"
    )

    logger.info("[IMP:9][timeout_literals][A2][docker_registry_auth] PASS: shared/timeouts imports present")
    logger.info("[IMP:9][timeout_literals][A2][docker_registry_auth] PASS: 0 local timeout constants")


@pytest.mark.gate
@ldd_trajectory
# 🧪 TRAP[TEST] · 2026-08-02 · REGRESSION · reconciler_projects timeout=IMAGE_CHECK_TIMEOUT (DevPlan 119 A2)
# · Scenario: docker manifest inspect вызывается с timeout=IMAGE_CHECK_TIMEOUT (60, не 30)
# · Last fail: timeout=30 (неверное значение — канон IMAGE_CHECK_TIMEOUT=60)
# · Remove if: reconciler_projects.py перестаёт проверять GHCR-образы
def test_timeout_in_reconciler_projects(caplog) -> None:
    """reconciler_projects.py: docker manifest inspect использует IMAGE_CHECK_TIMEOUT=60 (DevPlan 119 A2)."""
    caplog.set_level(logging.INFO)
    filepath = _CORE_INTERNAL / "reconciler_projects.py"
    assert filepath.is_file(), f"[IMP:10][A2] reconciler_projects.py not found: {filepath}"
    content = filepath.read_text(errors="replace")

    assert "from core.internal.shared.timeouts import IMAGE_CHECK_TIMEOUT" in content, (
        "[IMP:10][A2] reconciler_projects.py does not import IMAGE_CHECK_TIMEOUT from shared/timeouts"
    )
    assert "timeout=IMAGE_CHECK_TIMEOUT" in content, (
        "[IMP:10][A2] reconciler_projects.py does not use timeout=IMAGE_CHECK_TIMEOUT"
    )

    logger.info("[IMP:9][timeout_literals][A2][reconciler_projects] PASS: timeout=IMAGE_CHECK_TIMEOUT (60) in use")


@pytest.mark.gate
@ldd_trajectory
# 🧪 TRAP[TEST] · 2026-08-02 · NEGATIVE (R5) · литерал в новом domain-файле детектится (DevPlan 119 A2)
# · Scenario: probe-файл core/internal/ с subprocess docker + timeout=10 литерал → _find_offenders ловит
# · Last fail: N/A (новый negative-тест — исходный вход AUDIT-4 T1 = timeout=10 в docker_registry_auth:278)
# · Remove if: timeout-literals гейт отменяется
def test_timeout_literal_detected_negative(caplog) -> None:
    """R5 negative: timeout= литерал в новом domain-файле (A2 scope) детектируется.

    ## @purpose — Anti-survivorship: доказывает, что расширенный scope реально сканирует
    ##            новые domain-файлы (бывшее слепое пятно AUDIT-4 T1), а не пропускает их.
    ## @io — ⎋ None (assert: probe-литерал обнаружен)
    ## @complexity — O(F) — один временный файл
    """
    caplog.set_level(logging.INFO)
    import textwrap

    probe = _CORE_INTERNAL / "_gate_probe_timeout_a2.py"
    probe_rel = "_gate_probe_timeout_a2.py"
    probe.write_text(
        textwrap.dedent(
            """\
            import subprocess
            def check_image():
                return subprocess.run(
                    ["docker", "manifest", "inspect", "ghcr.io/x/y:latest"],
                    capture_output=True,
                    text=True,
                    timeout=10,
                )
            """
        )
    )
    try:
        _DOMAIN_FILES.add(probe_rel)
        offenders = _find_offenders()
        hits = [(rel, ln, val) for rel, ln, val in offenders if "_gate_probe_timeout_a2" in rel]
        assert hits, "R5 FAIL: timeout=10 literal in new domain file (A2 scope) was NOT detected"
        logger.info("[IMP:9][timeout_literals][A2][R5] PASS: probe %s:%d timeout=%d detected", *hits[0])
    finally:
        _DOMAIN_FILES.discard(probe_rel)
        probe.unlink(missing_ok=True)
