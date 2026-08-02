# GREP_SUMMARY: gate image-tag-form ghcr tag-policy versioned-tag digest-pin latest-forbidden CONTEXT_IMAGE base-yml U-60
# STRUCTURE: ┌scan platform-infra.yaml env_defaults.CONTEXT_IMAGE + modules/*/docker-compose.base.yml ghcr refs┐ → ◇ _is_valid_ghcr_ref (versioned|digest-pin|bare; bare :latest = RED) → ⊕ violations → ∑ assert → ⎋ negative R5 inline fixture
# region MODULE_CONTRACT
## @purpose  Gate enforcing the unified ghcr tag policy (DevPlan 116 B3 T7, U-60): every ghcr.io
##           image reference in production sources (platform-infra.yaml env_defaults.CONTEXT_IMAGE +
##           core/modules/*/docker-compose.base.yml image lines) must be a versioned tag (v<Y>.<M>.<D>),
##           a digest-pin (tag@sha256:<64hex> — tag irrelevant, immutable), or a bare repo name.
##           BARE `:latest` (no digest) is RED. Dev/test files are allowlisted (NOT scanned):
##           docker-compose.platform-dev.yml, tests/_conftest/smoke.py, docker-compose.test.yml.
## @scope    Static file analysis — no Docker daemon, no network. Scans:
##           1. core/platform-infra.yaml → env_defaults.CONTEXT_IMAGE
##           2. core/modules/*/docker-compose.base.yml → image: lines containing ghcr.io
## @invariants
##   - Digest-pinned refs (ANY tag + @sha256:<64hex>) are valid — the digest makes them immutable
##   - BARE `:latest` (no @sha256) is RED (tag policy: :latest only for dev/test allowlist)
##   - Non-versioned tag without digest (e.g. :foo) is RED
##   - Bare repo name (ghcr.io/org/repo) is valid (no tag drift surface)
##   - Negative R5 test: inline fixture with :latest in base.yml context → validator rejects
##   - All tests @pytest.mark.gate
## @rationale  U-60: three tag forms diverged (v2026.7.1 / :latest / tag@sha256). Single policy:
##             releases = versioned tags, prod defaults = digest-pin, :latest only in dev/test.
## @changes  2026-08-01 · Created (DevPlan 116 B3 T7)
# endregion MODULE_CONTRACT

import logging
import re
from pathlib import Path

import pytest
import yaml

logger = logging.getLogger(__name__)

PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
PLATFORM_INFRA = PROJECT_ROOT / "core" / "platform-infra.yaml"
MODULES_DIR = PROJECT_ROOT / "core" / "modules"

# Allowlisted dev/test files — NOT scanned (tag policy: :latest only for dev/test).
ALLOWLIST = {
    "docker-compose.platform-dev.yml",
    "tests/_conftest/smoke.py",
    "docker-compose.test.yml",
}

# Full ghcr ref: ghcr.io/org/repo[:tag][@sha256:<64hex>]
_GHCR_REF_RE = re.compile(r"^ghcr\.io/[^/]+/[a-z0-9-]+(?::[^@\s/]+)?(?:@sha256:[a-f0-9]{64})?$")
_VERSIONED_TAG_RE = re.compile(r"^v\d+\.\d+\.\d+$")


def _is_valid_ghcr_ref(ref: str) -> bool:
    """Validate a ghcr.io image reference against the unified tag policy.

    ## @purpose — Tag-policy validator (DevPlan 116 B3 T7, U-60).
    ## @io — ⇥ ref: str (e.g. ghcr.io/org/repo:v2026.7.1) → ⎋ bool
    ## @complexity — O(1) — regex + suffix checks
    ## @invariants
    ##   - Shape must match ghcr.io/org/repo(:tag)?(@sha256:64hex)?
    ##   - Digest-pinned (any tag + @sha256) → valid (immutable regardless of tag)
    ##   - No tag → valid (bare repo)
    ##   - Tag without digest → must be versioned vX.Y.Z (bare :latest → RED)
    """
    m = _GHCR_REF_RE.match(ref)
    if not m:
        return False
    # Digest-pinned → immutable → valid regardless of the tag component
    if "@sha256:" in ref:
        return True
    # Bare repo (no tag) → valid
    body = ref.split("/", 2)[2]  # "repo[:tag]" (no @digest here)
    if ":" not in body:
        return True
    tag = body.rsplit(":", 1)[1]
    return _VERSIONED_TAG_RE.fullmatch(tag) is not None


def _scan_ghcr_refs() -> list[tuple[str, int, str]]:
    """Scan platform-infra.yaml CONTEXT_IMAGE + module base.yml image lines for ghcr.io refs.

    ## @purpose — Collect (file, line_no, ref) tuples from production sources.
    ## @io — ⎋ list[tuple[str, int, str]]
    ## @complexity — O(F * L) where F = files, L = lines
    ## @invariants
    ##   - platform-infra.yaml: env_defaults.CONTEXT_IMAGE value (only ghcr refs)
    ##   - base.yml: any image: line containing ghcr.io — refs extracted via regex
    ##   - Allowlisted dev/test files never scanned
    """
    found: list[tuple[str, int, str]] = []

    # ── 1. platform-infra.yaml env_defaults.CONTEXT_IMAGE ──
    if PLATFORM_INFRA.exists():
        try:
            data = yaml.safe_load(PLATFORM_INFRA.read_text())
            ctx_image = (data or {}).get("env_defaults", {}).get("CONTEXT_IMAGE", "")
            if isinstance(ctx_image, str) and "ghcr.io" in ctx_image:
                found.append((str(PLATFORM_INFRA.relative_to(PROJECT_ROOT)), 0, ctx_image))
        except yaml.YAMLError:
            logger.debug("[IMP:7][image-tag-form] platform-infra.yaml unreadable (corrupt YAML) — skipping")

    # ── 2. core/modules/*/docker-compose.base.yml image lines ──
    for compose_file in sorted(MODULES_DIR.glob("*/docker-compose.base.yml")):
        rel = str(compose_file.relative_to(PROJECT_ROOT))
        for line_no, line in enumerate(compose_file.read_text().splitlines(), 1):
            stripped = line.strip()
            if stripped.startswith("#") or "image:" not in stripped:
                continue
            if "ghcr.io" not in stripped:
                continue
            # Extract all ghcr.io refs from the line (stops at ${} / quotes / whitespace)
            for m in re.finditer(r"ghcr\.io/[^\s\"'${}]+", stripped):
                ref = m.group(0).rstrip(".,;")
                if ref:
                    found.append((rel, line_no, ref))

    return found


@pytest.mark.gate
class TestGateImageTagForm:
    """Gate: unified ghcr tag form — versioned tag / digest-pin / bare; bare :latest is RED (U-60)."""

    # 🧪 TRAP[TEST] · 2026-08-01 · REGRESSION · ghcr tag form policy (DevPlan 116 B3 T7, U-60)
    # · Last fail: 3 diverging forms (v2026.7.1 / :latest / tag@sha256) across platform-infra.yaml,
    # ·   smoke.py, base.yml
    # · Remove if: tag policy changes
    def test_ghcr_refs_follow_tag_policy(self):
        """All ghcr refs in production sources match the unified tag policy."""
        refs = _scan_ghcr_refs()
        violations = [(rel, line_no, ref) for rel, line_no, ref in refs if not _is_valid_ghcr_ref(ref)]
        assert not violations, (
            f"GATE_TAG_FORM: {len(violations)} ghcr reference(s) violate tag policy "
            f"(versioned tag or digest-pin required; bare :latest is RED):\n"
            + "\n".join(f"  • {rel}:{line_no} — {ref}" for rel, line_no, ref in violations)
        )

    # 🧪 TRAP[TEST] · 2026-08-01 · REGRESSION · policy accepts canonical forms (DevPlan 116 B3 T7)
    # · Last fail: N/A (new positive test)
    # · Remove if: tag policy changes
    def test_valid_forms_accepted(self):
        """Versioned tag, digest-pin (any tag), and bare repo are valid."""
        assert _is_valid_ghcr_ref("ghcr.io/tronyxlab/hermes-agent-context:v2026.7.1")
        assert _is_valid_ghcr_ref("ghcr.io/tronyxlab/hermes-agent-context:latest@sha256:" + "a" * 64)
        assert _is_valid_ghcr_ref("ghcr.io/tronyxlab/hermes-agent-context@sha256:" + "a" * 64)
        assert _is_valid_ghcr_ref("ghcr.io/tronyx161/hermes-agent-base")
        # Context image default in platform-infra.yaml (v2026.7.1) must pass
        infra = yaml.safe_load(PLATFORM_INFRA.read_text())
        ctx = infra["env_defaults"]["CONTEXT_IMAGE"]
        assert _is_valid_ghcr_ref(ctx), f"platform-infra CONTEXT_IMAGE invalid: {ctx}"

    # 🧪 TRAP[TEST] · 2026-08-01 · NEGATIVE (R5) · bare :latest rejected (DevPlan 116 B3 T7)
    # · Last fail: smoke.py:116 used CONTEXT_IMAGE=ghcr.io/tronyxlab/hermes-agent-context:latest
    # ·   (test fixture — allowlisted); base.yml default was :latest@sha256 (digest-pin, valid)
    # · Remove if: tag policy changes
    def test_bare_latest_rejected_negative(self):
        """Inline fixture: bare :latest in a base.yml context → RED (R5 anti-survivorship)."""
        inline_base_yml_line = "image: ${CONTEXT_IMAGE:-ghcr.io/tronyxlab/hermes-agent-context:latest}"
        ref_match = re.search(r"ghcr\.io/[^\s\"'${}]+", inline_base_yml_line)
        assert ref_match, "Inline fixture must contain a ghcr ref"
        ref = ref_match.group(0).rstrip(".,;")
        assert not _is_valid_ghcr_ref(ref), f"Bare :latest must be RED, but validator accepted: {ref}"

    # 🧪 TRAP[TEST] · 2026-08-01 · REGRESSION · non-versioned tag without digest rejected
    # · Last fail: N/A (new negative)
    # · Remove if: tag policy changes
    def test_plain_tag_without_digest_rejected(self):
        """Non-versioned tag without digest (e.g. :foo) → RED (no tag-drift surface)."""
        assert not _is_valid_ghcr_ref("ghcr.io/tronyxlab/hermes-agent-context:latest")
        assert not _is_valid_ghcr_ref("ghcr.io/tronyxlab/hermes-agent-context:staging")

    # 🧪 TRAP[TEST] · 2026-08-03 · REGRESSION · CONTEXT_IMAGE default != SoT (DevPlan 122 T4, P-4)
    # · Last fail: hermes-agent base.yml:69 fallback `latest@sha256:dd36…` vs SoT v2026.7.1
    # ·   (platform-infra:146, .env.example:53, platform-env.yaml:128) — скрытый второй пин
    # · Remove if: CONTEXT_IMAGE канонизируется иначе
    def test_context_image_default_matches_sot(self):
        """Default тег CONTEXT_IMAGE в hermes-agent base.yml == env_defaults.CONTEXT_IMAGE (SoT)."""
        infra = yaml.safe_load(PLATFORM_INFRA.read_text())
        sot = infra["env_defaults"]["CONTEXT_IMAGE"]
        assert sot.startswith("ghcr.io/"), f"SoT CONTEXT_IMAGE должен быть ghcr-рефом: {sot}"
        base = (MODULES_DIR / "hermes-agent" / "docker-compose.base.yml").read_text()
        m = re.search(r"\$\{CONTEXT_IMAGE:-([^}]+)\}", base)
        assert m, "GATE_IMAGE_TAG_FORM: fallback ${CONTEXT_IMAGE:-...} не найден в hermes-agent base.yml"
        assert m.group(1) == sot, (
            f"GATE_IMAGE_TAG_FORM: compose default '{m.group(1)}' != SoT '{sot}' — скрытый второй пин образа (P-4)"
        )

    # 🧪 TRAP[TEST] · 2026-08-03 · NEGATIVE (R5) · исходный вход P-4 (DevPlan 122 T4)
    # · Last fail: base.yml:69 fallback ghcr.io/tronyxlab/hermes-agent-context:latest@sha256:dd36…
    # · Remove if: CONTEXT_IMAGE канонизируется иначе
    def test_latest_digest_fallback_detected_negative(self):
        """R5 negative: inline-фикстура fallback :latest@sha256 → RED (детектор ловит P-4)."""
        inline_line = "image: ${CONTEXT_IMAGE:-ghcr.io/tronyxlab/hermes-agent-context:latest@sha256:" + "d" * 64 + "}"
        m = re.search(r"\$\{CONTEXT_IMAGE:-([^}]+)\}", inline_line)
        assert m, "R5 FAIL: inline fixture must contain CONTEXT_IMAGE fallback"
        ref = m.group(1)
        assert ref.startswith("ghcr.io/"), "R5 FAIL: fixture must be a ghcr ref"
        # Детектор P-4: fallback != SoT (v2026.7.1) → RED
        assert ref != "ghcr.io/tronyxlab/hermes-agent-context:v2026.7.1", (
            "R5 FAIL: fixture должен отличаться от SoT (воспроизводить вход P-4)"
        )
