# 06-DevPlan — Бриф E: docs/manifest/TRAP sync

$ARTIFACT_CONTRACT
- PURPOSE: Синхронизация документации, манифестов и TRAP-аннотаций с фактическим состоянием кодовой базы после волн 116 и 117 A/B. Задачи 37–45 программного брифа 117.
- DESCRIPTION: 9 задач: (37) TRAP B8 D2 уже обновлён брифом A — осталась коррекция ensure_context_repo; (38) phantom-глагол platform-deliver → receive; (39) путь config_renderer.py bootstrap/deploy → llm/; (40) healthcheck_poll → healthcheck_poller.py; (41) entrypoint-manifest цепочки bootstrap/node-update — актуализация под B9; (42) Навигация: 5 «Канонический» → 3; (43) platform-test.yml: header ci-docker + inline openssl → make dev-certs; (44) Makefile 45→80 targets, README ci-docker; (45) secret-definitions.yaml email — уже решён, уточнить note.
- RATIONALE: После двух волн (A — deploy-канал, B — dead code) накопились расхождения между документацией и кодом. Без синхронизации агенты будут опираться на устаревшие TRAP/пути/глаголы, порождая ошибочные решения.
- ACCEPTANCE_CRITERIA:
  - AC-E1: `ensure_context_repo` в AGENTS.md ссылается на `context_overlay.py`, не `deploy-modules.sh`.
  - AC-E2: `platform-deliver` — 0 вхождений в AGENTS.md (кроме исторических TRAP с пометкой «удалён»).
  - AC-E3: Путь `config_renderer.py` в AGENTS.md:262 указывает на `core/internal/llm/config_renderer.py`.
  - AC-E4: `healthcheck_poll` в AGENTS.md:43 заменён на `healthcheck_poller.py`.
  - AC-E5: entrypoint-manifest цепочки `bootstrap-node`/`node-update` отражают B9-топологию (state machine, не плоский список скриптов).
  - AC-E6: Навигация AGENTS.md: ровно 3 строки «Канонический» (три AGENTS.md), остальные — «Вспомогательный».
  - AC-E7: platform-test.yml: header/docs → `ci-docker`, inline openssl → `make dev-certs`.
  - AC-E8: Makefile STRUCTURE — 80 targets; README — `MODE=ci-docker` документирован.
  - AC-E9: `make gate MODE=fast`, `make check-manifests` зелёные; glossary/canon_table актуальны после `make generate-agents-md`.
- IMPLEMENTS: 117 01-Brief задачи 37–45.
- IMPACTS: AGENTS.md (root, core/), core/entrypoint-manifest.yaml, .github/workflows/platform-test.yml, README.md, Makefile (STRUCTURE comment).
- REQUIRES: 117 01-Brief (реестр), завершённые брифы A (02-DevPlan) и B (03-DevPlan).

---

## 0. Коррекции к исходному брифу (по результатам верификации)

| Задача | Исходный вердикт брифа | Фактический вердикт | Действие |
|--------|----------------------|---------------------|----------|
| 37 | «TRAP B8 D2 (receive→dispatch)» — требует обновления | **Уже обновлён брифом A** (AGENTS.md:24-30): forced-command = dispatch, setup-node.sh = только sudoers, Rev снято. | TRAP не трогать. Править только `ensure_context_repo`: `deploy-modules.sh` → `context_overlay.py`. |
| 45 | «ci_default email с доменом» — проблема | **Уже решён** (DevPlan 116 T3, U-16): note на строке 156 объясняет, почему литерал `admin@ai-platform.local` корректен (generated .py требуют литералы). Домен совпадает с PLATFORM_DOMAIN SoT. | Закрыть без изменений кода. Уточнить формулировку note. |

---

## 1. Технический анализ и решения

### Задача 37 (HIGH) — ensure_context_repo: shell→Python, обновить AGENTS.md

**Факты (верифицированы):**
- TRAP B8 D2 (AGENTS.md:24-30): **уже обновлён брифом A**. Текст корректен: `orchestrator_cli dispatch`, `setup-node.sh — ТОЛЬКО generate_sudoers`, `Rev: — (B1 реализован)`. Изменений не требует.
- Часть про `ensure_context_repo`:
  - AGENTS.md:88: `deploy-modules.sh → ensure_context_repo` — **stale**. Фактическая реализация: `core/internal/bootstrap/deploy/context_overlay.py` (Python-модуль, 357 строк). `deploy-modules.sh` — 0 вхождений `ensure_context_repo` (подтверждено grep).
  - AGENTS.md:104: `**`ensure_context_repo()`** в `deploy-modules.sh`` — **stale**.
  - Вызывается через `deploy_orchestrator.py:238`: `context_overlay.ensure_context_repo(node_yaml)`.
  - Gate-тест `test_gate_context_overlay_git.py` — валидирует инвариант «git только внутри ensure_context_repo()», но не валидирует локацию в AGENTS.md.

**Решение D37:**
- **D37a (TRAP B8 D2):** без изменений — уже корректен после брифа A.
- **D37b (ensure_context_repo):** исправить 2 строки в AGENTS.md:
  - Строка 88: `deploy-modules.sh → ensure_context_repo` → `context_overlay.py → ensure_context_repo()`.
  - Строка 104: `в `deploy-modules.sh`` → `в `context_overlay.py` (Python-модуль, вызывается из deploy_orchestrator.py)`.

**Файлы:** AGENTS.md:88, :104.

**Риск:** LOW. Чисто документационная правка — код не меняется.

---

### Задача 38 (HIGH) — phantom-глагол `platform-deliver` в Triple Delivery таблице

**Факты (верифицированы):**
- `platform-deliver` **удалён** DevPlan 116 B1 (D1). VerificationReport 21 подтверждает: 0 вхождений в `core/`, `.github/`, `makefiles/`.
- AGENTS.md:98: `| **Project payload** | tar по SSH forced-command (\`platform-deliver\`) | Push (CI) | ...` — **единственное оставшееся вхождение** phantom-глагола в AGENTS.md.
- Актуальный глагол: `receive` (dispatch-канал, `orchestrator_cli dispatch receive`).

**Решение D38:** заменить `platform-deliver` → `receive` в AGENTS.md:98. Проверить consumer-scan: `rg "platform-deliver" AGENTS.md` — должно остаться 0 (кроме исторических TRAP с явной пометкой «удалён»).

**Файлы:** AGENTS.md:98.

**Риск:** LOW. Глагол не существует в коде — документация врёт.

---

### Задача 39 (MED) — путь config_renderer.py: bootstrap/deploy → llm/

**Факты (верифицированы):**
- AGENTS.md:262: `core/internal/bootstrap/deploy/config_renderer.py` — **не существует**.
- Фактический файл: `core/internal/llm/config_renderer.py` (перемещён при рефакторинге LLM-подсистемы).
- Подтверждено `glob **/config_renderer.py` → единственный результат: `core/internal/llm/config_renderer.py`.

**Решение D39:** исправить путь в AGENTS.md:262: `core/internal/bootstrap/deploy/config_renderer.py` → `core/internal/llm/config_renderer.py`.

**Файлы:** AGENTS.md:262.

**Риск:** LOW.

---

### Задача 40 (MED) — «shared/healthcheck_poll» → healthcheck_poller.py

**Факты (верифицированы):**
- AGENTS.md:43: `Python-реализация — ТОЛЬКО shared/healthcheck_poll` — **файл не существует**.
- `glob **/healthcheck_poll*` → единственный результат: `core/internal/deploy/healthcheck_poller.py`.
- Имя модуля: `healthcheck_poller`, не `healthcheck_poll`.

**Решение D40:** исправить `shared/healthcheck_poll` → `deploy/healthcheck_poller.py` в AGENTS.md:43.

**Файлы:** AGENTS.md:43.

**Риск:** LOW. Документационная неточность.

---

### Задача 41 (MED) — entrypoint-manifest: цепочки bootstrap/node-update (до-B9 топология)

**Факты (верифицированы):**
- `entrypoint-manifest.yaml:18-23` (bootstrap-node): перечисляет 9 скриптов плоским списком (`docker_registry_auth.py + firewall.sh + install-docker.sh + ...`), как если бы они вызывались напрямую из `bootstrap.sh`.
- `entrypoint-manifest.yaml:571-575` (node-update): аналогично — `issue-cert.sh + provision + deploy-modules + healthcheck`.
- После B9 (фазовая машина): `node-lifecycle.sh --mode init` делегирует в `lifecycle/cli.py` → `state_machine.py` (18 фаз). Индивидуальные скрипты — шаги фаз, не прямые вызовы из bootstrap.sh.
- Важно: `delegates_to` — **структурная секция**, НЕ generated (генератор `generate_entrypoint_manifest.py` перезаписывает только `allowed_verbs` и `gates[]`, сохраняя structural sections). Правка напрямую — допустима и не будет перезатёрта при `make generate-entrypoint-manifest`.

**Решение D41:**
- **bootstrap-node (строка 18-23):** заменить плоский список на: `bootstrap.sh → preflight.py → node-lifecycle.sh --mode init → lifecycle/cli.py (state machine: 18 phases — docker_registry_auth, firewall, install-docker, install-tor-proxy, setup-node, install-acme, deploy-modules, cert_orchestrator, context_deployer, …)`.
- **node-update (строка 573-575):** заменить на: `node-update.sh → node-lifecycle.sh --mode update → lifecycle/cli.py (state machine, update mode: issue-cert, provision, deploy-modules, healthcheck)`.

**Файлы:** `core/entrypoint-manifest.yaml:18-23, 571-575`.

**Риск:** LOW. Структурная секция — не затрагивает generated-поля. `make generate-entrypoint-manifest` не перезатрёт.

---

### Задача 42 (MED) — Навигация: 5 строк «Канонический» vs инвариант «3 канонических»

**Факты (верифицированы):**
- Инвариант §4: «AGENTS.md — 3 канонических файла (root, core/, core/modules/) + вспомогательные».
- Навигация AGENTS.md:279-288 — 5 строк с статусом «Канонический»:
  1. `AGENTS.md` (root) — ✅ канонический
  2. `core/AGENTS.md` — ✅ канонический
  3. `core/modules/AGENTS.md` — ✅ канонический
  4. `core/internal/template_engine.py` — ❌ **НЕ канонический AGENTS.md**
  5. `core/templates/template-manifest.yaml` — ❌ **НЕ канонический AGENTS.md**

**Решение D42:** изменить статус строк 4-5 с «Канонический» на «Вспомогательный». Инвариант §4 — единственный SoT каноничности.

**Файлы:** AGENTS.md:282-283.

**Риск:** LOW. Чисто классификационная правка.

---

### Задача 43 (MED) — platform-test.yml: header/docs ci-docker + inline openssl дубль

**Факты (верифицированы):**

**D43a — header/docs mismatch:**
- MODULE_CONTRACT строка 10: `Full gate (make gate MODE=full) runs AFTER Docker provisioning` — **врёт**: фактическая команда (строка 234) — `make gate MODE=ci-docker SKIP_PRECOMMIT=1`.
- MODULE_CONTRACT строка 18: также `MODE=full`.
- Строка 315 (summary): `Full gate: smoke + component + predeploy-docker`.
- Строка 229 (step name): `Run full gate (smoke + component + predeploy-docker ...)` — вводящее в заблуждение имя.
- `MODE=ci-docker` — легитимный режим, определён в `makefiles/ci.mk:134,215`.

**D43b — inline openssl дубль:**
- Строки 215-227: inline `openssl req -x509 -newkey rsa:2048 -nodes -keyout ... -out ... -subj ... -addext ...`.
- Существует `make dev-certs` → `core/modules/nginx/dev_cert_generator.py` — канонический генератор dev-сертификатов.
- Дублирование openssl-логики в CI = риск расхождения (изменение параметров в одном месте не отразится в другом).

**Решение D43:**
- **D43a:** заменить все упоминания «Full gate»/«MODE=full» в platform-test.yml на «ci-docker gate»/«MODE=ci-docker»:
  - Строка 10: `Full gate` → `ci-docker gate`
  - Строка 18: `MODE=full` → `MODE=ci-docker`
  - Строка 229 (step name): `Run full gate` → `Run ci-docker gate`
  - Строка 315 (summary): `Full gate` → `ci-docker gate`
- **D43b:** заменить inline openssl (строки 215-227) на `make dev-certs`. Предварительно проверить, что `dev_cert_generator.py` не требует Docker (чистый Python + openssl CLI).

**Файлы:** `.github/workflows/platform-test.yml:10,18,215-227,229,315`.

**Риск:** LOW для D43a (документация). MED для D43b — требует проверки, что `make dev-certs` работает в CI-окружении без Docker.

---

### Задача 44 (LOW) — Makefile STRUCTURE 45→80; README gate MODE

**Факты (верифицированы):**
- **Makefile:2:** `⎋ 45 .PHONY targets across 6 includes` — **устарело**. Фактический подсчёт: **80 .PHONY targets** (grep по всем makefiles/*.mk + Makefile).
- **README.md:30:** `` `make gate [MODE=fast\|full]` `` — не упоминает `MODE=ci-docker`, который используется в CI (`platform-test.yml:234`) и задокументирован в `makefiles/ci.mk:134`.
- **smoke_env_generated.py:** пути корректны (`tests/_conftest/smoke_env_generated.py`, docstring: `core/secret-definitions.yaml`). Generated-файл, не редактируется вручную. Проблема брифа не подтверждена.

**Решение D44:**
- **D44a:** исправить STRUCTURE в Makefile:2: `45 .PHONY targets` → `80 .PHONY targets`.
- **D44b:** обновить README.md:30: `[MODE=fast\|full]` → `[MODE=fast\|full\|ci-docker]`.
- **D44c (smoke_env_generated.py):** закрыть без изменений — пути корректны.

**Файлы:** Makefile:2, README.md:30.

**Риск:** LOW.

---

### Задача 45 (LOW) — secret-definitions.yaml ci_default email: уже решён

**Факты (верифицированы):**
- `secret-definitions.yaml:155`: `ci_default: "admin@ai-platform.local"` — домен `ai-platform.local` совпадает с `PLATFORM_DOMAIN` SoT (`platform-infra.yaml env_defaults`).
- Note (строка 156) объясняет: «generated .py требуют литералы, не `admin@${PLATFORM_DOMAIN}`».
- DevPlan 116 T3 (U-16) уже упразднил legacy-тестовый домен. Текущий домен корректен.
- Gate-тесты (`test_gate_domain_parity.py`, `test_gate_env_example_drift.py`) подтверждают `ai-platform.local` как канонический PLATFORM_DOMAIN.

**Решение D45:** закрыть без изменений кода. Опционально: уточнить формулировку note для ясности — убрать слово «legacy» (домен больше не legacy, а канонический).

**Файлы:** `core/secret-definitions.yaml:156` (note, опционально).

**Риск:** NONE.

---

## 2. Порядок реализации

Фаза 1 — AGENTS.md правки (нет зависимостей, один файл):
1. **D38** (platform-deliver → receive) — 1 строка.
2. **D39** (config_renderer.py путь) — 1 строка.
3. **D40** (healthcheck_poll → healthcheck_poller.py) — 1 строка.
4. **D42** (Навигация: 5→3 «Канонический») — 2 строки.
5. **D37b** (ensure_context_repo → context_overlay.py) — 2 строки.

Фаза 2 — entrypoint-manifest + platform-test.yml + README/Makefile:
6. **D41** (entrypoint-manifest цепочки) — 2 описания.
7. **D43a** (platform-test.yml header/docs) — 4 строки.
8. **D43b** (platform-test.yml inline openssl → make dev-certs) — ~12 строк замены.
9. **D44a** (Makefile STRUCTURE 45→80) — 1 строка.
10. **D44b** (README gate MODE ci-docker) — 1 строка.

Фаза 3 — верификация:
11. **D45** — верифицировать note, уточнить при необходимости (0-1 строка).
12. `make generate-agents-md` — перегенерация core/AGENTS.md (glossary/canon_table подхватят изменения из entrypoint-manifest).
13. `make generate-entrypoint-manifest` — перегенерация allowed_verbs/gates (структурные секции не перезатрёт).
14. `make check-manifests` — зелёный.
15. `make gate MODE=fast` — зелёный.

---

## 3. Критерии приёмки (повтор из контракта)

- AC-E1: `ensure_context_repo` → `context_overlay.py` в AGENTS.md:88 и :104.
- AC-E2: `platform-deliver` — 0 в AGENTS.md вне исторических TRAP.
- AC-E3: `config_renderer.py` путь → `core/internal/llm/`.
- AC-E4: `healthcheck_poll` → `healthcheck_poller.py`.
- AC-E5: entrypoint-manifest цепочки отражают B9 state machine.
- AC-E6: Навигация — ровно 3 «Канонический».
- AC-E7: platform-test.yml header → ci-docker; openssl → `make dev-certs`.
- AC-E8: Makefile STRUCTURE 80; README ci-docker.
- AC-E9: `make gate MODE=fast` + `make check-manifests` зелёные.

Дополнительно:
- `rg "platform-deliver" AGENTS.md` — 0 совпадений (кроме исторических TRAP с «удалён»).
- `rg "deploy-modules.sh.*ensure_context_repo" AGENTS.md` — 0 совпадений.
- `rg "shared/healthcheck_poll" AGENTS.md` — 0 совпадений.
- `rg "bootstrap/deploy/config_renderer" AGENTS.md` — 0 совпадений.

---

## 4. Риски и митигации

| Риск | Митигация |
|------|-----------|
| D43b: `make dev-certs` не работает в CI без Docker | Проверить `dev_cert_generator.py` — чистый Python + subprocess openssl. Если требует Docker → оставить inline openssl, но добавить TRAP[DEBT] о дублировании. |
| D41: генератор `generate_entrypoint_manifest.py` перезаписывает структурные секции | Подтверждено: генератор сохраняет structural sections (G3 cycle break). `delegates_to` — structural, не будет перезаписан. |
| `make generate-agents-md` после правок AGENTS.md: может перегенерировать glossary и потерять правки | Glossary (`GENERATED:START:glossary`) перезаписывается генератором. Правки в glossary НЕ делаем — все изменения вне generated-секций. |
| После правок entrypoint-manifest `make generate-agents-md` сгенерирует устаревший core/AGENTS.md canon_table | canon_table в core/AGENTS.md читает `delegates_to` из entrypoint-manifest. После D41 генератор подхватит обновлённые цепочки — это ожидаемое поведение. |

---

## 5. Оценка

- Изменяемые файлы: 5 (AGENTS.md, entrypoint-manifest.yaml, platform-test.yml, README.md, Makefile).
- Строк кода: ~20 строк правок + ~12 строк замены (D43b).
- Трудозатраты: ~0.1-0.2 дня агент-времени. Размер: **SMALL** (≤8 файлов, чисто документационные правки без архитектурных/API/schema-изменений).
- Generated-секции: glossary и canon_table перегенерируются через `make generate-agents-md` после правок. Ручные правки — только вне `GENERATED:START/END` маркеров.

## 6. Отклонения от исходного брифа

| Задача | Отклонение | Причина |
|--------|-----------|---------|
| 37 | TRAP B8 D2 — без изменений | Уже обновлён брифом A (02-DevPlan). Только ensure_context_repo. |
| 45 | email — без изменений кода | Уже решён DevPlan 116 T3. Домен корректен, note объясняет литерал. |
| 44c | smoke_env_generated.py пути | Пути корректны — проблема брифа не подтверждена. |

---

## Next Steps

### Реализация (единая волна — все задачи независимы)
```
coder Read /Users/tronyx/projects/ai-platform/.ai/plans/117-post-refactor-drift/06-DevPlan.md, implement all tasks D37-D45:
- Phase 1: AGENTS.md правки (D37b, D38, D39, D40, D42) — один файл, 7 строк
- Phase 2: entrypoint-manifest (D41), platform-test.yml (D43a, D43b), Makefile (D44a), README (D44b)
- Phase 3: Verify D45 (note only), run make generate-agents-md, make generate-entrypoint-manifest, make check-manifests, make gate MODE=fast
```
