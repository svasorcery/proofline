"""Resolve independent exact fragments against selected immutable source versions."""

from collections.abc import Mapping, Sequence

from proofline.domain.assembly import Evidence
from proofline.domain.sources import SourceSnapshot
from proofline.validation.result import (
    FieldPath,
    ResolvedFragment,
    ValidationIssue,
    ValidationResult,
)


def resolve_evidence(
    items: Sequence[Evidence], sources: Mapping[str, SourceSnapshot], path: FieldPath
) -> ValidationResult:
    issues: list[ValidationIssue] = []
    fragments: list[ResolvedFragment] = []
    for index, item in enumerate(items):
        source = sources.get(item.source_id)
        if source is None:
            issues.append(ValidationIssue("source_not_visible", (*path, index, "source_id")))
            continue
        for offset, fragment in enumerate(item.fragments):
            location = (*path, index, "fragments", offset)
            if not fragment.strip():
                issues.append(ValidationIssue("empty_fragment", location))
                continue
            start = source.text.find(fragment)
            if start < 0:
                issues.append(ValidationIssue("fragment_not_literal", location))
            elif source.text.find(fragment, start + 1) >= 0:
                issues.append(ValidationIssue("ambiguous_fragment", location))
            else:
                fragments.append(
                    ResolvedFragment(
                        source_id=source.source_id,
                        source_version_id=source.version_id,
                        source_sha256=source.text_sha256,
                        start_cp=start,
                        end_cp=start + len(fragment),
                        fragment=fragment,
                        path=location,
                    )
                )
    return ValidationResult(issues=tuple(issues), fragments=tuple(fragments))
