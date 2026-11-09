"""Literal source fragments and deterministic presentation. No model calls."""
from datetime import datetime, timezone
from decimal import Decimal
import hashlib

EVENT_ROLES = {
    "alternative_offered": "sent", "customer_replied": "sent",
    "customer_message_received": "received", "alternative_accepted": "received",
    "customer_withdrawal": "received", "supplier_cancelled": "occurred",
    "payment_captured": "occurred", "refund_initiated": "occurred",
    "refund_settled": "occurred", "authorization_released": "occurred",
}
FACT_VALUES = {
    "alternative_acceptance": {"confirmed", "conditional", "not_accepted", "unconfirmed"},
    "payment_outcome": {"refund_settled", "refund_pending", "authorization_release", "unconfirmed"},
    "withdrawal_timing": {"before_supplier_cancel", "after_supplier_cancel", "unconfirmed"},
    "cancellation_status": {"confirmed", "unconfirmed"},
}
REQUIREMENT_TEXT = {
    "original_customer_message": "Нужен оригинал сообщения с недвусмысленным отказом клиента от бронирования.",
    "delivery_receipt": "Нужна квитанция или заголовок получения этого сообщения поставщиком либо сервисом с датой и временем.",
    "quiet_room": "Подтвердите у отеля, что именно предложенный номер будет тихим.",
    "courtyard_windows": "Подтвердите у отеля, что окна именно предложенного номера выходят во двор.",
    "original_acceptance_reply": "Нужен оригинал ответа клиента на конкретное предложение альтернативы, чтобы проверить содержание согласия.",
    "bank_settlement_document": "Нужен банковский документ о фактическом зачислении возврата, связанный с исходной оплатой или возвратной трассой.",
}
LOOKUP_KINDS = {"read_source": "document", "query_provider": "provider_log", "query_payment": "payment_ledger"}


def resolve_evidence(items, sources):
    by_id = {source["id"]: source for source in sources}
    spans, issues = [], []
    for item in items:
        source = by_id.get(item.get("source_id"))
        if source is None:
            issues.append("source_not_visible")
            continue
        fragments = item.get("fragments", [])
        if not fragments:
            issues.append("empty_fragments")
        for fragment in fragments:
            if not isinstance(fragment, str) or not fragment.strip():
                issues.append("empty_fragment")
                continue
            start = source["text"].find(fragment)
            if start < 0:
                issues.append("fragment_not_literal")
                continue
            if source["text"].find(fragment, start + 1) >= 0:
                issues.append("ambiguous_fragment")
                continue
            spans.append({"source_id": source["id"], "start": start, "end": start + len(fragment),
                          "fragment": fragment, "source_sha256": hashlib.sha256(source["text"].encode("utf-8")).hexdigest()})
    return {"valid": not issues, "issues": issues, "spans": spans}


def covers(items, sets, sources):
    resolved = resolve_evidence(items, sources)
    if not resolved["valid"]:
        return False
    if not sets:
        return True
    by_id = {source["id"]: source for source in sources}

    def anchor_covered(need):
        source = by_id.get(need["source_id"])
        if source is None:
            return False
        text, anchor = source["text"], need["anchor"]
        if not anchor:
            return False
        start = text.find(anchor)
        matching = [s for s in resolved["spans"] if s["source_id"] == need["source_id"]]
        while start >= 0:
            if all(character.isspace() or any(s["start"] <= start + offset < s["end"] for s in matching)
                   for offset, character in enumerate(anchor)):
                return True
            start = text.find(anchor, start + 1)
        return False

    return any(all(anchor_covered(need) for need in option) for option in sets)


def instant(value):
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        raise ValueError("timezone_required")
    return parsed.astimezone(timezone.utc)


def time_errors(event, sources):
    issues = []
    when = event["time"]
    if event["event_type"] not in EVENT_ROLES or when["role"] != EVENT_ROLES.get(event["event_type"]):
        issues.append("wrong_time_role")
    try:
        instant(when["value"])
    except (ValueError, TypeError, AttributeError):
        issues.append("invalid_or_naive_time")
    resolved = resolve_evidence(event["evidence"], sources)
    if not resolved["valid"]:
        issues.extend(resolved["issues"])
    if not any(when["value"] in span["fragment"] for span in resolved["spans"]):
        issues.append("time_not_in_fragments")
    return issues


def followup_errors(followup, case, sources):
    action, locator, keys = followup["action"], followup["locator"], followup["requirements"]
    issues = []
    if len(set(keys)) != len(keys) or any(key not in REQUIREMENT_TEXT for key in keys):
        issues.append("invalid_requirement_keys")
    if action == "none":
        return issues + (["unexpected_followup_payload"] if locator is not None or keys else [])
    if not keys:
        issues.append("empty_requirements")
    if action == "ask_user":
        return issues + (["ask_user_has_locator"] if locator is not None else [])
    if action not in LOOKUP_KINDS:
        return issues + ["unknown_action"]
    source = next((s for s in case.get("sources", []) + case.get("initial_sources", []) if s["locator"] == locator), None)
    if source is None:
        issues.append("unknown_locator")
    elif source["kind"] != LOOKUP_KINDS[action]:
        issues.append("wrong_lookup_kind")
    visible_text = "\n".join(s["text"] for s in sources)
    catalogued = action == "read_source" and any(c["locator"] == locator and c["kind"] == "document" for c in case.get("catalog", []))
    if not locator or not (locator in visible_text or catalogued):
        issues.append("locator_not_visible")
    return issues


def render_requirements(keys):
    return [{"key": key, "text": REQUIREMENT_TEXT[key]} for key in keys]


def money_display(money):
    if type(money["amount_minor"]) is not int or money["currency"] != "USD" or money["scale"] != 2:
        raise ValueError("Expected integer USD minor units with scale=2")
    return f"{Decimal(money['amount_minor']).scaleb(-money['scale']):.2f} USD"


def validate_runtime(pack, case, sources):
    issues, resolved = [], {}
    facts = pack["facts"]
    keys = [f["key"] for f in facts]
    if len(keys) != len(set(keys)) or set(keys) != set(case["questions"]):
        issues.append("fact_keys_mismatch")
    for fact in facts:
        if fact["value"] not in FACT_VALUES.get(fact["key"], set()):
            issues.append("invalid_fact_value")
        if fact["value"] != "unconfirmed" and not fact["evidence"]:
            issues.append("known_fact_without_evidence")
        resolved[f"fact:{fact['key']}"] = resolve_evidence(fact["evidence"], sources)
        issues.extend(resolved[f"fact:{fact['key']}"]["issues"])
    moments, event_keys = [], []
    for i, event in enumerate(pack["timeline"]):
        issues.extend(time_errors(event, sources))
        resolved[f"event:{i}"] = resolve_evidence(event["evidence"], sources)
        try:
            moments.append(instant(event["time"]["value"]))
            event_keys.append((event["event_type"], moments[-1], event["time"]["role"]))
        except ValueError:
            pass
    if moments != sorted(moments):
        issues.append("timeline_not_sorted")
    if len(event_keys) != len(set(event_keys)):
        issues.append("duplicate_events")
    issues.extend(followup_errors(pack["follow_up"], case, sources))
    if pack["readiness"] == "ready_for_rules" and (pack["follow_up"]["action"] != "none" or any(f["value"] in ("unconfirmed", "conditional") for f in facts)):
        issues.append("premature_readiness")
    if pack["readiness"] == "request_evidence" and pack["follow_up"]["action"] == "none":
        issues.append("missing_followup")
    return {"valid": not issues, "issues": issues, "resolved": resolved}
