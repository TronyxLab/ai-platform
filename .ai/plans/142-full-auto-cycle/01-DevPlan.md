# 142-full-auto-cycle — 01-DevPlan.md

$START_DEVPLAN

$ARTIFACT_CONTRACT
PURPOSE:               Третий полный цикл тестирования платформы (голый сервер → штатная работа) должен пройти БЕЗ ручного вмешательства. Все ручные действия 1-го и 2-го циклов (141-server-recovery) автоматизируются или получают платформенный self-heal; каждый фикс — с R5-тестом; итоговый артефакт — промт повторного прогона.
DESCRIPTION:           Реестр REMAINS-MANUAL из анализа 2 циклов (A1-A6, B21, A4, A5, B26 + мелкие) → 8 волн фиксов (W1-W8): CI-root ключ в бутстрап, tmpfs reboot-устойчивость, TSDB self-heal, node-detect устойчивость, fallback-таргет CI, privoxy/firewall дрейф, детерминизм-мелочи, промт прогона.
RATIONALE:             Каждый из двух циклов 141 требовал ~10+ ручных SSH-действий на ноде (ключи, конфиги, TSDB, tmpfs-файлы) — повторение подтверждает системные пробелы, а не разовые баги. Третий прогон на том же коде даст тот же результат; нужны код-фиксы, а не новый прогон.
ACCEPTANCE_CRITERIA:   (1) Прогон 3: 0 ручных SSH-действий на ноде (кроме запуска make-таргетов с dev-машины); (2) core-deploy CI проходит сразу после bootstrap без ручного добавления ключа; (3) после reboot ноды nginx/status-page/прометеус само-восстанавливаются; (4) make check зелёный; R5-тест на каждый фикс; (5) chaos T4/T11 проходят без ручного восстановления.
IMPLEMENTS:            Находки 141-server-recovery (02-VerificationReport §6.4, failures-r2-3.md, транскрипт 2-го цикла).
IMPACTS:               node-configs/tronyx-vps/node.yaml, bootstrap-цепочка (bootstrap.sh → build-ssh-cmd.sh → cli.py → phases/system.py), lifecycle (state_machine, state_store), converge (новый юнит R10), firewall.py, install_tor_proxy.py, verify_sweep-окружение, .github/workflows (build-platform.yml, platform-test.yml), Makefile+entrypoint-manifest (новый глагол core-deliver), core/AGENTS.md глоссарий.
REQUIRES:              Решения Q1-Q6 (приняты 2026-08-06: все «(а)» кроме Q5 — локально); публичная часть VPS_SSH_KEY от оператора в node.yaml (Q1).
$END_ARTIFACT_CONTRACT

---

## 1. Цель и границы

**Цель:** следующий (3-й) прогон полного цикла «голый сервер → штатная работа» — полностью автоматический: все ранее ручные действия исполняются каноническими make-таргетами / CI / self-heal'ом платформы. Ничего «out of scope»: каждый пункт реестра ручных действий получает фикс или явное решение оператора.

**Границы:** платформенный код, CI-воркфлоу, node-configs, тесты, промт прогона. Вне скоупа: внешние инциденты GitHub (неустранимы — покрываются fallback W5), смена SSH host-key при реинсталле (покрывается промтом: accept-new).

## 2. Реестр ручных действий (источник: 2 цикла 141)

| # | Действие (что делалось вручную) | Цикл | Корень | Волна |
|---|--------------------------------|------|--------|-------|
| A1 | Добавление pub-ключа `ci-core-deploy` (VPS_SSH_KEY) в `/root/.ssh/authorized_keys` свежей ноды | 1, 2 | φ2 создаёт только ci-deploy forced-command ключ; механизма доставки CI-root ключа нет | **W1** |
| B21 | После reboot: nginx/status-page Exited(127) — `/run/platform` (tmpfs) пуст; ручная регенерация .htpasswd-platform, status-metrics.json, пересоздание контейнеров | 2 (chaos T11) | tmpfs-источники bind-mount'ов не переживают reboot; пересоздаются только φ4/φ9/cron | **W2** |
| A4 | Ручная чистка TSDB Prometheus (wal/blocks) после chaos T4 clock-skew | 2 | сэмплы с будущими timestamp'ами отклоняются; self-heal нет | **W3** |
| A5 | Зачистка мусорного `/opt/node-configs/unknown/` (пустой node.yaml) — node-detect падал «Multiple directories» | 2 | источник не устранён; auto_detect_node_name принимает любой каталог | **W4** |
| A6 | Ручной эквивалент core-deploy при GitHub Outage: rsync core/+scripts/ → provision → node-update (AGE_SECRET_KEY) | 2 | fallback-таргета нет; шаги CI воспроизводились вручную | **W5** |
| A2/A3 | Правка `/etc/privoxy/config` (listen-address на docker-gateway'и) + рестарт; `ufw allow 172.16.0.0/12:8118` | 2 | конфиг privoxy сброшен к 127.0.0.1 (после reboot/переустановки пакета); ufw-правило не в baseline | **W6** |
| B26 | Восстановление `/var/lib/platform/.bootstrap/state.json` (исчез, механизм не выявлен) | 2 | неизвестен; аудит-следа нет | **W7** |
| B8 | `_phase_input_hash` — json.loads на YAML node.yaml → content-hash фаз не работает | 1, 2 | баг парсинга | **W7** |
| T9 | chaos-тест ждёт `age` CLI на ноде — не установлен | 2 | φ1 не ставит age | **W7** |
| R10 | Build Platform Agent smoke: `undefined volume hermes-data` | 1, 2 | volume-декларация в smoke | **W7** |
| B1 | known_hosts после реинсталла (host-key сменился) | 1, 2 | ожидаемо при реинсталле | промт |
| R4 | NODE_HOST_MAP не задан (deploy-project CI через inputs.host) | 1, 2 | одноразовая настройка GitHub | промт |
| R11 | hermes-push denied (GITHUB_TOKEN vs public-пакет) — нужен PAT | 1, 2 | GitHub-ограничение | **решение Q5: остаётся локальным** |
| R13 | platform-test ci-docker фаза давно красная | 1, 2 | dev-стек / docker-гейт | **W8** |

**Уже автоматизировано (фиксы 141 — не трогаем):** B18-B24 (orphan, docker ps -a, NGINX_OVERLAY_DIR, practices.lock whitelist, contact-points parse_mode/chatid, NO_PROXY), REQ_FIX scripts/-доставка (a4218f38).

## 3. Суперпозиция: стратегии достижения «0 ручных действий»

Пять стратегий, от точечной к системной (комбинируются, финальная рекомендация — A+D+B+C):

### Вариант A — «Точечный фикс по реестру» (базовый)
Закрыть каждый пункт REMAINS-MANUAL отдельным код-фиксом (W1-W7). Плюсы: минимальный риск, каждый фикс проверяем R5-тестом. Минусы: не защищает от НОВЫХ ручных действий в 3-м цикле; «дыра» в следующем reboot может проявиться снова в другом месте.

### Вариант B — «Гейт автономности» (регрессия)
Для каждого ранее-ручного действия — R5-тест (negative + positive) И пункт в e2e-сьюте/chaos, который в прогоне 3 обязан пройти без ручного восстановления (T4 → assert self-heal TSDB; T11 → assert nginx/status-page healthy после reboot без вмешательства). Плюсы: фиксы не деградируют. Минусы: усилия на тесты.

### Вариант C — «Промт-оркестратор» (артефакт прогона)
Итоговый промт 3-го прогона: полный цикл голый сервер → штатная работа автономной сессией, с сигналами, критерием «0 ручных SSH-действий» и чек-листом бывших ручных шагов. Плюсы: воспроизводимость прогона как одного действия. Минусы: сам по себе не убирает ручные действия — только делает их видимыми.

### Вариант D — «Платформенный self-heal» (системный)
Нода сама восстанавливается после reboot/сбоев: tmpfs-файлы регенерируются (W2), prometheus чинит TSDB (W3), converge R-юниты детектят и лечат BAD-состояния (уже частично: R9). Плюсы: прогон 3 проходит даже при «случайностях»; продуцирует и пользуется прод. Минусы: больший скоуп.

### Вариант E — «Полный редизайн bootstrap/CI» (отвергнут)
Переписать каналы доставки (ключи/конфиги) заново. Отвергнут: 2 цикла 141 показали, что канонические каналы работают (receive, rsync, forced-command) — ломались именно отсутствующие звенья, а не архитектура. Риск регрессии неприемлем (урок: 141 B4/B9 — каскад от B-фиксов).

**Рекомендация: A + D как ядро (фиксы + self-heal), B как R5-гейты, C как итоговый промт (W8).**

## 4. Волны

### W1 — CI-root ключ в бутстрап (A1)
- **node.yaml**: новое поле `node.ci_root_key` (ПУБЛИЧНАЯ часть VPS_SSH_KEY — не секрет, канон как `owner_key`).
- **bootstrap.sh**: `--get-many` + `ci_root_key` (по образцу ci_deploy_key:120-124); локальная ветка — `--ci-root-key`, remote — через build_ssh_cmd.
- **build-ssh-cmd.sh**: `build_ssh_cmd` принимает 5-й ключ (printf %q), экспортирует `PLATFORM_CI_ROOT_KEY` (fallback env → param, как ci_deploy_key TRAP[BUG] P2).
- **cli.py**: `--ci-root-key` → `PLATFORM_CI_ROOT_KEY` (semi-optional + WARN, как ci-deploy ключ, строка 199-201).
- **phases/system.py φ2**: `add_ssh_key("root", key)` — идемпотентный append в `/root/.ssh/authorized_keys` (существующие строки не дублируются; owner-root).
- **node-configs/tronyx-vps/node.yaml**: добавить поле (значение от оператора, Q1).
- **R5**: unit φ2-ветки (monkeypatch authorized_keys), unit build_ssh_cmd (вывод содержит --ci-root-key), e2e-проверка: core-deploy проходит сразу после bootstrap.
- ⚠️ S7 security_posture: authorized_keys root не входит в S7-проверку (S7 — только ci-deploy) — не конфликтует.

### W2 — tmpfs /run/platform reboot-устойчивость (B21)
- **Решение Q2** (вариант «а» рекомендуемый): перенос артефактов из `/run/platform` в persistent `/var/lib/platform/run`:
  - `SECRETS_ENV_FILE`, `HTPASSWD_FILE`, `STATUS_METRICS_JSON`, watchdog-state — дефолты меняются на `/var/lib/platform/run/*` (дефолты в 10+ модулях: decrypt_secrets, secrets_manager, htpasswd, platform_export_metrics, watchdog, json_writer, telegram_notifier, compose_preflight, nginx/status-page compose bind-пути);
  - **ВАЖНО**: secrets.env переживает reboot (AGE-ключ недоступен на boot по канону S-13 — только persistent dir решает полностью);
  - локальный dev (macOS): env-переопределения уже существуют (STATUS_METRICS_JSON/HTPASSWD_FILE) — dev-локали сохраняются;
  - nginx/status-page compose: bind-пути → новые дефолты (с сохранением `${VAR:-}` параметризации).
- Альтернативы (если Q2 выберет): (б) systemd-tmpfiles.d + oneshot-юнит регенерации на boot (не решает secrets.env без AGE-ключа на ноде); (г) расширение liveness-probe T9.17 (только при вызове bootstrap, не при голом reboot).
- **R5**: chaos T11 — assert: после reboot nginx/status-page healthy БЕЗ ручных действий; unit: дефолты путей (гейт no_hardcoded_local_paths учёт новых путей).

### W3 — Prometheus TSDB self-heal после clock-skew (A4)
- **Платформенный юнит** (рекомендация Q3): новый converge-юнит `R10` (converge/monitoring.py или расширение runtime.py): для контейнера prometheus — если `docker logs` содержит «too far into the future»/«out of bounds» И targets недоступны → backup + очистка `wal/` и `blocks/` (только при детектированном коррапте!) + restart контейнера; иначе no-op. Строгая guard-логика — НЕ чистить здоровый TSDB.
- **Chaos T4**: после восстановления часов — health-probe prometheus; при коррапте assert, что R10-механизм (converge) восстановил метрики (или T4 выполняет платформенный self-heal вызов — не ручную чистку).
- **R5**: unit (эмуляция логов/состояния контейнера), chaos T4 assert.

### W4 — node auto-detect устойчивость (A5)
- **Расследование источника** `unknown/`: mtime 09:17Z + аудит-логи bootstrap; проверить core_deliverer/scp-deliver/node-lifecycle на создание каталогов по умолчанию; при нахождении — устранить источник.
- **Защитный фикс** (независимо от источника): `auto_detect_node_name` — кандидат = каталог, содержащий валидный `node.yaml`; junk-каталоги (без node.yaml) пропускаются с WARN; «Multiple directories» — только при >1 ВАЛИДНОМ кандидате.
- **R5**: unit node_detect (junk dir пропускается; 2 валидных — ошибка с перечислением).

### W5 — Fallback-таргет CI-канала (A6)
- Новый канонический глагол **`core-deliver`**: `make core-deliver NODE=<n>` — локальное зеркало core-deploy.yml: rsync core/ + scripts/ + makefiles/ + platform-env.yaml → `/opt/platform` (guard'ы как в workflow) → ssh `make provision SCOPE=networks,volumes` → `make node-update NODE=<n>` (AGE_SECRET_KEY из локальной цепочки). Использование: GitHub Outage / ручной деплой.
- Регистрация: Makefile .PHONY + `generate-entrypoint-manifest` (allowed_verbs) + глоссарий root AGENTS.md (G4) + `core/AGENTS.md` canon_table.
- ⚠️ Имя: НЕ `push-core` (forbidden-список) — `core-deliver` новое, не конфликтует.
- **R5**: gate-тесты глоссария проходят; dry-run режим (--dry-run) без мутаций.

### W6 — Privoxy/firewall дрейф (A2/A3)
- **Расследование** (первый шаг): воспроизвести — reboot → `ss -tlnp | grep 8118`; проверить, кто перезаписывает `/etc/privoxy/config` (dpkg-обновление privoxy? конфиг-файл пакета?); проверить почему `0.0.0.0` не принялся в 2-м цикле.
- **Фикс 1** (идемпотентный re-apply): tor-подшаг φ1 переиспользуем в update-режиме — добавить `write_privoxy_config` в φ11 (registry-update) с no-op при корректном конфиге (механизм 119 D3 уже идемпотентен).
- **Фикс 2** (firewall baseline): firewall.py — при TOR_ENABLED декларативное правило `ufw allow from 172.16.0.0/12 to any port 8118` + сверка в verify-шаге (вместо ручного ufw allow).
- **R5**: unit firewall (правило в baseline), unit privoxy_config (no-op на каноничном конфиге), e2e: после reboot telegram-доставка работает без ручных правок.

### W7 — Детерминизм-мелочи (B8, T9, R10, B26)
- **B8**: `state_machine._phase_input_hash` — `json.load` → `yaml.safe_load` (node.yaml YAML) + unit-тест на реальном node.yaml.
- **T9**: φ1 apt-пакеты += `age` (или скорректировать chaos-тест T9 — решение по минимальной правке).
- **R10**: build-platform.yml smoke — volume `hermes-data` декларация (bind `/var/lib/platform/hermes-agent/data`): починить compose-инвокацию smoke-степа.
- **B26**: расследование (state_store save/reset/--force пути; возможно связано с cleanup converge или chaos T11) + защита: аудит-запись (audit.jsonl) при пересоздании/удалении state.json + unit-тест сохранения.

### W8 — ci-docker гейт (R13 + B25, решение Q6: включён)
- **Диагностика**: локальный `make gate MODE=ci-docker` — собрать полный список падений (платформенный батч, не per-file). Ожидаемые кандидаты: B25 (dev status-page unhealthy — STATUS_METRICS_JSON на macOS недоступен, TRAP T10 «NODE игнорируется» локальным healthcheck), smoke/component/predeploy-docker расхождения с dev-стеком.
- **Фиксы**: (а) B25 — dev-локали status-page (HTPASSWD/STATUS_METRICS_JSON env уже параметризованы — проверить дефолт для macOS и compose-параметризацию); (б) прочие падения по результату диагностики — батч-фикс, каждый с R5-тестом.
- **Приёмка**: `make gate MODE=ci-docker` зелёный локально и в platform-test.yml (CI-прогон).

### W9 — Промт повторного прогона (артефакт, C-стратегия)
Полный промт 3-го прогона (см. Приложение А) — автономная сессия: Фаза 0-6 по образцу 141 + сигналы + критерий «0 ручных SSH-действий» + чек-лист бывших ручных шагов (A1-A6, B21, A4, A5) с требованием прохождения каноническими каналами.

## 5. Спорные вопросы — РЕШЕНО оператором (2026-08-06)

| # | Вопрос | Решение | Последствие для плана |
|---|--------|---------|----------------------|
| Q1 | Источник CI-root ключа (A1) | **(а)** поле `node.ci_root_key` в node.yaml | W1 как описано; оператор кладёт публичную часть VPS_SSH_KEY в node-configs/tronyx-vps/node.yaml |
| Q2 | tmpfs-фикс (B21) | **(а)** persistent `/var/lib/platform/run` | W2 как описано (широкий дифф дефолтов — гейт дефолтов + e2e T11) |
| Q3 | TSDB self-heal (A4) | **(а)** converge-юнит R10 + T4-assert | W3 как описано |
| Q4 | Fallback-таргет (A6) | **(а)** глагол `make core-deliver` | W5 как описано (+глоссарий/manifest) |
| Q5 | hermes-push (R11) | **(б)** остаётся локальным | CI-путь push НЕ чинится; пункт закрыт решением, не кодом |
| Q6 | ci-docker фаза (R13) | **(а)** включить в план | новая W8 (диагностика + фиксы + B25) |

## 6. Критерии приёмки

1. `make check` зелёный (после W1-W9).
2. Прогон 3: **0 ручных SSH-действий** на ноде; чек-лист A1-A6/B21/A4/A5 пройден каноническими каналами.
3. core-deploy (CI) проходит автоматически сразу после bootstrap — без ручного добавления ключа.
4. chaos T11: после reboot нода самовосстанавливается (nginx/status-page/прометеус) без вмешательства; T4: TSDB восстанавливается.
5. R5-тест на каждый фикс W1-W8; entrypoint-manifest/глоссарий консистентны (make check-manifests); `make gate MODE=ci-docker` зелёный (W8).
6. Приложение А (промт) готово к запуску (W9).

## 7. Коммит-политика (U-83)

- `docs(142): full-auto-cycle DevPlan` — документация.
- `feat(142): <wave> implementation` — по волне (W1-W9 раздельно, audit-trail).

## 8. Риски

- W2 (перенос tmpfs-путей) — широкий дифф дефолтов (10+ модулей): риск рассинхрона dev/прод → покрыть гейтом дефолтов и e2e T11.
- W6 — корень сброса privoxy-конфига может оказаться внешним (dpkg): фикс может потребовать dpkg-divert — в скоупе, но с пометкой «после репродукции».
- W3 — guard-логика очистки TSDB должна быть строгой (не чистить здоровый) — unit-тесты обязательны.
- B26 — механизм может не воспроизвестись: защитная аудит-запись + мониторинг в прогоне 3.

$END_DEVPLAN

---

## Приложение А — Промт повторного прогона (финализируется в W9 после реализации)

```
# ТРЕТИЙ ПОЛНЫЙ ЦИКЛ: голый сервер → штатная работа — БЕЗ ручного вмешательства

## Роль и цель
Ты — главный оператор ai-platform. Сервер tronyx-vps (103.88.243.151) переустановлен (голый).
Задача: прогнать ПОЛНЫЙ цикл сценариев от нуля до штатной работы АВТОМАТИЧЕСКИ.
ЖЁСТКИЙ КРИТЕРИЙ: 0 ручных SSH-действий на ноде. Каждое действие — каноническим
make-таргетом / CI / self-heal'ом платформы. Если понадобилось ручное SSH-вмешательство —
это БАГ плана 142: зафиксировать, НЕ чинить обходом (кроме 1 ретрая).

## Чек-лист «было ручным — стало автоматическим» (каждый пункт ОБЯЗАН пройти без рук)
1. CI-root ключ: после bootstrap `gh workflow run core-deploy.yml` (dispatch) → SUCCESS
   (НЕ добавлять ключ в authorized_keys вручную — φ2 делает это сам, W1)
2. tmpfs: после reboot ноды (chaos T11) nginx/status-page healthy БЕЗ регенерации файлов (W2)
3. TSDB: после chaos T4 (clock-skew) prometheus восстанавливает метрики через converge (W3)
4. node-detect: core-deploy/node-update не падают на мусорных каталогах node-configs (W4)
5. GitHub Outage: при недоступности Actions — `make core-deliver NODE=tronyx-vps` (W5)
6. Privoxy: telegram-доставка работает после reboot без правок конфига (W6)

## Фазы (как 141, сигналы/тайминги/телеграм — по образцу)
Фаза 0 префлайт → Фаза 1 make check + push → Фаза 2 bootstrap (make bootstrap-node,
NODE_PREBOOTSTRAPPED не нужен — preflight «голоты» сам) → Фаза 3 node-update + converge +
deploy-project ×4 (2 через CI dispatch) → Фаза 4 сертификаты (S3-кеш) + e2e-verify +
auth-matrix → Фаза 5 grafana/loki/telegram/LLM-проба → Фаза 6 отчёты
(02-VerificationReport, 04-TimingsReport, 05-TelegramSummary, 03-browser-checklist).
Chaos-сьют (T1-T11) — после полного бутстрапа, параллельно Фазе 4, НЕ пересекать с e2e-verify.
Конец: финальный вердикт + чек-лист «0 ручных действий» со ссылками на доказательства.
```

$END_DEVPLAN
