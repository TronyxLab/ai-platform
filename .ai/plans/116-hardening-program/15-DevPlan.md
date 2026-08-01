# 15-DevPlan — B5: Shared-консолидация операционных политик

<!-- GREP_SUMMARY: docker-compose ssh-opts timeouts retry healthcheck shared sole-path platform_config intervals ssh_opts -->
<!-- STRUCTURE: ┌решения архитектора D1-D8┐ → ◇ T1 timeouts.py → ◇ T2 ssh_opts.py → ◇ T3 docker_compose API → ◇ T4 docker_orchestrator → ◇ T5 deploy_engine → ◇ T6 reconciler → ◇ T7 channels → ◇ T8 platform_config → ◇ T9 healthcheck → ◇ T10 compose-интервалы+гейты → ⊕ T11 самоверификация -->
# region MODULE_CONTRACT
## @purpose  Волна B5 программы хардненинга (116): сделать shared-модули операционных политик ЕДИНСТВЕННЫМ путём (docker compose up/pull/retry, healthcheck-критерий, SSH_OPTS, timeouts) и удалить расходящиеся копии. 0 production-потребителей docker_compose_up → 3 (docker_orchestrator, DeployEngine, reconciler). Расхождение уже началось: --remove-orphans только в одной копии, timeout 120/180/30, ConnectTimeout=10 outlier.
## @scope    U-11, U-13, U-14, U-15, U-34, U-63. Файлы: core/internal/shared/{timeouts.py(NEW),ssh_opts.py(NEW),docker_compose.py}, core/internal/bootstrap/deploy/{docker_orchestrator.py,context_deployer.py}, core/internal/deploy/{deploy_engine.py,channels.py,healthcheck_poller.py,context_promoter.py}, core/internal/bootstrap/{core_deliverer.py,overlay_deliverer.py,remote_executor.py,converge/reconciler.py}, core/internal/config/platform_config.py, core/lib/{ssh.sh,healthcheck.sh}, core/modules/*/docker-compose.base.yml (postgres), core/entrypoint-manifest.yaml, core/AGENTS.md, tests/.
## @invariants
##   1. Sole-path: каждая операционная политика имеет ровно одну реализацию; копии запрещены гейтом.
##   2. Комментарии «Mirror lib/ssh.sh» устраняются — импорт канона вместо копирования.
##   3. state_machine.py НЕ трогается (мораторий инварианта 4 программы до B9) — allowlist в гейте.
##   4. Consumer-scan обязателен при любом удалении кода (инвариант 2 программы).
##   5. Fail-visible вместо тихих fallback (консистентно с B6 D4): platform_config без литеральных fallback'ов.
## @rationale Shared-модули созданы (DevPlan 079), но мёртвые: docker_compose_up — 0 production-потребителей, каждая новая волна добавляет 4-ю копию вместо перехода на shared (RC7). Волна делает структурно невозможным расхождение политик через код + гейты (sole-path + parity).
## @changes 2026-08-01 · Решения пользователя: (D1) SSH_OPTS — Python SoT shared/ssh_opts.py, lib/ssh.sh — тонкий фасад через python3 -m (уменьшение bash-поверхности); (D2) platform_config fallback-константы удаляются (fail-visible); (D3) state_machine — в allowlist до B9; (D4) интервалы healthcheck — классы 15/30/60, postgres 10s→15s; (D5) lib/healthcheck.sh остаётся shell-фасадом, критерий унифицируется, parity-гейт.
## @changes  SUPERSEDED 2026-08-01 — закрыт волнами 116; VR не требуется (D5, DevPlan 116 B11 T8 U-84) — 15-DevPlan.md
# endregion MODULE_CONTRACT

$ARTIFACT_CONTRACT:
  PURPOSE: Реализация волны B5 — 11 задач от timeouts/ssh_opts SoT до гейтов самоверификации.
  DESCRIPTION: Пошаговый план с точными файлами/строками, критериями приёмки на каждую U-проблему, новыми гейтами (trinity), порядком самоверификации.
  RATIONALE: Бриф фиксирует цели; DevPlan фиксирует решения архитектора (D1-D8, подтверждены пользователем 2026-08-01) и исполнительные шаги, чтобы Coder работал без архитектурных развилок.
  ACCEPTANCE_CRITERIA: (1) docker compose up/pull — одна реализация (shared/docker_compose.py), 4 локальные копии удалены; флаги/timeouts едины; (2) retry_pull — одна реализация с backoff [5,10,20], docker_orchestrator получает retry; (3) healthcheck — единый критерий «здоров» (inspect State.Health, running-без-healthcheck = здоров), 5 реализаций → 1 + тонкие обёртки; (4) SSH_OPTS — одна константа в shared/ssh_opts.py, 5 Python-копий импортируют; ConnectTimeout единый (30); lib/ssh.sh — фасад; (5) shared/timeouts.py: COMPOSE_UP_TIMEOUT/PULL_TIMEOUT/SSH_CONNECT_TIMEOUT/HEALTHCHECK_POLL_TIMEOUT — литералы в docker/ssh/healthcheck-домене заменены; (6) platform_config: fallback-константы удалены, чтение platform-env.yaml без cwd-эвристики; (7) интервалы healthcheck в compose: классы 15s/30s/60s (postgres 10s→15s) + гейт.
  IMPLEMENTS: U-11 (timeouts), U-13 (docker ops ×4 + retry ×3), U-14 (healthcheck ×5), U-15 (SSH_OPTS ×5), U-34 (platform_config fallbacks), U-63 (интервалы healthcheck)
  IMPACTS: core/internal/shared/{timeouts.py,ssh_opts.py,docker_compose.py}, bootstrap/deploy/{docker_orchestrator.py,context_deployer.py}, deploy/{deploy_engine.py,channels.py,healthcheck_poller.py,context_promoter.py}, bootstrap/{core_deliverer.py,overlay_deliverer.py,remote_executor.py,converge/reconciler.py}, config/platform_config.py, core/lib/{ssh.sh,healthcheck.sh}, core/modules/postgres/docker-compose.base.yml, core/entrypoint-manifest.yaml, core/AGENTS.md, shared/AGENTS.md, tests/gates/*, tests/unit/*
  REQUIRES: 04-Brief (B5); решения пользователя 2026-08-01 (D1-D5); B2 (parity-гейты механика), B4 (PlatformError контракт — shared-модули используют его), B6 (NodeYaml/context — контекст уже на фасаде)
---

## 1. Решения архитектора (подтверждены пользователем 2026-08-01)

| # | Вопрос | Решение |
|---|--------|---------|
| D1 | SoT для SSH_OPTS (U-15) | **Python SoT + shell-фасад.** Новый `shared/ssh_opts.py` — единственный источник флагов. 5 Python-копий (core_deliverer, overlay_deliverer, channels ×2, remote_executor) заменяются импортом. `lib/ssh.sh` получает SSH_OPTS_COMMON через `python3 -m core.internal.shared.ssh_opts --shell` (паттерн audit.sh: PYTHONPATH-init от BASH_SOURCE, source-guard readonly сохранён). ssh.sh source-ится всего в 3 местах — runtime-цена одного python3-вызова приемлема. **Уменьшение bash-поверхности** (пожелание пользователя). Парity-гейт shell↔Python не нужен — копии в shell нет; вместо него гейт «0 SSH_OPTS-литералов вне ssh_opts.py» |
| D2 | platform_config fallback-константы (U-34) | **Удалить, fail-visible** (консистентно с B6 D4): `_FALLBACK_S3_REGION/_FALLBACK_S3_PREFIX/_FALLBACK_S3_BUCKET/_FALLBACK_PLATFORM_CONTEXT` удаляются; отсутствие platform-env.yaml → "" + громкий WARNING/ERROR. Cwd-эвристика (4 уровня вверх) заменяется каноническим резолвингом: env `PLATFORM_ROOT` → script-relative корень репо. Контейнерные потребители (agent_watchdog, s3_ssl_cache, backup_config) уже пережили D4-семантику CONTEXT="" — поведение консистентно |
| D3 | state_machine.py (U-11, мораторий B9) | **Не трогать.** Весь state_machine.py — в allowlist гейта timeout-литералов до волны B9. Запрет: любые правки state_machine.py в этой волне |
| D4 | Интервалы healthcheck в compose (U-63) | **Классы 15/30/60:** критичные данные (postgres, clickhouse, minio, langfuse, litellm, hermes-agent) = 15s; сервисы (redis, nginx, status-page, monitoring, logging, infra-metrics) = 30s; фоновые (backup-cron) = 60s. Единственная правка compose: postgres 10s → 15s (устраняет самопротиворечие со start_period 15s). Гейт проверяет классификацию |
| D5 | lib/healthcheck.sh (U-14) | **Остаётся shell-фасадом** (модульные healthcheck'и на VPS без python-зависимости). Критерий унифицируется: контейнер без healthcheck (inspect Health.Status == "") в состоянии running → **здоров** (0), как в deploy_engine._poll_health; "starting"/""-не-running → 2. Parity-гейт: только shared healthcheck_poll имеет Python-реализацию docker-критерия |
| D6 | Различение TIMEOUT/FAILED в audit docker_orchestrator | Shared-функции возвращают bool (контракт non-fatal, никогда raise) + детализированные логи IMP:7/10. docker_orchestrator пишет audit status FAILED на False (различение TIMEOUT/ERROR/FAILED схлопывается — детали остаются в логах). Приемлемая деградация audit-трейла, компенсируется структурным единством |
| D7 | Расширение shared API | `docker_compose_up/pull/build` получают `compose_args` (pull — уже есть; up/build — добавляется), `service=None`, `env_override=None`, `flags: list[str]=[]` (up: политические флаги --remove-orphans/--force-recreate). `retry_pull` получает те же параметры + `max_attempts`/`backoff_seconds`. Обратная совместимость: все новые параметры опциональны с дефолтами |
| D8 | reconciler self-heal timeout | DOCKER_TIMEOUT=30 (self-heal up) → COMPOSE_UP_TIMEOUT=180: текущее 30s — занижено для up с пуллом образов; стандартизация на канон (задокументированное поведенческое изменение) |

---

## 2. Текущее состояние worktree (старт волны)

- HEAD `ec55571` (main), рабочее дерево ЧИСТОЕ (B2 + B6 закоммичены).
- Shared-модули: `docker_compose.py` (356 LOC) — up/pull/build/healthcheck_poll/retry_pull/check_image_exists; константы PULL_TIMEOUT=120/BUILD_TIMEOUT=300/UP_TIMEOUT=120/HEALTHCHECK_TIMEOUT=60 живут в самом модуле; healthcheck_poll — через `docker ps` (НЕ inspect); up НЕ поддерживает compose_args/flags/service/env.
- Копии политик (цель удаления):
  - `docker_orchestrator.py:668-678` (up --remove-orphans --force-recreate, timeout=180, build-skip ветка), `683-707` (build inline, timeout=120), `691-745` (up --remove-orphans [+--force-recreate], timeout=180), `892-895` (pull через shared, timeout=300, БЕЗ retry).
  - `deploy_engine.py:735-790` (_pull_image_with_retry: timeout=120, delays [5,10,20], env IMAGE_TAG, service), `791-822` (_atomic_up: timeout=120, service, env IMAGE_TAG), `834-875` (_poll_health: docker compose ps -q + inspect State.Status/Health.Status, interval=2), `880-930` (_perform_rollback: up --force-recreate, timeout=120).
  - `reconciler.py:2057-2068` (self-heal `docker compose -f <file> up -d`, DOCKER_TIMEOUT=30, строка 61).
  - `channels.py:41-43` (DEFAULT_DEPLOY_TIMEOUT=600, DEFAULT_RETRY_COUNT=2, DEFAULT_RETRY_BACKOFF=5), `199-213` (SCPChannel.ssh_opts, 5 флагов), `385-399` (ForcedCommandChannel.ssh_opts, 4 флага — без BatchMode).
  - `core_deliverer.py:37-52` (SSH_OPTS «Mirror lib/ssh.sh», 5 флагов), `overlay_deliverer.py:83-98` (SSH_OPTS), `remote_executor.py:42` (импорт из overlay_deliverer), `context_promoter.py:74` (`ssh -T -o ConnectTimeout=10 -o BatchMode=yes git@github.com` — outlier ConnectTimeout=10).
  - `lib/ssh.sh:52-63` (readonly SSH_OPTS_COMMON, 5 флагов, ConnectTimeout=30) — единственная shell-реализация.
  - `platform_config.py:33-40` (4 fallback-константы), `70-87` (cwd-эвристика поиска platform-env.yaml).
  - `lib/healthcheck.sh:179-213` (check_docker_health: inspect {{.State.Health.Status}}, возвращает 2 для starting/""), `healthcheck_poller.py:36-38` (DEFAULT_POLL_TIMEOUT=30/INTERVAL=10/MAX_RETRIES=6; docker-путь — inspect).
- Существующие гейты: `test_gate_healthcheck_unification.py` (AC4: start_period ∈ {5,15,30,60}), `test_gate_healthcheck_contract.py` (модульные контракты) — НЕ покрывают interval и sole-path docker/ssh.
- 140 литералов `timeout=N` в core/internal Python (U-11; в брифе 226 — с учётом shell).

---

## 3. Задачи

### T1 — U-11: shared/timeouts.py — единый реестр таймаутов [FUNDAMENT]

**1. Новый `core/internal/shared/timeouts.py`** (MODULE_CONTRACT + GREP_SUMMARY/STRUCTURE, по стандарту shared):

| Константа | Значение | Потребители |
|-----------|----------|-------------|
| `COMPOSE_UP_TIMEOUT` | 180 | docker_compose_up, docker_orchestrator, deploy_engine, reconciler |
| `PULL_TIMEOUT` | 300 | docker_compose_pull, retry_pull, deploy_engine, docker_orchestrator |
| `BUILD_TIMEOUT` | 300 | docker_compose_build, docker_orchestrator |
| `HEALTHCHECK_POLL_TIMEOUT` | 60 | healthcheck_poll, context_deployer, deploy_engine |
| `SSH_CONNECT_TIMEOUT` | 30 | ssh_opts.SSH_OPTS, context_promoter |
| `DEPLOY_TIMEOUT` | 600 | channels.DEFAULT_DEPLOY_TIMEOUT, remote_executor |
| `SSH_READ_TIMEOUT` | 60 | channels (scp/ssh вызовы timeout=60), ssh_read-эквиваленты |
| `RETRY_BACKOFF_SECONDS` | [5, 10, 20] | retry_pull (дефолт), deploy_engine (delays) |
| `IMAGE_CHECK_TIMEOUT` | 60 | check_image_exists |
| `DOCKER_CMD_TIMEOUT` | 10 | внутренние подвызовы docker ps/inspect в healthcheck_poll |

**2. `docker_compose.py`:** удалить локальные константы (35-40), импортировать из timeouts; константы сохраняются как re-export (`COMPOSE_UP_TIMEOUT = timeouts.COMPOSE_UP_TIMEOUT`) НЕ нужны — обновить все импорты потребителей (`from core.internal.shared.timeouts import ...`).

**3. `channels.py:41-43`:** `DEFAULT_DEPLOY_TIMEOUT`/`DEFAULT_RETRY_COUNT`/`DEFAULT_RETRY_BACKOFF` → импорт из timeouts (DEFAULT_RETRY_COUNT=2 — оставить как константу канала? НЕТ — перенести в timeouts как `RETRY_COUNT`).

**4. `remote_executor.py`:** `timeout=600` → `DEPLOY_TIMEOUT`.

**5. ВАЖНО:** не трогать state_machine.py (D3).

**Критерий приёмки:** rg `timeout=120|timeout=180|timeout=300|timeout=600` в docker/ssh/healthcheck-домене core/internal → 0 (кроме allowlist: state_machine.py, HTTP/S3-клиенты — покрыты гейтом T10).

---

### T2 — U-15: shared/ssh_opts.py — единый SoT SSH-флагов + фасад lib/ssh.sh [FUNDAMENT]

**1. Новый `core/internal/shared/ssh_opts.py`:**
- `SSH_OPTS: list[str] = ["-o", "BatchMode=yes", "-o", "StrictHostKeyChecking=accept-new", "-o", "ConnectTimeout=30", "-o", "ServerAliveInterval=30", "-o", "ServerAliveCountMax=10"]` (ConnectTimeout=30 из timeouts.SSH_CONNECT_TIMEOUT — f-string в константу: `f"-o", f"ConnectTimeout={SSH_CONNECT_TIMEOUT}"`... список литералов: `"-o", f"ConnectTimeout={timeouts.SSH_CONNECT_TIMEOUT}"`).
- `build_rsync_ssh_opts() -> str` — `f"ssh {' '.join(SSH_OPTS)}"` (переезд из core_deliverer.py:89-92 / overlay_deliverer.py:103-106 — единственная реализация).
- CLI: `python3 -m core.internal.shared.ssh_opts --shell` → печатает флаги через пробел (для bash `read -r -a`); `--rsync-e` → строка `ssh -o ...`; exit 0.
- MODULE_CONTRACT + TRAP[DECISION]: фиксирует «extract when consumers > 3» из vps_readiness.py:37-42 — триггер сработал (5 потребителей).

**2. Замена 5 Python-копий на импорт:**
- `core_deliverer.py:37-52` — удалить `SSH_OPTS` list; `from core.internal.shared.ssh_opts import SSH_OPTS, build_rsync_ssh_opts`; удалить комментарий «Mirror lib/ssh.sh»; `build_rsync_ssh_opts` (89-92) — удалить локальную, использовать импортированную.
- `overlay_deliverer.py:83-98` — то же; `overlay_deliverer.py:341` — `["ssh", *SSH_OPTS, ...]` остаётся (SSH_OPTS — импортированный).
- `remote_executor.py:42` — импорт из overlay_deliverer заменить на `from core.internal.shared.ssh_opts import SSH_OPTS`.
- `channels.py:199-213` (SCPChannel) — `self.ssh_opts: list[str] = list(SSH_OPTS)`; `385-399` (ForcedCommandChannel) — то же (BatchMode=yes добавляется — единый набор, для CI-deploy key это безопасно: ключ уже настроен, BatchMode не ломает -i-аутентификацию).
- `context_promoter.py:74` — `-o ConnectTimeout=10` → `-o ConnectTimeout=30` (единый SSH_CONNECT_TIMEOUT); github-probe остаётся отдельной командой (это НЕ копия SSH_OPTS — другой хост/протокол), но таймаут унифицируется.

**3. `lib/ssh.sh:52-63` — фасад:**
```bash
if ! declare -p SSH_OPTS_COMMON &>/dev/null; then
    _SSH_LIB_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
    export PYTHONPATH="${_SSH_LIB_DIR}/../..:${PYTHONPATH:-}"
    # TRAP[DECISION] 2026-08-01: SSH_OPTS_COMMON генерируется из shared/ssh_opts.py (Python SoT, D1).
    # bash 3.2 (macOS) — без mapfile; read -r -a по IFS; значения без пробелов.
    if ! command -v python3 >/dev/null 2>&1; then
        log_imp 10 "ssh" "python3 required for SSH_OPTS_COMMON (core.internal.shared.ssh_opts)"
        return 1
    fi
    read -r -a SSH_OPTS_COMMON <<< "$(python3 -m core.internal.shared.ssh_opts --shell)"
    readonly -a SSH_OPTS_COMMON
fi
```
Замечания: (а) если python3 -m падает (пустой вывод) — добавить проверку `${#SSH_OPTS_COMMON[@]} -eq 0` → громкий fail (иначе ssh с пустыми флагами молча повиснет); (б) `log_imp` доступен в ssh.sh (lib/logging.sh source-ится выше — проверить порядок, при необходимости фолбэк на `echo "[IMP:10]"`).

**Критерий приёмки:** rg `"Mirror lib/ssh.sh"` → 0; rg `ConnectTimeout=` в core/internal → только ssh_opts.py; rg `SSH_OPTS_COMMON` в core/lib → только ssh.sh (объявление) + потребители `${SSH_OPTS_COMMON[*]}`.

---

### T3 — U-13/U-14: shared/docker_compose.py — канонический API [FUNDAMENT]

**1. `docker_compose_up(compose_dir, timeout=COMPOSE_UP_TIMEOUT, compose_args=None, service=None, env_override=None, flags=None)`:** флаги политики `--remove-orphans/--force-recreate` — параметр `flags: list[str] = []`; `service` — для `docker compose up -d <service>`; `env_override` — dict для subprocess env (deploy_engine IMAGE_TAG). Команда: `["docker", "compose", *compose_args, "up", "-d", *flags, *(service and [service] or [])]`.

**2. `docker_compose_pull(compose_dir, timeout=PULL_TIMEOUT, compose_args=None, service=None, env_override=None)`** — расширение существующей (service+env).

**3. `docker_compose_build(compose_dir, timeout=BUILD_TIMEOUT, compose_args=None)`** — добавить compose_args (docker_orchestrator передаёт `*compose_args`).

**4. `healthcheck_poll(project_name, timeout=HEALTHCHECK_POLL_TIMEOUT, interval=3, service=None)` — ПЕРЕРАБОТКА критерия на inspect:**
```
loop до deadline:
  docker ps --filter name={project_name} --format {{.ID}} (timeout=DOCKER_CMD_TIMEOUT)
  если нет контейнеров → sleep(interval), continue
  для каждого cid: docker inspect --format '{{.State.Status}}|{{.State.Health.Status}}' {cid}
  критерий «здоров» (канон): ВСЕ контейнеры: (Status=="running" AND Health=="healthy") OR (Status=="running" AND Health=="") OR (Status=="running" AND Health=="none")
  любой "unhealthy" → ждать (не fail сразу — стартовые гонки)
  все здоровы → return "healthy"
timeout → "unhealthy"
```
Параметр `service` — фильтр `docker compose ps -q {service}` (для deploy_engine), иначе `docker ps --filter name=`. `use_inspect` убирается (всегда inspect). Докстринг/@invariants обновить: «единый критерий „здоров“ — inspect State.Health (running-без-healthcheck = здоров)».

**5. `retry_pull(compose_dir, max_attempts=3, backoff_seconds=RETRY_BACKOFF_SECONDS, timeout=PULL_TIMEOUT, compose_args=None, service=None, env_override=None)`** — проброс service/env.

**6. `check_image_exists`** — без изменений (timeout из timeouts).

**7. CLI в docker_compose.py — НЕ добавлять** (D5: shell-фасад остаётся).

**Критерий приёмки:** unit-тесты test_shared_docker_compose.py обновлены (mock docker inspect для healthcheck_poll; новые параметры up/pull/retry); импорты констант из timeouts.

---

### T4 — U-13: docker_orchestrator.py — миграция up/build/pull на shared

**1. Импорты (93-97):** + `docker_compose_up as _shared_docker_compose_up`, `docker_compose_build as _shared_docker_compose_build`, `retry_pull as _shared_retry_pull`; убрать прямой `subprocess.run` для compose.

**2. build-skip ветка (668-678):** `up_cmd_parts` → `_shared_docker_compose_up(module_dir, timeout=COMPOSE_UP_TIMEOUT, compose_args=compose_args, flags=["--remove-orphans", "--force-recreate"])`. Сохранить логику: success → sleep(1) → True; False → IMP:10 + False. try/except subprocess.TimeoutExpired/OSError → удаляются (shared ловит, возвращает False).

**3. build inline (683-707):** `["docker", "compose", *compose_args, "build"]` timeout=120 → `_shared_docker_compose_build(module_dir, timeout=BUILD_TIMEOUT, compose_args=compose_args)`. Хранение хэша (compute_source_hash/save_build_hash) — остаётся в docker_orchestrator (бизнес-логика).

**4. Основной up (691-745):** `up_cmd_parts` → `_shared_docker_compose_up(module_dir, timeout=COMPOSE_UP_TIMEOUT, compose_args=compose_args, flags=["--remove-orphans"] + (["--force-recreate"] if has_local_build else []))`. Audit-вызовы сохранить: False → audit FAILED (D6), True → audit DEPLOYED. Убрать except TimeoutExpired/OSError ветки (детали в логах shared).

**5. `_pull_module_images` (892-895):** `_shared_docker_compose_pull(...)` → `_shared_retry_pull(compose_dir, timeout=PULL_TIMEOUT, compose_args=pull_args)` (бриф: docker_orchestrator получает retry [5,10,20]). Non-fatal семантика сохраняется (False → warning, up retry'ит).

**Критерий приёмки:** rg `subprocess.run` в docker_orchestrator → 0 docker compose вызовов (остальные subprocess — rsync/ssh и пр.); rg `docker", "compose` в docker_orchestrator → 0.

---

### T5 — U-13/U-14: deploy_engine.py — миграция на shared

**1. `_pull_image_with_retry` (735-790):** → `_shared_retry_pull(project_dir, max_attempts=max_attempts, timeout=PULL_TIMEOUT, service=service, env_override={"IMAGE_TAG": ref})`. Логика rate-limit детекции (toomanyrequests/429) — перенести? НЕТ: shared не знает про rate-limit политику; решение: rate-limit детекция остаётся БОНУСОМ shared? Нет — shared docker_compose_pull уже логирует stderr[:200]; rate-limit распознавание уходит (единый backoff и так применяется). Зафиксировать: при едином retry_pull различение причины не теряется функционально (backoff одинаковый). Локальная функция удаляется.

**2. `_atomic_up` (791-822):** → `_shared_docker_compose_up(project_dir, timeout=COMPOSE_UP_TIMEOUT, service=service, env_override={"IMAGE_TAG": ref})`. Остаётся тонкой обёрткой (или вызывается инлайн — решает Coder по чистоте, обёртка допустима с делегированием).

**3. `_poll_health` (834-875):** → `_shared_healthcheck_poll(project_name=service, timeout=timeout, interval=interval, service=service) == "healthy"`. ВАЖНО: `docker compose ps -q {service}` даёт cid конкретного сервиса — shared healthcheck_poll с `service=` фильтрует так же (T3.4). Локальная реализация удаляется.

**4. `_perform_rollback` (880-930):** compose up часть → `_shared_docker_compose_up(project_dir, timeout=COMPOSE_UP_TIMEOUT, service=service, env_override=env, flags=["--force-recreate"])`. `docker tag` остаётся инлайн (не compose-политика).

**Критерий приёмки:** rg `"docker", "compose"` в deploy_engine → 0 (комментарии допускаются); _pull_image_with_retry/_atomic_up/_poll_health — тонкие обёртки или удалены.

---

### T6 — U-13: reconciler.py self-heal

**1. Строки 2057-2068:** `_run_subprocess(["docker", "compose", "-f", str(compose_file), "up", "-d"], timeout=DOCKER_TIMEOUT)` → `_shared_docker_compose_up(str(compose_file.parent), timeout=COMPOSE_UP_TIMEOUT, compose_args=["-f", str(compose_file)])`.

**2. DOCKER_TIMEOUT=30 (строка 61):** используется только в self-heal? Проверить все использования — если только тут, константа удаляется; если есть другие — оставить. Значение: self-heal получает COMPOSE_UP_TIMEOUT=180 (D8).

**3. Импорт:** `from core.internal.shared.docker_compose import docker_compose_up as _shared_docker_compose_up` (+ timeouts).

**Критерий приёмки:** rg `"docker", "compose"` в reconciler → 0; self-heal логирует через shared (IMP:9/10).

---

### T7 — U-15/U-11: channels.py + context_promoter + healthcheck_poller

**1. channels.py:** (а) константы 41-43 → timeouts (DEPLOY_TIMEOUT/RETRY_COUNT/RETRY_BACKOFF — последние две как `RETRY_COUNT`, `RETRY_BACKOFF_SECONDS[0]`? НЕТ: экспоненциальный backoff канала (5,10,20) — это `RETRY_BACKOFF_SECONDS` список; канал использует `delay = RETRY_BACKOFF_SECONDS[0]` и `delay *= 2` — сохранить поведение, источник значений — timeouts); (б) ssh_opts → `list(SSH_OPTS)` (обе ветки); (в) `timeout=60` (строка 275) — оставить (scp-внутренний) или SSH_READ_TIMEOUT — решить по смыслу: это подвызов ssh в SCPChannel — заменить на SSH_READ_TIMEOUT.

**2. context_promoter.py:74:** ConnectTimeout=10 → 30 (SSH_CONNECT_TIMEOUT импорт — строка в f-строке не нужна, литерал 30 + комментарий? Лучше: `from core.internal.shared.timeouts import SSH_CONNECT_TIMEOUT`; f-string в списке флагов).

**3. healthcheck_poller.py:** docker-путь (метод, использующий subprocess inspect) → делегирует `shared.healthcheck_poll(project_name, timeout=self.timeout, interval=self.interval)`. HTTP-путь (GET /health) остаётся в poller — это отдельная политика (HTTP-критерий), не docker-критерий. Константы 36-38 (DEFAULT_POLL_TIMEOUT=30) — остаются (HTTP-домен) ИЛИ из timeouts? HTTP-домен вне скоупа — оставить локальными (allowlist гейта).

**Критерий приёмки:** rg `"docker", "inspect` в healthcheck_poller → 0; SCPChannel/ForcedCommandChannel не содержат SSH-флагов-литералов.

---

### T8 — U-34: platform_config.py — удаление fallback'ов и cwd-эвристики

**1. Удалить:** `_FALLBACK_S3_REGION/_FALLBACK_S3_PREFIX/_FALLBACK_S3_BUCKET/_FALLBACK_PLATFORM_CONTEXT` (33-40) + `_SENTINEL_S3_BUCKET/_SENTINEL_CONTEXT` (46-48) пересматриваются: sentinel-семантики — это НЕ fallback'и SoT, они остаются (документированная семантика "").

**2. Accessors:** `default_s3_region() → get_default("S3_REGION")` (без fallback-аргумента), аналогично prefix/bucket/PLATFORM_CONTEXT. Поведение: файл отсутствует → "" + WARNING (уже есть warning в _load_defaults — поднять до ERROR? Нет: warning + "" — fail-visible достаточно, лог уже громкий).

**3. Path-резолвинг (70-87):** удалить цикл cwd → parent ×4. Новый порядок: (1) `PLATFORM_ROOT` env → `Path(PLATFORM_ROOT)/platform-env.yaml`; (2) script-relative: `Path(__file__).resolve().parent.parent.parent.parent.parent / "platform-env.yaml"` (core/ → корень репо) — уже есть ветка (script_dir); (3) если ничего — warning + "". Для тестов: параметр `config_path` в `_load_defaults` НЕ добавляем — тесты используют monkeypatch PLATFORM_ROOT/tmp_path (Zero Hardcode Rule).

**4. Докстринг MODULE_CONTRACT:** инвариант «fallback'и, идентичные platform-infra.yaml» → «литеральных fallback'ов нет (D2, fail-visible — консистентно с D4 B6)».

**5. Consumer-scan:** docker_orchestrator:447, agent_watchdog:455/931 (default_context → "" при отсутствии файла — уже работает после D4 B6), s3_ssl_cache/backup_config (default_s3_* → "" → graceful sentinel-логика там уже есть? ПРОВЕРИТЬ: если потребитель ломается на "" — добавить явную проверку на пустоту с ERROR; consumer-scan обязателен).

**Критерий приёмки:** rg `_FALLBACK_|fallback` в platform_config → 0 (кроме докстринга); unit-тесты platform_config обновлены (существующие тесты с fallback-ожиданиями — переписать на ""-семантику).

---

### T9 — U-14: lib/healthcheck.sh + context_deployer

**1. `lib/healthcheck.sh check_docker_health` (179-213):** унификация критерия (D5):
```bash
case "${health_status}" in
    "healthy")          return 0 ;;
    "unhealthy")        return 1 ;;
    "starting")         return 2 ;;
    ""|"none")          # нет healthcheck — здоров только если running
        state="$(docker inspect --format='{{.State.Status}}' "${container_id}" 2>/dev/null)"
        if [ "${state}" = "running" ]; then return 0; fi
        return 2 ;;
esac
```
Докстринг @return обновить: «0 — healthy или running-без-healthcheck».

**2. context_deployer._is_project_healthy (464-466):** уже тонкая обёртка — проверить, что после переработки shared healthcheck_poll сигнатура не сломала вызов (timeout=10/interval=1 — параметры сохраняются; константы HEALTH_GATE_TIMEOUT=60 рядом — заменить на HEALTHCHECK_POLL_TIMEOUT? HEALTH_GATE_TIMEOUT используется где-то ещё? Consumer-scan: если только для обёртки — заменить на timeouts.HEALTHCHECK_POLL_TIMEOUT).

**3. Parity-гейт:** семантика shell-фасада проверяется статически? НЕЛЬЗЯ проверить семантику inspect-парсинга статически — вместо этого гейт «healthcheck-критерий в Python только в shared» (см. T10.2).

**Критерий приёмки:** lib/healthcheck.sh — inspect State.Health.Status + running-без-healthcheck → 0; make healthcheck (локальный) не регрессирует (см. самоверификацию).

---

### T10 — U-63 + гейты B5 (trinity)

**1. U-63: postgres/docker-compose.base.yml:71-77** — `interval: 10s` → `interval: 15s` (класс критичных данных). Остальные модули уже в классах (проверено аудитом: clickhouse/minio/langfuse/litellm/hermes-agent=15s ✓, redis/nginx/status-page/monitoring/logging/infra-metrics=30s ✓, backup-cron=60s ✓).

**2. Новые гейты (файлы в tests/gates/, @pytest.mark.gate, регистрация в entrypoint-manifest — auto-discovered G3):**

| Файл | Проверяет |
|------|-----------|
| `test_gate_docker_sole_path.py` | AST-скан всех core/internal/*.py: subprocess.run, где cmd содержит "docker"+"compose" (список `["docker", "compose", ...]` или `"docker compose"` строка) → РАЗРЕШЕНО только в shared/docker_compose.py. allowlist: entrypoints/shell вне скоупа. Комментарии/строки докстринга исключаются (только AST-узлы вызовов) |
| `test_gate_ssh_opts_sole_path.py` | (а) rg «Mirror lib/ssh.sh» по core/ → 0; (б) AST: списки, содержащие "-o"+"BatchMode=yes" (или "ConnectTimeout=" в ssh-контексте) вне shared/ssh_opts.py → RED (allowlist: context_promoter github-probe — только флаг ConnectTimeout, проверять как «ssh -T» команду); (в) `ConnectTimeout=` литералы → только ssh_opts.py и context_promoter:74 (после миграции — литерал 30 там? Нет — context_promoter импортирует SSH_CONNECT_TIMEOUT → гейт: ConnectTimeout= литерал → только ssh_opts.py) |
| `test_gate_timeout_literals.py` | AST: `timeout=` с int-литералом ∈ {30,60,120,180,300,600} в core/internal → RED, если в docker/ssh/healthcheck-домене (файлы: docker_orchestrator, deploy_engine, reconciler, channels, context_deployer, remote_executor, core_deliverer, overlay_deliverer, healthcheck_poller, docker_compose, context_promoter, vps_readiness, deploy/*, bootstrap/deploy/*, converge/*). allowlist: state_machine.py (до B9, D3), HTTP/S3-домены (healthcheck_poller HTTP-часть, s3_ssl_cache, backup_config, cert_orchestrator HTTP, template_engine, monitor-скрипты) — allowlist константой в файле гейта с комментарием «сжимается волнами». Гейт RED-ит ТОЛЬКО docker/ssh/healthcheck-домен (скоуп волны), остальное — allowlist |
| `test_gate_healthcheck_intervals.py` | Чтение core/modules/*/docker-compose.base.yml: каждый `healthcheck.interval` ∈ классификации: критичные = 15s, сервисы = 30s, фоновые = 60s. Классификация — константа-словарь в гейте (SoT). postgres 10s → RED (после фикса — PASS) |

**3. Регистрация:** `make fix-gate` (generate-entrypoint-manifest пересоберёт секцию gates — auto-discovered) + `make check-manifests` PASS. ВАЖНО: новые гейты с @pytest.mark.gate попадают в `make gate` автоматически (прецедент B6 D6 — make-обёртки не нужны).

**Критерий приёмки:** 4 новых гейт-файла в tests/gates/ + строки в entrypoint-manifest.yaml gates: (auto-discovered); `pytest tests/gates/ -m gate -k "docker_sole_path or ssh_opts or timeout_literals or healthcheck_intervals"` → PASS.

---

### T11 — Самоверификация волны (порядок)

1. `make fix-gate && git add -u` (exec-bit, ruff, manifest regen).
2. `pytest tests/unit/test_shared_docker_compose.py tests/unit/test_shared_ssh_opts.py tests/unit/test_shared_timeouts.py tests/unit/test_platform_config.py` → PASS.
3. Новые гейты: `pytest tests/gates/ -m gate -k "docker_sole_path or ssh_opts or timeout_literals or healthcheck_intervals"` → PASS.
4. Sole-path grep: `rg "docker compose" core/internal --glob '*.py'` → только shared/docker_compose.py (+ 0 в subprocess-вызовах других файлов); `rg "Mirror lib/ssh.sh"` → 0.
5. `make gate MODE=fast` → зелёный.
6. Обновить shared/AGENTS.md инвентарь: +timeouts.py, +ssh_opts.py (таблица, правила — оба модуля имеют ≥2 потребителей); root AGENTS.md §New shared modules (086) — +2 строки.
7. Обновить core/AGENTS.md? Только если canonical table меняется — НЕТ (гейты без make-таргетов). TRAP[DECISION] в root AGENTS.md: фиксация D1 (SSH_OPTS Python SoT — пересмотр TRAP[DECISION] vps_readiness:37-42) и D5 (healthcheck-критерий канон) — краткая запись по формату TRAP.
8. Плановые артефакты: 15-DevPlan.md (этот файл); после реализации — VerificationReport (следующий NN).

---

## 4. Риски и решения

| Риск | Митигация |
|------|-----------|
| lib/ssh.sh без python3 на ранних стадиях bootstrap (install-docker до python) | ssh.sh source-ится в 3 местах; все — после установки python (bootstrap ставит python рано). Добавлен fail-fast: python3 недоступен → return 1 с IMP:10 |
| DeployEngine env IMAGE_TAG: shared-функции работают через cwd+env | `env_override` параметр — полная замена os.environ? НЕТ: `env = {**os.environ, **(env_override or {})}` — копия + override (не ломает COMPOSE_PROFILES-экспорты) |
| Reconciler compose_file → compose_dir=parent: файлы могут лежать вне каталога модуля | docker compose -f работает с любым путём; cwd=parent безопасен (относительные пути в compose резолвятся от compose-файла, не cwd — проверить на реальных модулях) |
| audit-трейл теряет TIMEOUT/ERROR различение | D6 задокументирован: shared логирует детали (IMP:10 timeout/fail), audit пишет FAILED |
| Гейт timeout-литералов — ложные срабатывания на HTTP/S3 | allowlist константой в гейте (явный список файлов вне скоупа), сжимается волнами |
| Изменение поведения: pull timeout 120→300, up 30→180 (reconciler), rate-limit-детекция удаляется | Поведенческие изменения задокументированы (D8 + T5.1); все в сторону увеличения надёжности/стандартизации; greenfield (инвариант 9) — сервер пересоздаётся |

---

## 5. Критерии завершения волны (AC брифа 04-Brief)

1. ✅ docker compose up/pull — одна реализация (shared/docker_compose.py), 4 копии удалены (T4-T6); флаги/timeouts едины.
2. ✅ retry_pull — одна реализация с backoff [5,10,20] (shared), docker_orchestrator + deploy_engine подключены (T4.5, T5.1).
3. ✅ healthcheck — единый критерий «здоров» (inspect State.Health, running-без-healthcheck=здоров), 5 реализаций → 1 + тонкие обёртки (T3.4, T5.3, T9).
4. ✅ SSH_OPTS — одна константа (shared/ssh_opts.py), 5 Python-потребителей импортируют; ConnectTimeout единый 30 (T2).
5. ✅ shared/timeouts.py: COMPOSE_UP_TIMEOUT=180/PULL_TIMEOUT=300/SSH_CONNECT_TIMEOUT=30/HEALTHCHECK_POLL_TIMEOUT=60 — литералы в docker/ssh/healthcheck-домене заменены + гейт (T1, T10.2).
6. ✅ platform_config: fallback-константы удалены, чтение platform-env.yaml без cwd-эвристики (T8).
7. ✅ интервалы healthcheck: классы 15/30/60 + гейт (T10.1-2).

Гейт самоверификации волны: `make gate MODE=fast` зелёный + 4 новых гейта PASS + sole-path grep-критерии (T11).
