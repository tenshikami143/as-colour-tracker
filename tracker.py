import os
import random
import base64
import json
import requests
from datetime import datetime, timezone
from supabase import create_client, Client

# Initialize Supabase credentials
SUPABASE_URL = os.environ.get("SUPABASE_URL")
SUPABASE_KEY = os.environ.get("SUPABASE_KEY")
supabase: Client = create_client(SUPABASE_URL, SUPABASE_KEY)

# AS Colour Australia GraphQL API credentials
GRAPHQL_URL = "https://www.ascolour.com.au/graphql"
raw_token = os.environ.get("ASCOLOUR_GRAPHQL_TOKEN") or os.environ.get("AS_COLOUR_API_TOKEN")

if raw_token and raw_token.lower().startswith("bearer "):
    API_TOKEN = raw_token.split(" ", 1)[1].strip()
else:
    API_TOKEN = raw_token

# Discord Webhook
DISCORD_WEBHOOK_URL = os.environ.get("DISCORD_WEBHOOK_URL")

# Optional switches (set as env vars / GitHub Actions secrets or workflow env)
#   SEED_ONLY=1  -> write stock to Supabase but send NO low-stock alerts (use for the first run after a fix)
#   DEBUG_RAW=1  -> print the raw inventory JSON of the first few variants so you can see what the API returns
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
    # Current short codes (e.g. 4001-BERRY-G-S -> "S")
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
    Handles SKUs like:
      4001-BERRY-G-S      (style-colour-sortLetter-size)  -> Berry / Small
      4001-BRI_R-F-XS     -> Bright Royal / XS
      3001-BLACK-SML      (older style-colour-size)       -> Black / Small
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


def extract_stock(v_node):
    """
    Returns an int, or None when the API gave us no stock number.
    (None is NOT the same as 0 - never store it as 0.)
    """
    inventory = v_node.get("inventory")
    if not inventory:
        return None
    aggregated = inventory.get("aggregated")
    if not aggregated:
        return None
    value = aggregated.get("availableToSell")
    if value is None:
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def check_token_expiration_and_alert():
    if not API_TOKEN or not DISCORD_WEBHOOK_URL:
        return
    try:
        parts = API_TOKEN.split(".")
        if len(parts) < 2:
            return
        payload_part = parts[1] + "=" * (-len(parts[1]) % 4)
        payload = json.loads(base64.urlsafe_b64decode(payload_part).decode("utf-8"))

        eat = payload.get("eat")
        if eat:
            now_ts = datetime.now(timezone.utc).timestamp()
            days_left = (eat - now_ts) / 86400  # Convert seconds to days

            if days_left <= 0:
                msg = "🚨 **MAYDAY! THE TOKEN HAS OFFICIALLY KICKED THE BUCKET!** 🪦 AS Colour has locked us out in the cold. Drop what you're doing, grab a fresh token, and slap it into GitHub Secrets before the tees revolt!"
                post_discord(msg)
                print("Alert sent: Token expired.")
            elif days_left <= 5:
                msg = f"⏳ **TICK-TOCK!** Your AS Colour token is aging like fine milk and expires in **{round(days_left, 1)} days**. Get ahead of the chaos and grab a fresh token before this ship hits the iceberg!"
                post_discord(msg)
                print(f"Alert sent: Token expires in {round(days_left, 1)} days.")
    except Exception as e:
        print(f"Error checking token expiration: {e}")


# ---------------------------------------------------------------------------
# GraphQL fetching
# ---------------------------------------------------------------------------

CATALOG_QUERY = """
query FetchCatalog($productCursor: String) {
  site {
    products(first: 50, after: $productCursor) {
      pageInfo {
        hasNextPage
        endCursor
      }
      edges {
        node {
          id
          name
          sku
          variants(first: 50) {
            pageInfo {
              hasNextPage
              endCursor
            }
            edges {
              node {
                id
                sku
                inventory {
                  aggregated {
                    availableToSell
                  }
                }
              }
            }
          }
        }
      }
    }
  }
}
"""

MORE_VARIANTS_QUERY = """
query MoreVariants($id: ID!, $variantCursor: String) {
  node(id: $id) {
    ... on Product {
      variants(first: 50, after: $variantCursor) {
        pageInfo {
          hasNextPage
          endCursor
        }
        edges {
          node {
            id
            sku
            inventory {
              aggregated {
                availableToSell
              }
            }
          }
        }
      }
    }
  }
}
"""

_warned_graphql_errors = False


def gql(query, variables, headers):
    """POST a GraphQL query. Returns the parsed JSON, or None on a hard failure."""
    global _warned_graphql_errors
    try:
        response = requests.post(
            GRAPHQL_URL,
            json={"query": query, "variables": variables},
            headers=headers,
            timeout=60,
        )
    except Exception as e:
        print(f"Request Exception: {e}")
        return None

    if response.status_code != 200:
        print(f"API Error: {response.status_code} - {response.text[:500]}")
        return None

    data = response.json()

    if data.get("errors"):
        # Some errors (e.g. a field you're not permitted to read) still come back with partial data.
        if not _warned_graphql_errors:
            print(f"GraphQL Error(s): {data['errors']}")
            _warned_graphql_errors = True
        if not data.get("data"):
            return None

    return data


def collect_variants(style_sku, product_name, variant_edges, scanned_items, stats):
    for v_edge in variant_edges:
        v_node = v_edge.get("node") or {}
        variant_sku = (v_node.get("sku") or "").strip()
        if not variant_sku:
            continue

        stats["seen"] += 1

        if DEBUG_RAW and stats["debug_printed"] < 5:
            print(f"[DEBUG_RAW] {variant_sku}: {json.dumps(v_node.get('inventory'))}")
            stats["debug_printed"] += 1

        stock = extract_stock(v_node)
        if stock is None:
            stats["no_data"] += 1
            continue  # never store "unknown" as 0

        colour, size = parse_sku_attributes(variant_sku)
        scanned_items[variant_sku] = {
            "style_sku": style_sku,
            "product_name": product_name,
            "colour_name": colour,
            "size_name": size,
            "stock": stock
        }


def fetch_monitored_inventory():
    headers = {"Content-Type": "application/json"}
    if API_TOKEN:
        headers["Authorization"] = f"Bearer {API_TOKEN}"

    scanned_items = {}
    stats = {"seen": 0, "no_data": 0, "debug_printed": 0}
    has_next_products = True
    product_cursor = None

    while has_next_products:
        data = gql(CATALOG_QUERY, {"productCursor": product_cursor}, headers)
        if data is None:
            break

        products_data = (data.get("data") or {}).get("site", {}).get("products", {}) or {}
        edges = products_data.get("edges", []) or []

        for edge in edges:
            product = edge.get("node") or {}
            style_sku = (product.get("sku") or "").strip()
            product_name = (product.get("name") or "").strip()

            if style_sku not in TARGET_STYLES:
                continue

            variants_conn = product.get("variants") or {}
            collect_variants(style_sku, product_name, variants_conn.get("edges", []) or [], scanned_items, stats)

            # Page through the rest of this product's variants (the first page only holds 50)
            v_page = variants_conn.get("pageInfo") or {}
            while v_page.get("hasNextPage"):
                more = gql(
                    MORE_VARIANTS_QUERY,
                    {"id": product.get("id"), "variantCursor": v_page.get("endCursor")},
                    headers,
                )
                if more is None:
                    break
                more_conn = ((more.get("data") or {}).get("node") or {}).get("variants") or {}
                collect_variants(style_sku, product_name, more_conn.get("edges", []) or [], scanned_items, stats)
                v_page = more_conn.get("pageInfo") or {}

        page_info = products_data.get("pageInfo") or {}
        has_next_products = page_info.get("hasNextPage", False)
        product_cursor = page_info.get("endCursor")

    return scanned_items, stats


# ---------------------------------------------------------------------------
# Main sync
# ---------------------------------------------------------------------------

def sync_inventory_and_alert():
    # Check token health first
    check_token_expiration_and_alert()

    print("Fetching catalog data with fully translated attributes from AS Colour Australia...")
    current_inventory, stats = fetch_monitored_inventory()
    print(
        f"Variants seen: {stats['seen']} | with stock data: {len(current_inventory)} | "
        f"no stock data: {stats['no_data']}"
    )

    if not current_inventory:
        print("No inventory data retrieved. Sending scan failure alert.")
        if stats["seen"] > 0:
            post_discord(
                f"🚨 **INVENTORY EMERGENCY!** The tracker found {stats['seen']} variants but **none** returned a stock number. "
                "The token probably can't read inventory (or AS Colour changed the field). Nothing was written to the database."
            )
        else:
            post_discord(
                "🚨 **INVENTORY EMERGENCY!** The tracker went looking for tees and found *absolute emptiness* (0 items). "
                "Either AS Colour put up a firewall or ghosts stole the warehouse. Check your token/API immediately!"
            )
        return

    # If a big share of variants had no stock number, the scan is unreliable - don't touch the DB
    if stats["seen"] and (stats["no_data"] / stats["seen"]) > MAX_NO_DATA_RATIO:
        print("Too many variants without stock data. Aborting before writing to Supabase.")
        post_discord(
            f"⚠️ **PARTIAL DATA:** {stats['no_data']} of {stats['seen']} variants returned no stock number, "
            "so this scan was skipped to avoid saving wrong zeros. Check the token permissions / API response."
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
