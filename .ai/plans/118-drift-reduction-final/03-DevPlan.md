# 03-DevPlan — Бриф B: мёртвый код, 2-й проход

<!-- $ARTIFACT_CONTRACT
PURPOSE:          Удаление остаточного мёртвого кода после волны 117 (dead-code sweep был неполным) — чистка перед ручным тестированием.
DESCRIPTION:      10 задач: B1 state_machine step-API, B2 content_hash rename, B3 NodeYaml typed-геттеры, B4 generate-manifests-atomic,
                  B5 vps_status_check, B6 12 мёртвых lib-функций, B7 ready-check ×2, B8 module-hooks, B9 pre-commit hook, B10 gate исключение.
RATIONALE:        Мёртвый код = ложные срабатывания поиска, дрейф документации, невидимые для гейтов зоны. Удаление безопасно (0 потребителей, верифицировано).
ACCEPTANCE_CRITERIA:
  - AC-B1: step-API удалён; тесты на удалённые методы помечены removed API; 0 ссылок в core/.
  - AC-B2: content_hash (bootstrap/deploy) переименован в build_cache.py; импорты обновлены; shared/content_hash не тронут.
  - AC-B3: typed-геттеры удалены ТОЛЬКО после verify-then-delete (git-история, CI, runbook); при 1 легитимном потребителе — KEEP с пометкой debug-only.
  - AC-B4: generate-manifests-atomic удалён из Makefile + allowed_verbs + глоссария.
  - AC-B5: vps_status_check.py + test удалены; 0 ссылок.
  - AC-B6: 12 мёртвых lib-функций + python_deps.sh удалены; source-строки почищены; их тесты удалены.
  - AC-B7: ready-check.sh ×2 удалены; Dockerfile COPY строки убраны; dead-code gate-исключение обновлено.
  - AC-B8: module-hooks решены: восстановлен триггер ИЛИ удалена регистрация (module.yaml + manifest module_hooks) — без «зарегистрировано, но не вызывается».
  - AC-B9: pre-commit hook commit-msg исправлен (check_commit_msg.py).
  - AC-B10: test_gate_dead_code.py исключение secrets-init.sh удалено.
  - AC-B11: gate MODE=fast, check-manifests, ruff — зелёные; 0 regressions.
IMPLEMENTS:       118 01-Brief задачи B1-B10.
IMPACTS:          core/internal/bootstrap/lifecycle/state_machine.py, core/lib/*.sh, core/internal/scripts/vps_status_check.py,
                  core/modules/{postgres,backup-cron}/ready-check.sh, core/modules/*/hooks/, .pre-commit-config.yaml, tests/, makefiles/, entrypoint-manifest.yaml.
REQUIRES:         118 01-Brief, верификация потребителей (в §1).
-->

---

## 1. Технический анализ и решения

### B1 (HIGH) — state_machine step-API

**Факты (верифицированы):** `state_machine.py` — `start_step/complete_step/skip_step/fail_step/get_current_step` (~334-439) + `_check_precondition/_check_postcondition/_is_step_done/_is_step_skipped/_hash_changed` (~640-705). `rg` по core/ + tests/ (вне state_machine.py) → **0 callers**. CLI работает через `execute_phase/setup_state` (grouped-phases эра B9).

**Решение:** удалить step-API + helpers. Тесты, ссылающиеся на удалённые методы → помечаются `removed API` (или удаляются). Проверить `state_store.py` — не пересекается ли persistence.

**Тест:** negative-тест: `hasattr` на удалённый метод = False (R5 для удалённого API).

**Риск:** MED (ломает legacy-тесты — правим по R5).

### B2 (MED) — content_hash номенклатурный rename

**Факты (верифицированы):** два модуля с именем `content_hash.py`: `bootstrap/deploy/content_hash.py` (Dockerfile/build-context hash, API: compute_source_hash/check_build_needed/save_build_hash) и `shared/content_hash.py` (generic file-list hash). Реализации разные, имена одинаковые.

**Решение:** rename `bootstrap/deploy/content_hash.py` → `bootstrap/deploy/build_cache.py`. Обновить импорты в `docker_orchestrator.py`. shared/content_hash — канон, не тронут.

**Тест:** импорт-тест build_cache; 0 ссылок на старое имя.

**Риск:** LOW.

### B3 (MED) — NodeYaml typed-геттеры (verify-then-delete)

**Факты:** ~500 LOC typed-геттеров в `shared/node_yaml.py` (get_tor_config, get_repos, get_postgres_init_databases, get_node_declaration, get_acme_dns_plugin, get_email, get_firewall, get_secrets_config, get_contexts, get_domain + CLI-флаги `--typed-*` в node_yaml_cli.py). Аудит: 0 shell-consumers.

**Решение (по решению пользователя verify-then-delete):**
1. `grep` shell/CI/runbook на `--typed-*` и `get_<domain>` потребителей.
2. Если 0 легитимных operator-usage → удалить геттеры + CLI-флаги. Иначе KEEP с пометкой debug-only.

**Тест:** negative-тест на удалённые CLI-флаги (unknown flag → exit≠0).

**Риск:** LOW-MED (после верификации).

### B4 (LOW) — generate-manifests-atomic

**Факты:** `makefiles/manifest.mk:118-162` — target со сломанной mv-семантикой (затирает root AGENTS.md), TRAP[DEBT] признал dead.

**Решение:** удалить таргет + `.PHONY` + запись в allowed_verbs + глоссарий (регенерация).

**Риск:** LOW (не вызывается).

### B5 (LOW) — vps_status_check.py

**Факты (верифицированы):** `core/internal/scripts/vps_status_check.py` (130 LOC) — 0 импортеров, 0 вызовов из Makefile/workflows/manifest; упоминается только в help-тексте check-no-new-inline-python3.sh:60 и своём тесте. Функциональность поглощена `project_lister.get_status_via_ssh`.

**Решение:** удалить модуль + тест + help-строку.

**Риск:** LOW.

### B6 (MED) — 12 мёртвых lib-функций + python_deps.sh

**Факты (верифицированы, присутствуют в):** `docker.sh:ensure_docker_network`, `healthcheck.sh:poll_until_healthy/poll_docker_health/check_tcp`, `logging.sh:log_info/log_ok`, `node-resolver.sh:resolve_node_from_env`, `ssh.sh:ssh_exec_dry_run`, `args.sh:parse_args` (все 4 потребителя определяют свой — контракт несовместим, TRAP passthrough), `secrets.sh:step_12b_ensure_secrets/_ensure_htpasswd_generated`. `python_deps.sh` (38 LOC, `require_python_module`) — 0 вызовов, source-ится adopt-project.sh:18.

**Решение:** удалить функции + их тесты. Для python_deps.sh — удалить файл + source-строку в adopt-project.sh + упоминание в validate.sh:9.

**Тест:** negative-тесты на удалённые функции (command not found через `type -t` = пусто).

**Риск:** LOW-MED (только тесты затрагивает; `poll_until_healthy`/`check_tcp` — убедиться, что module-interface.sh их не зовёт).

### B7 (LOW) — ready-check.sh ×2

**Факты (верифицированы):** `core/modules/postgres/ready-check.sh` (33) + `core/modules/backup-cron/ready-check.sh` (38) — только COPY в Dockerfile, 0 runtime-вызовов (compose/nginx не ссылаются). dead-code gate исключает их как «compose readiness probe» ошибочно.

**Решение:** удалить оба + COPY-строки из Dockerfile + устаревшее gate-исключение. Если это документированный ready-контракт модуля — вынести в комментарий module.yaml вместо файла.

**Риск:** LOW.

### B8 (HIGH) — module-hooks: wire или delete

**Факты (верифицированы):** 3 файла зарегистрированы (`module.yaml hooks.on_project_deploy` + `entrypoint-manifest.yaml module_hooks`): `nginx/nginx_reload_hook.sh`, `monitoring/hooks/on-project-deploy.sh`, `postgres/hooks/on-project-deploy.sh`. Триггер `invoke_module_interface <m> deploy-hook` **не вызывается** ни одним Python-модулем (удалён в 117 sweep). Потребители — только gate-тесты.

**Решение (ветвление):**
- **Вариант 1 (рекомендуемый для волны):** восстановить триггер в Python-пайплайне деплоя (deploy_orchestrator.py post-deploy-цепочка вызывает `deploy-hook` для зарегистрированных модулей через `shared/module_interface.py` из C5). Требует C5.
- **Вариант 2:** удалить регистрацию (module.yaml + manifest module_hooks) и файлы, если hooks устарели (nginx reload уже делает context_deployer; monitoring render — через Python; postgres — on_project_deploy.py существует как Python).

**Решение принимается на брифе по фактическому содержимому 3 файлов** (nginx_reload_hook — реальная логика reload-guard; monitoring/postgres — тонкие фасады). Рекомендация: nginx hook — восстановить триггер (реальная логика), monitoring/postgres — удалить регистрацию (Python-эквиваленты есть).

**Тест:** gate-тест: каждый module_hooks-verb имеет runtime-вызов (или удалён) — закрывает «зарегистрировано, но не вызывается».

**Риск:** MED (зависит от C5 при Варианте 1).

### B9 (LOW) — pre-commit hook commit-msg

**Факты (верифицированы):** `.pre-commit-config.yaml:250` (hook commit-msg) ссылается на `core/entrypoints/check-commit-msg.sh` — файла нет. Реальный модуль — `check_commit_msg.py`. Установленный `.git/hooks/commit-msg` вызывает его напрямую — hook сломан в конфиге.

**Решение:** исправить entry на `check_commit_msg.py` (или удалить shell-обёртку-ссылку). Проверить локальные `.git/hooks/` (pre-push пустой, pre-commit отключён — зафиксировать как локальное состояние, не репозиторий).

**Риск:** LOW.

### B10 (LOW) — test_gate_dead_code.py исключение

**Факты:** `_EXCEPTION_PATHS` содержит `core/internal/bootstrap/secrets-init.sh` — файл удалён (DevPlan 087/091). Запись бесполезна.

**Решение:** удалить запись из исключений.

**Риск:** LOW.

---

## 2. Порядок выполнения

```
B9 → B10 → B5 → B7     ← точечные удаления (независимы)
   │
B6 (lib-функции)       ← проверить module-interface.sh перед удалением
   │
B1 → B2                ← state_machine + rename (общие с D3 мега-плана нет)
   │
B3 (typed-геттеры)     ← verify-then-delete (требует git-историю)
   │
B4 (manifest-atomic)   ← вместе с G2 (регенерация манифеста)
   │
B8 (module-hooks)      ← ЗАВИСИТ от C5 (module_interface) при Варианте 1
```

## 3. Оценки

| Метрика | Значение |
|---------|----------|
| Задач | 10 |
| LOC-удаление | ~700 (B1 −140, B3 −500, B5 −130, B6 −242, B7 −71, B4 −48) |
| Рискованных | B3 (verify-then-delete), B8 (wire-vs-delete) |

## $END

Открытые вопросы:
1. **B8** — содержимое 3 hook-файлов определяет выбор wire-vs-delete; решение на имплементации брифа.
2. **B3** — проверка git-истории operator-usage (доступен ли git log по node_yaml CLI-флагам).
3. **B6** — проверить module-interface.sh и модульные healthcheck на вызовы poll_until_healthy/check_tcp.
