"""Check recorded live evidence without requiring its old application runtime."""

import argparse
import hashlib
import json
import re
import sys
from contextlib import suppress
from datetime import UTC, datetime
from pathlib import Path

CITATION_SCHEMA = "assembly-citation-ids-v1"
COMPACT_CITATION_SCHEMA = "assembly-citation-ids-compact-v1"
CITATION_SCHEMAS = {CITATION_SCHEMA, COMPACT_CITATION_SCHEMA}
CHOICES_SCHEMAS = {"assembly-choices-v1", *CITATION_SCHEMAS}
TIMELINE_ORDER = "utc-v1"
# The frozen parsing rules for utc-v1, independent of the current runtime.
UTC_V1_TIMESTAMP = re.compile(
    r"[0-9]{4}-[0-9]{2}-[0-9]{2}T(?:[01][0-9]|2[0-3]):[0-5][0-9]"
    r"(?::[0-5][0-9](?:[.,][0-9]{1,6})?)?"
    r"(?:Z|[+-](?:[01][0-9]|2[0-3])(?::?[0-5][0-9])?)"
)


def require(condition: bool, message: str) -> None:
    if not condition:
        raise ValueError(message)


def member(root: Path, name: str, label: str) -> Path:
    path = (root / name).resolve()
    require(path.is_relative_to(root), f"Path outside {label}")
    return path


def read(path: Path):
    return json.loads(path.read_text(encoding="utf-8"))


def canonical(value) -> str:
    return json.dumps(
        value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False
    )


def digest(value) -> str:
    return hashlib.sha256(canonical(value).encode("utf-8")).hexdigest()


def citation_input(snapshot: dict, visible: list[dict], profile: dict):
    # Reconstruct v1 without importing the active application's implementation.
    scope = digest(snapshot)
    catalogue = {}
    spans = {}
    numbered = []
    for source in visible:
        if not source["included"]:
            numbered.append(source)
            continue
        entry = dict(source)
        source_text = entry.pop("text")
        source_id = entry["source_id"]
        rows = []
        start = 0
        for line in source_text.split("\n"):
            end = start + len(line)
            cid = None
            if (
                line.strip()
                and source_text.find(line) == start
                and source_text.find(line, start + 1) < 0
            ):
                cid = "q" + digest([scope, source_id, start, end])[:16]
                require(cid not in spans, "Duplicate catalogue citation ID")
                spans[cid] = (source_id, line)
            rows.append({"citation_id": cid, "text": line})
            start = end + 1
        catalogue[source_id] = rows
        numbered.append({**entry, "citation_lines": rows})
    require(
        profile["provider_citation_catalogue_sha256"] == digest(catalogue)
        and profile["provider_citation_count"] == len(spans),
        "Citation catalogue mismatch",
    )
    return canonical({**snapshot, "catalog": numbered}), spans


def choices_card(
    raw: str,
    snapshot: dict,
    citations: dict | None = None,
    timeline_order: str | None = None,
    reviewed_citation_rules: dict | None = None,
) -> dict:
    def unique(pairs):
        value = dict(pairs)
        require(len(value) == len(pairs), "Duplicate key in choices output")
        return value

    def selected_ids(item, catalogue):
        ids = item["evidence"]
        require(
            isinstance(ids, list) and all(isinstance(cid, str) for cid in ids),
            "Invalid citation selection",
        )
        require(len(ids) == len(set(ids)), "Duplicate selected citation ID")
        require(all(cid in catalogue for cid in ids), "Unknown selected citation ID")
        return ids

    body = json.loads(raw, object_pairs_hook=unique)
    facts = body["facts"]
    keys = snapshot["requested_fact_keys"]
    require(isinstance(facts, dict) and set(facts) == set(keys), "Choices fact keys mismatch")
    require(
        all(
            isinstance(fact, dict) and set(fact) == {"value", "evidence"} for fact in facts.values()
        ),
        "Invalid choices fact shape",
    )
    if reviewed_citation_rules:
        # The v1 decoder is standard-library only and does not require an installed application.
        sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
        try:
            from proofline.providers.reviewed_citations import (
                apply_reviewed_citation_rules,
                expand_compact_response,
            )
        finally:
            sys.path.pop(0)
        require(citations is not None, "Reviewed citation rules require resolved citations")
        if "compact_case_id" in reviewed_citation_rules:
            require(
                reviewed_citation_rules["compact_case_id"] == snapshot["case_id"],
                "Compact case binding mismatch",
            )
            body = expand_compact_response(body, snapshot["case_id"])
        for item in [*facts.values(), *body["timeline"]]:
            selected_ids(item, citations)
        apply_reviewed_citation_rules(body, reviewed_citation_rules)
    body["facts"] = [{"key": key, **facts[key]} for key in keys]
    if citations is not None:
        for item in [*body["facts"], *body["timeline"]]:
            evidence = {}
            for cid in selected_ids(item, citations):
                source_id, fragment = citations[cid]
                evidence.setdefault(source_id, []).append(fragment)
            item["evidence"] = [
                {"source_id": source_id, "fragments": fragments}
                for source_id, fragments in evidence.items()
            ]
    if timeline_order == TIMELINE_ORDER:

        def instant(event):
            value = event["time"]["value"]
            require(UTC_V1_TIMESTAMP.fullmatch(value) is not None, "Invalid timestamp")
            return datetime.fromisoformat(value).astimezone(UTC)

        with suppress(ValueError, OverflowError):
            body["timeline"] = sorted(body["timeline"], key=instant)
    return body


def verify_archive(archive: Path, repository: Path) -> dict:
    archive, repository = archive.resolve(), repository.resolve()
    manifest = read(member(archive, "manifest.json", "archive"))
    require(manifest["format_version"] == 1, "Unsupported archive version")
    artifacts = manifest["artifact_hashes"]
    require(bool(artifacts), "Archive has no artifact hashes")
    for name, expected in artifacts.items():
        path = member(archive, name, "archive")
        require(
            hashlib.sha256(path.read_bytes()).hexdigest() == expected,
            f"Artifact hash mismatch: {name}",
        )

    # Source hashes describe provenance, not an obligation to run old code forever.
    changed, missing = [], []
    for name, expected in manifest["code_and_input_hashes"].items():
        path = member(repository, name, "repository")
        if not path.is_file():
            missing.append(name)
        elif hashlib.sha256(path.read_bytes()).hexdigest() != expected:
            changed.append(name)

    ledger = read(member(archive, "ledger.json", "archive"))
    attempts = ledger["attempts"]
    require(
        0 <= len(attempts) <= ledger["max_requests"] == manifest["approved_max_requests"],
        "Request budget mismatch",
    )
    require(
        ledger["approved_budget_usd"] == manifest["approved_budget_usd"], "Money budget mismatch"
    )
    response_ids, keys, conditions, policies = set(), set(), set(), set()
    totals = {"input_tokens": 0, "output_tokens": 0, "total_tokens": 0}
    unknown_usage, outcomes = [], []
    unknown_fields = {}
    for attempt in attempts:
        condition = attempt["condition"]
        require(condition not in conditions, "Duplicate condition")
        require(attempt["idempotency_key"] not in keys, "Duplicate idempotency key")
        conditions.add(condition)
        keys.add(attempt["idempotency_key"])
        run = read(member(archive, condition + "/run.json", "archive"))
        profile = run["generation_profile"]
        require(
            run["case_id"] == attempt["case_id"] and run["mode"] == "live", "Run identity mismatch"
        )
        require(
            profile["provider"] == manifest["provider"] and profile["max_retries"] == 0,
            "Provider/retry mismatch",
        )
        require(profile["requested_model"] == manifest["requested_model"], "Model mismatch")
        protocol = profile.get("api", "responses")
        provider_schema = profile.get("provider_schema_version")
        timeline_order = profile.get("canonical_timeline_order")
        citations = None
        require(
            provider_schema is None
            or (provider_schema in CHOICES_SCHEMAS and protocol == "chat_completions"),
            "Unsupported provider schema",
        )
        require(
            timeline_order is None
            or (timeline_order == TIMELINE_ORDER and provider_schema in CITATION_SCHEMAS),
            "Unsupported canonical timeline order",
        )
        if protocol == "responses":
            template = read(member(archive, "request-template.json", "archive"))
            request = {**template, "input": canonical(run["snapshot"])}
            schema = template["text"]["format"]["schema"]
            instructions = template["instructions"]
            usage_names = {key: key for key in totals}
        elif protocol == "chat_completions":
            if provider_schema in CHOICES_SCHEMAS:
                request_path = condition + "/request.json"
                require(request_path in artifacts, "Choices request is not sealed")
                request = read(member(archive, request_path, "archive"))
                snapshot = run["snapshot"]
                allowed = profile["provider_lookup_source_ids"]
                visible = [
                    s for s in snapshot["catalog"] if s["included"] or s["source_id"] in allowed
                ]
                require(
                    isinstance(allowed, list)
                    and allowed == [s["source_id"] for s in visible if not s["included"]],
                    "Choices lookup eligibility mismatch",
                )
                if provider_schema in CITATION_SCHEMAS:
                    sent_input, citations = citation_input(snapshot, visible, profile)
                else:
                    sent_input = canonical({**snapshot, "catalog": visible})
                expected_user_content = sent_input
            else:
                request = read(member(archive, "request-template.json", "archive"))
                sent_input = canonical(run["snapshot"])
                expected_user_content = ""
            messages = request.get("messages")
            require(
                isinstance(messages, list)
                and len(messages) == 2
                and all(isinstance(message, dict) for message in messages)
                and messages[0].get("role") == "system"
                and isinstance(messages[0].get("content"), str)
                and messages[1] == {"role": "user", "content": expected_user_content},
                "Chat request template or snapshot mismatch",
            )
            instructions = messages[0]["content"]
            messages[1]["content"] = sent_input
            schema = request["response_format"]["json_schema"]["schema"]
            if provider_schema == COMPACT_CITATION_SCHEMA:
                bindings = profile.get("reviewed_citation_rules_v1")
                require(
                    isinstance(bindings, dict)
                    and bindings.get("compact_case_id") == run["case_id"]
                    and set(schema["required"])
                    == set(schema["properties"])
                    == {"facts", "readiness", "follow_up"},
                    "Invalid compact response contract",
                )
            usage_names = {
                "input_tokens": "prompt_tokens",
                "output_tokens": "completion_tokens",
                "total_tokens": "total_tokens",
            }
        else:
            raise ValueError(f"Unsupported API protocol: {protocol}")
        require(digest(request) == profile["request_sha256"], f"Request hash mismatch: {condition}")
        require(
            digest(schema) == profile["schema_sha256"],
            "Schema hash mismatch",
        )
        require(
            hashlib.sha256(instructions.encode("utf-8")).hexdigest() == profile["prompt_sha256"],
            "Instruction hash mismatch",
        )
        policies.add(profile["policy_version"])
        response_id = profile.get("provider_response_id")
        if response_id is not None:
            require(response_id not in response_ids, "Duplicate provider response")
            response_ids.add(response_id)
        usage = run["usage"]
        require(usage is None or isinstance(usage, dict), "Invalid usage envelope")
        missing_usage = []
        for key, native_key in usage_names.items():
            value = usage.get(native_key) if usage is not None else None
            if type(value) is int and value >= 0:
                totals[key] += value
            else:
                missing_usage.append(key)
        if missing_usage:
            unknown_usage.append(condition)
            unknown_fields[condition] = missing_usage
        if run["status"] == "succeeded":
            proposal = read(member(archive, condition + "/proposal.json", "archive"))
            raw = run["raw_output"]
            body = (
                choices_card(
                    raw,
                    run["snapshot"],
                    citations,
                    timeline_order,
                    profile.get("reviewed_citation_rules_v1"),
                )
                if provider_schema in CHOICES_SCHEMAS
                else json.loads(raw)
            )
            require(body == proposal["body"], "Raw/canonical mismatch")
            require(run["snapshot"] == proposal["snapshot"], "Snapshot mismatch")
            require(
                digest(proposal["body"]) == proposal["body_sha256"], "Proposal body hash mismatch"
            )
            require(proposal["decision"] is None, "Unexpected operator decision")
        outcomes.append(
            {
                "condition": condition,
                "status": run["status"],
                "error_code": (run.get("error") or {}).get("code"),
            }
        )
    return {
        "archive_integrity": "pass",
        "artifact_hashes_verified": len(artifacts),
        "reserved_attempts": len(attempts),
        "unique_provider_responses": len(response_ids),
        "historical_policy_versions": sorted(policies),
        "stopped": ledger["stopped"],
        "received_usage_totals": totals,
        "usage_unknown_for": unknown_usage,
        "usage_unknown_fields": unknown_fields,
        "usage_totals_scope": (
            "Sum of known reported counters only; unknown fields are not zero usage."
        ),
        "outcomes": outcomes,
        "source_drift": {
            "changed": changed,
            "missing": missing,
            "interpretation": (
                "Provenance comparison only; recorded evidence is verified independently."
            ),
        },
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("archive", type=Path)
    parser.add_argument("--repository-root", type=Path, default=Path(__file__).resolve().parents[1])
    args = parser.parse_args()
    try:
        result = verify_archive(args.archive, args.repository_root)
    except (ValueError, KeyError, TypeError, OSError) as exc:
        print(f"Archive verification failed: {exc}", file=sys.stderr)
        return 1
    print(json.dumps(result, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
