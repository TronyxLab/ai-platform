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
