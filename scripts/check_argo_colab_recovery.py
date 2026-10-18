import argparse
import json
import shutil
import subprocess
import sys
import types
from pathlib import Path

p = argparse.ArgumentParser(
    description="Real Colab notebook smoke/resume regression with ensurepip disabled"
)
p.add_argument(
    "--data",
    type=Path,
    required=True,
    help="Publisher-hashed ARGO cache (Pt1 P71/P184 + geometry minimum)",
)
p.add_argument(
    "--source",
    type=Path,
    required=True,
    help="SHA-verified source ZIP pinned in notebook",
)
p.add_argument("--work-dir", type=Path, required=True, help="Fresh scratch directory")
p.add_argument(
    "--notebook",
    type=Path,
    default=Path(__file__).resolve().parents[1]
    / "notebooks/09_HeartTwin_ARGO_Recovery_v3.ipynb",
)
opts = p.parse_args()
base = opts.work_dir.resolve() / "content"
base.mkdir(parents=True, exist_ok=True)
shim = base.parent / "python-no-ensurepip"
real = sys.executable
shim.write_text(
    "#!"
    + real
    + "\nimport sys,subprocess\nreal="
    + repr(real)
    + '\nif sys.argv[1:3] == ["-m","venv"]:\n code="import venv; venv.EnvBuilder._setup_pip=lambda self, context: (_ for _ in ()).throw(RuntimeError(\\"ensurepip unavailable (injected Colab regression)\\")); venv.main()"\n raise SystemExit(subprocess.call([real,"-c",code,*sys.argv[3:]]))\nraise SystemExit(subprocess.call([real,*sys.argv[1:]]))\n'
)
shim.chmod(0o755)
old = subprocess.run(
    [str(shim), "-m", "venv", str(base.parent / "old-venv")],
    check=False,
    capture_output=True,
    text=True,
)
assert old.returncode and "ensurepip unavailable" in old.stderr
print("REGRESSION REPRODUCED: default venv fails when ensurepip unavailable")
label = "09_HeartTwin_ARGO_v3_recovery"
root = base / "drive/MyDrive/HeartTwinValidation" / label
(root / "cache").mkdir(exist_ok=True, parents=True)
shutil.copyfile(opts.source, root / "cache/source.zip")
legacy = base / "drive/MyDrive/HeartTwinValidation/09_HeartTwin_ARGO_v1/data"
legacy.parent.mkdir(exist_ok=True, parents=True)
if not legacy.exists():
    legacy.symlink_to(opts.data.resolve(), target_is_directory=True)
colab = types.ModuleType("google.colab")
calls = []
colab.drive = types.SimpleNamespace(mount=lambda p: None)
colab.files = types.SimpleNamespace(download=lambda p: calls.append(p))
sys.modules["google.colab"] = colab
n = json.loads(opts.notebook.read_text())
code = (
    n["cells"][1]["source"]
    .replace('CONTENT = Path("/content")', "CONTENT = Path(" + repr(str(base)) + ")")
    .replace("SMOKE_RECORD_LIMIT = None", "SMOKE_RECORD_LIMIT = 2")
    .replace("sys.executable,", "str(" + repr(str(shim)) + "),")
)
exec(compile(code, "delivered_v3_notebook", "exec"), {"__name__": "__main__"})  # noqa: S102 - execute our notebook under test
s = json.loads((root / "RUN_STATUS.json").read_text())
assert s["status"] == "PAUSED" and s["records_verified"] == 2, s
assert json.loads((root / "CACHE_LOCATION.json").read_text())["reuse_v1"] is True
assert len(calls) == 1
page = root / "validation/patients/Pt1/page-000.json"
before = page.read_bytes()
mtime = page.stat().st_mtime_ns
exec(compile(code, "delivered_v3_notebook_resume", "exec"), {"__name__": "__main__"})  # noqa: S102 - same trusted notebook, resume check
assert before == page.read_bytes() and mtime == page.stat().st_mtime_ns
assert len(calls) == 2
print(
    "PASS: real isolated install, pip check, imports, two real records, ZIP hashes, Drive reuse and unchanged-page resume, with ensurepip forced unavailable"
)
