# P3 — Do Not Touch (Launch-Week Freeze List, 10-Synthesis)

Основано на: freeze-рекомендациях аудита 01-architecture (обе генерации), cascade-map 06-dependencies (Tier-1/Tier-2 blast radius), и правиле минимизации churn. Нарушение любого пункта = риск остановить единственный канал верификации/доставки в момент максимальной нагрузки. Тесты и characterization-покрытие этих зон — РАЗРЕШЕНЫ И РЕКОМЕНДОВАНЫ.

## Заморозка контрактов (additive-only; никаких rename/type-change)

1. **`core/internal/shared` leaf-контракты**: `deploy_paths.py` (fan-in ~216 файлов, freeze-zone + characterization тест текущих дефолтов), `timeouts.py` (89 файлов + gate pinning точных импортов), `node_yaml.py` (~33+ потребителей, schema unversioned), `ssh_opts.py`, `platform_ports.py`. Любое изменение — только новое имя рядом, старое не трогать. (DEP-0001/0002/0053, A-26)
2. **Wire-контракты forced-command CI**: `OrchestratorDeployResult` / DeliveryChannel / сигнатура `deploy()` — additive-only поля, без переименований; JSON-схему receive не менять. (DEP-0004/0048/0050)
3. **Имена как контракты — заморозка значений до запуска**: canonical verbs (`converge`, `receive`, …), make target `check-diff` (pre-push hook вызывает литералом), static detector names (triple store), env names (особо `AGE_SECRET_KEY` — 35+ файлов ×4 языка), docker network names, module names, suite-ID/marker set в check-suite.yaml. (DEP-0016/0017/0019/0022/0030/0043/0044; A-27)
4. **env_defaults значения** platform-infra.yaml: freeze после freeze-date; hotfix runbook `make fix-gate && git add -u`; одно значение = ≥5 gate-файлов + 3 regen outputs. (DEP-0055, A-31)

## Заморозка структурных зон (только чтение + тесты)

5. **`deploy/orchestrator.py`** (DeployOrchestrator): НЕ сплитить, не переносить rollback/receive кластеры до characterization. Разрешены: точечные фиксы REF-0003/0004/0011 минимальными диффами. (A-33, ARCH-0040)
6. **`bootstrap/deploy/context_deployer.py`**: no refactor pre-launch (единственное исключение аудита: extract `_render_and_provision_llm`, только если придётся трогать). (A-33)
7. **`bootstrap/deploy/docker_orchestrator.py`**: плотнейшая bug-mine (8 TRAP[BUG], P0×1 P1×1) — любые правки ТОЛЬКО после characterization-покрытия всех 8 TRAP-сайтов; в launch week — не трогать вовсе.
8. **Package `__init__` ordering**: `check_suite/__init__.py` (константы выше re-export блока — load-bearing), `bootstrap/deploy/__init__.py`, `deploy/engine/__init__.py` (test-patched `_flow` holder не перемещать). Реорганизация констант → constants.py разрешена как атомарный фикс REF-0107 с прогоном make check. (A-17)
9. **`state_machine.py` header region**: константы над submodule-imports; фазовый список enumerated трижды — менять синхронно все три или не трогать. (A-01/A-17, ARCH-0018)
10. **Generated manifests**: `entrypoint-manifest.yaml`, `secrets-manifest.yaml`, `platform-env.yaml`, `*_generated.py`, GENERATED-секции AGENTS.md — никогда руками; только регенерация. (canon invariant 11; A-29)
11. **`core/check-suite.yaml`**: schema v1 не расширять; suite-ID/marker freeze; правки записей — аккуратно, это единая точка отказа всех проверок. (A-27)
12. **subprocess_io canon adoption sweep** (AI-0057): drift не доказан, triage-only; не рефакторить 92 call-site'а перед запуском.
13. **Shell facades**: `lib/logging.sh`, `lib/paths.sh`, `lib/healthcheck.sh`, `lib/secrets.sh`, `lib/docker.sh`, `lib/ssh.sh` — keep-decisions канона; миграции shell→python в launch week запрещены (Strangler-Fig — пост-launch).
14. **Микро-оптимизации вне reliability**: PERF low-value band (007, 011, 031/032, 035–037, 043, 045–047, 056, 057, 066, 067, 073, 075) — не тратить ни часа.
15. **Тесты**: не расширять покрытие wholesale (TEST-41 band); не трогать quarantine/golden механизмы; не удалять тесты вне подтверждённого dup/dead списка (TEST-44..47); R3-stale skip аудит — после запуска. Никаких version bump'ов (constitution #6).
16. **Multi-tenant security**: не внедрять redis ACL/auth, per-project DB isolation changes, socket-proxy, rootless mid-launch — flat-trust модель задокументирована (SEC-0007/0008/0012/0013 residuals).
17. **Template mechanisms**: не консолидировать 3 механизма шаблонизации; monitoring templates `${PROJECT}` vs `{{UPPER_SNAKE}}` mismatch — отдельный functional ticket (SEC low-value note), не hotfix.
18. **Плановая нумерация/реестры**: не ренумеровывать `.ai/plans/*`, не редактировать agent-manager state, не пересоздавать generated locks вручную.

## Позитивная рамка

Что МОЖНО в замороженных зонах: characterization-тесты, аддитивные guard'ы (REF-0003..0013 спроектированы как минимальные диффы), документирование residual'ов, TRAP-аннотации. Правило разрешения сомнений: если изменение нельзя откатить одним revert без риска для receive/polling/state-machine — оно не входит в launch window.
