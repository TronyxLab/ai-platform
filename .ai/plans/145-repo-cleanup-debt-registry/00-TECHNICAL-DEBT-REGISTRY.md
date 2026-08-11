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
**Версия реестра:** 2.0 (synced — DevPlan 145 W2, верификация 2026-08-11)
**Source-coverage:** 20/20 DevPlan-папок, 225 файлов с TRAP-аннотациями, git-worktree audit
**Создано волной:** 145-repo-cleanup-debt-registry
**Synced волной:** 145-repo-cleanup-debt-registry W2 (02-DevPlan §1.1–1.4 верификация)

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
| D-134-S4-MaxStartups | LOW | [CLOSED] | S | ~~negative-тест отсутствует~~ **CLOSED 145 W2**: `test_security_posture_maxstartups.py::test_negative_default_10_30_100` (MaxStartups=10:30:100 → FAIL, точный вход бага R5) + `test_negative_component_below` (rate 40<50 → FAIL). Negative-покрытие есть (имя иное, чем в реестре v1.0). | VR 134 Finding 1; **закрыто 145 W2 верификацией 2026-08-11** |

**Итог B:** 8 пунктов (6 [CLOSED], 2 **OPEN**: D-139-T1 HI, D-134-S4 LOW). D-139-T1 —
ближайший дедлайн (2026-11-01), но требует только операторского отчёта, не кода.

---

## Категория C — Ops / Bootstrap (крупнейший кластер открытого долга)

| ID | Severity | Status | Effort | Описание | Source / Rev |
|----|----------|--------|--------|----------|--------------|
| D-130-dev-metrics | MED | [CLOSED] | S | `make dev-metrics` | `ed549a5` (130 W1) |
| D-130-P3-4 | MED | [CLOSED] | M | ~~POSTGRES_PASSWORD rotation runbook~~ **CLOSED 145 W3** (решение оператора 2026-08-11): ротация отменена. ROTATION.md удалён, комментарий ротации в docker-compose.base.yml заменён TRAP[DECISION]. Пароль генерируется при initdb/bootstrap, смена env НЕ ротирует. Rev: если появится требование ротации — переоткрыть с автоматизацией. | 130 W3; **закрыто 145 W3 решением 2026-08-11** |
| D-130-D24-mirror | LOW | [CLOSED] | S | mirror.yml force-sync keep + 4-шаговая процедура | `ed549a5` (130 W4); **Rev 2026-10-21** |
| D-130-D15-D2 | LOW | [CLOSED] | S | 2 stale TRAP (pydantic, GHCR push) | `ed549a5` (130 W2/W4) |
| **D-126-D5** | **MED** | **OPEN** | **M** | OOM-инъекция clickhouse не верифицирована (T7 жертвой стал bash-аллокатор; restart-политика под OOM НЕ проверена). | 126 Debt D-5; **Rev 2026-09-15** |
| **D-126-T9-T11** | **MED** | **OPEN** | **XL** | Chaos-программа T9-T11 (cert/secrets corruption, restore-drill, reboot) не выполнена — сервер пересоздан. Требует реальные LE-сертификаты (ACME rate-limit вне харнесса). | 126 Debt + 136 Debt T9-T11; **Rev 2026-09-15** |
| D-136-B6 | MED | partially-done | M | CI-канал деплоя: core-deploy ВЕРИФИЦИРОВАН (141: SSH✅/rsync✅, баг scripts/ найден и фикс в core-deploy.yml:190-199 TRAP[BUG]). Project-деплой через CI workflow НЕ верифицирован — закроется после R15 (145 W1). | 136 Debt B6; **обновлено 145 W2 (core-канал ✅ 141)** |
| D-136-B7 | MED | partially-done | L | Реальные LE-сертификаты: 4 домена — реальные LE (не self-signed), восстановлены из S3-кеша и валидированы (certs-r2.md). НО 0 вызовов `acme.sh --issue` — fresh-выпуск (ACME DNS-01) не тестирован. Chaos-окно 2026-09-15 (145 W5). | 136 Debt B7; **обновлено 145 W2 (restore ✅, fresh-issue ❌)** |
| D-136-W9-T9.19-legacy | LOW | [CLOSED] | S | ~~`.hc_done_in_deploy` маркер без суффикса контекста~~ **CLOSED 145 W2**: оба узла (tronyx-vps, test-e2e) пересозданы 2026-08-06 (141) — legacy-маркеров физически нет; код пишет per-context (orchestrator_metrics.py:132-133). Obsolete-by-recreation. | 136 Debt; **закрыто 145 W2 верификацией 2026-08-11** |
| D-136-W10-nginx-sudoers | LOW | [CLOSED] | S | ~~nginx systemctl sudoers на Docker-нодах~~ **CLOSED 145 W3**: sudoers nginx удалены из setup-node.sh (обе ноды Docker, nginx в контейнере, systemctl unit not found на test-VPS). Rev: вернуть при появлении non-Docker ноды. | 136 Debt; **закрыто 145 W3 (D-136-W10)** |
| D-136-W10-S-13-drill | HI | OPEN | L | DR-drill AGE мастер-ключа НЕ выполнен (T12.12): off-node encrypted backup + restore-first на пересозданной ноде. Требует операторского окна + sops/KMS. | 136 Debt W10-S-13; **Rev 2026-08-31** ⚠️ **БЛИЖАЙШИЙ ДЕДЛАЙН** |
| D-142-Chaos-T6-T10 | MED | OPEN | L | Chaos r2 (attempt #30, 2026-08-06): T4/T5/T6 GREEN; T1/T2/T3/T7/T8/T9/T10/T11 RED (прогон шёл во время восстановления ноды — частично сконфаунжен). Требует chaos-окна на provisioned-ноде (145 W5, до 2026-09-15). | VR 142 §6; **описание обновлено 145 W2 по chaos-r2.log** |
| D-136-state-store-IMP9 | LOW | [CLOSED] | S | ~~save_state() без IMP:9 логов~~ **CLOSED 145 W3**: IMP:9 добавлен после _atomic_write_json (flock + tmp→rename, путь, mode, node, steps count). | VR 136:253,321; **закрыто 145 W3 (D-136-state-store-IMP9)** |

**Итог C:** 13 пунктов (9 [CLOSED] в т.ч. 145 W2/W3, 4 OPEN: 1 HI, 2 MED, 1 LOW).
Срочные: **D-136-W10-S-13-drill (HI, 2026-08-31)**, затем **D-126-D5/T9-T11/D-136-B7 (2026-09-15)**.
W3 закрыл: D-136-W9-T9.19, D-136-W10-nginx-sudoers, D-136-state-store-IMP9, D-130-P3-4.
W2 уточнил: D-136-B6 (core ✅, project — после W1), D-136-B7 (restore ✅, fresh-issue ❌), D-142-Chaos (по r2).

---

## Категория D — Безопасность

| ID | Severity | Status | Effort | Описание | Source |
|----|----------|--------|--------|----------|--------|
| **D-134-L3** | **MED** | **OPEN** | **M** | trivy (L1/L2 образы) + pip-audit (requirements.txt) + dependabot `pip` ecosystem — CI-сканирование уязвимостей. L4-детекция (S8) реализована, L3 — нет. | 134 DevPlan §5 Follow-up |
| **D-134-L4** | **MED** | **OPEN** | **M** | Scheduled weekly CI `hermes-build-context` → push → `node-update` — автопересборка после S8-детекции. Детекция есть, автопересборка — follow-up. | 134 DevPlan §5 Follow-up |
| **D-134-L5** | **LOW** | **OPEN** | **L** | fail2ban (SSH) + auditd + интеграция `security_posture.py --json` в Loki/Grafana + check-security в converge non-blocking. Фундамент `--json` заложен в W2. | 134 DevPlan §5 Follow-up |
| D-136-W11-C-8-residual | MED | [CLOSED] (resolved-as-decision) | M | ~~`VPS_SSH_KEY` в CI env — root-shell риск~~ **CLOSED 145 W2**: переоформлен в мониторинг — core-deploy.yml:49-60 TRAP[DECISION] 2026-08-05 (root-shell риск-принят, setup-ssh/cleanup/known_hosts, Rev 2026-10-21). | 136 Debt W11-C-8-residual; **закрыто 145 W2 как resolved-as-decision** |
| D-142-R15-B29 | HI | OPEN (External/RED) | S | Утрачена приватная пара `ci-deploy` (platform_personal_cicd) оператором при чистке ключей → `make deploy-project` / e2e MODE=remote **недоступны**. Путь (а) «выгрузить из gh-секрета» НЕВОЗМОЖЕН (секрета нет, 14 секретов в gh). Решение (145 W1 TRAP[DECISION]): восстановить из локальной пары `~/.ssh/platform_personal_cicd(.pub)` (pub на ноде работает, 141: receive SUCCESS). | VR 142 §4.4 R15; **путь уточнён 145 W1** |
| D-142-R17 | MED | partially-done | S | Docker Hub rate-limit в CI: механизм ЕСТЬ (`platform-test.yml:171-174` docker/login-action, DOCKER_HUB_AUTH-гейт, C-11). НО DOCKER_HUB_USERNAME/DOCKER_HUB_TOKEN отсутствуют в gh-секретах → DOCKER_HUB_AUTH='false' → анонимные пуллы. Закроется после 145 W1 (оператор предоставляет токен). | VR 142 §4.4 R17; **обновлено 145 W2 (механизм ✅, секреты ❌)** |

**Итог D:** 6 пунктов (1 [CLOSED] resolved-as-decision W2, 1 partially-done W2, 4 OPEN: 1 HI, 2 MED, 1 LOW).
D-142-R15 — **блокер** для `make deploy-project`/e2e remote (W1 восстанавливает секрет из локальной пары).
D-142-R17 — partially-done (механизм ✅, секреты ❌ — W1).

---

## Категория E — Шаблоны / Контракты / Make-targets

| ID | Severity | Status | Effort | Описание | Source |
|----|----------|--------|--------|----------|--------|
| D-137 | HI | [CLOSED] | XL | ~~Project-practices без VR~~ **CLOSED 145 W2** (решение оператора 2026-08-11): реализовано коммитами `bdaa3f6d`/`42b9aebd`, интеграция I1-I7 верифицирована в 142 VR §3. VR не создаётся ретроспективно — git log + 142 VR §3 приняты как evidence (TRAP[DECISION] 145 W2). | 137 DevPlan; **закрыто 145 W2 решением (J3)** |
| D-138 | MED | [CLOSED] | M | ~~Make-targets slim без VR~~ **CLOSED 145 W2** (решение оператора 2026-08-11): глоссарий AGENTS.md показывает реализацию (75 глаголов). VR не создаётся — git log + 142 VR §3 как evidence. | 138 DevPlan; **закрыто 145 W2 решением (J3)** |
| D-142-B37 | MED | [CLOSED] | S | ~~Frontend-шаблон без package-lock.json~~ **CLOSED 145 W2**: `templates/template-frontend/package-lock.json` существует, добавлен коммитом `bcb2e741` (143 W+B37). `npm ci` работает. | VR 142 §4.4 R18; **закрыто 145 W2 верификацией 2026-08-11** |
| D-142-B38 | MED | [CLOSED] | S | ~~`pipx install --force` перед каждым push~~ **CLOSED 145 W3**: pipx-блок удалён из pre-push-gate.sh (probe-тесты закрыты 129 W2, pipx не давал ценности). | VR 142 §5 B38; **закрыто 145 W3 (D-142-B38)** |
| D-133-096-reference | LOW | [CLOSED] | S | Битая ссылка на `.ai/debt/096` (удалён) в `core/internal/shared/AGENTS.md:100` | 131 W4 (`b845299`) |
| D-138-render-monitoring | MED | [CLOSED] | M | `run_monitoring_reconfig()` экстракция + post_deploy_chain | 138 W3 |

**Итог E:** 6 пунктов (5 [CLOSED] в т.ч. 145 W2/W3, 1 [CLOSED] D-133).
W2 закрыл: D-137, D-138 (решение J3), D-142-B37 (верификация).
W3 закрыл: D-142-B38 (pipx удалён).

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
| D-143-W1A-W1B-W2 | HI | [CLOSED] | M | ~~Backup observability partially-done~~ **CLOSED 145 W2**: W1A/W1B/W2 реализованы (`bcb2e741`, `b7a11860`, `95cc9145`), остаточный Loki binop закрыт 144 W1. | 143 DevPlan; **закрыто 145 W2 верификацией 2026-08-11** |
| D-144-W1-W2-W3 | HI | [CLOSED] | M | ~~Alert-rules fixes partially-done~~ **CLOSED 145 W2**: W1+W2+W3 слиты (`62120d45`) + follow-ups `f671491e` (cadvisor 256→512M) + `891e2393` (working_set_bytes). `alert-rules.yml:43-45` — expr без binop `< 1`. | 144 DevPlan; **закрыто 145 W2 верификацией 2026-08-11** |
| **D-143-memory-limits-projects** | **LOW** | **deferred** | **S** | Шаблоны проектов не задают `deploy.resources.limits.memory` → корень +Inf. Guard 143 W2 достаточен, сознательно НЕ трогаем (поведенческое изменение OOM-семантики). | 143 DevPlan §9 Out-of-scope |
| **D-143-logrotate** | **LOW** | **deferred** | **S** | Ротация `/var/log/platform/backup/*.log` — файлы растут без logrotate. Не критично (объём мал), promtail позиции устойчивы. | 143 DevPlan §9 Out-of-scope |
| **D-135-hermes-500** | **LOW** | **OPEN** | **M** | `hermes.tronyx.ru` корневой редирект → 500 на basic-auth redirect. Upstream-квирк приложения v2026.7.7.2 (логин-страница 200, сервис healthy). Требует upstream-фикса или патча L2. | StatusReport 135:147,206 |
| **D-140-P1** | LOW | [CLOSED] | S | `alert-rules.yml:13` `telegram-webhook` → `Telegram Critical / Telegram Warning` (DRIFT-D3) | **Подтверждено закрытым 2026-08-11** — `alert-rules.yml:13` содержит корректные имена. Закрыто в ходе составления этого реестра. |
| D-140-P5 | LOW | [CLOSED] | S | ~~Runtime validation Phase 5 unknown~~ **CLOSED 145 W2**: VR 140:217 — P5 BLOCKED только bash-permission на момент VR; после merge 140 циклы 141-145 прогнали gates зелёными — affected-тесты исполнялись многократно. | VR 140 P5; **закрыто 145 W2 верификацией 2026-08-11** |

**Итог G:** 7 пунктов (5 [CLOSED] в т.ч. 145 W2, 2 deferred, 1 OPEN LOW).
W2 закрыл: D-143-W1A-W1B-W2, D-144-W1-W2-W3, D-140-P5 (верификация).
OPEN: D-135-hermes-500 (LOW, W5 диагноз).

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

## Категория I — In-code TRAP[DEBT] (закрыты волной 145 W3)

> Source: кодовый скан. Все 5 TRAP[DEBT] в `tests/unit/` и `core/modules/nginx/config/` **сняты волной 145 W3**.

| ID | Severity | Файл | Статус | Описание |
|----|----------|------|--------|----------|
| D-I1 | MED | `tests/unit/test_vhost_configurator.py:25` | [CLOSED] 145 W3 | `configure_vhost_for_project` **реализован** в `vhost_renderer.py` (D-I1) — Python API primary path (D4) активирован; тест переведён с mock на реальный вызов. |
| D-I2 | LOW | `tests/unit/test_compose_validator.py:21` | [CLOSED] 145 W3 | `try_parse_compose`: except расширен на FileNotFoundError/OSError → best-effort None; R5-negative тест обновлён (returns_none вместо raises). |
| D-I3 | LOW | `tests/unit/test_practices_check_project.py` | [CLOSED] 145 W3 | audit_logger.write_audit_entry monkeypatched через autouse fixture — 0 side-effects на /var/log/platform/audit.jsonl. |
| D-I4 | LOW | `tests/unit/test_generate_catalog.py` | [CLOSED] 145 W3 | `logging.basicConfig(force=True)` перемещён в `_setup_logging()` (вызов из main()); module-level side-effect убран. |
| D-I5 | LOW | `core/modules/nginx/config/security-headers.conf:20` | [CLOSED] 145 W3 | TODO → `TRAP[DEBT]`-нотация с Rev (после SPA-аудита, Rev 2026-11-01); 0 живых TODO в коде. |

**Итог I:** 5 пунктов (все [CLOSED] 145 W3). `rg "TRAP\[DEBT\]" core/ tests/` = 0 вне .ai/plans/.

---

## Категория J — Документация / Process-долг (найдено при чистке 145)

| ID | Severity | Status | Effort | Описание |
|----|----------|--------|--------|----------|
| D-J1 | MED | [CLOSED] | S | ~~Untracked DevPlan-артефакты 143/144/07~~ **CLOSED 145 W2**: `git ls-files` — 142/07-StatusReport.md, 143/01-DevPlan.md, 144/01-DevPlan.md tracked; коммит `db7db81d` (145 W1). | **закрыто 145 W2 верификацией** |
| D-J2 | LOW | [CLOSED] | S | ~~Коллизия NNN=141 не задокументирована~~ **CLOSED 145 W2**: `.ai/plans/README.md` tracked; коммит `6be4896c` (145 W5) документирует коллизию 141. | **закрыто 145 W2 верификацией** |
| D-J3 | LOW | [CLOSED] | S | ~~VR-гэп 127/137/138/143/144~~ **CLOSED 145 W2** (решение оператора 2026-08-11, TRAP[DECISION] 145): VR не создаются ретроспективно — git log + 142 VR §3 (I1-I7) приняты как evidence. 127 полностью закрыт коммитами `e6ec95f`/`9eda35f`/`4593652`. | **закрыто 145 W2 решением (TRAP[DECISION])** |
| D-J4 | LOW | OPEN | S | **Evidence-папки**: `126-chaos-resilience/files/` (76 файлов, 6M) + `141-server-recovery/evidence/` (65 файлов, 828K) — операционные данные. Архивация — в 145 W4; удаление — после 2026-09-15. | **зависит от 145 W4 + chaos closed** |

**Итог J:** 4 пункта (3 [CLOSED] 145 W2, 1 OPEN D-J4 — зависит от W4 + chaos-окна).

---

## §СВОДКА — топ-приоритет

### Топ-5 срочных (ранжировано по дедлайну + критичности)

> Обновлено 145 W2 (2026-08-11). После W1 (секреты) R15/R17 закрываются; топ-5 смещается к операционным окнам.

| # | ID | Sev | Дедлайн | Effort | Действие |
|---|-----|-----|---------|--------|----------|
| 1 | **D-142-R15-B29** | **HI** | **немедленно** (блокер) | S | Восстановить CI_DEPLOY_KEY из локальной пары `~/.ssh/platform_personal_cicd` → gh secret set (145 W1 TRAP[DECISION]). После — B6 закрывается. |
| 2 | **D-136-W10-S-13-drill** | **HI** | **2026-08-31** | L | DR-drill AGE мастер-ключа: off-node encrypted backup + restore-first на пересозданной ноде. Требует операторского окна + sops/KMS (145 W5). |
| 3 | **D-126-D5 / T9-T11 / D-136-B7 / D-142-Chaos** | MED | **2026-09-15** | M+XL+L+L | Единое chaos-окно на provisioned-ноде (145 W5): OOM-clickhouse victim (T7), T8 ENOSPC, T9 cert corruption, T10 restore-drill, T11 reboot, fresh ACME DNS-01 (B7); regression T4/T5/T6; финальные статусы T1-T11 → реестр. |
| 4 | **D-139-T1** | HI | **2026-11-01** | S | `deploy.sh` verifications brief A: SSH-проверка audit-лога (0 вызовов deploy.sh как forced-command) → удалить deploy.sh + обновить manifest (145 W5). |
| 5 | **D-135-hermes-500 / D-134-L3 / D-134-L4** | LOW/MED/MED | после W1 | M/M/M | hermes-500 диагноз (upstream или L2-патч); L3 security-scan.yml (trivy+pip-audit, 145 W4); L4 hermes-nightly.yml (scheduled, 145 W4). |

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

> Пересчитано 145 W2 (2026-08-11, v2.0). После W1/W4/W5 — ещё ~8 пунктов закроются.

| Метрика | v1.0 (2026-08-11) | v2.0 (2026-08-11, после W2/W3) |
|---------|-------------------|-------------------------------|
| Всего пунктов | 67 | 67 |
| OPEN (требуют работы) | 27 | **10** (W1/W5 операторские + L3/L4 W4 + hermes-500) |
| partially-done | 3 | **3** (D-136-B6, D-136-B7, D-142-R17) |
| unknown (уточнить) | 4 | **0** (все уточнены W2) |
| deferred (out-of-scope) | 2 | 3 (D-127-issue-cert, D-143-memory-limits, D-143-logrotate) |
| monitoring (TRAP Rev) | 10 | 10 |
| [CLOSED] (audit-trail) | 21 | **41** (+20 закрыты W2/W3) |
| HI severity OPEN | 4 | **3** (D-142-R15, D-136-W10-S-13, D-139-T1) |
| Ближайший дедлайн | 2026-08-31 | **2026-08-31** (D-136-W10-S-13-drill) |
| Блокер продакшена | 1 (D-142-R15) | **1** (D-142-R15 — W1 восстанавливает) |

### Что закрыла волна 145 (W2 + W3)

- **W2 (синхронизация реестра):** 11 пунктов → [CLOSED] (D-134-S4, D-J1, D-J2, D-142-B37, D-140-P5, D-136-W9, D-143, D-144, D-130-P3-4, D-137, D-138) + D-J3 по решению + D-136-W11 resolved-as-decision + 3 partially-done уточнены + D-142-Chaos описание по r2.
- **W3 (код-чистка):** 8 пунктов → [CLOSED] (D-136-W10-nginx-sudoers, D-130-P3-4 артефакты, D-142-B38, D-136-state-store-IMP9, D-I1..I5).
- **Итого закрыто 145 W2+W3:** 20 пунктов.

---

$END_REGISTRY
