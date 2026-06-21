"""
SignalEdge Dashboard — Phase 4

4 tabs:
  1. Live Markets    — active markets + price history
  2. Divergence Feed — agent signals with rationale + cited sources
  3. Research Chat   — semantic headline search (ChromaDB RAG)
  4. Track Record    — agent hit rate, honestly framed

Run from the project root:
    streamlit run dashboard/app.py
"""

import json
import os
import sys

import pandas as pd
import streamlit as st

# Ensure project root is in sys.path regardless of where streamlit is launched from.
# Without this, `from db.schema import ...` fails when run as `streamlit run dashboard/app.py`.
_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

from db.schema import get_connection, init_db
from agent.rag import find_relevant
from agent.scorer import get_track_record
from ingestion.news import is_sensitive, hours_ago
from ingestion import markets as markets_module
from ingestion import news as news_module
from agent import brain as brain_module

# ─── Streamlit Cloud secret injection ─────────────────────────────────────────
# On Streamlit Community Cloud, API keys are stored in the secrets panel (not .env).
# This block copies st.secrets → os.environ so the rest of the code works unchanged.
# Locally, dotenv takes over (loaded by each module's load_dotenv() call).
try:
    for _k, _v in st.secrets.items():
        if _k not in os.environ:
            os.environ[_k] = str(_v)
except Exception:
    pass  # st.secrets is empty or unavailable (local dev) — dotenv handles it

# ─── Page config ──────────────────────────────────────────────────────────────
st.set_page_config(
    page_title="SignalEdge",
    page_icon="📡",
    layout="wide",
    initial_sidebar_state="collapsed",
)

# ─── Data loaders (cached 5 min so the DB isn't hit on every widget interaction)

@st.cache_data(ttl=300)
def load_markets() -> list[dict]:
    """Active markets joined with their most-recent price snapshot."""
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
    return [dict(r) for r in rows]


@st.cache_data(ttl=300)
def load_price_history(market_id: str) -> list[dict]:
    conn = get_connection()
    rows = conn.execute("""
        SELECT yes_price, fetched_at FROM market_prices
        WHERE market_id = ?
        ORDER BY fetched_at ASC
        LIMIT 200
    """, (market_id,)).fetchall()
    conn.close()
    return [dict(r) for r in rows]


@st.cache_data(ttl=300)
def load_recent_judgments(n: int = 50) -> list[dict]:
    conn = get_connection()
    rows = conn.execute("""
        SELECT j.*, m.question
        FROM agent_judgments j
        JOIN markets m ON m.id = j.market_id
        ORDER BY j.created_at DESC
        LIMIT ?
    """, (n,)).fetchall()
    conn.close()
    return [dict(r) for r in rows]


@st.cache_data(ttl=300)
def load_track_record() -> dict:
    return get_track_record()


@st.cache_data(ttl=60, show_spinner=False)
def search_headlines(query: str, top_k: int = 8) -> list[dict]:
    """Cache research queries for 1 minute — repeated questions feel instant."""
    return find_relevant(query, top_k=top_k)


# ─── Tab 1: Live Markets ───────────────────────────────────────────────────────

def tab_live_markets() -> None:
    markets = load_markets()

    if not markets:
        st.info(
            "No markets in the database yet.  \n"
            "Run `python run_pipeline.py --ingest-only` to fetch Polymarket data."
        )
        return

    prices = [m["yes_price"] or 0 for m in markets]
    volumes = [m["volume"] or 0 for m in markets]

    c1, c2, c3 = st.columns(3)
    c1.metric("Active Markets", len(markets))
    c2.metric("Total Volume", f"${sum(volumes):,.0f}")
    c3.metric("Avg YES Price", f"{sum(prices)/len(prices):.0%}" if prices else "—")

    st.divider()
    st.caption(f"{len(markets)} markets · data refreshes every 5 min · click a market to expand")

    for mkt in markets:
        yes_price = mkt["yes_price"]
        yes_str = f"{yes_price:.0%}" if yes_price is not None else "—"
        arrow = "↑" if (yes_price or 0) > 0.5 else "↓"
        vol_str = f"${mkt['volume']:,.0f}" if mkt["volume"] else "—"

        with st.expander(f"{arrow} {mkt['question']}", expanded=False):
            mc1, mc2, mc3 = st.columns(3)
            mc1.metric("YES Price", yes_str)
            mc2.metric("Volume", vol_str)
            mc3.metric("Category", mkt["category"] or "—")

            history = load_price_history(mkt["id"])
            if len(history) >= 2:
                df = pd.DataFrame(history)
                df["fetched_at"] = pd.to_datetime(df["fetched_at"])
                df = df.rename(columns={"fetched_at": "time", "yes_price": "YES price"})
                df = df.set_index("time")
                st.line_chart(df["YES price"], height=140, use_container_width=True)
                st.caption(f"Price history · {len(history)} snapshot(s)")
            else:
                st.caption("Run the pipeline a few more times to build price history (one point per run).")

            if mkt["end_date"]:
                st.caption(f"Resolves: {mkt['end_date']}")


# ─── Tab 2: Divergence Feed ───────────────────────────────────────────────────

def tab_divergence_feed() -> None:
    judgments = load_recent_judgments()

    if not judgments:
        st.info(
            "No agent signals yet.  \n"
            "Run `python run_pipeline.py` (without `--ingest-only`) to generate divergence signals."
        )
        return

    st.caption(f"{len(judgments)} signal(s) logged · most recent first")

    direction_filter = st.radio(
        "Filter by signal direction",
        ["All", "↑ Up (underpriced)", "↓ Down (overpriced)", "→ Neutral"],
        horizontal=True,
    )
    filter_map = {"↑ Up (underpriced)": "up", "↓ Down (overpriced)": "down", "→ Neutral": "neutral"}
    if direction_filter != "All":
        judgments = [j for j in judgments if j["direction"] == filter_map[direction_filter]]

    if not judgments:
        st.write("No signals match this filter.")
        return

    st.divider()

    for j in judgments:
        direction = j["direction"]
        arrow    = {"up": "↑", "down": "↓", "neutral": "→"}[direction]
        badge    = {"up": "🟢", "down": "🔴", "neutral": "⚪"}[direction]

        agent_mid  = (j["confidence_low"] + j["confidence_high"]) / 2
        divergence = agent_mid - j["market_price_at_call"]
        div_str    = f"{divergence:+.1%}"

        with st.expander(f"{badge} {arrow}  {j['question'][:85]}", expanded=False):
            jc1, jc2, jc3, jc4 = st.columns(4)
            jc1.metric("Signal", f"{arrow} {direction.upper()}")
            jc2.metric("Market Price", f"{j['market_price_at_call']:.0%}")
            jc3.metric("Agent Range", f"{j['confidence_low']:.0%}–{j['confidence_high']:.0%}")
            jc4.metric("Divergence", div_str)

            st.markdown(f"**Rationale:** {j['rationale']}")

            try:
                urls = json.loads(j["cited_urls"] or "[]")
                if urls:
                    st.markdown("**Cited sources:**")
                    for url in urls:
                        st.markdown(f"- [{url}]({url})")
            except (json.JSONDecodeError, TypeError):
                pass

            age = hours_ago(j["created_at"])
            flag = "✓ sufficient evidence" if j["was_sufficient"] else "⚠ insufficient evidence"
            st.caption(f"{age} · {j['headline_count']} headlines used · {flag}")


# ─── Tab 3: Research Chat ─────────────────────────────────────────────────────

def tab_research_chat() -> None:
    st.markdown(
        "Search the headline database using **natural language** — not just keywords.  \n"
        "*Example: \"Federal Reserve interest rate\" surfaces articles about "
        "\"FOMC monetary tightening\" even with no shared words.*"
    )

    st.divider()

    # Session state holds the full chat history so messages persist across reruns
    if "chat_history" not in st.session_state:
        st.session_state.chat_history = []

    # Render existing messages
    for msg in st.session_state.chat_history:
        with st.chat_message(msg["role"]):
            st.markdown(msg["content"])

    # New input
    if prompt := st.chat_input("Ask about any market topic (e.g. 'US Iran nuclear deal')..."):
        # Show and store the user message
        with st.chat_message("user"):
            st.markdown(prompt)
        st.session_state.chat_history.append({"role": "user", "content": prompt})

        with st.chat_message("assistant"):
            # Sensitive topic guardrail — fires before any ChromaDB call
            if is_sensitive(prompt):
                response = (
                    "⚠️ **Sensitive topic detected.**  \n"
                    "This query touches content I'm configured not to research "
                    "(violence, personal medical details, private individuals).  \n"
                    "Try asking about economic indicators, geopolitical treaties, "
                    "elections, or other publicly-relevant events."
                )
                st.markdown(response)
            else:
                with st.spinner("Searching headlines..."):
                    articles = search_headlines(prompt, top_k=8)

                if not articles:
                    response = (
                        "📭 **No relevant headlines found.**  \n\n"
                        "The headline database may be empty.  \n"
                        "Run `python run_pipeline.py --ingest-only` to fetch articles, then ask again."
                    )
                    st.markdown(response)
                else:
                    lines = [f"Found **{len(articles)} relevant headline(s):**\n"]
                    for i, a in enumerate(articles, 1):
                        age   = hours_ago(a.get("published_at", ""))
                        title = a.get("title", "(no title)")
                        src   = a.get("source", "unknown")
                        url   = a.get("url", "")
                        desc  = (a.get("description") or "").strip()

                        lines.append(f"**[{i}] {title}**  ")
                        lines.append(f"*{src} · {age}*  ")
                        if desc:
                            lines.append(f"{desc[:220]}  ")
                        if url:
                            lines.append(f"🔗 [{url}]({url})  ")
                        lines.append("")

                    response = "\n".join(lines)
                    st.markdown(response)

            st.session_state.chat_history.append({"role": "assistant", "content": response})

    # Clear button only appears once there's history
    if st.session_state.chat_history:
        if st.button("Clear chat", type="secondary"):
            st.session_state.chat_history = []
            st.rerun()


# ─── Tab 4: Track Record ─────────────────────────────────────────────────────

def tab_track_record() -> None:
    record = load_track_record()

    # Sample size warning is ALWAYS shown first — honest framing is core to the project
    if record["sample_size_warning"]:
        st.warning(
            f"⚠️ **Insufficient data for reliable statistics.**  \n"
            f"{record['sample_size_note']}  \n"
            f"Meaningful hit rates require at least 30 graded judgments "
            f"(markets need to resolve first)."
        )

    if record["total_graded"] == 0:
        st.info("No graded judgments yet — markets must resolve before scoring is possible.")
        st.markdown("""
        **How scoring works:**
        1. Each pipeline run checks Polymarket for newly resolved markets
        2. When a market settles (YES → 1.0 or NO → 0.0), all judgments for it are graded
        3. `"up"` judgment + resolved YES = ✓ correct
        4. `"down"` judgment + resolved YES = ✗ wrong
        5. `"neutral"` abstentions are recorded but never graded
        """)
        return

    tc1, tc2, tc3 = st.columns(3)
    hit_display = f"{record['hit_rate']:.1%}" if record["hit_rate"] is not None else "—"
    tc1.metric("Hit Rate", hit_display)
    tc2.metric("Correct / Graded", f"{record['correct']} / {record['total_graded']}")
    tc3.metric("Neutral abstentions", record["total_scored"] - record["total_graded"])

    st.divider()
    st.subheader("By Direction")

    dir_rows = []
    for direction, stats in record["by_direction"].items():
        arrow = "↑" if direction == "up" else "↓"
        hr = f"{stats['hit_rate']:.1%}" if stats["hit_rate"] is not None else "—"
        dir_rows.append({
            "Direction": f"{arrow} {direction.upper()}",
            "Graded": stats["graded"],
            "Correct": stats["correct"],
            "Hit Rate": hr,
        })

    st.dataframe(pd.DataFrame(dir_rows), hide_index=True, use_container_width=True)

    st.divider()
    st.caption(
        "**Honest framing:** Hit rates at small sample sizes can look impressive by chance alone. "
        "A 70% rate from 10 judgments has a 95% confidence interval of roughly ±29 percentage points "
        "— nearly meaningless. We report this transparently."
    )


# ─── Main ─────────────────────────────────────────────────────────────────────

def _sidebar() -> None:
    """
    Pipeline controls in the sidebar — useful for Streamlit Cloud demos
    where there's no local terminal to run run_pipeline.py.

    Step 1 (Fetch Markets) needs no API key.
    Steps 2 and 3 need NEWSDATA_API_KEY and ANTHROPIC_API_KEY respectively.
    """
    with st.sidebar:
        st.header("⚙️ Pipeline Controls")
        st.caption("Run each step in order to populate the dashboard.")

        st.markdown("**Step 1 — Markets** *(no API key required)*")
        if st.button("🔄 Fetch live markets", use_container_width=True):
            with st.spinner("Fetching from Polymarket..."):
                init_db()
                fetched = markets_module.run()
            st.success(f"Fetched {len(fetched)} markets")
            st.cache_data.clear()
            st.rerun()

        st.markdown("**Step 2 — News** *(requires NEWSDATA_API_KEY)*")
        has_news_key = bool(os.getenv("NEWSDATA_API_KEY"))
        if not has_news_key:
            st.caption("⚠ NEWSDATA_API_KEY not set")
        if st.button("📰 Fetch news headlines", disabled=not has_news_key, use_container_width=True):
            markets = load_markets()
            if not markets:
                st.warning("Fetch markets first (Step 1).")
            else:
                with st.spinner(f"Fetching news for {len(markets)} markets..."):
                    news_module.run(markets)
                st.success("News fetched and stored.")
                st.cache_data.clear()
                st.rerun()

        st.markdown("**Step 3 — Agent** *(requires ANTHROPIC_API_KEY)*")
        has_claude_key = bool(os.getenv("ANTHROPIC_API_KEY"))
        if not has_claude_key:
            st.caption("⚠ ANTHROPIC_API_KEY not set")
        if st.button("🤖 Run agent analysis", disabled=not has_claude_key, use_container_width=True):
            markets = load_markets()
            if not markets:
                st.warning("Fetch markets first (Step 1).")
            else:
                with st.spinner("Analysing markets with Claude..."):
                    brain_module.run(markets)
                st.success("Agent judgments saved.")
                st.cache_data.clear()
                st.rerun()

        st.divider()
        st.caption(
            "Dashboard data auto-refreshes every 5 min from the database.  \n"
            "Run `python run_pipeline.py` locally to execute all steps at once."
        )


def main() -> None:
    st.title("📡 SignalEdge")
    st.caption(
        "Prediction market signal detection · NOT financial advice · Research tool only"
    )

    _sidebar()

    tab1, tab2, tab3, tab4 = st.tabs([
        "📊 Live Markets",
        "⚡ Divergence Feed",
        "💬 Research Chat",
        "🏆 Track Record",
    ])

    with tab1:
        tab_live_markets()
    with tab2:
        tab_divergence_feed()
    with tab3:
        tab_research_chat()
    with tab4:
        tab_track_record()


if __name__ == "__main__":
    main()
