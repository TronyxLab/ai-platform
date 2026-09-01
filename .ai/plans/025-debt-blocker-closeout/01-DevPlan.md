$START_DEVPLAN

# DevPlan 025 — Закрытие DEBT/блокеров из хвостов девпланов + удаление выполненных планов

## $ARTIFACT_CONTRACT

| Поле | Значение |
|------|----------|
| PURPOSE | Собрать все хвостовые DEBT/блокеры девпланов (BLOCKED / NEEDS OWNER / Debt / Rev-условия) в единый реестр, закрыть их по подтверждённым вердиктам владельца и удалить выполненные планы. |
| DESCRIPTION | (1) Два внешних блокера, державших «хвосты» у серии валидационных планов, сняты владельцем 2026-09-01: **D5 GitHub-billing РАБОТАЕТ**, **G5 test-VPS НЕ НУЖНА**. (2) Два DR-долга в `core/AGENTS.md §3` с Rev 2026-08-31 — закрываются (backup off-node заведён и проверен F3 age-key-backup; drill на test-VPS снимается как ненужный). (3) Выполненные валидационные/merge-review планы, чьи единственные хвосты были этими внешними блокерами, удаляются. (4) Активные не-блокирующие TRAP[DEBT] в коде — НЕ трогаются (наблюдения, не блокеры). |
| RATIONALE | Хвосты — это не код, а незакрытый статус: внешние блокеры (billing/test-VPS) уже сняты владельцем, а планы продолжают висеть «BLOCKED», создавая ложное впечатление незавершённости. Удаление выполненных планов освобождает `.ai/plans/` от мёртвого контекста; DR-долги Rev-due (2026-08-31) требуют явного закрытия, иначе следующий агент снова начнёт их «догонять». |
| ACCEPTANCE_CRITERIA | (1) Единый реестр всех хвостовых блокеров/DEBT составлен (секция §1). (2) `core/AGENTS.md §3`: `DR-offnode-backup` помечен CLOSED, `DR-drill` помечен DISMISSED (test-VPS не нужна) — оба с датой 2026-09-01. (3) Выполненные планы из §3 Delete-list удалены (`git rm`), `make check` зелёный после удаления. (4) Планы с открытыми реальными хвостами сохранены (§3 Keep-list). (5) `make agent-check` exit 0. (6) Коммит: `docs(025): debt/blocker closeout — delete completed plans, close DR debts` (≤2 коммита, U-83). |
| IMPLEMENTS | Closeout всех хвостовых статусов планов 011–023 + DR-долги core/AGENTS.md. |
| IMPACTS | `core/AGENTS.md` (§3 DR), `.ai/plans/011–023` (удаление ~12 папок), план-уровневый Debt 013. |
| REQUIRES | **Предусловие: DevPlan 024 слит в main** (024 закрывает 3 code-хвоста 022 и правит root `AGENTS.md` + `core/internal/bootstrap/AGENTS.md` — разные файлы с core/AGENTS.md, но общий git-индекс требует последовательного коммита). НЕ требует VPS. |

---

## 1. Реестр хвостовых блокеров и DEBT (коллекция)

### 1.1 Внешние блокеры — сняты владельцем 2026-09-01

| ID | Суть | Вердикт владельца | Где зафиксирован |
|----|------|-------------------|------------------|
| D5 | GitHub Actions billing TronyxLab «payments failed / spending limit» — CI-канал deploy недоступен | ✅ **РАБОТАЕТ** | 011 (D5), 015 (D5: «5 проектов ждут CI payload»), 017 (D5), 020-acceptance (D5) |
| G5 | test-VPS недоступна — блокирует chaos/load/test-node/drill | ✅ **НЕ НУЖНА** | 011 (G5), 014 (G5), 017 (G5), 021 (G5), 022-launch-asi (G5) |
| S3-креды asi (F-05) | Timeweb S3 `InvalidAccessKeyId` — блок C2 cache-drill | ✅ **ЗАКРЫТО** (uncommitted: F-05 closure, C2 drill GREEN) | 020-launch-asi (F-05) |
| F-036 | load-test PromQL pull vs `AllowTcpForwarding=no` | ✅ **снят вместе с G5** | 012 (F-036), 017 (G3) |

### 1.2 DR-долги — `core/AGENTS.md §3` (Rev 2026-08-31, overdue)

| Debt | Текущий статус | Действие |
|------|---------------|----------|
| `DR-offnode-backup` (строка ~219-221) | «закрыто кодом: AGE_RECIPIENT в secret-definitions» + F3 age-key-backup PASS (tronyx-vps + asi-team-vps) | **CLOSED** — пометить закрытым, дата 2026-09-01 |
| `DR-drill` (строка ~222) | «DR-drill на test-VPS — Debt» | **DISMISSED** — test-VPS не нужна (владелец) |

### 1.3 План-уровневый Debt

| Артефакт | Элемент | Действие |
|----------|---------|----------|
| `.ai/plans/013-resilience-drills-rework/02-Debt.md` | D-013a (абсолютные `\ref`) | уже TRAP[ARCHIVED] 2026-08-27 (b889551) |
| `.ai/plans/013-resilience-drills-rework/02-Debt.md` | D-013b (LO, worktree-parity операторских .env) | **KEEP** — policy-задача, не блокер |

### 1.4 Миграция asi-group — `AGENTS.md:416` TRAP[DEBT] (MED)

**KEEP** — операторское действие вне репо (создать `<org>/asi-group-overlay`, snapshot `platform/` → push, `repos.core` → overlay). Единственный реальный незакрытый layout-долг. Rev: следующий деплой в контексте asi-group.

### 1.5 Source TRAP[DEBT] — не-блокирующие наблюдения

**НЕ удаляются, НЕ закрываются в этом плане** (это латентные наблюдения в коде, а не хвосты девпланов):

- 3 хвоста 022 — `resolve.py:109` (glob), `context_initializer.py:253` (deploy key), `context_overlay.py:242` (runbook-link) → **закрываются DevPlan 024** (вне скоупа 025).
- Остальные (LO/MED, non-blocking): `orphan_reconciler.py:148`, `deploy_orchestrator.py:978`, `lifecycle.py:117`, `vps_readiness.py:90`, `manifest_oracle.py:59`, `orchestrator.py:119`, `content_hash.py:10`, `vhost_renderer.py:1017`, `project_scaffolder.py:769`, `system.py:802`, тестовые (`test_gate_vhost_nginx_t.py:289`, `test_gate_cross_layer.py:202`, `test_shared_s3_client.py:28`, `test_sudoers_generator.py:258`, `test_loadtest_runner.py:32`, `_conftest/compose.py:506`, `_conftest/e2e.py:46`). → **KEEP**, пересмотр в отдельном плане techdebt-свипа.

## 2. Решения закрытия (per-item)

| # | Элемент | Действие | @rationale |
|---|---------|----------|------------|
| C1 | D5 billing | Резолв через удаление планов (в планах статус не правится — план удаляется) | Блокер снят владельцем; правка статуса в удаляемом файле — мусорная работа |
| C2 | G5 test-VPS | Резолв через удаление планов | test-VPS не нужна; DR-drill помечается DISMISSED отдельно (core/AGENTS.md) |
| C3 | S3-креды asi | F-05 закрыт (uncommitted) — коммитится в 024-цикле или этим планом | Уже есть GREEN-доказательство C2 drill |
| C4 | DR-offnode-backup | CLOSED в core/AGENTS.md §3 | AGE_RECIPIENT заведён (secret-definitions) + F3 age-key-backup PASS — Rev-условие выполнено |
| C5 | DR-drill | DISMISSED в core/AGENTS.md §3 | test-VPS не нужна (владелец) — drill на test-VPS снимается как требование |
| C6 | 013 D-013b | KEEP | policy «worktree parity», не блокер, отдельная задача |
| C7 | asi-group миграция | KEEP (operator) | вне репо; требует создания GitHub-репо `<org>/asi-group-overlay` |

## 3. Delete/Keep списки девпланов

### 3.1 DELETE — выполненные, хвосты = внешние блокеры (сняты)

Политика удаления (для Coder): папка удаляется если **все** условия:
- старший (highest-NN) артефакт имеет финальный вердикт (PASS / PASS_WITH_CONDITIONS / SUCCESS / PROVEN / READY);
- единственные открытые хвосты — D5 / G5 / S3-креды / F-036 (все сняты §1.1) ИЛИ план вытеснен более поздним планом того же контура (same context, выше NNN);
- нет невытесненного DRIFTED-находки или незакрытого code-DEBT.

| Папка | Основание удаления |
|-------|-------------------|
| `018-validation-closeout` | VR + Findings, closeout tronyx-vps завершён |
| `019-asi-group-pilot-integration` | StatusReport + VR; пилот вытеснен полноценным запуском (020/022) |
| `020-acceptance-validation` | StatusReport SUCCESS (2 blocked external = D5+G5 — сняты) |
| `020-launch-validation-asi-team-vps` | VR PASS + F-05 закрыт (C2 drill GREEN) |
| `021-merge-review-asi-team-vps` | VR PASS (хвосты G5/F — сняты/минимальный контекст) |
| `022-context-folder-merge` | реализация слита (c6f5d58), хвосты перешли в 024 |
| `022-launch-validation-asi-team-vps` | VR PASS (хвосты G5 — сняты) |
| `023-merge-review-asi-team-vps` | VR PASS |

Под удаление после проверки вытеснения (Coder сверяет нет уникальных открытых items):
| `011-launch-validation-tronyx-vps` | вытеснен 014 → 017 → 018 (нода пересоздавалась, P0-фиксы вошли в поздние планы) |
| `014-launch-validation-tronyx-vps` | вытеснен 017 → 018 |
| `017-launch-validation-tronyx-vps` | вытеснен 018 (closeout) + 020-acceptance (SUCCESS) |

### 3.2 KEEP — открытые реальные хвосты или активная работа

| Папка | Причина |
|-------|---------|
| `001`–`010` | старые планы; `002`/`010` имеют DRIFTED-находки (не вытеснены); удаление теряет cross-session контекст |
| `012-fast-bootstrap-deploy` | F-036 load-test + fast-bootstrap — закрыт только частично |
| `013-resilience-drills-rework` | D-013b открыт (policy) |
| `015-post-launch-fixes` | D5 снят, НО «5 проектов ждут CI payload» — сам деплой ещё не выполнен (оператор) |
| `016-cross-plan-techdebt` | сводный techdebt, не выполнен |
| `024-022-tails-closeout` | **АКТИВЕН** — реализуется параллельной сессией |
| `meta-refactoring` | umbrella-план с post-VR fix-DevPlans (16/17) — исторический источник серии 011–018 |

## 4. Draft Code Graph (что меняется)

```
core/AGENTS.md §3 (DR-мастер-ключа AGE / восстановление)
  → DR-offnode-backup: «Debt (Rev 2026-08-31)» → «CLOSED 2026-09-01»
  → DR-drill: «Debt (Rev 2026-08-31)» → «DISMISSED 2026-09-01 (test-VPS не нужна)»

git rm -r .ai/plans/<Delete-list>/*   (12 папок из §3.1)
  → make check зелёный (нет ссылок на удалённые планы в коде)
  → git commit docs(025)
```

Изменений в бизнес-логике НЕТ — только документация и удаление артефактов.

## $TASKS

### TASK-1 — Закрыть DR-долги в core/AGENTS.md §3
**Владелец:** Coder · **Сложность:** 1/10 · **Файлы:** `core/AGENTS.md`

- Прочитать §3 (строки ~195-225, секция «DR мастер-ключа AGE» / «Процедура восстановления (DR-drill)»).
- `DR-offnode-backup` → пометить `CLOSED 2026-09-01` (AGE_RECIPIENT заведён в secret-definitions; F3 age-key-backup PASS на tronyx-vps и asi-team-vps).
- `DR-drill` → пометить `DISMISSED 2026-09-01` (test-VPS не нужна — вердикт владельца).
- Сохранить остальной текст §3 без изменений.

**Acceptance:** grep `core/AGENTS.md` содержит `CLOSED 2026-09-01` и `DISMISSED 2026-09-01`; не осталось `DR-offnode-backup`/`DR-drill` с Rev 2026-08-31 в статусе «Debt».

### TASK-2 — Удалить выполненные планы
**Владелец:** Coder · **Сложность:** 3/10 · **Файлы:** 12 папок `.ai/plans/*` (§3.1)

- Для каждой папки из §3.1: перед удалением прочитать старший артефакт (highest-NN VR/StatusReport), подтвердить финальный вердикт + отсутствие уникальных открытых хвостов, не покрытых §1.1.
- **Fail-safe:** если обнаружен НЕ-снятый уникальный хвост (не D5/G5/S3/F-036 и не вытесненный) — папку НЕ удалять, добавить в отчёт как KEEP-исключение.
- Удалить через `git rm -r <папка>` (все папки — tracked; `logs/`-симлинки удаляются вместе с папкой).
- Проверить: `grep -rn "0XX-" core/ tests/` (по удалённым NNN) — нет ссылок на удалённые планы в коде/AGENTS.md.

**Acceptance:** §3.1-папки отсутствуют в `git status` (staged deletion); `make check` зелёный после удаления; отчёт: какие удалены, какие оставлены (с причиной).

### TASK-3 — Верификация + коммит
**Владелец:** Coder · **Сложность:** 2/10

1. `make check` до чистоты (батч; удаление планов не должно ронять сьюты — если референс на удалённый план найден, убрать/обновить).
2. `make agent-check` exit 0.
3. Коммит `docs(025): debt/blocker closeout — delete completed plans, close DR debts` (после 024-цикла; ≤2 коммита на DevPlan, U-83). Push — по запросу владельца.

**Acceptance:** `make check` зелёный; `make agent-check` clean; журнал `.ai/logs/runs.jsonl` фиксирует чистый прогон.

## 5. Риски

| # | Риск | Митигация |
|---|------|-----------|
| R1 | Конфликт с параллельной сессией 024 (общий git-индекс, правки AGENTS.md) | **Последовательность:** TASK-1/2/3 исполняются ПОСЛЕ merge 024 в main; core/AGENTS.md ≠ root AGENTS.md ≠ bootstrap/AGENTS.md (разные файлы) |
| R2 | Удаление папки с не-снятым уникальным хвостом | Fail-safe в TASK-2: не удалять, отчитаться |
| R3 | Удаление ломает ссылку в коде на план | TASK-2 grep по NNN; TASK-3 make check подтверждает |
| R4 | 013 D-013b ошибочно закрыт | НЕ в скоупе — KEEP (C6) |

## 6. Non-goals

- **Миграция asi-group** — оператор (C7), вне репо.
- **Закрытие source TRAP[DEBT]** (LO/MED наблюдения §1.5) — отдельный techdebt-свип.
- **Деплой 5 проектов из 015** — оператор (billing восстановлен, но сам push→CI ещё не выполнен).
- **024 code-хвосты** (glob/deploy-key/runbook) — параллельная сессия.

## 7. Next Steps (после merge DevPlan 024)

```
### Wave 1 (единственная, последовательно после 024)
Используй роль Coder: прочитай .ai/plans/025-debt-blocker-closeout/01-DevPlan.md,
реализуй TASK-1 (DR-долги core/AGENTS.md) → TASK-2 (удаление планов по §3.1 с fail-safe)
→ TASK-3 (make check до чистоты, make agent-check, коммит docs(025)).
```

$END_DEVPLAN
