"""
OpsLens - Product Intelligence Dashboard

Four views, each answering one question a product team actually asks:
  1. Product Health      - where is friction concentrated?
  2. Product Friction    - what is driving it, and who is affected?
  3. Recommendation      - what should we do, and how will we know it worked?
  4. Automation          - is the AI pipeline working?

Run with:  streamlit run dashboard/app.py
"""

import sqlite3
from pathlib import Path

import pandas as pd
import plotly.express as px
import streamlit as st

# ----------------------------------------------------------------------
# CONFIG
# ----------------------------------------------------------------------
ROOT = Path(__file__).resolve().parent.parent
DB_PATH = ROOT / "data" / "opslens.db"
AI_PATH = ROOT / "data" / "ai_classifications.csv"

BASELINE_REPEAT_RATE = 0.1838
FOCUS_ISSUES = ("refund_status_unclear", "refund_not_received")

st.set_page_config(page_title="OpsLens", page_icon="🔍", layout="wide")


# ----------------------------------------------------------------------
# DATA
# ----------------------------------------------------------------------
@st.cache_data
def run_query(sql: str) -> pd.DataFrame:
    with sqlite3.connect(DB_PATH) as con:
        return pd.read_sql(sql, con)


@st.cache_data
def load_ai() -> pd.DataFrame | None:
    if AI_PATH.exists():
        return pd.read_csv(AI_PATH)
    return None


# ----------------------------------------------------------------------
# SIDEBAR
# ----------------------------------------------------------------------
st.sidebar.title("OpsLens")
st.sidebar.caption("Product intelligence for customer operations")

page = st.sidebar.radio(
    "View",
    ["Product Health", "Product Friction", "Recommendation", "Automation"],
)



# ======================================================================
# 1. PRODUCT HEALTH
# ======================================================================
if page == "Product Health":
    st.title("Product Health")
    st.caption("Where is customer friction concentrated?")

    overall = run_query("""
        SELECT COUNT(*) AS tickets,
               AVG(repeat_contact * 1.0) AS repeat_rate,
               AVG(sla_breached * 1.0)   AS breach_rate,
               AVG(resolution_hours)     AS mean_hours,
               AVG(csat)                 AS csat
        FROM clean_tickets
    """).iloc[0]

    c1, c2, c3, c4 = st.columns(4)
    c1.metric("Repeat-contact rate", f"{overall.repeat_rate:.1%}",
              help="PRIMARY KPI. Share of tickets that are a follow-up from the "
                   "same user, same area, within 7 days.")
    c2.metric("SLA breach rate", f"{overall.breach_rate:.1%}")
    c3.metric("Mean resolution", f"{overall.mean_hours:.1f}h")
    c4.metric("CSAT", f"{overall.csat:.2f}")

    st.caption(f"{int(overall.tickets):,} tickets, March-August 2026")
    st.divider()

    # ---- problem ranking
    st.subheader("Problem ranking")
    st.caption("Volume and quality disagree. That disagreement is the finding.")

    areas = run_query("""
        SELECT product_area,
               COUNT(*)                  AS tickets,
               AVG(repeat_contact * 1.0) AS repeat_rate,
               AVG(sla_breached * 1.0)   AS breach_rate,
               AVG(resolution_hours)     AS mean_hours,
               AVG(csat)                 AS csat
        FROM clean_tickets
        GROUP BY product_area
        ORDER BY repeat_rate DESC
    """)

    left, right = st.columns(2)

    with left:
        fig = px.bar(areas.sort_values("tickets"), x="tickets", y="product_area",
                     orientation="h", title="Ticket volume",
                     labels={"tickets": "Tickets", "product_area": ""})
        fig.update_traces(marker_color="#9aa5b1")
        st.plotly_chart(fig, use_container_width=True)

    with right:
        ranked = areas.sort_values("repeat_rate").copy()
        ranked["status"] = ranked["repeat_rate"].apply(
            lambda r: "Above baseline" if r > BASELINE_REPEAT_RATE else "At or below baseline"
        )
        fig = px.bar(ranked, x="repeat_rate", y="product_area", orientation="h",
                     color="status", title="Repeat-contact rate (primary KPI)",
                     labels={"repeat_rate": "Repeat rate", "product_area": "", "status": ""},
                     color_discrete_map={"Above baseline": "#d64545",
                                         "At or below baseline": "#9aa5b1"})
        fig.add_vline(x=BASELINE_REPEAT_RATE, line_dash="dash",
                      annotation_text="baseline")
        fig.update_xaxes(tickformat=".0%")
        st.plotly_chart(fig, use_container_width=True)

    st.info(
        "**Payments is largest and healthy** — 26% of volume, repeat rate *below* "
        "baseline. **Refunds is fourth by size and worst on every quality metric.** "
        "Ranking by volume would have funded the wrong work."
    )

    display = areas.copy()
    for col, fmt in [("repeat_rate", "{:.1%}"), ("breach_rate", "{:.1%}"),
                     ("mean_hours", "{:.1f}h"), ("csat", "{:.2f}")]:
        display[col] = display[col].map(fmt.format)
    st.dataframe(display, use_container_width=True, hide_index=True)


# ======================================================================
# 2. PRODUCT FRICTION
# ======================================================================
elif page == "Product Friction":
    st.title("Product Friction: Refunds")
    st.caption("What is driving it, who is affected, and when did it start?")

    issues = run_query(f"""
        SELECT issue_type,
               COUNT(*)                  AS tickets,
               AVG(repeat_contact * 1.0) AS repeat_rate,
               AVG(resolution_hours)     AS mean_hours,
               AVG(csat)                 AS csat
        FROM clean_tickets
        WHERE product_area = 'Refunds'
        GROUP BY issue_type
        ORDER BY repeat_rate DESC
    """)
    issues["lift"] = issues["repeat_rate"] / BASELINE_REPEAT_RATE
    issues["excess"] = (issues["tickets"] *
                        (issues["repeat_rate"] - BASELINE_REPEAT_RATE)).round()

    st.subheader("Two of four issue types carry the damage")
    fig = px.bar(issues.sort_values("excess"), x="excess", y="issue_type",
                 orientation="h", text="lift",
                 labels={"excess": "Excess repeat contacts", "issue_type": ""})
    fig.update_traces(texttemplate="%{text:.2f}x lift", textposition="outside",
                      marker_color="#d64545")
    st.plotly_chart(fig, use_container_width=True)

    total_excess = int(issues["excess"].sum())
    focus_excess = int(issues[issues["issue_type"].isin(FOCUS_ISSUES)]["excess"].sum())
    st.info(
        f"**{focus_excess} of {total_excess} excess contacts "
        f"({focus_excess / total_excess:.0%})** sit in `refund_status_unclear` and "
        f"`refund_not_received`. Both are about *not knowing*. The two normal issue "
        f"types are about *disagreeing with an outcome*."
    )

    st.divider()

    # ---- trend with control
    st.subheader("When did it change?")
    st.caption("The control line is every other product area over the same months.")

    trend = run_query("""
        SELECT month,
               AVG(CASE WHEN product_area =  'Refunds' THEN repeat_contact * 1.0 END) AS Refunds,
               AVG(CASE WHEN product_area <> 'Refunds' THEN repeat_contact * 1.0 END) AS "All other areas"
        FROM clean_tickets
        GROUP BY month ORDER BY month
    """)
    melted = trend.melt(id_vars="month", var_name="group", value_name="repeat_rate")

    fig = px.line(melted, x="month", y="repeat_rate", color="group", markers=True,
                  labels={"repeat_rate": "Repeat-contact rate", "month": ""},
                  color_discrete_map={"Refunds": "#d64545",
                                      "All other areas": "#9aa5b1"})
    fig.update_yaxes(tickformat=".0%", range=[0, 0.42])
    st.plotly_chart(fig, use_container_width=True)

    st.info(
        "**Refunds climbs 10 points. The control stays flat.** Difference-in-differences "
        "isolates a **+7.6pp** effect specific to the two focus issue types "
        "(z = 5.6). Ticket volume grew 26% over this period — the control absorbs that."
    )

    st.divider()

    # ---- segments
    st.subheader("Who is affected?")
    segments = run_query("""
        SELECT user_segment,
               AVG(CASE WHEN product_area =  'Refunds' THEN repeat_contact * 1.0 END) AS refunds,
               AVG(CASE WHEN product_area <> 'Refunds' THEN repeat_contact * 1.0 END) AS elsewhere,
               SUM(CASE WHEN product_area =  'Refunds' THEN 1 ELSE 0 END) AS n
        FROM clean_tickets GROUP BY user_segment
    """)
    segments = segments[segments["n"] > 100].copy()
    segments["gap"] = segments["refunds"] - segments["elsewhere"]
    segments = segments.sort_values("gap", ascending=False)

    fig = px.bar(segments, x="user_segment", y="gap",
                 labels={"gap": "Friction gap vs own baseline", "user_segment": ""})
    fig.update_traces(marker_color="#d64545")
    fig.update_yaxes(tickformat=".0%")
    st.plotly_chart(fig, use_container_width=True)

    st.warning(
        "**No segment is specially affected** — every segment sits 13-16pp above its "
        "own baseline. Note SMB has the *highest raw* refund repeat rate (34.4%) but "
        "the *smallest gap*, because SMB runs hot everywhere. Ranking on the raw rate "
        "would have produced an SMB-specific recommendation the evidence does not support."
    )


# ======================================================================
# 3. RECOMMENDATION
# ======================================================================
elif page == "Recommendation":
    st.title("Recommendation")

    ai = load_ai()

    st.subheader("The finding")
    st.success(
        "**`amount_incorrect` returned zero across 200 classified tickets. "
        "The refund system calculates correctly. It does not communicate.**"
    )

    if ai is not None:
        left, right = st.columns([3, 2])

        with left:
            counts = ai["theme"].value_counts().reset_index()
            counts.columns = ["theme", "tickets"]
            fig = px.bar(counts.sort_values("tickets"), x="tickets", y="theme",
                         orientation="h", title="AI theme distribution (n=200)",
                         labels={"tickets": "Tickets", "theme": ""})
            fig.update_traces(marker_color="#4a7ba7")
            st.plotly_chart(fig, use_container_width=True)

        with right:
            gap_rate = ai["self_service_gap"].mean()
            st.metric("Self-service gap", f"{gap_rate:.0%}",
                      help="Customer contacted support only to obtain information "
                           "the product should have displayed.")
            st.caption(
                "This is the number the recommendation rests on. It reframes the "
                "problem from *support capacity* to *missing product surface*."
            )
    else:
        st.warning("`data/ai_classifications.csv` not found — run the AI step first.")

    st.divider()

    st.subheader("Proposed intervention")
    st.markdown("""
    **Ship a refund status tracker** showing, for every refund:

    1. Current state — Requested / Approved / Sent to bank / Settled
    2. **An expected arrival date** — not "processing"
    3. The date funds left our system

    Severity splits along a real line: `money_not_received` is 88% high severity
    ("I am out of pocket"), `no_status_visibility` is 82% medium ("I cannot tell what
    is happening"). A page showing only #1 solves the medium group and leaves the
    anxious majority still contacting support.
    """)

    st.divider()

    st.subheader("How we would measure it")
    m1, m2, m3 = st.columns(3)
    m1.metric("Primary KPI", "31.0% → <22%", "Refund repeat-contact rate")
    m2.metric("Guardrail 1", "93.8%", "Resolution rate — must not fall")
    m3.metric("Guardrail 2", "25.7h", "Median resolution — must not rise")

    st.caption(
        "Guardrails exist to catch the cheapest way to game the primary KPI: "
        "reducing repeat contacts by leaving tickets open, or by having agents "
        "spend far longer on each one."
    )

    st.divider()

    st.subheader("Confidence layers")
    st.dataframe(pd.DataFrame([
        {"Layer": "Observed (SQL)",
         "Claim": "1.7x repeat rate, 2 issue types, +7.6pp vs control",
         "Confidence": "High — reproducible"},
        {"Layer": "AI-derived",
         "Claim": "94% self-service gap, 84.5% visibility/money themes",
         "Confidence": "Medium — sampled, ~85-90% accurate"},
        {"Layer": "Hypothesis",
         "Claim": "Missing status surface; change around mid-June",
         "Confidence": "Low — needs deploy-log confirmation"},
        {"Layer": "Rejected",
         "Claim": "'Verification backlog'",
         "Confidence": "None — model invention, no textual basis"},
    ]), use_container_width=True, hide_index=True)


# ======================================================================
# 4. AUTOMATION
# ======================================================================
elif page == "Automation":
    st.title("Automation & AI Monitoring")

    ai = load_ai()

    st.subheader("Pipeline health")
    c1, c2, c3, c4 = st.columns(4)
    c1.metric("Tickets classified", f"{len(ai):,}" if ai is not None else "—")
    c2.metric("Pipeline success rate", "100%",
              help="Share of calls returning valid, parseable JSON. "
                   "This measures the PIPELINE, not correctness.")
    c3.metric("Theme accuracy", "86.7%", help="Hand-labelled validation set, n=30")
    c4.metric("Self-service-gap accuracy", "90.0%", help="n=30")

    st.warning(
        "**Success rate and accuracy are different metrics.** 100% success means every "
        "call returned parseable JSON. It says nothing about whether the labels are "
        "correct — that is the 86.7%. Conflating the two is the most common mistake in "
        "reporting on AI systems."
    )

    st.divider()

    st.subheader("Where the model was wrong")
    st.markdown("""
    All four theme disagreements were the same phrasing:
    *"this is my Nth time asking… nobody can tell me when I get my money back."*

    That sentence is genuinely both a visibility complaint and a money complaint.
    **This is taxonomy ambiguity, not model failure** — the fix is a tie-break rule
    in the prompt, not a larger model.

    Critically, none of the errors touched `self_service_gap`, the field the
    recommendation depends on.
    """)

    st.divider()

    st.subheader("Hybrid design: rules + AI")
    st.markdown("""
    | Layer | Handles | Why |
    |---|---|---|
    | **Deterministic rule** | Escalation decision, priority, SLA | Must be auditable and identical every time |
    | **LLM** | Interpreting free text into a theme | Language is the only part rules cannot do |

    An LLM should never own the escalation decision. If a P1 payment failure escalates
    only when a model feels like it, the system is not operable. The model classifies;
    the rule decides.
    """)

    st.divider()

    st.subheader("Failure handling")
    st.markdown("""
    - **Retry with exponential backoff** — 1s, 2s, 4s on transient errors
    - **Failures logged by type**, never silently dropped — that log *is* the success rate
    - **Configurable provider and model** — a free-tier model was deprecated mid-build;
      the fix was one line in `.env`, not a code change
    - **Fallback mode** so the dashboard runs without an API key
    """)