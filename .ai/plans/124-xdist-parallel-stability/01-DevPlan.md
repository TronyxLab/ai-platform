# 01-DevPlan.md — 124: xdist-параллельная стабильность тестов

<!-- GREP_SUMMARY: devplan-124, xdist, parallel, flaky, race, xdist-false, process-lock, docker-markers, pytest_sessionfinish, worker, sessionstart, counter, serial -->
<!-- STRUCTURE: ▶ $ARTIFACT_CONTRACT → ◇ Контекст (факты 2026-08-03) → ◇ Волна 1 (session-хуки только master) → ◇ Волна 2 (xdist:false для docker + process flock, A2+) → ◇ Волна 3 (фантом-маркеры, счётчик, /tmp; T5 отменён) → ◇ Волна 4 (AGENTS.md + правила, после кода волн 1-3) → ⎋ Критерии приёмки -->

# region MODULE_CONTRACT
## @purpose  Устранить флаки/гонки pytest-xdist (-n auto), из-за которых агенты гоняют тесты по несколько раз в поисках фантома; docker-тесты — single-process (один стек); зафиксировать правила параллельного запуска для новых тестов (tests/AGENTS.md). Решение A2+ (2026-08-03): xdist:false для docker вместо flock-механизма.
## @scope    tests/_conftest/session.py, tests/_conftest/counter.py, tests/gates/test_gate_manifests_up_to_date.py, tests/test_component_clickhouse.py, core/check-suite.yaml, core/internal/check_suite.py, tests/AGENTS.md, core/internal/test_runner.py.
## @invariants
##   1. Все фиксы проходят: make check (до чистоты) → make gate MODE=fast (один раз в конце)
##   2. Новый код — Python; shell — только тонкие фасады (языковая политика)
##   3. Никаких правок generated-файлов руками (инвариант 11); никаких auto version bump
##   4. Поведение одиночного прогона (-p no:xdist) НЕ меняется — только параллельного
##   5. Работа не мешает сессии «Итоговая проверка девпланов и RC-развёртывание»: docker-тесты
##      на dev-машине не гонять повторно (порты заняты prod-стеком — факт 10), фиксы — статические
## @rationale  Пользователь наблюдает флаки при параллельном запуске тестов (xdist). Эксперимент
##             2026-08-03 воспроизвёл гонку: 2 docker-воркера → 5 passed / 7 errors («No such
##             container», unhealthy, not_found), одиночный прогон hermes — 7 passed. Корни:
##             session-хуки в каждом воркере (docker-cleanup), конкурентный compose up одного
##             стека, refcount сетей в памяти процесса, фантом-маркер xdist_group.
## @changes 2026-08-03 | Создан по итогам диагностики: 4× static_audit (3222) 0 фейлов, contract
##           285 0 фейлов, predeploy 37 0 фейлов — статика стабильна; гонка воспроизведена на docker
##           (эксперимент контаминирован порт-конфликтами dev-машины — F1 отчёта QA; механизм гонки
##           подтверждён кодом: smoke.py:832 pre-cleanup `down --remove-orphans` + smoke.py:858 `rm -f`)
## @changes 2026-08-03 | QA-сверка перед реализацией: PYTEST_XDIST_WORKER — стандартный env
##           xdist-воркера (отсутствует в master); прецедент xdist: false — predeploy-docker
##           (check-suite.yaml:136); flock-прецедент — counter.py (DevPlan 120 §3.3)
## @changes 2026-08-03 | Ревью QA (02-VerificationReport, находки F1-F11) + решение пользователя A2+:
##           волна 2 переписана (T2 flock/DockerStackLock → _xdist_args(marker) docker-исключение +
##           gates-docker xdist:false + process-лок flock tests/.docker-suite.lock),
##           T5 (networks master-only) отменён (single-process → refcount корректен),
##           приёмка обновлена (пере-диагноз F1 на чистой машине, docker = 1 подъём стека,
##           красный прогон счётчика на static-сьюте), приложение A сжато до 3 неочевидных правил,
##           T7 перенесён в конец реализации (после кода волн 1-3, F5)
# endregion MODULE_CONTRACT

## $ARTIFACT_CONTRACT

| Поле | Значение |
|------|----------|
| **PURPOSE** | Сделать параллельный запуск тестов (pytest-xdist -n auto) детерминированным: убрать гонки session-хуков, docker-стека, сетей и счётчика; закрепить правила для новых тестов в tests/AGENTS.md |
| **DESCRIPTION** | Волна 1: docker-cleanup и счётчик — только в master-воркере (PYTEST_XDIST_WORKER). Волна 2 (A2+): исключение docker-маркеров из xdist в test_runner.py `_xdist_args(marker)`, gates-docker `xdist: false` в check-suite.yaml, process-лок `flock tests/.docker-suite.lock` вокруг docker-pytest-процессов (межсессионная гонка F4). Волна 3: снятие фантом-маркера xdist_group, честная семантика Attempt-счётчика, tmp_path вместо /tmp (T5 сети отменён — single-process). Волна 4 (после кода волн 1-3): раздел «Параллельный запуск» в tests/AGENTS.md (сжатые правила, инвариант 10) |
| **RATIONALE** | Механизм гонки подтверждён кодом (smoke.py:832 pre-cleanup `down --remove-orphans` + smoke.py:858 `rm -f` в каждом воркере); эксперимент 2026-08-03 (5 passed / 7 errors при -n 2) контаминирован порт-конфликтами dev-машины (F1) — пере-диагноз на чистой машине включён в приёмку. Решение A2+ (пользователь 2026-08-03) устраняет гонки по построению (single-process docker = ноль новых флаков), в отличие от flock-механизма A1, который требовал бы ~100+ LOC нового conftest-кода и не закрывал дыры F2/F4/F8 |
| **ACCEPTANCE_CRITERIA** | (1) Пере-диагноз F1 на чистой машине до реализации волны 2: test_smoke_nginx solo → PASS, затем hermes+nginx -n 2 с per-test breakdown; (2) повторная гонка docker-чисток невозможна: sessionfinish-хуки не выполняются в воркерах; (3) .test_counter.json: 1 инкремент за сессию, сброс только при aggregate-100% pass; (4) маркер xdist_group снят с манифест-гейта или переведён на --dist loadgroup; (5) tests/AGENTS.md содержит раздел «Параллельный запуск (xdist)» со сжатыми правилами (3 неочевидных); (6) make check + make gate MODE=fast зелёные; (7) docker-приёмка = 1 подъём стека (не «2× подряд»), 0 errors |
| **IMPLEMENTS** | Наблюдения пользователя 2026-08-03 (флаки xdist), решение A2+ (2026-08-03), находки QA-ревью F1-F11 (02-VerificationReport.md) |
| **IMPACTS** | tests/_conftest/session.py, tests/_conftest/counter.py, tests/gates/test_gate_manifests_up_to_date.py, tests/test_component_clickhouse.py, core/internal/test_runner.py, core/check-suite.yaml, core/internal/check_suite.py, tests/AGENTS.md |
| **REQUIRES** | pytest-xdist (установлен, 3.8.0); чистая машина для docker-приёмки (факт 10: порты заняты prod-стеком — docker-приёмка на CI/другой машине); flock (coreutils, доступен на macOS/Linux) |

---

## Контекст (факты 2026-08-03)

1. **static_audit стабилен под xdist**: 4 последовательных прогона `python -m core.internal.test_runner --marker static_audit` (-n auto, 12 воркеров): 3222 теста, 0 фейлов (65s / 68s / 98s / 105s — рост от нагрузки машины). Фантомов в статике не воспроизведено.
2. **contract стабилен**: 285 тестов, 0 фейлов (9.8s). **predeploy** (not requires_docker): 37 тестов, 0 фейлов (12.1s).
3. **Гонка docker-слоя воспроизведена** (механизм подтверждён кодом): `pytest tests/test_component_hermes.py tests/test_smoke_nginx.py -n 2` → **5 passed, 7 errors** (117.8s). Симптомы: `Error response from daemon: No such container: 1ee80528...`, `container litellm-test is unhealthy`, `container hermes-agent-test is unhealthy`, `Reused containers not healthy: {'postgres-test': 'not_found', 'pgbouncer-test': 'not_found'}`. Контроль: `test_component_hermes.py` одиночно (-p no:xdist) → **7 passed** (74.7s). **Важно (F1):** эксперимент контаминирован порт-конфликтами dev-машины (test_smoke_nginx одиночно = 5 errors — порты 80/443 заняты prod-стеком). Механизм гонки доказан кодом независимо: smoke.py:832 pre-cleanup `down --remove-orphans` + smoke.py:858 `rm -f` выполняются в каждом воркере. Пере-диагноз на чистой машине — шаг приёмки до реализации волны 2.
4. **`.test_counter.json` инкрементируется в каждом воркере**: эксперимент -n 2 → `Attempt #2` за ОДИН прогон; при -n auto (12 воркеров) один фейл-прогон даст `Attempt #12` — anti-loop протокол искажается. Сброс в 0 выполняется КАЖДЫМ воркером с exitstatus==0 — фейл параллельного воркера не фиксируется.
5. **`pytest_sessionfinish` выполняется в каждом xdist-воркере** (session.py:275): `_final_compose_cleanup()` (docker rm -f по label ai-platform-test), `_final_hermes_test_cleanup()` (hermes-test-*), `_force_release_test_networks()` — воркер, закончивший раньше, удаляет контейнеры/сети, пока другие воркеры их используют.
6. **platform_services (session-scoped) выполняется в каждом воркере независимо** (smoke.py:735): global pre-cleanup (`docker compose down` всех файлов, smoke.py:821-835) + stale `docker rm -f` (smoke.py:858-864) + compose up — воркер B, стартовавший позже, сносит стек воркера A («No such container», not_found). Reuse-механизм (reuse.py) — read-only проверка, НЕ межпроцессная блокировка.
7. **Фантом-маркер `xdist_group("serial")`** на test_gate_manifests_up_to_date.py:38: при `-n auto` (load distribution) xdist_group игнорируется (требует `--dist loadgroup`). Подтверждено комментарием в test_gate_timeout_literals.py:66 («Отвергнуто: xdist_group("serial") — требует --dist loadgroup, при -n auto (load) игнорируется»). Гейт `git diff --exit-code` по generated files параллелится с любым тестом без эффекта, но маркер создаёт ложное ощущение защиты.
8. **NetworkLeaseManager — refcount в памяти процесса** (networks.py:204 `self._leases: dict`): при xdist каждый воркер считает себя создателем сети; release()/release_all() одного воркера удаляет сеть, используемую другим.
9. **Общий /tmp-путь**: test_component_clickhouse.py:253 пишет `/tmp/clickhouse-compose-up-failed.log` (не tmp_path) — межворкерная гонка записи и мусор между прогонами.
10. **Окружение dev-машины**: поднят prod-стек (nginx, botanika, dance-site, tronyx-site, status-page — порты 80/443 заняты) → docker-тесты не полностью воспроизводятся одиночно (test_smoke_nginx одиночно: 5 errors — port conflict, retry-циклы compose up rc=1). Docker-приёмку волны 2 выполнять на чистой машине/CI, не на dev-машине.
11. **xdist worker id детекция**: стандартная — env `PYTEST_XDIST_WORKER` установлен в воркерах, отсутствует в master. Прецедент flock: `_CounterLock` в counter.py (fcntl.flock, DevPlan 120 §3.3). Прецедент xdist: false: predeploy-docker в check-suite.yaml:136.
12. **Ключевая точка переключения xdist — test_runner.py `_xdist_args()`** (строка 137): применяется ко ВСЕМ маркерным суитам (smoke, component, integration, predeploy-docker идут через test_runner). `make test-summary MARKER=smoke` (агентский путь) идёт через test_runner, а НЕ через check_suite. `TEST_NO_XDIST=1` (check_suite.py:422) прокидывается только при запуске через check_suite — агентский путь его не выставит. Точечный флип `xdist: false` в yaml покрывает только gates-docker и check_suite-инвокации; для полного покрытия нужно исключение docker-маркеров в `_xdist_args()` (F11).

---

## Волна 1 — Session-хуки только в master (гонки docker-cleanup и счётчика)

### T1. sessionstart/sessionfinish: docker-cleanup и счётчик — только master-воркер
**Проблема**: session.py:120/275 — хуки выполняются в каждом воркере; docker rm -f и сброс счётчика ломают параллельные воркеры (факты 4, 5).
**Задачи:**
- [ ] В `pytest_sessionstart`: `_is_xdist_worker()` (helper: `bool(os.environ.get("PYTEST_XDIST_WORKER"))`), инкремент счётчика — только если НЕ воркер. Воркерные старты — no-op (лог с worker id).
- [ ] В `pytest_sessionfinish`: `_final_compose_cleanup()`, `_final_hermes_test_cleanup()`, `_force_release_test_networks()` и read/reset счётчика — только в master. В воркере — только лог «worker cleanup skipped (master owns session)».
- [ ] exitstatus-семантика: master видит aggregate-результат сессии (xdist агрегирует в master) → сброс счётчика при полном PASS корректен.
- [ ] Unit-тест: tests/unit/test_session_xdist_guards.py — с mocked `PYTEST_XDIST_WORKER` (monkeypatch) проверить: воркер не инкрементирует/не сбрасывает/не чистит; master — делает.
**Приёмка**: тест с `-n 2` и фейлом в одном воркере не сбрасывает счётчик; попытка #1 (не #2) при первом фейле.

## Волна 2 — Single-process docker: xdist-исключение + process-лок (A2+, решение пользователя 2026-08-03)

> **🧐 TRAP[DECISION] · 2026-08-03 · — · A2+ (xdist:false + process-лок) вместо A1 (flock/DockerStackLock)**
> · **Rejected: A1 (flock)** — ~100+ LOC нового conftest-кода (docker_lock.py, retry-loop, TOCTOU double-check, ownership), дыра F2 (module-fixture lifecycle вне лока), новые флаки F8 (unbounded retry), не решает межсессионную гонку F4 без дополнительного session-мутекса, приёмка требует 2+ подъёмов стека
> · **Reason:** A2+ устраняет гонки по построению (docker = single-process, один стек): intra-process — через `_xdist_args(marker)` исключение docker-маркеров, inter-session — через process-level `flock`. Ноль новых флаков, ~15 LOC изменений, 1 подъём стека на приёмку. Совпадает с позицией пользователя (ограниченные ресурсы) и исходным дизайном wave-pipeline/ThreadPoolExecutor/reuse/refcount, спроектированным под один процесс (DevPlan 040/041). Стоимость: +2-3 мин ассерт-фазы docker-сьютов на редких full-gate прогонах
> · **Rev:** если docker-сьюты станут доминировать в wall-clock full-gate → рассмотреть A3 (единая docker-сессия) отдельным DevPlan

### T2a. test_runner.py: `_xdist_args(marker)` — исключение docker-маркеров из -n auto
**Проблема**: `_xdist_args()` (test_runner.py:137) применяет `-n auto` ко ВСЕМ маркерным суитам, включая smoke/component/integration/predeploy-docker. Агентский путь `make test-summary MARKER=smoke` идёт через test_runner (не check_suite) — гонка docker-стека сохраняется (F11).
**Задачи:**
- [ ] Переименовать `_xdist_args()` → `_xdist_args(marker: str | None = None)` с параметром marker.
- [ ] Определить множество `_DOCKER_MARKERS = {"smoke", "component", "integration", "predeploy-docker"}`.
- [ ] Если marker в `_DOCKER_MARKERS` — вернуть `[]` (без `-n auto`), независимо от `TEST_NO_XDIST`.
- [ ] `_run_all_suites` (строка 444): пробросить marker в `_xdist_args(marker)`.
- [ ] Прямой вызов через main (marker-диспетчер) — аналогично.
- [ ] Статические/contract/predeploy (не docker) маркеры — поведение без изменений (`-n auto`).
**Приёмка**: `make test-summary MARKER=smoke` → pytest БЕЗ `-n auto`; `make test-summary MARKER=static_audit` → pytest С `-n auto`.

### T2b. core/check-suite.yaml: gates-docker → `xdist: false`
**Проблема**: gates-docker (строки 97-104) без явного `xdist` → default `true` (check_suite.py:329). Сегодня ~0 тестов (`allow_no_tests: true`), но первый же docker-gate-тест унаследует гонку (F9). Прямой pytest-вызов (через check_suite `_apply_xdist`) получит `-n auto`.
**Задачи:**
- [ ] `gates-docker`: добавить `xdist: false` (строка ~99, после `docker: true`).
- [ ] Smoke/component (строки 145, 153) — НЕ менять: их `xdist: true` — метаданные; реальный xdist-контроль идёт через test_runner (T2a). Прямые pytest-вызовы для них не используются (smoke/component всегда через `make test MARKER=...` → test_runner).
**Приёмка**: `make gate MODE=fast` (включает gates-docker) → pytest для gates-docker без `-n auto`.

### T2c. Process-level `flock tests/.docker-suite.lock` вокруг docker-pytest-процессов
**Проблема**: межсессионная гонка (F4): два агента одновременно гоняют docker-сьюты → master-клинер одной сессии сносит активный стек другой. T2a/b решают intra-process гонку, но не inter-session.
**Задачи:**
- [ ] **Место: test_runner.py** — функция-обёртка `_run_docker_pytest()` (префикс `flock tests/.docker-suite.lock` перед subprocess.run для docker-маркеров). Мотивация: test_runner — канонический entry point для docker-сьютов (`make test-summary MARKER=smoke` — агентский путь). check_suite.py зеркально применяет тот же префикс в `_run_pytest_check()` при `spec.docker: true` (gates-docker, predeploy-docker). Единый lock-файл `tests/.docker-suite.lock` для всех docker-pytest-процессов на машине.
- [ ] Lock удерживается на весь процесс (subprocess.run до возврата) — retry/release не нужны: flock ядра ОС атомарно снимает lock при завершении процесса-держателя.
- [ ] Не-pytest чеки (tier=static, fix) — не затрагиваются.
- [ ] Lock-файл в `.gitignore` (tests/.docker-suite.lock — runtime-артефакт).
**Приёмка**: два параллельных `make test-summary MARKER=smoke` из разных терминалов — второй ждёт завершения первого (не пересекаются по docker-стеку).

## Волна 3 — Фантом-маркеры, честный счётчик, tmp-пути

> T5 (networks master-only) — **ОТМЕНЁН** решением A2+: при single-process docker (T2a) refcount сетей в памяти процесса корректен, acquire/release выполняются в единственном процессе. Модуль `tests/_conftest/networks.py` не требует изменений.

### T3. Снять фантом-маркер xdist_group с манифест-гейта
**Проблема**: test_gate_manifests_up_to_date.py:38 — маркер не работает при -n auto (факт 7).
**Задачи:**
- [ ] Убрать `@pytest.mark.xdist_group("serial")`; в docstring гейта зафиксировать: гейт безопасен параллельно (тесты не пишут в generated files; единственный писатель — pre-commit/генераторы вне pytest), маркер был фантомом.
- [ ] Решение по `--dist loadgroup` зафиксировать TRAP[DECISION]: НЕ вводить (риск деградации параллелизма, нет реальной гонки); Rev: при появлении теста, реально пишущего в общие файлы.
- [ ] Проверить прочие `xdist_group` в tests/ — после снятия не осталось (rg подтверждает единственный).
**Приёмка**: `rg -rn "xdist_group" tests/` → 0 вхождений (кроме задокументированных ссылок); make gate MODE=fast зелёный.

### T4. Честная семантика Attempt-счётчика
**Проблема**: инкремент/сброс в каждом воркере (факт 4).
**Задачи:**
- [ ] После T1: master инкрементирует 1 раз за сессию; воркеры — no-op.
- [ ] В `_increment_counter`/`_write_counter` оставить flock (защита от ПАРАЛЛЕЛЬНЫХ сессий агентов — 2 pytest одновременно).
- [ ] Проверить gates/conftest.py — там свой counter (отдельный файл/секция) — применить ту же master-guard логику, если он тоже на xdist.
**Приёмка**: фейл-прогон `-n 2` → `Attempt #1`; повторный → `Attempt #2`.

### T6. /tmp-пути → tmp_path
**Проблема**: test_component_clickhouse.py:253 — общий `/tmp/clickhouse-compose-up-failed.log` (факт 9).
**Задачи:**
- [ ] Заменить на tmp_path fixture (лог — в тестовой директории воркера).
- [ ] `rg '"/tmp/' tests -g "*.py"` — проверить остальные вхождения; задокументировать легитимные (test_node_configs/status-metrics в smoke.py:779-780 — bind-mount контракт compose, НЕ менять).
**Приёмка**: rg по /tmp в tests/ не показывает файлов, создаваемых тестами (кроме bind-mount контрактов).

## Волна 4 — Правила параллельного запуска в tests/AGENTS.md (после реализации волн 1-3)

> **⚠️ Порядок:** T7 выполняется ПОСЛЕ реализации кода волн 1-3 (F5: правки tests/AGENTS.md не должны описывать несуществующий код; документация вливается вместе с кодом, а не до него).

### T7. Раздел «Параллельный запуск (pytest-xdist)» в tests/AGENTS.md
**Проблема**: новые тесты пишутся без учёта параллельного запуска (-n auto — стандарт test_runner/check-suite). Текущий раздел (если есть) на 70% состоит из очевидной pytest-гигиены (monkeypatch/cwd/wait-фикстуры — нулевой прецедент нарушения, F6).
**Задачи:**
- [ ] Заменить/добавить раздел «Параллельный запуск (pytest-xdist)» в tests/AGENTS.md сжатым текстом из приложения A (3 неочевидных правила + интро, ~6 строк): docker-фикстуры канон, xdist_group-фантом, общие ресурсы без мутаций. Очевидная pytest-гигиена (monkeypatch, cwd, wait-фикстуры) исключена.
- [ ] Обновить/добавить инвариант 10 в списке tests/AGENTS.md invariants: «xdist-безопасность: `-n auto` — стандарт для статических тестов; docker-тесты — single-process по построению (один стек на машину), это не исключение, а свойство домена».
- [ ] Проверить отсутствие ссылок на несуществующие механизмы (DockerStackLock, master-семантика `PYTEST_XDIST_WORKER` для docker — только session-хуки через неё).
**Приёмка**: раздел присутствует, ≤6 строк правил; invariants — инвариант 10 с уточнённой формулировкой; нет ссылок на удалённые/нереализованные механизмы.

---

## Файл-манифест

| Файл | Действие | Волна |
|------|----------|-------|
| tests/_conftest/session.py | master-guard для sessionstart/sessionfinish (T1) | 1 |
| tests/unit/test_session_xdist_guards.py | НОВЫЙ: unit-тесты master/worker-семантики (T1) | 1 |
| core/internal/test_runner.py | `_xdist_args(marker)` — docker-множество исключено из `-n auto` (T2a); flock-обёртка `_run_docker_pytest()` для docker-pytest-процессов (T2c) | 2 |
| core/check-suite.yaml | gates-docker → `xdist: false` (T2b) | 2 |
| core/internal/check_suite.py | flock-префикс в `_run_pytest_check()` при `spec.docker: true` (T2c) | 2 |
| tests/.gitignore | добавить `.docker-suite.lock` (runtime lock-файл, T2c) | 2 |
| tests/gates/test_gate_manifests_up_to_date.py | снять xdist_group (T3) | 3 |
| tests/_conftest/counter.py | комментарии master-семантики (T4) | 3 |
| tests/gates/conftest.py | master-guard для gate-счётчика (T4) | 3 |
| tests/test_component_clickhouse.py | tmp_path для лога (T6) | 3 |
| tests/AGENTS.md | раздел «Параллельный запуск (xdist)» + инвариант 10 (T7) | 4 (после волн 1-3) |

**Удалены из манифеста (A2+):**
- ~~tests/_conftest/docker_lock.py~~ — T2 flock отменён
- ~~tests/_conftest/networks.py~~ — T5 сети отменён (single-process refcount корректен)
- ~~tests/_conftest/smoke.py~~ — T2 lock-модификации отменены

---

## Критерии приёмки (итог)

1. `make check` (до чистоты) → `make gate MODE=fast` — зелёные (статическая приёмка на dev-машине).
2. static_audit с xdist: 3× подряд 0 фейлов (dev-машина).
3. **Пере-диагноз F1 на чистой машине до реализации волны 2:** test_smoke_nginx solo → PASS (подтверждение: ошибки были от порт-конфликтов, не от гонки стека); затем hermes+nginx `-n 2` с per-test разбивкой ошибок (калибровка: какие ошибки реально от гонки). Результат фиксируется в комментарии к коммиту или DevPlan.
4. **Docker-приёмка (чистая машина/CI):** `make test-summary MARKER=smoke` → 0 errors; `docker network ls` чист после прогона; 1 подъём стека (не «2× подряд» — одиночный процесс). Проверка T2c: два параллельных `make test-summary MARKER=smoke` из разных терминалов — второй ждёт (process-лок).
5. **Счётчик (Anti-Loop Protocol):** фейл-прогон `-n 2` на static-сьюте (не docker!) → Attempt #1 (не #2); повторный → #2; успех → reset 0.
6. `rg xdist_group tests/` → 0 активных использований.
7. tests/AGENTS.md — раздел параллельного запуска присутствует (≤6 строк, 3 неочевидных правила); инвариант 10: «xdist — стандарт для статики; docker — single-process по построению (один стек)».
8. Сессия «Итоговая проверка девпланов и RC-развёртывание» не затронута: никакие docker-операции на dev-машине вне эксперимента (эксперимент завершён, контейнеры подчищены).

---

## Приложение A — Сжатый текст раздела для tests/AGENTS.md (T7)

```markdown
## Параллельный запуск (pytest-xdist)

Запуск через `-n auto` — стандарт (test_runner/check-suite); флак параллельного прогона = баг теста (DevPlan 124).
Обязательные правила (неочевидные; очевидная pytest-гигиена — monkeypatch/cwd/wait-фикстуры — опущена):

1. **Docker — только канонические фикстуры** (`platform_services`, модульные из `_conftest/smoke.py`).
   Прямой `docker compose up` запрещён: воркеры конкурентно поднимают один стек
   (эксперимент 2026-08-03: 5 passed / 7 errors при `-n 2`).
2. **`xdist_group("serial")` НЕ работает** при `-n auto` — не использовать
   (test_gate_timeout_literals.py:66).
3. **Общие ресурсы не мутируются:** файлы — `tmp_path`; рабочее репо
   (git add/commit/checkout, tracked-файлы) — read-only; docker/счётчик — через flock +
   master-семантику (`_conftest/counter.py`; session-хуки — только master, `PYTEST_XDIST_WORKER`).
   Остальное — стандартная pytest-гигиена.
```

$START_DEVPLAN
План 124 (данный файл) — полный документ выше.
$END_DEVPLAN
