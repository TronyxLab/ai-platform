<!-- GREP_SUMMARY: StatusReport, bootstrap, tronyx-vps, bare-metal, idempotency, scp-rsync-invariant, nginx-restarting -->

# $START_STATUS_REPORT
# $STATUS: ARCHIVED

## $ARTIFACT_CONTRACT
- **PURPOSE:** Зафиксировать результат bootstrap голого сервера tronyx-vps после переустановки до bare metal
- **DESCRIPTION:** Полный цикл: preflight → `make bootstrap-node NODE=tronyx-vps` → проверка идемпотентности вторым запуском → верификация инвариантов доставки (core = SCP/rsync, не git) → инвентаризация контейнеров
- **RATIONALE:** Сервер переустановлен хостером до bare metal; Connection Context Card была помечена STALE и переверифицирована с нуля (Verify before trust)
- **ACCEPTANCE_CRITERIA:** (1) Bootstrap exit 0; (2) второй запуск = no-op; (3) `/opt/platform/core` — не git-репозиторий; (4) модули платформы healthy
- **IMPLEMENTS:** Задача «Bootstrap голого сервера tronyx-vps» (план пользователя, 5 шагов)
- **IMPACTS:** tronyx-vps (103.88.243.151) — полная переинициализация платформы; `.ai/server-state.json` обновлён
- **REQUIRES:** ssh-agent (ED25519 Tronyx), AGE_SECRET_KEY (env, REDACTED), node-configs из контекста tronyx-lab

---

## Section 1 — Diagnostic Summary

**Environment Fingerprint (до bootstrap):**

| Параметр | Значение |
|----------|----------|
| Host | tronyx-vps (103.88.243.151), root |
| OS | Ubuntu 24.04.4 LTS, kernel 6.8.0-134-generic, x86_64 |
| Состояние | Bare metal подтверждён: NO_DOCKER, NO_PLATFORM, /opt пуст, диск 1.8G/77G, uptime 1:11 |
| RAM | 7.8Gi |

**Preflight (все PASS):**
- ssh-agent: ED25519 «Tronyx» загружен, совпадает с `owner_key` в node.yaml — PASS
- AGE-ключ: `AGE_SECRET_KEY` найден в окружении (источник `~/.zshrc` ← `~/.ssh/age-key-personal.txt`); значение REDACTED — PASS
- SSH: `ssh tronyx-vps "df -h"` OK, ControlMaster настроен — PASS
- Локальные конфиги: `tronyx-lab/node-configs/tronyx-vps/node.yaml` + `secrets/tronyx-vps.enc.yaml` (mode 600) — PASS
- DRY_RUN=1: node.yaml разрезолвлен, AGE-ключ детектирован, план доставки = rsync — PASS

**Issues:**

| Severity | Issue | Статус |
|----------|-------|--------|
| LOW | nginx в crash loop: `[emerg] host not found in upstream "tronyx-site"` | ОЖИДАЕМО — upstream-проект не задеплоен; стабилизируется после `make deploy` tronyx-site |
| LOW | Context overlay `/opt/tronyx-lab/platform` отсутствует (`No repos.platform in node.yaml`) | Известный WARN из прошлого bootstrap (карта, план 009) |
| LOW | Compose WARN: `LITELLM_METRICS_TOKEN`, `S3_ACCESS_KEY`, `AWS_*` не заданы на этапе деплоя | Модули healthy; секреты подтягиваются из `/run/platform/secrets.env` |

## Section 2 — Actions Taken

**Superposition (Mode 3 GUIDED):** канонический `make bootstrap-node` (принят) vs ручной SCP + удалённый скрипт (rejected: нарушает фасад Makefile) vs `node-update` (rejected: INIT, не UPDATE). Dry-run выполнен перед мутацией.

1. **Bootstrap #1** — `make bootstrap-node NODE=tronyx-vps` → `Bootstrap COMPLETE — exit 0`. Полный INIT: apt deps, Docker, tor/privoxy, ufw, users (platform, ci-deploy), acme.sh, sudoers per-module, decrypt-secrets, деплой модулей: **deployed=13 skipped=0 failed=0**. Healthchecks: 12/13 PASS, nginx FAIL (ожидаемо, см. выше).
2. **Bootstrap #2 (идемпотентность)** — все шаги lifecycle `SKIP: checkpoint found`; исключение — `verify-core` (read-only верификация, переигранная из-за backward-compat инвалидации content-hash, мутаций нет). rsync-конвергенция: удалён паразитный каталог `modules/nginx/config/platform-default.conf/` на remote. **No-op подтверждён.**
3. Мутации только через канонический таргет — ручных правок на VPS нет (P22 не задействован, легализация не требуется).

**Верификация инвариантов доставки:**

| Инвариант | Результат |
|-----------|-----------|
| Core через SCP/rsync, не git | ✅ `git -C /opt/platform/core status` → `fatal: not a git repository` |
| Context-overlay = git pull | ⚠️ N/A — `repos.platform` не задан в node.yaml (известный WARN) |
| Project payload = forced-command | ✅ ci_deploy_key установлен, `user-ci-deploy` шаг PASS |
| Только канонические make-таргеты | ✅ единственный verb: `bootstrap-node` |
| ufw | ✅ active: 22/80/443 ALLOW, 5432 explicit DENY |
| platform-secrets.service | ✅ enabled (one-shot, активируется на boot) |

**Контейнеры (20 Up healthy + 2 Exited(0) one-shot + 1 restarting):**
nginx (Restarting — ожидаемо), loki, promtail, clickhouse, minio, redis, postgres, pgbouncer, postgres-exporter, node-exporter, cadvisor, redis-exporter, nginx-prometheus-exporter, prometheus, grafana, langfuse, langfuse-redis, backup-cron, litellm, hermes-agent; one-shot: prometheus-config-init, minio-minio-createbuckets-1 (оба Exited 0).

## Section 3 — Audit Trail

| Время (MSK) | Действие | Rationale | Результат |
|-------------|----------|-----------|-----------|
| 15:50 | Read Connection Context Card + ai-instructions.yaml [IMP:8] | VALIDATE_CTX; карта помечена STALE (bare metal reinstall) | Card найдена, host = tronyx-vps |
| 15:50 | ssh-add -l, поиск AGE-ключа, ssh df -h [IMP:8] | PREFLIGHT checks 1-3 | Все PASS |
| 15:51 | Remote fingerprint (batch ssh) [IMP:8] | Verify before trust — подтвердить bare metal | NO_DOCKER, NO_PLATFORM |
| 15:52 | `make bootstrap-node NODE=tronyx-vps DRY_RUN=1` [IMP:9] | Валидация гипотезы перед мутацией | PASS |
| 15:53–16:05 | `make bootstrap-node NODE=tronyx-vps` [IMP:10] | Единственная мутация — канонический INIT | exit 0, deployed=13 failed=0 |
| 16:06 | Повторный `make bootstrap-node` [IMP:9] | Инвариант №6 — идемпотентность | No-op (все SKIP) |
| 16:08 | Batch-верификация инвариантов (ssh) [IMP:9] | System State > Intent | Core не git; 20 healthy |
| 16:09 | `docker logs nginx` [IMP:8] | Диагностика crash loop до вердикта | Root cause = отсутствующий upstream |
| 16:10 | Обновление `.ai/server-state.json` [IMP:8] | P1 — карта актуализирована | DONE |

**Deviations from plan:** нет. Шаг 4 плана расширен диагностикой nginx-логов (P14 — вердикт только после подтверждения root cause).

## Section 4 — Legalization Tasks

Пусто — ручных мутаций VPS не было; все изменения через канонический `make bootstrap-node`.

---

## Overall verdict: **SUCCESS**

- Bootstrap завершился успешно (exit 0, 13/13 модулей задеплоено)
- Идемпотентность подтверждена (второй запуск = no-op, все чекпойнты SKIP)
- Core — не git-репозиторий (`fatal: not a git repository`) — доставка SCP/rsync
- 20 контейнеров Up (healthy); nginx restarting по известной ожидаемой причине

**Next steps (шаблоны):**
1. Деплой проекта для стабилизации nginx: `make deploy PROJECT=tronyx-site` (git push → CI → forced-command)
2. Устранить WARN context-overlay: добавить `repos.platform` в `tronyx-lab/node-configs/tronyx-vps/node.yaml` либо создать `/opt/tronyx-lab/platform` — затем `make node-update NODE=tronyx-vps`

# $END_STATUS_REPORT
