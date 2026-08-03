# 134-security-hardening — 01-DevPlan.md

$START_DEVPLAN

$ARTIFACT_CONTRACT
PURPOSE:               Закрыть два главных security-гэпа платформы для эры автоматизированных ИИ-атак (эксплуатация известных CVE + supply chain): (1) автоматические security-обновления ОС через unattended-upgrades (сейчас apt используется только в bootstrap-цепочке φ1, автопатчинга НЕТ — проверено grep'ом, 0 упоминаний unattended-upgrades в репозитории); (2) проверка security-постурa ноды как канонический таргет `make check-security NODE=<name>` (сейчас check-suite.yaml — только code-quality чеки, на ноде security-проверок нет).
DESCRIPTION:           3 волны: W0 — фиксация + решения D1-D6; W1 — security_updates.py (паттерн python_deps.py/install_cron_metrics: декларативная генерация /etc/apt/apt.conf.d/20auto-upgrades + 50unattended-upgrades security-only origins, content-match no-op идемпотентность, атомарная запись через shared/atomic_writer, reboot-политика 04:30 c env-оверрайдом SECURITY_AUTO_REBOOT), вызов из φ1 system_bootstrap (шаг 5.5, non-fatal) и φ12 deploy_update (пропагация политики при node-update); W2 — security_posture.py (8 проверок S1-S8: unattended-upgrades-активность, pending security-апдейты, ufw, sshd -T, docker daemon live-restore/iptables/2375-2376, world-writable в /opt/platform + права секретов, forced-command целостность ci-deploy ключа, image freshness — digest drift локальный vs registry (docker manifest inspect, устаревшие образы с доступными фиксами); exit 0=healthy 1=warnings 2=errors; --json для будущей мониторинг-интеграции L5) + remote-канал по канону converge (build_check_security_ssh_cmd в build-ssh-cmd.sh, execute_remote_check_security в remote-cmd.sh, execute-check-security в remote_executor.py, entrypoint core/entrypoints/check-security.sh, таргет в makefiles/bootstrap.mk); W3 — верификация (per-task test-summary → make check → make gate MODE=fast).
RATIONALE:             Аудит 2026-08-04 (по запросу пользователя «готовы ли к ИИ-хакерам»): периметр силён (ufw declarative deny-5432/forbid-2375-2376, SSH forced-command dispatch, 0 git-токенов на VPS, age-секреты, bandit+gitleaks в pre-commit), НО: (1) известные CVE в незапатченных системах — самый дешёвый вектор автоматизированных атак — не закрыт вообще (нет unattended-upgrades, нет autoremove-политики, reboot-политики); (2) нет ни одного инструмента проверки security-постурa ноды (fail2ban/auditd/trivy/pip-audit — все отсутствуют, dependabot покрывает только github-actions); (3) docker-образы пересобираются только по content-hash исходников — security-фиксы базовых образов не подхватываются автоматически (L3 — вне скоупа, follow-up; L4-детекция — S8 в этой волне по решению пользователя 2026-08-04). Все решения — в существующем каноне (host-cron/apt-паттерны bootstrap, converge remote-канал, PYTHONPATH-фасады), без новой инфраструктуры.
ACCEPTANCE_CRITERIA:   (1) W1: security_updates.py покрыт unit-тестами (желаемые 20auto-upgrades/50unattended-upgrades с security-only origins, content-match no-op, auto-reboot true/false ветки, установка пакета unattended-upgrades, атомарная запись); вызов в φ1 (после firewall, non-fatal) и φ12 (non-fatal, node-update-пропагация). (2) W2: security_posture.py покрыт unit-тестами — каждая проверка S1-S8 имеет positive И negative кейс (R5: _negative), агрегация exit 0/1/2, --json-форма; remote-канал: execute-check-security в remote_executor.py + build_check_security_ssh_cmd (dry-run тест); make check-security NODE=<name> зелёный путь. (3) W3: per-task `make test-summary TEST_FILE=...` зелёный, `make check` чист, `make gate MODE=fast` зелёный, `make check-manifests` PASS (манифесты регенерированы). (4) Новый глагол check-security в глоссарии + entrypoint-manifest + core/AGENTS.md canonical table (generated).
IMPLEMENTS:            Решение пользователя 2026-08-04 («DevPlan: L1+L2 первой волной»): L1 = unattended-upgrades модуль, L2 = make check-security. L3 (trivy/pip-audit/dependabot-pip), L5 (fail2ban/auditd/мониторинг-интеграция) — зафиксированы как follow-up в Debt-реестре плана; L4-детекция (S8 image freshness) реализована в W2 по решению пользователя 2026-08-04, scheduled-пересборка — follow-up.
IMPACTS:               tronyx-vps (после деплоя волн): /etc/apt/apt.conf.d/20auto-upgrades + 50unattended-upgrades (security-only, reboot 04:30 при SECURITY_AUTO_REBOOT=true по умолчанию), пакет unattended-upgrades. Код: core/internal/bootstrap/security_updates.py (новый), lifecycle/phases/system.py (φ1 шаг 5.5 + φ12), security_posture.py (новый), build-ssh-cmd.sh, remote-cmd.sh, remote_executor.py, core/entrypoints/check-security.sh (новый), makefiles/bootstrap.mk, tests/unit/* (3 файла). Generated: entrypoint-manifest.yaml, core/AGENTS.md, root AGENTS.md глоссарий. Обратной несовместимости нет.
REQUIRES:              Зелёный baseline (make check + gate MODE=fast) до старта; решения W0 (D1-D6 ниже); для node-проверки — доступ к ноде (опционально, W3 smoke). Нода: Ubuntu 24.04 (unattended-upgrades доступен в репозиториях — проверено, пакет присутствует).
$END_ARTIFACT_CONTRACT

## 0. Draft Code Graph (XML)

```xml
<graph>
  <!-- W1: L1 unattended-upgrades -->
  <entity name="core_internal_bootstrap_security_updates_py" TYPE="MODULE"
    keywords="unattended-upgrades,20auto-upgrades,50unattended-upgrades,security-only,reboot-04:30,content-match-noop,atomic"
    annotation="CLI ensure: (1) apt-установка пакета unattended-upgrades через helpers install_apt_packages (идемпотентно, is_pkg_installed-gate); (2) декларативная запись /etc/apt/apt.conf.d/20auto-upgrades (Update-Package-Lists 1, Download-Upgradeable-Packages 1, Unattended-Upgrade 1, AutocleanInterval 7); (3) /etc/apt/apt.conf.d/50unattended-upgrades — security-only origins (Ubuntu stable/security + esm-infra), AutoRemove true, Automatic-Reboot true+04:30 (SECURITY_AUTO_REBOOT=false → reboot off), Automatic-Reboot-WithUsers true (live-restore делает docker-ребут безопасным); content-match no-op (канон install_cron_metrics), атомарная запись shared/atomic_writer. --auto-reboot true|false."
    CrossLinks="core/internal/bootstrap/lifecycle/helpers/system.py; core/internal/bootstrap/lifecycle/phases/system.py"/>
  <entity name="core_internal_bootstrap_lifecycle_phases_system_py" TYPE="MODULE"
    keywords="phase-system-bootstrap,security-updates-step-5.5,deploy-update,non-fatal"
    annotation="+шаг 5.5 в phase_system_bootstrap (после firewall): python3 security_updates.py --auto-reboot $SECURITY_AUTO_REBOOT, non-fatal (best-effort как firewall/tor); +вызов в phase_deploy_update (φ12) — пропагация политики при node-update (идемпотентно, no-op при совпадении конфига)."
    CrossLinks="core/internal/bootstrap/security_updates.py"/>
  <!-- W2: L2 check-security -->
  <entity name="core_internal_bootstrap_security_posture_py" TYPE="MODULE"
    keywords="security-posture,S1-S8,unattended-upgrades-active,pending-security,ufw,sshd-config,docker-live-restore,world-writable,forced-command,image-freshness,digest-drift,manifest-inspect,exit-0-1-2,json"
    annotation="8 проверок (stdlib + firewall.parse_ufw_status + shared/timeouts): S1 unattended-upgrades активен (файлы+пакет+apt-config dump); S2 pending security-апдейты (update-notifier apt-check --human-readable, >0 → warning); S3 ufw active + baseline 22/80/443 + DENY 5432 + нет 2375/2376 (переиспользует parse_ufw_status из firewall.py — 0 дублирования); S4 sshd -T → PermitRootLogin prohibit-password|no + PasswordAuthentication no + PubkeyAuthentication yes; S5 docker daemon.json live-restore=true + docker info iptables=true + нет LISTEN 2375/2376; S6 world-writable в /opt/platform + секреты не world-readable; S7 ci-deploy authorized_keys содержит forced-command command=\"cd ... orchestrator_cli dispatch\",restrict; S8 image freshness — docker ps → inspect RepoDigests vs `docker manifest inspect --verbose` текущего digest тега (digest-pin устарел / tag-based новый образ → WARN; manifest unknown → локальный образ PASS; registry недоступен → WARN graceful). Exit: 0=healthy 1=warnings (S2/S8) 2=errors (любой S-FAIL). --json: {checks: [...], exit_code}. Root-only (sshd -T)."
    CrossLinks="core/internal/bootstrap/firewall.py; core/internal/shared/timeouts.py"/>
  <entity name="core_internal_bootstrap_remote_executor_py" TYPE="MODULE"
    keywords="execute-check-security,ssh-proxy,vps-self-detect,no-sync-core"
    annotation="+CLI subcommand execute-check-security (зеркало execute-converge: resolve → VPS self-detect → prepare ssh opts → ssh root@host, БЕЗ sync-core — проверка read-only, remote core уже доставлен)."
    CrossLinks="core/internal/bootstrap/remote-cmd.sh; core/internal/bootstrap/build-ssh-cmd.sh"/>
  <entity name="core_internal_bootstrap_build_ssh_cmd_sh" TYPE="MODULE"
    keywords="build_check_security_ssh_cmd,printf-q,PYTHONPATH-export"
    annotation="+build_check_security_ssh_cmd (printf %q, D3): export PLATFORM_ROOT + PYTHONPATH=${remote_root} (канон TRAP[BUG] 2026-07-31 — security_posture импортирует core.internal) → python3 ${remote_root}/core/internal/bootstrap/security_posture.py [--json]."
    CrossLinks="core/internal/bootstrap/remote-cmd.sh"/>
  <entity name="core_entrypoints_check_security_sh" TYPE="MODULE"
    keywords="entrypoint,--node,--dry-run,--json,auto-detect,remote-fallback"
    annotation="Тонкий entrypoint (паттерн converge.sh: parse --node/--dry-run/--json, auto-detect node_detect, execute_remote_check_security, rc=2 → local exec security_posture.py, exit $rc)."
    CrossLinks="makefiles/bootstrap.mk"/>
  <entity name="makefiles_bootstrap_mk" TYPE="CONFIG"
    keywords="check-security-target,make-target,exit-0-1-2-passthrough"
    annotation="+таргет check-security: NODE обязателен, PLATFORM_ROOT export (TRAP[BUG] 2026-07-31), passthrough exit 0/1/2 (НЕ маскирует 1 — это check, не reconcile), --json/--dry-run флаги."
    CrossLinks="core/entrypoints/check-security.sh"/>
</graph>
```

## 1. Data Flow (шаг за шагом)

```
W0 ── make check + gate MODE=fast (baseline green) ──► решения (D1-D6) ──► DevPlan
W1 ── security_updates.py (unit-тесты) ──► φ1 шаг 5.5 (после firewall, non-fatal) + φ12 (node-update) ──►
     /etc/apt/apt.conf.d/20auto-upgrades + 50unattended-upgrades + пакет unattended-upgrades
     │ цикл: ensure() → is_pkg_installed? → apt install unattended-upgrades (APT_TIMEOUT=300)
     │      → desired config → content-match с существующим → нет? → atomic write → marker
W2 ── security_posture.py (unit-тесты S1-S8) ──► build_check_security_ssh_cmd + execute_remote_check_security
     ──► remote_executor execute-check-security ──► entrypoint check-security.sh ──► make check-security NODE=<n>
     │ цикл: S1-S8 → агрегация {0 healthy, 1 warnings (S2 pending>0, S8 image drift), 2 errors} → текст-отчёт + --json
W3 ── per-task test-summary ──► make check (до чистоты) ──► make gate MODE=fast ──► make check-manifests
     (опц.) smoke на ноде: security_posture.py (read-only, безопасен для прямого запуска)
```

## 2. File Manifest

| Файл | Действие | Волна |
|------|----------|-------|
| `.ai/plans/134-security-hardening/01-DevPlan.md` | создать | W0 |
| `core/internal/bootstrap/security_updates.py` | создать | W1 |
| `core/internal/bootstrap/lifecycle/phases/system.py` | модифицировать (+шаг 5.5 в φ1, +вызов в φ12) | W1 |
| `tests/unit/test_security_updates.py` | создать | W1 |
| `core/internal/bootstrap/security_posture.py` | создать | W2 |
| `core/internal/bootstrap/build-ssh-cmd.sh` | модифицировать (+build_check_security_ssh_cmd) | W2 |
| `core/internal/bootstrap/remote-cmd.sh` | модифицировать (+execute_remote_check_security) | W2 |
| `core/internal/bootstrap/remote_executor.py` | модифицировать (+execute-check-security CLI) | W2 |
| `core/entrypoints/check-security.sh` | создать | W2 |
| `makefiles/bootstrap.mk` | модифицировать (+таргет check-security) | W2 |
| `tests/unit/test_security_posture.py` | создать | W2 |
| `tests/unit/test_remote_executor.py` | модифицировать (+execute-check-security) | W2 |
| `core/entrypoint-manifest.yaml` | regenerate (make generate-entrypoint-manifest) | W2 |
| `core/AGENTS.md` | regenerate (make generate-agents-md) | W2 |
| `AGENTS.md` (root глоссарий) | regenerate (make generate-agents-md) | W2 |

## 3. Волны

### W0 — Фиксация (артефакты + решения)

Baseline: `make check` + `make gate MODE=fast` зелёные до старта. Решения (зафиксированы, отклонённые варианты):

**D1 — L1 = unattended-upgrades (Debian/Ubuntu канон), конфиг генерирует security_updates.py (паттерн python_deps.py + install_cron_metrics).**
- Rejected: самодельный cron `apt-get upgrade` (нет security-only фильтрации origins, нет dpkg-lock awareness — конфликт с apt-цепочкой bootstrap, нет reboot-политики); systemd timer unit (новая движущая часть).
- Reason: unattended-upgrades — стандарт для security-only автопатчинга: origins-фильтр (`${distro_id}:${distro_codename}-security`), dpkg-локи (не ломает apt в bootstrap), daily cron через apt-daily.timer, email-отчёты. Модуль генерирует конфиг декларативно с content-match no-op — повторный вызов не трогает диск (инвариант 6: строгая идемпотентность).

**D2 — Reboot-политика: Automatic-Reboot=true (04:30, WithUsers=true), env SECURITY_AUTO_REBOOT=false отключает.**
- Rejected: без reboot (kernel-CVE висит до ручного ребута — главная дыра); ребут в дневное время (даунтайм).
- Reason: kernel-фиксы требуют ребута, а unattended-upgrades без Automatic-Reboot их не применяет до перезагрузки. 04:30 — минимальный трафик. live-restore уже включён (docker_daemon.py) — контейнеры переживают рестарт демона. Env-оверрайд — для нод, где ребут недопустим.

**D3 — L2 = новый канонический таргет `make check-security NODE=<name>` по канону converge (remote_executor + printf %q + VPS self-detect).**
- Rejected: docker-compose модуль мониторинга (security-постур — диагностика хоста, не сервис); расширение healthcheck (liveness модулей, другой контракт).
- Reason: converge-канал готов (remote_executor.py execute-converge — resolve → VPS self-detect → ssh root@host, без sync-core). Exit-семантика: 0=healthy, 1=warnings (S2 pending>0 — между daily-кронами норма), 2=errors. Make НЕ маскирует 1 (в отличие от converge — это check-таргет, оператор должен видеть warning).

**D4 — Набор проверок S1-S8: минимально достаточный для реальных векторов, без мониторинг-тяжести.**
- S1 unattended-upgrades-active (файлы + пакет + apt-config dump) → FAIL=2; S2 pending-security>0 (update-notifier apt-check) → WARN=1; S3 ufw (переиспользование parse_ufw_status из firewall.py); S4 sshd -T (PermitRootLogin/PasswordAuthentication/PubkeyAuthentication); S5 docker (live-restore, iptables, нет 2375/2376 LISTEN); S6 world-writable /opt/platform + секреты; S7 forced-command целостность ci-deploy ключа (канал деплоя — единственный writer ключа lifecycle φ2, любая потеря `command=` = открытый SSH); S8 image freshness (digest drift локальный RepoDigests vs registry `docker manifest inspect --verbose` — пин устарел / tag-based новый образ → WARN, никогда FAIL: digest-pin — осознанная политика гейта image_tag_form; manifest unknown → локальный образ skip; registry недоступен/timeout → WARN graceful как apt-check в S2; только FAIL — docker недоступен). Все FAIL → exit 2.

**D5 — security_posture.py импортирует firewall.py (parse_ufw_status, BASELINE_PORTS, DENY_PORT) и shared/timeouts (гейт U-11) — PYTHONPATH экспортирует SSH-команда (канон TRAP[BUG] 2026-07-31).**
- Rejected: stdlib-only копия парсера ufw (дублирование бизнес-логики — нарушение принципа Small Simple Blocks + debt); лишние 50 LOC.
- Reason: bootstrap → bootstrap импорт легитимен (тот же слой); таймауты subprocess — из shared/timeouts (единый SoT, гейт test_gate_timeout_literals). build_check_security_ssh_cmd экспортирует PYTHONPATH=${remote_root} — тот же паттерн, что converge.sh:66.

**D6 — `--json` флаг в security_posture.py (тривиально, 10 LOC) — фундамент L5 (Grafana/Loki-интеграция).**
- Reason: L5 (мониторинг-интеграция) запланирован follow-up; JSON-выход теперь — 0 инфраструктурных затрат, экономит реверс-инжиниринг текстового отчёта потом.

### W1 — L1: security_updates.py + wiring

1. `security_updates.py` (CLI `ensure --auto-reboot true|false`):
   - apt-установка пакета `unattended-upgrades` (helpers install_apt_packages — is_pkg_installed gate, APT_TIMEOUT=300)
   - `desired_auto_upgrades_config()` → `/etc/apt/apt.conf.d/20auto-upgrades`:
     `APT::Periodic::Update-Package-Lists "1"; Download-Upgradeable-Packages "1"; Unattended-Upgrade "1"; AutocleanInterval "7";`
   - `desired_unattended_config(auto_reboot)` → `/etc/apt/apt.conf.d/50unattended-upgrades`:
     origins: `"origin=Ubuntu,archive=${distro_codename}-security"` + esm-infra; `Unattended-Upgrade::AutoRemove "true"`; `Automatic-Reboot "true"` (или false), `Automatic-Reboot-Time "04:30"`, `Automatic-Reboot-WithUsers "true"`
   - content-match no-op (прочитать существующий файл → совпал → skip), атомарная запись shared/atomic_writer (mode 0644)
   - LDD [IMP:9] на успех, [IMP:10] на fatal
2. `phases/system.py`:
   - φ1 шаг 5.5 (после firewall): `python3 security_updates.py --auto-reboot $(SECURITY_AUTO_REBOOT:-true)` — non-fatal (best-effort, как firewall/tor)
   - φ12 `phase_deploy_update`: тот же вызов — политика доезжает при `make node-update` (идемпотентно)
3. `tests/unit/test_security_updates.py` — LDD-тесты: desired-config содержимое (security-only origins, reboot-ветки), no-op на совпадение (0 записей в tmp_path), установка пакета (mock is_pkg_installed False → install вызов), атомарность, negative (R5): config-drift → перезапись.

### W2 — L2: check-security

1. `security_posture.py` (CLI `--json`, exit 0/1/2):
   - 8 проверок S1-S8 (см. D4), каждая — pure-функция `check_*(ctx) -> CheckResult(status, message)`; агрегация: любой FAIL → 2, иначе любой WARN → 1, иначе 0
   - subprocess через helpers (timeouts из shared/timeouts), root-check fail-fast (sshd -T требует root)
   - `--json`: `{"node": ..., "exit_code": N, "checks": [{"id": "S1", "status": "PASS|WARN|FAIL", "message": ...}]}`
   - LDD [IMP:9] per-check
2. remote-канал:
   - `build-ssh-cmd.sh` + `build_check_security_ssh_cmd`: `export PLATFORM_ROOT + PYTHONPATH` → `python3 ${remote_root}/core/internal/bootstrap/security_posture.py [--json]` (printf %q, D3)
   - `remote-cmd.sh` + `execute_remote_check_security` → `remote_executor.py` + CLI `execute-check-security` (зеркало execute-converge: resolve → VPS self-detect → ssh; БЕЗ sync-core)
   - `core/entrypoints/check-security.sh` (паттерн converge.sh: --node/--dry-run/--json, auto-detect, rc=2 → local exec)
3. `makefiles/bootstrap.mk` + таргет `check-security` (NODE обязателен, PLATFORM_ROOT export, passthrough exit)
4. `tests/unit/test_security_posture.py` — каждая проверка positive+negative (R5), агрегация 0/1/2, --json-форма, root-check; `tests/unit/test_remote_executor.py` + execute-check-security (resolve → ssh, dry-run, VPS self-detect, no-sync-core)
5. Регенерация манифестов: `make generate-entrypoint-manifest` + `make generate-agents-md` → entrypoint-manifest.yaml + core/AGENTS.md + root глоссарий (глагол `check-security`)

### W3 — Верификация

Per-task: `make test-summary TEST_FILE=tests/unit/test_security_updates.py` → `make test-summary TEST_FILE=tests/unit/test_security_posture.py` → `make test-summary TEST_FILE=tests/unit/test_remote_executor.py`. Фикс-цикл: `make check` (до чистоты) → `make check-manifests` → `make gate MODE=fast` (один раз, в конце). Опциональный smoke на ноде: `make check-security NODE=<name>` (read-only) + `security_updates.py ensure --dry-run`-эквивалент (модуль вызывает apt install — на smoke-ноде только после подтверждения оператора).

## 4. Критерии приёмки (сводка)

1. `make check-security NODE=<test>` на подготовленной ноде: exit 0, все S1-S8 PASS (после того как unattended-upgrades установлен волной W1)
2. `make check-manifests` PASS — generated-файлы в синхроне (глоссарий + entrypoint-manifest + core/AGENTS.md содержат check-security)
3. `make gate MODE=fast` зелёный; unit-тесты W1/W2 все зелёные с LDD IMP:9 траекториями
4. Нет регрессий в bootstrap: φ1/φ12 non-fatal ветки не роняют init/update (существующие тесты state_machine/phases зелёные)

## 5. Follow-up (вне скоупа, Debt)

- L3: trivy (L1/L2 образы) + pip-audit (requirements.txt) + dependabot `pip` ecosystem — CI-сканирование уязвимостей (check-suite.yaml новые static-записи)
- L4: scheduled-пересборка L2-образов (weekly CI hermes-build-context → push → node-update) — автоматический фикс после S8-детекции (детекция реализована, автопересборка — follow-up)
- L5: fail2ban для SSH, auditd, интеграция security_posture.py --json в Loki/Grafana (alert-rules), вызов check-security в converge non-blocking

$END_DEVPLAN
