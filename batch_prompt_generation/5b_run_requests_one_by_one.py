#!/usr/bin/env python3
"""
Run request JSONL files one-by-one (synchronous), instead of OpenAI Batch jobs.

This script is designed to consume the JSONL produced by
`batch_prompt_generation/create_batch_prompt_jsonl.py`, i.e. each line like:

  {"custom_id": "...", "method": "POST", "url": "/v1/responses" | "/v1/chat/completions", "body": {...}}

Key features:
  - Choose backend: OpenAI vs Azure OpenAI (`--backend openai|azure`)
  - Reuse existing clients in `utils/openai_llm_call.py` and `utils/azure_llm_call.py`
  - Per-request logging to:
      - results JSONL (request + raw response body or error)
      - stats JSONL (token usage: input/prompt, output, reasoning/thinking when available)
  - Resume mode: skip custom_ids already present in results JSONL or with per-request files
  - Per-request subfolder request_outputs/{custom_id}/ with output.txt, result.json, stats.json
    (durable; nothing lost if the process crashes). Incomplete/failed requests are not logged.

Environment:
  - backend=openai: OPENAI_API_KEY
  - backend=azure:  AZURE_OPENAI_ENDPOINT (or ENDPOINT_URL), AZURE_OPENAI_API_KEY,
                    AZURE_OPENAI_API_VERSION (optional)

Note:
  Token usage fields differ across endpoints/models. We log what the API returns:
    - Chat Completions: usage.prompt_tokens, usage.completion_tokens, usage.total_tokens,
                        usage.completion_tokens_details.reasoning_tokens (if present)
    - Responses: usage.input_tokens, usage.output_tokens, usage.total_tokens,
                 usage.output_tokens_details.reasoning_tokens (if present)
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Iterator, Optional, Set, Tuple
import re

# Optional progress bar
try:
    from tqdm import tqdm  # type: ignore
except Exception:  # pragma: no cover
    tqdm = None

# Repo imports
SCRIPT_DIR = Path(__file__).resolve().parent
REPO_ROOT = SCRIPT_DIR.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from utils.openai_llm_call import get_api_client as get_openai_client  # noqa: E402
from utils.azure_llm_call import (  # noqa: E402
    get_api_client as get_azure_client,
)


def _to_dict(obj: Any) -> Any:
    """Best-effort convert OpenAI SDK objects to plain dicts."""
    if obj is None:
        return None
    if isinstance(obj, (str, int, float, bool, list, dict)):
        return obj
    # OpenAI SDK objects are pydantic-like
    md = getattr(obj, "model_dump", None)
    if callable(md):
        try:
            return md()
        except Exception:
            pass
    d = getattr(obj, "dict", None)
    if callable(d):
        try:
            return d()
        except Exception:
            pass
    # Fallback: string repr
    return {"_repr": repr(obj)}


def _extract_usage(url: str, response_dict: Dict[str, Any]) -> Dict[str, Any]:
    """Normalize usage fields for logging across endpoints."""
    usage = response_dict.get("usage") or {}
    out: Dict[str, Any] = {"raw": usage}

    if url == "/v1/chat/completions":
        out["prompt_tokens"] = usage.get("prompt_tokens")
        out["output_tokens"] = usage.get("completion_tokens")
        out["total_tokens"] = usage.get("total_tokens")
        details = usage.get("completion_tokens_details") or {}
        out["reasoning_tokens"] = details.get("reasoning_tokens")
        return out

    if url == "/v1/responses":
        out["prompt_tokens"] = usage.get("input_tokens")
        out["output_tokens"] = usage.get("output_tokens")
        out["total_tokens"] = usage.get("total_tokens")
        details = usage.get("output_tokens_details") or {}
        out["reasoning_tokens"] = details.get("reasoning_tokens")
        return out

    # Unknown endpoint
    return out


def _iter_request_jsonl(path: Path) -> Iterator[Tuple[int, Dict[str, Any]]]:
    """Yield (line_no, obj) for non-empty JSONL lines."""
    with open(path, "r", encoding="utf-8") as f:
        for i, line in enumerate(f, start=1):
            s = line.strip()
            if not s:
                continue
            yield i, json.loads(s)


def _collect_jsonl_paths(input_path: Path) -> list[Path]:
    if input_path.is_file():
        return [input_path]
    if not input_path.is_dir():
        raise FileNotFoundError(f"Not a file or directory: {input_path}")
    return sorted(input_path.glob("*.jsonl"))


def _count_requests(paths: list[Path], max_requests: Optional[int] = None) -> int:
    """Count valid requests (same rules as main loop) for progress bar total. Stops at max_requests if set."""
    n = 0
    for p in paths:
        for _, req in _iter_request_jsonl(p):
            if max_requests is not None and n >= max_requests:
                return n
            custom_id = req.get("custom_id")
            method = req.get("method")
            url = req.get("url")
            body = req.get("body")
            if not isinstance(custom_id, str) or not custom_id:
                continue
            if method != "POST":
                continue
            if not isinstance(url, str) or not isinstance(body, dict):
                continue
            n += 1
    return n


def _load_llm_config(path: Path) -> Dict[str, Any]:
    """Load llm_config.json (same shape as used elsewhere in this repo)."""
    if not path.exists():
        raise FileNotFoundError(f"LLM config not found: {path}")
    with open(path, "r", encoding="utf-8") as f:
        data = json.load(f)
    # Defaults / expected fields
    data.setdefault("poll_interval", 30)
    data.setdefault("print_interval", 60)
    if "max_retries" not in data and "attempts" in data:
        data["max_retries"] = max(0, int(data["attempts"]) - 1)
    data.setdefault("max_retries", 5)
    for level in ("easy", "medium", "hard"):
        if level not in data or not isinstance(data[level], dict):
            raise ValueError(f"LLM config must contain '{level}' object")
        data[level].setdefault("timeout", 1200)
    return data


def _infer_difficulty_from_filename(path: Path) -> Optional[str]:
    """
    Infer difficulty from our job filenames, which typically end with '-easy.jsonl',
    '-medium.jsonl', or '-hard.jsonl'. Returns None if not found.
    """
    stem = path.stem
    # If file was split into parts, strip trailing _partN
    stem = re.sub(r"_part\d+$", "", stem)
    parts = stem.split("-")
    if not parts:
        return None
    last = parts[-1].lower()
    return last if last in {"easy", "medium", "hard"} else None


def _sanitize_custom_id(custom_id: str) -> str:
    """Make custom_id safe for use as a filename (no path separators or reserved chars)."""
    return re.sub(r'[/\\:*?"<>|]', "_", custom_id)


def _request_output_dir(outputs_dir: Path, custom_id: str) -> Path:
    """Per-request subfolder: request_outputs/{custom_id}/ (sanitized)."""
    return outputs_dir / _sanitize_custom_id(custom_id)


def _request_output_txt_path(outputs_dir: Path, custom_id: str) -> Path:
    """Path to the per-request output .txt inside the request subfolder."""
    return _request_output_dir(outputs_dir, custom_id) / "output.txt"


def _request_output_result_path(outputs_dir: Path, custom_id: str) -> Path:
    """Path to the per-request result .json inside the request subfolder."""
    return _request_output_dir(outputs_dir, custom_id) / "result.json"


def _request_output_stats_path(outputs_dir: Path, custom_id: str) -> Path:
    """Path to the per-request stats .json inside the request subfolder."""
    return _request_output_dir(outputs_dir, custom_id) / "stats.json"


def _request_completed_dir_exists(outputs_dir: Path, custom_id: str) -> bool:
    """True if this request has a completed output subfolder (for resume)."""
    d = _request_output_dir(outputs_dir, custom_id)
    return d.is_dir() and (d / "result.json").exists()


def _collect_pool_done_ids(pool_folder: Path) -> Set[str]:
    """Collect custom_ids that have result.json in any request_outputs subfolder under pool_folder."""
    out: Set[str] = set()
    pool_folder = pool_folder.resolve()
    if not pool_folder.exists() or not pool_folder.is_dir():
        return out
    for ro_dir in pool_folder.rglob("request_outputs"):
        if not ro_dir.is_dir():
            continue
        for d in ro_dir.iterdir():
            if d.is_dir() and (d / "result.json").exists():
                out.add(d.name)
    return out


def _extract_response_text(resp_dict: Dict[str, Any], url: str) -> str:
    """Extract the main text content from a response dict for saving to .txt."""
    if url == "/v1/chat/completions":
        choices = resp_dict.get("choices") or []
        if choices and isinstance(choices[0], dict):
            msg = choices[0].get("message") or {}
            content = msg.get("content")
            if isinstance(content, str):
                return content
        return ""
    if url == "/v1/responses":
        text = resp_dict.get("output_text")
        if isinstance(text, str) and text:
            return text
        output = resp_dict.get("output") or []
        parts = []
        for item in output if isinstance(output, list) else []:
            content = (item.get("content") if isinstance(item, dict) else getattr(item, "content", None)) or []
            for c in content if isinstance(content, list) else []:
                t = c.get("text") if isinstance(c, dict) else getattr(c, "text", None)
                if isinstance(t, str):
                    parts.append(t)
        return "\n".join(parts) if parts else ""
    return ""


def _read_done_custom_ids(results_path: Path) -> Set[str]:
    """Read custom_ids already recorded in results JSONL (for resume)."""
    done: Set[str] = set()
    if not results_path.exists():
        return done
    try:
        for _, obj in _iter_request_jsonl(results_path):
            cid = obj.get("custom_id")
            if isinstance(cid, str) and cid:
                done.add(cid)
    except Exception:
        raise
    return done


def _is_retryable_exception(e: Exception) -> bool:
    status = getattr(e, "status_code", None)
    if isinstance(status, int) and status in {429, 500, 502, 503, 504}:
        return True
    msg = str(e).lower()
    if "rate limit" in msg or "timeout" in msg or "temporarily" in msg:
        return True
    return False


def _call_one(
    *,
    backend: str,
    url: str,
    body: Dict[str, Any],
    timeout: Optional[int],
    poll_interval: int,
    print_interval: int,
) -> Dict[str, Any]:
    """Call one request and return response body as dict."""
    backend = backend.lower().strip()
    if backend == "openai":
        client = get_openai_client(timeout=timeout)
    elif backend == "azure":
        client = get_azure_client(timeout=timeout)
    else:
        raise ValueError(f"Unsupported backend: {backend}")

    if url == "/v1/chat/completions":
        timeout_str = f" (timeout: {timeout}s)" if timeout is not None else ""
        print(f"  Calling API{timeout_str}...")
        resp = client.chat.completions.create(**body)
        return _to_dict(resp)

    if url == "/v1/responses":
        # Use background mode for both Azure + OpenAI: submit -> poll -> retrieve (cancel on timeout).
        timeout_str = f" (timeout: {timeout}s)" if timeout is not None else ""
        print(f"  Submitting background job{timeout_str}...")

        submit_body: Dict[str, Any] = dict(body)
        inp = submit_body.get("input")
        if isinstance(inp, str) and inp:
            # Normalize string prompt to a single user message for consistency.
            submit_body["input"] = [{"role": "user", "content": inp}]
        submit_body["background"] = True

        try:
            submitted = client.responses.create(**submit_body)
        except Exception as e:
            # Fallback to synchronous call if SDK/server doesn't support background mode.
            # We keep the same normalized input, just drop `background`.
            print(f"  Warning: background submit failed ({e}); falling back to synchronous call...")
            fallback_body = dict(submit_body)
            fallback_body.pop("background", None)
            resp = client.responses.create(**fallback_body)
            return _to_dict(resp)

        response_id = getattr(submitted, "id", None)
        if not isinstance(response_id, str) or not response_id:
            # If we can't poll, return the submission object as-is
            return _to_dict(submitted)
        status = getattr(submitted, "status", None) or "submitted"
        print(f"  Job ID: {response_id}, Status: {status}")
        print(f"  Polling for completion{timeout_str}...")

        t0 = time.time()
        last_print = t0
        while True:
            elapsed = time.time() - t0
            if timeout is not None and elapsed >= timeout:
                try:
                    client.responses.cancel(response_id)
                except Exception:
                    pass
                raise RuntimeError(f"Responses job timed out after {timeout}s")

            try:
                resp = client.responses.retrieve(response_id)
            except Exception:
                resp = None
            if not resp:
                raise RuntimeError("Failed to check responses job status")

            status = getattr(resp, "status", None)
            if status == "completed":
                return _to_dict(resp)
            if status == "failed":
                return _to_dict(resp)
            if status == "incomplete":
                return _to_dict(resp)

            if status in ("queued", "in_progress"):
                now = time.time()
                if print_interval and (now - last_print) >= print_interval:
                    print(f"  Status: {status} (elapsed {elapsed:.1f}s)")
                    last_print = now
                time.sleep(poll_interval)
                continue

            time.sleep(poll_interval)

    raise ValueError(f"Unsupported url in request: {url!r} (expected /v1/chat/completions or /v1/responses)")


def main() -> None:
    ap = argparse.ArgumentParser(
        description="Run LLM request JSONL one-by-one (OpenAI or Azure), with per-request usage logging.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  # Run all JSONL in a directory against OpenAI
  python batch_prompt_generation/run_requests_one_by_one.py ./jobs_dir --backend openai --output-dir ./runs/run1

  # Run one JSONL file against Azure OpenAI
  python batch_prompt_generation/run_requests_one_by_one.py ./jobs_dir/jobs_responses_*.jsonl --backend azure --output-dir ./runs/azure_run

  # Resume (skip custom_ids already in results or request_outputs/)
  python batch_prompt_generation/run_requests_one_by_one.py ./jobs_dir --backend openai --output-dir ./runs/run1 --resume

  # Resume and also skip requests completed in another run (pool folder)
  python batch_prompt_generation/run_requests_one_by_one.py ./jobs_dir --backend openai --output-dir ./runs/run2 --resume --pool-folder ./runs/run1
        """,
    )
    ap.add_argument("input", type=str, help="A .jsonl file or a directory containing .jsonl files")
    ap.add_argument(
        "--llm-config",
        type=str,
        default=str(REPO_ROOT / "config" / "llm_config.json"),
        help="Path to llm_config.json (default: config/llm_config.json)",
    )
    ap.add_argument(
        "--backend",
        type=str,
        choices=["openai", "azure"],
        default=None,
        help="Which backend to call (default: llm_config.json backend)",
    )
    ap.add_argument("--output-dir", type=str, required=True, help="Directory to write results/stats files")
    ap.add_argument("--resume", action="store_true", help="Skip custom_ids already present in results JSONL or in request_outputs/")
    ap.add_argument(
        "--pool-folder",
        type=str,
        default=None,
        help="When --resume: also skip requests already completed in this output dir (expects request_outputs/ with subfolders)",
    )
    ap.add_argument("--max-requests", type=int, default=None, help="Stop after N requests (across all files)")
    ap.add_argument("--timeout", type=int, default=None, help="Override timeout seconds (default: from llm_config.json)")
    ap.add_argument("--poll-interval", type=int, default=None, help="Override poll interval seconds (default: from llm_config.json)")
    ap.add_argument("--print-interval", type=int, default=None, help="Override print interval seconds (default: from llm_config.json)")
    ap.add_argument("--model", type=str, default=None, help="Override model for all requests (default: use from JSONL body)")
    ap.add_argument("--reasoning-effort", type=str, default=None, help="Override reasoning effort for all requests (default: use from JSONL body)")
    ap.add_argument("--no-tqdm", action="store_true", help="Disable tqdm progress bar")
    ap.add_argument("--max-retries", type=int, default=None, help="Override max retries for retryable failures (default: from llm_config.json)")
    ap.add_argument("--retry-sleep", type=float, default=2.0, help="Base sleep seconds between retries (default: 2.0)")
    ap.add_argument("--sleep", type=float, default=0.0, help="Sleep seconds between successful requests (default: 0)")
    args = ap.parse_args()

    input_path = Path(args.input)
    if not input_path.exists():
        print(f"Error: input path does not exist: {input_path}", file=sys.stderr)
        sys.exit(1)

    out_dir = Path(args.output_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    results_path = out_dir / f"results_{args.backend}.jsonl"
    stats_path = out_dir / f"stats_{args.backend}.jsonl"
    # Per-request subfolders; resume skips when request_outputs/{custom_id}/result.json exists
    request_outputs_dir = out_dir / "request_outputs"
    request_outputs_dir.mkdir(parents=True, exist_ok=True)

    llm_config = _load_llm_config(Path(args.llm_config))
    backend = args.backend or llm_config.get("backend")
    if backend not in {"openai", "azure"}:
        print(f"Error: invalid backend {backend!r} (set --backend or llm_config.json backend)", file=sys.stderr)
        sys.exit(1)
    args.backend = backend

    done_ids: Set[str] = set()
    pool_done_ids: Set[str] = set()  # when --pool-folder set and --resume, skip if custom_id in any request_outputs under pool
    if args.resume:
        done_ids = _read_done_custom_ids(results_path)
        n_dirs = 0
        if request_outputs_dir.exists():
            n_dirs = sum(1 for d in request_outputs_dir.iterdir() if d.is_dir() and (d / "result.json").exists())
        msg = f"Resume enabled. Completed: {len(done_ids):,} in {results_path.name}, {n_dirs:,} request subfolders in request_outputs/"
        if args.pool_folder:
            pool_done_ids = _collect_pool_done_ids(Path(args.pool_folder))
            if pool_done_ids:
                msg += f", {len(pool_done_ids):,} in pool folder {args.pool_folder} (all request_outputs subfolders)"
            else:
                msg += f" (pool folder empty or invalid: {args.pool_folder})"
        print(msg)

    paths = _collect_jsonl_paths(input_path)
    if not paths:
        print("No .jsonl files found.", file=sys.stderr)
        sys.exit(0)

    processed = 0
    succeeded = 0
    failed = 0
    skipped_count = 0
    run_start_time = time.time()
    run_started_at = datetime.now(timezone.utc).isoformat()

    use_tqdm = (tqdm is not None) and (not args.no_tqdm)
    pbar = None
    if use_tqdm:
        total_requests = _count_requests(paths, args.max_requests)
        pbar = tqdm(total=total_requests, unit="req", dynamic_ncols=True)

    with open(results_path, "a", encoding="utf-8") as f_out, open(
        stats_path, "a", encoding="utf-8"
    ) as f_stats:
        for p in paths:
            difficulty = _infer_difficulty_from_filename(p) or "easy"
            level_cfg = llm_config.get(difficulty, llm_config.get("easy", {})) or {}
            effective_timeout = args.timeout if args.timeout is not None else int(level_cfg.get("timeout", 1200))
            effective_poll_interval = args.poll_interval if args.poll_interval is not None else int(llm_config.get("poll_interval", 30))
            effective_print_interval = args.print_interval if args.print_interval is not None else int(llm_config.get("print_interval", 60))
            effective_max_retries = args.max_retries if args.max_retries is not None else int(llm_config.get("max_retries", 5))

            for line_no, req in _iter_request_jsonl(p):
                if args.max_requests is not None and processed >= args.max_requests:
                    break

                custom_id = req.get("custom_id")
                method = req.get("method")
                url = req.get("url")
                body = req.get("body")

                if not isinstance(custom_id, str) or not custom_id:
                    continue
                if args.resume and (
                    custom_id in done_ids
                    or _request_completed_dir_exists(request_outputs_dir, custom_id)
                    or custom_id in pool_done_ids
                ):
                    skipped_count += 1
                    if pbar is not None:
                        pbar.update(1)
                    continue
                if method != "POST":
                    continue
                if not isinstance(url, str) or not isinstance(body, dict):
                    continue

                # Apply CLI overrides for model and reasoning effort
                body_to_send = dict(body)
                if args.model is not None:
                    body_to_send["model"] = args.model
                if args.reasoning_effort is not None:
                    if url == "/v1/responses":
                        body_to_send["reasoning"] = {"effort": args.reasoning_effort}
                    else:
                        body_to_send["reasoning_effort"] = args.reasoning_effort

                processed += 1

                print(f"\nSubmitting job for {custom_id}...")
                t0 = time.time()
                last_exc: Optional[Exception] = None
                resp_dict: Optional[Dict[str, Any]] = None

                for attempt in range(effective_max_retries + 1):
                    try:
                        resp_dict = _call_one(
                            backend=args.backend,
                            url=url,
                            body=body_to_send,
                            timeout=effective_timeout,
                            poll_interval=effective_poll_interval,
                            print_interval=effective_print_interval,
                        )
                        last_exc = None
                        break
                    except Exception as e:
                        last_exc = e
                        if attempt >= effective_max_retries or not _is_retryable_exception(e):
                            break
                        sleep_s = args.retry_sleep * (2**attempt)
                        time.sleep(sleep_s)

                dt = time.time() - t0

                # Azure background mode can return a final object with status=failed/incomplete;
                # treat that as an error (but keep the response body for debugging).
                if (
                    last_exc is None
                    and resp_dict is not None
                    and not (
                        args.backend == "azure"
                        and url == "/v1/responses"
                        and isinstance(resp_dict.get("status"), str)
                        and resp_dict.get("status") != "completed"
                    )
                ):
                    succeeded += 1
                    response_id = resp_dict.get("id")
                    usage = _extract_usage(url, resp_dict)
                    out_line = {
                        "id": response_id,
                        "custom_id": custom_id,
                        "backend": args.backend,
                        "source_file": str(p),
                        "source_line": line_no,
                        "request": {"method": method, "url": url, "body": body_to_send},
                        "response": {"status_code": 200, "body": resp_dict},
                        "error": None,
                        "elapsed_seconds": dt,
                    }
                    stats_line = {
                        "custom_id": custom_id,
                        "backend": args.backend,
                        "url": url,
                        "model": (body_to_send.get("model") if isinstance(body_to_send, dict) else None),
                        "elapsed_seconds": dt,
                        "prompt_tokens": usage.get("prompt_tokens"),
                        "output_tokens": usage.get("output_tokens"),
                        "reasoning_tokens": usage.get("reasoning_tokens"),
                        "total_tokens": usage.get("total_tokens"),
                        "usage_raw": usage.get("raw"),
                    }
                    # Write per-request subfolder first so we don't lose data on crash
                    req_dir = _request_output_dir(request_outputs_dir, custom_id)
                    req_dir.mkdir(parents=True, exist_ok=True)
                    response_text = _extract_response_text(resp_dict, url)
                    _request_output_txt_path(request_outputs_dir, custom_id).write_text(response_text, encoding="utf-8")
                    _request_output_result_path(request_outputs_dir, custom_id).write_text(
                        json.dumps(out_line, ensure_ascii=False), encoding="utf-8"
                    )
                    _request_output_stats_path(request_outputs_dir, custom_id).write_text(
                        json.dumps(stats_line, ensure_ascii=False), encoding="utf-8"
                    )
                    f_out.write(json.dumps(out_line) + "\n")
                    f_out.flush()
                    f_stats.write(json.dumps(stats_line) + "\n")
                    f_stats.flush()

                    if args.sleep and args.sleep > 0:
                        time.sleep(args.sleep)
                    if pbar is not None:
                        pbar.update(1)
                        pbar.set_postfix_str(f"ok={succeeded} fail={failed}", refresh=False)
                else:
                    failed += 1
                    if pbar is not None:
                        pbar.update(1)
                        pbar.set_postfix_str(f"ok={succeeded} fail={failed}", refresh=False)
                    # Do not log incomplete/failed requests (no per-request files, no errors JSONL)

            if args.max_requests is not None and processed >= args.max_requests:
                break

    if pbar is not None:
        pbar.close()

    wall_seconds = time.time() - run_start_time

    # Final summary (like run_api_calls_background.py)
    print(f"\n✓ Processing complete!")
    print(f"  Total processed this run: {processed:,}")
    print(f"  Successful: {succeeded:,}")
    print(f"  Failed: {failed:,}")
    print(f"  Skipped (resume): {skipped_count:,}")
    print(f"  Wall time: {wall_seconds:.1f}s")
    print(f"  Results: {results_path}")
    print(f"  Stats:   {stats_path}")
    print(f"  Per-request outputs: {request_outputs_dir}/{{custom_id}}/ (output.txt, result.json, stats.json); incomplete requests are not logged")

    # Statistics (aggregate token usage from stats JSONL - not in the original script)
    total_prompt = 0
    total_output = 0
    total_reasoning = 0
    total_tokens = 0
    n_stats = 0
    if stats_path.exists():
        for _, obj in _iter_request_jsonl(stats_path):
            n_stats += 1
            u = obj.get("prompt_tokens")
            if u is not None and isinstance(u, (int, float)):
                total_prompt += int(u)
            u = obj.get("output_tokens")
            if u is not None and isinstance(u, (int, float)):
                total_output += int(u)
            u = obj.get("reasoning_tokens")
            if u is not None and isinstance(u, (int, float)):
                total_reasoning += int(u)
            u = obj.get("total_tokens")
            if u is not None and isinstance(u, (int, float)):
                total_tokens += int(u)
    if n_stats > 0:
        print(f"\nStatistics (token usage, this run + any previous appends to stats):")
        print(f"  Requests in stats: {n_stats:,}")
        print(f"  Total prompt tokens:   {total_prompt:,}")
        print(f"  Total output tokens:   {total_output:,}")
        print(f"  Total reasoning tokens: {total_reasoning:,}")
        print(f"  Total tokens:          {total_tokens:,}")
        if succeeded > 0 and wall_seconds > 0:
            print(f"  Avg time per success:   {wall_seconds / succeeded:.1f}s")

    run_finished_at = datetime.now(timezone.utc).isoformat()
    summary = {
        "started_at": run_started_at,
        "finished_at": run_finished_at,
        "total_processed_this_run": processed,
        "succeeded": succeeded,
        "failed": failed,
        "skipped_resume": skipped_count,
        "wall_seconds": round(wall_seconds, 2),
        "total_prompt_tokens": total_prompt,
        "total_output_tokens": total_output,
        "total_reasoning_tokens": total_reasoning,
        "total_tokens": total_tokens,
        "requests_in_stats": n_stats,
    }
    # Timestamped filename so multiple runs in the same output dir don't overwrite
    started_safe = run_started_at.replace(":", "-").replace("+00:00", "Z")[:19]
    summary_path = out_dir / f"run_summary_{started_safe}.json"
    with open(summary_path, "w", encoding="utf-8") as f:
        json.dump(summary, f, indent=2)
    print(f"  Run summary saved: {summary_path}")


if __name__ == "__main__":
    main()

