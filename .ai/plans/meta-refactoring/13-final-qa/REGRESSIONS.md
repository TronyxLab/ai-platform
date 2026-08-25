<!-- GREP_SUMMARY: final-qa regressions behavior-changes hc-marker fail-open R9-blind merge-guard redact concurrency sshd timeouts -->
<!-- STRUCTURE: ▶ H-регрессии заявленных инвариантов → ⚡ M-хвосты → ∑ L/INFO → ⎋ none-blocking список -->

# REGRESSIONS — регрессии и изменения поведения (final QA, независимо)

Формат: SEV · файл:строка · что изменилось/сломано · REF. Всё верифицировано чтением кода.
Отличие от CRITICAL.md: не блокируют запуск сами по себе, но либо противоречат заявленным
инвариантам волн, либо создают новые failure modes.

---

## HIGH

**R1 · `_persist_new_vars` сохранил паттерн SEC-0015 вопреки @changes** ·
`core/internal/bootstrap/lifecycle/secrets_manager.py:856-864` · REF-0007.
`Path(tmp_path).open("w")` с фиксированным именем `.env.tmp` (symlink-follow) + chmod ПОСЛЕ записи +
наследование legacy-режима (`chmod(st_mode)` при существующем файле — 0644 сохраняется, tightening
«до 0600» из комментария Step 3.5 НЕ выполняется на этом пути). Митигация: umask 077 ставится рано
в `cli.py main()`/`node-lifecycle.sh` — окно закрывается для lifecycle-прогона; standalone
`secrets_manager ensure` и legacy-файлы остаются уязвимы. Тест `test_secret_writers_mode.py:132`
запатчил оба autogen-метода — путь непокрыт.

## MEDIUM

**R2 · Run-scoped hc_done-маркер не даёт fresh-гарантии в update-режиме** ·
`bootstrap/lifecycle/phases/docker.py:695-704` + `state_machine.py:203-209` · REF-0005.
Единственный читатель (φ11 registry_update deep-healthcheck) исполняется ДО φ12 deploy_update,
где живут писатель и свип ⇒ поглощённый маркер всегда написан ПРЕДЫДУЩИМ прогоном; возраст маркера
не проверяется. После ротации секретов в φ9 глубокий healthcheck стабильно подавляется чужим маркером.
Статус-кво улучшен (раньше хуже), но заявленная семантика «маркер этого прогона» не работает.

**R3 · Fail-open хвосты параллельного деплоя — тот же класс «success-marker до доказательства»** ·
`bootstrap/deploy/deploy_orchestrator.py:918-929,744-750,1263` · REF-0005.
JSONDecodeError → deployed=0, failed=[] → success-маркер; rc≠0 от deploy-many → только WARN;
broad-except группы (OSError от os.fork) вообще не попадает в failed. Упавший деплой гасит последний
глубокий healthcheck. Drain-ветка закрыта честно, orchestrator/group-error ветки — нет.

**R4 · R9 label-only детекция: слепая зона = ложный «converged»** ·
`bootstrap/converge/runtime.py:85-88,291-294` · REF-0014.
`ps` returncode≠0 (транзиентный сбой docker) и пустой stdout (legacy/docker run контейнеры без label)
оба схлопываются в `[]` → «No running containers» → continue → FULLY CONVERGED, exit 0, set_exit не
вызван. После удаления substring-fallback канал единственный; fail-open неотличим от успеха в отчёте.

**R5 · Merge-guard обходится частичным парсом** ·
`lifecycle/secrets_manager.py:636-648` + `shared/secrets_env_parser.py:114-117` · REF-0013.
Guard триггерит ТОЛЬКО при parse()==0; строки без `=` парсер молча скипает. Файл «1 валидная запись +
повреждённые операторские строки» проходит guard → merged из подмножества → атомарная перезапись
НЕОБРАТИМО теряет непарсабельные секреты. Класс P0, который REF-0013 закрывает, живёт уровнем ниже.

**R6 · Redact применяется ПОСЛЕ truncate хвоста stderr** ·
`bootstrap/core_deliverer.py:818-823` · REF-0007.
`redact_secrets(r.stderr.strip()[-500:], key)` — если ключ напечатан у границы окна, в лог уходит его
суффикс (`.replace(полный_ключ)` не матчит). Правильный порядок: redact → truncate. Тест гоняет полный
ключ, граничный случай не покрыт.

**R7 · Prelude-билдеры принимают значения ключей позиционным argv** ·
`shared/ssh_cmd_builder.py:450-458`, `build-ssh-cmd.sh:51-59`, вызов `bootstrap.sh:96` · REF-0007.
Короткоживущий python-процесс с ключом в argv (world-readable /proc) — окно мало, но env-канал был
бы строго безопаснее; заявка «ни локально не светятся» верна не полностью.

**R8 · Top-level concurrency reusable workflow не действует при вызове через uses + job без timeout** ·
`.github/workflows/deploy-project.yml:82-84,93` · REF-0011.
По контракту GitHub при вызове через `uses:` сериализует только `jobs.<id>.concurrency` caller'а;
standalone-запусков файла нет (единственный триггер workflow_call) ⇒ группа `deploy-${{inputs.project_name}}`
— мёртвый конфиг, иллюзия страховки. Реальная сериализация — workflow-level group шаблонного caller'а
(у adopter-генерации её нет); node-side flock остаётся единственной настоящей защитой. Плюс job без
`timeout-minutes` (дефолт 360) при cancel-in-progress:false — зависший receive блокирует очередь проекта
до 6 часов (parity: core-deploy 30, push-gate 25).

**R9 · Adopter генерирует антипаттерн REF-0012 для новых adopted-проектов** ·
`scaffold/project_adopter.py:206-240`; тест `test_project_adopter.py:483` закрепляет статус-кво ·
REF-0012/0011. `deploy-project.yml@main` (mutable) + tag-pins (`checkout@v7`, `build-push@v7`), без
concurrency/permissions. TRAP «adopted-легаси вне скоупа» не применим — это живой канал `make adopt-project`.
Два канала подключения проектов расходятся (new-project: stale-pin, adopt: mutable).

**R10 · sshd-нейтрализация держится на эвристике имени файла** ·
`security/sshd_policy.py:87,90-93,461-494` · REF-0016.
Glob `*cloud*` + regex без IGNORECASE: vendor drop-in с другим именем (`60-custom.conf`,
`PasswordAuthentication yes`) не нейтрализуется → apply True → φ1 зелёный при ослабленной политике;
post-apply сверки `sshd -T` в φ1 нет (S4 — отдельный ручной check-security). Также AllowUsers статичен:
свежий cloud-образ с пользователем `ubuntu` теряет следующую SSH-сессию (откат — console-only).

**R11 · ACME retry-бюджет противоречит внешнему таймауту** ·
`cert_orchestrator.py:127` × `issue_cert.py:105,417-426` · REF-0008.
Inner retry ≈ 2×300s+backoff ≈ 605s, внешний ISSUE_TIMEOUT=300s → медленный первый attempt съедает
бюджет, вторая попытка никогда не наступает, домен молча уходит в self-signed fallback. Backoff ко
всем rc≠0 включая LE rate-limit (5s повтор лишь жжёт лимит).

**R12 · Fingerprint salt покрывает не весь blocking-toolchain** ·
`check_suite/fingerprint.py:83` · REF-0107. Salt = (pytest, pytest-xdist, ruff); basedpyright —
БЛОКИРУЮЩИЙ чек манифеста — вне salt: `pip install -U basedpyright` при неизменном дереве → replay
старого зелёного от прежнего pyright. Закрытие TEST-094 частичное.

**R13 · Honesty deny-by-default: три тихие щели** · `tests/gates/test_gate_honesty_mode.py:111-127` ·
REF-0107. (1) glob только `*.yml` — `nightly.yaml` выпадает; (2) прямой вызов
`python3 -m core.internal.check_suite run …` мимо каналов-подстрок; (3) пин ищется по полному тексту —
закомментированный `# REQUIRE_HONESTY_MODE: fail` satisfies.

**R14 · Manifest oracle покрывает 1 манифест из 5** · `check_suite/manifest_oracle.py:13,65` · REF-0107.
Независимость реальная (yaml-only, AST-тест), но покрытие — только secrets-manifest; entrypoint-manifest/
platform-env/env_defaults_generated/smoke_env_generated остаются «генератор судит себя». Инвариант
DEEPSEEK (source=ci-secret ∧ ci_default) не выражен ни правилами, ни baseline.

**R15 · Postgres hook: GRANT исполняется не в той БД** ·
`modules/postgres/hooks/on_project_deploy.py:305-309,353-373` · REF-0002.
Все GRANT через один `_psql()` БЕЗ `-d <db_name>` → `GRANT CREATE,USAGE ON SCHEMA public` попадает в
БД `postgres` (проектная роль получает незапланированные права в админской БД), а в собственной БД
проекта grant' избыточен (роль = db owner → pg_database_owner владеет public с PG15). Функционально
деплой не ломается, но convergence проверяет неверную цель, а лишние привилегии в admin-DB — шум
принципа минимальных прав. Пред-range баг (DevPlan 133 W2), но теперь «честно верифицируется» тестами
на SQL-строку, не на целевую БД.

## LOW

- **L1** · `context_deployer.py:1044` — `_step_certs` единственный sibling без `subprocess.SubprocessError`
  в except (непоследовательное применение REF-0103 внутри одного файла).
- **L2** · `remote_executor.py:227`, `phases/docker.py:816` — устаревшие комментарии «600s» после
  выравнивания дефолтов на 900 (следующий parity-аудит зафиксирует фантомный divergence).
- **L3** · `watchdog.py:786` — OSError при re-save state прерывает остаток батча действий
  (расходится с собственным TRAP про host-cron timeout).
- **L4** · `ssl_certs.py:428-445` — FQDN validator не нормализует регистр (`Example.COM` → отказ,
  хотя vhost_renderer делает .lower()); ложный отказ легитимного входа.
- **L5** · `crontab:36-53` — flock -n skip полностью беззвучен (детекция пропуска бэкапа только через
  staleness метрики ≤25h).
- **L6** · `admin_client.py:475-478` — pagination требует int total_pages; строка "2" молча обрывает
  листинг после страницы 1 (класс PERF-082, объявленный закрытым). `async_get_key_info:533` глотает
  transport → None («ключа нет») — спящая мина дублей.
- **L7** · `file_lock.py:254-283` — TOCTOU existed→os.open: EACCES в микросекундном окне уходит в
  тихий degrade вместо fail-closed (узкое окно, но это ровно класс, объявленный недопустимым);
  `_depth/holder.refs` мутируются без блокировки (все потребители сегодня однопоточны).
- **L8** · `spool_retry/cleanup_spool` — unsentinel-файлы ретраятся бессрочно без disk-guard
  (осознанный fail-closed trade-off, алерт только Loki).
- **L9** · `reboot_policy.py:311-326` — сбой loginctl неотличим от «сессий нет» (fail-open детектор).

## INFO

- Snapshot пишется после healthcheck, но якорь previous_image stash'ится ДО compose-up (engine
  сохраняет образ до pull) — crash между up и записью снапшота теряет якорь этой попытки, старый
  healthy snapshot остаётся; приемлемо, задокументировать.
- `drain_all_count`: сигнал-классификация корректна (WIFSIGNALED → failed), лог печатает
  WEXITSTATUS=0 для signaled («FAILED (status=0)») — косметика диагностики.
- Abort-after-critical (REF-0110) не откатывает успешные группы/модули — стоп без общего отката,
  зафиксировано TRAP[DECISION]; консистентность хвоста обеспечена failed-учётом.
- Внесплановые изменения вне §6: zai glm-4.5-flash + ZAI_API_KEY (комментарий-санкция владельца,
  производные согласованы) и `docker-compose.macos.yml` litellm `DATABASE_URL=""` — противоречит
  инварианту 8 («PostgreSQL во всех окружениях»), self-declared debt; требуют явного решения владельца.
- AGENTS.md diff (+4−2) — легитимная синхронизация с REF-0017 (nginx:443, langfuse :3000/:3001).
- Freeze P3 соблюдён по всем пунктам: leaf-контракты аддитивны, AGE_SECRET_KEY/verbs/networks/detectors
  не тронуты, wire-DTO цел, __init__ ordering сохранён, docker_orchestrator.py пуст, lib/*.sh — тонкие.
