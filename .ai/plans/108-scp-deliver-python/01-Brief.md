# $ARTIFACT_CONTRACT
## @PURPOSE Миграция scp-deliver.sh (251 LOC) → Python-модуль + тонкий shell-фасад
## @DESCRIPTION
`core/internal/bootstrap/scp-deliver.sh` (251 LOC) — SCP/rsync доставка core-файлов на VPS.
Один из двух каналов доставки (Core — push-based SCP/rsync, NO git).

Функции:
- `prepare_ssh_opts()` — SSH-опции для разных режимов (init/update)
- `deliver_core()` — основная логика: rsync core/ + scp secrets/
- `deliver_node_configs()` — доставка node-configs/
- `ensure_remote_dirs()` — создание директорий на VPS
- Аудит-трейл логирование

**Особенность:** низкоуровневые системные операции (rsync, scp, ssh). Часть логики
уже в `overlay_deliverer.py` (sync-core). Нужно вынести оставшуюся оркестрацию.

**План:** вынести `deliver_core()`, `deliver_node_configs()`, `ensure_remote_dirs()` в Python
(`overlay_deliverer.py` расширение или `core_deliverer.py`). `prepare_ssh_opts()` остаётся
в shell (низкоуровневые SSH-опции).
## @RATIONALE
- 251 LOC — значительный объём для «низкоуровневой» операции
- После миграции remote-cmd.sh (Brief 101) и scp-deliver.sh, канал Core-доставки будет полностью в Python
- Аудит-трейл в Python — лучше тестируемость
## @ACCEPTANCE_CRITERIA
- AC1: Python-модуль `core_deliverer.py` (или расширение overlay_deliverer.py) с deliver_core(), deliver_node_configs(), ensure_remote_dirs()
- AC2: Shell-фасад ≤ 60 LOC (prepare_ssh_opts + вызов Python)
- AC3: `make bootstrap-node` — core доставка работает идентично
- AC4: `make node-update` — core доставка работает идентично
- AC5: DRY_RUN режим сохраняет поведение
- AC6: Аудит-трейл идентичен (логируются те же события)
- AC7: Rsync exclude-паттерны идентичны
- AC8: `make gate MODE=fast` зелёный (если применимо — scp-deliver не тестируется локально)
## @IMPLEMENTS Brief 108
## @IMPACTS core/internal/bootstrap/scp-deliver.sh, core/internal/bootstrap/core_deliverer.py (NEW) или overlay_deliverer.py (MODIFY), tests/unit/test_core_deliverer.py (NEW)
## @REQUIRES overlay_deliverer.py (уже существует, Wave 5d)
