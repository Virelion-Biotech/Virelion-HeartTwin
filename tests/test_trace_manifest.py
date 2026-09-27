from __future__ import annotations

import json

from hearttwin.contracts import Observation, Provenance, ServiceResult, WorkflowState
from hearttwin.workflow import _trace_manifest


def test_trace_manifest_is_compact_and_content_addressed_for_large_modalities() -> None:
    large_values = {
        "time": [float(i) / 250.0 for i in range(50_000)],
        "signals": {"lead_I": [float(i % 100) for i in range(50_000)]},
    }
    observation = Observation(
        observation_id="obs-large-ecg",
        modality="electrical",
        values=large_values,
        provenance=Provenance(source_service="fixture", run_id="obs-run"),
    )
    state = WorkflowState(entity_id="entity-1", observations=[observation])
    step = ServiceResult(
        service="ElectroTrace",
        capability="electrical.analyze",
        status="ok",
        data={"electrical_analysis": large_values},
        provenance=Provenance(
            source_service="ElectroTrace",
            run_id="electrotrace-run",
            content_sha256="a" * 64,
        ),
    )

    manifest = _trace_manifest(
        run_id="hearttwin-run",
        observations=[observation],
        state=state,
        canonical_state_fingerprint="b" * 64,
        steps=[step],
    )

    encoded = json.dumps(manifest, separators=(",", ":")).encode()
    assert len(encoded) < 10_000
    assert manifest["context"]["trace_payload"] == "content-addressed-manifest-v1"
    assert manifest["observations"][0]["payload_sha256"]
    assert manifest["results"][0]["data_sha256"]
    assert "signals" not in manifest["observations"][0]
    assert "electrical_analysis" not in manifest["results"][0]
