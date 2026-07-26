# RC3 Cleanup Brief — Инварианты, Языковая Политика, Stale-Код

$START_BRIEF

$ARTIFACT_CONTRACT
PURPOSE:               Привести платформу к полному соответствию инвариантам (#1), языковой политике (inline python3), и очистить кодовую базу от устаревших ссылок — как финальный шаг перед RC3.
DESCRIPTION:           Три независимые ветки работ: (A) консистентность Makefile-путей (4 строки), (B) извлечение PYEOF heredoc'ов и import-check'ов (9 inline-блоков в 5 скриптах), (C) удаление 9 stale-находок (мёртвый профиль, dangling references, diverged документация). Общий объём: ~15 файлов, ~50 строк изменений. Все изменения — низкорисковые (косметика, извлечение, удаление).
RATIONALE:             После волны DevPlans 070–084 платформа архитектурно целостна (Architecture Forensics Report, 2026-07-26), но имеет остаточный долг: 4 неконсистентных Makefile-пути, 27 inline python3 блоков в 13 скриптах (из них 9 блоков — прямые нарушения политики), 9 stale-референсов. Эти три проблемы — последний барьер перед RC3, не покрытый Gap Analysis (01-RC3-Gap-Analysis.md фокусируется на cross-plan consistency, а не на инвариантах/политике/stale-коде).
ACCEPTANCE_CRITERIA:   AC1: Все 58 .PHONY таргетов используют единый паттерн путей ($(_platform_root)/ или эквивалент). AC2: 0 PYEOF heredoc'ов в shell-скриптах (извлечены в .py файлы). AC3: 0 import-check'ов python3 -c "import yaml" (заменены на require_python_module). AC4: Мёртвый профиль gha-runner удалён из platform-env.yaml. AC5: Мёртвый глагол project-sync-secrets удалён из AGENTS.md. AC6: Статический tree listing в core/AGENTS.md актуализирован или заменён авто-генерацией. AC7: Stale skip-маркер (DevPlan 078 not merged) удалён из test_gate_env_example_drift.py. AC8: make gate MODE=fast — ЗЕЛЁНЫЙ. AC9: python -m pytest tests/ -s -v — 100% PASS.
IMPLEMENTS:            Architecture Forensics Report (2026-07-26) — Issues #1 (Makefile paths), #5 (inline python3), Stale References section. Дополняет 01-RC3-Gap-Analysis.md (не дублирует).
IMPACTS:               makefiles/helpers.mk, makefiles/bootstrap.mk (A); core/internal/scaffold/add-vhost.sh, context-init.sh, adopt-project.sh, add-project.sh, remove-project.sh, notify-hook.sh, validate.sh, verify-domains.sh, install-docker.sh, deploy-project.sh, vps-readiness.sh, node-resolver.sh, postgres/on-project-deploy.sh (B); platform-env.yaml, AGENTS.md, core/AGENTS.md, core/templates/module.mk, core/internal/llm/key_provisioner.py, core/modules/logging/config/loki-*.yml, tests/gates/test_gate_env_example_drift.py (C).
REQUIRES:              Python ≥3.10, существующие модули core/internal/scripts/yaml_query.py и core/internal/bootstrap/json_field_extractor.py, библиотека core/lib/python_deps.sh (существующая).
$END_ARTIFACT_CONTRACT

---

## §1 — Scope

Три независимые ветки работ. Можно выполнять параллельно — они не пересекаются по файлам.

---

## §2 — Ветка A: Консистентность Makefile-путей [Инвариант #1]

### Проблема

4 таргета из 58 используют пути без префикса `$(_platform_root)/`, в то время как остальные 54 используют. Это нарушает букву инварианта #1 («Makefile — единый фасад»).

### Затронутые строки

| Файл | Строка | Текущий код |
|------|:---:|------|
| `makefiles/helpers.mk` | 25 | `@core/internal/template-engine.sh check --verbose` |
| `makefiles/helpers.mk` | 31 | `@core/internal/template-engine.sh render-all` |
| `makefiles/bootstrap.mk` | 65 | `@bash core/entrypoints/converge.sh --node $(NODE) \` |
| `makefiles/bootstrap.mk` | 75 | `@bash core/internal/scaffold/add-vhost.sh --render-all ...` |

### Решение

Добавить `$(_platform_root)/` или `$(PLATFORM_ROOT)/` перед каждым путём, следуя паттерну остальных 54 таргетов.

### Риск

Нулевой. Пути резолвятся одинаково что с префиксом, что без (make выполняется из корня платформы). Изменение чисто косметическое.

---

## §3 — Ветка B: Извлечение inline python3 [Языковая Политика]

### Проблема

После Strangler-Fig топ-3 скриптов (node-lifecycle, converge, deploy-modules) — 0 inline python3. Но в 13 вспомогательных скриптах осталось 27 блоков. Из них **9 блоков — прямые нарушения политики** (AGENTS.md §Языковая политика п.3):

- **2 PYEOF heredoc'а** (75 строк Python в shell) — «heredoc → сигнал к извлечению»
- **7 import-check'ов** — `python3 -c "import yaml"` вместо `require_python_module`

Остальные 18 блоков — однотипные `python3 -c "import yaml, json, sys; ..."` для парсинга YAML/JSON. Они используют стандартные модули без бизнес-логики и могут быть отложены до следующей волны.

### Scope B: только 9 блоков в 5 скриптах

| # | Файл | Строки | Тип | Что сделать |
|---|------|:---:|------|-------------|
| B1 | `core/internal/scaffold/context-init.sh` | 275–322 | PYEOF heredoc (42 строки Python) | Извлечь в `core/internal/scaffold/context_registry.py` |
| B2 | `core/internal/scaffold/add-vhost.sh` | 270–303 | PYEOF heredoc (33 строки Python) | Извлечь в `core/internal/scaffold/vhost_yaml_reader.py` |
| B3 | `core/internal/scaffold/adopt-project.sh` | 397 | `python3 -c "import yaml"` | Заменить на `require_python_module yaml` |
| B4 | `core/internal/scaffold/adopt-project.sh` | 668 | `python3 -c "import yaml"` | Заменить на `require_python_module yaml` |
| B5 | `core/internal/scaffold/add-project.sh` | 705 | `python3 -c "import yaml"` | Заменить на `require_python_module yaml` |
| B6 | `core/internal/scaffold/remove-project.sh` | 209 | `python3 -c "import yaml"` | Заменить на `require_python_module yaml` |
| B7 | `core/internal/scaffold/add-vhost.sh` | 269 | `python3 -c "import yaml"` | Заменить на `require_python_module yaml` |
| B8 | `core/internal/validate/validate.sh` | 50 | `python3 -c "import jsonschema"` | Заменить на `require_python_module jsonschema` |
| B9 | `core/internal/notify/notify-hook.sh` | 86 | `python3 -c "import urllib.parse; ..."` | Извлечь в `core/internal/notify/url_encoder.py` |

### Инфраструктура

Добавить функцию `require_python_module()` в `core/lib/python_deps.sh` (или использовать существующий механизм из `python_deps.py`). Пример:

```bash
require_python_module() {
    python3 -c "import $1" 2>/dev/null || {
        echo "[IMP:10][deps] FATAL: Python module '$1' not installed" >&2
        return 1
    }
}
```

### Риск

Низкий. PYEOF heredoc'и — это чистые функции чтения YAML без побочных эффектов. Import-check'и — однострочники. Изменения в scaffold-скриптах должны быть протестированы через `make new-project`, `make adopt-project`, `make new-context`.

### Deferred (не в этом Brief)

16 блоков YAML/JSON-парсинга в 8 скриптах (validate.sh, deploy-project.sh, verify-domains.sh, remove-project.sh, vps-readiness.sh, node-resolver.sh, install-docker.sh, postgres/on-project-deploy.sh). Эти блоки используют стандартные модули и не содержат бизнес-логики. Будут вынесены в следующей волне.

---

## §4 — Ветка C: Удаление устаревших ссылок [Stale Code]

### Проблема

После рефакторинга остались ссылки на удалённые модули, мёртвые глаголы, diverged-документация, stale skip-маркеры, устаревшие TODO. Эти находки — шум, вводящий в заблуждение будущих агентов.

### Scope C: 9 находок

| # | Severity | Файл | Что | Действие |
|---|----------|------|-----|----------|
| C1 | **HIGH** | `platform-env.yaml:238` | Профиль `gha-runner` — модуль удалён (только `__pycache__/`), но профиль остался в generated-файле | Удалить `- gha-runner` из секции `COMPOSE_PROFILES`. Перегенерировать через `make generate-manifests`. |
| C2 | **MEDIUM** | `AGENTS.md:122` | Глагол `project-sync-secrets` со статусом ⏳ DISABLED. Не имеет ни таргета, ни скрипта, ни упоминания в manifest. | Удалить строку из глоссария. |
| C3 | **MEDIUM** | `core/AGENTS.md:100-138` | Статический tree listing `core/internal/` diverged от реальной ФС. Отсутствуют: preflight.py, docker_registry_auth.py, cert_orchestrator.py, context_deployer.py, compose_preflight.py, config_renderer.py, llm/. | Варианты: (а) удалить tree listing, заменив ссылкой «см. файловую систему», (б) актуализировать вручную, (в) заменить на авто-генерацию в `generate_agents_md.py`. Рекомендация: (а) — удалить, т.к. статические tree listings всегда diverg'ятся. |
| C4 | **MEDIUM** | `tests/gates/test_gate_env_example_drift.py:280` | `pytest.skip("DevPlan 078 not merged — NEXTAUTH_SECRET validation deferred")`. DevPlan 078 давно merged и verified. | Удалить skip. Если валидация NEXTAUTH_SECRET всё ещё нужна — создать отдельную issue. |
| C5 | **LOW** | `core/internal/llm/key_provisioner.py:83,106` | `# TODO: replace shim with real ai-platform.yaml scanner`. Отложен на неопределённый срок. | Конвертировать в TRAP[DECISION] с датой пересмотра: `# ⚠️ TRAP[DECISION] · 2026-07-26 · LOW · LLM key provisioner shim — replace with real ai-platform.yaml scanner`. |
| C6 | **LOW** | `core/templates/module.mk:19,26` | `"removed in Phase 2"` и `"moved to root Makefile Phase 2"`. «Phase 2» — аморфная метка без временной привязки. | Заменить на: `"removed in DevPlan 020"` или удалить ссылку на фазу, оставив только факт. |
| C7 | **LOW** | `core/modules/logging/config/loki-runtime-config.yml:20` | TRAP[DECISION] от 2026-07-02 про «Loki 3.6 config migration — keep legacy runtime config until migration verified». Миграция вероятно завершена. | Проверить статус миграции. Если завершена — удалить TRAP и legacy-конфиг. Если нет — обновить дату. |
| C8 | **LOW** | `core/modules/logging/config/loki-config.yml:33` | TRAP[DECISION] от 2026-07-03 про `min_ready_duration: 0s` для macOS Docker Desktop. | Проверить, нужен ли этот параметр для текущих deployment targets. Если нужен только для macOS-разработчиков — оставить с обновлённой датой. |
| C9 | **INFO** | `core/AGENTS.md:7` | Forbidden script inventory поддерживается вручную — рискует diverg'нуться от entrypoint-manifest.yaml (single source of truth). | Добавить комментарий: `# Source of truth: core/entrypoint-manifest.yaml#forbidden_scripts`. |

### Что НЕ трогаем

- **Gate-тесты на запрещённые глаголы** (`test_gate_lint_quality.py`, `test_generate_entrypoint_manifest.py`, `test_generate_agents_md.py`) — это enforcement-инфраструктура, а не устаревший код. Они активны и предотвращают регрессию.
- **Forbidden-списки в entrypoint-manifest.yaml** — single source of truth.
- **TRAP[DECISION] аннотации в bootstrap/converge** — все датированы июлем 2026, актуальны.
- **DevPlan references в комментариях** — исторический контекст, не stale.

---

## §5 — Dependency Graph

```
Ветка A (Makefile paths)     ─┐
                               ├──►  make fix-gate ──► make gate MODE=fast ──► RC3
Ветка B (inline python3)     ─┤
                               │
Ветка C (stale cleanup)       ─┘
```

Три ветки независимы — нет пересечений по файлам. Можно выполнять параллельно.

---

## §6 — Verification

### Pre-flight

```bash
make fix-gate && git add -u
make gate MODE=fast
```

### Per-branch checks

```bash
# Ветка A: проверить что все таргеты используют $(_platform_root)/
grep -n '@\(bash \)\?core/' makefiles/*.mk | grep -v '_platform_root\|PLATFORM_ROOT'

# Ветка B: 0 PYEOF heredoc'ов в shell-скриптах
grep -rn 'PYEOF' core/internal/scaffold/context-init.sh core/internal/scaffold/add-vhost.sh

# Ветка B: 0 import-check'ов python3 -c "import yaml" в scaffold
grep -rn 'python3 -c.*import yaml' core/internal/scaffold/

# Ветка C: gha-runner удалён из platform-env.yaml
grep 'gha-runner' platform-env.yaml  # должно быть пусто

# Ветка C: project-sync-secrets удалён из AGENTS.md
grep 'project-sync-secrets' AGENTS.md  # должно быть пусто
```

### Gate checklist

- [ ] `make fix-gate` — зелёный
- [ ] `make gate MODE=fast` — зелёный
- [ ] `python -m pytest tests/ -s -v` — 100% PASS
- [ ] `make new-project DIR=/tmp/test-rc3 TYPE=backend` — проект создаётся
- [ ] `make new-context NAME=test-rc3` — контекст создаётся
- [ ] `make adopt-project DIR=/tmp/test-rc3` — проект адаптируется

---

## §7 — Estimate

| Ветка | Файлов | Строк | Риск | Часов |
|-------|:---:|:---:|:---:|:---:|
| A — Makefile paths | 2 | 4 | Нулевой | 0.2 |
| B — Inline python3 extraction | 5 + 4 новых .py | ~60 изменений, ~80 новых | Низкий (scaffold-скрипты) | 2.0 |
| C — Stale cleanup | 7 | ~25 изменений | Низкий (удаление/комментарии) | 1.0 |
| **Итого** | **~16** | **~170** | **Низкий** | **3.2** |

---

$END_BRIEF
