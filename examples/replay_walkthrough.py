"""Exercise explicit operator commands on the synthetic V04 case through HTTP."""

import argparse
import copy
import json
from typing import Any
from urllib.error import HTTPError
from urllib.request import Request, urlopen
from uuid import uuid4


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base-url", default="http://127.0.0.1:8000")
    args = parser.parse_args()
    base = args.base_url.rstrip("/") + "/api/v1"

    def request(
        method: str, path: str, body: dict[str, Any] | None = None, *, key: str | None = None
    ) -> dict[str, Any]:
        headers = {"Content-Type": "application/json"}
        if key is not None:
            headers["Idempotency-Key"] = key
        data = json.dumps(body, ensure_ascii=False).encode("utf-8") if body is not None else None
        try:
            with urlopen(
                Request(base + path, data=data, headers=headers, method=method), timeout=30
            ) as response:
                result: dict[str, Any] = json.load(response)
                return result
        except HTTPError as error:
            detail = json.loads(error.read())
            raise RuntimeError(
                f"HTTP {error.code}: {detail['code']} — {detail['message']}"
            ) from None

    request("GET", "/ready")
    case = request("GET", "/cases/V04")
    run = request(
        "POST",
        "/cases/V04/runs",
        {
            "expected_case_version": case["version"],
            "mode": "replay",
            "replay_id": "v3-full-V04",
        },
        key=str(uuid4()),
    )
    if run["status"] != "succeeded" or run["generation_profile"]["new_model_call"]:
        raise RuntimeError("Expected a successful recorded replay without a model call")
    original = request("GET", f"/proposals/{run['proposal_revision_id']}")
    if original["status"] != "blocked_for_approval":
        raise RuntimeError("Expected the recorded V04 contradiction to remain visible")

    corrected_body = copy.deepcopy(original["body"])
    corrected_body["timeline"] = [
        event
        for event in corrected_body["timeline"]
        if event["event_type"] != "alternative_accepted"
    ]
    corrected_body["follow_up"]["action"] = "ask_user"
    corrected_body["follow_up"]["locator"] = None
    corrected = request(
        "POST",
        f"/proposals/{original['id']}/revisions",
        {
            "expected_case_version": original["case_version"],
            "body": corrected_body,
            "reason": (
                "Synthetic walkthrough: keep conditional acceptance "
                "and request missing confirmation."
            ),
        },
    )
    if corrected["status"] != "needs_operator_review":
        raise RuntimeError(
            "The corrected assembly should still require an explicit operator decision"
        )
    decision = request(
        "POST",
        f"/proposals/{corrected['id']}/decision",
        {
            "expected_case_version": corrected["case_version"],
            "kind": "approve",
            "reason": "Explicit demo operator command after checking the synthetic correction.",
        },
        key=str(uuid4()),
    )
    historical = request("GET", f"/proposals/{original['id']}")
    if historical["body"] != original["body"] or historical["active"]:
        raise RuntimeError("The original recorded response must remain unchanged in history")
    print(
        json.dumps(
            {
                "recorded_proposal": original["id"],
                "recorded_state": original["status"],
                "corrected_proposal": corrected["id"],
                "decision": decision["kind"],
                "follow_up_action": corrected_body["follow_up"]["action"],
                "current_case_version": decision["result_case_version"],
                "new_model_calls": 0,
            },
            indent=2,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
