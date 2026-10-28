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

## Use the hosted HeartTwin application

The repository now includes a one-origin hosted deployment path: the same service serves the interactive workbench and its API, runs the job worker, and persists cases, uploads and run artifacts on a mounted disk. End users do not enter a worker URL or install Python.

For the one-time owner deployment, use [HOSTED_HEARTTWIN.md](HOSTED_HEARTTWIN.md). It creates a private single-owner research instance and adds a password sign-in flow with expiring browser sessions. The Render service uses the repository’s pinned component stack and a persistent disk; its available-module list remains the source of truth for what actually runs.

This is not a multi-tenant sandbox. Do not share the owner password or use the default hosted instance for identifiable clinical data. Remote use must remain HTTPS, and the hosted app is not clinically qualified. GitHub Pages is an entry point only: it cannot run Python or native scientific solvers, so it opens the full application on the hosted Python service.

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


## Researcher and Doctor entry paths

The first screen separates **Researcher** and **Doctor** workspaces. Both roles can choose
one of three contexts: a patient-specific case, a typical adult male reference context,
or a typical adult female reference context. The selected role and context are shown in
the workspace banner and included in run labels for traceability; this UI context does
not alter solver inputs or silently assign physiology.

The patient-specific path opens **Patient models**, where users create/open a pseudonymous
case, attach reviewed acquisitions and record whether model inputs are measured, estimated,
assumed, population priors or unknown. User-provided source data and the actual selected
geometry/solver configuration remain authoritative.

The typical adult male/female options are **reference-context choices**, not a claim that
two validated sex-specific physiological digital twins are packaged. The current bundled
anatomy display is a simplified adult-male source surface; it is not a simulation-ready
volume mesh. A validated adult-female anatomical/physiological model is not bundled.
Before simulation, import a compatible, appropriately licensed model and supply explicit
physiological parameters. Selecting a reference phenotype alone never modifies mesh,
conductivities, conduction velocities or other model parameters.

The Doctor path is for organizing research cases and exploring model-based hypotheses.
**HeartTwin is not validated for clinical diagnosis or treatment decisions**, and a patient-
specific record is not automatically a clinically qualified digital twin. Use only an
appropriately authorized deployment for sensitive data; remove direct identifiers from
research uploads and follow local governance requirements.

