#!/usr/bin/env python3
"""
OpenAI API (api.openai.com) helpers for fetching LLM descriptions.

Uses the OpenAI client with chat completions or Responses API.
Requires: pip install openai
Environment: OPENAI_API_KEY
"""

from __future__ import annotations

import os
from typing import Any

try:
    from openai import OpenAI
except ImportError as e:
    raise ImportError("openai package required for OpenAI LLM. Install with: pip install openai") from e


def get_api_client(timeout: int | None = None) -> OpenAI:
    """Initialize OpenAI client from environment variables."""
    api_key = os.getenv("OPENAI_API_KEY")
    if not api_key:
        raise RuntimeError("OPENAI_API_KEY not set.")
    kwargs: dict[str, Any] = {"api_key": api_key}
    if timeout is not None:
        kwargs["timeout"] = timeout
    return OpenAI(**kwargs)


def get_description_from_llm(
    prompt: str,
    model: str,
    reasoning_effort: str | None = None,
    timeout: int = 1200,
    poll_interval: int = 30,
    print_interval: int = 60,
) -> str:
    """
    Get description from LLM via OpenAI API (synchronous call).

    Args:
        prompt: The prompt text (single user message).
        model: Model name (e.g. gpt-4o, o1).
        reasoning_effort: Optional; used for reasoning models if supported.
        timeout: Request timeout in seconds (default 1200).
        poll_interval: Unused for sync call; kept for API parity with Azure.
        print_interval: Unused for sync call; kept for API parity with Azure.

    Returns:
        The assistant message text.

    Raises:
        RuntimeError: On API failure.
    """
    client = get_api_client(timeout=timeout)
    messages = [{"role": "user", "content": prompt}]
    kwargs: dict[str, Any] = {
        "model": model,
        "messages": messages,
        "temperature": 1.0,
    }
    if reasoning_effort:
        kwargs["reasoning"] = {"effort": reasoning_effort}
    try:
        response = client.chat.completions.create(**kwargs)
    except Exception as e:
        # Some models use Responses API; try that if chat.completions not available
        if "reasoning" in str(e).lower() or "responses" in str(e).lower():
            try:
                resp = client.responses.create(
                    model=model,
                    input=messages,
                    temperature=1.0,
                    **({"reasoning": {"effort": reasoning_effort}} if reasoning_effort else {}),
                )
                out = getattr(resp, "output_text", None)
                if isinstance(out, str) and out:
                    return out
                # Fallback traverse
                for item in getattr(resp, "output", []) or []:
                    for c in getattr(item, "content", []) or []:
                        t = getattr(c, "text", None)
                        if isinstance(t, str):
                            return t
            except Exception as e2:
                raise RuntimeError(f"OpenAI API failed: {e2}") from e2
        raise RuntimeError(f"OpenAI API failed: {e}") from e
    choice = response.choices[0] if response.choices else None
    if not choice or not getattr(choice.message, "content", None):
        raise RuntimeError("OpenAI returned empty response")
    return choice.message.content or ""
