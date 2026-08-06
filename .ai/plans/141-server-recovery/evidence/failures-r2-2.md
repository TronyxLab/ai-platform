# failures-r2-2.md — B19 + B20: деплой-права и пост-деплой чейн

$START_FAILURE_REPORT

- **Дата/время:** 2026-08-06 12:30-12:50 MSK (2-й цикл, Фаза 3)
- **Обнаружено при:** deploy-project ×4, K3 verify, DevPlan 138 W3 проверка

## B19 — .deploy-snapshots root:root блокирует receive-деплой под ci-deploy

**Симптом:** `make deploy-project PROJECT=~/projects/tronyx-lab/tronyx-site NODE=tronyx-vps`:
контейнер обновлён и healthy, но результат FAILED:
`[IMP:10][DeployOrchestrator][deploy] Verify failed for tronyx-site: [Errno 13] Permission denied: '/opt/projects/tronyx-site/.deploy-snapshots/payload' (auditing FAILED)`.

**Причина:** бутстрап (φ8 context_deployer, root) создал `/opt/projects/<p>/.deploy-snapshots/` как
root:root 0755. Receive-деплой выполняется под ci-deploy → не может писать snapshot → FAILED.

**Workaround (нода):** `chown -R ci-deploy:ci-deploy /opt/projects/{tronyx-site,dance-site,botanika,legacy,roadmap}/.deploy-snapshots` → все 4 деплоя DEPLOYED (3.0-12.8s, healthy).

**Почему не было в 1-м цикле:** в 1-м цикле snapshots создавались первым deploy-project (под ci-deploy) — владелец ci-deploy; во 2-м цикле бутстрап сам создал проекты и snapshots (root).

**Фикс-кандидат:** DeployOrchestrator/ReceiveFlow при создании .deploy-snapshots — chown ci-deploy
(или snapshot-шаг: Permission denied → WARN, не FAIL; но аудит-трейл должен писаться).

## B20a — practices.lock НЕ доставляется payload'ом receive

**Симптом:** после `make project-sync-practices` (локально practices.lock создан) и `deploy-project`
(канонический канал) — на ноде `/opt/projects/tronyx-site/practices.lock` ОТСУТСТВУЕТ.

**Причина:** `core/internal/deploy/payload_deliverer.py:69`:
`_PAYLOAD_FILE_NAMES: tuple = (*PROJECT_COMPOSE_FILENAMES, "ai-platform.yaml", ".env.platform")` —
whitelist payload'а НЕ включает practices.lock. Противоречит AGENTS.md §Наследование практик
(DevPlan 137): «practices.lock ... доставляется на VPS payload'ом receive».

**Влияние:** K3 verify для не-adopted проекта всегда state=legacy (нет lock) → L2-PROPOSE вечно.

**Примечание:** practices.lock доставлен на ноду ТОЛЬКО ручным receive (root, полный tar) —
артефакт диагностики, владелец root:root (не канон).

**Фикс-кандидат:** добавить `"practices.lock"` в _PAYLOAD_FILE_NAMES (+ unit-тест).

## B20b — пост-деплой чейн (DevPlan 138 W3 render-monitoring автозапуск) пишет с Permission denied

**Симптом:** после 3 успешных deploy-project: `/opt/platform/catalog.json` — 2 байта от 09:09
(не регенерирован), `/opt/platform/prometheus-targets/` — ПУСТ (render-monitoring не отработал),
дашборды мониторинга не обновлены. DevPlan 138 W3 (автозапуск run_monitoring_reconfig) НЕ работает.

**Причина:** `_run_post_deploy_chain` вызывается (receive_flow.py:316 при is_success), но receive
выполняется под ci-deploy: `id ci-deploy` → группы `ci-deploy, adm, docker` — НЕ platform.
- `/opt/platform/catalog.json` — -rw-r--r-- root:platform → generate-catalog Permission denied (WARN)
- `/opt/platform/prometheus-targets/` — drwxrwsr-x root:platform → reconfig Permission denied (WARN)
WARN'ы скрыты (stderr forced-command не показывается при успехе) — молчаливая деградация.

**Фикс-кандидаты:** (а) ci-deploy в группу platform + chmod 664 catalog.json (и прочие артефакты
/opt/platform root:platform), (б) sudoers-whitelist для чейн-записей, (в) чейн-записи в
ci-deploy-writable пути + синк. Требует решения архитектора (минимальный: (а)).

## Варнинги K3 (в отчёт, non-blocking)

- dance-site/botanika/roadmap (adopted, без lock): `[PRACTICES:PROPOSE][L2][drift-practices] practices.lock not found (legacy)` — state=legacy, blocking=0, exit=0.
- tronyx-site: state=proposed, findings=0 (lock на ноде — через ручной receive; канон — B20a).

$END_FAILURE_REPORT
