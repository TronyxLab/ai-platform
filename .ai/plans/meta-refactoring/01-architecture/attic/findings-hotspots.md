# Direction 10 — Architectural Hotspots

Агент: форензик направления «hotspots» (data-driven: git churn × size × fan-in × co-change) · Дата: 2026-08-22

Итог направления: CRITICAL 0 · HIGH 3 · MEDIUM 2 · LOW 1. Самый горячий файл — **core/check-suite.yaml** (top churn 9 commits/6mo, эпицентр fix(ci)-шторма: 6/13 недавних CI-fixes его касаются, co-change со своим интерпретатором). Вторая hotspot-система — invariant-11 manifest-кластер вокруг sync_env_defaults.py. Все 49 шестимесячных коммитов лежат внутри последних 3 месяцев — нестабильность текущая, не историческая.

---

### ARCH-091: check-suite.yaml — verification keystone с максимальным churn и корреляцией fix-storm
- Severity: HIGH
- Confidence: HIGH
- Files: core/check-suite.yaml, core/internal/check_suite/runner.py, core/internal/test_runner.py, .github/workflows/platform-test.yml
- Symbols: checks[] schema (26 entries), два исполнителя (`make check` / `make gate`)
- Evidence: #1 churn-файл — 9 commits/6mo (следующий — 5). Co-change с test_runner.py 3× за последние 300 core-коммитов. За последние 3 месяца fix(ci) = 13/49 коммитов (27%); эти 13 касаются check-suite.yaml 6×, platform-test.yml 5×, runner.py + conftest многократно. Плотность окна: все 49 коммитов за 6 месяцев попадают в последние 3 месяца — нестабильность текущая.
- Failure/maintenance scenario: каждая правка gate-семантики требует синхронных изменений suite YAML + Python executor + GitHub workflow; дрейф всплывает как CI-отказы (сам fix(ci)-цикл).
- Impact: ~35% недавнего инженерного времени (17/49 коммитов с префиксами fix ci/smoke/gate) уходит на ремонт верификационной инфраструктуры вместо продукта.
- Minimal fix: деривировать job matrix platform-test.yml из check-suite.yaml (как это делает ci.mk), свалив третий hardcoded-consumer; добавить parity-гейт workflow-vs-suite.
- Code churn: M
- Phase: Pre-launch

### ARCH-092: Manifest Generation Contract cluster — 7-file «boundary fiction», движущийся одним блобом
- Severity: HIGH
- Confidence: HIGH
- Files: core/internal/scripts/sync_env_defaults.py, core/platform-infra.yaml, core/secret-definitions.yaml, core/secrets-manifest.yaml, core/AGENTS.md, core/internal/shared/AGENTS.md, core/entrypoint-manifest.yaml
- Symbols: env_defaults, autogen convention, GENERATED markers
- Evidence: сильнейшая co-change сеть в выборке 300 коммитов — 11 пар с ≥3 совместными появлениями, все внутри этого набора: AGENTS.md↔secret-definitions (4), AGENTS.md↔secrets-manifest (4), secret-definitions↔secrets-manifest (4), AGENTS.md↔entrypoint-manifest (4), sync_env_defaults↔platform-infra (4), плюс семь пар с count=3. Каждый файл индивидуально churn'ится 4–5 commits/6mo. Hub-код: sync_env_defaults.py = 961 LOC, 17 importer-файлов.
- Failure/maintenance scenario: добавление одной env var касается authoritative source → регенерация 3 outputs + 2 AGENTS.md sections + registry; пропущенная регенерация ловится только `make check MARKER=check-manifests` постфактум.
- Impact: высокий координационный налог; границы модулей существуют на диске, но не в change-реальности — merge'и в этот набор конфликтны by construction.
- Minimal fix: структурно ничего (инвариант 11 намеренный); снизить поверхность, сделав generate-manifests атомарным (таргет уже есть) и задокументировав набор как ОДИН логический модуль в навигации core/AGENTS.md.
- Code churn: M
- Phase: Pre-launch

### ARCH-093: Cost-of-one-verb — измеренная ceremony weight manifest-gate архитектуры
- Severity: MEDIUM
- Confidence: HIGH
- Files: makefiles/*.mk, core/entrypoint-manifest.yaml, AGENTS.md, core/check-suite.yaml, core/internal/scripts/generate_entrypoint_manifest.py, core/internal/scripts/generate_agents_md.py
- Symbols: allowed_verbs, .PHONY, GENERATED:START:glossary
- Evidence: добавление ОДНОГО публичного verb касается точно: (1) recipe + .PHONY в topic .mk (makefiles/bootstrap.mk:17, ci.mk:25, helpers.mk:24); (2) core/entrypoint-manifest.yaml:909 allowed_verbs block (64 verbs) — регенерируется static .PHONY parse, коммитится; (3) AGENTS.md:86-151 glossary table (65 rows) — второй generator pass, коммитится; (4) авто-enforcement: test_all_makefile_targets_in_allowed_verbs (entrypoint-manifest.yaml:1782) + namelint блокирует незарегистрированные таргеты; (5) если gated: check-suite.yaml entry (26 checks) + возможная parity platform-test.yml. Реестр несёт 2556 LOC суммарно, 527 gate entries. Нетто: 1 hand-edit → 2 generator run → 3 committed files → 2 enforcement layers revalidated.
- Failure/maintenance scenario: пропуск шага 2/3 = blocked push (by design); пропуск шага 5 = silent gap между `make check` и CI.
- Impact: friction намеренный (зеркалирование glossary/manifest — инвариант), но 2556-строчный коммитимый артефакт гарантирует merge conflicts при двух параллельных verb'ах.
- Minimal fix: сохранить контракт; сузить conflict surface, выделив allowed_verbs/gates в отдельный файл, включаемый entrypoint-manifest.yaml.
- Code churn: S
- Phase: Pre-launch

### ARCH-094: deploy_paths.py — shared-kernel fan-in king (87 importers)
- Severity: HIGH
- Confidence: MED
- Files: core/internal/shared/deploy_paths.py
- Symbols: path constants/helpers, потребляемые deploy, bootstrap, healthcheck, practices
- Evidence: 87 импортирующих .py файлов (наибольший fan-in из измеренных; далее: check_suite 24, sync_env_defaults 17, notifications 15). Размер 558 LOC. Churn умеренный (3 commits/6mo) — но каждый коммит ложится на blast radius в 87 файлов; ни один другой модуль близко не стоит.
- Failure/maintenance scenario: rename/переезд одной path-константы молча ломает десятки call sites; grep-based рефакторинги рискуют частичными обновлениями, невидимыми до VPS-деплоя.
- Impact: single point of architectural coupling; ограничивает скорость эволюции layout'а core delivery.
- Minimal fix: заморозить public API за явными exports + contract-тест на exported symbol set (дёшево сейчас, пока не наросли новые импортеры).
- Code churn: S
- Phase: Pre-launch

### ARCH-095: Дублирование executor-пары — check_suite/runner.py vs test_runner.py
- Severity: MEDIUM
- Confidence: MED
- Files: core/internal/check_suite/runner.py (403 LOC, 24 importers), core/internal/test_runner.py (909 LOC, 14 importers)
- Symbols: suite executor, xdist handling, fingerprint cache
- Evidence: оба churn'атся в lockstep с конфигом: runner.py 4 commits, test_runner.py 3 commits/6mo; check-suite.yaml↔test_runner.py co-change 3×. Суммарно 1312 LOC интерпретируют один и тот же 252-строчный YAML из двух режимов (`check` diagnostic vs `gate` canonical).
- Failure/maintenance scenario: новое поле схемы check-suite.yaml должно honor'иться одинаково в обоих интерпретаторах; дивергенция = класс багов «локально проходит (check), CI падает (gate)» — согласуется с fix(ci)-кластером.
- Impact: two-executors-one-manifest дизайн убивает config drift, но концентрирует semantic drift risk в самой паре.
- Minimal fix: извлечь общий step-resolution/validation в один модуль, который импортируют оба CLI; property-test что check-mode selection ⊆ gate-mode selection по полю gate_modes.
- Code churn: M
- Phase: Pre-launch

### ARCH-096: Makefile monolith risk — снят, проверенно здоров
- Severity: LOW
- Confidence: HIGH
- Files: Makefile, makefiles/*.mk
- Symbols: includes, MAKE_LOG_FILE shell wrapper
- Evidence: root Makefile = 101 LOC facade (header фиксирует split 747→~80 LOC W4-E4); zero top-level таргетов под `^[a-z]`; 12 includes суммарно 1394 LOC, крупнейший repair.mk 215 LOC; churn ≤2 commits/file/6mo. Единственная остаточная сложность — 30-строчный logging shell-wrapper блок (Makefile:74-101).
- Failure/maintenance scenario: только если новые контрибьюторы добавляют таргеты прямо в root Makefile вместо topic .mk.
- Impact: позитивный контроль — классический hotspot-кандидат hotspot'ом не является; действий сверх уже работающей lint-дисциплины не нужно.
- Minimal fix: none (опционально вынести logging wrapper в scripts/ при росте).
- Code churn: S
- Phase: Pre-launch
