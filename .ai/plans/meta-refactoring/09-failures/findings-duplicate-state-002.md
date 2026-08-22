# Findings: corrupted state (S2) — pre-launch audit (part 2)

$ARTIFACT_CONTRACT
- PURPOSE: Аудит failure-modes частичного применения payload и неидемпотентных повторов (S2)
- SCOPE: research-only; ID FAIL-0706–0712; продолжение findings-duplicate-state.md
- REQUIRES: сценарий S2 — обрыв tar/копирования, битый compose, рассинхрон payload-набора, one-shot контейнеры, повторные hooks
- ACCEPTANCE: каждый finding отвечает на 9 вопросов аудит-протокола

## Сводка S2

Tar-распаковка идёт в staging и all-or-nothing (tarfile → TarError → FAILED до копирования,
receive_flow.py:316-325, 530-535) — обрыв stdin коррупции не даёт. Риск начинается на фазе
копирования в target_dir (set-атомарности нет) и в слоях поверх (snapshot/vhost/file_sd).
One-shot контейнеры и post-deploy hooks проверены и идемпотентны (FAIL-0712).

### FAIL-0706 · MED · Битый docker-compose.yml проходит pre-deploy L1 gate (YAML-parse = L2 warning) — первый деплой остаётся с битым payload без rollback
- scenario: в payload невалидный YAML / compose без services; pre-deploy gate пропускает, файлы копятся, compose up падает
- evidence: `core/internal/deploy/verify_contracts.py:275-277` — parse-fail → `_RawFinding("compose-config-valid", KLASS_L2, ...)`; `_severity_for` (:337-346) — L2 → block только при state=active-full; `docker compose config`-валидация вообще пропускается в l1_only (`if not l1_only:` :289-293). Следствие: `has_blocking_violation()` False → gate PASS (receive_flow.py:414-421)
- Q1: копирование прошло, DeployEngine.deploy падает на up/preflight
- Q3: существующий проект — engine perform_rollback восстанавливает прошлый образ (engine/lifecycle.py:86-116); ПЕРВЫЙ деплой — handle_first_deploy → PlatformFatalError (first_deploy.py:35-52), откат невозможен по определению
- Q4: broken state для первого деплоя: /opt/projects/<p> содержит битые файлы, контейнеров нет; для существующего — сервис на старой версии (приемлемо)
- Q5: retry после фикса коммитом безопасен
- Q6: CI красный; outage нет (кроме самого первого релиза проекта)
- Q7: alert — CI + Telegram critical (deploy-project.yml:381-398)
- Q8: исправить compose → re-push
- Q9 (fix, точечный): в l1_only-режиме поднимать «compose-config-valid» до block — это чисто статическая YAML-parse проверка без docker-латентности, противоречия с 176 A.2 нет
- confidence: high · action: рекомендуется до launch (одна строка политики severity)

### FAIL-0707 · MED · Snapshot пишется без compose_state — orchestrator-rollback мёртвый код; после успешного engine-rollback аудит сообщает FAILED «rollback failed»
- scenario: healthcheck/compose failure на существующем проекте → engine откатывает образ, orchestrator запускает ВТОРОЙ rollback из history-snapshot и ломает отчётность
- evidence: `core/internal/deploy/orchestrator.py:549-554` — create_snapshot вызывается БЕЗ compose_state → `snapshot["compose_state"]={}` (history.py:171); потребители `_apply_deploy`:495-506 → `_rollback_deploy`:593-629 → `_rollback_compose`:1147-1153 читают previous_image → None → docker_tag пропущен → engine.deploy(ref="previous-rollback") → pull несуществующего тега → False. Реальный откат к этому моменту уже сделан engine.perform_rollback (engine/lifecycle.py:86-116) внутри _deploy_compose (engine.py:223-243)
- Q3: пользовательский сервис восстановлен (старая версия), но статус FAILED + error «Compose deploy failed, rollback failed» (orchestrator.py:623-629)
- Q4: broken state аудита: ROLLED_BACK недостижим как статус; snapshot-история не содержит previous_image ни для одного деплоя
- Q6: оператор начинает ручное вмешательство на живом (уже откаченном) сервисе по ложному сигналу
- Q7: alert есть (CI red + Telegram critical), но семантика ложная
- Q8: убедиться по `make project-status`, что сервис на старом образе; вручную ничего не требуются
- Q9 (fix): пробросить ImageInfo из ServiceDeployResult в create_snapshot(compose_state={"previous_image": id}) ЛИБО пропускать второй rollback при rollback_performed=True
- confidence: high · action: после launch; до launch — задокументировать в runbook («FAILED при healthcheck-fail часто значит "откатился успешно"»)

### FAIL-0708 · MED · PARTIAL считается success → CI зелёный при unhealthy-сервисе (окно между двумя healthcheck'ами)
- scenario: контейнер healthy на engine.wait_health (max_wait=60s, engine.py:161), деградирует до второй проверки HealthcheckPoller → PARTIAL → exit 0
- evidence: `core/internal/deploy/orchestrator.py:556` (`else DeployStatus.PARTIAL`); `OrchestratorDeployResult.is_success` включает PARTIAL (orchestrator.py:151-153); receive_flow.py:568 `return 0 if result.is_success() else 1`; post-deploy notify шлёт severity=info при PARTIAL (post_deploy_chain.py:79-85 — critical только FAILED/ROLLBACK)
- Q1: двойной healthcheck подряд (engine + poller) с разными моментами истины
- Q4: тихая порча: деплой помечен успехом, сервис лежит
- Q7: alert — только платформенные alert-rules (up==0) с их задержками; deploy-канал молчит
- Q8: оператор обнаруживает по алертам мониторинга/жалобам
- Q9 (fix, 1 строка): PARTIAL → exit 1 в receive_flow.run ИЛИ critical-notify при PARTIAL
- confidence: high (код), вероятность низкая (узкое окно) · action: дешёвый фикс, взять в пачку

### FAIL-0709 · MED · render_vhosts: unlink-then-move окно и half-applied overlay при сбое посреди переноса; nginx reload-guard молчит (WARN)
- scenario: сбой (ENOSPC/EACCES) во время переноса отрендеренных conf → overlay-каталог с неполным набором; все последующие reload отказываются
- evidence: `core/internal/scaffold/vhost_renderer.py:953-975` — nginx -t на temp_dir OK, затем unlink ВСЕХ GENERATED *.conf overlay (:957-967) и shutil.move по одному (:971-975); «atomic mv» = удаление+перенос, не атомарная подмена. При битом наборе nginx_reload_hook.sh:41-48 откажет в reload (exit 1) → WARN non-fatal в post-deploy chain (post_deploy_chain.py:222-229)
- Q1: старый конфиг живёт в памяти nginx (сервис работает), но новые домены не активируются, а каталог неконсистентен
- Q3: само не восстановится до следующего успешного render-all
- Q4: broken state ДА (каталог)
- Q5: retry render-all безопасен (повторный all-or-nothing прогон)
- Q6: новые проекты/домены не резолвятся в vhost → 404/default backend
- Q7: alert НЕТ (hook exit 1 → WARN в логах ноды)
- Q8: `make render-vhosts NODE=<n>` — каноническое восстановление
- Q9 (fix, минимальный): переносить новые файлы ДО удаления старых (перезапись поверх), удалять только отсутствующие в новом наборе; либо swap через symlink-каталог
- confidence: high (код), вероятность low (render-all редок) · action: opportunistic

### FAIL-0710 · LOW · Prometheus file_sd target пишется не атомарно (truncate+write)
- scenario: Prometheus читает file_sd точно во время write_text → transient parse error
- evidence: `core/internal/monitoring/prometheus_targets.py:87` — `target_file.write_text(json.dumps(...))`; канонический atomic_writer существует (`core/internal/shared/atomic_writer.py`), здесь не используется. Конкурентные записи разных проектов — разные файлы, конфликта нет
- Q3: Prometheus ретраит чтение file_sd сам — self-healing
- Q7: alert нет (transient, лог prometheus)
- Q9 (fix): заменить на atomic_write_json — унификация с каноном
- confidence: high · action: hygiene

### FAIL-0711 · MED · Устаревший compose-файл переживает переименование в репо — нода молча использует СТАРЫЙ конфиг при зелёном CI
- scenario: проект переименовывает compose.yaml → docker-compose.yml (или наоборот); старый файл остаётся в target_dir навсегда и выигрывает резолв
- evidence: receive копирует ТОЛЬКО staging_files (receive_flow.py:424,443-460) — лишние файлы target_dir не удаляются; порядок резолва `COMPOSE_FILENAMES = (compose.yaml, docker-compose.yaml, docker-compose.yml, ...)` ставит compose.yaml ПЕРВЫМ (`core/internal/shared/compose_files.py:39-45`), тогда как payload-whitelist `(docker-compose.yml, compose.yaml)` (:49-53). Переименование compose.yaml→docker-compose.yml ⇒ старый compose.yaml продолжает определять стек
- Q1: новый файл доставлен, но resolve_compose_file выбирает старый → IMAGE_TAG/конфиг нового деплоя игнорируется стеком (compose up по старому файлу с новым тегом — частичное применение)
- Q4: broken state тихое и стойкое
- Q6: расхождение git ↔ нода без внешних признаков
- Q7: alert НЕТ
- Q8: ручное удаление лишнего файла на ноде
- Q9 (fix, маленький): в ReceiveFlow.deploy удалять канонические PROJECT_COMPOSE_FILENAMES, отсутствующие в staging; плюс warning в verify_contracts при наличии обоих имён
- confidence: high (механика подтверждена; частота сценария — HYPOTHESIS) · action: фикс рядом с FAIL-0701

### FAIL-0712 · LOW · Проверено-безопасно: one-shot контейнеры и post-deploy hooks идемпотентны при повторном run
- scenario: re-run bootstrap φ8/φ12, повторный deploy, двойной post-deploy chain
- evidence: minio-createbuckets — restart "no", `mc mb --ignore-existing` ×2 (`core/modules/minio/docker-compose.base.yml:76-99`) → повтор no-op; prometheus-config-init — детерминированный sed-рендер в named volume, перезапись тем же содержимым (`core/modules/monitoring/docker-compose.base.yml:47-...`); зависимые — condition: service_completed_successfully (канон modules/AGENTS.md). Hooks: nginx_reload_hook.sh — reload идемпотентен + nginx -t guard; generate-catalog/monitoring-reconfig — регенерация артефактов; notify — идемпотентен
- остаточный риск только КОНКУРЕНТНЫЙ запуск (не повторный) — покрыт FAIL-0700/0701/0702
- confidence: high · action: none (зафиксировано как verified-safe)

## Синтез S2 → launch-blockers

Прямых CRITICAL нет: tar-staging all-or-nothing + engine image-rollback закрывают худшее.
До launch рекомендованы дешёвые фиксы: FAIL-0706 (severity-строка), FAIL-0708 (exit-код),
FAIL-0711 (удаление stale compose-имён — вместе с lock-кластером S1). FAIL-0707 — runbook-заметка.
