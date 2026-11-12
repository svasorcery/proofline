"""Explicit local operations: migrate, seed synthetic data, or serve one API process."""

import argparse
import json

import uvicorn
from pydantic import ValidationError
from sqlalchemy.exc import SQLAlchemyError

from proofline.application.seed import seed_demo
from proofline.config import Settings
from proofline.persistence.database import open_database
from proofline.persistence.migrate import SCHEMA_REVISION, schema_is_current, upgrade_database
from proofline.providers.replay import ReplayCatalog


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("migrate", help="Apply database migrations; never seed or reset data")
    commands.add_parser(
        "seed", help="Create missing synthetic cases without overwriting existing cases"
    )
    serve = commands.add_parser("serve", help="Run the single-process local demonstration API")
    serve.add_argument("--host", default="127.0.0.1")
    serve.add_argument("--port", type=int, default=8000)
    args = parser.parse_args()
    try:
        settings = Settings()
    except ValidationError as error:
        fields = sorted(
            {".".join(map(str, item["loc"])) for item in error.errors(include_input=False)}
        )
        parser.exit(2, f"Invalid configuration fields: {', '.join(fields)}\n")
    if args.command == "serve":
        if not 1 <= args.port <= 65535:
            parser.error("port must be between 1 and 65535")
        from proofline.api.app import create_app

        uvicorn.run(create_app(settings), host=args.host, port=args.port, workers=1)
        return 0
    database = open_database(settings.database_url)
    try:
        if args.command == "migrate":
            upgrade_database(database.engine)
            print(f"Database schema: {SCHEMA_REVISION}")
        else:
            if not schema_is_current(database.engine):
                parser.exit(1, "Database is not ready; run proofline migrate first.\n")
            created = seed_demo(database, ReplayCatalog(settings.fixtures_dir))
            print(json.dumps({"created_case_ids": created, "existing_cases_preserved": True}))
    except SQLAlchemyError:
        parser.exit(1, "Database operation failed; check DATABASE_URL and database availability.\n")
    finally:
        database.dispose()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
