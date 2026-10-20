"""Import a selected patient observation without discarding original source provenance."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path

from .patient_model import PatientCase, SourceFileRef
from .patient_observations import normalize_observation, verified_bytes


def import_observation(case, source, spec, *, output_dir):
    case = PatientCase.model_validate(case).model_dump(mode="json")
    source = SourceFileRef.model_validate(source)
    if spec["encounter_id"] not in {e["encounter_id"] for e in case["encounters"]}:
        raise ValueError("Observation requires an existing patient encounter")
    if spec["role"] not in {"context", "calibration", "validation", "excluded"}:
        raise ValueError("Declare an explicit observation evidence role")
    observation = normalize_observation(verified_bytes(source), format=spec["format"], unit=spec["unit"])
    canonical = observation
    if len(observation["values"]) == 1 and "time_s" not in observation and "labels" not in observation:
        canonical = {"value": observation["values"][0], "unit": observation["unit"]}
    raw = json.dumps(canonical, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()
    digest = hashlib.sha256(raw).hexdigest()
    root = Path(output_dir).resolve()
    root.mkdir(parents=True, exist_ok=True)
    path = root / (digest + ".json")
    if not path.exists():
        path.write_bytes(raw)
    elif path.read_bytes() != raw:
        raise ValueError("Normalized observation artifact is corrupt")
    aid, eid = spec["acquisition_id"], spec["evidence_id"]
    normalized = {"file_id": aid + "-normalized", "uri": path.as_uri(), "sha256": digest,
                  "media_type": "application/json", "metadata": {"derived_from_sha256": source.sha256,
                  "normalization": "explicit-json-csv-v1", "unit_conversion": "none"}}
    case["acquisitions"].append({"acquisition_id": aid, "subject_id": case["patient_id"],
           "encounter_id": spec["encounter_id"], "modality": spec["modality"],
           "acquired_at": spec.get("acquired_at"), "source_files": [source.model_dump(mode="json"), normalized],
           "units": {"measurement": spec["unit"]}, "metadata": {"variable": spec["variable"]}})
    case["evidence"].append({"evidence_id": eid, "subject_id": case["patient_id"],
          "acquisition_id": aid, "encounter_id": spec["encounter_id"], "role": spec["role"],
          "data_kind": spec["variable"], "withheld_from_fit": spec["role"] == "validation"})
    if spec.get("input_id"):
        if spec["role"] in {"validation", "excluded"}:
            raise ValueError("Held-out/excluded observations cannot become forward model inputs")
        values = observation["values"]
        value = values[0] if len(values) == 1 and "time_s" not in observation and "labels" not in observation else observation
        case["model_inputs"].append({"input_id": spec["input_id"], "domain": spec["domain"],
             "variable": spec["variable"], "value": value, "unit": spec["unit"], "status": "MEASURED",
             "source_acquisition_ids": [aid], "method": "Explicit normalized observation import; no inferred clinical meaning"})
    return PatientCase.model_validate(case), normalized
