# 01-DevPlan.md — 125: Post-RC systemic closure (post-RC-121)

<!-- GREP_SUMMARY: devplan-125, post-rc, systemic-closure, verify-per-project, deploy-channel-gate, forced-command, rsync-guards, wildcard-coverage, debts, 123-124-verification -->
<!-- STRUCTURE: ▶ $ARTIFACT_CONTRACT → ◇ Контекст (факты верификации) → ◇ 🚨 ВОЛНА 0 (операторский чеклист, КРУПНО) → ◇ Волна 1 (CI/CD-канал: T1-T3) → ◇ Волна 2 (системные закрытия: T4-T6) → ◇ Волна 3 (долги: T7-T13) → ◇ Волна 4 (верификация 123/124 + коммит-гигиена: T14-T17) → ◇ Критерии ночного прогона → ◇ Порядок выполнения → ⎋ 🚨 финальный операторский чеклист -->

# region MODULE_CONTRACT
## @purpose  Девплан системного закрытия классов багов, найденных RC-сессией 121 (ночь+день 2026-08-03): verify-race (P-22), хардкод forced-command, незащищённый deploy-project.yml, несимметричные rsync-guard'ы, FL15/FL20/FL24, долги D-4..D-11, отсутствие QA-верификации 123/124. Цель — штатный ночной прогон после пересоздания ОБЕИХ нод: 0 системных фиксов, все проекты 200 в браузере.
## @scope    .github/workflows/{deploy-project,core-deploy}.yml, core/internal/verify/domain_verifier.py + фасады, core/internal/bootstrap/lifecycle/phases/system.py + helpers/users.py, core/internal/bootstrap/cert_orchestrator.py, core/internal/shared/docker_auth.py, core/schemas/node.schema.json, core/internal/scripts/generate_platform_env.py, core/internal/bootstrap/deploy/* (D-9), tests/gates/test_gate_deploy_channel.py, tests/, .ai/plans/{123,124}/, node-configs/, VPS 103.88.243.151.
## @invariants
##   1. Все фиксы проходят: make check (до чистоты) → make gate MODE=fast (один раз в конце)
##   2. Прод-рендер vhost'ов НЕ меняется (byte-for-byte) — правило 7 RC-121
##   3. Новый код — Python; shell — только тонкие фасады (языковая политика)
##   4. Generated-файлы не правятся руками (инвариант 11); никаких auto version bump
##   5. remote-команды никогда не получают локальные пути (усилено T9 гейтом, 123)
##   6. Параллельные сессии в одном worktree main — ЗАПРЕЩЕНЫ (FL24): только worktrees; при старте сессии проверить git status (грязное дерево от чужой сессии = STOP и согласование)
## @rationale Верификация 121 (2026-08-03): 21/21 фикс подтверждён кодом, но 3 класса закрыты точечно (rsync, HOME-резолв, check_suite/remote_root без тестов), verify-race (P-22) открыт и даст ложные фейлы при параллельных деплоях, forced-command захардкожен /opt/platform без теста, deploy-project.yml защищён только комментариями, FL15/FL20 не выполнены, 123/124 реализованы без QA-верификации (VR отсутствуют), D-4 частично закрыт (branch вне schema).
## @changes 2026-08-03 | Создан по итогам верификации RC-121 + суперпозиции и решений пользователя (verify per-project; forced-command канон+тест+smoke; гейт deploy_channel; верификация 123/124; операторский чеклист КРУПНО в начале/конце; все долги D-4..D-11 в девплан)
# endregion MODULE_CONTRACT

## $ARTIFACT_CONTRACT

| Поле | Значение |
|------|----------|
| **PURPOSE** | Сделать ночной RC-прогон штатным после пересоздания обеих нод: закрыть классы багов RC-121 системно (не в моменте), закрыть долги D-4..D-11, верифицировать 123/124, доказать CI/CD-канал |
| **DESCRIPTION** | Волна 0: операторский чеклист (КРУПНО). Волна 1: verify per-project (P-22), гейт платформенных зависимостей deploy-project.yml, forced-command канон+тест+smoke (FL20). Волна 2: rsync-симметрия, FL15 wildcard-покрытие, HOME-резолв. Волна 3: долги D-4..D-11. Волна 4: QA-верификация 123/124, feat-коммит 124, VR + Debt-обновление |
| **RATIONALE** | Верификация 121: 21/21 фикс подтверждён; системные закрытия нужны для 0 фиксов в ночном прогоне; 123/124 без QA = риск скрытых регрессий; 9/13 рекомендаций false-lead-log закрыты (FL6/7/9/10/11/13/14/16/17/18/19/21/22 — 13 из 24), остались FL15/FL20/FL23/FL24 |
| **ACCEPTANCE_CRITERIA** | (1) gate GREEN; (2) verify per-project: деплой проекта при 502 соседнего — PASS; (3) forced-command ping-смоук после bootstrap; (4) CI: platform-gate-fast GREEN, core-deploy выполнен; (5) локальный стек 21/21 + *.local 200 (после O1 — браузер); (6) e2e 10/10 на пересозданной test-e2e (≤2 попытки, 0 системных фиксов); (7) прод-бустрап tronyx-vps штатно (≤2 попытки, возможен 1 операторский сброс state); (8) проекты tronyx.ru/sexydancerostov.ru/botanika.tronyx.ru → **200** в браузере; (9) долги D-4..D-7 закрыты фактом/диагностикой, D-9..D-11 — задачи выполнены или перенесены с обоснованием; (10) VR 123 + VR 124 + VR 125 созданы |
| **IMPLEMENTS** | Верификация RC-121 + обсуждение 2026-08-03 (суперпозиция, решения пользователя) |
| **IMPACTS** | .github/workflows/, core/internal/, core/schemas/, tests/, .ai/plans/{123,124,125}/, VPS 103.88.243.151 |
| **REQUIRES** | AGE-ключ, SSH root@VPS, gh auth, доступ к GitHub-настройкам (O2), sudo на dev-машине (O1) |

---

## Контекст (факты верификации 2026-08-03)

1. **verify-race P-22 открыт (D-14)**: `deploy-project.yml:161` → `verify <node>` → `domain_verifier.py:143-168 get_expose_domains` берёт ВСЕ expose-домены ноды → параллельный деплой соседнего проекта = 502 в момент verify → ложный FAIL. Дневная сессия подтвердила: tronyx-site/dance-site CI «failure» при зелёном деплое.
2. **Forced-command захардкожен**: `phases/system.py:230-231` — `command="cd /opt/platform && PYTHONPATH=/opt/platform ..."` — литерал, не канон `platform_remote_base()` (deploy_paths.py). Нет unit-теста на строку command= (helpers/users.py add_ssh_key forced_command_prefix). FL20 (forced-command ping-смоук после bootstrap) не автоматизирован — vps_readiness CMD_PING существует, но пост-bootstrap вызова нет.
3. **deploy-project.yml защищён только комментариями**: `test_gate_deploy_channel.py` покрывает verbs ⊆ {ping,receive,verify}, но НЕ запрещает платформенные зависимости (relative actions `uses: ./.github/actions/*`, `python3 -m core...`, `make gate`) — повторный занос сломает все контекстные org'ы молча.
4. **rsync-guard'ы несимметричны**: `core-deploy.yml:165-173` (node-configs условный) — только один из трёх rsync-шагов; `:141-147` (core/ `--delete`) и `:154-156` (makefiles/) без guard'ов — при отсутствии источника `--delete` молча унесёт файлы.
5. **FL15 не выполнен**: cert_orchestrator после issue проверяет только rc, не покрытие домена wildcard'ом — ложный alarm «Missing cert» (false-lead #15).
6. **HOME-резолв конвенция**: `docker_auth.py:156` — `f"/home/{user}"` вместо `pwd.getpwnam(user).pw_dir`; не-стандартный home (или отсутствующий каталог) ломает login молча.
7. **D-4 частично закрыт**: node.yaml tronyx-vps уже на `contexts:` (строка 1), `expose` в schema (`node.schema.json:239-241`), top-level `context` отсутствует; остаток — `branch: main` (node.yaml:24) вне schema (0 вхождений в schema).
8. **123 (waves 1-3, a7609c7) и 124 (A2+, внутри 95fb62c) без QA-верификации**: VerificationReport'ы отсутствуют; 124-реализация (flock `tests/.docker-suite.lock`, `_xdist_args` docker-exclusion, session.py master-guard, `check-suite.yaml gates-docker xdist: false`) спрятана внутри fix(121)-коммита — нарушение per-wave audit-trail (Commit Policy U-83).
9. **FL24 нарушается прямо сейчас**: в worktree main идут незакоммиченные правки tests/gates/ (3 файла, 124-эксклюзии) + активный pytest-прогон чужой сессии (наблюдение 2026-08-03 18:34). Инвариант 6 (worktrees обязательны) не enforced.
10. **Долги**: D-5 (cadvisor unhealthy), D-6 (install-tor-proxy rc=1), D-7 (ufw 22/tcp verify), D-9 (docker-дубли deploy_engine/orchestrator/docker.sh → shared/docker_compose.py — модуль УЖЕ существует), D-10 (generate_platform_env f-string→jinja), D-11 (test-env-leak-and-flakes) — открыты с Rev-датами; по решению пользователя 2026-08-03 все входят в девплан 125.

---

## 🚨 ВОЛНА 0 — Операторский чеклист (выполнить ДО старта кода; проверить КРУПНО в конце сессии)

> **Правило сессии:** если любой из O1-O4 не выполнен к концу — явно кричать об этом в финальном отчёте ДО передачи ночного промта. Не прятать.

- [ ] **O1. /etc/hosts (P-20)**: `sudo sh -c 'printf "127.0.0.1 ai-platform.local tronyx-site.ai-platform.local dance-site.ai-platform.local botanika.ai-platform.local platform.ai-platform.local\n" >> /etc/hosts'` — иначе *.local проверяется только через curl --resolve, браузерная проверка невозможна.
- [ ] **O2. GHCR (P-13)**: проверить настройки пакета `hermes-agent-base` в tronyx161 (owner/приватность/write:packages); при необходимости — публикация L1 (public, DevPlan 116 B3 D1). `build-hermes.yml:90 push: true` должен стать GREEN.
- [ ] **O3. Обнуление нод**: пересоздать test-e2e И tronyx-vps (решение пользователя «обе»). После пересоздания — ночной прогон.
- [ ] **O4. gate-условие**: `make gate MODE=fast` GREEN перед запуском ночного промта (единственная точка входа).

---

## Волна 1 — CI/CD-канал (системные закрытия)

### T1. verify per-project (P-22/D-14) — закрыть ложные фейлы при параллельных деплоях
**Проблема**: `domain_verifier.py` проверяет ВСЕ expose-домены ноды; параллельный деплой соседнего проекта даёт 502 в момент verify → ложный FAIL CI.
**Задачи:**
- [ ] `core/internal/verify/domain_verifier.py`: добавить `--project <name>` — `get_expose_domains(yaml_path, project=None)` фильтрует по project name (registry-имя из node.yaml projects[].name); без `--project` — прежнее поведение (обратная совместимость, `make verify NODE=<node>` = все домены).
- [ ] `core/entrypoints/verify.sh` + `core/internal/verify/verify-domains.sh`: проброс `--project` (фасады, тонкие).
- [ ] `.github/workflows/deploy-project.yml:161`: `verify ${{ env.target_node }}` → `verify ${{ env.target_node }} ${{ inputs.project_name }}`.
- [ ] Тесты: unit — verify с --project при 502/не-200 соседнего домена → PASS; negative (R5) — verify без --project по-прежнему проверяет все домены; smoke: `make verify NODE=<node> PROJECT=<p>`.
- [ ] Обновить глоссарий/канон-таблицу (make verify) при изменении контракта.
**Приёмка**: параллельные деплои проектов → CI GREEN без verify-race; `make verify NODE=...` (без PROJECT) не сломан.

### T2. Гейт «платформенные зависимости в deploy-project.yml»
**Проблема**: защита только комментариями-инвариантами (deploy-project.yml:8-10, 19-22); повторный занос relative actions / `python3 -m core` / `make gate` сломает caller-контексты молча.
**Задачи:**
- [ ] Расширить `tests/gates/test_gate_deploy_channel.py`: негативные проверки run-шагов и шагов workflow: (а) `uses:` — только allowlist (setup-python/setup-ssh/setup-platform и подобные стандартные); relative `uses: ./.github/actions/*` → RED; (б) run-строки не содержат `python3 -m core` и `make gate`/`make deploy`; (в) R5 negative-тест (probe-workflow с нарушением → RED).
- [ ] Сверка: текущий deploy-project.yml проходит новый гейт (без правок workflow, если он чист).
**Приёмка**: гейт GREEN на текущем workflow; нарушение (внесённый probe) → RED.

### T3. Forced-command: канон + тест + smoke (FL20)
**Проблема**: `phases/system.py:230` захардкожен `/opt/platform`; нет теста на строку command=; пост-bootstrap smoke forced-command ping не автоматизирован — мёртвый канал не обнаружится до первого CI-деплоя.
**Задачи:**
- [ ] `phases/system.py:229-231`: заменить литерал на `platform_remote_base()` (import из shared/deploy_paths.py); `cd {base} && PYTHONPATH={base}`.
- [ ] Unit-тест: `tests/unit/` — генерация authorized_keys-строки через helpers/users.py add_ssh_key forced_command_prefix: command= содержит канонический base и `orchestrator_cli dispatch`, `restrict` присутствует (mocks: helpers_users, subprocess).
- [ ] Smoke forced-command ping после bootstrap: связать vps_readiness `check_vps_ready` (CMD_PING, `/opt/platform`-скоуп) с финалом bootstrap — добавить вызов в конец φ8.5/финала init-режима (state_machine.py run_init_mode после converge) ИЛИ документированный операторский шаг `make converge NODE=<node>` (уже содержит ping-фазу?) — выбрать по коду, минимально: явный лог «forced-command ping: OK/FAIL» в финале bootstrap; при FAIL — не блокировать bootstrap (warning), но печатать КРУПНО.
- [ ] core/AGENTS.md: обновить канон-строку forced-command (если меняется контракт).
**Приёмка**: unit-тест command= GREEN; после bootstrap на свежей ноде в логе виден forced-command ping OK; хардкода /opt/platform в phases/system.py нет.

---

## Волна 2 — Системные закрытия классов

### T4. rsync-симметрия в core-deploy.yml
**Проблема**: guard только для node-configs; `core/` (`--delete`) и `makefiles/` без guard'ов — при отсутствии источника `--delete` молча унесёт файлы.
**Задачи:**
- [ ] `core-deploy.yml:141-147` (core/ --delete) и `:154-156` (makefiles/): `if [ -d <src> ]` guard по образцу :165-173; TRAP[BUG]-комментарий; для `--delete` — дополнительная проверка «источник непуст» перед удалением.
- [ ] (Опционально) лог «skipped (source missing)» вместо молчаливого пропуска.
**Приёмка**: прогон core-deploy без node-configs/makefiles на раннере → SUCCESS с skip-логами; с источниками — rsync выполняется.

### T5. FL15: wildcard-покрытие после issue (cert_orchestrator)
**Проблема**: «botanika issued successfully» без сертификата — issue-cert.sh SKIP'ает поддомены wildcard'а, cert_orchestrator проверяет только rc → ложный alarm.
**Задачи:**
- [ ] `cert_orchestrator.py` (путь _process_single_domain / _post_issue): после issue — проверка покрытия домена: SAN-разбор выпущенного cert через `shared/ssl_certs.py` (cert_subject_matches_domain / cert_get_subject) + wildcard-покрытие (`*.tronyx.ru` покрывает `botanika.tronyx.ru`) → INFO-лог «covered by wildcard», НЕ alarm; только реальное отсутствие покрытия → WARN/FAIL.
- [ ] Тест: unit — домен под wildcard'ом → covered=true; домен вне → false (mocks ssl_certs).
**Приёмка**: φ7 на ноде с wildcard'ом не даёт ложных «Missing cert»; тест GREEN.

### T6. HOME-резолв docker_auth (системное закрытие конвенции)
**Проблема**: `docker_auth.py:156` — `f"/home/{user}"`; не-стандартный home молча ломает creds-путь.
**Задачи:**
- [ ] `docker_auth.py:155-158`: `pwd.getpwnam(user).pw_dir` (import pwd; fallback на f"/home/{user}" при KeyError); сохранить TRAP.
- [ ] Unit-тест: user с getpwnam → правильный HOME; несуществующий user → fallback (mocks pwd).
**Приёмка**: гейт GREEN; тест покрывает оба пути.

---

## Волна 3 — Долги (все по решению пользователя 2026-08-03)

### T7. D-4 остаток: `branch` в schema
- [ ] `core/schemas/node.schema.json`: добавить `branch` (string, optional) в project-свойства (прецедент expose:239-241); `branch: main` в node.yaml:24 валидируется без warning.
- [ ] Проверка: `make validate NODE=tronyx-vps` (или эквивалент) без schema-warning.
**Приёмка**: D-4 закрыт полностью (contexts[] + expose + branch в schema).

### T8. D-5: cadvisor unhealthy (прод)
- [ ] На проде: `make healthcheck` + `docker logs cadvisor` + `docker inspect` — установить причину (обычно: не-монтированные /sys, cgroup v2, метрики CPU). Диагностика → фикс или задокументированный WARN с Rev.
**Приёмка**: cadvisor healthy ИЛИ задокументированная причина (TRAP/Dept-запись).

### T9. D-6: install-tor-proxy rc=1 (tor/privoxy конфиг)
- [ ] На проде: проверить `/etc/tor/torrc`, privoxy config, `systemctl status tor privoxy`; сопоставить с фиксом bool-нормализации (T6 123) — возможно, уже закрыт; при остатке — починить/задокументировать.
**Приёмка**: rc=0 при повторном запуске ИЛИ задокументированная причина.

### T10. D-7: firewall verify 22/tcp not found
- [ ] На проде: `ufw status verbose`; проверить verify-логику (может, default allow); при необходимости — фикс verify или документация.
**Приёмка**: verify 22/tcp проходит ИЛИ задокументировано (default allow by design).

### T11. D-9: docker-дубли deploy_engine/orchestrator/docker.sh → shared/docker_compose.py
**Проблема**: shared/docker_compose.py УЖЕ существует (docker_compose_pull/build/up/healthcheck_poll); дубли в deploy_engine.py/orchestrator/docker.sh (P2-5 111).
**Задачи:**
- [ ] Аудит: grep по `docker compose` в core/internal/bootstrap/deploy/*.py + core/internal/bootstrap/docker.sh → список дублей vs shared/docker_compose.py.
- [ ] Миграция потребителей на shared (Strangler-Fig, по одному; shell docker.sh — тонкий фасад или удаление при 0 потребителей).
- [ ] Гейт/тест: единый источник (по образцу compose_files_sole_path).
**Приёмка**: 0 дублей compose-операций; гейт GREEN.

### T12. D-10: generate_platform_env f-string → jinja (LOW, опциональный)
- [ ] Оценить: если строковый рендер стабилен и покрыт тестами (generate_platform_env_yaml:266) — задокументировать «оставлено by design» (TRAP[DECISION] LOW) вместо рефакторинга; иначе — миграция на Jinja2 (контракт: byte-identical вывод, тесты).
**Приёмка**: решение зафиксировано (рефакторинг ИЛИ задокументированный keep).

### T13. D-11: test-env-leak-and-flakes
- [ ] Аудит тестов на зависимость от env dev-машины (паттерн: AGE_SECRET_KEY-fix 8cd8c38 как канон — детерминированные fixture, никаких чтений gitignored .env): grep по os.environ.get в tests/ + tests/_conftest/; составить список; закрыть точечно (fixture/monkeypatch).
- [ ] Флаки: зафиксировать известные (xdist-гонки закрыты 124; остатки — по .test_counter.json/log).
**Приёмка**: список env-зависимостей закрыт; повторных CI-фейлов «Precondition failed» нет.

---

## Волна 4 — Верификация 123/124 + коммит-гигиена

### T14. QA-верификация 123 (T1-T12)
- [ ] По коду и тестам подтвердить каждый T 123: T1 (cache→gha build-platform.yml:115,128; platform-test.yml:178-196; CI-strict push deploy.mk:133-144), T2 (статический .PHONY-парсинг generate_entrypoint_manifest.py, fallback make -np только при пустом результате; полный diff в --check; pre-commit cache key), T4 (converge.sh:81-82 + tests/test_converge_exit.py), T5 (test_hermes_init try/finally + session.py sweep), T6 (node_yaml_cli._format_cli_value + test_gate_bool_string_literals allowlist=1), T7 (APT_TIMEOUT), T8 (docstrings + overlay паритет deploy_orchestrator.py:634-637), T9 (test_gate_local_path_in_remote + node-lifecycle.sh:28 TRAP), T10 (mapping-документ files/compose-mounts-mapping.md + core/modules/AGENTS.md), T11 (sync_requirements.py + check-requirements), T12 (ci.mk полный список Failed).
- [ ] Составить VerificationReport 123 (в .ai/plans/123-nightly-hardening/02-VerificationReport.md).
**Приёмка**: VR 123 с вердиктом по каждому T.

### T15. QA-верификация 124 (A2+)
- [ ] Подтвердить кодом: test_runner.py _xdist_args docker-exclusion + flock tests/.docker-suite.lock (мастер-процесс), session.py master-guard (PYTEST_XDIST_WORKER), check-suite.yaml gates-docker xdist:false, tests/gates/*.py эксклюзии _b11_negative_*_tmp/_gate_probe_marker_tmp + FileNotFoundError-обработка; counter-семантика (failed_runs/attempts — 2 файла, сверить writer'ов).
- [ ] Прогон: `make check` (WORKERS=6) 2× подряд без флаков (регрессионный критерий 124).
- [ ] Составить финальный VerificationReport 124 (обновить 02-VerificationReport.md: вердикт после реализации).
**Приёмка**: VR 124 финальный; 2× make check без флаков.

### T16. feat-коммит 124 (per-wave audit-trail)
**Проблема**: реализация 124 спрятана внутри fix(121) 95fb62c — нарушение Commit Policy (волна = свой коммит).
**Задачи:**
- [ ] Не переписывать историю (force-push запрещён). Зафиксировать факт атрибуции: TRAP[DECISION] в 125 (или VR 124) — «реализация A2+ вошла в 95fb62c (общий коммит дневной RC-сессии 121); per-wave трейл восстановлен документально». Если у 124 есть незакоммиченные остатки (tests/gates/*.py) — оформить их отдельным `feat(124)`-коммитом после согласования с владельцем (см. инвариант 6: чужие незакоммиченные правки не трогать без согласования).
**Приёмка**: факт атрибуции задокументирован; незакоммиченные правки 124 — в отдельном feat-коммите (если владелец подтвердит).

### T17. VR 125 + Debt-актуализация
- [ ] `02-VerificationReport.md` в 125: фазы, вердикт, Test Health Score.
- [ ] Обновить `.ai/plans/121-rc-verification/01-Debt.md`: D-4..D-7 → FIXED/закрыто-фактом, D-9..D-11 → FIXED или перенос с обоснованием, D-14 (P-22) → FIXED (T1).
- [ ] 122: закрыть формально (VR или отметка в 125, что T1-T7 верифицированы gate'ом — см. VerificationReport 121 Фаза 1).
**Приёмка**: все долги в актуальном состоянии; 122/123/124/125 имеют VR или явную отметку.

---

## Критерии ночного прогона (после выполнения девплана)

1. **CI**: platform-gate-fast GREEN (123/124 верифицированы), Build Hermes GREEN (O2), core-deploy SUCCESS, L1 в ghcr.
2. **Локальный стек**: make up → 21/21 healthy; *.local → 200/301 (после O1 — браузер).
3. **E2E**: bootstrap на пересозданной test-e2e штатно (≤2 попытки, 0 системных фиксов), test-node 10/10.
4. **Прод**: bootstrap на пересозданной tronyx-vps штатно (возможен 1 операторский сброс state), ACME-сертификаты, forced-command ping OK в логе (T3), nginx healthy.
5. **Проекты в браузере**: tronyx.ru, sexydancerostov.ru, botanika.tronyx.ru → **200**; receive-аудит в логах; verify per-project без ложных фейлов (T1).
6. **Классы багов не воспроизводятся**: verify-race (T1), мёртвый forced-command (T3), ложный «Missing cert» (T5), rsync-унос (T4), CI verify-ложные фейлы.

## Порядок выполнения

Волна 0 (чеклист, согласование) → Волна 1 (T1-T3, CI-канал) → Волна 2 (T4-T6) → Волна 3 (T7-T13, долги; T8-T10 требуют прод-доступа, можно в конце с диагностикой) → Волна 4 (T14-T17, верификация и артефакты) → `make check` (до чистоты) → `make gate MODE=fast` → финальный операторский чеклист (O1-O4) → передача ночного промта.

---

## 🚨 ФИНАЛЬНЫЙ ОПЕРАТОРСКИЙ ЧЕКЛИСТ (проверить КРУПНО ПЕРЕД передачей ночного промта)

> Если что-то из этого не сделано или агент не может сделать сам — это ДОЛЖНО быть явно выделено в финальном отчёте, иначе ночной прогон повторит вчерашние блокеры:

- [ ] **O1. /etc/hosts** — *.local в браузере (`sudo sh -c 'printf ...'`, команда в Контексте)
- [ ] **O2. GHCR** — `hermes-agent-base` публичен/права write (P-13 → Build Hermes GREEN)
- [ ] **O3. Ноды пересозданы** — test-e2e + tronyx-vps (после этого НИЧЕГО не деплоить до ночного промта)
- [ ] **O4. gate GREEN** — `make gate MODE=fast` последним
