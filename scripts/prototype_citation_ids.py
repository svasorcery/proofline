"""Exercise choosing citation IDs on two recorded cases, without a provider."""

import json
from copy import deepcopy
from dataclasses import dataclass
from pathlib import Path

from prototype_model_choices import check_wire_schema

from proofline.domain.assembly import AssemblyOutput, Evidence
from proofline.domain.inputs import AnalysisInput, canonical_json, json_digest
from proofline.domain.present_confirmations import IncludedAcceptanceReply
from proofline.validation.assembly import validate_assembly
from proofline.validation.reply_events import check_reply_events

ROOT = Path(__file__).resolve().parents[1]
ARCHIVE = ROOT / "evals/live"
V02 = ARCHIVE / "v8-groq-qwen-choices-v02-v04-2026-11-17/full-V02"
V04 = ARCHIVE / "v11-groq-qwen-same-document-v04-2026-11-18/full-V04"
INSTRUCTION = (
    "\n\nFor this exercise, evidence in each fact and event is an array of citation ID strings. "
    "Choose IDs from the source's citation_lines. Each ID identifies the exact text of one "
    "line. The application resolves the ID to that text. Choose the lines that together "
    "support this fact or event. In the answer use IDs from the current case only. "
    "A line with citation_id=null is context only."
)


@dataclass(frozen=True, slots=True)
class Span:
    source_id: str
    version_id: str
    text_sha256: str
    start: int
    end: int


@dataclass(frozen=True, slots=True)
class Citations:
    snapshot_digest: str
    model_input: dict
    spans: dict[str, Span]

    def id_at_line(self, source_id: str, one_based_line: int) -> str:
        source = next(
            source for source in self.model_input["catalog"] if source["source_id"] == source_id
        )
        citation_id = source["citation_lines"][one_based_line - 1]["citation_id"]
        if citation_id is None:
            raise ValueError("Line is not selectable")
        return citation_id


def load(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def catalog(snapshot: AnalysisInput) -> Citations:
    model_input = snapshot.model_dump(mode="json")
    snapshot_digest = json_digest(model_input)
    spans = {}
    for entry, payload in zip(snapshot.catalog, model_input["catalog"], strict=True):
        if not entry.included:
            continue
        source = entry.version()
        start = 0
        lines = []
        for line in source.text.split("\n"):
            end = start + len(line)
            citation_id = None
            if (
                line.strip()
                and source.text.find(line) == start
                and source.text.find(line, start + 1) < 0
            ):
                citation_id = (
                    "q" + json_digest([snapshot_digest, source.source_id, start, end])[:16]
                )
                if citation_id in spans:
                    raise ValueError("Duplicate citation ID")
                spans[citation_id] = Span(
                    source.source_id, source.version_id, source.text_sha256, start, end
                )
            lines.append({"citation_id": citation_id, "text": line})
            start = end + 1
        original_text = payload.pop("text")
        assert "\n".join(line["text"] for line in lines) == original_text
        payload["citation_lines"] = lines
    return Citations(snapshot_digest, model_input, spans)


def request_preview(original: dict, citations: Citations) -> dict:
    if not citations.spans:
        raise ValueError("There are no selectable citations")
    request = deepcopy(original)
    request["messages"][0]["content"] += INSTRUCTION
    request["messages"][1]["content"] = canonical_json(citations.model_input)
    output = request["response_format"]["json_schema"]
    output["name"] = "assembly_citation_ids_preview"
    output["schema"]["$defs"]["Evidence"] = {"type": "string", "enum": list(citations.spans)}
    return request


def decode(answer: dict, snapshot: AnalysisInput, citations: Citations) -> AssemblyOutput:
    if json_digest(snapshot.model_dump(mode="json")) != citations.snapshot_digest:
        raise ValueError("Citation catalogue belongs to another snapshot")
    if answer["case_id"] != snapshot.case_id:
        raise ValueError("Answer belongs to another case")
    if set(answer["facts"]) != set(snapshot.requested_fact_keys):
        raise ValueError("Answer must contain exactly the requested facts")
    sources = {source.source_id: source for source in snapshot.to_validation_context().sources}

    def evidence(ids: list[str]) -> list[Evidence]:
        if not isinstance(ids, list) or any(not isinstance(cid, str) for cid in ids):
            raise ValueError("Evidence must be a list of citation IDs")
        if len(set(ids)) != len(ids):
            raise ValueError("Repeated citation ID in the same field")
        grouped: dict[str, list[str]] = {}
        for cid in ids:
            span = citations.spans.get(cid)
            if span is None:
                raise ValueError(f"Unknown citation ID {cid}")
            source = sources.get(span.source_id)
            if source is None or (source.version_id, source.text_sha256) != (
                span.version_id,
                span.text_sha256,
            ):
                raise ValueError("Citation source version changed")
            grouped.setdefault(span.source_id, []).append(source.text[span.start : span.end])
        return [
            Evidence(source_id=source_id, fragments=fragments)
            for source_id, fragments in grouped.items()
        ]

    return AssemblyOutput.model_validate(
        {
            **answer,
            "facts": [
                {
                    "key": key,
                    **answer["facts"][key],
                    "evidence": evidence(answer["facts"][key]["evidence"]),
                }
                for key in snapshot.requested_fact_keys
            ],
            "timeline": [
                {**event, "evidence": evidence(event["evidence"])} for event in answer["timeline"]
            ],
        }
    )


def select_v02(citations: Citations) -> tuple[dict, dict]:
    def form(*lines: int) -> list[str]:
        return [citations.id_at_line("5c3fe2fc12a3430e", line) for line in lines]

    def log(*lines: int) -> list[str]:
        return [citations.id_at_line("9edd7227853c42a4", line) for line in lines]

    return (
        {"cancellation_status": log(1), "withdrawal_timing": form(4, 5) + log(2, 3)},
        {
            "supplier_cancelled": log(1),
            "customer_replied": form(2, 4),
            "customer_message_received": form(3, 4),
        },
    )


def select_v04(citations: Citations) -> tuple[dict, dict]:
    def reply(*lines: int) -> list[str]:
        return [citations.id_at_line("f71822cddbf648f2", line) for line in lines]

    return (
        {
            "alternative_acceptance": [*reply(4, 5), citations.id_at_line("60605d18ce3c437c", 1)],
        },
        {
            "alternative_offered": [citations.id_at_line("7c4b78e8ce5a4af4", n) for n in (2, 3)],
            "customer_replied": reply(2, 4),
            "customer_message_received": reply(1),
        },
    )


def review(answer: dict, snapshot: AnalysisInput, citations: Citations):
    card = decode(answer, snapshot, citations)
    validation = validate_assembly(card, snapshot.to_validation_context())
    if snapshot.case_id == "V04":
        binding = IncludedAcceptanceReply.model_validate(
            load(V04.parent / "preflight.json")["reply_binding"]
        )
        assert binding.event_evidence is not None and binding.matches(snapshot)
        validation = check_reply_events(card, snapshot, validation, binding)
    return card, validation


def main() -> None:
    cases = {}
    for folder in (V04, V02):
        run = load(folder / "run.json")
        snapshot = AnalysisInput.model_validate_json(json.dumps(run["snapshot"]))
        original = load(folder / "request.json")
        assert json.loads(original["messages"][1]["content"]) == snapshot.model_dump(mode="json")
        raw = json.loads(run["raw_output"])
        citations = catalog(snapshot)
        assert citations == catalog(snapshot)
        selector = select_v04 if snapshot.case_id == "V04" else select_v02
        facts, events = selector(citations)
        # A hand-picked stand-in for a model response. The archived result stays evidence.
        answer = deepcopy(raw)
        for key, fact in answer["facts"].items():
            fact["evidence"] = facts[key]
        for event in answer["timeline"]:
            event["evidence"] = events[event["event_type"]]
        archived_card = AssemblyOutput.model_validate(load(folder / "proposal.json")["body"])
        archived_check = validate_assembly(archived_card, snapshot.to_validation_context())
        assert archived_check.state == "blocked_for_approval"
        cases[snapshot.case_id] = (snapshot, original, raw, citations, answer, archived_check)

    results = {}
    for case_id, (snapshot, original, raw, citations, answer, archived_check) in cases.items():
        request = request_preview(original, citations)
        schema = request["response_format"]["json_schema"]["schema"]
        other = cases["V02" if case_id == "V04" else "V04"][3]
        samples = {"simulated_selection": (answer, True), "archived_free_text": (raw, False)}
        for label, invalid_id in (
            ("unknown_id", "q-unknown"),
            ("other_case_id", next(iter(other.spans))),
        ):
            bad = deepcopy(answer)
            next(iter(bad["facts"].values()))["evidence"] = [invalid_id]
            samples[label] = (bad, False)
            try:
                decode(bad, snapshot, citations)
            except ValueError as exc:
                assert str(exc).startswith("Unknown citation ID")
            else:
                raise AssertionError("Unexpected citation ID was accepted")
        card, validation = review(answer, snapshot, citations)
        assert not validation.issues
        assert card.readiness == raw["readiness"]
        assert card.follow_up.model_dump(mode="json") == raw["follow_up"]
        assert {fact.key: fact.value for fact in card.facts} == {
            key: fact["value"] for key, fact in raw["facts"].items()
        }
        assert [(event.event_type, event.time.model_dump()) for event in card.timeline] == [
            (event["event_type"], event["time"]) for event in raw["timeline"]
        ]

        changed = snapshot.model_dump(mode="json")
        source = changed["catalog"][0]
        source["version_id"] += "-new"
        fresh_snapshot = AnalysisInput.model_validate_json(json.dumps(changed))
        assert not set(citations.spans).intersection(catalog(fresh_snapshot).spans)
        try:
            decode(answer, fresh_snapshot, citations)
        except ValueError as exc:
            assert str(exc) == "Citation catalogue belongs to another snapshot"
        else:
            raise AssertionError("Stale catalogue was accepted")
        source.update(included=False, version_id=None, text_sha256=None, text=None)
        excluded = catalog(AnalysisInput.model_validate_json(json.dumps(changed)))
        assert "citation_lines" not in excluded.model_input["catalog"][0]
        assert all(span.source_id != source["source_id"] for span in excluded.spans.values())

        misplaced_issue = None
        if case_id == "V04":
            misplaced = deepcopy(answer)
            next(
                event
                for event in misplaced["timeline"]
                if event["event_type"] == "customer_replied"
            )["evidence"] = [citations.id_at_line("f71822cddbf648f2", line) for line in (2, 1)]
            samples["literal_id_at_wrong_event"] = (misplaced, True)
            _, misplaced_validation = review(misplaced, snapshot, citations)
            assert {issue.code for issue in misplaced_validation.issues} == {
                "reply_text_missing_from_send"
            }
            misplaced_issue = misplaced_validation.issues[0].code

        results[case_id] = {
            "selectable_ids": len(citations.spans),
            "archived_card_status": archived_check.state,
            "archived_literal_fragments": len(archived_check.fragments),
            "resolved_literal_fragments": len(validation.fragments),
            "card_status": validation.state,
            "requirements": card.follow_up.requirements,
            "facts_and_timeline_preserved": True,
            "wire_check": check_wire_schema(schema, samples),
            "stale_catalogue_rejected": True,
            "excluded_source_has_no_citations": True,
            "request_bytes_before": len(canonical_json(original).encode()),
            "request_bytes_with_ids": len(canonical_json(request).encode()),
        }
        if misplaced_issue is not None:
            results[case_id]["wrong_event_validation"] = misplaced_issue
    print(
        json.dumps(
            {"new_model_calls": 0, "selection": "manual simulation", "cases": results},
            ensure_ascii=False,
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
