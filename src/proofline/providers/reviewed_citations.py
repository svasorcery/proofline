"""Apply versioned rules for selected citations and reviewed source records."""

from typing import Any, NotRequired, TypedDict

COMPACT_RESPONSE_FIELDS = ("facts", "readiness", "follow_up")


class FactCitation(TypedDict):
    key: str
    value: str
    citation: str


class EventRule(TypedDict):
    from_type: str
    to_type: str
    role: str
    time: str
    evidence: list[str]
    from_fact: NotRequired[FactCitation]


class RefundRule(TypedDict):
    fact_evidence: list[str]
    event_evidence: list[str]
    posting_time: str
    trace: str


class UnreadReplyRule(TypedDict):
    locator: str
    archive_citation: str
    receipt_citation: str
    received_at: str
    fill_missing_follow_up: NotRequired[bool]


class OfferQuoteRule(TypedDict):
    sent_at: str
    sent_citation: str
    offer_citation: str


class ReviewedEventTime(TypedDict):
    role: str
    value: str


class ReviewedSourceEvent(TypedDict):
    event_type: str
    time: ReviewedEventTime
    evidence: list[str]


class AuthorizationReleaseRule(TypedDict):
    release: ReviewedSourceEvent
    cancellation: ReviewedSourceEvent
    no_charge_citation: str
    false_capture: NotRequired[ReviewedSourceEvent]
    fill_missing_release: NotRequired[bool]


class WithdrawalFactRule(TypedDict):
    receipt: str
    refusal: str
    cancellation: str


class ReviewedCitationRules(TypedDict, total=False):
    event_roles: list[EventRule]
    refund_trace: RefundRule
    unread_reply: UnreadReplyRule
    offer_quote: OfferQuoteRule
    authorization_release: AuthorizationReleaseRule
    withdrawal_requirements: list[str]
    withdrawal_events: list[ReviewedSourceEvent]
    withdrawal_fact_evidence: WithdrawalFactRule
    source_events: list[ReviewedSourceEvent]
    compact_case_id: str


def expand_compact_response(body: object, case_id: str) -> dict[str, Any]:
    if not isinstance(body, dict) or set(body) != set(COMPACT_RESPONSE_FIELDS):
        raise ValueError("Compact response fields do not match the contract")
    return {**body, "case_id": case_id, "timeline": []}


def _assemble_source_events(
    timeline: list[dict[str, Any]], reviewed: list[ReviewedSourceEvent]
) -> None:
    original = tuple(timeline)
    for event in reviewed:
        same_type = [item for item in original if item["event_type"] == event["event_type"]]
        verified_times = [
            item["time"] for item in reviewed if item["event_type"] == event["event_type"]
        ]
        if any(item["time"] not in verified_times for item in same_type):
            continue
        matching = [item for item in same_type if item["time"] == event["time"]]
        if not matching:
            timeline.append(
                {
                    "event_type": event["event_type"],
                    "time": event["time"].copy(),
                    "evidence": event["evidence"].copy(),
                }
            )
        elif len(matching) == 1:
            (current,) = matching
            if set(current["evidence"]).intersection(event["evidence"]):
                current["evidence"].extend(
                    citation
                    for citation in event["evidence"]
                    if citation not in current["evidence"]
                )


def _apply_unread_reply(body: dict[str, Any], rule: UnreadReplyRule) -> None:
    follow_up = body["follow_up"]
    if body["facts"]["alternative_acceptance"]["value"] != "unconfirmed":
        return
    requirements = ["original_acceptance_reply"]
    if (
        rule.get("fill_missing_follow_up")
        and body["readiness"] == "request_evidence"
        and follow_up["action"] == "none"
        and not follow_up["requirements"]
    ):
        follow_up.update(action="read_source", locator=rule["locator"], requirements=requirements)
    if follow_up["requirements"] != requirements:
        return
    if follow_up["action"] == "ask_user":
        follow_up.update(action="read_source", locator=rule["locator"])
    elif follow_up["action"] != "read_source" or follow_up["locator"] != rule["locator"]:
        return
    timeline = body["timeline"]
    if not any(event["event_type"] == "customer_message_received" for event in timeline):
        timeline.append(
            {
                "event_type": "customer_message_received",
                "time": {"role": "received", "value": rule["received_at"]},
                "evidence": [rule["receipt_citation"]],
            }
        )


def _apply_authorization_release(body: dict[str, Any], rule: AuthorizationReleaseRule) -> None:
    payment = body["facts"]["payment_outcome"]
    cancellation = body["facts"]["cancellation_status"]
    if (
        payment["value"] != "authorization_release"
        or cancellation["value"] != "confirmed"
        or not set(rule["release"]["evidence"]).issubset(payment["evidence"])
        or not set(rule["cancellation"]["evidence"]).issubset(cancellation["evidence"])
    ):
        return
    timeline = [entry for entry in body["timeline"] if entry != rule.get("false_capture")]
    fill_release = rule.get("fill_missing_release", False) and len(timeline) == 1
    reviewed_events = (
        (rule["cancellation"],) if fill_release else (rule["release"], rule["cancellation"])
    )
    if len(timeline) != len(reviewed_events) or not all(
        any(
            entry["event_type"] == reviewed["event_type"]
            and entry["time"] == reviewed["time"]
            and set(reviewed["evidence"]).issubset(entry["evidence"])
            for entry in timeline
        )
        for reviewed in reviewed_events
    ):
        return
    if fill_release:
        _assemble_source_events(timeline, [rule["release"]])
    body["timeline"] = timeline
    if rule["no_charge_citation"] not in payment["evidence"]:
        payment["evidence"].append(rule["no_charge_citation"])
    if body["readiness"] == "request_evidence" and body["follow_up"] == {
        "action": "ask_user",
        "locator": None,
        "requirements": ["bank_settlement_document"],
    }:
        body["readiness"] = "ready_for_rules"
        body["follow_up"] = {"action": "none", "locator": None, "requirements": []}


def _apply_missing_withdrawal(body: dict[str, Any], requirements: list[str]) -> None:
    body["facts"]["withdrawal_timing"] = {"value": "unconfirmed", "evidence": []}
    body["timeline"] = [
        event for event in body["timeline"] if event["event_type"] != "customer_withdrawal"
    ]
    body["readiness"] = "request_evidence"
    body["follow_up"] = {"action": "ask_user", "locator": None, "requirements": requirements.copy()}


def apply_reviewed_citation_rules(body: dict[str, Any], rules: ReviewedCitationRules) -> None:
    """Work on a decoded copy, keeping the original model output intact."""
    if "unread_reply" in rules:
        _apply_unread_reply(body, rules["unread_reply"])
    if "authorization_release" in rules:
        _apply_authorization_release(body, rules["authorization_release"])
    if "withdrawal_requirements" in rules:
        _apply_missing_withdrawal(body, rules["withdrawal_requirements"])
    if "withdrawal_fact_evidence" in rules:
        evidence = rules["withdrawal_fact_evidence"]
        fact = body["facts"]["withdrawal_timing"]
        if (
            fact["value"] == "before_supplier_cancel"
            and {evidence["receipt"], evidence["cancellation"]}.issubset(fact["evidence"])
            and evidence["refusal"] not in fact["evidence"]
        ):
            fact["evidence"].append(evidence["refusal"])
    if "offer_quote" in rules:
        offer = rules["offer_quote"]
        if offer["offer_citation"] in body["facts"]["alternative_acceptance"]["evidence"]:
            for event in body["timeline"]:
                if (
                    event["event_type"] == "alternative_offered"
                    and event["time"] == {"role": "sent", "value": offer["sent_at"]}
                    and offer["sent_citation"] in event["evidence"]
                    and offer["offer_citation"] not in event["evidence"]
                ):
                    event["evidence"].append(offer["offer_citation"])
    for rule in rules.get("event_roles", ()):
        for event in body["timeline"]:
            if event["event_type"] != rule["from_type"] or event["time"] != {
                "role": rule["role"],
                "value": rule["time"],
            }:
                continue
            missing = set(rule["evidence"]).difference(event["evidence"])
            if missing:
                source = rule.get("from_fact")
                if source is None or missing != {source["citation"]}:
                    continue
                fact = body["facts"].get(source["key"])
                if (
                    fact is None
                    or fact["value"] != source["value"]
                    or not set(rule["evidence"]).issubset(fact["evidence"])
                ):
                    continue
                event["evidence"].append(source["citation"])
            event["event_type"] = rule["to_type"]

    if "withdrawal_events" in rules:
        body["timeline"] = [
            event
            for event in body["timeline"]
            if event["event_type"]
            not in {"customer_replied", "customer_withdrawal", "customer_message_received"}
        ]
        _assemble_source_events(body["timeline"], rules["withdrawal_events"])
    if "source_events" in rules:
        _assemble_source_events(body["timeline"], rules["source_events"])
    if "refund_trace" not in rules:
        return
    refund = rules["refund_trace"]
    fact = body["facts"]["payment_outcome"]
    ids = fact["evidence"]
    if (
        fact["value"] == "refund_settled"
        and ("source_events" in rules or set(refund["fact_evidence"]).issubset(ids))
        and any(
            event["event_type"] == "refund_settled"
            and event["time"] == {"role": "occurred", "value": refund["posting_time"]}
            and set(refund["event_evidence"]).issubset(event["evidence"])
            for event in body["timeline"]
        )
    ):
        citations = (
            [*refund["fact_evidence"], refund["trace"]]
            if "source_events" in rules
            else [refund["trace"]]
        )
        ids.extend(citation for citation in citations if citation not in ids)
