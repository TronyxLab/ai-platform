# GREP_SUMMARY: dedup-contract sudo-whitelist symlink template no-static-duplicates
# STRUCTURE: ▶ test_symlinks → ◇ test_symlink_targets → ◇ test_template → ◇ test_no_static_duplicates
# @file test_dedup_contract.py
# @purpose  Contract tests ensuring sudo-whitelist.conf files are symlinks to the
#           template, no static duplicates exist, and the template is valid.
# @scope    4 tests covering AC4-AC5 plus template validation
# @invariants
#   - All 6 module sudo-whitelist.conf are symlinks
#   - All symlinks point to ../../templates/sudo-whitelist.template
#   - Template exists with {{MODULE_NAME}} placeholders and AUTO-GENERATED marker
#   - No static (non-symlink) sudo-whitelist.conf files exist in modules/
# @rationale Prevent regression of D11 (static sudo-whitelist duplicates)
#
# region MODULE_CONTRACT
## @purpose  4 contract tests verifying sudo-whitelist deduplication via symlinks
## @scope    Static file analysis of core/modules/*/sudo-whitelist.conf and
##           core/templates/sudo-whitelist.template. No subprocess calls.
## @invariants
##   - Uses platform_root fixture from conftest for project root resolution
##   - All tests use @ldd_trajectory decorator for LDD compliance
##   - No Docker or service dependencies — pure file I/O
## @rationale AC4-AC5 from DevPlan §TASK-3.5
# endregion MODULE_CONTRACT


import logging
import os
import pathlib
from pathlib import Path

import pytest
from conftest import ldd_trajectory

logger = logging.getLogger(__name__)

# ── Constants ──────────────────────────────────────────────────────────────────────────────────────

# Canonical set of all platform modules — must match actual directories in core/modules/
# This is a hardcoded whitelist so adding a module requires explicit registration.
# AGENTS.md is excluded (not a module directory).
# Rationale: set-equality, not dynamic — requires conscious registration of new modules.
PLATFORM_MODULES: set[str] = {
    "backup-cron",
    "clickhouse",
    "hermes-agent",
    "infra-metrics",
    "langfuse",
    "litellm",
    "log-collector",
    "logging",
    "minio",
    "monitoring",
    "nginx",
    "platform-secrets",
    "postgres",
    "redis",
    "status-page",
}

# Modules that should have a sudo-whitelist.conf symlink
WHITELIST_MODULES: list[str] = sorted([
    "hermes-agent",
    "nginx",
    "postgres",
    "redis",
    "backup-cron",
    "clickhouse",
])

EXPECTED_SYMLINK_TARGET: str = "../../templates/sudo-whitelist.template"

TEMPLATE_PATH_RELATIVE: str = Path("core") / "templates" / "sudo-whitelist.template"

# Minimum number of {{MODULE_NAME}} placeholders expected in template
MIN_MODULE_NAME_PLACEHOLDERS: int = 4


# ── Helpers ───────────────────────────────────────────────────────────────────────────────────────


def _get_whitelist_path(platform_root: str, module_name: str) -> str:
    """Get path to a module's sudo-whitelist.conf."""
    return Path(platform_root) / "core" / "modules" / module_name / "sudo-whitelist.conf"


def _get_template_path(platform_root: str) -> str:
    """Get path to sudo-whitelist template."""
    return Path(platform_root) / TEMPLATE_PATH_RELATIVE


# ── Tests ─────────────────────────────────────────────────────────────────────────────────────────

# ══════════════════════════════════════════════════════════════════════════════════════════════════
# Test 1: All sudo-whitelist.conf are symlinks (AC4)
# ══════════════════════════════════════════════════════════════════════════════════════════════════


# GUARD-PRESERVE (168): статический контракт AC4 (D11) — все sudo-whitelist.conf обязаны быть
# symlink; единственное покрытие класса дефекта static-дублей по WHITELIST_MODULES
@ldd_trajectory
def test_sudo_whitelist_all_symlinks(
    caplog: pytest.LogCaptureFixture,
    platform_root: str,
) -> None:
    """
    # ▶ core/modules/*/sudo-whitelist.conf → ⊕ os.path.islink → ◇ non-symlink? → ⎋ fail | pass
    """
    non_symlinks: list[str] = []
    for module in WHITELIST_MODULES:
        wl_path = _get_whitelist_path(platform_root, module)
        if not pathlib.Path(wl_path).exists():
            non_symlinks.append(f"{module}/sudo-whitelist.conf (MISSING)")
            logger.warning("[IMP:7][test_symlinks] %s: FILE MISSING", wl_path)
        elif not pathlib.Path(wl_path).is_symlink():
            non_symlinks.append(f"{module}/sudo-whitelist.conf (NOT SYMLINK)")
            logger.warning("[IMP:7][test_symlinks] %s: NOT a symlink", wl_path)
        else:
            logger.info("[IMP:8][test_symlinks] %s: symlink -> %s", module, pathlib.Path(wl_path).readlink())

    assert len(non_symlinks) == 0, "Non-symlink sudo-whitelist.conf found:\n" + "\n".join(non_symlinks)
    logger.info("[IMP:9][test_symlinks] All %d sudo-whitelist.conf are symlinks", len(WHITELIST_MODULES))


# ══════════════════════════════════════════════════════════════════════════════════════════════════
# Test 2: All symlinks point to the template (AC5)
# ══════════════════════════════════════════════════════════════════════════════════════════════════


# GUARD-PRESERVE (168): статический контракт AC5 (D11) — symlink-таргет обязан указывать на
# template; единственное покрытие целевого пути dedup-контракта
@ldd_trajectory
def test_sudo_whitelist_symlinks_point_to_template(
    caplog: pytest.LogCaptureFixture,
    platform_root: str,
) -> None:
    """
    # ▶ core/modules/*/sudo-whitelist.conf → ⊕ os.readlink == EXPECTED_SYMLINK_TARGET
    #   → ◇ mismatch? → ⎋ fail | pass
    """
    mismatches: list[str] = []
    for module in WHITELIST_MODULES:
        wl_path = _get_whitelist_path(platform_root, module)
        if not pathlib.Path(wl_path).is_symlink():
            mismatches.append(f"{module}/sudo-whitelist.conf: not a symlink")
            continue
        target = str(pathlib.Path(wl_path).readlink())
        if target != EXPECTED_SYMLINK_TARGET:
            mismatches.append(
                f"{module}/sudo-whitelist.conf: points to '{target}', expected '{EXPECTED_SYMLINK_TARGET}'"
            )
            logger.warning("[IMP:7][test_symlink_target] %s: target=%s", module, target)
        else:
            logger.info("[IMP:8][test_symlink_target] %s: correct target -> %s", module, target)

    assert len(mismatches) == 0, "Symlink target mismatches:\n" + "\n".join(mismatches)
    logger.info("[IMP:9][test_symlink_target] All symlinks point to %s", EXPECTED_SYMLINK_TARGET)


# ══════════════════════════════════════════════════════════════════════════════════════════════════
# Test 3: Template exists and is valid
# ══════════════════════════════════════════════════════════════════════════════════════════════════


@ldd_trajectory
def test_sudo_whitelist_template_exists_and_valid(
    caplog: pytest.LogCaptureFixture,
    platform_root: str,
) -> None:
    """
    # ▶ core/templates/sudo-whitelist.template → ⊕ exists + {{MODULE_NAME}} ≥4 +
    #   AUTO-GENERATED marker → ◇ missing? → ⎋ fail | pass
    """
    template_path = _get_template_path(platform_root)
    assert pathlib.Path(template_path).is_file(), f"Template not found: {template_path}"
    logger.info("[IMP:8][test_template] Template exists: %s", template_path)

    with pathlib.Path(template_path).open(encoding="utf-8") as f:
        content = f.read()

    # Check {{MODULE_NAME}} placeholders
    placeholder_count = content.count("{{MODULE_NAME}}")
    logger.info("[IMP:8][test_template] {{MODULE_NAME}} occurrences: %d", placeholder_count)
    assert placeholder_count >= MIN_MODULE_NAME_PLACEHOLDERS, (
        f"Template has only {placeholder_count} {{MODULE_NAME}} placeholders (expected ≥{MIN_MODULE_NAME_PLACEHOLDERS})"
    )

    # Check AUTO-GENERATED marker
    has_auto_generated = "AUTO-GENERATED" in content
    logger.info("[IMP:8][test_template] AUTO-GENERATED marker: %s", has_auto_generated)
    assert has_auto_generated, "Template missing AUTO-GENERATED marker"

    logger.info("[IMP:9][test_template] Template valid: %d placeholders, AUTO-GENERATED present", placeholder_count)


# ══════════════════════════════════════════════════════════════════════════════════════════════════
# Test 4: No static sudo-whitelist duplicates (comprehensive)
# ══════════════════════════════════════════════════════════════════════════════════════════════════


# GUARD-PRESERVE (168): статический контракт D11 (recursive-вариант) — НИ ОДНОГО static
# sudo-whitelist.conf в core/modules/; суперсет test_sudo_whitelist_all_symlinks (покрывает
# и файлы вне WHITELIST_MODULES) — единственное покрытие recursive-класса дефекта
@ldd_trajectory
def test_no_static_whitelist_duplicates(
    caplog: pytest.LogCaptureFixture,
    platform_root: str,
) -> None:
    """
    # ▶ core/modules/ recursive → ⊕ find files named sudo-whitelist.conf → ◇ non-symlink?
    #   → ⎋ fail | pass
    """
    modules_dir = Path(platform_root) / "core" / "modules"
    non_symlinks: list[str] = []

    # Walk through all directories under core/modules/ looking for sudo-whitelist.conf
    for root, _dirs, files in os.walk(modules_dir):
        for fname in files:
            if fname == "sudo-whitelist.conf":
                fpath = Path(root) / fname
                rel_path = os.path.relpath(fpath, platform_root)
                if not pathlib.Path(fpath).is_symlink():
                    non_symlinks.append(rel_path)
                    logger.warning("[IMP:7][test_no_duplicates] Static file: %s", rel_path)
                else:
                    logger.info(
                        "[IMP:8][test_no_duplicates] Symlink OK: %s -> %s", rel_path, pathlib.Path(fpath).readlink()
                    )

    assert len(non_symlinks) == 0, (
        f"Found {len(non_symlinks)} static sudo-whitelist.conf file(s) (not symlink):\n"
        + "\n".join(non_symlinks)
        + "\nAll sudo-whitelist.conf must be symlinks to ../../templates/sudo-whitelist.template"
    )
    logger.info("[IMP:9][test_no_duplicates] No static sudo-whitelist.conf files found")


# ══════════════════════════════════════════════════════════════════════════════════════════════════
# Test 5: Module whitelist equals actual listing of core/modules/ (строгий set-equality)
# ══════════════════════════════════════════════════════════════════════════════════════════════════


@ldd_trajectory
def test_module_whitelist_equals_listing(
    caplog: pytest.LogCaptureFixture,
    platform_root: str,
) -> None:
    """
    # ▶ core/modules/ → ⊕ os.listdir → ◇ diff with PLATFORM_MODULES?
    #   → ⎋ fail with diff | pass
    """
    modules_dir = Path(platform_root) / "core" / "modules"
    assert pathlib.Path(modules_dir).is_dir(), f"modules_dir not found: {modules_dir}"

    actual_dirs: set[str] = set()
    for entry in (p.name for p in modules_dir.iterdir()):
        entry_path = Path(modules_dir) / entry
        if pathlib.Path(entry_path).is_dir():
            actual_dirs.add(entry)

    logger.info("[IMP:8][test_module_whitelist] Actual module dirs (%d): %s", len(actual_dirs), sorted(actual_dirs))
    logger.info(
        "[IMP:8][test_module_whitelist] PLATFORM_MODULES  (%d): %s", len(PLATFORM_MODULES), sorted(PLATFORM_MODULES)
    )

    if actual_dirs == PLATFORM_MODULES:
        logger.info("[IMP:9][test_module_whitelist] ✅ Modules match: set(%s) == PLATFORM_MODULES", sorted(actual_dirs))
        return

    # Compute diffs for actionable error message
    missing_in_whitelist = actual_dirs - PLATFORM_MODULES
    extra_in_whitelist = PLATFORM_MODULES - actual_dirs

    msg_parts: list[str] = []
    if missing_in_whitelist:
        msg_parts.append(f"  Missing from PLATFORM_MODULES (add to constant): {sorted(missing_in_whitelist)}")
    if extra_in_whitelist:
        msg_parts.append(f"  Extra in PLATFORM_MODULES (remove from constant): {sorted(extra_in_whitelist)}")

    assert_msg = "core/modules/ listing != PLATFORM_MODULES:\n" + "\n".join(msg_parts)
    logger.error("[IMP:7][test_module_whitelist] %s", assert_msg)
    raise AssertionError(assert_msg)
