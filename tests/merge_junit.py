# GREP_SUMMARY: merge_junit junit xml report merge testsuite aggregation
# STRUCTURE: ▶ parse_args → ◇ for each input: parse XML → ⊕ aggregate attributes → ⊕ merge testcases → ⟦output XML⟧
# region MODULE_CONTRACT
## @purpose  Merge multiple JUnit XML report files into a single aggregated report.
##           Aggregates tests, errors, failures, skipped, time attributes and merges
##           all <testcase> elements from all inputs into a single <testsuite>.
## @scope    tests/merge_junit.py — stdlib-only (xml.etree.ElementTree + argparse)
## @invariants
##   - Missing input files are silently skipped (logged at IMP:7, continue)
##   - Exit 0 on success (including partial success with missing files)
##   - Exit 1 on output-write failure
##   - Exit 2 on no valid input files processed
##   - Output is a single <testsuite> with aggregated attributes and merged testcases
## @rationale JUnit XML reports from per-marker pytest runs need to be combined
##            for skip enforcement validation. Stdlib-only to avoid dependencies.
## @changes 2026-07-11 | Created per plan 1783753650648-junitxml-integration.md TASK-4
# endregion MODULE_CONTRACT

import argparse
import logging
import sys
import xml.etree.ElementTree as ET

logger = logging.getLogger(__name__)


def _aggregate_attributes(trees: list[ET.ElementTree]) -> dict[str, str]:
    """Aggregate numeric attributes from all testsuite elements.

    ## @purpose — Sum tests, errors, failures, skipped; sum time (float).
    ## @io — ⎋ dict with aggregated string attributes
    ## @complexity — O(N * S) where N = number of input files, S = testsuite count per file
    ## @rationale — Uses root.iter("testsuite") instead of root.get() because
    ##              pytest --junitxml wraps output in <testsuites>; attributes
    ##              (tests, errors, failures, skipped, time) live on child
    ##              <testsuite> elements, not on the wrapper.
    # ⚠️ TRAP[BUG] · 2026-07-11 · P1 · _aggregate_attributes read from wrong XML element
    # · Symptom: merged report.xml had tests=0, errors=0, failures=0, skipped=0 despite valid inputs
    # · Root: root.get("tests") reads from <testsuites> wrapper which has no attributes;
    # ·   pytest --junitxml wraps output in <testsuites>; attributes are on child <testsuite>
    # · Fix: iterate root.iter("testsuite") — consistent with _merge_testcases which already
    # ·   uses this pattern. Handles both <testsuites> wrapper and standalone <testsuite> root.
    # · Prevention: structural consistency rule — all aggregation functions in this module
    # ·   MUST use root.iter("testsuite") for attribute extraction.
    """
    total_tests = 0
    total_errors = 0
    total_failures = 0
    total_skipped = 0
    total_time = 0.0

    for tree in trees:
        root = tree.getroot()
        for testsuite in root.iter("testsuite"):
            total_tests += int(testsuite.get("tests", 0))
            total_errors += int(testsuite.get("errors", 0))
            total_failures += int(testsuite.get("failures", 0))
            total_skipped += int(testsuite.get("skipped", 0))
            total_time += float(testsuite.get("time", 0))

    return {
        "tests": str(total_tests),
        "errors": str(total_errors),
        "failures": str(total_failures),
        "skipped": str(total_skipped),
        "time": f"{total_time:.3f}",
    }


def _merge_testcases(trees: list[ET.ElementTree]) -> list[ET.Element]:
    """Collect all <testcase> elements from all input trees.

    ## @purpose — Extract every <testcase> preserving its children (skipped, failure, error).
    ## @io — ⎋ list of <testcase> Element objects
    ## @complexity — O(T) where T = total testcases across all inputs
    """
    testcases: list[ET.Element] = []
    for tree in trees:
        root = tree.getroot()
        # Handle both <testsuite> as root and <testsuites> wrapping
        for testsuite in root.iter("testsuite"):
            testcases.extend(testsuite.iter("testcase"))
        # Also handle direct <testcase> children of root (unlikely but defensive)
        for tc in root.iter("testcase"):
            if tc not in testcases:
                testcases.append(tc)
    return testcases


def merge_reports(input_paths: list[str], output_path: str) -> int:
    """Merge JUnit XML reports and write output.

    ## @purpose — Main merge orchestration: parse → aggregate → merge → write.
    ## @io — ⎋ int exit code (0=success, 1=write failure, 2=no valid inputs)
    ## @complexity — O(N * T) where N = input files, T = testcases per file
    """
    trees: list[ET.ElementTree] = []
    seen_count = 0

    for path in input_paths:
        try:
            tree = ET.parse(path)
            trees.append(tree)
            seen_count += 1
            logger.info("[IMP:7][merge] Loaded: %s", path)
        except FileNotFoundError:
            logger.warning("[IMP:7][merge] Missing input (skipped): %s", path)
        except ET.ParseError as e:
            logger.warning("[IMP:7][merge] Parse error (skipped) %s: %s", path, e)

    if not trees:
        logger.critical("[IMP:9][merge] No valid input files processed — nothing to merge")
        return 2

    # Aggregate attributes
    attrs = _aggregate_attributes(trees)
    attrs["name"] = "merged"

    # Merge testcases
    testcases = _merge_testcases(trees)

    # Build output tree
    root = ET.Element("testsuite", attrib=attrs)
    root.text = "\n  "
    root.tail = "\n"
    for tc in testcases:
        root.append(tc)
        tc.tail = "\n  "
    if testcases:
        testcases[-1].tail = "\n"

    tree_out = ET.ElementTree(root)
    try:
        # Write with XML declaration
        ET.indent(tree_out, space="  ")
        tree_out.write(output_path, encoding="utf-8", xml_declaration=True)
        logger.info(
            "[IMP:7][merge] Wrote merged report: %s (%s tests, %s testcases)",
            output_path,
            attrs["tests"],
            len(testcases),
        )
    except (OSError, PermissionError) as e:
        logger.critical("[IMP:9][merge] Failed to write output %s: %s", output_path, e)
        return 1

    logger.critical(
        "[IMP:9][merge] Merge complete: %d files → %s | tests=%s errors=%s failures=%s skipped=%s time=%s",
        seen_count,
        output_path,
        attrs["tests"],
        attrs["errors"],
        attrs["failures"],
        attrs["skipped"],
        attrs["time"],
    )
    return 0


def _parse_args(argv: list[str]) -> argparse.Namespace:
    """Parse command-line arguments.

    ## @purpose — Positional input files + -o/--output required flag.
    ## @io — ⎋ argparse.Namespace
    ## @complexity — O(1)
    """
    parser = argparse.ArgumentParser(
        description="Merge multiple JUnit XML report files into a single aggregated report.",
    )
    parser.add_argument("inputs", nargs="+", help="JUnit XML input files to merge")
    parser.add_argument("-o", "--output", required=True, help="Output merged JUnit XML file path")
    return parser.parse_args(argv)


def main() -> int:
    """Entry point.

    ## @purpose — Parse args, configure logging, call merge_reports.
    ## @io — ⎋ int exit code for sys.exit
    ## @complexity — O(N * T)
    """
    logging.basicConfig(
        level=logging.INFO,
        format="%(message)s",
        stream=sys.stderr,
    )
    args = _parse_args(sys.argv[1:])
    return merge_reports(args.inputs, args.output)


if __name__ == "__main__":
    sys.exit(main())
