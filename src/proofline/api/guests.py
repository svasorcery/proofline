"""The guest cookie is the only way to select a guest workspace over HTTP."""

from typing import Annotated

from fastapi import Depends, Request, Response

from proofline.application.errors import ApplicationError
from proofline.application.guests import GuestSessions

_COOKIE = "proofline_guest"


def guest_owner(request: Request) -> str | None:
    sessions: GuestSessions | None = request.app.state.guest_sessions
    if sessions is None:
        return None
    guest_id = sessions.resolve(request.cookies.get(_COOKIE))
    if guest_id is None:
        raise ApplicationError(
            "GUEST_SESSION_REQUIRED", "Open a guest workspace first.", status_code=401
        )
    return guest_id


type GuestOwner = Annotated[str | None, Depends(guest_owner)]


def open_guest_session(request: Request, *, secure: bool) -> Response:
    sessions: GuestSessions | None = request.app.state.guest_sessions
    if sessions is None:
        raise ApplicationError("NOT_FOUND", "Guest mode is disabled.", status_code=404)
    if request.headers.get("Origin") is None:
        raise ApplicationError(
            "ORIGIN_NOT_ALLOWED", "A guest workspace requires an allowed origin.", status_code=403
        )
    response = Response(status_code=204)
    if sessions.resolve(request.cookies.get(_COOKIE)) is None:
        response.set_cookie(
            _COOKIE,
            sessions.create(),
            path="/api/v1",
            httponly=True,
            secure=secure,
            samesite="strict",
        )
    return response
