"""AS Colour stock tracker - admin app (Streamlit).

Lets you manage the watchlist, the daily run time, the low-stock threshold and the pause switch,
see the latest stock, and start a run on demand. It talks to the same Supabase project as tracker.py.

Secrets needed (Streamlit "Secrets" settings, TOML):
    SUPABASE_URL, SUPABASE_KEY, APP_PASSWORD
Optional (enable the extra buttons):
    GITHUB_TOKEN, GITHUB_REPO ("owner/repo"), GITHUB_WORKFLOW ("tracker.yml"), GITHUB_BRANCH ("main")
    DISCORD_WEBHOOK_URL
"""
import hmac
import re
from datetime import datetime, timezone, time as dtime, timedelta
from urllib.parse import urlparse
from zoneinfo import ZoneInfo

import pandas as pd
import requests
import streamlit as st
from supabase import create_client

st.set_page_config(page_title="AS Colour Stock Tracker", page_icon="📦", layout="wide")

DEFAULT_SETTINGS = {
    "run_time": "09:00",
    "timezone": "Asia/Manila",
    "low_stock_threshold": "25",
    "paused": "false",
    "send_all_clear": "true",
}

TIMEZONES = [
    "Asia/Manila", "Australia/Sydney", "Australia/Melbourne", "Australia/Brisbane", "Australia/Adelaide",
    "Australia/Perth", "Pacific/Auckland", "Asia/Singapore", "Asia/Hong_Kong", "Asia/Tokyo",
    "Europe/London", "America/New_York", "America/Los_Angeles", "UTC",
]

CODE_RE = re.compile(r"^\d{3,5}[a-z]{0,2}$", re.I)         # a style code such as 5026 or 4001g
SLUG_CODE_RE = re.compile(r"-(\d{3,5}[a-z]{0,2})$", re.I)  # the style code at the end of a product link


# ---------------------------------------------------------------------------
# Small helpers (no Streamlit calls in here)
# ---------------------------------------------------------------------------

def to_int(value, default=None):
    try:
        return int(str(value).strip())
    except (TypeError, ValueError):
        return default


def truthy(value):
    return str(value).strip().lower() == "true"


def parse_run_time(value):
    try:
        hh, mm = str(value).strip().split(":")[:2]
        return dtime(int(hh), int(mm))
    except Exception:
        return dtime(9, 0)


def get_tz(name):
    try:
        return ZoneInfo((name or "UTC").strip())
    except Exception:
        return timezone.utc


def parse_entry(text):
    """One pasted item (an AS Colour product link, or a plain style code) -> a dict describing it."""
    raw = (text or "").strip().strip(",;")
    if not raw:
        return None
    if CODE_RE.match(raw):
        return {"input": raw, "ok": True, "style": raw.lower(), "slug": None, "url": None, "has_code": True}
    candidate = raw if "//" in raw else "https://" + raw
    try:
        parsed = urlparse(candidate)
    except ValueError:
        return {"input": raw, "ok": False, "why": "not a valid link"}
    host = (parsed.netloc or "").lower().split(":")[0]
    if not (host == "ascolour.com.au" or host.endswith(".ascolour.com.au")):
        return {"input": raw, "ok": False, "why": "not an AS Colour Australia link (it should be on ascolour.com.au)"}
    parts = [p for p in parsed.path.split("/") if p]
    if not parts:
        return {"input": raw, "ok": False, "why": "that link doesn't point to a product"}
    slug = parts[-1].lower()
    match = SLUG_CODE_RE.search(slug)
    return {"input": raw, "ok": True, "style": match.group(1).lower() if match else slug, "slug": slug,
            "url": f"https://www.ascolour.com.au/{slug}/", "has_code": bool(match)}


def parse_entries_with_repeats(text):
    """Split a pasted block into entries. Also returns the items that were pasted more than once."""
    entries, repeats, seen = [], [], set()
    for piece in re.split(r"[\s,;]+", (text or "").strip()):
        entry = parse_entry(piece)
        if not entry:
            continue
        key = entry.get("style") or entry["input"]
        if key in seen:
            repeats.append(entry["input"])
            continue
        seen.add(key)
        entries.append(entry)
    return entries, repeats


def parse_entries(text):
    return parse_entries_with_repeats(text)[0]


def norm_slug(name):
    """'Men's Organic Rib Singlet' -> 'mens-organic-rib-singlet' (how AS Colour writes it in a link)."""
    text = re.sub(r"['’`\"]", "", str(name or "").lower())
    return re.sub(r"[^a-z0-9]+", "-", text).strip("-")


def split_name(product_name, style_sku):
    """'Classic Tee | 5026' -> ('Classic Tee', '5026')"""
    name = (product_name or "").strip()
    if " | " in name:
        base, code = name.rsplit(" | ", 1)
        return base.strip(), code.strip()
    return name or str(style_sku), str(style_sku).upper()


def slug_of(url):
    path = urlparse(str(url or "")).path.strip("/")
    return path.split("/")[-1].lower() if path else None


def fmt_local(iso_value, tz):
    if not iso_value:
        return "—"
    try:
        return datetime.fromisoformat(str(iso_value)).astimezone(tz).strftime("%a %d %b, %H:%M")
    except Exception:
        return str(iso_value)


def next_run_text(settings, now_utc=None):
    now_utc = now_utc or datetime.now(timezone.utc)
    if truthy(settings.get("paused", "false")):
        return "Paused - scheduled runs are skipped until you switch it back on."
    tz = get_tz(settings.get("timezone"))
    now_local = now_utc.astimezone(tz)
    run_time = parse_run_time(settings.get("run_time"))
    today = now_local.date()
    if settings.get("last_run_date") == today.isoformat():
        day = today + timedelta(days=1)
    elif now_local.time() >= run_time:
        return "Due now - it will run at the next hourly check."
    else:
        day = today
    when = datetime.combine(day, run_time, tzinfo=tz)
    return f"{when:%a %d %b at %H:%M} ({tz}) - or shortly after, the check happens hourly."


# ---------------------------------------------------------------------------
# Secrets, login, database
# ---------------------------------------------------------------------------

def secret(name, default=None):
    try:
        value = st.secrets.get(name, default)
    except Exception:
        return default
    return value if value not in ("", None) else default


def require_login():
    expected = secret("APP_PASSWORD")
    if not expected:
        st.error("Set `APP_PASSWORD` in this app's secrets first (Streamlit → Settings → Secrets).")
        st.stop()
    if st.session_state.get("authed"):
        return
    st.title("📦 AS Colour Stock Tracker")
    with st.form("login"):
        password = st.text_input("Password", type="password")
        submitted = st.form_submit_button("Log in")
    if submitted:
        if hmac.compare_digest(password.encode(), str(expected).encode()):
            st.session_state["authed"] = True
            st.rerun()
        else:
            st.error("Wrong password.")
    st.stop()


@st.cache_resource
def get_db():
    return create_client(secret("SUPABASE_URL"), secret("SUPABASE_KEY"))


def fetch_all(table, columns, order, page=1000):
    """Read every row (Supabase returns at most 1000 per request)."""
    rows, start = [], 0
    while True:
        data = get_db().table(table).select(columns).order(order).range(start, start + page - 1).execute().data or []
        rows.extend(data)
        if len(data) < page:
            return rows
        start += page


def load_settings():
    settings = dict(DEFAULT_SETTINGS)
    for row in get_db().table("app_settings").select("key,value").execute().data or []:
        settings[row["key"]] = row.get("value")
    return settings


def save_settings(values):
    now = datetime.now(timezone.utc).isoformat()
    rows = [{"key": k, "value": str(v), "updated_at": now} for k, v in values.items()]
    get_db().table("app_settings").upsert(rows).execute()


INV_COLUMNS = ["variant_id", "style_sku", "product_name", "colour_name", "size_name", "stock_count", "updated_at"]
INV_COLUMNS_NEW = INV_COLUMNS + ["stock_status", "eta"]


def derive_status(stock, status):
    """Use the saved status when there is one, otherwise work it out from the stock number."""
    if isinstance(status, str) and status.strip():
        return status.strip()
    return "Out of stock" if int(stock or 0) == 0 else "In stock"


@st.cache_data(ttl=120, show_spinner="Loading stock…")
def load_inventory():
    try:
        rows = fetch_all("inventory_states", ",".join(INV_COLUMNS_NEW), "variant_id")
    except Exception:  # the status columns haven't been added yet (status_and_restock_setup.sql)
        rows = fetch_all("inventory_states", ",".join(INV_COLUMNS), "variant_id")
    df = pd.DataFrame(rows, columns=INV_COLUMNS_NEW)
    df["stock_count"] = pd.to_numeric(df["stock_count"], errors="coerce").fillna(0).astype(int)
    df["status"] = [derive_status(n, st_) for n, st_ in zip(df["stock_count"], df["stock_status"])]
    df["eta"] = df["eta"].fillna("")
    return df


EVENT_COLUMNS = ("event_type,variant_id,style_sku,product_name,colour_name,size_name,"
                 "previous_stock,previous_status,new_stock,new_status,eta,created_at")


def load_events(since_iso):
    """Low-stock and restock events since the given time, newest first. None if the table doesn't exist yet."""
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


def load_watchlist():
    try:
        rows = fetch_all("watchlist", "style_sku,product_id,enabled,url", "style_sku")
    except Exception:  # the url column hasn't been added yet (update_setup.sql)
        rows = fetch_all("watchlist", "style_sku,product_id,enabled", "style_sku")
    df = pd.DataFrame(rows, columns=["style_sku", "product_id", "enabled", "url"])
    df["product_id"] = pd.to_numeric(df["product_id"], errors="coerce").astype("Int64")
    df["enabled"] = df["enabled"].fillna(True).astype(bool)
    df["url"] = df["url"].astype(object).where(df["url"].notna(), None)
    return df


def flash(kind, message):
    st.session_state["flash"] = (kind, message)


def show_flash():
    item = st.session_state.pop("flash", None)
    if item:
        getattr(st, item[0])(item[1])


def dispatch_run():
    token, repo = secret("GITHUB_TOKEN"), secret("GITHUB_REPO")
    if not token or not repo:
        return False, "Add `GITHUB_TOKEN` and `GITHUB_REPO` to this app's secrets to enable the Run now button."
    workflow = secret("GITHUB_WORKFLOW", "tracker.yml")
    ref = secret("GITHUB_BRANCH", "main")
    try:
        response = requests.post(
            f"https://api.github.com/repos/{repo}/actions/workflows/{workflow}/dispatches",
            headers={
                "Authorization": f"Bearer {token}",
                "Accept": "application/vnd.github+json",
                "X-GitHub-Api-Version": "2022-11-28",
            },
            json={"ref": ref},
            timeout=30,
        )
    except Exception as e:
        return False, f"Couldn't reach GitHub: {e}"
    if response.status_code == 204:
        return True, "Run started. It takes about a minute - check Discord, then press Refresh."
    return False, f"GitHub answered {response.status_code}: {response.text[:200]}"


def send_test_discord():
    webhook = secret("DISCORD_WEBHOOK_URL")
    if not webhook:
        return False, "Add `DISCORD_WEBHOOK_URL` to this app's secrets to enable the test button."
    try:
        response = requests.post(webhook, json={"content": "✅ Test message from the AS Colour tracker admin app."}, timeout=30)
    except Exception as e:
        return False, f"Couldn't reach Discord: {e}"
    if response.status_code < 300:
        return True, "Test message sent - check your Discord channel."
    return False, f"Discord answered {response.status_code}."


# ---------------------------------------------------------------------------
# Tabs
# ---------------------------------------------------------------------------

CHANGE_WINDOWS = {"Last 24 hours": 1, "Last 3 days": 3, "Last 7 days": 7, "Last 30 days": 30}
LINK_COLUMN = {"Link": st.column_config.LinkColumn("Link", display_text="Open ↗")}


def style_links(wl):
    return {str(sku).lower(): url for sku, url in zip(wl["style_sku"], wl["url"]) if url}


def events_table(df, tz, links, kind):
    def was(stock, status):
        if pd.isna(stock):
            return "—"
        text = str(int(stock))
        return f"{text} ({str(status).lower()})" if kind == "restock" and isinstance(status, str) and status and status != "In stock" else text

    table = pd.DataFrame({
        "When": [fmt_local(v, tz) for v in df["created_at"]],
        "Product": df["product_name"],
        "Colour": df["colour_name"],
        "Size": df["size_name"],
        "Now": df["new_stock"],
        "Was": [was(n, s_) for n, s_ in zip(df["previous_stock"], df["previous_status"])],
    })
    if kind == "low":
        table["Status"] = df["new_status"]
        table["ETA"] = df["eta"].fillna("")
    table["Link"] = [links.get(str(s_).lower()) for s_ in df["style_sku"]]
    return table


def render_dashboard(settings, inv, wl):
    tz = get_tz(settings.get("timezone"))
    threshold = to_int(settings.get("low_stock_threshold"), 25)
    status = {"ok": "✅ OK", "failed": "❌ Failed"}.get(settings.get("last_run_status"), "—")

    window = st.selectbox("Show changes from the", list(CHANGE_WINDOWS), index=0)
    since = datetime.now(timezone.utc) - timedelta(days=CHANGE_WINDOWS[window])
    events = load_events(since.isoformat())
    lows = events[events["event_type"] == "low"] if events is not None else None
    restocks = events[events["event_type"] == "restock"] if events is not None else None

    c1, c2, c3, c4 = st.columns(4)
    c1.metric("Last run", fmt_local(settings.get("last_run_at"), tz))
    c2.metric("Last result", status)
    c3.metric(f"Fell below {threshold}", "—" if lows is None else f"{len(lows):,}")
    c4.metric(f"Restocked to {threshold}+", "—" if restocks is None else f"{len(restocks):,}")

    if settings.get("last_run_summary"):
        st.caption(f"Last run: {settings['last_run_summary']}")
    st.info(f"**Next scheduled run:** {next_run_text(settings)}")

    b1, b2, b3 = st.columns(3)
    if b1.button("▶️ Run now"):
        ok, message = dispatch_run()
        (st.success if ok else st.error)(message)
    if b2.button("🔔 Send test Discord message"):
        ok, message = send_test_discord()
        (st.success if ok else st.error)(message)
    if b3.button("🔄 Refresh data"):
        st.cache_data.clear()
        st.rerun()

    if events is None:
        st.warning("The change history isn't set up yet. Run `update_setup.sql` in the Supabase SQL editor, then run the tracker once. "
                   "From then on this page lists what fell below or was restocked.")
    else:
        links = style_links(load_watchlist())
        st.subheader(f"🔻 Fell below {threshold} - take off the website ({len(lows):,})")
        if lows.empty:
            st.success(f"Nothing fell below {threshold} in the {window.lower()}.")
        else:
            table = events_table(lows, tz, links, "low")
            st.dataframe(table, hide_index=True, column_config=LINK_COLUMN)
            st.download_button("⬇️ Download as CSV", table.drop(columns=["Link"]).to_csv(index=False).encode("utf-8"), "fell_below.csv", "text/csv", key="dl_low")

        st.subheader(f"🔺 Restocked to {threshold}+ - put back on the website ({len(restocks):,})")
        if restocks.empty:
            st.success(f"Nothing was restocked in the {window.lower()}.")
        else:
            table = events_table(restocks, tz, links, "restock")
            st.dataframe(table, hide_index=True, column_config=LINK_COLUMN)
            st.download_button("⬇️ Download as CSV", table.drop(columns=["Link"]).to_csv(index=False).encode("utf-8"), "restocked.csv", "text/csv", key="dl_restock")

    low_now = inv[inv["stock_count"] < threshold].sort_values(["stock_count", "style_sku", "variant_id"])
    with st.expander(f"Everything that is below {threshold} right now ({len(low_now):,}) - for reference"):
        st.caption("This is the full current list, including items that have been low for a long time. Alerts only cover changes.")
        if low_now.empty:
            st.write("Nothing is below the threshold.")
        else:
            st.dataframe(low_now[["style_sku", "product_name", "colour_name", "size_name", "stock_count", "status", "eta"]].rename(columns={
                "style_sku": "Style", "product_name": "Product", "colour_name": "Colour", "size_name": "Size",
                "stock_count": "Stock", "status": "Status", "eta": "ETA"}), hide_index=True)


def render_watchlist(inv):
    st.subheader("Watchlist")
    st.caption("The styles the tracker monitors. Paste AS Colour product links to add or remove them. "
               "A newly added style is picked up on the next run (that first run takes a few minutes).")

    wl = load_watchlist()

    names = {}   # style code (lower-case) -> product name, taken from the stock data
    for sku, name in zip(inv["style_sku"], inv["product_name"]):
        names.setdefault(str(sku).lower(), name)

    with st.form("add_styles", clear_on_submit=True):
        text = st.text_area("➕ Add products: paste one AS Colour link per line",
                            placeholder="https://www.ascolour.com.au/classic-tee-5026/\nhttps://www.ascolour.com.au/staple-tee-5001/", height=110)
        add_clicked = st.form_submit_button("Add to watchlist")

    if add_clicked:
        entries, repeats = parse_entries_with_repeats(text)
        status_of = {str(sku).lower(): bool(on) for sku, on in zip(wl["style_sku"], wl["enabled"])}   # style -> monitored?
        style_by_slug = {slug_of(u): str(sku).lower() for sku, u in zip(wl["style_sku"], wl["url"]) if u}
        style_by_name = {norm_slug(split_name(n, k)[0]): k for k, n in names.items() if k in status_of}

        def describe(style):
            if style in names:
                base, code = split_name(names[style], style)
                label = f"**{base}** (SKU {code})"
            else:
                label = f"SKU {style.upper()}"
            return label + ("" if status_of.get(style, True) else " - currently paused: tick **Monitor** in the table below to resume it")

        new_rows, dupes, bad = [], [], [e for e in entries if not e["ok"]]
        for e in [e for e in entries if e["ok"]]:
            match = None
            if e["style"] in status_of:
                match = e["style"]
            elif e["slug"] and e["slug"] in style_by_slug:
                match = style_by_slug[e["slug"]]
            elif e["slug"] and not e["has_code"] and e["slug"] in style_by_name:
                match = style_by_name[e["slug"]]
            if match:
                dupes.append(match)
            else:
                row = {"style_sku": e["style"], "enabled": True}
                if e["url"]:
                    row["url"] = e["url"]
                new_rows.append(row)

        for e in bad:
            st.error(f"Couldn't use `{e['input']}`: {e['why']}.")
        if dupes:
            st.warning("⚠️ **Already on the watchlist - not added again:**\n\n" + "\n".join(f"- {describe(d)}" for d in dict.fromkeys(dupes)))
        if repeats:
            st.warning("⚠️ You pasted these more than once (counted once): " + ", ".join(repeats))
        if new_rows:
            try:
                get_db().table("watchlist").upsert(new_rows).execute()
            except Exception:  # no url column yet: save the style codes only
                get_db().table("watchlist").upsert([{k: v for k, v in r.items() if k != "url"} for r in new_rows]).execute()
                st.warning("Saved, but links can't be stored yet. Run `update_setup.sql` in the Supabase SQL editor.")
            added = ", ".join(r["style_sku"] for r in new_rows)
            st.success(f"Added {len(new_rows)}: {added}. They'll be picked up on the next run.")
            wl = load_watchlist()
        elif not bad and not dupes and not repeats:
            st.warning("Paste at least one link first.")

    with st.form("remove_styles", clear_on_submit=True):
        rtext = st.text_area("➖ Remove products: paste the link (or style code)", height=80,
                             placeholder="https://www.ascolour.com.au/classic-tee-5026/")
        rconfirm = st.checkbox("Yes, remove them from the watchlist")
        remove_clicked = st.form_submit_button("Remove from watchlist")

    if remove_clicked:
        entries = parse_entries(rtext)
        for e in [e for e in entries if not e["ok"]]:
            st.error(f"Couldn't use `{e['input']}`: {e['why']}.")
        wanted_styles = {e["style"] for e in entries if e["ok"]}
        wanted_slugs = {e["slug"] for e in entries if e["ok"] and e["slug"]}
        matches = [sku for sku, url in zip(wl["style_sku"], wl["url"])
                   if str(sku).lower() in wanted_styles or (url and slug_of(url) in wanted_slugs)]
        if not entries:
            st.warning("Paste at least one link first.")
        elif not matches:
            st.info("None of those are on the watchlist.")
        elif not rconfirm:
            st.warning("Tick 'Yes, remove them' to confirm, then press the button again. Matches: " + ", ".join(matches))
        else:
            get_db().table("watchlist").delete().in_("style_sku", matches).execute()
            flash("success", "Removed: " + ", ".join(matches))
            st.rerun()

    enabled_count = int(wl["enabled"].sum()) if len(wl) else 0
    pending = int(wl["product_id"].isna().sum()) if len(wl) else 0
    st.write(f"**{enabled_count}** styles monitored out of **{len(wl)}** in the list"
             + (f" · **{pending}** waiting for their product ID" if pending else ""))

    if wl.empty:
        st.info("The watchlist is empty. Paste a link above to add a product.")
        return

    view = wl.copy()
    view["product"] = [names.get(str(sku).lower(), "") for sku in view["style_sku"]]
    view["status"] = view["product_id"].apply(lambda v: "✅ ready" if pd.notna(v) else "⏳ found on next run")
    view["remove"] = False
    search = st.text_input("Filter the list", placeholder="Type to filter, e.g. 5026 or maple", label_visibility="collapsed")
    if search.strip():
        q = search.strip()
        view = view[view["style_sku"].str.contains(q, case=False, na=False) | view["product"].str.contains(q, case=False, na=False)]
    view = view[["style_sku", "product", "enabled", "status", "url", "remove"]]

    edited = st.data_editor(
        view,
        key="watchlist_editor",
        hide_index=True,
        disabled=["style_sku", "product", "status", "url"],
        column_config={
            "style_sku": st.column_config.TextColumn("Style"),
            "product": st.column_config.TextColumn("Product"),
            "enabled": st.column_config.CheckboxColumn("Monitor", help="Untick to pause this style without deleting it"),
            "status": st.column_config.TextColumn("Status"),
            "url": st.column_config.LinkColumn("Link", display_text="Open ↗"),
            "remove": st.column_config.CheckboxColumn("Remove", help="Tick, then confirm and press Save"),
        },
    )

    original = dict(zip(wl["style_sku"], wl["enabled"]))
    to_remove = edited.loc[edited["remove"], "style_sku"].tolist()
    to_enable, to_disable = [], []
    for row in edited.itertuples():
        if row.style_sku in to_remove:
            continue
        was = original.get(row.style_sku, True)
        if bool(row.enabled) and not was:
            to_enable.append(row.style_sku)
        elif not bool(row.enabled) and was:
            to_disable.append(row.style_sku)

    confirm = False
    if to_remove:
        confirm = st.checkbox(f"Yes, remove {len(to_remove)} style(s) from the watchlist: {', '.join(to_remove)}")

    if st.button("💾 Save changes"):
        if not (to_remove or to_enable or to_disable):
            st.info("No changes to save.")
        elif to_remove and not confirm:
            st.warning("Tick the confirmation box to remove styles, then press Save again.")
        else:
            db = get_db()
            if to_enable:
                db.table("watchlist").update({"enabled": True}).in_("style_sku", to_enable).execute()
            if to_disable:
                db.table("watchlist").update({"enabled": False}).in_("style_sku", to_disable).execute()
            if to_remove:
                db.table("watchlist").delete().in_("style_sku", to_remove).execute()
            flash("success", f"Saved: {len(to_enable)} resumed, {len(to_disable)} paused, {len(to_remove)} removed.")
            st.rerun()

    with st.expander("🧹 Clean up old stock rows"):
        watched = {s_.lower() for s_ in wl.loc[wl["enabled"], "style_sku"]}
        stale = sorted(s_ for s_ in inv["style_sku"].dropna().unique() if s_.lower() not in watched)
        stale_rows = int(inv["style_sku"].isin(stale).sum())
        if not stale:
            st.write("Nothing to clean up - every stock row belongs to a monitored style.")
        else:
            st.write(f"{stale_rows:,} stock rows belong to styles that aren't being monitored: {', '.join(stale)}. "
                     "They no longer update and can be deleted.")
            if st.checkbox("Yes, delete those stock rows") and st.button("🗑️ Delete old stock rows"):
                get_db().table("inventory_states").delete().in_("style_sku", stale).execute()
                st.cache_data.clear()
                flash("success", f"Deleted {stale_rows:,} old stock rows.")
                st.rerun()


def render_schedule(settings):
    st.subheader("Schedule & alerts")
    st.caption("The tracker wakes up every hour and does the real work once a day, at the first check after the time you choose.")

    tz_options = list(TIMEZONES)
    current_tz = settings.get("timezone") or "Asia/Manila"
    if current_tz not in tz_options:
        tz_options.insert(0, current_tz)

    with st.form("schedule"):
        run_time = st.time_input("Run once a day at", value=parse_run_time(settings.get("run_time")), step=3600)
        tz_name = st.selectbox("Time zone", tz_options, index=tz_options.index(current_tz))
        threshold = st.number_input("Low-stock threshold (alert when a size/colour drops below this)",
                                    min_value=0, max_value=100000, step=5, value=to_int(settings.get("low_stock_threshold"), 25))
        paused = st.toggle("Pause the scheduled runs", value=truthy(settings.get("paused", "false")))
        all_clear = st.toggle("Post an 'all clear' message when nothing is low", value=truthy(settings.get("send_all_clear", "true")))
        saved = st.form_submit_button("💾 Save settings")

    if saved:
        save_settings({
            "run_time": run_time.strftime("%H:%M"),
            "timezone": tz_name,
            "low_stock_threshold": int(threshold),
            "paused": "true" if paused else "false",
            "send_all_clear": "true" if all_clear else "false",
        })
        flash("success", "Settings saved. They apply from the next hourly check.")
        st.rerun()

    st.info(f"**Next scheduled run:** {next_run_text(settings)}")
    st.caption("Tips: if you pick a time that has already passed today and the tracker hasn't run yet, it runs at the next hourly check. "
               "A failed run is retried at each hourly check, up to 3 times a day. 'Run now' on the Dashboard ignores the schedule and the pause switch.")


def render_stock(inv, threshold):
    st.subheader("Stock browser")
    if inv.empty:
        st.info("No stock data yet - run the tracker first.")
        return

    c1, c2, c3 = st.columns(3)
    styles = c1.multiselect("Style", sorted(inv["style_sku"].dropna().unique()))
    colours = c2.multiselect("Colour", sorted(inv["colour_name"].dropna().unique()))
    sizes = c3.multiselect("Size", sorted(inv["size_name"].dropna().unique()))
    d1, d2 = st.columns([3, 1])
    query = d1.text_input("Search", placeholder="Search product name or SKU")
    only_low = d2.checkbox(f"Only below {threshold}")

    df = inv
    if styles:
        df = df[df["style_sku"].isin(styles)]
    if colours:
        df = df[df["colour_name"].isin(colours)]
    if sizes:
        df = df[df["size_name"].isin(sizes)]
    if only_low:
        df = df[df["stock_count"] < threshold]
    if query.strip():
        q = query.strip()
        df = df[df["product_name"].str.contains(q, case=False, na=False) | df["variant_id"].str.contains(q, case=False, na=False)]

    st.caption(f"{len(df):,} variants shown")
    table = df[["variant_id", "product_name", "colour_name", "size_name", "stock_count", "status", "eta"]].rename(columns={
        "variant_id": "SKU", "product_name": "Product", "colour_name": "Colour", "size_name": "Size",
        "stock_count": "Stock", "status": "Status", "eta": "ETA",
    })
    st.dataframe(table, hide_index=True)
    st.download_button("⬇️ Download as CSV", table.to_csv(index=False).encode("utf-8"), "stock.csv", "text/csv")


# ---------------------------------------------------------------------------
# Page
# ---------------------------------------------------------------------------

def main():
    require_login()

    with st.sidebar:
        st.write("📦 AS Colour Stock Tracker")
        if st.button("Log out"):
            st.session_state["authed"] = False
            st.rerun()

    try:
        settings = load_settings()
    except Exception as e:
        st.error("Couldn't read the `app_settings` table in Supabase. Run `app_settings_setup.sql` in the Supabase SQL editor "
                 f"(and make sure RLS is off). Details: {e}")
        st.stop()

    try:
        inv = load_inventory()
    except Exception as e:
        st.error(f"Couldn't read the `inventory_states` table: {e}")
        st.stop()

    threshold = to_int(settings.get("low_stock_threshold"), 25)

    st.title("📦 AS Colour Stock Tracker")
    show_flash()
    tab_dash, tab_watch, tab_sched, tab_stock = st.tabs(["📊 Dashboard", "📋 Watchlist", "⏰ Schedule & alerts", "📦 Stock browser"])
    with tab_dash:
        render_dashboard(settings, inv, load_watchlist())
    with tab_watch:
        render_watchlist(inv)
    with tab_sched:
        render_schedule(settings)
    with tab_stock:
        render_stock(inv, threshold)


main()
