import pytest

from hearttwin.workflow import WorkflowError, _validate_binary_test_partition


def test_binary_test_partition_requires_heldout_samples() -> None:
    with pytest.raises(WorkflowError, match="test partition is empty"):
        _validate_binary_test_partition(
            {"S0": "train", "S1": "validation"},
            {"S0": 0, "S1": 1},
        )


def test_binary_test_partition_rejects_one_class_holdout() -> None:
    with pytest.raises(WorkflowError, match="both reference classes"):
        _validate_binary_test_partition(
            {
                "S0": "train",
                "S1": "train",
                "S2": "test",
                "S3": "test",
            },
            {"S0": 0, "S1": 1, "S2": 1, "S3": 1},
        )


def test_binary_test_partition_accepts_both_classes() -> None:
    test_ids = _validate_binary_test_partition(
        {
            "S0": "train",
            "S1": "validation",
            "S2": "test",
            "S3": "test",
        },
        {"S0": 0, "S1": 1, "S2": 0, "S3": 1},
    )
    assert test_ids == ["S2", "S3"]
