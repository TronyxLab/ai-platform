# Findings 005 — Practices + Scaffold (K1–K5 gating channels)

Scope: `core/internal/practices/`, `core/internal/scaffold/` · Agent wave 1 · 2026-08-22
Контекст: гоняется на каждом push разработчика (K5 pre-push hook) и на деплоях — умножается на размер команды.

### PERF-060 | HIGH | conf=High
- Category: последовательные независимые subprocess-проверки
- Hot path: yes — каждый push (K5 pre-push → project-check) и каждый деплой (K2)
- File/symbol: `core/internal/practices/check_project/runner.py::check_project`
- Trigger: `make project-check` исполняет выбранные проверки одна за другой
- Complexity/cost: wall = Σ длительностей до ~16 последовательных subprocess (gitleaks, compose config, ruff ×3, shellcheck, pytest ×2, pyright, eslint, npm build); worst-case serialized бюджет ≈205s
- Expected impact: параллелизация независимых проверок сокращает wall с Σ≈30–180s до max≈30–120s на push; × team size × pushes/day
- Evidence: `runner.py:129-131`
  ```python
  for check in selected:
      result = run_check(check, project_dir, fix=fix, facts=facts)
  ```
  Хендлеры — независимые subprocess (`checks/tool.py`), разделяют только read-only доступ к project_dir
- Minimal fix: ThreadPoolExecutor вокруг run_check для non-mutating проверок (--fix мутации оставить сериальными)
- Measurement: Σ duration_s в CheckReport vs wall-clock; expect ≈Σ→max
- Phase: Pre-launch

### PERF-061 | HIGH | conf=High
- Category: repeated computation — дубликат тестового прогона
- Hot path: yes — proposed/active-full states (каждый push после эскалации проекта)
- File/symbol: `checks/tool.py::check_pytest_baseline` + `check_pytest_full`
- Trigger: state ∈ {proposed, active-full} → select_checks выбирает ОБЕ проверки (baseline всегда + full)
- Complexity/cost: 2 полных последовательных `pytest -q -x` одного и того же suite, разница только `--strict-markers --strict-config`; бюджеты 30s + 120s
- Expected impact: минус целый дублирующий прогон suite на каждый push — типично 5–60s на разработчика
- Evidence: `tool.py:267`, `:290`; `_pytest_cmd(strict=True)` — строгий superset `strict=False` (`tool.py:66-73`)
- Minimal fix: гонять только strict invocation (superset), переиспользовать результат для baseline verdict
- Measurement: duration_s pytest id before/after; −1 полный suite run
- Phase: Pre-launch

### PERF-062 | MED | conf=High
- Category: canon/YAML перепарсивается 4× за один check
- Hot path: yes — каждый project-check
- File/symbol: `manifest.py::load_manifest` callers: `runner.py:109`, `runner.py:69→l1_checks:251`, `escalator.py:132→maturity_thresholds:266`, `drift.py:123`
- Trigger: один `check_project()` грузит + Draft7-валидирует practices_manifest.yaml 4 раза; плюс `read_lock` 2× и `ai-platform.yaml` 4×
- Complexity/cost: каждая загрузка = yaml.safe_load + json.load(schema) + Draft7Validator.iter_errors ≈5–15ms → ~40–80ms лишних за ран
- Expected impact: десятки ms на push; растёт с размером канона; тривиально убирается
- Evidence: `runner.py:69` — `l1_ids = {c.id for c in l1_checks()}` где `l1_checks()` ре-вызывает `load_manifest()`, хотя manifest уже загружен и передан рядом
- Minimal fix: module-level mtime-keyed cache в load_manifest (или проброс manifest через l1_checks/maturity_thresholds/check_drift_gate)
- Measurement: число validate_yaml_against_schema вызовов за ран 4 → 1
- Phase: Pre-launch

### PERF-063 | MED | conf=High
- Category: ≥7 независимых os.walk проходов по дереву за один ран
- Hot path: yes — каждый project-check
- File/symbol: `files.py::iter_project_files` consumers + `maturity.py::_count_code_files` + `file.py::check_docs_in_code`
- Trigger: maturity count (`maturity.py:210`), hygiene (`file.py:73`), grep-summary (`file.py:141`), docs-in-code FS scan (`file.py:190`), shellcheck listing (`tool.py:160`), transition scan (`file.py:280`), agent-check listing (`file.py:329`) — каждый делает свой walk
- Complexity/cost: O(7×F) stat/readdir syscalls; 2k-file проект ≈14k stats ≈50–200ms warm, multi-second cold (CI runners)
- Expected impact: ~85% walk overhead убирается одним walk'ом с фильтрацией per-check; важнее всего в CI K2 канале
- Evidence: `files.py:63-66`
- Minimal fix: материализовать file list один раз в check_project(), передавать snapshot файловым хендлерам
- Measurement: syscall count или Σ duration_s per-check before/after
- Phase: Pre-launch

### PERF-064 | MED | conf=Med
- Category: network install внутри push-gating check
- Hot path: yes — ts/react push при отсутствующем node_modules (fresh clone, CI workspace)
- File/symbol: `checks/tool.py::check_build`
- Trigger: package.json имеет build script и node_modules/ отсутствует → полное скачивание dependency tree во время локального гейтящего чека
- Complexity/cost: `npm ci` (network fetch) + `npm run build`, каждый cap 30s → до ~60s serialized + registry egress
- Expected impact: до 1–2 мин добавки к push на fresh clones; flaky registry даёт FAIL-noise. Задокументировано как deliberate CI-parity (TRAP) — рассматривать как accepted-risk decision к пересмотру
- Evidence: `tool.py:242-246`
- Minimal fix: WARN-skip build при отсутствии node_modules локально (install оставить CI), либо вынести npm ci из per-push гейта
- Measurement: build duration_s distribution on fresh clones
- Phase: Pre-launch

### PERF-065 | MED | conf=High
- Category: N+1 subprocess — Python интерпретатор на каждый node.yaml
- Hot path: conditional — `make project-status` / status queries в release-checklist converge probes (обязаны не зависать)
- File/symbol: `core/internal/scaffold/project_lister.py::find_project_node`
- Trigger: поиск одного проекта по N node.yaml файлов → N последовательных свежих interpreter launch
- Complexity/cost: каждый spawn = полный CPython startup + import (~100–250ms) ×N; чистая Python-работа без нужды в subprocess
- Expected impact: N×150–250ms экономии на lookup; меньше failure surface в status-пути
- Evidence: `project_lister.py:241-255` (`subprocess.run([sys.executable, "-m", "core.internal.shared.node_yaml", ...])`)
- Minimal fix: вызывать node_yaml library функции in-process
- Measurement: `time make project-status NAME=<p>` before/after
- Phase: Post-launch

### PERF-066 | LOW | conf=High
- Category: O(history) генерация ради скалярного ответа
- Hot path: yes — каждый project-check/sync_practices (age компонент maturity)
- File/symbol: `core/internal/practices/maturity.py::_git_first_commit`
- Trigger: `git log --reverse --format=%aI` стримит по строке на коммит всей истории; Python парсит весь stdout чтобы взять lines[0]; второй `--follow` вызов повторяет это при None
- Complexity/cost: O(H) stdout bytes; 10k-commit repo ≈50–150ms ×2 worst case
- Expected impact: мало абсолютно, но на каждый push; сводится к O(1)-ish
- Evidence: `maturity.py:171,184-188`
- Minimal fix: `git rev-list --max-parents=0 HEAD` + одиночный `git show -s --format=%aI <root>`
- Measurement: maturity age-step duration on long-history repo
- Phase: Post-launch

### PERF-067 | LOW | conf=High
- Category: full-content reads + double parse тех же файлов
- Hot path: yes — каждый project-check (baseline включительно)
- File/symbol: `checks/file.py::check_hygiene` (+`files.py::parse_structured`), `maturity.py::_count_code_files`
- Trigger: hygiene читает каждый текстовый файл целиком, затем .toml/.json читаются и парсятся ВТОРОЙ раз; maturity отдельно открывает каждый code-file для sniff первой строки
- Complexity/cost: O(total text bytes) resident + 2× I/O для structured файлов
- Expected impact: половина I/O на structured файлах и минус полный pass чтений; заметно на репо со многими крупными текстовыми артефактами
- Evidence: `file.py:75,86`; `files.py:136,140`
- Minimal fix: валидировать синтаксис из уже прочитанного content (строка в парсер); GENERATED-header exclusion по фиксированному rel-path set без открытия файлов
- Measurement: peak RSS + hygiene duration_s на представительном репо
- Phase: Post-launch
