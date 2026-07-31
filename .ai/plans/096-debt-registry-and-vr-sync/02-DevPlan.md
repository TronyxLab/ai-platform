$START_DEVPLAN
# DevPlan 096 — Debt Registry & VerificationReports Sync

$ARTIFACT_CONTRACT
PURPOSE:               Актуализировать tracking-артефакты: создать единый Debt-registry
                       (`.ai/debt/096-Residual-Debt.md`) для всех незакрытых долгов (085-RC3
                       blockers, OPEN-087/088/089, SHELL-RESIDUAL, COSMETIC, LESSON_LEARNED) и
                       пометить SUPERSEDED устаревшие VerificationReports планов 073/074/076/078/086.
                       Прогресс реализации существует (код закоммичен), но не отслежен в
                       артефактах — это создаёт иллюзию «идём по кругу» (Brief §RATIONALE).
DESCRIPTION:           Документационная задача (zero code). (1) Создать `.ai/debt/096-Residual-Debt.md`
                       с 5 разделами: OPEN-087/088/089, COSMETIC, SHELL-RESIDUAL, 085-RC3-BLOCKERS,
                       LESSON_LEARNED. (2) Добавить STATUS UPDATE (SUPERSEDED) в последние VR планов
                       073/074/076/078/086 без переписывания исторического контента (journal-модель).
                       (3) Верифицировать фактическое закрытие 3 CRITICAL blockers из RC3 Gap Analysis
                       (C1 shared/ ad-hoc, C2 gate invisible, C3 watchdog hardcoded paths) против
                       текущего кода и зафиксировать вердикт по каждому в debt-реестре.
RATIONALE:             Текущий debt registry содержит только `011-Debt.md` (2026-07-18) и
                       `091-residual-Debt.md` (6 findings из 091). Нет учёта 085-RC3 blockers,
                       SHELL-RESIDUAL, COSMETIC. VR 073/074/076/078/086 датированы до коммитов
                       реализации (c8100e4, dfbeb10, 119da0f и др.) — их вердикты «0% / NOT STARTED /
                       BROKEN» устарели. Раздел LESSON_LEARNED фиксирует урок: 3 экспертизы аудита
                       работали по snapshot-данным до коммитов 087-090, что привело к ложным
                       выводам («102 pre-existing failures», «deploy-project.sh в 2 копиях»).
ACCEPTANCE_CRITERIA:   AC1: `.ai/debt/096-Residual-Debt.md` существует, покрывает все 5 разделов
                           из Brief (OPEN-087/088/089, COSMETIC, SHELL-RESIDUAL, 085-RC3-BLOCKERS,
                           LESSON_LEARNED), каждый пункт actionable (Observed/Impact/When)
                       AC2: Каждый из VR 073/02, 074/VerificationReport, 076/VerificationReport,
                           078/VerificationReport, 086/01 содержит пометку STATUS UPDATE
                           (SUPERSEDED) со ссылкой на реальную реализацию (файл/коммит)
                       AC3: Исторический контент VR не изменён — добавлена только пометка
                       AC4: 085-RC3 blockers C1/C2/C3 верифицированы против текущего кода
                           (grep + gate-тесты), вердикт по каждому зафиксирован в debt-реестре
                       AC5: Не затронуты файлы других кодеров (см. §Non-Goals) — проверено
                           `git status` на финальном аудите
                       AC6: Создан `03-VerificationReport.md` для 096 с вердиктом по факту
                           (STABLE/DRIFTED), таблицей AC→статус и доказательствами
IMPLEMENTS:            Brief 096 (`.ai/plans/096-debt-registry-and-vr-sync/01-Brief.md`)
IMPACTS:
                       - `.ai/debt/096-Residual-Debt.md` (NEW)
                       - `.ai/plans/073-provision-python/02-VerificationReport.md` (STATUS UPDATE)
                       - `.ai/plans/074-monitoring-hooks-python/VerificationReport.md` (STATUS UPDATE)
                       - `.ai/plans/076-reconcile-python/VerificationReport.md` (STATUS UPDATE)
                       - `.ai/plans/078-secrets-tokens-unification/VerificationReport.md` (STATUS UPDATE)
                       - `.ai/plans/086-secrets-parser-pipeline-unification/01-VerificationReport.md` (STATUS UPDATE)
                       - `.ai/plans/096-debt-registry-and-vr-sync/03-VerificationReport.md` (NEW)
REQUIRES:              Фактическое состояние кода (проверяется grep/glob/тестами), а не
                       snapshot-данные. git log для ссылок на коммиты реализации.
$END_ARTIFACT_CONTRACT

---

## 1. Problem Matrix

| # | Проблема (диагноз аудита 2026-07-30) | Статус на момент DevPlan (2026-07-31) | Решается как |
|---|--------------------------------------|----------------------------------------|--------------|
| P1 | Debt registry пуст (1-я экспертиза: «011-Debt.md — единственный») | Подтверждено: есть 011 + 091-residual, нет 085-RC3/COSMETIC/SHELL-RESIDUAL | Создать 096-Residual-Debt.md с 5 разделами |
| P2 | VR 073/074/076/078/086 устарели (говорят «0%», код существует) | Подтверждено: provisioner.py (c8100e4), monitoring_config_renderer.py (dfbeb10), reconciler_projects.py, shared/ (119da0f) существуют | STATUS UPDATE (SUPERSEDED) в каждый VR |
| P3 | 3 CRITICAL blockers из RC3 Gap Analysis (C1/C2/C3) — статус неизвестен | Требует верификации против кода (Wave 3) | Wave 3: grep + gate-тесты → вердикт |
| P4 | 3 экспертизы работали по устаревшим данным → ложные выводы («102 pre-existing», «deploy-project.sh дубликат») | Подтверждено: оба debunked (gate чист кроме косметики; deploy-project.sh удалён) | LESSON_LEARNED в debt-реестре |
| P5 | COSMETIC-мелочи (region-mismatch, noqa, dead code) — учёт | Частично: 2 пункта Brief уже починены (коммиты c6905b0, 8a6dbcb); обнаружены новые (scaffold/__init__.py, project-list.sh, provision-environment.sh source audit_logging.sh) | COSMETIC-раздел с фактическим состоянием |

---

## 2. Draft Artifact Graph

```xml
<code_graph>
  <entity id="debt_registry" type="DOCUMENT" keywords="debt registry residual open-cosmetics shell-residual blockers lesson-learned">
    <annotation>.ai/debt/096-Residual-Debt.md — 5 разделов, каждый пункт actionable</annotation>
    <crossLinks>
      <link target="vr_sync" relation="references"/>
      <link target="rc3_gap_analysis" relation="references"/>
    </crossLinks>
  </entity>

  <entity id="vr_sync" type="DOCUMENT_SET" keywords="superseded status-update verification-report 073 074 076 078 086">
    <annotation>STATUS UPDATE пометки в 5 VR — journal-модель, исторический контент не меняется</annotation>
    <crossLinks>
      <link target="debt_registry" relation="referenced_by"/>
    </crossLinks>
  </entity>

  <entity id="rc3_gap_analysis" type="DOCUMENT" keywords="rc3 blockers C1 C2 C3 shared gate watchdog">
    <annotation>085-rc3-verification/01-RC3-Gap-Analysis.md — источник C1/C2/C3</annotation>
    <crossLinks>
      <link target="debt_registry" relation="consumed_by"/>
    </crossLinks>
  </entity>

  <entity id="vr096" type="DOCUMENT" keywords="verification report 096 verdict ac-table">
    <annotation>.ai/plans/096-debt-registry-and-vr-sync/03-VerificationReport.md</annotation>
    <crossLinks>
      <link target="debt_registry" relation="verifies"/>
      <link target="vr_sync" relation="verifies"/>
    </crossLinks>
  </entity>
</code_graph>
```

**Zero Python modules** — задача документационная, §$TEST_SPEC = NONE.

---

## 3. File Manifest

| # | Файл | Действие | Тип | Описание |
|---|------|:--------:|-----|----------|
| F1 | `.ai/debt/096-Residual-Debt.md` | CREATE | MARKDOWN | Debt-реестр: OPEN-087/088/089, COSMETIC, SHELL-RESIDUAL, 085-RC3-BLOCKERS, LESSON_LEARNED |
| F2 | `.ai/plans/073-provision-python/02-VerificationReport.md` | MODIFY | MARKDOWN | +STATUS UPDATE (SUPERSEDED): provisioner.py существует (commit c8100e4); найдена новая проблема — stale source audit_logging.sh |
| F3 | `.ai/plans/074-monitoring-hooks-python/VerificationReport.md` | MODIFY | MARKDOWN | +STATUS UPDATE (SUPERSEDED): monitoring_config_renderer.py (938 LOC) существует; 02-VR STABLE |
| F4 | `.ai/plans/076-reconcile-python/VerificationReport.md` | MODIFY | MARKDOWN | +STATUS UPDATE (SUPERSEDED): reconciler_projects.py (552 LOC) существует; reconcile-projects.sh → 48 LOC фасад |
| F5 | `.ai/plans/078-secrets-tokens-unification/VerificationReport.md` | MODIFY | MARKDOWN | +STATUS UPDATE (SUPERSEDED): shared/ (15 модулей) + secrets/ реализованы; 02-VR STABLE |
| F6 | `.ai/plans/086-secrets-parser-pipeline-unification/01-VerificationReport.md` | MODIFY | MARKDOWN | +STATUS UPDATE (SUPERSEDED): реализовано (commit 119da0f); 02-VR DRIFTED (WARNING) — 3 missed tasks |
| F7 | `.ai/plans/096-debt-registry-and-vr-sync/03-VerificationReport.md` | CREATE | MARKDOWN | VR 096: вердикт, AC-таблица, доказательства |

---

## 4. Step-by-Step Data Flow

```
Brief 096 → §1-3 → DevPlan 096 (этот документ)
  │
  ├─► Wave 1: Верификация фактов (2026-07-31, рабочее дерево)
  │   ├─► glob/ls: core/internal/provisioner.py, monitoring_config_renderer.py,
  │   │           reconciler_projects.py, core/internal/shared/* (15), core/internal/secrets/*
  │   ├─► git log --oneline: коммиты реализации (c8100e4, dfbeb10, 119da0f, aa6bd61, ab589ed)
  │   ├─► grep: python3 -c / heredoc по core/**/*.sh (SHELL-RESIDUAL)
  │   ├─► grep: scaffold/__init__.py re-exports, project-list.sh PLATFORM_ROOT (COSMETIC)
  │   └─► pytest: tests/gates/test_gate_no_hardcoded_local_paths.py (C3),
  │              tests/gates/test_gate_healthcheck_unification.py (C2)
  │
  ├─► Wave 2: Создание .ai/debt/096-Residual-Debt.md
  │   ├─► OPEN-087/088/089: из VR + фактическое состояние (финальные VR 087-03/088-04/089-04 —
  │   │       создаются параллельно другим кодером — только реально открытые пункты)
  │   ├─► COSMETIC: фактически открытые (проверено; 2 пункта Brief уже починены в коммитах)
  │   ├─► SHELL-RESIDUAL: wc -l core/**/*.sh + grep inline python3 → топ-скрипты с оценкой
  │   ├─► 085-RC3-BLOCKERS: C1/C2/C3 вердикты (Wave 1 evidence)
  │   └─► LESSON_LEARNED: урок из Brief (3 экспертизы по snapshot-данным)
  │
  ├─► Wave 3: VR sync (5 STATUS UPDATE пометок)
  │   └─► Для каждого VR: вставить блок STATUS UPDATE после заголовка/вердикта,
  │       НЕ трогая исторический контент (Anti-Loop Note из Brief)
  │
  └─► Wave 4: Создание 03-VerificationReport.md для 096
      └─► Вердикт по факту, AC1-AC6 → статус, evidence (файлы + тесты)
```

---

## 5. Acceptance Criteria (детально)

### AC1 — Debt registry существует и покрывает 5 разделов
- `.ai/debt/096-Residual-Debt.md` содержит секции: `## OPEN-087/088/089`, `## COSMETIC`,
  `## SHELL-RESIDUAL`, `## 085-RC3-BLOCKERS`, `## LESSON_LEARNED`
- Каждый пункт: Observed / Impact / Recommended fix / When

### AC2 — VR sync (SUPERSEDED)
- 5 VR (F2-F6) содержат блок вида:
  `**STATUS UPDATE 2026-07-31:** SUPERSEDED — implementation committed (см. git log ...). См. актуальный статус в DevPlan.md / новых VR.`
- Пометка честная: ссылается на реальные файлы/коммиты (проверено в Wave 1)

### AC3 — Journal-модель соблюдена
- В изменённых VR изменён ТОЛЬКО добавленный блок STATUS UPDATE; исторический контент (вердикты,
  находки, таблицы) побайтово не менялся. Проверка: `git diff` показывает только ADD-строки

### AC4 — C1/C2/C3 верифицированы
- C1: `ls core/internal/shared/AGENTS.md` → вердикт (открыт/закрыт)
- C2: `grep healthcheck_unification core/entrypoint-manifest.yaml` + pytest gate → вердикт
- C3: `pytest tests/gates/test_gate_no_hardcoded_local_paths.py` + grep agent_watchdog.py → вердикт
- Вердикты зафиксированы в 085-RC3-BLOCKERS секции

### AC5 — Forbidden-файлы не тронуты
- Финальный `git status --short`: среди изменённых файлов нет
  `.ai/plans/087|088|089/*`, `.ai/plans/094-*/*`, `tests/e2e/*`, `tests/gates/test_gate_ci_coverage.py`,
  `core/internal/test_runner.py`, `core/internal/scaffold/*`, `core/internal/shared/deploy_paths.py`,
  `core/internal/bootstrap/content-hash.sh`, `tests/test_scaffold_env_platform.py`,
  `tests/test_project_scaffold.py`, `tests/unit/test_reconciler.py`, `AGENTS.md`

### AC6 — VR 096 создан
- `03-VerificationReport.md` с вердиктом (STABLE/DRIFTED по факту), таблицей AC→статус,
  evidence (файлы, grep-результаты, тесты)

---

## 6. Implementation Plan

### Wave 1: Факт-верификация (перед записью артефактов)
1. Проверить существование модулей 073/074/076/078/086 (glob/ls)
2. Получить коммиты реализации (git log --oneline)
3. Проверить COSMETIC-пункты Brief: `tests/test_contract_deploy_ssh.py` (region/imports),
   `core/internal/bootstrap/lifecycle/phases.py` (noqa)
4. Проверить новые COSMETIC-кандидаты: `core/internal/scaffold/__init__.py`,
   `core/internal/scaffold/project-list.sh:11`
5. SHELL-RESIDUAL scan: `wc -l core/**/*.sh` + `grep python3 -c` + heredoc
6. C1/C2/C3 верификация (AC4)
7. `make gate MODE=fast` — фиксация фактического состояния gate для VR

### Wave 2: Debt registry (F1)
8. Создать `.ai/debt/096-Residual-Debt.md` с 5 разделами по результатам Wave 1
9. OPEN-087/088/089: только пункты, подтверждённые открытыми на момент проверки
   (финальные VR 087-03/088-04/089-04 создаются параллельно — сослаться, не дублировать)
10. COSMETIC: пункты, всё ещё открытые; НЕ дублировать уже починенные (commit c6905b0, 8a6dbcb)
11. SHELL-RESIDUAL: топ скриптов с бизнес-логикой + файлы с inline python3, оценка миграции
12. 085-RC3-BLOCKERS: C1/C2/C3 вердикты с evidence
13. LESSON_LEARNED: «3 экспертизы по устаревшим snapshot-данным — верификация против git log обязательна»

### Wave 3: VR sync (F2-F6)
14. Для каждого из 5 VR — добавить блок STATUS UPDATE (SUPERSEDED) с конкретной ссылкой
15. Проверить `git diff` — только ADD-строки (AC3)

### Wave 4: VR 096 (F7)
16. Создать `03-VerificationReport.md` — вердикт по факту (ожидается DRIFTED из-за
    незакрытого gate + открытых debt-пунктов), AC1-AC6 таблица, evidence
17. Финальный аудит: `git status --short` (AC5)

---

## 7. Risks & Mitigations

| Риск | Вероятность | Mitigation |
|------|:-----------:|------------|
| R1: Параллельные кодеры (087-03/088-04/089-04 VR, 092/093/094/095/097/098) меняют файлы, которые я читаю/оцениваю | HIGH | Все вердикты в debt-реестре датированы (2026-07-31) и основаны на проверке «на момент Wave 1». Указать в VR, что финальные VR волн создаются параллельно |
| R2: Случайная правка forbidden-файлов при VR sync | MEDIUM | File Manifest §3 ограничивает MODIFY только 5 VR + 2 CREATE. Финальная проверка `git status` (AC5) |
| R3: Искушение переписать исторический VR «чтобы был правильный» | MEDIUM | Anti-Loop Note из Brief: journal-модель. Добавлять ТОЛЬКО STATUS UPDATE-блок (AC3) |
| R4: Gate красный на момент верификации — ложный вердикт «проект сломан» | HIGH | В VR 096 зафиксировать КОНКРЕТНЫЕ причины (2 pre-commit hook на файлах других кодеров: tests/e2e/*, test_gate_ci_coverage.py) и что это не регрессия 096 |
| R5: COSMETIC-пункт уже починен параллельным кодером → дублирование в реестре | MEDIUM | Wave 1 проверяет факт ДО записи; починенные пункты не вносятся (задание пользователя) |

---

## 8. Non-Goals

- ❌ НЕ исправлять код (включая COSMETIC-пункты) — задача документационная. Найденные
  проблемы фиксируются в debt-реестре, а не чинятся
- ❌ НЕ трогать: `.ai/plans/087|088|089/*` VR (параллельный кодер), `.ai/plans/094-*/*` VR,
  `tests/e2e/*`, `tests/gates/test_gate_ci_coverage.py`, `core/internal/test_runner.py`,
  `core/internal/scaffold/*`, `core/internal/shared/deploy_paths.py`,
  `core/internal/bootstrap/content-hash.sh`, `tests/test_scaffold_env_platform.py`,
  `tests/test_project_scaffold.py`, `tests/unit/test_reconciler.py`, `AGENTS.md`
- ❌ НЕ создавать новые DevPlans/задачи — только registry + VR sync
- ❌ НЕ запускать `make generate-manifests` / менять entrypoint-manifest.yaml (Invariant 11 —
  генерация вне скоупа этой документационной задачи)

---

## 9. Migration Path / Verification

- **$TEST_SPEC = NONE** — задача документационная, тестовый код не создаётся
- Верификация: AC1-AC6 (см. §5)
- `make gate MODE=fast` — фиксируется как evidence в VR 096 (НЕ требование зелёного gate
  для этой задачи: gate блокирован файлами других кодеров, см. R4)

$END_DEVPLAN
