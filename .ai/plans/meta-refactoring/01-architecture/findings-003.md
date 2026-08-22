# Направление 3 — Circular dependencies

Метод: сбор рёбер `^from core\.|^import core` по core/ + проверка замыкания 4 подозрительных рёбер из волны 1; анализ package-init паттернов. Агент: explore, 27 tool calls. Дата 2026-08-22.
Итог: **CYCLES FOUND: 2** (оба — package `__init__` ↔ субмодули; runtime-стабильны сегодня). Все 4 междоменных подозрения опровергнуты.

## ARCH-0037 — check_suite/__init__ ↔ субмодули (7-узловой self-cycle)
- Severity: HIGH · Confidence: HIGH · Churn: M (<300; 7 заголовков + __init__) · WHEN: post-launch
- Files: check_suite/__init__.py:80,91,110,113,117,149 ↔ diff.py:31, diagnostic.py:34, fingerprint.py:35, gate.py:36-37, runner.py:51, single.py:31, manifest.py:32
- Chain: `check_suite/__init__ → diff → check_suite/__init__` (×7 субмодулей)
- Evidence: `__init__.py:80 from core.internal.check_suite.diff import (…)` при `diff.py:31 from core.internal import check_suite as cs`; gate.py:37 импортирует PROJECT_ROOT/VALID_GATE_MODES из partial package
- Scenario: работает только по порядку импортов — константы :57-62 обязаны стоять ВЫШЕ строки 78; изолированный импорт одного субмодуля (test isolation) перезапускает полный init пакета
- Minimal fix: константы → leaf check_suite/models.py; субмодули берут из models; __init__ = чистый re-export hub (канонический PEP 562 паттерн уже есть в scaffold/__init__.py)

## ARCH-0038 — bootstrap/deploy/__init__ ↔ deploy_orchestrator/docker_orchestrator
- Severity: HIGH · Confidence: HIGH · Churn: M (<300; 2 заголовка) · WHEN: post-launch
- Files: bootstrap/deploy/__init__.py:19,20 ↔ deploy_orchestrator.py:87-94, docker_orchestrator.py:102
- Chain: `bootstrap/deploy/__init__ → deploy_orchestrator → bootstrap/deploy/__init__`
- Evidence: `__init__.py:19 from …deploy.deploy_orchestrator import ModuleDeployResult, orchestrate` при `deploy_orchestrator.py:87 from core.internal.bootstrap.deploy import (context_overlay, docker_orchestrator, …)` — выживание через submodule-fallback binding
- Scenario: добавление re-export выше строки 19 в __init__ ломает цикл; каждый новый deploy-субмодуль обязан импортироваться в двух местах
- Minimal fix: в orchestrator'ах импортировать конкретные пути (`from …deploy.context_overlay import …`), никогда `from …deploy import <module>`

## Опровергнуто с доказательствами (замыкания НЕТ)
| Ребро | Вердикт | Evidence |
|---|---|---|
| shared/s3_client → config (ARCH-0006) | NO CYCLE | config/platform_config.py:33-40 — только stdlib+yaml; config/__init__ без импортов |
| dev_hosts → modules/nginx (ARCH-0007) | NO CYCLE | nginx/dev_cert_generator stdlib-only (:33-41); единственное modules→internal ребро — allowlisted postgres hook |
| tor_proxy_check → bootstrap/firewall (ARCH-0001) | NO CYCLE | firewall.py:64,70 — только docker_user_policy + shared.exceptions; bootstrap→healthcheck рёбер нет |
| scaffold → practices (ARCH-0002) | NO CYCLE | все 3 импорта LAZY внутри функций; контракт practices «не импортирует scaffold»; lazy = документированный cycle-breaker |

## Near-miss риск (открытые цепи без замыкания)
1. `s3_ssl_cache → s3_client → config`: если platform_config однажды импортирует shared.exceptions — E1 замкнётся мгновенно циклом через shared. Приоритетное структурное обязательство (fix ARCH-0006).
2. dev_hosts fan-out в два слоя (scaffold.vhost_renderer + modules.nginx) — обратного ребра нет.
3. scaffold/__init__: PEP 562 lazy `__getattr__` сознательно удерживает latent 2-node цикл scaffold_helpers ↔ project_* (contained by design).

## Clean packages (contract-only __init__)
deploy/, loadtest/, practices/, converge/ — docstring-only, eager submodule imports отсутствуют.
