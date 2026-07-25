#!/usr/bin/env python3
# GREP_SUMMARY: node_yaml, extract_context, shared, yaml-parser, context-extraction
# STRUCTURE: ▶ extract_context_from_node_yaml(path, log_tag) → ◇ yaml.safe_load → ◇ context str? → ◇ contexts[0]? → ⎋ ""
# region MODULE_CONTRACT
## @purpose  Canonical implementation of context extraction from node.yaml.
##           One node = one context. Reads context (string) or contexts[0].name (array, first element).
## @scope    Single-source-of-truth for _extract_context_from_node_yaml() previously
##           duplicated across state_machine.py, steps.py, and context_deployer.py.
## @invariants
##   1. Primary: top-level context field (string)
##   2. Fallback: contexts[0].name (array, first element)
##   3. Returns empty string on parse error
##   4. log_tag parameter controls LDD log prefix: [IMP:8][<log_tag>]
## @rationale Extracted from 3 duplicate copies (state_machine.py, steps.py,
##            context_deployer.py) — DRIFT-B5 elimination (Brief 077).
##            Uses context_deployer.py version as canonical (has invariants docstring).
## @changes  2026-07-25 · DevPlan 070 — Created shared module (DRIFT-B5)
# endregion MODULE_CONTRACT

import logging

logger = logging.getLogger(__name__)


# region FUNC_extract_context_from_node_yaml
## @purpose — Extract context name from node.yaml. One node = one context.
##            Reads context (string) or contexts[0].name (array, first element).
## @io — ⇥ node_yaml_path: str, log_tag: str = "context" → ⎋ str (empty if not found)
## @complexity — O(N) for YAML parse
## @invariants
##   - Primary: top-level context field (string)
##   - Fallback: contexts[0].name (array, first element)
##   - Returns empty string on parse error
##   - log_tag parameter controls LDD log prefix: [IMP:8][<log_tag>]
## @rationale Extracted from 3 duplicate copies (state_machine.py, steps.py,
##            context_deployer.py) — DRIFT-B5 elimination (Brief 077).
def extract_context_from_node_yaml(node_yaml_path: str, log_tag: str = "context") -> str:
    """Extract context name from node.yaml."""
    try:
        import yaml

        with open(node_yaml_path) as f:
            data = yaml.safe_load(f)
        if not isinstance(data, dict):
            return ""
        # Primary: context field (string)
        ctx = data.get("context", "")
        if ctx and isinstance(ctx, str):
            logger.info("[IMP:8][%s] Context from node.yaml context field: %s", log_tag, ctx)
            return ctx
        # Fallback: contexts array (first element)
        contexts = data.get("contexts", [])
        if contexts and isinstance(contexts, list) and len(contexts) > 0:
            first = contexts[0]
            if isinstance(first, dict):
                ctx = first.get("name", "")
            elif isinstance(first, str):
                ctx = first
            if ctx:
                logger.info("[IMP:8][%s] Context from node.yaml contexts[0].name: %s", log_tag, ctx)
                return ctx
    except Exception as e:
        logger.warning("[IMP:7][%s] Failed to parse %s: %s", log_tag, node_yaml_path, e)
    return ""


# endregion FUNC_extract_context_from_node_yaml
