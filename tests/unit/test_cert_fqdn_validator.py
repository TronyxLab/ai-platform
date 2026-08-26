"""
# GREP_SUMMARY: test cert-fqdn-validator validate-cert-domain-fqdn traversal R5-negative orchestrate-entry register-project add-project SEC-0026 REF-0008
# STRUCTURE: ▶ validator negatives (../, single-label, bad TLD) → ◇ orchestrate_certs entry: ConfigValidationError ДО side-effects (R5)
#            → ◇ register_project / add_project fail-fast ⎋ IMP:9
# region MODULE_CONTRACT
## @purpose  Тесты FQDN-валидатора cert-pipeline (REF-0008 подпункт 6, SEC-0026): needs.domain без
##           валидации доходил до sink'ов (live/<domain>/ пути, S3-keys, reloadcmd под root) —
##           `../`-домен = path traversal/RCE. Валидатор применяется на register_project И
##           orchestrate_certs entry (fail-fast).
## @scope    shared/ssl_certs.validate_cert_domain_fqdn + entry-gates cert_orchestrator/
##           project_registry/node_yaml.projects. Правила идентичны vhost_renderer (TRAP[DECISION]
##           в ssl_certs — дублирование из-за import-linter cycle).
## @invariants
##   - Violation → ConfigValidationError; orchestrate_certs НЕ выполняет НИКАКИХ side-effects
##     (S3 check/download, issue subprocess не вызываются) — R5-семантика
##   - Все файлы в tmp_path (Zero Hardcode)
## @rationale SEC-0026 (HIGH·B5): условный persistent root-RCE через attacker-owned домен
## @changes  2026-08-24 | REF-0008 (meta-refactoring В2) — Created
# endregion MODULE_CONTRACT
"""

import logging
import sys
from pathlib import Path
from unittest.mock import MagicMock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent / "core" / "internal" / "bootstrap"))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

import cert_orchestrator as co
import pytest

from core.internal.shared.exceptions import ConfigValidationError
from core.internal.shared.node_yaml import NodeYaml, ProjectEntry
from core.internal.shared.ssl_certs import validate_cert_domain_fqdn
from tests._conftest.ldd import ldd_trajectory

pytestmark = pytest.mark.static_audit

logger = logging.getLogger(__name__)


# ═════════════════════════════════════════════════════════════════════════════
# region validate_cert_domain_fqdn — negatives + positives
# ═════════════════════════════════════════════════════════════════════════════


@pytest.mark.parametrize(
    "fqdn",
    [
        "../evil.com",  # path traversal (исходный вход класса SEC-0026)
        "..",  # чистый traversal
        "example.com/../..",  # traversal внутри labels
        "singlelabel",  # нет TLD
        "bad_label.example.com",  # underscore в label
        "-lead.example.com",  # leading hyphen
        "example.c0m",  # TLD с цифрой
        "example.com/path",  # slash-инъекция
    ],
)
def test_validate_fqdn_rejects(fqdn: str, caplog) -> None:
    """Негативы валидатора: traversal/injection/malformed → ConfigValidationError."""
    caplog.set_level(logging.INFO)
    with pytest.raises(ConfigValidationError):
        validate_cert_domain_fqdn(fqdn)
    logger.critical("[IMP:9][test] fqdn rejected: %s", fqdn)


@pytest.mark.parametrize("fqdn", ["tronyx.ru", "app.example.com", "a-b.example.co"])
def test_validate_fqdn_accepts(fqdn: str, caplog) -> None:
    """Позитивы: легитимные FQDN проходят (без raise)."""
    caplog.set_level(logging.INFO)
    assert validate_cert_domain_fqdn(fqdn) is None
    logger.critical("[IMP:9][test] fqdn accepted: %s", fqdn)


# endregion validate_cert_domain_fqdn — negatives + positives


# ═════════════════════════════════════════════════════════════════════════════
# region orchestrate_certs entry gate — R5 negative (side-effect free rejection)
# ═════════════════════════════════════════════════════════════════════════════


@ldd_trajectory
def test_orchestrate_entry_rejects_traversal_before_side_effects(caplog, tmp_path: Path) -> None:
    """R5 NEGATIVE (SEC-0026): `../`-домен отклоняется на entry ДО любых S3/issue side-effects.

    Исходная форма бага: needs.domain без валидации проходил в cert pipeline (root-RCE).
    Детектор = entry-gate; тест ломается, если гейт исчезнет или сместится за side-effects.
    """
    caplog.set_level(logging.INFO)
    mock_s3 = MagicMock()
    issue_script = tmp_path / "issue_cert.py"
    issue_script.write_text("#!/usr/bin/env python3\n", encoding="utf-8")

    with pytest.raises(ConfigValidationError):
        co.orchestrate_certs(
            ["../evil"],
            str(issue_script),
            s3_cache=mock_s3,
            runner=MagicMock(),
            environ={},
        )

    mock_s3.check_cert.assert_not_called()
    assert mock_s3.check_cert.call_count == 0, "S3 не должен опрашиваться при невалидном домене"
    mock_s3.download_cert.assert_not_called()
    logger.critical("[IMP:9][test] R5: traversal-домен отклонён ДО S3/issue side-effects")


@ldd_trajectory
def test_orchestrate_entry_accepts_valid_domains(caplog, tmp_path: Path) -> None:
    """Позитив: легитимные домены проходят entry-gate и обрабатываются штатно."""

    class _FactsAfterDownload:
        """fullchain.pem «появляется» после mock-download (паттерн test_cert_orchestrator)."""

        def path_isfile(self, path: object) -> bool:
            return "fullchain.pem" in str(path)

    caplog.set_level(logging.INFO)
    mock_s3 = MagicMock()
    mock_s3.check_cert.return_value = True
    mock_s3.download_cert.return_value = True

    result = co.orchestrate_certs(
        ["example.com"],
        str(tmp_path / "missing-issue.py"),
        cert_validity_fn=lambda *_, **__: False,
        validity_path=str(tmp_path / "live"),
        s3_cache=mock_s3,
        facts=_FactsAfterDownload(),
        environ={"S3_BUCKET": "b"},
    )

    assert result.restored == 1, f"валидный домен должен пройти gate: {result.to_dict()}"
    logger.critical("[IMP:9][test] entry-gate пропускает легитимный домен (restore-first работает)")


# endregion orchestrate_certs entry gate — R5 negative (side-effect free rejection)


# ═════════════════════════════════════════════════════════════════════════════
# region register_project / add_project fail-fast
# ═════════════════════════════════════════════════════════════════════════════


def _write_node_yaml(tmp_path: Path) -> Path:
    yaml_path = tmp_path / "node.yaml"
    yaml_path.write_text("domain: platform.example.com\nprojects: []\n", encoding="utf-8")
    return yaml_path


@ldd_trajectory
def test_register_project_rejects_invalid_domain(caplog, tmp_path: Path) -> None:
    """Невалидный домен → ConfigValidationError на chokepoint, node.yaml НЕ мутируется.

    AI-0058r (DevPlan 17 T6.4): register_project демонтирован вместе с CLI — тест
    перенацелен на NodeYaml.add_project (chokepoint ВСЕХ mutation-путей).
    """
    caplog.set_level(logging.INFO)
    yaml_path = _write_node_yaml(tmp_path)

    with pytest.raises(ConfigValidationError):
        NodeYaml(str(yaml_path)).add_project(ProjectEntry(name="myproj", repo="org/myproj", domain="../evil.com"))

    assert "projects: []" in yaml_path.read_text(encoding="utf-8"), "node.yaml не должен мутироваться"
    logger.critical("[IMP:9][test] traversal-домен отклонён на chokepoint до мутации")


@ldd_trajectory
def test_add_project_rejects_invalid_domain(caplog, tmp_path: Path) -> None:
    """NodeYaml.add_project (chokepoint всех mutation-путей): невалидный домен → ConfigValidationError."""
    caplog.set_level(logging.INFO)
    yaml_path = _write_node_yaml(tmp_path)

    with pytest.raises(ConfigValidationError):
        NodeYaml(str(yaml_path)).add_project(ProjectEntry(name="p", repo="o/p", domain="bad_underscore.example.com"))

    assert "projects: []" in yaml_path.read_text(encoding="utf-8"), "node.yaml не должен мутироваться"
    logger.critical("[IMP:9][test] add_project chokepoint fail-fast (до _write_back)")


@ldd_trajectory
def test_register_project_valid_domain_registers(caplog, tmp_path: Path) -> None:
    """Позитив: легитимный домен регистрируется штатно (гейт не ломает канал).

    AI-0058r (DevPlan 17 T6.4): перенацелен на NodeYaml.add_project chokepoint.
    """
    caplog.set_level(logging.INFO)
    yaml_path = _write_node_yaml(tmp_path)

    ny = NodeYaml(str(yaml_path))
    ny.add_project(ProjectEntry(name="myproj", repo="org/myproj", domain="app.example.com"))

    content = yaml_path.read_text(encoding="utf-8")
    assert "domain: app.example.com" in content
    logger.critical("[IMP:9][test] register с валидным доменом — канал регистрации работает")


# endregion register_project / add_project fail-fast
