"""
# GREP_SUMMARY: check-suite, models, CheckSpec, CheckOutcome, resolve-command, error-summary, dataclasses
# STRUCTURE: ▶ ┌CheckSpec: id/tier/timeout/gate_modes/cmds┐ → ◇ resolve_command(gate_mode) → ⎋ cmd|None ▶ ┌CheckOutcome┐ → ◇ passed → ◇ error_summary → ⎋ отчёт
# region MODULE_CONTRACT
## @purpose  Data models пакета check_suite (DevPlan 170 W3 — извлечено из монолита
##           core/internal/check_suite.py): CheckSpec — нормализованная запись SoT-манифеста
##           core/check-suite.yaml; CheckOutcome — результат исполнения одного чека.
## @scope    core/internal/check_suite/models.py — stdlib-only. Потребители: manifest.py,
##           runner.py, report.py, diagnostic.py, gate.py, diff.py, single.py, __init__.py.
## @invariants
##   - CheckSpec.resolve_command: cmds[gate_mode] приоритетнее cmd; диагностический контекст
##     (gate_mode=None) резолвит канонический fast-вариант (паритет Phase 3)
##   - CheckOutcome.passed: exit_code == 0; passed_no_tests/blocked/auto_fixed — независимые флаги
##   - error_summary: ключевые fail/error-строки (≤ max_lines) для отчёта
## @rationale Выделение моделей — первый слой декомпозиции монолита (research-A §1): датаклассы
##            без побочных эффектов, чистое ядро, от которого зависят все runner-модули.
## @changes 170 W3 — extracted from check_suite.py (monolith 1666→package)
# endregion MODULE_CONTRACT
"""

from __future__ import annotations

from dataclasses import dataclass, field

# region DATA_MODELS


@dataclass
class CheckSpec:
    """Нормализованная запись манифеста (одна проверка набора)."""

    id: str
    tier: str
    timeout: int
    gate_modes: list[str] = field(default_factory=list)
    diagnostic: bool = True
    xdist: bool = True
    sequential: bool = False
    allow_no_tests: bool = False
    non_blocking: bool = False
    junit: str | None = None
    project_filter: bool = False
    docker: bool = False
    cmd: str | None = None
    cmds: dict[str, str] | None = None

    # region FUNC_CheckSpec_RESOLVE_COMMAND
    # ⚠️ TRAP[BUG] · 2026-08-02 · P1 · Диагностический прогон МОЛЧА пропускал чеки с только cmds
    # · Symptom: `make check` отчитывался GREEN за 21s, но gates/static_audit/predeploy НЕ исполнялись
    # ·   (7/11 чеков; static_audit 3106 тестов отсутствовал в прогоне — ложный зелёный)
    # · Root: resolve_command(gate_mode=None) возвращал self.cmd=None для чеков с только cmds
    # ·   (gates/gates-docker/static_audit/predeploy) — diagnostic-ветка «если cmd None — пропустить»
    # · Fix: diagnostic-контекст резолвит канонический fast-вариант (cmds["fast"]) — паритет
    # ·   Phase 3 (fast-выражения); run_diagnostic не может молча пропустить
    # ·   чек с резолвленной командой (assert-страховка отсутствует, но fast-фолбэк закрывает класс)
    # · Prevention: unit-тест test_resolve_command_diagnostic_falls_back_to_fast (регресс-гард)
    ## @purpose  Выбор команды чека: cmds[gate_mode] приоритетнее cmd (per-mode выражения РАЗНЫЕ:
    ##           gates fast/full, static_audit fast/full, predeploy fast/full — паритет ci.mk).
    ##           Диагностический контекст (gate_mode=None): канонический fast-вариант
    ##           (Phase 3 исполнял fast-выражения: gates без docker,
    ##           static_audit через test_runner, predeploy без docker) — cmd ИЛИ cmds["fast"].
    ## @io       ⇥ gate_mode: str | None (fast|full|ci-docker) → ⎋ str | None (команда или None)
    ## @complexity O(1)
    def resolve_command(self, gate_mode: str | None) -> str | None:
        """Resolve the command: cmds[gate_mode] wins; diagnostic falls back to cmds['fast']."""
        if gate_mode and self.cmds and gate_mode in self.cmds:
            return self.cmds[gate_mode]
        if self.cmd:
            return self.cmd
        if self.cmds:
            # Диагностика (gate_mode=None): fast-вариант — канонический diagnostic-набор.
            # Без этого чеки с ТОЛЬКО cmds (gates/static_audit/predeploy) МОЛЧА пропускались бы.
            return self.cmds.get("fast") or next(iter(self.cmds.values()), None)
        return None

    # endregion FUNC_CheckSpec_RESOLVE_COMMAND


@dataclass
class CheckOutcome:
    """Результат исполнения одного чека."""

    name: str
    exit_code: int
    stdout: str = ""
    stderr: str = ""
    duration_ms: float = 0.0
    auto_fixed: bool = False
    passed_no_tests: bool = False
    blocked: bool = False  # non_blocking провал (gate не роняет)

    @property
    def passed(self) -> bool:
        return self.exit_code == 0

    def error_summary(self, max_lines: int = 20) -> str:
        """Извлечь ключевые строки ошибок для отчёта (формат отчёта)."""
        combined = (self.stderr + "\n" + self.stdout).strip()
        if not combined:
            return "(no output)"
        lines = combined.split("\n")
        key_lines: list[str] = []
        for line in lines:
            stripped = line.strip()
            if not stripped:
                continue
            lower = stripped.lower()
            if any(kw in lower for kw in ("fail", "error", "warning:", "could not", "unable", "ref", "undefined")):
                key_lines.append(stripped)
        if not key_lines:
            non_empty = [ln.strip() for ln in lines if ln.strip()]
            key_lines = non_empty[-max_lines:]
        return "\n".join(key_lines[:max_lines])


# endregion DATA_MODELS
