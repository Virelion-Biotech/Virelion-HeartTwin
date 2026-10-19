from __future__ import annotations

from dataclasses import dataclass

import pytest

from hearttwin import AnatomyBundlePayload
from hearttwin.patient_ep import PatientEPWorkflowError
from hearttwin.patient_ep_strict import _require_patient_ep_ready


@dataclass
class _Spec:
    name: str = "cardianatomy"


class _Adapter:
    def __init__(self, response: dict):
        self.spec = _Spec()
        self.response = response
        self.calls: list[tuple[str, dict]] = []

    def available(self) -> bool:
        return True

    def invoke(self, capability: str, payload: dict) -> dict:
        self.calls.append((capability, payload))
        return dict(self.response)


class _Registry:
    def __init__(self, adapter: _Adapter):
        self.adapter = adapter

    def capability(self, capability: str):
        if capability == "anatomy.validate":
            return self.adapter
        return None


def _bundle() -> AnatomyBundlePayload:
    return AnatomyBundlePayload(
        subject_id="P1",
        study_id="S1",
        acquisition_id="A1",
        artifacts=[],
        bundle_fingerprint="a" * 64,
    )


def test_strict_entrypoint_requests_patient_ep_readiness() -> None:
    adapter = _Adapter({"bundle_fingerprint": "a" * 64})
    bundle = _require_patient_ep_ready(_Registry(adapter), _bundle())  # type: ignore[arg-type]
    assert bundle.bundle_fingerprint == "a" * 64
    assert adapter.calls[0][0] == "anatomy.validate"
    assert adapter.calls[0][1]["target"] == "patient_ep"


def test_strict_entrypoint_rejects_changed_anatomy_fingerprint() -> None:
    adapter = _Adapter({"bundle_fingerprint": "b" * 64})
    with pytest.raises(PatientEPWorkflowError, match="changed the anatomy bundle fingerprint"):
        _require_patient_ep_ready(_Registry(adapter), _bundle())  # type: ignore[arg-type]
