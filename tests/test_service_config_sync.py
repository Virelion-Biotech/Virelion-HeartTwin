import json
import os
import subprocess
from pathlib import Path

ROOT = Path(__file__).parents[1]


def test_external_and_packaged_service_registries_are_identical() -> None:
    packaged = (ROOT / "hearttwin" / "services.yaml").read_text(encoding="utf-8")
    external = (ROOT / "configs" / "services.yaml").read_text(encoding="utf-8")
    assert external == packaged


def test_bootstrap_clones_the_complete_compatibility_inventory(tmp_path) -> None:
    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    commands = tmp_path / "git-commands.txt"
    git = fake_bin / "git"
    git.write_text('#!/usr/bin/env bash\nprintf "%s\\n" "$*" >> "$BOOTSTRAP_GIT_LOG"\n')
    git.chmod(0o755)
    env = {**os.environ, "PATH": str(fake_bin) + os.pathsep + os.environ["PATH"],
           "BOOTSTRAP_GIT_LOG": str(commands)}
    subprocess.run(["bash", str(ROOT / "scripts/bootstrap_services.sh"), str(tmp_path / "services")],
                   cwd=tmp_path, env=env, check=True, capture_output=True, timeout=15)
    expected = json.loads((ROOT / "configs/compatibility-lock.json").read_text())["repositories"]
    urls = [command.split()[5] for command in commands.read_text().splitlines()]
    assert urls == [f"https://github.com/Virelion-Biotech/{item['name']}.git" for item in expected]
