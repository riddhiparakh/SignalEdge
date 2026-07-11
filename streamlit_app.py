"""
SignalEdge — Streamlit frontend.
Imports Python modules directly; no FastAPI server needed.
"""

import json
import os
import sys

import streamlit as st

# ── Secrets → env vars (Streamlit Cloud injects via st.secrets) ────────────────
try:
    for key in ("NEWSDATA_API_KEY", "ANTHROPIC_API_KEY"):
        if key in st.secrets and not os.environ.get(key):
            os.environ[key] = st.secrets[key]
except Exception:
    pass  # no secrets.toml in local dev — rely on .env

# ── Project root on sys.path ───────────────────────────────────────────────────
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from dotenv import load_dotenv
load_dotenv()

from db.schema import get_connection, init_db
from agent.rag import find_relevant
from agent.scorer import get_track_record
from ingestion.news import hours_ago, is_sensitive
import ingestion.markets as markets_module
import ingestion.news as news_module
import agent.brain as brain_module

init_db()

# ── Page config ────────────────────────────────────────────────────────────────
st.set_page_config(
    page_title="SignalEdge",
    page_icon="⚡",
    layout="wide",
)

st.markdown("""
<style>
  .metric-box {
    background: #1A1D23;
    border: 1px solid #2D3139;
    border-radius: 12px;
    padding: 20px;
    text-align: center;
  }
  .metric-val { font-size: 1.8rem; font-weight: 700; color: #F0F2F5; }
  .metric-lbl { font-size: 0.75rem; color: #6B7280; margin-top: 4px; }
  .accent { color: #00D4AA !important; }
  .up   { color: #4ADE80; }
  .down { color: #F87171; }
  .neutral { color: #9CA3AF; }
  .tag {
    display: inline-block;
    padding: 2px 10px;
    border-radius: 20px;
    font-size: 0.72rem;
    font-weight: 600;
  }
  .tag-up      { background:#052e1b; color:#4ADE80; }
  .tag-down    { background:#2d0a0a; color:#F87171; }
  .tag-neutral { background:#1c1e25; color:#9CA3AF; }
</style>
""", unsafe_allow_html=True)


# ── Sidebar ────────────────────────────────────────────────────────────────────
with st.sidebar:
    st.title("⚡ SignalEdge")
    st.caption("Polymarket divergence signals")
    st.divider()

    st.subheader("Pipeline")

    if st.button("1. Fetch Markets", use_container_width=True):
        with st.spinner("Fetching Polymarket data…"):
            try:
                fetched = markets_module.run()
                st.success(f"Fetched {len(fetched)} markets")
            except Exception as e:
                st.error(str(e))

    if st.button("2. Fetch News", use_container_width=True):
        with st.spinner("Fetching news headlines…"):
            try:
                conn = get_connection()
                rows = conn.execute("""
                    SELECT m.id, m.question, mp.yes_price, mp.volume
                    FROM markets m
                    LEFT JOIN market_prices mp ON mp.market_id = m.id
                    WHERE m.active = 1
                    GROUP BY m.id HAVING mp.fetched_at = MAX(mp.fetched_at)
                """).fetchall()
                conn.close()
                mkts = [dict(r) for r in rows]
                if not mkts:
                    st.warning("No markets yet — run step 1 first.")
                else:
                    articles = news_module.run(mkts)
                    st.success(f"Fetched {len(articles)} articles")
            except Exception as e:
                st.error(str(e))

    if st.button("3. Run Agent", use_container_width=True):
        with st.spinner("Running Claude agent…"):
            try:
                conn = get_connection()
                rows = conn.execute("""
                    SELECT m.id, m.question, m.end_date, m.category,
                           mp.yes_price, mp.no_price, mp.volume
                    FROM markets m
                    LEFT JOIN market_prices mp ON mp.market_id = m.id
                    WHERE m.active = 1
                    GROUP BY m.id HAVING mp.fetched_at = MAX(mp.fetched_at)
                """).fetchall()
                conn.close()
                mkts = [dict(r) for r in rows]
                if not mkts:
                    st.warning("No markets yet — run step 1 first.")
                else:
                    judgments = brain_module.run(mkts)
                    st.success(f"Generated {len(judgments)} judgments")
            except Exception as e:
                st.error(str(e))

    st.divider()
    st.caption("Run steps 1 → 2 → 3 in order to populate data.")


# ── Tabs ───────────────────────────────────────────────────────────────────────
tab_markets, tab_signals, tab_track, tab_research = st.tabs(
    ["📈 Markets", "⚡ Signals", "🏆 Track Record", "🔍 Research"]
)


# ── MARKETS ───────────────────────────────────────────────────────────────────
with tab_markets:
    st.header("Live Markets")
    st.caption("Active Polymarket prediction markets · ordered by volume")

    conn = get_connection()
    rows = conn.execute("""
        SELECT m.id, m.question, m.category, m.end_date,
               mp.yes_price, mp.no_price, mp.volume, mp.fetched_at
        FROM markets m
        LEFT JOIN market_prices mp ON mp.market_id = m.id
        WHERE m.active = 1
        GROUP BY m.id
        HAVING mp.fetched_at = MAX(mp.fetched_at)
        ORDER BY mp.volume DESC
    """).fetchall()
    conn.close()
    markets = [dict(r) for r in rows]

    if not markets:
        st.info('No markets yet. Click "1. Fetch Markets" in the sidebar.')
    else:
        total_vol = sum(m.get("volume") or 0 for m in markets)
        avg_price = sum(m.get("yes_price") or 0 for m in markets) / len(markets)

        c1, c2, c3 = st.columns(3)
        c1.metric("Active Markets", len(markets))
        c2.metric("Total Volume", f"${total_vol:,.0f}")
        c3.metric("Avg YES Price", f"{avg_price*100:.0f}%")

        st.divider()

        for m in markets:
            yes = m.get("yes_price") or 0
            vol = m.get("volume") or 0
            with st.expander(f"**{m['question']}**  —  YES {yes*100:.0f}%"):
                cols = st.columns(3)
                cols[0].metric("YES Price", f"{yes*100:.1f}%")
                cols[1].metric("NO Price", f"{(m.get('no_price') or 0)*100:.1f}%")
                cols[2].metric("Volume", f"${vol:,.0f}")

                if m.get("category"):
                    st.caption(f"Category: {m['category']}")
                if m.get("end_date"):
                    st.caption(f"End date: {m['end_date']}")

                # Price history chart
                conn2 = get_connection()
                ph = conn2.execute("""
                    SELECT yes_price, fetched_at FROM market_prices
                    WHERE market_id = ? ORDER BY fetched_at ASC LIMIT 200
                """, (m["id"],)).fetchall()
                conn2.close()
                if len(ph) > 1:
                    import pandas as pd
                    df = pd.DataFrame([dict(r) for r in ph])
                    df["fetched_at"] = pd.to_datetime(df["fetched_at"])
                    df = df.set_index("fetched_at")
                    st.line_chart(df["yes_price"], height=150)


# ── SIGNALS ───────────────────────────────────────────────────────────────────
with tab_signals:
    st.header("Divergence Signals")
    st.caption("Agent judgments where market price may not reflect current evidence")

    direction = st.radio(
        "Filter",
        ["all", "up", "down", "neutral"],
        horizontal=True,
        format_func=lambda x: {"all": "All", "up": "↑ Up", "down": "↓ Down", "neutral": "→ Neutral"}[x],
    )

    conn = get_connection()
    if direction != "all":
        rows = conn.execute("""
            SELECT j.*, m.question FROM agent_judgments j
            JOIN markets m ON m.id = j.market_id
            WHERE j.direction = ?
            ORDER BY j.created_at DESC LIMIT 50
        """, (direction,)).fetchall()
    else:
        rows = conn.execute("""
            SELECT j.*, m.question FROM agent_judgments j
            JOIN markets m ON m.id = j.market_id
            ORDER BY j.created_at DESC LIMIT 50
        """).fetchall()
    conn.close()

    judgments = []
    for r in rows:
        d = dict(r)
        try:
            d["cited_urls"] = json.loads(d.get("cited_urls") or "[]")
        except Exception:
            d["cited_urls"] = []
        mid = ((d.get("confidence_low") or 0) + (d.get("confidence_high") or 0)) / 2
        d["divergence"] = round(mid - (d.get("market_price_at_call") or 0), 4)
        d["age"] = hours_ago(d.get("created_at", ""))
        judgments.append(d)

    if not judgments:
        st.info('No signals yet. Run steps 1 → 2 → 3 in the sidebar.')
    else:
        st.caption(f"{len(judgments)} signal(s)")
        for j in judgments:
            dir_icon = {"up": "↑", "down": "↓", "neutral": "→"}.get(j["direction"], "")
            dir_color = {"up": "green", "down": "red", "neutral": "gray"}.get(j["direction"], "gray")

            with st.container(border=True):
                h_col, d_col = st.columns([4, 1])
                with h_col:
                    st.markdown(f"**{j['question']}**")
                    st.caption(j.get("age", ""))
                with d_col:
                    st.markdown(
                        f"<span style='color:{'#4ADE80' if j['direction']=='up' else '#F87171' if j['direction']=='down' else '#9CA3AF'};font-size:1.3rem;font-weight:700'>{dir_icon} {j['direction'].upper()}</span>",
                        unsafe_allow_html=True,
                    )

                c1, c2, c3, c4 = st.columns(4)
                c1.metric("Market Price", f"{(j.get('market_price_at_call') or 0)*100:.0f}%")
                c2.metric("Agent Range", f"{(j.get('confidence_low') or 0)*100:.0f}–{(j.get('confidence_high') or 0)*100:.0f}%")
                c3.metric("Divergence", f"{j['divergence']*100:+.1f}pp")
                c4.metric("Headlines", j.get("headline_count", 0))

                if j.get("rationale"):
                    with st.expander("Rationale"):
                        st.write(j["rationale"])
                        if j.get("cited_urls"):
                            st.markdown("**Sources:**")
                            for url in j["cited_urls"]:
                                st.markdown(f"- {url}")


# ── TRACK RECORD ──────────────────────────────────────────────────────────────
with tab_track:
    st.header("Track Record")
    st.caption("Agent accuracy graded against Polymarket resolutions · honestly reported")

    record = get_track_record()

    if record.get("sample_size_warning"):
        st.warning(f"Insufficient data for reliable statistics. {record.get('sample_size_note', '')} Meaningful hit rates require at least 30 graded judgments.")

    if record.get("total_graded", 0) == 0:
        st.info("No graded judgments yet. Markets must resolve on Polymarket before scoring is possible.")

        st.markdown("""
**How scoring works:**
1. Each pipeline run checks Polymarket for resolved markets
2. "up" judgment + market resolved YES = ✓ correct
3. "down" judgment + market resolved YES = ✗ wrong
4. "neutral" abstentions are never graded
        """)
    else:
        hit_pct = f"{record['hit_rate']*100:.1f}%" if record.get("hit_rate") is not None else "—"
        c1, c2, c3 = st.columns(3)
        c1.metric("Hit Rate", hit_pct)
        c2.metric("Correct / Graded", f"{record['correct']} / {record['total_graded']}")
        c3.metric("Abstentions", record["total_scored"] - record["total_graded"])

        st.divider()
        st.subheader("By Direction")

        import pandas as pd
        rows_data = []
        for dir_ in ("up", "down"):
            stats = record["by_direction"][dir_]
            hr = f"{stats['hit_rate']*100:.1f}%" if stats.get("hit_rate") is not None else "—"
            rows_data.append({
                "Direction": "↑ UP" if dir_ == "up" else "↓ DOWN",
                "Graded": stats["graded"],
                "Correct": stats["correct"],
                "Hit Rate": hr,
            })
        st.table(pd.DataFrame(rows_data))

        st.caption(
            "Honest framing: Hit rates at small sample sizes have wide confidence intervals "
            "and can be driven by luck rather than signal. A 70% rate from 10 judgments has "
            "a 95% CI of roughly ±29 percentage points."
        )


# ── RESEARCH ──────────────────────────────────────────────────────────────────
with tab_research:
    st.header("Research Chat")
    st.caption("Semantic search across all indexed headlines")

    query = st.text_input("Search headlines…", placeholder="e.g. Federal Reserve interest rates")

    if query:
        if is_sensitive(query):
            st.warning("Query flagged as sensitive — search disabled.")
        else:
            with st.spinner("Searching…"):
                try:
                    articles = find_relevant(query, top_k=8)
                    for a in articles:
                        a["age"] = hours_ago(a.get("published_at", ""))

                    if not articles:
                        st.info("No relevant articles found.")
                    else:
                        st.caption(f"{len(articles)} article(s) found")
                        for a in articles:
                            with st.container(border=True):
                                st.markdown(f"**[{a['title']}]({a.get('url', '#')})**")
                                cols = st.columns([2, 1])
                                cols[0].caption(f"{a.get('source', '')}  ·  {a.get('age', '')}")
                                if a.get("description"):
                                    st.write(a["description"])
                except Exception as e:
                    st.error(f"Search error: {e}")
