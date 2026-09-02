<!-- GREP_SUMMARY: deploy-integrity brief success-predicate verify-desired-state fail-loud allow-autogen final-verify overlay-deploy-key preflight-input honesty-gate -->
# GREP_SUMMARY: deploy-integrity, brief, success-predicate, verify-desired-state, fail-loud, allow-autogen, final-verify, overlay-deploy-key, preflight-input, honesty-gate
# STRUCTURE: ┌2 постмортема┐ → ◇ свернуто в 2 первопричины → ⊕ 4 решения владельца → ◇ scope P0 → ⎋ DevPlan 02

<!-- $START_BRIEF -->

## $ARTIFACT_CONTRACT

- **PURPOSE:** Закрепить результат двух постмортемов (`.ai/plans/028-deploy-postmortem/` и `.ai/plans/deploy-postmortem/`): превратить «чистый сервер → одна команда → рабочая система» из разово доказанного результата в защищённое гейтами свойство. Устранить первопричину (неверный предикат успеха), а не латать отдельные баги.
- **DESCRIPTION:** Два постмортема агрегируют ~73 fix-коммита в ~4 реальные первопричины, которые схлопываются в ДВА слоя: (1) «успех = заявление, а не проверка» (silent-success + checkpoint-skip + readiness-гонки + fail-soft + CI-дрейф); (2) отсутствие принудительного фальсифицирующего контура (красный CI 2.5 недели не блокировал). План реализует минимальный P0-путь: fail-loud на критических путях с флагом-дискриминатором `allow_autogen`, postcondition verify-desired-state в converge, final-verify фаза после φ8.5, авто-провижин overlay deploy-key, preflight input-contract, honesty-гейт для деплой-кода.
- **RATIONALE:** Точечные фиксы не устраняли класс — класс повторялся на новом пути исполнения. Устранять надо предикат (слой 1) и процесс (слой 2) одновременно. План минимален: только то, что разрывает цикл «deploy → ошибка → fix», без большого рефакторинга.
- **ACCEPTANCE_CRITERIA:** (1) На чистой ноде без enc-файла И без `allow_autogen` bootstrap падает fail-loud с явной причиной (не autogen-деградация). (2) Overlay clone-fail → exit 10 (не WARN). (3) Удаление live-серта/vhost/контейнера → повторный converge восстанавливает или fail-loud (не «no action»). (4) Фаза final-verify после φ8.5 проваливает exit 10, если серты/secrets.env/exposed-serving/GHCR≠skip не верифицированы. (5) `make new-context` + bootstrap клонирует приватный overlay без ручных scp/chmod/ssh-config. (6) `make validate-node-input` падает до любого SSH на кривом AGE-ключе/отсутствующем sops/требуемом ключе. (7) Новый silent-success паттерн в `bootstrap/deploy/` или `converge/` → honesty-гейт RED.
- **IMPLEMENTS:** Пункты D-1/D-2/D-5 + final-verify (C1) + preflight input-contract + honesty-гейт из `deploy-postmortem/minimal-fix-plan.md` и P0.1/P0.3/P0.4 из `028-deploy-postmortem/minimal-fix-plan.md`.
- **IMPACTS:** Код `core/internal/bootstrap/` (state_machine, phases, converge, preflight, context_overlay), `core/internal/scaffold/context_initializer.py`, `core/schemas/node.schema.json`, `core/entrypoint-manifest.yaml`, `tests/gates/`, `tests/unit/`. Не трогает deploy-arena репозиторий (вне ai-platform) и не добавляет blocking required-check на push.
- **REQUIRES:** Python 3.14+, запущенный `make check`; для live-верификации — чистая VM (multipass) или пересоздаваемая VPS с каноническими входами (node.yaml, enc.yaml, AGE-ключ, SSH-ключи).

---

## Source

> «Прочитай два комплексных отчета: .ai/plans/028-deploy-postmortem/ и .ai/plans/deploy-postmortem/. Наша задача системно решить проблему и ее первоисточник, подумай как можно расширить имеющиеся инструменты/гейты для решения этих проблем, если этого не достаточно или трудоемко, давай введем дополнительные проверки. Раскрой супер позицию, как исправить и закрепить результат. Обсуди со мной спорные моменты и запроси уточнения если требуется.»

## Clarifications (решения владельца, 4 раунда)

1. **Clean-server гейт** → не per-push blocking required-check. Владелец запускает арену чаще / еженедельно вручную. Автоматизация арены + required-check — тех-долг (Debt-артефакт 03).
2. **Различение autogen vs нарушение контракта** → флаг `allow_autogen` в `node.yaml` (`secrets.allow_autogen: true` у lab/arena; отсутствие = hard error).
3. **Readiness-предикат** → final-verify фаза после φ8.5 (end-state assertions до exit 0), без ре-ордеринга φ8.
4. **Overlay deploy-key (node-side)** → авто-провижин через core-канал (не ручной runbook-шаг), fail-loud при отсутствии.

## Decisions

### D1 — Двухуровневая модель первопричины
Свернуть 7 RC-причин двух отчётов в 2 слоя. **@rationale:** Q: почему два, а не семь? A: каждый из 7 RC — поверхность, на которой неверный предикат успеха проявился N раз; точечные фиксы не устраняли класс (секреты ×3 пути, CI ×2 подкласса, silent ×3 компонента). Лечить надо предикат и его фальсификацию.

### D2 — Fail-loud с флагом-дискриминатором `allow_autogen`
`node.yaml#secrets.allow_autogen: true` = «деградация допустима» (lab/arena без enc-файла); отсутствие = «контракт нарушен» → hard error. **@rationale:** Q: почему флаг в node.yaml, а не env? A: SoT в конфиге, видим гейтами и резолвом, а не внешним env-состоянием (RC6 повторение). Q: почему не «всегда hard-error»? A: план 026 §4 шаг 5 легально полагается на autogen — ломать арену нельзя.

### D3 — Final-verify фаза (φ-final-verify) после φ8.5
Лёгкая фаза в state machine: (a) серты всех exposed-доменов на диске; (b) `secrets.env` полный (re-run `verify_required_sops_secrets`); (c) exposed-проекты serving ИЛИ awaiting-CI с отрендеренным vhost; (d) GHCR-токен ≠ skip. FAIL → exit 10. **@rationale:** Q: почему фаза, а не `wait_ready` в φ8? A: решение владельца — не удлинять φ8 и не рисковать таймаутами; фаза даёт честный exit 0 без ре-ордеринга.

### D4 — Honesty-гейт как статический детектор (расширение семейства honesty_mode)
Новый `test_gate_deploy_honesty.py` сканирует `bootstrap/deploy/` + `converge/` на паттерны silent-success. **@rationale:** Q: почему статика, а не runtime? A: runtime postconditions ловят *конкретные* точки; статический детектор ловит *новые* silent-точки до живого прогона (закрывает RC2 как класс, не как инстанс).

### D5 — Preflight input-contract через расширение, не параллельный механизм
AGE-форма (single-line), env-vs-file приоритет, наличие enc-файла и required-ключей добавляются в существующий `preflight.py`; verb `validate-node-input` = тонкий фасад `preflight --scope input` (0 remote). **@rationale:** DRY-first: preflight уже владеет SSH/disk/S3/GHCR/DNS-пробами; отдельный механизм создал бы dual-mechanism дрейф.

## Scope

**Включено (P0, код):**
1. `allow_autogen` флаг (schema + validation + secrets-фаза fail-loud vs autogen).
2. Overlay clone-fail → exit 10 (context_overlay + orchestrator).
3. ssl_certs fail-closed на `None` extractor (честный статус-маппинг).
4. Converge postconditions (verify-desired-state) на vhosts/ssl_certs/runtime.
5. Final-verify фаза после φ8.5.
6. Overlay deploy-key node-side авто-провижин через core-канал.
7. Preflight input-contract + verb `validate-node-input`.
8. Honesty-гейт для деплой-кода.

**Исключено (задокументировано как Debt-артефакт 03):**
- Автоматизация deploy-arena (weekly CI) + blocking required-check на push.
- Per-service `wait_ready` в φ8 (заменено final-verify для честности exit 0).
- Полная унификация CI-канала (deploy-project.yml → thin wrapper над orchestrator_cli) — P1.
- R9 cooldown → честный «drift-masked» статус, hardcode-probe слой — P2.

## Severity

| # | Работа | Критичность | Обоснование |
|---|---|---|---|
| 1 | Fail-loud (overlay + required-секреты) | **P0** | Нода репортит READY при отсутствующем контексте |
| 2 | Converge verify-desired-state | **P0** | Дрейф (stub→0 vhosts, absent module, cert) невидим |
| 3 | Final-verify фаза | **P0** | Успех определялся rc фаз, а не фактом |
| 4 | Overlay deploy-key авто-провижин | **P0** | Ручной шаг на сервере = нарушение «одна команда» |
| 5 | Preflight input-contract | **P0** | Внешние предпосылки падают на φ4/φ8, а не на входе |
| 6 | Honesty-гейт | **P0** | Класс silent-success возвращается на новом компоненте |
| 7 | Env-hermeticity fixture | P1 | NODE_NAME-утечка дала ложный зелёный DR-restore |

<!-- $END_BRIEF -->
