# GREP_SUMMARY: test-firewall-peer placement build-peer-rules consumer-of publish-ports platform-peer stale-reconcile delete-from-source verify-peer-allow single-node-noop s2 s3 fixtures cross-node multi-node
# STRUCTURE: ▶ tmp_path + placement fixtures (s2/s3) → ○ build_peer_rules (S3 матрица/без-from/без-5432) → ○ prior-insert (peer ДО module-deny) → ○ stale-reconcile (delete from <ip>, ≥2 пира) → ○ verify (peer-ALLOW=PASS / Anywhere=FAIL) → ○ single-node no-op → ⎋ LDD IMP:9
# region MODULE_CONTRACT
## @purpose  Unit tests for peer-scoped firewall (DevPlan 010 T2.3/T2.4): core/internal/bootstrap/firewall.py
##           build_peer_rules/collect_stale_platform_rules/verify_firewall peer-семантика.
##           Native imports; placement из tests/fixtures/placement/ (T0.5); tmp_path для no-op-кейса.
## @scope    Tests: (a) S3 содержит allow from 10.8.0.13 → 6432 (apps→data, Acceptance W2), ни одного
##           правила без from, ни одного 5432; (b) peer-матрица включает 19000/9187/9121/3100/
##           9100/8080 (9113 — под ключом «nginx», co-location skip в S3); (c) stale-reconcile
##           delete-команды несут source IP при ≥2 пирах на порту (инвариант 4 — баг firewall.py:268);
##           (d) verify: peer-ALLOW от известного пира = PASS, Anywhere-публикация = FAIL;
##           (e) single-node (без placement) → пустой план.
## @invariants
##   - Pure functions — no subprocess, no Docker (native imports)
##   - Zero hardcoded paths: fixtures через Path(__file__) relative; no-op через tmp_path
##   - R5 anti-survivorship: (c) negative-тест на delete-форму без source (баг 268), (d) Anywhere=FAIL
##   - LDD: assert_ldd_imp9 (tests/helpers/gate_helpers) на success-path; failure-path — print-only
## @rationale T2.3/T2.4 acceptance (§9): peer-план S2/S3 содержит {6432,...,9121} + 9113 только
##            при split nginx↔monitoring (DR-H2 fix), НЕ содержит 5432 и Anywhere; delete-команды
##            stale-reconcile содержат `from <ip>`; single-node diff пуст.
## @changes 2026-08-22 · DevPlan 010 W2 T2.3/T2.4 — Created
# endregion MODULE_CONTRACT

import logging
from pathlib import Path

import pytest

from core.internal.bootstrap import firewall
from core.internal.shared.placement import load_placement
from tests.helpers.gate_helpers import assert_ldd_imp9

pytestmark = pytest.mark.static_audit

logger = logging.getLogger(__name__)

# ── Пути к фикстурам сценариев (T0.5): tests/fixtures/placement/ ────────────
_FIXTURES_DIR = Path(__file__).resolve().parent.parent / "fixtures" / "placement"

# S3-ноды: data-1=10.8.0.11, agent-1=10.8.0.12, apps-1=10.8.0.13
_S3_PEER_IPS = {"10.8.0.11", "10.8.0.12", "10.8.0.13"}


# region HELPERS


def _load_fixture(name: str):
    """Load a placement fixture (tests/fixtures/placement/<name>.yaml)."""
    placement = load_placement(_FIXTURES_DIR / f"{name}.yaml")
    assert placement is not None, f"{name}.yaml fixture must load"
    return placement


def _rules_cmds(rules: list[list[str]]) -> list[str]:
    """Join rule command lists into strings for membership assertions."""
    return [" ".join(r) for r in rules]


# endregion HELPERS


# region TEST_01_build_peer_rules_S3 (требование a)
# 🧪 TRAP[TEST] · 2026-08-22 · S3: allow from 10.8.0.13 → 6432 (apps→data) — Acceptance W2 DevPlan 010
# · Regression: если CONSUMER_OF/nginx-project-host маркер потеряется, apps-1 (хост проектов) теряет
# ·   доступ к pgbouncer — Acceptance W2 «ufw dry-run S3 показывает allow from 10.8.0.13 to port 6432»
# · Scenario: s3.yaml → build_peer_rules должен дать apps→data 6432, 0 правил без from, 0 портов 5432
# · Last fail: N/A — новый кейс W2 T2.3
# · Remove if: кросс-нодовый доступ проектов к pgbouncer перестанет быть сценарием (DevPlan 010 §8)
def test_build_peer_rules_s3_apps_to_pgbouncer(caplog: pytest.LogCaptureFixture) -> None:
    """build_peer_rules(s3): allow from 10.8.0.13 to 6432; без from-правил нет; 5432 отсутствует."""
    caplog.set_level(logging.DEBUG)

    rules = firewall.build_peer_rules(_load_fixture("s3"))
    cmds = _rules_cmds(rules)

    # (a-1) Acceptance W2: apps-1 (10.8.0.13) → pgbouncer 6432 на data-1
    assert "ufw allow from 10.8.0.13 to any port 6432/tcp comment platform-peer-6432-apps-1" in cmds, (
        f"S3 обязан содержать apps→data 6432 (Acceptance W2): {cmds}"
    )
    # (a-2) НИ ОДНО правило без from (Anywhere-публикация кросс-нодовых портов запрещена, инвариант 4)
    assert all("from" in c for c in cmds), f"каждое peer-правило обязано нести from <peer>: {cmds}"
    # (a-3) Прямой 5432 НЕ публикуется (потребители едут на data-ноду — DevPlan 010 §8)
    assert not any(" 5432" in c or "5432/tcp" in c for c in cmds), f"5432 не должен публиковаться: {cmds}"
    # (a-4) agent-1 (10.8.0.12) тоже потребитель pgbouncer (langfuse/litellm/hermes — CONSUMER_OF)
    assert "ufw allow from 10.8.0.12 to any port 6432/tcp comment platform-peer-6432-agent-1" in cmds

    logger.info(
        "[IMP:9][test_build_peer_rules_s3][assert] S3 peer-план: %d правил, apps→data 6432, 0 bare, 0 5432", len(rules)
    )
    assert_ldd_imp9(caplog)


# endregion TEST_01_build_peer_rules_S3


# region TEST_02_peer_matrix_ports (требование b)
# 🧪 TRAP[TEST] · 2026-08-22 · S3 peer-матрица §8: 19000/9187/9121/3100/9100/8080
# · Regression: если PEER_PUBLISH_PORTS потеряет порт (CH native 19000, exporter'ы, node-metrics),
# ·   открытие не генерируется — метрики/логи/аналитика кросс-нодово молча теряются
# · Scenario: s3.yaml → правила на ВСЕ порты матрицы §8 S3 (19000/9187/9121/3100/9100/8080)
# · Last fail: N/A — новый кейс W2 T2.4
# · Remove if: каноническая порт-матрица (DevPlan 010 §6.1 T2.2) пересмотрена
# · 9113 исключён из матрицы (DR-H2 fix): exporter в модуле nginx, co-located с monitoring
# ·   на apps-1 → локальный scrape без peer-правила (см. негативный тест ниже)
@pytest.mark.parametrize("port", [19000, 9187, 9121, 3100, 9100, 8080])
def test_build_peer_rules_s3_matrix_ports(caplog: pytest.LogCaptureFixture, port: int) -> None:
    """build_peer_rules(s3): матрица включает 19000/9187/9121/3100/9100/8080 (§8 S3)."""
    caplog.set_level(logging.DEBUG)

    rules = firewall.build_peer_rules(_load_fixture("s3"))
    cmds = _rules_cmds(rules)

    assert any(f"to any port {port}/tcp" in c for c in cmds), f"S3 peer-план обязан содержать порт {port} (§8): {cmds}"

    logger.info("[IMP:9][test_build_peer_rules_s3_matrix][assert] port %d in S3 peer-план", port)
    assert_ldd_imp9(caplog)


# endregion TEST_02_peer_matrix_ports


# region TEST_02b_nginx_exporter_local_scrape_negative
# 🧪 TRAP[TEST] · NEGATIVE (R5) · DR-H2 fix — 9113 co-location skip + consumer scoping
# · Last fail: аудит DevPlan 010 DR-H2 — старый код открывал 9113 data-1→apps-1 («ложный
#   peer-open»), тогда как nginx-exporter жил на data-1, а рендер таргетил apps-1: скрейп
#   был сломан во всех multi-node топологиях
# · Scenario: (a) s3.yaml НЕ порождает 9113-правил (nginx+monitoring co-located на apps-1);
#   (b) агент-нода БЕЗ потребителей (agent-1: hermes/litellm/langfuse) не получает правил
#   scrape-портов 9100/8080/9187/9121/9113; (c) топология с monitoring ОТДЕЛЬНО от nginx —
#   9113 открывается под ключом «nginx» (не service-exporters)
# · Remove if: nginx-exporter вернёт module-granularity размещение вне модуля nginx
def test_s3_no_9113_peer_rules_co_located(caplog: pytest.LogCaptureFixture) -> None:
    """S3: nginx+monitoring на одной ноде → 0 peer-правил 9113 (локальный Docker-DNS scrape)."""
    caplog.set_level(logging.DEBUG)

    cmds = _rules_cmds(firewall.build_peer_rules(_load_fixture("s3")))
    offenders = [c for c in cmds if "port 9113/tcp" in c]

    print("--- LDD TRAJECTORY ---")
    for c in cmds:
        if "exporter" in c or "9113" in c:
            print(c)
    print("--- END LDD TRAJECTORY ---")

    assert not offenders, f"S3: 9113 не должен публиковаться peer-правилом (co-location): {offenders}"
    logger.info("[IMP:9][test_s3_no_9113][assert] 0 правил 9113 в S3 (co-location skip)")
    assert_ldd_imp9(caplog)


def test_consumer_scoping_excludes_non_monitoring_peers(caplog: pytest.LogCaptureFixture) -> None:
    """Негативный scoping: agent-1 (без monitoring/nginx-потребителей) не получает scrape-портов."""
    caplog.set_level(logging.DEBUG)

    cmds = _rules_cmds(firewall.build_peer_rules(_load_fixture("s3")))
    scrape_ports = ("9100", "8080", "9187", "9121", "9113")
    offenders = [c for c in cmds if "-agent-1" in c and any(f"port {p}/tcp" in c for p in scrape_ports)]

    assert not offenders, f"agent-1 не потребитель scrape-портов, но получил правила: {offenders}"
    logger.info("[IMP:9][test_consumer_scoping][assert] agent-1 не получает 9100/8080/9187/9121/9113")
    assert_ldd_imp9(caplog)


def test_9113_follows_nginx_module_when_split(caplog: pytest.LogCaptureFixture, tmp_path: Path) -> None:
    """Топология с monitoring ОТДЕЛЬНО от nginx → 9113 открывается под ключом «nginx».

    DR-H2 fix regression: правило 9113 привязано к ноде, размещающей модуль nginx
    (exporter co-located), а не к ноде service-exporters.
    """
    caplog.set_level(logging.DEBUG)
    placement_yaml = tmp_path / "placement.yaml"
    placement_yaml.write_text(
        """\
context: split-lab
vpn_enforced: true
nodes:
  - name: data-1
    host: 10.8.0.11
  - name: apps-1
    host: 10.8.0.13
modules:
  postgres: { node: data-1 }
  redis: { node: data-1 }
  minio: { node: data-1 }
  clickhouse: { node: data-1 }
  backup-cron: { node: data-1 }
  service-exporters: { node: data-1 }
  platform-secrets: { node: data-1 }
  hermes-agent: { mode: "off" }
  litellm: { mode: "off" }
  langfuse: { mode: "off" }
  nginx: { node: apps-1 }
  status-page: { mode: "off" }
  monitoring: { node: data-1 }
  logging: { node: data-1 }
  log-collector: { mode: all-nodes }
  node-metrics: { mode: all-nodes }
""",
        encoding="utf-8",
    )
    placement = load_placement(placement_yaml)
    assert placement is not None

    cmds = _rules_cmds(firewall.build_peer_rules(placement))
    rule_9113 = "ufw allow from 10.8.0.11 to any port 9113/tcp comment platform-peer-9113-data-1"

    assert rule_9113 in cmds, f"monitoring(data-1) обязан скрейпить nginx-exporter(apps-1) по 9113: {cmds}"
    logger.info("[IMP:9][test_9113_split][assert] 9113 следует за модулем nginx (data-1→apps-1)")
    assert_ldd_imp9(caplog)


# endregion TEST_02b_nginx_exporter_local_scrape_negative


# region TEST_03_prior_insert_before_module_deny (инвариант 4 — ufw first-match)
# 🧪 TRAP[TEST] · 2026-08-22 · prior-insert: peer-ALLOW ПЕРЕД module-deny (ufw first-match)
# · Regression: если peer-правила вставятся ПОСЛЕ deny, deny выигрывает у allow — кросс-нодовый
# ·   трафик пиров режется собственным файрволом (первый-match ufw)
# · Scenario: build_rules(peer_rules=S3) → peer-allow 6379 идёт РАНЬШЕ deny 6379
# · Last fail: N/A — новый кейс W2 T2.3
# · Remove if: ufw first-match семантика перестанет быть каноном
def test_peer_rules_inserted_before_module_deny(caplog: pytest.LogCaptureFixture) -> None:
    """build_rules: peer-allow вставляется ПЕРЕД module-deny (ufw first-match, инвариант 4)."""
    caplog.set_level(logging.DEBUG)

    peer_rules = firewall.build_peer_rules(_load_fixture("s3"))
    rules = firewall.build_rules([], peer_rules=peer_rules)
    cmds = _rules_cmds(rules)

    peer_6379 = "ufw allow from 10.8.0.13 to any port 6379/tcp comment platform-peer-6379-apps-1"
    deny_6379 = "ufw deny 6379/tcp comment platform-module-deny"
    assert peer_6379 in cmds and deny_6379 in cmds
    assert cmds.index(peer_6379) < cmds.index(deny_6379), (
        "peer-allow обязан идти ДО module-deny (ufw first-match — иначе deny выигрывает)"
    )

    logger.info(
        "[IMP:9][test_peer_rules_inserted][assert] peer 6379 перед deny 6379 (%d < %d)",
        cmds.index(peer_6379),
        cmds.index(deny_6379),
    )
    assert_ldd_imp9(caplog)


# endregion TEST_03_prior_insert_before_module_deny


# region TEST_04_stale_reconcile_delete_carries_source (требование c, R5 — баг 268)
# 🧪 TRAP[TEST] · REGRESSION (R5) · DevPlan 010 инвариант 4 — delete-форма без source IP
# · Scenario: ≥2 пира на одном порту 6432 (10.8.0.12 + 10.8.0.13); bare `delete allow 6432/tcp`
# · · неоднозначна — ufw не знает, КАКОЕ правило удалить (старая форма firewall.py:268)
# · Last fail: 2026-08-22 — firewall.py:268 эмитил `["ufw","delete","allow","6432/tcp"]` без from
# · Remove if: ufw delete-математика сменится на полный spec-маппинг (правило → delete)
def test_stale_reconcile_delete_carries_source_two_peers(caplog: pytest.LogCaptureFixture) -> None:
    """collect_stale_platform_rules: при ≥2 пирах на порту КАЖДАЯ delete-команда несёт from <ip>."""
    caplog.set_level(logging.DEBUG)

    status = """Status: active
22/tcp ALLOW IN Anywhere  # platform-baseline
6432/tcp ALLOW IN 10.8.0.12  # platform-peer-6432-agent-1
6432/tcp ALLOW IN 10.8.0.13  # platform-peer-6432-apps-1
"""
    deletes = firewall.collect_stale_platform_rules(status, desired_allow={22, 80, 443})
    cmds = _rules_cmds(deletes)

    assert "ufw delete allow from 10.8.0.12 to any port 6432/tcp" in cmds, f"delete от первого пира: {cmds}"
    assert "ufw delete allow from 10.8.0.13 to any port 6432/tcp" in cmds, f"delete от второго пира: {cmds}"
    # R5 negative: голой формы `delete allow 6432/tcp` быть НЕ должно (баг 268)
    assert not any(c == "ufw delete allow 6432/tcp" for c in cmds), "delete без source IP неоднозначен (инвариант 4)"

    logger.info("[IMP:9][test_stale_reconcile_delete_carries_source][assert] обе delete-команды несут from <ip>")
    assert_ldd_imp9(caplog)


# 🧪 TRAP[TEST] · 2026-08-22 · Anywhere-baseline stale: bare delete сохраняется
# · Regression: baseline-правило (без from) должно удаляться голой формой — full-spec-перевод
# ·   не должен ломать Anywhere-правила (нет источника для переноса)
# · Scenario: stale platform-extra 8443 ANYWHERE → `ufw delete allow 8443/tcp` (без from)
# · Last fail: N/A — guard к full-spec delete-переводу (баг 268 фикс)
# · Remove if: baseline/Anywhere правила исчезнут из платформенного baseline
def test_stale_reconcile_delete_anywhere_keeps_bare_form(caplog: pytest.LogCaptureFixture) -> None:
    """collect_stale_platform_rules: Anywhere-правило (без from) удаляется голой delete-формой."""
    caplog.set_level(logging.DEBUG)

    status = """Status: active
22/tcp ALLOW IN Anywhere  # platform-baseline
8443/tcp ALLOW IN Anywhere  # platform-extra
"""
    deletes = firewall.collect_stale_platform_rules(status, desired_allow={22, 80, 443})
    cmds = _rules_cmds(deletes)

    assert "ufw delete allow 8443/tcp" in cmds, f"Anywhere-правило удаляется bare delete: {cmds}"

    logger.info("[IMP:9][test_stale_reconcile_delete_anywhere][assert] Anywhere stale → bare delete")
    assert_ldd_imp9(caplog)


# 🧪 TRAP[TEST] · 2026-08-22 · peer_ports: peer-матричные порты не stale при активном placement
# · Regression: без peer_ports stale-reconcile удалял бы peer-правила на следующем прогоне
# ·   (порт вне baseline-desired) → флап правила каждым apply (идемпотентность нарушена)
# · Scenario: статус содержит peer-правило 6432; collect_stale(..., peer_ports={6432}) → не удаляется
# · Last fail: N/A — новый кейс W2 T2.3
# · Remove if: lifecycle peer-правил перестанет управляться placement (v1)
def test_stale_reconcile_peer_ports_not_stale(caplog: pytest.LogCaptureFixture) -> None:
    """collect_stale_platform_rules(peer_ports=...): peer-матричный порт не удаляется (T2.3)."""
    caplog.set_level(logging.DEBUG)

    status = """Status: active
22/tcp ALLOW IN Anywhere  # platform-baseline
6432/tcp ALLOW IN 10.8.0.12  # platform-peer-6432-agent-1
"""
    deletes = firewall.collect_stale_platform_rules(status, desired_allow={22, 80, 443}, peer_ports={6432})
    assert deletes == [], "peer-матричный порт управляется placement — не должен удаляться baseline-reconcile"

    logger.info("[IMP:9][test_stale_reconcile_peer_ports_not_stale][assert] peer-порт вне stale-delete")
    assert_ldd_imp9(caplog)


# endregion TEST_04_stale_reconcile_delete_carries_source


# region TEST_05_verify_peer_semantics (требование d)
# 🧪 TRAP[TEST] · 2026-08-22 · verify: peer-ALLOW от известного пира = PASS (DevPlan 010 T2.3)
# · Regression: если verify продолжит трактовать 6432/6379 ALLOW как нарушение S-8, multi-node
# ·   прогон ложно-FAIL'ит на легитимных peer-открытиях
# · Scenario: 6432 ALLOW IN 10.8.0.12 (известный пир) + baseline + 5432 DENY → verify True
# · Last fail: N/A — новый кейс W2 T2.3
# · Remove if: peer-семантика verify перестанет быть каноном
def test_verify_firewall_peer_allow_from_known_peer_passes(caplog: pytest.LogCaptureFixture) -> None:
    """verify_firewall(peer_ips=...): peer-ALLOW от известного пира на матричном порту = PASS."""
    caplog.set_level(logging.DEBUG)

    status = """Status: active
22/tcp ALLOW IN Anywhere  # platform-baseline
80/tcp ALLOW IN Anywhere  # platform-baseline
443/tcp ALLOW IN Anywhere  # platform-baseline
10050/tcp ALLOW IN 92.53.116.12  # platform-zabbix
6432/tcp ALLOW IN 10.8.0.12  # platform-peer-6432-agent-1
5432/tcp DENY IN Anywhere  # explicit-deny-postgresql
6379/tcp DENY IN Anywhere  # platform-module-deny
"""
    assert firewall.verify_firewall(status, peer_ips=_S3_PEER_IPS) is True, "peer-ALLOW от известного пира = PASS"

    logger.info("[IMP:9][test_verify_peer_allow_passes][assert] peer-ALLOW 6432 от 10.8.0.12 → PASS")
    assert_ldd_imp9(caplog)


# 🧪 TRAP[TEST] · NEGATIVE (R5) · DevPlan 010 T2.3 — Anywhere-публикация на матричном порту
# · Scenario: 6432/tcp ALLOW IN Anywhere (без from) — кросс-нодовый порт открыт всему интернету
# · · (или 6379 ALLOW IN <неизвестный IP>) → verify обязан FAIL
# · Last fail: N/A — новый кейс W2 T2.3
# · Remove if: Anywhere-публикация кросс-нодовых портов станет разрешённой (инвариант 4 отменён)
def test_verify_firewall_anywhere_on_matrix_port_fails(caplog: pytest.LogCaptureFixture) -> None:
    """verify_firewall: Anywhere/неизвестный источник на кросс-нодовом порту = FAIL."""
    caplog.set_level(logging.INFO)

    # (d-1) Anywhere на 6432 (вне deny-реестра, peer-матричный) → FAIL
    status_anywhere = """Status: active
22/tcp ALLOW IN Anywhere  # platform-baseline
80/tcp ALLOW IN Anywhere  # platform-baseline
443/tcp ALLOW IN Anywhere  # platform-baseline
10050/tcp ALLOW IN 92.53.116.12  # platform-zabbix
6432/tcp ALLOW IN Anywhere  # user-opened
5432/tcp DENY IN Anywhere  # explicit-deny-postgresql
"""
    assert firewall.verify_firewall(status_anywhere, peer_ips=_S3_PEER_IPS) is False, (
        "Anywhere на 6432 обязан FAIL (инвариант 4)"
    )
    # (d-2) Неизвестный источник на 6379 (module-deny порт, peer-матричный) → FAIL
    status_unknown = """Status: active
22/tcp ALLOW IN Anywhere  # platform-baseline
80/tcp ALLOW IN Anywhere  # platform-baseline
443/tcp ALLOW IN Anywhere  # platform-baseline
10050/tcp ALLOW IN 92.53.116.12  # platform-zabbix
6379/tcp ALLOW IN 203.0.113.7  # attacker
5432/tcp DENY IN Anywhere  # explicit-deny-postgresql
"""
    assert firewall.verify_firewall(status_unknown, peer_ips=_S3_PEER_IPS) is False, (
        "ALLOW от неизвестного источника на peer-порту обязан FAIL"
    )
    assert any("[IMP:10]" in r.message for r in caplog.records), "FAIL обязан логировать IMP:10"


# 🧪 TRAP[TEST] · 2026-08-22 · verify: legacy без peer_ips (single-node) — peer-ALLOW = FAIL
# · Regression: single-node (без placement) не имеет известных пиров — любое ALLOW на module-deny
# ·   порту 6379 остаётся FAIL (легаси S-8 строгость не ослабляется)
# · Scenario: 6379 ALLOW IN 10.8.0.12, peer_ips=None (single-node) → FAIL
# · Last fail: N/A — guard к peer-семантике (T2.3)
# · Remove if: single-node перестанет быть каноном (DevPlan 010 §1.1)
def test_verify_firewall_no_peer_ips_keeps_legacy_strict(caplog: pytest.LogCaptureFixture) -> None:
    """verify_firewall без peer_ips: module-deny порт 6379 ALLOW остаётся FAIL (single-node legacy)."""
    caplog.set_level(logging.INFO)

    status = """Status: active
22/tcp ALLOW IN Anywhere  # platform-baseline
80/tcp ALLOW IN Anywhere  # platform-baseline
443/tcp ALLOW IN Anywhere  # platform-baseline
10050/tcp ALLOW IN 92.53.116.12  # platform-zabbix
6379/tcp ALLOW IN 10.8.0.12  # platform-peer-6379-agent-1
5432/tcp DENY IN Anywhere  # explicit-deny-postgresql
"""
    assert firewall.verify_firewall(status) is False, (
        "single-node (peer_ips=None): module-deny порт ALLOW обязан FAIL (S-8 легаси)"
    )


# endregion TEST_05_verify_peer_semantics


# region TEST_06_single_node_noop (требование e)
# 🧪 TRAP[TEST] · 2026-08-22 · single-node no-op (DevPlan 010 §1.1/§2.2 п.1)
# · Regression: если build_peer_rules упадёт на отсутствии placement, single-node контекст ломается
# · · (байт-совместимость — нет placement.yaml → легаси-поведение)
# · Scenario: build_peer_rules(None) → []; load_placement(tmp_path отсутствует) → None → []
# · Last fail: N/A — новый кейс W2 T2.3
# · Remove if: single-node legacy путь отменён
def test_build_peer_rules_single_node_noop(caplog: pytest.LogCaptureFixture, tmp_path: Path) -> None:
    """build_peer_rules: нет placement.yaml → пустой план правил (single-node no-op)."""
    caplog.set_level(logging.DEBUG)

    assert firewall.build_peer_rules(None) == [], "build_peer_rules(None) → [] (single-node)"

    missing = tmp_path / "placement.yaml"
    placement = load_placement(missing)
    assert placement is None, "missing placement.yaml → None"
    assert firewall.build_peer_rules(placement) == [], "build_peer_rules(None) → [] через load_placement"

    logger.info("[IMP:9][test_single_node_noop][assert] single-node: peer-план пуст")
    assert_ldd_imp9(caplog)


# 🧪 TRAP[TEST] · 2026-08-22 · run(placement_path=...) интеграция
# · Regression: если run() не прокидывает placement в build_rules, φ1-прогон с placement не применит
# ·   peer-правила (план применяется, но кросс-нодовые открытия отсутствуют)
# · Scenario: run(placement_path=s3.yaml, fake run_cmd) → в applied-командах есть platform-peer-*
# · Last fail: N/A — новый кейс W2 T2.3
# · Remove if: φ1-интеграция (lifecycle/phases/system.py) переедет на отдельный CLI-канал
def test_run_applies_peer_rules_with_placement(caplog: pytest.LogCaptureFixture) -> None:
    """run(placement_path=...): peer-правила попадают в применяемые ufw-команды."""
    caplog.set_level(logging.INFO)
    applied: list[list[str]] = []

    class FakeResult:
        returncode = 0
        stdout = ""
        stderr = ""

    def fake_run(cmd: list[str], **kwargs: object) -> FakeResult:
        applied.append(list(cmd))
        return FakeResult()

    # placement существует → peer-правила встроены; ufw status недоступен в тесте → verify=False,
    # но факт применения peer-правил проверяется по applied-командам (DI run_cmd канон W-H 163).
    result = firewall.run([], placement_path=str(_FIXTURES_DIR / "s3.yaml"), run_cmd=fake_run)
    assert result is False, "verify без ufw status → False (ожидаемо в тесте)"
    applied_cmds = _rules_cmds(applied)
    assert any("platform-peer-6432-apps-1" in c for c in applied_cmds), (
        f"peer-правило apps→data 6432 обязано попасть в apply: {applied_cmds[:3]}"
    )
    assert any("platform-peer-19000" in c for c in applied_cmds), "CH native 19000 в apply"


# endregion TEST_06_single_node_noop


# region TEST_07_s2_plan_openings (gate-аналог §9 test_peer_firewall_matrix_canonical)
# 🧪 TRAP[TEST] · 2026-08-22 · S2: матрица §8 S2 (первичный сценарий) — gate-аналог
# · Regression: если CONSUMER_OF/PEER_PUBLISH_PORTS деградируют, S2 теряет 6432/6379/9000/8123/19000/
# ·   3100/9100/8080/9187/9121 — gate §9 test_peer_firewall_matrix_canonical (build_rules(S2)) упадёт
# · Scenario: s2.yaml (data-1/main-1) → план содержит все порты §8 S2, ни одного 5432/Anywhere
# · Last fail: N/A — новый кейс W2 T2.3 (зеркалит gate-критерий §9)
# · Remove if: каноническая порт-матрица (T2.2) пересмотрена
def test_build_peer_rules_s2_plan_openings(caplog: pytest.LogCaptureFixture) -> None:
    """build_peer_rules(s2): §8 S2 открытия — {6432,6379,9000,8123,19000,3100,9100,8080,9187,9121}."""
    caplog.set_level(logging.DEBUG)

    rules = firewall.build_peer_rules(_load_fixture("s2"))
    cmds = _rules_cmds(rules)

    # S2: data-1=10.8.0.21, main-1=10.8.0.22 (единственный потребитель) — матрица §8 S2
    for port in (6432, 6379, 9000, 8123, 19000, 3100, 9100, 8080, 9187, 9121):
        assert any(f"to any port {port}/tcp" in c for c in cmds), f"S2 план обязан содержать {port}: {cmds}"
    assert all("from" in c for c in cmds), f"no Anywhere в S2: {cmds}"
    assert not any(" 5432" in c or "5432/tcp" in c for c in cmds), f"5432 не публикуется: {cmds}"
    # main-1 (10.8.0.22) — потребитель data-сервисов; data-1 (10.8.0.21) — потребитель loki-push
    assert "ufw allow from 10.8.0.22 to any port 6432/tcp comment platform-peer-6432-main-1" in cmds
    assert "ufw allow from 10.8.0.21 to any port 3100/tcp comment platform-peer-3100-data-1" in cmds

    logger.info("[IMP:9][test_build_peer_rules_s2][assert] S2 план: %d правил, все порты §8, 0 5432", len(rules))
    assert_ldd_imp9(caplog)


# endregion TEST_07_s2_plan_openings


# region TEST_08_facade_ports_s3 (TRAP[DECISION] completion 2026-08-24)
# 🧪 TRAP[TEST] · 2026-08-24 · REGRESSION · фасадные порты LLM-стека в S3 (§8 «4000/3001 для IP apps-1»)
# · Regression: если PEER_PUBLISH_PORTS потеряет litellm/langfuse/hermes-agent, проекты на
# ·   ingress-ноде теряют LLM/tracing (PLATFORM_LITELLM_URL), а nginx — hermes-dashboard upstream;
# ·   Acceptance W2 «hermes-dashboard.conf на apps-1 резолвит upstream agent-1» становится невыполнимым
# · Scenario: s3.yaml → allow from 10.8.0.13 to {4000,3001,9119} на agent-1; peer-scoped (from есть)
# · Last fail: N/A — фасады добавлены completion-фазой (TRAP[DECISION] в firewall.py)
# · Remove if: фасадные открытия признаны ложными и удалены из матрицы синхронно с этим тестом
def test_build_peer_rules_s3_facade_ports(caplog: pytest.LogCaptureFixture) -> None:
    """build_peer_rules(s3): litellm 4000 / langfuse 3001 / hermes 9119 открыты apps-1 (peer-scoped)."""
    caplog.set_level(logging.DEBUG)

    rules = firewall.build_peer_rules(_load_fixture("s3"))
    cmds = _rules_cmds(rules)

    for port in (4000, 3001, 9119):
        assert f"ufw allow from 10.8.0.13 to any port {port}/tcp comment platform-peer-{port}-apps-1" in cmds, (
            f"S3 обязан открывать фасад {port} для ingress-ноды (§8 S3): {cmds}"
        )
    assert all("from" in c for c in cmds), "фасадные правила тоже peer-scoped (инвариант 4)"

    logger.info("[IMP:9][test_build_peer_rules_s3_facade][assert] фасады 4000/3001/9119 → apps-1")
    assert_ldd_imp9(caplog)


# endregion TEST_08_facade_ports_s3


# region TEST_09_docker_user_peer_rules (DevPlan 16 T1.A)
def test_build_docker_user_peer_rules_post_dnat_ports(caplog: pytest.LogCaptureFixture) -> None:
    # 🧪 TRAP[TEST] · SCENARIO · DevPlan 16 T1.A P0-1 · DU peer-ACCEPT матчит POST-DNAT dport
    # · Regression: если билдер возьмёт host-порт вместо container (19000/3001), peer-ACCEPT
    #   «в никуда» — DOCKER-USER видит post-DNAT dport (9000/3000) и data-plane молча DROPается
    # · Scenario: s3.yaml → apps-1 → CH native --dport 9000 (host 19000!), langfuse UI 3000
    #   (host 3001!), pgbouncer 6432; комментарий platform-du-peer-<container>-<consumer>
    # · Last fail: аудит 15 P0-1 — DNAT'ed трафик мимо ufw, зелёный verify лгал о data-plane
    # · Remove if: DOCKER-USER peer-семантика отменена синхронно с test_docker_user_policy.py
    caplog.set_level(logging.DEBUG)

    du_rules = firewall.build_docker_user_peer_rules(_load_fixture("s3"))
    cmds = [" ".join(r) for r in du_rules]

    # Post-DNAT: host 19000 → container 9000; host 3001 → container 3000
    assert "-s 10.8.0.13 -p tcp --dport 9000 -j ACCEPT -m comment --comment platform-du-peer-9000-apps-1" in cmds, (
        f"CH native обязан матчить container 9000, не host 19000: {cmds}"
    )
    assert any("--dport 3000" in c and "10.8.0.13" in c for c in cmds), f"langfuse UI: 3001→3000: {cmds}"
    assert "-s 10.8.0.13 -p tcp --dport 6432 -j ACCEPT" in " ".join(cmds), "pgbouncer 6432→6432"
    # source=peer у КАЖДОГО правила (никогда Anywhere/RFC1918)
    assert all("-s 10.8." in c for c in cmds), f"все DU-peer правила source=peer: {cmds}"
    # single-node no-op
    assert firewall.build_docker_user_peer_rules(None) == []

    logger.info(
        "[IMP:9][test_build_du_peer][assert] %d DU-peer правил, post-dnat 9000/3000 подтверждены", len(du_rules)
    )
    assert_ldd_imp9(caplog)


def test_peer_dnat_parity_with_publish_matrix() -> None:
    # 🧪 TRAP[TEST] · REGRESSION · DevPlan 16 T1.A · host-порты PEER_DNAT_PAIRS == PEER_PUBLISH_PORTS
    # · Regression: дрейф матриц (порт добавлен в публикацию без DNAT-пары) → рантайм IMP:10-skip
    #   в build_docker_user_peer_rules и молча недостижимый data-plane
    # · Scenario: множества host-портов двух SoT-констант совпадают посервисно
    # · Last fail: N/A — новый кейс
    # · Remove if: матрицы консолидированы в одну структуру
    pub = {svc: set(ports) for svc, ports in firewall.PEER_PUBLISH_PORTS.items()}
    dnat_hosts: dict[str, set[int]] = {}
    for svc, pairs in firewall.PEER_DNAT_PAIRS.items():
        dnat_hosts[svc] = {h for h, _c in pairs}
        for _h, c in pairs:
            assert isinstance(c, int) and 0 < c < 65536, f"container-порт невалиден: {svc}"
    assert set(pub) <= set(dnat_hosts), f"сервис публикации без DNAT-пары: {set(pub) - set(dnat_hosts)}"
    for svc, ports in pub.items():
        assert dnat_hosts.get(svc) == ports, f"дрейф матриц по {svc}: publish={ports} dnat={dnat_hosts.get(svc)}"


# endregion TEST_09_docker_user_peer_rules


# region TEST_10_stale_peer_reconcile_self_heal (DevPlan 16 T1.A п.5 / P1-12)
def test_collect_stale_includes_peer_ports() -> None:
    # 🧪 TRAP[TEST] · NEGATIVE (R5) · DevPlan 16 T1.A P1-12 · стейл platform-peer-* удаляется
    # · Last fail: аудит 15 P1-12 — collect_stale_platform_rules пропускал peer-порты целиком,
    #   стейл копился, verify_firewall FAIL перманентно без self-heal
    # · Scenario: статус содержит валидную пару (10.8.0.13, 6432) и стейл (10.8.0.99, 6379):
    #   удаляется ТОЛЬКО стейл, full-spec формой `from <src>`
    # · Remove if: реконсиляция peer-правил перенесена в иной механизм
    status = (
        "6432/tcp                     ALLOW IN  10.8.0.13     # platform-peer-6432-apps-1\n"
        "6379/tcp                     ALLOW IN  10.8.0.99     # platform-peer-6379-gone\n"
    )
    desired = {("10.8.0.13", 6432)}
    deletes = firewall.collect_stale_peer_rules(status, desired)
    assert deletes == [["ufw", "delete", "allow", "from", "10.8.0.99", "to", "any", "port", "6379/tcp"]], deletes
    # Валидная пара сохраняется при повторном прогоне (идемпотентность)
    assert firewall.collect_stale_peer_rules("6432/tcp ALLOW IN 10.8.0.13 # platform-peer-6432-apps-1\n", desired) == []


def test_verify_firewall_expected_peer_absence_fails() -> None:
    # 🧪 TRAP[TEST] · NEGATIVE (R5) · DevPlan 16 T1.A п.4 · отсутствие ожидаемого peer-ALLOW = FAIL
    # · Last fail: аудит 15 P0-1/P1-12 — verify был зелёным при отсутствующих peer-правилах
    # · Scenario: expected={(10.8.0.13, 6432)}, в статусе порта нет → FAIL; правило появилось → PASS
    # · Remove if: absence-детект встроен иначе
    status_no_peer = (
        "Status: active\n"
        + "".join(f"{p}/tcp                     ALLOW IN  Anywhere\n" for p in (22, 80, 443))
        + "5432/tcp                     DENY IN   Anywhere\n"
    )
    expected = {("10.8.0.13", 6432)}
    assert (
        firewall.verify_firewall(
            status_no_peer, zabbix_monitoring=False, peer_ips={"10.8.0.13"}, expected_peer_allows=expected
        )
        is False
    ), "отсутствующий peer-ALLOW обязан ронять verify (data-plane недостижим)"
    status_with_peer = (
        status_no_peer + "6432/tcp                     ALLOW IN  10.8.0.13     # platform-peer-6432-apps-1\n"
    )
    assert (
        firewall.verify_firewall(
            status_with_peer, zabbix_monitoring=False, peer_ips={"10.8.0.13"}, expected_peer_allows=expected
        )
        is True
    )


# endregion TEST_10_stale_peer_reconcile_self_heal


# region TEST_11_run_docker_user_convergence (DevPlan 16 T1.A п.3-4)
def _scripted_runner(responses: dict[str, object]):
    """Fake runner: rc по подстроке команды; stdout из responses (подстрока → значение)."""

    class _R:
        def __init__(self, rc: int, out: str = "") -> None:
            self.returncode = rc
            self.stdout = out
            self.stderr = ""

    def fake(cmd, **kwargs):
        joined = " ".join(str(x) for x in cmd)
        for needle, spec in responses.items():
            if needle in joined:
                if isinstance(spec, tuple):
                    return _R(spec[0], spec[1])
                return _R(int(spec))
        return _R(0)

    return fake


# 🧪 TRAP[TEST] · SCENARIO · DevPlan 16 T1.A п.3 · run() с placement конвергирует DOCKER-USER
# · Scenario: scripted runner — ufw ok, iptables -L rc0, -C rc1/-A rc0, iptables-save содержит
#   peer-line → run() True; verify факта прошёл (зелёный статус = живой data-plane)
# · Last fail: аудит 15 P0-1 — зелёный verify при мёртвом DU data-plane
# · Remove if: DU-конвергенция переезжает в отдельный пост-деплой verb
def test_run_converges_docker_user_with_peers(caplog, monkeypatch) -> None:
    caplog.set_level(logging.DEBUG)
    placement = _load_fixture("s3")
    du_rules = firewall.build_docker_user_peer_rules(placement)

    def _save_line(rule: list[str]) -> str:
        """Канонизация аргументной формы в формат iptables-save (/32, -m tcp)."""
        args = list(rule)
        i = args.index("-s")
        src = args[i + 1]
        if "/" not in src:
            src += "/32"
        args[i + 1] = src
        j = args.index("-p", i)
        args.insert(j + 1, "-m")
        args.insert(j + 2, "tcp")
        return "-A DOCKER-USER " + " ".join(args)

    base_lines = [
        "-A DOCKER-USER -m conntrack --ctstate ESTABLISHED,RELATED -j ACCEPT",
        "-A DOCKER-USER -p tcp -m tcp --dport 80 -j ACCEPT",
        "-A DOCKER-USER -p tcp -m tcp --dport 443 -j ACCEPT",
        "-A DOCKER-USER -s 172.16.0.0/12 -j ACCEPT",
        "-A DOCKER-USER -s 10.32.0.0/16 -j ACCEPT",
    ]
    peer_lines = [_save_line(r) for r in du_rules]
    save_text = (
        "*filter\n:DOCKER-USER - [0:0]\n" + "\n".join(base_lines + peer_lines) + "\n-A DOCKER-USER -j DROP\nCOMMIT\n"
    )
    peer_line = peer_lines[0]
    # ufw-статус тоже обязан нести фактические peer-ALLOW (verify_firewall
    # expected_peer_allows — DevPlan 16 T1.A п.4)
    ufw_lines = [
        "Status: active",
        "22/tcp                     ALLOW IN  Anywhere",
        "80/tcp                     ALLOW IN  Anywhere",
        "443/tcp                     ALLOW IN  Anywhere",
        "5432/tcp                   DENY IN   Anywhere",
        "10050/tcp                  ALLOW IN  92.53.116.12  # platform-zabbix",
    ]
    for cmd in firewall.build_peer_rules(placement):
        host, port = cmd[3], int(cmd[7].removesuffix("/tcp"))
        ufw_lines.append(
            f"{port}/tcp                     ALLOW IN  {host}     # platform-peer-{port}-{cmd[8].split('-', 2)[-1]}"
        )
    fake = _scripted_runner({
        "iptables -w -L": 0,
        "iptables-save": (0, save_text),
    })
    ufw_status_text = "\n".join(ufw_lines) + "\n"

    # ufw status вызывается напрямую через subprocess.run (не DI) — патчим точечно
    class _StatusRunner:
        def __call__(self, cmd, **_kwargs):
            if cmd[:2] == ["ufw", "status"]:
                return type("R", (), {"returncode": 0, "stdout": ufw_status_text, "stderr": ""})()
            return fake(cmd)

    monkeypatch.setattr(firewall.subprocess, "run", _StatusRunner())
    result = firewall.run([], placement_path=str(_FIXTURES_DIR / "s3.yaml"), run_cmd=fake)
    assert result is True, "конвергенция DU с корректным фактом обязана давать True"

    # R5-negative: факт БЕЗ peer-line (правила не применились) → run() False
    save_stale = save_text.replace(peer_line + "\n", "")
    fake_bad = _scripted_runner({
        "iptables -w -L": 0,
        "iptables-save": (0, save_stale),
        "ufw status": (0, "Status: active\n"),
    })
    assert firewall.run([], placement_path=str(_FIXTURES_DIR / "s3.yaml"), run_cmd=fake_bad) is False, (
        "verify факта обязан ронять run() при отсутствии peer-ACCEPT в DOCKER-USER"
    )
    logger.info("[IMP:9][run-du-converge][assert] apply+verify факта: green=живой data-plane")


# endregion TEST_11_run_docker_user_convergence
