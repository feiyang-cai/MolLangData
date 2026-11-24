#!/usr/bin/env python3
"""
Deterministic, non-overlapping sampling across many TSV files in a folder.

Given:
  - a folder containing many TSV files (each with a header row)
  - desired number of samples per round (n)
  - a round id (0-based)
  - a random seed

This script defines a stable "global random order" over all data rows using a
64-bit hash of (seed, file_name, data_row_index_within_file). Then:
  round 0 gets the first n rows in this order,
  round 1 gets the next n rows,
  ...

This guarantees:
  - repeatability for a fixed seed
  - no duplicates across rounds (and none within a round), at the line level
  - identical number of samples per round (if enough total rows exist)
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
from dataclasses import dataclass
from datetime import datetime, timezone
from heapq import heappop, heappush
from pathlib import Path
from typing import Iterable, Iterator, List, Optional, Tuple


@dataclass(frozen=True)
class RowRef:
    file_name: str
    data_row_index: int  # 0-based index within file, excluding header
    line: str            # TSV line (no trailing newline)


def _hash64(seed: int, file_name: str, data_row_index: int) -> int:
    """
    Stable 64-bit hash, independent of PYTHONHASHSEED.
    """
    msg = f"{seed}\t{file_name}\t{data_row_index}".encode("utf-8", errors="strict")
    digest = hashlib.blake2b(msg, digest_size=8).digest()
    return int.from_bytes(digest, byteorder="big", signed=False)


def _format_k(n_samples: int) -> str:
    """
    Format sample count into an 'Xk' string for folder naming.
    Examples: 1000 -> '1k', 12500 -> '12.5k'
    """
    if n_samples < 0:
        return f"{n_samples}"
    if n_samples % 1000 == 0:
        return f"{n_samples // 1000}k"
    s = f"{n_samples / 1000:.6f}".rstrip("0").rstrip(".")
    return f"{s}k"


def _iter_tsv_files(tsv_dir: Path) -> List[Path]:
    files = sorted([p for p in tsv_dir.iterdir() if p.is_file() and p.suffix.lower() == ".tsv"])
    return files


def _iter_data_rows(tsv_files: List[Path]) -> Tuple[str, Iterator[RowRef], List[str]]:
    """
    Returns (header_line, iterator_of_data_rows, list_of_file_names).
    Validates that all TSV files share the same header line.
    """
    if not tsv_files:
        raise ValueError("No .tsv files found.")

    header: Optional[str] = None
    file_names: List[str] = []

    def gen() -> Iterator[RowRef]:
        nonlocal header
        for fp in tsv_files:
            file_names.append(fp.name)
            with fp.open("r", encoding="utf-8", errors="replace") as f:
                first = f.readline()
                if first == "":
                    # empty file: skip
                    continue
                this_header = first.rstrip("\n\r")
                if header is None:
                    header = this_header
                elif this_header != header:
                    raise ValueError(
                        "TSV header mismatch.\n"
                        f"- Expected: {header}\n"
                        f"- Found in {fp}: {this_header}"
                    )

                for idx, line in enumerate(f):
                    line = line.rstrip("\n\r")
                    if line == "":
                        continue
                    yield RowRef(file_name=fp.name, data_row_index=idx, line=line)

    it = gen()
    # header is set lazily; force at least one step so callers can rely on it
    try:
        first_row = next(it)
    except StopIteration:
        # All files empty or only headers
        raise ValueError("No data rows found in TSV files (only headers/empty files).")

    assert header is not None

    def chain_first() -> Iterator[RowRef]:
        yield first_row
        yield from it

    return header, chain_first(), file_names


def sample_round(
    *,
    tsv_dir: Path,
    n_samples: int,
    round_id: int,
    seed: int,
) -> Tuple[str, List[RowRef], dict]:
    if n_samples <= 0:
        raise ValueError(f"n_samples must be > 0 (got {n_samples}).")
    if round_id < 0:
        raise ValueError(f"round_id must be >= 0 (got {round_id}).")

    k = (round_id + 1) * n_samples
    start = round_id * n_samples
    end = start + n_samples

    tsv_files = _iter_tsv_files(tsv_dir)
    header, rows_iter, file_names = _iter_data_rows(tsv_files)

    # Keep the smallest k hashes using a max-heap (implemented via negative hash in min-heap).
    # Heap items: (-hash, file_name, data_row_index, line)
    heap: List[Tuple[int, str, int, str]] = []

    total_rows_seen = 0
    for row in rows_iter:
        total_rows_seen += 1
        h = _hash64(seed, row.file_name, row.data_row_index)
        heappush(heap, (-h, row.file_name, row.data_row_index, row.line))
        if len(heap) > k:
            heappop(heap)  # removes largest hash (most negative -h)

        if total_rows_seen % 5_000_000 == 0:
            print(
                f"[sample_from_tsv_folder] scanned {total_rows_seen:,} rows; heap={len(heap):,}/{k:,}",
                file=sys.stderr,
            )

    if len(heap) < end:
        raise ValueError(
            "Not enough total rows to satisfy requested round.\n"
            f"- total_rows_seen={total_rows_seen}\n"
            f"- need_at_least={end}\n"
            f"- (n_samples={n_samples}, round_id={round_id})"
        )

    # Sort by (hash asc, file_name, data_row_index) to build the deterministic global order.
    items = [(-neg_h, fn, idx, line) for (neg_h, fn, idx, line) in heap]
    items.sort(key=lambda t: (t[0], t[1], t[2]))

    chosen = [
        RowRef(file_name=fn, data_row_index=idx, line=line)
        for (_, fn, idx, line) in items[start:end]
    ]

    meta = {
        "tsv_dir": str(tsv_dir),
        "n_samples": n_samples,
        "round_id": round_id,
        "seed": seed,
        "k_kept": k,
        "slice_start": start,
        "slice_end": end,
        "total_rows_seen": total_rows_seen,
        "tsv_file_count": len(tsv_files),
        "tsv_files": file_names,
        "header": header,
        "hash": {
            "algo": "blake2b",
            "digest_size": 8,
            "message": "f'{seed}\\t{file_name}\\t{data_row_index}'",
        },
    }
    return header, chosen, meta


def main() -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Sample N rows from a folder of TSV files, deterministically and without overlap across rounds.\n\n"
            "Positional args (in order):\n"
            "  1) tsv_folder\n"
            "  2) n_samples\n"
            "  3) round_id (0-based)\n"
            "  4) seed (optional; default 533)\n"
            "  5) output_folder\n"
        ),
        formatter_class=argparse.RawTextHelpFormatter,
    )
    parser.add_argument("tsv_folder", type=str, help="Folder containing .tsv files (each with a header row).")
    parser.add_argument("n_samples", type=int, help="Number of samples per round.")
    parser.add_argument("round_id", type=int, help="Round id (0-based). Round 0 is the first round.")
    parser.add_argument("seed", type=int, nargs="?", default=533, help="Random seed (default: 533).")
    parser.add_argument("output_folder", type=str, help="Base output folder.")
    parser.add_argument(
        "--total-rounds",
        type=int,
        default=1,
        help=(
            "Optional: generate multiple rounds in a single scan.\n"
            "If provided, the positional round_id becomes the *start* round id.\n"
            "Example: round_id=0 --total-rounds=4 will write rounds 0,1,2,3.\n"
            "Default: 1 (only the specified round)."
        ),
    )

    args = parser.parse_args()

    tsv_dir = Path(args.tsv_folder)
    if not tsv_dir.exists() or not tsv_dir.is_dir():
        raise SystemExit(f"tsv_folder does not exist or is not a directory: {tsv_dir}")

    if args.total_rounds <= 0:
        raise SystemExit(f"--total-rounds must be > 0 (got {args.total_rounds}).")
    if args.round_id < 0:
        raise SystemExit(f"round_id must be >= 0 (got {args.round_id}).")

    out_base = Path(args.output_folder)

    # Multi-round in one pass:
    # keep top K where K = (start_round + total_rounds) * n_samples
    start_round = args.round_id
    end_round_exclusive = start_round + args.total_rounds
    k = end_round_exclusive * args.n_samples

    tsv_files = _iter_tsv_files(tsv_dir)
    header, rows_iter, file_names = _iter_data_rows(tsv_files)

    heap: List[Tuple[int, str, int, str]] = []
    total_rows_seen = 0
    for row in rows_iter:
        total_rows_seen += 1
        h = _hash64(args.seed, row.file_name, row.data_row_index)
        heappush(heap, (-h, row.file_name, row.data_row_index, row.line))
        if len(heap) > k:
            heappop(heap)
        if total_rows_seen % 5_000_000 == 0:
            print(
                f"[sample_from_tsv_folder] scanned {total_rows_seen:,} rows; heap={len(heap):,}/{k:,}",
                file=sys.stderr,
            )

    need_at_least = end_round_exclusive * args.n_samples
    if len(heap) < need_at_least:
        raise SystemExit(
            "Not enough total rows to satisfy requested rounds.\n"
            f"- total_rows_seen={total_rows_seen}\n"
            f"- need_at_least={need_at_least}\n"
            f"- (n_samples={args.n_samples}, start_round={start_round}, total_rounds={args.total_rounds})"
        )

    items = [(-neg_h, fn, idx, line) for (neg_h, fn, idx, line) in heap]
    items.sort(key=lambda t: (t[0], t[1], t[2]))

    created_at = datetime.now(timezone.utc).isoformat()
    for rid in range(start_round, end_round_exclusive):
        start = rid * args.n_samples
        end = start + args.n_samples

        out_dir = out_base / f"seed_{args.seed}_sample_{_format_k(args.n_samples)}_round_{rid}"
        out_dir.mkdir(parents=True, exist_ok=True)

        sampled_tsv = out_dir / "sampled.tsv"
        meta_path = out_dir / "metadata.json"

        with sampled_tsv.open("w", encoding="utf-8") as f:
            f.write(header + "\n")
            for (_, fn, idx, line) in items[start:end]:
                f.write(line + "\n")

        meta = {
            "tsv_dir": str(tsv_dir),
            "n_samples": args.n_samples,
            "round_id": rid,
            "start_round_id": start_round,
            "total_rounds": args.total_rounds,
            "seed": args.seed,
            "k_kept": k,
            "slice_start": start,
            "slice_end": end,
            "total_rows_seen": total_rows_seen,
            "tsv_file_count": len(tsv_files),
            "tsv_files": file_names,
            "header": header,
            "hash": {
                "algo": "blake2b",
                "digest_size": 8,
                "message": "f'{seed}\\t{file_name}\\t{data_row_index}'",
            },
            "output_dir": str(out_dir),
            "output_sampled_tsv": str(sampled_tsv),
            "created_at": created_at,
        }
        with meta_path.open("w", encoding="utf-8") as f:
            json.dump(meta, f, indent=2, ensure_ascii=False)
            f.write("\n")

        print(f"[sample_from_tsv_folder] wrote: {sampled_tsv}")
        print(f"[sample_from_tsv_folder] wrote: {meta_path}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())


