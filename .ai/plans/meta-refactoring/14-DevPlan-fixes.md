<!-- GREP_SUMMARY: devplan-fixes QA fix-forward C2-C6 regressions R1-R15 test-gaps waves READY_WITH_WARNINGS -->
<!-- STRUCTURE: ▶ Wave0 baseline-green → ⚡ Wave1 блокеры C2-C6 (6 параллельных) → ⚡ Wave2 регрессии R1-R13 (7 параллельных) → ∑ Track-O drills → ⎋ READY -->

$START_DEVPLAN

## $ARTIFACT_CONTRACT

| Поле | Значение |
|------|----------|
| PURPOSE | Довести мета-рефакторинг (волны 0–4) от вердикта final-QA **NOT_READY** до **READY_WITH_WARNINGS**, закрыв все блокеры C1–C6 и чеклист fix-forward п.1–9; затем до **READY** — закрыв HIGH/MEDIUM-регрессии R1–R13 и выполнив staging/drills |
| DESCRIPTION | Fix-forward план по итогам независимого QA (`.ai/plans/meta-refactoring/13-final-qa/`: CRITICAL.md, REGRESSIONS.md, TEST-GAPS.md, FINAL-VERDICT.md). 3 волны кода + операционный трек. Каждый таск = верифицированная находка QA с точными файлами:строками@HEAD `42679a0`, минимальным дизайном фикса, тест-спецификацией и критерием приёмки |
| RATIONALE | Q: почему отдельный план, а не продолжение 11-DevPlan? A: 11 закрыт леджером (12-StatusReport), QA — новый внешний вход с независимой нумерацией дефектов; отдельный план сохраняет аудит-трейл «аудит → фикс» и не переоткрывает закрытые волны |
| ACCEPTANCE_CRITERIA | SC1: `make check` + `make agent-check` зелёные на HEAD каждой волны; SC2: check-manifests GREEN после T1.5; SC3: все R5-негативы из §$TEST_SPEC существуют и RED без фикса / GREEN с ним; SC4: шаблонные И adopted проекты получают канал от свежего SHA-пина, гейт ловит stale; SC5: READY_WITH_WARNINGS = Волны 0–1 слиты; READY = + Волна 2 + Track O выполнен по release-checklist |
| IMPLEMENTS | Fix-forward чеклист FINAL-VERDICT п.2–9 (п.1/C1 уже разрешён — см. §Debt Intake); TEST-GAPS G1–G8 |
| IMPACTS | ~30 файлов: templates/template-{backend,frontend}/workflows, .github/workflows/deploy-project.yml, scaffold/project_adopter.py, deploy/verify_contracts.py, llm/{key_provisioner,admin_client}.py, entrypoints/core-deliver.sh, bootstrap/core_deliverer.py, shared/ssh_cmd_builder.py, bootstrap/security/sshd_policy.py, lifecycle/secrets_manager.py, shared/secrets_env_parser.py, lifecycle/phases/docker.py + state_machine.py, deploy/deploy_orchestrator.py, converge/runtime.py, watchdog.py, postgres/hooks/on_project_deploy.py, cert_orchestrator/issue_cert, check_suite/{fingerprint}.py, core/secret-definitions.yaml (+generated), AGENTS.md ×2, gate/unit-тесты (~14 файлов) |
| REQUIRES | HEAD `42679a0` (дерево чистое, check-manifests GREEN 2026-08-25T11:21); freeze P3 (leaf-контракты аддитивны); канон: Python-only new code, generated-файлы только через make generate-manifests, тестовая команда агента `make check` |

---

## §Source

Final QA отчёт `.ai/plans/meta-refactoring/13-final-qa/` (FINAL-VERDICT: **NOT_READY**; CRITICAL C1–C6; REGRESSIONS R1–R15+L1–L9; TEST-GAPS G1–G8). Пользовательская постановка: «Создай девплан необходимых фиксов после отчета QA».

## §Debt Intake (Step 0)

| Находка | Решение |
|---------|---------|
| **C1 (WIP 29 файлов)** | **RESOLVED вне плана**: WIP закоммичен как `42679a0` (41 файл, +1886/−180, 2026-08-25T09:41); рабочее дерево чистое; `make check MARKER=check-manifests` GREEN (журнал `logs/make/20260825-112144-check-check-manifests.log`). Остаточный риск «гейт-цикл на коммите не прогонялся» → T0.1 |
| R14 (oracle покрывает 1 манифест из 5) | DEFER → backlog B1: независимость реализована, расширение на 4 манифеста — M×4 при инкрементальной ценности; Rev: после Track O |
| L1–L9 (LOW-хвост) | DEFER → triage-таблица B2 (фикс точечными XS где дёшево: L1, L2, L4; L6 поглощён T1.3; остальные — документировать) |
| zai glm-4.5-flash / macos `DATABASE_URL=""` | Внесплановые, требуют решения владельца → B3. TRAP[DEBT]: macos-оверрайд противоречит инварианту 8 («PostgreSQL во всех окружениях») — владелец обязан либо дать DSN в macos-compose, либо оформить TRAP[DECISION]-исключение |
| Monitoring deep-audit оговорка (FINAL-VERDICT §Coverage) | DEFER → B4 (опциональный аудит-срез, не фикс) |
| DR-offnode-backup Debt (Rev 2026-08-31) | Закрывается операционно: T1.5 (канон AGE_RECIPIENT) + Track O шаг заведения ключа |

## §Requirements Analysis — критерии успеха

1. **Канал деплоя рождается запиненным и свежим** — new-project и adopt-project генерируют workflow от одного актуального SHA; stale-pin невозможен молча (гейт).
2. **L1-deny-set непроницаем для известных векторов** — docker.sock недостижим ни через сервисные volumes, ни через top-level volumes/driver_opts; host-namespace ключи закрыты полностью.
3. **provision-llm переживает transient** — сетевой сбой LiteLLM даёт failed++/WARN по потребителю, не абортит фазу; дубль ключа невозможен ни в одной ветке.
4. **Мастер-ключ никогда не в argv** — локальный `/proc/*/cmdline` любого процесса платформы не содержит секрета (fallback-deliver, prelude-билдеры).
5. **Fail-open хвосты устранены в ядре** — success-маркер/converged только после доказательства (hc_done fresh, orchestrator failed-accounting, converge ps-fail).

## §Size Classification

LARGE по счётчику файлов (>20), но артефакт — одиночный DevPlan по конвенции родительского плана (11-DevPlan.md, flat-file). CONFIRM_BRIEF пропущен: Brief заменяет внешний QA-отчёт с уже зафиксированным скоупом (fix-forward чеклист владельца-метода). ## @rationale: повторнаяCONFIRM-итерация добавила бы цикл без нового входа; scope жёстко ограничен находками QA, ничего сверх.

---

# $TASKS

Оценки: XS ≤20 строк, S ≤100, M ≤300. Каждая волна = один feat-коммит.

## Волна 0 — базлайн (блокирует всё)

### T0.1 · Гейт-цикл на HEAD [S] · closes C1-residual
Прогнать на чистом дереве `42679a0`: `make check` (до чистоты), `make agent-check`.
**AC:** exit 0 обоих; запись в `.ai/logs/runs.jsonl`; иначе — фикс-цикл до зелёного до старта Волны 1.

## Волна 1 — блокеры C2–C6 (параллельная, файлы не пересекаются)

### T1.1 [C2, R9, R8, G6] · Свежий SHA-pin деплой-канала: templates + adopter + гейт [M]
Факты@HEAD: pin `4425ce08…645ed` = коммит **2026-08-18** (комментарий «main snapshot 2026-08-24» ложен);
`git diff 4425ce0..HEAD -- .github/workflows/deploy-project.yml` = **+74/−12** (весь харденинг REF-0011/0012
вне пина). Шаблоны: `templates/template-{backend,frontend}/.github/workflows/deploy.yml:81` (обе).
Adopter: `core/internal/scaffold/project_adopter.py:232,240` — генерирует `deploy-project.yml@main`
(mutable) + tag-pins (`checkout@v7`, `build-push-action@v7`, :206–227) без concurrency; тест
`tests/unit/test_project_adopter.py:483` закрепляет статус-кво. R8: job receive в
`.github/workflows/deploy-project.yml:93` без `timeout-minutes` (дефолт 360).

Дизайн:
1. Новый SoT-модуль `core/internal/scaffold/channel_pin.py`: `DEPLOY_CHANNEL_PIN = "<HEAD-SHA>"`,
   `PIN_COMMENT = "main snapshot YYYY-MM-DD (REF-0012)"` — единственный источник пина для adopter'а.
2. Оба шаблона: перепинить :81 на HEAD `42679a084ff7b3a1281a4543e336f57cbc687875` + честный комментарий;
   обновить @changes-контракты шапки.
3. Adopter: `uses …@{DEPLOY_CHANNEL_PIN}` вместо `@main`; добавить top-level `permissions: contents: read`
   + `concurrency: {group: deploy-${{ github.workflow }}, cancel-in-progress: false}` (паритет шаблона,
   REF-0011 — сериализация caller'а, т.к. top-level concurrency reusable-файла при uses не действует);
   actions перевести на те же SHA-pins, что в шаблоне (:56,:59,:62,:69 template-backend).
4. `deploy-project.yml` job receive: `timeout-minutes: 30` (parity core-deploy; зависший receive больше
   не держит очередь проекта 360 мин).
5. Гейт G6 в `tests/gates/test_gate_workflow_sha_pins.py`: freshness-критерий офлайн — для КАЖДОГО
   `deploy-project.yml@<40hex>` в templates/*/ и в исходнике project_adopter.py (и для значения из
   channel_pin.py):
   `git merge-base --is-ancestor $(git log -1 --format=%H -- .github/workflows/deploy-project.yml) <pin>`
   → иначе RED; комментарий-дата ≥ date(last-touch-commit) — защита от лжи в комментарии (fixture :370).
6. Тесты: негативы «stale-pin → RED», «комментарий-дата раньше последнего изменения файла → RED»;
   обновить `test_project_adopter.py:483` (генерация содержит SHA-pin + concurrency + permissions).

**AC:** grep `4425ce0` по templates/ и scaffold/ пуст; `make check TEST_FILE=tests/gates/test_gate_workflow_sha_pins.py`
зелёный с новыми негативами; искусственная подмена пина на родителя last-touch-коммита делает гейт RED.

### T1.2 [C3] · verify_contracts: top-level volumes + расширенный deny-set [M]
Факты@HEAD: `_check_dangerous_volumes` (`verify_contracts.py:811`) читает только `services[].volumes`;
top-level секция доступна (doc уже парсится целиком — `networks` уходит в `_check_external_networks`,
вызов :343 в `verify_project_contracts:287`). Все 3 вызывающих — проектный payload
(receive_flow.py:295, orchestrator.py:113, orchestrator_cli.py:438, l1_only=True): платформенный стек
(postgres driver_opts-bind'ы) этим путём НЕ сканируется — глобальное deny безопасно для проектов.

Дизайн:
1. `_check_top_level_volumes(top_volumes)` → L1-violation при ЛЮБОМ непустом `driver_opts` у named-volume
   определения (легитимных bind-driver_opts в проектах нет — канон персистентности «named docker-managed
   volume»; device-сокеты ловятся тем же правилом + defense-in-depth сообщение с именем device).
   Провести через `_RawFinding("dangerous-volumes", KLASS_L1, …)`.
2. Расширить `_HOST_MODE_VALUE_KEYS` (:949): `ipc: host`, `uts: host` (case-insensitive сравнение уже есть).
3. Новая проверка service-level: `security_opt` — любое значение содержащее `unconfined`
   (seccomp/apparmor/systempaths=unconfined, case-insensitive) → violation; `volumes_from` присутствие
   (≠ None) → violation (паритет cap_add/devices).
4. R5-негативы в `tests/unit/test_verify_contracts.py` на ТОЧНЫЙ вход из CRITICAL.md (sock/driver_opts/
   device:/var/run/docker.sock + сервис с named-ref) + по одному на ipc/uts/security_opt/volumes_from +
   позитив-регрессия (named volume без driver_opts — OK; платформенный compose не проходит этот путь).

**AC:** fixture из CRITICAL.md → has_blocking=True; каждый новый ключ → отдельный негатив-тест;
`make check TEST_FILE=tests/unit/test_verify_contracts.py` зелёный.

### T1.3 [C4, L6, G2] · provisioner: transport-failures, fetch-once, запрет fall-through-generate [M]
Факты@HEAD: `key_provisioner.py` except-кортежи :717 (update) и :748 (generate) =
`(OSError, ConnectionError, TimeoutError)` — `LiteLLMTransportError(Exception)`
(`admin_client.py:63`) НЕ ловится → transient абортит всю φ-provision-llm; ветка update-fail
:720–723 логирует «falling through to generate» → при первом же расширении кортежа активируется
генератор дублей ключей; fetch-once отсутствует — `get_key_by_metadata` :680 внутри цикла = N пагинаций.
L6: `admin_client.py:476` `isinstance(total_pages, int)` — строка "2" молча обрывает листинг после
страницы 1; `async_get_key_info` :533–539 глотает httpx-ошибки → None («ключа нет»).

Дизайн:
1. Оба кортежа += `LiteLLMTransportError` (импорт из admin_client; семантика WARN + failed-consumer
   учёт, фаза продолжает следующих потребителей).
2. **Убрать fall-through**: ветка update-fail → WARN + continue (БЕЗ generate). Generate достижим ТОЛЬКО
   при `existing_key is None`. ## @rationale: неудачный update оставляет живой ключ со старой конфигурацией
   — деградация конфига лучше второго budget-bearing ключа (мина массовых дублей DATA-класса).
3. Fetch-once: перед циклом один `list_keys()` → индекс `{metadata.project: KeyInfo}`; lookup'ы из индекса;
   после успешного update/generate — обновление записи индекса локально (консистентность в рамках прогона;
   конкурентные прогоны уже сериализованы store-lock).
4. admin_client: total_pages — строгая конверсия: числовая строка → int(); прочее (None без страницы /
   мусор) → `LiteLLMTransportError("malformed pagination payload")`; `async_get_key_info` — 404 → None,
   остальные httpx-ошибки → `raise LiteLLMTransportError(...) from e` (убрать глотание).
5. Исправить лживый TRAP `key_provisioner.py:383–392` (corruption-chain тест появится в этом таске).
6. Тесты G2 (`tests/unit/test_llm_key_provisioner.py`, `test_llm_provision.py`):
   - corruption-chain: truncate store → `_load_key_store` → PlatformError (не silent `{}`);
   - pagination ≥2 страниц MockTransport (int) + строковый "2" → обе страницы прочитаны;
   - sync transport-error ≠ 404: ConnectError → LiteLLMTransportError → потребитель в failed, фаза жива;
   - update-fail → второй ключ НЕ создаётся (count вызовов generate_key == 0);
   - fetch-once: N потребителей → ровно 1 list_keys за прогон.

**AC:** перечисленные тесты зелёные; `except`-кортежи содержат LiteLLMTransportError; grep
«falling through to generate» в key_provisioner.py пуст.

### T1.4 [C5, R7, R6, G1] · Секреты вне argv: fallback-deliver + prelude + redact-порядок + stdin-тест [M]
Факты@HEAD: `core-deliver.sh` передаёт detected key флагом `--age-secret-key` (argv python-процесса);
`core_deliverer.py:903` CLI-флаг, :761/:802–803 — встраивание в remote stdin-script (remote-нога уже
чиста — TRAP :795–799); redact ПОСЛЕ truncate :822 (`r.stderr.strip()[-500:]` → суффикс ключа на границе
окна уходит в лог); prelude-билдеры `ssh_cmd_builder.py:450–458` + `build-ssh-cmd.sh:51–59` +
`bootstrap.sh:96` — значение ключа позиционным argv короткоживущего процесса; stdin-ветка
RemoteExecutor (`core/internal/bootstrap/remote_executor.py`, `bash -s` + input=) не покрыта тестами
(G1: `test_remote_executor.py:255` ассертит только legacy argv-путь).

Дизайн:
1. `core-deliver.sh`: удалить передачу `--age-secret-key <value>` (env/file остаются каноном —
   AGE_SECRET_KEY перекрывает AGE_SECRET_KEY_FILE по таблице AGENTS.md).
2. `core_deliverer.py`: убрать CLI-аргумент; `deliver_fallback()` детектирует ключ внутри Python через
   `node_detect.detect_age_key()` (та же цепочка env→SOPS_AGE_KEY→FILE→default-files); remote stdin-prelude
   не меняется. Отсутствие ключа → явный FATAL (не тихий skip).
3. Redact-порядок :822: `redact_secrets(r.stderr, key)` → потом `[-500:]` truncate (redact-before-truncate).
4. Prelude-билдеры: секрет-значения больше не идут позиционными argv — доставка значений в тело remote-
   скрипта (stdin-канал, тот же механизм что :800–804); shell-фасады остаются тонкими (<150 LOC).
5. Тесты:
   - G1 `test_remote_executor.py::test_stdin_payload_branch_uses_bash_s_input`: payload present → cmd
     заканчивается `bash -s`, payload в input=kwarg, секрет/REMOTE_CMD отсутствуют в argv; удаление ветки
     ломает тест (единственный страж argv-мира);
   - redact boundary: stderr, где ключ начинается на границе последних 500 символов → в лог не попадает
     ни полный ключ, ни суффикс;
   - deliver_fallback: argv процесса не содержит значения ключа (инспекция runner-cmd).

**AC:** `grep -rn "age-secret-key" core/entrypoints/core-deliver.sh` пуст; все три теста зелёные;
`make agent-check` чистый.

### T1.5 [C6] · AGE_RECIPIENT: матрица + канал доставки + release-checklist [S]
Факты@HEAD: объявлен `backup-cron/module.yaml:49` (required:false), читается `docker-compose.base.yml:90`
(`${AGE_RECIPIENT:-}`), но отсутствует в secret-definitions/secrets-manifest/platform-infra/.env.example →
на ноде всегда пуст → nightly upload fail-closed SKIP (сигнатура корректна: backup_postgres.py:353–358
IMP:9 + Loki BackupUploadFailure) → RPO 24ч фиктивен. Это ПУБЛИЧНЫЙ ключ-реципиент (не секрет; та же
конвенция что age-key-backup, age_cipher.py:17–21).

Дизайн (верифицирован субагентом):
1. `core/secret-definitions.yaml` (после S3_SECRET_KEY): `AGE_RECIPIENT / tier: optional / source: sops /
   ci_default: "" / note:` (публичный age-ключ реципиента off-site копий; пусто → SKIP fail-closed;
   оператор задаёт один раз при bootstrap). Прецедент полей — TELEGRAM_CHAT_ID (ci_default: "").
2. `platform-infra.yaml` НЕ трогать (env_defaults — CI/test-дефолты, прод-значение туда нельзя).
3. Регенерация: `make generate-manifests` (secrets-manifest/platform-env/smoke_env/env_example).
4. Док-правки: root AGENTS.md §Release checklist п.4 — пост-деплой шаг «off-site DR активен: AGE_RECIPIENT
   непуст в env backup-cron на prod; последние nightly uploads без BackupUploadFailure»; core/AGENTS.md
   §«DR мастер-ключа AGE»: §2 — дополнение «backup-дампы шифруются тем же recipient (backup-cron
   AGE_RECIPIENT)»; §5 — Debt DR-offnode-backup закрывается после заведения ключа (Track O).
5. Доставка: оператор добавляет значение в per-node sops-матрицу (`node-configs/secrets/<node>.enc.yaml`)
   → существующая цепочка secrets.env → compose `--env-file` подхватит автоматически.

**AC:** `make check MARKER=check-manifests` GREEN после регенерации; запись видна в secrets-manifest.yaml;
оба док-якоря содержат новые строки.

### T1.6 [R15, G4] · Postgres GRANT в целевую БД [XS]
Факты@HEAD (поправка к QA-клейму): `CREATE DATABASE ... OWNER postgres` (:148) — проектная роль НЕ owner БД,
поэтому `GRANT CREATE,USAGE ON SCHEMA public` в собственной БД проекта НЕ избыточен (pg_database_owner
неприменим) — чинится ТОЛЬКО таргетинг, убирать гранты нельзя.
Дизайн (верифицирован субагентом): `_psql(*args, runner=None, *, database: str | None = None)` — kwarg-only
аддитивно; при заданном database вставляет `["-d", database]`; все 3 DDL (:305–309) выполняют grant'ы с
`database=db_name`; ролевые операции (pg_roles SELECT, CREATE/ALTER ROLE) остаются кластерными. Ретроспективный
лишний грант в admin-DB на существующих нодах — разовый ручной REVOKE, вне фикса → B2.
Тест G4: `test_on_project_deploy.py` — для каждого psql-вызова с GRANT/REVOKE ассерт `" -d myproj_db "` в
joined-команде; точный argv schema-grant зафиксировать.

**AC:** тест G4 зелёный; повторный деплой идемпотентен (GRANT no-op при повторе).

## Волна 2 — регрессии HIGH/MEDIUM (после Волны 1; файлы не пересекаются)

### T2.A [R5, R1] · secrets_manager: strict-парсер merge-guard + secure persist [S+S]
1. R5 (`shared/secrets_env_parser.py:114–124`, guard `secrets_manager.py:638` И второй `:848`):
   `parse(path, prefix_filter=None, *, strict=False)` — strict собирает номера непустых не-комментарий
   строк без валидного `key=` → ConfigValidationError со списком строк; default False (все прочие
   потребители не тронуты). Включить strict на merge-path (:572) и autogen-persist (:848) — исключение
   срабатывает ДО merge-guard: файл нетронут, φ4 получает FATAL через существующую обёртку (:844–847).
   Тесты: P0-вход «1 валидная + garbage» → исключение, байты файла до/после идентичны; backward-compat
   матрица (comments/blank/export/inline-#/quoted парсятся штатно).
2. R1 (`secrets_manager.py:856–864` `_persist_new_vars`): заменить фиксированный `.env.tmp`
   open("w")+chmod-after на атомарную защищённую запись через `shared/atomic_writer.atomic_write_text`
   (tempfile+fsync+os.replace уже канон) с режимом 0600 ДО записи контента (O_EXCL/no-symlink-follow),
   legacy st_mode НЕ наследовать — всегда tighten до 0600. Покрыть путь в `test_secret_writers_mode.py`.

**AC:** оба сценария R5/R1 из REGRESSIONS.md воспроизводятся тестами (red) и проходят после фикса (green).

### T2.B [R2] · hc_done fresh-семантика [S]
Факты@HEAD: writer `_set_hc_marker` уже пишет run-id (deploy_orchestrator.py:1270–1277), свип старых —
`phases/docker._sweep_stale_hc_markers`; проблема — reader φ11 registry_update исполняется ДО писателя φ12
(state_machine порядок), т.е. поглощает маркер ПРОШЛОГО прогона без проверки возраста.
Дизайн: reader-side проверка свежести — state_machine фиксирует run-start timestamp при старте режима;
φ11 принимает маркер только если marker mtime ≥ run-start (чужой/старый прогон → healthcheck выполняется).
Тест: extend `test_hc_marker_run_scope.py` — маркер предыдущего прогона НЕ подавляет deep-healthcheck;
маркер текущего прогона (после φ12-писателя при retry) — подавляет.

**AC:** ротация секретов в φ9 больше не глушит глубокий healthcheck в φ11 того же прогона.

### T2.C [R3] · Orchestrator: честный failed-accounting [S]
Точки: JSONDecodeError/битые entries при парсинге вывода deploy-many (:922–932) → unparsable → ВСЕ
недоказанные проекты = failed (не deployed=0/failed=[]); rc≠0 от deploy-many subprocess (:744–750) →
failed-учёт + severity CRIT, не WARN-only; broad-except группы должен включать OSError fork-фейлы в failed.
Паттерн уже есть: :911–920 (TimeoutExpired/OSError → все failed) — распространить тот же контракт на
перечисленные хвосты. Тесты: extend `test_parallel_runner`/`test_deploy_many_observability` — битый JSON →
failed≠[] + exit 2; rc≠0 → CRIT.

**AC:** упавший деплой никогда не пишет success-marker и не гасит healthcheck (unit-негативы).

### T2.D [R4, G8] · Converge fail-closed + watchdog изоляция [M]
Верифицированный дизайн субагента:
1. `converge/runtime.py`: `resolve_container_name` → `list[str] | None` (None = ps rc≠0, IMP:9); в цикле R9
   None → report warn «runtime UNVERIFIED» + set_exit(1) + счётчик ps_unverified (WARN, не FAIL: exit 2
   зарезервирован за провалом heal; транзиентный сбой ps после успешного docker_info). Финальный агрегат
   :362–370: errors→fail > ps_unverified→warn > healed > converged — UNVERIFIED никогда не схлопывается
   в converged. Legacy: containers==[] при rc==0 → однократный допрос all=True с label-колонкой: строки
   с пустой label → warn «N контейнеров без compose-label, R9 их не видит» + set_exit(1); пустая нода
   (0 строк) остаётся зелёной.
2. `watchdog.py:786–791` (+ аналогично `_notify_crashloops_with_suppress:740–744`): OSError при re-save
   state → `failures += 1; continue` (остаток батча лечится; штамп = retry следующего прохода), IMP:10 лог.
Тесты: `test_reconciler_r9_runtime.py` — ps rc≠0 → НЕ converged/exit≥1; unlabeled=2 → warn; пустая нода →
green; `test_watchdog.py` — save_state OSError на первом действии → второе действие выполнено, exit 1.

**AC:** три негатива converge + watchdog-тест зелёные; транзиентный сбой docker не даёт ложного «FULLY CONVERGED».

### T2.E [R10] · sshd_policy: content-based нейтрализация [S]
`sshd_policy.py:466` glob `*cloud*` + weaken-regex без IGNORECASE → vendor drop-in произвольного имени
(`60-custom.conf` c `PasswordAuthentication yes`) не нейтрализуется. Дизайн: сканировать ВСЕ `*.conf`
(кроме self-hardening и *.disabled), детект ослабления — content-based case-insensitive regex; имя файла
перестаёт быть сигналом. Self-delete guard и rename-to-.disabled семантика сохраняются; rename-fail →
apply FAIL (уже так). AllowUsers-статичность (ubuntu lockout риск) — НЕ код: документировать в release-note
и Runbook (post-apply сверка sshd -T остаётся в check-security S-серии). Тесты: drop-in произвольного имени
с ослабляющей директивой → neutralized; case-вариант директивы ловится; доброкачественный vendor-conf НЕ тронут.

**AC:** оба новых теста зелёные; существующие sshd-тесты без регрессии.

### T2.F [R11, G5] · ACME бюджет + EC pair-match [S]
`cert_orchestrator.py:127` ISSUE_TIMEOUT=300 против inner retry ≈605s (`issue_cert.py:105,417–426`) → вторая
попытка недостижима, домен молча в self-signed fallback. Дизайн: outer ISSUE_TIMEOUT ≥ inner worst-case
(поднять до 700) ИЛИ сократить inner-attempts до fit — выбрать меньший дифф по коду; rate-limit ответ LE
(HTTP 429 / "rate limit" паттерн) → fail-fast БЕЗ повторов и backoff (повтор жжёт лимит). Тесты:
issue_cert_backoff — rate-limit ветка без повторов; `test_ssl_certs_pair_match.py` — параметризовать ec-256
(прод KEY_LENGTH) рядом с rsa:2048.

**AC:** бюджетная математика покрыта ассертом; EC-ветка pair-match прогоняется в CI.

### T2.G [R12, R13-full, G3, G7] · Check-suite самозащита [M]
1. R12 `fingerprint.py:83`: salt += basedpyright версия (importlib.metadata; отсутствие пакета → версия
   "absent" в salt, не crash).
2. R13/G7 honesty-детектор (`test_gate_honesty_mode.py:111–127`): glob `*.yml` + `*.yaml` (nightly.yaml);
   strip комментариев перед поиском пина (закомментированный REQUIRE_HONESTY_MODE не satisfies); прямую
   invocation-щель (`python3 -m core.internal.check_suite run`) — задокументировать как остаточный риск
   (детекция ненадёжна) + TRAP[DECISION].
3. G3 `test_gate_collection_floors.py`: mutation-негатив «слой опустел ниже floor → гейт RED».
4. G7 oracle (`manifest_oracle.py`): read_text/parse оборачивать → любое исключение = RED-вердикт, не traceback.
Тесты: self-негативы на каждую щель (yaml-файл с пином в комментарии → RED; .yaml workflow без пина → RED;
mutation floors → RED; oracle на битом манифесте → красный вердикт в структуре отчёта).

**AC:** все четыре кластера имеют red→green негативы; fingerprint реагирует на bump basedpyright (юнит-ассерт состава salt).

---

# $PARALLEL_GROUPS

```
Wave 0 (serial, блокер):        T0.1
Wave 1 (6 параллельных, файлы ∩ = ∅):
  T1.1 workflows/pin/adopter/gate · T1.2 verify_contracts · T1.3 llm provisioner
  T1.4 secrets-argv/redact/stdin · T1.5 AGE_RECIPIENT+manifests/docs · T1.6 postgres hook
  Command: coder Read .ai/plans/meta-refactoring/14-DevPlan-fixes.md, implement Wave 1: T1.x
  (по одному субагенту на таск; общий коммит волны после зелёного make check)
Wave 2 (7 параллельных, файлы ∩ = ∅):
  T2.A secrets_manager/parser · T2.B hc-marker · T2.C orchestrator · T2.D converge/watchdog
  T2.E sshd · T2.F acme/ec · T2.G check-suite gates
  Command: coder Read .ai/plans/meta-refactoring/14-DevPlan-fixes.md, implement Wave 2: T2.*
Backlog (вне READY):            B1–B4
```

Конфликты внутри волн отсутствуют (проверено матрицей файлов); T2.A объединяет R5+R1 из-за общего
`secrets_manager.py`.

# Acceptance Criteria (сводная таблица)

| ID | Критерий | Верификация |
|----|----------|-------------|
| AC-1 | Волна 0: make check + agent-check зелёные на 42679a0 | runs.jsonl |
| AC-2 | T1.1: свежий pin в 3 местах + freshness-гейт RED на stale | тест-негативы гейта |
| AC-3 | T1.2: fixture docker.sock через top-level volume → L1 block | test_verify_contracts |
| AC-4 | T1.3: transient → failed++, 0 дублей, 1×list_keys | тесты G2 |
| AC-5 | T1.4: секрет отсутствует во всех argv; redact до truncate | G1-тест + инспекция cmd |
| AC-6 | T1.5: manifests GREEN, запись в матрице, 2 док-якоря | check-manifests + grep |
| AC-7 | T1.6: GRANT c `-d <db>` | G4-ассерт |
| AC-8 | T2.*: каждый R-дефект имеет red→green тест | per-task TEST_SPEC |
| AC-9 | READY_WITH_WARNINGS: Волны 0–1 смержены, гейт-цикл зелёный | make check + hook push |
| AC-10 | READY: + Волна 2 + Track O по release-checklist | чеклист root AGENTS.md |

# $TEST_SPEC (ключевые строки; полный состав — в тасках)

| Test file | Test function | Scenario | Module under test |
|-----------|---------------|----------|-------------------|
| tests/gates/test_gate_workflow_sha_pins.py | test_*pin_freshness* / *_negative | stale-pin и ложная дата комментария → RED | sha-pins gate |
| tests/unit/test_project_adopter.py | test_*generated_workflow_pinned* | adopter генерирует SHA-pin+concurrency+permissions | project_adopter |
| tests/unit/test_verify_contracts.py | test_*toplevel_volume_driver_opts* + 4 новых ключа | fixture CRITICAL.md → block | verify_contracts |
| tests/unit/test_llm_key_provisioner.py | test_corruption_chain_fail_loud / test_fetch_once / test_update_fail_no_duplicate / test_transport_not_abort | G2-набор | key_provisioner |
| tests/unit/test_llm_provision.py | test_pagination_string_total_pages | str "2" → обе страницы | admin_client.list_keys |
| tests/unit/test_remote_executor.py | test_stdin_payload_branch_uses_bash_s_input | stdin-ветка, секрет вне argv | remote_executor |
| tests/unit/test_core_deliverer.py | test_redact_before_truncate_boundary / test_no_key_in_argv | граничный redact; argv-чистота | core_deliverer |
| tests/unit/test_on_project_deploy.py | test_grant_targets_project_db | `-d myproj_db` в psql argv | postgres hook |
| tests/unit/test_shared_secrets_env_parser.py | test_strict_unparsable_lines_fatal + compat-матрица | P0 partial-parse → fatal | secrets_env_parser |
| tests/unit/test_secret_writers_mode.py | test_persist_new_vars_secure_atomic | 0600 до записи, без symlink-follow | secrets_manager |
| tests/unit/test_hc_marker_run_scope.py | test_stale_marker_does_not_suppress_deep_hc | маркер чужого прогона | phases/docker+state_machine |
| tests/unit/test_deploy_many_observability.py | test_json_corrupt_all_failed / test_rc_nonzero_crit | битый JSON/rc≠0 → failed | deploy_orchestrator |
| tests/unit/test_reconciler_r9_runtime.py | test_ps_failure_not_converged / test_unlabeled_warn / test_empty_node_green | fail-closed converge | converge/runtime |
| tests/unit/test_watchdog.py | test_resave_oserror_does_not_block_batch | изоляция сбоя re-save | watchdog |
| tests/unit/test_sshd_policy.py | test_noncloud_named_weakening_dropin_neutralized | content-based детект | sshd_policy |
| tests/unit/test_issue_cert_backoff.py | test_rate_limit_no_retry | 429/rate-limit → fail-fast | issue_cert |
| tests/unit/test_ssl_certs_pair_match.py | param ec-256 | прод-тип ключа покрыт | ssl_certs pair-match |
| tests/gates/test_gate_collection_floors.py | test_layer_below_floor_red | mutation-негатив | floors gate |

# Design Decisions (@rationale)

## @rationale Q: почему deny ЛЮБОГО driver_opts в проектах, а не только device-сокетов? A: verify_contracts сканирует исключительно проектные payload (3 вызывающих, l1_only) — легитимных bind-driver_opts у проектов нет по канону персистентности; правило «присутствие → violation» паритетно cap_add/devices и не требует эвристик путей.
## @rationale Q: почему update-fail → continue, а не re-lookup+generate? A: неудачный update оставляет живой валидный ключ; generate создаёт второй budget-bearing ключ (мина массовых 401/двойного биллинга) — асимметрия риска однозначно запрещает fall-through.
## @rationale Q: почему pin-SoT модуль + литералы в шаблонах, а не полный рендер? A: шаблоны — статические payload template_engine (python туда не импортируется); triple-literal допустим при условии гейта-эквалайзера на всех трёх местах; полная шаблонизация пина — churn без снижения риска при работающем freshness-гейте.
## @rationale Q: почему R4 ps-фейл это WARN+exit 1, а не FAIL+exit 2? A: exit 2 зарезервирован за доказанным провалом heal; транзиентный сбой ps после успешного docker_info не должен будить дежурного, но и «converged» с непроверенным runtime недопустим — средняя семантика UNVERIFIED.
## @rationale Q: почему AGE_RECIPIENT tier=optional, а не required? A: required сломает bootstrap/preflight всех свежих нод без заведённого DR-ключа; fail-closed SKIP + сигнал + release-checklist шаг дают ту же гарантию RPO без блокировки запуска.
## @rationale Q: почему strict-парсер opt-in флагом, а не глобально? A: parser едят многие потребители; глобальная строгость — отдельное решение с миграцией; сейчас критична только merge-path (необратимая потеря), поэтому strict включается точно там.

# Верификационный протокол (канон репо)

- Per-task: `make check TEST_FILE=<path>` (один файл = один вызов); мелкие правки — `make check-diff`.
- Фикс-цикл: `make check` батчем до чистоты; финал волны — `make check` + `make agent-check` (обязателен).
- После T1.2/T1.5: `make check MARKER=check-manifests`.
- `make gate MODE=fast` локально НЕ запускать (OOM-политика 0.8) — арбитры: pre-push hook + CI push-gate.yml.
- Коммиты: `make fix-gate && git add -u` перед каждым commit; одна волна = один feat-коммит
  `feat(meta-refactoring): волна N QA-fix-forward — <состав>` (+ отдельный docs-коммит при необходимости; ≤2 на волну).

# Next Steps

### Волна 0
Use coder role and read /Users/tronyx/projects/ai-platform/.ai/plans/meta-refactoring/14-DevPlan-fixes.md, execute T0.1: `make check` (до чистоты), затем `make agent-check`. Стоп при первом RED — репорт.

### Волна 1
Use coder role and read DevPlan 14-DevPlan-fixes.md, implement Wave 1 tasks T1.1–T1.6 (параллельные субагенты по одному на таск; каждому — только его секцию таска). Для каждого таска: реализация по дизайну, тесты по $TEST_SPEC, затем `make check TEST_FILE=<его тест>` (до чистоты). После всех шести: `make check` батчем, `make agent-check`, `make fix-gate && git add -u`, commit `feat(meta-refactoring): волна 1 QA-fix-forward — C2..C6`.

### Волна 2
Use coder role and read DevPlan 14-DevPlan-fixes.md, implement Wave 2 tasks T2.A–T2.G (аналогичная схема). Финал: `make check`, `make agent-check`, commit `feat(meta-refactoring): волна 2 QA-fix-forward — R1..R13`.

### Track O (оператор/test-VPS, вне кода)
По release-checklist root AGENTS.md: staging node-update (REF-0007 drill) → full-stack REF-0017 → backup-cycle REF-0009 restore → drills В4 (chaos T1–T12, reboot/restore/age-key/load-smoke) → e2e scaffold→push→deploy → завести AGE_RECIPIENT в sops-матрицу prod-ноды и проверить nightly upload. Только после этого — перевод в READY.

# Backlog (вне скоупа READY)

| # | Позиция | Условие возврата |
|---|---------|------------------|
| B1 | R14 oracle-покрытие остальных манифестов | после Track O / первый drift-инцидент generated-файлов |
| B2 | L-хвост: L1 SubprocessError-консистентность, L2 stale-комментарии 600s, L4 FQDN lower() (XS); ретроспективный REVOKE грантов в admin-DB | ближайший touch соответствующих файлов |
| B3 | Решения владельца: zai glm-4.5-flash (санкция записана?), macos litellm DATABASE_URL="" vs инвариант 8 | следующий релиз |
| B4 | Monitoring deep-audit (оговорка FINAL-VERDICT §Coverage) | опционально |

$END_DEVPLAN
