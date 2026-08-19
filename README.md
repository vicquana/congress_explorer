# Congress Explorer

A research-oriented tool for exploring the Stanford Congressional Record corpus.

The project is designed primarily for humanities researchers who need to search, inspect, and compare Congressional speech without writing Python or working directly with large text archives.

The initial research use case is supporting Dr. Michael Graziano's work on religion, government, national security, and the ways U.S. political institutions identify, classify, and discuss religion.

This is a **research project first, not a technology demonstration**.

Computational methods should be added only when they help answer substantive historical questions or improve the researcher's ability to discover relevant primary sources.

---

## Current Research Scope

The project originally considered the full Stanford Congressional Record corpus from Congress 43 through Congress 114.

For the first research phase, the scope has been narrowed substantially.

### Phase 1 Corpus

The current target is:

```text
Congress 077–096
approximately 1941–1981
```

This period covers the historical context most directly relevant to the first research use case, including:

- World War II
- the OSS period
- creation and early history of the CIA
- the early Cold War
- the Korean War
- the Vietnam era
- late Cold War developments
- the period surrounding the 1979 Iranian Revolution

For this Phase 1 corpus, **all Congressional speeches should be retained**, not only speeches containing known religion-related keywords.

This is important because historically relevant discussions may use language such as:

```text
freedom of conscience
freedom of worship
spiritual values
sectarian
Catholic
Protestant
Jewish
Muslim
missionary
chaplain
atheistic communism
Christian civilization
```

without explicitly using the words `religion` or `religious`.

Keyword filtering may later be used to construct broader candidate corpora outside the Phase 1 period, but it should not replace the complete 077–096 research corpus.

---

## Data Source

The source data comes from the Stanford Congressional Record dataset.

The original Stanford archives include:

```text
hein-bound.zip
hein-daily.zip
```

Their coverage overlaps.

For the current Phase 1 research period, only:

```text
hein-bound.zip
```

is required.

`hein-daily.zip` is not required for Congresses 077–096 and is therefore outside the current Phase 1 workflow.

The project should not require both archives simply for the sake of maintaining complete historical coverage.

---

## Stanford Files Used

For each Congress, the core Stanford files are:

```text
speeches_XXX.txt
descr_XXX.txt
XXX_SpeakerMap.txt
```

For example:

```text
speeches_077.txt
descr_077.txt
077_SpeakerMap.txt
```

### `speeches_*.txt`

Contains the speech identifier and full speech text.

### `descr_*.txt`

Contains speech-level descriptive metadata, including information such as:

- speech ID
- chamber
- date
- raw speaker label
- speaker name fields
- state
- gender
- character count
- word count

### `*_SpeakerMap.txt`

Contains normalized speaker metadata.

Observed schema:

```text
speakerid|speech_id|lastname|firstname|chamber|state|gender|party|district|nonvoting
```

Example:

```text
43044451|430000002|HAMLIN|HANNIBAL|S|ME|M|R||voting
```

`speech_id` is used to connect speech text with descriptive and speaker metadata.

---

## Stanford Files Not Used

The Stanford archives contain large derived datasets such as:

```text
byspeaker_2gram_*.txt
byparty_2gram_*.txt
```

These files are not required for the Phase 1 application and should not be extracted or processed.

Other Stanford auxiliary datasets may be retained when useful, including:

```text
phrase_clusters/
party_full/
```

The existing Stanford religion phrase cluster may later provide one useful baseline for keyword-based retrieval.

It should not be treated as a complete definition of religion-related Congressional discourse.

---

## Data Strategy

Large Stanford ZIP archives should **not be fully extracted to disk**.

Local development is performed on a laptop with limited disk space and approximately 16 GB RAM.

The intended data flow is:

```text
hein-bound.zip
      ↓
read one Congress directly from archive
      ↓
speeches + descriptions + speaker metadata
      ↓
normalize and join records
      ↓
write compressed Parquet
      ↓
release temporary memory
      ↓
next Congress
```

The current Phase 1 target is:

```text
Congress 077
Congress 078
...
Congress 096
```

Each Congress should produce an independent Parquet file.

---

## Processed Corpus

The intended working corpus is:

```text
data/processed/
├── congress_077.parquet
├── congress_078.parquet
├── congress_079.parquet
├── ...
└── congress_096.parquet
```

One Parquet file per Congress is preferable to one large combined file because:

- individual Congresses can be rebuilt independently
- failures are easier to isolate
- validation is easier
- disk usage is easier to inspect
- DuckDB can query multiple Parquet files directly

DuckDB can query the full Phase 1 corpus with a pattern such as:

```sql
SELECT *
FROM read_parquet('data/processed/congress_*.parquet');
```

Parquet output uses Zstandard compression.

---

## Current Parquet Schema

The current processing implementation produces approximately the following fields:

```text
speech_id
congress
date
year
chamber

speaker_id
first_name
last_name
speaker
state
gender
party
district
nonvoting

char_count
word_count

speech_text
source
```

The raw `speaker` label should be retained even when normalized speaker metadata is available.

Labels such as:

```text
The SPEAKER
The PRESIDENT pro tempore
Mr. SMITH
```

may themselves be useful historical information.

---

## Current Project Structure

```text
congress_explorer/
│
├── data/
│   ├── hein-bound.zip
│   ├── phrase_clusters/
│   ├── party_full/
│   └── processed/
│
├── scripts/
│   ├── inspect_zip.py
│   ├── process_corpus.py
│   └── make_core_archive.py
│
├── app/
│   ├── __init__.py
│   ├── search_engine.py
│   └── app.py
│
├── main.py
├── pyproject.toml
├── uv.lock
├── .gitignore
└── README.md
```

### Important

`make_core_archive.py` is a legacy experiment and is not part of the intended Phase 1 pipeline.

It should eventually be removed because the project no longer needs to create another intermediate ZIP archive.

The intended pipeline is:

```text
Stanford ZIP
→ Parquet
```

not:

```text
Stanford ZIP
→ smaller ZIP
→ Parquet
```

---

## Python Environment

The project uses:

- Python >= 3.12
- `uv` for dependency and environment management
- VS Code for local development

Current dependencies declared in `pyproject.toml` include:

```text
duckdb
pyarrow
streamlit
```

Run Python commands through `uv`:

```bash
uv run python scripts/script_name.py
```

Launch Streamlit with:

```bash
uv run streamlit run app/app.py
```

---

## Current Implementation Status

### Confirmed Working

- Stanford archive contents have been inspected.
- The Stanford archive exhibits a 4 GiB ZIP offset issue with normal ZIP readers.
- A custom archive offset workaround has been implemented.
- The three core Stanford file families have been identified.
- Speech metadata and SpeakerMap records can be joined using `speech_id`.
- `scripts/process_corpus.py` exists and can generate Parquet.
- Congress 043 has been used as a prototype.
- Congress 043 produced approximately:
  - 119,302 speech records
  - 26.2 MB compressed Parquet

- A DuckDB search prototype exists in `app/search_engine.py`.
- A Streamlit interface prototype exists in `app/app.py`.

### Not Yet Complete or Verified

The following should **not** currently be considered complete:

- full Congress 043–114 processing
- Congress 077–096 batch processing
- true bounded-memory streaming
- CRC validation of ZIP members
- atomic Parquet output
- per-Congress validation reports
- processed-corpus manifest
- automated tests
- complete cross-Congress schema validation
- production-ready Streamlit interface

The code currently contains prototypes for several of these areas, but they require additional validation and cleanup.

---

## Important Known Technical Issue

The current archive-reading implementation is not yet truly streaming.

The existing implementation effectively performs:

```text
compressed ZIP member
      ↓
read complete compressed member into memory
      ↓
decompress complete member into memory
      ↓
parse records
```

The corpus processor also currently accumulates a full Congress in Python lists before constructing an Arrow table.

This worked for the Congress 043 prototype but does not guarantee bounded memory use for larger Congresses.

The next implementation step is to replace this with actual incremental processing.

The target behavior is:

```text
ZIP member
    ↓
small decompression chunks
    ↓
lines
    ↓
batch of several thousand speeches
    ↓
ParquetWriter
    ↓
clear batch
    ↓
continue
```

Memory use should remain bounded independently of the size of a Congress.

---

## Archive Reader Refactor

The Stanford-specific ZIP handling is currently duplicated in:

```text
scripts/inspect_zip.py
scripts/process_corpus.py
```

The next refactor should introduce:

```text
scripts/archive_reader.py
```

This module should become the single implementation responsible for:

- locating Stanford archives
- handling the 4 GiB offset issue
- opening Stanford ZIP members
- incremental decompression
- reading lines safely
- validating uncompressed byte counts
- validating CRC32 when possible

Other scripts should use this module instead of implementing their own ZIP handling.

---

## Data Processing Requirements

The corpus processor should ultimately satisfy the following requirements.

### Preserve Every Speech

The speech table should be the primary dataset.

Conceptually:

```text
speeches
   LEFT JOIN descriptions
   LEFT JOIN SpeakerMap
```

A speech should not disappear simply because its speaker cannot be mapped.

Missing metadata should remain nullable.

### Incremental Processing

Do not hold an entire Congress speech corpus in memory.

Write Parquet incrementally using batches.

### Atomic Output

Do not write directly to:

```text
congress_077.parquet
```

during processing.

Instead use:

```text
congress_077.parquet.tmp
```

and rename it only after processing and validation succeed.

This prevents partial Parquet files from being mistaken for completed corpus files.

### Batch Failure Reporting

If multiple Congresses are processed and one fails, the program may continue processing the remaining Congresses, but the final command must report the failures clearly and return a non-zero exit status.

Example:

```text
Processed: 18
Failed: 2

Failed Congresses:
084
091
```

---

## Corpus Validation

Every processed Congress should eventually produce validation statistics including:

```text
Congress
source archive
row count
unique speech IDs
duplicate speech IDs
missing speech text
missing dates
missing chamber
missing speaker IDs
description match rate
SpeakerMap match rate
minimum date
maximum date
House speech count
Senate speech count
other / procedural count
Parquet file size
archive integrity status
```

A corpus-level manifest should eventually summarize these results.

Possible location:

```text
data/processed/manifest.parquet
```

or:

```text
data/processed/manifest.csv
```

The manifest should be used for corpus status reporting instead of rescanning all Parquet files whenever possible.

---

## Research Search Interface

A DuckDB-based search prototype currently exists.

The intended researcher-facing interface should remain simple.

Primary search modes should initially be:

```text
Exact phrase
Contains all terms
Contains any term
```

The wording is important.

The current implementation uses substring matching rather than linguistic tokenization, so the UI should not imply stronger word-level semantics than the backend actually provides.

For example, a search for:

```text
sect
```

may also match:

```text
sectarian
section
```

Retrieval behavior should be documented because it can affect historical interpretation.

---

## Search Filters

Useful initial filters include:

- year
- Congress
- chamber
- party
- state
- speaker

The primary research workflow should emphasize finding and reading historical sources rather than presenting a complex analytics dashboard.

---

## KWIC

Search results should display a Key Word In Context preview.

Example:

```text
...the principle of [religious freedom] guaranteed
to every citizen of this Republic must not be...
```

The researcher should be able to expand the complete speech without losing the search context.

All historical text inserted into HTML should be escaped before markup is added so that original Congressional text is not altered or accidentally interpreted as HTML.

---

## Export

Researchers should be able to export useful search results.

The initial export should prioritize:

- metadata
- query
- KWIC snippet
- speech ID
- date
- speaker
- chamber
- party
- state

Full-speech export can be added separately if needed.

Large result exports should not unnecessarily load the entire corpus into application memory.

---

## Analytics

Analytics are secondary to source discovery.

Useful future summaries may include:

```text
matching speeches by year
matching speeches by Congress
matching speeches by chamber
matching speeches by party
```

These should be described accurately.

For example:

```text
matching speeches by year
```

is not the same as:

```text
number of keyword mentions by year
```

One speech containing a phrase twenty times still represents one matching speech unless occurrence counts are explicitly calculated.

Cross-party or historical comparisons should eventually consider normalization by total speech volume or total word count.

---

## Current Research Workflow

The intended Phase 1 workflow is:

```text
Stanford hein-bound archive
        ↓
Congresses 077–096
        ↓
complete speech-level Parquet corpus
        ↓
keyword / phrase search
        ↓
historian close reading
        ↓
identify retrieval failures
        ↓
only then consider additional methods
```

Potential later methods include:

- expanded historical dictionaries
- stemming
- fuzzy search
- full-text indexing
- TF-IDF
- semantic search
- embeddings
- clustering
- document classification

None of these should be added simply because the technology is available.

---

## Future Religion Candidate Corpus

Outside Congresses 077–096, it may eventually be useful to construct a smaller candidate corpus using broad religion-related vocabulary.

Such a dataset should explicitly be called a:

```text
religion candidate corpus
```

and should not be described as containing all Congressional discourse about religion.

Keyword selection introduces retrieval bias and can miss historically relevant material expressed through changing terminology.

The complete Phase 1 corpus should therefore remain unfiltered.

---

## Development Principles

1. Research questions come before technical features.
2. Preserve all speeches within the Phase 1 historical period.
3. Avoid unnecessary intermediate copies of very large datasets.
4. Do not fully extract Stanford archives.
5. Keep memory use appropriate for a 16 GB laptop.
6. Process Congresses independently.
7. Validate outputs before treating them as research data.
8. Record failures rather than silently skipping them.
9. Prefer reproducible compressed Parquet as the working corpus.
10. Keep the researcher-facing interface simple.
11. Do not introduce AI or ML methods until a retrieval problem justifies them.
12. Clearly distinguish prototype behavior from validated research infrastructure.

---

## Immediate Next Steps

### 1. Create a shared archive reader

Create:

```text
scripts/archive_reader.py
```

Move Stanford ZIP-specific archive handling out of:

```text
inspect_zip.py
process_corpus.py
```

The new module should eventually support true incremental decompression and integrity validation.

### 2. Refactor the corpus processor

Modify:

```text
scripts/process_corpus.py
```

to:

- use `archive_reader.py`
- process speeches in bounded batches
- write incrementally with `ParquetWriter`
- use atomic temporary output files
- report processing failures clearly
- focus Phase 1 defaults on Congresses 077–096

### 3. Add validation

Add per-Congress validation and a corpus manifest.

### 4. Test representative Congresses

Before processing all twenty Phase 1 Congresses, test several different parts of the period.

For example:

```text
077
087
096
```

Then expand testing if needed.

### 5. Process the Phase 1 corpus

Only after the processing pipeline is stable:

```bash
uv run python scripts/process_corpus.py --start 77 --end 96
```

### 6. Return to researcher UI

After the corpus is validated, refine:

```text
app/search_engine.py
app/app.py
```

based on actual research use rather than adding additional speculative features.

---

## Guiding Question

Before adding any feature, ask:

> What research problem does this solve for the historian?

If there is no clear answer, do not add the feature yet.
