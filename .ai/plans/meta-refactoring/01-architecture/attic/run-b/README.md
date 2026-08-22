# run-b — второй независимый прогон архитектурного аудита (эта сессия)

Дата: 2026-08-22 · Режим: READ-ONLY, код не исправлялся · 10 направлений, по одному субагенту general-класса.

## Статус сохранности

Каталог `01-architecture/` куратировался ПАРАЛЛЕЛЬНОЙ сессией (run-a, файлы `findings-001..010.md`,
`summary-run-a.md`). При её sweep-проходе файлы этого прогона были перемещены в `attic/`, часть —
удалена. Восстановлено в `run-b/` 5 из 10 файлов; полные тексты остальных направлений утеряны
(`.ai/plans/` вне git), индексы находок сохранены ниже из журнала сессии.

| Файл | Направление | Статус |
|------|-------------|--------|
| `findings-03-circular-deps.md` | circular dependencies | ✅ сохранён |
| `findings-05-hidden-state.md` | hidden global state | ✅ сохранён |
| `findings-06-infra-coupling.md` | infra coupling | ✅ сохранён |
| `findings-08-lifecycle-init.md` | init/lifecycle | ✅ сохранён |
| `findings-09-overengineering.md` | overengineering | ✅ сохранён |
| — | boundaries (ARCH-101…104) | ❌ текст утерян, индекс ниже |
| — | dependency direction (ARCH-201…205) | ❌ текст утерян, индекс ниже |
| — | god modules (ARCH-401…406) | ❌ текст утерян, индекс ниже |
| — | duplication (ARCH-701…706) | ❌ текст утерян, индекс ниже |
| — | hotspots (ARCH-1001…1006) | ❌ текст утерян, индекс ниже |

## Полный индекс находок run-b (53 находки)

Утерянные направления — по индексам из журнала (title/severity/confidence достоверны; file:line
восстанавливаются grep'ом по указанным символам).

**Boundaries (ARCH-1xx)** — текст утерян:
- ARCH-101 · internal→modules dotted-импорты вне контрактов, живое ребро dev_hosts.py→nginx.dev_cert_generator · HIGH/HIGH
- ARCH-102 · forbidden-shared-domains покрывает 3/23 доменов; живое ребро shared/s3_client→config · MED/HIGH
- ARCH-103 · reconciler_projects.py на корне internal обходит независимость bootstrap↔deploy · MED/HIGH
- ARCH-104 · template_engine.py — кросс-доменная библиотека вне реестра shared/ + compat re-export · MED/HIGH

**Dependency direction (ARCH-2xx)** — текст утерян:
- ARCH-201 · Мёртвый путь validate.sh — FQDN-preflight молча пропускается · HIGH/HIGH
- ARCH-202 · shared → config: лист-инвариант нарушен, контракт не покрывает · MED/HIGH
- ARCH-203 · core.internal → core.modules.ngixin разрешён gap'ом layers-контракта · MED/HIGH
- ARCH-204 · Python вызывает тонкие shell-фасады над Python (двойной хоп) · MED/HIGH
- ARCH-205 · Cross-domain вертикали покрыты только acyclic-гейтом, не направлением · LOW/HIGH

**Circular deps (ARCH-3xx)** — ✅: ARCH-301 hub-циклы через __init__.py check_suite (LOW); ARCH-302 bootstrap.deploy соседи через пакетное пространство имён (LOW); ARCH-303 скрытое runtime-ребро lifecycle→deploy через importlib, вне статических гейтов (MED). Итог скана: 366 py-файлов / 902 рёбра / 9 hub-циклов; межпакетных, shell- и make-циклов — 0.

**God modules (ARCH-4xx)** — текст утерян:
- ARCH-401 · DeployOrchestrator god-класс транспортного слоя деплоя · HIGH/HIGH
- ARCH-402 · God-кластер lifecycle: cli + phases/system + helpers/system ~3400 LOC · HIGH/HIGH
- ARCH-403 · context_deployer — pipeline-хаб discovery+deploy+compose-gen+health+LLM+CLI · MED/HIGH
- ARCH-404 · agent_check/__init__.py — весь инструмент (1092 LOC) в package init · LOW/HIGH
- ARCH-405 · vhost_renderer — парсинг+рендеринг+fs+harness+CLI в одном модуле · MED/HIGH
- ARCH-406 · cert_orchestrator — 7 механизмов cert-домена в одном модуле · LOW/MED

**Hidden state (ARCH-5xx)** — ✅: ARCH-501 CLI→os.environ инжекция как транспорт аргументов (HIGH); ARCH-502 AGE-ключ мутируемым os.environ между модулями (HIGH); ARCH-503 converge/infra.py 11 глобалов, reset_state покрывает 4 (MED-HIGH); ARCH-504 platform_config._loaded latch кэширует провал навсегда (MED); ARCH-505 bulk-source secrets.env — амбиентные креды процесса (HIGH); ARCH-506 файловые маркеры без схемы/версии как межпроцессное состояние (MED).

**Infra coupling (ARCH-6xx)** — ✅: ARCH-601 remove-project SSH f-string мимо ssh_cmd_builder + /opt/projects литерал (HIGH); ARCH-602 orphan_reconciler docker prune напрямую, счётчик из stdout (MED); ARCH-603 verify_contracts свой _run_docker + таймауты вне SoT (MED); ARCH-604 tls_check openssl-stdout парсинг как контракт (MED); ARCH-605 context_deployer chown инлайн + таймауты 30/60/120 (LOW); ARCH-606 HTTP-дублирование hermes urllib / admin_client httpx (LOW).

**Duplication (ARCH-7xx)** — текст утерян:
- ARCH-701 · Healthcheck «unhealthy»: wait vs immediate fail — Python SoT расходится с shell · HIGH/HIGH
- ARCH-702 · HealthcheckPoller HTTP-first short-circuit обходит docker-критерий · HIGH/HIGH
- ARCH-703 · node.yaml modules[].enabled — 4 семантики (deploy/HC/converge/AI-PLATFORM.md) · HIGH/HIGH
- ARCH-704 · Atomic write вне канона: dev_hosts без fsync + вынужденные дубли modules/ · MED/HIGH
- ARCH-705 · Retry/poll-циклы: fixed-interval vs deadline vs exp-backoff вне shared/retry · MED/MED
- ARCH-706 · SSH remote-cmd: printf %q-канон vs raw f-string в 7 точках · LOW-MED/MED

**Init/lifecycle (ARCH-8xx)** — ✅: ARCH-801 node-identity guard мёртв на done-state — чужая нода = no-op success (HIGH); ARCH-802 UPDATE-DAG дыряв: φ11 без deps, φ12 без φ10, --run-phase вне mode (MED); ARCH-803 postgres-хук после compose up, non-fatal, env не реинжектируется (HIGH); ARCH-804 importlib-by-path скрывает зависимость state_machine→deploy (MED); ARCH-805 161 ленивый импорт — порядок secrets→env вне import-рёбер (LOW); ARCH-806 import-time двери: try-import меняет стратегию, HOME заморожен (LOW).

**Overengineering (ARCH-9xx)** — ✅: ARCH-901 module_interface invoke Python→bash→Python round-trip (MED); ARCH-902 schema-validation stack тройной subprocess-wrapping + vestigial ajv backend (MED); ARCH-903 sync_env_defaults 56 hardcoded fallback-дефолтов дублируют SoT — drift уже случался (LOW-MED); ARCH-904 shared/content_hash.py мёртвый модуль + 2 inline-дубликата (LOW); ARCH-905 AppConfig PROJECTS_BASE dual-binding local/remote на одном ключе (LOW).

**Hotspots (ARCH-10xx)** — текст утерян:
- ARCH-1001 · check-suite.yaml — churn-магнит: ~18% всех коммитов · HIGH/HIGH
- ARCH-1002 · Change amplification: семантика = 20–40 файлов ко-чейнджа · HIGH/HIGH
- ARCH-1003 · Generated-but-committed манифесты: entrypoint-manifest 2556 LOC дифф-шум · MED/HIGH
- ARCH-1004 · deploy_paths.py crosshair: fan-in 57 × 558 LOC × churn · MED/HIGH
- ARCH-1005 · Строковая subprocess-связность CLI-монолитов >900 LOC · MED/MED
- ARCH-1006 · Осцилляция подсистем: heartbeat добавлен и удалён за 6 коммитов · LOW/MED

Топ-3 hotspot-файла прогона: `core/check-suite.yaml`, `core/internal/shared/deploy_paths.py`, `core/entrypoint-manifest.yaml`.

## Синтез

Итоговый merged TOP-10 обоих прогонов — `../summary.md`. Оригинал summary run-a — `../summary-run-a.md`.
