<!-- GREP_SUMMARY: cicd, log-streaming, smoke-hang, IMP-tracing, LDD, run_cmd, subprocess-io, check-suite, test-runner, agent-check, localize, iteration, bisect, observability, D1-fix -->
<!-- STRUCTURE: ┌диагноз (3 вопроса)┐ → ◇ зафиксированные решения → ◇ инвентарь executor'ов → ⊕ scope/критерии → ⎋ открытые вопросы -->
# region MODULE_CONTRACT
## @purpose  Brief «Единый стриминг логов CI + локализация ci-docker smoke-hang»: зафиксировать
##           диагноз утренних 900s-висяков смоука, ответить на 3 вопроса (IMP-трассировка,
##           «пусто 20–30 мин», виток ≤1 мин) и задать границы DevPlan.
## @scope    platform-test.yml (ci-docker gate) + все agent-facing executors логов
##           (make check / check-diff / gate / agent-check / test_runner / smoke-compose).
## @invariants
##   1. Стриминг — ЕДИНЫЙ канон в core/internal/shared/subprocess_io.py; потребители делегируют,
##      не дублируют логику (инвариант shared/AGENTS.md «2 потребителя ИЛИ дедуп ≥2»).
##   2. stdout остаётся machine-readable (test_runner <100 строк, agent-check --json) — стриминг
##      идёт ТОЛЬКО в stderr.
##   3. Таймаут убивает process-group (killpg), не оставляет орфанов (DevPlan 124 T2c, ночь-141).
##   4. Временные диагностические концепции (faulthandler-wrapper, SMOKE_HANG_PROBE, probe-workflow)
##      удаляются/консолидируются в конце — без следов.
## @rationale Утро 08-17: 7 коммитов добавляли диагностику поверх чёрного ящика, но корень —
##            три вложенных буфера вывода (pytest capture → run_cmd pipe → compose capture_output).
## @changes 2026-08-17 | Created — по итогам разбора CI-висяков (run 32025761115/32029164898/32031577141)
# endregion MODULE_CONTRACT

$START_BRIEF
$ARTIFACT_CONTRACT
PURPOSE:               Починить потерю логов в CI (единый стриминг) и локализовать точку зависания ci-docker smoke-сьюта; сократить виток проверки гипотезы до ≤1 мин.
DESCRIPTION:           Утренний разбор: smoke-hang (900s, 0 вывода) висит в одной из трёх фаз (import/collection → setup platform_services → тело теста), но вывода не видно, потому что три буфера съедают IMP-логи: (1) run_cmd (Popen+communicate) печатает только в конце/таймауте, (2) pytest capture, (3) _run_docker_smoke (capture_output). Дополнительно canonical shared/subprocess_io.run_subprocess и test_runner/agent_check тоже используют capture_output. DevPlan: ввести streaming-канон в subprocess_io.py и мигрировать 4 потребителя; затем probe-workflow + минимальный стек для бинарного поиска; в конце — убрать временные концепции.
RATIONALE:             IMP-трассировка исправна как «что за чем», но буферизована и в самой ранней точке hang ещё не выполняется; «пусто 20–30 мин» = smoke timeout 900s + буферизация run_cmd + пайплайн (fast-gate+pre-pull+build) поверх.
ACCEPTANCE_CRITERIA:   1) 4 потребителя делегируют единому streaming-канону, capture_output в них не остаётся; 2) зависший subprocess виден построчно + heartbeat в живом логе gate; 3) stdout machine-readable не сломан; 4) таймаут убивает process-group без орфанов; 5) точка hang локализована и исправлена, ci-docker smoke зелёный <15 мин; 6) виток гипотезы ≤1 мин (probe-workflow / локальный TEST_FILE); 7) git-дерево чистое, временные артефакты удалены.
IMPLEMENTS:            Диагностику CI-висяков 08-17 (commit 4d828d6→65881db) + решения обсуждения (приоритет 1 = стриминг, 2 = выбор инструмента, 3 = минимизация итераций).
IMPACTS:               core/internal/shared/subprocess_io.py (+streaming), core/internal/check_suite/runner.py, core/internal/test_runner.py, core/internal/agent_check/__init__.py, tests/_conftest/compose.py, core/check-suite.yaml, .github/workflows/*, tests/unit/*.
REQUIRES:              Локальный клон ~/projects/ai-platform, Docker (dev-машина), доступ к Actions UI (run 32029164898/32031577141).
$END_ARTIFACT_CONTRACT

---

## 1. Диагноз (факты из CI/CD и кода)

Утром 08-17 в ветку main идут безуспешные попытки заставить ci-docker smoke «говорить»:

| Коммит | Слой диагностики | Что НЕ решает |
|---|---|---|
| `e5a81cc` 00:38 | per-test `--timeout=600`, `PYTHONUNBUFFERED`, `if:failure()` дамп контейнеров | hang в import/collection — до тестов |
| `4d828d6` 03:17 | run_cmd сохраняет partial-stdout/stderr при таймаут-килле | видно ТОЛЬКО после 900s |
| `a3f5668` 09:05 | `xdist: false` на smoke | сняли гонку, hang остался |
| `06c30e3` 14:00 | `-s --log-cli-level=INFO`, параллельный pre-cleanup | сняли pytest-capture, но run_cmd-буфер остался |
| `0bc3590`/`2fbf23c`/`65881db` | faulthandler `dump_traceback_later(600s)` (conftest → начало conftest → `python3 -c` wrapper) | если hang в C-импорте — не сработает |

Ключевой факт из `tests/conftest.py` (L36–47) и `core/check-suite.yaml` (L199–202): последние run'ы (`32029164898`, `32031577141`) висят так рано, что **даже `[conftest-import] begin` не печатается** → зависание до импорта `tests/conftest.py`, т.е. в import-цепочке/collection.

### 1.1 Почему не помогает IMP-трассировка (вопрос 1)

IMP (`[IMP:1..10]`, LDD-канон в `.kilo/rules/principles.md` + `doxygen-generic.md`) — это обычный `logging`/`print`, проходящий через **три вложенных буфера**:

1. **`core/internal/check_suite/runner.py::run_cmd`** (L272–360): `Popen(stdout=PIPE, stderr=PIPE)` + `communicate(timeout)`. Вывод копится в памяти и печатается только по завершении/таймауте. Потребители — `make check`, `make check-diff`, `make gate` (fast/full/ci-docker).
2. **pytest capture** — частично снят `-s --log-cli-level=INFO` (коммит `06c30e3`), но это ложится в буфер №1.
3. **`tests/_conftest/compose.py::_run_docker_smoke`** (L207–227): `capture_output=True` — каждый `docker compose` subprocess сам по себе чёрный ящик до возврата.

Сверх того capture_output живёт ещё в трёх местах (см. §2 «Инвентарь»), включая сам канон `shared/subprocess_io.run_subprocess`.

**Вывод:** IMP исправен как «что за чем вызывается», но (а) буферизован, (б) в самой ранней точке hang (import/collection) Python-код с IMP ещё не выполнялся. Чинить надо стриминг, а не IMP.

### 1.2 Почему «упал сразу, а узнаём через 20–30 минут пустоту» (вопрос 2)

- smoke-чек в `core/check-suite.yaml` имеет `timeout: 900` (15 мин).
- `run_cmd` печатает вывод только по завершении/таймауту → «пусто» видно лишь после 900s.
- fail-fast механизмы (`_SMOKE_SETUP_DEADLINE_SECONDS=540`, `--timeout=600`, faulthandler) **живут внутри pytest** и не срабатывают при hang в import/collection.
- Пайплайн сверху: fast-gate ~3 мин + pre-pull ~5 мин + сборка hermes/backup-cron ~5–10 мин = **20–30 мин до первого «пусто»**.

### 1.3 Виток ≤1 мин (вопрос 3)

Нужны: (а) живой стриминг (Phase 1), (б) probe-инструмент с одним тестом (Phase 2), (в) бинарный поиск от минимального стека (Phase 3). Карантин (`tests/_conftest/quarantine.py`) **не годится как off-switch**: `_guard_and_activate` активирует `platform_services` по маркеру `requires_docker`, а `pytest.mark.skip` поверх карантина маркер не снимает → Docker всё равно поднимется. Рычаг отключения — `SMOKE_NO_DOCKER=1` в `_guard_and_activate` или сужение `gate_modes`.

---

## 2. Инвентарь executor'ов (оценка «кто ест логи»)

| Executor | Путь | Механизм | Вердикт |
|---|---|---|---|
| `make check` / `check-diff` / `gate` | `check_suite/runner.py::run_cmd` | Popen+communicate (буфер) | **мигрировать** |
| `make check MARKER=contract/ai-instructions/static_audit` | `test_runner.py` | `subprocess.run(capture_output=True)` ×6 | **мигрировать** |
| `make agent-check` | `agent_check/__init__.py` | `capture_output=True` ×4 | **мигрировать** |
| smoke/component compose | `tests/_conftest/compose.py::_run_docker_smoke` | `capture_output=True` | **мигрировать** (макс. ценность для hang) |
| canonical `run_subprocess` | `shared/subprocess_io.py` | `capture_output=True` | **расширить** (добавить streaming-функцию; сигнатуру `run_subprocess` не трогать) |
| `make test-node` | Makefile → `pytest tests/e2e/ -m requires_node` | без обёртки (прямо в терминал) | уже стримит — эталон |
| bootstrap/deploy remote-каналы | `remote_executor.py`, `ssh_*`, `core_deliverer` | vary | **вне scope** (remote-транспорт, иная семантика; кандидат на будущее) |

---

## 3. Зафиксированные решения

1. **Сначала стриминг** — единый канон в `shared/subprocess_io.py`, все 4 потребителя делегируют; никто не ест логи, ошибки видны по мере появления.
2. **CI-инструмент — на мой вкус**: отдельный `workflow_dispatch` `smoke-probe.yml` (1 тест, timeout 120s, живые логи). В конце — **удалить** probe и все неудачные концепции (faulthandler-wrapper, SMOKE_HANG_PROBE-bisect), если они не станут каноном.
3. **Минимизация итераций** — целевая метрика «время между гипотезой и её проверкой»; локально `make check TEST_FILE=tests/test_smoke_<module>.py` + `PLATFORM_COMPOSE_TIMEOUT=30`, в CI — probe-workflow.

---

## 4. Открытые вопросы (решаются в DevPlan)

| # | Вопрос | Варианты |
|---|---|---|
| O1 | Streaming-результат: новый `StreamingResult` dataclass vs `CompletedProcess`+флаги | dataclass (`.stdout/.stderr/.returncode/.duration_ms/.timed_out`) — совместим с потребностями CheckOutcome |
| O2 | Heartbeat-интервал и cap лога | 30s heartbeat; живой стрим в stderr с префиксом `[child]`, полный вывод — в памяти для отчёта |
| O3 | faulthandler-wrapper (`65881db`) судьба | консолидировать в канон как `--faulthandler-timeout` pytest-опцию ИЛИ удалить после локализации |
| O4 | Probe-workflow оставить или удалить | по умолчанию **удалить** (пункт 2 решений); оставить только при явном запросе |

$END_BRIEF
