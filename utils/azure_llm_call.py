#!/usr/bin/env python3
"""
Azure OpenAI background-mode helpers for fetching LLM descriptions.

Uses the Responses API in background mode: submit → poll → retrieve.
Requires: pip install openai
Environment: AZURE_OPENAI_ENDPOINT (or ENDPOINT_URL), AZURE_OPENAI_API_KEY,
             AZURE_OPENAI_API_VERSION (optional, default 2025-03-01-preview).
"""

from __future__ import annotations

import os
import time
from typing import Any

try:
    from openai import AzureOpenAI
except ImportError as e:
    raise ImportError("openai package required for Azure LLM. Install with: pip install openai") from e


def get_api_client(timeout: int | None = None) -> AzureOpenAI:
    """Initialize Azure OpenAI client from environment variables."""
    endpoint = os.getenv("AZURE_OPENAI_ENDPOINT") or os.getenv("ENDPOINT_URL")
    api_key = os.getenv("AZURE_OPENAI_API_KEY")
    api_version = os.getenv("AZURE_OPENAI_API_VERSION", "2025-03-01-preview")

    if not endpoint:
        raise RuntimeError(
            "Endpoint not set. Set AZURE_OPENAI_ENDPOINT or ENDPOINT_URL."
        )
    if not api_key:
        raise RuntimeError("AZURE_OPENAI_API_KEY not set.")

    client_kwargs: dict[str, Any] = {
        "azure_endpoint": endpoint,
        "api_key": api_key,
        "api_version": api_version,
    }
    if timeout is not None:
        client_kwargs["timeout"] = timeout
    return AzureOpenAI(**client_kwargs)


def _should_use_responses_api(model_name: str) -> bool:
    """Return True if the model should be called via Responses API."""
    if not model_name:
        return False
    name = model_name.lower()
    return (
        "gpt-5-pro" in name
        or "gpt-5.2" in name
        or "gpt-5" in name
        or "o4" in name
        or "o3" in name
        or "gpt-4.1" in name
    )


def _extract_text_from_response_object(response: Any) -> str:
    """Best-effort extraction of text from a Responses API response."""
    text = getattr(response, "output_text", None)
    if isinstance(text, str) and text:
        return text
    try:
        parts = []
        output = getattr(response, "output", None)
        if output is None and isinstance(response, dict):
            output = response.get("output")
        if isinstance(output, list):
            for item in output:
                content = getattr(item, "content", None) if not isinstance(item, dict) else item.get("content")
                if isinstance(content, list):
                    for c in content:
                        txt = getattr(c, "text", None)
                        if txt is None and isinstance(c, dict):
                            txt = c.get("text")
                        if isinstance(txt, str):
                            parts.append(txt)
        if parts:
            return "\n".join(parts)
    except Exception:
        pass
    return ""


def submit_background_job(
    client: AzureOpenAI,
    deployment: str,
    messages: list[dict[str, Any]],
    reasoning_effort: str | None = None,
) -> Any | None:
    """Submit a job in background mode. Returns response with .id and .status or None."""
    if not _should_use_responses_api(deployment):
        raise RuntimeError(
            f"Background mode requires Responses API. Model {deployment!r} may not support it."
        )
    try:
        api_params: dict[str, Any] = {
            "model": deployment,
            "input": messages,
            "temperature": 1.0,
            "background": True,
        }
        if reasoning_effort:
            api_params["reasoning"] = {"effort": reasoning_effort}
        return client.responses.create(**api_params)
    except Exception as e:
        raise RuntimeError(f"Failed to submit background job: {e}") from e


def check_job_status(client: AzureOpenAI, response_id: str) -> Any | None:
    """Check status of a background job. Returns response object or None."""
    try:
        return client.responses.retrieve(response_id)
    except Exception:
        return None


def cancel_background_job(client: AzureOpenAI, response_id: str) -> Any | None:
    """Cancel a background job. Returns final response or None."""
    try:
        return client.responses.cancel(response_id)
    except Exception:
        return None


def get_description_from_llm(
    prompt: str,
    model: str,
    reasoning_effort: str | None = None,
    timeout: int = 1200,
    poll_interval: int = 30,
    print_interval: int = 60,
) -> str:
    """
    Get description from LLM in background mode: submit → poll → retrieve.

    Args:
        prompt: The prompt text (single user message).
        model: Deployment/model name.
        reasoning_effort: Optional 'low', 'medium', 'high', 'xhigh'.
        timeout: Max seconds to wait (default 1200). Job is cancelled if exceeded.
        poll_interval: Seconds between status checks (default 30).
        print_interval: Seconds between progress prints while waiting (default 60).

    Returns:
        The extracted text from the completed job.

    Raises:
        RuntimeError: On submit failure, timeout, or job failed/incomplete.
    """
    messages = [{"role": "user", "content": prompt}]
    client = get_api_client(timeout=timeout)
    response = submit_background_job(client, model, messages, reasoning_effort)
    if not response:
        raise RuntimeError("Failed to submit background job")
    response_id = response.id
    job_start = time.time()
    last_print_time = time.time()

    while True:
        elapsed = time.time() - job_start
        if elapsed >= timeout:
            cancel_background_job(client, response_id)
            raise RuntimeError(f"Job timed out after {timeout}s")
        resp = check_job_status(client, response_id)
        if not resp:
            raise RuntimeError("Failed to check job status")
        status = resp.status
        if status == "completed":
            text = _extract_text_from_response_object(resp)
            return text
        if status == "failed":
            raise RuntimeError("Job failed")
        if status == "incomplete":
            raise RuntimeError("Job incomplete")
        if status in ("queued", "in_progress"):
            now = time.time()
            if now - last_print_time >= print_interval:
                remaining = timeout - elapsed
                print(f"  Status: {status}, elapsed: {elapsed:.1f}s / {timeout}s (remaining: {remaining:.1f}s)")
                last_print_time = now
            time.sleep(min(poll_interval, timeout - elapsed))
            continue
        time.sleep(poll_interval)
