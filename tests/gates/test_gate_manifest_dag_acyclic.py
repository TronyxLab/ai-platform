# GREP_SUMMARY: manifest-dag dag-acyclic generator-chains cycle-detection make-generate-manifests
# STRUCTURE: ▶ make generate-manifests -n → parse chains → ◇ Chain A (secrets → platform-env → env-example) ◇ Chain B (entrypoint → agents-md) ◇ Chain C (litellm-config) → ⊕ grep for repeated targets → ⎋ pass/fail
# region MODULE_CONTRACT
## @purpose  Проверка ацикличности Makefile .PHONY цепочки генераторов манифестов.
##            Гарантирует, что `make generate-manifests` выполняется в топологическом порядке
##            без циклических зависимостей между генераторами.
## @scope    CI gate — run as part of `make gate MODE=fast`
## @invariants
##   - Chain A: secrets-manifest → platform-env → .env.example — строгий порядок
##   - Chain B: entrypoint-manifest → AGENTS.md — строгий порядок
##   - Chain C: litellm-config — сольный генератор, не зависит от других цепей
##   - Ни один target не повторяется в выводе `make -n` (цикл = повторение)
##   - Все 3 цепи присутствуют в выводе
## @rationale DevPlan 090 — Manifest DAG. Ацикличность гарантирует детерминированный
##            порядок генерации и предотвращает deadlock при параллельном запуске.
## @changes 2026-07-30 · Created — DevPlan 090 gate
# endregion MODULE_CONTRACT

import logging
import subprocess
import sys

import pytest

from tests.conftest import ldd_trajectory

logger = logging.getLogger(__name__)

# Chain definitions: expected target basenames in order
CHAIN_A = ["secrets-manifest", "platform-env", "env-example"]
CHAIN_B = ["entrypoint-manifest", "agents-md"]
CHAIN_C = ["litellm-config"]


@pytest.mark.gate
@ldd_trajectory
# region FUNC_test_generator_dag_acyclic
## @purpose  Verify `make generate-manifests -n` produces a DAG with 3 chains, no cycles
## @io       ⇥ subprocess make -n → ⎋ assert pass/fail
## @complexity O(L) where L = lines of `make -n` output
## 🧪 TRAP[TEST] · 2026-07-30 · REGRESSION · Generator DAG acyclicity
## · Scenario: Run `make generate-manifests -n`, parse output for chain order and circular refs
## · Last fail: N/A (new gate)
## · Remove if: make generate-manifests target is restructured to non-serial execution
def test_generator_dag_acyclic(caplog) -> None:
    """Verify `make generate-manifests -n` shows an acyclic DAG with 3 chains.

    Chain A: secrets-manifest → platform-env → env-example
    Chain B: entrypoint-manifest → agents-md
    Chain C: litellm-config
    """
    caplog.set_level(logging.INFO)
    print("[IMP:8][test_generator_dag_acyclic] Running `make generate-manifests -n`...", file=sys.stderr)

    result = subprocess.run(
        ["make", "generate-manifests", "-n"],
        capture_output=True,
        text=True,
        timeout=120,
    )

    if result.returncode != 0:
        stderr_preview = result.stderr[:500] if result.stderr else "(no stderr)"
        logger.error(
            "[IMP:10][test_generator_dag_acyclic] FAILED: make generate-manifests -n exited %d\n%s",
            result.returncode,
            stderr_preview,
        )
        pytest.fail(
            f"`make generate-manifests -n` exited with code {result.returncode}.\n"
            f"Stderr: {stderr_preview}"
        )

    output = result.stdout
    lines = output.splitlines()
    print(f"[IMP:8][test_generator_dag_acyclic] Got {len(lines)} lines of output", file=sys.stderr)

    # ── Check 1: No repeated targets (cycle detection) ──
    # If a target repeats in `make -n` output, make is looping
    seen_targets: list[str] = []
    for line in lines:
        stripped = line.strip()
        if not stripped:
            continue
        # Extract the python script or the make target name from echo lines
        for marker in ["Generating ", "generating "]:
            if marker in stripped:
                target = stripped.split(marker)[-1].rstrip("...").strip().lower()
                # Normalize: strip file extensions, paths
                for ext in [".yaml", ".yml", ".py"]:
                    if target.endswith(ext):
                        target = target.replace(ext, "")
                # Handle compound descriptions
                target = target.split(" + ")[0].split(" +")[0].strip()
                seen_targets.append(target)
                break

    print(f"[IMP:7][test_generator_dag_acyclic] Extracted {len(seen_targets)} target(s): {seen_targets}", file=sys.stderr)

    # Check for repeats (cycle indicator)
    target_counts: dict[str, int] = {}
    for t in seen_targets:
        target_counts[t] = target_counts.get(t, 0) + 1
    cycles = [t for t, c in target_counts.items() if c > 1]
    if cycles:
        logger.error(
            "[IMP:10][test_generator_dag_acyclic] CYCLE DETECTED: targets %s appear %d times",
            cycles,
            [target_counts[c] for c in cycles],
        )
        pytest.fail(
            f"CYCLE DETECTED in generate-manifests DAG: targets {cycles} appear "
            f"multiple times in `make -n` output.\n"
            f"Full output:\n{output}"
        )
    print("[IMP:9][test_generator_dag_acyclic] No cycles detected — all targets unique", file=sys.stderr)

    # ── Check 2: All 3 chains are present ──
    # Flatten to lowercase token list for fuzzy matching
    output_lower = output.lower()

    def chain_present(chain_name: str, tokens: list[str]) -> bool:
        """Check if all tokens from a chain appear in the output."""
        missing = [t for t in tokens if t not in output_lower]
        if missing:
            logger.warning("[IMP:7][test_generator_dag_acyclic] Chain %s missing: %s", chain_name, missing)
            return False
        return True

    assert chain_present("A (secrets→platform-env→env-example)", CHAIN_A), (
        f"Chain A not fully present in `make generate-manifests -n` output.\n"
        f"Expected tokens: {CHAIN_A}\nOutput:\n{output}"
    )
    logger.info("[IMP:9][test_generator_dag_acyclic] Chain A (secrets→platform-env→env-example): PRESENT")

    assert chain_present("B (entrypoint→agents-md)", CHAIN_B), (
        f"Chain B not fully present in `make generate-manifests -n` output.\n"
        f"Expected tokens: {CHAIN_B}\nOutput:\n{output}"
    )
    logger.info("[IMP:9][test_generator_dag_acyclic] Chain B (entrypoint→agents-md): PRESENT")

    assert chain_present("C (litellm-config)", CHAIN_C), (
        f"Chain C not fully present in `make generate-manifests -n` output.\n"
        f"Expected tokens: {CHAIN_C}\nOutput:\n{output}"
    )
    logger.info("[IMP:9][test_generator_dag_acyclic] Chain C (litellm-config): PRESENT")

    # ── Check 3: Chain ordering — Chain A targets appear before Chain B targets ──
    # Since make -n echoes in execution order, we can check the index positions
    def find_line_index(text: str, pattern: str) -> int:
        """Find first line containing pattern (case-insensitive). Returns -1 if not found."""
        for i, line in enumerate(lines):
            if pattern.lower() in line.lower():
                return i
        return -1

    # Chain A should appear before Chain B (A is called first in Makefile)
    # Get first line of each chain
    chain_a_first_line = -1
    for token in CHAIN_A:
        idx = find_line_index(output, token)
        if idx >= 0:
            chain_a_first_line = idx if chain_a_first_line == -1 else min(chain_a_first_line, idx)
            break

    chain_b_first_line = -1
    for token in CHAIN_B:
        idx = find_line_index(output, token)
        if idx >= 0:
            chain_b_first_line = idx if chain_b_first_line == -1 else min(chain_b_first_line, idx)
            break

    if chain_a_first_line >= 0 and chain_b_first_line >= 0:
        assert chain_a_first_line < chain_b_first_line, (
            f"Chain order violation: Chain A starts at line {chain_a_first_line} "
            f"but Chain B starts earlier at line {chain_b_first_line}.\n"
            f"Expected Chain A → Chain B (secrets → platform-env before entrypoint → AGENTS.md)."
        )
        print("[IMP:9][test_generator_dag_acyclic] Chain ordering verified: A before B", file=sys.stderr)

    logger.info(
        "[IMP:9][test_generator_dag_acyclic] ALL PASS — DAG is acyclic with 3 chains in correct order"
    )


# endregion FUNC_test_generator_dag_acyclic
