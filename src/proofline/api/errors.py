"""One safe error envelope for validation, transport and persistence failures."""

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from pydantic import BaseModel, JsonValue
from sqlalchemy.exc import SQLAlchemyError
from starlette.exceptions import HTTPException
from starlette.responses import JSONResponse

from proofline.application.errors import ApplicationError


class ErrorView(BaseModel):
    code: str
    message: str
    details: dict[str, JsonValue]
    request_id: str


def error_response(
    request: Request,
    status_code: int,
    code: str,
    message: str,
    details: dict[str, JsonValue] | None = None,
) -> JSONResponse:
    request_id: str = request.state.request_id
    body = ErrorView(code=code, message=message, details=details or {}, request_id=request_id)
    return JSONResponse(
        body.model_dump(mode="json"), status_code=status_code, headers={"X-Request-ID": request_id}
    )


def register_error_handlers(app: FastAPI) -> None:
    @app.exception_handler(ApplicationError)
    async def application_error(request: Request, exc: ApplicationError) -> JSONResponse:
        return error_response(request, exc.status_code, exc.code, exc.message, exc.details)

    @app.exception_handler(RequestValidationError)
    async def validation_error(request: Request, exc: RequestValidationError) -> JSONResponse:
        # Pydantic's input, context and message can contain the submitted text.
        errors: list[JsonValue] = [
            {"loc": list(error["loc"]), "type": error["type"]} for error in exc.errors()
        ]
        return error_response(
            request, 422, "VALIDATION_ERROR", "Request validation failed.", {"errors": errors}
        )

    @app.exception_handler(HTTPException)
    async def http_error(request: Request, exc: HTTPException) -> JSONResponse:
        return error_response(
            request,
            exc.status_code,
            f"HTTP_{exc.status_code}",
            "The requested HTTP operation could not be completed.",
        )

    @app.exception_handler(SQLAlchemyError)
    async def database_error(request: Request, exc: SQLAlchemyError) -> JSONResponse:
        return error_response(
            request,
            503,
            "DATABASE_UNAVAILABLE",
            "The database is unavailable or its schema is not ready.",
        )

    @app.exception_handler(Exception)
    async def unexpected_error(request: Request, exc: Exception) -> JSONResponse:
        return error_response(request, 500, "INTERNAL_ERROR", "The request could not be completed.")
