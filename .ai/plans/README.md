# GREP_SUMMARY: plans-README, навигация, коллизия-141, debt-registry-pointer, NNN-allocation-rule
# STRUCTURE: ┌назначение┐ → ◇ единый реестр долга → ◇ коллизия 141 → ◇ правило NNN → ⎋ категории папок

$START_README

# .ai/plans/ — DevPlan artifacts

## Назначение

Management-артефакты платформы (Brief, DevPlan, VerificationReport, StatusReport, Debt)
по схеме `.ai/plans/{NNN:03d}-{slug}/{NN}-{Type}[-{qualifier}].md`.

Полная спецификация: `.kilo/rules/artifacts.md` → `$ARTIFACT_REGISTRY`.

---

## Единый реестр технического долга

**Canonical source:** [`145-repo-cleanup-debt-registry/00-TECHNICAL-DEBT-REGISTRY.md`](145-repo-cleanup-debt-registry/00-TECHNICAL-DEBT-REGISTRY.md)

Все точечные `*-Debt.md` (126, 136, 139) — **исторические снимки**.
При рассогласовании авторитетен единый реестр (R1 artifact-registry: highest-NN wins).

Реестр содержит **67 пунктов** в 9 категориях (A–J):
- 27 OPEN, 3 partially-done, 4 unknown, 2 deferred, 10 monitoring (TRAP Rev), 21 [CLOSED]
- Топ-5 срочных + граф очередей по effort в §СВОДКА реестра.

---

## Коллизия NNN=141

Две независимые задачи одной ночи (06.08.2026) заняли один номер:

| Папка | Тип | Назначение |
|-------|-----|------------|
| `141-server-recovery` | операторская | Восстановление tronyx-vps после chaos-прогонов (StatusReport + VR + evidence/) |
| `141-template-evolution` | стратегическая | Эволюция шаблонов backend/frontend (Brief + DevPlan + MetaDevPlan) |

**Причина:** параллельные сессии одной ночи, правило re-glob перед `mkdir` не выполнено.

**Резолюция (R3 artifact-registry):** *post-merge collisions tolerated, folder identity = full slug*.
При цитировании ВСЕГДА использовать полный slug (`141-server-recovery` / `141-template-evolution`),
**никогда** только NNN.

---

## Правило NNN-аллокации

```
Перед mkdir новой папки:
  1. re-glob .ai/plans/*
  2. NNN = max(existing NNN) + 1
  3. если NNN занят на момент mkdir → инкремент и retry
```

Post-merge коллизии (параллельные worktree) **допустимы** — folder identity = full `NNN-slug` string.
Не перенумеровывать существующие папки.

---

## Категории папок (snapshot 2026-08-11)

| Категория | Папки | Описание |
|-----------|-------|----------|
| **completed** (VR зелёный) | 128, 129, 130, 131, 132, 134 | Полностью завершены, audit-trail |
| **completed-impl** (влито без VR) | 127, 137, 138, 143, 144 | Код в main, VR не создан (git log = audit) |
| **completed-PARTIAL** (открытый долг) | 126, 136, 139, 142 | Реализовано, но есть OPEN-долг (см. реестр) |
| **completed-conditional** | 133, 140 | VR с caveat (gate-RED от чужих правок / P-items) |
| **meta-only** | 141-template-evolution | Стратегический план без реализации |
| **status-only** | 135 | Свёрточный E2E-отчёт (без VR/реализации) |
| **operational** | 141-server-recovery | Операторский цикл восстановления VPS |
| **registry** | 145 | Этот cleanup + единый реестр долга |

---

## Зависимости (граф кросс-ссылок)

```
126 (chaos) ──D-1/D-2──→ 132 (fault-tolerance)  [CLOSED ×15]
126 ──D-3..D-8──→ 140 (debt-close-wave)         [CLOSED ×24]
127 → 131 (cleanup SHELL-RESIDUAL)
128 → 127
129 → 131, 132
130 → 129, 131, 132, 133, 134
131 → закрытие 127-131 реестра
132 → 126 (D-1/D-2), 136
133 → 127, 131
134 → 128, 136, 137, 138
135 → 126-132 (status-обзор)
136 → 126, 132, 134, 140
137 → 133, 136
138 → 129, 136, 137
139 → 131, 138
140 → закрывает долги 126/136
141-template-evolution → 137 (practices)
142 → 141-template-evolution (MetaDevPlan-E2E)
143 → 142, 126 D-7
144 → 143, деплой-фиксы
145 → консолидация долга из всех выше
```

$END_README
