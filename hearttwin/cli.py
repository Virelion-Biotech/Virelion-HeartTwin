from __future__ import annotations
import argparse, json
from .config import load_registry
from .orchestrator import HeartTwin

def main() -> None:
    p=argparse.ArgumentParser(prog="hearttwin")
    sub=p.add_subparsers(dest="cmd", required=True)
    sub.add_parser("services")
    sub.add_parser("doctor")
    d=sub.add_parser("demo"); d.add_argument("--output", default="outputs/demo-state.json")
    r=p.parse_args(); registry=load_registry()
    if r.cmd == "services":
        for s in registry.services(): print(f"{s.name}\t{','.join(s.capabilities)}\t{s.repository}")
    elif r.cmd == "doctor":
        print(json.dumps(registry.doctor(), indent=2))
    else:
        import pathlib
        pathlib.Path(r.output).parent.mkdir(parents=True, exist_ok=True)
        run=HeartTwin(registry).run("demo-entity", capabilities=[])
        pathlib.Path(r.output).write_text(run.model_dump_json(indent=2))
        print(r.output)

if __name__ == "__main__": main()
