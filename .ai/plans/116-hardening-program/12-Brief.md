# 12-Brief — B11: Enforcement-гейты и процесс

<!-- GREP_SUMMARY: enforcement gates cross-layer audit-format glossary workflow-triggers debt-registry process hygiene -->
<!-- STRUCTURE: ┌scope┐ → ◇ гейты-инфраструктура → ◇ CI/процесс → ◇ документация → ⊕ критерии → ⎋ зависимости -->
# region MODULE_CONTRACT
## @purpose  Волна B11: финальные enforcement-гейты, CI-гигиена и процессные механизмы против дрейфа.
## @scope    U-09, U-10, U-45, U-51, U-57, U-58, U-79, U-80, U-82, U-83..U-88
## @invariants
##   - Enforcement: гейты с allowlist (решение 01-Brief); allowlist сжимается до нуля и не растёт.
##   - Комментарии-инварианты и ручные списки заменяются генерацией (глоссарий, манифесты).
##   - Реестр долга — живой SoT: stale-пункты невозможны (гейт свежести).
# endregion MODULE_CONTRACT

$ARTIFACT_CONTRACT:
  PURPOSE: Закрыть enforcement-инфраструктуру и процессные дыры — чтобы дрейф не возвращался после программы.
  DESCRIPTION: Фикс cross-layer gate (dotted-импорты + python3 -m), audit-format гейт, генерация глоссария из allowed_verbs, развязка downstream workflow, чистка артефактов и ghost-ссылок, стабилизация test_inventory, реестр долга с гейтом свежести, фиксация нового TRAP[DECISION] по enforcement.
  RATIONALE: Оба аудита: инварианты декларируются, но не enforce-ятся; гейты либо слепы (cross-layer), либо отсутствуют (audit format, паритет копий). Программа закрывает функциональные проблемы; эта волна делает невозможным их возврат.
  ACCEPTANCE_CRITERIA: (1) cross-layer gate ловит dotted-импорты и python3 -m (5 нарушений закрыты allowlist'ом, сжимается до 0); (2) audit.log: единый формат (audit.jsonl), format-гейт R2 (валидация JSONL); (3) root AGENTS.md глоссарий генерируется из allowed_verbs (G4-подобный генератор) — 0 ручных правок; (4) downstream workflow (core-deploy/build-platform/mirror) не зависят от full-gate; (5) артефакты (.bak, reports/, deploy-result.json) удалены/исключены; ghost-ссылки (overlay_deliverer:21, shared/AGENTS.md:31, node_lifecycle_static:521) исправлены; (6) test_inventory: единая регенерация, rename-детекция; (7) реестр долга: гейт свежести (stale-пункты RED), ghost-строки устранены; (8) новый TRAP[DECISION] (2026-07-31) фиксирует пересмотр TRAP 2026-07-21: CI-гейты с allowlist — канон; (9) P3-наблюдения (U-83..U-88) переведены в реестр долга с решениями (issue-cert justified, node-resolver — декомпозиция в backlog, big-bang commits — процессный лимит).
  IMPLEMENTS: U-09 (cross-layer gate), U-10 (audit format), U-45 (глоссарий), U-51 (артефакты), U-57 (триггеры), U-58 (ghost-ссылки), U-79 (test_inventory), U-80 (fix-churn), U-82 (реестр долга), U-83..U-88 (наблюдения)
  IMPACTS: tests/test_cross_layer_imports.py, core/internal/shared/audit_logger.py, AGENTS.md, makefiles/, .github/workflows/{core-deploy,build-platform,mirror,platform-test}.yml, tests/test_inventory.yaml, .ai/debt/, core/lib/audit.sh
  REQUIRES: Все предыдущие волны (гейты фиксируют их результаты)

---

## Scope

| U | Проблема | Ключевые файлы |
|---|----------|----------------|
| U-09 | Cross-layer gate слеп: dotted-импорты, python3 -m; 5 нарушений | tests/test_cross_layer_imports.py:263-296,373-427,452-564; agent_watchdog.py:42-44, backup_config.py:36, disk-monitor.sh:44, hooks ×2 |
| U-10 | Audit: 5 writers, 3 формата (audit.jsonl / audit.log JSON / pipe / free-text) | shared/audit_logger.py, deploy/audit_logger.py, state_machine.py:2324-2332, steps.py:481-492, backup-restore-test.sh:104 |
| U-45 | Глоссарий: 25 из 64 verbs отсутствуют | AGENTS.md, entrypoint-manifest.yaml:647-710 |
| U-51 | Артефакты: .pre-commit-config.yaml.bak, reports/ ×8, deploy-result.json failed | репозиторий |
| U-57 | 3 downstream на workflow_run platform-test full-gate | core-deploy.yml:31, build-platform.yml:26, mirror.yml:104 |
| U-58 | Ghost-ссылки: TRAP overlay_deliverer:21 (несуществующие строки), shared/AGENTS.md:31 (ложные потребители), test_node_lifecycle_static:521 (ложный контракт) | overlay_deliverer.py, shared/AGENTS.md, tests/test_node_lifecycle_static.py |
| U-79 | test_inventory двойная регенерация | tests/test_inventory.yaml + changes |
| U-80 | Fix-churn: entrypoint-manifest 65% fix, smoke.py 77%, state_machine 76% | git-история |
| U-82 | Реестр долга stale: T1 FIXED, P2-1 исчез | .ai/debt/001-Strangler-Fig-Closeout.md |
| U-83..88 | Big-bang commits; DevPlans без VR; issue-cert justified; node-resolver 271 LOC; CI-комментарии; cert ×3 | процесс |

## Ключевые артефакты

1. Cross-layer gate: _looks_like_path расширяется на dotted-names (regex `^[a-z_][\w]*(\.[a-z_][\w]*)+$` + python3 -m паттерн в scan_sh_file); 5 нарушений → allowlist → 0; LINT-EXEMPT документируется.
2. Audit: единый writer (shared/audit_logger, JSONL, расширенная схема ts/tag/status/operation/result); 5 writers маршрутизируются; state_machine/steps pipe-формат мигрирует; backup-restore-test → python-вызов; gate R2 валидирует JSONL (jq-парсимый).
3. Глоссарий: генератор глоссария из entrypoint-manifest allowed_verbs + .PHONY (как canon_table в core/AGENTS.md) — root AGENTS.md секция генерируется; check-manifests включает сверку.
4. Workflow: downstream триггеры — на `platform-test` fast-gate или отдельный workflow; flaky-изоляция.
5. Гигиена: удаление .bak/reports/deploy-result.json; gh-комментарии; ghost-ссылки исправлены.
6. Реестр долга: гейт свежести (каждая запись: статус + дата ревизии; stale > 90 дней → RED); интеграция с test_gate_debt_registry.py.
7. TRAP[DECISION] 2026-07-31: enforcement-гейты с allowlist — канон; пересмотр TRAP 2026-07-21 (rev-дата 2026-07-31).
8. P3-наблюдения: решения по каждому (issue-cert — justified, backlog; node-resolver — декомпозиция в backlog с rev-датой; big-bang — лимит коммитов на DevPlan; DevPlans 085/110/111 — закрыть VR'ами или пометить superseded).

## Гейт самоверификации волны

- Все гейты программы зелёные; allowlist'ы пустые.
- `make gate MODE=full` + check-manifests + новые гейты (cross-layer, audit-format, glossary, debt-freshness) зелёные.
- Полный `make fix-gate` чинит все drift-состояния.

## Зависимости

- От: B10 (тесты), B1-B9 (все гейты фиксируют их итоги).
- К: завершение программы 116.
