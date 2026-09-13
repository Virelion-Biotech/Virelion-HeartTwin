"""Dependency/provenance metadata for the upstream CDT integration."""
UPSTREAM_REPOSITORY = "juliacamps/Cardiac-Digital-Twin"
UPSTREAM_COMMIT = "816d51fab0837cfe9e20c7d3a318429e9acf0733"
UPSTREAM_LICENSE = "MIT"
UPSTREAM_LICENSE_COPYRIGHT = "Copyright (c) 2024 Julia Camps and Zhinuo (Jenny) Wang"

def manifest() -> dict[str, str]:
    return {
        "repository": UPSTREAM_REPOSITORY,
        "commit": UPSTREAM_COMMIT,
        "license": UPSTREAM_LICENSE,
        "copyright": UPSTREAM_LICENSE_COPYRIGHT,
        "integration_mode": "native-reimplementation-with-explicit-optional-upstream-adapter",
    }
