# 01-DevPlan.md — 123: Nightly hardening (пост-RC-121)

<!-- GREP_SUMMARY: devplan-123, nightly-hardening, converge-rc2, hermes-test-cleanup, p-13-ghcr, p-14-manifests, p-15-projects, bool-normalization, apt-timeouts, docstring-drift, local-path-remote-gate, compose-include-doc -->
<!-- STRUCTURE: ▶ $ARTIFACT_CONTRACT → ◇ Контекст → ◇ Волна 1 (CI/CD-канал) → ◇ Волна 2 (код-блокеры) → ◇ Волна 3 (системные закрытия) → ◇ Волна 4 (операторские шаги) → ⎋ Критерии ночного прогона -->

# region MODULE_CONTRACT
## @purpose  Девплан пост-верификации RC-121: закрыть найденные баги и классы багов, разблокировать CI/CD-канал (P-13/P-14/P-15), гарантировать штатный ночной прогон после пересоздания ОБЕИХ нод (test-e2e + tronyx-vps) и работу всех проектов в браузере (502→200).
## @scope    Волны 1-4: CI-workflows, core/internal, tests, node-configs, операторские шаги.
## @invariants
##   1. Все фиксы проходят: make check (до чистоты) → make gate MODE=fast (один раз в конце)
##   2. Прод-рендер vhost'ов НЕ меняется (byte-for-byte) — правило 7 RC-121
##   3. remote-команды никогда не получают локальные пути (усиливается гейтом T9)
##   4. Новый код — Python; shell — только тонкие фасады (языковая политика)
##   5. Никаких auto version bump, никаких правок generated-файлов руками (инвариант 11)
## @rationale  RC-121 вердикт STABLE с оговорками; верификация выявила: активный баг converge.sh (P0-класс), незакрытый cleanup hermes-test-контейнеров (503 на /health), неверную интерпретацию P-13 (403 на registry-cache, не на push L1), недетерминизм G3 (make -np) как главного кандидата P-14; 9/13 рекомендаций false-lead-log не выполнены.
## @changes  — 2026-08-03 | Создан по итогам верификации RC-121 + обсуждения с пользователем (скоуп: блокеры + системные; ноды: обе; P-14: воспроизведение; P-13: комбо)
## @changes  — 2026-08-03 | QA-сверка перед реализацией: T1 атрибуция P-13 разделена (build-hermes push:true 403 vs build-platform cache 403); T8 контракт sequential подтверждён кодом (deploy_orchestrator:673-677 прокидывает overlay, secrets_env_file/platform_root — нет); T11 конфликтные пакеты уже вынесены в отдельные шаги; T12 путь ci.mk; T3 tronyx-site позади origin/main; exec_module:38
# endregion MODULE_CONTRACT

## $ARTIFACT_CONTRACT

| Поле | Значение |
|------|----------|
| **PURPOSE** | Сделать ночной RC-прогон штатным: закрыть баги/классы багов из верификации RC-121, разблокировать CI, доставить проекты на прод (502→200) |
| **DESCRIPTION** | Волна 1: P-13 (ghcr cache→gha + права), P-14 (воспроизведение недетерминизма G3 + фикс + CI-диагностика), P-15 (push проектов). Волна 2: converge.sh rc=2, hermes-test cleanup. Волна 3: bool-нормализация, apt-таймауты, docstring-дрейфы/контракт sequential, гейт локальный-путь→remote, compose-include правило, единый python-deps список, pre-commit вывод. Волна 4: операторские шаги до ночного прогона |
| **RATIONALE** | Верификация 121: 14/14 фиксов подтверждены, но найдены 1 активный баг (converge), 1 незакрытый класс (hermes-test cleanup), 2 CI-блокера с переинтерпретацией (P-13 cache, P-14 make -np), 9 невыполненных рекомендаций false-lead-log |
| **ACCEPTANCE_CRITERIA** | (1) CI: platform-gate-fast GREEN, Build Hermes GREEN, core-deploy выполнен; (2) локальный стек 21/21 + *.local 200; (3) e2e 10/10 на пересозданной test-e2e; (4) прод-бустрап на пересозданной tronyx-vps + ACME; (5) все проекты 200 в браузере (tronyx.ru, sexydancerostov.ru, botanika.tronyx.ru) |
| **IMPLEMENTS** | Обсуждение 2026-08-03 (верификация RC-121, суперпозиция, решения пользователя) |
| **IMPACTS** | .github/workflows/, makefiles/, core/internal/bootstrap/, core/internal/scripts/, tests/, core/AGENTS.md, node-configs/, VPS 103.88.243.151, ghcr.io |
| **REQUIRES** | AGE-ключ, SSH root@VPS, gh auth, доступ к настройкам GitHub (пакеты ghcr), sudo на dev-машине (/etc/hosts) |

---

## Контекст (факты верификации 2026-08-03)

1. **converge.sh:75 — активный баг P0-класса**: `execute_remote_converge ...` без `|| remote_rc=$?` при `set -euo pipefail` → при rc=2 (VPS-self-detect) скрипт умирает до локального fallback. Паттерн уже исправлен в node-update.sh:89-90 (TRAP P0 2026-07-23). Следствие: `make converge` на VPS с self-detect НЕ выполняет reconcile.
2. **hermes-test-* контейнеры не чистятся** (false-lead #10 не закрыт): test_hermes_init.py — cleanup только в happy-path, session.py:179 фильтрует только label compose-project → exited-контейнеры → 503 на status-page /health.
3. **P-13 уточнение атрибуции (два разных 403)**: (а) `Build Hermes Images` (build-hermes.yml) — RED от **push:true** (docker/build-push-action@v6, строки 86-95): прямого `docker push` в ghcr.io/tronyx161/hermes-agent-base — 403 (пакет приватный/нет write:packages); cache там уже `type=gha` (строки 94-95), НЕ registry. (б) `Build Platform Agent` (build-platform.yml) Step 5 — docker-build-cache action БЕЗ cache-backend → **cache-to=type=registry** в ghcr.io/<owner>/ai-platform-build-cache — 403 на запись кэша (прецедент TRAP 2026-07-18, build-platform.yml:83); Step 7 — `make hermes-push-l1` (deploy.mk:133) non-fatal (`-docker push || echo`), exit 0 — маскирует 403 push. GITHUB_TOKEN логинится (build-platform.yml:84-89, build-hermes.yml:72-77), но запись/пакет 403. Проверено: VerificationReport P-13 «push L1 ghcr.io 403» = случай (а); registry-cache 403 = случай (б).
4. **P-14**: CI Step 5 `make check-manifests` падает, локально GREEN. Все 6 генераторов печатают diff при --check (G1-G6 подтверждено: первые 20 строк unified_diff в stderr) — diff в CI был, но не интерпретирован. Главный кандидат: **G3 entrypoint-manifest — `make -np` через `$(shell which gmake || which make)`** (makefiles/manifest.mk:131 → generate_entrypoint_manifest.py:96-98 `subprocess.run([gmake_path, "-np", "--dry-run"])`): локально `which gmake` = /opt/homebrew/bin/gmake **4.4.1** (Homebrew), CI ubuntu-latest → gmake отсутствует → `which make` = **make 4.4**; `$(shell)`-вызовы с GITHUB_REPOSITORY_OWNER (deploy.mk:129). Второстепенные: pre-commit cache key только по конфигу (platform-gate-fast.yml:68 `pre-commit-${{ hashFiles('.pre-commit-config.yaml') }}`); checkout@v7 без fetch-depth → shallow depth=1 (бьёт по git-зависимым гейтам Step 4, не по manifests).
5. **9/13 рекомендаций false-lead-log не выполнены**: FL1 (compose-include правило), FL2 (mapping маунтов; /var/lib/platform/* и /opt/platform-дефолты в compose остались), FL4 (sudo-обход), FL6 (гейт локальный-путь→remote), FL7 (единый python-deps список; requirements.txt дублирует jsonschema), FL9 (полный список Failed-хуков), FL10 (cleanup контейнеров), FL11 (контракт sequential: не прокидывает secrets_env_file/platform_root), FL12 (гейта untracked в core нет).
6. **Булевы из node_yaml CLI**: нормализация .lower() в парсерах есть (secrets_validator.py все ветки), но единой точки нет: 5 ручных .lower() + строгие сравнения secrets_manager.py:166/575, scaffold_helpers.py:156.
7. **apt-get таймауты**: канон DOCKER_APT_TIMEOUT покрыл только docker_installer; остались lifecycle/helpers/system.py:73-74 (timeout=120, legacy), tor_setup.py:70/87 (БЕЗ timeout — hang-риск), install-acme.sh:58 (без timeout).
8. **Docstring-дрейфы**: core_deliverer.py:78-90 (docstring/TRAP-текст с цепочкой «PLATFORM_REMOTE_BASE → PLATFORM_ROOT → /opt/platform», код делегирует platform_remote_base() БЕЗ PLATFORM_ROOT — устаревшая докa), deploy_paths.py:142 (DEFAULT_PLATFORM_BASE docstring упоминает PLATFORM_ROOT в цепочке — устарело), overlay_deliverer.py:179-182 (исторический TRAP[BUG]-коммент, НЕ docstring), deploy_orchestrator.py:631 («Overlay dir NOT passed in sequential path» — код на 673-677 уже прокидывает overlay_dir).
9. **exec_module без sys.modules** — тот же паттерн в test_gate_compose_include_sync.py:38 (spec.loader.exec_module(mod), строки 35-38; не 55-56) — ловушка.
10. **node-lifecycle.sh:28** — remote-сторона принимает `--age-secret-key-file` (ловушка passthrough).

---

## Волна 1 — CI/CD-канал (блокеры «проекты в браузере»)

### T1. P-13: GHCR push 403 (build-hermes) + cache→gha (build-platform) + проверка прав пакетов (комбо)
**Проблема**: Два независимых 403: (а) build-hermes.yml push:true L1 → 403 (RED); (б) build-platform.yml Step 5 cache-to=type=registry → 403 на кэш (прецедент TRAP 2026-07-18); Step 7 make hermes-push-l1 non-fatal маскирует push-403.
**Задачи:**
- [ ] (а) build-hermes.yml: диагностировать push 403 — GITHUB_TOKEN/приватность пакета `hermes-agent-base` (входит в O2). Код-сторона уже корректна (push:true фатальный, cache=gha) — если 403 на push, нужна операторская публикация/права, а не правка воркфлоу.
- [ ] (б) build-platform.yml Step 5 (Build hermes-agent-base): передать `cache-backend: gha` в docker-build-cache action (кэш GitHub Actions, без ghcr-прав). Платформенный platform-test.yml (backup-cron:177, hermes-agent-base:184) — тоже docker-build-cache без cache-backend → тот же 403-риск: перевести на gha (а не «оценить», т.к. 403 прецедент уже был 2026-07-18).
- [ ] (б) build-platform.yml Step 7 (Push L1): заменить `make hermes-push-l1` на явный `docker push` с проверкой rc и логом ошибки (не поглощать 2>/dev/null в CI-контексте; локальный DR-backup остаётся non-fatal — только CI-шаг делает явный fail при 403). Вариант: make-таргет с параметром `CI=1` → строгий режим.
- [ ] Оператор (Волна 4, O2): проверить в настройках GitHub: пакеты `hermes-agent-base` и `ai-platform-build-cache` — owner/приватность; GITHUB_TOKEN имеет packages: write (job permissions подтверждены); при желании вернуть registry-cache — диагностика 403 по логу.
**Приёмка**: Build Hermes Images GREEN в CI; L1 публикуется в ghcr.io/tronyx161 (backup + distribution base); core-deploy не блокируется.

### T2. P-14: воспроизведение недетерминизма G3 + фикс + CI-самодиагностика
**Проблема**: check-manifests RED в CI, локально GREEN (4 рана). Главный кандидат — `make -np` вывод в G3 (версии make: gmake 4.4.1 локально vs make 4.4 в ubuntu-latest; окружение $(shell)).
**Задачи:**
- [ ] Воспроизведение: `docker run -it ubuntu:24.04` (или ghcr.io/actions/runner-images-ubuntu24.04), установить make + python3.14 (deadsnakes PPA — как python_deps.py, system python 3.12 не подходит), чистый checkout origin/main → `make check-manifests` → сравнить расходящийся файл с локальным (ожидание: core/entrypoint-manifest.yaml, G3). Зафиксировать точный diff и причину (версия make 4.4 vs gmake 4.4.1 / окружение / порядок).
- [ ] Фикс по результату (один из):
  - (а) детерминизация G3: статический парсинг .PHONY из Makefile+makefiles/*.mk вместо `make -np` (устраняет класс навсегда), сохранив контракт allowed_verbs; ИЛИ
  - (б) фиксация версии make в CI (шаг setup make 4.4.1, идентичной локальной) + ключ кэша pre-commit с версией make; ИЛИ
  - (в) минимум: G3 принимает вывод `make -np` только как источник, но результат сортируется и фильтруется канонически + CI печатает полный diff.
- [ ] CI-самодиагностика (навсегда): в makefiles/manifest.mk check-manifests при errors>0 выводить: полный `git diff` по 6 генерируемым путям (не первые 20 строк), `make --version`, `python3 --version`, `which gmake/make`. (Генераторы уже печатают первые 20 строк diff — расширить до полного + окружение.)
- [ ] Pre-commit venv cache (platform-gate-fast.yml:64-68): добавить в ключ версию Python/make или hash генераторов (сейчас только .pre-commit-config.yaml).
**Приёмка**: воспроизведён diff-источник (задокументирован в отчёте сессии); CI-прогон на ветке GREEN; check-manifests при RED печатает полный diff + окружение.

### T3. P-15: доставка проектов через CI → 200 на проде
**Проблема**: прод vhost'ы загружены, но 502 — upstream'ы ждут CI-доставку; push проектов не выполнен (время).
**Задачи:**
- [ ] tronyx-site: локальный main (9e45334 «fix: remove legacy context field») ПОЗАДИ origin/main (5b7758f «Initial deploy from platform bootstrap» — уже на origin, merge-base = локальный main) → `git pull`/reset до origin/main → push (remote = TronyxLab ✓) → receive.
- [ ] dance-site, botanika: локальные main позади origin/main → `git pull`/reset до origin/main → push → receive.
- [ ] Мониторинг: receive-лог на ноде (forced-command), docker ps проектов, curl -k https://tronyx.ru, https://sexydancerostov.ru, https://botanika.tronyx.ru → **200** (не 502). Учёт: контекст tronyxlab (проекты в /opt/projects/<context>).
- [ ] Если core-deploy ещё не зелёный на момент T3 — T3 выполняется после T1/T2 (зависимость: CI-канал).
**Приёмка**: все 3 проекта 200 в браузере (curl -k + браузер оператора); receive-аудит в логах.

---

## Волна 2 — Код-блокеры ночного прогона

### T4. converge.sh: rc=2 passthrough (P0-класс)
**Проблема**: core/entrypoints/converge.sh:75 — `execute_remote_converge "${NODE_NAME}" "${PASSTHROUGH_ARGS[@]}"` без `|| remote_rc=$?`; при set -euo pipefail rc=2 убивает скрипт до локального fallback (строки 79-95) — reconcile не выполняется при self-detect.
**Задачи:**
- [ ] Фикс по образцу node-update.sh:89-90: `execute_remote_converge ... || local remote_rc=$?` (корректная идиома для set -e: `local rc; execute_remote_converge ... || rc=$?` — проверить, что `local remote_rc=$?` на следующей строке не ломает семантику; копировать точный паттерн node-update.sh).
- [ ] Тест: unit-тест на путь rc=2 (self-detect) — entrypoint НЕ умирает, выполняется локальный fallback (mocks: execute_remote_converge → rc=2; assert вызова internal converge.sh). Проверено: существующие тесты покрывают rc=2 для execute_update (tests/unit/test_remote_executor.py:92-121) и exit-логику внутреннего converge (tests/test_converge_exit.py), но НЕ путь rc=2 в entrypoint converge.sh (fallback на локальный exec) — тест нужно добавить.
**Приёмка**: `make converge NODE=<node>` при отсутствии SSH host выполняет локальный reconcile (rc=0/1 как задумано); тест rc=2 добавлен, gate GREEN.

### T5. hermes-test-* cleanup (false-lead #10)
**Проблема**: exited hermes-test-l1/l2-* контейнеры от pytest-сессий → 503 на status-page /health. Cleanup только в happy-path, sessionfinish фильтрует только compose-label.
**Задачи:**
- [ ] tests/test_hermes_init.py: обернуть создание/проверку контейнеров в try/finally (или fixture с yield + cleanup), гарантированно удалять контейнер при любом исходе (включая падение assert). Сейчас cleanup `docker rm -f` только в happy-path (строки 318-321, 486-489 — после assert).
- [ ] tests/_conftest/session.py:160-203 `_final_compose_cleanup()`: добавить второй фильтр `--filter name=hermes-test-` (docker rm -f всех её контейнеров) — сессионный sweep независимо от label (сейчас единственный фильтр `label=com.docker.compose.project=ai-platform-test`, строка 180 — hermes-test-* контейнеры без label не попадают).
- [ ] (Опционально) тест на cleanup-функцию.
**Приёмка**: после failed-прогона test_hermes_init.py в системе не остаётся exited hermes-test-*; status-page /health 200.

---

## Волна 3 — Системные закрытия классов

### T6. Булева нормализация единой точкой + гейт строгих сравнений
**Проблема**: node_yaml CLI возвращает Python-типы; нормализация .lower() размазана по потребителям (secrets_validator.py:346/350/359, reporting.py:73-76, system.py:75/132/422/512, modules_healthcheck.py:148); строгие сравнения secrets_manager.py:166/575, scaffold_helpers.py:156 (database != "false"), deploy_orchestrator.py:309 (enabled == "true").
**Задачи:**
- [ ] Нормализация в источнике: node_yaml CLI (core/internal/shared/node_yaml_cli.py `_cli_get`:129-146 — печатает сырое значение, Python-bool → "True") — булевы и числа возвращать lowercase-строками ("true"/"false"/числа без кавычек → строки) для --get-контракта; зафиксировать контракт в core/AGENTS.md.
- [ ] Гейт по образцу timeout_literals: `tests/gates/test_gate_bool_string_literals.py` — скан core/ на строгие `== "true"/"false"/"True"` и `!= "true"` БЕЗ .lower()/нормализации; allowlist пустой; R5 negative-тест.
- [ ] Зачистка остатков: secrets_manager.py:166/575 (tor_enabled — нормализовать вход или .lower()), scaffold_helpers.py:156 (зафиксировать тип database из project_yaml — типизированный аксессор), deploy_orchestrator.py:309 (вход уже нормализован — оставить комментарий).
**Приёмка**: гейт GREEN; строгих сравнений в core/ нет (кроме allowlist); контракт CLI задокументирован.

### T7. apt-get таймауты (канон)
**Проблема**: DOCKER_APT_TIMEOUT покрыл только docker_installer; остались helpers/system.py:73-74 (apt-get update/install, timeout=120 legacy), tor_setup.py:70/87 (apt-get update/install, БЕЗ timeout= — hang-риск), install-acme.sh:58 (apt-get install git, без timeout).
**Задачи:**
- [ ] shared/timeouts.py: добавить канон `APT_TIMEOUT = 300` (или переиспользовать DOCKER_APT_TIMEOUT для всех apt-get) — единый источник.
- [ ] lifecycle/helpers/system.py:73-74 → APT_TIMEOUT (убрать legacy 120); tor_setup.py:70/87 → timeout=APT_TIMEOUT (сейчас subprocess.run без timeout — hang-риск подтверждён); install-acme.sh:58 → таймаут через timeout-команду или Python-обёртку (тонкий фасад — политика).
- [ ] P-11-аудит: обновить список легитимных литералов (test_gate_timeout_literals домен apt).
**Приёмка**: все apt-get в bootstrap-цепи имеют канонический таймаут; gate GREEN.

### T8. Docstring-дрейфы и контракт sequential/parallel
**Проблема**: docstring'и описывают старое поведение; sequential не прокидывает secrets_env_file/platform_root (параллельный — прокидывает).
**Задачи:**
- [ ] core_deliverer.py:78-90 (`resolve_remote_base` docstring «PLATFORM_REMOTE_BASE → PLATFORM_ROOT → /opt/platform»), deploy_paths.py:142 (DEFAULT_PLATFORM_BASE docstring) — привести к фактической цепочке (**PLATFORM_REMOTE_BASE → /opt/platform, PLATFORM_ROOT УБРАН из remote-цепочки — TRAP 2026-08-03 в deploy_paths.py:190-194**). overlay_deliverer.py:179-182 — это исторический TRAP[BUG]-комментарий (описывает состояние ДО фикса DevPlan 108), НЕ docstring: его не править как docstring (история), при желании добавить примечание об актуальной цепочке.
- [ ] deploy_orchestrator.py:631 — docstring @invariants «Overlay dir NOT passed in sequential path» → актуальное поведение (код на 673-677 ПЕРЕДАЁТ overlay_dir=(overlays or {}).get(m_name) — docstring устарел).
- [ ] Решить контракт: sequential (deploy_orchestrator.py:673-677) вызывает deploy_docker_module без secrets_env_file/platform_root (дефолты None → /run/platform/secrets.env + platform_remote_base() в docker_orchestrator.py:273/279), parallel_runner.py:280-286 передаёт явно. Либо sequential прокидывает secrets_env_file/platform_root (паритет), либо зафиксировать расхождение TRAP'ом (если дефолты гарантированно совпадают) — выбор обосновать.
**Приёмка**: доки соответствуют коду; контракт двух путей един (или задокументированное расхождение).

### T9. Гейт «локальный путь → remote» (FL6)
**Проблема**: класс «локальный путь уходит в remote passthrough» закрыт точечно (TRAP), гейта на passthrough-аргументы нет; node-lifecycle.sh:28 — ловушка на remote-стороне.
**Задачи:**
- [ ] `tests/gates/test_gate_local_path_in_remote.py`: скан makefiles/, core/entrypoints/, core/internal/bootstrap/ — passthrough-аргументы (PASSTHROUGH_ARGS) и build_ssh_cmd/remote_executor не должны форвардить переменные-пути с локальными значениями (AGE_SECRET_KEY_FILE, PLATFORM_ROOT и т.п.) в remote-команды; allowlist пуст; R5 negative.
- [ ] ⚠️ НЕ дублировать существующий гейт `tests/gates/test_gate_no_hardcoded_local_paths.py` (проверяет hardcoded пути-литералы в Python-коде) — новый гейт про **форвард переменных в remote-аргументы**, скоуп другой (passthrough/build_ssh_cmd), отличить в гейте.
- [ ] node-lifecycle.sh:28: удалить/переименовать приём `--age-secret-key-file` на remote-стороне (флаг больше не форвардится — убрать ловушку) ИЛИ пометить TRAP с запретом форварда.
- [ ] core/AGENTS.md: правило «remote-команды никогда не получают локальные пути» в Cross-layer rules.
**Приёмка**: гейт GREEN; remote-сторона не принимает локальные пути; правило в AGENTS.md.

### T10. compose-include правило + mapping маунтов (FL1/FL2)
**Проблема**: правило «относительные bind-пути резолвятся от include-файла» не задокументировано; маунты /var/lib/platform/* (docker-compose.yml:58-82: postgres-data:58, wal-archive:64, backup-spool:70, hermes-data:82; + backup-logs:76 /var/log/platform/backup), /opt/platform (monitoring/docker-compose.base.yml:106-107: PROMETHEUS_TARGETS_DIR/PROMETHEUS_RULES_DIR дефолты), /opt/node-configs (status-page/docker-compose.base.yml:45: NODE_CONFIGS_DIR дефолт) — жёсткие дефолты для macOS.
**Задачи:**
- [ ] core/AGENTS.md или core/modules/AGENTS.md: TRAP/секция «compose include — относительные bind-пути резолвятся от include-файла; колокализация обязательна» (FL1).
- [ ] Mapping-документ (FL2): таблица всех compose-маунтов (файл, source, target, резолв) + статус macOS-совместимости; для /var/lib/platform/*, /opt/platform, /opt/node-configs — либо env-параметризация (по образцу STATUS_METRICS_JSON/HTPASSWD_FILE/NGINX_CERT_DIR — L1/L2 RC-121 прецеденты), либо явная dev-документация (данные локальные, .local/).
**Приёмка**: правило задокументировано; mapping-документ в артефакте сессии; новые модули следуют контракту.

### T11. Единый перечень python-зависимостей (FL7)
**Проблема**: core/requirements.txt дублирует jsonschema из python_deps Step 1b; Step 2 (pip -r requirements.txt) без --ignore-installed — хрупкая связка.
**Задачи:**
- [ ] Определить единый SoT: pyproject.toml [project] dependencies (комментарий requirements.txt указывает на него) → requirements.txt генерируется (sync-гейт по образцу env-контракта: count + содержимое) ИЛИ python_deps читает pyproject напрямую.
- [ ] Уточнение по факту кода: конфликтные пакеты (typing_extensions Step 1:390-401, jsonschema Step 1b:404-420, pyopenssl Step 1c:423-439) УЖЕ вынесены в отдельные шаги с --ignore-installed — Step 2 (python_deps.py:442-452, pip -r requirements.txt) для них НЕ нуждается в --ignore-installed (они уже поставлены поверх). Задача не «добавить --ignore-installed для трёх конфликтных», а проверить, не осталось ли НЕ вынесенных конфликтующих apt-пакетов в Step 2 (домен: остальные зависимости requirements.txt) + зафиксировать единый SoT.
**Приёмка**: нет ручного дублирования списков; Step 2 не воспроизводит RECORD-конфликт на bare VPS (остальные пакеты проверены).

### T12. pre-commit полный список Failed-хуков (FL9)
**Проблема**: pre-commit-run summary показывает не все Failed-хуки.
**Задачи:**
- [ ] makefiles/ci.mk:219-226 (таргет pre-commit-run, НЕ gate.mk — такого файла нет): после `pre-commit run --all-files` выводить полный список Failed-хуков (парсинг вывода) — минимум: `pre-commit run --show-diff-on-failure` флаг или обёртка с grep-фильтром.
**Приёмка**: в CI/локально при RED виден каждый упавший хук.

---

## Волна 4 — Операторские шаги ДО ночного прогона (не код)

- [ ] **O1. /etc/hosts (P-20)**: `sudo sh -c 'printf "127.0.0.1 ai-platform.local tronyx-site.ai-platform.local dance-site.ai-platform.local botanika.ai-platform.local platform.ai-platform.local\n" >> /etc/hosts'` — браузерная проверка *.local.
- [ ] **O2. GHCR-пакеты (P-13)**: проверить настройки `hermes-agent-base`, `ai-platform-build-cache` (owner/приватность/write) в tronyx161; при необходимости — публикация L1 (public package, DevPlan 116 B3 D1).
- [ ] **O3. Sudo-обход для ночной сессии (FL4)**: настроить sudoers (или документированный обход) — либо принять curl --resolve как канон ночных сессий (документировать в nightly-промте).
- [ ] **O4. Проекты**: dance-site/botanika — убедиться, что локальные main на origin/main (перед T3 push).
- [ ] **O5. Обнуление нод**: пересоздать test-e2e и tronyx-vps (решение пользователя «оба»); после — ночной прогон.

---

## Критерии ночного прогона (после выполнения девплана)

1. **CI**: platform-gate-fast GREEN (T1/T2), Build Hermes GREEN, core-deploy выполнен, L1 в ghcr.
2. **Локальный стек**: make up → 21/21 healthy; *.local → 200/301 (после O1 — браузер).
3. **E2E**: bootstrap на пересозданной test-e2e штатно (≤2 попытки, без системных фиксов), test-node 10/10.
4. **Прод**: bootstrap на пересозданной tronyx-vps штатно (возможен 1 операторский сброс state), ACME-сертификаты, nginx healthy.
5. **Проекты в браузере**: tronyx.ru, sexydancerostov.ru, botanika.tronyx.ru → **200** (502→200), контент корректен; receive-аудит в логах.
6. **Классы багов не воспроизводятся**: converge rc=2 (T4), hermes-test cleanup (T5), 503 на /health отсутствует, check-manifests в CI GREEN.

## Порядок выполнения

T1 → T2 (CI-канал) → T4 → T5 (код-блокеры) → T3 (проекты; после зелёного CI) → T6-T12 (системные, батчами через make check) → Волна 4 (операторские, согласование с пользователем) → финальный make gate MODE=fast → передача ночного промта.
