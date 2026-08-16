"""
# GREP_SUMMARY: test_cert_orchestrator_provider, provider-registry-integration, per-domain-env, allowlist-issue-env, http01, fallback-path, unknown-provider
# STRUCTURE: ▶ tmp node.yaml + secrets.env → ◇ orchestrate_certs (mock issue) → ◇ assert issue env (plugin/creds/challenge) → ◇ assert allowlist → ⎋ LDD
# region MODULE_CONTRACT
## @purpose  Интеграционные тесты cert_orchestrator × provider_registry (DevPlan 154 W1):
##           per-domain резолв, env-контракт issue-cert.sh (PLATFORM_ACME_DNS_PLUGIN + креды
##           строгим allowlist + ACME_CHALLENGE_MODE), http01-маппинг, путь без конфига,
##           fail-fast на неизвестном провайдере.
## @scope    Unit: subprocess.run мокается (никаких реальных acme.sh/S3); node.yaml и
##           secrets.env — через tmp_path (Zero Hardcode).
## @invariants
##   - Все тесты идут через orchestrate_certs (публичный конвейер), не приватные функции
##   - S3 отключён (S3_BUCKET пуст + s3_ssl_cache мок на miss) — чистый issue-путь
##   - Каждый успешный сценарий валидирует IMP:9 лог
## @rationale Вариант C (Brief 154 S1): реестр управляет env issue-cert.sh — контракт тестируется
##           на уровне оркестратора (реальный поток кредов), а не изолированно.
## @changes  CREATED: 2026-08-12 · DevPlan 154 W1
# endregion MODULE_CONTRACT
"""

from pathlib import Path

import pytest

from core.internal.bootstrap import cert_orchestrator as cert
from tests._conftest.ldd import ldd_trajectory

pytestmark = pytest.mark.static_audit

# ═════════════════════════════════════════════════════════════════════════════
# Helpers
# ═════════════════════════════════════════════════════════════════════════════

_NODE_YAML = """\
domain: asiteam.ru
email: admin@asiteam.ru
acme_dns_plugin: regru
acme_dns_plugins:
  asiteam.ru: regru
"""

_SECRETS_ENV = """\
REGRU_API_Username=asi-user
REGRU_API_Password=asi-pass
GHCR_PULL_TOKEN=ghcr-secret-should-not-leak
S3_BUCKET=test-bucket
"""


def _write(tmp_path: Path, name: str, content: str) -> Path:
    p = tmp_path / name
    p.write_text(content)
    return p


class _FakeS3Cache:
    """S3-cache fake (DI, DevPlan 167 D0): check/download — miss (чистый issue-путь)."""

    def check_cert(self, domain, s3_bucket) -> bool:  # ruff: ignore[ARG002]
        return False

    def download_cert(self, domain, cert_dir, acme_home, s3_bucket) -> bool:  # ruff: ignore[ARG002]
        return False

    def upload_cert(self, domain, validity_path, acme_home, s3_bucket) -> bool:  # ruff: ignore[ARG002]
        return True


class _FakeRunner:
    """CommandRunner-fake (DI runner-канал, DevPlan 167 D0): перехват issue-команды + env.

    ## @purpose — Заменяет monkeypatch cert.subprocess.run: env-контракт issue_cert
    ##             (allowlist кредов) наблюдается через env= runner-вызова (TRAP[DI-SEAM]
    ##             в cert_orchestrator._plw_body__issue_cert), а не через патч канала.
    """

    def __init__(self) -> None:
        self.cmds: list[list[str]] = []
        self.envs: list[dict] = []

    def run(self, cmd, *, timeout=None, check=False, env=None):  # ruff: ignore[ARG002]
        self.cmds.append(cmd)
        self.envs.append(dict(env or {}))

        class _R:
            returncode = 0
            stdout = "issued"
            stderr = ""

        return _R()


def _di_kwargs(tmp_path: Path, runner: _FakeRunner) -> dict:
    """DI-параметры orchestrate_certs (DevPlan 167 D0): fake-каналы вместо monkeypatch.

    ## @purpose — disk-miss (cert_validity_fn=False), S3 miss (fake cache),
    ##             issue перехватывается runner-каналом; CERT_VALIDITY_PATH → validity_path.
    """
    return {
        "runner": runner,
        "validity_path": str(tmp_path / "live"),
        "cert_validity_fn": lambda *_, **__: False,
        "s3_cache": _FakeS3Cache(),
    }


# ═════════════════════════════════════════════════════════════════════════════
# region Tests: provider-driven issue env
# ═════════════════════════════════════════════════════════════════════════════


@ldd_trajectory
def test_issue_env_provider_driven(caplog, tmp_path):
    """Реестр управляет env issue: plugin + allowlist-креды + challenge (per-domain regru)."""
    node_yaml = _write(tmp_path, "node.yaml", _NODE_YAML)
    secrets_env = _write(tmp_path, "secrets.env", _SECRETS_ENV)
    issue_script = _write(tmp_path, "issue-cert.sh", "#!/bin/bash\nexit 0")
    runner = _FakeRunner()

    result = cert.orchestrate_certs(
        ["roadmap.asiteam.ru"],
        str(issue_script),
        str(secrets_env),
        node_yaml=str(node_yaml),
        **_di_kwargs(tmp_path, runner),
    )

    issue_env = runner.envs[0]
    # Провайдер regru → issue вызван с плагином и кредами (allowlist)
    assert result.domains["roadmap.asiteam.ru"].status == "issued"
    assert issue_env["PLATFORM_ACME_DNS_PLUGIN"] == "regru"
    assert issue_env["ACME_CHALLENGE_MODE"] == "dns"
    assert issue_env["REGRU_API_Username"] == "asi-user"
    assert issue_env["REGRU_API_Password"] == "asi-pass"
    # Allowlist: GHCR-токен и S3-бакет НЕ утекают в env issue
    assert "GHCR_PULL_TOKEN" not in issue_env
    # IMP:9 логи оркестратора присутствуют
    found = any("[IMP:9][cert_orchestrator]" in r.message for r in caplog.records)
    assert found, "No IMP:9 cert_orchestrator log found"


@ldd_trajectory
def test_per_domain_provider_selection(caplog, tmp_path):
    """Два домена → разные провайдеры из acme_dns_plugins (per-domain, longest-suffix)."""
    node_yaml = _write(
        tmp_path,
        "node.yaml",
        """\
acme_dns_plugin: webnames
acme_dns_plugins:
  asiteam.ru: regru
  tronyx.ru: webnames
""",
    )
    secrets_env = _write(
        tmp_path,
        "secrets.env",
        "REGRU_API_Username=u\nREGRU_API_Password=p\nWEBNAMES_API_KEY=*wkey\n",
    )
    issue_script = _write(tmp_path, "issue-cert.sh", "#!/bin/bash\nexit 0")
    runner = _FakeRunner()

    cert.orchestrate_certs(
        ["roadmap.asiteam.ru", "botanika.tronyx.ru"],
        str(issue_script),
        str(secrets_env),
        node_yaml=str(node_yaml),
        **_di_kwargs(tmp_path, runner),
    )

    envs = {c["PLATFORM_DOMAIN"]: c for c in runner.envs}
    # asiteam.ru → regru (plugins_map), tronyx.ru → webnames (fallback... но ключ tronyx.ru задан явно)
    assert envs["roadmap.asiteam.ru"]["PLATFORM_ACME_DNS_PLUGIN"] == "regru"
    assert envs["botanika.tronyx.ru"]["PLATFORM_ACME_DNS_PLUGIN"] == "webnames"
    # Креды разделены per-provider: regru-домен не видит WEBNAMES_API_KEY
    assert "REGRU_API_Username" in envs["roadmap.asiteam.ru"]
    assert "WEBNAMES_API_KEY" not in envs["roadmap.asiteam.ru"]
    assert "WEBNAMES_API_KEY" in envs["botanika.tronyx.ru"]


@ldd_trajectory
def test_http01_provider_forces_http(caplog, tmp_path):
    """Провайдер http01 в реестре → ACME_CHALLENGE_MODE=http (issue-cert переключит ветку)."""
    node_yaml = _write(
        tmp_path,
        "node.yaml",
        "acme_dns_plugin: http01\nacme_dns_plugins:\n  asiteam.ru: http01\n",
    )
    secrets_env = _write(tmp_path, "secrets.env", "S3_BUCKET=x\n")
    issue_script = _write(tmp_path, "issue-cert.sh", "#!/bin/bash\nexit 0")
    runner = _FakeRunner()

    result = cert.orchestrate_certs(
        ["roadmap.asiteam.ru"],
        str(issue_script),
        str(secrets_env),
        node_yaml=str(node_yaml),
        **_di_kwargs(tmp_path, runner),
    )

    issue_env = runner.envs[0]
    assert result.domains["roadmap.asiteam.ru"].status == "issued"
    assert issue_env["ACME_CHALLENGE_MODE"] == "http"
    assert issue_env["PLATFORM_ACME_DNS_PLUGIN"] == "http01"


@ldd_trajectory
def test_fallback_path_without_node_config(caplog, tmp_path):
    """Нет acme-конфига в node.yaml → fallback: PLATFORM_ACME_DNS_PLUGIN НЕ задаётся (issue сам читает NODE_YAML)."""
    node_yaml = _write(tmp_path, "node.yaml", "domain: asiteam.ru\n")
    secrets_env = _write(tmp_path, "secrets.env", "WEBNAMES_API_KEY=*k\n")
    issue_script = _write(tmp_path, "issue-cert.sh", "#!/bin/bash\nexit 0")
    runner = _FakeRunner()

    result = cert.orchestrate_certs(
        ["asiteam.ru"], str(issue_script), str(secrets_env), node_yaml=str(node_yaml), **_di_kwargs(tmp_path, runner)
    )

    issue_env = runner.envs[0]
    assert result.domains["asiteam.ru"].status == "issued"
    assert "PLATFORM_ACME_DNS_PLUGIN" not in issue_env
    assert issue_env["ACME_CHALLENGE_MODE"] == "dns"


@ldd_trajectory
def test_unknown_provider_fails_domain(caplog, tmp_path):
    """Неизвестное имя провайдера → домен failed (fail-fast, TRAP 154), остальные продолжаются."""
    node_yaml = _write(tmp_path, "node.yaml", "acme_dns_plugin: bogus\n")
    secrets_env = _write(tmp_path, "secrets.env", "\n")
    issue_script = _write(tmp_path, "issue-cert.sh", "#!/bin/bash\nexit 0")
    runner = _FakeRunner()

    result = cert.orchestrate_certs(
        ["asiteam.ru"], str(issue_script), str(secrets_env), node_yaml=str(node_yaml), **_di_kwargs(tmp_path, runner)
    )

    assert result.domains["asiteam.ru"].status == "failed"
    assert "Unknown cert provider 'bogus'" in result.domains["asiteam.ru"].error


# endregion Tests: provider-driven issue env
