from hearttwin.provenance import canonical_json, sha256


def test_nonfinite_floats_have_explicit_stable_canonical_encoding() -> None:
    payload = {
        "nan": float("nan"),
        "pos": float("inf"),
        "neg": float("-inf"),
    }
    raw = canonical_json(payload).decode("utf-8")
    assert "NaN" not in raw
    assert "Infinity" not in raw
    assert '"nan"' in raw
    assert '"positive_infinity"' in raw
    assert '"negative_infinity"' in raw
    assert sha256(payload) == sha256(payload)


def test_nonfinite_sentinels_do_not_collide() -> None:
    assert sha256({"x": float("nan")}) != sha256({"x": float("inf")})
    assert sha256({"x": float("inf")}) != sha256({"x": float("-inf")})
