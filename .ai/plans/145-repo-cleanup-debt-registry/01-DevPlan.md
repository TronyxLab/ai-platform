# GREP_SUMMARY: DevPlan 145, repo-cleanup, worktrees, ветки, evidence-архивация, debt-registry, коллизия-141
# STRUCTURE: ┌контекст+диагноз (5 проблем)┐ → ◇ TRAP[DECISION] → ┌код-граф XML┐ → ┌волны W1/W2/W3/W4/W5┐ → ◇ acceptance criteria → ⎋ verification

$START_DEVPLAN

# DevPlan 145 — Чистка репозитория + единый реестр долга

$ARTIFACT_CONTRACT
PURPOSE:               Почистить репозиторий от: (1) слитых worktree-веток, (2) устаревших
                       локальных веток, (3) тяжёлых evidence-папок, (4) коллизии NNN=141,
                       (5) untracked DevPlan-артефактов. Свести ВЕСЬ технический долг
                       проекта в ОДИН canonical файл (`00-TECHNICAL-DEBT-REGISTRY.md`).
DESCRIPTION:           5 волн: W1 — закоммитить untracked DevPlan-артефакты (143/144/07);
                       W2 — удалить 4 слитые feature-ветки + 4 worktree + main-ref;
                       W3 — обновить agent-manager.json (снять висящие ссылки на worktrees);
                       W4 — архивировать evidence-папки (126/files + 141-server-recovery/evidence);
                       W5 — документировать коллизию 141 + создать `.ai/plans/README.md`.
                       Реестр долга (`00-TECHNICAL-DEBT-REGISTRY.md`) уже создан —
                       это pre-requisite волны (вне W1-W5).
RATIONALE:             Разведка 4 субагентов подтвердила: все 4 feature-ветки — ancestor
                       of main (слиты через merge-commit), worktrees чисты (0 незакоммиченных
                       изменений), stash пуст. Dangling-коммиты (~120) — нормальный фон,
                       `git gc` уберёт по расписанию. Операция БЕЗОПАСНА. Единый реестр
                       долга решает проблему «долг размазан по 15+ файлам» (Zero-Context
                       Survival для следующего агента).
ACCEPTANCE_CRITERIA:   AC1: `git worktree list` показывает только основной worktree.
                       AC2: `git branch` показывает только `main` (feature-ветки + main-ref удалены).
                       AC3: `.kilo/agent-manager.json` не содержит висящих ссылок на удалённые worktrees
                       (проверка через `agent_manager list`).
                       AC4: `git status` в основном worktree — clean (untracked DevPlan-артефакты закоммичены).
                       AC5: evidence-папки 126/141 заархивированы (`.tar.gz` в `.ai/plans/_archive/`)
                       ИЛИ подтверждено пользователем «оставить».
                       AC6: `.ai/plans/README.md` документирует коллизию 141 + ссылку на реестр долга.
                       AC7: `00-TECHNICAL-DEBT-REGISTRY.md` существует, содержит все 67 пунктов,
                       топ-5 приоритетов ранжирован.
                       AC8: `make check` зелёный (если применимо — не должно затрагивать код).
IMPLEMENTS:            Запрос оператора 2026-08-11: «почисти репозиторий от девпланов,
                       грязных ворктри и тому подобному, собери весь технический долг из них,
                       составь девплан работ на чистку и составление ОДНОГО ЕДИНОГО файла
                       со всем тех долгом проекта. Используй субагентов для максимальной
                       площади сканирования.»
IMPACTS:               .kilo/worktrees/{142-full-auto-fixes, 142-template-evolution,
                       143-backup-observability-fixes, 144-alert-rules-fixes} — УДАЛЕНИЕ;
                       git branches {142-full-auto-fixes, 142-template-evolution,
                       143-backup-observability-fixes, 144-alert-rules-fixes, main-ref} — УДАЛЕНИЕ;
                       .kilo/agent-manager.json — ОБНОВЛЕНИЕ (через agent_manager tool, НЕ вручную);
                       .ai/plans/{143-backup-observability-fixes, 144-alert-rules-fixes}/ — COMMIT;
                       .ai/plans/142-full-auto-cycle/07-StatusReport.md — COMMIT;
                       .ai/plans/126-chaos-resilience/files/ — АРХИВАЦИЯ (опц.);
                       .ai/plans/141-server-recovery/evidence/ — АРХИВАЦИЯ (опц.);
                       .ai/plans/README.md — СОЗДАНИЕ;
                       .ai/plans/145-repo-cleanup-debt-registry/00-TECHNICAL-DEBT-REGISTRY.md — создан (pre-req).
REQUIRES:              Подтверждение пользователя для: (а) удаления remote-веток origin/142-*,
                       (б) архивации/удаления evidence-папок, (в) `personal` worktree-папки.
$END_ARTIFACT_CONTRACT

---

## 1. Контекст и диагноз (2026-08-11, разведка 4 субагентов)

### Проблема 1: Слитые worktrees и ветки не вычищены

**Состояние:**
- 4 worktree в `.kilo/worktrees/`: `142-full-auto-fixes`, `142-template-evolution`,
  `143-backup-observability-fixes`, `144-alert-rules-fixes`
- 1 не-worktree папка: `personal` (тестовые payload'ы template-evolution, не git-worktree)
- 5 локальных веток: 4 feature + `main-ref` (устаревший снапшот main, на 10+ коммитов позади)
- 2 remote-ветки: `origin/142-full-auto-fixes`, `origin/142-template-evolution`

**Verdict субагента-санитара:**
- Все 4 feature-ветки — **ancestor of main** (`git merge-base --is-ancestor <branch> main` = YES)
- Все 4 worktree **чисты** (`git status --short` = пусто)
- Stash пуст
- Dangling-коммиты (~120) — нормальный фон, не блокер
- **RED-условий НЕТ** — чистка безопасна

### Проблема 2: Untracked DevPlan-артефакты

**Состояние:**
- `.ai/plans/143-backup-observability-fixes/` — полностью untracked (код слит в main, DevPlan нет)
- `.ai/plans/144-alert-rules-fixes/` — полностью untracked (код слит в main, DevPlan нет)
- `.ai/plans/142-full-auto-cycle/07-StatusReport.md` — untracked

**Риск:** потеря документации при `git clean` или clone на новой машине.

### Проблема 3: Коллизия NNN=141

**Состояние:** Две папки с одинаковым номером:
- `141-server-recovery` — операторский цикл восстановления VPS (StatusReport, VR, evidence/65 файлов)
- `141-template-evolution` — стратегическая эволюция шаблонов (Brief, DevPlan, MetaDevPlan)

**Анализ:** Параллельные сессии одной ночи (06.08), правило re-glob перед mkdir не выполнено.
По R3 artifact-registry: *post-merge collisions tolerated, folder identity = full slug*.

**Решение:** НЕ переименовывать (нарушит ссылки `142 → 141` в MetaDevPlan-E2E).
Зафиксировать в `.ai/plans/README.md`.

### Проблема 4: Технический долг размазан по 15+ файлам

**Состояние до 145:**
- 3 явных `*-Debt.md` (126, 136, 139) — каждый со своим форматом
- 6 debt-DevPlan (127-131, 140) — долг внутри DevPlan-тела
- VerificationReport'ы с follow-ups (128-134, 140, 142)
- TRAP-аннотации в коде (5 живых TRAP[DEBT] в тестах)
- TRAP Rev-условия в AGENTS.md (10 monitoring)

**Решение:** Единый canonical реестр `00-TECHNICAL-DEBT-REGISTRY.md` (✅ создан, 67 пунктов).

### Проблема 5: Тяжёлые evidence-папки

**Состояние:**
- `126-chaos-resilience/files/` — 76 файлов, 6M (verdict'ы/логи chaos-инъекций T1-T11)
- `141-server-recovery/evidence/` — 65 файлов, 828K (ci-logs, grafana/loki API-дампы, state-файлы)

**Анализ:** Это операционные данные, не архитектурные. После закрытия долга (126 D-5/T9-T11)
и завершения восстановления (141) — кандидаты на архивацию. **Но** сегодня 11.08, а Rev-дедлайны
126 — 2026-09-15; удалять преждевременно.

---

## 2. TRAP[DECISION]

⚠️ TRAP[DECISION] · 2026-08-11 · HI · Удаление слитых feature-веток безопасно — merge-commit preserved
· Rejected: оставить ветки «на всякий случай» (риск: накопление мёртвых веток, путаница при
  следующем fan-out, .kilo/worktrees раздувается)
· Reason: все 4 ветки — ancestor of main (подтверждено `git merge-base --is-ancestor`).
  Merge-commits (`47f0f962`, `12c2f3f7`, `b7a11860`, `62120d45`) сохраняют полную историю.
  Worktrees чисты (0 незакоммиченных изменений). `git branch -d` (не `-D`) проверит merged.
· Rev: если потребуется bisect по feature-ветке — reflog (30 дней) + dangling-коммиты содержат tips.

⚠️ TRAP[DECISION] · 2026-08-11 · MED · Evidence-папки НЕ удалять до закрытия долга 126
· Rejected: удалить evidence сейчас (риск: долг D-126-D5/T9-T11 имеет Rev 2026-09-15 —
  могут потребоваться baseline-данные для сравнения)
· Reason: архивация (`.tar.gz` в `_archive/`) — безопасный компромисс: освобождает рабочее
  дерево, сохраняет данные. Удаление — после 2026-09-15 при подтверждённом закрытии долга.
· Rev: 2026-09-15 — если D-126-D5/T9-T11 закрыты → удалить архив.

⚠️ TRAP[DECISION] · 2026-08-11 · MED · `personal` worktree-папка — требует подтверждения
· Rejected: удалить без вопроса (риск: тестовые payload'ы template-evolution 141/142
  могут использоваться текущим DevPlan)
· Reason: `personal` содержит `test-evo/` и `test-evo-fe/` (по 3 файла каждый) —
  тестовые шаблоны для эволюции. НЕ git-worktree (нет .git). Если template-evolution
  завершена — удалить; если нет — оставить.
· Rev: после подтверждения статуса template-evolution.

---

## 3. Код-граф (XML)

```xml
<devplan number="145" slug="repo-cleanup-debt-registry">
  <prerequisite>
    <artifact id="00-TECHNICAL-DEBT-REGISTRY" status="DONE"/>
  </prerequisite>
  <wave id="W1" name="commit-untracked-devplan-artifacts" effort="S" blocking="true">
    <task>git add .ai/plans/142-full-auto-cycle/07-StatusReport.md</task>
    <task>git add .ai/plans/143-backup-observability-fixes/</task>
    <task>git add .ai/plans/144-alert-rules-fixes/</task>
    <task>git add .ai/plans/145-repo-cleanup-debt-registry/</task>
    <task>git commit -m "docs(145): 00-TECHNICAL-DEBT-REGISTRY + untracked DevPlan artifacts 143/144/07"</task>
  </wave>
  <wave id="W2" name="remove-merged-worktrees-and-branches" effort="S" requires="W1">
    <task action="git-worktree-remove" path=".kilo/worktrees/142-full-auto-fixes"/>
    <task action="git-worktree-remove" path=".kilo/worktrees/142-template-evolution"/>
    <task action="git-worktree-remove" path=".kilo/worktrees/143-backup-observability-fixes"/>
    <task action="git-worktree-remove" path=".kilo/worktrees/144-alert-rules-fixes"/>
    <task action="git-branch-delete" branch="142-full-auto-fixes" flag="-d"/>
    <task action="git-branch-delete" branch="142-template-evolution" flag="-d"/>
    <task action="git-branch-delete" branch="143-backup-observability-fixes" flag="-d"/>
    <task action="git-branch-delete" branch="144-alert-rules-fixes" flag="-d"/>
    <task action="git-branch-delete" branch="main-ref" flag="-d"/>
    <task optional="true" action="git-push-origin-delete" branches="142-full-auto-fixes,142-template-evolution" requires_user_confirmation="true"/>
  </wave>
  <wave id="W3" name="update-agent-manager-state" effort="S" requires="W2">
    <task action="agent-manager-cleanup" tool="agent_manager" note="НЕ редактировать .kilo/agent-manager.json вручную"/>
  </wave>
  <wave id="W4" name="archive-evidence-folders" effort="S" optional="true" requires_user_confirmation="true">
    <task action="mkdir" path=".ai/plans/_archive/"/>
    <task action="tar-gz" source=".ai/plans/126-chaos-resilience/files/" target=".ai/plans/_archive/126-chaos-files.tar.gz"/>
    <task action="tar-gz" source=".ai/plans/141-server-recovery/evidence/" target=".ai/plans/_archive/141-server-recovery-evidence.tar.gz"/>
    <task action="git-rm-cached" path=".ai/plans/126-chaos-resilience/files/"/>
    <task action="git-rm-cached" path=".ai/plans/141-server-recovery/evidence/"/>
    <task action="git-add" path=".ai/plans/_archive/"/>
    <task note="Освобождает ~7M из рабочего дерева; данные в _archive/ + git history"/>
  </wave>
  <wave id="W5" name="document-collision-and-readme" effort="S" requires="W1">
    <task action="create-file" path=".ai/plans/README.md">
      <content>Документировать: коллизию 141, ссылку на реестр долга, правило NNN-аллокации</content>
    </task>
    <task action="git-commit" message="docs(145): .ai/plans/README.md — коллизия 141 + указатель на debt-registry"/>
  </wave>
  <verification>
    <task action="git-worktree-list" expect="1 worktree (main only)"/>
    <task action="git-branch" expect="main only"/>
    <task action="git-status" expect="clean"/>
    <task action="agent-manager-list" expect="no dangling worktree references"/>
  </verification>
</devplan>
```

---

## 4. Волны

### W1 — Commit untracked DevPlan-artifacts (BLOCKING)

**Цель:** Предотвратить потерю документации.

**Шаги:**
1. `git add .ai/plans/142-full-auto-cycle/07-StatusReport.md`
2. `git add .ai/plans/143-backup-observability-fixes/`
3. `git add .ai/plans/144-alert-rules-fixes/`
4. `git add .ai/plans/145-repo-cleanup-debt-registry/`
5. `git commit -m "docs(145): 00-TECHNICAL-DEBT-REGISTRY + untracked DevPlan artifacts 143/144/07 — repo cleanup W1"`

**Проверка:** `git status` — clean.

### W2 — Remove merged worktrees and branches

**Цель:** Удалить слитые feature-ветки и их worktrees.

**Шаги (порядок важен — сначала worktree, потом ветка):**
1. `git worktree remove .kilo/worktrees/142-full-auto-fixes`
2. `git worktree remove .kilo/worktrees/142-template-evolution`
3. `git worktree remove .kilo/worktrees/143-backup-observability-fixes`
4. `git worktree remove .kilo/worktrees/144-alert-rules-fixes`
5. `git branch -d 142-full-auto-fixes` (безопасно: проверит merged)
6. `git branch -d 142-template-evolution`
7. `git branch -d 143-backup-observability-fixes`
8. `git branch -d 144-alert-rules-fixes`
9. `git branch -d main-ref`

**Опционально (требует подтверждения пользователя):**
- `git push origin --delete 142-full-auto-fixes`
- `git push origin --delete 142-template-evolution`

**Проверка:** `git worktree list` → 1 worktree; `git branch` → `main` only.

### W3 — Update agent-manager state

**Цель:** Снять висящие ссылки на удалённые worktrees из `.kilo/agent-manager.json`.

**⚠️ ВАЖНО:** НЕ редактировать `.kilo/agent-manager.json` вручную (пersisted UI state).
Использовать `agent_manager` tool с `action: "list"` для проверки, затем `action: "stop"`
для каждого висящего session.

**Шаги:**
1. `agent_manager` tool → `action: "list"` — получить текущее состояние
2. Для каждого session, привязанного к удалённому worktree → `action: "stop"` (если применимо)
3. Проверить: `action: "list"` — нет висящих ссылок

### W4 — Archive evidence-folders (ОПЦИОНАЛЬНО, требует подтверждения)

**Цель:** Освободить ~7M из рабочего дерева, сохранив данные в `.tar.gz` + git history.

**⚠️ TRAP[DECISION]:** НЕ удалять evidence до 2026-09-15 (Rev долга D-126-D5/T9-T11).

**Шаги:**
1. `mkdir -p .ai/plans/_archive/`
2. `tar -czf .ai/plans/_archive/126-chaos-files.tar.gz -C .ai/plans/126-chaos-resilience files/`
3. `tar -czf .ai/plans/_archive/141-server-recovery-evidence.tar.gz -C .ai/plans/141-server-recovery evidence/`
4. `git rm -r --cached .ai/plans/126-chaos-resilience/files/`
5. `git rm -r --cached .ai/plans/141-server-recovery/evidence/`
6. `git add .ai/plans/_archive/`
7. `git add .gitignore` (добавить `.ai/plans/126-chaos-resilience/files/` и `.ai/plans/141-server-recovery/evidence/` в ignore — они останутся локально, но не в git)
8. `git commit -m "chore(145): archive evidence 126/files + 141/evidence → _archive/ (7M freed)"`

**Альтернатива (если пользователь отклонит):** оставить как есть, зафиксировать в README.

### W5 — Document collision + README

**Цель:** Зафиксировать коллизию 141 и создать указатель на реестр долга.

**Создать `.ai/plans/README.md`:**

```markdown
# .ai/plans/ — DevPlan artifacts

## Единый реестр долга

**Canonical source:** `145-repo-cleanup-debt-registry/00-TECHNICAL-DEBT-REGISTRY.md`

Все точечные `*-Debt.md` (126, 136, 139) — исторические снимки.
При рассогласовании авторитетен единый реестр.

## Коллизия NNN=141

Две независимые задачи одной ночи (06.08.2026) заняли один номер:
- `141-server-recovery` — операторский цикл восстановления VPS
- `141-template-evolution` — стратегическая эволюция шаблонов

По R3 artifact-registry: post-merge collisions tolerated, folder identity = full slug.
При цитировании ВСЕГДА использовать полный slug, никогда только NNN.

## Правило NNN-аллокации

Перед `mkdir` новой папки — re-glob `.ai/plans/*`, NNN = max existing + 1.
```

---

## 5. Acceptance Criteria (контрольный лист)

- [ ] **AC1:** `git worktree list` → только основной worktree (`/Users/tronyx/projects/ai-platform [main]`)
- [ ] **AC2:** `git branch` → только `* main` (5 веток удалено: 4 feature + main-ref)
- [ ] **AC3:** `agent_manager list` → нет висящих worktree-ссылок
- [ ] **AC4:** `git status` → clean (untracked DevPlan-артефакты закоммичены в W1)
- [ ] **AC5:** Evidence-папки: либо заархивированы (W4), либо подтверждено «оставить»
- [ ] **AC6:** `.ai/plans/README.md` существует, документирует коллизию 141 + ссылку на реестр
- [ ] **AC7:** `00-TECHNICAL-DEBT-REGISTRY.md` существует, 67 пунктов, топ-5 ранжирован ✅ (pre-req done)
- [ ] **AC8:** `make check` зелёный (код не затронут — ожидается PASS)

---

## 6. Verification (post-execution)

```bash
# 1. Worktrees
git worktree list
# Expected: /Users/tronyx/projects/ai-platform  ...  [main]

# 2. Branches
git branch
# Expected: * main

# 3. Status
git status
# Expected: clean working tree (или только intentional untracked)

# 4. Agent Manager
# (через tool agent_manager action=list)
# Expected: нет ссылок на удалённые worktrees

# 5. Registry exists
test -f .ai/plans/145-repo-cleanup-debt-registry/00-TECHNICAL-DEBT-REGISTRY.md && echo "OK"

# 6. README exists
test -f .ai/plans/README.md && echo "OK"

# 7. Untracked DevPlan artifacts committed
git log --oneline -1 -- .ai/plans/144-alert-rules-fixes/
# Expected: коммит из W1
```

---

## 7. Зависимости от подтверждения пользователя

Перед выполнением уточнить у пользователя:

1. **Remote-ветки** `origin/142-full-auto-fixes`, `origin/142-template-evolution` — удалить?
   (Деструктивно на remote; локально уже чисто.)
2. **Evidence-папки** (W4) — архивировать в `.tar.gz` или оставить как есть?
3. **`personal` worktree-папка** (`.kilo/worktrees/personal/`) — удалить?
   (Содержит тестовые payload'ы `test-evo`/`test-evo-fe` для template-evolution 141/142.)
4. **`git gc --prune=now`** — запустить для чистки 120+ dangling commits?
   (Опционально; git gc по расписанию уберёт сам.)

$END_DEVPLAN
