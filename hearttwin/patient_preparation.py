"""Native patient preparation capabilities and JSON CLI."""

from __future__ import annotations

import argparse
import json
from pathlib import Path


def invoke(capability, payload):
    if capability == "patient.coupled.simulate":
        from .patient_coupled import simulate_coupled
        return simulate_coupled(payload)
    if capability == "patient.images.segment":
        from .patient_images import segment_seeded
        return segment_seeded(payload)
    if capability == "patient.images.nnunet":
        from .patient_segmentation import segment_nnunet
        return segment_nnunet(payload)
    if capability == "patient.circulation.simulate":
        from .patient_closed_loop import simulate_closed_loop
        return simulate_closed_loop(payload)
    if capability == "patient.geometry.reconstruct":
        from .patient_geometry import reconstruct_labels
        return reconstruct_labels(payload)
    if capability == "patient.bsp.evaluate":
        from .patient_bsp import evaluate_bsp_holdout
        from .patient_ecg_eval import ECGEvaluationSpec
        return evaluate_bsp_holdout(
            payload["prediction"], payload["observation"],
            spec=ECGEvaluationSpec.model_validate(payload["protocol"])).model_dump(mode="json")
    if capability == "patient.leadfield.generate":
        from .patient_volume_conductor import generate_leadfield
        return generate_leadfield(payload)
    if capability == "patient.spatial_ep.simulate":
        from .patient_spatial_ep import simulate_spatial_ep
        return simulate_spatial_ep(payload)
    raise ValueError("Unknown native patient preparation capability")


def main():
    parser = argparse.ArgumentParser(description="Native research image/lead-field/spatial model preparation")
    parser.add_argument("capability")
    parser.add_argument("request", type=Path)
    args = parser.parse_args()
    print(json.dumps(invoke(args.capability, json.loads(args.request.read_text())), indent=2, allow_nan=False))


if __name__ == "__main__":
    main()
