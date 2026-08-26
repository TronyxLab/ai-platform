# GREP_SUMMARY: AGENTS.md capabilities CapabilityRegistration dynamic-import convention включённые-capability
# STRUCTURE: ┌capabilities/<id>.ts┐ → ◇ default CapabilityRegistration | фабрика → ⊕ dynamic import загрузчиком → ⎋ конвенция слоя

# AGENTS.md — src/capabilities/ (конвенция слоя template-ai-project)

Карта узла для агента: что здесь можно писать и как файлы подхватываются.

## Конвенция

- Каждый файл `capabilities/<id>.ts` = ОДНА включённая capability проекта (бриф §8: «включённые
  capability: import + секция конфига на файл»). Имя файла = id (`knowledge.ts` → capability
  `knowledge`). Файл подхватывается загрузчиком шаблона (src/index.ts) динамическим import
  по конвенции — центрального god-config нет.
- Файл экспортирует **default**: `CapabilityRegistration` (из `@ai-project/kernel`) или фабрику
  `() => CapabilityRegistration | Promise<CapabilityRegistration>`.
- Сама capability объявляет **manifest** через `defineCapability` из `@ai-project/sdk`:
  `{ id, version, configSchema, permissions, migrations }` — R8 (пакет = манифест + схема +
  миграции + contract-тесты), гейт `capability-manifest-valid`.
- **Действия** — `defineAction({ id, input, output, sideEffects, idempotent, grants, approval,
  surfaces })` из `@ai-project/sdk` (ядро 02-DevPlan §Contracts/invoke-spine); гранты ролей
  ссылаются на их id (гейт `role-grants-valid`).
- **Секция конфига**: каждая capability валидирует СВОЮ секцию `config/<capability>.json` своей
  zod-схемой (configSchema); опечатка в конфиге = понятная ошибка старта с путём, а не молчаливый
  сбой (бриф §13). Схема — zod (P6).
- Связь capability между собой — только ПОРТАМИ в замыкании при сборке, не invoke (W3 D7).

## Запреты

- Никаких `if (channel === …)` и веток по каналам — канал рендерит по своим данным
  ChannelCapabilities (бриф §10.2).
- Никаких пользовательских строк в коде (R6): тексты/кнопки/меню — в `content/` через `t()`.
- Никаких vendor-клиентов в проекте (R7, гейт `no-raw-vendor-clients`).

## Чек-лист PR

1. Файл подхвачен при старте (симуляция проекта зелёная).
2. configSchema секции и пример `config/<id>.json` синхронны.
3. `npm run typecheck && npm run test && npm run lint && npm run self-check` — зелёные.
