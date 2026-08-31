
"""
Streamlit interface for the full Stanford Congressional Record corpus.

Run:
    uv run streamlit run app/app.py
"""

from __future__ import annotations

from datetime import datetime
from pathlib import Path
import html
import json
import re
import sys

import pandas as pd
import streamlit as st

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from app.search_engine import DEFAULT_CSV_EXPORT_MAX_ROWS, SearchEngine, SearchFilter
from app.semantic_search import SemanticSearchEngine

CSV_EXPORT_MAX_ROWS = DEFAULT_CSV_EXPORT_MAX_ROWS

st.set_page_config(
    page_title="Congressional Record Explorer",
    page_icon="🏛️",
    layout="wide",
)

PAGE_SIZE = 20

SEARCH_MODE_LABELS = {
    "Exact phrase": "exact_phrase",
    "Contains all terms": "all_words",
    "Contains any term": "any_word",
    "Near (proximity)": "near",
}

PARTY_LABELS = {
    "Democrat": "D",
    "Republican": "R",
    "Other / unknown": "Other",
}

CHAMBER_LABELS = {
    "House": "H",
    "Senate": "S",
}


def format_date(value) -> str:
    if value is None:
        return "Unknown date"

    text = str(value).strip()
    if len(text) >= 8 and text[:8].isdigit():
        try:
            dt = datetime.strptime(text[:8], "%Y%m%d")
            return f"{dt.strftime('%B')} {dt.day}, {dt.year}"
        except ValueError:
            pass
    return text or "Unknown date"


def format_congress(congress_num: int) -> str:
    congress_num = int(congress_num)
    start_year = 1789 + (congress_num - 1) * 2
    end_year = start_year + 1

    suffix = "th"
    if congress_num % 100 not in (11, 12, 13):
        if congress_num % 10 == 1:
            suffix = "st"
        elif congress_num % 10 == 2:
            suffix = "nd"
        elif congress_num % 10 == 3:
            suffix = "rd"

    return f"{congress_num}{suffix} Congress ({start_year}–{end_year})"


def display_speaker(row: pd.Series) -> str:
    raw = str(row.get("speaker") or "").strip()
    if raw:
        return raw

    first = str(row.get("first_name") or "").strip()
    last = str(row.get("last_name") or "").strip()
    full = " ".join(part for part in [first, last] if part)
    return full or "Unknown speaker"


def party_display(value) -> str:
    value = str(value or "").strip()
    if value == "D":
        return "Democrat"
    if value == "R":
        return "Republican"
    return value or "Unknown party"


def chamber_display(value) -> str:
    value = str(value or "").upper().strip()
    if value == "H":
        return "House"
    if value == "S":
        return "Senate"
    return value or "Unknown chamber"


def highlight_full_text(text: str, terms: list[str]) -> str:
    """Return HTML-safe full speech text with literal search terms highlighted."""
    if not text:
        return ""

    literal_terms = [term.strip() for term in terms if term and term.strip()]

    if not literal_terms:
        return html.escape(text)

    pattern = re.compile(
        "|".join(
            sorted(
                (re.escape(term) for term in literal_terms),
                key=len,
                reverse=True,
            )
        ),
        flags=re.IGNORECASE,
    )

    rendered: list[str] = []
    cursor = 0

    for match in pattern.finditer(text):
        rendered.append(html.escape(text[cursor:match.start()]))
        rendered.append(
            "<mark class='kw-match'>"
            + html.escape(match.group(0))
            + "</mark>"
        )
        cursor = match.end()

    rendered.append(html.escape(text[cursor:]))
    return "".join(rendered)


def list_available_semantic_pilots() -> list[int]:
    """Congresses that have a pre-built sentence embedding index on disk."""
    base = PROJECT_ROOT / "data" / "embeddings"
    if not base.exists():
        return []

    pilots = []
    for path in sorted(base.glob("pilot_*")):
        match = re.fullmatch(r"pilot_(\d{3})", path.name)
        if match and SemanticSearchEngine.index_exists(path):
            pilots.append(int(match.group(1)))
    return pilots


@st.cache_resource(show_spinner=False)
def get_semantic_engine(congress: int) -> SemanticSearchEngine:
    """Cached across reruns so the model/embeddings load only once."""
    return SemanticSearchEngine(congress=congress)


def render_semantic_search_section() -> None:
    st.subheader("🧪 Experimental: Semantic sentence search (pilot)")
    st.caption(
        "Prototype, not yet validated research infrastructure. Type a "
        "sentence or short paragraph describing what you're looking for. "
        "Results are individual sentences ranked by embedding similarity, "
        "not literal keyword matches, and are scoped to a single pilot "
        "Congress rather than the full corpus."
    )

    pilots = list_available_semantic_pilots()
    if not pilots:
        st.info(
            "No sentence embedding index has been built yet. Build one "
            "with:\n\n"
            "`uv run python scripts/build_sentence_embeddings.py "
            "--congress 77`"
        )
        return

    pilot_congress = st.selectbox(
        "Pilot Congress",
        pilots,
        format_func=format_congress,
        key="semantic_pilot_congress",
    )

    query_text = st.text_area(
        "Describe what you're looking for",
        placeholder=(
            "e.g. the government should not establish or favor one "
            "religion over another"
        ),
        height=100,
        key="semantic_query_text",
    )

    top_k = st.slider(
        "Number of results",
        min_value=5,
        max_value=100,
        value=20,
        key="semantic_top_k",
    )

    if not st.button("Find similar sentences", type="primary"):
        return

    if not query_text.strip():
        st.warning("Enter a sentence or paragraph to search for.")
        return

    engine = get_semantic_engine(pilot_congress)

    with st.spinner("Embedding query and ranking sentences…"):
        try:
            result = engine.search(query_text, top_k=top_k)
        except Exception as exc:
            st.error(f"Semantic search failed: {exc}")
            return

    st.caption(
        f"Model: `{result.model}` · {result.sentence_count:,} indexed "
        f"sentences in {format_congress(result.congress)}"
    )
    st.markdown("---")

    for _, row in result.results.iterrows():
        speaker = display_speaker(row)
        date_label = format_date(row.get("date"))
        party = party_display(row.get("party"))
        chamber = chamber_display(row.get("chamber"))
        state = str(row.get("state") or "").strip()

        metadata = [date_label, chamber, party]
        if state:
            metadata.append(state)

        st.markdown(f"**similarity {row['similarity']:.3f}** · {speaker}")
        st.caption(" · ".join(metadata))
        st.markdown(f"> {row['sentence_text']}")

        full_text = str(row.get("speech_text") or "")
        if full_text:
            with st.expander("Read full speech"):
                highlighted = highlight_full_text(
                    full_text, [row["sentence_text"]]
                )
                st.markdown(
                    f"""
<div style="
    white-space: pre-wrap;
    line-height: 1.65;
    font-family: ui-monospace, SFMono-Regular, Menlo, Monaco, Consolas, monospace;
    padding: 1rem;
    border: 1px solid rgba(128,128,128,0.25);
    border-radius: 0.5rem;
    max-height: 38rem;
    overflow-y: auto;
">
{highlighted}
</div>
""",
                    unsafe_allow_html=True,
                )

        st.markdown("---")


@st.cache_data(show_spinner=False)
def load_corpus_metadata():
    """Cache corpus-wide stats/filter options across Streamlit reruns."""
    metadata_engine = SearchEngine()
    try:
        return (
            metadata_engine.get_corpus_stats(),
            metadata_engine.get_filter_options(),
        )
    finally:
        metadata_engine.close()


with st.spinner("Preparing Congressional Record corpus…"):
    try:
        stats, options = load_corpus_metadata()
        # SearchEngine owns a DuckDB connection. Use a fresh connection per
        # rerun; search-result IDs themselves are cached on disk.
        engine = SearchEngine()
    except Exception as exc:
        st.error("Could not prepare the Congressional Record corpus.")
        st.exception(exc)
        st.stop()

if not stats["total_congresses"]:
    st.error(
        "No Congressional Record Parquet corpus is available locally or from "
        "the configured Hugging Face dataset revision."
    )
    st.stop()

st.title("Congressional Record Explorer")
st.caption(
    f"Congresses {stats['congress_min']}–{stats['congress_max']} · "
    f"{stats['year_min']}–{stats['year_max']} · "
    f"{stats['total_speeches']:,} speeches"
)

st.markdown(
    """
**Data source:** Matthew Gentzkow, Jesse M. Shapiro, and Matt Taddy,
*Congressional Record for the 43rd–114th Congresses: Parsed Speeches and Phrase Counts*
(Stanford Libraries, 2018).

This explorer uses a Parquet-formatted derivative of the Stanford dataset,
hosted on Hugging Face for reproducible research.
"""
)

with st.expander("About the data and citation"):
    st.markdown(
        """
### Original dataset

Matthew Gentzkow, Jesse M. Shapiro, and Matt Taddy.  
*Congressional Record for the 43rd–114th Congresses: Parsed Speeches and Phrase Counts*.  
Palo Alto, CA: Stanford Libraries [distributor], 2018-01-16.

**Original dataset:**  
https://data.stanford.edu/congress_text

**License:** Open Data Commons Attribution License (ODC-BY 1.0)

### Parquet research edition

This application uses a Congress-level Parquet representation of the Stanford
dataset:

**Hugging Face dataset:**  
https://huggingface.co/datasets/yeeder/congressional-record-parquet

The deployed corpus is pinned to revision:

`f3352e5eddac0f4596ba68a0e5bfbcd225449b6c`

### Recommended citation

Please cite the original Stanford dataset in scholarly work. For reproducibility,
also record the Hugging Face dataset repository and revision used by this explorer.

```bibtex
@dataset{gentzkow2018congressionalrecord,
  author    = {Matthew Gentzkow and Jesse M. Shapiro and Matt Taddy},
  title     = {Congressional Record for the 43rd-114th Congresses:
               Parsed Speeches and Phrase Counts},
  publisher = {Stanford Libraries},
  address   = {Palo Alto, CA},
  year      = {2018},
  url       = {https://data.stanford.edu/congress_text}
}
```
"""
    )

with st.expander("About this research corpus"):
    st.markdown(
        """
This explorer searches the available Stanford Congressional Record corpus
across all processed Congresses. It is designed for historical source discovery:
search, inspect context, apply metadata filters, and read the full speech.

The year range and congressional-term filters are derived directly from the
Parquet corpus, rather than being hard-coded to a single research period.

**Search semantics are intentionally transparent.** “Contains all terms” and
“Contains any term” use literal case-insensitive substring matching. They do
not currently perform stemming, semantic search, or linguistic tokenization.

**Near (proximity)** matches two terms that occur within a chosen number of
words of each other. Word distance is approximated by whitespace-separated
tokens (the same untokenized approach used elsewhere in this search), not by
stemmed or linguistically normalized word counts.
"""
    )

semantic_mode = st.toggle(
    "🧪 Try experimental semantic sentence search instead",
    value=False,
    key="semantic_mode_toggle",
    help=(
        "Search by meaning instead of literal keywords. Paste a sentence "
        "or paragraph and get back the most similar sentences, ranked. "
        "Scoped to a single pilot Congress; see the caption below."
    ),
)

if semantic_mode:
    render_semantic_search_section()
    st.stop()

mode_label = st.selectbox(
    "Search mode",
    list(SEARCH_MODE_LABELS),
    index=0,
    key="search_mode_select",
    help=(
        "Near (proximity) matches two terms that appear within a chosen "
        "number of words of each other, instead of requiring an exact "
        "phrase."
    ),
)

with st.form("search_form"):
    near_term_a = ""
    near_term_b = ""
    near_max_gap = 5
    near_any_order = True

    if mode_label == "Near (proximity)":
        query_input = ""

        near_col_a, near_col_b = st.columns(2)
        with near_col_a:
            near_term_a = st.text_input(
                "Term A",
                value=st.session_state.get("form_near_a", ""),
                placeholder="e.g. freedom",
            )
        with near_col_b:
            near_term_b = st.text_input(
                "Term B",
                value=st.session_state.get("form_near_b", ""),
                placeholder="e.g. worship",
            )

        gap_col, order_col = st.columns(2)
        with gap_col:
            near_max_gap = st.slider(
                "Max words between Term A and Term B",
                min_value=0,
                max_value=50,
                value=st.session_state.get("form_near_gap", 5),
            )
        with order_col:
            near_any_order = st.checkbox(
                "Match either order (A…B or B…A)",
                value=st.session_state.get("form_near_order", True),
            )
    else:
        query_input = st.text_input(
            "Search Congressional speech",
            value=st.session_state.get("form_query", ""),
            placeholder="Try: religious freedom, freedom of worship, Vatican, communism",
        )

    sort_label = st.selectbox(
        "Sort",
        ["Oldest first", "Newest first"],
        index=0,
    )

    with st.expander("Filters", expanded=True):
        filter_col1, filter_col2, filter_col3 = st.columns(3)

        with filter_col1:
            years = st.slider(
                "Year range",
                min_value=int(stats["year_min"]),
                max_value=int(stats["year_max"]),
                value=(int(stats["year_min"]), int(stats["year_max"])),
            )

            congresses = st.multiselect(
                "Congressional term",
                options=options["congresses"],
                default=[],
                format_func=format_congress,
                placeholder="All congressional terms",
            )

        with filter_col2:
            chamber_labels = st.multiselect(
                "Chamber",
                options=list(CHAMBER_LABELS),
                default=[],
                placeholder="House and Senate",
            )

            party_labels = st.multiselect(
                "Party",
                options=list(PARTY_LABELS),
                default=[],
                placeholder="All parties",
            )

        with filter_col3:
            clean_states = sorted(
                {
                    state.strip().upper()
                    for state in options["states"]
                    if isinstance(state, str)
                    and len(state.strip()) == 2
                    and state.strip().isalpha()
                }
            )

            states = st.multiselect(
                "State",
                options=clean_states,
                default=[],
                placeholder="All states",
            )

            speaker_query = st.text_input(
                "Speaker",
                placeholder="e.g. Connally, Capper",
            )

    submitted = st.form_submit_button(
        "Search",
        type="primary",
        use_container_width=True,
    )

if submitted:
    st.session_state.form_query = query_input
    st.session_state.form_near_a = near_term_a
    st.session_state.form_near_b = near_term_b
    st.session_state.form_near_gap = near_max_gap
    st.session_state.form_near_order = near_any_order

    if mode_label == "Near (proximity)" and (
        not near_term_a.strip() or not near_term_b.strip()
    ):
        st.session_state.pop("active_search", None)
        st.warning(
            "Enter both Term A and Term B for a proximity search."
        )
    else:
        st.session_state.active_search = {
            "query": query_input.strip(),
            "search_mode": SEARCH_MODE_LABELS[mode_label],
            "near_term_a": near_term_a.strip(),
            "near_term_b": near_term_b.strip(),
            "near_max_gap": int(near_max_gap),
            "near_any_order": bool(near_any_order),
            "year_min": int(years[0]),
            "year_max": int(years[1]),
            "congress_list": [int(c) for c in congresses] or None,
            "chambers": [CHAMBER_LABELS[label] for label in chamber_labels] or None,
            "parties": [PARTY_LABELS[label] for label in party_labels] or None,
            "states": states or None,
            "speaker_query": speaker_query.strip(),
            "sort_by": "date_asc" if sort_label == "Oldest first" else "date_desc",
        }
        st.session_state.page_num = 1

if "active_search" not in st.session_state:
    st.markdown("### Suggested starting points")
    st.markdown(
        """
- **Exact phrase:** `religious freedom`
- **Exact phrase:** `freedom of worship`
- **Exact phrase:** `freedom of conscience`
- **Contains all terms:** `religion communism`
- **Contains any term:** `Vatican Catholic`
- **Exact phrase:** `sectarian`
- **Near (proximity):** `freedom` within 5 words of `worship`
"""
    )
    st.info("Enter a query and press **Search**.")
    st.stop()

if "page_num" not in st.session_state:
    st.session_state.page_num = 1

active = st.session_state.active_search
offset = (st.session_state.page_num - 1) * PAGE_SIZE

sf = SearchFilter(
    query=active["query"],
    search_mode=active["search_mode"],
    year_min=active["year_min"],
    year_max=active["year_max"],
    congress_list=active["congress_list"],
    chambers=active["chambers"],
    parties=active["parties"],
    states=active["states"],
    speaker_query=active["speaker_query"],
    near_term_a=active["near_term_a"],
    near_term_b=active["near_term_b"],
    near_max_gap=active["near_max_gap"],
    near_any_order=active["near_any_order"],
    limit=PAGE_SIZE,
    offset=offset,
    sort_by=active["sort_by"],
)

with st.spinner("Searching Congressional Record…"):
    try:
        results, total, highlight_terms = engine.search(sf)
    except Exception as exc:
        st.error(f"That search could not be completed: {exc}")
        st.stop()

if results.empty and total > 0 and st.session_state.page_num > 1:
    st.session_state.page_num = 1
    st.rerun()

st.markdown("---")

if total == 0:
    st.warning("No matching speeches found.")
    st.stop()

total_pages = max(1, (total + PAGE_SIZE - 1) // PAGE_SIZE)

st.subheader(f"{total:,} matching speeches")
st.caption(
    f"Page {st.session_state.page_num:,} of {total_pages:,} · "
    f"showing up to {PAGE_SIZE} speeches"
)

search_signature = json.dumps(active, sort_keys=True)

with st.expander("Export search results to CSV"):
    st.caption(
        f"Exports all matching speeches, up to {CSV_EXPORT_MAX_ROWS:,} rows, "
        "in the current sort order."
    )
    if st.button("Prepare CSV export"):
        with st.spinner("Preparing CSV export…"):
            try:
                csv_bytes, exported_rows, total_matches = engine.export_csv(sf)
            except Exception as exc:
                st.error(f"Could not prepare the CSV export: {exc}")
            else:
                st.session_state.csv_export = {
                    "bytes": csv_bytes,
                    "exported_rows": exported_rows,
                    "total_matches": total_matches,
                    "search_signature": search_signature,
                }

    export = st.session_state.get("csv_export")
    if export and export["search_signature"] == search_signature:
        if export["exported_rows"] < export["total_matches"]:
            st.warning(
                f"Export capped at the first {export['exported_rows']:,} of "
                f"{export['total_matches']:,} matching speeches for this sort "
                "order. Narrow the filters to export the rest."
            )
        st.download_button(
            "Download CSV",
            data=export["bytes"],
            file_name="congressional_record_search.csv",
            mime="text/csv",
        )

for _, row in results.iterrows():
    speaker = display_speaker(row)
    date_label = format_date(row.get("date"))
    congress = int(row["congress"]) if pd.notna(row["congress"]) else None
    chamber = chamber_display(row.get("chamber"))
    party = party_display(row.get("party"))
    state = str(row.get("state") or "").strip()

    metadata = [
        date_label,
        format_congress(congress) if congress is not None else "Unknown Congress",
        chamber,
        party,
    ]
    if state:
        metadata.append(state)

    st.markdown(f"#### {speaker}")
    st.caption(" · ".join(metadata))

    snippet = engine.extract_snippet(
        str(row.get("speech_text") or ""),
        highlight_terms,
        window_chars=170,
        max_snippets=2,
    )

    st.markdown(
        f"""
<div style="
    line-height:1.6;
    padding:0.8rem 1rem;
    border-left:3px solid rgba(128,128,128,0.35);
    margin-bottom:0.5rem;
">
{snippet}
</div>
""",
        unsafe_allow_html=True,
    )

    with st.expander("Read full speech"):
        full_text = str(row.get("speech_text") or "")
        highlighted_full_text = highlight_full_text(
            full_text,
            highlight_terms,
        )

        st.markdown(
            f"""
<div style="
    white-space: pre-wrap;
    line-height: 1.65;
    font-family: ui-monospace, SFMono-Regular, Menlo, Monaco, Consolas, monospace;
    padding: 1rem;
    border: 1px solid rgba(128,128,128,0.25);
    border-radius: 0.5rem;
    max-height: 38rem;
    overflow-y: auto;
">
{highlighted_full_text}
</div>
""",
            unsafe_allow_html=True,
        )

        detail_cols = st.columns(4)
        details = [
            ("Speech ID", row.get("speech_id")),
            ("Congress", format_congress(congress) if congress else "—"),
            ("Date", date_label),
            ("Speaker", speaker),
            ("Chamber", chamber),
            ("Party", party),
            ("State", state or "—"),
            ("Word count", row.get("word_count")),
        ]

        for index, (label, value) in enumerate(details):
            with detail_cols[index % 4]:
                st.caption(label)
                st.write(value if value not in ("", None) else "—")

    st.markdown("---")

prev_col, center_col, next_col = st.columns([1, 2, 1])

with prev_col:
    if st.button(
        "← Previous",
        disabled=st.session_state.page_num <= 1,
        use_container_width=True,
    ):
        st.session_state.page_num -= 1
        st.rerun()

with center_col:
    st.markdown(
        f"<div style='text-align:center;padding-top:0.5rem;'>"
        f"Page <strong>{st.session_state.page_num:,}</strong> "
        f"of <strong>{total_pages:,}</strong>"
        f"</div>",
        unsafe_allow_html=True,
    )

with next_col:
    if st.button(
        "Next →",
        disabled=st.session_state.page_num >= total_pages,
        use_container_width=True,
    ):
        st.session_state.page_num += 1
        st.rerun()
