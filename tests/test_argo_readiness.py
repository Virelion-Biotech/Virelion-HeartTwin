from __future__ import annotations

import hashlib
import json
import sys
from types import SimpleNamespace

import pytest

from hearttwin import argo_readiness as audit


@pytest.mark.parametrize("label", ["P", "U"])
def test_non_avp_sentinel_is_not_a_time_or_physiological_relabel(label):
    result = audit.annotation_result(
        SimpleNamespace(symbol=['"'], aux_note=[label], sample=[9998])
    )
    assert result["class"] == label
    assert result["avp_bounds_samples"] is None
    assert not result["bounds_applicable"] and not result["independent_LAT_truth"]


@pytest.mark.parametrize(
    "label,samples",
    [("A", [9999, 9999]), ("A", [12, 11]), ("P", [0, 1]), ("X", [0, 1])],
)
def test_bad_annotation_semantics_fail_closed(label, samples):
    with pytest.raises(ValueError):
        audit.annotation_result(
            SimpleNamespace(symbol=["(", ")"], aux_note=[label, label], sample=samples)
        )


def test_avp_bounds_are_samples_not_LAT_truth():
    result = audit.annotation_result(
        SimpleNamespace(symbol=["(", ")"], aux_note=["A", "A"], sample=[1800, 1850])
    )
    assert result["avp_bounds_samples"] == [1800, 1850]
    assert result["independent_LAT_truth"] is False


def test_checkpoint_rejects_changed_results_and_protocol(tmp_path):
    path = tmp_path / "page.json"
    audit.save_page(path, "one", {"P1": {"class": "U"}})
    assert audit.load_page(path, "one")["P1"]["class"] == "U"
    with pytest.raises(ValueError, match="integrity/identity"):
        audit.load_page(path, "two")
    value = json.loads(path.read_text())
    value["records"]["P1"]["class"] = "P"
    path.write_text(json.dumps(value))
    with pytest.raises(ValueError, match="integrity/identity"):
        audit.load_page(path, "one")


def test_cached_input_is_rehashed_not_trusted_from_receipt(tmp_path):
    path = tmp_path / "record.dat"
    path.write_bytes(b"changed")
    with pytest.raises(ValueError, match="Cached input checksum"):
        audit.download(
            path,
            hashlib.sha256(b"original").hexdigest(),
            audit.Budget(10),
            "https://example.test/a",
        )


def test_http_range_resume_and_complete_part_reuse(tmp_path, monkeypatch):
    path = tmp_path / "data.dat"
    path.with_suffix(".dat.part").write_bytes(b"ab")

    class Reply:
        status = 206

        def __init__(self):
            self.headers = {"Content-Range": "bytes 2-5/6"}

        def __enter__(self):
            return self

        def __exit__(self, *args):
            return None

        def read(self, size):
            payload = getattr(self, "payload", b"cdef")
            self.payload = b""
            return payload

    def fetch(request, timeout):
        assert request.get_header("Range") == "bytes=2-"
        return Reply()

    monkeypatch.setattr(audit.urllib.request, "urlopen", fetch)
    digest = hashlib.sha256(b"abcdef").hexdigest()
    audit.download(path, digest, audit.Budget(10), "https://example.test/a")
    assert path.read_bytes() == b"abcdef"
    path.rename(path.with_suffix(".dat.part"))
    monkeypatch.setattr(
        audit.urllib.request,
        "urlopen",
        lambda *a, **k: pytest.fail("complete part must not redownload"),
    )
    audit.download(path, digest, audit.Budget(10), "https://example.test/a")
    assert path.read_bytes() == b"abcdef"


def test_record_validation_precedes_full_download_and_resumes(tmp_path, monkeypatch):
    # Exercise the real orchestration with a complete official-size inventory;
    # replace only network and scientific parsers to test interruption ordering.
    monkeypatch.setitem(sys.modules, "wfdb", SimpleNamespace(__version__="test"))
    records = [
        f"ARGODataset_Folder/{p}/P{i}"
        for p, count in audit.native._EXPECTED_POINT_COUNTS.items()
        for i in range(count)
    ]
    names = set(audit.GLOBAL_FILES)
    names.update(n + "." + ext for n in records for ext in audit.EXTENSIONS)
    names.update(
        f"ARGODataset_Folder/{p}/{n}"
        for p in audit.native._EXPECTED_POINT_COUNTS
        for n in audit.METADATA
    )
    monkeypatch.setattr(
        audit, "manifest_lookup", lambda p: dict.fromkeys(names, "0" * 64)
    )
    monkeypatch.setattr(audit, "record_inventory", lambda p: records)
    downloads, validated = [], []

    def download(path, expected, budget, url=None):
        budget.check()
        downloads.append(str(path))
        return path

    monkeypatch.setattr(audit, "download", download)
    monkeypatch.setattr(
        audit, "validate_geometry", lambda path, ids: {"n_points": len(ids)}
    )

    def validate(base):
        validated.append(base.name)
        return {
            "patient": base.parent.name,
            "record": base.name,
            "annotations": {"annotation_consensus": {"class": "U"}},
        }

    monkeypatch.setattr(audit, "validate_record", validate)
    data, output = tmp_path / "data", tmp_path / "results"
    first = audit.run(data, output, 30, max_records=1)
    assert first["status"] == "PAUSED" and first["records_verified"] == 1
    assert (
        len(downloads) == 16
    )  # Manifest + 4 global + 5 geometry + 6 first-record files.
    assert validated == ["P0"]
    second = audit.run(data, output, 30, max_records=2)
    assert second["status"] == "PAUSED" and second["records_verified"] == 2
    assert validated == ["P0", "P1"]  # P0 reused after input verification.
    zero = audit.run(data, output, 0)
    assert zero["status"] == "PAUSED"  # Never claim a full cohort on budget exhaustion.


@pytest.mark.parametrize("sample", [9998, 9999])
def test_documented_and_observed_note_sentinels_remain_verbatim(sample):
    result = audit.annotation_result(
        SimpleNamespace(symbol=['"'], aux_note=["U"], sample=[sample])
    )
    assert result["raw_samples"] == [sample]
    assert result["avp_bounds_samples"] is None


def test_old_assumption_of_two_non_avp_boundaries_is_rejected():
    with pytest.raises(ValueError, match="non-AVP sentinel"):
        audit.annotation_result(
            SimpleNamespace(symbol=["(", ")"], aux_note=["P", "P"], sample=[9999, 9999])
        )
