# Направление 9 — Abstractions & overengineering

Метод: инвентаризация dispatch-реестров/ABC/wrapper-цепочек/config-indirection; sampling zero-caller exports. Агент: explore, 13 tool calls (step-limit; часть задач не завершена — см. остаток). Дата 2026-08-22.

Калибровка: задокументированные TRAP[DECISION]-решения (3 template-механизма, dual delivery, L2 merge, Strangler-Fig) НЕ ре-литигируются.

## ARCH-0022 — SCPChannel + `--scp` + `deploy-many`: неисполняемый канал доставки
- Severity: MEDIUM · Confidence: MED (Makefiles+sh проверены; .github/workflows ещё НЕ grepped — возможен скрытый caller) · Churn: M (<300: scp.py 209 LOC + flags) · WHEN: post-launch (после проверки workflows)
- Files: internal/deploy/orchestrator_cli.py:161,177,235-262; channels/scp.py (209 LOC)
- Symbols: --scp flag, SCPChannel, deploy-many subcommand
- Evidence: grep `--scp|deploy-many` по всем Makefile (root+makefiles/*) и core/**/*.sh → 0 callers; канонические пути используют ForcedCommandChannel и LocalChannel
- Scenario: мёртвый операторский путь — transport-код с rsync/agent-forwarding дрейфует без тестов; debug-цена при первом же использовании
- Impact: speculative channel с реальным кодом транспорта
- Minimal fix: удалить --scp/deploy-many/SCPChannel ИЛИ проверить workflows и wired-ить один make-таргет при реальной потребности

## ARCH-0023 — channels/base.py metadata_defaults: объявленный, но никем не читаемый атрибут ABC
- Severity: LOW · Confidence: HIGH (self-documented) · Churn: S · WHEN: post-launch
- Files: internal/deploy/channels/base.py:140
- Symbols: self.metadata_defaults
- Evidence: docstring: «Фактически не читается deliver()… декларация типизирует динамический атрибут»; пишется orchestrator_cli/reconciler_projects, не читается ни одним каналом
- Impact: два источника правды о delivery-metadata (атрибут vs payload.metadata)
- Minimal fix: удалить атрибут; метаданные каналов — только payload.metadata

## Checked clean (не single-impl machinery)
- `_VERB_HANDLERS` (orchestrator_cli.py:476): 6 handlers == CANONICAL_VERBS, assert-guarded полный реестр
- `PHASE_DISPATCH` (state_machine.py:280): 14 фаз, статический полный реестр
- channels ABC: 3 реализации, 2 реально исполняются (receive_flow.py:470, deliver)
- Retry каналов делегирован shared/retry.py — pass-through цепочек нет

## Остаток направления (не покрыто агентом — кандидат на волну 2+)
- Config-indirection трассировка (yaml→generated→env→default→consumer, подсчёт hops)
- Sampling unused public exports в shared/ + 2 доменах
- Wrapper-цепочки *args/**kwargs ≥2 hops
