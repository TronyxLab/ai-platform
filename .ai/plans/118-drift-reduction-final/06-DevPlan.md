# 06-DevPlan — Бриф E: Shell→Python финальная миграция

<!-- $ARTIFACT_CONTRACT
PURPOSE:          Миграция оставшихся shell-скриптов с бизнес-логикой на Python (Strangler-Fig финал) — ~1600 LOC бизнес-логики.
DESCRIPTION:      12 задач: E1 install-tor-proxy (D19), E2 install-docker, E3 firewall, E4 modules-healthcheck, E5 tor-proxy-healthcheck,
                  E6 scripts-audit, E7 platform-secrets/install, E8 hermes-images, E9 upload-s3 merge, E10 notify-hook merge, E11 adopt-project, E12 issue-cert упрощение.
RATIONALE:        Языковая политика AGENTS.md: новый код — Python, bash — тонкий фасад. Оставшиеся 8 файлов (MIGRATE-класс аудита) содержат
                  тестируемую бизнес-логику (парсинг, деградации, healthcheck-критерии, docker/apt-оркестрация). issue-cert.sh остаётся shell (TRAP 052/117-D4).
ACCEPTANCE_CRITERIA:
  - AC-E1: install-tor-proxy — transport-парсинг/деградация в Python; unit-тесты написаны ПЕРЕД миграцией; shell — apt/systemd оркестрация.
  - AC-E2..E8: мигрированные скрипты имеют Python-модуль + unit-тесты + тонкий shell-фасад (или прямой вызов python3 -m из lifecycle/Makefile).
  - AC-E9/E10: upload-s3/notify-hook логика слита в upload.py/telegram_notifier (0 дублей).
  - AC-E11: adopt-project — grep-YAML-парсинг в project_adopter; casing-валидация в Python.
  - AC-E12: issue-cert.sh использует node_yaml --get-many/--format lines; 0 grep|cut.
  - AC-E13: все миграции — БЕЗ нового функционала; поведение идентично (тесты подтверждают).
  - AC-E14: gate MODE=fast, check-manifests, ruff — зелёные.
IMPLEMENTS:       118 01-Brief задачи E1-E12.
IMPACTS:          core/internal/bootstrap/*.sh, core/internal/healthcheck/*.sh, core/internal/scripts-audit.sh, core/internal/build/hermes-images.sh,
                  core/modules/platform-secrets/install.sh, core/modules/backup-cron/scripts/upload-s3.sh, core/internal/notify/notify-hook.sh,
                  core/internal/scaffold/adopt-project.sh, core/internal/bootstrap/issue-cert.sh, makefiles/, entrypoint-manifest.yaml, tests/.
REQUIRES:         118 01-Brief; E1 — условие из мега-DevPlan D19 (тесты ПЕРЕД миграцией); E7 — системный модуль install, покрыть тестом до/после.
-->

---

## 1. Технический анализ и решения

### Общее правило (Strangler, языковая политика)

Каждая миграция:
1. **Тесты ПЕРЕД** — Python-логика извлекается ТОЛЬКО если может быть протестирована; при отсутствии unit-тестов и высоком риске — ОТЛОЖИТЬ на 119 (условие D19 мега-DevPlan).
2. Shell остаётся тонким фасадом (<150 LOC) или удаляется (прямой вызов `python3 -m` из lifecycle/Makefile).
3. Поведение идентично (без нового функционала); фиксируется тестом.

### E1 (MED) — install-tor-proxy.sh (422 LOC) [D19]

**Факты:** бизнес-логика: `install_packages` (71-108, webtunnel degradation chain, 4 вложенных if, фильтрация массива), `write_torrc` (147-196, динамический ClientTransportPlugin, ассоциативный массив TRANSPORT_BIN, дедупликация), verify_tor_circuit (60s retry), privoxy sed-edit, iptables catch-all, cron install. >3 if-веток бизнес-логики (Tier-1) + >150 LOC (Tier-2).

**Решение (по мега-DevPlan D19):** вынести парсинг Bridge-строк и деградацию транспортов в `bootstrap/tor_transport.py`. Shell — apt/systemd-оркестрация.

**Условие:** unit-тесты на transport-парсинг пишутся ПЕРЕД миграцией; если отсутствуют и рискованно — ОТЛОЖИТЬ на 119 (зафиксировать DEBT).

**Тест:** test_tor_transport (parsing, degradation, dedup).

**Риск:** MED (нетестируемый код → регрессия; условие-гейт).

### E2 (MED) — install-docker.sh (218 LOC)

**Факты:** apt-репозиторий Docker, выбор пакетов, daemon.json merge (через docker_daemon.py частично), systemd override, security-verify 2375/2376.

**Решение:** `bootstrap/docker_installer.py` — выбор пакетов + daemon-merge + verify (subprocess-оркестрация apt/systemd). Shell → тонкий фасад или прямой вызов из phases.py φ1.

**Тест:** unit-выбор пакетов, verify-логика.

**Риск:** MED (системные команды; тестируется на тестовом сервере).

### E3 (MED) — firewall.sh (167 LOC)

**Факты:** declarative ufw (reset→defaults→baseline 22/80/443→deny 5432→extra_ports), валидация портов 1-65535, запрет 2375/2376, verify ufw status.

**Решение:** `bootstrap/firewall.py` — декларативная политика + валидация (subprocess ufw). Порты из node.yaml (NodeYaml firewall-поддомен).

**Тест:** unit-валидация портов, политика.

**Риск:** LOW-MED.

### E4 (MED) — modules-healthcheck.sh (127 LOC)

**Факты:** итерация module.yaml (grep install_type), docker inspect State.Restarting/RestartCount (restart-loop, threshold >5), invoke_module_interface dispatch.

**Решение:** `healthcheck/modules_healthcheck.py` — restart-loop детекция + dispatch через shared/module_interface (C5). Healthcheck-критерий по канону (см. shared/docker_compose.healthcheck_poll).

**Тест:** unit restart-loop (threshold), критерий.

**Риск:** MED (канон healthcheck-критерия — не разойтись с docker_compose).

### E5 (MED) — tor-proxy-healthcheck.sh (121 LOC)

**Факты:** 3-stage проверка (Tor SOCKS5 curl, Privoxy forward, Telegram getMe), таймауты MAX_TIME=30, sourcing secrets.env.

**Решение:** `healthcheck/tor_proxy_check.py` — 3-stage с канон-таймаутами (shared/timeouts); telegram getMe делегирован в shared/telegram_notifier.

**Тест:** unit-3-stage (mock curl).

**Риск:** LOW-MED.

### E6 (MED) — scripts-audit.sh (97 LOC)

**Факты:** find shebang → exception patterns → grep manifest registration → отчёт.

**Решение:** `scripts/scripts_audit.py` — yaml-парсер entrypoint-manifest вместо grep; отчёт в том же формате. Сам скрипт попадает в исключения.

**Тест:** unit-аудит (tmp fixtures).

**Риск:** LOW.

### E7 (MED) — platform-secrets/install.sh (225 LOC)

**Факты:** age-key создание/миграция (KEY=VALUE формат), permission auto-fix, secrets-enc symlink-fallback, systemd unit install, ensure_platform_dirs (setgid 2775).

**Решение:** `modules/platform_secrets/installer.py` — file-менеджмент + systemd (subprocess). Замена вызова `_invoke_module_interface install` на Python.

**Тест:** unit-файловые операции (tmp_path), systemd unit content.

**Риск:** MED (системный модуль install — влияет на bootstrap; покрыть тестом до/после).

### E8 (LOW) — hermes-images.sh (77 LOC)

**Факты:** docker build L1/L2, --platform linux/amd64 (QEMU), BuildKit cache, CONTEXT guard.

**Решение:** `build/hermes_images.py` — docker build через subprocess (docker_orchestrator-стиль). CONTEXT guard в Python.

**Тест:** unit-guard, build-команда (mock docker).

**Риск:** LOW.

### E9 (LOW) — upload-s3.sh merge (84 LOC)

**Факты:** S3 credential validation, stat -c%s, env-пропуск, spool rm после успеха. Python-аналог upload.py (boto3, retries) существует.

**Решение:** валидация + размер + rm переносятся в `upload.py` (расширить модуль); upload-s3.sh → тонкий фасад `exec python3 upload.py`.

**Тест:** unit-upload.py (валидация, spool cleanup).

**Риск:** LOW.

### E10 (LOW) — notify-hook.sh merge (108 LOC)

**Факты:** severity→CHAT_ID mapping (critical/warning/info), message formatting, secrets sourcing, always-exit-0. shared/telegram_notifier.py уже делает send.

**Решение:** severity-mapping + форматирование → telegram_notifier (расширить); notify-hook.sh → тонкий фасад или удалить (прямой вызов python3 -m).

**Тест:** unit-telegram_notifier (mapping).

**Риск:** LOW.

### E11 (LOW) — adopt-project.sh grep-YAML (89 LOC)

**Факты:** grep-YAML auto-detection (target_node/domain/context из ai-platform.yaml), org derivation из пути, casing-validation context. TRAP[DEBT] 2026-07-26.

**Решение:** парсинг → `shared/project_yaml.py` (общий читатель ai-platform.yaml — кандидат из аудита монолитов) или project_adopter; casing-валидация в Python.

**Тест:** unit-парсинг.

**Риск:** LOW.

### E12 (LOW) — issue-cert.sh упрощение (D18)

**Факты:** issue-cert.sh:600-619 пере-парсит вывод `node_yaml --domain-config` через `grep '^platform_domain:' | cut -d: -f2-`.

**Решение:** `node_yaml --format lines` (паттерн deploy.sh:156) или `--get-many`.

**Тест:** существующий test_nginx_acme.

**Риск:** LOW.

---

## 2. Порядок выполнения

```
E12 (issue-cert упрощение)  ← дёшево, независимо
   │
E6 → E8 → E9 → E10 → E11    ← малые миграции (независимы)
   │
E3 (firewall) → E4 → E5     ← healthcheck/ufw (средние)
   │
E2 (install-docker) → E7 (platform-secrets) ← системные, тест до/после
   │
E1 (install-tor-proxy)      ← последний, условие тесты-ПЕРЕД (иначе 119)
```

## 3. Оценки

| Метрика | Значение |
|---------|----------|
| Задач | 12 |
| LOC | −1600 бизнес-логики из shell (shell остаётся фасадом) |
| Рискованных | E1 (условие-гейт), E2/E7 (системные команды) |
| Остаётся shell по TRAP | issue-cert.sh (714), build-ssh-cmd.sh (122), модульные healthcheck (контракт), s6-контейнерные скрипты |

## $END

Открытые вопросы:
1. **E1** — наличие unit-тестов на transport-парсинг; при отсутствии — DEBT-запись и перенос на 119.
2. **E2/E7** — тестируемость системных команд: насколько жизнеспособен unit-тест без реального сервера (mock-субпроцессы).
3. **E11** — объём общего `shared/project_yaml.py`: связь с монолитным анализом vhost_renderer (18 парсеров ai-platform.yaml) — вынести полный читатель или точечный фикс.
