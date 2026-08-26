# GREP_SUMMARY: AGENTS.md plugins escape-hatch CapabilityRegistration две-фазы
# STRUCTURE: ┌plugins/<id>.ts┐ → ◇ две фазы подхвата → ⊕ dynamic import загрузчиком → ⎋ конвенция слоя

# AGENTS.md — src/plugins/ (конвенция слоя template-ai-project)

Карта узла для агента: собственные плагины проекта — escape-hatch (бриф §8).

## Конвенция

- Каждый файл `plugins/<id>.ts` = проектный плагин. Подхват — загрузчиком шаблона (src/index.ts),
  ДВЕ фазы:
  - **Фаза 1 (ДО createKernel)**: именованный экспорт `identityProviders: IdentityProvider[]` —
    вклад в `KernelPorts.identityProviders`.
  - **Фаза 2 (ПОСЛЕ createKernel)**: именованный экспорт `install(handle: KernelHandle)` —
    доп. регистрации (экраны/флоу/данные), `void | Promise<void>`.
- Плагин — единственный escape-hatch проекта: то, что не выражается capability/точкой входа/ролью.
- Код в `plugins/` — ПРОЕКТНЫЙ (не ОС, не GENERATED): правки здесь — нормальная разработка.

## W3 D2: anonymous-провайдер идентичности пилота (канон для ботов)

cap-identity приходит в W4, но шаг 4 конвейера (гранты) обязателен уже сейчас (P8). Пилот asi-faq
регистрирует минимальный `IdentityProvider` как проектный плагин:

- любой TG-пользователь → `Principal{kind:'visitor', id: <tg-id>}`;
- роль `client` (src/roles/) с read-грантами на `knowledge.search`/`knowledge.answer`;
- провайдер возвращает **null** только для технически некорректных ссылок принципала: вызов падает
  на шаге 4 грантов с `PolicyError` (таксономия §15) — аноним не проходит грант-фильтр (матрица W3 №14);
- контракт `IdentityProvider` стабилен с W1 — в W4 замена провайдером cap-identity без ломки (R14).

## Запреты

- Не дублировать ОС: если механизм нужен >1 проекту — это capability-пакет (R4/R8), не плагин.
- Никаких пользовательских строк в коде — только `t()` из `content/`.

## Чек-лист PR

1. Плагин не ломает старт: валидация (validate) идёт ПОСЛЕ фазы 1, install — после validate.
2. identity-провайдеры не держат состояние в памяти (R15) — только порты.
3. `npm run typecheck && npm run test && npm run lint && npm run self-check` — зелёные.
