#!/usr/bin/env python3
"""
Run OPSIN + MolLangData parsing for a sampled.tsv folder.

Input:
  - a folder that contains `sampled.tsv`
  - required columns (TSV header):
      PUBCHEM_COMPOUND_CID
      SMILES
      canonical_smiles
      <IUPAC column> (default: PUBCHEM_IUPAC_SYSTEMATIC_NAME, configurable via --iupac-column)

Processing:
  - For each row, call OPSIN Java main:
      uk.ac.cam.ch.wwmm.opsin.ProcessSingleIupacForMolLangData "<IUPAC>"
    which returns JSON containing:
      opsin_smiles, mollangdata_smiles, opsin_xml, mollangdata_xml,
      opsin_status, mollangdata_status, messages, warnings, final_status, final_pass, ...

Outputs (written under the sample folder by default):
  - opsin_xml/         (one XML per sample)
  - mollangdata_xml/   (one XML per sample)
  - parsing_results.tsv
  - prints summary statistics to stdout

Extra rule (dataset check):
  - If Java `final_status` is PASS but RDKit-canonicalized `opsin_smiles` does NOT match
    RDKit-canonicalized `canonical_smiles` from the dataset, mark overall status as FAIL.
"""

from __future__ import annotations

import argparse
import csv
import json
import logging
import re
import subprocess
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)


try:
    from rdkit import Chem  # type: ignore

    RDKIT_AVAILABLE = True
except Exception:
    RDKIT_AVAILABLE = False


BASE_REQUIRED_COLUMNS = [
    "PUBCHEM_COMPOUND_CID",
    "SMILES",
    "canonical_smiles",
]


def _setup_logging(verbose: bool) -> None:
    level = logging.DEBUG if verbose else logging.INFO
    logging.basicConfig(
        level=level,
        format="%(asctime)s - %(levelname)s - %(message)s",
    )


def _default_opsin_jar() -> Optional[Path]:
    """
    Find the latest MolLangData JAR under the repository (repo root relative to this script).
    Looks in: repo_root/opsin/opsin-core/target, then repo_root/jar.
    """
    repo_root = Path(__file__).resolve().parent.parent
    for base in (repo_root / "opsin" / "opsin-core" / "target", repo_root / "jar"):
        if not base.exists():
            continue
        jars = sorted(base.glob("opsin-core-*-mollangdata-*-SNAPSHOT-jar-with-dependencies.jar"))
        if jars:
            return jars[-1]
        jars = sorted(base.glob("opsin-core-*-jar-with-dependencies.jar"))
        if jars:
            return jars[-1]
    return None


def _extract_mollangdata_version(jar_path: Path) -> Optional[str]:
    """
    Extract MolLangData version from JAR filename.
    Pattern: opsin-core-{opsin-version}-mollangdata-{mollangdata-version}-SNAPSHOT-jar-with-dependencies.jar
    Returns: {mollangdata-version} or None if pattern doesn't match
    """
    name = jar_path.name
    # Match: opsin-core-*-mollangdata-*-SNAPSHOT-jar-with-dependencies.jar
    match = re.search(r'opsin-core-.*-mollangdata-([^-]+)-SNAPSHOT-jar-with-dependencies\.jar', name)
    if match:
        return match.group(1)
    return None


def canonicalize_smiles_rdkit(smiles: str) -> str:
    """
    Canonicalize via RDKit. Returns empty string on failure.
    Matches dataset_sampling canonical_smiles generation (Chem.MolToSmiles(..., canonical=True)).
    
    Note: Do NOT normalize SMILES strings here - backslash-N can be valid in SMILES (stereochemistry).
    Only normalize when reading TSV field values (where \\N means missing).
    """
    if not smiles or not smiles.strip():
        return ""
    if not RDKIT_AVAILABLE:
        raise RuntimeError("RDKit is required for SMILES canonicalization but is not available.")
    try:
        mol = Chem.MolFromSmiles(smiles)
        if mol is None:
            return ""
        return Chem.MolToSmiles(mol, canonical=True)
    except Exception:
        return ""


def _extract_json_from_process_output(stdout: str) -> Dict[str, Any]:
    """
    OPSIN sometimes prints extra log lines; JSON is a single object.
    We parse the last line that looks like JSON.
    """
    lines = [ln.strip() for ln in stdout.splitlines() if ln.strip()]
    # Try from the end (JSON is printed last in our Java main)
    for ln in reversed(lines):
        if ln.startswith("{") and ln.endswith("}"):
            return json.loads(ln)
    # Fallback: find first '{' ... last '}' span
    start = stdout.find("{")
    end = stdout.rfind("}")
    if start >= 0 and end > start:
        return json.loads(stdout[start : end + 1])
    raise ValueError("Could not find JSON object in OPSIN output.")


def run_opsin_java(
    *,
    jar_path: Path,
    iupac: str,
    timeout_s: int,
) -> Tuple[Optional[Dict[str, Any]], Optional[str]]:
    """
    Returns (json_dict or None, error_string or None).
    """
    cmd = [
        "java",
        "-cp",
        str(jar_path),
        "uk.ac.cam.ch.wwmm.opsin.ProcessSingleIupacForMolLangData",
        iupac,
    ]
    try:
        p = subprocess.run(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            timeout=timeout_s,
            check=False,
        )
    except subprocess.TimeoutExpired:
        return None, f"timeout_after_{timeout_s}s"
    except Exception as e:
        return None, f"subprocess_error:{type(e).__name__}:{e}"

    if p.returncode != 0:
        # Keep stderr to help debugging, but don't spam: include first 500 chars
        err = (p.stderr or "").strip()
        err = err[:500]
        return None, f"java_returncode_{p.returncode}:{err}"

    try:
        return _extract_json_from_process_output(p.stdout), None
    except Exception as e:
        return None, f"json_parse_error:{type(e).__name__}:{e}"


class OpsinBatchRunner:
    """
    Keep one JVM alive and process IUPACs via stdin/stdout (JSONL).
    Much faster than spawning `java` per row.
    """

    def __init__(self, *, jar_path: Path) -> None:
        self.jar_path = jar_path
        self._p: Optional[subprocess.Popen[str]] = None

    def start(self) -> None:
        if self._p is not None:
            return
        cmd = [
            "java",
            "-cp",
            str(self.jar_path),
            "uk.ac.cam.ch.wwmm.opsin.ProcessIupacBatchForMolLangData",
        ]
        # stderr is noisy (log4j provider warnings); discard it so we don't block
        self._p = subprocess.Popen(
            cmd,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            text=True,
            bufsize=1,  # line-buffered
        )
        assert self._p.stdin is not None
        assert self._p.stdout is not None

    def close(self) -> None:
        if self._p is None:
            return
        try:
            if self._p.stdin:
                self._p.stdin.close()
        except Exception:
            pass
        try:
            self._p.terminate()
        except Exception:
            pass
        self._p = None

    def process_one(self, iupac: str) -> Tuple[Optional[Dict[str, Any]], Optional[str]]:
        """
        Returns (json_dict or None, error_string or None).
        """
        if self._p is None:
            self.start()
        assert self._p is not None
        assert self._p.stdin is not None
        assert self._p.stdout is not None

        try:
            self._p.stdin.write(iupac.replace("\n", " ").strip() + "\n")
            self._p.stdin.flush()
            # Batch mode can still emit non-JSON lines (e.g. logging).
            # If we don't skip them, we desync and all subsequent rows get wrong outputs.
            for _ in range(1000):
                line = self._p.stdout.readline()
                if line == "":
                    return None, "batch_java_eof"
                line = line.strip()
                if not line:
                    continue
                if not (line.startswith("{") and line.endswith("}")):
                    continue
                return json.loads(line), None
            return None, "batch_no_json_line"
        except Exception as e:
            return None, f"batch_error:{type(e).__name__}:{e}"


def _safe_id(cid: str, row_idx: int) -> str:
    cid = (cid or "").strip()
    if cid.isdigit():
        return cid
    return f"row_{row_idx}"


@dataclass
class StatsSet:
    """Statistics for one set of molecules (with or without '.')."""
    total: int = 0
    passed: int = 0
    opsin_problem: int = 0
    mollangdata_problem: int = 0
    opsin_smiles_mismatch_to_provided: int = 0
    empty_iupac: int = 0
    other: int = 0


@dataclass
class Stats:
    total: int = 0
    no_dot: StatsSet = field(default_factory=StatsSet)
    with_dot: StatsSet = field(default_factory=StatsSet)


def main() -> int:
    ap = argparse.ArgumentParser(
        description="Run OPSIN+MolLangData parsing for a folder containing sampled.tsv"
    )
    ap.add_argument("sample_folder", type=str, help="Folder containing sampled.tsv")
    ap.add_argument(
        "--opsin-jar",
        type=str,
        default=None,
        help="Path to opsin-core-*-jar-with-dependencies.jar (auto-detect if omitted)",
    )
    ap.add_argument(
        "--timeout-s",
        type=int,
        default=30,
        help="Timeout per molecule Java call (seconds). Default: 30",
    )
    ap.add_argument(
        "--out-dir",
        type=str,
        default=None,
        help="Output directory (default: <sample_folder>/parsing_out)",
    )
    ap.add_argument("--verbose", action="store_true", help="Verbose logging")
    ap.add_argument(
        "--max-rows",
        type=int,
        default=None,
        help="Optional: stop after processing N data rows (for quick tests).",
    )
    ap.add_argument(
        "--iupac-column",
        type=str,
        default="PUBCHEM_IUPAC_SYSTEMATIC_NAME",
        help="Column name to use for IUPAC names. Default: PUBCHEM_IUPAC_SYSTEMATIC_NAME. "
        "Can also use PUBCHEM_IUPAC_NAME.",
    )
    args = ap.parse_args()

    _setup_logging(args.verbose)

    sample_dir = Path(args.sample_folder)
    sampled_tsv = sample_dir / "sampled.tsv"
    if not sampled_tsv.exists():
        logger.error(f"Missing sampled.tsv: {sampled_tsv}")
        return 2

    jar_path = Path(args.opsin_jar) if args.opsin_jar else _default_opsin_jar()
    if jar_path is None or not Path(jar_path).exists():
        repo_root = Path(__file__).resolve().parent.parent
        logger.error(
            "Could not locate OPSIN MolLangData jar-with-dependencies.\n"
            "Provide --opsin-jar, or build from the OPSIN fork and place JAR in repo jar/ or opsin/opsin-core/target.\n"
            f"  (e.g. clone OPSIN into {repo_root}/opsin and run: mvn -q -pl opsin-core -am -DskipTests package -Pmollangdata)"
        )
        return 2

    # Extract MolLangData version from JAR filename
    mollangdata_version = _extract_mollangdata_version(jar_path)
    if mollangdata_version:
        logger.info(f"Detected MolLangData version: {mollangdata_version}")
    else:
        logger.warning("Could not extract MolLangData version from JAR filename")

    if not RDKIT_AVAILABLE:
        logger.error(
            "RDKit is required for the canonical_smiles matching check, but it's not available in this Python env."
        )
        return 2

    # Include MolLangData version in output directory name if available
    if args.out_dir:
        out_dir = Path(args.out_dir)
    else:
        base_out_dir = sample_dir / "parsing_out"
        if mollangdata_version:
            out_dir = sample_dir / f"parsing_out_mollangdata_{mollangdata_version}"
        else:
            out_dir = base_out_dir
    opsin_xml_dir = out_dir / "opsin_xml"
    mollangdata_xml_dir = out_dir / "mollangdata_xml"
    opsin_xml_dir.mkdir(parents=True, exist_ok=True)
    mollangdata_xml_dir.mkdir(parents=True, exist_ok=True)

    out_tsv = out_dir / "parsing_results.tsv"
    log_file = out_dir / "parsing_statistics.log"

    stats = Stats()
    # Track failed molecules without "." that have mollangdata_problem
    failed_no_dot_mollangdata: List[Dict[str, str]] = []

    # TSV output columns
    out_cols = [
        "PUBCHEM_COMPOUND_CID",
        "SMILES",
        "canonical_smiles",
        "iupac_column_name",
        "iupac_name",
        # from OPSIN JSON
        "opsin_status",
        "mollangdata_status",
        "opsin_message",
        "mollangdata_message",
        "mollangdata_reason_for_failure",
        "opsin_warnings",
        "mollangdata_warnings",
        "opsin_smiles",
        "mollangdata_smiles",
        "opsin_smiles_canonical",
        "mollangdata_smiles_canonical",
        "opsin_mollangdata_smiles_match",
        "final_pass",
        "final_status",
        # dataset check
        "provided_smiles_canonical_rdkit",
        "opsin_smiles_canonical_rdkit",
        "opsin_matches_provided_canonical_smiles",
        "overall_status",
        "overall_reason",
        # SMILES dot notation
        "provided_smiles_contains_dot",
        # bookkeeping
        "xml_opsin_path",
        "xml_mollangdata_path",
        "java_error",
    ]

    logger.info(f"Reading: {sampled_tsv}")
    logger.info(f"Writing outputs under: {out_dir}")
    logger.info(f"Using OPSIN jar: {jar_path}")
    if mollangdata_version:
        logger.info(f"MolLangData version: {mollangdata_version}")
    logger.info(f"Using IUPAC column: {args.iupac_column}")

    batch = OpsinBatchRunner(jar_path=Path(jar_path))
    batch.start()

    with sampled_tsv.open("r", encoding="utf-8", errors="replace", newline="") as fin, out_tsv.open(
        "w", encoding="utf-8", newline=""
    ) as fout:
        reader = csv.DictReader(fin, delimiter="\t")
        if reader.fieldnames is None:
            logger.error("sampled.tsv has no header row.")
            return 2

        required_columns = BASE_REQUIRED_COLUMNS + [args.iupac_column]
        missing = [c for c in required_columns if c not in reader.fieldnames]
        if missing:
            logger.error(f"sampled.tsv missing required columns: {missing}")
            logger.error(f"Found columns: {reader.fieldnames}")
            return 2

        writer = csv.DictWriter(fout, delimiter="\t", fieldnames=out_cols, extrasaction="ignore")
        writer.writeheader()

        for idx, row in enumerate(reader):
            if args.max_rows is not None and idx >= args.max_rows:
                break
            stats.total += 1
            cid = (row.get("PUBCHEM_COMPOUND_CID") or "").strip()
            raw_smiles = (row.get("SMILES") or "").strip()
            canonical_smiles = (row.get("canonical_smiles") or "").strip()
            iupac = (row.get(args.iupac_column) or "").strip()

            rec_id = _safe_id(cid, idx)

            # Check if provided SMILES contains "." (dot notation for disconnected components/salts)
            provided_smiles_contains_dot = "." in canonical_smiles if canonical_smiles else False

            base_out: Dict[str, Any] = {
                "PUBCHEM_COMPOUND_CID": cid,
                "SMILES": raw_smiles,
                "canonical_smiles": canonical_smiles,
                "iupac_column_name": args.iupac_column,
                "iupac_name": iupac,
                "java_error": "",
                "xml_opsin_path": "",
                "xml_mollangdata_path": "",
                "overall_status": "FAIL",
                "overall_reason": "",
                "provided_smiles_canonical_rdkit": "",
                "opsin_smiles_canonical_rdkit": "",
                "opsin_matches_provided_canonical_smiles": "",
                "provided_smiles_contains_dot": "true" if provided_smiles_contains_dot else "false",
            }

            # Stats buckets - separate by whether SMILES contains "."
            stats_set = stats.with_dot if provided_smiles_contains_dot else stats.no_dot
            stats_set.total += 1

            if iupac == "":
                base_out["overall_reason"] = "empty_iupac"
                base_out["java_error"] = "empty_iupac"
                # Fill expected OPSIN fields empty
                for k in out_cols:
                    base_out.setdefault(k, "")
                writer.writerow(base_out)
                stats_set.empty_iupac += 1
                if stats.total % 100 == 0:
                    logger.info(f"Processed {stats.total} rows...")
                continue

            j, err = batch.process_one(iupac)
            if j is None:
                base_out["java_error"] = err or "unknown_java_error"
                base_out["overall_reason"] = base_out["java_error"]
                for k in out_cols:
                    base_out.setdefault(k, "")
                writer.writerow(base_out)
                stats_set.other += 1
                if stats.total % 100 == 0:
                    logger.info(f"Processed {stats.total} rows...")
                continue

            # Write XML files
            opsin_xml = j.get("opsin_xml") or ""
            mollangdata_xml = j.get("mollangdata_xml") or ""
            opsin_xml_path = opsin_xml_dir / f"{rec_id}.xml"
            mollangdata_xml_path = mollangdata_xml_dir / f"{rec_id}.xml"
            try:
                opsin_xml_path.write_text(str(opsin_xml), encoding="utf-8")
                mollangdata_xml_path.write_text(str(mollangdata_xml), encoding="utf-8")
                base_out["xml_opsin_path"] = str(opsin_xml_path)
                base_out["xml_mollangdata_path"] = str(mollangdata_xml_path)
            except Exception as e:
                base_out["java_error"] = f"xml_write_error:{type(e).__name__}:{e}"

            # Copy JSON fields to TSV row
            def _jget(k: str) -> Any:
                v = j.get(k)
                if isinstance(v, (list, dict)):
                    return json.dumps(v, ensure_ascii=False)
                return "" if v is None else v

            base_out.update(
                {
                    "opsin_status": _jget("opsin_status"),
                    "mollangdata_status": _jget("mollangdata_status"),
                    "opsin_message": _jget("opsin_message"),
                    "mollangdata_message": _jget("mollangdata_message"),
                    "mollangdata_reason_for_failure": _jget("mollangdata_reason_for_failure"),
                    "opsin_warnings": _jget("opsin_warnings"),
                    "mollangdata_warnings": _jget("mollangdata_warnings"),
                    "opsin_smiles": _jget("opsin_smiles"),
                    "mollangdata_smiles": _jget("mollangdata_smiles"),
                    "opsin_smiles_canonical": _jget("opsin_smiles_canonical"),
                    "mollangdata_smiles_canonical": _jget("mollangdata_smiles_canonical"),
                    "opsin_mollangdata_smiles_match": _jget("opsin_mollangdata_smiles_match"),
                    "final_pass": _jget("final_pass"),
                    "final_status": _jget("final_status"),
                }
            )

            # Dataset-level final status
            final_status = str(j.get("final_status") or "")
            opsin_status = str(j.get("opsin_status") or "")
            mollangdata_status = str(j.get("mollangdata_status") or "")

            # Default overall decision: if Java PASS => PASS; otherwise FAIL (with reason from Java status)
            overall_status = "PASS" if final_status == "PASS" else "FAIL"
            overall_reason = final_status if final_status else "java_non_pass"

            # Extra rule: if PASS but OPSIN SMILES != provided canonical_smiles (after RDKit canonicalization) => FAIL
            if final_status == "PASS":
                opsin_smiles = str(j.get("opsin_smiles") or "")
                opsin_can = canonicalize_smiles_rdkit(opsin_smiles)
                provided_can = canonicalize_smiles_rdkit(canonical_smiles)
                base_out["opsin_smiles_canonical_rdkit"] = opsin_can
                base_out["provided_smiles_canonical_rdkit"] = provided_can

                # Only enforce mismatch check when the dataset provided canonical_smiles is present/valid.
                if provided_can == "":
                    base_out["opsin_matches_provided_canonical_smiles"] = ""
                else:
                    match = (opsin_can != "" and opsin_can == provided_can)
                    base_out["opsin_matches_provided_canonical_smiles"] = "true" if match else "false"
                    if not match:
                        overall_status = "FAIL"
                        overall_reason = "OPSIN output SMILES not matched to provided canonical_smiles"

            base_out["overall_status"] = overall_status
            base_out["overall_reason"] = overall_reason

            # Stats buckets - separate by whether SMILES contains "."
            # (stats_set was already set and total incremented earlier for early returns)
            if overall_status == "PASS":
                stats_set.passed += 1
            else:
                if overall_reason == "empty_iupac":
                    stats_set.empty_iupac += 1
                elif opsin_status != "SUCCESS":
                    stats_set.opsin_problem += 1
                elif mollangdata_status != "SUCCESS":
                    stats_set.mollangdata_problem += 1
                    # Track failed molecules without "." that have mollangdata_problem
                    if not provided_smiles_contains_dot:
                        failed_no_dot_mollangdata.append({
                            "cid": cid,
                            "iupac_name": iupac,
                            "canonical_smiles": canonical_smiles,
                            "mollangdata_status": str(mollangdata_status),
                            "mollangdata_message": str(j.get("mollangdata_message") or ""),
                            "mollangdata_reason_for_failure": str(j.get("mollangdata_reason_for_failure") or ""),
                            "overall_reason": overall_reason,
                        })
                elif overall_reason == "OPSIN output SMILES not matched to provided canonical_smiles":
                    stats_set.opsin_smiles_mismatch_to_provided += 1
                else:
                    stats_set.other += 1

            writer.writerow(base_out)

            if stats.total % 100 == 0:
                logger.info(f"Processed {stats.total} rows...")

    batch.close()

    # Write statistics to log file
    with log_file.open("w", encoding="utf-8") as f:
        f.write("=== Parsing Statistics ===\n\n")
        f.write(f"total\t{stats.total}\n\n")
        f.write("=== Molecules without '.' ===\n")
        f.write(f"no_dot_total\t{stats.no_dot.total}\n")
        f.write(f"no_dot_passed\t{stats.no_dot.passed}\n")
        f.write(f"no_dot_opsin_problem\t{stats.no_dot.opsin_problem}\n")
        f.write(f"no_dot_opsin_smiles_mismatch_to_provided\t{stats.no_dot.opsin_smiles_mismatch_to_provided}\n")
        f.write(f"no_dot_mollangdata_problem\t{stats.no_dot.mollangdata_problem}\n")
        f.write(f"no_dot_empty_iupac\t{stats.no_dot.empty_iupac}\n")
        f.write(f"no_dot_other\t{stats.no_dot.other}\n\n")
        f.write("=== Molecules with '.' ===\n")
        f.write(f"with_dot_total\t{stats.with_dot.total}\n")
        f.write(f"with_dot_passed\t{stats.with_dot.passed}\n")
        f.write(f"with_dot_opsin_problem\t{stats.with_dot.opsin_problem}\n")
        f.write(f"with_dot_opsin_smiles_mismatch_to_provided\t{stats.with_dot.opsin_smiles_mismatch_to_provided}\n")
        f.write(f"with_dot_mollangdata_problem\t{stats.with_dot.mollangdata_problem}\n")
        f.write(f"with_dot_empty_iupac\t{stats.with_dot.empty_iupac}\n")
        f.write(f"with_dot_other\t{stats.with_dot.other}\n\n")
        f.write(f"outputs_dir\t{out_dir}\n")
        f.write(f"results_tsv\t{out_tsv}\n")
        if mollangdata_version:
            f.write(f"mollangdata_version\t{mollangdata_version}\n")
        f.write(f"opsin_jar\t{jar_path}\n\n")
        
        # List failed molecules without "." that have mollangdata_problem
        f.write("=" * 80 + "\n")
        f.write(f"Failed molecules (no '.', mollangdata_problem): {len(failed_no_dot_mollangdata)}\n")
        f.write("=" * 80 + "\n\n")
        if failed_no_dot_mollangdata:
            for idx, mol in enumerate(failed_no_dot_mollangdata, 1):
                f.write(f"--- Molecule {idx} ---\n")
                f.write(f"CID: {mol['cid']}\n")
                f.write(f"IUPAC Name: {mol['iupac_name']}\n")
                f.write(f"Canonical SMILES: {mol['canonical_smiles']}\n")
                f.write(f"MolLangData Status: {mol['mollangdata_status']}\n")
                f.write(f"MolLangData Message: {mol['mollangdata_message']}\n")
                f.write(f"MolLangData Reason for Failure: {mol['mollangdata_reason_for_failure']}\n")
                f.write(f"Overall Reason: {mol['overall_reason']}\n")
                f.write("\n")
        else:
            f.write("No molecules found matching the criteria.\n")

    # Also print statistics to stdout
    print(f"total\t{stats.total}")
    print()
    print("=== Molecules without '.' ===")
    print(f"no_dot_total\t{stats.no_dot.total}")
    print(f"no_dot_passed\t{stats.no_dot.passed}")
    print(f"no_dot_opsin_problem\t{stats.no_dot.opsin_problem}")
    print(f"no_dot_opsin_smiles_mismatch_to_provided\t{stats.no_dot.opsin_smiles_mismatch_to_provided}")
    print(f"no_dot_mollangdata_problem\t{stats.no_dot.mollangdata_problem}")
    print(f"no_dot_empty_iupac\t{stats.no_dot.empty_iupac}")
    print(f"no_dot_other\t{stats.no_dot.other}")
    print()
    print("=== Molecules with '.' ===")
    print(f"with_dot_total\t{stats.with_dot.total}")
    print(f"with_dot_passed\t{stats.with_dot.passed}")
    print(f"with_dot_opsin_problem\t{stats.with_dot.opsin_problem}")
    print(f"with_dot_opsin_smiles_mismatch_to_provided\t{stats.with_dot.opsin_smiles_mismatch_to_provided}")
    print(f"with_dot_mollangdata_problem\t{stats.with_dot.mollangdata_problem}")
    print(f"with_dot_empty_iupac\t{stats.with_dot.empty_iupac}")
    print(f"with_dot_other\t{stats.with_dot.other}")
    print()
    print(f"outputs_dir\t{out_dir}")
    print(f"results_tsv\t{out_tsv}")
    print(f"statistics_log\t{log_file}")
    if mollangdata_version:
        print(f"mollangdata_version\t{mollangdata_version}")
    print(f"opsin_jar\t{jar_path}")
    print(f"\nFailed molecules (no '.', mollangdata_problem): {len(failed_no_dot_mollangdata)}")
    if failed_no_dot_mollangdata:
        print(f"Details written to: {log_file}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

