<!-- GREP_SUMMARY: fast-bootstrap-deploy devplan tasks waves test-spec one-command bootstrap deploy plan 012 -->
<!-- STRUCTURE: ▶ Requirements → ⟦Code Graph⟧ → ⚡ Data Flow → ⊕ $TASKS → ∑ $PARALLEL_GROUPS → ⎋ Next Steps -->
$START_DEVPLAN
# region MODULE_CONTRACT
## @purpose  DevPlan 012: реализация всех фиксов валидации 011 (P0/P1/P2) + автоматизация one-command
##           bootstrap/deploy. Авторитетный источник задач; Brief — 01-Brief.md (решения D1-D7).
## @scope    19 атомарных задач, 4 волны, критический путь = bootstrap fail-loud core (T3→T8→T9/T10).
## @invariants
##   - Каждая задача: один артефакт, измеримый AC, тесты из $TEST_SPEC
##   - Никаких новых make-глаголов (Brief D1); update-mode best-effort сохраняется (D2)
##   - required/generated секреты fail-loud всегда (D3)
##   - Верификация агента: per-task `make check TEST_FILE=...` → фикс-цикл `make check` до чистоты → `make agent-check`
# endregion MODULE_CONTRACT

# DevPlan — one-command bootstrap & honest deploy (план 012)

$ARTIFACT_CONTRACT
| Поле | Значение |
|------|----------|
| PURPOSE | Кодифицировать все находки плана 011 так, чтобы cold bootstrap и деплой проектов проходили одной командой без ручных обходов, с fail-loud честностью статусов |
| DESCRIPTION | 19 задач / 4 волны поверх существующей 14-фазной state machine; новые механизмы: ci_default auto-inject при decrypt, parity-гейт интерполяции, node-side dry-run φ8, strict-init exit, post-bootstrap report |
| RATIONALE | Q: почему такая декомпозиция? A: волны разделены по кластерам файлов (нет пересечений внутри волны → безопасное делегирование); критический путь минимален — только кластер deploy_orchestrator сериализован |
| ACCEPTANCE_CRITERIA | AC1-AC6 из 01-Brief.md §Acceptance; per-task AC ниже |
| IMPLEMENTS | F-001…F-037 (01-Findings.md 011), SEC-0018; решения владельца от 2026-08-26 (полный P2, sequential, auto-inject) |
| IMPACTS | ~35 файлов: core/internal/bootstrap/{deploy,lifecycle,phases}, core/internal/{deploy,llm,secrets,scaffold,healthcheck,shared}, core/modules/{platform-secrets,nginx,postgres,backup-cron}, tests/** |
| REQUIRES | Зелёный main; восстановленный канал task-делегирования ИЛИ последовательное исполнение главной сессией; для AC1 — пересозданная нода + окно оператора |

## Requirements Analysis — ключевые критерии успеха

1. **One-command bootstrap**: `make bootstrap-node NODE=<n>` без единого ручного вмешательства
   (сегодня: ZAI-матрица вручную, NGINX_OVERLAY_DIR workaround, source secrets.env для provision,
   drop-in PYTHONPATH после reboot — всё уходит в код).
2. **Fail-loud**: полу-стек невозможен как «success» — failed≠∅ в init → exit 2 + resumable.
3. **Preflight-самолечение**: класс ошибок «ключ есть в compose, отсутствует в матрице» лечится
   автоматически (ci_default) или падает ДО доставки (dry-run φ8, parity-гейт).
4. **Честный деплой**: rc деплоя = реальность (hook self-env), rollback честный ROLLED_BACK.
5. **Скорость**: wall-clock оператора ↓ (ноль обходов); machine-time не регрессирует
   (идемпотентный no-op ≤220s, init ≤~30 мин, новые проверки <60s).

## Draft Code Graph

```
core/
├── internal/
│   ├── bootstrap/
│   │   ├── python_deps.py                    [T2] req-path + marker-probe
│   │   ├── lifecycle/state_machine.py        [T9,T17] strict-init + report
│   │   └── deploy/
│   │       ├── docker_orchestrator.py        [T8] unconditional overlay export
│   │       ├── deploy_orchestrator.py        [T9,T10] --strict-init + dry-run preflight
│   │       ├── deploy-modules.sh             [T9] флаг-фасад
│   │       ├── compose_preflight.py          [T10] reuse helpers
│   │       └── llm_provision.py              [T12] env-chain
│   ├── deploy/
│   │   ├── hooks/post_deploy_chain.py        [T11] hook self-env
│   │   ├── rollback.py                       [T5] pull_policy + статус
│   │   ├── orchestrator.py                   [T5] lock-release/uid
│   │   └── age_key_backup.py                 [T6] matrix-resolve
│   ├── llm/key_provisioner.py                [T12] master-key chain + NO_PROXY + call-sites
│   ├── secrets/decrypt_secrets.py            [T3,T19] auto-inject + dev-dispatch
│   ├── scaffold/vhost_renderer.py            [T15] expose-consistency
│   ├── verify/sweep                          [T15] exposed-only фильтр
│   ├── healthcheck/{modules_healthcheck.py,watchdog.py} [T13,T16]
│   └── bootstrap/deploy/orphan_reconciler.py [T14]
├── modules/
│   ├── platform-secrets/platform-secrets.service [T1] PYTHONPATH
│   ├── nginx/nginx_reload_hook.sh            [T11] env-independent
│   ├── postgres/ (restore target)            [T7]
│   └── backup-cron/scripts/spool_retry.py    [T7] plaintext-isolation
└── secret-definitions.yaml                   [T4] SoT-реестр (read-only для гейта)
tests/
├── gates/test_gate_compose_interpolation_sot.py [T4, NEW]
├── unit/test_{python_deps,decrypt_secrets,rollback_contour,
│   docker_orchestrator,state_machine,key_provisioner,
│   post_deploy_chain,orphan_reconciler,...}.py  [расширения]
└── e2e/test_reboot_drill.py                  [T1, requires_node]
```

## Data Flow (после фиксов)

```
Оператор: make bootstrap-node NODE=n AGE_SECRET_KEY_FILE=f
▶ bootstrap.sh → preflight.py (ssh/disk/s3/ghcr/dns)
→ φ1 system_bootstrap (T2: deps гарантированы, boto3 probe)
→ φ4 secrets_provision (T3: decrypt + auto-inject ci_default c WARN;
                        required/generated missing → FAIL со списком ДО φ8)
→ φ7 certificates (S3-cache restore работает — T2 закрыл boto3)
→ φ8 deploy_services:
   ├─ T10 dry-run: docker compose config --quiet по всем группам c собранным env
   │   (любой ${VAR:?} unsatisfied → FAIL до контейнеров)
   ├─ T8: NGINX_OVERLAY_DIR экспортирован ВСЕГДА (не только nginx)
   ├─ deploy-modules (sequential) … failed≠∅ → T9: exit 2, state=failed, resumable
   └─ deploy-context → T12: provision-llm c master-key из secrets.env, NO_PROXY,
       TransportError → warn+continue
→ φ8.5 converge (T14: снимает контейнеры выключенных модулей)
→ T17 REPORT: deployed/failed, TLS, awaiting_projects, next commands
⎋ exit 0 только при полном успехе · reboot → юнит с PYTHONPATH (T1) поднимает стек

Деплой проекта: git push → CI receive → up healthy → T11 hook сам source-ит env
→ nginx -t OK → rc DEPLOYED (не ложный FAILED) · unhealthy → T5 rollback без
registry-pull локального тега → честный ROLLED_BACK
```

## $TASKS

Формат: ID · находка · артефакт · AC · зависимости · сложность (1-10).

### Волна 1 — независимые (разные файлы)

**T1 · [P0][F-037] Юнит platform-secrets с PYTHONPATH**
- Артефакт: `core/modules/platform-secrets/platform-secrets.service` + тест
- AC: unit содержит `Environment=PYTHONPATH=/opt/platform`; unit-тест читает файл и
  assert-ит строку (и наличие ExecStart на decrypt_secrets.py); requires_node e2e
  `tests/e2e/test_reboot_drill.py` (@pytest.mark.requires_node, manual) — reboot →
  platform-secrets active → docker healthy.
- Deps: нет · Сложность: 2

**T2 · [P1][F-019] python_deps: канонический путь requirements + инвалидация маркера**
- Артефакт: `core/internal/bootstrap/python_deps.py` + тесты
- AC: (a) requirements резолвится от `<core_dir>/requirements.txt` (канон доставки),
  не от корня платформы; (b) при marker-match выполняется import-probe критичных модулей
  (минимум boto3) — проваленный probe → переустановка (маркер не блокирует);
  (c) идемпотентность сохранена (повтор = no-op).
- Deps: нет · Сложность: 3

**T3 · [P1][F-014][D3] ci_default auto-inject при decrypt**
- Артефакт: `core/internal/secrets/decrypt_secrets.py` (+переиспользование загрузчика
  secret-definitions) + тесты
- AC: ключ tier=optional+ci_default, отсутствующий в матрице → дописан в secrets.env
  с маркер-комментарием `# auto-injected ci_default (plan 012)` и WARN-строкой в выводе;
  отсутствующий required/generated → exit≠0 со списком имён; полная матрица → поведение
  байт-идентично прежнему (регрессион-тест на фикстуре).
- Deps: нет · Сложность: 4

**T4 · [gate] Парити-гейт `${VAR:?}` ↔ SoT**
- Артефакт: `tests/gates/test_gate_compose_interpolation_sot.py` (новый)
- AC: сканирует `core/modules/*/docker-compose.base.yml` + root-compose на `${VAR:?...}`;
  каждый VAR обязан быть в {secret-definitions#name} ∪ {platform-infra env_defaults} ∪
  {tier=generated автоген-набор}; R5-негатив: инлайн-фикстура с неизвестным VAR → RED.
- Deps: нет · Сложность: 3

**T5 · [P1][F-025] Rollback honesty**
- Артефакт: `core/internal/deploy/rollback.py`, `orchestrator.py` (FileLock), тесты
- AC: (a) compose-up отката использует локальный previous-image без попытки registry-pull
  (pull_policy never/локальный резолв — по факту кода); (b) release lock удаляет lock-файл;
  (c) путь лока — uid-канон через shared/deploy_paths (без /tmp-коллизий root/ci-deploy);
  (d) сквозной статус ROLLED_BACK выставляется при verified (REF-0004 контракт цел).
- Deps: нет · Сложность: 4

**T6 · [P2][F-033] age-key-backup env из матрицы ноды**
- Артефакт: `core/internal/deploy/age_key_backup.py` + тест
- AC: AGE_RECIPIENT/S3_* резолвятся из sops-матрицы ноды (тот же механизм, что backup-cron);
  явные CLI-env сохраняют приоритет (override); без матрицы → читаемая ошибка, не тихий skip.
- Deps: нет · Сложность: 2

**T7 · [P1][F-031/F-032/SEC-0018] DR-ранбук restore + спул-изоляция**
- Артефакт: restore-target postgres (module.mk/custom), backup-cron спул, тесты
- AC: (a) restore работает штатно: root-compose + source secrets.env + COMPOSE_PROFILES
  (никаких «undefined volume»/«no service selected»); (b) порядок «postgres-only → restore →
  приложения» задокументирован в таргете (--clean-дамп или drop-before-restore — по факту кода);
  (c) pre_restore_* не лежат plaintext в retry-скане (gzip-суффикс или отдельный каталог вне
  scan-path); негатив-тест «plaintext .sql не попадает в S3-скан».
- Deps: нет · Сложность: 5

### Волна 2 — кластер deploy_orchestrator (сериализовано)

**T8 · [P1][F-015a] Unconditional NGINX_OVERLAY_DIR export**
- Артефакт: `core/internal/bootstrap/deploy/docker_orchestrator.py` + тесты
- AC: export выполняется для ЛЮБОГО module_name до первого compose-вызова (значение из
  overlay_dir/node.yaml#config_overlay, непустой fallback из env_defaults); R5-негатив:
  деплой не-nginx модуля на «голой» ноде (без env) проходит интерполяцию (регрессия F-015).
- Deps: нет (входит в волну 2 первой) · Сложность: 3

**T9 · [P1][F-015b] Strict-init семантика exit-кодов**
- Артефакт: `state_machine.py` (φ8/φ8.5), `deploy_orchestrator.py` (--strict-init),
  `deploy-modules.sh` (фасад-флаг), тесты characterisation + новые
- AC: init-режим: failed≠∅ ИЛИ crit>0 → фаза failed (exit 2), state.json = failed
  (resumable, повтор доводит); update-режим: контракт WARN→0 сохранён + IMP:9 summary
  `deployed=N failed=[...]`. Тест: init c failed-модулем → exit 2; update c тем же
  результатом → exit 0 + warning-summary.
- Deps: T8 · Сложность: 4

**T10 · Node-side interpolation dry-run (preflight φ8)**
- Артефакт: `deploy_orchestrator.py::_preflight` + reuse `compose_preflight` хелперов
- AC: перед деплоем каждой группы — `docker compose ... config --quiet` с собранным env
  (secrets.env + infra defaults + profiles + overlay-dir); первый unsatisfied `${VAR:?}` /
  ошибка модели → общий FAIL со списком всех проблемных модулей ДО создания контейнеров;
  добавляет <60s к init.
- Deps: T8 · Сложность: 4

**T11 · [P1][F-023] Deploy-hook самодостаточный env**
- Артефакт: `core/internal/deploy/hooks/post_deploy_chain.py`,
  `core/modules/nginx/nginx_reload_hook.sh` + regression-тест
- AC: hook выполняется в env-less ReceiveFlow без ручного source (source
  `/var/lib/platform/run/secrets.env` + overlay-dir export, либо `docker exec nginx nginx -t`
  вместо compose-exec — по факту кода выбирается минимальный вариант); регрессия F-023:
  успешный деплой → rc=DEPLOYED (не FAILED).
- Deps: нет (файлы не пересекаются с T8-T10) · Сложность: 3

### Волна 3 — LLM chain + ops honesty

**T12 · [P1][F-020/F-021/F-022] LLM chain env + transport coverage**
- Артефакт: `core/internal/llm/key_provisioner.py`, `llm_provision.py`, тесты
- AC: (a) master-key резолв: env → secrets_env_file() (явный fallback, F-020 закрыт в связке
  deploy-context); (b) host-run provision нейтрален к proxy (unset/NO_PROXY для 127.0.0.1 и
  локальных фасадов); PLATFORM_STATE_DIR через deploy_paths; base-url env-ручка
  (`LITELLM_BASE_URL`) наряду с CLI --base-url; (c) АУДИТ всех транспортных call-sites:
  LiteLLMTransportError в каждом except-кортеже с семантикой failed++, continue (G-тест на
  каждый сайт, включая листинг/поиск ключей вне текущего try).
- Deps: нет · Сложность: 5

**T13 · [P2][F-026] Window-based restart-детекция**
- Артефакт: детектор restart-loop (site: lib/healthcheck.sh / watchdog — locate по grep
  «restart loop»), тест
- AC: «restart-loop» поднимается только при рестартах в скользящем окне (например ≥N за
  15 мин), lifetime RestartCount от легитимных пересозданий не триггерит; E-сценарий F-026
  (Up 46 мин healthy после 14 деплой-рестартов) → PASS.
- Deps: нет · Сложность: 3

**T14 · [P2][F-027] Orphan reconciler снимает выключенные модули**
- Артефакт: `orphan_reconciler.py` (+converge-интеграция) + тесты
- AC: живой контейнер модуля, отсутствующего в желаемом COMPOSE_PROFILES/node.yaml →
  снимается (containers only, volumes НЕ затрагиваются); dry-run режим; R-тест
  «enabled:false → контейнер удалён, volume сохранён».
- Deps: нет · Сложность: 4

**T15 · [P2][F-034] Vhost expose-консистентность + e2e фильтр**
- Артефакт: `vhost_renderer.py`/render-all путь, verify_sweep, тесты
- AC: (a) найден и устранён реальный источник vhost для expose=false (root-cause: рендер,
  устаревший overlay-артефакт или scaffold-путь — зафиксировать в TRAP[BUG]); (b)
  e2e-verify ожидает ответ только от exposed-доменов node.yaml (non-exposed → пропуск);
  (c) R5-негатив: проект без expose → vhost не генерируется.
- Deps: нет · Сложность: 3

**T16 · [P2][F-016] Healthcheck NODE-guard**
- Артефакт: `core/entrypoints/healthcheck.sh` + `modules_healthcheck.py`
- AC: `make healthcheck NODE=<n>` с операторской машины НЕ молча проверяет локальный docker:
  либо явный remote-mode (SSH-exec), либо fail-loud с подсказкой («NODE — фильтр конфига;
  для ноды используйте ssh … / make e2e-verify NODE=»). Выбор варианта — по коду entrypoint
  (минимальный дифф).
- Deps: нет · Сложность: 2

### Волна 4 — эргономика one-command + интеграция

**T17 · Post-bootstrap report step**
- Артефакт: `lifecycle/cli.py`/`state_machine.py` финальный summary
- AC: после φ8.5 печатается отчёт (IMP:9): модули deployed/failed, TLS-статус, проекты
  awaiting_deploy, LLM keys, 3 suggested next commands (`check-security`, `e2e-verify`,
  `project-list`); JSON-вариант под флагом; не влияет на exit-code.
- Deps: T9 · Сложность: 2

**T18 · Hygiene batch: F-013, F-017, F-024, F-035, F-011, verify F-009/F-010/F-012/F-018**
- Артефакты: точечные правки (каждая ≤20 строк) + `git log`-верификация уже закоммиченных
- AC: (a) secrets-unlock bare-NODE на dev резолвит репо `node-configs/<ctx>/secrets/` когда
  /opt-путь отсутствует (только локальный dispatch, remote-passthrough не трогаем — гейт
  test_gate_local_path_in_remote зелёный); (b) PROJECTS_BASE dev-default ~/projects при
  незаданном env; (c) пустой `-i` в forced-command устранён; audit.jsonl доступен ci-deploy;
  (d) loadtest.mk использует $(PYTHON); (e) PROMETHEUS_RULES_DIR консолидирован к SoT
  deploy_paths (F-011); (f) фиксы F-009/F-010/F-012/F-018 подтверждены в git log
  (закрыть в Findings 011 пометкой committed).
- Deps: нет · Сложность: 3

**T19 · Интеграция: манифесты, документация, чистота**
- Артефакт: regenerated manifests, обновлённые AGENTS.md-секции (Runbook bootstrap:
  one-command flow, strict-init, auto-inject), финальные проверки
- AC: `make generate-manifests` чистый (drift=0); `make check` GREEN (известный ACCEPTED-RED
  test_no_empty_dirs asi-* — вне юрисдикции); `make agent-check` exit 0; Runbook-секция
  bootstrap/AGENTS.md отражает новую семантику.
- Deps: T1-T18 · Сложность: 2

**Merge-rule аудит:** микрозадачи (<2 файлов, <20 строк) без родителя сохранены как
standalone внутри T18 (batch) — отдельный трекинг не нужен.

## $PARALLEL_GROUPS

Пересечение файлов внутри волны = 0 (кластер deploy_orchestrator сериализован в волне 2).

```markdown
## $PARALLEL_GROUPS
### Wave 1 (independent, no shared files)
- Tasks: T1, T2, T3, T4, T5, T6, T7
- Command: Use coder role and read .ai/plans/012-fast-bootstrap-deploy/02-DevPlan.md, implement Wave 1: T1, T2, T3, T4, T5, T6, T7
### Wave 2 (deploy cluster, serialized within wave)
- Tasks: T8 → T9 → T10, параллельно T11
- Command: Use coder role and read .ai/plans/012-fast-bootstrap-deploy/02-DevPlan.md, implement Wave 2: T8, T9, T10, T11
### Wave 3 (llm + ops honesty, no shared files)
- Tasks: T12, T13, T14, T15, T16
- Command: Use coder role and read .ai/plans/012-fast-bootstrap-deploy/02-DevPlan.md, implement Wave 3: T12, T13, T14, T15, T16
### Wave 4 (integration)
- Tasks: T17, T18, затем T19
- Command: Use coder role and read .ai/plans/012-fast-bootstrap-deploy/02-DevPlan.md, implement Wave 4: T17, T18, T19
```

## Acceptance Criteria (сводная таблица)

| AC | Критерий | Задачи | Верификация |
|----|----------|--------|-------------|
| AC1 | Cold bootstrap одной командой без обходов | T1-T3,T8-T12 | Контрольный cold-start (manual, release-checklist) |
| AC2 | Init failed≠∅ → exit 2 + resumable | T8,T9 | Unit strict-init + R5-negative F-015 |
| AC3 | Auto-inject ci_default + fail-loud required + parity-gate | T3,T4 | Unit inject + gate RED-фикстура |
| AC4 | Честный деплой/rollback | T5,T11 | Регрессии F-023/F-025 |
| AC5 | Reboot-resilience из сгенерированных юнитов | T1 | Unit контента + requires_node drill |
| AC6 | Идемпотентность ≤220s no-op; init ≤~30 мин | T10,T17 | Замер cold-start; идемпотентность-тесты |

## File Manifest

| Область | Файлы |
|---------|-------|
| platform-secrets | platform-secrets.service, installer.py (verify only) |
| bootstrap | python_deps.py, lifecycle/state_machine.py, lifecycle/cli.py, deploy/{docker_orchestrator,deploy_orchestrator,deploy-modules.sh,compose_preflight(reuse)}.py* |
| deploy | orchestrator.py, rollback.py, hooks/post_deploy_chain.py, age_key_backup.py |
| llm | key_provisioner.py, llm_provision.py, admin_client.py (except-tuple only) |
| secrets | decrypt_secrets.py |
| scaffold/verify | vhost_renderer.py, internal/verify/sweep* |
| healthcheck | modules_healthcheck.py, watchdog.py, lib/healthcheck.sh (детектор) |
| modules | nginx/nginx_reload_hook.sh, postgres (Makefile/mk), backup-cron/scripts/spool_retry.py |
| gates/tests | tests/gates/test_gate_compose_interpolation_sot.py (NEW), tests/unit/* (≈12 расширений), tests/e2e/test_reboot_drill.py (NEW, requires_node) |
| docs | core/internal/bootstrap/AGENTS.md (Runbook), core/AGENTS.md (canon_table regeneration only if chains changed) |

## Design Decisions (@rationale — свод; полные формулировки в 01-Brief.md D1-D7)

- **D1 zero new verbs** — Q: почему? A: каноническая цепочка полна; чиним звенья, не строим параллельную.
- **D2 strict-init/best-effort-update** — Q: почему не глобальный strict? A: DEPLOY_BEST_EFFORT — контракт CI node-update; ломать = красный CI на warn-хвостах без пользы.
- **D3 auto-inject + parity gate** — Q: почему не только gate? A: прецедент DEEPSEEK/ZAI — то же значение вернулось бы руками; автоматизация убирает класс, гейт ловит будущее.
- **D8 dry-run в φ8, а не отдельным verb'ом** — Q: почему? A: защита должна стоять НА пути исполнения (невозможно забыть), а не рядом с ним; повторное использование env-сборки деплоя исключает рассинхрон.
- **D9 hook self-env предпочтён смене docker compose exec → docker exec** — Q: почему? A: сохраняет typed-контракт invoke_module_interface; прямой docker exec — fallback, если source-env в hook-контексте недоступен (решает Coder по факту кода, оба варианта покрыты AC T11).

## $TEST_SPEC

| Test file | Test function | Scenario | Module under test |
|-----------|---------------|----------|-------------------|
| tests/unit/test_platform_secrets_unit.py | test_unit_contains_pythonpath | Сгенерированный/установленный юнит содержит Environment=PYTHONPATH=/opt/platform | platform-secrets.service |
| tests/e2e/test_reboot_drill.py | test_reboot_stack_alive (requires_node) | reboot ноды → platform-secrets active → docker 25/25 healthy | systemd unit chain |
| tests/unit/test_python_deps.py | test_requirements_canonical_path / test_marker_invalidated_by_failed_import | Резолв <core_dir>/requirements.txt; marker-match + провал import-probe → reinstall | python_deps.py |
| tests/unit/test_decrypt_secrets.py | test_ci_default_auto_inject / test_missing_required_fails_loud / test_complete_matrix_unchanged | Отсутствующий optional+ci_default дописан c WARN; required/generated missing → exit≠0+список; полная матрица byte-identical | decrypt_secrets.py |
| tests/gates/test_gate_compose_interpolation_sot.py | test_all_required_vars_registered / test_unknown_var_red (R5-negative, сценарий ZAI F-014) | Каждый ${VAR:?} ↔ SoT; неизвестный VAR → RED | gate: composes ↔ manifests |
| tests/unit/test_docker_orchestrator.py | test_overlay_dir_exported_for_non_nginx (R5-negative F-015) | Деплой не-nginx модуля без внешнего env → интерполяция получает NGINX_OVERLAY_DIR | docker_orchestrator.py |
| tests/unit/test_state_machine.py | test_init_strict_exit_on_failed / test_update_best_effort_preserved | φ8 init failed≠∅ → exit 2 + state failed; тот же результат в update → exit 0 + summary | state_machine.py, deploy_orchestrator.py |
| tests/unit/test_deploy_orchestrator_preflight.py | test_dry_run_blocks_unsatisfied_interpolation | `${VAR:?}` unsatisfied → FAIL до контейнеров со списком модулей | _preflight dry-run |
| tests/unit/test_post_deploy_chain.py | test_hook_runs_without_receive_env (регрессия F-023) | Env-less ReceiveFlow → hook проходит, rc=DEPLOYED | post_deploy_chain.py, nginx_reload_hook.sh |
| tests/unit/test_rollback_contour.py | test_local_previous_image_no_registry_pull / test_lock_file_removed_on_release (F-025) | Откат на локальный тег без ghcr-pull; lock удалён; ROLLED_BACK при verified | rollback.py, orchestrator.py |
| tests/unit/test_key_provisioner.py | test_master_key_resolved_from_secrets_env / test_transport_error_continues_per_site (F-020/F-021) / test_no_proxy_for_local_facades (F-022) | Fallback-chain master-key; TransportError на КАЖДОМ сайте → failed++ continue; proxy-нейтральность host-run | key_provisioner.py |
| tests/unit/test_age_key_backup.py | test_env_resolved_from_node_matrix (F-033) | AGE_RECIPIENT/S3_* из матрицы; CLI-env приоритетнее | age_key_backup.py |
| tests/unit/test_postgres_restore.py / shellcheck | test_restore_target_contract (F-031/F-032) | Root-compose+env+profiles; plaintext pre_restore не в retry-скане (SEC-0018 негатив) | postgres restore, spool_retry.py |
| tests/unit/test_watchdog_restart_window.py | test_lifetime_restarts_do_not_trip (F-026) | Lifetime RestartCount высокий + Up long healthy → PASS; окно-рестарты → trip | restart-детектор |
| tests/unit/test_orphan_reconciler.py | test_disabled_module_container_removed_volume_kept (F-027) | enabled:false → контейнер снят, volume сохранён | orphan_reconciler.py |
| tests/unit/test_vhost_renderer.py | test_non_exposed_no_vhost + e2e sweep filter (F-034) | expose=false → vhost не генерируется; e2e ожидает только exposed | vhost_renderer, verify_sweep |
| tests/unit/test_healthcheck_guard.py | test_node_param_requires_remote_mode (F-016) | NODE≠local → guard/hint, не молчаливый локальный прогон | healthcheck entrypoint |

## Debt Intake (deferred, с условиями ревизии)

| Item | Условие Rev |
|------|-------------|
| F-036 load-test PromQL-pull vs AllowTcpForwarding=no | Решение владельца: node-side saturation-pull ИЛИ sshd exception; до решения load-test smoke остаётся BLOCKED |
| G2 chaos-night полный прогон | Владелец выделяет ночное окно (сценарии длительные) |
| D5 GitHub billing TronyxLab / core-deploy CI secret step_10 | Действия владельца; после — повтор D5 + H.3 |
| G5/H1 test-VPS | Появление test-VPS → обязательный `make test-node` до промоутов |
| DEPLOY_PARALLEL default=true | Отдельный план после стабилизации 012 (владелец: «нет» в этой итерации) |

## Change Impact / Configuration Drift

- Exit-контракт init меняется → каскад: bootstrap.sh/node-lifecycle.sh обработка кодов, CI-воркфлоу
  core-deploy (если парсят exit) — проверить потребителей `deploy-modules` rc перед merge T9.
- Auto-inject меняет содержимое secrets.env → потребители парсинга (compose_preflight,
  context_deployer, notify-hook) совместимы (формат KEY=value сохраняется) — покрыто
  регрессион-тестом T3.
- PROMETHEUS_RULES_DIR консолидация (T18e) — cascade: platform-infra.yaml ↔ compose monitoring
  ↔ .env.example (генерируется) — один SoT, остальные GENERATED.

## Next Steps

Коммиты: `docs(012): план one-command bootstrap` → по одному `feat(012)` на волну.

```markdown
### Wave 1
Use coder role and read /Users/tronyx/projects/ai-platform/.ai/plans/012-fast-bootstrap-deploy/02-DevPlan.md, implement Wave 1: T1, T2, T3, T4, T5, T6, T7
Верификация: per-task `make check TEST_FILE=...`; затем `make check` до чистоты; `make agent-check`.

### Wave 2
Use coder role and read /Users/tronyx/projects/ai-platform/.ai/plans/012-fast-bootstrap-deploy/02-DevPlan.md, implement Wave 2: T8, T9, T10, T11 (T8→T9→T10 последовательно, T11 параллельно)
Верификация: та же.

### Wave 3
Use coder role and read /Users/tronyx/projects/ai-platform/.ai/plans/012-fast-bootstrap-deploy/02-DevPlan.md, implement Wave 3: T12, T13, T14, T15, T16
Верификация: та же.

### Wave 4
Use coder role and read /Users/tronyx/projects/ai-platform/.ai/plans/012-fast-bootstrap-deploy/02-DevPlan.md, implement Wave 4: T17, T18, затем T19 (интеграция)
Финал: `make check` GREEN + `make agent-check` exit 0; AC1 (cold-start) — ручной шаг release-checklist на пересозданной ноде.
```

$END_DEVPLAN
