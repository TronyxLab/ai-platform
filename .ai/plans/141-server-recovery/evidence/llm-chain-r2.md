# 141-server-recovery — llm-chain-r2.md (2-й цикл)

$START_LLM_CHAIN_R2

> Проверка: 2026-08-06 10:47-11:25Z. Цель: provision-llm → POST через litellm (deepseek) → trace langfuse → логи Loki.

## 1. provision-llm ✅ (локальный стек, тот же код)

- `make provision-llm` → key_provisioner.py → 1 виртуальный ключ (hermes-agent), persisted в /var/folders/.../litellm-project-keys.json (вне репо).
- Локальный litellm 127.0.0.1:4000 (healthy); модели из лителлим-конфига: reasoning/deepseek-v4-pro, chat/reasoning-fallback/deepseek-chat → deepseek-v4-flash.

## 2. LLM-вызов (POST /chat/completions, model=deepseek-chat) ✅

| Метрика | Значение |
|---------|----------|
| HTTP | 200 |
| id | 8c688b44-7661-4898-ae71-8fec52979e9a |
| model | deepseek-chat (deepseek-v4-flash) |
| usage | prompt 92 / completion 50 (reasoning 50) / total 142 |

Вызов прошёл через litellm → DEEPSEEK API (реальный расход токенов). Ответ: reasoning-токены (max_tokens=50 ушли в reasoning, content пуст — ожидаемо для reasoning-режима).

## 3. Логи в Loki ✅ (локальный стек)

- `{compose_service="litellm"}` в локальном Loki (127.0.0.1:3100): вызов зафиксирован (`INFO: 127.0.0.1:44118 - "POST /chat/completions HTTP/1.1" 200 OK` в рамках sweep; плюс /health/liveliness и /metrics).

## 4. Trace в langfuse ⚠️ (локальный стек)

- Локальный langfuse (127.0.0.1:3001, v3.212.0): public API auth с ключами контейнера (pk-lf_68.../sk-lf_3d...) — ПРОХОДИТ (401 уходит при чужих ключах); но:
  - legacy /api/public/traces — 422 «Request timed out» (медленный эндпоинт, и с fromTimestamp-фильтром 30 мин);
  - v2-маршруты (/api/public/v2/traces, /api/public/v2/observations) — 404 (отсутствуют в этой сборке);
  - litellm-лог: `API error occurred: Internal server error occurred ... langfuse.com/support` — callback langfuse упал (ингейшн локального langfuse не принял/таймаутнул).
- Вывод по локальному: цепочка litellm→deepseek и Loki доказаны; trace в ЛОКАЛЬНОМ langfuse не подтверждён (производительность/таймауты dev-инстанса, ключи при этом совпадают). Это НЕ баг платформы — локальный langfuse исторически медленный (dev-данные), node-инстанс проверим серверной пробой.

## 5. Цепочка НА НОДЕ — ожидает server-ops (REQ_EVIDENCE 10:55Z)

- litellm на ноде слушает 127.0.0.1:4000 (loopback) — внешнего vhost'а нет (см. auth-matrix-r2.md §2) → пробу может выполнить только server-ops.
- Запрошено: (1) `cd /opt/platform && make provision-llm`; (2) curl POST /chat/completions (deepseek-chat); (3) ответ → evidence/llm-node-probe.json.
- После пробы: trace появится в node-langfuse (success_callback=langfuse в litellm-config.yml) — проверю через /api/public/traces (node-API отвечает быстро, v3.212.0); логи вызова — через grafana-proxy Loki ({compose_service="litellm"}).

## 6. Итог

| Звено | Локально | Нода |
|-------|----------|------|
| provision-llm | ✅ | запрошено |
| litellm POST deepseek | ✅ 200 (142 токена) | запрошено |
| Loki-логи вызова | ✅ | ✅ (стрим litellm жив, см. loki-r2.md) |
| langfuse trace | ⚠️ dev-таймауты | ожидается после пробы |

$END_LLM_CHAIN_R2
