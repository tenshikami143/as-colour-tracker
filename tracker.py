import os
import sys
import time
import traceback
import random
import json
import requests
from datetime import datetime, timedelta, timezone, time as dtime
from urllib.parse import urlparse
from zoneinfo import ZoneInfo
from supabase import create_client, Client

# Initialize Supabase credentials
SUPABASE_URL = os.environ.get("SUPABASE_URL")
SUPABASE_KEY = os.environ.get("SUPABASE_KEY")
supabase: Client = None  # created in main() so a missing secret is reported to Discord instead of crashing silently

# AS Colour's live stock API (the same one the bulk order grid on the website uses). No login or token needed.
STOCK_API_URL = "https://u43tbzpa7g.execute-api.ap-southeast-2.amazonaws.com/production//catalog/products"
STOCK_API_HEADERS = {
    "Accept": "application/json",
    "Origin": "https://www.ascolour.com.au",
    "Referer": "https://www.ascolour.com.au/",
    "Store-Hash": "lqiq2tqil5",  # the header the site's csrf-protection script adds; without it the API returns []
    "Cache-Control": "no-cache",
    "Pragma": "no-cache",
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Safari/537.36",
}

# Extra headers for the stock API as JSON, e.g. {"Store-Hash": "newvalue"} (only needed if the site changes them)
_extra_headers = os.environ.get("STOCK_API_EXTRA_HEADERS")
if _extra_headers:
    try:
        STOCK_API_HEADERS.update(json.loads(_extra_headers))
    except ValueError:
        print("STOCK_API_EXTRA_HEADERS is not valid JSON - ignoring it.")

STOCK_BATCH_SIZE = 10                                   # how many product IDs to request per call
SCAN_MAX_ID = int(os.environ.get("SCAN_MAX_ID", "2000"))  # highest product ID to try when scanning
PROBE_IDS = [416, 417, 248, 247]                        # products known to exist, used to check the API is answering

# Built-in fallback list, used only if the Supabase `watchlist` table can't be read.
# {style_sku: product_id} found by the scan on 14 Oct 2026.
DEFAULT_PRODUCT_IDS = {
    "1000": 204,
    "1001": 205,
    "1002": 206,
    "1117": 232,
    "3001": 245,
    "3003": 246,
    "3005": 247,
    "3006": 248,
    "3009": 450,
    "3010": 252,
    "3030": 258,
    "3031": 259,
    "3032": 260,
    "3033": 261,
    "3034": 864,
    "3035": 865,
    "3036": 866,
    "3037": 867,
    "4001": 416,
    "4001g": 262,
    "4008": 420,
    "4017": 1066,
    "4019": 1026,
    "4023": 1088,
    "4024": 1028,
    "4026": 567,
    "4053": 298,
    "4062": 304,
    "4063g": 584,
    "4064g": 585,
    "4080": 647,
    "4100": 501,
    "4101": 307,
    "4145": 539,
    "4146": 540,
    "4160": 692,
    "4161": 693,
    "4402": 314,
    "5001": 417,
    "5001g": 318,
    "5001v": 607,
    "5007": 323,
    "5012": 418,
    "5020": 608,
    "5025": 331,
    "5026": 333,
    "5026g": 910,
    "5040": 343,
    "5053": 352,
    "5065": 355,
    "5069": 1278,
    "5070": 508,
    "5071": 505,
    "5073": 631,
    "5074": 609,
    "5076": 817,
    "5079": 937,
    "5080": 576,
    "5082": 888,
    "5100": 504,
    "5101": 356,
    "5102": 358,
    "5102g": 1015,
    "5103": 1254,
    "5104": 1261,
    "5125": 373,
    "5145": 502,
    "5146": 503,
    "5148": 1018,
    "5160": 690,
    "5161": 691,
    "5162": 694,
    "5402": 378,
}
STYLE_PRODUCT_IDS = dict(DEFAULT_PRODUCT_IDS)  # replaced at runtime by the watchlist table

# Discord Webhook
DISCORD_WEBHOOK_URL = os.environ.get("DISCORD_WEBHOOK_URL")

# Optional switches (set as env vars in the workflow)
#   SEED_ONLY=1  -> write stock to Supabase but send NO low-stock alerts
#   DEBUG_RAW=1  -> print a few raw variants so you can see what the API returns
SEED_ONLY = os.environ.get("SEED_ONLY") == "1"
DEBUG_RAW = os.environ.get("DEBUG_RAW") == "1"

#   FORCE_RUN=1  -> ignore the daily schedule and pause switch (set automatically for manual "Run workflow" clicks)
FORCE_RUN = os.environ.get("FORCE_RUN") == "1"

# Settings below are overridden at runtime by the Supabase `app_settings` table (edited from the admin app)
LOW_STOCK_THRESHOLD = 25
SEND_ALL_CLEAR = True
RUN_TZ = timezone.utc          # time zone used for the date in the Discord report
MAX_REPORT_MESSAGES = 15       # safety cap on Discord messages per run
MAX_ATTEMPTS_PER_DAY = 3  # a failing run is retried at the next hourly check, up to this many times a day

# If more than this share of variants come back without a stock number, treat the scan as broken
MAX_NO_DATA_RATIO = 0.5

# Built-in fallback styles (the 73 original ones)
DEFAULT_STYLES = [
    "1000", "1001", "1002", "1117", "3001", "3003", "3005", "3006", "3009", "3010",
    "3030", "3031", "3032", "3033", "3034", "3035", "3036", "3037", "4001", "4001g",
    "4008", "4017", "4019", "4023", "4024", "4026", "4053", "4062", "4063g", "4064g",
    "4080", "4100", "4101", "4145", "4146", "4160", "4161", "4402", "5001", "5001g",
    "5001v", "5007", "5012", "5020", "5025", "5026", "5026g", "5040", "5053", "5065",
    "5069", "5070", "5071", "5073", "5074", "5076", "5079", "5080", "5082", "5100",
    "5101", "5102", "5102g", "5103", "5104", "5125", "5145", "5146", "5148", "5160",
    "5161", "5162", "5402"
]
# The styles actually monitored this run (set from the Supabase `watchlist` table in setup_watchlist())
TARGET_STYLES = list(DEFAULT_STYLES)
TARGET_LOWER = {style.lower(): style for style in TARGET_STYLES}
SLUG_TO_STYLE = {}   # product-page slug -> watchlist key, for links that have no style code in them

# Translation dictionaries for cleaner readability
COLOUR_MAP = {
    "Snset": "Sunset",
    "Buttr": "Butter",
    "Orchi": "Orchid",
    "Powdr": "Powder",
    "Walnt": "Walnut",
    "GRE_M": "Grey Marle",
    "NAT_R": "Natural",
    "BRI_R": "Bright Royal",
    "BLACK": "Black",
    "WHITE": "White",
    "NAVY": "Navy",
    "PINK": "Pink",
    "SAGE": "Sage",
    "ARMY": "Army",
    "BONE": "Bone",
    "BURGU": "Burgundy",
    "CAMEL": "Camel",
    "CHA_P": "Charcoal",
    "COAL": "Coal",
    "CREAM": "Cream",
    "DAR_C": "Dark Charcoal",
    "FORES": "Forest Green",
    "KEL_G": "Kelly Green",
    "KHAKI": "Khaki",
    "LBRTY": "Liberty",
    "MUSH": "Mushroom",
    "MUSTA": "Mustard",
    "ORANG": "Orange"
}

SIZE_MAP = {
    # Baby / kids / one size (letter-prefixed codes)
    "P-Os": "One Size",
    "S1218": "12-18 Months",
    "T1824": "18-24 Months",
    "P0-3": "0-3 Months",
    "Q3-6": "3-6 Months",
    "R6-12": "6-12 Months",
    "OS": "One Size",
    # Older 3-letter codes
    "XSM": "XS",
    "SML": "Small",
    "MED": "Medium",
    "LGE": "Large",
    "LRG": "Large",
    "XLG": "XL",
    # Current short codes
    "XS": "XS",
    "S": "Small",
    "M": "Medium",
    "L": "Large",
    "XL": "XL",
    "2XL": "2XL",
    "3XL": "3XL",
    "4XL": "4XL",
    "5XL": "5XL"
}


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

FAILED = False


def post_discord(content):
    """Returns True if Discord accepted the message."""
    if not DISCORD_WEBHOOK_URL:
        return False
    try:
        response = requests.post(DISCORD_WEBHOOK_URL, json={"content": content}, timeout=30)
        return response.status_code < 300
    except Exception as e:
        print(f"Discord post failed: {e}")
        return False


def alert_failure(message):
    """Post a failure alert, mark the run as failed (red in GitHub) and leave a flag so the workflow doesn't double-notify."""
    global FAILED
    FAILED = True
    print(message)
    if post_discord(message):
        try:
            with open("notified.flag", "w") as f:
                f.write("1")
        except OSError:
            pass


def parse_sku_attributes(variant_sku):
    """
    Fallback parser, used only when the API gives no option_values. Handles SKUs like:
      4001-BERRY-G-S      -> Berry / Small
      3001-BLACK-SML      -> Black / Small
      xxxx-BLACK-P-Os     -> Black / One Size
      xxxx-BLACK-Q-3-6    -> Black / 3-6 Months
    """
    parts = variant_sku.split("-")
    if len(parts) < 3:
        return "N/A", "N/A"

    raw_colour = parts[1]
    colour = COLOUR_MAP.get(raw_colour, raw_colour.replace("_", " ").title())

    rest = parts[2:]
    letter = None
    # A single leading letter (F, G, H, ...) is a sort prefix, not part of the size
    if len(rest) >= 2 and len(rest[0]) == 1 and rest[0].isalpha():
        letter = rest[0]
        rest = rest[1:]

    raw_size = "-".join(rest)
    candidates = [raw_size]
    if letter:
        candidates += [f"{letter}{raw_size}", f"{letter}-{raw_size}"]

    size = None
    for candidate in candidates:
        if candidate in SIZE_MAP:
            size = SIZE_MAP[candidate]
            break

    if size is None:
        cleaned = raw_size.replace("_", " ")
        size = cleaned.upper() if len(cleaned) <= 3 else cleaned.title()

    return colour, size


def attributes_from_variant(variant, variant_sku):
    """Colour/size from the API's option_values, falling back to the SKU."""
    colour = None
    size = None
    for ov in variant.get("option_values") or []:
        option_name = (ov.get("option_display_name") or "").strip().lower()
        label = (ov.get("label") or "").strip()
        if not label:
            continue
        if option_name in ("colour", "color"):
            colour = label
        elif option_name == "size":
            size = label

    sku_colour, sku_size = parse_sku_attributes(variant_sku)

    if colour:
        colour = COLOUR_MAP.get(colour, colour.replace("_", " ").title())
    else:
        colour = sku_colour

    if size:
        size = SIZE_MAP.get(size, size)
    else:
        size = sku_size

    return colour, size


def slug_from_url(url):
    """'https://www.ascolour.com.au/wos-maple-tee-4001/' -> 'wos-maple-tee-4001'"""
    path = urlparse(str(url or "")).path.strip("/")
    return path.split("/")[-1].lower() if path else None


def product_slug(product):
    path = ((product.get("custom_url") or {}).get("url") or "").strip().strip("/")
    return path.split("/")[-1].lower() if path else None


def extract_eta(variant):
    """AS Colour puts restock dates like 'ETA 20 Jan' in the variant's mpn field."""
    text = str(variant.get("mpn") or "").strip()
    return text[:100] if text and "eta" in text.lower() else None


def stock_status_label(stock):
    if stock is None:
        return "No stock data"
    if stock == 0:
        return "Out of stock"
    if stock < LOW_STOCK_THRESHOLD:
        return "Low"
    return "In stock"


def fetch_all_rows(table, order, page=1000):
    """Read every row. Supabase returns at most 1000 rows per request, so a plain select() silently misses the rest."""
    rows, start = [], 0
    while True:
        data = supabase.table(table).select("*").order(order).range(start, start + page - 1).execute().data or []
        rows.extend(data)
        if len(data) < page:
            return rows
        start += page


def to_int(value):
    if value is None:
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


# ---------------------------------------------------------------------------
# Stock API
# ---------------------------------------------------------------------------

def fetch_stock_batch(product_ids, label="", retry_empty=True):
    """
    Returns a list of product dicts. Returns None if the request failed (or, when retry_empty is
    True, if it kept coming back empty). With retry_empty=False an empty list is returned as [].
    """
    ids_param = ",".join(str(i) for i in product_ids)
    url = f"{STOCK_API_URL}?product_ids={ids_param}&customer_email="

    for attempt in range(2):
        try:
            response = requests.get(url, headers=STOCK_API_HEADERS, timeout=60)
        except Exception as e:
            print(f"Stock API request exception {label}: {e}")
            time.sleep(1)
            continue

        if response.status_code != 200:
            print(f"Stock API error {label}: {response.status_code} - {response.text[:300]}")
            time.sleep(1)
            continue

        try:
            data = response.json()
        except ValueError:
            print(f"Stock API returned non-JSON {label}: {response.text[:300]}")
            time.sleep(1)
            continue

        if isinstance(data, list) and data:
            return data

        if not retry_empty:
            return []

        print(f"Stock API returned an empty result {label} (attempt {attempt + 1}/2)")
        time.sleep(1)

    return None


def collect_product(product, scanned_items, stats):
    sku_key = (product.get("sku") or "").strip().lower()
    style_sku = TARGET_LOWER.get(sku_key)
    product_name = (product.get("name") or "").strip()

    if not style_sku:
        # A link without a style code in it (e.g. .../mens-organic-rib-singlet/): match it by its page address instead
        slug = product_slug(product)
        placeholder = SLUG_TO_STYLE.get(slug) if slug else None
        if not placeholder or not sku_key:
            return
        style_sku = sku_key
        stats["slug_resolved"][placeholder] = (sku_key, product.get("id"))

    # If the same product is also on the watchlist as a link without a style code, that extra row is a duplicate
    slug = product_slug(product)
    other = SLUG_TO_STYLE.get(slug) if slug else None
    if other and other != style_sku and other.lower() != sku_key and sku_key:
        stats["slug_resolved"][other] = (sku_key, product.get("id"))

    custom_path = ((product.get("custom_url") or {}).get("url") or "").strip()
    product_url = f"https://www.ascolour.com.au{custom_path}" if custom_path.startswith("/") else None

    stats["styles_found"].add(style_sku)
    if product.get("id"):
        stats["style_ids"][style_sku] = product.get("id")

    product_level_stock = to_int(product.get("inventory_level"))
    tracked_per_product = product.get("inventory_tracking") == "product"

    for variant in product.get("variants") or []:
        variant_sku = (variant.get("sku") or "").strip()
        if not variant_sku:
            continue

        stats["seen"] += 1

        stock = to_int(variant.get("inventory_level"))
        if stock is None and tracked_per_product:
            stock = product_level_stock

        if DEBUG_RAW and stats["debug_printed"] < 5:
            print(f"[DEBUG_RAW] {variant_sku}: inventory_level={variant.get('inventory_level')!r}, "
                  f"option_values={json.dumps(variant.get('option_values'))}")
            stats["debug_printed"] += 1

        if stock is None:
            stats["no_data"] += 1  # kept below: a variant with no stock number counts as low stock

        colour, size = attributes_from_variant(variant, variant_sku)
        scanned_items[variant_sku] = {
            "style_sku": style_sku,
            "product_name": product_name,
            "colour_name": colour,
            "size_name": size,
            "stock": stock,  # None = the API gave no stock number
            "eta": extract_eta(variant),
            "product_url": product_url,
        }


def scan_for_products(scanned_items, stats):
    """Slow path: try every product ID, keep the products whose SKU is one of our target styles."""
    print(f"Some styles have no product ID yet, scanning product IDs 1..{SCAN_MAX_ID} (takes a few minutes)...")

    # Make sure the API is answering at all before spending minutes on a scan
    probe = fetch_stock_batch(PROBE_IDS, label="(probe)")
    if not probe:
        stats["api_empty"] = True
        return

    for start in range(1, SCAN_MAX_ID + 1, STOCK_BATCH_SIZE):
        batch = list(range(start, min(start + STOCK_BATCH_SIZE, SCAN_MAX_ID + 1)))
        products = fetch_stock_batch(batch, label=f"(ids {batch[0]}..{batch[-1]})", retry_empty=False)
        for product in products or []:
            collect_product(product, scanned_items, stats)
        time.sleep(0.3)

    print("Product IDs found: " + json.dumps(dict(sorted(stats["style_ids"].items()))))


def fetch_known_products(style_ids, scanned_items, stats):
    """Fast path: we know every product ID, so just request them in batches."""
    id_list = sorted(set(style_ids.values()))
    failed_in_a_row = 0

    for i in range(0, len(id_list), STOCK_BATCH_SIZE):
        batch = id_list[i:i + STOCK_BATCH_SIZE]
        products = fetch_stock_batch(batch, label=f"(ids {batch[0]}..{batch[-1]})")

        if products is None and len(batch) > 1 and scanned_items:
            # Only worth retrying one by one if the API has been returning data so far
            products = []
            for pid in batch:
                single = fetch_stock_batch([pid], label=f"(id {pid})")
                if single:
                    products.extend(single)

        if products:
            failed_in_a_row = 0
            for product in products:
                collect_product(product, scanned_items, stats)
        else:
            failed_in_a_row += 1
            if not scanned_items and failed_in_a_row >= 3:
                print("Stock API keeps returning nothing - stopping early.")
                stats["api_empty"] = True
                break

        time.sleep(0.5)


def fetch_monitored_inventory():
    scanned_items = {}
    stats = {
        "seen": 0, "no_data": 0, "debug_printed": 0,
        "styles_found": set(), "style_ids": {}, "api_empty": False, "slug_resolved": {},
    }

    known_ids = {
        TARGET_LOWER[style.lower()]: pid
        for style, pid in STYLE_PRODUCT_IDS.items()
        if style.lower() in TARGET_LOWER
    }

    if len(known_ids) == len(TARGET_STYLES):
        fetch_known_products(known_ids, scanned_items, stats)
    else:
        scan_for_products(scanned_items, stats)

    stats["missing_styles"] = [s for s in TARGET_STYLES if s not in stats["styles_found"] and s not in stats["slug_resolved"]]
    return scanned_items, stats


# ---------------------------------------------------------------------------
# Settings & schedule (Supabase table `app_settings`, edited from the admin app)
# ---------------------------------------------------------------------------
# The workflow wakes up every hour. The script then decides whether today's run is due:
# it runs once per day, at the first hourly check after the chosen time (in the chosen time zone).

DEFAULT_SETTINGS = {
    "run_time": "09:00",
    "timezone": "Asia/Manila",
    "low_stock_threshold": "25",
    "paused": "false",
    "send_all_clear": "true",
}
FALLBACK_UTC_HOUR = 1  # only used if the settings table can't be read at all


def load_settings():
    settings = dict(DEFAULT_SETTINGS)
    try:
        rows = supabase.table("app_settings").select("key,value").execute().data or []
    except Exception as e:
        print(f"Could not read the app_settings table: {e}")
        settings["_unreadable"] = True
        return settings
    for row in rows:
        if row.get("key") is not None:
            settings[row["key"]] = row.get("value")
    return settings


def set_setting(key, value):
    supabase.table("app_settings").upsert(
        {"key": key, "value": str(value), "updated_at": datetime.now(timezone.utc).isoformat()}
    ).execute()


def get_timezone(settings):
    name = (settings.get("timezone") or "UTC").strip()
    try:
        return ZoneInfo(name)
    except Exception:
        print(f"Unknown time zone {name!r}, using UTC.")
        return timezone.utc


def parse_run_time(value):
    try:
        hh, mm = str(value).strip().split(":")[:2]
        return dtime(int(hh), int(mm))
    except Exception:
        return dtime(9, 0)


def should_run_now(settings, now_utc=None):
    """Returns (run_it, reason)."""
    now_utc = now_utc or datetime.now(timezone.utc)

    if FORCE_RUN:
        return True, "manual run requested"

    if settings.get("_unreadable"):
        if now_utc.hour == FALLBACK_UTC_HOUR:
            return True, "settings unreadable - fallback daily run"
        return False, "settings unreadable - waiting for the fallback hour"

    if str(settings.get("paused", "false")).strip().lower() == "true":
        return False, "the tracker is paused"

    tz = get_timezone(settings)
    now_local = now_utc.astimezone(tz)
    today = now_local.date().isoformat()

    if settings.get("last_run_date") == today:
        return False, f"already ran today ({today})"

    if settings.get("attempt_date") == today and (to_int(settings.get("attempts")) or 0) >= MAX_ATTEMPTS_PER_DAY:
        return False, f"already tried {MAX_ATTEMPTS_PER_DAY} times today"

    run_time = parse_run_time(settings.get("run_time"))
    if now_local.time() < run_time:
        return False, f"scheduled for {run_time.strftime('%H:%M')} ({tz}), it is {now_local.strftime('%H:%M')}"

    return True, "today's scheduled run is due"


def apply_settings(settings):
    global LOW_STOCK_THRESHOLD, SEND_ALL_CLEAR, RUN_TZ
    RUN_TZ = get_timezone(settings)
    threshold = to_int(settings.get("low_stock_threshold"))
    LOW_STOCK_THRESHOLD = threshold if threshold is not None and threshold >= 0 else 25
    SEND_ALL_CLEAR = str(settings.get("send_all_clear", "true")).strip().lower() != "false"


def record_attempt(settings, now_utc=None):
    now_utc = now_utc or datetime.now(timezone.utc)
    today = now_utc.astimezone(get_timezone(settings)).date().isoformat()
    attempts = (to_int(settings.get("attempts")) or 0) + 1 if settings.get("attempt_date") == today else 1
    try:
        set_setting("attempt_date", today)
        set_setting("attempts", attempts)
    except Exception as e:
        print(f"Could not record the attempt: {e}")


def record_result(settings, summary, now_utc=None):
    """Save the outcome so the admin app can show it, and mark today as done after a successful scheduled run."""
    now_utc = now_utc or datetime.now(timezone.utc)
    try:
        set_setting("last_run_at", now_utc.isoformat())
        set_setting("last_run_status", "failed" if FAILED else "ok")
        set_setting("last_run_summary", summary or ("The run failed - see the Discord alert / Action log" if FAILED else ""))
        if not FAILED and not FORCE_RUN:
            set_setting("last_run_date", now_utc.astimezone(get_timezone(settings)).date().isoformat())
    except Exception as e:
        print(f"Could not record the run result: {e}")


# ---------------------------------------------------------------------------
# Watchlist (Supabase table `watchlist`)
# ---------------------------------------------------------------------------

def apply_watchlist(styles, ids, slugs=None):
    global TARGET_STYLES, TARGET_LOWER, STYLE_PRODUCT_IDS, SLUG_TO_STYLE
    TARGET_STYLES = list(styles)
    TARGET_LOWER = {s.lower(): s for s in TARGET_STYLES}
    STYLE_PRODUCT_IDS = dict(ids)
    SLUG_TO_STYLE = dict(slugs or {})


def load_watchlist():
    """Enabled rows of the `watchlist` table, or None if it can't be used (the built-in list is used instead)."""
    try:
        data = supabase.table("watchlist").select("*").execute().data or []
    except Exception as e:
        print(f"Could not read the watchlist table: {e}")
        post_discord(
            "⚠️ **Watchlist warning:** couldn't read the `watchlist` table in Supabase, so this run used the built-in "
            "list of 73 styles instead. Check that the table exists and has RLS turned off (like `inventory_states`)."
        )
        return None

    rows = [r for r in data if (r.get("style_sku") or "").strip() and r.get("enabled") is not False]
    if not rows:
        print("The watchlist table is empty (or every row is disabled).")
        post_discord(
            "⚠️ **Watchlist warning:** the `watchlist` table has no enabled styles, so this run used the built-in "
            "list of 73 styles instead."
        )
        return None
    return rows


def setup_watchlist():
    rows = load_watchlist()
    if not rows:
        apply_watchlist(DEFAULT_STYLES, DEFAULT_PRODUCT_IDS)
        return None

    styles, ids, slugs, seen = [], {}, {}, set()
    for row in rows:
        sku = row["style_sku"].strip()
        if sku.lower() in seen:
            continue
        seen.add(sku.lower())
        styles.append(sku)
        product_id = to_int(row.get("product_id"))
        if product_id:
            ids[sku] = product_id
        slug = slug_from_url(row.get("url"))
        if slug:
            slugs[slug] = sku

    apply_watchlist(styles, ids, slugs)
    return rows


def save_found_product_ids(rows, found_ids, resolved=None):
    """Fill in the product_id column for rows that didn't have one, so later runs skip the slow scan."""
    resolved = resolved or {}
    existing = {r["style_sku"].strip().lower() for r in rows or []}
    for row in rows or []:
        canonical = TARGET_LOWER.get(row["style_sku"].strip().lower())

        if canonical in resolved:
            # The link had no style code: swap the placeholder for the real style code
            real_sku, real_id = resolved[canonical]
            try:
                if real_sku.lower() in existing:
                    supabase.table("watchlist").delete().eq("style_sku", row["style_sku"]).execute()
                else:
                    supabase.table("watchlist").update({"style_sku": real_sku, "product_id": real_id}).eq("style_sku", row["style_sku"]).execute()
                print(f"Link {row['style_sku']} is style {real_sku} (product ID {real_id}).")
            except Exception as e:
                print(f"Could not save the style code for {row['style_sku']}: {e}")
            continue

        product_id = found_ids.get(canonical)
        if product_id and to_int(row.get("product_id")) != product_id:
            try:
                supabase.table("watchlist").update({"product_id": product_id}).eq("style_sku", row["style_sku"]).execute()
                print(f"Saved product ID {product_id} for style {row['style_sku']}.")
            except Exception as e:
                print(f"Could not save the product ID for {row['style_sku']}: {e}")


# ---------------------------------------------------------------------------
# Main sync
# ---------------------------------------------------------------------------

FUN_MESSAGES = [
    "☕ **All Clear!** No inventory fires to extinguish today. Grab a coffee and enjoy the smooth sailing! ☕",
    "🚀 **Smooth Operations:** Stock levels are rock solid across your whole watchlist today. Keep up the awesome work! 🚀",
    "⏰ **Quiet Day in the Catalog:** Zero new low-stock items and zero restocks today. Everything's running like clockwork! ⏰",
    "👏 **Inventory Status:** All quiet on the stock front today! Time to relax or tackle the fun stuff on your list. 👏",
    "😎 **Beep Boop:** Scanned thousands of variants and found no threshold changes! Your inventory is chillin' today. 😎",
    "🍩 **Zero Drama:** No stock alerts today! Treat yourself to a donut (or two) for a job well done. 🍩",
    "💪 **Fort Knox Inventory:** Every watchlist style are fully stocked and holding the line today! 💪",
    "🌊 **Smooth Sailing:** Not a single SKU dropped below your low-stock threshold today. Breathe easy and have a fantastic morning! 🌊",
    "🎯 **Bullseye!** Scanned every single variant with 100% precision—no restocks or low-stock drops to report. 🎯",
    "🧘 **Inventory Zen:** Peaceful vibes in the database today. Everything is balanced and right where it needs to be. 🧘",
    "🧑‍🚀 **All Systems Nominal:** The catalog check passed with zero stock alerts. Houston, we have zero problems! 🧑‍🚀",
    "☕ **Pour Another Cup:** Everything is looking great across the catalog today. Sit back and enjoy your morning brew! ☕",
    "✨ **Golden Ratio:** No new low stock, no restock chaos. Just a pristine, smooth inventory run! ✨",
    "💤 **Sleeping Soundly:** The inventory warehouse is completely undisturbed today. Shhh, don't wake the tees! 💤",
    "🥧 **Flawless Run:** Today's scan went smoother than butter. All monitored styles are safe and sound! 🥧",
    "⚓ **On Course:** Staying steady on the high seas! Zero inventory emergencies detected in the last 24 hours. ⚓",
    "🕶️ **Too Cool for Stock Outs:** Stock levels are staying comfortable today. Put on those shades and enjoy your morning! 🕶️",
    "🏆 **Victory Lap:** No low-stock flags raised today! You're winning the inventory game. 🏆",
    "🥑 **Holy Guacamole!** Another day, another completely clean inventory report. Keep crushing it! 🥑",
    "🎮 **Level Complete:** Daily scan finished with 0 errors and 0 inventory alerts! Maximum score achieved. 🎮",
    "🌈 **Sunshine & Rainbows:** Nothing but healthy stock numbers across your whole watchlist today! 🌈",
    "🤫 **Warehouse Whispers:** The shelves are quiet today. All target variants are staying right where they belong! 🤫",
    "✈️ **Cruising Altitude:** 30,000 feet up and zero turbulence in the stock room today! ✈️",
    "🎵 **Sweet Harmony:** All styles are in perfect sync today. Not a single stock alarm sounded! 🎵",
    "🪄 **Inventory Wizardry:** Magic was worked overnight—no low stock, no missing sizes! 🪄",
    "🧊 **Chill Zone:** Stock counts are cool, calm, and collected. Zero action required from you today! 🧊",
    "🖼️ **Picture Perfect:** The inventory numbers look like a masterpiece today. Zero alerts on the horizon! 🖼️",
    "⚡ **Fully Charged:** Stock counts across all monitored items are looking super healthy today! ⚡",
    "🏅 **Gold Medal Day:** Clean sweep! All variants passed inspection with flying colors. 🏅",
    "🌾 **Smooth Harvest:** The inventory fields are bountiful and stable today. Enjoy the peace! 🌾",
    "⚡ **Lightning Fast & Quiet:** Scanned thousands of variants in seconds—zero stock threats found! ⚡",
    "🍀 **Lucky Days:** No surprise out-of-stock events today. Lady Luck is on our side! 🍀",
    "🚦 **Smooth Highway:** Cruising on green lights all morning. No inventory speed bumps today! 🚦",
    "🏰 **Castle Guarded:** The inventory fortress remains untouched today. All stock holds firm! 🏰",
    "🍕 **Pizza Time:** No inventory crises to handle today, so you've earned a pizza slice later! 🍕"
]


def previous_status_label(prev_data):
    saved = prev_data.get("stock_status")
    if saved:
        return saved
    stock = to_int(prev_data.get("stock_count"))
    if not stock:
        return "Out of stock"
    return "Low" if stock < LOW_STOCK_THRESHOLD else "In stock"


def low_line(ev):
    status = ev["new_status"]
    if status == "No stock data":
        stock = "**no stock data**"
    elif status == "Out of stock":
        stock = "**0** (out of stock)"
    else:
        stock = f"**{ev['new_stock']}** left"
    line = f"• {ev['colour_name']} / {ev['size_name']} — {stock}"
    if ev.get("previous_stock") is not None:
        line += f" · was {ev['previous_stock']}"
    if ev.get("eta"):
        line += f" · {ev['eta']}"
    return line


def restock_line(ev):
    line = f"• {ev['colour_name']} / {ev['size_name']} — now **{ev['new_stock']}**"
    if ev.get("previous_stock") is not None:
        line += f" · was {ev['previous_stock']}"
        previous = (ev.get("previous_status") or "").lower()
        if previous and previous != "in stock":
            line += f" ({previous})"
    return line


def product_blocks(events, line_fn, max_lines=10):
    """Group events by product so each product is one block: a bold name + link, then one line per colour/size."""
    groups = {}
    for ev in events:
        key = (ev.get("product_name") or ev["style_sku"], ev.get("product_url"))
        groups.setdefault(key, []).append(ev)
    blocks = []
    for (name, url), evs in sorted(groups.items(), key=lambda kv: (kv[0][0] or "").lower()):
        evs.sort(key=lambda e: (e.get("colour_name") or "", e.get("size_name") or ""))
        for i in range(0, len(evs), max_lines):
            head = f"**{name}**" + (f" <{url}>" if url else "") + (" (continued)" if i else "")
            blocks.append(head + "\n" + "\n".join(line_fn(e) for e in evs[i:i + max_lines]))
    return blocks


def chunk_report(sections, limit=1800):
    """Pack the blocks into Discord messages that stay under the 2000-character limit."""
    messages, current, current_title = [], "", None
    for title, blocks in sections:
        for index, block in enumerate(blocks):
            piece = ("" if current_title == title else title + "\n") + block
            if current and len(current) + 1 + len(piece) > limit:
                messages.append(current)
                piece = title + (" (continued)" if index else "") + "\n" + block
                current = ""
            current = (current + "\n" if current else "") + piece
            current_title = title
    if current:
        messages.append(current)
    return messages


def build_report(low_events, restock_events):
    threshold = LOW_STOCK_THRESHOLD
    sections = []
    if low_events:
        sections.append((f"🔻 **FELL BELOW {threshold}** — take these off the website ({len(low_events)})",
                         product_blocks(low_events, low_line)))
    if restock_events:
        sections.append((f"🔺 **RESTOCKED TO {threshold}+** — put these back on the website ({len(restock_events)})",
                         product_blocks(restock_events, restock_line)))
    messages = chunk_report(sections)
    stamp = datetime.now(RUN_TZ).strftime("%a %d %b")
    intro = (f"📅 **Stock report — {stamp}:** {len(low_events)} fell below {threshold}, "
             f"{len(restock_events)} restocked to {threshold}+ (changes since the last run).")
    messages[0] = intro + "\n\n" + messages[0]
    if len(messages) > MAX_REPORT_MESSAGES:
        hidden = len(messages) - MAX_REPORT_MESSAGES
        messages = messages[:MAX_REPORT_MESSAGES] + [f"…and {hidden} more message(s) of changes. Open the admin app to see the full list."]
    return messages


def sync_inventory_and_alert():
    watchlist_rows = setup_watchlist()
    source = "the Supabase watchlist table" if watchlist_rows else "the built-in list"
    print(f"Watching {len(TARGET_STYLES)} styles from {source}.")

    print("Fetching live stock from AS Colour Australia...")
    current_inventory, stats = fetch_monitored_inventory()
    print(
        f"Variants seen: {stats['seen']} | with stock data: {len(current_inventory)} | "
        f"no stock data: {stats['no_data']} | styles found: {len(stats['styles_found'])}"
    )
    if stats.get("missing_styles"):
        print(f"Styles with no data: {', '.join(stats['missing_styles'])}")

    if not current_inventory:
        if stats.get("api_empty"):
            alert_failure(
                "🚨 **SCAN FAILED!** The stock API keeps answering with an empty list, so it probably wants a header "
                "we aren't sending (the Store-Hash header may have changed). Nothing was written to the database. Check the Action log!"
            )
        else:
            alert_failure(
                "🚨 **SCAN FAILED!** The tracker went looking for tees and found *absolute emptiness* "
                "(no stock numbers from the stock API). Nothing was written to the database. Check the Action log!"
            )
        return

    # If a big share of variants had no stock number, the scan is unreliable - don't touch the DB
    if stats["seen"] and (stats["no_data"] / stats["seen"]) > MAX_NO_DATA_RATIO:
        alert_failure(
            f"🚨 **SCAN FAILED (partial data):** {stats['no_data']} of {stats['seen']} variants returned no stock number, "
            "so this scan was skipped to avoid saving wrong zeros. Check the Action log."
        )
        return

    if stats.get("missing_styles"):
        post_discord(
            "⚠️ **Watchlist warning:** couldn't find these styles on AS Colour: "
            + ", ".join(f"`{s}`" for s in stats["missing_styles"])
            + ". Check them for typos in the `watchlist` table (they were skipped)."
        )

    if watchlist_rows:
        save_found_product_ids(watchlist_rows, stats["style_ids"], stats["slug_resolved"])

    previous_states = {item["variant_id"]: item for item in fetch_all_rows("inventory_states", "variant_id")}

    low_events = []       # fell below the threshold since the last run
    restock_events = []   # back at/above the threshold since the last run
    upsert_payload = []
    now_iso = datetime.now(timezone.utc).isoformat()

    for sku, item_data in current_inventory.items():
        stock = item_data["stock"]          # None = no stock number from the API
        eta = item_data.get("eta")
        stock_value = stock if stock is not None else 0
        # Low = anything that is not a number at or above the threshold (0, 1-24, blank / no data)
        is_low = stock is None or stock < LOW_STOCK_THRESHOLD
        status = stock_status_label(stock)
        prev_data = previous_states.get(sku)

        if prev_data is not None and not SEED_ONLY:
            prev_stock = to_int(prev_data.get("stock_count"))
            # Judge the old numbers against today's threshold, so changing the threshold doesn't create fake events
            was_low = prev_data.get("stock_status") == "No stock data" or prev_stock is None or prev_stock < LOW_STOCK_THRESHOLD
            if is_low != was_low:
                event = {
                    "event_type": "low" if is_low else "restock",
                    "variant_id": sku,
                    "style_sku": item_data["style_sku"],
                    "product_name": item_data["product_name"],
                    "colour_name": item_data["colour_name"],
                    "size_name": item_data["size_name"],
                    "previous_stock": prev_stock,
                    "previous_status": previous_status_label(prev_data),
                    "new_stock": stock_value,
                    "new_status": status,
                    "eta": eta,
                    "product_url": item_data.get("product_url"),
                }
                (low_events if is_low else restock_events).append(event)

        upsert_payload.append({
            "variant_id": sku,
            "style_sku": item_data["style_sku"],
            "product_name": item_data["product_name"],
            "colour_name": item_data["colour_name"],
            "size_name": item_data["size_name"],
            "stock_count": stock_value,
            "is_below_25": is_low,
            "stock_status": status,
            "eta": eta,
            "updated_at": now_iso
        })

    # Upsert in batches so big catalogs don't hit request-size limits
    try:
        for i in range(0, len(upsert_payload), 500):
            supabase.table("inventory_states").upsert(upsert_payload[i:i + 500]).execute()
    except Exception as e:
        # Most likely the stock_status / eta columns don't exist yet: save without them and tell the user
        print(f"Saving with the status columns failed ({e}); retrying without them.")
        post_discord("⚠️ **Setup warning:** the `stock_status` / `eta` columns are missing in Supabase, so statuses and ETAs "
                     "weren't saved. Run `update_setup.sql` in the Supabase SQL editor.")
        legacy = [{k: v for k, v in row.items() if k not in ("stock_status", "eta")} for row in upsert_payload]
        for i in range(0, len(legacy), 500):
            supabase.table("inventory_states").upsert(legacy[i:i + 500]).execute()
    print(f"Upserted {len(upsert_payload)} variant rows.")

    all_events = low_events + restock_events
    if all_events:
        try:
            rows = [{k: v for k, v in ev.items() if k != "product_url"} for ev in all_events]
            for i in range(0, len(rows), 500):
                supabase.table("stock_events").insert(rows[i:i + 500]).execute()
            print(f"Recorded {len(rows)} stock event(s).")
        except Exception as e:
            print(f"Could not save stock events: {e}")
            post_discord("⚠️ **Setup warning:** couldn't save the change history. Run `update_setup.sql` in the Supabase SQL editor.")

    try:  # keep the history to the last 90 days
        cutoff = (datetime.now(timezone.utc) - timedelta(days=90)).isoformat()
        supabase.table("stock_events").delete().lt("created_at", cutoff).execute()
    except Exception:
        pass

    low_count = sum(1 for p in upsert_payload if p["is_below_25"])
    summary = (f"{len(upsert_payload)} variants across {len(stats['styles_found'])} styles, {low_count} below {LOW_STOCK_THRESHOLD}; "
               f"{len(low_events)} fell below, {len(restock_events)} restocked")

    if SEED_ONLY:
        print("SEED_ONLY is on: database updated, alerts skipped.")
        return summary

    if DISCORD_WEBHOOK_URL:
        if all_events:
            for message in build_report(low_events, restock_events):
                post_discord(message)
                time.sleep(0.6)  # stay well inside Discord's rate limit
            print(f"Sent the stock report: {len(low_events)} fell below, {len(restock_events)} restocked.")
        elif SEND_ALL_CLEAR:
            post_discord(
                random.choice(FUN_MESSAGES)
                + f"\n✅ **No changes since the last run:** nothing fell below {LOW_STOCK_THRESHOLD} and nothing was restocked."
            )
            print("No changes - all-clear message sent.")

    return summary


def main():
    global supabase
    settings = dict(DEFAULT_SETTINGS)
    summary = None
    try:
        if not SUPABASE_URL or not SUPABASE_KEY:
            raise RuntimeError("SUPABASE_URL / SUPABASE_KEY are missing (check the GitHub secrets and the workflow env block)")
        supabase = create_client(SUPABASE_URL, SUPABASE_KEY)

        settings = load_settings()
        run_it, reason = should_run_now(settings)
        print(f"Schedule check: {reason}.")
        if not run_it:
            return
        if settings.get("_unreadable"):
            post_discord(
                "⚠️ **Settings warning:** couldn't read the `app_settings` table in Supabase, so the schedule, pause switch and "
                "threshold from the admin app are being ignored (default threshold 25). Run `app_settings_setup.sql` if you haven't."
            )

        apply_settings(settings)
        if not FORCE_RUN:
            record_attempt(settings)

        summary = sync_inventory_and_alert()
    except Exception as e:
        traceback.print_exc()
        alert_failure(f"🚨 **TRACKER CRASHED!** `{type(e).__name__}: {str(e)[:300]}` Nothing (or only part of the run) was saved. Check the Action log!")

    record_result(settings, summary)

    if FAILED:
        sys.exit(1)


if __name__ == "__main__":
    main()
