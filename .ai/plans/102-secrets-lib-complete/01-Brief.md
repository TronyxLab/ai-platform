# $ARTIFACT_CONTRACT
## @PURPOSE Завершить миграцию core/lib/secrets.sh (291 LOC) — вынести остаточную бизнес-логику в Python
## @DESCRIPTION
План 078 (Secrets & Tokens Unification) завершён на 100% (Phase A+B), но миграция
`core/lib/secrets.sh` выполнена частично:

Уже в Python (через делегирование):
- `step_12b_ensure_secrets` → `secrets_manager.py ensure` ✅
- `_ensure_htpasswd_generated` → `shared/crypto.py` (078 T5) ✅
- `step_10_decrypt_secrets` → `decrypt_secrets.py` (086 T9) ✅

Осталось в shell:
- `step_10_decrypt_secrets()` — source secrets.env, sed proxy cleanup, AGE_SECRET_KEY/SOPS_AGE_KEY fallback + exit-1 abort (~46 LOC)
- `declare -f` stub-guard для step_start/step_done/step_skip (TRAP[BUG] 2026-07-23, L111-121)

Цель: вынести source + proxy cleanup в Python, оставить shell-оркестрацию <60 LOC.
Entrypoint `core/entrypoints/secrets.sh` уже тонкий (29 LOC) — НЕ трогаем.
## @RATIONALE
- 291 LOC lib-файла с бизнес-логикой — аномалия среди уже мигрированных lib (docker, audit, yaml_read, node-resolver)
- DevPlan 093 явно задеферрил полную миграцию, сейчас подходящий момент закрыть
- После миграции все lib-файлы с бизнес-логикой будут в Python (кроме vps-readiness — отдельный бриф)
## @ACCEPTANCE_CRITERIA
- AC1: `step_10_decrypt_secrets` source + proxy cleanup логика в Python (`secrets_manager.py` или новый `secrets_env_source.py`)
- AC2: Shell `step_10_decrypt_secrets` → вызов Python + exit code check (≤15 LOC)
- AC3: lib/secrets.sh общий размер ≤ 100 LOC
- AC4: `declare -f` stub-guard сохранён (source-safe)
- AC5: Поведение при отсутствии AGE_SECRET_KEY идентично
- AC6: Поведение при fallback SOPS_AGE_KEY идентично
- AC7: `make gate MODE=fast` зелёный
## @IMPLEMENTS Brief 102
## @IMPACTS core/lib/secrets.sh, core/internal/bootstrap/lifecycle/secrets_manager.py (MODIFY) или core/internal/secrets/secrets_env_source.py (NEW), tests/unit/test_secrets_env_source.py (NEW)
## @REQUIRES Ничего — secrets_manager.py, decrypt_secrets.py уже существуют
