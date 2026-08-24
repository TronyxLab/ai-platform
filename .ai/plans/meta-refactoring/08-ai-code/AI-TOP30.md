# AI-TOP30 — финальный рейтинг (pre-launch audit · AI-written code patterns)

Ranking = production risk × evidence strength ÷ code churn. Полные данные — findings-001…012.
Sev после верификации; Churn = оценка строк изменений; When = Pre (до запуска) / Post (можно после).

| # | ID | Sev | Суть | Churn | When |
|---|----|-----|------|-------|------|
| 1 | AI-0004 | HIGH·ACTIVE | Alert-rules пишутся в /opt/prometheus/rules, prometheus монтирует /opt/platform/prometheus-rules — алерты молча не загружаются в prod | ~10 | **Pre** |
| 2 | AI-0069 | HIGH | Документация/module.yaml: PostgreSQL 16; реально postgres:18.4 — backup/restore-runbooks против не той мажорной версии | <10 | **Pre** |
| 3 | AI-0011 | HIGH | Крах env-check валидатора → «missing=[]» → деплой без required secrets | ~15 | **Pre** |
| 4 | AI-0006 | MED·cond | receive: замена payload ДО per-project flock; rollback затирает чужой свежий деплой (retry-overlap CI) | ~20 | **Pre** |
| 5 | AI-0001 | HIGH·LATENT | PLATFORM_LANGFUSE_URL=http://langfuse:3001 при контейнере на 3000 — первый же проект с tracing получит connection-refused | <5+regen | **Pre** |
| 6 | AI-0023 | MED·ACTIVE | `make secrets-unlock NODE=X` молча расшифровывает первую по алфавиту ноду | ~25 | **Pre** |
| 7 | AI-0073 | MED | Три boto3-builder'а с разными таймаутами/retry; WAL-sync (RPO) — самые жёсткие | ~25 | **Pre** |
| 8 | AI-0070 | MED | backup-cron: crontab установлен дважды (/etc/cron.d + spool со сломанным полем user) — двойные срабатывания | ~5 | **Pre** |
| 9 | AI-0065 | MED | Двойной канон здоровья: контейнер без HEALTHCHECK = healthy для деплоя, но WARN→FAIL на status-page | ~15 | **Pre** |
| 10 | AI-0020 | HIGH→MED | lib/ssh.sh deploy-default 600s vs SoT 900s + устаревший «parity»-комментарий — cold-node операции рубятся раньше | <10 | **Pre** |
| 11 | AI-0022 | MED | deploy-project.yml руками собирает ssh-флаги без ConnectTimeout; гейт не ловит пропуск | ~6×wf | **Pre** |
| 12 | AI-0013 | MED | `git push --mirror` без таймаута на release-пути context-promote — вечное висение | <10 | **Pre** |
| 13 | AI-0014 | MED | docker login без таймаута — бутстрап-стейт-машина виснет на недоступном registry | <10 | **Pre** |
| 14 | AI-0012 | MED | Один и тот же healthcheck invoke с бюджетами 30s/180s/10s в трёх местах — вердикт зависит от кодового пути | <20 | **Pre** |
| 15 | AI-0063 | MED | Фикс P-14 (полный diff) применён к 1 из 7 копий manifest-check — check-manifests RED снова прячет причину за 20 строками | ~50 | **Pre** |
| 16 | AI-0038 | MED·cond | Phase-input-hash не включает lifecycle/phases/*.py — правки фаз не инвалидируют done-фазы bootstrap/node-update | ~15 | **Pre** |
| 17 | AI-0077 | MED | Сети langfuse: 3-ва расхождения (hermes-agent-net / shared-db-net+observability-net / proxy-net в доках) | ~10 | **Pre** (бандл №5) |
| 18 | AI-0072 | MED | status-page: env_requires мастер-пароль не потребляются модулем; STATUS_PAGE_HOST читается, но никем не задаётся | ~10 | **Pre** |
| 19 | AI-0071 | MED | hermes-agent module.yaml: «NO ports» при реальных 127.0.0.1-mappings, на которые завязан deep-healthcheck | ~10 | **Pre** |
| 20 | AI-0007 | MED | Пароль мастера в argv openssl (world-readable /proc/cmdline) — весь остальной репо на --password-stdin/env | ~10 | **Pre** |
| 21 | AI-0028+0029 | MED | Мёртвые обещания node.yaml: postgres_init_databases и repos.* — схема валидирует то, что никто не исполняет | ~30 | **Pre** (бандл) |
| 22 | AI-0030 | MED | module.yaml#systemd.* парсится схемой, игнорируется инсталлером (hardcode UNIT_NAME) | ~20 | Post |
| 23 | AI-0059 | MED | requires_compose_project: 0 prod-вызовов + ложный docstring про converge | ~20 | Post |
| 24 | AI-0058 | MED | load_existing_manifest мертва, но pin-гейт требует её НЕиспользования — удалить вместе с оговоркой гейта | ~30 | Post |
| 25 | AI-0062 | MED | Orphaned CLI project_registry register/deregister/list — конкурирующий «второй» реестровый CLI | ~40 | Post |
| 26 | AI-0037 | MED | Docstring scaffold: «never auto-creates repos» при реальном gh repo create в Step 7 | <10 | **Pre** |
| 27 | AI-0064 | MED | curl_http_code ×3 с разъехавшимися флагами/-семантикой ошибок; tor-копия глотает OSError | ~20 | Post |
| 28 | AI-0051 | MED | ≥20 скопированных LDD-trajectory блоков; в cert_collector-варианте потерян IMP:9-assert (anti-illusion отключён де-факто) | ~80 mech | **Pre** (дёшево) |
| 29 | AI-0054 | MED | Валидация схем: ajv приоритетнее python-Draft7 при наличии в PATH — «единственная точка» обходится окружением | ~30 | Post |
| 30 | AI-0021 | HIGH-debt | Release-политика пуша образов (нормализация org, версия, dual-tag) живёт только в .mk вне всех language-гейтов | ~40 | Post |

## Дешёвый pre-launch пакет (≤半天 работы, макс. снятие риска)
AI-0004 · AI-0001+AI-0077+AI-0003 (один regen) · AI-0069 · AI-0070 · AI-0007 · AI-0011 · AI-0065 · AI-0022 · AI-0013/AI-0014 · AI-0020 · AI-0037 · AI-0075 · AI-0028/29 · AI-0051

## Явно отложено (HYPOTHESIS / низкий приоритет)
file_lock._REENTRANT threading (без thread-потребителей) · AI-0057 subprocess_io adoption gap (нужен triage) · runtime-поведение почты cron (AI-0070, частично)
