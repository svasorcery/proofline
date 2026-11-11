import copy
from typing import Any

from proofline.domain.assembly import AssemblyOutput
from proofline.domain.sources import SourceReference, SourceSnapshot, ValidationContext
from proofline.validation import validate_assembly


def test_recorded_outputs_use_only_the_public_runtime_contract(
    recorded_conditions: list[Any],
) -> None:
    states = {}
    for record, case, payload in recorded_conditions:
        original = copy.deepcopy(payload)
        all_sources = case["initial_sources"] + case["sources"]
        selected = [s for s in all_sources if s["id"] != record["removed_source_id"]]
        visible_text = "\n".join(s["text"] for s in selected)
        locators = {s["locator"] for s in case["catalog"]} | {s["locator"] for s in selected}
        references = {
            s["id"]: SourceReference(
                case_id=case["id"], source_id=s["id"], kind=s["kind"], locator=s["locator"]
            )
            for s in all_sources
            if s["locator"] in locators or s["locator"] in visible_text
        }
        context = ValidationContext(
            case_id=case["id"],
            requested_fact_keys=tuple(case["questions"]),
            catalog=tuple(references.values()),
            sources=tuple(
                SourceSnapshot.from_text(
                    references[s["id"]], version_id="recorded:v3", text=s["text"]
                )
                for s in selected
            ),
        )
        result = validate_assembly(AssemblyOutput.model_validate(payload), context)
        condition = f"{record['mode']}/{case['id']}"
        states[condition] = result.state
        if condition == "full/V04":
            assert {issue.code for issue in result.issues} == {
                "acceptance_fact_event_conflict",
                "repeated_snapshot_lookup",
            }
        else:
            assert result.issues == (), (condition, result.issues)
        assert payload == original
    assert len(states) == 9
    assert states["full/V04"] == "blocked_for_approval"
    # Structural validity cannot detect V02's semantic omission from reference answers.
    assert states["full/V02"] == "needs_operator_review"
    assert list(states.values()).count("needs_operator_review") == 8
