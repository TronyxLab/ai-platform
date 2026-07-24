# GREP_SUMMARY: config_renderer, litellm-config, Jinja2, policy.yaml, model_list, fallbacks, --check, CLI, LDD
# STRUCTURE: ▶ parse_args() → ◇ load_policy(path) → ◇ build_model_list(policy) → ◇ build_fallbacks(policy) →
#            ◇ render_jinja2(template, data) → ◇ write_output(content, path) → ⎋ exit_code
# region MODULE_CONTRACT
## @purpose  Render litellm-config.yml from policy.yaml using a Jinja2 template.
##           Provides CLI: --policy PATH, --output PATH, --check (dry-run diff).
##           Part of DevPlan 049 Phase 3 — Config Renderer.
## @scope    Loads policy.yaml via LLMPolicy.from_yaml(), builds model_list and fallbacks
##           from active aliases, renders through Jinja2 template to produce
##           /core/modules/litellm/config/litellm-config.yml.
## @invariants
##   - Only aliases with non-empty deployments (DeploymentList with primary/fallback) are rendered
##   - Reserved aliases (empty deployments list) are SKIPPED — not in model_list
##   - model_name = alias name for primary, alias name + "-fallback" for fallback
##   - api_key = "os.environ/<provider.key_env>" resolved from provider definition
##   - model_info.mode = first alias.features entry
##   - Fallbacks chain primary → fallback for each alias with a fallback deployment
##   --check mode: renders to temp, compares byte-for-byte with output file, exit 0 if fresh
## @rationale Python Jinja2 rendering ensures type safety, testability, and
##            consistent output compared to manual YAML editing. --check mode
##            enables CI gate for manifest freshness (make check-manifests).
## @changes — 2026-07-24 | Created (DevPlan 049 Phase 3)
## 🧐 TRAP[DECISION] · 2026-07-24 · — · model_name + "-fallback" suffix for fallback deployments
## · Rejected: separate alias per fallback (would duplicate model_list entries with different names)
## · Reason: LiteLLM fallbacks reference model_name from model_list. Using "-fallback" suffix
## · ensures unique model_name for each fallback entry. LiteLLM resolves fallback by model_name match.
## · Rev: if LiteLLM adds native fallback-group support, simplify to a single model_list entry.
# endregion MODULE_CONTRACT

import argparse
import logging
import pathlib
import sys
import tempfile

# ── Project root resolution (must precede core.* imports) ─────────────────────
# config_renderer.py is at core/internal/llm/config_renderer.py
# Project root = 4 levels up
_PROJECT_ROOT = pathlib.Path(__file__).resolve().parent.parent.parent.parent
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

import yaml
from jinja2 import Environment, FileSystemLoader

from core.internal.llm.policy_schema import DeploymentList, LLMPolicy

logger = logging.getLogger(__name__)

# ── Template resolution ──────────────────────────────────────────────────────

# Template path relative to project root
_TEMPLATE_REL_PATH = pathlib.Path("core") / "modules" / "litellm" / "config" / "litellm-config.yml.j2"

# ── Data builders ────────────────────────────────────────────────────────────


def _build_model_list(policy: LLMPolicy) -> list[dict]:
    """Build model_list entries from active policy aliases.

    ## @purpose  Iterate aliases with non-empty deployments and build
    ##           litellm model_list entries. Reserved aliases (empty list)
    ##           are skipped — not rendered.
    ## @io
    ##   - policy: LLMPolicy — validated policy instance
    ##   - ⎋ list[dict] — model_list entries, each with name, litellm_model,
    ##                    api_key, access_group, mode
    ## @complexity O(A * D) where A = alias count, D = max 2 (primary + fallback)
    ## @invariants
    ##   - Reserved aliases (empty deployments list) produce zero entries
    ##   - Alias with only primary → 1 entry
    ##   - Alias with primary + fallback → 2 entries
    ##   - mode = first feature from alias.features (e.g. "reasoning", "chat")
    """
    model_list: list[dict] = []
    logger.log(logging.INFO, "[IMP:8][_build_model_list] Building model_list from %d aliases", len(policy.aliases))

    for alias_name, alias in policy.aliases.items():
        deployments = alias.deployments
        if isinstance(deployments, list) and len(deployments) == 0:
            # Reserved alias — skip
            logger.log(
                logging.DEBUG, "[IMP:6][_build_model_list] Skipping reserved alias '%s' (empty deployments)", alias_name
            )
            continue

        if isinstance(deployments, DeploymentList):
            # Determine mode from first feature
            mode = alias.features[0] if alias.features else "chat"
            logger.log(
                logging.INFO, "[IMP:8][_build_model_list] Processing alias '%s' with mode='%s'", alias_name, mode
            )

            # Primary deployment
            if deployments.primary is not None:
                provider_name = deployments.primary.provider
                provider = policy.providers.get(provider_name)
                if not provider:
                    logger.log(
                        logging.CRITICAL,
                        "[IMP:10][_build_model_list] Provider '%s' for alias '%s' not found",
                        provider_name,
                        alias_name,
                    )
                    raise ValueError(f"Provider '{provider_name}' for alias '{alias_name}' not found")
                api_key = f"os.environ/{provider.key_env}"
                entry = {
                    "name": alias_name,
                    "litellm_model": f"{provider_name}/{deployments.primary.model}",
                    "api_key": api_key,
                    "access_groups": [alias_name],
                    "mode": mode,
                }
                model_list.append(entry)
                logger.log(
                    logging.DEBUG,
                    "[IMP:7][_build_model_list] Added primary entry: name='%s', model='%s'",
                    alias_name,
                    deployments.primary.model,
                )

            # Fallback deployment
            if deployments.fallback is not None:
                provider_name = deployments.fallback.provider
                provider = policy.providers.get(provider_name)
                if not provider:
                    logger.log(
                        logging.CRITICAL,
                        "[IMP:10][_build_model_list] Fallback provider '%s' for alias '%s' not found",
                        provider_name,
                        alias_name,
                    )
                    raise ValueError(f"Fallback provider '{provider_name}' for alias '{alias_name}' not found")
                api_key = f"os.environ/{provider.key_env}"
                entry = {
                    "name": f"{alias_name}-fallback",
                    "litellm_model": f"{provider_name}/{deployments.fallback.model}",
                    "api_key": api_key,
                    "access_groups": [alias_name],
                    "mode": mode,
                }
                model_list.append(entry)
                logger.log(
                    logging.DEBUG,
                    "[IMP:7][_build_model_list] Added fallback entry: name='%s-fallback', model='%s'",
                    alias_name,
                    deployments.fallback.model,
                )

    logger.log(logging.CRITICAL, "[IMP:9][_build_model_list] Built %d model_list entries from policy", len(model_list))
    return model_list


def _build_fallbacks(policy: LLMPolicy) -> list[dict[str, str]]:
    """Build fallback chains from active aliases with fallback deployments.

    ## @purpose  For each alias with a fallback deployment, create a fallback
    ##           chain entry: primary → fallback. Only aliases with both
    ##           primary AND fallback generate entries.
    ## @io
    ##   - policy: LLMPolicy — validated policy instance
    ##   - ⎋ list[dict[str, str]] — fallback entries, each with primary and fallback
    ## @complexity O(A) where A = alias count
    ## @invariants
    ##   - Only aliases with DeploymentList and non-None primary AND fallback
    ##   - Reserved aliases (empty list) produce no fallback entries
    """
    fallbacks: list[dict[str, str]] = []
    logger.log(logging.INFO, "[IMP:8][_build_fallbacks] Building fallbacks from %d aliases", len(policy.aliases))

    for alias_name, alias in policy.aliases.items():
        deployments = alias.deployments
        if (
            isinstance(deployments, DeploymentList)
            and deployments.primary is not None
            and deployments.fallback is not None
        ):
            entry = {
                "primary": alias_name,
                "fallback": f"{alias_name}-fallback",
            }
            fallbacks.append(entry)
            logger.log(
                logging.DEBUG, "[IMP:7][_build_fallbacks] Added fallback: %s -> %s", entry["primary"], entry["fallback"]
            )

    logger.log(logging.CRITICAL, "[IMP:9][_build_fallbacks] Built %d fallback entries", len(fallbacks))
    return fallbacks


def _get_default_template_path() -> pathlib.Path:
    """Return the default path to the Jinja2 template.

    ## @purpose  Resolve path relative to project root.
    ## @complexity O(1)
    """
    return _PROJECT_ROOT / _TEMPLATE_REL_PATH


def _get_default_policy_path() -> pathlib.Path:
    """Return the default policy.yaml path relative to project root.

    ## @purpose  Default location: core/internal/llm/policy.yaml
    ## @complexity O(1)
    """
    return _PROJECT_ROOT / "core" / "internal" / "llm" / "policy.yaml"


# ── Renderer ─────────────────────────────────────────────────────────────────


def render_litellm_config(
    policy_path: pathlib.Path,
    template_path: pathlib.Path | None = None,
) -> str:
    """Render litellm-config.yml from policy.yaml via Jinja2 template.

    ## @purpose  Load policy → build model_list + fallbacks → render template → return YAML string.
    ##           This is the core rendering pipeline.
    ## @io
    ##   - policy_path: pathlib.Path — path to policy.yaml
    ##   - template_path: pathlib.Path | None — path to Jinja2 template (default: project template)
    ##   - ⎋ str — rendered litellm-config.yml content
    ##   - raises: FileNotFoundError, ValueError, jinja2.TemplateError
    ## @complexity O(A + M) where A = active aliases, M = model_list entries
    ## @invariants
    ##   - Output is valid YAML (parseable with yaml.safe_load)
    ##   - Output contains model_list, litellm_settings, and optional fallbacks
    ##   - Output does NOT contain reserved aliases (coding, vision, embedding)
    """
    logger.log(logging.INFO, "[IMP:7][render_litellm_config] Loading policy from: %s", policy_path)

    # Step 1: Load policy
    policy = LLMPolicy.from_yaml(policy_path)
    logger.log(
        logging.INFO,
        "[IMP:8][render_litellm_config] Policy loaded: %d aliases, %d providers",
        len(policy.aliases),
        len(policy.providers),
    )

    # Step 2: Build data structures
    model_list = _build_model_list(policy)
    fallbacks = _build_fallbacks(policy)

    # Step 3: Build template data
    settings = {
        "num_retries": 3,
        "drop_params": True,
        "success_callback": ["prometheus", "langfuse"],
        "failure_callback": ["prometheus"],
    }
    template_data = {
        "model_list": model_list,
        "settings": settings,
        "fallbacks": fallbacks,
    }
    logger.log(
        logging.INFO,
        "[IMP:8][render_litellm_config] Template data: %d model_list, %d fallbacks",
        len(model_list),
        len(fallbacks),
    )

    # Step 4: Resolve template
    if template_path is None:
        template_path = _get_default_template_path()
    logger.log(logging.INFO, "[IMP:7][render_litellm_config] Using template: %s", template_path)

    if not template_path.exists():
        logger.log(logging.CRITICAL, "[IMP:10][render_litellm_config] Template not found: %s", template_path)
        raise FileNotFoundError(f"Jinja2 template not found: {template_path}")

    template_dir = template_path.parent
    template_filename = template_path.name

    # Step 5: Render template
    env = Environment(  # nosec B701 — YAML generation, not HTML
        loader=FileSystemLoader(str(template_dir)),
        autoescape=False,
        trim_blocks=True,
        lstrip_blocks=True,
    )
    template = env.get_template(template_filename)
    rendered = template.render(**template_data)
    logger.log(logging.INFO, "[IMP:8][render_litellm_config] Template rendered: %d characters", len(rendered))

    # Step 6: Validate output YAML is parseable
    try:
        parsed = yaml.safe_load(rendered)
        if not isinstance(parsed, dict):
            raise ValueError("Rendered output is not a valid YAML mapping")
        logger.log(
            logging.CRITICAL,
            "[IMP:9][render_litellm_config] Rendered YAML is valid: %d top-level keys: %s",
            len(parsed),
            list(parsed.keys()),
        )
    except yaml.YAMLError as e:
        logger.log(logging.CRITICAL, "[IMP:10][render_litellm_config] Rendered YAML is invalid: %s", e)
        raise

    return rendered


def render_to_file(
    policy_path: pathlib.Path,
    output_path: pathlib.Path,
    template_path: pathlib.Path | None = None,
) -> None:
    """Render litellm-config.yml from policy.yaml and write to output file.

    ## @purpose  Convenience wrapper: render → write to file.
    ## @io
    ##   - policy_path: pathlib.Path — path to policy.yaml
    ##   - output_path: pathlib.Path — path to output litellm-config.yml
    ##   - template_path: pathlib.Path | None — optional custom template
    ## @complexity O(render) — delegates to render_litellm_config
    """
    logger.log(logging.INFO, "[IMP:7][render_to_file] Rendering to: %s", output_path)
    content = render_litellm_config(policy_path, template_path)

    output_path.parent.mkdir(parents=True, exist_ok=True)
    with open(output_path, "w") as f:
        f.write(content)

    logger.log(logging.CRITICAL, "[IMP:9][render_to_file] Written %d bytes to %s", len(content), output_path)


def check_freshness(
    policy_path: pathlib.Path,
    output_path: pathlib.Path,
    template_path: pathlib.Path | None = None,
) -> bool:
    """Check if the output file matches freshly rendered content (dry-run diff).

    ## @purpose  Render policy to a temp file and compare byte-for-byte with
    ##           the existing output file. Used by --check CLI mode and CI gates.
    ## @io
    ##   - policy_path: pathlib.Path — path to policy.yaml
    ##   - output_path: pathlib.Path — path to existing litellm-config.yml
    ##   - template_path: pathlib.Path | None — optional custom template
    ##   - ⎋ bool — True if output is fresh (matches rendered), False if stale
    ## @complexity O(render + read) — full render plus one file read
    ## @invariants
    ##   - If output_path does not exist, returns False (stale)
    ##   - Comparison is byte-level (no semantic diff)
    """
    logger.log(logging.INFO, "[IMP:7][check_freshness] Checking freshness of: %s", output_path)

    if not output_path.exists():
        logger.log(logging.WARNING, "[IMP:6][check_freshness] Output file does not exist: %s", output_path)
        return False

    # Render to temp file
    with tempfile.NamedTemporaryFile(mode="w", suffix=".yml", delete=False) as tmp:
        tmp_path = pathlib.Path(tmp.name)
        try:
            content = render_litellm_config(policy_path, template_path)
            tmp.write(content)
            tmp.flush()

            # Byte-by-byte comparison
            rendered_bytes = content.encode("utf-8")
            with open(output_path, "rb") as f:
                existing_bytes = f.read()

            is_fresh = rendered_bytes == existing_bytes
            if is_fresh:
                logger.log(logging.CRITICAL, "[IMP:9][check_freshness] Output is FRESH (content matches rendered)")
            else:
                logger.log(logging.WARNING, "[IMP:6][check_freshness] Output is STALE (content differs from rendered)")
        finally:
            tmp_path.unlink(missing_ok=True)

    return is_fresh


# ── CLI ──────────────────────────────────────────────────────────────────────


def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    """Parse command-line arguments.

    ## @purpose  CLI argument parser for config_renderer.py.
    ## @complexity O(1)
    """
    parser = argparse.ArgumentParser(
        description="Render litellm-config.yml from policy.yaml",
    )
    parser.add_argument(
        "--policy",
        type=str,
        default=None,
        help="Path to policy.yaml (default: core/internal/llm/policy.yaml)",
    )
    parser.add_argument(
        "--output",
        type=str,
        default=None,
        help="Output path for litellm-config.yml (default: print to stdout if --check not set)",
    )
    parser.add_argument(
        "--check",
        action="store_true",
        help="Dry-run mode: render to temp and compare with output file. Exit 0 if fresh, 1 if stale.",
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    """CLI entry point for config_renderer.py.

    ## @purpose  Parse args, load policy, render template, write output or check freshness.
    ## @io
    ##   - argv: list[str] | None — CLI arguments (default: sys.argv[1:])
    ##   - ⎋ int — exit code: 0 success, 1 stale/stale or render error
    ## @complexity O(render) — delegates to render functions
    ## @invariants
    ##   --check without --output → error (no file to compare)
    ##   --check with fresh output → exit 0
    ##   --check with stale output → exit 1
    ##   --output → write rendered content to file
    ##   Neither --output nor --check → print to stdout
    """
    args = _parse_args(argv)

    # Resolve paths
    policy_path = pathlib.Path(args.policy) if args.policy else _get_default_policy_path()
    template_path = _get_default_template_path()
    output_path = pathlib.Path(args.output) if args.output else None

    logger.log(logging.INFO, "[IMP:7][main] Config Renderer started")
    logger.log(logging.INFO, "[IMP:7][main] Policy: %s", policy_path)
    logger.log(logging.INFO, "[IMP:7][main] Template: %s", template_path)

    try:
        if args.check:
            if not output_path:
                logger.log(logging.CRITICAL, "[IMP:10][main] --check requires --output (no output file to compare)")
                print("ERROR: --check requires --output PATH", file=sys.stderr)
                return 1

            fresh = check_freshness(policy_path, output_path, template_path)
            if fresh:
                print(f"OK: {output_path} is fresh (matches rendered from {policy_path})")
                logger.log(logging.CRITICAL, "[IMP:9][main] Freshness check PASSED: %s is up-to-date", output_path)
                return 0
            print(f"STALE: {output_path} does not match rendered from {policy_path}", file=sys.stderr)
            logger.log(logging.WARNING, "[IMP:6][main] Freshness check FAILED: %s is stale", output_path)
            return 1
        if output_path:
            render_to_file(policy_path, output_path, template_path)
            print(f"Rendered: {output_path}")
            return 0
        content = render_litellm_config(policy_path, template_path)
        print(content, end="")
        return 0
    except (FileNotFoundError, ValueError, Exception) as e:
        logger.log(logging.CRITICAL, "[IMP:10][main] Render error: %s: %s", type(e).__name__, e)
        print(f"ERROR: {e}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
