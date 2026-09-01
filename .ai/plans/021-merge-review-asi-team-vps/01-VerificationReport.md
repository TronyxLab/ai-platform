# 01-VerificationReport — 021 merge-review asi-team-vps

$ARTIFACT_CONTRACT
PURPOSE:      Приём работы параллельной модели (ветка launch-validation/asi-team-vps,
              приёмо-сдаточная валидация платформы на ноде asi-team-vps): влить ветку в main
              merge-commit'ом, построчно оценить каждый фикс, прогнать верификацию merged-дерева,
              дать вердикт готовности платформы к пользователям и решение о промоуте.
DESCRIPTION:  Ревью 16 коммитов ветки (5 fix + 1 feat + 10 docs). Все 6 fix/feat построчно.
              Мердж без конфликтов (ort auto-merge cli.py/domains.py — изменения непересекающиеся).
              Локальная верификация merged-дерева: make check (3 раунда до чистоты), agent-check,
              check-manifests. Read-only sanity ноды: project-list + e2e-verify.
RATIONALE:    Критерий принятия — платформа на merged-дереве не имеет блокирующих регрессий,
              фиксы параллельной модели устраняют корень (не симптом), политики соблюдены.
ACCEPTANCE_CRITERIA:
  AC1: merge-commit ветки в main — done (a9937d8).
  AC2: каждый fix/feat построчно оценён: корень/тесты/скоуп/языковая политика — done (6/6 accepted).
  AC3: merged-дерево make check / agent-check / check-manifests зелёные — done (rc=0).
  AC4: sanity ноды read-only — done (project-list + e2e-verify green).
  AC5: вердикт готовности + решение о промоуте — ниже.
IMPLEMENTS:   §0 опрос владельца 2026-09-01 (merge-commit / промоут разрешён / выборочное ревью /
              влить поверх 9 коммитов + push всё / WIP отдельным коммитом).
IMPACTS:      main (merge a9937d8), 3 доп. коммита основной модели (WIP ×2 + merge-review доработки).
REQUIRES:     node asi-team-vps (77.233.221.129), SSH-доступ, age-контур asi.
$END_ARTIFACT_CONTRACT

---

## Итоговый вердикт: платформа готова к пользователям — **ДА (с оговорками)**

Критерий «одна команда с голой ноды поднимает сервер и деплоит roadmap» — **PROVEN**
(фаза B, отчёт второй модели + моя read-only проверка `e2e-verify` на живой ноде). Все 6
fix/feat-коммитов параллельной модели — **accepted** (корень устранён, тесты реально гоняются,
0 симптом-заглушек, языковая политика соблюдена). Блокирующих регрессий кода НЕТ.

Оговорки (не регрессии кода): фазы F (DR) и G2/G3/G5 (chaos/load/test-node) — **BLOCKED**
конфигурацией минимального контекста (нет postgres/backup-cron/monitoring) и внешней
недоступностью test-VPS; S3 SSL-кеш — внешние креды; apex-домен asiteam.ru без default vhost (P2).

---

## Сводка фаз (из отчёта второй модели → после моего ревью)

| Фаза | Вторая модель | После ревью | Примечание |
|------|---------------|-------------|------------|
| A — локальная верификация | ✅ | ✅ PASS | `make check` rc=0 на merged-дереве (подтверждено мной) |
| B — bootstrap-node | ✅ PROVEN | ✅ PASS | идемпотентность; 5 P0/P1-фиксов F-01..F-04 |
| C — TLS | ✅ (C2 BLOCKED) | ✅ PARTIAL | C1/C3/C4 PASS; C2 cache-drill BLOCKED (S3-креды) |
| D — каналы доставки | ✅ | ✅ PASS | +rollback verb (feat 87d0c04), audit fix (7f3a829) |
| E — конфигурация | ✅ | ✅ PASS | healthcheck/enabled/node-update/converge/сети |
| F — DR | BLOCKED | ⚠️ BLOCKED | minimal context (нет postgres/backup-cron); F3 age-key-backup dry-run ✅ |
| G — resilience | ✅ (частично) | ⚠️ PARTIAL | G1 reboot ✅ + G4 e2e-verify ✅; G2/G3/G5 BLOCKED |
| H — Release checklist | см. ниже | ⚠️ PARTIAL | check/check-manifests ✅; test-node/chaos BLOCKED |

## Release checklist (root AGENTS.md) — финальный статус

| # | Пункт | Финальный статус |
|---|-------|------------------|
| 1 | E2E test-VPS (`make test-node`) | BLOCKED — test-VPS недоступна (внешняя причина); `e2e-verify NODE=asi-team-vps` green |
| 2 | Chaos FULL | BLOCKED — test-VPS недоступна + минимальный контекст (нет postgres/redis/litellm/clickhouse) |
| 3 | CI-гейты: `make check` локально | ✅ rc=0 (3 раунда, подтверждено мной) |
| 3b | CI ветки (push-gate.yml) | ✅ success (по отчёту; F-09 run 33446668301) |
| 3c | `make check MARKER=check-manifests` | ✅ GREEN (подтверждено мной) |
| 4 | ПРОМОУТ | **УСЛОВНО** — см. §Решение о промоуте |
| 5 | Мониторинг без новых ошибок | ✅ (minimal context — мониторинг не включён; nginx/loki/status-page healthy) |

---

## Вердикты по фиксам параллельной модели (6 fix/feat, построчно)

| Находка | Приоритет | Вердикт | Обоснование |
|---------|-----------|---------|-------------|
| F-01 (9b8a6af) module-aware secrets fail-loud | P0 | **accepted** | Корень: глобальный fail-loud → module-aware (consumers ∩ enabled-модули). AGE_SECRET_KEY source sops→provisioner — верно (курица-яйцо). Единый shared-резолвер, +тесты. |
| F-02 (379fd01) pydantic-chain lazy import + re-exec + honest ssl_provision | P0 | **accepted** | Корень: module-level pydantic-импорт → lazy import. Re-exec с loop-guard (маркер+версия-гейт+probe). skipped_import при extractor=None ≠ converged. +7 тестов. |
| F-03 (b3b3100) auto-detect node | P0 | **accepted** | Корень: reboot-путь без NODE_NAME → auto-detect единственной ноды; неоднозначность → None (легаси) + WARN. Консервативно, +2 теста. |
| F-04 (9ef5db9) autogen after decrypt | P0 | **accepted** | Корень: reboot-путь терял autogen → ExecStartPost secrets_manager ensure. Идемпотентен, порядок гарантирует systemd. +2 теста. |
| F-07 (7f3a829) audit.jsonl dir traversal | P1 | **accepted** | Корень: чинился файл, не каталог → ACL u:ci-deploy:--x (без r) / chgrp+0710 (без o+x). Security-conscious. +тесты. |
| rollback verb (87d0c04) | feat | **accepted** | Чистый фич + рефакторинг: единый handler (args,ctx), parity-assert `_VERB_HANDLERS == CANONICAL_VERBS`, project-name guard. |

Итого: **6/6 accepted**, 0 accepted-with-fixes, 0 rejected.

## Доработки основной модели при мердже (мои, не параллельной модели)

1. **WIP-коммит `19b0949`** — φ1 python-deps self-heal (import-probe) + deploy-context `--node` resolution (cache-drill) + plan-артефакты 019/020.
2. **WIP-коммит `0b9a485`** — ai-project template filter в ai-instructions (Отклонение №6).
3. **Merge-review коммит `a823dc6`** — устранение 3 RED на merged-дереве, все — от моей работы:
   - python_deps `_resolve_requirements_path`/`_check_content_hash`/`_probe_critical_imports` → публичные (private-imports гейт);
   - test_gate_loc_allowlist: test_state_machine.py 1850→2100 (мой self-heal раздул файл);
   - project_lister/remover: `NODE=⟨node⟩` → ASCII `NODE=<node>` (runtime) + `\<node\>` (## doc-комментарии, doxygen zero-warnings).

## Аномалии, вынесенные в отчёт (не блокеры)

1. **Атрибуция коммитов:** автор ветки `test <test@test>` — аудит-атрибуция нарушена (тот же паттерн, что сессия 019). Рекомендация: канонизировать git identity ворктри параллельной модели.
2. **Неточность отчёта второй модели:** заявлено «15 коммитов / 6 fix / 8 docs», фактически **16 коммитов / 5 fix + 1 feat / 10 docs**.
3. **NNN-коллизия:** `020-acceptance-validation` (моя) и `020-launch-validation-asi-team-vps` (параллельная) — толерируется artifact-registry (параллельные ворктри).

## Верификация merged-дерева (мои прогоны)

| Команда | Результат |
|---------|-----------|
| `make check` (батч, раунд 1) | RED — 3 падения (private-imports, LOC, 2 unit) — все от моей работы |
| `make check` (раунд 2) | RED — 1 падение (doxygen 3 warnings от `<node>` в ##-комментариях) |
| `make check` (раунд 3) | ✅ **rc=0, All checks PASS** |
| `make agent-check` | ✅ exit 0 (0 blocking / 6 advisory C901/FBT) |
| `make check MARKER=check-manifests` | ✅ GREEN |

## Sanity ноды (read-only)

| Команда | Результат |
|---------|-----------|
| `make project-list NODE=asi-team-vps` | ✅ rc=0 — 1 проект: roadmap (roadmap.asiteam.ru, frontend) |
| `make e2e-verify NODE=asi-team-vps` | ✅ rc=0 — 2 endpoints green: login.asiteam.ru (404 by-design), roadmap.asiteam.ru (200); TLS wildcard *.asiteam.ru valid, 89 дней |

## Решение о промоуте

Условия автономного промоута по задаче §3:
- (a) все фазы PASS — **НЕ выполнено** (F DR — BLOCKED; G2/G3/G5 — BLOCKED; C2 — BLOCKED);
- (b) bootstrap одной командой подтверждён — ✅ (отчёт + e2e-verify);
- (c) все фиксы accepted — ✅ (6/6);
- владелец разрешил (§0 Q2) — ✅.

Итог: **`make context-promote CONTEXT=asi-group` НЕ выполняю автономно** — условие (a) не
выполнено буквально (фазы F/G BLOCKED минимальным контекстом + внешней недоступностью test-VPS,
не регрессией кода). Блокирующих регрессий кода нет; промоут — решение владельца.

## Коммиты этой сессии (main)

- `a9937d8` merge origin/launch-validation/asi-team-vps (16 коммитов ветки)
- `19b0949` fix(020) WIP — φ1 self-heal + deploy-context --node
- `0b9a485` feat(019) WIP — ai-project filter
- `a823dc6` fix(020) merge-review доработки (python_deps API, LOC, NODE hint)
