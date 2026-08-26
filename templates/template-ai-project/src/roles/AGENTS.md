# GREP_SUMMARY: AGENTS.md roles роли гранты Role permission
# STRUCTURE: ┌roles/<id>.ts┐ → ◇ Role | Role[] → ⊕ dynamic import загрузчиком → ⎋ конвенция слоя

# AGENTS.md — src/roles/ (конвенция слоя template-ai-project)

Карта узла для агента: роли и гранты проекта.

## Конвенция

- Каждый файл `roles/<id>.ts` = одна роль. Имя файла = id; подхват — загрузчиком шаблона.
- Файл экспортирует **default**: `Role` (из `@ai-project/kernel`) или `Role[]`, объявленный через
  `defineRole` из `@ai-project/sdk`.
- Роль перечисляет **гранты** на capability-действия через `grant('<capability>/<action>')` из
  `@ai-project/sdk` (branded `GrantRef` — опечатка формы ловится на type-check, не в рантайме).
- Иерархия ролей — включением: роль «роп» включает роль «менеджер» + свои гранты (бриф §9.1).
- Проверка гранта обязательна и при показе кнопки, и при выполнении действия (шаг 4 spine,
  P8) — на всех поверхностях.
- Все действия W3 — read с approval OFF; грант роли ссылается на существующие действия
  (гейт `role-grants-valid`, класс (b) validateStartup).

## Пример

```ts
import { defineRole } from '@ai-project/sdk';
import { grant } from '@ai-project/sdk';

export default defineRole({
  id: 'client',
  grants: [grant('knowledge/search'), grant('knowledge/answer')],
});
```

## Чек-лист PR

1. Каждый грант ссылается на действие включённой capability (иначе старт падает со списком путей).
2. Идентичность принципала → роль — через identity-провайдер (W3 D2: anonymous → visitor-роль).
3. `npm run typecheck && npm run test && npm run lint && npm run self-check` — зелёные.
