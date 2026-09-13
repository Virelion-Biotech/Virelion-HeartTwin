import numpy as np
from hearttwin.builtin_services import cardiac_digital_twin
from hearttwin.service_registry import ServiceRegistry, ServiceSpec
from hearttwin.api import VirelionServices

def test_builtin_payload_round_trip():
    payload={"geometry":{"node_xyz":[[0,0,0],[1,0,0],[0,1,0],[0,0,1]],"tetrahedra":[[0,1,2,3]]},"root_nodes":[0],"parameters":{"normal_speed":.2},"with_ecg":False}
    result=cardiac_digital_twin(payload)
    assert result["backend"]=="virelion-cdt-compatible"
    assert len(result["activation_ms"])==4
    assert np.isfinite(result["activation_ms"]).all()

def test_registry_builtin_is_available_and_callable():
    reg=ServiceRegistry([ServiceSpec("CardiSim","Virelion-Biotech/Virelion-CardiSim",("simulation.cardiac_twin",),builtin="cardiac_digital_twin")])
    assert reg.doctor()["CardiSim"] is True
    out=VirelionServices(reg).simulate_cardiac_twin("s1",geometry={"node_xyz":[[0,0,0],[1,0,0],[0,1,0],[0,0,1]],"tetrahedra":[[0,1,2,3]]},root_nodes=[0],with_ecg=False)
    assert out["backend"]=="virelion-cdt-compatible"
