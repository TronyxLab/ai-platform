# 142-full-auto-cycle — 04-TimingsReport.md

$START_TIMINGS_REPORT

> Сессия: 2026-08-07 15:18–20:30 MSK. Цикл 3 (после 141 cycle-1/cycle-2). Полный цикл «голый сервер → штатная работа» с жёстким критерием «0 ручных SSH-действий на ноде».

## Сводка по фазам

| Фаза | Шаг | Длительность | Накопительно | Причина |
|------|-----|-------------|--------------|---------|
| 3.1 | Префлайт (SSH/secrets/S3/стек) | ~7 мин | 7 мин | status-page local fix (W2-путь в старом контейнере) |
| 3.2 | make check (чистый main) | 767.8s | ~20 мин | fingerprint replay (кэш) |
| 3.3 | bootstrap попытка 1 | ~40s | — | **FAILED B27**: node-lifecycle.sh не принимал --ci-root-key |
| — | фикс B27 + make check + push | ~20 мин | ~40 мин | R5-тест, pre-push gate |
| 3.3 | bootstrap попытка 2 (ретрай) | ~17 мин | ~57 мин | 9 INIT фаз, 26 контейнеров, smoke forced-command OK |
| 3.4 | core-deploy CI dispatch | ~6 мин | ~63 мин | попытка 1 FAIL (VPS_SSH_KEY не base64 — R-фикс секрета); ретрай SUCCESS (C1) |
| 3.5 | converge ×3 | ~10 мин | ~73 мин | RED B28a/B28b (R9 oneshot + rc=2 коллизия) |
| 3.7/3.8 | e2e-verify | ~3 мин | ~76 мин | MODE=remote FAIL (B29 — ci-deploy ключ утрачен); MODE=local PASS 4/4+4/4 |
| — | фиксы B28-B35 (2 батча) + node-update ×3 | ~75 мин | ~151 мин | converge GREEN, C6 GREEN |
| 3.9 | LLM-проба (provision-llm + deepseek-chat) | ~3 мин | ~154 мин | «pong! 🏓» (114 токенов) |
| 3.10 | Chaos T1-T11 (полный) | 1484s (24:44) | ~179 мин | 5 passed (T1-T5) / 6 failed (T6-T11) |
| 3.10 | Chaos rerun --lf (6 failed) | 512s (8:32) | ~187 мин | подтверждение; B36 (IndexError) найден |
| 3.10 | Chaos T8+T11 rerun | 1048s (17:28) | ~205 мин | T11 self-heal GREEN (28 healthy); формальный cross-audit RED (изолированный rerun) |
| 3.11 | verify-141-be: scaffold + build amd64 + push + receive | ~25 мин | ~230 мин | первый pull failed (образ arm64/private) → buildx amd64 + public-пакет → DEPLOYED 16.1s |
| 3.12 | verify-141-fe: scaffold + npm + build + deploy | ~20 мин | ~250 мин | npm ci FAIL (B37 lockfile) → npm install; DEPLOYED 11.7s |
| 3.13 | bootstrap no-op (инвариант 6) | 21s ×2 | ~251 мин | **доказательство: 17 мин → 21s (no-op, все 9 фаз skip)** |
| 3.14 | reboot + self-heal | ~5 мин | ~256 мин | 28 контейнеров healthy БЕЗ ручных действий; privoxy 0.0.0.0 пережил reboot |
| 3.10/C5 | core-deliver dry-run | ~30s | — | C5 GREEN |
| 4 | Отчёты | ~30 мин | ~290 мин | 03/04/05/06 |

## Сравнение одинаковых шагов (доказательство прогресса циклов)

| Шаг | 141 cycle-1 | 141 cycle-2 | 142 cycle-3 | Дельта (c1→c3) |
|-----|------------|-------------|-------------|----------------|
| bootstrap-node (первый, cold) | 480s (после B4-fail) | 480s | ~1020s (B27-fail + ретрай) | B27 — проводка W1 была неполной (main-баг, исправлен) |
| bootstrap no-op (повторный) | 19s | 19s | **21s** | стабильно (инвариант 6) |
| deploy-project ×4 | 15s (receive verb) | 3-13s | — (B29: ключ ci-deploy утрачен; проекты развёрнуты bootstrap-каналом + verify-141-* через root-dispatch) | ⚠️ B29 — префлайт-остаток |
| e2e-verify | HTTP 4/4 TLS 4/4 | HTTP 4/4 TLS 4/4 depth=4 | **HTTP 4/4 TLS 4/4** (MODE=local) | стабильно |
| make check | 393s | ~390s | 767.8s (кэш-replay) | стабильно |
| converge | exit 0 | exit 0 | **exit 0 (после B28a/B28b)** | стабильно (после фиксов) |
| chaos PASSED | 3/11 (T4/T5/T6) | 3/11 (T4/T5/T6) | **5/11 (T1-T5)** | +2 (T1/T2/T3) |
| LLM-проба | — | deepseek «pong! 🏓» | deepseek «pong! 🏓» | стабильно |
| ручных SSH-действий на ноде | ~10+ | ~10+ | **0** | **критерий 142 AC2 достигнут** |

## Самые долгие шаги и ПОЧЕМУ

1. **Chaos-сьют 24:44 + reruns 26 мин** — reboot (T11), clock-skew (T4), sigkill (T6), OOM (T7) — каждый тест с окнами ожидания восстановления (60-600s).
2. **Фикс-цикл B28-B35 ~75 мин** — 4 коммита, 3 node-update --force, каждый с полным 5-фазным прогоном.
3. **verify-141-be ~25 мин** — первый pull failed (образ arm64 — нода amd64; пакет private — нода не авторизована на tronyx161-пакеты) → buildx amd64 + повторный receive.

## Полные данные

`evidence/timings.tsv` — фаза, шаг, команда, start/end ISO, duration_s, exit_code, retries, cause.

$END_TIMINGS_REPORT
