<!-- GREP_SUMMARY: fast-bootstrap-deploy brief one-command bootstrap deploy P0 P1 P2 preflight fail-loud plan 012 -->
<!-- STRUCTURE: ▶ Source → ⊕ Clarifications → ◇ Debt Intake → ⚡ Decisions (@rationale) → ⟦Scope⟧ → ⎋ Severity -->
$START_BRIEF
# region MODULE_CONTRACT
## @purpose  Brief плана 012: устранение всех находок валидации 011 (F-001…F-037) + автоматизация,
##           чтобы `make bootstrap-node NODE=<n>` проходил от голого железа до рабочей ноды одной
##           командой без ручных вмешательств, а деплой проектов давал честный rc.
## @scope    Bootstrap state machine (φ1-φ8.5), deploy-modules оркестратор, secrets-цепочка,
##           post-deploy hooks, rollback, DR-ранбуки (restore/age-key-backup), ops-honesty (healthcheck/
##           watchdog/reconciler/vhost), hygiene. Вне скоупа: блокировки владельца (см. §Scope).
## @invariants
##   - Никаких новых make-глаголов (D1)
##   - Update-mode сохраняет best-effort контракт; strict — только init (D2)
##   - required/generated секреты остаются fail-loud; auto-inject только tier=optional+ci_default (D3)
##   - Все фиксы сопровождаются тестами ($TEST_SPEC DevPlan), R5-негативы для каждого gate
## @rationale Валидация 011: вердикт READY_WITH_FIXES — «пересоздание ноды из голого железа НЕ
##            гарантированно проходит без ручных обходов». Цель 012: гарантировать.
# endregion MODULE_CONTRACT

# Brief — one-command bootstrap & honest deploy (план 012)

$ARTIFACT_CONTRACT
| Поле | Значение |
|------|----------|
| PURPOSE | Довести «голое железо → рабочая нода» и «git push / deploy-project → честно задеплоенный проект» до надёжных one-command потоков: закрыть P0/P1/P2 из валидации 011 кодом, добавить fail-loud семантику и preflight-самолечение |
| DESCRIPTION | ~20 задач в 4 волнах: (1) P0 reboot + fail-loud bootstrap core; (2) deploy honesty + LLM chain; (3) DR/ops honesty; (4) эргономика one-command + hygiene + отчёт bootstrap'а |
| RATIONALE | Q: почему план нужен? A: F-014/F-015 роняли холодный bootstrap дважды за сессию; каждый обход = минуты-часы операторского времени и риск полу-рабочей ноды, которую «success»-статус маскирует |
| ACCEPTANCE_CRITERIA | AC1-AC6 (§Acceptance ниже); верификация — $TEST_SPEC + контрольный cold-start на пересозданной ноде |
| IMPLEMENTS | Находки 01-Findings.md / 02-VerificationReport.md плана 011 (все, кроме BLOCKED владельца) |
| IMPACTS | core/internal/bootstrap/**, core/internal/deploy/**, core/internal/llm/key_provisioner.py, core/modules/{platform-secrets,nginx}/, core/internal/secrets/decrypt_secrets.py, core/internal/scaffold/, core/internal/healthcheck/, Makefiles модулей, tests/** |
| REQUIRES | main @ зелёный check; канал субагентов для делегирования волн (сейчас BLOCKED — см. §Risks); для контрольного cold-start — пересозданная нода и AGE-ключ оператора |

## Source

> «Проверь артефакты после бутстрапа, что можно и нужно оптимизировать / автоматизировать, чтобы процесс бутсрапа и деплоя проектов происходил максимально быстро в одну команду? Давай подготовим план работ для этого.
> Артефакты: .ai/plans/011-launch-validation-tronyx-vps/{01-Findings.md, 02-VerificationReport.md} — полный лог F-001…F-037 с evidence»

## Clarifications

| Вопрос | Решение владельца |
|--------|-------------------|
| Скоуп P2 | **Полный батч P2** — все находки отчёта в этом же плане |
| DEPLOY_PARALLEL=true default для init | **Нет** — последовательно; параллелизм отдельным планом после стабилизации |
| Стратегия против missing optional-key (класс F-014) | **Auto-inject ci_default** при decrypt (+WARN-список) + статический parity-гейт `${VAR:?}` ↔ SoT |

## Debt Intake

- `.ai/plans/*/*-Debt.md` — реестры отсутствуют (glob пуст).
- TRAP[DEBT]-обход в коде затронутых подсистем: активных блокирующих записей нет; релевантные
  TRAP[DECISION] (DD3 reversed `${VAR:?}` разрешён; REF-0004 rollback-контур; node-side decrypt
  канон SECRETS_FILE) учтены в решениях D2/D3/D5.
- DEFER (фиксируется в DevPlan §Debt Intake): F-036 load-test PromQL-pull vs sshd policy —
  нужно архитектурное решение владельца (node-side saturation-pull ИЛИ документированный sshd
  exception); G2 chaos-night, D5 GitHub billing, G5 test-VPS, core-deploy CI secret — действия
  владельца, вне код-скоупа.

## Decisions

### D1 — Ноль новых make-глаголов
Вся автоматизация внутри существующих verb'ов (`bootstrap-node`, `deploy-project`, `converge`,
`healthcheck`, `secrets-unlock`, `e2e-verify`).
## @rationale Q: почему? A: glossary/entrypoint-manifest/namelint churn ради нуля новых операций;
каноническая цепочка уже содержит все шаги (φ8 = deploy-modules + deploy-context + provision-llm) —
«одну команду» ломают баги, а не отсутствие оркестрации.

### D2 — Strict-init, best-effort-update (F-015 honesty)
`_compute_exit_code`: в init-режиме φ8/φ8.5 любой failed≠∅ → exit 2 (фаза fail-loud, resumable);
update-режим сохраняет WARN→exit 0, но печатает честный IMP:9 summary. Корневой фикс —
unconditional export NGINX_OVERLAY_DIR в docker_orchestrator (резолв из node.yaml#config_overlay,
fallback SoT-дефолт) ДО любого compose-вызова.
## @rationale Q: почему не глобальный strict? A: DEPLOY_BEST_EFFORT — осознанный контракт CI
node-update; ломать его = красный CI на warn-хвостах. Холодный bootstrap обязан быть полным:
полу-стек с exit 0 — инцидент класса F-015.

### D3 — Самолечение матрицы секретов (F-014)
При decrypt (decrypt_secrets.py): отсутствующие в матрице ключи с tier=optional+ci_default
дописываются в secrets.env с маркер-комментарием и WARN-списком в выводе; required/generated
отсутствующие → fail-loud со списком. Плюс новый parity-гейт: каждый `${VAR:?}` литерал во всех
module compose ↔ запись в secret-definitions.yaml / platform-infra.yaml (иначе RED).
## @rationale Q: почему auto-inject, а не только gate? A: прецедент DEEPSEEK/ZAI — лечение вручную
тем же значением ci_default; автоматизация убирает целый класс «забыли добавить в матрицу»,
гейт ловит будущие новые `${VAR:?}` без SoT-записи.

### D4 — Последовательный деплой сохраняется
DEPLOY_PARALLEL остаётся false по умолчанию (решение владельца). Machine-time не оптимизируем
в этой итерации — wall-clock оператора сокращается устранением ручных обходов.
## @rationale Q: почему? A: topo_sort-группы готовы, но гонки φ8 на холодной ноде — отдельная
поверхность риска; включать одновременно с fail-loud = два изменяющихся фактора в одном прогоне.

### D5 — LLM-chain env самодостаточность (F-020/F-022/F-021)
key_provisioner резолвит LITELLM_MASTER_KEY явным fallback-chain: env → /var/lib/platform/run/secrets.env
(secrets_env_parser); NO_PROXY для локальных фасадов (127.0.0.1/litellm) в runtime provision;
PLATFORM_STATE_DIR через shared/deploy_paths канон; base-url env-ручка. Все transport call-sites
provision-потока аудируются на except-покрытие (F-021: failed++, continue).

### D6 — DR-ранбуки чинятся как код (F-031/F-032/SEC-0018/F-033)
Restore target postgres: root-compose + source secrets.env + порядок «postgres-only → restore → apps»
(или --clean dump — решает Coder по факту кода); pre_restore_* спул изолируется от retry-скана
(gzip или отдельный каталог вне scan-path); age-key-backup резолвит env из sops-матрицы ноды
(механизм backup-cron).

### D7 — Reboot-drill остаётся requires_node (manual)
Код-фикс юнита + unit-тест контента; e2e «reboot → стек жив» — requires_node тест, ручной прогон
по release-checklist (канон U-83).
## @rationale Q: почему не CI-авто? A: авто-гейт создаст false-blocking при недоступности ноды;
Rev-условие канона (>40 requires_node тестов или первый инцидент) не наступило.

## Scope

**Included:** P0 F-037 · P1 F-014, F-015, F-019, F-020, F-021, F-022, F-023, F-025, F-031, F-032 ·
SEC-0018 · P2: F-009 (verify committed), F-013, F-016, F-017, F-024, F-026, F-027, F-033, F-034, F-035 ·
post-bootstrap report step · parity-гейт interpolation↔SoT · R5-негативы.

**Excluded (владелец/отдельные решения):** D5 GitHub billing org TronyxLab · G5 test-VPS
(недоступна) · G2 chaos-night окно · F-036 PromQL-pull vs AllowTcpForwarding=no · core-deploy
CI secret (repo settings) · DEPLOY_PARALLEL default · asi-* вложенное чужое дерево (F-008 —
вне юрисдикции).

## Acceptance

| # | Критерий | Верификация |
|---|----------|-------------|
| AC1 | Cold bootstrap свежей ноды одной командой `make bootstrap-node NODE=<n>` — без ручных env-workaround'ов (все фиксы цепочки F-013…F-037 в коде) | Контрольный cold-start на пересозданной ноде (ручной, release-checklist) |
| AC2 | Init φ8/φ8.5 с любым failed-модулем → exit 2 + читаемый отчёт; повторный запуск доводит до конца | Unit strict-init + R5-негатив bare-node non-nginx (F-015) |
| AC3 | Отсутствующий optional+ci_default ключ дописывается с WARN; required/generated отсутствующий → fail-loud; `${VAR:?}` вне SoT = RED гейта | Unit decrypt-inject; R5-негатив parity-гейта (сценарий ZAI) |
| AC4 | `deploy-project`/CI-deploy rc отражает реальность (hook self-env); rollback: локальный previous-tag без registry-pull, честный ROLLED_BACK, lock-файл не остаётся | Regression-тесты F-023/F-025 (env-less ReceiveFlow, local tag up) |
| AC5 | Юнит platform-secrets содержит PYTHONPATH=/opt/platform; reboot-drill проходит на свежесгенерированных юнитах | Unit-тест контента юнита + requires_node drill (ручной) |
| AC6 | Идемпотентность сохранена (re-run no-op ≤220s); init ≤~30 мин c учётом новых проверок (preflight <60s) | Замер на контрольном cold-start; существующие идемпотентность-тесты |

## Severity

- **CRITICAL**: F-037 (reboot = полный отказ), F-015 (полу-стек masked success), F-014 (bootstrap abort), F-019 (deps не ставятся → S3-cache мёртв)
- **HIGH**: F-020/F-021/F-022 (LLM chain), F-023 (ложный FAILED деплоя), F-025 (rollback dishonesty), F-031/F-032 (DR restore сломан), SEC-0018 (plaintext дамп в спуле)
- **MEDIUM**: F-013, F-016, F-017, F-024, F-026, F-027, F-033, F-034, F-035
- **LOW**: F-009 verify, hygiene, report step

## Risks

- Канал субагентов BLOCKED (`Insufficient Balance`) на момент планирования — если не восстановится,
  волны выполняются главной сессией последовательно (прецедент F-007 плана 011).
- Контрольный cold-start требует пересозданной ноды и окна оператора — финальная верификация AC1
  выносится за рамки волн (release-checklist).
- Изменение exit-code семантики init затрагивает bootstrap.sh/node-lifecycle.sh обработку кодов —
  покрыто characterisation-тестами до правки.

$END_BRIEF
