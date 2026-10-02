from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
import threading
import time

import numpy as np
import pytest

from hearttwin.native_services import _json_safe
from hearttwin.rehearsal_worker import RehearsalWorker


@dataclass
class FixtureResult:
    value: int
    values: tuple[int, ...]


def test_json_safe_normalizes_dataclasses_numpy_and_tuples() -> None:
    payload = {
        "fixture": FixtureResult(3, (1, 2)),
        "array": np.array([1.0, 2.0]),
        "scalar": np.int64(7),
    }
    assert _json_safe(payload) == {
        "fixture": {"value": 3, "values": [1, 2]},
        "array": [1.0, 2.0],
        "scalar": 7,
    }


def test_json_safe_rejects_nonfinite_float() -> None:
    with pytest.raises(ValueError, match="non-finite"):
        _json_safe({"value": float("nan")})


def test_json_safe_rejects_unknown_python_object() -> None:
    class Unknown:
        pass

    with pytest.raises(TypeError, match="non-JSON transport"):
        _json_safe(Unknown())


def test_worker_coalesces_concurrent_duplicate_requests() -> None:
    started = threading.Event()
    release = threading.Event()

    class Adapter:
        def __init__(self) -> None:
            self.calls = 0

        def invoke(self, capability, payload):
            self.calls += 1
            started.set()
            assert release.wait(timeout=10)
            return {"capability": capability, "value": payload["value"]}

    adapter = Adapter()
    worker = RehearsalWorker.__new__(RehearsalWorker)
    worker.service_name = "Fixture"
    worker.adapter = adapter
    worker.cache = {}
    worker.inflight = {}
    worker.cache_lock = threading.Lock()

    with ThreadPoolExecutor(max_workers=2) as pool:
        first = pool.submit(
            worker.invoke,
            "fixture.run",
            {"value": 42},
            "same-key",
        )
        assert started.wait(timeout=10)
        second = pool.submit(
            worker.invoke,
            "fixture.run",
            {"value": 42},
            "same-key",
        )
        time.sleep(0.05)
        assert adapter.calls == 1
        release.set()
        first_result = first.result(timeout=10)
        second_result = second.result(timeout=10)

    assert adapter.calls == 1
    assert first_result[0] == second_result[0] == {
        "capability": "fixture.run",
        "value": 42,
    }
    assert sorted([first_result[1], second_result[1]]) == [False, True]


def test_worker_caches_completed_idempotent_request() -> None:
    class Adapter:
        def __init__(self) -> None:
            self.calls = 0

        def invoke(self, capability, payload):
            self.calls += 1
            return {"ok": True}

    adapter = Adapter()
    worker = RehearsalWorker.__new__(RehearsalWorker)
    worker.service_name = "Fixture"
    worker.adapter = adapter
    worker.cache = {}
    worker.inflight = {}
    worker.cache_lock = threading.Lock()

    first = worker.invoke("fixture.run", {}, "key")
    second = worker.invoke("fixture.run", {}, "key")

    assert first == ({"ok": True}, False)
    assert second == ({"ok": True}, True)
    assert adapter.calls == 1
