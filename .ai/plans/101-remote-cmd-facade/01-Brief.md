# $ARTIFACT_CONTRACT
## @PURPOSE remote-cmd.sh (266 LOC) → реальный тонкий фасад ≤60 LOC
## @DESCRIPTION
`core/internal/bootstrap/remote-cmd.sh` документирован как 230 LOC (Wave 5d),
фактически 266 LOC. Wave 5d создала Python-модуль `overlay_deliverer.py` для
deliver/extract/resolve, но shell-оркестрация осталась раздутой.

Текущее состояние:
- `build_ssh_cmd()`, `build_update_ssh_cmd()`, `build_converge_ssh_cmd()` — printf %q builders (D3 решение, остаются)
- `_resolve_and_extract()` — уже делегирует в Python
- `execute_remote_update()` — 38 строк оркестрации: source scp-deliver, resolve, VPS self-SSH detect, prepare_ssh_opts, sync-core, build command, ssh_exec
- `execute_remote_converge()` — 20 строк оркестрации
- `execute_remote_reconcile()` — 20 строк оркестрации (почти идентична converge)
- `execute_remote_reconcile_entrypoint()` — 3 строки (passthrough)
- `deliver_vhost_overlays()` — уже Python-фасад

План: вынести execute_remote_* оркестрацию в Python (`overlay_deliverer.py` расширение или новый `remote_executor.py`).
printf %q D3 решение НЕ трогаем (TRAP[DECISION]).
## @RATIONALE
- 266 LOC при задокументированных 230 = drift
- 3 почти идентичные execute_remote_* функции — 78 строк копипасты
- Python-модуль уже существует (overlay_deliverer.py) — естественное расширение
## @ACCEPTANCE_CRITERIA
- AC1: Python-модуль `remote_executor.py` (или расширение overlay_deliverer.py) с execute_remote_* логикой
- AC2: Shell-фасад ≤ 60 LOC (printf %q builders + вызов Python)
- AC3: execute_remote_update работает идентично (bootstrap/node-update)
- AC4: execute_remote_converge работает идентично
- AC5: execute_remote_reconcile работает идентично
- AC6: DRY_RUN режим сохраняет поведение
- AC7: AGENTS.md обновлён: 230→60 LOC
- AC8: Все TRAP-аннотации сохранены (P0 VPS self-SSH loop, P4 ssh_exec, P1 PLATFORM_ROOT export)
## @IMPLEMENTS Brief 101
## @IMPACTS core/internal/bootstrap/remote-cmd.sh, core/internal/bootstrap/remote_executor.py (NEW) или overlay_deliverer.py (MODIFY), core/internal/bootstrap/AGENTS.md
## @REQUIRES Ничего — overlay_deliverer.py уже существует
