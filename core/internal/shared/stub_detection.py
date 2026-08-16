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

import logging
from pathlib import Path

logger = logging.getLogger(__name__)


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
        first_line = path_obj.read_text(encoding="utf-8").splitlines()[0] if path_obj.stat().st_size > 0 else ""
    except (OSError, IndexError):
        return False
    else:
        return "GENERATED-STUB" in first_line


# endregion FUNC_is_stub_ai_platform_yaml


# region FUNC_is_stub_container
## @purpose  Check if a running Docker container is a bootstrap stub (label ai-platform.bootstrap=true).
##           DevPlan 153 T6 (N1): stub-контейнер (nginx:alpine из _ensure_bootstrap_compose) проходит
##           healthcheck и маскировал недоставленный проект в deploy-context skip-логике.
## @io       ⇥ container_name: str, docker_inspect_fn: Callable | None (DI, DevPlan 167 D3)
##           → ⎋ bool (True = stub)
## @complexity O(1) — docker inspect I/O
## @invariants
##   - docker CLI ТОЛЬКО через shared/docker_ops.docker_inspect (гейт docker_sole_path)
##   - Точный матч label через {{index .Config.Labels "ai-platform.bootstrap"}} == "true"
##   - Docker unavailable / контейнер отсутствует (rc != 0) → False (никогда не raise)
## 🧐 TRAP[DI-SEAM] · 2026-08-14 · — · docker_inspect_fn DI-параметр (167 D3)
## · Rejected: прямой module-level вызов docker_ops.docker_inspect
## · Reason: seam = тестируемость реального inspect-вызова (тест передаёт fake-fn вместо
## ·   monkeypatch.setattr(docker_ops, "docker_inspect"); прод — module fallback без изменений)
## · Rev: если docker_inspect станет методом объекта-клиента — fn заменится инстансом
def is_stub_container(container_name: str, *, docker_inspect_fn=None) -> bool:
    """Check whether a Docker container is a bootstrap stub (label ai-platform.bootstrap=true).

    ▶ ┌container_name┐ → docker_inspect({{index .Config.Labels "ai-platform.bootstrap"}}) →
    │   ◇ rc != 0 → ⎋ False │ ◇ stdout.strip() == "true" → ⎋ True │ else → ⎋ False
    """
    from core.internal.shared.docker_ops import docker_inspect

    inspect_fn = docker_inspect_fn if docker_inspect_fn is not None else docker_inspect
    result = inspect_fn(container_name, format='{{index .Config.Labels "ai-platform.bootstrap"}}')
    if result.returncode != 0:
        logger.debug("[IMP:6][stub_detection] docker inspect failed for %s — not a stub", container_name)
        return False
    is_stub = (result.stdout or "").strip().lower() == "true"
    if is_stub:
        logger.info("[IMP:9][stub_detection] %s — stub container detected (ai-platform.bootstrap=true)", container_name)
    return is_stub


# endregion FUNC_is_stub_container
