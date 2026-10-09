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
            sku = v_node.get("sku", "UNKNOWN-SKU")
            
            # Parse SKU (e.g., '3001-BLACK-03M' -> base SKU: 3001, colour: BLACK, size: 03M)
            sku_parts = sku.split("-")
            base_sku = sku_parts[0] if len(sku_parts) > 0 else sku
            colour = sku_parts[1] if len(sku_parts) > 1 else "UNKNOWN"
            size = "-".join(sku_parts[2:]) if len(sku_parts) > 2 else ""
            
            # Use 500 as placeholder when stock is hidden/None
            stock_qty = 500 
            if inv and inv.get("aggregated") and inv["aggregated"].get("availableToSell") is not None:
                stock_qty = int(inv["aggregated"]["availableToSell"])
                
            # TEMPORARY TEST THRESHOLD: Set to 1000 to update all rows
            if stock_qty < 1000:
                low_stock_items.append({
                    "variant_id": base_sku,
                    "sku": sku,
                    "colour_name": colour,
                    "size_name": size,
                    "product_title": product_title,
                    "stock_count": stock_qty
                })
                
    return low_stock_items

def main():
    print("Starting AS Colour inventory scan (TEST MODE)...")
    low_stock_items = fetch_product_inventory()
    print(f"Found {len(low_stock_items)} items to sync.")
    
    for item in low_stock_items:
        variant_id = item["variant_id"]
        colour = item["colour_name"]
        size = item["size_name"]
        stock = item["stock_count"]
        sku = item["sku"]
        
        # Check existing record in Supabase using the base SKU as variant_id
        existing = supabase.table("inventory_states").select("*").eq("variant_id", variant_id).execute()
        existing_records = existing.data
        
        if not existing_records:
            supabase.table("inventory_states").insert({
                "variant_id": variant_id,
                "colour_name": colour,
                "size_name": size, 
                "stock_count": stock,
                "is_below_25": True,
                "updated_in_deconetwork": False
            }).execute()
            print(f"Inserted item to Supabase: Style {variant_id} ({colour} / {size})")
        else:
            supabase.table("inventory_states").update({
                "colour_name": colour,
                "size_name": size,
                "stock_count": stock
            }).eq("variant_id", variant_id).execute()
            print(f"Updated item in Supabase: Style {variant_id}")

if __name__ == "__main__":
    main()
