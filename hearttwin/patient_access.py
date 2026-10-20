"""Optional provisioned patient-workbench credentials.

Operators are trusted to run scientific subprocesses; this is not process or
filesystem isolation between hostile users. Scoped readers/reviewers cannot use
raw capability, upload or generic artifact endpoints.
"""

from __future__ import annotations
import hashlib
import hmac
from dataclasses import dataclass


@dataclass(frozen=True)
class PatientPrincipal:
    actor: str
    role: str
    patients: tuple[str, ...] = ()

    def permits(self, patient_id: str) -> bool:
        return self.role == "owner" or patient_id in self.patients

    def require(self, action: str, patient_id: str):
        roles = {
            "read": {"owner", "operator", "reader", "reviewer"},
            "write": {"owner", "operator"},
            "execute": {"owner", "operator"},
            "review": {"owner", "reviewer"},
            "archive": {"owner"},
        }
        if self.role not in roles[action] or not self.permits(patient_id):
            raise PermissionError("Patient access is not permitted")


class PatientAccess:
    def __init__(self, owner_token: str, credentials=()):
        self.owner_token = owner_token
        self.credentials = []
        for entry in credentials:
            if set(entry) != {"token_sha256", "actor", "role", "patients"}:
                raise ValueError(
                    "Credentials require token_sha256, actor, role and patients"
                )
            digest = entry["token_sha256"]
            if len(digest) != 64 or any(x not in "0123456789abcdef" for x in digest):
                raise ValueError(
                    "Credential token_sha256 must be a lowercase SHA-256 digest"
                )
            if (
                entry["role"] not in {"operator", "reviewer", "reader"}
                or not entry["actor"].strip()
            ):
                raise ValueError("Invalid patient credential role or actor")
            if not isinstance(entry["patients"], list) or not entry["patients"]:
                raise ValueError(
                    "Patient credentials require explicit patient assignments"
                )
            from .patient_store import identifier

            patients = tuple(identifier(x) for x in entry["patients"])
            if any(
                digest == old[0] or entry["actor"] == old[1].actor
                for old in self.credentials
            ):
                raise ValueError("Credential actors and token digests must be unique")
            self.credentials.append(
                (digest, PatientPrincipal(entry["actor"], entry["role"], patients))
            )

    def authenticate(self, supplied):
        if not isinstance(supplied, str) or not supplied.startswith("Bearer "):
            return None
        token = supplied.removeprefix("Bearer ")
        if hmac.compare_digest(token, self.owner_token):
            return PatientPrincipal("owner", "owner")
        digest = hashlib.sha256(token.encode()).hexdigest()
        for expected, principal in self.credentials:
            if hmac.compare_digest(digest, expected):
                return principal
        return None
