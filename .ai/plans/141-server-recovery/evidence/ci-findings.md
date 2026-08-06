# CI Findings — session 141 (night)

Append-only. TS in MSK (UTC+3).

## 2026-08-06T01:55:00+03:00 (MSK) — baseline
- run: — (snapshot cycle-01)
- вывод: 40 последних ранов — все от 2026-08-04 и ранее (последний run_id 30907282654, Mirror 15s). Новых ранов от push docs(140)/feat(140) нет.
- локальный контекст: 140-коммиты в ветке main локально (4801e377, 11ef2c74); первый git push оператора 22:40:45Z завершился rc=1 за 420s (timings.tsv phase1) — pre-push gate fail или сетевой обрыв.
- вердикт: OBSERVE — ждём повторный push; CI доступен, rate-limit нет.
## 2026-08-06T02:37:00+03:00 (MSK) — push 4801e377 (feat 140) landed 23:26:25Z — 4 рана, 3 новых проблемы
- run: 31056366725 platform-test (push, 0s, failure, 0 jobs)
- вывод: «This run likely failed because of a workflow file issue». Зарегистрированное имя workflow = путь файла
  (name: не читается парсером GitHub). actionlint: `context "secrets" is not allowed here` на строках 162/169 —
  `if: ${{ secrets.DOCKER_HUB_TOKEN != '' }}` и `== ''` (шаги C-11, добавлены в 4801e377, DevPlan 136 W11 T11.10).
  Документация GitHub (Context availability, jobs.<job_id>.steps.if): secrets НЕ входит в доступные контексты.
  PyYAML файл валиден — ломается именно GitHub-парсер (шаги: name→path, 0 jobs, instant failure).
- вердикт: RED — платформенный баг 140: platform-test мёртв на push/PR пока шаги не переписаны
  (напр. через env: DOCKER_HUB_TOKEN_PRESENT: ${{ secrets.DOCKER_HUB_TOKEN != '' }} на уровне job env).

## 2026-08-06T02:37:00+03:00 (MSK) — platform-gate-fast 31056367576 (push, failure, 435s)
- вывод: Step 9/10 static_audit: `python3 -m core.internal.test_runner --marker static_audit` → TIMEOUT 300s (exit 124).
  Check report: 8/9 PASS, 1 FAIL (static_audit). Остальные степы зелёные (make/pytest/bash).
- вердикт: FLAKE-подобно операторским локальным падениям pre-push (timings: «transient doxygen flake»,
  «static_audit timeout», 2× rc=1). Холодный раннер без кэша. НЕ связан с состоянием сервера.
- следствие: workflow_run-цепочка НЕ запустилась (см. ниже). Ожидаемое падение core-deploy (голый сервер)
  отложено до следующего push'а с зелёным гейтом.

## 2026-08-06T02:37:00+03:00 (MSK) — workflow_run цепочка после гейта (3 рана, все SKIPPED)
- run: 31056824178 Build Platform Agent / 31056824227 core-deploy / 31056824285 Mirror to TronyxLab
- вывод: workflow_run триггерится на completed (любой conclusion), job-уровневый if
  (conclusion==success && event==push) → все джобы пропущены, раны «skipped». Дизайн работает как задумано.
- вердикт: OBSERVE — повторный push оператора (gate retry) запустит цепочку заново.

## 2026-08-06T02:37:00+03:00 (MSK) — Build Hermes Images 31056367581 (push, failure, 96s) — ПЕРВЫЙ ран этого workflow
- вывод: L1 build собран, push `ghcr.io/tronyx161/hermes-agent-base:latest` → `denied: permission_denied: write_package`.
  Логин в build-hermes.yml: GITHUB_TOKEN (secrets.GITHUB_TOKEN, packages: write декларирован). Пакет
  hermes-agent-base подтверждён PUBLIC (gh api users/Tronyx161/packages/container/hermes-agent-base → public).
  Ограничение GitHub: GITHUB_TOKEN не может писать в публичные пакеты → write_package denied.
- вердикт: RED — L1-автопуш сломан в обоих мирах: (а) GITHUB_TOKEN vs public-пакет (этот ран),
  (б) до 140 workflow «Build Hermes Images» ни разу не запускался (первый ран — сегодня).
  Фикс: логин через PAT с write:packages (напр. GHCR_PAT secret) или пересмотреть видимость пакета.
- NOTE: ВАЖНО для оператора — на голом сервере L1 не будет лежать в ghcr.io свежим; контекстные L2-сборки
  (hermes-build-context) тянут L1 как build-base → могут упасть на отсутствии/старой версии.
## 2026-08-06T03:20:00+03:00 (MSK) — push 6c099a9b (fix 141): гейт SUCCESS, цепочка сработала, core-deploy = ОЖИДАЕМОЕ падение
- run: 31058578546 platform-gate-fast (push 6c099a9b, success, 468s) — гейт прошёл со 2-й попытки
- run: 31059030393 core-deploy (workflow_run, failure, 12s) — **ОЖИДАЕМОЕ**: SSH pre-flight FAILED
  `root@***: Permission denied (publickey,password)` — ci-deploy ключа нет на голом сервере
  (создаётся только bootstrap φ2). Соответствует брифу задачи: задокументировано, НЕ фиксить кодом.
- run: 31059030388 Mirror to TronyxLab (workflow_run, success)
- run: 31059030399 Build Platform Agent (workflow_run, failure, 2min) — smoke-степ:
  `service "hermes-agent" refers to undefined volume hermes-data: invalid compose project`.
  Новая причина фейла (Aug 4 был sha-resolve race). Смок теста L1-образа ломается на volume-контракте.
- вердикт: core-deploy = EXPECTED (task brief); Build Platform Agent = RED (реальный баг smoke-контракта, не связан с сервером)

## 2026-08-06T03:20:00+03:00 (MSK) — push 2665a866e (fix 141): фикс parse-бага подтверждён, R1-фейл = флаки
- run: 31059042978 platform-test (push 2665a866e, failure, 2min) — **parse-баг ИСПРАВЛЕН** (1 job запустился);
  фикс оператора = ховст secrets в job-env DOCKER_HUB_AUTH (коммит-месседж ссылается на находку дорожки ci-ops).
- run: 31059043002 platform-gate-fast (push 2665a866e, failure, 100s) — единственный упавший чек: pytest gates
  → test_gate_r1_no_pass_tests (R1, exit 1). НЕ воспроизводится: тот же скан на том же дереве (HEAD=2665a866e,
  tests/ идентичен 6c099a9b, где R1 прошёл) → 481 файл, 0 нарушений.
- вердикт: FLAKE — гонка xdist (probe/tmp .py файлы соседних гейтов попадают в окно R1-скана).
  Рекомендация оператору: при следующем push R1 должен пройти; если повторится на идентичном дереве — чинить
  изоляцию R1-скана (скан замораживать список файлов до walk, или исключать tmp-префиксы).
- NOTE: tests/e2e/test_chaos_resilience.py:924-927 — SyntaxWarning "\$" invalid escape (126-era, хайгиена, не блокер)
- run: workflow_run цепочка 2665a866e (31059137592-4) — все SKIPPED (гейт упал), дизайн-контракт соблюдён
## 2026-08-06T05:40:00+03:00 (MSK) — КОРРЕКЦИЯ атрибуции + РУТ-КОЗА фейла гейтов (2665a866e, a9f0e1e5)
- ⚠️ ПРЕДЫДУЩАЯ ЗАПИСЬ (03:20 MSK) ОШИБОЧНА: «1 failed» — это НЕ test_gate_r1_no_pass_tests.
  Имя в логе было warnings-summary grouping header (SyntaxWarning из ast.parse внутри R1-скана),
  а FAILED-строка и секция «= FAILURES =» вырезаются чек-раннером (в логе их нет).
  R1 на обоих деревьях ЧИСТ (проверено импортом реального гейт-модуля: 481 файл, 0 нарушений).
- run: 31070915825 / 31059043002 platform-gate-fast (failure, ~100s) — «1 failed, 482 passed, 19 skipped, 1 deselected»
- РУТ-КОЗА: tests/gates/test_gate_ci_env_vars.py::test_ci_env_vars_match_platform_env (детерминированный RED):
  platform-test.yml:88 добавляет job-env `DOCKER_HUB_AUTH: ${{ secrets.DOCKER_HUB_TOKEN != '' && secrets.DOCKER_HUB_USERNAME != '' }}`
  (фикс parse-бага secrets-in-if, 2665a866e). Гейт (T7 SoT, allowlist-пуст) требует: каждый env:-ключ воркфлоу
  ∈ platform-env.yaml env_defaults ИЛИ _GITHUB_BUILTINS ИЛИ _WORKFLOW_LOCAL_ENV_VARS (DOCKER_BUILDKIT, REGISTRY,
  L1_IMAGE, IMAGE_NAME, VPS_USER, NODE_HOST_MAP, REQUIRE_HONESTY_MODE, INTEGRATION_MODE, SKIP).
  DOCKER_HUB_AUTH отсутствует во всех трёх → violation → RED. Доказательство: grep DOCKER_HUB_AUTH platform-env.yaml → rc=1.
- почему «1 failed» одинаковый в обоих ранах: DOCKER_HUB_AUTH живёт в platform-test.yml с 2665a866e, a9f0e1e5 его не трогал.
  6c099a9b (зелёный гейт) — переменной ещё не было. Локальный make check оператора (01:07, rc=2) фейл не показал
  (возможно, кэш/дифф-скоуп) — CI поймал.
- ФИКС (2 варианта): (а) добавить DOCKER_HUB_AUTH в _WORKFLOW_LOCAL_ENV_VARS (как INTEGRATION_MODE — workflow-local
  select-константа); (б) зарегистрировать в platform-env.yaml env_defaults. Вариант (а) чище — переменная не платформенная.
- вердикт: RED — блокирует гейт до фикса; НЕ связан с сервером; ожидаемое падение core-deploy отложено до зелёного гейта.
- NOTE: SyntaxWarning tests/e2e/test_chaos_resilience.py:924-927 (invalid \$-escapes) — косметика, НЕ причина фейла.
## 2026-08-06T05:47:00+03:00 (MSK) — push 401e579b: gates-чек ЗЕЛЁНЫЙ (фикс DOCKER_HUB_AUTH сработал), static_audit — env-example drift
- run: 31071581563 platform-gate-fast (push 401e579b, failure, 745.8s) — 8/9 checks PASS; gates ✓ (env-vars гейт больше не падает)
- run: 31071581545 platform-test (push 401e579b, failure)
- ФЕЙЛ: static_audit exit 1 (638.4s) → test_env_example_matches_platform_env_defaults:
  `AssertionError: Expected 93 env_defaults, got 94` (3821 pass / 1 fail / 25 skip / 3847 total)
- ПРИЧИНА: оператор зарегистрировал DOCKER_HUB_AUTH в SoT core/platform-infra.yaml (2 вхождения) и перегенерировал
  platform-env.yaml (189: DOCKER_HUB_AUTH: 'false' — 94 vars), но `.env.example` НЕ перегенерирован (0 вхождений, 93 vars) →
  sync-тест env_defaults vs .env.example = RED. Классический дрейф generated-цепочки (инвариант 11), фикс = make generate-env-example.
- вердикт: AMBER (механический дрейф, один прогон генератора) — следующий push оператора должен быть зелёным.
## 2026-08-06T06:00:00+03:00 (MSK) — РЕЦЕПТ фикса env-example drift (детально, для следующей итерации оператора)
- Тест: tests/test_env_contract.py::test_env_example_matches_platform_env_defaults (static_audit marker)
- Двойной инвариант (оба шага обязательны при добавлении env_defaults):
  (1) tests/test_env_contract.py:46 — EXPECTED_ENV_DEFAULTS_COUNT = 93 → 94 (документированный протокол
      синхронизации в комментарии над константой; история: 86→89→90→93);
  (2) .env.example регенерация (make generate-env-example / fix-gate) — сейчас 0 вхождений DOCKER_HUB_AUTH
      (93 vars), platform-env.yaml уже 94 → ключ DOCKER_HUB_AUTH отсутствует в .env.example.
- Статус b554859f: НЕ содержит ни (1), ни (2) (stat: context_deployer/contact-points/ci.mk) →
  текущий гейт-ран упадёт на static_audit с тем же AssertionError.
- вердикт: AMBER→RED (механический дрейф, 2 пропущенных шага) — после их выполнения гейт должен стать зелёным.
## 2026-08-06T06:30:00+03:00 (MSK) — ВЕХА: гейт bd60d96d ЗЕЛЁНЫЙ → цепочка → core-deploy = ОЖИДАЕМОЕ падение (задокументировано по брифу)
- run: 31076557800 platform-gate-fast (push bd60d96d, success, ~13.5min) — env-parity фикс (count 94 + .env.example реген)
  закрыл static_audit; гейт прошёл ПОЛНОСТЬЮ.
- run: 31077314431 core-deploy (workflow_run, failure, ~11s) — **ОЖИДАЕМОЕ**: SSH pre-flight
  `root@***: Permission denied (publickey,password)` — ci-deploy ключа нет на голом tronyx-vps
  (создаётся только bootstrap φ2). Соответствует брифу задачи: НЕ фиксить кодом.
- run: 31077314446 Mirror to TronyxLab (success)
- run: 31077314447 Build Platform Agent (failure, 100s) — тот же smoke-баг `undefined volume hermes-data`
  (реальный CI-баг, не связан с сервером; см. запись 03:20 MSK)
- СЛЕДУЮЩИЙ ШАГ (ожидание): оператор завершит bootstrap φ2 (создаст ci-deploy ключ) → следующий зелёный
  гейт должен дать core-deploy SUCCESS.
## 2026-08-06T06:45:00+03:00 (MSK) — platform-test bd60d96d (31076557838): ci-docker фаза RED, НЕ блокирует деплой-цепочку
- run: 31076557838 platform-test (failure, ~14min) — fast-фаза зелёная, ci-docker фаза: make test MARKER=smoke → exit 2
  (1 failed, 7 passed, 31 errors); component → exit 0.
- ФЕЙЛ: tests/test_smoke_platform.py::test_all_compose_configs_valid — модульные compose-файлы INVALID standalone:
  `service "X" refers to undefined volume Y-data: invalid compose project` (backup-cron/clickhouse/hermes-agent/
  langfuse/logging/minio/monitoring/postgres/...). Дизайн volume-SoT (root compose = 12 volumes, DevPlan 116 B3 T4,
  модульные top-level volumes = ∅) делает standalone `docker compose -f module.yml config` невалидным.
  31 ERROR в smoke: сервисы не поднялись (loki/grafana/prometheus/hermes/langfuse/...).
- КОНТЕКСТ: platform-test красный давно (Aug 3-4: litellm timeout и пр.; тест из 119, не 140/141).
  Текущий вариант — плавающая причина из серии; pre-pull undefined-volume warning'и были и раньше (Aug 4: 8 вхождений).
- вердикт: INFO для дорожки — platform-test НЕ триггер деплой-цепочки (цепочка = workflow_run platform-gate-fast),
  деплой не блокирован. Для оператора: кандидат на отдельный баг-фикс вне ночной сессии (составной разбор
  модульных compose с root volumes либо standalone-валидация с -f root -f module).
