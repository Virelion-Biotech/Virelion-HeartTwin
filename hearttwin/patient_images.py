"""Pinned scalar image loading and explicit seeded segmentation in physical coordinates."""

from __future__ import annotations

import gzip
import io
import json

import numpy as np

from .patient_geometry import write_artifact
from .patient_observations import verified_bytes


def load_scalar_image(payload):
    fmt = payload["format"]
    if fmt == "json-volume-v1":
        data = json.loads(verified_bytes(payload["source"]))
        image = np.asarray(data["values"], dtype=float)
        affine = np.asarray(data["voxel_corner_affine_m"], dtype=float)
        if data["intensity_unit"] != payload["intensity_unit"]:
            raise ValueError("Image intensity units do not match the declared units")
        sources = [payload["source"]]
    elif fmt == "nifti-scalar":
        import nibabel as nib

        raw = verified_bytes(payload["source"])
        img = nib.Nifti1Image.from_bytes(gzip.decompress(raw) if raw[:2] == b"\x1f\x8b" else raw)
        if img.header.get_xyzt_units()[0] != "mm" or payload["coordinate_frame"] != "NIFTI_RAS":
            raise ValueError("NIfTI requires declared mm units and the NIFTI_RAS frame")
        image = np.asarray(img.dataobj, dtype=float)
        affine = img.affine.copy()
        affine[:3, 3] -= affine[:3, :3] @ np.full(3, 0.5)
        affine[:3] *= 0.001
        sources = [payload["source"]]
    elif fmt == "dicom-series":
        image, affine = load_dicom_series(payload)
        sources = payload["sources"]
    else:
        raise ValueError("Supported raw formats: json-volume-v1, nifti-scalar, dicom-series")
    if image.ndim != 3 or not 1 <= image.size <= 50000000 or not np.isfinite(image).all():
        raise ValueError("A finite scalar 3D image within the voxel limit is required")
    if (affine.shape != (4, 4) or not np.isfinite(affine).all()
            or not np.allclose(affine[3], [0, 0, 0, 1]) or abs(np.linalg.det(affine[:3, :3])) < 1e-24):
        raise ValueError("Image requires a nonsingular physical affine in metres")
    return image, affine, sources


def load_dicom_series(payload):
    import pydicom

    if payload["coordinate_frame"] != "DICOM_LPS" or len(payload["sources"]) < 2:
        raise ValueError("DICOM series needs at least two physical slices and a DICOM_LPS frame")
    datasets = [pydicom.dcmread(io.BytesIO(verified_bytes(ref))) for ref in payload["sources"]]
    first = datasets[0]
    keys = ("PatientID", "SeriesInstanceUID", "StudyInstanceUID", "FrameOfReferenceUID", "Modality", "Rows", "Columns")
    if (str(first.PatientID) != payload["dicom_patient_id"] or str(first.SeriesInstanceUID) != payload["series_instance_uid"]
            or first.Modality not in {"CT", "MR"}):
        raise ValueError("Explicit DICOM patient/series identity and CT/MR modality required")
    orientation = np.asarray(first.ImageOrientationPatient, dtype=float)
    spacing = np.asarray(first.PixelSpacing, dtype=float)
    if (orientation.shape != (6,) or spacing.shape != (2,) or not np.isfinite(orientation).all()
            or not np.isfinite(spacing).all() or np.any(spacing <= 0)
            or not np.allclose([np.linalg.norm(orientation[:3]), np.linalg.norm(orientation[3:])], 1, atol=1e-6)
            or abs(orientation[:3] @ orientation[3:]) > 1e-6):
        raise ValueError("DICOM orientation must have orthonormal direction cosines and positive pixel spacing")
    normal = np.cross(orientation[:3], orientation[3:])
    slices, seen = [], set()
    for ds in datasets:
        if any(str(getattr(ds, key)) != str(getattr(first, key)) for key in keys):
            raise ValueError("DICOM slices mix patients, series, frames, modalities or dimensions")
        if (int(getattr(ds, "NumberOfFrames", 1)) != 1 or int(getattr(ds, "SamplesPerPixel", 1)) != 1
                or ds.PhotometricInterpretation not in {"MONOCHROME1", "MONOCHROME2"}
                or hasattr(ds, "RealWorldValueMappingSequence") or hasattr(ds, "ModalityLUTSequence")):
            raise ValueError("Only single-frame scalar DICOM without unsupported intensity LUTs is supported")
        if not np.allclose(ds.ImageOrientationPatient, orientation, atol=1e-6) or not np.allclose(ds.PixelSpacing, spacing, atol=1e-6):
            raise ValueError("DICOM slice orientation or pixel spacing changes within the series")
        uid = str(ds.SOPInstanceUID)
        if uid in seen:
            raise ValueError("Duplicate DICOM SOP instance")
        seen.add(uid)
        pos = np.asarray(ds.ImagePositionPatient, dtype=float)
        if pos.shape != (3,) or not np.isfinite(pos).all():
            raise ValueError("DICOM requires finite physical slice positions")
        if ds.Modality == "CT":
            if payload["intensity_unit"] != "HU" or getattr(ds, "RescaleType", "HU") != "HU":
                raise ValueError("CT segmentation requires HU rescale interpretation")
            if not hasattr(ds, "RescaleSlope") or not hasattr(ds, "RescaleIntercept"):
                raise ValueError("CT must declare its stored-pixel to HU rescale")
        elif payload["intensity_unit"] != "arbitrary":
            raise ValueError("MR intensities are explicitly arbitrary in this importer")
        slope, intercept = float(getattr(ds, "RescaleSlope", 1)), float(getattr(ds, "RescaleIntercept", 0))
        if not np.isfinite([slope, intercept]).all() or slope == 0:
            raise ValueError("Invalid DICOM intensity rescale")
        values = np.asarray(ds.pixel_array, dtype=float)
        if values.shape != (int(ds.Rows), int(ds.Columns)):
            raise ValueError("Decoded DICOM pixel dimensions disagree with metadata")
        slices.append((pos, values * slope + intercept))
    slices.sort(key=lambda item: float(item[0] @ normal))
    positions = np.asarray([p for p, _ in slices])
    delta = np.diff(positions, axis=0)
    step = delta.mean(axis=0)
    if step @ normal <= 1e-6 or not np.allclose(delta, step, rtol=1e-5, atol=1e-4):
        raise ValueError("DICOM slices overlap, have missing positions or nonuniform spacing; select one phase/series")
    affine = np.eye(4)
    # Array axes: column, row, slice. DICOM spacing order: row, column.
    affine[:3, 0] = orientation[:3] * spacing[1]
    affine[:3, 1] = orientation[3:] * spacing[0]
    affine[:3, 2] = step
    affine[:3, 3] = positions[0] - affine[:3, :3] @ np.full(3, 0.5)
    affine[:3] *= 0.001
    return np.stack([v.T for _, v in slices], axis=2), affine


def segment_seeded(payload):
    """Connected threshold regions conditional on explicit patient seeds and ROI.

    This provides reproducible raw-image processing, not anatomical recognition.
    Labels remain pending review before reconstruction admits them.
    """
    from scipy.ndimage import generate_binary_structure, label

    image, affine, sources = load_scalar_image(payload)
    result = np.zeros(image.shape, dtype=np.int32)
    labels_seen, report = set(), []
    if not payload["regions"]:
        raise ValueError("Explicit patient segmentation seeds, thresholds and ROIs required")
    for region in payload["regions"]:
        identifier = region["label"]
        if type(identifier) is not int or not 0 < identifier < 2**31 or identifier in labels_seen or not region["seeds"]:
            raise ValueError("Distinct positive labels and nonempty seed lists required")
        labels_seen.add(identifier)
        bounds = np.asarray(region["roi_bounds"])
        if (bounds.shape != (3, 2) or bounds.dtype.kind not in "iu" or np.any(bounds[:, 0] < 0)
                or np.any(bounds[:, 1] > image.shape) or np.any(bounds[:, 0] >= bounds[:, 1])):
            raise ValueError("ROI bounds must be explicit start-inclusive/stop-exclusive voxel indices")
        low, high = region["threshold_min"], region["threshold_max"]
        if not np.isfinite([low, high]).all() or low > high:
            raise ValueError("Finite ordered intensity thresholds required")
        roi = np.zeros(image.shape, dtype=bool)
        roi[tuple(slice(a, b) for a, b in bounds)] = True
        components, _ = label(roi & (image >= low) & (image <= high), generate_binary_structure(3, 1))
        selected = set()
        for seed in region["seeds"]:
            if (not isinstance(seed, list) or len(seed) != 3
                    or any(type(i) is not int or not 0 <= i < image.shape[a] for a, i in enumerate(seed))):
                raise ValueError("Seed voxel index out of range")
            component = int(components[tuple(seed)])
            if component == 0:
                raise ValueError("Seed lies outside its ROI or intensity thresholds")
            selected.add(component)
        mask = np.isin(components, list(selected))
        if np.any(mask & (result != 0)):
            raise ValueError("Selected anatomical regions overlap; revise seeds/thresholds/ROIs")
        result[mask] = identifier
        report.append({"label": identifier, "voxels": int(mask.sum()), "volume_m3": float(mask.sum() * abs(np.linalg.det(affine[:3, :3])))})
    artifact = write_artifact(payload["output_dir"], "segmentation-labels", {"labels": result.tolist(), "voxel_corner_affine_m": affine.tolist()})
    receipt = {"subject_id": payload["subject_id"], "coordinate_frame": payload["coordinate_frame"],
               "method": "seeded-connected-threshold-v1", "source_files": sources, "labels": artifact,
               "intensity_unit": payload["intensity_unit"], "regions": payload["regions"], "measurements": report,
               "review_status": "pending", "limitations": "Seeds, thresholds and ROIs require expert selection; no automatic anatomical inference or clinical accuracy claim"}
    return {"labels": artifact, "segmentation": write_artifact(payload["output_dir"], "segmentation", receipt)}
