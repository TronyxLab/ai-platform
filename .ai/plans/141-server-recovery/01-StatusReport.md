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
