import pytest
from hearttwin.therapeutic_workspace import (
    TherapeuticWorkspaceError, build_therapeutic_workspace,
    validate_intervention_request, adapt_cardi_atlas_record, adapt_cardi_eval_assessment,
    adapt_electrotrace_genotype_profile, adapt_cardiep_disease_context, adapt_cardisim_scenario,
)

def item(**kw):
    v={"record_id":"atlas-ev-1","stratum":"myocardial_regeneration","evidence_state":"curated","source_component":"CardiAtlas","component_version":"0.9.0","interpretation":"division-supporting evidence; maturation not established","source_record_ids":["ev-1"],"source_provenance":[{"source_type":"peer_reviewed","source_identifier":"DOI:10.1000/x"}],"evaluation_dimensions":{"cell_cycle_entry":{"status":"assessed"},"functional_maturation":{"status":"not_assessed"}}}
    v.update(kw); return v

def test_workspace_keeps_strata_and_evidence_state_separate():
    w=build_therapeutic_workspace(evidence_items=[item()])
    assert w["evidence_items"][0]["evidence_state"]=="curated"
    assert len(w["by_stratum"]["myocardial_regeneration"])==1
    assert w["interpretation_policy"]["evaluation_dimensions_are_not_collapsed"]

def test_missing_source_lineage_fails_closed():
    with pytest.raises(TherapeuticWorkspaceError,match="source_record_ids"):
        build_therapeutic_workspace(evidence_items=[item(source_record_ids=[],source_provenance=[])])

def test_genotype_is_not_inferred():
    with pytest.raises(TherapeuticWorkspaceError,match="must not infer genotype"):
        build_therapeutic_workspace(evidence_items=[],genotype_profiles=[{"status":"confirmed","source_record_ids":["x"],"inferred_by_hearttwin":True}])

def test_missing_maturity_is_preserved_not_zeroed():
    w=build_therapeutic_workspace(evidence_items=[],maturity_profiles=[{"sample_id":"sample-1","timepoint":"day-7","dimensions":{"sarcomeric_organization":{"dimension":"sarcomeric_organization","status":"not_measured"}}}])
    assert w["maturity_profiles"][0]["dimensions"]["sarcomeric_organization"]["status"]=="not_measured"

def test_synthetic_challenge_requires_seed_and_generator_version():
    with pytest.raises(TherapeuticWorkspaceError,match="seed"):
        build_therapeutic_workspace(evidence_items=[],synthetic_challenges=[{"evidence_state":"synthetic"}])

def test_unsupported_therapy_backend_is_rejected():
    with pytest.raises(TherapeuticWorkspaceError,match="not explicitly registered"):
        validate_intervention_request({"backend":"future_backend","backend_status":"unknown"})
    validate_intervention_request({"backend":"cardiep_pacing","backend_status":"supported","modality":"pacing","execution_authorized":True})

def test_regenerative_intervention_requires_explicit_authorization():
    with pytest.raises(TherapeuticWorkspaceError,match="explicit supported-backend authorization"):
        validate_intervention_request({"backend":"supported","backend_status":"supported","modality":"cell_therapy","execution_authorized":False})


def test_cardi_eval_domains_are_mapped_without_losing_independent_fields():
    names = [
        "cell_cycle_entry", "mitosis_cytokinesis", "cardiomyocyte_number_lineage",
        "functional_maturation", "ventricular_function_remodelling", "fibrosis_scar",
        "arrhythmia_safety", "off_target_toxicity",
    ]
    assessment = {"domains": [{"domain": name, "status": "not_reported"} for name in names]}
    mapped = adapt_cardi_eval_assessment(assessment)
    assert "lineage_cell_number" in mapped
    assert "off_target_proliferation_toxicity" in mapped
    assert len(mapped) == 8


def test_cardi_eval_adapter_rejects_incomplete_or_unknown_domains():
    with pytest.raises(TherapeuticWorkspaceError, match="incomplete"):
        adapt_cardi_eval_assessment({"domains": [{"domain": "cell_cycle_entry", "status": "direct_support"}]})
    with pytest.raises(TherapeuticWorkspaceError, match="unknown or malformed"):
        adapt_cardi_eval_assessment({"domains": [{"domain": "invented", "status": "direct_support"}]})


def test_cardi_atlas_strata_are_mapped_explicitly():
    record = {
        "record_type": "therapeutic_evidence", "id": "atlas-1",
        "evidence_class": "myocardial_repair", "modality": "modRNA",
        "evidence_ids": ["source-1"], "proposed_mechanism": "Repair-associated observation",
        "curation_status": "source_checked",
    }
    adapted = adapt_cardi_atlas_record(
        record, evidence_state="curated", component_version="1.0.0",
        source_provenance=[{"source_identifier": "DOI:example"}],
    )
    assert adapted["stratum"] == "myocardial_repair_preservation"
    assert adapted["evidence_state"] == "curated"


def test_cardi_atlas_adapter_does_not_coerce_other_class():
    record = {"record_type": "therapeutic_evidence", "id": "atlas-2", "evidence_class": "other"}
    with pytest.raises(TherapeuticWorkspaceError, match="not mappable"):
        adapt_cardi_atlas_record(
            record, evidence_state="curated", component_version="1.0.0",
            source_provenance=[{"source_identifier": "x"}],
        )


def test_genotype_curated_status_matches_electrotrace_and_cardiep_contracts():
    profile = {
        "status": "curated", "source_record_ids": ["variant-1"],
        "source_provenance": [{"source_identifier": "ClinVar:example"}],
    }
    assert build_therapeutic_workspace(evidence_items=[], genotype_profiles=[profile])["genotype_profiles"] == [profile]


def test_native_myotrace_profile_shape_is_accepted_without_fake_global_modality():
    profile = {
        "profile_version": "myotrace-maturity-profile/1.0.0",
        "sample_id": "sample-1",
        "timepoint": "day-14",
        "dimensions": {
            "sarcomeric_organization": {
                "dimension": "sarcomeric_organization", "status": "not_measured",
                "value": None, "modality": None,
            },
            "contractility_beat_mechanics": {
                "dimension": "contractility_beat_mechanics", "status": "proxy",
                "value": 12.0, "unit": "motion_index", "modality": "mechanical",
            },
        },
    }
    workspace = build_therapeutic_workspace(evidence_items=[], maturity_profiles=[profile])
    assert workspace["maturity_profiles"][0]["dimensions"]["contractility_beat_mechanics"]["modality"] == "mechanical"


def test_electrotrace_nested_genotype_context_is_adapted():
    profile = {
        "profile_version": "electrotrace-genotype-ep-phenotype/1.0.0",
        "genotype_context": {
            "status": "curated", "variants": [],
            "source_record_ids": ["variant-1"],
            "source_provenance": [{"source_identifier": "ClinVar:1"}],
        },
    }
    adapted = adapt_electrotrace_genotype_profile(profile)
    assert adapted["status"] == "curated"
    assert adapted["source_component"] == "ElectroTrace"


def test_cardiep_genotype_status_is_mapped_without_inference():
    context = {
        "genotype_status": "reported", "source_record_ids": ["variant-2"],
        "source_provenance": [{"source_identifier": "registry:2"}],
        "inferred_from_ecg": False,
    }
    adapted = adapt_cardiep_disease_context(context)
    assert adapted["status"] == "reported"
    assert adapted["source_component"] == "CardiEP"


def test_genotype_adapter_rejects_ecg_inferred_status():
    with pytest.raises(TherapeuticWorkspaceError, match="must not infer genotype"):
        adapt_cardiep_disease_context({
            "genotype_status": "unknown", "inferred_from_ecg": True,
        })


def test_cardisim_manifest_is_flattened_for_workspace_contract():
    scenario = {
        "scenario_id": "post_mi_transient_proliferation",
        "axes": {"cell_cycle_entry": [[0.1, 0.2]]},
        "time_days": [0.0, 1.0],
        "fingerprint": "abc",
        "manifest": {
            "evidence_state": "synthetic", "seed": 7,
            "generator_version": "cardisim-regenerative-scenarios/1.0.0",
            "scenario_version": "1.0.0", "calibration_status": "uncalibrated_synthetic",
            "clinical_or_therapeutic_efficacy_inference": False,
        },
    }
    adapted = adapt_cardisim_scenario(scenario)
    assert adapted["evidence_state"] == "synthetic"
    workspace = build_therapeutic_workspace(evidence_items=[], synthetic_challenges=[adapted])
    assert workspace["synthetic_challenges"][0]["seed"] == 7
