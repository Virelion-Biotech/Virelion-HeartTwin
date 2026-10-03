from hearttwin import load_registry

def test_cardibench_intelligence_capabilities_are_routed_by_hearttwin():
    registry = load_registry()
    for capability in {
        "benchmark.health", "benchmark.resolve", "benchmark.search",
        "benchmark.catalog", "benchmark.discover", "benchmark.result.record",
        "benchmark.results",
    }:
        adapter = registry.capability(capability)
        assert adapter is not None
        assert adapter.spec.name == "CardiBench"
