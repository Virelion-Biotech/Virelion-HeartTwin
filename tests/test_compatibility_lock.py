import json
import re
from pathlib import Path


ROOT = Path(__file__).parents[1]
LOCK_PATH = ROOT / "configs" / "compatibility-lock.json"
REQ_PATH = ROOT / "requirements-services.txt"


def _requirements_pins() -> dict[str, str]:
    pattern = re.compile(
        r"github\.com/Virelion-Biotech/([^\.\n]+)\.git@([0-9a-f]{40})"
    )
    return dict(pattern.findall(REQ_PATH.read_text(encoding="utf-8")))


def test_compatibility_lock_uses_unique_exact_commit_shas() -> None:
    payload = json.loads(LOCK_PATH.read_text(encoding="utf-8"))
    repositories = payload["repositories"]
    names = [item["name"] for item in repositories]
    assert len(names) == len(set(names))
    assert all(re.fullmatch(r"[0-9a-f]{40}", item["sha"]) for item in repositories)
    assert {item["role"] for item in repositories} <= {"native", "command"}


def test_compatibility_lock_exactly_matches_install_matrix() -> None:
    payload = json.loads(LOCK_PATH.read_text(encoding="utf-8"))
    locked = {item["name"]: item["sha"] for item in payload["repositories"]}
    assert locked == _requirements_pins()


def test_compatibility_lock_covers_current_stack_extensions() -> None:
    names = {item["name"] for item in json.loads(LOCK_PATH.read_text())["repositories"]}
    assert {
        "Virelion-CardiAnatomy",
        "Virelion-CardiEP",
        "Virelion-CardiInfer",
        "Virelion-CardiMech",
        "Virelion-CardiFlow",
        "Virelion-CardiTherapy",
        "Virelion-CardiEval",
        "Virelion-CardiTrace",
    } <= names
