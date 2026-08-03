# 01-DevPlan.md — 124: xdist-параллельная стабильность тестов

<!-- GREP_SUMMARY: devplan-124, xdist, parallel, flaky, race, pytest_sessionfinish, worker, flock, docker, sessionstart, counter, network-lease, serial -->
<!-- STRUCTURE: ▶ $ARTIFACT_CONTRACT → ◇ Контекст (факты 2026-08-03) → ◇ Диагноз (корневые причины R1-R6) → ◇ Волна 1 (session-хуки только master) → ◇ Волна 2 (docker-стеk под flock) → ◇ Волна 3 (фантом-маркеры, счётчик, сети, /tmp) → ◇ Волна 4 (AGENTS.md + правила) → ⎋ Критерии приёмки -->

# region MODULE_CONTRACT
## @purpose  Устранить флаки/гонки pytest-xdist (-n auto), из-за которых агенты гоняют тесты по несколько раз в поисках фантома; зафиксировать правила параллельного запуска для новых тестов (tests/AGENTS.md).
## @scope    tests/_conftest/session.py, tests/_conftest/smoke.py, tests/_conftest/networks.py, tests/_conftest/counter.py, tests/gates/test_gate_manifests_up_to_date.py, tests/test_component_clickhouse.py, core/check-suite.yaml, tests/AGENTS.md, core/internal/test_runner.py.
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
##           285 0 фейлов, predeploy 37 0 фейлов — статика стабильна; гонка подтверждена на docker
## @changes 2026-08-03 | QA-сверка перед реализацией: PYTEST_XDIST_WORKER — стандартный env
##           xdist-воркера (отсутствует в master); прецедент xdist: false — predeploy-docker
##           (check-suite.yaml:136); flock-прецедент — counter.py (DevPlan 120 §3.3)
# endregion MODULE_CONTRACT

## $ARTIFACT_CONTRACT

| Поле | Значение |
|------|----------|
| **PURPOSE** | Сделать параллельный запуск тестов (pytest-xdist -n auto) детерминированным: убрать гонки session-хуков, docker-стека, сетей и счётчика; закрепить правила для новых тестов в tests/AGENTS.md |
| **DESCRIPTION** | Волна 1: docker-cleanup и счётчик — только в master-воркере (PYTEST_XDIST_WORKER). Волна 2: межворкерный flock вокруг platform_services (pre-cleanup → up) + xdist: false fallback в check-suite.yaml. Волна 3: снятие фантом-маркера xdist_group, честная семантика Attempt-счётчика, межпроцессный refcount сетей, tmp_path вместо /tmp. Волна 4: раздел «Параллельный запуск» в tests/AGENTS.md |
| **RATIONALE** | Эксперимент 2026-08-03: 2 docker-воркера → 5 passed / 7 errors; одиночный hermes 7 passed. static_audit 4× (3222 теста) и contract/predeploy — 0 фейлов: статика xdist-безопасна, гонки живут в docker/session-слое |
| **ACCEPTANCE_CRITERIA** | (1) test_component_hermes + test_smoke_nginx -n 2 → PASS на чистой машине; (2) повторная гонка docker-чисток невозможна: sessionfinish-хуки не выполняются в воркерах; (3) .test_counter.json: 1 инкремент за сессию, сброс только при aggregate-100% pass; (4) маркер xdist_group снят с манифест-гейта или переведён на --dist loadgroup; (5) tests/AGENTS.md содержит раздел «Параллельный запуск (xdist)»; (6) make check + make gate MODE=fast зелёные |
| **IMPLEMENTS** | Наблюдения пользователя 2026-08-03 (флаки xdist), эксперимент диагностики 2026-08-03 |
| **IMPACTS** | tests/_conftest/session.py, tests/_conftest/smoke.py, tests/_conftest/networks.py, tests/_conftest/counter.py, tests/gates/test_gate_manifests_up_to_date.py, tests/test_component_clickhouse.py, core/check-suite.yaml, tests/AGENTS.md, core/internal/test_runner.py |
| **REQUIRES** | pytest-xdist (установлен, 3.8.0), чистая dev-машина для docker-приёмки (факт 10: порты заняты prod-стеком — приёмка docker-части на CI/другой машине) |

---

## Контекст (факты 2026-08-03)

1. **static_audit стабилен под xdist**: 4 последовательных прогона `python -m core.internal.test_runner --marker static_audit` (-n auto, 12 воркеров): 3222 теста, 0 фейлов (65s / 68s / 98s / 105s — рост от нагрузки машины). Фантомов в статике не воспроизведено.
2. **contract стабилен**: 285 тестов, 0 фейлов (9.8s). **predeploy** (not requires_docker): 37 тестов, 0 фейлов (12.1s).
3. **Гонка docker-слоя воспроизведена**: `pytest tests/test_component_hermes.py tests/test_smoke_nginx.py -n 2` → **5 passed, 7 errors** (117.8s). Симптомы: `Error response from daemon: No such container: 1ee80528...`, `container litellm-test is unhealthy`, `container hermes-agent-test is unhealthy`, `Reused containers not healthy: {'postgres-test': 'not_found', 'pgbouncer-test': 'not_found'}`. Контроль: `test_component_hermes.py` одиночно (-p no:xdist) → **7 passed** (74.7s).
4. **`.test_counter.json` инкрементируется в каждом воркере**: эксперимент -n 2 → `Attempt #2` за ОДИН прогон; при -n auto (12 воркеров) один фейл-прогон даст `Attempt #12` — anti-loop протокол искажается. Сброс в 0 выполняется КАЖДЫМ воркером с exitstatus==0 — фейл параллельного воркера не фиксируется.
5. **`pytest_sessionfinish` выполняется в каждом xdist-воркере** (session.py:275): `_final_compose_cleanup()` (docker rm -f по label ai-platform-test), `_final_hermes_test_cleanup()` (hermes-test-*), `_force_release_test_networks()` — воркер, закончивший раньше, удаляет контейнеры/сети, пока другие воркеры их используют.
6. **platform_services (session-scoped) выполняется в каждом воркере независимо** (smoke.py:735): global pre-cleanup (`docker compose down` всех файлов, smoke.py:821-835) + stale `docker rm -f` (smoke.py:858-864) + compose up — воркер B, стартовавший позже, сносит стек воркера A («No such container», not_found). Reuse-механизм (reuse.py) — read-only проверка, НЕ межпроцессная блокировка.
7. **Фантом-маркер `xdist_group("serial")`** на test_gate_manifests_up_to_date.py:38: при `-n auto` (load distribution) xdist_group игнорируется (требует `--dist loadgroup`). Подтверждено комментарием в test_gate_timeout_literals.py:66 («Отвергнуто: xdist_group("serial") — требует --dist loadgroup, при -n auto (load) игнорируется»). Гейт `git diff --exit-code` по generated files параллелится с любым тестом без эффекта, но маркер создаёт ложное ощущение защиты.
8. **NetworkLeaseManager — refcount в памяти процесса** (networks.py:204 `self._leases: dict`): при xdist каждый воркер считает себя создателем сети; release()/release_all() одного воркера удаляет сеть, используемую другим.
9. **Общий /tmp-путь**: test_component_clickhouse.py:253 пишет `/tmp/clickhouse-compose-up-failed.log` (не tmp_path) — межворкерная гонка записи и мусор между прогонами.
10. **Окружение dev-машины**: поднят prod-стек (nginx, botanika, dance-site, tronyx-site, status-page — порты 80/443 заняты) → docker-тесты не полностью воспроизводятся одиночно (test_smoke_nginx одиночно: 5 errors — port conflict, retry-циклы compose up rc=1). Docker-приёмку волны 2 выполнять на чистой машине/CI, не на dev-машине.
11. **xdist worker id детекция**: стандартная — env `PYTEST_XDIST_WORKER` установлен в воркерах, отсутствует в master. Прецедент flock: `_CounterLock` в counter.py (fcntl.flock, DevPlan 120 §3.3). Прецедент xdist: false: predeploy-docker в check-suite.yaml:136.

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

## Волна 2 — Межворкерная блокировка docker-стека

### T2. flock вокруг platform_services (pre-cleanup → up) + защита от конкурентного compose
**Проблема**: два воркера одновременно выполняют pre-cleanup / stale rm / compose up одного стека (факт 6).
**Задачи:**
- [ ] `tests/_conftest/docker_lock.py`: `DockerStackLock` — flock на файл `tests/.docker-stack.lock` (по образцу `_CounterLock`, counter.py) + context-manager; при невозможности получить за короткий timeout — retry-loop с логом «waiting for other worker's compose lifecycle».
- [ ] В `platform_services` (smoke.py:735): обернуть критическую секцию «global pre-cleanup → stale rm → compose up всех модулей → все healthy» в `with DockerStackLock():`. Повторная проверка `check_foreign_containers` ПОСЛЕ захвата лока (двойная проверка — исключает TOCTOU).
- [ ] В sessionfinish-master (T1): финальный docker rm -f — тоже под тем же lock.
- [ ] Fallback-документация: если flock-подход не стабилизирует — в core/check-suite.yaml для smoke/component выставить `xdist: false` (прецедент: predeploy-docker:136); решение фиксируется TRAP[DECISION].
- [ ] `docker compose` команды внутри lock — с таймаутами (существующие `_run_docker_smoke`), lock гарантированно снимается в finally.
**Приёмка**: на чистой машине `pytest tests/test_component_hermes.py tests/test_smoke_nginx.py -n 2` → 0 errors (5+7 passed); повтор 2× без флаков. На dev-машине (порты заняты) — только статические проверки.

## Волна 3 — Фантом-маркеры, честный счётчик, сети, tmp-пути

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

### T5. NetworkLeaseManager: межпроцессный refcount или master-only
**Проблема**: refcount в памяти процесса (факт 8).
**Задачи:**
- [ ] Вариант A (минимальный): acquire/release сетей — только из master (docker-слой уже под flock в T2; сети создаются в platform_services); воркеры сетей не трогают.
- [ ] Вариант B (если понадобится параллельный docker): файловый refcount под flock (tests/.networks-lease.json + flock) — межпроцессный аналог counter.py.
- [ ] Выбор зафиксировать TRAP[DECISION] (A — primary: сети живут вместе со стеком, стек один).
**Приёмка**: два последовательных прогона docker-тестов не оставляют сетей; `docker network ls` чист.

### T6. /tmp-пути → tmp_path
**Проблема**: test_component_clickhouse.py:253 — общий `/tmp/clickhouse-compose-up-failed.log` (факт 9).
**Задачи:**
- [ ] Заменить на tmp_path fixture (лог — в тестовой директории воркера).
- [ ] `rg '"/tmp/' tests -g "*.py"` — проверить остальные вхождения; задокументировать легитимные (test_node_configs/status-metrics в smoke.py:779-780 — bind-mount контракт compose, НЕ менять).
**Приёмка**: rg по /tmp в tests/ не показывает файлов, создаваемых тестами (кроме bind-mount контрактов).

## Волна 4 — Правила параллельного запуска в tests/AGENTS.md

### T7. Раздел «Параллельный запуск (pytest-xdist)» в tests/AGENTS.md
**Проблема**: новые тесты пишутся без учёта параллельного запуска (-n auto — стандарт test_runner/check-suite).
**Задачи:**
- [ ] Добавить в tests/AGENTS.md раздел с правилами (см. приложение A): tmp_path вместо /tmp; никаких общих файлов без flock; docker — только через канонические фикстуры (platform_services), не прямой compose up; env — monkeypatch (воркеры изолированы, но глобальные мутации = межтестовая гонка в воркере); cwd — не менять без fixture; sleep-поллинг — через канонические wait-фикстуры; xdist_group НЕ работает при -n auto; git-операции — только в tmp-репо; общие репозиторные файлы (манифесты, git index) — read-only.
- [ ] Обновить Cross-References/инварианты списка (tests/AGENTS.md invariants) — добавить инвариант 10 «xdist-безопасность: параллельный запуск — стандарт, не исключение».
**Приёмка**: раздел присутствует; invariants списка — 10 пунктов.

---

## Файл-манифест

| Файл | Действие | Волна |
|------|----------|-------|
| tests/_conftest/session.py | master-guard для sessionstart/sessionfinish (T1) | 1 |
| tests/_conftest/docker_lock.py | НОВЫЙ: DockerStackLock (flock) (T2) | 2 |
| tests/_conftest/smoke.py | platform_services под lock + double-check (T2) | 2 |
| core/check-suite.yaml | (fallback, только при фейле T2) smoke/component xdist: false (T2) | 2 |
| tests/unit/test_session_xdist_guards.py | НОВЫЙ: unit-тесты master/worker-семантики (T1) | 1 |
| tests/gates/test_gate_manifests_up_to_date.py | снять xdist_group (T3) | 3 |
| tests/_conftest/networks.py | master-only acquire/release (T5) | 3 |
| tests/_conftest/counter.py | (если нужно) комментарии master-семантики (T4) | 3 |
| tests/gates/conftest.py | master-guard для gate-счётчика (T4) | 3 |
| tests/test_component_clickhouse.py | tmp_path для лога (T6) | 3 |
| tests/AGENTS.md | раздел «Параллельный запуск (xdist)» + инвариант 10 (T7) | 4 |

---

## Критерии приёмки (итог)

1. `make check` (до чистоты) → `make gate MODE=fast` — зелёные (статическая приёмка на dev-машине).
2. static_audit с xdist: 3× подряд 0 фейлов (dev-машина).
3. docker-приёмка (чистая машина/CI): hermes+nginx -n 2 → 0 errors, 2× подряд; docker network ls чист после прогонов.
4. Счётчик: фейл-прогон -n 2 → Attempt #1 (не #2); повторный → #2; успех → reset 0.
5. `rg xdist_group tests/` → 0 активных использований.
6. tests/AGENTS.md — раздел параллельного запуска присутствует.
7. Сессия «Итоговая проверка девпланов и RC-развёртывание» не затронута: никакие docker-операции на dev-машине вне эксперимента (эксперимент завершён, контейнеры подчищены).

---

## Приложение A — Текст раздела для tests/AGENTS.md (T7)

```markdown
## Параллельный запуск (pytest-xdist) — правила для новых тестов

Платформа запускает тесты через `-n auto` (test_runner.py / core/check-suite.yaml, DevPlan 120 §3.3):
**каждый новый тест обязан работать корректно параллельно.** Одиночный прогон — не исключение,
а частный случай. Правила:

1. **Файлы — только tmp_path.** Запрещены общие пути: `/tmp/*.log`, файлы в tests/, репо-файлы
   (кроме read-only). Общий файл без flock = гонка воркеров (прецедент: clickhouse-compose-up-failed.log).
2. **Docker — только канонические фикстуры** (`platform_services`, модульные фикстуры из _conftest/smoke.py).
   Прямой `docker compose up` в тесте запрещён: несколько воркеров конкурентно поднимают один стек
   (прецедент: 5 passed / 7 errors при -n 2). Стек и сети живут под DockerStackLock (master-секция).
3. **env — через monkeypatch.** Глобальные `os.environ[...]` без отката — межтестовая гонка внутри воркера.
4. **cwd — не менять.** Если нужен chdir — fixture с восстановлением (monkeypatch.chdir), иначе
   параллельные тесты в воркере видят чужой cwd.
5. **Ожидания — канонические wait-фикстуры** (`wait_for_containers_healthy`), не голые `time.sleep`.
6. **xdist_group("serial") НЕ работает** при `-n auto` (load): требует `--dist loadgroup` (test_gate_timeout_literals.py:66).
   Не использовать; при реальной необходимости serial — поднимать вопрос с архитектором.
7. **git-операции — только в tmp-репозитории** (tmp_path + git init). Мутации рабочего репо
   (git add/commit/checkout, правка tracked-файлов) — запрещены: параллельные гейты (manifests
   git diff) и pre-commit увидят чужие изменения.
8. **Общие глобальные ресурсы** (счётчик .test_counter.json, сети, docker-стек) — только через
   существующие механизмы: flock (_conftest/counter.py, _conftest/docker_lock.py) + master-семантику
   (session-хуки выполняются только в master-воркере, PYTEST_XDIST_WORKER).
```

$START_DEVPLAN
План 124 (данный файл) — полный документ выше.
$END_DEVPLAN
