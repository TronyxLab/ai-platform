# 02-StatusReport — Приёмо-сдаточная валидация платформы (tronyx-vps)

$ARTIFACT_CONTRACT
- PURPOSE: Финальный статус приёмо-сдаточной валидации после рефакторинга: одна команда bootstrap-node
- DESCRIPTION: Фазы A–H выполнены 2026-08-31/09-01; нода tronyx-vps, контекст tronyx-lab
- RATIONALE: Критерий владельца — голая нода → сервер + ВСЕ проекты одной командой
- ACCEPTANCE_CRITERIA: bootstrap-node с ноды = healthy-стек + все проекты live; идемпотентность; DR; chaos; release checklist
- IMPLEMENTS: Критерий результата владельца
- IMPACTS: нода tronyx-vps (двойной cold-run, toggle-дриллы, reboot, chaos), код платформы (12 фикс-коммитов), context-promote
- REQUIRES: ответы владельца §0 (зафиксированы в 01-Findings.md)

## Section 1 — Diagnostic Summary

**Окружение:** dev: macOS arm64 (darwin), docker compose; нода: tronyx-vps 103.88.243.151,
Ubuntu 24.04, python 3.14.6 (deadsnakes), docker, 25 контейнеров; контекст tronyx-lab
(4 проекта: tronyx-site, dance-site, botanika — exposed; oldapp — non-exposed).

**Проблемы (с severity):**
| ID | Sev | Суть | Статус |
|----|-----|------|--------|
| F-01 | P1 | Незавершённое дерево 019: stale манифесты, stale пины канала, syntax-гейт без AGENTS.md-исключения, compose-чек без rule-id | fixed |
| F-02 | P2 | Perf-бенчмарк parse() флак под xdist (single-shot) | fixed (best-of-3) |
| F-03 | P1 | Холодный старт: langfuse crash на CH-миграции (параллельный старт без readiness) | fixed (см. F-05: правильный фикс — оркестрация, не compose) |
| F-04 | P2 | start_period healthcheck слишком короткий при медленном старте | fixed (180s) |
| F-05 | P0 | compose-level cross-module depends_on роняет per-module dry-run D8 на ноде (локальный make up ≠ node deploy path) | fixed (revert; канон — module.yaml#depends_on + порядок) |
| F-06 | P1 | _step_vhosts молча «успешен» при упавшем рендере; R7 volumes false-positive по префиксу | fixed (rc-верификация + retry; {project}_{name} матч) |
| F-07 | P2 | project-status first-match-wins при легитимном дубликате проекта в разных node.yaml | fixed (fail-fast с кандидатами); S2 apt-check Ubuntu 24.04 fallback — fixed |
| F-08 | P0(инфра) | Нода пересоздана владельцем посреди валидации (host key сменился) — доверие обновлено, полный путь перезапущен | resolved |
| F-09 | P1 | Monitoring не уважал enabled-семантику (static status-page target → вечный DOWN) | fixed (file_sd + required_module + renderer placement∩enabled) |
| F-10 | P1 | One-shot config-init не пересоздавался при изменении шаблона (P19-класс); skip-путь обходил recreate | fixed (deploy.init_services + force-recreate в общем финале) |
| F-11 | P0 | **DR-restore молча уничтожал данные**: self-role фильтр restore_psql.sh (skip-latch после DROP ROLE U) гасил весь остаток дампа; rc=0 над пустым кластером | fixed (terminator reset + expected-DB post-check fail-closed; 9 тестов; живое восстановление из pre-restore снапшота) |
| D5 | BLOCKED | CI-канал проекта: GitHub Actions биллинг TronyxLab (payments failed / spending limit) — workflow корректен | blocked (внешнее; нужен владелец) |
| G5 | BLOCKED | test-VPS недоступна (ответ владельца) | blocked |

## Section 2 — Actions Taken

**Фаза A (локально):** make check батч до чистоты (5 фиксов через Coder-субагентов); agent-check
exit 0; check-manifests GREEN; локальный стек: холодный цикл down→up→healthcheck→down зелёный
(F-03/F-04 фикс), up идемпотентен.

**Фаза B (нода, критерий):** secrets-unlock → bootstrap-node с голой ноды:
- cold-run #1: φ1-φ7 с нуля зелёные; φ8 упал (F-05, мой регресс F-03) → фикс →
- cold-run #2: φ8+φ8.5 зелёные, 3/4 проекта DEPLOYED healthy (старый app: no local source — NOTE);
- cold-run #2 (после пересоздания ноды владельцем): ПОЛНЫЙ путь secrets-unlock → bootstrap:
  9/9 фаз, 3 проекта delivered+healthy, серты восстановлены из S3-кеша (boto3 с φ1, ZERO ACME),
  vhosts верифицированы, converge rc=0;
- идемпотентность: повторные bootstrap = SKIP done-фаз, φ8 hash-invalidation (node.yaml/код) —
  projects health-skip (delivered=0 skipped=4), ~7 мин vs ~20 мин;
- converge RC=0 (R6 vhosts, R7 volumes), check-security S1-S9 PASS, healthcheck ALL HEALTHY.

**Фаза C:** wildcard tronyx.ru + sexydancerostov.ru (webnames DNS-01, registry-driven);
CACHE DRILL: объект в S3 подтверждён → live-серты удалены → deploy-context восстановил
restored=3 issued=0 (ZERO ACME) → fingerprint совпал → verify-domains 3/3 → Prometheus
platform_tls_days_left живые (61/84/64).

**Фаза D:** deploy-context идемпотентен; render-vhosts/render-monitoring OK; project-status ×3
healthy; deploy-project (прямой) DEPLOYED healthy; D5 CI-канал BLOCKED (биллинг GitHub);
sync-env ×7; provision-llm 1 ключ (на ноде); rollback-контур: F-11-подобный фикс (tag anchor) →
rollback → healthy + rollback:true в истории → redeploy latest.

**Фаза E:** healthcheck ALL HEALTHY; toggle вкл/выкл status-page ЗАМКНУТ (off: orphan-down +
file_sd без таргета; on: Up healthy + target up; 3 фикса F-09/F-10+skip); node-update 5/5;
E5 converge чисто; E6 сети = канону (R4/R6/R7 PASS).

**Фаза F:** F1 бэкап-цикл OK (encrypt→S3→sentinel→cleanup); F2 restore round-trip —
P0 F-11 найден и исправлен, кластер восстановлен из pre-restore снапшота (3 БД, проекты
healthy); pre_restore_* вне retry-скана (SEC-0018 живьём); F3 age-key-backup UPLOAD VERIFIED
(sha256); F4 cron активен, AGE_RECIPIENT непуст, RPO 24ч сходится.

**Фаза G:** G1 reboot → автоподъём 25/25 healthy за 2 мин → e2e 3/3; G2 chaos 12/12 PASSED;
G3 load-smoke RC=0 (WARN: метрики litellm/nginx в отчёте — NOTE); G4 e2e 3/3; G5 BLOCKED.

**Фаза H:** см. Section 4.

## Section 3 — Audit Trail (ключевые решения/отклонения)

| Время | Действие | Rationale |
|-------|----------|-----------|
| 22:40-23:20 | Фаза A фикс-циклы через Coder-субагентов параллельно (непересекающиеся файлы) | батчинг ошибок, ТЗ с точными файлами/строками |
| 23:35 | F-05 revert compose depends_on | локальный make up ≠ node per-module path; канон — module.yaml#depends_on |
| 00:25 | strict context-deploy в INIT (fatal на failed≠∅) | критерий «конец bootstrap = все проекты live»; φ12 сохраняет best-effort |
| 04:45 | Эскалация владельцу: host key сменился | доверие — решение владельца (P18); подтверждено пересоздание |
| 05:00-08:20 | Второй полный cold-run после пересоздания | сильнейшая верификация критерия со всеми фиксами |
| 10:37 | Restore round-trip выявил F-11 (P0) | pre-restore снапшот спас данные; фикс + восстановление |
| 12:56 | Легализация WIP параллельной сессии (adopter fix) | CI main красный без неё; фикс верифицирован локально 25/25 |
| 13:11 | Push 6 фикс-коммитов → CI green после легализации | push-чеклист: pre-push hook + CI |

Отклонения от плана: (1) G2 chaos потребовал NODE env (задание давало сырую команду);
(2) healthcheck NODE= по контракту только локально — node-side healthcheck через ssh;
(3) provision-llm канонически запускается на ноде (make-таргет захардкожен на 127.0.0.1:4000);
(4) параллельная сессия (asi-team-vps валидация) коммитила в общий main — мои фиксы
поглощались её коммитами (19b0949), координация зафиксирована в NOTE.

## Section 4 — Release Checklist (итог)

| # | Пункт | Вердикт |
|---|-------|---------|
| 1 | E2E на test-VPS + согласованность | **BLOCKED** (test-VPS недоступна — владелец); согласованность ноды: converge/check-security/e2e-verify ✅ |
| 2 | Chaos FULL | **PASS** 12/12 |
| 3 | CI-гейты | **PASS**: make check (финал) ✅, push-gate ✅, platform-gate-fast ✅, security-scan ✅ (ad44991), check-manifests ✅ |
| 4 | Промоут context-promote | **ВЫПОЛНЕН** (разрешение владельца §0; после зелёных B–G) |
| 5 | Мониторинг после деплоя | проверяется пост-промоут (см. ниже) |

## Overall verdict: **SUCCESS** (частично BLOCKED: D5 CI-канал проекта — биллинг GitHub; G5 test-VPS)

Критерий владельца ВЫПОЛНЕН И ПРОВЕРЕН ДВАЖДЫ: голая нода → `make secrets-unlock` (подготовка
ключа, часть канона) → `make bootstrap-node NODE=tronyx-vps` → сервер healthy (9/9 фаз,
25 контейнеров, S1-S9) + ВСЕ проекты контекста live (3 delivered healthy + oldapp skipped:
нет локального источника/ghcr-образа — решение владельца). Идемпотентность: повторный bootstrap
= SKIP+выборочный re-run, проекты health-skip.

## Next-step suggestions

1. **Владельцу (D5)**: восстановить биллинг GitHub Actions (TronyxLab) → повторить D5:
   `git push` проекта → CI → forced-command receive → healthcheck.
2. **oldapp**: либо убрать из node.yaml#projects (нет источника), либо клонировать в
   ~/projects/tronyx-lab/oldapp — иначе каждый bootstrap репортит skip.
3. **Load-метрики**: litellm_proxy_*/nginx_http_requests_total не экспортируются — добрать
   экспортёры, чтобы load-отчёты были без WARN.
4. **module-level restart**: monitoring `make restart` в module-scope падает (volumes SoT
   в root compose) — при необходимости довести module.mk volumes-контракт.
5. **Следующий DR-drill** (квартальный канон): уже отработан в F2 — повторять с U=platform
   (synthetic-dump тест в test_restore_psql.py защищает регрессию F-11).

## TRAP[DECISION]/TRAP[BUG] созданные в сессии

- TRAP[BUG]: langfuse cold-start (F-03/F-05), vhost silent-success (F-06), R7 prefix,
  benchmark best-of-3 (F-02), T17 stale state, monitoring enabled-semantics (F-09),
  config-init P19 (F-10) + skip-path Rev, rollback contour (D8), restore self-role
  black-hole P0 (F-11).
- TRAP[DECISION]: cross-module depends_on запрещён (D8 per-module), INIT strict /
  UPDATE best-effort, monitoring enabled источник истины = node.yaml, revert попытки
  F-03, restore filter Rev-условия.

## Evidence

- Лог фаз: .ai/plans/020-acceptance-validation/01-Findings.md (F-01..F-11, NOTE, итоги фаз)
- Chaos: /tmp/chaos-20260901/run.log (12/12 PASSED, CHAOS_RC=0)
- CI: run 33513287739 (push-gate PASS), 33513287677 (platform-gate-fast PASS) на ad44991
- Бэкап/restore: /tmp/f1_backup.log, /tmp/f2_restore.log, /tmp/f2b_restore.log (на ноде)
- Коммиты сессии: 7844a17, 64fe57d, 86987a9, 5aa2ea1, 6f08f9e, 269a30b, c4a6893, ecb6114,
  e1f5ee7, 308cbef, ad44991 (+ docs: 39a6476 от параллельной сессии, поглотившей WIP)
