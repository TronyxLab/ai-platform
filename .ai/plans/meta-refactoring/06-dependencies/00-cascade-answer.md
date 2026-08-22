# Cascade Answer — «Какие изменения завтра потребуют каскадного изменения половины системы?»

Measured fan-in evidence from DEP-0001…0059. Ranked by blast radius × failure mode.

## Tier 1 — половина системы

**1. Любое семантическое изменение leaf-контрактов `core/internal/shared`**
— timeouts (`DEP-0002`: 89 файлов импортов + гейт пиннит точные import-строки в 15 канонических файлах), exceptions (`DEP-0003`: 99 файлов, задокументированный инцидент двойного класса PlatformFatalError), deploy_paths (`DEP-0001`: hub 216 prod-файлов). Каскад: compile-safe правка ломает поведение десятков CLI одновременно; parity-гейты превращают дрейф в жёсткий RED. **Pre-launch: freeze (additive-only).**

**2. Эволюция `OrchestratorDeployResult` / DeliveryChannel / `deploy()` (`DEP-0004`, `DEP-0048`)**
Каскад: deploy CLI + bootstrap φ8 context_deployer (импортирует класс!) + reconciler_projects + CI forced-command JSON wire contract одновременно. Одно поле = конструктор + to_dict + wire schema + 3 подсистемы. Плюс цикл deploy.engine↔lifecycle (`DEP-0011`) рядом. **Pre-launch: additive-only + не переименовывать; Protocol-разделение post-launch.**

## Tier 2 — треть системы, тихие отказы

**3. Переименование имени секрета в secret-definitions.yaml (AGE_SECRET_KEY и др.) (`DEP-0017`)**
35+ файлов через 4 языка (yaml/make/sh/py) + CI. Parity-гейт покрывает только generated — потребители молча держат старое имя → отказ расшифровки В РАНТАЙМЕ на ноде, не на гейте.

**4. Изменение схемы node.yaml / NodeYaml (`DEP-0053`)**
~33+ потребителей включая модульный слой (postgres hook); `raw()` обходит типизацию; схема без версии. Переименование ключа под multi-node = ручная правка 30+ мест, часть компилируется, но ведёт себя иначе.

**5. Добавление/переименование static detector (`DEP-0016`, `DEP-0019`)**
Тройное хранилище имён: registry tuple ↔ filenames ↔ check-suite `--only`. Худший режим отказа: НЕ падение, а silent false PASS — аудит сообщает зелёный статус, ничего не проверяя. **Единственный CRITICAL с направлением «тихая деградация верификации».**

## Tier 3 — операционные каскады

**6. Регистрация нового модуля платформы (`DEP-0054`)** — 5–8 координированных правок (≥4 gate-файла с hardcoded списками), ~25 glob-гейтов перевооружаются; пропуск одного whitelist'а = модуль без healthcheck-проверки (тихая дыра).

**7. Смена значения env_defaults в platform-infra.yaml (`DEP-0055`, `DEP-0058`)** — ≥5 gate-файлов с литеральными pins (домен запиннен дважды, порты четырежды, COMPOSE_PROFILES четыре раза) + регенерация 3 outputs.

**8. Переименование canonical verb (`DEP-0043`, `DEP-0044`)** — 6–8 слоёв на 4 языках; генератор закрывает 4 автоматически, но пропущенный `--verb`/argparse choice отказывает В РАНТАЙМЕ на VPS (последний хоп, худшая наблюдаемость); переименование check-diff ломает pre-push hook молча.

**9. Перемещение скриптов/каталогов в репозитории (`DEP-0038`, `DEP-0041`)** — ≥5 независимых реderivation PLATFORM_ROOT разной глубины ломаются выборочно; тесты маскируют (pytest rootdir в sys.path). 8 TRAP[BUG] за месяц — повторяющийся класс.

## Что НЕ каскадирует (проверено)
- Нет bidirectional пар между 9 подсистемами; shared — leaf с единственной утечкой s3_client→config
- Нет singleton/service-locator паттернов; DI через Protocol (env_facts — эталон)
- Deploy-критичный путь держит facade-дисциплину (docker_ops/ssh_opts/subprocess_io)
- Thin-facade claim для entrypoints подтверждён (max 126 LOC)

## Pre-launch план (max risk reduction / min churn)

| # | Действие | Finding | Churn | Risk reduction |
|---|----------|---------|-------|----------------|
| 1 | Freeze policy: timeouts/exceptions/node.yaml/OrchestratorDeployResult/verbs/secrets names/env-defaults — additive-only | T1-T2 | 0 | blocks all Tier-1 cascades |
| 2 | `--only` validation: unknown detector name → exit 2 | DEP-0016 | S | убивает silent false PASS |
| 3 | `_loaded=True` после успешного load в platform_config | DEP-0025 | S | empty-defaults latch |
| 4 | Константы check_suite → constants.py (цикл entrypoint) | DEP-0010 | S | make-check landmine |
| 5 | lifecycle.py:29 прямой импорт engine.flow | DEP-0011 | S | deploy import hardening |
| 6 | Snapshot-итерация в signal handler decrypt_secrets | DEP-0026 | S | key residue on /dev/shm |
| 7 | Fail-loud вместо warning-swallow в domains.py importlib | DEP-0018 | S-M | φ7/φ8 silent degradation |
| 8 | Артефакт-preconditions для φ4/φ5 handoffs (secrets.env, certs) | DEP-0039 | S-M | state.json lies |
| 9 | Narrow except в secrets_manager dual-mode | DEP-0037 | S | masked import bugs |
| 10 | Удалить мёртвый practices/check_project.py | DEP-0014 | S | dead-code trap |
| 11 | PRIVOXY_PORT → shared (leaf constant) | DEP-0015 | S | ungated upward edge |
| 12 | Parity-test для twin healthcheck реализаций | DEP-0045 | S | divergent verdicts |

Items 2-12: суммарно ~S-чёрновой churn, каждый — точечный (<5 файлов), ни один не трогает поведение деплоя, кроме добавления fail-loud проверок. Всё остальное — post-launch.
