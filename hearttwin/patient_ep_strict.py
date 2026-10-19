"""Strict patient-EP entrypoint layered over the core HeartTwin workflow.

The core patient EP module is intentionally backend-neutral and predates
CardiAnatomy's stricter ``patient_ep`` readiness target. This wrapper makes the
new patient-evidence gate mandatory without duplicating calibration or
confirmation logic.
"""
from __future__ import annotations

from copy import deepcopy
from typing import Any

from .contracts import AnatomyBundlePayload
from .patient_ep import PatientEPWorkflowError, PatientEPWorkflowResult, run_patient_ep_workflow
from .patient_model import PatientCase, PatientEPWorkflowSpec
from .service_registry import ServiceRegistry


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


def _require_safe_parameter_replay(spec: PatientEPWorkflowSpec) -> None:
    """Refuse implicit parameter transfer between heterogeneous EP backends.

    The core plumbing workflow can replay one backend's calibrated parameter
    dictionary into a confirmation run. That is acceptable only when both
    stages use the same backend/parameter semantics. Cross-backend use requires
    an explicit, versioned parameter-mapping contract with unit conversions and
    declared unmapped parameters; HeartTwin does not infer such semantics from
    matching parameter names.
    """
    if spec.fast_backend != spec.confirmation_backend:
        raise PatientEPWorkflowError(
            "Strict patient EP refuses implicit cross-backend parameter replay. "
            "An explicit parameter-mapping contract is required before calibrated "
            f"parameters may be transferred from {spec.fast_backend!r} to "
            f"{spec.confirmation_backend!r}."
        )


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
    calibration_settings: dict[str, Any] | None = None,
    confirmation_settings: dict[str, Any] | None = None,
) -> PatientEPWorkflowResult:
    """Require strict patient anatomy readiness before EP calibration.

    Exactly one anatomy source is accepted. When raw/staged anatomy input is
    supplied, reconstruction is executed first and its resulting bundle must
    pass CardiAnatomy's ``patient_ep`` gate before any ECG calibration request
    is constructed. Cross-backend parameter replay is rejected until an
    explicit parameter-mapping contract is supplied by a future workflow.
    """
    if (anatomy_bundle is None) == (anatomy_request is None):
        raise PatientEPWorkflowError(
            "Provide exactly one of anatomy_bundle or anatomy_request"
        )

    # Enforce this before any external service invocation so an unsupported
    # scientific workflow cannot consume patient data or compute resources.
    _require_safe_parameter_replay(spec)

    if anatomy_request is not None:
        request = deepcopy(anatomy_request)
        declared = request.get("subject_id")
        if declared is not None and str(declared) != case.patient_id:
            raise PatientEPWorkflowError("Anatomy request subject does not match patient")
        request["subject_id"] = case.patient_id
        built = _invoke(registry, "anatomy.build", request)
        bundle = AnatomyBundlePayload.model_validate(built)
    else:
        bundle = AnatomyBundlePayload.model_validate(anatomy_bundle)

    bundle = _require_patient_ep_ready(registry, bundle)
    return run_patient_ep_workflow(
        registry,
        case=case,
        spec=spec,
        anatomy_bundle=bundle.model_dump(mode="json"),
        parameter_bounds=parameter_bounds,
        initial_parameters=initial_parameters,
        parameter_units=parameter_units,
        calibration_settings=calibration_settings,
        confirmation_settings=confirmation_settings,
    )
