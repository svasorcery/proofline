"""Serve the built operator UI without hiding API errors behind an SPA fallback."""

import re
from pathlib import Path

from fastapi import FastAPI
from starlette.responses import FileResponse, Response
from starlette.staticfiles import StaticFiles
from starlette.types import Scope

_HASHED_ASSET = re.compile(r"-[A-Za-z0-9_-]{8,}\.[A-Za-z0-9]+$")


class _BuiltAssets(StaticFiles):
    async def get_response(self, path: str, scope: Scope) -> Response:
        response = await super().get_response(path, scope)
        if response.status_code == 200:
            response.headers["Cache-Control"] = (
                "public, max-age=31536000, immutable"
                if _HASHED_ASSET.search(Path(path).name)
                else "no-cache"
            )
            response.headers["X-Content-Type-Options"] = "nosniff"
        return response


def register_static_ui(app: FastAPI, ui_dir: Path) -> None:
    """API-only development works without a frontend build or Node runtime."""
    index = ui_dir / "index.html"
    if not index.is_file():
        return

    def operator_ui() -> FileResponse:
        return FileResponse(
            index,
            headers={"Cache-Control": "no-cache", "X-Content-Type-Options": "nosniff"},
        )

    app.add_api_route("/", operator_ui, methods=["GET", "HEAD"], include_in_schema=False)
    app.add_api_route(
        "/cases/{case_id}", operator_ui, methods=["GET", "HEAD"], include_in_schema=False
    )
    assets = ui_dir / "assets"
    if assets.is_dir():
        app.mount("/assets", _BuiltAssets(directory=assets), name="ui-assets")
