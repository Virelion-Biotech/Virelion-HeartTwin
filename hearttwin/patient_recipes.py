"""Assemble native patient tasks from named, reviewed case inputs; no silent defaults."""
from __future__ import annotations

from .patient_model import PatientCase


def circulation_recipe(case, encounter_id):
    case = PatientCase.model_validate(case)
    required = {
        "proximal_resistance": "mmHg*s/mL", "distal_resistance": "mmHg*s/mL",
        "compliance": "mL/mmHg", "distal_pressure_mmHg": "mmHg",
        "initial_capacitor_pressure": "mmHg", "inlet_flow": "mL/s",
    }
    inputs = {}
    for variable, unit in required.items():
        matches = [x for x in case.model_inputs if x.domain == "circulation" and x.variable == variable and x.status != "UNKNOWN"]
        if len(matches) != 1 or matches[0].unit != unit:
            raise ValueError(f"Provide exactly one circulation input {variable} in {unit}; declare measured, estimated, assumed or population provenance")
        inputs[variable] = {"$input": matches[0].input_id, "unit": unit}
    flow = inputs.pop("inlet_flow")
    template = {
        "subject_id": case.patient_id, "backend": "windkessel-3element-v1",
        "domain": {"domain_id": "patient-afterload", "region": "afterload",
                   "anatomy_ref": {"artifact_id": case.patient_id + "-case", "kind": "reduced_order_domain",
                                   "uri": "urn:hearttwin:case:" + case.patient_id}},
        "fluid": {"density": 1060, "dynamic_viscosity": 0.0035},
        "boundary_conditions": [{"boundary_id": "afterload", "kind": "windkessel", "region": "aorta",
            "parameters": {key: inputs[key] for key in ("proximal_resistance", "distal_resistance", "compliance", "distal_pressure_mmHg")}}],
        "settings": {"initial_capacitor_pressure": inputs["initial_capacitor_pressure"],
                     "inlet_flow": {**flow, "path": "values"}, "time_s": {**flow, "path": "time_s"}},
    }
    return {"encounter_id": encounter_id, "purpose": "baseline", "stages": [{
        "stage_id": "circulation", "capability": "flow.simulate", "template": template,
        "purpose": "Patient-input-bound three-element afterload simulation",
        "literal_rationale": {"fluid.density": "Required native contract field; not consumed by Windkessel",
                              "fluid.dynamic_viscosity": "Required native contract field; not consumed by Windkessel"}}]}
