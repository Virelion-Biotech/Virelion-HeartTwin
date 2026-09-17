from __future__ import annotations

from hearttwin import load_registry


NATIVE_SERVICES = {
    "CardiAtlas",
    "CardiBench",
    "CardiEval",
    "CardiLearn",
    "CardiSim",
    "CardiVex",
    "CardiStudio",
    "DCCP",
}


def test_all_endpoint_only_services_have_native_connections() -> None:
    registry = load_registry()
    specs = {spec.name: spec for spec in registry.services()}
    assert NATIVE_SERVICES <= set(specs)
    for name in sorted(NATIVE_SERVICES):
        assert specs[name].builtin is not None
        assert registry.adapters[name].available(), f"{name} native package is not installed"


def test_cardistudio_native_smoke() -> None:
    registry = load_registry()
    result = registry.capability("design.generate").invoke(
        "design.generate",
        {"entity_id": "smoke", "factors": {"condition": ["sham", "MI"]}, "replicates": 1},
    )
    assert result["n_runs"] == 2
    assert len(result["design"]) == 2


def test_cardibench_native_smoke() -> None:
    registry = load_registry()
    samples = [
        {"sample_id": f"S{i}", "group_id": f"G{i}", "study_id": "ST1", "label": "MI" if i % 2 else "sham"}
        for i in range(6)
    ]
    result = registry.capability("benchmark.resolve").invoke(
        "benchmark.resolve",
        {"entity_id": "smoke", "samples": samples, "seed": 1},
    )
    assert result["sample_count"] == 6
    assert set(result["assignments"]) == {item["sample_id"] for item in samples}
