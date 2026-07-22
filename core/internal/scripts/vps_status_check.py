# GREP_SUMMARY: vps_status_check, project-status, CI-preflight, verify-deliver, stdin-json
# STRUCTURE: ▶ main() → ◇ read stdin → ⊕ parse JSON → ⊕ validate status ∈ {found,stub} → ⎋ exit 0|1|2|3
# region MODULE_CONTRACT
## @purpose  CLI-валидатор статуса проекта на VPS. Принимает JSON project-status из stdin.
##           Заменяет inline `python3 -c "import json,sys..."` в deploy-project.yml (CICD-01d).
## @scope    Проверяет, что status ∈ {found, stub}. Используется в preflight и verify-deliver шагах CI.
##           Поддерживает режим --output-status-only для subshell-подстановки (печать статуса в лог).
## @invariants
##   - status ∈ {found, stub} → exit 0 (+ IMP:9 success message)
##   - status ∉ {found, stub} → exit 1 (IMP:10 stderr diagnostic)
##   - malformed JSON → exit 2 (IMP:10 stderr)
##   - пустой stdin → exit 3 (IMP:10 stderr)
##   - --output-status-only: печать голого значения статуса в stdout (для subshell), exit 0 всегда
## @rationale deploy-project.yml содержит 3 inline python3 для чтения STATUS_JSON из stdin (строки
##           100, 107×2, 136). Экстракция в typed-модуль даёт тестируемость и единый contract.
## @changes
##   LAST_CHANGE: 2026-07-22 | Created (StatusReport 046 T5 — CICD-01d)
# endregion MODULE_CONTRACT

from __future__ import annotations

# region IMPORTS
import argparse
import json
import sys

# endregion IMPORTS


# region CONSTANTS

VALID_STATUSES = {"found", "stub"}

# endregion CONSTANTS


# region PUBLIC_API


class EmptyStdinError(ValueError):
    """Raised when stdin is empty or whitespace-only."""


def parse_status_json(raw: str) -> dict:
    """Parse JSON from raw string.

    ▶ raw → ◇ json.loads → ⎋ dict

    ## @purpose  Распарсить JSON строку в dict.
    ## @io       in: str → out: dict
    ## @complexity O(N) где N = длина строки

    Raises:
        EmptyStdinError: if raw is empty/whitespace (caller distinguishes from malformed)
        json.JSONDecodeError: if raw is non-empty but not valid JSON
    """
    if not raw.strip():
        raise EmptyStdinError("empty stdin")
    return json.loads(raw)


# endregion PUBLIC_API


# region CLI


def main() -> int:
    """CLI entrypoint.

    ▶ argv → ◇ read stdin → ⊕ parse JSON → ⊕ validate status → ⎋ exit 0|1|2|3

    Modes:
      - default: validate status ∈ {found, stub}, exit 0 on valid, 1 on invalid
      - --output-status-only: print bare status value to stdout, exit 0 always
    """
    parser = argparse.ArgumentParser(
        prog="vps_status_check.py",
        description="Validate VPS project status from stdin JSON. Replaces inline python3 in deploy-project.yml.",
    )
    parser.add_argument(
        "--output-status-only",
        action="store_true",
        help="Print only the status value (for subshell use). Always exits 0 if JSON parses.",
    )
    args = parser.parse_args()

    raw = sys.stdin.read()

    try:
        data = parse_status_json(raw)
    except EmptyStdinError:
        # ⚠️ TRAP[BUG] · 2026-07-22 · MED · json.JSONDecodeError is subclass of ValueError
        # · Symptom: malformed JSON caught by `except ValueError` → exit 3 (empty) instead of exit 2 (malformed)
        # · Root: parse_status_json raised generic ValueError for empty, but json.JSONDecodeError (also ValueError)
        # ·   was caught by the same except clause
        # · Fix: dedicated EmptyStdinError subclass, caught by specific except (order matters)
        print("[IMP:10][vps-status] ERROR: empty stdin", file=sys.stderr)
        return 3
    except json.JSONDecodeError as e:
        print(f"[IMP:10][vps-status] ERROR: invalid JSON: {e}", file=sys.stderr)
        return 2

    if not isinstance(data, dict):
        print(f"[IMP:10][vps-status] ERROR: root is not a JSON object (got {type(data).__name__})", file=sys.stderr)
        return 2

    status = data.get("status", "")

    # DRIFT-046-3: --output-status-only для subshell-подстановки (print-only use-case)
    if args.output_status_only:
        print(status)
        return 0

    if status in VALID_STATUSES:
        print(f"[IMP:9][vps-status] VPS project status: {status}")
        return 0

    print(
        f"[IMP:10][vps-status] ERROR: unexpected status: {status!r} (expected one of {sorted(VALID_STATUSES)})",
        file=sys.stderr,
    )
    return 1


if __name__ == "__main__":
    sys.exit(main())


# endregion CLI
