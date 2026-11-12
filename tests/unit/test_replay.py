import json
import shutil
from pathlib import Path

import pytest

from proofline.application.errors import ApplicationError
from proofline.domain.inputs import AnalysisInput, compatibility_digest, source_bindings
from proofline.providers.replay import ReplayCatalog

FIXTURES = Path(__file__).resolve().parents[2] / "fixtures"


def test_each_recording_matches_exactly_its_reconstructed_input() -> None:
    catalog = ReplayCatalog(FIXTURES)
    assert len(catalog.recordings) == 9
    for recording in catalog.recordings:
        input_data = recording.input
        options = catalog.options(input_data)
        assert [option.replay_id for option in options] == [recording.option.replay_id]
        result = catalog.generate(catalog.prepare(recording.option.replay_id, input_data))
        assert json.loads(result.raw_output)["case_id"] == input_data.case_id
        assert result.generation_profile["mode"] == "replay"
        assert result.generation_profile["policy_version"] == "assembly-v3"
        assert result.usage is not None


def test_database_version_ids_do_not_change_replay_compatibility() -> None:
    catalog = ReplayCatalog(FIXTURES)
    original = catalog.recordings[0].input
    payload = original.model_dump(mode="json")
    for source in payload["catalog"]:
        if source["included"]:
            source["version_id"] = "new-database-version-id"
    rebuilt = AnalysisInput.model_validate_json(json.dumps(payload))
    assert compatibility_digest(rebuilt) == compatibility_digest(original)
    assert source_bindings(rebuilt) != source_bindings(original)


@pytest.mark.parametrize("mutation", ["text", "catalog", "order", "money", "questions"])
def test_replay_never_adapts_a_recording_to_changed_input(mutation: str) -> None:
    catalog = ReplayCatalog(FIXTURES)
    recording = next(r for r in catalog.recordings if r.option.replay_id == "v3-full-V01")
    payload = recording.input.model_dump(mode="json")
    if mutation == "text":
        payload["user_text"] += " Another request."
    elif mutation == "catalog":
        payload["catalog"][0]["title"] = "Changed metadata"
    elif mutation == "order":
        payload["catalog"].reverse()
        for index, source in enumerate(payload["catalog"]):
            source["position"] = index
    elif mutation == "money":
        payload["money"]["amount_minor"] += 1
    else:
        payload["requested_fact_keys"] = ["cancellation_status"]
    changed = AnalysisInput.model_validate_json(json.dumps(payload))
    assert catalog.options(changed) == []
    with pytest.raises(ApplicationError) as error:
        catalog.prepare(recording.option.replay_id, changed)
    assert error.value.code == "REPLAY_INPUT_MISMATCH"


def test_removed_source_text_is_not_present_in_runtime_snapshot() -> None:
    catalog = ReplayCatalog(FIXTURES)
    removed = next(r for r in catalog.recordings if r.option.replay_id == "v3-ablation-V01")
    excluded = [source for source in removed.input.catalog if not source.included]
    assert len(excluded) == 1
    assert excluded[0].text is None
    assert excluded[0].text_sha256 is None
    assert excluded[0].version_id is None
    assert excluded[0].source_id not in source_bindings(removed.input)


def test_generation_results_do_not_share_mutable_metadata() -> None:
    catalog = ReplayCatalog(FIXTURES)
    recording = catalog.recordings[0]
    prepared = catalog.prepare(recording.option.replay_id, recording.input)
    first = catalog.generate(prepared)
    first.generation_profile["mode"] = "changed"
    assert catalog.generate(prepared).generation_profile["mode"] == "replay"


@pytest.mark.parametrize("artifact", ["cases", "metadata", "catalog"])
def test_changed_recording_inputs_or_provenance_fail_closed(tmp_path: Path, artifact: str) -> None:
    fixtures = tmp_path / "fixtures"
    shutil.copytree(FIXTURES, fixtures)
    if artifact == "cases":
        path = fixtures / "cases/travel-cases.json"
        value = json.loads(path.read_text(encoding="utf-8"))
        value[0]["user_text"] += " Different original input."
    elif artifact == "metadata":
        path = fixtures / "replay/v3/full/V01/meta.json"
        value = json.loads(path.read_text(encoding="utf-8"))
        value["model"] = "a-different-model"
    else:
        path = fixtures / "replay/v3/catalog.json"
        value = json.loads(path.read_text(encoding="utf-8"))
        value["records"][0]["recorded_on"] = "2099-01-01"
    path.write_text(json.dumps(value, ensure_ascii=False), encoding="utf-8")
    with pytest.raises(ValueError, match="hash mismatch"):
        ReplayCatalog(fixtures)
