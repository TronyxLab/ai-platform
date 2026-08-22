# 07-performance — Performance Audit Index

Дата: 2026-08-22 · 1 неделя до production launch
Метод: до 10 параллельных субагентов (доменное партиционирование ~114k LOC prod-кода), evidence-based findings, код не изменялся.

## Файлы

| Файл | Скоуп | Findings |
|------|-------|----------|
| [TOP-10.md](TOP-10.md) | **Сводка: TOP-10 bottlenecks, fix-now / wait списки** | — |
| [findings-001-deploy-pipeline.md](findings-001-deploy-pipeline.md) | bootstrap/deploy + internal/deploy | PERF-001…007 |
| [findings-002-bootstrap-lifecycle.md](findings-002-bootstrap-lifecycle.md) | bootstrap/lifecycle | PERF-010, 011 |
| [findings-003-shared-libs.md](findings-003-shared-libs.md) | internal/shared + template_engine | PERF-030…037 |
| [findings-004-healthcheck-scripts.md](findings-004-healthcheck-scripts.md) | scripts + healthcheck (ежеминутный cron) | PERF-050…057 |
| [findings-005-practices-scaffold.md](findings-005-practices-scaffold.md) | practices + scaffold (K1–K5) | PERF-060…067 |
| [findings-006-qa-tooling.md](findings-006-qa-tooling.md) | static/check_suite/lint/agent_check/validate/verify_sweep | PERF-070…075 |
| [findings-007-llm-loadtest.md](findings-007-llm-loadtest.md) | llm + monitoring + loadtest | PERF-080…085 |
| [findings-008-modules-runtime.md](findings-008-modules-runtime.md) | core/modules (status-page, backup-cron) + bootstrap-root spot-check | PERF-040…047 |

## Статистика

- Всего findings: 45 подтверждённых + 4 HYPOTHESIS (PERF-037, 054, 073 conf=Med, 082)
- По severity: HIGH ×14, MED ×17, LOW ×14
- Correctness-adjacent (не чистый perf): PERF-002 (silent no-rollback), PERF-071/074 (false-green гейты)
- Pre-launch рекомендаций: 10 (см. TOP-10.md)

## Ограничения

- Тесты (~176k LOC) и vendor/ вне скоупа.
- Гипотезы помечены [HYPOTHESIS] и НЕ являются фактами; каждая требует measurement перед фиксом.
- Оценки impact — расчётные по коду; перед pre-launch фиксами прогнать соответствующие Measurement из карточек.
