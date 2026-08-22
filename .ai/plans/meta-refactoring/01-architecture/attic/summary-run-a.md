# Architecture Forensics — Summary: TOP-10 Architectural Risks

**Дата:** 2026-08-22 · **Commit:** 4425ce0 · **Метод:** 10 параллельных форензик-направлений (+ вложенные параллельные проходы, консолидированы; сырые отчёты — `attic/`) · **Код не исправлялся**

## Executive Summary

| Метрика | Значение |
|---|---|
| Канонических находок | ~60 (ARCH-0101..ARCH-1015, 10 направлений, файлы findings-001..010) |
| Severity | CRITICAL ×4 · HIGH ×14 · MEDIUM ×~25 · LOW ×~17 |
| Вердикт | **NEEDS_ATTENTION** — дисциплина слоёв/SoT необычно сильная (Makefile-facade здоров, shell-libs — чистый DAG, entrypoints без inline python3), но риск концентрируется в: god-модулях на критическом пути деплоя, bug-mine docker-слоя, enforcement-дырах boundary-гейтов и lifecycle-механике на filesystem-маркерах |

## TOP-10 рисков

### R1. docker_orchestrator.py — плотнейшая bug-mine: 8 TRAP[BUG] (P0×1, P1×1, P2×2)
**[ARCH-1014]** CRITICAL/95% · core/internal/bootstrap/deploy/docker_orchestrator.py.
Восемь задокументированных багов в одном файле (включая P0/P1 инциденты); файл содержит незакоммиченные правки. Любой рефакторинг вокруг docker-операций с высокой вероятностью реанимирует задокументированный класс ошибок. Fix: characterization-тесты всех 8 TRAP-сайтов до любых правок.

### R2. context_deployer.py — god-module (1276 LOC) на bootstrap-критическом пути φ8
**[ARCH-0402 + ARCH-1009]** CRITICAL/94% · deploy+certs+vhosts+nginx+verify+audit+LLM+CLI в одном файле; крупнейший в репо, prior P1 TRAP (ModuleNotFoundError standalone), build-fallback ветка наименее тестирована. Launch-morning сценарий со свежим проектом в node.yaml попадает именно туда. Fix: characterization-тест порядка шагов через существующие DI-швы; сплит post-launch.

### R3. deploy_paths.py — fan-in хаб (~54–62 импортёра, максимум репо) без контрактной заморозки API
**[ARCH-1007 + ARCH-1004]** CRITICAL/HIGH · канон ВСЕХ deploy-путей: cert/S3-cache/core-deliverer/overlay. «Маленькое» изменение дефолта одного резолвера тихо редиректит доставку сертификатов/core по всему флоту; гейт RED блокирует merge до патчей ~50 файлов. Сосед по blast radius: timeouts.py → ~97 импортёров. Fix: freeze-zone (additive-only) + characterization-тест дефолтов; deprecation-период для публичных символов shared.

### R4. agent_check/__init__.py — package init как god-module (1092 LOC, ≥5 ответственностей)
**[ARCH-0401]** CRITICAL/96% · data model + git detection + 5 tool-runners + doc-header checker + FP registry + CLI в import root. Каждый потребитель компилирует всё; нет API surface; собственный TRAP файла признаёт «внутренности НЕ декомпозированы». Fix: раскладка на модули, __init__ как re-export shim.

### R5. check-suite.yaml — churn-магнит (18% коммитов репо), блокирующий ВСЕ проверки
**[ARCH-1001 + ARCH-1008]** HIGH/HIGH · hand-edited SoT gate-канона: 9 коммитов/6 fix-subjects за пост-reset историю, прецедент OOM-инцидента. Регрессия runner/memory-guard во время push-спайка = ни один hotfix не верифицируется. Fix: стабилизация smoke-сьюта (источник churn), freeze suite-ID/marker set, characterization-тест резолвимости маркеров.

### R6. Change amplification: семантическое изменение подсистемы = 20–61 файл
**[ARCH-1002]** HIGH/HIGH · чек-лист одного глагола: 4 hand-SoT + 4 generated + 2 docs (промерено по feat 002/003/005). Прецедент осцилляции подсистемы: heartbeat — 4 коммита роста → полное удаление за 2. Fix: не менять модель генераторов; `make new-verb`-scaffold для ручных шагов.

### R7. bootstrap/ — god-domain: ~35.8k LOC / ~34.5% всего Python ядра
**[ARCH-1013]** HIGH/95% · 4 субдомена (lifecycle, deploy, converge, certs) с концентрацией крупнейших файлов репо (см. ARCH-040x). Любое изменение lifecycle-контракта трогает четверть ядра. Fix: фиксация внешних контрактов фаз (state machine + characterization) до любых перемещений; механический сплит — запретить до этого.

### R8. Lifecycle ordering на неявных зависимостях: φ7 certificates зависит от φ4 secrets.env вне графа фаз
**[ARCH-0801 + ARCH-0802/0803/0805]** HIGH/90% · dependency graph omits DNS-01 edge → reorder/standalone запуск ломает cert issuance; плюс import-time side effects: process-wide basicConfig(WARNING) глушит INFO-логи (remote_executor), destructive logger.handlers wipe, sys.path self-bootstrap ×65+, atexit/signal при импорте decrypt_secrets. Fix: декларировать edge в графе фаз; логирование/init — только из entrypoints.

### R9. validate.sh dead path: FQDN-preflight молча пропускается на КАЖДОМ деплое
**[ARCH-0204]** HIGH/HIGH · engine.py резолвит несуществующий `internal/validate/validate.sh` (схлопнут в entrypoints DevPlan 173); else-ветка на IMP:6 «skipping FQDN check» — fail-open вместо fail-fast, конфликт FQDN двух проектов не детектируется вовсе. Fix: перенаправить путь (S, <10 LOC) + поднять ветку до WARN. Единственный S-fix в TOP-10 — делать первым.

### R10. Boundary-enforcement эрозия: живые нарушения проходят гейты by construction
**[ARCH-0201 + ARCH-0106 + ARCH-0107 + ARCH-0105 + ARCH-0108]** HIGH/MEDIUM кластер · (а) shared→config пробил leaf-инвариант — forbidden-shared-domains покрывает 3 из ~24 доменов; (б) internal→modules (dev_hosts→nginx internals) разрешён layers-контрактом «вниз» вопреки канону invoke_module_interface; (в) детектор приватных импортов ловит только `_`-префиксы — находки (а),(б) невидимы ему; (г) reconciler_projects.py в корне internal — серая зона вне всех контрактов; (д) converge/infra.py — 12 module-level mutable globals как cross-domain state [ARCH-0501]. Fix: расширение контрактов + карта владения доменами в детекторе (M-churn).

## За пределами TOP-10 (достойные упоминания)
- **ARCH-0301/0302/0303** — три module-level цикла на критических путях (check_suite↔self, engine↔lifecycle parent, manifest generator↔Makefile .PHONY), работающие только благодаря partial-init fallback; один reorder = ImportError.
- **ARCH-0601/0602** — practices/scaffold вызывают docker напрямую мимо sole-path гейта (AST-gate видит только compose+ps/inspect/exec).
- **ARCH-0605/0606** — vhost через f-string мимо template engine; S3/GHCR секреты читаются напрямую из os.environ мимо secrets-manager.
- **ARCH-0701/0702/0703** — triplicated name-regex, AGE-masking ×3, второй (уже разошедшийся) реестр портов в firewall.
- **ARCH-0901** — content_hash.py мёртв (0 импортёров) при двух живых SHA-256 копиях рядом.
- **ARCH-1015** — нестандартный TRAP[DI-SEAM] невидим TRAP-навигации.
- **ARCH-0110/0111** — lazy рёбра bootstrap→llm, deploy→monitoring вне гейт-надзора.
- Полный реестр — findings-001..010.md; сырые проходы — attic/.

## Приоритетный порядок фиксов (минимальный путь)
1. **Pre-launch S-пакет (~день):** ARCH-0204 dead preflight; ARCH-0107/0201 leaf-контракт + forbidden-domains расширение; ARCH-0106/0202 forbidden-internal-modules; ARCH-0801 edge в графе фаз.
2. **Pre-launch M-пакет:** characterization-тесты R1/R2/R3 сайтов (docker_orchestrator TRAPs, context_deployer порядок шагов, deploy_paths дефолты); check-suite freeze (R5).
3. **Post-launch планово:** R4 agent_check декомпозиция; R6 new-verb scaffold; R7 bootstrap domain fixация контрактов; R10 остаточное; duplication wave (ARCH-07xx).
