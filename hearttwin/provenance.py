from __future__ import annotations

import hashlib
import json
import math
from typing import Any


_NONFINITE_TAG = "__hearttwin_nonfinite_float__"


def _canonicalize(value: Any) -> Any:
    if isinstance(value, float):
        if math.isnan(value):
            return {_NONFINITE_TAG: "nan"}
        if math.isinf(value):
            return {_NONFINITE_TAG: "inf" if value > 0 else "-inf"}
        return value
    if isinstance(value, dict):
        return {str(key): _canonicalize(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_canonicalize(item) for item in value]
    return value


def canonical_json(value: Any) -> bytes:
    return json.dumps(
        _canonicalize(value),
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
        default=str,
    ).encode("utf-8")


def sha256(value: Any) -> str:
    return hashlib.sha256(canonical_json(value)).hexdigest()


def new_run_id(entity_id: str, payload: Any) -> str:
    return f"ht-{sha256({'entity_id': entity_id, 'payload': payload})[:16]}"
