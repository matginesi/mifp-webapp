from __future__ import annotations

import pytest

from TESTS.sharding import shard_index


def test_sharding_is_stable_complete_and_disjoint() -> None:
    nodeids = [f"TESTS/webapp/test_module_{i % 37}.py::test_case[{i}]" for i in range(1000)]
    first = {nodeid for nodeid in nodeids if shard_index(nodeid, 2) == 0}
    second = {nodeid for nodeid in nodeids if shard_index(nodeid, 2) == 1}

    assert first.isdisjoint(second)
    assert first | second == set(nodeids)
    assert {nodeid for nodeid in nodeids if shard_index(nodeid, 2) == 0} == first
    assert abs(len(first) - len(second)) < 100


def test_sharding_rejects_non_positive_total() -> None:
    with pytest.raises(ValueError):
        shard_index("TESTS/webapp/test_example.py::test_case", 0)
