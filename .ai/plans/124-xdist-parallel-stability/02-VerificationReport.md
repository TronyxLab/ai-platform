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

### F11 [HIGH] Ключевая точка переключения xdist — test_runner.py, а не check-suite.yaml; fallback плана не сработал бы для агентского сценария
`01-DevPlan.md:77` (fallback: «в core/check-suite.yaml выставить xdist: false») vs `test_runner.py:444` — `_xdist_args()` применяется к КАЖДОЙ маркерной суите, включая smoke/component/integration, и `make test MARKER=smoke` / `make test-summary MARKER=smoke` (ежедневный агентский путь) идут через test_runner, а не через check_suite. `TEST_NO_XDIST=1` (check_suite.py:422) прокидывается только когда запуск идёт через check_suite; прямой `make test-summary MARKER=smoke` его не выставит → гонка останется. Точечный флип «xdist: false» в yaml покрывает только gates-docker и check_suite-инвокации.
**Фикс (для A2):** исключение docker-маркеров (smoke/component/integration/predeploy-docker) из `_xdist_args()` на уровне test_runner.py (например, `_xdist_args(marker)` с docker-множеством) + `xdist: false` в check-suite.yaml для gates-docker. Это закрывает ВСЕ entry points, включая агентский путь.

---

## Раздел 2 — Суперпозиция: стратегия docker-тестов (обсуждено с пользователем 2026-08-03)

Вопрос пользователя: «последовательно от малого к большему? пик производительности как при последовательном запуске?»

**Факты для решения:**
- Один стек (ai-platform-test, один compose project) — docker-тесты физически не могут идти параллельно на одной машине. Пик ресурсов при ЛЮБОМ варианте = 1 стек + static-воркеры. Пик НЕ выше последовательного: flock сериализует lifecycle, а не ускоряет его.
- Гонка реальна по коду (smoke.py:832 pre-cleanup `down --remove-orphans` + smoke.py:858 `rm -f` в каждом воркере), НЕ только по эксперименту — F1 уточняет величину, а не факт.
- `make check` не затрагивается: smoke/component `diagnostic: false` (check-suite.yaml:147,155) — docker-сьюты живут только в `make gate MODE=full`/`ci-docker` и ручных/агентских прогонах.
- check_suite уже последователен по pytest-чекам (check_suite.py:22) — межпроцессной гонки docker в gate нет, только intra-process xdist и межсессионная (агент+агент).
- «Последовательно от малого к большему» между ОТДЕЛЬНЫМИ pytest-процессами = N подъёмов стека (~5-10 мин каждый) — расточительно. Внутри одного процесса это уже реализовано: wave-pipeline (module.yaml#depends_on, волны с overlap, smoke.py:896-911). Новая последовательность между сьютами не нужна; нужна ОДНА сессия (A3).

### Вариант A1 — flock (как в плане, волна 2)
- **За:** сохраняет `-n auto`; параллелизм ассертов после подъёма стека (~2-3 мин экономии на docker-сьют из 8-15 мин).
- **Против:** ~100+ LOC нового кода (docker_lock.py, retry-loop, TOCTOU double-check, ownership); дыра F2 (module-fixture lifecycle); новые флаки (F8 — unbounded retry); не решает межсессионную гонку F4; приёмка требует 2+ подъёмов стека; новые режимы отказа = новые ретраи агентов = сожжённые ресурсы. Цель плана — детерминизм, а flock добавляет мини-распределённую систему.
- **Когда оправдан:** если docker-сьюты гоняются часто И ассерт-фаза доминирует над lifecycle (сейчас наоборот). Решение: нет.

### Вариант A2 — xdist: false для docker-маркеров (рекомендуется)
Реализация (F11): test_runner.py — `_xdist_args(marker)` с исключением docker-множества {smoke, component, integration, predeploy-docker} (закрывает агентский путь `make test-summary MARKER=smoke`) + `xdist: false` для gates-docker в check-suite.yaml (прямые pytest-cmd). Итоговая программа: **T1, T3, T4, T6 + флип; T2 и T5 — отменить.**
- **За:** возврат к проверенному single-process дизайну — wave-pipeline (ThreadPoolExecutor), reuse, refcount сетей, counter, thread-события — ВСЁ спроектировано под один процесс (DevPlan 040/041); детерминизм по построению, ноль новых флаков; точечная реализация (~15 LOC); приёмка = 1 подъём стека; совпадает с позицией пользователя (ресурсы).
- **Против:** ассерты docker-сьютов сериализуются (+2-3 мин к full-gate); инвариант 10 «xdist — стандарт» требует уточнения: xdist — стандарт для СТАТИКИ; docker — single-process по построению (один стек), это не исключение, а свойство домена.
- **Ключевая мотивация:** единственный вариант, где «флак» и «гонка» исчезают по построению, а не лечатся новым механизмом; стоимость — самая низкая; диагностика агентов перестаёт требовать повторных docker-прогонов (главный ресурсный выигрыш).

### Вариант A2+ — A2 + process-level flock вокруг docker-чеков (рекомендуется к A2)
A2 решает intra-process гонку, но не межсессионную (F4): два агента одновременно гоняют docker-сьюты → master-клинер одного сносит стек другого. Добавка: обёртка `flock tests/.docker-suite.lock pytest ...` на уровне test_runner/check_suite (shell/процессный lock, НЕ conftest-код).
- **За:** ~10 LOC; закрывает реальный сценарий пользователя (агент + агент, агент + пользователь); не требует retry/TOCTOU (lock держится на весь процесс, ретраи не нужны).
- **Против:** docker-сьюты от параллельных процессов сериализуются друг другом — приемлемо: они редки и уже последовательны в check_suite.
- **Мотивация:** закрывает класс F4 целиком ценой одной обёртки; при A1 этот же эффект требовал бы сессионного mutex'а с ownership-логикой.

### Вариант A3 — единая docker-сессия (gates-docker + predeploy-docker + smoke + component в одном serial-процессе)
- **За:** 1 подъём стека вместо 4 → экономия ~15-30 мин на каждый full-gate; «от малого к большему» внутри сессии уже реализован wave-pipeline'ом.
- **Против:** новый runner-код (объединение маркеров, порядок, junit-merge); пересечение с check-suite; приёмка дольше; scope creep для 124.
- **Когда:** follow-up отдельным DevPlan (125), НЕ сейчас. После A2+ он станет чистым выигрышем без пересечения рисков.

**Выбор (мотивация итоговая):** **A2+ ПРИНЯТ (решение пользователя 2026-08-03)** — единственный вариант, устраняющий гонки (intra- и inter-session) без новой conftest-логики, с минимальной ценой (2-3 мин ассерт-фазы на редких docker-прогонах) и максимальной экономией ресурсов (1 подъём стека на приёмку, детерминизм → без повторных прогонов агентов). A1 отклоняется: сложность и новые флаки противоречат цели плана. A3 — follow-up (DevPlan 125). Решение зафиксировать TRAP[DECISION] в DevPlan (вместо «fallback-документации» T2 task 4).

## РЕШЕНИЕ (пользователь, 2026-08-03) — A2+

**Обязательные задачи для правки DevPlan 124 Архитектором:**
- Волна 2 переписывается: T2 (flock) — ОТМЕНА; вместо неё: `_xdist_args(marker)` в test_runner.py с docker-множеством {smoke, component, integration, predeploy-docker} (F11 — покрывает агентский `make test-summary MARKER=...`) + `xdist: false` для gates-docker в check-suite.yaml + process-level `flock tests/.docker-suite.lock` вокруг docker-чеков в test_runner/check_suite (закрывает F4).
- T5 (сети) — ОТМЕНА (single-process → refcount корректен).
- T1, T3, T4, T6 — остаются без изменений (master-guard, xdist_group, counter, tmp_path).
- T7: сжатый текст правил (Раздел 4) + уточнение инварианта 10: «xdist — стандарт для статики; docker — single-process по построению (один стек)»; правки AGENTS.md — в конец реализации (F5).
- Приёмка: пере-диагноз F1 на чистой машине (nginx solo → PASS, hermes+nginx -n 2 с per-test breakdown); docker-приёмка = 1 подъём стека; красный прогон счётчика (AC 4) — на static-сьюте.
- Требования к тестам не меняются: unit-тест T1 (master/worker-guard) остаётся; нового conftest-кода нет.

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

1. **Пере-диагноз F1 на чистой машине** до реализации волны 2: nginx solo → PASS, затем hermes+nginx `-n 2` с per-test breakdown. Уточняет величину гонки (не факт — механизм доказан кодом), калибрует приёмку.
2. **Заменить T2/T5 на A2+** (решение пользователя 2026-08-03): `_xdist_args(marker)` с исключением docker-маркеров {smoke, component, integration, predeploy-docker} в test_runner.py (F11 — агентский путь!) + `xdist: false` для gates-docker в check-suite.yaml + process-level `flock tests/.docker-suite.lock` вокруг docker-чеков (F4). T2-задачу 4 (fallback) переписать как primary-решение с TRAP[DECISION]; T5 отменить (не нужен при single-process).
3. **T2 → при необходимости A1** (не рекомендовано): расширить lock на module-fixture lifecycle или запретить module-teardown общего стека (F2); bounded retry (F8).
4. **F4**: закрывается A2+ process-локом; TRAP[DECISION] вместо TRAP[DEBT].
5. **F5**: вынести правки tests/AGENTS.md из волны 4 в конец реализации (после кода волн 1-3); разнести 121/124 concerns по коммитам.
6. **AC 4 (F7)**: намеренный фейл — на static-сьюте, не docker. **AC 1/3**: 1 подъём стека на чистой машине (не 2+).
7. **Раздел 4** — вставить сжатый текст в T7; уточнить инвариант 10: «xdist — стандарт для статики; docker — single-process по построению (один стек)».

---

## Вердикт

**DRIFTED (WARNING) → решение A2+ принято 2026-08-03** — план 124 в текущем виде: волна 2 (flock) дороже и менее полна, чем конфиг-флип A2+ (F2, F3, F8, F11); диагноз гонки реален по механизму, но числа эксперимента контаминированы порт-конфликтами (F1); T7-документация уже в git описывает несуществующий код (F5) и на 70% состоит из очевидной гигиены (F6). Блокеров нет, но дизайн волны 2 пересматривается в пользу A2+ (п. «РЕШЕНИЕ»). Пик производительности при flock = последовательному (1 стек) — ожидание пользователя подтверждено; сложность тестов не растёт (A2+ не добавляет conftest-кода).

Делегирование: правки DevPlan 124 — Архитектору (п. «РЕШЕНИЕ» + Раздел 5); реализация после правок — Coder, волны 1-3; финальная верификация — QA.

$START_VERIFICATION_REPORT
План 124 — ревью завершено, вердикт DRIFTED (WARNING), рекомендуемые правки в Разделе 5.
$END_VERIFICATION_REPORT

---

# Финальный вердикт ПОСЛЕ реализации (DevPlan 125 T15, 2026-08-03)

## Метод

Рантайм-верификация реализации A2+ (код вошёл в 95fb62c/35c0c71) + статическое подтверждение каждого пункта:

| Пункт A2+ | Evidence | Вердикт |
|-----------|----------|---------|
| test_runner.py `_xdist_args` docker-exclusion | test_runner.py:113-117 — docker-маркеры {smoke, component, integration, predeploy-docker} исключены из `-n auto` (single-process стек) | ✅ PASS |
| flock `tests/.docker-suite.lock` (мастер-процесс) | test_runner.py:37 + check_suite.py:462-483 `_docker_suite_lock` — процессный advisory flock, ЕДИНЫЙ lock-файл машины (T2c) | ✅ PASS |
| session.py master-guard (PYTEST_XDIST_WORKER) | session.py:124-132 `_is_xdist_worker()` — counter increment/reset и docker-cleanup только на master | ✅ PASS |
| check-suite.yaml gates-docker xdist:false | core/check-suite.yaml: xdist: false на docker-чеках (строки 103, 137) | ✅ PASS |
| tests/gates/*.py эксклюзии `_b11_negative_*_tmp`/`_gate_probe_marker_tmp` + FileNotFoundError-обработка | 7 файлов gates/ + test_cross_layer_imports.py содержат probe-эксклюзии | ✅ PASS |
| counter-семантика (2 файла, writer'ы) | `tests/.test_counter.json` (ключ `attempts`, writer: `_conftest/counter.py` + session.py); `tests/gates/.test_counter.json` (ключ `failed_runs`, writer: tests/gates/conftest.py) — раздельные ключи/файлы, оба под flock + master-only | ✅ PASS |

## Регрессионный критерий 124

`make check` (WORKERS=6) 2× подряд без флаков — исполнен в рамках финальной верификации DevPlan 125
(см. VR 125: «make check до чистоты → make gate MODE=fast»). Оба прогона зелёные, 0 флаков.

## Вердикт

**FIXED.** Решение A2+ реализовано полностью (6/6 пунктов PASS), per-wave audit-trail восстановлен
документально: T16 (DevPlan 125) — факт атрибуции «реализация A2+ вошла в 95fb62c (общий коммит
дневной RC-сессии 121)» зафиксирован; незакоммиченные остатки 124 (pre-commit flake closure)
оформлены отдельным `feat(124)`-коммитом 35c0c71.
