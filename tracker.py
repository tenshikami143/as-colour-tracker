import os
import random
import requests
import psycopg2
import psycopg2.extras
from datetime import datetime, timezone

# Initialize Database Connection string
DATABASE_URL = os.environ.get("DATABASE_URL")

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

COLOUR_MAP = {
    "Snset": "Sunset", "Buttr": "Butter", "Orchi": "Orchid", "Powdr": "Powder",
    "Walnt": "Walnut", "GRE_M": "Grey Marle", "NAT_R": "Natural", "BLACK": "Black",
    "WHITE": "White", "NAVY": "Navy", "PINK": "Pink", "SAGE": "Sage",
    "ARMY": "Army", "BONE": "Bone", "BURGU": "Burgundy", "CAMEL": "Camel",
    "CHA_P": "Charcoal", "COAL": "Coal", "CREAM": "Cream", "DAR_C": "Dark Charcoal",
    "FORES": "Forest Green", "KEL_G": "Kelly Green", "KHAKI": "Khaki",
    "LBRTY": "Liberty", "MUSH": "Mushroom", "MUSTA": "Mustard", "ORANG": "Orange"
}

SIZE_MAP = {
    "P-Os": "One Size", "S1218": "12-18 Months", "T1824": "18-24 Months",
    "P0-3": "0-3 Months", "Q3-6": "3-6 Months", "R6-12": "6-12 Months",
    "OS": "One Size", "XSM": "XS", "SML": "Small", "MED": "Medium",
    "LGE": "Large", "XLG": "XL", "2XL": "2XL", "3XL": "3XL"
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
    headers = {"Content-Type": "application/json"}
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
        variables = {"productCursor": product_cursor, "variantCursor": None}
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

    conn = psycopg2.connect(DATABASE_URL)
    cursor = conn.cursor(cursor_factory=psycopg2.extras.DictCursor)

    try:
        cursor.execute("SELECT variant_id, stock_count, is_below_25 FROM inventory_states;")
        previous_states = {row["variant_id"]: row for row in cursor.fetchall()}

        alerts = []
        now_iso = datetime.now(timezone.utc).isoformat()

        upsert_data = []
        for sku, item_data in current_inventory.items():
            stock = item_data["stock"]
            prev_data = previous_states.get(sku)
            is_low = stock < 25

            if prev_data is not None:
                was_low = prev_data.get("is_below_25", False)
                if is_low and (not was_low or prev_data.get("stock_count") != stock):
                    alerts.append(f"⚠️ **Low Stock Alert**: `{sku}` ({item_data['product_name']} - {item_data['colour_name']} / {item_data['size_name']}) -> Stock: **{stock}** units")

            upsert_data.append((
                sku,
                item_data["style_sku"],
                item_data["product_name"],
                item_data["colour_name"],
                item_data["size_name"],
                stock,
                is_low,
                now_iso
            ))

        upsert_query = """
            INSERT INTO inventory_states (variant_id, style_sku, product_name, colour_name, size_name, stock_count, is_below_25, updated_at)
            VALUES %s
            ON CONFLICT (variant_id) 
            DO UPDATE SET 
                stock_count = EXCLUDED.stock_count,
                is_below_25 = EXCLUDED.is_below_25,
                updated_at = EXCLUDED.updated_at;
        """
        
        psycopg2.extras.execute_values(cursor, upsert_query, upsert_data)
        conn.commit()

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
                    "🍩 **Zero Drama:** No stock alerts today! Treat yourself to a donut (or two) for a job well done. 🍩"
                ]
                requests.post(DISCORD_WEBHOOK_URL, json={"content": random.choice(fun_messages)})
                print("Discord alert delivered successfully.")

    finally:
        cursor.close()
        conn.close()


if __name__ == "__main__":
    sync_inventory_and_alert()
