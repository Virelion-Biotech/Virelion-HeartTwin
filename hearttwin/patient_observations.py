"""Strict patient scalar/curve import and declared prediction sampling.

No guessed units, time alignment, coordinate registration or clinical interpretation.
"""

from __future__ import annotations

import csv
import hashlib
import io
import json
import math
from bisect import bisect_left
from pathlib import Path
from urllib.parse import unquote, urlparse

from pydantic import BaseModel, ConfigDict, Field, model_validator


class PatientObservation(BaseModel):
    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)
    values: list[float] = Field(min_length=1, max_length=100000)
    unit: str = Field(min_length=1)
    time_s: list[float] | None = None
    labels: list[str] | None = None

    @model_validator(mode="after")
    def alignment(self):
        if self.time_s is not None:
            if len(self.time_s) != len(self.values) or len(self.time_s) < 2:
                raise ValueError("A curve requires at least two aligned time samples")
            if any(a >= b for a, b in zip(self.time_s, self.time_s[1:])):
                raise ValueError("Observation time_s must be strictly increasing")
        if self.labels is not None and (
            len(self.labels) != len(self.values)
            or len(set(self.labels)) != len(self.labels)
        ):
            raise ValueError("Field observations require unique aligned labels")
        if self.labels is not None and self.time_s is not None:
            raise ValueError("Declare either time samples or spatial labels")
        return self


def finite_values(values):
    if not isinstance(values, list) or any(
        isinstance(v, bool) or not isinstance(v, (int, float)) or not math.isfinite(v)
        for v in values
    ):
        raise ValueError("Observation values must be finite JSON numbers")
    return values


def normalize_observation(raw: bytes, *, format: str, unit: str):
    """CSV uses exact time_s,value columns; JSON scalar or explicit curve/field."""
    if format == "csv":
        reader = csv.DictReader(io.StringIO(raw.decode("utf-8-sig")))
        if reader.fieldnames != ["time_s", "value"]:
            raise ValueError(
                "CSV header must be exactly time_s,value; time must be seconds"
            )
        rows = list(reader)
        if any(set(row) != {"time_s", "value"} or None in row.values() for row in rows):
            raise ValueError("Malformed observation CSV")
        item = {
            "time_s": [float(r["time_s"]) for r in rows],
            "values": [float(r["value"]) for r in rows],
            "unit": unit,
        }
    elif format == "json":

        def pairs(items):
            result = {}
            for key, value in items:
                if key in result:
                    raise ValueError("Duplicate observation JSON key")
                result[key] = value
            return result

        item = json.loads(raw, object_pairs_hook=pairs)
        if isinstance(item, dict) and set(item) == {"value", "unit"}:
            item = {"values": [item["value"]], "unit": item["unit"]}
        if not isinstance(item, dict) or item.get("unit") != unit:
            raise ValueError("Observation must contain explicitly matching units")
        finite_values(item.get("values"))
        if item.get("time_s") is not None:
            finite_values(item["time_s"])
    else:
        raise ValueError("Supported observation imports are JSON and CSV")
    observation = PatientObservation.model_validate(item)
    return observation.model_dump(mode="json", exclude_none=True)


def verified_bytes(source):
    parsed = urlparse(source.uri)
    if parsed.scheme not in {"", "file"} or parsed.netloc not in {"", "localhost"}:
        raise ValueError("Patient sources must be local/file artifacts")
    path = Path(unquote(parsed.path) if parsed.scheme else source.uri)
    if not source.sha256:
        raise ValueError("Patient sources require a SHA-256 pin")
    raw = path.read_bytes()
    if hashlib.sha256(raw).hexdigest() != source.sha256:
        raise ValueError("Patient source failed SHA-256 verification")
    return raw


def read_observation(source, unit):
    return PatientObservation.model_validate(
        normalize_observation(verified_bytes(source), format="json", unit=unit)
    )


def sample_prediction(prediction, spec):
    """No extrapolation, phase shift, rescaling or data-dependent alignment."""
    from .patient_domain import value_at

    if spec.get("field_quantity"):
        return sample_spatial_artifact(prediction, spec)
    value = value_at(prediction, spec["model_output"])
    values = finite_values(value if isinstance(value, list) else [value])
    actual_unit = prediction.get("units", {}).get(spec["model_output"].split(".")[-1])
    # Some native mechanics contracts omit a unit map; caller must explicitly declare it.
    declared = spec.get("prediction_unit") or actual_unit
    if declared != spec["unit"] or (
        actual_unit is not None and actual_unit != declared
    ):
        raise ValueError(
            "Prediction units must be explicit and match observation units"
        )
    if spec.get("time_s") is not None:
        times = finite_values(value_at(prediction, spec["model_time"]))
        if (
            len(times) != len(values)
            or len(times) < 2
            or any(a >= b for a, b in zip(times, times[1:]))
        ):
            raise ValueError(
                "Prediction time axis is not aligned and strictly increasing"
            )
        sampled = []
        for t in spec["time_s"]:
            if t < times[0] or t > times[-1]:
                raise ValueError(
                    "Observation is outside prediction time support; extrapolation forbidden"
                )
            j = bisect_left(times, t)
            if j == 0 or times[j] == t:
                sampled.append(values[j])
            else:
                f = (t - times[j - 1]) / (times[j] - times[j - 1])
                sampled.append(values[j - 1] + f * (values[j] - values[j - 1]))
        return sampled
    if spec.get("labels") is not None:
        labels = value_at(prediction, spec["model_labels"])
        if (
            not isinstance(labels, list)
            or len(labels) != len(values)
            or len(set(labels)) != len(labels)
        ):
            raise ValueError("Prediction requires unique aligned spatial labels")
        lookup = dict(zip(labels, values))
        if any(label not in lookup for label in spec["labels"]):
            raise ValueError(
                "Observation labels lack explicit prediction correspondence"
            )
        return [lookup[label] for label in spec["labels"]]
    return values


def sample_spatial_artifact(prediction, spec):
    """Select explicit native FEM tensor components on a hash-pinned mesh index space."""
    from .patient_model import SourceFileRef

    quantity = spec["field_quantity"]
    names = {
        "displacement_x": ("displacement", 0, None, "m", "node"),
        "displacement_y": ("displacement", 1, None, "m", "node"),
        "displacement_z": ("displacement", 2, None, "m", "node"),
    }
    for name, field, unit in [
        ("green_strain", "green_strain", "1"),
        ("cauchy_stress", "cauchy_stress", "Pa"),
    ]:
        for component, i, j in [
            ("xx", 0, 0),
            ("yy", 1, 1),
            ("zz", 2, 2),
            ("xy", 0, 1),
            ("xz", 0, 2),
            ("yz", 1, 2),
        ]:
            names[name + "_" + component] = (field, i, j, unit, "element")
    if quantity not in names or not spec.get("field_anatomy_sha256"):
        raise ValueError(
            "Declare a supported native FEM component and pinned anatomy hash"
        )
    artifacts = [
        x
        for x in prediction.get("outputs", [])
        if x.get("kind") == "spatial_mechanics_fields"
    ]
    if len(artifacts) != 1:
        raise ValueError(
            "Field target requires exactly one native spatial mechanics artifact"
        )
    artifact = artifacts[0]
    source = SourceFileRef(
        file_id=artifact["artifact_id"],
        uri=artifact["uri"],
        sha256=artifact.get("sha256"),
    )
    payload = json.loads(verified_bytes(source))
    if (
        payload.get("subject_id") != prediction.get("subject_id")
        or payload.get("anatomy_sha256") != spec["field_anatomy_sha256"]
    ):
        raise ValueError("Spatial field subject or mesh correspondence mismatch")
    field, i, j, unit, prefix = names[quantity]
    if unit != spec["unit"]:
        raise ValueError(
            "Spatial component units differ from declared observation units"
        )
    values = [row[i] if j is None else row[i][j] for row in payload[field]]
    labels = [prefix + ":" + str(index) for index in range(len(values))]
    return sample_prediction(
        {"field": values, "ids": labels, "units": {"field": unit}},
        {
            "model_output": "field",
            "model_labels": "ids",
            "unit": unit,
            "labels": spec["labels"],
        },
    )
