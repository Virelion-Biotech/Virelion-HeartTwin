"""Strict patient-EP entrypoint layered over the core HeartTwin workflow.

This wrapper makes patient-specific anatomy readiness mandatory, routes
scientific ECG calibration through ElectroTrace, and prevents implicit parameter
replay between heterogeneous electrophysiology backends. Cross-backend
confirmation is permitted only through an explicit, versioned parameter-mapping
contract that accounts for every calibrated source parameter.
"""
from __future__ import annotations

from copy import deepcopy
from pathlib import Path
from typing import Any

from .contracts import AnatomyBundlePayload
from .ep_parameter_mapping import (
    EPParameterMappingSpec,
    apply_ep_parameter_mapping,
)
from .patient_ecg_bridge import prepare_patient_ecg_split
from .patient_ep import (
    PatientEPWorkflowError,
    PatientEPWorkflowResult,
    _check_bundle_selection,
    _invoke,
    _reject_planned_backend_names,
    _selected_anatomy_acquisition,
    _selected_evidence,
    prepare_patient_ep_requests,
)
from .patient_model import (
    PatientCase,
    PatientEPWorkflowSpec,
    assess_patient_case_for_ep,
    validate_patient_ep_workflow,
)
from .service_registry import ServiceRegistry

_CAMPS_BACKEND = "camps-cdt-external-v1"


def _require_patient_ep_ready(
    registry: ServiceRegistry,
    bundle: AnatomyBundlePayload,
) -> AnatomyBundlePayload:
    readiness = _invoke(
        registry,
        "anatomy.validate",
        {"target": "patient_ep", "bundle": bundle.model_dump(mode="json")},
    )
    fingerprint = readiness.get("bundle_fingerprint") or bundle.bundle_fingerprint
    if not isinstance(fingerprint, str) or len(fingerprint) != 64:
        raise PatientEPWorkflowError(
            "CardiAnatomy patient_ep validation did not return a canonical bundle fingerprint"
        )
    if bundle.bundle_fingerprint not in {None, fingerprint}:
        raise PatientEPWorkflowError(
            "CardiAnatomy patient_ep validation changed the anatomy bundle fingerprint"
        )
    return bundle.model_copy(update={"bundle_fingerprint": fingerprint})


def _coerce_mapping(
    parameter_mapping: EPParameterMappingSpec | dict[str, Any] | None,
) -> EPParameterMappingSpec | None:
    if parameter_mapping is None:
        return None
    if isinstance(parameter_mapping, EPParameterMappingSpec):
        return parameter_mapping
    return EPParameterMappingSpec.model_validate(parameter_mapping)


def _require_safe_parameter_replay(
    spec: PatientEPWorkflowSpec,
    parameter_mapping: EPParameterMappingSpec | dict[str, Any] | None = None,
) -> EPParameterMappingSpec | None:
    """Require explicit semantics before parameters cross backend boundaries."""
    mapping = _coerce_mapping(parameter_mapping)
    if spec.fast_backend == spec.confirmation_backend:
        if mapping is not None:
            raise PatientEPWorkflowError(
                "parameter_mapping must be omitted when calibration and confirmation use "
                "the same backend"
            )
        return None

    if mapping is None:
        raise PatientEPWorkflowError(
            "Strict patient EP refuses implicit cross-backend parameter replay. "
            "Provide an explicit EPParameterMappingSpec with units, mapping rules, "
            "and declared ignored parameters."
        )
    if mapping.source_backend != spec.fast_backend:
        raise PatientEPWorkflowError(
            "parameter mapping source_backend does not match fast_backend"
        )
    if mapping.target_backend != spec.confirmation_backend:
        raise PatientEPWorkflowError(
            "parameter mapping target_backend does not match confirmation_backend"
        )
    return mapping


def _confirmation_parameters(
    *,
    spec: PatientEPWorkflowSpec,
    parameter_payload: dict[str, Any],
    mapping: EPParameterMappingSpec | None,
    fallback_units: dict[str, str] | None,
) -> tuple[dict[str, Any], dict[str, Any] | None]:
    values = parameter_payload.get("values")
    if not isinstance(values, dict) or not values:
        raise PatientEPWorkflowError("EP calibration returned no calibrated parameter set")
    units = dict(parameter_payload.get("units") or fallback_units or {})

    if spec.fast_backend == spec.confirmation_backend:
        return (
            {
                "values": {str(name): float(value) for name, value in values.items()},
                "units": units,
                "source": "calibrated",
            },
            None,
        )

    if mapping is None:  # defensive; guarded by _require_safe_parameter_replay
        raise PatientEPWorkflowError("cross-backend replay reached execution without mapping")
    try:
        mapped = apply_ep_parameter_mapping(
            mapping,
            source_backend=spec.fast_backend,
            target_backend=spec.confirmation_backend,
            values={str(name): float(value) for name, value in values.items()},
            units=units,
        )
    except (TypeError, ValueError) as exc:
        raise PatientEPWorkflowError(f"EP parameter mapping failed: {exc}") from exc

    return (
        {
            "values": dict(mapped.values),
            "units": dict(mapped.units),
            "source": "calibrated",
        },
        mapped.model_dump(mode="json"),
    )


def _electrotrace_patient_observations(
    registry: ServiceRegistry,
    *,
    case: PatientCase,
    spec: PatientEPWorkflowSpec,
    measurement_settings: dict[str, Any] | None,
    measurement_output_root: str | Path | None,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], dict[str, Any]]:
    """Prepare fit/holdout ECGs identically while keeping their roles isolated."""
    fit_evidence, holdout_evidence = _selected_evidence(case, spec)
    root = (
        Path(measurement_output_root)
        if measurement_output_root is not None
        else Path("hearttwin-patient-ep") / case.patient_id / "electrotrace"
    )
    fit, holdout = prepare_patient_ecg_split(
        registry,
        case=case,
        calibration_evidence=fit_evidence,
        validation_evidence=holdout_evidence,
        settings=measurement_settings,
        require_standard_12_leads=True,
        output_root=root,
    )
    fit_observations = [item.observation for item in fit]
    holdout_observations = [item.observation for item in holdout]
    provenance = {
        "service": "ElectroTrace",
        "capability": "electrical.prepare_calibration",
        "schema_version": "electrotrace-ep-calibration-v1",
        "calibration": [
            {
                "evidence_id": item.evidence_id,
                "acquisition_id": item.acquisition_id,
                "source_file_id": item.source_file_id,
                "source_sha256": item.source_sha256,
                "artifact_id": item.observation["artifact"]["artifact_id"],
                "artifact_sha256": item.observation["artifact"]["sha256"],
            }
            for item in fit
        ],
        "validation": [
            {
                "evidence_id": item.evidence_id,
                "acquisition_id": item.acquisition_id,
                "source_file_id": item.source_file_id,
                "source_sha256": item.source_sha256,
                "artifact_id": item.observation["artifact"]["artifact_id"],
                "artifact_sha256": item.observation["artifact"]["sha256"],
            }
            for item in holdout
        ],
    }
    return fit_observations, holdout_observations, provenance


def run_strict_patient_ep_workflow(
    registry: ServiceRegistry,
    *,
    case: PatientCase,
    spec: PatientEPWorkflowSpec,
    anatomy_bundle: dict[str, Any] | None = None,
    anatomy_request: dict[str, Any] | None = None,
    parameter_bounds: dict[str, tuple[float, float]],
    initial_parameters: dict[str, float] | None = None,
    parameter_units: dict[str, str] | None = None,
    parameter_mapping: EPParameterMappingSpec | dict[str, Any] | None = None,
    calibration_settings: dict[str, Any] | None = None,
    confirmation_settings: dict[str, Any] | None = None,
    measurement_settings: dict[str, Any] | None = None,
    measurement_output_root: str | Path | None = None,
) -> PatientEPWorkflowResult:
    """Run strict patient anatomy -> calibration -> unconditional confirmation.

    For the scientific Camps calibration backend, raw ECG acquisitions are first
    transformed into immutable ElectroTrace calibration artifacts. Fit and
    holdout acquisitions use the same fixed measurement-preparation settings,
    but validation observations remain outside both calibration and confirmation
    solver requests.
    """
    if (anatomy_bundle is None) == (anatomy_request is None):
        raise PatientEPWorkflowError(
            "Provide exactly one of anatomy_bundle or anatomy_request"
        )

    validate_patient_ep_workflow(case, spec)
    qc = assess_patient_case_for_ep(case)
    _reject_planned_backend_names(spec)
    mapping = _require_safe_parameter_replay(spec, parameter_mapping)
    selected_anatomy = _selected_anatomy_acquisition(case, spec)

    if anatomy_request is not None:
        request = deepcopy(anatomy_request)
        declared = request.get("subject_id")
        if declared is not None and str(declared) != case.patient_id:
            raise PatientEPWorkflowError("Anatomy request subject does not match patient")
        request["subject_id"] = case.patient_id
        request_acquisition = request.get("acquisition")
        if isinstance(request_acquisition, dict):
            declared_acquisition = request_acquisition.get("acquisition_id")
            if (
                declared_acquisition is not None
                and str(declared_acquisition) != selected_anatomy.acquisition_id
            ):
                raise PatientEPWorkflowError(
                    "Anatomy request acquisition does not match anatomy_acquisition_id"
                )
        built = _invoke(registry, "anatomy.build", request)
        bundle = AnatomyBundlePayload.model_validate(built)
    else:
        bundle = AnatomyBundlePayload.model_validate(anatomy_bundle)

    _check_bundle_selection(case, spec, bundle)
    bundle = _require_patient_ep_ready(registry, bundle)

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

    calibration_request = deepcopy(prepared.calibration_request)
    holdout_observations = list(prepared.holdout_observations)
    measurement_provenance: dict[str, Any] | None = None
    if spec.fast_backend == _CAMPS_BACKEND:
        (
            calibration_request["observations"],
            holdout_observations,
            measurement_provenance,
        ) = _electrotrace_patient_observations(
            registry,
            case=case,
            spec=spec,
            measurement_settings=measurement_settings,
            measurement_output_root=measurement_output_root,
        )
        settings = dict(calibration_request.get("settings") or {})
        if "hearttwin_measurement_preparation" in settings:
            raise PatientEPWorkflowError(
                "calibration settings use reserved hearttwin_measurement_preparation key"
            )
        settings["hearttwin_measurement_preparation"] = measurement_provenance
        calibration_request["settings"] = settings

    calibration = _invoke(registry, "ep.calibrate", calibration_request)
    if str(calibration.get("subject_id")) != case.patient_id:
        raise PatientEPWorkflowError("EP calibration returned a different subject")
    if str(calibration.get("backend")) != spec.fast_backend:
        raise PatientEPWorkflowError("EP calibration backend identity changed")
    parameter_payload = calibration.get("parameters")
    if not isinstance(parameter_payload, dict):
        raise PatientEPWorkflowError("EP calibration returned no parameter payload")

    confirmation_parameters, mapping_result = _confirmation_parameters(
        spec=spec,
        parameter_payload=parameter_payload,
        mapping=mapping,
        fallback_units=parameter_units,
    )
    confirmation_request = deepcopy(prepared.confirmation_request_template)
    confirmation_request["parameters"] = confirmation_parameters
    if mapping_result is not None:
        settings = dict(confirmation_request.get("settings") or {})
        settings["hearttwin_parameter_mapping"] = {
            "mapping_id": mapping_result["mapping_id"],
            "mapping_version": mapping_result["mapping_version"],
            "mapping_fingerprint": mapping_result["mapping_fingerprint"],
            "source_parameter_fingerprint": mapping_result[
                "source_parameter_fingerprint"
            ],
        }
        confirmation_request["settings"] = settings

    confirmation = _invoke(registry, "ep.simulate", confirmation_request)
    if str(confirmation.get("subject_id")) != case.patient_id:
        raise PatientEPWorkflowError("EP confirmation returned a different subject")
    if str(confirmation.get("backend")) != spec.confirmation_backend:
        raise PatientEPWorkflowError("EP confirmation backend identity changed")
    if confirmation.get("provenance", {}).get("anatomy_bundle_fingerprint") not in {
        None,
        prepared.anatomy_bundle_fingerprint,
    }:
        raise PatientEPWorkflowError("EP confirmation lost anatomy lineage")

    if mapping_result is not None:
        confirmation = deepcopy(confirmation)
        provenance = dict(confirmation.get("provenance") or {})
        if "hearttwin_parameter_mapping" in provenance:
            raise PatientEPWorkflowError(
                "EP backend supplied reserved hearttwin_parameter_mapping provenance"
            )
        provenance["hearttwin_parameter_mapping"] = mapping_result
        confirmation["provenance"] = provenance

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

    if measurement_provenance is not None:
        calibration = deepcopy(calibration)
        provenance = dict(calibration.get("provenance") or {})
        if "hearttwin_measurement_preparation" in provenance:
            raise PatientEPWorkflowError(
                "EP backend supplied reserved hearttwin_measurement_preparation provenance"
            )
        provenance["hearttwin_measurement_preparation"] = measurement_provenance
        calibration["provenance"] = provenance

    return PatientEPWorkflowResult(
        patient_id=case.patient_id,
        evidence_split=prepared.evidence_split,
        patient_qc=qc,
        anatomy_bundle=bundle.model_dump(mode="json"),
        calibration=calibration,
        confirmation=confirmation,
        holdout_observations=holdout_observations,
        scientific_grade=spec.scientific_grade,
        limitations=limitations,
    )
