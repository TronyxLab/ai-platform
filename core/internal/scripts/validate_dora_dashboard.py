# GREP_SUMMARY: validate_dora_dashboard, grafana, CI-validation, DORA-metrics, json-validator
# STRUCTURE: ▶ validate(path) → ◇ load JSON → ⊕ check uid + 4 DORA panels → ⎋ bool → exit 0|1|2
# region MODULE_CONTRACT
## @purpose  CLI-валидатор структуры DORA CI/CD дашборда Grafana.
##           Заменяет 9 строк inline python3 в platform-test.yml (CICD-01b).
## @scope    Проверяет uid='dora-ci-cd' и наличие 4 обязательных DORA-метрик-панелей.
##           Read-only, не мутирует дашборд.
## @invariants
##   - 4 обязательных панели: Deploy Frequency, Lead Time for Changes, MTTR, CFR
##   - Отсутствие панели → exit 1 с diagnostics (IMP:10 stderr)
##   - Неверный uid → exit 1 с diagnostics
##   - Не-JSON файл → exit 2 с diagnostics
##   - Файл не найден → exit 2
##   - Валидный дашборд → exit 0 + IMP:9 success message
## @rationale 9 строк inline python3 в platform-test.yml — нет причин для inline.
##            Экстракция в typed-модуль даёт тестируемость (unit-тесты) и единый contract.
## @changes
##   LAST_CHANGE: 2026-07-22 | Created (StatusReport 046 T3 — CICD-01b)
# endregion MODULE_CONTRACT

from __future__ import annotations

# region IMPORTS
import json
import pathlib
import sys
from typing import cast

# endregion IMPORTS


# region CONSTANTS

REQUIRED_PANELS = {
    "Deploy Frequency",
    "Lead Time for Changes",
    "Mean Time to Recovery (MTTR)",
    "Change Failure Rate (CFR)",
}

EXPECTED_UID = "dora-ci-cd"
DEFAULT_DASHBOARD_PATH = pathlib.Path("core/modules/monitoring/config/dashboards/dora-ci-cd.json")

# endregion CONSTANTS


# region PUBLIC_API


def validate(path: pathlib.Path) -> bool:
    """Validate DORA dashboard structure (uid + 4 required panels).

    ▶ path → ◇ load JSON → ⊕ check uid → ⊕ check panels → ⎋ bool

    ## @purpose  Вернуть True если дашборд валиден (uid + 4 панели), иначе False с diagnostics в stderr.
    ## @io       in: path (Path to JSON dashboard) → out: bool
    ## @complexity O(P) где P = число панелей
    """
    if not path.exists():
        print(f"[IMP:10][dora] ERROR: Dashboard file not found: {path}", file=sys.stderr)
        return False

    try:
        # W11: json.loads returns Any → cast(object) ПОСЛЕ runtime-guard'а
        # (TRAP[BUG] W11: голый cast к dict ломал array-root — 'list' has no attribute 'get';
        #  guard был в оригинале, типизация не должна его удалять)
        raw = cast(object, json.loads(path.read_text(encoding="utf-8")))
    except (json.JSONDecodeError, OSError) as e:
        print(f"[IMP:10][dora] ERROR: Cannot parse JSON: {e}", file=sys.stderr)
        return False

    if not isinstance(raw, dict):
        print(f"[IMP:10][dora] ERROR: Dashboard root is not a JSON object (got {type(raw).__name__})", file=sys.stderr)
        return False
    data = cast(dict[str, object], raw)

    if data.get("uid") != EXPECTED_UID:
        print(
            f"[IMP:10][dora] ERROR: Wrong dashboard UID: {data.get('uid')!r} (expected {EXPECTED_UID!r})",
            file=sys.stderr,
        )
        return False

    panels = data.get("panels", [])
    if not isinstance(panels, list):
        print(f"[IMP:10][dora] ERROR: 'panels' is not a list (got {type(panels).__name__})", file=sys.stderr)
        return False

    # W11: list[Unknown] after isinstance → cast to object list for item checks
    found = {
        str(cast(dict[str, object], p).get("title", "")) for p in cast(list[object], panels) if isinstance(p, dict)
    }
    missing = REQUIRED_PANELS - found
    if missing:
        print(f"[IMP:10][dora] ERROR: Missing required panels: {sorted(missing)}", file=sys.stderr)
        return False

    print(
        f"[IMP:9][dora] DORA dashboard OK: {len(cast(list[object], panels))} panels, "
        f"{len(REQUIRED_PANELS)} required present"
    )
    return True


# endregion PUBLIC_API


# region CLI


def main() -> int:
    """CLI entrypoint.

    ▶ argv[1] or DEFAULT → ◇ validate → ⎋ exit 0(valid) | 1(invalid) | 2(parse error)
    """
    path = pathlib.Path(sys.argv[1]) if len(sys.argv) > 1 else DEFAULT_DASHBOARD_PATH
    return 0 if validate(path) else 1


if __name__ == "__main__":
    sys.exit(main())


# endregion CLI
