#!/bin/bash
#SBATCH --job-name=sdf_to_tsv
#SBATCH --output=slurm_jobs/logs/sdf_to_tsv_%j.out
#SBATCH --error=slurm_jobs/logs/sdf_to_tsv_%j.err
#SBATCH --time=24:00:00
#SBATCH --nodes=1
#SBATCH --ntasks=1
#SBATCH --cpus-per-task=128
#SBATCH --mem=1000G
#SBATCH --partition=YOUR_PARTITION

# Set up environment
set -e

# Print job information
echo "=========================================="
echo "Job ID: $SLURM_JOB_ID"
echo "Job Name: $SLURM_JOB_NAME"
echo "Node: $SLURM_NODELIST"
echo "Start Time: $(date)"
echo "=========================================="

# Change to the directory containing this script and the Python script
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"

# Set paths (customize to your environment)
INPUT_DIR="/path/to/pubchem/SDF"
OUTPUT_DIR="/path/to/pubchem/TSV"
SCRIPT_PATH="sdf_to_tsv.py"

# Create output directory if it doesn't exist
mkdir -p "$OUTPUT_DIR"
mkdir -p slurm_jobs/logs

# Print configuration
echo "Configuration:"
echo "  Input directory: $INPUT_DIR"
echo "  Output directory: $OUTPUT_DIR"
echo "  Script: $SCRIPT_PATH"
echo ""

# Check if input directory exists
if [ ! -d "$INPUT_DIR" ]; then
    echo "ERROR: Input directory does not exist: $INPUT_DIR"
    exit 1
fi

# Count SDF files
SDF_COUNT=$(find "$INPUT_DIR" -name "*.sdf.gz" -type f | wc -l)
echo "Found $SDF_COUNT SDF files to process"
echo ""

# Activate conda environment (customize to your environment)
source ~/.bashrc  # or source /path/to/conda/etc/profile.d/conda.sh if needed
conda activate YOUR_CONDA_ENV

# Run the script
echo "Starting SDF to TSV conversion..."
echo ""

python3 "$SCRIPT_PATH" "$INPUT_DIR" "$OUTPUT_DIR"

# Check exit status
if [ $? -eq 0 ]; then
    echo ""
    echo "=========================================="
    echo "Job completed successfully!"
    echo "End Time: $(date)"
    echo "=========================================="
else
    echo ""
    echo "=========================================="
    echo "Job failed with exit code: $?"
    echo "End Time: $(date)"
    echo "=========================================="
    exit 1
fi

