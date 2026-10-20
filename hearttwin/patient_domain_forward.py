"""Fixed command adapter for CardiInfer's patient domain forward loop."""

from __future__ import annotations
import json
import os
import tempfile
from .config import load_registry
from .patient_domain import forward_payload, invoke
from .patient_observations import sample_prediction


def main():
    request = json.loads(os.environ["HEARTTWIN_PAYLOAD"])
    spec = request["context"]["patient_forward"]
    capability = spec["capability"]
    if capability not in {"mechanics.simulate", "flow.simulate", "patient.circulation.simulate", "patient.coupled.simulate"}:
        raise ValueError("Patient forward capability is unsupported")
    template = spec["template"]
    if template.get("subject_id") != request["subject_id"]:
        raise ValueError("Patient forward subject mismatch")
    payload = forward_payload(template, spec["parameter_paths"], request["parameters"])
    # Each evaluation gets its own artifacts; no cross-particle overwrite/races.
    with tempfile.TemporaryDirectory(prefix="hearttwin-forward-") as directory:
        if capability == "mechanics.simulate":
            payload.setdefault("settings", {})["output_dir"] = directory
        elif capability.startswith("patient."):
            payload["output_dir"] = directory
        output = invoke(load_registry(), capability, payload)
        if spec.get("sampling"):
            output["patient_sampled"] = {
                "t" + str(index): sample_prediction(output, target)
                for index, target in enumerate(spec["sampling"])
            }
        print(json.dumps(output, allow_nan=False))


if __name__ == "__main__":
    main()
