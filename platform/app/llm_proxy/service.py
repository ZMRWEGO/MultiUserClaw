"""LLM Proxy — the security core of the multi-tenant platform.

Receives OpenAI-compatible requests from user containers (authenticated
by container token), injects the real API key, records usage, enforces
quotas, and forwards to the actual LLM provider.
"""

from __future__ import annotations

from datetime import datetime, timezone, timedelta
from typing import Any

from fastapi import HTTPException, status
from litellm import acompletion
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.db.models import Container, UsageRecord, User


# ---------------------------------------------------------------------------
# Model → provider mapping
# ---------------------------------------------------------------------------

_MODEL_PROVIDER_MAP: dict[str, tuple[str, str]] = {
    # keyword in model name → (litellm prefix, settings attr for api key)
    "claude": ("", "anthropic_api_key"),
    "gpt": ("", "openai_api_key"),
    "deepseek": ("deepseek", "deepseek_api_key"),
    "o1": ("", "openai_api_key"),
    "o3": ("", "openai_api_key"),
    "o4": ("", "openai_api_key"),
    "moonshot": ("", "moonshot_api_key"),
    "glm": ("", "zhipu_api_key"),
}

# OpenAI-compatible providers that need a custom api_base
_CUSTOM_BASE_PROVIDERS: dict[str, tuple[str, str]] = {
    # keyword → (api_base, settings attr for api key)
    "qwen": ("https://dashscope.aliyuncs.com/compatible-mode/v1", "dashscope_api_key"),
    "aihubmix": ("https://aihubmix.com/v1", "aihubmix_api_key"),
}

# Provider-specific extra headers injected at proxy layer
_PROVIDER_HEADERS: dict[str, dict[str, str]] = {
    "moonshot": {"User-Agent": "claude-code/1.0"},
}

# Model-specific parameter overrides (e.g. kimi-k2.5 temperature)
_MODEL_OVERRIDES: list[tuple[str, dict[str, Any]]] = [
    ("kimi-k2.5", {"temperature": 1.0}),
    ("kimi-for-coding", {"temperature": 0.6}),
]


def _resolve_provider(model: str) -> tuple[str, str, str | None]:
    """Return (litellm_model_name, api_key, api_base_or_None) for the given model."""
    model_lower = model.lower()

    # Self-hosted vLLM: when configured, use it for the default model.
    # This takes priority so that e.g. a VLLM-served "Qwen3-14B" isn't
    # accidentally routed to DashScope via the "qwen" keyword.
    if settings.hosted_vllm_api_base:
        vllm_key = settings.hosted_vllm_api_key or "dummy"
        return f"hosted_vllm/{model}", vllm_key, settings.hosted_vllm_api_base

    # Check custom-base providers first (DashScope, AiHubMix, etc.)
    for keyword, (api_base, key_attr) in _CUSTOM_BASE_PROVIDERS.items():
        if keyword in model_lower:
            api_key = getattr(settings, key_attr, "")
            if api_key:
                return f"openai/{model}", api_key, api_base

    # Check standard providers
    for keyword, (prefix, key_attr) in _MODEL_PROVIDER_MAP.items():
        if keyword in model_lower:
            api_key = getattr(settings, key_attr, "")
            if api_key:
                litellm_model = f"{prefix}/{model}" if prefix else model
                api_base = None
                # Moonshot supports custom api_base (e.g. api.moonshot.cn)
                if keyword == "moonshot" and settings.moonshot_api_base:
                    api_base = settings.moonshot_api_base
                return litellm_model, api_key, api_base

    # Fallback: OpenRouter (routes any model)
    if settings.openrouter_api_key:
        return f"openrouter/{model}", settings.openrouter_api_key, None

    raise HTTPException(
        status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
        detail=f"No provider configured for model '{model}'",
    )


def _apply_model_overrides(model: str, kwargs: dict[str, Any]) -> None:
    """Apply model-specific parameter overrides from registry."""
    model_lower = model.lower()
    for pattern, overrides in _MODEL_OVERRIDES:
        if pattern in model_lower:
            kwargs.update(overrides)
            return


def _get_extra_headers(model: str) -> dict[str, str] | None:
    """Return provider-specific extra headers for the given model."""
    model_lower = model.lower()
    for keyword, headers in _PROVIDER_HEADERS.items():
        if keyword in model_lower:
            return headers
    return None


# ---------------------------------------------------------------------------
# Quota check
# ---------------------------------------------------------------------------

_TIER_LIMITS = {
    "free": settings.quota_free,
    "basic": settings.quota_basic,
    "pro": settings.quota_pro,
}


async def _check_quota(db: AsyncSession, user: User) -> None:
    """Raise 429 if the user exceeded their daily quota."""
    today_start = datetime.utcnow().replace(hour=0, minute=0, second=0, microsecond=0)
    result = await db.execute(
        select(func.coalesce(func.sum(UsageRecord.total_tokens), 0)).where(
            UsageRecord.user_id == user.id,
            UsageRecord.created_at >= today_start,
        )
    )
    used_today: int = result.scalar_one()
    limit = _TIER_LIMITS.get(user.quota_tier, _TIER_LIMITS["free"])

    if used_today >= limit:
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail=f"Daily token quota exceeded ({used_today:,}/{limit:,}). Resets at midnight UTC.",
        )


# ---------------------------------------------------------------------------
# Core proxy handler
# ---------------------------------------------------------------------------

async def proxy_chat_completion(
    db: AsyncSession,
    container_token: str,
    model: str,
    messages: list[dict],
    max_tokens: int = 4096,
    temperature: float = 0.7,
    tools: list[dict] | None = None,
    stream: bool = False,
) -> dict:
    """Validate token, check quota, forward to real LLM, record usage."""

    # 1. Authenticate container
    result = await db.execute(
        select(Container).where(Container.container_token == container_token)
    )
    container = result.scalar_one_or_none()
    if container is None:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid container token")

    # 2. Get user and check quota
    user_result = await db.execute(select(User).where(User.id == container.user_id))
    user = user_result.scalar_one_or_none()
    if user is None or not user.is_active:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="User account disabled")

    await _check_quota(db, user)

    # 3. Resolve provider
    litellm_model, api_key, api_base = _resolve_provider(model)

    # 4. Call LLM
    kwargs: dict = {
        "model": litellm_model,
        "messages": messages,
        "max_tokens": max_tokens,
        "temperature": temperature,
        "api_key": api_key,
    }
    if api_base:
        kwargs["api_base"] = api_base
    if tools:
        kwargs["tools"] = tools
        kwargs["tool_choice"] = "auto"

    # Apply model-specific parameter overrides (e.g. kimi-k2.5 temperature)
    _apply_model_overrides(model, kwargs)

    # Apply provider-specific extra headers (e.g. moonshot User-Agent)
    extra_headers = _get_extra_headers(model)
    if extra_headers:
        kwargs["extra_headers"] = extra_headers

    try:
        response = await acompletion(**kwargs)
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail=f"LLM provider error: {e}",
        )

    # 5. Record usage
    usage = getattr(response, "usage", None)
    if usage:
        record = UsageRecord(
            user_id=user.id,
            model=model,
            input_tokens=usage.prompt_tokens or 0,
            output_tokens=usage.completion_tokens or 0,
            total_tokens=usage.total_tokens or 0,
        )
        db.add(record)
        await db.commit()

    # 6. Update container last_active_at
    container.last_active_at = datetime.utcnow()
    await db.commit()

    # 7. Return OpenAI-compatible response
    return response.model_dump()
