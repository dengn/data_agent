"""LLM client wrapper — uses DeepSeek API (OpenAI-compatible)."""

import logging

from openai import OpenAI

from data_agent.config import settings

logger = logging.getLogger(__name__)

_client = None


def get_client() -> OpenAI:
    global _client
    if _client is None:
        _client = OpenAI(
            api_key=settings.llm_api_key,
            base_url=settings.llm_base_url,
        )
    return _client


def chat(
    system: str,
    user_message: str,
    model: str = "",
    max_tokens: int = 4096,
    temperature: float = 0.0,
) -> str:
    """Send a message to the LLM and return the text response."""
    client = get_client()
    response = client.chat.completions.create(
        model=model or settings.llm_model,
        max_tokens=max_tokens,
        temperature=temperature,
        messages=[
            {"role": "system", "content": system},
            {"role": "user", "content": user_message},
        ],
    )
    return response.choices[0].message.content
