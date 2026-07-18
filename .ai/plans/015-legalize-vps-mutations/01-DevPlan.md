<!-- GREP_SUMMARY: DevPlan, legalize-vps-mutations, converge, reconciler, deploy-project-B1, executable-bit-M1, audit-log-M2, projects-scaffold-M3, proxy-net-M4, etc-hosts-M5, repo-secrets-M6, known-hosts-M7, nginx-overlay-B5, context-promote-B4, ghcr-403-B2, org-secrets-B3, render-vhosts-S1, verify-vhosts-R6, generative-overlay -->
<!-- STRUCTURE: ┌$ARTIFACT_CONTRACT┐ → ◇ реестр+root-cause → ◇ GAPS-резолюция → ◇ TRAP[DECISION] отвергнутые гипотезы → ◇ архитектура converge → ⊕ волны 1-4 (задачи+edge cases) → ◇ Code Graph XML → ◇ Data Flow → ◇ Acceptance Criteria → ⎋ File Manifest + OperatorChecklist + риски -->

# $START_DEVPLAN

## $ARTIFACT_CONTRACT
- **PURPOSE:** Легализовать 7 ручных мутаций tronyx-vps (M1–M7) и устранить 5 нерешённых багов (B1–B5) так, чтобы bare-metal ре-bootstrap + first-deploy проходили без единого ручного SSH-вмешательства.
- **DESCRIPTION:** Синтез D⊕B (вариант S1): идемпотентный реконсилер `core/internal/bootstrap/converge.sh` (юниты R1–R6) читает `node.yaml` как desired state и сводит права/каталоги/сети/audit-log + верифицирует vhost-конфиги (R6, read-only); генерация vhost — у оператора (`make render-vhosts` из node.yaml через add-vhost.sh) с доставкой существующим rsync-каналом. Вызовы converge: bootstrap (init), node-update (update), standalone (`make converge`). Плюс точечные фиксы: deploy-финализация (B1), CI-миграция tronyx-site, context-promote SSH-push (B4), диагностика B2/B3, раскатка repo-secrets через gh CLI (M6). 4 волны: Reconciler → Генеративный overlay/сеть → Deploy pipeline/CI → QA.
- **RATIONALE:** Коллапс суперпозиции FULL (4 гипотезы) выполнен пользователем: выбрана D. Суперпозиция раскрыта повторно (2026-07-18, после уточнения масштаба: +1-2 контекста, +5-10 проектов за ~6 мес) — синтез D⊕B, коллапс точки интеграции также выполнен пользователем: S1. A/C отвергнуты, B поглощена синтезом, S2/S3 отвергнуты — см. TRAP[DECISION] §4. Root cause B1 и M1 подтверждены объективно до планирования (git-индекс 100644, поздняя инициализация DEPLOY_STATUS). GAPS закрыты ответами пользователя (§3).
- **ACCEPTANCE_CRITERIA:** (1) `make gate MODE=fast` зелёный; (2) двойной запуск `make converge NODE=tronyx-vps` — второй прогон no-op; (3) негативные gate-тесты на M1–M5 проходят; (4) `docker compose up --force-recreate` проектов не роняет nginx; (5) deploy-result.json=success и exit 0 при отсутствующем notify-hook; (6) CI зелёный у платформы и обоих проектов (для B2/B3 допустим исход BLOCKED + OperatorChecklist).
- **IMPLEMENTS:** Бриф «легализация ручных мутаций tronyx-vps + автоматизация first-deploy» (A1–A12); реестр проблем сессии 014 (B1–B5, M1–M7); .ai/plans/014-rebootstrap-tronyx-vps/01-StatusReport.md.
- **IMPACTS:** ai-platform (core/internal/bootstrap/, core/internal/deploy/, core/entrypoints/, Makefile, entrypoint-manifest.yaml, root AGENTS.md, tests/gates/, .github/workflows/), node-configs (overlays/nginx/), tronyx-site (.github/workflows/), dance-site (docker-compose.yml).
- **REQUIRES:** Ответы пользователя на 9 вопросов (получены 2026-07-18); доступ QA к tronyx-vps через канонические таргеты; для B2/B3 — права org-админа TronyxLab (вне кода, OperatorChecklist).
$END_ARTIFACT_CONTRACT

---

## $START_DOCUMENT_PLAN
### Document Plan
**SECTION_GOALS:**
- GOAL [Зафиксировать подтверждённые root cause до реализации] => GOAL_ROOT_CAUSE
- GOAL [Зафиксировать решения по GAPS и отвергнутые гипотезы] => GOAL_DECISIONS
- GOAL [Спроектировать converge.sh до уровня функций и контрактов] => GOAL_CONVERGE
- GOAL [Разложить работу на 4 волны с файлами, функциями и edge cases] => GOAL_WAVES
- GOAL [Дать QA проверяемые критерии и негативные тесты M1–M5] => GOAL_QA
**SECTION_USE_CASES:**
- USE_CASE [bare-metal reinstall → bootstrap → first deploy без SSH-рук] => SCENARIO_REBOOTSTRAP
- USE_CASE [force-recreate контейнеров проектов → nginx не падает] => SCENARIO_RECREATE
- USE_CASE [деплой при отсутствующем notify-hook → success/exit 0] => SCENARIO_B1_NEGATIVE
- USE_CASE [make converge NODE=x дважды → второй запуск no-op] => SCENARIO_IDEMPOTENT
$END_DOCUMENT_PLAN

---

## §1. Реестр проблем и подтверждённые root cause

Диагностика выполнена ДО планирования (Read before Act). Evidence:

| ID | Симптом | Подтверждённый root cause | Evidence |
|----|---------|---------------------------|----------|
| B1 | «Deploy SUCCESS» → «Deploy result: failed», exit 1 | `DEPLOY_STATUS="success"` присваивается только на deploy-project.sh:1010 — ПОСЛЕ `tag_current`→`prune_old_images`→`_trigger_deploy_hooks`→`audit_log`→`notify_hook` (строки 1000–1007). Любой сбой этих шагов под `set -e` убивает скрипт до присвоения → EXIT-trap `_finalize_deploy` (строка 84) пишет `failed`. Вероятный триггер — `audit_log` → permission denied на audit.log (= M2) либо неисполнимый notify-hook.sh (= M1: mode 100644 в git) | Чтение deploy-project.sh:998–1011; git ls-files -s |
| M1 | rsync доставил deploy-project.sh с mode 644 | **33 tracked `.sh` имеют mode 100644 в git-индексе** (deploy-project.sh, notify-hook.sh, verify-domains.sh, весь core/internal/bootstrap/, cron-скрипты backup-cron, install.sh модулей). rsync `-a` честно сохраняет права источника — источник неправ. `core/lib/*.sh` — sourced-only, для них 644 допустимо | `git ls-files -s -- '*.sh' \| awk '$1=="100644"'` |
| M2 | audit_logging.sh падает под ci-deploy | `_ensure_log_dir()` (core/lib/audit_logging.sh:17–27) создаёт dir 0750 root:adm, файл создаётся `>>` с umask-правами root (644) — ci-deploy не может писать. ci-deploy УЖЕ в группе adm (setup-node.sh:99) — достаточно mode 664 файла и g+w учёта | Чтение audit_logging.sh, setup-node.sh |
| M3 | FATAL: project directory not found | `/opt/projects` создаётся step_6b (node-lifecycle.sh:352–362), но per-project каталоги — только в `handle_deliver`; deploy-verb (parse_ssh_command:426) требует каталог с compose ДО деплоя. Итерации по `node.yaml#projects` с mkdir нет нигде | Разведка node-lifecycle.sh, deploy-project.sh |
| M4/M5 | nginx: host not found in upstream; /etc/hosts с захардкоженными IP | Overlay nginx.conf: `proxy_pass http://tronyx-site:80` без resolver; TRAP[DECISION] от 2026-06-29 «System nginx not in Docker» УСТАРЕЛ — nginx давно Docker-модуль на proxy-net (docker-compose.base.yml:80–86), Docker DNS 127.0.0.11 доступен | Разведка overlay + core/modules/nginx |
| M6 | Repo-secrets руками | `gh secret set` не используется нигде в репо (0 совпадений); scaffold не раскатывает секреты | grep по репо |
| M7 | Host key verification failed | `prepare_ssh_opts()` (scp-deliver.sh:65–74) делает `ssh-keygen -R` при КАЖДОЙ доставке + accept-new — MITM-защита фактически отключена; бриф требует init-only | Чтение scp-deliver.sh |
| B2 | dance-site ghcr 403 | Не диагностирован. Оба проекта используют идентичные `docker/login-action@v3` + `packages: write` + `GITHUB_TOKEN`. Разница — вне кода (package↔repo linkage / org package settings) | Разведка workflows |
| B3 | Org-secrets пустые в CI | Не подтверждён. Гипотеза: GitHub Free для org не отдаёт org-secrets приватным репо | Требует диагностики |
| B4 | context-promote неработоспособен | GIT_MIRROR_TOKEN отсутствует в tronyx-vps.enc.yaml (56 ключей проверены) И context-promote.sh выполняется локально у оператора — node-секреты ему всё равно недоступны | Разведка enc-файла + context-promote.sh:35–70 |
| B5 | deprecated `listen ... http2`; мёртвый conf.d/tronyx.ru.conf | nginx.conf overlay: 4 вхождения `listen 443 ssl http2` (строки 44,45,94,95); conf.d/tronyx.ru.conf не монтируется (монтируется только `overlays/nginx/` → `/etc/nginx/conf.d/overlay/`, подкаталог conf.d мёртв) | Разведка overlay |

**Уточнение брифа (A5):** все три `templates/template-*/docker-compose.yml` УЖЕ объявляют `proxy-net (external)`. Реальная дыра — adopted-проекты (dance-site): adopt-project.sh осознанно не трогает compose (строка 164 exclude-список).

---

## §2. Суперпозиция — итог коллапса

FULL-режим, 4 гипотезы, ADVERSARIAL по лидерам A/B — см. сессионный лог. Коллапс выполнен пользователем: **выбрана D «Reconciler platform-converge»**.

**Повторное раскрытие (2026-07-18):** после уточнения масштаба (+1-2 контекста, +5-10 проектов за ~6 мес) пользователь запросил синтез «лучшее от B и D». Суперпозиция точки интеграции: S1 «рендер у оператора + R6-verify на ноде» (8.5/10) / S2 «полный рендер на ноде» (6/10) / S3 «только апгрейд шаблона, без R6» (5/10); ADVERSARIAL S1 vs S2. Коллапс пользователем: **S1**. Отвергнутые — §4 (TRAP[DECISION]).

## §3. GAPS — резолюция (ответы пользователя, 2026-07-18)

| GAP | Решение | Влияние на план |
|-----|---------|-----------------|
| G1 (M2) | audit.log **664 root:adm** (группа platform НЕ создаётся) | T1.3 |
| G2 (M3) | Каталог **+ stub-файлы** (ai-platform.yaml, пустой .env.platform) if-missing, как в брифе | T1.4; stub помечается marker-комментарием, deliver перезаписывает |
| G3 (M1) | Решено evidence: чинить в git-индексе + defense-in-depth `--chmod` в rsync + R-PERMS в converge | T1.2, T1.8 |
| G4 (M7) | `ssh-keygen -R` **только в init**; update/deploy доверяют сохранённому ключу (honest TOFU) | T1.9 |
| G5 (M5) | Удаление /etc/hosts-записей — **ручной runbook**; converge только ДЕТЕКТИРУЕТ дрейф (WARN) | T1.6, OperatorChecklist §10 |
| G6 (A9) | tronyx-site — **полная миграция на reusable** deploy-project.yml (как dance-site); platform-deploy.yml удаляется | T3.3 |
| G8 (B4) | context-promote: **SSH push (git@github.com:<org>) с HTTPS+token fallback**; токен перестаёт быть обязательным | T3.4 |
| G7+G12 (B3/M6) | Значения repo-secrets — из **SOPS enc-файла**, раскатка `gh secret set`; артефакт «OperatorChecklist» допустим в DoD | T3.6, §10 |
| G11 (QA) | QA-доступ к tronyx-vps **разрешён через канонические таргеты** (verify/healthcheck/node-update/converge, module restart-hard); ad-hoc SSH запрещён | Волна 4 |
| G9 (B2) | Открыт частично: диагностика может закончиться BLOCKED — допустимо, фиксируется в OperatorChecklist | T3.5 |
| G10 (A5) | Решено evidence: шаблоны уже ок; gate-тест распространяется на adopted-проекты + валидация в adopt-project.sh | T2.4, T2.5 |
| G13 | Порядок раскатки меж-репо: (1) ai-platform → (2) node-configs → (3) node-update → (4) tronyx-site/dance-site CI | §7 Data Flow |
| S1 (синтез D⊕B) | Генерация vhost у оператора (render-vhosts), на ноде — R6 read-only verify; доставка rsync-каналом без изменений | T1.6b, T2.1, T2.2, §5.4 |

---

## §4. TRAP[DECISION] — отвергнутые гипотезы

```
⚠️ TRAP[DECISION] · 2026-07-18 · HI · Гипотеза D (Reconciler platform-converge) выбрана пользователем при коллапсе суперпозиции
· Контекст масштаба (уточнение владельца, 2026-07-18): планируется +1-2 контекста и +5-10 проектов в течение ~6 месяцев.
· Rejected НАВСЕГДА: A «Bootstrap-конвергенция» (score 8/10) — каждая мутация как отдельный checkpoint-шаг node-lifecycle.
·   Причина отказа: A полностью поглощена D — юниты R1-R5 и есть шаги конвергенции, централизованные
·   в одном компоненте. При заявленном масштабе converge гарантированно растёт — сворачивание юнитов
·   обратно в node-lifecycle недостижимо.
·   Rev: только если сам компонент converge будет удалён из архитектуры (тогда шаги возвращаются в lifecycle).
· MERGED (2026-07-18): B «генеративный nginx-overlay» (score 7.5/10) — влита в настоящий план как синтез D⊕B.
·   Основание: D не закрывает класс дрейфа M5/B5 (рукописные vhost-конфиги); при заявленном масштабе
·   (+1-2 контекста, +5-10 проектов за ~6 мес) ручная правка overlay повторила бы болезнь на каждом vhost.
·   Реализация: юнит R6 verify_vhosts() (T1.6b) + генерация у оператора make render-vhosts (T2.1) +
·   миграция существующих vhost (T2.2). Контраргумент blast radius снят выбором S1 (см. TRAP ниже).
· Rejected: C «Deliver-центричное provisioning» (score 5/10) — first-deploy сам создаёт всё через forced-command.
·   Причина отказа: ci-deploy не root — M1/M2/M7 недостижимы из этого канала; расширение forced-command
·   = необратимый рост security-поверхности.
·   Rev: если ноды станут cattle с полностью CI-определяемыми проектами и появится не-root механизм prov.
```

```
⚠️ TRAP[DECISION] · 2026-07-18 · HI · Синтез D⊕B: точка генерации vhost — S1 (рендер у оператора + R6-verify на ноде)
· Выбор пользователя при повторном коллапсе суперпозиции (после уточнения масштаба).
· Rejected: S2 «полный рендер на ноде» (score 6/10) — чистый desired-state, оператор вне цикла.
·   Причина: конфликт с rsync --delete (следующая доставка стирает сгенерированное на ноде → нужен
·   редизайн канала, +1 волна), потеря git-ревью ingress-конфигов, template engine на ноде.
·   Rev: если при 3 нодах × 10 проектах ручной render+commit станет узким местом — мигрировать S1→S2:
·   рендер переносится на ноду, R6 расширяется с verify до render; шаблон и юнит переиспользуются без выброса.
· Rejected: S3 «только апгрейд шаблона, без R6» (score 5/10) — B-lite без D: дрейф vhost между
·   scaffold-запусками никем не ловится.
·   Rev: нет — уступает S1 по всем осям при том же объёме шаблонной работы.
· Ключевое свойство безопасности S1: git-ревью diff + локальный nginx -t harness ДО прода + R6 nginx -t
·   на ноде ДО reload; работающий nginx не применяет битый конфиг (reload-валидация nginx).
```

---

## §5. Архитектура: converge (гипотеза D)

### 5.1 Компонент

**`core/internal/bootstrap/converge.sh`** — идемпотентный реконсилер desired state. Не новый «скрипт вне словаря»: регистрируется как канонический глагол `converge` в entrypoint-manifest.yaml + глоссарии root AGENTS.md + Makefile.

```
Makefile: make converge NODE=<name> [DRY_RUN=1]
  └─ core/entrypoints/converge.sh          (thin wrapper ≤150 LOC, SSH-proxy как node-update)
       └─ core/internal/bootstrap/converge.sh --node <name> [--dry-run] [--report-only]
            ├─ R1 reconcile_perms()        ← M1 (на ноде)
            ├─ R2 reconcile_audit_log()    ← M2
            ├─ R3 reconcile_projects()     ← M3 (node.yaml#projects)
            ├─ R4 reconcile_networks()     ← M4 (proxy-net exists; compose проектов объявляет её — WARN)
            ├─ R5 detect_hosts_drift()     ← M5 (ТОЛЬКО детекция, WARN — G5)
            └─ R6 verify_vhosts()          ← M5/B5 (read-only: vhost↔node.yaml + GENERATED-целостность + nginx -t)
```

Генерация vhost (S1, operator-side — на ноде НЕ рендерится):

```
make render-vhosts NODE=<n>
  └─ core/entrypoints/scaffold.sh render-vhosts
       └─ core/internal/scaffold/add-vhost.sh --render-all --node <n>
            ├─ читает node.yaml#projects (только записи с domain)
            ├─ рендер → node-configs/<node>/overlays/nginx/<domain>.conf (GENERATED header, §5.4)
            └─ локальная валидация: docker run nginx:<ver> nginx -t (dev-certs подмена путей)
  доставка: git commit в node-configs (ревью diff) → существующий rsync-канал bootstrap/node-update
```

Вызовы:
1. `node-lifecycle.sh --mode init` — новый checkpoint-шаг `step_17_converge` (после deploy-modules, до финального audit).
2. `node-lifecycle.sh --mode update` — новый update-шаг (каждый node-update конвергирует).
3. Standalone `make converge NODE=<name>` — точечная реконсиляция без полного update.

### 5.2 Контракт реконсилер-юнита

Каждый R-юнит обязан: (а) сначала ПРОВЕРИТЬ состояние, мутировать только при расхождении; (б) логировать `[IMP:9]` при мутации, `[IMP:7] SKIP: converged` при совпадении; (в) `--dry-run` — только план; (г) `--report-only` — exit 0 + JSON-отчёт drift'ов (для QA); (д) быть независимым — сбой юнита не прерывает остальные (аккумулируется в exit code: 0 = конвергировано, 1 = были мутации (штатно), 2 = юнит(ы) упали); (е) не трогать данные проектов (volumes, БД, images — инвариант O7).

### 5.3 Политика executable-bit (source of truth для M1)

- Все tracked `*.sh` **вне** `core/lib/` → mode 100755 в git-индексе (прямо вызываемые: entrypoints, internal, module install/healthcheck/cron-скрипты).
- `core/lib/*.sh` — sourced-only, 100644 допустим (маркер «не вызывать напрямую»).
- Defense-in-depth: rsync `--chmod=Du+rwx` не вводим (ломает семантику -a); вместо этого R1 на ноде + gate-тест в CI на git-индекс. Двухслойная защита: git-индекс (превентивно) + converge R1 (лечебно).

### 5.4 Контракт GENERATED-vhost (S1)

- Каждый сгенерированный conf начинается маркером `# GENERATED by add-vhost.sh — DO NOT EDIT` + источник (`node.yaml#projects[<name>]`) + content-hash тела (переиспользовать `core/internal/bootstrap/content-hash.sh`).
- Тело шаблона: `listen 443 ssl;` + `http2 on;` (не deprecated `listen ... http2`); `resolver 127.0.0.11 valid=30s ipv6=off;`; `set $upstream_<name> http://<name>:80;` + `proxy_pass $upstream_<name>;` — lazy DNS: nginx стартует и переживает force-recreate без живого upstream.
- Детерминизм: повторный рендер при неизменном node.yaml → байт-идентичный вывод (timestamp только в комментарии, исключён из content-hash) — пустой git-diff как признак конвергенции.
- `R6 verify_vhosts()` на ноде (read-only): (1) для каждого project с domain существует `<domain>.conf`; (2) GENERATED-маркер цел и content-hash совпадает (ручная правка = drift); (3) vhost-сироты без проекта → WARN; (4) `docker exec nginx nginx -t` проходит — иначе юнит FAIL и reload блокируется.
- Легальный путь изменения vhost — ТОЛЬКО правка шаблона или node.yaml + re-render; прямое редактирование сгенерированного файла = дрейф, детектируемый R6.

---

## §6. Волны реализации

### Волна 1 — Reconciler + source-side фиксы (Кодер 1, ai-platform)

**T1.1 — Каркас converge**
- Создать `core/internal/bootstrap/converge.sh`: MODULE_CONTRACT, GREP_SUMMARY, STRUCTURE, argparse (`--node`, `--dry-run`, `--report-only`), main-диспетчер R1–R5, LDD-логи [IMP:7–10].
- Создать `core/entrypoints/converge.sh` (thin wrapper: локальный exec или SSH-proxy через `remote-cmd.sh`, по образцу node-update.sh).
- Makefile: таргет `converge`; entrypoint-manifest.yaml: секция lifecycle, op `converge`; root AGENTS.md: строка глоссария; core/AGENTS.md: строка канонических операций.
- Edge cases: node.yaml отсутствует/невалидный YAML → FATAL exit 2 с [IMP:10] (не молчаливый skip); `--node` не задан локально → auto-detect как в bootstrap.sh:67; конкурентный запуск (converge поверх идущего node-update) → flock-лок `/var/lock/platform-converge.lock`, второй процесс — немедленный exit 3 «already running»; повторный запуск → все юниты SKIP (сценарий SCENARIO_IDEMPOTENT).

**T1.2 — R1 reconcile_perms (M1, лечебный слой)**
- Функция `reconcile_perms()`: `find /opt/platform/core -name '*.sh' -not -path '*/lib/*' ! -perm -u+x -exec chmod ug+x {} +`; счётчик исправленных; список в [IMP:9].
- Edge cases: 0 файлов → SKIP; symlink → не следовать (`-type f`); файл исчез между find и chmod (гонка с rsync core-deploy) → `chmod || true` per-file с WARN; огромное дерево — один exec-батч, не по-файлово.

**T1.3 — R2 reconcile_audit_log (M2, G1)**
- Функция `reconcile_audit_log()`: dir `/var/log/platform` 0750 root:adm; файл audit.log — создать при отсутствии, `chmod 0664`, `chown root:adm`. **НЕ 666.**
- Обновить `core/lib/audit_logging.sh::_ensure_log_dir()` — после append гарантировать 0664 (umask-независимо): `[[ -f $PLATFORM_AUDIT_LOG ]] || { touch ...; chmod 0664 ...; }` под root; под ci-deploy — только запись, без chown (нет прав — не фатально, `|| true` с [IMP:6]).
- Edge cases: audit.log — symlink (атака) → отказ + [IMP:10] FATAL юнита; файл >N ГБ → не трогать содержимое (ротация — logrotate, уже есть install_logrotate); ci-deploy вне группы adm (дрейф юзера) → R2 детектирует через `id -nG ci-deploy` и чинит `usermod -aG adm`; конкурентная запись audit_log из двух деплоев → append-only O_APPEND, безопасно.

**T1.4 — R3 reconcile_projects (M3, G2)**
- Функция `reconcile_projects()`: читает `node.yaml#projects` (переиспользовать паттерн inline-python из node-lifecycle.sh:529); для каждого `name`: `mkdir -p /opt/projects/<name>`, `chown ci-deploy:ci-deploy`, stub `ai-platform.yaml` (маркер `# GENERATED-STUB by converge — overwritten by CI deliver` + `project:`/`service:` = name) и пустой `.env.platform` (0640 ci-deploy) — **только if-missing**.
- Edge cases: `projects: []` или секция отсутствует → SKIP (не ошибка); имя проекта с `/` или `..` → отвергнуть юнитом (переиспользовать семантику `_validate_project_name`), [IMP:10], юнит FAIL; существующий НЕ-stub ai-platform.yaml → НЕ трогать (идемпотентность + миграция существующих данных); существующий stub → не перезаписывать (no-op); каталог существует с чужим владельцем → chown только каталога, содержимое не рекурсивно (не сломать доставленный payload); частичный сбой (создан каталог, упал chown) → повторный запуск дочинивает — операции атомарны поэлементно, отката не требуется.

**T1.5 — R4 reconcile_networks (M4)**
- Функция `reconcile_networks()`: `docker network inspect proxy-net || docker network create` (переиспользовать `ensure_docker_network()` из deploy-modules.sh:75 — вынести в `core/lib/docker.sh`, чтобы не дублировать); для каждого запущенного project-контейнера из node.yaml#projects: если не подключён к proxy-net → WARN [IMP:9] (подключение — забота compose проекта, см. T2.4; авто-`network connect` создал бы дрейф compose-vs-runtime).
- Edge cases: docker daemon недоступен → юнит FAIL exit-code 2, остальные юниты продолжают; proxy-net существует с другим driver → WARN, не пересоздавать (пересоздание = обрыв всех подключённых); конкурентный `docker network create` (гонка с deploy-modules) → inspect-after-fail паттерн.

**T1.6 — R5 detect_hosts_drift (M5, G5 — только детекция)**
- Функция `detect_hosts_drift()`: grep /etc/hosts на имена проектов из node.yaml → при находке WARN [IMP:9] «stale /etc/hosts entry, см. runbook» + вывод в `--report-only` JSON. **Мутации нет** (решение G5).
- Edge cases: /etc/hosts нечитаем → WARN; имя проекта — подстрока легитимной записи (например `localhost` vs проект `host`) → матчить по границам слова `\b<name>\b`.

**T1.6b — R6 verify_vhosts (S1, read-only)**
- Функция `verify_vhosts()` по контракту §5.4; переиспользует content-hash.sh.
- Edge cases: nginx-контейнер не запущен → пропустить `nginx -t` с WARN (синтаксис уже проверен у оператора), остальные проверки выполнить; overlay-каталог пуст при `projects` с доменами → FAIL; проект без `domain` (backend-only) → SKIP этого проекта; legacy-conf без GENERATED-маркера в переходный период (до завершения T2.2) → WARN, после миграции → FAIL (переключение строгости — маркер завершения миграции в node-configs, напр. отсутствие legacy-файлов); конфиг-сирота `*.conf.bak`/не-conf файлы в overlay → игнор (nginx включает только *.conf).

**T1.7 — Врезка в lifecycle**
- `node-lifecycle.sh`: init — `checkpoint_step "converge" step_17_converge` (соблюсти TRAP[BUSINESS] node-lifecycle.sh:454 — порядок объявления = порядок main); update — вызов converge до healthchecks.
- Edge cases: DRY_RUN=1 bootstrap → converge в dry-run; сбой converge в init → bootstrap FAIL (init обязан сконвергировать); сбой в update → WARN + продолжение (update толерантен), но exit code node-update отражает degraded.

**T1.8 — Санация git-индекса (M1, превентивный слой)**
- `git update-index --chmod=+x` для всех 33 файлов ИЗ СПИСКА КРОМЕ `core/lib/*.sh` (11 lib-файлов остаются 644 — sourced-only политика §5.3). Итого ~22 файла.
- Новый gate-тест `tests/gates/test_gate_executable_bit.py`: читает `git ls-files -s -- '*.sh'`; RED если файл вне `core/lib/` имеет 100644 или файл в `core/lib/` имеет shebang и вызывается напрямую (cross-check с dead-code gate данными); негативный тест `_negative`: фикстура с симулированным 100644-списком → gate обязан упасть (R5 test honesty).
- Edge cases: новый .sh добавлен без +x в будущем → gate ловит на CI (в этом смысл); Windows-checkout контрибьютора — mode в индексе, не в FS, стабильно.

**T1.9 — known_hosts init-only (M7, G4)**
- `scp-deliver.sh::prepare_ssh_opts()` — новый параметр `mode`; `ssh-keygen -R` выполняется ТОЛЬКО при `mode=init`; вызовы из bootstrap.sh передают init, из node-update/core-deploy — update. `StrictHostKeyChecking=accept-new` сохраняется везде.
- Заменить `StrictHostKeyChecking=no` на `accept-new` в project-list.sh:295 и remove-project.sh:330,355 (унификация политики).
- Обновить TRAP-блок в scp-deliver.sh (легализация решения G4).
- Edge cases: переустановленная нода при mode=update → SSH упадёт с host key mismatch — это ЖЕЛАЕМОЕ поведение (сигнал оператору выполнить bootstrap init); отсутствующий known_hosts файл → accept-new создаёт; CI-runner (эфемерный) → known_hosts пуст, accept-new, не регрессия.

### Волна 2 — Генеративный overlay и сеть (Кодер 2, ai-platform + node-configs + dance-site)

**T2.1 — Шаблон vhost + render-vhosts (M4/M5/B5, S1)**
- Обновить генерирующий шаблон в `core/internal/scaffold/add-vhost.sh` по контракту §5.4: `http2 on`, resolver 127.0.0.11, variable proxy_pass, GENERATED-маркер + content-hash; сохранить из текущего шаблона security-headers, таймауты, acme-challenge location, буферизацию.
- Новый режим `--render-all --node <n>`: итерация по `node.yaml#projects` с domain → рендер всех vhost разом. Регистрация канонического таргета `make render-vhosts NODE=<n>` → scaffold.sh: Makefile + entrypoint-manifest.yaml (scaffold-секция) + глоссарий root AGENTS.md + core/AGENTS.md (атомарно, иначе manifest-integrity gate красный).
- Локальный валидационный harness: `docker run --rm nginx:<версия из core/modules/nginx>` + `nginx -t` с подмонтированными rendered-конфигами; SSL-пути подменяются на dev-certs только в harness (nginx -t требует существования cert-файлов) — боевые пути в артефактах не меняются.
- Edge cases: `projects` пуст / без доменов → рендер 0 файлов, exit 0 + WARN; два проекта с одним domain → FATAL до записи первого файла (переиспользовать FQDN-uniqueness из validate.sh); повторный рендер без изменений node.yaml → байт-идентичный вывод, пустой git-diff (идемпотентность); частичный сбой рендера → всё-или-ничего: рендер во временный каталог, атомарный mv после nginx -t всех файлов; конкурентный render-vhosts двух операторов → git-конфликт в node-configs как естественный лок; предельно большой вход (50 проектов) → один прогон, один nginx -t; upstream недоступен в рантайме → 502 от nginx (не crash-loop), новые IP после force-recreate подхватываются ≤30s (resolver TTL).

**T2.2 — Миграция существующих vhost на генерацию (M5/B5)**
- Перегенерить `tronyx.ru` (+www-redirect) и `sexydancerostov.ru` через render-vhosts; diff против рукописных `overlays/nginx/nginx.conf` и `sexydancerostov.ru.conf` — перенести в шаблон недостающее (SSL-пути letsencrypt, health-endpoint dance-site); заменить рукописные файлы сгенерированными; удалить мёртвый каталог `overlays/nginx/conf.d/` целиком.
- Устаревший TRAP[DECISION] (nginx.conf:78–83 «Static IP proxy_pass — System nginx not in Docker») → ARCHIVED (Rev исполнен: nginx давно Docker-модуль на proxy-net), ссылка на TRAP S1 в §4.
- Edge cases (миграция данных): у tronyx-site в node.yaml `domain: www.tronyx.ru`, а vhost обслуживает пару apex+www → шаблон обязан канонизировать domain→(apex, www-redirect) — зафиксировать правило нормализации в add-vhost.sh (иначе рендер по node.yaml сломает redirect-блок); rollback при инциденте: git revert в node-configs + `make node-update` (прежние конфиги в git-истории); переходный период: R6 в режиме WARN до удаления последнего legacy-файла (T1.6b); отказ внешней зависимости (docker недоступен локально для harness) → render FAIL до записи, не «рендер без валидации».

**T2.3 — dance-site compose: proxy-net (M4/G10)**
- `dance-site/docker-compose.yml`: добавить `networks: [dance-site-net, proxy-net]` сервису + `proxy-net: {external: true, name: proxy-net}` — по образцу template-frontend.
- Проверить tronyx-site compose на тот же контракт (доставляется deliver-payload'ом).
- Edge cases: контейнер уже запущен на старой сети → следующий deploy пересоздаст с новыми сетями (compose diff); external-сеть отсутствует на ноде → compose up FAIL — R4 converge гарантирует существование до деплоя.

**T2.4 — adopt-project.sh: валидация proxy-net**
- Новая функция `validate_compose_networks()` в `core/internal/scaffold/adopt-project.sh`: парсит compose проекта; если сервис с доменом не объявляет `proxy-net (external)` → FAIL с инструкцией (инъекцию в чужой compose НЕ делаем — принцип «adopt не мутирует src»).
- Edge cases: compose.yaml vs docker-compose.yml (оба имени); YAML-якоря/extends → парсить через `docker compose config` если docker доступен, fallback — python yaml; пустой/битый compose → FAIL с понятным сообщением.

**T2.5 — Gate-тест сети проектов (M4)**
- Расширить `tests/gates/test_gate_project_compose.py`: каждый шаблонный + adopted compose (фикстуры из templates/ и tests/test_data/) объявляет `proxy-net external`. Негативная фикстура `_negative`: compose без proxy-net → gate падает.

**T2.6 — Runbook: очистка /etc/hosts (G5)** — секция OperatorChecklist §10, п.3: однократное удаление строк `172.18.0.5 tronyx-site` / `172.18.0.6 dance-site` ПОСЛЕ раскатки T2.1 (порядок критичен: сначала resolver-конфиг + node-update, потом очистка, иначе nginx с old-конфигом упадёт).

### Волна 3 — Deploy pipeline и CI (Кодер 1, ai-platform + tronyx-site)

**T3.1 — deploy-project.sh: финализация не роняет деплой (B1/A8)**
- `main()`: сразу после успешного `poll_until_healthy` (строка 997) — `DEPLOY_STATUS="success"`; `trap - ERR` (health-gate пройден, дальше нефатальная зона).
- Обернуть нефатальные шаги: `tag_current || true`, `prune_old_images || log_imp 8 ...`, `_trigger_deploy_hooks || ...`, `audit_log ... || log_imp 6 "audit unavailable"`, `notify_hook` (уже `|| true` внутри, но `[[ -x ]]`-ветка при 644 → лог, не сбой — проверить).
- Симметрично в rollback-ветке: `DEPLOY_STATUS` смыслово корректен до notify.
- MODULE_CONTRACT: обновить инварианты (финализация нефатальна; ERR trap снимается после health-gate) + TRAP[BUG] с root cause B1.
- Edge cases: сбой САМОГО health-gate → прежнее поведение (rollback, exit 1) — не ослаблять; сбой `_write_deploy_result` в EXIT-trap (диск полон) → лог в stderr, exit code не маскировать; двойной EXIT-trap вызов — bash гарантирует однократность; deliver-verb ветка (`DEPLOY_STATUS="deliver"`) — не задевать; конкурентные деплои одного проекта → вне скоупа (CI сериализует), зафиксировать в @invariants.

**T3.2 — Негативный тест B1**
- `tests/test_deploy_finalization.py` (или расширение существующего contract-теста): harness запускает deploy-project.sh в изолированном окружении (tmp_path, PROJECTS_BASE=tmp, фейковые docker/notify через PATH-стабы — для .sh субпроцесс легитимен, запрет subprocess касается python-бизнес-логики): (1) notify-hook отсутствует → deploy-result.json=success, exit 0; (2) audit.log недоступен для записи → success, exit 0; (3) health-gate падает → failed, exit 1 (анти-регрессия, тест `_negative`); LDD-трассировка IMP:7–10 в stdout по протоколу §TESTING.

**T3.3 — tronyx-site: миграция на reusable (A9/G6)**
- `tronyx-site/.github/workflows/deploy.yml`: переписать по образцу dance-site — build+push ghcr → `uses: TronyxLab/ai-platform/.github/workflows/deploy-project.yml@main` + `secrets: inherit`; hardcode 103.88.243.151 удаляется (reusable резолвит `vars.NODE_HOST_MAP`); deliver-step приходит из reusable.
- Удалить `tronyx-site/.github/workflows/platform-deploy.yml`; снять TRAP[DECISION] «SSH host hardcoded temporarily» (Rev исполнен).
- Edge cases: NODE_HOST_MAP не задана/битый JSON → reusable step «Resolve target node» обязан FAIL с внятным сообщением (проверить и при необходимости добавить валидацию в deploy-project.yml); staging-логика tronyx-site (dev branch) → перенести как отдельный job по образцу dance-site `deploy-staging`; первый прогон после миграции — контролируемый push в отдельной ветке.

**T3.4 — context-promote: SSH push с fallback (B4/A12/G8)**
- `core/entrypoints/context-promote.sh`: (1) primary — `git push --mirror git@github.com:${CONTEXT}/ai-platform.git` (ключ оператора из ssh-agent); (2) fallback — существующий HTTPS+GIT_MIRROR_TOKEN через GIT_ASKPASS, если SSH недоступен И токен задан; (3) fail-fast если оба канала недоступны, с инструкцией.
- MODULE_CONTRACT: инварианты обновить (токен опционален); TRAP[DECISION]: SSH primary, HTTPS fallback, причина — локальное выполнение у оператора, node-секреты недоступны.
- Edge cases: ssh-agent пуст → детект `ssh -T git@github.com` (timeout 10s) до push, переход на fallback без частичного push; push --mirror в несуществующую org/repo → понятный FATAL «create <org>/ai-platform first»; частичный сбой push (сеть) → `--mirror` атомарен per-ref, повторный запуск конвергентен; entrypoint остаётся ≤150 LOC (gate thin_wrapper) — при превышении вынести в `core/internal/deploy/context-promote-impl.sh` с регистрацией.

**T3.5 — Диагностика B2 (ghcr 403 dance-site)**
- Диагностический протокол (исполняет оператор/QA c правами org): сравнить `gh api /orgs/TronyxLab/packages?package_type=container` — привязка пакета dance-site к репо; проверить package settings → Manage Actions access; при отсутствии пакета — первый push с PAT (write:packages) и связка с репо.
- Acceptance: либо зелёный push, либо BLOCKED с зафиксированной причиной в OperatorChecklist. Кода в репо не порождает (кроме возможного TRAP[DECISION] в dance-site deploy.yml по итогам).

**T3.6 — Диагностика B3 + раскатка repo-secrets (M6/A11/G7)**
- Диагностика: минимальный тестовый workflow в приватном репо TronyxLab, читающий org-secret → подтвердить/опровергнуть ограничение GitHub Free.
- Если подтверждено: новый internal `core/internal/scaffold/sync-repo-secrets.sh` + таргет `make project-sync-secrets NAME=<n> [NODE=<node>]` (регистрация: Makefile, entrypoint-manifest.yaml scaffold-секция, глоссарий root AGENTS.md): sops -d enc-файла ноды → `gh secret set CI_DEPLOY_KEY|DOCKER_HUB_* --repo TronyxLab/<name>` + `gh variable set` для NODE_HOST_MAP (org-variable — однократно, в OperatorChecklist).
- КОНСТИТУЦИЯ §2: значения секретов не логировать; передача только через stdin `gh secret set --body-stdin`... (точный флаг: `gh secret set NAME < file` / `--body -`); аудит-лог фиксирует ИМЕНА, не значения.
- Обновить root AGENTS.md: результат диагностики B3 + TRAP[DECISION] «repo-level secrets навсегда» (если подтверждено).
- Edge cases: gh не установлен/не залогинен → fail-fast с инструкцией `gh auth login`; SOPS-ключ недоступен → fail-fast (существующий detect_age_key паттерн); повторный запуск → gh secret set идемпотентен (overwrite); секрет отсутствует в enc-файле → перечислить недостающие ключи и FAIL до первой записи (всё-или-ничего); rate-limit GitHub API → gh сам ретраит, при отказе — понятный вывод.

### Волна 4 — QA (доступ к прод-VPS через канонические таргеты — G11)

- Q4.1 `make gate MODE=fast` локально зелёный; затем `ruff format . && ruff check --fix .` (CI pre-flight правило).
- Q4.2 `make bootstrap-node NODE=tronyx-vps DRY_RUN=1` — план без мутаций, converge-шаг виден в плане.
- Q4.3 `make converge NODE=tronyx-vps` дважды: первый — отчёт мутаций (легализация текущего дрейфа), второй — полный no-op (все юниты SKIP). Сохранить `--report-only` JSON до/после как evidence.
- Q4.4 Негативные gate-тесты M1–M5 + R6: test_gate_executable_bit (M1), unit-тесты R2 прав audit.log (M2), R3 идемпотентность на tmp-фикстуре node.yaml (M3), test_gate_project_compose расширенный (M4), R5 детекция hosts-дрейфа на фикстуре (M5), R6 на фикстуре: удалённый vhost → FAIL, правленный (hash mismatch) → drift, сирота → WARN (AC12). Все — с LDD-трассировкой и негативными парами (Test Honesty R5).
- Q4.5 Раскатка по порядку G13: ai-platform push (CI) → `make render-vhosts NODE=tronyx-vps` + commit node-configs (ревью diff: рукописные vhost → GENERATED) → `make node-update NODE=tronyx-vps` → OperatorChecklist п.3 (/etc/hosts) → project CI.
- Q4.6 Финал SCENARIO_RECREATE: `restart-hard` проектных контейнеров (module-level canonical verb) → `make verify NODE=tronyx-vps` + `make healthcheck NODE=tronyx-vps`: nginx НЕ падает, домены отвечают (отказ от /etc/hosts подтверждён).
- Q4.7 SCENARIO_B1_NEGATIVE на реальном деплое: `make deploy PROJECT=tronyx-site` → CI зелёный, deploy-result.json=success, exit 0.
- Вердикт по единой шкале (SUCCESS/PARTIAL/FAIL/BLOCKED) в `02-VerificationReport.md`.

---

## §7. Step-by-step Data Flow (симуляция целевого сценария)

```
1. Оператор: make bootstrap-node NODE=tronyx-vps        (bare-metal init)
   ├─ scp-deliver: prepare_ssh_opts(mode=init) → ssh-keygen -R + accept-new   [M7 закрыт]
   ├─ rsync -a core/ → права из git-индекса УЖЕ 755                            [M1 превентивно]
   └─ node-lifecycle init: ... → step_17_converge
        ├─ R1: chmod-дочинка (0 файлов — уже 755) → SKIP
        ├─ R2: /var/log/platform 0750 root:adm; audit.log 0664 root:adm        [M2 закрыт]
        ├─ R3: /opt/projects/{tronyx-site,dance-site}/ + stub-файлы, ci-deploy [M3 закрыт]
        ├─ R4: proxy-net ensured                                               [M4 закрыт]
        ├─ R5: /etc/hosts чист (новая нода) → SKIP                             [M5 n/a]
        └─ R6: GENERATED vhosts ↔ node.yaml, nginx -t → OK                     [B5/M5 верифицированы]
1b. Оператор (при изменении projects): make render-vhosts NODE=tronyx-vps
   → diff в git-ревью (node-configs) → commit → доставка следующим bootstrap/node-update rsync-каналом
2. git push (проект) → project CI (reusable deploy-project.yml)
   ├─ Resolve node: vars.NODE_HOST_MAP → 103.88.243.151                        [hardcode удалён]
   ├─ Deliver: tar → ssh ci-deploy "platform-deliver <name>" → payload поверх stub'ов
   └─ Deploy: deploy-project.sh → health-gate OK → DEPLOY_STATUS=success → trap - ERR
        → tag/prune/hooks/audit/notify (нефатальны) → exit 0                   [B1 закрыт]
3. nginx (Docker, proxy-net): GENERATED vhosts (render-vhosts из node.yaml → git-ревью → rsync)
   → resolver 127.0.0.11 + variable proxy_pass: Docker DNS; force-recreate меняет IP → TTL 30s [M4/M5/B5 закрыты]
   → converge R6 на ноде: vhost↔node.yaml + nginx -t до reload
4. make context-promote CONTEXT=tronyx-lab → SSH push (ключ оператора)          [B4 закрыт]
5. Повторный make bootstrap-node / make converge → все шаги SKIP (no-op)        [инвариант №6]
```

## §8. Draft Code Graph (XML)

```xml
<CodeGraph plan="015-legalize-vps-mutations">
  <Entity name="converge_sh" TYPE="SCRIPT" path="core/internal/bootstrap/converge.sh">
    <keywords>reconciler, desired-state, idempotent, node-yaml, drift</keywords>
    <annotation>Реконсилер R1-R5; вызывается из node-lifecycle (init/update) и entrypoint</annotation>
    <CrossLinks>node_lifecycle_sh, docker_lib_sh, audit_logging_sh, entrypoint_converge_sh</CrossLinks>
    <Func name="reconcile_perms_FUNC"/>
    <Func name="reconcile_audit_log_FUNC"/>
    <Func name="reconcile_projects_FUNC"/>
    <Func name="reconcile_networks_FUNC"/>
    <Func name="detect_hosts_drift_FUNC"/>
    <Func name="verify_vhosts_FUNC"/>
  </Entity>
  <Entity name="add_vhost_sh" TYPE="SCRIPT" path="core/internal/scaffold/add-vhost.sh">
    <keywords>vhost-template, render-all, generated-header, content-hash, resolver-variable, nginx-t-harness, make-render-vhosts</keywords>
    <annotation>S1: генерация vhost из node.yaml#projects; детерминированный вывод; всё-или-ничего рендер</annotation>
    <CrossLinks>scaffold_sh, nginx_overlay_conf, converge_sh, content_hash_sh</CrossLinks>
  </Entity>
  <Entity name="entrypoint_converge_sh" TYPE="SCRIPT" path="core/entrypoints/converge.sh">
    <keywords>thin-wrapper, ssh-proxy, make-converge</keywords>
    <annotation>≤150 LOC; локальный exec или SSH-proxy по образцу node-update.sh</annotation>
    <CrossLinks>converge_sh, remote_cmd_sh, Makefile</CrossLinks>
  </Entity>
  <Entity name="deploy_project_sh" TYPE="SCRIPT" path="core/internal/deploy/deploy-project.sh">
    <keywords>finalization, err-trap, deploy-status, non-fatal</keywords>
    <annotation>B1: DEPLOY_STATUS=success сразу после health-gate; trap - ERR; финализация нефатальна</annotation>
    <CrossLinks>audit_logging_sh, notify_hook_sh, test_deploy_finalization_py</CrossLinks>
  </Entity>
  <Entity name="scp_deliver_sh" TYPE="SCRIPT" path="core/internal/bootstrap/scp-deliver.sh">
    <keywords>known-hosts, init-only, accept-new, prepare-ssh-opts</keywords>
    <annotation>M7: ssh-keygen -R только в mode=init</annotation>
    <CrossLinks>bootstrap_sh, node_update_sh</CrossLinks>
  </Entity>
  <Entity name="context_promote_sh" TYPE="SCRIPT" path="core/entrypoints/context-promote.sh">
    <keywords>ssh-push, https-fallback, git-mirror-token-optional</keywords>
    <annotation>B4: primary git@github.com push, fallback GIT_ASKPASS+token</annotation>
    <CrossLinks>mirror_yml</CrossLinks>
  </Entity>
  <Entity name="sync_repo_secrets_sh" TYPE="SCRIPT" path="core/internal/scaffold/sync-repo-secrets.sh">
    <keywords>gh-secret-set, sops, repo-secrets, github-free</keywords>
    <annotation>M6/B3: раскатка repo-secrets из SOPS через gh CLI; условно — по итогам диагностики B3</annotation>
    <CrossLinks>scaffold_sh, tronyx_vps_enc_yaml</CrossLinks>
  </Entity>
  <Entity name="nginx_overlay_conf" TYPE="CONFIG" path="node-configs/tronyx-vps/overlays/nginx/nginx.conf">
    <keywords>generated-artifact, resolver-127-0-0-11, variable-proxy-pass, http2-on, lazy-resolution</keywords>
    <annotation>M4/M5/B5: GENERATED артефакт render-vhosts (после T2.2); Docker DNS вместо /etc/hosts</annotation>
    <CrossLinks>nginx_module_compose, add_vhost_sh</CrossLinks>
  </Entity>
  <Entity name="test_gate_executable_bit_py" TYPE="TEST" path="tests/gates/test_gate_executable_bit.py">
    <keywords>git-index-mode, 100755, lib-exemption, negative-test</keywords>
    <annotation>M1: все tracked .sh вне core/lib/ = 100755</annotation>
    <CrossLinks>entrypoint_manifest_yaml</CrossLinks>
  </Entity>
  <Entity name="test_deploy_finalization_py" TYPE="TEST" path="tests/test_deploy_finalization.py">
    <keywords>notify-missing, audit-denied, deploy-result-success, ldd-trajectory</keywords>
    <annotation>B1 негативные сценарии + анти-регрессия health-gate FAIL</annotation>
    <CrossLinks>deploy_project_sh</CrossLinks>
  </Entity>
</CodeGraph>
```

## §9. Acceptance Criteria (сводные, проверяемые)

| # | Критерий | Проверка |
|---|----------|----------|
| AC1 | `make gate MODE=fast` зелёный локально | Волна 4 Q4.1 |
| AC2 | Все tracked `.sh` вне core/lib/ = 100755; gate падает на негативной фикстуре | test_gate_executable_bit |
| AC3 | `make converge NODE=tronyx-vps` 2-й запуск = no-op (все R-юниты SKIP) | Q4.3, report-only JSON |
| AC4 | deploy при отсутствующем notify-hook и/или недоступном audit.log → deploy-result.json=success, exit 0 | test_deploy_finalization + Q4.7 |
| AC5 | `restart-hard` проектов → nginx Up, `make verify NODE=tronyx-vps` PASS | Q4.6 |
| AC6 | tronyx-site и dance-site CI зелёные через reusable, без hardcode IP | push после T3.3 |
| AC7 | `make context-promote CONTEXT=…` работает без GIT_MIRROR_TOKEN (SSH) | T3.4 smoke |
| AC8 | Bootstrap DRY_RUN и повторный bootstrap-node = no-op (инвариант №6 сохранён) | Q4.2 |
| AC9 | B2/B3: диагноз зафиксирован; при BLOCKED — причина + шаги в OperatorChecklist | T3.5/T3.6 |
| AC10 | Глоссарий/манифест/Makefile/AGENTS.md консистентны (manifest-integrity gate зелёный) | существующий gate |
| AC11 | `make render-vhosts NODE=tronyx-vps` детерминирован: повторный рендер без изменений node.yaml → пустой git-diff | T2.1 |
| AC12 | R6: удалённый/вручную правленный vhost на фикстуре → converge FAIL/WARN по §5.4; негативная пара теста присутствует | T1.6b + Q4.4 |

## §10. OperatorChecklist (ручные операции вне кода — допущены решением G7+G12)

1. **[B3-диагностика]** Прогнать тестовый workflow чтения org-secret в приватном репо TronyxLab; зафиксировать результат в 02-VerificationReport.
2. **[B2]** GitHub UI/`gh api`: проверить привязку пакета `ghcr.io/tronyxlab/dance-site` к репо; при отсутствии — первый push с PAT write:packages, связать с репо; при недостатке прав — BLOCKED.
3. **[M5-миграция]** ПОСЛЕ node-update с GENERATED vhost-конфигами (T2.1/T2.2): удалить из /etc/hosts на tronyx-vps строки `tronyx-site` и `dance-site`; затем Q4.6. Единственная разовая ручная операция — легализована решением G5 как runbook.
4. **[NODE_HOST_MAP]** Убедиться, что org-variable `NODE_HOST_MAP={"tronyx-vps":"103.88.243.151"}` существует в TronyxLab (бриф утверждает — создана; верифицировать).
5. **[gh auth]** `gh auth login` с правами repo+secrets для project-sync-secrets (T3.6).

## §11. Открытые риски

| Риск | Митигация |
|------|-----------|
| R1: converge — новый глагол; гейты manifest-integrity/thin-wrapper/dead-code/name-linter упадут при рассинхроне регистрации | T1.1 включает ВСЕ точки регистрации атомарно; gate MODE=fast до push |
| R2: B2/B3 зависят от прав в GitHub UI — DoD «оба CI зелёные» может застрять в BLOCKED | Явно допущено (G9/G12): BLOCKED + OperatorChecklist = валидный частичный исход |
| R3: раскатка nginx-конфига и очистка /etc/hosts чувствительны к порядку — обратный порядок уронит прод-ingress | Жёсткий порядок в Q4.5/§10.3; resolver-конфиг обратно-совместим с наличием hosts-записей |
| R4: stub ai-platform.yaml (G2) может замаскировать неполный deliver-payload | Stub с маркером GENERATED-STUB; deploy-verb использует `service:` из stub = имя проекта — совпадает с конвенцией; deliver перезаписывает |
| R5: 4 репозитория, gate только в ai-platform — node-configs/проекты меняются без CI-сети безопасности | nginx -t локально (T2.1); compose config валидация (T2.3); порядок G13 |
| R6: sync-repo-secrets работает с расшифрованными секретами | stdin-only передача, значения не логируются (Конституция §2), tmp-файлы не создаются |
| R7: баг vhost-шаблона затрагивает все домены разом (цена генеративности, главный контраргумент B) | Тройной барьер S1: git-ревью diff в node-configs → локальный nginx -t harness до push → R6 nginx -t на ноде до reload; работающий nginx не применяет битый конфиг (reload-валидация); откат = git revert + node-update |
| R8: рассинхрон «шаблон обновлён, vhost не перерендерены» (новый класс дрейфа, порождаемый S1) | R6 сверяет content-hash тела с ожидаемым от текущего шаблона; render-vhosts в чеклист релиза при изменении add-vhost.sh |

# $END_DEVPLAN
