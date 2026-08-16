# GREP_SUMMARY: deploy-hooks, hooks-package, re-export, post-deploy-chain, 170-W4-B3
# STRUCTURE: ▶ deploy/hooks/__init__ → re-export hub → ⊕ run_post_deploy_chain (post_deploy_chain.py) → ⎋ единый публичный API пакета hooks/
# region MODULE_CONTRACT
## @purpose  Публичный API пакета core/internal/deploy/hooks/ (170 W4-B3) — re-export
##           run_post_deploy_chain (best-effort post-deploy chain, 4 подшага).
## @scope    Декомпозиция монолита deploy/orchestrator.py (research-A §3 B3: единственный
##           прямой subprocess-потребитель deploy-кластера → hooks/).
## @invariants
##   1. ВСЕ публичные символы доступны через пакет (run_post_deploy_chain)
##   2. Пакет НЕ импортирует orchestrator.py (отсутствие циклов; import-linter green)
## @changes 2026-08-15 | 170 W4-B3 — extracted from deploy/orchestrator.py
# endregion MODULE_CONTRACT

from core.internal.deploy.hooks.post_deploy_chain import run_post_deploy_chain

__all__ = [
    "run_post_deploy_chain",
]
