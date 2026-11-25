"""Closed, content-free diagnostics for compatible Responses/Chat API errors."""

import json

from pydantic import JsonValue

from proofline.providers.live import http_error_code

_CODES = frozenset({"json_validate_failed"})
_TYPES = frozenset(
    {
        "invalid_request_error",
        "authentication_error",
        "permission_error",
        "rate_limit_error",
        "server_error",
        "not_found_error",
        "unprocessable_entity_error",
    }
)
_PARAMS = frozenset(
    {
        "model",
        "input",
        "instructions",
        "text",
        "text.format",
        "text.format.type",
        "text.format.name",
        "text.format.strict",
        "text.format.schema",
        "max_output_tokens",
        "tools",
        "tool_choice",
        "background",
        "store",
        "truncation",
        "messages",
        "response_format",
        "response_format.type",
        "response_format.json_schema",
        "response_format.json_schema.name",
        "response_format.json_schema.strict",
        "response_format.json_schema.schema",
        "max_completion_tokens",
        "reasoning_effort",
        "n",
        "stream",
    }
)


def _known(value: object, allowed: frozenset[str]) -> str | None:
    # SDK response/error construction is permissive; annotations are not validation.
    return value if isinstance(value, str) and value in allowed else None


def classify_response_error(
    provider: str, status: int, *, code: object, error_type: object, param: object
) -> tuple[str, dict[str, JsonValue]]:
    known_code = _known(code, _CODES)
    output_rejected = provider == "groq" and status == 400 and known_code == "json_validate_failed"
    return (
        "INVALID_MODEL_OUTPUT" if output_rejected else http_error_code(status),
        {
            "provider_error_code": known_code,
            "provider_error_type": _known(error_type, _TYPES),
            "provider_error_param": _known(param, _PARAMS),
            "provider_error_stage": "output_validation" if output_rejected else None,
        },
    )


def rejected_output_fields(
    error_body: object, response_format: object
) -> dict[str, JsonValue] | None:
    """Compare root keys without retaining the rejected values or undeclared key names."""
    if not isinstance(error_body, dict) or not isinstance(response_format, dict):
        return None
    format_schema = response_format.get("json_schema")
    schema = format_schema.get("schema") if isinstance(format_schema, dict) else None
    if not isinstance(schema, dict):
        return None
    required, properties = schema.get("required"), schema.get("properties")
    if (
        not isinstance(required, list)
        or not isinstance(properties, dict)
        or not all(isinstance(name, str) and name in properties for name in required)
    ):
        return None
    raw = error_body.get("failed_generation")
    if not isinstance(raw, str) or len(raw) > 65_536:
        return None
    try:
        candidate = json.loads(raw)
    except (ValueError, RecursionError):
        return None
    if not isinstance(candidate, dict):
        return None
    return {
        "missing_required_fields": sorted(set(required) - candidate.keys()),
        "unexpected_field_count": len(candidate.keys() - properties.keys()),
    }
