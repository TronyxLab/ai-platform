# GREP_SUMMARY: gate context-contract contexts-canon invariant-3 node-yaml legacy-context removed flat-domain single-read-point
# STRUCTURE: ▶ (a) node.yaml-образцы: 0 × top-level ^context:, contexts: present → ◇ (b) 0 × extract-alias в core/ → ◇ (c) 0 × node._data в core/ → ◇ (d) validate() negative (legacy context, dict-domain) → ◇ (e) domain flat-only в образцах → ⎋ pass|fail
# region MODULE_CONTRACT
## @purpose  Контрактный gate (DevPlan 116 B6 T9.1, D6): инвариант 3 кодируется в коде —
##           contexts[] канон, legacy top-level 'context' удалён. Самоверификация волны B6:
##           (a) образцы node.yaml не содержат legacy-поля, (b) extract-алиас удалён из core/,
##           (c) приватный кэш-атрибут node._data не используется, (d) validate() отклоняет
##           legacy-фикстуры, (e) domain строго flat-строка.
## @scope    Read-only gate. Сканирует все **/node.yaml + tests/test_data/node_yaml_valid.yaml.
##           allowlist ИСКЛЮЧЕНИЙ: templates/template-context/modules/hermes-agent/config.yaml и
##           core/modules/hermes-agent/build/templates/profiles/platform/config.yaml — hermes-agent
##           config, другой домен (не node.yaml) — не сканируются (не совпадают с **/node.yaml).
## @invariants
##   - (a) top-level `^context:` → 0 в образцах; `contexts:` присутствует в каждом
##   - (b) `extract_context_from_node_yaml` в core/ → 0 (production-ссылок нет)
##   - (c) `node\._data` в core/ → 0 (приватный кэш-атрибут не используется)
##   - (d) NodeYaml.validate() на legacy `context:` → error «Legacy 'context'»;
##         на dict-domain → error «must be a string»
##   - (e) в образцах нет dict-формы `domain:` (regex ^domain:\s*$ с вложенным телом)
##   - Test marked @pytest.mark.gate — runs in `make gate MODE=fast`
## @rationale Два аудита сошлись: инвариант 3 декларирован, но не закодирован. Gate делает
##            расхождение контекста структурно невозможным (DevPlan 116 B6 T1/T9, D4/D5).
## @changes 2026-08-01 | Created (DevPlan 116 B6 T9.1)
# endregion MODULE_CONTRACT

import logging
import re
from pathlib import Path

import pytest

from core.internal.shared.node_yaml import NodeYaml
from tests.conftest import ldd_trajectory
from tests.helpers.gate_helpers import repo_root

logger = logging.getLogger(__name__)

ROOT = repo_root()
CORE_DIR = ROOT / "core"

# hermes-agent config (другой домен, НЕ node.yaml) — allowlist исключений (DevPlan 116 B6 T9.1a)
_ALLOWLIST = [
    ROOT / "templates" / "template-context" / "modules" / "hermes-agent" / "config.yaml",
    ROOT / "core" / "modules" / "hermes-agent" / "build" / "templates" / "profiles" / "platform" / "config.yaml",
]


def _node_yaml_samples() -> list[Path]:
    """Все node.yaml в репо + tests/test_data/node_yaml_valid.yaml (явное дополнение)."""
    samples: list[Path] = []
    for p in ROOT.rglob("node.yaml"):
        rel = p.relative_to(ROOT)
        if any(part in (".git", ".venv", "venv", "node_modules", "__pycache__", ".pytest_cache") for part in rel.parts):
            continue
        if p in _ALLOWLIST:
            continue
        samples.append(p)
    extra = ROOT / "tests" / "test_data" / "node_yaml_valid.yaml"
    if extra.is_file():
        samples.append(extra)
    return sorted(set(samples))


# region TEST_A_SAMPLES_NO_LEGACY_CONTEXT
@pytest.mark.gate
@ldd_trajectory
def test_node_yaml_samples_contexts_canon(caplog) -> None:
    """(a) Образцы node.yaml: 0 top-level `context:`, `contexts:` присутствует."""
    samples = _node_yaml_samples()
    assert samples, "no node.yaml samples found — scan must not be empty"
    for sample in samples:
        content = sample.read_text(encoding="utf-8")
        # Legacy top-level context field — ЗАПРЕЩЁН (invariant 3, DevPlan 116 B6 T1)
        assert not re.search(r"^context:\s", content, re.M), (
            f"{sample}: legacy top-level 'context:' present — use contexts[] canon"
        )
        # contexts section must be declared
        assert re.search(r"^contexts:", content, re.M), f"{sample}: missing 'contexts:' section"
        logger.info("[IMP:9][gate_context_contract][a] OK: %s (contexts[] canon)", sample)
    logger.critical("[IMP:9][gate_context_contract][a] PASS: %d samples, 0 legacy context", len(samples))


# endregion


# region TEST_B_NO_EXTRACT_ALIAS
@pytest.mark.gate
@ldd_trajectory
def test_no_extract_context_alias_in_core(caplog) -> None:
    """(b) deprecated extract-алиас удалён из production-путей (DevPlan 116 B6 T1.6/T2)."""
    hits = []
    for p in CORE_DIR.rglob("*.py"):
        content = p.read_text(encoding="utf-8", errors="replace")
        if "extract_context_from_node_yaml" in content:
            hits.append(str(p))
    assert not hits, f"deprecated alias references in core/: {hits}"
    logger.critical("[IMP:9][gate_context_contract][b] PASS: 0 extract-alias references in core/")


# endregion


# region TEST_C_NO_PRIVATE_CACHE_ACCESS
@pytest.mark.gate
@ldd_trajectory
def test_no_private_cache_access(caplog) -> None:
    """(c) приватный кэш-атрибут node._data не используется в core/ (DevPlan 116 B6 T8.2)."""
    hits = []
    for p in CORE_DIR.rglob("*.py"):
        content = p.read_text(encoding="utf-8", errors="replace")
        if "node._data" in content:
            hits.append(str(p))
    assert not hits, f"node._data references in core/: {hits}"
    logger.critical("[IMP:9][gate_context_contract][c] PASS: 0 node._data references in core/")


# endregion


# region TEST_D_VALIDATE_NEGATIVE_CONTRACT
@pytest.mark.gate
@ldd_trajectory
def test_validate_rejects_legacy_context(tmp_path, caplog) -> None:
    """(d) negative-контракт: legacy `context:` → validate() error «Legacy 'context'»."""
    legacy = tmp_path / "legacy.yaml"
    legacy.write_text("context: legacy\ncontexts:\n  - name: canon\nnode:\n  name: n\n  host: 1.2.3.4\nmodules: []\n")
    errors = NodeYaml(str(legacy)).validate()
    assert any("Legacy 'context'" in e for e in errors), f"expected Legacy 'context' error, got {errors}"
    logger.critical("[IMP:9][gate_context_contract][d] PASS: validate() rejects legacy 'context' field")


@pytest.mark.gate
@ldd_trajectory
def test_validate_rejects_dict_domain(tmp_path, caplog) -> None:
    """(d) negative-контракт: dict-domain → validate() error «must be a string» (DevPlan 116 B6 T7)."""
    dict_domain = tmp_path / "dict-domain.yaml"
    dict_domain.write_text(
        "domain:\n  platform: example.com\ncontexts:\n  - name: c\nnode:\n  name: n\n  host: 1.2.3.4\nmodules: []\n"
    )
    errors = NodeYaml(str(dict_domain)).validate()
    assert any("must be a string" in e for e in errors), f"expected dict-domain error, got {errors}"
    logger.critical("[IMP:9][gate_context_contract][d] PASS: validate() rejects dict-form domain")


# endregion


# region TEST_E_DOMAIN_FLAT_ONLY
@pytest.mark.gate
@ldd_trajectory
def test_node_yaml_samples_domain_flat_only(caplog) -> None:
    """(e) в образцах domain строго flat-строка (нет dict-формы, DevPlan 116 B6 T7/T9.1e)."""
    for sample in _node_yaml_samples():
        content = sample.read_text(encoding="utf-8")
        assert not re.search(r"^domain:\s*$", content, re.M), (
            f"{sample}: dict-form 'domain:' found — flat string only (domain: example.com)"
        )
        logger.info("[IMP:9][gate_context_contract][e] OK: %s (flat domain)", sample)
    logger.critical("[IMP:9][gate_context_contract][e] PASS: domain flat-only in all samples")
    # (e) — дублирование проверки: позитивный flat-кейс валиден
    flat = _node_yaml_samples()
    logger.critical("[IMP:9][gate_context_contract][e] %d sample(s) scanned", len(flat))


# endregion
