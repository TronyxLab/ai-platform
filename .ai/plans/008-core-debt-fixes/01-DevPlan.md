# GREP_SUMMARY: DevPlan, core-debt-fixes, ci_deploy_key, platform-deliver, payload-delivery, adopt-project-validation, forced-command
# STRUCTURE: ▶ ┌3 debts D1-D3┐ → ◇ contracts → ⊕ $TASKS (T1-T5) → ⊕ $PARALLEL_GROUPS (3 waves) → ⟦$TEST_SPEC⟧ → ⎋ next steps

$START_DEVPLAN

## $ARTIFACT_CONTRACT
- **PURPOSE:** Устранить 3 латентных дефекта core платформы, зафиксированных в `.ai/plans/007-dance-site-launch/02-Debt.md` (D1, D2, D3).
- **DESCRIPTION:** (D1) node.yaml.node.ci_deploy_key извлекается bootstrap.sh и доходит до step_6_create_ci_deploy_user; (D2) новый verb `platform-deliver <project>` в forced-command deploy-project.sh — первичная доставка project payload (tar.gz по stdin) в /opt/projects/<name>/ + SCP-шаг в reusable workflow + идемпотентное создание /opt/projects в bootstrap; (D3) adopt-project.sh: fail-fast вместо дефолта `personal`, lowercase для ghcr.io, exact-case для uses:, валидация context против node.yaml.
- **RATIONALE:** Q: почему сейчас? A: D1 ломает КАЖДЫЙ первый bootstrap ноды (CI-деплой мёртв без ручного env); D2 ломает деплой КАЖДОГО adopted-проекта (ручной SCP root'ом); D3 воспроизводит drift при каждом adopt. Все три — системные, не разовые.
- **ACCEPTANCE_CRITERIA:** `make gate MODE=fast` зелёный; новые тесты из $TEST_SPEC проходят; существующие contract-тесты не сломаны; invocation-контракт `<project> <ref>` и сигнатура bootstrap без ключа — backward-compatible.
- **IMPLEMENTS:** `.ai/plans/007-dance-site-launch/02-Debt.md` D1 (HI), D2 (HI), D3 (MED).
- **IMPACTS:** core/entrypoints/bootstrap.sh, core/internal/bootstrap/{remote-cmd.sh,node-lifecycle.sh}, core/internal/deploy/deploy-project.sh, .github/workflows/deploy-project.yml, core/internal/scaffold/adopt-project.sh, AGENTS.md, tests/.
- **REQUIRES:** Локальный запуск pytest; НЕ требует доступа к VPS (все проверки — статические/subprocess-тесты).

---

## Requirements Analysis — критерии успеха

1. **D1:** ключ из node.yaml доходит до `step_6_create_ci_deploy_user` без env-обходов — по той же цепочке, что owner_key (`bootstrap.sh` → `--ci-deploy-key` → `node-lifecycle.sh`). Пустой ключ = текущее поведение (skip + WARN), никаких breaking changes.
2. **D2:** первый деплой adopted-проекта проходит БЕЗ ручного SCP: CI доставляет payload через тот же ключ ci-deploy (forced-command verb), restrict сохранён, нет git-токенов на ноде.
3. **D3:** `adopt-project.sh` без явного org падает fail-fast (не пишет `personal`); ghcr-пути lowercase; `uses:` exact-case; drift относительно node.yaml обнаруживается на этапе adopt.
4. Все изменения аддитивны: существующие invocations, тесты и checkpoint-маркеры не ломаются.
5. Полный локальный gate зелёный: `make gate MODE=fast`.

## Верифицированный контекст (explore-аудит этой сессии)

| # | Факт | Где |
|---|------|-----|
| C1 | `bootstrap.sh:114` извлекает ТОЛЬКО owner_key (python3+yaml); `:124` local-вызов, `:143` `build_ssh_cmd` | core/entrypoints/bootstrap.sh |
| C2 | `node-lifecycle.sh:59-60` УЖЕ парсит `--ci-deploy-key`; `step_6` (:311-338) УЖЕ потребляет `PLATFORM_CI_DEPLOY_KEY` c корректным skip при пустом | core/internal/bootstrap/node-lifecycle.sh |
| C3 | `build_ssh_cmd()` в remote-cmd.sh:60-101 — сигнатура `(node, owner_key, age_key, passthrough...)`; `%q`-квотинг | core/internal/bootstrap/remote-cmd.sh |
| C4 | `deploy-project.sh:39` `PROJECTS_BASE=/opt/projects`; `:178-226` parse_ssh_command; `:203-213` требует существующий PROJECT_DIR + compose; НЕ создаёт и НЕ доставляет | core/internal/deploy/deploy-project.sh |
| C5 | `restrict`+`command=` в authorized_keys перехватывает и scp/sftp → голый scp-шаг ci-deploy НЕ работает; stdin forced-command доступен | SSH-семантика (проверено против отчёта explore — его тезис «scp не блокируется» ОШИБОЧЕН) |
| C6 | `adopt-project.sh:134` `PROJECT_ORG="${PROJECT_ORG:-${PLATFORM_ORG:-personal}}"`; `:254,268` IMAGE_NAME ghcr; `:303,312` uses:; `:472,492` register_in_node_yaml пишет repo как есть | core/internal/scaffold/adopt-project.sh |
| C7 | Стиль тестов: bash subprocess + `_extract_func`/`_run_bash`, tmp_path как PROJECTS_BASE, LDD caplog/stderr assert IMP:9 | tests/test_bootstrap_auto.py, tests/test_contract_deploy_ssh.py |

## §Contracts (формализация ДО имплементации)

### Contract 1 — ci_deploy_key propagation (D1)
```
node.yaml: node.ci_deploy_key (string, optional — схема уже есть: node.schema.json:91-94)
bootstrap.sh: CI_DEPLOY_KEY=$(python3 ... d.get('node',{}).get('ci_deploy_key','')) — по образцу owner_key; НЕ fatal при пустом
local mode:  node-lifecycle.sh ... [--ci-deploy-key "$CI_DEPLOY_KEY"]  (флаг добавляется ТОЛЬКО при непустом значении)
remote mode: build_ssh_cmd(node, owner_key, ci_deploy_key, age_key, passthrough...) — новый 3-й позиционный параметр;
             в SSH-команду добавляется $(printf '%q' '--ci-deploy-key') $(printf '%q' "$key") при непустом.
             ВСЕ существующие вызовы build_ssh_cmd обновить (grep -rn "build_ssh_cmd" core/); build_update_ssh_cmd НЕ трогать.
env-приоритет: явный PLATFORM_CI_DEPLOY_KEY (env) > node.yaml (env остаётся рабочим override-каналом)
```

### Contract 2 — verb `platform-deliver` (D2, решение пользователя: Deliver-verb в forced-command)
```
SSH_ORIGINAL_COMMAND: "platform-deliver <project>"          (диспетчеризация в parse_ssh_command ДО ветки deploy)
stdin:      tar.gz stream, жёсткий cap 1 MiB (head -c с проверкой остатка / dd)
<project>:  та же валидация имени, что у deploy-ветки (reuse существующей; запрет '/', '..', пустого)
whitelist:  ТОЛЬКО top-level файлы docker-compose.yml | compose.yaml | ai-platform.yaml | .env.platform
            (никаких директорий, симлинков, hardlink'ов; tar --no-same-owner; extract в mktemp -d → валидация → mv в PROJECT_DIR)
effects:    mkdir -p ${PROJECTS_BASE}/${PROJECT}; атомарная замена файлов; audit_log DELIVER-START/DELIVER-SUCCESS/DELIVER-FAIL
exit:       0 = success; 1 = validation/size/extract error (ничего не записано в PROJECT_DIR)
инварианты: restrict сохранён; PROJECTS_BASE — единственный источник пути (никаких /opt/projects хардкодов в новых строках);
            существующий invocation "<project> <ref>" не меняется
```

### Contract 3 — CI payload step (D2)
```
.github/workflows/deploy-project.yml, новый шаг ПЕРЕД ssh-deploy:
  tar czf - docker-compose.yml ai-platform.yaml [.env.platform если существует] | ssh ci-deploy@<host> "platform-deliver <project>"
  (тот же ключ/host-resolve, что у deploy-шага; compose.yaml как альтернатива docker-compose.yml)
Отказ доставки = fail job ДО деплоя.
```

### Contract 4 — adopt-project org/context (D3)
```
1. FAIL-FAST: если org резолвится в дефолт "personal" (нет --org, нет PLATFORM_ORG, нет context в существующем
   ai-platform.yaml) → log IMP:10 + exit 1 с подсказкой "--org <github-org>". Литерал "personal" как дефолт УДАЛЯЕТСЯ.
2. ghcr-пути: IMAGE_NAME использует "${workflow_org,,}" (lowercase); uses: использует $workflow_org как есть (exact case).
3. Консистентность: если node-configs/<node>/node.yaml доступен и содержит context — сверить с PROJECT_ORG
   case-insensitive; при расхождении casing → WARN + использовать вариант из node.yaml; при расхождении имени → exit 1.
4. register_in_node_yaml: repo пишется с org exact-case (согласованным по п.3).
```

## Data Flow (целевой, D2)

```
CI (deploy-project.yml)
  → step build-and-push → ghcr.io/<org lowercase>/<project>:<sha>
  → step deliver-payload: tar czf - <whitelist> | ssh ci-deploy@node "platform-deliver <project>"   [T3]
      → forced-command → parse_ssh_command: verb=platform-deliver → mkdir -p → tmp extract → validate → mv  [T2]
  → step deploy: ssh ci-deploy@node "<project> <sha>" → atomic up → healthcheck                      (без изменений)
Bootstrap (init): node.yaml.ci_deploy_key → bootstrap.sh → --ci-deploy-key → step_6 → authorized_keys [T1]
                  + идемпотентный step: mkdir -p /opt/projects, owner ci-deploy                       [T1]
```

---

## $TASKS

### T1 — D1: ci_deploy_key propagation + /opt/projects base dir (Coder, complexity 4)
Файлы: `core/entrypoints/bootstrap.sh`, `core/internal/bootstrap/remote-cmd.sh`, `core/internal/bootstrap/node-lifecycle.sh`, `tests/test_bootstrap_auto.py`.
1. Реализовать Contract 1 (извлечение + local + remote пути). Перед правкой `build_ssh_cmd` — `grep -rn "build_ssh_cmd" core/` и обновить ВСЕ call-sites под новую сигнатуру.
2. В `node-lifecycle.sh` добавить идемпотентный шаг (по образцу соседних step_* с checkpoint_step): `mkdir -p /opt/projects && chown ci-deploy:ci-deploy /opt/projects` — выполняется после создания юзера ci-deploy. Повторный вызов = no-op (инвариант №6).
3. Тесты — 3 функции из $TEST_SPEC (стиль C7: `_bash`/`_extract_func`, LDD IMP:9 assert).
4. TRAP[BUG] у места извлечения в bootstrap.sh (root: ключ объявлен в схеме, но не потреблялся; см. Debt D1).
- **Acceptance:** `python -m pytest tests/test_bootstrap_auto.py -s -v` зелёный; `bash -n` обоих .sh; пустой ci_deploy_key → поведение идентично текущему.
- **Deps:** нет.

### T2 — D2: verb platform-deliver в deploy-project.sh (Coder, complexity 5)
Файлы: `core/internal/deploy/deploy-project.sh`, `tests/test_contract_deploy_deliver.py` (новый).
1. Реализовать Contract 2. Диспетчеризация в `parse_ssh_command` (строки ~178-226); переиспользовать существующую валидацию имени проекта и `audit_log`.
2. Тесты — 5 функций из $TEST_SPEC, стиль `tests/test_contract_deploy_ssh.py` (bash subprocess, tmp_path как PROJECTS_BASE, mock docker не нужен — verb не трогает docker).
3. TRAP[DECISION] у verb-диспетчера: `Rejected: sftp-chroot юзер (второй ключ), git-pull проектов (deploy-keys на ноде, pull-based)` · `Reason: zero новых каналов/ключей, restrict сохранён` — решение подтверждено пользователем.
- **Acceptance:** `python -m pytest tests/test_contract_deploy_deliver.py -s -v` зелёный; существующие `tests/test_contract_deploy*.py` НЕ сломаны; `bash -n` OK.
- **Deps:** нет (контракт зафиксирован выше — параллельно с T1/T4).

### T3 — D2: CI payload step + документация delivery-модели (Coder, complexity 3)
Файлы: `.github/workflows/deploy-project.yml`, `AGENTS.md` (root), `tests/test_project_ci_contract.py` (проверка/минимальная правка).
1. Реализовать Contract 3 (шаг deliver ПЕРЕД deploy, тот же ключ/host-механизм).
2. В root `AGENTS.md` §«Два канала доставки» добавить строку: `Project payload | tar по SSH forced-command (platform-deliver) | Push (CI) | docker-compose.yml, ai-platform.yaml, .env.platform`.
3. Прогнать `python -m pytest tests/test_project_ci_contract.py -s -v`; если schema-тест reusable workflow ломается новым шагом — обновить тест (тест описывает контракт, контракт расширен осознанно).
- **Acceptance:** contract-тесты зелёные; YAML валиден (`python -c "import yaml,sys; yaml.safe_load(open('.github/workflows/deploy-project.yml'))"`).
- **Deps:** T2 (verb должен существовать до ссылки на него из CI).

### T4 — D3: adopt-project.sh валидация org/context/casing (Coder, complexity 4)
Файлы: `core/internal/scaffold/adopt-project.sh`, `tests/test_adopt_project_org_validation.py` (новый).
1. Реализовать Contract 4 (fail-fast, lowercase ghcr, exact-case uses:, сверка с node.yaml).
2. Тесты — 4 функции из $TEST_SPEC (bash subprocess, tmp_path-песочница с фикстурными ai-platform.yaml/node.yaml).
3. TRAP[BUG] у бывшего дефолта: root: молчаливый `personal` + отсутствие casing-нормализации породили drift dance-site (Debt D3).
- **Acceptance:** новый тест-файл зелёный; `make adopt-project` без ORG теперь падает с внятным сообщением (проверяется тестом, не вручную).
- **Deps:** нет.

### T5 — QA: полный gate + регресс (QA, complexity 2)
1. `python -m pytest tests/ -s -v` — весь suite.
2. `make gate MODE=fast` — должен быть зелёным.
3. Верификация LDD-трасс новых тестов (IMP:9 присутствует), сверка реализации с §Contracts 1-4.
4. VerificationReport → `.ai/plans/008-core-debt-fixes/{NN}-VerificationReport.md`.
- **Deps:** T1, T2, T3, T4.

## $PARALLEL_GROUPS

### Wave 1 (независимые, без общих файлов)
- Tasks: T1, T2, T4
- Command: `coder Read .ai/plans/008-core-debt-fixes/01-DevPlan.md, implement Wave 1: T1 | T2 | T4` (3 параллельных Coder)
### Wave 2
- Tasks: T3 (после T2)
### Wave 3
- Tasks: T5 (QA, после всех)

**Критический путь:** T2 → T3 → T5.

## Acceptance Criteria (сводная)

| # | Критерий | Проверка |
|---|----------|----------|
| A1 | ci_deploy_key доходит до step_6 из node.yaml | тесты T1; пустой ключ = старое поведение |
| A2 | platform-deliver доставляет whitelist-payload атомарно и безопасно | тесты T2 (traversal/oversize/whitelist) |
| A3 | CI workflow доставляет payload до деплоя | contract-тест T3 + YAML valid |
| A4 | adopt без org падает fail-fast; ghcr lowercase; uses: exact-case | тесты T4 |
| A5 | Ноль регрессий | полный pytest + `make gate MODE=fast` зелёные (T5) |

## File Manifest

| Файл | Изменение | Task |
|------|-----------|------|
| core/entrypoints/bootstrap.sh | извлечение ci_deploy_key, проброс в оба режима | T1 |
| core/internal/bootstrap/remote-cmd.sh | build_ssh_cmd: параметр ci_deploy_key | T1 |
| core/internal/bootstrap/node-lifecycle.sh | идемпотентный step: /opt/projects + chown | T1 |
| core/internal/deploy/deploy-project.sh | verb platform-deliver | T2 |
| .github/workflows/deploy-project.yml | шаг deliver-payload | T3 |
| AGENTS.md (root) | строка в таблицу каналов доставки | T3 |
| core/internal/scaffold/adopt-project.sh | fail-fast org, casing, сверка с node.yaml | T4 |
| tests/test_bootstrap_auto.py | +3 теста | T1 |
| tests/test_contract_deploy_deliver.py | новый, 5 тестов | T2 |
| tests/test_adopt_project_org_validation.py | новый, 4 теста | T4 |
| tests/test_project_ci_contract.py | правка при необходимости (новый шаг workflow) | T3 |

## $TEST_SPEC

| Test file | Test function | Scenario | Module under test |
|-----------|---------------|----------|-------------------|
| tests/test_bootstrap_auto.py | test_ci_deploy_key_extracted_from_node_yaml | node.yaml с ci_deploy_key → извлечение непустое; без ключа → пустая строка, не fatal | bootstrap.sh |
| tests/test_bootstrap_auto.py | test_build_ssh_cmd_includes_ci_deploy_key | непустой ключ → `--ci-deploy-key` в SSH-команде, %q-квотинг | remote-cmd.sh |
| tests/test_bootstrap_auto.py | test_build_ssh_cmd_empty_ci_deploy_key_omits_flag | пустой ключ → флага нет (backward compat) | remote-cmd.sh |
| tests/test_contract_deploy_deliver.py | test_deliver_valid_payload | tar.gz с whitelist-файлами → файлы в PROJECTS_BASE/<name>, DELIVER-SUCCESS в audit | deploy-project.sh |
| tests/test_contract_deploy_deliver.py | test_deliver_rejects_path_traversal | tar с ../evil или абсолютным путём → exit 1, PROJECT_DIR не изменён | deploy-project.sh |
| tests/test_contract_deploy_deliver.py | test_deliver_skips_non_whitelisted | tar с extra.sh → extra.sh НЕ извлечён, whitelist извлечён | deploy-project.sh |
| tests/test_contract_deploy_deliver.py | test_deliver_rejects_oversize | stream >1 MiB → exit 1 | deploy-project.sh |
| tests/test_contract_deploy_deliver.py | test_deliver_invalid_project_name | `platform-deliver ../x` → exit 1 | deploy-project.sh |
| tests/test_adopt_project_org_validation.py | test_adopt_fails_without_org | нет --org/PLATFORM_ORG/context → exit 1, IMP:10 в логе | adopt-project.sh |
| tests/test_adopt_project_org_validation.py | test_ghcr_path_lowercased | --org TronyxLab → IMAGE_NAME ghcr.io/tronyxlab/... | adopt-project.sh |
| tests/test_adopt_project_org_validation.py | test_uses_preserves_exact_case | --org TronyxLab → uses: TronyxLab/ai-platform/... | adopt-project.sh |
| tests/test_adopt_project_org_validation.py | test_context_mismatch_detected | node.yaml context=tronyx-lab vs --org расходятся по casing → WARN + node.yaml casing побеждает | adopt-project.sh |

## Design Decisions

### DD1 — Deliver-verb в forced-command (подтверждено пользователем)
`## @rationale` Q: почему не scp-шаг/sftp-юзер/git-pull? A: `restrict`+`command=` перехватывает и scp → голый scp-шаг через ci-deploy не работает; sftp-chroot = второй ключ и рост поверхности; git-pull = deploy-keys на ноде и инверсия push-модели. Verb по stdin: zero новых каналов и ключей, restrict сохранён, размер payload не ограничен SSH_ORIGINAL_COMMAND (данные идут по stdin, не в команде). Rejected: все три альтернативы выше.

### DD2 — /opt/projects создаётся bootstrap-шагом + mkdir -p в verb (defense in depth)
`## @rationale` Q: зачем оба? A: bootstrap задаёт ownership base-директории (ci-deploy) идемпотентно на init; verb гарантирует PROJECT_DIR даже на нодах, bootstrap'нутых до этого фикса (инвариант №9 — но живые ноды пересоздавать не обязательно).

### DD3 — Fail-fast вместо дефолта "personal"
`## @rationale` Q: почему не оставить дефолт? A: молчаливый дефолт — прямая причина Debt D3 (context: personal у реального проекта org tronyx-lab). Явный отказ дешевле часа диагностики CI. Rejected: автоопределение org через gh api — сетевой вызов в scaffold-скрипте, недетерминизм.

### DD4 — Backward compatibility как инвариант задачи
`## @rationale` Q: почему сигнатура build_ssh_cmd меняется, а не passthrough? A: явный позиционный параметр по образцу owner_key — симметрия двух ключей, один паттерн (Contract 1); все call-sites обновляются в T1 атомарно с верификационным grep. Invocation `<project> <ref>` и env PLATFORM_CI_DEPLOY_KEY остаются рабочими.

## §Change Impact (cascade >3 файлов)
Verb platform-deliver: deploy-project.sh → deploy-project.yml → AGENTS.md → test_contract_deploy_deliver.py → test_project_ci_contract.py (5 файлов, покрыто T2+T3).

## §Configuration DRY
`PROJECTS_BASE=/opt/projects` определён единожды (deploy-project.sh:39); T1-шаг bootstrap использует литерал `/opt/projects` — допустимо (разные хосты выполнения, bootstrap не source'ит deploy-project.sh). `## @rationale` Q: почему не общий конфиг? A: cross-script sourcing между bootstrap- и deploy-слоями создал бы связность слоёв ради одной константы; расхождение ловится тестом T2 (PROJECTS_BASE overridable env).

## Out of Scope
- Миграция tronyx-site из /opt/tronyx-lab/tronyx-site в /opt/projects (отдельная операционная задача).
- Пересоздание/ре-bootstrap живых нод (инвариант №9 — по требованию).
- Rename-логика GitHub-зеркал (закрыто в 007).

## Next Steps

### Wave 1
`Use coder role, read .ai/plans/008-core-debt-fixes/01-DevPlan.md, implement T1` · `... implement T2` · `... implement T4` (параллельно)
### Wave 2
`Use coder role, read .ai/plans/008-core-debt-fixes/01-DevPlan.md, implement T3`
### Wave 3
`Use QA role, read .ai/plans/008-core-debt-fixes/01-DevPlan.md, execute T5, write VerificationReport`

$END_DEVPLAN
