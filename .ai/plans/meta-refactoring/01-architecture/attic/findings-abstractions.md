# Direction 9 — Abstractions & Overengineering

Агент: форензик направления «abstractions» · Дата: 2026-08-22

Итог направления: CRITICAL 0 · HIGH 1 · MEDIUM 2 · LOW 3. Полиморфные абстракции здоровы (DeliveryChannel ABC — 3 реальные реализации; Protocols работают как DI-швы; ABC с одним наследником не найдено). Избыток концентрируется на границе shell↔Python (бессмысленный bash round-trip на самом горячем контуре, дублированные hash-примитивы вокруг мёртвого shared-модуля) и в registry ceremony (≥5 файлов на тривиальное добавление verb/secret), плюс мелкие unused-артефакты (write-only channel attribute, never-read policy constant, 33× CLI boilerplate) — кандидаты на одну dead-API волну.

---

### ARCH-081: Module-interface dispatch — Python→bash→Python pass-through sandwich на каждом healthcheck
- Severity: HIGH
- Confidence: HIGH
- Files: core/internal/shared/module_interface.py:74-83 (invoke строит `bash -c "source paths.sh && source module-interface.sh && invoke_module_interface …"`), core/lib/module-interface.sh:23-25 (shell wrapper = 1:1 `python3 -m core.internal.shared.module_interface invoke "$@"`), module_interface.py:277-313 (CLI main → dispatch() в ТОМ ЖЕ модуле); consumers: core/internal/healthcheck/modules_healthcheck.py:45, core/internal/bootstrap/deploy/healthcheck_runner.py:25,167; второй hand-rolled builder: core/internal/bootstrap/lifecycle/helpers/reporting.py:133-139
- Symbols: `module_interface.invoke`, `invoke_module_interface()` (shell), `module_interface.main/dispatch`
- Evidence: repo-wide grep по всем `.sh`: shell-функция `invoke_module_interface` имеет ZERO вызовов вне собственного определения — единственный инвокер это сам Python через построение bash-команды. Цепочка одного healthcheck: Python caller → subprocess #1 (bash, sourcing 2 libs) → shell wrapper → subprocess #2 (`python3 -m … invoke`) повторно входя в тот же Python-файл → dispatch() → subprocess #3 (`bash <module>/healthcheck.sh`). Два хопа прогоняют аргументы 1:1; вся validation живёт в Python dispatch(). «Дедупликация» (C5) консолидировала построение крюка, а не сам крюк; reporting.py:134 до сих пор несёт недедуплицированный вариант (sources только paths.sh).
- Failure/maintenance scenario: каждый liveness sweep (N модулей × retries × pollers в deploy + bootstrap φ11) платит 2 лишних старта интерпретаторов и bash-хоп, чей exit-code контракт (0/1/2) должен быть byte-stable в трёх слоях; два расходящихся bash builder'а — drift vector ровно того класса, ради убийства которого делался C5.
- Impact: latency- и complexity-налог на самый горячий операционный контур; 3 места изменения для любой dispatch-семантики.
- Minimal fix: Python callers импортируют `dispatch()` напрямую (cross-layer правило соблюдено — это всё ещё contract module_interface); shell facade оставить только реальным shell-вызывающим (сейчас — никого); удалить/унифицировать inline builder reporting.py.
- Code churn: S–M
- Phase: Post-launch

### ARCH-082: shared/content_hash.py имеет zero production-importers при hand-rolled sha256-хелперах ×3 рядом
- Severity: MEDIUM
- Confidence: HIGH
- Files: core/internal/shared/content_hash.py:10-15 (TRAP[DEBT] самодокументирует «без production-потребителей»), core/internal/bootstrap/reboot_policy.py:140-142 (`content_hash()`), core/internal/bootstrap/python_deps.py:98-115 (`_compute_content_hash/_save/_check`), core/internal/scaffold/vhost_renderer.py:472-484 (`compute_body_hash`)
- Symbols: `compute_content_hash` vs локальные `content_hash` / `_compute_content_hash` / `compute_body_hash`
- Evidence: скан импортеров: единственный импортер shared/content_hash — tests/unit/test_shared_content_hash.py (+ собственный CLI). При этом три модуля определяют приватные hashlib.sha256-обёртки: два string-hash варианта (reboot_policy truncates [:16], vhost full hex) и один file-hash вариант, дублирующий семантику shared-модуля 1:1.
- Failure/maintenance scenario: ~146 LOC + выделенный тест-файл поддерживаются без потребителей, пока каждое новое место хеширования копипастит очередной хелпер; смена алгоритма (например, order-independence) требует правки в 4 местах; TRAP[DEBT] от 2026-08-22 показывает, что cleanup-волна уже раз деприоритизировала это.
- Impact: мёртвая shared-поверхность плюс активная дубликация — худший исход dedup с обеих сторон.
- Minimal fix: adopt `compute_content_hash` в python_deps (file-list case идентичен) + string-mode параметр для reboot/vhost, либо удалить shared-модуль с его тестом.
- Code churn: S
- Phase: Post-launch

### ARCH-083: Registry ceremony cost — конкретный чеклист touchpoint'ов на одно тривиальное добавление
- Severity: MEDIUM
- Confidence: HIGH
- Files: Makefile/makefiles/*.mk (.PHONY), core/entrypoint-manifest.yaml (MANUAL block: make_target/visibility/mechanism/delegates_to/signature/operation_ru/description), генераторы core/internal/scripts/generate_entrypoint_manifest.py:12-14,348-384 (G3 cycle-break), generate_agents_md.py:135,186; parity-enforcers core/internal/static/verb_register.py:255, core/internal/lint/doc_header_validator.py:471-500, tests/gates/test_gate_no_unregistered_entrypoint.py:19-22
- Symbols: `allowed_verbs`, `generate_glossary`, `generate_canon_table`
- Evidence: добавление ОДНОГО публичного make-verb затрагивает ≥5 коммитимых файлов: (1) Makefile target+recipe (вручную); (2) entrypoint-manifest.yaml MANUAL entry с 7-полевым prose `delegates_to` chain (вручную); (3) GENERATED allowed_verbs/gates того же файла через `make generate-entrypoint-manifest`; (4) root AGENTS.md glossary GENERATED section; (5) core/AGENTS.md canon_table GENERATED section — затем 4 gate-слоя должны сойтись (.PHONY↔allowed_verbs↔manifest↔filesystem shebangs/module Makefiles). Добавление ОДНОГО секрета аналогично: secret-definitions.yaml entry → module.yaml env_requires → churn core/secrets-manifest.yaml → строка «Матрица ключей» core/AGENTS.md (grep-gated инвариант) → dual provisioning surface (sops node-configs/<NODE>.enc.yaml И GitHub Secrets) → gates password_charset/manifests_up_to_date/env_defaults_consistency/env_example_drift ≈ 6 скоординированных артефактов на одно имя ключа.
- Failure/maintenance scenario: контрибьютор, пропустивший шаг (2) или (4), получает RED от четырёх разных гейтов с разными repair-командами; MANUAL блок манифеста должен переживать прогоны генератора byte-exact («переносится генератором байт-в-байт»), что делает ручные правки хрупкими. 136 gate-файлов сторожат реестры, описывающие однослотовую платформу.
- Impact: process overhead на тривиальное изменение превышает само изменение ~5×; friction концентрируется именно там, где агенты/люди делают рутинные добавления.
- Minimal fix: выводить MANUAL поля манифеста механически (парсинг recipe/shebang вместо prose delegates_to), свалив шаги 2–5 в чистую генерацию; гейты оставить как verification only.
- Code churn: M
- Phase: Post-launch

### ARCH-084: `DEPLOY_BEST_EFFORT` — always-True policy-константа, никогда не импортируется, цитируется ~30× как comment justification
- Severity: LOW
- Confidence: HIGH
- Files: core/internal/shared/contracts.py:31 (`DEPLOY_BEST_EFFORT: bool = True`); citations: core/internal/bootstrap/deploy/deploy_orchestrator.py (≈20 comment-ссылок, напр. :123,269-270), parallel_runner.py:216-217,330-331,400-401, sudoers_generator.py:384, observability.py:42, orchestrator_metrics.py:97-100, domains.py:128; runtime reads: zero
- Symbols: `DEPLOY_BEST_EFFORT`
- Evidence: константа существует, чтобы сделать политику «machine-readable» (rationale contracts.py:19-22 заявляет ≥2 consumers incl. gate T8), но broad-except подавления, которые она оправдывает (`# noqa: EXC … best-effort: DEPLOY_BEST_EFFORT policy`), ссылаются на значение, которое ни один код не вычисляет. Соседние EXIT_* константы действительно импортируются (10+ модулей). Unused generality в чистом виде: knob, который нельзя повернуть.
- Failure/maintenance scenario: читатели выводят существование переключаемого deploy-режима, которого нет; переворот флага не изменит ничего, тогда как ruff-suppression комментарии подразумевают, что он управляет семантикой error handling.
- Impact: minor — documentation theater внутри используемого модуля; подтачивает доверие к claim'ам machine-readable-policy.
- Minimal fix: либо удалить константу (политика остаётся в docstrings/gate docs), либо реально консьюмить её там, где исполняются best-effort ветки.
- Code churn: S
- Phase: Post-launch

### ARCH-085: Channel-атрибут `metadata_defaults` — пишется 4 production-сайтами, не читается никем
- Severity: LOW
- Confidence: HIGH
- Files: core/internal/deploy/channels/base.py:137-140 (декларация признаёт «Фактически не читается deliver()»); writers: core/internal/deploy/orchestrator_cli.py:265-269,641-646, core/internal/reconciler_projects.py:293; readers: none
- Symbols: `DeliveryChannel.metadata_defaults`
- Evidence: ForcedCommandChannel.deliver (forced.py:78-80) и SCPChannel.deliver (scp.py:70-72) берут host/user/key_file исключительно из `payload.metadata`; callers добросовестно заполняют ОБА payload.metadata и metadata_defaults — атрибут является write-only shadow, поддерживаемым тестами, ассертящими его содержимое (test_orchestrator_cli.py:76-78,113-115).
- Failure/maintenance scenario: будущий контрибьютор резонно полагает, что metadata_defaults питает delivery (имя намекает на defaults) и «чинит» precedence — внося реальный баг; каждое добавление channel обязано решить, honoring ли атрибут, который никто не читает.
- Impact: вводящий в заблуждение API surface на самом security-sensitive пути (SSH credentials).
- Minimal fix: удалить атрибут, его 4 write-site и test-ассерты; payload.metadata — единственный канон.
- Code churn: S
- Phase: Post-launch

### ARCH-086: Отсутствующая примитивы — argparse→typed-dataclass ceremony, продублированная 33×
- Severity: LOW
- Confidence: HIGH
- Files: 20+ модулей, напр.: core/internal/deploy/payload_deliverer.py:447-458, orchestrator_cli.py, check_suite/__init__.py:166+, bootstrap/deploy/{deploy_orchestrator,context_deployer,docker_orchestrator,compose_preflight,sudoers_generator,spool_validator,secrets_validator,orphan_reconciler,context_overlay}.py, converge/reconciler.py:61+, engine/cli.py, shared/{docker_ops,test_journal,node_resolver,project_registry}.py
- Symbols: `_CliArgs` dataclass + `cast(_CliArgs, cast(object, parser.parse_args(argv)))`
- Evidence: grep насчитывает 33 вхождения double-cast идиомы, каждое с локально объявленным mirror-dataclass (~8 LOC идентичной ceremony на CLI): ≈260 строк, переимплементирующих «Namespace → typed view». Нарушает собственное правило admission в shared/ (≥2 consumers ⇒ shared module), которому следуют соседние паттерны (atomic_write, retry, run_subprocess).
- Failure/maintenance scenario: любое улучшение typed-boundary конвенции (validation, error reporting, help formatting) требует 33 синхронных правок; новые CLI копипастят идиому вместе с W11-комментарием.
- Impact: чистая boilerplate масса; поведенческого риска нет, но постоянный drag на каждом касании CLI.
- Minimal fix: один shared helper (напр., `shared/cli_typed.parse(parser, ArgsCls)` через `ArgsCls(**vars(ns))`) и механическая замена.
- Code churn: M (много файлов, тривиальные диффы)
- Phase: Post-launch
