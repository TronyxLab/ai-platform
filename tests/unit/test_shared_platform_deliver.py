#!/usr/bin/env python3
# GREP_SUMMARY: test-platform-deliver build-deliver-command parse-deliver-args forced-command verb
# STRUCTURE: ▶ build tests (org/project combinations) → ◇ parse tests (tokens, spaces, empty) → ⊕ ValueError edge-cases
# region MODULE_CONTRACT
## @purpose  Unit tests for core/internal/shared/platform_deliver.py
##           Verifies build_deliver_command() and parse_deliver_args() behavior.
## @scope    Tests: build with/without org, org=None, empty project; parse two/one token, spaces, empty input.
## @invariants
##   - All tests use native imports (no subprocess, no Docker)
##   - LDD: at least one IMP:9 log in each successful scenario
# endregion MODULE_CONTRACT

import logging

import pytest

from core.internal.shared.platform_deliver import build_deliver_command, parse_deliver_args

# ── build_deliver_command tests ───────────────────────────────────────────────


# region FUNC_test_build_with_org
## @purpose — Verify build_deliver_command returns "platform-deliver {org} {project}".
##            AC: org="myorg", project="myproj" → "platform-deliver myorg myproj".
## @complexity — O(1)
def test_build_with_org(caplog: pytest.LogCaptureFixture) -> None:
    """Build with org and project produces two-token format."""
    caplog.set_level(logging.INFO)

    # 🧪 TRAP[TEST] · Regression · Scenario: build with org+project
    # · Last fail: N/A (new test)
    # · Remove if: build_deliver_command signature changes

    result = build_deliver_command(org="myorg", project="myproj")

    # LDD trajectory
    found_imp9 = False
    print("--- LDD TRAJECTORY (IMP:7-10) ---")
    for record in caplog.records:
        if "[IMP:" in record.message:
            imp_level = int(record.message.split("[IMP:")[1].split("]")[0])
            if imp_level >= 7:
                print(record.message)
            if imp_level >= 9:
                found_imp9 = True
    print("--- END LDD TRAJECTORY ---")

    assert result == "platform-deliver myorg myproj", f"Expected 'platform-deliver myorg myproj', got {result!r}"
    assert found_imp9, "Critical LDD Error: No IMP:9 business logic log found"


# endregion FUNC_test_build_with_org


# region FUNC_test_build_without_org
## @purpose — Verify build_deliver_command without org produces single-token format.
##            AC: org="", project="myproj" → "platform-deliver myproj".
## @complexity — O(1)
def test_build_without_org(caplog: pytest.LogCaptureFixture) -> None:
    """Build without org produces single-token format."""
    caplog.set_level(logging.INFO)

    # 🧪 TRAP[TEST] · Regression · Scenario: build without org (empty string)
    # · Last fail: N/A (new test)
    # · Remove if: build_deliver_command signature changes

    result = build_deliver_command(org="", project="myproj")

    # LDD trajectory
    found_imp9 = False
    print("--- LDD TRAJECTORY (IMP:7-10) ---")
    for record in caplog.records:
        if "[IMP:" in record.message:
            imp_level = int(record.message.split("[IMP:")[1].split("]")[0])
            if imp_level >= 7:
                print(record.message)
            if imp_level >= 9:
                found_imp9 = True
    print("--- END LDD TRAJECTORY ---")

    assert result == "platform-deliver myproj", f"Expected 'platform-deliver myproj', got {result!r}"
    assert found_imp9, "Critical LDD Error: No IMP:9 business logic log found"


# endregion FUNC_test_build_without_org


# region FUNC_test_build_org_none
## @purpose — Verify build_deliver_command with org=None treats it as falsy → single-token.
##            AC: org=None, project="myproj" → "platform-deliver myproj".
## @complexity — O(1)
def test_build_org_none(caplog: pytest.LogCaptureFixture) -> None:
    """Build with org=None produces single-token format (None is falsy)."""
    caplog.set_level(logging.INFO)

    # 🧪 TRAP[TEST] · Regression · Scenario: build with org=None
    # · Last fail: N/A (new test)
    # · Remove if: build_deliver_command signature changes

    result = build_deliver_command(org=None, project="myproj")  # type: ignore[arg-type]

    # LDD trajectory
    found_imp9 = False
    print("--- LDD TRAJECTORY (IMP:7-10) ---")
    for record in caplog.records:
        if "[IMP:" in record.message:
            imp_level = int(record.message.split("[IMP:")[1].split("]")[0])
            if imp_level >= 7:
                print(record.message)
            if imp_level >= 9:
                found_imp9 = True
    print("--- END LDD TRAJECTORY ---")

    assert result == "platform-deliver myproj", f"Expected 'platform-deliver myproj', got {result!r}"
    assert found_imp9, "Critical LDD Error: No IMP:9 business logic log found"


# endregion FUNC_test_build_org_none


# region FUNC_test_build_empty_project
## @purpose — Verify build_deliver_command raises ValueError for empty project.
##            AC: project="" → ValueError with message "project must be non-empty".
## @complexity — O(1)
def test_build_empty_project(caplog: pytest.LogCaptureFixture) -> None:
    """Build with empty project raises ValueError."""
    caplog.set_level(logging.INFO)

    # 🧪 TRAP[TEST] · Regression · Scenario: build with empty project
    # · Last fail: N/A (new test)
    # · Remove if: build_deliver_command changes empty-project behavior

    with pytest.raises(ValueError, match="project must be non-empty"):
        build_deliver_command(org="myorg", project="")

    # No IMP:9 expected since the function raises before logging
    print("--- LDD TRAJECTORY (IMP:7-10) ---")
    for record in caplog.records:
        if "[IMP:" in record.message:
            imp_level = int(record.message.split("[IMP:")[1].split("]")[0])
            if imp_level >= 7:
                print(record.message)
    print("--- END LDD TRAJECTORY ---")


# endregion FUNC_test_build_empty_project


# ── parse_deliver_args tests ─────────────────────────────────────────────────


# region FUNC_test_parse_two_tokens
## @purpose — Verify parse_deliver_args returns (org, project) for two-token input.
##            AC: "myorg myproj" → ("myorg", "myproj").
## @complexity — O(1)
def test_parse_two_tokens(caplog: pytest.LogCaptureFixture) -> None:
    """Parse two tokens returns (org, project)."""
    caplog.set_level(logging.INFO)

    # 🧪 TRAP[TEST] · Regression · Scenario: parse two tokens
    # · Last fail: N/A (new test)
    # · Remove if: parse_deliver_args signature changes

    org, project = parse_deliver_args("myorg myproj")

    # LDD trajectory
    found_imp9 = False
    print("--- LDD TRAJECTORY (IMP:7-10) ---")
    for record in caplog.records:
        if "[IMP:" in record.message:
            imp_level = int(record.message.split("[IMP:")[1].split("]")[0])
            if imp_level >= 7:
                print(record.message)
            if imp_level >= 9:
                found_imp9 = True
    print("--- END LDD TRAJECTORY ---")

    assert org == "myorg", f"Expected org='myorg', got {org!r}"
    assert project == "myproj", f"Expected project='myproj', got {project!r}"
    assert found_imp9, "Critical LDD Error: No IMP:9 business logic log found"


# endregion FUNC_test_parse_two_tokens


# region FUNC_test_parse_one_token
## @purpose — Verify parse_deliver_args returns ("", project) for single-token input.
##            AC: "myproj" → ("", "myproj").
## @complexity — O(1)
def test_parse_one_token(caplog: pytest.LogCaptureFixture) -> None:
    """Parse one token returns ('', project)."""
    caplog.set_level(logging.INFO)

    # 🧪 TRAP[TEST] · Regression · Scenario: parse one token (legacy format)
    # · Last fail: N/A (new test)
    # · Remove if: parse_deliver_args signature changes

    org, project = parse_deliver_args("myproj")

    # LDD trajectory
    found_imp9 = False
    print("--- LDD TRAJECTORY (IMP:7-10) ---")
    for record in caplog.records:
        if "[IMP:" in record.message:
            imp_level = int(record.message.split("[IMP:")[1].split("]")[0])
            if imp_level >= 7:
                print(record.message)
            if imp_level >= 9:
                found_imp9 = True
    print("--- END LDD TRAJECTORY ---")

    assert org == "", f"Expected org='', got {org!r}"
    assert project == "myproj", f"Expected project='myproj', got {project!r}"
    assert found_imp9, "Critical LDD Error: No IMP:9 business logic log found"


# endregion FUNC_test_parse_one_token


# region FUNC_test_parse_with_spaces
## @purpose — Verify parse_deliver_args strips leading/trailing/multiple spaces.
##            AC: "  myorg   myproj  " → ("myorg", "myproj").
## @complexity — O(1)
def test_parse_with_spaces(caplog: pytest.LogCaptureFixture) -> None:
    """Parse with extra whitespace strips and splits correctly."""
    caplog.set_level(logging.INFO)

    # 🧪 TRAP[TEST] · Regression · Scenario: parse with irregular whitespace
    # · Last fail: N/A (new test)
    # · Remove if: parse_deliver_args changes whitespace handling

    org, project = parse_deliver_args("  myorg   myproj  ")

    # LDD trajectory
    found_imp9 = False
    print("--- LDD TRAJECTORY (IMP:7-10) ---")
    for record in caplog.records:
        if "[IMP:" in record.message:
            imp_level = int(record.message.split("[IMP:")[1].split("]")[0])
            if imp_level >= 7:
                print(record.message)
            if imp_level >= 9:
                found_imp9 = True
    print("--- END LDD TRAJECTORY ---")

    assert org == "myorg", f"Expected org='myorg', got {org!r}"
    assert project == "myproj", f"Expected project='myproj', got {project!r}"
    assert found_imp9, "Critical LDD Error: No IMP:9 business logic log found"


# endregion FUNC_test_parse_with_spaces


# region FUNC_test_parse_empty
## @purpose — Verify parse_deliver_args raises ValueError for empty input.
##            AC: "" → ValueError with message "args must be non-empty".
## @complexity — O(1)
def test_parse_empty(caplog: pytest.LogCaptureFixture) -> None:
    """Parse empty string raises ValueError."""
    caplog.set_level(logging.INFO)

    # 🧪 TRAP[TEST] · Regression · Scenario: parse empty input
    # · Last fail: N/A (new test)
    # · Remove if: parse_deliver_args changes empty-input behavior

    with pytest.raises(ValueError, match="args must be non-empty"):
        parse_deliver_args("")

    # No IMP:9 expected since the function raises before logging
    print("--- LDD TRAJECTORY (IMP:7-10) ---")
    for record in caplog.records:
        if "[IMP:" in record.message:
            imp_level = int(record.message.split("[IMP:")[1].split("]")[0])
            if imp_level >= 7:
                print(record.message)
    print("--- END LDD TRAJECTORY ---")


# endregion FUNC_test_parse_empty
