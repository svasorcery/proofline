import copy
import json
from pathlib import Path
from typing import Any

import pytest
from pydantic import ValidationError

from proofline.domain.assembly import AssemblyOutput
from proofline.schema import contract_matches, generated_schema

ROOT = Path(__file__).resolve().parents[2]


def test_all_recorded_outputs_round_trip_without_repair(recorded_conditions: list[Any]) -> None:
    for _, _, payload in recorded_conditions:
        result = AssemblyOutput.model_validate_json(json.dumps(payload))
        assert result.model_dump(mode="json") == payload


@pytest.mark.parametrize(
    "path,value",
    [
        (("case_id",), 123),
        (("case_id",), True),
        (("facts",), {}),
        (("facts", 0, "key"), "refund_approved"),
        (("facts", 0, "value"), "CONFIRMED"),
        (("facts", 0, "evidence", 0, "source_id"), 1),
        (("facts", 0, "evidence", 0, "fragments"), []),
        (("facts", 0, "evidence", 0, "fragments", 0), False),
        (("timeline", 0, "time", "value"), 1700000000),
        (("timeline", 0, "time", "role"), "exported"),
        (("follow_up", "locator"), 123),
        (("follow_up", "action"), "send_email"),
        (
            (
                "follow_up",
                "requirements",
            ),
            ["approve_payment"],
        ),
        (("readiness",), "approved"),
    ],
)
def test_wire_contract_rejects_coercions_and_unknown_values(
    valid_payload: dict[str, Any],
    path: tuple[str | int, ...],
    value: Any,
) -> None:
    target = valid_payload
    for part in path[:-1]:
        target = target[part]
    target[path[-1]] = value
    with pytest.raises(ValidationError):
        AssemblyOutput.model_validate(valid_payload)
    with pytest.raises(ValidationError):
        AssemblyOutput.model_validate_json(json.dumps(valid_payload))


@pytest.mark.parametrize(
    "path",
    [
        (),
        ("facts", 0),
        ("facts", 0, "evidence", 0),
        ("timeline", 0),
        ("timeline", 0, "time"),
        ("follow_up",),
    ],
)
def test_extra_fields_are_rejected_at_every_object_boundary(
    valid_payload: dict[str, Any],
    path: tuple[str | int, ...],
) -> None:
    target = valid_payload
    for part in path:
        target = target[part]
    target["approved"] = True
    with pytest.raises(ValidationError, match="Extra inputs are not permitted"):
        AssemblyOutput.model_validate(valid_payload)


def test_nullable_locator_is_still_required(valid_payload: dict[str, Any]) -> None:
    del valid_payload["follow_up"]["locator"]
    with pytest.raises(ValidationError):
        AssemblyOutput.model_validate(valid_payload)


def test_generated_schema_matches_the_frozen_wire_contract() -> None:
    frozen = json.loads((ROOT / "contracts/assembly-output.schema.json").read_text())
    assert contract_matches(frozen)
    changed = copy.deepcopy(frozen)
    changed["properties"]["follow_up"]["properties"]["locator"]["type"] = "string"
    assert not contract_matches(changed)
    assert generated_schema()["additionalProperties"] is False
