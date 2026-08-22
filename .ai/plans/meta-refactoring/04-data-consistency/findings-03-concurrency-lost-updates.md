# Concurrency & lost updates audit

Метод: статический анализ RMW-паттернов (`yaml.safe_load/json.load` × `dump/write`) в core/internal + трассировка lock-покрытия (FileLock/acquire_flock/flock) по каналам деплоя (CI receive, deploy-project, bootstrap/node-update/converge). Для каждого HIGH — интерливинг двух операций с конкретным потерянным обновлением. Read-only; make-гейты не запускались.

## DATA-301: NodeYaml-мутации (add/remove/update project/context) — RMW без lock
- **Severity:** HIGH · **Confidence:** high
- **Files:** core/internal/shared/node_yaml/projects.py · domains.py · _core.py:309-373 · core/internal/shared/project_registry.py:185 · scaffold/project_remover.py:196 · scaffold/context_registry.py:64 · shared/node_yaml/cli.py:456-488 · **Symbols:** `ProjectsMixin.add_project/remove_project/update_project`, `DomainsMixin.add_context`, `NodeYamlCore._write_back` · **Invariant:** мутация node.yaml атомарна относительно других мутаторов (нарушена: атомарна только одиночная запись, не RMW)
- **Violating scenario:** START: два `make new-project A --register` и `make new-project B --register` (или new-project × remove-project). Оба: `deepcopy(self._load())` → projects[] БЕЗ чужой записи → mutate → `_write_back` (atomic_writer, но целиком). END: последний writer перезаписал весь файл — проект A/B исчез из `node.yaml`: реестр потерян → vhost/мониторинг не рендерятся, `project-list` врёт. Атомарный `_write_back` защищает от tearing, НЕ от lost update.
- **Evidence:** projects.py:204-241 (`add_project`: deepcopy→mutate→write_back, без FileLock); _core.py:325-352 (_write_back атомарен, lock отсутствует); grep FileLock по node_yaml/ = 0 совпадений.
- **Impact:** тихая потеря регистрации проекта/контекста в каноническом реестре ноды.
- **Minimal fix:** обернуть мутации в `FileLock(<node.yaml>.lock, timeout=30)` внутри `_write_back`-вызова (единая точка) или в add_/remove_/update_ перед load.
- **Required test:** два процесса × N итераций add_project разных имён на один node.yaml → финал содержит оба имени (сейчас флакует).
- **Phase:** P1

## DATA-302: Deploy-lock молча деградирует в no-lock (PermissionError на существующем root-owned lock-файле)
- **Severity:** HIGH · **Confidence:** high
- **Files:** core/internal/shared/file_lock.py:164-180,189-199 · deploy/orchestrator.py:295-297 · deploy/audit/history.py:194 · **Symbols:** `FileLock._open_fd/acquire/release`, `platform_lock_path` · **Invariant:** успешный `acquire()` = взаимное исключение (нарушено: acquire() при деградации возвращает успех без замка)
- **Violating scenario:** bootstrap φ8 деплоит проекты как root → создаёт `/var/lock/platform-deploy-{X}.lock` mode 0644 owner=root. Далее CI receive (forced-command, user ci-deploy): `os.open(O_RDWR)` на чужой 0644-файл → PermissionError → `_open_fd`=None → WARN в лог → `acquire()` завершается УСПЕХОМ как no-op. START: два параллельных receive проекта X (два PR merged одновременно): оба деградируют → оба проходят guard → interleaved: unpack payload2 поверх payload1, compose up ×2, оба create_snapshot+prune. END: compose-стек от смеси двух версий; audit показывает два «успешных» деплоя. Дополнительно `release()` без matching `acquire()` декрементирует чужую глубину `_REENTRANT` (file_lock.py:253-260) — преждевременное снятие чужого замка в одном процессе.
- **Evidence:** file_lock.py:168-175 (`except PermissionError … return None`), :196-199 (`fd is None → return` без ошибки); orchestrator.py:297-298 ловит только FileLockError; chmod/chown lock-файлов в core/ отсутствуют (grep = 0).
- **Impact:** потеря единственной сериализации деплоя per-project именно в сценарии «root создал, ci-deploy читает».
- **Minimal fix:** (a) деградация только если файл ЕЩЁ не существует (EACCES на существующем = fatal); (b) создавать lock 0o666/umask-агрессивно или в каталоге с setgid; (c) release() декрементирует только если этот инстанс делал acquire.
- **Required test:** root-created 0644 lock file + non-root acquire → ожидание FileLockError, сейчас — тихий success (регресс на текущее поведение).
- **Phase:** P1

## DATA-303: bootstrap/node-update без run-level lock — интерливинг фаз, потеря чекпоинтов, двойной ACME-issue
- **Severity:** HIGH · **Confidence:** medium-high
- **Files:** core/internal/bootstrap/lifecycle/state_machine.py:487-507 · state_store.py:315-341 · lifecycle/cli.py · phases/system.py:1080-1130 (_run_converge) · cert_orchestrator.py:427-500 · **Symbols:** `StateMachine.__init__/run`, `save_state`, `_run_converge`, `_process_single_domain` · **Invariant:** выполнение пайплайна фаз сериализовано на ноде (нарушено: сериализована только запись state.json)
- **Violating scenario:** START: CI auto `make node-update` (φ9-φ13) и оператор `make bootstrap-node` одновременно по двум SSH-сессиям. Оба грузят state.json один раз (`load_state` в `__init__`), гоняют фазы параллельно: двойной `docker compose up` платформенных модулей; двойной cert_orchestrator → два acme.sh issue одного домена (Let's Encrypt duplicate-cert limit 5/week → fallback self-signed). Каждый пишет ЦЕЛИКОМ свой in-memory BootstrapState под lock: session A сохранила {certificates: done}; B, загруженный до этого, сохраняет свой снимок без A-прогресса. END: φ7-done потерян → следующий run переисполняет φ7 (повторный ACME-hit); current_step противоречив.
- **Evidence:** grep flock/FileLock по lifecycle/ = только state.json.lock (state_store.py:319); cli.py/main без acquire_flock; converge внутри фаз идёт через converge.sh→converge.py (flock есть), но остальные фазы (deploy-modules, cert, secrets) — без общего run-lock.
- **Impact:** гонки инфраструктурных мутаций ноды + потеря checkpoint'ов (переисполнение дорогих/лимитируемых фаз).
- **Minimal fix:** глобальный `/var/lock/platform-lifecycle.lock` (non-blocking) вокруг run_init_mode/run_update_mode; отказ = явный «lifecycle busy».
- **Required test:** два конкурентных StateMachine.run на общем tmp state.json → все step-status обоих writers присутствуют в финальном файле.
- **Phase:** P2

## DATA-304: Lock-namespace split у converge (/var/lock vs /tmp fallback) + отсутствие взаимного исключения deploy×converge
- **Severity:** MED · **Confidence:** high
- **Files:** core/internal/bootstrap/converge.py:82-83,180-217 · shared/file_lock.py:307-310 · **Symbols:** `LOCK_FILE_DEFAULT/LOCK_FILE_FALLBACK`, `acquire_flock`, `platform_lock_path` · **Invariant:** все процессы одного ресурса используют один lock-файл (нарушено: путь зависит от окружения процесса)
- **Violating scenario:** (a) root-процесс берёт /var/lock/platform-converge.lock; процесс с недоступным /var/lock (restricted user/container mount) молча падает на fallback /tmp/platform-converge.lock → оба «сериализованы», реально пересекаются. (b) Даже при одинаковом пути: converge держит platform-converge.lock, а активный `receive`/deploy-project держит platform-deploy-{p}.lock — РАЗНЫЕ файлы: reconciler R-units перезапускает контейнеры проекта посреди его compose up → частично применённый стек + ложный healthcheck-fail → rollback здорового деплоя.
- **Evidence:** converge.py:22 («mkdir-fail fallback → /tmp»), :82-83; grep: deploy-путь только platform_lock_path, общий lock между orchestrator.py:295 и converge.py отсутствует.
- **Impact:** ложная уверенность в сериализации; race deploy×converge на живом проекте.
- **Minimal fix:** fallback убрать (fail-fast вместо /tmp); для (b) — converge берёт deploy-locks затронутых проектов (read-intent) или единый node-lock для мутаторов.
- **Required test:** два процесса с разными LOCK-resolve путями → ровно один входит в критическую секцию (сейчас оба).
- **Phase:** P2

## DATA-305: persist_project_key — незащищённый неатомарный RMW хранилища LLM-ключей
- **Severity:** MED · **Confidence:** high
- **Files:** core/internal/llm/key_provisioner.py:366-396 · **Symbols:** `persist_project_key`, `get_default_persist_path` · **Invariant:** JSON-store переживает конкурентные записи и crash (нарушена обе)
- **Violating scenario:** START: node-update φ11 (llm-keys, N проектов) и ручной `make provision-llm` concurrently. Оба: json.load(store) → store[имя]=ключ → `open("w")` (truncate!) → json.dump. END: (1) lost update — ключ проекта из медленного writer стёрт; (2) crash/CPU-preemption между truncate и dump → пустой/обрезанный JSON → следующий reader (key_provisioner.py:372-378) логирует WARNING «overwriting» и затирает ВСЕ ключи. Плюс chmod(0600) ПОСЛЕ закрытия — окно plaintext-ключей 0644.
- **Evidence:** key_provisioner.py:392-393 (`open("w")+json.dump`, не atomic_writer), :368-378 (corrupt → overwrite-all), lock отсутствует (grep FileLock llm/ = 0).
- **Impact:** потеря virtual keys всех проектов одним повреждением файла; retry-деплой проектов с битым ключом.
- **Minimal fix:** atomic_write_json + FileLock(persist_path.lock, timeout=10); corrupt → fail-fast, не overwrite.
- **Required test:** 2 потока × persist разных ключей → оба в файле; kill между open("w") и dump → старый store читаем (atomicity).
- **Phase:** P1

## DATA-306: Локальные генераторы без lock и без atomic write — межмашинный конфликт не сериализуется ничем
- **Severity:** LOW-MED · **Confidence:** high
- **Files:** core/internal/scaffold/vhost_configurator.py:129-148 · scaffold/scaffold_helpers.py:210-213 · scaffold/gen_env_platform.py:501 · **Symbols:** `update_yaml_for_vhost/_plw_body_*`, `gen_ai_platform_yaml`, CLI write_text · **Invariant:** локальные мутации репозитория сериализованы или атомарны (нарушены обе)
- **Violating scenario:** flock машинно-локален: (a) разные машины операторов — lock невозможен by design, конфликт уходит в git (push reject non-ff — штатно, см. ниже); (b) ОДНА машина, два терминала: `make adopt-project` (update_yaml_for_vhost: load ai-platform.yaml → yaml.dump в тот же файл через `open("w")`) параллельно с ручной правкой needs.domain → перезапись чужой правки; .env.platform `write_text` (не atomic) → обрыв = пустой файл контракта окружения.
- **Evidence:** vhost_configurator.py:142-143 (`open("w")+yaml.dump`); scaffold_helpers.py:210-213 (то же); gen_env_platform.py:501 (`write_text`); ни atomic_writer, ни FileLock (grep = 0 по scaffold/).
- **Impact:** умеренный: single-operator workflow каноничен; но контрактные файлы (.env.platform) при обрыве записи теряются целиком.
- **Minimal fix:** перевести три writer'а на shared/atomic_writer; для same-machine гонок — опциональный FileLock рядом с файлом.
- **Required test:** concurrent update_yaml_for_vhost × 2 с разными domain → финал валидный YAML, один из доменов (не мусор).
- **Phase:** P3

## Замечания вне топ-6 (кратко)

- **Git-push гонки (item 6):** GitHub нативно отвергает non-ff push; workflows (.github/workflows/deploy-project.yml) force-push не выполняют; pre-push hook гейтит локально. Delivery-канал консистентен: гонка разрешается отклонением push, а не потерей данных. Risk остаётся только в ручном `git push --force` — вне кода.
- **file_lock.py прочее:** reentrancy-depth, переживающий exception-path, — известный факт (не дублируется). EINTR безопасен (PEP 475 auto-retry); unlink-гонки отсутствуют (lock-файл не удаляется — корректно); flock выбран осознанно против lockf/O_EXCL (kernel-managed release) — корректно.
- **Позитив:** deploy_history.create_snapshot/prune (history.py:194-242) и save_state корректны в своей области (reentrant lock + atomic write).

## Сводка

| ID | Title | Sev | Conf |
|----|-------|-----|------|
| DATA-301 | NodeYaml RMW без lock | HIGH | high |
| DATA-302 | Deploy-lock тихая деградация в no-lock | HIGH | high |
| DATA-303 | Bootstrap без run-lock | HIGH | med-high |
| DATA-304 | Converge lock namespace split + deploy×converge gap | MED | high |
| DATA-305 | LLM key store неатомарный RMW | MED | high |
| DATA-306 | Локальные генераторы без lock/atomicity | LOW-MED | high |

checked: 24 files
