# 09-failures · Production Failure-Mode Review

$ARTIFACT_CONTRACT
PURPOSE: pre-launch аудит поведения платформы при 17 классах отказов; минимизация production-риска при минимальном code churn
IMPLEMENTS: meta-refactoring audit, направление 09
IMPACTS: LAUNCH-BLOCKERS.md, RECOVERY-RISKS.md (синтез внизу папки)
REQUIRES: репо @ 4425ce0 (main), 2026-08-22

## Метод

- 17 сценариев × 9 вопросов (что происходит / где отказ / авто-recovery / broken state /
  retry / user impact / alert / операторское восстановление / минимальный фикс).
- 10 параллельных субагентов, research-only, код не меняется.
- Каждый finding: severity, category, файл+символ, evidence, сценарий, impact,
  confidence, action. Неподтверждённое — помечено `HYPOTHESIS`.
- Критерий рекомендаций: max риск-редукция / min churn (config > runbook > точечный код).
  Никакого рефакторинга ради архитектуры.

## Матрица сценариев → агенты

| Агент | Файл | Сценарии | ID |
|---|---|---|---|
| db | findings-db.md | database unavailable | FAIL-01xx |
| redis-queue | findings-redis-queue.md | Redis unavailable; queue backlog | FAIL-02xx |
| external-api | findings-external-api.md | external API timeout; malformed response | FAIL-03xx |
| crash-restart | findings-crash-restart.md | process crash; machine restart | FAIL-04xx |
| disk-memory | findings-disk-memory.md | disk full; memory pressure | FAIL-05xx |
| network-creds | findings-network-creds.md | network partition; expired credential | FAIL-06xx |
| dup-state | findings-duplicate-state.md | duplicate request; corrupted state | FAIL-07xx |
| migr-rollback | findings-migration-rollback.md | migration failure; rollback | FAIL-08xx |
| workers-tasks | findings-workers-tasks.md | worker crash; interrupted long task | FAIL-09xx |
| alerts | findings-alerts.md | cross-cutting: alert/recovery покрытие всех 17 | FAIL-10xx |

## Severity

- CRITICAL — data loss / невосстановимое состояние / прямой блокер launch
- HIGH — затяжной outage или тихая порча данных без alert
- MED — деградация с ручным восстановлением по runbook
- LOW — косметика/гигиена

## Верификация

Все 5 CRITICAL перепроверены главной сессией чтением первоисточников:
FAIL-0801 (оба workflow прочитаны целиком — build/push отсутствует), FAIL-0101
(нет секции hooks: в postgres/module.yaml; _module_deploy_hooks читает реестр),
FAIL-0200 (allkeys-lru в compose command), FAIL-0300 (пути restore vs сканера),
FAIL-0600 (операционный blocker — подтверждён Debt-статус age-key-backup).
HIGH-evidence выборочно (пути file:symbol существование).

## Итог покрытия

10 доменных файлов findings, 96 findings (5C/24H/~38M/~29L, вкл. 2 HYPOTHESIS),
все 17 сценариев × 9 вопросов + cross-cutting alert-матрица (детект 5/17).
Финальные документы: LAUNCH-BLOCKERS.md (5 blockers, 3 fix-now батча, drills),
RECOVERY-RISKS.md (матрица 17×6, cheat-sheet, остаточные риски).
