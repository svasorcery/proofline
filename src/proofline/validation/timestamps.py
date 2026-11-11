"""Parse explicit-offset timestamps without repairing or truncating source values."""

import re
from collections.abc import Mapping, Sequence
from datetime import UTC, datetime

from proofline.domain.sources import SourceSnapshot
from proofline.validation.result import ResolvedFragment

_TIMESTAMP = re.compile(
    r"[0-9]{4}-[0-9]{2}-[0-9]{2}T(?:[01][0-9]|2[0-3]):[0-5][0-9]"
    r"(?::[0-5][0-9](?:[.,][0-9]{1,6})?)?"
    r"(?:Z|[+-](?:[01][0-9]|2[0-3])(?::?[0-5][0-9])?)"
)
_CONTINUATION = re.compile(r"[.,:+-][0-9]")


def parse_instant(value: str) -> datetime:
    if _TIMESTAMP.fullmatch(value) is None:
        raise ValueError("Expected an extended ISO8601 timestamp with an explicit offset")
    return datetime.fromisoformat(value).astimezone(UTC)


def has_literal_timestamp(
    value: str, fragments: Sequence[ResolvedFragment], sources: Mapping[str, SourceSnapshot]
) -> bool:
    if not value:
        return False
    for span in fragments:
        source_text = sources[span.source_id].text
        offset = span.fragment.find(value)
        while offset >= 0:
            start = span.start_cp + offset
            end = start + len(value)
            suffix = source_text[end:]
            left_boundary = start == 0 or not source_text[start - 1].isalnum()
            right_boundary = not (suffix[:1].isalnum() or _CONTINUATION.match(suffix))
            if left_boundary and right_boundary:
                return True
            offset = span.fragment.find(value, offset + 1)
    return False
