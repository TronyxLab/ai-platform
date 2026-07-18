# GREP_SUMMARY: _conftest, conftest-package, re-export, conftest-modules, public-names
# STRUCTURE: __init__ → import all public (non-underscore) names from _conftest submodules
# region MODULE_CONTRACT
## @purpose — Re-export public (non-underscore) names from _conftest/* submodules for
##            backward-compatible access via conftest.py thin re-export layer.
##            Underscore-prefixed names (internal/cross-module helpers) are NOT re-exported
##            here — they are imported directly by _conftest/ submodules or re-exported
##            explicitly in tests/conftest.py.
## @scope — Internal package; non-underscore names are re-exported by tests/conftest.py
##          for test file consumption via `from _conftest import *`.
## @invariants
##   - Only PUBLIC (non-underscore) names are re-exported here
##   - Underscore-prefixed names used across _conftest/ submodules import directly
##   - conftest.py's explicit underscore imports are the conftest-level public API
## @rationale — Central re-export hub so conftest.py can do `from _conftest import *`
##              without name collisions. Per T6 cleanup: removed stale re-exports of
##              underscore-prefixed names that no external test consumes.
# endregion MODULE_CONTRACT

# ── audit ─────────────────────────────────────────────────────────────
from _conftest.audit import (  # noqa: F401
    all_compose_files,
    all_module_yamls,
    all_networks,
    discover_docker_modules,
    module_graph,
    modules_dir,
    platform_root,
)

# ── e2e ────────────────────────────────────────────────────────────────
from _conftest.e2e import (  # noqa: F401
    GRAFANA_URL,
    LOKI_PROXY_URL,
    PROMETHEUS_PROXY_URL,
    datasource_uids,
    grafana_credentials,
)

# ── infra ─────────────────────────────────────────────────────────────
from _conftest.infra import (  # noqa: F401
    platform_ports,
    test_infra,
)

# ── ldd ───────────────────────────────────────────────────────────────
from _conftest.ldd import (  # noqa: F401
    ldd_trajectory,
)

# ── networks ──────────────────────────────────────────────────────────
from _conftest.networks import (  # noqa: F401
    EXEMPT_CREATED_NETWORKS,
    PLATFORM_NETWORKS,
    docker_available,
    ensure_external_networks,
    is_production_host,
)

# ── secrets ───────────────────────────────────────────────────────────
from _conftest.secrets import (  # noqa: F401
    scan_directory_for_secrets,
)

# ── shellcheck ──────────────────────────────────────────────────────────
from _conftest.shellcheck import (  # noqa: F401
    _check_shellcheck_available,
    _parse_shellcheck_sc2154,
    get_shellcheck_bash_calls,
)

# ── session ───────────────────────────────────────────────────────────
from _conftest.session import (  # noqa: F401
    pytest_sessionfinish,
    pytest_sessionstart,
)

# ── skip_gate ─────────────────────────────────────────────────────────
from _conftest.skip_gate import (  # noqa: F401
    automatic_skip_gate,
    pytest_runtest_makereport,
)

# ── smoke ─────────────────────────────────────────────────────────────
from _conftest.smoke import (  # noqa: F401
    SMOKE_ENV,
    platform_env,
    platform_services,
)

# ── utilities ─────────────────────────────────────────────────────────
from _conftest.utilities import (  # noqa: F401
    assert_ldd_stderr,
    source_and_run,
)
