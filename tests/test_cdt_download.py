import importlib.util
from io import BytesIO
from pathlib import Path


SCRIPT = Path(__file__).parents[1] / "scripts" / "fetch_cdt_zenodo.py"
SPEC = importlib.util.spec_from_file_location("fetch_cdt_zenodo", SCRIPT)
assert SPEC is not None and SPEC.loader is not None
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)
download = MODULE.download


class _Response(BytesIO):
    def __init__(self, data: bytes, status: int):
        super().__init__(data)
        self.status = status

    def __enter__(self):
        return self

    def __exit__(self, *args):
        self.close()


def test_download_resumes_partial_file(tmp_path: Path, monkeypatch) -> None:
    destination = tmp_path / "fixture.bin"
    Path(str(destination) + ".part").write_bytes(b"abc")
    seen = []

    def fake_urlopen(request, timeout=120):
        seen.append(request.headers.get("Range"))
        return _Response(b"def", 206)

    monkeypatch.setattr("urllib.request.urlopen", fake_urlopen)
    download("https://example.test/fixture.bin", destination, expected_size=6)
    assert destination.read_bytes() == b"abcdef"
    assert seen == ["bytes=3-"]


def test_download_restarts_when_server_ignores_range(tmp_path: Path, monkeypatch) -> None:
    destination = tmp_path / "fixture.bin"
    Path(str(destination) + ".part").write_bytes(b"abc")
    replies = iter([_Response(b"abcdef", 200), _Response(b"abcdef", 200)])

    monkeypatch.setattr("urllib.request.urlopen", lambda request, timeout=120: next(replies))
    download("https://example.test/fixture.bin", destination, expected_size=6)
    assert destination.read_bytes() == b"abcdef"
