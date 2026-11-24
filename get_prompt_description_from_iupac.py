#!/usr/bin/env python3
"""
Standalone demo:

Input: an IUPAC name
Outputs:
  1) XML from OPSIN (prefers mollangdata_xml, falls back to opsin_xml)
  2) The prompt text used to generate the structure descriptions
  3) Optionally (--get-description): description from LLM (Azure or OpenAI backend)
"""

from __future__ import annotations

import argparse
import json
import shutil
import subprocess
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from utils.molecule_properties import get_difficulty_level

try:
    from utils.molecule_properties import (
        has_fused_ring_system,
        has_spiro_ring,
        has_bridged_ring,
    )
    PROPERTIES_AVAILABLE = True
except ImportError:
    PROPERTIES_AVAILABLE = False


def build_dynamic_prompt_template(
    default_template_path: Path,
    smiles: str,
    iupac: str,
    xml_metadata: str,
    exclude_xml: bool = False,
) -> str:
    """
    Build prompt template by combining default template with semantic sections.

    Semantic sections (fused, spiro, bridged) are added before the "**Inputs:**" section
    if the molecule has those ring types.

    When exclude_xml is enabled, we don't add semantic information because there is no XML information.
    """
    if not default_template_path.exists():
        raise FileNotFoundError(f"Default prompt template not found: {default_template_path}")

    template = default_template_path.read_text(encoding="utf-8")

    inputs_marker = "**Inputs:**"
    inputs_index = template.find(inputs_marker)

    if inputs_index == -1:
        return template

    before_inputs = template[:inputs_index]
    inputs_section = template[inputs_index:]

    semantic_sections: list[str] = []
    prompts_dir = default_template_path.parent

    if PROPERTIES_AVAILABLE and not exclude_xml:
        has_fused = has_fused_ring_system(smiles, iupac, xml_metadata)
        has_spiro = has_spiro_ring(smiles, iupac, xml_metadata)
        has_bridged = has_bridged_ring(smiles, iupac, xml_metadata)

        if has_fused:
            fused_path = prompts_dir / "fused_ring_semantic.md"
            if fused_path.exists():
                semantic_sections.append(fused_path.read_text(encoding="utf-8"))

        if has_spiro:
            spiro_path = prompts_dir / "spiro_ring_semantic.md"
            if spiro_path.exists():
                semantic_sections.append(spiro_path.read_text(encoding="utf-8"))

        if has_bridged:
            bridged_path = prompts_dir / "bridged_ring_semantic.md"
            if bridged_path.exists():
                semantic_sections.append(bridged_path.read_text(encoding="utf-8"))

    if semantic_sections:
        semantic_content = "\n\n" + "\n\n".join(semantic_sections) + "\n\n---\n\n"
        return before_inputs + semantic_content + inputs_section
    return template


def _extract_json_from_process_output(stdout: str) -> dict:
    """
    OPSIN prints a single JSON object to stdout (may include other logs).
    Parse the last line that looks like JSON.
    """
    lines = [ln.strip() for ln in stdout.splitlines() if ln.strip()]
    for ln in reversed(lines):
        if ln.startswith("{") and ln.endswith("}"):
            return json.loads(ln)
    start = stdout.find("{")
    end = stdout.rfind("}")
    if start >= 0 and end > start:
        return json.loads(stdout[start : end + 1])
    raise ValueError("Could not find JSON object in OPSIN output.")


def run_opsin_java(jar_path: Path, iupac: str, timeout_s: int = 30) -> dict:
    java = shutil.which("java")
    if not java:
        raise RuntimeError(
            "Could not find 'java' on PATH.\n"
            "Install a JDK/JRE (e.g., Temurin / Oracle JDK) and retry."
        )

    cmd = [
        java,
        "-cp",
        str(jar_path),
        "uk.ac.cam.ch.wwmm.opsin.ProcessSingleIupacForMolLangData",
        iupac,
    ]
    p = subprocess.run(
        cmd,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        timeout=timeout_s,
        check=False,
    )
    if p.returncode != 0:
        stderr = (p.stderr or "").strip()
        if "Unable to locate a Java Runtime" in stderr:
            raise RuntimeError(
                "Java was invoked but macOS could not locate a Java Runtime.\n"
                "Install a JDK/JRE (e.g., Temurin / Oracle JDK) and retry.\n"
                f"Raw stderr:\n{stderr}"
            )
        raise RuntimeError(f"OPSIN failed (rc={p.returncode}). stderr:\n{stderr}")
    return _extract_json_from_process_output(p.stdout)


def build_prompt_v13(
    prompts_dir: Path,
    iupac: str,
    smiles: str,
    xml_metadata: str,
    exclude_xml: bool = False,
) -> str:
    """Build prompt with dynamic template (fused/spiro/bridged semantic sections) then fill placeholders."""
    default_path = prompts_dir / "default.md"
    template = build_dynamic_prompt_template(
        default_path, smiles, iupac, xml_metadata, exclude_xml=exclude_xml
    )
    return (
        template.replace("{IUPAC}", iupac)
        .replace("{SMILES}", smiles)
        .replace("{XML_METADATA}", xml_metadata)
    )


# Folder under script dir where OPSIN jar is expected by default
OPSIN_JAR_DIR = "jar"


def _find_default_jar(script_dir: Path) -> Path:
    jar_dir = script_dir / OPSIN_JAR_DIR
    jars = sorted(jar_dir.glob("opsin-core-*-jar-with-dependencies.jar"))
    if not jars:
        raise FileNotFoundError(
            f"Could not find opsin-core-*-jar-with-dependencies.jar in {jar_dir}. "
            f"Place the OPSIN jar in the '{OPSIN_JAR_DIR}/' folder or use --opsin-jar."
        )
    return jars[-1]


# Defaults for optional LLM config keys (used when key is missing)
_DEFAULT_LLM_TIMEOUT = 1200
_DEFAULT_LLM_POLL_INTERVAL = 30
_DEFAULT_LLM_PRINT_INTERVAL = 60
_DEFAULT_LLM_ATTEMPTS = 1


def _load_llm_config(config_path: Path) -> dict[str, Any]:
    """Load config: backend, poll_interval, print_interval, attempts; easy/medium/hard each have model, reasoning_effort, timeout."""
    if not config_path.exists():
        raise FileNotFoundError(f"LLM config not found: {config_path}")
    with open(config_path, encoding="utf-8") as f:
        data = json.load(f)
    backend = data.get("backend")
    if backend not in ("azure", "openai"):
        raise ValueError("LLM config must have 'backend' set to 'azure' or 'openai'")
    for level in ("easy", "medium", "hard"):
        if level not in data or not isinstance(data[level], dict):
            raise ValueError(f"LLM config must have '{level}' with model, reasoning_effort, timeout")
        level_c = data[level]
        if "timeout" not in level_c:
            raise ValueError(f"LLM config level '{level}' must have 'timeout' (seconds)")
    data.setdefault("poll_interval", _DEFAULT_LLM_POLL_INTERVAL)
    data.setdefault("print_interval", _DEFAULT_LLM_PRINT_INTERVAL)
    data.setdefault("attempts", _DEFAULT_LLM_ATTEMPTS)
    return data


def _confirm_continue(message: str, default_no: bool = True) -> bool:
    """Ask user y/N; default_no=True means default is N."""
    suffix = " [y/N]: " if default_no else " [Y/n]: "
    try:
        answer = input(message + suffix).strip().lower()
    except EOFError:
        return not default_no
    if not answer:
        return not default_no
    return answer in ("y", "yes")


def main() -> None:
    script_dir = Path(__file__).resolve().parent

    ap = argparse.ArgumentParser(description="Standalone demo: IUPAC -> XML + prompt; optionally get description from LLM (--get-description).")
    ap.add_argument("iupac", type=str, help="IUPAC name")
    ap.add_argument("--opsin-jar", type=str, default=None, help=f"Path to OPSIN jar (default: {OPSIN_JAR_DIR}/opsin-core-*-jar-with-dependencies.jar)")
    ap.add_argument("--timeout-s", type=int, default=30, help="Timeout for OPSIN call (seconds)")
    ap.add_argument("--prompts-dir", type=str, default=None, metavar="DIR", help="Directory containing prompt templates (default: prompts/smiles_iupac_metadata_v13)")
    ap.add_argument("-o", "--output", type=str, default=None, metavar="DIR", help="Output folder: write prompt.md, descriptions.txt (if --get-description), and generation_info.json; if not set, print to stdout")
    ap.add_argument("--get-description", action="store_true", help="Get structure description from LLM (Azure or OpenAI per config)")
    ap.add_argument("--llm-config", type=str, default=None, metavar="FILE", help="JSON config: backend, timeout, poll_interval, print_interval, model per difficulty (default: config/llm_config.json)")
    ap.add_argument("--model", type=str, default=None, help="Override model for LLM call (when --get-description)")
    ap.add_argument("--reasoning-effort", type=str, default=None, help="Override reasoning effort for LLM call (when --get-description)")
    args = ap.parse_args()

    jar_path = Path(args.opsin_jar) if args.opsin_jar else _find_default_jar(script_dir)
    prompts_dir = Path(args.prompts_dir) if args.prompts_dir else (script_dir / "prompts" / "smiles_iupac_metadata_v13")
    output_dir = Path(args.output) if args.output else None
    if output_dir is not None:
        output_dir.mkdir(parents=True, exist_ok=True)

    opsin_json = run_opsin_java(jar_path, args.iupac, timeout_s=args.timeout_s)

    mollangdata_status = opsin_json.get("mollangdata_status")
    if mollangdata_status is not None:
        print("=== OPSIN STATUS ===")
        print(mollangdata_status)
        print()

    if args.get_description and mollangdata_status is not None and str(mollangdata_status).strip().upper() != "SUCCESS":
        if not _confirm_continue("OPSIN status is not SUCCESS. Still get description from LLM?"):
            args = argparse.Namespace(**{**vars(args), "get_description": False})

    opsin_smiles = opsin_json.get("opsin_smiles") or ""
    mollangdata_smiles = opsin_json.get("mollangdata_smiles") or ""
    opsin_xml = opsin_json.get("opsin_xml") or ""
    mollangdata_xml = opsin_json.get("mollangdata_xml") or ""

    smiles = mollangdata_smiles or opsin_smiles
    xml_metadata = mollangdata_xml or opsin_xml

    if not smiles:
        raise SystemExit("OPSIN did not return any SMILES.")
    if not xml_metadata:
        raise SystemExit("OPSIN did not return any XML.")

    prompt = build_prompt_v13(prompts_dir, args.iupac, smiles, xml_metadata)
    print("=== IUPAC ===")
    print(args.iupac)
    print()

    print("=== SMILES ===")
    print(smiles)
    print()

    level = get_difficulty_level(smiles)
    print("=== DIFFICULTY LEVEL ===")
    print(level)
    print()

    description: str | None = None
    generation_info: dict[str, Any] | None = None

    if args.get_description:
        llm_config_path = Path(args.llm_config) if args.llm_config else (script_dir / "config" / "llm_config.json")
        config = _load_llm_config(llm_config_path)
        backend = config["backend"]
        level_config = config[level]
        model = args.model or level_config.get("model")
        reasoning_effort = args.reasoning_effort or level_config.get("reasoning_effort")
        # Timeout is per level (required in config). Attempts: per-level or global default.
        timeout = level_config["timeout"]
        attempts = level_config["attempts"] if "attempts" in level_config else config.get("attempts", _DEFAULT_LLM_ATTEMPTS)
        attempts = max(1, int(attempts))
        poll_interval = config.get("poll_interval", _DEFAULT_LLM_POLL_INTERVAL)
        print_interval = config.get("print_interval", _DEFAULT_LLM_PRINT_INTERVAL)
        if not model:
            raise SystemExit("LLM config must specify 'model' for each difficulty (or use --model)")
        if backend == "azure":
            from utils.azure_llm_call import get_description_from_llm
        elif backend == "openai":
            from utils.openai_llm_call import get_description_from_llm
        else:
            raise SystemExit(f"Unknown LLM backend in config: {backend!r}")
        print("=== LLM (description) ===")
        print(f"Backend: {backend}, Model: {model}, Reasoning effort: {reasoning_effort or 'default'}, Timeout: {timeout}s, Attempts: {attempts}")
        last_error = None
        generation_start = time.time()
        for attempt in range(1, attempts + 1):
            if attempts > 1:
                print(f"Attempt {attempt}/{attempts}...")
            if backend == "azure":
                print("Submitting job and polling for completion...")
            try:
                if backend == "azure":
                    description = get_description_from_llm(
                        prompt, model=model, reasoning_effort=reasoning_effort,
                        timeout=timeout, poll_interval=poll_interval, print_interval=print_interval,
                    )
                else:
                    description = get_description_from_llm(
                        prompt, model=model, reasoning_effort=reasoning_effort,
                        timeout=timeout, poll_interval=poll_interval, print_interval=print_interval,
                    )
                last_error = None
                break
            except Exception as e:
                last_error = e
                if attempt < attempts:
                    print(f"  Attempt {attempt} failed: {e}")
                else:
                    raise
        if last_error is not None:
            raise last_error
        duration_seconds = round(time.time() - generation_start, 2)
        generation_info = {
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "duration_seconds": duration_seconds,
            "backend": backend,
            "model": model,
            "reasoning_effort": reasoning_effort or None,
            "difficulty_level": level,
            "timeout": timeout,
            "attempts": attempts,
            "iupac": args.iupac,
            "mollangdata_status": mollangdata_status,
        }
        if output_dir is not None:
            (output_dir / "descriptions.txt").write_text(description, encoding="utf-8")
            (output_dir / "generation_info.json").write_text(
                json.dumps(generation_info, indent=2), encoding="utf-8"
            )
            print(f"Description written to {output_dir / 'descriptions.txt'}")
            print(f"Generation info written to {output_dir / 'generation_info.json'}")
        else:
            print()
            print("=== DESCRIPTION ===")
            print(description)
            print()

    if output_dir is not None:
        (output_dir / "prompt.md").write_text(prompt, encoding="utf-8")
        print(f"Prompt written to {output_dir / 'prompt.md'}")
    else:
        print("=== PROMPT ===")
        print(prompt)
        print()


if __name__ == "__main__":
    try:
        main()
    except Exception as e:
        # Keep it demo-friendly: print a readable error and exit 1
        print(str(e))
        raise SystemExit(1)
