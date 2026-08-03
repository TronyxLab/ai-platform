# 128-debt-python-refactor — 01-Brief.md

$START_BRIEF

$ARTIFACT_CONTRACT
PURPOSE:               Закрыть python-рефакторинг-долги реестра .ai/debt/001: P2-5/D6 (Docker operations → shared module), D3 (5 test-side failures docker_orchestrator), D1/D2 (doc_header_validator нереализуемые проверки), D7 (3 inline-python3 whitelist-записи), D8 (gen_env_platform CLI-first), D10 (S3 timeout не в boto3 Config), D12-postgres (healthcheck.sh hardcoded names), nginx dual mechanism (config/dev-config), manifest.mk dead-комментарий, jsonschema TRAP[DEBT] HI (FIXED RC-сессией — снять), D9 (FIXED 118 E11 — снять).
DESCRIPTION:          5 волн: W1 — shared/docker_ops.py (дедупликация docker-операций deploy_engine + docker_orchestrator + lib/docker.sh, P2-5/D6). W2 — фикс D3 (5 test-side failures после W1). W3 — doc_header_validator D1/D2 (синхронизация манифеста с кодом). W4 — D7 (3 извлечения inline python3 в Python-модули). W5 — мелкие фиксы D8/D10/D12-hc/nginx-dual/manifest.mk + снятие stale-долгов jsonschema/D9.
RATIONALE:             Дублирование docker-операций (3 копии) — drift-акселератор (паттерн «extract when consumers > 3», как SSH_OPTS 116 B5). Нереализуемые проверки в манифесте лгут (namespace_collision_names) — манифест обязан отражать код. Inline python3 в shell — Tier-1 Strangler-триггер (языковая политика). Часть записей уже FIXED волнами (jsonschema — python_deps.py Step 1b RC-сессия; D9 — shared/project_yaml.py 118 E11) — требуется только снятие TRAP.
ACCEPTANCE_CRITERIA:   (1) shared/docker_ops.py — единственный источник docker-операций (гейт docker_sole_path или аналог; 0 дубликатов в deploy_engine/docker_orchestrator/docker.sh). (2) test_docker_orchestrator.py — 0 failures. (3) doc_header_validator: манифест и код совпадают (namespace_collision_names реализован ИЛИ удалён из манифеста; check_file_lines/check_shellcheck_directives удалены из описаний). (4) 3 inline-python3 whitelist-записи закрыты (извлечены в Python, whitelist пуст/сокращён). (5) Мелкие фиксы с unit-тестами; stale TRAP[DEBT] (jsonschema, D9) сняты с пометкой FIXED. (6) make check + gate зелёные.
IMPLEMENTS:            Решение пользователя 2026-08-03 (закрыть все известные долги); P2-5 из .ai/debt/001 (Rev 2026-09-30); записи D1-D12 реестра.
IMPACTS:               core/internal/deploy/deploy_engine.py, core/internal/bootstrap/deploy/docker_orchestrator.py, core/lib/docker.sh, core/internal/shared/docker_ops.py (NEW), core/internal/lint/doc_header_validator.py, core/entrypoint-manifest.yaml, core/internal/hooks/check-no-new-inline-python3.sh, core/internal/scaffold/project_adopter.py, core/internal/scripts/gen_env_platform.py, core/modules/backup-cron/scripts/s3_client.py, core/modules/postgres/healthcheck.sh, core/modules/nginx/config/nginx.conf, makefiles/manifest.mk, core/internal/scripts/jsonschema_validate.py, tests/ (unit-тесты).
REQUIRES:              Нет внешних. Бейзлайн: make check зелёный до старта.
$END_ARTIFACT_CONTRACT

## Scope (закрываемые долги)

| # | Долг | Суть | Действие |
|---|------|------|----------|
| 1 | P2-5/D6 | Docker operations в 3 копиях (deploy_engine:81, docker_orchestrator, docker.sh) | W1: shared/docker_ops.py |
| 2 | D3 | 5 test-side failures в test_docker_orchestrator.py | W2: фикс после W1 |
| 3 | D1/D2 | doc_header_validator: check_file_lines/check_shellcheck_directives не существуют; namespace_collision_names не реализуется | W3: синхронизация манифеста с кодом |
| 4 | D7 | 3 whitelist-записи inline python3: generate-catalog.sh heredoc, adopt-project.sh JSON, add-vhost.sh duplicate domain | W4: извлечение в Python |
| 5 | D8 | gen_env_platform.py CLI-first (sys.exit) → subprocess.run overhead | W5: main() → импортируемая функция |
| 6 | D10 | s3_client.py:64 S3 timeout не в boto3 Config | W5: boto3 Config (connect/read timeout) |
| 7 | D12-postgres | postgres/healthcheck.sh:15 hardcoded container names (-test stack непригоден) | W5: параметризация/переменные |
| 8 | nginx-dual | nginx.conf:106 config/ и dev-config/ дублируют vhost-топологию | W5: консолидация или явный keep |
| 9 | manifest.mk | generate-manifests-atomic dead (118 B4) — TRAP[DEBT] комментарий | W5: снять комментарий |
| 10 | jsonschema HI | TRAP[DEBT] 2026-08-01 — решён python_deps.py Step 1b (RC 121) | W5: снять TRAP как FIXED |
| 11 | D9 | node.yaml path duplicated — решён 118 E11 (shared/project_yaml.py) | W5: снять TRAP |

## Non-Goals

- Не трогаем issue-cert.sh / stable libs (keep-решения, DevPlan 127).
- Не мигрируем docker.sh целиком на Python — только дедупликация операций.
- Не меняем контракты CLI (exit-коды, аргументы) — только внутренняя структура.

$END_BRIEF
