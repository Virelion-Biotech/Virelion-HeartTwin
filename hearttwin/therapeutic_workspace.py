"""Fail-closed read model for regenerative and therapeutic evidence in HeartTwin.

This module aggregates already-curated component outputs. It does not fetch sources,
infer genotype, score clinical efficacy, or execute unsupported virtual therapies.
"""
from __future__ import annotations
from datetime import datetime, timezone
from typing import Any
import re
from urllib.parse import urlsplit

STRATA={"myocardial_regeneration","myocardial_repair_preservation","genetic_disease_modification","vascular_tissue_engineering","immune_modulation"}
EVIDENCE_STATES={"observed","curated","synthetic","simulated"}
EVAL_DOMAINS={"cell_cycle_entry","mitosis_cytokinesis","lineage_cell_number","functional_maturation","ventricular_function_remodelling","fibrosis_scar","arrhythmia_safety","off_target_proliferation_toxicity"}

class TherapeuticWorkspaceError(ValueError):
    """Raised when an evidence item cannot be represented without unsafe inference."""

def _text(v): return isinstance(v,str) and bool(v.strip())

def _valid_seed(value: Any) -> bool:
    return isinstance(value, int) and not isinstance(value, bool)

def _valid_source_ids(value: Any) -> bool:
    return isinstance(value, list) and bool(value) and all(_text(item) for item in value)

def _valid_http_url(value: Any) -> bool:
    if not _text(value):
        return False
    try:
        parsed = urlsplit(value)
        return parsed.scheme in {"http", "https"} and bool(parsed.netloc)
    except ValueError:
        return False

def _valid_source_provenance(sources: Any) -> bool:
    if not isinstance(sources, list) or not sources:
        return False
    for source in sources:
        if not isinstance(source, dict):
            return False
        source_url = source.get("source_url") or source.get("canonical_source_url")
        digest = source.get("source_sha256")
        if not all(_text(source.get(key)) for key in ("source_identifier", "source_type")):
            return False
        if not _valid_http_url(source_url) or not isinstance(digest, str) or not re.fullmatch(r"[0-9a-fA-F]{64}", digest):
            return False
    return True

def validate_evidence_item(item: dict[str, Any]) -> list[str]:
    if not isinstance(item, dict):
        return ["evidence item must be an object"]
    errors = []
    required = ("record_id", "stratum", "evidence_state", "source_component", "component_version", "interpretation")
    for key in required:
        if not _text(item.get(key)):
            errors.append(f"{key} is required")
    stratum = item.get("stratum")
    if not isinstance(stratum, str) or stratum not in STRATA:
        errors.append("stratum is unsupported")
    state = item.get("evidence_state")
    if not isinstance(state, str) or state not in EVIDENCE_STATES:
        errors.append("evidence_state is unsupported")
    if state in {"observed", "curated"}:
        if not _valid_source_ids(item.get("source_record_ids")):
            errors.append("observed/curated evidence requires non-empty string source_record_ids")
        if not _valid_source_provenance(item.get("source_provenance")):
            errors.append("observed/curated evidence requires complete source_provenance with identifier, type, HTTP(S) URL and SHA-256")
    if state in {"synthetic", "simulated"}:
        if not _valid_seed(item.get("seed")) or not _text(item.get("generator_version")):
            errors.append("synthetic/simulated evidence requires an integer seed and generator_version")
    if item.get("evaluation_dimensions") is not None:
        dims = item["evaluation_dimensions"]
        if not isinstance(dims, dict):
            errors.append("evaluation_dimensions must be an object")
        else:
            unknown = set(dims) - EVAL_DOMAINS
            if unknown:
                errors.append("unknown evaluation dimensions: " + ", ".join(sorted(unknown)))
    return errors

def validate_genotype_context(profile: dict[str, Any]) -> list[str]:
    if not isinstance(profile, dict):
        return ["genotype context must be an object"]
    errors = []
    status = profile.get("status")
    if not isinstance(status, str) or status not in {"confirmed", "reported", "curated", "unknown"}:
        errors.append("genotype status must be confirmed, reported, curated or unknown")
    if status in {"confirmed", "reported", "curated"}:
        if not _valid_source_ids(profile.get("source_record_ids")):
            errors.append("non-unknown genotype requires non-empty string source_record_ids")
        if not _valid_source_provenance(profile.get("source_provenance")):
            errors.append("non-unknown genotype requires complete source_provenance with identifier, type, HTTP(S) URL and SHA-256")
    if profile.get("inferred_by_hearttwin") is True or profile.get("inferred_from_ecg") is True:
        errors.append("HeartTwin must not infer genotype")
    return errors

def validate_intervention_request(
    request: dict[str, Any], *, registered_backends: set[str] | frozenset[str] | None = None
) -> None:
    """Validate against a trusted service-configured allow-list, never caller status text."""
    if not isinstance(request, dict):
        raise TherapeuticWorkspaceError("virtual intervention request must be an object")
    backend = request.get("backend")
    if (
        not _text(backend)
        or not isinstance(registered_backends, (set, frozenset))
        or backend not in registered_backends
    ):
        raise TherapeuticWorkspaceError("virtual intervention rejected: backend is not in HeartTwin's trusted registered-backend allow-list")
    modality = request.get("modality")
    if isinstance(modality, str) and modality in {"regenerative_gene_therapy", "cell_therapy", "myocardial_regeneration"} and request.get("execution_authorized") is not True:
        raise TherapeuticWorkspaceError("regenerative intervention execution requires an explicit supported-backend authorization")

def build_therapeutic_workspace(*, evidence_items:list[dict[str,Any]], genotype_profiles:list[dict[str,Any]]|None=None, maturity_profiles:list[dict[str,Any]]|None=None, synthetic_challenges:list[dict[str,Any]]|None=None, component_versions:dict[str,dict[str,str]]|None=None)->dict[str,Any]:
    """Validate and create a UI-ready evidence projection with source-state labels intact."""
    errors = []
    if not isinstance(evidence_items, list):
        raise TherapeuticWorkspaceError("evidence_items must be a list")
    for name, value in (("genotype_profiles", genotype_profiles), ("maturity_profiles", maturity_profiles), ("synthetic_challenges", synthetic_challenges)):
        if value is not None and not isinstance(value, list):
            raise TherapeuticWorkspaceError(f"{name} must be a list when supplied")
    if component_versions is not None and not isinstance(component_versions, dict):
        raise TherapeuticWorkspaceError("component_versions must be an object")
    ids = set()
    for i, item in enumerate(evidence_items):
        for err in validate_evidence_item(item):
            errors.append(f"evidence_items[{i}]: {err}")
        if not isinstance(item, dict):
            continue
        rid = item.get("record_id")
        if _text(rid):
            if rid in ids:
                errors.append(f"duplicate evidence record_id: {rid}")
            ids.add(rid)
    genotypes = genotype_profiles or []
    for i, profile in enumerate(genotypes):
        errors.extend(f"genotype_profiles[{i}]: {e}" for e in validate_genotype_context(profile))
    for i, profile in enumerate(maturity_profiles or []):
        if not isinstance(profile, dict):
            errors.append(f"maturity_profiles[{i}]: profile must be an object")
            continue
        dimensions = profile.get("dimensions")
        if not isinstance(dimensions, dict) or not dimensions:
            errors.append(f"maturity_profiles[{i}]: non-empty dimensions object is required")
            continue
        if not _text(profile.get("sample_id")) or not _text(profile.get("timepoint")):
            errors.append(f"maturity_profiles[{i}]: sample_id and timepoint are required")
        for name, measurement in dimensions.items():
            if not isinstance(measurement, dict) or measurement.get("dimension") != name:
                errors.append(f"maturity_profiles[{i}]: malformed dimension record {name!r}")
        # MyoTrace stores modality per dimension; missing dimensions stay missing and are never coerced to zero.
    for i, challenge in enumerate(synthetic_challenges or []):
        if not isinstance(challenge, dict):
            errors.append(f"synthetic_challenges[{i}]: challenge must be an object")
            continue
        if challenge.get("evidence_state") not in {"synthetic", "simulated"} or not _valid_seed(challenge.get("seed")) or not _text(challenge.get("generator_version")):
            errors.append(f"synthetic_challenges[{i}]: synthetic/simulated state, integer seed and generator_version are required")
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
    if not isinstance(evidence_class, str) or evidence_class not in _ATLAS_STRATA:
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
        if not isinstance(domain, dict) or not isinstance(domain.get("domain"), str) or domain.get("domain") not in _CARDIEVAL_DOMAINS:
            raise TherapeuticWorkspaceError("CardiEval assessment contains an unknown or malformed domain")
        key = _CARDIEVAL_DOMAINS[domain["domain"]]
        if key in mapped:
            raise TherapeuticWorkspaceError(f"duplicate CardiEval domain after mapping: {key}")
        mapped[key] = {k: v for k, v in domain.items() if k != "domain"}
    if set(mapped) != EVAL_DOMAINS:
        missing = sorted(EVAL_DOMAINS - set(mapped))
        raise TherapeuticWorkspaceError("CardiEval assessment is incomplete after mapping: " + ", ".join(missing))
    return mapped


def adapt_electrotrace_genotype_profile(profile: dict[str, Any]) -> dict[str, Any]:
    """Extract caller-supplied genotype context from an ElectroTrace profile."""
    if not isinstance(profile, dict) or not isinstance(profile.get("genotype_context"), dict):
        raise TherapeuticWorkspaceError("ElectroTrace profile requires genotype_context")
    context = dict(profile["genotype_context"])
    context["source_component"] = "ElectroTrace"
    context["profile_version"] = profile.get("profile_version")
    errors = validate_genotype_context(context)
    if errors:
        raise TherapeuticWorkspaceError("; ".join(errors))
    return context

def adapt_cardiep_disease_context(context: dict[str, Any]) -> dict[str, Any]:
    """Map CardiEP genotype_status to the shared HeartTwin genotype status field."""
    if not isinstance(context, dict):
        raise TherapeuticWorkspaceError("CardiEP disease context must be an object")
    adapted = dict(context)
    adapted["status"] = adapted.pop("genotype_status", "unknown")
    adapted["source_component"] = "CardiEP"
    errors = validate_genotype_context(adapted)
    if errors:
        raise TherapeuticWorkspaceError("; ".join(errors))
    return adapted

def adapt_cardisim_scenario(scenario: dict[str, Any]) -> dict[str, Any]:
    """Flatten the CardiSim result envelope while preserving its synthetic manifest."""
    if not isinstance(scenario, dict) or not isinstance(scenario.get("manifest"), dict):
        raise TherapeuticWorkspaceError("CardiSim scenario requires a manifest object")
    manifest = scenario["manifest"]
    if not isinstance(manifest.get("evidence_state"), str) or manifest.get("evidence_state") not in {"synthetic", "simulated"}:
        raise TherapeuticWorkspaceError("CardiSim manifest must declare synthetic or simulated evidence_state")
    if not _valid_seed(manifest.get("seed")) or not _text(manifest.get("generator_version")):
        raise TherapeuticWorkspaceError("CardiSim manifest requires an integer seed and generator_version")
    return {
        "scenario_id": scenario.get("scenario_id"),
        "axes": scenario.get("axes"),
        "time_days": scenario.get("time_days"),
        "fingerprint": scenario.get("fingerprint"),
        "evidence_state": manifest["evidence_state"],
        "seed": manifest["seed"],
        "generator_version": manifest["generator_version"],
        "scenario_version": manifest.get("scenario_version"),
        "calibration_status": manifest.get("calibration_status"),
        "clinical_or_therapeutic_efficacy_inference": manifest.get("clinical_or_therapeutic_efficacy_inference"),
    }
