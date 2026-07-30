"""성능 Target의 전용 Collection·ID·안전 OOM 계약을 확인한다."""

import pytest

from jipsa_rag_benchmark.target_server import (
    _controlled_oom_worker,
    _resolve_benchmark_collection,
    _validate_owned_ids,
)


def test_collection_is_derived_inside_owned_prefix() -> None:
    collection = _resolve_benchmark_collection(
        benchmark_token="test-token",
        explicit_collection=None,
        prefix="rag_benchmark_issue_159_",
    )

    assert collection.startswith("rag_benchmark_issue_159_")
    assert len(collection) <= 255


def test_explicit_collection_outside_prefix_is_rejected() -> None:
    with pytest.raises(ValueError, match="outside the owned prefix"):
        _resolve_benchmark_collection(
            benchmark_token="test-token",
            explicit_collection="production_collection",
            prefix="rag_benchmark_issue_159_",
        )


def test_cleanup_ids_must_stay_in_issue_159_range() -> None:
    assert _validate_owned_ids(159000, (1590000, 1590001)) == (1590000, 1590001)

    with pytest.raises(ValueError, match="test_user_idx"):
        _validate_owned_ids(1, (1590000,))

    with pytest.raises(ValueError, match="Every File_IDX"):
        _validate_owned_ids(159000, (1,))


def test_controlled_oom_worker_does_not_exhaust_the_host() -> None:
    result = _controlled_oom_worker(1)

    assert result["safe_probe"] is True
    assert result["bounded_allocation_mib"] == 1
    assert result["oom_observed"] is True
