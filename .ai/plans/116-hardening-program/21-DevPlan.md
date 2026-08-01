# 21-DevPlan — B3: Нода и метрики (greenfield-развёртывание)

<!-- GREP_SUMMARY: metrics-cron install_cron_metrics platform-metrics phases φ3 node-detect NODE_NAME prometheus.yml.tmpl volumes-SoT driver_opts CONTEXT_IMAGE ci_deploy_key node.yaml ghcr L1-public hermes-agent-base tag-policy minio-data langfuse-data bootstrap.sh batch node_yaml -->
<!-- STRUCTURE: ┌решения D1-D4┐ → ◇ T1 metrics cron φ3 → ◇ T2 NODE-детекция → ◇ T3 prometheus → ◇ T4 volumes SoT → ◇ T5 bootstrap batch → ◇ T6 ci_deploy_key → ◇ T7 ghcr L1-public → ◇ T8 bind-mounts → ◇ T9 манифесты → ⊕ T10 самоверификация -->
# region MODULE_CONTRACT
## @purpose  Волна B3 программы хардненинга (116): инфраструктура ноды для greenfield-развёртывания — metrics cron в φ3, единая NODE-детекция, устранение дублей prometheus.yml, консолидация volume-деклараций, batch-рефакторинг bootstrap.sh, ci_deploy_key SoT, ghcr L1-public + tag-политика, удаление мёртвых bind-mounts.
## @scope    U-03, U-38, U-48, U-49, U-52, U-53, U-60, U-67. Файлы: core/internal/bootstrap/lifecycle/{phases.py,helpers/system.py}, core/internal/healthcheck/platform-export-metrics.sh, core/internal/shared/{node_detect.py,node_yaml.py}, .github/workflows/{core-deploy,build-hermes,build-platform}.yml, core/modules/monitoring/{config/prometheus.yml(удаляется),config/prometheus.yml.tmpl,docker-compose.base.yml}, docker-compose.yml, docker-compose.macos.yml, docker-compose.platform-dev.yml, core/modules/{postgres,backup-cron,hermes-agent,minio,langfuse,monitoring}/docker-compose.base.yml, core/entrypoints/bootstrap.sh, core/platform-infra.yaml, platform-env.yaml (generated), core/modules/{minio,langfuse}/module.yaml, core/internal/bootstrap/deploy/spool_validator.py, core/internal/bootstrap/setup-node.sh, core/modules/hermes-agent/{build/Dockerfile,context/Dockerfile,docker-compose.base.yml}, core/entrypoint-manifest.yaml, core/AGENTS.md (generated), AGENTS.md (root), tests/.
## @invariants
##   1. Greenfield: сервер переустанавливается — все исправления применяются к чистому развёртыванию, без миграций (инвариант брифа).
##   2. Документация (docstring/gate-комментарии) не обещает того, чего нет в коде — каждый gate-тест волны проверяет КОД (код-присутствие), не комментарий.
##   3. Python-first: новая бизнес-логика — Python (phases.py/helpers, node_yaml CLI batch); shell — тонкие фасады.
##   4. Generated files (platform-env.yaml, core/AGENTS.md, entrypoint-manifest.yaml) — только через генераторы, не вручную.
##   5. Каждое удаление — consumer-scan (правило B8): rg по репо + тесты + CI + манифесты перед удалением.
##   6. tag-политика ghcr: релизы = версионный тег vYYYY.M.D, прод-дефолты = digest-pin (tag@sha256), :latest — только dev/test-оверрайды; гейт на форму тега.
## @rationale Бриф 10-Brief фиксирует цели (U-03..U-67); DevPlan фиксирует решения пользователя (D1-D4, 2026-08-01) и исполнительные шаги с точными файлами, чтобы Coder работал без архитектурных развилок. Подтверждённые факты: phases.py:37 docstring обещает metrics cron — кода нет; test_gate_status_page.py:275-291 утверждает установку cron бутстрапом — установщика нет; platform-export-metrics.sh:31-33 исключает только secrets (scripts попадёт в NODE_NAME); prometheus.yml ≡ .tmpl (md5 792507d7ab16a1692f9f25bba79ebc7d), рендерер реальный (sed LITELLM_MASTER_KEY, monitoring/base.yml:54-68); docker compose config: driver_opts модулей МЕРЖАТСЯ в root (bind эффективен) — root SoT безопасен; base.yml:66 НЕ передаёт CONTEXT_IMAGE в environment контейнера (платформенный dev-оверрайд `CONTEXT_IMAGE: ""` — единственный путь env в контейнер); bootstrap.sh 178 LOC, 6 per-field --get; build-hermes.yml пушит L2 в tronyx161 — никто не тянет (runtime = tronyxlab); build-platform.yml (контексты) собирает L1 из исходников — дублирование операций; L1 не содержит секретов (TRAP 2026-07-15) — публикация пакета безопасна; /var/lib/platform/{minio,langfuse}-data не используются контейнерами (minio-data/langfuse-redis-data — docker-тома driver:local).
## @changes 2026-08-01 · Решения пользователя (question 2026-08-01): (D1) ghcr — публичный L1-пакет на tronyx161 (private org/repo, public package), L2-push из build-hermes.yml удаляется, контексты тянут L1 анонимно; (D2) ci_deploy_key — node.yaml единственный источник, env-override удаляется; (D3) U-67 — мёртвые host-пути minio/langfuse удаляются (не монтируются); (D4) CONTEXT_IMAGE: "" — механизм заменяется явным image-оверрайдом (env-запись удаляется).
## @changes  SUPERSEDED 2026-08-01 — закрыт волнами 116; VR не требуется (D5, DevPlan 116 B11 T8 U-84) — 21-DevPlan.md
# endregion MODULE_CONTRACT

$START_DEVPLAN
$ARTIFACT_CONTRACT:
  PURPOSE: Реализация волны B3 — работающий мониторинг и корректная инфраструктура на переустановленной ноде: metrics cron в φ3, единая NODE-детекция, один prometheus-конфиг, один SoT volume-деклараций, batch bootstrap.sh ≤150 LOC, ci_deploy_key из node.yaml, ghcr L1-public + единая tag-политика, чистка мёртвых bind-mounts.
  DESCRIPTION: install_cron_metrics() в φ3 (cron.d/platform-metrics, flock + timeout 50s) с заменой gate-теста на проверку кода; NODE-детекция через канонический node_detect для 2 shell-потребителей; удаление prometheus.yml-дубля (рендерер реальный — остаётся .tmpl); консолидация volumes в root compose (driver_opts переносятся из модулей, модули снимают top-level volumes) + удаление CONTEXT_IMAGE: "" из platform-dev.yml; --get-many batch-режим node_yaml CLI + рефакторинг bootstrap.sh; ci_deploy_key — только node.yaml (env-override удаляется, TRAP[BUG] снимается); публичный L1-пакет ghcr.io/tronyx161/hermes-agent-base, L2-push из build-hermes.yml удаляется, build-platform.yml — контекст-зависимый pull/build, tag-политика + гейт формы тега; удаление /var/lib/platform/{minio,langfuse}-data из provision (spool_dir: none).
  RATIONALE: U-03: docstring и gate-тест утверждают установку metrics cron — кода нет, новая нода остаётся без метрик. U-38: 3 реализации NODE-детекции расходятся (metrics-скрипт не исключает scripts). U-48: md5-идентичные prometheus.yml/.tmpl при живом рендерере — дубль вводит в заблуждение. U-49: driver_opts дублируются в root и модулях (merge работает случайно), CONTEXT_IMAGE: "" — пустая строка как механизм. U-52: 6 per-field --get вызовов в bootstrap.sh. U-53: env-override против node.yaml SoT. U-60: L2-push в tronyx161 никто не тянет, L1 собирается в каждом контексте из исходников; цель — базовый L1 в одном месте, публичный для анонимного pull, 0 ключей в контекстных org. U-67: host-директории создаются, но не используются.
  ACCEPTANCE_CRITERIA: (1) metrics cron устанавливается в φ3 (cron.d/platform-metrics, flock + timeout 50s) — gate проверяет код-присутствие + контракт строки cron; e2e на переустановленном сервере (отсрочено); (2) prometheus.yml/.tmpl — один файл (.tmpl, рендерер реальный); (3) volume-декларации: root compose — единственный SoT (docker compose config не содержит driver_opts из модулей), macos-оверрайд cadvisor согласован, CONTEXT_IMAGE: "" отсутствует; (4) NODE-детекция: все потребители — node_detect (исключает scripts+secrets); (5) bootstrap.sh ≤ 150 LOC с одним batch-вызовом node_yaml; (6) ci_deploy_key — единственный источник node.yaml, env-override удалён, TRAP[BUG] снят; (7) ghcr: L1 публичный на tronyx161 (latest + sha-тег), L2-push из build-hermes.yml удалён, tag-форма едина (гейт), :latest только в dev/test allowlist; (8) /var/lib/platform/{minio,langfuse}-data удалены из platform-infra.yaml (+ регенерация platform-env.yaml), spool_dir: none.
  IMPLEMENTS: U-03, U-38, U-48, U-49, U-52, U-53, U-60, U-67
  IMPACTS: core/internal/bootstrap/lifecycle/{phases.py,helpers/system.py}, core/internal/healthcheck/platform-export-metrics.sh, core/internal/shared/{node_detect.py,node_yaml.py}, core/entrypoints/bootstrap.sh, core/internal/bootstrap/{setup-node.sh,deploy/spool_validator.py}, .github/workflows/{core-deploy,build-hermes,build-platform}.yml, docker-compose.yml, docker-compose.macos.yml, docker-compose.platform-dev.yml, core/modules/{monitoring,postgres,backup-cron,hermes-agent,minio,langfuse}/docker-compose.base.yml, core/modules/monitoring/config/prometheus.yml (удаляется), core/modules/hermes-agent/{build/Dockerfile,context/Dockerfile,docker-compose.base.yml}, core/modules/{minio,langfuse}/module.yaml, core/platform-infra.yaml, platform-env.yaml (generated), core/entrypoint-manifest.yaml, core/AGENTS.md (generated), AGENTS.md (root), tests/
  REQUIRES: 10-Brief (B3); решения пользователя 2026-08-01 (D1-D4); B1 (20-DevPlan — dispatch-канал, setup-node forced-command), B2 (03-DevPlan — паритет-гейты), B8 (17-DevPlan — гейт фантомов); чистое рабочее дерево на старте (пользователь коммитит перед началом)
$END_ARTIFACT_CONTRACT

---

## 1. Решения пользователя (подтверждены 2026-08-01)

| D | Вопрос | Решение |
|---|--------|---------|
| D1 | U-60: ghcr org/tag — супер-позиция доставки L1 | **Публичный L1-пакет на tronyx161 ghcr** (org/repo остаются приватными, публикуется только пакет hermes-agent-base — в нём нет секретов, только Python-зависимости). build-hermes.yml: L2 build+push УДАЛЯЕТСЯ (L2 — ответственность контекстных org), L1 пушится `:latest` + `sha-<sha>`. Контекстные org тянут L1 анонимно (0 ключей в чужих репо) и собирают только тонкий L2. mirror.yml не меняется (только репо). Цель «базовая платформа без контекста для тестирования»: L1 standalone (без CONTEXT-guard) — явная image-ссылка вместо пустой строки. |
| D2 | U-53: ci_deploy_key SoT | **node.yaml — единственный источник.** Env-override (PLATFORM_CI_DEPLOY_KEY > node.yaml) удаляется из bootstrap.sh; TRAP[BUG] 2026-07-17 снимается (fix подтверждён кодом). Канал доставки остаётся: bootstrap.sh (batch-экстракция) → node-lifecycle.sh --ci-deploy-key → PLATFORM_CI_DEPLOY_KEY env → setup-node.sh. |
| D3 | U-67: мёртвые host-пути minio/langfuse | **Удалить** /var/lib/platform/{minio,langfuse}-data из platform-infra.yaml (+ регенерация platform-env.yaml), spool_dir → `none` в module.yaml minio/langfuse. Данные живут в docker-томах (minio-data, langfuse-redis-data) — поведение не меняется. |
| D4 | U-49: CONTEXT_IMAGE: "" в platform-dev.yml | **Удалить env-запись** `environment: CONTEXT_IMAGE: ""`. Механизм L1-режима = явный `image: hermes-agent-base:latest` (уже есть в оверрайде). Пустых строк нет; base.yml не передаёт CONTEXT_IMAGE в environment контейнера — удаление не влияет на контейнер. |

## 2. Текущее состояние worktree (старт волны)

- B1-реализация (20-DevPlan) — в незакоммиченном дереве (workflow/манифесты/setup-node/dispatch-файлы M/D); **пользователь коммитит перед стартом волны**.
- `phases.py:37` — modulemap φ3 «docker auth, **metrics cron**, setup-node»; `phase_platform_setup` (292-348) НЕ содержит установки cron (U-03). Установщик cron отсутствует во всём core (grep: только install-tor-proxy.sh:324 `install_cron_healthcheck` — образец паттерна /etc/cron.d/).
- `tests/gates/test_gate_status_page.py:270-291` — TestGateStatusPageCrontabContract: docstring «installed by node-lifecycle.sh bootstrap» — установщика нет (U-03, gate «врёт»).
- `platform-export-metrics.sh:31-33` — `ls /opt/node-configs/ | grep -v secrets | head -1` — НЕ исключает `scripts` (U-38); `core-deploy.yml:169-171` — for-loop с исключением scripts+secrets (дубль); `node_detect.py` `auto_detect_node_name` (SKIP_DIRS = scripts+secrets) — каноничен, потребители: bootstrap.sh (уже), metrics-wrapper и CI — нет.
- `prometheus.yml` ≡ `prometheus.yml.tmpl` (md5 792507d7..., 146 строк, U-48); рендерер РЕАЛЬНЫЙ: `monitoring/docker-compose.base.yml:54-68` prometheus-config-init `sed 's/$${LITELLM_MASTER_KEY}/.../' /config/prometheus.yml.tmpl > /generated/prometheus.yml`; потребители prometheus.yml (не .tmpl): `tests/gates/test_p20_container_coupling.py:31` (PROMETHEUS_YML); test_gate_env_chain.py уже читает .tmpl.
- `docker-compose.yml:43-53` — 10 volumes `driver: local`; модули: postgres:117,124, backup-cron:103,110, hermes-agent:190 — `driver_opts` bind (type:none, o:bind, device:/var/lib/platform/...); langfuse-redis-data и prometheus-config-gen объявлены ТОЛЬКО в модулях; `docker compose config` (проверено): driver_opts МЕРЖАТСЯ в root-объявления (эффективный конфиг уже bind) — консолидация в root безопасна, поведение на mac не меняется.
- `docker-compose.macos.yml:23` — cadvisor `!override` (4 mount, docker.sock из ${HOME}); cadvisor — реальный сервис (infra-metrics, profiles: [infra-metrics], COMPOSE_PROFILES включает infra-metrics); Makefile:35-38 подключает macos.yml на darwin — оверрайд ЖИВОЙ, удалению не подлежит (согласуется с базой).
- `docker-compose.platform-dev.yml:34` — `CONTEXT_IMAGE: ""` (U-49); `hermes-agent/base.yml:66` — `image: ${CONTEXT_IMAGE:-ghcr.io/tronyxlab/hermes-agent-context:latest@sha256:dd36a...}`; base.yml НЕ объявляет CONTEXT_IMAGE в environment — пустая строка попадает в контейнер ТОЛЬКО через platform-dev.yml.
- `bootstrap.sh` — 178 LOC; per-field `--get` ×6 (строки 86-124): node.owner_key, node.ci_deploy_key, domain, context, contexts.0.name + --detect-age-key (U-52); `node_yaml.py` CLI: single `--get`/`--default`, `--json-output` есть, batch-режима НЕТ.
- `bootstrap.sh:105-109` — env-override `PLATFORM_CI_DEPLOY_KEY > node.yaml` (U-53); TRAP[BUG] 91-102 — «fix applied», но env-приоритет остался.
- `build-hermes.yml:44-46` — REGISTRY ghcr.io, L1_IMAGE/L2_IMAGE = tronyx161; L2 push в tronyx161 никто не тянет (U-60); `build-platform.yml` (зеркалится в контексты) строит L1 из исходников (`load:true`, build/Dockerfile) + smoke + push в свой org — дублирование L1-build в каждом контексте; `context/Dockerfile` — `FROM hermes-agent-base:latest` (локальный L1); L1 не содержит секретов (TRAP 2026-07-15: only Python dependencies).
- Формы тегов CONTEXT_IMAGE: platform-infra.yaml:144 `v2026.7.1` (tronyxlab), smoke.py:116 `:latest` (tronyxlab), base.yml:66 `tag@sha256` (tronyxlab) — 3 формы (U-60).
- `platform-infra.yaml:71,76` + `platform-env.yaml:56,59` — /var/lib/platform/{langfuse,minio}-data (U-67); minio compose: `minio-data:/data` (docker-том driver:local, БЕЗ bind), langfuse compose: `langfuse-redis-data` (driver:local); module.yaml minio/langfuse: `spool_dir: /var/lib/platform/{minio,langfuse}-data` — spool_validator проверяет host-директории (схема module.schema.json: spool_dir: `none` допустим).

## 3. Задачи

### T1 — U-03: install_cron_metrics в φ3 + gate на код, не комментарий [CRITICAL]

**Файлы:** `core/internal/bootstrap/lifecycle/phases.py`, `core/internal/bootstrap/lifecycle/helpers/system.py`, `tests/gates/test_gate_status_page.py` (расширение), `tests/unit/test_phase_metrics_cron.py` (новый), `core/internal/bootstrap/AGENTS.md` (φ3-описание), `core/entrypoint-manifest.yaml` (gates-запись — T9)

**Шаги:**

1. **helpers/system.py** — новая функция `install_cron_metrics(core_dir: str) -> bool` (Python, LDD IMP:7-10):
   - Константа `CRON_METRICS_FILE = "/etc/cron.d/platform-metrics"` и `CRON_METRICS_LINE = "* * * * * root /usr/bin/flock -n /run/lock/platform-metrics.lock /usr/bin/timeout 50 {core_dir}/internal/healthcheck/platform-export-metrics.sh >/dev/null 2>&1"` (абсолютные пути — cron.d работает с минимальным PATH; контракт брифа: flock + timeout 50s).
   - Идемпотентность: читать существующий файл → content match → SKIP (no-op); иначе temp file → сравнение/запись → `mv` (атомарно), `chmod 0644`; mkdir `/run/lock` best-effort (tmpfs, на Ubuntu существует).
   - Валидация после записи: `cron`/`crontab` не требуется — cron.d читается демоном; лог IMP:9 «cron installed», IMP:7 при отличии (перезапись).
2. **phases.py — phase_platform_setup (φ3)**: добавить шаг 2.5 «metrics cron» ПОСЛЕ setup-node (шаг 2) и ДО валидации sudoers (шаг 3): `helpers_system.install_cron_metrics(core_dir)`; нефатально (WARN при сбое, non_fatal_issues = True — контракт фазы); modulemap-комментарий :37 уже корректен («docker auth, metrics cron, setup-node») — docstring начинает соответствовать коду.
3. **Гейт** `tests/gates/test_gate_status_page.py` — TestGateStatusPageCrontabContract:
   - существующие 2 теста (backup-cron crontab НЕ содержит metrics-строки) — ОСТАЮТСЯ;
   - НОВЫЙ тест (код-присутствие, инвариант 2): импорт `phases.py` → AST-или текстовая проверка, что `phase_platform_setup` вызывает `install_cron_metrics` (не docstring!); импорт `helpers/system.py` → `CRON_METRICS_LINE` содержит `flock -n` И `timeout 50` И путь `platform-export-metrics.sh`; docstring теста переписывается без «installed by node-lifecycle.sh»-претензии — теперь утверждение проверяется кодом.
4. **Unit** `tests/unit/test_phase_metrics_cron.py` (native, tmp_path, без root):
   - `install_cron_metrics` c tmp_path core_dir → файл записан, content содержит flock+timeout+скрипт; повторный вызов → файл не переписывается (mtime/content-idempotency); изменение контента (мутация) → перезапись;
   - monkeypatch `os.path.exists`/запись для non-root: вызов НЕ роняет фазу (False + WARN-лог, IMP:9-assert);
   - LDD: caplog → фильтр IMP:7-10 печать перед assert.

**Критерий:** φ3 выполняет install_cron_metrics; `CRON_METRICS_LINE` = контракт (flock + timeout 50 + absolute path); gate проверяет КОД; `pytest tests/unit/test_phase_metrics_cron.py tests/gates/test_gate_status_page.py` зелёные.

### T2 — U-38: единая NODE-детекция (node_detect) для всех потребителей [FUNDAMENT]

**Файлы:** `core/internal/healthcheck/platform-export-metrics.sh`, `.github/workflows/core-deploy.yml`, `core/internal/shared/node_detect.py` (комментарии), `core/internal/healthcheck/platform_export_metrics.py` (docstring), `tests/unit/test_node_detect.py` (расширение)

**Шаги:**

1. **platform-export-metrics.sh:31-33** — заменить `ls ... | grep -v secrets | head -1` на:
   ```bash
   NODE_NAME=$(python3 -m core.internal.shared.node_detect --detect-node-name 2>/dev/null) || NODE_NAME="unknown"
   ```
   Приоритет: явный `NODE_NAME` env (строка 31, оставить) → node_detect → "unknown" (WARN). Убрать `grep -v secrets`-хак (node_detect исключает scripts+secrets). @changes-комментарий.
2. **core-deploy.yml:169-171** — заменить for-loop на ssh-вызов канонического детектора на VPS:
   ```bash
   NODE=$(ssh -o ConnectTimeout=10 ${{ env.VPS_USER }}@${{ secrets.VPS_HOST }} \
     'cd /opt/platform && python3 -m core.internal.shared.node_detect --detect-node-name' 2>/dev/null) || NODE=""
   ```
   (python3 = 3.14 после φ1, core в /opt/platform; ошибка детекции → прежний `::error::` + exit 1). Комментарий «same logic as converge.sh» актуализируется.
3. **node_detect.py** — MODULE_CONTRACT @scope: добавить потребителей (metrics-wrapper, core-deploy CI) в перечень; функция `auto_detect_node_name` не меняется (канон уже корректен).
4. **platform_export_metrics.py:63** `_get_node_name` — docstring: «NODE_NAME приходит от wrapper (node_detect); env-fallback "unknown" — только для ручного запуска» (детекция — обязанность wrapper'а, одна реализация).
5. **Тесты** (расширение test_node_detect.py): `auto_detect_node_name` с фикстурой tmp_path: dirs {app, scripts, secrets} → "app" (scripts/secrets исключены); пусто → NodeDetectionError; wrapper-логика: потребительские команды не тестируются (CI-шаг), но контракт CLI `--detect-node-name` уже покрыт.

**Критерий:** `rg "grep -v secrets|for d in /opt/node-configs"` по core/ и .github/ = 0 (кроме исторических комментариев); ровно один детектор имени ноды — node_detect; `pytest tests/unit/test_node_detect.py` зелёный.

### T3 — U-48: prometheus — один файл (.tmpl остаётся, рендерер реальный) [FUNDAMENT]

**Файлы:** `core/modules/monitoring/config/prometheus.yml` (удаляется), `tests/gates/test_p20_container_coupling.py`, `core/modules/monitoring/docker-compose.base.yml` (комментарий prometheus-config-init), `tests/gates/test_gate_env_chain.py` (проверка, без изменений)

**Шаги:**

1. **Решение:** рендерер реальный (prometheus-config-init: sed LITELLM_MASTER_KEY из .tmpl → /generated/prometheus.yml) → источник истины = `prometheus.yml.tmpl`; дубль `prometheus.yml` (md5-идентичный) УДАЛЯЕТСЯ.
2. **Consumer-scan** (правило инварианта 5): `rg "config/prometheus.yml"` → monitoring/base.yml (только .tmpl — ок), `tests/gates/test_p20_container_coupling.py:31` — `PROMETHEUS_YML` → `PROMETHEUS_YML_TMPL = PROJECT_ROOT / "core" / "modules" / "monitoring" / "config" / "prometheus.yml.tmpl"`; docstring «parses prometheus.yml» актуализировать. test_gate_env_chain.py:29 уже читает .tmpl — без изменений.
3. **monitoring/base.yml:54-68** — комментарий prometheus-config-init: «template is the single source (prometheus.yml removed, DevPlan 116 B3)»; сама команда не меняется.
4. Гейт-защита от возврата дубля: в test_gate_env_chain.py (или test_gate_status_page.py) — assert `prometheus.yml` (без .tmpl) НЕ существует в config/ (код-присутствие отрицания).

**Критерий:** `rg "config/prometheus.yml"` (без .tmpl) = 0 в core/ и tests/ (кроме отрицания в гейте); `docker compose config` для monitoring-профиля зелёный; тесты p20/env_chain зелёные.

### T4 — U-49: volumes — root compose единственный SoT + CONTEXT_IMAGE без пустой строки [CRITICAL]

**Файлы:** `docker-compose.yml`, `core/modules/{postgres,backup-cron,hermes-agent,minio,langfuse,monitoring}/docker-compose.base.yml`, `docker-compose.macos.yml` (комментарии), `docker-compose.platform-dev.yml`, `tests/gates/test_gate_volumes_sot.py` (новый), `tests/gates/test_gate_profiles_parity.py` (проверка), `tests/` (docker-compose.test.yml — проверить зависимость от модульных volumes)

**Шаги:**

1. **Root `docker-compose.yml` volumes — полный SoT** (12 имён):
   - bind-тома с driver_opts (перенос из модулей postgres/backup-cron/hermes-agent): `postgres-data`, `wal-archive` (device /var/lib/platform/postgres-data, /var/lib/platform/wal-archive), `backup-spool` (+ app-data? — проверить фактический device: /var/lib/platform/backup-spool), `backup-logs` (/var/log/platform/backup), `hermes-data` (/var/lib/platform/hermes-agent/data) — `driver: local` + `driver_opts: {type: none, o: bind, device: <путь>}`;
   - docker-managed (остаются `driver: local` без opts): `grafana-data`, `prometheus-data`, `loki-data`, `clickhouse-data`, `minio-data`, `langfuse-redis-data`, `prometheus-config-gen` (два последних — сейчас объявлены только в модулях → ДОБАВЛЯЮТСЯ в root);
   - STRUCTURE-комментарий :2/:10 — «12 volumes» актуализировать.
2. **Модульные compose-файлы** — удалить top-level `volumes:` секции: postgres (117-126), backup-cron (103-113), hermes-agent (190-195), minio (100-102), langfuse (162-164), monitoring (prometheus-config-gen); сервисные `volumes: [ - postgres-data:/var/lib/postgresql/data ]` (mount-ссылки) ОСТАЮТСЯ.
3. **Проверка merge** (обязательная, была предпроверена): `docker compose config` → volumes содержат driver_opts ровно из root; `docker compose config --profiles` для каждого профиля — сервисы видят свои тома; docker-compose.test.yml модулей (postgres/backup-cron) — проверяются: они переименовывают тома (-test) — конфликтов с root нет, править только при падении.
4. **docker-compose.macos.yml** — НЕ удаляется (cadvisor — реальный сервис, Makefile:37-38 подключает на darwin); обновить комментарии: «consolidated volumes live in root compose (D3/B3)»; убедиться, что `!override` cadvisor не конфликтует (базовые 4 mount совпадают с override по составу, кроме socket).
5. **docker-compose.platform-dev.yml (D4)** — удалить `environment: CONTEXT_IMAGE: ""`; оставить `image: hermes-agent-base:latest`; TRAP[DECISION] 2026-07-09 переписать: «механизм L1 = явный image-оверрайд; пустая строка запрещена (DevPlan 116 B3 D4)».
6. **Гейт** `tests/gates/test_gate_volumes_sot.py` (@pytest.mark.gate + manifest-gates, trinity):
   - парсинг root docker-compose.yml: volumes-ключи == объединение (ключи модульных top-level volumes = ∅);
   - парсинг модульных base.yml: top-level `volumes:` секция отсутствует (или содержит только `-test`/не-root имена);
   - негатив (R5): если модуль объявит volume-имя, пересекающееся с root → RED.
   - `CONTEXT_IMAGE: ""`-запрет: regex-скан docker-compose*.yml (root/macos/platform-dev) → `CONTEXT_IMAGE: ""` = 0 вхождений (D4).

**Критерий:** `docker compose config --volumes` = ровно root-декларации; `rg "driver_opts" core/modules/*/docker-compose.base.yml` = 0 (все в root); `rg 'CONTEXT_IMAGE: ""'` = 0; гейт volumes_sot зелёный.

### T5 — U-52: bootstrap.sh — batch node_yaml (один вызов) + ≤150 LOC [FUNDAMENT]

**Файлы:** `core/internal/shared/node_yaml.py` (CLI --get-many), `core/entrypoints/bootstrap.sh`, `tests/unit/test_node_yaml_cli_get_many.py` (новый), `tests/unit/test_bootstrap_batch.py` (новый/расширение), `core/internal/shared/AGENTS.md` (инвентарь — нет, модуль существует)

**Шаги:**

1. **node_yaml.py CLI** — новый флаг `--get-many <spec>` (batch, один python3-процесс):
   - формат: `--get-many owner_key:node.owner_key,ci_deploy_key:node.ci_deploy_key,platform_domain:domain,context:context,context0:contexts.0.name` (alias:dotted-key пары, через запятую);
   - вывод: строки `alias<TAB>value` (TAB-разделитель — значения могут содержать пробелы/`=`); отсутствующий ключ → строка `alias<TAB>` (пустое значение, exit 0 — shell-совместимость, как текущий --default "");
   - `--default` взаимодействие: значение по умолчанию пустое для всех ключей; валидация формата spec (пустой spec → ConfigValidationError, exit 4); @changes в MODULE_CONTRACT.
2. **bootstrap.sh** — рефакторинг main():
   - ОДИН вызов: `BATCH_OUTPUT="$(python3 -m core.internal.shared.node_yaml --file "${NODE_YAML}" --get-many owner_key:node.owner_key,ci_deploy_key:node.ci_deploy_key,platform_domain:domain,context:context,context0:contexts.0.name 2>/dev/null)"` → while-read по TAB: `OWNER_KEY`, `CI_DEPLOY_KEY`, `PLATFORM_DOMAIN`, `CONTEXT` (context0 как fallback: если CONTEXT пуст → CONTEXT=context0; приоритет context > contexts.0.name, как сейчас);
   - env-override для CI_DEPLOY_KEY УДАЛЯЕТСЯ (D2/T6) — batch содержит только node.yaml;
   - все существующие проверки (FATAL при отсутствии owner_key и т.д.) сохраняются; итоговая цель: файл ≤ 150 LOC (сейчас 178 — сокращение за счёт консолидации 6 вызовов в 1 + удаление env-ветки);
   - @changes/STRUCTURE-комментарий: «batch --get-many (DevPlan 116 B3 T5)».
3. **Тесты**:
   - `test_node_yaml_cli_get_many.py`: tmp_path node.yaml фикстура → `--get-many` возвращает правильные alias/value (TAB); отсутствующий ключ → пустая строка exit 0; битый spec → exit 4; `context` fallback-приоритет (context присутствует vs contexts.0.name);
   - `test_bootstrap_batch.py`: парсинг bootstrap.sh — рецепт main() содержит ровно 1 `--get-many` и 0 отдельных `--get` (текстовый assert, код-присутствие); LOC-метрика: bootstrap.sh ≤ 150 (парсинг файла).

**Критерий:** `rg "node_yaml --file.*--get " core/entrypoints/` = 0; bootstrap.sh ≤ 150 LOC; batch-вызов один; тесты зелёные.

### T6 — U-53: ci_deploy_key — node.yaml единственный источник [FUNDAMENT]

**Файлы:** `core/entrypoints/bootstrap.sh`, `core/internal/bootstrap/node-lifecycle.sh` (без изменений — канал env уже есть), `core/internal/bootstrap/setup-node.sh` (комментарии), `tests/unit/test_bootstrap_batch.py` (T5, негатив env-override)

**Шаги:**

1. **bootstrap.sh:105-109** — УДАЛИТЬ env-override-ветку (`if [[ -n "${PLATFORM_CI_DEPLOY_KEY:-}" ]] ... CI_DEPLOY_KEY="$PLATFORM_CI_DEPLOY_KEY"`) — node.yaml единственный источник (D2). Пустой ключ → прежнее поведение: WARN «ci-deploy restricted key setup will be skipped» (не FATAL).
2. **TRAP[BUG] 2026-07-17 (bootstrap.sh:91-102)** — переписать: fix применён в этой волне — «ci_deploy_key извлекается batch-вызовом node_yaml (T5); env-переопределение удалено (D2) — node.yaml SoT»; TRAP остаётся как исторический маркер с пометкой RESOLVED (не удалять полностью — B8-гейт допускает историю в TRAP).
3. **setup-node.sh:196,206-211** — комментарии: «PLATFORM_CI_DEPLOY_KEY приходит из node.yaml через bootstrap-канал (T5/T6)»; логика чтения env не меняется (канал доставки от node-lifecycle.sh).
4. **Гейт-тест** (в test_bootstrap_batch.py, R5-negative): текст bootstrap.sh НЕ содержит `PLATFORM_CI_DEPLOY_KEY` в ветке приоритета (regex: строка с `CI_DEPLOY_KEY="\$PLATFORM_CI_DEPLOY_KEY"` отсутствует); docstring теста ссылается на D2.

**Критерий:** `rg "PLATFORM_CI_DEPLOY_KEY" core/entrypoints/bootstrap.sh` = 0 (или только WARN-сообщение без присваивания приоритета); цепочка node.yaml → bootstrap → node-lifecycle → setup-node работает (unit-тест канала с инъекцией).

### T7 — U-60: ghcr — публичный L1 на tronyx161, L2-push удаляется, tag-политика + гейт [CRITICAL]

**Файлы:** `.github/workflows/build-hermes.yml`, `.github/workflows/build-platform.yml`, `core/modules/hermes-agent/context/Dockerfile`, `core/modules/hermes-agent/build/Dockerfile` (комментарии), `core/modules/hermes-agent/docker-compose.base.yml` (комментарии), `core/platform-infra.yaml` (env_defaults CONTEXT_IMAGE), `tests/_conftest/smoke.py` (allowlist-комментарий), `tests/gates/test_gate_image_tag_form.py` (новый), `AGENTS.md` (root TRAP 2026-07-15 + глоссарий hermes-push-l1), `core/AGENTS.md` (generated — T9), `core/internal/shared/AGENTS.md` (если упоминания)

**Шаги:**

1. **build-hermes.yml (D1)**:
   - УДАЛИТЬ L2 job/build+push (L2_IMAGE tronyx161, шаги docker/build-push-action для context/Dockerfile) — L2 — ответственность контекстных org (make hermes-build-context / их CI); consumer-scan: `L2_IMAGE`, `hermes-agent-context` в workflow — только в build-hermes.yml (проверить platform-deliver-цепочку B1 не ссылается);
   - L1 job: `tags: | ghcr.io/tronyx161/hermes-agent-base:latest ghcr.io/tronyx161/hermes-agent-base:sha-${GITHUB_SHA}` (short-sha или полный — полный предпочтителен для однозначности; константа sha — `${GITHUB_SHA}`);
   - @purpose/@invariants/@rationale: «L1 built ONCE in tronyx161 (public package — distribution base), L2 per-context».
2. **Публичность пакета — MANUAL-шаг (оператор, после merge)**: `gh package visibility set hermes-agent-base --visibility public` (или UI: Packages → hermes-agent-base → visibility → public) — задокументировать в workflow-комментарии + в AC волны (проверяется QA вручную; из CI не управляется). Логическое обоснование в комментарии: L1 без секретов (TRAP 2026-07-15), публикация = анонимный pull для контекстов.
3. **build-platform.yml** (зеркалится в контекстные org) — контекст-зависимый L1:
   - `if: github.repository_owner == 'tronyx161'` → текущий путь: build L1 из исходников (load:true) + smoke + push L1 в свой org (DR-бэкап — «одно место»);
   - `else` (контекстная org): шаг pull `docker pull ghcr.io/tronyx161/hermes-agent-base:latest` → `docker tag ... hermes-agent-base:latest` → smoke L1 (L1 standalone = «базовая платформа без контекста для тестирования»); фолбэк: если pull упал (registry недоступен) → build из исходников (существующий шаг, retry-логика);
   - комментарии M19/R6 «never used directly by contexts» актуализировать: контексты не РАБОТАЮТ на L1 как runtime (runtime = L2), L1 используется как BUILD-BASE и smoke-цель.
4. **context/Dockerfile** — `FROM hermes-agent-base:latest` (локальный тег) ОСТАЁТСЯ; @layer/@rationale: «L1 получается из ghcr.io/tronyx161/hermes-agent-base (public) и тегается локально перед сборкой L2 (см. build-platform.yml / make hermes-build-context)»; build/Dockerfile — заголовок: «pushed to ghcr.io/tronyx161 as public distribution base (DevPlan 116 B3 D1)».
5. **base.yml:66** — default `${CONTEXT_IMAGE:-ghcr.io/tronyxlab/hermes-agent-context:latest@sha256:...}` НЕ меняется (digest-pin — канон прод-дефолта); TRAP[PERF] 2026-07-18 актуализировать: «CI пушит versioned tags (см. tag-политику T7) — при появлении версии v2026.x.y переключить default на версионный тег@digest».
6. **tag-политика (единая форма)**:
   - релизы/прод-дефолты: `v<YYYY>.<M>.<D>` или `tag@sha256:<64hex>` — НИКОГДА голый `:latest`;
   - `:latest` — только dev/test: docker-compose.platform-dev.yml (`hermes-agent-base:latest`), smoke.py:116 (тест-фикстура), docker-compose.test.yml модулей;
   - platform-infra.yaml:144 CONTEXT_IMAGE default `ghcr.io/tronyxlab/hermes-agent-context:v2026.7.1` — форма корректна (версионный тег) — оставить.
7. **Гейт** `tests/gates/test_gate_image_tag_form.py` (@pytest.mark.gate + manifest-gates):
   - сканирует `core/platform-infra.yaml` env_defaults.CONTEXT_IMAGE + `core/modules/*/docker-compose.base.yml` image-ссылки на ghcr.io: матч `^ghcr\.io/[^/]+/[a-z0-9-]+(:v[0-9]+\.[0-9]+\.[0-9]+)?(@sha256:[a-f0-9]{64})?$` (версионный или digest-pin; голый `:latest` — RED);
   - allowlist `:latest`: docker-compose.platform-dev.yml, tests/_conftest/smoke.py, `docker-compose.test.yml` — вне скана (dev/test);
   - негатив (R5): инлайн-фикстура с `:latest` в base.yml-контексте → RED.
8. **AGENTS.md root**: TRAP[DECISION] 2026-07-15 «L1 pushed to ghcr.io as backup, never used directly by contexts» — ДОПОЛНИТЬ: «2026-08-01 (B3 D1): L1 публикуется (public package) — distribution base для контекстных L2-сборок и L1-только тестов; контексты не используют L1 как runtime»; глоссарий hermes-push-l1 — «Push L1 в ghcr.io как DR backup и дистрибутивную базу (public)»; hermes-push-l2 без изменений (контекстный глагол).

**Критерий:** build-hermes.yml = только L1 (latest + sha-тег); `rg "L2_IMAGE|hermes-agent-context" .github/workflows/build-hermes.yml` = 0; гейт tag-формы зелёный; manual-шаг публичности задокументирован в AC; контекстные CI: pull-L1-fallback-build.

### T8 — U-67: удаление мёртвых bind-mounts minio/langfuse [FUNDAMENT]

**Файлы:** `core/platform-infra.yaml` (volumes), `platform-env.yaml` (generated — регенерация), `core/modules/minio/module.yaml`, `core/modules/langfuse/module.yaml`, `core/internal/bootstrap/deploy/spool_validator.py` (комментарии/ожидания), `tests/unit/test_spool_validator.py` (расширение), `tests/_conftest/infra.py` (проверка volumes-чтения)

**Шаги:**

1. **platform-infra.yaml** — удалить из volumes: `- path: /var/lib/platform/langfuse-data` (:71), `- path: /var/lib/platform/minio-data` (:76). Consumer-scan: `rg "var/lib/platform/(minio|langfuse)-data"` → platform-env.yaml:56,59 (generated — регенерируется), module.yaml ×2 (шаг 2), spool_validator (шаг 3); контейнеры НЕ используют эти пути (проверено: minio-data/langfuse-redis-data — docker-тома).
2. **module.yaml minio:23 / langfuse:27** — `spool_dir: /var/lib/platform/{minio,langfuse}-data` → `spool_dir: none` (схема module.schema.json: `none` валиден — stateless в терминах spool_validator; `spool_volume` остаётся: minio-data / langfuse-redis-data — реальные docker-тома).
3. **spool_validator.py** — комментарий @purpose/@rationale: minio/langfuse — spool_dir:none (данные в docker-томах, host-пути удалены B3); verify-логика НЕ меняется (stateless → INFO-пропуск, уже реализовано); убедиться, что minio/langfuse не попадают в WARN-список missing (тест).
4. **Регенерация**: `make generate-platform-env` → platform-env.yaml без двух путей (generated — НЕ вручную); `make check-manifests` зелёный (T9).
5. **Тесты**: test_spool_validator.py — фикстура module.yaml minio с spool_dir:none → status ok, модуль в stateless (не missing); negative: spool_dir с удалённым путём → RED (защита от возврата); infra.py — чтение volumes из platform-env не падает при удалении.

**Критерий:** `rg "var/lib/platform/(minio|langfuse)-data"` по core/ = 0 (кроме git history); spool_validator: minio/langfuse stateless; `make check-manifests` зелёный.

### T9 — Манифесты и регенерация [FUNDAMENT]

**Файлы:** `core/entrypoint-manifest.yaml` (gates-записи T1/T4/T7), `core/internal/scripts/generate_entrypoint_manifest.py` (источник gates), `core/AGENTS.md` (generated), `AGENTS.md` (root — T7), `platform-env.yaml` (T8), `core/internal/bootstrap/AGENTS.md` (φ3-описание), `tests/gates/AGENTS.md` (инвентарь новых гейтов)

**Шаги:**

1. **entrypoint-manifest.yaml gates** (+3 новых гейта волны, trinity: файл tests/gates/ + @pytest.mark.gate + gates-запись): `metrics_cron_contract` (T1), `volumes_sot` (T4), `image_tag_form` (T7); источник — generate_entrypoint_manifest.py (SoT) → `make generate-entrypoint-manifest`.
2. **core/AGENTS.md**: `make generate-agents-md` — canon_table: hermes-push-l1 delegates_to (T7), при необходимости строки bootstrap-node/healthcheck без изменений.
3. **platform-env.yaml**: `make generate-platform-env` (T8).
4. **core/internal/bootstrap/AGENTS.md**: φ3-описание «platform-setup (docker-auth, sudoers, **metrics-cron**)» — дописать шаг metrics-cron (уже есть в списке — проверить текст на соответствие коду); **AGENTS.md root**: TRAP ghcr (T7 п.8) + глоссарий.
5. **tests/gates/AGENTS.md**: инвентарь — +test_gate_volumes_sot.py, +test_gate_image_tag_form.py, расширение test_gate_status_page.py (cron-контракт).
6. Проверка: `make check-manifests` зелёный (0 diff после регенерации).

**Критерий:** `make check-manifests` = 0 diff; новые гейты зарегистрированы (trinity); AGENTS.md не противоречат коду (инвариант 2).

### T10 — Самоверификация волны [GATE]

**Файлы:** новые гейты T1/T4/T7, unit-тесты T1/T2/T5/T8, `.github/workflows/build-hermes.yml` (T7), `docker-compose.yml` (T4)

**Шаги (строго по порядку):**

1. **Регенерация манифестов**: `make generate-manifests` → `git diff` — только ожидаемые изменения (T8/T9); `make check-manifests`.
2. **Гейты волны**: `pytest tests/gates/test_gate_status_page.py tests/gates/test_gate_volumes_sot.py tests/gates/test_gate_image_tag_form.py tests/gates/test_gate_env_chain.py tests/gates/test_p20_container_coupling.py -m gate` — зелёные.
3. **Unit-тесты волны**: `pytest tests/unit/test_phase_metrics_cron.py tests/unit/test_node_yaml_cli_get_many.py tests/unit/test_bootstrap_batch.py tests/unit/test_node_detect.py tests/unit/test_spool_validator.py` — зелёные.
4. **Compose-валидация**: `docker compose config --volumes` — driver_opts только из root; `docker compose config` (профили postgres,monitoring,infra-metrics) — без ошибок; `rg 'driver_opts' core/modules/*/docker-compose.base.yml` = 0.
5. **Consumer-scan финальный**: `rg "config/prometheus.yml"` (без .tmpl) = 0; `rg 'CONTEXT_IMAGE: ""'` = 0; `rg "PLATFORM_CI_DEPLOY_KEY.*=.*\$"` в bootstrap.sh = 0 (приоритетная ветка); `rg "var/lib/platform/(minio|langfuse)-data"` в core/ = 0; `rg "hermes-agent-context" .github/workflows/build-hermes.yml` = 0; `git status` — только ожидаемые файлы волны.
6. **Полный gate**: `make gate MODE=fast` зелёный; `make test MARKER=static` зелёный.
7. **Manual-шаг (оператор, после merge)**: публичность пакета hermes-agent-base (T7 п.2) + e2e на переустановленном сервере (greenfield): `make bootstrap-node` → `/etc/cron.d/platform-metrics` существует, `/var/cache/platform/metrics/` наполняется — отсроченный e2e (требует ноды), помечается в AC.

**Критерий:** все шаги зелёные; гейты волны ловят регрессию (дубль prometheus.yml, CONTEXT_IMAGE "", driver_opts в модулях, голый :latest в base, metrics-cron без кода); `git status` — только ожидаемые файлы.

## 4. Риски и решения

| Риск | Митигация |
|------|-----------|
| Установка cron в φ3 на ноде без cron-демона | Ubuntu (24.04) имеет cron; cron.d — стандартный механизм (прецедент tor-proxy-healthcheck, install-tor-proxy.sh:324); установка нефатальна (WARN), e2e-гейт на переустановленном сервере подтвердит. |
| Удаление prometheus.yml сломает тесты/сервисы | Consumer-scan выполнен: потребители — test_p20 (правится на .tmpl); compose использует только .tmpl; негативный гейт запрещает возврат дубля (T3). |
| Root-SoT volumes с driver_opts сломает macOS-стек | Предпроверено: `docker compose config` уже даёт bind-driver_opts (модульные opts мержатся) — консолидация в root НЕ меняет эффективный конфиг; macos.yml cadvisor — реальный сервис, сохраняется. |
| Standalone-деплой модуля без volumes-секции | deploy-modules использует root compose (include) — том создаётся из root-декларации; test-оверрайды модулей переименовывают тома (-test) — не зависят от модульных секций; проверка T4 п.3. |
| Удаление env-override ci_deploy_key сломает ручные бутстрапы | node.yaml доставляется rsync'ем до bootstrap (канал core/node-configs); ключ обязан быть в node.yaml (схема: node.ci_deploy_key) — иначе WARN «setup skipped» как раньше; ручные запуски редактируют node.yaml. |
| Публичный L1 — «нельзя публиковать приватное» | L1 не содержит секретов (TRAP 2026-07-15: only Python dependencies); публикуется ТОЛЬКО пакет, org/repo остаются приватными; runtime-образы контекстов (L2) остаются в приватных org. |
| L1-pull в контекстной CI гоняется с L1-push (stale :latest) | L1 меняется только при изменении hermes-agent/** (триггер build-hermes); фолбэк: pull-fail → build из исходников (существующий шаг); eventual-consistency :latest документируется; релизы — digest-pin. |
| Отмена L2-push в tronyx161 ломает VPS-деплой | VPS тянет L2 из tronyxlab (runtime) — tronyx161 L2 никто не использовал (проверено U-60); контекстные CI получают L1 по T7 п.3. |
| Гейт tag-формы ложно-блокирует dev-фикстуры | Allowlist явный: platform-dev.yml, smoke.py, docker-compose.test.yml; негатив-тест обязателен (R5); при ложных срабатываниях — сузить regex, не отключать гейт. |
| e2e на переустановленном сервере недоступен в волне | Отсроченный шаг T10 п.7 (greenfield-нода после push); контракты покрыты локально: unit (install_cron_metrics, --get-many, spool) + гейты (код-присутствие) + compose config. |

## 5. Критерии завершения волны (AC брифа 10-Brief)

- [ ] (1) metrics cron устанавливается в φ3 (cron.d/platform-metrics, flock + timeout 50s); gate проверяет код-присутствие, не комментарий (T1).
- [ ] (2) prometheus.yml/.tmpl — один файл (.tmpl, рендерер реальный); дубль удалён, негативный гейт (T3).
- [ ] (3) volume-декларации: root compose — единственный SoT; macos-оверрайд cadvisor согласован (сохранён как реальный сервис); CONTEXT_IMAGE: "" отсутствует (T4/D4).
- [ ] (4) NODE-детекция: единая функция node_detect (исключает scripts+secrets), потребители — metrics-wrapper + core-deploy CI (T2).
- [ ] (5) bootstrap.sh ≤ 150 LOC: один batch-вызов node_yaml --get-many (T5).
- [ ] (6) ci_deploy_key: node.yaml — единственный источник, env-override удалён, TRAP[BUG] снят (T6/D2).
- [ ] (7) ghcr: L1 public на tronyx161 (latest + sha-тег), L2-push удалён, единая tag-форма + гейт (T7/D1).
- [ ] (8) /var/lib/platform/{minio,langfuse}-data удалены из provision (platform-infra.yaml + регенерация), spool_dir: none (T8/D3).
- [ ] Гейты волны (metrics_cron_contract, volumes_sot, image_tag_form) зарегистрированы в entrypoint-manifest (trinity) (T9).
- [ ] `make gate MODE=fast` + `make test MARKER=static` зелёные; `make check-manifests` 0 diff; `docker compose config` валиден (T10).
- [ ] Manual: публичность пакета hermes-agent-base (оператор) + отсроченный e2e на переустановленном сервере (T10 п.7).
- [ ] `make fix-gate && git add -u` выполнен перед коммитом (CI pre-flight, .kilo/rules/_project.md).

$END_DEVPLAN
