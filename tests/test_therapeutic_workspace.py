import pytest
from hearttwin.therapeutic_workspace import (
    TherapeuticWorkspaceError, build_therapeutic_workspace,
    validate_intervention_request,
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
    w=build_therapeutic_workspace(evidence_items=[],maturity_profiles=[{"modality":"video","dimensions":{"sarcomere_alignment":{"status":"not_measured"}}}])
    assert w["maturity_profiles"][0]["dimensions"]["sarcomere_alignment"]["status"]=="not_measured"

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
