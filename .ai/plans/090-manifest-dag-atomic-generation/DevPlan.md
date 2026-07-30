$START_DEVPLAN

$ARTIFACT_CONTRACT
PURPOSE:               Устранение циклической зависимости в генерации манифестов. Построение явного DAG зависимостей 6 генераторов. Атомарная генерация (staging → rename). Удаление shell-фасада gen-env-platform.sh. Re-enable CI check-manifests.
DESCRIPTION:           Система генерации манифестов выросла органически через 5 разных DevPlans. Результат: 6 генераторов без явной топологии, циклическая зависимость (generate_entrypoint_manifest.py читает и пишет один файл), inconsistent state при прерывании `make generate-manifests`, shell-фасад gen-env-platform.sh с unnecessary indirection (4 потребителя), CI check-manifests закомментирован (platform-test.yml L122-125). Этот DevPlan строит явный DAG зависимостей как Makefile-таргеты (3 независимые цепи), добавляет атомарную генерацию (mktemp + trap + rename), ломает цикл в entrypoint-манифесте, добавляет --check режим во все генераторы (G6 уже имеет), удаляет shell-фасад, re-enable check-manifests в CI.
RATIONALE:             Manifest generation — это foundation слой: если манифесты расходятся с источниками, CI gate'ы дают ложные срабатывания. Циклическая зависимость маскирует баги генерации (генератор может читать свой же output как input). Shell-фасад gen-env-platform.sh создаёт unnecessary indirection с 4 потребителями. CI check-manifests отключён — divergence не детектируется при push.
ACCEPTANCE_CRITERIA:
  - AC1: Manifest DAG документирован в Makefile как цепочка .PHONY зависимостей (3 независимые цепи: A=secrets→platform→env-example, B=entrypoint→agents-md, C=litellm-config)
  - AC2: generate_entrypoint_manifest.py — разорван цикл: читает структуру из Makefile/tests, пишет allowed_verbs/gates. НЕ читает свой output.
  - AC3: make generate-manifests-atomic — генерация в staging/ (mktemp + trap EXIT), атомарный rename. При failure staging гарантированно удалён.
  - AC4: Все 6 генераторов имеют --check режим (byte-level comparison). G5-G6 уже имеют, формализованы под единый контракт.
  - AC5: gen-env-platform.sh → удалён. Все 4 потребителя вызывают gen_env_platform.py напрямую (import или python3 CLI).
  - AC6: make check-manifests использует --check каждого генератора + git diff --exit-code
  - AC7: make gate MODE=fast — зелёный
  - AC8: python -m pytest tests/ -v — все тесты проходят
  - AC9: .github/workflows/platform-test.yml — check-manifests раскомментирован
  - AC10: test_no_shell_manifest_generators.py — fail если любой генератор манифестов в shell
IMPLEMENTS:            Superposition Analysis 2026-07-28 — Проблема 5 (Manifest DAG cycle) + Agent 4 S5 findings + Agent 3 Manifest domain gaps + Findings 1-3 из архитектурного аудита 2026-07-28
IMPACTS:               19 файлов (5 CREATE, 13 MODIFY, 1 DELETE). Подробно в §4 File Manifest.
REQUIRES:              DP-088 (NodeYaml — генераторы будут использовать NodeYaml.load()). DP-089 (Deploy — не конфликтует, но может пересекаться по Makefile/CI changes). Рекомендуется merge DP-089 перед стартом DP-090 или координировать изменения.
$END_ARTIFACT_CONTRACT

---

# DevPlan 090: Manifest DAG + Atomic Generation

**Severity:** MEDIUM — CI reliability, но foundation-слой (влияет на все gate'ы)
**Created:** 2026-07-28
**Author:** Kilo (architect agent)
**Source:** Superposition S5, Agent 4 Manifest findings, Agent 3 Manifest domain, Architect audit 2026-07-28
**Sequenced:** AFTER DP-089 (Deploy), LAST in sequence

---

## §1. Current State

### 6 генераторов, 9 выходных файлов, 1 циклическая зависимость

```
Генераторы (в порядке выполнения):
  G1. generate_secrets_manifest.py
      IN:  secret-definitions.yaml, core/modules/*/module.yaml
      OUT: secrets-manifest.yaml

  G2. generate_platform_env.py
      IN:  platform-infra.yaml, secret-definitions.yaml, module discovery
      OUT: platform-env.yaml, smoke_env_generated.py, env_defaults_generated.py

  G3. generate_entrypoint_manifest.py   ← ЦИКЛИЧЕСКАЯ ЗАВИСИМОСТЬ
      IN:  entrypoint-manifest.yaml (читает СВОЙ ЖЕ output!)
      OUT: entrypoint-manifest.yaml#allowed_verbs/gates

  G4. generate_agents_md.py
      IN:  entrypoint-manifest.yaml
      OUT: core/AGENTS.md (canonical table + forbidden lists)

  G5. sync_env_defaults.py
      IN:  platform-env.yaml, secret-definitions.yaml
      OUT: .env.example

  G6. config_renderer.py (пропущен в предыдущей версии DevPlan)
      IN:  core/internal/llm/policy.yaml
      OUT: core/modules/litellm/config/litellm-config.yml
      --check: ✅ уже реализован (check_freshness, --check CLI arg)
```

**Циклическая зависимость (G3)**: `generate_entrypoint_manifest.py`:
1. Читает `entrypoint-manifest.yaml` → извлекает секции `allowed_verbs`, `gates`
2. Сканирует Makefile `.PHONY` targets + `tests/gates/*.py` `@pytest.mark.gate`
3. **Пишет** обновлённые секции в тот же `entrypoint-manifest.yaml`

Это самоподдерживающийся цикл: генератор читает свой output как input. Если генератор производит некорректный output в одном запуске, следующий запуск использует этот некорректный output как input → ошибка накапливается.

### Shell-фасад gen-env-platform.sh — unnecessary indirection (не дубликат)

**ОШИБКА в предыдущей версии:** DD4 и §1 утверждали, что gen-env-platform.sh — «дубликат» gen_env_platform.py. Это НЕВЕРНО.

**Реальность:**

| Файл | Язык | LOC | LOC логики | Роль |
|------|------|-----|-----------|------|
| `core/internal/scaffold/gen-env-platform.sh` | Shell | 165 | ~23 | Тонкий shell-фасад: arg parsing + delegate |
| `core/internal/scaffold/gen_env_platform.py` | Python | 153 | ~130 | Python-генератор .env.platform |

gen-env-platform.sh делегирует gen_env_platform.py через `python3 gen_env_platform.py` (строки 99-103). Проблема — **unnecessary indirection**, не duplication. Shell-фасад добавляет точку отказа без добавленной стоимости.

**4 потребителя** (не 2 как указано в предыдущей версии):

| Потребитель | Тип | Механизм вызова | Мишень |
|-------------|-----|-----------------|--------|
| `core/internal/scaffold/add-project.sh` | Shell | subprocess `gen-env-platform.sh` | Шаги T9d→T9e |
| `core/entrypoints/scaffold.sh` (sync-env) | Shell | `exec gen-env-platform.sh` | Шаги T9c→T9e |
| `core/internal/bootstrap/converge/reconciler.py` | Python | subprocess `bash gen-env-platform.sh` | Шаги T9b→T9e |
| `core/internal/scaffold/project_adopter.py` | Python | subprocess `gen_env_platform.py` (CLI-first) | Шаги T9a→T9e |

Дополнительная проблема: gen_env_platform.py имеет CLI-first дизайн — `main()` использует `print()` и `sys.exit()`, что предотвращает прямой import как библиотеки. project_adopter.py уже вызывает Python напрямую через subprocess, а не через shell-фасад, что подтверждает: shell-фасад — лишний слой.

### CI check-manifests DISABLED

`.github/workflows/platform-test.yml` lines 122-125:
```yaml
# - name: Check generated manifests up to date
#   run: make check-manifests
- name: Check manifests (DISABLED — test server testing)
  run: echo "[IMP:9][check-manifests] DISABLED — TESTING TEST SERVER — skipping"
```

check-manifests закомментирован с пометкой «DISABLED — TESTING TEST SERVER — skipping» (legacy от DP-089 тестового сервера). Это нарушает AC6: CI не проверяет divergence манифестов. Требует re-enable после переписывания генераторов.

### Проблема атомарности

`make generate-manifests` вызывает 6 генераторов последовательно в shell-рецепте. Если генератор 3 падает — генераторы 1 и 2 уже изменили свои output-файлы. Файловая система в inconsistent state. `make check-manifests` обнаружит divergence, но `git checkout` нужен для восстановления.

---

## §2. Target State

### Manifest DAG (Makefile dependencies)

```makefile
# Makefile — явный DAG через зависимости .PHONY таргетов
# 3 независимые цепи:
#   Chain A: G1 → G2 → G5 (secrets-manifest → platform-env → .env.example)
#   Chain B: G3 → G4 (entrypoint-manifest → AGENTS.md)
#   Chain C: G6 (litellm-config — полностью независима)

.PHONY: generate-manifests
generate-manifests: generate-secrets-manifest generate-entrypoint-manifest generate-litellm-config

# ── Chain A ─────────────────────────────────────────────────
.PHONY: generate-secrets-manifest
generate-secrets-manifest:
	python3 core/internal/scripts/generate_secrets_manifest.py \
		--secret-defs core/secret-definitions.yaml \
		--modules-dir core/modules \
		--output core/secrets-manifest.yaml

.PHONY: generate-platform-env
generate-platform-env: generate-secrets-manifest
	python3 core/internal/scripts/generate_platform_env.py \
		--infra core/platform-infra.yaml \
		--modules-dir core/modules \
		--secret-defs core/secret-definitions.yaml \
		--output platform-env.yaml \
		--smoke-env-output tests/_conftest/smoke_env_generated.py \
		--helpers-output tests/helpers/env_defaults_generated.py

.PHONY: generate-env-example
generate-env-example: generate-platform-env
	python3 core/internal/scripts/sync_env_defaults.py \
		--platform-env platform-env.yaml \
		--secret-defs core/secret-definitions.yaml \
		--output .env.example

# ── Chain B ─────────────────────────────────────────────────
.PHONY: generate-entrypoint-manifest
generate-entrypoint-manifest:
	python3 core/internal/scripts/generate_entrypoint_manifest.py \
		--makefile-dir . \
		--gmake-path $(shell which gmake 2>/dev/null || which make 2>/dev/null || echo make) \
		--existing-manifest core/entrypoint-manifest.yaml \
		--tests-dir tests/gates \
		--output core/entrypoint-manifest.yaml

.PHONY: generate-agents-md
generate-agents-md: generate-entrypoint-manifest
	python3 core/internal/scripts/generate_agents_md.py \
		--manifest core/entrypoint-manifest.yaml \
		--agents-md core/AGENTS.md \
		--marker canon_table

# ── Chain C ─────────────────────────────────────────────────
.PHONY: generate-litellm-config
generate-litellm-config:
	python3 core/internal/llm/config_renderer.py \
		--policy core/internal/llm/policy.yaml \
		--output core/modules/litellm/config/litellm-config.yml
```

**3 независимые цепи:**
- **Chain A** (G1→G2→G5): secrets-manifest.yaml → platform-env.yaml + smoke/env_defaults → .env.example. Sequential внутри цепи.
- **Chain B** (G3→G4): entrypoint-manifest.yaml#allowed_verbs/gates → core/AGENTS.md. Sequential внутри цепи.
- **Chain C** (G6): litellm-config.yml. Zero dependencies — может выполняться параллельно с A и B.

Цепи A и B независимы — не имеют общих input/output файлов. `make -j` может выполнять их параллельно.

### Атомарная генерация

```makefile
.PHONY: generate-manifests-atomic
generate-manifests-atomic:
	@staging=$$(mktemp -d /tmp/manifest-gen-XXXXXX); \
	trap "rm -rf $$staging" EXIT; \
	# Chain A: secrets → platform-env → env-example
	python3 core/internal/scripts/generate_secrets_manifest.py ... --output $$staging/secrets-manifest.yaml && \
	python3 core/internal/scripts/generate_platform_env.py ... --output $$staging/platform-env.yaml && \
	python3 core/internal/scripts/sync_env_defaults.py ... --output $$staging/.env.example && \
	# Chain B: entrypoint → agents-md
	python3 core/internal/scripts/generate_entrypoint_manifest.py ... --output $$staging/entrypoint-manifest.yaml && \
	python3 core/internal/scripts/generate_agents_md.py ... --output $$staging/AGENTS.md && \
	# Chain C: litellm-config
	python3 core/internal/llm/config_renderer.py ... --output $$staging/litellm-config.yml && \
	# Атомарный rename — все или ничего (одиночный mv, не цикл)
	mv $$staging/* "$(PLATFORM_ROOT)/"
	# Примечание: единый mv атомарнее цикла с basename. Работает когда staging/ и target/ на одном разделе (основной сценарий — /tmp и проект на одном FS).
	# trap EXIT автоматически удаляет staging при failure или signal
```

**Изменения против предыдущей версии:**
- `mkdir -p /tmp/manifest-gen-$$` → `mktemp -d /tmp/manifest-gen-XXXXXX` (безопасный уникальный temp, PID collision-resistant)
- `trap "rm -rf $$staging" EXIT` — гарантированная очистка при failure (ABORT, сигнал)
- Три явные цепи документированы в комментариях

### Разрыв цикла G3

```python
# generate_entrypoint_manifest.py — НОВЫЙ дизайн
# IN:  Makefile (список .PHONY targets), tests/gates/*.py (маркеры @pytest.mark.gate)
# IN:  entrypoint-manifest.yaml — ТОЛЬКО структурные секции (metadata, convention, schema)
#      НЕ читает allowed_verbs/gates — эти секции генерируются ЗАНОВО из sources
# OUT: entrypoint-manifest.yaml — allowed_verbs и gates ПЕРЕЗАПИСЫВАЮТСЯ полностью

def generate(input_manifest_path, makefile_path, gate_tests_dir, output_path):
    manifest = yaml.safe_load(open(input_manifest_path))
    # Сохраняем структурные секции
    metadata = manifest['metadata']
    convention = manifest['convention']
    schema = manifest.get('schema', {})

    # Генерируем allowed_verbs ИЗ Makefile (не из manifest!)
    verbs = extract_verbs_from_makefile(makefile_path)

    # Генерируем gates ИЗ тестов (не из manifest!)
    gates = extract_gates_from_tests(gate_tests_dir)

    # Пишем output — полная перезапись allowed_verbs/gates
    output = {
        'metadata': metadata,
        'convention': convention,
        'schema': schema,
        'allowed_verbs': verbs,
        'gates': gates,
    }
    yaml.dump(output, open(output_path, 'w'))
```

### §Check Mode Contract

Каждый генератор реализует `--check` режим по единому контракту:

1. **Byte-level comparison (не YAML semantic)**: сгенерированный output сравнивается с существующим файлом на диске побайтово. Не семантически (YAML с разным order ключей считается divergence). Причина: `make check-manifests` проверяет идентичность коммиту, не семантическую эквивалентность.

2. **Exit code**: 0 = output совпадает (fresh), 1 = divergence (stale). Единый exit code для всех генераторов.

3. **Stderr diff**: при divergence выводит diff (первые 20 строк изменений) на stderr. Без вывода при совпадении.

4. **--output required**: `--check` без `--output` → error (нет файла для сравнения). Исключение: G5/G6 используют известный output path из аргументов.

5. **Read-only**: `--check` НЕ пишет на диск. Output генерируется в temp (memory/tempfile) и сравнивается побайтово.

### --check Статус по генераторам

| Генератор | --check статус | Задача | Effort |
|-----------|---------------|--------|--------|
| G1 (generate_secrets_manifest.py) | Отсутствует | T1: добавить --check | 1 |
| G2 (generate_platform_env.py) | Отсутствует | T2: добавить --check — проверяет все 3 выходных файла одновременно (каждый сравнивается побайтово с диском). `--check` НЕ принимает `--output`; сравнивает все известные output path'ы, указанные при обычном запуске. | 2 |
| G3 (generate_entrypoint_manifest.py) | Отсутствует | T3: добавить --check | 1 |
| G4 (generate_agents_md.py) | Отсутствует | T4: добавить --check | 1 |
| G5 (sync_env_defaults.py) | ✅ Существует (--check, exit 2) | T5: формализовать до byte-level, exit code 1 | 0.5 |
| G6 (config_renderer.py) | ✅ Существует (--check, check_freshness) | T0: формализовать --check (exit 0/1, stderr diff) | 0.5 |

---

## §3. Wave Structure

### Wave 1: Foundation — --check mode for all generators

| Task | Описание | Effort |
|------|----------|--------|
| **T0** | G6 (config_renderer.py): формализовать --check — unit test для exit code 0/1, stderr diff. Существующий check_freshness остаётся, добавляется единый контракт. | 0.5 |
| **T1** | G1 (generate_secrets_manifest.py): добавить --check режим (byte-level, exit 0/1, stderr diff) | 1 |
| **T2** | G2 (generate_platform_env.py): добавить --check — побайтовое сравнение всех 3 выходных файлов одновременно. `--check` НЕ принимает `--output`; сравнивает все зарегистрированные output path'ы, указанные при обычном запуске. | 2 |
| **T3** | G3 (generate_entrypoint_manifest.py): добавить --check режим | 1 |
| **T4** | G4 (generate_agents_md.py): добавить --check режим | 1 |
| **T5** | G5 (sync_env_defaults.py): формализовать --check — byte-level comparison, exit code 0/1 (было 0/2) | 0.5 |

*G6 (config_renderer.py) уже имеет --check (check_freshness) — T0 формализует exit code и stderr diff под единый контракт.*

### Wave 2: Break G3 cycle + DAG

| Task | Описание | Effort |
|------|----------|--------|
| **T6** | G3 рефакторинг: разорвать цикл — allowed_verbs/gates НЕ читаются из manifest, генерируются заново из Makefile + tests/. Сохранять структурные секции (metadata, convention, schema). | 3 |
| **T7** | Makefile DAG: заменить последовательный recipe на .PHONY цепочку зависимостей (3 независимые цепи A/B/C) | 2 |
| **T8** | make generate-manifests-atomic: mktemp + trap EXIT + rename. Failure → staging удалён, оригиналы не тронуты. | 2 |

### Wave 3: Remove shell facade + Gate

| Task | Описание | Effort |
|------|----------|--------|
| **T9a** | gen_env_platform.py: извлечь `generate()` как библиотечную функцию (убрать `print()` + `sys.exit()` из logic, CLI-обёртка остаётся). | 1 |
| **T9b** | reconciler.py: мигрировать с subprocess `bash gen-env-platform.sh` на прямой `import gen_env_platform; gen_env_platform.generate()`. | 1 |
| **T9c** | scaffold.sh sync-env: мигрировать с `exec gen-env-platform.sh` на `python3 gen_env_platform.py --yaml ... --name ... --domain ... --output ...` | 1 |
| **T9d** | add-project.sh: мигрировать с `gen-env-platform.sh --name ...` на прямой вызов `gen_env_platform.py` (python3 CLI) | 1 |
| **T9e** | Удалить `core/internal/scaffold/gen-env-platform.sh`. Проверить отсутствие references (grep по core/ entrypoint-manifest.yaml). | 0.5 |
| **T9f** | `core/entrypoint-manifest.yaml`: удалить 2 stale references к gen-env-platform.sh в allowed_verbs/platform-deliver. grep: `gen-env-platform\.sh` в entrypoint-manifest.yaml. | 0.5 |
| **T10** | make check-manifests: переписать на --check всех 6 генераторов (быстрее git diff). Exit 0 = все fresh. | 1 |
| **T10.5** | CI: re-enable check-manifests в `.github/workflows/platform-test.yml` — раскомментировать lines 122-125, убрать echo-заглушку. | 0.5 |
| **T11** | Gate tests: `test_manifest_dag_acyclic.py`, `test_no_shell_manifest_generators.py` (+ M8, M9) | 2 |
| **T12** | make fix-gate + make gate MODE=fast + pytest tests/ -v | 1 |

### Meta: Tests

| Task | Описание | Effort |
|------|----------|--------|
| **M8** | `test_generate_entrypoint_manifest_no_self_read.py` — верификация: G3 не читает allowed_verbs/gates из entrypoint-manifest.yaml | 1 |
| **M9** | `test_atomic_generation_no_partial_writes.py` — верификация: при failure atomic generation staging очищен, оригиналы не тронуты | 1 |
| **M10** | `test_no_shell_manifest_generators.py` — запрет shell-генераторов манифестов (обобщает test_no_shell_gen_env.py) | 1 |

**Total effort: 6.0 + 7 + 9.5 + 3 = 25.5 → 26**

---

## §4. File Manifest

### CREATE (5)
| Файл | Назначение |
|------|-----------|
| `tests/gates/test_manifest_dag_acyclic.py` | Gate: проверка ацикличности DAG генераторов |
| `tests/gates/test_generate_entrypoint_manifest_no_self_read.py` | Gate: G3 не читает allowed_verbs/gates из manifest |
| `tests/gates/test_atomic_generation_no_partial_writes.py` | Gate: atomic generation при failure не оставляет partial writes |
| `tests/gates/test_no_shell_manifest_generators.py` | Gate: запрет shell-генераторов манифестов (обобщает test_no_shell_gen_env.py) |
| `tests/gates/test_yaml_deterministic_output.py` | Gate: byte-level determinism — два запуска с одинаковыми inputs дают идентичный bytes output |

### MODIFY (12)
| Файл | Изменение |
|------|----------|
| `core/internal/scripts/generate_entrypoint_manifest.py` | Разрыв цикла — allowed_verbs/gates из sources, не из manifest |
| `core/internal/scripts/generate_secrets_manifest.py` | +--check режим (byte-level) |
| `core/internal/scripts/generate_platform_env.py` | +--check режим (3 output файла) |
| `core/internal/scripts/generate_agents_md.py` | +--check режим |
| `core/internal/scripts/sync_env_defaults.py` | Формализовать --check: byte-level, exit code 0/1 |
| `core/internal/llm/config_renderer.py` | Формализовать --check под единый контракт (minor — exit code, stderr diff) |
| `core/internal/scaffold/gen_env_platform.py` | Извлечь generate() как библиотечную функцию (убрать print/sys.exit из логики) |
| `core/internal/bootstrap/converge/reconciler.py` | Мигрировать с subprocess на прямой import gen_env_platform.generate() |
| `core/entrypoints/scaffold.sh` | sync-env: вызывать python3 gen_env_platform.py вместо exec gen-env-platform.sh |
| `core/internal/scaffold/add-project.sh` | Вызывать gen_env_platform.py напрямую (python3 CLI) |
| `.github/workflows/platform-test.yml` | Re-enable check-manifests step (раскомментировать L122-125) |
| `core/entrypoint-manifest.yaml` | T9f: удалить 2 stale references к gen-env-platform.sh (allowed_verbs/platform-deliver) |

### MODIFY (Makefile)
| Файл | Изменение |
|------|-----------|
| `Makefile` | DAG зависимостей (3 цепи) + generate-manifests-atomic (mktemp+trap) + check-manifests через --check |

### DELETE (1)
| Файл | Причина |
|------|---------|
| `core/internal/scaffold/gen-env-platform.sh` | Shell-фасад; вся логика в gen_env_platform.py; все потребители мигрированы |

---

## §5. Acceptance Criteria (Detailed)

- [ ] AC1: `make generate-manifests -n` показывает DAG порядок: secrets-manifest → platform-env → env-example (Chain A), entrypoint-manifest → agents-md (Chain B), litellm-config (Chain C)
- [ ] AC2: `generate_entrypoint_manifest.py` — grep "yaml.safe_load.*entrypoint-manifest" внутри функции generate() → только для metadata/convention/schema, НЕ для allowed_verbs/gates
- [ ] AC3: `make generate-manifests-atomic` — создаёт staging/ (mktemp), генерирует туда, атомарно mv. При failure trap EXIT удаляет staging. Оригиналы не тронуты.
- [ ] AC4: `python3 core/internal/scripts/generate_secrets_manifest.py --check; echo $?` → 0 (нет divergence). Аналогично для G2-G6.
- [ ] AC5: `ls core/internal/scaffold/gen-env-platform.sh` → file not found. `grep "gen-env-platform\.sh" core/` → empty (кроме исторических references в AGENTS.md)
- [ ] AC6: `make check-manifests` — использует --check всех 6 генераторов (быстрее полной генерации + git diff)
- [ ] AC7: `make gate MODE=fast` — зелёный, все gate тесты PASS
- [ ] AC8: `python -m pytest tests/ -v` — все тесты проходят
- [ ] AC9: `.github/workflows/platform-test.yml` — check-manifests раскомментирован (L122-125), echo-заглушка удалена
- [ ] AC10: `python -m pytest tests/gates/test_no_shell_manifest_generators.py -v` — PASS; fail если любой генератор манифестов реализован в shell

---

## §6. Design Decisions

### DD1: Почему --check в каждом генераторе, а не один скрипт?
Каждый генератор знает свой контракт: какие входы, какие выходы, как сравнивать. Единый check-скрипт потребовал бы дублирования этого знания. --check как часть контракта генератора: «я знаю, как проверить свой output».

### DD2: Почему разрыв цикла через перегенерацию allowed_verbs/gates с нуля?
Текущий дизайн (читать старый manifest → модифицировать → писать) создаёт цикл, где ошибка накапливается. Новый дизайн (читать ТОЛЬКО структурные секции из manifest, allowed_verbs/gates генерировать ЗАНОВО из авторитетных источников) гарантирует, что каждый запуск производит одинаковый output при одинаковых inputs. Нет накопления ошибок между запусками.

### DD3: Почему атомарная генерация через staging/, а не git worktree?
Git worktree требует clean working tree и сложнее в CI (нужен git). Staging directory в /tmp проще: mktemp + generate + mv. При failure trap EXIT удаляет staging, оригиналы не тронуты. CI всегда имеет clean working tree (checkout), поэтому staging подходит.

### DD4 (CORRECTED): Почему gen-env-platform.sh удаляется, а не остаётся фасадом?

**ОШИБКА в предыдущей версии (DD4):** утверждалось, что gen-env-platform.sh (160 LOC) и gen_env_platform.py (150 LOC) — «прямые дубликаты». Это НЕВЕРНО. См. §1 Current State для полного анализа.

**Реальность:** gen-env-platform.sh — тонкий shell-фасад (~23 LOC логики делегации из 165 total). Проблема — **unnecessary indirection**, не duplication. 4 потребителя используют разные механизмы вызова, создавая несогласованный паттерн.

**Почему удаляем, а не оставляем:**
1. gen_env_platform.py имеет CLI-first дизайн (sys.exit в main()) — не может быть импортирован как библиотека. T9a исправляет это.
2. После T9b-T9d: все 4 потребителя вызывают gen_env_platform.py напрямую (Python import или python3 CLI). Shell-фасад становится мёртвым кодом.
3. DD4 прошлой версии содержал фактическую ошибку (называл фасад дубликатом). Исправлено.

### DD5: Почему make check-manifests через --check, а не git diff?
`git diff --exit-code` требует предварительной полной генерации (медленно: ~10-15 сек). --check каждого генератора быстрее (читает sources, генерирует в память, сравнивает побайтово с файлом — без записи на диск). Для CI критично время gate. После T10 check-manifests запускает 6 --check параллельно (make -j 6).

### DD6 (NEW): Почему --check использует byte-level comparison, не YAML semantic?

YAML semantic comparison требует загрузки обоих файлов в Python-объекты и сравнения структур. Это:
1. Медленнее (парсинг YAML, рекурсивное сравнение)
2. Пропускает косметические различия (order ключей, комментарии, форматирование, trailing whitespace)
3. Противоречит цели --check: проверка идентичности закоммиченному файлу

Byte-level comparison дешевле и строже. Если semantic-валидация структуры нужна — это задача отдельного инструмента (validate-modules), не --check.

### DD7 (NEW): Почему mktemp вместо mkdir?

`mktemp -d /tmp/manifest-gen-XXXXXX` гарантирует уникальность имени (PID collision-resistant). Предыдущий вариант `mkdir -p /tmp/manifest-gen-$$` мог коллизить при параллельных запусках генерации на одном CI runner. `mktemp` — стандартный безопасный паттерн в shell/POSIX.

### DD8 (NEW): Почему 3 независимые цепи?

Цепи A (G1→G2→G5), B (G3→G4) и C (G6) не имеют общих input/output файлов и могут выполняться независимо:

| Характеристика | Chain A | Chain B | Chain C |
|---------------|---------|---------|---------|
| Генераторы | G1, G2, G5 | G3, G4 | G6 |
| Входы | secret-definitions, platform-infra, module.yaml | Makefile, tests/gates, entrypoint-manifest (structural) | policy.yaml |
| Выходы | secrets-manifest.yaml, platform-env.yaml, .env.example | entrypoint-manifest.yaml (verbs/gates), AGENTS.md | litellm-config.yml |
| Атомарность | Sequential внутри | Sequential внутри | Singleton |

**Почему не все 6 независимы?** G2 зависит от G1 (secrets-manifest → platform-env включает secrets). G4 зависит от G3 (AGENTS.md строится из entrypoint-манифеста). G5 зависит от G2 (.env.example строится из platform-env.yaml). Но A и B независимы друг от друга — `make -j 2` может выполнять их параллельно.

---

## $TEST_SPEC

| Test file | Test function | Scenario | Module under test | Expected Result |
|-----------|--------------|----------|-------------------|----------------|
| `tests/gates/test_manifest_dag_acyclic.py` | `test_generator_dag_acyclic` | Проверка ацикличности Makefile .PHONY цепочки генераторов | Makefile generate-manifests DAG | PASS если `make generate-manifests -n` показывает ацикличный порядок (3 цепи, без циклов); FAIL если обнаружен цикл |
| `tests/gates/test_generate_entrypoint_manifest_no_self_read.py` | `test_no_self_read` | G3 не читает allowed_verbs/gates из entrypoint-manifest.yaml | generate_entrypoint_manifest.py | PASS если G3 читает только metadata/convention/schema из manifest, НЕ allowed_verbs/gates; FAIL если читает allowed_verbs/gates |
| `tests/gates/test_atomic_generation_no_partial_writes.py` | `test_no_partial_writes_on_failure` | При failure atomic generation staging/ удалён, оригиналы не тронуты | Makefile generate-manifests-atomic | PASS если после failure staging/ пуст/отсутствует и оригинальные файлы не изменены; FAIL если staging остался или файлы повреждены |
| `tests/gates/test_no_shell_manifest_generators.py` | `test_no_shell_generators` | Ни один генератор манифестов не реализован в shell (все — .py) | All generators (G1-G6) | PASS если grep по core/entrypoints/*.sh и core/internal/*.sh не находит shell-генераторы манифестов; FAIL если найден |
| `tests/gates/test_yaml_deterministic_output.py` | `test_yaml_deterministic_output` | Два запуска генератора с одинаковыми inputs дают идентичный bytes output | generate_secrets_manifest.py, generate_platform_env.py, generate_entrypoint_manifest.py | PASS если output двух последовательных запусков идентичен побайтово; FAIL если различается |

---

## §7. Implementation Commands

```
# === WAVE 1: --check mode ===
coder implement DevPlan 090 Wave 1:
  T0-T5 (--check mode для G1-G6; T0 формализует --check G6 под единый контракт)

# Verify Wave 1
for gen in generate_secrets_manifest generate_platform_env generate_entrypoint_manifest generate_agents_md sync_env_defaults; do
  python3 core/internal/scripts/$gen.py --check; echo "$gen: $?"
done
python3 core/internal/llm/config_renderer.py --check \
  --policy core/internal/llm/policy.yaml \
  --output core/modules/litellm/config/litellm-config.yml; echo "config_renderer: $?"

# === WAVE 2: Break cycle + DAG ===
coder implement DevPlan 090 Wave 2:
  T6 (G3: разрыв цикла), T7 (Makefile DAG), T8 (атомарная генерация),
  M8 (test_no_self_read), M9 (test_no_partial_writes)

# Verify Wave 2
make generate-manifests -n
# Проверить порядок: Chain A (secrets-manifest → platform-env → env-example),
# Chain B (entrypoint-manifest → agents-md), Chain C (litellm-config)
make generate-manifests-atomic
# Проверить: mktemp создаёт staging/, trap EXIT очищает при failure,
# атомарный mv: все файлы обновлены или ни один

# === WAVE 3: Remove shell facade + Gate ===
coder implement DevPlan 090 Wave 3:
  T9a (gen_env_platform.py library extract),
  T9b (reconciler.py → direct import),
  T9c (scaffold.sh sync-env → python3 gen_env_platform.py),
  T9d (add-project.sh → python3 gen_env_platform.py),
  T9e (delete gen-env-platform.sh),
  T9f (entrypoint-manifest.yaml: удалить stale references к gen-env-platform.sh),
  T10 (check-manifests через --check),
  T10.5 (CI re-enable check-manifests),
  T11 (gate tests: dag_acyclic + no_shell_generators),
  M10 (test_no_shell_manifest_generators.py),
  T12 (fix-gate + gate + pytest)

# Verify Wave 3
ls core/internal/scaffold/gen-env-platform.sh 2>&1
# Expected: No such file or directory
grep "gen-env-platform\.sh" core/entrypoint-manifest.yaml
# Expected: empty (0 matches) — stale references удалены
make check-manifests
# Expected: 0 exit code, использует --check всех 6 генераторов (быстрее git diff)

# Verify CI
grep -A 3 "check-manifests" .github/workflows/platform-test.yml
# Expected: раскомментирован (no leading #), заглушка DISABLED удалена

# Final verification
make fix-gate && make gate MODE=fast
python3 -m pytest tests/ -v
```

$END_DEVPLAN
