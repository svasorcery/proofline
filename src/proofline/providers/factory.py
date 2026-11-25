"""Select one explicit backend provider; configuration never probes the network."""

import httpx

from proofline.config import Settings
from proofline.providers.anthropic_messages import AnthropicMessagesProvider
from proofline.providers.groq_chat import GroqChatCompletionsProvider
from proofline.providers.live import LiveProvider
from proofline.providers.openai_responses import OpenAIResponsesProvider


def create_live_provider(
    settings: Settings, *, transport: httpx.AsyncBaseTransport | None = None
) -> LiveProvider:
    if settings.llm_provider == "anthropic":
        return AnthropicMessagesProvider(settings, transport=transport)
    if settings.llm_provider == "groq":
        return GroqChatCompletionsProvider(settings, transport=transport)
    return OpenAIResponsesProvider(settings, transport=transport)
