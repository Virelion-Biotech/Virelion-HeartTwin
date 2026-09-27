from pathlib import Path

from hearttwin.config import load_registry


def test_default_registry_loads_outside_repository_cwd(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.chdir(tmp_path)
    registry = load_registry()
    assert registry.capability("atlas.context") is not None
    assert registry.capability("bridge.publish") is not None
