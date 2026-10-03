# HeartTwin integration contract

## One front door

HeartTwin exposes one Python object:

```python
from hearttwin import load_registry
from hearttwin.api import VirelionServices

services = VirelionServices(load_registry())

# Individual service calls
services.electrical("sample-001", input_path="recording.csv")
services.mechanical("sample-001", input_path="video.mp4")
services.imaging("sample-001", input_path="images/")
services.safety("sample-001", input_path="mea.csv")
services.learn("sample-001", features={})
services.simulate("sample-001", scenario={})
services.evaluate("sample-001", submission={})

# Or run a composed twin workflow
run = services.run_twin("sample-001", context={"species":"human"})
```

## Service ownership

HeartTwin owns orchestration, common state, capability discovery, provenance attachment, error handling and the user-facing API. It does **not** copy specialist algorithms into this repository.

| Capability | Source repository |
|---|---|
| `atlas.*` | CardiAtlas |
| `benchmark.*` | CardiBench |
| `evaluation.*` | CardiEval |
| `electrical.*` | ElectroTrace |
| `mechanical.*` | MyoTrace |
| `imaging.*` | OptiCell |
| `safety.*` | CardioScore |
| `learn.*` | CardiLearn |
| `ep.*` | CardiEP |
| `infer.*` | CardiInfer |
| `flow.*` | CardiFlow |
| `simulation.*` | CardiSim |
| `therapy.*` | CardiTherapy |
| `trace.*` | CardiTrace |
| `bridge.*` | CardiBridge |
| `agent.*` | CardiAgent |
| `vex.*` | CardiVex |

## Adapter input convention

Every native adapter must select its inputs from the canonical HeartTwin payload rather than receiving the entire observation set and guessing which values belong to it.

An input-relevant observation uses a modality matching the capability family (`electrical`, `mechanical`, `imaging`, `safety`, `molecular`, etc.). The concrete file, directory, URI, or other primary input is carried under `Observation.values.input_path`. Additional service-specific parameters may be stored alongside it in the same `values` mapping.

For example:

```json
{
  "entity_id": "sample-001",
  "context": {"species": "human"},
  "observations": [
    {
      "observation_id": "obs-mechanics-001",
      "modality": "mechanical",
      "values": {
        "input_path": "video.mp4",
        "fps": 100
      },
      "provenance": {"source_service": "experiment", "run_id": "run-001"},
      "status": "observed"
    }
  ]
}
```

HeartTwin exposes `observations_for(payload, modality)` for adapters and integration code that need a deterministic modality filter. Adapters should fail with a clear input-contract error when the required modality/input is absent; they must not silently use an unrelated observation.

## Deployment modes

1. **HTTP services**: set the corresponding `*_URL` environment variable. The adapter calls `/health` and the configured service `path_template`, which defaults to `/v1/<capability path>`.
   - CardiAtlas's HTTP fallback is `CARDIATLAS_URL` and is expected to expose `/v1/atlas/search` and `/v1/atlas/context` with the same response shapes as the native adapter.
   - When CardiAtlas is installed locally, `CARDIATLAS_DB=/path/to/atlas.sqlite` optionally points the native adapter at a persistent Atlas. The path must already exist and be a file; HeartTwin will not silently create an empty database for a mistyped path. Request-local `records` are overlaid on the loaded in-memory service and are not written back to the database.
2. **Local services**: replace `endpoint` with a command in `configs/services.yaml`; HeartTwin sends the canonical request in `HEARTTWIN_PAYLOAD` and expects JSON on stdout. Command availability is checked with `shutil.which` using the first command token.
3. **Unavailable services**: no endpoint/command is required. The capability is returned as `unavailable`, preserving the rest of the run.

This abstraction means a specialist repository can change its internal implementation without forcing researchers to rewrite their HeartTwin workflow, provided its published adapter contract remains compatible.

## CardiBridge route exceptions

Most HTTP services use the default `/v1/{capability}` route transformation, where dots become slashes. CardiBridge currently exposes leaf routes such as `/v1/validate`; its HeartTwin registration therefore uses `path_template: /v1/{capability_leaf}` rather than forcing CardiBridge to rename its existing routes.

## Fail-closed scientific behavior

A missing modality is represented as missing. It is never converted to zero, negative, healthy, normal or any other biological conclusion. Adapter errors are recorded as errors and remain visible in the run report.


## CardiInfer inverse-model boundary

HeartTwin advertises CardiInfer's `infer.health`, `infer.backends`, `infer.ecosystem`,
`infer.run`, and `infer.propagate` capabilities. HeartTwin selects and routes the
inverse problem; CardiInfer owns priors, observation likelihood/discrepancy semantics,
posterior or MAP artifacts, convergence/identifiability diagnostics, and posterior
uncertainty propagation.

Generic CardiInfer backends may call another HeartTwin-compatible model service through
the forward-model contract embedded in `InferenceRequest.model_context.forward_model`.
That keeps forward physics in domain services such as CardiEP/CardiMech/CardiFlow while
CardiInfer remains solver-neutral. The stack integration CI pins the exact CardiInfer
revision used for these contracts.


## Flow and therapy readiness boundary

CardiFlow and CardiTherapy are registered as native/HTTP-capable services. CardiFlow ships the deterministic `windkessel-3element-v1` reduced-order afterload backend; its outputs are software-checked reference hemodynamics, not CFD or patient validation. CardiTherapy has zero default intervention backends and `therapy.run` fails closed. Unsupported Flow backends also fail closed.


## CardiBench intelligence boundary

HeartTwin routes `benchmark.health`, `benchmark.search`, `benchmark.catalog`, `benchmark.discover`, `benchmark.result.record`, and `benchmark.results` in addition to `benchmark.resolve`. Discovery/search/catalog are control-plane capabilities and never become biological observations. Only a resolved benchmark used by the current workflow enters `CardiacState`.

The multimodal workflow closes the loop as `CardiBench → CardiLearn → CardiEval → CardiBench result history → CardiTrace`. CardiBench owns benchmark/result identity; CardiEval scoring; CardiBridge transport; CardiTrace lineage; HeartTwin orchestration.
