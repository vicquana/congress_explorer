"""
Streamlit Web Application for Congressional Record Text Exploration & Analysis.
Designed for humanities and political history researchers.
"""

from datetime import datetime
import io
from pathlib import Path
import re
import sys
import time

import pandas as pd
import streamlit as st

try:
    from app.search_engine import SearchEngine, SearchFilter
except ImportError:
    from search_engine import SearchEngine, SearchFilter


# ---------------------------------------------------------
# Page Configuration & Custom CSS Styling
# ---------------------------------------------------------
st.set_page_config(
    page_title="Congressional Record Explorer",
    page_icon="🏛️",
    layout="wide",
    initial_sidebar_state="expanded",
)

CUSTOM_CSS = """
<style>
/* Modern typography and aesthetics */
@import url('https://fonts.googleapis.com/css2?family=Inter:wght@300;400;500;600;700&family=Newsreader:ital,opsz,wght@0,6..72,400;0,6..72,600;1,6..72,400&display=swap');

html, body, [class*="css"] {
    font-family: 'Inter', -apple-system, BlinkMacSystemFont, sans-serif;
}

/* Header Banner */
.hero-header {
    background: linear-gradient(135deg, #1e293b 0%, #0f172a 100%);
    color: #f8fafc;
    padding: 1.5rem 2rem;
    border-radius: 12px;
    margin-bottom: 1.5rem;
    border: 1px solid rgba(255, 255, 255, 0.1);
    box-shadow: 0 4px 12px rgba(0, 0, 0, 0.15);
}
.hero-title {
    font-size: 1.8rem;
    font-weight: 700;
    margin: 0;
    color: #f1f5f9;
    display: flex;
    align-items: center;
    gap: 0.6rem;
}
.hero-subtitle {
    font-size: 0.95rem;
    color: #94a3b8;
    margin-top: 0.4rem;
    margin-bottom: 0;
    line-height: 1.4;
}

/* Metadata Badges */
.badge {
    display: inline-block;
    padding: 0.2rem 0.55rem;
    border-radius: 6px;
    font-size: 0.75rem;
    font-weight: 600;
    letter-spacing: 0.02em;
    margin-right: 0.4rem;
    margin-bottom: 0.2rem;
}
.badge-chamber {
    background-color: #3b82f6;
    color: white;
}
.badge-party-r {
    background-color: #ef4444;
    color: white;
}
.badge-party-d {
    background-color: #2563eb;
    color: white;
}
.badge-party-other {
    background-color: #8b5cf6;
    color: white;
}
.badge-date {
    background-color: #475569;
    color: #f8fafc;
}
.badge-congress {
    background-color: #0d9488;
    color: white;
}
.badge-speaker {
    background-color: #334155;
    color: #cbd5e1;
}

/* Search Results Card */
.speech-card {
    background: var(--card-bg, #ffffff);
    border: 1px solid rgba(148, 163, 184, 0.25);
    border-radius: 10px;
    padding: 1.25rem;
    margin-bottom: 1rem;
    box-shadow: 0 2px 6px rgba(0, 0, 0, 0.04);
    transition: transform 0.15s ease, box-shadow 0.15s ease;
}
.speech-card:hover {
    border-color: rgba(59, 130, 246, 0.4);
    box-shadow: 0 4px 12px rgba(59, 130, 246, 0.08);
}
.speech-meta {
    display: flex;
    flex-wrap: wrap;
    align-items: center;
    gap: 0.3rem;
    margin-bottom: 0.75rem;
}
.speech-speaker-name {
    font-weight: 600;
    font-size: 1.05rem;
    color: var(--text-color, #1e293b);
}
.snippet-box {
    font-family: 'Newsreader', Georgia, serif;
    font-size: 1.02rem;
    line-height: 1.6;
    color: var(--snippet-color, #334155);
    background-color: var(--snippet-bg, #f8fafc);
    padding: 0.85rem 1rem;
    border-radius: 8px;
    border-left: 3px solid #3b82f6;
    margin: 0.5rem 0;
}
.kw-match {
    background-color: #fde047;
    color: #854d0e;
    font-weight: 600;
    padding: 0.1rem 0.25rem;
    border-radius: 3px;
}
.kw-separator {
    color: #94a3b8;
    padding: 0 0.4rem;
}

/* Full Speech Reader Text */
.full-speech-text {
    font-family: 'Newsreader', Georgia, serif;
    font-size: 1.05rem;
    line-height: 1.7;
    white-space: pre-wrap;
    padding: 1rem;
    background: var(--reader-bg, #f1f5f9);
    border-radius: 8px;
    color: var(--reader-color, #0f172a);
    max-height: 500px;
    overflow-y: auto;
}

/* Stat Box */
.stat-card {
    background: #1e293b;
    color: #f8fafc;
    padding: 1rem;
    border-radius: 10px;
    text-align: center;
    border: 1px solid rgba(255,255,255,0.08);
}
.stat-number {
    font-size: 1.5rem;
    font-weight: 700;
    color: #38bdf8;
}
.stat-label {
    font-size: 0.8rem;
    color: #94a3b8;
    text-transform: uppercase;
    letter-spacing: 0.05em;
}
</style>
"""
st.markdown(CUSTOM_CSS, unsafe_allow_html=True)


# ---------------------------------------------------------
# Search Engine Initialization
# ---------------------------------------------------------
@st.cache_resource
def get_engine() -> SearchEngine:
    return SearchEngine()


engine = get_engine()
corpus_stats = engine.get_corpus_stats()


# ---------------------------------------------------------
# Sidebar: Controls & Filters
# ---------------------------------------------------------
with st.sidebar:
    st.markdown("### 🔍 Search & Filters")

    # Search Query
    query = st.text_input(
        "Search Speeches",
        value="",
        placeholder="e.g. religious freedom, church, conscience",
        help="Enter keywords or exact phrases to search speech text.",
    )

    search_mode = st.selectbox(
        "Search Mode",
        options=["all_words", "exact_phrase", "any_word", "regex"],
        format_func=lambda x: {
            "all_words": "All Words (AND)",
            "exact_phrase": 'Exact Phrase ("...")',
            "any_word": "Any Word (OR)",
            "regex": "Regular Expression",
        }.get(x, x),
        help="Specify how multiple search words should be matched.",
    )

    st.markdown("---")
    st.markdown("#### ⏳ Historical Era & Years")

    # Year Presets
    era_preset = st.selectbox(
        "Quick Era Preset",
        options=[
            "Custom Range",
            "Full Corpus (1873-2016)",
            "19th Century (1873-1899)",
            "Progressive Era & WWI (1900-1929)",
            "New Deal & WWII (1930-1945)",
            "Post-War & Civil Rights (1946-1979)",
            "Modern Era (1980-2016)",
        ],
        index=1 if corpus_stats["total_congresses"] > 0 else 0,
    )

    min_corpus_year = max(1873, corpus_stats["year_min"])
    max_corpus_year = min(2016, corpus_stats["year_max"])

    if era_preset == "19th Century (1873-1899)":
        preset_range = (1873, 1899)
    elif era_preset == "Progressive Era & WWI (1900-1929)":
        preset_range = (1900, 1929)
    elif era_preset == "New Deal & WWII (1930-1945)":
        preset_range = (1930, 1945)
    elif era_preset == "Post-War & Civil Rights (1946-1979)":
        preset_range = (1946, 1979)
    elif era_preset == "Modern Era (1980-2016)":
        preset_range = (1980, 2016)
    elif era_preset == "Full Corpus (1873-2016)":
        preset_range = (min_corpus_year, max_corpus_year)
    else:
        preset_range = (min_corpus_year, max_corpus_year)

    year_range = st.slider(
        "Year Range",
        min_value=min_corpus_year,
        max_value=max_corpus_year,
        value=preset_range,
        step=1,
    )

    st.markdown("---")
    st.markdown("#### 🏛️ Chamber & Political Filters")

    # Chamber Filter
    chamber_choice = st.radio(
        "Chamber",
        options=["All Chambers", "Senate (S)", "House of Reps (H)"],
        index=0,
    )
    chambers_filter = (
        ["S"]
        if chamber_choice == "Senate (S)"
        else (["H"] if chamber_choice == "House of Reps (H)" else None)
    )

    # Party Filter
    col_p1, col_p2 = st.columns(2)
    with col_p1:
        p_rep = st.checkbox("Republican", value=True)
        p_dem = st.checkbox("Democrat", value=True)
    with col_p2:
        p_oth = st.checkbox("Other / Indep.", value=True)

    parties_filter = []
    if p_rep:
        parties_filter.append("R")
    if p_dem:
        parties_filter.append("D")
    if p_oth:
        parties_filter.append("Other")
    if len(parties_filter) == 3:
        parties_filter = None  # No filter needed if all selected

    # Speaker Name Filter
    speaker_query = st.text_input(
        "Speaker Name Filter",
        value="",
        placeholder="e.g. Hamlin, Kennedy, Humphrey",
    )

    # State Filter
    state_options = [
        "AL",
        "AK",
        "AZ",
        "AR",
        "CA",
        "CO",
        "CT",
        "DE",
        "FL",
        "GA",
        "HI",
        "ID",
        "IL",
        "IN",
        "IA",
        "KS",
        "KY",
        "LA",
        "ME",
        "MD",
        "MA",
        "MI",
        "MN",
        "MS",
        "MO",
        "MT",
        "NE",
        "NV",
        "NH",
        "NJ",
        "NM",
        "NY",
        "NC",
        "ND",
        "OH",
        "OK",
        "OR",
        "PA",
        "RI",
        "SC",
        "SD",
        "TN",
        "TX",
        "UT",
        "VT",
        "VA",
        "WA",
        "WV",
        "WI",
        "WY",
    ]
    selected_states = st.multiselect("States", options=state_options)
    states_filter = selected_states if selected_states else None

    st.markdown("---")
    # Results Options
    sort_by = st.selectbox(
        "Sort Results By",
        options=["date_asc", "date_desc", "word_count_desc", "word_count_asc"],
        format_func=lambda x: {
            "date_asc": "Date (Oldest First)",
            "date_desc": "Date (Newest First)",
            "word_count_desc": "Speech Length (Longest First)",
            "word_count_asc": "Speech Length (Shortest First)",
        }.get(x, x),
    )

    page_size = st.select_slider(
        "Results per page", options=[10, 25, 50, 100], value=25
    )


# ---------------------------------------------------------
# Main Content Area
# ---------------------------------------------------------

# Hero Header
st.markdown(
    """
<div class="hero-header">
    <div class="hero-title">🏛️ Congressional Record Text Explorer</div>
    <div class="hero-subtitle">
        A research platform for humanities scholars exploring the Stanford Congressional Record corpus (Congresses 43–114).
    </div>
</div>
""",
    unsafe_allow_html=True,
)

# Top KPI Summary Cards
kpi_c1, kpi_c2, kpi_c3, kpi_c4 = st.columns(4)
with kpi_c1:
    st.markdown(
        f"""
    <div class="stat-card">
        <div class="stat-number">{corpus_stats['total_congresses']} / 72</div>
        <div class="stat-label">Congresses Indexed</div>
    </div>
    """,
        unsafe_allow_html=True,
    )

with kpi_c2:
    st.markdown(
        f"""
    <div class="stat-card">
        <div class="stat-number">{corpus_stats['total_speeches']:,}</div>
        <div class="stat-label">Total Speeches</div>
    </div>
    """,
        unsafe_allow_html=True,
    )

with kpi_c3:
    st.markdown(
        f"""
    <div class="stat-card">
        <div class="stat-number">{corpus_stats['year_min']} – {corpus_stats['year_max']}</div>
        <div class="stat-label">Historical Span</div>
    </div>
    """,
        unsafe_allow_html=True,
    )

with kpi_c4:
    st.markdown(
        f"""
    <div class="stat-card">
        <div class="stat-number">{corpus_stats['total_size_mb']} MB</div>
        <div class="stat-label">Compressed Index Size</div>
    </div>
    """,
        unsafe_allow_html=True,
    )

st.markdown("<div style='height: 15px;'></div>", unsafe_allow_html=True)

# Tabs
tab_search, tab_analytics, tab_corpus = st.tabs(
    ["🔎 Search & Read", "📊 Corpus Trends & Analytics", "📁 Corpus Status"]
)


# ---------------------------------------------------------
# Tab 1: Search & Read
# ---------------------------------------------------------
with tab_search:
    if corpus_stats["total_congresses"] == 0:
        st.warning(
            "⚠️ No processed Congress Parquet files found. Please process data in the 'Corpus Status' tab or via scripts."
        )
    else:
        # Build search filter
        search_filter = SearchFilter(
            query=query,
            search_mode=search_mode,
            year_min=year_range[0],
            year_max=year_range[1],
            chambers=chambers_filter,
            parties=parties_filter,
            states=states_filter,
            speaker_query=speaker_query,
            limit=page_size,
            offset=0,
            sort_by=sort_by,
        )

        # Pagination State
        if "page_num" not in st.session_state:
            st.session_state.page_num = 1

        # Execute Search
        t_start = time.time()
        search_filter.offset = (st.session_state.page_num - 1) * page_size
        results_df, total_matches, highlight_terms = engine.search(
            search_filter
        )
        t_elapsed = round((time.time() - t_start) * 1000, 1)

        # Results Header & Stats
        res_col1, res_col2 = st.columns([3, 2])
        with res_col1:
            st.markdown(
                f"**Found `{total_matches:,}` matching speeches** "
                f"across years `{year_range[0]}`–`{year_range[1]}` (Query executed in `{t_elapsed} ms`)"
            )

        with res_col2:
            if total_matches > 0:
                # CSV Export Button
                csv_buffer = io.StringIO()
                # Prepare clean CSV export dataframe without truncation
                export_cols = [
                    "speech_id",
                    "congress",
                    "date",
                    "year",
                    "chamber",
                    "speaker",
                    "first_name",
                    "last_name",
                    "state",
                    "party",
                    "word_count",
                    "speech_text",
                ]
                results_df[export_cols].to_csv(csv_buffer, index=False)

                st.download_button(
                    label="⬇️ Export Current Results (CSV)",
                    data=csv_buffer.getvalue(),
                    file_name=f"congress_speeches_{query.replace(' ', '_') or 'all'}_{datetime.now().strftime('%Y%m%d_%H%M%S')}.csv",
                    mime="text/csv",
                    use_container_width=True,
                )

        st.markdown("<hr style='margin: 0.5rem 0 1rem 0;'>", unsafe_allow_html=True)

        # Render Speech Cards
        if results_df.empty:
            st.info(
                "💡 No speeches matched your search criteria. Try broadening your keywords, date range, or removing filters."
            )
        else:
            for idx, row in results_df.iterrows():
                # Format Date
                d_str = str(row.get("date", ""))
                if len(d_str) == 8 and d_str.isdigit():
                    try:
                        date_obj = datetime.strptime(d_str, "%Y%m%d")
                        formatted_date = date_obj.strftime("%B %d, %Y")
                    except Exception:
                        formatted_date = d_str
                else:
                    formatted_date = d_str or f"Year {row.get('year', '')}"

                # Speaker Name & Title
                first_name = row.get("first_name", "") or ""
                last_name = row.get("last_name", "") or ""
                raw_speaker = row.get("speaker", "") or "Unknown Speaker"

                if first_name and last_name:
                    display_speaker = (
                        f"{first_name.title()} {last_name.title()}"
                    )
                else:
                    display_speaker = raw_speaker

                # Badges
                chamber_code = row.get("chamber", "")
                chamber_name = (
                    "Senate"
                    if chamber_code == "S"
                    else ("House" if chamber_code == "H" else "Congress")
                )

                party_code = row.get("party", "")
                if party_code == "R":
                    party_badge = "<span class='badge badge-party-r'>Republican (R)</span>"
                elif party_code == "D":
                    party_badge = "<span class='badge badge-party-d'>Democrat (D)</span>"
                elif party_code:
                    party_badge = f"<span class='badge badge-party-other'>{party_code}</span>"
                else:
                    party_badge = "<span class='badge badge-party-other'>Non-partisan / Procedural</span>"

                state_code = row.get("state", "")
                state_badge = (
                    f"<span class='badge badge-speaker'>{state_code}</span>"
                    if state_code
                    else ""
                )

                # Snippet Generation
                snippet_html = engine.extract_snippet(
                    row.get("speech_text", ""),
                    terms=highlight_terms,
                    window_chars=180,
                    max_snippets=2,
                )

                # Render Card Container
                with st.container():
                    st.markdown(
                        f"""
                    <div class="speech-card">
                        <div class="speech-meta">
                            <span class="badge badge-date">📅 {formatted_date}</span>
                            <span class="badge badge-congress">🏛️ {row.get('congress', '')}th Congress</span>
                            <span class="badge badge-chamber">{chamber_name}</span>
                            {party_badge}
                            {state_badge}
                            <span class="badge badge-speaker">💬 {row.get('word_count', 0):,} words</span>
                        </div>
                        <div class="speech-speaker-name">{display_speaker}</div>
                        <div class="snippet-box">{snippet_html}</div>
                    </div>
                    """,
                        unsafe_allow_html=True,
                    )

                    # Full Speech Expander
                    with st.expander(
                        f"📖 Read Full Speech by {display_speaker} ({formatted_date})",
                        expanded=False,
                    ):
                        st.markdown(
                            f"""
                        <div class="full-speech-text">{row.get('speech_text', '')}</div>
                        """,
                            unsafe_allow_html=True,
                        )

            # Pagination Controls
            total_pages = max(1, (total_matches + page_size - 1) // page_size)
            if total_pages > 1:
                st.markdown("<br>", unsafe_allow_html=True)
                p_col1, p_col2, p_col3, p_col4 = st.columns([1, 2, 2, 1])

                with p_col1:
                    if st.button(
                        "⬅️ Previous",
                        disabled=(st.session_state.page_num <= 1),
                        use_container_width=True,
                    ):
                        st.session_state.page_num -= 1
                        st.rerun()

                with p_col2:
                    st.write(
                        f"Page **{st.session_state.page_num}** of **{total_pages}**"
                    )

                with p_col3:
                    target_p = st.number_input(
                        "Jump to page",
                        min_value=1,
                        max_value=total_pages,
                        value=st.session_state.page_num,
                        step=1,
                        key="jump_page_input",
                    )
                    if target_p != st.session_state.page_num:
                        st.session_state.page_num = target_p
                        st.rerun()

                with p_col4:
                    if st.button(
                        "Next ➡️",
                        disabled=(st.session_state.page_num >= total_pages),
                        use_container_width=True,
                    ):
                        st.session_state.page_num += 1
                        st.rerun()


# ---------------------------------------------------------
# Tab 2: Corpus Trends & Visual Analytics
# ---------------------------------------------------------
with tab_analytics:
    st.markdown("### 📊 Corpus Trends & Topic Distribution")

    if corpus_stats["total_congresses"] == 0:
        st.info("No data available for analytics.")
    else:
        st.markdown(
            f"Analyzing frequency and distribution for: **`{query if query.strip() else 'All Speeches'}`**"
        )

        with st.spinner("Computing aggregated historical metrics..."):
            aggs = engine.get_aggregations(search_filter)

        df_yr = aggs["by_year"]
        df_party = aggs["by_party"]
        df_chamber = aggs["by_chamber"]

        chart_c1, chart_c2 = st.columns([2, 1])

        with chart_c1:
            st.markdown("#### 📈 Mentions / Speeches Over Time")
            if not df_yr.empty:
                df_yr_indexed = df_yr.set_index("year")
                st.line_chart(df_yr_indexed["count"], height=320)
            else:
                st.info("No timeline data.")

        with chart_c2:
            st.markdown("#### 👥 Party Distribution")
            if not df_party.empty:
                df_p_indexed = df_party.set_index("party_label")
                st.bar_chart(df_p_indexed["count"], height=320)
            else:
                st.info("No party data.")

        st.markdown("---")
        st.markdown("#### 🏛️ Chamber Breakdown")
        if not df_chamber.empty:
            c_cols = st.columns(len(df_chamber))
            for i, r in df_chamber.iterrows():
                with c_cols[i]:
                    st.metric(label=r["chamber_label"], value=f"{r['count']:,}")


# ---------------------------------------------------------
# Tab 3: Corpus Status & Ingestion
# ---------------------------------------------------------
with tab_corpus:
    st.markdown("### 📁 Dataset Coverage & Parquet Index Status")

    st.markdown("""
    The canonical Stanford Congressional Record corpus comprises:
    - **Congresses 043–111 (1873–2010)**: Extracted from `hein-bound.zip`
    - **Congresses 112–114 (2011–2016)**: Extracted from `hein-daily.zip`
    """)

    parquet_files = engine.get_parquet_files()
    processed_congresses = set()
    for f in parquet_files:
        m = re.search(r"congress_(\d+)\.parquet", f.name)
        if m:
            processed_congresses.add(int(m.group(1)))

    st.write(
        f"**Indexed {len(processed_congresses)} of 72 Congresses** ({min(processed_congresses) if processed_congresses else 0} to {max(processed_congresses) if processed_congresses else 0})"
    )

    # Status Grid
    status_data = []
    for c in range(43, 115):
        source = "hein-bound" if c <= 111 else "hein-daily"
        is_done = c in processed_congresses
        status_data.append(
            {
                "Congress": f"{c:03d}",
                "Source Archive": source,
                "Status": "✅ Indexed" if is_done else "⏳ Pending",
            }
        )

    df_status = pd.DataFrame(status_data)
    st.dataframe(df_status, use_container_width=True, height=350)
