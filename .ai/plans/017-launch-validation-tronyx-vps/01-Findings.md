# 01-Findings · 017-launch-validation-tronyx-vps

## PROGRESS-чеклист фаз

| Фаза | Статус |
|------|--------|
| §0 Опрос владельца | ✅ done (2026-08-27 02:03) |
| Подготовка: удаление roadmap+oldapp | ⏳ pending |
| A Локальная верификация | ⏳ pending |
| B Bootstrap одной командой + идемпотентность | ⏳ pending |
| C TLS и кеш сертификатов | ⏳ pending |
| D Три канала доставки | ⏳ pending |
| E Вариации конфигурации + node-update | ⏳ pending |
| F DR: бэкапы и restore | ⏳ pending |
| G Resilience drills (reboot/chaos/load) | ⏳ pending |
| H Release checklist + промоут | ⏳ pending |

## §0 Ответы владельца (2026-08-27, question tool, один батч)

1. **Состояние ноды:** ГОЛАЯ (пересоздана) → холодный bootstrap с нуля.
2. **Целевая нода:** tronyx-vps (контекст tronyx-lab, host 103.88.243.151).
3. **Freeze на код:** СНЯТ — чинить свободно, fix-forward без ограничений.
4. **Chaos/reboot-дриллы:** разрешены, часы допустимы.
5. **Финальный промоут:** РАЗРЕШЁН (`make context-promote CONTEXT=tronyx-lab` после зелёных B–G).
6. **test-VPS:** НЕДОСТУПНА → G5/H1 = BLOCKED с причиной.
7. **DNS/ACME (webnames):** ДОСТУПНЫ — wildcard DNS-01 выпуск разрешён.
8. **Проекты контекста:** ИЗМЕНЕНИЕ владельца — удалить roadmap и oldapp из
   контекста tronyx-lab (канонический путь). Остаётся: tronyx-site,
   dance-site, botanika (все expose=true).

## Протокол сессии

- Чинить до победного: fail → Coder-субагент фикс → ре-верификация → дальше.
- Нода мутируется ТОЛЬКО последовательно из главной сессии.
- Единственная тестовая команда: make check (+MARKER/TEST_FILE/check-diff);
  gate MODE=fast вручную не гонять; test/test-summary запрещены.
- Findings чанками ≤40 строк; восстановление после сжатия: этот файл +
  .ai/plans/017-launch-validation-tronyx-vps/logs/latest.log + test_journal latest.

---

### F-01 · 2026-08-27 02:12 · подготовка · P1
- Симптом: remove-project roadmap reports «Nginx vhost deactivated ✔», но
  overlays/nginx/roadmap.tronyx.ru.conf остался на диске с АКТИВНЫМИ server-блоками.
- Ожидалось/получено: ожидалось удаление/деактивация vhost; получен живой конфиг,
  который улетел бы на ноду при bootstrap и ломал бы TLS/сертификаты removed проекта.
- Гипотеза: remove_vhost() деактивирует в ином смысле (canon-lookup) либо неполон;
  reconcile render-vhosts честно удаляет stale GENERATED-файлы.
- Фикс: канонический re-render `make render-vhosts NODE=tronyx-vps` — Removed 4 stale,
  отрендерено ровно 3 проекта; nginx -t PASS (docker harness).
- Ре-верификация: ls overlays/nginx = только botanika/sexydancerostov/tronyx/nginx.conf.
- Статус: fixed
- Evidence: вывод make render-vhosts выше; node.yaml без roadmap/oldapp (grep=0).

### F-02 · 2026-08-27 02:20 · подготовка · P0
- Симптом: pyright-hook.sh зависает навсегда на full-repo scan: осиротевший процесс
  с 17:48 предыдущего дня сжёг ~7h CPU (basedpyright node index.js, 98% CPU);
  make check шаг pyright падает по timeout 120s (exit 124).
- Ожидалось/получено: ожидался full-scan за разумное время ( шаг pyright-full
  ограничен 300s); получено — вечное зависание даже соло (>600s, пустой stdout).
- Гипотеза причины: pyrightconfig.json#exclude покрывает только top-level пути
  (.venv/.kilo/etc); на dev не исключены .worktrees/013-resilience-drills (288M:
  копия дерева с собственным .venv и 1030 *.py), projects/asi-group (474M чужого
  контекста), logs/ (133M), load-results/ — дублирование пакетов → взрыв анализа.
- Фикс: Coder — расширить exclude glob-паттернами; замерить длительность до/после.
- Ре-верификация: pending (после Coder).
- Статус: fixing
- Evidence: ps aux до kill; core/check-suite.yaml L123-151; pyrightconfig.json.

### NOTE-N1 · 2026-08-27 02:10 · подготовка
- node-configs/node-configs — случайный самоссылочный симлинк (создан ранее сегодня,
  похоже агентским glob) → риск бесконечной рекурсии обходчиков; удалён.
- projects/asi-group внутри репо платформы — посторонняя папка контекста (gitignored);
  не трогаю (владелец), но включена в pyright-exclude (F-02 fix).
- Фикс F-02 (Coder): pyrightconfig.json#exclude += "**/.worktrees", ".worktrees",
  "projects", "logs", "load-results". Полный скан после фикса: real 9.68s,
  "0 errors" (--stats: 387 source files — только core/). --changed-режим жив
  в обоих сценариях (exit 0 чистый / exit 1 на подложенной ошибке).
- Статус: fixed. Бонус: шаг pyright-full теперь укладывается в 300s и на dev.

---

## ФАЗА A — ЛОКАЛЬНАЯ ВЕРИФИКАЦИЯ (2026-08-27 02:03–02:47)

| # | Проверка | Результат |
|---|----------|-----------|
| A1 | make check батч | ✅ 20/20 PASS после 3 итераций (rc=0; r1: 4 fail → r3: 0) |
| A2 | make agent-check | ✅ PASS: blocking=0, advisory=1 (FBT001 autofix-hint) |
| A3 | check-manifests | ✅ ALL PASS |
| A4 | Локальный стек up→status→healthcheck→down | ✅ все healthy → ALL MODULES HEALTHY → down |
| A5 | Стартовое состояние | main @8cc757b, чистое дерево; .kilo/plans + 017-план созданы |

### Закрытые red'ы A1 (Coder-фиксы, evidence в тексте):
1. F-02 pyright hang → excludes fix (полный скан 9.7s).
2. sha-pins: templates/template-ai-project ci.yml checkout/setup-node на full SHA.
3. FROM pins: template-ai-project Dockerfile node:22.23.0-alpine@sha256.
4. test_secrets_validator contamination: изоляция SECRETS_ENV_FILE.
5. test_compose_preflight contamination: та же изоляция (58 ключей дефолтного
   secrets.env на dev делали preflight PASS).
6. pyright-hook счётчик changed-files (wc -l → grep -c '').

### PATTERN TRAP[TEST] · machine-state contamination class
Тесты, читающие дефолтный deploy_paths.secrets_env_file() (/var/lib/platform/run/
secrets.env), зависят от состояния dev-машины. Найдено 2 инстанса; исправлены
изоляцией через SECRETS_ENV_FILE=tmp. Rev: новые тесты env-requiring использовать
только с явной изоляцией.

---

## ФАЗА B — ХОЛОДНЫЙ BOOTSTRAP (2026-08-27 02:47–03:20)

B1 secrets-unlock: ✅ 58 ключей расшифрованы (AGE chain ок).

### F-03 · 03:20 · Фаза B · P0
- Симптом: `make bootstrap-node NODE=tronyx-vps` φ8 exit 10 (make Error 10):
  deployed=11, failed=[status-page, backup-cron, hermes-agent], crit=0;
  state.json: 9 шагов persisted, deploy_services=failed (resumable).
- Ожидалось/получено: конечное состояние ALL GREEN одной командой; получено
  3 build-модуля не собрались при ПЕРВОМ массовом pull с docker.io
  (buildkit не ретраит pull внутри build; daemon без прокси; IP в зоне
  ограничения docker hub). Повторы позже на ноте проходят (ручной hermes
  build RC=0, статус-page standalone RC=0) → транзиентная природа потверждена.
- Диагностика СШ: repro полного мерджа args дал «failed to read dockerfile»
  ТОЛЬКО при моём --project-directory override (введённый мной сбой,
  не баг канона); реальный stderr orchestrator обрезан на 200 символах
  (stderr.strip()[:200] в docker_compose_build — телеметрическая потеря).
- Гипотеза причины: cold-cache массовые пуллы нодой за короткое окно →
  обрывы; nested-retry отсутствует на уровне pull-before-build.
- Фикс (Coder): pre-pull пинненных баз из Dockerfile'ов build-модулей
  (static SoT extraction) с retry/backoff 5/15/45s + noisy «Invalid
  severity normal» → канонизация. Телеметрия stderr фиксится отдельно.
- Ре-верификация: повторный bootstrap (идемпотентный резюм φ8) → ALL GREEN.
- Статус: fixing
- Evidence: logs/make/20260827-024756-bootstrap-node-tronyx-vps.log,
  /tmp/{sp_build*,hm_build,bc_build}.log на ноде.

### NOTE-N2 · телеметрия
docker_compose_build/pull обрезают stderr до 200 символов — при диагностике
реальных причинloses. Rev: повысить лимит/писать полный stderr в spool-лог.

---

## ФАЗА B — ПРОДОЛЖЕНИЕ (03:50–04:12)

### F-04 · 04:00 · Фаза B · P0
- Симптом: φ8 context_deployer «Complete deployed=0 skipped=0 failed=0» при 4
  resolved-проектах; все → awaiting_deploy (GENERATED-STUB guard), счётчики
  невидимы для awaiting; фаза помечена success → маскировка.
- Гипотеза→подтверждение: у контекста нет канала cold-delivery: ghcr образы
  проектов ABSENT ×4; stub-guard отдаёт awaiting без попытки реального деплоя.
- Фикс (Coder): новая ЛОКАЛЬНАЯ фаза bootstrap — core/internal/deploy/
  project_payload_delivery.py: после remote init entrypoint delivers payload
  каждого локального проекта ~/projects/<ctx>/<name> через продовый канал
  orchestrator_cli deliver (forced-command receive → ReceiveFlow → compose up
  → healthcheck → snapshot → hooks); старый stub-guard нетронут.
- Ре-верификация: резюм №3 → delivered=3 skipped=1(no_local_source oldapp)
  failed=0; Trinity: tronyx-site/dance-site/botanika DEPLOYED healthy.
- Статус: fixed
- Evidence: logs/make/20260827-04????-bootstrap...log, snapshots на ноде
  /opt/projects/*/.deploy-snapshots/.

### F-05 · 04:08 · Фаза B · P1
- Симптом: post-deploy hook nginx rc=1 «unknown shorthand flag: 'T'» —
  успешный деплой проекта помечался FAILED (fail-loud P0-3), bootstrap.rc=2.
- Root: nginx_reload_hook.sh::run_in_nginx использовал `docker exec -T` —
  флаг `-T` существует только у compose exec; ручной docker exec зелёный.
- Фикс: primary-ветка без -T (stdin не нужен), compose-fallback сохранён;
  TRAP[BUG] инлайн. Single-file SCP как оперативный канал.
- Ре-верификация: хук на ноде RC=0 reload OK; повторный полный цикл
  delivery×3 exit=0 DEPLOYED×3.
- Статус: fixed

### NOTE-N3 · параллельный писатель на dev-машине
Документированные самовольные изменения вне сессии: коммит d5b3e83 (tsconfig),
пересоздание пустого projects/asi-faq ×3, ОТКАТ локального node.yaml к версии
с oldapp в 02:36 (нанёс resume-прогону stale-конфиги). Подозреваемый —
живая worktree-сессия .worktrees/013-resilience-drills + множественные kilo
serve. Владельцу рекомендовано закрыть чужую сессию перед промоутом.
Митигация этой сессии: re-apply решения владельца (remove-project) перед
каждой нодовой операцией; гейт no-empty-dirs расширён skip 'projects'.

### NOTE-N4 · телеметрия ReceiveFlow
audit.jsonl Permission denied (ci-deploy пишет в root-owned
/var/log/platform/audit.jsonl) — записи dropped. Требует chown/tmpfiles.d
фикса ноды (закрыть в Фазе E или отдельным фикс-таском).

---

## ФАЗА C — TLS И КЕШ СЕРТИФИКАТОВ (06:10–07:40)

C1: ✅ wildcard DNS-01 при bootstrap (LE issuer YE1; tronyx.ru+sexydancerostov.ru
    бандлы в /etc/letsencrypt/live, botanika через wildcard SNI). Все 3 → HTTPS 200.
C3: ✅ make verify-domains — ALL DOMAINS PASS (3/3 HTTP 200).
C4: ✅ platform_tls_days_left/self_signed in status-metrics + status-page
    /metrics + prometheus rules TLSCertExpiryWarning/Critical/SelfSigned
    ACTIVE (17 rules loaded, серии по 3 доменам 66/69/89 дней).

### F-06 · 06:12 · Фаза C · P0
- Симптом: C2 cache-check → cache miss ×3; первичный φ7 напечатал
  «cert_orchestrator not importable — skipping», фаза = success ЛОЖНО.
- Root 1 (транзиент): guarded import упал в первом процессе (не воспроизводится
  позже из любых cwd) — маскирование скипами недопустимо.
- Root 2 (детерминизм): boto3 отсутствовал в python ноды после φ1 (частичная
  установка на холодном прогоне) → upload/check degraded.
- Фикс: (а) fail-loud контракт provisioned|converged|skipped_import|error +
  done_with_warnings → резюм доводит (Coder, 48 тестов); (б) boto3 восстановлен
  каноническим python_deps ensure (node-op); TRAP[DEBT] не записан — риск повторной
  частичной установки закрыт тем же ensure при update-фазах.
- Ре-верификация: кеш наполнен каноническим CLI ×3, check=Valid pair ×3.
- Статус: fixed

### F-07 · 06:50 · Фаза C · P1 (drill-находка)
- Симптом: node-update RC=2 «deploy_update requires registry_update» — φ11
  done_with_warnings не удовлетворял dependency-gate (warn ≠ done).
- Фикс (Coder): PHASE_STATUS_SATISFIES_DEPENDENCY={done,done_with_warnings};
  strict re-run сохранён. Drill продолжен корректно.
- Статус: fixed

### F-08b · 07:20 · Фаза C · P1
- Симптом: drill C2 (destroys live+acme dirs) → в ходе окна восстановления
  wildcard ПЕРЕВЫПУЩЕН заново через LE (serial сменился), тогда как sexy
  домен восстановлен ИЗ КЕША байт-в-байт (SERIAL_IDENTICAL — канал S3 restore
  доказан). Атрибутировать reissuance НЕ удалось: ни одного чужого ssh/лога/
  процесса нет (последняя SSH-модель — все мои IP). Подозрение остаётся на
  параллельную активность вне журнала (см. NOTE-N3).
- Митиг.: кеш пере-выровнен с текущими сертами (upload ×3, check Valid).
- Статус: fixed (канал восстановлен+доказан), аномалия задокументирована.

### NOTE-N5 · алерты прометеуса молча мертвы на холодной ноде
/opt/platform/prometheus-rules создавался docker'ом как 0700 root bind-dir →
prometheus(nobody) не читает glob → 0 rules тихо. Фикс: chmod в prometheus-
config-init step (repo edit), применён на ноде (reload через restart init-шага).
Evidence: до фиксa total_rules=0, после =17.

### Drill-C2 итоговый вердикт
Восстановление БЕЗ ACME доказано канонически на sexy (download CLI <1s,
serial identical); полный flow теперь покрыт contractом fail-loud.

---

## ФАЗА D — ТРИ КАНАЛА ДОСТАВКИ (07:45–09:00)

| # | Проверка | Результат |
|---|----------|-----------|
| D1 | deploy-context re-run | ✅ rc0: 3 skipped healthy (идемпотентно) |
| D2 | render-monitoring botanika | ✅ backward-compat ветка (нет monitoring-секции); треб. явный PROJECT_DIR |
| D3 | project-list/status ×3 | ✅ found/healthy у всех |
| D4 | deploy-project direct (dance-site) | ✅ DEPLOYED healthy 2.5s (нужен ПОЛНЫЙ PROJECT_DIR — UX-нюанс) |
| D5 | CI-канал push | ⛔ BLOCKED: GitHub Billing org TronyxLab (известный внешний блокер с 011/014); канал receive при этом доказан D4/bootstrap |
| D6 | project-sync-env ×3 | ✅ rc0 (плюс repair двух залетевших asi-group scaffold'ов) |
| D7 | provision-llm | ✅ после F-10 резолвера: 1 key через loopback ноды |
| D8 | rollback-контур | ✅ ручной rollback rc0×2 (F-11 fix), идемпотентен |

### F-13 · 08:10 · Фаза D · P1
- Симптом: пост-bootstrap audit-записи не пишутся (DEPLOYED остановились 03:47Z).
- Root: R2 chmod 0664 root:adm vs writer ci-deploy → Permission denied.
- Фикс: R2 ACL/group-channel (CI_DEPLOY_USER SoT), идемпотентен.
- Ре-верификация: converge R2 converged; rollback пишет записи вновь.
- Статус: fixed

### F-14 · 08:30 · Фаза D · P1
- Симптом: φ11 llm_provision всегда «Temporary failure in name resolution».
- Root: _DEFAULT_BASE_URL=http://litellm:4000 нерезолвим с хоста ноды (docker DNS).
- Фикс: resolve_base_url explicit→env→DNS-probe→loopback (29 тестов).
- Статус: fixed

### F-15 · 08:50 · Фаза D · P1
- Симптом: ручной rollback FAILED при здоровом контейнере (bare retag + pull
  локального тега из registry + env-file отсутствовал).
- Фикс: compose-resolved retag + единый env-file args SoT + --pull never.
- Ре-верификация: rc0×2 на ноде; второй прогон 0.5s.
- Статус: fixed

---

## ФАЗА E — ВАРИАЦИИ КОНФИГУРАЦИИ + NODE-UPDATE (09:10–11:20)

| # | Проверка | Результат |
|---|----------|-----------|
| E1 | healthcheck ноды | ✅ ALL MODULES HEALTHY (после F-16 NODE_NAME auto-detect); без него ловил log-collector вне node.yaml |
| E2 | toggle status-page off/on | ✅ полный цикл: enabled:false → node-update φ12 остановил контейнер; обратно → up healthy |
| E3 | overlays учёт в render | ✅ evidence: overlay-hash reload T9.14 в φ11, nginx_reload_hook Ok после F-05 |
| E4 | make node-update ×3 | ✅ rc0 все; ключи только через env-file/prelude (в логах absent), config-sync живой |
| E5 | converge сразу после node-update | ✅ прогнан каждый раз; R2-first-run mutated — non-fatal честно |
| E6 | сетевая правда | ✅ канонические сети на месте (+NOTE-N6 про staging/test остатки прежних эпох) |

### F-16 · 09:20 · Фаза E · P1
- Симптом: make healthcheck НА НОДЕ перебирал все модули infra (log-collector FAIL)
  без фильтра node.yaml.
- Фикс: entrypoints/healthcheck.sh — автоэкспорт NODE_NAME через канон node_detect
  (только если не задан; dev не тронут).
- Статус: fixed

### F-17 · 10:10 · Фаза E · P1
- Симптом: R9 слеп (лейбл project=<module> ≠ канона U-49 project=platform);
  disabled-flow отсутствовал.
- Фикс: label primary + compose-canonical name fallback (shared интроспекция,
  кеш); disabled → compose down сервисов БЕЗ -v через билдеры; cooldown не
  маскирует stop (14+13 тестов).
- Статус: fixed

### F-18 · 10:40 · Фаза E · P1
- Симптом: внутренний bootstrap/converge.sh (исполняемый НА НОДЕ через ssh-канал)
  запускался с чистым env → R7/R8/R9 compose-introspection падали на
  ${NGINX_OVERLAY_DIR:?} (класс F-015).
- Фикс: self-env блок во внутреннем converge.sh (secrets.env source + NODE_NAME
  detect + overlay export; fake-path запрещён) по образцу F-023/healthcheck.
- Ре-верификация: R9 видит все контейнеры ноды; introspection успешна.
- Статус: fixed

### NOTE-N6 · сетевые артефакты прошлых эпох
На ноде остались staging-*/test-* сети от предыдущих инсталляций (миграция
/opt сохранялась). Не мешают; чистка — операторское решение после промоута.

---

## ФАЗА F — DR: БЭКАПЫ И RESTORE (11:00–12:30)

| # | Проверка | Результат |
|---|----------|-----------|
| F1 | Полный цикл pg_dumpall→gzip→age→S3 | ✅ UPLOAD VERIFIED sha256 b09e3c1a…; sentinel lifecycle корректен |
| F2 | Restore round-trip каноническим make restore DUMP_FILE=.age из S3 | ✅ «Restore complete» после F-19 серии (см. ниже); pre-restore snapshot работал каждый прогон |
| F3 | age-key-backup off-node | ✅ s3://tronyx-vps-backup/age-key-backup/…enc private ACL, sha256 match |
| F4 | Nightly cron + RPO | ✅ /etc/cron.d внутри контейнера: 01:30 retry · 03:00 dump · 03:30 app-data · 04:00 cleanup · 05:00 retention · hourly WAL; flock-guards |

### F-19 (серия P1) · restore round-trip вскрыла 5 дефектов DR-канала
1. PLATFORM_ROOT на один уровень выше на ноде (root-compose путь /opt/platform/core/docker-compose.yml отсутствовал).
2. stop/up рутовым compose требуют секреты+NGINX_OVERLAY_DIR даже под профилем postgres (интерполяция всего графа).
3. .age-артефакт скормлен psql сырьём → добавлен age_decrypt_stdout.py (plaintext В STDOUT, identity tmp+wipe).
4. psql без -d postgres: DROP DATABASE «currently open» → admin-db подключение.
5. pg_dumpall --clean содержит DROP ROLE <session-user> («cannot drop») → self-role фильтр потока.
Финал: весь пайплайн вынесен в backup-cron/scripts/restore_psql.sh (manual round-trip GREEN ×2,
make-путь «Restore complete»); Makefile использует его одной строкой (устранён класс
make-context env расхождений). Evidence: логи прогонов restore в logs/make/.
NOTE: SEC-0018 остаётся верным — pre_restore_* в отдельном каталоге вне retry-scan.

### NOTE-N7 · S3-имена легаси
Секреты ноды несут S3_ENDPOINT (канон S3_ENDPOINT_URL из platform-infra.yaml);
модульный код принимает оба. Rev: сузить матрицу ноды до канона при след. ротации.
