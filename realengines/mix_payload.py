#!/usr/bin/env python3
"""The stand-in application compute, shared by every engine.

Defining it once and importing it from :mod:`workload` guarantees that the
in-process engines and the shell-invoked ones perform byte-identical work, so a
timing difference between engines is scheduling overhead rather than a
different computation.

As a command:  mix_payload.py <input> <output>
"""

from __future__ import annotations

import hashlib
import sys


def mix(data: bytes) -> bytes:
    """Deterministic transformation that touches every byte."""
    digest = hashlib.sha256(data).digest()
    view = bytearray(data)
    for index in range(len(view)):
        view[index] ^= digest[index % len(digest)]
    return bytes(view)


def main() -> int:
    if len(sys.argv) != 3:
        print(f"usage: {sys.argv[0]} <input> <output>", file=sys.stderr)
        return 2
    with open(sys.argv[1], "rb") as handle:
        data = handle.read()
    with open(sys.argv[2], "wb") as handle:
        handle.write(mix(data))
    print(f"compute: {len(data)} bytes")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
