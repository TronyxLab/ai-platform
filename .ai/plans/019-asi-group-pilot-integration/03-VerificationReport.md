$START_VERIFICATION_REPORT

# 03-VerificationReport — Plan 019 asi-group pilot integration

🔒 Verified against SHA `67cd84e97a507ec1caccbcdc9395c0ee361a48d1`
⚠️ Working tree не чист: `tests/gates/test_gate_template_syntax.py` (M), `tests/unit/test_secrets_env_parser_benchmark.py` (M), `.ai/plans/020-acceptance-validation/` (??) — **чужие изменения параллельной сессии plan 020**, в коммит 67cd84e НЕ входят (проверено `git show --stat`), верификацию не затрагивают.

$ARTIFACT_CONTRACT
PURPOSE:      Семантическая верификация реализации Plan 019 (T1-T6, T8 код; T7 BLOCKED):
              drift-детект, инварианты, качество тестов, независимая проверка заявлений StatusReport.
DESCRIPTION:  QA-цикл STANDARD+/LARGE (26 файлов коммита + пилоты вне git): статический аудит,
              cross-file drift, инварианты AGENTS.md, Test Honesty R1-R5, runtime по журналу
              прогонов, config-sync цепочка needs.database/DSN/сетей.
RATIONALE:    Статус «All checks PASS» от исполнителя — необходимое, но недостаточное условие;
              QA проверяет отсутствие дрейфа и дыр в покрытии нового класса гейтов.
ACCEPTANCE_CRITERIA: Каждый AC DevPlan 019 имеет вердикт PASS/BLOCKED с evidence; находки
              классифицированы по severity; вердикт семантический (не механический).
IMPLEMENTS:   01-DevPlan.md (Rev 2), 02-StatusReport.md (исполнение W1-W2 + T7 BLOCKED).
IMPACTS:      Находки → делегирование Coder (unit-покрытие анализатора, E2E-смоук, doc-drift).
REQUIRES:     core/internal/shared/compose_service_contract.py, tests/gates/test_gate_service_network_coverage.py,
              .ai/logs/runs.jsonl (машинный журнал прогонов).
$END_ARTIFACT_CONTRACT

---

## Section 1 — Static Audit (Phase 1)

Compliance matrix (ключевые файлы × проверки = PASS/FAIL):

| Файл | MODULE_CONTRACT | GREP/STRUCTURE | regions | LDD IMP:9 | R1-R5 тесты | Вердикт |
|------|-----------------|----------------|---------|-----------|-------------|---------|
| core/internal/shared/compose_service_contract.py | PASS | PASS | PASS | PASS (IMP:9 verdict/provides) | косвенные (см. F-1) | PASS* |
| core/internal/deploy/parity_db.py | PASS | PASS | PASS | PASS (create/drop/cli) | PASS (5/5, R5×2, pw-гигиена) | PASS |
| core/entrypoints/parity-db.sh | PASS (29 LOC фасад) | PASS | PASS | n/a (фасад) | — | PASS |
| core/internal/deploy/verify_contracts.py (+139) | PASS (@changes) | PASS | PASS | PASS (IMP:9 service-contracts) | 50/50 регресс | PASS |
| checks/compose.py + checks/__init__.py | PASS (18→19) | PASS | PASS | PASS (IMP:9 verdict) | 4/4 | PASS |
| practices_manifest.yaml | PASS (@changes) | — | — | — | гейт 8/8 | PASS |
| project_scaffolder.py + scaffold_helpers.py | PASS | PASS | PASS | PASS | 3/3 + 13/13 регресс | PASS |
| templates/template-ai-project/{compose,AGENTS.md} | PASS | PASS | — | — | gate 1/1 | PASS |
| makefiles/deploy.mk + entrypoint-manifest | PASS | PASS | — | — | тринити | PASS |
| tests/gates/test_gate_service_network_coverage.py | PASS | PASS | PASS | ldd_trajectory | TRAP[TEST] R5×3 | PASS |
| tests/gates/test_gate_template_ai_project_networks.py | PASS | PASS | PASS | ldd_trajectory | TRAP[TEST] + TRAP[DECISION] | PASS |
| tests/unit/{test_check_compose_networks,test_parity_db,test_scaffold_ai_project}.py | PASS | PASS | PASS | IMP:9-ассерты | TRAP[TEST] | PASS |

Прочее: ruff.toml `['print']`-allowlist для parity_db.py — обоснован stdout-контрактом DSN; TRAP[DEBT]
на --dry-run скаффолдера — честная фиксация pre-existing долга; bare `except:`/secrets в новом коде — не обнаружено.

**Findings Section 1: 0 BLOCKER / 0 CRITICAL.**

## Section 2 — Drift Analysis (Phase 2)

| ID | Severity | Находка | Evidence | Действие |
|----|----------|---------|----------|----------|
| DRIFT-1 | LOW | StatusReport: «после коммитов 92009e1 + **e938260**» — e938260 не существует (stale pre-amend hash) | 02-StatusReport.md:95 vs `git log` (фактический 67cd84e) | делегировано: правка строки |
| DRIFT-2 | LOW | StatusReport отклонение №4: «перепинены 4e623c1→**14e560a**» — фактический пин `2419325` (коммит, менявший deploy-project.yml — цель семантически верна, формулировка неверна) | 02-StatusReport.md:116 vs core/internal/scaffold/channel_pin.py | делегировано: правка строки |
| DRIFT-3 | INFO | Image-org пилотов `ghcr.io/tronyxlab/*` ≠ контекст asi-group (F9) — сознательно вне скоупа, умирает с decommission легаси-пилотов (W7 T4, Debt Intake стр. 4) | projects/asi-group/*/docker-compose.yml:38,35 | действия нет |
| DRIFT-4 | INFO | client-bot/AGENTS.md: «DOMAIN=asi-faq.local» — историческая фактура рендера; labels/needs/.env.platform обновлены на faq.asiteam.ru (F10 закрыт) | projects/asi-group/client-bot/AGENTS.md vs compose:76 | действия нет |
| DRIFT-5 | INFO | PLATFORM_NO_PROXY содержит `.local` (shared-дефолт SoT no_proxy_internal) — легаси-артефакт, не ошибка 019 | .env.platform:33 vs platform-infra.yaml:104 | действия нет |

Сверки БЕЗ дрейфа:
- **Generated-каскад parity-db** (инвариант 11): Makefile .PHONY → entrypoint-manifest (entry:99 + allowed_verbs:967) → глоссарий root AGENTS.md → canon_table core/AGENTS.md — согласовано; `make check MARKER=check-manifests` exit 0 после коммита (журнал 23:13).
- **Gate-тринити**: оба новых gate-файла в tests/gates/ + `pytest.mark.gate` + entrypoint-manifest gates:1504/1588 — согласовано.
- **SoT-парити сетей**: provides.postgres=[shared-db-net], provides.litellm=[hermes-agent-net, shared-db-net, observability-net] (platform-infra.yaml:128-172) — compose пилотов/шаблона прикрепляют обе сети провайдеров; пересечения непустые.
- **Чужие изменения 020** не в коммите 67cd84e; workflow-пин-гейт зелёный.

**Summary: 0 CRITICAL / 0 HIGH / 2 LOW (doc) / 3 INFO.**

## Section 3 — Invariant Status (Phase 3)

| Инвариант | Статус | Evidence |
|-----------|--------|----------|
| 1. Makefile — единый фасад | HELD | deploy.mk parity-db → parity-db.sh (29 LOC) → python3 -m core.internal.deploy.parity_db |
| 5. entrypoint-manifest — реестр операций | HELD | entry + allowed_verbs + тринити-гейты зелёные |
| 11. Manifest Generation Contract | HELD | generated-каскад через generate-manifests; check-manifests exit 0 |
| 12. docs-in-code | HELD | docs/ не создан; документация в AGENTS.md/docstrings/GRAY-зонах |
| Языковая политика (Python-first, 0 inline python3) | HELD | parity_db.py 431 LOC Python; фасад 29 LOC; ruff-allowlist задокументирован |
| TRAP[BUSINESS] 019 (проектные роли без CREATEDB) | HELD | parity через привилегированный `docker exec postgres psql`; REVOKE CONNECT FROM PUBLIC; тест pw-гигиены |
| shared/AGENTS.md п.3(б): новый shared-модуль требует unit-тесты в tests/unit/ | **VIOLATED (formal)** | compose_service_contract.py (46-й модуль инвентаря) — прямых unit-тестов нет: покрытие только косвенное (K1-хендлер + K3-гейты); edge-cases `_extract_refs` (`$$`-escape, bare `$VAR`, `:?/?`-формы), `_service_networks` dict-form, `load_env_keys` missing-file, `load_provides` fail-fast — без прямого покрытия → **F-1** |
| Test Honesty R1-R5 (новые тесты) | HELD | R1: все с ассертами; R5: негативы на точном инцидентном инпуте (coverage+unresolved, db-needs, invalid-project-no-ssh, psql-failure); skip-маркеров не добавлено |

**Summary: 6 HELD / 1 VIOLATED (formal, тест-инфраструктура) / 0 AT_RISK.**

## Section 4 — Test Quality (Phase 4)

- Новые тесты: 13 (gates 4+1, units 4+5+3) — все поведенческие (behavioral), не implementation-тесты; substring-ассерты только на message-компоненты (правило-имена) — легитимно.
- R5-негативы: полный контур — точный инцидентный инпут 019 (`${DATABASE_URL}` + proxy-net only) закреплён на ОБОИХ рубежах (K1 и K3).
- LDD: ldd_trajectory + явные IMP:9-ассерты в unit-тестах (Anti-Illusion соблюдён).
- Skip-rate: 18/5693 ≈ 0.3% (baseline без изменений; новых skip'ов нет).
- Fragile tests: 0 новых; xdist-флак benchmark-теста (53ms>50ms) — чужой файл 020, вне скоупа.

| ID | Severity | Находка |
|----|----------|---------|
| F-1 | MEDIUM | Тест-покрытие единственного механизма класса: `compose_service_contract.py` — без прямых unit-тестов (formal-нарушение shared/AGENTS.md п.3(б)). Косвенное покрытие не ловит регрессию edge-cases парсера интерполяции (`$$`-escape, bare `$VAR`, dict-form networks/args) — гейт может молча ослепнуть на части входов |

**Test health score: 88/100** (−7: F-1 formal-дыра в покрытии SoT-механизма; −5: AC6 E2E-смоук не исполнен, см. Section 5).

## Section 5 — Runtime Validation (Phase 5)

Независимый перезапуск pytest средой QA заблокирован permission-политикой проекта (2 блока bash);
runtime-фаза верифицирована по **машинному журналу** `.ai/logs/runs.jsonl` (записи пишет test_journal
из фактических прогонов, не самоотчёт исполнителя):

| Проверка | Факт (журнал) | Вердикт |
|----------|---------------|---------|
| `make check` финальный @67cd84e (23:12) | exit 0, **pass 5675 / fail 0 / skip 18** | PASS |
| `make check` @67cd84e (23:06) | exit 1 — basedpyright hook «files were modified» (автофикс) + внутренние retry | объяснено, не регрессия |
| `make agent-check` @67cd84e | exit 0 | PASS |
| `make check MARKER=check-manifests` @67cd84e | exit 0 | PASS (инвариант 11) |
| Per-task прогоны W1/W2 (22:33-22:36) | 2 фикс-цикла gate-тестов (exit 1 → фикс → exit 0) | нормальный цикл |

| AC | Вердикт | Evidence |
|----|---------|----------|
| AC1 | **PASS (static) / runtime BLOCKED** | compose пилотов: 4 сети + `DATABASE_URL=${PLATFORM_POSTGRES_DSN}`; DSN интерполируется (журнал заявки + текст compose); контейнерные probe'ы на ноде — T7 BLOCKED (голая нода, честно зафиксировано) |
| AC2 | **PASS** | шаблон исправлен; gate test_gate_template_ai_project_networks (тринити) зелёный |
| AC3 | **PASS** | 3 L1-правила в verify_contracts (block всегда, l1_only-совместимо) + K1-зеркало; R5-негативы на инцидентном инпуте — 4/4 + 4/4 |
| AC4 | **PASS (static) / runtime BLOCKED** | needs.database=asi-faq_db/managers-bot_db = фактические имена БД/ролей (already-exits skip сработает); hook-конвергенция — T7 BLOCKED |
| AC5 | **PASS (code-level)** | parity_db.py (DI, идемпотентность, pw-гигиена, exit-контракт) + фасад + Makefile + generated-каскад + unit 5/5; живой parity-прогон — планово к W7 T4 |
| AC6 | **PASS (partial) → F-2** | choices/manifest/default/monitoring + unit 3/3 + templates-check 0; **E2E-смоук TASK-8(c) (make new-project … TEMPLATE=ai-project в tmp) не исполнен и не задокументирован как отклонение** |

**Anti-Illusion verdict: PASS** — IMP:9-телеметрия присутствует в новых модулях и ассертится в тестах (verdict/provides/cli-START/DONE); журнал содержит полные батч-сводки, не «тихий зелёный».

| ID | Severity | Находка |
|----|----------|---------|
| F-2 | MEDIUM | AC6: E2E-смоук скаффолд-канала (DevPlan TASK-8(с)) не исполнен и НЕ внесён в «Отклонения» StatusReport — первый реальный пользователь канала будет W7 T0 (4 проекта); риск: полная цепочка scaffold→practices.lock→AI-PLATFORM.md для ptype=ai-project ни разу не прогонялась end-to-end |

## Section 6 — Config Sync (Phase 6)

- **needs.database-цепочка**: ai-platform.yaml (asi-faq_db / managers-bot_db) ↔ dsn_template `${NAME}_db` (platform-infra.yaml:131) ↔ .env.platform DSN (роль `_user`, БД `_db`, pgbouncer:6432) — согласовано для обоих пилотов; хук сконвергирует СУЩЕСТВУЮЩИЕ БД (already-exists skip) — дублей не будет.
- **DSN-резолв**: DATABASE_URL=${PLATFORM_POSTGRES_DSN} присутствует в .env.platform обоих пилотов (реальные креды локально, файлы вне git — `git check-ignore` подтверждён; канон «пароль вне git» соблюдён).
- **Фантом .local устранён** (F10): PLATFORM_NGINX_URL=https://faq|managers.asiteam.ru; labels обновлены.
- **Сети**: external-декларации proxy-net/shared-db-net/hermes-agent-net в пилотах и шаблоне = SoT networks (platform-infra.yaml:30-58); own-net bridge per-project.
- ** practices_manifest.yaml ↔ exec-реестр**: 19-й чек зарегистрирован, channel [local, ci], class L1; schema-гейт 8/8.

**Findings Section 6: 0.**

---

## Semantic Verdict

# **DEGRADED (MEDIUM, non-blocking)**

Код-задачи T1-T6/T8 семантически корректны: инцидентный класс закрыт на трёх рубежах (шаблон →
K1 push → K3 deploy), двойной консьюмер анализатора без дублирования, parity-путь с сохранением
изоляции ролей, скаффолд-канал легализован. Дрейфа в поставленных артефактах нет. Вердикт DEGRADED
(а не STABLE) из-за дыр в тест-инфраструктуре нового единственного механизма (F-1: formal-нарушение
п.3(б) shared/AGENTS.md, edge-cases парсера без прямого покрытия) и неисполненного шага верификации
AC6 (F-2) + двух LOW doc-дрейфов в StatusReport (DRIFT-1/2). T7 (runtime-рубеж AC1/AC4) — BLOCKED
по независимой причине (голая нода), зафиксирован честно.

**Делегирование Coder (авторизовано владельцем в запросе):**
1. **F-1**: tests/unit/test_shared_compose_service_contract.py — прямое покрытие анализатора.
2. **F-2**: E2E-смоук скаффолда ai-project в tmp + документация результата в StatusReport.
3. **DRIFT-1/2**: правка двух stale-ссылок в 02-StatusReport.md.

$END_VERIFICATION_REPORT
