#!/usr/bin/env python3
# GREP_SUMMARY: manifest-oracle-tests, semantic-validator, consumers-parity, negative, real-tree-parity, no-generator-import, REF-0107
# STRUCTURE: ▶ fixture-tree (definitions+manifest+module.yaml) → ◇ O1..O4 violations → ⎋ assert ·
#            ▶ реальное дерево репо → ◇ oracle == [] (parity с зелёным check-manifests)
# region MODULE_CONTRACT
## @purpose  Тесты независимого manifests-oracle (REF-0107): негативы O1-O4 на tmp-деревьях
##           (stale/drift/consumers/structural) + oracle-parity — на РЕАЛЬНОМ дереве вердикт
##           пуст одновременно с зелёным generator-based check-manifests.
## @scope    core/internal/check_suite/manifest_oracle.py. Нативные импорты, tmp_path (0 hardcode).
## @invariants
##   - Oracle-модуль НЕ импортирует generate_secrets_manifest — структурная проверка
##     test_oracle_is_independent_from_generator сторожит это (иначе теряется смысл судьи)
## @rationale REF-0107 problem 6: «парити судится тем же генератором» + вакуумный git-diff.
## @changes 2026-08-25 | REF-0107 (DevPlan 11 Волна 3) — Created
# endregion MODULE_CONTRACT

import ast
import logging
import textwrap
from pathlib import Path

from core.internal.check_suite import manifest_oracle
from core.internal.check_suite.manifest_oracle import oracle_secrets_manifest

logger = logging.getLogger(__name__)


def _make_tree(tmp_path: Path, *, definitions: str, manifest: str, modules: dict[str, str]) -> Path:
    """Fixture-репозиторий: secret-definitions.yaml + secrets-manifest.yaml + модули.

    ## @io ⇥ definitions/manifest — YAML-текст; modules — {module_name: module.yaml-текст}
    """
    root = Path(tmp_path)
    (root / "core").mkdir(parents=True)
    (root / "core" / "secret-definitions.yaml").write_text(textwrap.dedent(definitions), encoding="utf-8")
    (root / "core" / "secrets-manifest.yaml").write_text(textwrap.dedent(manifest), encoding="utf-8")
    mods = root / "core" / "modules"
    mods.mkdir(parents=True, exist_ok=True)
    for module, yaml_text in modules.items():
        mdir = mods / module
        mdir.mkdir(exist_ok=True)
        (mdir / "module.yaml").write_text(textwrap.dedent(yaml_text), encoding="utf-8")
    return root


_DEFS_TWO = """\
    version: 1
    secrets:
    - name: ALPHA_TOKEN
      tier: required
      source: sops
      charset: ^[A-Za-z0-9]+$
    - name: BETA_GEN
      tier: generated
      source: autogen
      gen_command: openssl rand -hex 16
"""

_DEFS_ALPHA_ONLY = """\
    version: 1
    secrets:
    - name: ALPHA_TOKEN
      tier: required
      source: sops
      charset: ^[A-Za-z0-9]+$
"""

_MANIFEST_HAPPY = """\
    version: 1
    secrets:
    - name: ALPHA_TOKEN
      tier: required
      source: sops
      charset: ^[A-Za-z0-9]+$
      consumers:
      - mod-a
    - name: BETA_GEN
      tier: generated
      source: autogen
      gen_command: openssl rand -hex 16
      consumers: []
"""

_MANIFEST_ALPHA_STALE = """\
    version: 1
    secrets:
    - name: ALPHA_TOKEN
      tier: required
      source: sops
      consumers: []
    - name: BETA_GEN
      tier: generated
      source: autogen
      gen_command: openssl rand -hex 16
      consumers: []
"""

_MOD_A_REQUIRES_ALPHA = """\
env_requires:
  - ALPHA_TOKEN
"""


# region TEST_positive_and_negatives
def test_oracle_clean_tree_no_violations(tmp_path, caplog) -> None:
    """Согласованное дерево → 0 violations (O1-O4 все зелёные)."""
    caplog.set_level(logging.INFO)
    tree = _make_tree(
        tmp_path,
        definitions=_DEFS_TWO,
        manifest=_MANIFEST_HAPPY,
        modules={"mod-a": _MOD_A_REQUIRES_ALPHA},
    )
    violations = oracle_secrets_manifest(tree)
    assert violations == [], f"FAIL: чистое дерево не должно давать нарушений: {violations}"
    logger.info("[IMP:9][oracle-test] clean tree → empty verdict")


def test_oracle_o1_detects_stale_missing_secret(tmp_path) -> None:
    """O1-negative: секрет удалён из definitions, но остался в generated (vacuum git-diff miss)."""
    tree = _make_tree(
        tmp_path,
        definitions=_DEFS_ALPHA_ONLY,
        manifest=_MANIFEST_ALPHA_STALE,
        modules={},
    )
    violations = oracle_secrets_manifest(tree)
    assert any("O1" in v and "BETA_GEN" in v for v in violations), (
        f"R5 FAIL: oracle пропустил stale-секрет BETA_GEN: {violations}"
    )
    logger.info("[IMP:9][oracle-test] O1 stale detected")


def test_oracle_o2_detects_tier_drift(tmp_path) -> None:
    """O2-negative: tier в generated расходится с definitions."""
    drifted = _MANIFEST_HAPPY.replace(
        "- name: ALPHA_TOKEN\n      tier: required", "- name: ALPHA_TOKEN\n      tier: optional"
    )
    tree = _make_tree(
        tmp_path,
        definitions=_DEFS_TWO,
        manifest=drifted,
        modules={"mod-a": _MOD_A_REQUIRES_ALPHA},
    )
    violations = oracle_secrets_manifest(tree)
    assert any("O2" in v and "ALPHA_TOKEN" in v and "tier" in v for v in violations), (
        f"R5 FAIL: tier-drift не обнаружен: {violations}"
    )
    logger.info("[IMP:9][oracle-test] O2 tier-drift detected")


def test_oracle_o3_detects_consumer_drift(tmp_path) -> None:
    """O3-negative: module.yaml добавил env_requires, generated consumers не пересчитаны."""
    tree = _make_tree(
        tmp_path,
        definitions=_DEFS_TWO,
        manifest=_MANIFEST_HAPPY,
        modules={"mod-a": _MOD_A_REQUIRES_ALPHA, "mod-b": _MOD_A_REQUIRES_ALPHA},
    )
    violations = oracle_secrets_manifest(tree)
    assert any("O3" in v and "ALPHA_TOKEN" in v and "mod-b" in v for v in violations), (
        f"R5 FAIL: consumer-drift mod-b не обнаружен: {violations}"
    )
    logger.info("[IMP:9][oracle-test] O3 consumer-drift detected")


def test_oracle_o3_dict_form_env_requires(tmp_path) -> None:
    """O3-positive: env_requires в dict-форме ({name: X}) учитывается при расчёте consumers."""
    dict_form_manifest = (
        "version: 1\nsecrets:\n- name: ALPHA_TOKEN\n  tier: required\n  source: sops\n  consumers:\n  - mod-c\n"
    )
    tree = _make_tree(
        tmp_path,
        definitions=_DEFS_ALPHA_ONLY,
        manifest=dict_form_manifest,
        modules={},
    )
    mod_c = tree / "core" / "modules" / "mod-c"
    mod_c.mkdir(parents=True)
    (mod_c / "module.yaml").write_text(
        "env_requires:\n  - name: ALPHA_TOKEN\n    type: secret\n    required: true\n", encoding="utf-8"
    )
    violations = oracle_secrets_manifest(tree)
    assert violations == [], f"dict-форма env_requires должна засчитываться: {violations}"
    logger.info("[IMP:9][oracle-test] O3 dict-form recognized")


def test_oracle_o4_structural_generated_without_gen_command(tmp_path) -> None:
    """O4-negative: tier=generated без gen_command."""
    bad_manifest = (
        "version: 1\n"
        "secrets:\n"
        "- name: ALPHA_TOKEN\n"
        "  tier: required\n"
        "  source: sops\n"
        "  consumers: []\n"
        "- name: BETA_GEN\n"
        "  tier: generated\n"
        "  source: autogen\n"  # gen_command отсутствует
        "  consumers: []\n"
    )
    tree = _make_tree(tmp_path, definitions=_DEFS_TWO, manifest=bad_manifest, modules={})
    violations = oracle_secrets_manifest(tree)
    assert any("O4" in v and "BETA_GEN" in v and "gen_command" in v for v in violations), (
        f"R5 FAIL: generated без gen_command не пойман: {violations}"
    )
    logger.info("[IMP:9][oracle-test] O4 structural detected")


# endregion TEST_positive_and_negatives


# region TEST_real_tree_parity_and_independence
def test_oracle_real_tree_parity(caplog) -> None:
    """Oracle-parity: на живом дереве вердикт пуст одновременно с зелёным check-manifests.

    ## @purpose  Parity-якорь: CI держит generated-манифест свежим (генераторный гейт);
    ##            oracle обязан соглашаться на свежем дереве. Расхождение вердиктов =
    ##            баг oracle или дыра генераторного контракта — оба случая видны.
    """
    from tests.helpers.gate_helpers import repo_root

    caplog.set_level(logging.INFO)
    violations = oracle_secrets_manifest(repo_root())
    assert violations == [], (
        "[IMP:10][oracle-parity] семантический дрейф secrets-manifest, невидимый для "
        "git-diff/генератора:\n" + "\n".join(violations)
    )
    logger.info("[IMP:9][oracle-parity] PASS: oracle согласен с генераторным green на живом дереве")


def test_oracle_is_independent_from_generator() -> None:
    """Структурная гарантия нейтральности: oracle-модуль не импортирует генератор.

    ## @purpose  Судья не может быть тем же кодом, что и подсудимый (REF-0107 problem 6).
    """
    source = Path(manifest_oracle.__file__).read_text(encoding="utf-8")
    for node in ast.walk(ast.parse(source)):
        if isinstance(node, ast.Import):
            assert all("generate_secrets_manifest" not in alias.name for alias in node.names), (
                "oracle импортирует генератор — независимость вердикта потеряна"
            )
        if isinstance(node, ast.ImportFrom):
            assert node.module is None or "generate_secrets_manifest" not in node.module, (
                "oracle импортирует генератор — независимость вердикта потеряна"
            )
    logger.info("[IMP:9][oracle-independence] PASS: генератор не импортируется")


# endregion TEST_real_tree_parity_and_independence
