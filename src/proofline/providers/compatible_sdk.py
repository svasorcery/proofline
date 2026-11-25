"""Use the OpenAI SDK protocol without sending OpenAI's ambient headers."""

from typing import override

from openai import AsyncOpenAI, Omit


class CompatibleAsyncOpenAI(AsyncOpenAI):
    @property
    @override
    def default_headers(self) -> dict[str, str | Omit]:
        # Do not merge the SDK defaults: they include OpenAI-specific environment values.
        return {
            "Accept": "application/json",
            "Content-Type": "application/json",
            "Authorization": f"Bearer {self.api_key}",
        }
