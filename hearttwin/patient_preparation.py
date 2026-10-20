"""Native patient preparation capabilities and JSON CLI."""

from __future__ import annotations

import argparse
import json
from pathlib import Path


def invoke(capability, payload):
    if capability == "patient.geometry.reconstruct":
        from .patient_geometry import reconstruct_labels
        return reconstruct_labels(payload)
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
