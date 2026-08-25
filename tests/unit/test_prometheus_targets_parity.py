# GREP_SUMMARY: prometheus-targets-parity emitted-exporter-ports PEER_PUBLISH_PORTS peer-matrix 9127 9122 REF-0010 gap R5-negative DevPlan-16-T2A
# STRUCTURE: ▶ _NODE_TARGET_JOBS emission set → ◇ ⊆ flatten(PEER_PUBLISH_PORTS) → ⊕ R5-негатив (порт вне матрицы → детектор) → ⎋ PASS|FAIL
# region MODULE_CONTRACT
## @purpose  Parity-гейт эмит-set ↔ peer-матрица (DevPlan 16 T2.A / P1-3): каждый порт,
##           который prometheus_targets эмитит в node-jobs, обязан присутствовать в
##           PEER_PUBLISH_PORTS — иначе кросс-нодовый scrape молча DROPается (разрыв
##           REF-0010: 9127 pgbouncer-exporter и 9122 langfuse-redis-exporter были в targets,
##           но не в матрице/ deny-листах).
## @scope    tests/unit: чистые константы двух модулей; 0 subprocess.
## @invariants
##   - Emission set = {job.port для всех _NODE_TARGET_JOBS} (единый источник факта)
##   - R5-негатив: порт, изъятый из матрицы, детектируется missing-функцией
# endregion MODULE_CONTRACT

import logging

from core.internal.bootstrap import firewall
from core.internal.monitoring.prometheus_targets import _NODE_TARGET_JOBS

logger = logging.getLogger(__name__)

EMITTED_EXPORTER_PORTS: frozenset[int] = frozenset(job.port for job in _NODE_TARGET_JOBS)


def _missing_emission_ports(publish_flat: frozenset[int]) -> set[int]:
    """Порты эмиссии, отсутствующие в peer-матрице (детектор parity-гейта)."""
    return set(EMITTED_EXPORTER_PORTS) - set(publish_flat)


# 🧪 TRAP[TEST] · REGRESSION · DevPlan 16 T2.A P1-3 · эмит-set ⊆ PEER_PUBLISH_PORTS
# · Last fail: аудит 15 P1-3 — 9127 (pgbouncer-exporter) и 9122 (langfuse-redis-exporter)
#   эмитились в targets, но отсутствовали в PEER_PUBLISH_PORTS/MODULE_PORTS_DENY:
#   кросс-нодовый scrape этих экспортёров молча DROPался (job down без алерт-причины)
# · Scenario: все порты _NODE_TARGET_JOBS покрыты матрицей публикации
# · Remove if: parity переносится в общий SoT-гейт platform_ports↔targets
def test_emitted_exporter_ports_in_peer_matrix() -> None:
    publish_flat = frozenset(p for ports in firewall.PEER_PUBLISH_PORTS.values() for p in ports)
    missing = _missing_emission_ports(publish_flat)
    assert not missing, (
        f"порты node-targets вне peer-матрицы (кросс-нодовый scrape мёртв): {sorted(missing)}; "
        f"матрица={sorted(publish_flat)}"
    )
    # Deny-лист синхронен: exporter-порты закрыты для extra_ports (S-8 defense-in-depth)
    for port in EMITTED_EXPORTER_PORTS:
        assert port in firewall.MODULE_PORTS_DENY or port in firewall.PEER_PUBLISH_PORTS.get("node-metrics", ()), (
            f"порт {port} обязан быть в MODULE_PORTS_DENY (deny extra_ports)"
        )
    logger.info("[IMP:9][parity][assert] %d emitted ports ⊆ peer-matrix ✓", len(EMITTED_EXPORTER_PORTS))


# 🧪 TRAP[TEST] · NEGATIVE (R5) · DevPlan 16 T2.A · изъятие порта из матрицы детектируется
# · Scenario: simulated-матрица БЕЗ 9127 (исходный дефект REF-0010) → детектор рапортует
#   недостающий порт (red→green: до T2.A гейта не существовало вовсе)
# · Remove if: детектор заменён структурным сравнением SoT
def test_missing_port_detected_negative() -> None:
    publish_flat = frozenset(p for ports in firewall.PEER_PUBLISH_PORTS.values() for p in ports)
    broken = publish_flat - {firewall.PGBOUNCER_EXPORTER}
    missing = _missing_emission_ports(frozenset(broken))
    assert firewall.PGBOUNCER_EXPORTER in missing, "R5 FAIL: изъятие 9127 не детектируется"
    logger.info("[IMP:9][parity][negative] удаление порта матрицы ловится ✓")
