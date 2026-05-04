#!/usr/bin/env python3
"""
Script to extract specific columns from SDF files and save to separate TSV files.

This script processes all .sdf.gz files in a given directory and extracts
the following columns:
- SMILES
- PUBCHEM_IUPAC_OPENEYE_NAME
- PUBCHEM_IUPAC_CAS_NAME
- PUBCHEM_IUPAC_NAME_MARKUP
- PUBCHEM_IUPAC_NAME
- PUBCHEM_IUPAC_SYSTEMATIC_NAME
- PUBCHEM_IUPAC_TRADITIONAL_NAME

For each SDF file, a separate TSV file is created in the output folder.

Usage:
    python sdf_to_tsv.py <input_folder> <output_folder> [--demo] [--max-records N]
    
    --demo: Demo mode - only process the first .sdf.gz file found
    --max-records: Maximum number of records to process per file
"""

import os
import sys
import gzip
import argparse
import pandas as pd
from pathlib import Path
from typing import List, Dict, Any
import logging

try:
    from rdkit import Chem
    from rdkit.Chem import rdMolDescriptors
    RDKIT_AVAILABLE = True
except ImportError:
    RDKIT_AVAILABLE = False
    logging.warning("RDKit not available. Canonical SMILES will not be generated.")

# Set up logging
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

# Target columns to extract (output column names)
OUTPUT_COLUMNS = [
    'PUBCHEM_COMPOUND_CID',
    'SMILES',
    'canonical_smiles',
    'PUBCHEM_IUPAC_OPENEYE_NAME',
    'PUBCHEM_IUPAC_CAS_NAME',
    'PUBCHEM_IUPAC_NAME_MARKUP',
    'PUBCHEM_IUPAC_NAME',
    'PUBCHEM_IUPAC_SYSTEMATIC_NAME',
    'PUBCHEM_IUPAC_TRADITIONAL_NAME'
]

# Mapping from SDF property names to output column names
PROPERTY_TO_COLUMN = {
    'PUBCHEM_COMPOUND_CID': 'PUBCHEM_COMPOUND_CID',
    'PUBCHEM_SMILES': 'SMILES',
    'PUBCHEM_IUPAC_OPENEYE_NAME': 'PUBCHEM_IUPAC_OPENEYE_NAME',
    'PUBCHEM_IUPAC_CAS_NAME': 'PUBCHEM_IUPAC_CAS_NAME',
    'PUBCHEM_IUPAC_NAME_MARKUP': 'PUBCHEM_IUPAC_NAME_MARKUP',
    'PUBCHEM_IUPAC_NAME': 'PUBCHEM_IUPAC_NAME',
    'PUBCHEM_IUPAC_SYSTEMATIC_NAME': 'PUBCHEM_IUPAC_SYSTEMATIC_NAME',
    'PUBCHEM_IUPAC_TRADITIONAL_NAME': 'PUBCHEM_IUPAC_TRADITIONAL_NAME'
}

# Properties to extract from SDF files
TARGET_PROPERTIES = list(PROPERTY_TO_COLUMN.keys())


def canonicalize_smiles(smiles: str) -> str:
    """
    Convert SMILES to canonical SMILES using RDKit.
    
    Args:
        smiles: Input SMILES string
        
    Returns:
        Canonical SMILES string, or empty string if conversion fails
    """
    if not RDKIT_AVAILABLE or not smiles or smiles.strip() == '':
        return ''
    
    try:
        mol = Chem.MolFromSmiles(smiles)
        if mol is not None:
            return Chem.MolToSmiles(mol, canonical=True)
        else:
            return ''
    except Exception as e:
        logger.debug(f"Error canonicalizing SMILES '{smiles}': {str(e)}")
        return ''


def parse_sdf_file(file_path: str, max_records: int = None) -> List[Dict[str, Any]]:
    """
    Parse an SDF file and extract the target columns.
    
    SDF format structure:
        - Header block (3 lines)
        - Connection table (MOL block)
        - "M  END" line marks end of structure
        - Properties section: "> <PROPERTY_NAME>" followed by value line(s), empty line separator
        - "$$$$" marks end of record
    
    Args:
        file_path: Path to the SDF file (can be .sdf or .sdf.gz)
        max_records: Maximum number of records to process (None for all)
        
    Returns:
        List of dictionaries containing the extracted data
    """
    records = []
    
    try:
        # Determine if file is gzipped
        if file_path.endswith('.gz'):
            file_handle = gzip.open(file_path, 'rt', encoding='utf-8', errors='ignore')
        else:
            file_handle = open(file_path, 'r', encoding='utf-8', errors='ignore')
        
        with file_handle as f:
            current_record = {}
            in_properties = False
            current_prop_name = None
            current_prop_value = []
            
            for line in f:
                line_stripped = line.rstrip('\n\r')
                
                # Check if we're at the end of a record
                if line_stripped == '$$$$':
                    # Save any pending property value before closing record
                    if current_prop_name and current_prop_name in TARGET_PROPERTIES:
                        output_col_name = PROPERTY_TO_COLUMN[current_prop_name]
                        current_record[output_col_name] = '\n'.join(current_prop_value).strip()
                    
                    if current_record:
                        records.append(current_record)
                        # Check if we've reached the maximum number of records
                        if max_records is not None and len(records) >= max_records:
                            break
                    
                    current_record = {}
                    current_prop_name = None
                    current_prop_value = []
                    in_properties = False
                    continue
                
                # Check if we're entering the properties section (after "M  END")
                if line_stripped == 'M  END':
                    in_properties = True
                    continue
                
                # Extract properties if we're in the properties section
                if in_properties:
                    # Check if this is a property name line
                    if line_stripped.startswith('> <'):
                        # Save previous property if it exists and is in target properties
                        if current_prop_name and current_prop_name in TARGET_PROPERTIES:
                            output_col_name = PROPERTY_TO_COLUMN[current_prop_name]
                            current_record[output_col_name] = '\n'.join(current_prop_value).strip()
                        
                        # Extract property name (between '> <' and '>')
                        prop_name = line_stripped[3:-1]  # Remove '> <' and '>'
                        current_prop_name = prop_name
                        current_prop_value = []
                    elif current_prop_name:
                        # This is a value line for the current property
                        # Empty line indicates end of current property value
                        if line_stripped == '':
                            if current_prop_name and current_prop_name in TARGET_PROPERTIES:
                                output_col_name = PROPERTY_TO_COLUMN[current_prop_name]
                                current_record[output_col_name] = '\n'.join(current_prop_value).strip()
                            current_prop_name = None
                            current_prop_value = []
                        else:
                            # Accumulate value lines (handle multi-line values)
                            current_prop_value.append(line_stripped)
                        
    except Exception as e:
        logger.error(f"Error parsing file {file_path}: {str(e)}")
        import traceback
        logger.debug(traceback.format_exc())
        return []
    
    return records


def process_sdf_files(input_folder: str, output_folder: str, max_records: int = None, demo_mode: bool = False) -> None:
    """
    Process all SDF files in the input folder and save each to a separate TSV file.
    
    Args:
        input_folder: Path to folder containing SDF files
        output_folder: Path to folder where TSV files will be saved
        max_records: Maximum number of records to process per file (None for all)
        demo_mode: If True, only process the first .sdf.gz file found
    """
    input_path = Path(input_folder)
    output_path = Path(output_folder)
    
    if not input_path.exists():
        logger.error(f"Input folder does not exist: {input_folder}")
        return
    
    # Create output folder if it doesn't exist
    output_path.mkdir(parents=True, exist_ok=True)
    
    # Find all .sdf.gz files
    sdf_files = list(input_path.glob("*.sdf.gz"))
    
    if not sdf_files:
        logger.warning(f"No .sdf.gz files found in {input_folder}")
        return
    
    # Sort files for consistent ordering (process first file alphabetically)
    sdf_files.sort()
    total_files = len(sdf_files)
    
    # In demo mode, only process the first file
    if demo_mode:
        logger.info(f"DEMO MODE: Processing only 1 file out of {total_files} found")
        sdf_files = sdf_files[:1]
    else:
        logger.info(f"Found {total_files} SDF files to process")
    
    for sdf_file in sdf_files:
        logger.info(f"Processing: {sdf_file.name}")
        records = parse_sdf_file(str(sdf_file), max_records)
        
        if records:
            # Create output filename by replacing .sdf.gz with .tsv
            output_filename = sdf_file.stem.replace('.sdf', '') + '.tsv'
            output_file_path = output_path / output_filename
            
            # Save records to TSV
            save_to_tsv(records, str(output_file_path))
            logger.info(f"Saved {len(records)} records to {output_file_path}")
        else:
            logger.warning(f"No records extracted from {sdf_file.name}")


def save_to_tsv(records: List[Dict[str, Any]], output_file: str) -> None:
    """
    Save records to TSV file.
    
    Args:
        records: List of dictionaries containing the data
        output_file: Path to output TSV file
    """
    if not records:
        logger.warning("No records to save")
        return
    
    # Create DataFrame
    df = pd.DataFrame(records)
    
    # Generate canonical SMILES if RDKit is available
    if RDKIT_AVAILABLE and 'SMILES' in df.columns:
        logger.info("Generating canonical SMILES...")
        df['canonical_smiles'] = df['SMILES'].apply(canonicalize_smiles)
    else:
        df['canonical_smiles'] = ''
    
    # Ensure all target columns are present (fill missing with empty strings)
    for col in OUTPUT_COLUMNS:
        if col not in df.columns:
            df[col] = ''
    
    # Select only target columns (this excludes any extra columns like PUBCHEM_COMPOUND_CID)
    df = df[OUTPUT_COLUMNS]
    
    # Save to TSV
    df.to_csv(output_file, sep='\t', index=False, na_rep='')
    logger.info(f"Saved {len(records)} records to {output_file}")


def main():
    """Main function to handle command line arguments and process files."""
    parser = argparse.ArgumentParser(
        description="Extract specific columns from SDF files and save to separate TSV files"
    )
    parser.add_argument(
        "input_folder",
        help="Path to folder containing .sdf.gz files"
    )
    parser.add_argument(
        "output_folder",
        help="Path to output folder where TSV files will be saved"
    )
    parser.add_argument(
        "--max-records",
        type=int,
        default=None,
        help="Maximum number of records to process per file (default: all)"
    )
    parser.add_argument(
        "--demo",
        action="store_true",
        help="Demo mode: only process the first .sdf.gz file found (useful for testing)"
    )
    
    args = parser.parse_args()
    
    # Validate input folder
    if not os.path.exists(args.input_folder):
        logger.error(f"Input folder does not exist: {args.input_folder}")
        sys.exit(1)
    
    # Process files
    mode_str = "DEMO MODE" if args.demo else "FULL MODE"
    logger.info(f"Starting SDF processing ({mode_str})...")
    process_sdf_files(args.input_folder, args.output_folder, args.max_records, demo_mode=args.demo)
    logger.info("Processing completed successfully!")


if __name__ == "__main__":
    main()
