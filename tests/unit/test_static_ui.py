from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from proofline.api.app import create_app
from proofline.api.static import register_static_ui
from proofline.config import Settings


@pytest.fixture
def ui_dir(tmp_path: Path) -> Path:
    (tmp_path / "assets").mkdir()
    (tmp_path / "index.html").write_text("<!doctype html><title>Proofline UI</title>")
    (tmp_path / "assets" / "app-ab12.js").write_text("console.log('synthetic demo');")
    (tmp_path / "assets" / "app-abcdefgh.js").write_text("console.log('hashed asset');")
    (tmp_path / "private.txt").write_text("not a public asset")
    return tmp_path


def test_ui_serves_only_known_routes_and_assets(ui_dir: Path) -> None:
    app = FastAPI()
    register_static_ui(app, ui_dir)
    client = TestClient(app)

    for route in ("/", "/cases/V04"):
        response = client.get(route)
        assert response.status_code == 200
        assert response.headers["content-type"].startswith("text/html")
        assert "Proofline UI" in response.text
        assert response.headers["cache-control"] == "no-cache"
        assert client.head(route).status_code == 200

    asset = client.get("/assets/app-ab12.js")
    assert asset.status_code == 200
    assert "synthetic demo" in asset.text
    assert asset.headers["cache-control"] == "no-cache"
    assert client.get("/assets/app-abcdefgh.js").headers["cache-control"] == (
        "public, max-age=31536000, immutable"
    )
    assert client.get("/assets/missing.js").status_code == 404
    for route in ("/unknown", "/private.txt", "/api/v1/unknown", "/cases/V04/unknown"):
        response = client.get(route)
        assert response.status_code == 404
        assert response.headers["content-type"].startswith("application/json")
        assert "Proofline UI" not in response.text
    assert client.get("/assets/%2e%2e/private.txt").status_code == 404
    assert client.post("/").status_code == 405


def test_missing_build_is_valid_api_only_configuration(tmp_path: Path) -> None:
    app = FastAPI()
    register_static_ui(app, tmp_path / "not-built")
    assert TestClient(app).get("/").status_code == 404


def test_ui_preserves_api_docs_and_safe_error_envelopes(ui_dir: Path) -> None:
    app = create_app(
        Settings(
            database_url="postgresql+psycopg://unused/unused",
            fixtures_dir=Path(__file__).resolve().parents[2] / "fixtures",
            ui_dir=ui_dir,
        )
    )
    try:
        client = TestClient(app)
        assert client.get("/").status_code == 200
        assert client.get("/api/v1/health").json() == {"status": "ok"}
        assert client.get("/health").json() == {"status": "ok"}
        assert client.get("/api/v1/capabilities").status_code == 200
        assert client.get("/docs").status_code == 200
        assert client.get("/openapi.json").json()["info"]["title"] == "Proofline replay API"
        response = client.get("/api/v1/unknown")
        assert response.status_code == 404
        assert response.json()["code"] == "HTTP_404"
        assert response.json()["request_id"] == response.headers["x-request-id"]
        assert "/cases/{case_id}" not in app.openapi()["paths"]
    finally:
        app.state.service.database.dispose()


def test_ui_directory_can_be_configured_from_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("PROOFLINE_UI_DIR", "/custom/ui")
    settings = Settings(database_url="postgresql+psycopg://unused/unused")
    assert settings.ui_dir == Path("/custom/ui")
