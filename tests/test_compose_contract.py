#!/usr/bin/env python3
# GREP_SUMMARY: compose-contract image-pinning tag-digest base-yml docker-compose
# STRUCTURE: ▶ ⊕ each module {discovery} → ⊕ each image: tag@sha256:hex → ◇ skip local builds → ◇ known exceptions → pass/fail; · backup-cron/Dockerfile FROM separately
# region MODULE_CONTRACT
## @purpose  Contract test enforcing tag@digest format on all external images
##           in docker-compose.base.yml files and the backup-cron Dockerfile FROM.
## @scope    Static file analysis of docker-compose.base.yml ×N (discovery-based) + backup-cron/Dockerfile.
##           No Docker, no subprocess — pure file I/O + YAML parsing.
## @invariants
##   - Every `image:` (non-variable) must match /.+:[\w.\-]+@sha256:[0-9a-f]{64}/
##   - Every `${VAR:-default}` default value must match same regex
##   - Locally built images (service has `build:`) are SKIPPED
##   - Known exceptions listed in KNOWN_EXCEPTIONS dict
##   - backup-cron/Dockerfile FROM must have tag@digest format
##   - LDD telemetry via caplog IMP:7-10 + assert IMP:9
## @changes 2026-07-16 | TASK-5 (Gate Scope Closure): Discovery-based TARGET_MODULES,
##            parametrized per-module, backup-cron FROM separated to own test, +backup-cron KNOWN_EXCEPTION.
## @rationale DevPlan 017 T4: converges drift L (image pinning). Ensures
##            reproducible builds via tag@digest — prevents mutable tag drift.
# endregion MODULE_CONTRACT

import logging
import pathlib
import re

import pytest
import yaml

logger = logging.getLogger(__name__)

# ── Constants ───────────────────────────────────────────────────────────────

# Regex: image:tag@sha256:64-hex-chars
# Must have a colon-separated tag before @sha256:
IMAGE_TAG_DIGEST_RE: re.Pattern = re.compile(
    r"^.+:[\w.\-]+@sha256:[0-9a-f]{64}$"
)

PLATFORM_ROOT: pathlib.Path = pathlib.Path(__file__).resolve().parent.parent
MODULES_DIR: pathlib.Path = PLATFORM_ROOT / "core" / "modules"

# Auto-discovered modules with docker-compose.base.yml
# Discovery-based: sorted glob(core/modules/*/docker-compose.base.yml)
# Removes hardcoded list drift (Collapse #3 from Forensics Report)
TARGET_MODULES: list[str] = sorted(
    p.parent.name
    for p in MODULES_DIR.glob("*/docker-compose.base.yml")
)

# backup-cron Dockerfile FROM image check
BACKUP_CRON_DOCKERFILE: pathlib.Path = (
    MODULES_DIR / "backup-cron" / "Dockerfile"
)

# Known exceptions: images allowed without tag@digest
# Key: (module_name, image_value_or_default)
# Value: reason string
KNOWN_EXCEPTIONS: dict[tuple[str, str], str] = {
    (
        "hermes-agent",
        "ghcr.io/tronyxlab/hermes-agent-context:v2026.7.1",
    ): (
        "Context overlay image built by context CI — "
        "resolve digest when image is published"
    ),
    (
        "backup-cron",
        "backup-cron:latest",
    ): (
        "Locally built image — digest resolved at build time, "
        "not meaningful before build"
    ),
}


# ── Helpers ─────────────────────────────────────────────────────────────────


def _collect_service_images(
    base_yml_path: pathlib.Path, module_name: str
) -> list[dict]:
    """Parse docker-compose.base.yml and extract service image references.

    ## @purpose — Extract all `image:` values from a base.yml, flagging
    ##            locally-built services (have `build:` section).
    ## @returns — List of dicts: {service, module, image, is_local_build}
    """
    with open(base_yml_path) as f:
        content = f.read()

    data = yaml.safe_load(content)
    if not data or "services" not in data:
        return []

    services = data.get("services", {})
    result: list[dict] = []

    for svc_name, svc_config in services.items():
        if not isinstance(svc_config, dict):
            continue

        # Services with both build: and image: are local builds
        is_local_build = "build" in svc_config
        image_val = svc_config.get("image")

        if not image_val:
            continue

        result.append({
            "service": svc_name,
            "module": module_name,
            "image": image_val,
            "is_local_build": is_local_build,
        })

    return result


def _extract_default_from_variable(image_val: str) -> str | None:
    """Extract the default value from a compose variable reference.

    ## @purpose — Compose supports `${VAR:-default}` syntax. This extracts
    ##            `default` so the regex can check the fallback image.
    ## @example — `${PGBOUNCER_IMAGE:-img:tag@sha256:...}` → `img:tag@sha256:...`
    ## @returns — Default string or None if not a variable reference.
    """
    if not image_val.startswith("${") or ":-" not in image_val:
        return None

    default_start = image_val.index(":-") + 2
    if image_val.endswith("}"):
        return image_val[default_start:-1]
    return None


def _check_backup_cron_from() -> dict:
    """Check backup-cron Dockerfile FROM line for tag@digest format.

    ## @purpose — backup-cron is a local build; its FROM image must be pinned.
    ## @returns — Dict with {passed, image, error} or None if no FROM.
    """
    assert BACKUP_CRON_DOCKERFILE.is_file(), (
        f"[IMP:9] backup-cron Dockerfile not found at {BACKUP_CRON_DOCKERFILE}"
    )

    with open(BACKUP_CRON_DOCKERFILE) as f:
        for line in f:
            line_stripped = line.strip()
            if line_stripped.startswith("FROM "):
                # Extract image after "FROM "
                from_image = line_stripped[5:].strip()
                result = {
                    "image": from_image,
                    "passed": bool(
                        IMAGE_TAG_DIGEST_RE.match(from_image)
                    ),
                    "error": None,
                }
                if not result["passed"]:
                    result["error"] = (
                        f"backup-cron FROM {from_image} "
                        f"missing tag@digest format"
                    )
                return result

    return {"image": None, "passed": False, "error": "No FROM line found"}


# ── Tests ───────────────────────────────────────────────────────────────────


@pytest.mark.contract
@pytest.mark.parametrize("module_name", TARGET_MODULES)
def test_images_pinned_tag_digest(caplog, module_name: str) -> None:
    """Verify all external images in a module's docker-compose.base.yml use tag@digest format.

    ## @purpose — Contract enforcement per DevPlan 017 T4 / 019 TASK-5.
    ##            Discovery-based: checks all modules found in
    ##            core/modules/*/docker-compose.base.yml.
    ##            Parametrized per module for readable test output (node-id per module).
    ## @scenario —
    ##   ▶ Parse docker-compose.base.yml services
    ##     ▶ For each image: value:
    ##       ◇ Has build: section? → skip (local build)
    ##       ◇ Is variable with default? → extract default
    ##       ◇ In KNOWN_EXCEPTIONS? → log warning, skip
    ##       ◇ Matches tag@digest regex? → ✓
    ##       ◇ No match? → ✗ failure
    ##   ⎋ assert no failures + assert IMP:9 logged
    """
    caplog.set_level(logging.DEBUG)

    failures: list[str] = []
    checked_count: int = 0
    skipped_count: int = 0
    exception_count: int = 0
    found_imp9: bool = False

    base_yml = MODULES_DIR / module_name / "docker-compose.base.yml"
    assert base_yml.is_file(), (
        f"[IMP:9] docker-compose.base.yml not found: {base_yml}"
    )

    images = _collect_service_images(base_yml, module_name)
    logger.info(
        "[IMP:7][%s] Found %d image(s) in %s",
        module_name, len(images), base_yml.name,
    )

    for img_info in images:
        service: str = img_info["service"]
        image_val: str = img_info["image"]
        is_local: bool = img_info["is_local_build"]
        variable_default: str | None = _extract_default_from_variable(
            image_val
        )

        # Skip locally-built images (have build: section)
        if is_local:
            logger.info(
                "[IMP:8][%s/%s] local build, skip: %s",
                module_name, service, image_val,
            )
            skipped_count += 1
            continue

        # Extract the string to check against regex
        check_str: str = (
            variable_default
            if variable_default is not None
            else image_val
        )

        # Check if this is a known exception
        exception_key = (module_name, check_str)
        if exception_key in KNOWN_EXCEPTIONS:
            reason = KNOWN_EXCEPTIONS[exception_key]
            logger.warning(
                "[IMP:7][%s/%s] KNOWN EXCEPTION: %s — %s",
                module_name, service, check_str, reason,
            )
            exception_count += 1
            continue

        # Check tag@digest format
        if bool(IMAGE_TAG_DIGEST_RE.match(check_str)):
            logger.info(
                "[IMP:8][%s/%s] OK %s",
                module_name, service, check_str,
            )
            checked_count += 1
            found_imp9 = True
        else:
            msg = (
                f"[IMP:9][{module_name}/{service}] "
                f"MISSING tag@digest: {check_str}"
            )
            logger.error(msg)
            failures.append(msg)

    # Handle edge cases: no images, all local builds, or all exceptions
    if not images:
        logger.info(
            "[IMP:9][%s] No images found — vacuously passed", module_name
        )
        found_imp9 = True
    elif not found_imp9 and checked_count == 0:
        if skipped_count > 0 and exception_count == 0:
            logger.info(
                "[IMP:9][%s] All images are local builds — passed", module_name
            )
            found_imp9 = True
        elif exception_count > 0 and skipped_count == 0:
            logger.info(
                "[IMP:9][%s] All images are known exceptions — passed", module_name
            )
            found_imp9 = True
        elif exception_count > 0 and skipped_count > 0:
            logger.info(
                "[IMP:9][%s] All images are local builds or known exceptions — passed",
                module_name,
            )
            found_imp9 = True

    # ── LDD trajectory (manual via print) ─────────────────────────────────

    print(f"--- LDD TRAJECTORY (IMP:7-10) [{module_name}] ---")
    for record in caplog.records:
        if "[IMP:" in record.message:
            imp_str = record.message.split("[IMP:")[1]
            imp_level = int(imp_str.split("]")[0])
            if imp_level >= 7:
                print(record.message)
    print("--- END LDD TRAJECTORY ---")

    # ── Summary ────────────────────────────────────────────────────────────

    logger.info(
        "[IMP:8][%s] Summary — checked: %d, skipped(local): %d, "
        "exceptions: %d, failures: %d",
        module_name, checked_count, skipped_count, exception_count, len(failures),
    )

    # Fail assertions
    assert not failures, (
        f"[IMP:9] {len(failures)} image(s) missing tag@digest in {module_name}:\n"
        + "\n".join(failures)
    )
    assert found_imp9, f"[IMP:9] No IMP:9 log found in {module_name} trajectory"


@pytest.mark.contract
def test_backup_cron_from_pinned(caplog) -> None:
    """Verify backup-cron Dockerfile FROM uses tag@digest format.

    ## @purpose — Separate test for backup-cron Dockerfile FROM line.
    ##            The compose image (backup-cron:latest) is a local build handled
    ##            by parametrized test_images_pinned_tag_digest.
    ## @scenario —
    ##   ▶ Read backup-cron/Dockerfile
    ##   ▶ Extract FROM line
    ##   ▶ Match tag@digest regex
    ##   ⎋ pass/fail
    """
    caplog.set_level(logging.DEBUG)

    logger.info("[IMP:7][backup-cron] Checking Dockerfile FROM")
    bc_result = _check_backup_cron_from()
    if bc_result["image"]:
        logger.info(
            "[IMP:8][backup-cron] FROM %s", bc_result["image"]
        )
        if bc_result["passed"]:
            logger.info(
                "[IMP:9][backup-cron/Dockerfile] OK %s",
                bc_result["image"],
            )
        else:
            logger.error("[IMP:9] %s", bc_result["error"])

    # ── LDD trajectory ─────────────────────────────────────────────────────

    print("--- LDD TRAJECTORY (IMP:7-10) [backup-cron] ---")
    for record in caplog.records:
        if "[IMP:" in record.message:
            imp_str = record.message.split("[IMP:")[1]
            imp_level = int(imp_str.split("]")[0])
            if imp_level >= 7:
                print(record.message)
    print("--- END LDD TRAJECTORY ---")

    # Assertions after trajectory for full debug output on failure
    if bc_result["image"] and not bc_result["passed"]:
        pytest.fail(bc_result["error"])

    assert bc_result["image"] is not None, (
        "[IMP:9] No FROM line found in backup-cron Dockerfile"
    )
