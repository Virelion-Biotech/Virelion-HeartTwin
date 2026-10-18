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
