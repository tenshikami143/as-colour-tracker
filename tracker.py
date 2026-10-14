print("🚀 TRACKER SCRIPT STARTED SUCCESSFULLY")

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
    "ARC_B": "Arctic Blue",
    "ARMY": "Army",
    "ASH": "Ash",
    "ASP_M": "Asphalt Marle",
    "BBGUM": "Bubblegum",
    "BL/WH": "Black/White",
    "BLACK": "Black",
    "BLA_A": "Black Acid",
    "BLU_A": "Blue Acid",
    "BONE": "Bone",
    "BRI_R": "Bright Royal",
    "BURGU": "Burgundy",
    "BUTTR": "Butter",
    "CARDI": "Cardinal",
    "CAR_B": "Carolina Blue",
    "CHARC": "Charcoal",
    "CHA_P": "Charcoal",
    "COAL": "Coal",
    "COBLT": "Cobalt",
    "COPPR": "Copper",
    "CREAM": "Cream",
    "ECRU": "Ecru",
    "EMRLD": "Emerald",
    "EUCAL": "Eucalyptus",
    "FORES": "Forest Green",
    "F_ARM": "Faded Army",
    "F_BLK": "Faded Black",
    "F_BLU": "Faded Blue",
    "F_DUS": "Faded Dust",
    "F_MUS": "Faded Mustard",
    "F_ROS": "Faded Rose",
    "F_SLT": "Faded Slate",
    "GOLD": "Gold",
    "GRE_M": "Grey Marle",
    "GR_AS": "Grey Acid",
    "H_PNK": "Hot Pink",
    "KEL_G": "Kelly Green",
    "KHAKI": "Khaki",
    "LAGON": "Lagoon",
    "MID_B": "Mid Blue",
    "MOSS": "Moss Green",
    "NAT_R": "Natural",
    "NAVY": "Navy",
    "ORCHD": "Orchid",
    "ORCHI": "Orchid",
    "PETRL": "Petrol Blue",
    "PINE": "Pine Green",
    "PINK": "Pink",
    "POWDR": "Powder",
    "P_BLU": "Powder Blue",
    "P_PNK": "Powder Pink",
    "RED": "Red",
    "SAGE": "Sage",
    "SNSET": "Sunset",
    "Snset": "Sunset",
    "STORM": "Storm",
    "WALNT": "Walnut",
    "WH/RE": "White/Red",
    "WHITE": "White",
    "WHI_B": "White/Black",
    "WHI_M": "White Marle",
    "WHI_N": "White/Navy",
    "DAR_C": "Dark Charcoal",
    "LBRTY": "Liberty",
    "MUSH": "Mushroom",
    "MUSTA": "Mustard",
    "ORANG": "Orange"
}

SIZE_MAP = {
    "F-XS": "XS",
    "G-S": "Small",
    "H-M": "Medium",
    "I-L": "Large",
    "J-XL": "XL",
    "K-2XL": "2XL",
    "L-3XL": "3XL",
    "M-4XL": "4XL",
    "N-5XL": "5XL",
    "P-Os": "One Size",
    "OS": "One Size",
    "P0-3": "0-3 Months",
    "Q3-6": "3-6 Months",
    "R6-12": "6-12 Months",
    "00-03": "0-3 Months",
    "03-06": "3-6 Months",
    "06-12": "6-12 Months",
    "12-18": "12-18 Months",
    "18-24": "18-24 Months",
    "S1218": "12-18 Months",
    "T1824": "18-24 Months",
    "XSM": "XS",
    "SML": "Small",
    "MED": "Medium",
    "LGE": "Large",
    "XLG": "XL",
    "2XL": "2XL",
    "3XL": "3XL"
}


def parse_sku_attributes(variant_sku):
    normalized_sku = variant_sku.replace("-CHARC-", "-CHA_P-")
    
    parts = normalized_sku.split("-")
    if len(parts) >= 3:
        raw_colour = parts[1]
        raw_size = "-".join(parts[2:])
        
        colour = COLOUR_MAP.get(raw_colour, raw_colour.replace("_", " ").title())
        size = SIZE_MAP.get(raw_size, raw_size.replace("_", " ").title())
        
        return colour, size, normalized_sku
    return "N/A", "N/A", variant_sku


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
            days_left = (eat - now_ts) / 86400
            
            if days_left <= 0:
                msg = "🚨 **MAYDAY! THE TOKEN HAS OFFICIALLY KICKED THE BUCKET!** 🪦 AS Colour has locked us out in the cold. Drop what you're doing, grab a fresh token, and slap it into GitHub Secrets before the tees revolt!"
                requests.post(DISCORD_WEBHOOK_URL, json={"content": msg})
                print("Alert sent: Token expired.")
            elif days_left <= 5:
                msg = f"⏳ **TICK-TOCK!** Your AS Colour token is aging like fine milk and expires in **{round(days_left, 1)} days**. Get ahead of the chaos and grab a fresh token before this ship hits the iceberg!"
                requests.post(DISCORD_WEBHOOK_URL, json={"content": msg})
                print(f"Alert sent: Token expires in {round(days_left, 1)} days.")
    except Exception as e:
        print(f"Error checking token expiration: {e}")


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
              variants(first: 100) {
                edges {
                  node {
                    id
                    sku
                    inventory {
                      aggregated {
                        availableToSell
                        onHand
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
            "productCursor": product_cursor
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
                style_sku = product.get("sku", "").strip().lower()
                product_name = product.get("name", "").strip()

                is_target = any(style_sku.startswith(target.lower()) for target in TARGET_STYLES)

                if is_target:
                    variants = product.get("variants", {}).get("edges", [])
                    for v_edge in variants:
                        v_node = v_edge.get("node", {})
                        raw_v_sku = v_node.get("sku", "").strip()
                        
                        colour, size, variant_sku = parse_sku_attributes(raw_v_sku)

                        # Extract stock using aggregated subfields
                        inv_data = v_node.get("inventory") or {}
                        agg_data = inv_data.get("aggregated") if isinstance(inv_data, dict) else None
                        
                        stock = 0
                        if isinstance(agg_data, dict):
                            if agg_data.get("availableToSell") is not None:
                                stock = agg_data.get("availableToSell")
                            elif agg_data.get("onHand") is not None:
                                stock = agg_data.get("onHand")

                        # Detailed debug log for sample style 4001 Berry item
                        if "4001" in style_sku and "BERRY" in variant_sku:
                            print(f"DEBUG BERRY INVENTORY: inv={inv_data}, stock={stock}")

                        if variant_sku:
                            scanned_items[variant_sku] = {
                                "style_sku": style_sku.upper(),
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
    check_token_expiration_and_alert()

    print("Fetching catalog data with fully translated attributes from AS Colour Australia...")
    current_inventory = fetch_monitored_inventory()
    print(f"Scanned {len(current_inventory)} target variants from API.")

    if not current_inventory:
        print("No inventory data retrieved. Sending scan failure alert.")
        if DISCORD_WEBHOOK_URL:
            fail_msg = "🚨 **INVENTORY EMERGENCY!** The tracker went looking for tees and found *absolute emptiness* (0 items). Either AS Colour put up a firewall or ghosts stole the warehouse. Check your token/API immediately!"
            requests.post(DISCORD_WEBHOOK_URL, json={"content": fail_msg})
        return

    print("Fetching previous states from Supabase...")
    response = supabase.table("inventory_states").select("*").execute()
    
    previous_states = {}
    if response.data:
        for item in response.data:
            v_id = item.get("variant_id")
            previous_states[v_id] = item

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
            elif not is_low and was_low:
                alerts.append(f"🎉 **Restock Alert**: `{sku}` ({item_data['product_name']} - {item_data['colour_name']} / {item_data['size_name']}) -> Stock recovered to **{stock}** units!")

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
        print(f"Upserting {len(upsert_payload)} items into Supabase...")
        supabase.table("inventory_states").upsert(upsert_payload).execute()

    print(f"Webhook URL loaded? {'YES' if DISCORD_WEBHOOK_URL else 'NO (Check secret name!)'}")

    if DISCORD_WEBHOOK_URL:
        if alerts:
            print(f"Found {len(alerts)} alerts to send.")
            for alert in alerts:
                res = requests.post(DISCORD_WEBHOOK_URL, json={"content": alert})
                print(f"Discord response code: {res.status_code}")
        else:
            print("No alerts generated. Sending fallback confirmation...")
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
            res = requests.post(DISCORD_WEBHOOK_URL, json={"content": selected_message})
            print(f"Discord fallback response code: {res.status_code}")


if __name__ == "__main__":
    sync_inventory_and_alert()
