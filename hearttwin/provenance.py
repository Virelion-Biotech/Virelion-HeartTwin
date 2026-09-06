from __future__ import annotations
import hashlib, json
from typing import Any

def canonical_json(value: Any) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), default=str).encode()

def sha256(value: Any) -> str:
    return hashlib.sha256(canonical_json(value)).hexdigest()

def new_run_id(entity_id: str, payload: Any) -> str:
    return f"ht-{sha256({'entity_id': entity_id, 'payload': payload})[:16]}"
