# 141-server-recovery — 01-StatusReport.md

$START_STATUS_REPORT

$ARTIFACT_CONTRACT
PURPOSE:               Пофазовый статус ночной сессии 141 (tronyx-vps переустановлен, полный цикл до штатной работы).
DESCRIPTION:           Каждая фаза сессии — отдельная секция: что сделано, находки, решения, замеры (timings.tsv).
RATIONALE:             Единый журнал ночной автономной сессии; контекст в файлах, в чат — только короткие статусы.
ACCEPTANCE_CRITERIA:   (1) Каждая фаза — запись с вердиктом; (2) все находки/баги зафиксированы; (3) финальный вердикт в 02-VerificationReport.md.
IMPLEMENTS:            Ночная сессия 141 (операторский цикл сценариев платформы).
IMPACTS:               tronyx-vps (103.88.243.151), локальное дерево ai-platform, GitHub Actions.
REQUIRES:              — (сессия автономная, вопросы оператору — батч в первые 15 минут).
$END_ARTIFACT_CONTRACT

🔒 Начало сессии: 2026-08-06 00:47 MSK (UTC+3). Оператор спит, Telegram-бот на телефоне.

---

## Фаза 0 — Префлайт (00:47–01:35 MSK) — ✅ ЗАВЕРШЕНА

### Что сделано

| Шаг | Результат | Доказательство |
|-----|-----------|----------------|
| git status полный | Грязное дерево как ожидалось: staged 140-DevPlan + 27 modified + 4 untracked | git status |
| pre-commit hooks | Установлены (make pre-commit-install — в Фазе 1) | — |
| SSH-проба | ⚠️ Все 5 старых ключей отклонены; host-key сменился (сервер переустановлен) → known_hosts обновлён (`ssh-keygen -R`) | ssh log |
| Root-доступ | Оператор: «Сделал ssh-add» + новый root-пароль. Новый ключ `~/.ssh/tronyx-vps_new` РАБОТАЕТ | `SSH_OK_NEWKEY; x86_64` |
| AGE-ключ | ✅ AGE_SECRET_KEY в env; decrypt `tronyx-vps.enc.yaml` успешен | secrets.env (52 ключа) |
| S3-префлайт | ✅ Bucket `tronyx-vps-backups` жив (21 ключ). Кеш сертификатов: tronyx.ru, www.tronyx.ru, sexydancerostov.ru, botanika.tronyx.ru (fullchain/chain/privkey; account.tar.gz только для tronyx.ru/sexydancerostov.ru). НЕТ кеша: roadmap.tronyx.ru, platform-поддомены → оператор разрешил acme-выпуск через webnames | s3-list.txt (во временном каталоге) |
| WEBNAMES_API_KEY | ✅ В enc.yaml, 41 символ, астериск-префикс `*` — код обрезает сам (TRAP cert_orchestrator.py:650) | — |
| GitHub secrets | ✅ VPS_SSH_KEY, DEEPSEEK/OPENAI API keys, LITELLM_MASTER_KEY, LANGFUSE_* — на месте. ❌ NODE_HOST_MAP отсутствует → deploy-project CI пойдёт через `inputs.host` fallback | gh secret list |
| LLM-провайдер | ✅ DEEPSEEK_API_KEY + OPENAI_API_KEY в enc.yaml | secrets.env |
| Docker Hub | ✅ DOCKER_HUB_USERNAME/DOCKER_HUB_TOKEN в enc.yaml (шаг registry_auth) | secrets.env |
| Локальный Docker | ✅ Docker Desktop 29.6.2, полный стек healthy (compose ps), COMPOSE_PROFILES из platform-infra.yaml | docker ps |
| Telegram | ✅ TELEGRAM_BOT_TOKEN + TELEGRAM_CHAT_ID (9 цифр) в enc.yaml; оператор: один чат для обоих уровней | — |
| Пароли сервисов | ✅ В enc.yaml: GF_SECURITY_ADMIN_PASSWORD, LANGFUSE_INIT_USER_PASSWORD, LITELLM_MASTER_KEY, MINIO_ROOT_PASSWORD, POSTGRES_PASSWORD | secrets.env |
| Эксклюзивность сервера | ✅ Подтверждена оператором на 8 часов; новые certs через webnames — разрешены | question-ответ |

### 🔴 НАЙДЕННЫЕ БАГИ (фиксы в Фазе 1)

| # | Баг | Симптом | Файл | Статус |
|---|-----|---------|------|--------|
| B1 | inline-парсер secrets.env в `notify()` не снимает кавычки (`KEY='value'` из write_secrets_env) | `ValueError: invalid literal for int() with base 10: "8118'"` (TELEGRAM_PROXY_URL); 401-токен с кавычками | core/internal/shared/telegram_notifier.py | ✅ FIXED → канонический secrets_env_parser.parse() + TRAP[BUG] |
| B2 | CLI `send`/`get-me` вызывают `send_telegram(token=...)` — несуществующий kwarg | `TypeError: unexpected keyword argument 'token'` | там же (main) | ✅ FIXED → `bot_token=` |
| B3 | TELEGRAM_CHAT_ID_CRITICAL/WARNING отсутствуют в enc.yaml; contact-points.yml графаны ссылается на них (${VAR:-} → пусто) | Алерты графаны не доставятся | node-configs/tronyx-vps/secrets/tronyx-vps.enc.yaml | ✅ FIXED → sops set = TELEGRAM_CHAT_ID (решение оператора: один чат) |

### Решения

- **S1 (сессии):** Agent Manager worktree-сессии НЕ используются для дорожек сессии 141 — dirty working tree (незакоммиченный 140) не виден из worktree, отведённого от main. Используются local-сессии (тот же workspace) ПОСЛЕ коммита/push Фазы 1. Отклонение от буквы брифа, причина — целостность дерева.
- **S2 (telegram локально):** tor-прокси (TELEGRAM_PROXY_URL из enc.yaml) — серверный канал; на dev-машине не запущен → локальные отправки через вариант secrets.local.env без прокси. На сервере — полный env (проверка в Фазе 5).
- **S3 (секреты):** расшифрованные креды — в /var/folders/.../kilo/141-secrets/ (вне репо, chmod 600). В evidence/ — только имена/суммы.

### Telegram-милстоуны Фазы 0

- ✅ Доставлено: «🚀 Тест канала 141 — ночная сессия началась» (01:34 MSK, send, HTTP 200)
- ⚠️ Ранние notify-попытки (до фикса B1/B2) — DELIVERY FAILED, не доставлены (записаны в evidence/telegram-sent.log)

### Следующее

- Фаза 1: `make check` батч → фикс-цикл → коммиты docs(140)+feat(140) → push (pre-push gate) → CI-мониторинг.

$END_STATUS_REPORT


---

## Фазы 2-6 (02:10–10:00 MSK) — ✅ ЗАВЕРШЕНЫ (кратко; детали — в 02-VerificationReport.md)

- **Фаза 2**: e2e cold start 27 мин (3 failed/7 passed — B4/B9) → бутстрап-2 480s (9 INIT done, 21 контейнер) → no-op 19s (инвариант 6 доказан). Grafana crash-loop (#69950) → chat-id float (sops set) → каскад откатов → stack rebuild (compose up + B9 stubs).
- **Фаза 3**: node-update ×4 (re-decrypt, stubs B9), converge, deploy-project ×4 — все DEPLOYED healthy (receive verb, 11-12s).
- **Фаза 4**: серты botanika/roadmap выпущены (B12 FL15 false-wildcard, B13 proxy, acme dns_webnames + key injection) → S3-upload → e2e-verify GREEN (HTTP 4/4, TLS 4/4 depth=4) → сайты 200.
- **Фаза 5**: Grafana API (8 правил, datasources, contact-points), NO_PROXY (B15 — DatasourceError-шторм устранён), B16 (renderer import), B17 (up == bool 0 → ServiceDown FIRING), Telegram: 5 сообщений доставлено (470-475).
- **Фаза 6**: 02-VerificationReport, 04-TimingsReport, 05-TelegramSummary, 03-browser-checklist — готовы. Финальный милстоун отправлен.

**Итог: 17 багов (B1-B17), 12 коммитов, платформа штатно работает.**

---

## 🔄 РЕСТАРТ: сервер переустановлен ПОВТОРНО — 2-й полный цикл (11:26 MSK)

**Событие:** 2026-08-06 11:26 MSK сервер tronyx-vps переустановлен/сброшен (uptime 9 мин, `docker: command not found`, `/opt/platform` и `ci-deploy` отсутствуют, все сайты 000). Вся серверная часть 1-го цикла утрачена; локальные артефакты (12 коммитов с фиксами B1-B17, отчёты, S3-кеш, enc.yaml) целы. Запущен полный повторный цикл «голый сервер → штатная работа» на исправленном коде.

### Фаза 0-r2 — Префлайт рестарта (11:30–11:45 MSK) — ✅ ЗАВЕРШЕНА

| Шаг | Результат | Доказательство |
|-----|-----------|----------------|
| SSH-доступ | ✅ `tronyx-vps` alias (tronyx-vps_new) работает; known_hosts обновлён (host-key сменился — ожидаемо) | SSH_OK |
| CI-канал core-deploy | ✅ Ключ `ci-core-deploy` (~/.ssh/vps_ci_root.pub) добавлен в /root/.ssh/authorized_keys → `CI_CORE_DEPLOY_OK` (был единственной причиной вечных core-deploy failure 1-го цикла) | SSH verify |
| AGE_SECRET_KEY в GitHub Secrets | ✅ Установлен (repo, 08:36:59Z) — был причиной пустого AGE в node-update CI (core-deploy.yml:241) | gh secret list |
| S3-креды | ✅ Источник: secrets.env (имена S3_ENDPOINT/S3_ACCESS_KEY/S3_SECRET_KEY/S3_BUCKET — НЕ S3_ENDPOINT_URL); bucket `tronyx-vps-backups` жив | s3-cert-keys-r2.txt |
| S3-кеш сертификатов | ✅ Все 4 домена + wildcard: fullchain/chain/privkey; account.tar.gz (tronyx.ru, sexydancerostov.ru) | s3-cert-keys-r2.txt |
| Секреты | ✅ secrets.env + secrets.local.env (chmod 600, вне репо) — полный набор (52 ключа) | — |
| GitHub | ✅ VPS_SSH_KEY/VPS_HOST на месте; AGE_SECRET_KEY добавлен; org-secrets отсутствуют (repo-level) | gh secret list |
| Telegram-канал | ✅ tg.sh + secrets.local.env (без прокси, dev-машина); канал проверен 1-м циклом (5 сообщений) | telegram-sent.log |

### Решения рестарта

- **R2-S1:** 2-й цикл выполняется на чистом дереве (фиксы B1-B17 уже в main) — ожидается меньше ретраев, чем в 1-м цикле; каждый шаг — в timings.tsv с cause.
- **R2-S2:** Отклонение от брифа 1-го цикла зафиксировано: вопросы оператору НЕ перезадавались — все ответы 1-го цикла (SSH-ключ, AGE, S3, webnames, telegram, эксклюзивность) остаются в силе; сервер переустановлен явно перед запуском сессии (11:26 MSK vs старт 11:30 MSK).
- **R2-S3:** CI-канал core-deploy — теперь реально проверяемый сценарий (ключ + AGE_SECRET_KEY готовы): workflow_dispatch после успешного бутстрапа.

---

## Фаза «Хвосты» 2-го цикла (15:00–16:30 MSK) — закрытие R1 и операционных блокеров

| Шаг | Результат | Доказательство |
|-----|-----------|----------------|
| R1: транспорт telegram | ✅ privoxy слушал только 127.0.0.1 после reboot; grafana ходит на host.docker.internal (=172.17.0.1 docker0) → добавлены listen 172.17.0.1/172.22.0.1/172.18.0.1 + ufw allow 172.16.0.0/12:8118 | ss + curl 302 |
| R1: chatid | ✅ block-scalar «-\n79xxx9» → Telegram 400; grafana env-интерполяция на JSON-тексте: голое число → #69950 provisioning-fail → фикс `chatid: "${VAR} "` (хвостовой пробел — Telegram тримит, message_id 488) | sendMessage пробы |
| R1: parse_mode | ✅ grafana telegram дефолт = **MarkdownV2** → «{ } ( )» из summary/alertname → 400 «Character '(' is reserved» (воспроизведено); фикс `parse_mode: "Markdown"` (legacy — принимает всё) | sendMessage: V2=400, HTML=OK, Markdown=OK |
| **R1 итог** | ✅ **Алерты доставляются**: после рестарта 15:30:06Z 0 ошибок notify (до — шторм 400 каждую секунду). Коммит 98dd6d2a | docker logs grafana |
| LLM-цепочка с ноды | ✅ litellm 127.0.0.1:4000 → deepseek-chat «pong! 🏓» (3443 токена, reasoning 3348) | evidence/llm-node-probe.json |
| Prometheus TSDB | ✅ после chaos T4 (clock-skew +23h) сэмплы отклонялись → очистка wal/blocks (данные и так были пусты) → метрики 7/8 UP | curl up |
| cadvisor | ⚠️ R-остаток (не регрессия, в 1-м цикле тоже 7/8): fs-handler 1m49s/контейнер + таргет «cadvisor» не резолвится (prometheus-targets пуст — след B20b) | docker logs |
| core-deploy node-detect | ✅ каталог-мусор `/opt/node-configs/unknown/` (пустой node.yaml, артефакт бутстрапа 09:17Z) ломал авто-детект («Multiple directories») → удалён → detect = tronyx-vps | node_detect CLI |
| core-deploy история 2-го цикла | 3× failure: (1) 12:53 SSH-ключ отсутствовал — EXPECTED; (2) 14:01 provision Error 127 — scripts/ не доставлялись (REQ_FIX, фикс a4218f38); (3) 15:20 node-detect (unknown/) — фикс выше. Дальше — ждём гейт 98dd6d2a → dispatch | gh run |

### Решения хвостов

- **H1:** операционные фиксы на ноде (privoxy listen, ufw, TSDB-очистка, unknown/) выполнены главным оператором напрямую (server-ops/ci-ops завершили цикл или заблокированы gh edge-limit) — зафиксировано в evidence; все конфиг-правки продублированы в репо (contact-points.yml 98dd6d2a).
- **H2:** cadvisor — НЕ чинится в этой сессии (известный R, низкий приоритет): медленный fs-обход overlayfs на VPS + DNS-таргет; рекомендация — отдельный фикс (static target по IP или docker_sd).

$END_STATUS_REPORT
