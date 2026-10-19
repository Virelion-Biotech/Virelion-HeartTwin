"""HeartTwin-facing envelope for declared openCARP patient experiments.

CardiEP owns the detailed solver contract. HeartTwin intentionally does not
reimplement that schema. It does, however, require a stable experiment identity,
checks that the declared ECG/torso/electrode modes agree with the patient
workflow, fingerprints the complete declaration, and carries that identity
through orchestration/reporting/provenance.
"""
from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict, Field, model_validator

from .patient_model import PatientEPWorkflowSpec
from .provenance import sha256

_STANDARD_12 = {
    "I", "II", "III", "aVR", "aVL", "aVF",
    "V1", "V2", "V3", "V4", "V5", "V6",
}


class OpenCARPExperimentDeclaration(BaseModel):
    model_config = ConfigDict(extra="forbid")

    contract_version: str
    experiment_id: str
    experiment_version: str
    intended_use: str
    fingerprint: str = Field(min_length=64, max_length=64)
    field_model: str
    activation_model: str
    forward_model: str
    torso_mode: str
    electrode_mode: str
    expected_leads: list[str]

    @model_validator(mode="after")
    def validate_identity(self) -> OpenCARPExperimentDeclaration:
        if not self.contract_version.strip():
            raise ValueError("openCARP experiment contract_version must be explicit")
        if not self.experiment_id.strip() or not self.experiment_version.strip():
            raise ValueError("openCARP experiment ID/version must be explicit")
        if not self.intended_use.strip():
            raise ValueError("openCARP experiment intended_use must be explicit")
        return self


def inspect_opencarp_experiment(
    raw: dict[str, Any] | None,
    *,
    workflow: PatientEPWorkflowSpec,
) -> OpenCARPExperimentDeclaration:
    """Validate HeartTwin-visible experiment semantics and fingerprint all bytes/fields."""
    if not isinstance(raw, dict):
        raise ValueError(
            "scientific openCARP confirmation requires confirmation_settings.opencarp_experiment_spec"
        )
    ecg = raw.get("ecg")
    if not isinstance(ecg, dict):
        raise ValueError("openCARP experiment declaration requires an ecg object")

    forward_model = str(ecg.get("forward_model") or "")
    torso_mode = str(ecg.get("torso_mode") or "")
    electrode_mode = str(ecg.get("electrode_mode") or "")
    expected_leads = [str(item) for item in ecg.get("expected_leads") or []]
    if forward_model != workflow.observation_model:
        raise ValueError(
            "openCARP experiment forward_model disagrees with patient workflow observation_model"
        )
    if torso_mode != workflow.torso_mode:
        raise ValueError("openCARP experiment torso_mode disagrees with patient workflow")
    if electrode_mode != workflow.electrode_mode:
        raise ValueError("openCARP experiment electrode_mode disagrees with patient workflow")
    if set(expected_leads) != _STANDARD_12 or len(expected_leads) != len(_STANDARD_12):
        raise ValueError("openCARP patient experiment must declare exactly the standard 12 leads")

    declaration = OpenCARPExperimentDeclaration(
        contract_version=str(raw.get("contract_version") or ""),
        experiment_id=str(raw.get("experiment_id") or ""),
        experiment_version=str(raw.get("experiment_version") or ""),
        intended_use=str(raw.get("intended_use") or ""),
        fingerprint=sha256(raw),
        field_model=str(raw.get("field_model") or ""),
        activation_model=str(raw.get("activation_model") or ""),
        forward_model=forward_model,
        torso_mode=torso_mode,
        electrode_mode=electrode_mode,
        expected_leads=expected_leads,
    )
    if workflow.scientific_grade and declaration.contract_version != "1.0":
        raise ValueError(
            "scientific patient EP currently supports openCARP experiment contract_version='1.0'"
        )
    if not declaration.field_model or not declaration.activation_model:
        raise ValueError("openCARP experiment field_model and activation_model must be explicit")
    return declaration
