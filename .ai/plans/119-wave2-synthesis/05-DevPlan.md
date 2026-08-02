# 05-DevPlan — Бриф D: Shell→Python Migration

<!-- $ARTIFACT_CONTRACT
PURPOSE:          Миграция shell-бизнес-логики в Python: cert-валидация в issue-cert.sh (дубль ssl_certs),
                  tor-proxy install/privoxy-конфиг, module-interface dual-SoT, hermes-agent init/healthcheck.
DESCRIPTION:      9 задач (D1–D9). Каждая заменяет shell-бизнес-логику на Python-модуль, оставляя shell
                  как тонкий фасад (<150 LOC). Строгое соблюдение языковой политики: 0 новых inline python3.
RATIONALE:        Аудит 1 выявил shell-бизнес-логику, дублирующую существующие Python-модули (ssl_certs,
                  module_interface) или не имеющую тестов (tor-proxy packages, privoxy config).
ACCEPTANCE_CRITERIA:
  - AC-D-1: `make gate MODE=fast` зелёный после каждой миграции
  - AC-D-2: 0 новых inline python3 (heredoc/python3 -c) в изменённых shell-файлах
  - AC-D-3: Каждый новый Python-модуль имеет ≥1 unit-тест
  - AC-D-4: Shell-фасады <150 LOC (кроме issue-cert.sh — 719 LOC, не трогаем полностью)
  - AC-D-5: Языковая политика: новый код — Python, shell — тонкий фасад
IMPLEMENTS:       Бриф D из 01-Brief.md (волна 119) — Shell→Python Migration.
IMPACTS:          core/internal/bootstrap/issue-cert.sh, core/internal/shared/ssl_certs.py,
                  core/internal/bootstrap/install-tor-proxy.sh, core/internal/bootstrap/tor_setup.py (NEW),
                  core/internal/bootstrap/privoxy_config.py (NEW), core/lib/module-interface.sh,
                  core/internal/shared/module_interface.py, core/modules/hermes-agent/build/scripts/init.sh,
                  core/modules/hermes-agent/build/scripts/init.py (NEW),
                  core/modules/hermes-agent/healthcheck.sh,
                  core/modules/hermes-agent/healthcheck_deps.py (NEW),
                  core/entrypoints/deploy.sh.
REQUIRES:         Результаты аудита 1 (shell-бизнес-логика F1-F10).
-->

# DevPlan D — Shell→Python Migration

## $START_DEVPLAN

### Контекст

Волна 119, бриф D. Четвёртая волна — миграция shell-бизнес-логики в Python. Зависит от брифа B (shared модули — ssl_certs, module_interface уже существуют как канон). Строгое соблюдение языковой политики: новый код — Python, shell остаётся тонким фасадом.

---

## $TASKS

### TASK-D1: issue-cert.sh cert-валидация → ssl_certs CLI facade

| Поле | Значение |
|------|----------|
| **ID** | D1 |
| **Sev** | HIGH |
| **Сложность** | 5/10 |
| **Файлы** | `issue-cert.sh`, `shared/ssl_certs.py` |
| **Зависимости** | B5 (OpenSSL timeout — уже unified) |
| **Риск** | MED — удаление shell-функций из 719 LOC скрипта |

**Описание:**
`issue-cert.sh:_is_le_cert()` (L64-72) и `_acme_verify_cert()` (L372-406) дублируют `ssl_certs.cert_is_le_issuer()` и `ssl_certs.cert_check_expiry()`. Вызовы: L514, 647, 694, 703.

**Шаги:**
1. Добавить CLI-фасад в `shared/ssl_certs.py`: `if __name__ == "__main__"` с аргументами `--is-le` и `--check-expiry CERT DAYS` (паттерн `ssh_opts --shell`).
2. В `issue-cert.sh`: заменить вызовы `_is_le_cert()` на `python3 -m core.internal.shared.ssl_certs --is-le <cert>`.
3. В `issue-cert.sh`: заменить `_acme_verify_cert()` на `python3 -m core.internal.shared.ssl_certs --check-expiry <cert> <days>`.
4. Удалить shell-функции `_is_le_cert()` и `_acme_verify_cert()`.
5. Удалить `openssl x509 -enddate | cut -d= -f2-` пайплайн (L383) — он внутри `_acme_verify_cert`.
6. R5 negative-тест: `test_issue_cert_wrapper_consistency` — сравнить вывод shell и Python на одном сертификате.

**Acceptance Criteria:**
- AC-D1.1: `grep "_is_le_cert\|_acme_verify_cert" issue-cert.sh` → 0 (функции удалены)
- AC-D1.2: `issue-cert.sh` вызывает `python3 -m core.internal.shared.ssl_certs`
- AC-D1.3: `python3 -m core.internal.shared.ssl_certs --is-le /path/to/cert` → exit 0/1
- AC-D1.4: R5 negative-тест: shell и Python возвращают одинаковый результат на одном сертификате

---

### TASK-D2: install-tor-proxy.sh install_packages → tor_setup.py

| Поле | Значение |
|------|----------|
| **ID** | D2 |
| **Sev** | MED |
| **Сложность** | 6/10 |
| **Файлы** | `install-tor-proxy.sh`, `bootstrap/tor_setup.py` (NEW), `shared/tor_transport.py` |
| **Зависимости** | нет |
| **Риск** | MED — test-first, сложная state-machine |

**Описание:**
`install_packages()` (L52-116) — деградационная state-machine (webtunnel→obfs4 fallback). Извлечь в Python `tor_setup.py`, test-first.

**Шаги:**
1. Написать тесты для tor_setup: `test_tor_package_install_webtunnel`, `test_tor_package_fallback_obfs4`, `test_tor_package_all_absent`.
2. Создать `bootstrap/tor_setup.py`:
   - `install_tor_packages(dry_run: bool) -> list[str]` — возвращает список установленных пакетов.
   - `detect_available_transports() -> dict` — какие транспорты доступны в репозиториях.
3. В `install-tor-proxy.sh`: заменить `install_packages()` на `python3 tor_setup.py --install`.
4. Shell-функция становится тонким фасадом (<20 LOC).
5. R5 negative-тест: `test_tor_package_fallback` — webtunnel отсутствует → obfs4 выбран.

**Acceptance Criteria:**
- AC-D2.1: `tor_setup.py` существует с unit-тестами (test-first)
- AC-D2.2: `install-tor-proxy.sh:install_packages()` → вызов `python3 tor_setup.py`
- AC-D2.3: Shell-функция <20 LOC
- AC-D2.4: R5 negative-тест: fallback webtunnel→obfs4 работает

---

### TASK-D3: install-tor-proxy.sh write_privoxy_config → privoxy_config.py

| Поле | Значение |
|------|----------|
| **ID** | D3 |
| **Sev** | MED |
| **Сложность** | 4/10 |
| **Файлы** | `install-tor-proxy.sh`, `bootstrap/privoxy_config.py` (NEW) |
| **Зависимости** | нет (можно параллельно с D2) |
| **Риск** | LOW — идемпотентная мутация, легко тестируется |

**Описание:**
`write_privoxy_config()` (L172-213) — идемпотентная мутация (grep-guard + sed). Извлечь в Python-мутатор ~40 LOC.

**Шаги:**
1. Создать `bootstrap/privoxy_config.py`:
   - `write_privoxy_config(config_path, listen_addr, forward_addr) -> bool` — True если изменения внесены.
   - Идемпотентность: если конфиг уже содержит нужные строки → no-op.
2. Тесты: `test_privoxy_config_write`, `test_privoxy_config_idempotent`, `test_privoxy_config_no_clobber`.
3. В `install-tor-proxy.sh`: заменить `write_privoxy_config()` на `python3 privoxy_config.py`.
4. R5 negative-тест: `test_privoxy_config_idempotent` — двойной вызов не ломает конфиг.

**Acceptance Criteria:**
- AC-D3.1: `privoxy_config.py` существует с unit-тестами
- AC-D3.2: `install-tor-proxy.sh:write_privoxy_config()` → вызов Python
- AC-D3.3: Двойной вызов = no-op (идемпотентность)
- AC-D3.4: R5 negative-тест: конфиг не повреждён после повторной записи

---

### TASK-D4: module-interface.sh → thin facade over Python

| Поле | Значение |
|------|----------|
| **ID** | D4 |
| **Sev** | MED |
| **Сложность** | 3/10 |
| **Файлы** | `core/lib/module-interface.sh`, `shared/module_interface.py` |
| **Зависимости** | B4 (subprocess_io канон) |
| **Риск** | LOW — Python-канон уже существует (118 C5) |

**Описание:**
`module-interface.sh` (206 LOC) — dual-SoT с Python `shared/module_interface.py` (уже создан в 118 C5). Shell-библиотека дублирует логику. Свести shell к тонкому фасаду: `invoke_module_interface()` → `python3 -m core.internal.shared.module_interface invoke "$@"`.

**Шаги:**
1. Проверить, что `shared/module_interface.py` покрывает все use-case shell-библиотеки.
2. Если нет — расширить Python-модуль.
3. `module-interface.sh`: заменить тело функций на вызов `python3 -m core.internal.shared.module_interface <command>`.
4. Shell → <30 LOC (только source/shims).
5. R5 negative-тест: `test_module_interface_shell_parity` — shell и Python возвращают одинаковый результат.

**Acceptance Criteria:**
- AC-D4.1: `module-interface.sh` <30 LOC (тонкий фасад)
- AC-D4.2: Все вызовы `invoke_module_interface` проходят через Python-канон
- AC-D4.3: R5 negative-тест: shell/Python parity

---

### TASK-D5: hermes-agent init.sh → init.py

| Поле | Значение |
|------|----------|
| **ID** | D5 |
| **Sev** | MED |
| **Сложность** | 4/10 |
| **Файлы** | `hermes-agent/build/scripts/init.sh`, `hermes-agent/build/scripts/init.py` (NEW) |
| **Зависимости** | нет |
| **Риск** | LOW — cont-init скрипт, изолирован |

**Описание:**
`init.sh` (157 LOC) — cont-init бизнес-логика. Извлечь в `init.py` ~80 LOC, оставить shell-фасад.

**Шаги:**
1. Создать `init.py`: класс `HermesInit` с методами `setup_dirs()`, `check_config()`, `init_state()`.
2. `init.sh` → `#!/usr/bin/env bash; exec python3 /usr/local/bin/init.py "$@"` (5 LOC).
3. Unit-тесты для init.py.
4. R5 negative-тест: `test_hermes_init_py_parity` — сравнить поведение.

**Acceptance Criteria:**
- AC-D5.1: `init.py` существует (≥80 LOC Python)
- AC-D5.2: `init.sh` <10 LOC (тонкий фасад)
- AC-D5.3: Dockerfile копирует init.py
- AC-D5.4: R5 negative-тест: parity test

---

### TASK-D6: hermes-agent healthcheck deps-mode → Python

| Поле | Значение |
|------|----------|
| **ID** | D6 |
| **Sev** | MED |
| **Сложность** | 3/10 |
| **Файлы** | `hermes-agent/healthcheck.sh`, `hermes-agent/healthcheck_deps.py` (NEW) |
| **Зависимости** | нет |
| **Риск** | LOW — декларативная логика |

**Описание:**
`healthcheck.sh:48-112` — deps-режим (required/optional агрегация). Извлечь в Python `healthcheck_deps.py` ~50 LOC ИЛИ декларативную таблицу в module.yaml.

**Шаги:**
1. Создать `healthcheck_deps.py`: `check_deps(module_yaml_path) -> DepsResult`.
2. ИЛИ: добавить `healthcheck.deps` в `module.yaml` как декларативную таблицу.
3. `healthcheck.sh` → вызов Python для deps-режима.
4. R5 negative-тест: `test_hermes_hc_deps_aggregation` — проверка агрегации.

**Acceptance Criteria:**
- AC-D6.1: `healthcheck_deps.py` существует ИЛИ deps в module.yaml
- AC-D6.2: `healthcheck.sh` deps-режим → Python
- AC-D6.3: R5 negative-тест: required missing → unhealthy, optional missing → healthy

---

### TASK-D7: deploy.sh TRAP-актуальность

| Поле | Значение |
|------|----------|
| **ID** | D7 |
| **Sev** | LOW |
| **Сложность** | 1/10 |
| **Файлы** | `core/entrypoints/deploy.sh` |
| **Зависимости** | нет |
| **Риск** | LOW — только комментарий |

**Описание:**
`deploy.sh` (172 LOC) — Rev-условие 117 H D60: удаление после верификации A на production. Production не верифицирован → НЕ удалять. Обновить TRAP-комментарий с актуальным статусом.

**Шаги:**
1. Обновить TRAP[DECISION] в deploy.sh: Rev-условие не выполнено, статус актуален на 2026-08-02.

**Acceptance Criteria:**
- AC-D7.1: TRAP в deploy.sh содержит дату 2026-08-02 и статус «production не верифицирован»

---

### TASK-D8: LOW/INFO shell exceptions — документирование

| Поле | Значение |
|------|----------|
| **ID** | D8 |
| **Sev** | LOW |
| **Сложность** | 1/10 |
| **Файлы** | `AGENTS.md` (root) |
| **Зависимости** | нет |
| **Риск** | LOW — документация |

**Описание:**
F9/F11-F16 из аудита 1 — shell-скрипты, классифицированные как LOW/INFO (документированные исключения). Задокументировать в AGENTS.md, почему они НЕ мигрируются сейчас.

**Acceptance Criteria:**
- AC-D8.1: В AGENTS.md (root) таблица shell-исключений с причинами keep

---

### TASK-D9: F2 openssl x509 pipeline — удаление

| Поле | Значение |
|------|----------|
| **ID** | D9 |
| **Sev** | LOW |
| **Сложность** | 1/10 |
| **Файлы** | `issue-cert.sh` |
| **Зависимости** | D1 (удаляется вместе с _acme_verify_cert) |
| **Риск** | LOW — уходит вместе с D1 |

**Описание:**
`issue-cert.sh:383` — `openssl x509 -enddate | cut -d= -f2-` — единственный text-extraction пайплайн. Уходит вместе с удалением `_acme_verify_cert()` в D1.

**Acceptance Criteria:**
- AC-D9.1: `grep "openssl x509 -enddate.*cut" issue-cert.sh` → 0

---

## $PARALLEL_GROUPS

### Wave 1 (независимые по файлам)
```
coder Read .ai/plans/119-wave2-synthesis/05-DevPlan.md, implement Wave 1: D1, D2, D3, D4, D5, D6, D7, D8
```

D9 включена в D1.

**Файловые пересечения:**
- D1: `issue-cert.sh` + `ssl_certs.py`
- D2: `install-tor-proxy.sh` + `tor_setup.py` (NEW)
- D3: `install-tor-proxy.sh` + `privoxy_config.py` (NEW) — D2 и D3 меняют ОДИН файл (install-tor-proxy.sh) → НЕ параллелить
- D4: `module-interface.sh` + `module_interface.py`
- D5: `init.sh` + `init.py` (NEW)
- D6: `healthcheck.sh` + `healthcheck_deps.py` (NEW)
- D7: `deploy.sh`
- D8: `AGENTS.md`

**Скорректированные группы:**
- Group 1 (независимые): D1, D4, D5, D6, D7, D8
- Group 2 (после Group 1): D2
- Group 3 (после D2, общий файл install-tor-proxy.sh): D3

---

## $TEST_SPEC

| Test file | Test function | Scenario | Module under test |
|-----------|---------------|----------|-------------------|
| `tests/unit/test_ssl_certs.py` | `test_cli_is_le` | `--is-le` возвращает 0 для LE-сертификата | ssl_certs CLI |
| `tests/unit/test_ssl_certs.py` | `test_issue_cert_wrapper_consistency_negative` | R5: shell и Python возвращают одинаковый результат | ssl_certs vs issue-cert.sh |
| `tests/unit/test_tor_setup.py` | `test_install_tor_packages_webtunnel` | webtunnel доступен → установлен | tor_setup |
| `tests/unit/test_tor_setup.py` | `test_tor_package_fallback_obfs4_negative` | R5: webtunnel отсутствует → obfs4 | tor_setup |
| `tests/unit/test_privoxy_config.py` | `test_write_privoxy_config` | Запись конфига | privoxy_config |
| `tests/unit/test_privoxy_config.py` | `test_privoxy_config_idempotent_negative` | R5: двойной вызов = no-op | privoxy_config |
| `tests/unit/test_module_interface.py` | `test_module_interface_shell_parity_negative` | R5: shell и Python возвращают одинаковый результат | module_interface |
| `tests/unit/test_hermes_init.py` | `test_init_py_parity_negative` | R5: init.sh и init.py — одинаковое поведение | hermes-agent init |
| `tests/unit/test_hermes_healthcheck.py` | `test_hc_deps_aggregation_negative` | R5: required missing → unhealthy, optional → healthy | healthcheck deps |

---

## Acceptance Criteria Summary

| Критерий | Проверка |
|----------|----------|
| AC-D-ALL | `make gate MODE=fast && make check-manifests && ruff check .` зелёные |
| AC-D-PYTHON | 6 новых Python-модулей (ssl_certs CLI расширен, tor_setup, privoxy_config, init.py, healthcheck_deps.py) |
| AC-D-FACADE | 5 shell-файлов сокращены до тонких фасадов (module-interface <30, init.sh <10, etc.) |
| AC-D-INLINE | 0 новых inline python3 / heredoc в shell-файлах |
| AC-D-R5 | Каждая миграция имеет parity-тест (shell vs Python) |

---

## Next Steps

### Group 1
```
coder Read .ai/plans/119-wave2-synthesis/05-DevPlan.md, implement Group 1: D1, D4, D5, D6, D7, D8
```
### Group 2
```
coder Read .ai/plans/119-wave2-synthesis/05-DevPlan.md, implement Group 2: D2
```
### Group 3
```
coder Read .ai/plans/119-wave2-synthesis/05-DevPlan.md, implement Group 3: D3
```

После завершения:
```
make fix-gate && git add -u && make gate MODE=fast && make check-manifests
```

## $END_DEVPLAN
