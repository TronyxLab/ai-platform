# 05-Brief — B4: Контракты исключений и exit-кодов

<!-- GREP_SUMMARY: PlatformError exit-code raise ValueError RuntimeError sys.exit main-int contract legacy-parity -->
<!-- STRUCTURE: ┌scope┐ → ◇ иерархия → ◇ exit-контракт → ◇ legacy-parity → ⊕ критерии → ⎋ зависимости -->
# region MODULE_CONTRACT
## @purpose  Волна B4: enforce-ить иерархию PlatformError и единый контракт exit-кодов CLI.
## @scope    U-12, U-29, U-39
## @invariants
##   - Бизнес-слой: только raise (PlatformError), никогда sys.exit; sys.exit — только в main().
##   - Exit-коды: 0=ok, 1=generic, 2=ConfigNotFound, 3=ConfigParse, 4=Validation, 10=Fatal (контракт node_yaml.py:1397-1400 — расширить на весь core).
# endregion MODULE_CONTRACT

$ARTIFACT_CONTRACT:
  PURPOSE: Сделать типизированные исключения и exit-коды работающим контрактом вместо декорации.
  DESCRIPTION: Замена 36 bare raise на типизированные PlatformError, единый except-хендлер в main(), контракт main()->int, формализация «legacy parity» как осознанной политики с allowlist.
  RATIONALE: Иерархия создана (2026-07-26), но retrofit не выполнен: 36 bare raise ValueError/RuntimeError, exit-коды пробрасываются только в 3 main() через return, `sys.exit` живёт в библиотечных функциях (DeployEngine:953, provisioner:154). Caller не может программно различить тип ошибки; security-path (decrypt_secrets) inconsistent.
  ACCEPTANCE_CRITERIA: (1) 0 bare raise ValueError/RuntimeError в core/internal (ruff-правило, allowlist на время миграции); (2) единый `except PlatformError as e: sys.exit(e.exit_code)` паттерн во всех main(); (3) main()->int контракт: business-функции не вызывают sys.exit; (4) «legacy parity» — формализованный allowlist (deploy best-effort политика) с TRAP-документацией и сжимается волнами; (5) exit-коды 2/4/10 задокументированы в core/AGENTS.md и проверяются гейтом.
  IMPLEMENTS: U-12 (36 bare raise), U-29 (sys.exit в библиотеках, 3 CLI-паттерна), U-39 (legacy parity ×12)
  IMPACTS: core/internal/llm/policy_schema.py, scripts/validate_module_yaml.py, shared/platform_deliver.py, template_engine.py, deploy/deploy_engine.py, provisioner.py, bootstrap/converge/reconciler.py, deploy/orchestrator_cli.py
  REQUIRES: B2 (гейты с allowlist — механика миграции)

---

## Scope

| U | Проблема | Ключевые файлы |
|---|----------|----------------|
| U-12 | 36 raise ValueError/RuntimeError; exit_code только в 3 main() | llm/policy_schema.py:9, validate_module_yaml.py:6, platform_deliver.py:3, config_renderer.py:3, template_engine.py:2, ssh_command_parser.py:2; state_machine.py:1440, deploy_orchestrator.py:908, key_provisioner.py:746-749 |
| U-29 | sys.exit в библиотечных функциях: DeployEngine._handle_first_deploy:953 (NoReturn), provisioner:154; 3 CLI-паттерна (main()->int / main()->None / business sys.exit) | deploy/deploy_engine.py, provisioner.py, scaffold/project_scaffolder.py, deploy/reconciler_projects.py:539-541 |
| U-39 | «legacy parity» ×12: WARN→exit 0, failing step не прерывает, HC_DONE_MARKER всегда, широкие except | bootstrap/deploy/deploy_orchestrator.py:236,398,427,486,790; channels.py; payload_deliverer.py |

## Ключевые артефакты

1. Ruff custom rule (flake8-extension или локальный lint-скрипт в core/internal/scripts): запрет bare raise в core/internal; allowlist-файл известных нарушений, сжимаемый волнами.
2. Типизация: ConfigValidationError, ConfigNotFoundError, PlatformFatalError по месту (policy_schema → ConfigValidationError, decrypt_secrets → PlatformFatalError и т.д.).
3. Единый паттерн main(): `def main() -> int` + `sys.exit(main())`; except PlatformError → sys.exit(e.exit_code).
4. Удаление sys.exit из DeployEngine._handle_first_deploy (raise PlatformFatalError) и provision_networks (raise); тесты на importability (provisioner.provision_networks не убивает процесс).
5. «Legacy parity» — контракт `DEPLOY_BEST_EFFORT = True` в shared/contracts.py + TRAP[DECISION] с rev-датой; allowlist-гейт на широкие except.

## Гейт самоверификации волны

- Линт-гейт: 0 bare raise в core/internal (после сжатия allowlist).
- Гейт importability: pytest-тест импортирует все библиотечные модули — sys.exit отсутствует вне main().

## Зависимости

- От: B2 (allowlist-механика).
- К: B5 (shared-модули используют типизированные исключения), B1 (деплой-канал — единый контракт exit).
