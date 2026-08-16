# GREP_SUMMARY: status-page collectors facade re-export config checks http containers platform staleness readiness aggregate
# STRUCTURE: ┌collectors/ package facade┐ → re-export config + checks + staleness + readiness + aggregate → ⎋ app.py API (back-compat)
# region MODULE_CONTRACT
## @purpose  Facade of status-page collectors package (DevPlan 170 W7-E2). Re-exports all public
##           AND private names consumed by app.py and existing tests — backward-compatible paths
##           (collectors.get_all_checks, collectors._check_container, collectors._curl_vhost,
##           collectors._check_platform_service, collectors._curl_platform_service, ...).
## @scope    Decomposition of collectors.py (621 LOC) into {config, checks/{http,containers,platform},
##           staleness, readiness, aggregate}.py — facade preserves the module-level API.
## @invariants
##   - NO core/internal imports (cross-layer violation forbidden for modules)
##   - All names below are re-exports — behavior defined in the submodules (single implementation)
##   - import socket/subprocess — тестовые mock-пути "collectors.socket.*"/"collectors.subprocess.*"
##     резолвятся через атрибуты фасада (мок глобальных stdlib-модулей, W7-E2 совместимость)
## @rationale  DevPlan 170 W7-E2 — фасад = единственная точка импорта для app.py и тестов;
##            приватные имена тестов сохранены (research-A §6: collectors._check_container,
##            collectors._curl_vhost — 4 тест-файла).
## @changes  2026-08-15 · DevPlan 170 W7-E2 — created (collectors.py → package)
# ⚠️ TRAP[DECISION] · 2026-08-15 · — · Приватные алиасы в фасаде: публичные имена в подмодулях +
# ·   `from X import name as _alias` в __init__ — единственный легальный для static private-imports
# ·   способ сохранить приватные пути тестов (collectors._check_container/_curl_vhost/...)
# · Rejected: обновить все импорты/mock-пути тестов на подмодульные (collectors.checks.*) — 4 тест-файла
# · Reason: research-A §6 явно требует сохранить приватные имена; детектор private-imports
# ·   (core/internal/static) разрешает публичную сущность + приватный алиас, но запрещает
# ·   `from X import _name` без alias (allowlist пуст). Mock-пути socket/subprocess работают
# ·   глобально (патчат stdlib-модули), не через фасад.
# · Rev: полная миграция тестов на подмодульные публичные имена → снять алиасы фасада.
# endregion MODULE_CONTRACT

import socket
import subprocess

# Приватные алиасы на ПУБЛИЧНЫЕ имена подмодулей (канон static private-imports:
# `from X import name as _alias` — публичная сущность, приватный только алиас).
from .aggregate import (
    compute_overall as _compute_overall,
)
from .aggregate import (
    fan_out_checks as _fan_out_checks,
)
from .aggregate import (
    get_all_checks,
)
from .checks.containers import check_container as _check_container
from .checks.http import (
    HTTP_OK,
    HTTP_REDIRECT_MAX,
    HTTP_REDIRECT_MIN,
)
from .checks.http import (
    classify_http as _classify_http,
)
from .checks.http import (
    curl_http_code as _curl_http_code,
)
from .checks.http import (
    curl_platform_service as _curl_platform_service,
)
from .checks.http import (
    curl_vhost as _curl_vhost,
)
from .checks.platform import PLATFORM_SERVICES
from .checks.platform import check_platform_service as _check_platform_service
from .config import (
    extract_node_name,
    get_modules,
    get_vhosts,
    load_node_yaml,
    load_status_metrics,
    resolve_node_yaml_path,
)
from .readiness import readiness_check
from .staleness import compute_staleness

__all__ = [
    "HTTP_OK",
    "HTTP_REDIRECT_MAX",
    "HTTP_REDIRECT_MIN",
    "PLATFORM_SERVICES",
    # checks/containers
    "_check_container",
    # checks/platform
    "_check_platform_service",
    "_classify_http",
    # aggregate
    "_compute_overall",
    "_curl_http_code",
    "_curl_platform_service",
    "_curl_vhost",
    "_fan_out_checks",
    # staleness
    "compute_staleness",
    # config
    "extract_node_name",
    "get_all_checks",
    "get_modules",
    "get_vhosts",
    "load_node_yaml",
    "load_status_metrics",
    # readiness
    "readiness_check",
    "resolve_node_yaml_path",
]
