# 128-debt-python-refactor — 02-DevPlan.md

$START_DEVPLAN

$ARTIFACT_CONTRACT
PURPOSE:               Исполнить 11 задач из 01-Brief (128): дедупликация docker-операций (P2-5/D6), фикс D3, синхронизация doc_header_validator (D1/D2), извлечение 3 inline-python3 (D7), мелкие фиксы D8/D10/D12-hc/nginx-dual/manifest.mk, снятие stale-долгов (jsonschema HI, D9).
DESCRIPTION:           5 волн (см. Brief): W1 docker_ops shared; W2 D3; W3 doc_header_validator; W4 D7; W5 мелкие фиксы + stale-снятие. Каждая волна: реализация + unit-тесты + make check.
RATIONALE:             См. Brief 128 RATIONALE. Единый docker-слой устраняет дрейф 3 копий; манифест = SoT (инвариант 5); inline python3 — нарушение языковой политики.
ACCEPTANCE_CRITERIA:   См. Brief 128 (6 пунктов). Дополнительно: (7) ни один существующий тест не сломан (make test-summary MARKER=static_audit зелёный). (8) Гейт на единственный docker-слой зарегистрирован в entrypoint-manifest.yaml (секция gates) с @pytest.mark.gate.
IMPLEMENTS:            01-Brief.md (128).
IMPACTS:               См. Brief IMPACTS.
REQUIRES:              Бейзлайн make check зелёный; решение пользователя (выполнено).
$END_ARTIFACT_CONTRACT

## 0. Draft Code Graph (XML)

```xml
<graph>
  <entity name="core_internal_shared_docker_ops_py" TYPE="MODULE"
    keywords="docker,shared,dedup,ps,inspect,compose,exec"
    annotation="Единый слой docker-операций: docker_ps/docker_inspect/docker_exec/compose-обёртки. Потребители: deploy_engine, docker_orchestrator, lib/docker.sh (shell-фасад через python3 -m ... --shell)."
    CrossLinks="core/internal/deploy/deploy_engine.py; core/internal/bootstrap/deploy/docker_orchestrator.py; core/lib/docker.sh"/>
  <entity name="core_internal_lint_doc_header_validator_py" TYPE="MODULE"
    keywords="manifest-sync,namespace-collision,check-names"
    annotation="D1/D2: удалить нереализуемые имена из манифеста/описаний; namespace_collision_names — реализовать ИЛИ удалить из манифеста."
    CrossLinks="core/entrypoint-manifest.yaml"/>
  <entity name="core_internal_scaffold_project_adopter_py" TYPE="MODULE"
    keywords="gen-env,cli-first,json-analysis,duplicate-domain"
    annotation="D7/D8: gen_env_platform импортируемый; generate-catalog/adopt-project JSON/add-vhost duplicate domain — Python-функции."
    CrossLinks="core/internal/scripts/gen_env_platform.py; core/internal/catalog/generate-catalog.sh; core/internal/scaffold/adopt-project.sh; core/internal/scaffold/add-vhost.sh"/>
</graph>
```

## 1. Data Flow (шаг за шагом)

```
W1 ── инвентаризация docker-операций (deploy_engine.py:81 TRAP[DEBT], docker_orchestrator.py,
     lib/docker.sh) ─► shared/docker_ops.py (чистые функции + CLI --shell) ─►
     потребители на shared ─► gate docker_sole_path ─► unit-тесты
W2 ── 5 test-side failures test_docker_orchestrator.py ─► фикс (после W1 — общий слой
     устраняет расхождения моков) ─► тесты зелёные
W3 ── doc_header_validator: сверка манифеста и кода (namespace_collision_names,
     check_file_lines, check_shellcheck_directives) ─► манифест = код ─► тесты
W4 ── 3 inline-python3 (generate-catalog heredoc, adopt-project JSON, add-vhost dup-domain)
     ─► Python-модули/функции ─► whitelist check-no-new-inline-python3.sh сокращается
W5 ── D8 (gen_env_platform: функция main() -> int + __main__), D10 (boto3 Config),
     D12-hc (postgres/healthcheck.sh параметризация), nginx-dual (консолидация),
     manifest.mk (снять dead-комментарий), jsonschema/D9 (снять TRAP[DEBT] как FIXED)
```

## 2. File Manifest

| Файл | Действие | Волна |
|------|----------|-------|
| `core/internal/shared/docker_ops.py` | создать | W1 |
| `core/internal/deploy/deploy_engine.py` | рефакторинг на shared | W1 |
| `core/internal/bootstrap/deploy/docker_orchestrator.py` | рефакторинг на shared | W1 |
| `core/lib/docker.sh` | фасад над `python3 -m ... --shell` | W1 |
| `tests/gates/test_gate_docker_sole_path.py` | создать (гейт) | W1 |
| `tests/unit/test_docker_ops.py` | создать | W1 |
| `tests/unit/test_docker_orchestrator.py` | фикс 5 failures | W2 |
| `core/internal/lint/doc_header_validator.py` | D1/D2 фикс | W3 |
| `core/entrypoint-manifest.yaml` | синхронизация (namespace_collision_names и др.) | W3 |
| `core/internal/scripts/gen_env_platform.py` | D8: импортируемый main | W5 |
| `core/internal/scaffold/project_adopter.py` | D8 вызов без subprocess | W5 |
| `core/modules/backup-cron/scripts/s3_client.py` | D10: boto3 Config | W5 |
| `core/modules/postgres/healthcheck.sh` | D12-hc: параметризация имён | W5 |
| `core/modules/nginx/config/nginx.conf` | dual-mechanism: консолидация/keep | W5 |
| `makefiles/manifest.mk` | снять dead-комментарий | W5 |
| `core/internal/scripts/jsonschema_validate.py` | снять TRAP[DEBT] (FIXED RC) | W5 |
| `core/internal/hooks/check-no-new-inline-python3.sh` | whitelist-сокращение (D7) | W4 |
| `core/internal/catalog/generate-catalog.sh` + python-модуль | D7 heredoc → Python | W4 |
| `core/internal/scaffold/adopt-project.sh` (+ python) | D7 JSON → Python | W4 |
| `core/internal/scaffold/add-vhost.sh` (+ python) | D7 dup-domain → Python | W4 |

## 3. Волны

### W1 — shared/docker_ops.py (P2-5/D6)
1. Инвентаризация: docker-операции в deploy_engine.py, docker_orchestrator.py,
   lib/docker.sh (docker ps/inspect/exec/compose up-down/network).
2. `core/internal/shared/docker_ops.py`: чистые функции (типизированные), CLI
   `python3 -m core.internal.shared.docker_ops --shell` для shell-потребителей
   (паттерн ssh_opts 116 B5 D1).
3. Потребители переводятся на shared; lib/docker.sh — тонкий фасад.
4. Гейт `test_gate_docker_sole_path.py`: docker-команды (docker ps/inspect/exec)
   вне shared/docker_ops.py → RED (allowlist пуст); регистрация в manifest (gates).
5. Unit-тесты test_docker_ops.py (mock subprocess, LDD IMP:9).

**Acceptance W1:** 0 дубликатов docker-операций; гейт зелёный; unit-тесты.

### W2 — D3: 5 test-side failures
1. Прогнать tests/unit/test_docker_orchestrator.py — зафиксировать 5 failures.
2. После W1 общий слой упрощает моки — фиксить тесты (не бизнес-логику) к
   контракту shared-слоя.
3. Regression: make test-summary TEST_FILE=tests/unit/test_docker_orchestrator.py.

**Acceptance W2:** 0 failures в test_docker_orchestrator.py; TRAP[DEBT] D3 снят.

### W3 — doc_header_validator (D1/D2)
1. Сверка: namespace_collision_names (doc_header_validator.py:479) — решить:
   реализовать (валидация коллизий имён в doc-хедерах) ИЛИ удалить из манифеста.
   Решение по принципу Small Simple Blocks: реализовать, если ≤30 LOC и тестируемо;
   иначе удалить из манифеста (манифест обязан отражать код).
2. check_file_lines/check_shellcheck_directives (doc_header_validator.py:52) —
   имена не существуют в коде: удалить из Brief-описаний/манифеста.
3. Тесты на синхронизацию (расширить существующий тест doc_header_validator).

**Acceptance W3:** манифест и код совпадают; TRAP[DEBT] D1/D2 сняты.

### W4 — D7: 3 inline-python3 извлечения
1. `generate-catalog.sh` heredoc → Python-модуль (каталог-генератор) + вызов.
2. `adopt-project.sh` complex JSON analysis → функция в project_adopter (или
   shared/yaml_json.py) + вызов.
3. `add-vhost.sh` duplicate domain check → Python-функция (vhost_renderer или
   shared) + вызов.
4. check-no-new-inline-python3.sh: whitelist-записи удаляются (по одной на волну —
   hook-логика не меняется).

**Acceptance W4:** whitelist пуст (3 записи закрыты); hook не находит нарушений.

### W5 — Мелкие фиксы + stale-снятие
1. D8: gen_env_platform.py — `main() -> int` + `if __name__ == "__main__"`;
   project_adopter вызывает функцию напрямую (убрать subprocess ~100ms overhead).
2. D10: s3_client.py — boto3 Config(connect_timeout, read_timeout) из
   shared/timeouts.py (единый реестр таймаутов).
3. D12-hc: postgres/healthcheck.sh — имена контейнеров через переменные
   (COMPOSE_PROJECT/суффикс), пригодность для -test stack.
4. nginx-dual: config/ vs dev-config/ — консолидировать (dev-config → config
   параметризация) ИЛИ задокументировать явный keep (решение по объёму).
5. manifest.mk: снять TRAP[DEBT] dead-комментарий (generate-manifests-atomic
   удалён 118 B4).
6. jsonschema_validate.py: TRAP[DEBT] HI → снять как FIXED (python_deps.py Step 1b,
   RC-сессия 2026-08-03; requirements-комментарий в sync_requirements.py уже
   отражает). D9: снять (shared/project_yaml.py, 118 E11).

**Acceptance W5:** все 11 задач закрыты; TRAP[DEBT] сняты; make check + gate.

## 4. Критерии приёмки волн — сводка

| Волна | Критерий |
|-------|----------|
| W1 | docker_ops shared + гейт docker_sole_path + тесты |
| W2 | test_docker_orchestrator 0 failures |
| W3 | doc_header_validator манифест=код |
| W4 | 3 inline-python3 извлечены, whitelist пуст |
| W5 | 6 мелких фиксов + 2 stale-снятия, check+gate зелёные |

## 5. Риски и митигации

| Риск | Митигация |
|------|-----------|
| W1: сломать docker-вызовы в deploy (прод-деплой) | Байт-совместимые обёртки; полный static_audit прогон; e2e deploy на test-VPS |
| W3: namespace_collision_names важен для других доменов | Сначала grep потребителей; если реализация >30 LOC — удалить из манифеста с TRAP-записью о причине |
| W4: adopt-project JSON-анализ сложный | Выделить чистые функции с data-driven фикстурами (SWE-эвристика, §TESTING) |

$END_DEVPLAN
