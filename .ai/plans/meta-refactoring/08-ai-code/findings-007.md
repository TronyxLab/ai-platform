# Findings 007 — Test-suite AI-smells (mirroring implementation)
# Wave 1 · agent: QA test-mirror

## AI-0046 [MEDIUM] [test-mirror/private-imports]
Files: tests/unit/test_platform_export_metrics.py:48 (~10 tests); tests/unit/test_cert_collector.py:35
Symbols: imports of _get_node_name, _get_node_yaml_path, _load_cert, _san_match
Evidence: suites built on underscore-private helpers; any internal rename breaks tests while main()/public behavior unchanged.
Why AI-pattern: tests generated alongside impl copy its internal shape instead of driving public surface.
Minimal cleanup: route through main() outputs where feasible; mark intentional seams. Churn ~40 lines. Post-launch OK. Confidence: high.

## AI-0047 [MEDIUM] [test-mirror/mock-call-wiring]
Files: tests/unit/test_deploy_orchestrator.py:90-98 (+routing family)
Evidence: mock.patch.object(orch,"_deploy_sequential")… assert_called_once_with(["postgres","redis"],"/mods","/core",{}) — pins private decomposition and exact arg tuples; behavior-preserving refactor (merging seq/parallel paths) breaks suite.
Minimal cleanup: assert on observable outcomes (order of executed modules/results), not collaborator wiring. Churn ~30 lines. Confidence: high.

## AI-0048 [MEDIUM] [test-mirror/hardcoded-contract-violation]
Files: tests/unit/test_status_collectors.py:33,520,541
Evidence: curl_mock.assert_called_once_with("grafana:3000","/api/health",5) — container name + port + timeout literal hardcoded, violating repo's own tests/AGENTS.md invariant #3 (derive from infra).
Cleanup: derive from infra fixtures/constants. Churn ~15 lines. Confidence: high.

## AI-0049 [MEDIUM] [test-mirror/private-entrypoint-as-API]
Files: tests/unit/test_project_status_contract.py:27 (_dispatch); tests/unit/test_practices_check_project.py:39,285 (_run_check)
Evidence: suites exercise private dispatchers as "canonical objects"; CLI refactor breaks them despite stable verb behavior.
Cleanup: drive public verbs/entrypoints. Churn ~30 lines. Confidence: high.

## AI-0051 [MEDIUM·TOP-CANDIDATE] [boilerplate-dupe/lost-enforcement]
Files: tests/unit/test_platform_export_metrics.py:183-189 + 10 more copies (185,235,286,335,373,395,457,493,538,574,609); tests/unit/test_cert_collector.py:118-127 (+163,197,252,273); tests/unit/test_host_collector.py:69-93 (+111,157,187)
Evidence: ≥20 hand-copied LDD-trajectory blocks diverge from canonical tests/_conftest/ldd.py:37-62 (_print_ldd_trajectory); two variants iterate ["message","msg"] via getattr silent-empty fallback; cert_collector variant LOST the IMP:9 presence assert entirely (anti-illusion rule silently disabled there).
Why AI-pattern: boilerplate propagated by copy/paste instead of helper import; each copy drifts independently.
Actual risk: log-trajectory enforcement silently absent in whole files; future edits to canon don't propagate.
Minimal cleanup: replace copies with _print_ldd_trajectory import (mechanical sed-level). Churn ~80 lines across 3 files, zero behavior risk. Pre-launch: yes (cheap, restores QA signal). Confidence: high.

## AI-0050 [LOW] [tautological]
Files: tests/unit/test_cross_layer_helpers.py:336-337 — asserts isinstance(return,bool/str) of annotated helper (language-guarantee class, R2-adjacent). Churn <5 lines. Confidence: med.

## AI-0052 [LOW] [boilerplate-dupe/benign-divergence]
Files: tests/unit/test_host_collector.py variant keeps found_imp9 assert but uses diverged iteration; will silently diverge from future canon fixes. Covered by AI-0051 cleanup. Confidence: high.

# Negative results: no bare @pytest.mark.skip; no except-pass swallowing in tests/; gates' inline LDD blocks are faithful canon copies (by-design); skipif(CI/E2E) blocks legitimate env-gating.
