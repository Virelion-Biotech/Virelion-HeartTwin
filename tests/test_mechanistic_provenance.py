from types import SimpleNamespace

import pytest

from hearttwin.mechanistic_workflow import MechanisticWorkflowError, _call
from hearttwin.provenance import sha256


def registry(output, requests):
    adapter = SimpleNamespace(
        spec=SimpleNamespace(name="CardiMech", repository="Virelion-Biotech/Virelion-CardiMech"),
        available=lambda: True,
        invoke=lambda capability, payload: requests.append(payload) or output,
    )
    return SimpleNamespace(capability=lambda capability: adapter)


def test_workflow_provenance_is_sealed_without_modifying_solver_input_or_output():
    output = {"subject_id": "subject", "provenance": {"backend": "reference"}}
    payload = {"settings": {"cycles": 4}}
    requests = []
    context = {"posterior_replay_selection": {"selection": "accepted_posterior_sample", "objective": 1.0}}
    result = _call(registry(output, requests), "mechanics.simulate", "subject", payload,
                   parent_run_ids=["inference"], orchestration_context=context)
    assert requests == [{"settings": {"cycles": 4}}]
    assert output == {"subject_id": "subject", "provenance": {"backend": "reference"}}
    assert result.data["provenance"]["hearttwin_orchestration"] == context
    assert result.provenance.content_sha256 == sha256(result.data)
    context["posterior_replay_selection"]["objective"] = 100
    assert result.provenance.content_sha256 == sha256(result.data)


def test_service_cannot_impersonate_workflow_provenance():
    output = {"subject_id": "subject", "provenance": {"hearttwin_orchestration": {}}}
    with pytest.raises(MechanisticWorkflowError, match="reserved HeartTwin provenance"):
        _call(registry(output, []), "mechanics.simulate", "subject", {},
              parent_run_ids=[], orchestration_context={"posterior_replay_selection": {}})
