<!-- GREP_SUMMARY: statusreport 019 asi-group pilots incident compose networks DSN K1 K3 parity-db scaffold BLOCKED bare-node -->
<!-- STRUCTURE: ▶ $ARTIFACT_CONTRACT → ◇ статус задач T1-T8 (таблица) → ⛔ TASK-7 BLOCKED (доказательства) → ◇ верификация → ⎋ выводы + Rev -->
# region MODULE_CONTRACT
## @purpose  Статус-отчёт плана 019 (asi-group pilot integration): фактическое состояние волн
##           1-3, доказательства блокера TASK-7 (голая нода asi-team-vps), результаты
##           верификации. Для следующего агента/оператора: что сделано, что заблокировано,
##           как продолжить (Rev).
## @scope    План 019; волны 1-3 (TASK-1..TASK-8). Код-задачи (T1-T6, T8) — done;
##           операционная (T7) — ⛔ BLOCKED (environmental).
## @invariants
##   - Факты ноды проверены read-only SSH (2026-08-31): docker/service отсутствуют, /opt пуст
##   - Дубли БД/ролей НЕ создавались: манифесты пилотов несли name=существующим БД/ролям
##   - make check финальный прогон — единственный арбитр (после коммитов)
## @rationale Отчёт фиксирует «правду момента»: TASK-7 требует живой ноды, её нет —
##            молчаливый пропуск хуже честного BLOCKED (анти-луп, R4).
## @changes  2026-08-31 · Plan 019 — создан
## @changes  2026-08-31 · QA F-1/F-2/DRIFT-1/2 — правки: TASK-8 смоук-доказательство,
##            отклонение №6 (ai-instructions --template gap), stale-ссылки 92009e1+67cd84e
##            и 4e623c1→2419325 (фактический пин channel_pin.py)
# endregion MODULE_CONTRACT

# StatusReport — Plan 019: asi-group pilot integration

Дата: 2026-08-31 · Исполнитель: Coder (код) + Sysadmin-проверка ноды (read-only)

## $ARTIFACT_CONTRACT

- **PURPOSE**: зафиксировать состояние выполнения плана 019 и блокер TASK-7.
- **DESCRIPTION**: статусы TASK-1..TASK-8, доказательства голой ноды, результаты верификации.
- **RATIONALE**: следующий агент стартует отсюда, не перечитывая DevPlan целиком.
- **ACCEPTANCE_CRITERIA**: статусы по каждой задаче + факты-доказательства блокера.
- **IMPLEMENTS**: 01-DevPlan.md (все задачи кроме TASK-7 — код; TASK-7 — операционный блок).
- **IMPACTS**: проекты/пилоты asi-group (локальные правки, вне git), ядро платформы (K1/K3, parity-db, scaffolder).
- **REQUIRES**: bootstrap ноды asi-team-vps (или решение владельца о деплое) для закрытия TASK-7.

## Статусы задач

| Задача | Статус | Доказательство |
|--------|--------|----------------|
| TASK-1: template-ai-project (сети + DSN-маппинг + AGENTS.md env-контракт) | ✅ done | `templates/template-ai-project/{docker-compose.yml,AGENTS.md}`; gate `test_gate_template_ai_project_networks` (1/1) + `test_all_templates_use_strict_grammar` зелёный |
| TASK-2: production-compose пилотов | ✅ done (локально) | оба compose: 4 сети + `DATABASE_URL=${PLATFORM_POSTGRES_DSN}`; `docker compose --env-file .env.platform config` → DSN интерполирован, сети attached (AC1 static). managers-bot: сервис переименован asi-managers→managers-bot (U-37 service=project_name; image не менялся — F9 вне скоупа) |
| TASK-3: полные ai-platform.yaml пилотов + sync-env | ✅ done | `name: asi-faq / managers-bot`, `needs.database: asi-faq_db / managers-bot_db` (СОВПАДЕНИЕ с существующими БД/ролями — AC4), `needs.domain: faq/managers.asiteam.ru`, monitoring-pilot, quality=baseline, llm (managers: profile premium + overrides models=[chat-zai]); `make project-sync-env` → .env.platform + AI-PLATFORM.md перегенерированы |
| TASK-4: compose_service_contract.py + 3 L1-чека в verify_contracts.py + gate-тесты | ✅ done | `core/internal/shared/compose_service_contract.py` (RULE_* ×3, `ServiceContractInput`, `analyze_service_contracts`, `load_env_keys`, `load_provides` — реюс `compose_profiles.resolve_infra_path`); verify_contracts +3 L1 (contract_id=rule, KLASS_L1); gates: `test_gate_service_network_coverage` 4/4 + `test_gate_template_ai_project_networks` 1/1; регрессы: test_verify_contracts 50/50, test_on_project_deploy 20/20 |
| TASK-5: K1-зеркало (compose-service-networks) | ✅ done | practices_manifest.yaml entry (L1, baseline, local/ci); handler `check_compose_service_networks` в checks/compose.py (делегирует в ЕДИНСТВЕННЫЙ анализатор — dual-consumer); реестр 18→19; `tests/unit/test_check_compose_networks.py` 4/4 (R5-негатив: точный инцидентный вход `${DATABASE_URL}`) |
| TASK-6: verb parity-db | ✅ done | `core/internal/deploy/parity_db.py` (DI runner/resolve_host, ssh root@host → docker exec postgres psql, idempotent create/drop, DSN 1 строка stdout, пароль не в логах) + `core/entrypoints/parity-db.sh` + `makefiles/deploy.mk` target + generated-каскад (entrypoint-manifest allowed_verbs idx 42, glossary AGENTS.md:129, canon_table core/AGENTS.md:28) + `tests/unit/test_parity_db.py` 5/5 |
| TASK-8: легализация template-ai-project в scaffold-канале | ✅ done | choices {frontend, backend, ai-project}; gen_ai_platform_yaml: monitoring-ветка ai-project + needs.database default=name; template-manifest entry (consumer честный); `tests/unit/test_scaffold_ai_project.py` 3/3; templates-check exit 0; регрессы test_project_scaffolder 13/13; **E2E-смоук AC6 (QA F-2, 2026-08-31)**: `python3 -m core.internal.scaffold.project_scaffolder --name w7-smoke --template ai-project --projects-root <tmp> --node asi-team-vps` (CI_MODE=1, без --dry-run) → полный ai-platform.yaml (name=w7-smoke, needs.database=w7-smoke, monitoring metrics 8787/7d, quality=auto), compose 4 сети + DATABASE_URL=${PLATFORM_POSTGRES_DSN}, practices.lock (baseline/auto) + AI-PLATFORM.md + .env.platform (DSN w7-smoke_db); сгенерированный compose проходит shared-анализатор (0 violations, реальные SoT provides + env-ключи) — артефакты AC6 подтверждены; main() exit=1 на шаге 5b ai-instructions sync → Отклонение №6 |
| TASK-7: редеплой пилотов на ноду | ⛔ BLOCKED | нода asi-team-vps **голая** (доказательства ниже); редеплой невозможен до bootstrap |

## ⛔ TASK-7 BLOCKED — доказательства (read-only SSH, 2026-08-31)

```console
$ ssh asi-team-vps "which docker; ls /usr/bin/docker* /usr/local/bin/docker* 2>/dev/null"
(пусто — docker не установлен)

$ ssh asi-team-vps "hostname; ls /opt/ 2>/dev/null; systemctl status docker --no-pager 2>&1 | head -3"
asi-team-vps
(ls /opt/ пусто)
Unit docker.service could not be found.
```

- `/opt/` не существует (пусто) → платформа на ноде НЕ бутстрапилась (нет core/, node-configs, docker-стека).
- Модули postgres/pgbouncer и litellm на ноде не существуют → probe pgbouncer/litellm и
  hook-конвергенция БД не имеют целевой инфраструктуры (AC1 «контейнер на ноде резолвит
  pgbouncer:6432» невыполним без bootstrap).
- Фактура: сессии 169/177/178 подтверждали «оба сервера голые»; tronyx-vps бутстрапнут,
  asi-team-vps — нет (bootstrap остался за рамками 019).

**Закрытие TASK-7 требует (вне скоупа кода 019):**
1. `make bootstrap-node NODE=asi-team-vps` (инициатива владельца; ~30 мин; нужны asi-ключи).
2. Затем редеплой: `python3 -m core.internal.deploy.orchestrator_cli deliver --project asi-faq
   --project-dir projects/asi-group/client-bot --host <77.233.221.129>` (и managers-bot) —
   явное --project=имя манифеста (make-обёртка передаёт basename — для client-bot это client-bot ≠ asi-faq).
3. `make render-vhosts NODE=asi-team-vps` (faq.asiteam.ru, managers.asiteam.ru; wildcard
   *.asiteam.ru — прецедент login.asiteam.ru) + `make verify-domains`.
4. Hook-конвергенция БД: needs.database=asi-faq_db/managers-bot_db → already-exists skip
   (лог `[IMP:8][on_project_deploy][skip] database already exists`), роли конвергируются
   (ensure-convergence), `.platform-db.env` регенерируется.
5. Probe: `make project-status NAME=asi-faq NODE=asi-team-vps` + healthcheck.

## Верификация (перед коммитами)

| Проверка | Результат |
|----------|-----------|
| `make check TEST_FILE=tests/unit/test_check_compose_networks.py` | PASS 4/4 |
| `make check TEST_FILE=tests/unit/test_practices_check_project.py` (K1-регресс) | PASS 24/24 |
| `make check TEST_FILE=tests/gates/test_gate_practices_manifest.py` | PASS 8/8 |
| `make check TEST_FILE=tests/unit/test_parity_db.py` (субагент) | PASS 5/5 |
| `make check TEST_FILE=tests/unit/test_scaffold_ai_project.py` (субагент) | PASS 3/3 |
| `make check TEST_FILE=tests/gates/test_gate_service_network_coverage.py` (субагент) | PASS 4/4 |
| `make check TEST_FILE=tests/gates/test_gate_template_ai_project_networks.py` (субагент) | PASS 1/1 |
| `make check TEST_FILE=tests/unit/test_verify_contracts.py` (субагент) | PASS 50/50 |
| `make templates-check` | exit 0 |
| `pre-commit run --all-files` | 23 Passed / 0 Failed |
| `doxygen Doxyfile` | 0 warnings (DevPlan 097) |
| `docker compose --env-file .env.platform config` (оба пилота) | валиден; DSN интерполирован |
| Collateral: workflow-пины перепинены 4e623c1→2419325 (fix(018) W7 D5 изменил deploy-project.yml 2026-08-31; гейт test_gate_workflow_sha_pins) | Done |

### Финальная верификация (после коммитов 92009e1 + 67cd84e)

| Проверка | Результат |
|----------|-----------|
| `make check` (полный батч, all markers) | **All checks PASS** (включая manifests_up_to_date после коммита) |
| `make agent-check` | **clean=True** (blocking=0; advisory C901 — чужой файл параллельной сессии 020) |
| `pre-commit run --all-files` | 23 Passed / 0 Failed |
| `doxygen Doxyfile` | 0 warnings |
| `test_secrets_env_parser_benchmark` (solo, после xdist-флака 53ms>50ms) | PASS 2/2 — тайминг-флак под нагрузкой, не регрессия |

## Отклонения от плана (зафиксированы)

1. **$TEST_SPEC пути** `tests/unit/deploy/…`, `tests/unit/scaffold/…`, `tests/unit/practices/…` —
   фактическая конвенция репо: `tests/unit/` плоский (`test_parity_db.py`, `test_scaffold_ai_project.py`,
   `test_check_compose_networks.py`). Содержимое — 1:1 по $TEST_SPEC.
2. **needs.database** пилотов = **имя БД** (`asi-faq_db`/`managers-bot_db`), а не `true` —
   postgres-хук делает `CREATE DATABASE "<needs.database>"`; булево true создало бы БД «true».
   План-запись `database: true` — сокращение «флаг объявлен».
3. **managers-bot compose service** переименован `asi-managers` → `managers-bot` (контракт
   U-37 service=project_name; без этого deploy managers-bot падал на pull/up). Image не менялся.
4. **Точечный репин workflow-пинов** (collateral): pre-existing дрейф от fix(018) W7
   (4e623c1→2419325, фактический пин channel_pin.py), repair по рецепту гейта.
5. TASK-7 — BLOCKED, не partial (факты выше).
6. **E2E-смоук AC6 (QA F-2): main() exit=1 на шаге 5b** — `scaffold_instructions` (DevPlan 001
   T5.2) гоняет `ai-instructions sync --template ai-project`, а компилятор (vendor/ai_instructions)
   принимает ТОЛЬКО {all, backend, frontend} → «invalid choice: 'ai-project'». Все AC6-артефакты
   (ai-platform.yaml, compose, practices.lock, AI-PLATFORM.md, .env.platform) сгенерированы ДО
   этого шага и верифицированы (0 violations анализатора на сгенерированном compose) — канал
   скаффолда легален, W7 T0 разблокирован на уровне артефактов. Полный exit 0 требует добавления
   ai-project в choices `--template` компилятора (правильный фикс — vendor/ai_instructions или
   passthrough-режим; вне скоупа 019, зафиксирован как debt). Смоук-попытка честно зафиксирована:
   unit 3/3 + templates-check + smoke (артефакты) — цепочка scaffold→practices.lock→AI-PLATFORM.md
   для ptype=ai-project впервые прогнана end-to-end.

## Выводы

- Код-рубежи инцидента 019 закрыты: шаблон/пилоты (сети+DSN), K1+K3-гейты класса
  (один анализатор — два канала), parity-db verb, легализация scaffold-канала.
- Операционное закрытие (TASK-7) — после bootstrap ноды asi-team-vps (решение владельца).
- Rev: первым шагом нового агента — `make bootstrap-node NODE=asi-team-vps`, затем deliver
  с явным --project (см. «Закрытие TASK-7»).
