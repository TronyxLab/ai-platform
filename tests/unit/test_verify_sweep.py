# GREP_SUMMARY: unit-test, verify-sweep, e2e-verify, collect-endpoints, classify-http, by-design, san-wildcard, expiry-threshold, r4-fail-not-skip, devplan-136
# STRUCTURE: ▶ T1 local collect (node.yaml + vhost conf) → ▶ T2 remote collect (ssh DI) → ▶ T3 R4 ssh-fail
#            → ▶ T4/T5 nginx parser → ▶ T6/T7 HTTP classify (by-design + allowlist) → ▶ T8/T9 SAN wildcard
#            → ▶ T10 expiry threshold → ▶ T11-T13 check_http → ▶ T14-T17 check_tls (ok/expired/san-mismatch/R4) → ⎋ 17 pass
# region MODULE_CONTRACT
## @purpose  Unit tests for core/internal/verify_sweep.py (DevPlan 136 W5 T5.5) — endpoint parser
##           (mock vhost_renderer/node.yaml via tmp_path), by-design HTTP classification, wildcard
##           SAN matching, expiry threshold, R4 semantics (FAIL, not skip), LDD IMP:9.
## @scope    Pure unit tests — native imports, DI-раннеры (curl_runner/s_client_runner/ssh_runner),
##           NO real subprocess/network. W3.5-4: SAN/expiry/issuer хелперы — DI-параметрами
##           check_tls (cert_san_matches_fn/cert_days_left_fn/cert_check_expiry_fn/cert_is_le_issuer_fn).
## @invariants
##   - DI: curl_runner/s_client_runner/ssh_runner инжектятся (паттерн vps_readiness)
##   - Каждый тест: @ldd_trajectory + # 🧪 TRAP[TEST] (R1/R2 честность); маркер unit снят (177 W2.2)
##   - R5 negative: точные входы, которые должны быть отвергнуты (wildcard apex/deep, 502/504,
##     server_name_in_redirect substring, ssh rc!=0) — ANTI-SURVIVORSHIP
##   - tmp_path вместо hardcoded путей; node.yaml — через NodeYaml 3-path (platform_root=tmp_path)
## @rationale DevPlan 136 §5.5 $TEST_SPEC: парсер списка, классификация кодов (incl. by-design),
##            wildcard SAN-матчинг, expiry-порог, R5-negative, caplog IMP:9.
## @changes  2026-08-05 | DevPlan 136 W5 — Created (T5.5)
# endregion MODULE_CONTRACT

import logging
import subprocess

import pytest

from core.internal import verify_sweep
from core.internal.verify_sweep import (
    Endpoint,
    EndpointCollectionError,
    check_http,
    check_tls,
    classify_http_code,
    collect_endpoints,
    expiry_verdict,
    parse_nginx_server_names,
    san_matches_domain,
)
from tests.conftest import ldd_trajectory

logger = logging.getLogger(__name__)

NODE = "test-node"
HOST = "10.0.0.5"


def _fake_completed(stdout: str, returncode: int = 0) -> subprocess.CompletedProcess:
    """Собрать CompletedProcess для DI-раннеров (curl/s_client).

    ▶ ┌stdout, returncode┐ → ⊕ subprocess.CompletedProcess(args=[], ...) → ⎋ cp
    @invariants — args пустой (раннеры диспетчеризуются по вызову, не по args)
    """
    return subprocess.CompletedProcess(args=[], returncode=returncode, stdout=stdout, stderr="")


def _write_node_yaml(root, projects: list[dict]) -> str:
    """Записать node.yaml в <root>/node-configs/<NODE>/node.yaml (3-path канон).

    ▶ ┌root, projects┐ → ⊕ mkdir + YAML write → ⎋ str (путь к node.yaml)
    @invariants — node.host = HOST; projects из аргумента
    """
    node_dir = root / "node-configs" / NODE
    node_dir.mkdir(parents=True, exist_ok=True)
    yaml_path = node_dir / "node.yaml"
    lines = ["node:", f"  name: {NODE}", f"  host: {HOST}", "projects:"]
    for p in projects:
        lines.append(f"  - name: {p['name']}")
        if p.get("domain"):
            lines.append(f"    domain: {p['domain']}")
        if p.get("type"):
            lines.append(f"    type: {p['type']}")
    yaml_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return str(yaml_path)


# region FUNC_test_collect_local_projects_and_vhosts
## @purpose — T1: local collect объединяет node.yaml projects (domain) + overlays/nginx server_names,
##            дедуплицирует по fqdn, host из node.host (AC W5: collect_endpoints)
## @io — ⇥ tmp_path → ⎋ None (asserts endpoints)
## @complexity — O(1)
@ldd_trajectory
# 🧪 TRAP[TEST] · 2026-08-05 · REGRESSION · local endpoint collection path (DevPlan 136 §5.5 T5.5)
# · Last fail: N/A (new module)
# · Remove if: collect_endpoints local-mode sources are reworked
def test_collect_local_projects_and_vhosts(caplog, tmp_path, monkeypatch) -> None:
    """Local mode: node.yaml projects + overlay vhost server_names, dedup by fqdn."""
    caplog.set_level(logging.DEBUG)
    monkeypatch.delenv("NODE_HOST_MAP", raising=False)
    _write_node_yaml(
        tmp_path,
        [
            {"name": "site-a", "domain": "site-a.example.com"},
            {"name": "site-b", "domain": "site-b.example.com"},
            {"name": "no-domain", "type": "backend"},
        ],
    )
    overlays = tmp_path / "node-configs" / NODE / "overlays" / "nginx"
    overlays.mkdir(parents=True, exist_ok=True)
    (overlays / "site-c.example.com.conf").write_text(
        "server { listen 443 ssl; server_name site-c.example.com; }\n", encoding="utf-8"
    )
    # Дубликат fqdn site-a — должен дедуплицироваться (первый источник выигрывает)
    (overlays / "dup.example.com.conf").write_text(
        "server { listen 443 ssl; server_name site-a.example.com; }\n", encoding="utf-8"
    )

    logger.info("[IMP:7][test] T1: local collect scenario")
    eps = collect_endpoints(
        NODE, mode="local", node_configs_dir=str(tmp_path / "node-configs"), platform_root=str(tmp_path)
    )

    by_fqdn = {e.fqdn: e for e in eps}
    assert len(eps) == 3, f"Expected 3 unique endpoints, got {[e.fqdn for e in eps]}"
    assert by_fqdn["site-a.example.com"].source == "node-yaml"
    assert by_fqdn["site-b.example.com"].source == "node-yaml"
    assert by_fqdn["site-c.example.com"].source == "vhost-conf"
    assert all(e.host == HOST for e in eps), "host must come from node.yaml#node.host"
    logger.info("[IMP:9][test] T1 PASS: 3 endpoints collected (2 node-yaml + 1 vhost-conf), dedup OK")


# endregion FUNC_test_collect_local_projects_and_vhosts


# region FUNC_test_collect_remote_via_ssh
## @purpose — T2: remote collect читает nginx conf.d через ssh_runner DI (conf-парсинг)
## @io — ⇥ tmp_path + DI ssh_runner → ⎋ None (asserts endpoint source/name)
## @complexity — O(1)
@ldd_trajectory
# 🧪 TRAP[TEST] · 2026-08-05 · REGRESSION · remote collection path (DevPlan 136 T5.1 remote)
# · Last fail: N/A (new module)
# · Remove if: collect_endpoints remote-mode SSH read is reworked
def test_collect_remote_via_ssh(caplog, tmp_path, monkeypatch) -> None:
    """Remote mode: SSH cat conf.d → parse server_names → endpoints (source=remote-nginx)."""
    caplog.set_level(logging.DEBUG)
    monkeypatch.delenv("NODE_HOST_MAP", raising=False)
    _write_node_yaml(tmp_path, [{"name": "site-a", "domain": "site-a.example.com"}])

    def fake_ssh(host: str, user: str, cmd: str, timeout: int) -> tuple[int, str]:
        # DevPlan 153 T5 (N2): дефолт remote_conf_dir резолвится из имени ноды —
        # /opt/node-configs/<node>/overlays/nginx (путь на хосте, не внутри контейнера)
        assert f"cat /opt/node-configs/{NODE}/overlays/nginx/*.conf" in cmd
        return 0, (
            "server { listen 443 ssl; server_name api.example.com; }\n"
            "server { listen 443 ssl; server_name admin.example.com; }\n"
        )

    logger.info("[IMP:7][test] T2: remote collect scenario")
    eps = collect_endpoints(
        NODE,
        mode="remote",
        platform_root=str(tmp_path),
        ssh_runner=fake_ssh,
    )
    fqdns = sorted(e.fqdn for e in eps)
    assert fqdns == ["admin.example.com", "api.example.com"], f"Got {fqdns}"
    assert all(e.source == "remote-nginx" for e in eps)
    assert all(e.host == HOST for e in eps)
    logger.info("[IMP:9][test] T2 PASS: 2 remote endpoints via SSH conf.d read")


# endregion FUNC_test_collect_remote_via_ssh


# region FUNC_test_collect_remote_ssh_unavailable_fails_r4
## @purpose — T3: R4-negative — ssh-недоступен → EndpointCollectionError(exit 1), НЕ skip
## @io — ⇥ tmp_path + DI ssh_runner rc=1 → ⎋ None (asserts raise + exit_code 1)
## @complexity — O(1)
@ldd_trajectory
# 🧪 TRAP[TEST] · NEGATIVE (R5) · R4 ssh-unavailable — «нет ноды/ssh-недоступен → FAIL, не skip» (DevPlan 136 T5.1)
# · Last fail: R4 нарушение — молчаливый skip при недоступном SSH
# · Remove if: R4-семантика remote-collect отменяется
def test_collect_remote_ssh_unavailable_fails_r4(caplog, tmp_path, monkeypatch) -> None:
    """R4: SSH unavailable in remote mode → EndpointCollectionError(exit_code=1) — FAIL, not skip."""
    caplog.set_level(logging.DEBUG)
    monkeypatch.delenv("NODE_HOST_MAP", raising=False)
    _write_node_yaml(tmp_path, [{"name": "site-a", "domain": "site-a.example.com"}])

    def failing_ssh(host: str, user: str, cmd: str, timeout: int) -> tuple[int, str]:
        return 1, "ssh: Connection refused"

    logger.info("[IMP:7][test] T3: R4 ssh-unavailable scenario")
    with pytest.raises(EndpointCollectionError) as exc_info:
        collect_endpoints(NODE, mode="remote", platform_root=str(tmp_path), ssh_runner=failing_ssh)
    assert exc_info.value.exit_code == 1, "SSH unavailable must be operational FAIL (exit 1), not config (2)"
    assert "R4" in str(exc_info.value), "Error message must reference R4 semantics"
    logger.info("[IMP:9][test] T3 PASS: ssh rc!=0 → EndpointCollectionError exit 1 (R4 FAIL, not skip)")


# endregion FUNC_test_collect_remote_ssh_unavailable_fails_r4


# region FUNC_test_parse_nginx_server_names
## @purpose — T4: pure-парсер server_name — несколько имён, dedup, '_' ignore, lowercase
## @io — ⇥ conf-текст → ⎋ None (asserts список)
## @complexity — O(1)
# GUARD-PRESERVE (168): единственное happy-path покрытие parse_nginx_server_names (multi-name/dedup/lowercase/underscore-ignore)
@ldd_trajectory
# 🧪 TRAP[TEST] · 2026-08-05 · REGRESSION · nginx server_name parser (DevPlan 136 T5.5)
# · Last fail: N/A (new module)
# · Remove if: parse_nginx_server_names reworked
def test_parse_nginx_server_names(caplog) -> None:
    """server_name directives: 443-блоки, multiple names, dedup, '_' ignored, lowercase."""
    caplog.set_level(logging.DEBUG)
    conf = (
        "server { listen 80; server_name Example.COM; }\n"
        "server { listen 443 ssl; server_name api.example.com admin.example.com; }\n"
        "server { listen 443 ssl; server_name _; }\n"
    )
    logger.info("[IMP:7][test] T4: parser scenario")
    names = parse_nginx_server_names(conf)
    assert names == ["api.example.com", "admin.example.com"], f"Got {names}"
    logger.info("[IMP:9][test] T4 PASS: 2 unique lowercase names из 443-блоков, '_' ignored")


# 🧪 TRAP[TEST] · 2026-08-16 · NEGATIVE (R5) · релиз 1.0.0 — port-80-only vhost не endpoint
# · Scenario: apex-заглушка (listen 80 redirect, без 443) не попадает в HTTPS-свип
# ·   (asiteam.ru 444-stealth давал вечный e2e FAIL на минимальных нодах).
# · Remove if: sweep начнёт проверять HTTP-эндпоинты
@ldd_trajectory
def test_parse_nginx_server_names_port80_excluded(caplog) -> None:
    """R5-negative (1.0.0): server_name из listen-80-only блока НЕ извлекается."""
    conf = (
        "server { listen 80; listen [::]:80; server_name apex.example.com; "
        "location / { return 301 https://$host$request_uri; } }\n"
        "server { listen 443 ssl; server_name real.example.com; }\n"
    )
    names = parse_nginx_server_names(conf)
    assert names == ["real.example.com"], f"Got {names}"


# endregion FUNC_test_parse_nginx_server_names


# region FUNC_test_parse_nginx_server_names_negative_substring
## @purpose — T5: R5-negative — server_name_in_redirect (substring) НЕ должен матчиться
## @io — ⇥ conf с server_name_in_redirect → ⎋ None (asserts пусто)
## @complexity — O(1)
# GUARD-PRESERVE (168): R5-negative (anti-survivorship) — server_name_in_redirect substring trap (точный вход бага ^-якорного regex)
@ldd_trajectory
# 🧪 TRAP[TEST] · NEGATIVE (R5) · server_name-in-redirect substring trap — точный вход, ломавший ^-якорный regex
# · Last fail: ^-anchored regex пропускал server_name после первой директивы (multi-match на одной строке)
# · Remove if: parse_nginx_server_names regex changes anchor strategy
def test_parse_nginx_server_names_negative_substring(caplog) -> None:
    """R5 negative: server_name_in_redirect directive must NOT be parsed as a server_name."""
    caplog.set_level(logging.DEBUG)
    conf = "server { server_name_in_redirect on; listen 443 ssl; server_name real.example.com; }\n"
    logger.info("[IMP:7][test] T5: substring-trap scenario")
    names = parse_nginx_server_names(conf)
    assert names == ["real.example.com"], f"server_name_in_redirect must not match, got {names}"
    logger.info("[IMP:9][test] T5 PASS: server_name_in_redirect ignored")


# endregion FUNC_test_parse_nginx_server_names_negative_substring


# region FUNC_test_classify_http_codes_by_design
## @purpose — T6: by-design классификация кодов (200 OK, 301/302 redirect, 401/403 auth,
##            404/444 deny → pass; 502/504/5xx → fail)
## @io — ⇥ parametrize коды → ⎋ None (asserts вердикт)
## @complexity — O(1)
@ldd_trajectory
@pytest.mark.parametrize(
    "code,expected_verdict",
    [
        (200, "pass"),
        (301, "pass"),
        (302, "pass"),
        (401, "pass"),
        (403, "pass"),
        (404, "pass"),
        (444, "pass"),
        (502, "fail"),
        (504, "fail"),
        (500, "fail"),
        (503, "fail"),
    ],
)
# 🧪 TRAP[TEST] · 2026-08-05 · REGRESSION · by-design expected_codes classification (DevPlan 136 T5.1)
# · Last fail: N/A (new module; риск ложных FAIL на живой ноде — DevPlan 136 §9)
# · Remove if: classify_http_code by-design mapping changes
def test_classify_http_codes_by_design(caplog, code: int, expected_verdict: str) -> None:
    """HTTP code → by-design verdict (pass for auth/deny/redirect, fail for upstream errors)."""
    caplog.set_level(logging.DEBUG)
    logger.info("[IMP:7][test] T6: classify code=%d expected=%s", code, expected_verdict)
    assert classify_http_code(code) == expected_verdict
    logger.info("[IMP:9][test] T6 PASS: %d → %s", code, expected_verdict)


# endregion FUNC_test_classify_http_codes_by_design


# region FUNC_test_classify_http_expected_allowlist
## @purpose — T7: per-endpoint expected allowlist переопределяет by-design набор
## @io — ⇥ parametrize (code, expected) → ⎋ None (asserts вердикт)
## @complexity — O(1)
@ldd_trajectory
# 🧪 TRAP[TEST] · 2026-08-05 · REGRESSION · per-endpoint expected allowlist (DevPlan 136 T5.1)
# · Last fail: N/A (new module)
# · Remove if: expected-codes allowlist overrides are removed
def test_classify_http_expected_allowlist(caplog) -> None:
    """expected allowlist: строгий — вне списка → fail, даже 200."""
    caplog.set_level(logging.DEBUG)
    logger.info("[IMP:7][test] T7: allowlist scenario")
    assert classify_http_code(200, expected=[301]) == "fail"
    assert classify_http_code(301, expected=[200, 301]) == "pass"
    logger.info("[IMP:9][test] T7 PASS: allowlist strict semantics")


# endregion FUNC_test_classify_http_expected_allowlist


# region FUNC_test_san_wildcard_matching
## @purpose — T8: wildcard SAN-матчинг — exact, *.wildcard один уровень
## @io — ⇥ parametrize (fqdn, san, expected) → ⎋ None (asserts bool)
## @complexity — O(1)
# GUARD-PRESERVE (168): R5-пара (anti-survivorship) с test_san_negative_wildcard_boundaries — позитивные wildcard-кейсы
@ldd_trajectory
@pytest.mark.parametrize(
    "fqdn,san,expected",
    [
        ("api.example.com", "DNS:api.example.com", True),
        ("api.example.com", "DNS:*.example.com", True),
        ("example.com", "DNS:example.com", True),
        ("EXAMPLE.com", "DNS:example.com", True),
    ],
)
# 🧪 TRAP[TEST] · 2026-08-05 · REGRESSION · wildcard SAN matching positive cases (DevPlan 136 T5.5)
# · Last fail: N/A (new module)
# · Remove if: san_matches_domain semantics change
def test_san_wildcard_matching(caplog, fqdn: str, san: str, expected: bool) -> None:
    """Positive SAN matching: exact and one-level wildcard (case-insensitive)."""
    caplog.set_level(logging.DEBUG)
    logger.info("[IMP:7][test] T8: san %s vs fqdn %s", san, fqdn)
    assert san_matches_domain(fqdn, san) is expected
    logger.info("[IMP:9][test] T8 PASS: %s covers %s", san, fqdn)


# endregion FUNC_test_san_wildcard_matching


# region FUNC_test_san_negative_wildcard_boundaries
## @purpose — T9: R5-negative — wildcard НЕ покрывает apex, глубокие уровни, другие домены
## @io — ⇥ parametrize (fqdn, san) → ⎋ None (asserts False)
## @complexity — O(1)
# GUARD-PRESERVE (168): R5-negative (anti-survivorship) — wildcard SAN boundary rejection (apex/deep/foreign/empty)
@ldd_trajectory
@pytest.mark.parametrize(
    "fqdn,san",
    [
        # Точный вход бага-класса: wildcard не должен матчить apex
        ("example.com", "DNS:*.example.com"),
        # deep (двухуровневый) НЕ покрывается одноуровневым wildcard
        ("deep.api.example.com", "DNS:*.example.com"),
        # чужой домен не матчится
        ("evil-tronyx.ru", "DNS:*.tronyx.ru"),
        # невалидный wildcard (не первый сегмент)
        ("api.example.com", "DNS:foo.*.com"),
        ("", "DNS:*.example.com"),
        ("api.example.com", ""),
    ],
)
# 🧪 TRAP[TEST] · NEGATIVE (R5) · wildcard SAN boundary rejection — точные входы, ломавшие бы over-match
# · Last fail: гипотетический over-match: *.example.com ложно покрывал example.com (apex) / deep
# · Remove if: san_matches_domain wildcard semantics change
def test_san_negative_wildcard_boundaries(caplog, fqdn: str, san: str) -> None:
    """R5 negative: wildcard SAN must NOT cover apex, deeper levels, foreign domains, empty."""
    caplog.set_level(logging.DEBUG)
    logger.info("[IMP:7][test] T9: san %s vs fqdn %s", san, fqdn)
    assert san_matches_domain(fqdn, san) is False
    logger.info("[IMP:9][test] T9 PASS: %s does NOT cover %s", san, fqdn)


# endregion FUNC_test_san_negative_wildcard_boundaries


# region FUNC_test_expiry_verdict_threshold
## @purpose — T10: expiry-порог — ok ≥14д, warn <14д, fail при истечении и непарсируемой дате
## @io — ⇥ parametrize (days_left, verdict) → ⎋ None (asserts вердикт)
## @complexity — O(1)
@ldd_trajectory
@pytest.mark.parametrize(
    "days_left,expected",
    [
        (20, "ok"),
        (14, "ok"),
        (13, "warn"),
        (0, "warn"),
        (-1, "fail"),
        (None, "fail"),
    ],
)
# 🧪 TRAP[TEST] · 2026-08-05 · REGRESSION · expiry threshold WARN<14d/FAIL (DevPlan 136 T5.1)
# · Last fail: N/A (new module)
# · Remove if: EXPIRY_WARN_DAYS threshold logic changes
def test_expiry_verdict_threshold(caplog, days_left: int | None, expected: str) -> None:
    """Expiry threshold: >=14d ok, <14d warn, <0 expired fail, None unparseable fail."""
    caplog.set_level(logging.DEBUG)
    logger.info("[IMP:7][test] T10: days_left=%s expected=%s", days_left, expected)
    assert expiry_verdict(days_left) == expected
    logger.info("[IMP:9][test] T10 PASS: %r → %s", days_left, expected)


# endregion FUNC_test_expiry_verdict_threshold


# region FUNC_test_check_http_ok_and_fail
## @purpose — T11: check_http через curl_runner DI — 200 → ok; connection error → fail (R4)
## @io — ⇥ DI curl_runner → ⎋ None (asserts HttpResult)
## @complexity — O(1)
@ldd_trajectory
# 🧪 TRAP[TEST] · 2026-08-05 · REGRESSION · check_http curl DI path (DevPlan 136 T5.1)
# · Last fail: N/A (new module)
# · Remove if: check_http runner DI reworked
def test_check_http_ok(caplog) -> None:
    """HTTP 200 via curl_runner DI → ok verdict, code=200."""
    caplog.set_level(logging.DEBUG)
    ep = Endpoint(name="site-a", fqdn="site-a.example.com", host=HOST)

    def curl_runner(cmd: list[str], timeout: int) -> subprocess.CompletedProcess:
        assert "--resolve" in cmd and "site-a.example.com:443:10.0.0.5" in cmd
        return _fake_completed("200")

    logger.info("[IMP:7][test] T11: check_http ok scenario")
    result = check_http(ep, curl_runner=curl_runner)
    assert result.code == 200
    assert result.ok is True and result.verdict == "pass"
    logger.info("[IMP:9][test] T11 PASS: HTTP 200 → pass")


# endregion FUNC_test_check_http_ok


# region FUNC_test_check_http_connection_error_fails_r4
## @purpose — T12: R4-negative — connection error (curl rc!=0) → fail, НЕ skip
## @io — ⇥ DI curl_runner rc=7 → ⎋ None (asserts fail + error)
## @complexity — O(1)
@ldd_trajectory
# 🧪 TRAP[TEST] · NEGATIVE (R5) · connection error → FAIL (R4) — точный вход: curl exit 7
# · Last fail: R4 нарушение — connection error молчаливо skipped
# · Remove if: R4 semantics for check_http connection errors change
def test_check_http_connection_error_fails_r4(caplog) -> None:
    """R4: curl connection error (exit 7) → verdict fail, never skip."""
    caplog.set_level(logging.DEBUG)
    ep = Endpoint(name="site-a", fqdn="site-a.example.com", host=HOST)

    def curl_runner(cmd: list[str], timeout: int) -> subprocess.CompletedProcess:
        return _fake_completed("", returncode=7)

    logger.info("[IMP:7][test] T12: connection-error scenario")
    result = check_http(ep, curl_runner=curl_runner)
    assert result.ok is False and result.verdict == "fail"
    assert result.code is None and "curl exit 7" in (result.error or "")
    logger.info("[IMP:9][test] T12 PASS: curl rc=7 → fail (R4, not skip)")


# endregion FUNC_test_check_http_connection_error_fails_r4


# region FUNC_test_check_http_502_fails
## @purpose — T13: by-design FAIL код 502 → fail (R5-negative: upstream error не pass)
## @io — ⇥ DI curl_runner stdout=502 → ⎋ None (asserts fail)
## @complexity — O(1)
# GUARD-PRESERVE (168): R5-negative — HTTP 502 upstream error must FAIL (TRAP[TEST] NEGATIVE R5, DevPlan 136 §9)
@ldd_trajectory
# 🧪 TRAP[TEST] · NEGATIVE (R5) · 502 upstream error must FAIL — точный вход DevPlan 136 §9
# · Last fail: N/A (new module; риск: 502 классифицирован как by-design pass)
# · Remove if: 502/504 removed from FAIL classification
def test_check_http_502_fails(caplog) -> None:
    """R5 negative: HTTP 502 (upstream error) → fail — no legal by-design status."""
    caplog.set_level(logging.DEBUG)
    ep = Endpoint(name="site-a", fqdn="site-a.example.com", host=HOST)

    def curl_runner(cmd: list[str], timeout: int) -> subprocess.CompletedProcess:
        return _fake_completed("502")

    logger.info("[IMP:7][test] T13: 502 scenario")
    result = check_http(ep, curl_runner=curl_runner)
    assert result.ok is False and result.verdict == "fail" and result.code == 502
    logger.info("[IMP:9][test] T13 PASS: HTTP 502 → fail")


# endregion FUNC_test_check_http_502_fails


# region FUNC_test_check_tls_verdict_ok
## @purpose — T14: check_tls через s_client_runner DI + helper DI (SAN/expiry) — полный ok
## @io — ⇥ DI s_client_runner + helper DI → ⎋ None (asserts chain/san/days/verdict)
## @complexity — O(1)
@ldd_trajectory
# 🧪 TRAP[TEST] · 2026-08-05 · REGRESSION · check_tls happy path (DevPlan 136 T5.1)
# · Last fail: N/A (new module)
# · Remove if: check_tls verdict composition reworked
def test_check_tls_verdict_ok(caplog) -> None:
    """TLS ok: chain=2, SAN match, days_left=20 → verdict ok (SAN+expiry+chain green)."""
    caplog.set_level(logging.DEBUG)
    ep = Endpoint(name="site-a", fqdn="site-a.example.com", host=HOST)
    s_client_out = (
        "CONNECTED(00000003)\n"
        "-----BEGIN CERTIFICATE-----\nMIIBfakeleaf\n-----END CERTIFICATE-----\n"
        "-----BEGIN CERTIFICATE-----\nMIIBfakeinter\n-----END CERTIFICATE-----\n"
    )

    def s_client_runner(cmd: list[str], timeout: int) -> subprocess.CompletedProcess:
        assert "site-a.example.com" in cmd and f"{HOST}:443" in cmd
        return _fake_completed(s_client_out)

    logger.info("[IMP:7][test] T14: TLS ok scenario")
    result = check_tls(
        ep,
        s_client_runner=s_client_runner,
        cert_san_matches_fn=lambda *_, **__: True,
        cert_days_left_fn=lambda *_, **__: 20,
        cert_check_expiry_fn=lambda *_, **__: True,
        cert_is_le_issuer_fn=lambda *_, **__: True,
    )
    assert result.chain_depth == 2
    assert result.san_ok is True
    assert result.days_left == 20
    assert result.verdict == "ok" and result.ok is True
    logger.info("[IMP:9][test] T14 PASS: chain=2, SAN ok, 20d left → ok")


# endregion FUNC_test_check_tls_verdict_ok


# region FUNC_test_check_tls_expired_fails
## @purpose — T15: R5-negative — сертификат истёк (days_left=-1) → fail
## @io — ⇥ DI + cert_days_left_fn=-1 → ⎋ None (asserts fail)
## @complexity — O(1)
@ldd_trajectory
# 🧪 TRAP[TEST] · NEGATIVE (R5) · expired cert must FAIL — точный вход: notAfter в прошлом
# · Last fail: N/A (new module; риск: expired классифицирован как WARN)
# · Remove if: expired-certs removed from FAIL classification
def test_check_tls_expired_fails(caplog) -> None:
    """R5 negative: expired cert (days_left=-1) → verdict fail."""
    caplog.set_level(logging.DEBUG)
    ep = Endpoint(name="site-a", fqdn="site-a.example.com", host=HOST)
    s_client_out = "-----BEGIN CERTIFICATE-----\nMIIBfakeleaf\n-----END CERTIFICATE-----\n"

    def s_client_runner(cmd: list[str], timeout: int) -> subprocess.CompletedProcess:
        return _fake_completed(s_client_out)

    logger.info("[IMP:7][test] T15: expired-cert scenario")
    result = check_tls(
        ep,
        s_client_runner=s_client_runner,
        cert_san_matches_fn=lambda *_, **__: True,
        cert_days_left_fn=lambda *_, **__: -1,
        cert_check_expiry_fn=lambda *_, **__: False,
        cert_is_le_issuer_fn=lambda *_, **__: True,
    )
    assert result.verdict == "fail" and result.ok is False
    assert result.days_left == -1
    logger.info("[IMP:9][test] T15 PASS: expired cert → fail")


# endregion FUNC_test_check_tls_expired_fails


# region FUNC_test_check_tls_san_mismatch_fails
## @purpose — T16: R5-negative — SAN не покрывает fqdn → fail
## @io — ⇥ DI + cert_san_matches_fn=False → ⎋ None (asserts fail)
## @complexity — O(1)
@ldd_trajectory
# 🧪 TRAP[TEST] · NEGATIVE (R5) · SAN mismatch must FAIL — точный вход: сертификат чужого домена
# · Last fail: N/A (new module; риск: mismatch классифицирован как ok по chain только)
# · Remove if: SAN-match removed from check_tls FAIL conditions
def test_check_tls_san_mismatch_fails(caplog) -> None:
    """R5 negative: SAN does not cover fqdn → verdict fail (even with valid expiry)."""
    caplog.set_level(logging.DEBUG)
    ep = Endpoint(name="site-a", fqdn="site-a.example.com", host=HOST)
    s_client_out = "-----BEGIN CERTIFICATE-----\nMIIBfakeleaf\n-----END CERTIFICATE-----\n"

    def s_client_runner(cmd: list[str], timeout: int) -> subprocess.CompletedProcess:
        return _fake_completed(s_client_out)

    logger.info("[IMP:7][test] T16: SAN-mismatch scenario")
    result = check_tls(
        ep,
        s_client_runner=s_client_runner,
        cert_san_matches_fn=lambda *_, **__: False,
        cert_days_left_fn=lambda *_, **__: 30,
        cert_check_expiry_fn=lambda *_, **__: True,
        cert_is_le_issuer_fn=lambda *_, **__: True,
    )
    assert result.verdict == "fail" and result.ok is False
    assert result.san_ok is False
    logger.info("[IMP:9][test] T16 PASS: SAN mismatch → fail")


# endregion FUNC_test_check_tls_san_mismatch_fails


# region FUNC_test_check_tls_handshake_error_fails_r4
## @purpose — T17: R4-negative — TLS handshake fail (openssl rc!=0) → fail, НЕ skip
## @io — ⇥ DI s_client_runner rc=1 → ⎋ None (asserts fail + error)
## @complexity — O(1)
@ldd_trajectory
# 🧪 TRAP[TEST] · NEGATIVE (R5) · TLS handshake failure → FAIL (R4) — точный вход: openssl exit 1
# · Last fail: R4 нарушение — handshake error молчаливо skipped
# · Remove if: R4 semantics for check_tls handshake errors change
def test_check_tls_handshake_error_fails_r4(caplog) -> None:
    """R4: openssl s_client failure (exit 1) → verdict fail with error, never skip."""
    caplog.set_level(logging.DEBUG)
    ep = Endpoint(name="site-a", fqdn="site-a.example.com", host=HOST)

    def s_client_runner(cmd: list[str], timeout: int) -> subprocess.CompletedProcess:
        return _fake_completed("connect: Connection refused", returncode=1)

    logger.info("[IMP:7][test] T17: handshake-error scenario")
    result = check_tls(ep, s_client_runner=s_client_runner)
    assert result.ok is False and result.verdict == "fail"
    assert "handshake" in (result.error or "").lower()
    logger.info("[IMP:9][test] T17 PASS: openssl rc=1 → fail (R4, not skip)")


# endregion FUNC_test_check_tls_handshake_error_fails_r4


# region FUNC_test_default_remote_conf_dir_resolves
# GUARD-PRESERVE (168): единственное прямое покрытие _default_remote_nginx_conf_dir (DevPlan 153 T5 N2)
# 🧪 TRAP[TEST] · Regression · дефолт remote_conf_dir резолвится из имени ноды (DevPlan 153 T5, N2)
# · Scenario: _default_remote_nginx_conf_dir("tronyx-vps") → /opt/node-configs/tronyx-vps/overlays/nginx
# · Last fail: RC-прогон 2026-08-12 — дефолт /etc/nginx/conf.d/overlay (путь внутри контейнера) (N2)
# · Remove if: remote collect меняется на другой источник vhost-конфигов
def test_default_remote_conf_dir_resolves(caplog) -> None:
    """Default remote nginx conf dir must resolve from node name (host path)."""
    caplog.set_level(logging.DEBUG)
    conf_dir = verify_sweep._default_remote_nginx_conf_dir("tronyx-vps")
    assert conf_dir == "/opt/node-configs/tronyx-vps/overlays/nginx", f"Got {conf_dir}"
    logger.info("[IMP:9][test] T18 PASS: default remote_conf_dir=%s", conf_dir)


# endregion FUNC_test_default_remote_conf_dir_resolves
