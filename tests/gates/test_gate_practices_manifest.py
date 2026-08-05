# GREP_SUMMARY: gate practices-manifest SoT consistency unique-ids levels classes languages pins-parity root-precommit
# STRUCTURE: ▶ ┌SoT: practices_manifest.yaml┐ → ◇ (a) version=1 → ◇ (b) id уникальны/kebab → ◇ (c) level/class/channel валидны → ◇ (d) пороги 30/50 → ◇ (e) pins == корневой .pre-commit-config.yaml → ⎋ pass|fail
# region MODULE_CONTRACT
## @purpose  Гейт консистентности канона практик (DevPlan 137 W5 + W1): уникальные id,
##           валидные уровни/классы/каналы, пороги зрелости (30/50 — решение 2026-08-05),
##           паритет pins канона с корневым .pre-commit-config.yaml платформы (анти-дрейф:
##           версии хуков проекта = версиям платформы, DevPlan 137 §2.1).
## @scope    Read-only гейт (исполняется в make gate MODE=fast, -m gate).
## @invariants
##   - version == 1 (bump при несовместимом изменении схемы)
##   - check id — kebab-case, уникальный
##   - level ∈ {baseline, full}; class ∈ {L1, L2, L3}; channel ⊆ {local, ci, verify}
##   - maturity == {age_days_propose: 30, code_files_propose: 50}
##   - pins (gitleaks/ruff_pre_commit/shellcheck_py/pre_commit_hooks) == rev корневого
##     .pre-commit-config.yaml (conventional_pre_commit отсутствует в корневом конфиге —
##     commit-msg там локальный хук, поэтому паритет для него не проверяется)
## @rationale  Канон — единственный SoT практик; расхождение с платформой (pins) = дрейф
##             поведения линтеров между проектом и платформой (DevPlan 137 §7 риск).
## @changes  2026-08-05 · DevPlan 137 W1 — создан
# endregion MODULE_CONTRACT

import logging

import pytest
import yaml

from core.internal.practices.manifest import load_manifest
from tests.helpers.gate_helpers import repo_root

logger = logging.getLogger(__name__)

ROOT = repo_root()
_ROOT_PRECOMMIT = ROOT / ".pre-commit-config.yaml"

# pins, для которых паритет проверяется (есть в корневом конфиге платформы)
_PARITY_PINS = {
    "gitleaks": "gitleaks",
    "ruff_pre_commit": "ruff-pre-commit",
    "shellcheck_py": "shellcheck-py",
    "pre_commit_hooks": "pre-commit-hooks",
}


@pytest.mark.gate
def test_gate_practices_manifest_version_one() -> None:
    """Канон практик — version 1 (схема v1)."""
    manifest = load_manifest()
    assert manifest.version == 1


@pytest.mark.gate
def test_gate_practices_manifest_ids_unique_and_kebab() -> None:
    """Check id — kebab-case, уникальный (однозначный selection)."""
    manifest = load_manifest()
    ids = [c.id for c in manifest.checks]
    assert len(ids) == len(set(ids)), f"duplicate ids: {ids}"
    for cid in ids:
        assert cid.replace("-", "").isalnum() and cid == cid.lower(), f"id не kebab-case: {cid}"


@pytest.mark.gate
def test_gate_practices_manifest_levels_classes_channels() -> None:
    """level/class/channel — закрытые домены (из manifest_schema.json Draft7 enum — SoT)."""
    import json

    from core.internal.practices.manifest import DEFAULT_MANIFEST_PATH

    schema_path = DEFAULT_MANIFEST_PATH.parent / "manifest_schema.json"
    schema = json.loads(schema_path.read_text(encoding="utf-8"))
    item_schema = schema["properties"]["checks"]["items"]["properties"]
    valid_levels = set(item_schema["level"]["enum"])
    valid_classes = set(item_schema["class"]["enum"])
    valid_channels = set(item_schema["channel"]["items"]["enum"])

    manifest = load_manifest()
    for check in manifest.checks:
        assert check.level in valid_levels, f"{check.id}: bad level {check.level}"
        assert check.klass in valid_classes, f"{check.id}: bad class {check.klass}"
        assert set(check.channel) <= valid_channels, f"{check.id}: bad channel"
        assert check.timeout_sec > 0, f"{check.id}: timeout<=0"
        assert check.languages, f"{check.id}: empty languages"


@pytest.mark.gate
def test_gate_practices_manifest_maturity_thresholds() -> None:
    """Пороги зрелости = 30 дней / 50 файлов (решение пользователя 2026-08-05)."""
    manifest = load_manifest()
    assert manifest.maturity == {"age_days_propose": 30, "code_files_propose": 50}


@pytest.mark.gate
def test_gate_practices_manifest_pins_parity_with_root_precommit() -> None:
    """pins канона == rev корневого .pre-commit-config.yaml (анти-дрейф версий хуков)."""
    manifest = load_manifest()
    with open(_ROOT_PRECOMMIT) as f:
        root_cfg = yaml.safe_load(f)
    root_revs: dict[str, str] = {}
    for repo in root_cfg.get("repos", []):
        url = str(repo.get("repo", ""))
        name = url.rstrip("/").split("/")[-1]
        root_revs[name] = str(repo.get("rev", ""))
    for pin_key, repo_name in _PARITY_PINS.items():
        canon_pin = manifest.pins[pin_key]
        root_rev = root_revs.get(repo_name, "")
        assert root_rev, f"корневой .pre-commit-config.yaml не содержит репозитория {repo_name}"
        assert canon_pin == root_rev, (
            f"pin {pin_key}={canon_pin} != корневой rev {repo_name}={root_rev} — "
            f"анти-дрейф нарушен (обнови pins в practices_manifest.yaml И корневой конфиг)"
        )
