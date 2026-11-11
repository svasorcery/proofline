"""Synthetic examples exercise public interfaces without importing research code."""

import json
from pathlib import Path
from typing import Any

import pytest

ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture
def valid_payload() -> dict[str, Any]:
    return {
        "case_id": "example",
        "facts": [
            {
                "key": "cancellation_status",
                "value": "confirmed",
                "evidence": [
                    {
                        "source_id": "notice",
                        "fragments": ["Cancelled at 2026-01-02T10:00:00+03:00."],
                    }
                ],
            }
        ],
        "timeline": [
            {
                "event_type": "supplier_cancelled",
                "time": {"value": "2026-01-02T10:00:00+03:00", "role": "occurred"},
                "evidence": [
                    {
                        "source_id": "notice",
                        "fragments": ["Cancelled at 2026-01-02T10:00:00+03:00."],
                    }
                ],
            }
        ],
        "readiness": "ready_for_rules",
        "follow_up": {"action": "none", "locator": None, "requirements": []},
    }


@pytest.fixture
def recorded_conditions() -> list[tuple[dict[str, Any], dict[str, Any], dict[str, Any]]]:
    catalog = json.loads((ROOT / "fixtures/replay/v3/catalog.json").read_text(encoding="utf-8"))
    cases = json.loads((ROOT / "fixtures/cases/travel-cases.json").read_text(encoding="utf-8"))
    by_id = {case["id"]: case for case in cases}
    return [
        (
            record,
            by_id[record["case_id"]],
            json.loads((ROOT / record["output_path"]).read_text(encoding="utf-8")),
        )
        for record in catalog["records"]
    ]
