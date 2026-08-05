# 139-test-system-stewardship — 03-Debt.md

$START_DEBT_REGISTRY

$ARTIFACT_CONTRACT
PURPOSE:               Реестр долга/тикетов DevPlan 139 (test-system-stewardship) — открытые верификационные тикеты и закрытые DEBT-записи плана (волна W5).
DESCRIPTION:           2 записи: (1) тикет верификации brief A на production (deploy.sh — код НЕ трогать, Rev 2026-11-01) — задача W5 T4; (2) закрытая DEBT-запись W4.7 (render-monitoring hook-тест) — подтверждена реализацией (RESOLVED-практика 139 W5).
RATIONALE:             Plan §11 фиксирует два TRAP[DEBT]: W4.7 (render-monitoring hook-тест, Rev 2026-09-01) и W5 (deploy.sh верификация brief A, Rev 2026-11-01). W5 T4 требует завести тикет deploy.sh; W4.7 закрыт фактической реализацией в W4 (test_monitoring_post_deploy.py) — фиксируем RESOLVED, чтобы реестр отражал реальное состояние дерева.
ACCEPTANCE_CRITERIA:   (1) тикет deploy.sh зафиксирован с Rev 2026-11-01 и запретом правки кода; (2) W4.7 помечена RESOLVED с ссылкой на файл реализации; (3) реестр читается как источник истины для финального отчёта волны.
IMPLEMENTS:            02-DevPlan.md §6 W5 T4 (deploy.sh тикет); §11 TRAP[DEBT] (W4.7, W5).
IMPACTS:               core/entrypoints/deploy.sh — ТОЛЬКО тикет, код не изменяется.
REQUIRES:              02-DevPlan.md (139); artifact-registry (NN-Debt.md naming).
$END_ARTIFACT_CONTRACT

---

## Открытые тикеты

### T-1: deploy.sh — верификация brief A на production (W5 T4)

| Поле | Значение |
|------|----------|
| Статус | OPEN |
| Код | НЕ трогать (core/entrypoints/deploy.sh — переходный SSH forced-command entrypoint, 175 LOC, keep-решение DevPlan 119 D8) |
| Задача | StatusReport после БЛИЖАЙШЕГО деплоя на production: подтвердить, что legacy-ноды работают на каноническом канале `orchestrator_cli dispatch` (DevPlan 116 B1) и deploy.sh больше не вызывается из authorized_keys forced-command |
| Критерий закрытия | Все production-ноды используют SSH_ORIGINAL_COMMAND-диспетчер; 0 вызовов deploy.sh в audit-логах; удаление deploy.sh возможно без поломки authorized_keys |
| Rev-дата | 2026-11-01 (дедлайн верификации, вместе с yaml_read.sh Rev-окном, закрытым решением пользователя) |
| Ссылки | 02-DevPlan.md §11 TRAP[DEBT] «139 W5 — deploy.sh верификация brief A»; §7.3 «НЕ трогать: core/entrypoints/deploy.sh (175 LOC — тикет, не код)»; root AGENTS.md ⚠️ TRAP[DECISION] 2026-08-01 (Bootstrap forced-command → orchestrator_cli dispatch) |
| Владелец | Sysadmin (StatusReport) + QA (верификация после деплоя) |

## Закрытые записи (RESOLVED-практика 139 W5)

### R-1: W4.7 — render-monitoring hook-тест

| Поле | Значение |
|------|----------|
| Статус | RESOLVED · 2026-08-05 · 139 W4 (реализация) |
| Источник | 02-DevPlan.md §11 TRAP[DEBT] «139 W4.7 — render-monitoring hook-тест» (Status: OPEN, Rev 2026-09-01) |
| Закрытие | run_monitoring_reconfig извлечён (138 W3, core/internal/monitoring_config_renderer.py:599); покрытие — tests/unit/test_monitoring_post_deploy.py (test_n_all_steps, test_skip_no_monitoring_section, test_render_step_failure_non_fatal, test_post_deploy_chain_calls_reconfig, test_post_deploy_chain_reconfig_failure_non_fatal). Rev-условие «если 138 W3 не влит» НЕ наступило — задача выполнена в W4 (имя файла отличается от §7.2-манифеста: test_monitoring_post_deploy.py вместо test_monitoring_reconfig.py — отклонение документировано в отчёте W5) |
| Формат | TRAP[DEBT] · RESOLVED · 2026-08-05 · 139 W4 (test_monitoring_post_deploy.py) |

$END_DEBT_REGISTRY
