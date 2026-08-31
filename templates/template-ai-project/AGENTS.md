# GREP_SUMMARY: AGENTS.md template-ai-project ai-project Cordis capabilities entrypoints roles plugins jobs static gates
# STRUCTURE: ┌AGENTS.md┐ → ◇ src/{capabilities,entrypoints,roles,plugins} + jobs + content → ⊕ статические проверки → ⎋ конвенция загрузчика (без god-config)

# AGENTS.md — проект-бот (слой template-ai-project)

Карта узла для агента: правила проекта-бота восстанавливаются только по этому файлу. Слой
создаётся из `template-ai-project` (W3 D9) — корень дерева шаблонов (бриф §6.1, без parent).

## Что это

Проект на AI Project (боты, сервисы, админки): Cordis + пакеты `@ai-project/*` +
`src/{capabilities,entrypoints,roles,plugins}` + статические проверки. Состав проекта виден по
папкам, файлы подхватываются при старте по конвенции (никакого god-config, бриф §8).

## Структура

```
<project>/
  Dockerfile, docker-compose.yml, Makefile, tsconfig*.json, vitest.config.ts, .github/  # GENERATED
  AGENTS.md                       # этот файл — карта проекта
  template.yaml, practices.lock   # метаданные слоя + каркас практик (перегенерируется каналом)
  os-gates.json                   # подмножество гейтов §6.4 (@ai-project/static)
  src/
    index.ts                      # composition root: createKernel + порты + загрузчик конвенций
    capabilities/                 # включённые capability: файл = capability (defineCapability, zod, R8)
    entrypoints/                  # точки входа: канал + аудитория + представление (defineEntryPoint)
    roles/                        # роли: гранты через grant('cap/action') (defineRole)
    plugins/                      # проектные плагины (escape-hatch): identityProviders / install
  content/                        # тексты/кнопки/базы знаний — правит редактор (hot-reload, volume)
  config/                         # конфиг-секции capability (каждая валидирует СВОЮ, §13)
  tests/                          # симуляции сценариев (создаётся проектом)
```

## Конвенции (инварианты)

- **Конвенционные папки** (`capabilities/`, `entrypoints/`, `roles/`): файл = id, подхват при
  старте динамическим import; нарушение конвенции = агрегированная ошибка старта со списком
  путей (validateStartup conventionDirs, D11 W2). Имя файла: `/^[a-z][a-z0-9-]*\.ts$/`.
- **K10 (ядро)**: composition root вызывает `validate({conventionDirs})` ДО `start()`; `start()`
  сам не валидирует.
- **Идентичность** (W3 D2): anonymous-провайдер проекта (plugins/) → visitor-принципал; грант
  проверяется на выполнении (шаг 4 spine). cap-identity — W4.
- **Секреты** — только SOPS/age (createSopsSecrets): токен TG, ключ LLM никогда не в
  репозитории и не в образе (бриф §7/§16).
- **Состояние** — только в Postgres/volume (R15): сессии, курсор update_id, чанки, аудит;
  ничего значимого в памяти процесса.
- **Конфиг/контент** — на volume, hot-reload, образ неизменяем (бриф §7); тексты — только
  `t()` из `content/` (R6), русский по умолчанию.
- **env-контракт compose** (план 019 TASK-1): `DATABASE_URL` — маппинг переменной
  `PLATFORM_POSTGRES_DSN` из `.env.platform` (GENERATED; роль/БД провижинит хук postgres
  из `needs.database`); `LLM_BASE_URL` — маппинг `PLATFORM_LITELLM_URL`
  (fallback `http://litellm:4000`). Литеральный `DATABASE_URL` (без PLATFORM_-источника)
  в compose запрещён — переменной нет в `.env.platform`, интерполяция даёт пустую строку
  (инцидент пилотов asi-group, план 019 F3).
- **Сети compose** — own-net + `proxy-net` (ingress/TLS) + `shared-db-net` (pgbouncer:6432) +
  `hermes-agent-net` (litellm:4000), все external, имена фиксированы (SoT
  platform-infra.yaml#provides, DR-M4). `shared-cache-net` НЕ подключается — redis бот не
  потребляет (least privilege). Гейт `service-network-coverage` (K1/K3) блокирует деплой
  compose, потребляющего платформенный сервис без сети провайдера.

## Запреты (ломаются гейтами)

- Импорты: только `@ai-project/sdk` + объявленные `@ai-project/cap-*` (R7, `only-sdk-imports`);
  исключение — `src/index.ts` (composition root, `@ai-project/kernel` для createKernel).
  Глубокие импорты внутрь пакетов запрещены (`no-deep-imports`).
- Пользовательские строки в коде запрещены (`no-user-strings-in-os`) — всё в `content/`.
- Правки GENERATED-файлов (Dockerfile/compose/Makefile/.github) — дрейф (`generated-drift`):
  приходят синком практик, ручные правки затираются.
- Правки пакетов ОС: проект ОС не трогает (R2); новое в ОС — capability-пакетом (R8).

## Чек-лист PR

1. `npm run typecheck && npm run test && npm run lint && npm run self-check` — зелёные
   (self-check прогоняет os-gates.json subset).
2. Старт без ошибок конвенции: `/health` 200, `/metrics` отдаёт счётчики invoke + degrade.
3. Ноль правок ОС и GENERATED; конфиг-секции и `content/` синхронны с кодом.
4. Секреты не в git; `data-map` проекта заполнена.

## Ссылки

- Бриф: `.ai/plans/012-ai-project/01-Brief.md` (§6 шаблонный слой, §7 деплой, §8 структура).
- Девплан волны: `.ai/plans/012-ai-project/waves/W3-devplan.md` (D9/D10, DoD-8).
- Платформа (шаблоны — собственность платформы): патч-предложение `templates/template-ai-project/`.
