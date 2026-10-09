import os
import requests
from datetime import datetime, timezone, timedelta
from supabase import create_client, Client

# Initialize Supabase credentials
SUPABASE_URL = os.environ.get("SUPABASE_URL")
SUPABASE_KEY = os.environ.get("SUPABASE_KEY")
supabase: Client = create_client(SUPABASE_URL, SUPABASE_KEY)

# AS Colour GraphQL API credentials
GRAPHQL_URL = os.environ.get("AS_COLOUR_GRAPHQL_URL")
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

    print("Fetching catalog data from AS Colour...")

    while has_next_products:
        variables = {"productCursor": product_cursor, "variantCursor": None}
        response = requests.post(GRAPHQL_URL, json={"query": query, "variables": variables}, headers=headers)
        if response.status_code != 200:
            print(f"API Error: {response.status_code}")
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
                    if v_res.status_code != 200: break
                    p_edges = v_res.json().get("data", {}).get("site", {}).get("products", {}).get("edges", [])
                    target_p = next((p["node"] for p in p_edges if p["node"]["id"] == product_node["id"]), None)
                    if not target_p: break
                    variants_data = target_p.get("variants", {})
                else:
                    variants_data = product_node.get("variants", {})

                for edge in variants_data.get("edges", []):
                    v_node = edge["node"]
                    inv = v_node.get("inventory", {})
                    full_sku = v_node.get("sku", "UNKNOWN-SKU")
                    
                    base_sku = full_sku.split("-")[0].upper() if "-" in full_sku else full_sku.upper()
                    
                    if base_sku.lower() not in [s.lower() for s in TARGET_STYLES]:
                        continue
                    
                    colour, size = "UNKNOWN", "OS"
                    for opt_edge in v_node.get("options", {}).get("edges", []):
                        opt_node = opt_edge.get("node", {})
                        opt_name = opt_node.get("displayName", "").lower()
                        vals = opt_node.get("values", {}).get("edges", [])
                        if vals:
                            label = vals[0].get("node", {}).get("label", "")
                            if "color" in opt_name or "colour" in opt_name: colour = label
                            elif "size" in opt_name: size = label

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
        print("No DISCORD_WEBHOOK_URL set. Skipping notification.")
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

    if not fields:
        print("No 24h inventory changes to notify.")
        return

    payload = {
        "username": "AS Colour Inventory Bot",
        "embeds": [{
            "title": "📦 Daily Inventory Status Update (Past 24 Hours)",
            "color": 15158332 if new_low_stock else 3066993,
            "fields": fields,
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
    print(f"Scanned {len(scanned_items)} variants from API.")

    # Fetch current DB state
    existing_records = supabase.table("inventory_states").select("*").execute().data
    db_map = {(r["variant_id"], r["colour_name"], r["size_name"]): r for r in existing_records}

    new_low_stock = []
    restocked = []
    upsert_payload = []
    now_iso = datetime.now(timezone.utc).isoformat()

    for (style, colour, size), current_stock in scanned_items.items():
        db_item = db_map.get((style, colour, size))
        prev_stock = db_item.get("stock_count") if db_item else None

        # Condition 1: Newly dropped below 25
        if current_stock < 25:
            if prev_stock is None or prev_stock >= 25:
                new_low_stock.append({
                    "variant_id": style, "colour_name": colour, "size_name": size, "stock_count": current_stock
                })
            
            upsert_payload.append({
                "variant_id": style,
                "colour_name": colour,
                "size_name": size,
                "stock_count": current_stock,
                "previous_stock_count": prev_stock,
                "is_below_25": True,
                "updated_in_deconetwork": False,
                "last_changed_at": now_iso
            })

        # Condition 2: Restocked to 25+ from low stock
        elif current_stock >= 25 and db_item and db_item.get("is_below_25"):
            restocked.append({
                "variant_id": style, "colour_name": colour, "size_name": size, "stock_count": current_stock
            })
            # Remove restocked item from low-stock dashboard
            supabase.table("inventory_states").delete().match({
                "variant_id": style, "colour_name": colour, "size_name": size
            }).execute()

    # Save low-stock items to Supabase
    if upsert_payload:
        supabase.table("inventory_states").upsert(upsert_payload).execute()

    # Send consolidated 24h Discord alert
    send_discord_alert(new_low_stock, restocked)

if __name__ == "__main__":
    main()
