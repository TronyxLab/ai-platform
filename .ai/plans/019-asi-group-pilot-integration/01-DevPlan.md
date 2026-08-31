$START_DEVPLAN

# 01-DevPlan — 019 asi-group pilot integration

> **Rev 2 (2026-08-31):** обновлён под W7 ai-project (`.ai/plans/012-ai-project/waves/W7-devplan.md`
> в монорепо ai-project): семья из 4 будущих проектов (managers/clients/partners/executive) через
> `make new-project + template-ai-project`. Обнаружено: скаффолд-канал для template-ai-project
> НЕ существует (choices {frontend, backend} + манифест без регистрации) → добавлен TASK-8;
> TASK-1 становится hard-prerequisite W7 T0; судьба легаси-пилотов зафиксирована (decommission
> при деплое W7-семьи).

$ARTIFACT_CONTRACT
PURPOSE:      Закрыть инцидент подключения пилотов ai-project (client-bot/asi-faq, managers-bot/asi-managers)
              к шаред-сервисам платформы: production-compose не имел сетей shared-db-net/hermes-agent-net и
              ссылался на несуществующую переменную DATABASE_URL; устранить класс через фикс
              templates/template-ai-project + новый L1-гейт сетей/env; объявить needs.database;
              дать платформенный parity-DB путь для PG-parity прогонов.
DESCRIPTION:  STANDARD-план: фикс шаблона template-ai-project, регенерация production-compose двух пилотов,
              полные ai-platform.yaml манифесты (name/target_node/needs/monitoring/quality), новый L1-контракт
              service-network-coverage + env-var-resolution в verify_contracts.py (K3, pre-apply на VPS)
              с зеркалом в K1 project-check (единый shared-анализатор), verb `parity-db` (create/drop
              временной parity-БД через привилегированный путь). roadmap (эталон канона) не изменяется.
RATIONALE:    Аудит 2026-08-31: оба пилота генерированы дефектным шаблоном (только proxy-net,
              ${DATABASE_URL} → пусто при --env-file .env.platform); K3-гейт слеп к классу
              (проверяет только «нет чужих сетей», не «все потребляемые сервисы достижимы»).
              Локальный macos-local compose пилотов сделан правильно — агент понимал механику,
              но GENERATED production-compose не чинил. Вариант C супер-позиции утверждён владельцем.
ACCEPTANCE_CRITERIA:
  AC1: production-compose обоих пилотов содержит shared-db-net + hermes-agent-net +
       DATABASE_URL=${PLATFORM_POSTGRES_DSN}; контейнер на ноде резолвит pgbouncer:6432 и litellm:4000.
  AC2: templates/template-ai-project/docker-compose.yml генерирует compose с нужными сетями и
       PLATFORM_POSTGRES_DSN-маппингом (гейт test_gate_template_ai_project_networks).
  AC3: L1-гейт блокирует деплой compose, потребляющего платформенный сервис без сети провайдера
       (негативный тест на инцидентном инпуте) и ${VAR} без источника резолва.
  AC4: needs.database объявлен у обоих пилотов; хук postgres конвергирует СУЩЕСТВУЮЩИЕ БД/роли
       без создания дублей (already-exists skip), .env.platform регенерируется.
  AC5: `make parity-db ACTION=create|drop PROJECT=<n>` создаёт/дропает parity-БД через
       привилегированный путь; проектные роли БЕЗ CREATEDB (изоляция канона сохранена).
  AC6: `make new-project NAME=<x> TEMPLATE=ai-project` работает end-to-end: полный
       ai-platform.yaml (needs.database=имя, monitoring, quality), исправленный compose с сетями,
       practices.lock — W7 T0 (скаффолд managers) разблокирован.
IMPLEMENTS:   Утверждённый вариант C супер-позиции (опрос владельца 2026-08-31); хвост parity-сессии
              ai-project («повторные PG-parity прогоны требуют роль с CREATEDB либо платформенную parity-БД»).
IMPACTS:      templates/template-ai-project/*, projects/asi-group/{client-bot,managers-bot}/*,
              core/internal/deploy/verify_contracts.py, core/internal/shared/ (новый анализатор),
              core/internal/practices/{practices_manifest.yaml,check_project.py}, parity-db verb
              (Makefile + entrypoint-manifest + глоссарий — generated-каскад), core/internal/scaffold/
              (легализация template-ai-project в make new-project), core/templates/template-manifest.yaml,
              tests/gates, tests/unit.
REQUIRES:     core/platform-infra.yaml#provides (SoT сетей, DR-M4); core/internal/practices/practices_manifest.yaml
              #allowed_external_networks (все 6 сетей уже в allowlist — K3 не заблокирует фикс);
              идемпотентность hooks/on_project_deploy.py (already-exists skip); F-11 compose --env-file
              (deploy_paths.project_compose_env_args); practices.lock пилотов (state для K3);
              W7-девплан ai-project (кросс-репо: .ai/plans/012-ai-project/waves/W7-devplan.md) —
              4 будущих проекта scaffold'ятся через make new-project + template-ai-project (§3.6 W7).
$END_ARTIFACT_CONTRACT

## Source (запрос владельца, дословно)

> Проверь как подключены шаред сервисы к проектам в asi-group, их подключал агент из их репозитория,
> мне кажется он не разобрался как правильно подключать проекты к ai-platform. Проверь и раскрой
> супер позицию как это модифицировать в будущем, чтобы не было подобных проблем. Опроси меня если
> требуется и напиши девплан. [...] Так же проверь и третий проект - roadmap.
>
> Хвост прошлой сессии: «Владельцу платформы: обе роли пилотов (managers-bot, client-bot) без права
> CREATE DATABASE через pgbouncer — повторные PG-parity прогоны требуют роль с CREATEDB либо
> платформенную parity-БД (сама parity-БД от прогона X4 дропнута, как и заявлено)».

## Clarifications (опрос владельца, 2026-08-31)

| Вопрос | Ответ |
|---|---|
| Вариант супер-позиции | **C** — фикс шаблона + пилотов + L1-гейт сетей/env |
| Ингресс ботов | **Нужны домены** — needs.domain + expose, рендер через render-vhosts |
| БД пилотов | **Объявить needs.database** — хук конвергирует существующие БД/роли |
| PG-parity | **Платформенная parity-БД** — временная БД через привилегированный путь |

## Requirements Analysis — фактура аудита (всё проверено в этой сессии)

| # | Факт | Доказательство |
|---|---|---|
| F1 | pgbouncer слушает ТОЛЬКО shared-db-net; litellm — hermes-agent-net (+shared-db-net, +observability-net) | SoT core/platform-infra.yaml#provides (DR-M4); core/modules/{postgres,litellm}/docker-compose.base.yml |
| F2 | Production-compose обоих пилотов: сети = own-net + proxy-net ТОЛЬКО | projects/asi-group/{client-bot,managers-bot}/docker-compose.yml:49-70 |
| F3 | Compose пилотов: `DATABASE_URL=${DATABASE_URL}` — переменной нет в .env.platform (платформа генерирует PLATFORM_POSTGRES_DSN); деплой идёт `--env-file secrets.env --env-file .env.platform` (F-11) → интерполяция в ПУСТО | core/internal/shared/deploy_paths.py:369 (project_compose_env_args); .env.platform пилотов |
| F4 | Локальный docker-compose.macos-local.yml обоих пилотов — ПРАВИЛЬНЫЙ: shared-db-net + shared-cache-net + hermes-agent-net, `DATABASE_URL=${PLATFORM_POSTGRES_DSN:?}` | projects/asi-group/*/docker-compose.macos-local.yml:38,47-50 — агент понимал механику, GENERATED production-compose не чинил («ручные правки затираются практиками») |
| F5 | K3-гейт слеп к классу: `_check_external_networks` проверяет только «external-сеть вне allowlist»; обратного («потребляемый сервис без сети провайдера») нет | core/internal/deploy/verify_contracts.py:575-598 |
| F6 | ai-platform.yaml пилотов: только type + llm; нет name/target_node/needs/monitoring/quality; БД создана вручную (.platform-db.env) — канон «НЕ создавай свои БД вне needs.database» нарушен | ai-platform.yaml обоих пилотов; существующие DSN: asi-faq_user@asi-faq_db, managers-bot_user@managers-bot_db |
| F7 | Хук postgres идемпотентен: CREATE DATABASE skip on already-exists; ensure_project_db_access конвергирует роль/GRANT/креды и регенерирует .env.platform | core/modules/postgres/hooks/on_project_deploy.py:97-330 |
| F8 | roadmap подключён канонически: полный манифест, target_node=asi-team-vps, git push → CI org asi-group; сети proxy-net достаточны (SPA без needs). Косметика: DSN с литеральным `***` (needs.database нет — пароль не инжектируется) | ~/projects/asi-group/roadmap/{ai-platform.yaml,docker-compose.yml,.env.platform} |
| F9 | Image org пилотов `ghcr.io/tronyxlab/*` ≠ контекст asi-group (roadmap: `ghcr.io/asi-group/*`) | compose image: строка 25/27 пилотов |
| F10 | Лейблы пилотов: `platform.domain=asi-faq.local` / `asi-managers.local` — mDNS-псевдодомены; PLATFORM_NGINX_URL=https://ai-platform.local — фантом | labels compose + .env.platform |
| F11 | Allowlist K3 `allowed_external_networks` уже содержит все 6 платформенных сетей — добавление сетей пилотам гейт НЕ триггернет | practices_manifest.yaml:33-39 |
| F12 | Владелец подтвердил: ботам нужны публичные домены (wildcard *.asiteam.ru уже выдан — прецедент login.asiteam.ru overlay) | опрос 2026-08-31; managers-bot/deploy/nginx-overlay/login.asiteam.ru.conf |
| F13 | **template-ai-project НЕ создаётся через make new-project**: project_scaffolder.py:678 жёстко валидирует `--template ∈ {frontend, backend}` → ERROR «Invalid template type» | core/internal/scaffold/project_scaffolder.py:661-680 |
| F14 | template-ai-project НЕ зарегистрирован в core/templates/template-manifest.yaml (только template-backend:203, template-frontend:220) → templates-check покрытия нет | core/templates/template-manifest.yaml |
| F15 | template-ai-project НЕ содержит ai-platform.yaml payload — манифест генерирует gen_ai_platform_yaml (needs.domain/expose/database, monitoring per-type, quality); ветка monitoring знает только frontend/backend | scaffold_helpers.py:109-200; ls templates/template-ai-project/ |

**Кросс-план: W7 (ai-project 012) — семья из 4 ботов.** W7-девплан утверждён 2026-08-31:
проекты `managers` (эталон), `clients`, `partners`, `executive` — папки без префикса asi в
контексте asi-group; слой шаблона создаётся «автоматически: make new-project + template-ai-project»
(W7 §3.6); Q&A = knowledge.answer с разными источниками (АтмаГуру + файлы + живой CRM read через
webhook); идентичность allowlist/аноним; P10 на asi-team-vps. Взаимодействие с настоящим планом:

| W7-факт | Следствие для 019 |
|---|---|
| 4 проекта scaffold'ятся через make new-project + template-ai-project (§3.6) | F13/F14: канал НЕ работает → **TASK-8** (легализация) + **TASK-1** = hard-prerequisite W7 T0; без них волна повторит ручной скаффолд = инцидент ×4 |
| Скаффолд генерирует полный ai-platform.yaml (F15) с needs.database (если флаг передан) | TASK-8 задаёт дефолт needs.database=имя проекта (боты ВСЕГДА нуждаются в БД — kernel-стейт в Postgres) → хук провижинит БД 4 новых проектов автоматически, класс «ручной .platform-db.env» не повторится |
| D1 W7: «FAQ-бот (asi_help_bot) удаляется: Q&A — общая capability»; эталон managers эволюционирует из W4-пилота asi-managers | Легаси-пилоты (client-bot/asi-faq, managers-bot/asi-managers) живут до деплоя W7-семьи → фикс NOW (T2/T3/T7) остаётся, decommission — `make remove-project` после W7 T4 (координационная заметка, вне скоупа 019) |
| W7 T4: P10 живой прогон 4 ботов на asi-team-vps; parity/PG-прогоны ai-project | **TASK-6** (parity-db) обязателен ДО W7 T4 — 4 новых БД + parity-прогоны без CREATEDB |
| W7 §8.6: правка брифа §24/§18 — открытый вопрос владельца | Вне скоупа 019 (монорепо ai-project) |

**Ключевые критерии успеха:** (1) оба пилота в production-стеке достигают pgbouncer и litellm;
(2) шаблон template-ai-project генерирует корректный compose И скаффолд-канал легален — 4 будущих
проекта W7 стартуют из правильного каркаса; (3) класс закрыт гейтом на двух рубежах (K1 push /
K3 deploy); (4) БД пилотов под управлением платформы; (5) parity-прогоны имеют легальный
привилегированный путь без ослабления изоляции ролей; (6) W7 T0 не блокируется платформой.

## Draft Code Graph

```
templates/template-ai-project/docker-compose.yml       [CHG] +shared-db-net +hermes-agent-net;
                                                              DATABASE_URL=${PLATFORM_POSTGRES_DSN}
templates/template-ai-project/AGENTS.md                [CHG] env-контракт: DATABASE_URL ← PLATFORM_POSTGRES_DSN
projects/asi-group/client-bot/docker-compose.yml       [CHG] сети + DSN-маппинг (патч в пользу GENERATED-канала)
projects/asi-group/client-bot/ai-platform.yaml         [CHG] name/target_node/needs{database,domain,expose}/monitoring/quality
projects/asi-group/managers-bot/docker-compose.yml     [CHG] то же
projects/asi-group/managers-bot/ai-platform.yaml       [CHG] то же
core/internal/shared/compose_service_contract.py       [NEW] ЕДИНСТВЕННЫЙ статический анализатор (K1+K3):
                                                              coverage / env-resolution / needs-кросс-чек
core/internal/deploy/verify_contracts.py               [CHG] +2 L1-чека, потребляют shared-анализатор
core/internal/practices/check_project.py               [CHG] K1-чек `compose-service-networks` через тот же анализатор
core/internal/practices/practices_manifest.yaml        [CHG] +check entry (class L1, channel K1)
core/internal/deploy/parity_db.py                      [NEW] parity-БД create/drop (docker exec psql, DI CommandRunner)
core/entrypoints/parity-db.sh                          [NEW] тонкий фасад → python3 -m core.internal.deploy.parity_db
Makefile                                               [CHG] +parity-db (.PHONY → generated-каскад манифестов)
core/internal/scaffold/project_scaffolder.py           [CHG] choices += ai-project (F13); needs.database default=name;
                                                              monitoring-ветка для ptype=ai-project (F15)
core/templates/template-manifest.yaml                  [CHG] +регистрация template-ai-project (F14)
tests/gates/test_gate_service_network_coverage.py      [NEW] K3-гейт: позитив + негатив на инцидентном инпуте (R5)
tests/gates/test_gate_template_ai_project_networks.py  [NEW] шаблон ⊇ сети потребляемых сервисов (SoT-парити)
tests/unit/scaffold/test_scaffold_ai_project.py        [NEW] gen_ai_platform_yaml(ai-project) + choices-валидация
tests/unit/deploy/test_parity_db.py                    [NEW] create/drop idempotent (fake runner)
tests/unit/practices/test_check_compose_networks.py    [NEW] K1-чек на инцидентном compose → [PRACTICES:BLOCK]
```

## Data Flow

**Инцидентный путь (ДО):**
```
ai-project scaffold → template-ai-project compose (proxy-net only; DATABASE_URL=${DATABASE_URL})
  → deploy-project → verify_contracts PASS (coverage-чека нет, F5)
  → compose up (--env-file .env.platform) → ${DATABASE_URL} → "" (F3)
  → контейнер на proxy-net → pgbouncer:6432 НЕ резолвится (F1/F2) → crash-loop / LLM-degraded
```

**Канонический путь (ПОСЛЕ):**
```
scaffold (TASK-1) → compose: own-net + proxy-net + shared-db-net + hermes-agent-net;
  DATABASE_URL=${PLATFORM_POSTGRES_DSN}; LLM_BASE_URL=${PLATFORM_LITELLM_URL}
  → K1 project-check (TASK-5, push-рубеж): анализатор (TASK-4) → violation? [PRACTICES:BLOCK]
  → deploy-project → verify_contracts L1 (TASK-4, deploy-рубеж, pre-apply до compose-up, REF-0006):
      service-network-coverage: networks(svc) ∩ provides.networks(SoT) ≠ ∅
      env-var-resolution: каждый ${VAR} ∈ (.env.platform ∪ secret-definitions ∪ default)
      db-consumed-not-declared: PLATFORM_POSTGRES_DSN в env ⇔ needs.database в манифесте
  → compose up → pgbouncer/litellm достижимы → healthcheck /health 200
  → postgres hook: needs.database → already-exists SKIP → GRANT-converge → .env.platform regen
```

**Parity-путь (ПОСЛЕ):**
```
make parity-db ACTION=create PROJECT=managers-bot [NODE=<n>]
  → parity_db.py: docker exec postgres psql (привилегированный путь, postgres-контейнер)
  → CREATE DATABASE parity_managers-bot + ROLE parity_parity... (полные права ТОЛЬКО на parity-БД)
  → stdout DSN → parity-инструментарий ai-project consumes → прогоны →
make parity-db ACTION=drop PROJECT=managers-bot → DROP DATABASE + DROP ROLE (чисто)
```

## $TASKS

| ID | Задача | Артефакт | Роль | Deps | CX | AC |
|----|--------|----------|------|------|----|----|
| TASK-1 | Фикс templates/template-ai-project: сети + DSN-маппинг + AGENTS.md env-контракт | template compose + AGENTS.md | Coder | — | 3 | AC2 |
| TASK-2 | Патч production-compose обоих пилотов (сети + DATABASE_URL=${PLATFORM_POSTGRES_DSN}; LLM_BASE_URL=${PLATFORM_LITELLM_URL}) | 2×docker-compose.yml | Coder | T1 | 2 | AC1 |
| TASK-3 | Полные ai-platform.yaml пилотов + project-sync-env | 2×ai-platform.yaml (+2×.env.platform regen) | Coder | — | 3 | AC4 |
| TASK-4 | Shared-анализатор compose_service_contract.py + 2 L1-чека в verify_contracts.py + gate-тесты | analyzer + verify_contracts + tests/gates×2 | Coder | — | 6 | AC3 |
| TASK-5 | K1-зеркало: practices_manifest entry `compose-service-networks` + проводка в check_project.py + unit-тест | manifest + check + test | Coder | T4 | 4 | AC3 |
| TASK-6 | Verb `parity-db`: core/internal/deploy/parity_db.py + тонкий фасад + Makefile + generated-каскад + unit-тест | module + facade + Makefile + manifests | Coder | — | 5 | AC5 |
| TASK-8 | Легализация template-ai-project в скаффолд-канале: choices += ai-project, регистрация в template-manifest, monitoring-ветка, needs.database default | project_scaffolder + template-manifest + test | Coder | T1 | 4 | AC6 |
| TASK-7 | Редеплой пилотов на ноду: hook-конвергенция БД, render-vhosts (новые домены), probe pgbouncer/litellm, healthcheck | живой стек + отчёт прогона | Sysadmin | T2,T3,T4 | 4 | AC1,AC4 |

**Critical path:** TASK-8 → TASK-1 → TASK-2 → TASK-7 (инцидент + разблокировка W7 T0);
TASK-4 → TASK-5 (класс). TASK-8 зависит от T1 только логически (сначала фикс каркаса, потом
канал) — файлы не пересекаются, но E2E-проверка AC6 требует обоих.

**Детали задач:**

- **TASK-1.** compose шаблона: networks сервиса = `{{PROJECT_NAME}}-net + shared-db-net + hermes-agent-net + proxy-net(aliases)` (external, фиксированные имена — канон postgres/litellm модулей); `DATABASE_URL=${PLATFORM_POSTGRES_DSN}` (прецедент macos-local:38); labels `platform.domain={{DOMAIN}}` остаются. AGENTS.md шаблона: контракт «env DATABASE_URL — маппинг PLATFORM_POSTGRES_DSN из .env.platform; LLM_BASE_URL ← PLATFORM_LITELLM_URL». **не** трогать: Dockerfile, Makefile, .github (GENERATED-дрифт вне скоупа).
- **TASK-2.** Пилоты патчатся напрямую (инцидент), GENERATED-пометка сохраняется — будущий sync-канал ai-project перегенерирует из исправленного шаблона TASK-1. managers-bot: сохраняю volume .sops.yaml/secrets.yaml; client-bot: без sops-volumes (их нет сейчас). Сети: own-net + shared-db-net + hermes-agent-net + proxy-net. shared-cache-net НЕ добавлять (боты redis не потребляют — least privilege; локальный all-three attach — оверрайд macos-стека).
- **TASK-3.** `name`: client-bot → **asi-faq**, managers-bot → **managers-bot** (СОВПАДЕНИЕ с существующими БД/ролами — иначе хук создаст дубли, F6/F7). `type: typescript`; `target_node: asi-team-vps` (уточнить на исполнении: node-configs/asi-group/ + make project-status); `needs: {database: true, domain: faq.asiteam.ru | managers.asiteam.ru, expose: true}` (домены по wildcard-паттерну roadmap — владелец подтверждает при render-vhosts); `monitoring: {metrics: false, logs_retention: 7d, alerting: false, dashboard: false}` (уровень пилотов); `quality.level: baseline` (эскалация full — отдельное решение владельца, канон «full — только по явному согласию»). Затем `make project-sync-env` per project (GENERATED .env.platform перезапишется, пароли — из .platform-db.env хуком при деплое).
- **TASK-4.** Анализатор (единственный механизм — dual-mechanism ban §1.10): вход = parsed compose + ключи .env.platform + имена secret-definitions.yaml + needs манифеста + SoT provides из platform-infra.yaml. Правила: (a) `service-network-coverage` — для каждого ${PLATFORM_*_DSN|_URL} в environment/args сервиса: networks(svc) ∩ provides.networks(svc) ≠ ∅, иначе L1 violation; (b) `env-var-unresolved` — каждый ${VAR} без `:-`-дефолта резолвится из .env.platform ∪ secret-definitions, иначе L1; (c) `db-consumed-not-declared` — PLATFORM_POSTGRES_DSN потребляется ⇔ needs.database объявлен (ловит и ***-DSN-класс). verify_contracts: 2-3 L1-чека вызывают анализатор, severity=block на всех уровнях (L1-класс платформенной безопасности). **Негативный тест (R5):** инцидентный compose client-bot ДО фикса (только proxy-net + ${DATABASE_URL}) → violation по обоим правилам.
- **TASK-5.** practices_manifest.yaml: check id `compose-service-networks`, class L1, languages: [all], channel K1, auto_fix: false; schema_validator канона соблюсти. check_project.py: вызов shared-анализатора (НЕ копия логики), вывод `[PRACTICES:BLOCK]` в едином формате варнингов. practices.lock пилотов перегенерируется при следующем project-sync-practices.
- **TASK-6.** Языковая политика: логика в Python (parity_db.py, DI CommandRunner — прецедент on_project_deploy), entrypoint — тонкий фасад <30 LOC. CLI: `parity-db ACTION=<create|drop> PROJECT=<name> NODE=<node>`; create: `CREATE DATABASE "parity_<project>"` + `CREATE ROLE "parity_<project>_user" LOGIN PASSWORD '<autogen>'` + полные GRANT ВНУТРИ parity-БД only; idempotent (already-exists → DSN re-print); drop: DROP DATABASE + DROP ROLE. DSN в stdout единственной строкой (машинно-читаемо для parity-инструментария ai-project), остальное — IMP-логи в stderr. Generated-каскад: `make generate-manifests` (entrypoint-manifest.yaml#allowed_verbs/gates + глоссарий + core/AGENTS.md generated-секции) — Commit policy: каскад в том же feat-коммите.
- **TASK-7.** Порядок: redeploy обоих пилотов (`make deploy-project PROJECT=projects/asi-group/<n> NODE=asi-team-vps`) → хук конвергирует БД (already-exists skip проверяет лог IMP:8) → render-vhosts для faq/managers.asiteam.ru → probe: `docker exec <svc> wget -qO- http://pgbouncer:6432` (TCP-resolve) + litellm:4000 → `make healthcheck NODE=` + e2e-verify. rollback: snapshot DeployHistory штатный. **Координация W7:** легаси-пилоты снимаются с ноды `make remove-project` после деплоя W7-семьи (D1 W7: FAQ-бот удаляется; managers эволюционирует из asi-managers) — фикс 019 закрывает инцидент на переходный период, decommission — триггер «W7 T4 зелёный».
- **TASK-8.** Скаффолд-канал (F13/F14/F15): (a) `project_scaffolder.py` — choices `{"frontend", "backend", "ai-project"}`; `needs.database` default = имя проекта для ai-project (боты ВСЕГДА хранят стейт в Postgres — kernel-контракт; переопределяется флагом `--database=false` при необходимости); monitoring-ветка для ptype=ai-project: `{metrics: true, metrics_port: 8787, logs_retention: 7d, alerting: false, dashboard: false}` (kernel отдаёт /metrics на HEALTH_PORT — прецедент W3 AGENTS.md); (b) `core/templates/template-manifest.yaml` — entry по образцу template-backend:203 (template: ../../templates/template-ai-project/) → templates-check покрытие; (c) E2E-смоук: `make new-project NAME=w7-smoke TEMPLATE=ai-project` в tmp → полный ai-platform.yaml + исправленный compose + practices.lock → удалить. Открывает W7 T0 (§3.6 W7: make new-project + template-ai-project).

## $PARALLEL_GROUPS

### Wave 1 (независимые, нет общих файлов)
- Tasks: TASK-1, TASK-3, TASK-4, TASK-6, TASK-8
- Command: `coder Read .ai/plans/019-asi-group-pilot-integration/01-DevPlan.md, implement Wave 1: TASK-1, TASK-3, TASK-4, TASK-6, TASK-8`

### Wave 2 (зависимости из Wave 1)
- Tasks: TASK-2 (← T1), TASK-5 (← T4); файлы TASK-2 ∩ TASK-5 = ∅
- Command: `coder Read .ai/plans/019-asi-group-pilot-integration/01-DevPlan.md, implement Wave 2: TASK-2, TASK-5`

### Wave 3 (операционная, зависит от Wave 2)
- Tasks: TASK-7 (← T2, T3, T4; гейт K3 уже в дереве — деплой проходит через новый L1)
- Command: `sysadmin Read .ai/plans/019-asi-group-pilot-integration/01-DevPlan.md, execute TASK-7`

### Внешняя волна (кросс-репо, ai-project)
- После Wave 3: W7 T0 (эталон managers) стартует из легального скаффолда — координация владельца;
  TASK-6 (parity-db) обязателен до W7 T4 (P10 ×4).

## Acceptance Criteria (сводная таблица)

| AC | Критерий | Проверка | TASK |
|----|----------|----------|------|
| AC1 | Production-compose пилотов: 4 сети, DATABASE_URL=${PLATFORM_POSTGRES_DSN}; на ноде pgbouncer+litellm резолвятся, healthcheck зелёный | `docker compose config` локально; TASK-7 probe + healthcheck | T2, T7 |
| AC2 | Шаблон генерирует compose с сетями и DSN-маппингом | test_gate_template_ai_project_networks (зелёный) | T1 |
| AC3 | Деплой compose без сети провайдера / с неразрешимым ${VAR} / с PG-потреблением без needs.database — БЛОК на K1 и K3 | негативные тесты на инцидентном инпуте (R5) | T4, T5 |
| AC4 | needs.database у пилотов; хук skip already-exists, GRANT-converge, .env.platform regen; дублей БД/ролей НЕТ | лог хука IMP:8 «already exists — skipping» при деплое TASK-7 | T3, T7 |
| AC5 | parity-db create→DSN→прогоны→drop чисто; проектные роли без CREATEDB; повторный create idempotent | test_parity_db + живой прогон parity (ai-project) | T6 |
| AC6 | make new-project NAME=x TEMPLATE=ai-project: полный манифест (needs.database), исправленный compose, practices.lock; W7 T0 разблокирован | test_scaffold_ai_project + E2E-смоук в tmp | T8 (+T1) |

## File Manifest

| Файл | Действие | TASK |
|------|----------|------|
| templates/template-ai-project/docker-compose.yml | CHG | T1 |
| templates/template-ai-project/AGENTS.md | CHG | T1 |
| projects/asi-group/client-bot/docker-compose.yml | CHG | T2 |
| projects/asi-group/managers-bot/docker-compose.yml | CHG | T2 |
| projects/asi-group/client-bot/ai-platform.yaml | CHG | T3 |
| projects/asi-group/managers-bot/ai-platform.yaml | CHG | T3 |
| projects/asi-group/{client-bot,managers-bot}/.env.platform | REGEN (sync-env) | T3 |
| core/internal/shared/compose_service_contract.py | NEW | T4 |
| core/internal/deploy/verify_contracts.py | CHG | T4 |
| tests/gates/test_gate_service_network_coverage.py | NEW | T4 |
| tests/gates/test_gate_template_ai_project_networks.py | NEW | T4 |
| core/internal/practices/practices_manifest.yaml | CHG | T5 |
| core/internal/practices/check_project.py | CHG | T5 |
| tests/unit/practices/test_check_compose_networks.py | NEW | T5 |
| core/internal/deploy/parity_db.py | NEW | T6 |
| core/entrypoints/parity-db.sh | NEW | T6 |
| Makefile | CHG | T6 |
| core/internal/scaffold/project_scaffolder.py | CHG | T8 |
| core/templates/template-manifest.yaml | CHG | T8 |
| tests/unit/scaffold/test_scaffold_ai_project.py | NEW | T8 |
| entrypoint-manifest.yaml, глоссарий, core/AGENTS.md (generated) | REGEN (`make generate-manifests`) | T6 |
| tests/unit/deploy/test_parity_db.py | NEW | T6 |

## Design Decisions

### @rationale Q: почему контракт сетей = пересечение networks(svc) ∩ provides.networks(SoT), а не список «обязательных сетей»? A: статический анализ не знает DNS-рантайм; SoT platform-infra.yaml#provides (DR-M4) уже канонизирован parity-гейтом (⊆, REF-0017); пересечение — минимальная проверяемая форма того же контракта, без второго списка сетей (knowledge dedup).

### @rationale Q: почему один shared-анализатор, а не два чека? A: dual-mechanism = drift-ускоритель (§1.10): K3 (verify_contracts) и K1 (project-check) обязаны давать идентичный вердикт на одном compose — иначе push-рубеж и deploy-рубеж разойдутся; единственный механизм в core/internal/shared/ (критерии размещения — shared/AGENTS.md).

### @rationale Q: почему патч пилотов напрямую, а не ожидание sync-канала ai-project? A: инцидент закрывается немедленно; sync-канал пилотов — практики ai-project ОС (вне git платформы), исправленный шаблон TASK-1 обеспечивает корректность будущей регенерации; GENERATED-пометка сохраняется — правка легитимна как hotfix с фиксом источника.

### @rationale Q: почему name пилотов = asi-faq / managers-bot (≠ имени каталога client-bot)? A: хук идемпотентен ТОЛЬКО при совпадении имён БД/ролей (already-exists skip); имя client-bot создало бы client-bot_db — дубль и дрейф реестра; фактура F6.

### @rationale Q: почему shared-cache-net НЕ добавляется пилотам? A: боты redis не потребляют (kernel kv в Postgres); least privilege — blast-radius сети не расширяется без потребителя; локальный all-three attach macos-local — оверрайд dev-стека, не образец для production.

### @rationale Q: почему parity-БД отдельным verb, а не расширением postgres-хука? A: parity-прогон — не project-deploy (хук триггерится деплоем и ждёт needs.database в манифесте проекта); parity-БД по определению временная и вне lifecycle проектов; отдельный путь честнее флага-костыля в хуке.

# 💼 TRAP[BUSINESS] · 2026-08-31 · HI · Изоляция проектных ролей PG — принципиальна (CONNECT + CREATE,USAGE на своей БД и НИЧЕГО больше); parity-БД решается привилегированным платформенным путём, НЕ выдачей CREATEDB проектным ролям · Source: owner (выбор «Платформенная parity-БД») · Risk: CREATEDB у проектных ролей = ослабление L1-изоляции (роль создаёт БД вне needs-контракта, реестр слеп)

### @rationale Q: почему легализация скаффолда (TASK-8) в платформенном плане, а не усилиями ai-project? A: скаффолд-канал — собственность платформы (инвариант «make new-project — единственный способ создания проекта»); без choices/manifest-регистрации W7 §3.6 физически не исполним, а обходной ручной путь — точный сценарий инцидента пилотов; needs.database default=name переносит провижининг БД из рук агентов в хук — устранение первопричины, а не симптома.

## $TEST_SPEC

| Test file | Test function | Scenario | Module under test |
|-----------|---------------|----------|-------------------|
| tests/gates/test_gate_service_network_coverage.py | test_gate_network_coverage_blocks_db_without_shared_db_net | Инцидентный инпут: compose «только proxy-net» + `DATABASE_URL=${DATABASE_URL}` → violations по обоим правилам | verify_contracts (L1) |
| tests/gates/test_gate_service_network_coverage.py | test_gate_network_coverage_passes_canonical_compose | Исправленный compose пилота / template-backend → 0 findings | verify_contracts (L1) |
| tests/gates/test_gate_service_network_coverage.py | test_gate_db_consumed_without_needs_blocks | PLATFORM_POSTGRES_DSN в environment, needs.database отсутствует → L1 violation | verify_contracts (L1) |
| tests/gates/test_gate_service_network_coverage.py | test_gate_env_var_unresolved_blocks | ${VAR} без дефолта, вне .env.platform и secret-definitions → L1 violation | verify_contracts (L1) |
| tests/gates/test_gate_template_ai_project_networks.py | test_template_ai_project_declares_provider_networks | Шаблонный compose ⊇ сети потребляемых сервисов (platform-infra#provides, SoT-парити) | templates/template-ai-project |
| tests/unit/deploy/test_parity_db.py | test_parity_db_create_prints_dsn_idempotent | DI fake-runner: create → DSN в stdout; повторный create → already-exists skip, DSN повторно | parity_db |
| tests/unit/deploy/test_parity_db.py | test_parity_db_drop_removes_db_and_role | drop → DROP DATABASE + DROP ROLE; повторный drop → exit 0 без ошибки | parity_db |
| tests/unit/scaffold/test_scaffold_ai_project.py | test_scaffold_accepts_ai_project_template | choices: --template=ai-project проходит валидацию (негатив: unknown → ERROR) | project_scaffolder |
| tests/unit/scaffold/test_scaffold_ai_project.py | test_gen_yaml_ai_project_needs_database_default | gen_ai_platform_yaml(ptype=ai-project, database default=name) → needs.database, monitoring {metrics: true, metrics_port: 8787} | scaffold_helpers |
| tests/unit/practices/test_check_compose_networks.py | test_k1_check_blocks_incident_compose | K1-чек на инцидентном compose → [PRACTICES:BLOCK], shared-анализатор переиспользуется (не дублируется) | check_project + compose_service_contract |

## Debt Intake (Step 0 — аудит TRAP/DEBT затронутых зон)

| Находка | Классификация | Решение |
|---|---|---|
| gen_env_platform эмитит PLATFORM_POSTGRES_DSN с литеральным `***` проектам без needs.database (roadmap) — вводящий в заблуждение артефакт | DEFER | Чек `db-consumed-not-declared` (TASK-4) конвертирует риск в enforce-контракт; косметика `***` — ревизия при первом проекте, реально потребляющем DSN без needs |
| Sync-источник шаблона в монорепо ai-project (`patches/w3-template-ai-project/`) отстанет от исправленного платформенного шаблона (TASK-1) — кросс-репо дрейф | DEFER | Триггер: следующий релиз/волна ai-project — зеркалировать фикс в патч-копию; платформа — собственник шаблонов (канон AGENTS.md), монорепо — потребитель. **Актуализация Rev 2:** W7 §3.6 идёт через платформенный make new-project → монорепо-копия вторична, дрейф некритичен для W7 |
| macos-local compose пилотов содержит shared-cache-net без потребителя redis (dev-оверрайд) | DEFER | LO; не блокирует production; ревизия при чистке локальных оверрайдов |
| Легаси-пилоты (asi-faq, asi-managers) снимаются с ноды при деплое W7-семьи (D1 W7) | DEFER | Триггер: W7 T4 зелёный → `make remove-project` ×2; фикс 019 закрывает инцидент на переходный период |
| Найденные в коде TRAP (postgres hook already-exists TRAP[BUG], platform-infra DR-M4/TRAP[DECISION]) — учтены, новых не заводить | INFORM | — |

## Configuration Drift (Step 1.7)

Новых конфиг-значений план не вводит (сети/URL — существующие SoT: platform-infra.yaml#provides; allowlist уже полный — F11). Каскад TASK-6 (новый verb): Makefile → entrypoint-manifest.yaml → глоссарий → core/AGENTS.md generated-секции — все через `make generate-manifests`, divergence блокируется `make check MARKER=check-manifests` (инвариант 11). Ручных дубликатов НЕ создавать.

## Contracts (Step 1.9)

**parity-db CLI:** `make parity-db ACTION=<create|drop> PROJECT=<name> NODE=<node>` →
create: stdout = одна строка `postgresql://parity_<project>_user:<password>@pgbouncer:6432/parity_<project>` (exit 0; already-exists → re-print); drop: exit 0 (idempotent, отсутствие — не ошибка); ошибки → non-zero + stderr IMP-лог. Права parity-роли: ALL на parity-БД only, CONNECT/USAGE вне её — NOTHING.

**L1-чек (verify_contracts):** вход — parsed compose + env-ключи + secret-имена + needs; выход — `_RawFinding` с rule ∈ {service-network-coverage, env-var-unresolved, db-consumed-not-declared}; severity L1-block на всех practices-уровнях (класс платформенной безопасности — канон K3).

**K1-чек (project-check):** тот же анализатор; вывод `[PRACTICES:BLOCK]` (единый формат варнингов канона практик).

## Next Steps

### Wave 1
```
Use coder role and read .ai/plans/019-asi-group-pilot-integration/01-DevPlan.md, implement Wave 1: TASK-1, TASK-3, TASK-4, TASK-6, TASK-8. Верификация per-task: make check TEST_FILE=<файл>; фикс-цикл make check (до чистоты); финально make agent-check.
```

### Wave 2
```
Use coder role and read .ai/plans/019-asi-group-pilot-integration/01-DevPlan.md, implement Wave 2: TASK-2, TASK-5. Верификация per-task: make check TEST_FILE=<файл>; фикс-цикл make check (до чистоты); финально make agent-check.
```

### Wave 3
```
Use sysadmin role and read .ai/plans/019-asi-group-pilot-integration/01-DevPlan.md, execute TASK-7 (редеплой пилотов, конвергенция БД, render-vhosts, probe pgbouncer/litellm, healthcheck). Отчёт — 03-StatusReport.md в папке плана.
```

**Commit policy (U-83):** ≤2 коммита — `docs(019): 01-DevPlan — asi-group pilot integration` и `feat(019): implementation — template fix + scaffold legalization + L1 gate + parity-db + pilots` (по волнам — норма).

**W7-координация (после Wave 3):** платформа сняла блокеры W7 T0 (легальный скаффолд) и W7 T4 (parity-db); уведомить владельца ai-project — старт W7 возможен; decommission легаси-пилотов — после W7 T4 (Debt Intake, строка 4).

$END_DEVPLAN
