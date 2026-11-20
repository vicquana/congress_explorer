# Deploying Congress Explorer

The app is a Streamlit page over DuckDB. It reads the Parquet corpus (3.7 GB, 72 files) and keeps no state except a
small cache of search results. So a deployment is: one container, one persistent disk that holds the corpus, one port.

It cannot be a static site. A static host (GitHub Pages and the like) cannot run DuckDB over 3.7 GB of Parquet.
For a small slice of sources with page citations, a static page is possible; that is a different project.

## What is in the repository for this

| File | Purpose |
|---|---|
| `Dockerfile` | Python 3.12 image, non-root user, health check, Streamlit on port 8501. About 650 MB. |
| `requirements-app.txt`, `requirements-app.in` | Pinned run-time dependencies taken from `uv.lock`. They leave out `sentence-transformers`, so the image has no torch or CUDA wheels. |
| `.dockerignore` | Keeps the corpus, tests and derived files out of the build. |
| `.github/workflows/tests.yml` | Runs the tests and builds the image on every push and pull request. |

## Build and run

```bash
docker build -t congress-explorer .
```

Run it with a corpus you already have (read-only mount):

```bash
docker run -d --name congress-explorer -p 8501:8501 \
  -v /path/to/data/processed:/corpus:ro -e CONGRESS_DATA_DIR=/corpus \
  congress-explorer
```

Or let it fetch the pinned Hugging Face dataset (`yeeder/congressional-record-parquet`, about 3.7 GB) into a volume:

```bash
docker volume create congress-data
docker run --rm -v congress-data:/data congress-explorer \
  python -c "from pathlib import Path; from app.data_loader import resolve_processed_dir; print(resolve_processed_dir(Path('/app')))"
docker run -d --name congress-explorer -p 8501:8501 -v congress-data:/data congress-explorer
```

The first command downloads once, so visitors do not wait for it. Without a volume on `/data` the download repeats
every time the container is replaced.

Open http://localhost:8501. `curl http://localhost:8501/_stcore/health` returns `ok` as soon as the server is up; it
does not check that the corpus is present.

## Settings

| Variable | Default in the image | Meaning |
|---|---|---|
| `CONGRESS_DATA_DIR` | unset | Directory holding all 72 `congress_NNN.parquet` files. Overrides the download. |
| `HF_HOME` | `/data/huggingface` | Where the Hugging Face snapshot is stored. |
| `CONGRESS_SEARCH_CACHE_DIR` | `/data/search_cache` | Derived search-result cache. |
| `CONGRESS_SEARCH_CACHE_MAX_MB` | 512 | Cache size cap. |
| `CONGRESS_SEARCH_CACHE_MAX_FILES` | 100 | Cache file-count cap. |
| `CONGRESS_CSV_EXPORT_MAX_ROWS` | 50000 | Rows per CSV export. |

## Sizing, as measured

One user, one phrase search over the whole corpus ("freedom of worship", 647 matching speeches): the container used
44 MiB idle and about 290 MiB at its peak, and the search took 11 s; the same search again took 2 s from the cache.
These are single measurements on a 15 GiB machine with the corpus on local disk, not a load test. Plan for at least
5 GB of disk (corpus 3.7 GB plus cache) and measure with several simultaneous users before relying on a small host.

## Choosing a host

Any host that runs a container with a persistent disk of 5 GB or more fits (a VPS, or a platform volume). Hosts with
an ephemeral disk would download 3.7 GB on every cold start. Nothing in this repository has been tried on a host.

- **Hugging Face Space (Docker).** The official Spaces overview says Docker and Gradio Spaces need a paid plan to
  create and static Spaces are free; third-party guides say otherwise (checked 2026-10-12). Free CPU Spaces have
  2 vCPU, 16 GB RAM and a disk that is not kept across restarts, and sleep after 48 hours idle, so the snapshot would
  be downloaded again after each restart. Confirm what your account can create before planning around it.
- **Streamlit Community Cloud.** `README.md` says the app has so far relied on its auto-detection of `pyproject.toml`,
  and `pyproject.toml` lists `sentence-transformers` (so torch). Its resource limits were not checked against a 3.7 GB corpus.

## Before making it public

- There is no login. Anyone with the URL can search and export up to 50,000 rows. Put it behind your host's access
  control if the corpus use should be limited.
- The container keeps Streamlit's CORS and XSRF protection on and hides the developer toolbar. The dev container
  turns CORS and XSRF off for Codespaces; do not copy those flags.
- The corpus is the Stanford Congressional Record (ODC-BY 1.0). The app already shows the citation; keep it.
- The semantic-search pilot is not in the image's dependencies. It works only where `sentence-transformers` is
  installed and an index exists under `data/embeddings/`.

## What was tested

Tested here: the image builds; the container starts, reports healthy and serves the page; a real search through the
browser over the local corpus mounted read-only returns 647 results; the toolbar is hidden; the workflow file parses
as YAML. Not tested: the Hugging Face download path inside the container, any hosting platform, the GitHub Actions
run itself (it has never run), and behaviour under several users at once.
