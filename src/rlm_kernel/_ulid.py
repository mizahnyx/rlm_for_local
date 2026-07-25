"""Minimal ULID implementation — stdlib only (no dependencies).

A ULID is a 26-character Crockford base32 string encoding:
  - 10 chars: 48-bit Unix timestamp in milliseconds
  - 16 chars: 80 bits of cryptographically random data

This module replaces the `python-ulid` dependency per spec §2 dependency budget
("PyYAML + git binary only").
"""

from __future__ import annotations

import os
import time

# Crockford base32 alphabet
_B32 = "0123456789ABCDEFGHJKMNPQRSTVWXYZ"
_B32_MAP = {c: i for i, c in enumerate(_B32)}


def _encode_time(ts_ms: int) -> str:
    """Encode a 48-bit timestamp into 10 base32 characters."""
    chars = []
    for _ in range(10):
        chars.append(_B32[ts_ms & 0x1F])
        ts_ms >>= 5
    return "".join(reversed(chars))


def _encode_random(data: bytes) -> str:
    """Encode 10 bytes (80 bits) into 16 base32 characters."""
    # Convert 10 bytes into an integer for base32 encoding
    n = int.from_bytes(data, "big")
    chars = []
    for _ in range(16):
        chars.append(_B32[n & 0x1F])
        n >>= 5
    return "".join(reversed(chars))


def new() -> str:
    """Generate a new ULID as a 26-character string."""
    ts_ms = int(time.time() * 1000)
    rand_bytes = os.urandom(10)
    return _encode_time(ts_ms) + _encode_random(rand_bytes)
