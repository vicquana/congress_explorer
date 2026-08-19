# Congress Text Analysis

A research-oriented tool for exploring the Stanford Congressional Record corpus.

The goal of this project is to make large-scale Congressional Record text data usable by humanities researchers who may not program.

The initial use case is supporting research by Dr. Michael Graziano on religion, government, and the ways political institutions define and discuss religion.

This is a **research project first, not a technology demonstration**. Computational methods should be added only when they help answer substantive research questions.

---

## Project Goals

The first version of the project should provide a simple interface for researchers to:

- Search Congressional speeches by keyword or exact phrase
- Filter results by year
- Filter by House or Senate
- Filter by political party
- View speaker metadata
- Read the surrounding speech text
- Open the full speech
- Export search results as CSV

Possible future methods include:

- phrase and n-gram analysis
- TF-IDF
- semantic search
- text embeddings
- clustering
- document classification
- historical comparison across periods

These methods should only be added when they solve an actual research problem.

---

## Research Approach

The intended workflow is:

```text
Congressional Record
        ↓
basic search and filtering
        ↓
identify historically relevant material
        ↓
researcher close reading
        ↓
identify retrieval or comparison problems
        ↓
add computational methods when useful
```

The project should remain **human-in-the-loop**.

Models and algorithms should help reduce a very large corpus into material worth reading. Historical interpretation remains the responsibility of the researcher.

---

## Data Source

The initial corpus comes from the Stanford Congressional Record dataset.

The two main archives currently used are:

```text
hein-bound.zip
hein-daily.zip
```

### Coverage

`hein-bound` contains Congressional Record data for Congresses 43–111.

`hein-daily` contains data for Congresses 97–114.

Because the datasets overlap, the current plan for a canonical corpus is:

```text
Congress 043–111 → hein-bound
Congress 112–114 → hein-daily
```

This avoids duplicate records from the overlapping period.

---

## Important Data Handling Rule

**Do not fully extract the Stanford ZIP archives.**

The archives are very large and local disk space is limited.

The processing pipeline should read files directly from the ZIP archives using Python's `zipfile` module.

Files should be processed one Congress at a time.

Example:

```text
hein-bound.zip
      ↓
open one Congress inside ZIP
      ↓
read required files
      ↓
join speech + metadata
      ↓
write compressed Parquet
      ↓
release memory
      ↓
process next Congress
```

Do not create a complete uncompressed copy of the Stanford dataset.

---

## Stanford Files

For each Congress, the important files appear to be:

```text
speeches_043.txt
descr_043.txt
043_SpeakerMap.txt
```

with equivalent files for subsequent Congresses.

### `speeches_*.txt`

Expected to contain:

- `speech_id`
- speech text

The exact schema still needs to be confirmed.

### `descr_*.txt`

Expected to contain descriptive metadata associated with speeches.

The exact schema still needs to be confirmed.

### `*_SpeakerMap.txt`

Confirmed format:

```text
speakerid|speech_id|lastname|firstname|chamber|state|gender|party|district|nonvoting
```

Example:

```text
43044451|430000002|HAMLIN|HANNIBAL|S|ME|M|R||voting
```

`speech_id` will likely be the main key used to join the Stanford files.

---

## Files Not Needed for the Initial Application

The Stanford archives also contain derived data such as:

```text
byspeaker_2gram_*.txt
byparty_2gram_*.txt
```

These files are very large and are **not required for the initial search application**.

Do not extract or process them unless a later research question specifically requires them.

Other Stanford auxiliary datasets may be retained for future research, including:

- phrase clusters
- phrase partisanship
- vocabulary
- parsing audits
- speaker mapping statistics
- party metadata

They are not currently part of the core application pipeline.

---

## Local Project Structure

Current intended structure:

```text
congress_text_analysis/
│
├── raw/
│   ├── hein-bound.zip
│   ├── hein-daily.zip
│   └── other Stanford archives
│
├── processed/
│   └── generated Parquet files
│
├── scripts/
│   ├── inspect_zip.py
│   └── future processing scripts
│
├── app/
│   └── future Streamlit application
│
├── pyproject.toml
├── uv.lock
├── .python-version
└── README.md
```

The raw ZIP files should remain unchanged.

Generated or transformed data should go into `processed/`.

---

## Python Environment

The project uses:

- Python 3.12
- `uv` for Python and dependency management
- VS Code as the primary development environment

Python version:

```text
3.12
```

Run Python scripts with:

```bash
uv run python scripts/script_name.py
```

Avoid relying on manually activated virtual environments when possible.

---

## Current Dependencies

At the initial inspection stage, only the Python standard library is required.

`zipfile` is used to read Stanford files directly inside ZIP archives.

Future expected dependencies include:

```bash
uv add duckdb pyarrow
```

For the researcher-facing UI:

```bash
uv add streamlit
```

Potential core stack:

```text
Python
uv
zipfile
PyArrow
Parquet
DuckDB
Streamlit
```

Avoid adding unnecessary infrastructure.

In particular, the first version does **not** need:

- React
- a separate API backend
- Docker
- authentication
- a vector database
- cloud infrastructure
- an LLM
- embeddings

---

## Inspecting ZIP Contents

The current inspection script should read files directly from:

```text
raw/hein-bound.zip
```

without extracting the archive.

Example target files:

```text
hein-bound/speeches_043.txt
hein-bound/descr_043.txt
hein-bound/043_SpeakerMap.txt
```

A script can inspect a few lines using:

```python
from pathlib import Path
from zipfile import ZipFile

project_root = Path(__file__).resolve().parent.parent
zip_path = project_root / "raw" / "hein-bound.zip"

targets = [
    "hein-bound/speeches_043.txt",
    "hein-bound/descr_043.txt",
    "hein-bound/043_SpeakerMap.txt",
]

with ZipFile(zip_path) as z:
    for name in targets:
        info = z.getinfo(name)

        print()
        print(name)
        print(f"Uncompressed size: {info.file_size:,} bytes")

        with z.open(name) as f:
            for _ in range(5):
                line = f.readline()
                print(
                    line.decode(
                        "utf-8",
                        errors="replace"
                    ).rstrip()
                )
```

Run with:

```bash
uv run python scripts/inspect_zip.py
```

---

## Planned Processed Data Format

The goal is to create compact Parquet files, probably one per Congress:

```text
processed/
├── congress_043.parquet
├── congress_044.parquet
├── congress_045.parquet
├── ...
└── congress_114.parquet
```

This is preferable to one very large file because individual Congresses can be rebuilt independently.

DuckDB can query all files together:

```sql
SELECT *
FROM read_parquet('processed/congress_*.parquet');
```

The desired final schema is approximately:

```text
speech_id
congress
date
chamber
speaker_id
first_name
last_name
state
party
district
speech_text
source
```

The exact schema should be determined after inspecting the Stanford speech and description files.

---

## Planned Research Interface

The first researcher-facing application will likely use Streamlit.

The UI should remain intentionally simple.

Example:

```text
Congressional Record Explorer

Search:
[ religious freedom                 ]

Years:
[ 1940 ] to [ 1960 ]

Chamber:
[ All ]

Party:
[ All ]

[ Search ]
```

Results should show:

```text
Date
Congress
House / Senate
Speaker
Party
State
Relevant text snippet

[ View full speech ]
```

Researchers should also be able to download filtered results as CSV.

---

## Search Philosophy

The first implementation should use ordinary text search.

Examples:

```text
religion
"religious freedom"
church
conscience
"free exercise"
"church and state"
```

Do not begin with embeddings or topic modeling.

The purpose of initial search is to understand:

- what researchers actually look for
- which searches work
- which searches fail
- what kinds of historical material are difficult to retrieve

Only then should more advanced methods be considered.

---

## Possible Future Semantic Search

A later version may provide two researcher-facing search modes:

```text
Exact words
Similar ideas
```

The implementation of "Similar ideas" might use embeddings or another semantic retrieval technique.

The researcher should not need to understand the underlying machine-learning implementation.

The research question should determine the technology, not the other way around.

---

## Development Principles

When modifying this project:

1. Preserve the raw Stanford archives.
2. Do not extract the full archives.
3. Process data incrementally.
4. Keep memory usage appropriate for a laptop with 16 GB RAM.
5. Minimize temporary disk usage.
6. Prefer compressed Parquet for processed data.
7. Keep scripts reproducible.
8. Keep the researcher-facing interface simple.
9. Avoid unnecessary infrastructure.
10. Do not introduce AI or ML methods unless they clearly improve the research workflow.

---

---

## Current Status

Completed:

- Stanford archives downloaded (`hein-bound.zip` and `hein-daily.zip`)
- Archive contents inspected and ZIP64 offset quirks resolved
- Main Stanford file families confirmed (`speeches_*.txt`, `descr_*.txt`, `*_SpeakerMap.txt`)
- Ingestion pipeline implemented in `scripts/process_corpus.py` (streaming directly from ZIP into compressed Parquet files per Congress with zero temporary uncompressed disk usage)
- DuckDB search & analytics engine built in `app/search_engine.py` (supporting full text, exact phrase, boolean logic, metadata filters, and KWIC snippet highlighting)
- Interactive Streamlit research web interface implemented in `app/app.py`
- Unified command-line interface implemented in `main.py`
- Canonical Congresses 043–114 fully processed and indexed

---

## Quick Start & Usage

### 1. Launch Research Web Application

```bash
uv run streamlit run app/app.py
```
or via main:
```bash
uv run python main.py app
```

### 2. Search Speeches from Command Line

```bash
uv run python main.py search "religious freedom"
uv run python main.py search "church and state" --limit 10
```

### 3. Inspect Raw Archives without Unzipping

```bash
uv run python main.py inspect --congress 43
uv run python main.py inspect --congress 114 --lines 10
```

### 4. Process Congresses into Parquet

Process a single Congress:
```bash
uv run python main.py process --congress 43
```

Process a range:
```bash
uv run python main.py process --start 43 --end 50
```

Process all 72 canonical Congresses (43 to 114):
```bash
uv run python main.py process --all
```

---

## Guiding Question

Before adding a new technical feature, ask:

> What research problem does this solve for the historian?

If there is no clear answer, do not add the feature yet.

