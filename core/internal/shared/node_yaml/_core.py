#!/usr/bin/env python3
# GREP_SUMMARY: node-yaml-core, NodeYamlCore, lazy-load, cache, dotted-keys, get, get-list, raw, write-back, atomic-writer, 119-H
# STRUCTURE: ▶ NodeYamlCore(path) → ◇ _load() lazy+cache → ◇ get(key)/get_list(key) dotted → ◇ raw() → ◇ _write_back(data) → atomic_writer → ⎋ dict | raise PlatformError
# region MODULE_CONTRACT
## @purpose  Ядро NodeYaml (DevPlan 119 H1) — lazy-load + cache + dotted-key доступ + атомарная
##           запись. НЕ содержит доменной бизнес-логики (contexts/projects/modules/node —
##           в миксинах node_yaml/{domains,projects,modules,node}.py).
## @scope    Базовый класс для NodeYaml-агрегатора (node_yaml/__init__.py). Потребители:
##           все ~21 файл, использующий NodeYaml.get()/get_list()/raw()/_write_back().
## @invariants
##   1. Lazy-load: __init__ НЕ читает файл. Первое чтение — на _load() или любом геттере.
##   2. Cache: parsed data кэшируется до reload().
##   3. Dotted-key access: get("node.host") traverses nested dicts.
##   4. _load() возвращает {} для пустого/None YAML, никогда None.
##   5. _load() бросает ConfigNotFoundError на FileNotFoundError.
##   6. _load() бросает ConfigParseError на YAMLError или non-dict root.
##   7. get(key) бросает ConfigValidationError когда ключ не найден И default is None.
##   8. get_list(key) возвращает [] на missing key, бросает ConfigValidationError если не list.
##   9. _write_back делегирует запись в shared/atomic_writer (tempfile+fsync+os.replace,
##      DevPlan 119 E5/H2) — ruamel-специфика рендера сохранена, атомарность — канон.
##   10. _write_back инвалидирует _data cache на успехе И на провале (T6.2) — мутации
##       работают на deepcopy, cache никогда не отравляется (TRAP 2026-07-30 fixed).
## @rationale DevPlan 119 H1: ядро вынесено отдельно от доменных миксинов (AC-H1.3 —
##            агрегатор <300 LOC core logic). Ленивость предотвращает I/O в 30% случаев
##            (preflight создаёт NodeYaml, но не читает). Dotted-key API устраняет boilerplate.
## @changes 2026-08-03 · DevPlan 119 H1 — извлечено из node_yaml.py (строки __init__/_load/
##           load/reload/get/get_list/raw/_write_back) в node_yaml/_core.py без изменения логики
## @changes 2026-08-01 · DevPlan 116 B6 T6 — _write_back deepcopy + cache invalidation (T6.1/T6.2)
## @changes 2026-07-30 · DevPlan 088 — LDD logs + region markers
## @changes 2026-07-26 · DevPlan 038a — NodeYaml class created
# endregion MODULE_CONTRACT

import logging
import os
from typing import Any

import yaml

# DevPlan 119 E5/H2: атомарная запись — единый канон shared/atomic_writer (tempfile+fsync+replace).
from core.internal.shared.atomic_writer import atomic_write_text as _atomic_write_text
from core.internal.shared.exceptions import (
    ConfigNotFoundError,
    ConfigParseError,
    ConfigValidationError,
)

logger = logging.getLogger(__name__)


# region CLASS_NodeYamlCore
class NodeYamlCore:
    """Ядро NodeYaml: lazy-load, cache, dotted-key доступ, атомарная запись (DevPlan 119 H1).

    GREP_SUMMARY: NodeYamlCore, lazy-load, cache, dotted-keys, get, get-list, raw, write-back
    STRUCTURE: ▶ __init__(path) → ◇ _load() → ◇ cache → ◇ get(key)/get_list(key) → ◇ raw() → ◇ _write_back(data) → ⎋ dict
    """

    # region FUNC___init__
    ## @purpose  Конструктор. НЕ читает файл (lazy).
    ## @io — ⇥ path: str → ⎋ None
    ## @complexity — O(1)
    ## @invariants  No I/O. _data is None until first access.
    def __init__(self, path: str) -> None:
        """Initialize NodeYaml with file path (no I/O — lazy).

        Args:
            path: Absolute path to node.yaml
        """
        self._path: str = path
        self._data: dict[str, Any] | None = None
        logger.info("[IMP:7][NodeYaml] Created NodeYaml for %s (lazy)", path)

    # endregion FUNC___init__

    # region FUNC__load
    ## @purpose  Internal. Reads and parses YAML file. Returns cached if available.
    ## @io — ⇥ open(path) → yaml.safe_load → ⎋ dict (never None)
    ## @complexity — O(N) for YAML parse
    ## @invariants
    ##   - Raises ConfigNotFoundError on FileNotFoundError
    ##   - Raises ConfigParseError on YAMLError or non-dict root
    ##   - Returns {} for empty/None YAML content
    def _load(self) -> dict:
        """Read and parse node.yaml from disk.

        Returns:
            Parsed dict (empty dict if file is empty or yaml.safe_load returns None)

        Raises:
            ConfigNotFoundError: file does not exist
            ConfigParseError: YAML syntax error or non-dict root
        """
        if self._data is not None:
            return self._data

        logger.info("[IMP:8][NodeYaml] Loading node.yaml from %s", self._path)
        try:
            with open(self._path) as f:
                raw = yaml.safe_load(f)
        except FileNotFoundError as e:
            logger.error("[IMP:9][NodeYaml] node.yaml not found: %s", self._path)
            raise ConfigNotFoundError(f"node.yaml not found: {self._path}") from e
        except yaml.YAMLError as e:
            logger.error("[IMP:9][NodeYaml] YAML parse error in %s: %s", self._path, e)
            raise ConfigParseError(f"YAML parse error in {self._path}: {e}") from e

        # Handle None/empty YAML
        if raw is None:
            self._data = {}
        elif isinstance(raw, dict):
            self._data = raw
        else:
            logger.error("[IMP:9][NodeYaml] node.yaml root is not a dict: %s", type(raw))
            raise ConfigParseError(f"node.yaml root is not a dict: {type(raw)}")

        size = os.path.getsize(self._path)
        logger.info("[IMP:8][NodeYaml] Loaded node.yaml (%d bytes)", size)
        return self._data

    # endregion FUNC__load

    # region FUNC_load
    ## @purpose  Force load (or return cached). Idempotent.
    ## @io — ⇥ → ⎋ dict
    ## @complexity — O(1) if cached, O(N) on first call
    def load(self) -> dict:
        """Force load node.yaml or return cached data.

        Returns:
            Parsed dict
        """
        return self._load()

    # endregion FUNC_load

    # region FUNC_reload
    ## @purpose  Invalidate cache and reload from disk.
    ## @io — ⇥ → ⎋ dict
    ## @complexity — O(N) for YAML parse
    def reload(self) -> dict:
        """Invalidate cache and reload node.yaml from disk.

        Use after external modification (register/deregister project).

        Returns:
            Freshly parsed dict
        """
        self._data = None
        data = self._load()
        size = os.path.getsize(self._path)
        logger.info("[IMP:8][NodeYaml] Reloaded node.yaml (%d bytes)", size)
        return data

    # endregion FUNC_reload

    # region FUNC_get
    ## @purpose  Dotted-key access to YAML value.
    ## @io — ⇥ key: str, default: Any = None → ⎋ Any | raise ConfigValidationError
    ## @complexity — O(D) where D = number of dot-separated segments
    ## @invariants
    ##   - key "node.host" traverses data["node"]["host"]
    ##   - Raises ConfigValidationError if key not found AND default is None
    ##   - Returns default if key not found AND default is not None
    ##   - Raises ConfigValidationError if intermediate node is not a dict
    def get(self, key: str, default: Any = None) -> Any:
        """Dotted-key access to YAML value.

        Args:
            key: Dotted path (e.g., "node.host", "domain.platform")
            default: Value to return if key not found.
                     If default is None AND key not found → raises ConfigValidationError.
                     Explicit default=None is treated as "no default — raise on missing".

        Returns:
            Value at key path, or default

        Raises:
            ConfigValidationError: key not found and default not provided
            ConfigValidationError: intermediate node is not a dict

        Examples:
            node.get("node.host") → "1.2.3.4"
            node.get("node.host", default="localhost") → "1.2.3.4"
            node.get("nonexistent", default="fallback") → "fallback"
            node.get("nonexistent") → raises ConfigValidationError
        """
        data = self._load()
        parts = key.split(".")
        current: Any = data

        for i, part in enumerate(parts):
            if not isinstance(current, dict):
                partial = ".".join(parts[:i])
                logger.error(
                    "[IMP:9][NodeYaml] Cannot traverse into non-dict at '%s' in key '%s': %s",
                    partial,
                    key,
                    type(current),
                )
                raise ConfigValidationError(f"Cannot traverse into non-dict at '{partial}' for key '{key}'")
            if part not in current:
                if default is not None:
                    logger.info("[IMP:7][NodeYaml] get(%s) → default=%s", key, default)
                    return default
                logger.error("[IMP:9][NodeYaml] Key not found: %s in key '%s'", part, key)
                raise ConfigValidationError(f"Key not found: {key} (missing '{part}')")
            current = current[part]

        logger.info("[IMP:7][NodeYaml] get(%s) → %s", key, type(current).__name__)
        return current

    # endregion FUNC_get

    # region FUNC_get_list
    ## @purpose  Typed list access. Guarantees return type is list.
    ## @io — ⇥ key: str → ⎋ list
    ## @complexity — O(D) for dotted-key traversal
    ## @invariants
    ##   - Returns [] if key not found
    ##   - Raises ConfigValidationError if value exists but is not a list
    def get_list(self, key: str) -> list:
        """Typed list access with guaranteed list return type.

        Args:
            key: Dotted path to a list value

        Returns:
            List value (empty list if key not found)

        Raises:
            ConfigValidationError: value exists but is not a list
        """
        data = self._load()
        parts = key.split(".")
        current: Any = data

        for i, part in enumerate(parts):
            if not isinstance(current, dict):
                partial = ".".join(parts[:i])
                logger.error(
                    "[IMP:9][NodeYaml] Cannot traverse into non-dict at '%s' in key '%s': %s",
                    partial,
                    key,
                    type(current),
                )
                raise ConfigValidationError(f"Cannot traverse into non-dict at '{partial}' for key '{key}'")
            if part not in current:
                logger.info("[IMP:7][NodeYaml] get_list(%s) → [] (missing)", key)
                return []
            current = current[part]

        if not isinstance(current, list):
            logger.error("[IMP:9][NodeYaml] '%s' is not a list: %s", key, type(current))
            raise ConfigValidationError(f"'{key}' is not a list: {type(current)}")

        logger.info("[IMP:7][NodeYaml] get_list(%s) → list[%d]", key, len(current))
        return current

    # endregion FUNC_get_list

    # region FUNC_raw
    ## @purpose  Access raw parsed dict for backward compatibility.
    ## @io — ⇥ → ⎋ dict
    ## @complexity — O(1) after _load()
    def raw(self) -> dict:
        """Access raw parsed dict for backward compatibility.

        Use sparingly — prefer typed getters.

        Returns:
            Parsed dict
        """
        return self._load()

    # endregion FUNC_raw

    # region FUNC__write_back
    ## @purpose  Write YAML data back to the original file (DevPlan 119 H2 — атомарно через канон).
    ## @io — ⇥ data: dict → ⎋ None | raise ConfigParseError
    ## @complexity — O(N) for YAML dump
    ## @invariants
    ##   Uses ruamel.yaml first for comment preservation, falls back to PyYAML.
    ##   Рендер в строку → shared atomic_writer.atomic_write_text (tempfile+fsync+os.replace,
    ##   DevPlan 119 E5/H2) — атомарность каноническая, ruamel-специфика сохранена.
    ##   Invalidates _data cache after write AND on failure (DevPlan 116 B6 T6.2).
    ##   Raises ConfigParseError on write failure.
    # ⚠️ TRAP[BUG] · 2026-08-01 · P2 · FIXED — broad except Exception for ruamel fallback
    # · Symptom: Any ruamel.yaml failure was swallowed by `except Exception` → silent fallback
    # · Root: try/except ImportError covered the normal case; the additional broad except caught all.
    # · Fix: except narrowed to (yaml.YAMLError, OSError) only — genuine failures surface loudly (T6.2).
    # ⚠️ TRAP[BUG] · 2026-08-01 · P2 · FIXED — cache not invalidated on PyYAML failure
    # · Fix: self._data = None BEFORE raise in the PyYAML failure branch (DevPlan 116 B6 T6.1/T6.2).
    def _write_back(self, data: dict) -> None:
        """Write the YAML data back to the original file (atomic — E5/H2).

        Uses ruamel.yaml if available for comment preservation,
        falls back to PyYAML yaml.dump(). Рендер в строку → shared atomic_writer
        (tempfile + fsync + os.replace, DevPlan 119 E5/H2).

        Args:
            data: Dict to write as YAML

        Raises:
            ConfigParseError: on write failure
        """
        logger.info("[IMP:8][NodeYaml._write_back] Writing to %s", self._path)

        # Try ruamel.yaml first for comment preservation
        try:
            import io as _io

            from ruamel.yaml import YAML

            ryaml = YAML()
            ryaml.width = 4096  # prevent line wrapping
            buf = _io.StringIO()
            ryaml.dump(data, buf)
            self._data = None  # invalidate cache
            _atomic_write_text(self._path, buf.getvalue())
            logger.info("[IMP:9][NodeYaml._write_back] Written via ruamel.yaml (comments preserved, atomic)")
            return
        except ImportError:
            logger.info("[IMP:7][NodeYaml._write_back] ruamel.yaml not available, using PyYAML")
        except (yaml.YAMLError, OSError) as e:
            logger.warning("[IMP:7][NodeYaml._write_back] ruamel.yaml failed (%s), falling back to PyYAML", e)

        # Fallback: PyYAML
        try:
            content = yaml.dump(data, default_flow_style=False, sort_keys=False, allow_unicode=True)
            self._data = None  # invalidate cache
            _atomic_write_text(self._path, content)
            logger.info("[IMP:9][NodeYaml._write_back] Written via PyYAML (atomic)")
        except (OSError, yaml.YAMLError) as e:
            # DevPlan 116 B6 T6.2: invalidate cache BEFORE raise — mutations work on deepcopy,
            # but this guard protects against any cached-state drift on write failure.
            self._data = None
            logger.error("[IMP:10][NodeYaml._write_back] Write failed: %s", e)
            raise ConfigParseError(f"Failed to write node.yaml: {e}") from e

    # endregion FUNC__write_back


# endregion CLASS_NodeYamlCore
