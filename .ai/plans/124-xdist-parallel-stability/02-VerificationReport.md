# 02-VerificationReport.md — 124: ревью DevPlan xdist-параллельной стабильности

<!-- GREP_SUMMARY: verification-report, 124, xdist, flock, xdist-false, superposition, docker, performance, AGENTS.md-compression, race-diagnosis -->
<!-- STRUCTURE: ▶ $ARTIFACT_CONTRACT → ◇ Факты (SHA, что проверено) → ◇ Критика F1-F10 → ◇ Суперпозиция A1/A2/A3 → ◇ Производительность → ◇ Сложность тестов → ◇ Сжатие правил AGENTS.md → ⎋ Вердикт -->

# region MODULE_CONTRACT
## @purpose  Критическое ревью DevPlan 124 (xdist-параллельная стабильность): диагноз гонки, дизайн волны 2 (flock vs xdist:false), влияние на производительность и сложность тестов, сжатие правил в tests/AGENTS.md. Вход для правок Архитектора.
## @scope    .ai/plans/124-xdist-parallel-stability/01-DevPlan.md, tests/AGENTS.md §Параллельный запуск, tests/_conftest/{session,smoke,networks,counter}.py, core/check-suite.yaml, core/internal/{check_suite,test_runner}.py, tests/test_smoke_redis.py, tests/test_component_hermes.py, git-история
## @invariants
##   1. Ревью — не фикс: находки адресуются Архитектору через правки DevPlan
##   2. Все утверждения подтверждены evidence (файл:строка или git-история)
##   3. Рекомендации минимизируют новый код и новые точки флаков (позиция пользователя: ограниченные ресурсы)
## @rationale  Пользователь запросил максимальную критику девплана, проверку очевидности правил AGENTS.md (10 строк → минимум) и раскрытие суперпозиции по стратегии docker-тестов (flock vs последовательно vs xdist:false).
## @changes 2026-08-03 | Ревью до реализации волн 1-3 (код волн 1-3 НЕ написан: docker_lock.py отсутствует, master-guard в session.py отсутствует; в git только T7-документация)
# endregion MODULE_CONTRACT

## $ARTIFACT_CONTRACT

| Поле | Значение |
|------|----------|
| **PURPOSE** | Оценить DevPlan 124 со всех сторон: достоверность диагноза, эффективность дизайна волны 2, влияние на производительность/сложность тестов, сжатие правил tests/AGENTS.md до неочевидного минимума |
| **DESCRIPTION** | 10 находок (F1-F10) + суперпозиция из 3 вариантов docker-стратегии (A1 flock / A2 xdist:false / A3 единая docker-сессия) с анализом пиковой нагрузки и wall-clock + конкретный сжатый текст раздела tests/AGENTS.md |
| **RATIONALE** | Диагноз гонки в плане контаминирован порт-конфликтами dev-машины (F1); волна 2 (flock) не закрывает гонку полностью (F2) и дороже конфиг-фикса (F3); правила AGENTS.md на 70% состоят из очевидной pytest-гигиены (F6) |
| **ACCEPTANCE_CRITERIA** | (1) Архитектор получил конкретные правки DevPlan; (2) предложен текст раздела AGENTS.md ≤6 строк; (3) суперпозиция отвечает на вопрос «пик производительности vs последовательный запуск» |
| **IMPLEMENTS** | Запрос пользователя 2026-08-03: проверка девплана, очевидность правил, сложность/производительность тестов, суперпозиция по docker-тестам, максимальная критика |
| **IMPACTS** | .ai/plans/124-xdist-parallel-stability/01-DevPlan.md (правки Архитектором), tests/AGENTS.md, core/check-suite.yaml |
| **REQUIRES** | Решение пользователя по суперпозиции (A1/A2/A3); чистая машина для пере-диагноза F1 (не dev-машина — факт 10 плана) |

---

## Факты ревью

🔒 Verified against SHA `1de82856cffe5659a5323f08c4d0ebd953d28581` (дерево чистое).

Состояние реализации на момент ревью:
- Волны 1-3 НЕ написаны: `tests/_conftest/docker_lock.py` отсутствует; в `tests/_conftest/session.py` нет `PYTEST_XDIST_WORKER`-guard (sessionstart:120, sessionfinish:275 — без master-семантики).
- Волна 4 (T7) УЖЕ в git: `tests/AGENTS.md:178-191` — раздел «Параллельный запуск» (8 правил) + инвариант 10 (строки 18-19).
- Проверенные механизмы: `check_suite.py:22` — «pytest-чеки строго последовательно (1 pytest с -n auto за раз), static-чеки параллельно в потоках»; `check_suite.py:422` — `TEST_NO_XDIST=1` отключает xdist; `check-suite.yaml:136` — прецедент `predeploy-docker xdist: false`; `check-suite.yaml:145,153` — smoke/component `xdist: true`; `gates-docker` (строки 97-104) — `xdist` не указан → default true (`check_suite.py:329`); smoke/component `diagnostic: false` → в `make check` НЕ входят.
- Модульные фикстуры выполняют собственные compose-операции: `test_smoke_redis.py:68-81` (teardown `docker compose down -v --remove-orphans`), `test_component_hermes.py:52` (TRAP[BUG]: pre-flight down уже убивал общий стек).

---

## Раздел 1 — Критика (находки)

### F1 [HIGH] Диагноз гонки контаминирован порт-конфликтами dev-машины — «гонка подтверждена» преждевременно
`01-DevPlan.md:46` (эксперимент) vs `01-DevPlan.md:53` (факт 10).
План сам фиксирует: на dev-машине поднят prod-стек (порты 80/443), `test_smoke_nginx` **одиночно** = 5 errors (port conflict). Эксперимент `-n 2`: 12 тестов (7 hermes + 5 nginx) → 5 passed / 7 errors. Без per-test разбивки ошибок 5 из 7 консистентны с environmental-фейлом nginx (порт), и лишь ≤2 ошибки могут быть гонкой. «No such container», «unhealthy», «not_found» — тоже консистентны с порт-конфликтом nginx-стека (nginx не поднялся → reuse not healthy). Контроль плана (hermes solo = 7 passed) не контролирует nginx.
**Фикс:** до проектирования блокировок — пере-диагноз на чистой машине (nginx solo → PASS; затем hermes+nginx `-n 2` → зафиксировать per-test breakdown). 30 минут работы, которые могут обнулить волну 2 целиком.

### F2 [HIGH] Волна 2 не закрывает гонку: lifecycle модульных фикстур вне лока
`01-DevPlan.md:74-75` (T2: lock только вокруг platform_services) vs `test_smoke_redis.py:68-81`, `test_component_hermes.py:52`.
Модульные фикстуры (redis_compose, hermes и др.) выполняют собственные `docker compose up`/`down -v --remove-orphans` (teardown redis — с `--remove-orphans`!). Под xdist воркер B, чья module-фикстура пошла по up-пути (стек не был виден как foreign), на teardown снесёт стек, который воркеры C/D в этот момент переиспользуют. Прецедент уже был: TRAP[BUG] test_component_hermes.py:52 — pre-flight down убил контейнеры platform_services. Тот же класс гонки.
**Фикс:** либо lock покрывает и module-fixture lifecycle (up/teardown), либо module-фикстурам запрещается teardown общего стека (флаг ownership в состоянии лока: стек поднял master-секция → teardown только master sessionfinish). В плане это отсутствует.

### F3 [HIGH] flock — не выигрыш, а новая поверхность флаков; xdist:false — похоронен как «fallback»
`01-DevPlan.md:77` (fallback-документация) vs `check_suite.py:22` (pytest-чеки уже строго последовательны), `check-suite.yaml:136` (прецедент), `check_suite.py:422` (механизм TEST_NO_XDIST=1).
Гонка — строго intra-check (12 воркеров внутри ОДНОГО smoke-процесса; между checks они и так не пересекаются). `xdist: false` для gates-docker/smoke/component (3 строки конфига) устраняет гонку на 100%, с нулём нового кода, нулём retry-loop, нулём TOCTOU. flock (новый модуль, retry-loop, double-check, sessionfinish-под-локом, F2-дыра) покупает только параллелизм ассертов (~20-30% wall docker-сьюта). Постановка «flock primary, xdist:false fallback» — инверсия цены/пользы. Требование: явное решение по суперпозиции (см. Раздел 2), не «fallback-документация».

### F4 [MED] Межсессионная гонка docker не рассмотрена
`01-DevPlan.md:75` (lock снимается между секциями) vs `smoke.py:832` (pre-cleanup `down --remove-orphans`).
Лок сериализует только секции lifecycle и снимается между ними. Две параллельные pytest-сессии (агент + агент, агент + пользователь — реальный сценарий при ограниченных ресурсах) на одной машине: master-клинер сессии A сносит активный стек сессии B. План адресует только inter-worker гонку, не inter-session.
**Фикс:** либо session-level docker mutex (удерживается от первого up до master-cleanup), либо явное правило «одна docker-сессия на машину» + TRAP[DEBT]. В рамках 124 достаточно задокументировать + TRAP[DEBT]; без A1-лока проблема остаётся и при A2.

### F5 [MED] Коммит-гигиена: T7-документация влезла в fix(121); документация описывает несуществующий код
`git show 1de8285 --stat`: один коммит = 3 concerns (core-deploy.yml fix 121 + DevPlan 124 + tests/AGENTS.md 124) — нарушение per-wave commit policy. Хуже: `tests/AGENTS.md:185,191` ссылаются на `DockerStackLock` и master-семантику `PYTEST_XDIST_WORKER`, которых в коде нет (docker_lock.py отсутствует, session.py без guard). AGENTS.md, описывающий несуществующее поведение = дрейф по определению (инвариант «AGENTS.md — описание реальности»). Либо правила вливаются вместе с волнами 1-3 (после кода), либо помечаются «target-состояние, реализуется DevPlan 124».

### F6 [MED] Правила AGENTS.md: 8 правил — 5 очевидных; сжать до 3 неочевидных
`tests/AGENTS.md:184-191` vs история: нулевой прецедент нарушения правил 3 (monkeypatch), 4 (cwd), 5 (wait-фикстуры) — это стандартная pytest-гигиена, не платформенное знание; правило 6 (xdist_group) дублирует комментарий test_gate_timeout_literals.py:66. Неочевидны только: docker-фикстуры-канон (гонка стека), xdist_group-фантом, мутации репо/git + общие ресурсы. Предложение сжатия — Раздел 4 (10 строк → 6).

### F7 [LOW] Приёмка счётчика требует намеренно красного прогона — уточнить на чём
`01-DevPlan.md:148` (AC 4: фейл-прогон → Attempt #1). Намеренно красный docker-прогон = лишний подъём стека. Намеренный фейл можно сделать на static-сьюте (дёшево, ~1 мин) — счётчик и static инкрементируется (sessionstart общий). Уточнить формулировку AC.

### F8 [LOW] flock retry-loop без верхней границы
`01-DevPlan.md:74` («retry-loop с логом») — без max-wait. Holder завис (compose retry-циклы 5×30s+) → waiters блокируются до check-timeout (600s) → жёсткий error вместо флака. Нужен bounded wait + fail-fast (например, 2× lifecycle-таймаут, затем fail с понятным сообщением).

### F9 [INFO] gates-docker по умолчанию xdist:true
`check-suite.yaml:97-104` без явного `xdist` → default true (`check_suite.py:329`). Сегодня ~0 тестов (allow_no_tests: true), но первый же docker-gate-тест унаследует гонку. Включить gates-docker в xdist:false-flip (A2).

### F10 [INFO] T5 «master-only сети» противоречит T2 «воркер держит lock»
`01-DevPlan.md:102` vs `01-DevPlan.md:74`. Сети acquire делает воркер, выполнивший platform_services (под локом), release — master в sessionfinish. Функционально сходится на конце сессии, но «master-only acquire» не соответствует исполнению; плюс module-фикстуры зовут `ensure_external_networks` (test_component_hermes.py:24). При A2 (xdist:false) T5 не нужен вовсе: один процесс → refcount в памяти корректен.

---

## Раздел 2 — Суперпозиция: стратегия docker-тестов

Вопрос пользователя: «последовательно от малого к большему? пик производительности как при последовательном запуске?»

**Факты для решения:**
- Один стек (ai-platform-test, один compose project) — docker-тесты физически не могут идти параллельно на одной машине. Пик ресурсов при ЛЮБОМ варианте = 1 стек + static-воркеры. Пик НЕ выше последовательного — это подтверждается: flock сериализует lifecycle, а не ускоряет его.
- `make check` не затрагивается: smoke/component `diagnostic: false` (check-suite.yaml:147,155) — docker-сьюты живут только в `make gate MODE=full`/`ci-docker` и ручных прогонах.
- check_suite уже последователен по pytest-чекам (check_suite.py:22) — межпроцессной гонки docker нет, только intra-process xdist.
- «Последовательно от малого к большему» между ОТДЕЛЬНЫМИ pytest-процессами = N подъёмов стека (каждый ~5-10 мин) — расточительно. Внутри одного процесса это уже реализовано: wave-pipeline (зависимости по module.yaml#depends_on, старт волн по мере готовности — smoke.py:896-911). Новая последовательность между сьютами не нужна; нужна ОДНА сессия (A3).

### Вариант A1 — flock (как в плане, волна 2)
- **Плюсы:** сохраняет `-n auto`; параллелизм ассертов после подъёма стека (экономия ~1-3 мин на docker-сьют).
- **Минусы:** ~100+ LOC нового кода (docker_lock.py, retry-loop, TOCTOU double-check); дыра F2 (module-fixture lifecycle); новые флаки (F8); T5-противоречие (F10); приёмка требует 2+ docker-прогонов (ресурсы пользователя).
- **Когда оправдан:** если появится реальная потребность в параллельных docker-ассертах и будет закрыт F2. Сейчас — нет.

### Вариант A2 — xdist: false для docker-чеков (рекомендуется)
`check-suite.yaml`: gates-docker/smoke/component → `xdist: false` (механизм TEST_NO_XDIST=1 существует, прецедент predeploy-docker:136). Итоговая программа: **T1, T3, T4, T6 + конфиг-флип; T2 и T5 — отменить.**
- **Плюсы:** 3 строки конфига, ноль нового кода, ноль новых флаков; детерминизм; единый процесс → сети/counter корректны без T5; приёмка дешевле (1 docker-прогон, не 2+); возврат к до-120 поведению (single-process docker — проверенный дизайн T3-reuse).
- **Минусы:** ассерты docker-сьюта сериализуются (+1-3 мин к wall full-gate); в `make check` влияния нет (docker diagnostic:false).
- **Риск:** ручные `make test MARKER=smoke` станут серийными — это НЕ регрессия (так было до DevPlan 120).

### Вариант A3 — единая docker-сессия (gates-docker + predeploy-docker + smoke + component в одном serial-процессе)
- **Плюсы:** 1 подъём стека вместо 4 → экономия ~15-30 мин на каждом full-gate; «от малого к большему» внутри одной сессии (wave-порядок).
- **Минусы:** новый runner-код (объединение маркеров, порядок, junit-слияние); пересекается с check-suite архитектурой; приёмка дольше.
- **Когда:** follow-up отдельным DevPlan (волна 5 124 или 125), НЕ сейчас.

**Рекомендация:** A2 как primary (T1/T3/T4/T6 + флип), A3 — follow-up, A1 — только если появятся цифры, доказывающие, что параллелизм ассертов окупает новую поверхность флаков. Решение зафиксировать TRAP[DECISION] в DevPlan (вместо «fallback-документации» T2 task 4).

---

## Раздел 3 — Производительность и сложность тестов

### Производительность
| Сценарий | A1 (flock) | A2 (xdist:false) |
|---|---|---|
| static_audit (3222 теста) | без изменений (~65-105s) | без изменений |
| Пик ресурсов docker-фазы | 1 стек (как последовательно) | 1 стек (как последовательно) |
| Wall docker-сьюта | lifecycle серийный + ассерты ×12 воркеров | всё серийно (+1-3 мин) |
| `make check` | без изменений (docker не входит) | без изменений |
| Новые флаки | да (F8, F2) | нет |
| Ресурсы на приёмку | 2+ docker-прогона (план: AC 1,3,4) | 1 docker-прогон (AC на чистой машине) |

Вывод: пик производительности одинаков (1 стек) — интуиция пользователя верна. flock не даёт выигрыша по пику, только по ассерт-фазе ценой новой сложности и флаков.

### Сложность тестов
- **Авторская (per-test):** не растёт ни в одном варианте — правила для НОВЫХ тестов не добавляют кода (после сжатия F6 — тем более).
- **Conftest:** A1 — MEDIUM (docker_lock.py + retry-семантика + master-guard + сети); A2 — LOW (только master-guard в session.py + 3 строки yaml).
- **Новый unit-тест** test_session_xdist_guards.py нужен в обоих вариантах (T1) — небольшой, mocked env.
- Общий ответ: A2 не усложняет тесты и не замедляет их; A1 усложняет conftest умеренно с реальным шансом новых флаков.

---

## Раздел 4 — Сжатие раздела tests/AGENTS.md (T7, правка для Архитектора)

Текущий текст: `tests/AGENTS.md:178-191` — 8 правил (~10 строк). Очевидное (правила 3, 4, 5 — monkeypatch/cwd/wait-фикстуры) — стандартная pytest-гигиена без прецедентов нарушения, удаляется. Предлагаемый минимум (3 неочевидных правила, ~6 строк):

```markdown
## Параллельный запуск (pytest-xdist)

Запуск через `-n auto` — стандарт (test_runner/check-suite); флак параллельного прогона = баг теста (DevPlan 124). Обязательные правила:

1. **Docker — только канонические фикстуры** (`platform_services`, модульные из `_conftest/smoke.py`). Прямой `docker compose up` запрещён: воркеры конкурентно поднимают один стек (эксперимент 2026-08-03: 5 passed / 7 errors при `-n 2`).
2. **`xdist_group("serial")` НЕ работает** при `-n auto` — не использовать (test_gate_timeout_literals.py:66).
3. **Общие ресурсы не мутируются:** файлы — `tmp_path`; рабочее репо (git add/commit/checkout, tracked-файлы) — read-only; docker/счётчик/сети — через flock + master-семантику (`_conftest/counter.py`; session-хуки — только master, `PYTEST_XDIST_WORKER`). Остальное — стандартная pytest-гигиена.
```

Инвариант 10 (tests/AGENTS.md:18-19) — оставить без изменений (2 строки, уместен).

---

## Раздел 5 — Рекомендуемые правки DevPlan (для Архитектора)

1. **Пере-диагноз F1 на чистой машине** до реализации волны 2: nginx solo → PASS, затем hermes+nginx `-n 2` с per-test breakdown. Если гонка не воспроизведётся — волна 2 пересматривается (возможно, только конфиг-флип).
2. **Заменить T2/T5 на A2**: `xdist: false` для gates-docker/smoke/component; T5 отменить (не нужен при одном процессе); T2-задачу 4 (fallback) переписать как primary-решение с TRAP[DECISION].
3. **T2 → при необходимости A1**: расширить lock на module-fixture lifecycle или запретить module-teardown общего стека (F2); bounded retry (F8).
4. **F4**: задокументировать межсессионную гонку + TRAP[DEBT] (одна docker-сессия на машину) — остаётся и при A2.
5. **F5**: вынести правки tests/AGENTS.md из волны 4 в конец реализации (после кода волн 1-3); разнести 121/124 concerns по коммитам.
6. **AC 4 (F7)**: намеренный фейл — на static-сьюте, не docker.
7. **Раздел 4** — вставить сжатый текст в T7.

---

## Вердикт

**DRIFTED (WARNING)** — план 124 в текущем виде: диагноз гонки недоказан (F1), волна 2 (flock) не закрывает гонку полностью (F2) и дороже простого конфиг-фикса (F3); T7-документация уже в git описывает несуществующий код (F5) и на 70% состоит из очевидной гигиены (F6). Блокеров нет (план реализуем, статика стабильна), но дизайн волны 2 требует пересмотра в пользу A2 до реализации. Пик производительности при flock = последовательному (1 стек) — ожидание пользователя подтверждено; сложность тестов растёт умеренно только в A1.

Делегирование: правки DevPlan 124 — Архитектору (п. Раздел 5); реализация после правок — Coder, волны 1-3; финальная верификация — QA.

$START_VERIFICATION_REPORT
План 124 — ревью завершено, вердикт DRIFTED (WARNING), рекомендуемые правки в Разделе 5.
$END_VERIFICATION_REPORT
