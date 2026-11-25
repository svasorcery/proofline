"""Offline experiment for a verified confirmation already in the case snapshot."""

import json
from copy import deepcopy
from dataclasses import dataclass, replace
from pathlib import Path

from prototype_model_choices import check_wire_schema

from proofline.domain.assembly import AssemblyOutput, RequirementKey
from proofline.domain.inputs import AnalysisInput
from proofline.validation.assembly import validate_assembly

ROOT = Path(__file__).resolve().parents[1]
V04 = ROOT / "evals/live/v9-groq-qwen-choices-v04-2026-11-18/full-V04"
V01 = ROOT / "evals/live/v7-groq-qwen-choices-2026-11-17/ablation-V01"
REVIEWED_REPLY = (
    "Текст принятого сообщения:\n"
    "«Если там тихий номер с окнами во двор, согласен на соседний корпус. "
    "Если окна на улицу, этот вариант мне не подходит. "
    "Подтвердите, пожалуйста, куда выходят окна.»"
)


@dataclass(frozen=True, slots=True)
class IncludedProof:
    requirement: RequirementKey
    case_id: str
    source_id: str
    source_version_id: str
    text_sha256: str
    fragment: str

    def matches(self, snapshot: AnalysisInput) -> bool:
        return snapshot.case_id == self.case_id and any(
            s.included
            and s.source_id == self.source_id
            and s.version_id == self.source_version_id
            and s.text_sha256 == self.text_sha256
            and s.text is not None
            and bool(self.fragment.strip())
            and s.text.count(self.fragment) == 1
            for s in snapshot.catalog
        )


def load(path: Path):
    return json.loads(path.read_text(encoding="utf-8"))


def archived(folder: Path):
    run = load(folder / "run.json")
    snapshot = AnalysisInput.model_validate_json(json.dumps(run["snapshot"]))
    return snapshot, load(folder / "request.json"), json.loads(run["raw_output"])


def confirmed(snapshot: AnalysisInput, proofs: tuple[IncludedProof, ...]) -> set[RequirementKey]:
    return {proof.requirement for proof in proofs if proof.matches(snapshot)}


def narrowed_request(request: dict, covered: set[RequirementKey]) -> dict:
    result = deepcopy(request)
    allowed = result["response_format"]["json_schema"]["schema"]["$defs"]["RequirementKey"]["enum"]
    remaining = [key for key in allowed if key not in covered]
    if not remaining:
        raise ValueError("No requirement keys remain; review readiness separately")
    allowed[:] = remaining
    return result


def preview_without_covered_questions(original: dict, covered: set[RequirementKey]) -> dict:
    preview = deepcopy(original)
    follow_up = preview["follow_up"]
    remaining = [key for key in follow_up["requirements"] if key not in covered]
    if follow_up["requirements"] and not remaining:
        raise ValueError("The last question was removed; review readiness separately")
    follow_up["requirements"] = remaining
    return preview


def check_card(raw: dict, snapshot: AnalysisInput) -> None:
    assembly = AssemblyOutput.model_validate(
        {
            **raw,
            "facts": [{"key": key, **raw["facts"][key]} for key in snapshot.requested_fact_keys],
        }
    )
    assert not validate_assembly(assembly, snapshot.to_validation_context()).issues


def main() -> None:
    v04, request04, raw04 = archived(V04)
    source = next(s for s in v04.catalog if s.source_id == "f71822cddbf648f2")
    assert source.version_id is not None and source.text_sha256 is not None
    # The meaning of this fragment was reviewed against the fixture. The binding
    # checks that the same evidence is still present in this exact case and version.
    proof = IncludedProof(
        "original_acceptance_reply",
        v04.case_id,
        source.source_id,
        source.version_id,
        source.text_sha256,
        REVIEWED_REPLY,
    )
    assert proof.matches(v04)
    reviewed = (proof,)
    covered04 = confirmed(v04, reviewed)
    assert covered04 == {"original_acceptance_reply"}
    changed = narrowed_request(request04, covered04)
    schema = changed["response_format"]["json_schema"]["schema"]
    sample = preview_without_covered_questions(raw04, covered04)
    assert raw04 == json.loads(load(V04 / "run.json")["raw_output"])
    assert sample["follow_up"] == {
        "action": "ask_user",
        "locator": None,
        "requirements": ["quiet_room", "courtyard_windows"],
    }
    assert {k: v for k, v in sample.items() if k != "follow_up"} == {
        k: v for k, v in raw04.items() if k != "follow_up"
    }
    v04_checks = check_wire_schema(
        schema,
        {
            "old_answer_with_redundant_question": (raw04, False),
            "preview_with_only_missing_confirmations": (sample, True),
        },
    )
    check_card(sample, v04)

    v01, request01, raw01 = archived(V01)
    v01_covered = confirmed(v01, reviewed)
    assert not v01_covered and narrowed_request(request01, v01_covered) == request01
    assert preview_without_covered_questions(raw01, v01_covered) == raw01
    assert raw01["follow_up"] == {
        "action": "read_source",
        "locator": "1618ae5e5b894145",
        "requirements": ["original_acceptance_reply"],
    }
    v01_checks = check_wire_schema(
        request01["response_format"]["json_schema"]["schema"],
        {
            "open_the_unread_letter": (raw01, True),
        },
    )
    check_card(raw01, v01)

    edited = v04.model_dump(mode="json")
    entry = next(s for s in edited["catalog"] if s["source_id"] == proof.source_id)
    entry.update(included=False, text=None, version_id=None, text_sha256=None)
    excluded = AnalysisInput.model_validate_json(json.dumps(edited))
    controls = {
        "excluded_source": confirmed(excluded, reviewed),
        "old_version": confirmed(v04, (replace(proof, source_version_id="old-version"),)),
        "wrong_hash": confirmed(v04, (replace(proof, text_sha256="wrong-hash"),)),
        "absent_fragment": confirmed(v04, (replace(proof, fragment="MIME is in archive."),)),
    }
    for label, covered in controls.items():
        assert not covered, label
        assert narrowed_request(request04, covered) == request04, label
        assert preview_without_covered_questions(raw04, covered) == raw04, label

    only_covered = deepcopy(raw04)
    only_covered["follow_up"]["requirements"] = ["original_acceptance_reply"]
    try:
        preview_without_covered_questions(only_covered, covered04)
    except ValueError:
        last_question = "needs separate review; readiness was not changed"
    else:
        raise AssertionError("An empty set of remaining questions requires review")

    print(
        json.dumps(
            {
                "new_model_calls": 0,
                "V04": {
                    "removed": sorted(covered04),
                    "follow_up": sample["follow_up"],
                    "checks": v04_checks,
                },
                "V01": {"removed": [], "follow_up": raw01["follow_up"], "checks": v01_checks},
                "controls": {
                    "excluded_source_keeps_the_question": True,
                    "old_version_keeps_the_question": True,
                    "wrong_hash_keeps_the_question": True,
                    "absent_fragment_keeps_the_question": True,
                    "last_question": last_question,
                },
            },
            ensure_ascii=False,
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
