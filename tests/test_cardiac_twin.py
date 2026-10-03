import numpy as np
import pytest
from hearttwin.cardiac_twin import CardiacDigitalTwin, CalibrationSpec, ConductionNetwork, EPParameters, MeshGeometry, ScarMap

def tetra_mesh():
    xyz=np.array([[0.,0.,0.],[1.,0.,0.],[0.,1.,0.],[0.,0.,1.],[1.,1.,1.]])
    tet=np.array([[0,1,2,3],[1,2,3,4]])
    fibre=np.tile(np.array([1.,0.,0.]),(len(xyz),1))
    return MeshGeometry(xyz,tet,fibre=fibre)

def test_forward_simulation_is_deterministic_and_finite():
    twin=CardiacDigitalTwin(tetra_mesh(),ConductionNetwork((0,)))
    a=twin.simulate(); b=twin.simulate()
    assert np.all(np.isfinite(a.activation)); assert np.allclose(a.activation,b.activation); assert a.ecg.values.shape[0]==12

def test_scar_slows_propagation():
    healthy=tetra_mesh(); scarred=MeshGeometry(healthy.node_xyz,healthy.tetrahedra,fibre=healthy.fibre,scar=ScarMap(np.array([0,0,0,0,2])))
    params=EPParameters(); h=CardiacDigitalTwin(healthy,ConductionNetwork((0,)),params).simulate(with_ecg=False); s=CardiacDigitalTwin(scarred,ConductionNetwork((0,)),params).simulate(with_ecg=False)
    assert s.activation[4]>h.activation[4]

def test_calibration_recovers_synthetic_fibre_speed():
    geometry=tetra_mesh(); truth=EPParameters(fibre_speed=.90); target=CardiacDigitalTwin(geometry,ConductionNetwork((0,)),truth).simulate(with_ecg=False).activation
    learner=CardiacDigitalTwin(geometry,ConductionNetwork((0,)),EPParameters(fibre_speed=.40))
    learned,metrics=learner.calibrate(target_activation=target,spec=CalibrationSpec(bounds={"fibre_speed":(.20,1.20)},max_iterations=64,step_fraction=.25,weights={"activation":1.0}))
    assert abs(learned.fibre_speed-truth.fibre_speed)<.08; assert metrics["objective"]<.25

def test_invalid_parameter_order_is_rejected():
    with pytest.raises(ValueError): EPParameters(apd_min=330,apd_max=300).validate()
