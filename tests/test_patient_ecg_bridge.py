from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass

import pytest

from hearttwin.patient_ecg_bridge import prepare_patient_ecg_evidence
from hearttwin.patient_ep import PatientEPWorkflowError
from hearttwin.patient_ep_strict import run_strict_patient_ep_workflow
from hearttwin.patient_model import (
    CoordinateFrame,
    PatientAcquisition,
    PatientCase,
    PatientEPWorkflowSpec,
    PatientEvidenceItem,
    SourceFileRef,
)

SHA_CT = "a" * 64
SHA_FIT = "b" * 64
SHA_HOLDOUT = "c" * 64
SHA_DERIVED_FIT = "d" * 64
SHA_DERIVED_HOLDOUT = "e" * 64
BUNDLE_SHA = "f" * 64
LEADS = [
    "I",
    "II",
    "III",
    "aVR",
    "aVL",
    "aVF",
    "V1",
    "V2",
    "V3",
    "V4",
    "V5",
    "V6",
]


def _case() -> PatientCase:
    return PatientCase(
        patient_id="P1",
        coordinate_frames=[
            CoordinateFrame(frame_id="image", convention="DICOM_LPS", length_unit="mm")
        ],
        acquisitions=[
            PatientAcquisition(
                acquisition_id="ct",
                subject_id="P1",
                modality="ct",
                source_files=[
                    SourceFileRef(
                        file_id="ct-file",
                        uri="file:///data/ct.dcm",
                        sha256=SHA_CT,
                    )
                ],
                coordinate_frame_id="image",
            ),
            PatientAcquisition(
                acquisition_id="fit",
                subject_id="P1",
                modality="ecg",
                source_files=[
                    SourceFileRef(
                        file_id="fit-file",
                        uri="file:///data/fit.csv",
                        sha256=SHA_FIT,
                    )
                ],
                sampling_rate_hz=500.0,
                lead_definitions=LEADS,
                units={"signal": "mV"},
            ),
            PatientAcquisition(
                acquisition_id="holdout",
                subject_id="P1",
                modality="ecg",
                source_files=[
                    SourceFileRef(
                        file_id="holdout-file",
                        uri="file:///data/holdout.csv",
                        sha256=SHA_HOLDOUT,
                    )
                ],
                sampling_rate_hz=500.0,
                lead_definitions=LEADS,
                units={"signal": "mV"},
            ),
        ],
        evidence=[
            PatientEvidenceItem(
                evidence_id="fit-evidence",
                subject_id="P1",
                role="calibration",
                acquisition_id="fit",
                data_kind="12_lead_qrs",
            ),
            PatientEvidenceItem(
                evidence_id="holdout-evidence",
                subject_id="P1",
                role="validation",
                acquisition_id="holdout",
                data_kind="12_lead_qrs",
                withheld_from_fit=True,
            ),
        ],
    )


def _electrotrace_handoff(
    *,
    evidence_id: str,
    source_sha: str,
    derived_sha: str,
    leads: list[str] | None = None,
) -> dict:
    return {
        "contract_version": "1.0",
        "schema_version": "electrotrace-ep-calibration-v1",
        "entity_id": "P1",
        "observations": [
            {
                "observation_id": evidence_id,
                "kind": "ecg",
                "artifact": {
                    "artifact_id": f"electrotrace-{evidence_id}",
                    "kind": "electrotrace_ecg_calibration",
                    "uri": f"file:///prepared/{evidence_id}.json",
                    "sha256": derived_sha,
                    "metadata": {
                        "schema_version": "electrotrace-ep-calibration-v1",
                        "representation": "median_beat_template",
                        "sampling_rate_hz": 500.0,
                        "lead_names": list(leads or LEADS),
                        "source_sha256": source_sha,
                        "qrs": {"median_duration_ms": 96.0},
                    },
                },
                "coordinate_frame": "clinical_ecg",
                "units": "mV",
                "acquired_at": None,
            }
        ],
        "likelihood_hints": [
            {
                "term_id": f"{evidence_id}:morphology",
                "observation_id": evidence_id,
                "model_output": "ecg",
                "discrepancy": "correlation",
                "weight": 1.0,
            }
        ],
        "summary": {"artifact_id": f"electrotrace-{evidence_id}"},
    }


@dataclass
class _Spec:
    name: str


class _Adapter:
    def __init__(self, name: str, handler):
        self.spec = _Spec(name)
        self.handler = handler
        self.calls: list[tuple[str, dict]] = []

    def available(self) -> bool:
        return True

    def invoke(self, capability: str, payload: dict):
        self.calls.append((capability, deepcopy(payload)))
        return self.handler(capability, payload)


class _Registry:
    def __init__(self, adapters: dict[str, _Adapter]):
        self.adapters = adapters

    def capability(self, capability: str):
        return self.adapters.get(capability)


def _electrotrace_adapter(*, bad_source: bool = False, bad_leads: bool = False) -> _Adapter:
    def handler(_capability: str, payload: dict) -> dict:
        observation = payload["observations"][0]
        evidence_id = observation["values"]["observation_id"]
        if evidence_id == "fit-evidence":
            source_sha = "9" * 64 if bad_source else SHA_FIT
            derived_sha = SHA_DERIVED_FIT
        else:
            source_sha = SHA_HOLDOUT
            derived_sha = SHA_DERIVED_HOLDOUT
        leads = list(reversed(LEADS)) if bad_leads else LEADS
        return _electrotrace_handoff(
            evidence_id=evidence_id,
            source_sha=source_sha,
            derived_sha=derived_sha,
            leads=leads,
        )

    return _Adapter("ElectroTrace", handler)


def test_electrotrace_bridge_accepts_matching_patient_lineage() -> None:
    case = _case()
    registry = _Registry(
        {"electrical.prepare_calibration": _electrotrace_adapter()}
    )
    prepared = prepare_patient_ecg_evidence(
        registry,  # type: ignore[arg-type]
        case=case,
        evidence=case.evidence[0],
        require_standard_12_leads=True,
    )
    assert prepared.source_sha256 == SHA_FIT
    assert prepared.observation["artifact"]["sha256"] == SHA_DERIVED_FIT
    metadata = prepared.observation["artifact"]["metadata"]
    assert metadata["acquisition_id"] == "fit"
    assert metadata["evidence_id"] == "fit-evidence"
    assert metadata["withheld_from_fit"] is False


def test_electrotrace_bridge_rejects_wrong_raw_source_hash() -> None:
    case = _case()
    registry = _Registry(
        {"electrical.prepare_calibration": _electrotrace_adapter(bad_source=True)}
    )
    with pytest.raises(PatientEPWorkflowError, match="source hash does not match"):
        prepare_patient_ecg_evidence(
            registry,  # type: ignore[arg-type]
            case=case,
            evidence=case.evidence[0],
            require_standard_12_leads=True,
        )


def test_electrotrace_bridge_rejects_changed_lead_order() -> None:
    case = _case()
    registry = _Registry(
        {"electrical.prepare_calibration": _electrotrace_adapter(bad_leads=True)}
    )
    with pytest.raises(PatientEPWorkflowError, match="lead order/names"):
        prepare_patient_ecg_evidence(
            registry,  # type: ignore[arg-type]
            case=case,
            evidence=case.evidence[0],
            require_standard_12_leads=True,
        )


def test_strict_camps_path_uses_prepared_fit_ecg_and_never_exposes_holdout_to_solver() -> None:
    case = _case()
    ep_calibration_payloads: list[dict] = []
    ep_simulation_payloads: list[dict] = []

    anatomy = _Adapter(
        "CardiAnatomy",
        lambda _capability, _payload: {"bundle_fingerprint": BUNDLE_SHA},
    )
    electrotrace = _electrotrace_adapter()

    def calibrate(_capability: str, payload: dict) -> dict:
        ep_calibration_payloads.append(deepcopy(payload))
        return {
            "subject_id": "P1",
            "backend": "camps-cdt-external-v1",
            "parameters": {
                "values": {"sheet_speed": 0.04},
                "units": {"sheet_speed": "cm/ms"},
                "source": "calibrated",
            },
            "provenance": {},
        }

    def simulate(_capability: str, payload: dict) -> dict:
        ep_simulation_payloads.append(deepcopy(payload))
        return {
            "subject_id": "P1",
            "backend": "camps-cdt-external-v1",
            "parameters": payload["parameters"],
            "outputs": [],
            "provenance": {"anatomy_bundle_fingerprint": BUNDLE_SHA},
        }

    registry = _Registry(
        {
            "anatomy.validate": anatomy,
            "electrical.prepare_calibration": electrotrace,
            "ep.calibrate": _Adapter("CardiEP", calibrate),
            "ep.simulate": _Adapter("CardiEP", simulate),
        }
    )
    bundle = {
        "contract_version": "2.0.0",
        "subject_id": "P1",
        "study_id": "study-1",
        "acquisition_id": "ct",
        "artifacts": [
            {
                "artifact_id": "patient-volume",
                "kind": "volume_mesh",
                "uri": "file:///patient.vtu",
                "sha256": "1" * 64,
                "frame_id": "model-mm",
                "metadata": {},
            }
        ],
        "frames": [],
        "registrations": [],
        "labels": [],
        "stages": [],
        "qc": {},
        "provenance": {},
        "bundle_fingerprint": BUNDLE_SHA,
    }
    spec = PatientEPWorkflowSpec(
        patient_id="P1",
        anatomy_acquisition_id="ct",
        calibration_evidence_ids=["fit-evidence"],
        validation_evidence_ids=["holdout-evidence"],
        fast_backend="camps-cdt-external-v1",
        confirmation_backend="camps-cdt-external-v1",
        observation_model="lead_field",
        scientific_grade=True,
    )

    result = run_strict_patient_ep_workflow(
        registry,  # type: ignore[arg-type]
        case=case,
        spec=spec,
        anatomy_bundle=bundle,
        parameter_bounds={"sheet_speed": (0.02, 0.08)},
        initial_parameters={"sheet_speed": 0.04},
        parameter_units={"sheet_speed": "cm/ms"},
        calibration_settings={"workspace_manifest": {"fixture": True}},
        measurement_output_root="prepared-ecg",
    )

    assert len(ep_calibration_payloads) == 1
    fit_observations = ep_calibration_payloads[0]["observations"]
    assert len(fit_observations) == 1
    assert fit_observations[0]["artifact"]["kind"] == "electrotrace_ecg_calibration"
    assert fit_observations[0]["artifact"]["sha256"] == SHA_DERIVED_FIT
    assert SHA_DERIVED_HOLDOUT not in repr(ep_calibration_payloads[0])

    assert len(ep_simulation_payloads) == 1
    assert ep_simulation_payloads[0]["observations"] == []
    assert SHA_DERIVED_HOLDOUT not in repr(ep_simulation_payloads[0])

    assert len(result.holdout_observations) == 1
    holdout = result.holdout_observations[0]
    assert holdout["artifact"]["sha256"] == SHA_DERIVED_HOLDOUT
    assert holdout["artifact"]["metadata"]["withheld_from_fit"] is True
    assert result.calibration["provenance"]["hearttwin_measurement_preparation"]
