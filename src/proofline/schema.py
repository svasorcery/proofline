"""Export the generated schema or compare it with the frozen wire contract."""

import argparse
import json
from pathlib import Path
from typing import Any

from pydantic.json_schema import JsonSchemaValue

from proofline.domain.assembly import AssemblyOutput


def generated_schema() -> JsonSchemaValue:
    return AssemblyOutput.model_json_schema()


def _normalise(schema: JsonSchemaValue) -> JsonSchemaValue:
    """Remove representational differences in this non-recursive output schema."""
    definitions = schema.get("$defs", {})

    def walk(value: Any) -> Any:
        if isinstance(value, list):
            return [walk(item) for item in value]
        if not isinstance(value, dict):
            return value
        if "$ref" in value:
            reference = value["$ref"]
            if not reference.startswith("#/$defs/"):
                raise ValueError(f"Unsupported schema reference: {reference}")
            resolved = walk(definitions[reference.removeprefix("#/$defs/")])
            siblings = walk({key: item for key, item in value.items() if key != "$ref"})
            return {"allOf": [resolved, siblings]} if siblings else resolved
        result = {key: walk(item) for key, item in value.items() if key not in {"title", "$defs"}}
        alternatives = result.get("anyOf")
        if alternatives and all(set(item) == {"type"} for item in alternatives):
            result["type"] = sorted(item["type"] for item in alternatives)
            del result["anyOf"]
        for key in ("enum", "required", "type"):
            if isinstance(result.get(key), list):
                result[key] = sorted(result[key])
        return result

    result: JsonSchemaValue = walk(schema)
    return result


def contract_matches(expected: JsonSchemaValue) -> bool:
    return _normalise(generated_schema()) == _normalise(expected)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check", type=Path, help="Compare with an existing JSON Schema")
    args = parser.parse_args()
    if args.check is None:
        print(json.dumps(generated_schema(), ensure_ascii=False, indent=2))
        return 0
    expected = json.loads(args.check.read_text(encoding="utf-8-sig"))
    if not contract_matches(expected):
        parser.exit(1, "Generated schema differs from the frozen wire contract.\n")
    print("Generated schema matches the frozen wire contract.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
