# Failures: S1 migration failure / S2 rollback — pre-launch audit

- scenario: S1 = падение миграций при деплое (проектные + платформенные litellm/langfuse);
  S2 = фактический механизм healthcheck-rollback и restore-цепочка
- method: research-only, evidence = file:symbol + цитата; HYPOTHESIS помечен явно
- ID range: FAIL-0800–0899; companion: findings-migration-rollback-002.md
- Известное пересечение: PARTIAL→is_success=True уже зафиксировано другим агентом (FAIL-0102) — здесь не исследуется

## Контур деплоя (подтверждено кодом)

```
git push → deploy.yml проекта → reusable org/ai-platform/deploy-project.yml@main
  → tar czf - ai-platform.yaml [docker-compose.yml] [.env.platform] [practices.lock]
  | ssh ci-deploy@node "receive <project> <sha>"          # БЕЗ build/push образа!
  → ReceiveFlow: staging → os.replace → DeployOrchestrator.deploy
  → DeployEngine.deploy: save_previous_image → pull(IMAGE_TAG=<sha>, 5× backoff)
    → up_atomic → wait_health(docker inspect) → fail? → perform_rollback
```

Ключевой факт: **CI-канал не собирает проектный образ**. Workflow передаёт по forced-command
только конфиги (`deploy-project.yml:350-363`, FILES="ai-platform.yaml docker-compose.yml …");
образ `ghcr.io/<org>/<project>:${IMAGE_TAG:-latest}` должен существовать в registry ДО receive,
а `IMAGE_TAG=<github.sha>` подставляется в `up_atomic` (`engine/flow.py:79`,
env_override={"IMAGE_TAG": ref}). Единственный генератор build-job в репо —
`project_adopter.simplify_deploy_yml()` (project_adopter.py:181-241, docker/build-push-action,
tags sha+latest). Шаблоны new-project build-job НЕ содержат.

### FAIL-0801 · CRITICAL · Проекты из шаблона не имеют канала сборки образа — первый же деплой гарантированно падает на pull
- scenario: S2/S1 пограничный; новый проект через `make new-project` (канон единственного канала).
- evidence: `templates/template-backend/.github/workflows/deploy.yml` — единственный job
  `deploy: uses {{ORG}}/ai-platform/deploy-project.yml@main`, build/push отсутствует;
  то же в template-frontend. `ls .github/workflows/` платформы: core-deploy/deploy-project/
  hermes-nightly/mirror/platform-*/push-gate/security-scan — проектных образов нет;
  `docker/build-push-action` используется только platform-test.yml (+generated adopter).
  При этом `templates/template-backend/docker-compose.yml` ссылается
  `image: ghcr.io/{{ORG}}/{{PROJECT_NAME}}:${IMAGE_TAG:-latest}`, а engine пуллит
  строго `:<sha>` (`flow.pull_images` → retry_pull env IMAGE_TAG=ref).
  `engine/engine.py:218-220`: pull fail ×5 → `handle_first_deploy(...)` →
  PlatformFatalError exit 10 (lifecycle.py:123-134).
- 1) происходит: receive проходит, pull :<sha> падает 5× (~2 мин), first-deploy → fatal.
- 2) отказ: templates/template-backend/deploy.yml (нет build job) + engine/flow.py:pull_images.
- 3) авто-recovery: нет (first deploy — rollback невозможен by design).
- 4) broken state: payload на ноде обновлён (os.replace до deploy), контейнер остался старый/отсутствует.
- 5) retry безопасен: да (тот же фейл, идемпотентно).
- 6) user impact: ни один scaffolded backend/frontend проект не может задеплоиться вообще.
- 7) alert: CI red (единственный сигнал).
- 8) восстановление: вручную добавить build-push job в репозиторий проекта (как в
  project_adopter.py:194-222) и запустить push заново.
- 9) минимальный фикс до launch: добавить в оба шаблона build-push job (копия блока
  adopter'а) ИЛИ документировать adopter-generated workflow как обязательный post-scaffold шаг.
- confidence: high (цепочка подтверждена файлами; отсутствие любого билдера проверено grep'ом repo-wide).
- action: launch-blocker candidate №1.

### FAIL-0802 · HIGH · adopt-project генерирует вызов reusable workflow с несуществующим input `image_tag`
- scenario: S2; путь adopt-existing-project.
- evidence: сгенерированный yml (project_adopter.py:224-240) передаёт
  `with: project_name, image_tag: ${{ github.sha }}`; фактические inputs
  deploy-project.yml — ТОЛЬКО `project_name/node/host/org`
  (.github/workflows/deploy-project.yml:53-65, комментарий org: «kept for caller
  compatibility»).
- 1) происходит: GitHub Actions отвергает неизвестный input workflow_call при dispatch
  (HYPOTHESIS для runtime-поведения: validation error «Unexpected value»; сам факт
  расхождения схемы — evidence).
- 2) отказ: core/internal/scaffold/project_adopter.py:simplify_deploy_yml vs deploy-project.yml:workflow_call.inputs.
- 3) авто-recovery: нет.
- 4) broken state: проект «принят» (adopt-project зелёный), CI проекта всегда красный.
- 5) retry безопасен: да, детерминированный фейл.
- 6) user impact: adopted-проект не деплоится; оператор тратит время на разбор чужой ошибки схемы.
- 7) alert: CI red.
- 8) восстановление: удалить строку image_tag из deploy.yml проекта.
- 9) минимальный фикс: убрать `image_tag:` из генерируемого шаблона adopter'а
  (версию workflow берёт из github.sha сам) — 1 строка.
- confidence: high (расхождение схем), medium-high (интерпретация поведения GH).
- action: фикс до launch (trivial), проверить e2e на test-VPS вместе с FAIL-0801.

### FAIL-0803 · HIGH · Restore не соответствует задокументированному контракту: pre-restore снэпшот отсутствует, psql без ON_ERROR_STOP поверх живого кластера
- scenario: S2; DR после неудачной миграции/data corruption.
- evidence: контракт — `core/AGENTS.md:234` «страховка … nightly-дампы (RPO 24ч) +
  **pre-restore снэпшот в restore-таргете**». Фактическая цепочка: Makefile:restore
  (makefiles/modules.mk:90-99) → backup-cron/Makefile:44-53 (делегация) →
  postgres/Makefile:49-64:
  `gunzip -c "$(DUMP_FILE)" | docker exec -i postgres sh -c 'psql -U "$POSTGRES_USER" < /dev/stdin'`.
  Grep ON_ERROR_STOP/pre-restore по core/+makefiles — 0 совпадений (только doc-строка AGENTS.md).
- 1) происходит: дамп pg_dumpall льётся в ЖИВОЙ кластер (стек работает, приложения пишут);
  CREATE ROLE/DATABASE на существующие объекты дают ошибки, psql БЕЗ ON_ERROR_STOP
  продолжает и печатает их в stdout; таргет завершается «Restore complete» независимо.
- 2) отказ: core/modules/postgres/Makefile:restore (строки 59-63).
- 3) авто-recovery: нет; частичный restore выглядит успехом.
- 4) broken state: полусмешанное состояние (часть данных из дампа + записи приложений
  во время восстановления); откатить некуда — снэпшота нет.
- 5) retry безопасен: НЕТ — повторный прогон усугубляет смешение (idempotency дампа не гарантируется).
- 6) user impact: при DR — потеря консистентности вместо восстановления; RTO/RPO из AGENTS.md недостижимы.
- 7) alert: нет (локальная ручная операция).
- 8) восстановление: ручная процедура: остановить проектные контейнеры +
  `make down MODULES=<зависимые>` → dropdb/createdb → psql --single-transaction /
  ON_ERROR_STOP=1 → `make up`. Нигде не записана как runbook.
- 9) минимальный фикс до launch: (а) добавить `PGOPTIONS="-c ON_ERROR_STOP=1"` или
  `psql -v ON_ERROR_STOP=1`; (б) пре-шаг: `docker exec postgres pg_dumpall > pre_restore_$(ts).sql`
  (это и есть задокументированный снэпшот); (в) README-блок «остановить писателей перед restore».
- confidence: high.
- action: launch-blocker candidate №2 (DR-цепочка — последний рубеж, заявлен в каноне, не реализован).

### FAIL-0804 · MED · Rollback после health-fail не верифицируется повторным healthcheck
- scenario: S2; healthcheck нового образа красный → откат.
- evidence: `engine/engine.py:251-265` — `wait_health` fail → `perform_rollback(...)`
  → немедленный `return ServiceDeployResult(success=False, rollback_performed=...)`.
  Никто не поллит здоровье ОТКАЧЕННОГО контейнера. `lifecycle.perform_rollback:86-113`
  возвращает bool compose-up, не здоровье.
- 1) происходит: контейнер со старым образом стартует; если он тоже нездоров
  (например, схема БД уже мигрирована вперёд — см. FAIL-0806), результат всё равно
  rollback_performed=True.
- 2) отказ: engine/engine.py:256-265 (return сразу после rollback).
- 3) авто-recovery: частично — watchdog (10 мин unhealthy → docker restart) подхватит позже.
- 4) broken state: возможен «откаченный, но мёртвый» сервис, отчёты CI — FAILED (ок),
  но статус rollback вводит в заблуждение при разборе.
- 5) retry безопасен: повторный деплой — да.
- 6) user impact: продление downtime на время до watchdog-цикла (~10-15 мин).
- 7) alert: deploy burn-rate/telegram-notify есть (orchestrator audit); специализация нет.
  Пересечение с FAIL-0102 (PARTIAL→is_success) — упомянуто, не исследуется.
- 8) восстановление: `make status PROJECT=<p>` / `make healthcheck NODE=`; ручной рестарт.
- 9) минимальный фикс: после perform_rollback один `wait_health(service, max_wait)`
  и поле rollback_verified в результате (или лог IMP:9). ~10 строк.
- confidence: high.
- action: кандидат на quick-fix; не блокер.

### FAIL-0805 · MED · Платформенные auto-миграции (litellm Prisma, langfuse Prisma+ClickHouse) при фейле не блокируют node-update и чинятся только generic-алертами
- scenario: S1; обновление платформенных образов (context-promote / node-update φ12).
- evidence: litellm мигрирует Prisma при старте контейнера —
  `core/modules/litellm/docker-compose.base.yml:131-135` («verified Prisma migrate 45-55s»),
  DATABASE_URL через pgbouncer (:75); langfuse — `CLICKHOUSE_MIGRATION_URL`
  (`langfuse/docker-compose.base.yml:56`) + memory-комментарий «423 Prisma + ClickHouse
  migrations» (:9). Severity: `litellm/module.yaml:severity: normal`, langfuse: normal —
  D5-контракт «deploy failure НЕ блокирует node-update» (только postgres critical → exit 2).
- 1) происходит: новая версия litellm/langfuse стартует, миграция падает → контейнер
  unhealthy/crash-loop; deploy_orchestrator фиксирует warning, node-update завершается success.
- 2) отказ: deploy/deploy_orchestrator (severity-normal ветка) + compose healthcheck модулей.
- 3) авто-recovery: watchdog рестартит до RestartCount≤5, дальше сдаётся (watchdog.py:82-90).
- 4) broken state: схема может быть частично мигрирована (Prisma миграции поштучно
  транзакционны, но набор файлов — нет; HYPOTHESIS для langfuse CH-миграций);
  предыдущий образ платформенных модулей НЕ восстанавливается автоматически
  (rollback-механизм есть только у проектов, не у модулей φ12).
- 5) retry безопасен: повторный node-update — да, но воспроизводит тот же упавший migrate.
- 6) user impact: LLM-gateway/tracing лежат для всех проектов; платформа считает себя обновлённой.
- 7) alert: ServiceDown (CRITICAL, alert-rules.yml:правило 1) + ServiceDownShort (WARNING);
  специализированного алерта на failed migration нет.
- 8) восстановление: `cd core/modules/litellm && make restart-hard` после отката тега в
  compose/env (ручной downgrade образа через CONTEXT_IMAGE/digest-pin правку).
- 9) минимальный фикс: runbook «downgrade платформенного модуля» (точные команды правки
  digest-pin + restart-hard) + решение о severity=critical для litellm.
- confidence: high (конфиги/севьерити), medium (поведение Prisma при частичном фейле — HYPOTHESIS).
- action: документация + осознанное решение по severity; код не трогать до launch.
