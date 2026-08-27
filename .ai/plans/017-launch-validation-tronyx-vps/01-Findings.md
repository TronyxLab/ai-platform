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
