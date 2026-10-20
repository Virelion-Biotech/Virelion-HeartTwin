"""Pinned nnU-Net model-folder inference; no implicit model or weight downloads."""

from __future__ import annotations

import hashlib
import importlib.metadata
import json
import os
import shutil
import subprocess
import tempfile
from pathlib import Path

import numpy as np

from .patient_geometry import write_artifact
from .patient_images import load_scalar_image
from .patient_observations import verified_bytes


def model_inventory(manifest):
    root = Path(manifest["model_directory"]).resolve()
    if not manifest.get("trusted_by") or not manifest.get("training_provenance") or not root.is_dir():
        raise ValueError("Reviewed model directory and explicit training provenance required")
    expected = manifest["files"]
    if not expected:
        raise ValueError("Complete model-file SHA-256 inventory required")
    observed = {}
    for path in root.rglob("*"):
        if path.is_symlink():
            raise ValueError("Model assets cannot contain symbolic links")
        if path.is_file():
            observed[str(path.relative_to(root))] = hashlib.sha256(path.read_bytes()).hexdigest()
    if observed != expected:
        raise ValueError("Model directory differs from its exact pinned inventory")
    return root


def segment_nnunet(payload):
    """Single-channel local inference using an explicitly reviewed installed model.

    The installed package version, every model byte, folds, checkpoint and device
    must be selected. Output is checked against the input physical grid and remains
    pending expert review. This adapter does not establish model accuracy.
    """
    import nibabel as nib

    model_ref = payload["model_manifest"]
    manifest = json.loads(verified_bytes(model_ref))
    model_root = model_inventory(manifest)
    if importlib.metadata.version("nnunetv2") != manifest["nnunet_version"]:
        raise ValueError("Installed nnU-Net version differs from the reviewed model manifest")
    executable = shutil.which("nnUNetv2_predict_from_modelfolder")
    if executable is None:
        raise ValueError("Install the explicitly reviewed nnU-Net package version on the trusted worker")
    image, affine, sources = load_scalar_image(payload)
    if payload["intensity_unit"] != manifest["intensity_unit"]:
        raise ValueError("Model and patient input intensity conventions differ")
    dataset = json.loads((model_root / "dataset.json").read_text())
    if dataset.get("file_ending") != ".nii.gz" or len(dataset.get("channel_names", {})) != 1:
        raise ValueError("Adapter requires a single-channel NIfTI nnU-Net model")
    folds = manifest["folds"]
    checkpoint = manifest["checkpoint"]
    if (not folds or len(set(folds)) != len(folds) or any(type(i) is not int or i < 0 for i in folds)
            or not isinstance(checkpoint, str) or Path(checkpoint).name != checkpoint
            or any(f"fold_{fold}/{checkpoint}" not in manifest["files"] for fold in folds)):
        raise ValueError("Explicit unique folds and an exactly pinned checkpoint filename required")
    device = payload["device"]
    step_size = payload["tile_step_size"]
    if device not in {"cpu", "cuda"} or not 0 < step_size <= 1 or type(payload["test_time_augmentation"]) is not bool:
        raise ValueError("Select device, tile step size and test-time augmentation explicitly")
    timeout = payload["timeout_s"]
    if type(timeout) is not int or not 1 <= timeout <= 7200:
        raise ValueError("Inference timeout must be 1..7200 seconds")
    centre = affine.copy()
    centre[:3, 3] += centre[:3, :3] @ np.full(3, 0.5)
    centre[:3] *= 1000
    if payload["coordinate_frame"] == "DICOM_LPS":
        centre = np.diag([-1., -1., 1., 1.]) @ centre
    elif payload["coordinate_frame"] != "NIFTI_RAS":
        raise ValueError("Learned inference needs an explicit DICOM_LPS or NIFTI_RAS physical frame")
    output_root = Path(payload["output_dir"]).resolve()
    output_root.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="nnunet-", dir=output_root) as temporary:
        work = Path(temporary)
        inputs, outputs = work / "input", work / "output"
        inputs.mkdir()
        outputs.mkdir()
        img = nib.Nifti1Image(image.astype(np.float32), centre)
        img.header.set_xyzt_units("mm")
        nib.save(img, inputs / "patient_0000.nii.gz")
        command = [executable, "-i", str(inputs), "-o", str(outputs), "-m", str(model_root),
                   "-f", *map(str, folds), "-chk", checkpoint, "-device", device,
                   "-step_size", str(step_size), "-npp", "1", "-nps", "1"]
        if not payload["test_time_augmentation"]:
            command.append("--disable_tta")
        # Positive allowlist: arbitrary worker credential variable names stay out.
        allowed = {"PATH", "HOME", "LANG", "LC_ALL", "TMPDIR", "TEMP", "TMP", "SYSTEMROOT",
                   "LD_LIBRARY_PATH", "CUDA_VISIBLE_DEVICES", "OMP_NUM_THREADS", "MKL_NUM_THREADS",
                   "OPENBLAS_NUM_THREADS", "nnUNet_compile"}
        env = {k: v for k, v in os.environ.items() if k in allowed}
        completed = subprocess.run(command, env=env, capture_output=True, timeout=timeout, check=False)
        if completed.returncode:
            raise ValueError("Pinned nnU-Net inference failed; no segmentation artifact admitted")
        predicted = nib.load(outputs / "patient.nii.gz")
        labels = np.asanyarray(predicted.dataobj)
        if (labels.shape != image.shape or not np.allclose(predicted.affine, centre, rtol=0, atol=1e-4)
                or not np.isfinite(labels).all() or not np.equal(labels, np.floor(labels)).all()
                or not set(np.unique(labels).tolist()) <= set(manifest["label_ids"])):
            raise ValueError("Segmentation output changed physical grid or contains undeclared labels")
        model_inventory(manifest)
        verified_bytes(model_ref)
        artifact = write_artifact(output_root, "segmentation-labels", {"labels": labels.astype(int).tolist(), "voxel_corner_affine_m": affine.tolist()})
    receipt = {"subject_id": payload["subject_id"], "coordinate_frame": payload["coordinate_frame"],
               "method": "nnunet-pinned-local-v1", "model_manifest": model_ref, "source_files": sources,
               "labels": artifact, "review_status": "pending", "nnunet_version": manifest["nnunet_version"],
               "folds": folds, "checkpoint": checkpoint, "device": device, "tile_step_size": step_size,
               "test_time_augmentation": payload["test_time_augmentation"],
               "qualification": "Inference adapter only; supplied trained model and anatomical output require independent qualification"}
    return {"labels": artifact, "segmentation": write_artifact(output_root, "segmentation", receipt)}
