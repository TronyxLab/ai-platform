# $ARTIFACT_CONTRACT
## @PURPOSE Дедупликация entrypoints + re-enable pre-push-gate.sh
## @DESCRIPTION
Две независимые подзадачи:

**Подзадача 1: Дедупликация `auto_detect_node_name()` и `detect_age_key()`**
- `auto_detect_node_name()` — идентична в `bootstrap.sh:71-86` и `converge.sh:54-77`
- `detect_age_key()` — идентична в `bootstrap.sh:56-69` и `node-update.sh:48-61`
- Обе имеют shell-fallback при наличии Python-модуля `shared/age_key.py` — нарушение single-source-of-truth
- Решение: вынести в `core/internal/shared/node_detect.py`, удалить дубликаты, убрать shell-fallback

**Подзадача 2: Re-enable pre-push-gate.sh**
- `core/entrypoints/pre-push-gate.sh` отключён 25 июля (`exit 0` всегда)
- Сегодня 31 июля — дата прошла, гейт мёртв
- Нарушение контракта `entrypoint-manifest.yaml` (заявлен как "Blocking pre-push gate")
- Решение: убрать `exit 0`, раскомментировать `make gate MODE=fast`
## @RATIONALE
- Дублирование в 3 entrypoints — violation принципа single-source-of-truth
- Shell-fallback при наличии Python-модуля — violation языковой политики
- Отключённый pre-push gate — риск пропуска битых коммитов в main
## @ACCEPTANCE_CRITERIA
- AC1: `core/internal/shared/node_detect.py` с `auto_detect_node_name()` и `detect_age_key()`
- AC2: `bootstrap.sh`, `converge.sh`, `node-update.sh` вызывают Python вместо shell-функций
- AC3: Shell-fallback удалён (Python — single source of truth)
- AC4: `bootstrap.sh` ≤ 170 LOC, `converge.sh` ≤ 100 LOC, `node-update.sh` ≤ 100 LOC
- AC5: `pre-push-gate.sh` — убран `exit 0`, раскомментирован `make gate MODE=fast`
- AC6: `make bootstrap-node`, `make converge`, `make node-update` работают идентично
- AC7: `make gate MODE=fast` зелёный
## @IMPLEMENTS Brief 104
## @IMPACTS core/entrypoints/bootstrap.sh, core/entrypoints/converge.sh, core/entrypoints/node-update.sh, core/entrypoints/pre-push-gate.sh, core/internal/shared/node_detect.py (NEW), tests/unit/test_node_detect.py (NEW)
## @REQUIRES Ничего
