$START_BRIEF

# Brief 038c — Зачистка inline python3 (Wave 5 DevPlan 038)

## $ARTIFACT_CONTRACT

| Поле | Значение |
|------|---------|
| **PURPOSE** | Зачистить ~17 inline `python3 -c` / heredoc в ~12 shell-файлах — замена на CLI фасада NodeYaml (создан в 038a) или `yaml_query.py --stdin` (существует). Завершить Wave 5 DevPlan 038. |
| **DESCRIPTION** | Финальная волна декомпозиции DevPlan 038 — удаление inline python3 из shell-скриптов. 12 shell-файлов содержат 17+ фрагментов встроенного Python-кода, нарушающих языковую политику платформы (AGENTS.md). Два канала замены: (1) `import yaml` → CLI фасада `NodeYaml` из DevPlan 038a; (2) `import json` → `yaml_query.py --stdin`. Легитимные inline python3 (3 случая) сохраняются. |
| **RATIONALE** | После DevPlans 038a (NodeYaml unified facade + CLI) и 038b (yaml_read.sh адаптация) инфраструктура для замены inline python3 полностью готова. Зачистка inline python3 — прямое требование языковой политики (AGENTS.md, Tier 1 Strangler-триггер) и AC7 исходного DevPlan 038. |
| **ACCEPTANCE_CRITERIA** | **AC7:** `grep -rn 'python3 -c.*import yaml' core/ --include='*.sh'` → 0 (кроме комментариев). **AC9:** Pre-commit hook `check-no-new-inline-python3.sh` passes. **AC8:** `make gate MODE=fast` passes. **AC11:** `yaml_read.sh` удалён из whitelist (после замены `yaml_read_domain_config`). **AC12:** Количество `python3 -c` / heredoc в shell под `core/` уменьшено с ~17 до 3 (только легитимные). |
| **IMPLEMENTS** | Wave 5 (T5.1-T5.4) DevPlan 038 — inline python3 cleanup |
| **IMPACTS** | ~12 shell-файлов: `core/lib/yaml_read.sh`, `core/lib/node-resolver.sh`, `core/lib/vps-readiness.sh`, `core/internal/validate/validate.sh`, `core/internal/verify/verify-domains.sh`, `core/internal/deploy/deploy-project.sh`, `core/internal/scaffold/add-vhost.sh`, `core/internal/scaffold/remove-project.sh`, `core/internal/scaffold/adopt-project.sh`, `core/modules/postgres/hooks/on-project-deploy.sh`, `core/internal/catalog/generate-catalog.sh`, `core/internal/hooks/check-no-new-inline-python3.sh` (whitelist) |
| **REQUIRES** | DevPlan 038a (NodeYaml unified facade + CLI: `--file`, `--get`, `--domain-config`, `--validate`, `--json-output`, `--items`), `core/internal/scripts/yaml_query.py` (существует, `--stdin` режим) |

---

## $DOCUMENT_PLAN

### 1. Problem Statement

После DevPlans 038a/038b в shell-скриптах под `core/` остаётся ~17 фрагментов inline python3:

| Категория | Кол-во | Файлы |
|-----------|--------|-------|
| `python3 -c` с `import yaml` | 6 | `node-resolver.sh`, `validate.sh` (2), `verify-domains.sh`, `remove-project.sh`, `on-project-deploy.sh` |
| `python3 heredoc` с `import yaml` | 1 | `yaml_read.sh:133` (`yaml_read_domain_config`) |
| `python3 -c` с `import json` | 7 | `node-resolver.sh`, `vps-readiness.sh` (2), `deploy-project.sh` (2), `verify-domains.sh`, `add-vhost.sh` (2) |
| `python3 -c` с `import json` (stdin stream) | 1 | `add-vhost.sh:548` (duplicate domain check) |
| `python3 -c` сложный анализ JSON | 1 | `adopt-project.sh:427` (~40 строк) |
| `python3 heredoc` (каталог) | 1 | `generate-catalog.sh:40` (~55 строк) |
| **Легитимные (сохранить)** | **3** | `python_deps.sh` (проверка модуля), `install-docker.sh` (platform detection), `validate.sh:97` (jsonschema) |

Все inline python3 с `import yaml` или `import json` нарушают языковую политику (Tier 1 Strangler-триггер: любой новый `python3 -c` / heredoc — сигнал к извлечению). После создания NodeYaml CLI фасада (038a) замена стала тривиальной.

### 2. Why Now

- **Инфраструктура готова** — NodeYaml CLI фасад (038a) предоставляет все необходимые методы: `--domain-config`, `--get`, `--validate`, `--json-output`, `--items`. `yaml_query.py --stdin` уже существует.
- **Минимальный риск** — замена механическая, не меняет бизнес-логику. Shell-обёртка вызывает CLI вместо inline python3.
- **AC7 блокирует закрытие DevPlan 038** — без этой волны исходный DevPlan не может быть завершён.

### 3. Scope

#### Scope In

| # | Файл | Строки | Тип замены | Инструмент |
|---|------|--------|-----------|------------|
| 1 | `core/lib/yaml_read.sh` | 133-146 | heredoc → CLI | `NodeYaml --domain-config` |
| 2 | `core/lib/node-resolver.sh` | 255-270 | `python3 -c` → `--stdin` | `yaml_query.py --stdin` |
| 3 | `core/lib/node-resolver.sh` | 302-310 | `python3 -c` → CLI | `NodeYaml --get node.host` |
| 4 | `core/lib/vps-readiness.sh` | 74, 78 | `python3 -c` → `--stdin` | `yaml_query.py --stdin` |
| 5 | `core/internal/validate/validate.sh` | 71-76 | `python3 -c` → CLI | `NodeYaml --json-output` |
| 6 | `core/internal/validate/validate.sh` | 276-282 | `python3 -c` → CLI | `NodeYaml --get monitoring.host_port` |
| 7 | `core/internal/verify/verify-domains.sh` | 106-118 | `python3 -c` → CLI | `NodeYaml --domain-config` (reuse) |
| 8 | `core/internal/verify/verify-domains.sh` | 141 | `python3 -c` → `--stdin` | `yaml_query.py --stdin` |
| 9 | `core/internal/deploy/deploy-project.sh` | 445-453 | `python3 -c` → `--stdin` | `yaml_query.py --stdin` |
| 10 | `core/internal/deploy/deploy-project.sh` | 479-483 | `python3 -c` → `--stdin` | `yaml_query.py --stdin` |
| 11 | `core/internal/scaffold/add-vhost.sh` | 548-564 | `python3 -c` → `--stdin` | `yaml_query.py --stdin` (stream check) |
| 12 | `core/internal/scaffold/add-vhost.sh` | 779-780 | `python3 -c` → `--stdin` | `yaml_query.py --stdin` |
| 13 | `core/internal/scaffold/remove-project.sh` | 162-174 | `python3 -c` → CLI | `NodeYaml --get projects` + grep |
| 14 | `core/internal/scaffold/adopt-project.sh` | 399-407 | `python3 -c` → CLI | `NodeYaml --json-output` |
| 15 | `core/internal/scaffold/adopt-project.sh` | 427-468 | `python3 -c` → модуль | `yaml_query.py --stdin` (multi-field) или Python-модуль |
| 16 | `core/modules/postgres/hooks/on-project-deploy.sh` | 43-46 | `python3 -c` → CLI | `NodeYaml --get needs.database` |
| 17 | `core/internal/catalog/generate-catalog.sh` | 40-84 | heredoc → CLI | `NodeYaml --get` (для отдельных полей) или Python-модуль |

#### Scope Out

- `python_deps.sh:22` — легитимная проверка наличия модуля
- `install-docker.sh:116` — platform detection однострочник
- `validate.sh:97-107` — jsonschema валидация (не node.yaml)
- `deploy.sh` entrypoint — `python3 -m` вызовы (уже используют Python-модули, не inline python3)
- Создание новых Python-модулей для сложной логики — **out of scope 038c** (это scope 038a/038b). 038c только заменяет inline python3 на CLI-вызовы.
- `generate-catalog.sh` — heredoc генерирует JSON-каталог. Если NodeYaml CLI не покрывает все поля — extraction в Python-модуль (deferred до 038d или doc-комментарий с `⚠️ TRAP[DEBT]`). Этот файл обрабатывается best-effort.

### 4. Stakeholders

| Стейкхолдер | Интересы |
|-------------|----------|
| Разработчики платформы | Меньше inline python3 = проще отладка, grep, тестирование |
| CI/CD система | Pre-commit hook enforcement без false positives |
| Архитектор платформы | Языковая политика enforced, AC7 закрыт |
| Будущие агенты | Shell-файлы содержат только оркестрацию, бизнес-логика в Python |

### 5. Success Criteria

| AC | Критерий | Проверка |
|----|----------|----------|
| AC7 | 0 активных inline `python3 -c "import yaml"` | `grep -rn 'python3 -c.*import yaml' core/ --include='*.sh'` → 0 (кроме комментариев) |
| AC9 | Pre-commit hook `check-no-new-inline-python3` passes | `make check-no-new-inline-python3` → exit 0 |
| AC8 | `make gate MODE=fast` passes | CI green |
| AC11 | `yaml_read.sh` удалён из whitelist | `grep 'yaml_read' core/internal/hooks/check-no-new-inline-python3.sh` → 0 |
| AC12 | Число inline python3 сокращено с ~17 до ≤3 | Ручной подсчёт по `grep` до/после |

### 6. Dependencies

| Зависимость | Статус | Что нужно |
|-------------|--------|-----------|
| DevPlan 038a (NodeYaml CLI) | **ТРЕБУЕТСЯ** | CLI фасад с флагами: `--file`, `--get <dotted.key>`, `--domain-config`, `--json-output`, `--items`, `--default <val>`, `--find-project <name>`. Exit codes: 0=success, 1=not found, 2=file not found, 3=parse error. |
| `yaml_query.py --stdin` | ✅ СУЩЕСТВУЕТ | Используется для замены `import json` inline python3 |
| DevPlan 038b (yaml_read.sh adaptation) | **ТРЕБУЕТСЯ** | `yaml_read.sh` функции (`yaml_get_field`, `yaml_get_list`) уже делегируют в CLI фасада |

### 7. Risks

| # | Риск | Severity | Mitigation |
|---|------|----------|------------|
| R1 | **NodeYaml CLI не покрывает все use-case** — часть inline python3 требует методов, не запланированных в API 038a | MEDIUM | Инвентаризация API в DevPlan 038c §NodeYaml CLI API Requirements; если метод отсутствует — добавить в scope 038a или deferred |
| R2 | **Exit code несовместимость** — замена inline python3 на CLI меняет exit code поведение | MEDIUM | Проверить exit code маппинг: NodeYaml → `yaml_read.sh` → callers. CLI использует стандартные коды (0/1/2/3), совпадающие с `yaml_query.py` |
| R3 | **Сложные inline-блоки требуют извлечения в Python-модуль** — `generate-catalog.sh` (55 строк), `adopt-project.sh:427` (40 строк) | MEDIUM | Best-effort замена через CLI. Если CLI не покрывает — `⚠️ TRAP[DEBT]` в файле + extraction deferred до отдельной волны |
| R4 | **whitelist update конфликтует с параллельными изменениями** — несколько волн трогают `check-no-new-inline-python3.sh` | LOW | 038c — последняя волна DevPlan 038; whitelist обновляется атомарно в конце |

$END_BRIEF
