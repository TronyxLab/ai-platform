# 08-DevPlan — Бриф G: гейты/манифест/глоссарий

<!-- $ARTIFACT_CONTRACT
PURPOSE:          Закрыть loophole-зоны регистрации гейтов/манифеста: invisible-гейты, дубли записей, висячие gate_id, глоссарий.
DESCRIPTION:      5 задач: G1 регистрация invisible-гейтов, G2 templates-check дубль + check-exception-patterns, G3 allowlist docs 6→8,
                  G4 module.yaml D5, G5 висячие gate_id в manifest.
RATIONALE:        Невидимые гейты (без @pytest.mark.gate) не выполняются в make gate — это «gate зелёный, система врёт». Висячие repair-ссылки
                  ломают контракт fix-gate. check-exception-patterns — реальный таргет, отсутствующий в манифесте/глоссарии.
ACCEPTANCE_CRITERIA:
  - AC-G1: test_gate_platform_env_schema зарегистрирован (pytestmark + manifest gates[]) и выполняется в make gate.
            test_restart_consistency — НЕ регистрируется (удаляется в F7).
  - AC-G2: templates-check одна запись (repair-вариант); check-exception-patterns в .PHONY + allowed_verbs + глоссарии (после make generate-manifests).
  - AC-G3: root AGENTS.md + tests/gates/AGENTS.md — allowlist 6→8 с комментарием (117: D19/D29/T52).
  - AC-G4: все 14 module.yaml имеют полный D5 (severity/restart/resources/env_requires); make validate-modules зелёный.
  - AC-G5: нет висячих gate_id в repair/non_repairable_gates; r1_no_pass_tests → test_r1_no_pass_tests; висячие make-таргет-гейты документированы.
  - AC-G6: make check-manifests зелёный (все generated актуальны).
IMPLEMENTS:       118 01-Brief задачи G1-G5.
IMPACTS:          tests/gates/{test_gate_platform_env_schema,test_restart_consistency}.py, makefiles/ci.mk, core/entrypoint-manifest.yaml,
                  AGENTS.md (глоссарий, allowlist), core/templates/module.mk + 14 module.yaml, tests/gates/AGENTS.md.
REQUIRES:         118 01-Brief; G2 требует make generate-manifests; F7 (удаление test_restart_consistency) ДО регистрации.
-->

---

## 1. Технический анализ и решения

### G1 (HIGH) — невидимые гейты

**Факты (верифицированы):** `tests/gates/test_gate_platform_env_schema.py` (339 LOC) — 0 `@pytest.mark.gate`/pytestmark; `tests/gates/test_restart_consistency.py` (257 LOC) — то же. Оба невидимы для `--collect-only -m gate` → не выполняются в `make gate`.

**Решение:**
1. `test_gate_platform_env_schema.py` — добавить `pytestmark = pytest.mark.gate` + запись в `entrypoint-manifest.yaml#gates[]` + регенерация.
2. `test_restart_consistency.py` — **НЕ регистрировать**: он удаляется в F7 (консолидация в test_gate_make_contract). Зафиксировать зависимость G1 ← F7.

**Тест:** `pytest tests/gates -m gate --collect-only` содержит оба имени (платформенный — новый, restart-consistency — до удаления).

**Риск:** LOW (маркер + регенерация).

### G2 (MED) — templates-check дубль + check-exception-patterns loophole

**Факты (верифицированы):**
- `entrypoint-manifest.yaml:93` (validate) + `:479` (repair) — две записи templates-check; генератор (G3 merge) сохраняет структурные секции → регенерация не уберёт дубль.
- `ci.mk:331` — `check-exception-patterns` вызывается gate-конвейером (Step 2c/8), но НЕ в `.PHONY` (ci.mk:15) → невидим для генератора манифеста (gmake -np берёт только .PHONY) → отсутствует в allowed_verbs и глоссарии. Гейт test_all_phony_targets_discovered собирает только .PHONY → loophole.

**Решение:**
1. ci.mk:15 — добавить `check-exception-patterns` в `.PHONY`.
2. entrypoint-manifest.yaml — удалить дублирующую запись templates-check из validate (оставить repair-вариант с repairs_gates) ИЛИ наоборот; согласовать с генератором.
3. `make generate-manifests` → allowed_verbs + глоссарий обновляются.

**Тест:** `make check-manifests` зелёный; gmake -np содержит check-exception-patterns; глоссарий AGENTS.md содержит его.

**Риск:** LOW.

### G3 (LOW) — allowlist docs 6→8

**Факты:** cross-layer allowlist: root AGENTS.md + tests/gates/AGENTS.md фиксируют «6 записей», фактически 8 (после D19/D29/T52 волны 117).

**Решение:** обновить документацию: 6→8 + комментарий «расширен до 8 (117: D19/D29/T52)». Проверить актуальное число записей в test_cross_layer_imports.py перед правкой.

**Тест:** документационный grep (0 упоминаний «6 записей»).

**Риск:** LOW.

### G4 (LOW) — module.yaml D5-контракты

**Факты:** `nginx/module.yaml` — неполный D5 (нет severity/restart/resources/env_requires); вероятно другие модули.

**Решение:** `make validate-modules` → добить недостающие D5-поля во всех 14 module.yaml. Контракт-валидация, без функционала.

**Тест:** validate-modules зелёный; валидатор не ругается.

**Риск:** LOW.

### G5 (MED) — висячие gate_id в manifest

**Факты (верифицированы):**
- `:377` `repair: fix-ruff → gate_id: ruff-format` — id нет в gates[] (авто-дискавери pytest).
- `:392` `repair: fix-gate → gate_id: check-manifests` — check-manifests это make-таргет-гейт, не pytest-гейт; id нет в gates[].
- `:727` `non_repairable_gates: template-syntax-contract` — id нет в gates[] (файл test_gate_template_syntax.py есть, но id другой).
- `:732` `non_repairable_gates: r1_no_pass_tests` vs gates[] id `test_r1_no_pass_tests` (:1598-1601) — несовпадение имени.

**Решение:** выровнять имена на реальные id из gates[]. Для make-таргет-гейтов (check-manifests) — задокументировать как `make-target-gate` (новый класс в manifest-схеме ИЛИ комментарий) — нельзя ссылаться на несуществующий pytest-id. template-syntax-contract — найти фактический id файла и использовать его.

**Тест:** гейт manifest-integrity зелёный; fix-gate repair-карта резолвится (проверить test_gate_manifest_integrity).

**Риск:** LOW (документация/регистрация).

---

## 2. Порядок выполнения

```
G1 (invisible gates)   ← вместе с F7 (restart_consistency удаление)
   │
G2 (manifest дубль)    ← требует регенерацию (make generate-manifests)
   │
G5 (gate_id)           ← ручная правка manifest + проверка integrity-гейта
   │
G3 (allowlist docs)    ← независимо
   │
G4 (module.yaml D5)    ← независимо
```

## 3. Оценки

| Метрика | Значение |
|---------|----------|
| Задач | 5 |
| LOC | −20 (дедуп templates-check) + ~+15 (register check-exception-patterns) + ~+40 (module.yaml D5) |
| Гейтов | +1 регистрация (G1a), −1 удаление (F7, не G1) |
| Зависимости | G1 ← F7, G2 → регенерация |

## $END

Открытые вопросы:
1. **G2** — какую запись templates-check считать каноном (validate или repair) — согласовать с генератором manifest G3.
2. **G5** — вводить ли класс make-target-gate в manifest-схеме или комментарий; решение на имплементации.
