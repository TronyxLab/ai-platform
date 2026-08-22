# Findings 04 — God modules/classes

Context: LOC allowlist (`tests/gates/test_gate_loc_allowlist.py`) already adjudicates raw size (3 core monoliths + 6 test giants; default 500-LOC warning). Findings below are responsibility-based.

## ARCH-0012 — `agent_check/__init__.py`: 1092-LOC package init carrying 6 responsibilities
- **Severity:** P2 · **Confidence:** 0.9 · **Churn:** M · **Phase:** pre-launch acceptable (mechanical), post-launch safer
- **Files:** `core/internal/agent_check/__init__.py` (32 top-level defs/classes): schema block (13 TypedDicts :130-249, dataclasses :260,:315) · git change detection (`_git_changed`:411) · three tool adapters (`run_ruff`:497, `run_basedpyright`:567, `run_static`:640) · doc-header policy (`check_doc_headers`:695) · FP-registry policy (`load_fp_registry`:785, `_dedupe`:844) · orchestration/report/CLI (`run`:892, `_human_report`:1002, `main`:1045)
- **Evidence:** it's an `__init__.py` — every import surface pulls the full graph of an unrelated domain.
- **Scenario:** adding a 4th tool adapter or touching report format edits the same file as the schema definitions; merge friction between parallel DevPlans on the agent L1 signal (`make agent-check`).
- **Impact:** moderate (dev-tool), but this is the mandated agent gate — stability matters.
- **Minimal fix:** split into `types.py`, `changed.py`, `runners/{ruff,pyright,static}.py`, `doc_headers.py`, `report.py`; `__init__` becomes re-export shim (public API stable).

## ARCH-0013 — `lifecycle/phases/system.py`: 7 of 14 bootstrap phases in one "system" bucket
- **Severity:** P2 · **Confidence:** 0.8 · **Churn:** M · **Phase:** post-launch preferred (bootstrap regression = fresh-VPS failure)
- **Files:** `core/internal/bootstrap/lifecycle/phases/system.py` (1236 LOC): `phase_system_bootstrap`:395 (192 LOC), `_create_ci_deploy_user`:626, `phase_user_accounts`:678, `phase_platform_setup`:794, `phase_node_configuration`:1008, `phase_converge_services`:1141, `phase_node_config_update`:1169, `phase_converge_update`:1222; sibling `helpers/system.py` also ~1000 LOC
- **Evidence:** canon prescribes phase functions in *domain* modules; user-accounts and converge domains are unrelated to system provisioning yet live here.
- **Scenario:** converge-phase fix re-runs gates against a file spanning apt/docker/tor/users/converge; incident triage ambiguity ("which system broke?").
- **Minimal fix:** extract `phases/converge.py`, `phases/node_config.py`, `phases/user_accounts.py`; keep re-exports in `system.py`.

## ARCH-0014 — `DeployOrchestrator`: 17 methods / 1032-LOC class; rollback cluster extractable
- **Severity:** P2 · **Confidence:** 0.75 · **Churn:** M–L (41 test files reference deploy.orchestrator) · **Phase:** post-launch (highest regression risk; needs characterization tests)
- **Files:** `core/internal/deploy/orchestrator.py:186`. Clusters: pipeline (`deploy`:252 111 LOC, `_prepare_deploy`:376, `_apply_deploy`:455, `deploy_many`:645) · **rollback/snapshot (`rollback`:711, `_rollback_deploy`:593, `_rollback_compose`:1132, `_restore_payload_files`:1094 — cohesive ~230 LOC)** · lifecycle ops (`status`:781, `remove`:817) · payload plumbing
- **Evidence:** decomposition precedent exists and works (`receive`:900 → ReceiveFlow delegate; post-deploy chain → hooks module).
- **Impact:** fix-forward rollback semantics changes edit 4 methods interleaved with happy-path code; highest-churn class in repo.
- **Minimal fix:** extract `deploy/rollback_manager.py`; keep thin delegating `rollback()` signature.

## ARCH-0015 — `scaffold/vhost_renderer.py`: templating + validation + docker harness + orchestration + CLI
- **Severity:** P3 · **Confidence:** 0.75 · **Churn:** S–M · **Phase:** pre-launch OK
- **Files:** `vhost_renderer.py` (1233 LOC): `generate_vhost_body`:299 (113-LOC inline template), `check_duplicate_domains`:499, `nginx_t_harness`:819 (**197-LOC docker run nginx -t harness**), `render_all`:851 (146), `main`:1117
- **Impact:** infrastructure verification entangled with text generation; template-syntax changes re-review docker-harness logic.
- **Minimal fix:** move `nginx_t_harness` + validators → `vhost_validate.py` or `shared/`.

## ARCH-0016 — `bootstrap/lifecycle/cli.py`: CLI dispatch + embedded smoke-verification suite (~250 LOC)
- **Severity:** P3 · **Confidence:** 0.65 · **Churn:** S · **Phase:** pre-launch fine
- **Files:** `lifecycle/cli.py` (1164 LOC): parser/modes (`_run_phases`:757) + verification subsystem (`_forced_command_smoke`:498, `_smoke_check_authorized_keys`:525, `_smoke_check_dispatch_ping`:553, `_final_verification_pass`:601, `_run_liveness_probe`:1104)
- **Minimal fix:** extract `lifecycle/verification.py`.
