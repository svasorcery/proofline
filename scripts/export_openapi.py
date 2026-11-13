"""Export the maintained HTTP schema without connecting to a database or model."""

import json
from pathlib import Path

from proofline.api.app import create_app
from proofline.config import Settings


def main() -> None:
    root = Path(__file__).resolve().parents[1]
    # SQLAlchemy opens connections lazily. This schema-only factory never enters
    # the application's lifespan, so recovery and database access do not run.
    config = Settings(
        _env_file=None,
        database_url="postgresql+psycopg://schema:schema@127.0.0.1/proofline_schema",
        fixtures_dir=root / "fixtures",
    )
    app = create_app(config)
    try:
        print(json.dumps(app.openapi(), ensure_ascii=True, sort_keys=True))
    finally:
        app.state.service.database.dispose()


if __name__ == "__main__":
    main()
