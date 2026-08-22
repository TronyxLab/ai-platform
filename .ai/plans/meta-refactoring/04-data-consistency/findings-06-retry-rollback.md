# Retry & rollback audit

Метод: статический аудит retry-путей (`shared/retry.py` + 4 потребителя + 3 ad-hoc цикла) и rollback-цепочки
(`DeployEngine.deploy` → `perform_rollback`; `DeployOrchestrator._apply_deploy/_rollback_deploy/rollback`;
`parallel_runner.deploy_docker_group`). Каждый вывод подтверждён file:line; make test/gate/check не запускались.

## Инвентарь: операция × идемпотентна? × под retry?

| Операция | Идемпотентна | Под retry | Где |
|---|---|---|---|
| `ssh receive <project> <sha>` (полный деплой) | НЕТ (snapshots, telegram, compose-циклы) | ДА ×3 | channels/base.py:190 |
| docker compose pull | Да | Да ×5, backoff [5,10,20,40,60] | engine/flow.py:54 |
| apt-get update/install | Практ. да | Да ×2 | lifecycle/helpers/system.py:184 |
| Фазы bootstrap φ1–φ13 целиком | Канон «идемпотентен», для φ7 (acme issue) — квота LE | Да ×2 | state_machine.py:377,714 |
| acme.sh issue | НЕТ (rate-limit LE) | Да (свой цикл) | bootstrap/issue_cert.py:393 |
| Telegram notify | Throttle (event,fingerprint) 3600s — дедуп одинаковых | Косвенно (через retry канала) | notifications.py:532–546 |
| orchestrator_cli deploy-many | Мутация | НЕТ (timeout → молча (0,[])) | deploy_orchestrator.py:607 |
| healthcheck-поллинг | Read-only | Циклы ожидания | docker_compose.py:520 |

SSH-мутации под retry в deploy/bootstrap: единственный случай — `_retry_deliver` (DATA-601); core_deliverer/
remote_executor/context_overlay retry-циклов не имеют. Двойные алерты системно не выявлены (throttle гасит
повторы того же fingerprint; остаточный риск — разные status DEPLOYED/PARTIAL → разные fingerprint, см. DATA-602).

## DATA-601: _retry_deliver повторяет удалённую мутацию receive при неоднозначном сбое транспорта
- **Severity:** HIGH · **Confidence:** high
- **Files:** core/internal/deploy/channels/base.py, channels/forced.py · **Symbols:** `DeliveryChannel._retry_deliver`, `ForcedCommandChannel.deliver/_send_forced`, `DeployOrchestrator.deploy` · **Invariant:** retry допустим только для идемпотентных операций / «не выполнено»
- **Violating scenario:** START: ssh execute `receive p sha1` (timeout=DEPLOY_TIMEOUT=900s покрывает ВЕСЬ remote-пайплайн unpack→compose up→health→snapshot→notify) → сбой: обрыв связи после выполнения на VPS / таймаут при ещё работающем remote (subprocess.run убивает только локальный ssh, receive-процесс осиротел и довершает мутацию) → `_receive_reply`: success=False → END: retry через [5,10] повторяет полный receive: второй compose-up-цикл, дубли snapshot'ов и telegram; если attempt-1 ещё держит flock — attempt-2/3 падают «Concurrent deploy blocked» → CI красный при фактически успешном деплое.
- **Evidence:** base.py:190–196 (`retryable=lambda r: not r.success`, attempts=3) · forced.py:141 (`timeout=self.timeout` вокруг всей remote-работы) · orchestrator.py:295–314 (lock timeout=0 → FAILED)
- **Impact:** at-least-once семантика у POST-подобной операции; двойные side-effects; ложный CI-fail.
- **Minimal fix:** удалённый idempotency-key: receive начинает с проверки «last healthy snapshot.version == sha → вернуть кэш-успех»; либо ретраить только connect/pre-exec фейлы.
- **Required test:** fake-runner: первый вызов «timeout после отправки stdin», второй — успех → assert ≤1 полная мутация или dedup-ответ.
- **Phase:** deploy-канал (CI deliver / deploy-project)

## DATA-602: PARTIAL (нездоров) трактуется как success: exit 0 в CI, telegram «задеплоено», rollback не запускается
- **Severity:** HIGH · **Confidence:** high
- **Files:** core/internal/deploy/orchestrator.py, deploy/receive_flow.py · **Symbols:** `OrchestratorDeployResult.is_success`, `DeployOrchestrator._verify_deploy`, `ReceiveFlow.run` · **Invariant:** нездоровый деплой не должен репортиться как успех
- **Violating scenario:** START: engine.wait_health прошёл (контейнер running без healthcheck = healthy), затем контейнер crash-loop'ится / docker-ps name-substring поймал чужой контейнер → poller.poll_until_healthy → unhealthy/timeout → END: result_status=PARTIAL → is_success()=True → post_deploy_chain шлёт telegram+monitoring reconfig, run() возвращает 0 → CI зелёный, нездоровый образ остаётся running, fix-forward никогда не инициируется (отказ невидим).
- **Evidence:** orchestrator.py:556 (`PARTIAL`), :151–153 (is_success ∈ {DEPLOYED,PARTIAL,SKIPPED}) · receive_flow.py:559 (chain при is_success), :568 (`return 0 if result.is_success()`)
- **Impact:** silent-degraded production; ложный положительный сигнал деплоя; аудит пишет PARTIAL, но алёрт — «deployed».
- **Minimal fix:** PARTIAL → exit≠0 (+ опционально trigger rollback); notify severity=warning со статусом healthcheck.
- **Required test:** fake-poller возвращает timeout → assert exit 1, post-chain не вызвана (или warning-event).
- **Phase:** receive (forced-command), deploy-project

## DATA-603: Периметр rollback = образ+payload: «старый код + новое состояние» вне периметра
- **Severity:** HIGH · **Confidence:** high
- **Files:** core/internal/deploy/engine/lifecycle.py, deploy/orchestrator.py, core/AGENTS.md · **Symbols:** `perform_rollback`, `_restore_payload_files`, `run_post_deploy_chain` · **Invariant:** rollback должен иметь задокументированный периметр состояния
- **Violating scenario:** START: receive v2 → compose up → entrypoint v2 прогоняет миграции (ALTER TABLE), init-контейнеры пишут volumes, monitoring reconfig применился (в PARTIAL-ветке цепочка успевает ДО фиксации результата) → health fail → END: perform_rollback возвращает только образ (re-tag + up --force-recreate); T9.8 восстанавливает payload-файлы (compose/.env.platform/ai-platform.yaml) — итого НЕ откатываются: (1) схема БД после миграций entrypoint (canon fix-forward, core/AGENTS.md:231–233), (2) данные volumes (down без -v сохраняет; запись v2 осталась), (3) monitoring/status-page конфиги от reconfig, (4) уже отправленные notify, (5) DeployHistory-снимок неудачного деплоя. v1 код работает против схемы/данных v2 → риск crash-loop (unknown column) — принят каноном только для миграций; пункты 2–5 в каноне не перечислены.
- **Evidence:** lifecycle.py:86–113 (rollback = tag+up) · orchestrator.py:1094–1121 (payload restore — единственное восстановление файлов) · core/AGENTS.md:231–233
- **Impact:** недокументированные формы несогласованного END-состояния; эксплуатационные сюрпризы при первом же реальном rollback.
- **Minimal fix:** зафиксировать периметр в core/AGENTS.md §fix-forward (volumes/monitoring/notify) + expand-contract требование к миграциям проектов.
- **Required test:** e2e: деплой с миграцией + принудительный health-fail → assert документированное END-состояние (v1 поднят, схема новая — known-behavior тест).
- **Phase:** канон + verify_contracts

## DATA-604: Snapshot'ы оркестратора никогда не содержат compose_state → snapshot-rollback структурно нерабочий
- **Severity:** HIGH · **Confidence:** high
- **Files:** core/internal/deploy/orchestrator.py, deploy/audit/history.py, engine/lifecycle.py · **Symbols:** `_verify_deploy`, `create_snapshot`, `latest_snapshot`, `_rollback_compose` · **Invariant:** rollback по snapshot обязан ссылаться на предыдущий рабочий образ
- **Violating scenario:** START: единственный caller create_snapshot (orchestrator.py:549) НЕ передаёт compose_state → history.py:171 кладёт `{}` → latest_snapshot.compose_state.previous_image всегда None → (а) `_rollback_compose` пропускает re-tag и делает engine.deploy(ref="previous-rollback"): тег `{service}:previous-rollback` существует только для dangling-образов (lifecycle.py:66–69), pull локального тега из registry фейлится ×5 (~2.5 мин, flow.py:54–61) → handle_first_deploy → PlatformFatalError (engine.py:218–220) → caught → FAILED; (б) публичный `rollback(project)` без snapshot_id целится в тот же пустой snapshot → всегда FAILED. Двойной rollback: после каждого health-fail engine УЖЕ успешно выполнил perform_rollback (engine.py:256), оркестраторная ветка (orchestrator.py:494–506) запускает второй, заведомо падающий проход → статус FAILED вместо ROLLED_BACK, аудит/CI сигналят «rollback failed» при фактическом успехе, +2.5 мин удержания deploy-lock.
- **Evidence:** orchestrator.py:549–554 (без compose_state), :1147–1160 (prev_image_id=None → deploy previous-rollback), :1161–1164 (PlatformFatalError⊂PlatformError → False) · history.py:171,333–345 · lifecycle.py:66–69
- **Impact:** ручной rollback API мёртв; статус-машина ROLLED_BACK/FAILED недостоверна; каждый health-fail тянет лишние ~2.5 мин pull-ретраев под lock.
- **Minimal fix:** писать compose_state (previous_image id/tag) в create_snapshot из engine-результата; `_apply_deploy` различать «engine already rolled back» (не запускать второй проход); rollback выбирать последний snapshot с health_status="healthy".
- **Required test:** unit: health-fail путь → assert ровно один rollback-проход и статус ROLLED_BACK; integration: manual rollback после двух деплоев восстанавливает образ N-1.
- **Phase:** deploy/orchestrator + audit/history

## DATA-605: Healthcheck-окна: преждевременный rollback при медленном старте; до ~21 мин ожидания под lock; substring-фильтр ловит чужие контейнеры
- **Severity:** MEDIUM · **Confidence:** high
- **Files:** core/internal/deploy/engine/engine.py, engine/flow.py, deploy/healthcheck_poller.py, shared/docker_compose.py · **Symbols:** `wait_health`, `deploy_compose(max_wait=60)`, `poll_until_healthy`, `healthcheck_poll` · **Invariant:** решение rollback принимается после исчерпания разумного окна старта; окно конечно
- **Violating scenario:** (a) START: сервис стартует >60s (миграции в entrypoint) → END: wait_health(60s) исчерпан → perform_rollback убивает потенциально здоровый релиз (premature rollback, лишний fix-forward). Обратной гонки «бесконечное ожидание» нет — все окна ограничены. (b) Worst-case orchestrator-поллера: 20 итераций × (docker-poll 60s + sleep 3s) ≈ 21 мин удержания deploy-lock при crash-loop'ящемся сервисе. (c) poller-ветка использует `docker ps --filter name=<project>` (substring): проект `api` матчиг `api-worker`/`myapi` чужого стека → ложный unhealthy → PARTIAL.
- **Evidence:** engine.py:133 (max_wait=60), :241 · flow.py:92–94 · healthcheck_poller.py:143–164 (20×(60+3)s), :574–579 → docker_compose.py:574–579 (name-filter), :593 («unhealthy»→ждать), :616–617 (timeout→unhealthy)
- **Impact:** ложно-негативные rollback'и длинно стартующих сервисов; многочасовые очереди деплоев при каскадном crash-loop; cross-project false-PARTIAL.
- **Minimal fix:** per-project start_period/окно из ai-platform.yaml; poller — `docker compose ps -q` (service-scope) вместо глобального name-filter; суммарный бюджет poller'а ≈ engine-окну.
- **Required test:** unit: два контейнера `x` и `x-worker` разных проектов → poller здоров по своему; fake-clock: slow-start 90s при окне 120s → DEPLOYED без rollback.
- **Phase:** deploy-engine + healthcheck_poller

## DATA-606: bootstrap: таймаут orchestrator_cli deploy-many → (0, []) — убитый деплой репортится как «0 failed», exit 0
- **Severity:** MEDIUM · **Confidence:** high
- **Files:** core/internal/bootstrap/deploy/deploy_orchestrator.py · **Symbols:** `_deploy_orchestrator`, `_compute_exit_code` · **Invariant:** частично применённая мутация не должна агрегироваться как отсутствие отказов
- **Violating scenario:** START: subprocess `orchestrator_cli deploy-many` (LocalChannel-деплой модулей) превышает DEPLOY_TIMEOUT=900s → TimeoutExpired убивает ТОЛЬКО родительский CLI (дети-«docker compose up» осиротевшие довершаются) → except возвращает `(0, [])` → failed пуст → severity crit=warn=0 → END: exit 0; часть модулей остаётся в промежуточном состоянии (новые контейнеры без HC-вердикта), rollback/down группы не выполняется (atomic-rollback параллельного пути не срабатывает — он видит группу завершённой), reconcile только при следующем converge.
- **Evidence:** deploy_orchestrator.py:607–610 (`except (TimeoutExpired, OSError): return 0, []`), :636–640 (rc!=0 → WARN-only), :905–912 (WARN→exit 0); parallel_runner.py:352–380 (rollback только при group_failed>0)
- **Impact:** немой partial-деплой платформенных модулей за exit 0; расхождение state.json/фактического стека до следующего converge.
- **Minimal fix:** TimeoutExpired → возвращать (0, docker_names) (все незавершённые = failed) или отдельный severity=crit; опционально post-timeout `docker compose ps`-реконсиляция.
- **Required test:** fake runner бросает TimeoutExpired → assert failed==docker_names и exit_code=2.
- **Phase:** bootstrap φ8/φ12 (DEPLOY_ORCHESTRATOR=true)

## Сводка ответов на вопросы аудита

1. Неидемпотентные под retry — таблица выше; единственная критичная — receive-канал (DATA-601).
2. Не откатывается: миграции БД, volumes-данные, monitoring/status конфиги, отправленные notify, сам неудачный snapshot; .env.platform/compose ОТКАТЫВАЮТСЯ (T9.8 payload restore, но лишь в оркестраторной ветке). Сценарий — DATA-603; структурный слом snapshot-rollback — DATA-604.
3. Частичный compose up: engine оперирует одним service (flow.py:72–80); группа модулей — atomic down всех (parallel_runner.py:352–380) без отката образа/конфига; retry/converge доводит контейнеры, но не volumes/конфиг; откат compose-конфига существует только как T9.8 payload restore. Тихий partial — DATA-606.
4. Гонки ожидания — DATA-605: premature rollback (>60s start), bounded worst-case ~21 мин, substring-collision; бесконечного ожидания нет.
5. Двойные алерты: throttle (event,fingerprint) 3600s гасит повторы — системной проблемы нет; остаточное — PARTIAL vs DEPLOYED как разные fingerprint (см. DATA-602).
6. SSH-мутация под retry: только ForcedCommandChannel (DATA-601); остальные SSH/rsync пути retry не имеют (core_deliverer, remote_executor, context_overlay — проверено grep'ом).
