# 09-Brief — B1: Деплой-канал greenfield (единый контракт)

<!-- GREP_SUMMARY: deploy-channel forced-command receive verbs SSH_ORIGINAL_COMMAND workflow deploy-project NODE-resolve status -->
<!-- STRUCTURE: ┌scope┐ → ◇ verb-контракт → ◇ forced-command → ◇ CLI/make → ◇ workflow → ⊕ критерии → ⎋ зависимости -->
# region MODULE_CONTRACT
## @purpose  Волна B1: спроектировать ЕДИНЫЙ деплой-канал под новую CI (greenfield) — вместо починки трёх разошедшихся каналов.
## @scope    U-04, U-05, U-22, U-23, U-24, U-30, U-36, U-37, U-55, U-56
## @invariants
##   - Один контракт вызова: make-таргет ↔ CLI ↔ forced-command ↔ workflow — 1:1:1:1, проверяется гейтом.
##   - Legacy-каналы (platform-deploy.yml, stage-deploy.yml, verb-словарь в deploy.sh) удаляются, не чинятся (инвариант 9).
##   - SSH_ORIGINAL_COMMAND диспетчеризуется: receive (tar) + status/verify — через один dispatcher.
# endregion MODULE_CONTRACT

$ARTIFACT_CONTRACT:
  PURPOSE: Единый, верифицируемый деплой-канал для новой CI/CD на переустановленном сервере.
  DESCRIPTION: Forced-command dispatcher по SSH_ORIGINAL_COMMAND (receive/status/verify), починка make deploy-project (--skip-verify, NODE→host резолв), удаление фантомных platform-deploy.yml/stage-deploy.yml, живая манифест-цепочка (notify-hook/generate-catalog), единый status-контракт, устранение phantom-полей version/service/project.
  RATIONALE: RC4: 5 способов вызвать деплой с разными форматами аргументов/ошибок; receive() игнорирует SSH_ORIGINAL_COMMAND — CI-верификация фиктивна (preflight маскирован, verify всегда падает); два staging-workflow шлют данные в сломанном формате. Greenfield: не чиним legacy, проектируем канал с нуля.
  ACCEPTANCE_CRITERIA: (1) forced-command = `orchestrator_cli dispatch`: диспетчеризация SSH_ORIGINAL_COMMAND (receive|status|verify|platform-deliver); (2) receive() корректно принимает tar, возвращает JSON result; (3) deploy-project.yml: preflight реально проверяет, verify работает, exit-коды честные; (4) make deploy-project: --skip-verify реализован ИЛИ удалён; NODE резолвится в host (extract_node_host); (5) platform-deploy.yml + stage-deploy.yml удалены (или переведены на новый канал) — фантом platform-deploy.sh очищен во всех 26 местах; (6) манифест-цепочка: receive → notify-hook + generate-catalog работает (или цепочка убрана из манифеста с обоснованием); (7) status: единый JSON-контракт (канал forced-command — канон; make project-status и DeployEngine.status — обёртки); (8) ai-platform.yaml: поля version/service задокументированы (или удалены из receive, версия приходит через --version); (9) verb-коллизия: validate_project_name проверяет имена против verb-словаря; (10) render-vhosts: NODE_CONFIGS_DIR с дефолтом.
  IMPLEMENTS: U-04 (forced-command receive), U-05 (deploy-project CLI), U-22 (фантом platform-deploy.sh), U-23 (3 workflow), U-24 (манифест-цепочка), U-30 (double channel), U-36 (status ×3), U-37 (phantom-поля), U-55 (render-vhosts), U-56 (verb-коллизия)
  IMPACTS: core/internal/deploy/orchestrator.py, orchestrator_cli.py, channels.py, ssh_command_parser.py, setup-node.sh, .github/workflows/{deploy-project,platform-deploy,stage-deploy}.yml, makefiles/deploy.mk, entrypoint-manifest.yaml, core/entrypoints/deploy.sh
  REQUIRES: B4 (exit-коды), B2 (гейты), B3-решения по ноде (setup-node часть)

---

## Scope

| U | Проблема | Ключевые файлы |
|---|----------|----------------|
| U-04 | receive() игнорирует SSH_ORIGINAL_COMMAND; status/verify с пустым stdin → FAIL; preflight `|| true` | orchestrator.py:673-772, deploy-project.yml:112-170, setup-node.sh:11,112 |
| U-05 | --skip-verify нет в argparse; NODE не резолвится, не передаётся | deploy.mk:58,72-78, orchestrator_cli.py:61-76, overlay_deliverer.py:158 |
| U-22 | Фантом platform-deploy.sh: 26 упоминаний | setup-node.sh:87,110-115, workflows, loki-config.yml:74,98, monitoring/defaults.yaml:5, platform-secrets/install.sh:151 |
| U-23 | 3 CI-workflow: разная логика; 2 сломаны (raw args без tar) | platform-deploy.yml:71-138, stage-deploy.yml:87-120, deploy-project.yml:129-164 |
| U-24 | Манифест-цепочка receive → notify-hook/generate-catalog мертва | entrypoint-manifest.yaml:39-41, orchestrator.py, notify-hook.sh |
| U-30 | subprocess deploy-many всегда (0,[]) — наблюдаемость выключена | deploy_orchestrator.py:516-546 |
| U-36 | Status ×3: raw compose ps / JSON exit 0 / GENERATED-STUB | project_lister.py:261-343, orchestrator_cli.py:212-215, deploy_engine.py:454-525 |
| U-37 | receive(): version всегда "latest" — phantom-поля | orchestrator.py:712-719,752-758, template-backend/ai-platform.yaml |
| U-55 | render-vhosts: NODE_CONFIGS_DIR без дефолта | bootstrap.mk:83, scaffold/add-vhost.sh:93 |
| U-56 | classify_verb prefix-матч: проект «status» задиспатчится как verb | ssh_command_parser.py:119-132 |

## Ключевые артефакты

1. **Forced-command dispatcher**: `orchestrator_cli dispatch` — читает SSH_ORIGINAL_COMMAND, маршрутизирует receive (tar stdin) / status / verify / platform-deliver; setup-node.sh:112 обновляется; verb-словарь K1 переносится в один модуль (shared/verbs.py), манифест-гейт 1:1.
2. **receive()**: возвращает JSON (project, version, sha, status); verify-гейт в CI работает; preflight без `|| true`.
3. **make deploy-project**: NODE → host через extract_node_host (chain deploy.mk → CLI → channel); --skip-verify — реализовать или убрать из deploy.mk (решение по контракту); dry-run проверка на тестовом сервере (greenfield!).
4. **Workflow**: один канал — deploy-project.yml (tar + platform-deliver + verify); platform-deploy.yml/stage-deploy.yml удаляются; downstream-триггеры — в B11.
5. **Манифест-цепочка**: notify-hook/generate-catalog либо вызываются из receive (post-deploy), либо удаляются из манифеста — без «мёртвой документации».
6. **Status-контракт**: ProjectStatus JSON — канон (exit 0/1 честно), обёртки конвертируют.
7. **Поля версии**: ai-platform.yaml-схема расширяется (version/service) ИЛИ receive принимает version из отдельного аргумента/заголовка — пиннинг по sha работает.
8. **Verb-коллизия**: validate_project_name + reserve-список verbs; тест на проект «status».

## Гейт самоверификации волны

- Гейт канала: makefile-таргет ↔ CLI-аргументы ↔ forced-command verbs ↔ workflow-команды — 1:1 (парсинг манифеста + парсинг makefile + парсинг workflow).
- e2e-прогон на переустановленном сервере: deploy-project проходит preflight → deliver → verify → status.

## Зависимости

- От: B4 (exit-коды), B2 (гейты), B10 (контрактные тесты канала).
- К: B3 (нода — setup-node часть), B11 (workflow-триггеры).
