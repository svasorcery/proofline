"""Historical proof stays verifiable when the active application evolves."""

import hashlib
import json
import shutil
import subprocess
import sys
from pathlib import Path

import pytest
from pydantic import SecretStr

from proofline.config import Settings
from proofline.domain.inputs import AnalysisInput, json_digest
from proofline.providers.factory import create_live_provider

ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "scripts/verify_live_archive.py"


def run(archive, repository):
    return subprocess.run(
        [sys.executable, "-B", str(SCRIPT), str(archive), "--repository-root", str(repository)],
        text=True,
        capture_output=True,
        check=False,
    )


def copy_archive(tmp_path):
    archive = tmp_path / "archive"
    shutil.copytree(ROOT / "evals/live/v1-groq-2026-11-16", archive)
    repository = tmp_path / "current"
    (repository / "src/proofline/providers").mkdir(parents=True)
    (repository / "src/proofline/providers/live_profile.py").write_text(
        "new policy", encoding="utf-8"
    )
    return archive, repository


def test_old_archive_verifies_with_source_drift_reported(tmp_path):
    archive, repository = copy_archive(tmp_path)
    result = run(archive, repository)
    assert result.returncode == 0, result.stderr
    report = json.loads(result.stdout)
    assert report["archive_integrity"] == "pass" and report["reserved_attempts"] == 5
    assert report["unique_provider_responses"] == 4 and report["stopped"] is True
    assert "src/proofline/providers/live_profile.py" in report["source_drift"]["changed"]


def test_stage4_v55_archive_reconstructs_all_six_accepted_cards():
    archive = ROOT / "evals/live/v55-groq-gpt-oss-20b-stage4-acceptance-2026-11-25"
    result = run(archive, ROOT)
    assert result.returncode == 0, result.stderr
    report = json.loads(result.stdout)
    assert report["archive_integrity"] == "pass"
    assert report["reserved_attempts"] == report["unique_provider_responses"] == 6
    assert report["stopped"] is True


def test_corruption_of_recorded_answer_fails_even_when_sources_differ(tmp_path):
    archive, repository = copy_archive(tmp_path)
    (archive / "full-V02/run.json").write_text("{}", encoding="utf-8")
    result = run(archive, repository)
    assert result.returncode != 0 and "Artifact hash mismatch" in result.stderr


def test_archive_manifest_cannot_read_outside_its_directory(tmp_path):
    archive, repository = copy_archive(tmp_path)
    manifest = json.loads((archive / "manifest.json").read_text())
    manifest["artifact_hashes"]["../outside.txt"] = "unused"
    (archive / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
    result = run(archive, repository)
    assert result.returncode != 0 and "Path outside archive" in result.stderr


def synthetic_chat_archive(tmp_path, usage):
    """Ephemeral verifier input, never a live receipt or new model response."""
    archive = tmp_path / "synthetic-chat"
    shutil.copytree(ROOT / "evals/live/v2-groq-v32-2026-11-16", archive)
    template = json.loads((archive / "request-template.json").read_text())
    template = {
        "model": template["model"],
        "messages": [
            {"role": "system", "content": template["instructions"]},
            {"role": "user", "content": ""},
        ],
        "response_format": {"type": "json_schema", "json_schema": template["text"]["format"]},
        "max_completion_tokens": 4096,
        "n": 1,
        "stream": False,
        "tools": [],
        "tool_choice": "none",
    }
    template["response_format"]["json_schema"].pop("type")
    run_path = archive / "full-V02/run.json"
    run_data = json.loads(run_path.read_text())
    request = json.loads(json.dumps(template))
    request["messages"][1]["content"] = json.dumps(
        run_data["snapshot"], ensure_ascii=False, sort_keys=True, separators=(",", ":")
    )
    profile = run_data["generation_profile"]
    profile["api"] = "chat_completions"
    profile["request_profile"] = "groq-chat_completions-v1"
    profile["endpoint"] = "https://api.groq.com/openai/v1/chat/completions"
    profile["request_sha256"] = hashlib.sha256(
        json.dumps(request, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()
    run_data["usage"] = usage
    run_path.write_text(json.dumps(run_data), encoding="utf-8")
    (archive / "request-template.json").write_text(json.dumps(template), encoding="utf-8")
    manifest_path = archive / "manifest.json"
    manifest = json.loads(manifest_path.read_text())
    for name in manifest["artifact_hashes"]:
        manifest["artifact_hashes"][name] = hashlib.sha256(
            (archive / name).read_bytes()
        ).hexdigest()
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
    return archive


def test_chat_archive_reconstructs_request_and_maps_native_usage_without_double_counting(tmp_path):
    archive = synthetic_chat_archive(
        tmp_path,
        {
            "prompt_tokens": 12,
            "completion_tokens": 7,
            "total_tokens": 19,
            "completion_tokens_details": {"reasoning_tokens": 3},
        },
    )
    result = run(archive, ROOT)
    assert result.returncode == 0, result.stderr
    report = json.loads(result.stdout)
    assert report["received_usage_totals"] == {
        "input_tokens": 12,
        "output_tokens": 7,
        "total_tokens": 19,
    }
    assert report["usage_unknown_for"] == []


def test_chat_archive_partial_usage_stays_explicitly_unknown(tmp_path):
    archive = synthetic_chat_archive(tmp_path, {"prompt_tokens": 12})
    result = run(archive, ROOT)
    assert result.returncode == 0, result.stderr
    report = json.loads(result.stdout)
    assert report["usage_unknown_for"] == ["full-V02"]
    assert report["usage_unknown_fields"] == {"full-V02": ["output_tokens", "total_tokens"]}


def test_unknown_archive_protocol_fails_instead_of_guessing_responses(tmp_path):
    archive = synthetic_chat_archive(tmp_path, None)
    run_path = archive / "full-V02/run.json"
    run_data = json.loads(run_path.read_text())
    run_data["generation_profile"]["api"] = "unknown"
    run_path.write_text(json.dumps(run_data), encoding="utf-8")
    manifest_path = archive / "manifest.json"
    manifest = json.loads(manifest_path.read_text())
    manifest["artifact_hashes"]["full-V02/run.json"] = hashlib.sha256(
        run_path.read_bytes()
    ).hexdigest()
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
    result = run(archive, ROOT)
    assert result.returncode != 0 and "Unsupported API protocol" in result.stderr


@pytest.mark.parametrize("extra_fact", [False, True])
def test_choices_archive_preserves_raw_map_and_validates_converted_card(tmp_path, extra_fact):
    source = ROOT / "evals/live/v5-groq-qwen-medium-2026-11-17"
    archive = tmp_path / "choices-archive"
    folder = archive / "full-V04"
    folder.mkdir(parents=True)
    manifest = json.loads((source / "manifest.json").read_text(encoding="utf-8"))
    ledger = json.loads((source / "ledger.json").read_text(encoding="utf-8"))
    ledger["attempts"] = [a for a in ledger["attempts"] if a["condition"] == "full-V04"]
    run_data = json.loads((source / "full-V04/run.json").read_text(encoding="utf-8"))
    proposal = json.loads((source / "full-V04/proposal.json").read_text(encoding="utf-8"))
    settings = Settings(
        _env_file=None,
        database_url="postgresql+psycopg://unused/unused",
        live_enabled=True,
        llm_provider="groq",
        llm_model="qwen/qwen3.8-27b",
        groq_reasoning_effort="medium",
        groq_prompt_profile="v3.3-choices-test",
        llm_api_key=SecretStr("prepare-only"),
    )
    prepared = create_live_provider(settings).prepare(
        AnalysisInput.model_validate_json(json.dumps(run_data["snapshot"]))
    )
    run_data["generation_profile"].update(prepared.generation_profile)
    raw = {
        **proposal["body"],
        "facts": {
            fact["key"]: {"value": fact["value"], "evidence": fact["evidence"]}
            for fact in proposal["body"]["facts"]
        },
    }
    if extra_fact:
        raw["facts"]["payment_outcome"] = {"value": "unconfirmed", "evidence": []}
    run_data["raw_output"] = json.dumps(raw, ensure_ascii=False)
    manifest["artifact_hashes"] = {}
    for name, value in {
        "ledger.json": ledger,
        "full-V04/request.json": prepared.request,
        "full-V04/run.json": run_data,
        "full-V04/proposal.json": proposal,
    }.items():
        path = archive / name
        path.write_text(json.dumps(value), encoding="utf-8")
        manifest["artifact_hashes"][name] = hashlib.sha256(path.read_bytes()).hexdigest()
    assert proposal["body_sha256"] == json_digest(proposal["body"])
    (archive / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
    result = run(archive, ROOT)
    if extra_fact:
        assert result.returncode != 0 and "Choices fact keys mismatch" in result.stderr
    else:
        assert result.returncode == 0, result.stderr


@pytest.mark.parametrize("unknown_id", [False, True])
def test_citation_archive_checks_selection_against_numbered_snapshot(tmp_path, unknown_id):
    source = ROOT / "evals/live/v11-groq-qwen-same-document-v04-2026-11-18"
    folder = tmp_path / "full-V04"
    folder.mkdir()
    run_data = json.loads((source / "full-V04/run.json").read_text(encoding="utf-8"))
    proposal = json.loads((source / "full-V04/proposal.json").read_text(encoding="utf-8"))
    ledger = json.loads((source / "ledger.json").read_text(encoding="utf-8"))
    manifest = json.loads((source / "manifest.json").read_text(encoding="utf-8"))
    settings = Settings(
        _env_file=None,
        database_url="postgresql+psycopg://unused/unused",
        live_enabled=True,
        llm_provider="groq",
        llm_model="qwen/qwen3.8-27b",
        groq_reasoning_effort="medium",
        groq_prompt_profile="v3.3-citation-ids-test",
        llm_api_key="prepare-only",
    )
    snapshot = AnalysisInput.model_validate_json(json.dumps(run_data["snapshot"]))
    prepared = create_live_provider(settings).prepare(snapshot)
    numbered = json.loads(prepared.request["messages"][1]["content"])["catalog"][1]
    reply_line = numbered["citation_lines"][3]
    raw = json.loads(run_data["raw_output"])
    raw["facts"]["alternative_acceptance"]["evidence"] = [reply_line["citation_id"]]
    for event in raw["timeline"]:
        event["evidence"] = []
    raw["timeline"].reverse()
    assert prepared.decode_output is not None
    body = prepared.decode_output(json.dumps(raw)).model_dump(mode="json")
    assert [event["time"] for event in body["timeline"]] == [
        event["time"] for event in reversed(raw["timeline"])
    ]
    assert body["facts"][0]["evidence"] == [
        {"source_id": numbered["source_id"], "fragments": [reply_line["text"]]}
    ]
    proposal.update(body=body, body_sha256=json_digest(body))
    if unknown_id:
        raw["facts"]["alternative_acceptance"]["evidence"] = ["q-unknown"]
    run_data["raw_output"] = json.dumps(raw)
    run_data["generation_profile"].update(prepared.generation_profile)

    manifest["artifact_hashes"] = {}
    for name, value in {
        "ledger.json": ledger,
        "full-V04/request.json": prepared.request,
        "full-V04/run.json": run_data,
        "full-V04/proposal.json": proposal,
    }.items():
        path = tmp_path / name
        path.write_text(json.dumps(value), encoding="utf-8")
        manifest["artifact_hashes"][name] = hashlib.sha256(path.read_bytes()).hexdigest()
    (tmp_path / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
    result = run(tmp_path, ROOT)
    if unknown_id:
        assert result.returncode != 0 and "Unknown selected citation ID" in result.stderr
    else:
        assert result.returncode == 0, result.stderr
