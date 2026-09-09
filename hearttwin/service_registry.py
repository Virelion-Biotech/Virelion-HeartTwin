from __future__ import annotations

import json
import os
import shutil
import subprocess
import urllib.request
from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class ServiceSpec:
    name: str
    repository: str
    capabilities: tuple[str, ...]
    endpoint: str | None = None
    command: str | None = None
    optional: bool = True
    path_template: str = "/v1/{capability}"


class ServiceAdapter:
    def __init__(self, spec: ServiceSpec):
        self.spec = spec

    def available(self) -> bool:
        if self.spec.endpoint:
            try:
                with urllib.request.urlopen(self.spec.endpoint.rstrip("/") + "/health", timeout=2):
                    return True
            except Exception:
                return False
        if self.spec.command:
            first_token = self.spec.command.split()[0] if self.spec.command.strip() else ""
            return shutil.which(first_token) is not None
        return False

    def _request_path(self, capability: str) -> str:
        capability_path = capability.replace(".", "/")
        return self.spec.path_template.format(
            capability=capability_path,
            capability_leaf=capability.rsplit(".", 1)[-1],
        )

    def invoke(self, capability: str, payload: dict[str, Any]) -> dict[str, Any]:
        if capability not in self.spec.capabilities:
            raise ValueError(f"{self.spec.name} does not advertise {capability}")
        if self.spec.endpoint:
            req = urllib.request.Request(
                self.spec.endpoint.rstrip("/") + self._request_path(capability),
                data=json.dumps(payload).encode(),
                headers={"Content-Type": "application/json"},
                method="POST",
            )
            with urllib.request.urlopen(req, timeout=120) as response:
                return json.loads(response.read())
        if self.spec.command:
            env = os.environ.copy()
            env["HEARTTWIN_PAYLOAD"] = json.dumps(payload)
            process = subprocess.run(
                self.spec.command,
                shell=True,
                capture_output=True,
                text=True,
                timeout=600,
                env=env,
            )
            if process.returncode:
                raise RuntimeError(process.stderr.strip() or f"{self.spec.name} failed")
            return json.loads(process.stdout) if process.stdout.strip() else {}
        raise RuntimeError(f"Service {self.spec.name} has no endpoint or command")


class ServiceRegistry:
    def __init__(self, specs: list[ServiceSpec]):
        self.adapters = {s.name: ServiceAdapter(s) for s in specs}

    def services(self) -> list[ServiceSpec]:
        return [a.spec for a in self.adapters.values()]

    def capability(self, capability: str) -> ServiceAdapter | None:
        for adapter in self.adapters.values():
            if capability in adapter.spec.capabilities:
                return adapter
        return None

    def doctor(self) -> dict[str, bool]:
        return {name: adapter.available() for name, adapter in self.adapters.items()}


def observations_for(payload: dict[str, Any], modality: str) -> list[dict[str, Any]]:
    """Return observations whose modality is exactly the requested family."""
    return [
        observation
        for observation in payload.get("observations", [])
        if observation.get("modality") == modality
    ]
