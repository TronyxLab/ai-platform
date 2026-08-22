# Направление 10 — Architectural hotspots (churn × complexity × fan-in)

Метод: три прохода — (A) churn `git log --since="6 months ago" --name-only -- core/ | sort | uniq -c | sort -rn` + fan-in по rg + amplification `git show --stat` по feat-коммитам; (B) git log 90d name-only frequency × wc -l top30 × fan-in × fix-density по commit subjects; (C) сводный TRAP-density/fan-in анализ основного прохода. Консолидация: 2026-08-22. Commit: 4425ce0.

**Важные оговорки методики:** история сброшена на 1.0.0 (`b1d6e2b feat(release): 1.0.0 … history reset`), всего 49 коммитов — «6 месяцев» фактически = вся пост-reset история; churn-числа занижены для кода до reset. Fan-in замеры расходятся между проходами (deploy_paths: 54 / 57 / 62; timeouts: 97) из-за разных методик (src-only vs src+test vs шаблоны rg) — трактовать порядково как ~50–60 и ~100 соответственно.

## Hotspot-матрица, проход A (commits-6mo)

| # | file | commits-6mo | LOC | fan-in (importers) | риск |
|---|------|-------------|-----|--------------------|------|
| 1 | core/check-suite.yaml | 9 | 252 | SoT: читают runner.py+gate.py+CI | HIGH |
| 2 | core/internal/check_suite/runner.py | 4 | 403 (pkg ~2200) | 12 (pkg) | MED-HIGH |
| 3 | core/entrypoint-manifest.yaml | 5 | 2556 | generated | MED |
| 4 | core/internal/shared/deploy_paths.py | 3 | 558 | **57** | HIGH |
| 5 | core/internal/shared/timeouts.py | 1 | 158 | **97** | MED-HIGH |
| 6 | core/internal/scripts/sync_env_defaults.py | 4 | 961 | 0 (subprocess) | MED |
| 7 | core/internal/scaffold/project_scaffolder.py | 4 | 928 | 3 | MED |
| 8 | core/internal/test_runner.py | 3 | 909 | 0 (subprocess) | MED |
| 9 | core/secrets-manifest.yaml | 4 | 415 | generated | LOW-MED |
| 10 | core/AGENTS.md | 5 | 400 | generated-секции | MED |

Fan-in-рекорды shared-слоя: `timeouts.py` — 97 импортёров, `deploy_paths.py` — 57, `node_yaml.py` — 46, `ssh_opts.py` — 9 (замер 2026-08-22).

## Hotspot-матрица, проход B (commits-90d, fix-density)

| # | Файл | LOC | commits90d | fixes90d | fan-in src/test | Ответственность | Хрупкое сопряжение |
|---|------|-----|-----------|----------|------------------|------------------|--------------------|
| 1 | core/check-suite.yaml | cfg | 9 | **6** | SoT-config | все сьюты make check | runner.py + parity-гейты |
| 2 | internal/check_suite/runner.py | 403 | 4 | 2 | 6/0 | subprocess-движок check/gate | xdist math, flock, memory-guard |
| 3 | internal/deploy/org_secrets_provisioner.py | 274 | 4 | 2 | 0/1 | автопровижн org-секретов | gh CLI, env_reader last-match |
| 4 | internal/scaffold/project_scaffolder.py | 928 | 4 | 2 | 0/1 | new-project pipeline | template-manifest, NodeYaml write-back |
| 5 | internal/scripts/sync_env_defaults.py | 961 | 4 | 1 | 0/1 | генерация .env.example | 2 SoT-YAML + G5 freshness gate |
| 6 | internal/shared/deploy_paths.py | 558 | 3 | 0 | **54**/**6** | канон ВСЕХ deploy-путей | cert/S3-cache/core-deliverer/overlay |
| 7 | internal/shared/docker_ops.py | 824 | 1 | 0 | 18/1 | единственный docker CLI слой | docker_sole_path gate, timeouts |
| 8 | internal/deploy/orchestrator.py | 1220 | 1 | 0 | 3/**11** | единый фасад деплоя проекта | channels, verbs, history schema |
| 9 | internal/bootstrap/deploy/context_deployer.py | **1276** | 1 | 0 | 0/5 | деплой контекста в φ8 | state_machine, node.yaml, nginx_reload |
| 10 | internal/shared/ssh_opts.py | 112 | 1 | 0 | 7/2 | SoT SSH-флагов | positional read в lib/ssh.sh |

## ARCH-1001 — check-suite.yaml — churn-магнит: 18% всех коммитов репозитория
- Severity: HIGH · Confidence: HIGH · Churn: 9 commits · Phase: Post-launch
- Files: core/check-suite.yaml (252 LOC) · Symbols: checks[], gate_modes, tier
- Evidence: 9 коммитов из 49 total (18%): `git log --oneline --since="6 months ago" -- core/check-suite.yaml`. 7 из последних 8 — ci/smoke-фиксы (65881db, 0bc3590, 06c30e3, a3f5668, e5a81cc, 4c8b893, 4425ce0) — серия стабилизации 900s-hang смоука. Файл hand-edited SoT (core/check-suite.yaml:5-9 «Единый SoT-манифест набора проверок», оба executor'а читают только его).
- Scenario: каждое изменение CI/smoke-поведения требует правки одного YAML, но он же — канонический порядок шагов gate; ошибка в записи роняет и `make check`, и `make gate`, и CI (push-gate.yml). Подтверждено проходом B: 9 коммитов/6 fix-subjects (топ репозитория); прецедент OOM-инцидента (TRAP v1.0.1, macOS hang).
- Impact: единая точка отказа dev-цикла; стоянка каждого deploy/release чекпойнта (check — единственная санкционированная команда)
- Minimal fix: стабилизировать smoke-сьют (источник 7 коммитов), после чего churn манифеста упадёт естественным образом; схему v1 не расширять без bump version (fingerprint-кэш); characterization-тест: check-suite.yaml парсится и каждый маркер резолвится ≥1 тестом

## ARCH-1002 — Change amplification — семантическое изменение подсистемы = 20-40 файлов
- Severity: HIGH · Confidence: HIGH · Churn: Makefile-система — 3 коммита/6mo, но каждый feat её трогает · Phase: Post-launch
- Files: Makefile+makefiles/ (1495 LOC), core/check-suite.yaml, core/secret-definitions.yaml (398), core/platform-infra.yaml (281), core/notification-catalog.yaml + generated: entrypoint-manifest.yaml, secrets-manifest.yaml, AGENTS.md×2
- Evidence: `git show --stat 6d7297a` (feat 005, удаление heartbeat) = 27 файлов; `git show --stat 2ca0334` (feat 003 telegram) = 39 файлов (26 в core/+tests/+Makefile), среди них 8 production-модулей + 6 манифестов/доков; `git show --stat 4865fb6` (feat 002) = 61 файл. Чек-лист добавления глагола (по manifest_driver.py:92-97 `_GENERATED_PATHS` + generate_entrypoint_manifest.py:8-13): (1) Makefile/makefiles/*.mk .PHONY [hand] → (2) G1 generate-entrypoint-manifest → entrypoint-manifest.yaml#allowed_verbs [gen], (3) глоссарий root AGENTS.md [gen, G4R], (4) core/AGENTS.md [gen, G4], (5) при новой проверке — check-suite.yaml [hand], (6) при новых секретах — secret-definitions.yaml [hand] → secrets-manifest.yaml [gen]. Гейты-блокаторы дрейфа: tests/gates/test_gate_manifests_up_to_date.py, test_gate_manifest_dag_acyclic.py, test_gate_manifest_signature_parity.py, test_gate_manifest_integrity.py.
- Scenario: добавить/убрать глагол или подсистему → ко-чейндж 4 hand-SoT + перегенерация 4 generated + 2 docs; пропуск любого — RED в `make check MARKER=check-manifests`.
- Impact: стоимость семантического изменения умножается на ~6 обязательных спутников; commit 6d7297a показал 4 затронутых подсистемы разом
- Minimal fix: не менять модель (генераторы + гейты работают), но ввести `make new-verb`-scaffold, автоматизирующий шаги 1-4

## ARCH-1003 — Generated-but-committed манифесты: entrypoint-manifest.yaml 2556 LOC при ручном SoT 1495 LOC
- Severity: MEDIUM · Confidence: HIGH · Churn: 5 commits · Phase: Post-launch
- Files: core/entrypoint-manifest.yaml (2556 LOC, 10 GENERATED-маркеров), core/secrets-manifest.yaml (415), AGENTS.md (497, 18 маркеров), core/AGENTS.md (400, 4 маркера)
- Evidence: churn entrypoint-manifest.yaml = 5 коммитов (4425ce0, 4c8b893, 65881db, 6d7297a, 4865fb6) — каждый feat/CI-фикс тянет перегенерацию; генератор G3 cycle-break задокументирован (generate_entrypoint_manifest.py:20-24, гейт test_gate_generate_entrypoint_manifest_no_self_read.py). manifest_driver.py:190-191 — G4/G4R проверяют AGENTS.md.
- Scenario: ручная правка allowed_verbs в YAML переживает до следующего generate-manifests, затем молча затирается; ревьюер видит в diff 2556-строчного файла шум перегенерации.
- Impact: diff-шум в каждом feat-коммите затрудняет ревью реальных изменений; риск скрытого ручного дрейфа между перегенерациями
- Minimal fix: изолировать generated-секции маркерами (как сделано для AGENTS.md) — коммитить только allowed_verbs/gates-блоки, уменьшив diff-шум

## ARCH-1004 — Shared-facade blast radius: timeouts.py 158 LOC → 97 импортёров
- Severity: MEDIUM · Confidence: HIGH · Churn: deploy_paths 3, timeouts 1 · Phase: Post-launch
- Files: core/internal/shared/timeouts.py (158/97), deploy_paths.py (558/57, churn 3), node_yaml.py (—/46), ssh_opts.py (112/9)
- Evidence: fan-in замер командой выше; deploy_paths.py — единственный true crosshair (fan-in ~54-62 × LOC 558 × churn 3, в т.ч. 2ca0334 и 6d7297a). timeouts.py не менялся 6mo (1 коммит = reset), но любое изменение сигнатуры = ~97 файлов.
- Scenario: рефакторинг константы/фасада deploy_paths (путь, timeout, DSN-хелпер) ломает компиляцию в десятках модулей.
- Impact: главный сдерживающий фактор рефакторинга shared-слоя; риск не в размере, а в fan-in
- Minimal fix: ничего не мигрировать; pyright strict-контракт + deprecation-период для публичных символов shared-фасадов (сейчас смена имени = мгновенный RED без грациозного периода)

## ARCH-1005 — Строковая (subprocess) связность CLI-монолитов >900 LOC — слепая зона fan-in-анализа
- Severity: MEDIUM · Confidence: MEDIUM · Churn: sync_env_defaults 4, scaffolder 4, test_runner 3 · Phase: Post-launch
- Files: core/internal/scripts/sync_env_defaults.py (961), scaffold/project_scaffolder.py (928), test_runner.py (909), bootstrap/deploy/context_deployer.py (1276)
- Evidence: прямые импорты = 0/3/1/0 соответственно, но test_runner вызывается строкой из check-suite.yaml:151,164-174 (`cmd: python3 -m core.internal.test_runner …`); sync_env_defaults — из check-suite cmds + генераторов (rg «sync_env_defaults» core/internal/scripts/manifest_driver.py). LOC-гейт tests/gates/test_gate_loc_allowlist.py допускает 1276 LOC (context_deployer в allowlist).
- Scenario: переименование/смена CLI-флагов монолита не видна import-графу — ломаются строки cmds в check-suite.yaml; grep-навигация агентов по «import» не находит потребителя.
- Impact: рефакторинг этих файлов требует ручного grep по yaml/mk; статические гейты не покрывают строковую связность
- Minimal fix: единый реестр subprocess-вызовов (модуль-константа с CLI-контрактом) или smoke-тест на `--help`-парсинг каждого entrypoint из check-suite.yaml

## ARCH-1006 — Осцилляция подсистем: heartbeat — 4 коммита роста, затем полное удаление (feat 005)
- Severity: LOW · Confidence: MEDIUM · Churn: подсистема целиком за 6 коммитов · Phase: Post-launch
- Files: core/internal/scripts/heartbeat_check.py (удалён 6d7297a), shared/notifications.py, shared/telegram_notifier.py, healthcheck/watchdog.py, deploy/hooks/post_deploy_chain.py
- Evidence: churn heartbeat_check.py = 4 коммита до удаления; feat 003 (2ca0334) добавил heartbeat-checker out-of-band, feat 005 (6d7297a) удалил подсистему целиком (−1624 строк, 27 файлов) через 2 коммита. tor_proxy_check.py — 3 коммита (465444a добавил canary → 6d7297a упростил).
- Scenario: фича добавляет точку интеграции в 4-6 модулей, затем откатывается — но ко-чейндж манифестов остаётся в истории как шум; риск повторного цикла для следующей подсистемы (кандидат: notifications).
- Impact: стоимость ложного старта подсистемы непропорциональна из-за amplification; сигнальный паттерн для review: фича, трогающая >3 shared-модулей + 2 манифеста
- Minimal fix: pre-implementation чек-лист в Brief/DevPlan: перечислить обязательные спутники (ARCH-1002) до старта; флаг-фичи вместо физического удаления при неопределённости

## ARCH-1007 — deploy_paths.py: хаб с fan-in ~54-62 на критическом пути доставки
- Severity: CRITICAL · Confidence: HIGH · Churn: M (characterization-тест) · WHEN: pre-launch
- Files: core/internal/shared/deploy_paths.py · Symbols: CANONICAL_DEPLOY_PATHS, projects_base(), letsencrypt_live(), platform_remote_base()
- Evidence: 54 src-импортёра (максимум в репо, см. также замеры 57/62 в проходе A/C), 6 тест-файлов, parity-gates (test_gate_deploy_paths + entrypoint-manifest)
- Scenario: «маленькое» изменение дефолта/env-override одного резолвера тихо редиректит цели доставки сертификатов/S3-cache/core-deliverer по всему флоту
- Impact: misdelivered certs/core на prod; гейт RED блокирует merge до патчей ~50 файлов
- Minimal fix: freeze-zone на существующие резолверы (additive-only ключи) + characterization-тест, пинящий текущие дефолты
- Cross-ref: ARCH-1004

## ARCH-1008 — check-suite.yaml + runner.py: самый чинимый узел блокирует ВСЕ проверки
- Severity: HIGH · Confidence: HIGH · Churn: L · WHEN: pre-launch
- Files: core/check-suite.yaml; internal/check_suite/runner.py
- Evidence: 9 коммитов/6 fix-subjects (топ репозитория); прецедент OOM-инцидента (TRAP v1.0.1, macOS hang)
- Scenario: правка suite-spec или регрессия memory-guard во время push-спайка → make check виснет/OOM → ни один hotfix не верифицируется
- Impact: стоянка каждого deploy/release чекпойнта
- Minimal fix: freeze suite-ID/marker set; characterization-тест резолвимости маркеров
- Cross-ref: ARCH-1001 (дубль-подтверждение вторым проходом)

## ARCH-1009 — context_deployer.py: крупнейший файл на bootstrap-критическом пути (φ8)
- Severity: HIGH · Confidence: HIGH · Churn: L · WHEN: pre-launch
- Files: internal/bootstrap/deploy/context_deployer.py (1276 LOC, #1 в core/)
- Symbols: deploy_context, _step_certs/_step_deploy_projects/_step_vhosts/_step_nginx_reload/_step_verify
- Evidence: 5 test-fan-in файлов; prior HI TRAP (ModuleNotFoundError standalone); DI-швы уже есть (runner/facts/fns параметры)
- Scenario: launch-morning deploy-context со свежим проектом в node.yaml попадает в build-fallback ветку (ghcr auth hiccup) — наименее тестированную комбинаторику
- Impact: частичный даун стека при запуске
- Minimal fix: characterization-тест порядка шагов + idempotent-skip логики через существующие DI-швы
- Cross-ref: ARCH-0402 (god-module разбор), ARCH-1013

## ARCH-1010 — orchestrator.py receive-verb: единая точка входа CI-деплоя, максимальная test-связность
- Severity: HIGH · Confidence: MED · Churn: L · WHEN: pre-launch
- Files: internal/deploy/orchestrator.py (+channels, verbs.py) · Symbols: DeployOrchestrator.receive(), classify_verb
- Evidence: 1220 LOC; test fan-in 11 (максимум); единственная точка приёма forced-command receives
- Scenario: правка layout/version-string payload'а в launch week ломает dispatch receive → атомарный отказ всех git-push деплоев проектов
- Impact: production deploy-канал вниз; fallback — ручной emergency deploy-project
- Minimal fix: dispatch-level contract-тест: tar-payload → receive() → OrchestratorDeployResult, включая путь lock-contention

## ARCH-1011 — sync_env_defaults.py: G5-freshness gate тормозит hotfix-коммит
- Severity: MEDIUM · Confidence: HIGH · Churn: L (процедурно) · WHEN: pre-launch (runbook)
- Files: internal/scripts/sync_env_defaults.py (961 LOC); platform-infra.yaml; secret-definitions.yaml
- Evidence: gate требует byte-identical регенерации; P1 TRAP уже был (sys.path str-vs-Path)
- Scenario: hotfix env_defaults без `generate-manifests` → check-manifests RED → pre-commit отклоняет коммит под давлением времени
- Impact: задержка security/hotfix push в launch week
- Minimal fix: процедурно — `make fix-gate && git add -u` обязателен в hotfix-runbook (уже задокументировано — enforce чеклистом); freeze env_defaults-секции после freeze-date

## ARCH-1012 — org_secrets_provisioner: best-effort глотает ошибки gh → пустые секреты у первого деплоя
- Severity: MEDIUM · Confidence: HIGH · Churn: S · WHEN: pre-launch
- Files: internal/deploy/org_secrets_provisioner.py · Symbols: _ORG_SECRET_PLAN, provision flow
- Evidence: инвариант #1 сознательно swallow'ит ошибки gh (return True); построен после инцидента «mirror-org core-deploy упал за 9s с пустым host»
- Scenario: rate-limit/scope-ошибка gh при promote → секреты молча отсутствуют → первый CI-deploy проекта падает с пустым VPS_HOST далеко от корня
- Impact: запутанный launch-day фейл с неверной диагностикой
- Minimal fix: post-promote guard: assert `gh secret list` содержит обязательные имена (VPS_HOST, VPS_SSH_KEY, AGE_SECRET_KEY) до успеха promote
- Cross-ref: bug-аудит BUG-0903 (та же поверхность со стороны runtime)

## ARCH-1013 — bootstrap/ — god-domain: 35.8k LOC / ~34.5% всего Python ядра
- Severity: HIGH · Confidence: 95% · Churn: — · WHEN: pre-launch
- Files: core/internal/bootstrap/** (~35.8k LOC, 4 субдомена: lifecycle, deploy, converge, certs)
- Symbols: —
- Evidence: сводный замер основного прохода: доля bootstrap-домена в core Python ~34.5%; внутри него сконцентрированы крупнейшие файлы репо (context_deployer 1276, phases/system 1236, cli 1164, helpers/system 1001, secrets_manager 973 — см. ARCH-040x)
- Scenario: любое изменение lifecycle-контракта затрагивает четверть ядра; code review и тестирование деградируют по времени
- Impact: главный структурный риск рефакторинга — домен слишком велик для безопасного изменения
- Minimal fix: не механический сплит, а фиксация внешних контрактов фаз (state machine + characterization-тесты) до любых перемещений

## ARCH-1014 — docker_orchestrator.py — плотнейшая концентрация багов: 8 TRAP[BUG] (P0×1, P1×1, P2×2)
- Severity: CRITICAL · Confidence: 95% · Churn: — · WHEN: pre-launch
- Files: core/internal/bootstrap/deploy/docker_orchestrator.py
- Symbols: —
- Evidence: grep 'TRAP[BUG]' — 8 маркеров в одном файле, включая P0 и P1 инциденты (плотнейшая bug-mine репозитория); файл в working-tree изменён незакоммиченными правками
- Scenario: следующий рефакторинг вокруг docker-операций с высокой вероятностью реанимирует один из задокументированных классов ошибок
- Impact: docker-слой — самый хрупкий компонент платформы
- Minimal fix: приоритетное покрытие characterization-тестами всех 8 TRAP-сайтов до любых правок; рассмотреть вынос в отдельную волну meta-refactoring

## ARCH-1015 — Нестандартный маркер TRAP[DI-SEAM] в deploy/orchestrator.py невидим для TRAP-грепов
- Severity: MEDIUM · Confidence: 80% · Churn: S · WHEN: post-launch
- Files: core/internal/deploy/orchestrator.py
- Symbols: TRAP[DI-SEAM]
- Evidence: в файле используется нестандартный тип ловушки TRAP[DI-SEAM]; весь инструментальный поиск знаний (`grep "TRAP\["`, doc-headers, агентские навигационные правила) рассчитан на закрытый словарь типов [BUG/DEBT/DECISION/PERF/INCIDENT/BUSINESS]
- Scenario: критичное архитектурное знание (DI-шов) не находится стандартной навигацией агентов/разработчиков
- Impact: knowledge locality нарушена — ловушка есть, но не работает
- Minimal fix: переименовать в TRAP[DECISION] с qualifier или добавить тип в закрытый словарь канона

### Приоритезация направления
1. ARCH-1014 (CRITICAL/95%) — bug-mine docker_orchestrator: тесты до правок.
2. ARCH-1007 (CRITICAL/HIGH) — freeze+characterization deploy_paths.
3. ARCH-1001/1008 (HIGH/HIGH) — стабилизация smoke снимает главный churn-поток.
4. ARCH-1002 (HIGH/HIGH) — `make new-verb` scaffold снижает amplification.
5. ARCH-1013 (HIGH/95%) — контрактная фиксация bootstrap-домена перед любым сплитом.
6. ARCH-1009/1010 (HIGH) — contract-тесты φ8 и receive-verb.
7. ARCH-1004..1006, 1011, 1012, 1015 — планово.
