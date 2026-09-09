from __future__ import annotations

import os
import re
from pathlib import Path

import yaml

from .service_registry import ServiceRegistry, ServiceSpec

_ENV = re.compile(r"^\$\{([A-Z0-9_]+)\}$")


def _resolve(value):
    if isinstance(value, str):
        match = _ENV.match(value)
        return os.getenv(match.group(1), "") if match else value
    return value


def load_registry(path: str | Path = "configs/services.yaml") -> ServiceRegistry:
    raw = yaml.safe_load(Path(path).read_text())
    specs = []
    for item in raw.get("services", []):
        specs.append(
            ServiceSpec(
                name=item["name"],
                repository=item["repository"],
                capabilities=tuple(item.get("capabilities", [])),
                endpoint=_resolve(item.get("endpoint")),
                command=_resolve(item.get("command")),
                optional=item.get("optional", True),
                path_template=item.get("path_template", "/v1/{capability}"),
            )
        )
    return ServiceRegistry(specs)
