# $ARTIFACT_CONTRACT
## @PURPOSE Закрытие тестового долга после Strangler-Fig миграций + верификация E2E теста 095
## @DESCRIPTION
Два компонента тестового долга, выявленные при аудите:

**Подзадача 1: 3 Strangler-тест-регрессии из 038 06-VR**
- `test_deploy_snapshot.py` — функции удалены в Wave 5e, тесты остались
- `test_gate_compose_profiles_consistency` — затронут adopt-project миграцией
- Gate-тест (не уточнён в VR)
- Задача зарегистрирована в 038 06-VR: `task(subagent_type="Plan", description="Fix Strangler-Fig test regressions")` — не выполнена

**Подзадача 2: E2E тест 095 — верификация реализации**
- DevPlan 095 (`095-e2e-bootstrap-pipeline-test`) — план верифицирован (03-VR pre-implementation)
- Статус реализации НЕ подтверждён
- Нужно: проверить наличие тестового файла, запустить, верифицировать результат

**Подзадача 3: python_deps.sh inline fix (P2, SMALL)**
- `core/lib/python_deps.sh:22` — последний `python3 -c "import ${module}"` во всём проекте
- Формальное нарушение языковой политики (Tier 1 триггер)
- Решение: заменить на вызов `python3 -c "from importlib.util import find_spec; ..."` и добавить `@rationale` о sanctioned exception, либо вынести в `.py` файл
## @RATIONALE
- Тестовые регрессии — риск маскировки реальных багов
- E2E тест — критичен для верификации bootstrap pipeline после всех миграций
- python_deps.sh — последний красный флаг языковой политики
## @ACCEPTANCE_CRITERIA
- AC1: 3 тестовые регрессии из 038 06-VR исправлены (тесты проходят или обновлены под новый API)
- AC2: E2E тест 095: файл существует, маркер зарегистрирован, `make test MARKER=e2e` проходит
- AC3: `python_deps.sh:22` — inline `python3 -c` заменён на sanctioned вызов или добавлен `@rationale`
- AC4: `make test MARKER=all` — все тесты зелёные (0 failures)
- AC5: `make gate MODE=fast` зелёный
- AC6: `make test-inventory-sync` — инвентарь актуален
## @IMPLEMENTS Brief 110
## @IMPACTS tests/test_deploy_snapshot.py, tests/gates/test_gate_compose_profiles_consistency.py, tests/e2e/test_e2e_bootstrap.py (проверить), core/lib/python_deps.sh, tests/test_inventory.yaml
## @REQUIRES Результаты всех миграционных брифов (099-109) для актуального тестирования
