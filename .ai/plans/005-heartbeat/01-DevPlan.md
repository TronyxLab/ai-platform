# 01-DevPlan — Удаление dead-man's switch (heartbeat: reader + writer + tor-canary)

## $ARTIFACT_CONTRACT

- **PURPOSE:** Полностью удалить из платформы систему dead-man's switch (heartbeat-check reader + heartbeat.py writer + tor-canary), включая все креды, события, тесты и документальные ссылки — без следов существования.
- **DESCRIPTION:** DevPlan 003 A2/A3 («heartbeat-check» reader + tor-canary) и DevPlan 162 W6-1 (writer heartbeat.py) добавлялись для out-of-band живучести. Компонент признан ненужным: reader падает на каждом прогоне (пустые креда + сломанный импорт `core`), writer осиротеет без reader'а, tor-canary ездит на heartbeat-payload'е. Удаляется целиком.
- **RATIONALE:** «Это просочилось в девплан, но не нужно в проекте» (заявка оператора). Осиротевший writer продолжит писать S3 каждые 15 мин без читателя — противоречит «без следов».
- **ACCEPTANCE_CRITERIA:** `grep -ri "heartbeat"` → 0 совпадений в code/config/tests/workflows (допустимо только в исторических `.ai/plans/003*` и `162*`); `grep -ri "S3_READONLY"` → 0; `grep -ri "tor-chain\|tor_chain_down\|tor:chain_down"` → 0; `make check` зелёный; workflow-count gate = 8.
- **IMPLEMENTS:** удаление reader (003 A2), writer (162 W6-1), tor-canary (003 A3).
- **IMPACTS:** backup-cron (Dockerfile/crontab/compose), shared (deploy_paths, notifications), bootstrap (tor_proxy_check), secret-definitions/platform-infra/notification-catalog, гейты (workflow_consistency, org_secrets_provisioner).
- **REQUIRES:** удаление GitHub Secrets `S3_READONLY_*` (repo+org) и read-only IAM-ключа в S3-провайдере — out-of-band.

---

## 1. Граница удаления (зафиксировано)

| Компонент | Удалить | Источник |
|-----------|---------|----------|
| **reader** `heartbeat-check` | полностью | 003 A2 |
| **writer** `heartbeat.py` | полностью | 162 W6-1 |
| **tor-canary** (state-file + audit `tor:chain_down` + `tor_chain_down` в payload) | полностью | 003 A3 |

**Остаётся:** 3-stage Tor healthcheck `tor_proxy_check.py` (DevPlan 118) — только само ядро проверки; из него вычищаются наросты 003 A3.

---

## 2. Файл-манифест

### 2.1 DELETE (5 файлов)

| Файл | Что |
|------|-----|
| `.github/workflows/heartbeat-check.yml` | CI-крон reader |
| `core/internal/scripts/heartbeat_check.py` | reader Python |
| `core/modules/backup-cron/scripts/heartbeat.py` | writer Python |
| `tests/unit/test_heartbeat_check.py` | тест reader |
| `tests/unit/test_backup_heartbeat.py` | тест writer |

### 2.2 EDIT — SoT (15 файлов)

| Файл | Правка |
|------|--------|
| `core/secret-definitions.yaml` | удалить `S3_READONLY_ACCESS_KEY`/`S3_READONLY_SECRET_KEY` (L282–297) + комментарий L282; из notes TELEGRAM (L156, L162) и L143 убрать «heartbeat-check» |
| `core/platform-infra.yaml` | удалить блок S3_READONLY (L194–199); удалить tor-chain host-dir (L279) |
| `core/notification-catalog.yaml` | удалить `heartbeat.stale` (L63–67) и `tor.chain_down` (L68–72); в L57 убрать «heartbeat» |
| `core/internal/scripts/sync_env_defaults.py` | удалить генерацию S3_READONLY (L450–453); удалить tor-canary строку (L749) |
| `core/internal/deploy/org_secrets_provisioner.py` | из `_ORG_SECRET_PLAN` удалить 2×S3_READONLY (L50–51); GREP_SUMMARY (L1), docstring (L15), комментарий (L143) |
| `core/internal/deploy/context_promoter.py` | L306: убрать `heartbeat-check/` из комментария |
| `core/internal/shared/notifications.py` | L570: убрать `heartbeat-check` из TRAP-комментария |
| `core/internal/shared/deploy_paths.py` | удалить `tor_chain_state_file()` (L364–376) |
| `core/internal/healthcheck/tor_proxy_check.py` | удалить наросты 003 A3: `_write_chain_state`+`_plw_body_chain_state` (L170–218), `_write_chain_audit` (L221–246), вызовы в `run_all` (вернуть чистый `return False` по стадиям), параметры `state_file`/`audit_fn`, `TOR_CHAIN_STATE_FILE` в `main`; @changes L22–23 |
| `core/modules/backup-cron/Dockerfile` | удалить `COPY scripts/heartbeat.py` (L87–88) + комментарий (L112–113) |
| `core/modules/backup-cron/scripts/crontab` | удалить heartbeat `*/15` (L44–47) |
| `core/modules/backup-cron/docker-compose.base.yml` | удалить `TOR_CHAIN_STATE_FILE` env (L92–94) и read-only bind (L103–106) |
| `core/AGENTS.md` | L272: убрать `S3_READONLY_*` из матрицы ключей |
| `core/internal/shared/AGENTS.md` | L50: убрать `heartbeat_check` из списка потребителей `notifications.py` |
| `core/internal/bootstrap/AGENTS.md` | L228–229: убрать heartbeat из «Операционных проверок» |

### 2.3 EDIT — тесты (2 файла)

| Файл | Правка |
|------|--------|
| `tests/gates/test_gate_workflow_consistency.py` | убрать `"heartbeat-check.yml"` (L53), комментарии L49/L63, счётчик 9→8 (L64) |
| `tests/unit/test_org_secrets_provisioner.py` | убрать S3_READONLY из фикстур/ассертов (L59, L81, L87, L91) |

### 2.4 REGENERATE (3 артефакта, через `make generate-manifests`)

| Файл | Генератор |
|------|-----------|
| `core/secrets-manifest.yaml` | из secret-definitions.yaml |
| `platform-env.yaml` | из platform-infra.yaml |
| `.env.example` | из sync_env_defaults.py |

---

## 3. Волны исполнения

**Wave 1 — ядро reader:** удалить 5 файлов (2.1), удалить событие `heartbeat.stale` + `tor.chain_down` из catalog, workflow-гейт (счётчик 8).

**Wave 2 — writer + tor-canary:** heartbeat.py, crontab, Dockerfile, compose (env+bind), tor_proxy_check.py (откат к pre-003), deploy_paths.py (`tor_chain_state_file`).

**Wave 3 — секреты/генерируемые:** secret-definitions, platform-infra, sync_env_defaults, org_secrets_provisioner (+тест), context_promoter, notifications — затем `make generate-manifests`.

**Wave 4 — доки:** core/AGENTS.md, shared/AGENTS.md, bootstrap/AGENTS.md.

---

## 4. Acceptance / верификация

1. `grep -ri "heartbeat" .` → только исторические `.ai/plans/003*`/`162*` (они — неизменяемая история, вне scope).
2. `grep -ri "S3_READONLY" .` → **0**.
3. `grep -ri "tor-chain\|tor_chain_down\|tor:chain_down" .` → **0** (вне исторических планов).
4. `make check` → зелёный (в т.ч. workflow_consistency 8, notification_parity без `heartbeat.stale`/`tor.chain_down`).
5. Out-of-band (ручное, вне git): удалить GitHub Secrets `S3_READONLY_ACCESS_KEY`/`S3_READONLY_SECRET_KEY` (repo `Tronyx161/AI-platform` + org `TronyxLab`), удалить read-only IAM-ключ в S3-провайдере; в Actions UI перестанут появляться `failure`-раны `heartbeat-check`.

---

## 5. Классификация и риски

- **Размер:** >20 файлов, но **чисто субтрактивная** правка (нет arch/API/schema-изменений) — фактически STANDARD-риск; DevPlan без Brief.
- **Риск R1 (низкий):** `test_gate_notification_parity` — удаление событий вместе с единственным call-site сохраняет паритет; если гейт имеет явный allowlist — поправить в Wave 1.
- **Риск R2 (низкий):** e2e chaos T5 использует `tor_proxy_check` (ядро остаётся) — не затронут.
- **Риск R3 (средний):** после удаления writer'а crontab/Dockerfile-compose должны собраться чисто (`make check MARKER=contract`).
