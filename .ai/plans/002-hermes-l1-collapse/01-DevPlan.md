# GREP_SUMMARY: DevPlan hermes-agent L1-collapse L2 single-Dockerfile multi-stage distribution-base-removal CI-simplification
# STRUCTURE: ▶ $ARTIFACT_CONTRACT → ◇ контекст/решение → ◇ Draft Code Graph (XML) → ◇ Waves (W1-W7: Dockerfile → Python → make/compose → CI → tests → docs → verify) → ⊕ File Manifest → ⎋ Acceptance Criteria

$ARTIFACT_CONTRACT
PURPOSE:               Схлопнуть L1 (hermes-agent-base) в L2 (hermes-agent-context) — упростить сборку
                       образа hermes-agent по аналогии с остальными модулями (backup-cron, status-page,
                       postgres): один Dockerfile + compose build. L2 сохраняется и остаётся основным
                       (per-context overlay-образ).
DESCRIPTION:           Единый multi-stage Dockerfile (base-стадия = бывший L1, final-стадия = context
                       overlay + CONTEXT guard + USER 10000). Удаляются: L1-жизненный цикл, public
                       distribution base, digest-pin (.github/l1-distribution-digest), :sha-/version-теги
                        L1, pull→bare-tag→build на ноде. Упрощаются 2 Python-модуля, 4→2 make-таргета,
                        3→1 CI-workflow, набор тестов/гейтов/контрактов (включая полный rewrite
                        test_hermes_init.py/test_component_hermes.py), инвариант 10 + 3 TRAP[DECISION].
RATIONALE:             Запуск hermes без контекста не нужен пользователям; L2-payload (context/) пока пуст,
                       но контекстный overlay-механизм (init.py runtime rsync + context/) сохраняется и
                       будет наполнен. Version-машинерия обслуживала идею «контекстные org пулят L1
                       анонимно» — при схлопнутом L1 она избыточна.
ACCEPTANCE_CRITERIA:   make check зелёный; make hermes-build-context CONTEXT=<ctx> собирает единственный
                       образ hermes-agent-context; hermes-agent-base не встречается ни в одном
                       compose/Dockerfile/workflow; gha-cache переиспользует base-слои; D18-баг (bare-tag)
                       отсутствует.
 IMPLEMENTS:            root AGENTS.md инвариант 10 (сборка hermes), TRAP[DECISION] «L1 public distribution
                       base», TRAP[DECISION] «L1 = build-base + smoke-цель».
IMPACTS:               core/modules/hermes-agent/* (Dockerfile, build/-payload, context/, compose.base, module.yaml),
                       core/internal/build/hermes_images.py, core/internal/bootstrap/deploy/hermes_workflow.py,
                       core/internal/bootstrap/deploy/build_cache.py, core/internal/bootstrap/lifecycle/helpers/users.py,
                       makefiles/deploy.mk, .github/workflows/*, .github/actions/*, tests/*,
                       core/entrypoint-manifest.yaml, core/secret-definitions.yaml, AGENTS.md (root/core/modules).
REQUIRES:              Решение пользователя (суперпозиция схлопнута: Option «Схлопнуть L1 в L2»).
$END_ARTIFACT_CONTRACT

$START_DEVPLAN

# DevPlan 002 — Схлопнуть L1 в L2 (hermes-agent)

## 0. Контекст и решение

Текущая сборка hermes-agent — единственная в платформе трёхслойная модель L0→L1→L2:

- **L0** — upstream `nousresearch/hermes-agent:v2026.8.3@sha256…` (digest-pin).
- **L1** — `hermes-agent-base`: config/skills/profiles/plugins/init + monkey-patch #55985 + rsync;
  публикуется в ghcr как **public distribution base**.
- **L2** — `hermes-agent-context`: `FROM hermes-agent-base:latest` + CONTEXT guard + `USER 10000`;
  payload `context/` пока пуст (только `.gitkeep`).

Поверх — 4 make-таргета (`hermes-build-platform/-build-context/-push-l1/-push-l2`), 2 Python-модуля
(`build/hermes_images.py` 223 LOC, `deploy/hermes_workflow.py` 199 LOC), entrypoint `build.sh`,
3 CI-workflow (`build-hermes.yml`, `build-platform.yml`, `hermes-nightly.yml`), digest-файл
`.github/l1-distribution-digest`, теги `:latest`/`:v<pyproject-version>`/`:sha-<sha>`.

**Решение (суперпозиция схлопнута):** L1 схлопывается в L2. L2 — целевая модель (per-context overlay),
сохраняется и используется. Version-машинерия (distribution base + digest + sha/version-теги +
bare-tag) удаляется — она обслуживала неиспользуемую сейчас возможность «анонимный pull L1».

## 1. Draft Code Graph (XML)

```xml
<Module name="core_modules_hermes_agent_Dockerfile_py" TYPE="dockerfile" keywords="L2,multi-stage,base,context,CONTEXT-guard">
  <Stage name="validate" annotation="alpine: shellcheck + yq + chmod (build-only)"/>
  <Stage name="base" annotation="FROM nousresearch/hermes-agent (L0) + platform artifacts + monkey-patch + rsync"/>
  <Stage name="final" annotation="FROM base + context config/skills + init-context.sh + USER 10000 + HEALTHCHECK"/>
  <CrossLinks>hermes_images_py, docker_compose_base_yml</CrossLinks>
</Module>

<Module name="core_internal_build_hermes_images_py" TYPE="python" keywords="build_context,CONTEXT-guard,BuildKit-cache">
  <Func name="build_context" annotation="single action: docker build -t hermes-agent-context --build-arg CONTEXT"/>
  <Func name="main" annotation="choices=[build-context|L2]; CONTEXT guard exit 1"/>
  <CrossLinks>Dockerfile, deploy_mk</CrossLinks>
</Module>

<Module name="core_internal_bootstrap_deploy_hermes_workflow_py" TYPE="python" keywords="pull-or-build,single-image,compose-config-images">
  <Func name="handle_hermes_agent" annotation="config --images → all found? True : compose build (BUILD_TIMEOUT)"/>
  <CrossLinks>docker_orchestrator_py, docker_compose_base_yml</CrossLinks>
</Module>

<Module name="makefiles_deploy_mk" TYPE="makefile" keywords="hermes-build-context,hermes-push-l2">
  <Target name="hermes-build-context" annotation="python3 -m core.internal.build.hermes_images build-context (без build.sh)"/>
  <Target name="hermes-push-l2" annotation="push L2 в org контекста (:latest + :v<version>)"/>
  <CrossLinks>hermes_images_py, entrypoint_manifest_yaml</CrossLinks>
</Module>
```

## 2. Data Flow (пошагово)

1. **Local build:** `make hermes-build-context CONTEXT=<org>` → `python3 -m core.internal.build.hermes_images build-context`
   → `docker build --platform linux/amd64 -t hermes-agent-context --build-arg CONTEXT=<org> -f core/modules/hermes-agent/Dockerfile <repo-root>`
   (BuildKit local cache `/tmp/.hermes-build-cache`).
2. **Push:** `make hermes-push-l2 CONTEXT=<org>` → docker tag + push `ghcr.io/<org>/hermes-agent-context:{latest,v<version>}`.
3. **Node deploy:** `deploy_orchestrator._phase_hermes` → `hermes_workflow.handle_hermes_agent`
   → `docker compose config --images` → все найдены? → `up`; иначе `docker compose build` из source.
4. **Runtime:** init.py (02-platform-init) + init-context.sh (04-context-init) — контекстный overlay
   rsync из `/opt/hermes/context/` (source = context payload образа; будущий per-context fill).

## 3. Волны и задачи

### W1 — Dockerfile (коллапс L1→L2)
- **T1.1** Создать `core/modules/hermes-agent/Dockerfile` — единый multi-stage:
  стадии `validate` (alpine) → `base` (FROM L0 + артефакты `build/` + monkey-patch + rsync) →
  `final` (FROM base + `context/` + 04-context-init + `USER 10000` + HEALTHCHECK).
  Порядок COPY: base-слои до context-слоёв (layer-cache).
- **T1.2** Удалить `core/modules/hermes-agent/build/Dockerfile` и `core/modules/hermes-agent/context/Dockerfile`.
- **T1.3** `.dockerignore` (root): добавить `!core/modules/hermes-agent/build/**` в allowlist
  (сейчас только `context/**`); обновить MODULE_CONTRACT.
- **T1.4** Починить payload `build/` (COPY в base-стадию): `build/config/.env.example:44`
  (`BASE_IMAGE=hermes-agent-base:latest` → L0-образ/удалить), `build/config/SOUL.md:20`,
  `build/templates/profiles/default/SOUL.md:25` (упоминания «L1»/hermes-agent-base).

### W2 — Python build-модули
- **T2.1** `hermes_images.py`: удалить `build_l1`/`L1_IMAGE`/dispatch `build-platform|L1`;
  оставить `build_context()` (бывш. build_l2) + `main()` с `choices=[build-context|L2]`.
- **T2.2** `hermes_workflow.py`: удалить `L1_BASE_IMAGE`/`GHCR_ORG`/docker_ops import/`docker`/`ghcr_org`
  DI-параметры; flow = `config --images → all found? True : compose build`.
- **T2.3** Удалить `core/entrypoints/build.sh` (middle-hop схлопнут).

### W3 — Make + Compose
- **T3.1** `makefiles/deploy.mk`: удалить `hermes-build-platform`, `hermes-push-l1`, `GHCR_OWNER`;
  `hermes-build-context` → прямой вызов `.venv/bin/python3 -m core.internal.build.hermes_images build-context`;
  `hermes-push-l2` без изменений.
- **T3.2** `core/modules/hermes-agent/docker-compose.base.yml`: `build.dockerfile` → `core/modules/hermes-agent/Dockerfile`;
  схлопнуть L0/L1/L2-комментарии (TRAP[DECISION] 2026-07-06/07, W7 fix — удалить; CONTEXT_IMAGE SoT + TRAP[PERF] — оставить).
- **T3.3** `core/modules/hermes-agent/module.yaml`: обновить @changes (L1→L2).
- **T3.4** Удалить `docker-compose.platform-dev.yml` (L1 dev-оверрайд мёртв) + убрать из
  `COMPOSE_BASE_FILES` в root `Makefile`.

### W4 — CI
- **T4.1** Удалить `.github/workflows/build-hermes.yml` (public distribution base).
- **T4.2** Удалить `.github/workflows/build-platform.yml` (L1 build+smoke+push+sync-digest;
  smoke покрыт platform-test.yml ci-docker gate).
- **T4.3** Удалить `.github/l1-distribution-digest`.
- **T4.4** `platform-test.yml`: build-шаг → единый образ (`context: .`,
  `file: core/modules/hermes-agent/Dockerfile`, `tags: hermes-agent-context:latest`,
  `cache-scope: hermes-agent-context`); pre-pull `nousresearch/hermes-agent:v2026.8.3`;
  `CONTEXT_IMAGE: hermes-agent-context:latest`.
- **T4.5** `hermes-nightly.yml`: комментарии L1→L2 → «единый L2-образ» (таргеты не меняются).
- **T4.6** `.github/actions/*` (docker-build-cache/action.yml:7-8,39; sha-resolve/action.yml:5,9,21,24;
  provisioner-call/action.yml:7,16): обновить `@scope`/`@changes` (hermes-agent-base/build-platform →
  единый образ/platform-test).
- **T4.7** Комментарии downstream-цепочек: `mirror.yml:45`, `push-gate.yml:26`,
  `platform-gate-fast.yml:2,5` (упоминания build-platform → убрать).

### W5 — Тесты и гейты
- **T5.1** `tests/unit/test_hermes_images.py`: переписать под `build_context` (guard + cmd + failure + main-dispatch).
- **T5.2** `tests/unit/test_hermes_workflow.py`: переписать под single-build (1 build call, не 2).
- **T5.3** Удалить `tests/unit/test_hermes_l1_bare_tag.py` (D18 — bare-tag исчез).
- **T5.4** `tests/unit/test_hermes_version.py`: переписать (L1 label удалён; единый образ).
- **T5.5** `tests/test_hermes_l2_fallback.py`: обновить static-аудит (`"Local L1→L2 build failed"` →
  `"Local build failed"`; убрать L1-специфичные паттерны).
- **T5.6** `tests/gates/test_gate_compose_no_base_image.py`: переписать → «hermes-agent-base НИГДЕ»
  + «base.yml использует CONTEXT_IMAGE var» (удалить test_platform_dev_has_l1_image).
- **T5.7** Гейты CI: `test_gate_workflow_consistency.py` (workflow-list/count 10→8, убрать build-platform-тесты),
  `test_gate_ci_coverage.py` (deploy_workflows без build-platform), `test_gate_ci_trigger_strength.py`
  (`_DOWNSTREAM_WORKFLOWS` без build-platform), `test_gate_ci_env_vars.py` (`TEST_CREDS_WORKFLOWS` без build-platform),
  `test_gate_workflow_checkout_order.py` (ссылки).
- **T5.8** `test_gate_root_agents_invariants.py` (инвариант 10 → hermes-build-context/hermes-push-l2),
  `test_gate_image_tag_form.py` (убрать `hermes-agent-base` из valid-набора), `test_generate_agents_md.py`
  (убрать hermes-build-platform), `test_smoke_provision_environment.py`/`test_gate_networks_sot.py`/
  `test_gate_volumes_sot.py` (platform-dev ссылки).
- **T5.9** `tests/gates/test_gate_thin_wrapper.py:229`: `test_discovery` — убрать
  `assert "build.sh" in names` (entrypoint удалён T2.3). CRITICAL.
- **T5.10** `tests/test_hermes_init.py` (563 LOC, requires_docker): полный rewrite под единый
  Dockerfile; удалить `_L1_DOCKERFILE`/`_L2_DOCKERFILE` (71–73) и MODULE_CONTRACT-инвариант
  «L1 без CONTEXT guard / L2 с guard» → заменить на проверку guard-поведения единого образа
  (guard есть + USER 10000). CRITICAL.
- **T5.11** `tests/test_component_hermes.py` (1091 LOC): `GHCR_ORG` (85),
  `hermes-agent-base:latest` (336), build-from-context (337–338) → единый образ. CRITICAL.
- **T5.12** `tests/gates/test_gate_ci_env_vars.py:85`: удалить `L1_IMAGE` из каталога валидных env
  (T5.7 покрывает только `TEST_CREDS_WORKFLOWS`).
- **T5.13** `tests/gates/test_gate_image_tag_form.py:40`: удалить `docker-compose.platform-dev.yml`
  из file-list (в дополнение к T5.8 — hermes-agent-base из valid-набора).
- **T5.14** `tests/unit/test_secrets_validation.py:74`: комментарий «hermes-agent-base (L1, local only)».
- **T5.15** `tests/contracts/test_make_target_contracts.py:124,178-179`: пример «build-platform»
  subcommand в docstring/комментарии.

### W6 — Инварианты и доки
- **T6.1** root `AGENTS.md`: переписать инвариант 10 (сборка hermes = единый L2, без L1-distribution);
  пересмотреть TRAP[DECISION] «L1 public distribution base» и «L1 = build-base + smoke-цель».
- **T6.2** `core/AGENTS.md`: canon-таблица (удалить hermes-build-platform/hermes-push-l1 строки)
  + матрица ключей (стр. 255 — удалить `GHCR_OWNER`).
- **T6.3** `core/entrypoint-manifest.yaml`: build-секция (2 записи), `dev-dr` список, `allowed_verbs`,
  consumers `audit.sh` (убрать build.sh) — затем `make generate-entrypoint-manifest`.
- **T6.4** `core/modules/AGENTS.md`: `{build,context}/` — «только hermes-agent» → единый Dockerfile;
  упоминание `docker-compose.platform-dev.yml` как dev-оверрайда.
- **T6.5** `core/internal/static/dead_code.py`: обновить пример «build.sh build-platform».
- **T6.6** `core/secret-definitions.yaml:349`: note «Previously used by platform-test.yml,
  build-platform.yml» → убрать build-platform; затем `make generate-manifests` (перегенерация
  `core/secrets-manifest.yaml:365`).
- **T6.7** `core/internal/bootstrap/lifecycle/helpers/users.py:47,98,101,104,153`: docstrings про
  `GHCR_OWNER` → убрать/заменить.
- **T6.8** `core/internal/bootstrap/deploy/build_cache.py:10-11`: @scope формулировка «GHCR pull»
  → обновить (после схлопывания — сборка из source, не pull).

### W7 — Верификация
- **T7.1** `make generate-manifests` (регенерация allowed_verbs/gates).
- **T7.2** `make check` до чистоты (батч всех ошибок).
- **T7.3** Ручной дымовой прогон: `make hermes-build-context CONTEXT=test` (локально, если Docker доступен).

## 4. File Manifest

| Действие | Файл |
|----------|------|
| CREATE | `core/modules/hermes-agent/Dockerfile` |
| DELETE | `core/modules/hermes-agent/build/Dockerfile` |
| DELETE | `core/modules/hermes-agent/context/Dockerfile` |
| DELETE | `core/entrypoints/build.sh` |
| DELETE | `docker-compose.platform-dev.yml` |
| DELETE | `.github/workflows/build-hermes.yml` |
| DELETE | `.github/workflows/build-platform.yml` |
| DELETE | `.github/l1-distribution-digest` |
| DELETE | `tests/unit/test_hermes_l1_bare_tag.py` |
| MODIFY | `.dockerignore` |
| MODIFY | `core/internal/build/hermes_images.py` |
| MODIFY | `core/internal/bootstrap/deploy/hermes_workflow.py` |
| MODIFY | `core/internal/bootstrap/deploy/docker_orchestrator.py` (docstrings) |
| MODIFY | `makefiles/deploy.mk` |
| MODIFY | `core/modules/hermes-agent/docker-compose.base.yml` |
| MODIFY | `core/modules/hermes-agent/module.yaml` |
| MODIFY | `core/modules/hermes-agent/build/config/.env.example` |
| MODIFY | `core/modules/hermes-agent/build/config/SOUL.md` |
| MODIFY | `core/modules/hermes-agent/build/templates/profiles/default/SOUL.md` |
| MODIFY | `Makefile` (COMPOSE_BASE_FILES) |
| MODIFY | `.github/workflows/platform-test.yml` |
| MODIFY | `.github/workflows/hermes-nightly.yml` |
| MODIFY | `.github/workflows/mirror.yml` |
| MODIFY | `.github/workflows/push-gate.yml` |
| MODIFY | `.github/workflows/platform-gate-fast.yml` |
| MODIFY | `.github/actions/docker-build-cache/action.yml` |
| MODIFY | `.github/actions/sha-resolve/action.yml` |
| MODIFY | `.github/actions/provisioner-call/action.yml` |
| MODIFY | `tests/unit/test_hermes_images.py` |
| MODIFY | `tests/unit/test_hermes_workflow.py` |
| MODIFY | `tests/unit/test_hermes_version.py` |
| MODIFY | `tests/test_hermes_l2_fallback.py` |
| MODIFY | `tests/test_hermes_init.py` |
| MODIFY | `tests/test_component_hermes.py` |
| MODIFY | `tests/unit/test_secrets_validation.py` |
| MODIFY | `tests/contracts/test_make_target_contracts.py` |
| MODIFY | `tests/gates/test_gate_thin_wrapper.py` |
| MODIFY | `tests/gates/test_gate_compose_no_base_image.py` |
| MODIFY | `tests/gates/test_gate_workflow_consistency.py` |
| MODIFY | `tests/gates/test_gate_ci_coverage.py` |
| MODIFY | `tests/gates/test_gate_ci_trigger_strength.py` |
| MODIFY | `tests/gates/test_gate_ci_env_vars.py` |
| MODIFY | `tests/gates/test_gate_workflow_checkout_order.py` |
| MODIFY | `tests/gates/test_gate_root_agents_invariants.py` |
| MODIFY | `tests/gates/test_gate_image_tag_form.py` |
| MODIFY | `tests/unit/test_generate_agents_md.py` |
| MODIFY | `tests/test_smoke_provision_environment.py` |
| MODIFY | `tests/gates/test_gate_networks_sot.py` |
| MODIFY | `tests/gates/test_gate_volumes_sot.py` |
| MODIFY | `AGENTS.md` (root — инвариант 10 + TRAP) |
| MODIFY | `core/AGENTS.md` (canon-таблица) |
| MODIFY | `core/modules/AGENTS.md` |
| MODIFY | `core/entrypoint-manifest.yaml` |
| MODIFY | `core/secret-definitions.yaml` |
| MODIFY | `core/secrets-manifest.yaml` (generated) |
| MODIFY | `core/internal/bootstrap/lifecycle/helpers/users.py` |
| MODIFY | `core/internal/bootstrap/deploy/build_cache.py` |
| MODIFY | `core/internal/static/dead_code.py` |

## 5. Acceptance Criteria

1. `make check` зелёный (все проверки из `core/check-suite.yaml`).
2. `make hermes-build-context CONTEXT=<ctx>` собирает единственный образ `hermes-agent-context`.
3. `hermes-agent-base` не встречается ни в одном compose/Dockerfile/workflow/тесте/`build/`-payload
   (кроме негативных drift-детекторов).
4. `make hermes-build-platform` и `make hermes-push-l1` отсутствуют (namelint/manifest чистые).
5. Node-side deploy: `handle_hermes_agent` — один путь build из source, без L1 pull/bare-tag.
6. CI: 8 workflow-файлов (build-hermes/build-platform удалены); platform-test строит единый образ.
7. D18-баг (bare-tag `FROM hermes-agent-base:latest`) невозможен — L1-образ не существует.
8. Тест-контракт `test_hermes_init.py` проверяет guard-поведение единого образа (инвариант
   «L1 без guard / L2 с guard» удалён), а не два Dockerfile; `test_component_hermes.py` и
   `test_gate_thin_wrapper.py` зелёные под единый образ/без `build.sh`.

## 6. Риски и TRAP

⚠️ TRAP[DECISION] · 2026-08-16 · — · Удаление L1 public distribution base
· Rejected: сохранение L1 как shared base (дистрибутивная база + DR).
· Reason: L2-payload пуст, запуск без контекста не нужен; контекстные org собирают L2 из source
·   (gha-cache переиспользует base-слои). DR-канал `make hermes-push-l1` заменяется `hermes-push-l2`
·   в org контекста.
· Rev: если появится >1 org, тянущих L1 анонимно (цена анонимного pull станет критичной) —
·   вернуть отдельную base-стадию с публикацией.

⚠️ TRAP[DECISION] · 2026-08-16 · — · docker-compose.platform-dev.yml удалён целиком
· Rejected: оставить пустой/частичный dev-оверрайд.
· Reason: файл содержал только L1-оверрайд hermes; после коллапса dev = единый образ с CONTEXT=test.
· Rev: если появится новый dev-only оверрайд (не hermes) — пересоздать файл.

⚠️ TRAP[DECISION] · 2026-08-16 · — · Контракт «L1 без CONTEXT guard / L2 с guard» исчезает
· Rejected: сохранить двухобразный тест (build/ + context/ Dockerfile) для проверки guard-разницы.
· Reason: после схлопывания разницы нет — единый образ всегда с guard (final-стадия). Тест
·   `test_hermes_init.py` переписывается на проверку guard-поведения единого образа (guard + USER 10000).
· Rev: если вновь появится guard-less базовая стадия с публикацией — вернуть контракт.

$END_DEVPLAN
