import os
import random
import requests
from datetime import datetime, timezone
import streamlit as st
from supabase import create_client, Client

# Streamlit UI Title
st.title("Tee Junction Inventory Tracker")

# 1. Robust Supabase Initialization
@st.cache_resource
def init_supabase():
    url = None
    key = None
    
    # Try Streamlit Cloud secrets first
    try:
        if "SUPABASE_URL" in st.secrets:
            url = st.secrets["SUPABASE_URL"]
        if "SUPABASE_KEY" in st.secrets:
            key = st.secrets["SUPABASE_KEY"]
    except Exception:
        pass
        
    # Fallback to environment variables (for GitHub Actions)
    if not url:
        url = os.environ.get("SUPABASE_URL")
    if not key:
        key = os.environ.get("SUPABASE_KEY")
        
    if not url or not key:
        return None
        
    return create_client(url, key)

supabase = init_supabase()

# 2. Fetch and Display Inventory Data on the Web Dashboard
@st.cache_data(ttl=60)
def load_web_data():
    if not supabase:
        return []
    response = supabase.table("inventory_states").select("*").execute()
    return response.data

# Render Web Dashboard UI if running inside Streamlit
if supabase:
    try:
        data = load_web_data()
        if data:
            col1, col2 = st.columns(2)
            col1.metric("Total Items Tracked", len(data))
            
            st.subheader("Current Stock Levels")
            st.dataframe(data, width="stretch")
        else:
            st.info("Connected to Supabase, but no inventory records found in 'inventory_states'.")
    except Exception as e:
        st.error(f"Error fetching data from Supabase: {e}")
else:
    st.error("Supabase credentials missing! Please check your Streamlit Secrets.")

# AS Colour Australia GraphQL API credentials
GRAPHQL_URL = "https://www.ascolour.com.au/graphql"
raw_token = os.environ.get("ASCOLOUR_GRAPHQL_TOKEN") or os.environ.get("AS_COLOUR_API_TOKEN")

if raw_token and raw_token.lower().startswith("bearer "):
    API_TOKEN = raw_token.split(" ", 1)[1].strip()
else:
    API_TOKEN = raw_token

# Discord Webhook
DISCORD_WEBHOOK_URL = os.environ.get("DISCORD_WEBHOOK_URL")

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
    "P-Os": "One Size",
    "S1218": "12-18 Months",
    "T1824": "18-24 Months",
    "P0-3": "0-3 Months",
    "Q3-6": "3-6 Months",
    "R6-12": "6-12 Months",
    "OS": "One Size",
    "XSM": "XS",
    "SML": "Small",
    "MED": "Medium",
    "LGE": "Large",
    "XLG": "XL",
    "2XL": "2XL",
    "3XL": "3XL"
}


def parse_sku_attributes(variant_sku):
    parts = variant_sku.split("-")
    if len(parts) >= 3:
        raw_colour = parts[1]
        raw_size = "-".join(parts[2:])
        
        colour = COLOUR_MAP.get(raw_colour, raw_colour.replace("_", " ").title())
        size = SIZE_MAP.get(raw_size, raw_size.replace("_", " ").title())
        
        return colour, size
    return "N/A", "N/A"


def fetch_monitored_inventory():
    headers = {
        "Content-Type": "application/json"
    }
    if API_TOKEN:
        headers["Authorization"] = f"Bearer {API_TOKEN}"

    scanned_items = {}
    has_next_products = True
    product_cursor = None

    query = """
    query FetchCatalog($productCursor: String,$variantCursor: String) {
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
      }
    }
    """

    while has_next_products:
        variables = {
            "productCursor": product_cursor,
            "variantCursor": None
        }

        try:
            response = requests.post(GRAPHQL_URL, json={"query": query, "variables": variables}, headers=headers)
            if response.status_code != 200:
                print(f"API Error: {response.status_code} - {response.text}")
                break

            data = response.json()
            if "errors" in data:
                print(f"GraphQL Error: {data['errors']}")
                break

            products_data = data.get("data", {}).get("site", {}).get("products", {})
            edges = products_data.get("edges", [])

            for edge in edges:
                product = edge.get("node", {})
                style_sku = product.get("sku", "").strip()
                product_name = product.get("name", "").strip()

                if style_sku in TARGET_STYLES:
                    variants = product.get("variants", {}).get("edges", [])
                    for v_edge in variants:
                        v_node = v_edge.get("node", {})
                        variant_sku = v_node.get("sku", "").strip()
                        
                        colour, size = parse_sku_attributes(variant_sku)

                        inv_data = v_node.get("inventory", {}).get("aggregated", {})
                        stock = inv_data.get("availableToSell", 0) if inv_data else 0

                        if variant_sku:
                            scanned_items[variant_sku] = {
                                "style_sku": style_sku,
                                "product_name": product_name,
                                "colour_name": colour,
                                "size_name": size,
                                "stock": stock
                            }

            page_info = products_data.get("pageInfo", {})
            has_next_products = page_info.get("hasNextPage", False)
            product_cursor = page_info.get("endCursor")

        except Exception as e:
            print(f"Request Exception: {e}")
            break

    return scanned_items


def sync_inventory_and_alert():
    print("Fetching catalog data with fully translated attributes from AS Colour Australia...")
    current_inventory = fetch_monitored_inventory()
    print(f"Scanned {len(current_inventory)} target variants from API.")

    if not current_inventory:
        print("No inventory data retrieved. Exiting.")
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

        if prev_data is not None:
            was_low = prev_data.get("is_below_25", False)
            if is_low and (not was_low or prev_data.get("stock_count") != stock):
                alerts.append(f"⚠️ **Low Stock Alert**: `{sku}` ({item_data['product_name']} - {item_data['colour_name']} / {item_data['size_name']}) -> Stock: **{stock}** units")

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

    if upsert_payload:
        supabase.table("inventory_states").upsert(upsert_payload).execute()

    if DISCORD_WEBHOOK_URL:
        if alerts:
            for i in range(0, len(alerts), 10):
                chunk = "\n".join(alerts[i:i + 10])
                requests.post(DISCORD_WEBHOOK_URL, json={"content": chunk})
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
            selected_message = random.choice(fun_messages)
            requests.post(DISCORD_WEBHOOK_URL, json={"content": selected_message})
            print("Discord alert delivered successfully.")


if __name__ == "__main__":
    sync_inventory_and_alert()
