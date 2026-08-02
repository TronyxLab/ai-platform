# 05-DevPlan — Бриф D: монолит-декомпозиция (Python-архитектура)

<!-- $ARTIFACT_CONTRACT
PURPOSE:          Декомпозиция крупнейших Python-модулей (топ-20, 4 файла >1000 LOC) после Strangler-миграции 117; очистка shared/ от реликтов.
DESCRIPTION:      7 задач: D1 docker_orchestrator, D2 node_yaml миксины, D3 shared-чистка, D4 env-requires, D5 github_ops, D6 context_deployer god-function, D7 generate_platform_env codegen.
RATIONALE:        Монолиты = источник дрейфа: каждая правка затрагивает несвязанные темы, приватные методы переходят границы модулей,
                  конкурирующие реализации параллелизма/healthcheck. Декомпозиция — чистые экстракции без смены контрактов.
ACCEPTANCE_CRITERIA:
  - AC-D1: docker_orchestrator разделён (parallel_runner, healthcheck_runner, hermes_workflow); оркестратор <900 LOC; 0 конкурирующих реализаций параллелизма.
  - AC-D2 (опционально): node_yaml декомпозирован по поддоменам; API .get() не изменён; 831 вызовов не сломаны.
  - AC-D3: age_key.py удалён (decrypt_secrets переведён на node_detect); ssh_command_parser перемещён к потребителю; deploy_paths — в C7.
  - AC-D4: единый env-requires-чекер; validate_module_yaml и secrets_validator делегируют; 0 расхождений вердиктов.
  - AC-D5: create_github_repo единственный (github_ops); project_scaffolder делегирует.
  - AC-D6: deploy_context разбит на шаги с typed-контрактами (после A5); god-function <300 LOC.
  - AC-D7 (опционально): codegen через jinja-шаблоны вместо f-string; поведение не изменено.
  - AC-D8: gate MODE=fast, check-manifests, ruff — зелёные; 0 regressions в тестах затрагиваемых модулей.
IMPLEMENTS:       118 01-Brief задачи D1-D7.
IMPACTS:          core/internal/bootstrap/deploy/{docker_orchestrator.py,context_deployer.py}, core/internal/shared/node_yaml.py,
                  core/internal/scripts/generate_platform_env.py, core/internal/scaffold/{project_scaffolder.py,github_ops.py},
                  core/internal/{secrets/decrypt_secrets.py,bootstrap/deploy/secrets_validator.py,scripts/validate_module_yaml.py}, tests/.
REQUIRES:         118 01-Brief; D6 после A5 (importlib-фикс); D3 частично в C7 (deploy_paths).
-->

---

## 1. Технический анализ и решения

### D1 (MED) — docker_orchestrator.py (1401 LOC)

**Факты (аудит):** 7 тем в одном файле: compose build/up деплой, fork-параллелизм + drain (888-1070), atomic rollback группы (943-968), cleanups (legacy hermes, observability), pre-pull, `_handle_hermes_agent` (289-411, 123 LOC спец-workflow), healthcheck-инвокация ×3 (run_healthcheck/_invoke_healthcheck[_full], 1112-1260), аудит, CLI. `deploy_docker_group` (876-1032) — отдельная подсистема (parallel scheduling + rollback + healthcheck). Дублирует drain-логику (`_drain_completed_count`/`_drain_all_count`).

**Решение (чистые экстракции, без смены контрактов):**
1. `bootstrap/deploy/parallel_runner.py` — fork/slot-waiter/drain (888-1070) + `deploy_docker_group`.
2. `bootstrap/deploy/healthcheck_runner.py` — healthcheck-инвокации (1112-1260) → делегирует в shared/module_interface (C5).
3. `bootstrap/deploy/hermes_workflow.py` — `_handle_hermes_agent` (289-411).
4. Оркестратор остаётся: роутинг модулей + CLI.

**Тест:** существующие test_deploy_orchestrator + новые unit на parallel_runner (drain, rollback).

**Риск:** MED — файл чаще всех правится; экстракции по одной (strangler внутри файла), каждая с зелёным гейтом.

### D2 (MED, ОПЦИОНАЛЬНО) — node_yaml.py (1512 LOC)

**Факты:** 31 публичный метод покрывает 12 поддоменов схемы; 831 внешних вызовов `.get()`; мутации задублированы через project_registry (тонкий мост); `_write_back` — 3-я реализация atomic-write в кодовой базе.

**Решение:** миксины по поддоменам (`node_yaml/domains.py`, `node_yaml/secrets.py`, `node_yaml/firewall.py`, ...) без смены сигнатур; `NodeYaml` — тонкий кэш-агрегатор. `_write_back` → `shared/atomic_writer.py`.

**Условие включения:** выполняется ТОЛЬКО если брифа D остаётся время после D1/D3-D6; иначе — DEBT-запись (риск: 831 .get() — высокая цена при низком текущем дрейфе).

**Тест:** весь существующий test_node_yaml (регрессионный набор большой).

**Риск:** HIGH (831 call-site). Отмечен как откладываемый.

### D3 (LOW) — shared-чистка реликтов

**Факты (верифицированы):**
- `shared/age_key.py` — compat-шим после миграции в node_detect (DevPlan 104); потребитель — `decrypt_secrets.py` через sys.path-хак (`from n import detect_n`).
- `shared/ssh_command_parser.py` (279) — 1 прод-потребитель (orchestrator_cli); не дотягивает до ≥2.
- `shared/deploy_paths.py` — 0 прод-потребителей (пересечение с C7).

**Решение:** age_key → удалить, decrypt_secrets перевести на node_detect (убрать sys.path-хак). ssh_command_parser → перенести в `deploy/` рядом с потребителем (или задокументировать как специальный). deploy_paths — решается в C7.

**Тест:** decrypt_secrets unit после перехода; 0 ссылок на age_key.

**Риск:** LOW.

### D4 (MED) — единый env-requires-чекер

**Факты (верифицированы):** `validate_module_yaml.py:327` `check_env_requires_presence` (module.yaml-driven) и `secrets_validator.py:75` `check_env_requires` (manifest-driven) — два валидатора одной сущности, разные семантики → расходящиеся вердикты.

**Решение:** единый чекер в `shared/env_requires.py` (объединить обе семантики: module.yaml requirements vs secrets-manifest наличие). Оба валидатора делегируют; вердикты совпадают.

**Тест:** negative-тест на расхождение (модуль с requirement, которого нет в manifest → оба валидатора одинаково).

**Риск:** MED — проверить существующие ожидания тестов (test_validate_module_yaml, test_secrets_validator) до объединения.

### D5 (MED) — github_ops дубль

**Факты (верифицированы):** `project_scaffolder.py:357` `create_github_repo(org, name, project_dir, dry_run)` — дубль `github_ops.py:27` с той же сигнатурой.

**Решение:** project_scaffolder делегирует в github_ops; локальная реализация удаляется. Проверить расхождения тела (api-key source, gh vs curl).

**Тест:** new-project dry-run тест + unit github_ops.

**Риск:** LOW-MED.

### D6 (MED) — context_deployer god-function

**Факты:** `deploy_context` (606-735) выполняет 6 подсистем: extract context, cert orchestration, project deploy, vhost render (bash), nginx reload (docker exec), verify (bash). `_render_and_provision_llm` (521-549), `_ensure_bootstrap_compose` (398). После A5 (importlib-фикс) остаётся god-function.

**Решение:** разбить `deploy_context` на шаги с typed-контрактами (`_step_certs`, `_step_deploy_projects`, `_step_vhosts`, `_step_nginx_reload`, `_step_verify`) — по одному методу на инфраструктуру. `_render_and_provision_llm` → в `llm/`. nginx reload → shared/docker_compose-фасад.

**Тест:** существующий test_context_deployer + per-step unit.

**Риск:** MED (после A5 — чистая реструктуризация, контракты шагов типизированы).

### D7 (LOW, ОПЦИОНАЛЬНО) — generate_platform_env codegen

**Факты:** строки 331-425 генерируют исполняемый Python-код (smoke_env_generated.py, env_defaults_generated.py) f-string'ами — хрупко, не grep-able.

**Решение (опционально):** jinja-шаблоны в `generated_sources/` вместо f-string. Поведение идентично (byte-compare гейт check-env-defaults подтверждает).

**Условие включения:** только при остатке времени; иначе DEBT.

**Тест:** существующий test_generate_platform_env + byte-compare.

---

## 2. Порядок выполнения

```
D3 (shared-чистка)     ← дёшево, независимо
   │
D5 (github_ops)        ← дёшево, независимо
   │
D4 (env-requires)      ← проверить тесты ДО
   │
D1 (docker_orchestrator) ← крупный, по одной экстракции
   │
D6 (context_deployer)  ← после A5
   │
D2 / D7 (опционально)  ← только при остатке времени, иначе DEBT
```

## 3. Оценки

| Метрика | Значение |
|---------|----------|
| Задач | 7 (2 опциональные) |
| LOC | −200…−400 (выносы не уменьшают общий LOC, но уменьшают монолитность) |
| Рискованных | D2 (831 .get() — откладывается), D4 (двойная семантика) |
| Зависимости | D6 ← A5, D3 ∩ C7 |

## $END

Открытые вопросы:
1. **D2/D7** — включать ли в волну или зафиксировать как DEBT (решение по факту времени после D1/D3-D6).
2. **D1** — порядок экстракций: parallel_runner первым (самый большой риск-снятие) или healthcheck_runner (вход для C5)?
