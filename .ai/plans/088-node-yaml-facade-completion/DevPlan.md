$START_DEVPLAN

$ARTIFACT_CONTRACT
PURPOSE:               Завершить миграцию NodeYaml facade — все прямые yaml.safe_load() и yq eval → NodeYaml typed API. Удалить shell-зависимость yq. Объединить 3 независимые реализации resolve_node_yaml в одну Python. Расширить NodeYaml до полного покрытия всех 41 поля node.yaml (13 top-level + 28 nested).
DESCRIPTION:           NodeYaml facade создан в DP-070 (только context extraction — 3 поля). Несмотря на это, Python-файлы всё ещё используют прямой yaml.safe_load(), shell-скрипты используют yq (внешний CLI с другой семантикой парсинга), а resolve_node_yaml существует в трёх независимых реализациях (shell node-resolver.sh + Python overlay_deliverer.py + Python domain_verifier.py). Этот DevPlan завершает миграцию: NodeYaml становится единственным typed интерфейсом для всех операций с node.yaml, yq удаляется как зависимость.
RATIONALE:             5+ способов чтения node.yaml создают недетерминированное поведение: yq и PyYAML могут по-разному парсить анкоры/escape-символы/multi-doc YAML. Баг в одном пути чтения не воспроизводится в другом → труднодиагностируемые ошибки. Единый typed API с document validation (jsonschema) устраняет класс ошибок «не то поле не из того парсера».
ACCEPTANCE_CRITERIA:
  - AC1: NodeYaml typed API покрывает все 41 поле node.yaml по реальной core/schemas/node.schema.json
  - AC2: 0 grep "yaml.safe_load.*node" core/internal/ вне NodeYaml (кроме legacy migration notes)
  - AC3: 0 grep "yq.*node\.yaml\|yq.*node_yaml" core/ — yq удалён
  - AC4: resolve_node_yaml — единственная реализация в Python (node-resolver.sh, domain_verifier.py → удалены или фасад)
  - AC5: 0 grep "yaml_helpers\.py" core/ — yaml_helpers.py удалён (bootstrap.sh мигрирован на NodeYaml CLI)
  - AC6: NodeYaml.validate() — jsonschema валидация node.yaml при загрузке (использует core/schemas/node.schema.json)
  - AC7: функциональная эквивалентность подтверждена тестами — все существующие тесты проходят без модификации
  - AC8: make gate MODE=fast — зелёный
  - AC9: python -m pytest tests/ -v — все тесты проходят
IMPLEMENTS:            Superposition Analysis 2026-07-28 — Проблема 3 (NodeYaml 3/39 coverage) + Agent 3 Parallel Branches Report (Node YAML domain) + DP-070 (завершение)
IMPACTS:               ~35 файлов (1 MODIFY NodeYaml, ~22 MODIFY consumers, 2 DELETE, ~4 CREATE tests, 2 MODIFY gate, 1 MODIFY bootstrap). Подробно в File Manifest.
REQUIRES:              DP-070 (NodeYaml facade exists), DP-087 (Bootstrap — может конфликтовать с changes в node.yaml consumers). Рекомендуется merge DP-087 перед стартом DP-088.
$END_ARTIFACT_CONTRACT

---

# DevPlan 088: NodeYaml Facade Completion

**Severity:** HIGH — 5+ параллельных путей чтения node.yaml, недетерминированное поведение
**Created:** 2026-07-28
**Author:** Kilo (architect agent)
**Source:** Superposition Analysis, Parallel Branches Report (Agent 3 — Node YAML domain)
**Sequenced:** AFTER DP-087 (Bootstrap), BEFORE DP-089 (Deploy)

---

## §1. Current State

### NodeYaml facade coverage (DP-070): 3 поля из 41 (13 top-level + 28 nested)

```
node.yaml structure (по core/schemas/node.schema.json):
  context: str                         ← NodeYaml.get_context() ✅
  contexts: [{name,description,...}]   ← НЕ покрыто
  domain: str                          ← NodeYaml.get_domain_config() 🔶 partial
  email: str                           ← NodeYaml.get_domain_config() 🔶 partial
  acme_dns_plugin: enum(string)        ← НЕ покрыто (строка, НЕ объект)
  node:
    name, host, owner_key              ← НЕ покрыто как dataclass
    ci_deploy_key, timezone            ← НЕ покрыто
  firewall: {extra_ports: [int]}       ← НЕ покрыто
  secrets: {enc_file, required:[...]}  ← НЕ покрыто
  tor: {enabled,skip_verify,bridges_file} ← НЕ покрыто
  modules: [{name,enabled,...}]        ← NodeYaml.get_modules() ✅
  projects: [{name,repo,type,...}]     ← NodeYaml.get_projects() ✅
  postgres_init_databases: [str]       ← НЕ покрыто
  repos: {core, node_configs}          ← НЕ покрыто
```

### Способы чтения node.yaml

| Способ | Используется в | Файлов |
|--------|---------------|--------|
| `NodeYaml(path)` typed API | overlay_deliverer, preflight, vhost_renderer, project_adopter, мониторинг | ~8 |
| `yaml.safe_load(f)` direct | cert_collector, platform_export_metrics, monitoring_config_renderer, sync_env_defaults, gen_env_platform, generate_secrets_manifest, project_adopter (некоторые), и др. | ~12 |
| `yq eval` CLI (shell) | add-project.sh, remove-project.sh, project-list.sh | 3 |
| `python3 -m core.internal.shared.node_yaml` CLI | node-resolver.sh, yaml_read.sh, validate.sh, issue-cert.sh | 4 |
| `yaml_helpers.py` (bootstrap) | bootstrap.sh entrypoint | 1 |

### 3 независимые resolve_node_yaml

| Файл | Реализация | 3-path логика |
|------|-----------|--------------|
| `core/lib/node-resolver.sh` | Shell: yaml_read_key → python3 CLI | ✅ |
| `core/internal/bootstrap/overlay_deliverer.py` | Python: NodeYaml.load() | ✅ |
| `core/internal/verify/domain_verifier.py:109` | Python: os.path + glob по 3 путям | ✅ |

Реализации:
- `node-resolver.sh` — shell, использует yq для парсинга ключей, 3-path: (1) текущая директория, (2) $HOME/projects/*/node-configs/, (3) /opt/node-configs/
- `overlay_deliverer.py` — Python, использует NodeYaml.load(), 3-path аналогичный
- `domain_verifier.py:109` — Python, чистая 3-path реализация без NodeYaml: (1) platform_root/node-configs/{name}/, (2) $HOME/projects/*/node-configs/{name}/, (3) /opt/node-configs/{name}/

---

## §2. Target State

### NodeYaml полное typed API (schema-based)

```python
# === Top-level scalar fields ===
context: str                                         # "tronyxlab"
domain: str                                          # "tronyx.ru"
email: str                                           # admin email
acme_dns_plugin: str                                 # enum: webnames|cf|dp|generic

# === Contexts ===
contexts: list[ContextEntry]                         # [{name, description, node_configs_repo, hermes_agent_repo}]
@dataclass ContextEntry:
    name: str
    description: str = ""
    node_configs_repo: str = ""
    hermes_agent_repo: str = ""

# === Node metadata ===
node: NodeDeclaration                                # {name, host, owner_key, ci_deploy_key, timezone}
@dataclass NodeDeclaration:
    name: str = ""
    host: str = ""
    owner_key: str = ""
    ci_deploy_key: str | None = None
    timezone: str = "UTC"

# === Firewall ===
firewall: FirewallConfig                             # {extra_ports: [int]}
@dataclass FirewallConfig:
    extra_ports: list[int] = field(default_factory=list)

# === Secrets ===
secrets: SecretsConfig                               # {enc_file, required: [{name, env_var, description}]}
@dataclass SecretsConfig:
    enc_file: str = ""
    required: list[SecretEntry] = field(default_factory=list)
@dataclass SecretEntry:
    name: str
    env_var: str
    description: str = ""

# === Tor ===
tor: TorConfig                                       # {enabled, skip_verify, bridges_file}
@dataclass TorConfig:
    enabled: bool = False
    skip_verify: bool = False
    bridges_file: str = ""

# === Lists ===
modules: list[ModuleEntry]                           # [{name, enabled, config_overlay}]
projects: list[ProjectEntry]                         # [{name, repo, type, domain, database, context}]
postgres_init_databases: list[str]

# === Repos ===
repos: ReposConfig                                   # {core, node_configs}
@dataclass ReposConfig:
    core: str = ""
    node_configs: str = ""

# === Методы ===
@classmethod
def load(cls, path: str | Path, validate: bool = True) -> NodeYaml: ...
@classmethod
def resolve(cls, node_name: str | None = None) -> NodeYaml: ...  # 3-path resolution
def get_project(self, name: str) -> dict | None: ...
def validate(self, schema_path: str | None = None) -> list[str]: ...  # использует core/schemas/node.schema.json
def add_project(self, project: ProjectEntry) -> None: ...
def remove_project(self, name: str) -> bool: ...
def update_project(self, name: str, **updates) -> bool: ...
```

**Итого: 13 top-level полей, 28 nested полей = 41 поле** (против выдуманных network/DockerConfig/UsersConfig/CertConfig/AcmeConfig/MonitoringConfig/NotifyConfig/BootstrapConfig — этих НЕТ в реальной schema).

### Все потребители → NodeYaml

```
Было:                              Стало:
add-project.sh                     add-project.sh
  yq eval ...                        python3 -c "from ... import NodeYaml; ..."

remove-project.sh                  remove-project.sh
  yq eval ...                        python3 -c "from ... import NodeYaml; ..."

project-list.sh                    project-list.sh
  yq eval (13 calls)                 python3 -c "from ... import NodeYaml; ..."

yaml_helpers.py                    → УДАЛЁН
  yaml.safe_load(f)

node-resolver.sh                   → ФАСАД (1 строка)
  resolve_node_yaml()               python3 -c "from ... import NodeYaml; NodeYaml.resolve()"
```

### §Mutation API

NodeYaml НЕ имеет mutation API — все изменения node.yaml выполняются через `yq eval -i` (add-project.sh, remove-project.sh). Для полного удаления yq необходимы:

```python
def add_project(self, project: ProjectEntry) -> None:
    """Add a project to node.yaml and write back to disk.

    ## @purpose  Replace yq eval -i ".projects += [...]" for add-project.sh
    ## @param project  ProjectEntry dataclass with name, repo, type, domain, database, context
    ## @raises ConfigValidationError if project.name already exists (duplicate guard)
    ## @invariants  Writes back to original file path. Fails on duplicate name.
    """

def remove_project(self, name: str) -> bool:
    """Remove a project from node.yaml and write back to disk.

    ## @purpose  Replace yq eval -i "del(.projects[] | select(.name == '${name}'))"
    ## @param name  Project name to remove
    ## @returns True if project was found and removed, False if not found
    ## @invariants  Writes back to original file path. Does NOT remove matching repo.
    """

def update_project(self, name: str, **updates) -> bool:
    """Update fields of an existing project entry.

    ## @purpose  Replace yq eval -i for project field updates
    ## @param name  Project name to update
    ## @param updates  Fields to update (e.g., domain="new.example.com")
    ## @returns True if project was found and updated, False if not found
    ## @invariants  Does not add new projects (use add_project). Only updates existing.
    """
```

**Design decisions:**
- Все методы пишут обратно в исходный файл (`self._path`), сохраняя YAML-комментарии через ruamel.yaml.
- `add_project()` проверяет дубликаты по `name` (не по `repo`) — предотвращает случайное дублирование.
- `remove_project()` не удаляет связанные ресурсы (DB, certs) — это ответственность вызывающего кода.
- `update_project()` принимает `**updates` — гибкость для разных сценариев миграции, пока потребители не консолидируются.

---

## §3. Wave Structure

### Wave 1: NodeYaml API Expansion

| Task | Описание | Effort |
|------|----------|--------|
| **T1** | Расширить NodeYaml dataclass: добавить все 36 недостающих полей по core/schemas/node.schema.json (13 top-level + 26 nested). typed sub-dataclasses: ContextEntry, NodeDeclaration, FirewallConfig, SecretsConfig, SecretEntry, TorConfig, ModuleEntry, ProjectEntry, ReposConfig. | 4 |
| **T2** | Реализовать NodeYaml.resolve(): 3-path resolution — объединить 3 существующие реализации (node-resolver.sh, overlay_deliverer.py, domain_verifier.py:109) в одну Python. Аргументы: node_name (из env или hostname), config_dir. | 2 |
| **T2.5** | Мигрировать domain_verifier.py resolve_node_yaml: заменить вызов domain_verifier.py:resolve_node_yaml() → NodeYaml.resolve(). Валидировать, что все три 3-path реали化 дают эквивалентные результаты на тестовых нодах. | 1 |
| **T3** | Реализовать NodeYaml.validate(): jsonschema валидация (опционально, validate=True по умолчанию). Schema: использовать существующий core/schemas/node.schema.json — НЕ создавать новый node_yaml_schema.json. | 2 |
| **T3.5** | Реализовать NodeYaml mutation API: add_project(), remove_project(), update_project() с write-back в исходный файл через ruamel.yaml (сохранение YAML-комментариев). Unit-тесты для mutation. | 3 |
| **T4a** | Unit-тесты typed property: test_node_yaml_full.py — 15 тестов типизированных полей и dataclass-контрактов | 2 |
| **T4b** | Unit-тесты resolve: test_node_yaml_full.py — 5 тестов 3-path resolution | 1 |
| **T4c** | Unit-тесты validate: test_node_yaml_full.py — 5 тестов jsonschema validation | 1 |
| **T4d** | Unit-тесты mutation API: test_node_yaml_mutation.py — 8 тестов add/remove/update project | 2 |
| **T4e** | Unit-тесты CLI + parity: test_node_yaml_full.py — 7 тестов CLI + parity-тест yq eval vs NodeYaml CLI | 1 |

### Wave 2: Python Consumer Migration (~12 файлов)

| Task | Описание | Effort |
|------|----------|--------|
| **T4.5** | Аудит consumers: классифицировать все файлы с yaml.safe_load на читающих node.yaml vs другие YAML (template-manifest.yaml, entrypoint-manifest.yaml, secrets.yaml, compose.yaml, module.yaml). Исключить ложных потребителей. | 1 |
| **T5** | Миграция Python consumers (реальные): cert_collector.py, platform_export_metrics.py, monitoring_config_renderer.py, sync_env_defaults.py, gen_env_platform.py, generate_secrets_manifest.py, project_adopter.py (частично), vhost_renderer.py — yaml.safe_load → NodeYaml.load() | 4 |
| **T6** | Миграция оставшихся Python consumers: все найденные на T4.5 yaml.safe_load с node.yaml | 2 |

### Wave 3: Shell → Python Bridge + Dependency Removal

| Task | Описание | Effort |
|------|----------|--------|
| **T6.5** | Миграция yaml_read.sh callers: issue-cert.sh, node-lifecycle.sh, deploy-project.sh — заменить yaml_read_key / yaml_read_domain_config на python3 NodeYaml CLI. ⚠️ `yaml_read_key` вызывается из `node-lifecycle.sh:97`, но не определена в `yaml_read.sh` — нужно добавить или заменить на NodeYaml CLI. | 2 |
| **T7** | yq удаление: add-project.sh, remove-project.sh, project-list.sh (13 yq eval вызовов!) — замена yq eval на python3 NodeYaml CLI + mutation API для add-project/remove-project. | 3 |
| **T8** | node-resolver.sh → фасад: resolve_node_yaml → python3 NodeYaml.resolve(). Также заменить все call sites node-resolver.sh. | 1 |
| **T9.0** | **bootstrap.sh pre-migration**: заменить 5 вызовов yaml_helpers.py (node.owner_key, node.ci_deploy_key, domain, context, contexts.0.name) на NodeYaml CLI. Добавить bootstrap.sh в File Manifest MODIFY. | 2 |
| **T9** | yaml_helpers.py → удалить. Все вызовы заменены на NodeYaml (T9.0 + Wave 2). | 1 |
| **T10** | yaml_read.sh → фасад: yaml_read_key → python3 NodeYaml CLI. Проверить все call sites после T6.5. | 1 |

### Wave 4: Validation + Gate

| Task | Описание | Effort |
|------|----------|--------|
| **T11** | Gate test: test_node_yaml_single_source.py — fail если grep находит yaml.safe_load(node) вне NodeYaml | 1 |
| **T12** | Gate test: test_no_yq_dependency.py — fail если grep находит yq eval | 1 |
| **T13** | make fix-gate + make gate MODE=fast + pytest tests/ -v | 1 |

### Mutations / Removals (M-tasks)

| Task | Описание | Effort |
|------|----------|--------|
| **M6** | test_yaml_helpers.py — удалить или заменить на test_node_yaml_migration_compat.py (проверка, что NodeYaml CLI даёт те же результаты) | 1 |
| **M9** | Обновление документации: AGENTS.md, core/AGENTS.md — обновить описание NodeYaml facade, убрать упоминания yq, yaml_helpers.py, node-resolver.sh. | 1 |

---

## §4. File Manifest

### CREATE (2)
| Файл | Назначение |
|------|-----------|
| `tests/unit/test_node_yaml_full.py` | Unit-тесты полного NodeYaml API (30+ тестов) |
| `tests/unit/test_node_yaml_mutation.py` | Unit-тесты mutation API (add/remove/update project) |

### MODIFY (до ~25, уточняется после T4.5)
| Файл | Изменение |
|------|----------|
| `core/internal/shared/node_yaml.py` | Расширение API: 36+ полей с typed sub-dataclasses, resolve(), validate(), mutation API |
| `core/internal/scaffold/project_adopter.py` | yaml.safe_load → NodeYaml |
| `core/internal/reconciler_projects.py` | yaml.safe_load → NodeYaml (line 133) |
| `core/internal/bootstrap/overlay_deliverer.py` | resolve_node_yaml → NodeYaml.resolve() |
| `core/internal/verify/domain_verifier.py` | resolve_node_yaml() → NodeYaml.resolve() |
| `core/internal/scaffold/add-project.sh` | yq eval → NodeYaml CLI + mutation API |
| `core/internal/scaffold/remove-project.sh` | yq eval → NodeYaml CLI + mutation API |
| `core/internal/scaffold/project-list.sh` | yq eval (13 вызовов) → NodeYaml CLI |
| `core/lib/node-resolver.sh` | → фасад, python3 NodeYaml.resolve() |
| `core/lib/yaml_read.sh` | → фасад, python3 NodeYaml CLI |
| `core/entrypoints/bootstrap.sh` | yaml_helpers.py (5 вызовов) → NodeYaml CLI |
| `core/internal/bootstrap/issue-cert.sh` | yaml_read.sh → NodeYaml CLI |
| `core/internal/bootstrap/node-lifecycle.sh` | yaml_read.sh → NodeYaml CLI |
| `core/internal/deploy/deploy-project.sh` | yaml_read.sh → NodeYaml CLI |

### DELETE (2)
| Файл | Причина |
|------|---------|
| `core/internal/bootstrap/yaml_helpers.py` | Все потребители мигрированы на NodeYaml (T9.0 + Wave 2) |
| `tests/unit/test_yaml_helpers.py` | Заменён на test_node_yaml_migration_compat.py (M6) |

### НЕ МОДИФИЦИРУЮТСЯ (ложные потребители из предыдущей версии)

Эти файлы НЕ читают node.yaml — они используют другие YAML-источники:
- `secrets_validator.py` — читает secrets.yaml
- `discover_modules.py` — читает compose.yaml + module.yaml
- `template_engine.py` — читает template-manifest.yaml
- `generate_entrypoint_manifest.py` — читает entrypoint-manifest.yaml
- `generate_agents_md.py` — читает entrypoint-manifest.yaml, генерирует AGENTS.md
- `reconcile-projects.sh` — тонкий фасад над Python reconciler (не использует yq)

---

## §5. Design Decisions

### DD1: Почему typed dataclass, а не dict?
20+ потребителей обращаются к полям node.yaml с разными именами и assumptions. typed dataclass:
- IDE autocomplete для всех полей
- Статическая проверка типов (mypy)
- Документированный контракт (docstring на каждое поле)
- Защита от опечаток: node.contxet → AttributeError на этапе написания, не runtime

### DD2: Почему validate() опционально (по умолчанию True)?
Принудительная валидация может сломать обратную совместимость для старых node.yaml с устаревшими полями. Потребители, которые хотят strict validation, передают validate=True (default). Миграционные скрипты могут передать validate=False. Через 2 релиза validate станет обязательным.

### DD3: Почему удаление yq, а не обёртка?
yq — внешняя зависимость (Go binary), требующая установки через brew/apt. Разная семантика парсинга с PyYAML (анкоры, теги, multi-doc). NodeYaml CLI на Python использует тот же PyYAML, что и все Python-потребители → guaranteed consistency. Удаление yq устраняет внешнюю зависимость.

### DD4: resolve_node_yaml — почему Python, не shell?
Три текущие реализации идентичны по логике (3-path поиск). Python-версии уже существуют в overlay_deliverer.py и domain_verifier.py:109. Shell-версия в node-resolver.sh добавляет латентность (subprocess) и не имеет тестов. NodeYaml.resolve() становится единственной — с unit-тестами.

### DD5: Почему для validate() использовать core/schemas/node.schema.json, а не генерировать из dataclass?
Дублирование schema → drift. JSON Schema в core/schemas/ — authoritative source, уже используется платформой (validate.sh). NodeYaml.validate() грузит ту же schema. Если dataclass и schema расходятся → gate test обнаруживает. **НЕ создавать новый node_yaml_schema.json** — использовать существующий.

### DD6: Почему mutation API не часть T1?
Mutation API (add/remove/update project) — отдельная ответственность с write-back, блокировками и тестами. T1 фокусируется на read path (typed dataclasses). T3.5 добавляет write path.

### DD7: Почему yq удаление — mutation API, а не ruamel.yaml напрямую в shell?
add-project.sh и remove-project.sh используют `yq eval -i` для in-place мутации YAML. Замена на `python3 -c` с NodeYaml mutation API гарантирует: (1) тот же PyYAML парсер, (2) typed ProjectEntry валидация при добавлении, (3) единый write-back механизм. ruamel.yaml используется под капотом NodeYaml для сохранения YAML-комментариев. Shell-скрипты становятся тонкими фасадами.

### DD8: Почему 3 resolve_node_yaml, а не 2?
domain_verifier.py:109 содержит полную 3-path реализацию resolve_node_yaml() без NodeYaml. Это третья независимая реализация, обнаруженная при аудите (out-of-band). Все три должны быть заменены на единый NodeYaml.resolve() для гарантии консистентности.

### DD9: Почему bootstrap.sh — отдельный T9.0?
bootstrap.sh использует 5 вызовов yaml_helpers.py для извлечения ключей node.yaml. Удаление yaml_helpers.py (T9) без предварительной миграции bootstrap.sh сломает bootstrap pipeline. T9.0 выполняется до T9 и может быть выполнен параллельно с Wave 2.

### DD10: Почему T4.5 (аудит) перед T5?
Предыдущая версия DevPlan ошибочно включила 5 файлов, которые НЕ читают node.yaml (secrets_validator.py, discover_modules.py, template_engine.py, generate_entrypoint_manifest.py, generate_agents_md.py). Эти файлы используют другие YAML-источники (secrets.yaml, compose.yaml, template-manifest.yaml, entrypoint-manifest.yaml). T4.5 предотвращает повторение ошибки путём явной классификации потребителей перед миграцией.

---

## §6. Implementation Commands

```
# === WAVE 1: NodeYaml API ===
coder implement DevPlan 088 Wave 1:
  T1 (расширить NodeYaml: 36+ полей с typed sub-dataclasses по schema),
  T2 (NodeYaml.resolve(): 3-path resolution — унифицировать 3 реализации),
  T2.5 (domain_verifier.py: migrate resolve_node_yaml → NodeYaml.resolve()),
  T3 (NodeYaml.validate(): использовать core/schemas/node.schema.json),
  T3.5 (NodeYaml mutation API: add_project, remove_project, update_project),
  T4a (typed props: 15 тестов), T4b (resolve: 5 тестов), T4c (validate: 5 тестов), T4d (mutation: 8 тестов), T4e (CLI + parity: 8 тестов)

# Verify Wave 1
python3 -m pytest tests/unit/test_node_yaml_full.py tests/unit/test_node_yaml_mutation.py -v

# === WAVE 2: Consumer Audit + Python Migration ===
coder implement DevPlan 088 Wave 2:
  T4.5 (аудит: классифицировать все yaml.safe_load — node.yaml vs другие YAML),
  T5 (миграция Python consumers — только реальные: cert_collector, platform_export_metrics,
      monitoring_config_renderer, sync_env_defaults, gen_env_platform,
      generate_secrets_manifest, project_adopter, vhost_renderer),
  T6 (миграция оставшихся Python consumers — уточняются в T4.5)

# Verify Wave 2
grep -rn "yaml\.safe_load.*node" core/internal/ | grep -v node_yaml.py | grep -v "# LEGACY"
# Expected: empty (или только закомментированные legacy references)

# === WAVE 3: Shell → Python + Bootstrap Migration ===
coder implement DevPlan 088 Wave 3:
  T6.5 (yaml_read.sh callers: issue-cert.sh, node-lifecycle.sh, deploy-project.sh),
  T7 (yq → python3 CLI + mutation API: add-project.sh, remove-project.sh, project-list.sh),
  T8 (node-resolver.sh фасад → NodeYaml.resolve()),
  T9.0 (bootstrap.sh pre-migration: 5 вызовов yaml_helpers.py → NodeYaml CLI),
  T9 (удалить yaml_helpers.py),
  T10 (yaml_read.sh фасад → NodeYaml CLI)

# Verify Wave 3
grep -rn "yq.*eval" core/
# Expected: empty
grep -rn "yaml_helpers" core/
# Expected: empty (кроме legacy notes)
grep -rn "yaml_read_key\|yaml_read_domain_config" core/lib/ core/internal/ --include="*.sh" | grep -v yaml_read.sh
# Expected: empty (все заменены на NodeYaml CLI)

# === WAVE 4: Gate + Cleanup ===
coder implement DevPlan 088 Wave 4:
  M6 (test_yaml_helpers.py → удалить/заменить),
  M9 (обновить AGENTS.md — убрать yq, yaml_helpers, node-resolver),
  T11 (gate: single source),
  T12 (gate: no yq),
  T13 (fix-gate + gate)

# Final verification
make fix-gate && make gate MODE=fast
python3 -m pytest tests/ -v
```

$END_DEVPLAN
