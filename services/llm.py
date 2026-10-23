"""Shared LLM helper: a graceful wrapper around Amazon Bedrock via Strands.

Every agent calls ``call_llm(prompt)`` to get free-text reasoning. The wrapper is
deliberately *fail-soft*: if no model is configured, the SDK is missing,
credentials are absent, or the call errors out, it returns ``None`` instead of
raising. Callers treat ``None`` as "no LLM available" and fall back to their
deterministic logic, which keeps every agent testable offline and demo-safe.

Primary path: the **Strands Agents SDK** (`strands`) with its `BedrockModel`
provider — the agent framework this project is built around. If Strands is not
installed, the helper falls back to a direct ``boto3`` Bedrock Runtime call so
the agents still work. Both paths honour the same configuration and the same
fail-soft contract.

Configuration (all via environment variables, nothing hardcoded):
    BEDROCK_MODEL_ID   the model to invoke, e.g. an Anthropic Claude model id
                       your AWS account has access to. If unset, call_llm
                       short-circuits to None (pure deterministic mode).
    AWS_REGION         Bedrock region (default us-east-1).
"""
from __future__ import annotations

import json
import os
from typing import Optional

DEFAULT_REGION = "us-east-1"

# System prompt shared by callers that want a terse, no-chatter model.
DEFAULT_SYSTEM_PROMPT = (
    "You are a precise financial analysis assistant. Follow the user's output "
    "format exactly and never invent numbers that were not provided."
)


def _call_via_strands(
    prompt: str,
    model_id: str,
    region: str,
    *,
    max_tokens: int,
    temperature: float,
    system_prompt: str,
) -> Optional[str]:
    """Invoke Bedrock through the Strands Agents SDK. Returns None on any issue."""
    try:
        from strands import Agent
        from strands.models import BedrockModel
    except Exception:  # noqa: BLE001 - Strands not installed; caller falls back
        return None

    try:
        model = BedrockModel(
            model_id=model_id,
            region_name=region,
            max_tokens=max_tokens,
            temperature=temperature,
        )
        agent = Agent(model=model, system_prompt=system_prompt)
        result = agent(prompt)
        # AgentResult stringifies to the model's text reply.
        text = str(result).strip()
        return text or None
    except Exception:  # noqa: BLE001 - fail soft on any Strands/AWS error
        return None


def _bedrock_body(model_id: str, prompt: str, max_tokens: int, temperature: float) -> Optional[dict]:
    """Request body for a direct boto3 call (Anthropic messages schema only)."""
    mid = model_id.lower()
    if "anthropic" in mid or "claude" in mid:
        return {
            "anthropic_version": "bedrock-2023-05-31",
            "max_tokens": max_tokens,
            "temperature": temperature,
            "messages": [{"role": "user", "content": prompt}],
        }
    return None


def _call_via_boto3(
    prompt: str,
    model_id: str,
    region: str,
    *,
    max_tokens: int,
    temperature: float,
) -> Optional[str]:
    """Fallback: invoke Bedrock Runtime directly with boto3. None on any issue."""
    try:
        import boto3
    except Exception:  # noqa: BLE001
        return None

    body = _bedrock_body(model_id, prompt, max_tokens, temperature)
    if body is None:
        return None

    try:
        client = boto3.client("bedrock-runtime", region_name=region)
        resp = client.invoke_model(
            modelId=model_id,
            body=json.dumps(body),
            contentType="application/json",
            accept="application/json",
        )
        payload = json.loads(resp["body"].read())
        parts = payload.get("content", [])
        text = "".join(p.get("text", "") for p in parts if isinstance(p, dict))
        return text.strip() or None
    except Exception:  # noqa: BLE001 - fail soft on any AWS/credential error
        return None


def call_llm(
    prompt: str,
    *,
    max_tokens: int = 1024,
    temperature: float = 0.2,
    system_prompt: str = DEFAULT_SYSTEM_PROMPT,
) -> Optional[str]:
    """Invoke the configured Bedrock model and return its text, or None.

    Tries Strands first, then a direct boto3 call. This never raises: any problem
    (unset BEDROCK_MODEL_ID, missing SDK, missing credentials, unsupported model,
    API error, empty response) results in ``None`` so callers fall back to their
    deterministic logic.
    """
    model_id = os.environ.get("BEDROCK_MODEL_ID")
    if not model_id:
        return None

    region = os.environ.get("AWS_REGION") or DEFAULT_REGION

    text = _call_via_strands(
        prompt, model_id, region,
        max_tokens=max_tokens, temperature=temperature, system_prompt=system_prompt,
    )
    if text:
        return text

    return _call_via_boto3(
        prompt, model_id, region,
        max_tokens=max_tokens, temperature=temperature,
    )


def call_llm_json(prompt: str, **kwargs) -> Optional[dict]:
    """Call the LLM and parse its reply as JSON, tolerating code fences.

    Returns None if the LLM is unavailable or the reply is not valid JSON.
    """
    text = call_llm(prompt, **kwargs)
    if not text:
        return None
    cleaned = text.strip()
    if cleaned.startswith("```"):
        # strip ```json ... ``` fences the model may add
        cleaned = cleaned.split("```", 2)[1] if cleaned.count("```") >= 2 else cleaned
        if cleaned.lstrip().lower().startswith("json"):
            cleaned = cleaned.lstrip()[4:]
    try:
        return json.loads(cleaned)
    except Exception:  # noqa: BLE001
        return None
