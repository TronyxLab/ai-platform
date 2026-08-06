# failures-r2-3.md — B21-B25: reboot-устойчивость и healthcheck-дефекты (вскрыты chaos T11)

$START_FAILURE_REPORT

- **Дата/время:** 2026-08-06 12:15-13:00 MSK (2-й цикл, Фаза 4 — chaos-сьют)
- **Результат:** 8 failed / 3 passed (43:19). PASSED: T4/T5/T6.

## B21 — bind-mount-источники /run/platform (tmpfs) не переживают reboot → nginx/status-page Exited(127)

**Симптом (после `systemctl reboot`, T11):** `docker ps -a`: `nginx Exited (127)`, `status-page Exited (127)`,
`cadvisor Created`. Сайты 000.

**docker inspect:**
```
nginx:       error mounting "/run/platform/.htpasswd-platform" to rootfs at "/etc/nginx/conf.d/.htpasswd-platform": ... not a directory
status-page: error mounting "/run/platform/status-metrics.json" to rootfs at "/run/platform/status-metrics.json": ... not a directory
```
/run — tmpfs: файлы исчезают при перезагрузке; docker создаёт источники как ДИРЕКТОРИИ → mount dir-на-файл → 127.

**Кто пересоздаёт после reboot:** `.htpasswd-platform` — ТОЛЬКО φ4/φ9 (secrets_manager htpasswd, skip при no-op bootstrap!);
`status-metrics.json` — metrics-cron (через время) — но до cron nginx/status-page мертвы.

**Восстановление (ручное, канон-команды):**
`python3 -m core.internal.bootstrap.lifecycle.secrets_manager htpasswd --email ... --password ...`
(env из /run/platform/secrets.env, файл пересоздан — secrets.env сам лежит в /run и восстанавливается φ9/decrypt? —
нет: secrets.env тоже tmpfs! но он был на месте после reboot — вероятно, docker restart пересоздал? — требует проверки)
+ `bash core/internal/healthcheck/platform-export-metrics.sh` (status-metrics.json).

**Фикс-кандидат (требует решения архитектора):** (а) файлы НЕ в tmpfs (например, /var/lib/platform/run/),
(б) systemd-tmpfiles.d/генератор при загрузке, (в) docker-entrypoint пересоздаёт файлы при старте контейнера,
(г) бутстрап no-op должен проверять/пересоздавать файлы /run/platform (liveness T9.17 расширить).

## B22 — converge R9 не видит упавшие контейнеры (self-heal мёртв)

**Симптом:** после B21 (nginx/status-page Exited, cadvisor Created) `converge` → «FULLY CONVERGED»,
R9: `Module nginx all containers OK` (проверен ТОЛЬКО nginx-prometheus-exporter), `Module infra-metrics`/`status-page` — «no containers»,
`healed=0 errors=0`. Ничего не починено.

**Причина:** `resolve_container_name` (converge/runtime.py) использует `docker ps --filter name=<module>` —
ТОЛЬКО running-контейнеры. Exited/dead/created невидимы → get_container_state не вызывается → BAD-состояния
не детектируются → `compose up -d` self-heal не срабатывает никогда.

**Фикс-кандидат:** docker ps **-a** (+ фильтр по compose-project или имён модулей); тест: убить контейнер
(docker kill) → converge должен поднять.

## B23 — nginx compose fallback ${NGINX_OVERLAY_DIR:-./overlays}: ручной compose up теряет vhost-оверлей

**Симптом:** после моего ручного `docker compose -f root --profile nginx up -d` (без NGINX_OVERLAY_DIR env):
nginx пересоздан с mount `/opt/platform/core/modules/nginx/overlays -> /etc/nginx/conf.d/overlay` (ПУСТОЙ!),
все vhost-конфиги (tronyx.ru.conf и др.) пропали из контейнера → 0 server-блоков с listen 443 → все сайты 444.

**Причина:** `docker-compose.base.yml: ${NGINX_OVERLAY_DIR:-./overlays}` — fallback на пустой каталог модуля.
Канонический деплой (deploy_docker_module) всегда ставит NGINX_OVERLAY_DIR=/opt/node-configs/<node>/overlays/nginx,
но любой ручной/внешний compose up (оператор, CI-хелпер) тихо теряет vhosts.

**Фикс-кандидат:** fallback `${NGINX_OVERLAY_DIR:?required}` (fail-fast вместо тихой пустоты) ИЛИ
auto-detect node-configs overlay при отсутствии env.

## B24 — status-page healthcheck.sh deep хардкодит host 127.0.0.1:8080 (порт не публикуется!)

**Симптом:** `status-page healthcheck.sh` MODE=deep: `check_http "http://127.0.0.1:8080/health" "200"` —
а status-page НЕ публикует порты (compose: «NO external ports — accessed via nginx proxy_pass»,
внутренний STATUS_PAGE_PORT=8080 «дублирует CADVISOR_PORT намеренно»). На хосте 8080 занят
cadvisor/test-project → deep-check всегда бьётся об чужой сервис.

**Фикс-кандидат:** check_http через nginx (https://platform.tronyx.ru/...) ИЛИ docker exec
`curl localhost:8080/health` внутри контейнера.

## B25 (dev-only, не блокер ноды) — dev status-page unhealthy 24h

dev-машина: `status-page Up 24 hours (unhealthy)` (остальные healthy). make healthcheck — локальный
(TRAP T10: NODE игнорируется) → exit 1. Нода при этом 25/25 healthy. Причина dev-unhealthy —
вероятно, STATUS_METRICS_JSON bind-mount (на macOS /run/platform отсутствует) — требует dev-разбора.

## Chaos-контекст

- T1: containers not recovered: ['backup-cron' (B18b — модуль не задеплоен), 'cadvisor' (Created)].
- T3/T10: «No such container: backup-cron» (B18b).
- T7: «OOM victim not named: 3» — OOM-инъекция не дала ожидаемой жертвы (специфика окружения).
- T8: IndexError (парсинг вывода disk-pressure) — специфика.
- T9: «age: command not found» — age CLI НЕ установлен на ноде (тест ожидает его; decrypt идёт другим путём)
  — тест-окружение, требует решения: установить age ИЛИ скорректировать тест.
- T11: reboot — вскрыл B21/B22; восстановление вручную (см. B21). Нода после восстановления: 25/25 healthy.

$END_FAILURE_REPORT

## B26 — state.json исчез с ноды (между 12:31 и 12:58 MSK)

**Симптом:** финальная сверка — FileNotFoundError: /var/lib/platform/.bootstrap/state.json.
Директория .bootstrap (mtime 12:48) содержала только .nginx-overlay hash, python-deps.hash, state.json.lock.

**Контекст:** state.json существовал в 12:14 (pending update-фаз), в 12:28 (node-update "State loaded: mode=update current_step=5"),
в 12:31 (bootstrap no-op, setup_state + atomic write "committed"). Исчез после. Механизм НЕ выявлен
(в окне: converge ×3, docker compose up ×3, healthcheck, ручной receive с rollback — ни один не пишет/не удаляет state.json).

**Последствие:** следующий bootstrap-node стал бы ПОЛНЫМ (потеря инварианта 6: INIT не DEPLOY).

**Восстановление (оператор, из фактических логов):** state.json пересоздан — 14 фаз done
(hash'и INIT-фаз из чтения 12:14; update-фазы done без hash — B8 не даёт считать hash). Проверено: node-update no-op (все 5 фаз skip).

**Фикс-кандидат:** аудит-запись/уведомление при удалении state.json; расследовать механизм (возможно, связан с atomic_write/os.replace при конкурентном доступе или cleanup-путём).
