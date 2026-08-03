# 133-project-platform-contract — 01-Brief.md

$START_BRIEF

$ARTIFACT_CONTRACT
PURPOSE:               Дать агенту, работающему внутри репозитория подключённого проекта, явный контракт платформы: что платформа предоставляет в принципе, что реально включено на его ноде, какие сервисы выделены его проекту и какие границы нельзя переступать — через файл AI-PLATFORM.md в каждом проекте, являющийся ссылкой на канонический документ инструкций внутри платформы. Параллельно — починить фактическое предоставление шаред-доступа к БД по запросу проекта (needs.database), обнаруженное эмпирическими тестами.
DESCRIPTION:           2 направления. (1) Файл-контракт: новый канонический документ docs/platform-project-contract.md (инструкции платформы) + гибридный файл AI-PLATFORM.md в каждом проекте (статичная часть: рамки, ссылки на канон, DO NOT, приоритет инструкций; генерируемая секция GENERATED:START/END: per-node enabled-модули, сервисы проекта с DSN/URL, домены, сети). Генератор — Python (core/internal/scaffold/gen_project_platform_md.py), интегрируется в new-project / adopt-project / project-sync-env / converge R3. (2) Фикс шаред-доступа к БД: pgbouncer переводится на wildcard-маршрутизацию (`* = host=postgres auth_user=postgres`, делегация auth в postgres через auth_query) вместо жёсткого списка DATABASE_URLS; хук on_project_deploy расширяется: создание роли `${project}_user` + пароль + GRANT + credentials-файл .platform-db.env на ноде; gen_env_platform подставляет реальный пароль в DSN при наличии credentials. Файл-контракт описывает ФАКТИЧЕСКОЕ окружение, а не aspirational-DSN.
RATIONALE:             Эмпирические тесты 2026-08-03 на локальном стеке: модули вкл/выкл работают (status-page stateless, minio stateful с сохранением volume); needs.database создаёт БД, но (а) pgbouncer не маршрутизирует её (FATAL: no such database — жёсткий список в pgbouncer.ini), (б) роль ${NAME}_user не существует — DSN-шаблон фиктивен. Решения пользователя (суперпозиция 2026-08-03): формат C (гибрид: статичный указатель + генерируемая секция), фикс БД — в этом плане (B), имя файла — AI-PLATFORM.md. Паттерн generated-секций каноничен (глоссарий, canon_table — инвариант 11). Wildcard-маршрутизация pgbouncer поддерживается entrypoint'ом edoburu/pgbouncer (generate_config_db_entry: `${DB_NAME:-*} = host=... auth_user=...`).
ACCEPTANCE_CRITERIA:   (1) AI-PLATFORM.md сгенерирован и закоммичен во все 3 проекта tronyx-lab (botanika, dance-site, tronyx-site); содержит ссылку на канон платформы и актуальную per-node секцию. (2) Канонический docs/platform-project-contract.md существует и ссылается из AI-PLATFORM.md. (3) Локальный e2e: needs.database → хук создаёт БД + роль + GRANT; подключение через pgbouncer:6432 с ролью проекта работает; без роли — auth fail (не «no such database»). (4) .env.platform на ноде после провижининга содержит реальный пароль роли в DSN. (5) make check + make gate MODE=fast зелёные; unit-тесты генератора и хука покрывают идемпотентность и negative-сценарий найденного бага (R5). (6) Регрессия: существующие тесты on_project_deploy, gen_env_platform, converge зелёные.
IMPLEMENTS:            Требование пользователя 2026-08-03: «потестируй включение/выключение модулей и шаред-доступ к БД; придумай файл-ссылку на инструкции платформы во всех проектах tronyx-lab; раскрой суперпозицию и напиши девплан». Коллапс: C + B + имя AI-PLATFORM.md.
IMPACTS:               core/modules/postgres/docker-compose.base.yml (DATABASE_URLS → wildcard); core/modules/postgres/hooks/on_project_deploy.py (роль+пароль+GRANT+credentials); core/internal/scaffold/gen_env_platform.py (password-injection); core/internal/scaffold/ (+новый gen_project_platform_md.py, scaffold_helpers, project_scaffolder, project_adopter); core/internal/bootstrap/converge/projects.py (R3 if-missing); docs/platform-project-contract.md (новый канон); docs/projects-root-AGENTS.md (упоминание); entrypoint-manifest.yaml (описание project-sync-env); репо tronyx-lab/botanika, tronyx-lab/dance-site, tronyx-lab/tronyx-site (новый файл AI-PLATFORM.md, 1 коммит на проект); tests/ (unit + e2e).
REQUIRES:              Локальный docker-стек (поднят, 24 healthy-контейнера, проверено 2026-08-03); репо проектов tronyx-lab в ~/projects/tronyx-lab/; решения пользователя по коллапсу суперпозиции (получены); подтверждение по деталям дизайна (D1-D5 в DevPlan — приняты по умолчанию, GUIDED-режим).
$END_ARTIFACT_CONTRACT

## 1. Контекст и доказательства

### 1.1 Эмпирические результаты тестов (2026-08-03, локальный стек)

| Тест | Результат | Вывод |
|------|-----------|-------|
| Модуль вкл/выкл `status-page` (stateless) | stop+rm → контейнер удалён; up -d → `Up (healthy)` | Механизм профилей compose корректен |
| Модуль вкл/выкл `minio` (stateful) | stop+rm → контейнер удалён; volumes `ai-platform_minio-data`/`minio_minio-data` сохранены; up → `Up (healthy)` | Данные переживают выключение |
| `needs.database: provtest_db` → хук auto_create_db | БД создана | Хук работает |
| Прямой доступ `postgres:5432` (shared-db-net, md5, POSTGRES_PASSWORD) | работает | Шаред-доступ = общий суперпользователь |
| **Доступ через pgbouncer:6432** (канон DSN) | **FATAL: no such database: provtest_db** | pgbouncer.ini с жёстким списком (platform/litellm/langfuse), проектные БД не маршрутизируются |
| Роль `tronyx-site_user` (из DSN-шаблона) | не существует | DSN-шаблон `postgresql://${NAME}_user:***@pgbouncer:6432/${NAME}_db` фиктивен |

### 1.2 Текущее состояние контракта для агента проекта

- `.env.platform` — генерируется из platform-env.yaml `provides` (PLATFORM_* переменные, DSN с `***`);
- Генерируемый AGENTS.md проекта (DD13, ≤60 строк): «Platform provides» + ссылка на `.env.platform`;
- `~/projects/AGENTS.md` = symlink на `docs/projects-root-AGENTS.md` (walk-up канон, только dev-машина);
- **Пробелы:** нет ссылки на канон платформы ВНУТРИ репо проекта (walk-up не работает в CI/изолированном клоне); provides генерится глобально, не per-node (агент не видит, какие модули включены на его ноде); DSN не соответствует реальности.

## 2. Границы скоупа

- **В скоупе:** файл-контракт AI-PLATFORM.md + канон docs/; фикс шаред-доступа к БД (роли + pgbouncer wildcard + credentials-канал); тесты; правки 3 проектов tronyx-lab.
- **Вне скоупа:** POSTGRES_PASSWORD rotation (существующий TRAP[DEBT] 2026-07-17, не трогаем); per-project логирование/аудит доступа к БД; доставка AI-PLATFORM.md на VPS (payload whitelist НЕ расширяется — файл для агентов в репо, не для рантайма); фикс других provides (redis/clickhouse/minio per-project изоляция — только документируется).

$END_BRIEF
