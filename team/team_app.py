"""AS Colour stock tracker - team app (READ ONLY).

A simple page your team can open to see what fell below the threshold, what was restocked, and
the current stock of every monitored product. It only reads data: there is nothing here that can
change the watchlist, the schedule or the stock.

Secrets needed (Streamlit "Secrets" settings, TOML):
    SUPABASE_URL, SUPABASE_KEY, TEAM_PASSWORD
"""
import hmac
from datetime import datetime, timezone, timedelta
from zoneinfo import ZoneInfo

import pandas as pd
import streamlit as st
from supabase import create_client

st.set_page_config(page_title="AS Colour Stock", page_icon="📦", layout="wide")

DEFAULT_SETTINGS = {"timezone": "Asia/Manila", "low_stock_threshold": "25"}
CHANGE_WINDOWS = {"Last 24 hours": 1, "Last 3 days": 3, "Last 7 days": 7}
LINK_COLUMN = {"Link": st.column_config.LinkColumn("Link", display_text="Open ↗")}
INV_COLUMNS = ["variant_id", "style_sku", "product_name", "colour_name", "size_name", "stock_count", "updated_at"]
INV_COLUMNS_NEW = INV_COLUMNS + ["stock_status"]
EVENT_COLUMNS = "event_type,variant_id,style_sku,product_name,colour_name,size_name,new_stock,new_status,created_at"


# ---------------------------------------------------------------------------
# Small helpers
# ---------------------------------------------------------------------------

def to_int(value, default=None):
    try:
        return int(str(value).strip())
    except (TypeError, ValueError):
        return default


def get_tz(name):
    try:
        return ZoneInfo((name or "UTC").strip())
    except Exception:
        return timezone.utc


def fmt_local(iso_value, tz):
    if not iso_value:
        return "—"
    try:
        return datetime.fromisoformat(str(iso_value)).astimezone(tz).strftime("%a %d %b, %H:%M")
    except Exception:
        return str(iso_value)


def split_name(product_name, style_sku):
    """'Classic Tee | 5026' -> ('Classic Tee', '5026')"""
    name = (product_name or "").strip()
    if " | " in name:
        base, code = name.rsplit(" | ", 1)
        return base.strip(), code.strip()
    return name or str(style_sku), str(style_sku).upper()


def secret(name, default=None):
    try:
        value = st.secrets.get(name, default)
    except Exception:
        return default
    return value if value not in ("", None) else default


def require_login():
    expected = secret("TEAM_PASSWORD")
    if not expected:
        st.error("Set `TEAM_PASSWORD` in this app's secrets first (Streamlit → Settings → Secrets).")
        st.stop()
    if st.session_state.get("authed"):
        return
    st.title("📦 AS Colour Stock")
    with st.form("login"):
        password = st.text_input("Password", type="password")
        submitted = st.form_submit_button("Open")
    if submitted:
        if hmac.compare_digest(password.encode(), str(expected).encode()):
            st.session_state["authed"] = True
            st.rerun()
        else:
            st.error("Wrong password.")
    st.stop()


# ---------------------------------------------------------------------------
# Data (everything here only reads)
# ---------------------------------------------------------------------------

@st.cache_resource
def get_db():
    return create_client(secret("SUPABASE_URL"), secret("SUPABASE_KEY"))


def fetch_all(table, columns, order, page=1000):
    rows, start = [], 0
    while True:
        data = get_db().table(table).select(columns).order(order).range(start, start + page - 1).execute().data or []
        rows.extend(data)
        if len(data) < page:
            return rows
        start += page


def load_settings():
    settings = dict(DEFAULT_SETTINGS)
    try:
        for row in get_db().table("app_settings").select("key,value").execute().data or []:
            settings[row["key"]] = row.get("value")
    except Exception:
        pass
    return settings


@st.cache_data(ttl=120, show_spinner="Loading stock…")
def load_inventory():
    try:
        rows = fetch_all("inventory_states", ",".join(INV_COLUMNS_NEW), "variant_id")
    except Exception:  # the status column hasn't been added yet
        rows = fetch_all("inventory_states", ",".join(INV_COLUMNS), "variant_id")
    df = pd.DataFrame(rows, columns=INV_COLUMNS_NEW)
    df["stock_count"] = pd.to_numeric(df["stock_count"], errors="coerce").fillna(0).astype(int)
    df["status"] = [
        s_.strip() if isinstance(s_, str) and s_.strip() else ("Out of stock" if n == 0 else "In stock")
        for n, s_ in zip(df["stock_count"], df["stock_status"])
    ]
    return df


@st.cache_data(ttl=120, show_spinner=False)
def load_links():
    try:
        rows = fetch_all("watchlist", "style_sku,url", "style_sku")
    except Exception:
        return {}
    return {str(r["style_sku"]).lower(): r["url"] for r in rows if r.get("url")}


def load_events(since_iso):
    """Events since the given time, newest first. None if the history table doesn't exist yet."""
    rows, start = [], 0
    try:
        while True:
            data = (get_db().table("stock_events").select(EVENT_COLUMNS).gte("created_at", since_iso)
                    .order("created_at", desc=True).range(start, start + 999).execute().data or [])
            rows.extend(data)
            if len(data) < 1000:
                break
            start += 1000
    except Exception:
        return None
    return pd.DataFrame(rows, columns=EVENT_COLUMNS.split(","))


# ---------------------------------------------------------------------------
# Tabs
# ---------------------------------------------------------------------------

def changes_table(df, tz, links):
    names = [split_name(n, s_) for n, s_ in zip(df["product_name"], df["style_sku"])]
    return pd.DataFrame({
        "When": [fmt_local(v, tz) for v in df["created_at"]],
        "Product": [n[0] for n in names],
        "SKU": [n[1] for n in names],
        "Colour": df["colour_name"],
        "Size": df["size_name"],
        "Stock": ["no data" if s_ == "No stock data" else str(n) for n, s_ in zip(df["new_stock"], df["new_status"])],
        "Link": [links.get(str(s_).lower()) for s_ in df["style_sku"]],
    })


def render_changes(tz, threshold, links):
    window = st.selectbox("Show changes from the", list(CHANGE_WINDOWS), index=0)
    since = datetime.now(timezone.utc) - timedelta(days=CHANGE_WINDOWS[window])
    events = load_events(since.isoformat())
    if events is None:
        st.info("No change history yet. It appears after the tracker's next run.")
        return

    lows = events[events["event_type"] == "low"]
    restocks = events[events["event_type"] == "restock"]

    st.subheader(f"🔻 Fell below {threshold} ({len(lows):,})")
    if lows.empty:
        st.success(f"Nothing fell below {threshold} in the {window.lower()}.")
    else:
        st.dataframe(changes_table(lows, tz, links), hide_index=True, column_config=LINK_COLUMN)

    st.subheader(f"🔺 Restocked to {threshold}+ ({len(restocks):,})")
    if restocks.empty:
        st.success(f"Nothing was restocked in the {window.lower()}.")
    else:
        st.dataframe(changes_table(restocks, tz, links), hide_index=True, column_config=LINK_COLUMN)


def render_lookup(inv, threshold):
    if inv.empty:
        st.info("No stock data yet.")
        return

    query = st.text_input("Search", placeholder="Product name or SKU, e.g. maple or 4001")
    c1, c2 = st.columns(2)
    colours = c1.multiselect("Colour", sorted(inv["colour_name"].dropna().unique()))
    sizes = c2.multiselect("Size", sorted(inv["size_name"].dropna().unique()))
    only_low = st.checkbox(f"Only show items below {threshold}")

    df = inv
    if query.strip():
        q = query.strip()
        df = df[df["product_name"].str.contains(q, case=False, na=False) | df["variant_id"].str.contains(q, case=False, na=False)]
    if colours:
        df = df[df["colour_name"].isin(colours)]
    if sizes:
        df = df[df["size_name"].isin(sizes)]
    if only_low:
        df = df[df["stock_count"] < threshold]

    st.caption(f"{len(df):,} items shown")
    names = [split_name(n, s_) for n, s_ in zip(df["product_name"], df["style_sku"])]
    table = pd.DataFrame({
        "Product": [n[0] for n in names],
        "SKU": [n[1] for n in names],
        "Colour": df["colour_name"].to_numpy(),
        "Size": df["size_name"].to_numpy(),
        "Stock": df["stock_count"].to_numpy(),
        "Status": df["status"].to_numpy(),
    }).sort_values(["Product", "Colour", "Size"])
    st.dataframe(table, hide_index=True)


def render_products(inv, threshold, links):
    if inv.empty:
        st.info("No stock data yet.")
        return
    rows = []
    for style, group in inv.groupby("style_sku"):
        base, code = split_name(group["product_name"].iloc[0], style)
        low = int((group["stock_count"] < threshold).sum())
        rows.append({"Product": base, "SKU": code, f"Below {threshold}": f"{low} of {len(group)}", "Link": links.get(str(style).lower())})
    table = pd.DataFrame(rows).sort_values("Product")
    st.caption(f"{len(table)} products are monitored.")
    st.dataframe(table, hide_index=True, column_config=LINK_COLUMN)


# ---------------------------------------------------------------------------
# Page
# ---------------------------------------------------------------------------

def main():
    require_login()

    try:
        settings = load_settings()
        inv = load_inventory()
        links = load_links()
    except Exception as e:
        st.error(f"Couldn't load the stock data. Please tell the admin. Details: {e}")
        st.stop()

    tz = get_tz(settings.get("timezone"))
    threshold = to_int(settings.get("low_stock_threshold"), 25)

    st.title("📦 AS Colour Stock")
    st.caption(f"Last updated: {fmt_local(settings.get('last_run_at'), tz)} · low means below {threshold} · this page is read-only")
    if st.button("🔄 Refresh"):
        st.cache_data.clear()
        st.rerun()

    tab_changes, tab_lookup, tab_products = st.tabs(["🔔 Changes", "🔎 Stock lookup", "📋 Products"])
    with tab_changes:
        render_changes(tz, threshold, links)
    with tab_lookup:
        render_lookup(inv, threshold)
    with tab_products:
        render_products(inv, threshold, links)


main()
