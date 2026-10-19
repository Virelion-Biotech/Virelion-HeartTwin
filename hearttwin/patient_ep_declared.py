"""Scientific patient-EP entrypoint with declared openCARP experiment semantics.

The lower-level strict workflow remains backend/plumbing infrastructure. This
wrapper is the public scientific path: when high-fidelity confirmation uses the
package openCARP backend, a versioned experiment declaration is mandatory before
any solver call. The complete declaration is already part of the confirmation
request settings (and therefore its request hash); HeartTwin additionally
records a compact declaration envelope in confirmation provenance for reporting
and audit trails.
"""
from __future__ import annotations

from copy import deepcopy
from typing import Any

from .ep_parameter_mapping import EPParameterMappingSpec
from .patient_ep import PatientEPWorkflowResult
from .patient_ep_strict import run_strict_patient_ep_workflow as _run_core
from .patient_model import PatientCase, PatientEPWorkflowSpec
from .patient_opencarp_experiment import inspect_opencarp_experiment
from .service_registry import ServiceRegistry


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
    parameter_mapping: EPParameterMappingSpec | dict[str, Any] | None = None,
) -> PatientEPWorkflowResult:
    settings = deepcopy(confirmation_settings or {})
    declaration = None
    if spec.scientific_grade and spec.confirmation_backend == "opencarp-external-v1":
        raw_experiment = settings.get("opencarp_experiment_spec")
        declaration = inspect_opencarp_experiment(raw_experiment, workflow=spec)

    result = _run_core(
        registry,
        case=case,
        spec=spec,
        anatomy_bundle=anatomy_bundle,
        anatomy_request=anatomy_request,
        parameter_bounds=parameter_bounds,
        initial_parameters=initial_parameters,
        parameter_units=parameter_units,
        calibration_settings=calibration_settings,
        confirmation_settings=settings,
        parameter_mapping=parameter_mapping,
    )
    if declaration is None:
        return result

    confirmation = deepcopy(result.confirmation)
    provenance = dict(confirmation.get("provenance") or {})
    existing = provenance.get("hearttwin_opencarp_experiment")
    envelope = declaration.model_dump(mode="json")
    if existing is not None and existing != envelope:
        raise ValueError(
            "confirmation returned conflicting HeartTwin openCARP experiment provenance"
        )
    provenance["hearttwin_opencarp_experiment"] = envelope
    confirmation["provenance"] = provenance
    return result.model_copy(update={"confirmation": confirmation})
