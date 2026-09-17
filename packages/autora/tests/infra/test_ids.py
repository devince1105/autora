import time
import uuid

import pytest

from autora.infra.ids import uuid7, uuid7_timestamp_ms


def test_version_and_variant():
    value = uuid7()
    assert value.version == 7
    assert value.variant == uuid.RFC_4122


def test_embeds_current_time():
    before = time.time_ns() // 1_000_000
    value = uuid7()
    after = time.time_ns() // 1_000_000
    assert before <= uuid7_timestamp_ms(value) <= after + 1


def test_strictly_increasing_within_process():
    values = [uuid7() for _ in range(20_000)]
    assert values == sorted(values)
    assert len(set(values)) == len(values)


def test_timestamp_rejects_non_v7():
    with pytest.raises(ValueError):
        uuid7_timestamp_ms(uuid.uuid4())
