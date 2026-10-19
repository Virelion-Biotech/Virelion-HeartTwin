"""Patient-specific ventricular activation/QRS workflow for HeartTwin.

The first patient-specific vertical slice is intentionally narrow:

    patient anatomy -> EP calibration -> independent forward confirmation

Validation measurements are never passed into the calibration request and are
not supplied to the confirmation solver. They are returned as untouched
references for a separate evaluator so a solver cannot accidentally condition
on its own holdout data.
"""
from __future__ import annotations

from copy import deepcopy
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from .contracts import AnatomyBundlePayload
from .patient_model import (
    EvidenceSplitReport,
    PatientAcquisition,
    PatientCase,
    PatientCaseQC,
    PatientEPWorkflowSpec,
    PatientEvidenceItem,
    SourceFileRef,
    assess_patient_case_for_ep,
    validate_patient_ep_workflow,
)
from .service_registry import ServiceRegistry


# These names document the intended architecture but are not registered CardiEP
# backends today. Fail explicitly instead of allowing a planned backend name to
# look operational.
_PLANNED_BACKENDS = {
    "cardiep_eikonal": (
        "planned patient fast-activation backend alias; use an actually registered "
        "CardiEP backend such as numpy-eikonal-v1 for plumbing tests"
    ),
    "opencarp_monodomain": (
        "planned openCARP confirmation backend; an openCARP command/plugin adapter "
        "has not yet been implemented in CardiEP"
    ),
}


class PatientEPWorkflowError(RuntimeError):
    """Raised when a patient-specific EP boundary is unavailable or ambiguous."""


class PatientEPPreparedRequests(BaseModel):
    model_config = ConfigDict(extra="forbid")
    patient_id: str
    anatomy_bundle_fingerprint: str
    anatomy_ref: dict[str, Any]
    calibration_request: dict[str, Any]
    confirmation_request_template: dict[str, Any]
    holdout_observations: list[dict[str, Any]] = Field(default_factory=list)
    evidence_split: EvidenceSplitReport
    patient_qc: PatientCaseQC


class PatientEPWorkflowResult(BaseModel):
    model_config = ConfigDict(extra="forbid")
    patient_id: str
    evidence_split: EvidenceSplitReport
    patient_qc: PatientCaseQC
    anatomy_bundle: dict[str, Any]
    calibration: dict[str, Any]
    confirmation: dict[str, Any]
    holdout_observations: list[dict[str, Any]] = Field(default_factory=list)
    scientific_grade: bool
    limitations: list[str] = Field(default_factory=list)


def _invoke(registry: ServiceRegistry, capability: str, payload: dict[str, Any]) -> dict[str, Any]:
    adapter = registry.capability(capability)
    if adapter is None:
        raise PatientEPWorkflowError(f"No service advertises {capability}")
    if not adapter.available():
        raise PatientEPWorkflowError(f"{adapter.spec.name} is unavailable for {capability}")
    output = adapter.invoke(capability, payload)
    if not isinstance(output, dict):
        raise PatientEPWorkflowError(f"{capability} returned a non-object payload")
    return output


def _reject_planned_backend_names(spec: PatientEPWorkflowSpec) -> None:
    for field_name, backend in (
        ("fast_backend", spec.fast_backend),
        ("confirmation_backend", spec.confirmation_backend),
    ):
        explanation = _PLANNED_BACKENDS.get(backend)
        if explanation is not None:
            raise PatientEPWorkflowError(
                f"{field_name}={backend!r} is not implemented: {explanation}"
            )


def _one_anatomy_artifact(bundle: AnatomyBundlePayload) -> dict[str, Any]:
    """Select one EP volume artifact; never silently choose between duplicates."""
    for kind in ("volume_mesh", "ep_volume_mesh"):
        matches = [item for item in bundle.artifacts if item.get("kind") == kind]
        if len(matches) == 1:
            return dict(matches[0])
        if len(matches) > 1:
            raise PatientEPWorkflowError(
                f"Anatomy bundle contains multiple {kind!r} artifacts; selection must be explicit"
            )
    raise PatientEPWorkflowError(
        "Patient EP workflow requires one simulation-quality volume mesh artifact"
    )


def _ep_anatomy_ref(bundle: AnatomyBundlePayload) -> dict[str, Any]:
    artifact = _one_anatomy_artifact(bundle)
    if bundle.bundle_fingerprint is None:
        raise PatientEPWorkflowError("Anatomy bundle has no canonical fingerprint")
    metadata = dict(artifact.get("metadata") or {})
    metadata["bundle_fingerprint"] = bundle.bundle_fingerprint
    frame_id = artifact.get("frame_id", artifact.get("coordinate_frame"))
    return {
        "artifact_id": str(artifact["artifact_id"]),
        "kind": str(artifact["kind"]),
        "uri": str(artifact["uri"]),
        "sha256": artifact.get("sha256"),
        "coordinate_frame": None if frame_id is None else str(frame_id),
        "metadata": metadata,
    }


def _evidence_acquisition(case: PatientCase, item: PatientEvidenceItem) -> PatientAcquisition:
    if not item.acquisition_id:
        raise PatientEPWorkflowError(
            f"Evidence {item.evidence_id!r} has no acquisition_id; patient ECG workflow "
            "currently requires acquisition-backed evidence"
        )
    matches = [acq for acq in case.acquisitions if acq.acquisition_id == item.acquisition_id]
    if len(matches) != 1:
        raise PatientEPWorkflowError(
            f"Evidence {item.evidence_id!r} does not resolve to exactly one acquisition"
        )
    return matches[0]


def _primary_source(acquisition: PatientAcquisition) -> SourceFileRef:
    if not acquisition.source_files:
        raise PatientEPWorkflowError(
            f"Acquisition {acquisition.acquisition_id!r} has no source file"
        )
    primary_id = acquisition.metadata.get("primary_file_id")
    if primary_id is not None:
        matches = [item for item in acquisition.source_files if item.file_id == primary_id]
        if len(matches) != 1:
            raise PatientEPWorkflowError(
                f"Acquisition {acquisition.acquisition_id!r} primary_file_id is unresolved"
            )
        return matches[0]
    if len(acquisition.source_files) != 1:
        raise PatientEPWorkflowError(
            f"Acquisition {acquisition.acquisition_id!r} contains multiple files; set "
            "metadata.primary_file_id rather than relying on file order"
        )
    return acquisition.source_files[0]


def _ecg_observation(case: PatientCase, item: PatientEvidenceItem) -> dict[str, Any]:
    acquisition = _evidence_acquisition(case, item)
    if acquisition.modality != "ecg":
        raise PatientEPWorkflowError(
            f"Evidence {item.evidence_id!r} resolves to {acquisition.modality}, not ECG"
        )
    source = _primary_source(acquisition)
    metadata = dict(source.metadata)
    metadata.update(
        {
            "acquisition_id": acquisition.acquisition_id,
            "encounter_id": acquisition.encounter_id,
            "sampling_rate_hz": acquisition.sampling_rate_hz,
            "lead_definitions": list(acquisition.lead_definitions),
            "withheld_from_fit": item.withheld_from_fit,
            "evidence_id": item.evidence_id,
        }
    )
    artifact = {
        "artifact_id": source.file_id,
        "kind": "measured_ecg",
        "uri": source.uri,
        "sha256": source.sha256,
        "coordinate_frame": acquisition.coordinate_frame_id,
        "metadata": metadata,
    }
    return {
        "observation_id": item.observation_id or item.evidence_id,
        "kind": "ecg",
        "artifact": artifact,
        "coordinate_frame": acquisition.coordinate_frame_id,
        "units": acquisition.units.get("signal"),
        "acquired_at": (
            None if acquisition.acquired_at is None else acquisition.acquired_at.isoformat()
        ),
    }


def _selected_evidence(
    case: PatientCase,
    spec: PatientEPWorkflowSpec,
) -> tuple[list[PatientEvidenceItem], list[PatientEvidenceItem]]:
    by_id = {item.evidence_id: item for item in case.evidence}
    fit = [by_id[evidence_id] for evidence_id in spec.calibration_evidence_ids]
    holdout = [by_id[evidence_id] for evidence_id in spec.validation_evidence_ids]
    return fit, holdout


def prepare_patient_ep_requests(
    *,
    case: PatientCase,
    spec: PatientEPWorkflowSpec,
    anatomy_bundle: dict[str, Any] | AnatomyBundlePayload,
    parameter_bounds: dict[str, tuple[float, float]],
    initial_parameters: dict[str, float] | None = None,
    parameter_units: dict[str, str] | None = None,
    calibration_settings: dict[str, Any] | None = None,
    confirmation_settings: dict[str, Any] | None = None,
) -> PatientEPPreparedRequests:
    """Prepare strict CardiEP requests without executing any scientific engine."""
    split = validate_patient_ep_workflow(case, spec)
    qc = assess_patient_case_for_ep(case)
    _reject_planned_backend_names(spec)
    bundle = (
        anatomy_bundle
        if isinstance(anatomy_bundle, AnatomyBundlePayload)
        else AnatomyBundlePayload.model_validate(anatomy_bundle)
    )
    if bundle.subject_id != case.patient_id:
        raise PatientEPWorkflowError("Anatomy bundle subject does not match PatientCase")
    if bundle.bundle_fingerprint is None:
        raise PatientEPWorkflowError(
            "Anatomy bundle must be canonicalized by anatomy.validate before EP preparation"
        )

    if spec.scientific_grade and spec.observation_model == "lead_field":
        if spec.fast_backend == "numpy-eikonal-v1":
            raise PatientEPWorkflowError(
                "numpy-eikonal-v1 uses CardiEP's exploratory pseudo-ECG and cannot satisfy "
                "a scientific-grade lead-field observation model"
            )
        if spec.confirmation_backend == "numpy-eikonal-v1":
            raise PatientEPWorkflowError(
                "scientific-grade confirmation cannot use numpy-eikonal-v1 pseudo-ECG"
            )

    fit_evidence, holdout_evidence = _selected_evidence(case, spec)
    calibration_observations = [_ecg_observation(case, item) for item in fit_evidence]
    holdout_observations = [_ecg_observation(case, item) for item in holdout_evidence]
    anatomy_ref = _ep_anatomy_ref(bundle)

    calibration_request = {
        "subject_id": case.patient_id,
        "anatomy_ref": anatomy_ref,
        "observations": calibration_observations,
        "backend": spec.fast_backend,
        "parameter_bounds": {
            str(name): [float(bounds[0]), float(bounds[1])]
            for name, bounds in parameter_bounds.items()
        },
        "initial_parameters": (
            None
            if initial_parameters is None
            else {
                "values": {str(k): float(v) for k, v in initial_parameters.items()},
                "units": dict(parameter_units or {}),
                "source": "prior",
            }
        ),
        "inference_backend": None,
        "settings": dict(calibration_settings or {}),
    }
    confirmation_request_template = {
        "subject_id": case.patient_id,
        "anatomy_ref": anatomy_ref,
        "backend": spec.confirmation_backend,
        "parameters": {
            "values": {},
            "units": dict(parameter_units or {}),
            "source": "calibrated",
        },
        # Deliberately empty. Holdout observations are evaluated only after the
        # solver has emitted an unconditional prediction.
        "observations": [],
        "settings": dict(confirmation_settings or {}),
    }
    return PatientEPPreparedRequests(
        patient_id=case.patient_id,
        anatomy_bundle_fingerprint=bundle.bundle_fingerprint,
        anatomy_ref=anatomy_ref,
        calibration_request=calibration_request,
        confirmation_request_template=confirmation_request_template,
        holdout_observations=holdout_observations,
        evidence_split=split,
        patient_qc=qc,
    )


def run_patient_ep_workflow(
    registry: ServiceRegistry,
    *,
    case: PatientCase,
    spec: PatientEPWorkflowSpec,
    anatomy_bundle: dict[str, Any] | None = None,
    anatomy_request: dict[str, Any] | None = None,
    parameter_bounds: dict[str, tuple[float, float]],
    initial_parameters: dict[str, float] | None = None,
    parameter_units: dict[str, str] | None = None,
    calibration_settings: dict[str, Any] | None = None,
    confirmation_settings: dict[str, Any] | None = None,
) -> PatientEPWorkflowResult:
    """Execute patient anatomy readiness, calibration, and held-out prediction.

    Exactly one of ``anatomy_bundle`` or ``anatomy_request`` is required. Raw
    patient images are not silently segmented by HeartTwin; the selected
    CardiAnatomy request must name/configure the reconstruction backend.
    """
    if (anatomy_bundle is None) == (anatomy_request is None):
        raise PatientEPWorkflowError(
            "Provide exactly one of anatomy_bundle or anatomy_request"
        )

    split = validate_patient_ep_workflow(case, spec)
    qc = assess_patient_case_for_ep(case)
    _reject_planned_backend_names(spec)

    if anatomy_request is not None:
        request = deepcopy(anatomy_request)
        declared = request.get("subject_id")
        if declared is not None and str(declared) != case.patient_id:
            raise PatientEPWorkflowError("Anatomy request subject does not match patient")
        request["subject_id"] = case.patient_id
        output = _invoke(registry, "anatomy.build", request)
        bundle = AnatomyBundlePayload.model_validate(output)
    else:
        bundle = AnatomyBundlePayload.model_validate(anatomy_bundle)

    if bundle.subject_id != case.patient_id:
        raise PatientEPWorkflowError("Anatomy bundle subject does not match patient")

    readiness = _invoke(
        registry,
        "anatomy.validate",
        {"target": "ep", "bundle": bundle.model_dump(mode="json")},
    )
    fingerprint = readiness.get("bundle_fingerprint") or bundle.bundle_fingerprint
    if not isinstance(fingerprint, str) or len(fingerprint) != 64:
        raise PatientEPWorkflowError(
            "CardiAnatomy did not provide a canonical EP-ready bundle fingerprint"
        )
    if bundle.bundle_fingerprint not in {None, fingerprint}:
        raise PatientEPWorkflowError("CardiAnatomy changed the anatomy bundle fingerprint")
    bundle = bundle.model_copy(update={"bundle_fingerprint": fingerprint})

    prepared = prepare_patient_ep_requests(
        case=case,
        spec=spec,
        anatomy_bundle=bundle,
        parameter_bounds=parameter_bounds,
        initial_parameters=initial_parameters,
        parameter_units=parameter_units,
        calibration_settings=calibration_settings,
        confirmation_settings=confirmation_settings,
    )
    calibration = _invoke(registry, "ep.calibrate", prepared.calibration_request)
    if str(calibration.get("subject_id")) != case.patient_id:
        raise PatientEPWorkflowError("EP calibration returned a different subject")
    if str(calibration.get("backend")) != spec.fast_backend:
        raise PatientEPWorkflowError("EP calibration backend identity changed")
    parameter_payload = calibration.get("parameters")
    if not isinstance(parameter_payload, dict) or not isinstance(
        parameter_payload.get("values"), dict
    ):
        raise PatientEPWorkflowError("EP calibration returned no calibrated parameter set")

    confirmation_request = deepcopy(prepared.confirmation_request_template)
    confirmation_request["parameters"] = {
        "values": dict(parameter_payload["values"]),
        "units": dict(parameter_payload.get("units") or parameter_units or {}),
        "source": "calibrated",
    }
    confirmation = _invoke(registry, "ep.simulate", confirmation_request)
    if str(confirmation.get("subject_id")) != case.patient_id:
        raise PatientEPWorkflowError("EP confirmation returned a different subject")
    if str(confirmation.get("backend")) != spec.confirmation_backend:
        raise PatientEPWorkflowError("EP confirmation backend identity changed")
    if confirmation.get("provenance", {}).get("anatomy_bundle_fingerprint") not in {
        None,
        fingerprint,
    }:
        raise PatientEPWorkflowError("EP confirmation lost anatomy lineage")

    limitations = list(qc.warnings)
    if spec.torso_mode != "patient_specific":
        limitations.append(
            "ECG forward model does not use a patient-specific torso; morphology/amplitude "
            "personalization is therefore incomplete"
        )
    if spec.electrode_mode != "recorded":
        limitations.append(
            "ECG electrode coordinates are not directly recorded for this patient"
        )

    return PatientEPWorkflowResult(
        patient_id=case.patient_id,
        evidence_split=split,
        patient_qc=qc,
        anatomy_bundle=bundle.model_dump(mode="json"),
        calibration=calibration,
        confirmation=confirmation,
        holdout_observations=prepared.holdout_observations,
        scientific_grade=spec.scientific_grade,
        limitations=limitations,
    )
