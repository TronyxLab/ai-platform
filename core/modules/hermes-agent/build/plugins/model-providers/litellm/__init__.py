# GREP_SUMMARY: litellm provider, hermes-agent, plugin, model-provider, gateway
# STRUCTURE: ◇ ProviderProfile(litellm) → ⊕ register_provider
# region MODULE_CONTRACT
## @purpose  LiteLLM Gateway provider profile for hermes-agent — routes all LLM requests through LiteLLM proxy
## @scope    Provider registration for litellm gateway in hermes-agent model-provider plugin system
## @invariants Uses LITELLM_API_KEY env var, base_url configurable via LITELLM_BASE_URL or OPENAI_BASE_URL
## @rationale LiteLLM handles model routing, fallbacks, and API key management centrally
# endregion MODULE_CONTRACT

"""LiteLLM Gateway provider profile.

Routes all LLM requests through the LiteLLM proxy at OPENAI_BASE_URL.
The LiteLLM proxy handles model routing, fallbacks, and API key management
centrally. Hermes-agent uses semantic aliases (reasoning, chat) which
LiteLLM resolves to actual model deployments (deepseek-v4-pro, etc.).

Key details:
  - Uses LITELLM_API_KEY env var for authentication (LiteLLM virtual key)
  - base_url is configurable via LITELLM_BASE_URL or OPENAI_BASE_URL env var
  - fallback_models are LiteLLM semantic aliases, not actual model names
  - No model-specific quirks (thinking, reasoning_effort) — LiteLLM manages those
"""

from providers import register_provider
from providers.base import ProviderProfile

litellm = ProviderProfile(
    name="litellm",
    aliases=("litellm-gateway", "llm-gateway", "llm-proxy"),
    env_vars=("LITELLM_API_KEY",),
    display_name="LiteLLM Gateway",
    description="LiteLLM proxy — routes to DeepSeek and other providers",
    signup_url="",
    fallback_models=(
        "reasoning",
        "chat",
    ),
    base_url="http://litellm:4000/v1",
    default_aux_model="chat",
)

register_provider(litellm)
