"""The Python test suite must not open real provider HTTP connections."""

import asyncio

import httpx
import pytest


def test_real_sync_http_is_blocked_before_connection():
    with httpx.Client() as client, pytest.raises(AssertionError, match="Real HTTP disabled"):
        client.get("http://127.0.0.1:0")


def test_real_async_http_is_blocked_before_connection():
    async def attempt():
        async with httpx.AsyncClient() as client:
            await client.get("http://127.0.0.1:0")

    with pytest.raises(AssertionError, match="Real HTTP disabled"):
        asyncio.run(attempt())
