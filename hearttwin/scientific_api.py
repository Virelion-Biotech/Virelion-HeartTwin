"""Scientific HeartTwin facade with fail-closed patient EP semantics.

``VirelionServices`` remains the broad compatibility facade for the full stack.
This subclass is the intended entrypoint for patient-specific research runs: it
routes patient EP through the declared openCARP experiment gate before any
solver is touched, and preserves the strict confirmation-before-holdout ordering.
"""
from __future__ import annotations

from typing import Any

from .api import VirelionServices
from .patient_ecg_artifacts import evaluate_patient_ep_holdouts
from .patient_ep_declared import run_strict_patient_ep_workflow
from .patient_model import PatientCase, PatientEPWorkflowSpec


class ScientificVirelionServices(VirelionServices):
    """Virelion service facade for declared, auditable patient-specific science."""

    def run_patient_ep(
        self,
        case: PatientCase,
        spec: PatientEPWorkflowSpec,
        **kwargs: Any,
    ):
        """Run patient EP through the declared scientific experiment gate."""
        return run_strict_patient_ep_workflow(
            self.registry,
            case=case,
            spec=spec,
            **kwargs,
        )

    def run_patient_ep_with_holdout_evaluation(
        self,
        case: PatientCase,
        spec: PatientEPWorkflowSpec,
        *,
        evaluation_spec: Any,
        **kwargs: Any,
    ) -> dict[str, Any]:
        """Run declared patient EP, then read/evaluate the immutable holdout artifact."""
        result = run_strict_patient_ep_workflow(
            self.registry,
            case=case,
            spec=spec,
            **kwargs,
        )
        reports = evaluate_patient_ep_holdouts(result, spec=evaluation_spec)
        return {
            "workflow": result.model_dump(mode="json"),
            "validation": [item.model_dump(mode="json") for item in reports],
        }
