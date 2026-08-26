$START_DEBT

# Debt — 013 Resilience Drills Rework (обнаружено при верификации в worktree)

## $ARTIFACT_CONTRACT
```yaml
PURPOSE: Зафиксировать два латентных дефекта, обнаруженных при верификации DevPlan 013,
         требующих отдельного решения вне скоупа задачи.
DESCRIPTION: D-013a — абсолютные \ref-пути в AGENTS.md ломают doxygen-check для любого
         не-канонического пути чекаута (worktree/переезд); D-013b — ряд сьютов зависят от
         незакоммиченных операторских артефактов (.env, core/modules/hermes-agent/.env),
         из-за чего make check в чистом git worktree не бывает полностью зелёным.
RATIONALE: Оба дефекта воспроизведены на PRISTINE HEAD ворктри (git stash-эксперимент) —
         они НЕ вызваны изменениями DevPlan 013; в каноническом чекауте ~/projects/ai-platform
         оба проходят (doxygen: 0 warnings; все 6 тестов зелёные).
ACCEPTANCE_CRITERIA: n/a (Debt-реестр)
IMPLEMENTS: —
IMPACTS: core/internal/bootstrap/AGENTS.md:236, tests/gates/AGENTS.md:145 (\ref);
         tests/unit/test_s3_ssl_cache.py, tests/unit/test_secrets_validation.py,
         tests/test_predeploy_gate.py (зависимости от untracked-артефактов)
REQUIRES: решение архитектора (фикс \ref → относительные ссылки; policy «worktree parity»
         для операторских .env)
```

## D-013a · MED · Абсолютные `\ref` в AGENTS.md ломают doxygen-check вне канонического пути

- **Observed:** `make check` в worktree `.worktrees/013-resilience-drills` — doxygen-check FAIL:
  ровно 2 warning'а «unable to resolve reference to '/Users/tronyx/projects/ai-platform/…'».
- **Suspected:** `\ref` с АБСОЛЮТНЫМ путём резолвится только когда совпадает с INPUT-корнем
  Doxyfile; любой другой путь чекаута (worktree, CI-mirror, переезд) даёт 2 warning'а →
  zero-warnings инвариант (DevPlan 097) нарушен средой, не кодом.
- **Impact:** ложный RED doxygen-check на легитимных чекаутах; маскирует реальные warnings.
- **Fix sketch:** заменить абсолютные `\ref` на относительные (`AGENTS.md`, `../../AGENTS.md`)
  в core/internal/bootstrap/AGENTS.md:236 и tests/gates/AGENTS.md:145; проверить doxygen-check
  из двух разных путей.
- **When:** верификация DevPlan 013 (2026-08-26), make check в worktree.

## D-013b · LO · Сьюты зависят от untracked операторских артефактов — worktree не может быть зелёным

- **Observed:** в чистом worktree падают 6 тестов: test_s3_ssl_cache ×2 (нужен S3_BUCKET из
  untracked `.env`), test_secrets_validation ×3 + test_predeploy_gate::test_required_env_vars_present
  (нужен untracked `core/modules/hermes-agent/.env` и связанные env).
- **Suspected:** conftest/env-loading читает операторские dotfiles по repo_root; в fresh clone /
  worktree их нет → FAIL. В каноническом чекауте всё зелёное.
- **Impact:** `make check` в worktree не достигает rc=0 независимо от содержимого ветки;
  агенты в ворктри видят «чужие» красные сьюты (потеря времени на разбор — 30+ мин за сессию 013).
- **Fix sketch (варианты):** (1) policy-документ «минимальный набор symlink'ов для worktree»
  (.venv, node-configs, .env, core/modules/hermes-agent/.env) в tests/e2e/README или
  core/internal/shared/AGENTS.md; (2) R4-стиль skip→FAIL с сообщением «отсутствует
  <артефакт> — операторская среда»; (3) детект worktree и явный маркер-фильтр.
- **When:** верификация DevPlan 013 (2026-08-26).

$END_DEBT
