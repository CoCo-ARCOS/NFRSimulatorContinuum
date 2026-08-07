#!/usr/bin/env python3
"""Payload generation for the real-engine workload.

Random bytes are the wrong input for this evaluation. They are incompressible,
so every compression mechanism costs CPU and returns a ratio of 1.0 -- ZSTD
even grows the payload slightly. That silently disables an entire NFR family:
the volume-reduction clauses can never pay off, and a bandwidth-first contract
becomes indistinguishable from one without compression at all.

Real data compresses. This module generates payloads whose compression ratio
can be dialled to a target, reproducibly and without shipping a corpus, by
interleaving structured blocks (which compressors exploit) with incompressible
ones. It can also tile a real file, for evaluations that want a specific
scientific format rather than a synthetic stand-in.

As a command, so that shell-invoked engines generate the same payloads as the
in-process ones:

    payload.py --bytes 16777216 --kind synthetic --ratio 3.0 --out raw.bin
    payload.py --bytes 16777216 --kind file --source corpus/ --out raw.bin
"""

from __future__ import annotations

import argparse
import hashlib
import random
import sys
from pathlib import Path

# Block size for the structured/incompressible interleave. Large enough that a
# compressor's window sees the redundancy, small enough to keep the mix even.
BLOCK = 64 * 1024

# A small vocabulary produces text-like data with natural redundancy, which is
# what compressors are tuned for -- far closer to real payloads than a single
# repeated byte, which would compress absurdly and mislead the other way.
_WORDS = (
    "sensor reading timestamp station latitude longitude altitude pressure "
    "temperature humidity wind speed direction sample quality flag observation "
    "instrument calibration channel sequence record header payload checksum"
).split()

KINDS = ("synthetic", "random", "file")


# Text is assembled once and then sliced, rather than rebuilt per block.
# Building it word by word costs about 50 MiB/s, which at the payload sizes
# used here made generation a fifth of the measured runtime -- harness cost
# masquerading as workload cost.
_TEMPLATE_BYTES = 4 * 1024 * 1024


def _text_template(rng: random.Random) -> bytes:
    parts = []
    size = 0
    while size < _TEMPLATE_BYTES + BLOCK:
        line = f"{rng.randrange(1_000_000)}," + " ".join(
            rng.choice(_WORDS) for _ in range(12)
        ) + "\n"
        encoded = line.encode()
        parts.append(encoded)
        size += len(encoded)
    return b"".join(parts)


def _structured_block(template: bytes, rng: random.Random) -> bytes:
    """One block of text-like, compressible content, sliced from the template.

    Varying the offset keeps blocks non-identical, so the payload compresses
    like text rather than like a repeated buffer.
    """
    start = rng.randrange(0, len(template) - BLOCK)
    return template[start:start + BLOCK]


def synthetic(n_bytes: int, target_ratio: float = 3.0, seed: int = 0) -> bytes:
    """Payload whose compression ratio lands near ``target_ratio``.

    A compressor reduces the structured blocks almost entirely and the random
    blocks not at all, so with a structured fraction ``f`` the compressed size
    is roughly ``(1 - f)`` of the original: choosing ``f = 1 - 1/ratio`` puts
    the achieved ratio in the right neighbourhood. It is an approximation --
    the achieved ratio is reported by ``measure_ratio`` rather than assumed.
    """
    if target_ratio < 1.0:
        raise ValueError("target_ratio must be >= 1.0")
    rng = random.Random(seed)
    structured_fraction = 0.0 if target_ratio <= 1.0 else 1.0 - (1.0 / target_ratio)

    blocks = max(1, n_bytes // BLOCK)
    n_structured = int(round(blocks * structured_fraction))
    plan = [True] * n_structured + [False] * (blocks - n_structured)
    rng.shuffle(plan)

    template = _text_template(rng) if n_structured else b""
    out = bytearray()
    for is_structured in plan:
        out += _structured_block(template, rng) if is_structured else rng.randbytes(BLOCK)
    if len(out) < n_bytes:
        out += rng.randbytes(n_bytes - len(out))
    return bytes(out[:n_bytes])


def incompressible(n_bytes: int, seed: int = 0) -> bytes:
    """Seeded random bytes: reproducible, and still a ratio of 1.0."""
    return random.Random(seed).randbytes(n_bytes)


# Repeating a source inflates its compression ratio sharply: a corpus with a
# native ZSTD ratio of 2.8 measures 235 once tiled 80 times, because the
# compressor matches whole copies. Past this factor the payload says more about
# the tiling than about the data, so the run is warned.
TILE_WARN_FACTOR = 1.5


def _corpus_files(root: Path) -> list[Path]:
    """Every readable file under a directory, in a stable order."""
    return sorted(p for p in Path(root).rglob("*") if p.is_file() and p.stat().st_size > 0)


def from_source(source: Path, n_bytes: int, seed: int = 0) -> bytes:
    """Build a payload from a real file or a directory of real files.

    A directory is the better input: distinct files are concatenated, so the
    payload grows without repeating content and keeps the corpus's true
    compressibility. A single file must be tiled once the payload exceeds it,
    which inflates the ratio; that case warns with the factor involved.

    Content is used as raw bytes, so any format works. The format is not
    neutral for what is being measured, though -- already-compressed formats
    (JPEG, HDF5 with internal compression, .gz) sit near 1.0 and make the
    volume-reduction clauses unachievable, exactly like random bytes.
    """
    source = Path(source)
    if source.is_dir():
        files = _corpus_files(source)
        if not files:
            raise ValueError(f"{source} contains no readable files")
        rng = random.Random(seed)
        order = list(files)
        rng.shuffle(order)

        chunks: list[bytes] = []
        total = 0
        exhausted = 0
        while total < n_bytes:
            for path in order:
                data = path.read_bytes()
                chunks.append(data)
                total += len(data)
                if total >= n_bytes:
                    break
            else:
                # One full sweep of the corpus was not enough; going round
                # again repeats content, which is the tiling problem.
                exhausted += 1
                if exhausted == 1:
                    corpus = sum(p.stat().st_size for p in files)
                    factor = n_bytes / corpus if corpus else float("inf")
                    print(
                        f"warning: corpus {source} holds {corpus} bytes but "
                        f"{n_bytes} were requested ({factor:.1f}x); content will "
                        "repeat and the measured compression ratio will be "
                        "inflated. Add more files or lower --payload-bytes.",
                        file=sys.stderr,
                    )
        return b"".join(chunks)[:n_bytes]

    data = source.read_bytes()
    if not data:
        raise ValueError(f"{source} is empty")
    if len(data) >= n_bytes:
        return data[:n_bytes]

    factor = n_bytes / len(data)
    if factor > TILE_WARN_FACTOR:
        print(
            f"warning: {source} is {len(data)} bytes but {n_bytes} were "
            f"requested, so it is tiled {factor:.1f}x. Repetition inflates the "
            "compression ratio; pass a directory of distinct files instead.",
            file=sys.stderr,
        )
    repeats = (n_bytes // len(data)) + 1
    return (data * repeats)[:n_bytes]


# Retained under its original name for callers that pass a single file.
from_file = from_source


def make_payload(n_bytes: int, *, kind: str = "synthetic", ratio: float = 3.0,
                 seed: int = 0, source: Path | None = None) -> bytes:
    if kind == "random":
        return incompressible(n_bytes, seed)
    if kind == "file":
        if source is None:
            raise ValueError("kind='file' requires a source path (file or directory)")
        return from_source(source, n_bytes, seed)
    if kind == "synthetic":
        return synthetic(n_bytes, ratio, seed)
    raise ValueError(f"unknown payload kind {kind!r}; expected one of {KINDS}")


def measure_ratio(data: bytes, algorithm: str = "ZSTD") -> float:
    """Compression ratio actually achieved, for the run provenance.

    Recording this alongside the results keeps the evaluation honest: the
    target is an input, the achieved ratio is a measurement.
    """
    try:
        if algorithm.upper() == "LZ4":
            import lz4.frame
            packed = lz4.frame.compress(data)
        elif algorithm.upper() == "ZLIB":
            import zlib
            packed = zlib.compress(data)
        else:
            import zstandard
            packed = zstandard.ZstdCompressor().compress(data)
    except ImportError:
        return float("nan")
    return len(data) / len(packed) if packed else float("nan")


def describe(data: bytes) -> dict[str, float | str]:
    return {
        "bytes": len(data),
        "sha256": hashlib.sha256(data).hexdigest()[:16],
        "ratio_lz4": round(measure_ratio(data, "LZ4"), 3),
        "ratio_zstd": round(measure_ratio(data, "ZSTD"), 3),
        "ratio_zlib": round(measure_ratio(data, "ZLIB"), 3),
    }


def provenance(n_bytes: int, *, kind: str = "synthetic", ratio: float = 3.0,
               seed: int = 0, source: Path | None = None) -> dict[str, object]:
    """What the payload actually is, for the run record.

    The target ratio is an input; the achieved ratio is a measurement, and only
    the latter belongs in a results table. Generation is deterministic, so the
    payload measured here is byte-identical to the one the tasks process --
    including for the engines that generate it in a separate process.
    """
    data = make_payload(n_bytes, kind=kind, ratio=ratio, seed=seed, source=source)
    record: dict[str, object] = {
        "kind": kind,
        "seed": seed,
        "target_ratio": ratio if kind == "synthetic" else None,
        "source": str(source) if source else None,
        **describe(data),
    }
    if source is not None and Path(source).is_dir():
        files = _corpus_files(Path(source))
        record["corpus_files"] = len(files)
        record["corpus_bytes"] = sum(p.stat().st_size for p in files)
    return record


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--bytes", type=int, required=True)
    parser.add_argument("--kind", choices=KINDS, default="synthetic")
    parser.add_argument("--ratio", type=float, default=3.0,
                        help="Target compression ratio for kind=synthetic")
    parser.add_argument("--seed", type=int, default=0)
    parser.add_argument("--source", type=Path, default=None,
                        help="File, or directory of files, for kind=file")
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--describe", action="store_true")
    args = parser.parse_args()

    data = make_payload(args.bytes, kind=args.kind, ratio=args.ratio,
                        seed=args.seed, source=args.source)
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_bytes(data)
    if args.describe:
        print(describe(data))
    else:
        print(f"wrote {len(data)} bytes to {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())


# Keep the CLI honest about os.urandom's absence: seeding matters because the
# same payload must be reproducible across engines and across repeats.
assert BLOCK % 1024 == 0
