# State machines integrity audit — persistence & validity

Метод: чтение lifecycle/state_machine.py (931 LOC), state_store.py, cli.py (1164 LOC), phases/preconditions.py, shared/file_lock.py; grep статусов/маркеров по core/internal (cert_orchestrator, deploy/engine, verify_contracts, python_deps). Read-only: файлы + grep + glob; make-цели не запускались. Известные смежные факты (node-identity guard на done-state, дыры UPDATE-DAG φ11/φ12, --run-phase вне mode, false idempotency φ1/φ7) не дублируются.

---

## DATA-801: Конкурентный bootstrap — lock покрывает только запись, весь run работает с несинхронизированным in-memory снимком
- **Severity:** HIGH · **Confidence:** HIGH
- **Files:** `core/internal/bootstrap/lifecycle/state_store.py`, `core/internal/bootstrap/lifecycle/cli.py` · **Symbols:** `load_state`, `save_state`, `StateMachine.__init__`, `_run_phases`, `_mark_phase_success` · **Invariant:** два конкурентных процесса не должны терять чужие переходы (checkpoint = source of truth)
- **Violating scenario:** START: P1 (`--mode init`) и P2 (`--mode update`, CI node-update) оба вызывают load_state без lock → каждый держит свой BootstrapState до конца процесса. P1 доводит φ4→done и save; P2 (stale-снимок) завершает свою фазу и save → last-writer-wins затирает done(φ4). END: state.json ≠ reality — фаза выполнена на ноде, маркера нет → повторный deploy при следующем run; при пересечении deploy-фаз — параллельный docker compose одного стека. Авторы сами фиксируют класс гонки «bootstrap + node-update параллельно» в rationale T9.2, но починен только tearing tmp-файла, не lost-update.
- **Evidence:** `state_store.py:263-270` — load читает файл БЕЗ lock; `state_store.py:318-320` — flock только вокруг записи (30s timeout); `cli.py:432` + `_run_phases:770-856` — один sm.state живёт в памяти весь прогон, каждый save пишет ЦЕЛИКОЙ снимок.
- **Impact:** потеря checkpoint'ов, двойное выполнение deploy/converge-фаз, ping-pong setup_state (mode/node mismatch reset, state_machine.py:852-862) при разных mode двух процессов.
- **Minimal fix:** lock-сессия на весь run (flock на state.json.lock от __init__ до конца CLI) ИЛИ read-modify-write с повторным load+merge под lock перед каждым save.
- **Required test:** e2e-тест двумя процессами (tmp_path state): P1 mark φN done → P2 mark φM done → assert оба маркера присутствуют.
- **Phase:** fix

## DATA-802: Zombie done — done-маркеры 14 фаз никогда не сверяются с реальностью артефактов
- **Severity:** HIGH · **Confidence:** HIGH
- **Files:** `cli.py`, `phases/preconditions.py`, `state_machine.py` · **Symbols:** `_run_phases` (skip-path), `PRECONDITIONS`, `check_phase`, `_run_liveness_probe`, `phase_needs_rerun` · **Invariant:** status=done ⟹ артефакты фазы существуют на ноде
- **Violating scenario:** START: оператор удаляет `/etc/letsencrypt/live/<domain>` (или secrets.env, ~/.docker/config.json, пользователя ci-deploy). `--mode init`: skip-path проверяет ТОЛЬКО status+hash → «already done — skipping» ×9 → liveness probe (docker info + диск) зелёный → exit 0 «no-op». END: state=done, reality=артефакт отсутствует — навсегда: hash-инвалидация (_HASH_INVALIDATED_PHASES) реагирует только на смену кода/node.yaml, но НЕ на исчезновение артефакта.
- **Фазы с неподкреплёнными верификацией done-маркерами:** certificates (серт-файлы), secrets_provision (/opt/platform/secrets/*), registry_auth (docker auth), system_bootstrap (packages/firewall/tor), user_accounts (users/SSH), platform_setup (cron/dirs), converge_services/update, а также deploy_* на уровне артефактов (контейнеры). Preconditions — единственные reality-проверки — это tool/env-checks ДО исполнения и выполняются ТОЛЬКО внутри execute_phase: skip-путь их обходит.
- **Evidence:** `cli.py:797-807` — skip по status без любых проверок; `preconditions.py:301-312` — реестр прекондишенов = which/env/docker-info, ни одной artifact-проверки; `state_machine.py:703` — precondition_check вызывается только при исполнении фазы; `cli.py:1115-1147` — liveness = docker info + disk_usage.
- **Impact:** nginx с битым vhost, деплой без секретов, CI-push канал мёртв — машина рапортует здоровье; детект только внешними healthcheck/e2e-verify по симптомам.
- **Minimal fix:** postcondition-верификация при skip (лёгкий exists-чек ключевого артефакта фазы; fail → сброс в pending) или периодическая reconcile-проходка (аналог converge) перед no-op exit.
- **Required test:** unit: state с done(certificates) + удалённый cert-файл (tmp facts DI) → init должен сбросить статус, не skip.
- **Phase:** fix

## DATA-803: Формат state.json без schema_version; round-trip молча уничтожает неизвестные поля; legacy-«done»-ключ трактуется читателями противоположно
- **Severity:** MEDIUM · **Confidence:** HIGH
- **Files:** `state_store.py`, `state_machine.py` · **Symbols:** `BootstrapState.to_dict/from_dict`, `StepState.from_dict`, `phase_is_done`, `dry_run_plan` · **Invariant:** сериализация обратима; все читатели одного файла согласованы
- **Violating scenario:** (a) Version-skew: новое ядро добавляет поле F в steps; на ноду rsync'ом доставлено старое ядро → load+save (любой фазовый save) молча стрипает F у всех записей — данные уничтожены без ошибки. (b) Legacy-файл с записью `{"name": x, "done": true}` (без status): phase_is_done → done (skip), StepState.from_dict → status="pending" (re-execute), dry_run_plan(dict) → «pending» — три читателя, три ответа на один JSON.
- **Evidence:** `state_store.py:158-167` to_dict — фиксированный набор ключей; `state_store.py:169-186` from_dict — неизвестные ключи игнорируются; поля version/schema нет нигде (grep); `state_machine.py:405-410` принимает `done:true` как done, `state_store.py:130-131` тот же ключ отображает в pending; `state_machine.py:906-914` plan читает только `done`.
- **Impact:** тихая деградация state при даунгрейде/частичной доставке core (канон SCP/rsync это допускает); непредсказуемый re-run фаз после восстановления старого бэкапа state.json.
- **Minimal fix:** поле `schema_version` в to_dict + строгая проверка в load_state (unknown version → StateCorruptError с инструкцией); единый парсер entry (phase_is_done из from_dict-нормализованных данных).
- **Required test:** unit: from_dict→to_dict round-trip сохраняет unknown key → сегодня RED (документирует контракт), после фикса GREEN; тест трёх читателей на legacy-записи.
- **Phase:** fix

## DATA-804: Переходы не закодированы данными: «failed» пишется неисполнявшейся фазе, --run-phase исполняет без persist, RUNNING — мёртвый статус
- **Severity:** MEDIUM · **Confidence:** HIGH
- **Files:** `cli.py`, `state_machine.py` · **Symbols:** `_run_phases` (except-ветки), `_run_single_phase`, `PHASE_STATUS_RUNNING`, `_mark_phase_success/_mark_phase_with_warnings` · **Invariant:** persisted status отражает исполнение фазы; переходы валидируются машиной
- **Violating scenario:** (a) START: φ8 блокируется PhaseDependencyError (φ7=done_with_warnings). END: state φ8=**failed**, хотя код фазы не выполнялся ни разу → аудит/мониторинг/оператор лечат φ8 вместо причины (φ7). (b) `--run-phase X` успешно исполняет фазу и НЕ пишет ни status, ни hash (execute_phase без _mark_phase_success) → reality двинулась, state нет; hash-инвалидация для такой фазы невозможна. (c) Crash между pending и done неотличим от never-started: RUNNING объявлен, но ни разу не присваивается.
- **Evidence:** `cli.py:829-833/840-844/850-854` — entry.status="failed" в ветках Dependency/Precondition/Fatal; `cli.py:366-383` — _run_single_phase: execute_phase + return, 0 обращений к steps; grep: `PHASE_STATUS_RUNNING` определён (`state_machine.py:260`), присваиваний нет; переходы — прямой мутабельный assign в ≥6 местах cli.py без таблицы легальных переходов.
- **Impact:** ложная диагностика отказов, расхождение «исполнено vs записано», невозможность отличить in-flight от упавшего (усугубляет DATA-801).
- **Minimal fix:** data-driven таблица переходов (from_status×event→to_status) с raise на нелегальном; для blocked-by-dependency — отдельный статус `blocked`; _run_single_phase — persist через общий _mark_phase_success/warnings.
- **Required test:** unit: PhaseDependencyError на φ8 → assert статус NOT failed (blocked/pending preserved); --run-phase success → assert status=done + hash записан.
- **Phase:** fix

## DATA-805: Мини-стейтмашины вне lifecycle — ad-hoc механизмы с расходящимися семантиками skipped/failed/retry
- **Severity:** LOW · **Confidence:** HIGH
- **Files:** `cert_orchestrator.py`, `deploy/engine/results.py`, `deploy/verify_contracts.py`, `python_deps.py`, `deploy/orchestrator.py` (DeployHistory) · **Symbols:** `DomainCertResult.status`, `StatusResult.status`, `VerifyReport.state`, `_check_content_hash`, `DeployHistory` · **Invariant:** одинаковые слова о состояниях значат одно и то же; retry-семантика едина
- **Violating scenario:** «skipped»: lifecycle = нейтральный пропуск (TOR-off), cert_orchestrator = УСПЕХ (валидный серт найден) — агент/дашборд, агрегирующий статусы, трактует skip как проблему (или наоборот как успех). «retry»: lifecycle авторетраит ТОЛЬКО исключения (`RETRYABLE_EXCEPTIONS`), False-return → done_with_warnings до следующего ручного прогона; python_deps marker-mismatch → немедленный полный reinstall; cert domain failed → просто в отчёте, без retry. DeployHistory (event-sourcing снапшотов) и state.json (single snapshot) — две философии хранения того же класса данных.
- **Evidence:** `state_machine.py:255-260` vs `cert_orchestrator.py:170-171` (`pending|restored|issued|skipped|failed`) vs `engine/results.py:72` (`found|not_found|stub`) vs `verify_contracts.py:146` (`baseline|proposed|active-full|unmanaged`); `python_deps.py:201-223` — self-migrating marker (единственный пример миграции формата в репо — контраст к DATA-803); `state_machine.py:430` RETRYABLE_EXCEPTIONS.
- **Impact:** когнитивная цена для агентов/операторов: кросс-подсистемный вывод «что упало и что ретраится» требует чтения 5 словарей; cert-failed домен остаётся сломанным при «здоровом» φ7=done_with_warnings — две правды о сертификатах.
- **Minimal fix:** не унифицировать насильно (домены разные), но: общий enum-реестр статусов + docstring-контракт семантики (skip=neutral|success) в одном месте (shared/contracts.py), и общий retry-предикат для cert/deploy путей (уже есть shared/retry.py).
- **Required test:** гейт-тест: каждое статус-слово задокументировано в реестре; grep-тест на новые ad-hoc словари статусов.
- **Phase:** propose

## DATA-806: FileLock молча деградирует в no-lock — единственная гарантия сериализации writers исчезает по PermissionError
- **Severity:** LOW · **Confidence:** HIGH
- **Files:** `core/internal/shared/file_lock.py`, `state_store.py` · **Symbols:** `FileLock._open_fd`, `FileLock.acquire`, `save_state` · **Invariant:** конкурентные writers state.json всегда сериализованы
- **Violating scenario:** START: каталог `.bootstrap/` недоступен на запись процессу P2 (owner/perms drift: root-владелец, CI-пользователь, restore-артефакт). `_open_fd` ловит PermissionError/OSError → WARN + return None → acquire() возвращается без лока → save_state пишет state.json БЕЗ сериализации. END: DATA-801 наступает даже при однопоточном «последовательном» использовании — гарантия, которую даёт flock, выключена молча (WARN в лог, exit 0).
- **Evidence:** `file_lock.py:164-181` — PermissionError/OSError → WARN + None (degrade-контракт задокументирован как dev-machine convenience); `state_store.py:319-320` — lock.acquire() не отличает acquired от degraded (нет API `held()`-check перед записью).
- **Impact:** на ноде (канонический сценарий root+CI) тихое отключение serialization при perms-drift; маскирует ровно ту гонку, ради которой T9.2 строился.
- **Minimal fix:** для state.json-лока degrade запрещён: параметр `required=True` → OSError/FileLockError вместо no-lock (dev-удобство оставить opt-in).
- **Required test:** unit: lock-файл в read-only dir (chmod 555 tmp_path) → save_state должен raise, не писать; сегодня — RED.
- **Phase:** fix

---

### Сводка
| ID | Тема | Sev | Conf |
|----|------|-----|------|
| DATA-801 | Lost-update при конкурентных runs | HIGH | HIGH |
| DATA-802 | Zombie done без верификации артефактов | HIGH | HIGH |
| DATA-803 | Нет schema_version; round-trip strip; разнобой readers | MED | HIGH |
| DATA-804 | Переходы ad-hoc: failed неисполнявшимся, --run-phase без persist, мёртвый RUNNING | MED | HIGH |
| DATA-805 | Расходящиеся словари статусов мини-стейтмашин | LOW | HIGH |
| DATA-806 | Молчаливая деградация lock → no-lock | LOW | HIGH |

Приоритет фикса: DATA-801 → DATA-802 → DATA-803 (801/802 устраняют 80 % сценариев «state ≠ reality»; 803/804 дешёвые и снижают частоту 801/802-последствий).
