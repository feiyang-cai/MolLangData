#!/usr/bin/env python3
"""
Create batch prompt JSONL from processed OPSIN/MolLangData results.

Uses the same difficulty routing and prompt construction as get_prompt_description_from_iupac.py:
- Difficulty: utils.molecule_properties.get_difficulty_level(smiles)
- Prompt: build_prompt_v13 (dynamic template: adds fused/spiro/bridged semantic sections before **Inputs:** when the molecule has those ring types)
- Model and reasoning_effort from config/llm_config.json (easy/medium/hard keys at top level)
No molecule_filters. Output is JSONL split by model and reasoning_effort (and optional TXT).
"""

import sys
import re
import argparse
import pandas as pd
from pathlib import Path
from typing import Optional, Dict, Tuple, List
import random
import json

# Paths: this script is in batch_prompt_generation/; repo root is parent (no scripts/ in this repo)
SCRIPT_DIR = Path(__file__).resolve().parent
REPO_ROOT = SCRIPT_DIR.parent
UTILS_DIR = REPO_ROOT / "utils"
if str(UTILS_DIR) not in sys.path:
    sys.path.insert(0, str(UTILS_DIR))
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

# Same difficulty as get_prompt_description_from_iupac.py
from utils.molecule_properties import get_difficulty_level

# Same prompt build as get_prompt_description_from_iupac.py
import importlib.util
_get_prompt_spec = importlib.util.spec_from_file_location(
    "get_prompt_description_from_iupac",
    REPO_ROOT / "get_prompt_description_from_iupac.py",
)
get_prompt_module = importlib.util.module_from_spec(_get_prompt_spec)
_get_prompt_spec.loader.exec_module(get_prompt_module)
build_prompt_v13 = get_prompt_module.build_prompt_v13


def load_xml_from_file(xml_path: Path, cid: str) -> Optional[str]:
    """Load XML metadata from file if it exists (for batch; get_prompt gets XML from OPSIN)."""
    xml_file = xml_path / f"{cid}.xml"
    if xml_file.exists():
        try:
            with open(xml_file, "r", encoding="utf-8") as f:
                return f.read().strip()
        except Exception:
            pass
    return None


def load_llm_config(config_path: Path) -> Dict:
    """Load LLM config JSON. Expects easy/medium/hard at top level with model, reasoning_effort (and optional timeout)."""
    if not config_path.exists():
        raise FileNotFoundError(f"LLM config not found: {config_path}")
    with open(config_path, "r", encoding="utf-8") as f:
        cfg = json.load(f)
    for level in ("easy", "medium", "hard"):
        if level not in cfg or not isinstance(cfg.get(level), dict):
            raise ValueError(f"LLM config must have top-level keys easy, medium, hard (each a dict with model, reasoning_effort)")
    return cfg


def get_model_and_effort_for_difficulty(config: Dict, difficulty: str) -> Tuple[str, str]:
    """Get (model, reasoning_effort) for a difficulty level from config (same shape as get_prompt_description_from_iupac)."""
    entry = config.get(difficulty, config.get("easy", {}))
    model = entry.get("model", "")
    effort = entry.get("reasoning_effort", "medium")
    return (model, effort)


def _apply_exclude_prompt_options(prompt: str, exclude_iupac: bool, exclude_xml: bool) -> str:
    """Remove IUPAC/XML sections (by header line) if exclude flags are set. build_prompt_v13 already filled placeholders."""
    if exclude_iupac:
        prompt = re.sub(r"\n?\*\*IUPAC Name:\*\*[^\n]*", "", prompt)
    if exclude_xml:
        prompt = re.sub(r"\n?\*\*XML Metadata:\*\*[^\n]*", "", prompt)
    return prompt


def build_batch_request_entry(
    *,
    api_format: str,
    custom_id: str,
    model: str,
    reasoning_effort: str,
    prompt: str,
    chat_completions_url: str,
    responses_url: str,
) -> Dict:
    """
    Build a single JSONL line for OpenAI Batch-style request files.
    Only API fields are included (custom_id, method, url, body); difficulty_level
    is not part of the LLM API and is never written to JSONL.

    - chat_completions: POST /v1/chat/completions with {"model", "messages", "reasoning_effort"?}
    - responses: POST /v1/responses with {"model", "input", "reasoning": {"effort": ...}?}
    """
    api_format = (api_format or "").strip().lower()
    if api_format not in {"chat_completions", "responses"}:
        raise ValueError(f"Unsupported api_format={api_format!r} (expected 'chat_completions' or 'responses')")

    if api_format == "responses":
        body: Dict = {
            "model": model,
            "input": prompt,
        }
        if reasoning_effort:
            body["reasoning"] = {"effort": reasoning_effort}
        entry: Dict = {
            "custom_id": custom_id,
            "method": "POST",
            "url": responses_url,
            "body": body,
        }
    else:
        body = {
            "model": model,
            "messages": [{"role": "user", "content": prompt}],
        }
        if reasoning_effort:
            body["reasoning_effort"] = reasoning_effort
        entry = {
            "custom_id": custom_id,
            "method": "POST",
            "url": chat_completions_url,
            "body": body,
        }

    return entry


def extract_effort_from_entry(entry: Dict, api_format: str) -> str:
    """Normalize reasoning effort for grouping/file naming."""
    body = entry.get("body") or {}
    api_format = (api_format or "").strip().lower()
    if api_format == "responses":
        reasoning = body.get("reasoning") or {}
        return reasoning.get("effort") or "none"
    return body.get("reasoning_effort") or "none"


def main():
    parser = argparse.ArgumentParser(
        description="Create batch prompt JSONL from MolLangData TSV with difficulty-based model selection (same as get_prompt_description_from_iupac)",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples (run from repo root or batch_prompt_generation/):
  python batch_prompt_generation/create_batch_prompt_jsonl.py data/parsing_out data/output
  python batch_prompt_generation/create_batch_prompt_jsonl.py data/parsing_out data/output prompts/smiles_iupac_metadata_v13
  python create_batch_prompt_jsonl.py data/parsing_out data/output --no-txt
        """,
    )
    default_prompts = str(REPO_ROOT / "prompts" / "smiles_iupac_metadata_v13")
    parser.add_argument("input_folder", type=str, help="Folder containing parsing_results.tsv")
    parser.add_argument("output_folder", type=str, help="Output folder for JSONL (and optional TXT)")
    parser.add_argument(
        "prompts_folder",
        type=str,
        nargs="?",
        default=default_prompts,
        help=f"Folder with prompt templates (default: {default_prompts})",
    )
    parser.add_argument(
        "--llm-config",
        type=str,
        default=None,
        help="Path to llm_config.json (default: config/llm_config.json at repo root)",
    )
    parser.add_argument(
        "--xml-type",
        type=str,
        choices=["mollangdata_xml", "opsin_xml"],
        default="mollangdata_xml",
        help="XML folder name (default: mollangdata_xml)",
    )
    parser.add_argument("--exclude-iupac", action="store_true", help="Exclude IUPAC from prompts")
    parser.add_argument("--exclude-xml", action="store_true", help="Exclude XML metadata from prompts")
    parser.add_argument("--allow-dots", action="store_true", help="Allow SMILES with dots")
    parser.add_argument("--sample-size", type=int, default=None, help="Random sample size")
    parser.add_argument("--random-seed", type=int, default=533, help="Random seed for sampling")
    parser.add_argument("--custom-prefix", type=str, default="", help="Prefix for compound IDs")
    parser.add_argument(
        "--no-txt",
        action="store_true",
        help="Do not write individual prompt .txt files, only JSONL",
    )
    parser.add_argument(
        "--api-format",
        type=str,
        choices=["responses", "chat_completions"],
        default="chat_completions",
        help="Batch request format/endpoints to emit (default: chat_completions)",
    )
    parser.add_argument(
        "--chat-completions-url",
        type=str,
        default="/v1/chat/completions",
        help="URL field to use for chat completions JSONL entries (default: /v1/chat/completions)",
    )
    parser.add_argument(
        "--responses-url",
        type=str,
        default="/v1/responses",
        help="URL field to use for responses JSONL entries (default: /v1/responses)",
    )
    args = parser.parse_args()

    input_path = Path(args.input_folder)
    if not input_path.exists():
        print(f"Error: Input folder does not exist: {args.input_folder}")
        sys.exit(1)
    tsv_file = input_path / "parsing_results.tsv"
    if not tsv_file.exists():
        print(f"Error: parsing_results.tsv not found in {input_path}")
        sys.exit(1)

    prompts_path = Path(args.prompts_folder)
    if not prompts_path.exists():
        print(f"Error: Prompts folder does not exist: {args.prompts_folder}")
        sys.exit(1)

    llm_config_path = Path(args.llm_config) if args.llm_config else (REPO_ROOT / "config" / "llm_config.json")
    try:
        llm_config = load_llm_config(llm_config_path)
    except Exception as e:
        print(f"Error loading LLM config: {e}")
        sys.exit(1)

    xml_path = input_path / args.xml_type
    if not xml_path.exists():
        xml_path = None
    output_path = Path(args.output_folder)
    output_path.mkdir(parents=True, exist_ok=True)
    prompts_output_path = output_path / "prompts"
    if not args.no_txt:
        prompts_output_path.mkdir(parents=True, exist_ok=True)

    df = pd.read_csv(tsv_file, sep="\t", dtype=str, low_memory=False)
    print(f"Loaded {len(df):,} records")

    col_upper = {c.upper(): i for i, c in enumerate(df.columns)}
    cid_col = col_upper.get("PUBCHEM_COMPOUND_CID")
    smiles_col = col_upper.get("CANONICAL_SMILES") or col_upper.get("SMILES")
    iupac_col = col_upper.get("IUPAC_NAME")
    final_status_col = col_upper.get("FINAL_STATUS")
    dot_col = col_upper.get("PROVIDED_SMILES_CONTAINS_DOT")

    if smiles_col is None or iupac_col is None or final_status_col is None:
        print("Error: Need columns PUBCHEM_COMPOUND_CID, CANONICAL_SMILES (or SMILES), IUPAC_NAME, FINAL_STATUS")
        sys.exit(1)

    df = df[df.iloc[:, final_status_col].str.upper() == "PASS"]
    print(f"After FINAL_STATUS=PASS: {len(df):,}")

    if not args.allow_dots and dot_col is not None:
        df = df[df.iloc[:, dot_col].str.upper() != "TRUE"]
        print(f"After filtering dots: {len(df):,}")
    elif not args.allow_dots and dot_col is None and smiles_col is not None:
        df = df[~df.iloc[:, smiles_col].str.contains(".", na=False, regex=False)]
        print(f"After filtering dots (fallback): {len(df):,}")

    if args.sample_size and len(df) > args.sample_size:
        random.seed(args.random_seed)
        df = df.sample(n=args.sample_size, random_state=args.random_seed)
        print(f"After sampling: {len(df):,}")

    skipped = 0
    generated = 0
    difficulty_counts = {"easy": 0, "medium": 0, "hard": 0}
    jsonl_entries = []

    for idx, row in df.iterrows():
        cid = str(row.iloc[cid_col]).strip() if cid_col is not None else ""
        canonical_smiles = str(row.iloc[smiles_col]).strip() if smiles_col is not None else ""
        iupac = str(row.iloc[iupac_col]).strip() if iupac_col is not None else ""

        if not cid or not canonical_smiles or not iupac:
            skipped += 1
            continue
        if canonical_smiles.upper() in ("TRUE", "FALSE", "SUCCESS", "FAILURE", "WARNING", "ERROR", "PASS"):
            skipped += 1
            continue

        xml_metadata = ""
        if xml_path and cid:
            xml_metadata = load_xml_from_file(xml_path, cid) or ""

        # Same difficulty as get_prompt_description_from_iupac.py
        level = get_difficulty_level(canonical_smiles, iupac=iupac, xml_metadata=xml_metadata)
        difficulty_counts[level] = difficulty_counts.get(level, 0) + 1
        model, reasoning_effort = get_model_and_effort_for_difficulty(llm_config, level)

        # Same prompt construction as get_prompt_description_from_iupac.py (build_prompt_v13):
        # dynamic template adds fused/spiro/bridged semantic sections before **Inputs:** when present
        prompt = build_prompt_v13(
            prompts_path,
            iupac,
            canonical_smiles,
            xml_metadata,
            exclude_xml=args.exclude_xml,
        )
        prompt = _apply_exclude_prompt_options(prompt, args.exclude_iupac, args.exclude_xml)

        compound_id = f"{args.custom_prefix}_{cid}" if args.custom_prefix else cid

        if not args.no_txt:
            out_txt = prompts_output_path / f"{compound_id}.txt"
            with open(out_txt, "w", encoding="utf-8") as f:
                f.write(prompt)

        entry = build_batch_request_entry(
            api_format=args.api_format,
            custom_id=compound_id,
            model=model,
            reasoning_effort=reasoning_effort,
            prompt=prompt,
            chat_completions_url=args.chat_completions_url,
            responses_url=args.responses_url,
        )
        jsonl_entries.append((entry, level))
        generated += 1
        if generated % 1000 == 0:
            print(f"  {generated:,} / {len(df):,}")

    # Write JSONL by (model, reasoning_effort, difficulty_level); only API fields in file
    groups: Dict[Tuple[str, str, str], List[Dict]] = {}
    for entry, difficulty_level in jsonl_entries:
        model = entry["body"]["model"]
        effort = extract_effort_from_entry(entry, args.api_format)
        key = (model, effort, difficulty_level)
        if key not in groups:
            groups[key] = []
        groups[key].append(entry)

    jsonl_files = []
    for (model, effort, difficulty_level), entries in sorted(groups.items()):
        ms = model.replace("/", "_").replace("\\", "_").replace(":", "_")
        es = effort.replace("/", "_").replace("\\", "_").replace(":", "_")
        ds = difficulty_level.replace("/", "_").replace("\\", "_").replace(":", "_")
        out_jsonl = output_path / f"jobs_{args.api_format}_{ms}-{es}-{ds}.jsonl"
        with open(out_jsonl, "w", encoding="utf-8") as f:
            for e in entries:
                f.write(json.dumps(e) + "\n")
        jsonl_files.append((model, effort, difficulty_level, out_jsonl, len(entries)))
        print(f"  {out_jsonl.name}: {len(entries):,} (model={model}, reasoning_effort={effort}, difficulty={difficulty_level})")

    print(f"\nSummary: generated={generated:,}, skipped={skipped:,}")
    print("Difficulty:", difficulty_counts)
    print("JSONL files:", [f[3].name for f in jsonl_files])


if __name__ == "__main__":
    main()
