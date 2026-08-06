# CI State — session 141 cycle 2 (ci-ops)

Append-only. TS in MSK (UTC+3). Baseline: 2026-08-06T11:41:00+03:00 (08:41 UTC).

## Baseline (11:41 MSK)

- **HEAD main**: `8a3ee3754` (docs(141): финальные evidence-обновления 1-го цикла)
- **Последний гейт для HEAD main**: platform-gate-fast `31081168332` **success** (07:30:16Z push) →
  цепочка `31082046946` Mirror success / `31082047016` core-deploy **failure** (SSH pre-flight,
  ключ отсутствовал — 1-й цикл) / `31082047036` Build Platform Agent failure (smoke hermes-data).
- **Секреты (Tronyx161/AI-platform)**:
  - `AGE_SECRET_KEY` — обновлён **2026-08-06T08:36:59Z (11:36 MSK)** — сразу после переустановки сервера (11:26 MSK). ✅ на месте.
  - `VPS_SSH_KEY` / `VPS_HOST` — от 2026-08-04 (без изменений); pub ci-core-deploy добавлен в /root/.ssh/authorized_keys нового сервера (по брифу).
- **Стратегия**: workflow_dispatch core-deploy на ref main после P2_BOOTSTRAPPED. sha-resolve verify=true: для dispatch SHA = github.sha = 8a3ee3754, успешный platform-gate-fast для этого SHA есть (31081168332) → verify пройдёт, skip=false → деплой пойдёт.
- **Порог деплоя**: ожидание P2_BOOTSTRAPPED (SIGNALS.md) — сервер эксклюзивно у server-ops до этого сигнала.
- **Известные RED 1-го цикла (вне сервера)**: Build Platform Agent smoke `undefined volume hermes-data`; Build Hermes Images write_package denied (GITHUB_TOKEN vs public-пакет). Не блокируют core-deploy.
- Тайминги: поллер (все шаги) → timings.tsv; CI-логи → evidence/ci-logs-r2/; сводка ранов → ci-runs-r2.tsv (116 ранов 1-го цикла + r2-добавки, state изолирован).

## Хронология 2-го цикла

| Время (MSK) | Событие |
|-------------|---------|
| 11:26 | сервер tronyx-vps переустановлен (server-ops) |
| 11:36:59Z | AGE_SECRET_KEY обновлён в секретах (подтверждение брифа) |
| 11:41 | baseline (эта запись); поллер инициализирован (40 ранов, 0 новых с 07:43Z) |
| 11:41 | ожидание P2_BOOTSTRAPPED — бутстрап в эксклюзиве server-ops |
| 11:53 | `TREE_CLEAN` (local-validation): make check PASS 773s — дерево кода готово |
| 11:53→12:16 | поллинг: 0 записей сигналов, 0 новых CI-ранов (бутстрап server-ops продолжается ~50 мин) |
| 12:35 | `REQ_EVIDENCE` (server-ops→evidence): litellm/minio HTTP 000, остальные живы — не дорожка ci-ops |
| 12:16→13:12 | поллинг: 0 новых сигналов, 0 новых CI-ранов (state стабилен, 40 ранов) |
| 13:12 | **СВОДКА за час**: бутстрап server-ops идёт ~106 мин; CI-активности нет (0 новых ранов с 07:43Z); деплой ждёт P2_BOOTSTRAPPED |
| 12:35→14:24 | сигналы: REQ_EVIDENCE ×3 (vhost'ы litellm/minio, LLM-проба, P5-блокеры alertmanager/prometheus) — адресованы evidence/server-ops, не ci-ops; 0 новых CI-ранов |
| 14:24 | **СВОДКА за 2-й час**: бутстрап ~178 мин (cold bootstrap + ручное восстановление P5); деплой по-прежнему ждёт P2_BOOTSTRAPPED |
| 12:40→12:53 | `FIXES_AVAILABLE` (local-validation, b9fbc47f) → гейт 31102362497 **SUCCESS** → workflow_run-цепочка автоматически |
| 12:53 | **core-deploy 31103410072 FAIL**: SSH pre-flight ✅ (ключ работает!), rsync ✅, provision ❌ (`scripts/make-log-shell.sh` missing, Error 127) |
| 12:57 | **РЕАЛЬНЫЙ БАГ CI-доставки**: scripts/ не входит в rsync ни CI, ни bootstrap (11ef2c74 добавил SHELL wrapper, доставку не обновили). Finding + REQ_FIX отправлены. До фикса core-deploy детерминированно падает. Build Platform Agent — тот же smoke-баг hermes-data |
| 12:57→16:29 | ожидание: REQ_FIX в очереди local-validation (0 ответов); P2_BOOTSTRAPPED нет; CI-ранов новых нет (platform-test b9fbc47f — известный RED) |
| 13:47 | `FIXES_AVAILABLE` bc3a448b (B19/B20 deploy-project: ci-deploy group, practices.lock) → гейт SUCCESS → core-deploy 31108723544 FAIL (тот же provision scripts/ — детерминированность подтверждена 2-м прогоном) |
| 13:47→17:35 | ожидание: REQ_FIX (scripts/) без ответа ~1ч40м; P2_BOOTSTRAPPED нет; активных CI-ранов нет |
| 18:05 | gh api BLOCKED ×3 (edge reset; curl жив) → BLOCKED_ci-ops записан в SIGNALS.md; поллинг приостановлен (правило 7, без обходных путей). Итог 2-го цикла: SSH ✅/rsync ✅/provision ❌ (scripts/ баг, REQ_FIX) ×2; P2 не получен |
