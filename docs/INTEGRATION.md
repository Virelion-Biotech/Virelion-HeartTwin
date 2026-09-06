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
| `simulation.*` | CardiSim |
| `trace.*` | CardiTrace |
| `bridge.*` | CardiBridge |
| `agent.*` | CardiAgent |
| `vex.*` | CardiVex |

## Deployment modes

1. **HTTP services**: set the corresponding `*_URL` environment variable. The adapter calls `/health` and `/v1/<capability path>`.
2. **Local services**: replace `endpoint` with a command in `configs/services.yaml`; HeartTwin sends the canonical request in `HEARTTWIN_PAYLOAD` and expects JSON on stdout.
3. **Unavailable services**: no endpoint/command is required. The capability is returned as `unavailable`, preserving the rest of the run.

This abstraction means a specialist repository can change its internal implementation without forcing researchers to rewrite their HeartTwin workflow, provided its published adapter contract remains compatible.

## Fail-closed scientific behavior

A missing modality is represented as missing. It is never converted to zero, negative, healthy, normal or any other biological conclusion. Adapter errors are recorded as errors and remain visible in the run report.
