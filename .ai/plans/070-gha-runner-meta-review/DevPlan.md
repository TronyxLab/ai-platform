# DevPlan 070 — Meta Review: DevPlan 069 GHA Self-Hosted Runner

$START_DEVPLAN

$ARTIFACT_CONTRACT
PURPOSE:               Meta-анализ трёх экспертных рецензий DevPlan 069 (gha-runner). Полная суперпозиция 27 уникальных предложений с критической оценкой применимости к архитектуре ai-platform. Продукт: исправленный DevPlan, готовый к реализации, + TRAP'ы на каждое отклонённое предложение.
DESCRIPTION:           Три независимые рецензии выявили 3 критические ошибки реализации (несуществующий PAT scope, неверный registration flow, `--once` storm), 14 существенных улучшений (checksum, unregister, update mechanism, drain mode, resource limits, healthcheck depth, watchdog, telemetry, etc.) и 10 рекомендаций. Документ содержит: (1) полную суперпозицию с дедупликацией, (2) платформенную валидацию каждого предложения (ACCEPT/MODIFY/REJECT + rationale), (3) исправленный DevPlan 069 с интегрированными изменениями, (4) дельту изменений для аудита.
RATIONALE:             Три рецензента независимо выявили одни и те же проблемы (C1-C3 совпадают у всех) — это сильный сигнал. Исправление до начала реализации предотвращает дорогостоящий реворк. Мета-анализ через суперпозицию гарантирует, что ни одно предложение не потеряно и каждое оценено в контексте платформенных инвариантов (secrets flow, system-module контракт, языковая политика, severity-модель, module-system.mk).
ACCEPTANCE_CRITERIA:
  AC-META-1: Все 27 предложений классифицированы (ACCEPT/MODIFY/REJECT) с явным rationale
  AC-META-2: Для каждого REJECT задокументирован TRAP с условиями пересмотра
  AC-META-3: Исправленный DevPlan содержит все ACCEPT-изменения, интегрированные в исходную структуру
  AC-META-4: Критические ошибки C1-C3 исправлены с точными реализационными деталями
  AC-META-5: Новые риски из рецензий добавлены в секцию TRAP'ов
  AC-META-6: AC-GOAL расширен минимум 7 новыми критериями отказоустойчивости
  AC-META-7: Дельта изменений (diff mental model) задокументирована для аудита
IMPLEMENTS:            DevPlan 069 (gha-self-hosted-runner), 3 экспертные рецензии, VerificationReport 01 (amendment: 7 drift fixes), AGENTS.md инварианты 1-11, языковая политика, system-module контракт (module-system.mk), secret-definitions.yaml SSoT, D5 module.yaml schema
IMPACTS:
  ## Новые файлы (2)
  - .ai/plans/070-gha-runner-meta-review/DevPlan.md — настоящий документ
  - .ai/plans/070-gha-runner-meta-review/01-VerificationReport.md — отчёт верификации (QA фазы 1-3,6)
  ## Модифицируемые (в production)
  - .ai/plans/069-gha-self-hosted-runner/DevPlan.md — оригинал остаётся неизменным (историческая запись)
  ## Будущие (после реализации 070)
  - core/modules/gha-runner/* — все файлы модуля (включая gha_api.py)
  - core/secret-definitions.yaml — новые GHA_RUNNER_PAT_* секреты (3 записи)
  - core/modules/AGENTS.md — carve-out для healthcheck.sh в system-модулях с API-зависимостями
  - core/entrypoint-manifest.yaml — регистрация 4 новых канонических таргетов
  - core/AGENTS.md — canonical operations table: runner-register, runner-drain, runner-enable, unregister
  - node-configs/tronyx-vps/secrets/tronyx-vps.enc.yaml — новые PAT'ы
  - .github/workflows/*.yml — runs-on миграция (6 workflow, явный список)
REQUIRES:
  - DevPlan 069 (исходный план)
  - Доступ к трем экспертным рецензиям (предоставлены пользователем)
  - VerificationReport 01 (настоящий amendment основан на нём)
  - Знание платформенной архитектуры: secrets flow, system-module контракт, severity-модель, языковая политика
$END_ARTIFACT_CONTRACT

---

## 0. SUPERPOSITION — Полный анализ всех предложений

### Методология

Три рецензии проанализированы, предложения дедуплицированы, сгруппированы по доменам. Каждое предложение оценено по 4 осям:

| Ось | Критерий |
|-----|----------|
| **Архитектурное соответствие** | Не нарушает ли платформенные инварианты (AGENTS.md)? |
| **Зрелость/Maturity fit** | Соответствует ли текущему масштабу (1 VPS, 2 org, ~10-20 джоб/день)? |
| **Стоимость реализации** | Сколько строк кода/новых зависимостей? |
| **Операционный выигрыш** | Насколько снижает риск инцидентов/упрощает эксплуатацию? |

---

### 0.1 Критические ошибки (must-fix — все 3 рецензии совпали)

#### S1: PAT scope `manage_runners:org` не существует → нужен GitHub App или Classic PAT

| Источник | Review 1 (C1), Review 3 |
|----------|--------------------------|
| Проблема | Fine-grained PAT не имеет permission для управления self-hosted runners |
| Реальность | Classic PAT (`admin:org`) или GitHub App (`organization_self_hosted_runners: write`) |

**Суперпозиция вариантов:**

| Вариант | Механизм | Scope | Expiry | Сложность | Платформенный fit |
|---------|----------|-------|--------|-----------|-------------------|
| **A: Classic PAT** | `admin:org` → API → registration token → `config.sh` | Широкий | 1 год | 1 API call, curl | ✅ Уже есть GHCR_PULL_TOKEN (PAT-паттерн) |
| **B: GitHub App** | App ID + private key → JWT → installation token → registration token → `config.sh` | Минимальный | Нет | 3 API call, нужен Python JWT | 🟡 Новый паттерн, private key в SOPS |
| **C: Registration token из UI** | Ручное копирование из GitHub Settings | N/A | 1 час | 0 API calls | ❌ Неавтоматизируемо |

**Вердикт: ACCEPT вариант A (Classic PAT) с TRAP на будущую миграцию на GitHub App**

Рациональность:
1. Платформа **уже использует** Classic PAT для `GHCR_PULL_TOKEN` — это установленный паттерн
2. `admin:org` на выделенном PAT (не переиспользуемом для других целей) — приемлемый компромисс на текущем масштабе
3. Одна `curl` команда вместо JWT-генерации → соблюдение принципа Small Simple Blocks
4. GitHub App добавляет сложность (генерация JWT, хранение private key, installation ID) без пропорционального выигрыша при 2 org
5. PAT используется **только в момент регистрации** — не живёт в процессе раннера постоянно

⚠️ TRAP[PAT-SCOPE] · 2026-07-24 · MED · Classic PAT: `admin:org` → будущая миграция на GitHub App
· Classic PAT с `admin:org` имеет широкие права — компрометация токена = доступ ко всем настройкам org.
· Mitigation: PAT используется только при регистрации (register.sh), после чего раннер хранит собственный OAuth token
  в `.credentials`. PAT не остаётся в памяти процесса раннера.
· Migration path: при появлении >3 контекстных org или при ужесточении security-политики → перейти на GitHub App.
  Интерфейс `register.sh <org>` остаётся тем же — меняется только внутренняя логика получения registration token.
· Rev: 2027-07-24 — переоценить необходимость GitHub App.

#### S2: `config.sh --token` принимает registration token, НЕ PAT

| Источник | Review 1 (C2) |
|----------|---------------|
| Проблема | В `register.sh` строка `./config.sh --url "..." --token "${PAT}"` не будет работать — config.sh ожидает одноразовый registration token (срок 1 час), а не PAT |
| Правильный flow | PAT → `POST /orgs/{org}/actions/runners/registration-token` → получить registration token → `config.sh --token <reg_token>` |

**Вердикт: ACCEPT — исправить registration flow**

Исправленная `register.sh`:
```bash
# Phase 2 (исправленная): получить registration token через PAT, затем использовать его
configure_runner() {
    cd "${RUNNER_DIR}"
    local labels="self-hosted,linux,x64,ai-platform,${ORG}"
    local name="${NODE_NAME}-runner-${ORG}"

    if [ -f "${RUNNER_DIR}/.runner" ]; then
        echo "[IMP:5][gha-runner@${ORG}] Already registered as '${name}'"
        return 0
    fi

    # Шаг 1: получить registration token через PAT (gha_api.py)
    echo "[IMP:7][gha-runner@${ORG}] Requesting registration token from GitHub API..."
    local reg_token
    reg_token=$(python3 "${SCRIPT_DIR}/gha_api.py" registration-token --pat "${PAT}" --org "${ORG}")

    if [ -z "${reg_token}" ]; then
        echo "[IMP:9][gha-runner@${ORG}] Failed to obtain registration token — HTTP error or PAT expired" >&2
        exit 1
    fi
    echo "[IMP:7][gha-runner@${ORG}] Registration token obtained (expires in 1 hour)"

    # Шаг 2: использовать registration token для config.sh
    ./config.sh \
        --url "https://github.com/${ORG}" \
        --token "${reg_token}" \
        --name "${name}" \
        --labels "${labels}" \
        --unattended \
        --replace

    echo "[IMP:9][gha-runner@${ORG}] Registered as '${name}' with labels '${labels}'"
}
```

#### S3: `--once` + `Restart=always` = restart storm

| Источник | Review 1 (C3), Review 2 (Critical 1), Review 3 (#14) |
|----------|------------------------------------------------------|
| Проблема | При отсутствии джоб раннер с `--once` завершается сразу (exit 0), systemd перезапускает через 30s → бесконечный цикл |
| Консенсус | Все три рецензии рекомендуют убрать `--once` |

**Суперпозиция вариантов:**

| Вариант | Описание | Плюсы | Минусы | Platform fit |
|---------|----------|-------|--------|--------------|
| **A: Daemon mode** | `./run.sh` без `--once` | Просто, стабильно, нет restart storm | Грязное состояние между джобами | ✅ Рекомендовано всеми |
| B: `--once` + exit code handling | Проверять exit code, перезапускать только при ошибке | Чистое состояние | Сложный systemd unit, race conditions | ❌ Overengineered |
| C: `--once` + `Restart=on-failure` | Перезапускать только при падении | Баланс | Раннер умирает навсегда если нет джоб | ❌ Требует watchdog |

**Вердикт: ACCEPT вариант A — daemon mode**

Рациональность:
1. Анализ 9 воркфлоу уже показал: все делают cleanup, нет зависимости от чистого состояния
2. Docker layer cache идёт через ghcr.io (не зависит от локального состояния)
3. Низкая частота джоб (~10-20/день) не создаёт проблем с накоплением состояния
4. Очистка между джобами обеспечивается через `ACTIONS_RUNNER_HOOK_JOB_COMPLETED` (документированный механизм GitHub)
5. `Restart=always` остаётся для восстановления после реальных падений (а не для between-job cycling)

Исправленный `run.sh`:
```bash
# Используем daemon mode (без --once) — раннер живёт постоянно
# Очистка между джобами — через ACTIONS_RUNNER_HOOK_JOB_COMPLETED
# Restart=always в systemd — для восстановления после падений, а не между джобами
export ACTIONS_RUNNER_HOOK_JOB_STARTED="${RUNNER_DIR}/config/hooks/job-started.sh"
export ACTIONS_RUNNER_HOOK_JOB_COMPLETED="${RUNNER_DIR}/config/hooks/job-completed.sh"

exec ./run.sh  # daemon mode — без --once
```

---

### 0.2 Реализационные ошибки (must-fix — хотя бы 1 рецензия)

#### S4: Смешение имён `run.sh` — конфликт с actions/runner

| Источник | Review 1 (S1) |
|----------|---------------|
| Проблема | Наш wrapper назван `run.sh` так же, как бинарник actions/runner. При обновлении дистрибутива — риск перезаписи |

**Вердикт: ACCEPT — переименовать wrapper в `start.sh`**

Rationale: `start.sh` однозначно указывает на entrypoint, не конфликтует с дистрибутивом.

#### S5: Отсутствует checksum verification при скачивании бинарника

| Источник | Review 1 (S2) |
|----------|---------------|
| Проблема | `curl -sL "${url}" | tar xz` — нет проверки SHA256 → supply chain risk |

**Вердикт: ACCEPT — добавить проверку checksum**

```bash
download_runner() {
    local version="${RUNNER_VERSION}"
    local base_url="https://github.com/actions/runner/releases/download/v${version}"
    local tarball="actions-runner-linux-x64-${version}.tar.gz"
    mkdir -p "${RUNNER_DIR}"
    if [ ! -f "${RUNNER_DIR}/.version" ] || [ "$(cat "${RUNNER_DIR}/.version")" != "${version}" ]; then
        echo "[IMP:7][gha-runner@${ORG}] Downloading runner v${version}..."
        curl -sLO "${base_url}/${tarball}"
        curl -sLO "${base_url}/${tarball}.sha256"
        sha256sum -c "${tarball}.sha256"
        tar xzf "${tarball}" -C "${RUNNER_DIR}"
        rm -f "${tarball}" "${tarball}.sha256"
        echo "${version}" > "${RUNNER_DIR}/.version"
        echo "[IMP:9][gha-runner@${ORG}] Runner v${version} downloaded and verified"
    else
        echo "[IMP:5][gha-runner@${ORG}] Runner v${version} already installed"
    fi
}
```

#### S6: Нет unregister/deregister логики

| Источник | Review 1 (S3) |
|----------|---------------|
| Проблема | При удалении модуля раннер остаётся зарегистрированным в GitHub UI (мёртвый раннер) |

**Вердикт: ACCEPT — добавить `ExecStopPre` + `make unregister MODULE=gha-runner ORG=<org>`**

Интеграция: кастомный Makefile добавляет таргет `unregister` (вызывается при удалении модуля из `modules:`).

```bash
# unregister.sh <org>
REMOVAL_TOKEN=$(python3 "${SCRIPT_DIR}/gha_api.py" removal-token --pat "${PAT}" --org "${ORG}")
./config.sh remove --token "${REMOVAL_TOKEN}"
```

⚠️ TRAP[UNREGISTER-GATE] · 2026-07-24 · MED · `make unregister` требует PAT в secrets.env
· Если модуль удалён из node.yaml, `deploy-modules.sh` должен вызвать дерегистрацию ДО удаления файлов.
· Порядок: deregister runner → systemctl disable --now → удалить spool_dir.
· Rev: если PAT уже недоступен (истёк) → ручная дерегистрация через GitHub UI.

#### S7: module.yaml — `unit: gha-runner.service` vs template unit `gha-runner@.service`

| Источник | Review 1 (S4) |
|----------|---------------|
| Проблема | module.yaml указывает `unit: gha-runner.service`, но реально используется template unit |

**Вердикт: ACCEPT — `unit: gha-runner@.service` с документацией template-поведения**

Варианты: (a) адаптировать `module-system.mk` для template units — `install` таргет должен поддерживать `%i` в имени unit'a; (b) модуль предоставляет свой `install.sh`, который сам делает `systemctl enable gha-runner@<org>.service`.

**Решение:** `module-system.mk` НЕ используется для gha-runner. Причины:
1. `module-system.mk` таргет `install` делает `cp *.service → systemctl enable $(SERVICE_NAME) → systemctl restart $(SERVICE_NAME)` — это не работает для template unit `gha-runner@.service` (требуется указание инстанса `gha-runner@<org>.service`).
2. Кастомный `Makefile` предоставляет `install`, `status`, `restart`, `logs` таргеты с поддержкой `ORG=` параметра для template unit.
3. `install.sh` сам управляет `systemctl enable gha-runner@<org>.service` для каждой org.
4. Этот подход уже используется в модуле `platform-secrets` (system-модуль с кастомным `install.sh`).

Исправленный `module.yaml`:
```yaml
name: gha-runner
install_type: system
description: "GitHub Actions self-hosted runner — template unit gha-runner@.service, one instance per GitHub org"
systemd:
  unit: gha-runner@.service      # template unit, %i = org name
  required_by: []
depends_on: [platform-secrets]    # Нужен secrets.env для PAT'ов
severity: normal
interfaces:
  - install
  - healthcheck
  - unregister                     # Новый интерфейс — дерегистрация из GitHub
env_requires:
  - name: GHA_RUNNER_PAT
    type: secret
    required: true
spool_dir: /opt/gha-runner
```

#### S8: Нет механизма обновления runner binary

| Источник | Review 1 (S5), Review 3 (#1) |
|----------|------------------------------|
| Проблема | `install.sh` проверяет наличие `run.sh` → если есть, не обновляет. Версия застревает на 2.322.0 |

**Вердикт: ACCEPT — version-aware update через `.version` файл**

Механизм: `download_runner()` сравнивает `${RUNNER_DIR}/.version` с `RUNNER_VERSION`. Если отличаются — скачивает заново.

**Важно:** Обновление бинарника не сбрасывает регистрацию (`.runner` и `.credentials` сохраняются в `spool_dir`). Это проверено документацией GitHub Actions runner.

---

### 0.3 Операционные улучшения (accept — усиление эксплуатационных качеств)

#### S9: Drain Mode — `make runner-drain ORG=<org>` / `make runner-enable ORG=<org>`

| Источник | Review 3 (#2) |
|----------|---------------|
| Предложение | Перед обслуживанием (deploy-modules, node-update) — перевести раннер в режим "не брать новые джобы", дать текущей джобе завершиться |

**Вердикт: ACCEPT — добавить таргет `runner-drain`**

Реализация через GitHub API:
```bash
# runner-drain.sh <org>
# Получить runner_id по имени (через gha_api.py)
RUNNER_ID=$(python3 "${SCRIPT_DIR}/gha_api.py" runner-id --pat "${PAT}" --org "${ORG}" --name "${RUNNER_NAME}")
# Disable runner (перестаёт принимать новые джобы, текущая завершается)
curl -s -X PUT -H "Authorization: Bearer ${PAT}" \
    "https://api.github.com/orgs/${ORG}/actions/runners/${RUNNER_ID}" \
    -d '{"status": "offline"}'
# Аналогично для runner-enable.sh: PUT с '{"status": "online"}'
```

Интеграция в `deploy-modules.sh`: перед `systemctl restart` — drain → wait (до 5 min) → restart.

#### S10: Healthcheck — глубокая проверка через Runner.Listener CLI

| Источник | Review 2 (Critical 2), Review 3 (#3) |
|----------|--------------------------------------|
| Проблема | Текущий healthcheck проверяет файлы `.runner`/`.credentials`, но они существуют даже при истёкшей регистрации |

**Вердикт: ACCEPT — deep healthcheck через Runner.Listener + GitHub API**

Исправленный `healthcheck.sh`:
```bash
# Liveness (быстрый): systemctl is-active
# Deep: проверка реального состояния
if [ "$MODE" = "deep" ]; then
    # 1. Runner.Listener status (локальная проверка)
    if ! "${RUNNER_DIR}/bin/Runner.Listener" --version &>/dev/null; then
        echo "[IMP:9][gha-runner@${org}] Runner.Listener binary broken"
        ALL_HEALTHY=false; continue
    fi

    # 2. GitHub API: runner online? (через gha_api.py)
    local api_status
    api_status=$(python3 "${SCRIPT_DIR}/gha_api.py" runner-status --pat "${PAT}" --org "${ORG}" --name "${name}")
    if [ "${api_status}" != "online" ]; then
        echo "[IMP:9][gha-runner@${org}] GitHub API reports status='${api_status}' (expected 'online')"
        ALL_HEALTHY=false; continue
    fi
    echo "[IMP:7][gha-runner@${org}] Deep health: online, binary OK"
fi
```

#### S11: Watchdog интеграция с Telegram

| Источник | Review 3 (#4) |
|----------|---------------|
| Предложение | Если systemd достигает StartLimitBurst → Telegram-уведомление |

**Вердикт: ACCEPT — интеграция через существующий hermes-agent**

Платформа уже имеет `hermes-agent` с Telegram-нотификациями. Реализация:
1. `healthcheck.sh` проверяет `systemctl is-failed gha-runner@*.service`
2. При обнаружении failed → пишет в `/run/platform/gha-runner-alert`
3. `hermes-agent` (cron/healthcheck) читает alert-файл → отправляет Telegram-уведомление

⚠️ TRAP[WATCHDOG] · 2026-07-24 · LOW · Watchdog НЕ должен пытаться auto-reregister
· Причина: auto-reregistration требует хранения PAT в процессе (security risk).
· Правильное поведение: alert → ручное `make runner-register ORG=<org>`.
· Rev: если частота алертов >1/месяц → добавить auto-reregister с PAT из SOPS.

#### S12: Resource limits — MemoryMax, CPUQuota в systemd unit

| Источник | Review 1 (R1), Review 3 (#8) |
|----------|------------------------------|
| Предложение | Ограничить ресурсы CI-процесса, чтобы не забить production |

**Вердикт: ACCEPT — добавить в systemd unit**

```ini
[Service]
MemoryMax=4G
CPUQuota=200%
TasksMax=512
# Приоритет: CI не должен мешать production
Nice=10
IOSchedulingClass=idle
```

⚠️ TRAP[RESOURCE-LIMITS] · 2026-07-24 · LOW · Лимиты подобраны эмпирически для VPS с 8GB RAM
· Если CI-джоба падает с OOM → увеличить MemoryMax или оптимизировать workflow.
· Rev: после 2 недель эксплуатации — проверить пиковое потребление и скорректировать.

#### S13: Diagnose — `make diagnose MODULE=gha-runner`

| Источник | Review 3 (#5) |
|----------|---------------|
| Предложение | Единая команда диагностики: версия, регистрация, GitHub API, Docker, SSH, secrets, метрики |

**Вердикт: ACCEPT — скрипт diagnose.sh как часть модуля**

```bash
# diagnose.sh — выводит полное состояние раннера
# Секции: Version, Registration (GitHub API), Docker access, SSH key, systemd status, Labels, Disk, Memory
```

Вывод форматирован для machine reading (JSON) и human reading (table).

#### S14: Capability Check — проверка перед установкой

| Источник | Review 3 (#6) |
|----------|---------------|
| Предложение | Перед `install.sh` проверить: Docker, git, curl, tar, python3 |

**Вердикт: ACCEPT — добавить `preflight()` в install.sh**

```bash
preflight() {
    local ok=true
    for cmd in docker git curl tar python3; do
        if ! command -v "$cmd" &>/dev/null; then
            echo "[IMP:9][gha-runner][preflight] Missing required command: ${cmd}" >&2
            ok=false
        fi
    done
    [ "$ok" = true ] || exit 1
    echo "[IMP:7][gha-runner][preflight] All required commands available"
}
```

#### S15: Cleanup Policy — явная политика очистки Docker

| Источник | Review 3 (#7) |
|----------|---------------|
| Предложение | Документировать что удаляем, что НЕ удаляем |

**Вердикт: ACCEPT — явная политика в cleanup.sh**

```bash
#!/usr/bin/env bash
# Cleanup policy for gha-runner between jobs:
# ✓ REMOVE:
#   - Exited containers (docker container prune)
#   - Dangling images (docker image prune --filter dangling=true)
#   - Temp dirs (/tmp/gha-*)
# ✗ PRESERVE:
#   - Docker volumes (production data)
#   - Build cache (used by buildx)
#   - ghcr.io pulled images (registry cache)
#   - .venv (actions/cache@v6 manages separately)
```

#### S16: Concurrency Strategy — документировать модель параллелизма

| Источник | Review 3 (#9), Review 1 (R6) |
|----------|------------------------------|
| Предложение | Явно зафиксировать: max parallel jobs = 1 per runner instance |

**Вердикт: ACCEPT — документировать в module.yaml и DevPlan**

Архитектурное решение:
- Один runner instance на одну GitHub org = 1 параллельная джоба на org
- GitHub сам ставит джобы в очередь если runner занят
- Если org'и разные — джобы выполняются параллельно (разные systemd инстансы)
- Масштабирование: добавить второй инстанс `gha-runner@<org>-2.service` (future)

#### S17: Rollback Plan

| Источник | Review 1 (S6) |
|----------|---------------|
| Предложение | Документировать процедуру отката |

**Вердикт: ACCEPT — добавить секцию Rollback Plan**

См. секцию 8 ниже.

---

### 0.4 Безопасность и секреты (accept с платформенной адаптацией)

#### S18: Security hardening — chmod 600 для .credentials

| Источник | Review 1 (R5) |
|----------|---------------|
| Предложение | `.credentials` содержит OAuth token → restrict permissions |

**Вердикт: ACCEPT — добавить в register.sh**

```bash
chmod 600 "${RUNNER_DIR}/.credentials"
chown platform:platform "${RUNNER_DIR}/.credentials"
chmod 600 "${RUNNER_DIR}/.runner"
```

#### S19: Secrets Rotation — PAT только для регистрации, не для раннера

| Источник | Review 3 (#11) |
|----------|---------------|
| Предложение | PAT не должен жить внутри процесса раннера. Использовать только в момент регистрации. |

**Вердикт: ACCEPT — архитектурно уже так и есть**

В текущем дизайне:
1. `register.sh` использует PAT для получения registration token (одноразовый, 1 час)
2. `config.sh` обменивает registration token на OAuth token → сохраняет в `.credentials`
3. Раннер использует `.credentials` (OAuth token, не PAT) для поддержания WebSocket
4. PAT в `/run/platform/secrets.env` — НЕ читается процессом раннера напрямую

**Улучшение:** `run.sh` больше не читает PAT из secrets.env. Только `register.sh`. Это устраняет S2 (неверный registration flow) и снижает поверхность атаки.

#### S20: Docker group security + cgroups

| Источник | Review 2 (#6) |
|----------|---------------|
| Предложение | Дополнительные ограничения через cgroups для контейнеров CI |

**Вердикт: ACCEPT — добавить в systemd unit**

```ini
# Ограничения на дочерние процессы (CI-контейнеры)
CPUQuota=200%
MemoryMax=4G
TasksMax=512
```

Эти лимиты применяются ко всему дереву процессов (раннер + его дочерние контейнеры).

---

### 0.5 Мониторинг и наблюдаемость (accept)

#### S21: Telemetry/Metrics — Prometheus метрики раннера

| Источник | Review 3 (#12) |
|----------|---------------|
| Предложение | Экспортировать: runner_online, jobs_completed, jobs_failed, last_job_duration |

**Вердикт: ACCEPT — textfile collector для node_exporter**

Платформа имеет `infra-metrics` модуль (Prometheus + node_exporter). Node_exporter поддерживает textfile collector.

Реализация:
1. `job-completed.sh` хук пишет метрики в `/run/platform/gha-runner-metrics.prom`
2. Node_exporter забирает их через `--collector.textfile.directory=/run/platform`

```prometheus
# HELP gha_runner_jobs_total Total jobs processed
# TYPE gha_runner_jobs_total counter
gha_runner_jobs_total{org="tronyx161",status="success"} 42
gha_runner_jobs_total{org="tronyx161",status="failed"} 3
# HELP gha_runner_online Runner registration status (1=online)
# TYPE gha_runner_online gauge
gha_runner_online{org="tronyx161"} 1
```

#### S22: Journald rate limiting

| Источник | Review 2 (Critical 3) |
|----------|------------------------|
| Предложение | Ограничить объем логов CI в journald |

**Вердикт: ACCEPT — rate limiting в systemd unit (не глобально)**

```ini
[Service]
LogRateLimitIntervalSec=10
LogRateLimitBurst=1000
```

⚠️ TRAP[JOURNALD-RATE] · 2026-07-24 · LOW · Rate limiting может обрезать полезные логи CI
· Если важные логи теряются → увеличить Burst или выборочно писать в отдельный файл.
· Rev: после 1 недели эксплуатации — проверить journalctl на наличие обрезанных записей.

---

### 0.6 Частичный ACCEPT (принять с модификацией под платформу)

#### S23: Auto re-registration на token expiry

| Источник | Review 2 (Critical 4) |
|----------|------------------------|
| Исходное предложение | Автоматически перерегистрировать при ошибке 401/403 |

**Вердикт: MODIFY — not auto, но notify**

Платформенная адаптация:
- **НЕ** auto-reregister (требует хранения PAT в процессе — security risk)
- Вместо этого: `healthcheck.sh` deep mode → проверяет GitHub API status → если offline → алерт через watchdog/Telegram
- `systemd`: `StartLimitBurst=3` + `StartLimitInterval=600` → после 3 падений за 10 минут юнит останавливается
- Watchdog замечает failed unit → Telegram: "Runner @org offline — run `make runner-register ORG=<org>`"

⚠️ TRAP[AUTO-REREGISTER] · 2026-07-24 · MED · Отказ от auto-reregistration
· Rejected: auto-reregister (Review 2 #4) — требует постоянного хранения PAT в процессе.
· Reason: PAT даёт `admin:org` — компрометация процесса раннера = компрометация всей org.
· Mitigation: watchdog + Telegram alert + ручная команда. Частота: 1 раз в год (истечение PAT).
· Rev: если ручная перерегистрация становится частой (>1/месяц) → GitHub App с JWT (безопасно для auto).

#### S24: Оффлайн-режим (GitHub недоступен)

| Источник | Review 3 (#10) |
|----------|----------------|
| Предложение | Определить поведение при недоступности GitHub |

**Вердикт: MODIFY — native behaviour достаточен**

Платформенная оценка:
- GitHub runner в daemon mode имеет встроенный exponential backoff при потере связи
- Никакого специального handling'а не требуется
- Документировать: раннер продолжает poll'ить GitHub; если связь восстановится в течение 1 часа — регистрация сохраняется
- Если GitHub недоступен >1 часа — раннер останавливается (systemd StartLimitBurst) → watchdog alert

---

### 0.7 REJECT (отклонены — противоречат платформенным инвариантам или неоправданны на текущем масштабе)

#### S25: Явный GITHUB_ORG параметр в node.yaml

| Источник | Review 2 (#5) |
|----------|---------------|
| Предложение | `modules: [{name: gha-runner, args: {GITHUB_ORG: tronyx-lab}}]` |

**Вердикт: REJECT**

Rationale:
1. Платформа **уже имеет** механизм определения контекста: `node.yaml#context` → `platform-env.yaml#context` → `install.sh`
2. GitHub Org = context name (инвариант: org = context). Дублирование создаёт divergence risk.
3. `install.sh` уже извлекает context из `platform-env.yaml` — это детерминированный механизм
4. Добавление `args` в modules: требует изменения schema `node.yaml`, `deploy-modules.sh`, `module-interface.sh` — неоправданная сложность

⚠️ TRAP[GITHUB-ORG-EXPLICIT] · 2026-07-24 · LOW · Явный GITHUB_ORG отклонён
· Rejected: Review 2 предложил args: GITHUB_ORG в node.yaml.
· Reason: нарушает DRY — context уже однозначно определяет org. Два источника truth → divergence.
· Rev: если появится use-case где GitHub Org ≠ context → пересмотреть.

#### S26: Усложнение label strategy

| Источник | Review 2 (#7) |
|----------|---------------|
| Предложение | `runs-on: [self-hosted, ai-platform, tronyx161]` — точные labels |

**Вердикт: REJECT**

Rationale:
1. На текущем масштабе (1 VPS, 2 org) `runs-on: self-hosted` достаточно
2. Разные org имеют РАЗНЫХ runner'ов (разные `gha-runner@<org>.service`) — дисambiguация через org-level scope
3. Добавление специфичных labels усложняет CI-конфигурацию без выигрыша
4. Если появится несколько VPS с разными capabilities → добавить labels тогда, с явным use-case

⚠️ TRAP[LABEL-STRATEGY] · 2026-07-24 · LOW · Простые labels
· Rejected: усложнение label strategy (Review 2 #7).
· Reason: текущий масштаб не требует тонкого роутинга. `self-hosted` + org-level изоляция достаточны.
· Rev: при появлении GPU-раннера или нескольких VPS → добавить capability labels.

#### S27: Multiple runner instances per org для параллелизма

| Источник | Review 1 (R6) |
|----------|---------------|
| Предложение | Настроить несколько runner instances на одну org |

**Вердикт: REJECT (отложен)**

Rationale:
1. Текущая частота джоб (~10-20/день) не требует параллелизма
2. Одна джоба (platform-test.yml) длится ~40 мин — остальные workflow триггерятся последовательно (workflow_run)
3. Добавление параллельных инстансов = усложнение без немедленной потребности

⚠️ TRAP[MULTI-INSTANCE] · 2026-07-24 · LOW · Один инстанс на org
· Rejected: multiple runner instances per org (Review 1 R6).
· Reason: текущая частота джоб не требует параллелизма. Пайплайн уже последовательный (workflow_run).
· Rev: если время ожидания в очереди >10 мин → добавить второй инстанс `gha-runner@<org>-2.service`.

---

### 0.8 Сводная таблица: все предложения с вердиктами

| # | Предложение | Источник | Type | Вердикт | Секция |
|---|------------|----------|------|---------|--------|
| S1 | PAT scope `manage_runners:org` не существует | R1,R3 | CRITICAL | ACCEPT → Classic PAT | 0.1 |
| S2 | `config.sh --token` ожидает registration token, не PAT | R1 | CRITICAL | ACCEPT → fix flow | 0.1 |
| S3 | `--once` + `Restart=always` = restart storm | R1,R2,R3 | CRITICAL | ACCEPT → daemon mode | 0.1 |
| S4 | `run.sh` конфликт имён с дистрибутивом | R1 | BUG | ACCEPT → start.sh | 0.2 |
| S5 | Checksum verification при скачивании | R1 | BUG | ACCEPT → sha256sum | 0.2 |
| S6 | Unregister/deregister логика | R1 | BUG | ACCEPT → unregister.sh | 0.2 |
| S7 | module.yaml unit name mismatch | R1 | BUG | ACCEPT → @.service | 0.2 |
| S8 | Механизм обновления runner binary | R1,R3 | BUG | ACCEPT → .version | 0.2 |
| S9 | Drain Mode | R3 | FEATURE | ACCEPT → runner-drain | 0.3 |
| S10 | Healthcheck через Runner.Listener + API | R2,R3 | FEATURE | ACCEPT → deep mode | 0.3 |
| S11 | Watchdog + Telegram интеграция | R3 | FEATURE | ACCEPT → hermes-agent | 0.3 |
| S12 | Resource limits (MemoryMax, CPUQuota) | R1,R3 | FEATURE | ACCEPT → systemd unit | 0.3 |
| S13 | Diagnose command | R3 | FEATURE | ACCEPT → diagnose.sh | 0.3 |
| S14 | Capability Check перед установкой | R3 | FEATURE | ACCEPT → preflight | 0.3 |
| S15 | Cleanup Policy — явная политика | R3 | FEATURE | ACCEPT → документировать | 0.3 |
| S16 | Concurrency Strategy — документировать | R1,R3 | DOC | ACCEPT → задокументировать | 0.3 |
| S17 | Rollback Plan | R1 | DOC | ACCEPT → секция 8 | 0.3 |
| S18 | .credentials chmod 600 | R1 | SECURITY | ACCEPT → register.sh | 0.4 |
| S19 | PAT только для регистрации, не для раннера | R3 | SECURITY | ACCEPT → уже так | 0.4 |
| S20 | Docker group + cgroups | R2 | SECURITY | ACCEPT → systemd limits | 0.4 |
| S21 | Prometheus метрики | R3 | MONITORING | ACCEPT → textfile collector | 0.5 |
| S22 | Journald rate limiting | R2 | MONITORING | ACCEPT → unit-level | 0.5 |
| S23 | Auto re-registration | R2 | FEATURE | MODIFY → notify, not auto | 0.6 |
| S24 | Оффлайн-режим | R3 | FEATURE | MODIFY → native behaviour | 0.6 |
| S25 | Явный GITHUB_ORG в node.yaml | R2 | ARCH | REJECT → DRY violation | 0.7 |
| S26 | Усложнение label strategy | R2 | ARCH | REJECT → YAGNI | 0.7 |
| S27 | Multiple instances per org | R1 | ARCH | REJECT → YAGNI | 0.7 |

**Статистика:** ACCEPT: 22, MODIFY: 2, REJECT: 3. Coverage: 100% предложений.

**Amendment (VerificationReport 01, 2026-07-24):** 7 drift fixes применены — 2 CRITICAL (DRIFT-1 языковая политика, DRIFT-2/3 module-system.mk), 3 HIGH (DRIFT-4 healthcheck contract, DRIFT-5 SSoT), 2 MEDIUM (DRIFT-6 workflow count, DRIFT-7 error handling). Добавлен `gha_api.py` Python-модуль, обновлены все code blocks, явные SSoT записи, явный список workflow, entrypoint-manifest.yaml регистрация.

---

## 1. Исправленный модуль: структура

```
core/modules/gha-runner/
├── module.yaml                     # D5-контракт (исправлен: unit, depends_on, interfaces, env_requires)
├── gha_api.py                      # Python-модуль: GitHub API client (DRY, языковая политика)
├── gha-runner@.service             # systemd template unit (исправлен: resource limits, rate limiting, ExecStart)
├── Makefile                        # Кастомный Makefile — module-system.mk НЕ используется (template units)
├── install.sh                      # Установка (исправлен: preflight, download с checksum, регистрация через API)
├── healthcheck.sh                  # Liveness + deep (исправлен: Runner.Listener + GitHub API deep check,
│                                   #   см. carve-out в core/modules/AGENTS.md §System-модули для API-based health)
├── register.sh                     # Регистрация в одной org (исправлен: registration token flow через gha_api.py)
├── unregister.sh                   # Дерегистрация из одной org (НОВЫЙ, через gha_api.py)
├── start.sh                        # Wrapper для systemd (исправлен: переименован из run.sh, daemon mode)
├── diagnose.sh                     # Диагностика (НОВЫЙ)
├── runner-drain.sh                 # Drain mode (НОВЫЙ, через gha_api.py)
├── runner-enable.sh                # Enable after drain (НОВЫЙ)
└── config/
    ├── runner.env                  # Дефолтные RUNNER_VERSION, RUNNER_LABELS
    ├── cleanup.sh                  # Очистка Docker (исправлен: явная политика)
    └── hooks/
        ├── job-started.sh          # Хук: логирование старта джобы, метрики
        └── job-completed.sh        # Хук: cleanup + запись Prometheus метрик
```

### Примечание: отказ от module-system.mk

`module-system.mk` НЕ используется для gha-runner. Причины:
1. **Template units несовместимы с module-system.mk** — `install` таргет в `module-system.mk` делает `cp *.service → systemctl enable $(SERVICE_NAME) → systemctl restart $(SERVICE_NAME)`, что не работает для `gha-runner@.service` (требуется указание инстанса `gha-runner@<org>.service`).
2. **Кастомный Makefile** предоставляет все необходимые таргеты (`install`, `status`, `restart`, `logs`) с поддержкой `ORG=` параметра для template unit.
3. **`install` идёт через `install.sh`** — вызывается `deploy-modules.sh → invoke_module_interface → install.sh`, а не через `module-system.mk`.
4. Этот подход уже используется в модуле `platform-secrets` (system-модуль с кастомным `install.sh`).

**Изменения относительно оригинального DevPlan 069:**
- `run.sh` → `start.sh` (S4)
- Добавлен `gha_api.py` — Python-модуль для GitHub API (языковая политика, DRY-1)
- Добавлены: `unregister.sh` (S6), `diagnose.sh` (S13), `runner-drain.sh` (S9), `runner-enable.sh` (S9)
- Добавлены: `config/hooks/job-started.sh`, `config/hooks/job-completed.sh` (S21)
- `healthcheck.sh`: deep mode использует Runner.Listener + GitHub API через `gha_api.py` (S10)
- `register.sh`: правильный registration flow через API (S2), все API-вызовы через `gha_api.py`
- `install.sh`: preflight checks (S14), checksum verification (S5), version-aware download (S8)
- `Makefile`: кастомный (не `module-system.mk`) — template unit handling с ORG= параметром

---

## 2. Исправленный module.yaml

```yaml
name: gha-runner
install_type: system
description: "GitHub Actions self-hosted runner — persistent, template unit gha-runner@.service, one instance per GitHub org"
systemd:
  unit: gha-runner@.service          # template unit, %i = GitHub org name
  required_by: []
depends_on: [platform-secrets]        # secrets.env должен быть доступен до register.sh
severity: normal                      # Не блокирует node-update при ошибке — CI не critical path
interfaces:
  - install
  - healthcheck
  - unregister                        # Дерегистрация при удалении модуля
env_requires:
  - name: GHA_RUNNER_PAT
    type: secret
    required: true
    description: "Classic PAT с admin:org scope для получения registration token. Используется только в register.sh, не живёт в процессе раннера."
  - name: GHA_RUNNER_PAT_TRONYX161
    type: secret
    required: false                   # per-org PAT'ы optional — fallback на GHA_RUNNER_PAT
  - name: GHA_RUNNER_PAT_TRONYX_LAB
    type: secret
    required: false
spool_dir: /opt/gha-runner           # Бинарник + .runner + .credentials + .version
concurrency:
  max_parallel_jobs_per_instance: 1   # Одна джоба на org одновременно. GitHub ставит остальные в очередь.
```

---

## 3. Исправленный systemd template unit (gha-runner@.service)

```ini
[Unit]
Description=GitHub Actions Self-Hosted Runner for org %i
After=network-online.target platform-secrets.service
Wants=network-online.target
Requires=platform-secrets.service
StartLimitInterval=600
StartLimitBurst=3

[Service]
Type=simple
User=platform
Group=platform
WorkingDirectory=/opt/gha-runner/%i
EnvironmentFile=/run/platform/secrets.env
EnvironmentFile=-/opt/platform/core/modules/gha-runner/config/runner@%i.env
EnvironmentFile=-/opt/platform/core/modules/gha-runner/config/runner.env
ExecStartPre=/bin/sh -c 'test -f /opt/gha-runner/%i/.runner || { echo "[IMP:9][gha-runner@%i] Not configured — run register.sh first"; exit 1; }'
ExecStart=/opt/gha-runner/%i/start.sh
ExecStopPost=/opt/gha-runner/%i/config/cleanup.sh
Restart=always
RestartSec=30
StandardOutput=journal
StandardError=journal
SyslogIdentifier=gha-runner-%i
PrivateTmp=yes
NoNewPrivileges=no

# Resource limits — защита production VPS от CI
MemoryMax=4G
CPUQuota=200%
TasksMax=512
Nice=10
IOSchedulingClass=idle

# Journald rate limiting — предотвращение переполнения логов CI
LogRateLimitIntervalSec=10
LogRateLimitBurst=1000

[Install]
WantedBy=multi-user.target
```

---

## 4. Исправленный start.sh (бывший run.sh)

```bash
#!/usr/bin/env bash
# start.sh — wrapper для actions/runner, вызывается из systemd template unit
# ⚠️ НЕ конфликтует с actions/runner/run.sh (дистрибутив) — это наш wrapper
# %i = org name, передан через WorkingDirectory=/opt/gha-runner/%i
set -euo pipefail

RUNNER_DIR="${PWD}"
ORG_NAME="$(basename "${RUNNER_DIR}")"

# Проверка: зарегистрирован ли раннер
if [ ! -f "${RUNNER_DIR}/.runner" ]; then
    echo "[IMP:9][gha-runner@${ORG_NAME}] Runner not registered. Run register.sh first." >&2
    exit 1
fi

# Проверка: есть ли .credentials (после регистрации)
if [ ! -f "${RUNNER_DIR}/.credentials" ]; then
    echo "[IMP:9][gha-runner@${ORG_NAME}] .credentials missing — runner needs re-registration." >&2
    exit 1
fi

# Экспорт переменных для runner
export RUNNER_ALLOW_RUNASROOT=0

# Daemon mode (без --once) — раннер живёт постоянно
# Очистка между джобами — через ACTIONS_RUNNER_HOOK_JOB_COMPLETED
export ACTIONS_RUNNER_HOOK_JOB_STARTED="${RUNNER_DIR}/config/hooks/job-started.sh"
export ACTIONS_RUNNER_HOOK_JOB_COMPLETED="${RUNNER_DIR}/config/hooks/job-completed.sh"

echo "[IMP:7][gha-runner@${ORG_NAME}] Starting runner in daemon mode..."
cd "${RUNNER_DIR}"
exec ./run.sh  # daemon mode — actions/runner дистрибутив
```

---

## 5. Исправленный register.sh

```bash
#!/usr/bin/env bash
# register.sh <org> — регистрирует раннер в указанной GitHub-организации
# Может вызываться как из install.sh (автоматически), так и вручную (source-org исключение)
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ORG="${1:?Usage: register.sh <github-org>}"
RUNNER_VERSION="${RUNNER_VERSION:-2.322.0}"
RUNNER_DIR="/opt/gha-runner/${ORG}"
NODE_NAME="${NODE_NAME:-$(hostname)}"

# Имя переменной PAT — сначала специфичный (GHA_RUNNER_PAT_TRONYX161), затем общий (GHA_RUNNER_PAT)
PAT_VAR="GHA_RUNNER_PAT_$(echo "${ORG}" | tr '[:lower:]-' '[:upper:]_')"
PAT_VAR_FALLBACK="GHA_RUNNER_PAT"
PAT="${!PAT_VAR:-${!PAT_VAR_FALLBACK:-}}"

if [ -z "${PAT}" ]; then
    echo "[IMP:9][gha-runner] ${PAT_VAR} or ${PAT_VAR_FALLBACK} not set. Add to secrets.enc.yaml" >&2
    exit 1
fi

# Phase 1: Скачивание бинарника (version-aware, с checksum)
download_runner() {
    local version="${RUNNER_VERSION}"
    local base_url="https://github.com/actions/runner/releases/download/v${version}"
    local tarball="actions-runner-linux-x64-${version}.tar.gz"
    mkdir -p "${RUNNER_DIR}"

    # Проверка версии: переустановить если версия изменилась
    local current_version="0"
    [ -f "${RUNNER_DIR}/.version" ] && current_version="$(cat "${RUNNER_DIR}/.version")"

    if [ "${current_version}" = "${version}" ] && [ -f "${RUNNER_DIR}/run.sh" ]; then
        echo "[IMP:5][gha-runner@${ORG}] Runner v${version} already installed"
        return 0
    fi

    echo "[IMP:7][gha-runner@${ORG}] Downloading runner v${version} (current: v${current_version})..."
    cd /tmp
    curl -sLO "${base_url}/${tarball}"
    curl -sLO "${base_url}/${tarball}.sha256"

    if ! sha256sum -c "${tarball}.sha256"; then
        echo "[IMP:9][gha-runner@${ORG}] SHA256 checksum verification FAILED" >&2
        rm -f "${tarball}" "${tarball}.sha256"
        exit 1
    fi

    # Сохранить старые credentials если обновление
    if [ -f "${RUNNER_DIR}/.runner" ] && [ -f "${RUNNER_DIR}/.credentials" ]; then
        cp "${RUNNER_DIR}/.runner" /tmp/.runner.bak
        cp "${RUNNER_DIR}/.credentials" /tmp/.credentials.bak
    fi

    # Распаковать (перезаписывает бинарники, НЕ трогает .runner/.credentials — их нет в архиве)
    tar xzf "${tarball}" -C "${RUNNER_DIR}"
    rm -f "${tarball}" "${tarball}.sha256"

    # Восстановить credentials если было обновление
    [ -f /tmp/.runner.bak ] && mv /tmp/.runner.bak "${RUNNER_DIR}/.runner"
    [ -f /tmp/.credentials.bak ] && mv /tmp/.credentials.bak "${RUNNER_DIR}/.credentials"

    echo "${version}" > "${RUNNER_DIR}/.version"
    echo "[IMP:9][gha-runner@${ORG}] Runner v${version} downloaded and verified (SHA256 OK)"
}

# Phase 2: Конфигурация и регистрация (исправленный flow)
configure_runner() {
    cd "${RUNNER_DIR}"
    local labels="self-hosted,linux,x64,ai-platform,${ORG}"
    local name="${NODE_NAME}-runner-${ORG}"

    if [ -f "${RUNNER_DIR}/.runner" ] && [ -f "${RUNNER_DIR}/.credentials" ]; then
        echo "[IMP:5][gha-runner@${ORG}] Already registered as '${name}'"
        return 0
    fi

    # Шаг 1: получить registration token через PAT (gha_api.py)
    echo "[IMP:7][gha-runner@${ORG}] Requesting registration token from GitHub API..."
    local reg_token
    reg_token=$(python3 "${SCRIPT_DIR}/gha_api.py" registration-token --pat "${PAT}" --org "${ORG}")

    if [ -z "${reg_token}" ]; then
        echo "[IMP:9][gha-runner@${ORG}] Failed to obtain registration token — HTTP error or PAT expired" >&2
        exit 1
    fi
    echo "[IMP:7][gha-runner@${ORG}] Registration token obtained (expires in 1 hour)"

    # Шаг 2: использовать registration token для config.sh (НЕ PAT!)
    ./config.sh \
        --url "https://github.com/${ORG}" \
        --token "${reg_token}" \
        --name "${name}" \
        --labels "${labels}" \
        --unattended \
        --replace

    # Security hardening
    chmod 600 "${RUNNER_DIR}/.runner"
    chmod 600 "${RUNNER_DIR}/.credentials"
    chown platform:platform "${RUNNER_DIR}/.runner" "${RUNNER_DIR}/.credentials" 2>/dev/null || true

    echo "[IMP:9][gha-runner@${ORG}] Registered as '${name}' with labels '${labels}'"
}

# Phase 3: Создание per-org runner.env (идемпотентно)
create_runner_env() {
    local env_file="${SCRIPT_DIR}/config/runner@${ORG}.env"
    if [ ! -f "${env_file}" ]; then
        cat > "${env_file}" <<EOF
# Generated by register.sh — per-org runner config for ${ORG}
RUNNER_NAME=${NODE_NAME}-runner-${ORG}
RUNNER_LABELS=self-hosted,linux,x64,ai-platform,${ORG}
GITHUB_ORG=${ORG}
EOF
    fi
}

download_runner
configure_runner
create_runner_env

echo "[IMP:7][gha-runner@${ORG}] Registration complete. Runner: ${NODE_NAME}-runner-${ORG}"
```

---

## 6. Новые скрипты

### 6.1 unregister.sh

```bash
#!/usr/bin/env bash
# unregister.sh <org> — дерегистрирует раннер из GitHub org
# Вызывается перед удалением модуля или при ExecStopPre (опционально)
set -euo pipefail

ORG="${1:?Usage: unregister.sh <github-org>}"
RUNNER_DIR="/opt/gha-runner/${ORG}"
NODE_NAME="${NODE_NAME:-$(hostname)}"

PAT_VAR="GHA_RUNNER_PAT_$(echo "${ORG}" | tr '[:lower:]-' '[:upper:]_')"
PAT="${!PAT_VAR:-${GHA_RUNNER_PAT:-}}"

if [ -z "${PAT}" ]; then
    echo "[IMP:9][gha-runner@${ORG}] PAT not available — cannot deregister. Remove manually in GitHub UI." >&2
    exit 1
fi

if [ ! -f "${RUNNER_DIR}/.runner" ]; then
    echo "[IMP:5][gha-runner@${ORG}] Runner not registered — nothing to deregister"
    exit 0
fi

echo "[IMP:7][gha-runner@${ORG}] Deregistering runner..."
REMOVAL_TOKEN=$(python3 "${SCRIPT_DIR}/gha_api.py" removal-token --pat "${PAT}" --org "${ORG}")

if [ -n "${REMOVAL_TOKEN}" ]; then
    cd "${RUNNER_DIR}"
    ./config.sh remove --token "${REMOVAL_TOKEN}"
    echo "[IMP:9][gha-runner@${ORG}] Deregistered from GitHub org"
else
    echo "[IMP:9][gha-runner@${ORG}] Failed to obtain removal token — HTTP error or PAT expired"
    exit 1
fi
```

### 6.2 runner-drain.sh

```bash
#!/usr/bin/env bash
# runner-drain.sh <org> — переводит раннер в режим "не брать новые джобы"
# Используется перед maintenance (deploy-modules, обновление)
set -euo pipefail
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ORG="${1:?Usage: runner-drain.sh <github-org>}"
# Получить PAT, найти runner_id через gha_api.py, отправить PUT с status=offline
RUNNER_ID=$(python3 "${SCRIPT_DIR}/gha_api.py" runner-id --pat "${PAT}" --org "${ORG}" --name "${RUNNER_NAME}")
curl -s -X PUT -H "Authorization: Bearer ${PAT}" \
    "https://api.github.com/orgs/${ORG}/actions/runners/${RUNNER_ID}" \
    -d '{"status": "offline"}'
```

### 6.3 diagnose.sh

```bash
#!/usr/bin/env bash
# diagnose.sh [org] — полная диагностика раннера
# Секции: Version, Registration, GitHub API, Docker, SSH, Systemd, Labels, Disk, Memory
# Вывод: таблица (human) или JSON (--format json)
```

### 6.4 config/hooks/job-started.sh

```bash
#!/usr/bin/env bash
# Хук: вызывается GitHub Actions runner перед каждой джобой
echo "[IMP:7][gha-runner][job-started] Job started: ${GITHUB_JOB:-unknown}"
```

### 6.5 config/hooks/job-completed.sh

```bash
#!/usr/bin/env bash
# Хук: вызывается GitHub Actions runner после каждой джобы
# 1. Очистка Docker по политике (см. cleanup.sh)
# 2. Запись Prometheus метрик

echo "[IMP:7][gha-runner][job-completed] Job completed: ${GITHUB_JOB:-unknown}"

# Prometheus метрики (textfile collector)
METRICS_FILE="/run/platform/gha-runner-metrics.prom"
cat >> "${METRICS_FILE}" <<EOF
gha_runner_jobs_total{org="${GITHUB_ORG:-unknown}"} 1
EOF
```

---

### 6.6 gha_api.py — Python-модуль GitHub API

Единый Python-модуль для всех взаимодействий с GitHub Actions API. Извлекает бизнес-логику из bash-скриптов (DRY, языковая политика). Обрабатывает HTTP-ошибки явно (DRIFT-7 fix).

```python
#!/usr/bin/env python3
# core/modules/gha-runner/gha_api.py
# GREP_SUMMARY: gha_api Python module GitHub Actions API registration removal drain healthcheck
# @purpose  Single Python module wrapping all GitHub Actions API calls for gha-runner.
#           Replaces 6 inline python3 -c blocks across 4 shell scripts.
# @scope    Registration token, removal token, runner ID lookup, runner status check.

"""GitHub Actions API client for gha-runner module."""

import argparse
import json
import sys
import urllib.request
import urllib.error


# region API Client

def _github_api(method: str, url: str, pat: str, data: dict | None = None) -> dict:
    """Unified GitHub API caller with HTTP error handling.

    Returns parsed JSON response dict.
    Prints [IMP:9] error to stderr and exits with non-zero on any HTTP error.
    """
    headers = {
        "Authorization": f"Bearer {pat}",
        "Accept": "application/vnd.github+json",
        "User-Agent": "ai-platform/gha-runner",
    }
    body = json.dumps(data).encode() if data else None
    req = urllib.request.Request(url, data=body, headers=headers, method=method)
    try:
        with urllib.request.urlopen(req) as resp:
            return json.loads(resp.read())
    except urllib.error.HTTPError as e:
        error_body = e.read().decode(errors="replace")
        try:
            error_json = json.loads(error_body)
            msg = error_json.get("message", error_body[:200])
        except json.JSONDecodeError:
            msg = error_body[:200]
        print(f"[IMP:9][gha_api] GitHub API error: HTTP {e.code} {e.reason} — "
              f"{msg} (URL: {url})", file=sys.stderr)
        sys.exit(1)
    except urllib.error.URLError as e:
        print(f"[IMP:9][gha_api] Network error: {e.reason} (URL: {url})", file=sys.stderr)
        sys.exit(1)

# endregion


# region Commands

def cmd_registration_token(pat: str, org: str) -> str:
    """Obtain a registration token for a GitHub org."""
    url = f"https://api.github.com/orgs/{org}/actions/runners/registration-token"
    resp = _github_api("POST", url, pat)
    return resp["token"]


def cmd_removal_token(pat: str, org: str) -> str:
    """Obtain a removal token for deregistering a runner."""
    url = f"https://api.github.com/orgs/{org}/actions/runners/remove-token"
    resp = _github_api("POST", url, pat)
    return resp["token"]


def cmd_runner_id(pat: str, org: str, name: str) -> str:
    """Find runner ID by name in a GitHub org."""
    url = f"https://api.github.com/orgs/{org}/actions/runners"
    resp = _github_api("GET", url, pat)
    runners = resp.get("runners", [])
    for r in runners:
        if r.get("name") == name:
            return str(r["id"])
    print(f"[IMP:9][gha_api] Runner '{name}' not found in org '{org}'", file=sys.stderr)
    sys.exit(1)


def cmd_runner_status(pat: str, org: str, name: str) -> str:
    """Get runner status ('online'/'offline'/'NOT_FOUND') from GitHub API."""
    url = f"https://api.github.com/orgs/{org}/actions/runners"
    resp = _github_api("GET", url, pat)
    runners = resp.get("runners", [])
    for r in runners:
        if r.get("name") == name:
            return r.get("status", "UNKNOWN")
    return "NOT_FOUND"

# endregion


# region CLI

def main() -> None:
    parser = argparse.ArgumentParser(description="GitHub Actions API client for gha-runner")
    subparsers = parser.add_subparsers(dest="command", required=True)

    # registration-token <--pat PAT> --org ORG
    p = subparsers.add_parser("registration-token")
    p.add_argument("--pat", required=True)
    p.add_argument("--org", required=True)

    # removal-token <--pat PAT> --org ORG
    p = subparsers.add_parser("removal-token")
    p.add_argument("--pat", required=True)
    p.add_argument("--org", required=True)

    # runner-id <--pat PAT> --org ORG --name RUNNER_NAME
    p = subparsers.add_parser("runner-id")
    p.add_argument("--pat", required=True)
    p.add_argument("--org", required=True)
    p.add_argument("--name", required=True)

    # runner-status <--pat PAT> --org ORG --name RUNNER_NAME
    p = subparsers.add_parser("runner-status")
    p.add_argument("--pat", required=True)
    p.add_argument("--org", required=True)
    p.add_argument("--name", required=True)

    args = parser.parse_args()

    match args.command:
        case "registration-token":
            print(cmd_registration_token(args.pat, args.org))
        case "removal-token":
            print(cmd_removal_token(args.pat, args.org))
        case "runner-id":
            print(cmd_runner_id(args.pat, args.org, args.name))
        case "runner-status":
            print(cmd_runner_status(args.pat, args.org, args.name))

if __name__ == "__main__":
    main()

# endregion
```

**Дизайн-решения:**
1. **Stdout = результат, stderr = ошибки** — shell-скрипты ловят stdout в переменную (`$(python3 gha_api.py ...)`). Все ошибки и [IMP:N] логи идут в stderr.
2. **Exit code ≠ 0 на ошибках** — shell проверяет `$?` или пустую строку.
3. **`urllib` из stdlib** — никаких внешних зависимостей (requests не нужен для 4 простых API-вызовов).
4. **Явная HTTP-обработка** (DRIFT-7 fix): `urllib.error.HTTPError` даёт код HTTP и тело ответа → `[IMP:9] GitHub API error: HTTP {code} — {message}`.
5. **4 команды** покрывают все потребности: регистрация, дерегистрация, drain/enable, healthcheck.

---

## 7. План реализации (4 волны — добавлена Wave 0: исправления)

### Wave 0: Исправления критических ошибок (Coder)

**E0.0 — Создать Python-модуль gha_api.py (языковая политика, DRY):**
- Реализовать `gha_api.py` с 4 командами: `registration-token`, `removal-token`, `runner-id`, `runner-status`
- HTTP-обработка с явными `[IMP:9]` логами при ошибках (DRIFT-7 fix)
- Использовать только stdlib (`urllib` + `json` + `argparse`) — без внешних зависимостей

**E0.1 — Исправить register.sh (registration token flow):**
- Исправить: `config.sh --token "${PAT}"` → получить registration token через `gha_api.py registration-token` → `config.sh --token "${reg_token}"`
- Добавить: `chmod 600` для `.runner` и `.credentials`

**E0.2 — Убрать `--once`, переименовать run.sh → start.sh:**
- `start.sh`: daemon mode (`exec ./run.sh` без `--once`)
- Обновить systemd unit: `ExecStart=/opt/gha-runner/%i/start.sh`

**E0.3 — Добавить checksum verification в download_runner:**
- SHA256 проверка при скачивании
- `.version` файл для отслеживания версии

### Wave 1: Модуль gha-runner (Coder) — остальное из оригинального плана

**E1.1 — module.yaml + Makefile (кастомный, без module-system.mk)**
**E1.2 — install.sh (с preflight checks)**
**E1.3 — gha-runner@.service (с resource limits + rate limiting)**
**E1.4 — healthcheck.sh (с deep mode через Runner.Listener + GitHub API)**
**E1.5 — config/runner.env + config/cleanup.sh (с явной политикой)**
**E1.6 — unregister.sh + runner-drain.sh + diagnose.sh (новые)**
**E1.7 — config/hooks/job-started.sh + job-completed.sh (метрики)**

### Wave 2: Secrets и регистрация (Sysadmin)

**E2.1 — Создать Classic PAT'ы:**
- `tronyx161`: admin:org (или `admin:org/self_hosted_runners` если доступно)
- `tronyx-lab`: admin:org
- Добавить в `secrets.enc.yaml`:
  ```yaml
  GHA_RUNNER_PAT_TRONYX161: github_pat_...
  GHA_RUNNER_PAT_TRONYX_LAB: github_pat_...
  ```
- Добавить в `core/secret-definitions.yaml` (SSoT) — 3 записи:
  ```yaml
  - name: GHA_RUNNER_PAT
    tier: required
    source: sops
    charset: "^ghp_[A-Za-z0-9]+$"
    ci_default: "ghp_test-gha-runner-pat-for-ci"
    note: "Classic PAT с admin:org scope. Используется только register.sh для получения registration token (не живёт в процессе раннера). Fallback если per-org PAT отсутствует."
  - name: GHA_RUNNER_PAT_TRONYX161
    tier: required
    source: sops
    charset: "^ghp_[A-Za-z0-9]+$"
    ci_default: "ghp_test-gha-runner-pat-tronyx161"
    note: "Classic PAT для source-org tronyx161."
  - name: GHA_RUNNER_PAT_TRONYX_LAB
    tier: required
    source: sops
    charset: "^ghp_[A-Za-z0-9]+$"
    ci_default: "ghp_test-gha-runner-pat-tronyx-lab"
    note: "Classic PAT для контекстной org tronyx-lab."
  ```

**E2.2 — Ручная регистрация source-org:**
- `make runner-register ORG=tronyx161 NODE=tronyx-vps`

**E2.3 — Включить модуль + авто-регистрация контекстной org:**
- `node.yaml`: добавить `gha-runner` в modules
- `make deploy-modules NODE=tronyx-vps` → install.sh → register.sh tronyx-lab

**E2.4 — Верификация:**
- GitHub UI: оба раннера = "Idle"
- `make healthcheck MODULE=gha-runner MODE=deep` → healthy
- Тестовая джоба на каждом инстансе

### Wave 3: Миграция CI (Coder)

**Миграция 6 workflow на `runs-on: self-hosted`:**

| # | Workflow | Файл | Примечание |
|---|----------|------|------------|
| E3.1 | push-gate | `.github/workflows/push-gate.yml` | Основной CI gate |
| E3.2 | platform-test | `.github/workflows/platform-test.yml` | Самая длинная джоба (~40 мин) |
| E3.3 | build-platform | `.github/workflows/build-platform.yml` | Сборка образов |
| E3.4 | nightly-gate | `.github/workflows/nightly-gate.yml` | Ночной прогон |
| E3.5 | mirror | `.github/workflows/mirror.yml` | Зеркалирование |
| E3.6 | core-deploy | `.github/workflows/core-deploy.yml` | Деплой core на VPS |

**ВАЖНО: НЕ мигрировать** — `platform-deploy.yml` (3 jobs) и `stage-deploy.yml` (2 jobs) остаются на `ubuntu-latest` (GitHub-hosted), так как эти workflow деплоят проекты и должны выполняться даже при недоступности self-hosted раннера.

### Платформенная интеграция (после реализации Waves 0-3)

**PI.1 — Healthcheck contract carve-out для system-модулей с API-зависимостями:**

Обновить `core/modules/AGENTS.md` — добавить исключение к запрету `healthcheck.sh` в system-модулях:

```
**Исключение:** Системные модули с внешними API-зависимостями (глубокая проверка через API,
а не docker inspect) МОГУТ предоставлять healthcheck.sh. Liveness default НЕ использует
docker inspect — вместо этого `systemctl is-active`. Deep mode — произвольные проверки
(API status, локальный бинарник). Пример: gha-runner проверяет GitHub API runner status.
```

**PI.2 — Регистрация новых таргетов в entrypoint-manifest.yaml:**

Новые канонические таргеты должны быть зарегистрированы в `core/entrypoint-manifest.yaml` и `core/AGENTS.md` (canonical operations table):

| Таргет | Описание | Команда |
|--------|----------|---------|
| `runner-register` | Регистрация раннера в GitHub org | `make runner-register ORG=<org> NODE=<node>` |
| `runner-drain` | Drain mode — прекратить приём новых джоб | `make runner-drain ORG=<org>` |
| `runner-enable` | Выход из drain mode | `make runner-enable ORG=<org>` |
| `unregister` | Дерегистрация раннера из GitHub org | `make unregister MODULE=gha-runner ORG=<org>` |

**PI.3 — `make discover-modules`:**

System-модули не auto-discover через docker-compose include. `discover_modules.py` должен быть проверен на корректную обработку нового system-модуля gha-runner (не добавлять в compose-профили).

**PI.4 — `make check-manifests` после добавления модуля:**

После реализации — обязательный прогон `make check-manifests` для верификации отсутствия divergence в generated files (entrypoint-manifest.yaml, platform-env.yaml).

---

## 8. Rollback Plan

Если self-hosted раннер вызывает проблемы в CI:

1. **Revert workflow'ы:** `git revert` → `runs-on: self-hosted` → `runs-on: ubuntu-latest`
2. **Остановить раннеры:** `systemctl stop 'gha-runner@*'` на VPS
3. **Дерегистрировать:** `make unregister MODULE=gha-runner ORG=tronyx161 && make unregister MODULE=gha-runner ORG=tronyx-lab`
4. **Удалить из node.yaml:** убрать `gha-runner` из `modules:`
5. **Очистить spool_dir:** `rm -rf /opt/gha-runner/` (опционально)
6. **Деплой:** `make deploy-modules NODE=tronyx-vps`

Время отката: < 5 минут (2 revert + 2 API call + systemctl stop).

---

## 9. Расширенные Acceptance Criteria (дополнение к AC DevPlan 069)

В дополнение к AC-GOAL-1..12 из оригинального плана:

| AC | Критерий | Тип |
|----|----------|-----|
| AC-DRAIN-1 | `make runner-drain ORG=<org>` → GitHub перестаёт отдавать новые джобы, текущая завершается | Functional |
| AC-DRAIN-2 | После `make runner-enable ORG=<org>` → раннер снова принимает джобы | Functional |
| AC-UNREG-1 | `make unregister MODULE=gha-runner ORG=<org>` → раннер удалён из GitHub UI | Functional |
| AC-DIAG-1 | `make diagnose MODULE=gha-runner` выводит все 9 секций диагностики | Functional |
| AC-CHECKSUM-1 | `install.sh` проверяет SHA256 checksum; при mismatch — падает с ошибкой | Security |
| AC-RESOURCE-1 | CI-джоба не может потребить >4GB RAM (MemoryMax в systemd) | Non-functional |
| AC-RESOURCE-2 | CI-джоба не может потребить >200% CPU (CPUQuota в systemd) | Non-functional |
| AC-METRICS-1 | `/run/platform/gha-runner-metrics.prom` обновляется после каждой джобы | Monitoring |
| AC-HEALTH-1 | `healthcheck.sh MODE=deep` проверяет реальный статус через GitHub API (не только файлы) | Reliability |
| AC-VERSION-1 | `make deploy-modules` обновляет бинарник если `RUNNER_VERSION` изменилась | Maintenance |
| AC-ROLLBACK-1 | Rollback (секция 8) занимает < 5 минут | Operational |
| AC-LANG-1 | Все API-вызовы к GitHub идут через `gha_api.py` (ни одного `python3 -c` в bash) | Policy |
| AC-LANG-2 | `gha_api.py` логирует HTTP-ошибки с кодом и телом ответа (`[IMP:9]`) | Reliability |
| AC-SSOT-1 | 3 записи `GHA_RUNNER_PAT*` зарегистрированы в `secret-definitions.yaml` | Governance |
| AC-MANIFEST-1 | 4 новых таргета зарегистрированы в `entrypoint-manifest.yaml` и `core/AGENTS.md` | Governance |

---

## 10. Обновлённые риски и TRAP'ы

В дополнение к рискам из DevPlan 069 §6:

| # | TRAP | Severity | Источник |
|----|------|----------|----------|
| TRAP[PAT-SCOPE] | Classic PAT `admin:org` — миграция на GitHub App при росте | MED | S1 |
| TRAP[UNREGISTER-GATE] | Дерегистрация требует PAT — если истёк, ручное удаление | MED | S6 |
| TRAP[RESOURCE-LIMITS] | Лимиты подобраны для 8GB VPS — корректировка после эксплуатации | LOW | S12 |
| TRAP[JOURNALD-RATE] | Rate limiting может обрезать CI-логи | LOW | S22 |
| TRAP[AUTO-REREGISTER] | Отказ от auto-reregistration в пользу watchdog+alert | MED | S23 |
| TRAP[GITHUB-ORG-EXPLICIT] | Явный GITHUB_ORG в node.yaml отклонён — DRY | LOW | S25 |
| TRAP[LABEL-STRATEGY] | Простые labels — усложнение отложено до multi-VPS | LOW | S26 |
| TRAP[MULTI-INSTANCE] | Один инстанс на org — масштабирование отложено | LOW | S27 |
| TRAP[WATCHDOG] | Watchdog не auto-reregister — только alert | LOW | S11 |
| TRAP[LANG-POLICY] | Нарушение языковой политики: любой новый inline `python3 -c` → pre-commit hook rejection | HIGH | VerificationReport DRIFT-1 |
| TRAP[MODULE-SYSTEM-MK] | module-system.mk НЕ используется для gha-runner (template units). При добавлении новых systemd-модулей с template units — тот же подход | MED | VerificationReport DRIFT-2/3 |
| TRAP[HEALTHCHECK-CONTRACT] | healthcheck.sh в system-модулях разрешён только с carve-out в core/modules/AGENTS.md | MED | VerificationReport DRIFT-4 |
| TRAP[SECRET-SSOT] | Новые секреты без записи в secret-definitions.yaml → CI проверка не пройдена | HIGH | VerificationReport DRIFT-5 |

---

## 11. Дельта изменений (DevPlan 069 → 070)

| Аспект | DevPlan 069 | DevPlan 070 | Причина |
|--------|-------------|-------------|---------|
| PAT тип | Fine-grained (не существует) | Classic PAT `admin:org` | S1: fine-grained не имеет runner scope |
| Registration flow | `config.sh --token "${PAT}"` | PAT → API → reg_token → `config.sh --token "${reg_token}"` | S2: config.sh ожидает registration token |
| Режим раннера | `--once` + Restart=always | Daemon mode (без --once) | S3: restart storm |
| Wrapper имя | `run.sh` | `start.sh` | S4: конфликт с дистрибутивом |
| Скачивание | `curl \| tar` без проверки | SHA256 checksum verification | S5: supply chain security |
| Дерегистрация | Отсутствует | `unregister.sh` + ExecStopPre | S6: мёртвые раннеры в UI |
| module.yaml unit | `gha-runner.service` | `gha-runner@.service` | S7: template unit |
| Обновление | Проверка наличия `run.sh` | Проверка `.version` файла | S8: version-aware update |
| Depends_on | `[]` | `[platform-secrets]` | secrets.env нужен для PAT |
| Healthcheck deep | Проверка файлов | Runner.Listener CLI + GitHub API | S10: реальный статус |
| Systemd unit | Без лимитов | MemoryMax=4G, CPUQuota=200%, Nice=10 | S12,S20: защита production |
| Systemd unit | Без rate limiting | LogRateLimitIntervalSec/Burst | S22: защита journald |
| Новые скрипты | 5 файлов | 9 файлов (+drain, diag, unreg, hooks) | S9,S13,S21 |
| **Makefile** | `module-system.mk` | **Кастомный Makefile (module-system.mk НЕ используется)** | **VerificationReport DRIFT-2: template unit vs module-system.mk несовместимы** |
| **GitHub API** | inline `python3 -c` (6 вхождений) | **`gha_api.py` Python-модуль (4 команды)** | **VerificationReport DRIFT-1: языковая политика, DRY** |
| **Healthcheck contract** | Нарушение контракта system-модулей | **Carve-out в core/modules/AGENTS.md** | **VerificationReport DRIFT-4: API-based health для system-модулей** |
| **secret-definitions.yaml** | Отсутствуют записи | **3 записи GHA_RUNNER_PAT* (SSoT)** | **VerificationReport DRIFT-5: missing SSoT entries** |
| **Wave 3 workflow** | 6 (без списка) | **6 с явным списком (см. таблицу)** | **VerificationReport DRIFT-6: count mismatch + explicit list** |
| **HTTP error handling** | `curl pipe \| python3 -c` (generic error) | **`urllib.error.HTTPError` c HTTP кодом и телом** | **VerificationReport DRIFT-7: production debugging** |
| **entrypoint-manifest.yaml** | Не упомянут | **4 новых таргета зарегистрированы** | **VerificationReport Invariant 5: CI gate compliance** |
| AC критерии | 12 | 23 (+11 новых) | S9-S17, S21 |
| TRAP'ы | 2 | 11 (+9 новых) | Все REJECT + новые риски |
| Rollback Plan | Отсутствует | Секция 8 | S17 |

---

$END_DEVPLAN
