"""
OpsLens - synthetic customer-issue dataset generator.

WHY SYNTHETIC
-------------
This project needs structured operational fields (resolution time, SLA, repeat
contacts, segment) and unstructured free text in the SAME row. Public support
datasets give you one or the other, not both. Rather than stitch two unrelated
sources together, the data-generating process is written explicitly and
published here, so the analysis can be checked against known ground truth.

DESIGN NOTES
------------
The dataset deliberately contains COMPETING signals, so that naive analysis
lands on the wrong answer:
  * one product area dominates raw ticket volume and absolute SLA breaches
  * one product area has the slowest resolution times for reasons outside
    the product team's control
  * one contact channel looks much worse than the others, but the effect is
    largely confounded by which customer segment uses that channel
Only rate-based analysis, controlled for segment mix, prioritises correctly.

Repeat contacts are generated as REAL linked follow-up tickets (same user,
same product area, within a few days) rather than as a random flag. This means
repeat_contact is re-derivable in SQL with a window function.

Usage:
    python generate_data.py
Outputs:
    data/tickets.csv
    data/GROUND_TRUTH.md   <- do not read until analysis is finished
"""

import os
import random
from datetime import datetime, timedelta

import numpy as np
import pandas as pd

# ----------------------------------------------------------------------
# CONFIG
# ----------------------------------------------------------------------
SEED = 7
N_PRIMARY = 28_000          # first-contact tickets; follow-ups added on top
START_DATE = datetime(2026, 3, 1)
END_DATE = datetime(2026, 8, 31)
REGRESSION_DATE = datetime(2026, 6, 15)   # a release degrades one workflow
REPEAT_WINDOW_DAYS = 7                    # definition of a repeat contact

SLA_TARGET_HOURS = {"P1": 8, "P2": 24, "P3": 48, "P4": 72}

DATA_DIR = "data"
OUT_PATH = os.path.join(DATA_DIR, "tickets.csv")
TRUTH_PATH = os.path.join(DATA_DIR, "GROUND_TRUTH.md")

random.seed(SEED)
np.random.seed(SEED)
rng = np.random.default_rng(SEED)

# ----------------------------------------------------------------------
# DIMENSIONS
# ----------------------------------------------------------------------
PRODUCT_AREAS = {
    "Payments": 0.27,
    "Orders": 0.20,
    "Delivery": 0.17,
    "Account": 0.14,
    "Refunds": 0.13,
    "Subscription": 0.09,
}

ISSUE_TYPES = {
    "Payments": ["payment_failed", "card_declined", "double_charge", "checkout_error"],
    "Orders": ["order_missing_item", "order_cancel_request", "order_modify", "wrong_item"],
    "Delivery": ["delivery_delayed", "delivery_lost", "address_change", "courier_issue"],
    "Account": ["login_issue", "password_reset", "profile_update", "verification_failed"],
    "Refunds": ["refund_not_received", "refund_status_unclear", "partial_refund", "refund_rejected"],
    "Subscription": ["billing_cycle", "plan_change", "cancel_subscription", "auto_renew_issue"],
}

ISSUE_WEIGHTS = {
    "Payments": [0.36, 0.28, 0.14, 0.22],
    "Orders": [0.30, 0.26, 0.22, 0.22],
    "Delivery": [0.44, 0.16, 0.20, 0.20],
    "Account": [0.35, 0.28, 0.17, 0.20],
    "Refunds": [0.34, 0.31, 0.19, 0.16],
    "Subscription": [0.28, 0.26, 0.26, 0.20],
}

SEGMENTS = {"Consumer": 0.55, "SMB": 0.28, "Enterprise": 0.10, "VIP": 0.07}
REGIONS = {"EU": 0.34, "NA": 0.30, "APAC": 0.22, "LATAM": 0.14}

# Channel mix depends on segment. This is what confounds the naive
# "email is our worst channel" conclusion.
CHANNEL_BY_SEGMENT = {
    "Consumer":   {"email": 0.30, "chat": 0.34, "in_app": 0.28, "phone": 0.08},
    "SMB":        {"email": 0.46, "chat": 0.23, "in_app": 0.22, "phone": 0.09},
    "Enterprise": {"email": 0.25, "chat": 0.20, "in_app": 0.10, "phone": 0.45},
    "VIP":        {"email": 0.20, "chat": 0.25, "in_app": 0.15, "phone": 0.40},
}

# ----------------------------------------------------------------------
# EFFECT SIZES  (resolution-time multiplier, additive repeat-contact prob)
# ----------------------------------------------------------------------
AREA_BASE = {
    "Payments":     (1.00, 0.15),
    "Orders":       (0.95, 0.13),
    "Delivery":     (1.75, 0.16),   # slow, but not a repeat-contact problem
    "Account":      (0.70, 0.11),
    "Refunds":      (1.45, 0.21),
    "Subscription": (0.85, 0.12),
}

ISSUE_MODIFIER = {
    "refund_not_received":   (1.70, 0.14),
    "refund_status_unclear": (1.55, 0.17),
    "delivery_lost":         (1.45, 0.07),
    "delivery_delayed":      (1.20, 0.02),
    "double_charge":         (1.30, 0.06),
    "verification_failed":   (1.25, 0.05),
    "payment_failed":        (1.00, 0.02),
}

SEGMENT_MODIFIER = {
    "Consumer":   (1.00,  0.00),
    "SMB":        (1.30,  0.07),
    "Enterprise": (0.80, -0.02),
    "VIP":        (0.65, -0.04),
}

# Deliberately small: the raw channel gap is mostly segment composition.
CHANNEL_MODIFIER = {
    "email":  (1.08,  0.02),
    "chat":   (0.95,  0.00),
    "in_app": (1.02,  0.01),
    "phone":  (0.97, -0.01),
}

REGION_MODIFIER = {
    "EU":    (1.00, 0.00),
    "NA":    (0.95, 0.00),
    "APAC":  (1.12, 0.02),
    "LATAM": (1.05, 0.01),
}

REGRESSION_ISSUES = ("refund_not_received", "refund_status_unclear")
REGRESSION_RES_MULT = 1.40
REGRESSION_REPEAT_ADD = 0.13

# ----------------------------------------------------------------------
# TEXT
# ----------------------------------------------------------------------
TEXT_TEMPLATES = {
    "refund_not_received": [
        "Returned my order on {d} and still no refund. It has been {n} days now.",
        "This is my {ord} time asking about the refund for {oid}. Nobody can tell me when I get my money back.",
        "Refund was approved but nothing has arrived in my account.",
        "Still not credited after {n} days. I am out of pocket and nobody will give me a date.",
        "I was told 5-7 working days for the refund on {oid}. We are well past that.",
        "no refund yet. {n} days. please sort this",
    ],
    "refund_status_unclear": [
        "The refund page still says processing. It has said processing for {n} days.",
        "There is no way to see the status of my refund anywhere in the app.",
        "Contacting again because I have had no update at all. The status never changes.",
        "Can someone confirm the refund for {oid} was actually issued? I see no confirmation anywhere.",
        "I keep having to chase this. There is zero visibility on where my refund is.",
        "why does it just say pending, pending, pending. give me a date",
        "Asked twice already. Still cannot tell if this has been processed or not.",
    ],
    "partial_refund": [
        "I only received part of my refund for {oid}. The rest is still outstanding.",
        "Refund amount is wrong, the delivery fee was not returned.",
        "Got a partial refund with no explanation of why the rest was withheld.",
    ],
    "refund_rejected": [
        "My refund request was rejected and I do not understand the reason given.",
        "Refund declined for {oid} but the item was clearly faulty on arrival.",
    ],
    "payment_failed": [
        "Payment failed at checkout for {oid}, I tried three different cards.",
        "Cannot complete payment, it errors out every single time.",
        "Transaction keeps failing but my bank says nothing is being blocked.",
        "payment wont go through",
    ],
    "card_declined": [
        "My card was declined but there are funds available.",
        "Card keeps getting declined on your site, works everywhere else.",
    ],
    "double_charge": [
        "I was charged twice for {oid}, please reverse one of them.",
        "Two identical charges on my statement for the same purchase.",
    ],
    "checkout_error": [
        "Checkout throws an error when I apply a discount code.",
        "The checkout page freezes after I enter my address.",
    ],
    "order_missing_item": [
        "Order {oid} arrived with one item missing.",
        "Package was short by two items compared to the invoice.",
    ],
    "order_cancel_request": [
        "I need to cancel {oid} before it ships.",
        "Please cancel my order, I selected the wrong size.",
    ],
    "order_modify": [
        "Can I change the quantity on {oid}?",
        "Need to update the items on my order before dispatch.",
    ],
    "wrong_item": [
        "Received the wrong item in {oid}.",
        "The product delivered does not match what I ordered.",
    ],
    "delivery_delayed": [
        "Delivery for {oid} is {n} days late with no explanation.",
        "Tracking has not updated since it left the warehouse.",
        "courier still hasnt turned up",
    ],
    "delivery_lost": [
        "Courier marked {oid} as delivered but nothing arrived.",
        "Parcel appears to be lost in transit.",
    ],
    "address_change": [
        "I need to change the delivery address for {oid}.",
        "Moved house, need the shipment redirected.",
    ],
    "courier_issue": [
        "Courier left the package outside in the rain.",
        "Driver did not attempt delivery but marked it as failed.",
    ],
    "login_issue": [
        "Cannot log in, it keeps saying invalid credentials.",
        "Locked out of my account after the app update.",
        "cant login",
    ],
    "password_reset": [
        "Password reset email never arrives.",
        "The reset link says expired immediately.",
    ],
    "profile_update": [
        "Cannot update my billing address in settings.",
        "Phone number will not save on my profile.",
    ],
    "verification_failed": [
        "Identity verification keeps failing with a valid document.",
        "Verification has been stuck in review for {n} days.",
    ],
    "billing_cycle": [
        "I was billed on the wrong date this month.",
        "Billing cycle changed without any notice.",
    ],
    "plan_change": [
        "Want to downgrade my plan but the option is greyed out.",
        "Upgraded but I am still on the old plan limits.",
    ],
    "cancel_subscription": [
        "Trying to cancel my subscription and cannot find how.",
        "Cancelled last month but was charged again.",
    ],
    "auto_renew_issue": [
        "Auto renew charged me despite being turned off.",
        "Renewal happened early without warning.",
    ],
}

FOLLOWUP_PREFIX = [
    "Following up again. ",
    "Still waiting. ",
    "No response to my last message. ",
    "Chasing this again. ",
    "",
    "",
]

ORDINALS = ["second", "third", "fourth"]
TYPO_MAP = {"the": "teh", "refund": "refnd", "please": "pls", "because": "becuase"}


def make_text(issue_type: str, is_followup: bool, days_open: float) -> str:
    """Build a customer message. Follow-ups carry escalation language."""
    template = random.choice(TEXT_TEMPLATES[issue_type])
    text = template.format(
        d=(START_DATE + timedelta(days=random.randint(0, 150))).strftime("%d %b"),
        n=max(2, int(days_open) + random.randint(0, 8)),
        ord=random.choice(ORDINALS),
        oid=f"ORD-{random.randint(100000, 999999)}",
    )
    if is_followup:
        text = random.choice(FOLLOWUP_PREFIX) + text
    if random.random() < 0.04:
        for good, bad in TYPO_MAP.items():
            text = text.replace(good, bad, 1)
    if random.random() < 0.03:
        text = text.upper()
    return text


# ----------------------------------------------------------------------
# HELPERS
# ----------------------------------------------------------------------
def weighted_pick(mapping: dict) -> str:
    keys = list(mapping.keys())
    return random.choices(keys, weights=[mapping[k] for k in keys], k=1)[0]


def sample_created_at() -> datetime:
    """Volume grows ~25% over the period; weekdays and daytime are busier."""
    total_days = (END_DATE - START_DATE).days
    while True:
        day = int(rng.integers(0, total_days))
        growth = 1.0 + 0.25 * (day / total_days)
        date = START_DATE + timedelta(days=day)
        weekday_factor = 0.55 if date.weekday() >= 5 else 1.0
        if random.random() < (growth / 1.25) * weekday_factor:
            break
    hour = int(np.clip(rng.normal(13, 4), 0, 23))
    return date + timedelta(hours=hour, minutes=int(rng.integers(0, 60)))


def pick_priority(issue: str, segment: str) -> str:
    weights = {"P1": 0.06, "P2": 0.22, "P3": 0.44, "P4": 0.28}
    if issue in ("double_charge", "payment_failed", "delivery_lost"):
        weights = {"P1": 0.18, "P2": 0.38, "P3": 0.31, "P4": 0.13}
    if segment in ("Enterprise", "VIP"):
        weights = {"P1": weights["P1"] + 0.10, "P2": weights["P2"] + 0.08,
                   "P3": weights["P3"] - 0.10, "P4": weights["P4"] - 0.08}
    keys = list(weights.keys())
    return random.choices(keys, weights=[max(0.01, weights[k]) for k in keys], k=1)[0]


def effects(area, issue, segment, channel, region, created_at):
    """Combine all multiplicative / additive effects for one ticket."""
    res_mult, repeat_p = AREA_BASE[area]
    for mod in (ISSUE_MODIFIER.get(issue, (1.0, 0.0)),
                SEGMENT_MODIFIER[segment],
                CHANNEL_MODIFIER[channel],
                REGION_MODIFIER[region]):
        res_mult *= mod[0]
        repeat_p += mod[1]
    if created_at >= REGRESSION_DATE and issue in REGRESSION_ISSUES:
        res_mult *= REGRESSION_RES_MULT
        repeat_p += REGRESSION_REPEAT_ADD
    return res_mult, repeat_p


def build_ticket(created_at, area, issue, segment, region, channel,
                 user_id, is_followup):
    """Produce one ticket row."""
    priority = pick_priority(issue, segment)
    res_mult, _ = effects(area, issue, segment, channel, region, created_at)

    if priority == "P1":
        res_mult *= 0.45
    elif priority == "P2":
        res_mult *= 0.70
    elif priority == "P4":
        res_mult *= 1.25
    if is_followup:
        res_mult *= 0.60          # follow-ups are handled faster, not better

    base_hours = float(rng.lognormal(mean=2.20, sigma=0.80))
    resolution_hours = round(min(base_hours * res_mult, 720.0), 2)

    age_days = (END_DATE - created_at).days
    open_prob = 0.04 + (0.22 if resolution_hours > 96 else 0.0)
    if age_days < 7:
        open_prob += 0.45
    if random.random() < min(open_prob, 0.60):
        status = random.choices(["open", "pending"], weights=[0.6, 0.4])[0]
        resolved_at, resolution_out = None, None
    else:
        status = "resolved"
        resolved_at = created_at + timedelta(hours=resolution_hours)
        resolution_out = resolution_hours

    sla_breached = int(resolution_hours > SLA_TARGET_HOURS[priority])

    if status == "resolved" and random.random() < 0.60:
        score = 4.4
        score -= 1.15 if is_followup else 0.0
        score -= 0.80 if sla_breached else 0.0
        score -= 0.45 if resolution_hours > 120 else 0.0
        score += float(rng.normal(0, 0.75))
        csat = int(np.clip(round(score), 1, 5))
    else:
        csat = None

    return {
        "user_id": user_id,
        "created_at": created_at,
        "resolved_at": resolved_at,
        "product_area": area,
        "issue_type": issue,
        "priority": priority,
        "status": status,
        "customer_text": make_text(issue, is_followup, (resolution_hours or 96) / 24),
        "user_segment": segment,
        "region": region,
        "channel": channel,
        "repeat_contact": int(is_followup),
        "sla_breached": sla_breached,
        "resolution_hours": resolution_out,
        "csat": csat,
    }


# ----------------------------------------------------------------------
# BUILD
# ----------------------------------------------------------------------
def generate() -> pd.DataFrame:
    rows = []
    for _ in range(N_PRIMARY):
        created_at = sample_created_at()
        area = weighted_pick(PRODUCT_AREAS)
        issue = random.choices(ISSUE_TYPES[area], weights=ISSUE_WEIGHTS[area], k=1)[0]
        segment = weighted_pick(SEGMENTS)
        region = weighted_pick(REGIONS)
        channel = weighted_pick(CHANNEL_BY_SEGMENT[segment])
        user_id = f"U-{int(rng.integers(1, 16000)):05d}"

        rows.append(build_ticket(created_at, area, issue, segment, region,
                                 channel, user_id, is_followup=False))

        # ---- linked follow-up contacts (this IS the repeat-contact signal)
        _, repeat_p = effects(area, issue, segment, channel, region, created_at)
        repeat_p = min(max(repeat_p, 0.02), 0.80)
        n_followups = 0
        if random.random() < repeat_p:
            n_followups = 1 + int(random.random() < repeat_p * 0.45)

        last_at = created_at
        for _ in range(n_followups):
            gap = timedelta(days=float(rng.uniform(1.0, REPEAT_WINDOW_DAYS)))
            follow_at = last_at + gap
            if follow_at > END_DATE:
                break
            last_at = follow_at
            rows.append(build_ticket(follow_at, area, issue, segment, region,
                                     channel, user_id, is_followup=True))

    df = pd.DataFrame(rows)
    df = df.sort_values("created_at").reset_index(drop=True)
    df.insert(0, "ticket_id", [f"TCK-{100000 + i}" for i in range(len(df))])
    return df


def add_realistic_mess(df: pd.DataFrame) -> pd.DataFrame:
    """Inject the kinds of defects that exist in every real operational export."""
    n = len(df)

    idx = df.sample(frac=0.03, random_state=SEED).index
    df.loc[idx, "region"] = df.loc[idx, "region"].str.lower()
    idx = df.sample(frac=0.01, random_state=SEED + 1).index
    df.loc[idx, "region"] = " " + df.loc[idx, "region"].astype(str) + " "

    idx = df.sample(frac=0.013, random_state=SEED + 2).index
    df.loc[idx, "user_segment"] = ""

    idx = df[df["resolution_hours"].notna()].sample(n=int(n * 0.003),
                                                   random_state=SEED + 3).index
    df.loc[idx, "resolution_hours"] = -df.loc[idx, "resolution_hours"]

    idx = df[df["csat"].notna()].sample(n=int(n * 0.004), random_state=SEED + 4).index
    df.loc[idx, "csat"] = 0

    df = pd.concat([df, df.sample(80, random_state=SEED + 5)], ignore_index=True)
    return df


def format_dates(df: pd.DataFrame) -> pd.DataFrame:
    """Most timestamps are standard; a minority use ISO-T format."""
    def fmt(series, iso_mask):
        out = series.dt.strftime("%Y-%m-%d %H:%M:%S")
        out[iso_mask] = series[iso_mask].dt.strftime("%Y-%m-%dT%H:%M:%S")
        return out.fillna("")

    iso_mask = pd.Series(rng.random(len(df)) < 0.006, index=df.index)
    df["created_at"] = fmt(pd.to_datetime(df["created_at"]), iso_mask)
    df["resolved_at"] = fmt(pd.to_datetime(df["resolved_at"]), iso_mask)
    return df


TRUTH_DOC = f"""# GROUND TRUTH - do not open until your analysis is complete

This file records the parameters used to generate `tickets.csv`. It exists so
the analysis can be scored against known truth. Reading it before analysing
defeats the purpose.

## Seeded relationships
- Regression date: {REGRESSION_DATE:%Y-%m-%d}, affecting issue types
  {", ".join(REGRESSION_ISSUES)} (resolution x{REGRESSION_RES_MULT},
  repeat-contact probability +{REGRESSION_REPEAT_ADD}).
- Segment effect: SMB resolution x{SEGMENT_MODIFIER['SMB'][0]},
  repeat +{SEGMENT_MODIFIER['SMB'][1]}.
- Channel effects are small by design; the raw channel gap is mostly
  explained by segment mix (see CHANNEL_BY_SEGMENT).
- Delivery has the highest area resolution multiplier
  ({AREA_BASE['Delivery'][0]}) but only a baseline repeat-contact rate -
  a slow process, not a self-service failure.
- Payments carries the largest raw ticket volume and the largest absolute
  count of SLA breaches.
- SLA breach is deterministic: resolution_hours > target for that priority
  ({SLA_TARGET_HOURS}).
- repeat_contact = 1 for tickets generated as linked follow-ups from the same
  user, same product area, within {REPEAT_WINDOW_DAYS} days.

## Injected data-quality defects
- ~1.3% blank `user_segment`
- ~4% inconsistent `region` casing / padding
- ~0.3% negative `resolution_hours`
- ~0.4% `csat` = 0 (valid range is 1-5)
- 80 exact duplicate rows
- ~0.6% timestamps in ISO-T format instead of space-separated
"""


def main() -> None:
    os.makedirs(DATA_DIR, exist_ok=True)
    df = generate()
    df = add_realistic_mess(df)
    df = format_dates(df)

    cols = ["ticket_id", "user_id", "created_at", "resolved_at", "product_area",
            "issue_type", "priority", "status", "customer_text", "user_segment",
            "region", "channel", "repeat_contact", "sla_breached",
            "resolution_hours", "csat"]
    df[cols].to_csv(OUT_PATH, index=False)

    with open(TRUTH_PATH, "w") as fh:
        fh.write(TRUTH_DOC)

    print(f"Wrote {len(df):,} rows to {OUT_PATH}")
    print(f"Wrote sealed answer key to {TRUTH_PATH}")


if __name__ == "__main__":
    main()
