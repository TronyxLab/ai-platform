# Root Causes — drill-down по повторяющимся классам отказов

Метод: Observed failure → Immediate cause → Underlying cause → Systemic cause.
Источники: git 2026-08-25→09-02, находки F-01..F-14 (027), φ/phi-цепочка (020/022), DATA-*/PERF-* (meta-refactoring), прямое чтение `core/internal/bootstrap/*`.

---

## RC-1 · «healthy» ≠ «serving» — нет контракта readiness, отдельного от healthcheck

- **Observed:** langfuse падает на миграции ClickHouse при cold bootstrap; Loki `/ready` 503 «schedulers 0»; hermes `ConnectionResetError(104)` на первом chat-completions; litellm 7 фейлов healthcheck, потом healthy.
- **Immediate:** сервисы стартуют параллельно и объявляются healthy по `State.Health.Status`, ещё прогреваясь.
- **Underlying:** единственный сигнал успеха — «контейнер running + health status»; понятия «реально обслуживает» нет. Порядок выражен `module.yaml#depends_on` + deploy-order, а рантайм-гонка замазывается `start_period: 180s`.
- **Systemic:** каждый рейс лечится *padding'ом окна*, а не ожиданием готовности зависимого. Класс всплывает и в тестах (F-13 Loki, F-14 hermes), и на cold start (langfuse/litellm).
- **Один корневой фикс:** readiness-предикат на деплой-пути — фаза завершается, когда зависимый сервис *обслуживает нужную операцию*, а не когда он «healthy».

## RC-2 · Bootstrap — чекпойнт-skip, а не реконсиляция желаемого состояния

- **Observed:** F-01 (φ8 vhost-render 0/3, 4 проекта GENERATED-STUB, exit 10); F-02 (удалённый серт не восстановлен до R6); F-09 (enabled-модуль absent → R9 «no action needed»); F-10 (R-ssl mutated каждый converge → node-update rc=1).
- **Immediate:** юниты-реконсилеры отсутствуют/статус грубый/absent не трактуется как дрейф.
- **Underlying:** «done/converged» — это *заявление в `state.json`*, а не перепроверка артефакта. Skip-путь смотрит `status + hash`, но не существование артефакта; инвалидация по hash реагирует на изменение кода, не на исчезновение результата.
- **Systemic:** идемпотентность реализована как «фаза done → skip», а не «проверь желаемое состояние, потом действуй». Provisioning зависит от накопленного состояния, которое никогда не ре-валидируется.
- **Один корневой фикс:** пост-условие (verify-desired-state) на критических юнитах φ8/converge; absent/несоответствие = дрейф, а не done.

## RC-3 · Хрупкий multi-hop транспорт AGE-ключей/секретов

- **Observed:** bootstrap умирает на φ4; ключ доходит до lifecycle с неверными байтами. Сага: `3358f98`→`9852633`→`081ffe6`→`fc515c1`→`41ddd6c`→`d1337ab`.
- **Immediate:** ключ пересекает 5+ строковых/бинарных границ, каждая со своей семантикой кавычек/нормализации/re-exec: `node_detect` (env→SOPS_AGE_KEY→FILE→keys.txt→/etc/age/key.txt) → local python → ssh stdin prelude (`bash -s`) → remote env → sops.
- **Underlying:** нет единого типизированного представления «ключа» — он пере-кодируется на каждом хопе, каждый хоп — независимая поверхность багов. Фиксы phi — это диагностика (digest) или нормализация границы, а не пересборка цепи.
- **Systemic:** у секретов нет единого typed transport identity. Три канала (env/file/stdin prelude) с конфликтующим приоритетом + глобальный env-leak + multi-line-небезопасный протокол.
- **Один корневой фикс:** канонизировать один канал (файловый) + fail-loud на любой рассинхрон; digest на границах оставить как теле-метрию. (Уже частично сделано `d1337ab` + fail-loud — остаточный риск P1.)

## RC-4 · CI-канал — параллельная реализация локального канала

- **Observed:** локальный bootstrap/deploy-project работает, CI падает: F-05 (gitleaks v8.30.1 переименовал checksums → silent exit 1), F-06 (`SSH_OPTS` job-level `$RUNNER_TEMP` literal), F-07 (dispatch `split()` не парсит кавычки CI).
- **Immediate:** workflow дублирует логику, которую локальный Python-канал делает иначе (env-расширение, кавычки args, скачивание бинаря, транспорт секретов).
- **Underlying:** `deploy-project.yml`/`core-deploy.yml` — параллельные *реализации* `orchestrator_cli deliver`, а не тонкие обёртки над тем же кодом. Все три P0 — «работает локально, ломается в CI».
- **Systemic:** два кодовых пути дрейфуют; каждый фикс латает CI-путь симптом-локально.
- **Один корневой фикс:** CI-шаги = тонкая обёртка над локальным каналом (минимум — required CI E2E-гейт, чтобы дрейф не копился; полная унификация — P1).

## RC-5 · Silent failure / false-green (успех заявлен, а не проверен)

- **Observed:** F-01 (stub → 0 vhosts, отрендерено «успешно»), F-05 (gitleaks exit 1 с 0 строк вывода), `308cbef` (restore black-hole: rc=0 над пустым кластером), `6f08f9e` (silent-0 vhost).
- **Immediate:** exit-статусы и success-логи не привязаны к реальному результату; успех утверждается завершением пути.
- **Underlying:** нет пост-условий; «успех» — это *заявление*, а не *проверка*. Правило «ALL errors visible» документировано, но не enforced на деплой-пути.
- **Systemic:** это наблюдаемая грань RC-1/RC-2 — ничто не верифицирует «операция произвела желаемый артефакт/состояние».
- **Один корневой фикс:** пост-условный гейт на критических путях (fail-closed на отсутствие артефакта/rc).

## RC-6 · Fail-soft на критических путях → clean-server контракт нарушен

- **Observed:** bootstrap репортит success при отсутствующем overlay/секретах/сертах.
- **Immediate:** `context_overlay.py:285-295` clone fail → WARN+return 1 (non-fatal); `helpers/secrets.py:224` required∧sops пост-чек гейтится наличием enc-файла; `preflight.py` DNS WARN-only.
- **Underlying:** не хватает различия «деградация допустима» vs «контракт нарушен»; нарушение контракта обязано быть hard-error.
- **Systemic:** «clean server» в контракте на деле включает operator-side состояние + один ручной шаг на сервере (node-side overlay deploy-key), и pipeline на это молча полагается.

## RC-7 · Нет обязательного clean-server acceptance-гейта

- **Observed:** `platform-test` красный с 2026-08-17 (redis NOAUTH ×3 + Loki `/ready`), никого не блокирует.
- **Immediate:** DevPlan 010 T2.0a (requirepass + loopback facade) не обновил smoke-контракт; красный сигнал не гейтит промоуты.
- **Underlying:** нет механической связи «инвариант изменён» → «все потребители обновлены»; parity-гейты покрывают generated files, но не runtime-ожидания smoke.
- **Systemic:** нет required clean-server сигнала — дрейф контрактов копится невидимо 2.5 недели, и классы RC-1/2/4 доезжают до production.

---

## Ранжирование системных причин по числу порождённых фиксов

1. **RC-1 «healthy ≠ serving»** — ~14 downstream (самый высокий рычаг; причина, почему цель падает только на bare metal).
2. **RC-3 AGE-транспорт** — ~12 downstream (наибольший security-impact, сопоставимо).
3. **RC-2 чекпойнт-skip** — ~10 downstream, питает RC-6.
4. **RC-5 silent-success** — 13 поверхностей (мета-грань RC-1/2).
5. **RC-4 CI-дрейф** — ~6 downstream.
6. **RC-7 нет гейта** — энэйблер (не спавнит напрямую, но делает детект отложенным).

## Полная карта проблем

| Root cause | Симптомы | Коммиты/находки | Повторялось | Критичность | Что исправить |
|---|---|---|---|---|---|
| healthy ≠ serving | langfuse exit, Loki 503, hermes reset, false healthy | `64fe57d`/`86987a9`, F-13/F-14, `0260235` | Да | P0 | readiness-гейт на деплой-пути |
| чекпойнт-skip маскирует дрейф | stub→0 vhosts, absent→no action, cert не восстановлен, R-ssl mutated | F-01, F-02, F-09, F-10, DATA-802 | Да | P0 | postcondition/verify-desired-state |
| silent-success | rc=0 над пустым кластером, silent vhost, gitleaks silent | `308cbef`, `6f08f9e`, F-05, `61b942f` | Да | P0 | fail-closed на отсутствие результата |
| AGE multi-hop транспорт | φ4 no identity, multi-line env, digest-сага | `d1337ab`, `41ddd6c`, `3358f98`, `9852633`, `081ffe6`, `fc515c1` | Да | P0→P1 | единый канал + fail-loud |
| fail-soft на критике | overlay WARN, autogen-only, cert «converged» при None | `context_overlay.py`, `helpers/secrets.py:224`, `379fd01` | Да | P0 | hard-error на нарушение контракта |
| CI-дрейф | gitleaks, SSH_OPTS literal, quoted args | F-05, F-06, F-07, `2419325` | Да | P0→P1 | CI = обёртка локального канала / required CI гейт |
| нет required clean гейта | красный CI 2.5 нед, промоуты шли | F-13 | Да | P0 | required platform-test + destroy-дрилл |
| идемпотентность node-update | R-ssl mutated каждый converge | F-10 `848576a`, `6094933` | Да | P1 | честный статус-маппинг (сделано) |
| restore black-hole | rc=0 пустой кластер | `308cbef` | Нет | P1 | expected-DB пост-чек (сделано) |
