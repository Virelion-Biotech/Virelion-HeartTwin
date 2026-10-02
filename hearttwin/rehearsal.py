from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor, as_completed
import json
import os
from pathlib import Path
import socket
import subprocess
import sys
import threading
import time
from typing import Any
import urllib.error
import urllib.request

import numpy as np

from .config import load_registry
from .contracts import Observation, Provenance
from .operations import ArtifactStore, CaseJournal, IdempotencyStore, JobQueue
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
    return _rows_for_case(CANARY, n=n, prefix="R")


def _rows_for_case(
    case_id: str,
    *,
    n: int = 20,
    prefix: str = "S",
) -> list[dict[str, Any]]:
    return [
        {
            "sample_id": f"{case_id}-{prefix}{i:03d}",
            "group_id": f"{case_id}-G{i:03d}",
            "study_id": case_id,
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


def _run_concurrent_case_probe(
    distributed: ServiceRegistry,
    *,
    journal_path: Path,
    artifacts: ArtifactStore,
    base_case_id: str,
) -> dict[str, Any]:
    """Run several isolated cases concurrently through shared remote workers."""

    case_ids = [
        f"{base_case_id}-CONCURRENT-A",
        f"{base_case_id}-CONCURRENT-B",
        f"{base_case_id}-CONCURRENT-C",
    ]
    submission_order = list(case_ids)
    delay_by_case = {
        case_ids[0]: 0.0,
        case_ids[1]: 0.05,
        case_ids[2]: 0.10,
    }
    seed_by_case = {
        case_ids[0]: 3,
        case_ids[1]: 3,
        case_ids[2]: 1,
    }

    journal = CaseJournal(journal_path)
    queue = JobQueue(journal_path)
    job_ids: dict[str, str] = {}
    for index, concurrent_case_id in enumerate(case_ids):
        rows = _rows_for_case(
            concurrent_case_id,
            n=18,
            prefix=concurrent_case_id.rsplit("-", 1)[-1],
        )
        input_payload = {
            "probe": "multi-case-isolation",
            "case_id": concurrent_case_id,
            "rows_sha256": sha256(rows),
        }
        journal.begin_case(concurrent_case_id, input_payload)
        job_payload = {
            "case_id": concurrent_case_id,
            "rows_sha256": input_payload["rows_sha256"],
            "delay_seconds": delay_by_case[concurrent_case_id],
            "seed": seed_by_case[concurrent_case_id],
        }
        job_ids[concurrent_case_id] = queue.enqueue(
            concurrent_case_id,
            "concurrent-workflow",
            job_payload,
        )
        if index == 0:
            duplicate = queue.enqueue(
                concurrent_case_id,
                "concurrent-workflow",
                job_payload,
            )
            if duplicate != job_ids[concurrent_case_id]:
                raise RuntimeError(
                    "Duplicate concurrent submission was not idempotently collapsed"
                )

    concurrent_enqueued = [
        item
        for item in queue.list_jobs()
        if item["job_type"] == "concurrent-workflow"
    ]
    if len(concurrent_enqueued) != len(case_ids):
        raise RuntimeError("Duplicate submission created an extra queue job")

    # Simulate the coordinator process disappearing after enqueue but before work.
    del queue
    del journal
    queue = JobQueue(journal_path)
    journal = CaseJournal(journal_path)

    completion_order: list[str] = []
    completion_lock = __import__("threading").Lock()
    secondary_completed = __import__("threading").Event()

    def process_one(worker_id: str) -> dict[str, Any] | None:
        claim = queue.claim(worker_id, lease_seconds=180.0)
        if claim is None:
            return None

        job_id = str(claim["job_id"])
        concurrent_case_id = str(claim["case_id"])
        payload = dict(claim["payload"])
        rows = _rows_for_case(
            concurrent_case_id,
            n=18,
            prefix=concurrent_case_id.rsplit("-", 1)[-1],
        )
        expected_sha = sha256(rows)
        case_seed = int(payload["seed"])
        if payload.get("rows_sha256") != expected_sha:
            queue.fail(job_id, worker_id, "rows fingerprint mismatch")
            raise RuntimeError(
                f"Queued rows fingerprint changed for {concurrent_case_id}"
            )

        time.sleep(float(payload.get("delay_seconds", 0.0)))
        observations = [
            _observation(
                concurrent_case_id,
                "molecular",
                {
                    "gene_a": 0.4,
                    "gene_b": 0.6,
                    "concurrent_case": concurrent_case_id,
                },
            ),
            _observation(
                concurrent_case_id,
                "structural",
                {
                    "region": "left_ventricle",
                    "zone": "IZ",
                    "concurrent_case": concurrent_case_id,
                },
            ),
            _observation(
                concurrent_case_id,
                "clinical",
                {
                    "condition": "concurrent_rehearsal",
                    "concurrent_case": concurrent_case_id,
                },
            ),
        ]

        simulation_config = {
            "preset": "mi",
            "n_cells": 6,
            "duration": 0.75,
            "dt": 0.25,
        }
        workflow_payload = {
            "case_id": concurrent_case_id,
            "rows_sha256": expected_sha,
            "observations": [
                item.model_dump(mode="json")
                for item in observations
            ],
            "feature_columns": ["gene_a", "gene_b"],
            "benchmark_policy": "subject_heldout",
            "seed": case_seed,
            "simulation": simulation_config,
        }

        def execute() -> dict[str, Any]:
            run = run_multimodal_workflow(
                distributed,
                entity_id=concurrent_case_id,
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
                simulation=dict(simulation_config),
                seed=case_seed,
            )
            return run.model_dump(mode="json")

        try:
            result, reused = journal.run_stage(
                concurrent_case_id,
                "concurrent-workflow",
                workflow_payload,
                execute,
                max_attempts=1,
            )
            if reused:
                raise RuntimeError(
                    f"First concurrent execution was cached for {concurrent_case_id}"
                )

            # Force A to finish after a sibling has fully persisted completion.
            if concurrent_case_id == case_ids[0]:
                if not secondary_completed.wait(timeout=120):
                    raise RuntimeError(
                        "Could not force out-of-order concurrent completion"
                    )

            state = result.get("state") or {}
            canonical = state.get("cardiac_state") or {}
            if result.get("entity_id") != concurrent_case_id:
                raise RuntimeError("Concurrent workflow changed top-level case identity")
            if state.get("entity_id") != concurrent_case_id:
                raise RuntimeError("Concurrent WorkflowState crossed case boundaries")
            if canonical.get("entity_id") != concurrent_case_id:
                raise RuntimeError("Concurrent CardiacState crossed case boundaries")
            if not canonical.get("state_fingerprint"):
                raise RuntimeError("Concurrent CardiacState has no fingerprint")

            atlas = state.get("atlas") or {}
            if atlas.get("context_id") != f"{concurrent_case_id}-context":
                raise RuntimeError("Concurrent atlas context crossed case boundaries")

            trace = state.get("trace") or {}
            if not trace.get("run_id") or not trace.get("input_artifact_id"):
                raise RuntimeError("Concurrent trace output is incomplete")

            output = artifacts.put_json(
                f"workflow-run-{concurrent_case_id}",
                result,
            )
            if not artifacts.verify(output):
                raise RuntimeError("Concurrent output artifact failed verification")
            journal.complete_case(concurrent_case_id, output)
            queue.complete(
                job_id,
                worker_id,
                {
                    "case_id": concurrent_case_id,
                    "workflow_run_id": result["run_id"],
                    "artifact_sha256": output.sha256,
                },
            )
            with completion_lock:
                completion_order.append(concurrent_case_id)
            if concurrent_case_id != case_ids[0]:
                secondary_completed.set()
            return {
                "case_id": concurrent_case_id,
                "workflow_run_id": result["run_id"],
                "state_fingerprint": canonical["state_fingerprint"],
                "trace_run_id": trace["run_id"],
                "trace_input_artifact_id": trace["input_artifact_id"],
                "artifact": output.model_dump(),
            }
        except Exception as exc:
            try:
                queue.fail(job_id, worker_id, str(exc))
            except Exception:
                pass
            journal.fail_case(concurrent_case_id, str(exc))
            raise

    with ThreadPoolExecutor(max_workers=3) as pool:
        futures = [
            pool.submit(process_one, f"case-worker-{index}")
            for index in range(3)
        ]
        results = []
        for future in as_completed(futures):
            item = future.result()
            if item is not None:
                results.append(item)

    if len(results) != len(case_ids):
        raise RuntimeError("Not every concurrent case completed")
    if completion_order == submission_order:
        raise RuntimeError("Concurrent cases did not complete out of order")

    result_by_case = {item["case_id"]: item for item in results}
    if set(result_by_case) != set(case_ids):
        raise RuntimeError("Concurrent case result set is incomplete")

    fingerprints = {
        item["state_fingerprint"]
        for item in results
    }
    workflow_run_ids = {
        item["workflow_run_id"]
        for item in results
    }
    trace_run_ids = {
        item["trace_run_id"]
        for item in results
    }
    trace_artifacts = {
        item["trace_input_artifact_id"]
        for item in results
    }
    if len(fingerprints) != len(case_ids):
        raise RuntimeError("Concurrent cases shared a canonical-state fingerprint")
    if len(workflow_run_ids) != len(case_ids):
        raise RuntimeError("Concurrent cases shared a workflow run ID")
    if len(trace_run_ids) != len(case_ids):
        raise RuntimeError("Concurrent cases shared a trace run ID")
    if len(trace_artifacts) != len(case_ids):
        raise RuntimeError("Concurrent cases shared a trace input artifact")

    fresh_journal = CaseJournal(journal_path)
    for concurrent_case_id in case_ids:
        snapshot = fresh_journal.snapshot(concurrent_case_id)
        if snapshot["case"]["status"] != "ok":
            raise RuntimeError(
                f"Concurrent case did not persist as ok: {concurrent_case_id}"
            )
        event_case_ids = {
            event["case_id"]
            for event in snapshot["events"]
        }
        if event_case_ids != {concurrent_case_id}:
            raise RuntimeError("Journal events crossed case boundaries")

    jobs = queue.list_jobs()
    concurrent_jobs = [
        item
        for item in jobs
        if item["job_type"] == "concurrent-workflow"
    ]
    if len(concurrent_jobs) != len(case_ids):
        raise RuntimeError("Unexpected concurrent queue job count")
    if any(item["status"] != "ok" for item in concurrent_jobs):
        raise RuntimeError("At least one concurrent queue job is not complete")

    return {
        "case_ids": case_ids,
        "submission_order": submission_order,
        "completion_order": completion_order,
        "duplicate_submission_collapsed": True,
        "coordinator_restart_recovered": True,
        "case_isolation_verified": True,
        "unique_state_fingerprints": len(fingerprints),
        "unique_workflow_run_ids": len(workflow_run_ids),
        "unique_trace_run_ids": len(trace_run_ids),
        "unique_trace_artifacts": len(trace_artifacts),
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
    journal_path = root / "journal.sqlite3"
    journal = CaseJournal(journal_path)
    queue = JobQueue(journal_path)
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

    queue_job_id = queue.enqueue(
        case_id,
        "distributed-workflow",
        {"rows_sha256": input_manifest["rows_sha256"]},
    )
    first_claim = queue.claim("worker-a", lease_seconds=0.05)
    if first_claim is None or first_claim["job_id"] != queue_job_id:
        raise RuntimeError("Initial durable queue claim failed")
    time.sleep(0.08)
    second_claim = queue.claim("worker-b", lease_seconds=30.0)
    if (
        second_claim is None
        or second_claim["job_id"] != queue_job_id
        or second_claim["attempts"] != 2
    ):
        raise RuntimeError("Expired queue lease was not reclaimed correctly")
    queue.complete(
        queue_job_id,
        "worker-b",
        {"recovered": True, "case_id": case_id},
    )
    queue_state = queue.get(queue_job_id)
    if queue_state["status"] != "ok":
        raise RuntimeError("Reclaimed queue job did not complete")
    journal.event(
        case_id,
        "fault.queue_lease_recovered",
        {
            "job_id": queue_job_id,
            "attempts": queue_state["attempts"],
        },
    )

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
    worker_env["HEARTTWIN_REHEARSAL_IDEMPOTENCY_DB"] = str(
        root / "worker-idempotency.sqlite3"
    )
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

        concurrent_probe = _run_concurrent_case_probe(
            distributed,
            journal_path=journal_path,
            artifacts=artifacts,
            base_case_id=case_id,
        )

        def execute_branch_probes() -> dict[str, Any]:
            probes: dict[str, Any] = {}
            probes["CardiAnatomy"] = distributed.capability(
                "anatomy.presets"
            ).invoke("anatomy.presets", {})
            probes["CardiEP"] = distributed.capability(
                "ep.backends"
            ).invoke("ep.backends", {})
            probes["CardiInfer"] = distributed.capability(
                "infer.backends"
            ).invoke("infer.backends", {})
            probes["CardiMech"] = distributed.capability(
                "mechanics.health"
            ).invoke(
                "mechanics.health",
                {"entity_id": case_id},
            )
            probes["CardiStudio"] = distributed.capability(
                "design.generate"
            ).invoke(
                "design.generate",
                {
                    "entity_id": case_id,
                    "factors": {"condition": ["probe"]},
                    "replicates": 1,
                },
            )
            probes["DCCP"] = distributed.capability(
                "host.map"
            ).invoke(
                "host.map",
                {
                    "entity_id": case_id,
                    "module_scores": {},
                },
            )

            if "cine_cmr_biventricular" not in probes["CardiAnatomy"].get(
                "presets", {}
            ):
                raise RuntimeError("CardiAnatomy distributed probe failed")
            if not probes["CardiEP"].get("backends"):
                raise RuntimeError("CardiEP distributed probe failed")
            if not probes["CardiInfer"].get("backends"):
                raise RuntimeError("CardiInfer distributed probe failed")
            if probes["CardiMech"].get("service") != "CardiMech":
                raise RuntimeError("CardiMech distributed probe failed")
            if probes["CardiMech"].get("status") not in {"ok", "degraded"}:
                raise RuntimeError("CardiMech distributed probe returned invalid status")
            if probes["CardiStudio"].get("n_runs") != 1:
                raise RuntimeError("CardiStudio distributed probe failed")
            if not isinstance(probes["DCCP"], dict):
                raise RuntimeError("DCCP distributed probe failed")
            return probes

        branch_probes, _ = journal.run_stage(
            case_id,
            "distributed-branch-probes",
            {
                "services": [
                    "CardiAnatomy",
                    "CardiEP",
                    "CardiInfer",
                    "CardiMech",
                    "CardiStudio",
                    "DCCP",
                ]
            },
            execute_branch_probes,
        )

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

        # Prove the real ServiceAdapter retry loop can bridge a temporary outage.
        workers["CardiAtlas"].stop()
        retry_env_names = (
            "HEARTTWIN_HTTP_ATTEMPTS",
            "HEARTTWIN_HTTP_TIMEOUT_SECONDS",
            "HEARTTWIN_HTTP_BACKOFF_SECONDS",
        )
        retry_env_before = {
            name: os.environ.get(name)
            for name in retry_env_names
        }
        os.environ["HEARTTWIN_HTTP_ATTEMPTS"] = "6"
        os.environ["HEARTTWIN_HTTP_TIMEOUT_SECONDS"] = "0.5"
        os.environ["HEARTTWIN_HTTP_BACKOFF_SECONDS"] = "0.1"

        restart_errors: list[BaseException] = []

        def delayed_atlas_restart() -> None:
            try:
                time.sleep(0.25)
                workers["CardiAtlas"].start()
            except BaseException as exc:  # pragma: no cover - surfaced below
                restart_errors.append(exc)

        restart_thread = threading.Thread(
            target=delayed_atlas_restart,
            name="rehearsal-atlas-restart",
        )
        restart_thread.start()
        try:
            retry_result = recovered.invoke(
                "atlas.search",
                {
                    "entity_id": case_id,
                    "query": f"{case_id}-retry-recovery",
                    "records": [],
                },
            )
        finally:
            restart_thread.join(timeout=30)
            for name, previous in retry_env_before.items():
                if previous is None:
                    os.environ.pop(name, None)
                else:
                    os.environ[name] = previous

        if restart_thread.is_alive():
            raise RuntimeError("Delayed CardiAtlas restart thread did not finish")
        if restart_errors:
            raise RuntimeError(
                f"Delayed CardiAtlas restart failed: {restart_errors[0]}"
            )
        if retry_result.get("query") != f"{case_id}-retry-recovery":
            raise RuntimeError("HTTP retry did not recover through temporary outage")
        journal.event(
            case_id,
            "fault.http_retry_recovered",
            {"service": "CardiAtlas"},
        )

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

        workers["CardiAnatomy"].restart()
        third, third_cached = _post_json(
            duplicate_url,
            duplicate_payload,
            idempotency_key="duplicate-probe",
        )
        if third != first or not third_cached:
            raise RuntimeError(
                "Successful idempotency result did not survive worker restart"
            )
        journal.event(
            case_id,
            "fault.idempotency_survived_restart",
            {"service": "CardiAnatomy"},
        )

        drop_payload = {
            "entity_id": case_id,
            "_rehearsal_drop_response_once": True,
        }
        anatomy_health = distributed.capability("anatomy.health")
        if anatomy_health is None:
            raise RuntimeError("anatomy.health route disappeared")
        drop_result = anatomy_health.invoke(
            "anatomy.health",
            drop_payload,
        )
        if drop_result.get("status") != "ok":
            raise RuntimeError(
                "Dropped-response retry did not recover a valid anatomy health result"
            )
        drop_key = sha256(
            {
                "service": "CardiAnatomy",
                "capability": "anatomy.health",
                "payload": drop_payload,
            }
        )
        durable_idempotency = IdempotencyStore(
            root / "worker-idempotency.sqlite3"
        )
        persisted_drop = durable_idempotency.get(
            "CardiAnatomy",
            drop_key,
            "anatomy.health",
            {"entity_id": case_id},
        )
        if persisted_drop != drop_result:
            raise RuntimeError(
                "Dropped-response result was not recovered from durable idempotency"
            )
        journal.event(
            case_id,
            "fault.response_drop_recovered",
            {"service": "CardiAnatomy", "idempotency_key": drop_key},
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

        workflow_seed = 6
        simulation_config = {
            "preset": "mi",
            "n_cells": 12,
            "duration": 1.0,
            "dt": 0.25,
        }
        workflow_payload = {
            "case_id": case_id,
            "rows_sha256": sha256(rows),
            "observations": [
                observation.model_dump(mode="json")
                for observation in observations
            ],
            "feature_columns": ["gene_a", "gene_b"],
            "benchmark_policy": "subject_heldout",
            "seed": workflow_seed,
            "simulation": simulation_config,
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
                simulation=dict(simulation_config),
                seed=workflow_seed,
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
            "http_retry_recovered": True,
            "duplicate_suppressed": second_cached,
            "idempotency_survived_worker_restart": third_cached,
            "response_drop_recovered_from_durable_result": True,
            "artifact_corruption_detected": True,
            "durable_replay_reused": replay_reused,
            "queue_lease_recovered": True,
            "queue_job_id": queue_job_id,
            "queue_attempts": queue_state["attempts"],
            "branch_probes_passed": sorted(branch_probes),
            "concurrent_cases": concurrent_probe,
            "multi_case_isolation_verified": concurrent_probe[
                "case_isolation_verified"
            ],
            "duplicate_case_submission_collapsed": concurrent_probe[
                "duplicate_submission_collapsed"
            ],
            "coordinator_restart_recovered": concurrent_probe[
                "coordinator_restart_recovered"
            ],
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
