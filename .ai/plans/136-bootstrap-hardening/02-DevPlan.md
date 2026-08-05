# 136-bootstrap-hardening — 02-DevPlan.md

$START_DEVPLAN

$ARTIFACT_CONTRACT
PURPOSE:               Мега-план закрытия класса «свежий бутстрап открывает баги»: регрессионные тесты всех D1-D23, фиксы латентных экземпляров, перевод ручных конфигов в код бутстрапа, автоматизация приёмочной верификации (test-node + e2e-verify + dev-hosts), закрытие «ничьих зон» (QA 134, Debt T9-T11/hermes, runbook CI-ключей).
DESCRIPTION:           Двенадцать волн (W1-W8 исходные + W9-W12 meta-расширение) в двух потоках (repo/server) с субагентным исполнением: каждая волна = отдельный Code-субагент с чёткими AC, проверками make test-summary/check-diff, фикс-циклом make check и коммитом ≤2 по правилу U-83. Волны W1-W8 — закрытие известных дефектов D1-D23 и харнесс; волны W9-W12 (meta) — долгоиграющие латентные дефекты, выявленные углублённым аудитом 6 субагентов (bootstrap / lifecycle / CI / modules / security / test-infra), требующие длинной проверки (мульти-прогоны, гонки, DR-сценарии, нагрузка). Волны независимы и мержятся инкрементально.
RATIONALE:             См. Brief 136 §2 (первопричины R1-R8). Ключевое: тесты без харнесса не дают «будущей» защиты; харнесс без тестов не верифицирует прошлые фиксы; ручные конфиги (MaxStartups) — источник повторяющихся инцидентов; «ничьи зоны» (134, T9-T11, hermes) теряются без Debt-протокола. Мета-расширение (W9-W12) закрывает R9-R14: первопричины, выходящие за пределы «свежего бутстрапа» — гонки конкурентных деплоев, мёртвая идемпотентность (content-hash), отсутствие реальной DR для AGE-ключей, security-эскалационные цепочки через sudoers, тихий дрейф healthcheck-контрактов и CI false-negative. Эти дефекты «дороги в проверке» (multi-run harness, simulation, audit-trail forensics), но ночью автономно выполнимы.
ACCEPTANCE_CRITERIA:   Глобальные: (1) матрица покрытия D1-D23 в VerificationReport 136 — каждый дефект имеет регресс-тест или обоснование ops/env; (2) полный цикл «пересозданная голая нода → make test-node → make e2e-verify → повторный bootstrap no-op» проходит автоматически; (3) 0 ручных конфигов, требуемых бутстрапом (MaxStartups в коде); (4) gate ALL PASS; (5) 134 верифицирован, Debt-записи T9-T11/hermes с Rev, runbook создан; (6) МЕТА: ни один L-класс дефект (concurrency, idempotency-deadcode, audit-gaps) не остаётся без регрессионного теста или Debt с Rev; (7) МЕТА: sudoers PRIVESC цепочки (S-1/S-2/S-3) либо закрыты фиксами, либо оформлены TRAP[DECISION] с осознанным risk-acceptance и Rev-условием; (8) МЕТА: все CI false-negative (C-1/C-2/C-5) либо закрыты gate-тестами, либо Debt; (9) МЕТА: DR-стратегия AGE мастер-ключа задокументирована.
IMPLEMENTS:            Brief 136; находки 5 субагентов-разведчиков (2026-08-05); находки 6 углублённых аудит-субагентов (meta-расширение 2026-08-05: bootstrap B-*, lifecycle L-*, CI C-*, modules M-*, security S-*, test-infra T-*).
IMPACTS:               core/internal/bootstrap/ (security_posture.py, phases, deploy-modules.sh, install-acme.sh, lifecycle/{state_store,state_machine,cli}.py, lifecycle/helpers/{users,system}.py, deploy/{orchestrator,channels,receive_flow,deploy_history,deploy_engine,orchestrator_cli}.py), core/internal/verify_sweep.py (новый), core/internal/dev_hosts.py (новый), core/internal/shared/audit_logger.py, core/internal/secrets/decrypt_secrets.py, core/internal/bootstrap/setup-node.sh (sudoers), core/internal/bootstrap/firewall.py, core/modules/{status-page,monitoring,logging,nginx,langfuse,hermes-agent}/healthcheck.sh, core/modules/status-page/app.py, makefiles/, entrypoint-manifest.yaml, check-suite.yaml, AGENTS.md, core/AGENTS.md, tests/ (~25 файлов), tests/_conftest/{smoke,session,wave_pipeline,e2e,infra}.py, tests/e2e/, tests/gates/, node-configs/, .github/workflows/{platform-test,platform-gate-fast,core-deploy,mirror,build-platform}.yml, .github/actions/{sha-resolve,docker-build-cache}/action.yml, docs/ci-secrets-rotation.md (новый), docs/age-master-key-dr.md (новый), .ai/plans/134 (VerificationReport), .ai/plans/136-bootstrap-hardening/04-Debt.md, nginx overlay в репо.
REQUIRES:              main с 23 фиксами 135; доступ к tronyx-vps (пересоздаваемый); gate зелёный на старте; ~10ч автономной работы (W1-W8) + ночь автономной работы для meta-волн W9-W12 (параллельные прогоны, симуляции, многочасовые multi-run тесты).
$END_ARTIFACT_CONTRACT

$START_DOCUMENT_PLAN
### Document Plan
**SECTION_GOALS:**
- GOAL [Определить первопричины и стратегию W1-W8] => G1 (§2, §5)
- GOAL [Задать целевую архитектуру автоматизации W1-W8] => G2 (§3, §4)
- GOAL [Разбить работу W1-W8 на исполняемые волны с AC] => G3 (§6)
- GOAL [Задать приоритет W1-W8 на 10 часов и порядок] => G4 (§7)
- GOAL [Зафиксировать манифест файлов и риски W1-W8] => G5 (§8, §9)
- GOAL [Meta: развернуть суперпозицию для долгоиграющих дефектов W9-W12] => G6 (§11)
- GOAL [Meta: разбить W9-W12 на исполняемые волны с AC] => G7 (§12)
- GOAL [Meta: зафиксировать риски и порядок запуска meta-волн ночью] => G8 (§9 meta, §13)
**SECTION_USE_CASES:**
- USE_CASE [Code-субагент исполняет волну W1..W8] => SC1 (промт-шаблон §6.0)
- USE_CASE [Code-субагент исполняет meta-волну W9..W12 автономно ночью] => SC1-meta (промт-шаблон §12.0)
- USE_CASE [Оператор пересоздаёт тестовую ноду] => SC2 (§6.6)
- USE_CASE [QA верифицирует план целиком включая meta] => SC3 (§7, §12 AC)
$END_DOCUMENT_PLAN

## 1. Стратегия

**Два потока, двенадцать волн, инкрементальный релиз.** W1-W8 — исходный план закрытия класса «свежий бутстрап открывает баги». W9-W12 — мета-расширение: долгоиграющие латентные дефекты, выявленные углублённым аудитом 6 субагентов (78 находок → 30 уникальных после дедупликации), требующие длинной проверки.

```
поток REPO (код+тесты)                поток SERVER (нода, параллельно)
─────────────────────────────────     ─────────────────────────────────
W1  регресс-тесты D1-D23         ─┐
W2  латентные классы             ─┤   SC2: оператор пересоздаёт ноду
W3  MaxStartups в код            ─┼──► (ожидание 30-60 мин/прогон)
W4  dev-hosts                    ─┤   W6: полный test-node цикл
W5  e2e-verify                   ─┤   e2e-verify на ноде
W6  харнесс-маркеры/fix         ─┘   повторный bootstrap no-op
W7  долги/доки/QA-134
─── meta-расширение (ночь, автономно) ───
W9  concurrency & state races    ─┐   параллельные деплои на test-VPS
W10 security posture deep       ─┤   port-scan, sudoers forensics
W11 CI false-negatives          ─┤   CI dry-run с красным full-gate
W12 test-infra honesty + DR     ─┘   multi-run flaky detection, AGE DR
W8  финальный gate + VerificationReport (оба потока, meta-результаты включены)
```

**Принцип разделения W1-W8 и W9-W12:** W1-W8 — закрытие известных дефектов (детерминированные тесты, точечные фиксы). W9-W12 — долгоиграющие (гонки, DR, security-эскалации, CI timing): требуют либо множественных прогонов на ноде, либо симуляций, либо аудита истории. Запускать W9-W12 автономно ночью — после того как W1-W8 влит (или параллельно с W6 server-циклом, если нода свободна).

Правила исполнения (инварианты AGENTS.md):
1. Все операции через `make <target>`; каждый шаг платформы после фикса перезапускается и подтверждается идемпотентно.
2. Коммиты ≤2 на волну: `docs(136)` / `feat(136)`.
3. Проверки: пер-задачные `make test-summary TEST_FILE=...`, фикс-цикл `make check` до чистоты, полный `make gate MODE=fast` — в конце каждой волны (fast-скоуп, не после каждой мелочи).
4. Запрещён `git checkout/restore` для отката одиночных файлов.
5. Новые таргеты (`e2e-verify`, `dev-hosts`) — через manifest-контракт: entrypoint-manifest.yaml + глоссарий AGENTS.md (генерируется) + canon_table core/AGENTS.md.
6. Тесты по testing-правилам: native imports, tmp_path, никаких hardcoded paths, LDD IMP:9 в успешных сценариях, R1-R5 (без pass-тестов, negative-тесты для каждого бага — R5 anti-survivorship: тест должен использовать ТОЧНЫЙ вход, вызвавший баг).
7. Shell keep-файлы (install-acme.sh, 700 LOC) не юнит-тестируются — только интеграционные/файловые фикстуры или харнесс.

## 2. Целевая архитектура

### 2.1 Существующие компоненты (не трогаем, используем)

| Компонент | Путь | Роль |
|-----------|------|------|
| Харнесс test-node | tests/e2e/ + makefiles/ci.mk:106 | Полный цикл bootstrap на test-VPS (маркер requires_node) |
| dry-run бутстрапа | tests/integration/test_bootstrap_dry_run.py | 14 фаз INIT + 5 UPDATE без сервера |
| Тест-инфраструктура фаз | tests/unit/test_bootstrap_phases.py | Unit φ2-φ4 (AGE, provision, auth) |
| verify | makefiles/deploy.mk:192 + core/internal/verify/verify-domains.sh | HTTPS-проверка expose:true доменов (реюз TLS-логики) |
| vhost-рендерер | core/internal/scaffold/vhost_renderer.py | server_names источник для e2e-verify/dev-hosts |
| security_posture | core/internal/bootstrap/security_posture.py | S4 sshd-проверки (расширить MaxStartups) |
| sha-resolve | .github/actions/sha-resolve/action.yml | Единственный SoT ретрая 10×30s (D10) |

### 2.2 Новые компоненты

```
verify_sweep_py (core/internal/verify_sweep.py)
  ├─ collect_endpoints(node, mode=local|remote) -> list[Endpoint]
  │    └─ источники: vhost_renderer server_names + node.yaml projects + module.yaml
  ├─ check_http(ep)  -> (code, ok)        # expected_codes классификация by-design
  ├─ check_tls(ep)   -> (chain, san, expiry)  # openssl s_client, wildcard-матчинг
  └─ main() -> int   # 0/1/2, [IMP:9] на каждый вердикт, --json

dev_hosts_py (core/internal/dev_hosts.py)
  ├─ collect_hosts() -> set[str]          # server_names + SAN dev_certs
  ├─ block_diff(etc_hosts, hosts)         # маркер-блок BEGIN/END ai-platform
  ├─ apply(path, hosts)                   # атомарно (tmp+mv), sudo для /etc/hosts
  └─ main() -> int   # 0 ок / 1 diff (dry-run) / 2 config error; --print --apply --dry-run

security_posture_py (расширение)
  ├─ check_sshd()      # + MaxStartups 30:50:200 (S4)
  └─ apply_sshd_dropin()  # /etc/ssh/sshd_config.d/99-platform-maxstartups.conf, идемпотентно

tests/unit/test_verify_sweep.py, tests/unit/test_dev_hosts.py,
tests/unit/test_security_posture_maxstartups.py, + расширения существующих
```

### 2.3 Draft Code Graph (XML)

```xml
<graph>
  <entity name="verify_sweep_py" type="MODULE" keywords="e2e-verify sweep endpoints tls http"
          annotation="core/internal/verify_sweep.py — sweep-верификация всех endpoints ноды"/>
  <entity name="dev_hosts_py" type="MODULE" keywords="dev-hosts etc-hosts macos block"
          annotation="core/internal/dev_hosts.py — идемпотентное управление /etc/hosts"/>
  <entity name="security_posture_py_FUNC_check_sshd" type="FUNC" keywords="sshd maxstartups posture"
          annotation="добавить проверку/применение MaxStartups (drop-in, не правка основного файла)"
          crosslinks="docker_orchestrator_py deploy_modules_sh"/>
  <entity name="deploy_modules_sh" type="SHELL" keywords="provision networks volumes proxy-net fallback"
          annotation="исправить fallback provision: все сети+volumes, снять || true маскировку"/>
  <entity name="docker_orchestrator_py" type="MODULE" keywords="syspath bootstrap root"
          annotation="добавить self-bootstrap корня репо (канон config_renderer.py:44-45)"/>
  <entity name="s3_ssl_cache_py" type="MODULE" keywords="acme reloadcmd cron pythonpath"
          annotation="self-bootstrap корня для cron-контекста (renew-hook)"/>
  <entity name="test_bootstrap_dry_run_py" type="TEST" keywords="dry-run phases networks volumes"
          annotation="расширить: assert provision сетей/volumes в dry-run"/>
  <entity name="test_bootstrap_phases_py" type="TEST" keywords="phases age auth provision"
          annotation="расширить: empty-creds→auth (D5), AGE detect+persist (D15)"/>
  <entity name="test_nginx_acme_py" type="TEST" keywords="install-acme merge ecc idempotent"
          annotation="добавить: merge-fallback в существующий /opt/acme.sh (D4)"/>
  <entity name="test_hermes_images_py" type="TEST" keywords="l1 bare-tag l2 build"
          annotation="добавить: bare-tag после pull (D18)"/>
  <entity name="test_context_promoter_py" type="TEST" keywords="resolve-org case mixed"
          annotation="добавить: tronyx-lab vs TronyxLab (D9)"/>
  <entity name="test_orchestrator_cli_dispatch_py" type="TEST" keywords="verify dispatch args"
          annotation="добавить: split node/project (D17)"/>
  <entity name="test_docker_auth_py" type="TEST" keywords="ghcr chown config"
          annotation="добавить: chown после root-записи (D16)"/>
  <entity name="test_templates_py" type="TEST" keywords="vhost health proxy-pass set"
          annotation="добавить: /health location без URI, set в location (D19/D20)"/>
  <entity name="test_e2e_conftest_py" type="TEST" keywords="requires-node chaos marker"
          annotation="маркер-фикс B4: excludes chaos из make test-node"/>
  <entity name="ci_secrets_rotation_md" type="DOC" keywords="runbook rotation ci keys"
          annotation="docs/ci-secrets-rotation.md — vps_ci_root/CI_DEPLOY_KEY/MIRROR_SSH_KEY"/>
</graph>
```

## 3. Data Flow — целевой сценарий «голый сервер за одну команду»

```
[оператор] make test-node NODE=test-e2e
   │ pre-flight (W6): нода пересоздана оператором SC2 (инвариант 9)
   ▼
[tests/e2e conftest] node_ssh → test_vps_fresh (rm state.json) → suite requires_node (без chaos)
   ▼
T6  cold-start bootstrap (9 INIT фаз) ──► assert: 20+ контейнеров, сети/volumes из манифеста
T7  node-update (5 UPDATE фаз) ──► assert: AGE ключ персистится (D15)
T8  converge идемпотентный ──► assert: no-op
T9  deploy test-project receive ──► assert: payload tar принят (D11)
T10 healthcheck docker inspect ──► assert: healthy
T11 backup snapshot / T12 restore round-trip
T13 idempotent rebootstrap ──► assert: 9 фаз skip
T15/T16 failure-сценарии (ssh timeout 124, forced-command receive)
   ▼
[оператор/агент] make e2e-verify NODE=test-e2e        (W5)
   ▼
collect_endpoints(server_names + projects) → check_http/check_tls
   ▼
таблица endpoint→HTTP→TLS→вердикт, exit 0 → приёмка «всё 200 + зелёные» СКРИПТОМ
   ▼
[CI] make deploy / context-promote (канал, вне харнесса — B6 задокументирован)
   ▼
[локально] make dev-hosts APPLY=1 → /etc/hosts актуален (W4)
```

Критерий успеха: следующая сессия типа 135 не содержит ни одного diagnose→fix→rerun цикла по платформенному коду — всё закрыто тестами W1/W2 или харнессом W6.

## 4. Суперпозиция — развёрнутая

### 4.1 Стратегический выбор (Mode 1: FULL)

## SUPERPOSITION: как закрыть класс «каждый бутстрап открывает баги»?
### Option A: Test-first [score: 6/10]
Approach: только регрессионные тесты D1-D23 (W1), без харнесса и новых таргетов.
Trade-offs: закрывает прошлое, НЕ ловит будущие баги (новый код снова не проверен на голой ноде); 10ч уйдут на тесты без изменения процесса.
Best when: харнесс невозможно запускать (нет ноды).

### Option B: Harness-first [score: 7/10]
Approach: только канонизация test-node + e2e-verify, без регресс-тестов D.
Trade-offs: закрывает будущее (каждый бутстрап = автотест), НО старые фиксы D4-D18 остаются непроверенными — следующий «геройский» прогон может их сломать незаметно; e2e-verify без expected_codes по endpoint'ам даст ложные FAIL.
Best when: прошлые фиксы уже имеют покрытие.

### Option C: Static-gate-first [score: 5/10]
Approach: гейты на паттерны (sys.path bootstrap, idempotency-маркеры, «provision elsewhere» grep-гейт).
Trade-offs: дёшево (~2ч), ловит классы A/C/F статически, НО не верифицирует поведение (D4/D5/D7 поведенческие); риск ложно-позитивных блокировок.
Best when: как дополнение к A/B, не как основа.

### Option D: Гибрид волнами (рекомендация) [score: 9/10]
Approach: W1 регресс-тесты → W2 фиксы латентных → W3 MaxStartups → W5/W4 автоматизация (e2e-verify/dev-hosts) → W6 харнесс-прогон на пересозданной ноде → W7 долги/QA-134.
Trade-offs: максимальная полнота при бюджете 10ч; риск — объём, но волны независимы и мержатся инкрементально; харнесс-прогон (W6) идёт параллельно repo-волнам.
Best when: 10ч автономной работы, нода доступна, релиз в конце недели.
### Recommendation: Option D — единственный вариант, закрывающий R1-R8 одновременно.
**Collapse signal:** если нода недоступна — свернуть к Option A+W7 (тесты + долги) и перенести W6.

### 4.2 Углы специалистов (трудности и решения)

| Специалист | Взгляд на план | Трудности | Решение в плане |
|---|---|---|---|
| Платформенный архитектор | Классы A-G — это симптомы отсутствия инвариантов в bootstrap-коде | self-bootstrap корня не стандартизирован (5 скриптов не проверены); shell keep-файлы (install-acme 700 LOC) не тестируемы юнитами | W2: канон self-bootstrap + верификация 5 скриптов; D4 тест — файловой фикстурой; канонизация в AGENTS.md |
| SRE / DevOps | Скрипт-первый принцип: бутстрап должен быть исполняемым артефактом, а не историей агента | Инвариант 9 нарушен (135 бутстрапил поверх старого); test_vps_fresh не пересоздаёт ноду (B3); chaos-тесты падают на голой ноде (B4); прогон 30-60 мин | W6: маркер-фикс, pre-flight «голоты» (docker absent assert), SC2-процедура пересоздания, README; W3: MaxStartups в код |
| QA | R5 anti-survivorship: каждый D-фикс требует negative-теста на точный вход | 6+ фиксов без тестов; dry-run не покрывает детали; гейты R1-R3 могут блокировать новые тесты с bare pass | W1: negative-тесты (merge в существующий dir, empty creds, mixed-case org, root-owned stub); LDD IMP:9 обязателен; матрица покрытия в VerificationReport 136 |
| Security | Ручные конфиги = дрейф конфигурации; CI-ключи без runbook = знание в голове | MaxStartups только на ноде; CI_DEPLOY_KEY repo-level ×N проектов (D12); VPS_SSH_KEY ротация непрозрачна; MIRROR_SSH_KEY | W3: drop-in в security_posture.py; W7: docs/ci-secrets-rotation.md с процедурами и чек-листами; grep-гейт имён секретов |
| CI/автоматизация | Цепочка gate→core-deploy/mirror/build-platform хрупка | mirror.yml post-push verify без ретрая (ложный FAIL); sha-resolve retry — единственный SoT (OK); org-паритет воркфлоу не проверяется | W7: ретрай-фикс mirror (10×10s); паритет-гейт org (воркфлоу-набор source vs context) |
| Продакт/владелец | Критерий «всё 200 + зелёные» должен быть командой, а не таблицей в отчёте | Sweep ad-hoc; verify покрывает 3 домена; таблицы вручную | W5: e2e-verify exit-контракт + --json; таблица = вывод команды |
| Майнтейнер процесса | «Ничьи зоны» порождают потери | 134 без VerificationReport; T9-T11/hermes без Debt; B12 chaos пишет в .ai/plans/126 | W7: ретро-QA 134; Debt-записи с Rev; chaos-артефакты в /tmp/chaos-<date> |

## 5. Волны

### 5.0 Промт-шаблон Code-субагента (SC1)

```
Прочитай .ai/plans/136-bootstrap-hardening/02-DevPlan.md, секцию W<N>.
Реализуй ВСЕ задачи волны W<N> по DevPlan.
Правила: (1) make fix-gate && git add -u перед коммитом; (2) пер-задачные проверки
make test-summary TEST_FILE=<файл> (или pytest <файл> -q); (3) фикс-цикл make check
до чистоты; (4) make gate MODE=fast в конце волны; (5) коммит ≤2: docs(136)/feat(136);
(6) тесты: native imports, tmp_path, LDD IMP:9, R5 negative-тест на точный вход бага;
(7) НЕ трогать чужие волны; (8) запрещён git checkout/restore для одиночных файлов.
Верни: список файлов, тесты с результатами, подтверждение AC волны.
```

### 5.1 W1 — Регрессионные тесты свежего бутстрапа (поток REPO, ~3.5ч)

Цель: каждый платформенный дефект D1-D23 имеет регресс-тест (или явное «ops/env»-обоснование в матрице). Приоритет HIGH: класс «свежий бутстрап».

| Задача | Дефект | Файл теста | Подход (точный вход бага = R5) |
|--------|--------|-----------|--------------------------------|
| T1.1 | D4 | tests/test_nginx_acme.py | Файловая фикстура tmp_path: существующий /opt/acme.sh с cert-каталогами *_ecc → повторный запуск install-acme (mock git) → merge-fallback, *_ecc сохранены, exit 0 |
| T1.2 | D5 | tests/unit/test_bootstrap_phases.py | φ3 с ПУСТЫМИ кредами (тот вход, что вызывал баг) → docker_registry_auth всё равно запускается (mock subprocess, assert вызова) |
| T1.3 | D7 | tests/integration/test_bootstrap_dry_run.py | Расширить dry-run: assert provision создаёт ВСЕ сети (не только proxy-net) и volumes из манифеста; отрицательный кейс — отсутствие фазы provision → FAIL |
| T1.4 | D15 | tests/unit/test_age_key.py | mock os.path.isfile: /etc/age/key.txt отсутствует → detect-цепочка генерирует; присутствует → no-op; assert персист (D15) |
| T1.5 | D18 | tests/unit/test_hermes_images.py | mock pull/tag: после GHCR pull L1 bare-tag `hermes-agent-base:latest` создаётся (D18); без bare-tag → L2 build FAIL |
| T1.6 | D9 | tests/unit/test_context_promoter.py | overlay context.yaml `org: tronyx-lab` (mixed-case вход) → _resolve_org даёт каноническое имя (D9) |
| T1.7 | D16 | tests/unit/test_docker_auth.py | mock chown: config.json после root-процесса → владелец целевого пользователя (D16) |
| T1.8 | D17 | tests/unit/test_orchestrator_cli_dispatch.py | dispatch args `verify <node> <project>` (тот вход, что сливался) → split корректный (D17) |
| T1.9 | D19/D20 | tests/test_templates.py + test_vhost_renderer.py | Рендер vhost с /health: нет `proxy_pass $var/URI`, `set $upstream` В location (D19/D20); negative: старый паттерн → тест падает |
| T1.10 | D1/D2/D3 | tests/unit/test_deploy_mk_chain.py (или существующие) | up-safe с пустым MODULES: COMPOSE_PROFILES из .env пробрасывается (D2); render-monitoring без PYTHONPATH (mock env) → self-bootstrap работает (D3) |
| T1.11 | D11 | tests/unit/test_receive_flow.py | Существующий root-owned стуб docker-compose.yml (readonly-файл) → receive os.remove + copy2 успешен (D11) |
| T1.12 | Матрица | — | Заполнить docs-блок матрицы покрытия D1-D23: «тест | ops/env | причинно» — для VerificationReport 136 |

AC W1: все тесты зелёные; R5-негативные кейсы на точный вход бага присутствуют; LDD IMP:9 в успешных сценариях; `make check` чистый; gate fast ALL PASS.

### 5.2 W2 — Латентные классы A/C/F (поток REPO, ~3.5ч)

Цель: закрыть подтверждённые разведкой кандидаты; дочитать классы B/D/E/G и закрыть подтверждённое.

| Задача | Класс | Файл:строка | Фикс | Тест |
|--------|-------|-------------|------|------|
| T2.1 | A | core/internal/bootstrap/deploy/docker_orchestrator.py:76-77 | Self-bootstrap корня репо (канон config_renderer.py:44-45) | subprocess с пустым env без PYTHONPATH из cwd≠root → exit 0 |
| T2.2 | A | core/internal/bootstrap/issue-cert.sh:237,302,361 | reloadcmd/renew-hook: PYTHONPATH или self-bootstrap в s3_ssl_cache.py (cron-контекст acme.sh) | env -i python3 s3_ssl_cache.py → exit 0; assert self-bootstrap в файле |
| T2.3 | C | core/internal/bootstrap/deploy-modules.sh:29-36 | Fallback provision: ВСЕ сети/volumes из манифеста (не только proxy-net) | Mock provision exit 1 → фасад логирует FAIL и НЕ продолжает молча; assert сетей |
| T2.4 | F | core/internal/bootstrap/deploy-modules.sh:31-32 | Снять `\|\| true` с provision: провал → видимый FAIL (IMP:9), не маскировка | Unit: mock exit 1 → фасад не идёт дальше |
| T2.5 | C | deploy-modules.sh:40-41 (llm-keys TRAP[CROSS-LAYER]) | Проверить, что state_machine post-deploy lifecycle реально провижинит llm-keys; если нет — связать | Интеграция/гейт: «provision elsewhere» упоминания ↔ парный вызов в state_machine |
| T2.6 | A | monitoring/*.py fallback (7 файлов) | Согласовать: fallback добавляет корень (или документированный канон) | Standalone-инвокация каждого модуля из чистого env → exit 0 |
| T2.7 | E | lifecycle/cli.py + state_machine.py + state_store.py | Дочитать D8-класс: raw-dict записи, resume без setup_state | Расширить test_state_machine.py resume-кейсами (missing phase) |
| T2.8 | B | install-*.sh (install-tor-proxy.py, setup-node.sh, firewall.sh) | Дочитать идемпотентность (clone/cp в существующие dir) | Где подтверждено — фикс + файловый тест |
| T2.9 | G | scaffold/vhost_renderer.py + шаблоны | Дочитать другие `proxy_pass $var+URI` / location-scope переменные | Где подтверждено — фикс + рендер-тест |
| T2.10 | A | compose_preflight.py, key_provisioner.py, dead_code_checker.py, context_deployer.py | Верифицировать self-bootstrap; где нет — добавить | Тесты инвокации из чистого env |

Правило: фиксить ТОЛЬКО подтверждённые кодом кандидаты (Fail-Fast, никаких «на всякий случай»). Неподтверждённое — записать в Debt-приложение VerificationReport.
AC W2: все HIGH-кандидаты разведки закрыты; классы B/D/E/G дочитаны, подтверждённое закрыто; тесты зелёные; check чистый; gate fast ALL PASS.

### 5.3 W3 — MaxStartups в код бутстрапа (поток REPO, ~1ч)

| Задача | Файл | Действие |
|--------|------|----------|
| T3.1 | core/internal/bootstrap/security_posture.py | check_sshd(): добавить проверку MaxStartups ≥ 30:50:200 (drop-in) |
| T3.2 | core/internal/bootstrap/security_posture.py | apply: `/etc/ssh/sshd_config.d/99-platform-maxstartups.conf` (drop-in, НЕ правка основного файла), идемпотентно, + reload sshd |
| T3.3 | tests/unit/test_security_posture_maxstartups.py | mock fs: drop-in создаётся при отсутствии; no-op при совпадении; содержимое корректно |

AC W3: свежий бутстрап воспроизводит MaxStartups (проверяется в W6 на пересозданной ноде); unit-тесты зелёные.

### 5.4 W4 — make dev-hosts (поток REPO, ~2ч)

| Задача | Файл | Действие |
|--------|------|----------|
| T4.1 | core/internal/dev_hosts.py | По дизайну (исследование 3): collect_hosts (server_names + SAN dev_certs), маркер-блок BEGIN/END ai-platform dev-hosts, --dry-run (exit 1 при diff), --print, --apply (sudo, атомарно tmp+mv) |
| T4.2 | makefiles/dev.mk | Таргет `dev-hosts` (default --dry-run; `APPLY=1` → --apply) |
| T4.3 | core/entrypoint-manifest.yaml + AGENTS.md glossary + core/AGENTS.md canon_table | Регенерация манифестов (make generate-entrypoint-manifest / generate-agents-md) |
| T4.4 | tests/unit/test_dev_hosts.py | tmp_path вместо /etc/hosts; diff/merge; идемпотентность; mock sudo |

AC W4: `make dev-hosts` на macOS выдаёт diff/применяет; повторный --apply no-op; manifest/glossary актуальны (check-manifests зелёный).

### 5.5 W5 — make e2e-verify (поток REPO, ~4ч)

| Задача | Файл | Действие |
|--------|------|----------|
| T5.1 | core/internal/verify_sweep.py | По дизайну (исследование 2): collect_endpoints (local: vhost_renderer server_names + node.yaml projects; remote: ssh nginx conf.d), check_http (expected_codes классификация: 200 OK, 301/302 by-design, 401/403 auth, 404/444 deny, 502/504 FAIL), check_tls (openssl chain, wildcard SAN, expiry: WARN<14д, FAIL при истечении), exit 0/1/2, --json, [IMP:9] |
| T5.2 | makefiles/ci.mk | Таргет `e2e-verify` (NODE обязателен, R4-семантика) |
| T5.3 | core/check-suite.yaml | Запись id: e2e-verify (diagnostic, БЕЗ gate_modes — требует живую ноду) |
| T5.4 | entrypoint-manifest + глоссарии | Регенерация манифестов |
| T5.5 | tests/unit/test_verify_sweep.py | Парсер списка (mock vhost_renderer/node.yaml), классификация кодов (включая by-design), wildcard SAN-матчинг, expiry-порог; R5-negative |
| T5.6 | — | Реюз TLS-логики verify-domains.sh (или явное DRY-обоснование) |

AC W5: `make e2e-verify NODE=tronyx-vps` (в W6 на ноде) выдаёт таблицу и exit 0 при всех OK; unit-тесты зелёные; check-manifests зелёный.

### 5.6 W6 — Канонизация харнесса test-node + прогон (потоки REPO+SERVER, ~3ч repo + ожидание server)

| Задача | Файл | Действие |
|--------|------|----------|
| T6.1 (repo) | tests/e2e/test_chaos_resilience.py + makefiles/ci.mk:117 | Маркер-фикс B4: `make test-node` = `-m "requires_node and not chaos"` (или chaos-отдельный таргет test-node-chaos); README состав обновить (B11) |
| T6.2 (repo) | tests/e2e/conftest.py | Pre-flight «голоты» (B3+): до suite — assert на ноде НЕТ docker/platform (иначе FAIL с понятным сообщением); test_vps_fresh: задокументировать, что пересоздание — операторская процедура SC2, не автосброс |
| T6.3 (repo) | tests/e2e/README.md | Обновить: состав suite, маркеры, длительность, B6/B7 ограничения (CI-путь, реальные ACME — вне харнесса) |
| T6.4 (server, SC2) | — | Оператор пересоздаёт VPS (инвариант 9); агент фиксирует: fresh Ubuntu, docker absent |
| T6.5 (server) | — | `make bootstrap-node NODE=test-e2e` (первый, канонический) → фиксы W3 проверены (MaxStartups drop-in на месте) |
| T6.6 (server) | — | `make test-node NODE=test-e2e` — полный цикл (без chaos); зафиксировать результаты |
| T6.7 (server) | — | `make e2e-verify NODE=test-e2e` (W5) — таблица, exit 0; повторный bootstrap = no-op |

AC W6: полный цикл на пересозданной ноде зелёный; MaxStartups воспроизведён бутстрапом; таблица e2e-verify получена; результаты в VerificationReport 136.

### 5.7 W7 — Долги, QA-134, runbook, CI-фиксы (поток REPO, ~2ч)

| Задача | Файл | Действие |
|--------|------|----------|
| T7.1 | .ai/plans/134-security-hardening/03-VerificationReport.md | Ретро-QA 134 (QA-субагент по коммитам 0e125c5/cff4b4b/3e459f5): AC из 01-DevPlan.md, gate fast, LDD |
| T7.2 | .ai/plans/136-bootstrap-hardening/04-Debt.md | Записи с Rev: T9-T11 (пересозданная нода, отдельное окно), hermes root-500 (upstream/патч L2), D-3..D-8 из 126 (актуальные статусы), B6/B7 ограничения харнесса |
| T7.3 | docs/ci-secrets-rotation.md | Runbook по дизайну (исследование 5): матрица ключей (vps_ci_root/VPS_SSH_KEY, platform_personal_cicd/CI_DEPLOY_KEY repo-level ×N, MIRROR_SSH_KEY, GITHUB_TOKEN, TELEGRAM_*, AGE), процедуры ротации с чек-листами, откат N дней |
| T7.4 | .github/workflows/mirror.yml:204-213 | Ретрай post-push verify (10×10s) — закрыть гонку eventual-consistency |
| T7.5 | node-configs/tronyx-vps/overlays/nginx/ | Синхронизировать репо-версии с актуальным vhost_renderer.py выводом (D19/D20 фиксы) — убрать дрейф имён (tronyx.ru.conf vs www.tronyx.ru.conf) |
| T7.6 | core/ (server-state.json mirror-запись) | Исправить/удалить ложную запись о втором mirror (dockerhub.timeweb.cloud) |
| T7.7 | docs/ | Grep-гейт имён секретов: какие секреты не упоминаются в документации (GHCR_OWNER, GIT_MIRROR_TOKEN, TELEGRAM_*) — добавить в runbook |

AC W7: VerificationReport 134 оформлен (QA-вердикт); 04-Debt.md содержит T9-T11/hermes с Rev; runbook создан; mirror-ретрай в коде; overlay синхронизирован; gate зелёный.

### 5.8 W8 — Финальная верификация (оба потока, ~2ч)

| Задача | Действие |
|--------|----------|
| T8.1 | `make check` до чистоты; `make gate MODE=fast` ALL PASS |
| T8.2 | Матрица покрытия D1-D23 → в VerificationReport 136 |
| T8.3 | .ai/plans/136-bootstrap-hardening/03-VerificationReport.md (QA-субагент): AC по волнам W1-W7, LDD, R1-R5 |
| T8.4 | Итоговый статус: результаты test-node/e2e-verify с ноды (из W6) в отчёт |

AC W8: gate ALL PASS; VerificationReport 136 с вердиктом SUCCESS/PARTIAL; все 8 глобальных AC закрыты или явно задокументированы.

## 6. Приоритет на 10 часов (порядок запуска)

```
0ч   W1 (repo, Code-субагент)          ─┐ параллельно:
     W3 (repo, Code-субагент, ~1ч)      ─┤ SC2 запрос оператору на пересоздание ноды
2-3ч W2 (repo, Code-субагент)           ─┤ (пересоздание ~15-30 мин)
3-4ч W4+W5 (repo, Code-субагенты)       ─┤ после SC2: W6.5-W6.7 (server, агент)
5-6ч W6.1-W6.3 (repo)                   ─┘ (бутстрап 30-45 мин + test-node ~1ч)
7ч   W7 (repo)                          ─┐
8-9ч W8 (repo+server, QA-субагент)      ─┘
10ч  Итог: VerificationReport 136
```

Срезы при нехватке времени (строгий порядок):
1. Обязательно: W1 (тесты), W3 (MaxStartups), W6.1-W6.3 (маркеры/README), W7.2 (Debt) — без этого класс не закрывается.
2. Желательно: W5 (e2e-verify) — главная автоматизация приёмки; W2 (латентные) — по подтверждённым HIGH.
3. Откладываемо: W4 (dev-hosts — боль, но не блокер), W7.4-W7.7 (CI-мелочи), W7.1 (ретро-QA 134).

## 7. Глобальные Acceptance Criteria (итог)

| # | Критерий | Волна |
|---|----------|-------|
| G-AC1 | Матрица D1-D23: каждый дефект — регресс-тест или явное ops/env-обоснование | W1, W8 |
| G-AC2 | Все HIGH-кандидаты латентных классов закрыты фиксами с тестами; классы B/D/E/G дочитаны | W2 |
| G-AC3 | MaxStartups воспроизводится свежим бутстрапом (drop-in) — подтверждено на пересозданной ноде | W3, W6 |
| G-AC4 | `make test-node` на пересозданной ноде зелёный (без chaos); pre-flight «голоты» работает | W6 |
| G-AC5 | `make e2e-verify NODE=<n>`: таблица + exit 0; манифест/глоссарий актуальны | W5, W6 |
| G-AC6 | `make dev-hosts` идемпотентен (dry-run/print/apply) | W4 |
| G-AC7 | VerificationReport 134; 04-Debt.md (T9-T11, hermes, D-3..D-8) с Rev; docs/ci-secrets-rotation.md | W7 |
| G-AC8 | gate ALL PASS; VerificationReport 136 с вердиктом | W8 |
| **G-AC9 (meta)** | **Concurrent-deploy lock реализован (flock на deploy-{project}.lock и state.json); регрессионный тест на параллельные deploys/state-writers — зелёный; L-1/L-2/B-2/B-3/B-12 закрыты** | **W9** |
| **G-AC10 (meta)** | **PRIVESC-цепочки S-1/S-2/S-3: либо закрыты сужением sudoers (gate-тест на шаблон генератора), либо оформлены TRAP[DECISION] с осознанным risk-acceptance и Rev-датой; S-4/S-5/S-6/S-7 либо расширены в коде, либо Debt** | **W10** |
| **G-AC11 (meta)** | **CI false-negative C-1 (деплой по fast-gate вместо full) и C-2 (integration continue-on-error) закрыты gate-тестом или явным TRAP; кэш L1 (C-5) верифицирован чтением action.yml** | **W11** |
| **G-AC12 (meta)** | **Test-infra honesty (T-1/T-2 counter, T-3/T-4 wave xdist) либо фиксы с тестами, либо Debt; DR-стратегия AGE мастер-ключа (S-12) задокументирована в docs/age-master-key-dr.md** | **W12** |

## 8. File Manifest

**Создаются (W1-W8):**
- core/internal/verify_sweep.py, core/internal/dev_hosts.py
- tests/unit/test_verify_sweep.py, tests/unit/test_dev_hosts.py, tests/unit/test_security_posture_maxstartups.py
- docs/ci-secrets-rotation.md
- .ai/plans/136-bootstrap-hardening/03-VerificationReport.md, 04-Debt.md
- .ai/plans/134-security-hardening/03-VerificationReport.md

**Создаются (W9-W12 meta):**
- core/internal/bootstrap/lifecycle/lock.py (flock helper для state.json + deploy locks)
- core/internal/bootstrap/idempotency.py (content-hash invalidation для update-фаз)
- tests/unit/test_deploy_concurrent_lock.py (2-нити deploy → сериализация)
- tests/unit/test_state_store_concurrent_writers.py (2-нити save_state → consistency)
- tests/unit/test_idempotency_hash.py (config change → phase re-run)
- tests/unit/test_audit_failure_paths.py (exception → audit entry FAILED)
- tests/unit/test_receive_flow_atomicity.py (rollback восстанавливает payload-файлы)
- tests/unit/test_channels_injection.py (project_name `;`/`../` инъекция)
- tests/gates/test_gate_sudoers_hardening.py (шаблон sudoers без docker run/exec/rsync -e)
- tests/gates/test_gate_healthcheck_drift.py (контракты модулей vs канон D5)
- tests/integration/test_multi_bootstrap_idempotency.py (3× bootstrap на test-VPS, идемпотентность)
- tests/integration/test_flaky_detection.py (5× прогонов под нагрузкой, фиксация flaky)
- docs/age-master-key-dr.md (DR: хранение мастер-ключа, off-node backup, процедура восстановления)

**Модифицируются (W1-W8):**
- core/internal/bootstrap/security_posture.py, core/internal/bootstrap/deploy/docker_orchestrator.py, core/internal/bootstrap/issue-cert.sh, core/internal/bootstrap/deploy-modules.sh, core/internal/bootstrap/install-acme.sh (если подтвердится B-класс), core/internal/monitoring/*.py (7 fallback), core/internal/bootstrap/lifecycle/cli.py (если подтвердится E-класс), core/internal/scaffold/vhost_renderer.py (если подтвердится G-класс)
- makefiles/ci.mk (e2e-verify, test-node маркер), makefiles/dev.mk (dev-hosts)
- core/entrypoint-manifest.yaml, core/check-suite.yaml, AGENTS.md (глоссарий), core/AGENTS.md (canon_table) — через генераторы
- tests/: test_nginx_acme.py, test_bootstrap_phases.py, test_bootstrap_dry_run.py, test_age_key.py, test_hermes_images.py, test_context_promoter.py, test_docker_auth.py, test_orchestrator_cli_dispatch.py, test_templates.py, test_vhost_renderer.py, test_receive_flow.py, test_state_machine.py (+ новый test_deploy_mk_chain.py при необходимости)
- tests/e2e/conftest.py, tests/e2e/test_chaos_resilience.py, tests/e2e/README.md
- .github/workflows/mirror.yml
- node-configs/tronyx-vps/overlays/nginx/*.conf
- .ai/plans/135-end-to-end-platform/01-StatusReport.md (ссылка на 136)

**Модифицируются (W9-W12 meta):**
- core/internal/bootstrap/lifecycle/state_store.py (flock + unique tmp), state_machine.py (wire `_step_hash`), cli.py (audit в failure-путях, убрать `done=` kwarg, liveness probe на no-op bootstrap)
- core/internal/bootstrap/lifecycle/helpers/users.py (forced-command reconciliation), helpers/system.py (`/bin/bash -c "command -v sops"`, journald active-line check)
- core/internal/bootstrap/lifecycle/phases/docker.py (nginx overlay delete-drift, `.hc_done_in_deploy` per-context, φ11 scope append, deploy timeout)
- core/internal/bootstrap/setup-node.sh (sudoers сужение: NO docker compose run/exec, NO rsync; валидация NODE_NAME)
- core/internal/bootstrap/firewall.py (incremental вместо disable+reset; DOCKER-USER chain)
- core/internal/bootstrap/security_posture.py (S4 расширение sshd-директив; S6 расширение критичных путей; S7 exclusivity проверка; S3/S5 реальный LISTEN через ss + DOCKER-USER)
- core/internal/shared/audit_logger.py (fsync, fail-on-OSError, malformed-line alert)
- core/internal/deploy/orchestrator.py (flock вокруг deploy), channels.py (shlex.quote + pre-delivery validation), receive_flow.py (MAX_PAYLOAD_BYTES, atomic staging), deploy_history.py (tmp+rename snapshot), orchestrator_cli.py (validate_project_name в dispatch)
- core/modules/status-page/app.py (ThreadingHTTPServer, /healthz staleness 503)
- core/modules/{monitoring,logging,nginx,langfuse,hermes-agent}/healthcheck.sh (env-параметризация имён, корректные порты/codes, HTTP-проверка в deps-режиме)
- tests/_conftest/session.py, _conftest/smoke.py, _conftest/wave_pipeline.py, _conftest/e2e.py (унификация counter, scope env-фикстур, xdist-safe wave events)
- .github/workflows/platform-gate-fast.yml, platform-test.yml (cache key, integration outcome gate), core-deploy.yml (timeout, root vs forced-command TRAP), build-platform.yml (digest pinning, cache key hashFiles)
- .github/actions/docker-build-cache/action.yml (cache key + hashFiles context)
- .github/actions/sha-resolve/action.yml (bounded retry при run-not-found)
- core/AGENTS.md (canon_table: новые verify_sweep/dev_hosts/lock/idempotency модули)

## 9. Риски и открытые вопросы

| Риск | Митигация |
|------|-----------|
| Пересоздание ноды требует оператора (SC2) — узкое место | Запросить сразу в начале (W1 стартует параллельно); без ноды — срез «обязательно» (W1/W3/W6.1-3/W7.2) |
| e2e-verify ложные FAIL (auth/redirect/by-design) | expected_codes-классификация из манифеста; by-design 404/444/401/302 не FAIL; W6.7 калибровка на живой ноде |
| chaos-тесты остаются в test-node (B4) | Маркер-фикс T6.1 — обязателен до любого прогона на ноде |
| Shell keep-файлы не тестируемы юнитами | Файловые фикстуры (D4), харнесс (W6); задокументировать в матрице |
| ACME rate-limit / реальные LE-сертификаты вне харнесса (B7) | e2e-verify на tronyx-vps (домены с LE) частично закрывает; T9-T11 — Debt с Rev |
| Объём (8 волн) > 10ч | Срезы §6; волны независимы — инкрементальный мерж до релиза 2026-08-09 |
| Генерация манифестов (W4/W5) ломает check-manifests | Прогон make generate-* + check-manifests в каждой волне; коммиты ≤2 |
| mirror post-push retry (W7.4) без ретрая — ложный FAIL на CI | 10×10s по канону sha-resolve |
| Инвариант 9 (пересоздание) дорогой | Документировать SC2 как обязательный шаг Фазы B; pre-flight «голоты» T6.2 делает нарушение видимым |
| **(meta) Сужение sudoers ломает доставку core (rsync/docker)** | **T9.4: верификация на test-VPS что core_deliverer работает без sudo rsync; если нужен — точечный sudoers с конкретными флагами + gate-тест** |
| **(meta) flock на state.json дедлочит bootstrap при зависшем процессе** | **non-blocking flock (LOCK_EX|LOCK_NB) с таймаутом и явной ошибкой «state locked by PID X»; cleanup stale lock по PID liveness** |
| **(meta) content-hash invalidation перевыполняет фазы при каждом node.yaml edit** | **hash только по релевантным полям (modules, services), не весь файл; debounce через mtime** |
| **(meta) CI false-negative фиксы (C-1/C-2) ломают текущий flow релиза** | **Сначала TRAP[DECISION] с осознанным risk-acceptance, потом постепенное ужесточение; gate-тест как canary** |
| **(meta) AGE DR — off-node backup создаёт новую поверхность атаки (ключ в облаке)** | **age-key зашифрован sops/KMS перед выгрузкой; backup-процедура документирована с threat-model** |
| **(meta) Multi-run flaky detection (T-интегра) требует часы CPU** | **Запускать ночью автономно; результаты — в Debt (flaky → Debt с Rev) или fix (если детерминированный)** |
| **(meta) Concurrency-тесты сами флакают (гонки недетерминированы)** | **Повторять N раз (pytest-repeat или параметризация); assert на инвариант (consistency), не на детерминизм** |

## 10. Порядок релиза (конец недели)

1. Волны мержатся инкрементально: после каждой — gate fast зелёный (W1..W7 → W8).
2. W6-прогон на ноде — до релиза (главное доказательство G-AC2/3/4/5).
3. **Meta-волны W9-W12 запускаются автономно после влития W1-W8 (или параллельно с W6 server-циклом); каждая волна — отдельная ветка, gate fast зелёный после каждой; Debt-записи для отложенного.**
4. VerificationReport 136 — финальный артефакт; при PARTIAL-вердикте — Debt-приложение с Rev; meta-находки (W9-W12) включены в матрицу покрытия и Debt-реестр.

---

## 11. Суперпозиция-2 (meta-расширение) — развёрнутая

### 11.1 Источник: находки 6 углублённых аудит-субагентов

| Домен | Субагент | Находок | Уникальных после дедуп |
|-------|----------|---------|------------------------|
| bootstrap subsystem | explore B-* | 13 | 10 (B-1..B-13) |
| lifecycle / state / deploy | explore L-* | 12 | 11 (L-1..L-12) |
| CI / workflows / gates | explore C-* | 14 | 14 (C-1..C-14) |
| modules / templates | explore M-* | 7 | 7 (M-1..M-7) |
| security posture | explore S-* | 15 | 15 (S-1..S-15) |
| test infrastructure | explore T-* | 15 | 15 (T-1..T-15) |
| **Итого** | **6** | **76** | **~30 после кросс-домен дедуп** |

**Кросс-домен дубликаты (консолидированы):**
- state.json race: B-2 ≡ L-2 (state_store concurrent writers) → одна задача W9
- content-hash dead code: B-1 ≡ L-4 (`_step_hash` не вызывается) → одна задача W9
- `done=True` TypeError: B-6 ≡ L-3 → одна задача W9
- audit gaps: L-5 (bootstrap) ≡ L-11 (deploy) ≡ S-6 (tamperable) → W9 + W10
- concurrent deploy lock: L-1 (deploy) ≡ L-9 (retry double-deploy) ≡ L-12 (snapshot race) → W9
- receive injection: L-8 (SCPChannel) ≡ L-10 (dispatch validation) → W9
- sudoers PRIVESC: S-1/S-2/S-3 → W10
- SSH hardening: S-4/S-5/S-10 ≡ (MaxStartups из W3) → W10
- healthcheck drift: M-1..M-7 → W10 (cross-cutting)
- CI false-neg: C-1 (fast-gate deploys) ≡ C-2 (integration continue-on-error) → W11
- cache staleness: C-5/C-6/C-10/C-13 → W11
- test-infra honesty: T-1/T-2 (counter) ≡ T-3/T-4 (wave xdist) ≡ T-5/T-6 (env pollution) → W12
- DR / AGE: S-12/S-13 → W12

### 11.2 Стратегический выбор для meta-волн (Mode 1: FULL)

## SUPERPOSITION: как структурировать meta-расширение (W9-W12)?

### Option A: Fix-everything (все 30 находок — фиксы) [score: 4/10]
Approach: каждая находка → фикс + тест за ночь.
Trade-offs: нереалистично за ночь (часть требует мульти-прогонов на ноде, simulation, audit-forensics); риск «быстрых фиксов» без верификации ломает bootstrap; sudoers-сужение без тщательной верификации ломает доставку core.
Best when: бесконечный бюджет.

### Option B: Test-and-debt (все находки — регрессионные тесты или Debt) [score: 7/10]
Approach: для каждой находки — либо детерминированный регрессионный тест (если возможно без ноды), либо Debt-запись с Rev-датой и описанием verification-cost.
Trade-offs: безопасно, воспроизводимо, не ломает bootstrap; НО sudoers PRIVESC (S-1/S-2/S-3) нельзя просто Debt — это CRITICAL security, требует немедленного risk-decision (TRAP[DECISION] или фикс).
Best when: ночь автономно, нода может быть недоступна.

### Option C: Severity-gated hybrid (CRITICAL → фикс или TRAP; HIGH → тест; MEDIUM/LOW → Debt) [score: 9/10]
Approach:
- CRITICAL (S-1/S-2/S-3 sudoers, S-7 ufw bypass) → либо фикс + gate-тест, либо явный TRAP[DECISION] с risk-acceptance и Rev-датой (осознанный выбор, не игнорирование);
- HIGH concurrency/security/CI (L-1, L-2, L-4, L-5, L-6, L-8, S-4, S-6, S-12, C-1, C-2, C-5, T-1, T-2, T-3, T-4, M-1) → регрессионные тесты (deteministic) + фикс если тест поймал;
- MEDIUM/LOW → Debt с Rev-датой и verification-cost описанием.
Trade-offs: балансирует безопасность и реальность; CRITICAL не откладывается; HIGH ловится тестами автоматически при будущих изменениях; MEDIUM/LOW не теряются.
Best when: ночь автономной работы, нода доступна часть времени.

### Recommendation: Option C — единственный вариант, закрывающий R9-R14 без риска сломать платформу ночными «быстрыми фиксами».
**Collapse signal:** если нода недоступна всю ночь — свернуть CRITICAL к TRAP[DECISION] (risk-acceptance с Rev), HIGH к тестам, MEDIUM/LOW к Debt; W6 server-цикл переносится.

### 11.3 Углы специалистов для meta-волн (дополнительные к §4.2)

| Специалист | Взгляд на meta-план | Трудности | Решение в W9-W12 |
|---|---|---|---|
| Security engineer | sudoers PRIVESC — немедленный риск, но сужение ломает доставку | S-1/S-2/S-3: docker/rsync NOPASSWD = root escape; но core_deliverer зависит от этого | W10: верификация на test-VPS что доставка работает без sudo rsync; gate-тест на шаблон sudoers; TRAP[DECISION] если risk-acceptance |
| SRE / reliability | concurrency-гонки — невидимы до инцидента | L-1/L-2/L-9: нет lock на deploy/state; retry дублирует deploy | W9: flock + non-blocking + stale-lock cleanup; регрессионный тест на 2-нити |
| Platform architect | content-hash идемпотентность — мёртвый код, документация лжёт | L-4/B-1: `_step_hash` определён, не вызывается; node.yaml change не инвалидирует | W9: wire hash в execute_phase; тест config change → phase re-run |
| QA lead | test-infra сама по себе хрупкая и лживая | T-1/T-2: dual counter; T-3/T-4: wave xdist; T-5/T-6: env pollution | W12: унификация counter, xdist-safe events, scope env-фикстур; multi-run flaky detection harness |
| DR / business continuity | AGE мастер-ключ — единственная точка отказа | S-12: ключ только на ноде; нода умирает → secrets невосстановимы | W12: docs/age-master-key-dr.md; off-node encrypted backup; тест восстановления |
| CI engineer | CI «зелёный, система врёт» — known класс | C-1: fast-gate деплоит; C-2: integration continue-on-error | W11: gate-тест на соответствие trigger-gate и full-gate; integration outcome gate |

### 11.4 Приоритизация внутри meta-волн (verification-cost × severity)

| Находка | Severity | Verification cost | Действие |
|---------|----------|-------------------|----------|
| S-1/S-2/S-3 sudoers PRIVESC | CRITICAL | HIGH (test-VPS forensics) | W10: фикс ИЛИ TRAP[DECISION] |
| S-7 ufw Docker bypass | CRITICAL | HIGH (port-scan) | W10: фикс (S3/S5 реальный LISTEN) ИЛИ TRAP |
| L-1/L-2 concurrency (deploy/state) | HIGH | MEDIUM (2-нити unit) | W9: flock + регрессионный тест |
| L-4/B-1 content-hash dead | HIGH | LOW (unit, mock config) | W9: wire + тест |
| C-1 fast-gate deploys | HIGH | MEDIUM (CI analysis) | W11: gate-тест ИЛИ TRAP |
| C-2 integration continue-on-error | HIGH | LOW (1 PR) | W11: outcome gate |
| S-6 audit tamperable | HIGH | MEDIUM (forensics) | W10: fsync + fail-on-error |
| S-12 AGE DR | HIGH | HIGH (DR drill) | W12: docs + (если время) drill |
| L-5/L-11 audit failure paths | HIGH | LOW (unit, mock exception) | W9: finally + audit FAILED |
| L-8 receive injection | HIGH | MEDIUM (injection test) | W9: shlex.quote + pre-delivery validation |
| M-1 status-page blocking | HIGH | HIGH (load test) | W10: ThreadingHTTPServer |
| T-1/T-2 counter | HIGH | LOW (unit) | W12: unify |
| T-3/T-4 wave xdist | HIGH | MEDIUM (multi-run) | W12: xdist-safe ИЛИ Debt |
| C-5 L1 cache stale (гипотеза) | HIGH | HIGH (read action.yml + digest compare) | W11: верификация чтением, фикс если подтверждено |
| Остальные MEDIUM/LOW | MED/LOW | varies | Debt с Rev-датой |

---

## 12. Волны meta-расширения (W9-W12)

### 12.0 Промт-шаблон Code-субагента для meta-волн (SC1-meta)

```
Прочитай .ai/plans/136-bootstrap-hardening/02-DevPlan.md, секцию W<N> (meta).
Реализуй задачи волны W<N> по принципу Option C (severity-gated hybrid):
- CRITICAL → фикс ИЛИ TRAP[DECISION] (если фикс ломает платформу);
- HIGH → регрессионный тест (deteministic, native imports, tmp_path) + фикс если тест поймал;
- MEDIUM/LOW → Debt-запись в 04-Debt.md с Rev-датой и verification-cost.
Правила: (1) make fix-gate && git add -u; (2) make test-summary TEST_FILE=<файл>;
(3) make check до чистоты; (4) make gate MODE=fast в конце; (5) коммит ≤2;
(6) тесты: R5 negative на точный вход бага, LDD IMP:9; (7) НЕ трогать чужие волны;
(8) запрещён git checkout/restore одиночных файлов; (9) для concurrency-тестов —
pytest-repeat или параметризация (N прогонов), assert на инвариант (consistency);
(10) для sudoers/security — сначала верификация на test-VPS, потом фикс;
(11) если фикс ломает платформу → оформить TRAP[DECISION] с Rev-датой.
Верни: список файлов, тесты с результатами, TRAP-тексты (если применимо), Debt-записи.
```

### 12.1 W9 — Concurrency & state-machine races (поток REPO + SERVER, ~4ч)

Цель: закрыть HIGH concurrency-дефекты (L-1, L-2, L-4, L-5, L-8, L-9, L-11, L-12, B-1, B-2, B-3, B-4, B-6, B-7, B-8, B-10, B-12, B-13). Принцип: flock где нужно, content-hash wiring, audit в failure-путях, pre-delivery validation.

| Задача | Источник | Файл | Действие |
|--------|----------|------|----------|
| T9.1 | L-1, L-9, L-12 | core/internal/deploy/orchestrator.py + core/internal/bootstrap/lifecycle/lock.py (новый) | flock на `/var/lock/platform-deploy-{project}.lock` вокруг deploy(); non-blocking (LOCK_EX\|LOCK_NB) с понятной ошибкой «locked by PID X»; cleanup stale по PID liveness |
| T9.2 | L-2, B-2 | core/internal/bootstrap/lifecycle/state_store.py | save_state: unique tmp (tempfile.mkstemp) + flock на state.json; load_state возвращает явную ошибку при коррапте (не fresh state) |
| T9.3 | L-4, B-1 | core/internal/bootstrap/lifecycle/state_machine.py + cli.py | Wire `_step_hash` в execute_phase: сравнение hash входов (modules, services из node.yaml) с сохранённым; mismatch → re-run фазы; B-1: update-фазы также инвалидируются hash'ом |
| T9.4 | B-6, L-3 | core/internal/bootstrap/lifecycle/cli.py:495,527 | Убрать `done=True`/`done=False` kwarg из StepState (поля нет); тест на else-ветку маркировки |
| T9.5 | B-7 | core/internal/bootstrap/lifecycle/state_store.py:344-348 | migration: обернуть legacy root-key данные в `StepState.from_dict()`, не сырой dict |
| T9.6 | L-5, L-11 | core/internal/bootstrap/lifecycle/cli.py + core/internal/deploy/orchestrator.py | audit в finally/except: write_audit_log с result=FAILED при PlatformFatalError/PhaseDependencyError; DeployOrchestrator audit в except-ветках (rollback OSError, healthcheck exception) |
| T9.7 | L-8, L-10 | core/internal/deploy/channels.py + orchestrator_cli.py | shlex.quote(project_name) в SSH-командах; validate_project_name в `_prepare_deploy` (до deliver) и в `_dispatch` (до маршрутизации) |
| T9.8 | L-6 | core/internal/deploy/receive_flow.py + orchestrator.py rollback | Atomic staging: копировать в staging dir → rename в target; rollback восстанавливает payload-файлы из snapshot (не только compose) |
| T9.9 | L-7 | core/internal/deploy/receive_flow.py | MAX_PAYLOAD_BYTES (env-configurable, default 1GB); потоковое чтение; reject при превышении |
| T9.10 | L-12 | core/internal/deploy/deploy_history.py | create_snapshot: tmp+rename (атомарно); prune под тем же flock что T9.1 |
| T9.11 | B-3 | core/internal/bootstrap/lifecycle/state_machine.py | Wire `_should_retry` вокруг phase_func (RETRY_COUNT=2, backoff); ИЛИ удалить dead code и документировать fail-fast |
| T9.12 | B-4 | core/internal/bootstrap/lifecycle/helpers/system.py:103-108 | `/bin/bash -c "command -v sops"` (или shutil.which); тест на повторный φ1 без re-download |
| T9.13 | B-8 | core/internal/bootstrap/lifecycle/helpers/system.py:361-363 | journald idempotency guard: active-line check (не substring); тест на `#Storage=persistent` commented |
| T9.14 | B-10 | core/internal/bootstrap/lifecycle/phases/docker.py:269-293 | nginx overlay: hash всего содержимого dir (включая deletions); reload при change |
| T9.15 | B-12 | core/internal/bootstrap/lifecycle/phases/docker.py:127,409 | deploy-modules timeout: поднять до 600с (или per-module); resume-aware orphan cleanup |
| T9.16 | B-13 | core/internal/bootstrap/lifecycle/phases/docker.py:246 | φ11 provision: `action="append"` для --scope ИЛИ loop (как φ3); тест на сети+тома |
| T9.17 | B-9 | core/internal/bootstrap/lifecycle/cli.py:560-564 | no-op bootstrap: lightweight liveness probe (docker info, disk, port) даже когда фазы done; downgrade тяжёлых |
| T9.18 | B-5 | core/internal/bootstrap/lifecycle/helpers/users.py:82-98 | authorized_keys: parse существующей записи, сравнить command= prefix, reconcile если drift |
| T9.19 | B-11 | core/internal/bootstrap/lifecycle/phases/docker.py:354 | `.hc_done_in_deploy` marker: scope per-context (не node-global) |
| T9.20 | — | tests/unit/test_deploy_concurrent_lock.py, test_state_store_concurrent_writers.py, test_idempotency_hash.py, test_audit_failure_paths.py, test_receive_flow_atomicity.py, test_channels_injection.py | Регрессионные тесты на T9.1-T9.9 (R5 negative, LDD IMP:9, N прогонов для concurrency) |

AC W9: G-AC9 закрыт; все HIGH concurrency-дефекты имеют регрессионные тесты; flock реализован (deploy + state); content-hash wired; audit в failure-путях; sudoers не трогаются (это W10); gate fast ALL PASS; Debt для MEDIUM/LOW (B-14..B-15 если найдены).

### 12.2 W10 — Security posture deep (поток REPO + SERVER, ~5ч)

Цель: CRITICAL sudoers PRIVESC (S-1/S-2/S-3) — фикс ИЛИ TRAP[DECISION]; HIGH security (S-4/S-5/S-6/S-7/S-8/S-10/S-12) — фиксы с верификацией на test-VPS; MEDIUM/LOW — Debt. Healthcheck drift (M-1..M-7) — фиксы контрактов.

| Задача | Источник | Файл | Действие (severity-gated) |
|--------|----------|------|---------------------------|
| T10.1 | S-1, S-2, S-3 (CRITICAL) | core/internal/bootstrap/setup-node.sh:55-64,73 | **Верификация на test-VPS**: core_deliverer без `sudo rsync`? docker без `sudo docker compose run/exec`? Если да → сузить sudoers (gate-тест на шаблон). Если core_deliverer ломается → TRAP[DECISION] с risk-acceptance, Rev-дата, mitigation (audit, monitoring). Решение принимает оператор (в prompt: «получив верификацию, примени ОДИН из двух путей»). |
| T10.2 | S-7 (CRITICAL гипотеза) | core/internal/bootstrap/firewall.py + security_posture.py S3/S5 | Проверить compose publish-порты postgres/minio/clickhouse; если 0.0.0.0 → фикс: внутренние сервисы без publish (только internal network) ИЛИ DOCKER-USER chain; S3/S5 — реальный LISTEN через `ss -tlnp` + cross-check с compose |
| T10.3 | S-4 | security_posture.py:285-301 | S7: FAIL при любой строке authorized_keys БЕЗ канонического forced-command prefix + проверка perms 0600/owner |
| T10.4 | S-5 | security_posture.py:179-201 | S4: расширить на 8-10 sshd-директив (AllowUsers, ClientAliveInterval, PermitUserEnvironment, X11Forwarding, AllowTcpForwarding, KexAlgorithms, ciphers, MACs, LoginGraceTime, UsePAM) |
| T10.5 | S-6, S-15 | core/internal/shared/audit_logger.py | fsync после append; fail (exit≠0) при OSError; alert при malformed JSON в read; source-поле (UID/process) в схеме |
| T10.6 | S-8 | firewall.py:34-38,75-76 | extra_ports только с `from <ip>` (не 0.0.0.0); расширить FORBIDDEN/CHECK до реестра портов модулей (SoT platform-infra.yaml) |
| T10.7 | S-9 | setup-node.sh:35,43,100 | Валидация NODE_NAME regex `^[a-zA-Z0-9_-]+$` до генерации sudoers-фрагмента |
| T10.8 | S-10 | security_posture.py:249-272 | S6: расширить проверку world-writable на критичные пути (~ci-deploy/.ssh, /etc/sudoers.d, /var/log/platform, /etc/age) |
| T10.9 | S-11 | setup-node.sh:68 | Синхронизировать sudoers путь с audit.jsonl (не audit.log); ИЛИ убрать мёртвую запись; gate на соответствие |
| T10.10 | S-14 | firewall.py:70-71,135-153 | Incremental firewall (без disable+reset); ИЛИ enable default-deny перед модификациями; signature «firewall not active» в healthcheck |
| T10.11 | M-1 (HIGH) | core/modules/status-page/app.py:417 | ThreadingHTTPServer (или /healthz на fast-path); нагрузочный тест «медленный апстрим + /healthz опрос» |
| T10.12 | M-2, M-3, M-4, M-5, M-6 | core/modules/{monitoring,logging,nginx,langfuse,hermes-agent}/healthcheck.sh | env-параметризация имён контейнеров (паттерн infra-metrics); корректные порты/codes; HTTP-проверка в deps-режиме (hermes) |
| T10.13 | M-7 | status-page/app.py:329-340 | /healthz возвращает 503 при staleness > порога (синхронизировать с /health) |
| T10.14 | — | tests/gates/test_gate_sudoers_hardening.py (новый), test_gate_healthcheck_drift.py (новый) | Gate-тесты: sudoers-шаблон без опасных паттернов; healthcheck-контракты vs канон D5 |
| T10.15 | S-12, S-13 (HIGH, Debt-til-DR) | docs/age-master-key-dr.md (новый) | DR-стратегия: где хранится мастер-ключ, off-node encrypted backup, процедура восстановления; S-13: tmpfs для temp-ключа, sanitize sops stderr |

AC W10: G-AC10 закрыт; CRITICAL sudoers либо сужены (gate-тест зелёный) либо TRAP[DECISION] с Rev; SSH hardening расширен; audit tamper-resistant; healthcheck-контракты выровнены; DR-документ создан; gate fast ALL PASS; Debt для LOW (S-13 partial, S-15).

### 12.3 W11 — CI false-negatives & cache (поток REPO, ~3ч)

Цель: закрыть CI false-negative (C-1, C-2, C-5, C-6, C-10, C-13); верифицировать cache-staleness гипотезы чтением action.yml; gate-тесты как canary.

| Задача | Источник | Файл | Действие |
|--------|----------|------|----------|
| T11.1 | C-1 (HIGH) | .github/workflows/{core-deploy,mirror,build-platform}.yml + tests/gates/test_gate_ci_trigger_strength.py (новый) | Вариант A: gate-тест проверяет, что workflow_run-trigger — самый сильный gate (full, не fast). Вариант B: TRAP[DECISION] «deploys on fast-gate by design, full-gate is PR-only» с Rev. Решение — после верификации текущего branch protection. |
| T11.2 | C-2 (HIGH) | .github/workflows/platform-test.yml:274-301 | Убрать `continue-on-error` ИЛИ добавить явный step-gate `if: steps.integration_*.outcome == 'failure'` → exit 1 |
| T11.3 | C-5, C-13 (HIGH гипотеза) | .github/actions/docker-build-cache/action.yml (прочитать!) | Если cache-key без hashFiles(context) → добавить `hashFiles('core/modules/hermes-agent/build/**', 'pyproject.toml')` + `${{ runner.os }}-py${{ matrix.python-version }}` |
| T11.4 | C-6, C-10 | .github/workflows/build-platform.yml:101-112 | L1 digest-pinning: записать digest в generated-манифест; context-воркфлоу пулит по digest; fail при расхождении; fallback-L1 — явный `::error::` маркер |
| T11.5 | C-3 | .github/workflows/mirror.yml:115-117 | При non-fast-forward: `git fetch origin main && git checkout --detach origin/main` перед push (push актуального main) |
| T11.6 | C-4 | .github/workflows/platform-test.yml:96 | Убрать `\|\| true` на fetch origin/main; ИЛИ проверка `git rev-parse --verify` + WARNING+skip с пометкой |
| T11.7 | C-7 | .github/workflows/mirror.yml:72-96 | Удалить дублирующий устаревший MODULE_CONTRACT |
| T11.8 | C-8 | .github/workflows/core-deploy.yml:40-41 + AGENTS.md | Верифицировать VPS_SSH_KEY: forced-command ci-deploy ИЛИ root shell? Если root → TRAP[DECISION] с Rev; если ci-deploy → команды через dispatch |
| T11.9 | C-9 | .github/workflows/core-deploy.yml:56 | Поднять timeout до 25-30 мин ИЛИ разделить на rsync (A) + node-update (B) с отдельными timeout |
| T11.10 | C-11 | .github/workflows/platform-test.yml:136-143,161-176 | Docker Hub токен через `env:` (не run:); pre-pull outcome aggregation + `::warning::`/`::error::` |
| T11.11 | C-12 (гипотеза) | .github/actions/sha-resolve/action.yml (прочитать!) | Bounded retry при «run not found» (eventual-consistency окно); fail при итоговой неопределённости |
| T11.12 | C-14 (LOW, Debt) | .github/workflows/platform-test.yml:65 | macOS-leg матрицы для non-Docker gate ИЛИ явный TRAP «Linux-only CI» |

AC W11: G-AC11 закрыт; C-1/C-2 либо gate-тест либо TRAP; cache-staleness (C-5/C-13) верифицирован чтением action.yml (фикс если подтверждён); digest-pinning для L1; gate fast ALL PASS; Debt для LOW (C-14).

### 12.4 W12 — Test-infra honesty & DR (поток REPO + SERVER, ~4ч)

Цель: test-infra honesty (T-1..T-15) — унификация counter, xdist-safe events, scope env-фикстур, multi-run flaky detection; DR для AGE (S-12); cross-cutting Debt-реестр.

| Задача | Источник | Файл | Действие |
|--------|----------|------|----------|
| T12.1 | T-1, T-2 (HIGH) | tests/_conftest/session.py + tests/gates/conftest.py | Унификация: один counter-модуль, один путь файла; gates/conftest — ре-экспорт; reset только при 100% PASS полной сессии (не поднабора) |
| T12.2 | T-3, T-4 (HIGH) | tests/_conftest/{smoke,wave_pipeline}.py | Снимок started/failed под lock перед yield; signal финального wave-event в main thread finally; xdist: маркер-фильтр docker-тестов в один воркер (ИЛИ documented TRAP «single-process docker») |
| T12.3 | T-5, T-6 (HIGH) | tests/_conftest/e2e.py + tests/conftest.py | Scope env-фикстур маркером `e2e`; restore NO_PROXY; platform_env на module-scope (не session) |
| T12.4 | T-7 | tests/_conftest/smoke.py:157 | Ленивый load platform-env.yaml (не import-time); fallback на env_defaults_generated.py |
| T12.5 | T-8 | tests/_conftest/session.py:80-83,96 | schema-валидация только в master (PYTEST_XDIST_WORKER гейт); per-test skip/fail вместо pytest.exit |
| T12.6 | T-9 | tests/conftest.py:181-185 | Документировать сортировку как контракт; gate на state-leak (каждый файл отдельно vs полный suite) |
| T12.7 | T-10, T-11 | tests/_conftest/smoke.py:597-607,456-496 | Loki timeout → fail/skip loki-зависимых; retry-until-green: логировать счётчик, gate при >15% retry-rate |
| T12.8 | T-12 | tests/_conftest/e2e.py:145,178-209 | R4-fail вместо skip (Grafana password absent); datasource_uids fail при недоступности |
| T12.9 | T-13, T-14 | tests/_conftest/session.py:239-284, smoke.py:168-180 | docker rm -f по label (не имени); host-директории cleanup в teardown ИЛИ tmp_path |
| T12.10 | T-15 | tests/conftest.py:176-179, _conftest/infra.py:312-328 | `_test_infra_was_active` вычислять в master на полной коллекции |
| T12.11 | — | tests/integration/test_multi_bootstrap_idempotency.py (новый), test_flaky_detection.py (новый) | Multi-run harness: 3× bootstrap на test-VPS (идемпотентность); 5× прогонов под нагрузкой (flaky detection); результаты в Debt или fix |
| T12.12 | S-12 (DR) | docs/age-master-key-dr.md (завершение из W10) | Off-node encrypted backup мастер-ключа (sops/KMS); процедура восстановления; threat-model; (если время) DR-drill на test-VPS |
| T12.13 | — | .ai/plans/136-bootstrap-hardening/04-Debt.md | Финальный Debt-реестр: все MEDIUM/LOW находки из W9-W11 + T-находки не закрытые фиксом; каждая с Rev-датой и verification-cost |

AC W12: G-AC12 закрыт; counter унифицирован; wave-события xdist-safe (или TRAP); env-фикстуры scoped; multi-run harness отработал (результаты в Debt/fix); DR-документ завершён; Debt-реестр финализирован; gate fast ALL PASS.

---

## 13. Порядок запуска meta-волн (ночь, автономно)

```
После влития W1-W8 (или параллельно с W6 server-циклом):
0ч   W9 (repo, Code-субагент, concurrency)      ─┐ параллельно:
     W11 (repo, Code-субагент, CI)               ─┤  W6 server-цикл (если нода)
2-3ч W10 (repo+server, security — verif на VPS)  ─┤  после W6: W10 verif
3-4ч W12 (repo+server, test-infra + DR)          ─┘  W12 multi-run на ноде
4-5ч Итог: Debt-реестр, VerificationReport 136 (meta-включения)
```

**Срезы meta при нехватке времени/ноды (строгий порядок):**
1. Обязательно: W10.1 (sudoers CRITICAL — фикс или TRAP), W9.1-W9.3 (concurrency flock + content-hash), W11.1-W11.2 (CI false-neg gate), W12.13 (Debt-реестр).
2. Желательно: W9.4-W9.20 (остальные concurrency), W10.2-W10.10 (security расширение), W12.1-W12.3 (test-infra honesty HIGH).
3. Откладываемо: W10.11-W10.13 (healthcheck drift — Debt), W11.3-W11.12 (CI cache/mirror — Debt), W12.4-W12.12 (test-infra MEDIUM, DR drill).

**Принцип автономности:** каждая meta-волна — отдельный Code-субагент с чётким prompt (SC1-meta); если фикс ломает платформу (sudoers, lock, hash) — субагент оформляет TRAP[DECISION] и продолжает; критические блокеры логируются в Debt, не останавливают ночную работу.

$END_DEVPLAN
