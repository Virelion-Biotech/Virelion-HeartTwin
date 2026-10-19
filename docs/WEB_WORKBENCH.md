# Unified HeartTwin workbench

The workbench puts all 22 registered services in one interface, with uploaded inputs,
capability launchers, explicit sequential workflows, the existing mechanistic workflow,
persistent execution history, logs and downloadable results. It invokes the existing
HeartTwin adapters; it does not replace their scientific contracts or validation gates.
CardiLearn is visible but cannot execute through this gateway while its data/model
qualification gates remain incomplete. Other modules execute only if installed on the
connected worker, with the inputs and backend configuration that their contracts require.
Advanced capabilities currently use editable JSON; availability does not mean every
capability has a guided form or has passed independent scientific validation.

## Run everything on your computer or research server

Use Python 3.10–3.12 and a virtual environment. From the HeartTwin repository:

```bash
python -m pip install -e '.[dev]'
python -m pip install -r requirements-services.txt
hearttwin-web
```

The second command installs the pinned component stack. Platform-specific dependencies,
external backend credentials, model assets and GPU resources still belong on the worker.
Keep its virtual environment active so command adapters are on `PATH`.

Open `http://127.0.0.1:8787`, click **Connect worker**, and enter the connection token
printed in your terminal. The local worker URL is prefilled. The token stays in the
browser tab's memory and must be entered again after a refresh. You can also start the
server with `python -m hearttwin.web`.

1. Upload an input through **Files** or a module launcher.
2. Select a module and capability, then configure its request. ElectroTrace, MyoTrace,
   OptiCell and CardioScore have file-based starter forms. Read the selected module's
   documentation for its accepted input schema and scientific scope.
3. Submit the request and inspect **Runs & results**. A failed module stays failed;
   its log is available for diagnosis.
4. Download the result JSON and other output artifacts. Output integrity is checked
   against the fingerprints recorded when execution completed.

The default workspace is `hearttwin-workspace/`. It contains `jobs.sqlite3`, `uploads/`
and per-job `runs/`. Preserve the entire directory to retain input identities, receipts,
requests, logs and results. Choose another directory with `--workspace /absolute/path`.
Backups and retention are your worker operator's responsibility.

## Use the private hosted interface

The hosted website is the same frontend. Static hosting does not provide Python,
installed scientific modules, GPU execution or permanent storage for research jobs.
To run from that website, connect it to a persistent worker you operate over HTTPS.
A temporary development runtime is not a permanent compute deployment.

Put the gateway behind an HTTPS reverse proxy and configure its exact frontend origin
and worker hostname. For example, after setting a strong `HEARTTWIN_WEB_TOKEN` environment
variable of at least 32 characters through your server's secret manager:

```bash
hearttwin-web --host 127.0.0.1 --port 8787 \
  --public-host worker.example.org \
  --allow-origin https://virelion-hearttwin-workbench.poeticridge35.chatgpt.site \
  --workspace /srv/hearttwin/workspace
```

Forward `https://worker.example.org` to `127.0.0.1:8787`, preserving the public Host
header. In the website, enter that HTTPS URL and your token. A separate frontend domain
requires its own exact `--allow-origin`. Do not put tokens in URLs, source files or
frontend bundles. Remote binding without an explicitly supplied token is rejected.
A browser may impose local-network restrictions when a hosted page accesses localhost;
use the local interface for local execution, or a properly secured HTTPS worker.

This gateway trusts one owner. Its adapters can access that owner's worker files and
execute installed numerical programs. Subprocess isolation is for job lifetime/logs,
not an operating-system security sandbox. Do not expose it as a shared public or
multi-tenant service. Public/private website access does not authenticate the worker;
the worker requires its own bearer token for every API call.

## Workflow configuration

A sequential workflow requires explicit capability calls. Use `{"$upload":"file-id"}`
where a native payload expects an input path. Use `{"$step":"first.outputs.0"}` to
insert a specific output from an earlier step into a later request. No implicit schema
conversion or biological correspondence is inferred. Step IDs must be unique simple
names. The builder can import/export the complete request as JSON.

The mechanistic workflow calls `run_mechanistic_twin_workflow` and requires all eight
participating services. Provide a complete configuration matching that function's
contract, including anatomy, EP parameters, mechanics calibration, flow conditions,
therapy settings and evaluation reference outcomes. The starter JSON intentionally
contains empty fields rather than invented patient inputs or reference measurements.
CardiLearn-dependent multimodal fusion/superstack workflows are not exposed.

## Execution and limits

- One serial worker; each job runs in a separate Python subprocess.
- Persistent queued/running/completed/failed/cancelled status in SQLite.
- Idempotency keys prevent duplicate submission of an identical request.
- Queued jobs can be cancelled. Running jobs stop on worker shutdown or timeout.
- Default timeout: 3,600 seconds, configurable with `--timeout` up to 86,400 seconds.
- An expired execution lease becomes an explicit error and is not silently retried.
  Inspect partial outputs before deliberately resubmitting interrupted scientific work.
- Default upload limit: 256 MiB per file; job JSON limit: 2 MiB; sequence limit: 32 steps.
- HTTP artifact download limit: 64 MiB per artifact. Retrieve larger outputs on the worker.
- Execution logs display their last 24 KB. Full logs remain in the job directory.
- The run list shows the most recent 200 jobs; older records remain in SQLite.
- The frontend's request timeout is 30 seconds; job execution continues independently.
- Completed result and artifact fingerprints are checked before delivery. Partial outputs
  from failed/running jobs are not completion-qualified evidence.

The API is deliberately small: authenticated catalog, input upload/list, job submit/list,
job detail, queued cancellation and artifact download. Static UI assets require no token;
research data and job operations do. Allowed origins and Host headers are checked, and
neither credentials nor compute endpoints are baked into the published frontend.

A successful run confirms software execution. It does not qualify biological predictions
or clinical use. Scientific gates and module limitations remain attached to the research
outputs.

## Model workspace

The default view is a dark modeling workstation with a structure explorer, interactive
3D surface display, view properties, workflow stages and an execution monitor. The
default heart is an attributed BodyParts3D adult male anatomical reference, with
11 selectable source-mapped groups. It is a simplified surface for visualization,
not a patient reconstruction, computed result or simulation-ready cardiac volume mesh.
See [anatomy provenance and license](anatomy/README.md). **Show schematic** retains
the original six-layer procedural illustration as an explicitly labeled optional view.

Use **Import geometry** to inspect a JSON file locally in the browser. This action
does not upload the file or submit a job. The viewer accepts zero-based triangular
surfaces or tetrahedral meshes; it extracts exterior faces from tetrahedra and rejects
non-manifold faces shared by more than two tetrahedra. For example:

```json
{
  "geometry": {
    "node_xyz": [[0,0,0], [1,0,0], [0,1,0], [0,0,1]],
    "tetrahedra": [[0,1,2,3]],
    "units": {"node_xyz": "mm"}
  },
  "activation_ms": [0, 10, 15, 20]
}
```

This tetrahedron is a software-format example, not a heart model. A surface file may
use `triangles` instead of `tetrahedra`. Coordinate units are displayed as unspecified
if absent. The viewer fits geometry using one uniform scale without altering the
stored input; arbitrary axes are X/Y/Z, not inferred patient anatomical orientations.
Camera presets refer to display directions and do not register imported anatomy.

Drag or use arrow keys to rotate; scroll or use plus/minus to zoom. Surface/mesh mode,
structure visibility, opacity, ground grid and display clipping work locally. Clipping
removes surface faces across a display plane; it does not create a capped cross-section
or provide volumetric measurements. **Fit** resets the camera, and **Reset display
settings** restores visibility and rendering controls.

Activation colors and playback appear only when `activation_ms` is supplied with one
finite value per geometry node in matching order. No heart motion, ECG or physiological
measurements are invented. In **Runs & results**, **Open in model view** can inspect a
completed run with inline `request.payload.geometry` and its result's matching activation
array. Geometry referenced only by a worker-side filename must be imported separately
with its corresponding result data; the frontend does not guess node correspondence.

Current browser limits are 16 MiB per geometry JSON file, 30,000 vertices, 60,000 exterior
surface faces and 80,000 input tetrahedra. This lightweight surface inspector does not
replace commercial high-density volumetric rendering, segmentation or model preparation.
Larger artifacts remain downloadable or accessible on the worker. Local geometry is
kept only in the current tab's memory. The bundled anatomical reference is compressed
for delivery and verified before decoding; it works without a compute connection.

See [UI design references](UI_DESIGN_REFERENCES.md) for the public commercial materials
used to inform the workstation layout.

## Task-oriented analyses

The hosted workbench starts with recording and image tasks, rather than package names.
`hearttwin/webui/tasks.js` maps every registered service to a descriptive task, required
input, expected output and an explicit default operation. Analysis search includes data
types and research questions; less common evidence and data tools are expandable.
Package names and operation selectors remain available in Technical settings.

ECG, video/TIFF motion, microscopy QC and MEA scoring retain their real command-adapter
file handoff. A selected upload is required before submission. Non-file model operations
start with an empty configuration and cannot be submitted until inputs are supplied.
The displayed atlas is never silently used as a simulation mesh. No measurements or
clinical conclusions are fabricated by the UI. Scientific validity still depends on the
selected model, input quality, and independent evaluation.

Validation: `node --test tests/webui_tasks.test.cjs tests/webui_geometry.test.cjs` checks
registered operation routing, search, task availability and geometry contracts.
