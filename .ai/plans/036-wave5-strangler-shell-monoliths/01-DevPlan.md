$START_DEVPLAN

# DevPlan 036 — Wave 5: Strangler-Fig для 6 оставшихся shell-монолитов

$ARTIFACT_CONTRACT
- **PURPOSE:** Декомпозиция 6 shell-монолитов с бизнес-логикой (deploy-project, add-vhost, adopt-project, issue-cert, remote-cmd, verify-domains) по методологии Strangler-Fig: Python-модуль получает бизнес-логику, shell остаётся тонким фасадом (<150 LOC).
- **DESCRIPTION:** Продолжение Wave 4 (035-wave4-strangler-top3), которая сократила топ-3 скрипта с 4114 до 392 LOC. Оставшиеся 6 скриптов содержат 5350 LOC, из которых ~60-70% — бизнес-логика, подлежащая извлечению в Python.
- **RATIONALE:** Выполнение языковой политики (AGENTS.md: новый код — Python), устранение inline `python3 -c` (12 блоков в 6 файлах), дедупликация общей логики (template engine, SSH command parser, YAML parsing), повышение тестируемости.
- **ACCEPTANCE_CRITERIA:**
  - AC-1: Каждый shell-файл ≤150 LOC (фасад) — оркестрация + вызов Python-модуля (≤200 для VPS и remote-cmd.sh — printf %q command builders остаются в shell per D3)
  - AC-2: 0 inline `python3 -c` / `<<PYEOF` в shell-фасадах
  - AC-3: Unit-тесты покрывают все извлечённые Python-модули (≥80% coverage)
  - AC-4: Все существующие интеграционные/gate-тесты остаются зелёными
  - AC-5: `make test` и `make gate MODE=fast` — зелёные на всех волнах
  - AC-6: Ни один production-путь не сломан (deploy-project — критический VPS-компонент)
- **IMPLEMENTS:** Wave 5 Strangler-Fig декомпозиции оставшихся shell-монолитов
- **IMPACTS:**
  - `core/internal/deploy/deploy-project.sh` (1183→~200 LOC)
  - `core/internal/scaffold/add-vhost.sh` (926→~150 LOC)
  - `core/internal/scaffold/adopt-project.sh` (906→~150 LOC)
  - `core/internal/bootstrap/issue-cert.sh` (696→~500 LOC, минимальные изменения)
  - `core/internal/bootstrap/remote-cmd.sh` (672→~200 LOC)
  - `core/internal/verify/verify-domains.sh` (281→~60 LOC)
  - Новые Python-модули: ~7 файлов в `core/internal/`
  - Новые тесты: ~7 файлов в `tests/unit/`
- **REQUIRES:**
  - Python ≥3.10, `pytest`, `pyyaml` (уже в проекте)
  - `core/internal/shared/ssh_command_parser.py` (уже создан в DevPlan 081)
  - `core/internal/template_engine.py` (уже существует)
  - `core/internal/shared/content_hash.py` (уже существует)
$END_ARTIFACT_CONTRACT

---

## Debt Intake

Перед анализом проведён аудит TRAP-аннотаций и DEBT-регистров в целевых файлах:

| Файл | TRAP | Статус |
|------|------|--------|
| `deploy-project.sh` | TRAP[BUG] B1 (DEPLOY_STATUS order), TRAP[DECISION] rollback on-node, TRAP[DECISION] SSH forced-command, TRAP[DECISION] audit_log replaces audit_write, TRAP[BUG] platform-deliver exit 1, TRAP[BUG] env var prefix, TRAP[DECISION] deliver via stdin | IN_SCOPE: все TRAP сохраняются при миграции в Python-модуль |
| `add-vhost.sh` | TRAP[BUG] pipefail+|| chain, TRAP[BUG] DRIFT-1 flat directory, TRAP[DECISION] harness vhost isolation | IN_SCOPE: переносятся в Python |
| `adopt-project.sh` | TRAP[DECISION] local parse_args, TRAP[BUG] молчаливый дефолт "personal" + casing | IN_SCOPE |
| `issue-cert.sh` | TRAP[BUG] false diagnosis webnames, TRAP[BUG] acme.sh basename bug, TRAP[BUSINESS] API key shred, TRAP[DECISION] HTTP-01 fallback | DEFER: script сохраняется как shell subprocess (TRAP cert_orchestrator) |
| `remote-cmd.sh` | TRAP[BUG] ci_deploy_key not exported, TRAP[BUG] VPS self-SSH loop, TRAP[BUG] node-update не доставлял core | IN_SCOPE |
| `verify-domains.sh` | TRAP[BUG] status-page URL mismatch | IN_SCOPE |

---

## Requirements Analysis

### Ключевые критерии успеха

1. **Zero inline python3:** Устранить все 12 блоков `python3 -c` / `<<PYEOF` в 6 скриптах
2. **Shell-фасады <150 LOC:** Каждый скрипт — только оркестрация (parse args → call Python → exit)
3. **Дедупликация:** Устранить дублирование между `add-vhost.sh` ↔ `template_engine.py`, `remote-cmd.sh` ↔ `ssh_command_parser.py`, `validate.sh` (inline jsonschema)
4. **Безопасность VPS-деплоя:** `deploy-project.sh` — критический компонент, изменения не должны сломать production-деплой
5. **Unit-тесты:** Каждый извлечённый Python-модуль должен иметь ≥80% coverage

### Текущее состояние (baseline)

| Файл | LOC | Inline p3 | Роль | Риск |
|------|-----|:---:|------|:----:|
| `core/internal/deploy/deploy-project.sh` | 1183 | 2 | VPS-side deploy engine | 🔴 КРИТИЧЕСКИЙ |
| `core/internal/scaffold/add-vhost.sh` | 926 | 3 | Vhost template generation | 🟡 СРЕДНИЙ |
| `core/internal/scaffold/adopt-project.sh` | 906 | 2 | Project adoption wizard | 🟡 СРЕДНИЙ |
| `core/internal/bootstrap/issue-cert.sh` | 696 | 0 | acme.sh subprocess wrapper | 🟢 НИЗКИЙ |
| `core/internal/bootstrap/remote-cmd.sh` | 672 | 0 | SSH command builder | 🟡 СРЕДНИЙ |
| `core/internal/verify/verify-domains.sh` | 281 | 2 | Post-deploy verification | 🟢 НИЗКИЙ |

---

## Architecture Overview

### Superposition Analysis

Для группы из 6 скриптов рассмотрены 4 стратегии:

#### Option A: Полный Strangler-Fig для всех 6 [score: 8/10]

**Подход:** Каждый скрипт → Python-модуль бизнес-логики + shell-фасад (<150 LOC). Максимальное соответствие языковой политике.

**Trade-offs:**
- ➕ Полное устранение inline python3, повышение тестируемости
- ➕ Единообразие с Wave 4 (top-3)
- ➖ Высокий risk для deploy-project.sh (VPS-side, atomic deploy, rollback)
- ➖ Большой объём работы (~3500 LOC извлечения)

**Best when:** команда готова к полной миграции, есть staging-окружение для тестирования deploy-project

#### Option B: Прагматичный — Strangler-Fig для всех КРОМЕ issue-cert.sh [score: 9/10] ⭐

**Подход:** 5 скриптов → Python, issue-cert.sh — минимальная чистка (TRAP cert_orchestrator уже определяет его как shell subprocess)

**Trade-offs:**
- ➕ Ниже risk (issue-cert.sh не трогаем)
- ➕ deploy-project.sh — последняя волна, после верификации всех остальных
- ➖ issue-cert.sh остаётся с 696 LOC shell (но это осознанное решение — TRAP)

**Best when:** баланс risk/reward, соблюдение существующих архитектурных решений (TRAP)

#### Option C: Консервативный — только низкорисковые [score: 6/10]

**Подход:** Только verify-domains.sh + add-vhost.sh + adopt-project.sh. deploy-project.sh и remote-cmd.sh — отложить.

**Trade-offs:**
- ➕ Минимальный risk для production
- ➖ deploy-project.sh (1183 LOC) остаётся крупнейшим монолитом в проекте
- ➖ Не завершает языковую политику, оставляет 3 из 6 монолитов

#### Option D: Big-bang — единый Python Deploy Engine [score: 4/10]

**Подход:** Объединить deploy-project.sh + remote-cmd.sh + verify-domains.sh в единый `core/internal/deploy/deploy_engine.py`

**Trade-offs:**
- ➖ Высокий coupling между разнородными доменами (VPS-deploy vs SSH-proxy vs HTTP-verify)
- ➖ Риск регрессии — один баг ломает все три подсистемы
- ➖ Противоречит AI-First Architecture (DDD: разделяй по бизнес-доменам)

**Rejected:** нарушает принцип Small Simple Blocks и domain separation

#### Option E: Phased Rollout with Feature Flags [score: 7/10]

**Подход:** Каждая миграция оборачивается в `DEPLOY_V2_ENGINE` feature flag. Shell facade проверяет флаг → при false падает на старую shell-реализацию. Включает A/B тестирование: деплой 50% проектов через Python, 50% через shell. Мониторинг error rate, latency, rollback frequency — полное переключение при confidence >95%.

**Trade-offs:**
- ➕ Максимальная rollback safety — флаг отключается одним коммитом
- ➕ Объективные метрики для принятия решения о полном переходе
- ➡️ Feature flag overhead — код поддерживает обе реализации одновременно (Tech debt до удаления legacy)
- ➖ Раздувание shell facade: проверка флага + conditional dispatch удлиняют facade сверх лимита AC-1
- ➖ Не решает проблему тестируемости — legacy shell path остаётся нететированным

**Best when:** production-стабильность критичнее скорости миграции, есть observability-инфраструктура для A/B сравнения

#### Option F: Reverse Strangler — Python Core, Shell Plugins [score: 7/10]

**Подход:** Python `DeployOrchestrator` — центральный engine с day 1. Shell-скрипты сводятся к функциям-плагинам, вызываемым через `subprocess.run()` ТОЛЬКО для inherently shell-bound операций (docker CLI, printf %q, acme.sh). Flow control, error handling, state management — в Python.

**Trade-offs:**
- ➕ Чистая архитектурная граница: Python = бизнес-логика, shell = системные вызовы
- ➕ Полная тестируемость всей бизнес-логики с первого дня
- ➖ Большой upfront work — требуется спроектировать DeployOrchestrator до миграции первого скрипта
- ➖ Высокий risk: ошибка в архитектуре Python-engine блокирует все миграции (single point of failure на раннем этапе)
- ➖ Противоречит принципу Small Simple Blocks (один класс берет на себя 4+ домена)

**Best when:** команда имеет чёткое видение deploy-архитектуры и готова к upfront investment

### Multi-Dimensional Scoring Matrix

| Dimension | A (Full SF) | B (Pragmatic) | C (Conservative) | D (Big-bang) | E (Flagged) | F (Reverse) |
|-----------|:---:|:---:|:---:|:---:|:---:|:---:|
| Risk to production | 6 | 8 | 9 | 2 | 9 | 4 |
| Code quality gain | 9 | 8 | 4 | 6 | 8 | 10 |
| Implementation speed | 5 | 7 | 9 | 3 | 4 | 3 |
| Testability gain | 9 | 8 | 4 | 5 | 8 | 10 |
| Rollback safety | 6 | 7 | 10 | 2 | 10 | 5 |
| Team confidence | 7 | 9 | 7 | 3 | 8 | 5 |
| Lang policy compliance | 10 | 9 | 4 | 8 | 9 | 10 |
| **Composite** | **7.4** | **8.0** | **6.7** | **4.1** | **7.9** | **6.7** |

**Analysis:** Option B (Pragmatic Strangler-Fig) retains the highest composite score (8.0), followed closely by Option E (Flagged, 7.9). While Option E offers superior rollback safety (10 vs 7), it introduces facade bloat that conflicts with AC-1 shell size limits and defers the testability benefit. Option B provides the best balance of risk, speed, and code quality — consistent with the Wave 4 experience.

### Recommendation: Option B — Прагматичный Strangler-Fig (score: 8.0 composite)

**Обоснование:**
1. **Wave 4 precedent:** Option B уже успешно применён к топ-3 скриптам (4114→392 LOC, 204/210 tests pass). Процесс отлажен, риски известны.
2. **`issue-cert.sh`** уже под управлением `cert_orchestrator.py` (Python) — TRAP[DECISION] явно документирует shell subprocess by design. Миграция не даст прироста, но создаст risk регрессии cert renewal.
3. **`deploy-project.sh`** — самая сложная миграция, выносится ПОСЛЕДНЕЙ волной после верификации всех остальных. Корректный порядок (low→high risk) обеспечивает максимальную safety.
4. **Feature flags (Option E)** рассмотрены и отклонены из-за конфликта с AC-1 (facade bloat) и отсрочки testability gain.
5. **Reverse Strangler (Option F)** отклонён из-за high upfront risk и противоречия Small Simple Blocks.
6. **Composite победитель: Option B (8.0).** Option E (7.9) близок, но проигрывает по implementation speed и testability gain.

---

## Step-by-Step Data Flow

### Wave 1: verify-domains.sh (низкий риск, быстрая победа)

```
ДО:
  verify-domains.sh (281 LOC)
  ├── resolve_yaml() — 3-path search (bash, 50 строк)
  ├── get_expose_domains() — inline python3 -c import yaml (12 строк)
  ├── verify_domains() — curl + inline python3 JSON→bash + status-page (120 строк)
  └── main() — orchestration (30 строк)

ПОСЛЕ:
  verify-domains.sh (~60 LOC, shell-фасад)
  ├── parse_args → resolve NODE, PLATFORM_ROOT
  ├── python3 -m core.internal.verify.domain_verifier verify --node <n> --platform-root <path>
  └── exit с кодом из Python

  core/internal/verify/domain_verifier.py (~200 LOC, Python-модуль)
  ├── resolve_node_yaml(node, platform_root) → Path
  ├── get_expose_domains(yaml_path) → list[str]
  ├── verify_domain(domain, timeout) → VerifyResult
  ├── verify_status_page(platform_domain, email, password) → VerifyResult
  └── main(): orchestrate → list[VerifyResult] → exit 0|1
```

### Wave 2: add-vhost.sh + adopt-project.sh (средний риск, локальные операции)

```
ДО:
  add-vhost.sh (926 LOC)
  ├── read_project_yaml() — grep-based YAML parse
  ├── generate_vhost_body() — nginx template (SSL, proxy, headers)
  ├── render_all() — batch pipeline: parse→validate→render→nginx -t→atomic mv
  ├── check_duplicate_domains() — inline python3
  ├── nginx_t_harness() — docker-based validation
  └── read_node_yaml_projects() — частично делегирован vhost_yaml_reader.py

  adopt-project.sh (906 LOC)
  ├── generate_minimal_ai_platform_yaml() — YAML generation
  ├── simplify_deploy_yml() — CI workflow rewriting
  ├── validate_compose_networks() — inline python3 compose analysis
  ├── register_in_node_yaml() — yq/python3
  ├── gen_project_makefile() / gen_project_agents() — heredoc templates
  └── configure_vhost() → вызывает add-vhost.sh

ПОСЛЕ:
  add-vhost.sh (~150 LOC, shell-фасад)
  ├── parse_args → MODE, PROJECT_DIR, NODE_CONFIGS_DIR, RENDER_NODE
  ├── MODE=render-all → python3 -m core.internal.scaffold.vhost_renderer render-all --node <n> --node-configs-dir <path>
  ├── MODE=add → python3 -m core.internal.scaffold.vhost_renderer add --project-dir <path> --node-configs-dir <path>
  ├── MODE=remove → python3 -m core.internal.scaffold.vhost_renderer remove --project-dir <path> --node-configs-dir <path>
  └── exit с кодом из Python

  core/internal/scaffold/vhost_renderer.py (~500 LOC, Python-модуль)
  ├── read_project_yaml(project_dir) → ProjectConfig
  ├── read_node_yaml_projects(node_yaml_path) → list[ProjectEntry]
  ├── generate_vhost_body(fqdn, project_name, cert_domain) → str
  ├── check_duplicate_domains(entries) → void (raise on duplicate)
  ├── render_vhost(entry, cert_domain) → VhostFile
  ├── nginx_t_harness(temp_dir, nginx_version) → bool
  ├── render_all(node_yaml, node_configs_dir, overlay_dir) → RenderResult
  └── CLI: add/remove/render-all subcommands

  adopt-project.sh (~150 LOC, shell-фасад)
  ├── parse_args → PROJECT_DIR, PROJECT_NAME, PROJECT_ORG, ...
  ├── python3 -m core.internal.scaffold.project_adopter adopt --dir <path> [--force] [...]
  └── exit с кодом из Python

  core/internal/scaffold/project_adopter.py (~500 LOC, Python-модуль)
  ├── generate_minimal_ai_platform_yaml(project_dir, config) → Path
  ├── simplify_deploy_yml(project_dir, org, project_name) → bool
  ├── validate_compose_networks(compose_path, has_domain) → ValidationResult
  ├── register_in_node_yaml(node_yaml_path, project_config) → bool
  ├── configure_vhost(project_dir, domain, node_configs_dir) → bool
  ├── validate_org_against_node_yaml(node_yaml_path, org) → void (raise on mismatch)
  └── CLI: adopt subcommand
```

### Wave 3: remote-cmd.sh (средний риск, SSH-proxy)

```
ДО:
  remote-cmd.sh (672 LOC)
  ├── build_ssh_cmd() — printf %q command builder (сложная логика экспорта env vars)
  ├── build_update_ssh_cmd() — аналогично для update
  ├── build_converge_ssh_cmd() — аналогично для converge
  ├── execute_remote_update() — rsync core + build cmd + SSH exec
  ├── execute_remote_converge() — аналогично
  ├── execute_remote_reconcile() / execute_remote_reconcile_entrypoint()
  └── deliver_vhost_overlays() — rsync overlays

ПОСЛЕ:
  remote-cmd.sh (~200 LOC, shell-фасад)
  ├── build_ssh_cmd() — остаётся (printf %q — inherent shell)
  ├── build_update_ssh_cmd() — остаётся
  ├── build_converge_ssh_cmd() — остаётся
  ├── execute_remote_*() — делегируют Python для:
  │   ├── resolve_node_yaml (уже в node-resolver.sh)
  │   ├── extract_node_host (уже в node-resolver.sh)
  │   └── prepare_ssh_opts (остаётся shell)
  ├── deliver_vhost_overlays() → python3 -m core.internal.bootstrap.overlay_deliverer deliver --node <n>

  core/internal/bootstrap/overlay_deliverer.py (~150 LOC, Python-модуль)
  ├── deliver_vhost_overlays(node_name, platform_root, dry_run) → DeliveryResult
  └── CLI: deliver subcommand
```

### Wave 4: deploy-project.sh (критический VPS-компонент)

```
ДО:
  deploy-project.sh (1183 LOC)
  ├── parse_ssh_command() — частично Python (ssh_command_parser), частично shell (env stripping)
  ├── handle_deliver() — tar.gz payload validation
  ├── save_previous_image() — Docker inspect
  ├── pull_image_with_retry() — Docker pull + retry
  ├── atomic_up() — Docker compose up
  ├── _check_deploy_health() — health polling
  ├── tag_current() — Docker tag
  ├── perform_rollback() — atomic rollback
  ├── prune_old_images() — image cleanup
  ├── handle_first_deploy() — first deploy failure
  ├── capture_deploy_snapshot() — Docker compose ps/images
  ├── handle_remove() — project removal
  ├── handle_status() — JSON status report
  ├── _trigger_deploy_hooks() / _trigger_remove_hooks()
  └── main() — verb dispatch

ПОСЛЕ:
  deploy-project.sh (~200 LOC, shell-фасад — VPS forced-command entrypoint)
  ├── source libs (logging, docker, healthcheck, paths, yaml_read)
  ├── trap handlers (ERR → rollback, EXIT → finalize)
  ├── parse_ssh_command() → delegate to ssh_command_parser + platform_deliver parser
  ├── notify_hook() → тонкая обёртка
  ├── main():
  │   ├── verb=platform-deliver → python3 -m core.internal.deploy.payload_deliverer deliver <project> <org>
  │   ├── verb=deploy → python3 -m core.internal.deploy.deploy_engine deploy <project> <ref>
  │   ├── verb=remove → python3 -m core.internal.deploy.deploy_engine remove <project>
  │   ├── verb=status → python3 -m core.internal.deploy.deploy_engine status <project>
  │   └── verb=verify → exec verify.sh (already thin)
  └── exit с кодом из Python

  core/internal/deploy/deploy_engine.py (~600 LOC, Python-модуль)
  ├── class DeployEngine:
  │   ├── deploy(project, ref) → DeployResult
  │   │   ├── save_previous_image()
  │   │   ├── capture_deploy_snapshot()
  │   │   ├── docker_login()
  │   │   ├── pull_image(retries=3, backoff=[5,10,20])
  │   │   ├── atomic_up()
  │   │   ├── poll_health(service, check_fn, timeout=60, interval=2) → bool
  │   │   ├── tag_current()
  │   │   ├── prune_old_images(keep=3)
  │   │   └── trigger_deploy_hooks()
  │   ├── rollback(previous_image) → bool
  │   ├── remove(project) → RemoveResult
  │   ├── status(project) → StatusResult
  │   └── _validate_project_name(name) → bool
  └── CLI: deploy/remove/status subcommands

  core/internal/deploy/payload_deliverer.py (~150 LOC, Python-модуль)
  ├── class PayloadDeliverer:
  │   └── deliver(project, org) → DeliverResult
  │       ├── read stdin (1 MiB cap)
  │       ├── validate tar.gz (whitelist, path traversal, symlinks)
  │       └── atomic extract to PROJECTS_BASE/<org>/<project>
  └── CLI: deliver subcommand
```

### Wave 5 (минимальная): issue-cert.sh (чистка без Strangler-Fig)

```
Изменения:
  - Извлечь _is_le_cert(), _acme_verify_cert(), _is_subdomain() в Python (если есть смысл)
  - ИЛИ оставить как есть (TRAP cert_orchestrator — shell subprocess by design)
  - Никаких структурных изменений — только документирование TRAP

Решение: issue-cert.sh НЕ разбирается. TRAP[DECISION] в cert_orchestrator.py явно говорит:
это shell subprocess, вызываемый через subprocess.run(). Миграция на Python-вызовы
subprocess.run(["acme.sh", ...]) не даст прироста в тестируемости, но создаст risk
для cert renewal (cron + acme.sh). Оставляем как есть, добавляем TRAP-комментарий
о решении.
```

---

## Draft Code Graph

```
core/internal/
├── deploy/
│   ├── deploy-project.sh       # → ~200 LOC (shell facade)
│   ├── deploy_engine.py        # NEW ~600 LOC (deploy/remove/status/rollback engine)
│   └── payload_deliverer.py    # NEW ~150 LOC (tar.gz payload delivery)
├── scaffold/
│   ├── add-vhost.sh            # → ~150 LOC (shell facade)
│   ├── adopt-project.sh        # → ~150 LOC (shell facade)
│   ├── vhost_renderer.py       # NEW ~500 LOC (vhost generation, render-all, nginx harness)
│   └── project_adopter.py      # NEW ~500 LOC (project adoption, CI rewrite, compose validation)
├── bootstrap/
│   ├── remote-cmd.sh           # → ~200 LOC (shell facade, keeps printf %q builders)
│   └── overlay_deliverer.py    # NEW ~150 LOC (vhost overlay rsync delivery)
├── verify/
│   ├── verify-domains.sh       # → ~60 LOC (shell facade)
│   └── domain_verifier.py      # NEW ~200 LOC (YAML resolve + domain curl + status-page)
└── shared/
    └── ssh_command_parser.py   # EXISTS (DevPlan 081) — reused by deploy_engine

tests/unit/
├── test_deploy_engine.py       # NEW
├── test_payload_deliverer.py   # NEW
├── test_vhost_renderer.py      # NEW
├── test_project_adopter.py     # NEW
├── test_overlay_deliverer.py   # NEW
└── test_domain_verifier.py     # NEW
```

---

## Design Decisions

### ## @rationale D1: Порядок волн — от низкого риска к высокому

**Q:** Почему deploy-project.sh последний, а не первый (он самый большой)?

**A:** deploy-project.sh — VPS-side forced-command, атомарный деплой с rollback. Ошибка здесь = production outage. Все остальные скрипты либо локальные (add-vhost, adopt-project, verify-domains), либо не затрагивают состояние (remote-cmd — SSH proxy). Миграция deploy-project требует максимальной confidence в процессе Strangler-Fig. После успешных Waves 1-3 команда будет иметь: (а) отлаженный pipeline миграции, (б) работающие unit-тесты для паттерна "shell facade → Python module", (в) опыт тестирования Python-модулей с Docker-зависимостями (vhost_renderer уже тестирует docker-based nginx harness).

### ## @rationale D2: issue-cert.sh — осознанный пропуск

**Q:** Почему issue-cert.sh (696 LOC) не разбирается?

**A:** TRAP[DECISION] в `cert_orchestrator.py` (DevPlan 052) документирует: issue-cert.sh — shell subprocess, вызываемый через `subprocess.run()` из Python-оркестратора. Бизнес-логика оркестрации доменов, S3-кэша, выбора режима (DNS-01/HTTP-01) — уже в Python. issue-cert.sh отвечает только за acme.sh CLI interaction, которая inherently shell-bound. Замена `bash issue-cert.sh` на `subprocess.run(["acme.sh", "--issue", ...])` в Python не даст прироста тестируемости (acme.sh всё равно требует реального DNS API), но создаст risk для cron-based cert renewal. **Решение:** оставить как есть, добавить TRAP-комментарий с ссылкой на этот DevPlan.

### ## @rationale D3: remote-cmd.sh — command builders остаются в shell

**Q:** Почему `build_ssh_cmd()` / `build_update_ssh_cmd()` не переносятся в Python?

**A:** Эти функции используют `printf '%q'` — bash-builtin для shell-safe quoting, не имеющий прямого аналога в Python (`shlex.quote()` близок, но не идентичен для всех edge cases с env vars и спецсимволами). Бизнес-логика SSH proxy (resolve node → detect host → rsync core → exec SSH) извлекается в Python, но сами command builders остаются в shell. Это гибридный подход: shell отвечает за quoting, Python — за flow control и error handling.

### ## @rationale D4: add-vhost.sh использует существующий template_engine.py

**Q:** `generate_vhost_body()` генерирует nginx-конфиг. Почему не использовать `core/internal/template_engine.py`?

**A:** `template_engine.py` использует strict grammar `{{UPPER_SNAKE}}` для предотвращения коллизий с Go/Prometheus-шаблонами. Nginx-конфиг содержит `${host}`, `${request_uri}` — это nginx-переменные, не шаблонные placeholders. Использование `template_engine.py` (Jinja2 или strict regex) здесь не подходит — механизм шаблонизации nginx — это сам nginx. `generate_vhost_body()` остаётся template generator на Python (f-строки / string interpolation), но НЕ использует `template_engine.py`.

### ## @rationale D5: deploy-project.sh — rollback logic мигрирует как есть

**Q:** Atomic rollback — сложная логика с Docker. Как тестировать?

**A:** Логика rollback тестируется через unit-тесты с mocked Docker calls (`subprocess.run` mock). Интеграционный тест deploy pipeline требует staging-окружения (VPS или Docker-in-Docker). В scope данного DevPlan — unit-тесты. Интеграционные тесты — отдельная задача (см. §Future Work).

---

## $TASKS

### TASK-036A: Wave 1 — verify-domains.sh → domain_verifier.py
- **Owner:** Coder
- **Output:** `core/internal/verify/domain_verifier.py` (~200 LOC), обновлённый `core/internal/verify/verify-domains.sh` (~60 LOC)
- **Acceptance:** shell ≤60 LOC, 0 inline python3, `make test` зелёный, тесты в `tests/unit/test_domain_verifier.py`
- **Dependencies:** None
- **Complexity:** 3/10
- **Checkpoint:** `make test` зелёный, shell facade ≤60 LOC, 0 inline python3
- **Sign-off:** QA review VerificationReport для волны перед переходом к следующей

### TASK-036B: Wave 2a — add-vhost.sh → vhost_renderer.py
- **Owner:** Coder
- **Output:** `core/internal/scaffold/vhost_renderer.py` (~500 LOC), обновлённый `core/internal/scaffold/add-vhost.sh` (~150 LOC)
- **Acceptance:** shell ≤150 LOC, 0 inline python3, `make render-vhosts NODE=<test>` работает идентично, тесты в `tests/unit/test_vhost_renderer.py`
- **Dependencies:** None (не зависит от TASK-036A)
- **Complexity:** 6/10
- **Checkpoint:** `make test` зелёный, shell facade ≤150 LOC, 0 inline python3
- **Sign-off:** QA review VerificationReport для волны перед переходом к следующей

### TASK-036C: Wave 2b — adopt-project.sh → project_adopter.py
- **Owner:** Coder
- **Output:** `core/internal/scaffold/project_adopter.py` (~500 LOC), обновлённый `core/internal/scaffold/adopt-project.sh` (~150 LOC)
- **Acceptance:** shell ≤150 LOC, 0 inline python3, `make adopt-project DIR=<test>` работает идентично, тесты в `tests/unit/test_project_adopter.py`
- **Dependencies:** TASK-036B (использует vhost_renderer для configure_vhost)
- **Complexity:** 5/10
- **Checkpoint:** `make test` зелёный, shell facade ≤200 LOC, 0 inline python3
- **Sign-off:** QA review VerificationReport для волны перед переходом к следующей

### TASK-036D: Wave 3 — remote-cmd.sh → overlay_deliverer.py + cleanup
- **Owner:** Coder
- **Output:** `core/internal/bootstrap/overlay_deliverer.py` (~150 LOC), обновлённый `core/internal/bootstrap/remote-cmd.sh` (~200 LOC)
- **Acceptance:** shell ≤200 LOC, execute_remote_*() используют Python для resolve+host extraction, тесты в `tests/unit/test_overlay_deliverer.py`
- **Dependencies:** None (может идти параллельно с Wave 2)
- **Complexity:** 4/10
- **Checkpoint:** `make test` зелёный, shell facade ≤200 LOC, 0 inline python3
- **Sign-off:** QA review VerificationReport для волны перед переходом к следующей

### TASK-036E: Wave 4 — deploy-project.sh → deploy_engine.py + payload_deliverer.py
- **Owner:** Coder
- **Output:** `core/internal/deploy/deploy_engine.py` (~600 LOC), `core/internal/deploy/payload_deliverer.py` (~150 LOC), обновлённый `core/internal/deploy/deploy-project.sh` (~200 LOC)
- **Acceptance:** shell ≤200 LOC, 0 inline python3, `make deploy-project PROJECT=<test> NODE=<test>` работает идентично (dry-run), тесты в `tests/unit/test_deploy_engine.py`, `tests/unit/test_payload_deliverer.py`
- **Dependencies:** TASK-036A (использует domain_verifier для post-deploy verify), TASK-036D (использует overlay_deliverer)
- **Complexity:** 9/10 (CRITICAL — VPS component)
- **Checkpoint:** `make test` зелёный, shell facade ≤200 LOC, 0 inline python3; staging deploy на тестовой VPS пройден
- **Sign-off:** QA review VerificationReport для волны + подтверждение staging-теста перед merge

### TASK-036F: Wave 5 — issue-cert.sh — документирование и минимальная чистка
- **Owner:** Coder
- **Output:** TRAP-комментарий в issue-cert.sh и cert_orchestrator.py с ссылкой на DevPlan 036
- **Acceptance:** документировано решение об осознанном пропуске
- **Dependencies:** None
- **Complexity:** 1/10
- **Checkpoint:** TRAP-комментарий добавлен, issue-cert.sh не изменён структурно
- **Sign-off:** QA review VerificationReport для волны

### TASK-036G: Integration verification — полный прогон
- **Owner:** QA
- **Output:** `02-VerificationReport.md`
- **Acceptance:** `make test && make gate MODE=fast` зелёные, все 6 shell-фасадов ≤200 LOC (≤150 для non-VPS), 0 inline python3
- **Dependencies:** TASK-036A, TASK-036B, TASK-036C, TASK-036D, TASK-036E
- **Complexity:** 3/10
- **Checkpoint:** `make test && make gate MODE=fast` зелёные, все 6 shell-фасадов соответствуют AC-1
- **Sign-off:** QA выносит семантический вердикт (DRIFTED, ALIGNED или DEGRADED)

---

## $PARALLEL_GROUPS

### Group 1 (independent, no shared files)
- **Tasks:** TASK-036A (verify-domains), TASK-036F (issue-cert doc)
- **Command:** `coder Read DevPlan.md, implement Wave 1: TASK-036A, TASK-036F`

### Group 2 (independent from Group 1, shared files between B and C)
- **Tasks:** TASK-036B (add-vhost), TASK-036D (remote-cmd) — параллельно
- **Then:** TASK-036C (adopt-project) — зависит от TASK-036B
- **Command Group 2A:** `coder Read DevPlan.md, implement Wave 2a: TASK-036B, TASK-036D`
- **Command Group 2B:** `coder Read DevPlan.md, implement Wave 2b: TASK-036C`

### Group 3 (depends on Groups 1+2)
- **Tasks:** TASK-036E (deploy-project)
- **Command:** `coder Read DevPlan.md, implement Wave 3: TASK-036E`

### Group 4 (verification, depends on all)
- **Tasks:** TASK-036G
- **Command:** `qa Read DevPlan.md + VerificationReport.md, verify Wave 5: TASK-036G`

---

## Task Dependency Graph

```
TASK-036A (verify-domains) ─────────────────────────────────────────┐
TASK-036F (issue-cert doc) ─────────────────────────────────────────┤
                                                                     │
TASK-036B (add-vhost) ─────┬────────────────────────────────────────┤
TASK-036D (remote-cmd) ────┤ (parallel)                              │
                           │                                         │
TASK-036C (adopt-project) ─┘ (depends on B) ────────────────────────┤
                                                                     │
                                                                     ▼
                                                              TASK-036E (deploy-project)
                                                                     │
                                                                     ▼
                                                              TASK-036G (integration verify)
```

---

## Acceptance Criteria Summary

| ID | Критерий | Метод проверки |
|----|----------|---------------|
| AC-1 | shell ≤150 LOC (≤200 для VPS и remote-cmd.sh — printf %q command builders остаются в shell per D3) | `wc -l` на каждом shell-файле |
| AC-2 | 0 inline `python3 -c` / `<<PYEOF` | `grep "python3 -c\|<<PYEOF"` → 0 matches |
| AC-3 | Unit-тесты ≥80% coverage | `pytest --cov` |
| AC-4 | Существующие тесты зелёные | `make test` |
| AC-5 | Gate зелёный | `make gate MODE=fast` |
| AC-6 | Production deploy не сломан | Dry-run: `make deploy-project ... --dry-run` |

---

## File Manifest

### Modified files
| Файл | До (LOC) | После (LOC) | Сокращение |
|------|----------|-------------|------------|
| `core/internal/deploy/deploy-project.sh` | 1183 | ~200 | 83% |
| `core/internal/scaffold/add-vhost.sh` | 926 | ~150 | 84% |
| `core/internal/scaffold/adopt-project.sh` | 906 | ~150 | 83% |
| `core/internal/bootstrap/issue-cert.sh` | 696 | ~680 | 2% (TRAP doc only) |
| `core/internal/bootstrap/remote-cmd.sh` | 672 | ~200 | 70% |
| `core/internal/verify/verify-domains.sh` | 281 | ~60 | 79% |
| **Total** | **4664** | **~1440** | **69%** |

### New files
| Файл | LOC | Назначение |
|------|-----|-----------|
| `core/internal/deploy/deploy_engine.py` | ~600 | Atomic deploy/rollback/remove/status engine |
| `core/internal/deploy/payload_deliverer.py` | ~150 | Tar.gz payload delivery with validation |
| `core/internal/scaffold/vhost_renderer.py` | ~500 | Vhost generation, render-all, nginx harness |
| `core/internal/scaffold/project_adopter.py` | ~500 | Project adoption wizard |
| `core/internal/bootstrap/overlay_deliverer.py` | ~150 | Vhost overlay rsync delivery |
| `core/internal/verify/domain_verifier.py` | ~200 | Post-deploy domain verification |
| `tests/unit/test_deploy_engine.py` | ~300 | Unit tests for deploy engine |
| `tests/unit/test_payload_deliverer.py` | ~100 | Unit tests for payload deliverer |
| `tests/unit/test_vhost_renderer.py` | ~300 | Unit tests for vhost renderer |
| `tests/unit/test_project_adopter.py` | ~300 | Unit tests for project adopter |
| `tests/unit/test_overlay_deliverer.py` | ~100 | Unit tests for overlay deliverer |
| `tests/unit/test_domain_verifier.py` | ~150 | Unit tests for domain verifier |

---

## $TEST_SPEC

| Test file | Test function | Scenario | Module under test |
|-----------|---------------|----------|-------------------|
| `tests/unit/test_domain_verifier.py` | `test_resolve_yaml_path1_local` | node.yaml найден по path 1 (platform-local) | `domain_verifier.resolve_node_yaml()` |
| `tests/unit/test_domain_verifier.py` | `test_resolve_yaml_path2_org` | node.yaml найден по path 2 (org repos) | `domain_verifier.resolve_node_yaml()` |
| `tests/unit/test_domain_verifier.py` | `test_resolve_yaml_not_found` | node.yaml не найден → raise | `domain_verifier.resolve_node_yaml()` |
| `tests/unit/test_domain_verifier.py` | `test_get_expose_domains_empty` | node.yaml без expose:true проектов | `domain_verifier.get_expose_domains()` |
| `tests/unit/test_domain_verifier.py` | `test_get_expose_domains_with_domains` | node.yaml с expose:true + domain | `domain_verifier.get_expose_domains()` |
| `tests/unit/test_vhost_renderer.py` | `test_generate_vhost_body_platform_domain` | Генерация vhost для platform domain (wildcard cert) | `vhost_renderer.generate_vhost_body()` |
| `tests/unit/test_vhost_renderer.py` | `test_generate_vhost_body_personal_domain` | Генерация vhost для personal domain (own cert) | `vhost_renderer.generate_vhost_body()` |
| `tests/unit/test_vhost_renderer.py` | `test_check_duplicate_domains_no_dup` | Нет дубликатов FQDN → pass | `vhost_renderer.check_duplicate_domains()` |
| `tests/unit/test_vhost_renderer.py` | `test_check_duplicate_domains_has_dup` | Дубликат FQDN → raise DuplicateDomainError | `vhost_renderer.check_duplicate_domains()` |
| `tests/unit/test_vhost_renderer.py` | `test_read_project_yaml_expose_true` | Парсинг ai-platform.yaml с expose:true | `vhost_renderer.read_project_yaml()` |
| `tests/unit/test_vhost_renderer.py` | `test_read_project_yaml_no_expose` | Парсинг ai-platform.yaml без expose → skip | `vhost_renderer.read_project_yaml()` |
| `tests/unit/test_project_adopter.py` | `test_generate_minimal_yaml` | Генерация ai-platform.yaml для нового проекта | `project_adopter.generate_minimal_ai_platform_yaml()` |
| `tests/unit/test_project_adopter.py` | `test_validate_compose_networks_has_proxy` | Compose с proxy-net external → pass | `project_adopter.validate_compose_networks()` |
| `tests/unit/test_project_adopter.py` | `test_validate_compose_networks_no_proxy` | Compose без proxy-net → fail | `project_adopter.validate_compose_networks()` |
| `tests/unit/test_project_adopter.py` | `test_validate_org_mismatch` | Org не совпадает с node.yaml context → raise | `project_adopter.validate_org_against_node_yaml()` |
| `tests/unit/test_overlay_deliverer.py` | `test_deliver_no_overlays` | Нет .conf файлов → skip gracefully | `overlay_deliverer.deliver_vhost_overlays()` |
| `tests/unit/test_overlay_deliverer.py` | `test_deliver_dry_run` | Dry-run mode → печатает команду, не выполняет | `overlay_deliverer.deliver_vhost_overlays()` |
| `tests/unit/test_deploy_engine.py` | `test_validate_project_name_valid` | Валидное имя проекта → pass | `deploy_engine._validate_project_name()` |
| `tests/unit/test_deploy_engine.py` | `test_validate_project_name_traversal` | Имя с `..` → reject | `deploy_engine._validate_project_name()` |
| `tests/unit/test_deploy_engine.py` | `test_validate_project_name_slash` | Имя с `/` → reject | `deploy_engine._validate_project_name()` |
| `tests/unit/test_deploy_engine.py` | `test_prune_old_images_below_limit` | Изображений ≤ KEEP → no-op | `deploy_engine.prune_old_images()` (mocked docker) |
| `tests/unit/test_deploy_engine.py` | `test_prune_old_images_above_limit` | Изображений > KEEP → удаление старых | `deploy_engine.prune_old_images()` (mocked docker) |
| `tests/unit/test_deploy_engine.py` | `test_remove_idempotent` | Повторный remove → no-op, exit 0 | `deploy_engine.remove()` (mocked docker) |
| `tests/unit/test_deploy_engine.py` | `test_status_not_found` | Статус несуществующего проекта → null | `deploy_engine.status()` |
| `tests/unit/test_payload_deliverer.py` | `test_validate_whitelist_ok` | Валидные файлы (compose, yaml, env) → pass | `payload_deliverer._validate_content()` |
| `tests/unit/test_payload_deliverer.py` | `test_validate_whitelist_reject` | Невалидный файл → reject | `payload_deliverer._validate_content()` |
| `tests/unit/test_payload_deliverer.py` | `test_validate_symlink_reject` | Symlink в payload → reject | `payload_deliverer._validate_content()` |
| `tests/unit/test_payload_deliverer.py` | `test_validate_size_cap` | Payload > 1 MiB → reject | `payload_deliverer._read_payload()` |

$TEST_SPEC: 28 tests specified (4 modules, 6 test files)

---

## Risk Assessment & Mitigations

| Risk | Severity | Mitigation |
|------|----------|------------|
| deploy-project.sh regression → production outage | 🔴 CRITICAL | Wave 4 — последняя, после верификации Waves 1-3; dry-run mode; staging-тестирование на тестовой VPS; Python-модуль с mocked Docker для unit-тестов |
| remote-cmd.sh: printf %q несовместимость с Python shlex.quote() | 🟡 MEDIUM | Command builders остаются в shell; Python управляет flow control |
| add-vhost.sh: nginx template regression → неправильные vhost'ы | 🟡 MEDIUM | Snapshot-тесты для вывода generate_vhost_body(); nginx -t harness в CI |
| adopt-project.sh: yq/py3 fallback regression | 🟢 LOW | Python-модуль использует PyYAML (уже в проекте), без внешних зависимостей |
| issue-cert.sh: случайная поломка cron renewal | 🟢 LOW | НЕ трогаем — только TRAP-документирование |
| Cross-wave dependency: TASK-036C зависит от TASK-036B | 🟡 MEDIUM | TASK-036C стартует только после успешного TASK-036B; vhost_renderer API стабилен |
| Pre-existing test_gate_deploy_paths.py:151 gate failure | 🟢 LOW | Документировано как known pre-existing issue (BASELINE-1); не связано с DevPlan 036; рекомендуется исправить до Wave 4 (deploy-project) чтобы избежать путаницы при интерпретации gate-результатов |

---

## Integration Test Plan

### Staging Verification (per wave)

| Wave | Script | Staging Test | Pass Criteria |
|------|--------|-------------|---------------|
| 1 | verify-domains | `make verify NODE=<test>` | All domains resolve, status-page accessible |
| 2a | add-vhost | `make render-vhosts NODE=<test>` | All vhosts render, nginx -t passes |
| 2b | adopt-project | `make adopt-project DIR=/tmp/test-project` | Project registered, vhost configured |
| 3 | remote-cmd | `make node-update NODE=<test>` | Update completes, healthcheck green |
| 4 | deploy-project | `make deploy-project PROJECT=<test> NODE=<test> --dry-run` → затем реальный деплой staging-проекта | Deploy succeeds, rollback works, healthcheck passes |

### CI Gate: wave gate per PR

После каждой волны — отдельный PR с обязательным `make gate MODE=fast`.
Wave 4 (deploy-project) дополнительно требует staging deploy на тестовой VPS перед merge.

---

## Rollback Strategy

| Wave | Rollback Method | Recovery Time |
|------|----------------|:---:|
| 1 | `git revert` → shell facade restored | <5 min |
| 2a | `git revert` → shell facade restored + regenerate vhosts | <10 min |
| 2b | `git revert` + ручная регистрация если adopt был прерван | <15 min |
| 3 | `git revert` → node-update восстанавливается | <10 min |
| 4 | ⚠️ deploy-project: `git revert` + deploy старого shell на VPS через `make bootstrap-node NODE=<test> --force` | <30 min |

При любом regression на production: немедленный `git revert` merge-коммита волны.

---

## Future Work (out of scope)

1. **Интеграционные тесты deploy_engine:** требуют Docker-in-Docker или staging VPS — отдельная задача
2. **issue-cert.sh полная миграция:** если acme.sh получит Python API — пересмотреть решение D2
3. **Полная дедупликация node-resolver:** `resolve_node_yaml()` дублируется в 4+ скриптах — кандидат на shared Python-модуль (отдельный DevPlan)
4. **Docker operations library:** `save_previous_image()`, `pull_image_with_retry()`, `prune_old_images()` — общие для нескольких скриптов, кандидаты на `core/internal/shared/docker_ops.py`

---

## TRAP Inventory (post-migration)

После миграции все существующие TRAP из shell-файлов переносятся в соответствующие Python-модули в формате docstring-комментариев:

```python
# ⚠️ TRAP[BUG] · 2026-07-18 · P1 · Deploy reports 'failed' despite success (B1)
# · Symptom: ... · Root: ... · Fix: ... · Prevention: ...
```

Плюс новые TRAP:

```python
# 🧐 TRAP[DECISION] · 2026-07-26 · — · Wave 5: deploy-project Strangler-Fig migrated to Python
# · Rejected: keeping deploy logic in shell (risk: 1183 LOC monolith with 3 inline python3 blocks)
# · Reason: языковая политика (AGENTS.md), тестируемость, дедупликация с ssh_command_parser
# · Rev: если Python deploy_engine вызывает >10% latency vs shell → профилировать и оптимизировать
```

```python
# 📝 TRAP[DEBT] · 2026-07-26 · LO · issue-cert.sh остаётся shell subprocess — осознанно пропущен
# · Observed: 696 LOC чистого bash для acme.sh взаимодействия
# · Suspected: TRAP cert_orchestrator уже определяет это как shell subprocess by design
# · Impact: при изменении acme.sh CLI может потребоваться миграция на Python subprocess.run
# · When: during Wave 5 Strangler-Fig — deferred, out of scope per DevPlan 036 D2
```

---

## Next Steps

### Wave 1 (independent, low risk)
```
coder Read .ai/plans/036-wave5-strangler-shell-monoliths/01-DevPlan.md, implement Wave 1: TASK-036A, TASK-036F
```

### Wave 2a (independent from Wave 1)
```
coder Read .ai/plans/036-wave5-strangler-shell-monoliths/01-DevPlan.md, implement Wave 2a: TASK-036B, TASK-036D
```

### Wave 2b (depends on Wave 2a)
```
coder Read .ai/plans/036-wave5-strangler-shell-monoliths/01-DevPlan.md, implement Wave 2b: TASK-036C
```

### Wave 3 (depends on Waves 1+2)
```
coder Read .ai/plans/036-wave5-strangler-shell-monoliths/01-DevPlan.md, implement Wave 3: TASK-036E
```

### Wave 4 (verification)
```
qa Read .ai/plans/036-wave5-strangler-shell-monoliths/01-DevPlan.md, verify Wave 5: TASK-036G
```

$END_DEVPLAN
