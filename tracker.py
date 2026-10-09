import os
import random
import requests
from datetime import datetime, timezone
from supabase import create_client, Client

# Initialize Supabase credentials
SUPABASE_URL = os.environ.get("SUPABASE_URL")
SUPABASE_KEY = os.environ.get("SUPABASE_KEY")
supabase: Client = create_client(SUPABASE_URL, SUPABASE_KEY)

# AS Colour GraphQL API credentials
GRAPHQL_URL = "https://www.ascolour.com/graphql"
API_TOKEN = os.environ.get("ASCOLOUR_GRAPHQL_TOKEN") or os.environ.get("AS_COLOUR_API_TOKEN")

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


def fetch_monitored_inventory():
    headers = {
        "Content-Type": "application/json"
    }
    if API_TOKEN:
        headers["X-Auth-Token"] = API_TOKEN

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

                if style_sku in TARGET_STYLES:
                    variants = product.get("variants", {}).get("edges", [])
                    for v_edge in variants:
                        v_node = v_edge.get("node", {})
                        variant_sku = v_node.get("sku", "").strip()
                        
                        inv_data = v_node.get("inventory", {}).get("aggregated", {})
                        stock = inv_data.get("availableToSell", 0) if inv_data else 0

                        if variant_sku:
                            scanned_items[variant_sku] = {
                                "style_sku": style_sku,
                                "product_name": product.get("name"),
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
    print("Fetching catalog data for 73 target styles...")
    current_inventory = fetch_monitored_inventory()
    print(f"Scanned {len(current_inventory)} target variants from API.")

    if not current_inventory:
        print("No inventory data retrieved. Exiting.")
        return

    # Fetch existing states from Supabase
    response = supabase.table("inventory_states").select("*").execute()
    previous_states = {item["sku"]: item for item in response.data} if response.data else {}

    alerts = []
    upsert_payload = []
    now_iso = datetime.now(timezone.utc).isoformat()

    for sku, item_data in current_inventory.items():
        stock = item_data["stock"]
        prev_data = previous_states.get(sku)
        is_low = stock < 25
        was_low = prev_data.get("is_low_stock", False) if prev_data else False

        # Alert logic: trigger alert if stock dropped below 25 or changed while low
        if is_low and (not was_low or (prev_data and prev_data.get("stock_level") != stock)):
            alerts.append(f"⚠️ **Low Stock Alert**: `{sku}` ({item_data['product_name']}) -> Stock: **{stock}** units")

        upsert_payload.append({
            "sku": sku,
            "style_sku": item_data["style_sku"],
            "product_name": item_data["product_name"],
            "stock_level": stock,
            "is_low_stock": is_low,
            "last_updated": now_iso
        })

    # Batch upsert to Supabase
    if upsert_payload:
        supabase.table("inventory_states").upsert(upsert_payload).execute()

    # Dispatch alerts or send fun message to Discord
    if DISCORD_WEBHOOK_URL:
        if alerts:
            for i in range(0, len(alerts), 10):
                chunk = "\n".join(alerts[i:i + 10])
                requests.post(DISCORD_WEBHOOK_URL, json={"content": chunk})
            print(f"Sent {len(alerts)} alert(s) to Discord.")
        else:
            # No changes found - send fun cheerful notification
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
                "⚡ **Fully Charged:** Stock levels across all monitored items are looking super healthy today! ⚡",
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
