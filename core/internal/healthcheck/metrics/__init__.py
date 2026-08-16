# GREP_SUMMARY: metrics package healthcheck docker cert project host json-writer cache
# STRUCTURE: ┌re-export public symbols┐ → docker_collector, cert_collector, project_collector, host_collector, json_writer, cache
# region MODULE_CONTRACT
## @purpose  Package init for healthcheck metric collectors — re-exports public symbols
## @scope    Modules: docker_collector, cert_collector, project_collector, host_collector, json_writer, cache
## @invariants
##   - Public symbols re-exported for coordinator convenience import
##   - All collectors return plain dicts (no custom types) — JSON-serializable
## @rationale Single import point for platform_export_metrics.py coordinator
# endregion MODULE_CONTRACT

from core.internal.healthcheck.metrics.cache import CacheManager
from core.internal.healthcheck.metrics.cert_collector import get_certs
from core.internal.healthcheck.metrics.docker_collector import get_containers, get_image_sizes
from core.internal.healthcheck.metrics.host_collector import get_host_disk
from core.internal.healthcheck.metrics.json_writer import SCHEMA_VERSION, atomic_write
from core.internal.healthcheck.metrics.project_collector import get_projects

# B9 T6.1 (гейт приватных межмодульных импортов, allowlist пуст): приватные хелперы
# cert_collector._load_cert/_san_match НЕ re-экспортируются — 0 потребителей через пакет
# (white-box тест импортирует из cert_collector напрямую — tests/ вне скоупа гейта).

__all__ = [
    "SCHEMA_VERSION",
    "CacheManager",
    "atomic_write",
    "get_certs",
    "get_containers",
    "get_host_disk",
    "get_image_sizes",
    "get_projects",
]
