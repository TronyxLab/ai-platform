# 131-debt-cleanup — 01-Brief.md

$START_BRIEF

$ARTIFACT_CONTRACT
PURPOSE:               Финальная волна закрытия долговой информации: после реализации 127-130 удалить из репозитория все артефакты технического долга — реестр .ai/debt/ (5 файлов), TRAP[DEBT]-комментарии в коде (~30 мест), gate-тест реестра test_gate_debt_registry.py + его manifest-запись, устаревшие сводки AD-решений. Оставить только действующие правила (языковая политика «новый код — Python», keep-решения в AGENTS.md).
DESCRIPTION:           4 волны. W1 — удаление .ai/debt/* (5 файлов) + .ai/plans 126-зависимостей не трогаем (план активен). W2 — удаление TRAP[DEBT]-комментариев во всех файлах кода/тестов/манифестов (полный grep-инвентарь; закрытые — без следа, живые наблюдения — только если долг реально остался — переносятся в {NN}-Debt.md артефакт по протоколу). W3 — удаление test_gate_debt_registry.py + manifest-записи + __pycache__ (gate trinity: файл+маркер+манифест). W4 — обновление документации: root AGENTS.md (таблица Shell-исключений — актуализация, TRAP[DECISION] debt-freshness — ревизия), .kilo/rules/artifacts.md (тип Debt остаётся как процессный механизм — проверить актуальность ссылок), .kilo/agents/*.md (TRAP[DEBT] формат — оставить, реестр-путь обновить), ссылки на .ai/debt в core/internal/shared/AGENTS.md и других файлах.
RATIONALE:             Решение пользователя 2026-08-03: «информацию о долгах удалить из репозитория», «исторические сводки не нужны», остаётся правило «новый код — Python». Реестр выполнил свою функцию (долги спланированы в 127-130 и закрыты); дальнейшее хранение = мёртвый груз и источник дрейфа (гейт свежести будет RED после удаления). Механизм TRAP[DEBT]/Debt-артефактов сохраняется для будущих наблюдений (процесс), исторические данные удаляются.
ACCEPTANCE_CRITERIA:   (1) `rg "TRAP\[DEBT\]"` в коде/тестах/манифестах/доках = 0 (кроме .kilo/agents + skills — механизм). (2) .ai/debt/ не существует (rm -rf; файлы в git history — история сохраняется). (3) test_gate_debt_registry.py удалён + manifest-запись удалена + __pycache__ очищен; make check и gate зелёные (trinity не сломан). (4) root AGENTS.md актуален: таблица Shell-исключений отражает реальность после 127, TRAP[DECISION] debt-freshness ревизован (реестр удалён). (5) Ссылки на .ai/debt/* в коде/доках обновлены или удалены (grep по "\.ai/debt"). (6) make check + make gate MODE=fast зелёные.
IMPLEMENTS:            Решение пользователя 2026-08-03 (удаление долговой информации после закрытия); результат 127-130.
IMPACTS:               .ai/debt/* (5 файлов — удалить), ~30 файлов с TRAP[DEBT] (код+тесты), tests/gates/test_gate_debt_registry.py (удалить), core/entrypoint-manifest.yaml (gate-запись), root AGENTS.md, .kilo/agents/architect.md, code.md, qa.md, sysadmin.md (упоминания .ai/debt-реестра — ревизия), .kilo/rules/artifacts.md (Debt-тип — проверить), core/internal/shared/AGENTS.md (ссылка .ai/debt/096-Residual-Debt.md), core/internal/provision-environment.sh (упоминание C-5 реестра).
REQUIRES:              Завершённая реализация 127-130 (все долги закрыты); make check зелёный до старта.
$END_ARTIFACT_CONTRACT

## Scope

| # | Действие | Объект |
|---|----------|--------|
| 1 | rm -rf | `.ai/debt/` (001-Strangler-Fig-Closeout.md, 121-rc-deferred.md, letsencrypt-path-hardcode.md, test-env-leak-and-flakes.md, watchdog-undelivered.md) |
| 2 | Удалить TRAP[DEBT] | ~30 файлов кода/тестов (полный grep-инвентарь на момент реализации) |
| 3 | Удалить gate-тест | tests/gates/test_gate_debt_registry.py + manifest-запись (id: debt-registry/…, см. entrypoint-manifest.yaml gates) + __pycache__ |
| 4 | Ревизия доков | root AGENTS.md (Shell-исключения, TRAP[DECISION] B11 debt-freshness), .kilo/agents/*.md, .kilo/rules/artifacts.md, core/internal/shared/AGENTS.md, core/internal/provision-environment.sh |
| 5 | Проверка ссылок | `rg "\.ai/debt"` → 0 после правок (или только .kilo-механизм) |

## Non-Goals

- НЕ удаляем механизм TRAP[DEBT]/Debt-артефактов (процессные правила в .kilo — остаются; будущие наблюдения пишутся в {NN}-Debt.md по artifact-registry).
- НЕ трогаем .ai/plans/126-chaos-resilience и .ai/plans/132-fault-tolerance (активные планы; долги D-1 journald→Loki и D-2 Telegram-маркеры из 126 закрываются в 132-fault-tolerance W3/W4).
- НЕ трогаем .ai/backlog/ (фичи, не долги).
- НЕ переписываем TRAP[DECISION] историю — только записи, ссылающиеся на удалённый реестр.

$END_BRIEF
