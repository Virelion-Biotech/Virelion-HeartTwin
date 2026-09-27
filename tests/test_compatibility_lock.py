import json
import re
from pathlib import Path


LOCK_PATH = Path(__file__).parents[1] / "configs" / "compatibility-lock.json"


def test_compatibility_lock_uses_unique_exact_commit_shas():
    payload = json.loads(LOCK_PATH.read_text(encoding="utf-8"))
    repositories = payload["repositories"]
    names = [item["name"] for item in repositories]
    assert len(names) == len(set(names))
    assert all(re.fullmatch(r"[0-9a-f]{40}", item["sha"]) for item in repositories)
    assert {item["role"] for item in repositories} <= {"native", "adapter"}


def test_compatibility_lock_covers_declared_stack():
    names = {item["name"] for item in json.loads(LOCK_PATH.read_text())["repositories"]}
    expected = {
        "Virelion-CardiAtlas", "Virelion-CardiBench", "Virelion-CardiEval",
        "Virelion-CardiLearn", "Virelion-CardiSim", "Virelion-CardiVex",
        "Virelion-CardiStudio", "Virelion-DCCP", "Virelion-ElectroTrace",
        "Virelion-MyoTrace", "Virelion-OptiCell", "Virelion-CardioScore",
        "Virelion-CardiTrace", "Virelion-CardiBridge", "Virelion-CardiAgent",
    }
    assert names == expected
