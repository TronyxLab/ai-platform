# GREP_SUMMARY: AGENTS.md entrypoints точки-входа channel audience presentation
# STRUCTURE: ┌entrypoints/<id>.ts┐ → ◇ канал + аудитория + представление → ⊕ dynamic import загрузчиком → ⎋ конвенция слоя

# AGENTS.md — src/entrypoints/ (конвенция слоя template-ai-project)

Карта узла для агента: точки входа проекта.

## Конвенция

- Каждый файл `entrypoints/<id>.ts` = точка входа: канал + аудитория + представление (бриф §8).
  Имя файла = id; подхват — загрузчиком шаблона (динамический import по конвенции).
- Файл экспортирует **default**: `EntryPoint` (из `@ai-project/kernel`) или `EntryPoint[]`,
  объявленный через `defineEntryPoint` из `@ai-project/sdk`.
- Точка входа описывает: канал (ChannelCapabilities данными), аудиторию (роль/принципал),
  кнопки/меню (из capability × гранты роли × лимиты канала, бриф §9), интенты меню (старт,
  контакты) со скорингом.

## D3: default-route свободного текста (W3, текстовая механика)

FAQ-бот должен отвечать на свободный вопрос, даже если он не совпал с интентами меню:

1. Ядро W2 разрешает входящее: `resolveInbound(message, principal)` → скоринг интентов меню.
2. Сильное совпадение → интент-маршрут. Слабое/нет → ядро возвращает
   `ResolutionOutcome{kind:'fallback', reason:'no-candidate'}`.
3. **Маршрут по умолчанию исполняет АДАПТЕР точки входа проекта** (не ядро, правок ядра нет):
   получает fallback-исход и сам вызывает `invoke('knowledge.answer', {question}, ctx
   surface='channel')` от имени принципала (visitor).
4. LLM-роутер включается ТОЛЬКО при неоднозначности между интентами; ниже жёсткого порога —
   fallback-контент точки входа + событие `resolution.fallback`.

## Запреты

- Никаких vendor-клиентов в точке входа — канал приходит пакетом (`@ai-project/channel-telegram`).
- Никаких пользовательских строк — только `t()` из `content/`.

## Чек-лист PR

1. Симуляция точки входа зелёная (включая fallback → knowledge.answer по D3).
2. Кнопки соответствуют лимитам канала (≤8/ряд, callback ≤64Б, ≤4096 симв.).
3. `npm run typecheck && npm run test && npm run lint && npm run self-check` — зелёные.
