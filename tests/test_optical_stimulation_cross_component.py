import pytest


CONTRACT = "virelion.optical-stimulation/1.0.0"


def _protocol():
    return {
        "schema_version": CONTRACT,
        "protocol_id": "hearttwin-cross-component-canary",
        "modality": "optogenetic",
        "target": {
            "cell_type": "hiPSC-derived cardiomyocyte",
            "anatomical_region": None,
            "spatial_pattern": "global",
            "geometry_ref": None,
        },
        "actuator": {
            "name": "test-actuator",
            "class": "opsin",
            "expression_method": "other",
        },
        "light": {
            "wavelength_nm": 470.0,
            "irradiance_mw_mm2": 0.8,
            "pulse_width_ms": 5.0,
            "frequency_hz": 2.0,
        },
        "timing": {"start_ms": 100.0, "duration_ms": 1000.0},
        "control": {
            "mode": "open_loop",
            "feedback_signal": None,
            "controller_ref": None,
        },
        "provenance": {"source": "simulation", "source_id": "cross-component-canary"},
    }


def test_cardisim_and_opticell_accept_the_same_hearttwin_protocol():
    cardisim = pytest.importorskip("cardisim.optical_stimulation")
    opticell = pytest.importorskip("opticell.optical_stimulation")

    assert cardisim.OPTICAL_STIMULATION_SCHEMA_VERSION == CONTRACT
    assert opticell.OPTICAL_STIMULATION_SCHEMA_VERSION == CONTRACT

    payload = _protocol()
    sim_protocol = cardisim.OpticalStimulationProtocol.from_mapping(payload)
    imaging_protocol = opticell.validate_optical_stimulation_metadata(payload)

    assert sim_protocol.protocol_id == imaging_protocol["protocol_id"]
    assert sim_protocol.wavelength_nm == imaging_protocol["light"]["wavelength_nm"]
    assert sim_protocol.frequency_hz == imaging_protocol["light"]["frequency_hz"]
    assert cardisim.illumination_active(100.0, sim_protocol)
