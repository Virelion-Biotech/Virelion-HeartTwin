from pathlib import Path
import hashlib, io, json, platform, urllib.request
import numpy as np
import scipy
from scipy.io import loadmat
OUT = Path("/content/HeartTwin_external_evidence_2026_10_21")
OUT.mkdir(exist_ok=True)
PIN = "2da0cac9b13b1d3b6eb41e7a00ffde3c78e9c8a4"
BASE = f"https://raw.githubusercontent.com/mjcolebank/Colebank_2023_AcuteIschemia/{PIN}/"
EXPECTED = {
"m1_timeseries_final.mat":"bfc57850915d37043aa63165b3ffc699c9dea4b2",
"m1_mean_var.mat":"267aba9c71ee616cc98ba5eacbfd2b09c66f2199",
"m2_timeseries_final.mat":"a67d4e28bc50556178d3e3df0fb3f2e313800460",
"m2_mean_var.mat":"56dc550c72e5fed1a9dbb422b3b2a6dec6eab8df",
"m3_timeseries_final.mat":"85e850422c0570a035107a39761817217b66052b",
"m3_mean_var.mat":"1f54e8451aa80cba4d88857261620761803a0f5d"}
protocol = {"source_pin":PIN, "development_subjects":["mouse1"],
"reserved_subjects":["mouse2","mouse3"], "reserved_values_opened":False,
"purpose":"Source/format qualification only; no HeartTwin prediction or validation score.",
"species":"mouse", "LV_RV_acquisition":"sequential catheter recordings, not simultaneous",
"time_axis":"publisher phase-normalized cycles, not raw acquisition timestamps",
"measured_flow_available":False,"KIT_parameters_changed":False}
(OUT/"protocol.json").write_text(json.dumps(protocol,indent=2))
receipts = {}
for name, expected in EXPECTED.items():
    raw=urllib.request.urlopen(BASE+"MainCode/matfiles_to_load/"+name,timeout=60).read()
    blob=hashlib.sha1(b"blob "+str(len(raw)).encode()+b"\0"+raw).hexdigest()
    assert blob == expected, (name,blob,expected)
    (OUT/name).write_bytes(raw)
    receipts[name]={"bytes":len(raw),"sha256":hashlib.sha256(raw).hexdigest(),"git_blob":blob}
(OUT/"source_receipts.json").write_text(json.dumps(receipts,indent=2))
schema={}
for name in ["m1_timeseries_final.mat","m1_mean_var.mat"]:
    mat=loadmat(OUT/name)
    schema[name]={k:{"shape":list(v.shape),"dtype":str(v.dtype)} for k,v in mat.items() if not k.startswith("__")}
(OUT/"development_schema.json").write_text(json.dumps(schema,indent=2))
print("SOURCE CHECKS: six exact Git blobs verified; mouse2/3 values NOT opened.")
print(json.dumps(schema,indent=2))
print("ENVIRONMENT",platform.python_version(),np.__version__,scipy.__version__)
