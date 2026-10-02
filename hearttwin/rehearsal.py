from __future__ import annotations

import json
import os
from pathlib import Path
import socket
import subprocess
import sys
import time
from typing import Any
import urllib.error
import urllib.request

import numpy as np

from .config import load_registry
from .contracts import Observation, Provenance
from .operations import ArtifactStore, CaseJournal
from .provenance import sha256
from .service_registry import ServiceRegistry, ServiceSpec
from .workflow import run_multimodal_workflow


CANARY = "HEARTTWIN-OPERATIONAL-REHEARSAL"


def _free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


def _get_json(url: str, timeout: float = 2.0) -> dict[str, Any]:
    with urllib.request.urlopen(url, timeout=timeout) as response:
        return json.loads(response.read().decode("utf-8"))


def _post_json(
    url: str,
    payload: dict[str, Any],
    *,
    timeout: float = 5.0,
    idempotency_key: str | None = None,
) -> tuple[dict[str, Any], bool]:
    headers = {"Content-Type": "application/json"}
    if idempotency_key:
        headers["X-HeartTwin-Idempotency-Key"] = idempotency_key
    request = urllib.request.Request(
        url,
        data=json.dumps(payload, allow_nan=False).encode("utf-8"),
        headers=headers,
        method="POST",
    )
    with urllib.request.urlopen(request, timeout=timeout) as response:
        cached = response.headers.get("X-HeartTwin-Rehearsal-Cached") == "1"
        return json.loads(response.read().decode("utf-8")), cached


class WorkerProcess:
    def __init__(
        self,
        service: str,
        endpoint: str,
        *,
        env: dict[str, str],
        log_dir: Path,
    ):
        self.service = service
        self.endpoint = endpoint
        self.port = int(endpoint.rsplit(":", 1)[-1])
        self.env = dict(env)
        self.log_dir = log_dir
        self.process: subprocess.Popen | None = None
        self._log_handle = None

    def start(self) -> None:
        if self.process is not None and self.process.poll() is None:
            return
        self.log_dir.mkdir(parents=True, exist_ok=True)
        log_path = self.log_dir / f"{self.service}.log"
        self._log_handle = log_path.open("ab")
        self.process = subprocess.Popen(
            [
                sys.executable,
                "-m",
                "hearttwin.rehearsal_worker",
                "--service",
                self.service,
                "--host",
                "127.0.0.1",
                "--port",
                str(self.port),
            ],
            env=self.env,
            stdout=self._log_handle,
            stderr=subprocess.STDOUT,
        )
        deadline = time.monotonic() + 30.0
        while time.monotonic() < deadline:
            if self.process.poll() is not None:
                raise RuntimeError(
                    f"{self.service} worker exited during startup; see {log_path}"
                )
            try:
                health = _get_json(self.endpoint + "/health", timeout=0.5)
                if health.get("status") == "ok":
                    return
            except Exception:
                time.sleep(0.1)
        raise TimeoutError(f"{self.service} worker did not become healthy")

    def stop(self) -> None:
        process = self.process
        if process is not None and process.poll() is None:
            process.terminate()
            try:
                process.wait(timeout=10)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=5)
        self.process = None
        if self._log_handle is not None:
            self._log_handle.close()
            self._log_handle = None

    def restart(self) -> None:
        self.stop()
        self.start()


def _observation(entity_id: str, modality: str, values: dict[str, Any]) -> Observation:
    return Observation(
        observation_id=f"obs-{entity_id}-{modality}",
        modality=modality,
        values=values,
        provenance=Provenance(
            source_service="hearttwin-operational-rehearsal",
            source_repository="Virelion-Biotech/Virelion-HeartTwin",
            run_id=f"rehearsal-{modality}",
        ),
    )


def _rows(n: int = 20) -> list[dict[str, Any]]:
    return [
        {
            "sample_id": f"R{i:03d}",
            "group_id": f"RG{i:03d}",
            "study_id": CANARY,
            "label": "MI" if i % 2 else "sham",
            "target": i % 2,
            "gene_a": float(i) / n,
            "gene_b": float((i * 7) % n) / n,
        }
        for i in range(n)
    ]


def _write_inputs(root: Path) -> dict[str, Path]:
    import pandas as pd
    import cv2

    root.mkdir(parents=True, exist_ok=True)

    ecg = root / "ecg.csv"
    fs = 100.0
    t = np.arange(0.0, 2.0, 1.0 / fs)
    pd.DataFrame(
        {"time": t, "lead_I": np.sin(2 * np.pi * 1.2 * t)}
    ).to_csv(ecg, index=False)

    video = root / "motion.avi"
    writer = cv2.VideoWriter(
        str(video),
        cv2.VideoWriter_fourcc(*"MJPG"),
        20.0,
        (64, 64),
        isColor=False,
    )
    if not writer.isOpened():
        raise RuntimeError("OpenCV could not create the rehearsal AVI")
    try:
        for frame_index in range(60):
            frame = np.zeros((64, 64), dtype=np.uint8)
            phase = frame_index / 20.0 * 2.0 * np.pi
            radius = int(12 + 3 * np.sin(phase))
            cv2.circle(frame, (32, 32), radius, 220, -1)
            writer.write(frame)
    finally:
        writer.release()

    image = root / "cells.png"
    microscopy = np.zeros((96, 96), dtype=np.uint8)
    cv2.circle(microscopy, (32, 48), 12, 180, -1)
    cv2.circle(microscopy, (64, 48), 10, 220, -1)
    if not cv2.imwrite(str(image), microscopy):
        raise RuntimeError("OpenCV could not create the rehearsal microscopy image")

    from virelion_cardioscore.io.synthetic import load_synthetic_dataset

    safety = root / "mea.csv"
    dataset = load_synthetic_dataset(
        n_compounds=1,
        n_concentrations=4,
        seed=2718,
    )
    dataset.features.to_csv(safety, index=False)
    return {
        "electrical": ecg,
        "mechanical": video,
        "imaging": image,
        "safety": safety,
    }


def _distributed_registry(
    base: ServiceRegistry,
    endpoints: dict[str, str],
) -> ServiceRegistry:
    specs: list[ServiceSpec] = []
    for spec in base.services():
        if spec.name == "CardiSimNative":
            specs.append(spec)
            continue
        endpoint = endpoints.get(spec.name)
        if endpoint is None:
            raise RuntimeError(f"No rehearsal worker endpoint for {spec.name}")
        specs.append(
            ServiceSpec(
                name=spec.name,
                repository=spec.repository,
                capabilities=spec.capabilities,
                endpoint=endpoint,
                optional=spec.optional,
                path_template=spec.path_template,
            )
        )
    return ServiceRegistry(specs)


def run_operational_rehearsal(
    workdir: str | Path,
    *,
    case_id: str = CANARY,
) -> dict[str, Any]:
    root = Path(workdir).resolve()
    root.mkdir(parents=True, exist_ok=True)
    journal = CaseJournal(root / "journal.sqlite3")
    artifacts = ArtifactStore(root / "artifacts")
    inputs = _write_inputs(root / "inputs")
    rows = _rows()

    input_manifest = {
        "case_id": case_id,
        "input_files": {
            key: {"path": str(path), "size": path.stat().st_size}
            for key, path in inputs.items()
        },
        "rows_sha256": sha256(rows),
    }
    journal.begin_case(case_id, input_manifest)

    base = load_registry()
    worker_specs = [
        spec for spec in base.services()
        if spec.name != "CardiSimNative"
    ]
    endpoints = {
        spec.name: f"http://127.0.0.1:{_free_port()}"
        for spec in worker_specs
    }

    worker_env = os.environ.copy()
    for key in list(worker_env):
        if key.startswith("CARDI") and key.endswith("_URL"):
            worker_env.pop(key, None)
    worker_env["CARDIBRIDGE_STORE_PATH"] = str(root / "cardibridge.sqlite3")
    worker_env["CARDITRACE_ROOT"] = str(root / "carditrace")
    worker_env["HEARTTWIN_REHEARSAL_ENDPOINTS"] = json.dumps(endpoints)

    workers = {
        spec.name: WorkerProcess(
            spec.name,
            endpoints[spec.name],
            env=worker_env,
            log_dir=root / "logs",
        )
        for spec in worker_specs
    }

    try:
        for worker in workers.values():
            worker.start()
        distributed = _distributed_registry(base, endpoints)

        health, _ = journal.run_stage(
            case_id,
            "health-fanout",
            {"services": sorted(endpoints)},
            lambda: {
                name: _get_json(endpoint + "/health")
                for name, endpoint in endpoints.items()
            },
        )
        unhealthy = [
            name for name, result in health.items()
            if result.get("status") != "ok"
        ]
        if unhealthy:
            raise RuntimeError(f"Unhealthy rehearsal workers: {unhealthy}")

        timeout_observed = False
        try:
            _post_json(
                endpoints["CardiAtlas"] + "/v1/atlas/search",
                {
                    "entity_id": case_id,
                    "query": case_id,
                    "records": [],
                    "_rehearsal_delay_seconds": 1.0,
                },
                timeout=0.05,
                idempotency_key="timeout-probe",
            )
        except Exception:
            timeout_observed = True
        if not timeout_observed:
            raise RuntimeError("Timeout probe unexpectedly completed")
        journal.event(case_id, "fault.timeout_observed", {"service": "CardiAtlas"})

        workers["CardiAtlas"].stop()
        outage_observed = False
        try:
            _get_json(endpoints["CardiAtlas"] + "/health", timeout=0.2)
        except Exception:
            outage_observed = True
        if not outage_observed:
            raise RuntimeError("Worker outage probe unexpectedly remained reachable")
        workers["CardiAtlas"].start()
        recovered = distributed.capability("atlas.search")
        if recovered is None:
            raise RuntimeError("atlas.search route disappeared after worker restart")
        recovered_result = recovered.invoke(
            "atlas.search",
            {"entity_id": case_id, "query": case_id, "records": []},
        )
        if recovered_result.get("query") != case_id:
            raise RuntimeError("Restarted CardiAtlas returned a misaligned result")
        journal.event(case_id, "fault.worker_recovered", {"service": "CardiAtlas"})

        duplicate_url = endpoints["CardiAnatomy"] + "/v1/anatomy/health"
        duplicate_payload = {"entity_id": case_id}
        first, first_cached = _post_json(
            duplicate_url,
            duplicate_payload,
            idempotency_key="duplicate-probe",
        )
        second, second_cached = _post_json(
            duplicate_url,
            duplicate_payload,
            idempotency_key="duplicate-probe",
        )
        if first != second or first_cached or not second_cached:
            raise RuntimeError("Worker idempotency cache did not suppress duplicate work")
        journal.event(
            case_id,
            "fault.duplicate_suppressed",
            {"service": "CardiAnatomy"},
        )

        observations = [
            _observation(
                case_id,
                "molecular",
                {"gene_a": 0.4, "gene_b": 0.6, "canary": case_id},
            ),
            _observation(
                case_id,
                "structural",
                {"region": "left_ventricle", "zone": "IZ", "canary": case_id},
            ),
            _observation(
                case_id,
                "clinical",
                {"condition": "operational_rehearsal", "canary": case_id},
            ),
            _observation(
                case_id,
                "electrical",
                {"input_path": str(inputs["electrical"]), "time_col": "time"},
            ),
            _observation(
                case_id,
                "mechanical",
                {
                    "input_path": str(inputs["mechanical"]),
                    "fps": 20,
                    "allow_qc_fail": True,
                },
            ),
            _observation(
                case_id,
                "imaging",
                {
                    "input_path": str(inputs["imaging"]),
                    "cell_method": "threshold",
                    "adaptive_qc": False,
                },
            ),
            _observation(
                case_id,
                "safety",
                {"input_path": str(inputs["safety"])},
            ),
        ]

        workflow_payload = {
            "case_id": case_id,
            "rows_sha256": sha256(rows),
            "observations": [
                observation.model_dump(mode="json")
                for observation in observations
            ],
        }

        def execute_workflow() -> dict[str, Any]:
            run = run_multimodal_workflow(
                distributed,
                entity_id=case_id,
                observations=observations,
                benchmark_samples=[
                    {
                        "sample_id": row["sample_id"],
                        "group_id": row["group_id"],
                        "study_id": row["study_id"],
                        "label": row["label"],
                        "region": "IZ" if row["target"] else "remote",
                    }
                    for row in rows
                ],
                learning_data=rows,
                feature_columns=["gene_a", "gene_b"],
                reference_labels={
                    row["sample_id"]: row["target"]
                    for row in rows
                },
                simulation={
                    "preset": "mi",
                    "n_cells": 12,
                    "duration": 1.0,
                    "dt": 0.25,
                },
                seed=314159,
            )
            return run.model_dump(mode="json")

        workflow_result, reused = journal.run_stage(
            case_id,
            "distributed-workflow",
            workflow_payload,
            execute_workflow,
            max_attempts=2,
        )
        if reused:
            raise RuntimeError("First distributed workflow execution was unexpectedly cached")
        if workflow_result.get("entity_id") != case_id:
            raise RuntimeError("Distributed workflow changed case identity")
        modalities = {
            item["modality"]
            for item in workflow_result["state"]["modality_analyses"]
        }
        if modalities != {"electrical", "mechanical", "imaging", "safety"}:
            raise RuntimeError(
                f"Distributed specialist modalities were incomplete: {modalities}"
            )

        replay_result, replay_reused = journal.run_stage(
            case_id,
            "distributed-workflow",
            workflow_payload,
            lambda: (_ for _ in ()).throw(
                RuntimeError("cached stage should not execute")
            ),
        )
        if not replay_reused or replay_result != workflow_result:
            raise RuntimeError("Durable stage replay was not idempotent")

        output = artifacts.put_json("workflow-run", workflow_result)
        if not artifacts.verify(output):
            raise RuntimeError("Fresh workflow artifact failed verification")

        artifacts.corrupt_for_rehearsal(output)
        if artifacts.verify(output):
            raise RuntimeError("Corrupted artifact was not detected")
        journal.event(
            case_id,
            "fault.artifact_corruption_detected",
            output.model_dump(),
        )

        repaired = artifacts.put_json("workflow-run", workflow_result)
        if not artifacts.verify(repaired):
            raise RuntimeError("Repaired workflow artifact failed verification")
        journal.complete_case(case_id, repaired)

        reopened = CaseJournal(root / "journal.sqlite3")
        snapshot = reopened.snapshot(case_id)
        if snapshot["case"]["status"] != "ok":
            raise RuntimeError("Reopened case journal did not preserve completion state")

        summary = {
            "case_id": case_id,
            "status": "ok",
            "distributed_service_count": len(endpoints),
            "worker_endpoints": endpoints,
            "workflow_run_id": workflow_result["run_id"],
            "workflow_state_fingerprint": workflow_result["state"]["cardiac_state"][
                "state_fingerprint"
            ],
            "output_artifact": repaired.model_dump(),
            "timeout_observed": timeout_observed,
            "worker_restart_recovered": True,
            "duplicate_suppressed": second_cached,
            "artifact_corruption_detected": True,
            "durable_replay_reused": replay_reused,
            "journal_event_count": len(snapshot["events"]),
            "journal_stage_count": len(snapshot["stages"]),
        }
        (root / "rehearsal-summary.json").write_text(
            json.dumps(summary, indent=2, sort_keys=True),
            encoding="utf-8",
        )
        return summary
    except Exception as exc:
        journal.fail_case(case_id, str(exc))
        raise
    finally:
        for worker in workers.values():
            worker.stop()
