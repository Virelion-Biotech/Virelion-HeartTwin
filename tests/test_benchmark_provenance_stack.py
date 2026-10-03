import pytest


def test_benchmark_provenance_contracts_are_available_across_stack() -> None:
    cardiatlas = pytest.importorskip("cardiatlas")
    cardibridge = pytest.importorskip("cardibridge")
    cardi_trace = pytest.importorskip("cardi_trace")

    assert callable(cardiatlas.build_server)
    assert isinstance(cardiatlas.DEFAULT_PORT, int)

    registry = cardibridge.default_registry()
    names = set(registry.names())
    assert cardibridge.BENCHMARK_EVIDENCE in names
    assert cardibridge.BENCHMARK_RESULT in names
    assert registry.model(cardibridge.BENCHMARK_EVIDENCE) is cardibridge.BenchmarkEvidence
    assert registry.model(cardibridge.BENCHMARK_RESULT) is cardibridge.BenchmarkResultRecord

    assert callable(cardi_trace.trace_benchmark_discovery)
    assert callable(cardi_trace.trace_benchmark_result)
