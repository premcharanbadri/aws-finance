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

# Default model: verified live in this account via Bedrock Converse. The
# hackathon-kit's GPT profiles were not available here (end-of-life / not
# provisioned), so we default to Llama 3.3 70B. Override with BEDROCK_MODEL_ID.
DEFAULT_MODEL_ID = os.getenv("BEDROCK_MODEL_ID", "us.meta.llama3-3-70b-instruct-v1:0")
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
        pass
    # Models often wrap JSON in prose or extra text. Extract the first
    # balanced {...} object by brace-matching (handles nested braces).
    obj = _extract_first_object(text)
    if obj is not None:
        try:
            return json.loads(obj)
        except json.JSONDecodeError:
            # Last resort: tolerate single-quoted JSON (e.g. Python-style).
            import ast

            try:
                result = ast.literal_eval(obj)
                if isinstance(result, dict):
                    return result
            except (ValueError, SyntaxError):
                pass
    raise json.JSONDecodeError("no JSON object found in model output", text, 0)


def _extract_first_object(text: str) -> str | None:
    """Return the first balanced {...} substring, or None."""
    start = text.find("{")
    if start == -1:
        return None
    depth = 0
    for i in range(start, len(text)):
        if text[i] == "{":
            depth += 1
        elif text[i] == "}":
            depth -= 1
            if depth == 0:
                return text[start : i + 1]
    return None


# Module-level singleton for convenience in agents/services.
_default_client: LLMClient | None = None


def get_client() -> LLMClient:
    global _default_client
    if _default_client is None:
        _default_client = LLMClient()
    return _default_client
