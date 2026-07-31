# $ARTIFACT_CONTRACT
## @PURPOSE Миграция context-promote.sh (161 LOC) → Python-модуль + тонкий shell-фасад (~40 LOC)
## @DESCRIPTION
`core/entrypoints/context-promote.sh` — единственный «толстый» entrypoint (161 LOC)
без соответствующего Python-модуля. Содержит бизнес-логику:

- SSH availability check: `ssh -T git@github.com` + grep authenticated/Hi (~10 LOC)
- GIT_ASKPASS heredoc генерация с trap EXIT cleanup (~15 LOC)
- `_do_promote()`: SSH primary → `git push --mirror` / HTTPS fallback → `git push --mirror` (~55 LOC)
- MIRROR_VERIFICATION: `ls-remote` HEAD vs `rev-parse` HEAD (~15 LOC)
- `audit_step` wrapping (W2-E3 pattern)
- Error handling with читаемыми сообщениями

План: вынести всю бизнес-логику в `core/internal/deploy/context_promoter.py`.
Shell оставляет: валидацию CONTEXT, вызов Python, exit code.
## @RATIONALE
- Единственный entrypoint >100 LOC без Python-модуля — аномалия
- GIT_ASKPASS heredoc генерация в shell — Tier 1 триггер (heredoc с бизнес-логикой)
- 161→40 LOC (−75%), закрывает последний пробел в entrypoints
## @ACCEPTANCE_CRITERIA
- AC1: Python-модуль `core/internal/deploy/context_promoter.py` с функциями: check_ssh_available(), promote_via_ssh(), promote_via_https(), verify_mirror()
- AC2: GIT_ASKPASS token handling в Python (subprocess с env, без heredoc/trap)
- AC3: Shell-фасад ≤ 40 LOC (CONTEXT validation + вызов Python)
- AC4: SSH primary path работает идентично
- AC5: HTTPS fallback (GIT_MIRROR_TOKEN) работает идентично
- AC6: MIRROR_VERIFICATION (ls-remote HEAD == rev-parse HEAD) идентична
- AC7: Токен никогда не появляется в process list/shell history (как сейчас)
- AC8: `make context-promote CONTEXT=<ctx>` проходит без изменений
- AC9: Все существующие TRAP-аннотации сохранены
## @IMPLEMENTS Brief 103
## @IMPACTS core/entrypoints/context-promote.sh, core/internal/deploy/context_promoter.py (NEW), tests/unit/test_context_promoter.py (NEW), core/entrypoint-manifest.yaml
## @REQUIRES Ничего
