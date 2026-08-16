# GREP_SUMMARY: gate ai-instructions-pins SoT tag-digest parity requires-instructions-version template-scaffold
# STRUCTURE: ┌scan pins.yaml + templates/*/template.yaml┐ → ◇ _validate_pins (tag format, digest format, hermes, templates) → ◇ parity template↔pin → ⊕ violations → ∑ assert → ⎋ negative R5 inline fixture
# region MODULE_CONTRACT
## @purpose  Gate enforcing the ai-instructions pins SoT (DevPlan 001 R11/T6.2): формат
##           canon.tag (v<major>.<minor>.<patch>), canon.digest (sha256:<64 hex> — если присутствует),
##           hermes-конфигурация профиля platform, templates.requires_instructions_version;
##           parity: requires_instructions_version шаблонов ≤ pin платформы (анти-дрейф scaffold)
## @scope    Static YAML analysis — no network, no Docker. Files:
##           1. core/internal/ai-instructions/ai-instructions-pins.yaml (SoT)
##           2. templates/template-{backend,frontend}/template.yaml
## @invariants
##   - canon.tag обязателен и матчит ^v\d+\.\d+\.\d+$
##   - canon.digest опционален в dev (W7-финализация), но если присутствует — sha256:<64 hex>
##   - hermes.enabled/roles_as_skills — bool; profile — непустой; emit_dir — непустой
##   - templates.requires_instructions_version — непустой семантический номер
##   - Parity: версия шаблона > pin платформы → RED (scaffold fail-fast, T5.2)
##   - Negative R5: inline fixture с invalid pin → валидатор отвергает
##   - Все тесты @pytest.mark.gate + @pytest.mark.ai_instructions
## @rationale  Digest-pin канон платформы (инвариант 11) распространяется на артефакт
##   инструкций; parity-гейт закрывает дрейф «шаблон новее платформы» (DevPlan 001 риск 2)
# endregion MODULE_CONTRACT

import logging
import re
from pathlib import Path

import pytest
import yaml

logger = logging.getLogger(__name__)

PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
PINS_PATH = PROJECT_ROOT / "core" / "internal" / "ai-instructions" / "ai-instructions-pins.yaml"
TEMPLATES_DIR = PROJECT_ROOT / "templates"

TAG_RE = re.compile(r"^v\d+\.\d+\.\d+$")
DIGEST_RE = re.compile(r"^sha256:[0-9a-f]{64}$")
SEMVER_RE = re.compile(r"^\d+\.\d+\.\d+$")

pytestmark = [
    pytest.mark.gate,
    pytest.mark.ai_instructions,
]


def _ver_tuple(version: str) -> tuple[int, ...]:
    parts: list[int] = []
    for seg in str(version).lstrip("v").split("."):
        try:
            parts.append(int(seg))
        except ValueError:
            break
    return tuple(parts)


def _validate_pins(data: dict) -> list[str]:
    """Валидировать pins.yaml; вернуть список нарушений (пусто = OK)."""
    violations: list[str] = []
    canon = data.get("canon") or {}
    tag = str(canon.get("tag") or "")
    if not TAG_RE.match(tag):
        violations.append(f"canon.tag {tag!r} не матчит {TAG_RE.pattern}")
    digest = canon.get("digest")
    if digest is not None and not DIGEST_RE.match(str(digest).strip()):
        violations.append(f"canon.digest {digest!r} не матчит sha256:<64 hex>")
    hermes = data.get("hermes") or {}
    violations.extend(
        f"hermes.{flag} должен быть bool"
        for flag in ("enabled", "roles_as_skills")
        if not isinstance(hermes.get(flag), bool)
    )
    if not str(hermes.get("profile") or "").strip():
        violations.append("hermes.profile пуст")
    if not str(hermes.get("emit_dir") or "").strip():
        violations.append("hermes.emit_dir пуст")
    templates = data.get("templates") or {}
    req = str(templates.get("requires_instructions_version") or "").strip()
    if not SEMVER_RE.match(req):
        violations.append(f"templates.requires_instructions_version {req!r} не матчит semver")
    return violations


# region TEST_pins_soT_format
def test_pins_sot_format_and_hermes_config():
    """SoT pins.yaml: формат tag/digest + hermes-профиль + requires_instructions_version."""
    assert PINS_PATH.is_file(), f"SoT отсутствует: {PINS_PATH}"
    data = yaml.safe_load(PINS_PATH.read_text(encoding="utf-8")) or {}
    violations = _validate_pins(data)
    assert not violations, f"pins.yaml нарушения: {violations}"
    hermes = data["hermes"]
    assert hermes["profile"] == "platform", "профиль hermes — platform (D2)"
    assert "profiles" in str(hermes["emit_dir"]), "emit_dir должен вести в каталог профилей"


# endregion TEST_pins_soT_format


# region TEST_templates_parity
def test_templates_instructions_parity():
    """Parity: requires_instructions_version шаблонов ≤ pin платформы (T5.2 анти-дрейф)."""
    data = yaml.safe_load(PINS_PATH.read_text(encoding="utf-8")) or {}
    pinned = str((data.get("templates") or {}).get("requires_instructions_version") or "")
    assert SEMVER_RE.match(pinned), "pin платформы должен быть semver"
    for tpl_dir in sorted(TEMPLATES_DIR.glob("template-*")):
        tpl_yaml = tpl_dir / "template.yaml"
        if not tpl_yaml.is_file():
            continue
        tpl = yaml.safe_load(tpl_yaml.read_text(encoding="utf-8")) or {}
        required = str(tpl.get("requires_instructions_version") or "")
        assert SEMVER_RE.match(required), f"{tpl_dir.name}: requires_instructions_version не semver: {required!r}"
        assert _ver_tuple(required) <= _ver_tuple(pinned), (
            f"{tpl_dir.name} требует инструкции v{required}, платформа пинит v{pinned} — обнови платформу"
        )


# endregion TEST_templates_parity


# region TEST_negative_r5
def test_negative_invalid_pins_rejected():
    """R5 negative: invalid pin (bad tag, bad digest) → валидатор отвергает."""
    invalid = {
        "canon": {"tag": "0.7", "digest": "sha256:zzz"},
        "hermes": {"enabled": "yes", "roles_as_skills": True, "profile": " ", "emit_dir": ""},
        "templates": {"requires_instructions_version": ""},
    }
    violations = _validate_pins(invalid)
    assert len(violations) >= 5, f"ожидались нарушения формата, получено: {violations}"
    assert any("canon.tag" in v for v in violations)
    assert any("canon.digest" in v for v in violations)
    assert any("requires_instructions_version" in v for v in violations)


# endregion TEST_negative_r5
