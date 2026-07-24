# DevPlan 069 — GitHub Actions Self-Hosted Runner как system-модуль ai-platform

$START_DEVPLAN

$ARTIFACT_CONTRACT
PURPOSE:               Добавить модуль `gha-runner` в ai-platform для запуска GitHub Actions self-hosted runner на production-сервере. Решает проблему исчерпания минут GitHub-hosted runners. Runner — system-модуль (systemd), опциональный (подключается через modules: в node.yaml), persistent (один долгоживущий процесс на все джобы).
DESCRIPTION:           Новый system-модуль `core/modules/gha-runner/` (install_type: system) по паттерну `platform-secrets`. Установка: скачивание бинарника actions/runner, регистрация через GitHub API (PAT с scope `manage_runners:org`), systemd template unit `gha-runner@.service` — один инстанс на GitHub-организацию. Конфигурация: runner name = `<node-name>-runner-<org>`, labels = `self-hosted,linux,x64,ai-platform`. Опциональность: модуль включается в `node.yaml#modules` — не обязателен для всех серверов. Source-org (tronyx161): ручная регистрация один раз (исключение). Контекстные орги: автоматическая регистрация при деплое модуля на ноду контекста. Мульти-org дизайн: один systemd template unit, несколько инстансов (`gha-runner@tronyx161.service`, `gha-runner@tronyx-lab.service`), каждый со своим PAT и spool_dir. CI-воркфлоу мигрируют с `runs-on: ubuntu-latest` на `runs-on: self-hosted`. Четыре фазы: (1) модуль + install.sh + systemd template unit, (2) PAT-менеджмент через secrets (один PAT на org), (3) миграция CI workflows на self-hosted, (4) авто-регистрация в контекстных org при promoute.
RATIONALE:             Кончились минуты GitHub-hosted runners. Production-сервер (tronyx-vps) простаивает большую часть времени — можно использовать его ресурсы. System-модуль (не Docker) выбран потому что: (а) CI-воркфлоу используют Docker (buildx, compose) — раннер на хосте имеет нативный доступ без DinD/socket-mount; (б) platform-secrets уже доказал жизнеспособность system-модуль контракта; (в) module-system.mk даёт install/status/restart/logs из коробки. Persistent (не ephemeral) выбран потому что: анализ 9 воркфлоу показал, что ни один не использует artifacts между джобами; все workflow делают cleanup в конце; Docker layer cache выживает через ghcr.io registry backend; низкая частота джоб (~10-20/день) не оправдывает сложность ephemeral регистрации.
ACCEPTANCE_CRITERIA:
  AC-GOAL-1: Модуль `gha-runner` проходит `make validate-modules` (module.yaml соответствует D5 schema)
  AC-GOAL-2: `make install MODULE=gha-runner` (через module-system.mk) устанавливает systemd unit и регистрирует раннер в GitHub org
  AC-GOAL-3: `make status MODULE=gha-runner` показывает `active (running)` + GitHub registration status ("Idle")
  AC-GOAL-4: `make restart MODULE=gha-runner` перезапускает раннер без потери регистрации
  AC-GOAL-5: `make logs MODULE=gha-runner` показывает journalctl логи раннера
  AC-GOAL-6: `make deploy-modules NODE=tronyx-vps` с `modules: [gha-runner]` в node.yaml успешно устанавливает раннер
  AC-GOAL-7: CI workflow на self-hosted раннере успешно выполняет: checkout → pre-commit → минимальный gate (dry-run)
  AC-GOAL-8: Раннер автоматически перезапускается при падении процесса (Restart=always в systemd unit)
  AC-GOAL-9: PAT хранится в `secrets.enc.yaml` (SOPS/age), НЕ в открытом виде в конфигурации
  AC-GOAL-10: Runner labels включают `ai-platform` — CI workflows могут таргетировать конкретно этот раннер
  AC-GOAL-11: Модуль НЕ ломает bootstrap/converge нод без `gha-runner` в modules: (opt-in)
  AC-GOAL-12: CI workflows обновлены: `runs-on` заменён на self-hosted labels где применимо; GitHub-hosted остаётся fallback'ом
IMPLEMENTS:            AGENTS.md инвариант 1 (единый фасад через make), инвариант 6 (bootstrap-node идемпотентность), system-модуль контракт (core/modules/AGENTS.md), языковая политика (shell как thin wrapper)
IMPACTS:
  ## Новые файлы (8)
  - core/modules/gha-runner/module.yaml — D5 metadata (install_type: system, severity: normal)
  - core/modules/gha-runner/gha-runner@.service — systemd template unit (%i = org name)
  - core/modules/gha-runner/Makefile — include module-system.mk
  - core/modules/gha-runner/install.sh — установка: определяет контекст → register.sh → enable systemd
  - core/modules/gha-runner/register.sh — регистрация в одной GitHub-org (вызывается из install.sh или вручную)
  - core/modules/gha-runner/healthcheck.sh — liveness (systemctl is-active всех инстансов) + deep (registration status)
  - core/modules/gha-runner/config/runner.env — дефолтные значения (RUNNER_VERSION, RUNNER_LABELS)
  - core/modules/gha-runner/config/cleanup.sh — очистка Docker между джобами
  ## Генерируемые (per-org, не коммитятся)
  - core/modules/gha-runner/config/runner@<org>.env — per-org конфиг (генерируется register.sh)
  ## Модифицируемые (3-5)
  - core/internal/bootstrap/deploy-modules.sh — возможно, не требует изменений (system-модуль dispatch уже есть)
  - .github/workflows/platform-test.yml — runs-on: self-hosted
  - .github/workflows/push-gate.yml — runs-on: self-hosted
  - .github/workflows/build-platform.yml — runs-on: self-hosted
  - .github/workflows/core-deploy.yml — runs-on: self-hosted (опционально)
REQUIRES:
  - Доступ к GitHub Organization settings (создание PAT с `manage_runners:org`)
  - `/var/run/docker.sock` доступ (раннер на хосте — уже есть)
  - `~/.ssh/id_rsa` доступ для CI deploy-джоб (раннер на хосте — уже есть)
  - AGE-encrypted secrets (platform-secrets уже обеспечивает)
  - systemd (Ubuntu 24.04 — есть)
  - Доступ к GitHub API из раннера (прямой или через SOCKS-прокси, если Tor используется)
$END_ARTIFACT_CONTRACT

---

## 0. SUPERPOSITION — Выбор архитектуры

### Краткий recap (полная версия в чате)

| # | Вариант | Score | Вердикт |
|---|---------|-------|---------|
| A | **System-модуль (systemd + host Docker)** | **9/10** | ✅ ВЫБРАН |
| B | Docker-модуль + /var/run/docker.sock mount | 7/10 | ❌ Security антипаттерн без выигрыша |
| C | Docker-модуль + DinD | 6/10 | ❌ Overhead, сложность, не нужна изоляция |
| D | Вне платформы — ручная установка | 3/10 | ❌ Нарушает инварианты |
| E | Ephemeral раннеры с авто-регистрацией | 5/10 | ❌ Overengineered для текущего масштаба |

### Обоснование выбора Option A

**Почему system (не Docker):**
1. CI-воркфлоу используют Docker интенсивно — `platform-test.yml` делает buildx, compose up/down, pre-pull. Раннер на хосте имеет нативный `/var/run/docker.sock` без прокладок.
2. CI-воркфлоу используют SSH (`core-deploy.yml` rsync, `deploy-project.yml` tar→forced-command). Раннер на хосте имеет доступ к `~/.ssh/id_rsa`.
3. `platform-secrets` — proof того, что system-модуль контракт работает в production.

**Почему persistent (не ephemeral):**
1. Анализ 9 воркфлоу показал: **ни один не использует artifacts** между джобами, все делают cleanup
2. Docker layer cache между job'ами идёт через ghcr.io registry backend — не зависит от локального состояния раннера
3. Actions cache (`.venv`, gitleaks) использует GitHub's `actions/cache@v6` — HTTP API, не локальная ФС
4. Низкая частота джоб (~10-20/день) не создаёт проблем с грязным состоянием
5. Ephemeral требует rotation токенов, auto-registration, cleanup на SIGTERM — неоправданная сложность

---

## 1. Дизайн модуля

### 1.0 Мульти-org архитектура (коллапс Q4+Q6)

**Проблема:** Один бинарник GitHub Actions runner может быть зарегистрирован только в **одной** GitHub-организации. VPS обслуживает source-org (`tronyx161`) + контекстные орги (`tronyx-lab`, ...). Нужно чтобы раннер на одном VPS принимал джобы из нескольких орг.

**Решение:** Systemd **template unit** — `gha-runner@.service`. Один unit-файл, несколько инстансов:

```
gha-runner@tronyx161.service    → раннер для source-org   → /opt/gha-runner/tronyx161/
gha-runner@tronyx-lab.service   → раннер для контекстной   → /opt/gha-runner/tronyx-lab/
gha-runner@<context>.service    → раннер для любой org     → /opt/gha-runner/<context>/
```

Каждый инстанс:
- Свой spool_dir (`/opt/gha-runner/<org>/`) — бинарник, конфигурация, credentials
- Свой PAT (`GHA_RUNNER_PAT_<ORG>`) — fine-grained token с scope на конкретную org
- Своё имя раннера (`<node>-runner-<org>`)
- Свои labels (`self-hosted,linux,x64,ai-platform,<org>`)

**Регистрация:**
- Source-org (`tronyx161`): **ручная**, один раз. Исключение из автоматизации — PAT для source-org создаётся вручную, регистрация через `make runner-register ORG=tronyx161`.
- Контекстные орги: **автоматическая** при деплое модуля. `install.sh` определяет контекст из `platform-env.yaml` → регистрирует раннер в соответствующей GitHub-org. PAT для контекстной org должен быть в `secrets.enc.yaml` этой ноды.

### 1.1 Структура модуля

```
core/modules/gha-runner/
├── module.yaml                 # D5-контракт
├── gha-runner@.service         # systemd template unit (%i = org name)
├── Makefile                    # include ../../templates/module-system.mk
├── install.sh                  # Установка: download + configure + register + enable (для текущего контекста)
├── healthcheck.sh              # liveness + deep (per-instance)
├── register.sh                 # Регистрация в одной GitHub-org (вызывается из install.sh или вручную)
└── config/
    ├── runner.env              # Дефолтные RUNNER_LABELS, переменные
    └── runner@.env             # Per-org переменные: RUNNER_NAME, GITHUB_ORG (опционально, install.sh генерирует)
```

### 1.2 module.yaml

```yaml
name: gha-runner
install_type: system
description: "GitHub Actions self-hosted runner — persistent, one runner per node"
systemd:
  unit: gha-runner.service
  required_by: []               # Раннер НЕ RequiredBy=docker.service — не критичен для production
depends_on: []
severity: normal                # Не блокирует node-update при ошибке
interfaces:
  - install
  - healthcheck
env_requires:
  - name: GHA_RUNNER_PAT
    type: secret
    required: true
spool_dir: /opt/gha-runner      # Бинарник + .runner + .credentials
```

**Ключевые решения:**
- `severity: normal` — падение раннера не блокирует `node-update`. Production-стек не зависит от CI.
- `required_by: []` — раннер не является предусловием для Docker (в отличие от `platform-secrets` где RequiredBy=docker.service)
- `spool_dir: /opt/gha-runner` — постоянное хранилище для бинарника и раннер-конфигурации (не tmpfs, нужно переживать ребуты)
- `env_requires: GHA_RUNNER_PAT` — Personal Access Token с `manage_runners:org` scope, расшифровывается из secrets.enc.yaml

### 1.3 systemd template unit (gha-runner@.service)

Использует systemd template (`%i` = имя инстанса = GitHub org name):

```ini
[Unit]
Description=GitHub Actions Self-Hosted Runner for org %i
After=network-online.target platform-secrets.service
Wants=network-online.target
Requires=platform-secrets.service

[Service]
Type=simple
User=platform
Group=platform
WorkingDirectory=/opt/gha-runner/%i
EnvironmentFile=/run/platform/secrets.env
EnvironmentFile=-/opt/platform/core/modules/gha-runner/config/runner@%i.env
EnvironmentFile=-/opt/platform/core/modules/gha-runner/config/runner.env
ExecStartPre=/bin/sh -c 'test -f /opt/gha-runner/%i/.runner || echo "[IMP:3][gha-runner@%i] Not configured — run register.sh first"'
ExecStart=/opt/gha-runner/%i/run.sh
ExecStopPost=/opt/gha-runner/%i/config/cleanup.sh
Restart=always
RestartSec=30
StartLimitInterval=600
StartLimitBurst=3
StandardOutput=journal
StandardError=journal
SyslogIdentifier=gha-runner-%i
PrivateTmp=yes
NoNewPrivileges=no             # Раннеру нужен Docker доступ

[Install]
WantedBy=multi-user.target
```

**Ключевые решения:**
- `After=platform-secrets.service` + `Requires=platform-secrets.service` — secrets (включая GHA_RUNNER_PAT) должны быть доступны до старта раннера
- `Restart=always` + `RestartSec=30` — авто-перезапуск при падении; 30s задержка чтобы GitHub успел обработать deregistration
- `NoNewPrivileges=no` — раннеру нужен Docker socket (нельзя полностью sandbox'ить)
- `User=platform` — не root; группа platform имеет docker-доступ
- `EnvironmentFile=/run/platform/secrets.env` — PAT читается из tmpfs (расшифрован platform-secrets)
- Запуск через `run.sh` — wrapper скрипт с pre/post хуками (обновление registration при необходимости)

### 1.4 run.sh (wrapper)

```bash
#!/usr/bin/env bash
# Wrapper для actions/runner — вызывается из systemd template unit
# %i = org name, передан через WorkingDirectory=/opt/gha-runner/%i
set -euo pipefail

RUNNER_DIR="${PWD}"  # WorkingDirectory уже установлен в /opt/gha-runner/<org>
ORG_NAME="$(basename "${RUNNER_DIR}")"

# Проверка: зарегистрирован ли раннер
if [ ! -f "${RUNNER_DIR}/.runner" ]; then
    echo "[IMP:9][gha-runner@${ORG_NAME}] Runner not registered. Run register.sh first." >&2
    exit 1
fi

# Имя переменной PAT: GHA_RUNNER_PAT или GHA_RUNNER_PAT_<ORG_UPPER>
PAT_VAR="GHA_RUNNER_PAT_$(echo "${ORG_NAME}" | tr '[:lower:]-' '[:upper:]_')"
PAT_VAR_FALLBACK="GHA_RUNNER_PAT"
PAT="${!PAT_VAR:-${!PAT_VAR_FALLBACK:-}}"

if [ -z "${PAT}" ]; then
    echo "[IMP:9][gha-runner@${ORG_NAME}] Neither ${PAT_VAR} nor ${PAT_VAR_FALLBACK} is set." >&2
    exit 1
fi

# Экспорт переменных для run.sh
export RUNNER_ALLOW_RUNASROOT=0
export ACTIONS_RUNNER_HOOK_JOB_STARTED="${RUNNER_DIR}/config/hooks/job-started.sh"
export ACTIONS_RUNNER_HOOK_JOB_COMPLETED="${RUNNER_DIR}/config/hooks/job-completed.sh"

# Запуск раннера с --once (одна джоба → завершение → systemd перезапускает)
cd "${RUNNER_DIR}"
exec ./run.sh --once
```

**Решение `--once`:** Раннер берёт **одну** джобу, завершается, systemd перезапускает его через `Restart=always`. Это даёт:
- Естественную очистку между джобами (новый процесс = чистое состояние)
- Без сложности ephemeral регистрации (registration сохраняется)
- GitHub сам обрабатывает «нет джоб» → раннер ждёт → systemd перезапускает

### 1.5 install.sh (вызывается из deploy-modules.sh → module-interface.sh install)

```bash
#!/usr/bin/env bash
# install.sh — установка GHA self-hosted runner для текущего контекста
# Вызывается из deploy-modules.sh при наличии gha-runner в modules: node.yaml
set -euo pipefail

# Определение целевой организации
determine_org() {
    # Приоритет: GITHUB_ORG из runner.env → CONTEXT из platform-env.yaml
    if [ -n "${GITHUB_ORG:-}" ]; then
        echo "${GITHUB_ORG}"
    elif [ -f /opt/platform/platform-env.yaml ]; then
        # Извлечение context из platform-env.yaml
        python3 -c "import yaml; print(yaml.safe_load(open('/opt/platform/platform-env.yaml'))['context'])" 2>/dev/null || echo ""
    else
        echo ""
    fi
}

# Основная логика:
main() {
    local org
    org="$(determine_org)"
    if [ -z "${org}" ]; then
        echo "[IMP:9][gha-runner] Cannot determine target GitHub org. Set GITHUB_ORG in runner.env or context in platform-env.yaml" >&2
        exit 1
    fi

    echo "[IMP:7][gha-runner] Installing runner for org: ${org}"

    # Вызов register.sh для этой org
    bash "${SCRIPT_DIR}/register.sh" "${org}"

    # Включение systemd template instance
    cp "${SCRIPT_DIR}/gha-runner@.service" /etc/systemd/system/
    systemctl daemon-reload
    systemctl enable "gha-runner@${org}.service"
    systemctl restart "gha-runner@${org}.service"
}
main "$@"
```

### 1.5b register.sh (регистрация в одной GitHub-org)

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

# Phase 1: Скачивание бинарника (идемпотентно)
download_runner() {
    local version="${RUNNER_VERSION}"
    local url="https://github.com/actions/runner/releases/download/v${version}/actions-runner-linux-x64-${version}.tar.gz"
    mkdir -p "${RUNNER_DIR}"
    if [ ! -f "${RUNNER_DIR}/run.sh" ]; then
        curl -sL "${url}" | tar xz -C "${RUNNER_DIR}"
        echo "[IMP:7][gha-runner@${ORG}] Runner binary v${version} downloaded"
    else
        echo "[IMP:5][gha-runner@${ORG}] Runner binary already present"
    fi
}

# Phase 2: Конфигурация и регистрация (config.sh сам проверяет уже зарегистрирован или нет)
configure_runner() {
    cd "${RUNNER_DIR}"
    local labels="self-hosted,linux,x64,ai-platform,${ORG}"
    local name="${NODE_NAME}-runner-${ORG}"

    if [ -f "${RUNNER_DIR}/.runner" ]; then
        echo "[IMP:5][gha-runner@${ORG}] Already registered as '${name}'"
        return 0
    fi

    ./config.sh \
        --url "https://github.com/${ORG}" \
        --token "${PAT}" \
        --name "${name}" \
        --labels "${labels}" \
        --unattended \
        --replace  # Заменяет существующий раннер с тем же именем (перерегистрация)

    echo "[IMP:9][gha-runner@${ORG}] Registered as '${name}' with labels '${labels}'"
}

# Phase 3: Создание per-org runner.env (если не существует)
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

### 1.6 healthcheck.sh

```bash
#!/usr/bin/env bash
set -euo pipefail
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
MODE="${1:-}"

# Определение активных инстансов (какие org обслуживаются на этой ноде)
ACTIVE_ORGS=$(systemctl list-units --type=service --state=active 'gha-runner@*.service' --no-legend 2>/dev/null | awk '{print $1}' | sed 's/gha-runner@\(.*\)\.service/\1/')

if [ -z "${ACTIVE_ORGS}" ]; then
    echo "[IMP:7][gha-runner] No active runner instances" >&2
    exit 1  # unhealthy — ни один раннер не запущен
fi

ALL_HEALTHY=true
for org in ${ACTIVE_ORGS}; do
    RUNNER_DIR="/opt/gha-runner/${org}"

    # Liveness: systemd unit active
    if ! systemctl is-active --quiet "gha-runner@${org}.service"; then
        echo "[IMP:7][gha-runner@${org}] systemd unit not active" >&2
        ALL_HEALTHY=false
        continue
    fi

    # Deep: проверка registration
    if [ "$MODE" = "deep" ]; then
        if [ ! -f "${RUNNER_DIR}/.runner" ]; then
            echo "[IMP:9][gha-runner@${org}] .runner file missing — registration lost" >&2
            ALL_HEALTHY=false
            continue
        fi
        if [ ! -f "${RUNNER_DIR}/.credentials" ]; then
            echo "[IMP:9][gha-runner@${org}] .credentials file missing" >&2
            ALL_HEALTHY=false
            continue
        fi
        echo "[IMP:7][gha-runner@${org}] Registered and running"
    else
        echo "[IMP:7][gha-runner@${org}] Active"
    fi
done

if [ "${ALL_HEALTHY}" = true ]; then
    exit 0
else
    exit 1
fi
```

### 1.7 config/runner.env (дефолтные значения)

```bash
# Дефолтные значения для всех инстансов gha-runner@*
# Per-org значения переопределяются в config/runner@<org>.env

# Версия бинарника actions/runner
RUNNER_VERSION=2.322.0
# Базовые labels (org-специфичная метка добавляется в register.sh)
RUNNER_LABELS=self-hosted,linux,x64,ai-platform
```

### 1.8 config/runner@<org>.env (per-org, генерируется register.sh)

```bash
# Generated by register.sh — per-org runner config for tronyx-lab
RUNNER_NAME=tronyx-vps-runner-tronyx-lab
RUNNER_LABELS=self-hosted,linux,x64,ai-platform,tronyx-lab
GITHUB_ORG=tronyx-lab
```

---

## 2. PAT-менеджмент и безопасность

### 2.1 Где хранятся PAT'ы

Каждая GitHub-организация требует свой PAT (fine-grained token привязан к одной org). Нейминг: `GHA_RUNNER_PAT_<ORG_UPPER>` или `GHA_RUNNER_PAT` (fallback).

```
User → GitHub Settings → Fine-grained PAT:
  PAT_1: manage_runners:org для tronyx161
  PAT_2: manage_runners:org для tronyx-lab
  ... для каждой контекстной org
  ↓ копирует в secrets.enc.yaml
AGE-encrypted secrets.enc.yaml на машине оператора
  ↓ make secrets-unlock NODE=tronyx-vps
SCP → /opt/platform/secrets/secrets.enc.yaml (encrypted at rest)
  ↓ platform-secrets.service (при загрузке)
/run/platform/secrets.env (tmpfs):
  GHA_RUNNER_PAT_TRONYX161=github_pat_...
  GHA_RUNNER_PAT_TRONYX_LAB=github_pat_...
  ↓ EnvironmentFile в gha-runner@.service
run.sh извлекает PAT_VAR по имени org
```

### 2.2 Source-org vs контекстные орги

| Аспект | Source-org (tronyx161) | Контекстные орги (tronyx-lab, ...) |
|--------|------------------------|-------------------------------------|
| Регистрация раннера | **Ручная**, один раз. `make runner-register ORG=tronyx161` | **Автоматическая** при деплое модуля (`install.sh` → `register.sh`) |
| PAT создание | Вручную в GitHub Settings → `secrets.enc.yaml` | Вручную в GitHub Settings → `secrets.enc.yaml` |
| PAT имя в secrets | `GHA_RUNNER_PAT_TRONYX161` или `GHA_RUNNER_PAT` (fallback) | `GHA_RUNNER_PAT_TRONYX_LAB` (spec-org) |
| Инстанс systemd | `gha-runner@tronyx161.service` | `gha-runner@tronyx-lab.service` |
| Когда регистрируется | Однократно, вручную | При каждом `make deploy-modules` (идемпотентно) |

### 2.3 Scope PAT

| Scope | Значение | Зачем |
|-------|---------|-------|
| `manage_runners:org` | `tronyx161` | Регистрация/дерегистрация раннеров в source-org |
| Истечение | 90 дней (max для fine-grained) | Ротация через напоминание |

**Fine-grained PAT** (не classic) — минимальные права, привязан к конкретной org.

### 2.3 Ротация токена

При истечении PAT (каждые 90 дней):
1. Создать новый PAT в GitHub Settings
2. Обновить `GHA_RUNNER_PAT` в `secrets.enc.yaml`
3. `make secrets-unlock && make deploy-modules NODE=tronyx-vps MODULE=gha-runner`
4. systemd перезапустит раннер с новым токеном (изменение `run/platform/secrets.env` не триггерит перезапуск — нужно `make restart MODULE=gha-runner`)

**Улучшение (future):** `PathModified=/run/platform/secrets.env` в systemd unit — авто-рестарт при изменении secrets. Но это рискованно (любое изменение secrets.env = перезапуск раннера). Пока — ручной `make restart`.

---

## 3. Миграция CI Workflows

### 3.1 Стратегия

**Фаза 1 (этот DevPlan):** Быстрые workflow мигрируют на self-hosted. GitHub-hosted остаётся fallback'ом.

**Фаза 2 (отдельный DevPlan):** Все workflow на self-hosted; GitHub-hosted только для matrix-сборок (если понадобятся).

### 3.2 Какие workflow мигрируют

| Workflow | Текущий `runs-on` | Новый `runs-on` | Обоснование |
|----------|-------------------|-----------------|-------------|
| **push-gate.yml** (quick-gate) | `ubuntu-latest` | `self-hosted` | Быстрый гейт (3-5 min), не требует GitHub-hosted специфики |
| **platform-test.yml** | `ubuntu-latest` | `self-hosted` | Основной CI — тесты, lint, gate. Docker-интенсивный — быстрее на своём железе |
| **build-platform.yml** | `ubuntu-latest` | `self-hosted` | Docker build — быстрее на своём железе (нет загрузки образа) |
| **nightly-gate.yml** | `ubuntu-latest` | `self-hosted` | Ночной полный gate |
| **core-deploy.yml** | `ubuntu-latest` | `self-hosted` или `ubuntu-latest` | Деплой на VPS — минимальная разница; можно оставить GitHub-hosted для разнесения рисков |
| **deploy-project.yml** | `ubuntu-latest` | `self-hosted` или `ubuntu-latest` | Аналогично core-deploy |
| **mirror.yml** | `ubuntu-latest` | `self-hosted` | Простой git push — неважно где |
| **platform-deploy.yml** | `ubuntu-latest` | `ubuntu-latest` | Reusable, legacy — не трогаем |
| **stage-deploy.yml** | `ubuntu-latest` | `ubuntu-latest` | Staging — не трогаем |

**Правило безопасности:** Deploy-воркфлоу (`core-deploy.yml`, `deploy-project.yml`) могут остаться на GitHub-hosted для разнесения рисков (компрометация раннера ≠ компрометация деплой-канала). Но это удваивает расход минут. Решение: мигрируем на self-hosted, но с дополнительным аудитом.

### 3.3 Label strategy

```yaml
# Точный таргетинг (только наш раннер)
runs-on: [self-hosted, ai-platform]

# Широкий таргетинг (любой self-hosted раннер в org)
runs-on: self-hosted

# Fallback на GitHub-hosted (если self-hosted недоступен)
runs-on: [self-hosted, ubuntu-latest]  # Не работает — GitHub не поддерживает fallback labels
```

**Выбрано:** `runs-on: self-hosted` для всех workflow. Этого достаточно — в org только один self-hosted раннер. Если появятся другие — добавим `ai-platform` label для дифференциации.

### 3.4 Container actions на self-hosted

Некоторые composite actions используют `runs-on: ubuntu-latest` внутри себя (например, `docker-build-cache` вызывает `docker/build-push-action` который работает на любом раннере с Docker). Нужно проверить что все actions совместимы с self-hosted.

**Известные ограничения self-hosted:**
- `actions/cache@v6` → OK (использует HTTP API)
- `docker/build-push-action@v7` → OK (требует Docker, который есть на VPS)
- `actions/setup-python@v6` → OK (устанавливает Python, если нет)
- `appleboy/ssh-action` → OK
- `actions/checkout@v4` → OK

---

## 4. План реализации (3 волны)

### Wave 1: Модуль gha-runner (Coder)

**E1 — module.yaml + Makefile:**
- Файлы: `module.yaml`, `Makefile`
- `module.yaml` соответствует D5 schema (валидация: `make validate-modules`)

**E2 — install.sh:**
- Скачивание бинарника `actions/runner` (версия параметризуется)
- Проверка checksum
- Конфигурация через `config.sh --unattended`
- Регистрация в GitHub org через PAT
- Создание spool_dir `/opt/gha-runner`

**E3 — gha-runner.service + run.sh:**
- systemd unit с Restart=always
- `run.sh` wrapper с `--once` флагом
- Хуки `job-started.sh` + `job-completed.sh` (logging, cleanup)
- EnvironmentFile: `/run/platform/secrets.env` + `config/runner.env`

**E4 — healthcheck.sh:**
- Liveness: `systemctl is-active gha-runner`
- Deep: проверка `.runner` и `.credentials` файлов

**E5 — config/runner.env:**
- Дефолтные значения (RUNNER_NAME, LABELS, GITHUB_ORG)
- Документация всех переменных

**E6 — config/cleanup.sh:**
- Очистка между джобами: dangling Docker images, остановленные контейнеры CI, временные файлы
- Вызывается из ExecStopPost в systemd unit

### Wave 2: Secrets, регистрация и включение (Sysadmin)

**E7 — Создать PAT'ы и добавить в secrets.enc.yaml:**
- Создать fine-grained PAT для `tronyx161` (scope: `manage_runners:org`)
- Создать fine-grained PAT для `tronyx-lab` (scope: `manage_runners:org`)
- (Опционально: PAT для других контекстных орг)
- Добавить в `secrets.enc.yaml` для ноды `tronyx-vps`:
  ```yaml
  GHA_RUNNER_PAT_TRONYX161: github_pat_...
  GHA_RUNNER_PAT_TRONYX_LAB: github_pat_...
  ```
- Зашифровать через age

**E8 — Ручная регистрация в source-org (исключение):**
- `make runner-register ORG=tronyx161 NODE=tronyx-vps` (или через ssh)
- Верификация: GitHub UI → tronyx161 → Settings → Actions → Runners → `tronyx-vps-runner-tronyx161` = "Idle"

**E9 — Включить модуль в node.yaml + деплой (контекстная авто-регистрация):**
- В `node-configs/tronyx-vps/node.yaml` добавить `gha-runner` в `modules:`
- `make bootstrap-node NODE=tronyx-vps` (или `make deploy-modules NODE=tronyx-vps MODULE=gha-runner`)
  → `deploy-modules.sh` вызывает `invoke_module_interface gha-runner install`
  → `install.sh` определяет контекст = `tronyx-lab`
  → `register.sh tronyx-lab` регистрирует раннер
  → `systemctl enable gha-runner@tronyx-lab.service`
- ⚠️ Если на ноде только source-org (без контекста), `install.sh` должен fallback на `GITHUB_ORG` из runner.env или пропустить авто-регистрацию (выполнена в E8)

**E10 — Верификация регистрации и healthcheck:**
- GitHub UI → tronyx-lab → Settings → Actions → Runners → `tronyx-vps-runner-tronyx-lab` = "Idle"
- `make healthcheck MODULE=gha-runner MODE=deep` → оба инстанса healthy
- `systemctl status 'gha-runner@*'` → оба running
- Ручной запуск тестовой джобы на каждом инстансе

### Wave 3: Миграция CI Workflows (Coder)

**E11 — Миграция push-gate.yml:**
- `runs-on: ubuntu-latest` → `runs-on: self-hosted`
- Тестовый push → проверить что джоба выполнилась на self-hosted раннере

**E12 — Миграция platform-test.yml:**
- `runs-on: ubuntu-latest` → `runs-on: self-hosted`
- Проверить Docker buildx работает (доступ к Docker daemon)
- Проверить compose up/down на VPS
- Проверить что `actions/cache@v6` работает (cache scope изменился: новый runner)

**E13 — Миграция build-platform.yml:**
- `runs-on: ubuntu-latest` → `runs-on: self-hosted`
- Проверить push в ghcr.io (доступ к registry credentials)
- Проверить smoke test (запуск контейнера локально)

**E14 — Миграция остальных:**
- core-deploy.yml → `runs-on: self-hosted` (rsync/scp на localhost — быстрее)
- nightly-gate.yml → `runs-on: self-hosted`
- mirror.yml → `runs-on: self-hosted`
- deploy-project.yml (reusable) → не меняем сам файл; проектные workflow, которые его вызывают, мигрируют на `self-hosted`

---

## 5. $PARALLEL_GROUPS

```
Wave 1: [E1, E2] → E3 → [E4, E5, E6]  (E1+E2 параллельно; E4+E5+E6 параллельно после E3)
Wave 2: E7 → [E8, E9] → E10            (E8+E9 параллельно после E7; E10 после)
Wave 3: [E11, E12] → [E13, E14]        (E11+E12 параллельно; E13+E14 параллельно после)
```

---

## 6. Риски и TRAP'ы

### 6.1 Security

| Риск | Вероятность | Impact | Mitigation |
|------|-------------|--------|------------|
| Компрометация раннера → доступ к production Docker | Low | CRITICAL | Раннер под `User=platform` (не root). Docker socket доступ неизбежен (CI нужен Docker). Mitigation: аудит логов, изоляция networks |
| PAT утечка через логи | Low | HIGH | PAT только в tmpfs; логи фильтруются (GHA_RUNNER_PAT не должен появляться в journalctl) |
| Self-hosted раннер принимает джобы из публичных форков | Medium | HIGH | Настройка: Settings → Actions → Runners → "Require approval for all outside collaborators" |

### 6.2 Reliability

| Риск | Вероятность | Impact | Mitigation |
|------|-------------|--------|------------|
| Раннер падает во время джобы | Medium | MEDIUM | Restart=always в systemd; джоба перезапустится на любом доступном раннере (если есть GitHub-hosted fallback) |
| Ресурсный contention с production | Medium | MEDIUM | Docker resource limits (уже заданы в base.yml). Раннер — лёгкий процесс (~200MB RAM когда idle) |
| Обновление бинарника runner | Low | LOW | `install.sh` проверяет версию и обновляет при `make deploy-modules` (идемпотентно) |

### 6.3 Workflow-специфичные

| Риск | Вероятность | Impact | Mitigation |
|------|-------------|--------|------------|
| `actions/cache@v6` не находит кеш на self-hosted | Medium | MEDIUM | Cache scope изменится (runner.os = Linux, но cache key другой). Первый ран после миграции — cache miss, последующие — hit |
| macOS/Windows специфичные шаги | None | — | Все workflow — Linux-only (ubuntu-latest → self-hosted Linux) |
| Tor прокси (SOCKS5) блокирует GitHub API | Low | LOW | Раннеру нужен доступ к api.github.com. Tor прокси уже настроен для hermes-agent. Если раннер за прокси — добавить `HTTPS_PROXY` в runner.env |

### ⚠️ TRAP[GH-API] · 2026-07-24 · MED · Self-hosted runner на VPS за NAT — inbound connections
· Раннер инициирует **outbound** WebSocket к GitHub (не inbound). NAT не проблема.
· GitHub Actions использует long-polling HTTP/WebSocket: раннер → api.github.com → получает джобы.
· Единственное требование: доступ к `https://api.github.com` и `https://*.actions.githubusercontent.com`.
· Rev: если GitHub перейдёт на inbound-модель — нужно будет добавить firewall rule.

### ⚠️ TRAP[PAT-EXPIRY] · 2026-07-24 · MED · PAT истекает каждые 90 дней
· Fine-grained PAT имеет максимальный срок 90 дней (classic — до 1 года, но с более широкими правами).
· При истечении: раннер теряет регистрацию при следующем перезапуске; systemd пытается 3 раза (StartLimitBurst=3), затем останавливается.
· Mitigation: cron-напоминание за 7 дней до истечения (можно через hermes-agent Telegram notify).
· Rev: если частота ротации слишком высокая → перейти на GitHub App (не имеет срока истечения, но сложнее в настройке).

---

## 7. Решения по коллапсу суперпозиции (опрос пользователя)

| # | Вопрос | Решение |
|---|--------|---------|
| Q1 | Прокси (Tor) для GitHub API? | **Напрямую** — GitHub API легитимный трафик, не требует анонимизации |
| Q2 | `--once` или `--daemon`? | **`--once`** — одна джоба → systemd перезапускает, чистое состояние между джобами |
| Q3 | Org-level или repo-level регистрация? | **Org-level** — один раннер для всех репозиториев в org |
| Q4 | Деплой-воркфлоу мигрировать? | **Да, всё на self-hosted** — максимальная экономия минут |
| Q5 | Нужен `make runner-reregister`? | **Да** — отдельный таргет `register.sh <org>` (ручной вызов для source-org) |
| Q6 | Source vs контекстные орги? | **Source — ручная регистрация (исключение). Контекстные — автоматическая при деплое модуля.** Реализовано через systemd template unit `gha-runner@.service` + `register.sh <org>` |

---

$END_DEVPLAN
