<!-- GREP_SUMMARY: Brief, arch-forensics, collapse-fixes, observability-gap, invariant-collapse, boundary-collapse, path-prefix-collapse, verifier-blindness, hybrid-strategy, plan-no-implementation -->
<!-- STRUCTURE: ┌ARTIFACT_CONTRACT┐ → ◇ Background → ◇ Root Causes (3) → ◇ Fix Plan (5 waves) → ◇ Waves Detail → ◇ Acceptance Criteria → ◇ Non-scope → ⎋ Dependencies -->

# $ARTIFACT_CONTRACT
- **PURPOSE:** Сводный БРИФ исправлений архитектурных коллапсов ai-platform, выявленных двумя прогонами `arch-forensics` (VerificationReport 01 и 02). Без реализации — только план.
- **DESCRIPTION:** Описывает 4 подтверждённых коллапса суперпозиции (INVARIANT, BOUNDARY, PATH-PREFIX, OBSERVABILITY), их корневые причины, план из 5 волн исправлений в гибридной стратегии Rules+Runtime, критерии приёмки и границы скоупа.
- **RATIONALE:** Два последовательных отчёта (01 от 2026-07-16, 02 от 2026-07-18) выявили персистирующие коллапсы + 2 новых (PATH-PREFIX с runtime-эффектом, OBSERVABILITY). Между отчётами ~20 коммитов не затронули ни одну из корневых причин — система требует структурированного плана исправлений, а не ad-hoc фиксов.
- **ACCEPTANCE_CRITERIA:** Каждая волна имеет измеримые критерии; все коллапсы закрыты (не симптомы — корневые причины); гейты больше не дают ложных гарантий; PATH-PREFIX silent failure устранён; observability coverage ≥ severity≥high модулей.
- **IMPLEMENTS:** skill `arch-forensics` (Staff Software Architect Pattern), протокол `dev-pipeline` (Brief → Architect → Coder → QA → Fix)
- **IMPACTS:** `core/AGENTS.md` (cross-layer rule), `core/entrypoints/healthcheck.sh:12-13` (контрадикция), `tests/test_cross_layer_imports.py` (_looks_like_path gate blindness), `core/modules/backup-cron/scripts/crontab:44,46` (битые пути), `core/templates/sudo-whitelist.template` (битые пути), `core/modules/monitoring/config/prometheus.yml.tmpl` (scrape coverage gap), `core/modules/infra-metrics/docker-compose.base.yml` (postgres-exporter absent)
- **REQUIRES:** `01-VerificationReport.md`, `02-VerificationReport.md` того же плана; `core/AGENTS.md`, `core/modules/AGENTS.md`, `entrypoint-manifest.yaml`

$START_BRIEF

# Brief: Architecture Collapse Remediation Plan

## Background

### Что произошло

Два прогона `arch-forensics` (2026-07-16 и 2026-07-18) выявили **4 коллапса суперпозиции** — точки, где заявленная архитектурная модель системы недостоверна. Между отчётами выполнено ~20 коммитов (Wave 1/2 features, CI optimization, gate fixes), но **ни один не адресовал корневые причины коллапсов**. Система улучшается в измерениях, покрытых существующими гейтами, но сами гейты имеют фундаментальные слепые зоны.

### Отчёт 01 → Отчёт 02: что изменилось

| Измерение | 01 (2026-07-16) | 02 (2026-07-18) | Δ |
|-----------|-----------------|-----------------|---|
| Entrypoints | 15 | 16 | +1 (check-doc-headers.sh) |
| CI workflows | 8 | 9 | +1 |
| Test node IDs | ~822 | 874 | +52 |
| Gate entries | 36 | 41 | +5 |
| INVARIANT COLLAPSE | CRITICAL | CRITICAL | **PERSISTS** |
| BOUNDARY COLLAPSE | HIGH | HIGH | **PERSISTS** |
| PATH-PREFIX COLLAPSE | не обнаружен | HIGH | **NEW** |
| OBSERVABILITY COLLAPSE | не обнаружен | HIGH | **NEW** |

### Что уже сделано (commit `a0c7dc1` — CI hardening)

Агент CI выполнил подготовительную работу, закоммиченную перед началом этого плана:
- **Digest pinning**: hermes-agent overlay pinned to `@sha256`, busybox в monitoring обновлён
- **Smoke diagnostics**: safety net для stale containers, улучшенная диагностика `docker compose ps`, новые env vars
- **CI workflow**: шаг resolve-node переименован для консистентности логов

Эти изменения — тактические улучшения, не закрывающие ни один из 4 коллапсов. Они корректны и сохранены.

---

## Три корневые причины (НЕ симптомы)

Все 4 коллапса + doc drift порождены тремя ортогональными архитектурными дефицитами:

### Причина A — STATIC ANALYSIS CEILING

Гейт-система анализирует **исходный код** (строковые литералы в `.sh` файлах). Не анализирует:
- **Cron**: `crontab` файлы, systemd unit files — не `.sh`, не парсятся
- **Bash-переменные**: `bash "$hc_script"` — `_looks_like_path()` требует `/` в литерале, переменная его не содержит
- **Hook-цепочки**: `deploy-project.sh → monitoring/hooks/on-project-deploy.sh → generate-catalog.sh` — два прыжка, статика видит только первый
- **Сгенерированные конфиги**: `prometheus.yml` генерируется из `.tmpl` через `envsubst`/`sed` — статика не знает финального содержимого

**Что сломалось:** `tests/test_cross_layer_imports.py` (Gate #8) репортит "0 violations" при 6 реальных runtime-нарушениях. Gate даёт **ложную гарантию**.

### Причина B — SoT FRAGMENTATION (Source of Truth)

`PLATFORM_ROOT="/opt/platform"` определён в **одном** месте (`core/lib/paths.sh:33`). Но 5+ consumer-точек используют хардкоженные `/opt/core/` пути:
- `crontab:44,46` — внутри контейнера, путь не существует
- `sudo-whitelist.template:12,36-41` — на хосте, путь не существует после rsync
- `systemd/README.md:189,192` — документация
- `.kilo/server-state-vps.json:5` — конфиг агента
- `.kilo/agents/sysadmin.md:469` — документация агента

**Ни один гейт не проверяет консистентность путей** между `paths.sh` и остальными файлами. Механизм синхронизации отсутствует.

### Причина C — OBSERVABILITY AS AFTERTHOUGHT

Модули декларируют `severity: critical` в `module.yaml`, но:
- Prometheus `.tmpl` не содержит scrape job для postgres (critical), hermes-agent, langfuse, minio, loki
- Отсутствует postgres-exporter контейнер в infra-metrics
- Отсутствует hermes-agent metrics endpoint
- Нет гейта, проверяющего: `∀ module with severity≥high → ∃ scrape job`

**Что сломалось:** отказ postgres (CORRUPTION, slow-query death) невидим до хард-аутейта. Blast radius = 4+ модулей (весь LLM-стек).

---

## План исправлений: 5 волн

| # | Волна | Что | Приоритет | Блокирует причины |
|---|-------|-----|----------|-------------------|
| **W1** | Observability Coverage | postgres-exporter + hermes metrics + scrape gate | **HIGH** | C |
| **W2** | Model Surgery | Устранить контрадикцию internal↛modules, ввести typed contract | **HIGH** | A |
| **W3** | Gate Hardening | `_looks_like_path` → трекать присвоения переменных; path-consistency gate; doc-consistency gate | **MEDIUM** | A, B |
| **W4** | Path Remediation | Исправить `/opt/core/` → `/opt/platform/core/` в crontab, sudo-whitelist, документации | **MEDIUM** | B |
| **W5** | Runtime Sentinel | `verify-node-paths.sh` пост-деплой верификация, интеграция в `healthcheck`/`audit` | **LOW** | A, B |

---

## Волны: детализация

### W1 — Observability Coverage (HIGH, причина C)

**Файлы:**
- `core/modules/infra-metrics/docker-compose.base.yml` — добавить postgres-exporter контейнер (образ `quay.io/prometheuscommunity/postgres-exporter`, read-only доступ к postgres)
- `core/modules/monitoring/config/prometheus.yml.tmpl` — добавить scrape jobs:
  - `postgres-exporter:9187` (postgres метрики: connections, locks, replication lag)
  - `hermes-agent:9119` (если метрики уже есть) или добавить metrics endpoint в hermes-agent
- `tests/gates/test_gate_observability_coverage.py` — **новый gate**: парсит все `module.yaml` → для severity≥high проверяет наличие scrape job в `.tmpl`; парсит `.tmpl` → для каждого job проверяет существование targets в compose-файлах

**Критерии приёмки:**
1. `postgres-exporter` стартует в observability-net, connected to shared-db-net
2. Prometheus скрейпит `postgres-exporter:9187` (connections, locks, replication state)
3. Hermes-agent имеет `/metrics` endpoint и scrape job
4. Gate `test_gate_observability_coverage` красный если: severity≥high модуль без scrape job ИЛИ scrape target не резолвится в compose-файлах
5. Gate зелёный на текущем стеке

### W2 — Model Surgery (HIGH, причина A)

**Файлы:**
- `core/modules/AGENTS.md` → добавить секцию `## Module Interfaces (typed contract)`:
  - Модуль может декларировать `interfaces: [healthcheck, deploy-hook, remove-hook, install]` в `module.yaml`
  - `internal/` может вызывать только зарегистрированные интерфейсы
- `core/modules/<name>/module.yaml` → добавить поле `interfaces` для модулей, вызываемых из internal/ (postgres, redis, nginx, monitoring, platform-secrets, backup-cron)
- `core/entrypoints/healthcheck.sh:12-13` → удалить контрадикторное утверждение, заменить на ссылку на typed contract
- `core/AGENTS.md` cross-layer таблица → обновить: `internal/ → modules/` разрешено **только через зарегистрированные interfaces**
- `tests/test_cross_layer_imports.py` (`_looks_like_path`) → добавить проверку: вызов `modules/<name>/*` из internal/ допустим только если `<name>/module.yaml` декларирует соответствующий interface

**Важно:** Это НЕ отказ от принципа изоляции. Это замена неработающего запрета на enforceable contract. Код и документация совпадают.

**Критерии приёмки:**
1. `core/AGENTS.md` и `core/entrypoints/healthcheck.sh` не противоречат друг другу
2. `module.yaml` всех 14 модулей содержит поле `interfaces` (пустой массив если internal не вызывает)
3. Gate #8 (`test_cross_layer_imports`) красный если internal вызывает `modules/<name>/healthcheck.sh` а `interfaces` не содержит `healthcheck`
4. Gate #8 больше не слеп к вызовам через переменные — трекает присвоения переменных

### W3 — Gate Hardening (MEDIUM, причины A+B)

**Файлы:**
- `tests/test_cross_layer_imports.py` → расширить `_looks_like_path()`:
  - Трекать `local var=".../modules/..."` и `var="${CORE_DIR}/modules/..."` присвоения
  - Классифицировать `bash "$var"` как path-bearing если var была присвоена из path-содержащего литерала
- `tests/gates/test_gate_path_consistency.py` — **новый gate**:
  - Сканирует все `crontab`, `*.service`, `*.timer`, `*.path` файлы
  - Извлекает все абсолютные пути
  - Проверяет что пути либо начинаются с `PLATFORM_ROOT`, либо используют `${PATHS_*}` переменные, либо `/usr/`, `/bin/`, `/etc/` (системные)
  - `/opt/core/` → красный
- `tests/gates/test_gate_doc_consistency.py` — **новый gate**:
  - Проверяет что все глаголы из `entrypoint-manifest.yaml` присутствуют в `AGENTS.md` ✅-таблице
  - Проверяет что все pytest markers из `pyproject.toml` используются хотя бы в одном тесте

**Критерии приёмки:**
1. `_looks_like_path` классифицирует `bash "$hc_script"` как path-bearing (при наличии присвоения выше)
2. Gate `test_gate_path_consistency` красный на текущих `/opt/core/` в crontab, sudo-whitelist
3. Gate `test_gate_doc_consistency` красный на `verify` missing, `static`/`static_audit` mismatch

### W4 — Path Remediation (MEDIUM, причина B)

**Файлы:**
- `core/modules/backup-cron/scripts/crontab:44,46` → заменить `/opt/core/` на `/opt/platform/core/`
- `core/templates/sudo-whitelist.template:12,36-41` → параметризовать от `{{PLATFORM_ROOT}}` или заменить на `/opt/platform/core/`
- `core/bootstrap/systemd/README.md:189,192` → заменить `/opt/core/` на `/opt/platform/core/`
- `.kilo/server-state-vps.json:5` → обновить `workdir` (если актуально)
- `.kilo/agents/sysadmin.md:469` → обновить пример пути

**Критерии приёмки:**
1. `rg '/opt/core/' core/` не находит хардкоженных prod-путей (кроме исторических комментариев)
2. Gate `test_gate_path_consistency` зелёный (из W3)
3. Backup-cron docker-healthcheck.sh cron-job реально выполняется в контейнере (файл существует)

### W5 — Runtime Sentinel (LOW, причины A+B)

**Файлы:**
- `core/internal/verify/verify-node-paths.sh` — **новый скрипт**: проверяет после деплоя:
  - Все cron-задачи в `/etc/cron.d/` ссылаются на существующие файлы
  - Все systemd `ExecStart`/`ExecStartPre` резолвятся
  - Все Prometheus scrape targets в сгенерированном `prometheus.yml` отвечают на HTTP (если Prometheus запущен)
  - Все `sudoers` правила ссылаются на существующие файлы
- `core/internal/bootstrap/node-lifecycle.sh` → добавить вызов `verify-node-paths.sh` в `--mode update` после deploy-system
- `core/internal/healthcheck/modules-healthcheck.sh` → добавить опциональный вызов `verify-node-paths.sh` при `--deep`
- `core/entrypoints/audit.sh` → интегрировать вызов
- `core/entrypoint-manifest.yaml` → зарегистрировать как `script:` (не отдельный make target — вызывается из существующих)

**Критерии приёмки:**
1. `make healthcheck NODE=<node>` включает path-верификацию в deep-режиме
2. `make node-update NODE=<node>` выполняет `verify-node-paths.sh` после деплоя
3. При несовпадении путей — явная ошибка с указанием файла и строки

---

## Acceptance Criteria (сквозные)

1. **Gate система больше не даёт ложных гарантий** — все 4 коллапса либо закрыты, либо обнаружены существующими/новыми гейтами
2. **INVARIANT COLLAPSE закрыт** — `core/AGENTS.md` cross-layer правило машиночитаемо и enforceable; gated
3. **BOUNDARY COLLAPSE закрыт** — modules→internal вызовы через cron/systemd проверяются path-consistency gate
4. **PATH-PREFIX COLLAPSE закрыт** — `/opt/core/` не используется в prod-путях; gate path-consistency предотвращает регресс
5. **OBSERVABILITY COLLAPSE закрыт** — postgres и hermes-agent имеют метрики; gate observability-coverage предотвращает регресс
6. **Doc drift устранён** — `verify` в glossary, `static`/`static_audit` консистентны
7. **Gate-count консистентен** — 42 файла = 42 manifest entry (orphan найден и зарегистрирован)

---

## Non-scope (чего НЕ делаем)

- `sudo-whitelist.template` полная параметризация от PLATFORM_ROOT — только замена хардкода, не рефакторинг шаблонизатора
- `.kilo/` файлы — только path-фиксы, не аудит всей конфигурации агентов
- `core/bootstrap/systemd/README.md` — только path-фикс, не актуализация всей документации bootstrap
- Рефакторинг 6 runtime call sites (замена на typed contract) — только контракт в module.yaml + gate, не переписывание существующих вызовов
- Gate #8 variable tracking — MVP (только `local var=...` присвоения), не полный data-flow analysis

---

## Dependencies

| Волна | Блокируется | Блокирует |
|-------|------------|-----------|
| W1 | — | — |
| W2 | — | — |
| W3 | W2 (нужен typed contract для path-consistency) | W4 (path-consistency gate должен быть красным перед фиксом) |
| W4 | W3 (нужен gate для верификации фикса) | — |
| W5 | W4 (нужны скорректированные пути для верификации) | — |

**Рекомендуемый порядок:** W1 + W2 параллельно → W3 → W4 → W5.

---

## Сводка для исполнителя

Проблема не в отдельных багах (crontab, prometheus, healthcheck.sh), а в том, что **гейт-система не проверяет архитектурную модель, которую сама декларирует**. Gate green создаёт иллюзию здоровья — код коммитится, CI проходит, а в проде cron падает, метрик нет, документация противоречит коду.

5 волн плана:
- **W1** закрывает OBSERVABILITY COLLAPSE (добавляет метрики + gate покрытия)
- **W2** закрывает INVARIANT COLLAPSE (typed contract вместо фиктивного запрета)
- **W3** закрывает статический потолок (path-consistency gate, улучшенный `_looks_like_path`)
- **W4** закрывает PATH-PREFIX COLLAPSE (фикс хардкоженных путей + gate предотвращает регресс)
- **W5** добавляет runtime-верификацию (ловит то, что статика принципиально не может)

После выполнения всех 5 волн система перестаёт «ремонтировать по кругу» — новые гейты ловят корневые причины, а не симптомы.

$END_BRIEF
