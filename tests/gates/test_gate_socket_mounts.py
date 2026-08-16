# GREP_SUMMARY: gate socket-mounts docker.sock alloy socket-proxy hermes-agent prompt-injection host-root 162-W2-5 164-W1-5
# STRUCTURE: ▶ glob core/modules/*/docker-compose.base.yml → ○ per-service volumes ∋ docker.sock → ◇ svc ∈ ALLOWED (alloy|socket-proxy) ? → ⊕ violations → ⎋ FAIL|PASS
# region MODULE_CONTRACT
## @purpose  Gate-тест: сокет-маунты ТОЛЬКО alloy + socket-proxy (DevPlan 162 W2-5; promtail→Alloy 164 W1-5).
##           `/var/run/docker.sock` в volumes сервиса = доступ к docker API хоста. `:ro` на
##           unix-сокете НЕ блокирует connect() → prompt injection → docker API → host root.
##           hermes-agent (единственный бывший потребитель) — сокет удалён; возврат — только
##           через socket-proxy (tecnativa/docker-socket-proxy CONTAINERS=1 read-only).
## @scope    Все core/modules/*/docker-compose.base.yml (производственные compose модулей).
##           test-compose НЕ сканируются (изолированные CI-среды, не прод-вектор);
##           канон-исключение для них — docker-compose.test.yml hermes-agent (удалено, 162 W2-5).
## @invariants
##   - docker.sock маунт разрешён ТОЛЬКО сервисам alloy (сбор логов) и socket-proxy
##   - Любой другой сервис с docker.sock → violation (FAIL)
##   - Формат маунта: `<host>:/var/run/docker.sock:ro` (путь host может отличаться)
## @rationale DevPlan 162 W2-5 (P1 security): сокет = root-вектор; keep-список минимален.
##            Promtail легитимен (чтение docker-логов через API), socket-proxy — ограниченный
##            прокси (design intent DevPlan 162 W2-5 Rev). Всё остальное — RED.
## @changes  2026-08-13 | DevPlan 162 W2-5 — создан
# endregion MODULE_CONTRACT

import logging
import pathlib
from typing import Any

import pytest
import yaml

from tests._conftest.ldd import ldd_trajectory
from tests.helpers.gate_helpers import load_yaml, repo_root

logger = logging.getLogger(__name__)

MODULES_DIR = repo_root() / "core" / "modules"

# Keep-список сервисов, которым разрешён docker.sock (162 W2-5):
# alloy — сбор docker-логов (discovery.docker; promtail EOL 164 W1-5), socket-proxy — ограниченный прокси.
ALLOWED_SOCKET_MOUNT_SERVICES = {"alloy", "socket-proxy"}

DOCKER_SOCKET_HOST_PATHS = ("/var/run/docker.sock", "/run/docker.sock")


def _load_compose(path: pathlib.Path) -> dict[str, Any] | None:
    """Load compose YAML or return None on error."""
    try:
        data = load_yaml(path)
        return data if isinstance(data, dict) else None
    except (FileNotFoundError, yaml.YAMLError):
        return None


def _base_compose_files() -> list[pathlib.Path]:
    """All core/modules/*/docker-compose.base.yml."""
    return sorted(MODULES_DIR.glob("*/docker-compose.base.yml"))


def _service_has_docker_socket(volumes: Any) -> bool:
    """True если volumes сервиса содержит docker.sock-маунт (host-путь или контейнер-путь)."""
    if not isinstance(volumes, list):
        return False
    for vol in volumes:
        if not isinstance(vol, str):
            continue
        parts = [p.strip() for p in vol.split(":")]
        if not parts:
            continue
        container_path = parts[1] if len(parts) > 1 else parts[0]
        if container_path == "/var/run/docker.sock":
            return True
        if parts[0] in DOCKER_SOCKET_HOST_PATHS:
            return True
    return False


def _find_socket_mounts() -> list[str]:
    """List '<module>/<service>' with docker.sock mount (violations)."""
    violations: list[str] = []
    for path in _base_compose_files():
        data = _load_compose(path)
        if data is None:
            logger.info("[IMP:7][gate-socket-mounts] skip unparsed compose: %s", path.name)
            continue
        services = data.get("services", {})
        if not isinstance(services, dict):
            continue
        module = path.parent.name
        for svc_name, svc_def in services.items():
            if not isinstance(svc_def, dict):
                continue
            if _service_has_docker_socket(svc_def.get("volumes")):
                if svc_name in ALLOWED_SOCKET_MOUNT_SERVICES:
                    logger.info(
                        "[IMP:8][gate-socket-mounts] %s/%s docker.sock (allowed keep-list)",
                        module,
                        svc_name,
                    )
                    continue
                violations.append(f"{module}/{svc_name}")
                logger.info("[IMP:9][gate-socket-mounts] FAIL: %s/%s mounts docker.sock", module, svc_name)
    return violations


@pytest.mark.gate
@ldd_trajectory
# 🧪 TRAP[TEST] · 2026-08-13 · REGRESSION · Gate invariant — docker.sock только promtail/socket-proxy
# · Scenario: все core/modules/*/docker-compose.base.yml → volumes сервисов → docker.sock
# · Last fail: hermes-agent монтировал /var/run/docker.sock:ro (162 W2-5) — :ro не блокирует connect()
# · Remove if: политика сокет-маунтов отменена архитектором (пересмотр 162 W2-5 Rev)
def test_gate_socket_mounts_only_allowed(caplog) -> None:
    """Докер-сокет маунтится ТОЛЬКО alloy/socket-proxy (host-root вектор закрыт)."""
    violations = _find_socket_mounts()

    assert not violations, (
        "[IMP:9][gate-socket-mounts] docker.sock mount вне keep-листа "
        f"(alloy|socket-proxy): {violations} — :ro на unix-сокете НЕ блокирует API (162 W2-5)"
    )
    logger.info(
        "[IMP:9][gate-socket-mounts] PASS: docker.sock mounts restricted to %s", sorted(ALLOWED_SOCKET_MOUNT_SERVICES)
    )
