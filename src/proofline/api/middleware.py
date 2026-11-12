"""Request correlation and an explicit browser-origin boundary for mutations."""

import re
from uuid import uuid4

from fastapi import FastAPI, Request
from starlette.middleware.base import RequestResponseEndpoint
from starlette.responses import Response

from proofline.api.errors import error_response
from proofline.config import Settings

_SAFE_REQUEST_ID = re.compile(r"[A-Za-z0-9._:-]{1,128}\Z")
_SAFE_METHODS = {"GET", "HEAD", "OPTIONS"}


def register_request_middleware(app: FastAPI, settings: Settings) -> None:
    origins = {settings.app_origin, *settings.dev_origins}

    @app.middleware("http")
    async def request_boundary(request: Request, call_next: RequestResponseEndpoint) -> Response:
        supplied = request.headers.get("X-Request-ID", "")
        request.state.request_id = (
            supplied if _SAFE_REQUEST_ID.fullmatch(supplied) else str(uuid4())
        )
        origin = request.headers.get("Origin")
        if request.method not in _SAFE_METHODS and origin is not None and origin not in origins:
            return error_response(
                request, 403, "ORIGIN_NOT_ALLOWED", "The request origin is not allowed."
            )
        response = await call_next(request)
        response.headers["X-Request-ID"] = request.state.request_id
        return response
