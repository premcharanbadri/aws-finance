"""
Shared Bedrock LLM wrapper.

Follows the exact Converse pattern proven in hackathon-kit/infer.py:
boto3 bedrock-runtime .converse(), GPT inference profiles, region/model
from environment. Every LLM-driven agent (Coordinator, Investment Analyst,
Predictor, Market Strategist) calls through here so model config lives in
one place.
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from typing import Any

import boto3

DEFAULT_MODEL_ID = os.getenv("BEDROCK_MODEL_ID", "us.openai.gpt-5.6-luna")
DEFAULT_REGION = os.getenv("AWS_REGION", "us-east-1")


@dataclass
class LLMClient:
    """Thin wrapper over Bedrock Converse.

    Reuses a single boto3 client. `system` and `tools` are optional so the
    same client serves plain generation, JSON-structured output, and
    tool-using agents.
    """

    model_id: str = DEFAULT_MODEL_ID
    region: str = DEFAULT_REGION
    max_tokens: int = 2048
    temperature: float = 0.2
    _client: Any = field(default=None, repr=False)

    def __post_init__(self) -> None:
        self._client = boto3.client("bedrock-runtime", region_name=self.region)

    def generate(
        self,
        prompt: str,
        system: str | None = None,
        max_tokens: int | None = None,
        temperature: float | None = None,
    ) -> str:
        """Single-turn text generation."""
        kwargs: dict[str, Any] = {
            "modelId": self.model_id,
            "messages": [{"role": "user", "content": [{"text": prompt}]}],
            "inferenceConfig": {
                "maxTokens": max_tokens or self.max_tokens,
                "temperature": self.temperature if temperature is None else temperature,
            },
        }
        if system:
            kwargs["system"] = [{"text": system}]
        response = self._client.converse(**kwargs)
        return "".join(
            part.get("text", "")
            for part in response["output"]["message"]["content"]
        )

    def generate_json(
        self,
        prompt: str,
        system: str | None = None,
        max_tokens: int | None = None,
    ) -> dict[str, Any]:
        """Generate and parse a JSON object.

        We instruct the model to return JSON only and defensively strip
        code fences. For production, prefer Converse tool-use / structured
        output to guarantee schema compliance.
        """
        guard = (
            "Respond with a single valid JSON object and nothing else. "
            "Do not wrap it in markdown code fences."
        )
        sys_prompt = f"{system}\n\n{guard}" if system else guard
        raw = self.generate(prompt, system=sys_prompt, max_tokens=max_tokens, temperature=0.0)
        return _parse_json_lenient(raw)


def _parse_json_lenient(raw: str) -> dict[str, Any]:
    """Best-effort JSON extraction from an LLM response."""
    text = raw.strip()
    if text.startswith("```"):
        text = text.split("```", 2)[1]
        if text.startswith("json"):
            text = text[4:]
        text = text.strip()
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        start, end = text.find("{"), text.rfind("}")
        if start != -1 and end != -1 and end > start:
            return json.loads(text[start : end + 1])
        raise


# Module-level singleton for convenience in agents/services.
_default_client: LLMClient | None = None


def get_client() -> LLMClient:
    global _default_client
    if _default_client is None:
        _default_client = LLMClient()
    return _default_client


# ---------------------------------------------------------------------------
# Fail-soft functional API (merged from the Akshay CPA work).
#
# agents/cpa_liquidity.py and any other deterministic-first agent calls these.
# Contract: NEVER raise. Return None when no model is configured, the SDK is
# missing, credentials are absent, or the call errors — so callers fall back to
# deterministic logic and stay testable offline / demo-safe.
#
# Primary path: Strands Agents SDK (BedrockModel). Fallback: the LLMClient /
# Bedrock Converse wrapper defined above. This keeps a single place for model
# config while serving both the Prem stack (get_client/LLMClient) and the
# deterministic-first agents (call_llm/call_llm_json).
# ---------------------------------------------------------------------------

DEFAULT_SYSTEM_PROMPT = (
    "You are a precise financial analysis assistant. Follow the user's output "
    "format exactly and never invent numbers that were not provided."
)


def _call_via_strands(prompt, model_id, region, *, max_tokens, temperature, system_prompt):
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
        text = str(agent(prompt)).strip()
        return text or None
    except Exception:  # noqa: BLE001 - fail soft on any Strands/AWS error
        return None


def call_llm(
    prompt: str,
    *,
    max_tokens: int = 1024,
    temperature: float = 0.2,
    system_prompt: str = DEFAULT_SYSTEM_PROMPT,
):
    """Invoke the configured Bedrock model and return its text, or None.

    Tries Strands first, then the Converse LLMClient. Never raises: any problem
    (unset BEDROCK_MODEL_ID, missing SDK/credentials, API error, empty response)
    yields None so callers fall back to deterministic logic.
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

    # Fallback to the Converse wrapper above.
    try:
        client = LLMClient(model_id=model_id, region=region, max_tokens=max_tokens, temperature=temperature)
        out = client.generate(prompt, system=system_prompt).strip()
        return out or None
    except Exception:  # noqa: BLE001 - fail soft
        return None


def call_llm_json(prompt: str, **kwargs):
    """Call the LLM and parse its reply as JSON, tolerating code fences.

    Returns None if the LLM is unavailable or the reply is not valid JSON.
    """
    text = call_llm(prompt, **kwargs)
    if not text:
        return None
    try:
        return _parse_json_lenient(text)
    except Exception:  # noqa: BLE001
        return None
