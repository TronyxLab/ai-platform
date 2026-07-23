# GREP_SUMMARY: DevPlan 064 CI deduplication infrastructure compose-profiles cleanup-docker compose-files variable agents-md-loc-removal
# STRUCTURE: ┌ARTIFACT_CONTRACT┐ → ◇ superposition-collapse (S1+S2+S3+S4+S5) → ⊕ file-manifest → ⚡ step-plan → ⚠ TRAP[INDEX] → ⎋ verification

$START_DEVPLAN
$ARTIFACT_CONTRACT
PURPOSE:               Устранить 5 источников дублирования в CI-инфраструктуре и Makefile: (S1) COMPOSE_PROFILES хардкод в 3 местах, (S2) cleanup-логика в 2 workflow, (S3) верификация discover-modules composite action, (S4) compose-file логика в 4 таргетах modules.mk, (S5) устаревшие LOC-цифры в AGENTS.md.
DESCRIPTION:           Пять изолированных изменений: (1) новый composite action compose-profiles — читает platform-env.yaml → экспортирует COMPOSE_PROFILES в $GITHUB_ENV, заменяет хардкод в platform-test.yml и push-gate.yml; (2) новый composite action cleanup-docker — единая логика docker compose down для всех workflows; (3) аудит discover-modules — подтверждение что используется composite action, не inline; (4) переменная COMPOSE_BASE_FILES в Makefile — устраняет 4× дублирование в up/down/restart/status; (5) удаление блока LOC-статистики из core/internal/bootstrap/AGENTS.md.
RATIONALE:             COMPOSE_PROFILES хардкожен в Makefile:30, platform-test.yml:71, push-gate.yml:47. При добавлении нового модуля — правки в 3 местах, риск рассинхронизации. Cleanup-логика идентична в platform-test.yml:358-372 и nightly-gate.yml:111-125 (22 строки × 2). modules.mk содержит один и тот же блок compose-file resolution 4 раза (up:28-31, down:49-52, restart:59-62, status:69-72). AGENTS.md содержит LOC-цифры (reconciler.py: 2136→2284, state_machine.py: 1599→2086, steps.py: 729→994, etc.) — расхождение с реальностью задокументировано в анализе 2026-07-23.
ACCEPTANCE_CRITERIA:   (1) `make _get_all_profiles` и `platform-env.yaml` profiles совпадают; (2) CI platform-test и push-gate зелёные после замены хардкода на composite action; (3) cleanup-docker composite action работает в nightly-gate и platform-test; (4) `make up`, `make down`, `make restart`, `make status` работают идентично с COMPOSE_BASE_FILES; (5) `make check-manifests` проходит; (6) AGENTS.md не содержит устаревших LOC-цифр
IMPLEMENTS:            Анализ оптимизаций ai-platform от 2026-07-23 — пункты 1-5
IMPACTS:               .github/actions/compose-profiles/ (новый), .github/actions/cleanup-docker/ (новый), .github/workflows/platform-test.yml, .github/workflows/push-gate.yml, .github/workflows/nightly-gate.yml, Makefile, makefiles/modules.mk, core/internal/bootstrap/AGENTS.md
REQUIRES:              Локальный `make gate MODE=fast`, `make up`/`make down` для верификации modules.mk; push в feature-ветку для верификации CI
$END_ARTIFACT_CONTRACT

---

## $DOCUMENT_PLAN

**SECTION_GOALS:**
- GOAL S1: COMPOSE_PROFILES — единый источник через composite action → GOAL_PROFILES
- GOAL S2: Cleanup logic — composite action cleanup-docker → GOAL_CLEANUP
- GOAL S3: Module list generation — аудит, подтверждение dedup → GOAL_MODLIST
- GOAL S4: modules.mk compose-file — переменная COMPOSE_BASE_FILES → GOAL_COMPOSE
- GOAL S5: AGENTS.md — удаление устаревших LOC-цифр → GOAL_DOCS
- GOAL V: Верификация всех изменений локально и в CI → GOAL_VERIFY

**SECTION_USE_CASES:**
- USE_CASE 1: Разработчик пушит PR → platform-test: compose-profiles читает platform-env.yaml → UC_PR
- USE_CASE 2: Разработчик пушит в feature-ветку → push-gate: compose-profiles читает platform-env.yaml → UC_PUSH
- USE_CASE 3: Ночной прогон → nightly-gate: cleanup-docker composite action → UC_NIGHTLY
- USE_CASE 4: Локальный запуск → make up/down/restart/status с COMPOSE_BASE_FILES → UC_LOCAL
- USE_CASE 5: Добавлен новый модуль → platform-env.yaml обновлён через generate-manifests → COMPOSE_PROFILES подхватывается автоматически во всех CI workflows → UC_NEW_MODULE
$END_DOCUMENT_PLAN

---

## Часть 1: S1 — COMPOSE_PROFILES dedup (3 копии → 1 composite action)

### Контекст

Строка из 13 модульных профилей хардкожена в трёх местах:

| Место | Строка | Роль |
|-------|--------|------|
| `Makefile:30` | `export COMPOSE_PROFILES ?= postgres,redis,...` | Локальный source of truth |
| `platform-test.yml:71` | `COMPOSE_PROFILES: "postgres,redis,..."` | CI job-level env |
| `push-gate.yml:47` | `COMPOSE_PROFILES: "postgres,redis,..."` | CI job-level env |

Авторитетный источник списка профилей — `platform-env.yaml:176-189` (секция `profiles`), генерируемая `generate_platform_env.py`. При добавлении нового модуля список обновляется автоматически через `make generate-manifests`.

### Целевое состояние

```
platform-env.yaml (profiles:)  ←── authoritative source
        │
        ▼
.github/actions/compose-profiles/action.yml  ←── composite action (new)
        │
        ├── platform-test.yml  (вызов action вместо хардкода)
        └── push-gate.yml      (вызов action вместо хардкода)

Makefile → export COMPOSE_PROFILES ?= ...  (без изменений, локальный source of truth)
```

### Новый файл: `.github/actions/compose-profiles/action.yml`

```yaml
# GREP_SUMMARY: compose-profiles composite-action platform-env profiles csv env-export
# STRUCTURE: runs:composite → ○ yaml_query --get profiles --items → ⊕ paste -sd, → export GITHUB_ENV
# region MODULE_CONTRACT
## @purpose  Composite action: читает список профилей из platform-env.yaml и экспортирует
##           COMPOSE_PROFILES в $GITHUB_ENV для всех последующих шагов.
## @scope    Вызывается в CI workflows как setup-шаг. Делегирует в core/internal/scripts/yaml_query.py.
## @invariants
##   - Читает platform-env.yaml#profiles (authoritative source)
##   - Экспортирует COMPOSE_PROFILES как comma-separated string в $GITHUB_ENV
##   - Идемпотентен: повторный вызов перезаписывает то же значение
## @rationale  DevPlan 064 S1: устраняет 3× хардкод COMPOSE_PROFILES.
##             При добавлении нового модуля через generate-manifests список обновляется
##             автоматически во всех CI workflows без ручных правок.
# endregion MODULE_CONTRACT

name: 'Export COMPOSE_PROFILES'
description: 'Reads profiles from platform-env.yaml and exports COMPOSE_PROFILES env variable'

runs:
  using: 'composite'
  steps:
    - name: Export COMPOSE_PROFILES from platform-env.yaml
      shell: bash
      run: |
        PROFILES=$(python3 core/internal/scripts/yaml_query.py --file platform-env.yaml --get profiles --items | paste -sd, -)
        echo "COMPOSE_PROFILES=$PROFILES" >> "$GITHUB_ENV"
        echo "[IMP:9][compose-profiles] Exported COMPOSE_PROFILES=$PROFILES"
```

### Изменения в `platform-test.yml`

**Было (строка 67-71):**
```yaml
    env:
      INTEGRATION_MODE: live
      HERMES_DASHBOARD_PASSWORD: ${{ secrets.HERMES_DASHBOARD_PASSWORD }}
      COMPOSE_PROFILES: "postgres,redis,nginx,clickhouse,backup-cron,hermes-agent,monitoring,logging,litellm,langfuse,infra-metrics,minio,status-page"
```

**Стало (строка 67-69):**
```yaml
    env:
      INTEGRATION_MODE: live
      HERMES_DASHBOARD_PASSWORD: ${{ secrets.HERMES_DASHBOARD_PASSWORD }}
```

И добавить шаг после setup (после строки 99, перед pre-commit):
```yaml
      - name: Export COMPOSE_PROFILES from platform-env.yaml
        uses: ./.github/actions/compose-profiles
```

### Изменения в `push-gate.yml`

**Было (строки 41-47):**
```yaml
    # COMPOSE_PROFILES — required for ${VAR:?error} enforcement (DevPlan 033 W3-E3 Option A).
    # Without this, `docker compose config` validates inactive profiles that reference
    # critical secrets with ${VAR:?...} syntax (even though .env.example has test values,
    # this is a safety net for future secrets without test defaults).
    # Source of truth: platform-env.yaml profiles section.
    env:
      COMPOSE_PROFILES: "postgres,redis,nginx,clickhouse,backup-cron,hermes-agent,monitoring,logging,litellm,langfuse,infra-metrics,minio,status-page"
```

**Стало (строки 41-45 — комментарий сохранён, env удалён):**
```yaml
    # COMPOSE_PROFILES — exported by compose-profiles composite action (DevPlan 064 S1).
    # Reads from platform-env.yaml (authoritative source). Replaces hardcoded string.
    # Required for ${VAR:?error} enforcement (DevPlan 033 W3-E3 Option A).
```

И добавить шаг после setup (после строки 59):
```yaml
      - name: Export COMPOSE_PROFILES from platform-env.yaml
        uses: ./.github/actions/compose-profiles
```

### Что НЕ меняется

| Компонент | Статус | Причина |
|-----------|--------|---------|
| `Makefile:30` — `export COMPOSE_PROFILES ?= ...` | Без изменений | Локальный source of truth для `make`; не участвует в CI |
| `build-platform.yml` — `COMPOSE_PROFILES: hermes-agent` | Без изменений | Это scoped-значение для smoke-теста одного модуля, не полный список |
| `platform-env.yaml:176-189` | Без изменений | Уже авторитетный источник |

---

## Часть 2: S2 — Cleanup logic dedup (2 копии → 1 composite action)

### Контекст

Индентичная логика очистки Docker Compose стеков дублирована:

| Файл | Строки | Условие |
|------|--------|---------|
| `platform-test.yml` | 358-372 | `if: false` (debug mode) |
| `nightly-gate.yml` | 111-125 | `if: always()` |

Оба блока (22 строки каждый):
1. Итерируют `module_discovery.py --format lines`
2. Для каждого compose-файла: `docker compose -f $file -p ai-platform-test down --timeout 5 --remove-orphans`
3. И `-p ai-platform-integration-test` тоже

### Новый файл: `.github/actions/cleanup-docker/action.yml`

```yaml
# GREP_SUMMARY: cleanup-docker composite-action compose-down remove-orphans module-discovery
# STRUCTURE: inputs:project-names → runs:composite → ○ module_discovery --format lines → ⚡ docker compose down per project → ⎋ done
# region MODULE_CONTRACT
## @purpose  Composite action: останавливает и удаляет Docker Compose стеки для всех модулей.
##           Заменяет дублирующуюся cleanup-логику в platform-test.yml и nightly-gate.yml.
## @scope    Вызывается из CI workflow steps. Делегирует в core/internal/scripts/module_discovery.py.
## @invariants
##   - Итерирует все docker-модули через module_discovery.py --format lines
##   - Для каждого модуля: docker compose down --timeout 5 --remove-orphans для каждого project-name
##   - Никогда не фейлит сборку (|| true на каждом down)
##   - inputs.project-names: comma-separated, default "ai-platform-test,ai-platform-integration-test"
## @rationale  DevPlan 064 S2: устраняет 2× дублирование cleanup-логики.
##             При добавлении нового CI project-name — один input, не две копии кода.
# endregion MODULE_CONTRACT

name: 'Cleanup Docker Resources'
description: 'Stops and removes Docker Compose stacks for all discovered modules'

inputs:
  project-names:
    description: 'Comma-separated project names to clean up'
    required: false
    default: 'ai-platform-test,ai-platform-integration-test'

runs:
  using: 'composite'
  steps:
    - name: Cleanup Docker Compose stacks
      shell: bash
      run: |
        echo "::group::Cleanup Docker Compose stacks"
        PROJECTS="${{ inputs.project-names }}"
        IFS=',' read -ra PROJ_ARR <<< "$PROJECTS"
        if [ -f /tmp/module_list.json ]; then
          while IFS= read -r compose_file; do
            if [ -f "$compose_file" ]; then
              for proj in "${PROJ_ARR[@]}"; do
                proj_trimmed=$(echo "$proj" | xargs)
                docker compose -f "$compose_file" -p "$proj_trimmed" down --timeout 5 --remove-orphans 2>/dev/null || true
              done
            fi
          done < <(python3 core/internal/scripts/module_discovery.py --format lines)
        else
          echo "[cleanup] /tmp/module_list.json not found — skipping cleanup"
        fi
        echo "::endgroup::"
```

⚠️ **TRAP[COMPAT]:** Оригинальный код использовал `if [ -f /tmp/module_list.json ]` как guard, но затем читал через `module_discovery.py --format lines` (не из JSON). Новый код сохраняет эту логику: `/tmp/module_list.json` используется только как signal file (проверка что discover-modules выполнен), а список читается через модуль заново.

### Изменения в `platform-test.yml`

**Было (строки 358-372):**
```yaml
      - name: Cleanup Docker resources
        if: false
        run: |
          echo "::group::Cleanup Docker Compose stacks"
          if [ -f /tmp/module_list.json ]; then
            while IFS= read -r compose_file; do
              if [ -f "$compose_file" ]; then
                docker compose -f "$compose_file" -p ai-platform-test down --timeout 5 --remove-orphans 2>/dev/null || true
                docker compose -f "$compose_file" -p ai-platform-integration-test down --timeout 5 --remove-orphans 2>/dev/null || true
              fi
            done < <(python3 core/internal/scripts/module_discovery.py --format lines)
          else
            echo "[cleanup] Module list not found — skipping cleanup"
          fi
          echo "::endgroup::"
```

**Стало:**
```yaml
      - name: Cleanup Docker resources
        if: false
        uses: ./.github/actions/cleanup-docker
```

### Изменения в `nightly-gate.yml`

**Было (строки 111-125):**
```yaml
      - name: Cleanup Docker resources
        if: always()
        run: |
          echo "::group::Cleanup Docker Compose stacks"
          if [ -f /tmp/module_list.json ]; then
            ... (идентично platform-test.yml)
          fi
          echo "::endgroup::"
```

**Стало:**
```yaml
      - name: Cleanup Docker resources
        if: always()
        uses: ./.github/actions/cleanup-docker
```

---

## Часть 3: S3 — Module list generation (аудит существующего dedup)

### Контекст

`discover-modules` composite action (StatusReport 046 T2, CICD-01a) уже используется:

| Workflow | Строка | Статус |
|----------|--------|--------|
| `platform-test.yml` | 172-174 | ✅ `uses: ./.github/actions/discover-modules` |
| `nightly-gate.yml` | 106-108 | ✅ `uses: ./.github/actions/discover-modules` |

### Аудит

Inline-вызовы `python3 core/internal/scripts/module_discovery.py` напрямую (не через composite action) обнаружены в двух категориях:

| Категория | Файл:строка | Статус после S2 |
|-----------|-------------|-----------------|
| Cleanup-блоки | `platform-test.yml:368`, `nightly-gate.yml:121` | ✅ Заменены на `cleanup-docker` composite action |
| Pre-pull шаг | `platform-test.yml:181` | ⚠️ Остаётся (шаг отключён `if: false` — TRAP[DEBUG] от 2026-07-23) |

**После S2 инлайн-вызовы module_discovery.py останутся только в pre-pull шаге (if: false — не исполняется).**

⚠️ TRAP[DEBUG] · 2026-07-23 · pre-pull шаг (`platform-test.yml:181`) содержит inline-вызов `module_discovery.py`, но весь блок отключён `if: false` (TRAP[DEBUG] на строке 105). После снятия debug-блокировки pre-pull шаг должен быть мигрирован на composite action `discover-modules`.

### Действие

Проверить что после изменений S1+S2 прямые вызовы `module_discovery.py` в workflow остались только в отключённом pre-pull шаге:

```bash
grep -rn "module_discovery.py" .github/workflows/
# Ожидание: 1 результат — platform-test.yml:181 (pre-pull шаг, if: false)
# Cleanup-блоки (platform-test.yml:368, nightly-gate.yml:121) — заменены на composite action
```

---

## Часть 4: S4 — modules.mk compose-file dedup (4 копии → 1 переменная)

### Контекст

Блок разрешения compose-файлов дублирован в 4 таргетах `modules.mk`:

```
up:       строки 28-31
down:     строки 49-52
restart:  строки 59-62
status:   строки 69-72
```

Каждый содержит:
```bash
_compose_files="-f docker-compose.yml -f docker-compose.platform-dev.yml"; \
if [ "$$(uname)" = "Darwin" ] && [ -f docker-compose.macos.yml ]; then \
    _compose_files="$$_compose_files -f docker-compose.macos.yml"; \
fi; \
```

### Целевое состояние

Добавить переменную `COMPOSE_BASE_FILES` в корневой `Makefile`, вычисляемую на этапе парсинга:

```makefile
# === Docker Compose shared files (resolved at parse time, used by modules.mk) ===
COMPOSE_BASE_FILES := -f docker-compose.yml -f docker-compose.platform-dev.yml
ifeq ($(shell uname -s),Darwin)
    ifneq ($(wildcard docker-compose.macos.yml),)
        COMPOSE_BASE_FILES += -f docker-compose.macos.yml
    endif
endif
```

`ifeq`/`wildcard` — GNU Make функции, вычисляемые при парсинге Makefile. На macOS с присутствующим `docker-compose.macos.yml` переменная получит `-f docker-compose.macos.yml`. На Linux — нет. Результат детерминирован для данной платформы.

### Изменения в `modules.mk`

**Таргет `up` (строки 22-44):**

Было:
```makefile
up: discover-modules dev-certs
	@echo "[IMP:7][make][up] Starting platform stack..."
	@bash $(_platform_root)/core/internal/provision-environment.sh --scope networks --scope volumes \
		--platform-env $(_platform_root)/platform-env.yaml
	@_compose_files="-f docker-compose.yml -f docker-compose.platform-dev.yml"; \
	if [ "$$(uname)" = "Darwin" ] && [ -f docker-compose.macos.yml ]; then \
		_compose_files="$$_compose_files -f docker-compose.macos.yml"; \
		echo "[IMP:7][make][up] macOS detected — including docker-compose.macos.yml"; \
	fi; \
	if [ -n "$(MODULES)" ]; then \
		_profiles=""; \
		IFS=',' read -ra _mods <<< "$(MODULES)"; \
		for _m in "$${_mods[@]}"; do \
			_profiles="$$_profiles --profile $$_m"; \
		done; \
		echo "[IMP:7][make][up] Using profiles: $(MODULES)"; \
		docker compose $$_compose_files $$_profiles up -d; \
	else \
		docker compose $$_compose_files up -d; \
	fi
	@echo "[IMP:9][make][up] Platform stack started"
```

Стало:
```makefile
up: discover-modules dev-certs
	@echo "[IMP:7][make][up] Starting platform stack..."
	@bash $(_platform_root)/core/internal/provision-environment.sh --scope networks --scope volumes \
		--platform-env $(_platform_root)/platform-env.yaml
	@if [ -n "$(MODULES)" ]; then \
		_profiles=""; \
		IFS=',' read -ra _mods <<< "$(MODULES)"; \
		for _m in "$${_mods[@]}"; do \
			_profiles="$$_profiles --profile $$_m"; \
		done; \
		echo "[IMP:7][make][up] Using profiles: $(MODULES)"; \
		docker compose $(COMPOSE_BASE_FILES) $$_profiles up -d; \
	else \
		docker compose $(COMPOSE_BASE_FILES) up -d; \
	fi
	@echo "[IMP:9][make][up] Platform stack started"
```

**Таргет `down` (строки 47-54):**

Было:
```makefile
down:
	@echo "[IMP:7][make][down] Stopping platform stack..."
	@_compose_files="-f docker-compose.yml -f docker-compose.platform-dev.yml"; \
	if [ "$$(uname)" = "Darwin" ] && [ -f docker-compose.macos.yml ]; then \
		_compose_files="$$_compose_files -f docker-compose.macos.yml"; \
	fi; \
	docker compose $$_compose_files down -v
	@echo "[IMP:9][make][down] Platform stack stopped"
```

Стало:
```makefile
down:
	@echo "[IMP:7][make][down] Stopping platform stack..."
	@docker compose $(COMPOSE_BASE_FILES) down -v
	@echo "[IMP:9][make][down] Platform stack stopped"
```

**Таргет `restart` (строки 57-64):**

Было:
```makefile
restart:
	@echo "[IMP:7][make][restart] Soft restarting all services..."
	@_compose_files="-f docker-compose.yml -f docker-compose.platform-dev.yml"; \
	if [ "$$(uname)" = "Darwin" ] && [ -f docker-compose.macos.yml ]; then \
		_compose_files="$$_compose_files -f docker-compose.macos.yml"; \
	fi; \
	docker compose $$_compose_files stop && docker compose $$_compose_files start
	@echo "[IMP:9][make][restart] All services soft restarted"
```

Стало:
```makefile
restart:
	@echo "[IMP:7][make][restart] Soft restarting all services..."
	@docker compose $(COMPOSE_BASE_FILES) stop && docker compose $(COMPOSE_BASE_FILES) start
	@echo "[IMP:9][make][restart] All services soft restarted"
```

**Таргет `status` (строки 67-74):**

Было:
```makefile
status:
	@echo "[IMP:7][make][status] Displaying running services..."
	@_compose_files="-f docker-compose.yml -f docker-compose.platform-dev.yml"; \
	if [ "$$(uname)" = "Darwin" ] && [ -f docker-compose.macos.yml ]; then \
		_compose_files="$$_compose_files -f docker-compose.macos.yml"; \
	fi; \
	docker compose $$_compose_files ps
	@echo "[IMP:9][make][status] Status displayed"
```

Стало:
```makefile
status:
	@echo "[IMP:7][make][status] Displaying running services..."
	@docker compose $(COMPOSE_BASE_FILES) ps
	@echo "[IMP:9][make][status] Status displayed"
```

### Итог: modules.mk — 111 → 89 строк (-22 строки, -20%)

---

## Часть 5: S5 — AGENTS.md удаление устаревших LOC-цифр

### Контекст

`core/internal/bootstrap/AGENTS.md` содержит таблицы с LOC-цифрами модулей (строки 140-167), зафиксированные на момент Wave 4. Фактические значения разошлись:

| Модуль | AGENTS.md | Факт | Δ |
|--------|-----------|------|-----|
| `docker_orchestrator.py` | 1155 | 1312 | +157 |
| `reconciler.py` | 2136 | 2284 | +148 |
| `state_machine.py` | 1599 | 2086 | +487 |
| `steps.py` | 729 | 994 | +265 |

Суммарная секция `deploy/` (~2220) фактически ~2600, `converge/` (1367→2284), `lifecycle/` (2330→3080).

Проект активно переписывается (Strangler-Fig), и поддержание ручных LOC-цифр не имеет ценности.

### Изменения в `core/internal/bootstrap/AGENTS.md`

**Удалить строки 136-168** — секция `## Python-модули декомпозиции (Wave 4 — Strangler-Fig)` с таблицами LOC.

**Сохранить без изменений:**
- Строки 169-211: Mermaid-диаграмма Lifecycle State Machine (W5-E6)
- Строки 213-220: Shell-фасады сводка (исторический achievement — 90% сокращение, цифры верифицированы)
- Строки 224-234: Unit-тесты список

**После удаления** секция `### Lifecycle State Machine (W5-E6)` (mermaid) станет прямым продолжением раздела `## Артефакты`. Структурно логично — диаграмма документирует архитектуру state machine, а не LOC-статистику.

---

## Файловый манифест

| Файл | Действие | Строк |
|------|----------|-------|
| `.github/actions/compose-profiles/action.yml` | CREATE | ~30 |
| `.github/actions/cleanup-docker/action.yml` | CREATE | ~55 |
| `.github/workflows/platform-test.yml` | MODIFY: убрать хардкод COMPOSE_PROFILES, добавить compose-profiles шаг, заменить cleanup на composite action | ~8 строк изменений, -22 строки удалено |
| `.github/workflows/push-gate.yml` | MODIFY: убрать хардкод COMPOSE_PROFILES, добавить compose-profiles шаг | ~5 строк изменений, -7 строк удалено |
| `.github/workflows/nightly-gate.yml` | MODIFY: заменить cleanup на composite action | ~3 строки изменений, -16 строк удалено |
| `Makefile` | MODIFY: добавить COMPOSE_BASE_FILES после строки 30 | +6 строк |
| `makefiles/modules.mk` | MODIFY: заменить 4 compose-file блока на $(COMPOSE_BASE_FILES) | -22 строки |
| `core/internal/bootstrap/AGENTS.md` | MODIFY: удалить строки 136-168 | -33 строки |

**Всего: 8 файлов (2 новых, 6 изменённых), ~-80 строк net.**

---

## Пошаговый план реализации

### Step 1: Создать composite action compose-profiles
- [ ] Создать `.github/actions/compose-profiles/action.yml`
- [ ] Реализовать чтение `platform-env.yaml` через `yaml_query.py --get profiles --items | paste -sd, -`
- [ ] Экспорт в `$GITHUB_ENV`

### Step 2: Создать composite action cleanup-docker
- [ ] Создать `.github/actions/cleanup-docker/action.yml`
- [ ] Реализовать итерацию по `module_discovery.py --format lines`
- [ ] Поддержать `inputs.project-names` (comma-separated)

### Step 3: Обновить platform-test.yml
- [ ] Убрать `COMPOSE_PROFILES` из job-level `env:` (строка 71)
- [ ] Добавить шаг `Export COMPOSE_PROFILES` → `uses: ./.github/actions/compose-profiles` после setup
- [ ] Заменить cleanup-шаг (строки 358-372) на `uses: ./.github/actions/cleanup-docker`

### Step 4: Обновить push-gate.yml
- [ ] Убрать `COMPOSE_PROFILES` из job-level `env:` (строка 47)
- [ ] Добавить шаг `Export COMPOSE_PROFILES` → `uses: ./.github/actions/compose-profiles` после setup
- [ ] Обновить комментарий (строки 41-45)

### Step 5: Обновить nightly-gate.yml
- [ ] Заменить cleanup-шаг (строки 111-125) на `uses: ./.github/actions/cleanup-docker`

### Step 6: Добавить COMPOSE_BASE_FILES в Makefile
- [ ] Добавить блок после строки 30 (`export COMPOSE_PROFILES`)
- [ ] `ifeq ($(shell uname -s),Darwin)` + `wildcard` для macos-файла

### Step 7: Обновить modules.mk
- [ ] `up`: заменить compose-file блок на `$(COMPOSE_BASE_FILES)`
- [ ] `down`: заменить на одну строку `docker compose $(COMPOSE_BASE_FILES) down -v`
- [ ] `restart`: заменить на `$(COMPOSE_BASE_FILES)`
- [ ] `status`: заменить на `$(COMPOSE_BASE_FILES)`

### Step 8: Удалить LOC-блок из AGENTS.md
- [ ] Удалить строки 136-168 из `core/internal/bootstrap/AGENTS.md`

### Step 9: Локальная верификация
- [ ] `make gate MODE=fast` — зелёный
- [ ] `make check-manifests` — зелёный (новые composite actions не затрагивают generated files)
- [ ] `make up` + `make status` + `make restart` + `make down` — все 4 таргета работают идентично
- [ ] Проверить: `grep -rn "module_discovery.py" .github/workflows/` — 0 результатов

### Step 10: CI верификация
- [ ] Push в feature-ветку → push-gate зелёный, `COMPOSE_PROFILES` экспортирован из platform-env.yaml
- [ ] PR в main → platform-test зелёный (после восстановления `if: false`)

---

## Верификация

```bash
# 1. COMPOSE_PROFILES идентичность (order-independent — порядок в Makefile ≠ platform-env.yaml алфавитный, но docker compose не зависит от порядка)
diff <(make _get_all_profiles | tr ',' '\n' | sort) <(python3 core/internal/scripts/yaml_query.py --file platform-env.yaml --get profiles --items | sort)
# Ожидание: no diff (13 одинаковых модулей, порядок игнорируется)

# 2. modules.mk — все 4 таргета работают
make up && make status && make restart && make down
# Ожидание: все 4 выполняются без ошибок

# 3. Fast gate зелёный
make gate MODE=fast
# Ожидание: PASS

# 4. Манифесты актуальны
make check-manifests
# Ожидание: PASS

# 5. AGENTS.md не содержит устаревших LOC
grep -c "| 1155" core/internal/bootstrap/AGENTS.md
# Ожидание: 0

# 6. No inline module_discovery in workflows
grep -rn "module_discovery.py" .github/workflows/
# Ожидание: 1 результат — platform-test.yml:181 (pre-pull шаг, if: false)
# Cleanup-блоки (platform-test.yml:368, nightly-gate.yml:121) заменены на composite action cleanup-docker

# 7. Composite actions валидны
yamllint .github/actions/compose-profiles/action.yml
yamllint .github/actions/cleanup-docker/action.yml
# Ожидание: PASS
```

---

## Откат

```bash
git revert <merge-commit>
```

Изменения изолированы в 8 файлах (2 новых composite action, 6 модифицированных). Не затрагивают production-код, тесты, или инфраструктуру. Откат тривиален:

1. Удалить `.github/actions/compose-profiles/` и `.github/actions/cleanup-docker/`
2. Восстановить хардкод `COMPOSE_PROFILES` в platform-test.yml и push-gate.yml
3. Восстановить inline cleanup в platform-test.yml и nightly-gate.yml
4. Восстановить compose-file блоки в modules.mk
5. Восстановить LOC-таблицы в AGENTS.md

$END_DEVPLAN
