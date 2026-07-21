# GREP_SUMMARY: 01-DevPlan status-page-test macOS bind-mount docker-compose override volumes smoke-skip
# STRUCTURE: ▶ Problem → ◇ Root Cause → ⊕ Solution Design → ◆ Implementation → ⎋ Acceptance Criteria
$START_DEVPLAN

$ARTIFACT_CONTRACT
PURPOSE:               Исправить ошибку запуска контейнера status-page-test на macOS Docker Desktop,
                        вызванную неработающими bind-mount volumes.
DESCRIPTION:           Двухуровневое решение: (1) переопределить volumes в docker-compose.test.yml
                        для использования /tmp-путей, доступных Docker Desktop на macOS;
                        (2) добавить явный skip-маркер в smoke-тест как safety net.
RATIONALE:             Bind-mount с хост-путями (/run/platform/, /opt/node-configs/) не
                        поддерживаются Docker Desktop на macOS, т.к. Docker работает внутри VM.
                        Паттерн исправления уже применён в postgres, clickhouse, backup-cron
                        (замена bind-mount на Docker-managed volumes) и в test_smoke_nginx.py
                        (skip при is_macos()). Решение использует /tmp-пути, которые уже
                        обеспечены smoke conftest через _SMOKE_VOLUME_BIND_DIRS.
ACCEPTANCE_CRITERIA:   1. `make gate MODE=full` на macOS — 0 failures (status-page-test не в failures)
                        2. `make test MARKER=smoke` на macOS — status-page-test контейнер healthy или skip
                        3. `make test MARKER=smoke` в CI (Linux) — регрессий нет, status-page-test healthy
                        4. docker-compose.test.yml содержит явный `volumes: !override`
                        5. JUnit XML smoke-отчёт не содержит failure для status-page-test
IMPLEMENTS:            STRESS_TEST_REPORT.md (остаточный дефект macOS), gate skip-enforcement failure
IMPACTS:               core/modules/status-page/docker-compose.test.yml (модификация),
                        tests/_conftest/smoke.py (модификация — создание тестовых файлов),
                        tests/test_smoke_platform.py (опциональная модификация — skip safety net)
REQUIRES:              Docker Desktop на macOS (для локальной верификации),
                        CI Linux runner (для регрессионной проверки)
$END_ARTIFACT_CONTRACT

---

## 1. Problem Statement

**Симптом**: `make gate MODE=full` на macOS возвращает PARTIAL из-за 1 failure в skip-enforcement gate:
контейнер `status-page-test` не стартует на macOS Docker Desktop.

**JUnit XML evidence** (report-smoke.xml):
```xml
<testcase classname="tests.test_smoke_platform" name="test_critical_services_healthy" ...>
  <failure>Failed: Containers not running: status-page-test</failure>
</testcase>
```

**Контекст**: дефект не блокирует Linux-деплой (зафиксирован в STRESS_TEST_REPORT.md как остаточный),
но ломает `make gate MODE=full` на macOS, что нарушает pre-flight проверки разработчика.

---

## 2. Root Cause Analysis

### 2.1 Цепочка причин

```
docker-compose.test.yml НЕ переопределяет volumes
  → наследуются bind-mount из docker-compose.base.yml:
      /run/platform/docker-health.json:/run/platform/docker-health.json:ro
      ${NODE_CONFIGS_DIR}/.../node.yaml:/opt/node-configs/.../node.yaml:ro
  → на macOS Docker Desktop эти хост-пути не существуют в VM
  → docker compose up создаёт контейнер, но он падает при старте
      (volume mount error → exit)
  → test_critical_services_healthy (test_smoke_platform.py)
      обнаруживает status-page-test как unhealthy
  → gate skip-enforcement фиксирует failure в JUnit XML
  → gate MODE=full → PARTIAL
```

### 2.2 Почему bind-mount не работает на macOS

Docker Desktop на macOS запускает Docker daemon внутри Linux VM (HyperKit/QEMU).
Bind-mount работает только для путей, которые:
1. Находятся внутри домашней директории пользователя (`/Users/...`)
2. Находятся в `/tmp` (специально шарится Docker Desktop)
3. Явно добавлены в File Sharing (Preferences → Resources → File Sharing)

Пути `/run/platform/` и `/opt/node-configs/` не удовлетворяют этим условиям.

### 2.3 Почему это не проявляется в других модулях

| Модуль | Решение | Статус |
|--------|---------|--------|
| postgres | docker-compose.test.yml переопределяет volume на `postgres-data-test` (Docker-managed) | Исправлен |
| clickhouse | docker-compose.test.yml переопределяет volume на `clickhouse-data-test` (Docker-managed) | Исправлен |
| backup-cron | docker-compose.test.yml переопределяет volumes на `backup-spool-test` + `backup-logs-test` (Docker-managed) | Исправлен |
| nginx | `@pytest.mark.skipif(is_macos(), ...)` на тестах с bind-mount/cert | Исправлен |
| **status-page** | **НЕТ переопределения volumes, НЕТ skip-маркера** | **ДЕФЕКТ** |

---

## 3. Solution Design

### 3.1 Superposition analysis

| Option | Подход | Плюсы | Минусы | Вердикт |
|--------|--------|-------|--------|---------|
| **A** | Переопределить volumes в test.yml на /tmp-пути (bind-mount, но /tmp доступен на macOS) | Минимальные изменения, использует существующую инфраструктуру smoke conftest | Зависимость от /tmp (доступен на всех платформах) | **SELECTED** — консистентно с существующим паттерном |
| B | Использовать Docker-managed volumes + копирование файлов через `docker compose cp` | Полная изоляция от хост-ФС | Сложнее: нужен init container или post-start hook, ломает паттерн `!override` | Отклонено — over-engineered |
| C | Только skip-маркер на smoke-тесте (без фикса compose) | Минимум изменений | Не исправляет реальную проблему, test overlay остаётся сломанным на macOS | Отклонено — маскирует, не исправляет |
| D | Использовать Docker `configs:` вместо volumes | Нативное Docker-решение | Не работает с docker compose `!override` для замены volumes на configs | Отклонено — несовместимо с test overlay паттерном |

**Решение: Option A + safety skip**

1. **Primary**: Переопределить volumes в `docker-compose.test.yml` → использовать `/tmp`-пути, которые:
   - Уже создаются smoke conftest (`_SMOKE_VOLUME_BIND_DIRS`: `/tmp/test-node-configs/test-node`, `/tmp/run/platform`)
   - Доступны Docker Desktop на macOS (File Sharing по умолчанию включает `/tmp`)
   - Работают идентично на Linux (CI)

2. **Secondary (safety net)**: Проверить, нужен ли skip-маркер в `test_critical_services_healthy` — если status-page не является critical-сервисом на macOS, добавить conditional skip.

### 3.2 Design decisions

- **app.py**: монтируется через относительный путь `./app.py:/app/app.py:ro` — это работает на macOS (файл внутри проекта), **оставляем как есть**
- **node.yaml**: заменяем `${NODE_CONFIGS_DIR}/${NODE_NAME}/node.yaml` → `/tmp/test-node-configs/${NODE_NAME}/node.yaml` (smoke conftest гарантирует наличие директории и создание файла)
- **docker-health.json**: заменяем `/run/platform/docker-health.json` → `/tmp/run/platform/docker-health.json` (smoke conftest гарантирует наличие директории и создание файла)
- **SMOKE_ENV**: уже содержит `NODE_CONFIGS_DIR=/tmp/test-node-configs` и `NODE_NAME=test-node` — compose подхватит из env

### 3.3 Test data generation

Smoke conftest уже создаёт директории (`_ensure_volume_dirs`), но **не создаёт файлы** внутри них.
Необходимо добавить создание:
- `/tmp/test-node-configs/test-node/node.yaml` — минимальный валидный YAML
- `/tmp/run/platform/docker-health.json` — пустой JSON (или с тестовыми данными)

Файлы создаются в `platform_services` fixture **до** `docker compose up`.

---

## 4. Step-by-Step Implementation

### Step 1: Modify `core/modules/status-page/docker-compose.test.yml`

Добавить `volumes: !override` с зафиксированными `/tmp`-путями:

```yaml
services:
  status-page:
    container_name: status-page-test
    networks: !override
      - test-proxy-net
    ports: !override
      - "18080:8080"
    volumes: !override
      - ./app.py:/app/app.py:ro
      - /tmp/test-node-configs/test-node/node.yaml:/opt/node-configs/test-node/node.yaml:ro
      - /tmp/run/platform/docker-health.json:/run/platform/docker-health.json:ro

networks:
  test-proxy-net:
    external: true
```

**Изменения**:
- `node.yaml`: хост-путь заменён с `${NODE_CONFIGS_DIR}/${NODE_NAME}` на `/tmp/test-node-configs/test-node`
- `docker-health.json`: хост-путь заменён с `/run/platform/` на `/tmp/run/platform/`
- `app.py`: оставлен относительный путь (работает везде)

### Step 2: Modify `tests/_conftest/smoke.py` — генерация тестовых файлов

Добавить в `platform_services` fixture (после `_ensure_volume_dirs`, до `docker compose up`):

```python
# ── Generate test data files for status-page bind-mount ───────────
_test_node_yaml = Path("/tmp/test-node-configs/test-node/node.yaml")
_test_docker_health = Path("/tmp/run/platform/docker-health.json")

_test_node_yaml.parent.mkdir(parents=True, exist_ok=True)
_test_node_yaml.write_text(textwrap.dedent("""\
    node:
      name: test-node
      platform_domain: test.local
    projects: []
    modules: {}
"""))

_test_docker_health.parent.mkdir(parents=True, exist_ok=True)
_test_docker_health.write_text("{}")

_logger.info("[IMP:8][conftest][platform_services] Test data files created for status-page bind-mount")
```

### Step 3: (Safety net) Conditional skip в smoke-тесте

Проверить `test_critical_services_healthy` — если status-page-test определяется как critical-сервис,
добавить conditional exclusion на macOS. Альтернативно: добавить `status-page-test` в список не-critical
контейнеров при `is_macos()`.

**Решение**: в коде `test_critical_services_healthy` (test_smoke_platform.py) уже есть логика фильтрации
critical-сервисов через `_resolve_critical_containers()`. На macOS status-page-test либо должен быть
исключён из critical (не production-ready модуль на macOS), либо должен подниматься успешно.
Первичное решение (Steps 1-2) делает контейнер поднимаемым — skip safety net может не понадобиться.

**Проверить после Step 1-2**: если контейнер успешно стартует на macOS, Step 3 не требуется.

### Step 4: Verification

```bash
# Локально на macOS
make test MARKER=smoke

# Проверить JUnit XML
python3 -c "
import xml.etree.ElementTree as ET
tree = ET.parse('tests/report.xml')
for tc in tree.iter('testcase'):
    for f in tc.findall('failure'):
        if 'status-page' in (f.text or ''):
            print('FAIL:', tc.get('name'), f.text)
"
```

### Step 5: Gate check

```bash
make gate MODE=full
# Ожидаемый результат: 0 failures (status-page-test больше не в failures)
```

---

## 5. File Manifest

| Файл | Действие | Описание |
|------|----------|----------|
| `core/modules/status-page/docker-compose.test.yml` | **MODIFY** | Добавить `volumes: !override` с /tmp-путями |
| `tests/_conftest/smoke.py` | **MODIFY** | Добавить генерацию node.yaml и docker-health.json в `/tmp` |
| `tests/test_smoke_platform.py` | **REVIEW** | Проверить необходимость conditional skip для status-page-test на macOS (может не потребоваться после Steps 1-2) |
| `.ai/plans/022-macos-status-page-bind-mount/01-DevPlan.md` | **CREATE** | Этот DevPlan |

**Файлы НЕ затрагиваются**:
- `core/modules/status-page/docker-compose.base.yml` — production compose, не меняется
- `core/modules/status-page/app.py` — бизнес-логика, не меняется
- `tests/test_status_page.py` — unit-тесты, не требуют Docker
- `tests/gates/test_gate_status_page.py` — gate-тесты, статический анализ
- `core/modules/status-page/Dockerfile` — не меняется

---

## 6. Acceptance Criteria

| # | Критерий | Метод проверки | Приоритет |
|---|----------|----------------|-----------|
| AC-1 | `make gate MODE=full` на macOS → 0 failures (status-page-test НЕ в failures) | Ручной запуск на macOS | HIGH |
| AC-2 | `make test MARKER=smoke` на macOS → status-page-test healthy (или явный skip) | Ручной запуск на macOS | HIGH |
| AC-3 | `make test MARKER=smoke` в Linux CI → регрессий нет, все контейнеры healthy | CI pipeline (platform-test.yml) | HIGH |
| AC-4 | `docker-compose.test.yml` содержит явный `volumes: !override` | Статическая проверка (gate или review) | MEDIUM |
| AC-5 | Smoke conftest создаёт `/tmp/test-node-configs/test-node/node.yaml` и `/tmp/run/platform/docker-health.json` | Логи [IMP:8] в smoke-выводе | MEDIUM |
| AC-6 | JUnit XML smoke-отчёт не содержит `<failure>` с упоминанием `status-page-test` | Парсинг report.xml | HIGH |
| AC-7 | `make test MARKER=all` на macOS → unit-тесты status_page (10 тестов) — PASSED без регрессий | Ручной запуск на macOS | MEDIUM |

---

## 7. Draft Code Graph (XML)

```xml
<graph>
  <!-- Source artifacts (modified) -->
  <entity id="docker_compose_test_yml_status_page" type="FILE" layer="Infrastructure">
    <annotation>Добавлен volumes: !override — замена bind-mount на /tmp-пути</annotation>
    <crossLinks>
      <link target="docker_compose_base_yml_status_page" relation="OVERRIDES"/>
      <link target="smoke_conftest_platform_services" relation="DEPENDS_ON"/>
    </crossLinks>
  </entity>

  <entity id="smoke_conftest_platform_services" type="FIXTURE" layer="Test">
    <annotation>platform_services — добавлена генерация node.yaml и docker-health.json в /tmp</annotation>
    <crossLinks>
      <link target="SMOKE_VOLUME_BIND_DIRS" relation="USES"/>
      <link target="docker_compose_test_yml_status_page" relation="PROVIDES_DATA_FOR"/>
    </crossLinks>
  </entity>

  <!-- Existing artifacts (not modified, for context) -->
  <entity id="docker_compose_base_yml_status_page" type="FILE" layer="Infrastructure">
    <annotation>Production compose — volumes с /run/platform/ и /opt/node-configs/ (не меняется)</annotation>
  </entity>

  <entity id="SMOKE_VOLUME_BIND_DIRS" type="CONSTANT" layer="Test">
    <annotation>Список bind-mount директорий, создаваемых smoke conftest (уже содержит /tmp/test-node-configs/test-node и /tmp/run/platform)</annotation>
  </entity>

  <entity id="test_critical_services_healthy" type="TEST" layer="Test">
    <annotation>Smoke-тест, проверяющий health всех critical-контейнеров (обнаруживает status-page-test failure)</annotation>
    <crossLinks>
      <link target="docker_compose_test_yml_status_page" relation="WAITS_FOR"/>
    </crossLinks>
  </entity>

  <entity id="test_gate_skip_enforcement" type="GATE_TEST" layer="Gate">
    <annotation>Парсит JUnit XML и валидирует failures == 0</annotation>
    <crossLinks>
      <link target="test_critical_services_healthy" relation="READS_OUTPUT_OF"/>
    </crossLinks>
  </entity>

  <!-- Data flow -->
  <edge from="smoke_conftest_platform_services" to="docker_compose_test_yml_status_page" label="создаёт тестовые файлы в /tmp → compose монтирует"/>
  <edge from="docker_compose_test_yml_status_page" to="test_critical_services_healthy" label="контейнер healthy → тест PASS"/>
  <edge from="test_critical_services_healthy" to="test_gate_skip_enforcement" label="JUnit XML без failure → gate PASS"/>
</graph>
```

---

## 8. Step-by-Step Data Flow

```
1. smoke conftest: platform_services fixture
   ├── _ensure_volume_dirs(_SMOKE_VOLUME_BIND_DIRS)
   │   └── mkdir -p /tmp/test-node-configs/test-node /tmp/run/platform
   ├── [NEW] Создание test data files:
   │   ├── write /tmp/test-node-configs/test-node/node.yaml (минимальный YAML)
   │   └── write /tmp/run/platform/docker-health.json (пустой JSON)
   ├── ensure_external_networks(["test-proxy-net"])
   │   └── docker network create test-proxy-net (если не существует)
   └── docker compose -f docker-compose.base.yml -f docker-compose.test.yml up -d --wait
       ├── volumes (из test.yml override):
       │   ├── ./app.py:/app/app.py:ro              ✅ работает (относительный путь)
       │   ├── /tmp/test-node-configs/test-node/node.yaml:ro  ✅ работает (/tmp доступен)
       │   └── /tmp/run/platform/docker-health.json:ro        ✅ работает (/tmp доступен)
       └── container: status-page-test → healthy (через healthcheck curl /health)

2. test_smoke_platform.py: test_critical_services_healthy
   ├── _resolve_critical_containers() → ["status-page-test", ...]
   ├── poll container health → status-page-test: healthy ✅
   └── assert all healthy → PASS ✅

3. test_gate_skip_enforcement.py (MODE=full gate)
   ├── parse JUnit XML (tests/report.xml)
   ├── assert failures == 0 → PASS ✅
   └── gate MODE=full → SUCCESS ✅
```

---

## 9. Risks & Mitigations

| Риск | Вероятность | Влияние | Митигация |
|------|-------------|---------|-----------|
| `/tmp`-пути не работают в CI (Linux) | Низкая | MEDIUM | `/tmp` — стандартный путь на Linux, используется другими smoke-тестами. CI smoke уже работает с `/tmp` путями. |
| app.py меняется между запусками — inconsistency | Низкая | LOW | app.py монтируется через относительный путь (как и раньше), hot-reload не требуется для smoke. |
| Smoke не создаёт файлы вовремя (race condition) | Низкая | HIGH | Файлы создаются синхронно в fixture до `docker compose up` — race невозможен. |
| Регрессия на Linux (изменение путей ломает production) | Низкая | HIGH | Изменения только в `docker-compose.test.yml` (test overlay), `docker-compose.base.yml` не трогается. Production deploy не затронут. |

---

## 10. Dependencies

- **Нет внешних зависимостей** — все компоненты уже существуют в проекте
- **Не требует новых Python-пакетов**
- **Не требует изменений в CI pipeline** — существующий `platform-test.yml` подхватит изменения автоматически

---

## 11. Rollback Plan

Если изменения вызывают регрессию в CI:
1. Revert `core/modules/status-page/docker-compose.test.yml` → убрать `volumes: !override`
2. Revert `tests/_conftest/smoke.py` → убрать генерацию тестовых файлов
3. Результат: возврат к исходному состоянию (status-page-test падает на macOS, но Linux CI — OK)

$END_DEVPLAN
