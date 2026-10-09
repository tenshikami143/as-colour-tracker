import os
import requests
from supabase import create_client, Client

# Initialize Supabase credentials
SUPABASE_URL = os.environ.get("SUPABASE_URL")
SUPABASE_KEY = os.environ.get("SUPABASE_KEY")
supabase: Client = create_client(SUPABASE_URL, SUPABASE_KEY)

# AS Colour GraphQL API credentials/endpoints
GRAPHQL_URL = os.environ.get("AS_COLOUR_GRAPHQL_URL")
API_TOKEN = os.environ.get("AS_COLOUR_API_TOKEN")

def fetch_product_inventory():
    headers = {
        "Content-Type": "application/json",
        "Authorization": f"Bearer {API_TOKEN}" if API_TOKEN else ""
    }
    
    query = """
    {
      site {
        products(first: 50) {
          edges {
            node {
              id
              name
              variants(first: 50) {
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
    
    response = requests.post(GRAPHQL_URL, json={"query": query}, headers=headers)
    if response.status_code != 200:
        print(f"API Error: {response.status_code} - {response.text}")
        return []
        
    data = response.json()
    products = data.get("data", {}).get("site", {}).get("products", {}).get("edges", [])
    
    low_stock_items = []
    
    for product_edge in products:
        product_node = product_edge["node"]
        product_title = product_node.get("name")
        
        for edge in product_node.get("variants", {}).get("edges", []):
            v_node = edge["node"]
            
            inv = v_node.get("inventory", {})
            full_sku = v_node.get("sku", "UNKNOWN-SKU")
            
            # Parse full SKU (e.g., '1000-BLACK-P-OS')
            sku_parts = full_sku.split("-")
            base_sku = sku_parts[0] if len(sku_parts) > 0 else full_sku
            colour = sku_parts[1] if len(sku_parts) > 1 else "UNKNOWN"
            size = "-".join(sku_parts[2:]) if len(sku_parts) > 2 else "OS"
            
            stock_qty = 999 
            if inv and inv.get("aggregated") and inv["aggregated"].get("availableToSell") is not None:
                stock_qty = int(inv["aggregated"]["availableToSell"])
                
            if stock_qty < 25:
                low_stock_items.append({
                    "full_sku": full_sku,
                    "base_sku": base_sku,
                    "colour_name": colour,
                    "size_name": size,
                    "product_title": product_title,
                    "stock_count": stock_qty
                })
                
    return low_stock_items

def main():
    print("Starting AS Colour inventory scan (PRODUCTION)...")
    low_stock_items = fetch_product_inventory()
    print(f"Found {len(low_stock_items)} items below threshold (< 25).")
    
    for item in low_stock_items:
        full_sku = item["full_sku"]
        base_sku = item["base_sku"]
        colour = item["colour_name"]
        size = item["size_name"]
        stock = item["stock_count"]
        
        # Match using full SKU to locate the exact row in Supabase
        existing = supabase.table("inventory_states").select("*").eq("variant_id", full_sku).execute()
        existing_records = existing.data
        
        if stock >= 25:
            if existing_records:
                supabase.table("inventory_states").delete().eq("variant_id", full_sku).execute()
                print(f"Restocked & removed from Supabase: {full_sku}")
        else:
            if not existing_records:
                supabase.table("inventory_states").insert({
                    "variant_id": base_sku,  # Now stores only base style number (e.g., '1000')
                    "colour_name": colour,
                    "size_name": size, 
                    "stock_count": stock,
                    "is_below_25": True,
                    "updated_in_deconetwork": False
                }).execute()
                print(f"Added low-stock item to Supabase: {base_sku} ({colour} / {size})")
            else:
                # Update existing row and overwrite variant_id with clean base SKU
                supabase.table("inventory_states").update({
                    "variant_id": base_sku,  # Overwrites '1000-BLACK-P-OS' with '1000'
                    "colour_name": colour,
                    "size_name": size,
                    "stock_count": stock
                }).eq("variant_id", full_sku).execute()
                print(f"Updated low-stock item in Supabase: {base_sku} ({colour} / {size})")

if __name__ == "__main__":
    main()
