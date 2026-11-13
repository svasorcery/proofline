"""One generation outcome, including safe diagnostics and known provider metadata."""

from dataclasses import dataclass

from pydantic import JsonValue


@dataclass(frozen=True, slots=True)
class GenerationResult:
    raw_output: str | None
    generation_profile: dict[str, JsonValue]
    usage: dict[str, JsonValue] | None
    error: dict[str, JsonValue] | None = None
