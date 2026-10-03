"""Explicit ElectroTrace -> CardiInfer -> CardiEP calibration workflow."""
from __future__ import annotations

from typing import Any, Mapping, Sequence

from .contracts import Observation
from .provenance import sha256
from .service_registry import ServiceRegistry


class EPCalibrationWorkflowError(RuntimeError):
    """Raised when a required EP-calibration boundary is unavailable or invalid."""


def prepare_ep_inference_problem(
    registry: ServiceRegistry,
    *,
    entity_id: str,
    electrical_observation: Observation,
    anatomy_ref: Mapping[str, Any],
    priors: Sequence[Mapping[str, Any]],
    inference_backend: str,
    ep_backend: str,
    ep_settings: Mapping[str, Any] | None = None,
    fixed_parameters: Mapping[str, float] | None = None,
    sampler_settings: Mapping[str, Any] | None = None,
    seed: int | None = None,
) -> dict[str, Any]:
    """Prepare a typed CardiInfer request from a measured electrical observation."""
    if electrical_observation.modality != "electrical":
        raise EPCalibrationWorkflowError("EP calibration requires an electrical observation")
    if not electrical_observation.values.get("input_path"):
        raise EPCalibrationWorkflowError(
            "Electrical calibration observation requires values.input_path"
        )

    electrotrace = registry.capability("electrical.prepare_calibration")
    if electrotrace is None:
        raise EPCalibrationWorkflowError(
            "electrical.prepare_calibration is not registered"
        )
    if not electrotrace.available():
        raise EPCalibrationWorkflowError(
            "ElectroTrace calibration capability is unavailable"
        )
    handoff = electrotrace.invoke(
        "electrical.prepare_calibration",
        {
            "entity_id": entity_id,
            "observations": [electrical_observation.model_dump(mode="json")],
        },
    )
    if not isinstance(handoff, dict):
        raise EPCalibrationWorkflowError(
            "ElectroTrace calibration capability returned a non-object payload"
        )
    handoff_entity = handoff.get("entity_id")
    if handoff_entity is not None and str(handoff_entity) != str(entity_id):
        raise EPCalibrationWorkflowError(
            f"ElectroTrace handoff entity_id {handoff_entity!r} does not match "
            f"requested entity_id {entity_id!r}"
        )

    try:
        from cardiep import observations_from_electrotrace
        from cardiinfer import ep_inference_request_from_electrotrace
    except Exception as exc:  # pragma: no cover - optional component boundary
        raise EPCalibrationWorkflowError(
            "CardiEP and CardiInfer must be installed for EP calibration"
        ) from exc

    # Validate the measurement against the CardiEP observation contract before
    # it is allowed to become a likelihood input.
    ep_observations = observations_from_electrotrace(handoff)
    if not ep_observations:
        raise EPCalibrationWorkflowError("ElectroTrace returned no EP observations")

    request = ep_inference_request_from_electrotrace(
        handoff,
        subject_id=entity_id,
        inference_backend=inference_backend,
        ep_backend=ep_backend,
        anatomy_ref=anatomy_ref,
        priors=priors,
        ep_settings=ep_settings,
        fixed_parameters=fixed_parameters,
        sampler_settings=sampler_settings,
        seed=seed,
    )
    request_json = request.model_dump(mode="json")
    return {
        "contract_version": "1.0",
        "entity_id": entity_id,
        "measurement_handoff": handoff,
        "ep_observations": [
            item.model_dump(mode="json") for item in ep_observations
        ],
        "inference_request": request_json,
        "problem_sha256": sha256(
            {
                "entity_id": entity_id,
                "measurement_handoff": handoff,
                "inference_request": request_json,
            }
        ),
    }


def run_ep_calibration(
    registry: ServiceRegistry,
    *,
    entity_id: str,
    electrical_observation: Observation,
    anatomy_ref: Mapping[str, Any],
    priors: Sequence[Mapping[str, Any]],
    inference_backend: str,
    ep_backend: str,
    ep_settings: Mapping[str, Any] | None = None,
    fixed_parameters: Mapping[str, float] | None = None,
    sampler_settings: Mapping[str, Any] | None = None,
    seed: int | None = None,
) -> dict[str, Any]:
    """Run the EP inverse problem through CardiInfer.

    The inference backend is responsible for evaluating CardiEP forward-model
    outputs against the likelihood terms.  If no backend is configured,
    CardiInfer intentionally fails closed.
    """
    problem = prepare_ep_inference_problem(
        registry,
        entity_id=entity_id,
        electrical_observation=electrical_observation,
        anatomy_ref=anatomy_ref,
        priors=priors,
        inference_backend=inference_backend,
        ep_backend=ep_backend,
        ep_settings=ep_settings,
        fixed_parameters=fixed_parameters,
        sampler_settings=sampler_settings,
        seed=seed,
    )
    infer = registry.capability("infer.run")
    if infer is None:
        raise EPCalibrationWorkflowError("infer.run is not registered")
    if not infer.available():
        raise EPCalibrationWorkflowError("CardiInfer is unavailable")
    try:
        result = infer.invoke("infer.run", problem["inference_request"])
    except Exception as exc:
        raise EPCalibrationWorkflowError(f"CardiInfer EP calibration failed: {exc}") from exc

    return {
        **problem,
        "inference_result": result,
        "result_sha256": sha256(result),
    }
