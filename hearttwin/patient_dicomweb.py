"""Owner-configured HTTPS DICOMweb retrieval with exact patient/series checks."""

from __future__ import annotations

import hashlib
import io
import json
import os
import re
import urllib.request
from email import policy
from email.parser import BytesParser
from pathlib import Path
from urllib.parse import urlparse

from .patient_observations import verified_bytes


def retrieve_dicom_instances(config, *, output_dir):
    """Retrieve explicitly selected SOP instances; never discover arbitrary patients.

    Called only by the trusted owner import path, not a patient-plan capability.
    No hospital is contacted until the owner configures it. No token/URL is stored
    in returned patient artifacts. Raw clinical files stay on the trusted worker.
    """
    import pydicom

    class NoRedirect(urllib.request.HTTPRedirectHandler):
        def redirect_request(self, req, fp, code, msg, headers, newurl):
            raise ValueError("DICOMweb redirects refused; configure the final endpoint")

    base = config["base_url"].rstrip("/")
    parsed = urlparse(base)
    if (parsed.scheme != "https" or not parsed.hostname or parsed.username or parsed.password or parsed.query
            or parsed.fragment or "%" in parsed.path or ".." in parsed.path.split("/")):
        raise ValueError("DICOMweb base must be a direct HTTPS endpoint without credentials/query/fragment")
    study, series, instances = config["study_instance_uid"], config["series_instance_uid"], config["sop_instance_uids"]
    if not 2 <= len(instances) <= 2000 or len(set(instances)) != len(instances):
        raise ValueError("Select 2..2000 distinct DICOM SOP instances explicitly")
    for uid in [study, series, *instances]:
        if not isinstance(uid, str) or len(uid) > 64 or not re.fullmatch(r"[0-9]+(?:\.[0-9]+)+", uid):
            raise ValueError("Invalid DICOM UID")
    if not config.get("dicom_patient_id"):
        raise ValueError("Explicit expected DICOM patient identity required")
    headers = {"Accept": "application/dicom; transfer-syntax=*"}
    if config.get("token_env"):
        token = os.environ[config["token_env"]]
        if not token or "\r" in token or "\n" in token:
            raise ValueError("Invalid worker DICOMweb token")
        headers["Authorization"] = "Bearer " + token
    opener = urllib.request.build_opener(NoRedirect())
    root = Path(output_dir).resolve()
    root.mkdir(parents=True, exist_ok=True)
    sources = []
    total = 0
    for index, uid in enumerate(instances):
        url = base + f"/studies/{study}/series/{series}/instances/{uid}"
        with opener.open(urllib.request.Request(url, headers=headers), timeout=60) as response:
            raw = response.read(50_000_001)
            content_type = response.headers.get("Content-Type", "")
        total += len(raw)
        if len(raw) > 50_000_000 or total > 1_000_000_000:
            raise ValueError("DICOMweb response exceeds per-instance/series limits")
        if content_type.lower().startswith("multipart/"):
            message = BytesParser(policy=policy.default).parsebytes(("Content-Type: " + content_type + "\r\nMIME-Version: 1.0\r\n\r\n").encode() + raw)
            parts = list(message.iter_parts())
            if len(parts) != 1 or parts[0].get_content_type() != "application/dicom":
                raise ValueError("Expected exactly one DICOM instance per WADO response")
            raw = parts[0].get_payload(decode=True)
        elif not content_type.lower().startswith("application/dicom"):
            raise ValueError("DICOMweb returned an unsupported content type")
        ds = pydicom.dcmread(io.BytesIO(raw), stop_before_pixels=True)
        if (str(ds.PatientID) != config["dicom_patient_id"] or str(ds.StudyInstanceUID) != study
                or str(ds.SeriesInstanceUID) != series or str(ds.SOPInstanceUID) != uid):
            raise ValueError("Downloaded DICOM belongs to a different patient/study/series/instance")
        digest = hashlib.sha256(raw).hexdigest()
        path = root / (digest + ".dcm")
        if path.exists() and path.read_bytes() != raw:
            raise ValueError("Pinned DICOM artifact is corrupt")
        path.write_bytes(raw)
        sources.append({"file_id": f"dicom-{index:05d}", "uri": path.as_uri(), "sha256": digest,
                        "media_type": "application/dicom", "metadata": {"transport": "owner-configured-https-dicomweb"}})
    return sources


def import_hospital_images(case, config, *, output_dir):
    """Attach a configured acquisition only after download and geometric QC finish."""
    from .patient_images import load_scalar_image
    from .patient_model import PatientCase

    case = PatientCase.model_validate(case).model_dump(mode="json")
    if config["encounter_id"] not in {e["encounter_id"] for e in case["encounters"]}:
        raise ValueError("Hospital images require an existing encounter")
    if config["acquisition_id"] in {a["acquisition_id"] for a in case["acquisitions"]}:
        raise ValueError("Acquisition ID already exists; choose a new immutable acquisition")
    if config["frame_id"] in {f["frame_id"] for f in case["coordinate_frames"]}:
        raise ValueError("Image frame ID already exists")
    sources = retrieve_dicom_instances(config, output_dir=output_dir)
    import pydicom
    actual_modality = pydicom.dcmread(io.BytesIO(verified_bytes(sources[0])), stop_before_pixels=True).Modality
    if {"ct": "CT", "mri": "MR"}.get(config["modality"]) != actual_modality:
        raise ValueError("Declared acquisition modality disagrees with downloaded DICOM")
    image, affine, _ = load_scalar_image({**config, "format": "dicom-series", "coordinate_frame": "DICOM_LPS", "sources": sources})
    case["coordinate_frames"].append({"frame_id": config["frame_id"], "convention": "DICOM_LPS", "length_unit": "m"})
    case["acquisitions"].append({"acquisition_id": config["acquisition_id"], "subject_id": case["patient_id"],
        "encounter_id": config["encounter_id"], "modality": config["modality"], "source_files": sources,
        "coordinate_frame_id": config["frame_id"], "units": {"intensity": config["intensity_unit"], "length": "m"},
        "metadata": {"voxel_shape": list(image.shape), "voxel_corner_affine_m": affine.tolist(), "geometry_checked": True}})
    if config["modality"] not in {"ct", "mri"}:
        raise ValueError("Declare CT or MRI acquisition modality")
    report = {"instance_count": len(sources), "geometry_checked": True, "acquisition_id": config["acquisition_id"]}
    # Source manifests deliberately omit hospital URLs, auth values and patient IDs.
    Path(output_dir).mkdir(parents=True, exist_ok=True)
    (Path(output_dir) / "retrieval-receipt.json").write_text(json.dumps(report, indent=2))
    return PatientCase.model_validate(case), report
