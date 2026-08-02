# 01-Brief — Волна 118: финальное снижение дрейфа (post-117)

<!-- $ARTIFACT_CONTRACT
PURPOSE:          Полный реестр задач финальной волны снижения дрейфа после волн 116/117 (shell→Python, SoT-dedup).
                  Разбиение на 7 брифов (A-G) для точечной проработки. Цель — унификация и значительное снижение
                  дрейфа БЕЗ добавления нового функционала. После волны 118 — ручное тестирование на тестовом сервере.
DESCRIPTION:      6 параллельных аудиторов (shell-бизнес-логика, Python-монолиты, мёртвый код/сироты,
                  SoT-дубли/кросс-слойные нарушения, тестовый набор, Makefile/manifest/глоссарий) + точечная
                  grep-верификация → 51 задача: 20 из мега-DevPlan (D1-D20, поглощён) + 31 новая. 7 брифов.
RATIONALE:        Аудит проведён после полного мержа волны 117 (main чист, 51 коммит). Сервер tronyx-vps —
                  пересоздан, тестовый, проектов нет, мигрировать нечего. Ручное тестирование начнётся ПОСЛЕ этой волны,
                  поэтому критические фиксы деплоя (бриф A) выполняются первыми.
ACCEPTANCE_CRITERIA:
  - AC1: Все задачи закрыты или явно отклонены с обоснованием (каждый бриф = свой DevPlan + VerificationReport).
  - AC2: `make gate MODE=fast` и `make check-manifests` зелёные после каждого брифа.
  - AC3: 0 новых глаголов в entrypoint-manifest (AC6 мега-DevPlan сохраняется).
  - AC4: `pytest tests/ -m "not requires_node"` — 0 regressions.
  - AC5: LOC-сокращение ≥ 700 (консервативно: мёртвый код ~700 + shell-миграция ~1600).
  - AC6: R5 ANTI-SURVIVORSHIP — каждое упрощение/удаление покрыто negative-тестом (или тестом на удалённый API).
  - AC7: Бриф A верифицирован на test-VPS: `make deploy-context`/`deploy-project` проходят (SCPChannel-fix).
IMPLEMENTS:       Решение пользователя 2026-08-02: все 7 брифов A-G в волну 118; мега-DevPlan D1-D20 поглощён в брифы.
IMPACTS:          core/internal/deploy/, core/internal/shared/, core/internal/bootstrap/ (deploy, lifecycle, converge),
                  core/internal/scaffold/, core/internal/healthcheck/, core/lib/, core/entrypoints/, core/modules/,
                  tests/, tests/gates/, makefiles/, .github/workflows/, .pre-commit-config.yaml, entrypoint-manifest.yaml.
REQUIRES:         Результаты 6 аудитов 2026-08-02 (субагенты), grep-верификация ключевых находок (см. §3).
-->

---

## 1. Контекст

- Волна 117 (Shell→Python финал + SoT-dedup + Test Honesty) завершена: main чист, 51 коммит не запушен (волна 118 не зависит от push).
- Остаток shell: 19 entrypoint-фасадов (1206 LOC) + 13 lib (1856 LOC) + ~37 внутренних/модульных скриптов (~5024 LOC).
- Python: 57.1K LOC в core, 127.8K LOC тестов (517 файлов).
- Сервер tronyx-vps пересоздан — тестовый, проекты не запущены. Ручное тестирование — ПОСЛЕ волны 118.
- Предыдущий мега-DevPlan (D1-D20) архивирован: `01-DevPlan-mega-absorbed.md`, его задачи распределены по брифи A-G.

## 2. Методология аудита

6 параллельных субагентов по зонам:
1. **Shell-бизнес-логика** — все .sh вне entrypoints/lib: классификация тонкий-фасад/бизнес-логика/мёртвый, Python-аналоги, потребители.
2. **Python-монолиты** — топ-20 .py: смешанные ответственности, слои, shared/ kitchen-sink.
3. **Мёртвый код/сироты** — entrypoints vs Makefile vs manifest, lib-функции без вызовов, orphan-модули, dangling-ссылки.
4. **SoT-дубли** — SSH-флаги, порты, таймауты, healthcheck-критерии, compose-команды, пути, кросс-слойные импорты.
5. **Тестовый набор** — монстры, дубли, дыры покрытия волны 117, pass-тесты, skip-маркеры.
6. **Makefile/manifest/глоссарий** — дрейф таргетов, forbidden-глаголы, gate_id, дубли.

Все критические находки **верифицированы grep-проверками** (SCPChannel, compose-списки, reconciler paths, importlib, _assemble_payload, check-exception-patterns).

## 3. Критические факты (верифицированы в коде)

| # | Факт | Evidence |
|---|------|----------|
| K1 | **SCPChannel без metadata** — deploy-context всегда FAILED: `SCPChannel()` без host → channels.py:228 возвращает FAILED | context_deployer.py:287, channels.py:228 |
| K2 | **Списки compose-файлов расходятся** — converge лечит модули, которые деплой не поднимает: `(compose.yaml, docker-compose.yaml, docker-compose.base.yml)` vs `(compose.yaml, compose.yml, docker-compose.yml)` | docker_orchestrator.py:133, converge/runtime.py:224, converge/volumes.py:160 |
| K3 | **importlib.spec_from_file_location** в context_deployer — обход системы импорта, тихий полом cert-кода | context_deployer.py:647 |
| K4 | **reconciler_projects.py** хардкодит `/opt/projects` без env-цепочки | reconciler_projects.py:392 |
| K5 | **3 module-hook'а мертвы на runtime** — зарегистрированы (module.yaml + manifest module_hooks), но триггер `invoke_module_interface <m> deploy-hook` удалён в 117 | rg deploy-hook → только module-interface.sh docs |
| K6 | **Сломанный pre-commit hook commit-msg** — .pre-commit-config.yaml:250 ссылается на несуществующий check-commit-msg.sh | .pre-commit-config.yaml:250 |
| K7 | **check-exception-patterns** — реальный таргет gate-конвейера, но НЕ в .PHONY → невидим для manifest-генератора/глоссария | ci.mk:331 |
| K8 | **2 tar-пути** — `orchestrator._assemble_payload` (949) и `payload_deliverer.assemble_payload` (119) — дрейф формата | orchestrator.py:949, payload_deliverer.py:119 |
| K9 | **3 pass-теста в гейтах** — нет assert (R1-дыра: гейт сканирует по-файлово, не по-функции) | test_gate_compose_base_contract, test_gate_ci_env_vars, test_gate_workflow_consistency |
| K10 | **os.healthcheck_poller таймауты** 30/10 vs канон 60/3; ssh_read 10с vs SSH_READ_TIMEOUT=60 в scaffold-слое | healthcheck_poller.py:37-38, project_remover.py:286, project_lister.py:332 |

## 4. Полный реестр задач (51 → 7 брифов)

Легенда: `[D#]` — задача из мега-DevPlan (поглощена), `[NEW]` — новая из аудита 2026-08-02. Sev: CRIT/HIGH/MED/LOW.

### Бриф A — Критические фиксы деплоя (блокирует ручное тестирование)

| # | Sev | Задача | Evidence |
|---|-----|--------|----------|
| A1 | CRIT | SCPChannel→LocalChannel в context_deployer (payload уже на VPS после overlay) | [D5] context_deployer.py:287 |
| A2 | HIGH | Compose-списки файлов → единый SoT `shared/compose_files.py`, 4+ потребителя | [NEW] K2 |
| A3 | HIGH | reconciler_projects `/opt/projects` → env-цепочка PROJECTS_BASE | [NEW] K4 |
| A4 | HIGH | `_assemble_payload` дубль → делегирование в payload_deliverer | [NEW] K8 |
| A5 | HIGH | importlib-обход → нормальный импорт cert_orchestrator + приватный `_is_cert_valid` | [NEW] K3 |
| A6 | HIGH | status/remove/stub консолидация (deploy_orchestrator → deploy_engine) | [D1] |
| A7 | MED | Двойной snapshot (deploy_engine vs deploy_history) → один канон | [D2] |
| A8 | MED | deploy_engine `deploy()`/`_deploy_inner` — схлопнуть дублирование | [NEW] |

### Бриф B — Мёртвый код, 2-й проход (~700 LOC)

| # | Sev | Задача | Evidence |
|---|-----|--------|----------|
| B1 | HIGH | state_machine step-API (start/complete/skip/fail_step, pre/postcondition, ~130 LOC, 0 callers) | [D3] |
| B2 | MED | content_hash rename (2 модуля одного имени) → build_cache.py | [D4] |
| B3 | MED | NodeYaml typed-геттеры verify-then-delete (~500 LOC) | [D6] |
| B4 | LOW | generate-manifests-atomic — удалить dead target | [D14] |
| B5 | LOW | vps_status_check.py — осиротевший модуль (130 LOC) + тест | [NEW] |
| B6 | MED | 12 мёртвых lib-функций (~242 LOC) + python_deps.sh | [NEW] |
| B7 | LOW | ready-check.sh ×2 (postgres, backup-cron) — мёртвые (COPY в образ, 0 вызовов) | [NEW] |
| B8 | HIGH | 3 module-hook'а (nginx_reload, monitoring, postgres) — решить: восстановить триггер ИЛИ удалить регистрацию | [NEW] K5 |
| B9 | LOW | .pre-commit-config commit-msg hook — починить ссылку на check_commit_msg.py | [NEW] K6 |
| B10 | LOW | test_gate_dead_code.py — устаревшее исключение secrets-init.sh | [NEW] |

### Бриф C — SoT-унификация констант/путей/таймаутов

| # | Sev | Задача | Evidence |
|---|-----|--------|----------|
| C1 | MED | docker_ops.py — 6 таймаут-литералов вне scope гейта | [D8] |
| C2 | LOW | context_promoter — SSH-флаги через shared/ssh_opts | [D9] |
| C3 | MED | COMPOSE_PROFILES — единый loader (platform-infra.yaml SoT) | [D10] |
| C4 | MED | `--timeout 30` ×3 + fallback-порты sync_env_defaults → SoT | [D11] |
| C5 | MED | invoke_module_interface — консолидация 2 bash-обёрток → shared | [D7] |
| C6 | MED | litellm-config.yml путь — 4 копии + шаблон → единая константа | [NEW] |
| C7 | MED | deploy_paths.py активация: `/etc/letsencrypt/live`, `/opt/node-configs`, `/opt/platform` | [NEW] |
| C8 | LOW | converge/infra.py:39 AUDIT_LOG_FILE → импорт из shared/audit_logger | [NEW] |
| C9 | MED | cert-валидность — единая политика в shared/ssl_certs (2 комбинации сейчас) | [NEW] |
| C10 | MED | Двойной run_subprocess (subprocess_io vs converge/infra) — несовместимые сигнатуры | [NEW] |
| C11 | MED | healthcheck_poller 30/10→60/3 + ssh_read scaffold 10с→SSH_READ_TIMEOUT | [NEW] K10 |

### Бриф D — Монолит-декомпозиция (Python-архитектура)

| # | Sev | Задача | Evidence |
|---|-----|--------|----------|
| D1 | MED | docker_orchestrator (1401) → parallel_runner + healthcheck_runner + hermes_workflow | [NEW] |
| D2 | MED | node_yaml (1512) — миксины по поддоменам (ОПЦИОНАЛЬНО, риск 831 .get()) | [NEW] |
| D3 | LOW | shared-чистка: age_key.py (compat-шим), ssh_command_parser (1 потребитель), deploy_paths (пересечение с C7) | [NEW] |
| D4 | MED | check_env_requires дубль (secrets_validator vs validate_module_yaml) — единый чекер | [NEW] |
| D5 | MED | project_scaffolder.create_github_repo → github_ops | [NEW] |
| D6 | MED | context_deployer god-function → шаги с typed-контрактами (после A5) | [NEW] |
| D7 | LOW | generate_platform_env codegen f-string → jinja (опционально) | [NEW] |

### Бриф E — Shell→Python финальная миграция (~1600 LOC)

| # | Sev | Задача | LOC | Evidence |
|---|-----|--------|-----|----------|
| E1 | MED | install-tor-proxy.sh Tier-2 (условие: тесты ПЕРЕД миграцией, иначе 119) | 422 | [D19] |
| E2 | MED | install-docker.sh (пакеты, daemon.json, verify) | 218 | [NEW] |
| E3 | MED | firewall.sh (декларативный ufw) | 167 | [NEW] |
| E4 | MED | modules-healthcheck.sh (restart-loop, docker inspect) | 127 | [NEW] |
| E5 | MED | tor-proxy-healthcheck.sh (3-stage) | 121 | [NEW] |
| E6 | MED | scripts-audit.sh (grep-аудит → Python+yaml) | 97 | [NEW] |
| E7 | MED | platform-secrets/install.sh (age-key, systemd) | 225 | [NEW] |
| E8 | LOW | hermes-images.sh (docker build L1/L2) | 77 | [NEW] |
| E9 | LOW | upload-s3.sh → upload.py (merge валидации) | 84 | [NEW] |
| E10 | LOW | notify-hook.sh → telegram_notifier (merge severity-mapping) | 108 | [NEW] |
| E11 | LOW | adopt-project.sh grep-YAML → project_adopter | 89 | [NEW] |
| E12 | LOW | issue-cert.sh:600 — node_yaml `--format lines` вместо grep|cut | 15 | [D18] |

### Бриф F — Тесты: чистка и дыры

| # | Sev | Задача | Evidence |
|---|-----|--------|----------|
| F1 | HIGH | 3 pass-теста в гейтах → реальные assert (R1) | [NEW] K9 |
| F2 | MED | test_agent_watchdog.py — 4 дубль-теста CircuitBreaker (после T52) | [NEW] |
| F3 | MED | test_monitoring_config_renderer.py (943) — разделить/удалить дубли 7 новых файлов | [NEW] |
| F4 | MED | Орфан test_smoke_bootstrap_dry_run.sh — зарегистрировать или удалить | [NEW] |
| F5 | MED | test_on_project_deploy.py — sys.path.insert → пакетный импорт | [NEW] |
| F6 | LOW | test_reconciler.py монолит (6 модулей) — разбить | [NEW] |
| F7 | MED | restart-гейты консолидация (test_restart_consistency → test_gate_make_contract) | [D13] |

### Бриф G — Гейты/манифест/глоссарий

| # | Sev | Задача | Evidence |
|---|-----|--------|----------|
| G1 | HIGH | Невидимый гейт test_gate_platform_env_schema → зарегистрировать (restart_consistency — см. F7) | [D12] |
| G2 | MED | templates-check дубль в manifest + check-exception-patterns → .PHONY + реген | [D15] K7 |
| G3 | LOW | Cross-layer allowlist docs 6→8 | [D16] |
| G4 | LOW | module.yaml D5-контракты (validate-modules) | [D20] |
| G5 | MED | Висячие gate_id в manifest (ruff-format, check-manifests, template-syntax-contract, r1_no_pass_tests) | [NEW] |

## 5. Порядок выполнения и зависимости

```
01. Бриф A (критические фиксы деплоя)   ← БЛОКИРУЕТ ручное тестирование
02. Бриф G (манифест/гейты)             ← независим, дёшев, закрывает loophole
        ── параллельно ──
03. Бриф B (мёртвый код)                ← чистка, облегчает B6/E-поиск
04. Бриф C (SoT-унификация)             ← зависит от A2/A4 (общие файлы)
05. Бриф D (монолит-декомпозиция)       ← после C (общие файлы), D6 после A5
06. Бриф E (shell→Python)               ← после B (чистка), тесты ПЕРЕД миграцией
07. Бриф F (тесты)                      ← последним, закрывает дыры после всех рефакторингов
        │
        ▼
РУЧНОЕ ТЕСТИРОВАНИЕ на tronyx-vps
```

## 6. Оценки

| Метрика | Значение |
|---------|----------|
| Задач | 51 (A:8, B:10, C:11, D:7, E:12, F:7, G:5) |
| LOC-сокращение | ≥700 (мёртвый код ~700, shell-миграция ~1600, дедуп ~200) |
| Bugfix'ов | 1 CRIT (A1 SCPChannel) + 1 HIGH (A5 importlib) |
| Новых глаголов | 0 (AC3) |
| Рискованных задач | B8 (module-hooks: решение wire-vs-delete), E1 (tor-proxy: тесты-перед), D2 (node_yaml: 831 .get()) |
| Гейтов | +1 регистрация (G1), −1 консолидация (F7) |

## 7. Правила исполнения

- Каждый бриф = свой DevPlan (`NN-DevPlan.md`) + VerificationReport после имплементации.
- Каждый бриф завершается `make gate MODE=fast && make check-manifests && ruff check .` (AC2).
- Удаление API (B1, B3, B5, B6, B7) — по R5: negative-тест на удалённый API или тест-маркер removed.
- Миграции shell→Python (E1-E11) — только с unit-тестами на Python-логику ПЕРЕД миграцией (Tier-1/Tier-2 Strangler).
- Commit policy U-83: `docs(118): ...` + `feat(118): <Brief> ...` (волна-коммиты).
