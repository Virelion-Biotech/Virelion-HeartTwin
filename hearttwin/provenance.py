from __future__ import annotations

import hashlib
import json
import math
from collections.abc import Mapping, Sequence
from typing import Any

_NONFINITE_PREFIX = "\u0000hearttwin:nonfinite:"
_ESCAPED_STRING_PREFIX = "\u0000hearttwin:string:"


def _canonicalize(value: Any) -> Any:
    if isinstance(value, float):
        if math.isnan(value):
            return _NONFINITE_PREFIX + "nan"
        if math.isinf(value):
            return _NONFINITE_PREFIX + (
                "positive_infinity" if value > 0 else "negative_infinity"
            )
        return value
    if isinstance(value, str) and (
        value.startswith(_NONFINITE_PREFIX)
        or value.startswith(_ESCAPED_STRING_PREFIX)
    ):
        return _ESCAPED_STRING_PREFIX + value
    if isinstance(value, Mapping):
        return {str(key): _canonicalize(item) for key, item in value.items()}
    if isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray)):
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
