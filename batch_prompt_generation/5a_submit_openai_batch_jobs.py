#!/usr/bin/env python3
"""
Submit OpenAI Batch API jobs from JSONL files produced by create_batch_prompt_jsonl.py.

Workflow:
  1. Upload JSONL file(s) with purpose="batch" (optionally split if over 50k requests).
  2. Create a batch per file with endpoint from the JSONL (e.g. /v1/chat/completions or /v1/responses).
  3. Optionally wait until batches complete (poll).
  4. Optionally retrieve output and error files to a local directory.

Requires: pip install openai
Environment: OPENAI_API_KEY

Ref: https://platform.openai.com/docs/api-reference/batch
"""

from __future__ import annotations

import argparse
import io
import json
import os
import sys
import time
from pathlib import Path
from typing import Any, Iterator

try:
    from openai import OpenAI
except ImportError as e:
    raise ImportError("openai package required. Install with: pip install openai") from e

# OpenAI Batch limit (docs): max requests per file
MAX_REQUESTS_PER_FILE = 50_000

SCRIPT_DIR = Path(__file__).resolve().parent
REPO_ROOT = SCRIPT_DIR.parent


def get_client(timeout: int | None = None) -> OpenAI:
    """Initialize OpenAI client from OPENAI_API_KEY."""
    api_key = os.getenv("OPENAI_API_KEY")
    if not api_key:
        raise RuntimeError("OPENAI_API_KEY environment variable is not set.")
    kwargs: dict[str, Any] = {"api_key": api_key}
    if timeout is not None:
        kwargs["timeout"] = timeout
    return OpenAI(**kwargs)


def infer_endpoint_from_jsonl(path: Path) -> str:
    """Read first line of JSONL and return the 'url' field (e.g. /v1/chat/completions)."""
    with open(path, "r", encoding="utf-8") as f:
        first = f.readline()
    if not first.strip():
        raise ValueError(f"Empty or invalid JSONL: {path}")
    obj = json.loads(first)
    url = obj.get("url")
    if not url or not isinstance(url, str):
        raise ValueError(f"JSONL first line missing 'url': {path}")
    return url


def count_jsonl_lines(path: Path) -> int:
    """Count non-empty lines in a JSONL file."""
    n = 0
    with open(path, "r", encoding="utf-8") as f:
        for line in f:
            if line.strip():
                n += 1
    return n


def iter_jsonl_chunks(
    path: Path,
    max_requests: int = MAX_REQUESTS_PER_FILE,
) -> Iterator[list[dict[str, Any]]]:
    """
    Yield chunks of request objects from a JSONL file, each chunk with at most
    max_requests items.
    """
    chunk: list[dict[str, Any]] = []
    for line in open(path, "r", encoding="utf-8"):
        line = line.strip()
        if not line:
            continue
        obj = json.loads(line)
        if chunk and len(chunk) >= max_requests:
            yield chunk
            chunk = []
        chunk.append(obj)
    if chunk:
        yield chunk


def collect_jsonl_paths(input_path: Path) -> list[Path]:
    """Return list of .jsonl files: either [input_path] or all .jsonl under input_path if directory."""
    if input_path.is_file():
        if input_path.suffix.lower() != ".jsonl":
            print(f"Warning: not a .jsonl file: {input_path}", file=sys.stderr)
        return [input_path]
    if not input_path.is_dir():
        raise FileNotFoundError(f"Not a file or directory: {input_path}")
    return sorted(input_path.glob("*.jsonl"))


def upload_and_create_batches(
    client: OpenAI,
    jsonl_path: Path,
    endpoint: str,
    completion_window: str = "24h",
    max_requests_per_file: int = MAX_REQUESTS_PER_FILE,
    metadata: dict[str, str] | None = None,
) -> list[dict[str, Any]]:
    """
    Upload JSONL (splitting if needed by request count) and create one batch per file.
    Returns list of batch info dicts: {"batch_id", "input_file_id", "endpoint", "request_count", "split_index"}.
    """
    total_lines = count_jsonl_lines(jsonl_path)
    if total_lines == 0:
        return []

    batches_info: list[dict[str, Any]] = []
    chunks = list(iter_jsonl_chunks(jsonl_path, max_requests_per_file))
    for split_index, requests in enumerate(chunks):
        # Write chunk to a temp-like in-memory buffer (we need a file-like for upload)
        buf = io.BytesIO()
        for obj in requests:
            buf.write((json.dumps(obj) + "\n").encode("utf-8"))
        buf.seek(0)
        name = jsonl_path.name
        if len(chunks) > 1:
            name = f"{jsonl_path.stem}_part{split_index}{jsonl_path.suffix}"
        # Upload with purpose="batch"
        file_obj = client.files.create(file=(name, buf), purpose="batch")
        batch = client.batches.create(
            input_file_id=file_obj.id,
            endpoint=endpoint,
            completion_window=completion_window,
            metadata=metadata or {},
        )
        batches_info.append({
            "batch_id": batch.id,
            "input_file_id": file_obj.id,
            "endpoint": endpoint,
            "request_count": len(requests),
            "split_index": split_index if len(chunks) > 1 else None,
            "source": str(jsonl_path),
        })
    return batches_info


def wait_for_batches(
    client: OpenAI,
    batch_ids: list[str],
    poll_interval_seconds: int = 60,
) -> dict[str, str]:
    """Poll until each batch is in a terminal state. Returns batch_id -> status."""
    terminal = {"completed", "failed", "expired", "cancelled"}
    statuses: dict[str, str] = {}
    remaining = set(batch_ids)
    while remaining:
        for bid in list(remaining):
            b = client.batches.retrieve(bid)
            statuses[bid] = b.status
            if b.status in terminal:
                remaining.discard(bid)
        if remaining:
            time.sleep(poll_interval_seconds)
    return statuses


def retrieve_batch_results(
    client: OpenAI,
    batch_id: str,
    output_dir: Path,
) -> tuple[Path | None, Path | None]:
    """
    Download output and error files for a completed batch to output_dir.
    Returns (output_path, error_path); either can be None if not present.
    """
    b = client.batches.retrieve(batch_id)
    output_path = None
    error_path = None
    if getattr(b, "output_file_id", None):
        content = client.files.content(b.output_file_id).content
        out_file = output_dir / f"{batch_id}_output.jsonl"
        out_file.write_bytes(content)
        output_path = out_file
    if getattr(b, "error_file_id", None):
        content = client.files.content(b.error_file_id).content
        err_file = output_dir / f"{batch_id}_errors.jsonl"
        err_file.write_bytes(content)
        error_path = err_file
    return output_path, error_path


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Submit OpenAI Batch API jobs from JSONL (upload, then wait and retrieve results by default).",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  # Submit, wait for completion, and download results (default)
  python batch_prompt_generation/submit_openai_batch_jobs.py ./jobs_dir --output-dir ./results

  # Submit only (no wait, no retrieve)
  python batch_prompt_generation/submit_openai_batch_jobs.py ./jobs_dir --no-wait

  # Split large files at 20k requests per batch
  python batch_prompt_generation/submit_openai_batch_jobs.py ./jobs_dir --max-requests 20000 --output-dir ./results
        """,
    )
    parser.add_argument(
        "input",
        type=str,
        help="Path to a single .jsonl file or a directory containing .jsonl files",
    )
    parser.add_argument(
        "--output-dir",
        type=str,
        default=None,
        help="Directory for retrieved output/error files (default: batch_results)",
    )
    parser.add_argument(
        "--no-wait",
        dest="wait",
        action="store_false",
        default=True,
        help="Submit only; do not wait for completion or retrieve results (default: wait and retrieve)",
    )
    parser.add_argument(
        "--poll-interval",
        type=int,
        default=60,
        help="Seconds between status polls when waiting (default: 60)",
    )
    parser.add_argument(
        "--max-requests",
        type=int,
        default=MAX_REQUESTS_PER_FILE,
        help=f"Max requests per batch file when splitting (default: {MAX_REQUESTS_PER_FILE})",
    )
    parser.add_argument(
        "--completion-window",
        type=str,
        default="24h",
        help="Batch completion window (default: 24h)",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Only list files and inferred endpoints, do not upload or create batches",
    )
    args = parser.parse_args()

    input_path = Path(args.input)
    if not input_path.exists():
        print(f"Error: input path does not exist: {input_path}", file=sys.stderr)
        sys.exit(1)

    try:
        client = get_client()
    except RuntimeError as e:
        print(f"Error: {e}", file=sys.stderr)
        sys.exit(1)

    paths = collect_jsonl_paths(input_path)
    if not paths:
        print("No .jsonl files found.", file=sys.stderr)
        sys.exit(0)

    if args.dry_run:
        for p in paths:
            try:
                endpoint = infer_endpoint_from_jsonl(p)
                n = count_jsonl_lines(p)
                print(f"  {p}  endpoint={endpoint}  lines={n}")
            except Exception as e:
                print(f"  {p}  error={e}")
        return

    all_batches: list[dict[str, Any]] = []
    for jsonl_path in paths:
        try:
            endpoint = infer_endpoint_from_jsonl(jsonl_path)
        except ValueError as e:
            print(f"Skip {jsonl_path}: {e}", file=sys.stderr)
            continue
        info_list = upload_and_create_batches(
            client,
            jsonl_path,
            endpoint,
            completion_window=args.completion_window,
            max_requests_per_file=args.max_requests,
        )
        for info in info_list:
            all_batches.append(info)
            print(f"  Created batch {info['batch_id']}  endpoint={endpoint}  requests={info['request_count']}  source={jsonl_path.name}")

    if not all_batches:
        print("No batches created.")
        return

    batch_ids = [b["batch_id"] for b in all_batches]
    print(f"\nTotal batches: {len(batch_ids)}")

    if args.wait:
        print("Waiting for batches to complete (polling)...")
        statuses = wait_for_batches(client, batch_ids, args.poll_interval)
        for bid, st in statuses.items():
            print(f"  {bid}  status={st}")
        out_dir = Path(args.output_dir) if args.output_dir else Path("batch_results")
        out_dir.mkdir(parents=True, exist_ok=True)
        for bid in batch_ids:
            try:
                out_p, err_p = retrieve_batch_results(client, bid, out_dir)
                if out_p:
                    print(f"  Saved {out_p}")
                if err_p:
                    print(f"  Saved {err_p}")
            except Exception as e:
                print(f"  Retrieve {bid}: {e}", file=sys.stderr)


if __name__ == "__main__":
    main()
