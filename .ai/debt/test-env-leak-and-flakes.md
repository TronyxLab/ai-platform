# Тестовые env-утечки и флейки полного прогона — DEBT (2026-08-02)

> Создан: 2026-08-02 | Обнаружен при батч-диагностике сессии «119 Wave 3: Бриф C»
> (зависание серийного прогона static_audit 1800s+; preflight static_audit timeout 300s)

## Суть

Три взаимосвязанные проблемы тестовой инфраструктуры, ломающие полный серийный
прогон `make test-summary MARKER=static_audit` и дающие флейки в параллельном
(xdist) прогоне:

1. **Env-утечки между тестами** — прямые записи `os.environ[...]` без отката в
   тест-хелперах → глобальное env-состояние «протекает» в последующие тесты.
2. **Реальный Docker/SSH поллинг в static-тестах** — `test_receive_yaml_version_field_ignored`
   в серийном прогоне выполнил РЕАЛЬНЫЙ `HealthcheckPoller` (20 попыток × 60s = 1276.8s)
   вместо monkeypatch-заглушки → весь прогон виснет.
3. **Флейк `test_deploy_mk_chain.py:128`** — реальный SSH к 1.2.3.4 в xdist-прогоне
   (monkeypatch на ForcedCommandChannel не применился к моменту вызова).

## Наблюдение (Observed)

- `tests/test_platform_export_metrics.py::TestCoordinator` — 3 теста пишут
  `os.environ["NODE_NAME"]="test-node"` + NODE_YAML_PATH/STATUS_METRICS_JSON/METRICS_CACHE_DIR
  напрямую, без restore (строки ~920/959/1023).
- `tests/test_status_page.py::_setup_app_env` — 5 env-переменных
  (NODE_YAML_PATH, STATUS_METRICS_JSON, NODE_NAME, NODE_CONFIGS_DIR, PLATFORM_DOMAIN)
  без отката.
- Серийный прогон 3044 тестов: `test_node_lifecycle_static.py::test_node_lifecycle_dry_run_contract`
  FAIL — «Expected NODE_NAME-required diagnostic, got: Cannot resolve NODE_YAML for node=test-node»
  — node-lifecycle.sh прочитал утекший `NODE_NAME=test-node` из env.
- Тот же прогон: `test_orchestrator_receive_version.py::test_receive_yaml_version_field_ignored`
  — 1276.8s с логами реального `HealthcheckPoller][docker] testproj not healthy after 60s poll window`
  (attempt 1/20 … 20/20) — т.е. monkeypatch `poll_until_healthy` НЕ сработал.
- xdist-прогон (6 воркеров, 238s): `test_deploy_mk_chain.py:128 assert 1 == 0` —
  реальный ForcedCommandChannel выполнил SSH «Connection to 1.2.3.4 port 22 timed out».
- В изоляции все перечисленные тесты проходят за 0.1-0.6s — дефект проявляется
  ТОЛЬКО при полном прогоне (порядок/состояние).

## Гипотеза (Suspected)

- Env-утечки: `os.environ` мутируется без monkeypatch → pytest не откатывает →
  последующие тесты видят чужое окружение. Триггер флейка №1 подтверждён:
  `NODE_NAME=test-node` → node-lifecycle.sh другой diagnostic.
- Флейк №2 (SSH к 1.2.3.4): monkeypatch ставится на атрибут модуля
  `core.internal.deploy.orchestrator_cli.ForcedCommandChannel`, но `_deliver`
  захватывает глобал `ForcedCommandChannel` из `__globals__` модуля. Если между
  импортом теста и вызовом модуль был пересоздан (reload/`del sys.modules` —
  например `test_status_page.py` и `test_platform_export_metrics.py` удаляют
  модули из sys.modules) — monkeypatch патчит НОВЫЙ объект модуля, а `_deliver`
  ссылается на старый globals. Требует верификации (needs investigation).
- Зависание №3 (1276.8s): вероятно та же reload-гонка — патч
  `HealthcheckPoller.poll_until_healthy` не виден классу, созданному внутри
  `receive()` → `DeployOrchestrator(projects_base=...)` создаёт НОВЫЙ poller
  с дефолтными 20×60s. Требует верификации (needs investigation).

## Влияние (Impact)

- Сессия-кодер в полном прогоне виснет на 20+ минут (1276.8s) или падает на
  флейках → повторный полный прогон → цикл «тест до первой ошибки → перезапуск
  всех тестов» (паттерн, зафиксированный в сессии 119 Wave 3 Бриф C).
- `make preflight` статический audit всегда timeout (300s < 1276.8s) → gate-канал
  недостоверен.
- Флейки подрывают Anti-Loop протокол: счётчик попыток растёт без реальной причины.

## Действие (частично выполнено 2026-08-02)

- ✅ `tests/test_platform_export_metrics.py::TestCoordinator` (3 теста): прямые
  `os.environ[...]` заменены на `monkeypatch.setenv(...)` — автоматический откат.
- ✅ `tests/test_status_page.py::_setup_app_env`: добавлен snapshot/restore env в
  finally-стиле (app.py читает env только на уровне модуля — restore после reload
  безопасен) + TRAP[BUG] комментарий.
- ⏳ Флейк `test_deploy_mk_chain.py` и зависание `test_orchestrator_receive_version.py`:
  root-cause требует отдельного расследования (reload-гонка sys.modules vs monkeypatch).
- ⏳ Рассмотреть: замена прямых `os.environ` мутаций на monkeypatch по всему tests/
  (grep `os.environ\[` в тест-хелперах); добавление `--timeout` (pytest-timeout) для
  статического прогона, чтобы виснущий тест фейлился быстро вместо 1276.8s.

| Status | Rev |
|--------|-----|
| OPEN (частично fixed) | **План 129 W4** (2026-08-09): reload-гонка monkeypatch/sys.modules + pytest-timeout; W2/W3 — xdist-race и env-утечки. После реализации реестр удаляется (план 131). |
