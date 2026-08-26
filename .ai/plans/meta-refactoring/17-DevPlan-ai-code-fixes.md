<!-- GREP_SUMMARY: devplan ai-code-fixes AI-audit residuals 77-ID verification matrix waves silent-failure resolver-convergence dead-code doc-drift test-hygiene -->
<!-- STRUCTURE: ▶ W0 baseline → ⚡W1 silent-failure×4 ∥ ⚡W2 cheap-high-signal×4 ∥ ⚡W3 atomicity×8 ∥ ⚡W4 resolvers/knobs×5 ∥ ⚡W5 dedup×6 ∥ ⚡W6 dead-code×7 ∥ ⚡W7 signatures/docs×10 ∥ ⚡W8 test-hygiene×5 → ∑ check до чистоты -->

$START_DEVPLAN

## $ARTIFACT_CONTRACT

| Поле | Значение |
|------|----------|
| PURPOSE | Закрыть все актуальные находки аудита `08-ai-code` (AI-0001…AI-0077), неподтверждённые как исправленные на HEAD `4e623c1`: 58 PRESENT + 6 PARTIAL → 62 actionable ID, сгруппированные в 34 задачи / 8 волн |
| DESCRIPTION | Fix-forward план по завершённой верификации аудита. Каждый ID перепроверен субагентами по символам/file:line на текущем дереве (после мерджа плана 16); FIXED-позиции зафиксированы с fixing-commit и в план не входят. Задачи сгруппированы по классам дефектов и непересекающимся файлам |
| RATIONALE | Q: почему новый план, а не расширение 14/16? A: 14/16 закрыты и смержены (`4e623c1`); их scope — P0/P1 других треков аудита. AI-код-находки образуют собственный класс (AI-written patterns: fake-knobs, copy-paste divergence, doc-drift) с собственной волной верификации. Отдельный план сохраняет трейл «аудит 08 → верификация → фикс» |
| ACCEPTANCE_CRITERIA | SC1: ни одна проверка не превращается тихо в pass — env-check crash ≠ missing=[], φ11 provisioner-фейл виден в отчёте, критерий здоровья един, правка phases/*.py инвалидирует done-фазы. SC2: документированные контракты = коду (PG 18.4, hermes/platform-secrets/status-page module.yaml честны, doc-drift кластер закрыт). SC3: одно знание — одна реализация (таймаут деплоя, RUN_BASE, NODE_CONFIGS, dotenv-грамматика, san-матчер, GENERATED_HEADER, boto3-builder, check_generated). SC4: мёртвый API удалён вместе с пинящими гейтами; фейковые флаги/ручки исчезли из CLI. SC5: тестовый сигнал восстановлен (IMP:9-assert везде, публичная поверхность, литералы из инфраструктуры). SC6: после каждой волны `make check` + `make agent-check` зелёные |
| IMPLEMENTS | Аудит `.ai/plans/meta-refactoring/08-ai-code/findings-001…012.md`: все ID со статусом PRESENT/PARTIAL по верификации @HEAD 4e623c1 (см. §Verification Matrix); FIXED-позиции — только документируются |
| IMPACTS | ~70 файлов: core/internal/{bootstrap/deploy,lifecycle,healthcheck,deploy,secrets,scaffold,validate,config,scripts,shared,static}, core/modules/{postgres,langfuse,hermes-agent,status-page,platform-secrets,backup-cron,monitoring}, core/lib/*.sh, core/templates/module.mk, makefiles/*.mk, AGENTS.md, .github/workflows/deploy-project.yml (нет — только чтение), tests/unit/* (~15 файлов), tests/gates/* (3 файла), generated-манифесты через `make generate-manifests` |
| REQUIRES | Волна 0: чистое дерево (сейчас `M core/entrypoint-manifest.yaml`) + `make check` + `make agent-check` зелёные на стартовом HEAD; канон: Python-only new code, generated-файлы только через `make generate-manifests`, тестовая команда агента `make check`, `make gate MODE=fast` в dev-цикле не запускать |

---

## §Source

Пользовательская постановка: «Вот крупный аудит, часть ошибок уже исправлена, проверь и найди оставшиеся актуальные недоделки и составь девплан их исправления» + «Продолжай сессию, просто одна ветка еще мерджилась в маин. Теперь весь код на месте».

Вход: аудит `.ai/plans/meta-refactoring/08-ai-code/` (77 ID, дерево @1272521). Верификация выполнена 9 параллельными субагентами против HEAD `4e623c1` (после мерджа плана 16) + спот-чек топ-клеймов оркестратором. Полные вердикты — §Verification Matrix ниже; evidence каждого ID хранится в сессии планирования и продублирован якорями в задачах.

## §Verification Matrix @HEAD 4e623c1

**FIXED (11, вне плана):** AI-0001 (langfuse :3000, 404cad0) · AI-0003 (host nginx, 404cad0) · AI-0004 (prometheus_rules_dir_sot, aaa209d) · AI-0077 (networks parity, 404cad0) · AI-0006 (flock до payload + workflow concurrency, 2dca576) · AI-0007 (openssl -stdin, 2dca576) · AI-0013 (mirror timeout, 404cad0) · AI-0014 (docker_auth timeout, 404cad0) · AI-0021 (push_l2 в Python, 8c9fd0a) · AI-0023 (NODE dispatch, aaa209d) · AI-0070 (crontab одинарный, 63ce627)

**CLOSED-BY-DECISION (1, вне плана):** AI-0022 — ConnectTimeout в deploy-project.yml отсутствует намеренно: TRAP[DECISION] REF-0012 (:102-110), reusable-workflow без платформенного core/, гейт D69 банит сырые литералы, риск ограничен `timeout-minutes: 30`. Ревизия не требуется.

**PARKED (2, §Debt Intake):** AI-0057 (subprocess_io adoption gap, нужен триаж) · file_lock._REENTRANT threading (нет threaded-потребителей).

**OPEN (62 actionable ID → задачи ниже):**

| Задача | IDs | Класс | Сев |
|--------|-----|-------|-----|
| T1.1 | AI-0011 | silent-failure | HIGH |
| T1.2 | AI-0010r | silent-failure | MED |
| T1.3 | AI-0065 | dual-criterion | MED·ACTIVE |
| T1.4 | AI-0038 | stale-invalidation | MED·ACTIVE-cond |
| T2.1 | AI-0069r | doc-contract | HIGH→doc |
| T2.2 | AI-0051+0052 | lost-QA-signal | MED |
| T2.3 | AI-0063 | diverged-fix-propagation | MED |
| T2.4 | AI-0073 | dup-boto3/RPO | MED |
| T3.1 | AI-0008 | race-window | LOW |
| T3.2 | AI-0009 | torn-read/false-comment | LOW |
| T3.3 | AI-0017+0037 | foot-gun/false-contract | MED |
| T3.4 | AI-0015+0016 | retry-burn/swallow | LOW |
| T3.5 | AI-0018+0019 | missing-timeout/suppress | LOW |
| T3.6 | AI-0072 | fake-env-requires | MED |
| T3.7 | AI-0071 | contract-misstate | MED |
| T3.8 | AI-0075 | false-security-invariant | LOW→sec-doc |
| T4.1 | AI-0024+0025 | resolver-bypass | MED |
| T4.2 | AI-0002r+0020r+0039 | inert-timeout-drift | LOW/MED |
| T4.3 | AI-0026+0027 | fake-flags | LOW-MED |
| T4.4 | AI-0028+0029r+0030 | dead-schema-promise | MED |
| T4.5 | AI-0074 | fake-scenario-knob | LOW |
| T5.1 | AI-0064 | probe-triplet | MED |
| T5.2 | AI-0066 | wildcard-san ×3 | LOW |
| T5.3 | AI-0067 | header-dup | LOW |
| T5.4 | AI-0068 | two-readers | LOW |
| T5.5 | AI-0055+0061 | three-dotenv-grammars | MED |
| T5.6 | AI-0054 | env-dependent-validator | MED |
| T6.1 | AI-0058 | dead-code/gate-pinned | MED |
| T6.2 | AI-0059 | dead-code/false-docstring | MED |
| T6.3 | AI-0060 | test-only-getters | LOW |
| T6.4 | AI-0062 | orphaned-CLI | MED |
| T6.5 | AI-0053 | dead-accessors | MED |
| T6.6 | AI-0056 | wrapper-chain | LOW |
| T6.7 | AI-0005 | resources-unmodeled | LOW |
| T7.1 | AI-0031 | ignored-flags | LOW |
| T7.2 | AI-0032 | self-admitted-dead-flag | LOW |
| T7.3 | AI-0033 | param-ignored ×4 | LOW |
| T7.4 | AI-0034 | param-ignored ×2 | LOW |
| T7.5 | AI-0035 | vestigial-chain | LOW |
| T7.6 | AI-0036 | broken-exclusion-regex | LOW·LATENT |
| T7.7 | AI-0040 | return-contract-doc | MED |
| T7.8 | AI-0041 | noop-validator | LOW |
| T7.9 | AI-0042..0045 | stale-STRUCTURE/TRAP | LOW ×4 |
| T7.10 | AI-0076 | facade-divergence | LOW |
| T8.1 | AI-0046 | private-imports | MED |
| T8.2 | AI-0047 | mock-wiring-pins | MED |
| T8.3 | AI-0048 | hardcoded-literals | MED |
| T8.4 | AI-0049 | private-entrypoint-as-API | MED |
| T8.5 | AI-0050 | tautological-asserts | LOW |

PARTIAL-остатки, вошедшие в задачи: AI-0010 (провижинер починен 77c8221, фаза — нет) · AI-0012 → поглощён T1.3-смежным пакетом? НЕТ: AI-0012 (healthcheck budget 30/60/180) → **T1.5** (добавлено ниже, перенос из матрицы: reporting.py:143 literal 30, module_interface.py COMPOSE_UP_TIMEOUT=180) · AI-0020r (комментарии remote_executor) · AI-0029r (repos.node_configs; repos.core стал живым через context_overlay.py:308 — НЕ удалять) · AI-0069r (AGENTS.md:207 + compose STRUCTURE:3 + `.ai/about-for-new-conversations-in-chat.md:19`) · AI-0002r (600-литералы в SoT-цепочке env-default).

### T1.5 [AI-0012r] · Единый бюджет healthcheck-invoke [XS]
`HEALTHCHECK_CMD_TIMEOUT=60` введён (timeouts.py:124, мигрированы modules_healthcheck:245,255 + deploy_orchestrator:1120,1182), но `helpers/reporting.py:143` держит literal `timeout=30`, а `module_interface.py:74` дефолт `COMPOSE_UP_TIMEOUT=180` — тот же вызов `<mod> healthcheck liveness` получает 30 или 60 в зависимости от пути.
Дизайн: reporting.py:143 → `HEALTHCHECK_CMD_TIMEOUT`; у invoke в module_interface дефолт параметром от той же константы (COMPOSE_UP остаётся для compose up, но healthcheck-вызовы — только HEALTHCHECK_CMD_TIMEOUT). Файлы: helpers/reporting.py, shared/module_interface.py. **AC:** grep `timeout=30` в healthcheck-путях пуст; все invoke получают одну константу; существующие тесты зелёные.

---

## §Debt Intake

Существующие DEBT-реестры: glob `.ai/plans/**/*-Debt.md` — пусто. Находки, сознательно НЕ вошедшие в таски:

| Находка | Решение |
|---------|---------|
| AI-0057 subprocess_io adoption gap (raw `subprocess.run` в ~99 файлах vs 36 импортирующих канон; gap растёт) | DEFER — нужен отдельный триаж-проход (часть raw-сайтов — оправданные примитивы docker_ops/http_probe); не рефакторить вслепую |
| file_lock._REENTRANT + notifications._THROTTLE_REGISTRY thread-locks | DEFER — threaded-потребителей нет; Rev: любое появление ThreadPool-потребителя FileLock |
| AI-0022 ConnectTimeout в reusable workflow | CLOSED-BY-DECISION (TRAP[DECISION] REF-0012; компенсация timeout-minutes:30) — не реопening |
| AI-0029 `repos.core` | Стал живым полем (context_overlay.py:308, gen_project_platform_md.py:614) — клейм аудита устарел наполовину; в T4.4 удаляется ТОЛЬКО `repos.node_configs` |
| AI-0035 vestige частично рационализирован контрактом («принимается для сигнатурной совместимости») | T7.5 минимизирует до: убрать ложное утверждение overlay_deliverer «sets PLATFORM_ROOT»; полный демонтаж цепочки — по усмотрению имплементатора в рамках AC |
| Грязное рабочее дерево (`M core/entrypoint-manifest.yaml`) | Волна 0: `make fix-gate && git add -u` + коммит до старта волн |

## §Requirements Analysis

Ключевые критерии успеха (см. ACCEPTANCE_CRITERIA в контракте): (1) класс «валидатор молча = pass» уничтожен на всех трёх канонических входах (env-check, llm-provision, health-criterion); (2) инвалидация бутстрап-фаз честна при правке кода фаз; (3) каждый дублированный элемент знания сведён к одной реализации с явным SoT; (4) удаление мёртвого кода всегда тащит за собой пинящие гейты/тесты/инвентарные строки — иначе гейт ложно RED; (5) док-дрейф чинится пачками без поведенческих изменений, чтобы ревью было механическим.

## §Size Classification

LARGE по счётчику файлов (~70), но артефакт — одиночный DevPlan по конвенции родительской папки (flat-file: 11/14/16-DevPlan). CONFIRM_BRIEF пропущен: ## @rationale — Brief замещает внешний аудит-отчёт 08-ai-code + свежая верификация @HEAD (все находки пронумерованы, локали и cleanup-направления указаны аудитом; объём пере-подтверждён 9 субагентами). Прецедент — план 16.

---

# $TASKS

Оценки: XS ≤20 строк, S ≤100, M ≤300. Одна волна = один feat-коммит. Все задачи стартуют от HEAD после Волны 0; имплементатор обязан перечитать целевые файлы перед правкой (строки могли уйти после верификации). TRAP[BUG]/TRAP[DECISION]-аннотации добавляют имплементаторы в момент фикса (обычный протокол).

## Волна 0 — baseline (сериально, блокирует всё)

### T0.1 · Чистое дерево + зелёные арбитры [XS]
`make fix-gate && git add -u` (сейчас модифицирован `core/entrypoint-manifest.yaml` — регенерировать/закоммитить), затем `make check` (до чистоты) + `make agent-check`. **AC:** оба арбитра зелёные, запись в `.ai/logs/runs.jsonl`; все волны стартуют исключительно от этого HEAD.

## Волна 1 — silent-failure на канонических путях (4 параллельных задачи, файлы ∩=∅)

### T1.1 [AI-0011] · env-check: крах валидатора ≠ «missing=[]» [S]
Факт: `deploy_orchestrator.py:1097-1102` — `except Exception` → warning → `missing = []`: крах `secrets_validator.check_env_requires` неотличим от pass, модуль деплоится без required secrets.
Дизайн: ветка исключения → `missing = ["<env_check_error>: {exc}"]` (sentinel-непустота) ИЛИ явный `ok=False`-путь модуля + запись в отчёт деплоя (`env_check_error`), IMP:9. Не глотать: DEPLOY_BEST_EFFORT-политика файла не распространяется на L1-класс required-secrets (канон практик: безопасность блокирует на любом уровне). Тесты: unit — валидатор raise'ит → модуль помечен failed/пропущен с env_check_error в отчёте; валидатор вернул [] → деплой идёт (регрессия).
**AC:** крах валидатора никогда не даёт пустой missing; отчёт содержит отдельный счётчик/строку env_check_error; R5-негатив на точный сценарий аудита.

### T1.2 [AI-0010r] · φ11: сигнал provisioner доезжает до отчёта бутстрапа [S]
Факт: провижинер сторона починена (key_provisioner.py:653 failed_consumers → PlatformError, exit≠0; admin_client LiteLLMTransportError — 77c8221/ceedffb), но фаза `phases/docker.py:578-588` гоняет `provision-llm.sh` с `non_fatal=True`, результат не читает, логирует ложный IMP:9 «LLM virtual keys provisioned», возвращает False → φ11 done зелёным.
Дизайн: читать rc/результат run_subprocess; rc≠0 → IMP:9 ERROR-строка + запись в bootstrap-report (`llm_provision: failed_consumers=N`), ложный success-лог удалить; non_fatal=True сохранить (бутстрап не падает), но сводка фазы обязана отличать failed от skipped. Файлы: phases/docker.py (+reporting helper если нужен).
**AC:** LiteLLM-down сценарий даёт ERROR в сводке и запись в отчёте; успешный прогон — прежний IMP:9; unit на разбор rc.

### T1.3 [AI-0065] · Единый канон здоровья collector↔canon [S]
Факт: `docker_collector.py:274` (`Status=="healthy"`; running-без-HEALTHCHECK ⇒ healthy=False) vs канон `shared/docker_compose.py:606` (`running AND health∈{healthy,"",none}`) vs `containers.py:48-49` (WARN при not-healthy) ⇒ легитимно-healthcheck-less контейнеры вечно WARN на статус-странице, aggregate ⇒ FAIL.
Дизайн: docker_collector переиспользует канон (импорт из shared/docker_compose или общий предикат рядом с ним — leaf-контракт, циклов нет); status-page `containers.py` WARN только при `health=="unhealthy"`; TRAP[DECISION] S-WARN в aggregate.py не трогать (жёсткость агрегации — отдельное решение). Проверить смежных: watchdog.py:388/408 уже согласован с каноном (health None/none — не кандидат).
**AC:** для контейнера running без HEALTHCHECK: collector=healthy, status-page без WARN; unhealthy ⇒ WARN; parity-тест collector-vs-canon на трёх состояниях (healthy/unhealthy/none).

### T1.4 [AI-0038] · phase-hash включает lifecycle/phases/*.py [S]
Факт: `_phase_input_hash` (state_machine.py:536-573) хэширует phase_value + node.yaml{modules,services} + байты самого state_machine.py; правки `lifecycle/phases/*.py` НЕ инвалидируют done-φ8/φ8.5/φ11/φ12/φ13 на bootstrap-node/node-update. Докстринги (:23, ~:458, :524/:533-534) обещают большее, чем делает.
Дизайн: hasher дополняется отсортированным кортежем sha256 байтов `lifecycle/phases/*.py` (+`__init__.py`); docstring'и привести к факту (убрать упоминания node-lifecycle.sh/subprocess-политики — заодно T7.9-пересечение по файлу допустимо внутри одной задачи). Converge-путь не затрагивается (другой reconciler).
**AC:** unit — мутация байт phases/x.py меняет hash → phase_needs_rerun True; неизменённое дерево — hash стабилен (replay идемпотентен); докстринги соответствуют коду.

## Волна 2 — дешёвые пакеты высокого сигнала (4 параллельных, файлы ∩=∅)

### T2.1 [AI-0069r] · PostgreSQL 18.4 — док-конвергенция [XS]
module.yaml/compose уже на 18.4 (63ce627). Остатки: `AGENTS.md:207` «Единый PostgreSQL 16», `core/modules/postgres/docker-compose.base.yml:3` STRUCTURE `▶ postgres:16`, `.ai/about-for-new-conversations-in-chat.md:19` «PostgreSQL 16». Править все три на 18.4 (или «PostgreSQL 18» — по формулировке module.yaml). **AC:** `grep -r "PostgreSQL 16\|postgres:16"` по репо (вне .ai/plans) пуст.

### T2.2 [AI-0051+0052] · LDD-trajectory: 29 копий → канон-хелпер [S]
Факт: 29 рукокопий блока траектории (test_platform_export_metrics.py ×20, test_cert_collector.py ×5, test_host_collector.py ×4); канон `tests/_conftest/ldd.py:_print_ldd_trajectory`; cert_collector-вариант ПОТЕРЯЛ found_imp9-assert (anti-illusion выключен де-факто); host_collector итерирует `["message","msg"]` через getattr.
Дизайн: механическая замена копий на import канона; cert_collector-тестам вернуть found_imp9-assert; ноль поведенческих изменений сьютов кроме восстановления проверки. **AC:** grep копий (маркерная строка траектории) вне _conftest — 0; все три файла зелёные; cert_collector фейлится при отсутствии IMP:9 (негатив-проверка руками при имплементации).

### T2.3 [AI-0063] · check_generated(): один хелпер вместо 7 копий [M]
Факт: P-14 (полный diff) применён только в generate_entrypoint_manifest.py:611-650; ещё 6 сайтов режут `diff_lines[:20]`: generate_platform_env.py:500, generate_secrets_manifest.py:285, sync_env_defaults.py:971, sync_requirements.py:206, generate_agents_md.py:431 и :476.
Дизайн: общий helper (рядом с генераторами — core/internal/scripts/ или shared) `check_generated(path, content) -> int` с полной диагностикой DIFF (без среза) + миграция 6 сайтов; DIFF_LINES_MAX-семантику сохранить для rc, но печать — полная. **AC:** искусственный drift >20 строк в любом манифесте показывает источник divergence полностью; check-manifests зелёный на чистом дереве.

### T2.4 [AI-0073] · boto3: один строитель с явными override'ами [S]
Факт: upload.py:203-206 (connect 30/read 60/standard retries) vs wal_sync.py:222 (10/30/max_attempts=3 — самый жёсткий, RPO-критичный) vs retention.py:443-446 (_BOTO_*=30/60); s3_client.py:11-12 заявляет «единственное место» construction — вдвойне ложь.
Дизайн: все три модуля строят клиент через s3_client-строитель с явными per-call override параметрами (wal_sync сохраняет свои жёсткие значения как именованную константу WAL_SYNC_S3_TIMEOUTS с TRAP-rationale RPO); докстринг s3_client привести к факту. **AC:** grep `boto3.client` в backup-cron — только s3_client.py; wal_sync тайминги не изменились (regression-тест значений); unit на override-проброс.

## Волна 3 — атомарность и операционные ловушки (8 параллельных, файлы ∩=∅)

### T3.1 [AI-0008] · Атомарная запись htpasswd/env_file [XS]
htpasswd.py:148-150 и converge/projects.py:309-311: write_text + chmod-after (umask-окно, неатомарно). Перевести на `shared.atomic_writer.atomic_write_text(mode=...)` (0o600 / 0o640 соответственно). **AC:** unit — читатель между созданием и chmod видит либо старый, либо полный новый файл; права выставлены атомарно.

### T3.2 [AI-0009] · status-metrics.json: атомарная запись + честный комментарий [XS]
json_writer.py:120-125 пишет truncate-in-place; platform_export_metrics.py:15 утверждает «status-page never sees partial file». Выбрано: temp-file + os.replace (снимает класс torn-read целиком; reader уже толерантен) + исправить комментарий. TRAP[DOCKER-BIND-MOUNT] обновить (rename-семантика bind-mount валидна на Linux). **AC:** unit — конкурентный читатель не видит частичный JSON; комментарий соответствует коду.

### T3.3 [AI-0017+0037] · github_ops: таймауты + честный контракт [S]
github_ops.py:47-52,75-80,84-96 — gh/git без timeout, любой фейл → `return True` (:13 «Never raises … return True»); project_scaffolder.py:20 — «Never auto-creates GitHub repos» при реальном `gh repo create` Step 7 (:811 вызывает безусловно).
Дизайн: subprocess.run с DEPLOY_TIMEOUT-класс константой (новая GITHUB_OPS_TIMEOUT в shared/timeouts.py); фейл таймаута/rc → return False + IMP:8; докстринг scaffolder описывает авто-создание repo как шаг (условия: gh CLI присутствует). **AC:** зависшая сеть не висит вечно; неудача создания repo больше не репортится успехом; докстринги = поведение.

### T3.4 [AI-0015+0016] · Retry-предикат + честный pull-OSError [S]
reporting.py:142-160 — FileNotFoundError (bash) выжигает весь retry-бюджет (~100s/модуль); parallel_runner.py:99-100 — `except OSError: pass` на compose-read → бессмысленный retry_pull несуществующего image.
Дизайн: retry-цикл получает retryable-предикат (permanent: FileNotFoundError/PermissionError → немедленный fail; временные — как сейчас), прецедент — shared/retry; pull_module_images: OSError → IMP:8 warn → needs-build без retry-бюджета. **AC:** permanent-ошибка фейлит за один проход; OSError логируется и не порождает pull-retry; суммарное время негативного сценария падает кратно.

### T3.5 [AI-0018+0019] · dev-cert таймауты + chown не молчит [S]
dev_cert_generator.py:190-195,267-279,319-323,412-432 — четыре subprocess без timeout (siblings ограничены DEFAULT_OPENSSL_TIMEOUT); platform-secrets/installer.py:172-173,284 — contextlib.suppress(OSError, TimeoutExpired) вокруг chown без warn/rc.
Дизайн: переиспользовать ssl_certs-константу во всех четырёх местах; chown-suppress → IMP:7 warn с путём + rc включён в prereq-результат (chmod-успех ≠ prereq-pass при chown-фейле). **AC:** introspection-тест — все subprocess-вызовы файла несут timeout; chown-фейл виден в prereq-отчёте.

### T3.6 [AI-0072] · status-page: честные env-требования [S]
module.yaml:53-55 требует PLATFORM_MASTER_EMAIL/PASSWORD, которых app.py не читает (Basic Auth живёт в nginx htpasswd); STATUS_PAGE_HOST (app.py:55) не задаётся никем.
Дизайн: env_requires перенести к потребителю (nginx module.yaml — проверить, что secret-definitions уже определяет глобально: да, PLATFORM_MASTER_EMAIL/PASSWORD определены в secret-definitions.yaml) ИЛИ удалить из status-page; мёртвый read STATUS_PAGE_HOST удалить; `make generate-manifests` (secrets-manifest пересоберётся). **AC:** env_requires каждого модуля = реально читаемым переменным; grep STATUS_PAGE_HOST пуст; check-manifests зелёный.

### T3.7 [AI-0071] · hermes-agent module.yaml: правда об экспозиции [XS]
module.yaml:8,14 «NO ports», proxy-net+project-net vs compose:118-119 (127.0.0.1:9119/8642) + сети hermes-agent-net/observability-net; healthcheck.sh:75 зависит от loopback-портов.
Дизайн: networks/exposure текст = compose-факту; loopback-only binding задокументирован как исключение (не host-публикация). **AC:** module.yaml не противоречит compose; gates, читающие module.yaml, не видят ложной публикации.

### T3.8 [AI-0075] · platform-secrets: persistent-by-design формулировка [XS]
module.yaml:10,49 обещает tmpfs/«never touches disk decrypted» — фактически SECRETS_ENV_FILE=/var/lib/platform/run/secrets.env персистентен (решение 142-W2). Переформулировать: decrypted secrets persist on disk 0600 вне payload/git; tmpfs — только temp-key (/dev/shm). **AC:** module.yaml не содержит ложного инварианта; формулировка согласована с AGENTS.md DO NOT #4.

## Волна 4 — резолверы конфига и честность ручек (5 параллельных, файлы ∩=∅ внутри волны)

### T4.1 [AI-0024+0025] · Shell/make потребители читают резолверы [M]
Факт: NODE_CONFIGS_DIR/NODE_CONFIGS_REMOTE_BASE резолвятся 4 способами (lib/secrets.sh:35, decrypt_secrets.py:449, modules_healthcheck.py:304 — мимо canonical resolver; платформенный exporter — через него); PLATFORM_RUN_BASE игнорируется всеми shell/make потребителями (platform-export-metrics.sh:64-65 unconditional export, notify-hook.sh:41, templates/module.mk:51, lib/secrets.sh:55) — установка канонического knob'а расщепляет state на два каталога.
Дизайн: shell-обёртки получают одну строку инициализации от Python-резолвера (паттерн `eval "$(python3 -m core.internal.shared.deploy_paths shell-exports)"` — новый subcommand, тонкий фасад <150 LOC) ЛИБО прямой консультации `${PLATFORM_RUN_BASE:-default}` перед экспортом — выбрать один механизм на все сайты; decrypt_secrets/modules_healthcheck переходят на deploy_paths-резолвер. Файлы: lib/secrets.sh, decrypt_secrets.py, modules_healthcheck.py, platform-export-metrics.sh, notify-hook.sh, templates/module.mk (+deploy_paths shell-exports). **AC:** установка PLATFORM_RUN_BASE relocates ВСЕ run-артефакты единообразно (структурный тест); NODE_CONFIGS_REMOTE_BASE override слышен healthcheck-фильтром и secrets-glob; парity-гейт на literals не RED.

### T4.2 [AI-0002r+0020r+0039] · Таймаут-документация: одна цифра 900 [S]
Остатки инертного дрейфа: platform-infra.yaml:314 `PLATFORM_DEPLOY_TIMEOUT: "600"` + platform-env.yaml:223 + .env.example:278 + sync_env_defaults.py:804-805 "(default: 600)" vs SoT 900; remote_executor.py:66,:227 комментарии «(600s)»; timeouts.py:21 таблица «deploy=600»; channels/base.py:15 + channels/__init__.py:19 докстринги «(default 600s)»; core_deliverer TRAP-комментарий с «DEPLOY_TIMEOUT=600».
Дизайн: SoT env-default → 900 + `make generate-manifests`; все комментарии/докстринги/строка таблицы — 900 со ссылкой на TRAP cold-node (timeouts.py:147-150). **AC:** `grep -rn "600" ` по перечисленным местам пуст; regen-артефакты закоммичены; check-manifests зелёный.

### T4.3 [AI-0026+0027] · Фейковые флаги удалены [S]
context_deployer.py:1230,1250 — `--no-fallback-build` парсится и выбрасывается (fallback удалён DevPlan 091; комментарий «no flag, no fallback» при живом threading ghcr_fallback_build :355,:482,:507-511); engine.py:166 + cli.py:43,71,98 — `keep_images`/`--keep-images` без prune-логики (docstring «keep during prune» о несуществующем).
Дизайн: удалить флаг/параметр по всей цепочке (argparse → CliArgs → threading → сигнатуры), включая ruff ignore-маркеры; обновить callers/тесты. **AC:** `--no-fallback-build` и `--keep-images` отсутствуют в CLI; `grep ghcr_fallback_build` пуст; help-texts не упоминают удалённое.

### T4.4 [AI-0028+0029r+0030] · Мёртвые schema-обещания сняты [M]
node.schema.json:277-286 `postgres_init_databases` (+ записи в node-configs/*/node.yaml) — никто не исполняет; `repos.node_configs` (:287-300 часть) — ноль accessors (**repos.core оставить — живой**, context_overlay.py:308); module.schema.json:117-120 `systemd.*` + platform-secrets/module.yaml:29-31 — инсталлер держит UNIT_NAME hardcode, RequiredBy живёт в .unit-файле.
Дизайн: снять свойства из схем + записи из node-configs (обе ноды); systemd-блок удалить из schema+module.yaml (выбор «удалить», не «имплементить»: реализация добавила бы неиспользуемую поверхность перед запуском; TRAP[DECISION] с Rev «если понадобится per-module systemd-конфиг»). Валидаторы схем прогнать на всех node-configs/module.yaml. **AC:** схемы не валидируют неисполняемые поля; `grep postgres_init_databases\|repos.node_configs\|systemd:` по конфигам пуст; check-manifests/gates зелёные.

### T4.5 [AI-0074] · LT_METHOD emission удалён [XS]
runner_cli.py:451 эмитит `"LT_METHOD": spec.method` — читателей ноль (web.py GET, остальные POST захардкожены). Удалить эмиссию (или реализовать dispatch — выбор «удалить»: сценарии стабильны, поверхность не растёт). **AC:** grep LT_METHOD пуст; loadtest smoke зелёный.

## Волна 5 — конвергенция дублей (6 параллельных, файлы ∩=∅ внутри волны)

### T5.1 [AI-0064] · curl-пробы: SoT + документированная граница [S]
tor_proxy_check.py:58 (`-s`, молча глотает OSError→None :68-69) vs SoT http_probe.py:43 (`-sS`, fail-verbose) — один слой, обязан импортировать; status-page collectors/checks/http.py:71 (`-sSk`, dict) — кросс-слойная копия, но module contract :15 запрещает импорт core/internal → добавить TRAP[DECISION] о сознательной копии с Rev-условием + выровнять семантику ошибок (OSError surfaced в dict.error — уже сделано, зафиксировать в TRAP).
**AC:** tor_proxy_check импортирует SoT (curl_http_code не определяется локально); status-page копия несёт TRAP с обоснованием границы модуля; флаг-расхождение задокументировано осознанно.

### T5.2 [AI-0066] · wildcard-san: cert_collector импортирует канон [XS]
cert_collector.py:67,83-86 — третья незадокументированная копия `_san_match` + избыточный re-test условия :84-85. Заменить на `shared.ssl_certs` helper (как tls_check.py). **AC:** локального matcher'а нет; unit cert_collector зелёный.

### T5.3 [AI-0067] · GENERATED_HEADER — одна константа [XS]
practices/generators.py:57 vs maturity.py:61 (короче, работает по prefix-совпадению :230). maturity импортирует константу generators (или общую в practices/shared). **AC:** одна строка-определение на репо; drift-детект maturity зелёный.

### T5.4 [AI-0068] · readiness читает через gated loader [XS]
collectors/readiness.py:52-63 `_read_metrics_file` без schema_version-гейта — /healthz может PASS со stale-schema файлом при FLAG /health. readiness переиспользует `load_status_metrics` (config.py:217). **AC:** /healthz FAIL на старой схеме так же, как /health; общий loader, второй reader удалён.

### T5.5 [AI-0055+0061] · Dotenv-грамматика едина; export_shell усыновлён [M]
Три грамматики: env_reader.py:52 `_LINE_RE` («всё после =», без кавычек/комментариев) vs secrets_env_parser.py:79 `_parse_line` (кавычки + unquoted-#) vs decrypt_secrets.py:265 `_yaml_to_env` (своя re-имплементация quote-strip + `'\\''`-escaping); export_shell (secrets_env_parser.py:354) жив только в своих тестах.
Дизайн: env_reader переводится на secrets_env_parser.parse (одна грамматика; учесть недавний strict-mode c1b8b82 — env_reader остаётся lenient-фасадом поверх общего парсера, strict пробрасывается параметром); decrypt_secrets._yaml_to_env заменяется вызовом export_shell; дублирующая имплементация удаляется. Это самое рискованное изменение волны — покрыть тестами на `FOO="bar #x"`, многострочные, quoted-# во всех трёх входах ДО рефакторинга (characterization), потом менять. **AC:** characterization-тесты доказывают идентичный разбор одной строки всеми тремя путями; старые `_LINE_RE`/_yaml_to_env тела удалены.

### T5.6 [AI-0054] · Валидатор схем pinned к python-Draft7 [S]
validate_orchestrator.py:153-175 `_detect_validator` выбирает ajv по наличию в PATH — «единственная Draft7-точка» (schema_validator.py:14) обходится окружением: один и тот же YAML валидируется разными движками dev-vs-CI.
Дизайн: ajv-ветка удалена; валидация всегда python Draft7Validator (schema_validator); сообщение об ошибке единообразное; опция выбора — не окружение, а явный config при будущей необходимости (TRAP[DECISION]). **AC:** тест с фейковым `ajv` в PATH — движок всё равно python; докстринги/доки без упоминания ajv-приоритета.

## Волна 6 — мёртвый код и API-гигиена (7 параллельных, файлы ∩=∅ внутри волны)

### T6.1 [AI-0058] · load_existing_manifest демонтирован [S]
Удалить функцию (generate_entrypoint_manifest.py:341), пункт pinning-гейта (test_gate_generate_entrypoint_manifest_no_self_read.py:101-107 требует её НЕиспользование — оговорка снимается вместе с функцией), связанные тесты. **AC:** символа нет; гейт зелёный без оговорки; generate-entrypoint-manifest работает.

### T6.2 [AI-0059] · requires_compose_project удалена [S]
compose_files.py:115 — prod-refs нулевые, docstring ссылается на несуществующих потребителей; удалить fn + инвентарную строку + тесты (test_shared_compose_files.py:120-131). **AC:** символа нет в коде/инвентаре; gates зелёные.

### T6.3 [AI-0060] · Test-only геттеры deploy_paths удалены [XS]
get_canonical_paths/get_deprecated_paths (:130,:142) обслуживают только свой gate-тест; тест перевести на прямые константы, геттеры удалить. **AC:** гейт test_gate_deploy_paths зелёный без геттеров.

### T6.4 [AI-0062] · Orphaned registry CLI срезан [S]
project_registry.py:147/236/298 + argparse-dispatch :424-470 (`register/deregister/list`) — конкурирующий «второй» реестровый CLI; канон — scaffold-путь (scaffold.mk:85). Срезать CLI-поверхность, библиотечные функции (validate_project_name/discover_llm_projects) сохранить. **AC:** `python3 -m core.internal.shared.project_registry` без verb → честная ошибка/short-help без мёртвых команд; scaffold-verbs работают.

### T6.5 [AI-0053] · Мёртвые typed-accessors удалены [XS]
platform_config.py:199 default_s3_prefix, :273 default_platform_context (zero callers, docstring признаёт) + неиспользуемый alias-слой; тесты accessor'ов удалить. **AC:** символов нет; get_default-путь покрыт.

### T6.6 [AI-0056] · Engine→flow шимы инlinedены [S]
engine.py:48-52 импортирует flow-функции ради single-statement шимов (_atomic_up:508-513 и др.), прод-вызовов шимов нет — только тесты (test_deploy_engine.py:329,595,610). Инline: callers зовут flow/shared напрямую; тесты перенацелить; шимы удалить. **AC:** pass-through методов нет (AST/grep); сьют зелёный.

### T6.7 [AI-0005] · langfuse resources промоделированы [S]
module.yaml:38-42 декларирует limits+reservations web без worker; compose держит langfuse-worker (1024M) и web без reservations. Дописать worker в module.yaml resources + reservations синхронно compose; test_memory_limits.py расширить (worker в сумме лимитов, reservations сверяются с base.yml). **AC:** gate покрывает langfuse (web+worker); рассинхрон module.yaml↔compose ловится тестом.

## Волна 7 — сигнатуры и док-дрейф (10 параллельных, файлы ∩=∅ внутри волны)

### T7.1 [AI-0031] · volumes/vhosts ветвят dry_run/report_only [XS]
volumes.py:141-142, vhosts.py:148-149 подавляют ARG001 и не ветвят (докстринги обещают); прецедент — networks.py/runtime.py. Подключить флаги по образцу соседей (мутации под dry_run — preview-лог). **AC:** dry_run-вызов volumes/vhosts не мутирует и печатает план; unit на обеих.

### T7.2 [AI-0032] · Мёртвое поле node_yaml удалено [XS]
docker_orchestrator.py:732 Protocol field + :763 help «(unused in docker_orchestrator)» — удалить поле/help, callers не передают. **AC:** help-текст чистый, Protocol без мёртвого поля.

### T7.3 [AI-0033] · scaffold_helpers: параметры используются или сняты [S]
gen_ai_platform_yaml(org):113, gen_project_makefile(domain):292, gen_project_platform_md(name):482, register_in_node_yaml(node):547 — ARG001-подавленные; реальные callers передают исчезающие значения. Решение по каждому: использовать (если значение должно попадать в артефакт — проверить ожидания callers) или снять параметр + правка callers. **AC:** ни одного ARG001-подавления на этих четырёх; артефакты scaffold содержат ожидаемые данные (тесты scaffold зелёные).

### T7.4 [AI-0034] · Лишние параметры сигнатур срезаны [XS]
exit_code_from_results(crit,warn,**deployed**) (orchestrator_metrics.py:99) и verify_mirror(context,…) (context_promoter.py:211) — параметры не участвуют; срезать + callers. **AC:** сигнатуры без неиспользуемых аргументов; тесты зелёные.

### T7.5 [AI-0035] · Vestigial-цепочка resolve_node_yaml [S]
Минимум: убрать ложное заявление overlay_deliverer.py:92 («projects_dir sets PLATFORM_ROOT» — не задаёт); далее по решению имплементатора: снять projects_dir-параметр цепочки node_resolver.py:96-100 → overlay_deliverer.py:99 → remote_executor.py:92 (Protocol) с правкой CLI-вызова :332, либо оставить с честной VESTIGIAL-аннотацией везде (byte-compat keep-decision уже задокументирован в контракте модуля). **AC:** ни одного ложного докстринга о projects_dir; решение зафиксировано TRAP[DECISION].

### T7.6 [AI-0036] · Lookahead regex починен + негативный тест [XS]
hardcoded_paths.py:46-48 `/home/[\w.-]+/(?!runner/work/)[\w.-]+/` — исключение CI-path не работает (lookahead после первого компонента). Исправить на `/home/(?!runner/work/)...`; добавить R5-негатив: `/home/runner/work/repo/repo/...` НЕ матчится, обычный `/home/user/...` матчится. **AC:** оба кейса покрыты unit-тестом; статик-гейт зелёный.

### T7.7 [AI-0040] · healthcheck_poller докстринги = факт [XS]
STRUCTURE :6 «⎋ str(healthy|unhealthy)» и инварианты :21-22/:109 «returns unhealthy» vs реальный `HealthcheckResult` со статусом "timeout" (:83-95). Докстринги привести к dataclass-контракту (класс-level @io :106 уже верный). **AC:** grep "returns \"unhealthy\"" в файле пуст; STRUCTURE отражает HealthcheckResult.

### T7.8 [AI-0041] · policy_schema: no-op валидатор снят, STRUCTURE честный [XS]
_validate_default_profile_exists (:279-293) — bare `return v` (реальная проверка в from_yaml step 4a :371-377); STRUCTURE :2 называет несуществующие load_yaml/validate_with_jsonschema. Удалить no-op (или rename в honest-заглушку с @note), STRUCTURE переписать по реальным именам. **AC:** STRUCTURE имена резолвятся в определения файла.

### T7.9 [AI-0042..0045] · Stale STRUCTURE/TRAP батч [XS]
retry.py:31-34 TRAP «запись не добавлена» — запись есть в shared/AGENTS.md → TRAP закрыть (ARCHIVED); preflight.py:3 STRUCTURE run_all_checks → run_preflight (:493); state_machine.py:19-21 инвариант про subprocess.run(timeout=120|600) — утверждение убрать (subprocess в файле нет; пересечение с T1.4 по файлу — допустимо, разные секции, выполнить последовательно); dead_code.py:4 STRUCTURE _scan_makefile_refs/_scan_precommit_refs → _scan_file_refs (:300). **AC:** все названные символы резолвятся; TRAP retry.py в ARCHIVED-регионе.

### T7.10 [AI-0076] · PYTHONPATH-фикс распространён на sibling-фасады [XS]
add-vhost.sh:25-27 несёт TRAP[BUG] P1-фикс `export PYTHONPATH="${PLATFORM_ROOT}:…"`; project-list.sh:12 и remove-project.sh:11-13 исполняют `python3 -m` без экспорта (remove даже вычисляет PLATFORM_ROOT и не экспортирует). Применить тот же двухстрочный паттерн обоим. **AC:** запуск фасадов из произвольного cwd с чистым PYTHONPATH работает (структурный тест/ручная проверка); TRAP-ссылки согласованы.

## Волна 8 — тестовая гигиена (5 параллельных, файлы ∩=∅ внутри волны)

### T8.1 [AI-0046] · Приватные импорты → публичная поверхность [S]
test_platform_export_metrics.py:48 (_get_node_name,_get_node_yaml_path), test_cert_collector.py:35 (_load_cert,_san_match): где возможно — гнать через main()/публичные результаты; где это seam по дизайну — пометить комментарием intentional-seam (единый маркер). **AC:** underscore-импорты либо исчезли, либо явно помечены единым маркером; сьюты зелёные.

### T8.2 [AI-0047] · Wiring-ассерты → наблюдаемые исходы [S]
test_deploy_orchestrator.py:90-98 (+семейство routing) — patch.object(_deploy_sequential)+assert_called_once_with(["postgres","redis"],"/mods","/core",{}) пинают приватную декомпозицию. Переписать на наблюдаемые результаты (порядок исполнения/итоговые счётчики через фейковый orchestrator-слой). **AC:** сьюта не референсит приватные методы в ассертах; покрытие сценария seq/parallel сохранено.

### T8.3 [AI-0048] · Литералы выводятся из инфраструктуры [XS]
test_status_collectors.py:33,520,541 — curl_mock.assert_called_once_with("grafana:3000","/api/health",5) нарушает инвариант №3 tests/AGENTS.md. Выводить из infra-фикстур/констант (collector-конфиг). **AC:** литералов контейнера/порта/таймаута в ассертах нет; тест ловит регрессию endpoint'а (негатив-проверка при имплементации).

### T8.4 [AI-0049] · Приватные диспатчеры → публичные глаголы [S]
test_project_status_contract.py:27 (_dispatch) и test_practices_check_project.py:39,285 (_run_check) — гоняют приватные диспетчеры как «канонические объекты». Перевести на публичные verb-вызовы (CLI-слой), контракт-ассерты сохранить. **AC:** приватных диспатчеров в тестах нет; рефакторинг CLI не ломает сьют (проверить структурно: мок-точка = публичная функция).

### T8.5 [AI-0050] · Тавтологические ассерты заменены [XS]
test_cross_layer_helpers.py:336-337 — isinstance(bool/str) аннотированного хелпера (языковая гарантия, R2-класс). Заменить на поведенческий ассерт (значения соответствуют ожидаемым для fixture-входа). **AC:** ассерты фальсифицируемы (падают при сломанном хелпере, а не только при смене языка).

---

## $TEST_SPEC

| Test file | Test function | Scenario | Module under test |
|-----------|---------------|----------|-------------------|
| tests/unit/test_deploy_orchestrator_envcheck.py | test_env_check_crash_is_loud | validator raises → модуль failed + env_check_error в отчёте, не «missing=[]» | deploy_orchestrator |
| tests/unit/test_deploy_orchestrator_envcheck.py | test_env_check_pass_deploys_negative | валидатор вернул [] → деплой продолжается (регрессия/R5) | deploy_orchestrator |
| tests/unit/test_phases_docker_w9.py (ext) | test_llm_provision_failure_surfaces | rc≠0 → ERROR-строка + report entry; IMP:9-success отсутствует | phases/docker |
| tests/unit/test_health_criterion_parity.py | test_running_without_healthcheck_is_healthy | collector-вердикт == канон на healthy/unhealthy/none | docker_collector ↔ docker_compose |
| tests/unit/test_state_machine_phase_hash.py | test_phase_code_edit_invalidates | мутация phases/*.py байт → needs_rerun True; стабильность на неизменном дереве | state_machine |
| tests/unit/test_reporting_timeouts.py | test_healthcheck_invoke_single_budget | reporting.py и module_interface используют HEALTHCHECK_CMD_TIMEOUT | helpers/reporting, module_interface |
| tests/unit/test_memory_limits.py (ext) | test_langfuse_worker_and_reservations | сумма лимитов включает worker; reservations сверяются | gate memory_limits ↔ langfuse compose |
| tests/unit/test_backup_s3_client.py (ext) | test_builder_overrides | wal_sync/upload/retention идут через s3_client builder; жёсткие wal_sync-тайминги сохранены | backup-cron s3_client |
| tests/unit/test_htpasswd_atomic.py | test_no_partial_read_window | конкурентный read видит старый или полный файл; mode enforced | htpasswd, converge/projects |
| tests/unit/test_status_metrics_writer.py | test_torn_read_impossible | temp+os.replace: читатель никогда не видит частичный JSON | metrics/json_writer |
| tests/unit/test_github_ops_contract.py | test_timeout_and_false_on_failure | зависший gh → False по таймауту; фейл → False | scaffold/github_ops |
| tests/unit/test_healthchecks_retry_predicate.py | test_permanent_error_single_pass | FileNotFoundError → fail без retry-бюджета | helpers/reporting |
| tests/unit/test_parallel_runner_pull.py | test_oserror_logged_needs_build | OSError → warn + needs-build, без retry_pull | parallel_runner |
| tests/unit/test_dev_cert_timeouts.py | test_all_subprocess_bounded | introspection: каждый subprocess.run несёт timeout | nginx/dev_cert_generator |
| tests/unit/test_platform_secrets_prereq.py | test_chown_failure_visible | chown-фейл → warn + prereq fail | platform-secrets/installer |
| tests/unit/test_status_page_env_honesty.py | test_env_requires_matches_reads | env_requires module.yaml ⊆ реально читаемых переменных | status-page/module contract |
| tests/unit/test_run_base_relocation.py | test_knob_relocates_all_artifacts | PLATFORM_RUN_BASE перемещает все run-артефакты единообразно | deploy_paths + shell consumers |
| tests/unit/test_dotenv_grammar_unification.py | test_quoted_hash_identical_across_parsers | `FOO="bar #x"` идентично разбирается env_reader/parser/export_shell (characterization сначала!) | env_reader, secrets_env_parser, decrypt_secrets |
| tests/unit/test_validator_pinned.py | test_fake_ajv_on_path_ignored | фейковый ajv в PATH → движок python-Draft7 | validate_orchestrator |
| tests/unit/test_static_hardcoded_paths.py (ext) | test_ci_path_excluded_negative | /home/runner/work/… не матчится; /home/user/… матчится (R5) | static/hardcoded_paths |
| tests/gates/test_gate_generate_entrypoint_manifest_no_self_read.py (mod) | — | оговорка load_existing_manifest снята, гейт зелёный | generate_entrypoint_manifest |
| tests/unit/test_full_diff_diagnostics.py | test_divergence_shown_beyond_line20 | drift >20 строк печатается полностью во всех 7 сайтах | check_generated helper |

Остальные задачи волны — механические/док-правки: `$TEST_SPEC: covered by существующие сьюты (зелёные после правки)` — @rationale: изменение контрактов без новой бизнес-логики; новые тесты сверх таблицы не плодить (test-honesty: каждый тест должен иметь шанс упасть).

## $PARALLEL_GROUPS

### Волна 1 (silent-failure; файлы ∩=∅)
- Tasks: T1.1, T1.2, T1.3, T1.4, T1.5
- Command: `coder Read .ai/plans/meta-refactoring/17-DevPlan-ai-code-fixes.md, implement Wave 1: T1.1, T1.2, T1.3, T1.4, T1.5`
### Волна 2 (cheap-high-signal; ∩=∅)
- Tasks: T2.1, T2.2, T2.3, T2.4
### Волна 3 (atomicity/foot-guns; ∩=∅)
- Tasks: T3.1, T3.2, T3.3, T3.4, T3.5, T3.6, T3.7, T3.8
### Волна 4 (resolvers/knobs; ∩=∅)
- Tasks: T4.1, T4.2, T4.3, T4.4, T4.5
### Волна 5 (dedup; ∩=∅; T5.5 — characterization-first)
- Tasks: T5.1, T5.2, T5.3, T5.4, T5.5, T5.6
### Волна 6 (dead-code; ∩=∅)
- Tasks: T6.1, T6.2, T6.3, T6.4, T6.5, T6.6, T6.7
### Волна 7 (signatures/doc-drift; ∩=∅; T1.4 и T7.9 шарят state_machine.py — выполнять в разных волнах: T1.4 раньше, T7.9 позже)
- Tasks: T7.1, T7.2, T7.3, T7.4, T7.5, T7.6, T7.7, T7.8, T7.9, T7.10
### Волна 8 (test-hygiene; ∩=∅; после W2.2 — те же тестовые файлы у T2.2/T8.1 разведены волнами)
- Tasks: T8.1, T8.2, T8.3, T8.4, T8.5

## Acceptance Criteria (сводная таблица)

| SC | Критерий | Верификация |
|----|----------|-------------|
| SC1 | Крах валидатора ≠ pass (env-check/provision/health/hash) | T1.1-T1.5 тесты + `make check TEST_FILE=` каждого |
| SC2 | Контракты = коду (PG18.4, module.yaml ×3, doc-drift кластер) | grep-наборы из задач T2.1/T3.6-8/T7.7-9 пусты |
| SC3 | Одно знание — одна реализация | grep-наборы T4.x/T5.x; parity-тесты |
| SC4 | Мёртвый API удалён с гейтами; фейки исчезли из CLI | grep T6.x/T4.3/T4.5; gates зелёные |
| SC5 | QA-сигнал восстановлен | T2.2 (IMP:9-assert), T8.x (фальсифицируемые ассерты) |
| SC6 | Арбитры зелёные после каждой волны | `make check` + `make agent-check` per wave; журнал `.ai/logs/runs.jsonl` |

## File Manifest (ключевые, ~70)

Волна 1: deploy_orchestrator.py · phases/docker.py · docker_collector.py + collectors/checks/containers.py · state_machine.py · helpers/reporting.py + module_interface.py
Волна 2: AGENTS.md · .ai/about-for-new-conversations-in-chat.md · postgres/docker-compose.base.yml · tests (export_metrics/cert/host collectors) · 6 generator-скриптов + новый helper · backup-cron scripts/{upload,wal_sync,retention,s3_client}.py
Волна 3: htpasswd.py · converge/projects.py · metrics/json_writer.py · platform_export_metrics.py · github_ops.py · project_scaffolder.py · reporting.py · parallel_runner.py · dev_cert_generator.py · platform-secrets/installer.py · status-page/{module.yaml,app.py} · hermes-agent/module.yaml · platform-secrets/module.yaml
Волна 4: lib/secrets.sh · decrypt_secrets.py · modules_healthcheck.py · platform-export-metrics.sh · notify-hook.sh · templates/module.mk · platform-infra.yaml + regen (platform-env.yaml, .env.example) · sync_env_defaults.py · remote_executor.py · timeouts.py · channels/* · context_deployer.py · engine.py + cli.py · node.schema.json · module.schema.json · node-configs/*/node.yaml · runner_cli.py
Волна 5: tor_proxy_check.py · collectors/checks/http.py · cert_collector.py · practices/{generators,maturity}.py · collectors/{readiness,config}.py · env_reader.py · secrets_env_parser.py · decrypt_secrets.py · validate_orchestrator.py
Волна 6: generate_entrypoint_manifest.py + его gate · compose_files.py · deploy_paths.py + gate-тест · project_registry.py · platform_config.py · engine/flow + тесты · langfuse/{module.yaml,test_memory_limits}
Волна 7: volumes/vhosts/networks(converge) · docker_orchestrator.py · scaffold_helpers.py · orchestrator_metrics.py · context_promoter.py · node_resolver/overlay_deliverer/remote_executor · hardcoded_paths.py + тест · healthcheck_poller.py · policy_schema.py · retry/preflight/state_machine/dead_code (docs) · project-list.sh · remove-project.sh
Волна 8: tests/unit/{test_platform_export_metrics,test_cert_collector,test_host_collector(частично T2.2),test_deploy_orchestrator,test_status_collectors,test_project_status_contract,test_practices_check_project,test_cross_layer_helpers}.py

## Design Decisions

## @rationale Q: одиночный DevPlan без Brief? A: внешние аудит-артефакты 08-ai-code + свежая верификация @HEAD заменяют Brief (все ID пронумерованы, evidence file:line, cleanup-векторы предложены аудитом); прецедент — план 16; повторный CONFIRM добавил бы цикл без нового входа.
## @rationale Q: почему «удалить», а не «имплементировать» фейковые ручки (--keep-images, --no-fallback-build, LT_METHOD, systemd.*, postgres_init_databases)? A: ноль потребителей и ноль бизнес-требований на них; имплементация перед запуском добавляет непротестированную поверхность — противоположно pre-launch приоритету. Rev: появление требования → отдельная задача с тестами.
## @rationale Q: канон здоровья — какая сторона выигрывает (T1.3)? A: канон shared/docker_compose.py (running AND health∈{healthy,"",none}) — он же в lib/healthcheck.sh и гейтах деплоя; коллектор статуса подтягивается к большинству, а не наоборот; агрегатная жёсткость (WARN⇒FAIL) сохраняется отдельным решением (TRAP S-WARN).
## @rationale Q: dotenv-конвергенция — направление (T5.5)? A: secrets_env_parser — самый строгий и недавно укреплённый (strict-mode) парсер → становится единственной грамматикой; env_reader остаётся lenient-фасадом над ним; export_shell усыновляется в decrypt_secrets вместо третьей имплементации. Characterization-тесты пишутся ДО рефакторинга.
## @rationale Q: валидатор схем — почему python-pin, а не config (T5.6)? A: CI/dev паритет важнее гипотетической скорости ajv; env-sniffing — источник класса «локально зелёное, в CI красное»; config-опцию можно добавить позже явным решением.
## @rationale Q: T1.1 — почему не расширять DEPLOY_BEST_EFFORT на env-check? A: required-secrets — L1-класс практик (безопасность блокирует на любом уровне); молчаливый деплой без секретов противоречит канону; best-effort остаётся для вспомогательных шагов.
## @rationale Q: phase-hash — bytes, не mtime (T1.4)? A: git-checkout выравнивает mtime — mtime-инвалидация ненадёжна; sha256 байт детерминирован и дёшев на 6-10 файлах фаз.
## @rationale Q: порядок волн? A: W1 убивает класс «молчаливых провалов» (максимальный production-risk), W2 — дешёвый высокий сигнал (TOP30 Pre-пакет), W3-W4 — операционная гигиена, W5-W6 — конвергенция/мёртвый код, W7-W8 — механика без поведения. Каждая волна самодостаточна для релиза, если последующие отложены.

## Next Steps

### Волна 0
Use coder role and read /Users/tronyx/projects/ai-platform/.ai/plans/meta-refactoring/17-DevPlan-ai-code-fixes.md — execute T0.1 (fix-gate, commit tree, make check + make agent-check до чистоты)

### Волна 1
Use coder role and read /Users/tronyx/projects/ai-platform/.ai/plans/meta-refactoring/17-DevPlan-ai-code-fixes.md, implement Wave 1: T1.1, T1.2, T1.3, T1.4, T1.5 — последовательность шагов писать шагами: `make check` (до чистоты), `make agent-check`; один feat-коммит волны после зелёных арбитров

### Волны 2–8
Аналогично: «implement Wave N: <task ids>» — каждая волна = feat-коммит; `make gate MODE=fast` вручную не запускать (OOM-политика; арбитры — pre-push hook + CI)

$END_DEVPLAN
