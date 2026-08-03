# 133-project-platform-contract — 03-VerificationReport.md

$START_VERIFICATION_REPORT

$ARTIFACT_CONTRACT
PURPOSE:               Верифицировать реализацию DevPlan 133 (AI-PLATFORM.md контракт + шаред-доступ к БД): unit-тесты, e2e на локальном стеке, make check, make gate MODE=fast, локальный e2e-сценарий (status-page toggle + DB-провижининг).
DESCRIPTION:           W1-W3 реализованы (код + тесты + правки 3 проектов tronyx-lab). W4: 28 unit-тестов + 3 e2e-теста зелёные; make check и make gate MODE=fast — все проверки зелёные КРОМЕ одного предсуществующего блокера (test_debt_registry_no_trivial_entries — in-flight правки .ai/debt/001 из плана 127, НЕ вызван реализацией 133).
RATIONALE:             Отчёт фиксирует вердикт по 6 acceptance-критериям DevPlan 133 с доказательствами (тесты, gate-статусы, e2e-сценарий) и документирует единственный незакрытый блокер с владельцем.
ACCEPTANCE_CRITERIA:   (1) AI-PLATFORM.md в 3 проектах, закоммичен; (2) docs/platform-project-contract.md + навигация; (3) локальный e2e: БД+роль+GRANT, psql через pgbouncer, auth failure для неизвестной роли; (4) password-injection в .env.platform; (5) make check + make gate; (6) регрессии зелёные.
IMPLEMENTS:            02-DevPlan.md (133-project-platform-contract) — волны W1-W4.
IMPACTS:               core/modules/postgres/{docker-compose.base.yml, hooks/on_project_deploy.py}; core/internal/scaffold/{gen_project_platform_md.py (new), gen_env_platform.py, scaffold_helpers.py, project_scaffolder.py, project_adopter.py}; core/internal/bootstrap/converge/projects.py; core/entrypoints/scaffold.sh; makefiles/scaffold.mk; docs/{platform-project-contract.md (new), projects-root-AGENTS.md}; AGENTS.md (root + core); core/entrypoint-manifest.yaml; pyproject.toml; tests/{unit×3, e2e×1, test_pgbouncer_static, test_cross_layer_imports, gates×1}; репо tronyx-lab/{botanika, dance-site, tronyx-site} (AI-PLATFORM.md).
REQUIRES:              Локальный docker-стек (postgres+pgbouncer, wildcard-миграция выполнена); ~/projects/tronyx-lab (3 проекта); доступ на запись в репо проектов (коммиты выполнены).
$END_ARTIFACT_CONTRACT

## Вердикт

**PASS с одним предсуществующим блокером** (не связан с реализацией 133).

| Критерий (AC) | Статус | Доказательство |
|---------------|--------|----------------|
| AC1: AI-PLATFORM.md в 3 проектах, закоммичен, URL канона + GENERATED-секция | ✅ | Файлы созданы генератором, коммиты: botanika `466e2ea`, dance-site `aefce72`, tronyx-site `1f3bbaf` («feat: add AI-PLATFORM.md platform contract reference») |
| AC2: docs/platform-project-contract.md — канон; навигация root AGENTS.md | ✅ | Файл создан (MODULE_CONTRACT, окружение, каналы доставки, DO NOT, приоритет); строки в root AGENTS.md §Навигация |
| AC3: локальный e2e: БД+роль+GRANT; psql через pgbouncer:6432; неизвестная роль → auth failure (не «no such database») | ✅ | `tests/e2e/test_shared_db_access.py` — 3/3 passed (полный цикл, R5-negative, идемпотентность) |
| AC4: .env.platform перегенерация с реальным паролем (password-injection) | ✅ | `test_dsn_with_credentials_injects_password` + e2e assert (пароль в DSN, `***` отсутствует) |
| AC5: make check + make gate MODE=fast зелёные; unit-тесты | ⚠️ | 455/456 gate-тестов PASS; единственный FAIL — предсуществующий `test_debt_registry_no_trivial_entries` (см. §Блокер) |
| AC6: регрессии (on_project_deploy, gen_env_platform, converge, scaffold) | ✅ | test_converge_projects + test_project_adopter + test_converge_infra: 31/31; on_project_deploy 15/15; gen_env_platform 7/7; gen_project_platform_md 6/6 |

## 1. Реализация (волны W1-W3)

| Волна | Компонент | Файл |
|-------|-----------|------|
| W1 | Канон окружения проекта | `docs/platform-project-contract.md` (новый) |
| W1 | Генератор AI-PLATFORM.md (модуль + CLI, atomic write, GENERATED-маркеры, graceful degradation) | `core/internal/scaffold/gen_project_platform_md.py` (новый) |
| W1 | Wrapper scaffold-слоя | `scaffold_helpers.gen_project_platform_md` |
| W1 | Интеграция new-project (Step 5) / adopt-project (Step 5) | `project_scaffolder.py`, `project_adopter.py` |
| W1 | Converge R3 (AI-PLATFORM.md if-missing) | `converge/projects.py` (+`reconcile_project_platform_md`) |
| W2 | pgbouncer wildcard `*` (DATABASE_URLS без имени БД, D5) | `docker-compose.base.yml` |
| W2 | Хук: роль `${project}_user` + пароль + GRANT CONNECT/CREATE,USAGE (D6) + `.platform-db.env` (0600) + regen .env.platform | `hooks/on_project_deploy.py` |
| W2 | Password-injection (`credentials` в generate(), CLI `--project-dir/--credentials-file`) | `gen_env_platform.py` |
| W2 | sync-env → .env.platform + AI-PLATFORM.md (CLI-оркестрация), Makefile `PROJECT_DIR` | `scaffold.sh`, `makefiles/scaffold.mk` |
| W3 | AI-PLATFORM.md в botanika/dance-site/tronyx-site + коммиты | репо tronyx-lab (3 коммита) |
| W3 | Документация/реестры | `docs/projects-root-AGENTS.md`, `entrypoint-manifest.yaml`, root AGENTS.md (глоссарий регенерирован) |

## 2. Тесты

### Unit (28 passed)

| Файл | Сценарии |
|------|----------|
| `tests/unit/test_gen_project_platform_md.py` (6) | статик-рендер + маркеры; per-node секция (enabled-модули/DSN/сети/needs); регенерация = section-update без дублей (статик-правки сохраняются); skip/force; graceful (missing files); CLI |
| `tests/unit/test_on_project_deploy.py` (15) | базовые D65-сценарии (без изменений) + W2: роль создана (CREATE ROLE+GRANT×2+credentials 0600), роль существует (идемпотентность, пароль не меняется), роль без credentials (skip), regen .env.platform при первом создании; негативы (invalid db_name, psql fail, missing password); R5-negative (wildcard, 0 захардкоженных БД) |
| `tests/unit/test_gen_env_platform.py` (7) | контракт структуры; DSN с `***` без credentials (обратная совместимость); DSN с паролем (`***` → реальный пароль); load_credentials (missing/parse); CLI; CLI `--project-dir` (resolv + инъекция) |

### E2E локальный стек (3 passed, маркер `integration` + `requires_docker`)

`tests/e2e/test_shared_db_access.py`:
- **Полный цикл:** needs.database → хук (БД+роль+GRANT+`.platform-db.env` 0600+regen .env.platform) → assert БД/роль/credentials → `psql -h pgbouncer -p 6432 -U <role> -d <db> SELECT 1` = 1 → cleanup (REVOKE → DROP ... WITH (FORCE));
- **R5-negative:** несуществующая роль → `FATAL: no such user` (auth failure pgbouncer), а НЕ «no such database» (баг жёсткого списка закрыт, D5);
- **Идемпотентность:** повторный деплой → пароль роли не меняется, credentials стабильны.

Запуск: `python3 -m pytest tests/e2e/test_shared_db_access.py -m integration` — 3/3 PASS.

### Регрессии (31 passed)

`test_converge_projects.py` + `test_project_adopter.py` + `test_converge_infra.py` — 31/31 PASS (R3-расширение и adopter Step 5 не сломали существующие сценарии).

## 3. make check / make gate MODE=fast

| Проверка | Результат |
|----------|-----------|
| `make check` (диагностический прогон) | Все чеки зелёные, КРОМЕ: (а) `test_debt_registry_no_trivial_entries` — предсуществующий блокер (см. §Блокер); (б) `test_parse_benchmark_1000_vars` — load-flake (standalone 3/3 PASS, 0.14s; порог 50ms чувствителен к параллельной нагрузке, secrets_env_parser не изменялся) |
| `make gate MODE=fast` | 455/456 gate-тестов PASS; FAIL — только `test_debt_registry_no_trivial_entries` (предсуществующий). pre-commit/ruff/bandit/doxygen/check-manifests — PASS. `make doxygen-check`: 0 warnings |

## 4. Локальный e2e-сценарий (W4.2)

1. **Модуль toggle status-page:** `docker stop status-page` → `docker start status-page` → healthy (канон: running + healthy) ✅
2. **DB-провижининг полный цикл** (покрыт e2e-тестом на живом стеке): needs.database → хук → БД `e2e_dbproj_db` + роль `e2e_dbproj_user` + GRANTs → `.platform-db.env` (0600) → `.env.platform` с реальным паролем → `SELECT 1` через pgbouncer:6432 → cleanup (0 остатков: `SELECT count(*) FROM pg_database WHERE datname LIKE 'e2e%'` = 0) ✅

## 5. pgbouncer wildcard-миграция (D5)

- `docker compose up -d --force-recreate pgbouncer` → `pgbouncer.ini`: `* = host=postgres port=5432 auth_user=postgres` (одноразовая миграция стека);
- Регрессия существующих БД: platform/litellm/langfuse → `SELECT 1` через pgbouncer:6432 = 1/1/1 ✅.

## 6. Блокер (предсуществующий, НЕ вызван реализацией 133)

`test_debt_registry_no_trivial_entries` (AC3 SHELL-RESIDUAL, LOC ≤ 200) — RED из-за in-flight правок `.ai/debt/001-Strangler-Fig-Closeout.md` (сессия «закрыть все долги», планы 127-131; файл был изменён ДО начала работ по 133, коммитов нет):

- Строки S4-S7 помечены FIXED/SUPERSEDED с актуальными LOC (25/23/110/26), но **не удалены** из §SHELL-RESIDUAL — гейт требует LOC > 200 для каждой строки секции.
- Владелец: сессия плана 127 (Debt-планирование) / 131-debt-cleanup. Корректное закрытие: удалить строки S4-S7 из таблицы (закрытые долги не остаются в SHELL-RESIDUAL).
- На HEAD (до in-flight правок) LOC строк были 223/218/215/206 (>200) — гейт был зелёным; RED введён самой in-flight сессией.
- 133 не трогал `.ai/debt/*` (изменения 133 — только в core/, docs/, tests/, makefiles/, pyproject.toml).

## 7. Коммиты (ai-platform)

| Коммит | Содержание |
|--------|-----------|
| `6795e4c` docs(133) | DevPlan + Brief (планирование) |
| `c51604d` feat(133) | Генератор W1 + scaffold-интеграция + R3; W2: pgbouncer wildcard, хук ролей/credentials, password-injection; W3: manifest/AGENTS/docs; тесты (unit+e2e) |
| `b9688a7`+`e8ddc95`+`7636ae8`+`8c8e8df`+`c38d76c`+`57ac15c` style/docs(133) | ruff format, region-баланс, doc-headers, bandit nosec, doxygen zero-warnings |
| `9929258` style(126) | pre-existing ruff-format drift в provisioner.py/test_provisioner_volumes_owner.py (коммит d90dd9a, блокировал gate для всех — отформатирован) |

Проекты tronyx-lab: `466e2ea`, `aefce72`, `1f3bbaf` (по 1 коммиту на проект, DevPlan W3.2).

## 8. Известные ограничения

- Ротация пароля роли — вне скоупа (существующий TRAP[DEBT] 2026-07-17, POSTGRES_PASSWORD rotation);
- `.env.platform` regen хуком — через CLI subprocess (TRAP[DECISION] в on_project_deploy.py: modules→internal импорт запрещён cross-layer allowlist «не растёт»);
- `test_parse_benchmark_1000_vars` — флаки под параллельной нагрузкой (порог 50ms), standalone стабилен; зафиксирован в долг test-env-leak-and-flakes (Rev 2026-08-09).

$END_VERIFICATION_REPORT
