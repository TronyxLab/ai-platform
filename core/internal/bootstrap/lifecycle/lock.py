#!/usr/bin/env python3
# GREP_SUMMARY: lifecycle-lock, flock, file-lock, state-lock, deploy-lock, reentrant, concurrency, shared-re-export
# STRUCTURE: ▶ lifecycle.lock → ┌re-export FileLock/FileLockError/platform_lock_path из shared/file_lock┐ → ⎋ единая точка импорта для bootstrap/lifecycle
# region MODULE_CONTRACT
## @purpose  Тонкий фасад flock-хелпера для bootstrap/lifecycle-кода (путь из DevPlan 136
##           File Manifest: core/internal/bootstrap/lifecycle/lock.py). Каноническая
##           реализация — core/internal/shared/file_lock.py (см. @rationale в shared-модуле:
##           deploy/ НЕ может импортировать bootstrap/, поэтому общий канон живёт в shared/).
## @scope    Re-export FileLock, FileLockError, platform_lock_path для потребителей
##           bootstrap/lifecycle (state_store.save_state — T9.2). deploy-слой импортирует
##           напрямую из shared/ (T9.1/T9.10) — направление bootstrap → deploy сохранено.
## @invariants
##   - НЕ дублирует реализацию — только from-импорты (единственный SoT — shared/file_lock)
##   - API идентичен shared-модулю: FileLock(path, *, timeout, poll_interval), FileLockError
## @rationale DevPlan 136 W9 T9.1: flock-хелпер нужен и bootstrap (state.json), и deploy
##           (deploy lock). Путь из манифеста — bootstrap/lifecycle/lock.py, но deploy →
##           bootstrap запрещён (core/AGENTS.md) → реализация в shared/, фасад здесь.
## 🧐 TRAP[DECISION] · 2026-08-05 · — · lock-хелпер в shared/file_lock.py, НЕ bootstrap/lifecycle/lock.py
## · Rejected: полная реализация в lifecycle/lock.py (путь DevPlan 136 File Manifest) —
## ·   deploy/orchestrator.py + deploy_history.py импортировали бы bootstrap/ (запрещено:
## ·   core/AGENTS.md «deploy/ НЕ может импортировать bootstrap/», гейт cross_layer_imports).
## · Reason: единственный канон для двух слоёв — shared/ (оба импортируют shared свободно);
## ·   lifecycle/lock.py остаётся тонким re-export'ом (DevPlan-путь соблюдён для bootstrap-кода).
## · Rev: если cross-layer правило deploy→bootstrap будет ослаблено — реализацию можно
## ·   перенести в lifecycle/lock.py без изменения API (re-export сохраняет контракт).
## @changes  2026-08-05 · DevPlan 136 W9 — создан (W9)
# endregion MODULE_CONTRACT

from core.internal.shared.file_lock import FileLock, FileLockError, platform_lock_path

__all__ = ["FileLock", "FileLockError", "platform_lock_path"]
