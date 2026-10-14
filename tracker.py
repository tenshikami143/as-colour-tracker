import os
from datetime import datetime
from playwright.sync_api import sync_playwright
import requests

SUPABASE_URL = os.getenv("SUPABASE_URL", "").strip()
SUPABASE_KEY = os.getenv("SUPABASE_KEY", "").strip()
DISCORD_WEBHOOK_URL = os.getenv("DISCORD_WEBHOOK_URL", "").strip()

TARGET_STYLES = [
    "4001", "4026", "4051", "4008", "4079", "4006", "4125", "4161", "4160", "1117", "1160"
]

GRAPHQL_QUERY = """
query GetStyleInventory($sku: String!) {
  site {
    products(sku: $sku) {
      edges {
        node {
          entityId
          sku
          name
          variants {
            edges {
              node {
                entityId
                sku
                upc
                inventory {
                  isInStock
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

def upsert_to_supabase(records):
    if not records:
        return
    url = f"{SUPABASE_URL}/rest/v1/inventory_states"
    headers = {
        "apikey": SUPABASE_KEY,
        "Authorization": f"Bearer {SUPABASE_KEY}",
        "Content-Type": "application/json",
        "Prefer": "resolution=merge-duplicates"
    }
    response = requests.post(url, json=records, headers=headers)
    if response.status_code not in [200, 201, 204]:
        print(f"Supabase Sync Error: {response.status_code} - {response.text}")
    else:
        print(f"Successfully upserted {len(records)} variant records to Supabase.")

def send_discord_alert(message):
    if not DISCORD_WEBHOOK_URL:
        return
    requests.post(DISCORD_WEBHOOK_URL, json={"content": message})

def main():
    print("🚀 PLAYWRIGHT TRACKER STARTED")
    all_records = []
    
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        context = browser.new_context()
        page = context.new_page()
        
        # Navigate to the site to establish session context
        page.goto("https://www.ascolour.com.au/")
        
        for style in TARGET_STYLES:
            print(f"Fetching catalog data for style: {style}")
            try:
                # Execute GraphQL fetch directly inside the browser session context
                response = page.request.post(
                    "https://www.ascolour.com.au/graphql",
                    data={
                        "query": GRAPHQL_QUERY,
                        "variables": {"sku": style}
                    }
                )
                
                if response.status != 200:
                    print(f"API Error for style {style}: {response.status}")
                    continue
                    
                data = response.json()
                products = data.get("data", {}).get("site", {}).get("products", {}).get("edges", [])
                
                for p_edge in products:
                    product = p_edge.get("node", {})
                    style_name = product.get("name")
                    
                    for v_edge in product.get("variants", {}).get("edges", []):
                        v_node = v_edge.get("node", {})
                        inventory_info = v_node.get("inventory", {}) or {}
                        is_in_stock = inventory_info.get("isInStock", False)
                        stock_indicator = 1 if is_in_stock else 0
                        
                        all_records.append({
                            "variant_id": v_node.get("sku"),
                            "style_sku": product.get("sku"),
                            "product_name": style_name,
                            "stock_count": stock_indicator,
                            "is_below_25": stock_indicator < 25,
                            "updated_at": datetime.utcnow().isoformat()
                        })
            except Exception as e:
                print(f"Error fetching SKU {style}: {e}")
                
        browser.close()
        
    if all_records:
        upsert_to_supabase(all_records)
        print(f"Scanned {len(all_records)} target variants from API.")
    else:
        print("No inventory data retrieved.")
        send_discord_alert("⚠️ Inventory Tracker warning: No inventory data retrieved.")

if __name__ == "__main__":
    main()
