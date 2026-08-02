# 09-Debt.md — Волна 118 Бриф D: отложенные задачи D2/D7

<!-- $ARTIFACT_CONTRACT
PURPOSE:          Зафиксировать отложенные задачи Брифа D (DevPlan 05): D2 (node_yaml миксины) и D7 (jinja codegen), не выполненные в волне 118 по бюджету.
DESCRIPTION:      D2 — декомпозиция node_yaml.py (1164 LOC) на миксины по поддоменам (риск 831 .get() вызовов, HIGH). D7 — замена f-string codegen (generate_platform_env.py:331-425) на jinja-шаблоны (LOW, byte-compare риск).
RATIONALE:        DevPlan 05 §D2/§D7: «выполняется ТОЛЬКО если брифа D остаётся время после D1/D3-D6; иначе — DEBT-запись». Бюджет волны израсходован на D1 (крупная экстракция docker_orchestrator → 3 модуля) + D3-D6 + R5 negative-тесты.
ACCEPTANCE_CRITERIA:
  - D2 перенесён на 119: node_yaml миксины с сохранением API .get() (831 вызова не сломаны)
  - D7 перенесён на 119: jinja-шаблоны в generated_sources/, byte-compare гейт check-env-defaults подтверждает
IMPLEMENTS:       118 01-Brief задачи D2/D7 (отложены).
IMPACTS:          core/internal/shared/node_yaml.py, core/internal/scripts/generate_platform_env.py.
REQUIRES:         Нет.
-->

---

## 1. D2 (MED, ОПЦИОНАЛЬНО) — node_yaml.py (1512→1164 LOC) миксины по поддоменам

**Статус:** ОТЛОЖЕН на волну 119.

**Почему DEBT:** DevPlan 05 §D2 условие включения: «выполняется ТОЛЬКО если брифа D остаётся время после D1/D3-D6; иначе — DEBT-запись (риск: 831 .get() — высокая цена при низком текущем дрейфе)». Бюджет волны 118 израсходован на D1 (1397-LOC docker_orchestrator → parallel_runner + healthcheck_runner + hermes_workflow, −470 LOC монолитности) + D3 (age_key удалён, ssh_command_parser перенесён) + D4 (единый env-requires чекер) + D6 (context_deployer god-function → 5 typed-шагов) + R5 negative-тесты на каждое удаление.

**Что делать на 119:**
- `node_yaml/domains.py`, `node_yaml/secrets.py`, `node_yaml/firewall.py`, ... — миксины по 12 поддоменам схемы
- `NodeYaml` — тонкий кэш-агрегатор (без смены сигнатур, API `.get()` НЕ меняется)
- `_write_back` → `shared/atomic_writer.py` (устранить 3-ю реализацию atomic-write в кодовой базе)
- Тест: весь существующий test_node_yaml (регрессионный набор большой)
- Риск: HIGH (831 call-site) — проверить verify-then-delete

**Rev:** 2026-08-02 — волна 118 closed; условие включения не выполнено (время израсходовано на D1).

## 2. D7 (LOW, ОПЦИОНАЛЬНО) — generate_platform_env codegen: f-string → jinja

**Статус:** ОТЛОЖЕН на волну 119.

**Почему DEBT:** DevPlan 05 §D7 условие включения: «только при остатке времени; иначе DEBT». D7 — косметическое улучшение (строки 331-425 генерируют исполняемый Python-код f-string'ами — «хрупко, не grep-able»), но с риском: byte-compare гейт check-env-defaults сверяет сгенерированные файлы байт-в-байт, любой пробел в jinja-шаблоне ломает check-manifests. Стоимость (jinja2 в шаблонах + регрессия byte-compare) не оправдана при зелёном текущем состоянии codegen.

**Что делать на 119:**
- jinja-шаблоны в `generated_sources/` (smoke_env_generated.py.j2, env_defaults_generated.py.j2)
- `generate_smoke_env_py` / `generate_helpers_py` — рендер через jinja2.Template
- Поведение идентично — byte-compare гейт check-env-defaults подтверждает (AC-D7)
- Тест: существующий test_generate_platform_env + byte-compare

**Rev:** 2026-08-02 — волна 118 closed; кодgen стабилен, риск регрессии byte-compare перевесил косметическую выгоду.

---

## Сводка волны 118 Бриф D

| Задача | Статус | Примечание |
|--------|--------|-----------|
| D1 docker_orchestrator → parallel_runner/healthcheck_runner/hermes_workflow | ✅ | Оркестратор 1397→927 LOC, 0 конкурирующих реализаций параллелизма |
| D2 node_yaml миксины | ⏸ DEBT | 831 .get() — HIGH риск, перенесён на 119 |
| D3 shared-чистка (age_key, ssh_command_parser, deploy_paths) | ✅ | age_key удалён; ssh_command_parser → deploy/; deploy_paths — C7 (готово) |
| D4 единый env-requires чекер | ✅ | shared/env_requires.py, оба валидатора делегируют, R5 negative-тест |
| D5 github_ops дубль | ✅ | Уже lazy-facade с 117 (T58.1); добавлен R5 negative-тест на отсутствие дубля |
| D6 context_deployer god-function → шаги | ✅ | deploy_context → _step_certs/_step_deploy_projects/_step_vhosts/_step_nginx_reload/_step_verify; nginx reload → shared/docker_compose.nginx_reload |
| D7 jinja codegen | ⏸ DEBT | LOW, byte-compare риск; перенесён на 119 |
