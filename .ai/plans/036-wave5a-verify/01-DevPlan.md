$START_DEVPLAN

# DevPlan 036A — Wave 5a: Strangler-Fig verify-domains.sh + issue-cert.sh TRAP Doc

$ARTIFACT_CONTRACT
- **PURPOSE:** Strangler-Fig миграция verify-domains.sh (281→60 LOC shell-фасад + ~200 LOC Python-модуль domain_verifier.py) и документирование осознанного пропуска issue-cert.sh (TRAP cert_orchestrator — shell subprocess by design).
- **DESCRIPTION:** Извлечение бизнес-логики (resolve YAML, parse expose:true domains, curl HTTP-верификация, status-page health check) из verify-domains.sh в `core/internal/verify/domain_verifier.py`. issue-cert.sh не изменяется структурно — только добавление TRAP-комментария с ссылкой на DevPlan 036 D2. Это первая (низкорисковая) волна общей программы Strangler-Fig DevPlan 036.
- **RATIONALE:** Выполнение языковой политики (AGENTS.md: новый код — Python), устранение 2 inline `python3 -c` блоков в verify-domains.sh, повышение тестируемости domain verification логики до unit-уровня. issue-cert.sh осознанно пропущен — вся оркестрация уже в cert_orchestrator.py (Python), скрипт отвечает только за acme.sh CLI interaction (inherently shell-bound).
- **ACCEPTANCE_CRITERIA:**
  - AC-1: verify-domains.sh shell-фасад ≤60 LOC, 0 inline `python3 -c` / `<<PYEOF`
  - AC-2: `core/internal/verify/domain_verifier.py` — 4 business-logic функции + main() CLI (resolve_node_yaml, get_expose_domains, verify_domain, verify_status_page, main) перенесены с сохранением семантики; main() покрывается интеграционно через 11 unit-тестов
  - AC-3: `make verify NODE=<test>` работает идентично до и после миграции
  - AC-4: Unit-тесты в `tests/unit/test_domain_verifier.py` — ≥6 тестов, все зелёные
  - AC-5: `make test MARKER=unit` зелёный (domain_verifier тесты)
  - AC-6: `make gate MODE=fast` зелёный (весь проект)
  - AC-7: issue-cert.sh — добавлен TRAP-комментарий с ссылкой на DevPlan 036 D2, без структурных изменений
- **IMPLEMENTS:** Wave 5a (Wave 1 + Wave 5) из DevPlan 036 — TASK-036A (verify-domains Strangler-Fig) + TASK-036F (issue-cert TRAP doc)
- **IMPACTS:**
  - `core/internal/verify/verify-domains.sh` — 281→~60 LOC (shell facade)
  - `core/internal/verify/domain_verifier.py` — NEW ~200 LOC (Python module)
  - `core/internal/bootstrap/issue-cert.sh` — 696→~700 LOC (+4 строки TRAP-комментария)
  - `tests/unit/test_domain_verifier.py` — NEW ~150 LOC
  - `core/entrypoints/verify.sh` — БЕЗ изменений (уже делегирует verify-domains.sh)
- **REQUIRES:**
  - Python ≥3.10, `pytest`, `pyyaml` (уже в проекте)
  - `core/lib/logging.sh` (source в verify-domains.sh)
  - Никаких внешних зависимостей (только stdlib: yaml, json, sys, pathlib, subprocess, logging)
$END_ARTIFACT_CONTRACT

---

## Debt Intake

### TRAP Audit: verify-domains.sh

| # | TRAP | Строка | Статус |
|---|------|--------|--------|
| 1 | TRAP[BUG] · 2026-07-24 · P2 · status-page URL mismatch | L194-198 | **IN_SCOPE** — переносится в `domain_verifier.py::verify_status_page()` как docstring-комментарий |

**Детали TRAP[BUG] status-page URL mismatch:**
```
# ⚠️ TRAP[BUG] · 2026-07-24 · P2 · status-page URL mismatch
# · Symptom: curl https://tronyx.ru/health → nginx overlay proxied to tronyx-site project → 500
# · Root: status-page lives on platform.tronyx.ru (platform-vhost.conf), not apex domain
# · Fix: use platform.${PLATFORM_DOMAIN}/health instead of ${PLATFORM_DOMAIN}/health
```
Переносится как docstring в `verify_status_page()` с сохранением всех полей (Symptom, Root, Fix).

### TRAP Audit: issue-cert.sh

| # | TRAP | Строка | Статус |
|---|------|--------|--------|
| 1 | TRAP[DECISION] cert_orchestrator — shell subprocess by design (module contract) | L8-9 | **DEFER** — осознанное решение, не требует изменений |
| 2 | TRAP[DECISION] D1 — DNS-01 primary, HTTP-01 graceful degradation | L23-26 | **DEFER** — бизнес-логика оркестрации в cert_orchestrator.py |
| 3 | TRAP[BUG] P0 · mkcert certs survived bootstrap | L48-55 | **DEFER** — функция _is_le_cert() уже исправляет, не в скоупе |
| 4 | TRAP[BUG] P0 · FALSE DIAGNOSIS zone_manager_unavailable | L109-123 | **DEFER** |
| 5 | TRAP[BUG] P2 · acme.sh basename bug — PID in temp dir | L133-149 | **DEFER** |
| 6 | TRAP[BUSINESS] HI · API key cleaned from disk after use | L150-152 | **DEFER** |
| 7 | TRAP[DECISION] · HTTP-01 fallback | L240-243 | **DEFER** |
| 8 | TRAP[BUG] P1 · Early exit blocked project domains | L604-609 | **DEFER** |
| 9 | TRAP[DECISION] · exit → return in main() | L610-613 | **DEFER** |
| 10 | TRAP[DECISION] · issue-cert.sh main() preserved as CLI debug entrypoint | L690-695 | **DEFER** — это именно то решение, которое мы документируем |

**Все 10 TRAP в issue-cert.sh — DEFER.** Единственное изменение: добавление нового TRAP-комментария с ссылкой на DevPlan 036 D2 (см. §Design Decision D2).

---

## Requirements Analysis

### Ключевые критерии успеха

1. **Zero inline python3:** Устранить 2 inline-блока `python3 -c` в verify-domains.sh (строки 106-122 и 141-146)
2. **Shell-фасад ≤60 LOC:** verify-domains.sh — только оркестрация (parse args → call Python → exit)
3. **Семантическая эквивалентность:** `make verify NODE=<test>` даёт идентичный результат (те же HTTP-статусы, те же exit codes, тот же stdout-формат)
4. **TRAP-сохранность:** TRAP[BUG] status-page URL mismatch перенесён в Python-модуль без потери полей
5. **Unit-тесты ≥6:** Покрывают resolve_node_yaml (3 пути), get_expose_domains (с доменами и без), verify_domain (HTTP 200 и connection failed), verify_status_page (OK и no-creds skip)

### Текущее состояние (baseline)

| Файл | LOC | Inline p3 | Роль | Риск миграции |
|------|-----|:---:|------|:---:|
| `core/internal/verify/verify-domains.sh` | 281 | 2 | Post-deploy HTTP verification | 🟢 НИЗКИЙ |
| `core/internal/bootstrap/issue-cert.sh` | 696 | 0 | acme.sh subprocess wrapper | 🟢 НИЗКИЙ (только документирование) |

---

## Architecture Overview

### Superposition Analysis — verify-domains.sh Migration

Для verify-domains.sh (281 LOC, низкий risk) рассмотрены 4 стратегии:

#### Option A: Полный Strangler-Fig [score: 9/10] ⭐

**Подход:** Извлечь ВСЮ бизнес-логику (resolve_yaml, get_expose_domains, verify_domains + status-page) в `domain_verifier.py`. Shell остаётся тонким фасадом (~60 LOC): parse args → `python3 domain_verifier.py verify --node <n> --platform-root <path>` → exit с кодом из Python.

**Trade-offs:**
- ➕ Полное устранение inline python3 (2 блока), максимальное соответствие языковой политике
- ➕ Unit-тестируемость всей бизнес-логики (не только inline-блоки, но и resolve_yaml, curl-логика)
- ➕ Единообразие с Wave 4 (top-3) — паттерн «Python модуль + shell фасад» уже отлажен
- ➕ Низкий risk — verify-domains вызывается ТОЛЬКО локально (`make verify`), никогда на VPS
- ➖ Требует написания ~200 LOC Python + ~150 LOC тестов (умеренные трудозатраты)

**Best when:** low-risk script, inline python3 присутствует, shell-функции простые и самодостаточные.

#### Option B: Inline-only извлечение [score: 6/10]

**Подход:** Извлечь ТОЛЬКО 2 inline `python3 -c` блока в отдельные Python-функции. Bash-функции (resolve_yaml — 56 строк, verify_domains — curl loop) оставить в shell. Shell вызывает Python для YAML-парсинга и JSON→bash конвертации.

**Trade-offs:**
- ➕ Минимальные изменения — только 2 inline-блока уходят в Python
- ➕ Меньше кода для review/тестирования
- ➖ Shell-скрипт остаётся ~220 LOC — не соответствует AC-1 (≤60 LOC)
- ➖ Смешанная ответственность: часть логики в shell, часть в Python — усложняет отладку
- ➖ Не решает проблему тестируемости curl-логики и status-page health check
- ➖ Противоречит DDD-принципу: один домен (verify) размазан по двум языкам

**Rejected:** полумера — не даёт тестируемости curl-логики, оставляет shell-скрипт >200 LOC.

#### Option C: Оставить как есть [score: 4/10]

**Подход:** verify-domains.sh остаётся без изменений. Добавить только TRAP-комментарий о том, что скрипт работает стабильно и миграция отложена.

**Trade-offs:**
- ➕ Ноль трудозатрат
- ➖ Не соответствует языковой политике (AGENTS.md)
- ➖ 2 inline `python3 -c` остаются (нарушение языковой политики)
- ➖ Пропущенная возможность — verify-domains самый низкорисковый из 6 скриптов DevPlan 036

**Rejected:** противоречит языковой политике и решению DevPlan 036 Option B.

#### Option D: Merge в entrypoint verify.sh [score: 5/10]

**Подход:** Упразднить verify-domains.sh полностью. Бизнес-логику извлечь в domain_verifier.py. entrypoints/verify.sh вызывает Python напрямую (без промежуточного verify-domains.sh).

**Trade-offs:**
- ➕ Устраняет один уровень indirection (entrypoint → internal → Python → entrypoint → internal → Python напрямую)
- ➖ Нарушает layered architecture: entrypoints/ НЕ должны содержать бизнес-логику или прямые вызовы Python-модулей (AGENTS.md: entrypoints → internal/)
- ➖ Увеличивает entrypoints/verify.sh с 89 до ~100 LOC, приближая к лимиту thin-wrapper (150 LOC)
- ➖ Ломает существующий контракт: другие скрипты (deploy-project.sh post-deploy) могут вызывать verify-domains.sh напрямую

**Rejected:** нарушает layered architecture и существующий контракт вызова.

### Multi-Dimensional Scoring Matrix

| Dimension | A (Full SF) | B (Inline-only) | C (Leave as-is) | D (Merge entrypoint) |
|-----------|:---:|:---:|:---:|:---:|
| Lang policy compliance | 10 | 6 | 2 | 10 |
| Testability gain | 10 | 4 | 0 | 10 |
| Architectural consistency | 10 | 4 | 0 | 3 |
| Implementation effort | 7 | 9 | 10 | 6 |
| Rollback safety | 10 | 9 | 10 | 9 |
| **Composite** | **9.4** | **6.4** | **4.4** | **7.6** |

### Recommendation: Option A — Полный Strangler-Fig (score: 9.4 composite)

**Обоснование:**
1. **Wave 4 precedent:** точно такой же паттерн применён к deploy-modules, converge, node-lifecycle — 4114→392 LOC, проверен на production
2. **Низкий risk:** verify-domains вызывается только локально через `make verify`, клиентская HTTP-верификация, никогда не выполняется на VPS
  3. **Измеримый выигрыш:** 281→60 LOC shell (−79%), 0 inline python3, 11 unit-тестов вместо 0
4. **Консистентность:** единый подход для всех скриптов DevPlan 036
5. **Option B отброшен:** полумера — не решает проблему тестируемости curl-логики, противоречит DDD
6. **Option C отброшен:** противоречит языковой политике
7. **Option D отброшен:** нарушает layered architecture (entrypoints → internal контракт)

---

## Step-by-Step Data Flow

### verify-domains.sh: ДО → ПОСЛЕ

```
ДО (281 LOC, 2 inline python3 блокa):
 verify-domains.sh
 ├── source lib/logging.sh
 ├── FUNC resolve_yaml() — 3-path search (bash, 56 строк)
 │   ├── Path 1: platform-local ({PLATFORM_ROOT}/node-configs/{node}/node.yaml)
 │   ├── Path 2: org repos ($HOME/projects/*/node-configs/{node}/node.yaml)
 │   └── Path 3: VPS fallback (/opt/node-configs/{node}/node.yaml)
 ├── FUNC get_expose_domains() — inline python3 -c import yaml (12 строк)
 │   └── Parse node.yaml → filter expose:true → extract domain → JSON array
 ├── FUNC verify_domains() — curl + inline python3 JSON→bash + status-page (120 строк)
 │   ├── Inline python3: JSON array → bash array (null-delimited)
 │   ├── curl loop: https://{domain} --max-time {timeout}
 │   ├── HTTP 200 check → pass/warn per domain
 │   └── Status-page health check (platform.{domain}/health, Basic Auth)
 └── FUNC main() — orchestrate (30 строк)
     └── resolve → parse → verify → exit with code

ПОСЛЕ (~60 LOC shell facade + ~200 LOC Python):
 verify-domains.sh (~60 LOC, shell-фасад)
 ├── source lib/logging.sh (для log_imp совместимости с Python logging)
 ├── parse_args → NODE, PLATFORM_ROOT, CURL_TIMEOUT
 ├── python3 -m core.internal.verify.domain_verifier verify \
 │     --node {NODE} --platform-root {PLATFORM_ROOT} --curl-timeout {CURL_TIMEOUT}
 └── exit с кодом из Python

 core/internal/verify/domain_verifier.py (~200 LOC, Python-модуль)
 ├── @dataclass VerifyResult(domain: str, status: str, http_code: int|None, error: str|None)
 ├── resolve_node_yaml(node: str, platform_root: Path) → Path
 │   ├── Path 1: platform_root / "node-configs" / node / "node.yaml"
 │   ├── Path 2: HOME / "projects" / glob(*) / "node-configs" / node / "node.yaml"
 │   └── Path 3: Path("/opt/node-configs") / node / "node.yaml"
 │   └── raise FileNotFoundError with searched paths if none found
 ├── get_expose_domains(yaml_path: Path) → list[str]
 │   └── yaml.safe_load → iterate projects[expose=true] → collect domain
 ├── verify_domain(domain: str, timeout: int) → VerifyResult
 │   └── subprocess.run curl -sS -o /dev/null -w '%{http_code}' --max-time {t} https://{domain}
 │   └── Parse HTTP code → VerifyResult(pass/fail/connection_error)
 ├── verify_status_page(platform_domain: str, email: str, password: str) → VerifyResult
 │   └── curl https://platform.{domain}/health -u {email}:{password} --max-time 30
 │   └── TRAP[BUG] status-page URL: platform subdomain, NOT apex (DevPlan 051 P2)
 └── main(): argparse CLI → orchestrate → list[VerifyResult] → exit 0|1
     └── IMP:9 logs: "ALL DOMAINS PASS" / "SOME DOMAINS FAILED"
```

### issue-cert.sh: БЕЗ изменений (TRAP doc only)

```
Изменения (4 строки):
  issue-cert.sh (~700 LOC)
  └── + # ⚠️ TRAP[DECISION] · 2026-07-26 · HI · Wave 5a: issue-cert.sh осознанно пропущен
      + # · Rejected: Python-порт acme.sh CLI interaction
      + # · Reason: TRAP cert_orchestrator (DevPlan 052) определяет это как shell subprocess by design.
      + #   Бизнес-логика оркестрации уже в cert_orchestrator.py. @see DevPlan 036A D2.
```

**Вызов verify-domains.sh НЕ меняется:**
- `make verify NODE=<node>` → `core/entrypoints/verify.sh` → `core/internal/verify/verify-domains.sh` → `python3 domain_verifier.py`
- `deploy-project.sh` post-deploy hook: вызов verify-domains.sh напрямую продолжает работать (shell facade)
- Обратная совместимость: все существующие caller'ы не требуют изменений

---

## Draft Code Graph

```
core/internal/verify/
├── verify-domains.sh            # → ~60 LOC (shell facade: parse args → call Python → exit)
└── domain_verifier.py           # NEW ~200 LOC (resolve YAML + parse domains + curl verify + status-page)

core/internal/bootstrap/
└── issue-cert.sh                # → ~700 LOC (+4 строки TRAP-комментария, без структурных изменений)

core/entrypoints/
└── verify.sh                    # БЕЗ изменений (89 LOC, уже делегирует verify-domains.sh)

tests/unit/
└── test_domain_verifier.py      # NEW ~150 LOC (11 unit-тестов)
```

---

## Design Decisions

### ## @rationale D1: Полный Strangler-Fig для verify-domains (Option A)

**Q:** Почему полный Strangler-Fig, а не только извлечение inline python3 (Option B)?

**A:** verify-domains.sh — идеальный кандидат для полного Strangler-Fig:
1. **Низкий risk:** вызывается только локально (`make verify`), никогда не выполняется на VPS. Ошибка здесь = неверный статус верификации, а не production outage.
2. **Чистый домен:** HTTP-верификация — самодостаточный bounded context без зависимостей от других подсистем (в отличие от deploy-project.sh, который связан с Docker, SSH, rollback).
3. **Измеримый выигрыш:** Option B оставляет curl-логику и status-page health check в непротестированном bash. Полная миграция даёт 11 unit-тестов против 0.
4. **Precedent:** Wave 4 уже доказал эффективность полного Strangler-Fig для shell-скриптов схожего размера (deploy-modules 1664→91 LOC, converge 1149→137 LOC).
5. **Inline python3 — триггер, не цель:** два блока `python3 -c` — это симптомы, а не корневая проблема. Корневая проблема — бизнес-логика в shell, которую нельзя unit-тестировать. Извлечение только inline-блоков лечит симптом, но не болезнь.

### ## @rationale D2: issue-cert.sh — осознанный пропуск (подтверждение D2 из DevPlan 036)

**Q:** Почему issue-cert.sh (696 LOC) не разбирается даже минимально?

**A:** TRAP[DECISION] в `cert_orchestrator.py` (DevPlan 052) и module contract в `issue-cert.sh` (L8-9) явно документируют: этот скрипт — shell subprocess, вызываемый через `subprocess.run()` из Python-оркестратора. Все 696 строк — это acme.sh CLI interaction (DNS-01 webnames API key injection, HTTP-01 standalone mode, cert install, cron setup, renewal hooks, expiry verification). Бизнес-логика оркестрации (domain iteration, S3 cache, cron scheduling, project certs) УЖЕ в `cert_orchestrator.py`.

**Анализ 10 TRAP в issue-cert.sh:**
- 3 TRAP[BUG] (mkcert certs, false diagnosis, acme.sh basename) — исправлены, код стабилен
- 3 TRAP[DECISION] (DNS-01 primary, HTTP-01 fallback, exit→return) — архитектурные решения подтверждены
- 1 TRAP[BUSINESS] (API key shred) — security requirement, работает
- 3 TRAP (cert_orchestrator, main() preserved, early exit fix) — документируют текущий дизайн

Порт этих 696 LOC в Python (`subprocess.run(["acme.sh", ...])`) не даст прироста тестируемости (acme.sh всё равно требует реального DNS API), но создаст risk для cron-based cert renewal (crontab вызывает acme.sh, не Python).

**Решение:** добавить TRAP-комментарий в начало issue-cert.sh с ссылкой на этот DevPlan. Без структурных изменений.

### ## @rationale D3: curl через subprocess.run, не через requests

**Q:** Почему `verify_domain()` использует `subprocess.run(["curl", ...])`, а не `requests.get()`?

**A:** Три причины:
1. **Семантическая эквивалентность:** verify-domains.sh использует curl. Замена на requests меняет TLS-стек (OpenSSL vs Python ssl), HTTP-поведение (redirect following, User-Agent), таймауты (connect + read vs единый --max-time). Это создаёт риск незамеченных различий на production.
2. **Нулевые зависимости:** curl есть везде (Linux, macOS). requests требует установки (не в stdlib).
3. **Консистентность с экосистемой:** другие скрипты платформы (healthcheck.sh, status-page liveness) используют curl. Единый подход упрощает отладку (одинаковые TLS-ошибки, одинаковый вывод).

**Исключение:** `get_expose_domains()` использует `yaml.safe_load()` (PyYAML, уже в проекте) — замена inline python3 на нативный Python-код без внешней зависимости.

### ## @rationale D4: shell-фасад сохраняет source lib/logging.sh

**Q:** Зачем shell-фасаду source lib/logging.sh, если вся бизнес-логика в Python?

**A:** Две функции logging.sh используются в shell-фасаде:
1. `log_imp 8/9/10` — для логирования ошибок парсинга аргументов и вызова Python (до передачи управления)
2. Совместимость с Python-логированием: domain_verifier.py будет использовать стандартный `logging` модуль с форматом `[IMP:X][domain-verifier][func] message`, идентичным тому, что генерирует `log_imp` в bash

Это сохраняет консистентность логов при переходе shell → Python (единый формат, те же IMP-уровни).

### ## @rationale D5: resolve_node_yaml — сохранение 3-path search логики

**Q:** `resolve_node_yaml()` — 56 строк bash с shopt nullglob. Стоит ли это извлекать в Python?

**A:** Да, потому что:
1. **Тестируемость:** 3-path search с nullglob — сложная логика с edge cases (отсутствие директории, коллизия путей, macOS vs Linux glob). В bash это тестируется только интеграционно (реальные файлы). В Python — unit-тесты с `tmp_path` фикстурами.
2. **Дедупликация в будущем:** `resolve_node_yaml()` дублируется в 4+ скриптах (deploy-project.sh, remote-cmd.sh, converge.sh). Извлечение в domain_verifier.py — первый шаг к будущему `core/internal/shared/node_resolver.py` (DevPlan 036 §Future Work).
3. **shopt nullglob — inherent bash complexity:** Python `pathlib.Path.glob()` нативно обрабатывает отсутствие файлов без nullglob-трюков. Код становится проще.

---

## $TASKS

### TASK-036A1: verify-domains.sh Strangler-Fig → domain_verifier.py
- **Owner:** Coder
- **Output:**
  - `core/internal/verify/domain_verifier.py` (~200 LOC) — Python-модуль с функциями resolve_node_yaml, get_expose_domains, verify_domain, verify_status_page, main
  - `core/internal/verify/verify-domains.sh` (~60 LOC) — shell-фасад (parse args → python3 call → exit)
  - `tests/unit/test_domain_verifier.py` (~150 LOC) — 11 unit-тестов
- **Acceptance:**
  - shell ≤60 LOC, 0 inline python3 (grep `python3 -c\|<<PYEOF` → 0 matches)
  - Все 4 функции перенесены с сохранением семантики: 3-path resolve, expose:true filter, curl HTTP 200 check, status-page health check
  - TRAP[BUG] status-page URL mismatch перенесён в Python docstring
  - `make test MARKER=unit` зелёный (11 тестов в test_domain_verifier.py)
  - `make verify NODE=<test>` работает идентично (тот же stdout/exit code)
- **Dependencies:** None
- **Complexity:** 3/10
- **Checkpoint:** `make test MARKER=unit` зелёный, `grep -c "python3 -c" verify-domains.sh` → 0

### TASK-036A2: issue-cert.sh TRAP documentation
- **Owner:** Coder
- **Output:**
  - `core/internal/bootstrap/issue-cert.sh` — +4 строки TRAP-комментария (в начало файла, после module contract)
- **Acceptance:**
  - TRAP-комментарий содержит: ссылку на DevPlan 036A D2, rationale (cert_orchestrator — shell subprocess by design), rejected alternative (Python-порт acme.sh CLI)
  - Никаких структурных изменений в коде issue-cert.sh (diff показывает только +4 строки)
  - `make gate MODE=fast` зелёный (issue-cert.sh не затронут функционально)
- **Dependencies:** None
- **Complexity:** 1/10
- **Checkpoint:** `git diff -- core/internal/bootstrap/issue-cert.sh` показывает только TRAP-комментарий

### Merge Rule Check
- TASK-036A2: files_count=1, estimated_lines=4 — qualifies for merge into TASK-036A1
- **Однако:** концептуально это разные артефакты (verify-domains migration vs issue-cert doc) и в master DevPlan 036 они разделены (TASK-036A и TASK-036F). Оставляем раздельными для clarity audit trail.
- **@keep_separate:** TASK-036A2 концептуально отличен, несмотря на микро-размер

---

## $PARALLEL_GROUPS

### Wave 1 (independent, no shared files)
- **Tasks:** TASK-036A1, TASK-036A2
- **Command:** `coder Read .ai/plans/036-wave5a-verify/01-DevPlan.md, implement Wave 5a: TASK-036A1, TASK-036A2`

Файлы не пересекаются: TASK-036A1 затрагивает `core/internal/verify/` + `tests/unit/`, TASK-036A2 — только `core/internal/bootstrap/issue-cert.sh`.

---

## Acceptance Criteria Summary

| ID | Критерий | Метод проверки |
|----|----------|---------------|
| AC-1 | verify-domains.sh ≤60 LOC, 0 inline python3 | `wc -l` + `grep "python3 -c\|<<PYEOF"` → 0 matches |
| AC-2 | domain_verifier.py — 4 business-logic функции + main() CLI | `grep "^def " core/internal/verify/domain_verifier.py` → resolve_node_yaml, get_expose_domains, verify_domain, verify_status_page, main |
| AC-3 | `make verify NODE=<test>` идентичен | Ручной прогон до/после на тестовой ноде |
| AC-4 | Unit-тесты ≥6, все зелёные | `pytest tests/unit/test_domain_verifier.py -v` → 11 passed |
| AC-5 | `make test MARKER=unit` зелёный | Все существующие unit-тесты не сломаны |
| AC-6 | `make gate MODE=fast` зелёный | Полный gate-прогон без регрессий |
| AC-7 | issue-cert.sh — только TRAP-комментарий | `git diff -- core/internal/bootstrap/issue-cert.sh` → +4 строки, без изменений кода |

---

## $TEST_SPEC

| Test file | Test function | Scenario | Module under test |
|-----------|---------------|----------|-------------------|
| `tests/unit/test_domain_verifier.py` | `test_resolve_node_yaml_path1_local` | node.yaml найден в platform-local (путь 1) — возвращает Path | `domain_verifier.resolve_node_yaml()` |
| `tests/unit/test_domain_verifier.py` | `test_resolve_node_yaml_path2_org` | node.yaml найден в org repos (путь 2, glob) — возвращает Path | `domain_verifier.resolve_node_yaml()` |
| `tests/unit/test_domain_verifier.py` | `test_resolve_node_yaml_path3_vps` | node.yaml найден в /opt/node-configs (путь 3, fallback) — возвращает Path | `domain_verifier.resolve_node_yaml()` |
| `tests/unit/test_domain_verifier.py` | `test_resolve_node_yaml_not_found` | node.yaml не найден ни по одному пути → FileNotFoundError | `domain_verifier.resolve_node_yaml()` |
| `tests/unit/test_domain_verifier.py` | `test_get_expose_domains_with_domains` | node.yaml с expose:true + domain → возвращает список доменов | `domain_verifier.get_expose_domains()` |
| `tests/unit/test_domain_verifier.py` | `test_get_expose_domains_no_expose` | node.yaml без expose:true проектов → возвращает пустой список | `domain_verifier.get_expose_domains()` |
| `tests/unit/test_domain_verifier.py` | `test_verify_domain_http200` | curl возвращает HTTP 200 → VerifyResult.pass | `domain_verifier.verify_domain()` |
| `tests/unit/test_domain_verifier.py` | `test_verify_domain_connection_failed` | curl connection error → VerifyResult.fail с error | `domain_verifier.verify_domain()` |
| `tests/unit/test_domain_verifier.py` | `test_verify_domain_non_200` | curl возвращает HTTP 302/500 → VerifyResult.warn | `domain_verifier.verify_domain()` |
| `tests/unit/test_domain_verifier.py` | `test_verify_status_page_ok` | status-page /health возвращает HTTP 200 с Basic Auth → pass | `domain_verifier.verify_status_page()` |
| `tests/unit/test_domain_verifier.py` | `test_verify_status_page_missing_creds` | PLATFORM_MASTER_EMAIL/PASSWORD не заданы → skip gracefully (None result) | `domain_verifier.verify_status_page()` |

$TEST_SPEC: 11 тестов (1 модуль, 1 тестовый файл) — превышает минимум в 6 тестов.

**Тестовые фикстуры:**
- `tmp_path` для создания временных node.yaml файлов (пути 1-3, expose:true сценарии)
- `mocker` (pytest-mock) для мокирования `subprocess.run` в verify_domain и verify_status_page
- `monkeypatch` для установки `PLATFORM_DOMAIN`, `PLATFORM_MASTER_EMAIL`, `PLATFORM_MASTER_PASSWORD` env vars

**Анти-иллюзия (LDD Telemetry):**
Каждый тест проверяет IMP:9 логи через caplog. Успешные сценарии (`test_verify_domain_http200`, `test_verify_status_page_ok`) требуют ≥1 IMP:9 лога (бизнес-логика). Падающие сценарии (`test_verify_domain_connection_failed`) проверяют IMP:10 логи ошибок.

---

## Risk Assessment

| Risk | Severity | Mitigation |
|------|----------|------------|
| Семантическая неэквивалентность curl → subprocess.run(["curl", ...]) | 🟢 LOW | curl вызывается тот же бинарный, те же флаги (`-sS -o /dev/null -w '%{http_code}' --max-time`). Единственное отличие: shell pipe vs subprocess stdout capture — без эффекта на HTTP-поведение. |
| Ошибка в resolve_node_yaml пути 2 (glob) | 🟢 LOW | Python `pathlib.Path.glob()` надёжнее bash `nullglob` — не требует stateful shopt переключений. Unit-тест покрывает сценарий с коллизией путей. |
| Регрессия status-page health check (Basic Auth) | 🟢 LOW | Логика 1:1 перенесена (тот же URL `platform.{domain}/health`, те же `-u` credentials). TRAP[BUG] сохранён. |
| issue-cert.sh случайная поломка | 🟢 NONE | Только добавление комментария — функциональность не затрагивается. `make gate MODE=fast` подтверждает. |
| Нарушение контракта вызова (deploy-project.sh post-deploy hook) | 🟢 LOW | Shell-фасад verify-domains.sh сохраняет тот же интерфейс (`verify-domains.sh <node> <platform_root>`). Все caller'ы продолжают работать без изменений. |

**Общий risk: LOW.** verify-domains — клиентский инструмент, не VPS-компонент. Даже при полной поломке восстанавливается `git revert` за <1 мин.

---

## Rollback Strategy

| Компонент | Метод отката | Время восстановления |
|-----------|-------------|:---:|
| domain_verifier.py + shell facade | `git revert <merge-commit>` — shell восстанавливается из git history (281 LOC оригинал) | <1 мин |
| issue-cert.sh TRAP-комментарий | `git revert <merge-commit>` — удаление 4 строк комментария | <1 мин |
| test_domain_verifier.py | Удаляется вместе с domain_verifier.py при revert | <1 мин |

**Общее время восстановления: <5 минут.** Никаких VPS-операций, никаких миграций данных, никаких docker-перезапусков.

---

## TRAP Inventory (post-migration)

### Новые TRAP в domain_verifier.py

```python
# ⚠️ TRAP[BUG] · 2026-07-24 · P2 · status-page URL mismatch
# · Symptom: curl https://tronyx.ru/health → nginx overlay proxied to tronyx-site project → 500
# · Root: status-page lives on platform.tronyx.ru (platform-vhost.conf), not apex domain
# · Fix: use platform.{PLATFORM_DOMAIN}/health instead of {PLATFORM_DOMAIN}/health
# · @see DevPlan 051 P2
# (перенесён из verify-domains.sh L194-198)
```

### Новые TRAP в issue-cert.sh (добавляются после module contract, перед `set -euo pipefail`)

```bash
# ⚠️ TRAP[DECISION] · 2026-07-26 · HI · Wave 5a: issue-cert.sh осознанно пропущен — shell subprocess by design
# · Rejected: Python-порт acme.sh CLI interaction (696 LOC → Python subprocess.run(["acme.sh", ...]))
# · Reason: TRAP cert_orchestrator (DevPlan 052) определяет issue-cert.sh как shell subprocess.
#   Бизнес-логика оркестрации (domain iteration, S3 cache, cron scheduling, project certs)
#   уже в cert_orchestrator.py. Порт acme.sh CLI в Python не даст прироста тестируемости
#   (acme.sh требует реального DNS API), но создаст risk для cron-based cert renewal.
# · @see DevPlan 036A D2 — полное обоснование
# · Rev: если acme.sh получит Python API — пересмотреть решение
```

### TRAP в verify-domains.sh (shell facade)

```bash
# ⚠️ TRAP[DECISION] · 2026-07-26 · MED · Wave 5a: verify-domains.sh Strangler-Fig → domain_verifier.py
# · Rejected: keeping business logic in shell (281 LOC, 2 inline python3 blocks)
# · Reason: языковая политика (AGENTS.md), тестируемость, дедупликация resolve_node_yaml
# · @see DevPlan 036A D1
```

---

## File Manifest

### Modified files

| Файл | До (LOC) | После (LOC) | Сокращение | Изменения |
|------|----------|-------------|------------|-----------|
| `core/internal/verify/verify-domains.sh` | 281 | ~60 | 79% | Полный Strangler-Fig: shell facade |
| `core/internal/bootstrap/issue-cert.sh` | 696 | ~700 | 0% | +4 строки TRAP-комментария |

### New files

| Файл | LOC | Назначение |
|------|-----|-----------|
| `core/internal/verify/domain_verifier.py` | ~200 | Python-модуль: resolve YAML + parse domains + curl verify + status-page |
| `tests/unit/test_domain_verifier.py` | ~150 | 11 unit-тестов для domain_verifier.py |

### Unchanged files

| Файл | LOC | Причина |
|------|-----|--------|
| `core/entrypoints/verify.sh` | 89 | Уже thin-wrapper, делегирует verify-domains.sh — изменений не требуется |

### Before/After Summary

| Метрика | До | После |
|---------|-----|-------|
| Shell LOC (verify-domains) | 281 | ~60 |
| Inline python3 блоков | 2 | 0 |
| Python LOC (domain_verifier) | 0 | ~200 |
| Unit-тестов (domain verification) | 0 | 11 |
| Общее покрытие тестами (verify domain) | Интеграционное (ручное) | Unit (автоматическое) + интеграционное |

---

## Next Steps

### Wave 5a (TASK-036A1 + TASK-036A2)
```
coder Read .ai/plans/036-wave5a-verify/01-DevPlan.md, implement Wave 5a: TASK-036A1, TASK-036A2
```

После реализации:
```
make test MARKER=unit && make gate MODE=fast
```

$END_DEVPLAN
