# $ARTIFACT_CONTRACT
## @PURPOSE Миграция vps-readiness.sh (181 LOC) → Python-модуль + fix латентного бага `$first`
## @DESCRIPTION
`core/lib/vps-readiness.sh` (181 LOC) — последний lib-файл с бизнес-логикой в bash.
4 pre-flight проверки с fail-fast порядком, remediation hints, JSON-диагностика для CI.

Функции для миграции:
- `check_vps_ready()` — оркестрация проверок с fail-fast
- SSH check (10s timeout)
- Forced-command ping
- /opt/projects/ exists + writable
- Docker daemon (skip if --quick)
- JSON diagnostics (--json)
- Remediation hints для каждого failure mode

⚠️ **Латентный баг:** строка 170: `$first || json_diag+=","` — после `first=false`
bash пытается выполнить пустую команду → `false: command not found`.
Корректно: `if ! $first; then json_diag+=","; fi`

План: вынести всю бизнес-логику в `core/internal/shared/vps_readiness.py`.
Shell оставляет: source ssh.sh, вызов Python, exit code.
## @RATIONALE
- Последний lib-файл с бизнес-логикой не в Python (после миграции secrets.sh в Brief 102)
- Латентный баг в JSON-диагностике — активный дефект
- 181→40 LOC (−78%)
## @ACCEPTANCE_CRITERIA
- AC1: Python-модуль `core/internal/shared/vps_readiness.py` с check_vps_ready() и всеми проверками
- AC2: Shell-фасад ≤ 40 LOC (source ssh.sh + вызов Python)
- AC3: `check_vps_ready <node>` работает идентично
- AC4: `check_vps_ready <node> --quick` работает идентично
- AC5: `check_vps_ready <node> --json` работает идентично
- AC6: Латентный баг `$first` исправлен в Python-версии
- AC7: Все remediation hints сохранены
- AC8: NODE_HOST_MAP резолвинг идентичен
- AC9: Unit-тесты на check_vps_ready (mock ssh_read)
- AC10: `make gate MODE=fast` зелёный
## @IMPLEMENTS Brief 105
## @IMPACTS core/lib/vps-readiness.sh, core/internal/shared/vps_readiness.py (NEW), tests/unit/test_vps_readiness.py (NEW)
## @REQUIRES Ничего — lib/ssh.sh используется как external dependency
