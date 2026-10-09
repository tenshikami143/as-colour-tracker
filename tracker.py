import os
import random
import requests
from datetime import datetime, timezone
from supabase import create_client, Client

# Initialize Supabase credentials
SUPABASE_URL = os.environ.get("SUPABASE_URL")
SUPABASE_KEY = os.environ.get("SUPABASE_KEY")
supabase: Client = create_client(SUPABASE_URL, SUPABASE_KEY)

# AS Colour GraphQL API credentials with safety fallback
GRAPHQL_URL = os.environ.get("AS_COLOUR_GRAPHQL_URL") or "https://www.ascolour.com.au/api/graphql"
API_TOKEN = os.environ.get("AS_COLOUR_API_TOKEN")

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
        "Content-Type": "application/json",
        "Authorization": f"Bearer {API_TOKEN}" if API_TOKEN else ""
    }
    
    scanned_items = {}
    has_next_products = True
    product_cursor = None

    query = """
    query FetchCatalog($productCursor: String,$variantCursor: String) {
      site {
        products(first: 50, after: $productCursor) {
          pageInfo { hasNextPage endCursor }
          edges {
            node {
              id name
              variants(first: 50, after: $variantCursor) {
                pageInfo { hasNextPage endCursor }
                edges {
                  node {
                    id sku
                    options {
                      edges {
                        node {
                          displayName
                          values { edges { node { label } } }
                        }
                      }
                    }
                    inventory { aggregated { availableToSell } }
                  }
                }
              }
            }
          }
        }
      }
    }
    """

    print(f"Fetching catalog data for {len(TARGET_STYLES)} target styles...")

    while has_next_products:
        variables = {"productCursor": product_cursor, "variantCursor": None}
        response = requests.post(GRAPHQL_URL, json={"query": query, "variables": variables}, headers=headers)
        if response.status_code != 200:
            print(f"API Error: {response.status_code} - {response.text}")
            break

        products_data = response.json().get("data", {}).get("site", {}).get("products", {})
        
        for product_edge in products_data.get("edges", []):
            product_node = product_edge["node"]
            has_next_variants = True
            variant_cursor = None
            
            while has_next_variants:
                if variant_cursor:
                    var_vars = {"productCursor": product_cursor, "variantCursor": variant_cursor}
                    v_res = requests.post(GRAPHQL_URL, json={"query": query, "variables": var_vars}, headers=headers)
                    if v_res.status_code != 200: 
                        break
                    p_edges = v_res.json().get("data", {}).get("site", {}).get("products", {}).get("edges", [])
                    target_p = next((p["node"] for p in p_edges if p["node"]["id"] == product_node["id"]), None)
                    if not target_p: 
                        break
                    variants_data = target_p.get("variants", {})
                else:
                    variants_data = product_node.get("variants", {})

                for edge in variants_data.get("edges", []):
                    v_node = edge["node"]
                    inv = v_node.get("inventory", {})
                    full_sku = v_node.get("sku", "UNKNOWN-SKU")
                    
                    base_sku = full_sku.split("-")[0].upper() if "-" in full_sku else full_sku.upper()
                    
                    # Filter out non-monitored styles
                    if base_sku.lower() not in [s.lower() for s in TARGET_STYLES]:
                        continue
                    
                    colour, size = "UNKNOWN", "OS"
                    for opt_edge in v_node.get("options", {}).get("edges", []):
                        opt_node = opt_edge.get("node", {})
                        opt_name = opt_node.get("displayName", "").lower()
                        vals = opt_node.get("values", {}).get("edges", [])
                        if vals:
                            label = vals[0].get("node", {}).get("label", "")
                            if "color" in opt_name or "colour" in opt_name: 
                                colour = label
                            elif "size" in opt_name: 
                                size = label

                    if colour == "UNKNOWN" and "-" in full_sku:
                        parts = full_sku.split("-")
                        colour = parts[1] if len(parts) > 1 else "UNKNOWN"
                        size = "-".join(parts[2:]) if len(parts) > 2 else size

                    stock_qty = 999 
                    if inv and inv.get("aggregated") and inv["aggregated"].get("availableToSell") is not None:
                        stock_qty = int(inv["aggregated"]["availableToSell"])

                    key = (base_sku, colour, size)
                    scanned_items[key] = stock_qty

                var_page = variants_data.get("pageInfo", {})
                has_next_variants = var_page.get("hasNextPage", False)
                variant_cursor = var_page.get("endCursor")

        prod_page = products_data.get("pageInfo", {})
        has_next_products = prod_page.get("hasNextPage", False)
        product_cursor = prod_page.get("endCursor")

    return scanned_items

def send_discord_alert(new_low_stock, restocked):
    if not DISCORD_WEBHOOK_URL:
        print("No DISCORD_WEBHOOK_URL configured. Skipping Discord alert.")
        return

    fields = []
    
    if new_low_stock:
        desc = "\n".join([f"• **{item['variant_id']}** - {item['colour_name']} / {item['size_name']} (`{item['stock_count']}` left)" for item in new_low_stock[:15]])
        if len(new_low_stock) > 15:
            desc += f"\n*...and {len(new_low_stock) - 15} more*"
        fields.append({
            "name": "🔻 New Low Stock Alerts (< 25)",
            "value": desc,
            "inline": False
        })

    if restocked:
        desc = "\n".join([f"• **{item['variant_id']}** - {item['colour_name']} / {item['size_name']} (Now `{item['stock_count']}`)" for item in restocked[:15]])
        if len(restocked) > 15:
            desc += f"\n*...and {len(restocked) - 15} more*"
        fields.append({
            "name": "🟢 Restocked Items (>= 25)",
            "value": desc,
            "inline": False
        })

    # Actionable alerts (Low stock or Restocks found)
    if fields:
        payload = {
            "username": "AS Colour Inventory Bot",
            "embeds": [{
                "title": "📦 Daily Inventory Status Update (Past 24 Hours)",
                "color": 15158332 if new_low_stock else 3066993,
                "fields": fields,
                "timestamp": datetime.now(timezone.utc).isoformat()
            }]
        }
    # No changes found - send fun cheerful notification
    else:
        fun_messages = [
            "☀️ **All Clear!** No inventory fires to extinguish today. Grab a coffee and enjoy the smooth sailing! ☕",
            "✨ **Smooth Operations:** Stock levels are rock solid across all 73 styles today. Keep up the awesome work! 🚀",
            "🎉 **Quiet Day in the Catalog:** Zero new low-stock items and zero restocks today. Everything's running like clockwork! ⏰",
            "🏖️ **Inventory Status:** All quiet on the stock front today! Time to relax or tackle the fun stuff on your list. ☀️",
            "🤖 **Beep Boop:** Scanned thousands of variants and found no threshold changes! Your inventory is chillin' today. 😎",
            "🍩 **Zero Drama:** No stock alerts today! Treat yourself to a donut (or two) for a job well done. 🍩",
            "🛡️ **Fort Knox Inventory:** All 73 monitored styles are fully stocked and holding the line today! 💪",
            "🌴 **Smooth Sailing:** Not a single SKU dropped below 25 today. Breathe easy and have a fantastic morning! 🌊",
            "🎯 **Bullseye!** Scanned every single variant with 100% precision—no restocks or low-stock drops to report. 🎯",
            "🧘 **Inventory Zen:** Peaceful vibes in the database today. Everything is balanced and right where it needs to be. 🧘‍♂️",
            "🚀 **All Systems Nominal:** The catalog check passed with zero stock alerts. Houston, we have zero problems! 👨‍🚀",
            "☕ **Pour Another Cup:** Everything is looking great across the catalog today. Sit back and enjoy your morning brew! ☕",
            "🌟 **Golden Ratio:** No new low stock, no restock chaos. Just a pristine, smooth inventory run! ✨",
            "💤 **Sleeping Soundly:** The inventory warehouse is completely undisturbed today. Shhh, don't wake the tees! 👕💤",
            "🎈 **Flawless Run:** Today's scan went smoother than butter. All monitored styles are safe and sound! 🧈",
            "🧭 **On Course:** Staying steady on the high seas! Zero inventory emergencies detected in the last 24 hours. ⚓",
            "🕶️ **Too Cool for Stock Outs:** Stock levels are staying comfortable today. Put on those shades and enjoy your morning! 😎",
            "🏆 **Victory Lap:** No low-stock flags raised today! You're winning the inventory game. 🏆",
            "🥑 **Holy Guacamole!** Another day, another completely clean inventory report. Keep crushing it! 🥑",
            "🕹️ **Level Complete:** Daily scan finished with 0 errors and 0 inventory alerts! Maximum score achieved. 🎮",
            "🌈 **Sunshine & Rainbows:** Nothing but healthy stock numbers across all 73 styles today! 🌈",
            "📦 **Warehouse Whispers:** The shelves are quiet today. All target variants are staying right where they belong! 🤫",
            "🚀 **Cruising Altitude:** 30,000 feet up and zero turbulence in the stock room today! ✈️",
            "🎶 **Sweet Harmony:** All styles are in perfect sync today. Not a single stock alarm sounded! 🎵",
            "🧙‍♂️ **Inventory Wizardry:** Magic was worked overnight—no low stock, no missing sizes! 🪄",
            "🧊 **Chill Zone:** Stock counts are cool, calm, and collected. Zero action required from you today! 🧊",
            "🎨 **Picture Perfect:** The inventory numbers look like a masterpiece today. Zero alerts on the horizon! 🖼️",
            "🔋 **Fully Charged:** Stock levels across all monitored items are looking super healthy today! ⚡",
            "🥇 **Gold Medal Day:** Clean sweep! All variants passed inspection with flying colors. 🥇",
            "🌾 **Smooth Harvest:** The inventory fields are bountiful and stable today. Enjoy the peace! 🌾",
            "⚡ **Lightning Fast & Quiet:** Scanned thousands of variants in seconds—zero stock threats found! ⚡",
            "🍀 **Lucky Days:** No surprise out-of-stock events today. Lady Luck is on our side! 🍀",
            "🚗 **Smooth Highway:** Cruising on green lights all morning. No inventory speed bumps today! 🚦",
            "🏰 **Castle Guarded:** The inventory fortress remains untouched today. All stock holds firm! 🏰",
            "🍕 **Pizza Time:** No inventory crises to handle today, so you've earned a pizza slice later! 🍕"
        ]
        
        payload = {
            "username": "AS Colour Inventory Bot",
            "embeds": [{
                "title": "✅ Daily Inventory Check: All Quiet!",
                "description": random.choice(fun_messages),
                "color": 3447003,
                "timestamp": datetime.now(timezone.utc).isoformat()
            }]
        }

    res = requests.post(DISCORD_WEBHOOK_URL, json=payload)
    if res.status_code in [200, 204]:
        print("Discord alert delivered successfully.")
    else:
        print(f"Failed to send Discord alert: {res.status_code} - {res.text}")

def main():
    scanned_items = fetch_monitored_inventory()
    print(f"Scanned {len(scanned_items)} target variants from API.")

    # Retrieve active DB records
    existing_records = supabase.table("inventory_states").select("*").execute().data
    db_map = {(r["variant_id"], r["colour_name"], r["size_name"]): r for r in existing_records}

    new_low_stock = []
    restocked = []
    upsert_payload = []
    now_iso = datetime.now(timezone.utc).isoformat()

    for (style, colour, size), current_stock in scanned_items.items():
        db_item = db_map.get((style, colour, size))
        prev_stock = db_item.get("stock_count") if db_item else None

        # Newly dropped below 25
        if current_stock < 25:
            if prev_stock is None or prev_stock >= 25:
                new
