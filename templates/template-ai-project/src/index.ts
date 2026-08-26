// GREP_SUMMARY: template-ai-project composition root index createKernel ports platform-adapters PostgresStore SessionStore config content secrets cap-llm LlmPort cap-knowledge channel-telegram loader convention capabilities entrypoints roles plugins validateStartup conventionDirs start healthPort polling
// STRUCTURE: ▶ load config sections (config/*.json) → ⊕ load plugins phase-1 (identityProviders) → ⊕ createKernel(ports) → ○ load convention dirs (capabilities/entrypoints/roles/plugins) → ⊕ register → ◇ validate({conventionDirs}) → ◇ start() → ⚡ channel-telegram polling
// # region MODULE_CONTRACT
// ## @purpose  Composition root проекта-бота (template-ai-project, W3 D9; бриф §8): единственная
// ##           точка сборки ядра — createKernel(@ai-project/kernel) + production-порты
// ##           (@ai-project/platform-adapters: PostgresStore/SessionStore, file Config/Content
// ##           source с hot-reload, SOPS Secrets) + LLM-адаптер (@ai-project/cap-llm) +
// ##           capability (@ai-project/cap-knowledge) + канал (@ai-project/channel-telegram) +
// ##           загрузчик конвенционных папок (capabilities/entrypoints/roles/plugins).
// ##           Финальная сборка пилота — T7 (asi-faq); этот файл — РАБОЧИЙ каркас: структура
// ##           оркестрации и точки расширения фиксированы, фактические экспорты пакетов W3
// ##           (T2-T5) сверяются при сборке пилота.
// ## @scope    src/index.ts проекта; КОНВЕНЦИЯ-ИНВАРИАНТЫ
// ##           - Нарушение конвенции конвенционных папок = агрегированная ошибка старта через
// ##             kernel validateStartup conventionDirs (D11 W2: список ВСЕХ путей, не первая ошибка)
// ##           - K10 (ядро): validate({conventionDirs}) ДО start(); start() сам не валидирует
// ##           - Идентичность W3 — anonymous-провайдер ПРОЕКТА как plugin (D2; см. plugins/AGENTS.md)
// ##           - Секреты (токен TG, ключ LLM) — только через SOPS Secrets (createSopsSecrets),
// ##             никогда не в репозитории и не в образе (бриф §7/§16)
// ## @rationale Бриф §8: «Создал файл в roles/ или entrypoints/ — он подхвачен при старте;
// ##            никакого god-config» — состав проекта виден по папкам, каждая capability
// ##            валидирует свою секцию конфига (§13). P12/§6.3: слой — статика, никаких
// ##            init/install-генераторов в шаблоне.
// ## @modulemap src/index.ts -> dist/index.js (CMD node dist/index.js, Dockerfile)
// ## @usecases T7 пилот asi-faq, любой новый проект из template-ai-project
// # endregion MODULE_CONTRACT

import { readdirSync } from 'node:fs';
import { join, resolve } from 'node:path';
import { pathToFileURL } from 'node:url';

// ── Ядро (единственная легальная точка импорта @ai-project/kernel — composition root, R7 carve-out) ──
import { createKernel, type ConventionDir, type KernelHandle, type KernelPorts } from '@ai-project/kernel';

// ── Production-адаптеры портов ядра (T5, @ai-project/platform-adapters) ──────────────
// Экспортные имена финализируются в T5; здесь — целевые фабрики по D8/W3-девплану.
// - PostgresStore: Store на Postgres (kernel_* namespace + мигратор), R15
// - SessionStore: SessionStore на Postgres (переживает рестарт)
// - createFileConfigSource: ConfigSource из config/ с watch (hot-reload, §13)
// - createFileContentSource: ContentSource из content/ с watch (hot-reload, §13)
// - createSopsSecrets: Secrets через SOPS/age платформы (честная ошибка старта со списком путей)
import {
  createFileConfigSource,
  createFileContentSource,
  createSopsSecrets,
  PostgresStore,
  SessionStore,
} from '@ai-project/platform-adapters';

// ── LLM-адаптер (T3, @ai-project/cap-llm) ────────────────────────────────────────────
// createLlmPort: LlmPort-реализация (LiteLLM + Langfuse span D11 + учёт токенов D6);
// createLlmRouterPort: реализация LlmRouterPort ядра (роутинг интентов, W2-порт).
// Экспортные имена финализируются в T3.
import { createLlmPort, createLlmRouterPort } from '@ai-project/cap-llm';

// ── Capability (T2, @ai-project/cap-knowledge) ────────────────────────────────────────
// createKnowledgeCapability: CapabilityRegistration cap-knowledge с LlmPort в замыкании
// (D7: capability связываются ПОРТАМИ, не invoke). Экспорт финализируется в T2.
import { createKnowledgeCapability } from '@ai-project/cap-knowledge';

// ── Канал (T4, @ai-project/channel-telegram) ──────────────────────────────────────────
// ChannelCapabilities(tg) данными + адаптер polling/webhook + рендер Outbound.
// startTelegramPolling: запуск polling из конфиг-секции channel-telegram. Экспорт в T4.
import { ChannelCapabilities, startTelegramPolling } from '@ai-project/channel-telegram';

import type { IdentityProvider, ConfigSource, ContentSource, Secrets, Log, Clock, Store } from '@ai-project/kernel';

// ── Конвенция: имя файла конвенционной папки (P3/R11, kernel validate.ts) ────────────
const CONVENTION_FILE = /^[a-z][a-z0-9-]*\.ts$/;

// # region FUNC_loadConfigSections
// ## @purpose  Загрузка конфиг-секций проекта из config/*.json (бриф §13: per-capability секции).
// ##           Файл на volume с hot-reload — носитель закрыт портом ConfigSource; здесь только
// ##           первичное чтение для createKernel-конфигурации (healthPort и др. из kernel.json).
// ## @io        ⇥ dir (путь до config/) → ⎋ Record<sectionId, unknown>
// ## @complexity O(files) — синхронное чтение при старте
async function loadConfigSections(dir: string): Promise<Record<string, unknown>> {
  const sections: Record<string, unknown> = {};
  for (const name of readdirSync(dir)) {
    if (!name.endsWith('.json')) continue;
    const sectionId = name.replace(/\.json$/u, '');
    const { default: data } = (await import(pathToFileURL(join(dir, name)).href)) as { default: unknown };
    // ⚠️ Ключ `_doc` в примерах конфига — маркер источника дефолтов (W3 D5/D6); при сборке
    // пилота (T7) секции синхронизируются с configSchema пакетов и `_doc` удаляется.
    sections[sectionId] = data;
  }
  return sections;
}
// # endregion FUNC_loadConfigSections

// # region FUNC_loadConventionDir
// ## @purpose  Загрузчик шаблона по конвенции (бриф §8): динамический import всех файлов
// ##           /^[a-z][a-z0-9-]*\.ts$/ из конвенционной папки. Не-ts файлы (AGENTS.md, .gitkeep,
// ##           README) загрузчик пропускает; их присутствие НЕ нарушение загрузки (нарушение
// ##           конвенции ловит validateStartup conventionDirs — см. collectConventionFileViolations).
// ## @io        ⇥ dir + collector(result, module) → ⎋ Promise<void>; папка отсутствует — no-op
// ## @complexity O(files)
async function loadConventionDir(
  dir: string,
  collector: (mod: Record<string, unknown>) => void | Promise<void>,
): Promise<void> {
  let entries: string[];
  try {
    entries = readdirSync(dir);
  } catch {
    return; // конвенционная папка опциональна (kernel: missing dir skipped silently)
  }
  for (const name of entries.sort()) {
    if (!CONVENTION_FILE.test(name)) continue;
    const mod = (await import(pathToFileURL(join(dir, name)).href)) as Record<string, unknown>;
    await collector(mod);
  }
}
// # endregion FUNC_loadConventionDir

// # region FUNC_registerConventionModules
// ## @purpose  Регистрация capabilities/entrypoints/roles по конвенции (бриф §8):
// ##           - capabilities/*.ts: default export = CapabilityRegistration | () => CapabilityRegistration
// ##           - entrypoints/*.ts: default export = EntryPoint | EntryPoint[] (defineEntryPoint, sdk)
// ##           - roles/*.ts:       default export = Role | Role[] (defineRole + grant(), sdk)
// ##           Каждая capability валидирует СВОЮ секцию конфига (configSchema) — нарушение =
// ##           ошибка старта со списком путей (validateStartup, класс config).
// ## @io        ⇥ handle + пути папок → ⎋ void (регистрирует в реестрах ядра)
// ## @complexity O(items)
async function registerConventionModules(
  handle: KernelHandle,
  capabilitiesDir: string,
  entrypointsDir: string,
  rolesDir: string,
): Promise<void> {
  await loadConventionDir(capabilitiesDir, async (mod) => {
    const raw = mod['default'];
    const registration = typeof raw === 'function' ? await (raw as () => unknown)() : raw;
    handle.register(registration as Parameters<KernelHandle['register']>[0]);
  });
  await loadConventionDir(entrypointsDir, async (mod) => {
    const entry = mod['default'];
    handle.register({ name: 'entrypoints', entryPoints: Array.isArray(entry) ? entry : [entry] });
  });
  await loadConventionDir(rolesDir, async (mod) => {
    const role = mod['default'];
    handle.register({ name: 'roles', roles: Array.isArray(role) ? role : [role] });
  });
}
// # endregion FUNC_registerConventionModules

// # region FUNC_loadPlugins
// ## @purpose  Проектные плагины (escape-hatch, бриф §8; plugins/AGENTS.md): ДВЕ фазы.
// ##           Фаза 1 (ДО createKernel): `identityProviders` — вклад в KernelPorts.identityProviders
// ##           (D2: anonymous-провайдер пилота — любой TG-пользователь → Principal{kind:'visitor'}).
// ##           Фаза 2 (ПОСЛЕ createKernel): `install(handle)` — доп. регистрации (экраны/флоу).
// ## @io        ⇥ pluginsDir → ⎋ { identityProviders, installs }
// ## @complexity O(plugins)
async function loadPlugins(pluginsDir: string): Promise<{
  identityProviders: IdentityProvider[];
  installs: Array<(handle: KernelHandle) => void | Promise<void>>;
}> {
  const identityProviders: IdentityProvider[] = [];
  const installs: Array<(handle: KernelHandle) => void | Promise<void>> = [];
  await loadConventionDir(pluginsDir, (mod) => {
    const contributed = mod['identityProviders'];
    if (Array.isArray(contributed)) identityProviders.push(...(contributed as IdentityProvider[]));
    if (typeof mod['install'] === 'function') installs.push(mod['install'] as (h: KernelHandle) => void | Promise<void>);
  });
  return { identityProviders, installs };
}
// # endregion FUNC_loadPlugins

// # region FUNC_buildKernelPorts
// ## @purpose  Сборка KernelPorts из production-адаптеров (T5) + plugin-провайдеров идентичности.
// ##           DATABASE_URL / LLM_BASE_URL — из окружения (compose env, .env.platform); секреты —
// ##           SOPS. Отсутствие обязательных переменных = честная ошибка старта (fail-fast).
// ## @io        ⇥ env + configSections + identityProviders → ⎋ KernelPorts
// ## @complexity O(1)
function buildKernelPorts(
  env: NodeJS.ProcessEnv,
  identityProviders: IdentityProvider[],
  secrets: Secrets,
): KernelPorts {
  const databaseUrl = env.DATABASE_URL;
  if (!databaseUrl) {
    throw new Error('[IMP:10][startup][ports] DATABASE_URL is required (compose env) — bot cannot persist state (R15)');
  }
  const store: Store = new PostgresStore({ connectionString: databaseUrl });
  const log: Log = {
    info(msg: string, fields?: Record<string, unknown>): void {
      console.log(JSON.stringify({ level: 'info', msg, ...fields }));
    },
    warn(msg: string, fields?: Record<string, unknown>): void {
      console.warn(JSON.stringify({ level: 'warn', msg, ...fields }));
    },
    error(msg: string, fields?: Record<string, unknown>): void {
      console.error(JSON.stringify({ level: 'error', msg, ...fields }));
    },
  };
  const clock: Clock = { now: () => new Date() };
  // ⚠️ Порты, чьи фабрики финализируются в T5, здесь представлены целевыми сигнатурами;
  // при сборке пилота (T7) сверяются с фактическими экспортами platform-adapters.
  const configSource: ConfigSource = createFileConfigSource(resolve('config'));
  const contentSource: ContentSource = createFileContentSource(resolve('content'));

  return {
    store,
    sessionStore: new SessionStore({ store }),
    clock,
    log,
    trace: { span: (name: string) => ({ name, end: () => {} }) },
    secrets,
    memoryStore: { get: async () => null, set: async () => {}, del: async () => {} },
    approvals: { request: async () => ({ status: 'approved' as const }) },
    audit: { write: async () => {} },
    llmRouter: createLlmRouterPort({ configSource }),
    configSource,
    contentSource,
    identityProviders,
  };
}
// # endregion FUNC_buildKernelPorts

// # region FUNC_main
// ## @purpose  Оркестрация старта (K10-контракт): load → createKernel → register → validate → start → polling.
// ## ⚠️ TRAP[DECISION] · 2026-08-22 · — · kernel validateStartup сканирует конвенционные папки
// ##   и флагает ЛЮБОЙ не-.ts файл (AGENTS.md/.gitkeep) как нарушение — конфликт с брифом §15
// ##   («AGENTS.md в roles/»). Требуется аддитивная правка kernel collectConventionFileViolations:
// ##   пропускать dot-файлы и *.md. Rejected: не класть AGENTS.md в конвенционные папки (бриф §15).
// ##   Reason: ядро W2 вне зоны T6 (R2: правки ядра = 0; D13 — единственное исключение W3).
// ##   Rev: сборка пилота T7 падает на AGENTS.md в roles/ без правки ядра.
async function main(): Promise<void> {
  // 1. Конфиг-секции (config/*.json) — healthPort и дефолты ядра из config/kernel.json
  const sections = await loadConfigSections(resolve('config'));
  const kernelSection = (sections['kernel'] ?? {}) as { healthPort?: number };
  const healthPort = kernelSection.healthPort ?? 8787;

  // 2. Плагины фаза 1: identityProviders (D2 anonymous-провайдер) ДО createKernel
  const plugins = await loadPlugins(resolve('src/plugins'));

  // 3. Секреты SOPS/age (честная ошибка старта со списком путей при нерасшифрованном .sops.yaml)
  const secrets = createSopsSecrets();

  // 4. Ядро: production-порты (T5) + plugin-провайдеры
  const handle = createKernel(buildKernelPorts(process.env, plugins.identityProviders, secrets), { healthPort });

  // 5. Capability cap-knowledge (T2): LlmPort в замыкании (D7), LlmRouterPort ядра (T3)
  handle.register(createKnowledgeCapability({ llmPort: createLlmPort() }));

  // 6. Конвенционные папки: capabilities/ entrypoints/ roles/
  await registerConventionModules(handle, resolve('src/capabilities'), resolve('src/entrypoints'), resolve('src/roles'));

  // 7. validateStartup с conventionDirs (K10): агрегированная ошибка со списком ВСЕХ путей
  const conventionDirs: ConventionDir[] = [
    { dir: resolve('src/capabilities'), kind: 'capabilities' },
    { dir: resolve('src/entrypoints'), kind: 'entrypoints' },
    { dir: resolve('src/roles'), kind: 'roles' },
  ];
  await handle.validate({ conventionDirs });

  // 8. Плагины фаза 2: install(handle) — экраны/флоу/доп. регистрации
  for (const install of plugins.installs) await install(handle);

  // 9. Старт ядра: Cordis-плагины, outbox recovery, /health + /metrics на healthPort
  await handle.start();

  // 10. Канал: polling TG из конфиг-секции channel-telegram (D4: transport enum из ChannelCapabilities)
  const channelConfig = (sections['channel-telegram'] ?? {}) as { transport?: 'polling' | 'webhook'; token?: string };
  if (channelConfig.transport === 'polling') {
    await startTelegramPolling(handle, {
      capabilities: ChannelCapabilities.telegram(),
      tokenRef: channelConfig.token ?? 'tg:{{PROJECT_NAME}}:bot-token',
      secrets,
    });
  }
}
// # endregion FUNC_main

main().catch((err: unknown) => {
  // Честная ошибка старта: никакого молчаливого деграда (P4); AggregateStartupError печатает ВСЕ пути
  console.error(`[IMP:10][startup][fatal] ${err instanceof Error ? err.message : String(err)}`);
  process.exit(1);
});
