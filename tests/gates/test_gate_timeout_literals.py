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
##           План 170 W1-A1: +AMBER-домены (_AMBER_DOMAIN_FILES) — lifecycle/healthcheck/
##           monitoring/dev-инструменты (research-D §D1): НИКАКИХ timeout=<int> литералов на
##           ЛЮБОМ вызове (не только docker/ssh cmd) — зачищенные файлы целиком под SoT-канон.
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
## @changes 2026-08-03 | DevPlan 123 T7 — P-11 аудит apt-домена: +test_apt_timeouts_use_canon
##                      (APT_TIMEOUT в shared/timeouts; system.py/tor_setup.py/install_acme.py)
##                      + R5 negative (apt-get subprocess.run без timeout детектится)
## @changes 2026-08-14 | план 170 W1-A1 — +AMBER-домен-скоуп (25 файлов lifecycle/healthcheck/
##                      monitoring/dev-инструменты, research-D §D1): test_no_timeout_literals_in_amber_domains
##                      (любой int, не только канонический набор) + R5 negative (timeout=123 probe);
##                      test_apt_timeouts_use_canon +python_deps.py (φ1); test_openssl_timeout_uses_canon
##                      +crypto.py (фикс рассинхрона B5)
# endregion MODULE_CONTRACT

import ast
import logging
import re

import pytest

from tests.conftest import ldd_trajectory
from tests.helpers.gate_helpers import repo_root

# ⚠️ xdist-race (DevPlan 119 H): R5-negative-тесты пишут probe-файлы (_gate_probe_opt_path.py/
# _gate_probe_opt_path_b3.py/_gate_probe_timeout_a2.py) ВО tmp_path (DevPlan 119 H — Zero Hardcode
# Rule) и сканируют их через параметр root=tmp_path; позитивные тесты сканируют рабочее дерево.
# Сканеры также исключают файлы с префиксом _gate_probe_ (тестовые артефакты, НЕ продукт).
# Отвергнуто: xdist_group("serial") — требует --dist loadgroup, при -n auto (load) игнорируется.
# 2026-08-04 (DevPlan 129 W2): probe-файлы уже в tmp_path
# (перенесены DevPlan 119 H, см. _find_offenders/_find_opt_path_literals с root=tmp_path).
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
    "deploy/channels/scp.py",  # W4-B1 (план 170): channels.py → пакет channels/ (scp.py — ssh/rsync-домен)
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
# timeout=литерал на ЛЮБОМ вызове → RED. Пути ОТНОСИТЕЛЬНО core/modules/ (фикс C1 — прежний
# ROOT-relative rel не матчил ни один файл, та же латентная ошибка, что и в _is_domain_file).
# 2026-08-03 (RC 121, долг 119 C2): watchdog-файлы (agent_watchdog/docker_ops/circuit_breaker)
# УДАЛЕНЫ вместе с подсистемой — 3 записи убраны.
_MODULE_DOMAIN_FILES: set[str] = (
    set()
)  # watchdog удалён (RC 121); будущие модульные docker/ssh-файлы регистрируются здесь

# ── AMBER-домены (план 170 W1-A1, research-D §D1) ──────────────────────────
# Зачищенные домены вне docker/ssh/healthcheck-скоупа: lifecycle/healthcheck/monitoring/
# dev-инструменты. Правило: НИКАКИХ timeout=<int-литерал> на ЛЮБОМ вызове (не только
# docker/ssh) — все значения обязаны быть константами SoT (timeouts.py) или модульными
# константами с TRAP-комментарием. В отличие от _ALLOWED_TIMEOUT_LITERALS, ловим ЛЮБОЙ
# int (включая 5/123/1800) — зачистка требует полного отсутствия литералов.
# Пути ОТНОСИТЕЛЬНО core/internal/ (конвенция C1).
_AMBER_DOMAIN_FILES: set[str] = {
    # lifecycle-фазы и helpers
    "bootstrap/lifecycle/phases/system.py",
    "bootstrap/lifecycle/phases/docker.py",
    "bootstrap/lifecycle/phases/certs.py",
    "bootstrap/lifecycle/cli.py",
    "bootstrap/lifecycle/helpers/system.py",
    "bootstrap/lifecycle/helpers/users.py",
    "bootstrap/lifecycle/secrets_manager.py",
    # bootstrap-утилиты
    "bootstrap/reboot_policy.py",
    "bootstrap/cert_expiry_check.py",
    "bootstrap/cron_installer.py",
    "bootstrap/issue_cert.py",
    # 170 W6-D1: security_posture.py (монолит 1131) → пакет security/ — таймауты из SoT timeouts.py
    "bootstrap/security/apt_security.py",
    "bootstrap/security/deploy_channel_posture.py",
    "bootstrap/security/docker_posture.py",
    "bootstrap/security/fs_perms.py",
    "bootstrap/security/run.py",
    "bootstrap/security/sshd_policy.py",
    # секреты
    "secrets/decrypt_secrets.py",
    # healthcheck-метрики
    "healthcheck/metrics/project_collector.py",
    "healthcheck/metrics/docker_collector.py",
    # monitoring
    "monitoring/service_reload.py",
    "monitoring/langfuse_projects.py",
    # dev-инструменты
    "agent_check/__init__.py",  # 170 W10-C: agent_check.py → пакет (коллизия файл+пакет снята)
    "static/__main__.py",
    "lint/grepsummary_validator.py",
    "scripts/generate_entrypoint_manifest.py",
    # deploy-префикс-файлы уже в _DOMAIN_FILES (cmd-скоуп) — добавляются сюда для
    # ЛЮБОГО-Call правила (git/python3/bash не docker/ssh — cmd-скан их пропускал)
    "deploy/orchestrator.py",
    "deploy/preflight.py",
    "bootstrap/deploy/context_overlay.py",
    "bootstrap/deploy/llm_provision.py",
}


def _find_amber_offenders(root: "object | None" = None) -> list[tuple[str, int, int]]:
    """Найти timeout=<int-литерал> на ЛЮБОМ вызове в AMBER-доменах (план 170 W1-A1).

    ▶ ┌core/internal AMBER files┐ → ○ AST walk → ◇ ЛЮБОЙ Call с timeout=<int Constant> → ⊕ offenders → ⎋ list
    ## @purpose  AMBER-домены (lifecycle/healthcheck/monitoring/dev-инструменты) зачищены от
    ##            литералов: НИКАКИХ timeout=<int> на любом вызове (runner.run/subprocess.run/
    ##            urllib.urlopen/run_subprocess). В отличие от docker/ssh-скоупа (где важен cmd),
    ##            здесь файл целиком подлежит SoT-канону — литералы вне _ALLOWED_TIMEOUT_LITERALS
    ##            (5/123/1800) тоже RED (зачистка, не только канонический набор).
    ## Параметр root (DevPlan 119 H): R5-тесты сканируют probe во tmp_path — Zero Hardcode Rule.
    """
    base = _CORE_INTERNAL if root is None else root
    offenders: list[tuple[str, int, int]] = []
    for p in sorted(base.rglob("*.py")):
        if "__pycache__" in p.parts:
            continue
        rel = p.relative_to(base).as_posix()
        if rel not in _AMBER_DOMAIN_FILES:
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
                    and not isinstance(kw.value.value, bool)
                ):
                    offenders.append((rel, node.lineno, kw.value.value))  # ruff: ignore[PERF401] — вложенные циклы, extend нечитаем
    return offenders


@pytest.mark.gate
@ldd_trajectory
def test_no_timeout_literals_in_amber_domains(caplog) -> None:
    """AMBER-домены (lifecycle/healthcheck/monitoring/dev): 0 timeout=<int> литералов (план 170 W1-A1)."""
    offenders = _find_amber_offenders()
    if offenders:
        for rel, lineno, val in offenders:
            logger.error("[IMP:10][timeout_literals][amber] %s:%d timeout=%d literal", rel, lineno, val)
        pytest.fail(
            f"timeout= int literals in AMBER domains ({len(offenders)}):\n"
            + "\n".join(f"  - {rel}:{lineno} timeout={val}" for rel, lineno, val in offenders)
            + "\n\nВсе значения — константы SoT (core/internal/shared/timeouts.py) или модульные "
            "константы с TRAP-комментарием (план 170 W1-A1, research-D §D1)."
        )
    logger.info("[IMP:9][timeout_literals][amber] PASS: 0 timeout= int literals in AMBER domains")


@pytest.mark.gate
@ldd_trajectory
# 🧪 TRAP[TEST] · 2026-08-14 · NEGATIVE (R5) · timeout=123 в AMBER-домене детектится (план 170 W1-A1)
# · Scenario: probe-файл (tmp_path) с subprocess.run(..., timeout=123) в AMBER-домене →
# ·   _find_amber_offenders ловит (литерал вне канонического набора тоже RED — зачистка)
# ·   (DevPlan 119 H: probe в tmp_path — Zero Hardcode Rule, устранение xdist-race)
# · Last fail: lifecycle/helpers/system.py:86-198 — timeout=30/10/120 литералы (исходный вход W1-A1)
# · Remove if: AMBER-домен-скоуп гейта отменяется
def test_amber_timeout_literal_detected_negative(caplog, tmp_path) -> None:
    """R5 negative: timeout=123 в AMBER-домене (исходный вход W1-A1) детектируется."""
    caplog.set_level(logging.INFO)
    import textwrap

    probe = tmp_path / "_gate_probe_amber_timeout.py"
    probe_rel = "_gate_probe_amber_timeout.py"
    probe.write_text(
        textwrap.dedent(
            """\
            import subprocess

            def probe_cmd():
                return subprocess.run(
                    ["python3", "check.py"],
                    capture_output=True,
                    text=True,
                    timeout=123,
                )
            """
        )
    )
    try:
        _AMBER_DOMAIN_FILES.add(probe_rel)
        offenders = _find_amber_offenders(root=tmp_path)
        hits = [(rel, ln, val) for rel, ln, val in offenders if "_gate_probe_amber_timeout" in rel]
        assert hits, "R5 FAIL: timeout=123 literal in AMBER domain was NOT detected"
        logger.info("[IMP:9][timeout_literals][amber][R5] PASS: probe %s:%d timeout=%d detected", *hits[0])
    finally:
        _AMBER_DOMAIN_FILES.discard(probe_rel)
        probe.unlink(missing_ok=True)


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


def _find_offenders(root: "object | None" = None) -> list[tuple[str, int, int]]:
    """Найти timeout= литералы в docker/ssh/healthcheck-вызовах domain-файлов.

    ▶ ┌core/internal domain files┐ → ○ AST walk → ◇ subprocess.* + timeout=литерал ∈ set + cmd domain
      → ⊕ offenders → ⎋ list. Пути — ОТНОСИТЕЛЬНО корня (по умолчанию core/internal/, фикс C1).
    Параметр root (DevPlan 119 H): R5-тесты сканируют probe во tmp_path — Zero Hardcode Rule,
    устраняет xdist-race (probe-файлы больше не пишутся в рабочее дерево).
    """
    base = _CORE_INTERNAL if root is None else root
    offenders: list[tuple[str, int, int]] = []
    for p in sorted(base.rglob("*.py")):
        if "__pycache__" in p.parts:
            continue
        rel = p.relative_to(base).as_posix()
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
                    if kw.arg in ("args", "cmd", "command"):  # ruff: ignore[PLR6201] — tuple: set-literal = verb-register RED
                        cmd_node = kw.value
                        break
            if cmd_node is None:
                continue
            if not _cmd_is_domain(cmd_node):
                continue
            offenders.append((rel, node.lineno, timeout_val))
    return offenders


def _find_module_offenders(root: "object | None" = None) -> list[tuple[str, int, int]]:
    """Найти timeout= литералы на ЛЮБОМ вызове в модульных domain-файлах (watchdog, DevPlan 118 C1).

    ▶ ┌core/modules watchdog files┐ → ○ AST walk → ◇ ЛЮБОЙ Call с timeout=литерал ∈ set → ⊕ offenders → ⎋ list
    ## @purpose  docker_ops._run_docker(...) передаёт timeout литералом АРГУМЕНТОМ (не через
    ##            subprocess.*) — правило «любой вызов» покрывает этот паттерн: файлы из
    ##            _MODULE_DOMAIN_FILES целиком docker/ssh-домен (agent_watchdog, docker_ops).
    ## Параметр root (DevPlan 119 H): R5-тесты сканируют probe во tmp_path — Zero Hardcode Rule,
    ## устраняет xdist-race.
    """
    base = _CORE_MODULES if root is None else root
    offenders: list[tuple[str, int, int]] = []
    for p in sorted(base.rglob("*.py")):
        if "__pycache__" in p.parts:
            continue
        rel = p.relative_to(base).as_posix()
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
                    offenders.append((rel, node.lineno, kw.value.value))  # ruff: ignore[PERF401] — вложенные циклы, extend нечитаем
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
def test_r5_negative_module_rule_detects_run_docker_literal(caplog, tmp_path) -> None:
    """R5 negative: исходный вход C1 (self._run_docker([...], timeout=30)) — module-rule ловит."""
    import textwrap

    # DevPlan 119 H: probe во tmp_path (Zero Hardcode Rule) — рабочее дерево не загрязняется,
    # xdist-race с позитивным сканером _find_module_offenders устранён.
    # probe_rel добавляется в _MODULE_DOMAIN_FILES с watchdog-префиксом — сканер (root=tmp_path)
    # вычисляет rel = basename; добавляем БЕЗ префикса, чтобы релятивный путь совпал.
    probe = tmp_path / "_gate_probe_tmp.py"
    probe_rel = "_gate_probe_tmp.py"  # rel от tmp_path-корня (сканер root=tmp_path)
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
        offenders = _find_module_offenders(root=tmp_path)
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
# · Scenario: probe-файл tmp_path/ с subprocess docker + timeout=10 литерал → _find_offenders ловит
# ·   (DevPlan 119 H: probe перенесён из рабочего дерева core/internal/ в tmp_path — Zero Hardcode
# ·   Rule + устранение xdist-race)
# · Last fail: N/A (новый negative-тест — исходный вход AUDIT-4 T1 = timeout=10 в docker_registry_auth:278)
# · Remove if: timeout-literals гейт отменяется
def test_timeout_literal_detected_negative(caplog, tmp_path) -> None:
    """R5 negative: timeout= литерал в новом domain-файле (A2 scope) детектируется.

    ## @purpose — Anti-survivorship: доказывает, что расширенный scope реально сканирует
    ##            новые domain-файлы (бывшее слепое пятно AUDIT-4 T1), а не пропускает их.
    ## @io — ⎋ None (assert: probe-литерал обнаружен)
    ## @complexity — O(F) — один временный файл
    """
    caplog.set_level(logging.INFO)
    import textwrap

    # DevPlan 119 H: probe во tmp_path (Zero Hardcode Rule) — рабочее дерево не загрязняется,
    # xdist-race между R5-создателем и позитивным сканером устранён.
    probe = tmp_path / "_gate_probe_timeout_a2.py"
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
        offenders = _find_offenders(root=tmp_path)
        hits = [(rel, ln, val) for rel, ln, val in offenders if "_gate_probe_timeout_a2" in rel]
        assert hits, "R5 FAIL: timeout=10 literal in new domain file (A2 scope) was NOT detected"
        logger.info("[IMP:9][timeout_literals][A2][R5] PASS: probe %s:%d timeout=%d detected", *hits[0])
    finally:
        _DOMAIN_FILES.discard(probe_rel)
        probe.unlink(missing_ok=True)


# ── DevPlan 119 B2/B3: 0 литералов /opt/{projects,platform,node-configs} вне deploy_paths ──
# AC-B2.1 / AC-B3.1: grep '"/opt/projects"|"/opt/platform"|"/opt/node-configs"' core/internal/
# (кроме deploy_paths.py — SoT) → 0. Сканер + R5 negative-тесты.

_OPT_PATH_LITERAL = re.compile(r'["\']/opt/(projects|platform|node-configs)["\']')
_OPT_PATH_CANON = "shared/deploy_paths.py"


def _find_opt_path_literals(root: "object | None" = None) -> list[tuple[str, int, str]]:
    """Найти литералы /opt/{projects,platform,node-configs} в core/internal (кроме deploy_paths.py).

    ▶ ┌core/internal/**/*.py┐ → ○ line scan → ◇ regex ["']/opt/(projects|platform|node-configs)["'] → ⊕ offenders → ⎋ list
    ## @purpose  AC-B2.1/AC-B3.1 (DevPlan 119): 0 дублирующих литералов путей вне SoT deploy_paths.
    ##            Комментарии с литералом тоже RED (дрейф-источник — переписывать, не цитировать).
    ## Параметр root (DevPlan 119 H): R5-тесты сканируют probe во tmp_path — Zero Hardcode Rule,
    ## устраняет xdist-race (probe-файлы больше не пишутся в рабочее дерево).
    """
    base = _CORE_INTERNAL if root is None else root
    offenders: list[tuple[str, int, str]] = []
    for p in sorted(base.rglob("*.py")):
        if "__pycache__" in p.parts:
            continue
        rel = p.relative_to(base).as_posix()
        if rel == _OPT_PATH_CANON:
            continue
        try:
            lines = p.read_text(errors="replace").splitlines()
        except OSError:
            continue
        for i, line in enumerate(lines, 1):
            if _OPT_PATH_LITERAL.search(line):
                offenders.append((rel, i, line.strip()))
    return offenders


@pytest.mark.gate
@ldd_trajectory
def test_no_opt_path_literals_in_core_internal(caplog) -> None:
    """0 литералов /opt/{projects,platform,node-configs} вне shared/deploy_paths (AC-B2.1/B3.1)."""
    offenders = _find_opt_path_literals()
    if offenders:
        for rel, lineno, line in offenders:
            logger.error("[IMP:10][opt_path_literals] %s:%d %s", rel, lineno, line)
        pytest.fail(
            f"Литералы /opt/* вне SoT ({len(offenders)}):\n"
            + "\n".join(f"  - {rel}:{lineno} {line}" for rel, lineno, line in offenders)
            + "\n\nКанон: core/internal/shared/deploy_paths.py — projects_base()/platform_remote_base()/"
            "node_configs_remote()/DEFAULT_PROJECTS_BASE (DevPlan 119 B2/B3)."
        )
    logger.info("[IMP:9][opt_path_literals] PASS: 0 /opt/* литералов вне shared/deploy_paths")


@pytest.mark.gate
@ldd_trajectory
# 🧪 TRAP[TEST] · 2026-08-02 · NEGATIVE (R5) · литерал /opt/projects в новом файле детектится (B2)
# · Scenario: probe-файл (tmp_path) с os.environ.get("PROJECTS_BASE", "/opt/projects") → сканер ловит
# ·   (DevPlan 119 H: probe в tmp_path — Zero Hardcode Rule, устранение xdist-race)
# · Last fail: deploy_engine.py:234 projects_base="/opt/projects" (исходный вход AUDIT-4 T2)
# · Remove if: opt-path гейт отменяется
def test_opt_projects_literal_detected_negative(caplog, tmp_path) -> None:
    """R5 negative: /opt/projects литерал (исходный вход B2) детектируется."""
    caplog.set_level(logging.INFO)
    import textwrap

    probe = tmp_path / "_gate_probe_opt_path.py"
    probe.write_text(
        textwrap.dedent(
            """\
            import os
            DEFAULT_PROJECTS_BASE = "/opt/projects"
            root = os.environ.get("PROJECTS_BASE", "/opt/projects")
            """
        )
    )
    try:
        hits = [
            (rel, ln, line) for rel, ln, line in _find_opt_path_literals(root=tmp_path) if "_gate_probe_opt_path" in rel
        ]
        assert hits, "R5 FAIL: /opt/projects literal (исходный вход B2) не обнаружен"
        logger.info("[IMP:9][opt_path_literals][B2][R5] PASS: probe %s:%d %s detected", *hits[0])
    finally:
        probe.unlink(missing_ok=True)


@pytest.mark.gate
@ldd_trajectory
# 🧪 TRAP[TEST] · 2026-08-02 · NEGATIVE (R5) · литералы /opt/platform + /opt/node-configs детектятся (B3)
# · Scenario: probe-файл (tmp_path) с os.environ.get("PLATFORM_ROOT", "/opt/platform") и
# ·   "/opt/node-configs" → ловится (DevPlan 119 H: probe в tmp_path — Zero Hardcode Rule,
# ·   устранение xdist-race)
# · Last fail: orchestrator.py:890 platform_root="/opt/platform", secrets.py:91 "/opt/node-configs" (AUDIT-4 T3)
# · Remove if: opt-path гейт отменяется
def test_opt_platform_nodeconfigs_literal_detected_negative(caplog, tmp_path) -> None:
    """R5 negative: /opt/platform + /opt/node-configs литералы (исходный вход B3) детектируются."""
    caplog.set_level(logging.INFO)
    import textwrap

    probe = tmp_path / "_gate_probe_opt_path_b3.py"
    probe.write_text(
        textwrap.dedent(
            """\
            import os
            platform_root = os.environ.get("PLATFORM_ROOT", "/opt/platform")
            node_configs = os.environ.get("NODE_CONFIGS_DIR", "/opt/node-configs")
            """
        )
    )
    try:
        hits = [
            (rel, ln, line)
            for rel, ln, line in _find_opt_path_literals(root=tmp_path)
            if "_gate_probe_opt_path_b3" in rel
        ]
        assert hits, "R5 FAIL: /opt/platform + /opt/node-configs literals (исходный вход B3) не обнаружены"
        logger.info("[IMP:9][opt_path_literals][B3][R5] PASS: probe %s:%d %s detected", *hits[0])
    finally:
        probe.unlink(missing_ok=True)


# ── DevPlan 119 B5: openssl timeout → DEFAULT_OPENSSL_TIMEOUT (канон ssl_certs) ──


@pytest.mark.gate
@ldd_trajectory
# 🧪 TRAP[TEST] · 2026-08-02 · REGRESSION · openssl-таймаут = канон DEFAULT_OPENSSL_TIMEOUT (B5)
# · Scenario: cert_orchestrator + nginx_harness импортируют DEFAULT_OPENSSL_TIMEOUT; timeout=30 литералов нет
# · Last fail: cert_orchestrator.py:452,474 + nginx_harness.py:159 — timeout=30 (дубль SoT, AUDIT-4 T4)
# · Remove if: openssl subprocess удаляется из этих модулей
def test_openssl_timeout_uses_canon(caplog) -> None:
    """openssl subprocess использует DEFAULT_OPENSSL_TIMEOUT (не литерал 30) — B5."""
    caplog.set_level(logging.INFO)
    # W1-A1 (план 170): +crypto.py (shared/htpasswd) — фикс рассинхрона B5 (_OPENSSL_TIMEOUT=15 →
    # DEFAULT_OPENSSL_TIMEOUT=10, TRAP[BUG] 2026-08-14); crypto.py был вне B5-скоупа гейта
    for rel in ("bootstrap/cert_orchestrator.py", "scaffold/nginx_harness.py", "shared/crypto.py"):
        filepath = _CORE_INTERNAL / rel
        content = filepath.read_text(errors="replace")
        assert "DEFAULT_OPENSSL_TIMEOUT" in content, f"[IMP:10][B5] {rel} не использует DEFAULT_OPENSSL_TIMEOUT"
        # Литерал timeout=30 для openssl — только через канон
        assert "timeout=30" not in content, f"[IMP:10][B5] {rel} содержит литерал timeout=30 (дубль SoT)"
    logger.info(
        "[IMP:9][timeout_literals][B5] PASS: cert_orchestrator + nginx_harness + crypto используют DEFAULT_OPENSSL_TIMEOUT"
    )


# ── DevPlan 119 B7: converge/infra таймауты → shared/timeouts ──


@pytest.mark.gate
@ldd_trajectory
# 🧪 TRAP[TEST] · 2026-08-02 · REGRESSION · converge/infra таймауты из shared/timeouts (B7)
# · Scenario: converge/infra.py импортирует CONVERGE_DOCKER_TIMEOUT/FILE_OP_TIMEOUT; локальных нет
# · Last fail: DOCKER_TIMEOUT=30, FILE_OP_TIMEOUT=15 локально в converge/infra.py (AUDIT-4 T5)
# · Remove if: converge/infra перестаёт выполнять docker/file-операции
def test_converge_infra_timeouts_from_shared(caplog) -> None:
    """converge/infra.py: таймауты импортируются из shared/timeouts (B7, AC-B7.1/B7.2)."""
    caplog.set_level(logging.INFO)
    filepath = _CORE_INTERNAL / "bootstrap" / "converge" / "infra.py"
    content = filepath.read_text(errors="replace")
    assert "from core.internal.shared.timeouts import" in content, (
        "[IMP:10][B7] converge/infra.py не импортирует таймауты из shared/timeouts"
    )
    assert "CONVERGE_DOCKER_TIMEOUT" in content, "[IMP:10][B7] CONVERGE_DOCKER_TIMEOUT не используется"
    assert "FILE_OP_TIMEOUT" in content, "[IMP:10][B7] FILE_OP_TIMEOUT не используется"
    assert "DOCKER_TIMEOUT = 30" not in content, "[IMP:10][B7] локальный DOCKER_TIMEOUT = 30 остался (SoT drift)"
    assert "FILE_OP_TIMEOUT = 15" not in content, "[IMP:10][B7] локальный FILE_OP_TIMEOUT = 15 остался (SoT drift)"
    logger.info("[IMP:9][timeout_literals][B7] PASS: converge/infra таймауты из shared/timeouts")


# ── DevPlan 119 B8: vps_readiness SSH_TIMEOUT → SSH_CONNECT_TIMEOUT ──


@pytest.mark.gate
@ldd_trajectory
# 🧪 TRAP[TEST] · 2026-08-02 · REGRESSION · vps_readiness SSH-таймаут = SSH_CONNECT_TIMEOUT (B8)
# · Scenario: vps_readiness.py использует SSH_CONNECT_TIMEOUT; локального SSH_TIMEOUT нет
# · Last fail: SSH_TIMEOUT = 30 локально (дубль SoT, AUDIT-4 T6)
# · Remove if: vps_readiness перестаёт выполнять SSH-проверки
def test_vps_readiness_ssh_connect_timeout(caplog) -> None:
    """vps_readiness.py: SSH-таймаут = SSH_CONNECT_TIMEOUT (B8, AC-B8.1)."""
    caplog.set_level(logging.INFO)
    filepath = _CORE_INTERNAL / "shared" / "vps_readiness.py"
    content = filepath.read_text(errors="replace")
    assert "SSH_CONNECT_TIMEOUT" in content, "[IMP:10][B8] vps_readiness не использует SSH_CONNECT_TIMEOUT"
    assert "SSH_TIMEOUT" not in content, "[IMP:10][B8] локальный SSH_TIMEOUT остался (SoT drift)"
    logger.info("[IMP:9][timeout_literals][B8] PASS: vps_readiness использует SSH_CONNECT_TIMEOUT")


# ── DevPlan 123 T7: apt-get в bootstrap-цепи → APT_TIMEOUT (канон shared/timeouts) ──
# P-11 аудит apt-домена: system.py (install_apt_packages, 120) + tor_setup.py
# (apt_update/apt_install, БЕЗ timeout — hang-риск) + install_acme.py (W3.5-1: timeout=APT_TIMEOUT).


def _apt_subprocess_without_timeout(source: str) -> list[int]:
    """AST-скан: subprocess.run вызовы с apt-get в cmd БЕЗ timeout kwarg → номера строк.

    ▶ ┌source┐ → ○ AST walk → ◇ subprocess.run + "apt-get" в cmd + нет timeout kwarg
      → ⊕ lineno list → ⎋ list[int] (пусто = все apt-вызовы имеют таймаут)
    ## @purpose  Проверка T7: каждый subprocess.run apt-get обязан иметь timeout=
    ##            (канон APT_TIMEOUT). Формат-независима (многострочные вызовы тоже ловятся).
    """
    tree = ast.parse(source)
    bad: list[int] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        fn = node.func
        if not (isinstance(fn, ast.Attribute) and isinstance(fn.value, ast.Name) and fn.value.id == "subprocess"):
            continue
        if fn.attr != "run":
            continue
        cmd_node = node.args[0] if node.args else None
        if not isinstance(cmd_node, ast.List):
            continue
        strs = [e.value for e in cmd_node.elts if isinstance(e, ast.Constant) and isinstance(e.value, str)]
        if "apt-get" not in strs:
            continue
        has_timeout = any(kw.arg == "timeout" for kw in node.keywords)
        if not has_timeout:
            bad.append(node.lineno)
    return bad


@pytest.mark.gate
@ldd_trajectory
# 🧪 TRAP[TEST] · 2026-08-03 · REGRESSION · apt-get домен использует APT_TIMEOUT канон (DevPlan 123 T7)
# · Scenario: system.py (install_apt_packages) и tor_setup.py (apt_update/apt_install) используют
# ·   timeout=APT_TIMEOUT из shared/timeouts; install_acme.py — timeout=APT_TIMEOUT (W3.5-1, 164)
# · Last fail: system.py:73-74 timeout=120 , tor_setup.py:70/87 БЕЗ timeout (hang-риск),
# ·   install-acme.sh:58 без timeout (→ install_acme.py: timeout=APT_TIMEOUT)
# · Remove if: apt-get вызовы удаляются из bootstrap-цепи
def test_apt_timeouts_use_canon(caplog) -> None:
    """apt-get в bootstrap-цепи: канон APT_TIMEOUT из shared/timeouts (DevPlan 123 T7)."""
    caplog.set_level(logging.INFO)

    # (а) канон определён в shared/timeouts.py
    timeouts_path = _CORE_INTERNAL / "shared" / "timeouts.py"
    assert timeouts_path.is_file(), f"[IMP:10][T7] timeouts.py not found: {timeouts_path}"
    timeouts_content = timeouts_path.read_text(errors="replace")
    assert "APT_TIMEOUT = 300" in timeouts_content, "[IMP:10][T7] APT_TIMEOUT = 300 отсутствует в shared/timeouts"
    # (б) lifecycle/helpers/system.py — install_apt_packages: канон вместо 120.
    # DevPlan 162 W7-4: apt-get update/install переведены на _run_with_retry (retry-wrapper,
    # 3 попытки + backoff) — каждая retry-строка с apt-get обязана передавать timeout=APT_TIMEOUT
    # (wrapper пробрасывает timeout в run_subprocess; канон APT_TIMEOUT сохраняется).
    system_path = _CORE_INTERNAL / "bootstrap" / "lifecycle" / "helpers" / "system.py"
    assert system_path.is_file(), f"[IMP:10][T7] system.py not found: {system_path}"
    system_content = system_path.read_text(errors="replace")
    assert "timeouts import APT_TIMEOUT" in system_content or "APT_TIMEOUT," in system_content, (
        "[IMP:10][T7] system.py не импортирует APT_TIMEOUT из shared/timeouts"
    )
    # Только строки реальных вызовов (комментарии с упоминанием apt-get не считаются)
    system_apt_lines = [ln for ln in system_content.splitlines() if "apt-get" in ln and "_run_with_retry(" in ln]
    assert system_apt_lines, "[IMP:10][T7] в system.py не найдены _run_with_retry apt-get вызовы"
    system_violations = [
        ln.strip() for ln in system_apt_lines if "timeout=APT_TIMEOUT" not in ln or "timeout=120" in ln
    ]
    assert not system_violations, f"[IMP:10][T7] apt-get строки system.py вне канона: {system_violations}"

    # (в) tor_setup.py — apt_update/apt_install: timeout=APT_TIMEOUT на каждом apt-get
    tor_path = _CORE_INTERNAL / "bootstrap" / "tor_setup.py"
    assert tor_path.is_file(), f"[IMP:10][T7] tor_setup.py not found: {tor_path}"
    tor_content = tor_path.read_text(errors="replace")
    assert "from core.internal.shared.timeouts import APT_TIMEOUT" in tor_content, (
        "[IMP:10][T7] tor_setup.py не импортирует APT_TIMEOUT из shared/timeouts"
    )
    assert tor_content.count("timeout=APT_TIMEOUT") >= 2, (
        "[IMP:10][T7] timeout=APT_TIMEOUT должен быть и в apt_update, и в apt_install"
    )
    assert "timeout=120" not in tor_content, "[IMP:10][T7] tor_setup.py содержит timeout=120"
    assert _apt_subprocess_without_timeout(tor_content) == [], (
        "[IMP:10][T7] tor_setup.py содержит subprocess.run apt-get без timeout"
    )

    # (г) install_acme.py — apt-get install git под timeout=APT_TIMEOUT (W3.5-1: канон APT_TIMEOUT,
    # прежний GNU 'timeout 300 apt-get' в install-acme.sh заменён run_subprocess timeout=APT_TIMEOUT)
    acme_path = ROOT / "core" / "internal" / "bootstrap" / "install_acme.py"
    assert acme_path.is_file(), f"[IMP:10][T7] install_acme.py not found: {acme_path}"
    acme_content = acme_path.read_text(errors="replace")
    assert "from core.internal.shared.timeouts import APT_TIMEOUT" in acme_content, (
        "[IMP:10][T7] install_acme.py не импортирует APT_TIMEOUT из shared/timeouts"
    )
    assert "timeout=APT_TIMEOUT" in acme_content, (
        "[IMP:10][T7] install_acme.py не использует timeout=APT_TIMEOUT для apt-get"
    )
    assert "timeout 300 apt-get" not in acme_content, (
        "[IMP:10][T7] install_acme.py содержит GNU timeout wrapper (должен быть timeout=APT_TIMEOUT)"
    )
    assert "apt-get" in acme_content, "[IMP:10][T7] в install_acme.py не найден apt-get вызов"

    # (д) python_deps.py (φ1, план 170 W1-A1) — apt-get в bootstrap-цепи: канон APT_TIMEOUT.
    # W1-A1 Prevention: python_deps добавлялся в цепь после T7 и обходил канон (timeout=600) —
    # гейт не покрывал φ1 (TRAP[BUG] 2026-08-14 в python_deps.py). Теперь покрыт.
    deps_path = _CORE_INTERNAL / "bootstrap" / "python_deps.py"
    assert deps_path.is_file(), f"[IMP:10][T7] python_deps.py not found: {deps_path}"
    deps_content = deps_path.read_text(errors="replace")
    assert "from core.internal.shared.timeouts import APT_TIMEOUT" in deps_content, (
        "[IMP:10][T7] python_deps.py не импортирует APT_TIMEOUT из shared/timeouts"
    )
    # Все apt-get/add-apt-repository вызовы φ1 обязаны использовать timeout=APT_TIMEOUT
    deps_apt_calls = deps_content.count("timeout=APT_TIMEOUT")
    assert deps_apt_calls >= 4, (
        f"[IMP:10][T7] python_deps.py: ожидалось ≥4 timeout=APT_TIMEOUT (apt-get/add-apt-repository), найдено {deps_apt_calls}"
    )
    # AST-проверка (не текст): в коде-вызовах φ1 не должно быть timeout=600 (комментарии TRAP
    # упоминают историческое значение — текстовая проверка ложно-RED)
    deps_tree = ast.parse(deps_content)
    bad_600: list[int] = [
        node.lineno
        for node in ast.walk(deps_tree)
        if isinstance(node, ast.Call)
        for kw in node.keywords
        if kw.arg == "timeout" and isinstance(kw.value, ast.Constant) and kw.value.value == 600
    ]
    assert not bad_600, f"[IMP:10][T7] python_deps.py: timeout=600 литералы в коде: {bad_600} (рассинхрон W1-A1)"

    logger.info("[IMP:9][timeout_literals][T7] PASS: apt-get домен использует APT_TIMEOUT канон (300)")


@pytest.mark.gate
@ldd_trajectory
# 🧪 TRAP[TEST] · 2026-08-03 · NEGATIVE (R5) · apt-get subprocess.run без timeout детектится (T7)
# · Scenario: probe (tmp_path) с subprocess.run(["apt-get", ...]) без timeout → AST-скан ловит
# · Last fail: tor_setup.py:70/87 — apt-get update/install без timeout (исходный вход T7, hang-риск)
# · Remove if: apt-timeout канон-гейт отменяется
def test_apt_subprocess_without_timeout_detected_negative(caplog, tmp_path) -> None:
    """R5 negative: subprocess.run apt-get без timeout (исходный вход T7) детектируется."""
    caplog.set_level(logging.INFO)
    import textwrap

    probe = tmp_path / "_gate_probe_apt_timeout.py"
    probe.write_text(
        textwrap.dedent(
            """\
            import subprocess
            def apt_update():
                return subprocess.run(["apt-get", "update", "-qq"], capture_output=True, text=True)
            """
        )
    )
    try:
        bad = _apt_subprocess_without_timeout(probe.read_text(errors="replace"))
        assert bad, "R5 FAIL: apt-get subprocess.run без timeout не обнаружен"
        logger.info("[IMP:9][timeout_literals][T7][R5] PASS: probe:%d apt-get без timeout detected", bad[0])
    finally:
        probe.unlink(missing_ok=True)
