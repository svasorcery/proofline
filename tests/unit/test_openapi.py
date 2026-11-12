from pathlib import Path

import pytest

from proofline.api.app import create_app
from proofline.config import Settings


@pytest.mark.parametrize(
    "path",
    [
        "/api/v1/cases/{case_id}/runs",
        "/api/v1/proposals/{revision_id}/decision",
    ],
)
def test_openapi_requires_the_idempotency_header(path: str) -> None:
    app = create_app(
        Settings(
            database_url="postgresql+psycopg://unused/unused",
            fixtures_dir=Path(__file__).resolve().parents[2] / "fixtures",
        )
    )
    try:
        operation = app.openapi()["paths"][path]["post"]
        header = next(
            parameter
            for parameter in operation["parameters"]
            if parameter["in"] == "header" and parameter["name"] == "Idempotency-Key"
        )
        assert header["required"] is True
        assert header["schema"]["type"] == "string"
    finally:
        app.state.service.database.dispose()
