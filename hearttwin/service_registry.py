from __future__ import annotations

import http.client
import json
import os
import shutil
import shlex
import subprocess
import time
import urllib.error
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
    builtin: str | None = None
    optional: bool = True
    path_template: str = "/v1/{capability}"


class ServiceAdapter:
    def __init__(self, spec: ServiceSpec):
        self.spec = spec

    def _builtin_available(self) -> bool:
        if self.spec.builtin == "cardiac_digital_twin":
            return True
        if not self.spec.builtin:
            return False
        try:
            __import__(self._native_package(self.spec.builtin))
            return True
        except Exception:
            return False

    def _endpoint_healthy(self) -> bool:
        if not self.spec.endpoint:
            return False
        try:
            with urllib.request.urlopen(self.spec.endpoint.rstrip("/") + "/health", timeout=2):
                return True
        except Exception:
            return False

    def available(self) -> bool:
        if self.spec.builtin == "cardibridge":
            return self._builtin_available() or self._endpoint_healthy()
        if self._builtin_available():
            return True
        if self.spec.endpoint:
            return self._endpoint_healthy()
        if self.spec.command:
            first_token = self.spec.command.split()[0] if self.spec.command.strip() else ""
            return shutil.which(first_token) is not None
        return False

    @staticmethod
    def _native_package(builtin: str) -> str:
        return {
            "cardianatomy": "cardianatomy",
            "cardiatlas": "cardiatlas",
            "cardibench": "cardi_bench",
            "cardieval": "cardieval",
            "cardilearn": "cardilearn",
            "cardisim": "cardisim",
            "cardivex": "cardivex",
            "cardistudio": "cardistudio",
            "dccp": "dccp",
        }.get(builtin, builtin)

    def _request_path(self, capability: str) -> str:
        capability_path = capability.replace(".", "/")
        return self.spec.path_template.format(
            capability=capability_path,
            capability_leaf=capability.rsplit(".", 1)[-1],
        )

    def _invoke_http(self, capability: str, payload: dict[str, Any]) -> dict[str, Any]:
        if not self.spec.endpoint:
            raise RuntimeError(f"Service {self.spec.name} has no HTTP endpoint")
        from .provenance import sha256

        raw = json.dumps(
            payload,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        ).encode("utf-8")
        idempotency_key = sha256(
            {
                "service": self.spec.name,
                "capability": capability,
                "payload": payload,
            }
        )
        timeout = float(os.getenv("HEARTTWIN_HTTP_TIMEOUT_SECONDS", "120"))
        attempts = int(os.getenv("HEARTTWIN_HTTP_ATTEMPTS", "3"))
        backoff = float(os.getenv("HEARTTWIN_HTTP_BACKOFF_SECONDS", "0.2"))
        max_response_bytes = int(
            os.getenv("HEARTTWIN_HTTP_MAX_RESPONSE_BYTES", str(32 * 1024 * 1024))
        )
        if (
            timeout <= 0
            or attempts < 1
            or backoff < 0
            or max_response_bytes < 1
        ):
            raise ValueError("Invalid HeartTwin HTTP retry/size configuration")

        url = self.spec.endpoint.rstrip("/") + self._request_path(capability)
        last_error: Exception | None = None
        for attempt in range(1, attempts + 1):
            req = urllib.request.Request(
                url,
                data=raw,
                headers={
                    "Content-Type": "application/json",
                    "X-HeartTwin-Idempotency-Key": idempotency_key,
                    "X-HeartTwin-Attempt": str(attempt),
                },
                method="POST",
            )
            try:
                with urllib.request.urlopen(req, timeout=timeout) as response:
                    content_length = response.headers.get("Content-Length")
                    if content_length is not None:
                        try:
                            declared = int(content_length)
                        except ValueError as exc:
                            raise RuntimeError(
                                f"Invalid Content-Length from "
                                f"{self.spec.name}/{capability}"
                            ) from exc
                        if declared > max_response_bytes:
                            raise RuntimeError(
                                f"HTTP response from {self.spec.name}/{capability} "
                                f"exceeds {max_response_bytes} bytes"
                            )
                    body = response.read(max_response_bytes + 1)
                    if len(body) > max_response_bytes:
                        raise RuntimeError(
                            f"HTTP response from {self.spec.name}/{capability} "
                            f"exceeds {max_response_bytes} bytes"
                        )
                    decoded = json.loads(body)
                    if not isinstance(decoded, dict):
                        raise RuntimeError(
                            f"HTTP response from {self.spec.name}/{capability} "
                            "must be a JSON object"
                        )
                    return decoded
            except urllib.error.HTTPError as exc:
                try:
                    body = exc.read().decode("utf-8", errors="replace")
                except Exception:
                    body = ""
                detail = body.strip()
                if exc.code < 500:
                    if detail:
                        exc.msg = f"{exc.msg}: {detail}"
                    raise
                error = RuntimeError(
                    f"HTTP {exc.code} from {self.spec.name}/{capability}"
                    + (f": {detail}" if detail else "")
                )
                error.__cause__ = exc
                last_error = error
                if attempt == attempts:
                    raise error
            except (
                urllib.error.URLError,
                TimeoutError,
                ConnectionError,
                http.client.HTTPException,
            ) as exc:
                last_error = exc
                if attempt == attempts:
                    raise
            if backoff:
                time.sleep(backoff * attempt)

        assert last_error is not None
        raise last_error

    def invoke(self, capability: str, payload: dict[str, Any]) -> dict[str, Any]:
        if capability not in self.spec.capabilities:
            raise ValueError(f"{self.spec.name} does not advertise {capability}")
        if self.spec.builtin == "cardibridge":
            from .cardibridge_adapter import invoke_cardibridge
            return invoke_cardibridge(capability, payload, endpoint=self.spec.endpoint)
        if self.spec.builtin == "cardiac_digital_twin":
            from .builtin_services import cardiac_digital_twin
            return cardiac_digital_twin(payload)
        if self._builtin_available():
            from .native_services import invoke_native
            return invoke_native(self.spec.name, capability, payload)
        if self.spec.endpoint:
            return self._invoke_http(capability, payload)
        if self.spec.command:
            env = os.environ.copy()
            raw = json.dumps(payload)
            env.pop("HEARTTWIN_PAYLOAD", None)
            env.pop("HEARTTWIN_PAYLOAD_STDIN", None)
            env["HEARTTWIN_CAPABILITY"] = capability
            if len(raw.encode("utf-8")) > 32768:
                env["HEARTTWIN_PAYLOAD_STDIN"] = "1"
            else:
                env["HEARTTWIN_PAYLOAD"] = raw
            argv = shlex.split(self.spec.command)
            if not argv:
                raise RuntimeError(f"Service {self.spec.name} has an empty command")
            process = subprocess.run(
                argv,
                input=raw if env.get("HEARTTWIN_PAYLOAD_STDIN") == "1" else None,
                shell=False,
                capture_output=True,
                text=True,
                timeout=600,
                env=env,
                check=False,
            )
            if process.returncode:
                raise RuntimeError(process.stderr.strip() or f"{self.spec.name} failed")
            return json.loads(process.stdout) if process.stdout.strip() else {}
        raise RuntimeError(f"Service {self.spec.name} has no endpoint, command, or builtin")


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
    return [o for o in payload.get("observations", []) if o.get("modality") == modality]
