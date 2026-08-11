# GREP_SUMMARY: TECHNICAL-DEBT-REGISTRY, ai-platform, единый реестр долга, OPEN-items, TRAP-Rev, cleanup-source
# STRUCTURE: ┌метаданные┐ → ◇ легенда статусов → ┌A.Shell→Python (закрыт)┐ → ┌B.Test-infra┐ → ┌C.Ops/Bootstrap┐ → ┌D.Security┐ → ┌E.Contracts┐ → ┌F.Cleanup (закрыт)┐ → ┌G.Operations┐ → ┌H.TRAP-Rev monitoring┐ → ┌I.In-code TRAP[DEBT]┐ → ⎋ топ-приоритет

$START_REGISTRY

# Единый реестр технического долга — ai-platform

> **Единственный canonical source правды о долге проекта.**
> Все точечные `*-Debt.md` в подпапках `.ai/plans/` — исторические снимки;
> при рассогласовании **авторитетен этот файл** (R1 artifact-registry).
>
> **Source-агрегация:** 4 параллельных субагента отсканировали `.ai/plans/**`
> (20 папок, 73 артефакта), кодовую базу (TODO/FIXME/TRAP/noqa/type:ignore),
> git-состояние (worktrees/ветки/dangling), DevPlan-зависимости.

$ARTIFACT_CONTRACT
PURPOSE:               Единый canonical реестр ВСЕГО технического долга ai-platform —
                      открытого, частично-сделанного, отложенного (deferred), и условного
                      (TRAP Rev-условия). Закрытые долги включены как audit-trail
                      (помечены `[CLOSED]`) — они объясняют эволюцию, но не требуют действий.
DESCRIPTION:           9 категорий (A–I), 67 пунктов. Каждый пункт: Source, Severity,
                      Status, Description, Rev/Trigger, Effort. Топ-приоритет в §СВОДКА.
                      Формат: `D-<NNN>-<slug>` для долга из DevPlan NNN,
                      `D-H<N>` для TRAP-Rev из AGENTS.md, `D-I<N>` для in-code TRAP[DEBT].
RATIONALE:             До этого файла долг был размазан по 3 `*-Debt.md` (126/136/139),
                      6 debt-DevPlan (127-131, 140), VerificationReport'ам (follow-ups),
                      StatusReport'ам и TRAP-аннотациям в коде. Поиск «что ещё надо сделать?»
                      требовал чтения 15+ файлов. Единый реестр решает это (Zero-Context
                      Survival: следующий агент читает один файл). Исторические `*-Debt.md`
                      остаются как audit-trail, но не как source of truth.
ACCEPTANCE_CRITERIA:   AC1: все OPEN/partially-done/unknown/deferred долги из `.ai/plans/**`
                      и кода присутствуют с полными метаданными. AC2: каждый `[CLOSED]`
                      пункт содержит commit-hash или DevPlan-номер закрытия. AC3: дубли
                      между источниками отмечены `[DUP]`. AC4: TRAP Rev-условия
                      (latent debt) выделены в отдельную категорию H с триггерами.
                      AC5: топ-5 приоритетов явно ранжирован в §СВОДКА.
IMPLEMENTS:            Решение оператора 2026-08-11 — «собери весь технический долг
                      из DevPlan'ов в ОДИН ЕДИНЫЙ файл».
IMPACTS:               `.ai/plans/145-repo-cleanup-debt-registry/00-TECHNICAL-DEBT-REGISTRY.md`
                      (этот файл). Не влияет на код. Исторические `*-Debt.md` (126/136/139)
                      остаются как есть — переадресация на этот файл добавлена в их заголовки
                      отдельной задачей (не в скоупе 145).
REQUIRES:              — (read-only агрегация).
$END_ARTIFACT_CONTRACT

**Дата составления:** 2026-08-11
**Версия реестра:** 1.0
**Source-coverage:** 20/20 DevPlan-папок, 225 файлов с TRAP-аннотациями, git-worktree audit
**Создано волной:** 145-repo-cleanup-debt-registry

---

## Легенда статусов

| Status | Значение | Действие |
|--------|----------|----------|
| **OPEN** | Долг активен, требует работы | Запланировать |
| **partially-done** | Часть сделана, остаток явный | Доработать |
| **unknown** | Статус неясен (нет VerificationReport) | Уточнить через git log /verify |
| **deferred** | Сознательно отложено (out-of-scope DevPlan) | Проверять триггер |
| **[CLOSED]** | Закрыто, оставлено как audit-trail | Ничего не делать |
| **monitoring** | TRAP Rev-условие — latent debt | Проверять триггер периодически |

**Severity:** HI (блокер/безопасность/данные), MED (функциональный долг), LOW (cosmetic/process).
**Effort:** S (≤1 день), M (1-3 дня), L (3-7 дней), XL (неделя+).

---

## Категория A — Shell→Python миграция

> Категория исторически закрыта (волны 127-131, 140). Оставлена как audit-trail.

| ID | Severity | Status | Effort | Описание | Source / Закрыто |
|----|----------|--------|--------|----------|------------------|
| D-127-install-tor-proxy | MED | [CLOSED] | M | `install-tor-proxy.sh` 321→25 LOC + `install_tor_proxy.py` | `e6ec95f` (127 W1) |
| D-127-node-resolver | MED | [CLOSED] | M | `node-resolver.sh` 215→99 LOC + `shared/node_resolver.py` | `e6ec95f` (127 W2) |
| D-127-issue-cert | LOW | deferred | — | `issue-cert.sh` (700 LOC, acme.sh CLI) — keep by design | AGENTS.md keep-таблица; **Rev: 2027-02** (стабилизация acme.sh API ≥6 мес) |
| D-128-docker-ops | MED | [CLOSED] | M | `shared/docker_ops.py` (6 потребителей + фасад), гейт `docker_sole_path` | `9eda35f` (128 W1) |
| D-128-inline-python3 | LOW | [CLOSED] | S | 3 inline `python3 -c`/heredoc — whitelist пуст | `9eda35f` (128 W4) |
| D-128-w5-fixes | LOW | [CLOSED] | S | 6 мелких Python-фиксов (gen_env, s3, postgres, nginx-dual-keep) | `9eda35f` (128 W5) |

**Итог A:** 6 пунктов (5 [CLOSED], 1 deferred). Открытого долга нет. `issue-cert.sh` — единственный
latent item с Rev 2027-02.

---

## Категория B — Тестовая инфраструктура

| ID | Severity | Status | Effort | Описание | Source / Закрыто |
|----|----------|--------|--------|----------|------------------|
| D-129-T2-T6 | MED | [CLOSED] | M | 5 тестовых долгов (litellm crash, spool_volume, vacuous-check, tmp_path, e2e-error) | `4593652` (129 W1) |
| D-129-xdist-race | MED | [CLOSED] | S | `wc -l` гонка с xdist-воркёрами + probe-файлы вне tmp_path | `4593652` (129 W2) |
| D-129-reload-race | HI | [CLOSED] | L | `reload_safe.py` канон (0 `del sys.modules`), висящий прогон 1276s → таймаут | `4593652` (129 W4) |
| D-129-pytest-timeout | MED | [CLOSED] | S | `pytest-timeout>=2.3.0`, static=300s | `4593652` (129 W4) |
| D-129-D13-D14 | LOW | [CLOSED] | S | 2 stale TRAP-снятия | `4593652` (129 W5) |
| **D-139-T1** | **HI** | **OPEN** | **S** | `core/entrypoints/deploy.sh` (175 LOC, keep-решение 119 D8) — верификация brief A на production: 0 вызовов в audit-логах → удаление. **Код НЕ трогать.** StatusReport после ближайшего деплоя. | `.ai/plans/139-test-system-stewardship/03-Debt.md` T-1; **Rev: 2026-11-01** |
| D-139-R1 | LOW | [CLOSED] | S | render-monitoring hook-тест | 139 W4 |
| **D-134-S4-MaxStartups** | **LOW** | **OPEN** | **S** | `security_posture.py:234-243` проверяет MaxStartups ≥ 30:50:200, но нет negative-теста `test_negative_maxstartups_below_minimum` (MaxStartups=10:30:100 → FAIL) | VR 134 Finding 1; делегировано в 136 Coder W8 |

**Итог B:** 8 пунктов (6 [CLOSED], 2 **OPEN**: D-139-T1 HI, D-134-S4 LOW). D-139-T1 —
ближайший дедлайн (2026-11-01), но требует только операторского отчёта, не кода.

---

## Категория C — Ops / Bootstrap (крупнейший кластер открытого долга)

| ID | Severity | Status | Effort | Описание | Source / Rev |
|----|----------|--------|--------|----------|--------------|
| D-130-dev-metrics | MED | [CLOSED] | S | `make dev-metrics` | `ed549a5` (130 W1) |
| **D-130-P3-4** | **MED** | **partially-done** | **M** | POSTGRES_PASSWORD rotation: runbook `ROTATION.md` создан (6 шагов), **автоматизация НЕ реализована**. Риск рассинхрона потребителей (litellm, backup-cron). | 130 W3; **Rev ≤ 2026-11-04** (ротация ≥1/квартал ИЛИ автоматизация) |
| D-130-D24-mirror | LOW | [CLOSED] | S | mirror.yml force-sync keep + 4-шаговая процедура | `ed549a5` (130 W4); **Rev 2026-10-21** |
| D-130-D15-D2 | LOW | [CLOSED] | S | 2 stale TRAP (pydantic, GHCR push) | `ed549a5` (130 W2/W4) |
| **D-126-D5** | **MED** | **OPEN** | **M** | OOM-инъекция clickhouse не верифицирована (T7 жертвой стал bash-аллокатор; restart-политика под OOM НЕ проверена). | 126 Debt D-5; **Rev 2026-09-15** |
| **D-126-T9-T11** | **MED** | **OPEN** | **XL** | Chaos-программа T9-T11 (cert/secrets corruption, restore-drill, reboot) не выполнена — сервер пересоздан. Требует реальные LE-сертификаты (ACME rate-limit вне харнесса). | 126 Debt + 136 Debt T9-T11; **Rev 2026-09-15** |
| **D-136-B6** | **MED** | **OPEN** | **M** | CI-канал деплоя вне харнесса: git push → workflow → VPS. CI dry-run деплоя реального проекта + верификация на ноде — не покрыто. | 136 Debt B6; **Rev 2026-10-21** |
| **D-136-B7** | **MED** | **OPEN** | **L** | Реальные LE-сертификаты вне харнесса (ACME rate-limit, DNS-01). | 136 Debt B7; **Rev 2026-09-15** (совместно с T9-T11) |
| **D-136-W9-T9.19-legacy** | **LOW** | **OPEN** | **S** | `.hc_done_in_deploy` маркер без суффикса контекста (φ11 `docker.py:354`); per-context форма есть, legacy-маркер на существующих нодах не мигрирован. | 136 Debt; переоформлено 140-W1; **Rev: ближайший node-update** |
| **D-136-W10-nginx-sudoers** | **LOW** | **OPEN** | **S** | nginx `systemctl restart nginx` sudoers на Docker-нодах — legacy (nginx в контейнере). Осталось для non-Docker нод. | 136 Debt; переоформлено 140-W1; **Rev 2026-10-21** |
| **D-136-W10-S-13-drill** | **HI** | **OPEN** | **L** | DR-drill AGE мастер-ключа НЕ выполнен (T12.12): off-node encrypted backup + restore-first на пересозданной ноде. Требует операторского окна + sops/KMS. | 136 Debt W10-S-13; **Rev 2026-08-31** ⚠️ **БЛИЖАЙШИЙ ДЕДЛАЙН** |
| **D-142-Chaos-T6-T10** | **MED** | **OPEN** | **L** | Chaos T6-T10 формально RED: T6 postgres-sigkill маркеры; T7 oom-clickhouse victim; T8 disk-pressure ENOSPC; T9 cert corruption; T10 restore-drill пустой вывод. T4/T11 — GREEN. Требует отдельного диагностического плана. | VR 142 §6; StatusReport 142:51 |
| **D-136-state-store-IMP9** | **LOW** | **OPEN** | **S** | `save_state()` в `state_store.py` не имеет IMP:9 логов (flock, tmp→rename, коррапт-детекция). | VR 136:253,321 |

**Итог C:** 13 пунктов (5 [CLOSED], 8 **OPEN**: 1 HI, 4 MED, 3 LOW). Самый тяжёлый кластер.
Срочные: **D-136-W10-S-13-drill (HI, 2026-08-31)**, затем **D-126-D5/T9-T11/D-136-B7 (2026-09-15)**.

---

## Категория D — Безопасность

| ID | Severity | Status | Effort | Описание | Source |
|----|----------|--------|--------|----------|--------|
| **D-134-L3** | **MED** | **OPEN** | **M** | trivy (L1/L2 образы) + pip-audit (requirements.txt) + dependabot `pip` ecosystem — CI-сканирование уязвимостей. L4-детекция (S8) реализована, L3 — нет. | 134 DevPlan §5 Follow-up |
| **D-134-L4** | **MED** | **OPEN** | **M** | Scheduled weekly CI `hermes-build-context` → push → `node-update` — автопересборка после S8-детекции. Детекция есть, автопересборка — follow-up. | 134 DevPlan §5 Follow-up |
| **D-134-L5** | **LOW** | **OPEN** | **L** | fail2ban (SSH) + auditd + интеграция `security_posture.py --json` в Loki/Grafana + check-security в converge non-blocking. Фундамент `--json` заложен в W2. | 134 DevPlan §5 Follow-up |
| **D-136-W11-C-8-residual** | **MED** | **OPEN** | **M** | `VPS_SSH_KEY` в CI workflow env остаётся (root-shell доступ). forced-command ci-deploy верифицирован, но ключ в env — риск MIGRATE/промоут-сценариев. Аудит всех использований + сужение до ci-deploy forced-command. | 136 Debt W11-C-8-residual; **Rev 2026-10-21** |
| **D-142-R15-B29** | **HI** | **OPEN (External/RED)** | **S** | Утрачена приватная пара `ci-deploy` (platform_personal_cicd) оператором при чистке ключей → `make deploy-project` / e2e MODE=remote **недоступны**. Решение: (а) выгрузить CI_DEPLOY_KEY из gh-секрета, (б) регенерация пары + authorized_keys (1 ручное SSH), (в) root-dispatch канал (неканон). | VR 142 §4.4 R15; StatusReport 142:47 |
| **D-142-R17** | **MED** | **OPEN (External)** | **S** | Docker Hub rate-limit в CI (apk add rsync). Нет `docker/login` к Docker Hub в workflow. Решение: `docker/login-action` + `DOCKER_HUB_USERNAME/TOKEN` в gh secrets. | VR 142 §4.4 R17 |

**Итог D:** 6 пунктов (все **OPEN**: 1 HI, 4 MED, 1 LOW). D-142-R15 — **блокер** для
`make deploy-project`/e2e remote, требует оператора.

---

## Категория E — Шаблоны / Контракты / Make-targets

| ID | Severity | Status | Effort | Описание | Source |
|----|----------|--------|--------|----------|--------|
| **D-137** | **HI** | **unknown** | **XL** | Project-practices (наследование ruff/pre-commit/CI-gate/verify-контрактов без копипаста, baseline/full, эскалатор). Реализовано (коммиты `bdaa3f6d`/`42b9aebd`, интеграция I1-I7 в 142 VR §3), но **VerificationReport НЕ создан**. | 137 DevPlan; нет VR |
| **D-138** | **MED** | **unknown** | **M** | Make-targets slim (78→75 .PHONY, 74→70 глаголов). Глоссарий AGENTS.md показывает реализацию, но **VR не создан**. | 138 DevPlan; нет VR |
| **D-142-B37** | **MED** | **OPEN** | **S** | Frontend-шаблон `templates/template-frontend` без `package-lock.json` → `npm ci` FAIL в CI (K2). Решение: добавить lock ИЛИ сменить K2 на `npm install`. | VR 142 §4.4 R18; StatusReport 142:48 |
| **D-142-B38** | **MED** | **OPEN** | **S** | `pipx install --force` перед каждым push → push «зависал» + hook-прогоны gate давали flake-фейлы (probe-тесты), отсутствующие в ручном `make gate MODE=fast`. | VR 142 §5 B38; коммит `4248a1e9` (Debt-запись); StatusReport 142:59 |
| D-133-096-reference | LOW | [CLOSED] | S | Битая ссылка на `.ai/debt/096` (удалён) в `core/internal/shared/AGENTS.md:100` | 131 W4 (`b845299`) |
| D-138-render-monitoring | MED | [CLOSED] | M | `run_monitoring_reconfig()` экстракция + post_deploy_chain | 138 W3 |

**Итог E:** 6 пунктов (2 **unknown** (137, 138 — дописать VR или принять git log как audit),
2 **OPEN** (B37, B38), 2 [CLOSED]).

---

## Категория F — Cleanup-волны (историческая, закрыта)

| ID | Severity | Status | Effort | Описание | Source / Закрыто |
|----|----------|--------|--------|----------|------------------|
| D-131-registry-removal | LOW | [CLOSED] | S | Удаление `.ai/debt/` (5 файлов) | `b845299` (131 W1) |
| D-131-TRAP-DEBT-removal | LOW | [CLOSED] | M | ~45 TRAP[DEBT] удалены из 37 файлов; `rg "TRAP\[DEBT\]"` вне .kilo = 0 в коде (5 остаются в тестах — см. категорию I) | `b845299` (131 W2) |
| D-131-gate-registry | LOW | [CLOSED] | S | `test_gate_debt_registry.py` + manifest-запись удалены | `b845299` (131 W3) |
| D-140-W1-sync | LOW | [CLOSED] | S | Синхронизация Debt-реестров 126/136 (3 CLOSED-by-код) | 140 W1 |

**Итог F:** 4 пункта (все [CLOSED]). Категория оставлена как audit-trail: показывает, что
исторические `.ai/debt/` реестры уже однажды чистились (131), но долг мигрировал в
`*-Debt.md` внутри DevPlan-папок — текущий реестр (этот файл) консолидирует обе волны.

---

## Категория G — Операционные инциденты / Observability

| ID | Severity | Status | Effort | Описание | Source |
|----|----------|--------|--------|----------|--------|
| **D-143-W1A-W1B-W2** | **HI** | **partially-done** | **M** | Backup observability: (1) promtail file-scrape для BACKUP COMPLETE маркера; (2) cron env inheritance через `/etc/environment`; (3) High Memory guard для контейнеров без limits. W1A/W1B/W2 реализованы (`bcb2e741`, `b7a11860`, `95cc9145`), но 144 обнаружил остаточный дефект Loki binop → 144 W1. | 143 DevPlan |
| **D-144-W1-W2-W3** | **HI** | **partially-done** | **M** | Alert-rules fixes: (1) Backup Freshness Loki binop `< 1` → threshold-only; (2) `$labels.container` → `$labels.name` (cAdvisor); (3) memory limits cadvisor 256→512M, loki/clickhouse подняты. W1+W2+W3 слиты (`62120d45`), но потребовали 2 follow-up коммита (`f671491e` cadvisor 256→512M, `891e2393` high_memory на `working_set_bytes` — usage включает page cache). **VR не создан.** | 144 DevPlan; деплой-фиксы |
| **D-143-memory-limits-projects** | **LOW** | **deferred** | **S** | Шаблоны проектов не задают `deploy.resources.limits.memory` → корень +Inf. Guard 143 W2 достаточен, сознательно НЕ трогаем (поведенческое изменение OOM-семантики). | 143 DevPlan §9 Out-of-scope |
| **D-143-logrotate** | **LOW** | **deferred** | **S** | Ротация `/var/log/platform/backup/*.log` — файлы растут без logrotate. Не критично (объём мал), promtail позиции устойчивы. | 143 DevPlan §9 Out-of-scope |
| **D-135-hermes-500** | **LOW** | **OPEN** | **M** | `hermes.tronyx.ru` корневой редирект → 500 на basic-auth redirect. Upstream-квирк приложения v2026.7.7.2 (логин-страница 200, сервис healthy). Требует upstream-фикса или патча L2. | StatusReport 135:147,206 |
| **D-140-P1** | LOW | [CLOSED] | S | `alert-rules.yml:13` `telegram-webhook` → `Telegram Critical / Telegram Warning` (DRIFT-D3) | **Подтверждено закрытым 2026-08-11** — `alert-rules.yml:13` содержит корректные имена. Закрыто в ходе составления этого реестра. |
| **D-140-P5** | LOW | **unknown** | S | Runtime validation Phase 5 (140 VR) — BLOCKED bash permission rule. Состояние тестов после merge 140 неясно. | VR 140 P5 |

**Итог G:** 7 пунктов (2 **partially-done** HI, 2 deferred, 1 **OPEN** LOW, 1 [CLOSED], 1 unknown).

---

## Категория H — TRAP Rev-условия (latent debt, мониторинг триггеров)

> Это **условный** долг: фикс требуется только при срабатывании триггера.
> Источник: root `AGENTS.md` TRAP[DECISION] + TRAP в коде.

| ID | Severity | Status | Trigger | Описание | Source |
|----|----------|--------|---------|----------|--------|
| D-H1 | HI | [CLOSED] | — | Bootstrap forced-command → orchestrator_cli dispatch | AGENTS.md; Rev снято волной 117 D1 |
| **D-H2** | **HI** | **monitoring** | **2026-10-21** | Строгий гейт фантомов (0 упоминаний 4 удалённых имён, allowlist пуст). Пересмотр, если начнёт блокировать легитимную историческую документацию. | AGENTS.md TRAP 2026-08-01 |
| **D-H3** | **HI** | **monitoring** | второй shell-потребитель SSH-флагов | `shared/ssh_opts.py` Python SoT; `lib/ssh.sh` — фасад. Пересмотр при появлении 2-го shell-потребителя. | AGENTS.md TRAP 2026-08-01 |
| **D-H4** | **HI** | **monitoring** | новое состояние контейнера | Healthcheck-критерий канон: running AND (healthy\|""\|none) = здоров. Пересмотр при новом состоянии. | AGENTS.md TRAP 2026-08-01 |
| **D-H5** | **HI** | **monitoring** | L1 несёт context-specific data | L1 push ghcr.io (disaster recovery, public package). Контексты НЕ используют L1 как runtime. | AGENTS.md TRAP 2026-08-01 |
| **D-H6** | **MED** | **monitoring** | shell-скрипт >500 LOC с inline python3 | Strangler-Fig decomposition canonical pattern. | AGENTS.md TRAP 2026-07-22 |
| **D-H7** | **HI** | **monitoring** | **2026-10-21** | Enforcement языковой политики через parity-гейты (COMPOSE_PROFILES, PLATFORM_DOMAIN, template coverage, cross-layer imports). Пересмотр при ложно-блокировках. | AGENTS.md TRAP 2026-07-31 |
| **D-H8** | **HI** | **monitoring** | CI-deploy стабильно <300s | SSH staging-gate для `lib/ssh.sh` (single point of failure). Снизить timeout 600s→400s при стабильном <300s. | AGENTS.md TRAP 2026-07-21 |
| **D-H9** | **HI** | **monitoring** | **2026-10-22** | Decision Gate: Python-First strategy (4114→395 shell LOC, −90%). Переоценка метрик после ≥2 недель на production. | AGENTS.md TRAP 2026-07-22 |
| **D-H10** | **HI** | **monitoring** | deploy-context >5min | Bootstrap pipeline redesign: deploy-context as step 18 (index 23). Сделать async при >5min. | AGENTS.md TRAP 2026-07-22 |
| D-H11 | LOW | **monitoring** | overrides structure changes | Loki runtime config placeholder (`loki-runtime-config.yml:20`) | код, единственный Rev вне AGENTS.md |

**Итог H:** 11 пунктов (1 [CLOSED], 10 **monitoring**). Ближайшие даты пересмотра:
**2026-10-21** (D-H2, D-H7), **2026-10-22** (D-H9). До этих дат — без действий.

---

## Категория I — In-code TRAP[DEBT] (живые маркеры в тестах)

> Source: кодовый скан. Вне `.ai/plans/` и `.kilo/` — 5 живых TRAP[DEBT], все в `tests/unit/`.

| ID | Severity | Файл:строка | Описание |
|----|----------|-------------|----------|
| **D-I1** | **MED** | `tests/unit/test_vhost_configurator.py:25` | `configure_vhost_for_project` НЕ существует в `vhost_renderer.py` — тест фиксирует контракт для отсутствующей функции. Либо реализовать функцию, либо удалить тест. |
| D-I2 | LOW | `tests/unit/test_compose_validator.py:21` | `try_parse_compose`: отсутствующий compose-файл при недоступном docker → FileNotFoundError вместо best-effort skip |
| D-I3 | LOW | `tests/unit/test_practices_check_project.py` | W1-тест пишет РЕАЛЬНЫЕ аудит-записи в `/var/log/platform/audit.jsonl` (side-effect) |
| D-I4 | LOW | `tests/unit/test_generate_catalog.py` | `generate_catalog.py` вызывает `logging.basicConfig(force=True)` — побочный эффект |
| D-I5 | LOW | `core/modules/nginx/config/security-headers.conf:20` | `TODO: migrate to nonce/hash-based CSP after SPA code audit` — единственный живой TODO в коде |

**Итог I:** 5 пунктов (1 MED, 4 LOW). D-I1 — единственный потенциально-функциональный
(тест на несуществующую функцию); остальные — test-hygiene.

---

## Категория J — Документация / Process-долг (найдено при чистке 145)

| ID | Severity | Status | Effort | Описание |
|----|----------|--------|--------|----------|
| **D-J1** | **MED** | **OPEN** | **S** | **Untracked DevPlan-артефакты 143/144/07**: `.ai/plans/143-backup-observability-fixes/`, `.ai/plans/144-alert-rules-fixes/`, `.ai/plans/142-full-auto-cycle/07-StatusReport.md` — код слит в main, но DevPlan-файлы НЕ в git. Закоммитить. |
| **D-J2** | **LOW** | **OPEN** | **S** | **Коллизия NNN=141**: `141-server-recovery` + `141-template-evolution` — две независимые задачи одной ночи (06.08). По R3 artifact-registry post-merge collisions tolerated, folder identity = full slug. Документировать в `.ai/plans/README.md` (если создаётся). |
| **D-J3** | **LOW** | **OPEN** | **S** | **VR-гэп**: планы 127, 137, 138, 143, 144 — реализованы (коммиты в main), но VerificationReport не создан. Решение: либо дописать ретроспективные VR, либо принять `git log` как audit-trail (зафиксировать решение). |
| **D-J4** | **LOW** | **OPEN** | **S** | **Evidence-папки**: `126-chaos-resilience/files/` (76 файлов, 6M) + `141-server-recovery/evidence/` (65 файлов, 828K) — операционные данные (логи инъекций/восстановления), не архитектурные. Кандидаты на архивацию/удаление после закрытия долга. |

**Итог J:** 4 пункта (все **OPEN**, 1 MED, 3 LOW). D-J1 — должен быть закрыт в ходе чистки 145.

---

## §СВОДКА — топ-приоритет

### Топ-5 срочных (ранжировано по дедлайну + критичности)

| # | ID | Sev | Дедлайн | Effort | Действие |
|---|-----|-----|---------|--------|----------|
| 1 | **D-142-R15-B29** | **HI** | **немедленно** (блокер) | S | Утрачена пара `ci-deploy` → `make deploy-project`/e2e remote недоступны. **Требует оператора**: регенерация пары + authorized_keys (1 ручное SSH) ИЛИ выгрузка CI_DEPLOY_KEY из gh-секрета. |
| 2 | **D-136-W10-S-13-drill** | **HI** | **2026-08-31** | L | DR-drill AGE мастер-ключа: off-node encrypted backup + restore-first на пересозданной ноде. Требует операторского окна + sops/KMS. |
| 3 | **D-126-D5 / T9-T11 / D-136-B7** | MED | **2026-09-15** | M+XL+L | Chaos-окно на пересозданной ноде с реальными LE-сертификатами: OOM-clickhouse verification, cert/secrets corruption, restore-drill, reboot, ACME rate-limit. Объединить в один operational window. |
| 4 | **D-139-T1** | HI | **2026-11-01** | S | `deploy.sh` verifications brief A: после ближайшего деплоя — StatusReport (0 вызовов в audit-логах → удаление). Код НЕ трогать. |
| 5 | **D-142-R17** | MED | после R15 | S | Docker Hub rate-limit в CI: `docker/login-action` + `DOCKER_HUB_USERNAME/TOKEN` в gh secrets. |

### Граф очередей по effort

| Effort | Пункты OPEN | Суммарная оценка |
|--------|-------------|------------------|
| S | D-139-T1, D-134-S4, D-142-R15, D-142-R17, D-142-B37, D-142-B38, D-136-W9-T9.19, D-136-W10-nginx-sudoers, D-136-state-store-IMP9, D-I1..I5, D-J1..J4 | ~16×S = 16 дней |
| M | D-130-P3-4, D-126-D5, D-136-B6, D-134-L3, D-134-L4, D-136-W11-C-8, D-137(VR), D-143-W1A, D-144-W1, D-135-hermes-500 | ~10×M = 30 дней |
| L | D-136-W10-S-13-drill, D-142-Chaos-T6-T10, D-136-B7, D-134-L5 | ~4×L = 20 дней |
| XL | D-126-T9-T11 (operational), D-137 (если дописывать VR) | ~2×XL = 3+ недели |

### Категории без открытого долга (audit-only)

- **A (Shell→Python):** 5 [CLOSED], 1 deferred (issue-cert.sh, Rev 2027-02)
- **F (Cleanup-волны):** 4 [CLOSED]

### Категории с monitoring-only (TRAP Rev, без действий до даты)

- **H:** 10 monitoring. Ближайшая дата: **2026-10-21** (D-H2, D-H7)

---

## §Метрики реестра

| Метрика | Значение |
|---------|----------|
| Всего пунктов | **67** |
| OPEN (требуют работы) | **27** |
| partially-done | **3** |
| unknown (уточнить) | **4** |
| deferred (out-of-scope) | **2** |
| monitoring (TRAP Rev) | **10** |
| [CLOSED] (audit-trail) | **21** |
| HI severity OPEN | **4** (D-142-R15, D-136-W10-S-13, D-139-T1, + D-143/D-144 partially) |
| Ближайший дедлайн | **2026-08-31** (D-136-W10-S-13-drill) |
| Блокер продакшена | **1** (D-142-R15 — ci-deploy ключ) |

---

$END_REGISTRY
