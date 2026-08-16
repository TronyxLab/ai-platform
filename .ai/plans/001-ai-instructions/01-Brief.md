<!-- GREP_SUMMARY: ai-instructions, конвенционный компилятор, markdown-инструкции, формат Гермеса, канон/проект, protected-id, reference-модель, watcher, ai-instructions.lock, dogfood, варианта D -->
<!-- STRUCTURE: ┌решения интервью┐ → ◇ дерево контента → ◇ markdown-формат → ◇ алгоритм walk/resolve/emit → ⊕ артефакты/ссылки → ⊕ watcher/pack/lock → ⊕ дистрибуция → ⎋ dogfood + acceptance -->
# region MODULE_CONTRACT
## @purpose  Brief задачи «ai-instructions»: конвенционный компилятор инструкций (канон + проект) как встроенный AI-слой Platform, с платформой в качестве первого потребителя
## @scope    Архитектурный контракт: модель слоёв, формат источников, дерево контента, алгоритм компиляции, reference-модель артефактов, дистрибуция, dogfood-план
## @invariants
##   1. Источники инструкций — декларативный markdown (формат скиллов Гермеса); Python — только тонкий компилятор/CLI (никакой бизнес-логики в контенте)
##   2. Компиляция — convention-over-config: позиция файла в дереве определяет назначение; MODULE_CONTRACT-директивы уточняют (папка + директивы)
##   3. Сборка — reference-модель: файлы эмитятся 1:1 без склейки, инструменты подключают их ссылками (нативная модель kilo и hermes)
##   4. Канон — только мета-правила (5 групп, protected-id); проектные дополнения add-only поверх protected
##   5. Первый потребитель — сам ai-platform (dogfood); practices-система остаётся отдельной
## @rationale Быстрый цикл обновлений варианта D (отдельный репо, платформа — вендор-пиннер и распространитель) + уточнения: конвенционный компилятор вместо XML-манифестной машинерии, ссылки вместо склейки, канон-минимализм
# endregion MODULE_CONTRACT

$START_BRIEF
$ARTIFACT_CONTRACT
PURPOSE:               Спроектировать ai-instructions — конвенционный компилятор инструкций, собирающий финальные инструкции из канона (мета-правила) + проектных дополнений по понятному файлово-древесному алгоритму, с автоматической пересборкой при изменениях
DESCRIPTION:           Репо ai-instructions: контент — дерево каталогов с markdown-файлами в формате Гермеса (rules/, roles/, skills/, playbooks/), runtime — новый тонкий Python-компилятор (walk → resolve → emit → manifest → lock), замена bundlekit-машинерии. Компилятор эмитит файлы 1:1 (без склейки) в .ai/ потребителя и регистрирует их ссылками. Триггеры: file-watcher (первичный) + ai-instructions sync. Pack-режим материализует единый markdown для внешних репозиториев. Пиннинг: ai-instructions.lock (версии + sha256). Дистрибуция: git tag + tarball/OCI-артефакт, платформа пиннит tag@digest. Первый потребитель — ai-platform (пересборка собственного .ai из канона + проектных дополнений)
RATIONALE:             Исследование (вариант D: быстрый цикл обновлений контента, платформа вендорит и распространяет) + интервью: (1) markdown-инструкции с MODULE_CONTRACT-директивами — формат скиллов Гермеса, дерево проще хранить в стейте модели без галлюцинаций; (2) запутанность текущего компилятора устраняется конвенцией «папка решает всё»; (3) ссылки вместо склейки — нативная модель kilo (instructions в kilo.json) и hermes (каталог skills), монолитная конкатенация давала 34% шума (research/design-0.4-to-0.5.md); (4) канон — мета-правила, без «лесенки» копирования правил сверху вниз; (5) платформа сама потребляет сгенерированные инструкции (канон + проект как единый набор)
ACCEPTANCE_CRITERIA:   См. секцию «Критерии приёмки» ниже (8 проверяемых критериев)
IMPLEMENTS:            Результаты архитектурного исследования ai-instructions (вариант D, федеративная модель) + ответы интервью (15 решений)
IMPACTS:               ai-platform (собственный .ai, entrypoint `ai`/`ai-sync`, check-suite-проверка), ai-instructions (новый runtime, списание bundlekit), проекты платформы (scaffold/ai-sync — в последующих волнах), deepseek-harness (pack)
REQUIRES:              Доступ к репо ai-instructions (миграция контента), локальный клон ai-platform (~/projects/ai-platform), kilo (потребитель артефактов)
$END_ARTIFACT_CONTRACT

$START_DOCUMENT_PLAN
### Document Plan
**SECTION_GOALS:**
- GOAL Зафиксировать все решения интервью как единый контракт => GOAL_DECISIONS
- GOAL Описать дерево контента и формат источников => GOAL_CONTENT_TREE
- GOAL Описать детерминированный алгоритм компиляции => GOAL_ALGORITHM
- GOAL Описать артефакты, триггеры, pack, lock, дистрибуцию => GOAL_DELIVERY
- GOAL Описать dogfood-план для ai-platform и критерии приёмки => GOAL_DOGFOOD
**SECTION_USE_CASES:**
- USE_CASE Разработчик правит markdown-файл → watcher пересобирает только затронутые артефакты => SCENARIO_WATCHER
- USE_CASE Платформа собирает своё .ai из канона + проектных дополнений => SCENARIO_PLATFORM_BUILD
- USE_CASE Внешний репозиторий получает pack-файл => SCENARIO_PACK
- USE_CASE Проект пиннит версию инструкций ai-instructions.lock => SCENARIO_LOCK
$END_DOCUMENT_PLAN

---

## 1. Контекст

AI Instructions Framework (ai-instructions) — компилятор инструкций (markdown → .ai) для AI-агентов. По итогам архитектурного исследования принят **вариант D**: ai-instructions остаётся отдельным репозиторием с быстрым релиз-циклом; Platform вендорит его (pin tag@digest) и распространяет через свои каналы; проекты получают только скомпилированные артефакты без Python-зависимости.

Интервью добавило три принципиальных уточнения:
1. **Конвенционный компилятор** — сборка строится автоматически от структуры папок/файлов, без XML-манифеста-реестра, секций и priority-механизма текущего bundlekit (главный источник запутанности).
2. **Инструкции работают и для самой платформы** — финальные инструкции генерируются в корне ai-platform из канона (мета-правила ai-instructions) + проектных дополнений (правила платформы как проектные). Канон — минимален, никакого копирования «лесенкой» сверху вниз.
3. **Ссылки вместо склейки** — инструкции разбиты на файлы, итоговые артефакты ссылаются на них, а не копируют контент в монолиты.

## 2. Зафиксированные решения интервью

| # | Вопрос | Решение | Следствие |
|---|--------|---------|-----------|
| 1 | Формат источников | **Markdown** (1 файл = 1 instruction) | Формат скиллов Гермеса (SKILL.md + MODULE_CONTRACT); отказ от секций/priority/framework-manifest; директивы MODULE_CONTRACT = «frontmatter» |
| 2 | Дерево контента | `rules/ roles/ skills/ playbooks/ policies/` | Позиция файла определяет назначение |
| 3 | Привязка к агенту | Папка по умолчанию + директивы MODULE_CONTRACT | `roles/<r>/` → только эта роль; `skills/` → все |
| 4 | Override проекта над каноном | **Protected-id** | Коллизия проекта с protected-правилом канона → ошибка; non-protected — замещение по id; новые id — add-only |
| 5 | Артефакты kilo | **Ссылки, без склейки** | 1 source → 1 target-файл; kilo.json `instructions` подключает; agent-файлы тонкие |
| 6 | Pack-режим | **Да** — `ai-instructions pack` | Единый markdown для внешних (deepseek-harness) |
| 7 | Триггеры пересборки | **Watcher dev-машины** (первичный) | + `ai-instructions sync` как ручной эквивалент того же конвейера |
| 8 | AGENTS.md платформы | **Ручные проектные источники** | Компилятор включает их ссылками in place, не перезаписывает; gate-тринити не трогаем |
| 9 | Состав канона | 5 групп: **inheritance, conventions, workflow, markup, security** | Коммуникационные нормы и release-safety → проектные правила платформы |
| 10 | Репозиторий | **Имя ai-instructions сохраняется** | Без ребрендинга |
| 11 | Судьба bundlekit | **Новый тонкий компилятор** | Старая машинерия списывается; контент мигрирует |
| 12 | Дистрибуция | **Git tag + tarball/OCI** | Платформа пиннит tag@digest (digest-pin — канон платформы) |
| 13 | Lock | **ai-instructions.lock** (pin + sha256) | Образец — practices.lock; дрейф-детект |
| 14 | Первый потребитель | **Сам ai-platform** (dogfood) | Пересборка собственного .ai из канона + проектных дополнений |
| 15 | Practices | **Остаётся отдельной** | Конвергенция — отдельным решением в будущем |

## 3. Дерево контента (репо ai-instructions)

```
ai-instructions/
├── VERSION                        # semver версия контента
├── rules/                         # канонические правила (все агенты)
│   ├── inheritance.md             #   @protected — модель наследования: слои, protected-id, алгоритм resolve
│   ├── conventions.md             #   @protected — конвенции источников: дерево, markdown-формат, именование
│   ├── workflow.md                #   @protected — каркас: роли Architect/Coder/QA, стадии, артефакты
│   ├── markup.md                  #   @protected — GREP_SUMMARY/STRUCTURE/TRAP/MODULE_CONTRACT
│   └── security.md                #   @protected — абсолюты: секреты, git, авторизация системных команд
├── roles/                         # роли-агенты
│   ├── architect/role.md          #   ровно один role.md = определение роли
│   ├── coder/role.md
│   ├── qa/role.md
│   └── sysadmin/role.md
├── skills/<name>/SKILL.md         # скиллы (формат Гермеса)
├── playbooks/<name>.md            # сценарии (эмитятся как skills)
├── policies/                      # в каноне пусто; используется проектными политиками платформы
└── runtime/                       # новый тонкий компилятор (Python)
    ├── cli.py                     #   ai-instructions {sync, watch, pack, check}
    ├── walker.py                  #   обход деревьев источников
    ├── resolver.py                #   слои канон→проект, protected-id
    ├── emitter.py                 #   source → target (1:1)
    └── lock.py                    #   ai-instructions.lock: pin + sha256
```

Проект потребителя (любой потребитель, включая ai-platform) хранит всё AI-содержимое в `.ai/`:

```
<consumer>/                        # корень потребителя
├── AGENTS.md                      # ручной проектный источник, reference in place (не перезаписывается)
└── .ai/                           # всё AI-содержимое проекта
    ├── rules/*.md                 #   проектные правила (add-only) + материализованный канон (stamped)
    ├── roles/<name>/role.md       #   проектные роли
    ├── skills/<name>/SKILL.md     #   проектные скиллы (формат Гермеса)
    ├── playbooks/<name>.md        #   проектные сценарии
    └── policies/                  #   проектные политики
```

## 4. Markdown-формат (1 файл = 1 instruction, формат Гермеса)

Скиллы — в точном формате Гермеса (`skills/<name>/SKILL.md`):

```markdown
# GREP_SUMMARY: SKILL server-status base-agent health uptime
# STRUCTURE: ▶ triggers:/status/health → response:operational → ⎋
# region MODULE_CONTRACT
## @purpose     Report container health, uptime, and version
## @scope       Responds to /status, health, uptime queries
## @invariants  Lightweight status check — no external dependencies
## @changes     LAST_CHANGE: 2026-06-23 | initial creation
# endregion MODULE_CONTRACT

# server-status
triggers:
  - "/status"
  - "health"
response: |
  Agent is operational.
```

Правила и роли используют тот же MODULE_CONTRACT-канон:

```markdown
# GREP_SUMMARY: RULE testing pytest anti-loop
# STRUCTURE: ┌fixtures┐ → ◇ assertions → ⊕ LDD-trajectory
# region MODULE_CONTRACT
## @purpose     Правила тестирования
## @scope       Все Python-тесты проекта
## @invariants  tmp_path, native imports, IMP:9-логи
## @protected   true
## @order       10
# endregion MODULE_CONTRACT

# testing
... markdown-контент без преобразований ...
```

- **Директивы MODULE_CONTRACT** — «frontmatter в markdown»: `@protected` (только канон; default отсутствует), `@order` (сортировка в pack), `@roles` (явное ограничение роли; default — из позиции). `@purpose`/`@scope`/`@invariants` — обязательная документация.
- **Дефолты — из позиции в дереве**: файл в `roles/architect/` без директив уже привязан к роли; правило в `rules/` без `@protected` — замещаемо проектом.
- **Удалено** относительно bundlekit: XML-источники (framework-manifest, секции с priority, classifier), dual-config (ai-instructions.yaml + kilo-config.yaml), `<roles>`-драйвер манифеста. Единственный драйвер — дерево + директивы MODULE_CONTRACT.

## 5. Алгоритм компиляции (convention-over-config)

```
Вход: [канон ai-instructions @pin] + [.ai/ потребителя + AGENTS.md]
Шаг 1 walk    — обход деревьев, сбор всех *.md с относительными путями
Шаг 2 resolve — effective-карта по id (id = относительный путь без расширения):
                · protected-правило канона + коллизия в .ai/ → ОШИБКА (fail-fast, id + оба пути)
                · non-protected + тот же id в .ai/ → проект замещает
                · новые id в .ai/ → add-only (добавляются)
Шаг 3 emit    — 1:1 маппинг source → target, БЕЗ склейки, в .ai/ потребителя:
                rules/<n>.md          → .ai/rules/<n>.md
                roles/<r>/role.md     → .ai/roles/<r>/role.md
                skills/<n>/SKILL.md   → .ai/skills/<n>/SKILL.md
                playbooks/<n>.md      → .ai/skills/playbook-<n>/SKILL.md
Шаг 4 manifest — регистрация .ai/ в конфиге потребителя ссылками (kilo.json: instructions/skills);
                пользовательские ключи сохраняются (паттерн manage_config из bundlekit)
Шаг 5 lock     — ai-instructions.lock: pin версии канона + sha256 каждого выходного файла
```

**Инварианты:**
- generated-файлы получают stamp `<!-- ai-instructions:<version> -->`; файл без stamp (ручной) — **never overwritten** (инвариант наследуется от bundlekit);
- повторный прогон на неизменном дереве — no-op (сверка хэшей, пересборка только изменённого);
- pack-порядок детерминирован: канон → проект → путь в дереве → директива `@order`;
- ноль внешних Python-зависимостей (stdlib; pyyaml — если потребуется manifest-склейка).

## 6. Триггеры пересборки

- **`ai-instructions watch`** (первичный): наблюдатель за репо ai-instructions (локальный клон), `.ai/` и AGENTS.md потребителя; debounce; изменение → тот же конвейер, что и sync, но пересобираются только затронутые файлы (1:1 маппинг). Реализация — stdlib polling (mtime+hash); watchdog-зависимость — открытый вопрос DevPlan.
- **`ai-instructions sync`**: ручной одноразовый прогон (аналог project-sync-practices).
- CI/pre-push гейты — вне scope v1 (интервью: только watcher).

## 7. Pack-режим

`ai-instructions pack --out <file>` — детерминированная материализация resolved-дерева в единый markdown: заголовки секций из путей, порядок по §5-инварианту. Назначение — внешние репозитории без системы ссылок (deepseek-harness). Для kilo/hermes pack не используется.

## 8. Lock и дистрибуция

```yaml
# ai-instructions.lock (образец схемы)
version: 1
canon_version: 0.7.0          # pin контента ai-instructions
platform_version: <git-sha>   # pin проектных правил платформы (только для platform-проектов)
generated_at: <ISO8601>
files:
  - path: .ai/rules/inheritance.md
    sha256: ...
    source: rules/inheritance.md
```

- Релиз ai-instructions: git tag semver → tarball контент-дерева в GitHub Release (+ OCI-артефакт ghcr.io — вторая итерация).
- Платформа пиннит tag@digest в собственном SoT; dev-машина резолвит: локальный клон → pin-кэш → remote.
- Проекты платформы не ставят pip и не клонируют ai-instructions — только артефакты через scaffold/ai-sync (волны 3+).
- Дрейф-детект: `ai-instructions check` сверяет хэши lock с фактическими файлами.

## 9. Dogfood-план (первый потребитель — ai-platform)

1. Новый компилятор собирает `.ai/` ai-platform из канона (репо ai-instructions) + проектных дополнений (AGENTS.md триада referenced in place + при необходимости `.ai/`-дополнения).
2. Миграция существующего `.kilo`: файлы-выход ai-instructions 0.6.3 заменяются новым generated-слоем; ручные файлы (без stamp) сохраняются untouched.
3. Проверка в check-suite: `make check MARKER=ai-instructions` (дрейф по ai-instructions.lock + детерминизм двойного прогона).
4. Рабочий цикл платформы (make check, агенты Architect/Coder/QA, gate-тринити) не деградирует — финальная верификация.

## 10. Открытые вопросы (решаются в DevPlan)

| # | Вопрос | Варианты |
|---|--------|----------|
| O1 | Watcher: stdlib polling vs watchdog | stdlib (0 зависимостей) предпочтителен; watchdog — если polling >1s задержки |
| O2 | Полный набор директив MODULE_CONTRACT | кандидаты: language, stack, model, description — сверка с потребностями kilo-фронтматтера |
| O3 | Миграционная карта контента | какие из 4 ролей, 13 skills, 18 inline-гранул, constitution переносятся 1:1, какие объединяются/переписываются |
| O4 | OCI-артефакт | нужен ли в v1, или достаточно GitHub Release tarball + git tag |
| O5 | Порядок публикации | как платформа пиннит tag@digest: в каком файле SoT (по образцу practices pins) |

## 11. Критерии приёмки

1. `ai-instructions sync` в корне ai-platform собирает .ai из канона + проектных дополнений детерминированно; повторный прогон — no-op (<10s, хэши совпадают).
2. Изменение одного markdown-источника → пересборка ровно его выходного файла (1:1).
3. Коллизия проекта с protected-правилом канона → fail-fast с точным сообщением (id + оба пути).
4. Ручные файлы в .ai/ (без stamp) не перезаписываются ни при каких прогонах.
5. `ai-instructions check` ловит ручное изменение generated-файла (дрейф по ai-instructions.lock).
6. `ai-instructions pack` выдаёт валидный единый markdown; проверено на структуре deepseek-harness.
7. ai-platform работает на новых инструкциях: `make check` зелёный, агенты Architect/Coder/QA функционируют в kilo.
8. Конвейер bundlekit (компиляция старого формата) выведен из эксплуатации; старый код не вызывается новым конвейером.

$END_BRIEF
