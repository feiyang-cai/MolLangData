# MolLangData: A Large-Scale Dataset for Molecular Structure-Language Description

<a id="readme-top"></a>

[![Hugging Face](https://img.shields.io/badge/🤗%20Datasets-AnonymousMolLangData-yellow)](https://huggingface.co/datasets/mollangdata/MolLangData)
[![License: MIT](https://img.shields.io/badge/License-MIT-blue.svg)](LICENSE)

**Anonymous review snapshot.**  
**MolLangData** is a large-scale dataset of molecular structures paired with natural-language descriptions, generated via a rule-regularized method. It supports training and evaluating models for molecular structure-language alignment.

---

## Table of contents

- [OPSIN (IUPAC → XML / SMILES)](#opsin-iupac--xml--smiles)
- [Requirements](#requirements)
- [Quick start: single molecule](#single-molecule-get-prompt-and-description-from-iupac)
- [Dataset generation pipeline](#dataset-generation-pipeline)
- [Dataset on Hugging Face](#dataset-on-hugging-face-structure-and-validation)
- [License](#license)

---

## OPSIN (IUPAC → XML / SMILES)

We use a customized OPSIN fork that adds **complete XML structure metadata** for building prompts:

- **Fork:** anonymous review link to be added later
- The repository is for reference; a **compiled JAR** is provided for the single-molecule workflow below.
- The OPSIN fork README and usage will be linked in the anonymous review version when ready.

<p align="right">(<a href="#readme-top">back to top</a>)</p>

---

## Requirements

- **Python** 3.8+
- **Java** (JRE or JDK on `PATH`)
- **Python dependencies:** `pip install -r requirements.txt` (installs `openai`, `rdkit`, and related packages)

<p align="right">(<a href="#readme-top">back to top</a>)</p>

---

## Single molecule: get prompt and description from IUPAC

The script **`get_prompt_description_from_iupac.py`** takes one IUPAC name, runs OPSIN to obtain XML and SMILES, computes a **difficulty level** (easy / medium / hard), builds the prompt, and optionally calls an LLM to generate a structure description.

**Files involved**

| Path | Purpose |
|------|---------|
| `get_prompt_description_from_iupac.py` | Main script |
| `jar/` | Place the OPSIN JAR here: `jar/opsin-core-*-jar-with-dependencies.jar` |
| `prompts/smiles_iupac_metadata_v13/` | Prompt template and semantic sections (default, bridged/fused/spiro rings) |
| `config/llm_config.json` | LLM backend (Azure/OpenAI), model, reasoning effort per difficulty, timeouts (used only with `--get-description`) |

**Run (from repo root)**

- **Prompt only (no LLM):**

  ```bash
  python3 get_prompt_description_from_iupac.py "3,4-dihydro-2H-1,5-benzodioxepin-7-yl-(2-fluorophenyl)methanone"
  ```

- **With LLM description** (model and reasoning chosen by difficulty; backend from config):

  ```bash
  python3 get_prompt_description_from_iupac.py "3,4-dihydro-2H-1,5-benzodioxepin-7-yl-(2-fluorophenyl)methanone" --get-description
  ```

- **Write output to a folder** (`prompt.md`, `descriptions.txt`, `generation_info.json`):

  ```bash
  python3 get_prompt_description_from_iupac.py "ethane" --get-description -o out/
  ```

<details>
<summary><strong>Options</strong></summary>

| Option | Description |
|--------|-------------|
| `--opsin-jar PATH` | Path to OPSIN JAR (default: `jar/opsin-core-*-jar-with-dependencies.jar`) |
| `--prompts-dir DIR` | Prompt templates directory (default: `prompts/smiles_iupac_metadata_v13`) |
| `-o DIR` / `--output DIR` | Output folder: writes `prompt.md`; with `--get-description` also `descriptions.txt` and `generation_info.json` |
| `--get-description` | Call LLM to generate structure description (uses `config/llm_config.json` unless overridden) |
| `--llm-config FILE` | LLM config JSON (default: `config/llm_config.json`) |
| `--model MODEL` | Override LLM model |
| `--reasoning-effort EFFORT` | Override LLM reasoning effort |

</details>

<details>
<summary><strong>Output</strong></summary>

- **Without `-o`:** Prints IUPAC, OPSIN status, SMILES, difficulty level, and (if `--get-description`) the description and prompt to stdout.
- **With `-o DIR`:** Writes `DIR/prompt.md`; with `--get-description` also writes `DIR/descriptions.txt` and `DIR/generation_info.json` (backend, model, reasoning effort, duration, etc.).

</details>

<p align="right">(<a href="#readme-top">back to top</a>)</p>

---

## Dataset generation pipeline

This section describes how to obtain or regenerate the MolLangData dataset. Pre-sampled and pre-processed artifacts exist, but external hosting links are omitted from this anonymous snapshot. You can also run the full pipeline from raw PubChem data. **Note:** LLM outputs are non-deterministic, so re-running the pipeline will not reproduce the same descriptions.

### Pre-sampled and pre-processed data

Preprocessed sampled TSV files, Step 3 parsing outputs, and Step 4 prompt-job files exist for this workflow, but their external download locations are intentionally omitted in this anonymous review snapshot.

| Resource | Contents |
|----------|----------|
| Pre-sampled molecular data | Sampled TSV files across multiple rounds, with optional intermediate parsing outputs and prompt-job artifacts. |

The published [MolLangData](https://huggingface.co/datasets/mollangdata/MolLangData) dataset on Hugging Face corresponds to **round_0** data.

### Pipeline overview

| Step | Description |
|------|-------------|
| **1** | SDF → TSV — convert PubChem SDF to TSV (`batch_prompt_generation/miscellaneous/1_sdf_to_tsv`) |
| **2** | Sampling — deterministic sampling from TSV into chunks (e.g. 200k per round) (`batch_prompt_generation/miscellaneous/2_sampling_from_pubchecm`) |
| **3** | OPSIN + MolLangData parsing — full XML structure data per `sampled.tsv` |
| **4** | Create batch prompt JSONL — build LLM job files from parsing output |
| **5** | Run LLM jobs — submit to obtain structure descriptions (OpenAI Batch or one-by-one) |

**Steps 1 and 2** can be time-consuming. Scripts: `batch_prompt_generation/miscellaneous/1_sdf_to_tsv` and `batch_prompt_generation/miscellaneous/2_sampling_from_pubchecm`.

---

### Step 3 — OPSIN + MolLangData parsing

Run the customized OPSIN tool on each sampled chunk to obtain full XML structure data.

- **Input:** A folder containing `sampled.tsv`.
- **Output:** Under that folder (or `--out-dir`) as `parsing_out_mollangdata_<version>/`, containing `opsin_xml/`, `mollangdata_xml/`, and `parsing_results.tsv`.

The script auto-detects the OPSIN JAR in the repo (`opsin/opsin-core/target/` or `jar/`); override with `--opsin-jar`.

**Run** (from repo root; `<sample_folder>` is the folder containing `sampled.tsv`):

```bash
python3 batch_prompt_generation/3_run_opsin_mollangdata_on_sampled.py <sample_folder>
```

<details>
<summary><strong>Step 3 — Arguments</strong></summary>

| Argument | Description |
|----------|-------------|
| `sample_folder` | Folder containing `sampled.tsv` (required). TSV must have `PUBCHEM_COMPOUND_CID`, `SMILES`, `canonical_smiles`, and an IUPAC column (see `--iupac-column`). |
| `--opsin-jar PATH` | Path to OPSIN MolLangData JAR. Default: auto-detect under repo `opsin/opsin-core/target/` or `jar/`. |
| `--out-dir DIR` | Output directory. Default: `<sample_folder>/parsing_out`; output is under `parsing_out_mollangdata_<version>/`. |
| `--iupac-column NAME` | IUPAC column name. Default: `PUBCHEM_IUPAC_SYSTEMATIC_NAME`. |
| `--timeout-s N` | Timeout in seconds per molecule (default: 30). |
| `--max-rows N` | Process only first N rows (for testing). |
| `--verbose` | Verbose logging. |

</details>

---

### Step 4 — Create batch prompt JSONL

Build LLM job files from Step 3 output. The script assigns **difficulty** (easy/medium/hard), builds the **prompt** with the same dynamic template as the single-molecule script (including fused/spiro/bridged sections when applicable), and **routes** model and reasoning effort from `config/llm_config.json`. Output is JSONL (and optional per-prompt TXT) for one-by-one or batch LLM runs. Supports **Azure** and **OpenAI**, and both **Chat Completions** and **Responses** APIs.

<details>
<summary><strong>Step 4 — Backend / API recommendations</strong></summary>

- **Azure:** GPT-5.2 does not support batch on Azure as of February 13, 2026. Use **one-by-one** with **Responses API** (`--api-format responses`).
- **OpenAI:** Both Responses and Chat Completions work. We recommend **Chat Completions** (`--api-format chat_completions`) when using OpenAI.

</details>

<br>

**Run** (from repo root). `<input_folder>` must be Step 3 output containing `parsing_results.tsv` (e.g. `parsing_out_mollangdata_0.1.3`).

```bash
python3 batch_prompt_generation/4_create_batch_prompt_jsonl.py <input_folder> <output_folder> [prompts_folder] --api-format responses
```

<details>
<summary><strong>Step 4 — Arguments</strong></summary>

| Argument | Description |
|----------|-------------|
| `input_folder` | Folder containing `parsing_results.tsv` (and `mollangdata_xml/` or `opsin_xml/`). Typically Step 3 output (e.g. `parsing_out_mollangdata_0.1.3`). |
| `output_folder` | Directory for JSONL output (and optional TXT). Files are split by model and reasoning effort. |
| `prompts_folder` | Optional; default: `prompts/smiles_iupac_metadata_v13`. |
| `--llm-config FILE` | LLM config (default: `config/llm_config.json`). Must have `easy`, `medium`, `hard` with `model` and `reasoning_effort`. |
| `--api-format` | `responses` or `chat_completions`. Default: `chat_completions`. Use `responses` for Responses API. |
| `--chat-completions-url URL` | URL for chat-completions (default: `/v1/chat/completions`). |
| `--responses-url URL` | URL for responses (default: `/v1/responses`). |
| `--xml-type` | `mollangdata_xml` or `opsin_xml` (default: `mollangdata_xml`). |
| `--exclude-iupac` | Omit **IUPAC Name:** block from prompts. |
| `--exclude-xml` | Omit **XML Metadata:** block from prompts. |
| `--allow-dots` | Include rows whose SMILES contain a dot (disconnected component). |
| `--sample-size N` | Randomly sample N rows (for testing). |
| `--random-seed N` | Seed for sampling (default: 533). |
| `--custom-prefix STR` | Prefix for compound IDs in `custom_id`. |
| `--no-txt` | Do not write per-prompt `.txt`; JSONL only. |

</details>

---

### Step 5 — Run LLM jobs (structure descriptions)

> ⚠️ **Cost warning:** Step 5 calls the LLM API for every prompt and can incur large costs. Check usage and billing before running. We recommend using the published [MolLangData](https://huggingface.co/datasets/mollangdata/MolLangData) dataset unless you need to regenerate or extend descriptions.

**Two options:**

| Option | Script | Use case |
|--------|--------|----------|
| **5a — OpenAI Batch** | `5a_submit_openai_batch_jobs.py` | Upload JSONL to OpenAI Batch API, wait, and retrieve. **OpenAI only.** Batch pricing is lower than on-demand. Requires `OPENAI_API_KEY`. |
| **5b — One-by-one** | `5b_run_requests_one_by_one.py`, `5b_run_tmux_jobs.sh` | Sequential or parallel (e.g. via tmux). Works with **Azure** and **OpenAI**. |

<details>
<summary><strong>Step 5 — Recommendations</strong></summary>

- **OpenAI:** Prefer **5a (Batch)** when available.
- **Azure:** Use **5b (one-by-one)**.
- **API format:** For 5b, prefer **Responses API**. Generate job JSONL with `--api-format responses` in Step 4 when using 5b.

</details>

<br>

**Run (from repo root)**

- **5a — OpenAI Batch** (submit, wait, retrieve). Replace `<input>` with a Step 4 `.jsonl` file or a directory of `.jsonl` files.

  ```bash
  python3 batch_prompt_generation/5a_submit_openai_batch_jobs.py <input> --output-dir ./batch_results
  ```

- **5b — One-by-one, single process** (Azure, resume with pool). Replace `<input>` with a Step 4 `.jsonl` file or a directory of `.jsonl` files.

  ```bash
  python3 batch_prompt_generation/5b_run_requests_one_by_one.py <input> --backend azure --output-dir ./runs/run1 --resume --pool-folder ./runs/pool
  ```

- **5b — One-by-one, multiple processes** (e.g. tmux; split one JSONL into 4 workers). Replace `<input.jsonl>` with a Step 4 `.jsonl` file:

  ```bash
  bash ./batch_prompt_generation/5b_run_tmux_jobs.sh -f <input.jsonl> -s myrun -n 4 -o ./runs_out
  ```

<details>
<summary><strong>Step 5a — Arguments (OpenAI Batch)</strong></summary>

| Argument | Description |
|----------|-------------|
| `input` | Single `.jsonl` file or directory of `.jsonl` (from Step 4). |
| `--output-dir DIR` | Where to save retrieved output/error files (default: `batch_results`). |
| `--no-wait` | Submit only; do not wait or download (default: wait and retrieve). |
| `--poll-interval N` | Seconds between status polls (default: 60). |
| `--max-requests N` | Max requests per batch file when splitting (default: 50000). |
| `--completion-window DUR` | Batch completion window (default: `24h`). |
| `--dry-run` | List files and endpoints only; no upload or batch creation. |

</details>

<details>
<summary><strong>Step 5b — Arguments</strong></summary>

**One-by-one runner** (`5b_run_requests_one_by_one.py`)

| Argument | Description |
|----------|-------------|
| `input` | **(Required.)** A single `.jsonl` file or a directory of `.jsonl` files from Step 4. Each line is one LLM request. |
| `--output-dir DIR` | **(Required.)** Directory for outputs: `results_<backend>.jsonl`, `stats_<backend>.jsonl`, and `request_outputs/<custom_id>/` per request. |
| `--backend` | `azure` or `openai`. Selects which LLM API to call. |
| `--llm-config FILE` | Path to LLM config JSON (default: `config/llm_config.json`). Used for endpoint and auth. |
| `--resume` | If set, skip requests that already have an entry in `--output-dir` and continue from the rest. Use after an interrupted run. |
| `--pool-folder DIR` | When using Responses API background mode: folder to store in-flight request IDs for polling. Required for `--resume` to work correctly. |
| `--model MODEL` | Override the model from config (e.g. `gpt-5.2`). |
| `--reasoning-effort LEVEL` | Override reasoning effort from config (e.g. `high`, `xhigh`). |
| `--max-requests N` | Process at most N requests (for testing). Omit to process all. |
| `--timeout N` | Request timeout in seconds. |
| `--poll-interval N` | Seconds between polls when using Responses API background mode. |
| `--max-retries N`, `--retry-sleep SEC` | Retry failed requests up to N times, waiting SEC seconds between retries. |
| `--sleep SEC` | Optional delay in seconds between requests (rate limiting). |
| `--no-tqdm` | Disable progress bar. |

**Tmux multi-process** (`5b_run_tmux_jobs.sh`)

Splits one JSONL into N chunks and runs N workers in tmux windows, each calling `5b_run_requests_one_by_one.py` on one chunk.

| Argument | Description |
|----------|-------------|
| `-f FILE` | **(Required.)** Input JSONL file from Step 4. |
| `-s SESSION_NAME` | **(Required.)** Tmux session name for the worker windows. |
| `-n N` | **(Required.)** Number of splits = number of parallel workers (e.g. `4` → 4 tmux windows). |
| `-o OUTPUT_BASE` | Base output directory; each worker gets a subdir (e.g. `./runs_out`). |
| `-p POOL_FOLDER` | Pool folder for Responses API background mode (passed to each worker). |
| `-i START_IDX` | Deployment start index for multi-deployment setups. |
| `--llm-config`, `--backend`, `--model`, `--reasoning-effort` | Passed through to each worker. |
| `-t TIMEOUT` | Request timeout in seconds. |
| `--no-tqdm` | Disable progress bar in workers. |

</details>

<p align="right">(<a href="#readme-top">back to top</a>)</p>

---

## Dataset on Hugging Face: structure and validation

The [MolLangData dataset on Hugging Face](https://huggingface.co/datasets/mollangdata/MolLangData) is provided in two configurations with the following structure and validation results.

### Dataset structure

1. **validated_data**
   - All validated data (2k samples), including descriptions that passed validation and those that did not.
   - See validation precision in the [Dataset statistics](#dataset-statistics) table below.

2. **generated_data**
   - All generated data from **round 0**, excluding the validated subset.
   - These samples are not validated.

### Dataset statistics

| Difficulty | Model   | Reasoning effort | Generated samples | Validated samples | Validation precision |
|------------|---------|------------------|-------------------|-------------------|----------------------|
| Easy       | GPT-5.2 | high             | 105,085 (65.2%)   | 1,317 (65.8%)     | 1,300 (98.7%)        |
| Medium     | GPT-5.2 | xhigh            | 40,916 (25.4%)    | 496 (24.8%)       | 492 (99.2%)          |
| Hard       | GPT-5.2 | xhigh            | 15,110 (9.4%)     | 187 (9.4%)        | 180 (98.3%)          |
| **Overall**| —       | —                | **161,111**       | **2,000**         | **1,972 (98.6%)**    |

<p align="right">(<a href="#readme-top">back to top</a>)</p>

---

## License

This project is licensed under the [MIT License](LICENSE).

<p align="right">(<a href="#readme-top">back to top</a>)</p>
