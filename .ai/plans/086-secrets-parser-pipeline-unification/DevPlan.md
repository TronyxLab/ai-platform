$START_DEVPLAN

$ARTIFACT_CONTRACT
PURPOSE:               Унификация всех механизмов работы с secrets.env — 7 независимых парсеров → 1 shared модуль. Завершение извлечения shell-логики секретов в Python (decrypt-secrets.sh, secrets-init.sh). Консолидация дублирующихся cross-cutting concern'ов (Telegram-нотификации, Docker auth, AGE-key формат). Включение скрытых парсеров (cert_orchestrator.py bash subprocess, node-lifecycle.sh source import).
DESCRIPTION:           DevPlan закрывает архитектурные gaps, оставшиеся после DP-078. DP-078 исправил 7 точечных дрейфов (age_key, crypto, token leak, naming), но не создал единого источника истины для парсинга secrets.env. Результат: 7 файлов парсят один и тот же формат с разной обработкой edge cases (кавычки, комментарии, export-префикс) — включая 2 скрытых парсера: cert_orchestrator.py._source_secrets_env() (L719-764, bash subprocess) и node-lifecycle.sh `set -a; source "$secrets_env"` (L84). Этот DevPlan создаёт shared модуль-парсер и мигрирует на него всех потребителей. Дополнительно: извлечение оставшейся shell-логики (decrypt-secrets.sh 223 LOC, secrets-init.sh 110 LOC, generate-catalog.sh inline python3), консолидация 6 Telegram-нотификаторов (3 shell + 3 Python), унификация Docker auth (5 точек), стандартизация AGE-key формата.
RATIONALE:             После DP-078 пользователь наблюдает возврат дрейфа в домене секретов. Причина: DP-078 чинил симптомы (7 drift points), но не архитектурную причину — множественные независимые входы в систему секретов. Исправление бага в одном парсере не фиксит его в четырёх других. Этот DevPlan — архитектурное решение: один модуль, один контракт, все потребители через него. Языковая политика (§Языковая политика AGENTS.md): decrypt-secrets.sh и secrets-init.sh содержат бизнес-логику в shell — должны быть извлечены в Python (Tier 2 Strangler).
ACCEPTANCE_CRITERIA:
  - AC1: secrets_env_parser.py — единый модуль с parse()/write()/merge() — все 7 потребителей мигрированы
  - AC2: decrypt-secrets.sh бизнес-логика извлечена в Python (decrypt_secrets.py), shell — фасад <30 LOC
  - AC3: secrets-init.sh бизнес-логика извлечена в Python (secrets_manager.py или новый модуль), shell удалён
  - AC4: generate-catalog.sh inline python3 heredoc извлечён в generate_catalog.py
  - AC5: 6 Telegram-нотификаторов (3 shell + 3 Python) → 1 telegram_notifier.py; все старые удалены
  - AC6: Docker auth унифицирован: 5 точек (lib/docker.sh, docker_registry_auth.py, state_machine.py._ghcr_auth, steps.py._ghcr_docker_login, core/entrypoints/deploy-context.sh) → единый docker_auth.py
  - AC7: AGE-key формат стандартизирован: один канонический формат, документированный в age_key.py
  - AC8: 0 grep-совпадений по старым паттернам парсинга secrets.env в не-shared файлах
  - AC9: make gate MODE=fast — зелёный
  - AC10: python -m pytest tests/ -v — все тесты проходят (включая новые unit-тесты для извлечённых модулей)
IMPLEMENTS:            Superposition Analysis 2026-07-28 — Проблема 1 (Секреты: 3 входа) + Агент 2 Duplicate Logic Report (Duplicates 1-4) + Языковая политика AGENTS.md Tier 2 extraction
IMPACTS:               36+ файлов (12 CREATE, 23 MODIFY, 1 DELETE + 2 функции); 26 задач в 4 волнах. Подробно в File Manifest.
REQUIRES:              DP-078 (завершён — shared/age_key.py, shared/crypto.py существуют). DP-070 (завершён — shared/__init__.py существует). Никаких других блокирующих зависимостей.
$END_ARTIFACT_CONTRACT

---

# DevPlan 086: Secrets Parser & Pipeline Unification

**Severity:** HIGH — архитектурный дрейф (7 парсеров), безопасность (shell handling секретов)
**Created:** 2026-07-28
**Author:** Kilo (architect agent)
**Source:** Superposition Analysis, Duplicate Logic Report (Agent 2), Parallel Branches Report (Agent 3)
**Sequenced:** AFTER DP-078 (done), BEFORE DP-087 (Bootstrap)

---

## §1. Контекст: что DP-078 исправил и что осталось

### Исправлено DP-078 (7 drift points S1-S7)
- Age-key detection: 5 shell-копий → 1 Python age_key.py
- crypto.py: htpasswd генерация → shared модуль
- Docker registry token leak: bash -c "echo {token}" → subprocess.run(input=token)
- OPENAI_API_KEY убран из платформенного enforcement
- GHCR_PUSH_TOKEN формализован в secret-definitions.yaml
- POSTGRES_PASSWORD/NEXTAUTH_SECRET значения унифицированы

### НЕ исправлено — архитектурный gap
| Проблема | Файлы | Корневая причина |
|----------|-------|-----------------|
| 7 парсеров secrets.env | secrets_manager.py, secrets_validator.py, compose_preflight.py, agent_watchdog.py, lib/secrets.sh, **cert_orchestrator.py, node-lifecycle.sh** | Каждый писал свой парсер независимо. 2 скрытых не были учтены в первоначальном анализе |
| decrypt-secrets.sh (223 LOC shell) | core/internal/secrets/decrypt-secrets.sh | Python state_machine.py оборачивает shell через 4-уровневую цепочку |
| secrets-init.sh (110 LOC shell) | core/internal/bootstrap/secrets-init.sh | Python state_machine.py вызывает shell subprocess |
| generate-catalog.sh inline python3 | core/internal/catalog/generate-catalog.sh | 60 строк python3 heredoc в shell-скрипте |
| 6 Telegram-нотификаторов (3 shell + 3 Python) | notify-hook.sh, agent_watchdog.py, disk-monitor.sh, tor-proxy-healthcheck.sh, **state_machine.py._send_telegram(), steps.py._send_telegram_notification()** | Каждый шлёт curl сам. 4 Python-нотификатора добавлены в state machine |
| Docker auth shell + Python (5 точек) | lib/docker.sh, docker_registry_auth.py, **state_machine.py._ghcr_auth(), steps.py._ghcr_docker_login(), core/entrypoints/deploy-context.sh** | Дублирование docker login --password-stdin через shell и Python |
| AGE-key формат diverge | age_key.py vs decrypt-secrets.sh vs platform-secrets/install.sh | AGE_SECRET_KEY=sk-... vs bare sk-... |
| **cert_orchestrator.py hidden bash parser (6-й парсер)** | core/internal/bootstrap/cert_orchestrator.py | `_source_secrets_env()` (L719-764) использует subprocess bash с распарсингом через source + env, а не прямой Python-парсинг |
| **node-lifecycle.sh implicit import (7-й парсер)** | core/entrypoints/node-lifecycle.sh | `set -a; source "$secrets_env"` (L84) загружает secrets.env в shell env без явного парсинга — невидимый потребитель |

---

## §2. Draft Code Graph (структура решения)

```
core/internal/shared/secrets_env_parser.py     [CREATE] — единый парсер
    ├── parse(path) → dict[str, str]          # raise FileNotFoundError если файл отсутствует; caller обязан проверить существование
    ├── write(path, data, mode=0o600) → None   # атомарная запись через tempfile + rename
    ├── merge(*paths) → dict[str, str]         # last-wins: последний path перезаписывает дубликаты
    ├── export_shell(path) → str               # генерирует shell export statements для source-подстановки (node-lifecycle.sh)
    └── _parse_line(line) → tuple[key, value]  # (приватный) обработка export, кавычек, inline comments, unicode

core/internal/shared/telegram_notifier.py      [CREATE] — единый Telegram-клиент
    └── send_telegram(message, bot_token, chat_id, proxy_url=None) → bool   # urllib-based, опциональный SOCKS/HTTP proxy

core/internal/shared/docker_auth.py            [CREATE] — единый Docker auth
    ├── docker_login(registry, username, token) → bool     # docker login --password-stdin
    ├── ghcr_login(token, user="ci-deploy") → bool         # специализированный GHCR login (registry=ghcr.io)
    └── configure_docker_auth(username, token, mirror_url=None) → dict   # генерирует ~/.docker/config.json entry для mirror registry

core/internal/secrets/decrypt_secrets.py       [CREATE] — Python-ядро decrypt
    ├── detect_age_key() → str                (делегирует age_key.py)
    ├── decrypt_sops_file(age_key, path) → str
    └── write_secrets_env(decrypted_data, output_path) → None
    └── main() — CLI entrypoint

core/internal/catalog/generate_catalog.py     [CREATE] — извлечён из generate-catalog.sh

core/internal/bootstrap/lifecycle/secrets_manager.py  [MODIFY]
    ├── import secrets_env_parser (вместо source_secrets_env)
    ├── import telegram_notifier (удалить _send_telegram inline)
    └── secrets-init логика → _init_service_passwords() (из secrets-init.sh)

core/internal/bootstrap/deploy/secrets_validator.py   [MODIFY]
    └── _check_env_requires() → secrets_env_parser.parse()

core/internal/bootstrap/deploy/compose_preflight.py   [MODIFY]
    └── load_secrets_env() → secrets_env_parser.parse()

core/modules/hermes-agent/watchdog/agent_watchdog.py  [MODIFY]
    └── _load_token() → secrets_env_parser.parse()

core/lib/secrets.sh                                   [MODIFY]
    └── step_10_decrypt_secrets() → python3 decrypt_secrets.py

core/internal/secrets/decrypt-secrets.sh              [MODIFY → фасад <30 LOC]
core/internal/bootstrap/secrets-init.sh               [DELETE]
core/internal/catalog/generate-catalog.sh             [MODIFY → фасад <10 LOC]
core/internal/notify/notify-hook.sh                   [MODIFY → python3 telegram_notifier.py]
core/lib/docker.sh                                    [MODIFY → python3 docker_auth.py]
```

---

## §3. Wave Structure

### Wave 1: Foundation — shared модули (независимые CREATE'ы)

| Task | Описание | Файлы | Effort |
|------|----------|-------|--------|
| **T1** | Создать secrets_env_parser.py: parse()/write()/merge() + unit-тесты (12 test cases: parse with export, parse with quotes, parse with comments, write atomic, merge override, empty file, inline comments, mixed quotes, spaces around `=`, empty KEY=, unicode, prefix_filter) | 2 CREATE | 3 |
| **T2** | Создать telegram_notifier.py: send_telegram() + unit-тест (mocked urllib) | 2 CREATE | 2 |
| **T3** | Создать docker_auth.py: docker_login() + unit-тест | 2 CREATE | 2 |
| **T4** | Стандартизировать AGE-key формат: документировать канонический формат в age_key.py docstring; проверить все 3 точки использования на консистентность | 1 MODIFY | 1 |

**Wave 1 acceptance:** 4 новых Python-модуля, 15 unit-тестов (12 для парсера + 1 telegram + 1 docker + 1 AGE-key). Независимы, могут выполняться параллельно.

### Wave 2: Consumer Migration — миграция 7 парсеров

| Task | Описание | Файлы | Effort |
|------|----------|-------|--------|
| **T5** | secrets_manager.py: source_secrets_env() → secrets_env_parser.parse(); перенос _init_service_passwords() из secrets-init.sh в secrets_manager.py | 1 MODIFY | 3 |
| **T6** | secrets_validator.py: _check_env_requires() inline parser → secrets_env_parser.parse() | 1 MODIFY | 1 |
| **T7** | compose_preflight.py: load_secrets_env() → secrets_env_parser.parse() | 1 MODIFY | 1 |
| **T8** | agent_watchdog.py: _load_token() → secrets_env_parser.parse() + telegram_notifier.send_telegram(); удалить _send_telegram() метод | 1 MODIFY | 2 |

| **T17** | cert_orchestrator.py: `_source_secrets_env()` (L719-764) — заменить bash subprocess распарсинг на `secrets_env_parser.parse()`; удалить код bash-парсинга | 1 MODIFY | 2 |
| **T18** | node-lifecycle.sh: `source "$secrets_env"` (L84) — заменить на `source <(python3 -c "from shared.secrets_env_parser import export_shell; print(export_shell('...'))")`; парсинг через shared модуль, shell env как транспорт | 1 MODIFY | 1 |

**Wave 2 acceptance:** 0 grep по старым паттернам парсинга (grep -rn "for line in.*open.*secrets\|source_secrets_env\|set -a;.*source.*secrets" core/ → empty). 6/6 потребителей мигрированы (T5–T8, T17–T18). T9 (lib/secrets.sh) отложен в Wave 3.

### Wave 3: Shell → Python Extraction

| Task | Описание | Файлы | Effort |
|------|----------|-------|--------|
| **T10** | decrypt_secrets.py: извлечь бизнес-логику из decrypt-secrets.sh (223 LOC) в Python. Ключевые инварианты: temp key в /tmp с 0600, wipe через dd, trap на EXIT/INT/TERM. Shell → фасад <30 LOC. Unit-тесты: decrypt success, decrypt fail (wrong key), temp key cleanup, no secret in logs | 2 CREATE, 1 MODIFY | 4 |
| **T9** | lib/secrets.sh: step_10_decrypt_secrets() — заменить inline bash-парсинг на python3 decrypt_secrets.py (создан в T10, поэтому T9 перенесён в Wave 3) | 1 MODIFY | 2 |
| **T11** | secrets-init.sh → удалить. Логика перенесена в secrets_manager.py._init_service_passwords() на T5. Shell-фасад удалён. **Важно:** удалить `state_machine.py._step_secrets_init()` И `steps.py._step_secrets_init()` — оба вызывали secrets-init.sh через subprocess | 1 DELETE, 3 MODIFY | 1 |
| **T12** | generate_catalog.py: извлечь python3 heredoc (60 LOC) из generate-catalog.sh. Shell → фасад <10 LOC | 2 CREATE, 1 MODIFY | 2 |

**Wave 3 acceptance:** 0 inline python3 heredoc в generate-catalog.sh. decrypt-secrets.sh <30 LOC (только фасад). lib/secrets.sh вызывает decrypt_secrets.py (step_10 распарсинг унифицирован). secrets-init.sh удалён; `state_machine.py._step_secrets_init()` и `steps.py._step_secrets_init()` удалены.

### Wave 4: Cross-cutting cleanup + State Machine migration + Gate

| Task | Описание | Файлы | Effort |
|------|----------|-------|--------|
| **T13** | Docker auth консолидация: lib/docker.sh docker_login() → python3 docker_auth.py; docker_registry_auth.py импортирует docker_auth вместо своего inline docker login | 2 MODIFY | 2 |
| **T14** | Telegram консолидация: notify-hook.sh → python3 telegram_notifier.py; disk-monitor.sh → python3 telegram_notifier.py; tor-proxy-healthcheck.sh → python3 telegram_notifier.py | 3 MODIFY | 2 |
| **T15** | Gate test: test_secrets_env_parser — валидирует, что ВСЕ secrets.env consumers используют shared модуль. Gate test: test_no_inline_secrets_parsing — fail если найден прямой open() secrets.env вне shared/ | 2 CREATE | 2 |
| **T16** | make fix-gate + make gate MODE=fast → зелёный. Верификация: grep по старым паттернам | — | 1 |
| **T19** | state_machine.py: `_send_telegram()` + steps.py: `_send_telegram_notification()` → python3 telegram_notifier.send_telegram(); удалить inline urllib | 2 MODIFY | 3 |
| **T20** | state_machine.py: `_ghcr_auth()` + steps.py: `_ghcr_docker_login()` → docker_auth.ghcr_login() + docker_auth.configure_docker_auth(); удалить inline subprocess | 2 MODIFY | 2 |
| **T21** | state_machine.py: мигрировать 5 функций на shared модули (M2: import secrets_env_parser, telegram_notifier, docker_auth вместо inline; удалить _step_secrets_init) | 1 MODIFY | 3 |
| **T22** | steps.py: мигрировать 3 функции на shared модули (M3: import docker_auth, telegram_notifier вместо inline) | 1 MODIFY | 2 |
| **T23** | Интеграционный тест: fixture secrets.env → secrets_env_parser.parse() → assert ВСЕ 7 consumers загружают одинаковые данные. Проверка целостности pipeline | 1 CREATE | 2 |
| **T24** | Performance benchmark: test_secrets_env_parser_benchmark — secrets_env_parser.parse() с >1000 vars <50ms. CI gate fail если >100ms | 1 CREATE | 1 |
| **T25** | Doc update: core/AGENTS.md (добавить 3 новых shared модуля), entrypoint-manifest.yaml (новые entrypoints), secret-definitions.yaml (AGE-key стандарт) | 3 MODIFY | 2 |
| **T26** | checkpoint_migration.py: обновить mapping для мигрированных функций (state_machine telegram→telegram_notifier, ghcr→docker_auth, secrets_init→secrets_manager) | 1 MODIFY | 1 |

---

## §4. File Manifest

### CREATE (12)
| Файл | Назначение |
|------|-----------|
| `core/internal/shared/secrets_env_parser.py` | Единый парсер secrets.env |
| `core/internal/shared/telegram_notifier.py` | Единый Telegram-клиент |
| `core/internal/shared/docker_auth.py` | Единый Docker registry auth |
| `core/internal/secrets/decrypt_secrets.py` | Python-ядро дешифровки SOPS/age |
| `core/internal/catalog/generate_catalog.py` | Python-ядро генерации каталога |
| `tests/unit/test_secrets_env_parser.py` | Unit-тесты парсера |
| `tests/unit/test_telegram_notifier.py` | Unit-тесты Telegram-клиента (mocked urllib) |
| `tests/unit/test_docker_auth.py` | Unit-тесты Docker registry auth |
| `tests/unit/test_decrypt_secrets.py` | Unit-тесты дешифровки |
| `tests/gates/test_gate_no_inline_secrets_parsing.py` | Gate: запрет прямого парсинга secrets.env |
| `tests/integration/test_secrets_pipeline_integration.py` | Интеграционный тест: все 7 consumers через shared модуль |
| `tests/unit/test_secrets_env_parser_benchmark.py` | Performance benchmark: >1000 vars <50ms |

### MODIFY (23)
| Файл | Изменение |
|------|----------|
| `core/internal/bootstrap/lifecycle/secrets_manager.py` | source_secrets_env → parser; +_init_service_passwords() |
| `core/internal/bootstrap/deploy/secrets_validator.py` | _check_env_requires → parser |
| `core/internal/bootstrap/deploy/compose_preflight.py` | load_secrets_env → parser |
| `core/modules/hermes-agent/watchdog/agent_watchdog.py` | _load_token → parser; _send_telegram → telegram_notifier |
| `core/lib/secrets.sh` | step_10 → python3 decrypt_secrets.py |
| `core/lib/docker.sh` | docker_login → python3 docker_auth.py |
| `core/internal/bootstrap/docker_registry_auth.py` | Импорт docker_auth вместо inline docker login |
| `core/internal/secrets/decrypt-secrets.sh` | → фасад <30 LOC |
| `core/internal/notify/notify-hook.sh` | → python3 telegram_notifier.py |
| `core/internal/catalog/generate-catalog.sh` | → фасад <10 LOC |
| `core/internal/bootstrap/lifecycle/state_machine.py` | M2: 5 функций → secrets_env_parser + telegram_notifier + docker_auth; удалить _step_secrets_init; импорт вместо inline |
| `core/internal/bootstrap/lifecycle/steps.py` | M3: 3 функции → docker_auth + telegram_notifier импорт вместо inline |
| `core/internal/bootstrap/cert_orchestrator.py` | _source_secrets_env() → secrets_env_parser.parse(); удалить bash subprocess (L719-764) |
| `core/entrypoints/node-lifecycle.sh` | source "$secrets_env" → export_shell() через python3; парсинг унифицирован |
| `core/AGENTS.md` | Добавить 3 новых shared модуля в каталог операций |
| `core/internal/checkpoint_migration.py` | Обновить mapping: telegram→telegram_notifier, ghcr→docker_auth, secrets_init→secrets_manager |
| `core/internal/scripts/sync_env_defaults.py` | Импорт secrets_env_parser (если есть inline парсинг) |
| `core/internal/scripts/generate_secrets_manifest.py` | Импорт secrets_env_parser (если есть inline парсинг) |
| `core/internal/shared/age_key.py` | Документировать канонический AGE-key формат в docstring |
| `core/modules/backup-cron/scripts/disk-monitor.sh` | → python3 telegram_notifier.py |
| `core/internal/healthcheck/tor-proxy-healthcheck.sh` | → python3 telegram_notifier.py |
| `core/entrypoint-manifest.yaml` | Добавить новые entrypoints (secrets_env_parser, telegram_notifier, docker_auth) |
| `core/secret-definitions.yaml` | Обновить AGE-key стандарт |

### DELETE (1 файл + 2 функции)
| Файл / Функция | Причина |
|------|---------|
| `core/internal/bootstrap/secrets-init.sh` | Логика перенесена в secrets_manager.py |
| `state_machine.py._step_secrets_init()` | Вызов secrets-init.sh — удалён shell-фасад |
| `steps.py._step_secrets_init()` | Вызов secrets-init.sh — удалён shell-фасад |

---

## §5. Acceptance Criteria (Detailed)

- [ ] AC1: `core/internal/shared/secrets_env_parser.py` — parse()/write()/merge(). 12 unit-тестов PASS (добавлены: inline comments, mixed quotes, spaces around `=`, empty KEY=, unicode, prefix_filter).
- [ ] AC2: `core/internal/secrets/decrypt_secrets.py` — CLI entrypoint, 4 unit-теста PASS. decrypt-secrets.sh <30 LOC.
- [ ] AC3: `core/internal/bootstrap/secrets-init.sh` — удалён. `_init_service_passwords()` в secrets_manager.py. `state_machine.py._step_secrets_init()` И `steps.py._step_secrets_init()` удалены.
- [ ] AC4: `core/internal/catalog/generate_catalog.py` — CLI entrypoint. generate-catalog.sh <10 LOC.
- [ ] AC5: `core/internal/shared/telegram_notifier.py` — send_telegram(). 0 grep "curl.*TELEGRAM_BOT_TOKEN|urllib\.request.*TELEGRAM|requests\.post.*TELEGRAM" в не-shared файлах. Учтены все 6 точек (3 shell + 3 Python).
- [ ] AC6: `core/internal/shared/docker_auth.py` — docker_login() + ghcr_login() + configure_docker_auth(). Все 5 точек делегируют shared модулю.
- [ ] AC7: AGE-key формат документирован в age_key.py docstring. Все 3 точки использования консистентны.
- [ ] AC8: `grep -rn "for line in.*open.*secrets\|source_secrets_env\|set -a;.*source.*secrets\|\. .*secrets\.env" core/internal/ core/entrypoints/ | grep -v secrets_env_parser.py` → empty. Покрытие всех 7 способов парсинга.
- [ ] AC9: `grep -rn "source.*secrets\.env\|\. \/run\/platform\/secrets" --include="*.sh" core/ entrypoints/ core/entrypoints/` → только decrypt-secrets.sh фасад.
- [ ] AC10: `make gate MODE=fast` — зелёный, `test_gate_no_inline_secrets_parsing.py` PASS.
- [ ] AC11: `python -m pytest tests/ -v` — 100% PASS (текущие + новые).
- [ ] AC12: Performance benchmark: `secrets_env_parser.parse()` с >1000 vars <50ms (CI gate <100ms).
- [ ] AC13: Integration test: все 7 consumers загружают один и тот же secrets.env через shared модуль.

---

## §6. Design Decisions

### DD1: Почему не в DP-078?
DP-078 фокусировался на 7 конкретных drift points (S1-S7) из Brief 077. Архитектурная проблема «7 парсеров» (включая 2 скрытых) была обнаружена ПОСЛЕ завершения DP-078, в ходе системного анализа 2026-07-28 и последующего аудита cert_orchestrator.py + node-lifecycle.sh. DP-078 correctly исправил симптомы; DP-086 исправляет корневую причину.

### DD2: Почему единый secrets_env_parser, а не децентрализованные импорты?
Семь файлов реализуют парсинг secrets.env с разной обработкой edge cases:
- secrets_manager.py: обрабатывает export, кавычки, комментарии (полный)
- secrets_validator.py: только `=` и `#` (минимальный)
- compose_preflight.py: частичная обработка кавычек
- agent_watchdog.py: только TELEGRAM_BOT_TOKEN и TELEGRAM_CHAT_ID (специализированный)
- lib/secrets.sh: bash regex с YAML-форматом (key: value)
- **cert_orchestrator.py**: bash subprocess → source + env (скрытый, L719-764)
- **node-lifecycle.sh**: `set -a; source "$secrets_env"` (скрытый, L84 — невидимый потребитель)

Любой баг в одном парсере не фиксится в других. Единый модуль = один источник истины.

**Примечание:** `node-lifecycle.sh` не может быть полностью мигрирован на Python — его `set -a; source` конвертирует secrets.env в shell environment variables для всего lifecycle shell-скрипта (другие step-функции читают переменные из env, а не парсят файл). Стратегия: `node-lifecycle.sh` загружает vars через `python3 -c "from shared.secrets_env_parser import export_shell; export_shell('...')"` — генерирует shell export statements, которые затем source'ятся. Shell env как транспорт остаётся, но парсинг файла унифицирован.

### DD3: Почему decrypt_secrets.py — полное извлечение, а не фасад?
decrypt-secrets.sh (223 LOC) содержит критичную бизнес-логику: temp key lifecycle, SOPS subprocess, wipe через dd. State machine вызывает его через 4-уровневую цепочку: Python → bash -c "source logging.sh && source checkpoint.sh && source secrets.sh && step_10_decrypt_secrets". Это хрупко (зависит от shell function names, env state). Python-извлечение делает цепочку: Python → subprocess(sops) — без shell-посредника.

### DD4: Почему telegram_notifier — новое, а не расширение существующего?
6 независимых реализаций (3 shell: notify-hook.sh, disk-monitor.sh, tor-proxy-healthcheck.sh; 3 Python: agent_watchdog.py urllib, state_machine.py._send_telegram(), steps.py._send_telegram_notification()) не имеют общего интерфейса и различаются по механизму (curl, urllib, requests). Проще создать один typed модуль и мигрировать всех, чем патчить существующие с разными сигнатурами.

### DD5: Безопасность — нерегрессионные инварианты для decrypt_secrets.py
- Temp key пишется в /tmp с 0o600 → идентично поведению shell
- Wipe через dd if=/dev/zero (не rm -f) → предотвращает recovery из файловой системы
- Единый trap на SIGINT/SIGTERM/EXIT → атомарная очистка
- SOPS_AGE_KEY_FILE env var → совместимость с sops CLI
- Никакие secret values не попадают в логи → валидируется unit-тестом

### DD6: Почему lib/secrets.sh и node-lifecycle.sh не полностью мигрируются?
Оба файла остаются shell-фасадами по разным причинам:

**lib/secrets.sh** — библиотека shell-функций для bootstrap pipeline, source'ится в других shell-скриптах. Её `step_10_decrypt_secrets()` вызывает `decrypt_secrets.py` через Python, но сама остаётся shell-функцией (другие step-функции в lib/secrets.sh зависят от shell env). Полная миграция потребовала бы переписывания всего bootstrap pipeline на Python — выходит за рамки текущей волны (см. Bootstrap DP-087).

**node-lifecycle.sh** — entrypoint, который последовательно вызывает step-функции, часть из которых читает переменные из shell env (установленные через `set -a; source "$secrets_env"`). Замена `source secrets.env` на Python-парсинг + генерацию export statements унифицирует парсинг, но shell env как транспорт между step-функциями остаётся. Полная миграция потребовала бы переписывания всего lifecycle на Python.

**Компромисс:** Парсинг файла унифицирован (через `secrets_env_parser.export_shell()`), shell env как рантайм-транспорт сохранён. Это минимальный инвазивный шаг, closing the gap без переписывания двух крупных shell-модулей.

---

## §Rollback Plan

Пошаговый план отката для каждого компонента. Применяется если `make gate MODE=fast` падает после деплоя волны, или если production сломан.

### Pre-rollback: freeze
```bash
git tag rollback-point-086-$(date +%Y%m%d-%H%M%S)
git stash  # если есть незакоммиченные изменения от rollback wave
```

### Level 1: Быстрый откат одного модуля (если бага локализована)

| Компонент | Действие отката | Время |
|-----------|----------------|-------|
| secrets_env_parser.py | `git checkout HEAD~1 -- core/internal/shared/secrets_env_parser.py`; перезапустить consumers | 2 min |
| telegram_notifier.py | `git checkout HEAD~1 -- core/internal/shared/telegram_notifier.py`; вернуть inline curl в каждом consumer | 5 min |
| docker_auth.py | `git checkout HEAD~1 -- core/internal/shared/docker_auth.py`; вернуть inline docker login | 3 min |
| decrypt_secrets.py | `git checkout HEAD~1 -- core/internal/secrets/decrypt_secrets.py core/internal/secrets/decrypt-secrets.sh` | 2 min |
| generate_catalog.py | `git checkout HEAD~1 -- core/internal/catalog/generate_catalog.py core/internal/catalog/generate-catalog.sh` | 1 min |

### Level 2: Полный откат волны (если волна сломана)

**Wave 1 (shared modules):**
```bash
git revert <wave1-merge-commit> --no-edit
# Восстанавливает: 5 старых парсеров в consumers, inline shell telegram, inline docker auth
```

**Wave 2 (consumer migration):**
```bash
git revert <wave2-merge-commit> --no-edit
# Восстанавливает: source_secrets_env(), inline _check_env_requires(), load_secrets_env(), _load_token()
```

**Wave 3 (shell extraction):**
```bash
git revert <wave3-merge-commit> --no-edit
# Восстанавливает: decrypt-secrets.sh 223 LOC, secrets-init.sh, generate-catalog.sh inline python3
```

**Wave 4 (cleanup):**
```bash
git revert <wave4-merge-commit> --no-edit
# Восстанавливает: inline docker login, inline telegram, старые state_machine.py/steps.py
```

### Level 3: Полный откат всего DP-086
```bash
git revert <086-base-commit>..HEAD --no-edit
make fix-gate && make gate MODE=fast
```

### Post-rollback верификация
```bash
# 1. Файлы shared/ удалены
test ! -f core/internal/shared/secrets_env_parser.py

# 2. Старые парсеры восстановлены
grep -q "source_secrets_env" core/internal/bootstrap/lifecycle/secrets_manager.py

# 3. Shell-скрипты восстановлены
wc -l core/internal/secrets/decrypt-secrets.sh  # Expected: ~223
test -f core/internal/bootstrap/secrets-init.sh

# 4. Gate зелёный
make gate MODE=fast
```

---

## §Migration Gate

Критерии готовности к переходу между волнами и финальный gate для production.

### Gate G1: Wave 1 → Wave 2 (shared modules ready)

| # | Критерий | Проверка |
|---|----------|----------|
| G1.1 | secrets_env_parser.parse() — 12 тестов PASS | `pytest tests/unit/test_secrets_env_parser.py -v` |
| G1.2 | secrets_env_parser.merge() last-wins корректно | Тест с дублирующимися ключами |
| G1.3 | telegram_notifier.send_telegram() — mocked test PASS | `pytest tests/unit/test_telegram_notifier.py -v` |
| G1.4 | docker_auth.docker_login()/ghcr_login()/configure_docker_auth() — PASS | `pytest tests/unit/test_docker_auth.py -v` |
| G1.5 | AGE-key формат консистентен во всех 3 точках | `make check-age-key-format` (есть или создать) |
| G1.6 | No regression in existing tests | `pytest tests/ -v --ignore=tests/gates/` |

### Gate G2: Wave 2 → Wave 3 (7 consumers migrated)

| # | Критерий | Проверка |
|---|----------|----------|
| G2.1 | 0 grep на старые паттерны | `grep -rn "for line in.*open.*secrets\|source_secrets_env\|set -a;.*source.*secrets" core/ | grep -v secrets_env_parser.py` → empty |
| G2.2 | cert_orchestrator.py больше не парсит secrets.env через bash | `grep -q "subprocess.*source.*secrets\|_source_secrets_env" core/internal/bootstrap/cert_orchestrator.py` → fail |
| G2.3 | node-lifecycle.sh не source'ит secrets.env напрямую | `grep "source \$secrets_env" core/entrypoints/node-lifecycle.sh` → empty |
| G2.4 | Все 7 consumers используют импорт из shared | `grep -r "from shared.secrets_env_parser import" core/` — 7 совпадений (по одному на consumer) |
| G2.5 | Performance: parse() с реальным secrets.env (<50ms) | Встроено в benchmark test |

### Gate G3: Wave 3 → Wave 4 (shell extraction clean)

| # | Критерий | Проверка |
|---|----------|----------|
| G3.1 | decrypt-secrets.sh <30 LOC | `wc -l core/internal/secrets/decrypt-secrets.sh` |
| G3.2 | secrets-init.sh удалён | `test ! -f core/internal/bootstrap/secrets-init.sh` |
| G3.3 | generate-catalog.sh <10 LOC | `wc -l core/internal/catalog/generate-catalog.sh` |
| G3.4 | 0 inline python3 heredoc | `grep -rn "python3.*<<\|python3.*-c\|PYEOF" core/internal/catalog/generate-catalog.sh core/internal/secrets/decrypt-secrets.sh` → empty |
| G3.5 | 4 decrypt unit теста PASS | `pytest tests/unit/test_decrypt_secrets.py -v` |

### Gate G4: Final Gate (all waves complete)

| # | Критерий | Проверка |
|---|----------|----------|
| G4.1 | AC1-AC13 все `[x]` | §5 Acceptance Criteria |
| G4.2 | Integration test PASS (7 consumers) | `pytest tests/integration/test_secrets_pipeline_integration.py -v` |
| G4.3 | Performance benchmark PASS | `pytest tests/unit/test_secrets_env_parser_benchmark.py -v` |
| G4.4 | checkpoint_migration.py mapping актуален | `grep -q "telegram_notifier\|docker_auth\|secrets_manager" core/internal/checkpoint_migration.py` |
| G4.5 | core/AGENTS.md обновлён | `grep -q "secrets_env_parser\|telegram_notifier\|docker_auth" core/AGENTS.md` |
| G4.6 | `make gate MODE=fast` зелёный | `make fix-gate && make gate MODE=fast` |
| G4.7 | `make gate MODE=full` зелёный | `make gate MODE=full` |
| G4.8 | All tests 100% PASS | `python -m pytest tests/ -v` |

---

## §7. Implementation Commands

```
# === WAVE 1: Foundation (parallel) ===
coder implement DevPlan 086 Wave 1:
  T1 (secrets_env_parser.py + 12 unit tests: export, quotes, comments, write atomic,
     merge override, empty file, inline comments, mixed quotes, spaces around =,
     empty KEY=, unicode, prefix_filter),
  T2 (telegram_notifier.py + unit test — mocked urllib, proxy support),
  T3 (docker_auth.py + unit test — docker_login, ghcr_login, configure_docker_auth),
  T4 (AGE-key format standardization in age_key.py)

# Verify Wave 1 — Gate G1
python3 -m pytest tests/unit/test_secrets_env_parser.py tests/unit/test_telegram_notifier.py tests/unit/test_docker_auth.py -v

# === WAVE 2: Consumer Migration (6 парсеров) ===
coder implement DevPlan 086 Wave 2:
  T5 (secrets_manager.py → parser), T6 (secrets_validator.py → parser),
  T7 (compose_preflight.py → parser), T8 (agent_watchdog.py → parser + telegram),
  T17 (cert_orchestrator.py → parser, удалить bash subprocess L719-764),
  T18 (node-lifecycle.sh → export_shell() через python3, L84)

# Verify Wave 2 — Gate G2
grep -rn "for line in.*open.*secrets\|source_secrets_env\|set -a;.*source.*secrets" core/ | grep -v secrets_env_parser.py
# Expected: empty
# Проверить cert_orchestrator.py — нет _source_secrets_env()
grep -q "_source_secrets_env" core/internal/bootstrap/cert_orchestrator.py && echo "FAIL" || echo "PASS"
# Проверить node-lifecycle.sh — нет source secrets.env
grep "source \$secrets_env" core/entrypoints/node-lifecycle.sh && echo "FAIL" || echo "PASS"

# === WAVE 3: Shell → Python ===
coder implement DevPlan 086 Wave 3:
  T10 (decrypt_secrets.py + фасад <30 LOC),
  T9  (lib/secrets.sh → python3 decrypt_secrets.py, созданный в T10),
  T11 (удалить secrets-init.sh, state_machine.py._step_secrets_init(), steps.py._step_secrets_init()),
  T12 (generate_catalog.py + фасад <10 LOC)

# Verify Wave 3 — Gate G3
python3 -m pytest tests/unit/test_decrypt_secrets.py -v
wc -l core/internal/secrets/decrypt-secrets.sh
# Expected: <30
test ! -f core/internal/bootstrap/secrets-init.sh && echo "PASS: deleted" || echo "FAIL"
grep -rn "python3.*<<\|python3.*-c\|PYEOF" core/internal/catalog/generate-catalog.sh core/internal/secrets/decrypt-secrets.sh
# Expected: empty

# === WAVE 4: Cleanup + Gate ===
coder implement DevPlan 086 Wave 4:
  T13 (docker auth unity — lib/docker.sh + docker_registry_auth.py → docker_auth),
  T14 (telegram unity — notify-hook.sh, disk-monitor.sh, tor-proxy-healthcheck.sh → telegram_notifier),
  T15 (gate tests — test_gate_no_inline_secrets_parsing + test_secrets_env_parser),
  T16 (fix-gate + gate MODE=fast),
  T19 (state_machine.py._send_telegram() + steps.py._send_telegram_notification() → telegram_notifier),
  T20 (state_machine.py._ghcr_auth() + steps.py._ghcr_docker_login() → docker_auth),
  T21 (state_machine.py: 5 функций → shared модули; удалить _step_secrets_init),
  T22 (steps.py: 3 функции → shared модули),
  T23 (integration test — 7 consumers через shared),
  T24 (performance benchmark — >1000 vars <50ms),
  T25 (doc update — core/AGENTS.md, entrypoint-manifest.yaml, secret-definitions.yaml),
  T26 (checkpoint_migration.py — обновить mapping)

# Verify Wave 4 — Gate G4
make fix-gate && make gate MODE=fast
make gate MODE=full
python3 -m pytest tests/ -v
grep -q "secrets_env_parser\|telegram_notifier\|docker_auth" core/AGENTS.md && echo "AGENTS.md: OK"
grep -q "telegram_notifier\|docker_auth\|secrets_manager" core/internal/checkpoint_migration.py && echo "mapping: OK"
```

$END_DEVPLAN
