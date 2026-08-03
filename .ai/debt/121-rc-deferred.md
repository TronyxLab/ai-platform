# RC-121 отложенные долги — DEBT (перенесено из .ai/plans/121-rc-verification/01-Debt.md)

> Создан: 2026-08-03 | Перенос при чистке .ai/plans (выполненные планы удалены, история — в git)
> Источник: ночная RC-сессия 121 (01-Debt.md); закрытые записи D-1/D-3..D-11/D-13/D-14 → FIXED (см. реестр 001)

## Открытые долги (актуальны)

| # | Долг | Severity | Почему отложено | Rev |
|---|------|----------|-----------------|-----|
| D-2 | P-13: Build Hermes L1 push 403 (ghcr.io/tronyx161) | HIGH | Операторская проверка GHCR-токена/пакета | 2026-08-10 |
| D-12 | Локальный status-metrics/htpasswd cron отсутствует (dev-локали) — файлы генерируются вручную | LOW | Документировано в Fix Recipe | 2026-08-31 |
| D-15 | P-23: e2e φ8 deploy_context «No module named 'pydantic'» (non-fatal, error-path) | LOW | На проде не воспроизвёлся; ошибка обработки деплоя stub | 2026-08-31 |

## Закрытые долги из .ai/plans/118 (учёт при удалении плана)

| # | Долг | Статус |
|---|------|--------|
| D2 (118) | node_yaml.py (1164 LOC) → миксины по поддоменам | FIXED волной 119-H: пакет `core/internal/shared/node_yaml/` (6 миксинов: domains/modules/node/projects/resolve/validation) + `shared/atomic_writer.py`; API `.get()` сохранён |
| D7 (118) | generate_platform_env f-string codegen → jinja | CLOSED (keep by design) волной 125 T12: рендер уже структурный (yaml.dump); TRAP[DECISION] LOW в generate_platform_env.py:265 |

| Status | Rev |
|--------|-----|
| OPEN (3 записи) | D-2: 2026-08-10; D-12/D-15: 2026-08-31 |
