#!/bin/bash
# Split a JSONL file and run splits in parallel tmux windows using
# `batch_prompt_generation/run_requests_one_by_one.py`.
#
# - Uses Responses API background mode automatically (handled inside the Python runner).
# - Loads defaults from config/llm_config.json for backend/model/reasoning-effort,
#   but CLI args here override those defaults.
# - For Azure, you can fan out across multiple deployments via --start-idx.
#   For OpenAI, we keep a single model (no per-split deployment suffixing).

set -euo pipefail

# Function to display usage
usage() {
    cat << EOF
Usage: $0 [OPTIONS]

Required arguments:
  -f, --file FILE              Input JSONL file to split
  -s, --session-name NAME      Tmux session name
  -n, --num-splits N           Number of jobs to split into

Optional arguments:
  -o, --output-base DIR         Base directory for output folders (default: same as input file directory)
  -p, --pool-folder DIR        Pool folder to check for existing responses when resuming (default: None)
  --llm-config FILE            Path to llm_config.json (default: repo config/llm_config.json)
  --backend openai|azure       Override backend (default: llm_config.json backend)
  --model NAME                 Override model/deployment (default: llm_config.json <difficulty>.model)
  --reasoning-effort LEVEL     Override reasoning effort (default: llm_config.json <difficulty>.reasoning_effort)
  -i, --start-idx N            Azure only: starting index for deployment suffixes (default: 1; deployment 1 = MODEL, 2+ = MODEL-N)
  -t, --timeout N              Override request timeout in seconds (passed to runner)
  --no-tqdm                    Disable tqdm progress bars in each worker
  -h, --help                   Show this help message

Examples:
  $0 -f jobs.jsonl -s myjobs -n 5
  $0 -f jobs.jsonl -s myjobs -n 10 --backend azure --model gpt-5.2 --reasoning-effort xhigh -i 1
EOF
    exit 1
}

# Default values
OUTPUT_BASE=""
LLM_CONFIG=""
BACKEND=""
MODEL=""
REASONING_EFFORT=""
START_IDX=""
POOL_FOLDER=""
TIMEOUT=""
NO_TQDM=false

# Parse command line arguments
while [[ $# -gt 0 ]]; do
    case $1 in
        -f|--file)
            JSONL_FILE="$2"
            shift 2
            ;;
        --llm-config)
            LLM_CONFIG="$2"
            shift 2
            ;;
        --backend)
            BACKEND="$2"
            shift 2
            ;;
        --model)
            MODEL="$2"
            shift 2
            ;;
        --reasoning-effort)
            REASONING_EFFORT="$2"
            shift 2
            ;;
        -s|--session-name)
            SESSION_NAME="$2"
            shift 2
            ;;
        -n|--num-splits)
            NUM_SPLITS="$2"
            shift 2
            ;;
        -o|--output-base)
            OUTPUT_BASE="$2"
            shift 2
            ;;
        -p|--pool-folder)
            POOL_FOLDER="$2"
            shift 2
            ;;
        -i|--start-idx)
            START_IDX="$2"
            shift 2
            ;;
        -t|--timeout)
            TIMEOUT="$2"
            shift 2
            ;;
        --no-tqdm)
            NO_TQDM=true
            shift
            ;;
        -h|--help)
            usage
            ;;
        *)
            echo "Unknown option: $1"
            usage
            ;;
    esac
done

# Validate required arguments
if [[ -z "${JSONL_FILE:-}" ]] || [[ -z "${SESSION_NAME:-}" ]] || [[ -z "${NUM_SPLITS:-}" ]]; then
    echo "Error: Missing required arguments"
    echo ""
    usage
fi

# Validate JSONL file exists
if [[ ! -f "$JSONL_FILE" ]]; then
    echo "Error: JSONL file does not exist: $JSONL_FILE"
    exit 1
fi

# Validate num_splits is a positive integer
if ! [[ "$NUM_SPLITS" =~ ^[1-9][0-9]*$ ]]; then
    echo "Error: num-splits must be a positive integer"
    exit 1
fi

# Validate timeout if provided
if [[ -n "${TIMEOUT:-}" ]] && ! [[ "$TIMEOUT" =~ ^[1-9][0-9]*$ ]]; then
    echo "Error: timeout must be a positive integer"
    exit 1
fi

# Resolve repo root (this script lives in repo/temp/)
SCRIPT_DIR_ABS="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR_ABS}/.." && pwd)"

# Defaults
if [[ -z "${LLM_CONFIG:-}" ]]; then
    LLM_CONFIG="${REPO_ROOT}/config/llm_config.json"
fi

if [[ ! -f "$LLM_CONFIG" ]]; then
    echo "Error: llm_config.json not found: $LLM_CONFIG"
    exit 1
fi

# Set output base directory (create if provided and missing)
if [[ -z "$OUTPUT_BASE" ]]; then
    OUTPUT_BASE="$(cd "$(dirname "$JSONL_FILE")" && pwd)"
else
    mkdir -p "$OUTPUT_BASE"
    OUTPUT_BASE="$(cd "$OUTPUT_BASE" && pwd)"
fi

# Get absolute input path
JSONL_FILE_ABS="$(cd "$(dirname "$JSONL_FILE")" && pwd)/$(basename "$JSONL_FILE")"

# Infer difficulty from filename (best-effort) to choose defaults from llm_config.json
_infer_difficulty() {
    local b
    b="$(basename "$1" | tr '[:upper:]' '[:lower:]')"
    if [[ "$b" == *"hard"* ]]; then echo "hard"; return; fi
    if [[ "$b" == *"medium"* ]]; then echo "medium"; return; fi
    echo "easy"
}

DIFFICULTY="$(_infer_difficulty "$JSONL_FILE_ABS")"

# Load defaults from llm_config.json (backend + per-difficulty model/reasoning_effort)
{ read -r CFG_BACKEND; read -r CFG_MODEL; read -r CFG_REASONING_EFFORT; } < <(
python3 - "$LLM_CONFIG" "$DIFFICULTY" <<'PY'
import json, sys
path = sys.argv[1]
diff = sys.argv[2]
cfg = json.load(open(path, "r", encoding="utf-8"))
backend = cfg.get("backend", "") or ""
level = cfg.get(diff) or cfg.get("easy") or {}
model = level.get("model", "") or ""
re = level.get("reasoning_effort", "") or ""
print(backend)
print(model)
print(re)
PY
)

if [[ -z "${BACKEND:-}" ]]; then BACKEND="$CFG_BACKEND"; fi
if [[ -z "${MODEL:-}" ]]; then MODEL="$CFG_MODEL"; fi
if [[ -z "${REASONING_EFFORT:-}" ]]; then REASONING_EFFORT="$CFG_REASONING_EFFORT"; fi

if [[ -z "$BACKEND" ]]; then
    echo "Error: backend not set (pass --backend or set it in $LLM_CONFIG)"
    exit 1
fi
if [[ "$BACKEND" != "openai" && "$BACKEND" != "azure" ]]; then
    echo "Error: invalid backend: $BACKEND (expected openai|azure)"
    exit 1
fi
if [[ -z "$MODEL" ]]; then
    echo "Error: model not set (pass --model or set it in $LLM_CONFIG)"
    exit 1
fi
if [[ -z "$REASONING_EFFORT" ]]; then
    echo "Error: reasoning-effort not set (pass --reasoning-effort or set it in $LLM_CONFIG)"
    exit 1
fi

# Azure only: start-idx (optional; default 1 so deployments are 1, 2, 3... → gpt-5.2, gpt-5.2-2, gpt-5.2-3)
if [[ "$BACKEND" == "azure" ]]; then
    if [[ -z "${START_IDX:-}" ]]; then
        START_IDX="1"
    fi
    if ! [[ "$START_IDX" =~ ^[1-9][0-9]*$ ]]; then
        echo "Error: start-idx must be a positive integer (Azure deployments start from 1)"
        exit 1
    fi
fi

# Create temporary directory for split files
TEMP_DIR="$(dirname "$JSONL_FILE_ABS")/split_${SESSION_NAME}_$$"
mkdir -p "$TEMP_DIR"

# Count requests (non-empty lines) and validate splits
TOTAL_LINES="$(python3 - "$JSONL_FILE_ABS" <<'PY'
import sys
p = sys.argv[1]
with open(p, "r", encoding="utf-8") as f:
    n = sum(1 for line in f if line.strip())
print(n)
PY
)"
if [[ "$TOTAL_LINES" -lt "$NUM_SPLITS" ]]; then
    echo "Error: num-splits ($NUM_SPLITS) is greater than number of non-empty lines ($TOTAL_LINES)"
    exit 1
fi

# Split the JSONL file (no body mutation; model/reasoning-effort are passed via CLI)
echo "Preparing split JSONL files..."
python3 - "$JSONL_FILE_ABS" "$NUM_SPLITS" "$TEMP_DIR" <<'PY'
import sys
from pathlib import Path

input_file = Path(sys.argv[1])
num_splits = int(sys.argv[2])
out_dir = Path(sys.argv[3])
out_dir.mkdir(parents=True, exist_ok=True)

lines = []
with input_file.open("r", encoding="utf-8") as f:
    for line in f:
        s = line.strip()
        if s:
            lines.append(s)

total = len(lines)
per = total // num_splits
for i in range(num_splits):
    start = i * per
    end = total if i == num_splits - 1 else (i + 1) * per
    chunk = lines[start:end]
    out_path = out_dir / f"split_{i:03d}_{input_file.name}"
    with out_path.open("w", encoding="utf-8") as w:
        for s in chunk:
            w.write(s + "\n")
    print(f"Split {i}: {len(chunk)} lines -> {out_path.name}")
PY
echo ""

# Check if tmux session already exists
if tmux has-session -t "$SESSION_NAME" 2>/dev/null; then
    echo "Warning: Tmux session '$SESSION_NAME' already exists."
    read -p "Do you want to kill it and create a new one? (y/N): " -n 1 -r
    echo
    if [[ $REPLY =~ ^[Yy]$ ]]; then
        tmux kill-session -t "$SESSION_NAME"
    else
        echo "Aborting. Please use a different session name or kill the existing session."
        exit 1
    fi
fi

# Create tmux session with first window
tmux new-session -d -s "$SESSION_NAME" -n "job-0"

# Function to create and run job in tmux
create_tmux_job() {
    local split_idx="$1"
    # Format split_idx with leading zeros (e.g., 000, 001, 002)
    local split_idx_formatted=$(printf "%03d" "$split_idx")
    local split_file="$TEMP_DIR/split_${split_idx_formatted}_$(basename "$JSONL_FILE_ABS")"
    local output_folder="$OUTPUT_BASE/${SESSION_NAME}_job${split_idx}"

    # Create output folder
    mkdir -p "$output_folder"

    # Create new window in existing session (except for first one)
    if [[ $split_idx -gt 0 ]]; then
        tmux new-window -t "$SESSION_NAME" -n "job-${split_idx}"
    fi

    # Per-split model (Azure can suffix deployments; OpenAI keeps a single model)
    local model_for_split="$MODEL"
    if [[ "$BACKEND" == "azure" ]]; then
        local deployment_idx=$((START_IDX + split_idx))
        if [[ $deployment_idx -eq 1 ]]; then
            model_for_split="$MODEL"
        else
            model_for_split="${MODEL}-${deployment_idx}"
        fi
    fi

    # Prepare the command
    local cmd="cd \"$REPO_ROOT\" && python3 \"$REPO_ROOT/batch_prompt_generation/5b_run_requests_one_by_one.py\" \"$split_file\" --output-dir \"$output_folder\" --resume --llm-config \"$LLM_CONFIG\" --backend \"$BACKEND\" --model \"$model_for_split\" --reasoning-effort \"$REASONING_EFFORT\""

    # Add optional arguments if provided
    if [[ -n "${POOL_FOLDER:-}" ]]; then
        cmd="$cmd --pool-folder \"$POOL_FOLDER\""
    fi
    if [[ -n "${TIMEOUT:-}" ]]; then
        cmd="$cmd --timeout \"$TIMEOUT\""
    fi
    if [[ "$NO_TQDM" == true ]]; then
        cmd="$cmd --no-tqdm"
    fi

    # Send command to tmux window
    tmux send-keys -t "${SESSION_NAME}:job-${split_idx}" "$cmd" C-m

    echo "Created tmux window '${SESSION_NAME}:job-${split_idx}' for job $split_idx"
    echo "  Backend: $BACKEND"
    echo "  Model: $model_for_split"
    echo "  Input: $split_file"
    echo "  Output: $output_folder"
    echo ""
}

# Create jobs for each split
echo "Creating tmux windows and starting jobs..."
for ((i=0; i<NUM_SPLITS; i++)); do
    create_tmux_job "$i"
done

# Summary
echo "=========================================="
echo "Summary:"
echo "  Session name: $SESSION_NAME"
echo "  Number of jobs: $NUM_SPLITS"
echo "  Backend: $BACKEND"
echo "  Model: $MODEL"
if [[ "$BACKEND" == "azure" ]]; then
  echo "  Start index (azure): $START_IDX"
fi
echo "  Reasoning effort: $REASONING_EFFORT"
echo "  Runner: batch_prompt_generation/run_requests_one_by_one.py"
echo "  Split files location: $TEMP_DIR"
echo ""
echo "All jobs are running in tmux session '$SESSION_NAME' as separate windows:"
for ((i=0; i<NUM_SPLITS; i++)); do
    echo "  - job-${i}"
done
echo "To attach: tmux attach -t $SESSION_NAME"
echo ""
echo "Output folders:"
for ((i=0; i<NUM_SPLITS; i++)); do
    echo "  - $OUTPUT_BASE/${SESSION_NAME}_job${i}"
done
echo "=========================================="
