# 08-Brief — B7: Модульный контракт

<!-- GREP_SUMMARY: module-contract restore restart module.mk backup state.json nginx configs pyproject volume-rename -->
<!-- STRUCTURE: ┌scope┐ → ◇ make-контракт → ◇ конфиги → ◇ зависимости → ⊕ критерии → ⎋ зависимости -->
# region MODULE_CONTRACT
## @purpose  Волна B7: починить контракт модулей — make-таргеты, конфигурации, зависимости, генерацию конфигов.
## @scope    U-25, U-46, U-50, U-61, U-62, U-65
## @invariants
##   - module.mk — единственный источник make-контракта модуля; документация (AGENTS.md) не расходится с кодом.
##   - Один механизм шаблонизации на директорию (правило template-механизмов).
# endregion MODULE_CONTRACT

$ARTIFACT_CONTRACT:
  PURPOSE: Привести контракт модулей в соответствие с документацией и реальностью.
  DESCRIPTION: Реализация restore-таргетов (11 модулей), исправление restart-семантики (recreate vs soft), удаление hermes-специфичного state.json из generic module.mk, консолидация nginx config/dev-config, декларация pyproject-зависимостей, легализация wiring monitoring_config_renderer.
  RATIONALE: core/modules/AGENTS.md:167 утверждает контракт, которого нет (restore без recipe; restart = recreate вопреки «без пересоздания»); generic-шаблон module.mk несёт hermes-specific логику; nginx-конфиги в двух полных копиях; httpx не объявлен в pyproject.
  ACCEPTANCE_CRITERIA: (1) restore реализован для всех docker-модулей (или контракт сужен + глоссарий правлен); (2) restart-семантика определена и задокументирована точно (restart vs restart-hard); (3) module.mk backup — параметризован (state.json только для hermes-agent); (4) nginx: один набор конфигов (config/) + dev-оверрайд без дублей; (5) pyproject: httpx добавлен, requests/python-dotenv перенесены в dev/test extra; (6) monitoring_config_renderer зарегистрирован в манифесте/makefile (или удалён module-hook с явной заменой); (7) паттерн volume-rename -test задокументирован как канон (или устранён через override-механизм).
  IMPLEMENTS: U-25 (restore/restart/backup + restart-поле), U-46 (nginx configs), U-50 (pyproject), U-61 (state.json), U-62 (volume-rename), U-65 (renderer wiring)
  IMPACTS: core/templates/module.mk, core/Makefile.common, core/modules/*/Makefile, core/modules/nginx/config+dev-config, pyproject.toml, core/entrypoint-manifest.yaml, core/modules/AGENTS.md
  REQUIRES: B4 (контракты), B8 (dead-code решения по renderer/фасадам)

---

## Scope

| U | Проблема | Ключевые файлы |
|---|----------|----------------|
| U-25 | .PHONY restore без recipe (11/13); restart: stop start = recreate; AGENTS.md:167 врёт; module.yaml restart поле 0/14 | templates/module.mk:62, Makefile.common:14, modules/AGENTS.md:167, postgres/Makefile:68, backup-cron/Makefile:70, module.schema.json:98-102 |
| U-46 | nginx config/ (10) vs dev-config/ (12): полные дубли; ssl-params в HTTP-only dev | core/modules/nginx/config/, dev-config/ |
| U-50 | httpx не объявлен (admin_client.py:26); requests/python-dotenv в runtime | pyproject.toml:35-36, llm/admin_client.py |
| U-61 | module.mk backup: хардкод docker cp :/app/state.json в generic-шаблоне | templates/module.mk:113-116 |
| U-62 | volume rename -test workaround скопирован в 5 модулей | postgres/test.yml:20-24, backup-cron:38-39, clickhouse:48, hermes-agent:52 |
| U-65 | monitoring_config_renderer: жив через module-hook, вне manifest/makefile; 19 pass-тестов | modules/monitoring/hooks/on-project-deploy.sh:39, entrypoint-manifest.yaml |

## Ключевые артефакты

1. restore: реализовать для 11 модулей (паттерн postgres/backup-cron: DUMP_FILE/restore из backup) ИЛИ сузить контракт в module.mk + AGENTS.md + глоссарий — решение архитектора, единообразно.
2. restart: определить семантику (restart = stop+start без recreate → исправить Makefile.common; restart-hard = --force-recreate); AGENTS.md:167 — точная формулировка.
3. module.mk backup: параметр BACKUP_SOURCE_FILE (default пусто); hermes-agent передаёт /app/state.json; WARNING-путь убран.
4. nginx: dev-config сжимается до оверрайдов (envsubst-переменные + ssl-dev.conf), полные копии удаляются; NGINX_CONF_DIR default → config/.
5. pyproject: httpx в runtime deps; requests/python-dotenv → [dev]; CI-гейт на импорты (import-check).
6. monitoring_config_renderer: регистрация таргета make render-monitoring + запись в entrypoint-manifest.yaml; 19 pass-тестов удаляются в B10 (или здесь — согласованно).
7. Volume-паттерн: канонизировать (документировать TRAP в module.mk-контракте) или заменить override-механизмом compose.

## Гейт самоверификации волны

- Гейт make-контракта: для каждого модуля `make -n restore/restart/backup` не падает (dry-run).
- Гейт модульного манифеста: каждый hook/модуль зарегистрирован (entrypoint-manifest + module.yaml).

## Зависимости

- От: B4 (контракты), B8 (renderer-решение).
- К: B10 (тесты модульных Makefile), B11 (глоссарий правки).
