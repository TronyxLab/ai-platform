# 136-bootstrap-hardening — 04-Debt.md

$START_DEBT

$ARTIFACT_CONTRACT
PURPOSE:               Реестр долгов волны W7 (DevPlan 136 §5.7): «ничьи зоны» без Debt-записей — T9-T11 (повторный прогон chaos-программы на пересозданной ноде + реальные ACME), hermes root-500, живые долги D-3..D-8 из программы 126, ограничения харнесса B6/B7, находки W2-агентов (overlay-дрейф, firewall reset→apply).
DESCRIPTION:           Актуальные статусы на 2026-08-05. Каждая запись: категория, описание, verification-cost, Rev-дата в окне 2026-08-19..2026-11-01, статус OPEN (или CLOSED с указанием закрывающей волны). D-3..D-8 переносятся из .ai/plans/126-chaos-resilience/04-Debt.md (актуальные статусы: D-1/D-2 CLOSED-by-132, D-3..D-8 OPEN — сверено с VerificationReport 126 W5). Формат записей — канон 126 (Status + Rev-условие).
RATIONALE:             Протокол artifacts.md: Debt-реестр фиксирует АКТУАЛЬНЫЕ статусы. Brief 136 R7: «T9-T11 и hermes-500 не имеют Debt-записей» — «ничьи зоны» теряются без Debt-протокола; W7 закрывает пробел с Rev-датами, чтобы долги не были бессрочными.
ACCEPTANCE_CRITERIA:   (1) Каждая запись имеет категорию (T9-T11/hermes/D-x/B-x), описание, verification-cost и Rev-дату в диапазоне 2026-08-19..2026-11-01; (2) D-3..D-8 — актуальные статусы из 126 (проверено по 04-Debt.md 126 + VerificationReport 126 W5); (3) B6/B7 задокументированы; (4) находки W2 (overlay-дрейф, firewall reset→apply) зафиксированы; (5) нет выдуманных долгов — каждый подтверждён артефактами.
IMPLEMENTS:            DevPlan 136 §5.7 T7.2; Brief 136 AC(7) / R7; VerificationReport 126 W5 (T9-T11 не выполнялись).
IMPACTS:               Будущие волны: фиксы D-3..D-8 (мониторинг/alerting/Loki), T9-T11 (отдельное окно пересозданной ноды), hermes L2-патч, W10 (firewall incremental), W12 (DR AGE).
REQUIRES:              .ai/plans/126-chaos-resilience/04-Debt.md + 03-VerificationReport.md (статусы D-3..D-8, T9-T11); 136 Brief/DevPlan (T9-T11, hermes root-500, B6/B7); рабочие артефакты W2 (overlay-дрейф, firewall).
$END_ARTIFACT_CONTRACT

---

## Реестр долгов — актуальные статусы (2026-08-05)

| ID | Категория | Суть | Status | Verification-cost | Rev-дата / Rev-условие |
|----|-----------|------|--------|-------------------|------------------------|
| T9-T11 | T9-T11 | Повторный прогон chaos-программы 126 (T9-T11) НЕ выполнен: окно закрыто, сервер пересоздан; требуется пересозданная нода + реальные LE-сертификаты (ACME) — вне харнесса (B7). DevPlan 136 §9: «ACME rate-limit / реальные LE-сертификаты вне харнесса — T9-T11 Debt с Rev» | **OPEN** | HIGH — полный chaos-прогон на пересозданной ноде (~2-4ч + окно оператора SC2) | **Rev: 2026-09-15** — следующее окно пересозданной ноды (SC2) с реальными ACME-доменами; при прогоне — перепроверить T8 ENOSPC-маркеры (D-7) и Loki-окно (D-8) |
| hermes-root-500 | hermes | hermes-agent контейнер работает с root-привилегиями (нет USER-директивы в Dockerfile; init.py имеет chown-if-root workaround, TRAP[BUG] 2026-07-06). Правильный фикс — upstream hermes-проекта или патч L2 (build/scripts/init.py + USER в Dockerfile контекста). Причина Debt: root-постурa контейнера = лишняя поверхность при компрометации модуля | **OPEN** | HIGH — L2 rebuild + верификация non-root работы (volume perms, dashboard, API server 8642) на ноде | **Rev: 2026-10-21** — после upstream-релиза hermes (или L2-патча); при верификации non-root на test-VPS — закрыть |
| D-3 | D-x (126) | Grafana alerting→Telegram цепочка не активна: `contact-points.yml` = пустой safe-default (0 receivers, 0 policies); алерты fire (rules state) но НЕ доставляются | **OPEN** | LOW — rename contact-points.yml.telegram + TELEGRAM_* env + тестовая доставка | **Rev: 2026-08-19** — при активации — перепроверить fire/resolve цикл на реальном алерте |
| D-4 | D-x (126) | T6: Grafana alert на падение postgres НЕ сработал (11s даунтайм; Service Down rule `for: 1m` > TTR; alerts_total=0) | **OPEN** | LOW — правка rule (снизить `for:` Service Down) + fire-тест | **Rev: 2026-09-05** — если анти-флаппинг важнее покрытия sub-minute падений — принять как дизайн и закрыть |
| D-5 | D-x (126) | T7: OOM-инъекция НЕ попала в clickhouse — жертвой стал bash-аллокатор; restart-политика clickhouse под OOM НЕ проверена (verification gap) | **OPEN** (verification gap) | MEDIUM — целевой OOM-прогон (memory cgroup на clickhouse, аллокатор в контейнере) | **Rev: 2026-09-15** — при подтверждённом OOMKilled→restart — закрыть |
| D-6 | D-x (126) | T8: Grafana DiskSpaceLow rule неэффективна — expr `node_filesystem_avail_bytes/... < 0.2` БЕЗ mountpoint-фильтра: reducer last берёт произвольную серию → state inactive при 90% fill | **OPEN** | LOW — фикс expr (mountpoint-фильтр) + fire-проверка на 90% fill | **Rev: 2026-08-25** — после фикса — повторный T8-прогон (или ручной df-тест) |
| D-7 | D-x (126) | T8: ENOSPC-след НЕ реконструируем из персистентных источников (journald 0 совпадений, backup-cron.log пуст) — «инцидент без следа» | **OPEN** | LOW — маркер в backup-postgres.sh (auditfile) / логирование docker exec-вывода | **Rev: 2026-09-05** — при наличии ENOSPC-следа в персистентных источниках — закрыть |
| D-8 | D-x (126) | Loki resilience: T4 — clock skew ±24h → 1943 rejected «entry too far behind»; T8 — ingester «shutting down» весь окно (promtail 500s, Loki недоступен) | **OPEN** | MEDIUM — исследование/фикс (toleration clock jump, WAL, healthcheck-критерий) + повторный T4/T8 | **Rev: 2026-09-20** — после фикса — повторный T4/T8-прогон (skew + длительное окно) |
| B6 | B-x (харнесс) | Ограничение харнесса test-node: CI-канал деплоя (make deploy / context-promote → deploy-project.yml) ВНЕ харнесса — харнесс покрывает локальный forced-command receive, не полный CI-путь доставки (git push → workflow → VPS) | **OPEN** | MEDIUM — CI dry-run деплоя реального проекта + верификация на ноде | **Rev: 2026-10-21** — после стабилизации CI-деплоя ≥1 релизного цикла; задокументировать в tests/e2e/README.md |
| B7 | B-x (харнесс) | Ограничение харнесса: реальные LE-сертификаты (ACME rate-limit, DNS-01 webnames) ВНЕ харнесса — харнесс использует self-signed/dev-certs; реальный ACME верифицируется только e2e-verify на tronyx-vps | **OPEN** | HIGH — e2e-verify на ноде с реальными доменами (частично закрывает) + полный ACME-цикл в отдельном окне | **Rev: 2026-09-15** — совместно с T9-T11 (пересозданная нода); при полном ACME-цикле в харнессе — закрыть |
| W2-overlay-drift | W2-находка | Overlay-дрейф nginx: 7 файлов `node-configs/*/overlays/nginx/*.conf` с `proxy_pass $upstream_x/health` (D19/D20: нет `set $upstream` в location /health + URI на переменной → 500) + дрейф имён www.tronyx.ru.conf vs tronyx.ru.conf. **Закрыт W7 T7.5** (repo-копии синхронизированы с vhost_renderer.py: tronyx-vps 4 файла + test-node 3 файла; org-копия tronyx-lab/node-configs синхронизирована). ⚠️ node-configs/ gitignored — доставка через rsync (Core-канал), НЕ коммитится в ai-platform | **CLOSED-by-136-W7** | LOW (рендер-сверка + nginx -t гейт test_gate_vhost_nginx_t) | Rev-условие выполнено (2026-08-05 T7.5): рендер-сверка = 0 diff; при повторном дрейфе — воспроизводится рендером `make render-vhosts NODE=<n>` |
| W2-firewall-reset | W2-находка | firewall reset→apply: firewall.py выполняет disable+reset перед применением правил (S-14, W10 T10.10: incremental вместо disable+reset; DOCKER-USER chain) — окно без firewall при переприменении | **OPEN** | MEDIUM — инкрементальный ufw + gate-тест (signature «firewall not active» в healthcheck) | **Rev: 2026-10-21** — W10-скоуп (DevPlan 136 §12.2 T10.10); при реализации incremental — закрыть |

---

## Сводка статусов

- **CLOSED:** D-1 (132 W3), D-2 (132 W4) — из 126, подтверждены VerificationReport 126 W5; W2-overlay-drift (136 W7 T7.5).
- **OPEN:** T9-T11 (отдельное окно пересозданной ноды), hermes-root-500 (upstream/L2-патч), D-3..D-8 (мониторинг/alerting/Loki), B6 (CI-канал вне харнесса), B7 (реальные ACME вне харнесса), W2-firewall-reset (W10-скоуп).
- Все Rev-даты в окне **2026-08-19..2026-11-01**; после Rev-даты запись пересматривается (закрывается фиксом или переоформляется с новой Rev).

$END_DEBT
