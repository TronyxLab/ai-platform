$START_VERIFICATION_REPORT

$ARTIFACT_CONTRACT
PURPOSE:               Семантический QA-аудит DevPlan 092 (Scaffold Python Completion, 1972 LOC). Проверка консистентности плана с реальным состоянием исходных файлов, выявление drift, contract violations, рисков реализации.
DESCRIPTION:           Полный аудит по 10 измерениям: $ARTIFACT_CONTRACT compliance, AC completeness, cross-references с реальными исходниками, wave feasibility, тестовая стратегия, risk register, semantic markup, out of scope, sequencing, file manifest accuracy. Все shell-файлы (project-list.sh, context-init.sh, remove-project.sh, add-project.sh), Python-зависимости (node_yaml.py, project_adopter.py, context_registry.py) прочитаны и сверены с утверждениями DevPlan.
RATIONALE:             DevPlan мигрирует последний крупный блок бизнес-логики (4 shell-монолита, 1972 LOC). Цена ошибки в плане: CLI contract mismatch → поведенческая регрессия на production-ноде. Ручная верификация плана перед делегированием Coder предотвращает дорогие rework-циклы.
ACCEPTANCE_CRITERIA:
  - AC-VR1: Все утверждения DevPlan о LOC/функциях/inline python3 сверены с реальными файлами
  - AC-VR2: Все cross-references (node_yaml.py, project_adopter.py, context_registry.py) верифицированы — line numbers точны, методы существуют
  - AC-VR3: CLI-контракты Python-модулей совместимы с существующими shell-скриптами (фасады не требуют модификации entrypoint)
  - AC-VR4: Выявлены все расхождения между Brief и DevPlan
  - AC-VR5: Risk register проверен на полноту — все значимые риски идентифицированы
IMPLEMENTS:            AGENTS.md §Языковая политика (Strangler-Fig Tier-1/2), TRAP 2026-07-22 Decision Gate, Brief 092 §Required Actions
IMPACTS:               CREATE: 02-VerificationReport.md
REQUIRES:              DevPlan 092 (аудируемый артефакт), Brief 092, исходные shell/Python файлы из core/internal/scaffold/
$END_ARTIFACT_CONTRACT

---

# VerificationReport 092: Scaffold Python Completion DevPlan Audit

🔒 **Verified against SHA:** `8a6dbcbf08297c0f4e044be254e244b20cadfa69`
**Working tree:** dirty (11 modified files — не затрагивают scaffold/, irrelevant for this audit)
**Audit date:** 2026-07-30
**Audit type:** Pre-implementation DevPlan audit (LARGE — >20 files, architectural changes)

---

## Semantic Verdict: DRIFTED (severity: HIGH)

**Rationale:** План содержит два CLI contract mismatch (CRITICAL — блокируют реализацию Wave 3 и Wave 2 как поведенчески-совместимые), одно фактическое расхождение с Brief (MEDIUM), и несколько минорных замечаний. План implementable при условии исправления CRITICAL-проблем до начала соответствующих волн.

| Dimension | Status | Issues |
|-----------|--------|--------|
| $ARTIFACT_CONTRACT | ✅ PASS | Все 7 полей присутствуют, содержательны |
| AC Completeness | ⚠️ PASS (1 WARNING) | AC5 верификация слишком слаба |
| Cross-references (source files) | ⚠️ PASS (1 MEDIUM) | Brief/DevPlan расхождение по функциям add-project.sh |
| Wave 1 (lister) | ✅ PASS | Низкий риск, все утверждения подтверждены |
| Wave 2 (context) | 🔴 FAIL (1 CRITICAL) | CLI contract mismatch: positional vs named args |
| Wave 3 (remover) | 🔴 FAIL (1 CRITICAL) | CLI contract mismatch: --force исчез, --keep-data добавлен |
| Wave 4 (scaffolder) | ⚠️ PASS (1 WARNING) | vhost_renderer.remove_vhost дублирует функциональность |
| Testing Strategy | ⚠️ PASS (2 WARNING) | Anti-Loop не детализирован; IMP:9 assertion шаблонный |
| Risk Register | ⚠️ PASS (1 MEDIUM) | Пропущен риск модификации entrypoints/scaffold.sh |
| Semantic Markup | ⚠️ PASS (2 LOW) | $DOCUMENT_PLAN отсутствует; файл без NN-префикса |
| Out of Scope | ✅ PASS | Полный, корректный |
| Sequencing | ✅ PASS | Waves 1-3 независимы, Wave 4a→4b правилен |
| File Manifest | ✅ PASS | LOC оценки релевантны, пути корректны |

**Project health score:** 68/100 (2 CRITICAL, 2 MEDIUM, 5 LOW/WARNING)

---

## §1. $ARTIFACT_CONTRACT Compliance

### Compliance Matrix

| Field | Status | Evidence |
|-------|--------|----------|
| PURPOSE | ✅ PASS | L4 — чётко описывает бизнес-цель |
| DESCRIPTION | ✅ PASS | L5 — детальный план действий |
| RATIONALE | ✅ PASS | L6 — обоснование с ссылкой на AGENTS.md, TRAP, 3 аудита |
| ACCEPTANCE_CRITERIA | ✅ PASS | L7-16 — 9 измеримых критериев (AC1-AC9) |
| IMPLEMENTS | ✅ PASS | L17 — ссылается на Brief 092, AGENTS.md, TRAP Decision Gate |
| IMPACTS | ✅ PASS | L18 — перечислены все CREATE/MODIFY/DELETE |
| REQUIRES | ✅ PASS | L19 — DP-088 STABLE, DP-091 Wave C, context_registry.py — реальные зависимости |

### Формат
- ✅ `$START_DEVPLAN` / `$END_DEVPLAN` на месте (L1, L533)
- ⚠️ `$DOCUMENT_PLAN` отсутствует — doc-protocols §1 рекомендует создать skeleton ДО тела документа
- ⚠️ Файл назван `DevPlan.md` вместо `02-DevPlan.md` — нарушение journal naming из §ARTIFACT_REGISTRY

---

## §2. AC Completeness Audit

### AC Analysis

| AC | Описание | Проверяем? | Статус |
|----|----------|-----------|--------|
| AC1 | Все 4 операции работают идентично | unit-тесты + manual smoke | ✅ Конкретен, измерим |
| AC2 | 4 shell-фасада <50 LOC | `wc -l` | ✅ Конкретен, измерим |
| AC3 | 0 inline python3 | `grep -rn "python3 -c\|python3 <<"` | ✅ Конкретен, измерим |
| AC4 | Unit-тесты с LDD IMP:9-10 | pytest + Anti-Loop | ✅ Конкретен, измерим |
| AC5 | `make project-status NAME=<p>` идентично | grep на "status" в project_lister.py | ⚠️ Слишком слабая верификация |
| AC6 | Shared helper extraction | grep import scaffold_helpers | ✅ Конкретен, измерим |
| AC7 | `make gate MODE=fast` зелёный | exit 0 | ✅ Конкретен, измерим |
| AC8 | Все тесты проходят | pytest exit 0 | ✅ Конкретен, измерим |
| AC9 | manifests drift-free | `make check-manifests` | ✅ Конкретен, измерим |

### WARNING: AC5 Verification Insufficient

**Локация:** DevPlan L443
**Проблема:** AC5 верифицируется как `grep -n "status" core/internal/scaffold/project_lister.py`. Это проверяет только наличие подстроки `status` в исходном коде, но НЕ проверяет функциональную эквивалентность `make project-status`.
**Предлагаемое исправление:** Заменить на: `make project-status NAME=<test-proj> | diff - <(expected_output)` или хотя бы `python3 -m core.internal.scaffold.project_lister --status --name <test-proj>` с проверкой exit code.

---

## §3. Consistency & Cross-References

### Verified Claims

| Утверждение | Файл | Ожидание | Факт | Статус |
|-------------|------|----------|------|--------|
| project-list.sh 403 LOC | project-list.sh | 403 | 403 | ✅ |
| project-list.sh 7 inline python3 | project-list.sh | 7 | 7 (L166, L183, L204, L215-218×4) | ✅ |
| project-list.sh 8 функций | project-list.sh | 8 | 8 | ✅ |
| context-init.sh 364 LOC | context-init.sh | 364 | 364 | ✅ |
| context-init.sh 0 inline python3 | context-init.sh | 0 | 0 | ✅ |
| context-init.sh 7 функций | context-init.sh | 7 | 9 (с _usage, _report_summary) | ⚠️ LOW |
| remove-project.sh 423 LOC | remove-project.sh | 423 | 423 | ✅ |
| remove-project.sh 2 inline python3 | remove-project.sh | 2 | 2 (L147, L151) | ✅ |
| remove-project.sh 7 функций | remove-project.sh | 7 | 7 | ✅ |
| add-project.sh 782 LOC | add-project.sh | 782 | 782 | ✅ |
| add-project.sh 0 inline python3 | add-project.sh | 0 | 0 | ✅ |
| add-project.sh 16 функций | add-project.sh | 16 | 16 | ✅ |
| NodeYaml.get_projects() L551 | node_yaml.py | L551 | L551 | ✅ |
| NodeYaml.get_project() L1104 | node_yaml.py | L1104 | L1104 | ✅ |
| NodeYaml.add_project() L1141 | node_yaml.py | L1141 | L1141 | ✅ |
| NodeYaml.remove_project() L1194 | node_yaml.py | L1194 | L1194 | ✅ |
| NodeYaml.update_project() L1233 | node_yaml.py | L1233 | L1233 | ✅ |
| project_adopter.generate_minimal... L182 | project_adopter.py | L182 | L182 | ✅ |
| project_adopter.gen_project_makefile L445 | project_adopter.py | L445 | L445 | ✅ |
| project_adopter.gen_project_agents L498 | project_adopter.py | L498 | L498 | ✅ |
| project_adopter.register_in_node_yaml L715 | project_adopter.py | L715 | L715 | ✅ |
| context_registry.py ~105 LOC | context_registry.py | 105 | 105 | ✅ |
| remove-project.sh НЕ использует `-v` | remove-project.sh | 0 `down.*-v` | 0 (6 подтверждений NO `-v`) | ✅ |
| __init__.py существует | scaffold/ | exists | exists (10 LOC) | ✅ |
| entrypoints/scaffold.sh существует | entrypoints/ | exists | exists (128 LOC) | ✅ |
| DP-091 существует | .ai/plans/ | exists | 01-Brief + 02-DevPlan | ✅ |

### MEDIUM: Brief vs DevPlan Discrepancy — add-project.sh Function Count

**Локация:** Brief 01 L19 vs DevPlan L44
**Проблема:** Brief утверждает "11 функций" для add-project.sh, DevPlan корректно указывает "16 функций". Фактический подсчёт: 16 функций (parse_args, auto_domain, show_plan, confirm, copy_template, generate_ai_platform_yaml, render_project_template, gen_env_platform, gen_project_makefile, gen_project_agents, git_init_project, generate_checklist, create_github_repo, run_add_vhost, register_in_node_yaml, main).
**Риск:** Если Coder будет ориентироваться на Brief — занизит оценку сложности Wave 4.
**Предлагаемое исправление:** Обновить Brief L19: "11 функций" → "16 функций".

---

## §4. Wave Feasibility

### Wave 1: project_lister.py — ✅ PASS

- Все 7 inline python3 блоков покрываются миграцией на Python (подтверждено grep)
- NodeYaml.get_projects() (L551) обеспечивает 90% логики — верно
- 6 unit-тестов покрывают основные сценарии (table, json, filter, empty, multi-node)
- Фасад <30 LOC реалистичен
- **Риск LOW** — оценка верна

### Wave 2: context_initializer.py — 🔴 FAIL (CRITICAL)

**Локация:** DevPlan L211-217 (argparse CLI) vs context-init.sh L74-112 (positional args)

**Проблема:** DevPlan описывает `context_initializer.py` с argparse-интерфейсом:
```python
main() — argparse: --name, --node, --org, --skip-gh-repo
```

Но текущий `context-init.sh` использует позиционные аргументы:
```bash
CONTEXT_NAME="$1"     # позиционный
--description <desc>  # именованный
--org <org>           # именованный
--skip-gh-repo        # флаг
--node-yaml <path>    # именованный
```

Фасад `exec python3 -m core.internal.scaffold.context_initializer "$@"` передаст первый позиционный аргумент напрямую. Argparse НЕ поймёт позиционный аргумент без `--name`.

**Входная точка:** `entrypoints/scaffold.sh:72` делает:
```bash
exec "${PATHS_INTERNAL_DIR}/scaffold/context-init.sh" "$@"
```

После миграции: shell-фасад context-init.sh получает `"$@"` как `new-context-name --org myorg --skip-gh-repo`. Если фасад делает `exec python3 -m ... "$@"`, то Python-модуль должен принимать позиционный первый аргумент как `--name`.

**Предлагаемое исправление (один из вариантов):**
- **Option A:** Python-модуль принимает позиционный первый аргумент (`parser.add_argument("name", nargs="?")`) — сохраняет обратную совместимость
- **Option B:** Shell-фасад делает трансляцию: `python3 -m ... --name "$1"` (shift positional → named) — требует логики в фасаде, увеличивает LOC
- **Option C:** Изменить entrypoints/scaffold.sh для передачи `--name` (но тогда ломается совместимость с прямыми вызовами context-init.sh)

**Рекомендация:** Option A — минимальные изменения, сохраняет обратную совместимость.

Также: DevPlan не упоминает флаг `--node-yaml` (присутствует в оригинале) и `--description` (присутствует в оригинале). Убедиться, что эти флаги учтены.

### Wave 3: project_remover.py — 🔴 FAIL (CRITICAL)

**Локация:** DevPlan L245 (project_remover flags) vs remove-project.sh L63-66 (actual flags)

**Проблема:** DevPlan описывает аргументы `project_remover.py`:
```
--name, --node, --keep-data, --dry-run
```

Но текущий `remove-project.sh` использует:
```
--name <name>, --node <node>, --force (skip confirmation)
```

**Расхождения:**
1. `--force` ИСЧЕЗ — текущий скрипт использует его для пропуска confirmation prompt
2. `--keep-data` ДОБАВЛЕН — новый флаг, которого нет в оригинале (логика: данные И ТАК не удаляются, флаг — no-op)
3. `--dry-run` ДОБАВЛЕН — новый флаг, полезен, но не из оригинального поведения

**Нарушение Anti-Loop Note:** "Behaviour-preserving — каждый Wave мигрирует логику 1:1. Никаких улучшений". Добавление `--keep-data` и `--dry-run` — это улучшение, которое должно быть в отдельном debt-плане. Исчезновение `--force` — прямая behavioural regression.

**Входная точка:** `entrypoints/scaffold.sh:86`:
```bash
exec "${PATHS_INTERNAL_DIR}/scaffold/remove-project.sh" "$@"
```

Если пользователь вызывает `make remove-project NAME=foo --force` → scaffold.sh передаёт `--name foo --force` в remove-project.sh. После миграции shell-фасад получит `--name foo --force` и передаст Python-модулю. Если Python-модуль не принимает `--force` — ошибка argparse.

**Предлагаемое исправление:**
1. Вернуть `--force` в argparse проектного модуля (пропуск confirmation)
2. `--dry-run` и `--keep-data` вынести в отдельный debt-план ИЛИ явно пометить как no-op в DevPlan (поведение по умолчанию = keep data, dry-run = дополнительный функционал, не меняющий поведение по умолчанию)
3. Документировать, что `--keep-data` — это явный no-op (документирует существующее поведение, не меняет его)

### Wave 4a: scaffold_helpers.py — ✅ PASS

- 4 shared-функции корректно идентифицированы (подтверждены grep в project_adopter.py)
- Refactor-first изолирует риск (отдельная верификация adopter-тестов)
- Тестовый файл test_scaffold_helpers.py — разумное покрытие

### Wave 4b: project_scaffolder.py — ⚠️ PASS (WARNING)

**Локация:** DevPlan L141 vs vhost_renderer.py L628

**Проблема:** `project_scaffolder.py` содержит функцию `run_add_vhost(...)` → "delegates to add-vhost.sh". Однако `vhost_renderer.py:628` уже содержит функцию `remove_vhost(project_name, overlays_dir, platform_root)`. В scope удаления vhost есть две потенциальные реализации — shell `rm -f` в project_remover.py и Python `vhost_renderer.remove_vhost()`.

**Риск:** После миграции Wave 3, `project_remover.py` будет использовать `rm vhost file` (как сейчас в shell), а `vhost_renderer.py` уже имеет `remove_vhost()`. Это создаёт drift-риск: две реализации одной операции. DevPlan правильно идентифицирует `vhost_renderer.py` как KEEP (вне scope), но не документирует, что project_remover.py НЕ должен дублировать её логику.

**Предлагаемое исправление:** Добавить в Wave 3 секцию: "project_remover.remove_vhost() делегирует в vhost_renderer.remove_vhost() напрямую (Python import), а не делает shell `rm -f`". Это потребует импорта из vhost_renderer.py, что корректно (оба в core/internal/scaffold/).

---

## §5. Testing Strategy

### LDD Compliance

| Модуль | IMP:9 assertion | Anti-Loop counter | Статус |
|--------|-----------------|-------------------|--------|
| test_project_lister.py | ✅ Прописан (L197) | ✅ Упомянут | PASS |
| test_context_initializer.py | ✅ Прописан (L225) | ⚠️ Не детализирован | PASS (minor) |
| test_project_remover.py | ✅ Прописан (L255) | ⚠️ Не детализирован | PASS (minor) |
| test_scaffold_helpers.py | ❌ Не указан явно | ❌ Не упомянут | ⚠️ WARNING |
| test_project_scaffolder.py | ✅ Прописан (L305) | ⚠️ Не детализирован | PASS (minor) |

### Test Honesty Rules Compliance

| Правило | Статус | Комментарий |
|---------|--------|-------------|
| R1 (no pass-tests) | ✅ | Все тесты имеют meaningful assertions |
| R2 (no unfalsifiable) | ✅ | Запрещены `assert isinstance` |
| R3 (no stale skip) | ✅ | Без `@pytest.mark.skip` |
| R4 (no skip on service) | ✅ | Mock через DI, не skip |
| R5 (negative tests) | ✅ | test_unregister_removes_all_duplicates для TRAP node_yaml.py:1186 |

### DI Strategy

- `ssh_read`/`ssh_exec` → передаваемые callable ✅ (адекватно)
- `subprocess.run` → injectable runner ✅ (адекватно)
- Все path через tmp_path ✅ (Zero Hardcode Rule)

### WARNING: Anti-Loop Protocol не детализирован

**Локация:** DevPlan L398
**Проблема:** Anti-Loop секция только ссылается на `tests/conftest.py` и `.test_counter.json`, но не описывает:
- Конкретные CHECKLIST-items для каждого тестового модуля
- Порог эскалации для каждого Wave
- Разные failure modes для unit (локальные) vs integration (SSH) тестов

**Предлагаемое исправление:** Добавить по 2-3 модуль-специфичных CHECKLIST-items (например: "caplog.set_level(logging.INFO) для test_project_lister", "Mock ssh_exec возвращает tuple (rc, stdout) не str для test_project_remover").

### WARNING: test_scaffold_helpers.py LDD не прописан

**Локация:** DevPlan L276
**Проблема:** Для test_scaffold_helpers.py не указан IMP:9 assertion и Anti-Loop counter. Все остальные тестовые модули имеют LDD trajectory блок.
**Предлагаемое исправление:** Добавить: "LDD: caplog IMP:9 assertion, Anti-Loop counter".

---

## §6. Risk Register Analysis

### Идентифицированные риски (DevPlan R1-R8)

| ID | Risk | Severity | Статус |
|----|------|----------|--------|
| R1 | Wave 4 HIGH-сложность | HIGH | ✅ Корректно |
| R2 | SSH side-effects (compose down) | MED | ✅ Корректно |
| R3 | node_yaml.remove_project дубликаты | LOW | ✅ Корректно |
| R4 | gh CLI / git external deps | LOW | ✅ Корректно |
| R5 | DRIFT: project_adopter refactor | MED | ✅ Корректно |
| R6 | macOS timeout vs Linux gtimeout | LOW | ✅ Корректно |
| R7 | context-init skeleton markup | LOW | ✅ Корректно |
| R8 | template-engine.sh subprocess | LOW | ✅ Корректно |

### MEDIUM: Пропущенный риск — модификация entrypoints/scaffold.sh

**Проблема:** DevPlan L346 говорит "verify — Проверка dispatch (без изменений если фасады сохраняют CLI-контракт)". Но CLI-контракты Wave 2 и Wave 3 НЕ сохраняются (см. CRITICAL findings выше). Если контракты изменятся — scaffold.sh может потребовать изменений.

Кроме того, scaffold.sh L68 делает `exec add-project.sh`, L72 делает `exec context-init.sh`, L86 делает `exec remove-project.sh`, L96 делает `exec project-list.sh`. Если после миграции shell-фасады продолжают принимать те же аргументы — scaffold.sh не требует изменений. Но это предположение должно быть явно верифицировано, а не просто "verify".

**Предлагаемое исправление:** Добавить риск R9: "R9 | CLI contract change → scaffold.sh modification | MED | Фасады сохраняют CLI-контракт; scaffold.sh проверен на каждом wave | Wave 2/3 CRITICAL fix".

### INFO: Пропущенный риск — тесты на реальном gh CLI

**Проблема:** Wave 2 использует `gh repo create`. Тест `test_missing_org_skips_gh` покрывает graceful degradation, но нет теста на успешный вызов `gh`. Это корректно для unit-тестов (gh — внешняя зависимость), но стоит упомянуть как integration-риск.
**Предлагаемое исправление:** Добавить в R4: "integration-тест с реальным gh CLI — отдельный план".

---

## §7. Semantic Markup Compliance

| Проверка | Статус | Детали |
|----------|--------|--------|
| $START_DEVPLAN / $END_DEVPLAN | ✅ | L1, L533 |
| GREP_SUMMARY в DevPlan | N/A | Не требуется для планов (только для кода) |
| TRAP аннотации | ✅ | TRAP node_yaml.py:1186 (L92), R2-R8 с TRAP-links |
| $ARTIFACT_CONTRACT | ✅ | L3-20 |
| Структура секций (§1-§10) | ✅ | Чёткая нумерация, таблицы |

### LOW: $DOCUMENT_PLAN отсутствует

**Локация:** DevPlan (отсутствует)
**Проблема:** doc-protocols SKILL §1 рекомендует создавать `$DOCUMENT_PLAN` skeleton перед генерацией тела документа для защиты от context drift.
**Предлагаемое исправление:** Добавить `$START_DOCUMENT_PLAN` / `$END_DOCUMENT_PLAN` блок перед `$ARTIFACT_CONTRACT`.

### LOW: Файл без NN-префикса

**Локация:** DevPlan.md (должен быть `02-DevPlan.md`)
**Проблема:** Нарушение journal naming grammar из §ARTIFACT_REGISTRY (`{NN}-{Type}.md`). Brief назван `01-Brief.md`, а DevPlan — просто `DevPlan.md`.
**Предлагаемое исправление:** Переименовать в `02-DevPlan.md`.

---

## §8. Out of Scope Audit

### Заявленный out-of-scope

| Элемент | Статус | Комментарий |
|---------|--------|-------------|
| Оптимизация логики | ✅ | Anti-Loop Note — migrate 1:1 |
| vhost_renderer.py | ✅ | 54885 bytes — корректно исключён |
| gen_env_platform.py | ✅ | Уже Python — вызывается как subprocess |
| add-vhost.sh | ✅ | 6059 bytes — корректно исключён |
| Полное удаление shell-фасадов | ✅ | Будущий план |
| adopt-project.sh | ✅ | 89 LOC — уже фасад |

### Потенциально забытые элементы

| Элемент | Статус |
|---------|--------|
| `core/entrypoints/scaffold.sh` — модификация dispatch | ⚠️ Упомянут как "verify" (L346), но без деталей |
| `core/lib/node-resolver.sh` — используется context-init.sh L41 | ⚠️ Не упомянут. После миграции Python-модуль должен самостоятельно разрешать node.yaml |
| `core/lib/args.sh` — используется remove-project.sh L42 | ⚠️ Не упомянут. Python-модуль использует argparse — зависимость исчезает |
| `core/lib/audit_logging.sh` — используется remove-project.sh L44 | ⚠️ Не упомянут. Python-модуль должен имплементировать audit_step эквивалент |

**Рекомендация:** Добавить секцию "Deprecated shell lib dependencies after migration" с перечислением lib-файлов, которые больше не нужны shell-фасадам.

---

## §9. Sequencing & Independence

### Граф зависимостей

```
DP-091 Wave C (merge) ──REQUIRED──→ DP-092 START ✅ (DP-091 существует)
     ┌─────────────────┬─────────────────┐
     ▼                 ▼                 ▼
Wave 1 (lister)  Wave 2 (context)  Wave 3 (remover)
independent ✅    independent ✅    independent ✅
     └─────────────────┴─────────────────┘
                       │
                       ▼
           Wave 4a (scaffold_helpers) ✅
           VERIFY: adopter tests green ✅
                       │
                       ▼
           Wave 4b (project_scaffolder) ✅
```

### Проверка независимости Waves 1-3

| Проверка | Статус | Обоснование |
|----------|--------|-------------|
| Разные Python-модули | ✅ | project_lister.py ≠ context_initializer.py ≠ project_remover.py |
| Разные shell-фасады | ✅ | project-list.sh ≠ context-init.sh ≠ remove-project.sh |
| Разные тестовые файлы | ✅ | test_project_lister.py ≠ test_context_initializer.py ≠ test_project_remover.py |
| Общие зависимости | ✅ | Все три используют NodeYaml (read-only в Wave 1, mutation в Wave 3) — без конфликтов |
| __init__.py exports | ⚠️ | Параллельные изменения __init__.py могут создать merge conflict. Рекомендация: каждый Wave добавляет свой export в отдельную строку, финальный `make fix-gate` выравнивает |

**Вердикт:** Waves 1-3 действительно независимы и могут выполняться параллельно. Wave 4a→4b серийная зависимость корректна.

---

## §10. File Manifest Accuracy

### CREATE

| Файл | DevPlan LOC est. | Реалистичность | Комментарий |
|------|-----------------|----------------|-------------|
| project_lister.py | ~250 | ✅ | 8 shell-функций + argparse boilerplate |
| context_initializer.py | ~200 | ✅ | 7 шагов, каждый ~30 LOC |
| project_remover.py | ~220 | ✅ | 4 шага + report |
| scaffold_helpers.py | ~300 | ✅ | 4 shared-функции ~75 LOC каждая |
| project_scaffolder.py | ~450 | ✅ | 16 shell-функций → Python |
| test_project_lister.py | ~200 | ✅ | 6 тестов |
| test_context_initializer.py | ~180 | ✅ | 5 тестов |
| test_project_remover.py | ~220 | ✅ | 6 тестов |
| test_scaffold_helpers.py | ~200 | ✅ | 4 shared-функции |
| test_project_scaffolder.py | ~280 | ✅ | 8 тестов |

### MODIFY

| Файл | Δ LOC | Статус |
|------|-------|--------|
| project-list.sh | 403→<30 | ✅ |
| context-init.sh | 364→<50 | ✅ |
| remove-project.sh | 423→<50 | ✅ |
| add-project.sh | 782→<50 | ✅ |
| project_adopter.py | -~200 | ✅ |
| __init__.py | +exports | ✅ |
| test_project_lifecycle.py | extend | ⚠️ Не специфицировано, что именно extend |

### WARNING: test_project_lifecycle.py extend не специфицирован

**Локация:** DevPlan L345
**Проблема:** "extend" без деталей. Что добавляется? Новые тесты для новых операций? Модификация существующих?
**Предлагаемое исправление:** Указать конкретно: "добавить test_remove_project_lifecycle, test_list_projects_lifecycle или модифицировать существующие тесты для работы с новыми Python-модулями через shell-фасады".

---

## Findings Summary

### CRITICAL (блокирует реализацию)

| ID | Finding | Локация | Fix |
|----|---------|---------|-----|
| C1 | context_initializer.py CLI mismatch: positional vs named | §3 Wave 2, L211-217 | Option A: принять позиционный `name` в argparse |
| C2 | project_remover.py CLI mismatch: --force исчез, --keep-data добавлен | §3 Wave 3, L245 | Вернуть `--force`, `--keep-data` сделать no-op |

### HIGH (должно быть исправлено перед стартом)

(Нет HIGH-проблем кроме CRITICAL)

### MEDIUM (существенные замечания)

| ID | Finding | Локация | Fix |
|----|---------|---------|-----|
| M1 | Brief vs DevPlan: add-project.sh 11→16 функций | Brief L19 | Обновить Brief |
| M2 | Пропущенный риск: scaffold.sh модификация | DevPlan L346, §7 | Добавить R9 |

### LOW/WARNING (nice-to-have)

| ID | Finding | Локация | Fix |
|----|---------|---------|-----|
| W1 | AC5 верификация слабая | DevPlan L443 | Заменить grep на функциональный тест |
| W2 | $DOCUMENT_PLAN отсутствует | DevPlan (отсутствует) | Добавить skeleton |
| W3 | Файл без NN-префикса | DevPlan.md | Переименовать в 02-DevPlan.md |
| W4 | test_scaffold_helpers.py без LDD | DevPlan L276 | Добавить IMP:9 assertion |
| W5 | Anti-Loop не детализирован по модулям | DevPlan L398 | Добавить модуль-специфичные CHECKLISTs |
| W6 | vhost_renderer.remove_vhost не учтён | DevPlan L131 | Делегировать в vhost_renderer |
| W7 | context-init.sh функций 9, не 7 | DevPlan L39 | Исправить на 9 |
| W8 | test_project_lifecycle.py extend не специфицирован | DevPlan L345 | Уточнить |

---

$END_VERIFICATION_REPORT