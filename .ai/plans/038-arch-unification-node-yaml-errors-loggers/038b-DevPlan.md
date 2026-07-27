$START_DEVPLAN

# DevPlan 038b — sys.exit removal + loggers + typed exceptions (Waves W2+W3+W4)

## $ARTIFACT_CONTRACT

| Поле | Значение |
|------|---------|
| **PURPOSE** | Спроектировать пошаговую реализацию волн W2 (sys.exit removal), W3 (консистентные логгеры), W4 (типизированные исключения) из DevPlan 038 как единый PR с тремя независимыми подзадачами |
| **DESCRIPTION** | Декомпозитный DevPlan, выделяющий из родительского DevPlan 038 волны W2+W3+W4. Каждая волна атомарна: W2 — рефакторинг 1 файла + обновление 3 shell-callers, W3 — механическая замена в 15 файлах, W4 — замена исключений в ~30 файлах с новым CI gate. Волны не пересекаются по файлам. W2+W3 полностью независимы от W1 (038a). W4 требует только `exceptions.py` из 038a. |
| **RATIONALE** | Три волны в одном PR: суммарный diff <800 строк, 0 конфликтов между волнами (разные файлы), независимый rollback каждой волны через `git revert` отдельных коммитов. W2 (5 файлов) и W3 (15 файлов) — low-risk механические замены. W4 (~30 файлов) — средний риск, но exception hierarchy стабильна (определена в 038a). |
| **ACCEPTANCE_CRITERIA** | 1. AC2: 0 `sys.exit()` в библиотечных функциях `project_registry.py`. 2. AC3: 0 hardcoded `getLogger("literal")`. 3. AC4: 4 subclass от `PlatformError` в `exceptions.py`. 4. AC5: 0 `raise RuntimeError` в `core/internal/bootstrap/`. 5. AC6: `except Exception` только в `__main__`. 6. Новый gate `make check-exception-patterns` встроен в `make gate MODE=fast`. 7. Все существующие тесты проходят. |
| **IMPLEMENTS** | Brief 038b — sys.exit removal + loggers + typed exceptions (Waves W2+W3+W4 декомпозиции DevPlan 038) |
| **IMPACTS** | 1 новый Python файл (`test_project_registry.py`), ~50 модифицируемых Python файлов, 3 shell-файла (проверка), `Makefile` (новый gate), `core/entrypoint-manifest.yaml` (регистрация gate), `tests/unit/test_exceptions.py` (дополнение) |
| **REQUIRES** | DevPlan 038a COMPLETED — `core/internal/shared/exceptions.py` должен существовать с 5 классами: `PlatformError` (base, exit_code=1), `ConfigNotFoundError` (exit_code=2), `ConfigParseError` (exit_code=3), `ConfigValidationError` (exit_code=4), `PlatformFatalError` (exit_code=10). Без этого файла W4 заблокирована. W2 и W3 от 038a не зависят. |

---

## Debt Intake

Аудит существующих TRAP/DEBT/JIRA в зоне изменений W2+W3+W4:

| Источник | TRAP/DEBT | Решение |
|----------|-----------|---------|
| `project_registry.py:12` | `## @invariants` — «`sys.exit` задекларирован как фича для shell-совместимости» | **IN_SCOPE (W2)**: `sys.exit` убирается из библиотечных функций, остаётся только в CLI `__main__`. Инвариант обновляется. |
| `bootstrap/yaml_helpers.py:11` | `## @invariants` — «Never raises: returns "" on any parse error» | **DEFER**: в 038b не трогается. Унификация с фасадом — в 038a (W1). |
| `state_machine.py:1034` | `raise RuntimeError("node-lifecycle must run as root")` | **IN_SCOPE (W4)**: → `raise PlatformFatalError(...)` |
| `steps.py:187` | `raise RuntimeError(f"apt-get install failed")` | **IN_SCOPE (W4)**: → `raise PlatformFatalError(...)` |
| `state_machine.py:1106,1713` | `raise RuntimeError("node.yaml not found")` | **IN_SCOPE (W4)**: → `raise ConfigNotFoundError(...)` |
| `state_machine.py:1679` | `raise RuntimeError("secrets.env not found")` | **IN_SCOPE (W4)**: → `raise ConfigNotFoundError(...)` |
| `state_machine.py:1408-1812` | `raise RuntimeError(...)` (6 cases) | **IN_SCOPE (W4)**: → `raise PlatformFatalError(...)` |
| `steps.py:259-677` | `raise RuntimeError(...)` (12 cases) | **IN_SCOPE (W4)**: → `raise PlatformFatalError(...)` |

---

## Wave 2 — sys.exit removal in project_registry.py

### W2.1 — Scope

| Категория | Количество |
|-----------|------------|
| Файлов: Python | 1 (project_registry.py) |
| Файлов: shell (проверка) | 3 (add-project.sh, adopt-project.sh, remove-project.sh) |
| Файлов: тесты | 1 (новый: test_project_registry.py) |
| Точек изменений | 12 `sys.exit()` → `return (bool, str)` + CLI `__main__` update |
| **Всего файлов** | **5** |

### W2.2 — Полный список замен `sys.exit()` → `return (bool, str)`

| # | Функция | Строка | Старый код | Новый код |
|---|---------|--------|-----------|-----------|
| 1 | `register_project` | 54 | `sys.exit(1)` | `return (False, "PyYAML not available")` |
| 2 | `register_project` | 61 | `sys.exit(0)` | `return (False, "Missing params")` |
| 3 | `register_project` | 73 | `sys.exit(0)` | `return (True, "Idempotent SKIP")` |
| 4 | `register_project` | 91 | `sys.exit(0)` | `return (True, "Registered")` |
| 5 | `deregister_project` | 117 | `sys.exit(1)` | `return (False, "PyYAML not available")` |
| 6 | `deregister_project` | 124 | `sys.exit(0)` | `return (False, "Missing params")` |
| 7 | `deregister_project` | 131 | `sys.exit(0)` | `return (True, "No projects section")` |
| 8 | `deregister_project` | 144 | `sys.exit(0)` | `return (True, "Removed")` |
| 9 | `list_projects` | 172 | `sys.exit(1)` | `return (False, "PyYAML not available")` |
| 10 | `list_projects` | 176 | `sys.exit(1)` | `return (False, "Missing path")` |
| 11 | `list_projects` | 183 | `sys.exit(1)` | `return (False, "Failed to read")` |
| 12 | `list_projects` | 194 | `sys.exit(0)` | `return (True, "Listed N projects")` |

**CLI `__main__` update:**
- Вызывать функцию → получить `(success: bool, message: str)`
- `print(message)` (для shell stdout)
- `sys.exit(0 if success else 1)`
- Все существующие exit codes сохраняются

### W2.3 — Shell-callers audit (проверка, не изменение)

| Файл | Вызов | Что проверить |
|------|-------|---------------|
| `core/internal/scaffold/add-project.sh` (~719) | `python3 project_registry.py register ...` | `|| log_warn` / `|| exit` — обработка exit code |
| `core/internal/scaffold/adopt-project.sh` (~674) | `python3 project_registry.py register ...` | `|| log_warn` / `|| exit` — обработка exit code |
| `core/internal/scaffold/remove-project.sh` (~212) | `python3 project_registry.py deregister ...` | `|| log_warn` / `|| exit` — обработка exit code |

**Ожидаемый результат:** shell-callers используют `||` для обработки ошибок. Поскольку CLI `__main__` выдаёт идентичные exit codes (0=success, 1=error), изменения в shell-файлах не требуются — только аудит.

### W2.4 — Unit-тесты

Новый файл: `tests/unit/test_project_registry.py`

| Test | Scenario | Expected |
|------|----------|----------|
| `test_register_returns_tuple` | Вызов `register_project()` напрямую | `(True, str)` или `(False, str)` |
| `test_deregister_returns_tuple` | Вызов `deregister_project()` напрямую | `(True, str)` или `(False, str)` |
| `test_list_returns_tuple` | Вызов `list_projects()` напрямую | `(True, str)` или `(False, str)` |
| `test_register_no_sys_exit` | Импорт `register_project` и вызов | Процесс не terminated, возвращён tuple |
| `test_cli_exit_code_success` | `__main__` с успешным вызовом | `sys.exit(0)` |
| `test_cli_exit_code_failure` | `__main__` с ошибочным вызовом | `sys.exit(1)` |
| `test_negative_sys_exit_in_library` | `grep 'sys.exit' project_registry.py` исключая `__main__` | 0 совпадений (AC2) |

---

## Wave 3 — Hardcoded loggers → `__name__`

### W3.1 — Scope

| Категория | Количество |
|-----------|------------|
| Файлов: Python | 15 |
| Файлов: shell | 0 |
| Точек изменений | 15 (одна замена на файл) |
| **Всего файлов** | **15** |

### W3.2 — Полный список замен

| # | Файл | Строка | Старый логгер | Новый (`__name__`) |
|---|------|--------|---------------|---------------------|
| 1 | `core/internal/provisioner.py` | 31 | `"provisioner"` | `__name__` |
| 2 | `core/internal/reconciler_projects.py` | 34 | `"reconcile_projects"` | `__name__` |
| 3 | `core/internal/shared/content_hash.py` | 28 | `"content_hash"` | `__name__` |
| 4 | `core/internal/shared/docker_compose.py` | 33 | `"docker_compose"` | `__name__` |
| 5 | `core/internal/shared/audit_logger.py` | 33 | `"audit_logger"` | `__name__` |
| 6 | `core/internal/shared/ssh_command_parser.py` | 33 | `"ssh_command_parser"` | `__name__` |
| 7 | `core/internal/shared/platform_deliver.py` | 30 | `"platform_deliver"` | `__name__` |
| 8 | `core/internal/bootstrap/_topo_sort.py` | 43 | `"_topo_sort"` | `__name__` |
| 9 | `core/internal/bootstrap/deploy/content_hash.py` | 43 | `"content_hash"` | `__name__` |
| 10 | `core/internal/bootstrap/deploy/sudoers_generator.py` | 38 | `"sudoers_generator"` | `__name__` |
| 11 | `core/internal/bootstrap/deploy/compose_preflight.py` | 39 | `"compose_preflight"` | `__name__` |
| 12 | `core/internal/bootstrap/deploy/docker_orchestrator.py` | 100 | `"docker_orchestrator"` | `__name__` |
| 13 | `core/internal/bootstrap/deploy/spool_validator.py` | 44 | `"spool_validator"` | `__name__` |
| 14 | `core/internal/bootstrap/deploy/secrets_validator.py` | 40 | `"secrets_validator"` | `__name__` |
| 15 | `core/modules/hermes-agent/watchdog/agent_watchdog.py` | 40 | `"watchdog"` | `__name__` |

**Уже правильные (НЕ трогать):**
- `core/internal/bootstrap/yaml_helpers.py:33` — уже `__name__` ✅
- `core/internal/shared/node_yaml.py:22` — уже `__name__` ✅

### W3.3 — Эффект замены

Замена `"docker_compose"` → `__name__` меняет лог-префикс с `[IMP:X][docker_compose]` на `[IMP:X][core.internal.shared.docker_compose]`. Это ожидаемое поведение:
- Qualified module name улучшает отладку
- Формат `[IMP:X][func_name]` НЕ меняется (func_name задаётся вручную в каждом вызове `logger.info()`)
- Grep по `[IMP:` продолжает работать

### W3.4 — Верификация

```bash
# После замены:
grep 'getLogger("[a-z]' core/internal/ core/modules/ --include='*.py'
# Ожидаемый результат: 0 совпадений (AC3)
```

---

## Wave 4 — Typed exception hierarchy

**⚠️ BLOCKER: This entire Wave 4 REQUIRES DevPlan 038a to be COMPLETED first.**
Without `core/internal/shared/exceptions.py` (5 classes from 038a), W4 cannot be implemented.
W2 and W3 are independent and can proceed immediately.

### W4.0 — Precondition

Файл `core/internal/shared/exceptions.py` должен существовать и содержать 5 классов (создаётся в 038a):

```python
class PlatformError(Exception):
    """Base exception for all platform errors."""
    exit_code: int = 1

class ConfigNotFoundError(PlatformError):
    """Configuration file not found."""
    exit_code: int = 2

class ConfigParseError(PlatformError):
    """Configuration file parse error (YAML syntax, JSON decode)."""
    exit_code: int = 3

class ConfigValidationError(PlatformError):
    """Configuration structure validation error (missing key, wrong type)."""
    exit_code: int = 4

class PlatformFatalError(PlatformError):
    """Non-recoverable platform error (root required, preconditions)."""
    exit_code: int = 10
```

### W4.1 — Scope

| Категория | Количество |
|-----------|------------|
| Файлов: Python (P4.1-P4.4 — targeted) | 5 |
| Файлов: Python (P4.5 — batch) | 21 |
| Файлов: верхнеуровневые обработчики | ~5 |
| Файлов: Makefile + entrypoint-manifest | 2 |
| Файлов: тесты (дополнение) | 1 |
| **Всего файлов** | **~30** |

### W4.2 — P4.1: Silent swallow → `return []` (reconciler_projects.py)

Файл: `core/internal/reconciler_projects.py`

| # | Строка | Старый паттерн | Новый паттерн |
|---|--------|---------------|---------------|
| 1 | 132-135 | `except Exception as exc: logger.warning(...); return []` | `except (ConfigNotFoundError, ConfigParseError) as exc: logger.warning(...); return []` |
| 2 | 270-279 | `except Exception as exc: logger.warning(...); return None` | `except (ConfigNotFoundError, ConfigParseError) as exc: logger.warning(...); return None` |
| 3 | 457-464 | `except Exception as exc: logger.error(...); return False` | `except (ConfigNotFoundError, ConfigParseError) as exc: logger.error(...); return False` |

### W4.3 — P4.2: Silent swallow → `return False` (s3_ssl_cache.py)

Файл: `core/internal/bootstrap/s3_ssl_cache.py`

| # | Строка | Старый паттерн | Новый паттерн |
|---|--------|---------------|---------------|
| 4 | 252 | `except Exception as e: return False` | `except (ConfigNotFoundError, ConfigParseError) as e: return False` |
| 5 | 281 | `except Exception as e: return False` | `except (ConfigNotFoundError, ConfigParseError) as e: ...` |
| 6 | 315 | `except Exception as e: return {}` | `except (ConfigNotFoundError, ConfigParseError) as e: ...` |
| 7 | 447, 535, 552, 569, 587, 709 | `except Exception as e: ...` | Сузить до ожидаемых типов (file I/O, YAML parse, network) |

### W4.4 — P4.3: `raise RuntimeError` → typed exceptions (state_machine.py, steps.py)

#### state_machine.py

Файл: `core/internal/bootstrap/lifecycle/state_machine.py`

| # | Строка | `RuntimeError` message | Замена |
|---|--------|----------------------|--------|
| 8 | 1034 | `"must run as root"` | `raise PlatformFatalError("node-lifecycle must run as root")` |
| 9 | 1106 | `"node.yaml not found"` | `raise ConfigNotFoundError(f"node.yaml not found: {path}")` |
| 10 | 1408-1434 | 5× `RuntimeError(...)` (разные) | `raise PlatformFatalError(...)` |
| 11 | 1614 | `RuntimeError(...)` | `raise PlatformFatalError(...)` |
| 12 | 1679 | `"secrets.env not found"` | `raise ConfigNotFoundError(f"secrets.env not found: {path}")` |
| 13 | 1713 | `"node.yaml not found"` | `raise ConfigNotFoundError(f"node.yaml not found: {path}")` |
| 14 | 1812 | `RuntimeError(...)` | `raise PlatformFatalError(...)` |

#### steps.py

Файл: `core/internal/bootstrap/lifecycle/steps.py`

| # | Строки | `RuntimeError` count | Замена |
|---|--------|---------------------|--------|
| 15 | 187-192 | 3 cases | `raise PlatformFatalError(...)` |
| 16 | 259-274 | 3 cases | `raise PlatformFatalError(...)` |
| 17 | 315-318 | 2 cases | `raise PlatformFatalError(...)` |
| 18 | 384-398 | 3 cases | `raise PlatformFatalError(...)` |
| 19 | 560 | 1 case | `raise PlatformFatalError(...)` |
| 20 | 677 | 1 case | `raise PlatformFatalError(...)` |

**Итого P4.3:** ~20 замен `RuntimeError` → typed exceptions.

### W4.5 — P4.4: DomainCertResult error differentiation (cert_orchestrator.py)

Файл: `core/internal/bootstrap/cert_orchestrator.py`

| # | Строка | Старый паттерн | Новый паттерн |
|---|--------|---------------|---------------|
| 21 | 382, 409, 547, 611, 688 | `except Exception as e: DomainCertResult(..., error=str(e))` | `except (ConfigNotFoundError, ConfigParseError, PlatformFatalError) as e: DomainCertResult(..., error=f"{type(e).__name__}: {e}")` |

### W4.6 — P4.5: Batch update оставшихся `except Exception` блоков

Полный список файлов с `except Exception` блоками (взято из родительского DevPlan 038 §P4.5, строки 817-837, с верифицированными путями):

| # | Файл | Блоков | Контекст |
|---|------|--------|----------|
| 1 | `core/internal/healthcheck/platform_export_metrics.py` | 13 | Метрики: YAML + JSON + file I/O |
| 2 | `core/internal/healthcheck/metrics/cert_collector.py` | 5 | Сертификаты: file I/O + YAML |
| 3 | `core/internal/healthcheck/metrics/project_collector.py` | 1 | Проекты: YAML |
| 4 | `core/internal/bootstrap/preflight.py` | 3 | Preflight: YAML + file I/O |
| 5 | `core/internal/bootstrap/discover_modules.py` | 1 | Module discovery: YAML |
| 6 | `core/internal/bootstrap/deploy/context_deployer.py` | 7 | Деплой контекста: YAML + git + shell |
| 7 | `core/internal/bootstrap/deploy/context_overlay.py` | 2 | Контекстный overlay: YAML + git |
| 8 | `core/internal/bootstrap/converge/reconciler.py` | 5 | Converge: YAML + SSH |
| 9 | `core/internal/bootstrap/deploy/docker_orchestrator.py` | 4 | Docker: compose + registry |
| 10 | `core/internal/bootstrap/deploy/sudoers_generator.py` | 4 | Sudoers: file I/O |
| 11 | `core/internal/bootstrap/deploy/orphan_reconciler.py` | 3 | Orphan cleanup: compose |
| 12 | `core/internal/bootstrap/lifecycle/secrets_manager.py` | 1 | Secrets: AGE decryption |
| 13 | `core/internal/llm/key_provisioner.py` | 3 | LLM keys: API + file I/O |
| 14 | `core/internal/scripts/sync_env_defaults.py` | 1 | Env sync: YAML + file I/O |
| 15 | `core/internal/scripts/generate_entrypoint_manifest.py` | 1 | Manifest gen: YAML |
| 16 | `core/internal/scripts/generate_agents_md.py` | 1 | Agents gen: YAML + file I/O |
| 17 | `core/internal/scaffold/context_registry.py` | 2 | Context registry: YAML |
| 18 | `core/internal/scaffold/vhost_yaml_reader.py` | 1 | Vhost: YAML |
| 19 | `core/internal/shared/ssh_command_parser.py` | 1 | SSH parser: subprocess |
| 20 | `core/internal/bootstrap/lifecycle/steps.py` | 3 | Steps: subprocess + file I/O |
| 21 | `core/internal/shared/node_yaml.py` | 1 | NodeYaml: YAML (legacy path) |

**Итого P4.5:** ~80 блоков `except Exception` в 21 файле.

**Стратегия замены для P4.5:**
- YAML-чтение → `except (ConfigNotFoundError, ConfigParseError)`
- File I/O → `except (OSError, IOError)` или `except (ConfigNotFoundError, PermissionError)`
- Subprocess → `except (subprocess.CalledProcessError, OSError)`
- JSON → `except (json.JSONDecodeError, ConfigParseError)`
- Network → `except (ConnectionError, TimeoutError)`

### W4.7 — Верхнеуровневые обработчики

Обновить `except Exception` / `except PlatformError` в верхнеуровневых точках входа:

| Файл | Локация | Изменение |
|------|---------|-----------|
| `core/internal/bootstrap/lifecycle/state_machine.py` | main loop | `except PlatformError as e: logger.critical(...); sys.exit(e.exit_code)` |
| `core/internal/bootstrap/lifecycle/steps.py` | entry point | `except PlatformError as e: logger.critical(...); raise` (пробросить в state_machine) |
| CLI entrypoints (3-5 файлов) | `__main__` | `except PlatformError as e: print(e, file=sys.stderr); sys.exit(e.exit_code)` |

**Маппинг exit_code:**
| Exception | exit_code | sys.exit() |
|-----------|----------|------------|
| `PlatformError` | 1 | `sys.exit(1)` |
| `ConfigNotFoundError` | 2 | `sys.exit(2)` |
| `ConfigParseError` | 3 | `sys.exit(3)` |
| `ConfigValidationError` | 4 | `sys.exit(4)` |
| `PlatformFatalError` | 10 | `sys.exit(10)` |

### W4.8 — Новый CI gate: `make check-exception-patterns`

Добавить в `Makefile`:

```makefile
# region check-exception-patterns
.PHONY: check-exception-patterns
check-exception-patterns:
	@echo "[IMP:7][gate] Checking for bare except Exception in non-CLI code..."
	@! grep -rEn 'except[[:space:]]+Exception' core/internal/ --include='*.py' \
		| grep -v '__main__' \
		| grep -v '# noqa: EXC' \
		|| (echo "FAIL: bare except Exception found in non-CLI code" && exit 1)

**Примечание:** этот gate должен быть размещён в `makefiles/ci.mk` (не в корневом Makefile) для следования существующему паттерну, и добавлен как шаг в pipeline `make gate MODE=fast`.
	@echo "[IMP:9][gate] All exception handlers are typed."
# endregion check-exception-patterns
```

**Интеграция в `make gate MODE=fast`:**
```makefile
gate-fast: check-exception-patterns \
           ...existing-gates...
```

**Регистрация в `core/entrypoint-manifest.yaml`:**
```yaml
# Add to the gates section (exact format depends on manifest schema):
- name: check-exception-patterns
  description: "Ensure all except Exception blocks are typed (non-CLI code only)"
  gate: [fast, full]
```

**Примечание:** `# noqa: EXC` — маркер для легитимных `except Exception` блоков (например, в `__main__` или где осознанно нужен catch-all с последующим re-raise/log). Использовать только в обоснованных случаях с комментарием.

### W4.9 — Дополнение тестов

Дополнить `tests/unit/test_exceptions.py` (создаётся в 038a):
- Если 038a реализован: дополнить существующий файл 8 тестами
- Если 038a НЕ реализован: создать файл с нуля, включив базовые тесты иерархии + exit_code маппинг

| Test | Scenario | Expected |
|------|----------|----------|
| `test_platform_error_exit_code` | `raise PlatformError()` → catch → `.exit_code` | `1` |
| `test_config_not_found_exit_code` | `raise ConfigNotFoundError()` → catch → `.exit_code` | `2` |
| `test_config_parse_error_exit_code` | `raise ConfigParseError()` → catch → `.exit_code` | `3` |
| `test_config_validation_error_exit_code` | `raise ConfigValidationError()` → catch → `.exit_code` | `4` |
| `test_platform_fatal_error_exit_code` | `raise PlatformFatalError()` → catch → `.exit_code` | `10` |
| `test_exception_inheritance` | `isinstance(ConfigNotFoundError(), PlatformError)` | `True` |
| `test_exception_str_message` | `str(ConfigNotFoundError("test"))` | `"test"` |
| `test_all_subclasses_registered` | `PlatformError.__subclasses__()` length | `4` |

---

## Task Decomposition

### Wave 2 — sys.exit removal

| Task | Description | Files | Complexity | Dependencies | AC |
|------|-------------|-------|------------|-------------|-----|
| **T2.1** | Заменить 12 `sys.exit()` → `return (bool, str)` в `register_project`, `deregister_project`, `list_projects` | 1 | 3 | None | 0 `sys.exit()` в библиотечных функциях |
| **T2.2** | Обновить CLI `__main__`: маппинг `(success, msg)` → `print(msg); sys.exit(0/1)` | 1 | 1 | T2.1 | CLI exit codes идентичны старым |
| **T2.3** | Аудит shell-callers: проверить `|| log_warn` / `|| exit` обработку в add-project.sh, adopt-project.sh, remove-project.sh | 3 | 2 | T2.2 | Shell-скрипты работают без изменений |
| **T2.4** | Создать `tests/unit/test_project_registry.py`: 7 тестов (см. W2.4) | 1 (NEW) | 3 | T2.1 | Все тесты проходят, AC2 подтверждён |

### Wave 3 — hardcoded loggers → `__name__`

| Task | Description | Files | Complexity | Dependencies | AC |
|------|-------------|-------|------------|-------------|-----|
| **T3.1** | Механическая замена `getLogger("literal")` → `getLogger(__name__)` в 15 файлах | 15 | 2 | None | Все 15 файлов обновлены |
| **T3.2** | Верификация: `grep 'getLogger("[a-z]' core/internal/ core/modules/ --include='*.py'` → 0 | N/A | 1 | T3.1 | AC3 подтверждён |
| **T3.3** | Запустить существующие тесты: убедиться что формат логов не сломан | N/A | 2 | T3.1 | Все тесты проходят, LDD-логи содержат qualified name |

### Wave 4 — typed exception hierarchy

| Task | Description | Files | Complexity | Dependencies | AC |
|------|-------------|-------|------------|-------------|-----|
| **T4.1** | Заменить `raise RuntimeError` на typed exceptions в state_machine.py, steps.py (P4.3: ~20 замен) | 2 | 4 | T1.0 (exceptions.py из 038a) | AC5: 0 `raise RuntimeError` |
| **T4.2** | Сузить `except Exception` в reconciler_projects.py, s3_ssl_cache.py (P4.1-P4.2: ~10 замен) | 2 | 3 | T1.0 | Каждый `except` ловит конкретные типы |
| **T4.3** | Обновить cert_orchestrator.py: дифференцировать ошибки в DomainCertResult (P4.4: 5 замен) | 1 | 2 | T1.0 | `DomainCertResult.error` содержит тип ошибки |
| **T4.4** | Batch update оставшихся `except Exception` блоков (P4.5: ~80 блоков в 21 файле) | 21 | 6 | T1.0 | AC6: `except Exception` только в `__main__` |
| **T4.5** | Обновить верхнеуровневые обработчики: `except PlatformError`, маппинг `exit_code` → `sys.exit(e.exit_code)` | ~5 | 3 | T4.1-T4.4 | Все CLI entrypoints корректно маппят exit codes |
| **T4.6** | Добавить `make check-exception-patterns` в Makefile + entrypoint-manifest.yaml | 2 | 2 | T4.4 | Новый gate встроен в `make gate MODE=fast` |
| **T4.7** | Дополнить `tests/unit/test_exceptions.py`: 8 тестов на exit_code маппинг и наследование | 1 | 2 | T1.0, T4.5 | Все тесты проходят, AC4 подтверждён |

---

## Acceptance Criteria Mapping

| AC | Критерий | Wave | Проверка |
|----|----------|------|----------|
| AC2 | 0 `sys.exit()` в библиотечных функциях `project_registry.py` | W2 | `grep 'sys.exit' core/internal/shared/project_registry.py \| grep -v '__main__' \| grep -v 'if __name__'` → 0 |
| AC3 | 0 hardcoded `getLogger("literal")` | W3 | `grep -r 'getLogger("[a-z]' core/internal/ core/modules/ --include='*.py'` → 0 |
| AC4 | 4 subclass от `PlatformError` | W4 (dep: 038a) | `grep 'class.*Error.*PlatformError' core/internal/shared/exceptions.py` → 4 matches |
| AC5 | 0 `raise RuntimeError` для платформенных ошибок | W4 | `grep -r 'raise RuntimeError' core/internal/bootstrap/ --include='*.py'` → 0 |
| AC6 | `except Exception` только в `__main__` | W4 | `grep -rn 'except\s\+Exception' core/internal/ --include='*.py' \| grep -v '__main__'` → 0 |
| AC8 | `make gate MODE=fast` passes | All | `make gate MODE=fast` → exit 0 |
| AC10 | Все существующие тесты проходят | All | `python -m pytest tests/ -s -v` → all pass |

---

## File Manifest

### Новые файлы

| Файл | Назначение | Wave |
|------|-----------|------|
| `tests/unit/test_project_registry.py` | Unit-тесты для project_registry return codes | W2 |

### Модифицируемые файлы (W2 — sys.exit removal)

| Файл | Изменение |
|------|-----------|
| `core/internal/shared/project_registry.py` | 12 `sys.exit()` → `return (bool, str)` + `__main__` update |
| `core/internal/scaffold/add-project.sh` | Аудит exit code handling (без изменений, если `\|\|` уже есть) |
| `core/internal/scaffold/adopt-project.sh` | Аудит exit code handling |
| `core/internal/scaffold/remove-project.sh` | Аудит exit code handling |

### Модифицируемые файлы (W3 — logger names)

| Файл | Изменение |
|------|-----------|
| `core/internal/provisioner.py:31` | `"provisioner"` → `__name__` |
| `core/internal/reconciler_projects.py:34` | `"reconcile_projects"` → `__name__` |
| `core/internal/shared/content_hash.py:28` | `"content_hash"` → `__name__` |
| `core/internal/shared/docker_compose.py:33` | `"docker_compose"` → `__name__` |
| `core/internal/shared/audit_logger.py:33` | `"audit_logger"` → `__name__` |
| `core/internal/shared/ssh_command_parser.py:33` | `"ssh_command_parser"` → `__name__` |
| `core/internal/shared/platform_deliver.py:30` | `"platform_deliver"` → `__name__` |
| `core/internal/bootstrap/_topo_sort.py:43` | `"_topo_sort"` → `__name__` |
| `core/internal/bootstrap/deploy/content_hash.py:43` | `"content_hash"` → `__name__` |
| `core/internal/bootstrap/deploy/sudoers_generator.py:38` | `"sudoers_generator"` → `__name__` |
| `core/internal/bootstrap/deploy/compose_preflight.py:39` | `"compose_preflight"` → `__name__` |
| `core/internal/bootstrap/deploy/docker_orchestrator.py:100` | `"docker_orchestrator"` → `__name__` |
| `core/internal/bootstrap/deploy/spool_validator.py:44` | `"spool_validator"` → `__name__` |
| `core/internal/bootstrap/deploy/secrets_validator.py:40` | `"secrets_validator"` → `__name__` |
| `core/modules/hermes-agent/watchdog/agent_watchdog.py:40` | `"watchdog"` → `__name__` |

### Модифицируемые файлы (W4 — typed exceptions)

#### P4.1 (silent swallow → typed)
| Файл | Блоков |
|------|--------|
| `core/internal/reconciler_projects.py` | 3 |

#### P4.2 (silent swallow → typed)
| Файл | Блоков |
|------|--------|
| `core/internal/bootstrap/s3_ssl_cache.py` | 7 |

#### P4.3 (RuntimeError → typed)
| Файл | Блоков |
|------|--------|
| `core/internal/bootstrap/lifecycle/state_machine.py` | ~11 |
| `core/internal/bootstrap/lifecycle/steps.py` | ~12 |

#### P4.4 (DomainCertResult)
| Файл | Блоков |
|------|--------|
| `core/internal/bootstrap/cert_orchestrator.py` | 5 |

#### P4.5 (batch except Exception — 21 файл)
| Файл | Блоков |
|------|--------|
| `core/internal/healthcheck/platform_export_metrics.py` | 13 |
| `core/internal/healthcheck/metrics/cert_collector.py` | 5 |
| `core/internal/healthcheck/metrics/project_collector.py` | 1 |
| `core/internal/bootstrap/preflight.py` | 3 |
| `core/internal/bootstrap/discover_modules.py` | 1 |
| `core/internal/bootstrap/deploy/context_deployer.py` | 7 |
| `core/internal/bootstrap/deploy/context_overlay.py` | 2 |
| `core/internal/bootstrap/converge/reconciler.py` | 5 |
| `core/internal/bootstrap/deploy/docker_orchestrator.py` | 4 |
| `core/internal/bootstrap/deploy/sudoers_generator.py` | 4 |
| `core/internal/bootstrap/deploy/orphan_reconciler.py` | 3 |
| `core/internal/bootstrap/lifecycle/secrets_manager.py` | 1 |
| `core/internal/llm/key_provisioner.py` | 3 |
| `core/internal/scripts/sync_env_defaults.py` | 1 |
| `core/internal/scripts/generate_entrypoint_manifest.py` | 1 |
| `core/internal/scripts/generate_agents_md.py` | 1 |
| `core/internal/scaffold/context_registry.py` | 2 |
| `core/internal/scaffold/vhost_yaml_reader.py` | 1 |
| `core/internal/shared/ssh_command_parser.py` | 1 |
| `core/internal/bootstrap/lifecycle/steps.py` | 3 |
| `core/internal/shared/node_yaml.py` | 1 |

#### Верхнеуровневые обработчики + CI gate
| Файл | Изменение |
|------|-----------|
| `core/internal/bootstrap/lifecycle/state_machine.py` | `except PlatformError` в main loop |
| `core/internal/bootstrap/lifecycle/steps.py` | `except PlatformError` проброс |
| CLI entrypoints (3-5 файлов) | `except PlatformError` + `sys.exit(e.exit_code)` |
| `Makefile` | Новый target `check-exception-patterns` |
| `core/entrypoint-manifest.yaml` | Регистрация `check-exception-patterns` |

#### Тесты
| Файл | Изменение |
|------|-----------|
| `tests/unit/test_exceptions.py` | Дополнить 8 тестами на exit_code маппинг и наследование |

---

## File Count Summary

| Wave | Новых файлов | Модифицируемых Python | Модифицируемых shell | Модифицируемых config | Всего |
|------|-------------|----------------------|---------------------|----------------------|-------|
| **W2** | 1 | 1 | 3 (аудит) | 0 | **5** |
| **W3** | 0 | 15 | 0 | 0 | **15** |
| **W4** | 0 | ~26 | 0 | 2 (Makefile + manifest) | **~28** |
| **Total 038b** | **1** | **~42** | **3** | **2** | **~48** |

**Примечание:** Файлы, фигурирующие в нескольких подзадачах W4 (например, `state_machine.py` — и в P4.3, и в верхнеуровневых обработчиках), подсчитаны один раз. Реальное уникальное количество файлов — ~45.

---

## Implementation Order

### Рекомендуемая последовательность (один PR, три коммита):

```
Commit 1: Wave 2 (sys.exit removal)
  ├── project_registry.py: 14 замен + __main__ update
  ├── tests/unit/test_project_registry.py: новый
  └── Shell-callers: аудит (no-op если всё ок)

Commit 2: Wave 3 (hardcoded loggers → __name__)
  └── 15 файлов: механическая замена getLogger("literal") → getLogger(__name__)

Commit 3: Wave 4 (typed exception hierarchy)
  ├── P4.3: state_machine.py + steps.py: ~20 RuntimeError → typed
  ├── P4.1-P4.2: reconciler_projects.py + s3_ssl_cache.py: ~10 except сужений
  ├── P4.4: cert_orchestrator.py: 5 DomainCertResult
  ├── P4.5: 21 файл: ~80 except Exception → typed
  ├── Верхнеуровневые обработчики: except PlatformError + exit_code
  ├── Makefile + entrypoint-manifest.yaml: новый gate
  └── tests/unit/test_exceptions.py: дополнение
```

### Параллелизм:
- Commit 1 и Commit 2 можно делать параллельно (разные файлы, 0 конфликтов)
- Commit 3 ждёт Commit 1+2 (для чистоты review), но технически не зависит от них
- Commit 3 требует `exceptions.py` из 038a

---

## CI Gate Impact

### Новый gate: `make check-exception-patterns`

- **Тип:** блокирующий (входит в `make gate MODE=fast`)
- **Проверка:** `grep -rn 'except\s\+Exception' core/internal/ --include='*.py' | grep -v '__main__' | grep -v '# noqa: EXC'`
- **Ожидаемый результат:** 0 совпадений
- **Исключения:** блоки `except Exception` в `if __name__ == "__main__"` (CLI верхнего уровня) и маркированные `# noqa: EXC`
- **Регистрация:** `core/entrypoint-manifest.yaml` → `allowed_in: [gate-fast, gate-full]`

### Существующие gates (не затрагиваются)

| Gate | Статус |
|------|--------|
| `check-manifests` | Без изменений |
| `check-no-new-inline-python3` | Без изменений (W5 — отдельный PR) |
| `lint` / `ruff` | Без изменений |

---

## Risk & Mitigations

| # | Риск | Severity | Mitigation |
|---|------|----------|------------|
| R1 | **P4.5: массовое сужение `except Exception` ломает рантайм** — новый неожиданный exception не ловится | MEDIUM | Каждый `except Exception` заменяется на конкретные типы, основанные на анализе кода в блоке try. Если блок try вызывает 3 разных операции (YAML + subprocess + file I/O) — `except` должен включать все три типа. CI gate `check-exception-patterns` подтверждает что голых `except Exception` не осталось. |
| R2 | **W4: `exit_code` расходится с ожидаемым** — shell-скрипты проверяют `$? -eq 0`, новый код возвращает нестандартный код | MEDIUM | Маппинг exit_code зафиксирован: 0=success, 1=generic error, 2=not found, 3=parse error, 4=validation error, 10=fatal. Shell-скрипты используют `||` (проверка на non-zero), а не конкретные коды. |
| R3 | **W3: qualified logger name слишком длинный** — `core.internal.bootstrap.deploy.secrets_validator` занимает 50 символов в префиксе | LOW | LDD-логи и так содержат `[IMP:X][func_name]`. Длинный префикс модуля — это только вопрос читаемости, не функциональности. При необходимости можно сократить через `__name__.split(".")[-1]` в будущем. |
| R4 | **038a не готов до старта 038b** — `exceptions.py` отсутствует, W4 заблокирована | MEDIUM | Начать с W2 и W3 (независимы от 038a). Если 038a готова — W4 можно делать сразу. Если нет — W2+W3 мёржатся как независимый PR, W4 — следующим PR. |
| R5 | **P4.5: неправильный выбор типов исключений** — не все `except Exception` блоки в файле имеют одинаковый контекст | MEDIUM | Каждый блок анализируется индивидуально. Стратегия: прочитать try-блок → определить какие операции → выбрать соответствующие exception types. Не применять шаблонную замену. |

---

## Verification Checklist

Перед merge проверить:

- [ ] **W2:** `grep 'sys.exit' core/internal/shared/project_registry.py | grep -v '__main__' | grep -v 'if __name__'` → 0
- [ ] **W2:** `python -m pytest tests/unit/test_project_registry.py -v` → all pass
- [ ] **W3:** `grep -r 'getLogger("[a-z]' core/internal/ core/modules/ --include='*.py'` → 0
- [ ] **W4:** `grep -r 'raise RuntimeError' core/internal/bootstrap/ --include='*.py'` → 0
- [ ] **W4:** `grep -rn 'except\s\+Exception' core/internal/ --include='*.py' | grep -v '__main__' | grep -v '# noqa: EXC'` → 0
- [ ] **W4:** `grep 'class.*Error.*PlatformError' core/internal/shared/exceptions.py` → 4 matches
- [ ] **W4:** `python -m pytest tests/unit/test_exceptions.py -v` → all pass
- [ ] **Gate:** `make check-exception-patterns` → exit 0, "All exception handlers are typed."
- [ ] **Gate:** `make gate MODE=fast` → exit 0
- [ ] **Tests:** `python -m pytest tests/ -s -v` → all pass (no regression)
- [ ] **Lint:** `ruff check .` → 0 errors

---

## Next Steps

```text
# Шаг 1: Реализация W2 + W3 (можно начинать сразу, не ждать 038a)
coder Read /Users/tronyx/projects/ai-platform/.ai/plans/038-arch-unification-node-yaml-errors-loggers/038b-DevPlan.md,
     implement Wave 2 (T2.1-T2.4) and Wave 3 (T3.1-T3.3)
     as Commits 1+2.

# Шаг 2: Реализация W4 (требует exceptions.py из 038a)
coder Read /Users/tronyx/projects/ai-platform/.ai/plans/038-arch-unification-node-yaml-errors-loggers/038b-DevPlan.md,
     implement Wave 4 (T4.1-T4.7) as Commit 3.
     Requires: core/internal/shared/exceptions.py must exist with 5 classes.

# Шаг 3: Финальная верификация
make gate MODE=fast && python -m pytest tests/ -s -v
```

$END_DEVPLAN
