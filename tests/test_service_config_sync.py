from pathlib import Path


ROOT = Path(__file__).parents[1]


def test_external_and_packaged_service_registries_are_identical() -> None:
    packaged = (ROOT / "hearttwin" / "services.yaml").read_text(encoding="utf-8")
    external = (ROOT / "configs" / "services.yaml").read_text(encoding="utf-8")
    assert external == packaged
