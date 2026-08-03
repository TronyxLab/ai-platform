# 131-debt-cleanup — 02-DevPlan.md

$START_DEVPLAN

$ARTIFACT_CONTRACT
PURPOSE:               Исполнить 01-Brief (131): удалить все артефакты технического долга из репозитория (реестры, TRAP[DEBT]-комментарии, gate-тест реестра) после закрытия долгов 127-130; ревизовать документацию; оставить процессный механизм.
DESCRIPTION:           4 волны (см. Brief): W1 — удаление .ai/debt/; W2 — TRAP[DEBT]-комментарии; W3 — gate-тест реестра (trinity); W4 — документация и ссылки.
RATIONALE:             См. Brief 131 RATIONALE. Ключевой инвариант: механизм (формат TRAP[DEBT], Debt-артефакты) остаётся, исторические данные удаляются.
ACCEPTANCE_CRITERIA:   См. Brief 131 (6 пунктов).
IMPLEMENTS:            01-Brief.md (131); решение пользователя 2026-08-03.
IMPACTS:               См. Brief IMPACTS.
REQUIRES:              Реализация 127-130 завершена; make check зелёный.
$END_ARTIFACT_CONTRACT

## 0. Draft Code Graph (XML)

```xml
<graph>
  <entity name="ai_debt" TYPE="DIRECTORY"
    keywords="registry,debt,remove"
    annotation="W1: rm -rf .ai/debt/ (5 файлов). История — в git (коммиты 1dba31d/e44f00b и ранее)."
    CrossLinks=""/>
  <entity name="tests_gates_test_gate_debt_registry_py" TYPE="TEST"
    keywords="gate,registry,remove,trinity"
    annotation="W3: удалить файл + manifest-запись (entrypoint-manifest.yaml gates) + __pycache__. Правило tests/gates/AGENTS.md: удаление gate = файл + манифест + кэш."
    CrossLinks="core/entrypoint-manifest.yaml"/>
  <entity name="kilo_agents" TYPE="PACKAGE"
    keywords="trap-debt,mechanism,registry-path"
    annotation="W4: ревизия упоминаний .ai/debt/001-... в .kilo/agents/*.md (architect.md:100 glob .ai/plans/*/*-Debt.md — актуально; упоминания реестра — убрать). Механизм TRAP[DEBT] сохраняется."
    CrossLinks=".kilo/agents/architect.md; .kilo/agents/code.md; .kilo/agents/qa.md; .kilo/agents/sysadmin.md"/>
</graph>
```

## 1. Data Flow (шаг за шагом)

```
W1 ── git rm -rf .ai/debt/ (5 файлов force-added — удаляются штатным git rm;
     .gitignore .ai/* продолжает игнорировать) ─► rg "\.ai/debt" — инвентарь ссылок
W2 ── rg "TRAP\[DEBT\]" — полный инвентарь (~30 мест, исключая .kilo/) ─►
     для каждого: долг закрыт (127-130) → удалить комментарий;
     долг реально остался → {NN}-Debt.md в активном плане (по протоколу artifacts.md)
W3 ── rm tests/gates/test_gate_debt_registry.py ─► manifest-запись (grep id в
     entrypoint-manifest.yaml) ─► rm __pycache__ остатки ─► тесты trinity
     (test_gate_manifest_integrity) зелёные
W4 ── root AGENTS.md: таблица Shell-исключений актуализирована (после 127),
     TRAP[DECISION] B11 «debt-freshness (реестр долга...)» → ревизия текста
     (механизм Debt-артефактов остаётся в artifact-registry);
     .kilo/agents/*.md: пути реестра → актуальные ({NN}-Debt.md);
     core/internal/shared/AGENTS.md: ссылка .ai/debt/096-Residual-Debt.md →
     актуализировать (096-план удалён в e44f00b, долг C1 закрыт);
     core/internal/provision-environment.sh:28 «Reverted-debt: C-5» — снять/обновить
```

## 2. File Manifest

| Файл | Действие | Волна |
|------|----------|-------|
| `.ai/debt/*` (5 файлов) | git rm | W1 |
| ~30 файлов с TRAP[DEBT] (core/, tests/, makefiles/, .github/) | удалить комментарии | W2 |
| `tests/gates/test_gate_debt_registry.py` | rm | W3 |
| `core/entrypoint-manifest.yaml` | gate-запись debt-registry удалить | W3 |
| `AGENTS.md` (root) | Shell-исключения + debt-freshness ревизия | W4 |
| `.kilo/agents/architect.md` (строки 35/100), `code.md` (58/239-244), `qa.md` (374-399), `sysadmin.md` (163-168) | ревизия путей реестра | W4 |
| `core/internal/shared/AGENTS.md` (строка 100) | ссылка .ai/debt/096 → актуализация | W4 |
| `core/internal/provision-environment.sh` (строка 28) | Reverted-debt C-5 → снять | W4 |

## 3. Волны

### W1 — Удаление .ai/debt/
1. Проверка предусловия: 127-130 реализованы (все долги закрыты — git log/реестр
   статусов). Если нет — стоп, cleanup только после закрытия.
2. `git rm -r .ai/debt/` (5 файлов: 001-Strangler-Fig-Closeout.md, 121-rc-deferred.md,
   letsencrypt-path-hardcode.md, test-env-leak-and-flakes.md, watchdog-undelivered.md).
   История сохраняется в git (коммиты 2026-07-31..08-03).
3. `rg "\.ai/debt"` — инвентарь ссылок для W4 (не удалять вслепую).

**Acceptance W1:** .ai/debt/ отсутствует; git rm выполнен; инвентарь ссылок собран.

### W2 — TRAP[DEBT]-комментарии
1. `rg "TRAP\[DEBT\]"` с исключением .kilo/ → полный список (~30 мест:
   check-file-lines.sh, test_gate_timeout_literals.py, test_gate_marker_location.py,
   test_cross_layer_imports.py, doc_header_validator.py, project_adopter.py,
   s3_client.py, postgres/healthcheck.sh + docker-compose.base.yml, nginx.conf,
   dev-config/*.conf, deploy_engine.py, overlay_deliverer.py, jsonschema_validate.py,
   docker_orchestrator.py, check-no-new-inline-python3.sh, mirror.yml, test_smoke_litellm.py,
   test_spool_dir.py, test_volume_spool_consistency.py, test_lib_node_resolver.py,
   skip_gate.py, networks.py, test_smoke_monitoring.py, test_shared_timeouts.py,
   test_check_suite.py, test_gate_dead_code.py, test_gate_compose_no_base_image.py,
   test_add_vhost.py, makefiles/manifest.mk, shared/AGENTS.md и др.).
2. Для каждого: долг закрыт (127-130) → удалить комментарий целиком (история — git).
   Долг не закрыт (edge) → {NN}-Debt.md в активном плане + удалить из кода.
3. Условные keep-комментарии (D13 «Rev: при возврате к inline-сервисам») —
   удалить; решение живёт в коде/AGENTS.md.
4. Проверка: `rg "TRAP\[DEBT\]"` в коде = 0; `rg "TRAP\[DEBT\]"` в .kilo — только
   формат-описания (механизм).

**Acceptance W2:** 0 TRAP[DEBT] в коде/тестах/манифестах/доках (кроме .kilo-механизма).

### W3 — Gate-тест реестра (trinity)
1. Удалить `tests/gates/test_gate_debt_registry.py`.
2. Удалить manifest-записи: grep "test_gate_debt_registry" в core/entrypoint-manifest.yaml
   (секция gates, auto-discovered id) — удалить все связанные.
3. Очистить `tests/gates/__pycache__/` (остатки .pyc).
4. Проверка: make check + test_gate_manifest_integrity (trinity) зелёные;
   `make gate MODE=fast` — зелёный (gate-сет не ссылается на удалённый файл).

**Acceptance W3:** файл+манифест+кэш удалены; trinity-гейт зелёный.

### W4 — Документация и ссылки
1. root AGENTS.md: таблица Shell-исключений — актуальный состав после 127
   (issue-cert.sh, deploy.sh, libs; мигрированные удалены); TRAP[DECISION]
   «Enforcement-гейты... debt-freshness (реестр долга...)» — ревизия: реестр удалён,
   механизм Debt-артефактов остаётся (artifact-registry), текст решения обновить.
2. .kilo/agents/architect.md (строки 35/100): упоминания реестра .ai/debt →
   {NN}-Debt.md протокол (артефакт-регистр — единственный источник); code.md/qa.md/
   sysadmin.md — формат TRAP[DEBT] остаётся, пути реестра не упоминаются (проверить).
3. core/internal/shared/AGENTS.md:100 — ссылка .ai/debt/096-Residual-Debt.md:
   096-план удалён (e44f00b), долг C1 закрыт → ссылку снять/заменить на актуальный
   источник (комментарий в файле).
4. core/internal/provision-environment.sh:28 — «Reverted-debt: C-5 in
   .ai/debt/096-Residual-Debt.md» → снять упоминание (реестр удалён).
5. `rg "\.ai/debt"` финальный → 0 (или только механизм-ссылки без файлов).

**Acceptance W4:** 0 битых ссылок на .ai/debt; AGENTS.md актуален; gate зелёный.

## 4. Критерии приёмки волн — сводка

| Волна | Критерий |
|-------|----------|
| W1 | .ai/debt/ удалён, инвентарь ссылок собран |
| W2 | 0 TRAP[DEBT] в коде/доках (кроме .kilo-механизма) |
| W3 | gate-тест удалён, trinity зелёный |
| W4 | 0 ссылок на .ai/debt, документация актуальна, check+gate зелёные |

## 5. Риски и митигации

| Риск | Митигация |
|-------|-----------|
| Удаление TRAP[DEBT] «вслепую» затронет живые наблюдения | Поэлементная проверка: каждый комментарий сверяется со статусом долга (127-130); живые → Debt-артефакт |
| Gate-сет сломается после удаления gate-теста (зависимости) | rg "debt_registry|test_gate_debt_registry" по всему репо перед удалением; прогон make check |
| .kilo-механизм (TRAP[DEBT] формат) удалится случайно | Исключение .kilo/ из W2-скана; W4 — только ревизия путей, не формата |
| 126-chaos план ссылается на .ai/debt (04-Debt.md создастся в 126) | .ai/plans/126 НЕ трогаем; его Debt-артефакты живут в плане (не в .ai/debt/) |

$END_DEVPLAN
