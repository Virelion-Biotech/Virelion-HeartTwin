import pytest
from hearttwin.credibility import (
    ContextOfUse,
    CredibilityEnvelope,
    DataRoleRecord,
    UncertaintyRecord,
    CredibilityEvidence,
    require_scientific_claim,
    merge_credibility,
)
from hearttwin.contracts import CardiacState
from hearttwin.state import CardiacStateStore


def context():
    return ContextOfUse(
        context_id="activation-v1",
        purpose="LAT research",
        population="declared cohort",
        endpoints=("LAT",),
        applicability_domain=("sinus rhythm",),
    )


def role(name, digest="a"):
    return DataRoleRecord(
        dataset_id=name,
        dataset_sha256=digest * 64,
        subject_ids=("donor-1",),
        role=name,
        context_id="activation-v1",
    )


def test_biological_reuse_and_fingerprint_aliases_are_rejected():
    for records in [
        (role("training"), role("external_validation", "b")),
        (role("training"), role("external_validation")),
    ]:
        with pytest.raises(ValueError, match="conflicting"):
            CredibilityEnvelope(context=context(), data_roles=records)


def test_empty_envelope_cannot_authorize_a_predictive_claim():
    with pytest.raises(ValueError):
        require_scientific_claim(
            CredibilityEnvelope(),
            artifact_id="ep-1",
            endpoint="LAT",
            claim="predictive",
        )


def test_evidence_is_bound_to_context_artifact_and_endpoint():
    evidence = CredibilityEvidence(
        evidence_id="software-1",
        artifact_id="ep-1",
        context_id="activation-v1",
        endpoint="LAT",
        level=0,
        comparator="analytic fixture",
        dataset_id="fixture",
        dataset_version="1",
        population="declared cohort",
        metric="error",
        value=0,
        acceptance_maximum=1,
        protocol_sha256="a" * 64,
        report_sha256="b" * 64,
        data_role="challenge",
    )
    env = CredibilityEnvelope(context=context(), evidence=(evidence,))
    assert require_scientific_claim(
        env, artifact_id="ep-1", endpoint="LAT", claim="software"
    )
    for artifact, endpoint, claim in [
        ("ep-2", "LAT", "software"),
        ("ep-1", "ECG", "software"),
        ("ep-1", "LAT", "empirical"),
    ]:
        with pytest.raises(ValueError):
            require_scientific_claim(
                env, artifact_id=artifact, endpoint=endpoint, claim=claim
            )


def test_uncertainty_and_context_are_in_state_fingerprint():
    a = CardiacStateStore.new("s").snapshot()
    env = CredibilityEnvelope(
        context=context(),
        uncertainties=(
            UncertaintyRecord(uncertainty_id="u", artifact_id="ep-1", kind="fiber"),
        ),
    )
    store = CardiacStateStore(CardiacState(entity_id="s", credibility=env))
    assert store.snapshot().state_fingerprint != a.state_fingerprint
    assert store.snapshot().credibility.uncertainties[0].status == "unknown"


def test_merge_does_not_overwrite_conflicting_contexts():
    env = CredibilityEnvelope(context=context())
    assert merge_credibility(env, env) == env
    other = context().model_copy(update={"population": "different"})
    with pytest.raises(ValueError, match="conflicting"):
        merge_credibility(env, CredibilityEnvelope(context=other))


def test_empirical_claim_requires_all_declared_uncertainty_sources():
    co = context()
    lineage = DataRoleRecord(
        dataset_id="test",
        dataset_sha256="a" * 64,
        subject_ids=("heldout-donor",),
        role="challenge",
        context_id=co.context_id,
    )
    evidence = tuple(
        CredibilityEvidence(
            evidence_id=f"e-{i}",
            artifact_id="ep-1",
            context_id=co.context_id,
            endpoint="LAT",
            level=i,
            comparator="fixture",
            dataset_id="test",
            dataset_version="1",
            population=co.population,
            metric="error",
            value=0,
            acceptance_maximum=1,
            protocol_sha256="b" * 64,
            report_sha256="c" * 64,
            data_role="challenge",
        )
        for i in range(4)
    )
    budget = tuple(
        UncertaintyRecord(
            uncertainty_id=f"u-{kind}",
            artifact_id="ep-1",
            kind=kind,
            status="sensitivity_only",
            method="declared sweep",
            evidence_ids=("e-3",),
        )
        for kind in co.required_uncertainty_kinds
    )
    env = CredibilityEnvelope(
        context=co, evidence=evidence, data_roles=(lineage,), uncertainties=budget
    )
    assert require_scientific_claim(
        env, artifact_id="ep-1", endpoint="LAT", claim="empirical"
    )
    with pytest.raises(ValueError, match="uncertainty"):
        require_scientific_claim(
            env.model_copy(update={"uncertainties": budget[:-1]}),
            artifact_id="ep-1",
            endpoint="LAT",
            claim="empirical",
        )


def test_direct_service_result_cannot_bypass_claim_admission():
    from hearttwin.contracts import ServiceResult

    with pytest.raises(ValueError):
        ServiceResult(
            service="custom",
            capability="custom",
            status="ok",
            data={
                "scientific_claims": [
                    {"artifact_id": "p", "endpoint": "survival", "claim": "predictive"}
                ]
            },
        )
    with pytest.raises(ValueError, match="Patient outcomes"):
        ServiceResult(
            service="CardiTherapy",
            capability="therapy.run",
            status="ok",
            data={
                "validation_status": "empirically_checked",
                "outcomes": [
                    {
                        "endpoint": "survival",
                        "endpoint_scope": "patient_outcome",
                        "value": 1,
                    }
                ],
            },
        )


def test_mutated_claims_fail_before_state_mutation():
    from hearttwin.contracts import ServiceResult

    result = ServiceResult(service="custom", capability="custom", status="ok")
    result.data["scientific_claims"] = [
        {"artifact_id": "p", "endpoint": "survival", "claim": "predictive"}
    ]
    store = CardiacStateStore.new("s")
    before = store.fingerprint()
    with pytest.raises(ValueError):
        store.reduce_service_result(result)
    assert store.fingerprint() == before


def test_admitted_claim_is_retained_and_rechecked_on_snapshot():
    from hearttwin.contracts import ServiceResult

    evidence = CredibilityEvidence(
        evidence_id="software-1",
        artifact_id="ep-1",
        context_id="activation-v1",
        endpoint="LAT",
        level=0,
        comparator="fixture",
        dataset_id="fixture",
        dataset_version="1",
        population="declared cohort",
        metric="error",
        value=0,
        acceptance_maximum=1,
        protocol_sha256="a" * 64,
        report_sha256="b" * 64,
        data_role="challenge",
    )
    result = ServiceResult(
        service="custom",
        capability="custom",
        status="ok",
        credibility=CredibilityEnvelope(context=context(), evidence=(evidence,)),
        scientific_claims=[
            {"artifact_id": "ep-1", "endpoint": "LAT", "claim": "software"}
        ],
    )
    store = CardiacStateStore.new("s")
    store.reduce_service_result(result)
    assert store.snapshot().scientific_claims[0].artifact_id == "ep-1"
    store.state.credibility = CredibilityEnvelope(context=context())
    with pytest.raises(ValueError):
        store.snapshot()
