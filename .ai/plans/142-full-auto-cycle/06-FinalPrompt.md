# 142-full-auto-cycle — 06-FinalPrompt.md

$START_FINAL_PROMPT

> Финальный промт 4-го (контрольного) прогона — готов к запуску как одного действия.
> Финализирован по результатам цикла 3 (2026-08-07): добавлены префлайт-пункты R14 (VPS_SSH_KEY base64) и R15/B29 (локальный ci-deploy ключ), ретрай-политика и known-issues.

# КОНТРОЛЬНЫЙ 4-Й ПРОГОН: полный цикл голый сервер → штатная работа

## Роль и цель
Ты — главный оператор ai-platform. Сервер tronyx-vps (103.88.243.151) переустановлен (голый).
Задача: прогнать ПОЛНЫЙ цикл от нуля до штатной работы АВТОМАТИЧЕСКИ.
ЖЁСТКИЙ КРИТЕРИЙ: 0 ручных SSH-действий на ноде. Каждое действие — каноническим
make-таргетом / CI dispatch / self-heal'ом платформы. Если понадобилось ручное
SSH-вмешательство (кроме 1 ретрая) — это РЕГРЕССИЯ: зафиксируй, НЕ чини обходом.

## Префлайт (обязательно перед стартом, оператор онлайн 15 мин)
1. node-configs/tronyx-vps/node.yaml содержит ci_root_key + owner_key + ci_deploy_key (pub)
2. **VPS_SSH_KEY в gh secret — ОБЯЗАТЕЛЬНО base64 от приватной части ci_root_key**
   (R14: в цикле 3 секрет был не-base64 → setup-ssh «invalid input» → core-deploy FAILED;
   фикс: `base64 -i ~/.ssh/ai-platform/tronyx-vps-ci | tr -d '\n' | gh secret set VPS_SSH_KEY`)
3. **Локальная приватная пара ci-deploy существует** (`~/.ssh/ci_deploy_key` или
   KEY_FILE=… для make deploy-project / e2e MODE=remote) (R15/B29: в цикле 3 утрачена —
   деплой проектов шёл bootstrap-каналом + root-dispatch, что неканон для 3.6)
4. AGE_SECRET_KEY в gh secret list
5. S3 tronyx-vps-backups bucket жив (≥4 домена + wildcard кеш)
6. TELEGRAM_BOT_TOKEN + TELEGRAM_CHAT_ID в enc.yaml (sops decrypt проверка)
7. WEBNAMES_API_KEY в enc.yaml (для новых доменов)
8. DOCKER_HUB_USERNAME + DOCKER_HUB_TOKEN в enc.yaml
9. Локальный стек: make status (все healthy; status-page — пересоздать контейнер после W2-путей)
10. tronyx-vps эксклюзивен 8 часов (question оператору)
11. git status clean (планы 141/142 закоммичены); make pre-commit-install
Любой RED → батч-вопрос оператору, НЕ стартуй с RED префлайтом.

## Фазы (по образцу цикла 3, сигналы/тайминги/телеграм — evidence/timings.tsv)
Фаза 0: префлайт (выше) → known_hosts: ssh-keygen -R 103.88.243.151 + accept-new
Фаза 1: make check на чистом main → push (pre-push hook прогонит gate)
Фаза 2: make bootstrap-node NODE=tronyx-vps (W1: ci_root_key добавляется φ2; 9 INIT фаз;
   no-op повтор 21s — инвариант 6)
Фаза 3: gh workflow run core-deploy.yml (C1: SUCCESS без ручного ключа)
   → make converge NODE=tronyx-vps (exit 0 — FULLY CONVERGED)
   → deploy-project ×4 (tronyx-site, dance-site, botanika, roadmap) через CI/make
Фаза 4: сертификаты (S3-кеш) + e2e-verify NODE=tronyx-vps (HTTP 4/4, TLS 4/4)
Фаза 5: LLM-проба (litellm → deepseek-chat «pong! 🏓») + Telegram (0 notify errors)
Фаза 6: chaos T1-T11 (T4 TSDB + T11 reboot — КРИТИЧНО AC4; T6-T10 — известные RED, Debt)
Фаза 7: интеграция 141: make new-project NAME=verify-141-be TEMPLATE=backend →
   сборка amd64 образа (buildx --platform linux/amd64 — нода amd64!) → push ghcr →
   receive → /health 200; make new-project NAME=verify-141-fe TEMPLATE=frontend →
   npm install && npm run build (⚠️ npm ci: B37 lockfile-деbt) → deploy
Фаза 8: bootstrap no-op (21s) + reboot → self-heal (28 контейнеров healthy, privoxy 0.0.0.0)
Конец: финальный вердикт + чек-лист «0 ручных действий» со ссылками на доказательства.

## Known-issues цикла 3 (НЕ блокеры, но ожидай)
- core-deploy CI: если setup-ssh «base64: invalid input» → перекодировать VPS_SSH_KEY (R14)
- converge: если exit 2 → проверить R9 oneshot-guard (B28a) и rc=2 различение (B28b) — фиксы в main
- chaos T6-T10 — известные RED (Debt §6 VerificationReport 03); T11 — self-heal GREEN,
  формальный cross-audit RED при изолированном rerun (прогоняй весь сьют одним заходом)
- deploy-project: если «Permission denied (publickey)» для ci-deploy → восстановить
  приватную пару ci-deploy (R15) — иначе root-dispatch receive (зафиксировать как обход)

## Отчёты
- .ai/plans/142-full-auto-cycle/03-VerificationReport.md (вердикты C1-C10 + I1-I7, мета-реестр)
- 04-TimingsReport.md (timings.tsv, сравнение циклов)
- 05-TelegramSummary.md (милстоуны)
- 06-FinalPrompt.md (этот промт, обновить по результатам)

$END_FINAL_PROMPT
