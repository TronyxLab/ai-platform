# 02-DevPlan — Бриф A: единый deploy-канал + bootstrap-консистентность

$ARTIFACT_CONTRACT
- PURPOSE: Реализация задач 1–8 программного брифа 117 — устранение CRITICAL-расхождения forced-command (K1) и консистентность bootstrap-конвейера ПЕРЕД ручным тестированием на пересозданном tronyx-vps.
- DESCRIPTION: 8 задач: (1) единый ci-deploy forced-command dispatch, (2) дубль docker_registry_auth φ3/φ6, (3) дубль cert_orchestrator, (4) мёртвый issue-cert.sh main(), (5) state-machine resume/WARN-маскировка, (6) preflight при done, (7) exec ssh в bootstrap.sh, (8) проглоченные ошибки.
- RATIONALE: K1 (receive vs dispatch) гарантированно ломает `make deploy` через CI на новых нодах — блокирует ручное тестирование волны. Без добавления функционала — только унификация и честность конвейера.
- ACCEPTANCE_CRITERIA:
  - AC-A1: на свежей ноде authorized_keys ci-deploy = `orchestrator_cli dispatch` (единственный писатель — users.py).
  - AC-A2: `docker_registry_auth.py` выполняется 1 раз за init; `systemctl restart docker` — 0 раз или 1 раз с guard.
  - AC-A3: cert_orchestrator второй вызов — no-op при валидных сертификатах (≥30 дней).
  - AC-A4: issue-cert.sh без main() (удалён мёртвый код), executor работает.
  - AC-A5: WARN-фаза не маскируется под done; при повторном init — перевыполняется; current_step честный.
  - AC-A6: preflight пропускается при всех done-фазах.
  - AC-A7: bootstrap.sh использует lib/ssh.sh; ошибки парсинга node.yaml логируются.
  - AC-A8: `make gate MODE=fast`, `make check-manifests` зелёные; unit/e2e-тесты обновлены (dispatch-ожидания).
- IMPLEMENTS: 117 01-Brief задачи 1–8.
- IMPACTS: core/internal/bootstrap/lifecycle/{phases.py,state_machine.py,cli.py,helpers/users.py}, core/internal/bootstrap/{setup-node.sh,node-lifecycle.sh,issue-cert.sh,docker_registry_auth.py}, core/entrypoints/bootstrap.sh, core/internal/bootstrap/deploy/context_deployer.py, AGENTS.md (TRAP B8 D2), tests/ (unit lifecycle, e2e).
- REQUIRES: 117 01-Brief (реестр), результаты аудита bootstrap-конвейера 2026-08-01.

---

## 1. Технический анализ и решения

### Задача 1 (CRITICAL) — единый forced-command `dispatch`

**Факты (верифицированы):**
- φ2 (phases.py:243-257): `helpers_users.add_ssh_key("ci-deploy", key, forced_command_prefix='command="python3 -m core.internal.deploy.orchestrator_cli receive",restrict')` — пишет receive.
- φ3 (setup-node.sh:97-119): `add_ci_deploy_command` пишет dispatch, но только если ключа ещё нет (`grep`-guard, setup-node.sh:69) → при порядке φ2→φ3 receive побеждает.
- setup-node.sh также создаёт пользователей (create_user platform/ci-deploy, add_owner_key) — дублирует φ2 (no-op по id-guard).
- orchestrator_cli.py: dispatch-режим существует и принимает verb из SSH_ORIGINAL_COMMAND (B1); receive — legacy, игнорирует SSH_ORIGINAL_COMMAND (U-04).

**Решение D1:** единственный писатель ci-deploy ключа — Python users.py (φ2). `forced_command_prefix` → `dispatch`. Из setup-node.sh удалить create_user/add_owner_key/add_ci_deploy_command (дубли φ2); оставить generate_sudoers (уникальная реализация, visudo -c + atomic mv). Обновить docstring users.py:12 и TRAP B8 D2 в root AGENTS.md: текст — фактическое решение («канонический паттерн — Python lifecycle φ2 → dispatch; setup-node.sh — только sudoers»); Rev-условие (B1) снять.

**Файлы:** phases.py:256, users.py:12, setup-node.sh:86-119 (+ create_user/add_owner_key секции), AGENTS.md TRAP, тесты unit/lifecycle (users/ssh-key ожидания).

**Риск:** LOW. Затрагивает только новые ноды (текущих проектов нет).

### Задача 2 (HIGH) — дубль docker_registry_auth φ3+φ6

**Факты:** φ3 (phases.py:308-321) вызывает docker_registry_auth.py при наличии файла; φ6 (phases.py:518) — при наличии DOCKER_HUB_USERNAME/TOKEN. Скрипт делает `_restart_docker()` при каждом вызове (docker_registry_auth.py:188) → 2 рестарта daemon за init.

**Решение D2:** Docker Hub auth — ТОЛЬКО в φ3 (ранний, до pull). В φ6 убрать вызов docker_registry_auth.py (оставить GHCR-часть). В docker_registry_auth.py добавить guard: restart docker только если запись auth отсутствовала в ~/.docker/config.json ДО login (сравнить до/после), повторный login без изменения — без restart. Идемпотентность: второй вызов (любой) = no-op.

**Файлы:** phases.py:518 (убрать Docker Hub блок), docker_registry_auth.py:188 (guard restart).

**Риск:** MED. Проверить фактическую структуру docker_registry_auth.py перед правкой (auth-механизм: docker login vs config.json vs systemd drop-in).

### Задача 3 (MED) — cert_orchestrator ×2

**Факты:** φ7 — issuance всех доменов; φ8 → deploy_context → context_deployer.py:691 — повторный вызов cert_orchestrator (идемпотентен по disk-check, но повторяет S3/openssl-проходы).

**Решение D3:** в context_deployer.py перед вызовом cert_orchestrator — skip, если все домены контекста имеют валидные сертификаты (переиспользовать критерий issue-cert.sh verify expiry ≥30 дней, через существующую Python-валидацию s3_ssl_cache/_validate_cert или cert_orchestrator). Лог [IMP:9] skip + причину.

**Файлы:** context_deployer.py:691.

**Риск:** LOW (второй вызов и без того идемпотентен; правка — оптимизация).

### Задача 4 (MED) — issue-cert.sh main()

**Факты (пересмотрены при реализации — аудит был ошибочен):** исходный аудит утверждал «main() — мёртвый код, 0 вызывающих» и DevPlan D4 планировал удаление. Верификация при реализации показала: **main() — ЖИВОЙ executor**. `issue-cert.sh` заканчивается `main "$@"` (хвост файла), а cert_orchestrator.py:451 вызывает `bash issue-cert.sh` без аргументов (env: PLATFORM_DOMAIN + ACME_CHALLENGE_MODE) — т.е. main() выполняется при КАЖДОМ вызове скрипта. Подтверждение: tests/test_nginx_acme.py:40-60 `_source_and_run_issue_cert_no_main()` явно вырезает `main "$@"`, чтобы тот не выполнился. Удаление main() = `bash issue-cert.sh` станет no-op с exit 0 → cert_orchestrator молча пометит «issued», не выпустив сертификат (нарушение Fail-Fast и AC-A4).

**Решение D4 (отклонение от плана, зафиксировано 2026-08-01):** main() СОХРАНЁН как живой executor. Удалены только устаревший TRAP[DECISION] «CLI debug entrypoint» (issue-cert.sh:698-703) и вводящий в заблуждение фрейминг; region переписан под «EXECUTOR ENTRY — вызывается ТОЛЬКО cert_orchestrator.py:451 (subprocess bash, timeout=300)». В файле — блок «⚠️ ОТКЛОНЕНИЕ от DevPlan 117 D4» с доказательствами. Внутренние функции не тронуты (TRAP: shell subprocess by design, порт в Python отложен до стабилизации API ≥6 мес).

**Файлы:** issue-cert.sh (TRAP + фрейминг).

**Риск:** LOW. Открытый вопрос на решение Архитектора: полный Python-порт issuance (по TRAP cert_orchestrator.py:435) — вне скоупа волны (без нового функционала).

### Задача 5 (MED) — state-machine: WARN-маскировка и resume

**Факты:** execute_grouped_phase (state_machine.py:214-221) — мёртвый (0 production-caller, TRAP[DEBT]); фаза, вернувшая False (non-fatal WARN), помечается done (execute_phase игнорирует результат, state_machine.py:543-548) → 2-й запуск SKIPит фазу с предупреждениями; current_step всегда 0 (state_machine.py:798-808, cli.py:240-245, TRAP[BUG] — спасает setdefault).

**Решение D5:**
- WARN-семантика: фаза с non-fatal issues получает статус `done_with_warnings` (сохранить warnings в state). При проверке done — `done_with_warnings` НЕ считается done → фаза перевыполняется на следующем init (после успеха без WARN — статус done). Это чинит молчаливые пропуски (например, не настроенный DNS-плагин acme).
- current_step: обновлять при успешном выполнении фазы (индекс следующей фазы); при фейле — сохранять текущий для resume-диагностики. Убрать TRAP[BUG] (семантика становится честной).
- execute_grouped_phase: удалить (мёртвый код) + снять TRAP[DEBT] с фиксацией решения: sub-step resume вне скоупа волны (без нового функционала).

**Файлы:** state_machine.py:214-221, 543-548, 798-808; cli.py:240-245; state_store.py (формат статуса).

**Риск:** MED. Идемпотентность bootstrap-инварианта №6 (2-й вызов = no-op INIT) не нарушается: no-op — для успешно выполненных фаз; WARN-фазы по определению не завершены успешно. Обновить unit-тесты state_machine.

### Задача 6 (MED) — preflight при всех done

**Факты:** node-lifecycle.sh:60-64 — preflight.py выполняется при каждом init, даже если state.json показывает все фазы done.

**Решение D6:** preflight — только если есть pending-фазы (state_machine знает статус): в cli.py после загрузки state — если все фазы done → пропустить preflight с логом [IMP:9] «all phases done — preflight skipped». При наличии pending/WARN — выполнять.

**Файлы:** cli.py (main-диспетчер), node-lifecycle.sh:60-64 (или перенести вызов preflight в cli.py — решение по месту реализации).

**Риск:** LOW.

### Задача 7 (LOW) — bootstrap.sh:147 exec ssh

**Факты:** bootstrap.sh:147 — прямой `exec ssh` в обход lib/ssh.sh → флаги вне SSH_OPTS SoT (риск расхождения).

**Решение D7:** заменить на `source lib/ssh.sh` + `ssh_exec` (или передать SSH_OPTS через `python3 -m core.internal.shared.ssh_opts --shell`), сохранив DRY_RUN-семантику. Проверить, что bootstrap.sh — единственный entrypoint с прямым ssh (rg "exec ssh" по entrypoints).

**Файлы:** core/entrypoints/bootstrap.sh:147.

**Риск:** LOW.

### Задача 8 (LOW) — проглоченные ошибки

**Факты:** bootstrap.sh:73 `node_yaml --get-many 2>/dev/null || true`; node-lifecycle.sh:53 `node_yaml --get ... 2>/dev/null || echo "false"`.

**Решение D8:** stderr не глотать: логировать ошибку через lib/logging.sh log_imp + продолжать с fallback-значением ТОЛЬКО при реальной ошибке (отличать «ключ отсутствует» от «файл не читается»). firewall.sh:76 `disable || true` — легитимный best-effort, задокументировать комментарием (не менять).

**Файлы:** bootstrap.sh:73, node-lifecycle.sh:53.

**Риск:** LOW.

## 2. Порядок реализации

1. **D1** (критично) → правки + тесты + TRAP-обновление.
2. **D2, D3, D4** (bootstrap-путь) → правки + тесты.
3. **D5, D6** (state machine) → правки + unit-тесты.
4. **D7, D8** (entrypoint) → правки.
5. Финальный прогон: `make gate MODE=fast`, `make check-manifests` (entrypoint-manifest не меняется — цепочки не трогаем, это бриф E), точечные unit/e2e на lifecycle.

## 3. Критерии приёмки (повтор из контракта)
- AC-A1..AC-A8 (см. $ARTIFACT_CONTRACT).
- Дополнительно: `rg -n "receive" core/internal/bootstrap/` — 0 совпадений кроме легитимных (orchestrator_cli.py receive verb, docstring history).

## 4. Риски и решения
| Риск | Митигация |
|------|-----------|
| Изменение статус-семантики state (D5) сломает e2e bootstrap dry-run | Обновить тесты; e2e на test-VPS после волны (AC3 программы) |
| D2: docker auth механизм несовместим с guard | Прочитать docker_registry_auth.py перед правкой; guard по фактическому механизму |
| Удаление main() issue-cert сломает скрипты, вызывающие его с аргументами | rg по репо: подтверждено 0 вызовов; тесты проверить |
| Задача 5 трогает идемпотентность (инвариант 6) | Идемпотентность сохранена для успешных фаз; WARN ≠ done задокументировать в phases.py docstring |

## 5. Оценка
- Изменяемые файлы: ~10 core-файлов + AGENTS.md TRAP + ~5 тест-файлов.
- Трудозатраты: ~0.5-1 день агент-времени. Размер: STANDARD (9-20 файлов, бизнес-логика) → только DevPlan.
