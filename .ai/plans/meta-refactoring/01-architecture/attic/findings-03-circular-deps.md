# Circular dependencies audit

Метод: одноразовый AST-скрипт (`/tmp/find_cycles.py`, `.venv/bin/python`) построил полный граф импортов `core/` — 366 модулей, 902 внутренних ребра (top-level и ленивые import'ы внутри функций размечены отдельно), поиск всех простых циклов + package-граф глубины 3; сырые 21 кандидата после резолва символов сократились до hub-паттернов. Дополнительно: попарные grep-проверки динамических импортов (importlib не виден AST), Shell↔Python колец, парсинг make-графа (`/tmp/make_graph.py`: 12 includes, 70 таргетов, рекурсивные `$(MAKE)`).

## ARCH-301: Hub-циклы через `__init__.py` в check_suite — субмодули импортируют символы из пакета, который импортирует их
- **Severity:** Low · **Confidence:** High
- **Files:** `core/internal/check_suite/__init__.py`, `core/internal/check_suite/{manifest,diagnostic,diff,fingerprint,gate,runner,single}.py` · **Symbols:** `PROJECT_ROOT`, `VALID_GATE_MODES`, `VALID_TIERS`, `run_diagnostic`
- **Evidence:** цикл: `check_suite/__init__` → `diagnostic` → `manifest` → `check_suite/__init__`
  - `__init__.py:78`: `from core.internal.check_suite.diagnostic import DEFAULT_MAX_WORKERS, run_diagnostic`
  - `manifest.py:32`: `from core.internal.check_suite import PROJECT_ROOT, VALID_GATE_MODES, VALID_TIERS`
  - Обратные рёбра в `__init__` имеют 7 субмодулей (diagnostic, diff, fingerprint, gate, manifest, runner, single)
- **Scenario:** работает только пока константы (`PROJECT_ROOT` и др.) определены в `__init__.py` ДО строк импорта субмодулей (сейчас: определения ~L50–77, импорты L78+). Перенос определения ниже или re-export после субмодульных импортов → `ImportError: cannot import name ... from partially initialized module` при первом прямом `import core.internal.check_suite.manifest`.
- **Impact:** хрупкость к рефакторингу `__init__`; importlinter/статические гейты видят ложные циклы и теряют сигнал; 7 субмодулей нельзя переносить изолированно.
- **Minimal fix:** манифест-модуль `check_suite/constants.py` (PROJECT_ROOT, VALID_*) — субмодули импортируют его напрямую, `__init__` реэкспортирует; обратные рёбра исчезают. · **Churn:** S (~8 файлов, механический) · **Phase:** Pre-launch

## ARCH-302: bootstrap.deploy — субмодули импортируют соседей через пакетное пространство имён вместо прямых путей
- **Severity:** Low · **Confidence:** High
- **Files:** `core/internal/bootstrap/deploy/__init__.py`, `deploy_orchestrator.py`, `docker_orchestrator.py` · **Symbols:** `orchestrate`, `pre_pull_images`
- **Evidence:** цикл: `bootstrap/deploy/__init__` → `deploy_orchestrator` → `bootstrap/deploy/__init__`
  - `__init__.py:19-21`: `from core.internal.bootstrap.deploy.deploy_orchestrator import ModuleDeployResult, orchestrate` / `...docker_orchestrator import pre_pull_images`
  - `deploy_orchestrator.py:87-93`: `from core.internal.bootstrap.deploy import (context_overlay, docker_orchestrator, orphan_reconciler, secrets_validator, spool_validator, sudoers_generator)`
  - `docker_orchestrator.py:102-107`: `from core.internal.bootstrap.deploy import (healthcheck_runner, hermes_workflow, observability, orphan_reconciler, parallel_runner)`
- **Scenario:** современный import system резолвит `from pkg import submodule` через fallback-импорт, поэтому кольцо функционально безопасно (в отличие от ARCH-301). Но каждый новый субмодуль, подключённый в `__init__` после этих строк и ссылающийся на соседей через пакет, добавляет скрытое ребро; аналитики циклов (importlinter) дают ложноположительные срабатывания на весь пакет.
- **Impact:** замыленный сигнал гейтов; порядок строк в `__init__` становится неявным контрактом; рефакторинг переименований требует правок во всех via-package импортах.
- **Minimal fix:** заменить `from core.internal.bootstrap.deploy import X` на прямые `from core.internal.bootstrap.deploy.X import ...` (2 файла, 11 имён). · **Churn:** XS · **Phase:** Post-launch

## ARCH-303: Скрытое runtime-ребро bootstrap.lifecycle → bootstrap.deploy через importlib (вне статического графа)
- **Severity:** Medium · **Confidence:** High
- **Files:** `core/internal/bootstrap/lifecycle/helpers/domains.py`, `core/internal/bootstrap/deploy/context_deployer.py` · **Symbols:** `import_deploy_context()`, `context_deployer.deploy_context()`
- **Evidence:** НЕ цикл (проверено: deploy/ имеет 0 импортов в bootstrap.lifecycle — ни статических, ни динамических), но невидимое для AST/importlinter ребро:
  - `domains.py:72`: `deployer_path = Path(core_dir) / "internal" / "bootstrap" / "deploy" / "context_deployer.py"`
  - `domains.py:73`: `spec = importlib.util.spec_from_file_location("context_deployer", deployer_path)`
  - `domains.py:81,83`: `sys.modules["context_deployer"] = deployer_mod` … `deployer_mod.deploy_context(...)`
  - Загружаемый модуль тянет поддерево: `context_deployer.py:12-22` импортирует `cert_orchestrator`, `config.platform_config`, `deploy.channels.LocalChannel`, `deploy.orchestrator.DeployOrchestrator`, 9 модулей shared.
- **Scenario:** подозрение по ТЗ подтверждено частично: `state_machine.py` динамический импорт deploy уже НЕ делает (комментарии `state_machine.py:34-35,90,276-277` фиксируют разрыв циклов phases↔state_machine и state_store↔state_machine) — но паттерн переехал в `helpers/domains.py`. При отсутствии файла/ошибке импорта — non-fatal: `domains.py:91` `logger.warning("[IMP:7]...Cannot load context_deployer.py")` → деплой контекстов молча пропадает из lifecycle-прогона.
- **Impact:** гейты циклов и зависимостей не видят зависимость lifecycle→deploy+config+deploy.engine; регрессия «случайный импорт lifecycle из deploy» создаст реальный цикл, неотслеживаемый статикой до runtime; silent-degradation при поломке пути.
- **Minimal fix:** заменить spec_from_file_location на обычный `from core.internal.bootstrap.deploy.context_deployer import deploy_context` в try/except (направление уже легально, цикла нет); либо добавить ребро в allowlist importlinter явно. · **Churn:** S · **Phase:** Pre-launch

## Проверено — циклов не найдено

| Сектор | Результат |
|--------|-----------|
| Циклы между файловыми модулями (366 файлов, 902 рёбер) | 0 простых циклов (все 21 кандидата AST = hub-паттерны ARCH-301/302 и артефакт резолва символов) |
| Заданные пары пакетов: bootstrap.lifecycle↔bootstrap.deploy, deploy↔healthcheck↔verify_sweep, catalog↔scripts, agent_check↔lint/static, practices↔llm | 0 рёбер в обе стороны у каждой пары (статически + ленивые импорты) |
| Package-циклы depth=3 (вкл. ленивые рёбра) | 0 |
| Shell↔Python кольца | Кольца нет: `vps_readiness.py→lib/ssh.sh→ssh_opts --shell`; `post_deploy_chain.py:268-269→notify-hook.sh/generate-catalog.sh→telegram_notifier/generate_catalog.py` (обратных импортов в caller-пакеты нет); `scaffold/vhost_configurator.py:191→add-vhost.sh→vhost_renderer.py` (vhost_renderer не импортирует vhost_configurator); `node-lifecycle.sh:48→lifecycle/cli.py`. Направления ацикличны |
| Make-граф | 0 циклов таргетов; 3 рекурсивных вызова ацикличны (`modules.mk:38 up→provision`; `repair.mk:175,177 fix-gate→generate-manifests/fix-pycache`); 0 дублей таргетов между 12 mk-файлами |
| Данные-циклы через диск | Нет межпакетных: state.json читается только внутри bootstrap.lifecycle (flock+tmp-rename); cert-expiry/reboot-policy state-файлы самодостаточны |

Побочное наблюдение (вне скоупа циклов): `engine.py:95` ссылается на несуществующий `core/internal/validate/validate.sh` (фактический скрипт — `core/entrypoints/validate.sh`); `preflight.py:66-68` при отсутствии файла молча пропускает FQDN-проверку (`IMP:6` лог) — бывший Python→shell→Python путь депрецирован тихим skip'ом.
