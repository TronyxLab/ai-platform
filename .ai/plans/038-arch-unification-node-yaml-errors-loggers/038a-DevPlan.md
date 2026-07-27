$START_DEVPLAN

# DevPlan 038a — Wave 1: Unified NodeYaml Facade + Typed Exceptions

## $ARTIFACT_CONTRACT

| Поле | Значение |
|------|---------|
| **PURPOSE** | Детальный план реализации Wave 1 DevPlan 038 — проектирование API фасада `NodeYaml`, иерархии исключений, CLI-интерфейса, unit-тестов и пошаговой миграции 36 consumers на новый API |
| **DESCRIPTION** | Реализация класса `NodeYaml` (lazy-load + cache, 11 методов), иерархии из 5 типизированных исключений, CLI-интерфейса для shell consumers, 20+ unit-тестов, и миграция 26 Python + ~10 shell файлов. Используются актуальные пути post-DevPlan 079 (lifecycle/, deploy/, converge/) |
| **RATIONALE** | Wave 1 — фундамент архитектурной унификации. Без единого фасада невозможно системно решить проблемы W2-W5. Lazy-load + cache даёт 99% reduction worst-case healthcheck. Dotted-key API устраняет nested dict boilerplate. Typed exceptions обеспечивают fail-fast и различие recoverable/fatal errors |
| **ACCEPTANCE_CRITERIA** | AC1: grep yaml.safe_load → только в yaml_query.py:_load_yaml и node_yaml.py. AC2: make gate MODE=fast passes. AC3: Все существующие тесты проходят. AC4: CLI выдаёт валидный JSON для --get/--items. AC5: 20+ unit-тестов, покрытие ≥90% |
| **IMPLEMENTS** | Brief 038a — Wave 1: Unified NodeYaml Facade + Typed Exceptions |
| **IMPACTS** | `core/internal/shared/{exceptions,node_yaml}.py`, `tests/unit/test_{node_yaml_facade,exceptions}.py`, 26 Python-файлов, ~10 shell-файлов |
| **REQUIRES** | DevPlan 038 (02-DevPlan.md) — blueprint, Brief 038a (038a-Brief.md), DevPlan 070/079 — COMPLETED, Python 3.10+, PyYAML, pytest |

---

## Design Decisions

### DD1: Почему расширение существующего `shared/node_yaml.py`, а не новый файл?

## @rationale
**Q:** `core/internal/shared/node_yaml.py` уже существует (67 строк, `extract_context_from_node_yaml`). Почему не создать новый `unified_node_yaml.py`?
**A:** Расширение существующего файла минимизирует breaking changes. `extract_context_from_node_yaml()` остаётся как backward-compat alias → `NodeYaml(path).get_context()` + DeprecationWarning. Все существующие imports не ломаются. Имя файла соответствует ответственности — «node_yaml» = чтение node.yaml.

### DD2: Почему lazy-load, а не eager?

## @rationale
**Q:** Почему не читать файл в конструкторе `NodeYaml(path)`?
**A:** Lazy-load позволяет создать экземпляр без I/O (конструктор — pure). Файл читается при первом `.get()` / `.load()`. Это критично для сценариев, где NodeYaml создаётся условно (например, в `preflight.py` — создаётся всегда, но читается только если preflight проходит). Eager load привёл бы к ненужному I/O в 30% случаев.

### DD3: Почему CLI через `python3 -m`, а не отдельный entrypoint?

## @rationale
**Q:** Почему CLI реализован как `if __name__ == "__main__"` в `node_yaml.py`, а не отдельный `core/entrypoints/node_yaml_cli.py`?
**A:** `python3 -m core.internal.shared.node_yaml --file X --get Y` — стандартный Python-паттерн. Фасад и CLI в одном файле = single source of truth. При изменении API фасада CLI обновляется автоматически. Отдельный entrypoint создал бы drift risk (CLI отстаёт от API). Shell consumers уже используют `python3 -m core.internal.scripts.yaml_query` — такой же паттерн.

### DD4: Почему 5 классов исключений, а не 3?

## @rationale
**Q:** Почему не ограничиться `PlatformError(base)` + `ConfigError(parse/not found)` + `PlatformFatalError` = 3 класса?
**A:** Separation of concerns:
- `ConfigNotFoundError` (exit_code=2) vs `ConfigParseError` (exit_code=3) — принципиально разные recoverability. Not found → можно создать файл. Parse error → нужно исправить YAML-синтаксис.
- `ConfigValidationError` (exit_code=4) vs `ConfigParseError` — валидация структуры (missing key, wrong type) ≠ ошибка парсинга. Caller может по-разному обрабатывать.
- `PlatformFatalError` (exit_code=10) — невосстановимая ошибка, требующая ручного вмешательства.

---

## Architecture Overview

### Component Diagram

```
┌─────────────────────────────────────────────────────────────┐
│  core/internal/shared/                                       │
│                                                              │
│  ┌─ exceptions.py (NEW) ──────────────────────────────────┐ │
│  │  PlatformError(exit_code=1)                             │ │
│  │  ├── ConfigNotFoundError(exit_code=2)                   │ │
│  │  ├── ConfigParseError(exit_code=3)                      │ │
│  │  ├── ConfigValidationError(exit_code=4)                  │ │
│  │  └── PlatformFatalError(exit_code=10)                   │ │
│  └────────────────────────────────────────────────────────┘ │
│                                                              │
│  ┌─ node_yaml.py (EXTENDED: 67→~350 LOC) ─────────────────┐ │
│  │  class NodeYaml:                                        │ │
│  │    __init__(path)         # Lazy, no I/O                │ │
│  │    _load() → dict         # Internal: open + parse      │ │
│  │    load() → dict          # Public: force load          │ │
│  │    reload() → dict        # Invalidate cache + reload   │ │
│  │    get(key, default)      # Dotted-key access           │ │
│  │    get_list(key) → list   # Typed list access           │ │
│  │    get_context() → str    # context or contexts[0].name │ │
│  │    get_projects() → list  # projects array              │ │
│  │    get_modules() → list   # modules array               │ │
│  │    get_domain_config()    # → DomainConfig NamedTuple   │ │
│  │    get_node_info()        # → NodeInfo NamedTuple       │ │
│  │    validate() → list[str] # Structural validation       │ │
│  │    raw() → dict           # Raw data access (compat)    │ │
│  │                                                         │ │
│  │  # Backward-compat aliases (deprecated)                 │ │
│  │  extract_context_from_node_yaml(path, log_tag) → str    │ │
│  │                                                         │ │
│  │  # CLI (if __name__ == "__main__")                      │ │
│  │  --file PATH  --get KEY  [--items]  [--default VAL]    │ │
│  │  --file PATH  --domain-config                           │ │
│  │  --file PATH  --context                                  │ │
│  │  --file PATH  --validate                                 │ │
│  └────────────────────────────────────────────────────────┘ │
└──────────────────────┬──────────────────────────────────────┘
                       │
     ┌─────────────────┼─────────────────┐
     │                 │                 │
┌────┴─────┐   ┌───────┴───────┐   ┌────┴──────────┐
│ 26 Python │   │ yaml_read.sh  │   │ 8 shell       │
│ consumers │   │ (lib wrapper) │   │ entrypoints    │
│           │   │               │   │                │
│ NodeYaml( │   │ yaml_get_field│   │ node-resolver  │
│   path)   │   │ yaml_get_list │   │ verify-domains │
│ .load()   │   │ yaml_read_    │   │ remove-project │
│ .get()    │   │   domain_conf │   │ adopt-project  │
│ .get_     │   │               │   │ generate-cat   │
│  context()│   │               │   │ on-project-dep │
└───────────┘   └───────────────┘   └────────────────┘
```

### Data Flow

```
┌─ Caller ─┐     ┌─ NodeYaml Facade ───────────────┐     ┌─ File System ─┐
│           │     │                                  │     │                │
│  .get()   │────▶│ 1. Lazy? _data is None?         │     │                │
│           │     │    YES → open() + yaml.safe_load │────▶│  node.yaml     │
│           │     │    NO  → use cache               │     │                │
│           │     │ 2. Traverse dotted key           │     │                │
│           │◀────│ 3. Return value or default       │     │                │
│           │     │ 4. On error → raise PlatformErr  │     │                │
└───────────┘     └──────────────────────────────────┘     └────────────────┘
```

---

## API Contract

### Class: `NodeYaml`

```python
class NodeYaml:
    """
    Unified facade for reading ai-platform node.yaml configuration.

    GREP_SUMMARY: NodeYaml, yaml-facade, lazy-load, cache, dotted-keys, config-reader
    STRUCTURE: ▶ NodeYaml(path) → ◇ _load() → ◇ cache → ◇ get(key)/get_list(key)/... → ⎋ typed result | raise PlatformError

    Usage:
        node = NodeYaml("/etc/platform/node.yaml")
        host = node.get("node.host", default="localhost")
        ctx = node.get_context()
        projects = node.get_projects()

    Lazy loading: file is read on first access, not in constructor.
    Cache: parsed data is cached until reload() is called.
    """

    def __init__(self, path: str) -> None:
        """
        Constructor. Does NOT read the file (lazy).
        Args:
            path: Absolute path to node.yaml
        """
        ...

    def _load(self) -> dict:
        """
        Internal. Reads and parses YAML file.
        Raises:
            ConfigNotFoundError: file does not exist
            ConfigParseError: YAML syntax error or non-dict root
        Returns:
            Parsed dict (never None, empty dict if file is empty/None)
        [IMP:8][NodeYaml] Loading node.yaml from {path}
        [IMP:9][NodeYaml] Failed to parse node.yaml: {error}
        """
        ...

    # region Public API

    def load(self) -> dict:
        """
        Force load (or return cached). Idempotent.
        Returns: parsed dict
        [IMP:8][NodeYaml] Loaded node.yaml ({size} bytes)
        """
        ...

    def reload(self) -> dict:
        """
        Invalidate cache and reload from disk.
        Use after external modification (register/deregister project).
        Returns: freshly parsed dict
        [IMP:8][NodeYaml] Reloaded node.yaml ({size} bytes)
        """
        ...

    def get(self, key: str, default: Any = None) -> Any:
        """
        Dotted-key access to YAML value.
        Args:
            key: Dotted path (e.g., "node.host", "domain.platform")
            default: Value to return if key not found.
                     If default is None AND key not found → ConfigValidationError
                     (explicit default=None is treated as "no default")
        Returns: value at key path, or default
        Raises:
            ConfigValidationError: key not found and default not provided
            ConfigParseError: intermediate node is not a dict
        Examples:
            node.get("node.host") → "1.2.3.4"
            node.get("node.host", default="localhost") → "1.2.3.4"
            node.get("nonexistent", default="fallback") → "fallback"
            node.get("nonexistent") → raises ConfigValidationError
        [IMP:7][NodeYaml] get({key}) → {type}
        """
        ...

    def get_list(self, key: str) -> list:
        """
        Typed list access. Guarantees return type is list.
        Args:
            key: Dotted path to a list value
        Returns: list (empty list if key not found)
        Raises:
            ConfigValidationError: value exists but is not a list
        [IMP:7][NodeYaml] get_list({key}) → list[{len}]
        """
        ...

    def get_context(self) -> str:
        """
        Extract context name from node.yaml.
        Priority:
          1. Top-level 'context' field (string)
          2. 'contexts' array → first element's 'name' field (dict) or value (string)
          3. Empty string if neither found
        Returns: context name or ""
        [IMP:8][NodeYaml] Context: {ctx}
        """
        ...

    def get_projects(self) -> list[dict]:
        """
        Get projects list from node.yaml.
        Returns: list of project dicts (empty list if 'projects' key missing)
        Raises:
            ConfigValidationError: 'projects' exists but is not a list
        [IMP:7][NodeYaml] Projects: {count}
        """
        ...

    def get_modules(self) -> list[dict]:
        """
        Get modules list from node.yaml.
        Returns: list of module dicts (empty list if 'modules' key missing)
        Raises:
            ConfigValidationError: 'modules' exists but is not a list
        [IMP:7][NodeYaml] Modules: {count}
        """
        ...

    def get_domain_config(self) -> "DomainConfig":
        """
        Extract domain configuration as a typed NamedTuple.
        Returns: DomainConfig(platform_domain, email, acme_dns_plugin, project_domains)
        [IMP:8][NodeYaml] Domain config: {platform_domain}
        """
        ...

    def get_node_info(self) -> "NodeInfo":
        """
        Extract node metadata as a typed NamedTuple.
        Returns: NodeInfo(fqdn, owner_key, docker_mirror)
        [IMP:8][NodeYaml] Node info: {fqdn}
        """
        ...

    def validate(self) -> list[str]:
        """
        Validate node.yaml structure.
        Checks:
          - 'node' section exists
          - 'node.host' is non-empty
          - 'domain' section exists
          - 'domain.platform' is non-empty
        Returns: list of error messages (empty = valid)
        [IMP:8][NodeYaml] Validation: {len(errors)} errors
        """
        ...

    def raw(self) -> dict:
        """
        Access raw parsed dict for backward compatibility.
        Use sparingly — prefer typed getters.
        Returns: parsed dict
        """
        ...

    # endregion Public API
```

### NamedTuples

```python
from typing import NamedTuple

class DomainConfig(NamedTuple):
    """Typed domain configuration from node.yaml."""
    platform_domain: str = ""
    email: str = ""
    acme_dns_plugin: str = ""
    project_domains: list[str] = []

class NodeInfo(NamedTuple):
    """Typed node metadata from node.yaml."""
    fqdn: str = ""
    owner_key: str = ""
    docker_mirror: str = ""
```

### Backward-Compat Alias

```python
def extract_context_from_node_yaml(node_yaml_path: str, log_tag: str = "context") -> str:
    """
    DEPRECATED: Use NodeYaml(path).get_context() instead.
    Maintained for backward compatibility during migration.
    """
    import warnings
    warnings.warn(
        "extract_context_from_node_yaml() is deprecated. "
        "Use NodeYaml(path).get_context() instead.",
        DeprecationWarning,
        stacklevel=2,
    )
    return NodeYaml(node_yaml_path).get_context()
```

---

## Typed Exception Hierarchy

### `core/internal/shared/exceptions.py`

```python
# GREP_SUMMARY: exceptions, platform-error, typed-exceptions, exit-codes, config-errors
# STRUCTURE: ▶ PlatformError(base) → ◇ ConfigNotFoundError(2) / ConfigParseError(3) / ConfigValidationError(4) / PlatformFatalError(10)

class PlatformError(Exception):
    """Base exception for all platform errors.
    All subclasses carry an exit_code for CLI/shell compatibility.
    """
    exit_code: int = 1

class ConfigNotFoundError(PlatformError):
    """Configuration file not found (ENOENT). Recoverable: file can be created."""
    exit_code: int = 2

class ConfigParseError(PlatformError):
    """Configuration parse error (YAML syntax, JSON decode, non-dict root).
    Recoverable: fix the syntax."""
    exit_code: int = 3

class ConfigValidationError(PlatformError):
    """Configuration structure validation error (missing required key, wrong type).
    Recoverable: add the missing key or fix the type."""
    exit_code: int = 4

class PlatformFatalError(PlatformError):
    """Non-recoverable platform error (root required, preconditions violated).
    Requires manual intervention."""
    exit_code: int = 10
```

### Exception Usage in NodeYaml

| Метод | Ситуация | Исключение |
|-------|----------|------------|
| `_load()` | `FileNotFoundError` при `open()` | `ConfigNotFoundError(f"node.yaml not found: {path}")` |
| `_load()` | `yaml.YAMLError` при `safe_load()` | `ConfigParseError(f"YAML parse error in {path}: {e}")` |
| `_load()` | Результат парсинга — не dict | `ConfigParseError(f"node.yaml root is not a dict: {type(data)}")` |
| `get(key)` | Ключ не найден, default не задан | `ConfigValidationError(f"Key not found: {key}")` |
| `get(key)` | Промежуточный узел не dict | `ConfigValidationError(f"Cannot traverse into non-dict at {partial_key}")` |
| `get_list(key)` | Значение не list | `ConfigValidationError(f"'{key}' is not a list: {type(value)}")` |
| `get_projects()` | `projects` не list | `ConfigValidationError(f"'projects' is not a list: {type(value)}")` |
| `get_modules()` | `modules` не list | `ConfigValidationError(f"'modules' is not a list: {type(value)}")` |

---

## CLI Interface

### Usage

```bash
# Basic dotted-key access (outputs value as string)
python3 -m core.internal.shared.node_yaml --file /etc/platform/node.yaml --get node.host
# → 1.2.3.4

# With default value
python3 -m core.internal.shared.node_yaml --file node.yaml --get nonexistent --default "fallback"
# → fallback

# List items as JSON array (for shell parsing)
python3 -m core.internal.shared.node_yaml --file node.yaml --get projects --items
# → [{"name": "app1", "domain": "app1.example.com"}, ...]

# Domain config (outputs field:value lines for shell parsing)
python3 -m core.internal.shared.node_yaml --file node.yaml --domain-config
# → platform_domain:example.com
# → email:admin@example.com
# → acme_dns_plugin:cloudflare
# → project_domains:app1.example.com app2.example.com

# Context extraction (outputs context string)
python3 -m core.internal.shared.node_yaml --file node.yaml --context
# → myorg

# Validation (outputs errors to stderr, exit code = count of errors)
python3 -m core.internal.shared.node_yaml --file node.yaml --validate
# → (no output if valid, exit 0)
# → ERROR: missing node.host (stderr, exit 1)
```

### CLI Argument Parser

```python
# argparse configuration (in if __name__ == "__main__")
parser = argparse.ArgumentParser(description="NodeYaml unified facade CLI")
parser.add_argument("--file", required=True, help="Path to node.yaml")
parser.add_argument("--get", help="Dotted key to retrieve (e.g., node.host)")
parser.add_argument("--default", help="Default value if key not found")
parser.add_argument("--items", action="store_true", help="Output list as JSON array")
parser.add_argument("--domain-config", action="store_true", help="Output domain config as field:value lines")
parser.add_argument("--json-output", action="store_true", help="Output entire YAML document as JSON")
parser.add_argument("--find-project", help="Find project by name and output JSON + org + host")
parser.add_argument("--context", action="store_true", help="Output context name")
parser.add_argument("--validate", action="store_true", help="Validate node.yaml structure")
```

### Exit Codes

| Exit Code | Ситуация |
|-----------|----------|
| 0 | Success (value found, validation passed) |
| 1 | Not found (key missing, project not found) AND Generic PlatformError |
| 2 | ConfigNotFoundError (file not found) |
| 3 | ConfigParseError (YAML syntax error) |
| 4 | ConfigValidationError (missing key, wrong type) |
| 10 | PlatformFatalError (unrecoverable) |

> **Note:** `--get` with missing key returns exit code 1 (not 4) for shell compatibility (`||` chaining). The Python API raises `ConfigValidationError` for missing keys (strict), but CLI uses exit code 1 so that `cmd --get X || fallback` works as expected in shell scripts.

### Output Formats

| Флаг | Формат вывода | Пример |
|------|--------------|--------|
| `--get` (без `--items`) | Plain string (значение) | `1.2.3.4` |
| `--get` + `--items` | JSON array | `[{"name": "app1"}, ...]` |
| `--domain-config` | `field:value` lines (для shell parsing) | `platform_domain:example.com` |
| `--json-output` | JSON (весь документ) | `{"node": {"host": "1.2.3.4"}, ...}` |
| `--find-project <name>` | JSON проекта + `___ORG___<org>` + `___HOST___<host>` | `{"name": "app1", ...}\n___ORG___myorg\n___HOST___1.2.3.4` |
| `--context` | Plain string | `myorg` |
| `--validate` | Errors на stderr (по одной на строку) | `ERROR: missing node.host` |

---

## Migration Guide

### Pattern 1: Python — прямой `yaml.safe_load` → `NodeYaml`

```python
# ДО:
import yaml
with open(node_yaml_path) as f:
    data = yaml.safe_load(f) or {}
host = data.get("node", {}).get("host", "")

# ПОСЛЕ:
from core.internal.shared.node_yaml import NodeYaml
node = NodeYaml(node_yaml_path)
host = node.get("node.host", default="")
```

**Особый случай — `or {}`:**
```python
# ДО:
data = yaml.safe_load(f) or {}  # None-safe

# ПОСЛЕ:
node = NodeYaml(path)
# NodeYaml._load() гарантированно возвращает dict (пустой если yaml.safe_load → None)
```

**Особый случай — try/except вокруг чтения:**
```python
# ДО:
try:
    with open(path) as f:
        data = yaml.safe_load(f)
except (FileNotFoundError, yaml.YAMLError) as e:
    logger.error(f"Failed: {e}")
    return []

# ПОСЛЕ:
from core.internal.shared.exceptions import ConfigNotFoundError, ConfigParseError
try:
    node = NodeYaml(path)
    projects = node.get_projects()
except (ConfigNotFoundError, ConfigParseError) as e:
    logger.error(f"Failed: {e}")
    return []
```

### Pattern 2: Shell — inline `python3 -c "import yaml"` → CLI

```bash
# ДО:
host=$(python3 -c "
import yaml
with open('$NODE_YAML') as f:
    data = yaml.safe_load(f)
print(data.get('node', {}).get('host', ''))
")

# ПОСЛЕ:
host=$(python3 -m core.internal.shared.node_yaml --file "$NODE_YAML" --get node.host --default "")
```

### Pattern 3: `yaml_read.sh` functions → CLI wrappers

```bash
# ДО (yaml_read.sh):
yaml_read_domain_config() {
    python3 - <<'PYEOF'
import yaml, sys
with open(sys.argv[1]) as f:
    data = yaml.safe_load(f)
domain = data.get("domain", {})
print(f"PLATFORM_DOMAIN={domain.get('platform', '')}")
...
PYEOF
}

# ПОСЛЕ (yaml_read.sh):
yaml_read_domain_config() {
    python3 -m core.internal.shared.node_yaml --file "${1:-$NODE_YAML_PATH}" --domain-config
}
```

### Pattern 4: `NODE_YAML_PATH` env var → explicit path

```python
# ДО:
node_yaml_path = os.environ.get("NODE_YAML_PATH", "/etc/platform/node.yaml")
with open(node_yaml_path) as f:
    data = yaml.safe_load(f)

# ПОСЛЕ:
from core.internal.shared.node_yaml import NodeYaml
node = NodeYaml(node_yaml_path)  # path вычисляется так же, но чтение — через фасад
```

### Pattern 5: `yaml_get_field` / `yaml_get_list` / `yaml_get` → CLI

```bash
# ДО:
domain=$(yaml_get_field "$NODE_YAML_PATH" domain.platform)
modules=$(yaml_get_list "$NODE_YAML_PATH" modules)

# ПОСЛЕ:
domain=$(python3 -m core.internal.shared.node_yaml --file "$NODE_YAML_PATH" --get domain.platform --default "")
modules=$(python3 -m core.internal.shared.node_yaml --file "$NODE_YAML_PATH" --get modules --items)
```

---

## Tasks

### T1.1 — Создать `exceptions.py`

| Параметр | Значение |
|----------|---------|
| **Описание** | Создать `core/internal/shared/exceptions.py` с 5 классами исключений |
| **Файлы** | 1 (NEW): `core/internal/shared/exceptions.py` |
| **Сложность** | 2 |
| **Зависимости** | None |
| **AC** | Модуль импортируется без ошибок, все 5 классов созданы, каждый имеет атрибут `exit_code` с правильным значением |

**Проверка:**
```bash
python3 -c "
from core.internal.shared.exceptions import (
    PlatformError, ConfigNotFoundError, ConfigParseError,
    ConfigValidationError, PlatformFatalError
)
assert ConfigNotFoundError.exit_code == 2
assert ConfigParseError.exit_code == 3
assert ConfigValidationError.exit_code == 4
assert PlatformFatalError.exit_code == 10
assert issubclass(ConfigNotFoundError, PlatformError)
print('OK: all 5 exceptions with correct exit codes')
"
```

### T1.2 — Расширить `node_yaml.py`: класс `NodeYaml`

| Параметр | Значение |
|----------|---------|
| **Описание** | Расширить `core/internal/shared/node_yaml.py`: класс `NodeYaml` с 11 методами + NamedTuples + backward-compat alias |
| **Файлы** | 1: `core/internal/shared/node_yaml.py` (67→~350 строк) |
| **Сложность** | 8 |
| **Зависимости** | T1.1 |
| **AC** | Все 11 методов реализованы с type hints, LDD-логами IMP:7-9, docstrings с GREP_SUMMARY. `extract_context_from_node_yaml()` остаётся как deprecated alias с DeprecationWarning |

**Список методов с приоритетом реализации:**
1. `__init__`, `_load`, `load` — ядро (lazy load + cache)
2. `get`, `get_list` — базовый доступ (dotted keys)
3. `get_context`, `get_projects`, `get_modules` — семантические геттеры
4. `get_domain_config`, `get_node_info` — typed NamedTuples
5. `validate` — структурная валидация
6. `reload`, `raw` — утилиты

### T1.3 — Добавить CLI в `node_yaml.py`

| Параметр | Значение |
|----------|---------|
| **Описание** | Добавить `if __name__ == "__main__"` блок с argparse: `--file`, `--get`, `--domain-config`, `--context`, `--validate`, `--items`, `--default` |
| **Файлы** | 1: `core/internal/shared/node_yaml.py` |
| **Сложность** | 4 |
| **Зависимости** | T1.2 |
| **AC** | CLI запускается `python3 -m core.internal.shared.node_yaml --file X --get Y`. Все флаги работают. Exit codes соответствуют `PlatformError.exit_code`. `--get` без `--items` выводит plain string. `--get --items` выводит JSON array. `--domain-config` выводит `KEY=VALUE` lines |

### T1.4 — Unit-тесты

| Параметр | Значение |
|----------|---------|
| **Описание** | Написать 2 тестовых файла: `test_node_yaml_facade.py` (20+ тестов) и `test_exceptions.py` (3+ теста) |
| **Файлы** | 2 (NEW): `tests/unit/test_node_yaml_facade.py`, `tests/unit/test_exceptions.py` |
| **Сложность** | 6 |
| **Зависимости** | T1.3 |
| **AC** | 20+ тестов для NodeYaml, 3+ для exceptions. Покрытие ≥90%. Test honesty rules (R1-R5) соблюдены. LDD trajectory IMP:7-10 логируются в caplog |

**Test spec (21 тест NodeYaml):**

| # | Тест | Fixture | Проверка |
|---|------|---------|----------|
| 1 | `test_load_valid_yaml` | tmp_path + валидный node.yaml | `load()` → dict с ключами |
| 2 | `test_load_file_not_found` | tmp_path + несуществующий файл | `load()` → `ConfigNotFoundError` |
| 3 | `test_load_malformed_yaml` | tmp_path + битый YAML | `load()` → `ConfigParseError` |
| 4 | `test_load_non_dict_root` | tmp_path + YAML-список `[1,2,3]` | `load()` → `ConfigParseError` |
| 5 | `test_load_empty_file` | tmp_path + пустой файл | `load()` → `{}` |
| 6 | `test_load_none_yaml` | tmp_path + `null` YAML | `load()` → `{}` |
| 7 | `test_cache_hit` | tmp_path + mock `open` | Повторный `.load()` не вызывает `open()` |
| 8 | `test_reload_invalidates_cache` | tmp_path + изменяемый файл | `.reload()` → новые данные |
| 9 | `test_get_simple_key` | Валидный YAML | `.get("node.host")` → `"1.2.3.4"` |
| 10 | `test_get_nested_key` | Валидный YAML | `.get("domain.platform")` → `"example.com"` |
| 11 | `test_get_deeply_nested` | 3+ уровня | `.get("a.b.c")` → значение |
| 12 | `test_get_missing_key_no_default` | Валидный YAML | `.get("nonexistent")` → `ConfigValidationError` |
| 13 | `test_get_missing_key_with_default` | Валидный YAML | `.get("nonexistent", default="fb")` → `"fb"` |
| 14 | `test_get_list` | Валидный YAML с projects | `.get_list("projects")` → `list[dict]` |
| 15 | `test_get_list_not_a_list` | Валидный YAML | `.get_list("domain")` → `ConfigValidationError` |
| 16 | `test_get_list_missing_key` | Валидный YAML | `.get_list("nonexistent")` → `[]` |
| 17 | `test_get_context_string` | `context: "myorg"` | `.get_context()` → `"myorg"` |
| 18 | `test_get_context_array` | `contexts: [{name: "myorg"}]` | `.get_context()` → `"myorg"` |
| 19 | `test_get_context_empty` | Нет context/contexts | `.get_context()` → `""` |
| 20 | `test_get_projects` | Валидный YAML | `.get_projects()` → `list[dict]` с правильным количеством |
| 21 | `test_get_modules` | Валидный YAML | `.get_modules()` → `list[dict]` |

**Test spec (CLI тесты):**

| # | Тест | Проверка |
|---|------|----------|
| 22 | `test_cli_get` | subprocess `--file X --get node.host` → stdout = `"1.2.3.4"` |
| 23 | `test_cli_get_items` | subprocess `--get projects --items` → stdout = валидный JSON |
| 24 | `test_cli_domain_config` | subprocess `--domain-config` → stdout содержит `PLATFORM_DOMAIN=` |
| 25 | `test_cli_context` | subprocess `--context` → stdout = `"myorg"` |
| 26 | `test_cli_validate_valid` | subprocess `--validate` → exit 0 |
| 27 | `test_cli_validate_invalid` | subprocess `--validate` на битом YAML → exit 2 |
| 28 | `test_cli_file_not_found` | subprocess `--file /nonexistent --get x` → exit 2 |

**Test spec (exceptions):**

| # | Тест | Проверка |
|---|------|----------|
| 29 | `test_platform_error_exit_codes` | Каждый subclass имеет правильный `exit_code` |
| 30 | `test_exception_inheritance` | `ConfigNotFoundError` is `PlatformError` |
| 31 | `test_exception_message` | `ConfigNotFoundError("msg")` → `str(e) == "msg"` |
| 32 | `test_exception_catch_by_base` | `except PlatformError` ловит все подклассы |

### T1.5 — Миграция Python consumers

| Параметр | Значение |
|----------|---------|
| **Описание** | Заменить прямые `yaml.safe_load` для node.yaml в 26 Python-файлах на `NodeYaml(path)` |
| **Файлы** | 26 (список в Brief 038a §7) |
| **Сложность** | 10 |
| **Зависимости** | T1.4 |
| **AC** | `grep 'yaml.safe_load' core/internal/` → только `yaml_query.py:_load_yaml` и `node_yaml.py:NodeYaml._load`. Все Python-файлы импортируют `NodeYaml` из `core.internal.shared.node_yaml` |

**Порядок миграции (от наименьшего риска к наибольшему):**

| Batch | Файлы | Причина порядка |
|-------|-------|-----------------|
| Batch A (4) | `monitoring_config_renderer.py`, `gen_env_platform.py`, `vhost_yaml_reader.py`, `context_registry.py` | Scaffold — наименьший risk surface |
| Batch B (4) | `platform_export_metrics.py`, `cert_collector.py`, `project_collector.py`, `app.py` | Healthcheck/status-page — read-only consumers |
| Batch C (4) | `provisioner.py`, `reconciler_projects.py`, `policy_schema.py`, `project_registry.py` | Shared libs — много consumers |
| Batch D (6) | `preflight.py`, `yaml_helpers.py`, `s3_ssl_cache.py`, `compose_preflight.py`, `spool_validator.py`, `secrets_validator.py` | Bootstrap helpers — участвуют в CI |
| Batch E (5) | `secrets_manager.py`, `context_overlay.py`, `context_deployer.py`, `state_machine.py`, `steps.py` | Bootstrap core — CRITICAL, мигрировать осторожно |
| Batch F (3) | `reconciler.py`, `docker_orchestrator.py` (если есть yaml.safe_load), оставшиеся | Converge/verify |

**⚠️ TRAP[MIGRATION] · Файлы с несколькими точками чтения** — мигрировать все точки в одном файле за один проход, не оставлять смешанное состояние (часть через NodeYaml, часть через прямой yaml.safe_load).

**Файлы с >1 точкой:**
- `state_machine.py` — 3 точки
- `steps.py` — 2 точки
- `context_deployer.py` — 2 точки
- `context_overlay.py` — 2 точки
- `reconciler.py` — 3 точки
- `secrets_validator.py` — 8 точек
- `reconciler_projects.py` — 2 точки

### T1.6 — Обновить `yaml_read.sh`

| Параметр | Значение |
|----------|---------|
| **Описание** | Заменить тела функций в `core/lib/yaml_read.sh` на вызов CLI фасада (backward-compat обёртки) |
| **Файлы** | 1: `core/lib/yaml_read.sh` |
| **Сложность** | 3 |
| **Зависимости** | T1.3 |
| **AC** | `yaml_get_field`, `yaml_get_list`, `yaml_read_domain_config` работают через CLI фасада и возвращают те же значения |

**Функции для обновления:**
- `yaml_get_field()` → `python3 -m core.internal.shared.node_yaml --file "$1" --get "$2" --default "${3:-}"`
- `yaml_get_list()` → `python3 -m core.internal.shared.node_yaml --file "$1" --get "$2" --items`
- `yaml_read_domain_config()` → `python3 -m core.internal.shared.node_yaml --file "${1:-$NODE_YAML_PATH}" --domain-config`

### T1.7 — Миграция shell consumers (inline python3)

| Параметр | Значение |
|----------|---------|
| **Описание** | Заменить inline `python3 -c "import yaml"` в ~8 shell-файлах на вызов CLI фасада |
| **Файлы** | ~8: `node-resolver.sh`, `remove-project.sh`, `adopt-project.sh`, `verify-domains.sh`, `generate-catalog.sh`, `on-project-deploy.sh`, `validate.sh` (YAML-часть), `add-vhost.sh` |
| **Сложность** | 5 |
| **Зависимости** | T1.6 |
| **AC** | `grep -rn 'python3 -c.*import yaml' core/ --include='*.sh'` → 0 результатов (кроме комментариев) |

**Файлы, которые НЕ трогать (легитимные inline python3):**
- `python_deps.sh` — проверка наличия Python-модуля
- `install-docker.sh` — platform detection (не YAML)
- `validate.sh:97-107` — jsonschema валидация (не node.yaml, остаётся)

### T1.8 — Очистка `NODE_YAML_PATH` env var

| Параметр | Значение |
|----------|---------|
| **Описание** | Заменить использование `NODE_YAML_PATH` env var на явную передачу пути в `NodeYaml(path)` |
| **Файлы** | ~3: `converge.sh`, `add-vhost.sh` (export), `validate.sh` |
| **Сложность** | 4 |
| **Зависимости** | T1.5 |
| **AC** | `grep 'NODE_YAML_PATH' core/ --include='*.sh'` → только в `paths.sh` (определение константы) и `yaml_read.sh` (как default для backward-compat) |

### T1.9 — Gate verification

| Параметр | Значение |
|----------|---------|
| **Описание** | Запустить `make gate MODE=fast`, исправить регрессии |
| **Файлы** | N/A |
| **Сложность** | 3 |
| **Зависимости** | T1.5–T1.8 |
| **AC** | `make gate MODE=fast` passes. `python -m pytest tests/ -s -v` → all pass |

---

## Test Data Fixture

```yaml
# tests/test_data/node_yaml_valid.yaml
node:
  host: 1.2.3.4
  fqdn: node1.example.com
  owner_key: age1abc123
  docker_mirror: https://mirror.example.com
context: myorg
domain:
  platform: example.com
  email: admin@example.com
  acme_dns_plugin: cloudflare
projects:
  - name: app1
    domain: app1.example.com
    repo: git@github.com:org/app1.git
  - name: app2
    domain: app2.example.com
modules:
  - name: nginx
    enabled: true
  - name: postgres
    enabled: false
```

```yaml
# tests/test_data/node_yaml_contexts.yaml (array fallback test)
contexts:
  - name: myorg
    repo: git@github.com:myorg/ai-platform.git
node:
  host: 1.2.3.4
domain:
  platform: example.com
```

```yaml
# tests/test_data/node_yaml_invalid.yaml (malformed)
node:
  host: 1.2.3.4
domain:
  platform: example.com
projects: "not_a_list"   # ← validation error
```

---

## CI Gate Impact

### Existing gates affected

| Gate | Изменение |
|------|-----------|
| `check-no-new-inline-python3` | Добавить `core/internal/shared/node_yaml.py` в whitelist. После T1.7 — удалить из whitelist все мигрированные shell-файлы |
| `check-manifests` | Добавить entrypoint `node_yaml.py:CLI` в `entrypoint-manifest.yaml` (python3 -m mode) |
| `make gate MODE=fast` | Должен проходить после каждого task batch'а |

### Новые проверки (НЕ gates в рамках Wave 1, но проверочные команды)

```bash
# После T1.5 — проверка что все Python consumers мигрированы
grep -rn 'yaml.safe_load' core/internal/ --include='*.py' \
  | grep -v 'yaml_query.py' \
  | grep -v 'node_yaml.py'

# После T1.7 — проверка что нет inline python3 с import yaml
grep -rn 'python3 -c.*import yaml' core/ --include='*.sh'

# После T1.8 — проверка что NODE_YAML_PATH не используется для чтения
grep -rn 'NODE_YAML_PATH' core/ --include='*.py' --include='*.sh' \
  | grep -v 'paths.sh' \
  | grep -v 'yaml_read.sh'
```

---

## Implementation Sequence

```
T1.1 (exceptions.py)
  │
  ▼
T1.2 (NodeYaml class)
  │
  ▼
T1.3 (CLI)
  │
  ▼
T1.4 (unit tests)
  │
  ├──────────────────────┐
  ▼                      ▼
T1.5 (Python consumers)  T1.6 (yaml_read.sh)
Batch A (scaffold)         │
Batch B (healthcheck)      ▼
Batch C (shared libs)    T1.7 (shell consumers)
Batch D (bootstrap)        │
Batch E (bootstrap core)   ▼
Batch F (converge)       T1.8 (NODE_YAML_PATH cleanup)
  │                        │
  └────────┬───────────────┘
           ▼
       T1.9 (gate verification)
```

**Рекомендуемый порядок для Coder:**
1. Сначала T1.1 + T1.2 + T1.3 + T1.4 (core implementation + tests) — один PR
2. Затем T1.5 Batch A+B (проверить что тесты проходят)
3. T1.6 + T1.7 (shell migration) — можно параллельно с T1.5 Batch C
4. T1.5 Batch C+D+E+F (оставшиеся Python consumers) — последовательно
5. T1.8 (NODE_YAML_PATH cleanup)
6. T1.9 (финальная верификация)

---

## Risk Matrix (Wave 1 specific)

| # | Риск | Severity | Probability | Mitigation |
|---|------|----------|-------------|------------|
| R1 | `NodeYaml` API не покрывает edge-case существующего кода | HIGH | MEDIUM | Аудит всех 26 Python-файлов перед миграцией, сверка с test data fixture |
| R2 | Shell consumers ломаются из-за изменения формата вывода CLI | HIGH | LOW | `--domain-config` сохраняет `KEY=VALUE` формат. `--get` сохраняет plain string. `--items` сохраняет JSON array |
| R3 | `extract_context_from_node_yaml` consumers (3 файла) не обновлены на новый API | MEDIUM | LOW | DeprecationWarning в логах предупредит. grep по вызовам старой функции |
| R4 | Кэш не инвалидируется после register/deregister project | MEDIUM | MEDIUM | `reload()` обязателен после любой операции записи node.yaml. Тест `test_reload_invalidates_cache` |
| R5 | Lazy load создаёт гонку: файл изменён между конструктором и первым `.get()` | LOW | LOW | Однопоточный bootstrap pipeline. Если файл изменён — `reload()` при следующей операции |
| R6 | 26 файлов мигрируются не полностью — часть остаётся на старом API | MEDIUM | LOW | Grep gate после каждого batch'а. CI блокирует неполную миграцию |

---

## Rollback Plan

Если Wave 1 вызывает regression:

1. **Revert `node_yaml.py`** — откатить к версии из `git` (67 строк, только `extract_context_from_node_yaml`)
2. **Revert 26 Python consumers** — `git revert` коммиты миграции (batch revert)
3. **Revert `yaml_read.sh`** — восстановить старые тела функций
4. **Revert 8 shell consumers** — восстановить inline python3

**Время отката:** ~15 минут (один `git revert` всех коммитов Wave 1 + `make gate MODE=fast`)

**Частичный откат возможен:** если проблема только в одном batch'е (например, Batch E — state_machine), откатить только этот batch, остальные остаются.

---

## Verification Checklist

Перед мержем Wave 1:

- [ ] `python -m pytest tests/unit/test_node_yaml_facade.py tests/unit/test_exceptions.py -v` → все тесты pass
- [ ] `grep 'yaml.safe_load' core/internal/ --include='*.py' | grep -v yaml_query.py | grep -v node_yaml.py` → пусто
- [ ] `grep -rn 'python3 -c.*import yaml' core/ --include='*.sh'` → 0 (кроме комментариев)
- [ ] `grep -rn 'NODE_YAML_PATH' core/ --include='*.py' | grep -v 'os.environ.get\|os.getenv'` → 0 (только чтение env для определения пути)
- [ ] `make gate MODE=fast` → green
- [ ] `python -m pytest tests/ -s -v` → все существующие тесты pass
- [ ] `python3 -m core.internal.shared.node_yaml --file tests/test_data/node_yaml_valid.yaml --get node.host` → `1.2.3.4`
- [ ] `python3 -m core.internal.shared.node_yaml --file tests/test_data/node_yaml_valid.yaml --get projects --items | python3 -m json.tool` → валидный JSON
- [ ] `python3 -m core.internal.shared.node_yaml --file tests/test_data/node_yaml_valid.yaml --domain-config` → `PLATFORM_DOMAIN=example.com`
- [ ] `python3 -m core.internal.shared.node_yaml --file tests/test_data/node_yaml_valid.yaml --context` → `myorg`
- [ ] `python3 -m core.internal.shared.node_yaml --file tests/test_data/node_yaml_valid.yaml --validate; echo "exit=$?"` → `exit=0`
- [ ] `python3 -m core.internal.shared.node_yaml --file /nonexistent --get x; echo "exit=$?"` → `exit=2`
- [ ] DeprecationWarning выводится при вызове `extract_context_from_node_yaml(path)`

$END_DEVPLAN
