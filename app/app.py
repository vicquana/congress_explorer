
"""
Streamlit interface for the full Stanford Congressional Record corpus.

Run:
    uv run streamlit run app/app.py
"""

from __future__ import annotations

from datetime import datetime
from pathlib import Path
import html
import re
import sys

import pandas as pd
import streamlit as st

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from app.search_engine import SearchEngine, SearchFilter

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


# Do not cache SearchEngine: it owns a DuckDB connection, and Streamlit reruns
# (for example pagination) should get a fresh connection.
engine = SearchEngine()

stats = engine.get_corpus_stats()
options = engine.get_filter_options()

if not stats["total_congresses"]:
    st.error(
        "No Congressional Record Parquet corpus was found in `data/processed/`."
    )
    st.stop()

st.title("Congressional Record Explorer")
st.caption(
    f"Congresses {stats['congress_min']}–{stats['congress_max']} · "
    f"{stats['year_min']}–{stats['year_max']} · "
    f"{stats['total_speeches']:,} speeches"
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
"""
    )

with st.form("search_form"):
    query_input = st.text_input(
        "Search Congressional speech",
        value=st.session_state.get("form_query", ""),
        placeholder="Try: religious freedom, freedom of worship, Vatican, communism",
    )

    control_a, control_b = st.columns(2)

    with control_a:
        mode_label = st.selectbox(
            "Search mode",
            list(SEARCH_MODE_LABELS),
            index=0,
        )

    with control_b:
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
    st.session_state.active_search = {
        "query": query_input.strip(),
        "search_mode": SEARCH_MODE_LABELS[mode_label],
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
    limit=PAGE_SIZE,
    offset=offset,
    sort_by=active["sort_by"],
)

with st.spinner("Searching Congressional Record…"):
    results, total, highlight_terms = engine.search(sf)

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
