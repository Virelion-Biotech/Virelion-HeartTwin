"""Synthetic raw-image geometry, segmentation contracts and hospital boundaries."""

import hashlib
import importlib.util
import io
import json
from pathlib import Path
from types import SimpleNamespace
from urllib.parse import unquote, urlparse

import numpy as np
import pytest

from hearttwin.patient_dicomweb import retrieve_dicom_instances
from hearttwin.patient_geometry import write_artifact
from hearttwin.patient_images import load_scalar_image, segment_seeded
from hearttwin.patient_segmentation import segment_nnunet

pytestmark = pytest.mark.skipif(any(importlib.util.find_spec(m) is None for m in ["scipy", "nibabel", "pydicom"]), reason="patient-native extras required")


def read(ref):
    return json.loads(Path(unquote(urlparse(ref["uri"]).path)).read_bytes())


def raw_image_request(tmp_path):
    image = np.zeros((5, 5, 5))
    image[0:2, 0:2, 0:2] = 100
    image[3:5, 3:5, 3:5] = 100
    source = write_artifact(tmp_path, "raw-volume", {"values": image.tolist(), "intensity_unit": "HU", "voxel_corner_affine_m": np.diag([0.001, 0.001, 0.001, 1.]).tolist()})
    return {"subject_id": "P1", "source": source, "format": "json-volume-v1", "intensity_unit": "HU", "coordinate_frame": "DICOM_LPS", "output_dir": str(tmp_path),
            "regions": [{"label": 1, "threshold_min": 90., "threshold_max": 110., "seeds": [[0, 0, 0]], "roi_bounds": [[0, 5], [0, 5], [0, 5]]}]}


def dicom_bytes(z, instance, *, patient="HOSPITAL-P1"):
    from pydicom.dataset import FileDataset, FileMetaDataset
    from pydicom.uid import CTImageStorage, ExplicitVRLittleEndian

    meta = FileMetaDataset()
    meta.MediaStorageSOPClassUID = CTImageStorage
    meta.MediaStorageSOPInstanceUID = instance
    meta.TransferSyntaxUID = ExplicitVRLittleEndian
    ds = FileDataset(None, {}, file_meta=meta, preamble=b"\0" * 128)
    ds.SOPClassUID = CTImageStorage
    ds.SOPInstanceUID = instance
    ds.PatientID = patient
    ds.StudyInstanceUID = "1.2.3"
    ds.SeriesInstanceUID = "1.2.3.4"
    ds.FrameOfReferenceUID = "1.2.5"
    ds.Modality = "CT"
    ds.Rows, ds.Columns = 2, 3
    ds.ImageOrientationPatient = [0., 1., 0., -1., 0., 0.]
    ds.ImagePositionPatient = [10., 20., float(z)]
    ds.PixelSpacing = [2., 3.]
    ds.PhotometricInterpretation = "MONOCHROME2"
    ds.SamplesPerPixel = 1
    ds.BitsAllocated = ds.BitsStored = 16
    ds.HighBit, ds.PixelRepresentation = 15, 1
    ds.RescaleSlope, ds.RescaleIntercept, ds.RescaleType = 2., -100., "HU"
    ds.PixelData = np.arange(6, dtype=np.int16).reshape(2, 3).tobytes()
    stream = io.BytesIO()
    ds.save_as(stream, enforce_file_format=True)
    return stream.getvalue()


def series_request(tmp_path, positions=(20., 0., 10.)):
    sources = []
    for i, z in enumerate(positions):
        raw = dicom_bytes(z, "1.2.3.4." + str(i + 1))
        path = tmp_path / f"slice-{i}.dcm"
        path.write_bytes(raw)
        sources.append({"file_id": f"slice-{i}", "uri": path.as_uri(), "sha256": hashlib.sha256(raw).hexdigest()})
    return {"format": "dicom-series", "sources": sources, "dicom_patient_id": "HOSPITAL-P1", "series_instance_uid": "1.2.3.4", "coordinate_frame": "DICOM_LPS", "intensity_unit": "HU"}


def test_dicom_oblique_coordinates_slice_sorting_and_hu(tmp_path):
    image, affine, _ = load_scalar_image(series_request(tmp_path))
    assert image.shape == (3, 2, 3)
    np.testing.assert_allclose(image[:, :, 0], np.arange(6).reshape(2, 3).T * 2 - 100)
    np.testing.assert_allclose(affine[:3, 0], [0., 0.003, 0.])
    np.testing.assert_allclose(affine[:3, 1], [-0.002, 0., 0.])
    np.testing.assert_allclose(affine[:3, 2], [0., 0., 0.01])
    np.testing.assert_allclose(affine[:3, 3], [0.011, 0.0185, -0.005])


@pytest.mark.parametrize("change", ["patient", "series", "spacing", "phase"])
def test_dicom_rejects_identity_and_geometric_ambiguity(tmp_path, change):
    p = series_request(tmp_path, (0., 10., 25.) if change == "spacing" else (0., 0., 10.) if change == "phase" else (0., 10., 20.))
    if change == "patient":
        p["dicom_patient_id"] = "other"
    elif change == "series":
        p["series_instance_uid"] = "1.2.8"
    with pytest.raises(ValueError):
        load_scalar_image(p)


def test_seeded_segmentation_selects_only_seeded_connected_region(tmp_path):
    p = raw_image_request(tmp_path)
    result = segment_seeded(p)
    labels = np.asarray(read(result["labels"])["labels"])
    assert np.sum(labels == 1) == 8
    assert labels[4, 4, 4] == 0
    receipt = read(result["segmentation"])
    assert receipt["review_status"] == "pending"
    assert receipt["measurements"][0]["volume_m3"] == pytest.approx(8e-9)
    assert receipt["source_files"][0]["sha256"] == p["source"]["sha256"]


@pytest.mark.parametrize("change", ["seed", "overlap", "unit"])
def test_seeded_segmentation_rejects_ambiguity(tmp_path, change):
    p = raw_image_request(tmp_path)
    if change == "seed":
        p["regions"][0]["seeds"] = [[2, 2, 2]]
    elif change == "overlap":
        p["regions"].append({**p["regions"][0], "label": 2})
    else:
        p["intensity_unit"] = "arbitrary"
    with pytest.raises(ValueError):
        segment_seeded(p)


def test_nnunet_contract_pins_model_grid_and_sanitizes_environment(tmp_path, monkeypatch):
    import nibabel as nib
    import hearttwin.patient_segmentation as module

    model = tmp_path / "model"
    (model / "fold_0").mkdir(parents=True)
    (model / "dataset.json").write_text(json.dumps({"channel_names": {"0": "CT"}, "file_ending": ".nii.gz"}))
    (model / "fold_0/checkpoint_final.pth").write_bytes(b"synthetic-contract-only-not-a-trained-model")
    manifest = {"model_directory": str(model), "trusted_by": "synthetic-contract-test", "training_provenance": "No real model in this test",
                "files": {str(p.relative_to(model)): hashlib.sha256(p.read_bytes()).hexdigest() for p in model.rglob("*") if p.is_file()},
                "nnunet_version": "synthetic-contract", "intensity_unit": "HU", "folds": [0], "checkpoint": "checkpoint_final.pth", "label_ids": [0, 1]}
    p = raw_image_request(tmp_path)
    p.update(model_manifest=write_artifact(tmp_path, "model-manifest", manifest), device="cpu", tile_step_size=0.5, test_time_augmentation=False, timeout_s=10)
    calls = []

    def run(command, **kwargs):
        assert "HOSPITAL_AUTH" not in kwargs["env"]
        assert "HEARTTWIN_WEB_TOKEN" not in kwargs["env"]
        inputs, outputs = Path(command[command.index("-i") + 1]), Path(command[command.index("-o") + 1])
        image = nib.load(inputs / "patient_0000.nii.gz")
        labels = (np.asanyarray(image.dataobj) > 90).astype(np.int16)
        prediction = nib.Nifti1Image(labels, image.affine)
        prediction.header.set_xyzt_units("mm")
        nib.save(prediction, outputs / "patient.nii.gz")
        calls.append(command)
        return SimpleNamespace(returncode=0)

    monkeypatch.setenv("HOSPITAL_AUTH", "synthetic-only")
    monkeypatch.setenv("HEARTTWIN_WEB_TOKEN", "synthetic-only")
    monkeypatch.setattr(module.importlib.metadata, "version", lambda name: "synthetic-contract")
    monkeypatch.setattr(module.shutil, "which", lambda name: "/trusted/synthetic-nnunet-contract")
    monkeypatch.setattr(module.subprocess, "run", run)
    result = segment_nnunet(p)
    assert read(result["segmentation"])["review_status"] == "pending"
    assert np.count_nonzero(read(result["labels"])["labels"]) == 16
    assert "--disable_tta" in calls[0]
    (model / "fold_0/checkpoint_final.pth").write_bytes(b"tampered")
    with pytest.raises(ValueError, match="inventory"):
        segment_nnunet(p)
    assert len(calls) == 1


@pytest.mark.parametrize("multipart", [False, True])
def test_dicomweb_pins_instances_and_refuses_mismatched_patient(tmp_path, monkeypatch, multipart):
    import urllib.request

    config = {"base_url": "https://hospital.invalid/dicomweb", "study_instance_uid": "1.2.3", "series_instance_uid": "1.2.3.4",
              "sop_instance_uids": ["1.2.3.4.1", "1.2.3.4.2"], "dicom_patient_id": "HOSPITAL-P1", "token_env": "SYNTHETIC_DICOM_TOKEN"}
    calls = []

    class Response(io.BytesIO):
        pass

    class Opener:
        def open(self, request, timeout):
            assert request.get_header("Authorization") == "Bearer synthetic-only"
            calls.append(request.full_url)
            raw = dicom_bytes(len(calls) * 10, request.full_url.rsplit("/", 1)[-1])
            if multipart:
                raw = b'--example\r\nContent-Type: application/dicom\r\n\r\n' + raw + b'\r\n--example--\r\n'
            result = Response(raw)
            result.headers = {"Content-Type": 'multipart/related; boundary="example"; type="application/dicom"' if multipart else "application/dicom"}
            return result

    monkeypatch.setenv("SYNTHETIC_DICOM_TOKEN", "synthetic-only")
    monkeypatch.setattr(urllib.request, "build_opener", lambda *a: Opener())
    result = retrieve_dicom_instances(config, output_dir=tmp_path)
    assert len(result) == 2 and len(calls) == 2
    assert "synthetic-only" not in json.dumps(result)
    config["dicom_patient_id"] = "OTHER-PATIENT"
    with pytest.raises(ValueError, match="different patient"):
        retrieve_dicom_instances(config, output_dir=tmp_path)


def test_patient_plan_rejects_unbound_plain_source_reference():
    from hearttwin.patient_personalization import PatientPlanJob, resolve_patient_template

    job = PatientPlanJob(case={"patient_id": "P1", "encounters": [{"encounter_id": "v"}]}, case_revision=1,
                         case_fingerprint="0" * 64, encounter_id="v", stages=[{"stage_id": "s", "capability": "patient.images.segment", "purpose": "test", "template": {}}])
    with pytest.raises(ValueError, match="must use"):
        resolve_patient_template({"uri": "file:///unbound-image", "sha256": "0" * 64}, job)
