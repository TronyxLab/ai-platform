# LAUNCH-BLOCKERS · production failure-mode review (09-failures)

$ARTIFACT_CONTRACT
PURPOSE: минимальный набор исправлений/операций до production launch — max risk reduction / min churn
IMPLEMENTS: аудит 17 failure-сценариев (findings-*.md этой папки)
IMPACTS: release checklist; RECOVERY-RISKS.md (соседний файл)
REQUIRES: репо @ 4425ce0 · 2026-08-22 · все evidence выборочно верифицированы главной сессией

## Вердикт

**5 BLOCKER** (без них launch невозможен/опасен) · **3 батча FIX-NOW** (<1 дня суммарно,
config-first) · ACCEPT-RISK (runbook/post-launch, перечень в RECOVERY-RISKS.md).
База аудита: 96 findings (5 CRITICAL / 24 HIGH / ~38 MED / ~29 LOW) по всем 17 сценариям.

## BLOCKERS

| # | ID | Суть | Evidence (верифицировано) | Минимальный фикс | Effort |
|---|----|------|---------------------------|------------------|--------|
| B1 | FAIL-0801 · C | Scaffolded-проект физически не может задеплоиться: ни caller (`templates/template-backend/.github/workflows/deploy.yml` — единственный job делегирует), ни reusable `deploy-project.yml` (только checkout/quality/tar+ssh) не собирают образ, а engine пуллит `ghcr.io/<org>/<project>:<sha>` | прочитаны оба workflow целиком; grep build/push/docker — 0 шагов сборки образа | Добавить build&push job (caller или reusable с input) ИЛИ прогнать e2e scaffold→push→deploy на test-VPS и подтвердить альтернативный канал | M |
| B2 | FAIL-0101 · C | Проектная БД/роль не создаётся при деплое: механизм hooks существует (`post_deploy_chain.py:183 _module_deploy_hooks` читает реестр module.yaml), но в `core/modules/postgres/module.yaml` секции `hooks:` НЕТ (только комментарий :35) → проект с needs.database получает DSN от несуществующей БД | grep hooks module.yaml — только комментарий; реестр пуст | Зарегистрировать hook создания role/DB/GRANT в postgres/module.yaml + wrapper (~5 строк); interim — ручной runbook provisioning | S |
| B3 | FAIL-0200 · C | Единственная реальная очередь платформы (langfuse ingestion) молча эвиктируется: `--maxmemory 64mb --maxmemory-policy allkeys-lru` | `langfuse/docker-compose.base.yml:177-182` — подтверждено чтением | `allkeys-lru` → `noeviction` (1 строка) + рестарт; опц. maxmemory ↑ | XS |
| B4 | FAIL-0300 · C | S3-restored TLS-серты вне renewal И вне expiry-скана: restore пишет в `/etc/letsencrypt/live` (`cert_orchestrator.py:45,465`), сканер смотрит только `/root/.acme.sh/**/fullchain.cer` (`cert_expiry_check.py:48-60`) → тихое истечение ≤90д = полный HTTPS-outage ingress без алерта | оба файла прочитаны, пути подтверждены | Добавить letsencrypt-live в пути скана cert_expiry_check (несколько строк + unit-arg) | S |
| B5 | FAIL-0600 · C | Потеря AGE master-ключа = невосстановимость секретов (DR-канал off-node backup — незакрытый Debt). Фикс операционный, не кодовый | `lifecycle/phases/secrets.py`, `node_detect.py:detect_age_key` | Выполнить `make age-key-backup` + restore-drill ДО launch; занести Debt в план | XS ops |

## FIX-NOW (один патч-батч, config-first)

### Батч A · Observability (весь config, ~полдня)
| ID | Fix |
|----|-----|
| FAIL-1001 · H | Алерт `pg_up == 0 or absent()` (postgres-exporter жив при мёртвом PG — класс FAIL-0201 на stateful-ядре) |
| FAIL-1000 · H | Scrape-job + алерт для minio (вне наблюдения вовсе) |
| FAIL-0201 · H | `redis_up==0 or absent()` + `evicted_keys_total` growth alert |
| FAIL-0204 · H | Jobs для langfuse/langfuse-worker/langfuse-redis в prometheus.yml.tmpl |
| FAIL-0100 · H | pgbouncer-exporter или синтетический DSN-check (фасад всех проектов вне scrape) |
| FAIL-0504 · H | DiskSpace/HighMemory `noDataState: "OK"` → `Alerting` (2 строки; иначе disk-full гасит и монитор) |
| FAIL-0402 · H | Dead-man's switch: внешний heartbeat на status-page `/health` (watchdog видит только внутрь стека) |
| FAIL-1003/1004 · M | `repeat_interval` 24h ↓ для critical; warning-доставка без `disable_notifications` |

### Батч B · Целостность деплоя (маленький код, <20 строк суммарно)
| ID | Fix |
|----|-----|
| FAIL-0700 · H | `concurrency: { group: deploy-${{ inputs.project_name }}, cancel-in-progress: false }` в deploy-project.yml + шаблонах (1 строка YAML) |
| FAIL-0702 · H | chown/chmod deploy-lock под ci-deploy (по образцу history.py:188) — иначе после root-деплоя замок молча не работает ВСЕГДА |
| FAIL-0701 · H | Поднять существующий flock в ReceiveFlow.deploy выше backup/copy (reentrant — дедлока нет) |
| FAIL-0703 · M | retryable = not success AND exit_code != 124 (таймаут = исход неизвестен) |
| FAIL-0102 · H | PARTIAL ≠ `is_success=True` (CI зелёный при упавшем healthcheck) + тест |
| FAIL-0802 · H | adopt-project генерирует несуществующий input `image_tag` → CI adopted-проекта всегда красный (`project_adopter.py:224-240`) |
| FAIL-0711 · M | Устаревший compose-файл переживает переименование в репо — нода молча на старом конфиге при зелёном CI (чистить stale имена вместе с lock-кластером) |
| FAIL-0708/0706 · M | PARTIAL=success окно между healthcheck'ами (exit-код) + битый compose проходит pre-deploy L1 gate как warning (severity-строка) |

### Батч C · Backup/restore и инфраструктура
| ID | Fix |
|----|-----|
| FAIL-0903 · H | Свежесть бэкапа = mtime лога, не факт дампа; partial dump неотличим. Маркер `.last_verified` после «gzip integrity OK» (<10 строк) |
| FAIL-0803 · H | Restore: `ON_ERROR_STOP=1` + pre-restore дамп + «остановить писателей» (сейчас льёт в живой кластер и рапортует success) |
| FAIL-0904/0905 · M | reboot timer 04:30 → 05:45 (убивает backup-окно); `flock -n` ×4 cron-строк backup |
| FAIL-0511 · H | `oom_score_adj: -500` postgres (иначе host-OOM убивает БД первой; system.py:685 это признаёт) |
| FAIL-0805 · M | Platform auto-migrate фейл (litellm Prisma/langfuse CH) severity=normal → зелёный node-update при лежащем LLM-gateway |
| FAIL-0602 · H | Expiry-check для GHCR_PULL_TOKEN (login non-fatal → анонимный pull; rollback спасает старое, новый проект = FATAL exit 10) |
| FAIL-0608 · H | INIT-clone context-overlay эскалировать до FATAL (сейчас GitHub-partition при bootstrap даёт ноду без overlay/vhosts при зелёном пайплайне) |

## Обязательные drills до launch (не код)

1. **Reboot-drill на test-VPS** — закрывает FAIL-0400 (авторебут-политика уже активна: timer 04:30 `Persistent=true`) и замеряет фактическое окно boot-ordering (FAIL-0401).
2. **E2E scaffold→push→deploy** нового проекта на test-VPS — единственная проверка B1/B2 end-to-end.
3. **Restore-drill** из ночного дампа на test-VPS — после фикса FAIL-0803.
4. `chaos FULL T1–T12` + `make test-node NODE=<test>` — уже в release checklist.

## Принятые риски (runbook/post-launch)

Перечень — в RECOVERY-RISKS.md §«Остаточные риски»: 0502, 0301, 0302, 0303, 0401(после drill),
0900, 1006, 1005, 1007, 0406, 0704, 0705, 0405(закрывается 0903), HYP: 0407, 1008.

## Позитивные контролы (НЕ трогать)

Лог-ротация 13/13 compose + daemon.json; memory limits 100% long-running контейнеров;
restart unless-stopped/always; Loki compactor retention; WAL safe-delete; watchdog cron */5;
zram 4G; bootstrap --resume (state.json коррапт-защищён, verified-safe 0906); converge
NB-lock конвергентные reconcilers (0907); export-metrics flock+timeout+atomic (0902);
cert-expiry daily check сам по себе работает (дыра только в путях скана — B4).
