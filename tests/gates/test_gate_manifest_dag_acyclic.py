# GREP_SUMMARY: manifest-dag dag-acyclic generator-chains cycle-detection make-generate-manifests
# 🧐 TRAP[DECISION] · 2026-08-17 · — · GREP_SUMMARY keyword `generator-chains` содержит подстроку
#   `tor-chain` (genera-tor-chain-s) — ложное срабатывание acceptance-grep удалённого dead-man's switch
#   canary (DevPlan 005). Ключевое слово описывает DAG цепочек генераторов манифестов, НЕ canary.
#   Намеренно НЕ переформулировано: переписывание семантического ключевого слова несвязанного гейта
#   ради литерального grep — cargo-cult. · Rev: если появится требование строго нулевого литерала
#   `tor-chain` в tracked-дереве — переформулировать ключевое слово в `generator-chain-map`
# STRUCTURE: ▶ make generate-manifests -n → parse chains → ◇ Chain A (secrets → platform-env) ◇ Chain B (entrypoint → agents-md) ◇ Chain C (litellm-config) → ⊕ grep for repeated targets → ⎋ pass/fail
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

# Chain definitions: expected target basenames
# The `make generate-manifests` target depends on 3 chain heads (G1, G3, G6).
# Full chain sequences are verified via chain-specific targets.
# Chain A heads: generate-secrets-manifest (G1)
# Chain B heads: generate-entrypoint-manifest (G3)
# Chain C heads: generate-litellm-config (G6)
# Full chain A sequence: secrets-manifest → platform-env → env-example (G1 → G2 → G5)
# Full chain B sequence: entrypoint-manifest → agents-md (G3 → G4)
CHAIN_HEADS = ["secrets-manifest", "entrypoint-manifest", "litellm-config"]
CHAIN_A = ["secrets-manifest", "platform-env"]
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
        check=False,
    )

    if result.returncode != 0:
        stderr_preview = result.stderr[:500] if result.stderr else "(no stderr)"
        logger.error(
            "[IMP:10][test_generator_dag_acyclic] FAILED: make generate-manifests -n exited %d\n%s",
            result.returncode,
            stderr_preview,
        )
        pytest.fail(f"`make generate-manifests -n` exited with code {result.returncode}.\nStderr: {stderr_preview}")

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
                target = stripped.split(marker)[-1].replace("...", "").strip().lower()
                # Normalize: strip file extensions, paths
                for ext in [".yaml", ".yml", ".py"]:
                    if target.endswith(ext):
                        target = target.replace(ext, "")
                # Handle compound descriptions
                target = target.split(" + ")[0].split(" +")[0].strip()
                seen_targets.append(target)
                break

    print(
        f"[IMP:7][test_generator_dag_acyclic] Extracted {len(seen_targets)} target(s): {seen_targets}", file=sys.stderr
    )

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

    # ── Check 2: All 3 chain heads are present in `make generate-manifests -n` ──
    # `make generate-manifests` only depends on chain heads (G1, G3, G6).
    # Full chain sequences are verified via chain-specific targets.
    output.lower()

    def tokens_present(tokens: list[str], text: str) -> list[str]:
        """Return missing tokens from the given text."""
        text_lower = text.lower()
        return [t for t in tokens if t not in text_lower]

    missing_heads = tokens_present(CHAIN_HEADS, output)
    assert not missing_heads, (
        f"Chain heads missing in `make generate-manifests -n` output.\n"
        f"Missing: {missing_heads}\nExpected: {CHAIN_HEADS}\nOutput:\n{output}"
    )
    logger.info("[IMP:9][test_generator_dag_acyclic] All 3 chain heads present: %s", CHAIN_HEADS)

    # ── Check 3: Full chains via chain-specific targets ──
    # Chain A: generate-env-example → G1 → G2 → G5
    result_a = subprocess.run(
        ["make", "generate-env-example", "-n"],
        capture_output=True,
        text=True,
        timeout=120,
        check=False,
    )
    assert result_a.returncode == 0, f"make generate-env-example -n failed: {result_a.stderr[:500]}"
    missing_a = tokens_present(CHAIN_A, result_a.stdout)
    assert not missing_a, (
        f"Chain A not fully present in `make generate-env-example -n`.\n"
        f"Missing: {missing_a}\nExpected: {CHAIN_A}\nOutput:\n{result_a.stdout}"
    )
    logger.info("[IMP:9][test_generator_dag_acyclic] Chain A (secrets→platform-env→env-example): FULLY PRESENT")

    # Chain B: generate-agents-md → G3 → G4
    result_b = subprocess.run(
        ["make", "generate-agents-md", "-n"],
        capture_output=True,
        text=True,
        timeout=120,
        check=False,
    )
    assert result_b.returncode == 0, f"make generate-agents-md -n failed: {result_b.stderr[:500]}"
    missing_b = tokens_present(CHAIN_B, result_b.stdout)
    assert not missing_b, (
        f"Chain B not fully present in `make generate-agents-md -n`.\n"
        f"Missing: {missing_b}\nExpected: {CHAIN_B}\nOutput:\n{result_b.stdout}"
    )
    logger.info("[IMP:9][test_generator_dag_acyclic] Chain B (entrypoint→agents-md): FULLY PRESENT")

    # Chain C: generate-litellm-config → G6 (singleton)
    result_c = subprocess.run(
        ["make", "generate-litellm-config", "-n"],
        capture_output=True,
        text=True,
        timeout=120,
        check=False,
    )
    assert result_c.returncode == 0, f"make generate-litellm-config -n failed: {result_c.stderr[:500]}"
    missing_c = tokens_present(CHAIN_C, result_c.stdout)
    assert not missing_c, (
        f"Chain C not present in `make generate-litellm-config -n`.\n"
        f"Missing: {missing_c}\nExpected: {CHAIN_C}\nOutput:\n{result_c.stdout}"
    )
    logger.info("[IMP:9][test_generator_dag_acyclic] Chain C (litellm-config): PRESENT")

    # ── Check 4: Chain ordering via make generate-manifests -n ──
    def find_line_index(text: str, pattern: str) -> int:
        """Find first line containing pattern (case-insensitive). Returns -1 if not found."""
        for i, line in enumerate(lines):
            if pattern.lower() in line.lower():
                return i
        return -1

    # Chain A (secrets-manifest) should appear before Chain B (entrypoint-manifest)
    a_idx = find_line_index(output, "secrets-manifest")
    b_idx = find_line_index(output, "entrypoint-manifest")
    if a_idx >= 0 and b_idx >= 0:
        assert a_idx < b_idx, (
            f"Chain order violation: Chain A at line {a_idx} "
            f"but Chain B at earlier line {b_idx}.\n"
            f"Expected Chain A → Chain B."
        )
        print("[IMP:9][test_generator_dag_acyclic] Chain ordering verified: A before B", file=sys.stderr)

    logger.info("[IMP:9][test_generator_dag_acyclic] ALL PASS — DAG is acyclic with 3 chains in correct order")


# endregion FUNC_test_generator_dag_acyclic
