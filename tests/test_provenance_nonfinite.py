from hearttwin.provenance import canonical_json, sha256


def test_nonfinite_metrics_have_stable_canonical_hashes() -> None:
    nan_a = canonical_json({"metric": float("nan")})
    nan_b = canonical_json({"metric": float("nan")})
    pos_inf = canonical_json({"metric": float("inf")})
    neg_inf = canonical_json({"metric": float("-inf")})

    assert nan_a == nan_b
    assert b"NaN" not in nan_a
    assert b"Infinity" not in pos_inf
    assert nan_a != pos_inf
    assert pos_inf != neg_inf


def test_nonfinite_metric_hash_is_deterministic_and_distinct() -> None:
    payload = {
        "metrics": {
            "auroc": float("nan"),
            "loss": 0.25,
        },
        "nested": [float("inf"), float("-inf")],
    }
    assert sha256(payload) == sha256(payload)
    assert sha256(payload) != sha256(
        {
            "metrics": {"auroc": None, "loss": 0.25},
            "nested": [None, None],
        }
    )
