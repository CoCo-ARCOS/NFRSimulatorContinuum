#!/usr/bin/env python3
"""The stand-in application compute, shared by every engine.

Defining it once and importing it from :mod:`workload` guarantees that the
in-process engines and the shell-invoked ones perform byte-identical work, so a
timing difference between engines is scheduling overhead rather than a
different computation.

The stage also honours a target duration. The profiler is told each task's
``service_time_s`` and predicts makespan and energy from it; if the harness
instead runs for however long its placeholder happens to take, the
predicted-against-measured comparison measures the placeholder rather than the
cost model. The transform is therefore repeated until the declared service time
has elapsed -- real work on real bytes, keeping the CPU busy so the energy
figure stays meaningful, rather than a sleep that would idle it.

As a command:  mix_payload.py <input> <output> [--seconds S]
"""

from __future__ import annotations

import argparse
import hashlib
import sys
import time


def _transform(data: bytes) -> bytes:
    """One pass of a deterministic transformation that touches every byte.

    Vectorised: a per-byte Python loop runs at a few MiB/s, which at these
    payload sizes made the placeholder dominate every measurement. The work is
    the same XOR against a digest-derived key, done at memory bandwidth.
    """
    digest = hashlib.sha256(data).digest()
    try:
        import numpy as np
        block = np.frombuffer(data, dtype=np.uint8)
        key = np.frombuffer(digest, dtype=np.uint8)
        # Broadcasting the 32-byte key over a (n/32, 32) view avoids
        # materialising a tiled copy the size of the payload.
        size = block.size
        whole = (size // key.size) * key.size
        out = np.empty(size, dtype=np.uint8)
        np.bitwise_xor(block[:whole].reshape(-1, key.size), key,
                       out=out[:whole].reshape(-1, key.size))
        if whole < size:
            np.bitwise_xor(block[whole:], key[:size - whole], out=out[whole:])
        return out.tobytes()
    except ImportError:
        # Big-integer XOR is still C-level, and keeps the module usable
        # wherever NumPy is not installed.
        key = (digest * (len(data) // len(digest) + 1))[:len(data)]
        width = len(data)
        mixed = int.from_bytes(data, "big") ^ int.from_bytes(key, "big")
        return mixed.to_bytes(width, "big")


def mix(data: bytes, seconds: float | None = None) -> bytes:
    """Transform ``data``, taking at least ``seconds`` of busy compute.

    Each pass rekeys from the digest of its own input, so repeating the
    transform keeps producing fresh work rather than undoing the previous pass.
    """
    started = time.perf_counter()
    result = _transform(data)
    if seconds:
        while time.perf_counter() - started < seconds:
            result = _transform(result)
    return result


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("input")
    parser.add_argument("output")
    parser.add_argument("--seconds", type=float, default=None,
                        help="Declared service time to emulate")
    args = parser.parse_args()

    with open(args.input, "rb") as handle:
        data = handle.read()
    started = time.perf_counter()
    result = mix(data, args.seconds)
    with open(args.output, "wb") as handle:
        handle.write(result)
    print(f"compute: {len(data)} bytes in {time.perf_counter() - started:.3f}s")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
