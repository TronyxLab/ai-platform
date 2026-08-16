<!-- GREP_SUMMARY: DevPlan ai-instructions конвенционный-компилятор канон-0.7.0 bundlekit-списание dogfood hermes-skills scaffold-проекты роли-как-скиллы protected-id emitter lock pack watcher -->
<!-- STRUCTURE: ▶ $ARTIFACT_CONTRACT → ◇ контекст/решения-интервью → ◇ Draft Code Graph (XML) → ◇ Data Flow → ◇ Waves (W1-W7: контент → runtime → списание+релиз → dogfood → проекты → гейты → verify) → ⊕ File Manifest → ⎋ Acceptance + TRAP -->
# region MODULE_CONTRACT
## @purpose  DevPlan задачи 001 «ai-instructions»: конвенционный компилятор инструкций (канон + проект),
##           замена bundlekit-машинерии, dogfood в ai-platform, наследование шаблонами проектов
## @scope    Два репо: ai-instructions (контент-канон 0.7.0 + runtime + списание bundlekit + релиз)
##           и ai-platform (dogfood-сборка .kilo, hermes build/skills, scaffold-доставка в проекты, гейты)
## @invariants
##   - Канон = контент framework-source 0.6.3 переносится 1:1 (контент не меняется; только
##     XML-обёртка → markdown + MODULE_CONTRACT); все файлы канона @protected
##   - 1 granule/секция = 1 файл (23 правила: 4 секции constitution + 19 гранул); 13 скиллов; 4 роли
##   - Роли фреймворка в hermes эмитятся как СКИЛЛЫ role-<id> (явный вызов @role-architect),
##     НЕ как профили (профили hermes — отдельная ручная система)
##   - Runtime — stdlib + pyyaml (единственная зависимость); pip install из git-repo в venv платформы
##   - .kilo/ generated коммитится; ручные файлы (без stamp) — never overwritten
##   - Шаблоны проектов не содержат снапшот инструкций — scaffold генерит из живого канона
##     (уровни по @language/@stack: all/backend/frontend)
## @rationale Решения интервью (17 ответов) + уточнения пользователя к брифу: канон = фактический
##            контент ai-instructions (не 5 синтетических групп), проекты в скоупе v1, OCI сразу,
##            роли фреймворка ≠ роли hermes
# endregion MODULE_CONTRACT

$ARTIFACT_CONTRACT
PURPOSE:               Реализовать ai-instructions 0.7.0 — конвенционный компилятор инструкций
                       (walk → resolve → emit → lock) и подключить платформу как первого потребителя:
                       сборка .kilo из канона + проектных дополнений, портирование скиллов в hermes-agent,
                       доставка инструкций в проекты через scaffold с уровнями наследования.
DESCRIPTION:           Репо ai-instructions: контент-канон 0.7.0 (перенос framework-source 1:1:
                       23 правила, 13 скиллов, 4 роли) + новый runtime (stdlib+pyyaml: walker/resolver/
                       emitter/locker/packer/watcher/CLI) + списание bundlekit + релиз (tag v0.7.0,
                       GitHub Release tarball, OCI ghcr.io). Репо ai-platform: SoT-пин tag@digest,
                       make-таргеты ai-instructions-sync/check, первый sync (.kilo + hermes build/skills/ +
                       ai-instructions.lock), check-suite-сьют, scaffold-доставка в проекты
                       (ai-sync таргет, requires_instructions_version), роли канона → hermes-скиллы
                       role-<id> (взамен профилей).
RATIONALE:             Интервью (17 решений, см. §0): канон — вычитанный контент 0.6.3 без изменений
                       (3 месяца эксплуатации); конвенция «папка решает всё» вместо XML-машинерии;
                       ссылки вместо склейки (34% шума); проекты — в скоупе v1 (уровни наследования
                       через @language/@stack); OCI сразу (полный supply-chain); роли гермеса ≠ роли
                       фреймворка (вызов через @role-<id> как скилл).
ACCEPTANCE_CRITERIA:   См. §5 (9 критериев брифа + 6 дополненных для расширенного скоупа).
IMPLEMENTS:            Brief 01-Brief.md (вариант D + 15 решений интервью), DevPlan 002 (L1-collapse —
                       единый образ hermes, build/ = payload base-стадии), инварианты платформы 10-12.
IMPACTS:               ai-instructions (весь репо: контент, runtime, списание bundlekit, релиз),
                       ai-platform (core/internal/ai_instructions/, makefiles, .kilo/, core/modules/
                       hermes-agent/build/skills/, scaffold, check-suite, entrypoint-manifest, AGENTS.md),
                       проекты платформы (scaffold + ai-sync), deepseek-harness (pack-проверка).
REQUIRES:              Локальные клоны ai-instructions и ai-platform (ворктри 002-hermes-l1-collapse),
                       kilo (потребитель), npm-пакет @deepseek-ai/dsh@0.1.0-rc.6 (глобально),
                       Docker (для дымового прогона), ghcr.io-токен (OCI-артефакт).
$END_ARTIFACT_CONTRACT

$START_DEVPLAN

# DevPlan 001 — ai-instructions: конвенционный компилятор инструкций

## 0. Контекст и решения интервью

Текущее состояние: инструкции платформы (`.kilo/rules|agents|skills`) сгенерированы
ai-instructions 0.6.3 — bundlekit-машинерией (XML framework-manifest, секции с priority,
targets/kilo|hermes, dual-config). Платформа не интегрирована с компилятором (Makefile/check-suite
чистые); stamp `<!-- ai-instructions:0.6.3 -->` есть в 6 rules, 6 agents, 12 skills;
`_project.md` — ручной (без stamp). Реализация ложится в ворктри
`/Users/tronyx/projects/ai-platform-002-l1-collapse` (ветка `002-hermes-l1-collapse`) поверх
DevPlan 002: hermes — единый образ, `build/skills/` = платформенные артефакты base-стадии
(все org-зеркала), `context/skills/` = контекстный overlay (вне scope 001).

| # | Решение (итог интервью) | Детали |
|---|--------------------------|--------|
| R1 | Скоуп: оба репо, один DevPlan | ai-instructions: контент+runtime+списание+релиз; ai-platform: dogfood+проекты+гейты |
| R2 | Канон = контент framework-source 1:1 | Без переписывания контента; 23 правила (4 секции constitution + 19 гранул), 13 скиллов, 4 роли; все @protected |
| R3 | 1 granule/секция = 1 файл | kebab-имена; testing/* и traps/* раскладываются плоско (testing-pytest-infra, traps-decision, …) |
| R4 | Все 12 скиллов платформы → канон | + 13-й drift-detection (есть в 0.6.3, нет в .kilo платформы) — появится при sync |
| R5 | Скиллы (канон+платформа) → hermes build/skills/ | Без фильтра; context/skills/ — вне scope 001; hermes-нативные (monitor-*, server-status) не трогаются |
| R6 | Роли фреймворка → hermes как СКИЛЛЫ | role-architect/role-coder/role-qa/role-sysadmin в build/skills/, явный вызов @role-<id>; профили hermes — ручные, не трогаем |
| R7 | Шаблоны проектов — реализовать в v1 | Scaffold доставляет инструкции в проекты; уровни: all/backend/frontend по @language/@stack |
| R8 | Watcher: stdlib polling | mtime+hash, debounce, 0 зависимостей |
| R9 | Директивы: + language/stack | @protected, @order, @roles, @model, @description, @language, @stack (+обязательные @purpose/@scope/@invariants) |
| R10 | Дистрибуция: tag + tarball + OCI сразу | v0.7.0; GitHub Release tarball; ORAS-артефакт ghcr.io/ai-instructions |
| R11 | Pin в новом SoT-манифесте | core/internal/ai_instructions/ai-instructions-pins.yaml + parity-гейт |
| R12 | Доставка runtime: pip из git-repo | pyproject; venv платформы; pin в requirements |
| R13 | explore/scanner → .ai/roles/ платформы | kilo-специфичные subagents (mode: subagent, hidden) — только kilo, не hermes |
| R14 | _project.md → .ai/rules/ платформы | Эмитится обратно в .kilo/rules/ как generated со stamp |
| R15 | .kilo/ generated коммитится | + дрейф-гейт в check-suite (по образцу инварианта 11) |
| R16 | Глагол: ai-instructions-sync | Регистрация в entrypoint-manifest.yaml (namelint, allowed_verbs) |
| R17 | Bundlekit: удалить сразу | bundlekit/ + framework-source/ + dual-config; история в git |

**Расхождения с брифом (зафиксировано интервью):** канон — фактический контент 0.6.3,
а не 5 синтетических групп (§2 решение 9 брифа); OCI — в v1 (бриф: вторая итерация);
роли→профили hermes отменено (R6); проекты — в v1 (бриф: волны 3+); директивы расширены (R9).

**Миграционная карта канона (framework-source → новое дерево):**

| Источник 0.6.3 | Назначение v0.7.0 |
|----------------|-------------------|
| constitution.xml: CONSTITUTION, COMMUNICATION, PRINCIPLES, MARKUP | rules/constitution.md, rules/communication.md, rules/principles.md, rules/markup.md |
| granules/inline/{bash_output_to_tmp,completion,doxygen_generic,fail_fast,mini_diagrams,search-escalation,semantic_distillation}.xml | rules/{bash-output-to-tmp,completion,doxygen-generic,fail-fast,mini-diagrams,search-escalation,semantic-distillation}.md |
| granules/inline/artifacts/artifact-registry.xml | rules/artifact-registry.md |
| granules/inline/superposition/superposition.xml | rules/superposition.md |
| granules/inline/testing/{anti_loop,ldd_telemetry,pytest_infra,test_honesty}.xml | rules/testing-{anti-loop,ldd-telemetry,pytest-infra,test-honesty}.md |
| granules/inline/traps/{perf,debt,incident,bug,decision,business}_trap.xml | rules/traps-{perf,debt,incident,bug,decision,business}.md |
| granules/skills/<name>.xml (13) | skills/<name>/SKILL.md (frontmatter name/description сохраняется, тело 1:1) |
| roles/{architect,coder,qa,sysadmin}.xml | roles/<name>/role.md (секции 1:1; у sysadmin +ANTI_LOOP/CONNECTION_CONTEXT/PREFLIGHT/SECURITY) |

**Имена ролей:** канон использует id `coder`; процессы платформы — `Code`
(task subagent_type). Решение: emit-алиас `coder → code` в платформенном конфиге (R-алиас,
см. TRAP[3] §6) — канон не переименовывается, существующие процессы не ломаются.

## 1. Draft Code Graph (XML)

```xml
<Module name="ai_instructions_runtime_walker_py" TYPE="python" keywords="walk,canon,consumer-tree,id">
  <Func name="walk_tree" annotation="обход канона + .ai/ потребителя; id = относительный путь без расширения"/>
  <Func name="collect" annotation="*.md → {id: (source_path, kind, directives)}; kind из позиции: rules|roles|skills|playbooks|policies"/>
  <CrossLinks>resolver_py, canon_source_py</CrossLinks>
</Module>

<Module name="ai_instructions_runtime_canon_source_py" TYPE="python" keywords="pin-cache,git-clone,tag">
  <Func name="resolve" annotation="--canon-path → pin-кэш (~/.cache/ai-instructions/<tag>) → git clone --depth 1 --branch <tag>"/>
  <CrossLinks>walker_py, cli_py</CrossLinks>
</Module>

<Module name="ai_instructions_runtime_resolver_py" TYPE="python" keywords="protected-id,override,aliases,effective-map">
  <Func name="resolve" annotation="protected+коллизия → fail-fast (id+оба пути); non-protected → проект замещает; новые id add-only"/>
  <Func name="apply_aliases" annotation="role_aliases: coder→code (платформенный конфиг)"/>
  <CrossLinks>walker_py, emitter_py</CrossLinks>
</Module>

<Module name="ai_instructions_runtime_emitter_py" TYPE="python" keywords="1-1,stamp,never-overwrite,frontmatter,hermes-roles-as-skills">
  <Func name="emit" annotation="rules→.kilo/rules/; roles→.kilo/agents/ (frontmatter 1:1 + stamp); skills→.kilo/skills/ + hermes build/skills/; playbooks→skills/playbook-<n>/"/>
  <Func name="emit_hermes_roles" annotation="roles канона → build/skills/role-<id>/SKILL.md (name: role-<id>, вызов @role-<id>); роли с mode:subagent — только kilo"/>
  <Func name="cleanup_orphans" annotation="stamped-файлы, отсутствующие в effective-карте, удаляются; файлы без stamp — never overwritten"/>
  <Func name="emit_project_mode" annotation="--project-dir: фильтр @language/@stack (all|backend|frontend); kilo.json manage_config; hermes=false"/>
  <CrossLinks>lock_py, resolver_py</CrossLinks>
</Module>

<Module name="ai_instructions_runtime_lock_py" TYPE="python" keywords="sha256,drift,ai-instructions-lock">
  <Func name="write_lock" annotation="canon_version + platform_version(git-sha) + files[]: path/sha256/source"/>
  <Func name="check_drift" annotation="сверка хэшей lock ↔ фактические файлы (ai-instructions check)"/>
  <CrossLinks>emitter_py</CrossLinks>
</Module>

<Module name="ai_instructions_runtime_packer_py" TYPE="python" keywords="pack,deterministic,order">
  <Func name="pack" annotation="единый markdown: канон → проект → путь → @order; заголовки секций из путей"/>
  <CrossLinks>resolver_py</CrossLinks>
</Module>

<Module name="ai_instructions_runtime_watcher_py" TYPE="python" keywords="polling,mtime-hash,debounce">
  <Func name="watch" annotation="stdlib polling канона + .ai/ + AGENTS.md; debounce; пересборка только затронутых (1:1)"/>
  <CrossLinks>cli_py, emitter_py</CrossLinks>
</Module>

<Module name="ai_instructions_runtime_cli_py" TYPE="python" keywords="sync,watch,pack,check,manage-config">
  <Func name="main" annotation="subcommands: sync|watch|pack|check; --config <pins.yaml>; manage_config: kilo.json instructions += .kilo/rules/*.md (пользовательские ключи сохраняются)"/>
  <CrossLinks>emitter_py, lock_py, watcher_py, packer_py</CrossLinks>
</Module>

<Module name="ai_platform_internal_ai_instructions_pins_yaml" TYPE="yaml" keywords="SoT,pin,tag-digest,aliases">
  <Entry name="canon" annotation="tag@digest v0.7.0 (digest-pin канон платформы)"/>
  <Entry name="role_aliases" annotation="{coder: code}"/>
  <Entry name="hermes" annotation="roles_as_skills: true"/>
  <Entry name="templates" annotation="requires_instructions_version"/>
  <CrossLinks>makefiles, check_suite_yaml</CrossLinks>
</Module>

<Module name="ai_platform_scaffold_project_scaffolder_py" TYPE="python" keywords="ai-sync,requires-instructions-version">
  <Func name="scaffold_instructions" annotation="после gen_project_practices: ai-instructions sync --project-dir (уровень из template: backend|frontend|all)"/>
  <CrossLinks>ai_instructions_runtime_cli_py, gen_project_makefile</CrossLinks>
</Module>
```

## 2. Data Flow (пошагово)

**SCENARIO_SYNC (платформа):** `make ai-instructions-sync` →
`python3 -m ai_instructions sync --config core/internal/ai_instructions/ai-instructions-pins.yaml`
→ canon_source: локальный клон (~/projects/ai-instructions) → pin-кэш → git clone по tag →
walker (канон 23+13+4 + .ai/ платформы: rules/_project.md, roles/explore, roles/scanner) →
resolver (protected-id, aliases coder→code, effective-карта) →
emitter (rules→.kilo/rules/, roles→.kilo/agents/ с frontmatter 1:1+stamp,
skills→.kilo/skills/ + core/modules/hermes-agent/build/skills/,
роли канона→build/skills/role-<id>/, cleanup stamped-сирот) →
lock (ai-instructions.lock: sha256) → повторный прогон — no-op (хэши совпадают).

**SCENARIO_PROJECT (scaffold):** `make new-project NAME=<n> TEMPLATE=template-frontend` →
project_scaffolder → после gen_project_practices → `ai-instructions sync --project-dir <dir>
--template frontend` → фильтр по @language/@stack (all + frontend) → .kilo проекта (rules/agents/
skills) + kilo.json (manage_config: instructions += [".kilo/rules/*.md"]) + ai-instructions.lock →
git_init (файлы в init-коммит).

**SCENARIO_PROJECT_UPDATE:** в папке проекта `make ai-sync` →
`$(MAKE) -C $(PLATFORM_DIR) ai-instructions-sync PROJECT=$(CURDIR)` → платформенный компилятор
пересобирает .kilo проекта из текущего канона (pin) + .ai/ проекта; дрейф → check.

**SCENARIO_PACK:** `ai-instructions pack --out instructions.md --config …` → resolved-дерево →
единый markdown (порядок: канон → проект → путь → @order) → проверка на структуре
@deepseek-ai/dsh 0.1.0-rc.6.

**SCENARIO_WATCH:** `ai-instructions watch` → stdlib polling (mtime+hash, debounce 500ms) →
изменение файла канона/.ai/ → пересборка только затронутых выходов (1:1) → lock-обновление.

## 3. Волны и задачи

### W1 — ai-instructions: контент-канон 0.7.0 (миграция 1:1)

- **T1.1** `VERSION` → `0.7.0`; создать дерево: `rules/` (23), `roles/` (4), `skills/` (13),
  `playbooks/` (пусто, .gitkeep), `policies/` (пусто, .gitkeep).
- **T1.2** constitution.xml → 4 правила: секции CONSTITUTION/COMMUNICATION/PRINCIPLES/MARKUP
  → `rules/{constitution,communication,principles,markup}.md`; XML-обёртка → MODULE_CONTRACT
  (+`@protected  true`), GREP_SUMMARY/комментарии сохраняются, тела секций — байт-в-байт.
- **T1.3** 19 inline-гранул → `rules/*.md` по карте §0 (kebab-имена, вложенные — плоско);
  каждая с MODULE_CONTRACT + `@protected  true`.
- **T1.4** 13 скиллов → `skills/<name>/SKILL.md`: frontmatter (name/description) сохраняется,
  `<skill>`-обёртка → MODULE_CONTRACT + `@protected  true`, markdown-тело 1:1.
- **T1.5** 4 роли → `roles/<name>/role.md`: YAML frontmatter (name/description — минимальный),
  секции XML → markdown-заголовки (контент 1:1), MODULE_CONTRACT, GREP_SUMMARY/STRUCTURE.
- **T1.6** Проверка: `make ai-instructions sync --canon-path .` на синтетическом потребителе —
  эмиссия 1:1 (diff тел с исходными XML-секциями без изменений).

### W2 — ai-instructions: runtime компилятора

- **T2.1** `pyproject.toml`: package `ai_instructions`, console_script `ai-instructions`,
  единственная зависимость pyyaml; python ≥3.11.
- **T2.2** `runtime/canon_source.py`: резолв канона (--canon-path → pin-кэш
  `~/.cache/ai-instructions/<tag>` → `git clone --depth 1 --branch <tag>`; fail-fast при
  недоступности всех трёх).
- **T2.3** `runtime/walker.py`: обход канона + `.ai/` потребителя; id = относительный путь
  без расширения; kind из позиции (rules|roles|skills|playbooks|policies); парсинг
  MODULE_CONTRACT-директив (R9-набор) и YAML frontmatter ролей.
- **T2.4** `runtime/resolver.py`: effective-карта — protected+коллизия → ОШИБКА
  (fail-fast: id + оба пути); non-protected — проект замещает; новые id — add-only;
  `role_aliases` (coder→code); валидация директив (неизвестная директива → warn).
- **T2.5** `runtime/emitter.py`: 1:1 маппинг — rules→`.kilo/rules/`, roles→`.kilo/agents/`
  (frontmatter 1:1 + stamp), skills→`.kilo/skills/<n>/SKILL.md` + hermes
  `build/skills/<n>/SKILL.md` (1:1), playbooks→`skills/playbook-<n>/`, policies→дерево `.kilo/`;
  роли канона→hermes `build/skills/role-<id>/SKILL.md` (роли с frontmatter
  `mode: subagent` — только kilo); stamp `<!-- ai-instructions:<version> -->`;
  never-overwrite (файл без stamp — ручной); cleanup stamped-сирот; project-mode
  (--project-dir: фильтр @language/@stack → all|backend|frontend, hermes=false).
- **T2.6** `runtime/lock.py`: `ai-instructions.lock` по схеме брифа §8 (canon_version,
  platform_version = git-sha потребителя, files[]: path/sha256/source).
- **T2.7** `runtime/packer.py`: детерминированный pack (канон → проект → путь → @order;
  заголовки секций из путей).
- **T2.8** `runtime/watcher.py`: stdlib polling (mtime+hash, debounce 500ms, пересборка только
  затронутых выходов).
- **T2.9** `runtime/cli.py`: `{sync, watch, pack, check}`; `--config` (потребительский
  конфиг: pin/aliases/emit-флаги); `manage_config` — kilo.json: instructions +=
  [".kilo/rules/*.md"], пользовательские ключи сохраняются (паттерн bundlekit manage_config).
- **T2.10** Тесты: полный rewrite `tests/` (walker/resolver/emitter/lock/packer/watcher/
  canon_source/cli; protected-fail-fast, never-overwrite, determinism, aliases,
  project-filter, frontmatter 1:1); ruff + LDD (IMP:7-10, IMP:9 в успешных сценариях).
- **T2.11** CI ai-instructions: `.github/workflows/ci.yml` (pytest + ruff, push/PR).

### W3 — ai-instructions: списание bundlekit + релиз 0.7.0

- **T3.1** Удалить `bundlekit/`, `framework-source/`, `build/` (bdist), `ai-instructions.yaml`,
  `kilo-config.yaml` (dual-config), `MANIFEST.in`, `Doxyfile`, `ai_instructions.egg-info/`.
- **T3.2** `README.md`: переписать под 0.7.0 (дерево, формат, CLI, потребители).
- **T3.3** Релиз: git tag `v0.7.0` → GitHub Release tarball контент-дерева
  (VERSION, rules/, roles/, skills/, playbooks/, policies/).
- **T3.4** OCI-артефакт: workflow ORAS-push `ghcr.io/ai-instructions/ai-instructions:v0.7.0`
  (public package, tarball как слой-артефакт).
- **T3.5** `research/` — сохранить (история исследования), README-note «archived 0.6.3».

### W4 — ai-platform: dogfood-ядро (SoT → entrypoint → первый sync)

- **T4.1** `core/internal/ai_instructions/ai-instructions-pins.yaml` (SoT, коммитится):
  canon pin `v0.7.0@<digest>` (digest-pin канон платформы), `role_aliases: {coder: code}`,
  `hermes: {roles_as_skills: true}`, `templates: {requires_instructions_version: …}`;
  MODULE_CONTRACT + parity-гейт (формат tag@digest).
- **T4.2** Makefile: `ai-instructions-sync` (PROJECT|--project-dir, TEMPLATE) →
  `python3 -m ai_instructions sync --config …`; регистрация в `core/entrypoint-manifest.yaml`
  (allowed_verbs + глоссарий) → `make generate-entrypoint-manifest`; namelint зелёный.
- **T4.3** Dev-setup: pip install ai-instructions из git (pin в requirements/dev);
  идемпотентно (повторный вызов — no-op); venv платформы.
- **T4.4** Миграция `.ai/` платформы: `_project.md` → `.ai/rules/_project.md`;
  `explore.md`/`scanner.md` → `.ai/roles/{explore,scanner}/role.md` (frontmatter 1:1).
- **T4.5** Первый sync: пересборка `.kilo/` (rules 23+1, agents 6 — coder→code, skills 13),
  `core/modules/hermes-agent/build/skills/` (+13 канон, +4 role-скиллы), удаление старых
  stamped-файлов, `ai-instructions.lock` в корне (коммитится).
- **T4.6** check-suite: сьют `ai-instructions` (`core/check-suite.yaml`): drift
  (lock ↔ файлы), детерминизм (двойной прогон no-op), pins-parity (формат tag@digest,
  соответствие SoT); `make check MARKER=ai-instructions`.

### W5 — ai-platform: проекты и шаблоны (наследование)

- **T5.1** Emitter project-mode (в W2.5) + CLI-флаги; уровни наследования:
  all (без директив) / backend (@language: python) / frontend (@stack: react, @language: typescript).
- **T5.2** `project_scaffolder.py`: после `gen_project_practices` — `scaffold_instructions()`
  (ai-instructions sync --project-dir); `template.yaml` шаблонов: поле
  `requires_instructions_version` + сверка при scaffold (по образцу practices, fail при новее).
- **T5.3** `scaffold_helpers.gen_project_makefile`: таргет `ai-sync` →
  `@$(MAKE) -C $(PLATFORM_DIR) ai-instructions-sync PROJECT=$(CURDIR)`.
- **T5.4** `project_adopter.py` (adopt-project): ai-sync после адаптации.
- **T5.5** Обновление проектов: `make ai-instructions-sync PROJECT=<dir>` из платформы —
  пересборка .kilo проекта + lock + дрейф-детект; шаблоны payload-снапшотов НЕ содержат
  (TRAP[2] §6).

### W6 — ai-platform: тесты, гейты, доки

- **T6.1** `tests/unit/test_ai_instructions_*.py` (платформенная обёртка-интеграция):
  protected-fail-fast, never-overwrite, determinism, aliases (coder→code), project-filter
  (@language/@stack), manage_config (kilo.json ключи сохраняются), lock-дрейф.
- **T6.2** Гейты: namelint (ai-instructions-sync в allowed_verbs),
  `test_gate_ai_instructions_pins.py` (SoT-формат + parity), check-suite-сьют (T4.6).
- **T6.3** Доки: `AGENTS.md` root (глоссарий — generate-agents-md, навигация, контракт
  окружения проекта: ai-sync), `core/AGENTS.md` (каталог операций, SoT-файл),
  `core/modules/AGENTS.md` (build/skills/ — generated зона ai-instructions,
  hermes-нативные скиллы — ручные), `.dockerignore` (если требуется для build/skills/).
- **T6.4** `core/internal/static/dead_code.py` / audit-списки: bundlekit-упоминания в
  платформе отсутствуют (проверка — платформа не ссылалась на ai-instructions).

### W7 — Верификация

- **T7.1** `make ai-instructions-sync` ×2: второй прогон no-op <10s, хэши совпадают;
  `ai-instructions check` — чистый.
- **T7.2** `make check` зелёный (включая `MARKER=ai-instructions`); `make agent-check` clean.
- **T7.3** Pack на deepseek-harness: исследовать потребление инструкций в
  @deepseek-ai/dsh@0.1.0-rc.6 (npm root -g), `ai-instructions pack --out` → валидация.
- **T7.4** Дымовой (если Docker доступен): `make hermes-build-context CONTEXT=test` —
  скиллы канона + role-скиллы в образе (`/opt/hermes/skills/`).
- **T7.5** Dev-цикл платформы не деградирует: `make check` + агенты kilo функционируют
  (Architect/Coder/QA — имена сохранены через alias).

## 4. File Manifest

### Репо ai-instructions

| Действие | Файл |
|----------|------|
| CREATE | `rules/constitution.md`, `rules/communication.md`, `rules/principles.md`, `rules/markup.md` |
| CREATE | `rules/artifact-registry.md`, `rules/superposition.md`, `rules/bash-output-to-tmp.md`, `rules/completion.md`, `rules/doxygen-generic.md`, `rules/fail-fast.md`, `rules/mini-diagrams.md`, `rules/search-escalation.md`, `rules/semantic-distillation.md` |
| CREATE | `rules/testing-anti-loop.md`, `rules/testing-ldd-telemetry.md`, `rules/testing-pytest-infra.md`, `rules/testing-test-honesty.md` |
| CREATE | `rules/traps-perf.md`, `rules/traps-debt.md`, `rules/traps-incident.md`, `rules/traps-bug.md`, `rules/traps-decision.md`, `rules/traps-business.md` |
| CREATE | `roles/{architect,coder,qa,sysadmin}/role.md` |
| CREATE | `skills/<13×name>/SKILL.md` (из granules/skills) |
| CREATE | `playbooks/.gitkeep`, `policies/.gitkeep` |
| CREATE | `runtime/{__init__,cli,walker,resolver,emitter,lock,packer,watcher,canon_source,config}.py` |
| CREATE | `pyproject.toml` (package + console_script) |
| CREATE | `.github/workflows/ci.yml`, `.github/workflows/oci-release.yml` |
| CREATE | `tests/` (rewrite под runtime) |
| MODIFY | `VERSION` (0.6.3 → 0.7.0), `README.md` |
| DELETE | `bundlekit/`, `framework-source/`, `build/`, `ai-instructions.yaml`, `kilo-config.yaml`, `MANIFEST.in`, `Doxyfile`, `ai_instructions.egg-info/` |

### Репо ai-platform (ворктри 002-hermes-l1-collapse)

| Действие | Файл |
|----------|------|
| CREATE | `core/internal/ai_instructions/ai-instructions-pins.yaml` (SoT) |
| CREATE | `.ai/rules/_project.md` |
| CREATE | `.ai/roles/{explore,scanner}/role.md` |
| CREATE | `ai-instructions.lock` (generated, коммитится) |
| CREATE | `tests/unit/test_ai_instructions_sync.py`, `tests/gates/test_gate_ai_instructions_pins.py` |
| CREATE | `makefiles/ai-instructions.mk` (или секция в dev.mk — по решению Coder) |
| MODIFY | `Makefile` (include), `core/check-suite.yaml` (сьют ai-instructions), `core/entrypoint-manifest.yaml` (+generated) |
| MODIFY | `core/internal/scaffold/project_scaffolder.py`, `scaffold_helpers.py` (gen_project_makefile: ai-sync), `project_adopter.py` |
| MODIFY | `templates/template-backend/template.yaml`, `templates/template-frontend/template.yaml` (requires_instructions_version) |
| MODIFY | `.kilo/rules/*`, `.kilo/agents/*`, `.kilo/skills/*` (пересборка sync'ом) |
| MODIFY | `core/modules/hermes-agent/build/skills/*` (+13 канон, +4 role-скиллы) |
| MODIFY | `AGENTS.md` (root), `core/AGENTS.md`, `core/modules/AGENTS.md` |
| MODIFY | requirements/dev (pin ai-instructions) |
| DELETE | `.kilo/rules/{constitution,principles,testing,artifacts,communication,markup}.md` (старые stamped 0.6.3) |

## 5. Acceptance Criteria

1. `make ai-instructions-sync` в корне ai-platform собирает `.kilo` (24 правила, 6 агентов,
   13 скиллов) и портирует скиллы в hermes `build/skills/` (13 + 4 role-скилла)
   детерминированно; повторный прогон — no-op (<10s, хэши совпадают).
2. Изменение одного источника → пересборка ровно его выходного файла (1:1).
3. Коллизия проекта с @protected-правилом канона → fail-fast с id + оба пути.
4. Ручные источники `.ai/` и ручные файлы `.kilo/` (без stamp, включая hermes-нативные
   скиллы monitor-*) не перезаписываются ни при каких прогонах.
5. `ai-instructions check` ловит ручное изменение generated-файла (дрейф по lock);
   `make check MARKER=ai-instructions` зелёный в CI.
6. `ai-instructions pack` выдаёт валидный единый markdown; проверено на структуре
   @deepseek-ai/dsh 0.1.0-rc.6.
7. ai-platform работает на новых инструкциях: `make check` зелёный, `make agent-check`
   clean, агенты Architect/Coder/QA функционируют (alias coder→code).
8. bundlekit выведен из эксплуатации: `bundlekit/` и `framework-source/` удалены из репо
   ai-instructions; старый код не вызывается новым конвейером.
9. Скиллы канона + role-скиллы запечены в единый образ hermes (build/skills/);
   hermes healthy, роли вызываются явно @role-<id>.
10. Релиз 0.7.0: git tag v0.7.0 + GitHub Release tarball + OCI-артефакт ghcr.io.
11. Pin: `ai-instructions-pins.yaml` содержит tag@digest; parity-гейт зелёный.
12. Scaffold нового проекта (backend и frontend) кладёт .kilo + lock с уровнями
    наследования (@language/@stack); `make ai-sync` в проекте пересобирает.
13. `make ai-instructions-sync PROJECT=<dir>` обновляет существующий проект;
    requires_instructions_version сверяется при scaffold.
14. Идемпотентность dev-setup: повторный pip install — no-op (bootstrap не деградирует).
15. Watcher (`ai-instructions watch`): правка файла канона/.ai/ → пересборка затронутых
    выходов без полного sync (stdlib polling).

## 6. Риски и TRAP

⚠️ TRAP[DECISION] · — · Роли фреймворка ≠ роли hermes (O6)
· Rejected: генерация profiles/<name>/SOUL.md + config.yaml из roles канона.
· Reason: в hermes свои роли (профили default/platform/research — ручные, вычитаны отдельно);
·   роли фреймворка — инструкции-скиллы: build/skills/role-<id>/SKILL.md, явный вызов
·   @role-<id>. Канон не дублирует профильную систему hermes.
· Rev: если hermes-агент начнёт консумировать роли фреймворка как системные роли —
·   пересмотреть маппинг role→profile.

⚠️ TRAP[DECISION] · — · Роли — YAML frontmatter в источнике; правила/скиллы — MODULE_CONTRACT-директивы
· Rejected: роли только на директивах (@model/@description/…) с выводом frontmatter.
· Reason: kilo-специфичные поля ролей (mode: subagent, hidden, steps, temperature, color,
·   permission-дерево) невыразимы компактным набором директив; текущий frontmatter вычитан
·   месяцами эксплуатации. Компилятор эмитит frontmatter 1:1 + stamp.
· Rev: если hermes-скиллам ролей понадобится конфиг из директив — добавить @hermes-*.

⚠️ TRAP[DECISION] · — · Шаблоны проектов не содержат снапшот инструкций
· Rejected: payload-снапшоты .kilo в templates/template-* (как practices-файлы).
· Reason: инструкции генерируются из ЖИВОГО канона платформы при scaffold (по образцу
·   gen_project_practices) — обновление канона = обновление всех будущих проектов без
·   ре-релиза шаблонов; шаблон несёт только requires_instructions_version (анти-дрейф).
· Rev: если появится требование офлайн-scaffold (без платформы) — ввести снапшоты в шаблоны.

⚠️ TRAP[DECISION] · — · Emit-алиас ролей coder→code (платформенный конфиг)
· Rejected: переименование канонной роли coder→code ИЛИ принятие канонных имён платформой.
· Reason: канон переносится 1:1 (id coder); процессы платформы (task subagent_type: Code,
·   kilo.json agent.code) завязаны на Code — алиас сохраняет оба мира без дублирования.
· Rev: если канон примет id code — удалить alias из pins.

⚠️ TRAP[DECISION] · — · OCI-артефакт в v1 (ORAS, public ghcr.io/ai-instructions)
· Rejected: только GitHub Release tarball (бриф: OCI — вторая итерация).
· Reason: решение интервью R10 — полный supply-chain сразу; digest-pin платформы
·   распространяется на артефакт канона.
· Rev: если OCI-канал не будет использоваться платформой >1 релиза — вернуть tarball-only.

⚠️ TRAP[DECISION] · — · pyyaml — единственная runtime-зависимость
· Rejected: stdlib-only (ручной парсинг YAML frontmatter).
· Reason: kilo frontmatter — произвольный YAML (permission-дерево ролей); ручной парсер
·   = баги и галлюцинации структуры. Бриф допускает pyyaml для manifest-склейки.
· Rev: если frontmatter-потребление сведётся к плоским ключам — вернуть stdlib-only.

**Риски:** (1) merge в main после параллельной сессии — .kilo/ пересобран в ворктри 002,
возможны конфликты с main в .kilo/ при мерже → стратегия: main-сессия не трогает .kilo
(контракт сессии), мерж принять версию ворктри. (2) pin на v0.7.0 до релиза (W4 зависит от
W3) — в dev pin на commit, финальный tag@digest — в W7. (3) дымовой docker build требует
Docker на macOS — при недоступности T7.4 деградирует до проверки COPY-путей статически.

$END_DEVPLAN
