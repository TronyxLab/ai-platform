# Findings 006 — QA tooling (static/check_suite/lint/agent_check/validate/verify_sweep)

Scope: `core/internal/static/`, `check_suite/`, `lint/`, `agent_check/`, `validate/`, `config/`, `verify/`, `verify_sweep/`, `test_runner.py` · Agent wave 1 · 2026-08-22
Контекст: гейтит каждый commit/push — wall-time waste умножается на всех разработчиков и CI.

### PERF-070 | MED | conf=High
- Category: per-rule tree walks + redundant AST parses
- Hot path: yes — каждый `make check` (static-ast), gate fast/full, CI push-gate
- File/symbol: `core/internal/static/{bare_raise,bool_string_literals,exception_patterns,sys_exit_contract,private_imports}.py::detect`
- Trigger: 5 AST-детекторов независимо re-collect и re-parse все ~368 core .py файлов; ~10 детекторов делают свой tree walk
- Complexity/cost: O(5 × (walk+parse)) вместо O(1 × …); измерено: full run **5.38s**, AST-детекторы alone **3.66s**
- Expected impact: ~2.5–3s waste на каждый static pass; ×N devs ×CI pushes/день ≈ десятки CI-минут/неделю
- Evidence: `bare_raise.py:102` (идентичная строка в 4 sibling'ах): `files = sorted(p for p in scan_root.rglob("*.py") ...)`
- Minimal fix: shared single-pass file collection + ast.parse cache (парс один раз, tree всем детекторам)
- Measurement: `time python3 -m core.internal.static check`: 5.4s → ~2.5s
- Phase: Pre-launch

### PERF-071 | MED | conf=High — ⚠️ correctness-adjacent (мёртвый гейт, false-green)
- Category: silently no-op gate entry (dead check)
- Hot path: yes — каждый diagnostic run + gate fast
- File/symbol: `core/check-suite.yaml::checks[id=check-exception-patterns]` → `registry.py::run_all`
- Trigger: манифест передаёт `--only exception_patterns` (underscore), а DETECTORS названы через дефис (`"exception-patterns"`) → фильтр не матчит ничего → **все 14 детекторов skipped, exit 0 PASS безусловно**
- Complexity/cost: чистый waste: process spawn + full package import (~0.15s/ран) с нулевым покрытием
- Expected impact: постоянно false-green dedicated gate; wasted CI slot за каждый ран
- Evidence: `check-suite.yaml:80`; `registry.py:166`; live run: `[skip] exception-patterns (not in --only)` ×14 → `PASS — 0 findings`
- Minimal fix: селектор `--only exception-patterns` либо удалить entry (дубликат static-ast)
- Measurement: ран с underscore → 14 skips/0 findings (bug); после фикса — 1 детектор или entry удалён
- Phase: Pre-launch (correctness-adjacent)

### PERF-072 | MED | conf=High
- Category: последовательные независимые network probes
- Hot path: yes — `make e2e-verify` (release-checklist step 4, post-deploy), `make verify-domains`
- File/symbol: `core/internal/verify_sweep/__init__.py::main`; same `core/internal/verify/domain_verifier.py::_verify_domains`
- Trigger: sweep по N endpoints: на endpoint один curl spawn + один `openssl s_client` spawn, строго serial
- Complexity/cost: O(N × (t_curl + t_tls)); worst case unreachable endpoint жжёт CURL_TIMEOUT 10s дважды serial → N×~20s
- Expected impact: ~20 endpoints ≈ 15–25s happy path, минуты при деградации endpoints в launch-week деплои; ÷6–8 с thread pool
- Evidence: `verify_sweep/__init__.py:208-212`; `domain_verifier.py:369`
- Minimal fix: ThreadPoolExecutor(max_workers=8) по endpoints
- Measurement: sweep wall-time before/after на prod endpoint set
- Phase: Pre-launch

### PERF-073 | LOW | conf=Med
- Category: O(files × refs) tree rescans
- Hot path: no — doc-header lint passes (full validator)
- File/symbol: `core/internal/lint/doc_header_validator.py::_find_in_internal`
- Trigger: каждый bare .sh-ref в каждом .md, промахнувшийся мимо 4 прямых кандидатов, триггерит свежий полный rglob core/internal (~650 файлов)
- Complexity/cost: O(M × R_unresolved × F_internal)
- Expected impact: sub-second сегодня; растёт линейно с docs/refs — bounded waste
- Evidence: `doc_header_validator.py:308`, callers `:272`, `:371`
- Minimal fix: один basename→path index core/internal за ран
- Measurement: rglob call count per lint pass
- Phase: Pre-launch

### PERF-074 | LOW | conf=High — ⚠️ correctness-adjacent (schema-шаг валидирует пустоту)
- Category: wasted check — vacuous gate slot
- Hot path: yes — validate entry в каждом `make check` и gate
- File/symbol: `core/internal/validate/validate_orchestrator.py::main/discover_targets/_SCHEMA_ROUTING`
- Trigger: argless invocation ищет YAML под core/internal, но routable basenames (node.yaml/module.yaml/ai-platform.yaml) имеют **ноль** вхождений там (живут в core/modules/*/, node-configs/*/, проектах) → каждый файл уходит в skip branch → exit 0 "All files valid" безусловно
- Complexity/cost: wasted bash+python process + os.walk за ран (~0.5–1s) + перманентно пустой gate slot
- Expected impact: малый CPU waste × высокая частота; главная цена — false assurance (класс PERF-071)
- Evidence: `validate_orchestrator.py:540`, `:560-562`; verified `find core/internal -name node.yaml -o ...` → empty [HYPOTHESIS на intent: возможно deliberate при явных путях из CI]
- Minimal fix: discovery на core/modules + node-configs (или явные targets в check-suite.yaml cmd)
- Measurement: число `Validating:` строк за ран: 0 сегодня → >0 после фикса
- Phase: Pre-launch

### PERF-075 | LOW | conf=High
- Category: последовательные независимые шаги + двойной ruff spawn
- Hot path: yes — каждый `make agent-check` (обязателен перед завершением задачи)
- File/symbol: `core/internal/agent_check/__init__.py::run`
- Trigger: ruff spawn'ится дважды по тому же changed set (blocking + advisory selects); 5 независимых шагов строго sequential
- Complexity/cost: wall = Σ вместо max(); self-documented TRAP[PERF]: full-repo ≈8.1s (basedpyright 5.85s), типика 2–20 файлов 0.9–1.5s
- Expected impact: <5s claim держится для типичных diff'ов — не патологично; двойной ruff добавляет ~100–300ms/ран
- Evidence: `agent_check/__init__.py:916,919`; TRAP `:883-891` документирует deferral
- Minimal fix: один ruff call с обоими rule sets (или ThreadPool по шагам)
- Measurement: summary.duration_ms в agent-check JSON report
- Phase: Post-launch
