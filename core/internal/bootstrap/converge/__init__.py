# GREP_SUMMARY: converge-package, reconciler, desired-state, python-decomposition, infra, perms, audit, projects, networks, vhosts, volumes, sudoers, runtime, monitoring
# STRUCTURE: ┌reconciler.py оркестратор R1-R10┐ → ⚡ 9 доменных модулей (perms/audit/projects/networks/vhosts/volumes/sudoers/runtime/monitoring) → ⊕ infra.py (глобалы+report/exit/subprocess) → ⎋ exit_code {0,1,2} + JSON report
# region MODULE_CONTRACT
## @purpose  Python decomposition package for converge.sh (W4-E3). Replaces shell reconcile logic
##           with typed Python modules. B9 T2 (U-31): SRP-декомпозиция reconciler-монолита (2286 LOC) —
##           reconciler.py — оркестратор (~250 LOC), 9 доменных модулей + infra.py.
## @scope    core/internal/bootstrap/converge/ — reconciler.py (оркестратор R1-R10 + main),
##           infra.py (модульные глобалы + report/exit/subprocess/unit_filter + константы),
##           perms.py (R1), audit.py (R2), projects.py (R3), networks.py (R4),
##           vhosts.py (R5+R6), volumes.py (R7), sudoers.py (R8), runtime.py (R9),
##           prometheus_tsdb.py (R10 — TSDB self-heal, 142 W3)
## @invariants
##   - reconciler.py — единственный entrypoint для converge reconciliation (депеширует доменам)
##   - Каждый R-unit автономен и независим от других; глобалы — в infra.py (публичные имена)
##   - JSON report обязателен для всех режимов (--dry-run, --reconcile)
##   - Домены импортируют helpers из infra (report_add/set_exit/run_subprocess) — единый канон
## @rationale  DevPlan 116 B9 D3: 2286-LOC монолит reconciler → оркестратор + 8 доменов + infra.
##            Каждый домен — одна ответственность (SRP), тестируем отдельно.
## @changes  2026-08-01 · B9 T2 — SRP-декомпозиция reconciler (домены + infra)
# endregion MODULE_CONTRACT

"""
Converge reconciliation package.

Modules:
  - reconciler.py — оркестратор R1-R10 (main + dispatch)
  - infra.py      — модульные глобалы + report/exit/subprocess/unit_filter + константы
  - perms.py      — R1 reconcile_perms (executable-bit)
  - audit.py      — R2 reconcile_audit_log (audit.jsonl 0664 root:adm + ci-deploy adm)
  - projects.py   — R3 reconcile_projects (project dirs + stub + .env.platform)
  - networks.py   — R4 reconcile_networks (proxy-net + connectivity)
  - vhosts.py     — R5 detect_hosts_drift + R6 verify_vhosts (nginx vhosts)
  - volumes.py    — R7 reconcile_volumes (detect-only named volumes, O7)
  - sudoers.py    — R8 reconcile_sudoers (sudoers.d drift + self-heal)
  - runtime.py    — R9 reconcile_runtime_state (container state + compose up -d + cooldown)
  - prometheus_tsdb.py — R10 reconcile_prometheus_tsdb (TSDB self-heal, 142 W3)
"""
