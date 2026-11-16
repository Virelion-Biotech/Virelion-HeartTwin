"""Fail-closed read model for regenerative and therapeutic evidence in HeartTwin.

This module aggregates already-curated component outputs. It does not fetch sources,
infer genotype, score clinical efficacy, or execute unsupported virtual therapies.
"""
from __future__ import annotations
from datetime import datetime, timezone
from typing import Any

STRATA={"myocardial_regeneration","myocardial_repair_preservation","genetic_disease_modification","vascular_tissue_engineering","immune_modulation"}
EVIDENCE_STATES={"observed","curated","synthetic","simulated"}
EVAL_DOMAINS={"cell_cycle_entry","mitosis_cytokinesis","lineage_cell_number","functional_maturation","ventricular_function_remodelling","fibrosis_scar","arrhythmia_safety","off_target_proliferation_toxicity"}

class TherapeuticWorkspaceError(ValueError):
    """Raised when an evidence item cannot be represented without unsafe inference."""

def _text(v): return isinstance(v,str) and bool(v.strip())

def validate_evidence_item(item:dict[str,Any])->list[str]:
    errors=[]
    required=("record_id","stratum","evidence_state","source_component","component_version","interpretation")
    for key in required:
        if not _text(item.get(key)): errors.append(f"{key} is required")
    if item.get("stratum") not in STRATA: errors.append("stratum is unsupported")
    state=item.get("evidence_state")
    if state not in EVIDENCE_STATES: errors.append("evidence_state is unsupported")
    if state in {"observed","curated"}:
        if not item.get("source_record_ids"): errors.append("observed/curated evidence requires source_record_ids")
        if not item.get("source_provenance"): errors.append("observed/curated evidence requires source_provenance")
    if state in {"synthetic","simulated"}:
        if item.get("seed") is None or not _text(item.get("generator_version")):
            errors.append("synthetic/simulated evidence requires seed and generator_version")
    if item.get("evaluation_dimensions") is not None:
        dims=item["evaluation_dimensions"]
        if not isinstance(dims,dict): errors.append("evaluation_dimensions must be an object")
        else:
            unknown=set(dims)-EVAL_DOMAINS
            if unknown: errors.append("unknown evaluation dimensions: "+", ".join(sorted(unknown)))
    return errors

def validate_genotype_context(profile:dict[str,Any])->list[str]:
    errors=[]
    status=profile.get("status")
    if status not in {"confirmed","reported","curated","unknown"}: errors.append("genotype status must be confirmed, reported, curated or unknown")
    if status in {"confirmed","reported","curated"}:
        if not profile.get("source_record_ids"):
            errors.append("non-unknown genotype requires source_record_ids")
        if not profile.get("source_provenance"):
            errors.append("non-unknown genotype requires source_provenance")
    if profile.get("inferred_by_hearttwin") is True:
        errors.append("HeartTwin must not infer genotype")
    return errors

def validate_intervention_request(request:dict[str,Any])->None:
    backend=request.get("backend")
    if request.get("backend_status")!="supported" or not _text(backend):
        raise TherapeuticWorkspaceError("virtual intervention rejected: backend is not explicitly registered as supported")
    if request.get("modality") in {"regenerative_gene_therapy","cell_therapy","myocardial_regeneration"} and request.get("execution_authorized") is not True:
        raise TherapeuticWorkspaceError("regenerative intervention execution requires an explicit supported-backend authorization")

def build_therapeutic_workspace(*, evidence_items:list[dict[str,Any]], genotype_profiles:list[dict[str,Any]]|None=None, maturity_profiles:list[dict[str,Any]]|None=None, synthetic_challenges:list[dict[str,Any]]|None=None, component_versions:dict[str,dict[str,str]]|None=None)->dict[str,Any]:
    """Validate and create a UI-ready evidence projection with source-state labels intact."""
    errors=[]
    ids=set()
    for i,item in enumerate(evidence_items):
        for err in validate_evidence_item(item): errors.append(f"evidence_items[{i}]: {err}")
        rid=item.get("record_id")
        if rid in ids: errors.append(f"duplicate evidence record_id: {rid}")
        ids.add(rid)
    genotypes=genotype_profiles or []
    for i,p in enumerate(genotypes):
        errors.extend(f"genotype_profiles[{i}]: {e}" for e in validate_genotype_context(p))
    for i,p in enumerate(maturity_profiles or []):
        if not _text(p.get("modality")) or not isinstance(p.get("dimensions"),dict):
            errors.append(f"maturity_profiles[{i}]: modality and dimensions are required")
        # Missing dimensions stay missing; never coerce to zero.
    for i,c in enumerate(synthetic_challenges or []):
        if c.get("evidence_state") not in {"synthetic","simulated"} or c.get("seed") is None or not _text(c.get("generator_version")):
            errors.append(f"synthetic_challenges[{i}]: synthetic/simulated state, seed and generator_version are required")
    if errors: raise TherapeuticWorkspaceError("\n".join(errors))
    by_stratum={s:[] for s in sorted(STRATA)}
    for item in evidence_items: by_stratum[item["stratum"]].append(item)
    return {
        "contract_version":"1.0.0",
        "generated_at":datetime.now(timezone.utc).isoformat(),
        "component_versions":component_versions or {},
        "evidence_items":evidence_items,
        "by_stratum":by_stratum,
        "genotype_profiles":genotypes,
        "maturity_profiles":maturity_profiles or [],
        "synthetic_challenges":synthetic_challenges or [],
        "interpretation_policy":{
            "evidence_states_remain_distinct":True,
            "evaluation_dimensions_are_not_collapsed":True,
            "missing_maturity_is_not_zero":True,
            "genotype_is_never_inferred":True,
            "unsupported_interventions_fail_closed":True,
            "clinical_decision_support":False,
            "clinical_efficacy_claim":False
        }
    }


# Explicit adapters keep upstream component vocabularies visible and prevent
# silent semantic coercion at the HeartTwin boundary.
_ATLAS_STRATA = {
    "myocardial_regeneration": "myocardial_regeneration",
    "myocardial_repair": "myocardial_repair_preservation",
    "myocardial_preservation": "myocardial_repair_preservation",
    "genetic_disease_modification": "genetic_disease_modification",
    "vascular_tissue_engineering": "vascular_tissue_engineering",
    "immune_modulation": "immune_modulation",
}
_CARDIEVAL_DOMAINS = {
    "cell_cycle_entry": "cell_cycle_entry",
    "mitosis_cytokinesis": "mitosis_cytokinesis",
    "cardiomyocyte_number_lineage": "lineage_cell_number",
    "functional_maturation": "functional_maturation",
    "ventricular_function_remodelling": "ventricular_function_remodelling",
    "fibrosis_scar": "fibrosis_scar",
    "arrhythmia_safety": "arrhythmia_safety",
    "off_target_toxicity": "off_target_proliferation_toxicity",
}

def adapt_cardi_atlas_record(
    record: dict[str, Any], *, evidence_state: str, component_version: str,
    source_provenance: list[dict[str, Any]],
) -> dict[str, Any]:
    """Convert one CardiAtlas therapeutic record without inferring evidence state."""
    if not isinstance(record, dict) or record.get("record_type") != "therapeutic_evidence":
        raise TherapeuticWorkspaceError("CardiAtlas adapter requires a therapeutic_evidence record")
    evidence_class = record.get("evidence_class")
    if evidence_class not in _ATLAS_STRATA:
        raise TherapeuticWorkspaceError(f"CardiAtlas evidence_class is not mappable: {evidence_class!r}")
    record_id = record.get("id")
    if not _text(record_id) or not _text(component_version):
        raise TherapeuticWorkspaceError("CardiAtlas record id and component_version are required")
    source_ids = record.get("evidence_ids") or record.get("source_ids") or []
    interpretation = record.get("proposed_mechanism") or "Interpretation not supplied; no efficacy inference."
    item = {
        "record_id": record_id,
        "stratum": _ATLAS_STRATA[evidence_class],
        "evidence_state": evidence_state,
        "source_component": "CardiAtlas",
        "component_version": component_version,
        "interpretation": interpretation,
        "source_record_ids": list(source_ids),
        "source_provenance": source_provenance,
        "atlas_modality": record.get("modality"),
        "target_or_genotype": record.get("target_or_genotype"),
        "model_system": record.get("model_system"),
        "species": record.get("species"),
        "endpoints": record.get("endpoints", []),
        "curation_status": record.get("curation_status", "unreviewed"),
    }
    errors = validate_evidence_item(item)
    if errors:
        raise TherapeuticWorkspaceError("; ".join(errors))
    return item

def adapt_cardi_eval_assessment(assessment: dict[str, Any]) -> dict[str, dict[str, Any]]:
    """Map CardiEval's domain vocabulary to HeartTwin's stable workspace keys."""
    if not isinstance(assessment, dict) or not isinstance(assessment.get("domains"), list):
        raise TherapeuticWorkspaceError("CardiEval adapter requires an assessment with a domains array")
    mapped: dict[str, dict[str, Any]] = {}
    for domain in assessment["domains"]:
        if not isinstance(domain, dict) or domain.get("domain") not in _CARDIEVAL_DOMAINS:
            raise TherapeuticWorkspaceError("CardiEval assessment contains an unknown or malformed domain")
        key = _CARDIEVAL_DOMAINS[domain["domain"]]
        if key in mapped:
            raise TherapeuticWorkspaceError(f"duplicate CardiEval domain after mapping: {key}")
        mapped[key] = {k: v for k, v in domain.items() if k != "domain"}
    if set(mapped) != EVAL_DOMAINS:
        missing = sorted(EVAL_DOMAINS - set(mapped))
        raise TherapeuticWorkspaceError("CardiEval assessment is incomplete after mapping: " + ", ".join(missing))
    return mapped
