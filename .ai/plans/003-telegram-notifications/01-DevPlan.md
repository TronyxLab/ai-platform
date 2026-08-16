$START_DEVPLAN
$ARTIFACT_CONTRACT
PURPOSE:               Унифицировать Telegram-оповещения платформы: закрыть «тихие отказы» (Тир 1),
                       консолидировать 6+ точек отправки на единый severity-контракт (S2) и добавить
                       out-of-band живучесть (S4). Двусторонние подтверждения (Тир 2 / S3) — ВНЕ скоупа
                       этого плана (отложены решением оператора).
DESCRIPTION:           Волна A — Telegram-хуки на failure() всех CI-воркфлоу (прямой HTTPS, без Tor) +
                       heartbeat-reader (GitHub Actions cron, читает S3) + failure-ветка деплоя.
                       Волна B — единый shared/notifications.py (Notification dataclass, HTML-конверт,
                       единый экранизатор, resolve_chat_id SoT, throttle/dedup-реестр, audit-fallback) +
                       миграция 6 отправителей на severity + notification-catalog (лёгкий S5) + parity-гейт.
RATIONALE:             Два несвязанных механизма доставки (Grafana vs Python) и 4 из 6 Python-отправителей
                       шлют без severity; CI/CD failure никого не уведомляет (::error:: в UI); heartbeat
                       пишется в S3 без читателя. «Тихий отказ» противоречит D-2 (реконструируемость провалов).
ACCEPTANCE_CRITERIA:   1) CI-failure доставляет Telegram critical из GitHub Actions (без ноды).
                       2) Узел умер целиком → heartbeat stale >2ч алертит извне.
                       3) Все Python-отправители шлют через единый notify_event(severity=...), единый конверт.
                       4) Единый SoT severity→chat; parity-гейт Python↔Grafana зелёный.
                       5) Деплой FAILED/ROLLBACK уведомляется (сейчас только success).
                       6) `make check` зелёный; новые модули покрыты unit-тестами.
IMPLEMENTS:            Аудит Telegram-коммуникаций (части 1–4, сессия 2026-08-16); закрывает
                       Failure Matrix-гэпы: CI/CD silent failure, heartbeat stale, Tor-цепь, deploy FAILED.
IMPACTS:               core/internal/shared/{telegram_notifier,notifications}.py, 6 отправителей,
                       .github/workflows/* (6+), core/secret-definitions.yaml, core/AGENTS.md (матрица ключей),
                       core/internal/shared/AGENTS.md (инвентарь), Grafana contact-points.yml (parity).
REQUIRES:              GitHub Secrets: TELEGRAM_BOT_TOKEN + TELEGRAM_CHAT_ID_{CRITICAL,WARNING} (уже в матрице);
                       НОВЫЕ: S3 read-only creds для heartbeat-checker. Python 3.14 stdlib (boto3 — только CI-крон).
$END_ARTIFACT_CONTRACT

# 01-DevPlan — Унификация Telegram-оповещений

**Скоуп:** Тир 1 (закрыть тишину) + консолидация S2 + живучесть S4.
**Вне скоупа:** подтверждения через Telegram (Тир 2 / S3) — отложено, отдельный план позже.

---

## 1. Решения (коллапс суперпозиции — зафиксированы оператором)

| Ось | Решение |
|-----|---------|
| Архитектура | Гибрид **S2 (фундамент) + S1 (CI-хуки) + S4 (живучесть)**. S5 не делаем; вместо полного event-bus — декларативный `notification-catalog.yaml` (лёгкий S5-элемент) |
| Формат | **HTML-конверт + единый `escape_html()`** (parse_mode=HTML уже де-факто в `notify()`) |
| Футер конверта | **corr_id + timestamp + ссылка (CI run / лог-путь) + предлагаемое действие** |
| Транспорт из CI | **Прямой HTTPS** из GitHub Actions (нода не задействована, Tor не нужен) |
| Подтверждения | **Отложены** — не в этом плане |

**Ключевой дизайн-принцип (важнее S1 из аудита):** CI-хуки НЕ пишут отдельный curl/HTTP-клиент.
Они вызывают тот же `core/internal/shared/notifications.py` (stdlib-only), который на ноде идёт
через Tor/Privoxy, а в CI — напрямую (`proxy_url=None`). Один конверт, один экранизатор, один
клиент на обе среды. Иначе дублирование конверта между нодой и CI = новый рассинхрон.

---

## 2. Волны

### Волна A — закрыть тишину (Тир 1, дешёвый выигрыш по риску)

- **A1.** Composite action `.github/actions/notify-telegram/action.yml` — вызывает
  `python3 -m core.internal.shared.notifications notify-ci ...` с прямым HTTPS; env из GitHub Secrets.
- **A2.** `core/internal/scripts/heartbeat_check.py` + workflow `.github/workflows/heartbeat-check.yml`
  (cron */30): list S3 `{prefix}/heartbeat/` → auto-обнаружение нод → stale >2ч → Telegram critical.
- **A3.** `tor_proxy_check.py` — при RED: `write_audit_entry` + state-file, читаемый out-of-band чекером
  (канарейка перестаёт молчать; прямая доставка из ноды невозможна — Tor мёртв, утечки IP не допускаем).
- **A4.** `post_deploy_chain.py` + `notify-hook.sh` — ветка FAILED/ROLLBACK с `severity=critical`
  (сейчас шлётся только success).

### Волна B — консолидация (S2, единый notifier-контракт)

- **B1.** `core/internal/shared/notifications.py` — `Notification` dataclass, `format_envelope()`,
  `escape_html()`, `resolve_chat_id()` (SoT), `notify_event()` (non-blocking, всегда True),
  throttle/dedup-реестр по `(event, fingerprint)`, audit-fallback при провале доставки.
- **B2.** Единый SoT severity→chat в `notifications.py` (константа) + parity-гейт
  `test_notification_channels` против `contact-points.yml` (закрывает двойной SoT и TRAP о fallback-чате).
- **B3.** Миграция 6 отправителей на `notify_event(severity=...)` (таблица §5.2).
- **B4.** `core/notification-catalog.yaml` — реестр событий (id → severity/context/throttle/action) +
  parity-гейт «код шлёт только зарегистрированные события».
- **B5.** Секреты: S3 read-only creds для heartbeat-checker в `secret-definitions.yaml` + матрица ключей.

### Волна C — подтверждения (Тир 2 / S3) — ОТЛОЖЕНО

Restore / remove-project / ротация ключей / direct-deploy — inline-keyboard + callback-сервис.
**Не в этом плане.** Порог ревизии: если после A+B оператору нужно подтверждать >4 классов операций
или появляется вторая нода — поднять приоритет и оформить отдельный DevPlan.

---

## 3. Draft Code Graph (XML)

```xml
<graph>
  <entity id="Notification_py_CLASS" type="dataclass" keywords="severity,context,event,corr_id,details,links,action,ts">
    <annotation>Каноническая обёртка сообщения — единый payload всех отправителей</annotation>
    <crosslink to="notify_event_py_FUNC"/>
  </entity>
  <entity id="notify_event_py_FUNC" type="function" keywords="non-blocking,severity-routing,throttle,fallback">
    <annotation>Единая точка отправки: resolve_chat_id → format_envelope → send_telegram → audit-fallback</annotation>
    <crosslink to="resolve_chat_id_py_FUNC" to="format_envelope_py_FUNC" to="send_telegram_py_FUNC"/>
  </entity>
  <entity id="resolve_chat_id_py_FUNC" type="function" keywords="SoT,severity,chat,env">
    <annotation>ЕДИНСТВЕННЫЙ SoT severity→chat (перенесён из telegram_notifier)</annotation>
  </entity>
  <entity id="format_envelope_py_FUNC" type="function" keywords="HTML,envelope,footer,corr_id,link,action">
    <annotation>Канонический HTML-конверт: badge + context + message + details + footer</annotation>
  </entity>
  <entity id="escape_html_py_FUNC" type="function" keywords="html,escape,stdlib">
    <annotation>Единый экранизатор (html.escape) — единственный в платформе</annotation>
  </entity>
  <entity id="heartbeat_check_py_FUNC" type="function" keywords="s3,list,staleness,out-of-band">
    <annotation>Reader heartbeat: S3 list → stale>2ч → notify critical (CI cron)</annotation>
    <crosslink to="notify_event_py_FUNC"/>
  </entity>
  <entity id="notify_telegram_action_yml" type="action" keywords="ci,composite,direct-https,failure">
    <annotation>Composite action: python3 -m notifications notify-ci (прямой HTTPS)</annotation>
  </entity>
</graph>
```

---

## 4. Data Flow

```
[отправители: reporting / cert_expiry / reboot_policy / security_updates / watchdog / notify-hook / post_deploy_chain]
        │  формируют Notification(severity, context, event, message, corr_id, details, links, action)
        ▼
notify_event(n)                        # shared/notifications.py, non-blocking (всегда True)
        ├─ throttle? (event,fingerprint) ── да → IMP:8 suppressed → return True
        ├─ resolve_chat_id(severity)      # SoT: critical→_CRITICAL, warning→_WARNING, info→base
        ├─ format_envelope(n)             # HTML: badge + context + msg + details + footer
        ├─ send_telegram(..., proxy_url)  # нода=Tor/Privoxy, CI=None (прямой HTTPS)
        └─ fail → IMP:9 DELIVERY FAILED + write_audit_entry("notify:failed")  # D-2 fallback

[CI workflows] failure() ──► notify-telegram composite action ──► notify-ci (прямой HTTPS) ──► _CRITICAL

[heartbeat.py, нода cron */15] ──put_object──► S3 {prefix}/heartbeat/{node}/heartbeat.json
[heartbeat-check.yml, CI cron */30] ──list+staleness──► stale>2ч? ──► notify critical (прямой HTTPS)
```

**Северity-роутинг (единый):** `critical → TELEGRAM_CHAT_ID_CRITICAL` (fallback base) ·
`warning → TELEGRAM_CHAT_ID_WARNING` (fallback base) · `info → TELEGRAM_CHAT_ID`.
Grafana `contact-points.yml` читает те же env — parity-гейт гарантирует совпадение словаря severity.

**Пример конверта (HTML):**
```html
🚨 <b>[CRITICAL]</b> [deploy] Деплой {project} {version} FAILED — healthcheck-rollback
• Причина: container unhealthy (exit 137)
⏱ 2026-08-16T18:20:00Z · 🪪 corr-deploy-{project}-{version} · 🔗 <a href="{run_url}">CI run</a> · 💡 fix-forward: новый коммит
```

---

## 5. File Manifest

### 5.1 Новые файлы

| Файл | Назначение |
|------|-----------|
| `core/internal/shared/notifications.py` | Единый notifier: Notification dataclass, format_envelope, escape_html, resolve_chat_id (SoT), notify_event (throttle + audit-fallback), CLI `notify` / `notify-ci`. stdlib-only (boto3/audit_logger — лениво) |
| `core/internal/scripts/heartbeat_check.py` | Reader heartbeat: S3 list + staleness → notify (out-of-band, CI cron) |
| `.github/actions/notify-telegram/action.yml` | Composite action — прямой HTTPS через `python3 -m notifications notify-ci` |
| `.github/workflows/heartbeat-check.yml` | Cron */30 — heartbeat-checker (S3 read creds + прямой Telegram) |
| `core/notification-catalog.yaml` | Реестр событий: id → severity/context/throttle/action (лёгкий S5) |
| `tests/unit/test_shared_notifications.py` | Конверт, escape, resolve SoT, throttle, fallback, non-blocking |
| `tests/unit/test_heartbeat_check.py` | S3-list/стальные/порог (DI boto3) |
| `tests/gates/test_gate_notification_parity.py` | Parity: resolve_chat_id ↔ contact-points.yml; каталог ↔ call-sites |

### 5.2 Модифицируемые файлы

| Файл | Изменение | severity после |
|------|-----------|----------------|
| `core/internal/shared/telegram_notifier.py` | `resolve_chat_id`/`format_notify_message` → shim над notifications (backward-compat); +`escape_html` | — |
| `core/internal/bootstrap/lifecycle/helpers/reporting.py` | `send_telegram` → `notify_event`; DI-паттерн сохранить (TRAP 160 W4b) | errors>0→critical, warnings→warning, иначе info |
| `core/internal/bootstrap/cert_expiry_check.py` | subprocess `send` → `notify --severity critical` (stdlib-only канон сохранить) | critical |
| `core/internal/bootstrap/reboot_policy.py` | subprocess `send` → `notify --severity` | deferred→warning, executed→info |
| `core/internal/bootstrap/security_updates.py` | `send_telegram(HTML)` → `notify_event` | critical |
| `core/internal/healthcheck/watchdog.py` | `notify` critical → `notify_event` (event id + corr_id) | critical (без изменения) |
| `core/internal/healthcheck/tor_proxy_check.py` | RED → audit-entry + state-file (для out-of-band) | — |
| `core/internal/notify/notify-hook.sh` | +`--severity` уже есть; контракт сохранить | info (success) |
| `core/internal/deploy/hooks/post_deploy_chain.py` | +FAILED/ROLLBACK ветка | critical (fail), info (success) |
| `.github/workflows/core-deploy.yml` | +notify-telegram на failure | critical |
| `.github/workflows/deploy-project.yml` | +notify на [PRACTICES:BLOCK]/gitleaks/ruff/rollback | critical |
| `.github/workflows/platform-gate-fast.yml`, `platform-test.yml`, `push-gate.yml` | +notify на gate-fail | critical |
| `.github/workflows/security-scan.yml` | +notify на trivy/pip-audit fail | critical |
| `.github/workflows/build-platform.yml`, `build-hermes.yml`, `hermes-nightly.yml` | +notify на build-fail | warning |
| `core/secret-definitions.yaml` | +S3 read-only creds (heartbeat-checker) + CI Telegram | — |
| `core/AGENTS.md` §«Ротация ключей» | +S3 read creds в матрицу | — |
| `core/internal/shared/AGENTS.md` | +строка `notifications.py` в инвентарь (44-й) | — |
| `core/check-suite.yaml` | +новые тесты/гейт в сьюты | — |

---

## 6. Acceptance Criteria (проверяемость)

| # | Критерий | Проверка |
|---|----------|----------|
| 1 | CI-failure → Telegram critical без ноды | намеренный fail в test-ветке → сообщение в `_CRITICAL` |
| 2 | Heartbeat stale >2ч → критический алерт извне | остановить heartbeat cron на test-VPS → сообщение через 2ч |
| 3 | 6 отправителей — только `notify_event`/`notify`, без прямого `send` | grep call-sites + parity-гейт |
| 4 | Единый конверт (badge+footer) у всех отправителей | snapshot-тест `format_envelope` |
| 5 | deploy FAILED/ROLLBACK уведомляется | негативный прогон deploy-project (gitleaks-fail) |
| 6 | `make check` зелёный; `make check MARKER=gates` — parity-гейты | журнал `.ai/logs/runs.jsonl` |
| 7 | Ни один уведомительный путь не блокирует операцию | `notify_event` всегда True (unit-тест) |

---

## 7. Риски и TRAP'ы (Read before Act)

- **TRAP[DECISION] 160 W4b** — DI `notifier` живёт в потребителе, НЕ `http_opener` в `send_telegram`.
  `notifications.py` обязан следовать этому паттерну (инъекция в `notify_event`, не в транспорт).
- **TRAP[DECISION] cert_expiry stdlib-only** — `cert_expiry_check.py` вызывается systemd БЕЗ PYTHONPATH;
  не ломать stdlib-only канон (subprocess-канал с PYTHONPATH из `__file__` — оставить).
- **TRAP[DECISION] resolve_chat_id fallback** — удалять `TELEGRAM_CHAT_ID`-fallback ТОЛЬКО когда все
  потребители перешли на severity. В B3 это произойдёт; base-чат остаётся для info.
- **TRAP[BUG] 141** — Telegram из контейнера/ноды ТОЛЬКО через Tor/Privoxy (`api.telegram.org` недоступен
  напрямую). **Не добавлять** direct-HTTP fallback на ноде — утечка IP ноды. Direct HTTPS — только CI/GH Actions.
- **TRAP[BUG] Grafana parse_mode** — Grafana telegram default = MarkdownV2; конверт Python-канала — HTML.
  Разные parse_mode — осознанно (разные рендереры), parity-гейт сверяет только словарь severity, не разметку.
- **OOM-политика** — верификация только `make check`/`check-diff`; `make gate MODE=fast` в dev-цикле запрещён.
- **CI-секреты** — любой новый CI-секрет обязан попасть в `core/secret-definitions.yaml` + матрицу ротации
  (grep-гейт; иначе RED). S3 read creds — отдельный ключ с read-only IAM, не переиспользовать master-ключи.

$END_DEVPLAN
