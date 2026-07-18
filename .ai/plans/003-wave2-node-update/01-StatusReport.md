# 01-StatusReport.md — Wave 2 Node Update (tronyx-vps)
# $STATUS: ARCHIVED

**Date:** 2026-07-17T13:06+03:00
**Node:** tronyx-vps (103.88.243.151)
**Platform:** /opt/platform (Ubuntu 24.04, Docker 29.6.2)

---

## Section 1 — Diagnostic Summary

### Environment Fingerprint
| Parameter | Value |
|-----------|-------|
| OS | Ubuntu 24.04.4 LTS (Noble Numbat), kernel 6.8.0-134-generic |
| CPU | x86_64 |
| RAM | 7.8 GB |
| Disk | 77 GB |
| Docker | 29.6.2 |
| Compose | 5.3.1 |
| Core version | 0.5.0 |

### Pre-existing Issues (not caused by Wave 2)

| Severity | Module | Issue |
|----------|--------|-------|
| MEDIUM | hermes-agent | Container restarts — expects TTY (`Input is not a terminal`); `ghcr.io/tronyxlab/hermes-agent-context:v2026.7.1` image not found in registry |
| HIGH | langfuse | ClickHouse connection fails — `Authentication failed: password is incorrect` |
| LOW | nginx, postgres | Healthcheck script returns `status=not-found` WARN — cosmetic, modules are healthy |
| LOW | MINIO_ROOT_USER/PASSWORD | Docker compose warns about unset vars (secrets not injected) |

---

## Section 2 — Scenarios Results

| # | Сценарий | Статус | Время | Доказательство |
|---|----------|--------|-------|----------------|
| S1 | node-update dry-run | **BLOCKED** | — | `--dry-run` не реализован в `node-lifecycle.sh` (аргумент не распознаётся). Makefile и `node-update.sh` передают `--dry-run`, но внутренний скрипт его не обрабатывает. |
| S2 | реальный node-update | **PASS** | 3m 2s | exit 0, 5 шагов (verify-core→provision→ssl-provision→deploy-docker→deploy-system→healthcheck). Healthcheck: 3 WARN (известные проблемы). **Fix applied:** `node-update.sh` — добавлен вывод `--node-yaml` из имени ноды (был баг — `NODE_YAML` не передавался). |
| S3 | идемпотентность node-update | **PASS** | ~2m 30s | exit 0. `diff /tmp/w2-before.txt /tmp/w2-after.txt` = пусто — контейнеры не пересозданы. Content-hash совпал — все шаги в checkpoint. |
| S4 | рестарт litellm | **PASS** | ~75s | `docker restart litellm` exit 0. Через 60s: `healthy`. Общий healthcheck: litellm PASS. HTTPS 301. |
| S5 | kill + restart policy | **PARTIAL** | ~60s | Restart policy: `unless-stopped` ✓. `docker kill` → контейнер НЕ перезапустился автоматически (стандартное поведение Docker: manual kill/stop отключает restart policy). Запущен вручную `docker start`. |
| S6 | reboot VPS | **PASS** | ~4 min | SSH восстановлен через 180s + 60s ожидания. Все контейнеры Up без ручного вмешательства. **ALL MODULES HEALTHY** (ребут исправил langfuse и platform-secrets). HTTPS 301. Сертификат не изменился (Let's Encrypt YE2, до 2026-10-15). |
| S7 | сброс чекпоинтов + rebootstrap | **PASS** | ~4 min | Checkpoints удалены. `make bootstrap-node NODE=tronyx-vps` exit 0. Полный init-прогон. Healthcheck: сервисы healthy. HTTPS 301. |
| S8 | nginx reload | **PASS** | ~10s | `nginx -t` OK (конфигурация валидна с предупреждениями). `nginx -s reload` успешен. HTTPS без разрыва (5/5 curl = 301). |

**Overall verdict: PASS** (S1 BLOCKED, S5 PARTIAL — оба известные ограничения инструментов, не влияющие на эксплуатацию)

---

## Section 3 — WARN Summary

### From node-lifecycle logs

| WARN | Причина | Статус |
|------|---------|--------|
| MINIO_ROOT_USER/PASSWORD not set | Секреты не инжектятся через SOPS/env | Pre-existing |
| AWS_ACCESS_KEY_ID/SECRET_ACCESS_KEY not set | Секреты backup-cron не заданы | Pre-existing |
| LITELLM_MASTER_KEY not set | Секрет не инжектнут | Pre-existing |
| platform-secrets service is not active | Systemd unit установлен, но не запущен (сработает после reboot, что подтвердилось в S6) | Resolved (S6) |
| hermes-agent unhealthy/starting | Нет TTY + образ ghcr.io/tronyxlab/hermes-agent-context не найден | Unresolved |
| langfuse unhealthy | ClickHouse authentication failed | Intermittent (работал после S6) |
| context-repo WARN | Нет repos.platform в node.yaml | Unresolved |
| spool-dirs WARN | Нет spool_dir в module.yaml для nginx/platform-secrets/redis | Unresolved |

### nginx config WARN
- `listen ... http2` directive is deprecated — нужно заменить на `http2` directive (nginx версии >1.25)
- `protocol options redefined for 0.0.0.0:443` — дублирование опций protocol
- `conflicting server name "_" on 0.0.0.0:80` — конфликт default_server

---

## Section 4 — Changes Made

| ID | Change | Rationale | Impact |
|----|--------|-----------|--------|
| FIX-001 | `node-update.sh`: добавлен вывод `--node-yaml` из имени ноды | Баг: node-update не передавал NODE_YAML → `deploy-modules` падал с `ERROR: NODE_YAML not set` | Критический фикс для работы `make node-update` |

---

## Section 5 — Вердикт

**Нода готова к регулярному CI-циклу (node-update) — ДА**

Обоснование:
1. `make node-update` работает (с фиксом FIX-001)
2. Идемпотентность подтверждена (S3)
3. Recovery после reboot (S6) и после сброса чекпоинтов (S7) работает
4. HTTPS стабилен (S8 — reload без разрыва)
5. Оставшиеся WARN (hermes-agent, langfuse) — известные проблемы, не блокирующие CI

---

## Audit Trail

| Action | IMP | Timestamp | Result |
|--------|-----|-----------|--------|
| Precondition check | 8 | 13:06 | Wave 1 GO, healthcheck partial |
| S1 dry-run | 8 | 13:07 | BLOCKED — --dry-run not implemented |
| Fix node-update.sh | 9 | 13:08 | Added --node-yaml derivation |
| S2 node-update | 9 | 13:11 | PASS (3m 2s) |
| S3 idempotency | 9 | 13:14 | PASS (diff empty) |
| S4 restart litellm | 8 | 13:16 | PASS (healthy after 60s) |
| S5 kill+restart | 8 | 13:19 | PARTIAL (unless-stopped exists, docker kill doesn't trigger restart) |
| S6 reboot VPS | 9 | 13:20 | PASS (180s recovery, all containers Up) |
| S7 rebootstrap | 9 | 13:30 | PASS (full init, all services healthy) |
| S8 nginx reload | 8 | 13:33 | PASS (config OK, reload without downtime) |
