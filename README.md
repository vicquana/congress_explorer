# Congress Explorer

A research-oriented tool for exploring the Stanford Congressional Record corpus.

The project is designed primarily for humanities researchers who need to search, inspect, and compare Congressional speech without writing Python or working directly with large text archives.

The initial research use case is supporting Dr. Michael Graziano's work on religion, government, national security, and the ways U.S. political institutions identify, classify, and discuss religion.

This is a **research project first, not a technology demonstration**.

Computational methods should be added only when they help answer substantive historical questions or improve the researcher's ability to discover relevant primary sources.

---

## Research Scope

The project originally planned a narrower Phase 1 corpus (Congress 077–096, roughly 1941–1981) before expanding to cover the full Stanford Congressional Record corpus.

**The current working corpus covers the complete Stanford range:**

```text
Congress 043–114
approximately 1873–2016
17,782,222 speeches
```

All Congressional speeches are retained, not only speeches containing known religion-related keywords. This matters because historically relevant discussions may use language such as:

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

without explicitly using the words `religion` or `religious`. Keyword filtering can be layered on top of this corpus for scoped studies, but it does not replace the complete unfiltered corpus as the primary research dataset.

---

## Data Source

The source data comes from the Stanford Congressional Record dataset:

> Matthew Gentzkow, Jesse M. Shapiro, and Matt Taddy. *Congressional Record for the 43rd–114th Congresses: Parsed Speeches and Phrase Counts*. Palo Alto, CA: Stanford Libraries [distributor], 2018-01-16. <https://data.stanford.edu/congress_text> (Open Data Commons Attribution License, ODC-BY 1.0).

The original Stanford archives are:

```text
hein-bound.zip   — Congresses 043–111
hein-daily.zip   — Congresses 112–114
```

Both archives are required to cover the full 043–114 range; their coverage does not overlap for the canonical file set this project uses.

### Distribution

Raw Stanford ZIP archives and processed Parquet files are **not committed to this repository** (see `.gitignore`). The processed corpus is instead published as a versioned dataset on Hugging Face Hub:

```text
https://huggingface.co/datasets/yeeder/congressional-record-parquet
```

The application resolves its corpus at startup in this order (`app/data_loader.py`):

1. `CONGRESS_DATA_DIR` environment variable, if set and containing all 72 canonical Parquet files.
2. Local `data/processed/` directory, if it contains all 72 canonical Parquet files.
3. The pinned Hugging Face dataset snapshot (downloaded to the standard HF cache on first run).

This means the app can be cloned and run with no local data at all — it will fetch the pinned dataset revision automatically — while local development can still use a fully local corpus for speed.

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

`speech_id` is used to connect speech text with descriptive and speaker metadata. Every speech is retained even when descriptive or speaker metadata cannot be matched — the join is `speeches LEFT JOIN descr LEFT JOIN SpeakerMap`, and missing metadata fields are left nullable rather than dropping the speech.

---

## Stanford Files Not Used

The Stanford archives contain large derived datasets such as:

```text
byspeaker_2gram_*.txt
byparty_2gram_*.txt
```

These files are not extracted or processed by this project.

Small Stanford auxiliary reference files are retained under `data/`:

```text
data/phrase_clusters/   — keyword and phrase-cluster reference lists
data/party_full/        — party name reference list
```

The existing Stanford religion phrase cluster may provide one useful baseline for keyword-based retrieval. It is not treated as a complete definition of religion-related Congressional discourse.

---

## Data Pipeline

Large Stanford ZIP archives are never fully extracted to disk. `scripts/archive_reader.py` streams each archive member directly:

```text
hein-bound.zip / hein-daily.zip
      ↓
StanfordArchive reads one member's compressed bytes directly from the
raw file handle (working around a documented 4 GiB local-header-offset
bug in the Stanford archives)
      ↓
decompressed in 1 MiB chunks, CRC32 + uncompressed size validated
against the central directory once the member is fully read
      ↓
scripts/process_corpus.py batches records (10,000 speeches per batch)
and writes them incrementally via pyarrow.parquet.ParquetWriter
      ↓
output is written to congress_XXX.parquet.tmp and only renamed to
congress_XXX.parquet after row-count and schema validation succeed
      ↓
next Congress
```

`scripts/archive_reader.py` is the single implementation responsible for locating Stanford archives, handling the 4 GiB offset issue, incremental decompression, and CRC/size validation. Both `scripts/inspect_zip.py` and `scripts/process_corpus.py` use it rather than implementing their own ZIP handling.

### Batch failure reporting

`process_range()` continues past a failed Congress rather than aborting the whole run, then reports failures clearly with a non-zero exit status:

```text
Processed: 70
Failed: 2

Failed Congresses:
084
091
```

### Corpus manifest

`scripts/build_manifest.py` produces `manifest.json`, a corpus-level summary (row counts, byte sizes, year ranges, per-Congress metadata) committed to the repository. It reflects the corpus that is actually published on Hugging Face, and is used for status reporting instead of rescanning every Parquet file.

---

## Processed Corpus

The working corpus is one Parquet file per Congress:

```text
data/processed/
├── congress_043.parquet
├── congress_044.parquet
├── ...
└── congress_114.parquet
```

One Parquet file per Congress is preferable to one large combined file because:

- individual Congresses can be rebuilt independently
- failures are easier to isolate
- validation is easier
- disk usage is easier to inspect
- DuckDB can query multiple Parquet files directly

DuckDB queries the full corpus with a pattern such as:

```sql
SELECT *
FROM read_parquet('data/processed/congress_*.parquet', union_by_name=true);
```

Parquet output uses Zstandard compression.

---

## Parquet Schema

Each processed Congress produces the following fields:

```text
speech_id
congress
date        -- stored as a string (YYYYMMDD); not yet a typed date column
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

The raw `speaker` label is retained even when normalized speaker metadata is available. Labels such as:

```text
The SPEAKER
The PRESIDENT pro tempore
Mr. SMITH
```

may themselves be useful historical information.

---

## Project Structure

```text
congress_explorer/
│
├── data/
│   ├── phrase_clusters/       — committed reference files
│   ├── party_full/            — committed reference file
│   ├── processed/             — gitignored; local Parquet corpus (optional)
│   ├── search_cache/          — gitignored; derived query-result cache
│   └── embeddings/            — gitignored; pilot sentence-embedding indexes
│
├── scripts/
│   ├── archive_reader.py            — single ZIP-reading implementation
│   ├── inspect_zip.py               — CLI preview of raw ZIP contents
│   ├── process_corpus.py            — ZIP → Parquet pipeline
│   ├── build_manifest.py            — builds manifest.json from processed Parquet
│   └── build_sentence_embeddings.py — builds a pilot Congress's semantic search index
│
├── experiment/
│   └── build_search_db.py     — prototype full-text-search backend (not wired
│                                 into the app yet; see Roadmap)
│
├── app/
│   ├── __init__.py
│   ├── data_loader.py         — resolves local vs. Hugging Face corpus
│   ├── search_engine.py       — DuckDB query engine + result caching
│   ├── semantic_search.py     — experimental pilot-Congress semantic search
│   └── app.py                 — Streamlit interface
│
├── .devcontainer/
│   └── devcontainer.json      — Codespaces / VS Code dev container
│
├── main.py                    — CLI entrypoint (app / process / inspect / search)
├── manifest.json               — committed corpus-level summary
├── pyproject.toml
├── uv.lock
├── .gitignore
└── README.md
```

---

## Python Environment

The project uses:

- Python >= 3.12
- `uv` for dependency and environment management

Dependencies declared in `pyproject.toml`:

```text
duckdb
huggingface-hub
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
# or
uv run main.py app
```

### Configuration (environment variables)

```text
CONGRESS_DATA_DIR              — override the processed-Parquet directory
CONGRESS_SEARCH_CACHE_DIR      — override the search-result cache directory
CONGRESS_SEARCH_CACHE_MAX_MB   — cap search-cache disk usage (default 512)
CONGRESS_SEARCH_CACHE_MAX_FILES— cap search-cache file count (default 100)
CONGRESS_CSV_EXPORT_MAX_ROWS   — cap rows per CSV export (default 50,000)
```

### Dev container / Codespaces

`.devcontainer/devcontainer.json` provisions a Python 3.12 container and runs `uv sync` on creation, then launches Streamlit on attach. Use VS Code's "Reopen in Container" or a GitHub Codespace to get a working environment without local setup.

---

## Research Search Interface

The Streamlit app (`app/app.py`) and DuckDB engine (`app/search_engine.py`) provide:

**Search modes:**

```text
Exact phrase
Contains all terms
Contains any term
Near (proximity)
```

The current implementation uses literal, case-insensitive substring matching rather than linguistic tokenization, so the UI does not imply stronger word-level semantics than the backend provides. For example, a search for `sect` may also match `sectarian` and `section`.

**Near (proximity)** matches two terms that occur within a chosen number of words (0–50) of each other, in either order by default. It is implemented as a RE2 regex (`term_a (?:\s+\S+){0,N}\s+term_b`, plus the reversed order when "either order" is checked) rather than linguistic tokenization — word distance is approximated by whitespace-separated tokens, the same untokenized approach used by the other search modes.

An additional `regex` search mode exists in `SearchEngine` (backed by DuckDB's RE2-based `regexp_matches`, which is not vulnerable to catastrophic-backtracking ReDoS) with pattern-length and compile-validation guards. It is not yet exposed in the Streamlit UI — see Roadmap.

**Filters:** year range, Congress, chamber, party, state, speaker name.

**KWIC (Key Word In Context):** results show a highlighted snippet around each match; the full speech can be expanded without losing the search context. All historical text inserted into HTML is escaped before markup is added, so original Congressional text is never altered or misinterpreted as HTML.

**Pagination and caching:** the first request for a given query/filter combination scans the corpus once and writes matching speech IDs to a small derived Parquet cache, keyed by a hash of the filters and a corpus fingerprint (file names, sizes, mtimes). Subsequent pages of the same search reuse that cache instead of rescanning the corpus. The cache is pruned to stay under configurable size/file-count limits.

**CSV export:** search results can be exported as CSV directly from the results page ("Export search results to CSV" → "Prepare CSV export" → "Download CSV"). The export reuses the same cached result set as pagination (no extra corpus scan) and includes the same columns shown in the UI (full speech text and all metadata fields). Exports are capped at `CONGRESS_CSV_EXPORT_MAX_ROWS` rows (default 50,000); when a search has more matches than the cap, the UI shows a truncation warning with the true total rather than silently dropping rows.

---

## Semantic Sentence Search (Experimental Pilot)

**Status: prototype, not validated research infrastructure.** Scoped to a single pilot Congress at a time, not the full corpus — see Development Principle #11 and the Guiding Question.

This is a second, independent search mode from the literal `SearchEngine` above. Instead of substring matching, it ranks individual *sentences* by embedding similarity to a free-text query (a phrase, sentence, or short paragraph), so a researcher can describe a theme in their own words instead of guessing the exact vocabulary a speaker used.

**Building a pilot index:**

```bash
uv run python scripts/build_sentence_embeddings.py --congress 77 --limit 500 --force   # smoke test
uv run python scripts/build_sentence_embeddings.py --congress 77 --force                # full Congress
```

`scripts/build_sentence_embeddings.py`:

1. Splits every speech in the chosen Congress into sentences with `pysbd` (rule-based, no downloaded language model, so segmentation is deterministic and reproducible).
2. Drops sentences shorter than `--min-words` (default 3) to cut down on procedural boilerplate ("Mr. Speaker.").
3. Embeds each sentence with a local `sentence-transformers` model (default `all-MiniLM-L6-v2`, CPU, 384 dimensions, L2-normalized so cosine similarity is a plain dot product).
4. Writes `data/embeddings/pilot_<congress>/` (`sentences.parquet`, `embeddings.npy`, `meta.json`) — a derived, rebuildable artifact, not committed to the repository (see `.gitignore`).

**Querying:** `app/semantic_search.py` (`SemanticSearchEngine`) loads a pilot's `sentences.parquet` + `embeddings.npy`, embeds the query with the same model, and ranks all indexed sentences by cosine similarity (brute-force NumPy dot product — adequate at single-Congress scale; would need an ANN index such as FAISS or DuckDB's VSS extension to scale to the full 043–114 corpus). Results are joined back to the source Parquet for speaker/date/party/state metadata and the full speech text.

**UI:** in the Streamlit app, toggle "🧪 Try experimental semantic sentence search instead" above the main search form. The toggle only lists Congresses that already have a built pilot index; if none exist, it shows the build command above.

**Known limitations:**

- Scoped to whichever Congress(es) have a pilot index built locally — not the full corpus.
- Brute-force similarity search does not scale past roughly one Congress on a laptop CPU; a full-corpus build would need chunked/incremental embedding, an ANN index, and materially more compute/storage.
- The full embedding matrix is loaded into RAM on first query (~2.3 GB for Congress 077's 1.5M sentences), so the first query after the app starts takes roughly 10–20s; later queries in the same running app are sub-second to a few seconds.
- `all-MiniLM-L6-v2` is a small general-purpose sentence embedding model, not fine-tuned for historical Congressional English; ranking quality has not been formally evaluated against this corpus.
- Sentence segmentation and the `min-words` cutoff are heuristics, not manually validated against this corpus's OCR/transcription quirks.

---

## Analytics

Analytics are secondary to source discovery and are not yet implemented. Potentially useful future summaries include matching speeches by year, by Congress, by chamber, and by party — described carefully, since "matching speeches by year" is not the same as "number of keyword mentions by year." Cross-party or historical comparisons should eventually consider normalization by total speech volume or total word count.

---

## Current Implementation Status

### Confirmed working

- Full Congress 043–114 corpus processed and published (Hugging Face dataset `yeeder/congressional-record-parquet`, pinned revision).
- Streaming, bounded-memory archive reader with CRC32 and size validation (`scripts/archive_reader.py`).
- Incremental, batched Parquet writing with atomic (`.tmp` → rename) output and per-Congress schema/row-count validation (`scripts/process_corpus.py`).
- Batch failure reporting with non-zero exit status across a multi-Congress run.
- Corpus manifest (`manifest.json`) built from the processed corpus.
- DuckDB search engine with disk-cached, paginated results (`app/search_engine.py`).
- Streamlit research interface with KWIC snippets, full-speech view, and metadata filters (`app/app.py`).
- CSV export of full search results, capped and with a truncation warning.
- Near (proximity) search mode: two terms within a configurable word gap, RE2-backed, exposed in the UI.
- Regex search mode implemented with pattern-length and compile-validation hardening (RE2-backed, not exposed in the UI yet).
- Local-vs-cloud corpus resolution with no local data required (`app/data_loader.py`).
- Dev container provisioning via `uv sync` (Python 3.12).
- Experimental semantic sentence search prototype (`scripts/build_sentence_embeddings.py`, `app/semantic_search.py`), scoped to a single pilot Congress — see the section above. Not validated research infrastructure.

### Not yet complete or verified

- Automated tests (none currently exist for `archive_reader.py`'s offset correction, `process_corpus.py`'s join logic, `search_engine.py`, or `semantic_search.py`).
- CI (no `.github/workflows/`).
- Containerized / cloud-hosting configuration (no Dockerfile, `fly.toml`, etc. — the app currently relies on Streamlit Community Cloud's own `pyproject.toml` auto-detection, or manual `uv run streamlit run`).
- Regex search mode exposed as a UI option.
- The `date` column is a raw string, not a typed date/timestamp.
- The `experiment/build_search_db.py` full-text-search prototype is not wired into the app.
- Semantic sentence search does not scale past a single pilot Congress (no ANN index, no full-corpus build).

---

## Development Principles

1. Research questions come before technical features.
2. Preserve all speeches across the full 043–114 corpus; do not silently narrow scope via keyword filtering.
3. Avoid unnecessary intermediate copies of very large datasets.
4. Do not fully extract Stanford archives.
5. Keep memory use bounded independently of Congress size.
6. Process Congresses independently.
7. Validate outputs before treating them as research data.
8. Record failures rather than silently skipping them.
9. Prefer reproducible compressed Parquet as the working corpus.
10. Keep the researcher-facing interface simple.
11. Do not introduce AI or ML methods until a retrieval problem justifies them.
12. Clearly distinguish prototype behavior from validated research infrastructure.

---

## Roadmap

Ordered by what most reduces risk for a research tool researchers will actually rely on:

1. **Automated tests** for the archive reader's offset-correction logic and the corpus processor's metadata join, plus a CI workflow that runs them.
2. **Deployment configuration** — a Dockerfile or hosting config beyond relying on Streamlit Community Cloud auto-detection, if the app needs to run somewhere else.
3. **Typed `date` column** instead of a raw `YYYYMMDD` string.
4. **Decide the fate of `experiment/build_search_db.py`** — either finish it into a real full-text-search backend (stemming, tokenized ranking) and wire it into `SearchEngine`, or remove it if the direction changes.
5. **Expose the `regex` search mode** in the Streamlit UI once there's a concrete research need for it.
6. **Scale semantic sentence search beyond a single pilot Congress**, if the pilot proves useful for real research questions: an ANN index (FAISS or DuckDB VSS) instead of brute-force NumPy, chunked/incremental embedding across the full 043–114 corpus, and an evaluation of ranking quality against this corpus rather than assuming a general-purpose sentence model transfers well to historical Congressional English.

None of these should be added simply because the technology is available.

---

## Guiding Question

Before adding any feature, ask:

> What research problem does this solve for the historian?

If there is no clear answer, do not add the feature yet.
