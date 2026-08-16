#!/usr/bin/env python3
# GREP_SUMMARY: gate run-paths-sole deploy-paths canonical /var/lib/platform raw-literals core-internal allowlist stdlib-only anti-drift R5
# STRUCTURE: ▶ AST-скан core/internal/**/*.py → ○ Constant-str с "/var/lib/platform/" вне docstrings → ◇ allowlist-файл? → skip → ⟦RED: offenders⟧ → ⎋ PASS (0 литералов вне deploy_paths)
# region MODULE_CONTRACT
## @purpose  Run/state path sole-resolver gate (DevPlan 170 W1-A2): raw-литералы
##           "/var/lib/platform/..." в core/internal (код, НЕ docstrings) → RED.
##           Единственный источник путей — core/internal/shared/deploy_paths.py (резолверы).
##           Дублирующий литерал пути = дрейф-источник: код уходит от резолвера, дефолты
##           расходятся (регрессия-класс 142 W2 — 65 литералов /run/platform в 27 модулях).
## @scope    Сканирует core/internal/**/*.py AST-сканом: строковые литералы КОДА (ast.Constant,
##           не docstring-узлы) с подстрокой "/var/lib/platform/". Docstrings и комментарии
##           исключены (документация 142 W2 легитимна). Allowlist — документированные
##           исключения (SoT + stdlib-only + владение другими волнами).
## @invariants
##   - RED: Constant-str в коде (вне docstring) с "/var/lib/platform/" в core/internal вне allowlist
##   - Docstring/комментарий-литералы — НЕ RED (документация, не резолверы)
##   - Allowlist: deploy_paths.py (SoT); watchdog.py/cert_expiry_check.py (stdlib-only —
##     cron/systemd без PYTHONPATH, TRAP[DECISION] 2026-08-14); reboot_policy.py (вне файл-раздела
##     W1-A2); python_deps.py (W1-A1 владеет); state_machine.py/cli.py/phases/docker.py (W5 владеет);
##     inline_secrets.py (regex-паттерн детектора P4, не резолвер); telegram_notifier.py (help-строка CLI)
##   - f-строки (JoinedStr) — НЕ RED (резолверы дают f-строки; литерал в f-строке — тоже RED,
##     если Constant-часть содержит паттерн)
##   - Пути allowlist — ОТНОСИТЕЛЬНО core/internal/ (паттерн timeout-literals C1)
## @rationale DevPlan 170 W1-A2: ~15 RED + ~10 AMBER литералов /var/lib/platform/* дублировали
##            deploy_paths (research-D D1-пути). Резолверы добавлены в deploy_paths.py; гейт
##            защищает от регрессии (новый литерал = RED). R5-negative доказывает детекцию.
##            stdlib-only исключения (watchdog/cert_expiry_check): импорт core.internal в cron/
##            systemd-контексте = ModuleNotFoundError (регрессия 142 W2 P1) — канон «литерал +
##            env-override», документирован TRAP[DECISION] на месте литералов.
## @changes 2026-08-14 | DevPlan 170 W1-A2 — created
# endregion MODULE_CONTRACT

import ast
import logging
import textwrap

import pytest

from tests.conftest import ldd_trajectory
from tests.helpers.gate_helpers import repo_root

logger = logging.getLogger(__name__)

ROOT = repo_root()
_CORE_INTERNAL = ROOT / "core" / "internal"

# ── Allowlist (относительно core/internal/) — документированные исключения ──
# Каждая запись — осознанное отклонение от канона (SoT / stdlib-only / владение волной).
_ALLOWLIST_FILES: set[str] = {
    # SoT резолверов — единственный файл, где литералы /var/lib/platform/* ЛЕГАЛЬНЫ
    "shared/deploy_paths.py",
    # stdlib-only (cron без PYTHONPATH; TRAP[DECISION] 2026-08-14 на месте литерала):
    # импорт core.internal = ModuleNotFoundError каждые 5 мин (регрессия 142 W2 P1,
    # gate test_gate_watchdog_clean_env.py)
    "healthcheck/watchdog.py",
    # stdlib-only (platform-reboot.service без PYTHONPATH; TRAP[DECISION] 2026-08-14)
    "bootstrap/cert_expiry_check.py",
    # Вне файл-раздела W1-A2 (reboot-policy-state.json:54) — следующая sweep-волна
    "bootstrap/reboot_policy.py",
    # W1-A1 владеет файлом (HASH_DIR:58; брифинг W1-A2 п.8 — резолвер добавлен в deploy_paths,
    # правку файла оставили волне владельца)
    "bootstrap/python_deps.py",
    # W5/W2-A1 владеют (state.json/PLATFORM_STATE_DIR)
    "bootstrap/lifecycle/state_machine.py",
    "bootstrap/lifecycle/cli.py",
    "bootstrap/lifecycle/phases/docker.py",
    # Regex-паттерн детектора (P4 dot-source .sh) — сигнатура поиска, НЕ путь-резолвер
    "static/inline_secrets.py",
    # help-строка CLI (secrets.env) — текстовая документация, не резолвер
    "shared/telegram_notifier.py",
}


def _docstring_linenos(tree: ast.Module) -> set[int]:
    """Собрать lineno docstring-узлов (Module/ClassDef/FunctionDef первый Expr-Constant)."""
    linenos: set[int] = set()
    for node in ast.walk(tree):
        if isinstance(node, (ast.Module, ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)):
            body = node.body
            if body and isinstance(body[0], ast.Expr):
                val = body[0].value
                if isinstance(val, ast.Constant) and isinstance(val.value, str):
                    linenos.add(body[0].lineno)
    return linenos


def _find_platform_path_literals(root: "object | None" = None) -> list[tuple[str, int, str]]:
    """Найти литералы /var/lib/platform/ в КОДЕ core/internal (вне docstrings и allowlist).

    ▶ ┌core/internal/**/*.py┐ → ○ ast.parse → ○ Constant-str вне doc-узлов → ◇ /var/lib/platform/ → ⊕ offenders → ⎋ list
    ## @purpose  AC (DevPlan 170 W1-A2): 0 дублирующих литералов путей вне SoT deploy_paths.
    ##            Параметр root (DevPlan 119 H): R5-тесты сканируют probe во tmp_path —
    ##            Zero Hardcode Rule, устраняет xdist-race.
    """
    base = _CORE_INTERNAL if root is None else root
    offenders: list[tuple[str, int, str]] = []
    for p in sorted(base.rglob("*.py")):
        if "__pycache__" in p.parts:
            continue
        rel = p.relative_to(base).as_posix()
        if rel in _ALLOWLIST_FILES:
            continue
        try:
            tree = ast.parse(p.read_text(encoding="utf-8", errors="replace"))
        except (OSError, SyntaxError):
            continue
        doc_linenos = _docstring_linenos(tree)
        for node in ast.walk(tree):
            if isinstance(node, ast.Constant) and isinstance(node.value, str):
                if node.lineno in doc_linenos:
                    continue
                if node.value.lstrip().startswith("#"):
                    # Комментарий-контент (генераторы .env.example/манифестов) — текст для записи,
                    # не путь-резолвер (sync_env_defaults.py:254,661 — документация 142 W2)
                    continue
                if "/var/lib/platform/" in node.value:
                    offenders.append((rel, node.lineno, node.value.strip()))
    return offenders


@pytest.mark.gate
@ldd_trajectory
def test_no_platform_path_literals_in_core_internal(caplog) -> None:
    """0 литералов /var/lib/platform/ вне shared/deploy_paths в core/internal (AC W1-A2)."""
    caplog.set_level(logging.INFO)
    offenders = _find_platform_path_literals()
    if offenders:
        for rel, lineno, value in offenders:
            logger.error("[IMP:10][run_paths_sole] %s:%d %r", rel, lineno, value)
        pytest.fail(
            f"Литералы /var/lib/platform/* вне SoT ({len(offenders)}):\n"
            + "\n".join(f"  - {rel}:{lineno} {value!r}" for rel, lineno, value in offenders)
            + "\n\nКанон: core/internal/shared/deploy_paths.py — резолверы run_base()/"
            "wal_archive_dir()/backup_spool_dir()/bootstrap_state_dir()/..."
            " (DevPlan 170 W1-A2)."
        )
    logger.info("[IMP:9][run_paths_sole] PASS: 0 /var/lib/platform/* литералов вне shared/deploy_paths")


@pytest.mark.gate
@ldd_trajectory
# 🧪 TRAP[TEST] · 2026-08-14 · NEGATIVE (R5) · литерал /var/lib/platform/run детектится
# · Scenario: probe-файл (tmp_path) с os.environ.get("WATCHDOG_STATE_FILE", "/var/lib/platform/run/...")
# ·   → AST-сканер ловит (DevPlan 119 H: probe в tmp_path — Zero Hardcode Rule, xdist-safe)
# · Last fail: watchdog.py:74 литерал (исходный вход 170 W1-A2; канон — allowlist stdlib-only)
# · Remove if: run-paths гейт отменяется
def test_platform_run_literal_detected_negative(caplog, tmp_path) -> None:
    """R5 negative: /var/lib/platform/run литерал (исходный вход W1-A2) детектируется."""
    caplog.set_level(logging.INFO)
    probe = tmp_path / "_gate_probe_run_paths.py"
    probe.write_text(
        textwrap.dedent(
            """\
            import os
            DEFAULT_STATE_FILE: str = os.environ.get(
                "WATCHDOG_STATE_FILE",
                "/var/lib/platform/run/watchdog-state.json",
            )
            """
        )
    )
    try:
        hits = [
            (rel, ln, val)
            for rel, ln, val in _find_platform_path_literals(root=tmp_path)
            if "_gate_probe_run_paths" in rel
        ]
        assert hits, "R5 FAIL: /var/lib/platform/run literal (исходный вход W1-A2) не обнаружен"
        logger.info("[IMP:9][run_paths_sole][R5] PASS: probe %s:%d %r detected", *hits[0])
    finally:
        probe.unlink(missing_ok=True)
