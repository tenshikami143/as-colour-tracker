import os
import time
import random
import json
import requests
from datetime import datetime, timezone
from supabase import create_client, Client

# Initialize Supabase credentials
SUPABASE_URL = os.environ.get("SUPABASE_URL")
SUPABASE_KEY = os.environ.get("SUPABASE_KEY")
supabase: Client = create_client(SUPABASE_URL, SUPABASE_KEY)

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

# {style_sku: product_id} for every target style. While this is empty (or incomplete) the script
# scans product IDs 1..SCAN_MAX_ID to find them (slow, about 3-4 minutes) and prints the finished
# dictionary in the log. Paste that dictionary here to make every run fast.
STYLE_PRODUCT_IDS = {
    # "4001": 416,
    # "3005": 247,
}

# Discord Webhook
DISCORD_WEBHOOK_URL = os.environ.get("DISCORD_WEBHOOK_URL")

# Optional switches (set as env vars in the workflow)
#   SEED_ONLY=1  -> write stock to Supabase but send NO low-stock alerts
#   DEBUG_RAW=1  -> print a few raw variants so you can see what the API returns
SEED_ONLY = os.environ.get("SEED_ONLY") == "1"
DEBUG_RAW = os.environ.get("DEBUG_RAW") == "1"

# If more than this share of variants come back without a stock number, treat the scan as broken
MAX_NO_DATA_RATIO = 0.5

# Monitored Scope (73 Target Styles)
TARGET_STYLES = [
    "1000", "1001", "1002", "1117", "3001", "3003", "3005", "3006", "3009", "3010",
    "3030", "3031", "3032", "3033", "3034", "3035", "3036", "3037", "4001", "4001g",
    "4008", "4017", "4019", "4023", "4024", "4026", "4053", "4062", "4063g", "4064g",
    "4080", "4100", "4101", "4145", "4146", "4160", "4161", "4402", "5001", "5001g",
    "5001v", "5007", "5012", "5020", "5025", "5026", "5026g", "5040", "5053", "5065",
    "5069", "5070", "5071", "5073", "5074", "5076", "5079", "5080", "5082", "5100",
    "5101", "5102", "5102g", "5103", "5104", "5125", "5145", "5146", "5148", "5160",
    "5161", "5162", "5402"
]
TARGET_LOWER = {style.lower(): style for style in TARGET_STYLES}

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

def post_discord(content):
    if not DISCORD_WEBHOOK_URL:
        return
    try:
        requests.post(DISCORD_WEBHOOK_URL, json={"content": content}, timeout=30)
    except Exception as e:
        print(f"Discord post failed: {e}")


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
    style_sku = TARGET_LOWER.get((product.get("sku") or "").strip().lower())
    product_name = (product.get("name") or "").strip()

    if not style_sku:
        return

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
            stats["no_data"] += 1
            continue  # never store "unknown" as 0

        colour, size = attributes_from_variant(variant, variant_sku)
        scanned_items[variant_sku] = {
            "style_sku": style_sku,
            "product_name": product_name,
            "colour_name": colour,
            "size_name": size,
            "stock": stock
        }


def scan_for_products(scanned_items, stats):
    """Slow path: try every product ID, keep the products whose SKU is one of our target styles."""
    print(f"STYLE_PRODUCT_IDS is incomplete, scanning product IDs 1..{SCAN_MAX_ID} (takes a few minutes)...")

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

    print("Paste this into tracker.py (replace the empty STYLE_PRODUCT_IDS) to skip the scan next time:")
    print("STYLE_PRODUCT_IDS = " + json.dumps(dict(sorted(stats["style_ids"].items())), indent=4))


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
        "styles_found": set(), "style_ids": {}, "api_empty": False,
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

    stats["missing_styles"] = [s for s in TARGET_STYLES if s not in stats["styles_found"]]
    return scanned_items, stats


# ---------------------------------------------------------------------------
# Main sync
# ---------------------------------------------------------------------------

def sync_inventory_and_alert():
    print("Fetching live stock from AS Colour Australia...")
    current_inventory, stats = fetch_monitored_inventory()
    print(
        f"Variants seen: {stats['seen']} | with stock data: {len(current_inventory)} | "
        f"no stock data: {stats['no_data']} | styles found: {len(stats['styles_found'])}"
    )
    if stats.get("missing_styles"):
        print(f"Styles with no data: {', '.join(stats['missing_styles'])}")

    if not current_inventory:
        print("No inventory data retrieved. Sending scan failure alert.")
        if stats.get("api_empty"):
            post_discord(
                "🚨 **INVENTORY EMERGENCY!** The stock API keeps answering with an empty list, so it probably wants a header "
                "we aren't sending (the Store-Hash header may have changed). Nothing was written to the database. Check the Action log!"
            )
        else:
            post_discord(
                "🚨 **INVENTORY EMERGENCY!** The tracker went looking for tees and found *absolute emptiness* "
                "(no stock numbers from the stock API). Nothing was written to the database. Check the Action log!"
            )
        return

    # If a big share of variants had no stock number, the scan is unreliable - don't touch the DB
    if stats["seen"] and (stats["no_data"] / stats["seen"]) > MAX_NO_DATA_RATIO:
        print("Too many variants without stock data. Aborting before writing to Supabase.")
        post_discord(
            f"⚠️ **PARTIAL DATA:** {stats['no_data']} of {stats['seen']} variants returned no stock number, "
            "so this scan was skipped to avoid saving wrong zeros. Check the Action log."
        )
        return

    response = supabase.table("inventory_states").select("*").execute()
    previous_states = {item["variant_id"]: item for item in response.data} if response.data else {}

    alerts = []
    upsert_payload = []
    now_iso = datetime.now(timezone.utc).isoformat()

    for sku, item_data in current_inventory.items():
        stock = item_data["stock"]
        prev_data = previous_states.get(sku)
        is_low = stock < 25

        if prev_data is not None and not SEED_ONLY:
            was_low = prev_data.get("is_below_25", False)
            if is_low and (not was_low or prev_data.get("stock_count") != stock):
                alerts.append(
                    f"⚠️ **Low Stock Alert**: `{sku}` ({item_data['product_name']} - "
                    f"{item_data['colour_name']} / {item_data['size_name']}) -> Stock: **{stock}** units"
                )

        upsert_payload.append({
            "variant_id": sku,
            "style_sku": item_data["style_sku"],
            "product_name": item_data["product_name"],
            "colour_name": item_data["colour_name"],
            "size_name": item_data["size_name"],
            "stock_count": stock,
            "is_below_25": is_low,
            "updated_at": now_iso
        })

    # Upsert in batches so big catalogs don't hit request-size limits
    for i in range(0, len(upsert_payload), 500):
        supabase.table("inventory_states").upsert(upsert_payload[i:i + 500]).execute()
    print(f"Upserted {len(upsert_payload)} variant rows.")

    if SEED_ONLY:
        print("SEED_ONLY is on: database updated, alerts skipped.")
        return

    if DISCORD_WEBHOOK_URL:
        if alerts:
            for i in range(0, len(alerts), 10):
                chunk = "\n".join(alerts[i:i + 10])
                post_discord(chunk)
            print(f"Sent {len(alerts)} alert(s) to Discord.")
        else:
            fun_messages = [
                "☕ **All Clear!** No inventory fires to extinguish today. Grab a coffee and enjoy the smooth sailing! ☕",
                "🚀 **Smooth Operations:** Stock levels are rock solid across all 73 styles today. Keep up the awesome work! 🚀",
                "⏰ **Quiet Day in the Catalog:** Zero new low-stock items and zero restocks today. Everything's running like clockwork! ⏰",
                "👏 **Inventory Status:** All quiet on the stock front today! Time to relax or tackle the fun stuff on your list. 👏",
                "😎 **Beep Boop:** Scanned thousands of variants and found no threshold changes! Your inventory is chillin' today. 😎",
                "🍩 **Zero Drama:** No stock alerts today! Treat yourself to a donut (or two) for a job well done. 🍩",
                "💪 **Fort Knox Inventory:** All 73 monitored styles are fully stocked and holding the line today! 💪",
                "🌊 **Smooth Sailing:** Not a single SKU dropped below 25 today. Breathe easy and have a fantastic morning! 🌊",
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
                "🌈 **Sunshine & Rainbows:** Nothing but healthy stock numbers across all 73 styles today! 🌈",
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
            post_discord(random.choice(fun_messages))
            print("Discord alert delivered successfully.")


if __name__ == "__main__":
    sync_inventory_and_alert()
