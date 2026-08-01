# 16-DevPlan — B4: Контракты исключений и exit-кодов

<!-- GREP_SUMMARY: PlatformError exit-code bare-raise sys.exit main-int DEPLOY_BEST_EFFORT legacy-parity allowlist importability contracts -->
<!-- STRUCTURE: ┌решения архитектора D1-D6┐ → ◇ T1 contracts.py → ◇ T2 типизация raise → ◇ T3 бизнес sys.exit → ◇ T4 main()-контракт → ◇ T5 гейт bare-raise → ◇ T6 гейт sys.exit → ◇ T7 exit-коды docs → ◇ T8 legacy-parity гейт → ◇ T9 тесты → ⊕ T10 самоверификация -->
# region MODULE_CONTRACT
## @purpose  Волна B4 программы хардненинга (116): сделать типизированные исключения и exit-коды РАБОТАЮЩИМ контрактом вместо декорации. 40 bare raise → PlatformError-иерархия, business sys.exit → raise, единый main() -> int, legacy parity формализован как осознанная политика.
## @scope    U-12, U-29, U-39. Файлы: core/internal/shared/{contracts.py(NEW),exceptions.py,platform_deliver.py,secrets_manifest_reader.py,secrets_env_parser.py,ssh_command_parser.py}, core/internal/{llm/policy_schema.py,llm/config_renderer.py,secrets/decrypt_secrets.py,template_engine.py,provisioner.py,reconciler_projects.py,scripts/validate_module_yaml.py,scripts/generate_platform_env.py,healthcheck/metrics/json_writer.py,deploy/{deploy_engine.py,channels.py,context_promoter.py}}, core/internal/{scaffold/{project_scaffolder.py,project_adopter.py,gen_env_platform.py,context_initializer.py,context_registry.py,project_lister.py,project_remover.py,vhost_renderer.py},bootstrap/deploy/deploy_orchestrator.py}, core/AGENTS.md, core/entrypoint-manifest.yaml, tests/.
## @invariants
##   1. Бизнес-слой: только raise (PlatformError-иерархия), никогда sys.exit; sys.exit — только в main() и __main__.
##   2. Exit-коды: 0=ok, 1=generic, 2=ConfigNotFound, 3=ConfigParse, 4=Validation, 10=Fatal — единый контракт на весь core.
##   3. main() контракт: `def main() -> int` + `sys.exit(main())`; `except PlatformError as e: return e.exit_code`.
##   4. state_machine.py НЕ трогается (мораторий инварианта 4 программы до B9) — уже соответствует контракту, конфликтов нет.
##   5. Legacy parity — формализованная политика DEPLOY_BEST_EFFORT (shared/contracts.py + TRAP[DECISION] с rev-датой), сжимается волнами, а НЕ переписывается в B4.
##   6. Consumer-scan обязателен при любом изменении типа raise (инвариант 2 программы) — тесты, ожидающие ValueError/RuntimeError, мигрируются.
## @rationale Иерархия создана (2026-07-26), но retrofit не выполнен: 40 bare raise, sys.exit живёт в библиотечных функциях (provisioner:154, deploy_engine:953), caller не может программно различить тип ошибки; «legacy parity» декларируется комментариями (# noqa: EXC, «legacy parity» ×12) вместо контракта. Волна делает расхождение структурно невозможным через код + гейты.
## @changes 2026-08-01 · Решения пользователя: (D1) гейт bare-raise — pytest AST-сканер, не ruff-плагин; (D2) allowlist — константа _ALLOWLIST в тесте (паттерн B2), не YAML-файл; (D3) миграция ВСЕХ 61 main() на единый контракт; (D4) строгая семантика exit-кодов (docker недоступен → PlatformFatalError 10, first-deploy → PlatformFatalError 10; greenfield инвариант 9 разрешает смену кодов); (D5) legacy parity — новый shared/contracts.py с DEPLOY_BEST_EFFORT.
# endregion MODULE_CONTRACT

$ARTIFACT_CONTRACT:
  PURPOSE: Реализация волны B4 — 10 задач от shared/contracts.py до гейтов самоверификации (bare-raise AST-сканер, sys.exit-контракт, exit-коды документация).
  DESCRIPTION: Пошаговый план с точными файлами/строками, маппингом 40 bare raise на классы исключений, критериями приёмки на каждую U-проблему, новыми гейтами (trinity), порядком самоверификации.
  RATIONALE: Бриф фиксирует цели; DevPlan фиксирует решения архитектора (D1-D5, подтверждены пользователем 2026-08-01) и исполнительные шаги, чтобы Coder работал без архитектурных развилок.
  ACCEPTANCE_CRITERIA: (1) 0 bare raise ValueError/RuntimeError в core/internal (гейт с _ALLOWLIST=пусто); (2) единый `except PlatformError` → return e.exit_code во всех main(); (3) main()->int контракт: business-функции не вызывают sys.exit (гейт); (4) legacy parity — DEPLOY_BEST_EFFORT в shared/contracts.py + TRAP[DECISION] + allowlist-гейт на широкие except; (5) exit-коды 2/4/10 задокументированы в core/AGENTS.md и проверяются гейтом; (6) make gate MODE=fast зелёный.
  IMPLEMENTS: U-12 (40 bare raise), U-29 (business sys.exit ×2 + 61 main()-паттерн), U-39 (legacy parity ×12 → контракт)
  IMPACTS: shared/{contracts.py,exceptions.py}, llm/{policy_schema.py,config_renderer.py}, secrets/decrypt_secrets.py, template_engine.py, provisioner.py, deploy/{deploy_engine.py,channels.py,context_promoter.py,orchestrator_cli.py}, scaffold/*, bootstrap/deploy/deploy_orchestrator.py, core/AGENTS.md, core/entrypoint-manifest.yaml, tests/
  REQUIRES: 05-Brief (B4); решения пользователя 2026-08-01 (D1-D5); B2 (allowlist-механика гейтов); B5 (15-DevPlan — конфликтующие файлы deploy_engine/channels мигрируются поверх B5-состояния); greenfield (инвариант 9 — смена exit-кодов допустима)
---

## 1. Решения архитектора (подтверждены пользователем 2026-08-01)

| # | Вопрос | Решение |
|---|--------|---------|
| D1 | Механика гейта «0 bare raise» (AC1) | **Pytest-гейт с AST-сканером** — `tests/gates/test_gate_no_bare_raise.py`: AST-обход всех .py в core/internal, детект `raise ValueError/RuntimeError`, `_ALLOWLIST` исключений. Консистентно с прецедентом test_gate_exception_audit.py и B2 profiles_parity; 0 новых зависимостей; trinity-интеграция с make gate |
| D2 | Формат allowlist (AC1/AC4) | **Константа `_ALLOWLIST` в коде теста** (паттерн B2 test_gate_profiles_parity.py), НЕ YAML-файл. Сжимается волнами правкой теста; гейт валидирует, что каждая запись ещё существует (file:line) |
| D3 | Объём main()-миграции (AC2/AC3) | **Все 61 main() в core/internal** приводятся к `def main() -> int` + `sys.exit(main())` + `except PlatformError as e: return e.exit_code`. Механическая трансформация; единственное исключение — state_machine.py (мораторий B9; уже соответствует) |
| D4 | Семантика exit-кодов при типизации (AC5) | **Строгая семантика контракта.** provisioner:154 (docker недоступен) → raise PlatformFatalError (10); deploy_engine:953 (first-deploy failed) → raise PlatformFatalError (10). Смена кодов 2→10, 1→10 допустима (инвариант 9 greenfield, backward-compat не нужна); consumer-scan shell-фасадов обязателен |
| D5 | Размещение контракта legacy parity (AC4) | **Новый `shared/contracts.py`** — DEPLOY_BEST_EFFORT=True + константы exit-кодов + TRAP[DECISION] с rev-датой (2026-10-21). Потребители ≥2 (deploy_orchestrator, channels, гейт). Регистрация в shared/AGENTS.md инвентарь (19-й модуль) |
| D6 | Судьба no-raise контрактов (U-39, deploy_orchestrator) | Поведение legacy parity НЕ меняется в B4: WARN→exit 0, HC_DONE_MARKER, широкие except остаются (формализация через DEPLOY_BEST_EFFORT + allowlist-гейт). Сжатие — в B1/B8. Исключение: только перевод `except Exception` → более узкие, если это не меняет поведение |

---

## 2. Текущее состояние worktree (старт волны)

- HEAD `ec55571` (main, B2+B6 закоммичены), рабочее дерево чистое (кроме untracked 15-DevPlan.md B5).
- **ВАЖНО (порядок волн):** волна B5 (15-DevPlan.md) реализуется параллельной сессией. B4 стартует ПОСЛЕ завершения B5 (решение пользователя). Конфликтующие файлы — `deploy/deploy_engine.py`, `deploy/channels.py`, `bootstrap/converge/reconciler.py`, `deploy/context_promoter.py` (B5 трогает таймауты/SSH_OPTS/healthcheck, B4 — raise/sys.exit/main) — Coder работает поверх финального состояния B5 (rebase/merge перед стартом).
- Иерархия `shared/exceptions.py` существует: PlatformError(1)/ConfigNotFoundError(2)/ConfigParseError(3)/ConfigValidationError(4)/PlatformFatalError(10), создана DevPlan 038a. Retrofit НЕ выполнен.
- 40 bare raise ValueError/RuntimeError в 15 файлах (детальный маппинг — T2).
- Business sys.exit вне main(): `provisioner.py:154` (provision_networks, sys.exit(2)), `deploy_engine.py:953` (`_handle_first_deploy`, sys.exit(1), NoReturn), `context_initializer.py:98` (validate_name, sys.exit(1)), `context_registry.py:66` (register_context, sys.exit(1)).
- 61 main(): 26 уже `-> int`, 16 `-> None`, 19 без аннотации; образцы правильного паттерна — state_machine.py:1317, orchestrator_cli.py:144, key_provisioner.py, deploy_orchestrator.py:871.
- Legacy parity ×12 в `bootstrap/deploy/deploy_orchestrator.py` (WARN→exit 0, HC_DONE_MARKER, 14 широких `except Exception` c `# noqa: EXC`), exit-контракт CRIT→2/WARN→0 (строки 21, 786-801).
- Тесты, ожидающие ValueError/RuntimeError (сломаются): `tests/unit/test_secrets_manifest_reader.py:111,126`, `tests/test_validate_module_yaml.py:555,561`, `tests/test_ssl_s3_cache.py:212` (вне core/internal — проверить, НЕ мигрировать без необходимости).

---

## 3. Задачи

### T1 — U-39: NEW `shared/contracts.py` — контракт операционных политик [FUNDAMENT]

**1. Новый `core/internal/shared/contracts.py`** (MODULE_CONTRACT + GREP_SUMMARY/STRUCTURE, стандарт shared):
- `DEPLOY_BEST_EFFORT: bool = True` — deploy-политика legacy parity: «failing step не прерывает деплой; WARN→exit 0; HC_DONE_MARKER всегда» (U-39). Потребители: deploy_orchestrator (комментарии-инварианты → импорт), гейт T8.
- Константы exit-кодов (семантика из exceptions.py, но machine-readable для гейтов):
  ```python
  EXIT_OK = 0; EXIT_GENERIC = 1; EXIT_CONFIG_NOT_FOUND = 2
  EXIT_CONFIG_PARSE = 3; EXIT_CONFIG_VALIDATION = 4; EXIT_FATAL = 10
  ```
- ⚠️ TRAP[DECISION] · 2026-08-01 · HI · Legacy parity как осознанная политика, а не долг
  - Rejected: немедленное переписывание 12 legacy-точек deploy_orchestrator (риск регрессии деплой-канала в волне про исключения)
  - Reason: greenfield-деплой (B1) проектирует единый канал — legacy parity сожмётся там; B4 фиксирует политику контрактом + гейтом, не меняя поведение (D6)
  - Rev: 2026-10-21 — если B1 не сожмёт legacy parity → пересмотреть DEPLOY_BEST_EFFORT=False и мигрировать точки

**2. Регистрация в инвентарь:** `shared/AGENTS.md` — строка в таблице (19-й модуль, «Потребители: deploy_orchestrator, гейты B4»), @changes +1; `core/AGENTS.md` §New shared modules (086-стиль).

**3. Потребители-контракты:** в `deploy_orchestrator.py` заменить комментарии «(legacy parity)» на `from core.internal.shared.contracts import DEPLOY_BEST_EFFORT` (минимум 2 точки: _compute_exit_code:790, _set_hc_marker:815 — поведение не меняется, только явная связь с контрактом).

**Тесты:** `tests/unit/test_shared_contracts.py` (NEW): константы существуют, exit-коды согласованы с exceptions.py (2/3/4/10), DEPLOY_BEST_EFFORT is True (фиксация политики).

**Критерий:** `rg "legacy parity" core/internal/bootstrap/deploy/deploy_orchestrator.py` — только строки с импортом/ссылкой на contracts.py или docstring-упоминанием политики; инвентарь shared/AGENTS.md обновлён.

---

### T2 — U-12: Типизация 40 bare raise → PlatformError-иерархия [FUNDAMENT]

**Маппинг (файл → класс → обоснование):**

| Файл (кол-во) | Класс | Обоснование |
|----------------|-------|-------------|
| `llm/policy_schema.py` (9) | `ConfigValidationError` | Валидация структуры policy-конфига |
| `scripts/validate_module_yaml.py` (6) | `ConfigValidationError` | Валидация module.yaml (env_requires/структура) |
| `shared/secrets_manifest_reader.py` (4) | `ConfigValidationError` | Структура manifest (не dict, пустой, не list) |
| `secrets/decrypt_secrets.py` (4) | `PlatformFatalError` | Security-path: отсутствие AGE-ключа, sops failure, write failure — требует ручного вмешательства (бриф: decrypt_secrets → PlatformFatalError) |
| `shared/platform_deliver.py` (3) | `ConfigValidationError` | Невалидные аргументы verb-команды |
| `llm/config_renderer.py` (3) | `ConfigValidationError` | Невалидная структура рендера/отсутствие провайдера |
| `template_engine.py` (2) | `ConfigValidationError` | Невалидные KEY=value пары |
| `shared/ssh_command_parser.py` (2) | `ConfigValidationError` | Пустая команда после stripping |
| `shared/secrets_env_parser.py` (1) | `ConfigValidationError` | Unicode decode (структура данных) |
| `scripts/generate_platform_env.py` (1) | `ConfigParseError` | YAML-корень не dict (parse, не validation) |
| `scaffold/vhost_renderer.py` (1) | `PlatformFatalError` | nginx -t validation failed — внешний инструмент, невосстановимо без ручного действия |
| `scaffold/project_adopter.py` (1) | `ConfigValidationError` | Невалидный ввод адаптации |
| `healthcheck/metrics/json_writer.py` (1) | `ConfigParseError` | Temp-файл невалидный JSON (parse) |
| `deploy/context_promoter.py` (1) | `ConfigValidationError` | GIT_MIRROR_TOKEN пуст (структура окружения) |
| `deploy/channels.py` (1) | `ConfigValidationError` | Payload требует tar_path+project_name |

**Правила миграции:**
1. Каждый raise: `raise ValueError(...)` → `raise ConfigValidationError(...)` (по таблице); import из shared.exceptions (проверить существующий import-паттерн в файле: try/except-импорты в bootstrap-слое — сохранить паттерн).
2. Сообщения сохраняются дословно (контракт логов/тестов); `from e`-цепочки сохраняются.
3. Если класс-исключение уже импортирован файлом — просто замена типа.
4. Тесты, ожидающие ValueError/RuntimeError от этих функций — мигрировать на новые классы (см. T9; `pytest.raises(ConfigValidationError)` и т.д.).

**Критерий:** `rg "raise (ValueError|RuntimeError)" core/internal --glob "*.py"` → 0; `make test` по затронутым unit-файлам зелёный.

---

### T3 — U-29: Удаление business sys.exit → raise [FUNDAMENT]

**1. `deploy/deploy_engine.py:937-954` `_handle_first_deploy`:** `-> NoReturn` + `sys.exit(1)` → `-> None` + `raise PlatformFatalError(f"First deploy failed — no rollback possible: {reason}")`. Callsites 349, 355, 383: обновить комментарии «unreachable — _handle_first_deploy raises SystemExit» → «raises PlatformFatalError»; проверить, что вышестоящий обработчик ловит PlatformError (deploy() → CLI main T4).

**2. `provisioner.py:154` `provision_networks`:** `sys.exit(2)` (docker недоступен) → `raise PlatformFatalError("Docker is not available — provision networks requires docker")` (D4). main() (319-388): dispatch-ветки обернуть — `provision_networks(...)` в try/except PlatformError → `return e.exit_code` (паттерн T4); docstring exit-коды (2 — docker unavailable) обновить: 10 — docker unavailable.

**3. `scaffold/context_initializer.py:98` `validate_name`** (B6 D3 враппер с SystemExit): перевести на контракт — `if not validate_project_name(name): raise ConfigValidationError(f"Invalid name: {name}")`; callsite в main() (465?) — уже в try/except? Проверить; иначе обернуть в main по T4.

**4. `scaffold/context_registry.py:66` `register_context`:** `sys.exit(1)` → `raise ConfigValidationError(...)` (или PlatformFatalError для IO-ошибок — по контексту строки 63-75: чтение/запись ошибки → PlatformFatalError, дубликат → ConfigValidationError как в B6 T6.4 «EXISTS»); callsite `context_initializer.py:524` (`sys.exit(reg_rc)`) — адаптировать.

**5. `scaffold/project_scaffolder.py:623-672`:** sys.exit внутри main() — допустимо по контракту? НЕТ: контракт D3 — main() -> int, sys.exit только в `__main__`. Заменить `sys.exit(N)` → `return N` (623, 637, 646, 664, 672); `main() -> None` → `-> int`.

**6. Consumer-scan (инвариант 2):** `rg "sys.exit" core/internal --glob "*.py"` после миграции — только внутри `def main()` / `if __name__ == "__main__":` (проверяется гейтом T6).

**Критерий:** rg sys.exit в core/internal — 0 вне main()/__main__; importability-тесты (T9): `import provisioner; provisioner.provision_networks(...)` без docker → PlatformFatalError, процесс жив.

---

### T4 — U-29: Единый main()-контракт — все 61 main() [CODE]

**Паттерн (канон — state_machine.py:1317/1435, orchestrator_cli.py:144/232):**
```python
def main() -> int:
    """..."""
    try:
        ...
        return 0
    except PlatformError as e:
        logger.critical("[IMP:10][main] Unhandled platform error (exit=%d): %s", e.exit_code, e)
        print(f"[FATAL] {e}", file=sys.stderr)
        return e.exit_code

if __name__ == "__main__":
    sys.exit(main())
```

**Объём (61 файл, список):**
- Уже `-> int` (26): проверить наличие except PlatformError → return e.exit_code (state_machine, orchestrator_cli, key_provisioner, deploy_orchestrator, provisioner, reconciler_projects, docker_orchestrator, context_deployer, context_overlay, deploy_engine CLI, ...) — добавить хендлер где отсутствует.
- `-> None` (16): `def main() -> None` → `def main() -> int` + `return 0`/`return e.exit_code`; внутренние sys.exit(N) → return N (project_scaffolder, gen_env_platform, context_initializer, project_lister, project_remover, decrypt_secrets, vhost_renderer, ...).
- Без аннотации (19): добавить `-> int` по тому же паттерну.
- **Исключения (мораторий):** state_machine.py не трогать (уже соответствует).

**Правила:**
1. Поведение exit-кодов сохраняется, кроме случаев D4 (строгая семантика: где был голый `sys.exit(1)` при PlatformError-условии → return e.exit_code).
2. `except Exception` финальный catch-all в main() — допустим (top-level CLI handler, паттерн state_machine:1443), помечать `# noqa: EXC — top-level CLI handler for unexpected errors`.
3. LOG-формат: `[IMP:10][main] Unhandled platform error (exit=%d): %s` единый.
4. Не менять аргументы main(argv: list[str] | None = None) — где есть, сохранить.

**Критерий:** 61/61 main() → `-> int` + `sys.exit(main())` в __main__; `rg "def main\(\) -> None" core/internal` → 0; `rg "sys\.exit\(" core/internal --glob "*.py"` — только в main()/__main__.

---

### T5 — U-12: Гейт «0 bare raise» — test_gate_no_bare_raise.py [ENFORCEMENT]

**1. NEW `tests/gates/test_gate_no_bare_raise.py`** (@pytest.mark.gate + @ldd_trajectory):
- AST-обход всех `core/internal/**/*.py` (паттерн test_gate_exception_audit.py: `ast.parse`, walk, ast.Raise → exc.func.id in {"ValueError", "RuntimeError"});
- `_ALLOWLIST: set[tuple[str, int]]` = пустое множество ПОСЛЕ миграции T2 (стартовое значение при разработке — текущие 40 записей file:line, сжимается до 0 к концу волны);
- Валидация allowlist: каждая запись (file, line) реально существует и содержит raise (иначе — stale-запись → fail, сжимается);
- Allowlist-исключение из файла: `shared/exceptions.py` сам (класс-определения, raise PlatformError внутри) — НЕ матчится (сканер ловит только ValueError/RuntimeError);
- Учёт моратория: если в state_machine.py появится bare raise — RED (он уже чистый, мораторий не спасает от новых).

**2. Регистрация (trinity):** запись в `core/entrypoint-manifest.yaml` секция gates (id: no-bare-raise, repair_class: L3 — ручная миграция); `make generate-manifests` (G3) + `make check-manifests` зелёный.

**Критерий:** гейт зелёный; `_ALLOWLIST` пуст; `make gate MODE=fast` включает гейт.

---

### T6 — U-29: Гейт sys.exit-контракта + importability [ENFORCEMENT]

**1. NEW `tests/gates/test_gate_sys_exit_contract.py`** (@pytest.mark.gate):
- AST-сканер core/internal: sys.exit встречается ТОЛЬКО внутри `def main(...)` тела ИЛИ в `if __name__ == "__main__":` блоке;
- Точность: проверять `ast.Call` с `func.id == "exit"`/attr `sys.exit` (import-анализ: имя sys), родительские узлы — FunctionDef с именем main / If с test `__name__ == "__main__"`;
- Дополнительно: `def main() -> None` в core/internal → RED (контракт D3).
- Allowlist: пуст (после T3/T4); при моратории state_machine — файл уже чист.

**2. Importability-тест (AC волны «гейт importability»):** `tests/unit/test_importability_no_exit.py` (NEW): import всех библиотечных модулей из списка (provisioner, deploy_engine, context_registry, context_initializer, scaffold-модули, shared/*) — процесс не завершается (SystemExit не бросается на import); прямой вызов `provision_networks` без docker → PlatformFatalError (не SystemExit).

**3. Регистрация trinity** как T5.2.

**Критерий:** гейт зелёный; importability-тест PASS; `rg "def main\(\) -> None" core/internal` → 0 (подтверждение T4).

---

### T7 — AC5: Документация exit-кодов + гейт [ENFORCEMENT]

**1. `core/AGENTS.md`:** секция «Exit-коды» (рядом с глоссарием/каноническими операциями):
```
| Код | Семантика | Исключение |
|-----|-----------|------------|
| 0 | ok | — |
| 1 | generic error | PlatformError base |
| 2 | ConfigNotFound | ConfigNotFoundError (файл можно создать) |
| 3 | ConfigParse | ConfigParseError (синтаксис YAML/JSON) |
| 4 | ConfigValidation | ConfigValidationError (структура) |
| 10 | Fatal — ручное вмешательство | PlatformFatalError |
```
+ инвариант: «business-функции НЕ вызывают sys.exit; sys.exit только в main()/__main__» (дублирует root AGENTS.md глоссарий при необходимости — нет, root не трогаем, только core/AGENTS.md).

**2. NEW `tests/gates/test_gate_exit_codes_documented.py`** (@pytest.mark.gate): core/AGENTS.md содержит строки для кодов 2/4/10 с упоминанием классов ConfigNotFoundError/ConfigValidationError/PlatformFatalError (простое substring/line-сравнение).

**3. Регистрация trinity.**

**Критерий:** гейт зелёный; документация в core/AGENTS.md присутствует.

---

### T8 — U-39: Allowlist-гейт на широкие except (legacy parity) [ENFORCEMENT]

**1. NEW `tests/gates/test_gate_broad_except_allowlist.py`** (@pytest.mark.gate):
- AST-сканер: `except Exception` в core/internal ДОПУСТИМ только если: (а) строка-код содержит `# noqa: EXC`, И (б) в теле/комментарии есть маркер политики — `DEPLOY_BEST_EFFORT` или `legacy parity` или `best-effort` или `top-level CLI handler` (список допустимых маркеров — константа в тесте);
- Allowlist-исключение: существующие 14 мест deploy_orchestrator.py (все уже `# noqa: EXC` + legacy-комментарии) — проходят по маркерам; НОВЫЕ широкие except без маркера → RED;
- Сжатие: B4 не требует сокращения числа (D6); гейт фиксирует отсутствие НОВЫХ неразмеченных.

**2. `deploy_orchestrator.py`:** docstring-инварианты 236/398/427/486/790 уже ссылаются на legacy parity — добавить ссылку `DEPLOY_BEST_EFFORT` (импорт из contracts.py, T1.3) в _compute_exit_code/_set_hc_marker.

**3. Регистрация trinity.**

**Критерий:** гейт зелёный; 0 новых широких except без маркеров; deploy_orchestrator импортирует DEPLOY_BEST_EFFORT.

---

### T9 — U-12/U-29: Тесты [CODE]

**1. Миграция ожидающих ValueError/RuntimeError (consumer-scan, инвариант 2):**
- `tests/unit/test_secrets_manifest_reader.py:111,126` → `pytest.raises(ConfigValidationError)`;
- `tests/test_validate_module_yaml.py:555,561` → `pytest.raises(ConfigValidationError)`;
- `tests/test_ssl_s3_cache.py:212` — вне core/internal (ssl_s3_cache модуль hermes-agent?): проверить, что raise НЕ из типизируемых файлов; если из shared/ — мигрировать; если нет — не трогать (зафиксировать в отчёте).

**2. Новые тесты:**
- `tests/unit/test_shared_contracts.py` (T1);
- `tests/unit/test_importability_no_exit.py` (T6.2);
- `tests/unit/test_provisioner_no_docker.py`: provision_networks(platform_env с networks, dry_run=False) в среде без docker (mock shutil.which → None) → raises PlatformFatalError, exit_code == 10;
- `tests/unit/test_deploy_engine_first_deploy.py`: _handle_first_deploy(...) → raises PlatformFatalError (а не SystemExit);
- update `tests/test_converge_exit.py`/инвентарь при необходимости (консервирующие тесты на sys.exit-коды).

**3. LDD:** все новые тесты — caplog IMP:7-10 траектория + assert IMP:9 (стандарт §TESTING).

**Критерий:** make test MARKER=static зелёный; новые unit-тесты PASS; 0 тестов, ожидающих ValueError/RuntimeError от типизированных функций.

---

### T10 — Самоверификация волны [VERIFY]

1. **Порядок:** старт ТОЛЬКО после завершения B5 (15-DevPlan реализация закоммичена); при конфликтах в deploy_engine/channels/reconciler/context_promoter — `git merge`/rebase на финальный B5-HEAD перед началом.
2. `make fix-gate && git add -u` — чистое дерево.
3. `make gate MODE=fast` — зелёный (включает 4 новых гейта: no-bare-raise, sys-exit-contract, exit-codes-documented, broad-except-allowlist); `make check-manifests` — зелёный.
4. Consumer-scan чек-лист:
   - `raise ValueError/RuntimeError` → 0 в core/internal;
   - `sys.exit` → только main()/__main__;
   - `pytest.raises(ValueError|RuntimeError)` → 0 для типизированных функций (test_ssl_s3_cache проверен);
   - `_handle_first_deploy` callsites (349, 355, 383) — комментарии обновлены;
   - shell-фасады, проверяющие exit-код 2 от provisioner (provision.mk/CI): docker-unavailable теперь 10 — обновить (инвариант 9; rg "provision" makefiles/ .github/).
5. Обновление консервирующих тестов: test_validate_module_yaml, test_secrets_manifest_reader, test_converge_exit (если sys.exit-зависим).
6. Коммит одним или несколькими логическими коммитами (стиль `fix(116): ...` / `feat(116): ...`), включая регенерированные entrypoint-manifest/AGENTS.md (make generate-manifests) и новый shared/AGENTS.md-инвентарь.

---

## 4. Порядок и зависимости

T1 (contracts.py) → T2 (типизация raise) + T3 (business sys.exit) → T4 (main-миграция, зависит от T2/T3 — финальное состояние) → T5/T6/T7/T8 (гейты после предметов; T5 после T2, T6 после T3/T4, T7/T8 после T1) → T9 (тесты параллельно с T2-T4) → T10.

Критический путь: T2 (40 raise) → T4 (61 main) → T6 (гейт sys.exit).

Логические волны для Coder'а (одна сессия, последовательно):
- **Wave 1:** T1 + T7 + T8 (contracts.py, документация, legacy-гейт) — фундамент без риска;
- **Wave 2:** T2 (типизация 40 raise) + T9-тесты-миграции;
- **Wave 3:** T3 (business sys.exit) + T4 (61 main);
- **Wave 4:** T5 + T6 (гейты) + T10 (верификация).

## 5. Риски

| Риск | Митигация |
|------|-----------|
| Конфликт с B5 (deploy_engine/channels/reconciler/context_promoter) | Старт после завершения B5; rebase/merge; T-порядок фиксирован (п.4) |
| Смена exit-кода 2→10 (provisioner docker) ломает shell-фасады/CI | Consumer-scan (T10.4): rg provisioner-кодов в makefiles/.github; greenfield (инвариант 9) разрешает |
| Тесты, ожидающие ValueError/RuntimeError, молча пропускают миграцию | T9 consumer-scan + гейт T5 (0 bare raise) ловит дрейф |
| Main()-миграция 61 файла — механические правки ломают поведение | Паттерн-канон (T4) + importability-тест (T6.2) + make gate |
| Широкие except без маркеров в не-деплой модулях | T8 гейт: маркеры `noqa: EXC` + политика; существующие помечены |
| state_machine (мораторий) — случайная правка | T5/T6 allowlist пуст, файл чист; запрет правок в DevPlan |
| Регенерация entrypoint-manifest ломает другие гейты | B2-прецедент: make generate-manifests + check-manifests в T10 |

## 6. Сдача волны

Все 5 AC брифа: (1) 0 bare raise (гейт T5, _ALLOWLIST пуст); (2) единый except PlatformError → return e.exit_code во всех 61 main(); (3) business-функции без sys.exit (гейт T6); (4) DEPLOY_BEST_EFFORT в shared/contracts.py + TRAP[DECISION] + гейт T8; (5) exit-коды 2/4/10 в core/AGENTS.md + гейт T7. Дополнительно: `make gate MODE=fast` зелёный; 4 новых гейта зарегистрированы (trinity); make test MARKER=static зелёный; коммит без смешивания с B5.
