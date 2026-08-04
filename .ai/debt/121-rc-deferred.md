# RC-121 отложенные долги — DEBT (перенесено из .ai/plans/121-rc-verification/01-Debt.md)

> Создан: 2026-08-03 | Перенос при чистке .ai/plans (выполненные планы удалены, история — в git)
> Источник: ночная RC-сессия 121 (01-Debt.md); закрытые записи D-1/D-3..D-11/D-13/D-14 → FIXED (см. реестр 001)

## Долги RC-121 (все закрыты планом 130-debt-ops, 2026-08-04)

| # | Долг | Severity | Почему отложено | Rev |
|---|------|----------|-----------------|-----|
| D-2 | P-13: Build Hermes L1 push 403 (ghcr.io/tronyx161) | HIGH | ~~Операторская проверка GHCR-токена/пакета~~ → **FIXED пользователем 2026-08-03** (верификация: `make hermes-push-l1`). 130 W4: GHCR_PUSH_TOKEN env отсутствует на dev-машине → live-проверка push **требует токена** (не выполнялась); FIXED зафиксирован по решению пользователя. | FIXED 2026-08-03 (верификация при токене) |
| D-12 | Локальный status-metrics/htpasswd cron отсутствует (dev-локали) — файлы генерируются вручную | LOW | ~~Спланировано: план 130 W1~~ → **FIXED 2026-08-04 (130 W1)**: таргет `make dev-metrics` (helpers.mk + entrypoint-manifest + README) вызывает platform_export_metrics.py + secrets_manager htpasswd CLI; верифицирован дважды на dev-локали (exit 0, htpasswd byte-identical no-op). | FIXED 2026-08-04 (130 W1) |
| D-15 | P-23: e2e φ8 deploy_context «No module named 'pydantic'» (non-fatal, error-path) | LOW | ~~На проде не воспроизвёлся; pydantic в requirements.txt~~ → **FIXED 2026-08-04 (план 130 W2)** — верификация: единственный pydantic-импорт во всём core/internal — `llm/policy_schema.py` (llm-домен, НЕ deploy-путь φ8: context_deployer/deploy — 0 обязательных импортов); `pydantic>=2.0.0` в core/requirements.txt (сгенерирован из pyproject.toml), ставится на ноды python_deps Step 2 → runtime pydantic присутствует; error-path на проде не воспроизведён. | FIXED 2026-08-04 (130 W2) |

## Закрытые долги из .ai/plans/118 (учёт при удалении плана)

| # | Долг | Статус |
|---|------|--------|
| D2 (118) | node_yaml.py (1164 LOC) → миксины по поддоменам | FIXED волной 119-H: пакет `core/internal/shared/node_yaml/` (6 миксинов: domains/modules/node/projects/resolve/validation) + `shared/atomic_writer.py`; API `.get()` сохранён |
| D7 (118) | generate_platform_env f-string codegen → jinja | CLOSED (keep by design) волной 125 T12: рендер уже структурный (yaml.dump); TRAP[DECISION] LOW в generate_platform_env.py:265 |

| Status | Rev |
|--------|-----|
| CLOSED (все 3 записи FIXED) | D-2: FIXED 2026-08-03 (пользователь); D-12: FIXED 2026-08-04 (130 W1); D-15: FIXED 2026-08-04 (130 W2) |

## Планирование (2026-08-03, решение пользователя «закрыть все долги»)

Все записи перенесены в план **130-debt-ops** (D-12 W1, D-15 W2). После реализации
реестр `.ai/debt/` удаляется целиком (план **131-debt-cleanup**).
