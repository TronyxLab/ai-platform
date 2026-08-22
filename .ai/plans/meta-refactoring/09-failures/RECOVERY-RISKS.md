# RECOVERY-RISKS · восстановление при отказах (09-failures)

$ARTIFACT_CONTRACT
PURPOSE: карта восстановления по всем 17 failure-сценариям + слепые зоны мониторинга + операторский cheat-sheet
IMPLEMENTS: вопросы №3–№8 аудиторского протокола (findings-*.md)
IMPACTS: операторский runbook launch-периода; LAUNCH-BLOCKERS.md
REQUIRES: репо @ 4425ce0 · 2026-08-22

## Матрица восстановления (17 сценариев)

Авто-recovery / Детект / Broken state / Retry / Оператору:

| # | Сценарий | Авто-recovery | Детект | Broken state | Retry | Оператору |
|---|----------|---------------|--------|--------------|-------|-----------|
| 1 | database unavailable | частично: postgres `unless-stopped` поднимется; pgbouncer фасад вне scrape | partial: `pg_up` алерта нет (1001); exporter-alive маскирует | langfuse-очередь вытесняется при простое (0104+0200); деплой → PARTIAL, но CI зелёный (0102) | деплой после восстановления безопасен | рестарт модуля postgres; проверять pgbouncer ОТДЕЛЬНО от postgres |
| 2 | redis unavailable | да (`restart: always`) | **НЕТ** (0201) | кэш — ок; langfuse queue — потеря | безопасен | контейнер redis; алерты добавить до launch |
| 3 | external API timeout | частично: ACME retry 2× без backoff (0302) | частично | серт может остаться self-signed на 90д (0301) | повторный выпуск возможен, но жжёт LE rate-limit | ручной issue_cert; пауза между попытками |
| 4 | network partition | watchdog unhealthy≥10мин — только внутрь стека (0402) | partial; GitHub-partition при bootstrap = тихо зелёный пайплайн (0608) | нода без context-overlay/vhosts (0608) | повторный converge безопасен (NB-lock, 0907✓) | `make converge NODE=` |
| 5 | process crash | да: restart policies + watchdog */5 | **ДА** (лучше всего покрыто) | crash-loop без exit у hermes не лечится docker'ом (0900) | n/a | `make converge` R9 чинит hang |
| 6 | machine restart | частично: daemon поднимает всё, порядок НЕ гарантирован (0401) | partial: dead-man's switch отсутствует (0402) | транзиентный crash-loop зависимых; окно LLM 5xx ~45–60с (0404) | reboot-drill до launch обязателен (политика авторебута УЖЕ активна, 0400) | drill; затем `make check NODE=` |
| 7 | disk full | нет: postgres первый отказник (ENOSPC pg_wal → crash-loop, 0502) | слепнет ВМЕСТЕ с монитором (0504 noDataState OK); канарейка BackupFreshness ~24ч | полный outage БД до ручной чистки; данные crash-safe (P0 закрыт) | чистка → рестарт | prune образов (сейчас monthly — мало), retention.size; `docker system df` |
| 8 | memory pressure | host-OOM выберет postgres/clickhouse по RSS (0511) | да, но с дырой 0504 | убитый postgres = outage соседей | рестарт | фикс `oom_score_adj:-500`; zram 4G уже есть |
| 9 | malformed external response | зависит от парсера (кластер MED 03xx/06xx) | частично | битые промежуточные артефакты возможны | обычно безопасен | перезапуск шага |
| 10 | duplicate request (деплой) | барьера НЕТ: concurrency-group нет (0700), копирование payload вне lock (0701), lock молча деградирует после root-деплоя (0702) | **НЕТ** — смесь файлов при зелёном CI | mixed payload: history пишет один sha, файлы — смесь | чистый redeploy последней версии полностью чинит | concurrency+lock фиксы до launch; сверка файлов с git |
| 11 | corrupted state | нет общего механизма | частично | restore без ON_ERROR_STOP льёт в живой кластер и говорит «complete» (0803) | redeploy безопасен; restore — только по runbook | pre-restore дамп руками до фикса |
| 12 | migration failure | платформенный auto-migrate фейл severity=normal → node-update зелёный (0805) | ДА (по матрице alerts) | half-migrated схема litellm/langfuse возможна | fix-forward канон | логи litellm Prisma/langfuse CH при node-update смотреть явно |
| 13 | rollback | образ-only: схема БД/vhosts/secrets/buckets ВНЕ периметра (T9.8 подтверждён кодом, 0806–0808) | частично: rollback healthcheck НЕ верифицируется повторно (0804) | старый код против новой схемы недопустим → только fix-forward | fix-forward коммит; предыдущий sha — вручную через deploy-project | не рассчитывать на auto-rollback как на полную отмену |
| 14 | worker crash | backup-cron `always` ✓ (0901); export-metrics verified-safe ✓ (0902); hermes hang без exit НЕ рестартует (0900) | **НЕТ** (слепая зона матрицы alerts) | — | converge R9 | `make converge NODE=` |
| 15 | queue backlog | eviction молча убивает очередь (0200 allkeys-lru) | **НЕТ**: jobs langfuse в prometheus отсутствуют (0204) | тихая потеря трейсов | noeviction + drain вручную | алерт evictions до launch |
| 16 | interrupted long task | bootstrap `--resume` докатывает ✓ (0906); converge NB-lock ✓ (0907) | частично | partial dump в spool неотличим от хорошего (0903); CI-receive timeout auto-retry поверх брошенного деплоя (0703) | resume безопасен; исключение — retry после таймаута receive | проверить дамп `gzip -t`; redeploy вместо доверия таймауту |
| 17 | expired credential | GHCR login non-fatal → анонимный pull фасадов (0602) | НЕТ до первого падения pull | новый проект FATAL exit 10 (усиливается B1/0801) | refresh токена | expiry-check токена; AGE-key-backup DR (B5/0600) |

## Слепые зоны мониторинга (авто-детект 5/17)

Детектируются автоматически: process crash, disk full*, memory pressure*, migration failure,
rollback (*с оговоркой noDataState 0504).
Слепые зоны: **duplicate request, worker crash, queue backlog** + сквозные:
postgres за живым exporter'ом (1001), minio (1000), смерть loki/alloy-пайплайна видна
только через ~31ч канарейкой BackupFreshness (1002), низкотрафиковый vhost с 502 невидим (1006).

## Операторский cheat-sheet

| Симптом | Действие |
|---------|----------|
| Нода после reboot, стек деградировал | подождать docker daemon → `make status NODE=<n>` → `make converge NODE=<n>` |
| Деплой PARTIAL, а CI зелёный | НЕ считать успехом (0102): `make project-status PROJECT=<p> NODE=<n>` → redeploy |
| Postgres down / проекты без БД | рестарт модуля postgres; pgbouncer проверять отдельно (алерта нет); хук БД мог не создаться (B2) |
| HTTPS outage / близко истечение серта | выпуск вручную cert_orchestrator; проверять ОБА каталога: `/root/.acme.sh` И `/etc/letsencrypt/live` (до фикса B4) |
| Подозрение на битый payload ноды | чистый redeploy последнего sha; diff файлов `/opt/projects/<p>` против git |
| Нужен restore БД (до фикса 0803) | остановить писателей → pre-restore dump → `psql -v ON_ERROR_STOP=1`; не доверять «Restore complete» |
| Диск >80% | `docker system df`; prune dangling-образов; объёмы pg_wal/clickhouse/minio/prometheus |
| Тишина в Telegram | канал Tor→Privoxy мог умереть (0303 SPOF): tor service; CI-notify идёт напрямую мимо Tor |
| Потеря/компрометация AGE ключа | есть ли off-node копия? (если B5 ещё не выполнен — секреты невосстановимы) |
| Bootstrap/converge прервался | `make bootstrap-node ... --resume` (0906✓) / повторный converge (0907✓) |

## Остаточные риски (принято, runbook/post-launch)

- FAIL-0502 · disk-full бьёт postgres первым — смягчается батчем A/B (alerts, prune weekly, retention.size)
- FAIL-0301/0302 · self-signed fallback без алерта; ACME retry без backoff — после B4 полиш
- FAIL-0303 · все TG через Tor→Privoxy = SPOF канала — обход: notify-ci direct; внешний heartbeat (батч A) закрывает детект
- FAIL-0401 · boot-ordering окно — принять ПОСЛЕ reboot-drill с замером; опц. boot-unit 10 строк
- FAIL-0900 · hermes hang — лечится ручным converge; алерт есть
- FAIL-1006/1005/1007 · vhost-502 невидим, /healthz никто не опрашивает, retention Prom 15d/Loki 7d — post-launch
- FAIL-0406 · prune `-af until=720h` vs rollback-образы >30д — согласовать с lifecycle.py:88
- FAIL-0704/0705 · remove→replace окно; мусорные tmpdir payload — рядом с фиксом 0701
- HYPOTHESIS: 0407 (tor без Restart drop-in — `systemctl show tor -p Restart`), 1008 (пустой LITELLM_MASTER_KEY → ложный ServiceDown)
- Покрытие аудита: corrupted-state (сценарий №11) покрыт напрямую — findings-duplicate-state-002.md
  (FAIL-0706–0712: битый compose сквозь L1-gate, мёртвый orchestrator-rollback 0707, half-applied
  vhosts 0709, stale-compose после rename 0711); one-shot контейнеры и hooks идемпотентны (0712✓),
  остаточный риск только конкурентный запуск — покрыт кластером 0700/0701/0702.
