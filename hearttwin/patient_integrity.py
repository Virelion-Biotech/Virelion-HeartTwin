"""Source-byte and duplicate-content checks complement acquisition-ID split checks."""

from __future__ import annotations
import hashlib
from .patient_ecg_artifacts import _local_path


def verify_patient_sources(case, calibration_ids, validation_ids):
    evidence = {item.evidence_id: item for item in case.evidence}
    acquisitions = {item.acquisition_id: item for item in case.acquisitions}
    by_role = {"calibration": set(), "validation": set()}
    for role, ids in (("calibration", calibration_ids), ("validation", validation_ids)):
        for evidence_id in ids:
            item = evidence[evidence_id]
            if not item.acquisition_id:
                raise ValueError(
                    "Patient run evidence must identify its source acquisition"
                )
            acquisition = acquisitions[item.acquisition_id]
            if not acquisition.source_files:
                raise ValueError("Patient evidence acquisition has no source files")
            for source in acquisition.source_files:
                if source.sha256 is None:
                    raise ValueError("Patient run sources must be SHA-256 pinned")
                path = _local_path(source.uri)
                digest = hashlib.sha256()
                with path.open("rb") as handle:
                    for block in iter(lambda: handle.read(1024 * 1024), b""):
                        digest.update(block)
                if digest.hexdigest() != source.sha256.lower():
                    raise ValueError(
                        "Patient source byte integrity verification failed"
                    )
                by_role[role].add(source.sha256.lower())
    if by_role["calibration"] & by_role["validation"]:
        raise ValueError(
            "Identical source content is assigned to calibration and validation"
        )
