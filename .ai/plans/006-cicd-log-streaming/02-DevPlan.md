# 02-DevPlan — Единый стриминг логов CI + локализация ci-docker smoke-hang

<!-- GREP_SUMMARY: cicd, log-streaming, run-subprocess-streaming, killpg, heartbeat, check-suite, test-runner, agent-check, compose, smoke-probe, bisect, faulthandler, importtime, cleanup, D1-fix -->
<!-- STRUCTURE: ┌streaming-канон (Wave 1)┐ → ◇ миграция 4 потребителей (Wave 2–4) → ◇ fast-iteration probe (Wave 5) → ◇ локализация bisect (Wave 6) → ⎋ cleanup + acceptance (Wave 7) -->
# region MODULE_CONTRACT
## @purpose  Исполнение Brief 006: единый streaming-канон subprocess в shared/, миграция
##           check_suite/test_runner/agent_check/compose, probe-инструмент быстрой итерации,
##           локализация точки hang ci-docker smoke, финальная зачистка временных концепций.
## @scope    core/internal/shared/subprocess_io.py + 4 потребителя + check-suite.yaml +
##           .github/workflows + tests/unit.
## @invariants
##   1. Streaming-канон — stdlib-only, Popen(start_new_session=True) + killpg при таймауте,
##      стрим ТОЛЬКО в stderr, полный вывод накапливается для отчёта.
##   2. Сигнатура канонического run_subprocess НЕ меняется (30+ потребителей) — рядом добавляется
##      run_subprocess_streaming (расширение, не ломка).
##   3. stdout machine-readable сохранён (test_runner summary <100 строк, agent-check --json).
##   4. Временные артефакты (probe-workflow, SMOKE_HANG_PROBE, faulthandler-wrapper) удаляются
##      в Wave 7, если не приняты как канон.
## @rationale Brief 006 §1: три вложенных буфера съедают IMP-логи; стриминг — пред-условие
##            локализации; без него каждая итерация = 30 мин слепого ожидания.
## @changes 2026-08-17 | Created — следствие Brief 006
# endregion MODULE_CONTRACT

## $ARTIFACT_CONTRACT

- **PURPOSE:** Ввести единый streaming-канон subprocess-вызова, мигрировать на него все agent-facing executor'ы логов, сократить виток проверки гипотезы до ≤1 мин и локализовать точку зависания ci-docker smoke-сьюта.
- **DESCRIPTION:** Двухчастная работа. Часть A (стриминг): новый `run_subprocess_streaming` в `shared/subprocess_io.py` (Popen+start_new_session, reader-потоки с tee в stderr, heartbeat, killpg при таймауте, накопление полного вывода); миграция `check_suite/runner.run_cmd`, `test_runner.py` (6 сайтов), `agent_check/__init__.py` (4 сайта), `tests/_conftest/compose._run_docker_smoke`. Часть B (локализация): `smoke-probe.yml` (workflow_dispatch, 1 тест ≤120s) + `SMOKE_NO_DOCKER`/`SMOKE_MODULES` рычаги + бинарный поиск от минимального wave-0 модуля; финальная зачистка временных концепций.
- **RATIONALE:** IMP-трассировка буферизована и в ранней точке hang не исполняется — сначала устранить слепоту (стриминг), затем сузить (bisect), затем починить корень.
- **ACCEPTANCE_CRITERIA:** см. §6.
- **IMPLEMENTS:** Brief 006 (решения 1–3, открытые вопросы O1–O4).
- **IMPACTS:** см. Brief §2 «Инвентарь».
- **REQUIRES:** Actions UI (чтение логов run'ов), локальный Docker для воспроизведения.

---

## 1. Часть A — Единый стриминг (приоритет 1)

### 1.1 Контракт `run_subprocess_streaming`

Новый символ в `core/internal/shared/subprocess_io.py` (рядом с `run_subprocess`, сигнатуру которого НЕ трогаем):

```python
def run_subprocess_streaming(
    cmd: list[str], *,
    timeout: int | None = DEFAULT_TIMEOUT,
    env: dict[str, str] | None = None,
    cwd: str | Path | None = None,
    stream: bool = True,            # tee в stderr по строкам
    heartbeat: int = 30,            # 0 = выключить; иначе [IMP:8][stream][heartbeat] каждые N s
    check: bool = False,
    non_fatal: bool = False,
    fatal_rc: tuple[int, ...] = (),
) -> StreamingResult:
```

**Инварианты реализации:**
- `subprocess.Popen(..., stdout=PIPE, stderr=PIPE, text=True, start_new_session=True, env=child_env)`; `child_env` всегда содержит `PYTHONUNBUFFERED=1` (перенос из `run_cmd` L285–286 — дети не буферизуют Python-вывод).
- 2 reader-потока читают stdout/stderr построчно: при `stream=True` — немедленно в stderr с префиксом `[child]`; всегда накапливают полный вывод в буфер (для отчёта/asserts).
- heartbeat-поток: каждые `heartbeat` секунд логирует `elapsed=Xs, pid=Y` — тишина ≠ зависание.
- Таймаут → `os.killpg(os.getpgid(pid), SIGKILL)` + drain читающих потоков → возврат `StreamingResult(timed_out=True, returncode=124, partial stdout/stderr)` — **никогда не raise** (graceful-семантика канона `check=False`).
- `FileNotFoundError` → `returncode=127`.
- `StreamingResult` dataclass: `.stdout`, `.stderr`, `.returncode`, `.duration_ms`, `.timed_out` — совместим с потребностями `CheckOutcome` и `CompletedProcess`-контрактом потребителей.
- **stdout остаётся чистым**: стрим и heartbeat — только в stderr (`logging` на `sys.stderr`).

Решение O1/O2: dataclass + heartbeat 30s + полный вывод в памяти.

---

## 2. Файл-манифест

### 2.1 EDIT — канон (1 файл)

| Файл | Правка |
|---|---|
| `core/internal/shared/subprocess_io.py` | добавить `StreamingResult` + `run_subprocess_streaming` (§1.1); обновить MODULE_CONTRACT/`@changes`; **не менять** `run_subprocess` |

### 2.2 EDIT — миграция потребителей (4 файла)

| Файл | Правка |
|---|---|
| `core/internal/check_suite/runner.py` | `run_cmd` (L272–360): Popen+communicate → `run_subprocess_streaming`; сохранить `docker_lock`, exit 124/127, partial-output логирование, форму `CheckOutcome`; убрать локальный `PYTHONUNBUFFERED` (теперь в каноне) |
| `core/internal/test_runner.py` | 6 сайтов `subprocess.run(capture_output=True)` (L546, L564, L621, L650, L836, L879 + `_run_docker_pytest` L574/630/845) → `run_subprocess_streaming`; сохранить stdout=machine-readable summary, exit 124, JUnit-fallback |
| `core/internal/agent_check/__init__.py` | 4 сайта (L431, L528, L593, L657) → `run_subprocess_streaming`; сохранить `--json` чистоту stdout (стрим в stderr) |
| `tests/_conftest/compose.py` | `_run_docker_smoke` (L207–227): `capture_output=True` → `run_subprocess_streaming` (stream=True); вернуть объект с `.stdout/.stderr/.returncode` (потребители `_module_start_with_retry` читают `result.stderr`) |

### 2.3 EDIT — тесты (3 файла)

| Файл | Правка |
|---|---|
| `tests/unit/test_shared_subprocess_io.py` | +unit-тесты streaming: стрим в stderr, накопление, heartbeat, killpg при таймауте (без орфанов), rc=127/124, `PYTHONUNBUFFERED` в child env |
| `tests/unit/test_check_suite.py` | parity `run_cmd`: форма CheckOutcome, exit 124/127, partial-output (существующие monkeypatch-контракты `check_suite.X` сохранить) |
| `tests/unit/test_test_runner.py` | parity: stdout-summary <100 строк, exit 124, JUnit-fallback при краше до XML |

### 2.4 CREATE — probe-инструмент (временный, Wave 5)

| Файл | Назначение |
|---|---|
| `.github/workflows/smoke-probe.yml` | `workflow_dispatch` (inputs: `module`, `test`, `timeout`): checkout + setup + build minimal + `pytest tests/test_smoke_<module>.py -m smoke -s --log-cli-level=DEBUG --timeout=<t> --tb=short -rs`, `timeout-minutes: 15`. **ВРЕМЕННЫЙ** — удалить в Wave 7, если не решено иначе (O4) |

### 2.5 EDIT — рычаги быстрой итерации

| Файл | Правка |
|---|---|
| `tests/_conftest/compose.py` | `_guard_and_activate` (L626–634): + `SMOKE_NO_DOCKER=1` → `return False` (off-switch без подъёма стека); + `SMOKE_MODULES=<csv>` — фильтр волн до минимального набора |
| `core/check-suite.yaml` | smoke-check: добавить env-проброс `SMOKE_MODULES`/`SMOKE_NO_DOCKER` не требуется (env из процесса наследуется); при необходимости — комментарий |

---

## 3. Волны исполнения

**Wave 1 — канон (Часть A ядро):** `subprocess_io.py` + `run_subprocess_streaming` + unit-тесты (§2.1, §2.3 строка 1). Критерий: `make check MARKER=unit` зелёный, тесты streaming покрывают таймаут-килл и стрим.

**Wave 2 — check_suite (`make check/check-diff/gate`):** миграция `run_cmd` (§2.2 строка 1) + parity-тест. Критерий: `make gate MODE=fast` зелёный; зависший subprocess виден построчно.

**Wave 3 — test_runner (`make check MARKER=...`):** миграция 6 сайтов + parity-тест. Критерий: `make check MARKER=contract` summary на stdout не изменился.

**Wave 4 — agent_check + compose (макс. ценность для hang):** §2.2 строки 3–4. Критерий: `make agent-check` зелёный; `make check MARKER=smoke` локально показывает живой `docker compose up` вывод.

**Wave 5 — fast-iteration (приоритет 3):** `smoke-probe.yml` + `SMOKE_NO_DOCKER`/`SMOKE_MODULES` (§2.4, §2.5). Критерий: 1 тест в CI ≤1 мин с живыми логами; локально `SMOKE_NO_DOCKER=1 make check MARKER=smoke` доказывает «без Docker = мгновенно».

**Wave 6 — локализация (Часть B, приоритет 2):** бинарный поиск от wave-0 минимального модуля (redis/postgres/nginx) с добавлением по 1–3 теста; import/collection — `python -X importtime -m pytest ...` + faulthandler; на живых логах определить точную фазу (import/collection vs setup vs тело). Починка корня в той же волне. Критерий: точка hang названа строкой/файлом и исправлена; ci-docker smoke зелёный <15 мин.

**Wave 7 — зачистка (приоритет 2):** удалить `smoke-probe.yml` (если не принят, O4), консолидировать/удалить faulthandler-wrapper в `check-suite.yaml` (L203, `65881db`) и `SMOKE_HANG_PROBE`-bisect в `tests/conftest.py` (L42–91) — либо оформить `--faulthandler-timeout` как канон (O3). Критерий: `git status` чистый, временных файлов нет.

---

## 4. Ключевые риски и митигации

| Риск | Митигация |
|---|---|
| R1: сломать machine-readable stdout (test_runner/agent_check) | стрим ТОЛЬКО в stderr; parity-тесты stdout в Wave 3/4 |
| R2: дедлок reader-потоков при таймаут-килле | killpg → drain потоков → join с таймаутом; unit-тест таймаут-килла |
| R3: раздувание gate-лога стримом docker compose | префикс `[child]` + полный вывод в памяти (в лог — построчно, отчёт уже хвостит) |
| R4: слом формы CheckOutcome/CompletedProcess у потребителей | `StreamingResult` с теми же атрибутами; parity-тесты `test_check_suite.py`/`test_test_runner.py` |
| R5: hang в C-уровне импорта (Python faulthandler не сработает) | `python -X importtime`, bisect импортов conftest, при необходимости `strace` на CI-раннере |
| R6: орфаны при таймауте (ночь-141, DevPlan 124) | `start_new_session` + `killpg` в каноне — единая точка, орфаны исключены конструктивно |

---

## 5. Do NOT (границы)

1. **НЕ менять** сигнатуру канонического `run_subprocess` (30+ потребителей) — только add streaming-функцию рядом.
2. **НЕ стримить в stdout** — machine-readable контракты (`test_runner` summary, `agent-check --json`) неприкосновенны.
3. **НЕ карантинить** smoke-тесты через `QUARANTINE` как off-switch (маркер `requires_docker` не снимается — `_guard_and_activate` всё равно поднимет стек).
4. **НЕ оставлять** временные концепции (probe-workflow, bisect-печати, faulthandler-wrapper) без решения O3/O4 в Wave 7.
5. **НЕ трогать** bootstrap/deploy remote-каналы (`remote_executor`, `ssh_*`) — вне scope (иная семантика remote-транспорта).

---

## 6. Acceptance / верификация

1. **Единый канон:** `grep -rn "capture_output=True" core/internal/check_suite core/internal/test_runner.py core/internal/agent_check tests/_conftest/compose.py` → 0; все 4 потребителя вызывают `run_subprocess_streaming`.
2. **Живой стрим:** зависший subprocess (искусственный `sleep 300`) в `make gate MODE=ci-docker` показывает построчный вывод + heartbeat в логе ДО таймаута.
3. **stdout чист:** `make check MARKER=contract` summary <100 строк на stdout; `make agent-check --json` валидный JSON на stdout.
4. **Без орфанов:** после таймаут-килла `ps` не показывает осиротевших pytest/xdist-детей.
5. **Локализация:** точка hang названа (фаза + файл/строка) и исправлена; `make gate MODE=ci-docker` smoke зелёный <15 мин.
6. **Виток ≤1 мин:** `smoke-probe.yml` гоняет 1 тест <1 мин с живыми логами; локально `make check TEST_FILE=tests/test_smoke_<module>.py PLATFORM_COMPOSE_TIMEOUT=30` воспроизводит/опровергает гипотезу за минуты.
7. **Зачистка:** `git status` чистый; временные файлы и неудачные концепции удалены или оформлены как канон (O3).
8. **Канон агента:** `make agent-check` exit 0; `make check` зелёный (включая новые unit-тесты streaming).

---

## 6.1 Статус исполнения (2026-08-17, сессия продолжения)

- **Wave 1-5 (Часть A):** выполнено и верифицировано — streaming-канон
  run_subprocess_streaming (subprocess_io.py), миграция 4 потребителей (runner/test_runner/
  agent_check/compose), unit+parity тесты (72 passed), SMOKE_NO_DOCKER/SMOKE_MODULES рычаги.
- **Wave 6 (локализация):** корень назван и исправлен:
  1. DNS-alias'ы postgres/pgbouncer на test-shared-db-net (core/modules/postgres/
     docker-compose.test.yml, W6): langfuse/litellm Prisma @pgbouncer:6432 → P1001 +
     120s poll-петли на каждый тест (корень 900s-hang).
  2. SMOKE_ENV в compose ps (tests/_conftest/health.py, W6): root compose требует
     NGINX_OVERLAY_DIR — без smoke-env poll вечно False → minio ложно unhealthy.
  Верификация: полный smoke 38 passed / 1 skipped (honesty env) за 218s локально
  (было: 900s-килл с 0 вывода); langfuse-сьют 7 passed за 131s.
- **Wave 7 (зачистка):** O3 = pytest-native faulthandler_timeout=600 (pyproject.toml),
  wrapper python3 -c удалён из check-suite.yaml; O4 = smoke-probe.yml удалён;
  SMOKE_HANG_PROBE-bisect удалён из conftest.py + platform-test.yml + test_gate_ci_env_vars.
- **Известные локальные артефакты окружения (НЕ регрессии плана):**
  static_audit 3 failed — setgid-бит на macOS tmpfs (test_ensure_platform_dirs_creates_2775)
  и sudo-блокировка sandbox'а (test_converge_r3_*) — воспроизводятся на чистом дереве (stash).

## 7. Классификация

- **Размер:** LARGE (arch-изменение: новый streaming-канон в shared/ + миграция 4 потребителей + workflow). Brief + DevPlan — по канону `.kilo/rules/artifact-registry.md`.
- **Обратимость:** Часть A аддитивна (run_subprocess не тронут); Часть B временные артефакты удаляются в Wave 7.
- **Dogfood:** первым потребителем streaming-канона становится сам `make check`/gate — живой лог сразу в dev-цикле.
