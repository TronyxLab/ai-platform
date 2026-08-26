# GREP_SUMMARY: AGENTS.md jobs фоновые-задания cap-jobs kebab-case cron
# STRUCTURE: ┌jobs/<jobId>.ts┐ → ◇ kebab-case id (/^[a-z][a-z0-9-]*\.ts$/) → ⊕ валидатор/загрузчик → ⎋ конвенция слоя

# AGENTS.md — src/jobs/ (конвенция слоя template-ai-project)

Карта узла для агента: фоновые задания проекта (W5, cap-jobs).

## Конвенция

- Каждый файл `jobs/<jobId>.ts` = ОДНО задание проекта (W5 T2, `@ai-project/cap-jobs`).
  Имя файла = id задания (`/^[a-z][a-z0-9-]*\.ts$/`, kebab-case); подхват — валидатором
  конвенции cap-jobs (`loadJobModules`) при старте по динамическому import — никакого
  god-config (P3/R11, прецедент конвенционных папок ядра).
- Файл экспортирует **default**: `JobDefinition` из `@ai-project/cap-jobs` (zod-схема
  `JobDefinitionSchema`): `id`, `trigger` (закрытый discriminated union: cron/событие/ручной/
  webhook), `actionRef` (id действия единого реестра P11), `args`, `grants.roleIds`, `retry`,
  `timeoutMs`, `catchUp`, `enabled`.
- Задание вызывает ТЕ ЖЕ действия, что и кнопка, через `invoke()` с `surface='job'`,
  `principalId='system:<jobId>'`, `correlationId=runId` (D5 W5) — «языка заданий» нет, ядро
  не правится (D3 W5: ноль правок базового состава).
- Гранты задания — `grants.roleIds`: действуют через шаг 4 spine (системный принципал
  `system:<jobId>`), runner сам гранты не проверяет (cap-jobs AGENTS.md).

## @invariants

- Валидатор cap-jobs (`loadJobModules`) агрегирует ВСЕ нарушения конвенции в ОДНУ
  `JobsConventionError` со списком ВСЕХ путей при старте (D11-прецедент AggregateStartupError
  ядра); частичная регистрация запрещена — либо все модули валидны, либо одна ошибка со списком.
- Задания вызывают действия через `invoke()` с Principal=`system:<jobId>` (surface='job',
  correlationId=runId стабилен на ретраях) — тем же spine, что интерактивные поверхности.

## Пример (скелет, закомментирован)

```ts
// Файл src/jobs/<jobId>.ts — имя файла = id задания (kebab-case).
// default export = JobDefinition из @ai-project/cap-jobs.
import type { JobDefinition } from '@ai-project/cap-jobs';

// export default {
//   id: 'demo-job',                       // = имя файла demo-job.ts (P3: подбор по имени)
//   title: 'Demo job',                    // технический title; тексты — content/ (R6)
//   trigger: { kind: 'cron', expression: '0 * * * *' }, // cron | event | manual | webhook
//   actionRef: 'knowledge/answer',        // id действия единого реестра (P11)
//   args: { question: '…' },              // дефолтные аргументы действия
//   grants: { roleIds: ['system'] },      // гранты принципала system:<jobId> (шаг 4 spine)
//   retry: { maxAttempts: 3 },            // опционально; дефолты — конфиг-секция cap-jobs
//   timeoutMs: 60_000,                    // дефолт 60000
//   enabled: true,
// } satisfies JobDefinition;
```

## Запреты

- Никаких пользовательских строк в коде задания (R6) — тексты в `content/` через `t()`.
- Никаких vendor-клиентов и глубоких импортов (R7, гейты `no-raw-vendor-clients`,
  `no-deep-imports`) — только `@ai-project/sdk` + объявленные `@ai-project/cap-*`.
- Runner/триггеры cap-jobs — код ОС; в проекте не дублируются (R3/R8).

## Чек-лист PR

1. Задание подхвачено при старте: валидатор cap-jobs зелёный, старт без `JobsConventionError`.
2. `actionRef` ссылается на действие включённой capability; гранты задания валидны
   (гейт `role-grants-valid`).
3. `npm run typecheck && npm run test && npm run lint && npm run self-check` — зелёные.

## Ссылки

- `@ai-project/cap-jobs` (AGENTS.md пакета) — реестр заданий, конвенция `src/jobs/*.ts`,
  порт JobQueue, runner. Девплан: `.ai/plans/012-ai-project/waves/W5-devplan.md`
  (§2 T2/T13, §3.1 D2/D4/D5/D12/D14).
