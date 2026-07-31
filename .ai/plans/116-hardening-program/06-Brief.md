# 06-Brief — B8: Dead-code волна (consumer-scan)

<!-- GREP_SUMMARY: dead-code steps.py orchestrator-available content-hash s3-ssl-cache resume_phase dangling-refs consumer-scan -->
<!-- STRUCTURE: ┌scope┐ → ◇ мёртвые модули → ◇ фантомы/dangling → ◇ тесты-консерваторы → ⊕ критерии → ⎋ зависимости -->
# region MODULE_CONTRACT
## @purpose  Волна B8: удаление мёртвого кода с обязательным consumer-scan (прецедент audit_logging.sh — сломал provision на 2 дня).
## @scope    U-26, U-27, U-40, U-41, U-42, U-64, U-66
## @invariants
##   - Любое удаление сопровождается: rg по потребителям (код+тесты+CI+манифест) → удаление консервирующих тестов → зелёный gate.
##   - Мёртвый код не «чинится», а удаляется; если потребитель существует — он мигрирует, а не сохраняется ради него.
# endregion MODULE_CONTRACT

$ARTIFACT_CONTRACT:
  PURPOSE: Очистить кодовую базу от рудиментов Strangler-Fig и фантомных ссылок.
  DESCRIPTION: Удаление steps.py, _ORCHESTRATOR_AVAILABLE-флага и мёртвых функций, dead CLI-утилит, фасадов content-hash.sh/s3-ssl-cache.sh, resume_phase, yaml_read_domain_config; устранение dangling-ссылок (включая критичную phases.py:225 → удалённый deploy-project.sh).
  RATIONALE: RC2: удаление старых реализаций не сопровождается удалением потребителей — фантомные имена, compatibility-слои, тесты-консерваторы. phases.py:225 пишет forced-command на несуществующий файл — новые ноды получат сломанные authorized_keys.
  ACCEPTANCE_CRITERIA: (1) steps.py удалён (манифест/AGENTS.md обновлены); (2) _ORCHESTRATOR_AVAILABLE + deploy_via_orchestrator(docker_orchestrator) + deliver_via_orchestrator_scp удалены; (3) json_field_extractor, url_encoder удалены; (4) content-hash.sh, s3-ssl-cache.sh удалены вместе с CERT_SCRIPTS-тестом; (5) resume_phase/_grouped_phases удалены вместе с 3 пинящими тестами (или тесты переписаны на реальный путь); (6) phases.py:225 переведён на реальный forced-command; install.sh:20, sync_env_defaults:174, AGENTS.md:99, .env.example:47 — очищены; (7) yaml_read_domain_config удалена + тест test_deploy_modules.py:732-831 обновлён; (8) gate dead-code зелёный с нулевым allowlist.
  IMPLEMENTS: U-26 (_ORCHESTRATOR_AVAILABLE), U-27 (steps.py), U-40 (CLI-утилиты), U-41 (фасады), U-42 (dangling refs), U-64 (yaml_read_domain_config), U-66 (resume_phase)
  IMPACTS: core/internal/bootstrap/lifecycle/*, core/lib/yaml_read.sh, core/internal/bootstrap/{content-hash.sh,s3-ssl-cache.sh}, core/internal/notify/url_encoder.py, core/internal/scripts/json_field_extractor.py, tests/*, core/entrypoint-manifest.yaml
  REQUIRES: B5 (shared-модули — некоторые dead-копии заменяются shared), B10 (тесты-консерваторы удаляются согласованно)

---

## Scope

| U | Проблема | Ключевые файлы |
|---|----------|----------------|
| U-26 | _ORCHESTRATOR_AVAILABLE в 2 файлах; deploy_via_orchestrator:963, deliver_via_orchestrator_scp:243 — 0 callers | docker_orchestrator.py:104-111,980, overlay_deliverer.py:38,243,264 |
| U-27 | steps.py 615 LOC неиспользуемых хелперов; suppress-import; в манифесте | steps.py, state_machine.py:68-69, entrypoint-manifest.yaml:635,645, lifecycle/__init__.py:13 |
| U-40 | json_field_extractor (161 LOC, «for other shell consumers» — ложь), url_encoder (46 LOC) | scripts/json_field_extractor.py, notify/url_encoder.py |
| U-41 | content-hash.sh (87 LOC), s3-ssl-cache.sh (26 LOC) — никто не source'ит; тест требует файлы | bootstrap/content-hash.sh, bootstrap/s3-ssl-cache.sh, tests/test_cert_backup_gap.py:48-52 |
| U-42 | **phases.py:225 forced-command → удалённый deploy-project.sh**; install.sh:20 source audit_logging.sh; sync_env_defaults:174 → .env.example:47; AGENTS.md:99; deploy.mk:66; workflows:130,180 | phases.py, platform-secrets/install.sh, sync_env_defaults.py, AGENTS.md |
| U-64 | yaml_read_domain_config: 0 production-callers; тест требует её | lib/yaml_read.sh:121-131, tests/test_deploy_modules.py:732-831 |
| U-66 | resume_phase/_grouped_phases: 0 callers в core; 3 теста пинят | state_machine.py:213-223,967,990, tests/integration/test_bootstrap_dry_run.py:775, tests/e2e/test_failure_scenarios.py:61, tests/test_node_lifecycle_static.py:552 |

## Ключевые артефакты

1. Consumer-scan чек-лист (обязателен для каждого удаления): rg по repo (код, tests/, .github/, makefiles/, entrypoint-manifest.yaml, AGENTS.md) → список правок → удаление → локальный gate.
2. Удаление steps.py: убрать suppress-import из state_machine, consumer-записи из entrypoint-manifest.yaml, упоминания из bootstrap/AGENTS.md.
3. phases.py:225: заменить на `orchestrator_cli receive` (как setup-node.sh:112) — ИЛИ согласовать с B1 (единый verb-канал).
4. Тесты-консерваторы: CERT_SCRIPTS (s3-ssl-cache.sh), resume_phase-пины (3), yaml_read_domain_config (test_deploy_modules) — удалить/переписать в той же волне.
5. Правка фантомных ссылок: platform-secrets/install.sh (audit.sh вместо audit_logging.sh), sync_env_defaults:174 (dev_cert_generator.py), AGENTS.md глоссарий (make dev-certs → dev_cert_generator.py).
6. Gate dead-code (существующий test_gate_dead_code.py, BFS call-graph): allowlist обнуляется; новые orphans = RED.

## Гейт самоверификации волны

- test_gate_dead_code.py + test_gate_no_unregistered_entrypoint.py зелёные с нулевым allowlist.
- rg-гейт фантомов: 0 упоминаний deploy-project.sh/state_migration.py/audit_logging.sh/generate-dev-certs.sh в коде и CI.

## Зависимости

- От: B1-решения по деплой-каналу (phases.py:225 связано), B5 (часть копий заменяется shared).
- К: B9 (SRP-декомпозиция на очищенной базе).
