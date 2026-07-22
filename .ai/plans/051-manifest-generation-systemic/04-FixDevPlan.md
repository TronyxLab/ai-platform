# FixDevPlan 051 — Manifest Generation: устранение drift findings из VerificationReport

$START_DEVPLAN

$ARTIFACT_CONTRACT
PURPOSE:               Устранение 6 findings из VerificationReport 051 (семантический вердикт DRIFTED/CRITICAL). Две критические дыры: отсутствие CI gate enforcement и невалидный gmake path на macOS. Два HIGH: неполный check-manifests и отсутствие @scope в сгенерированном YAML.
DESCRIPTION:           Single-wave fix — 4 файла, 6 правок. CRITICAL: добавить CI шаг check-manifests, исправить gmake path. HIGH: расширить check-manifests до 6 файлов, добавить @scope в generate_secrets_manifest.py. MEDIUM/LOW: pin nginx версию в test-data, документировать platform-secrets исключение.
RATIONALE:             Реализация DevPlan 051 core (генераторы, тесты, YAML) — solid. Но без CI enforcement инвариант 11 («CI gate блокирует divergence») не выполняется — drift может накапливаться между запусками. gmake path блокирует локальный запуск на macOS.
ACCEPTANCE_CRITERIA:
  AC-F1: `make check-manifests` шаг присутствует в `.github/workflows/platform-test.yml` (после fast gate, до Docker setup)
  AC-F2: `make generate-manifests` работает на macOS без `/opt/homebrew/bin/gmake` (использует `which gmake || which make` или grep fallback)
  AC-F3: `make check-manifests` проверяет все 6 generated файлов (secrets-manifest.yaml, platform-env.yaml, smoke_env_generated.py, env_defaults_generated.py, entrypoint-manifest.yaml, core/AGENTS.md)
  AC-F4: `core/secrets-manifest.yaml` содержит `## @scope` в MODULE_CONTRACT после генерации
  AC-F5: `make generate-manifests && make check-manifests` → exit 0 на чистом дереве
  AC-F6: `make gate MODE=fast` green (статические тесты)
IMPLEMENTS:            VerificationReport 051 §Recommendations — Must Fix + Should Fix
IMPACTS:
  ## Модифицируемые (4)
  - Makefile (root) — строка 60 (gmake path), строки 73 (check-manifests file list)
  - .github/workflows/platform-test.yml — новый CI step после fast gate
  - core/internal/scripts/generate_secrets_manifest.py — строка 325-338 (добавить ## @scope)
  - core/modules/AGENTS.md — документировать исключение platform-secrets (system module)
REQUIRES:
  - Python ≥3.10
  - git ≥2.30
$END_ARTIFACT_CONTRACT

---

## TASK-1 (CRITICAL): CI step `make check-manifests` — DRIFT-CI-001

**Файл:** `.github/workflows/platform-test.yml`

**Позиция:** после шага `Run fast gate` (строка 116), перед `Stage 2: Docker setup`. `check-manifests` не требует Docker — fast path.

**Правка — добавить новый step:**
```yaml
      # Manifest generation contract — блокирует push если generated files diverged
      - name: Check generated manifests up to date
        run: make check-manifests
```

---

## TASK-2 (CRITICAL): gmake path — DRIFT-GMAKE-001

**Файл:** `Makefile`, строка 60

**Проблема:** `/opt/homebrew/bin/gmake` не существует на macOS. Нужен auto-detect.

**Правка — заменить строку 60:**
```makefile
# Было:
		--gmake-path /opt/homebrew/bin/gmake \

# Стало:
		--gmake-path $(shell which gmake 2>/dev/null || which make 2>/dev/null || echo make) \
```

**Контекст:** `generate_entrypoint_manifest.py` уже имеет grep fallback если gmake недоступен. Но лучше дать правильный путь. `which make` на macOS даст `/usr/bin/make` (BSD make), который не поддерживает `-np`. Но Python-скрипт имеет fallback на grep, так что это безопасно.

---

## TASK-3 (HIGH): check-manifests incomplete — DRIFT-CHECK-001

**Файл:** `Makefile`, строка 73

**Проблема:** `git diff --exit-code` проверяет только 4/6 generated файлов.

**Правка — заменить строку 73:**
```makefile
# Было:
	@git diff --exit-code -- core/secrets-manifest.yaml platform-env.yaml tests/_conftest/smoke_env_generated.py tests/helpers/env_defaults_generated.py || \

# Стало:
	@git diff --exit-code -- core/secrets-manifest.yaml platform-env.yaml \
		tests/_conftest/smoke_env_generated.py tests/helpers/env_defaults_generated.py \
		core/entrypoint-manifest.yaml core/AGENTS.md || \
```

---

## TASK-4 (LOW): @scope missing in generated secrets-manifest.yaml — STATIC-SCOPE

**Файл:** `core/internal/scripts/generate_secrets_manifest.py`, строки 325-338

**Проблема:** Сгенерированный MODULE_CONTRACT header не содержит `## @scope`.

**Правка:** добавить `## @scope` после `## @purpose` строки (после строки 330).

```python
# После строки 330 (## @purpose), добавить:
"## @scope    Auto-generated from core/secret-definitions.yaml + module.yaml consumers.\n"
"##           Consumed by CI gates, deploy-modules.sh, secrets-init.sh.\n"
```

---

## TASK-5 (MEDIUM): nginx version drift in test-data — DRIFT-NGINX-001

**Файл:** `tests/test_data/projects/*/docker-compose.yml` — nginx image tag.

**Проблема:** test-data использует `stable-alpine` (floating tag), production — `1.28-alpine` (pinned digest).

**Правка:** заменить `nginx:stable-alpine` → `nginx:1.28-alpine` во всех test-data docker-compose файлах. Это не production — только test fixtures.

---

## TASK-6 (LOW): platform-secrets contract exception — CONTRACT-PLATFORM-SECRETS

**Файл:** `core/modules/AGENTS.md`

**Проблема:** `platform-secrets` — system module (install_type: system, systemd service) — не требует `docker-compose.base.yml`. Контракт модуля этого не документирует.

**Правка:** добавить в `core/modules/AGENTS.md` (в секцию о `docker-compose.base.yml` обязательности):
```markdown
**Исключение:** модули с `install_type: system` (например, `platform-secrets`) — systemd-сервисы, не Docker-контейнеры — не требуют `docker-compose.base.yml`.
```

---

## Порядок выполнения

```
TASK-2 (gmake) ─┐
TASK-3 (check)  ─┤ все независимы → параллельно
TASK-4 (scope)  ─┤
TASK-6 (docs)   ─┘
    │
    ▼
TASK-1 (CI step) — после остальных, т.к. проверяет результат
    │
    ▼
TASK-5 (nginx) — косметический, last
```

## Верификация

```bash
# 1. Генерация (с новым gmake path)
make generate-manifests

# 2. Проверка (с новым списком файлов)
make check-manifests
# exit 0 expected

# 3. Fast gate
make gate MODE=fast
# green expected

# 4. Проверить @scope в сгенерированном файле
grep '@scope' core/secrets-manifest.yaml
# должен найти строку
```

$END_DEVPLAN
