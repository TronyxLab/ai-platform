# GREP_SUMMARY: lifecycle-helpers, subprocess-io, system, users, secrets, validation, domains, reporting, bootstrap-phases
# STRUCTURE: ┌helpers package (7 I/O-модулей)┐ → ◇ subprocess_io → ◇ system → ◇ users → ◇ secrets → ◇ validation → ◇ domains → ◇ reporting → ⎋ односторонняя зависимость: phases → helpers
# region MODULE_CONTRACT
## @purpose  Package marker for core/internal/bootstrap/lifecycle/helpers/ — I/O-хелперы state_machine,
##           извлечённые из монолита (DevPlan 116 B9 T1, U-08). Все функции ПУБЛИЧНЫЕ.
## @scope    Модули: subprocess_io (run_subprocess), system (apt/users-системные), users (SSH-ключи),
##           secrets (decrypt/ensure), validation (core/node.yaml/sudoers), domains (context_deployer,
##           cert_orchestrator), reporting (healthchecks/audit/telegram). Вызываются из phases.py —
##           односторонняя зависимость state_machine → phases → helpers (цикл phases↔state_machine устранён).
## @invariants
##   - Ни один helpers-модуль НЕ импортирует state_machine на уровне модуля (кроме TYPE_CHECKING) —
##     направление зависимостей только вниз (phases → helpers)
##   - helpers/domains.py использует публичную extract_domains_for_context (T3, CS-1)
##   - Каждый модуль несёт собственную MODULE_CONTRACT/GREP_SUMMARY/STRUCTURE + LDD-логи [IMP:1-10]
## @rationale DevPlan 116 B9 D1: I/O-хелперы (~730 LOC: apt/users/ssh/secrets/validation/domains)
##            вынесены в lifecycle/helpers/ пакет — state_machine.py остаётся чистой оркестрацией (~950 LOC).
## @changes  2026-08-01 · Created (B9 T1 — SRP-декомпозиция state_machine)
# endregion MODULE_CONTRACT

"""
I/O-хелперы bootstrap lifecycle (public API, односторонняя зависимость от phases.py).

Modules:
  - subprocess_io.py — run_subprocess() — безопасный subprocess wrapper
  - system.py        — is_pkg_installed/install_apt_packages/ensure_sops/ghcr_auth
  - users.py         — create_user/add_ssh_key/ensure_projects_base
  - secrets.py       — decrypt_secrets/ensure_secrets_exist
  - validation.py    — verify_core_files/validate_node_yaml/validate_sudoers
  - domains.py       — import_deploy_context/extract_domains/ssl_provision_via_orchestrator
  - reporting.py     — run_healthchecks/write_audit_log/send_telegram
"""
