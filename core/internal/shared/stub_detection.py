#!/usr/bin/env python3
# GREP_SUMMARY: stub-detection, is-stub-ai-platform-yaml, GENERATED-STUB, shared, converge, reconciler-projects
# STRUCTURE: ▶ ┌path┐ → ◇ is_file? → ⚡ read_text().splitlines()[0] (st_size>0) → ◇ "GENERATED-STUB" in first_line? → ⎋ bool
# region MODULE_CONTRACT
## @purpose  Единая реализация is_stub-детекции ai-platform.yaml (GENERATED-STUB marker).
##           Консолидирует дубль reconciler_projects.py:146-166 и converge/reconciler.py:825-843
##           (DevPlan 116 B9 T4, U-28). Поведение идентично обоим предшественникам.
## @scope    shared/stub_detection.py: is_stub_ai_platform_yaml(path). Потребители:
##           core/internal/reconciler_projects.py (тонкий wrapper is_stub_project),
##           converge/projects.py (R3 — прямой вызов).
## @invariants
##   - Первая строка файла содержит "GENERATED-STUB" → True
##   - Missing/empty/OSError/IndexError → False (никогда не raise)
##   - Empty файл (st_size == 0) → splitlines() НЕ вызывается (IndexError-защита)
## @rationale U-28: два идентичных алгоритма (_is_stub в reconciler, is_stub_project в
##            reconciler_projects) — консолидируются в ОДНУ функцию. R3 local stub-создание и
##            remote stub→deploy ОРТОГОНАЛЬНЫ (инвариант 4) — консолидируется только детекция.
## @changes  2026-08-01 · Created (B9 T4, U-28)
# endregion MODULE_CONTRACT

from __future__ import annotations

from pathlib import Path


# region FUNC_is_stub_ai_platform_yaml
## @purpose  Check if ai-platform.yaml is a GENERATED-STUB (not real config).
## @io       ⇥ path: str | Path → ⎋ bool (True = stub)
## @complexity O(1) — чтение первой строки
## @invariants
##   - "GENERATED-STUB" ищется в ПЕРВОЙ строке (маркер пишется в шапку stubs)
##   - False при missing/empty/OSError/IndexError — graceful degradation, никогда не raise
def is_stub_ai_platform_yaml(path: str | Path) -> bool:
    """Check whether ai-platform.yaml is a GENERATED-STUB.

    Reads the first line of the file and checks for the GENERATED-STUB marker.
    A missing file or a file without the marker is NOT a stub.
    """
    path_obj = Path(path)
    if not path_obj.is_file():
        return False
    try:
        first_line = path_obj.read_text().splitlines()[0] if path_obj.stat().st_size > 0 else ""
        return "GENERATED-STUB" in first_line
    except (OSError, IndexError):
        return False


# endregion FUNC_is_stub_ai_platform_yaml
