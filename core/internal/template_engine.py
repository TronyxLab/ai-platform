#!/usr/bin/env python3
# GREP_SUMMARY: template-engine Python-core render check grammars placeholder {{UPPER_SNAKE}}
# STRUCTURE: ┌parse_vars→StrictGrammar RE┐ → ◇ render_template → ◇ render_all → ◇ check_all
# region MODULE_CONTRACT
## @purpose  Core template rendering engine with strict placeholder grammar {{UPPER_SNAKE}}
## @scope    Вызывается из bash-CLI (template-engine.sh), CI-gates, и тестов
## @invariants
##   - Placeholder grammar: {{[A-Z][A-Z0-9_]*}} — uppercase start, no spaces, no dollar sign
##   - All variables resolvable or explicit allow_missing=True
##   - Output is deterministic: render(template, vars) → всегда одинаковый вывод при одинаковых входах
##   - Atomic writes: пишет во временный файл, затем os.rename (не cross-filesystem)
##   - Strict grammar rejects {{lowercase}}, {{ $labels.x }}, and unclosed {{VAR
## @rationale Python core для нативной тестируемости (§TESTING). Strict grammar исключает
##            коллизию с Go-templating ({{ $labels.x }}) и Grafana ({{instance}}).
# endregion MODULE_CONTRACT

import os
import re
import sys
import tempfile
import logging
from typing import IO

try:
    import yaml
except ImportError:
    yaml = None  # type: ignore[assignment]

logger = logging.getLogger(__name__)

# ── Strict placeholder grammar ──────────────────────────────────────────────
# Only matches {{UPPER_SNAKE}} — uppercase start, uppercase+digits+underscore, no spaces, no $
PLACEHOLDER_RE = re.compile(r"\{\{([A-Z][A-Z0-9_]*)\}\}")


class TemplateError(Exception):
    """Raised on template rendering errors.

    Attributes:
        template_path: Path to the template file that caused the error.
        unresolved: List of unresolved placeholder names (if applicable).
        line_no: Line number where the error occurred (0 if unknown).
    """

    def __init__(
        self,
        message: str,
        *,
        template_path: str = "",
        unresolved: list[str] | None = None,
        line_no: int = 0,
    ):
        super().__init__(message)
        self.template_path = template_path
        self.unresolved = unresolved or []
        self.line_no = line_no


# region FUNC_PARSE_VARS
## @purpose  Parse KEY=val pairs from CLI arguments into a dictionary
## @io       Input: list of "KEY=value" strings → Output: dict[str, str]
## @complexity O(n) where n = number of var pairs
def parse_vars(var_pairs: list[str]) -> dict[str, str]:
    """Parse ``KEY=value`` pairs into a dictionary.

    Args:
        var_pairs: List of strings in ``KEY=value`` format, e.g. ``["A=1", "B=2"]``.

    Returns:
        Dictionary mapping keys to values.

    Raises:
        ValueError: If any pair is not in ``KEY=value`` format.

    Example:
        >>> parse_vars(["NAME=world", "X=42"])
        {'NAME': 'world', 'X': '42'}
    """
    result: dict[str, str] = {}
    for pair in var_pairs:
        if "=" not in pair:
            raise ValueError(f"Invalid variable format, expected KEY=value: {pair!r}")
        key, _, value = pair.partition("=")
        if not key:
            raise ValueError(f"Empty key in variable pair: {pair!r}")
        # Last value wins on duplicate
        result[key] = value
    return result


# endregion FUNC_PARSE_VARS

# region FUNC_RENDER_TEMPLATE
## @purpose  Render a template file substituting {{VAR}} placeholders
## @io       Input: template_path (file with {{VAR}}), output_path (optional), vars dict
##           Output: str (when output_path=None or dry_run=True) or None (when written to file)
## @complexity O(n * m) where n = file size, m = number of unique placeholders
def render_template(
    template_path: str,
    output_path: str | None = None,
    vars: dict[str, str] | None = None,
    *,
    allow_missing: bool = False,
    dry_run: bool = False,
) -> str | None:
    """Render a template file substituting ``{{VAR}}`` placeholders with values.

    Args:
        template_path: Path to the template file.
        output_path: If provided, write rendered output to this path atomically.
                     If ``None``, return the rendered string.
        vars: Dictionary of variable → value substitutions.
        allow_missing: If ``False`` (default), raise ``TemplateError`` on unresolved
                       placeholders. If ``True``, leave unresolved ``{{VAR}}`` as-is
                       and log a warning.
        dry_run: If ``True``, return the rendered string without writing to disk.
                 Implied when ``output_path`` is ``None``.

    Returns:
        Rendered string if ``output_path`` is ``None`` or ``dry_run=True``,
        ``None`` otherwise (successful write).

    Raises:
        TemplateError: On invalid grammar, binary content, unresolved placeholders,
                       or unclosed ``{{``.
        FileNotFoundError: If ``template_path`` does not exist.
        PermissionError: If output directory is not writable.
    """
    import logging

    log = logging.getLogger(__name__)
    vars = vars or {}

    # Resolve symlinks
    real_path = os.path.realpath(template_path)

    log.log(7, "[IMP:7][render_template] Reading template: %s", real_path)

    # Check file size for streaming threshold (>100MB)
    file_size = os.path.getsize(real_path)
    large_file = file_size > 100 * 1024 * 1024  # 100MB

    if large_file:
        log.log(8, "[IMP:8][render_template] Large template detected (%d bytes), streaming", file_size)
        content = _read_large_file(real_path)
    else:
        with open(real_path, "rb") as f:
            raw = f.read()
        # Check for binary content (null byte detection)
        if b"\x00" in raw:
            raise TemplateError(
                "binary content detected",
                template_path=template_path,
            )
        content = raw.decode("utf-8")

    # Check for unclosed {{ (starts without matching }})
    _check_unclosed(content, template_path)

    # Parse and replace placeholders using strict grammar
    unresolved: list[str] = []
    line_no = 0

    def _replacer(m: re.Match) -> str:
        nonlocal line_no
        var_name = m.group(1)
        # Track approximate line number
        line_no = content[: m.start()].count("\n") + 1

        if var_name in vars:
            return vars[var_name]
        unresolved.append(var_name)
        if not allow_missing:
            raise TemplateError(
                f"unresolved placeholder: {m.group(0)}",
                template_path=template_path,
                unresolved=[var_name],
                line_no=line_no,
            )
        # Log warning and leave placeholder as-is
        log.warning(
            "[IMP:6][render_template] WARNING: unresolved placeholder %s in %s (line %d)",
            m.group(0),
            template_path,
            line_no,
        )
        return m.group(0)

    rendered = PLACEHOLDER_RE.sub(_replacer, content)

    if unresolved and allow_missing:
        log.log(
            6,
            "[IMP:6][render_template] %d unresolved placeholders allowed in %s: %s",
            len(unresolved),
            template_path,
            ", ".join(unresolved),
        )

    log.log(9, "[IMP:9][render_template] Render complete: %s", real_path)

    # Dry-run or output_path=None → return string
    if dry_run or output_path is None:
        return rendered

    # Atomic write: write to temp file, then os.rename
    _atomic_write(rendered, output_path)

    log.log(8, "[IMP:8][render_template] Written to: %s", output_path)
    return None


# endregion FUNC_RENDER_TEMPLATE

# region FUNC_CHECK_UNCLOSED
def _check_unclosed(content: str, template_path: str) -> None:
    """Check for unclosed ``{{`` placeholders.

    Raises TemplateError if any ``{{`` is found without a matching ``}}``.
    """
    pos = 0
    while True:
        start = content.find("{{", pos)
        if start == -1:
            break
        end = content.find("}}", start + 2)
        if end == -1:
            line = content[:start].count("\n") + 1
            raise TemplateError(
                "unclosed placeholder",
                template_path=template_path,
                line_no=line,
            )
        pos = end + 2


# endregion FUNC_CHECK_UNCLOSED

# region FUNC_READ_LARGE_FILE
def _read_large_file(path: str, chunk_size: int = 64 * 1024) -> str:
    """Read a large file (>100MB) in chunks to avoid memory detonation."""
    parts: list[str] = []
    null_detected = False
    with open(path, "rb") as f:
        while True:
            chunk = f.read(chunk_size)
            if not chunk:
                break
            if b"\x00" in chunk:
                null_detected = True
                break
            parts.append(chunk.decode("utf-8"))
    if null_detected:
        raise TemplateError("binary content detected", template_path=path)
    return "".join(parts)


# endregion FUNC_READ_LARGE_FILE

# region FUNC_ATOMIC_WRITE
def _atomic_write(content: str, output_path: str) -> None:
    """Write content atomically via temp file + os.rename.

    Uses the same directory as output_path to ensure same-filesystem rename.
    """
    import os
    import tempfile

    dir_name = os.path.dirname(os.path.abspath(output_path))
    os.makedirs(dir_name, exist_ok=True)

    fd, tmp_path = tempfile.mkstemp(
        prefix=".tmp_",
        suffix=".rendered",
        dir=dir_name,
    )
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as tmp:
            tmp.write(content)
        os.rename(tmp_path, output_path)
    except BaseException:
        # Clean up temp file on any failure
        try:
            os.unlink(tmp_path)
        except OSError:
            pass
        raise


# endregion FUNC_ATOMIC_WRITE

# region FUNC_RENDER_ALL
## @purpose  Read template-manifest.yaml and render all entries
## @io       Input: manifest_path → reads YAML, iterates all templates[], renders each
##           Output: int — 0 on success, error count on partial failure
## @complexity O(t * n) where t = number of templates, n = avg file size
def render_all(
    manifest_path: str,
    *,
    extra_vars: dict[str, str] | None = None,
    dry_run: bool = False,
) -> int:
    """Read the template manifest and render all entries.

    Args:
        manifest_path: Path to ``template-manifest.yaml``.
        extra_vars: Additional variables merged into each template's vars.
        dry_run: If ``True``, render in-memory without writing.

    Returns:
        Number of errors encountered (0 = all OK).

    Raises:
        FileNotFoundError: If manifest_path does not exist.
        yaml.YAMLError: If manifest is not valid YAML.
    """
    log = logging.getLogger(__name__)
    log.log(7, "[IMP:7][render_all] Reading manifest: %s", manifest_path)

    if not os.path.exists(manifest_path):
        raise FileNotFoundError(f"Manifest not found: {manifest_path}")

    if yaml is None:
        raise ImportError("PyYAML is required for manifest support")

    with open(manifest_path, "r") as f:
        manifest = yaml.safe_load(f)

    if not manifest or "templates" not in manifest:
        log.log(8, "[IMP:8][render_all] No templates in manifest")
        return 0

    standard_vars: dict[str, str] = {}
    std_defs = manifest.get("standard_vars", {})
    for var_name, var_def in std_defs.items():
        default = var_def.get("default")
        if default is not None:
            standard_vars[var_name] = str(default)
        # Try env
        resolve_from = var_def.get("resolve_from", [])
        for source in resolve_from:
            if source.startswith("env."):
                env_key = source[4:]
                env_val = os.environ.get(env_key)
                if env_val:
                    standard_vars[var_name] = env_val
                    break

    errors = 0
    for entry in manifest["templates"]:
        tmpl_path = entry["template"]
        output = entry.get("output")
        entry_type = entry.get("type", "single")

        # Merge vars: standard vars + entry-specific vars + extra_vars
        merged_vars = dict(standard_vars)
        entry_vars = entry.get("vars", {})
        for var_name, var_def in entry_vars.items():
            if isinstance(var_def, dict):
                required = var_def.get("required", False)
                default = var_def.get("default")
                if default is not None:
                    merged_vars[var_name] = str(default)
                # Source from env if configured
                source = var_def.get("source")
                if source == "env":
                    env_val = os.environ.get(var_name)
                    if env_val:
                        merged_vars[var_name] = env_val
            else:
                # Simple string value
                merged_vars[var_name] = str(var_def)

        # Merge extra_vars (CLI overrides)
        if extra_vars:
            for k, v in extra_vars.items():
                if v is not None:
                    merged_vars[k] = v

        # Determine which vars are required (not optional) when allow_missing=False
        required_vars_list: list[str] = []
        for var_name, var_def in entry_vars.items():
            if isinstance(var_def, dict):
                if var_def.get("required", True):
                    required_vars_list.append(var_name)
            else:
                # Simple string — assumed required
                required_vars_list.append(var_name)

        # Resolve template path relative to manifest dir
        manifest_dir = os.path.dirname(os.path.abspath(manifest_path))
        abs_tmpl_path = os.path.join(manifest_dir, tmpl_path) if not os.path.isabs(tmpl_path) else tmpl_path

        try:
            if entry_type == "directory":
                if dry_run:
                    log.log(8, "[IMP:8][render_all] DRY-RUN directory: %s", abs_tmpl_path)
                else:
                    log.log(7, "[IMP:7][render_all] Rendering directory: %s", abs_tmpl_path)
                    if not os.path.isdir(abs_tmpl_path):
                        log.log(8, "[IMP:8][render_all] Directory not found: %s", abs_tmpl_path)
                        errors += 1
                        continue
                    _render_directory(abs_tmpl_path, merged_vars, dry_run=dry_run)
            else:
                allow_missing = not required_vars_list
                render_template(
                    abs_tmpl_path,
                    output_path=output if not dry_run else None,
                    vars=merged_vars,
                    allow_missing=allow_missing,
                    dry_run=dry_run,
                )
        except (TemplateError, FileNotFoundError, PermissionError) as e:
            log.log(9, "[IMP:9][render_all] ERROR rendering %s: %s", tmpl_path, e)
            errors += 1

    status = "OK" if errors == 0 else f"FAIL({errors})"
    log.log(9, "[IMP:9][render_all] Render complete: %s", status)
    return errors


# endregion FUNC_RENDER_ALL

# region FUNC_RENDER_DIRECTORY
def _render_directory(dir_path: str, vars: dict[str, str], *, dry_run: bool = False) -> None:
    """Recursively render all text files in a directory.

    Used for project template directories (template-backend/, template-frontend/, etc.).
    """
    import logging

    log = logging.getLogger(__name__)
    for root, _dirs, files in os.walk(dir_path):
        for fname in files:
            fpath = os.path.join(root, fname)
            # Skip binary files
            try:
                with open(fpath, "rb") as f:
                    head = f.read(1024)
                    if b"\x00" in head:
                        log.log(6, "[IMP:6][render_directory] Skipping binary: %s", fpath)
                        continue
            except OSError:
                continue
            try:
                render_template(fpath, vars=vars, allow_missing=True, dry_run=dry_run)
            except TemplateError as e:
                log.log(8, "[IMP:8][render_directory] Template error in %s: %s", fpath, e)


# endregion FUNC_RENDER_DIRECTORY

# region FUNC_CHECK_ALL
## @purpose  Dry-run render of all manifest entries; return diagnostics
## @io       Input: manifest_path → Output: (success, diagnostics_list)
## @complexity O(t * n) where t = number of templates
def check_all(
    manifest_path: str,
    *,
    extra_vars: dict[str, str] | None = None,
) -> tuple[bool, list[str]]:
    """Dry-run render all entries in the manifest and return diagnostics.

    Args:
        manifest_path: Path to ``template-manifest.yaml``.
        extra_vars: Additional variables for rendering.

    Returns:
        Tuple of ``(all_ok, diagnostics)`` where ``diagnostics`` is a list of
        ``"OK: path"`` or ``"UNRESOLVED: path: {{VAR}}"`` strings.
    """
    log = logging.getLogger(__name__)
    log.log(7, "[IMP:7][check_all] Checking manifest: %s", manifest_path)

    if not os.path.exists(manifest_path):
        raise FileNotFoundError(f"Manifest not found: {manifest_path}")

    if yaml is None:
        raise ImportError("PyYAML is required for manifest support")

    with open(manifest_path, "r") as f:
        manifest = yaml.safe_load(f)

    if not manifest or "templates" not in manifest:
        return True, ["No templates in manifest"]

    diagnostics: list[str] = []
    errors = 0

    for entry in manifest["templates"]:
        tmpl_path = entry["template"]
        entry_vars_def = entry.get("vars", {})
        entry_type = entry.get("type", "single")

        # Build test vars with defaults
        test_vars: dict[str, str] = {}
        for var_name, var_def in entry_vars_def.items():
            if isinstance(var_def, dict):
                default = var_def.get("default")
                if default is not None:
                    test_vars[var_name] = str(default)
                elif not var_def.get("required", True):
                    # Optional, no default — skip (allow_missing will handle)
                    pass
                else:
                    # Required, no default — use placeholder name as test value
                    test_vars[var_name] = f"<{var_name}>"
            else:
                test_vars[var_name] = str(var_def)

        # Merge extra_vars
        if extra_vars:
            for k, v in extra_vars.items():
                if v is not None:
                    test_vars[k] = v

        manifest_dir = os.path.dirname(os.path.abspath(manifest_path))
        abs_tmpl_path = os.path.join(manifest_dir, tmpl_path) if not os.path.isabs(tmpl_path) else tmpl_path

        try:
            if entry_type == "directory":
                if not os.path.isdir(abs_tmpl_path):
                    diagnostics.append(f"UNRESOLVED: {tmpl_path}: directory not found")
                    errors += 1
                    continue
                diagnostics.append(f"OK: {tmpl_path} (directory)")
            else:
                result = render_template(
                    abs_tmpl_path,
                    vars=test_vars,
                    allow_missing=True,
                    dry_run=True,
                )
                if result is not None:
                    # Check for leftover unresolved placeholders
                    unresolved_found = PLACEHOLDER_RE.findall(result)
                    if unresolved_found:
                        diagnostics.append(
                            f"UNRESOLVED: {tmpl_path}: {', '.join(f'{{{{{v}}}}}' for v in unresolved_found)}"
                        )
                        errors += 1
                    else:
                        diagnostics.append(f"OK: {tmpl_path}")
                else:
                    diagnostics.append(f"OK: {tmpl_path}")
        except (TemplateError, FileNotFoundError, PermissionError) as e:
            diagnostics.append(f"UNRESOLVED: {tmpl_path}: {e}")
            errors += 1

    log.log(9, "[IMP:9][check_all] Check complete: %d errors", errors)
    return errors == 0, diagnostics


# endregion FUNC_CHECK_ALL

# region FUNC_RENDER_DIRECTORY_IN_PLACE
## @purpose  Render all text files in a directory in-place, writing rendered output back.
## @io       Input: dir_path (directory tree), vars (substitutions dict)
##           Output: int — 0 on success, error count on partial failure
## @complexity O(n) where n = number of files in directory tree
## @rationale Used by scaffold (add-project.sh) after copying template to project dir.
##            Unlike _render_directory() (dry-run only for manifest check), this actually writes.
def render_directory_in_place(
    dir_path: str,
    vars: dict[str, str] | None = None,
) -> int:
    """Render all text files in a directory in-place, substituting ``{{VAR}}`` placeholders.

    Reads each text file, replaces placeholders using the provided vars, and writes
    the rendered content back atomically. Binary files are skipped automatically.
    Unresolved placeholders are left as-is (``allow_missing=True``).

    Args:
        dir_path: Directory to traverse recursively.
        vars: Dictionary of variable → value substitutions.

    Returns:
        Number of errors encountered (0 = all OK).
    """
    log = logging.getLogger(__name__)
    vars = vars or {}
    errors = 0

    log.log(7, "[IMP:7][render_directory_in_place] Rendering directory: %s", dir_path)

    if not os.path.isdir(dir_path):
        log.log(9, "[IMP:9][render_directory_in_place] Path not found: %s", dir_path)
        return 1

    for root, _dirs, files in os.walk(dir_path):
        for fname in files:
            fpath = os.path.join(root, fname)
            # Skip binary files
            try:
                with open(fpath, "rb") as f:
                    head = f.read(1024)
                    if b"\x00" in head:
                        log.log(6, "[IMP:6][render_directory_in_place] Skipping binary: %s", fpath)
                        continue
            except OSError:
                continue
            try:
                render_template(
                    fpath,
                    output_path=fpath,
                    vars=vars,
                    allow_missing=True,
                )
            except TemplateError as e:
                log.log(8, "[IMP:8][render_directory_in_place] Template error in %s: %s", fpath, e)
                errors += 1
            except (FileNotFoundError, PermissionError) as e:
                log.log(8, "[IMP:8][render_directory_in_place] Error in %s: %s", fpath, e)
                errors += 1

    status = "OK" if errors == 0 else f"FAIL({errors})"
    log.log(9, "[IMP:9][render_directory_in_place] Render complete: %s", status)
    return errors


# endregion FUNC_RENDER_DIRECTORY_IN_PLACE

# region CLI_MAIN
def main() -> None:
    """CLI entry point for direct use (not through bash wrapper).

    Usage::
        python3 -m core.internal.template_engine render <template> [output] [VAR=val ...]
        python3 -m core.internal.template_engine render-all [--manifest PATH] [VAR=val ...]
        python3 -m core.internal.template_engine check [--manifest PATH] [--verbose]
    """
    import logging

    logging.basicConfig(
        level=logging.WARNING,
        format="[IMP:%(levelno)s][%(name)s] %(message)s",
        stream=sys.stderr,
    )

    args = sys.argv[1:]
    if not args:
        print("Usage: template_engine.py <render|render-all|check> [...]", file=sys.stderr)
        sys.exit(2)

    command = args[0]
    rest = args[1:]

    try:
        if command == "render":
            if len(rest) < 1:
                print("Usage: template_engine.py render <template> [output] [VAR=val ...]", file=sys.stderr)
                sys.exit(2)
            tmpl = rest[0]
            output = rest[1] if len(rest) > 1 and "=" not in rest[1] else None
            var_start = 2 if output is not None else 1
            var_pairs = rest[var_start:]
            vars_dict = parse_vars(var_pairs) if var_pairs else {}
            result = render_template(tmpl, output_path=output, vars=vars_dict)
            if result is not None and output is None:
                print(result)
        elif command == "render-all":
            manifest = "core/templates/template-manifest.yaml"
            var_pairs: list[str] = []
            i = 0
            while i < len(rest):
                if rest[i] == "--manifest" and i + 1 < len(rest):
                    manifest = rest[i + 1]
                    i += 2
                elif "=" in rest[i]:
                    var_pairs.append(rest[i])
                    i += 1
                else:
                    i += 1
            extra = parse_vars(var_pairs) if var_pairs else None
            errors = render_all(manifest, extra_vars=extra)
            sys.exit(min(errors, 127))
        elif command == "render-dir":
            if len(rest) < 1:
                print("Usage: template_engine.py render-dir <directory> [VAR=val ...]", file=sys.stderr)
                sys.exit(2)
            dir_path = rest[0]
            var_pairs = rest[1:]
            vars_dict = parse_vars(var_pairs) if var_pairs else {}
            errors = render_directory_in_place(dir_path, vars_dict)
            sys.exit(min(errors, 127))
        elif command == "check":
            manifest = "core/templates/template-manifest.yaml"
            verbose = False
            i = 0
            while i < len(rest):
                if rest[i] == "--manifest" and i + 1 < len(rest):
                    manifest = rest[i + 1]
                    i += 2
                elif rest[i] == "--verbose":
                    verbose = True
                    i += 1
                else:
                    i += 1
            ok, diagnostics = check_all(manifest)
            if verbose:
                for line in diagnostics:
                    print(line)
            sys.exit(0 if ok else 1)
        else:
            print(f"Unknown command: {command}", file=sys.stderr)
            sys.exit(2)
    except (TemplateError, FileNotFoundError, PermissionError, ValueError) as e:
        print(f"ERROR: {e}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
# endregion CLI_MAIN
