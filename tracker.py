import os
import requests
from datetime import datetime

# Environment Variables from GitHub Actions Secrets
SUPABASE_URL = os.getenv("SUPABASE_URL")
SUPABASE_KEY = os.getenv("SUPABASE_KEY")
ASCOLOUR_GRAPHQL_TOKEN = os.getenv("ASCOLOUR_GRAPHQL_TOKEN")
DISCORD_WEBHOOK_URL = os.getenv("DISCORD_WEBHOOK_URL")

# AS Colour Public Storefront / B2B GraphQL Endpoint
GRAPHQL_URL = "https://www.ascolour.com.au/graphql"  # Update if using a specific storefront domain endpoint

HEADERS = {
    "Content-Type": "application/json",
    "Authorization": f"Bearer {ASCOLOUR_GRAPHQL_TOKEN}" if ASCOLOUR_GRAPHQL_TOKEN else ""
}

# Target 73 Style SKUs (modify/expand list as needed based on your parser setup)
TARGET_STYLES = [
    "4001", "4026", "4051", "4008", "4079", "4006", "4125", "4161", "4160", "1117", "1160"
    # ... include your full 73 style list here
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

def fetch_inventory_for_style(style_sku):
    payload = {
        "query": GRAPHQL_QUERY,
        "variables": {"sku": style_sku}
    }
    
    try:
        response = requests.post(GRAPHQL_URL, json=payload, headers=HEADERS, timeout=30)
        if response.status_code != 200:
            print(f"API Error: {response.status_code} - {response.text}")
            return []
            
        data = response.json()
        if "errors" in data:
            print(f"GraphQL Error for SKU {style_sku}: {data['errors']}")
            return []
            
        products = data.get("data", {}).get("site", {}).get("products", {}).get("edges", [])
        variants_found = []
        
        for p_edge in products:
            product = p_edge.get("node", {})
            style_name = product.get("name")
            
            for v_edge in product.get("variants", {}).get("edges", []):
                v_node = v_edge.get("node", {})
                inventory_info = v_node.get("inventory", {}) or {}
                
                # Check isInStock boolean or fallback value
                is_in_stock = inventory_info.get("isInStock", False)
                stock_indicator = 1 if is_in_stock else 0
                
                variants_found.append({
                    "variant_id": v_node.get("sku"),
                    "style_sku": product.get("sku"),
                    "product_name": style_name,
                    "stock_count": stock_indicator,
                    "is_below_25": stock_indicator < 25,
                    "updated_at": datetime.utcnow().isoformat()
                })
                
        return variants_found
    except Exception as e:
        print(f"Exception encountered while fetching SKU {style_sku}: {e}")
        return []

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
    payload = {"content": message}
    requests.post(DISCORD_WEBHOOK_URL, json=payload)

def main():
    print("🚀 TRACKER SCRIPT STARTED SUCCESSFULLY")
    all_records = []
    
    for style in TARGET_STYLES:
        print(f"Fetching catalog data for style: {style}")
        variants = fetch_inventory_for_style(style)
        if variants:
            all_records.extend(variants)
            
    if all_records:
        upsert_to_supabase(all_records)
        print(f"Scanned {len(all_records)} target variants from API.")
    else:
        print("No inventory data retrieved.")
        send_discord_alert("⚠️ Inventory Tracker warning: No inventory data was retrieved during the latest run.")

if __name__ == "__main__":
    main()
