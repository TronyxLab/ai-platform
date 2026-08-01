# 20-DevPlan — B1: Деплой-канал greenfield (единый контракт)

<!-- GREP_SUMMARY: deploy-channel dispatch SSH_ORIGINAL_COMMAND receive status verify ping verbs.py orchestrator_cli deploy-project deploy.mk deploy-project.yml platform-deploy phantom NODE-resolve extract_node_host version sha notify-hook generate-catalog deploy-many ProjectStatus render-vhosts NODE_CONFIGS_DIR -->
<!-- STRUCTURE: ┌решения D1-D7┐ → ◇ T1 verbs.py+parser → ◇ T2 dispatch+receive → ◇ T3 status-контракт → ◇ T4 workflow единый канал → ◇ T5 deploy.mk → ◇ T6 deploy-many → ◇ T7 фантом-чистка → ◇ T8 render-vhosts → ◇ T9 манифесты → ⊕ T10 самоверификация -->
# region MODULE_CONTRACT
## @purpose  Волна B1 программы хардненинга (116): спроектировать ЕДИНЫЙ деплой-канал (greenfield) — forced-command dispatcher по SSH_ORIGINAL_COMMAND, receive с честными preflight/verify/exit-кодами, один CI-workflow, устранение фантома platform-deploy.sh.
## @scope    U-04, U-05, U-22, U-23, U-24, U-30, U-36, U-37, U-55, U-56. Файлы: core/internal/shared/verbs.py (новый), core/internal/shared/ssh_command_parser.py, core/internal/shared/platform_deliver.py (удаляется), core/internal/deploy/orchestrator_cli.py, core/internal/deploy/orchestrator.py, core/internal/deploy/channels.py, core/internal/deploy/deploy_engine.py (status-контракт), core/internal/scaffold/project_lister.py, core/internal/bootstrap/deploy/deploy_orchestrator.py, core/internal/bootstrap/setup-node.sh, core/internal/bootstrap/deploy/context_deployer.py, core/internal/deploy/payload_deliverer.py, core/internal/shared/project_registry.py, core/internal/shared/deploy_paths.py, core/internal/shared/vps_readiness.py, core/internal/validate/validate_orchestrator.py, makefiles/deploy.mk, makefiles/bootstrap.mk, core/internal/scaffold/add-vhost.sh, .github/workflows/{deploy-project,platform-deploy,stage-deploy,mirror}.yml, core/entrypoint-manifest.yaml, core/AGENTS.md (generated), AGENTS.md (root), core/internal/shared/AGENTS.md, core/modules/{logging/config/loki-config.yml,loki-runtime-config.yml}, core/modules/monitoring/{defaults.yaml,config/prometheus.yml,prometheus.yml.tmpl,alert-rules.yml}, core/modules/platform-secrets/install.sh, tests/.
## @invariants
##   1. Один контракт вызова: make-таргет ↔ CLI ↔ forced-command verbs ↔ workflow — 1:1:1:1, проверяется гейтом (T10).
##   2. Legacy-каналы (platform-deploy.yml, stage-deploy.yml, verb platform-deliver, legacy-формат `deploy <project> <sha> [env]`) удаляются, не чинятся (инвариант 9 брифа, greenfield).
##   3. SSH_ORIGINAL_COMMAND диспетчеризуется: receive (tar) + status/verify/ping/remove — через один dispatcher `orchestrator_cli dispatch`.
##   4. Неизвестный verb → JSON-ошибка + exit 1 (честные exit-коды, B4); никакого дефолт-фолбэка на deploy.
##   5. Status-контракт: ProjectStatus JSON — канон; exit 0 для found/stub, exit 1 для not_found/error.
##   6. Версия пинится по sha через аргументы SSH-команды (receive <project> <sha>), а НЕ через phantom-поля ai-platform.yaml.
##   7. Python-first: новые проверки волны — pytest-гейты (trinity: tests/gates/ + @pytest.mark.gate + entrypoint-manifest gates).
## @rationale Бриф фиксирует цели (U-04..U-56); DevPlan фиксирует решения пользователя (D1-D7, 2026-08-01) и исполнительные шаги с точными файлами, чтобы Coder работал без архитектурных развилок. Подтверждённые факты: forced-command = `orchestrator_cli receive` (setup-node.sh:112) игнорирует SSH_ORIGINAL_COMMAND — CI-шаги «status test»/«verify» фактически гоняют receive с пустым stdin и падают под `|| true` (верификация фиктивна); `--skip-verify` в deploy.mk передаётся в CLI, где аргумента нет → argparse fail; deploy-project не передаёт host → SCPChannel всегда FAILED («requires 'host'»); orchestrator_cli status всегда exit 0; _deploy_orchestrator (bootstrap) всегда возвращает (0, []) без парсинга вывода; platform-deploy.sh — 26 упоминаний вне гейта B8.
## @changes 2026-08-01 · Решения пользователя (question 2026-08-01): (D1) one-shot receive — tar по stdin → `dispatch receive` = extract + preflight + compose + healthcheck + notify/catalog, затем `verify <node>`; verb platform-deliver УДАЛЯЕТСЯ; (D2) legacy-формат `deploy <project> <sha> [env]` и strip-префиксы platform-deploy удаляются — unknown verb = JSON-ошибка exit 1; (D3) --skip-verify удаляется из deploy.mk (мёртвый код); (D4) манифест-цепочка receive → notify-hook + generate-catalog ОЖИВЛЯЕТСЯ (post-deploy, неблокирующе); (D5) версия через аргументы (receive <project> <sha>), phantom-read version/service из yaml удаляется; (D6) status: found/stub → exit 0, not_found/error → exit 1; make project-status переводится на forced-command status; (D7) bootstrap deploy-many переводится на LocalChannel (на-ноде операция — тот же прецедент TRAP[DECISION] receive 2026-07-31) + парсинг JSON-вывода (U-30).
# endregion MODULE_CONTRACT

$START_DEVPLAN
$ARTIFACT_CONTRACT:
  PURPOSE: Реализация волны B1 — единый верифицируемый деплой-канал: dispatcher по SSH_ORIGINAL_COMMAND, честный receive, один workflow, статус-контракт, чистка фантома.
  DESCRIPTION: Новый forced-command dispatcher (orchestrator_cli dispatch: ping/exit/status/verify/remove/receive), receive с версией из аргументов + post-deploy notify/catalog, единый deploy-project.yml (ping-префлайт → receive → verify) с удалением platform-deploy.yml/stage-deploy.yml, make deploy-project с NODE→host резолвом и удалением --skip-verify, статус-контракт ProjectStatus (exit 0/1), наблюдаемость bootstrap deploy-many (LocalChannel + парсинг JSON), verb-словарь shared/verbs.py с reserve-списком (U-56), NODE_CONFIGS_DIR дефолт (U-55), фантом-чистка platform-deploy.sh + расширение гейта B8.
  RATIONALE: RC4: 5 способов вызвать деплой с разными форматами/ошибками; receive игнорирует SSH_ORIGINAL_COMMAND — CI-верификация фиктивна (status test / verify падают под || true); два staging-workflow шлют данные в сломанном формате; SCPChannel без host всегда FAILED; status всегда exit 0; deploy-many без наблюдаемости.
  ACCEPTANCE_CRITERIA: (1) forced-command = `orchestrator_cli dispatch`; диспетчеризация SSH_ORIGINAL_COMMAND (receive|status|verify|ping|remove); (2) receive принимает tar + версию из аргументов, возвращает JSON (project, version, sha, status); (3) deploy-project.yml: preflight = реальный ping, verify работает, exit-коды честные (без || true-масок); (4) make deploy-project: --skip-verify удалён, NODE резолвится в host (extract_node_host); (5) platform-deploy.yml + stage-deploy.yml удалены, platform-deploy.sh очищен во всех местах, гейт фантомов расширен; (6) receive → notify-hook + generate-catalog работает post-deploy; (7) status: ProjectStatus JSON — канон, exit 0/1 честно, make project-status — обёртка через forced-command; (8) version/service-поля из receive удалены, версия приходит через аргументы; (9) validate_project_name проверяет имена против verb-словаря (тест на проект «status»); (10) render-vhosts: NODE_CONFIGS_DIR с дефолтом.
  IMPLEMENTS: U-04, U-05, U-22, U-23, U-24, U-30, U-36, U-37, U-55, U-56
  IMPACTS: core/internal/deploy/{orchestrator,orchestrator_cli,channels,deploy_engine,payload_deliverer}.py, core/internal/shared/{verbs,ssh_command_parser,platform_deliver,project_registry,deploy_paths,vps_readiness}.py, core/internal/bootstrap/{setup-node.sh,deploy/context_deployer.py,deploy/deploy_orchestrator.py}, core/internal/scaffold/{project_lister.py,add-vhost.sh}, core/internal/validate/validate_orchestrator.py, makefiles/{deploy,bootstrap}.mk, .github/workflows/{deploy-project,platform-deploy,stage-deploy,mirror}.yml, core/entrypoint-manifest.yaml, core/AGENTS.md (generated), AGENTS.md (root), core/internal/shared/AGENTS.md, core/modules/{logging/config/loki-config.yml,loki-runtime-config.yml,monitoring/{defaults.yaml,config/prometheus.yml,prometheus.yml.tmpl,alert-rules.yml},platform-secrets/install.sh}, tests/
  REQUIRES: 09-Brief (B1); решения пользователя 2026-08-01 (D1-D7); B4 (exit-коды), B5 (shared), B8 (гейт фантомов — расширяется), B2 (гейт-механика); чистое рабочее дерево на старте (пользователь коммитит перед началом)
$END_ARTIFACT_CONTRACT

---

## 1. Решения пользователя (подтверждены 2026-08-01)

| D | Вопрос | Решение |
|---|--------|---------|
| D1 | Форма единого канала | **One-shot receive**: CI шлёт tar по stdin → `dispatch receive <project> <sha>` = extract + preflight + compose-deploy + healthcheck + notify/catalog в одном вызове (JSON-результат); затем `verify <node>`. Verb `platform-deliver` УДАЛЯЕТСЯ из диспетчера (shared/platform_deliver.py + его тест удаляются). Verb-множество диспетчера: ping, exit, status, verify, remove, receive. |
| D2 | Legacy-формат `deploy <project> <sha> [env]` | **Удаляется**: дефолт-фолбэк classify_verb и strip-префиксы `platform-deploy` убираются; неизвестный verb → JSON-ошибка + exit 1 (честные exit-коды B4). |
| D3 | `--skip-verify` в make deploy-project | **Удаляется из deploy.mk** — мёртвый код (аргумента нет в CLI, вызов всегда падал); verify встроен в канал. |
| D4 | Манифест-цепочка receive → notify-hook + generate-catalog (U-24) | **Оживляется**: после успешного деплоя receive вызывает notify-hook (Telegram, неблокирующий — always exit 0) + generate-catalog (regen catalog.json), best-effort, WARN при ошибке. |
| D5 | Phantom-поля version/service (U-37) | **Версия через аргументы**: `receive <project> <sha>` — CI передаёт github.sha; version попадает в DeployResult/snapshot (sha-pinning). Чтение version/service из ai-platform.yaml удаляется; схема проекта не меняется (шаблон полей не содержит). |
| D6 | Status-контракт (U-36) | **Честные коды + wrapper**: status verb — found/stub → exit 0, not_found/error → exit 1; ProjectStatus JSON — канон {project, status, containers, last_deploy}; make project-status (project_lister) переводится на forced-command status через SSH; DeployEngine.status — тот же контракт (StatusResult уже совместим). |
| D7 | Bootstrap deploy-many (U-30) | **LocalChannel + парсинг JSON**: на-ноде операция — SCP-доставка самой себе бессмысленна (тот же прецедент TRAP[DECISION] receive 2026-07-31 → LocalChannel); _deploy_orchestrator парсит JSON-вывод deploy-many → честные (deployed, failed). |

## 2. Текущее состояние worktree (старт волны)

- B9 (b18b69b) + B7 preflight (a6b5baf) закоммичены; **рабочее дерево грязное** (preflight-волна a6b5baf: изменённые .env.example, AGENTS.md, core/Makefile.common, entrypoint-manifest.yaml, preflight.py, sync_env_defaults.py, module.yaml ×5, удалён security-headers.conf и др.) — пользователь коммитит перед стартом.
- `setup-node.sh:112`: `restrict_opts="command=\"python3 -m core.internal.deploy.orchestrator_cli receive\",restrict"` — receive игнорирует SSH_ORIGINAL_COMMAND (U-04); комментарии 87/110-115 упоминают platform-deploy.sh.
- `ssh_command_parser.py`: classify_verb с дефолт-фолбэком «deploy» (U-56: проект «status» задиспатчится как verb; голый `status` без пробела → deploy); strip-префиксы platform-deploy (legacy).
- `deploy-project.yml`: preflight шлёт `status test` под `|| true` (фактически receive с пустым stdin → FAIL); deliver шлёт `platform-deliver <org> <project>` (verb будет удалён); verify-шаги маскируют ошибки.
- `platform-deploy.yml` (220 LOC) + `stage-deploy.yml` — второй/третий каналы, сырые аргументы без tar (U-23).
- `deploy.mk:58,72-78`: `--skip-verify` передаётся в CLI (аргумента нет → fail); deploy-project не передаёт host → SCPChannel всегда FAILED; LAUNCH=1 шлёт `--host "$(NODE)"` без NODE→host резолва.
- `orchestrator_cli.py:212-215`: status всегда exit 0; receive без аргументов версии (orchestrator.py:712-719,752-758 — version=latest из yaml, phantom-поля).
- `orchestrator.py:673-772` (receive): preflight-логики нет, verify вне receive; `deploy_project.yml:112-170` — preflight `|| true`.
- `bootstrap/deploy/deploy_orchestrator.py:512-541`: `_deploy_orchestrator` всегда возвращает `(0, [])` — вывод deploy-many не парсится (U-30).
- `project_lister.py:261-343` (make project-status): raw `docker compose ps` по SSH, не через status-verb.
- `bootstrap.mk:83` / `scaffold/add-vhost.sh:93`: NODE_CONFIGS_DIR без дефолта (U-55).
- platform-deploy.sh — 26 упоминаний (setup-node.sh ×4, loki-config.yml ×2, loki-runtime-config.yml ×4, monitoring/defaults.yaml ×1, prometheus.yml ×2, prometheus.yml.tmpl ×2, alert-rules.yml ×1, platform-secrets/install.sh ×1, validate_orchestrator.py ×1, deploy_paths.py, vps_readiness.py, payload_deliverer.py, ssh_command_parser.py, context_deployer.py ×2, тесты test_config_merge/test_deploy_direct/test_ssh_command_parser/test_shared_ssh_command_parser); `_PHANTOM_NAMES` (B8) — 4 имени, platform-deploy.sh НЕ включён.
- `shared/platform_deliver.py` + test_shared_platform_deliver.py — verb platform-deliver (удаляется, D1); живых потребителей build_deliver_command нет (context_deployer — только комментарии 387/413).

## 3. Задачи

### T1 — U-56/D2: shared/verbs.py + ssh_command_parser — verb-словарь, exact-match, unknown = error [FUNDAMENT]

**Файлы:** `core/internal/shared/verbs.py` (новый), `core/internal/shared/ssh_command_parser.py`, `core/internal/shared/project_registry.py`, `core/internal/shared/AGENTS.md` (инвентарь), `AGENTS.md` (root, §New shared modules), `tests/unit/test_shared_verbs.py` (новый)

**Шаги:**

1. **verbs.py** (новый shared-модуль, MODULE_CONTRACT + GREP_SUMMARY/STRUCTURE по doxygen-python):
   ```python
   CANONICAL_VERBS: tuple[str, ...] = ("ping", "exit", "status", "verify", "remove", "receive")
   # Resolve-список имён, недоступных для проектов (U-56): validate_project_name REJECTS verb-имена
   def is_verb(name: str) -> bool: ...
   ```
   Запись в shared/AGENTS.md инвентарь (критерий: ≥2 потребителя — ssh_command_parser + project_registry) + строка в root AGENTS.md §New shared modules.
2. **ssh_command_parser.py**:
   - `_strip_prefixes`: удалить шаги 3-4 (strip `platform-deploy ` / bare `platform-deploy`) — legacy (D2); оставить path-prefix strip (deploy.sh) и trim.
   - `classify_verb(cleaned)`: точный матч по CANONICAL_VERBS (включая голые `status`, `remove`, `verify`, `receive` — сейчас голый `status` уходит в deploy!); префикс-матч `verb + " "`; **unknown → raise ConfigValidationError** (никакого дефолт-фолбэка, инвариант 4).
   - `parse_ssh_command`: args по verb; для receive — `<project> [<sha>]` (два токена); для status/remove — `<project>`; verify — `<node>`; ping/exit — args=None.
   - Обновить @invariants/@changes: verb-словарь из verbs.py, unknown-семантика.
3. **project_registry.py**: `validate_project_name()` — добавить проверку `is_verb(name)` → False (U-56); константа `_VERB_RESERVE` из verbs.py; @rationale-комментарий.
4. **Тесты** `tests/unit/test_shared_verbs.py` (LDD, IMP:9-assert):
   - classify: `status` (голый) → status; `status myproj` → status; `receive proj abc123` → receive; `ping` → ping;
   - unknown: `deploy proj sha` → ConfigValidationError; `frobnicate x` → ConfigValidationError;
   - R5 negative: проект с именем `status` → validate_project_name False; `status` как SSH_ORIGINAL_COMMAND → verb status, НЕ проект;
   - `_strip_prefixes`: `platform-deploy foo` НЕ стрипится (перестал быть префиксом) — команда остаётся как есть и уходит в unknown.

**Критерий:** голый `status` классифицируется как verb; проект «status» невалиден; unknown verb → ошибка (не deploy); `pytest tests/unit/test_shared_verbs.py` зелёный.

### T2 — U-04/D1/D4/D5: orchestrator_cli dispatch + receive с версией + пост-деплой цепочка [CRITICAL]

**Файлы:** `core/internal/deploy/orchestrator_cli.py`, `core/internal/deploy/orchestrator.py`, `core/internal/deploy/channels.py`, `core/internal/deploy/payload_deliverer.py` (docstring), `tests/unit/test_orchestrator_cli_dispatch.py` (новый), `tests/unit/test_orchestrator_receive_version.py` (новый/расширение test_orchestrator.py)

**Шаги:**

1. **`dispatch` subcommand** в orchestrator_cli.py:
   - Читает `SSH_ORIGINAL_COMMAND` (env); фолбэк — CLI args; пусто → JSON `{"status":"ERROR","error":"empty SSH_ORIGINAL_COMMAND"}` exit 1.
   - Парсит через `parse_ssh_command` (T1), маршрутизирует:
     - `ping` → print "pong", exit 0 (vps_readiness CMD_PING — живой потребитель!);
     - `exit` → exit 0;
     - `status <project>` → `orchestrator.status()` JSON (contract T3), exit 0/1 по статусу;
     - `remove <project>` → `orchestrator.remove()`, exit 0/1;
     - `verify <node>` → subprocess `core/internal/verify/verify-domains.sh <node> <PLATFORM_ROOT>` (тонкая оркестрация shell-фасада, языковая политика допускает), exit-код pass-through;
     - `receive [project] [sha]` → tar из stdin → `DeployOrchestrator.receive(project, version)` (п.2);
     - ConfigValidationError (unknown verb) → JSON `{"status":"ERROR","error":...}` exit 1 (инвариант 4).
   - Логирование IMP:9 на маршрутизацию; обработка PlatformError → `return e.exit_code` (B4-контракт).
2. **receive()** (orchestrator.py, статикметод → экземплярный или сигнатура с аргументами):
   - `receive(project_name: str | None = None, version: str = "latest")` — проект из аргументов SSH-команды (валидируется validate_project_name + is_verb reserve) с фолбэком на ai-platform.yaml `name` (для локальных/ручных вызовов); **version только из аргументов (D5)** — чтение `config.get("version")`/`config.get("service")` УДАЛЯЕТСЯ (U-37), service = project_name.
   - Ужесточение: пустой stdin → ошибка; ai-platform.yaml отсутствует → ошибка (fail-fast, БЕЗ `|| true`-масок).
   - Результат JSON: `{"status", "project", "version", "sha", "error_info", ...}` — в `DeployResult.to_dict()` добавить поле `version` (AC2: project, version, sha, status; sha = version из CI).
   - **Пост-деплой цепочка (D4, U-24)**: после успешного деплоя (DEPLOYED/PARTIAL) best-effort:
     - `notify-hook.sh --severity info 🚀 <msg>` (subprocess, timeout 30s, сбой → WARN, деплой НЕ фейлится — дизайн notify-hook always exit 0);
     - `generate-catalog.sh` (subprocess, timeout 60s, сбой → WARN).
   - Обновить MODULE_CONTRACT/@changes: dispatcher-канал, версия из аргументов, цепочка.
3. **channels.py — ForcedCommandChannel.deliver**: remote_cmd = `f"receive {project} {version}"` (вместо `python3 -m ... receive`) — при forced-command на сервере эта строка становится SSH_ORIGINAL_COMMAND для dispatch (D1). Версия берётся из `payload.version`. @invariants-комментарий.
4. **Тесты**:
   - `test_orchestrator_cli_dispatch.py` (native, monkeypatch sys.argv/SSH_ORIGINAL_COMMAND, без subprocess для business-логики): ping → "pong" 0; status found → 0; status not_found → 1 (T3); unknown verb → JSON error 1; receive с tar-фикстурой (tmp_path) → DeployResult JSON содержит version;
   - `test_orchestrator_receive_version.py`: receive(project, version="abc123") → snapshot/result version=abc123; yaml БЕЗ version-поля → версия из аргументов (U-37 negative: версия НЕ "latest" при переданном sha); post-deploy цепочка: monkeypatch notify-hook/generate-catalog вызовы — вызываются после успеха, НЕ вызываются при FAILED, сбой цепочки → WARN не фейлит деплой.

**Критерий:** `echo -n "" | python3 -m core.internal.deploy.orchestrator_cli dispatch` (пустой stdin) → exit 1; `python3 -m core.internal.deploy.orchestrator_cli dispatch status nonexistent` → exit 1; `python3 -m core.internal.deploy.orchestrator_cli dispatch ping` → "pong" 0; тесты зелёные.

### T3 — U-36/D6: status-контракт ProjectStatus JSON [FUNDAMENT]

**Файлы:** `core/internal/deploy/orchestrator_cli.py`, `core/internal/deploy/deploy_engine.py` (StatusResult), `core/internal/scaffold/project_lister.py`, `core/internal/scripts/vps_status_check.py` (без изменений — уже валидирует канон), `tests/unit/test_project_status_contract.py` (новый)

**Шаги:**

1. **orchestrator_cli status** (п. T2): exit 0 для found/stub, exit 1 для not_found/error; JSON — `ProjectStatus.to_dict()` = `{project, status, containers, last_deploy}` (канон). @changes-комментарий.
2. **DeployEngine.status** (deploy_engine.py:480+): StatusResult уже несёт `{project, node, status, containers, last_deploy}` — зафиксировать в MODULE_CONTRACT: «StatusResult = тот же контракт, что ProjectStatus (поле node — расширение)». Никаких расхождений полей: проверить, что `last_deploy` структура совпадает (дикт) или задокументировать.
3. **make project-status → forced-command status** (project_lister.py:261-343, U-36): `get_status_via_ssh` — SSH-команда заменяется с raw `docker compose ps` на verb `status <project>` (через ssh_read/lib/ssh.sh, ci-deploy); ответ JSON парсится (orchestrator_cli status stdout) и рендерится в человекочитаемую таблицу (Name/Status/Ports из containers). Фолбэк-пользователи (current_user) сохраняются; timeout ≤10s.
4. **Тесты** `test_project_status_contract.py`: канон-поля у orchestrator.status().to_dict() и DeployEngine.StatusResult (set-сравнение ключей); exit-коды: found/stub → 0, not_found → 1 (через dispatch CLI-тест T2 или напрямую main()); project_lister: ssh_runner-инъекция возвращает status-JSON → рендер таблицы содержит имя проекта (не raw compose ps).

**Критерий:** ровно один JSON-контракт статуса; status exit-коды честные; project-status работает через status-verb (тест с инъекцией ssh_runner); vps_status_check.py не меняется.

### T4 — U-23/D1: deploy-project.yml — единый канал + удаление legacy workflow [CRITICAL]

**Файлы:** `.github/workflows/deploy-project.yml`, `.github/workflows/platform-deploy.yml` (удаляется), `.github/workflows/stage-deploy.yml` (удаляется), `.github/workflows/mirror.yml` (комментарий:42), `tests/test_project_ci_contract.py`, `tests/test_project_scaffolder.py` (T9-исключения — проверить актуальность), `tests/gates/test_gate_ci_coverage.py`

**Шаги:**

1. **deploy-project.yml** — шаги:
   - `Resolve target node` — без изменений (NODE_HOST_MAP, yaml_query);
   - `Validate project payload` — без изменений (make gate MODE=fast PROJECT=...);
   - `Check VPS readiness` — замена `status <project> || true` на **`ping`**: `ssh ... "ping"` → ожидается `pong` (честный preflight, без масок; `set -euo pipefail` уже есть);
   - `Deliver + deploy` — единый шаг: `tar czf - $FILES | ssh ci-deploy@host "receive ${{ inputs.project_name }} ${{ github.sha }}"` — exit-код SSH = результат receive (нет `|| true`); FILES = docker-compose.yml|compose.yaml + ai-platform.yaml + .env.platform (как сейчас);
   - `Verify deliver` — удаляется (дубль: receive уже вернул JSON с result.status; шаг «Post-deploy verify» через `verify <node>` остаётся каноном);
   - `Post-deploy verify` — без изменений (`verify ${{ env.target_node }}`).
   - Обновить @purpose/@changes-комментарии (TRAP[BUG] 2026-07-20 про «use verify verb» — актуализировать).
2. **Удаление platform-deploy.yml + stage-deploy.yml** (инвариант 9: не чинить); consumer-scan:
   - mirror.yml:42 — комментарий про stage-deploy.yml → deploy-project.yml;
   - templates/ — rglob platform-deploy.yml = 0 (гейт test_project_ci_contract.py:229 уже проверяет);
   - test_project_scaffolder.py T9-тесты (exclusion из копии) — оставить (логика copy_template жива), ре-вординг docstring по желанию;
   - test_gate_ci_coverage.py — проверить, что не ссылается на удалённые workflow (агрегаторы: core-deploy/build-platform/mirror — не трогаются).
3. **entrypoint-manifest.yaml** (см. T9): секция deploy — delegates_to актуализируется под новый канал.

**Критерий:** 0 файлов platform-deploy.yml/stage-deploy.yml в репо и templates/; deploy-project.yml использует только verbs ping/receive/verify; `set -euo pipefail` и честные exit-коды во всех SSH-шагах (нет `|| true`).

### T5 — U-05/D3: make deploy-project — NODE→host + deliver-команда + удаление --skip-verify [FUNDAMENT]

**Файлы:** `makefiles/deploy.mk`, `core/internal/deploy/orchestrator_cli.py` (subcommand deliver), `core/lib/node-resolver.sh` (extract_node_host — существует), `tests/unit/test_deploy_mk_chain.py` (новый/расширение), `core/entrypoint-manifest.yaml` (signature deploy-project)

**Шаги:**

1. **CLI subcommand `deliver`** (операторская сторона, D1-консистентно): `orchestrator_cli deliver --project <p> --version <sha> --host <h> [--user] [--key-file] [--project-dir]`:
   - ассемблирует payload (PayloadDeliverer/_assemble_payload), доставляет через ForcedCommandChannel (remote_cmd `receive <project> <version>`, T2 п.3), печатает JSON-результат с VPS (парсинг stdout deliver), exit 0/1 по нему;
   - НЕ вызывает локальный compose (обход двойного канала: orchestrator.deploy() шаг 4 `_deploy_compose` — локальный; для remote-деплоя неприменим — @rationale + TRAP[DECISION]).
2. **deploy.mk deploy-project**:
   - удалить `SKIP_VERIFY`/`--skip-verify` (D3) и `--scp`;
   - NODE → host: `source core/lib/node-resolver.sh && extract_node_host` (резолв node.yaml из node-configs, 3-candidate path) → `--host <resolved>`; NODE без yaml → fail-fast с читаемой ошибкой;
   - вызов: `python3 -m core.internal.deploy.orchestrator_cli deliver --project <name> --project-dir <dir> --host <host> [--version <sha>]`;
   - LAUNCH=1 блок в `deploy`: тот же deliver-путь (сейчас `--forced-command --host NODE` — заменить на deliver c extract_node_host).
3. **entrypoint-manifest.yaml**: signature deploy-project → `make deploy-project PROJECT=<dir> NODE=<node>` + delegates_to обновить; генерация manifest (T9).
4. **Тесты** `test_deploy_mk_chain.py`: цепочка make→CLI: парсинг deploy.mk рецепта deploy-project → содержит `deliver` + `--host` (не `--skip-verify`, не `--scp`); deliver CLI: monkeypatch ForcedCommandChannel → JSON result от VPS пробрасывается в stdout, exit по result; negative: NODE без host-резолва → exit 1.

**Критерий:** `make deploy-project PROJECT=<dir> NODE=<node>` не содержит мёртвых флагов; host резолвится через extract_node_host; deliver не выполняет локальный compose.

### T6 — U-30/D7: bootstrap deploy-many — LocalChannel + наблюдаемость [FUNDAMENT]

**Файлы:** `core/internal/bootstrap/deploy/deploy_orchestrator.py`, `core/internal/deploy/channels.py` (LocalChannel — существует), `tests/unit/test_deploy_many_observability.py` (новый/расширение)

**Шаги:**

1. **LocalChannel для deploy-many** (D7): `_deploy_orchestrator` — cmd `deploy-many --projects <names>` без `--scp`, channel = LocalChannel (на-ноде операция: payload уже в /opt/projects/<module>/; SCP-доставка самой себе бессмысленна — прецедент TRAP receive 2026-07-31). CLI deploy-many: `--local` флаг ИЛИ default LocalChannel при отсутствии --scp/--forced-command (проверить обратную совместимость: build_channel сейчас default SCPChannel — сменить дефолт на LocalChannel при отсутствии host).
2. **Парсинг JSON-вывода** (U-30): stdout deploy-many = JSON-массив DeployResult → `deployed = count(status == DEPLOYED)`, `failed = [project for status in (FAILED, ROLLED_BACK)]`; IMP:9-лог с именами; вернуть `(deployed, failed)` честно (сейчас всегда `(0, [])`).
3. Семантика WARN-only остаётся (DEPLOY_BEST_EFFORT, B4) — фейлы не роняют bootstrap, но наблюдаемы.
4. **Тесты**: _deploy_orchestrator с monkeypatched subprocess (stdout = JSON-массив 2×DEPLOYED + 1×FAILED) → (2, ["mod3"]); пустой список → (0, []); returncode != 0 → лог WARN + честный (0, failed-из-JSON).

**Критерий:** deploy-many на ноде не пытается SCP-доставить самому себе; (deployed, failed) отражают реальный JSON; `pytest tests/unit/test_deploy_many_observability.py` зелёный.

### T7 — U-22/D1/D2: фантом platform-deploy.sh + удаление platform-deliver [CRITICAL]

**Файлы:** `core/internal/bootstrap/setup-node.sh`, `core/internal/shared/platform_deliver.py` (удаляется), `tests/unit/test_shared_platform_deliver.py` (удаляется), `core/internal/shared/ssh_command_parser.py` (T1), `core/internal/deploy/payload_deliverer.py`, `core/internal/shared/deploy_paths.py`, `core/internal/shared/vps_readiness.py`, `core/internal/validate/validate_orchestrator.py`, `core/internal/bootstrap/deploy/context_deployer.py`, `core/modules/logging/config/{loki-config.yml,loki-runtime-config.yml}`, `core/modules/monitoring/{defaults.yaml,config/prometheus.yml,config/prometheus.yml.tmpl,config/alert-rules.yml}`, `core/modules/platform-secrets/install.sh`, `tests/test_config_merge.py`, `tests/test_deploy_direct.py`, `tests/unit/test_ssh_command_parser.py`, `tests/unit/test_shared_ssh_command_parser.py`, `tests/gates/test_gate_phantom_refs.py`, `core/internal/shared/AGENTS.md`

**Шаги (каждое удаление — consumer-scan по правилу программы: rg имя → код + тесты + CI + манифесты):**

1. **setup-node.sh**: `restrict_opts="command=\"python3 -m core.internal.deploy.orchestrator_cli dispatch\",restrict"` (строка 112); комментарии 11/86-87/94/110-115 — переписать без platform-deploy.sh («forced-command = orchestrator_cli dispatch, SSH_ORIGINAL_COMMAND диспетчеризуется»); лог-сообщения DONE/WARN — актуализировать.
2. **platform_deliver.py + test_shared_platform_deliver.py** — удалить (D1: verb platform-deliver мёртв); consumer-scan: build_deliver_command/parse_deliver_args потребителей нет (проверено: context_deployer — только комментарии); shared/AGENTS.md — удалить строку из инвентаря; test_importability_no_exit.py — проверить упоминание.
3. **payload_deliverer.py** (docstring 8/26/31): «Used by platform-deliver verb» → receive-канал; TRAP[BUG] 2026-07-20 про platform-deliver exit 1 — актуализировать или удалить.
4. **deploy_paths.py / vps_readiness.py / validate_orchestrator.py**: заменить/удалить упоминания platform-deploy.sh (deprecated-пути, стрипы, allowlist'ы) — проверить контекст каждого (путь к скрипту vs verb).
5. **context_deployer.py:387,413**: комментарии GENERATED-STUB «Replaced by CI platform-deliver» → «Replaced by CI receive (dispatch channel)».
6. **Мониторинг/логирование**: loki-config.yml (2), loki-runtime-config.yml (4), monitoring/defaults.yaml (1), prometheus.yml (2), prometheus.yml.tmpl (2), alert-rules.yml (1) — заменить platform-deploy.sh в текстах alert-правил/лейблах на канонические имена (receive/deploy-project) или удалить устаревшие правила (проверить семантику каждого: это alert-тексты, не исполняемый код).
7. **platform-secrets/install.sh:151** — комментарий → receive/dispatch.
8. **Тесты**: test_config_merge.py (docstrings 16/31), test_deploy_direct.py (проверить фактическое использование), test_ssh_command_parser.py + test_shared_ssh_command_parser.py — обновить под T1 (убрать platform-deploy-кейсы).
9. **Гейт фантомов** (B8 D3-механика): `_PHANTOM_NAMES` += `"platform-deploy.sh"` (allowlist остаётся пустым); проверить, что после чистки гейт зелёный.

**Критерий:** `rg "platform-deploy\.sh"` по репо (кроме git history и allowed) = 0; `rg "platform-deploy"` в shared-парсере/verbs = 0; гейт фантомов с расширенным списком зелёный.

### T8 — U-55: render-vhosts — NODE_CONFIGS_DIR с дефолтом [FUNDAMENT]

**Файлы:** `makefiles/bootstrap.mk:83`, `core/internal/scaffold/add-vhost.sh:93`

**Шаги:**

1. **bootstrap.mk**: `NODE_CONFIGS_DIR ?= <дефолт>` (проверить существующий канон: PLATFORM_ROOT/node-configs или аналог — consumer-scan по node-resolver.sh 3-candidate path и вызывающим render-vhosts).
2. **add-vhost.sh:93**: та же дефолт-логика (если скрипт принимает параметром — проверить вызов из render-vhosts; дефолт единый через lib/paths.sh при наличии).
3. Тест: запуск render-vhosts-цепочки без NODE_CONFIGS_DIR → не падает на пустой переменной (dry-run).

**Критерий:** `make render-vhosts NODE=<node>` работает без явного NODE_CONFIGS_DIR; 0 `set -u`-фейлов на незаданной переменной.

### T9 — Манифесты: entrypoint-manifest + core/AGENTS.md + глоссарий [FUNDAMENT]

**Файлы:** `core/entrypoint-manifest.yaml` (генерируемый — правится через источник/генератор), `core/AGENTS.md` (generated canon_table), `AGENTS.md` (root, глоссарий), `core/internal/shared/AGENTS.md` (инвентарь, T1/T7)

**Шаги:**

1. **entrypoint-manifest.yaml**:
   - `deploy` (строки 36-44): delegates_to → `git push → CI → .github/workflows/deploy-project.yml (receive verb) → orchestrator_cli dispatch receive → DeployOrchestrator.receive() → notify-hook + generate-catalog` (цепочка живая, D4);
   - `deploy-project` (45-51): delegates_to → `orchestrator_cli deliver (ForcedCommandChannel receive)`; signature;
   - allowed_verbs — без изменений (make-таргеты не меняются); scripts-секция orchestrator_cli.py — signature `receive/deploy-many` → `dispatch/deliver/deploy-many`.
   - Механика: править источник генератора `core/internal/scripts/generate_entrypoint_manifest.py` (SoT) → `make generate-entrypoint-manifest`; проверить `make check-manifests`.
2. **core/AGENTS.md**: `make generate-agents-md` после правки манифеста (canon_table: deploy/deploy-project строки).
3. **Root глоссарий**: строка `deploy-project` — «Direct project deploy via DeployOrchestrator (SCPChannel)» → «(forced-command receive, NODE→host)»; `deploy` — delegates_to уже упоминает receive → актуализировать упоминание notify/catalog (живое).
4. **shared/AGENTS.md** (T1/T7): инвентарь — +verbs.py, −platform_deliver.py; root AGENTS.md §New shared modules (086) — +verbs.py.

**Критерий:** `make check-manifests` зелёный (0 diff после регенерации); глоссарий не противоречит коду.

### T10 — Самоверификация волны: гейт канала 1:1:1:1 + полный gate [GATE]

**Файлы:** `tests/gates/test_gate_deploy_channel.py` (новый), `core/entrypoint-manifest.yaml` (gates-запись), `tests/gates/test_gate_phantom_refs.py` (T7), `tests/unit/test_shared_verbs.py` (T1), `tests/unit/test_orchestrator_cli_dispatch.py` (T2), `tests/unit/test_project_status_contract.py` (T3), `tests/unit/test_deploy_many_observability.py` (T6)

**Шаги (строго по порядку):**

1. **Регенерация манифестов**: `make generate-manifests` → `git diff` — изменения соответствуют T9 (не ручная правка generated).
2. **Гейт канала** `tests/gates/test_gate_deploy_channel.py` (@pytest.mark.gate + gates-запись в entrypoint-manifest, trinity):
   - make-таргеты (парсинг makefiles/deploy.mk .PHONY): deploy, deploy-project, context-promote... — каждый из затронутых имеет запись в манифесте;
   - CLI-подкоманды (build_parser orchestrator_cli): receive/status/verify/remove/ping/dispatch/deliver/deploy-many — пересекаются с verb-словарём verbs.py (1:1);
   - workflow: парсинг deploy-project.yml run-шагов → используемые verbs ⊆ {ping, receive, verify}; 0 упоминаний platform-deploy/stage-deploy;
   - negative: verb в workflow, отсутствующий в verbs.py → RED (R5).
3. **Гейты волны**: `pytest tests/gates/test_gate_phantom_refs.py -m gate` (расширенный список, T7); `pytest tests/unit/test_shared_verbs.py tests/unit/test_orchestrator_cli_dispatch.py tests/unit/test_project_status_contract.py tests/unit/test_deploy_many_observability.py` — зелёные.
4. **Полный gate**: `make gate MODE=fast` зелёный; `make test MARKER=static` зелёный.
5. **Финальный consumer-scan**: rg по удалённым именам (platform-deploy.sh, platform-deliver, stage-deploy.yml, --skip-verify) по core/ + tests/ + makefiles/ + .github/ → 0 висячих ссылок; `git status` — только ожидаемые файлы волны.
6. **Dry-run на тестовом сервере** (greenfield, ручной шаг после push): bootstrap-нода → deploy-project.yml e2e (ping → receive → verify) — помечается как отсроченный e2e-прогон (требует ноды; CI-прогон на push).

**Критерий:** все шаги зелёные; гейт канала ловит рассинхрон make↔CLI↔verbs↔workflow; `git status` — только ожидаемые файлы волны.

## 4. Риски и решения

| Риск | Митигация |
|------|-----------|
| Удаление legacy-формата `deploy <project> <sha> [env]` ломает старый CI/ручные вызовы | Greenfield: setup-node.sh меняет forced-command на dispatch в той же волне; старый формат → JSON-ошибка с понятным текстом (не тихий fallback). Все workflow обновляются в T4. |
| vps_readiness `ping`-проверка сломается при переходе на dispatch | ping-verb обязателен в диспетчере (T2 п.1); тест dispatch ping; vps_readiness.py не меняется. |
| receive post-deploy notify/catalog зафейлит деплой (сеть/секреты) | Best-effort: subprocess с timeout, сбой → WARN (неблокирующий дизайн notify-hook); тест «сбой цепочки не фейлит деплой» (T2). |
| ForcedCommandChannel remote_cmd смена на verb-форму ломает операторов без forced-command | Для ключей БЕЗ forced-command ssh сам выполнит `receive ...` — команды нет в PATH → понятная ошибка; документируется в channels.py @invariants; deliver-команда (T5) — единственный операторский путь. |
| deploy-project без локального compose (deliver) меняет поведение LAUNCH=1 | LAUNCH=1 — редкий операторский путь; обновляется на deliver в той же волне; TRAP[DECISION] в CLI фиксирует «двойной канал» как устранённый. |
| Удаление platform-deliver заденет контекстные пути (context_deployer/reconciler) | Consumer-scan показал: живых вызовов build_deliver_command нет (только комментарии); T7 п.2/5. |
| loki/prometheus alert-правила со ссылками на platform-deploy.sh потеряют смысл | Тексты правил — диагностические; замена на receive/deploy-project или удаление устаревших (T7 п.6); конфиги валидируются `docker compose config`-dry-run при необходимости. |
| DeployEngine.status vs ProjectStatus рассинхрон полей | Канон фиксируется в MODULE_CONTRACT обоих модулей + тест set-сравнения ключей (T3 п.4). |
| Гейт канала 1:1:1:1 окажется хрупким на парсинге workflow | Парсинг ограничен: YAML → run-шаги → regex verbs; negative-тест обязателен (R5); при ложных срабатываниях — сузить скоуп до строк с ssh/tar. |
| e2e на переустановленном сервере недоступен в этой волне | Отсроченный шаг T10 п.6: e2e-прогон после push (CI) + dry-run на тестовой ноде при доступности; гейты волны покрывают контракты локально. |

## 5. Критерии завершения волны (AC брифа 09-Brief)

- [ ] (1) forced-command = `orchestrator_cli dispatch`; диспетчеризация SSH_ORIGINAL_COMMAND (receive|status|verify|ping|remove); unknown → JSON-ошибка exit 1 (T1/T2).
- [ ] (2) receive принимает tar + версию из аргументов, возвращает JSON (project, version, sha, status); phantom-read version/service удалён (T2, U-37).
- [ ] (3) deploy-project.yml: preflight = реальный ping, verify работает, exit-коды честные (0 масок `|| true`) (T4).
- [ ] (4) make deploy-project: --skip-verify удалён; NODE резолвится в host через extract_node_host; deliver без локального compose (T5).
- [ ] (5) platform-deploy.yml + stage-deploy.yml удалены; platform-deploy.sh = 0 упоминаний; platform-deliver удалён; гейт фантомов расширен (T4/T7).
- [ ] (6) receive → notify-hook + generate-catalog работает post-deploy (best-effort) (T2/D4).
- [ ] (7) status: ProjectStatus JSON — канон, exit 0/1 честно; make project-status — обёртка через forced-command status (T3).
- [ ] (8) версия приходит через аргументы (receive <project> <sha>), sha-pinning в snapshots (T2/D5).
- [ ] (9) validate_project_name резервирует verb-имена; тест на проект «status» (T1).
- [ ] (10) render-vhosts: NODE_CONFIGS_DIR с дефолтом (T8).
- [ ] Гейт волны: канал 1:1:1:1 + фантом-гейт + unit-тесты волны + `make gate MODE=fast` зелёный (T10).
- [ ] `make fix-gate && git add -u` выполнен перед коммитом (CI pre-flight, .kilo/rules/_project.md).

$END_DEVPLAN
