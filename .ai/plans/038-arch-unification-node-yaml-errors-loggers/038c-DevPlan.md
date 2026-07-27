$START_DEVPLAN

# DevPlan 038c — Зачистка inline python3 (Wave 5 DevPlan 038)

## $ARTIFACT_CONTRACT

| Поле | Значение |
|------|---------|
| **PURPOSE** | Детальный план замены ~17 inline python3 на CLI-вызовы в ~12 shell-файлах. Пошаговые спецификации замены для каждого файла, whitelist update, верификация. |
| **DESCRIPTION** | DevPlan разбивает работу на 4 задачи (T5.1-T5.4 по классификации исходного DevPlan 038): замена `import yaml` на NodeYaml CLI, замена `import json` на `yaml_query.py --stdin`, обновление whitelist, финальная верификация. Каждая замена специфицирована с точными строками, старым кодом и новым кодом. |
| **RATIONALE** | Механическая замена — низкий риск, но 12 файлов требуют систематического подхода. Пошаговые спецификации предотвращают пропуск вариантов и обеспечивают консистентность exit code handling. |
| **ACCEPTANCE_CRITERIA** | AC7, AC8, AC9, AC11, AC12 из Brief 038c. |
| **IMPLEMENTS** | Wave 5 DevPlan 038 (T5.1-T5.4) |
| **IMPACTS** | ~12 shell-файлов + 1 whitelist update |
| **REQUIRES** | DevPlan 038a (NodeYaml CLI facade), `core/internal/scripts/yaml_query.py` |

---

## $SUPERPOSITION

### Option A: Механическая замена 1:1 (выбрано)

Каждый inline python3 заменяется эквивалентным CLI-вызовом. Shell-обёртка остаётся, меняется только `python3 -c "..."` на `python3 -m core.internal.shared.node_yaml --file ...` или `echo | python3 .../yaml_query.py --stdin`.

**Pro:** Минимальный diff, низкий риск, легко review. **Con:** Некоторые сложные блоки (generate-catalog.sh) требуют NodeYaml методов, которые могут быть не реализованы в 038a.

### Option B: Извлечение в Python-модули

Каждый сложный inline-блок (>10 строк) извлекается в отдельный Python-модуль в `core/internal/scripts/`. Shell вызывает `python3 module.py`.

**Pro:** Полное соответствие языковой политике, тестируемость. **Con:** Scope creep — создание 5+ новых Python-модулей выходит за рамки 038c (это scope 038a/038b).

### Option C: Гибрид — CLI для простых, Python-модуль для сложных

Inline python3 с простым YAML-доступом → NodeYaml CLI. Сложные блоки (`generate-catalog.sh`, `adopt-project.sh:427`) → Python-модуль.

**Pro:** Прагматично. **Con:** Размывает границу между 038a (создание фасада) и 038c (замена inline).

### Decision: Option A с best-effort fallback

**Выбрано:** Option A (механическая замена). Для блоков, которые NodeYaml CLI не покрывает → `⚠️ TRAP[DEBT]` комментарий в файле + регистрация в debt tracker для следующей волны. Не создаём новых Python-модулей в scope 038c.

---

## $DESIGN_DECISIONS

### DD1: Exit code mapping

| Источник | Код | Семантика |
|----------|-----|-----------|
| NodeYaml CLI | 0 | Успех |
| NodeYaml CLI | 1 | Key not found / validation failed |
| NodeYaml CLI | 2 | File not found |
| NodeYaml CLI | 3 | Parse error |
| NodeYaml CLI | 4 | ConfigValidationError (missing required key, wrong type) |
| `yaml_query.py --stdin` | 0 | Успех |
| `yaml_query.py --stdin` | 1 | Key not found |
| `yaml_query.py --stdin` | 2 | Empty stdin |
| `yaml_query.py --stdin` | 3 | Invalid JSON |

**Правило:** Shell-обёртка должна пробрасывать exit code CLI как есть, если код >0. Добавлять собственный error handling только для pre-flight проверок (файл существует, etc.).

**Примечание:** Shell `||` fallback обрабатывает все non-zero коды единообразно, поэтому exit code 4 не ломает существующее поведение.

### DD2: `yaml_read.sh` удаление из whitelist

После замены `yaml_read_domain_config()` на NodeYaml CLI, `yaml_read.sh` больше не содержит inline python3. Он удаляется из whitelist `check-no-new-inline-python3.sh`.

### DD3: `yaml_query.py --stdin` для multi-field extraction

`yaml_query.py --stdin --get field` извлекает одно поле. Для multi-field extraction (например, `deploy-project.sh:445` извлекает `verb`, `args`, `cleaned`) делается 3 отдельных вызова. Альтернатива — один вызов с `--json-output` и парсинг в bash через `jq`. Выбран вариант с 3 вызовами (меньше зависимостей, `jq` не гарантирован на VPS).

**⚠️ PREREQUISITE FIX:** `yaml_query.py --stdin --items` currently requires `--get` and will fail with `parser.error()` if used alone on a JSON array. Before T5.2 item 14 can work, yaml_query.py must be patched:
- When `--stdin` is used with `--items` and WITHOUT `--get`, treat the stdin JSON as an array and output each element on a separate line.
- This is a ~3 line fix in yaml_query.py's argparse handler.

### DD4: `add-vhost.sh:548` duplicate domain check — best-effort

Этот блок читает JSON-строки из stdin и проверяет дубликаты доменов. `yaml_query.py` не поддерживает stream processing. Варианты:
1. Сохранить как есть (легитимный случай, не YAML, не JSON-файл, а stream)
2. Извлечь в Python-модуль `check_duplicate_domains.py` (выходит за scope 038c)

**Решение:** Оставить с `⚠️ TRAP[DEBT]` комментарием. Это не `import yaml`, это stream processing с `import json`.

### DD5: `generate-catalog.sh` — out of scope 038c

55-строчный heredoc генерирует JSON-каталог проектов. Требует сложной логики (walk directories, parse multiple YAML files, aggregate fields). NodeYaml CLI не покрывает этот use-case.

**Решение:** Оставить с `⚠️ TRAP[DEBT]` комментарием + зарегистрировать в debt tracker. Извлечение в `core/internal/scripts/generate_catalog.py` — отдельная задача.

---

## NodeYaml CLI API Requirements (должен предоставить 038a)

Ниже — минимальный API, необходимый для замены всех inline python3 в scope 038c:

### Флаги CLI

```
python3 -m core.internal.shared.node_yaml \
  --file <path>            # Путь к node.yaml (обязательный)
  --get <dotted.key>       # Извлечь значение по dotted key (напр. node.host, monitoring.host_port)
  --default <value>        # Значение по умолчанию если ключ не найден
  --domain-config          # Вывести domain config в формате field:value (4 строки)
  --json-output            # Вывести результат как JSON (для dict/list)
  --items                  # Если значение — список, вывести каждый элемент на отдельной строке
  --validate               # Валидировать структуру node.yaml
  --find-project <name>    # Найти проект по имени, вывести JSON + org + host
```

### Формат вывода `--domain-config`

```
platform_domain:<domain>
email:<email>
acme_dns_plugin:<acme_dns_plugin>
project_domains:<space-separated domains>
```

Совместим с текущим форматом `yaml_read_domain_config()` (строки 142-145 в `yaml_read.sh`).

### Формат вывода `--find-project <name>`

```
<JSON проекта>
___ORG___<org>
___HOST___<host>
```

Совместим с текущим форматом `remove-project.sh:169-171`.

### Exit codes

| Код | Семантика |
|-----|-----------|
| 0 | Успех |
| 1 | Key not found / validation failed / project not found |
| 2 | File not found |
| 3 | YAML parse error |

---

## $TASKS

### T5.1: Замена `import yaml` inline python3 на NodeYaml CLI

| # | Файл | Строки | Старый паттерн | Новый вызов | Сложность |
|---|------|--------|---------------|-------------|-----------|
| 1 | `core/lib/yaml_read.sh` | 133-146 | `python3 - "$node_yaml" <<'PYEOF' ...` | `python3 -m core.internal.shared.node_yaml --file "$node_yaml" --domain-config` | 3 |
| 2 | `core/lib/node-resolver.sh` | 302-310 | `python3 -c "import yaml..."` → host extraction | `python3 -m core.internal.shared.node_yaml --file "${yaml_path}" --get node.host --default ""` | 2 |
| 3 | `core/internal/validate/validate.sh` | 71-76 | `python3 -c "import yaml..."` → YAML→JSON | `python3 -m core.internal.shared.node_yaml --file "$yaml_file" --json-output` | 2 |
| 4 | `core/internal/validate/validate.sh` | 276-282 | `python3 -c "import yaml..."` → host_port | `python3 -m core.internal.shared.node_yaml --file "${yaml_file}" --get monitoring.host_port --default 0` | 2 |
| 5 | `core/internal/verify/verify-domains.sh` | 106-118 | `python3 -c "import yaml..."` → domain list | `python3 -m core.internal.shared.node_yaml --file "${yaml_path}" --domain-config` (reuse, extract project_domains) | 3 |
| 6 | `core/internal/scaffold/remove-project.sh` | 162-174 | `python3 -c "import yaml..."` → project lookup | `python3 -m core.internal.shared.node_yaml --file "${ny}" --find-project "${name}"` | 2 |
| 7 | `core/internal/scaffold/adopt-project.sh` | 399-407 | `python3 -c "import yaml..."` → YAML→JSON | `python3 -m core.internal.shared.node_yaml --file "${compose_path}" --json-output` | 2 |
| 8 | `core/modules/postgres/hooks/on-project-deploy.sh` | 43-46 | `python3 -c "import yaml..."` → db name | `python3 -m core.internal.shared.node_yaml --file "${ai_yaml}" --get needs.database --default ""` | 2 |

#### Детальные спецификации замены

##### 1. `core/lib/yaml_read.sh:133-146` — `yaml_read_domain_config()`

**Старый код (строки 133-146):**
```bash
    python3 - "$node_yaml" <<'PYEOF'
import yaml, sys
with open(sys.argv[1]) as f:
    data = yaml.safe_load(f)
domain = data.get('domain', '')
email = data.get('email', '')
acme_dns_plugin = data.get('acme_dns_plugin', '')
projects = data.get('projects', [])
project_domains = [p.get('domain', '') for p in projects if isinstance(p, dict) and p.get('domain')]
print(f"platform_domain:{domain}")
print(f"email:{email}")
print(f"acme_dns_plugin:{acme_dns_plugin}")
print(f"project_domains:{' '.join(project_domains)}")
PYEOF
```

**Новый код:**
```bash
    python3 -m core.internal.shared.node_yaml \
        --file "$node_yaml" \
        --domain-config || return $?
```

**Exit code:** NodeYaml CLI возвращает 0 (успех), 2 (file not found), 3 (parse error). `|| return $?` пробрасывает код вызывающей стороне. Существующая проверка `[[ ! -f "$node_yaml" ]]` на строке 128 остаётся (pre-flight check).

**Примечание:** Формат вывода `--domain-config` должен быть идентичен старому (4 строки `field:value`).

##### 2. `core/lib/node-resolver.sh:302-310` — `extract_node_host()`

**Старый код (строки 305-316):**
```bash
    host="$(python3 -c "
import yaml, sys
try:
    with open('${yaml_path}') as f:
        data = yaml.safe_load(f)
    if data is None:
        sys.exit(1)
    print(data.get('node', {}).get('host', '') or '')
except Exception:
    sys.exit(1)
" 2>/dev/null)" || {
        log_imp 10 "-" "Failed to parse YAML or extract host: ${yaml_path}"
        return 1
    }
```

**Новый код:**
```bash
    host="$(python3 -m core.internal.shared.node_yaml \
        --file "${yaml_path}" \
        --get node.host \
        --default "" 2>/dev/null)" || {
        log_imp 10 "-" "Failed to parse YAML or extract host: ${yaml_path}"
        return 1
    }
```

**Примечание:** Старый код молча возвращал пустую строку при ошибке (`2>/dev/null`, `||` блок). Новый код сохраняет это поведение — NodeYaml CLI с `--default ""` возвращает пустую строку для отсутствующего ключа, а `2>/dev/null` подавляет stderr.

##### 3. `core/internal/validate/validate.sh:71-76` — YAML→JSON конвертация

**Старый код (строки 71-76):**
```bash
    if ! python3 -c "
import sys, json, yaml
with open(sys.argv[1]) as f:
    data = yaml.safe_load(f)
print(json.dumps(data))
" "$yaml_file" > "$tmp_json" 2>/dev/null; then
```

**Новый код:**
```bash
    if ! python3 -m core.internal.shared.node_yaml \
        --file "$yaml_file" \
        --json-output > "$tmp_json" 2>/dev/null; then
```

**Примечание:** `--json-output` должен вывести **весь** YAML-документ как JSON (не конкретное поле). Это семантически эквивалентно `yaml.safe_load + json.dumps`. Если NodeYaml CLI требует поле для `--json-output`, то альтернатива: `--json-output` без `--get` = весь документ.

##### 4. `core/internal/validate/validate.sh:276-282` — host_port extraction

**Старый код (строки 276-282):**
```bash
        host_port="$(python3 -c "
import sys, json, yaml
with open('${yaml_file}') as f:
    data = yaml.safe_load(f)
mon = data.get('monitoring', {})
print(mon.get('host_port', 0))
" 2>/dev/null || echo "0")"
```

**Новый код:**
```bash
        host_port="$(python3 -m core.internal.shared.node_yaml \
            --file "${yaml_file}" \
            --get monitoring.host_port \
            --default "0" 2>/dev/null || echo "0")"
```

**Примечание:** `--default "0"` возвращает `0` если ключ отсутствует. Внешний `|| echo "0"` — fallback при ошибке парсинга (exit 3).

##### 5. `core/internal/verify/verify-domains.sh:106-118` — domain list extraction

**Старый код (строки 106-121):**
```bash
    python3 -c "
import yaml, sys, json
try:
    with open('${yaml_path}') as f:
        data = yaml.safe_load(f)
    projects = data.get('projects', []) if data else []
    domains = []
    for p in projects:
        if p.get('expose', False) is True:
            if p.get('domain'):
                domains.append(p['domain'])
    print(json.dumps(domains))
except Exception as e:
    print(json.dumps({'error': str(e)}), file=sys.stderr)
    sys.exit(1)
" 2>/dev/null
```

**Новый код:**
```bash
    # Extract domains via --domain-config (reuses NodeYaml, extracts project_domains line)
    domain_config="$(python3 -m core.internal.shared.node_yaml \
        --file "${yaml_path}" \
        --domain-config 2>/dev/null)" || {
        echo '[]'
        return
    }
    # Parse project_domains line: project_domains:domain1 domain2 ...
    project_domains_line="$(echo "$domain_config" | grep '^project_domains:' | cut -d: -f2-)"
    if [[ -z "$project_domains_line" ]]; then
        echo '[]'
    else
        # Convert space-separated domains to JSON array
        echo "$project_domains_line" | python3 -c "
import sys, json
domains = sys.stdin.read().strip().split()
print(json.dumps(domains))
" 2>/dev/null
    fi
```

**⚠️ TRAP[DESIGN] · 2026-07-26 · MED · verify-domains.sh domain filtering**
· Problem: Старый код фильтрует проекты по `expose: true` и извлекает `domain`.
·   `--domain-config` извлекает ВСЕ project domains (без фильтрации по expose).
·   NodeYaml CLI не имеет фильтра `--filter expose=true`.
· Mitigation: Временное решение — `--domain-config` выдаёт все project_domains.
·   Если фильтрация по `expose` критична — добавить `--get exposed-domains` в NodeYaml CLI (scope 038a).
·   Либо: оставить старый код до добавления метода в NodeYaml.
· Rev: если верификация доменов ломается из-за отсутствия фильтрации → добавить метод в NodeYaml.

##### 6. `core/internal/scaffold/remove-project.sh:162-174` — project lookup

**Старый код (строки 162-176):**
```bash
            py_result="$(python3 -c "
import yaml, sys, json
try:
    with open('${ny}') as f:
        data = yaml.safe_load(f)
    for p in data.get('projects', []):
        if p.get('name') == '${name}':
            print(json.dumps(p))
            print('___ORG___' + (p.get('repo', '').split('/')[0] if p.get('repo') else ''))
            print('___HOST___' + (data.get('node', {}).get('host', '')))
            sys.exit(0)
    sys.exit(1)
except Exception:
    sys.exit(1)
" 2>/dev/null || true)"
```

**Новый код:**
```bash
            py_result="$(python3 -m core.internal.shared.node_yaml \
                --file "${ny}" \
                --find-project "${name}" 2>/dev/null || true)"
```

**Примечание:** `--find-project <name>` должен выводить JSON проекта, затем `___ORG___<org>`, затем `___HOST___<host>`. Формат идентичен старому коду (строки 169-171). Exit code: 0 = найдено, 1 = не найдено.

##### 7. `core/internal/scaffold/adopt-project.sh:399-407` — YAML→JSON

**Старый код (строки 399-407):**
```bash
        resolved_content="$(python3 -c "
import sys, yaml
with open('${compose_path}') as f:
    data = yaml.safe_load(f)
if not data or not isinstance(data, dict):
    sys.exit(1)
import json
print(json.dumps(data))
" 2>/dev/null)" || true
```

**Новый код:**
```bash
        resolved_content="$(python3 -m core.internal.shared.node_yaml \
            --file "${compose_path}" \
            --json-output 2>/dev/null)" || true
```

**Примечание:** `--json-output` без `--get` = весь документ как JSON. Обработка `null` или не-dict: NodeYaml CLI должен вернуть exit code 3 (parse error) или аналогичный.

##### 8. `core/modules/postgres/hooks/on-project-deploy.sh:43-46` — db name extraction

**Старый код (строки 43-50):**
```bash
    db_name="$(python3 -c "
import sys, json, yaml
with open('${ai_yaml}') as f:
    data = yaml.safe_load(f)
needs = data.get('needs', {})
db = needs.get('database', False)
print(db if db and db != False else '')
" 2>/dev/null || echo "")"
```

**Новый код:**
```bash
    db_name="$(python3 -m core.internal.shared.node_yaml \
        --file "${ai_yaml}" \
        --get needs.database \
        --default "" 2>/dev/null || echo "")"
```

**Примечание:** Старый код проверяет `db != False` (в YAML `database: false`). NodeYaml CLI с `--default ""` вернёт пустую строку для отсутствующего ключа, но для `database: false` (False в Python) должен вернуть строку `"False"` или пустую строку. **Важно:** поведение должно быть эквивалентно — если `needs.database` = `false` в YAML, результат должен быть пустой строкой.

---

### T5.2: Замена `import json` inline python3 на `yaml_query.py --stdin`

| # | Файл | Строки | Старый паттерн | Новый вызов | Сложность |
|---|------|--------|---------------|-------------|-----------|
| 9 | `core/lib/node-resolver.sh` | 255-270 | `python3 -c "import json..."` → host из JSON env | `echo "${node_host_map_json}" \| python3 "${YAML_READ_CORE_DIR}/internal/scripts/yaml_query.py" --stdin --get "${node_name}" --default ""` | 2 |
| 10 | `core/lib/vps-readiness.sh` | 74 | `python3 -c "import json..."` → host из JSON stdin | `echo "${NODE_HOST_MAP}" \| python3 "${YAML_READ_CORE_DIR}/internal/scripts/yaml_query.py" --stdin --get "${node_name}" --default ""` | 1 |
| 11 | `core/lib/vps-readiness.sh` | 78 | `python3 -c "import json..."` → list keys | ⚠️ TRAP[DEBT] — `yaml_query.py` не имеет `--keys` флага | — |
| 12 | `core/internal/deploy/deploy-project.sh` | 445-453 | `python3 -c "import json..."` → 3 поля из JSON | 3 вызова `yaml_query.py --stdin --get <field>` | 3 |
| 13 | `core/internal/deploy/deploy-project.sh` | 479-483 | `python3 -c "import json..."` → 2 поля из JSON | 2 вызова `yaml_query.py --stdin --get <field>` | 2 |
| 14 | `core/internal/verify/verify-domains.sh` | 141-146 | `python3 -c "import json..."` → null-delimited list | `yaml_query.py --stdin --items` + post-process | 3 |
| 15 | `core/internal/scaffold/add-vhost.sh` | 779-780 | `python3 -c "import json..."` → name + domain | 2 вызова `yaml_query.py --stdin --get name/domain` | 1 |

#### Детальные спецификации замены

##### 9. `core/lib/node-resolver.sh:255-270` — JSON host map lookup

**Старый код (строки 255-270):**
```bash
    host="$(python3 -c "
import json, sys
try:
    data = json.loads('${node_host_map_json//\'/\'\"\'\"\'}')
    node = '${node_name}'
    if node not in data:
        print(f'K5: Node \"{node}\" not found in NODE_HOST_MAP', file=sys.stderr)
        sys.exit(1)
    print(data[node])
except json.JSONDecodeError as e:
    print(f'K5: NODE_HOST_MAP is not valid JSON: ${e}', file=sys.stderr)
    sys.exit(1)
except Exception as e:
    print(f'K5: Failed to resolve node: ${e}', file=sys.stderr)
    sys.exit(1)
")" || {
        log_imp 10 "-" "Failed to resolve node=${node_name} from NODE_HOST_MAP"
        return 1
    }
```

**Новый код:**
```bash
    host="$(echo "${node_host_map_json}" | python3 "${YAML_READ_CORE_DIR}/internal/scripts/yaml_query.py" \
        --stdin --get "${node_name}" --default "" 2>/dev/null)" || {
        log_imp 10 "-" "Failed to resolve node=${node_name} from NODE_HOST_MAP"
        return 1
    }
    if [[ -z "$host" ]]; then
        log_imp 10 "-" "Node ${node_name} not found in NODE_HOST_MAP"
        return 1
    fi
```

**Примечание:** `yaml_query.py --stdin` читает JSON из stdin. `--get "${node_name}"` ищет ключ верхнего уровня. `--default ""` возвращает пустую строку если ключ не найден. Дополнительная проверка `[[ -z "$host" ]]` заменяет Python-логику "node not in data".

##### 10. `core/lib/vps-readiness.sh:74` — host from JSON

**Старый код (строка 74):**
```bash
        ssh_host="$(echo "${NODE_HOST_MAP}" | python3 -c "import json,sys; m=json.load(sys.stdin); print(m.get('${node_name}',''))" 2>/dev/null || true)"
```

**Новый код:**
```bash
        ssh_host="$(echo "${NODE_HOST_MAP}" | python3 "${YAML_READ_CORE_DIR}/internal/scripts/yaml_query.py" \
            --stdin --get "${node_name}" --default "" 2>/dev/null || true)"
```

##### 11. `core/lib/vps-readiness.sh:78` — list keys (TRAP[DEBT])

**Старый код (строка 78):**
```bash
            remediation_hints+=("Check NODE_HOST_MAP for node '${node_name}'. Current keys: $(echo "${NODE_HOST_MAP}" | python3 -c "import json,sys; print(list(json.load(sys.stdin).keys()))" 2>/dev/null || echo "unparseable")")
```

**Проблема:** `yaml_query.py` не имеет флага для вывода всех ключей JSON-объекта.

**Решение (на выбор):**
- **A (предпочтительно):** Добавить `--keys` флаг в `yaml_query.py`, который выводит все ключи верхнего уровня (space-separated или one per line). Scope: 038c или отдельная micro-task.
- **B (fallback):** Оставить старый код с `⚠️ TRAP[DEBT]` комментарием.
- **C:** Заменить на `python3 -c "import sys,json; print(' '.join(json.load(sys.stdin).keys()))"` — технически всё ещё inline python3, но без внешних зависимостей (только stdlib).

**Рекомендация:** Вариант A. Добавить `--keys` в `yaml_query.py` (3 строки кода).

##### 12. `core/internal/deploy/deploy-project.sh:445-453` — multi-field JSON extraction

**Старый код (строки 445-453):**
```bash
    } <<< "$(python3 -c "
import json, sys
r = json.loads(sys.argv[1])
if 'error' in r:
    sys.exit(1)
print(r['verb'])
print(r.get('args') or '')
print(r['cleaned'])
" "$json_output")"
```

**Новый код (3 раздельных вызова):**
```bash
    verb="$(echo "$json_output" | python3 "${YAML_READ_CORE_DIR}/internal/scripts/yaml_query.py" \
        --stdin --get verb --default "" 2>/dev/null)" || true
    args="$(echo "$json_output" | python3 "${YAML_READ_CORE_DIR}/internal/scripts/yaml_query.py" \
        --stdin --get args --default "" 2>/dev/null)" || true
    cleaned="$(echo "$json_output" | python3 "${YAML_READ_CORE_DIR}/internal/scripts/yaml_query.py" \
        --stdin --get cleaned --default "" 2>/dev/null)" || true
```

**⚠️ TRAP[REFACTOR] · 2026-07-26 · LOW · Multi-call overhead**
· Old code: 1 python3 process → 3 fields. New code: 3 python3 processes.
· Risk: тройной overhead на запуск python3 (~30ms → ~90ms per invocation).
· Mitigation: `deploy-project.sh` вызывается через SSH forced-command, latency network-bound (>500ms).
·   30ms overhead незначителен. Если profiling покажет проблемы — консолидировать в один вызов с `--json-output`.
· Rev: если deploy-project становится медленнее на >5% → консолидировать вызовы.

**Примечание:** Старый код использовал here-string (`<<<`) для передачи JSON как `sys.argv[1]`. Новый код передаёт JSON через stdin pipe. Функционально эквивалентно.

##### 13. `core/internal/deploy/deploy-project.sh:479-483` — platform_deliver JSON

**Старый код (строки 479-483):**
```bash
            read -r org project <<< "$(python3 -c "
import json, sys
r = json.loads(sys.argv[1])
print(r.get('org', ''), r.get('project', ''))
" "$parsed_json")"
```

**Новый код:**
```bash
            org="$(echo "$parsed_json" | python3 "${YAML_READ_CORE_DIR}/internal/scripts/yaml_query.py" \
                --stdin --get org --default "" 2>/dev/null)" || true
            project="$(echo "$parsed_json" | python3 "${YAML_READ_CORE_DIR}/internal/scripts/yaml_query.py" \
                --stdin --get project --default "" 2>/dev/null)" || true
```

##### 14. `core/internal/verify/verify-domains.sh:141-146` — null-delimited JSON array

**Старый код (строки 141-146):**
```bash
    done < <(python3 -c "
import json, sys
doms = json.loads('${domains_json}')
for d in doms:
    print(d, end='\\0')
")
```

**Новый код:**
```bash
    done < <(echo '${domains_json}' | python3 "${YAML_READ_CORE_DIR}/internal/scripts/yaml_query.py" \
        --stdin --items 2>/dev/null | tr '\n' '\0')
```

**Примечание:** `--items` выводит каждый элемент списка на отдельной строке. `tr '\n' '\0'` конвертирует newline в null delimiter для совместимости с `while IFS= read -r -d ''`.

##### 15. `core/internal/scaffold/add-vhost.sh:779-780` — JSON name/domain extraction

**Старый код (строки 779-780):**
```bash
        proj_name="$(echo "$entry" | python3 -c "import sys,json; print(json.loads(sys.stdin.read())['name'])" 2>/dev/null || echo "")"
        proj_domain="$(echo "$entry" | python3 -c "import sys,json; print(json.loads(sys.stdin.read())['domain'])" 2>/dev/null || echo "")"
```

**Новый код:**
```bash
        proj_name="$(echo "$entry" | python3 "${YAML_READ_CORE_DIR}/internal/scripts/yaml_query.py" \
            --stdin --get name --default "" 2>/dev/null || echo "")"
        proj_domain="$(echo "$entry" | python3 "${YAML_READ_CORE_DIR}/internal/scripts/yaml_query.py" \
            --stdin --get domain --default "" 2>/dev/null || echo "")"
```

---

### T5.3: Обновление whitelist в `check-no-new-inline-python3.sh`

**Файл:** `core/internal/hooks/check-no-new-inline-python3.sh`

**Изменения:**

1. **Удалить `yaml_read.sh` из whitelist:**
   ```
   # Было:
   WHITELIST_REGEX="^core/lib/yaml_read\.sh$|^core/internal/scripts/.*\.py$|^core/internal/hooks/.*\.sh$"
   # Стало:
   WHITELIST_REGEX="^core/internal/scripts/.*\.py$|^core/internal/hooks/.*\.sh$"
   ```

2. **Добавить `python_deps.sh` в whitelist (явно):**
   `python_deps.sh` уже не детектируется как violation (там `python3 -c "import X"` без yaml/json), но whitelist должен явно разрешать легитимные случаи.

3. **Добавить `generate-catalog.sh` в whitelist (временно, из-за TRAP[DEBT]):**
   Если `generate-catalog.sh` не мигрирован в 038c, его heredoc остаётся. Добавить в whitelist с комментарием:
   ```
   # ⚠️ TRAP[DEBT] · 2026-07-26 · generate-catalog.sh heredoc — deferred extraction to Python module
   ```

4. **Добавить комментарий о `install-docker.sh`:**
   `install-docker.sh:116` не детектируется (однострочник без `import`), но документировать.

**Итоговый whitelist после 038c:**
```bash
WHITELIST_REGEX="^core/internal/scripts/.*\.py$|^core/internal/hooks/.*\.sh$"
# Легитимные inline python3 (явно разрешены, детектор пропускает автоматически):
# - core/lib/python_deps.sh:22 — проверка наличия Python-модуля (python3 -c "import X")
# - core/internal/bootstrap/install-docker.sh:116 — platform detection (без import)
# - core/internal/validate/validate.sh:97 — jsonschema валидация (heredoc, не node.yaml)
# ⚠️ TRAP[DEBT] — deferred extraction:
# - core/internal/catalog/generate-catalog.sh:40 — catalog generation heredoc (55 строк)
```

---

### T5.4: Верификация

#### Step 1: AC7 — grep check

```bash
# Должен вернуть 0 результатов (или только комментарии)
grep -rn 'python3 -c.*import yaml' core/ --include='*.sh' | grep -v '^[^:]*:#'
```

#### Step 2: AC9 — pre-commit hook

```bash
# Должен пройти без ошибок
python3 core/internal/hooks/check-no-new-inline-python3.sh
# Эквивалентно (если hook интегрирован):
git add core/lib/yaml_read.sh core/lib/node-resolver.sh ...  # все изменённые файлы
# Hook сработает на pre-commit
```

#### Step 3: AC8 — gate fast

```bash
make gate MODE=fast
```

#### Step 4: AC11 — whitelist clean

```bash
grep 'yaml_read' core/internal/hooks/check-no-new-inline-python3.sh
# Должен вернуть 0 результатов (кроме комментариев)
```

#### Step 5: AC12 — audit count

```bash
# Посчитать оставшиеся inline python3 (должно быть ≤3 легитимных + TRAP[DEBT])
grep -rn 'python3 -c\|python3 - <<\|python3 <<EOF\|python3 <<PYEOF' core/ --include='*.sh' \
    | grep -v 'check-no-new-inline-python3' \
    | grep -v 'python_deps.sh' \
    | grep -v 'install-docker.sh' \
    | grep -v 'validate.sh:97' \
    | grep -v '^[^:]*:#'
# Ожидаемый результат: 0 (или только generate-catalog.sh если TRAP[DEBT])
```

#### Step 6: Функциональный smoke test

```bash
# Проверить yaml_read.sh функции
source core/lib/yaml_read.sh
yaml_get_field "path/to/node.yaml" "domain"
yaml_get_list "path/to/node.yaml" "projects"
yaml_read_domain_config "path/to/node.yaml"

# Проверить node-resolver.sh
source core/lib/node-resolver.sh
resolve_node_from_env "test-node" '{"test-node":"1.2.3.4"}'
extract_node_host "path/to/node.yaml"
```

---

## $RISKS

| # | Риск | Severity | Mitigation | Статус |
|---|------|----------|------------|--------|
| R1 | **NodeYaml CLI не реализует все методы** — `--domain-config`, `--find-project`, `--json-output` без `--get` | HIGH | Явно задокументированы в §NodeYaml CLI API Requirements. Если метод отсутствует → файл остаётся с `⚠️ TRAP[DEBT]` (не блокирует 038c, блокирует закрытие AC7) | 🔴 Зависит от 038a |
| R2 | **Exit code incompatibility** — CLI возвращает коды, несовместимые с shell-обёрткой | MEDIUM | Стандартизированные exit codes (0/1/2/3). `|| return $?` пробрасывает как есть. `2>/dev/null || true` глушит ошибки где нужно | 🟡 Mitigated |
| R3 | **Multi-field JSON extraction overhead** — 3 вызова вместо 1 (deploy-project.sh) | LOW | Network latency dominates (>500ms SSH). 60ms overhead = <1%. Если profiling покажет проблемы → консолидировать | 🟢 Accepted |
| R4 | **`verify-domains.sh` domain filtering regression** — `--domain-config` не фильтрует по `expose` | MEDIUM | Документировано в TRAP[DESIGN]. Если фильтрация критична → добавить метод в NodeYaml CLI | 🟡 Mitigated |
| R5 | **`generate-catalog.sh` + `adopt-project.sh:427` остаются не мигрированными** | LOW | TRAP[DEBT] комментарии. Extraction deferred до отдельной волны | 🟢 Accepted |
| R6 | **Конфликт с параллельными DevPlans** — другие волны меняют те же shell-файлы | MEDIUM | 038c — последняя волна DevPlan 038. Должна применяться после 038a/038b. Если другие DevPlans (079, 081, 082) трогают те же файлы — разрешать конфликты при merge | 🟡 Mitigated |

---

## $PARALLEL_GROUPS

### Wave 1 (T5.1 — NodeYaml CLI replacements)
- **Tasks:** T5.1 (8 файлов)
- **Независимы:** все 8 файлов не зависят друг от друга
- **Зависимость:** NodeYaml CLI (038a)
- **Command:** `coder Read 038c-DevPlan.md, implement T5.1: replace import yaml inline python3 with NodeYaml CLI`

### Wave 2 (T5.2 — yaml_query.py --stdin replacements)
- **Tasks:** T5.2 (7 замен в 5 файлах)
- **Независимы:** все файлы не зависят от T5.1
- **Command:** `coder Read 038c-DevPlan.md, implement T5.2: replace import json inline python3 with yaml_query.py --stdin`

### Wave 3 (T5.3 + T5.4 — whitelist + verification)
- **Tasks:** T5.3 (whitelist update), T5.4 (verification)
- **Зависимость:** T5.1 + T5.2 (все замены выполнены)
- **Command:** `coder Read 038c-DevPlan.md, implement T5.3 + T5.4: update whitelist and verify AC7, AC8, AC9`

---

## File Manifest

### Модифицируемые файлы (T5.1 — NodeYaml CLI)

| Файл | Изменение | Строки |
|------|-----------|--------|
| `core/lib/yaml_read.sh` | `yaml_read_domain_config()` → `NodeYaml --domain-config` | 133-146 |
| `core/lib/node-resolver.sh` | `extract_node_host` → `NodeYaml --get node.host` | 302-310 |
| `core/internal/validate/validate.sh` | YAML→JSON → `NodeYaml --json-output` | 71-76 |
| `core/internal/validate/validate.sh` | host_port → `NodeYaml --get monitoring.host_port` | 276-282 |
| `core/internal/verify/verify-domains.sh` | domain list → `NodeYaml --domain-config` | 106-118 |
| `core/internal/scaffold/remove-project.sh` | project lookup → `NodeYaml --find-project` | 162-174 |
| `core/internal/scaffold/adopt-project.sh` | YAML→JSON → `NodeYaml --json-output` | 399-407 |
| `core/modules/postgres/hooks/on-project-deploy.sh` | db name → `NodeYaml --get needs.database` | 43-46 |

### Модифицируемые файлы (T5.2 — yaml_query.py --stdin)

| Файл | Изменение | Строки |
|------|-----------|--------|
| `core/lib/node-resolver.sh` | JSON lookup → `yaml_query.py --stdin` | 255-270 |
| `core/lib/vps-readiness.sh` | JSON host/key listing → `yaml_query.py --stdin` | 74, 78 |
| `core/internal/deploy/deploy-project.sh` | Multi-field JSON → `yaml_query.py --stdin` | 445-453, 479-483 |
| `core/internal/verify/verify-domains.sh` | Null-delimited list → `yaml_query.py --stdin --items` | 141-146 |
| `core/internal/scaffold/add-vhost.sh` | JSON name/domain → `yaml_query.py --stdin` | 779-780 |

### Модифицируемые файлы (T5.3 — whitelist)

| Файл | Изменение |
|------|-----------|
| `core/internal/hooks/check-no-new-inline-python3.sh` | Удалить `yaml_read.sh` из whitelist, добавить TRAP[DEBT] комментарии |

### Файлы с TRAP[DEBT] (НЕ изменяются в 038c)

| Файл | Причина | Строки |
|------|---------|--------|
| `core/internal/scaffold/add-vhost.sh` | Duplicate domain check — stream processing, не покрывается `yaml_query.py` | 548-564 |
| `core/internal/scaffold/adopt-project.sh` | Complex JSON analysis — 40 строк, не покрывается `yaml_query.py --stdin` | 427-468 |
| `core/internal/catalog/generate-catalog.sh` | Catalog generation heredoc — 55 строк, не покрывается NodeYaml CLI | 40-84 |

### Легитимные inline python3 (НЕ изменяются)

| Файл | Причина | Строки |
|------|---------|--------|
| `core/lib/python_deps.sh` | Проверка наличия Python-модуля | 22 |
| `core/internal/bootstrap/install-docker.sh` | Platform detection (без import) | 116 |
| `core/internal/validate/validate.sh` | jsonschema валидация (не node.yaml) | 97-107 |

### Файлы, требующие доработки в зависимых DevPlans

| Файл | Что нужно | DevPlan |
|------|-----------|---------|
| `core/internal/scripts/yaml_query.py` | Добавить `--keys` флаг для вывода ключей JSON-объекта | 038c (micro-fix) или 038d |
| `core/internal/shared/node_yaml.py` | CLI фасад с флагами: `--file`, `--get`, `--domain-config`, `--json-output`, `--items`, `--default`, `--find-project`, `--validate` | 038a |

---

## Сводка: что изменится в цифрах

| Метрика | До 038c | После 038c | Delta |
|---------|---------|------------|-------|
| `python3 -c "import yaml"` (active) | 6 | 0 | -6 |
| `python3 -c "import json"` (active) | 7 | 0 | -7 |
| `python3 heredoc` (active, исключая легитимные) | 2 | 1 (generate-catalog.sh TRAP[DEBT]) | -1 |
| Файлов с inline python3 | 12 | 4 (легитимные) + 1 (TRAP[DEBT]) | -8 |
| Whitelist entries | 3 patterns | 2 patterns + TRAP[DEBT] docs | -1 |
| AC7 status | ❌ FAIL | ✅ PASS | +1 |

$END_DEVPLAN
