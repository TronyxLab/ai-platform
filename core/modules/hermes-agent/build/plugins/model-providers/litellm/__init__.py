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

# W11: providers-пакет — build-time зависимость образа hermes-agent (вне репозитория) →
# импорты нерезолвимы статически; контракт ProviderProfile/register_provider — внешний
from typing import cast

from providers import (
    register_provider,  # pyright: ignore[reportMissingImports, reportUnknownVariableType] — W11 external build-time providers-пакет
)
from providers.base import (
    ProviderProfile,  # pyright: ignore[reportMissingImports, reportUnknownVariableType] — W11 external build-time providers-пакет
)

# ⚠️ TRAP[DECISION] · 2026-08-14 · — · Порт-дубль SoT platform-infra.yaml (litellm 4000) — cross-layer
# · Rejected: импорт core/internal/shared/platform_ports из модуля
# · Reason: core/AGENTS.md Cross-layer — modules НЕ импортируют core/internal; build/plugins
# ·   собирается в образ hermes-agent без core/. Значение — зеркало SoT (container-порт litellm);
# ·   LITELLM_BASE_URL/OPENAI_BASE_URL в compose переопределяют base_url при рантайме.
# · Rev: при появлении модульного конфиг-механизма → убрать локальный дубль.
_LOCAL_PORT_LITELLM: int = 4000

# W11: ProviderProfile — external build-time тип → cast к object (литерал-построение сохраняется)
litellm: object = cast(
    "object",
    ProviderProfile(
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
        base_url=f"http://litellm:{_LOCAL_PORT_LITELLM}/v1",
        default_aux_model="chat",
    ),
)

register_provider(litellm)
