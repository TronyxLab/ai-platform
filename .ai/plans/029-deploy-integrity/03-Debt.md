<!-- GREP_SUMMARY: deploy-integrity debt arena-weekly required-check clean-server automation -->
# GREP_SUMMARY: deploy-integrity, debt, arena-weekly, required-check, clean-server, automation

# 📝 TRAP[DEBT] · 2026-09-02 · HI · Автоматизация clean-server гейта (arena + required-check) — владелец запускает вручную/еженедельно
# · Observed: план 029 закрывает первопричину (неверный предикат успеха) в коде, но НЕ строит
#   принудительный фальсифицирующий контур: deploy-arena (план 026) спроектирована, но не построена;
#   blocking required-check на push в main отсутствует (F-13, красный CI 2.5 недели не блокировал).
# · Suspected: причина известна — владелец принял решение «запускать арену чаще / еженедельно вручную»
#   вместо per-push blocking (осознанный отказ от false-blocking из-за инфраструктурной недоступности
#   ноды — тот же TRAP, что у requires_node).
# · Impact: до постройки арены cold-only регрессии (класс F-01/F-02, AGE-транспорт, clean≠warmed)
#   детектируются только ручным прогоном владельца с задержкой; свойство «чистый сервер → одна команда»
#   остаётся подтверждаемым вручную, а не гарантированным системой.
# · When: обнаружено в DevPlan 029 T-analysis (постмортемы 028 + deploy-postmortem).
# · Rev: когда (a) self-hosted/multipass runner докажет стабильность ИЛИ (b) первый production-инцидент
#   класса cold-only после ручной валидации → поднять до scheduled-nightly CI (arena up/verify/down
#   single-node headless, срез T1–T9 плана 026) + thin `test_gate_deploy_arena.py` (падает при
#   отсутствии/устаревании результата) + required-check на push в main.

---

# Debt-реестр: автоматизация clean-server контура

Владелец (2026-09-02): «Я буду сам чаще запускать арену или еженедельный тест, когда реализуем — укажи как тех долг».

## Что осталось (не входит в код P0 плана 029)

1. **Построить deploy-arena** (репо `~/projects/deploy-arena/`, срез T1–T9 плана 026): single-node
   headless `arena up/verify/down` на multipass VM. Это НЕ код ai-platform — отдельный репозиторий.
2. **Еженедельный CI-прогон** (scheduled weekly, self-hosted/multipass runner): cold-bootstrap rc=0 →
   проекты healthy → re-run no-op → destroy. Пока владелец делает это вручную.
3. **Required-check на push в main** (`platform-test` + `platform-gate-fast` + arena-cold): включается
   ТОЛЬКО после пункта 2 докажет стабильность (избегание false-blocking). GitHub settings-изменение,
   вне репозитория — кодифицируется SoT `core/ci-required-checks.yaml` + `test_gate_branch_protection.py`.
4. **Per-service readiness `wait_ready`** (langfuse clickhouse-миграции, litellm /health, loki /ready,
   hermes warmup): финал-verify фаза даёт честный exit 0, но не *ожидает* serving — гонки старта
   остаются P2 (padding `start_period` пока закрывает).

## Связь с TRAP[DECISION] (root AGENTS.md)

`requires_node` остаётся ручным по той же причине (инфраструктурная недоступность ≠ регрессия кода).
Этот долг — тот же класс, применённый к clean-bootstrap. Rev-условие корневого TRAP
(>40 requires_node-тестов ИЛИ первый production-инцидент этого класса) распространяется и сюда.
