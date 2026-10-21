# Explicit BSP observations in the native electrical workflow

HeartTwin's native reciprocal conductor can now produce arbitrary **reviewed, explicitly referenced electrode observations** as well as its existing standard 12-lead output. Native propagation carries these channels as BSP signals, and patient.bsp.evaluate evaluates a frozen prediction against an immutable, explicitly withheld observation.

This completes a software contract previously missing for 128-channel BSP recordings. It does not qualify the EDGAR exports, create missing channels, infer their units/reference, or replace clinical validation.

## Reviewed electrode artifact
The existing subject ID, mesh SHA-256, coordinate frame, reviewer and electrode node mapping still apply. Optional observations selects the new schema:

```json
{
  "subject_id": "synthetic-subject",
  "mesh_sha256": "<exact conductor hash>",
  "coordinate_frame": "MODEL",
  "reviewer": "qualified-reviewer",
  "node_ids": {"B001": 12, "B002": 18, "CMS": 20},
  "observations": {
    "channel_names": ["B001-CMS", "B002-CMS"],
    "weights": [{"B001": 1.0, "CMS": -1.0}, {"B002": 1.0, "CMS": -1.0}],
    "reference": "recorded-CMS",
    "unit": "V"
  }
}
```

Node IDs are already canonical zero-based indices into the reviewed tetrahedral conductor. The example numbers are illustrative, not an EDGAR mapping. Each weight row must name recorded electrodes, contain finite nonboolean values, be nonzero, sum to zero within numerical tolerance and have L1 norm at most two. The generator supports 1..512 unique channels, 2..1024 distinct boundary electrodes and at most ten million operator entries. Channel labels and reference labels are explicit declarations, not proof that an acquisition used that convention.

Balanced weights ensure that changing the numerical potential gauge does not change the measured difference. Bipolar, CMS and declared common-average combinations can be represented without relabelling them as WCT or manufacturing V1–V6. Channel/element batching bounds the temporary reciprocal contraction. Conductivity, geometry and exact heart-to-torso correspondence checks remain enforced.

Without observations, the legacy artifact and standard nine-electrode/12-lead convention remain supported. Generalized artifacts use hearttwin-reciprocal-p1-v2; native propagation emits bsp_V, lead_names, measurement_reference, signal_kind and signal_unit. It does not emit an ecg_V field for generalized BSP.

## Immutable observation and evaluation
A measured observation artifact uses schema hearttwin-bsp-observation-v1, matching subject_id, coordinate_frame, measurement_reference and lead_names, with explicit time_s, values_V (time-by-channel), signal_unit=V, evidence_id and withheld_from_fit=true. Time must be finite, increasing, uniform, start at zero and match the prediction. No silent resampling, time shift, channel permutation, voltage conversion or reference conversion is performed.

The evaluation protocol must explicitly set require_standard_12_lead=false, list the same channel order and use zero fixed prediction offset. It reuses the established waveform metrics and optional predeclared acceptance criteria. No universal clinical threshold is added.

```python
from hearttwin.config import load_registry

report = load_registry().capability("patient.bsp.evaluate").invoke(
    "patient.bsp.evaluate",
    {"prediction": prediction_ref,
     "observation": measured_ref,
     "protocol": {"expected_leads": channel_names,
                  "require_standard_12_lead": False}})
```

The same capability is available through:
```sh
hearttwin-patient-prepare patient.bsp.evaluate request.json
```

Both artifacts require valid SHA-256 references. The held-out flag is an explicit assertion; this lower-level comparison cannot prove that a recording was never viewed or used for fitting. Full PatientCase independent-source qualification remains necessary for a scientific workflow claim. The separate CardiEP/openCARP standard-ECG confirmation bridge is unchanged.

## Verification and remaining qualification
Synthetic tests include a 128-channel anisotropic conductor checked against an independent forward linear-system solve, gauge invariance, uniform-voltage null response, explicit common-average re-referencing and channel permutation, command-line serialization, propagation, registry evaluation and rejection of convention/hash mismatches. The legacy standard-ECG manufactured tests also run.

The native propagation model remains a declared phenomenological cubic excitation/recovery model, not a validated human ion-channel model. Synthetic self-comparison tests verify serialization and metric contracts; they do not demonstrate predictive accuracy. No patient data are used by these tests.

EDGAR still requires authoritative export rate/unit/reference metadata, electrode index/order qualification, reviewed geometry and a compatible tetrahedral conductor. The new API supports explicitly supplied BSP observations once those inputs are qualified; it does not make the current public exports solve-ready.
