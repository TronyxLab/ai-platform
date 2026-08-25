# GREP_SUMMARY: gate manifests-up-to-date check-manifests freshness generated git-diff
# STRUCTURE: ▶ ┌generated_files┐ → git diff --exit-code → ◇ returncode 0? → ⊕ pass/fail → ⎋ LDD trajectory
# region MODULE_CONTRACT
## @purpose  Freshness gate for generated manifests. Runs git diff --exit-code directly on
##            generated files (no make indirection). File list synced with __check_manifests_original.
## @scope    CI gate — replaces 381 LOC of manual validation
## @invariants
##   - Runs git diff --exit-code on generated files (not subprocess make)
##   - Exit code 0 = all generated files up to date
##   - Exit code 1 = drift detected, error message guides to `make generate-manifests`
##   - File list MUST stay synced with __check_manifests_original in Makefile
## @rationale DevPlan 046 (W3-2): git diff вместо subprocess make. Экономия ~0.5s (fork
##            make vs fork git). Список файлов строго соответствует __check_manifests_original.
## @changes 2026-07-24 | Refactored: subprocess.run make → direct git diff (DevPlan 046 W3-2)
# endregion MODULE_CONTRACT

import logging
import subprocess

import pytest

from tests.conftest import ldd_trajectory

logger = logging.getLogger(__name__)

# Synced with __check_manifests_original in Makefile (lines 91-93)
_GENERATED_FILES = [
    "core/secrets-manifest.yaml",
    "platform-env.yaml",
    "tests/_conftest/smoke_env_generated.py",
    "tests/helpers/env_defaults_generated.py",
    "core/entrypoint-manifest.yaml",
    "core/AGENTS.md",
]


@pytest.mark.gate
@ldd_trajectory
# ⚠️ TRAP[DECISION] · 2026-08-03 · — · xdist_group("serial") снят — фантом-маркер (DevPlan 124 T3)
# · Rejected: оставить маркер / ввести --dist loadgroup
# · Reason: при -n auto (load distribution) xdist_group игнорируется (требует --dist loadgroup,
#   test_gate_timeout_literals.py:66) — маркер создавал ложное ощущение защиты. Гейт безопасен
#   параллельно: тесты НЕ пишут в generated files (единственный писатель — pre-commit/генераторы
#   вне pytest); git diff --exit-code параллелится с любым тестом без эффекта. --dist loadgroup
#   НЕ вводится: риск деградации параллелизма без реальной гонки.
# · Rev: при появлении теста, реально пишущего в общие файлы → пересмотреть serial-механизм.
# 🧪 TRAP[TEST] · 2026-07-22 · REGRESSION · Gate invariant — all generated manifests up to date
# · Scenario: Generated files (secrets-manifest.yaml, platform-env.yaml, smoke_env_generated.py,
#   env_defaults_generated.py, entrypoint-manifest.yaml, AGENTS.md) must match authoritative sources.
# · Last fail: N/A (new gate)
# · Remove if: make check-manifests mechanism is superseded
def test_manifests_up_to_date(caplog):
    """Verify all generated files are up to date via git diff --exit-code.

    АРБИТР ВОПРОСА «дерево == HEAD» (DevPlan 16 T2.E): divergence = коммит ещё не сделан.
    Repair-действие — COMMIT (не make generate-manifests: диск уже == генераторам, это
    вопрос парного арбитра check-manifests в manifest_driver).

    Replaces subprocess.run(["make", "check-manifests"]) with direct git diff --exit-code
    on the generated files list synced from __check_manifests_original in Makefile.
    Saves ~0.5s by eliminating make-fork indirection (DevPlan 046 W3-2).

    Параллельно-безопасен (DevPlan 124 T3): гейт НЕ пишет в generated files — единственный
    писатель pre-commit-хук/генераторы вне pytest; git diff --exit-code — read-only.
    Маркер @pytest.mark.xdist_group("serial") снят: при -n auto (load) он игнорируется
    (требует --dist loadgroup) — был фантомом, а не защитой.
    """
    caplog.set_level(logging.INFO)
    logger.info(
        "[IMP:8][test_manifests_up_to_date] Running git diff --exit-code on %d generated files...",
        len(_GENERATED_FILES),
    )

    cmd = ["git", "diff", "--exit-code", "--", *_GENERATED_FILES]
    result = subprocess.run(
        cmd,
        capture_output=True,
        text=True,
        timeout=60,
        check=False,
    )

    if result.returncode != 0:
        diff_output = result.stdout or result.stderr or "(no diff output)"
        logger.error(
            "[IMP:10][test_manifests_up_to_date] FAILED: generated files differ from committed HEAD\n%s",
            diff_output,
        )
        # DevPlan 16 T2.E (проц.№1): арбитр отвечает на вопрос «дерево == HEAD» —
        # правильное действие КОММИТ, а не регенерация (диск уже == генераторам;
        # make generate-manifests здесь no-op и вводит в заблуждение).
        pytest.fail(
            f"Generated manifests differ from committed HEAD (git diff exit={result.returncode}).\n"
            ">>> Правильное действие: СКОММИТИТЬ их — git add <files> && git commit.\n"
            ">>> НЕ запускай make generate-manifests: этот арбитр проверяет 'дерево == HEAD',\n"
            "    диск УЖЕ соответствует генераторам (это арбитр check-manifests).\n"
            ">>> Если файлы правились руками ВНЕ GENERATED-регионов — правка будет затёрта:\n"
            "    откатись или правь SoT + перегенерируй осознанно.\n"
            f"Diff output:\n{diff_output}"
        )

    logger.info("[IMP:9][test_manifests_up_to_date] PASSED — all generated manifests up to date")
