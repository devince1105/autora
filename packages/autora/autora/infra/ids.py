"""Time-ordered identifiers (UUIDv7, RFC 9562).

Every primary key and event id in Autora is a UUIDv7: sortable by creation time, native
``uuid`` column type, no extra dependency. Python 3.12 has no ``uuid.uuid7`` (added in
3.14), so this module provides one.

Layout: 48-bit unix ms | 4-bit version (7) | 12-bit counter | 2-bit variant | 62-bit random.
The 12-bit field is a per-process counter seeded randomly each millisecond, so ids created
by one process are strictly increasing even within the same millisecond.
"""

from __future__ import annotations

import os
import threading
import time
import uuid

_lock = threading.Lock()
_last_ms = 0
_counter = 0

_COUNTER_MAX = 0xFFF


def uuid7() -> uuid.UUID:
    global _last_ms, _counter
    with _lock:
        now_ms = time.time_ns() // 1_000_000
        if now_ms > _last_ms:
            _last_ms = now_ms
            # Seed low so there is headroom to increment within the same millisecond.
            _counter = int.from_bytes(os.urandom(2), "big") & 0x7FF
        else:
            _counter += 1
            if _counter > _COUNTER_MAX:
                # Counter exhausted (or clock went backwards): borrow the next millisecond.
                _last_ms += 1
                _counter = 0
        ms, counter = _last_ms, _counter

    rand_b = int.from_bytes(os.urandom(8), "big") & ((1 << 62) - 1)
    value = (ms & ((1 << 48) - 1)) << 80
    value |= 0x7 << 76
    value |= counter << 64
    value |= 0b10 << 62
    value |= rand_b
    return uuid.UUID(int=value)


def uuid7_timestamp_ms(value: uuid.UUID) -> int:
    """Unix epoch milliseconds embedded in a UUIDv7."""
    if value.version != 7:
        raise ValueError(f"not a UUIDv7: {value}")
    return value.int >> 80
