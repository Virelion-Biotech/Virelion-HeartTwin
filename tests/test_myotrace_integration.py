from __future__ import annotations

import json
import os
import shutil
import subprocess

import pytest

from hearttwin.contracts import Observation, Provenance
from hearttwin.orchestrator import HeartTwin
from hearttwin.service_registry import ServiceRegistry, ServiceSpec


def _make_synthetic_video(path):
    cv2 = pytest.importorskip("cv2")
    import numpy as np

    fps = 30
    writer = cv2.VideoWriter(
        str(path),
        cv2.VideoWriter_fourcc(*"mp4v"),
        fps,
        (128, 128),
        isColor=False,
    )
    if not writer.isOpened():
        pytest.skip("OpenCV MP4 writer unavailable on this runner")
    try:
        for frame_idx in range(fps * 6):
            frame = np.zeros((128, 128), dtype=np.uint8)
            phase = frame_idx / fps * 2 * np.pi * 1.25
            radius = int(22 + 8 * (0.5 + 0.5 * np.sin(phase)))
            cv2.circle(frame, (64, 64), radius, 220, -1)
            writer.write(frame)
    finally:
        writer.release()


def test_myotrace_local_command_end_to_end(tmp_path):
    command = "myotrace-hearttwin"
    if shutil.which(command) is None:
        pytest.skip("MyoTrace HeartTwin command is not installed")

    video = tmp_path / "synthetic.mp4"
    _make_synthetic_video(video)
    observation = Observation(
        observation_id="obs-mechanical-001",
        modality="mechanical",
        values={"input_path": os.fspath(video), "fps": 30},
        provenance=Provenance(
            source_service="integration-test",
            source_repository="Virelion-Biotech/Virelion-HeartTwin",
            run_id="fixture-run",
        ),
    )
    registry = ServiceRegistry(
        [
            ServiceSpec(
                name="MyoTrace",
                repository="Virelion-Biotech/Virelion-MyoTrace",
                capabilities=("mechanical.analyze",),
                command=command,
            )
        ]
    )
    run = HeartTwin(registry).run(
        "sample-001",
        observations=[observation],
        capabilities=["mechanical.analyze"],
    )
    assert len(run.results) == 1
    assert run.results[0].status == "ok"
    assert "n_beats" in run.results[0].data["summary"]


def test_myotrace_adapter_has_json_contract(tmp_path):
    command = shutil.which("myotrace-hearttwin")
    if command is None:
        pytest.skip("MyoTrace HeartTwin command is not installed")
    payload = {"entity_id": "sample", "context": {}, "observations": []}
    env = os.environ.copy()
    env["HEARTTWIN_PAYLOAD"] = json.dumps(payload)
    result = subprocess.run([command], env=env, capture_output=True, text=True, check=False)
    assert result.returncode != 0
    assert "No 'mechanical' observation" in result.stderr
