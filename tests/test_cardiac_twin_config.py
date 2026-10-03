from hearttwin.config import load_registry

def test_native_and_external_cardi_sim_are_separate(tmp_path):
    cfg=tmp_path/'services.yaml'
    cfg.write_text('''services:\n  - name: CardiSim\n    repository: Virelion-Biotech/Virelion-CardiSim\n    capabilities: [simulation.run]\n    endpoint: http://127.0.0.1:9\n  - name: CardiSimNative\n    repository: Virelion-Biotech/Virelion-HeartTwin\n    capabilities: [simulation.cardiac_twin]\n    builtin: cardiac_digital_twin\n''')
    reg=load_registry(cfg)
    assert reg.capability('simulation.run').spec.name=='CardiSim'
    assert reg.capability('simulation.cardiac_twin').spec.name=='CardiSimNative'
    assert reg.capability('simulation.cardiac_twin').available() is True
