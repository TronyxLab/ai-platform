# Deploy Postmortem — почему ~10 циклов «deploy → ошибка → fix» не дали воспроизводимый clean bootstrap

Анализ: git history 2026-08-25 → 2026-09-02 (145 коммитов, планы 011–027), 5 независимых субагентов + прямое чтение bootstrap-пайплайна.

---

## VERDICT

**Current state:**
Система НЕ имеет стабильного свойства «чистый сервер → одна команда → рабочая система». Последняя валидация (027, tronyx-vps) *дотянула* ноду до `healthy` + 3 проекта live + зелёный CI — но это свойство **перезавоёвывалось фикс за фиксом прямо на чистой ноде**, а не воспроизводилось:
- первая попытка cold bootstrap упала (F-01: exit 10, 4 проекта GENERATED-STUB);
- `oldapp skipped=no_local_source` — не все проекты доставлены;
- `make test-node` (E2E на test-VPS) ни разу не гонялся — BLOCKED во всех валидациях 020→027;
- CI main был **красным непрерывно 2.5 недели (с 2026-08-17)** и не блокировал промоуты (нет branch protection);
- «одна команда» верна только как *оркестратор платформенной инфраструктуры*, но молча предполагает состояние оператора и один ручной шаг на сервере.

**Main root causes:**
1. **Неправильный предикат успеха.** «Контейнер healthy» и «фаза done» (liveness/чекпойнт) используются там, где нужен «сервис реально обслуживает» и «желаемое состояние проверено» (readiness/postcondition). Это породило ~14 фиксов.
2. **Bootstrap — чекпойнт-skip, а не реконсиляция.** «Фаза done → пропустить» не проверяет артефакт → дрейф невидим (семейство F-01/F-02/F-09/F-10).
3. **Нет обязательного clean-server acceptance-гейта.** Красный CI 2.5 недели ничего не блокировал → дрейф контрактов (T2.0a → smoke) копится незаметно.
4. **Fail-soft на критических путях** (overlay clone, required-секреты, выпуск сертов) → отчёт об успехе при отсутствующем контексте.
5. **Хрупкий multi-hop транспорт AGE-ключей/секретов** (3 канала, конфликт приоритетов) — теперь fail-loud, но остаётся поверхностью рецидива.
6. **CI-канал — параллельная реализация локального канала** (F-05/F-06/F-07), дрейфует.
7. **Культура симптом-локальных фиксов** (мета-причина): фикс A ломает этап N+1 (re-exec `379fd01`→`e0d0e09`; depends_on `64fe57d`→`86987a9`; R-ssl `F-02→F-03→F-10`).

**P0 blockers (блокируют «чистый сервер → одна команда»):**
1. Node-side overlay deploy-key + SSH-алиас — **ручной шаг на сервере**, а фейл клона — WARN+return 1 (молчаливый успех при отсутствующем overlay).
2. Converge/чекпойнт не верифицирует желаемое состояние (маскирует дрейф).
3. Readiness ≠ healthcheck на холодном старте (langfuse/litellm/loki/hermes).
4. Нет required clean-server гейта — красный CI не блокирует промоут.
5. Отсутствие required-секретов → тихая деградация в «autogen-only» (placeholder-креды), пост-чек завязан на наличие enc-файла.

**Minimal path to DONE:**
1. Fail-loud вместо silent-success в 3 fail-soft точках (overlay clone / required secrets / cert issuance).
2. Converge = «проверь желаемое состояние, потом действуй» (postcondition на критических юнитах), не «фаза done → skip».
3. Readiness-гейт на деплой-пути (ждать, пока зависимый сервис реально обслуживает, не только healthy).
4. Обязательный clean-server гейт: required platform-test на main + дрилл «destroy → bootstrap from zero».
5. Перенести node-side overlay deploy-key из ручного шага в фазу/`make new-context` (иначе fail-loud).

Подробно: [`root-causes.md`](root-causes.md) · [`deploy-timeline.md`](deploy-timeline.md) · [`bugfix-taxonomy.md`](bugfix-taxonomy.md) · [`minimal-fix-plan.md`](minimal-fix-plan.md).

---

## Разбор полётов — почему 10 циклов не дали результата (3–7 фундаментальных причин)

Не перечисляя все ошибки — это подтверждённые историей причины:

1. **Bootstrap не является транзакционным процессом с контрактами между фазами.** Фазы имеют предусловия, но не имеют *пост-условий*: фаза может быть `done` при частичном результате, следующая фаза ест частичный результат (F-01: φ8 читает stub `ai-platform.yaml` от converge R3 → 0 vhosts). `state.json` — чекпойнт-флаги, а не верифицируемое состояние.

2. **Readiness подменён healthcheck.** Единственный сигнал успеха — «контейнер running + health status», его не хватает для зависимостей (langfuse↔clickhouse, litellm, Loki `/ready`, hermes API). Каждая гонка лечится padding'ом `start_period`, а не ожиданием готовности.

3. **Нет обязательного clean-server acceptance-гейта.** Красный `platform-test` с 08-17 не блокировал ничего. Классы 1–2 доезжают до production и обнаруживаются только при следующем cold bootstrap.

4. **Ошибки фиксируются локально (симптом), а не классом.** F-02→F-03→F-10 — три бага одного нового юнита подряд; re-exec — фикс создал следующий P0; depends_on — фикс сломал другой контекст сборки. Каждый фикс не проверялся против инварианта «второй запуск = no-op» и «локальный путь ≠ node/CI путь».

5. **Provisioning зависит от накопленного состояния и молча на него полагается.** Overlay-ключ ручной, clone фейлится мягко, required-секреты при отсутствии enc-файла тихо деградируют. Часть «clean server» в контракте на деле включает operator-side состояние + один серверный шаг человека.

---

## Top-5 root causes (по числу порождённых багфиксов)

| # | Root cause | Downstream (примерно) |
|---|---|---|
| 1 | «healthy/phase-done» ≠ «serving/desired-state verified» | ~14: langfuse/litellm cold-start, F-01, F-02, F-09, F-10, F-13, F-14, DATA-201/205/802 |
| 2 | Bootstrap — чекпойнт-skip, не реконсиляция | ~10: F-01, φ8 exit 10, strict-init `5aa2ea1`, DATA-101/801/803/804 |
| 3 | Хрупкий AGE-key/секрет транспорт | ~12: phi1/phi3/phi4, F-05 node_detect, `9d36691`/`96b42c3`, DATA-1001..1006 |
| 4 | Симптом-локальная культура фиксов (мета) | F-02→F-03→F-10, re-exec→argv, depends_on→revert |
| 5 | CI-канал ≠ локальный канал (дрейф) | ~6: F-05, F-06, F-07, F-12, DATA-1001, channel-pin `688055c`/`fa30c22`/`e5d76fa` |

---

## Карта проблем (сводная)

| Root cause | Симптомы | Коммиты/находки | Повторялось | Критичность |
|---|---|---|---|---|
| Нет postcondition/readiness | langfuse exit, Loki 503, hermes reset, «Vhosts rendered» при 0 vhosts | `64fe57d`/`86987a9`, `6f08f9e`, F-13/F-14, `308cbef` | Да | P0 |
| Чекпойнт-skip маскирует дрейф | stub expose → 0 vhosts, absent module → «no action», cert не восстановлен, R-ssl mutated каждый раз | F-01, F-02, F-09, F-10, DATA-802 | Да | P0 |
| AGE-ключ multi-hop | φ4 «no identity», multi-line env, digest-сага | `d1337ab`, `41ddd6c`, `3358f98`, `9852633`, `081ffe6`, `fc515c1` | Да (6 фиксов) | P0→P1 (уже fail-loud) |
| CI дрейф | gitleaks silent, SSH_OPTS literal, quoted args | F-05, F-06, F-07, `2419325` | Да | P0 (сейчас green) |
| Fail-soft на критике | overlay clone WARN, required secrets autogen-only, cert «converged» при None | `context_overlay.py`, `helpers/secrets.py:224`, `379fd01` | Да | P0 |
| Нет required clean гейта | красный CI 2.5 нед | F-13 | Да | P0 (процесс) |
| Идемпотентность node-update | R-ssl mutated → rc=1 | F-10 `848576a` | Да | P1 |

Детальная карта и drill-down — в [`root-causes.md`](root-causes.md).
