import pytest
from hearttwin.contracts import Observation, Provenance
from hearttwin.service_registry import ServiceRegistry, ServiceSpec
from hearttwin.orchestrator import HeartTwin

def test_empty_twin_is_explicitly_valid():
    reg=ServiceRegistry([ServiceSpec("x","Virelion-Biotech/x",("learn.infer",))])
    run=HeartTwin(reg).run("e1", capabilities=["learn.infer","missing.capability"])
    assert run.entity_id == "e1"
    assert run.results[0].status == "error" or run.results[0].status == "ok"
    assert run.results[1].status == "unavailable"

def test_observation_provenance_survives():
    p=Provenance(source_service="test", run_id="r1")
    o=Observation(observation_id="o1", modality="electrical", values={"hr":60}, provenance=p)
    reg=ServiceRegistry([])
    run=HeartTwin(reg).run("e1", observations=[o], capabilities=[])
    assert run.state.observations[0].provenance.run_id == "r1"
