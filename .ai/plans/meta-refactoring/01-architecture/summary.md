# Summary — TOP-10 архитектурных рисков (merged: run-a + run-b)

Аудит выполнен ДВУМЯ независимыми прогонами в параллельных сессиях:
- **run-a** — полный сет: `findings-001…010.md` (50 находок: CRITICAL 3 / HIGH 12 / MEDIUM 20 / LOW 15), оригинал синтеза — `summary-run-a.md`.
- **run-b** — эта сессия: 53 находки, полные тексты 26 сохранились в `run-b/` (5 файлов), 27 — индексы в `run-b/README.md` (тексты утеряны при sweep-проходе run-a; см. примечание ниже).

Merged-база: **103 находки**, пересечение прогонов повышает confidence ключевых рисков.
Принцип отбора TOP-10: production failure likelihood × blast radius ÷ code churn.

> ⚠️ Примечание о целостности артефактов: обе сессии писали в один каталог одновременно;
> run-a переместила/удалила часть файлов run-b. Восстановление и провенанс — `run-b/README.md`.

## TOP-10

| # | ID | Risk (one-line) | Sev | Files | Minimal fix | Phase |
|---|----|-----------------|-----|-------|-------------|-------|
| 1 | ARCH-403+402 · ARCH-0039+0027 | Deploy-path god-кластер на критическом пути: context_deployer.py 1276 LOC = 5 подсистем (φ8), DeployOrchestrator транспортный god, lifecycle cli+phases+helpers ~3400 LOC — hotfix-коллизии двух команд в одних файлах, правки без characterization-сетки | CRIT/HIGH | bootstrap/deploy/context_deployer.py; deploy/orchestrator.py; lifecycle/{cli,phases/system,helpers/system}.py | extract `_render_and_provision_llm`; characterization-тесты порядка шагов через существующие DI-швы (без рефакторинга) | Post-launch (тесты — pre-launch) |
| 2 | ARCH-1004 · ARCH-0024 | deploy_paths.py — crosshair-хаб: fan-in 54–57 × 558 LOC × активный churn; «маленькая» правка резолвера путей = misdelivery по всему флоту + RED-блок merge | CRIT/MED | shared/deploy_paths.py | freeze-zone + characterization-тест дефолтов (additive-only) | Pre-launch (тест) |
| 3 | ARCH-801+802 · ARCH-0016 | Bootstrap state machine integrity: node-identity guard мёртв на done-state (чужая нода = no-op success); done вечен для большинства фаз (hash-invalidation не покрывает φ1–φ7/φ9–φ10 → re-bootstrap молча SKIP); UPDATE-DAG дыряв (φ11 без deps, φ12 без φ10, --run-phase вне mode) | HIGH | lifecycle/state_machine.py, cli.py | hash-invalidation на все мутабельные фазы или явный skip-notice; guard на все переходы | Pre-launch |
| 4 | ARCH-0014+0015 · ARCH-806 | Import-time side effects в remote/deploy пути: basicConfig(WARNING) при импорте глушит INFO/[IMP] телеметрию; decrypt_secrets hijack SIGTERM/SIGINT+atexit процесса-оркестратора; try-import меняет стратегию исполнения | CRIT/HIGH | bootstrap/remote_executor.py; secrets/decrypt_secrets.py | side effects только в main() ×3 | Pre-launch |
| 5 | ARCH-501+502+505 | Hidden state через env как транспорт: CLI инжектирует аргументы через os.environ; AGE-ключ доставляется мутируемым env между модулями; bulk-source secrets.env = амбиентные креды всего процесса | HIGH | deploy CLI, secrets/, crypto.py | явные параметры/fd вместо env-инжекции; scope env только вокруг subprocess | Pre-launch |
| 6 | ARCH-0031+0010 · ARCH-601 | SoT bypass в SSH/путях: `/opt/<ctx>/platform` захардкожен в двух оркестраторах вне deploy_paths; SSH-флаги production receive захардкожены в CI ×4 и разошлись с ssh_opts (нет ConnectTimeout); remove-project собирает SSH f-string мимо ssh_cmd_builder + /opt/projects литерал | HIGH/MED | deploy_orchestrator.py, context_overlay.py, workflows/*, orchestrator remove-project | `context_overlay_base()` в deploy_paths; job-level SSH_OPTS из `ssh_opts --shell`; сборка команд через builder | Pre-launch |
| 7 | ARCH-202+102 · ARCH-0006 | Leaf-layer inversion: shared/s3_client → config/domain — живое ребро к циклу через shared; контракт forbidden-shared-domains покрывает 3/23 доменов, enforcement не ловит | MED-HIGH | shared/s3_client.py, .importlinter | аксессор → shared, ребро инвертировать; расширить контракт на все домены | Pre-launch |
| 8 | ARCH-701+702 | Healthcheck-семантика расходится между реализациями: unhealthy → wait-and-retry (shell-фасад) vs immediate fail (Python SoT); HTTP-first short-circuit обходит docker-критерий канона «running AND healthy» | HIGH | lib/healthcheck.sh, healthcheck_poller.py, modules/*/healthcheck.sh | единый критерий в одном модуле, shell — тонкий фасад; short-circuit убрать | Pre-launch |
| 9 | ARCH-1001+1002+1003 | Change amplification: семантическое изменение = 20–40 ко-чейндж файлов; check-suite.yaml — churn-магнит (~18% коммитов, единственная точка правки CI/gate); entrypoint-manifest.yaml 2556 LOC generated-but-committed — дифф-шум в каждом feat | HIGH/MED | check-suite.yaml, entrypoint-manifest.yaml | генерация check-suite секций из module.yaml; diff-suppress generated | Post-launch |
| 10 | ARCH-703 | node.yaml `modules[].enabled` — 4 конкурирующие семантики под одним флагом (deploy / healthcheck / converge / документирование AI-PLATFORM.md): отключение модуля в одной подсистеме молча меняет поведение трёх других | HIGH | node_yaml/, deploy, healthcheck, converge | разделить на intent-поля или задокументировать единственную семантику + гейт | Post-launch |

## Ближняя периферия (11–16)

- **ARCH-201** · Мёртвый путь validate.sh — FQDN-preflight молча пропускается (HIGH).
- **ARCH-903** · sync_env_defaults: 56 hardcoded fallback-дефолтов дублируют SoT — drift уже случался.
- **ARCH-504** · platform_config `_loaded=True` до чтения файла — первый caller замораживает провал загрузки навсегда.
- **ARCH-901** · module_interface.invoke — Python→bash→Python round-trip.
- **ARCH-0045** · file_lock `_REENTRANT` depth-registry переживает exception-path → потеря flock-сериализации.
- **ARCH-0029** · org_secrets_provisioner глотает ошибки gh → promote «успешен» без секретов.

## Кросс-куттинг темы

1. **Критический путь держится на дисциплине, не на тестах** — три крупнейших файла (context_deployer, orchestrator receive, check-suite runner) без комбинаторных тестов; characterization-тесты дешевле рефакторинга.
2. **Гейты существуют, но enforcement дыряв** — границы ловят только `_`-приватность, leaf-инвариант shared не покрыт (3/23), cross-domain импорты проходят CI зелёными.
3. **Import-time side effects** — logging/signals/env-фриз зависят от ПОРЯДКА импортов; фикс однотипный: side effects только в main().
4. **Env/fs как скрытый транспорт состояния** — os.environ-инжекция аргументов, файловые маркеры без схемы/версии, bulk-source кредов.
5. **Семантический словарь размножается** — enabled×4, healthcheck×N реализаций с расходящейся семантикой, retry×3 стиля, SSH-канон vs raw f-string.

## Рекомендуемый pre-launch минимум (по ROI)

- **Волна A (S-фиксы, ~1 день):** №4 side effects→main(); №7 s3_client аксессор; №6 context_overlay_base + SSH_OPTS; №8 short-circuit убрать; №3 skip-notice для SKIP-фаз.
- **Волна B (тесты без правок прод-кода):** characterization deploy_paths-дефолтов; порядка шагов context_deployer; парсинга check-suite.yaml; receive-dispatch tar→result.
- **Волна C (при времени):** №5 env-транспорт → параметры; №10 intent-поля enabled.

## Доверие к результату

Все находки evidence-based (path:line + цитаты). Пересечение двух независимых прогонов подтверждает
ключевые риски (deploy_paths hub, context_deployer god, state machine idempotency, shared→config,
import-time side effects) — для них confidence повышен до HIGH независимо от схем оценки.
Расхождения схем ID: run-a = ARCH-0001…0050, run-b = ARCH-101…1006 (индексы в `run-b/README.md`).
