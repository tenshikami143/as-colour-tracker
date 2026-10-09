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
            
            # Use 500 as placeholder when stock is hidden/None
            stock_qty = 500 
            if inv and inv.get("aggregated") and inv["aggregated"].get("availableToSell") is not None:
                stock_qty = int(inv["aggregated"]["availableToSell"])
                
            # TEMPORARY TEST THRESHOLD: Set to 1000 to update all rows
            if stock_qty < 1000:
                low_stock_items.append({
                    "sku": sku,
                    "product_title": product_title,
                    "display_name": f"{product_title} ({sku})",
                    "stock_count": stock_qty
                })
                
    return low_stock_items

def main():
    print("Starting AS Colour inventory scan (TEST MODE)...")
    low_stock_items = fetch_product_inventory()
    print(f"Found {len(low_stock_items)} items to sync.")
    
    for item in low_stock_items:
        sku = item["sku"]
        stock = item["stock_count"]
        
        # Check existing record in Supabase using the SKU as variant_id
        existing = supabase.table("inventory_states").select("*").eq("variant_id", sku).execute()
        existing_records = existing.data
        
        if not existing_records:
            supabase.table("inventory_states").insert({
                "variant_id": sku,  # Using SKU to match table structure
                "colour_name": item["display_name"],
                "size_name": "", 
                "stock_count": stock,
                "is_below_25": True,
                "updated_in_deconetwork": False
            }).execute()
            print(f"Inserted item to Supabase: {sku} (Stock: {stock})")
        else:
            supabase.table("inventory_states").update({
                "stock_count": stock
            }).eq("variant_id", sku).execute()
            print(f"Updated item in Supabase: {sku} (Stock: {stock})")

if __name__ == "__main__":
    main()
