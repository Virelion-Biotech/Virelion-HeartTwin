"""Offline synthetic patient UI fixture, for visual review without exposing a worker.

Uses the actual packaged HTML/CSS/app.js with fixture reads. It never runs models,
modifies patient records or contacts an API. This is not a live-worker E2E test.
"""
from __future__ import annotations

import argparse
import base64
import json
from pathlib import Path
import re


def render(root):
    ui = Path(root) / "hearttwin/webui"
    case = {"patient_id": "SYNTHETIC-UI", "encounters": [{"encounter_id": "baseline", "physiologic_state": {"rhythm": "synthetic sinus rhythm", "medications": "synthetic fixture"}}],
            "acquisitions": [{"acquisition_id": "pressure-baseline", "modality": "pressure"}],
            "model_inputs": [{"input_id": "pressure", "variable": "outlet_pressure", "status": "MEASURED", "unit": "mmHg"}],
            "evidence": [{"acquisition_id": "pressure-baseline", "role": "calibration"}]}
    routes = {"/jobs": {"jobs": []}, "/files": {"files": []}, "/patients": {"patients": [{"patient_id": "SYNTHETIC-UI", "revision": 1}]},
              "/patients/SYNTHETIC-UI": {"patient_id": "SYNTHETIC-UI", "revision": 1, "case": case},
              "/patients/SYNTHETIC-UI/readiness": {"ep": {"ready_for_ep_reconstruction": False, "blocking_issues": ["No patient anatomy or ECG in this synthetic UI fixture."], "warnings": []}},
              "/patients/SYNTHETIC-UI/history": {"revisions": [{"revision": 1, "actor": "synthetic-review", "reason": "Visual fixture only"}], "events": []},
              "/patients/SYNTHETIC-UI/runs": {"runs": []}}
    bootstrap = """
window.HeartTwinGeometry={init(){return {redraw(){},stop(){}}}};
const fixtureRoutes=ROUTES;
window.fetch=async function(url,options={}){
 if(options.method && options.method!=='GET')throw Error('Synthetic read-only visual fixture; saves and model runs are disabled.');
 if(url==='catalog.json')return new Response(JSON.stringify(CATALOG));
 const path=new URL(url).pathname.replace(/^\\/api/,'');
 if(!(path in fixtureRoutes))throw Error('Unavailable in the synthetic visual fixture: '+path);
 return new Response(JSON.stringify(fixtureRoutes[path]));
};
""".replace("ROUTES", json.dumps(routes)).replace("CATALOG", (ui / "catalog.json").read_text())
    app = (ui / "app.js").read_text().replace("state.catalog=c.services;initialize();", "state.catalog=c.services;initialize();state.connected=true;state.identity={role:'owner'};state.url='https://synthetic.fixture';state.token='synthetic-only';navigate('patients');openPatient('SYNTHETIC-UI');")
    html = (ui / "index.html").read_text()
    html = re.sub(r"<link[^>]+>", "", html)
    html = html.replace("</head>", "<style>" + (ui / "styles.css").read_text() + "</style></head>")
    html = re.sub(r'<script[^>]+src="(?:viewer|app)\.js"[^>]*></script>', "", html)
    logo = "data:image/png;base64," + base64.b64encode((ui / "virelion-logo.png").read_bytes()).decode()
    html = html.replace('src="virelion-logo.png"', 'src="' + logo + '"')
    html = html.replace('<main id="main">', '<main id="main"><p role="note" style="background:#fff2cc;padding:12px">SYNTHETIC UI REVIEW · Offline read-only fixture · No patient data or live worker</p>')
    return html.replace("</body>", "<script>" + bootstrap + app + "</script></body>")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    args.output.write_text(render(Path(__file__).resolve().parents[1]))
