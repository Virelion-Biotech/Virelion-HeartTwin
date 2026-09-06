from __future__ import annotations
import os, subprocess, urllib.request, json
from dataclasses import dataclass
from typing import Any, Callable

@dataclass(frozen=True)
class ServiceSpec:
    name: str
    repository: str
    capabilities: tuple[str, ...]
    endpoint: str | None = None
    command: str | None = None
    optional: bool = True

class ServiceAdapter:
    def __init__(self, spec: ServiceSpec): self.spec = spec
    def available(self) -> bool:
        if self.spec.endpoint:
            try:
                with urllib.request.urlopen(self.spec.endpoint.rstrip('/') + '/health', timeout=2): return True
            except Exception: return False
        if self.spec.command: return True
        return False
    def invoke(self, capability: str, payload: dict[str, Any]) -> dict[str, Any]:
        if capability not in self.spec.capabilities:
            raise ValueError(f"{self.spec.name} does not advertise {capability}")
        if self.spec.endpoint:
            req = urllib.request.Request(self.spec.endpoint.rstrip('/') + '/v1/' + capability.replace('.', '/'),
                data=json.dumps(payload).encode(), headers={'Content-Type':'application/json'}, method='POST')
            with urllib.request.urlopen(req, timeout=120) as r: return json.loads(r.read())
        if self.spec.command:
            env = os.environ.copy(); env['HEARTTWIN_PAYLOAD'] = json.dumps(payload)
            p = subprocess.run(self.spec.command, shell=True, capture_output=True, text=True, timeout=600, env=env)
            if p.returncode: raise RuntimeError(p.stderr.strip() or f"{self.spec.name} failed")
            return json.loads(p.stdout) if p.stdout.strip() else {}
        raise RuntimeError(f"Service {self.spec.name} has no endpoint or command")

class ServiceRegistry:
    def __init__(self, specs: list[ServiceSpec]): self.adapters = {s.name: ServiceAdapter(s) for s in specs}
    def services(self) -> list[ServiceSpec]: return [a.spec for a in self.adapters.values()]
    def capability(self, capability: str) -> ServiceAdapter | None:
        for a in self.adapters.values():
            if capability in a.spec.capabilities: return a
        return None
    def doctor(self) -> dict[str, bool]: return {n: a.available() for n,a in self.adapters.items()}
