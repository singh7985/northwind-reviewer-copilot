"""Shared LLM client with structured-output helpers and graceful no-key fallback."""
from __future__ import annotations

import json
import logging
from typing import Any, Optional, Type, TypeVar

from openai import OpenAI
from pydantic import BaseModel

from ..config import get_settings


log = logging.getLogger(__name__)
T = TypeVar("T", bound=BaseModel)


_client: Optional[OpenAI] = None


def client() -> Optional[OpenAI]:
    global _client
    s = get_settings()
    if not s.llm_enabled:
        return None
    if _client is None:
        _client = OpenAI(api_key=s.openai_api_key)
    return _client


def chat_structured(
    model: str,
    schema_cls: Type[T],
    system: str,
    user_parts: list[dict[str, Any]],
    temperature: float = 0.0,
) -> Optional[T]:
    """Call chat.completions with JSON Schema and parse into schema_cls.

    Returns None if LLM is disabled or call fails (so callers can degrade
    gracefully to deterministic/needs_human_review).
    """
    c = client()
    if c is None:
        return None
    try:
        # Use structured outputs via response_format=json_schema
        schema = schema_cls.model_json_schema()
        _strict_schema(schema)
        resp = c.chat.completions.create(
            model=model,
            temperature=temperature,
            messages=[
                {"role": "system", "content": system},
                {"role": "user", "content": user_parts},
            ],
            response_format={
                "type": "json_schema",
                "json_schema": {
                    "name": schema_cls.__name__,
                    "schema": schema,
                    "strict": False,
                },
            },
        )
        raw = resp.choices[0].message.content or "{}"
        return schema_cls.model_validate_json(raw)
    except Exception as e:
        log.warning("LLM structured call failed: %s", e)
        return None


def embed(texts: list[str]) -> Optional[list[list[float]]]:
    c = client()
    if c is None or not texts:
        return None
    s = get_settings()
    try:
        resp = c.embeddings.create(model=s.openai_embedding_model, input=texts)
        return [d.embedding for d in resp.data]
    except Exception as e:
        log.warning("Embedding call failed: %s", e)
        return None


def chat_plain(
    model: str, system: str, user: str, temperature: float = 0.0
) -> Optional[str]:
    c = client()
    if c is None:
        return None
    try:
        resp = c.chat.completions.create(
            model=model,
            temperature=temperature,
            messages=[
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
        )
        return resp.choices[0].message.content
    except Exception as e:
        log.warning("LLM plain call failed: %s", e)
        return None


def _strict_schema(schema: dict) -> None:
    """Best-effort tweaks so OpenAI JSON Schema mode accepts our Pydantic output."""
    # Pydantic emits "$defs"; OpenAI tolerates it. We just ensure additionalProperties
    # is false where types are object, to avoid hallucinated keys.
    def walk(node: Any) -> None:
        if isinstance(node, dict):
            if node.get("type") == "object" and "additionalProperties" not in node:
                node["additionalProperties"] = False
            for v in node.values():
                walk(v)
        elif isinstance(node, list):
            for v in node:
                walk(v)

    walk(schema)
